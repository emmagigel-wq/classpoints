"""落盘：原子写入、备份轮转、大文件旁路存储。

上一代的存储有致命缺陷（实测）：主数据全塞进 localStorage，带一张大背景时
序列化 6.29MB，`setItem` 静默失败——界面上分加了、磁盘上没存，关掉页面
分数就没了，而且不报错（浏览器抛的 QuotaExceededError 被 catch 吞掉）。

这里的三条对策：
1. **写入返回结果对象**，失败绝不静默；调用方必须处理。
2. **原子写入**：先写临时文件再替换，断电不会留下半个文件。
3. **大文件分开存**：视频/背景不进主数据，避免一份大文件拖垮整个存档。
"""
from __future__ import annotations

import json
import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

from app.core.models import AppState
from app.version import APP_ID

BACKUP_KEEP = 10
MAX_MAIN_BYTES = 8388608


@dataclass
class WriteResult:
    """写入结果。ok=False 时 message 是给用户看的文案，reason 是机器判定的原因。"""
    ok: bool
    reason: str = ''
    message: str = ''
    bytes_written: int = 0
    backup_path: Path | None = None

    def __bool__(self) -> bool:
        return self.ok


@dataclass
class LoadResult:
    ok: bool
    state: AppState | None = None
    message: str = ''
    reason: str = ''
    from_backup: Path | None = None
    warnings: list[str] = field(default_factory=list)


def app_data_dir() -> Path:
    """程序数据目录：默认 %APPDATA%\\班级积分系统，可由用户在设置里改到别处。

    解析优先级见 `app/core/paths.py`：环境变量 CLASSPOINTS_DATA_DIR →
    指针文件里的 active 目录 → 默认 %APPDATA%\\班级积分系统。
    """
    from app.core import paths
    return paths.current_data_dir()


def data_file() -> Path:
    return app_data_dir() / 'data.json'


def backup_dir() -> Path:
    return app_data_dir() / 'backups'


def media_dir() -> Path:
    return app_data_dir() / 'media'


def ensure_dirs() -> None:
    app_data_dir().mkdir(parents=True, exist_ok=True)
    backup_dir().mkdir(parents=True, exist_ok=True)
    media_dir().mkdir(parents=True, exist_ok=True)


def _atomic_write(path: Path, text: str) -> None:
    """原子写入：临时文件 → flush+fsync → 替换目标。

    替换要重试：Windows 上如果另一处（换数据目录时的 copytree、杀软扫描、
    网盘同步）正握着目标文件的句柄，且那个句柄没带 FILE_SHARE_DELETE，
    `os.replace` 会直接抛 WinError 5（拒绝访问）。实测这就是「换目录那几秒
    保存失败」的成因，重试几次就过去了。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f'.tmp{os.getpid()}')
    with open(tmp, 'w', encoding='utf-8', newline='\n') as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    _replace_with_retry(tmp, path)


def _replace_with_retry(src: Path, dst: Path, attempts: int = 12) -> None:
    """`os.replace` 遇到占用就退让重试；最后一次仍失败才把异常抛给调用方。"""
    last = None
    for i in range(attempts):
        try:
            os.replace(src, dst)
            return
        except PermissionError as exc:
            last = exc
            time.sleep(0.02 + 0.03 * i)
    if last is not None:
        raise last


def _pytest_guard() -> WriteResult | None:
    """测试运行期间的兜底：绝不允许写到老师的真实数据目录。

    2026-10-24 出过一次事故——某个测试/探针忘了设 `CLASSPOINTS_DATA_DIR`，
    于是在 pytest 里把空班级写进了 `%APPDATA%\\班级积分系统`，把真实班级冲掉了。
    这里做最后一道闸：pytest 环境下，只要数据目录不是显式指定的（环境变量或指针），
    就直接拒写并返回错误，让测试自己暴露出「没隔离数据目录」的问题，
    而不是悄悄毁掉用户数据。
    """
    if not os.environ.get('PYTEST_CURRENT_TEST'):
        return None
    if os.environ.get('CLASSPOINTS_DATA_DIR') or os.environ.get('CLASSPOINTS_PROBE'):
        return None
    try:
        from app.core import paths
        if paths.read_pointer() is not None:
            return None
    except Exception:
        pass
    return WriteResult(False, 'pytest-guard', '测试环境未指定数据目录，已拒绝写入真实数据目录（保护用户数据）。请在测试里设置 CLASSPOINTS_DATA_DIR 或 CLASSPOINTS_PROBE。')


def write_state(state: AppState, *, make_backup: bool = True) -> WriteResult:
    """保存主数据。失败返回 ok=False，调用方必须把 message 显示给用户。"""
    blocked = _pytest_guard()
    if blocked is not None:
        return blocked
    from app.core import paths
    problem = paths.active_dir_problem()
    if problem:
        return WriteResult(False, 'dir-missing', f'{problem}。本次没有保存，请先恢复该目录再记分。')
    try:
        ensure_dirs()
    except OSError as exc:
        return WriteResult(False, 'mkdir', f'无法创建数据目录：{exc}')
    state.saved_at = int(time.time() * 1000)
    payload = state.to_dict()
    try:
        text = json.dumps(payload, ensure_ascii=False, separators=(',', ':'))
    except (TypeError, ValueError) as exc:
        return WriteResult(False, 'serialize', f'数据无法序列化：{exc}')
    size = len(text.encode('utf-8'))
    if size > MAX_MAIN_BYTES:
        return WriteResult(False, 'too-large', f'数据体积异常（{size / 1048576:.1f}MB），已中止保存以免写坏存档。请检查是否把大文件放进了主数据。')
    target = data_file()
    backup_path = None
    try:
        if make_backup and target.exists():
            backup_path = _rotate_backup(target)
        _atomic_write(target, text)
    except OSError as exc:
        return WriteResult(False, 'io', f'保存失败：{exc}')
    return WriteResult(True, 'ok', '已保存', bytes_written=size, backup_path=backup_path)


def _rotate_backup(source: Path) -> Path | None:
    """把当前存档复制到 backups/，并清理超出保留数的旧备份。"""
    try:
        bdir = backup_dir()
        bdir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime('%Y%m%d-%H%M%S')
        dest = bdir / f'data-{stamp}.json'
        n = 1
        while dest.exists():
            dest = bdir / f'data-{stamp}-{n}.json'
            n += 1
        shutil.copy2(source, dest)
        _prune_backups(bdir)
        return dest
    except OSError:
        return None


def _prune_backups(bdir: Path, keep: int = BACKUP_KEEP) -> None:
    files = sorted(bdir.glob('data-*.json'), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in files[keep:]:
        try:
            old.unlink()
        except OSError:
            pass


def list_backups() -> list[Path]:
    bdir = backup_dir()
    if not bdir.is_dir():
        return []
    return sorted(bdir.glob('data-*.json'), key=lambda p: p.stat().st_mtime, reverse=True)


def _salvage_corrupt(path: Path) -> Path | None:
    """把读不动的存档改名留档，避免下一次保存把它彻底覆盖掉。

    用户遇到「存档损坏」时，最早的几笔分往往还能人工从原文件里抠出来。
    如果不留档，程序一启动是空班级，随手加一个人就会把整个 data.json 覆盖成
    只有一个人的新档 —— 原始字节从此不存在。
    """
    stamp = time.strftime('%Y%m%d-%H%M%S')
    dest = path.with_name(f'{path.name}.bad-{stamp}')
    n = 1
    while dest.exists():
        dest = path.with_name(f'{path.name}.bad-{stamp}-{n}')
        n += 1
    try:
        path.replace(dest)
        return dest
    except OSError:
        return None


def read_state() -> LoadResult:
    """读取主数据；坏了就尝试最近的备份（并如实说明用了备份）。"""
    from app.core import paths
    problem = paths.active_dir_problem()
    if problem:
        return LoadResult(False, None, f'{problem}。为避免把它覆盖成空白存档，本次没有读写任何数据。', 'dir-missing')
    target = data_file()
    if not target.exists():
        return LoadResult(True, AppState(), '尚无存档，已用空白班级开局', 'missing')
    result = _try_read(target)
    if result.ok:
        return result
    salvaged = _salvage_corrupt(target)
    kept = f'，原文件已留档为 {salvaged.name}' if salvaged else ''
    warnings = [f'主存档读取失败（{result.message}）{kept}，已尝试从备份恢复']
    for backup in list_backups():
        alt = _try_read(backup)
        if not alt.ok:
            continue
        return LoadResult(True, alt.state, f'主存档损坏，已从备份 {backup.name} 恢复{kept}', 'corrupt', from_backup=backup, warnings=warnings)
    return LoadResult(False, None, f'存档损坏且没有可用备份：{result.message}{kept}', 'corrupt', warnings=warnings)


def _try_read(path: Path) -> LoadResult:
    try:
        text = path.read_text(encoding='utf-8')
    except OSError as exc:
        return LoadResult(False, None, f'无法读取：{exc}', 'corrupt')
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        return LoadResult(False, None, f'内容不是合法 JSON：{exc}', 'corrupt')
    if not isinstance(raw, dict):
        return LoadResult(False, None, '内容格式不是对象', 'corrupt')
    try:
        state = AppState.from_dict(raw)
    except Exception as exc:
        return LoadResult(False, None, f'数据结构无法解析：{exc}', 'corrupt')
    return LoadResult(True, state, '已读取存档', 'ok')


def media_path(name: str) -> Path:
    """媒体文件的存放路径。只允许放在 media/ 内，防路径穿越。"""
    safe = Path(name).name
    return media_dir() / safe


def save_media_bytes(name: str, data: bytes) -> WriteResult:
    """保存视频/背景等大文件——与主数据分开，主数据只留元信息。"""
    try:
        ensure_dirs()
        dest = media_path(name)
        tmp = dest.with_suffix(dest.suffix + f'.tmp{os.getpid()}')
        with open(tmp, 'wb') as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        _replace_with_retry(tmp, dest)
        return WriteResult(True, 'ok', '已保存', bytes_written=len(data))
    except OSError as exc:
        return WriteResult(False, 'io', f'媒体文件保存失败：{exc}')


def read_media_bytes(name: str) -> bytes | None:
    path = media_path(name)
    if not path.is_file():
        return None
    try:
        return path.read_bytes()
    except OSError:
        return None


def delete_media(name: str) -> bool:
    path = media_path(name)
    try:
        if path.is_file():
            path.unlink()
            return True
    except OSError:
        return False
    return False


def media_exists(name: str) -> bool:
    return bool(name) and media_path(name).is_file()


def export_bundle(state: AppState) -> dict:
    """导出为可迁移的整包（含版本号，便于导入时提示来源）。"""
    from app.version import __version__
    return {
        'kind': 'classpoints-bundle',
        'appVersion': __version__,
        'exportedAt': int(time.time() * 1000),
        'state': state.to_dict(),
    }


def import_bundle(raw: dict) -> LoadResult:
    """从整包恢复。"""
    if not isinstance(raw, dict):
        return LoadResult(False, None, '文件内容不是对象', 'corrupt')
    inner = raw.get('state', raw)
    try:
        state = AppState.from_dict(inner if isinstance(inner, dict) else {})
    except Exception as exc:
        return LoadResult(False, None, f'数据结构无法解析：{exc}', 'corrupt')
    note = ''
    src = raw.get('appVersion')
    if src:
        from app.version import __version__
        if src != __version__:
            note = f'这份数据由 v{src} 导出，当前程序是 v{__version__}'
    return LoadResult(True, state, note or '导入成功', 'ok')
