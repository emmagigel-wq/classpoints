"""整库备份 / 恢复：带 manifest 的 zip 归档 + 轮转。

为什么不用 storage 里那套 `backups/*.json`：那套是「每次保存顺手存一份上一个版本」，
防的是**写坏**；这里要防的是**整库迁移与灾难恢复**——换机器、误删、数据被改乱后回滚。
所以多了一个 manifest，把「这份包是谁、什么时候、什么版本、什么 schema 写的」钉在包里，
恢复时**先验 manifest 再动数据**，schema 不兼容明确报错，绝不静默覆盖现有存档。

包结构：
    manifest.json     版本 / schema / 导出时间 / 班级名 / 各类条数
    data.json         AppState.to_dict() 的原样内容
    media/<文件名>     可选：媒体文件（include_media=True 时）

安全：读取时对每个成员做 zip-slip 校验（成员名不得含 `..`、不得是绝对路径、
不得越出解包目录），恶意或损坏的包在解包前就被拒绝。

对外函数：
    create_backup(state=None, ...)   -> BackupResult   打包（带轮转）
    inspect_backup(path, ...)        -> RestoreResult  只读校验，**不碰磁盘**
    restore_backup(path, ...)        -> RestoreResult  校验通过后写回 data.json
    list_archives(...) / prune_archives(...)            轮转管理
"""
from __future__ import annotations

import json
import os
import shutil
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.core import storage
from app.core.models import AppState
from app.version import APP_ID, DATA_SCHEMA, __version__
from app.io import IoResult

__all__ = [
    'BACKUP_KEEP',
    'MANIFEST_NAME',
    'DATA_NAME',
    'MEDIA_PREFIX',
    'BackupResult',
    'RestoreResult',
    'create_backup',
    'inspect_backup',
    'restore_backup',
    'list_archives',
    'prune_archives',
    'backup_pattern',
]

BACKUP_KEEP = 10
MANIFEST_NAME = 'manifest.json'
DATA_NAME = 'data.json'
MEDIA_PREFIX = 'media/'
VIDEOS_PREFIX = 'videos/'
BUNDLE_KIND = f'{APP_ID}-backup'


@dataclass
class BackupResult(IoResult):
    path: Path | None = None
    bytes_written: int = 0
    entries: list[str] = field(default_factory=list)
    pruned: list[str] = field(default_factory=list)


@dataclass
class RestoreResult(IoResult):
    """恢复结果。`state` 在 inspect 阶段就已解析好，但**磁盘还没动**。"""

    state: AppState | None = None
    path: Path | None = None
    manifest: dict[str, Any] = field(default_factory=dict)
    restored_to: Path | None = None
    safety_copy: Path | None = None
    media_restored: int = 0
    theme_restored: int = 0
    needs_force: bool = False


def backup_pattern() -> str:
    return f'{APP_ID}-backup-*.zip'


def default_archive_dir() -> Path:
    """归档目录：数据目录下的 `archives/`（与 storage 的 backups/ 分开，语义不同）。"""
    return storage.app_data_dir() / 'archives'


def _manifest_for(state: AppState, data_bytes: int, include_media: bool) -> dict[str, Any]:
    return {
        'kind': BUNDLE_KIND,
        'appId': APP_ID,
        'appVersion': __version__,
        'dataSchema': DATA_SCHEMA,
        'createdAt': int(time.time() * 1000),
        'className': state.class_name,
        'counts': {
            'students': len(state.students),
            'history': len(state.history),
            'lottery': len(state.lottery),
            'prizes': len(state.prizes),
        },
        'dataBytes': data_bytes,
        'includeMedia': bool(include_media),
    }


def _collect_media(entries: list[tuple[str, bytes]]) -> None:
    """把媒体文件收进包。

两个来源：`media/`（背景图/启动动画这类旁路文件）与 `videos/`（抽奖视频库）。
早先只收 `media/`，于是「备份包含抽奖视频」这个勾选项对视频库完全无效——
勾了也一个视频都没进包，用户换电脑后视频凭空消失。两处都要收。
"""
    for prefix, folder in (
        (MEDIA_PREFIX, storage.media_dir()),
        (VIDEOS_PREFIX, _videos_dir()),
    ):
        if folder is None or not folder.is_dir():
            continue
        for item in sorted(folder.iterdir()):
            if not item.is_file():
                continue
            try:
                entries.append((prefix + item.name, item.read_bytes()))
            except OSError:
                continue
    try:
        from app.core import videos as _videos
        bindings = _videos.bindings_file()
        if bindings is not None and bindings.is_file():
            entries.append(('videos.json', bindings.read_bytes()))
    except Exception:
        return


def _videos_dir():
    """视频库目录；导入失败时返回 None（老版本/裁剪包里可能没有这个模块）。"""
    try:
        from app.core.videos import videos_dir
    except Exception:
        return None
    return videos_dir()


def _collect_theme(entries: list[tuple[str, bytes]]) -> None:
    """把主题文件（icon/ media/ ui/）收进包，zip 内前缀 ``theme/``。

与 media/ 的区别：主题是「界面长相」那一套小文件，用户端下载同步内容时
明确排除它；备份时可选带不带（单张小图，体积可以忽略）。
"""
    from app.core import theme_store
    for rel, blob in theme_store.read_theme().items():
        entries.append((theme_store.ZIP_PREFIX + '/' + rel, blob))


def create_backup(
    state: AppState | None = None,
    *,
    archive_dir: str | Path | None = None,
    keep: int = BACKUP_KEEP,
    include_media: bool = False,
    include_theme: bool = False,
    source: str | Path | None = None,
) -> BackupResult:
    """把整库打包成带时间戳的 zip，并轮转保留最近 `keep` 份。

数据来源优先级：`state`（内存里的当前状态，最准）→ `source` / 默认 data.json 文件。
调用方通常直接给 Store.state，这样**还没保存的改动也会进备份**。

返回 ok=False 时不留下半个 zip（临时文件会被清掉）。
"""
    target_dir = Path(archive_dir) if archive_dir else default_archive_dir()
    warnings: list[str] = []

    if state is not None:
        try:
            payload = state.to_dict()
            text = json.dumps(payload, ensure_ascii=False, separators=(',', ':'))
        except (TypeError, ValueError) as exc:
            return BackupResult(
                ok=False, reason='serialize',
                error=f'当前数据无法序列化，已中止备份：{exc}',
            )
        data_bytes = text.encode('utf-8')
    else:
        src = Path(source) if source else storage.data_file()
        if not src.is_file():
            return BackupResult(
                ok=False,
                reason='missing',
                error=f'找不到主数据文件，无法备份：{src}',
            )
        try:
            data_bytes = src.read_bytes()
        except OSError as exc:
            return BackupResult(
                ok=False, reason='io',
                error=f'读取主数据失败：{exc}',
            )
        try:
            raw = json.loads(data_bytes.decode('utf-8'))
            state = AppState.from_dict(raw if isinstance(raw, dict) else {})
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            return BackupResult(
                ok=False,
                reason='corrupt',
                error=f'主数据不是合法 JSON，已中止备份（避免把坏数据也备成「好备份」）：{exc}',
            )
        warnings.append(f'备份内容取自文件 {src.name}。')

    members: list[tuple[str, bytes]] = [(DATA_NAME, data_bytes)]
    if include_media:
        _collect_media(members)
    if include_theme:
        _collect_theme(members)
    manifest = _manifest_for(state, len(data_bytes), include_media)
    manifest['includeTheme'] = bool(include_theme)
    manifest['mediaCount'] = sum(1 for name, _ in members if name.startswith(MEDIA_PREFIX))
    manifest['themeCount'] = sum(
        1 for name, _ in members
        if name.startswith('theme/')
    )
    members.insert(
        0,
        (MANIFEST_NAME, json.dumps(manifest, ensure_ascii=False, indent=2).encode('utf-8')),
    )

    try:
        target_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return BackupResult(
            ok=False, reason='mkdir',
            error=f'无法创建备份目录 {target_dir}：{exc}',
        )

    stamp = time.strftime('%Y%m%d-%H%M%S')
    dest = target_dir / f'{APP_ID}-backup-{stamp}.zip'
    n = 1
    while dest.exists():
        dest = target_dir / f'{APP_ID}-backup-{stamp}-{n}.zip'
        n += 1

    tmp = dest.with_name(dest.name + f'.tmp{os.getpid()}')
    try:
        with zipfile.ZipFile(tmp, 'w', zipfile.ZIP_DEFLATED) as zf:
            for name, blob in members:
                info = zipfile.ZipInfo(
                    name,
                    date_time=time.localtime(time.time())[:6],
                )
                info.compress_type = zipfile.ZIP_DEFLATED
                zf.writestr(info, blob)
        size = tmp.stat().st_size
        os.replace(tmp, dest)
    except OSError as exc:
        _quiet_unlink(tmp)
        return BackupResult(
            ok=False, reason='io',
            error=f'写入备份包失败：{exc}',
        )
    except Exception as exc:
        _quiet_unlink(tmp)
        return BackupResult(
            ok=False, reason='unexpected',
            error=f'打包备份时出错：{exc}',
        )

    pruned = prune_archives(keep, archive_dir=target_dir)
    if pruned:
        warnings.append(f'已清理 {len(pruned)} 份超出保留数（{keep}）的旧归档。')

    return BackupResult(
        ok=True, reason='ok', path=dest, bytes_written=size,
        entries=[name for name, _ in members], pruned=pruned, warnings=warnings,
        message=f'已备份到 {dest.name}（{size / 1024:.1f} KB）',
    )


def _quiet_unlink(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        return


def list_archives(archive_dir: str | Path | None = None) -> list[Path]:
    """列出归档，按修改时间从新到旧。"""
    target = Path(archive_dir) if archive_dir else default_archive_dir()
    if not target.is_dir():
        return []
    files = [p for p in target.glob(backup_pattern()) if p.is_file()]
    return sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)


def prune_archives(keep: int = BACKUP_KEEP, *, archive_dir: str | Path | None = None) -> list[str]:
    """只保留最近 `keep` 份归档，返回被删掉的文件名。

只碰匹配 `classpoints-backup-*.zip` 的文件——同一个目录里放别的东西不受影响。
`keep <= 0` 视为「不轮转」（返回空列表，一份都不删），避免误传参把备份清空。
"""
    if keep <= 0:
        return []
    files = list_archives(archive_dir)
    removed: list[str] = []
    for old in files[keep:]:
        try:
            old.unlink()
            removed.append(old.name)
        except OSError:
            continue
    return removed


def _safe_member(name: str) -> str:
    """校验 zip 成员名，拒绝 zip-slip 与绝对路径。返回规范化后的相对路径。"""
    text = (name or '').replace('\\', '/').strip()
    if not text:
        raise ValueError('归档里有一个空的成员名')
    if text.startswith('/') or (len(text) > 1 and text[1] == ':'):
        raise ValueError(f'归档成员是绝对路径，已拒绝：{name}')
    parts = [p for p in text.split('/') if p not in ('', '.')]
    if any(p == '..' for p in parts):
        raise ValueError(f'归档成员包含「..」，可能是恶意路径，已拒绝：{name}')
    return '/'.join(parts)


def _read_manifest(zf: zipfile.ZipFile) -> tuple[dict[str, Any] | None, str]:
    try:
        raw = zf.read(MANIFEST_NAME)
    except KeyError:
        return None, (
            f'这不是本程序导出的备份包：归档里没有 manifest.json。包里现有内容：'
            f'{'、'.join(zf.namelist()[:8]) or '（空）'}'
        )
    except (zipfile.BadZipFile, OSError) as exc:
        return None, f'读取 manifest.json 失败：{exc}'
    try:
        data = json.loads(raw.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return None, f'manifest.json 内容损坏，无法解析：{exc}'
    if not isinstance(data, dict):
        return None, 'manifest.json 不是对象结构，无法识别这份备份。'
    kind = data.get('kind')
    if kind and kind != BUNDLE_KIND:
        return None, f'这份归档的类型是「{kind}」，不是本程序的整库备份。'
    return data, ''



def inspect_backup(
    path: str | Path,
    *,
    expected_schema: int | None = None,
    force: bool = False,
) -> RestoreResult:
    """只读校验一个备份包并解析出 AppState。**不写任何文件**。

schema 不兼容时返回 ok=False、reason="schema-incompatible"、needs_force=True，
并明确写出「包里的 schema 是多少、当前程序支持多少」——而不是把数据覆盖上去。
调用方若确认可强行恢复，可 `force=True`（会在结果里留 warning）。
"""
    target = Path(path)
    if not target.exists():
        return RestoreResult(
            ok=False, reason='missing', error=f'备份文件不存在：{target}'
        )
    if target.is_dir():
        return RestoreResult(
            ok=False, reason='missing', error=f'这是目录不是备份文件：{target}'
        )

    want = DATA_SCHEMA if expected_schema is None else int(expected_schema)
    try:
        zf = zipfile.ZipFile(target, 'r')
    except zipfile.BadZipFile as exc:
        return RestoreResult(
            ok=False, reason='not-zip', error=f'这不是一个有效的 zip 备份包：{exc}', path=target
        )
    except OSError as exc:
        return RestoreResult(
            ok=False, reason='io', error=f'无法打开备份包：{exc}', path=target
        )

    warnings: list[str] = []
    with zf:
        manifest, err = _read_manifest(zf)
        if manifest is None:
            return RestoreResult(
                ok=False, reason='not-backup', error=err, path=target
            )
        for name in zf.namelist():
            try:
                _safe_member(name)
            except ValueError as exc:
                return RestoreResult(
                    ok=False,
                    reason='unsafe-member',
                    error=f'归档里有不安全的成员路径，已拒绝恢复（这是典型的 zip-slip 攻击特征）：{exc}',
                    path=target, manifest=manifest,
                )
        got = manifest.get('dataSchema')
        if not isinstance(got, int):
            return RestoreResult(
                ok=False,
                reason='schema-missing',
                error='备份包的 manifest 里没有记录数据结构版本（dataSchema），无法确认兼容性，已拒绝恢复以免覆盖现有数据。',
                path=target, manifest=manifest,
            )
        if got != want:
            if not force:
                return RestoreResult(
                    ok=False,
                    reason='schema-incompatible',
                    needs_force=True,
                    error=f'数据结构版本不兼容：这份备份是 schema {got} 写的，当前程序支持 schema {want}。已拒绝恢复（现有存档未改动）。请用对应版本的程序打开，或确认风险后强制恢复。',
                    path=target, manifest=manifest,
                )
            warnings.append(
                f'强制恢复了 schema {got} 的备份（当前支持 schema {want}），部分字段可能读不出来。'
            )
        try:
            raw = zf.read(DATA_NAME)
        except KeyError:
            return RestoreResult(
                ok=False,
                reason='corrupt',
                error=f'备份包里没有 {DATA_NAME}（只有：{'、'.join(zf.namelist()[:8])}），已拒绝恢复。',
                path=target, manifest=manifest,
            )
        except (zipfile.BadZipFile, OSError) as exc:
            return RestoreResult(
                ok=False,
                reason='corrupt',
                error=f'读取 {DATA_NAME} 失败：{exc}', path=target,
                manifest=manifest,
            )
        try:
            payload = json.loads(raw.decode('utf-8'))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            return RestoreResult(
                ok=False,
                reason='corrupt',
                error=f'备份包里的 {DATA_NAME} 不是合法 JSON，已拒绝恢复：{exc}',
                path=target, manifest=manifest,
            )
        if not isinstance(payload, dict):
            return RestoreResult(
                ok=False,
                reason='corrupt',
                error=f'备份包里的 {DATA_NAME} 不是对象结构，已拒绝恢复。',
                path=target, manifest=manifest,
            )
        try:
            state = AppState.from_dict(payload)
        except Exception as exc:
            return RestoreResult(
                ok=False,
                reason='corrupt',
                error=f'备份里的数据结构无法解析，已拒绝恢复：{exc}',
                path=target, manifest=manifest,
            )

        media_items: list[tuple[Path, bytes]] = []
        for name in zf.namelist():
            if not name.replace('\\', '/').startswith(MEDIA_PREFIX):
                continue
            try:
                safe = _safe_member(name)
            except ValueError as exc:
                warnings.append(str(exc))
                continue
            rel = safe[len(MEDIA_PREFIX):]
            if not rel:
                continue
            disk_name = Path(rel).name
            if not disk_name:
                continue
            try:
                media_items.append((storage.media_path(disk_name), zf.read(name)))
            except (zipfile.BadZipFile, OSError) as exc:
                warnings.append(f'媒体文件 {disk_name} 读取失败，已跳过：{exc}')

        video_items: list[tuple[Path, bytes]] = []
        vdir = _videos_dir()
        if vdir is not None:
            for name in zf.namelist():
                norm = name.replace('\\', '/')
                if not norm.startswith(VIDEOS_PREFIX):
                    continue
                try:
                    safe = _safe_member(name)
                except ValueError as exc:
                    warnings.append(str(exc))
                    continue
                rel = safe[len(VIDEOS_PREFIX):]
                if not rel:
                    continue
                disk_name = Path(rel).name
                if not disk_name:
                    continue
                try:
                    video_items.append((vdir / disk_name, zf.read(name)))
                except (zipfile.BadZipFile, OSError) as exc:
                    warnings.append(f'视频文件 {disk_name} 读取失败，已跳过：{exc}')

        theme_items: dict[str, bytes] = {}
        for name in zf.namelist():
            norm = name.replace('\\', '/')
            if not norm.startswith('theme/'):
                continue
            try:
                safe = _safe_member(name)
            except ValueError as exc:
                warnings.append(str(exc))
                continue
            rel = safe[len('theme/'):]
            if not rel:
                continue
            try:
                theme_items[rel] = zf.read(name)
            except (zipfile.BadZipFile, OSError) as exc:
                warnings.append(f'主题文件 {rel} 读取失败，已跳过：{exc}')

    bindings_bytes = b''
    try:
        with zipfile.ZipFile(path) as zf_bind:
            if 'videos.json' in set(zf_bind.namelist()):
                bindings_bytes = zf_bind.read('videos.json')
    except (zipfile.BadZipFile, OSError, ValueError):
        bindings_bytes = b''

    src_version = manifest.get('appVersion')
    if src_version and src_version != __version__:
        warnings.append(f'这份备份由 v{src_version} 导出，当前程序是 v{__version__}。')

    result = RestoreResult(
        ok=True,
        reason='ok',
        path=target, manifest=manifest,
        warnings=warnings,
        message='备份包校验通过，可以恢复。',
    )
    result.state = state
    result.media_restored = 0
    setattr(result, '_media_items', media_items)
    setattr(result, '_video_items', video_items)
    setattr(result, '_bindings_bytes', locals().get('bindings_bytes', b''))
    setattr(result, '_theme_items', theme_items)
    return result



def restore_backup(
    path: str | Path,
    *,
    target: str | Path | None = None,
    expected_schema: int | None = None,
    force: bool = False,
    safety_backup: bool = True,
    restore_media: bool = True,
) -> RestoreResult:
    """校验通过后把备份写回主数据文件。

顺序刻意是「先校验 → 再保险备份 → 最后替换」：
1. `inspect_backup` 全量校验；不过就**一个字节都不写**（现有存档完好）。
2. 覆盖前把当前的 data.json 复制一份到 archives/ 之外的 `pre-restore-*.json`，
   万一恢复错了还能退回去。
3. 原子写入（临时文件 + os.replace），中途失败不会留下半个存档。

注意：本函数**不**调用 `Store.replace_state`——回写后请由调用方重新加载
（`Store.load()`），这样界面能拿到完整的变更通知。返回值里的 `state`
也可直接喂给 `Store.replace_state`，二者等价。
"""
    inspected = inspect_backup(path, expected_schema=expected_schema, force=force)
    if not inspected.ok:
        return inspected
    dest = Path(target) if target else storage.data_file()
    warnings = list(inspected.warnings)
    safety = None

    if safety_backup and dest.is_file():
        try:
            safety = storage.backup_dir() / (f'pre-restore-{time.strftime("%Y%m%d-%H%M%S")}.json')
            safety.parent.mkdir(parents=True, exist_ok=True)
            n = 1
            while safety.exists():
                safety = safety.with_name(
                    f'pre-restore-{time.strftime("%Y%m%d-%H%M%S")}-{n}.json'
                )
                n += 1
            shutil.copy2(dest, safety)
        except OSError as exc:
            safety = None
            warnings.append(f'恢复前无法备份现有存档（{exc}），仍继续恢复。')

    assert inspected.state is not None

    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return RestoreResult(
            ok=False, reason='mkdir',
            error=f'无法创建目标目录 {dest.parent}：{exc}', path=dest, manifest=inspected.manifest,
        )

    try:
        payload = inspected.state.to_dict()
        text = json.dumps(payload, ensure_ascii=False, separators=(',', ':'))
        tmp = dest.with_name(dest.name + f'.tmp{os.getpid()}')
        with open(tmp, 'w', encoding='utf-8', newline='\n') as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, dest)
    except (OSError, TypeError, ValueError) as exc:
        _quiet_unlink(dest.with_name(dest.name + f'.tmp{os.getpid()}'))
        return RestoreResult(
            ok=False, reason='io',
            error=f'写入存档失败，现有数据未改动：{exc}',
            path=dest, manifest=inspected.manifest, warnings=warnings,
        )

    written_media = 0
    if restore_media:
        for media_path, blob in getattr(inspected, '_media_items', []):
            try:
                media_path.parent.mkdir(parents=True, exist_ok=True)
                with open(media_path, 'wb') as fh:
                    fh.write(blob)
                written_media += 1
            except OSError as exc:
                warnings.append(f'媒体文件 {media_path.name} 恢复失败：{exc}')

    written_videos = 0
    if restore_media:
        _bindings_blob = None
        for video_path, blob in getattr(inspected, '_video_items', []):
            try:
                video_path.parent.mkdir(parents=True, exist_ok=True)
                with open(video_path, 'wb') as fh:
                    fh.write(blob)
                written_videos += 1
            except OSError as exc:
                warnings.append(f'视频文件 {video_path.name} 恢复失败：{exc}')
        _blob = getattr(inspected, '_bindings_bytes', b'') or b''
        if _blob:
            try:
                from app.core import videos as _videos

                target_file = _videos.bindings_file()
                if target_file is not None:
                    target_file.parent.mkdir(parents=True, exist_ok=True)
                    with open(target_file, 'wb') as fh:
                        fh.write(_blob)
            except Exception as exc:
                warnings.append(f'奖项视频绑定恢复失败：{exc}')

    written_theme = 0
    theme_items = getattr(inspected, '_theme_items', {}) or {}
    if restore_media and theme_items:
        from app.core import theme_store
        written_theme = theme_store.restore_theme(theme_items)
        if written_theme < len(theme_items):
            warnings.append(
                f'主题文件恢复了 {written_theme}/{len(theme_items)} 个，其余路径不合法或写入失败。'
            )

    return RestoreResult(
        ok=True, reason='ok',
        state=inspected.state, path=inspected.path, manifest=inspected.manifest,
        restored_to=dest, safety_copy=safety,
        warnings=warnings,
        media_restored=written_media + written_videos, theme_restored=written_theme,
        message=f'已从 {(inspected.path.name if inspected.path else '备份')} 恢复'
        + (f'，恢复前存档已留底为 {safety.name}' if safety else '')
        + (f'，抽奖视频 {written_videos} 个' if written_videos else ''),
    )
