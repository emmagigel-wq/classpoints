"""数据目录定位：让用户能把「班级积分系统」的数据挪到任意盘符。

为什么需要单独一层：数据目录原本硬编码成 `%APPDATA%\\班级积分系统`
（见 storage.app_data_dir）。一旦允许用户改目录，就必须有一个**不会被
搬走**的地方记着「当前数据在哪」——否则改完目录，程序下次启动仍然去
老路径找指针，永远读不回来。

所以指针文件固定在 `%LOCALAPPDATA%\\班级积分系统\\location.json`，
与数据目录本身解耦：

    {"active": "D:\\\\班级数据", "pending": "E:\\\\班级数据"}

- `active`：当前生效的数据目录。
- `pending`：用户刚改、尚未重启生效的目录（重启时 apply_pending 把它提升为 active）。

优先级（从高到低）：
1. 环境变量 `CLASSPOINTS_DATA_DIR`（测试与便携模式用，直接压过一切）；
2. 指针文件里的 active；
3. 默认 `%APPDATA%\\班级积分系统`。

本模块只依赖标准库，不导入 Qt，可以被 core / services / ui 任一层安全引用。
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
import time
from pathlib import Path

APP_DIR_NAME = '班级积分系统'
LOCATION_FILE = 'location.json'
_MIGRATE_ITEMS = ('data.json', 'videos.json', 'videos', 'media', 'backups', 'theme', 'splash', 'notifications.json')


def default_data_dir() -> Path:
    """出厂默认数据目录：%APPDATA%\\班级积分系统。

    探针模式（``CLASSPOINTS_PROBE=1``）下退到一个临时目录：开发期的探针脚本
    忘了设 ``CLASSPOINTS_DATA_DIR`` 就会**写进老师的真实数据目录**，还会把
    ``backups/`` 里的历史备份轮转掉——2026-10-24 真发生过一次。宁可让探针
    写进临时目录、事后自己去找，也不给它碰真实数据的机会。
    """
    if os.environ.get('CLASSPOINTS_PROBE') and not os.environ.get('CLASSPOINTS_DATA_DIR'):
        import tempfile
        return Path(tempfile.gettempdir()) / f'{APP_DIR_NAME}-probe' / str(os.getpid())
    base = os.environ.get('APPDATA')
    if base:
        return Path(base) / APP_DIR_NAME
    return Path.home() / f'.{APP_DIR_NAME}'


def pointer_dir() -> Path:
    """指针文件所在目录：%LOCALAPPDATA%\\班级积分系统（不随数据目录搬家）。

    探针模式下改到临时目录：``request_data_dir`` / ``apply_pending_data_dir``
    会写这个指针文件，而 ``CLASSPOINTS_PROBE`` 只保护了数据目录、保护不到它。
    2026-10-24 复核时真踩过——迁移类探针把真实指针指向了一个临时目录，
    之后正式启动就会读到一个被回收的路径。
    """
    if os.environ.get('CLASSPOINTS_PROBE') == '1':
        return Path(tempfile.gettempdir()) / f'{APP_DIR_NAME}-probe-pointer'
    base = os.environ.get('LOCALAPPDATA') or os.environ.get('APPDATA')
    if base:
        return Path(base) / APP_DIR_NAME
    return Path.home() / f'.{APP_DIR_NAME}'


def pointer_file() -> Path:
    return pointer_dir() / LOCATION_FILE


_POINTER_WARNING: list[str] = []


def consume_pointer_warning() -> str | None:
    """取走「指针文件损坏/丢失」的告警（只报一次）。

    界面上必须提示：配置坏了会静默回落到默认目录，用户看到的是默认目录里的
    旧班级或空班级，而他的真实数据其实还在自定义目录里——不说一声就是「数据没了」。
    """
    if _POINTER_WARNING:
        return _POINTER_WARNING.pop()
    return None


def _read_pointer() -> dict:
    path = pointer_file()
    try:
        raw = path.read_text(encoding='utf-8')
    except FileNotFoundError:
        return {}
    except (OSError, UnicodeDecodeError) as exc:
        _POINTER_WARNING.append(f'数据目录配置文件无法读取（{exc}），已使用默认目录。')
        return {}
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        stamp = time.strftime('%Y%m%d-%H%M%S')
        try:
            path.replace(path.with_name(f'{path.name}.bad-{stamp}'))
        except OSError:
            pass
        _POINTER_WARNING.append('数据目录配置文件已损坏，已回退到默认目录并留档为 location.json.bad-*。如果原来的数据在别的文件夹，请在设置里重新选择。')
        return {}
    if isinstance(data, dict):
        return data
    return {}


def _write_pointer(data: dict) -> None:
    """原子写指针文件。

    带重试：Windows 上另一个线程/进程正读着 location.json 时，`os.replace` 会
    抛 `PermissionError: [WinError 5] 拒绝访问`（读句柄默认不带 FILE_SHARE_DELETE）。
    运行中改数据目录的同时恰好在存盘，就会踩到这个——重试几次即可，不必让用户重来。
    """
    path = pointer_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f'{path.name}.{os.getpid()}-{threading.get_ident()}.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    last = None
    for attempt in range(10):
        try:
            os.replace(tmp, path)
            return
        except OSError as exc:
            last = exc
            time.sleep(0.01 * (attempt + 1))
    try:
        tmp.unlink()
    except OSError:
        pass
    if last is not None:
        raise last
    raise OSError('无法写入数据目录配置')


def _env_override() -> Path | None:
    raw = os.environ.get('CLASSPOINTS_DATA_DIR')
    if raw:
        return Path(raw)
    return None


def current_data_dir() -> Path:
    """当前生效的数据目录。"""
    override = _env_override()
    if override is not None:
        return override
    active = _read_pointer().get('active')
    if active:
        return Path(str(active))
    return default_data_dir()


def pending_data_dir() -> Path | None:
    """已记录、等下次启动生效的目录；没有则 None。"""
    pending = _read_pointer().get('pending')
    if pending:
        return Path(str(pending))
    return None


def _copy_tree_tolerant(src: Path, dst: Path) -> list[str]:
    """合并式拷贝目录，容忍「扫描后被删掉」的个别文件（返回真正失败的条目）。

    为什么不用 ``shutil.copytree``：程序正在运行时 backups/ 会被轮转删除，
    copytree 撞上一个刚好消失的文件就整体抛错，于是**整个换目录被取消**——
    用户看到的是「部分内容未能复制」，其实只是某份旧备份刚好过期。
    """
    bad = []
    for root, _dirs, files in os.walk(src):
        rel = Path(root).relative_to(src)
        out = dst / rel
        try:
            out.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            bad.append(f'{rel}：{exc.strerror or exc}')
            continue
        for name in files:
            s, t = Path(root) / name, out / name
            try:
                shutil.copy2(s, t)
            except FileNotFoundError:
                continue
            except OSError as exc:
                bad.append(f'{rel / name}：{exc.strerror or exc}')
    return bad


def _copy_into(src: Path, dst: Path, *, archive: bool = True) -> list[str]:
    """把数据目录里的现有内容搬到 dst，返回出问题的条目（不抛）。

    两条数据安全底线：
    * 目录类条目（videos/backups）**合并**过去，绝不 `rmtree` 目标——用户要是把数据
      目录指到一个已经有东西的文件夹，删掉的是他自己的文件。
    * 目标里已有同名 `data.json`（例如切回一个旧目录）先改名留档，再覆盖；
      宁可多一个 `.old-<时间戳>`，也不要静默吃掉一份完整存档。

    `archive=False` 用于「启动时再补抄一遍」：那时目标里的东西本来就是上次从这台机器
    抄过去的，再留档只会攒出一堆 .old-* 垃圾。
    """
    problems = []
    if not src.exists():
        return problems
    dst.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime('%Y%m%d-%H%M%S')
    for name in _MIGRATE_ITEMS:
        item = src / name
        if not item.exists():
            continue
        target = dst / name
        try:
            if item.is_dir():
                problems.extend(f'{name}/{p}' for p in _copy_tree_tolerant(item, target))
                continue
            if target.exists() and archive:
                target.replace(target.with_name(f'{target.name}.old-{stamp}'))
            shutil.copy2(item, target)
        except OSError as exc:
            problems.append(f'{name}：{exc.strerror or exc}')
    return problems


def request_data_dir(new_dir: Path, *, migrate: bool = True) -> tuple[bool, str]:
    """登记「下次启动改用 new_dir」，可选把现有数据复制过去。

    返回 (是否成功, 给用户看的说明)。成功后必须重启程序才生效——
    因为 Store 已经按老目录打开了文件句柄，运行中换目录会丢数据。
    """
    if _env_override() is not None:
        return (False, '当前由环境变量 CLASSPOINTS_DATA_DIR 指定数据目录，界面无法更改。')
    target = Path(new_dir).expanduser()
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return (False, f'无法创建目录：{exc.strerror or exc}')
    probe = target / '.write_test'
    try:
        probe.write_text('ok', encoding='utf-8')
        probe.unlink()
    except OSError as exc:
        return (False, f'该目录不可写：{exc.strerror or exc}')
    src = current_data_dir()
    try:
        if target.resolve() == src.resolve():
            return (False, '该目录就是当前数据目录，无需更改。')
    except OSError:
        pass
    problems = []
    staging = None
    if migrate:
        stamp = time.strftime('%Y%m%d-%H%M%S')
        staging = target.with_name(f'.cpmig-{stamp}')
        try:
            staging.mkdir(parents=True, exist_ok=False)
        except OSError as exc:
            return (False, f'无法创建临时目录：{exc.strerror or exc}')
        problems = _copy_into(src, staging)
        if not problems:
            problems = _staging_conflicts(staging, target)
        if not problems:
            try:
                _commit_staging(staging, target, stamp)
            except OSError as exc:
                problems = [f'写入新目录失败：{exc.strerror or exc}']
        if problems:
            shutil.rmtree(staging, ignore_errors=True)
            return (False, '部分内容未能复制到新目录，已取消更改（原目录与目标目录都没有被改动）：' + '；'.join(problems))
        shutil.rmtree(staging, ignore_errors=True)
    data = _read_pointer()
    data['pending'] = str(target)
    data.setdefault('active', str(src))
    try:
        _write_pointer(data)
    except OSError as exc:
        return (False, f'写入配置失败：{exc.strerror or exc}')
    return (True, f'已记录新目录，重启后生效（现有数据已复制到 {target}）。')


def apply_pending_data_dir() -> tuple[bool, str]:
    """启动时调用：把 pending 提升为 active。返回 (是否发生了切换, 说明)。"""
    data = _read_pointer()
    pending = data.get('pending')
    if not pending:
        return (False, '')
    target = Path(str(pending))
    if not target.exists():
        data.pop('pending', None)
        try:
            _write_pointer(data)
        except OSError:
            pass
        return (False, f'新数据目录 {target} 不存在，已继续使用原目录。')
    probe = target / '.write_test'
    try:
        probe.write_text('ok', encoding='utf-8')
        probe.unlink()
    except OSError as exc:
        data.pop('pending', None)
        try:
            _write_pointer(data)
        except OSError:
            pass
        return (False, f'新数据目录 {target} 不可写（{exc.strerror or exc}），已继续使用原目录。')
    old = current_data_dir()
    try:
        same = old.exists() and target.resolve() == old.resolve()
    except OSError:
        same = False
    if not same:
        late = _copy_into(old, target, archive=False)
        if late:
            tries = int(data.get('pending_tries') or 0) + 1
            if tries >= 3:
                data.pop('pending', None)
                data.pop('pending_tries', None)
                try:
                    _write_pointer(data)
                except OSError:
                    pass
                return (False, '新数据目录里还有内容没同步过去，已连试 3 次仍未成功，本次迁移已放弃（继续使用原目录）。没能搬过去的是：' + '；'.join(late))
            data['pending_tries'] = tries
            try:
                _write_pointer(data)
            except OSError:
                pass
            return (False, f'新数据目录里还有内容没同步过去，本次已继续使用原目录（第 {tries} 次，下次启动会再试）：' + '；'.join(late))
    data.pop('pending_tries', None)
    data['active'] = str(target)
    data.pop('pending', None)
    try:
        _write_pointer(data)
    except OSError as exc:
        return (False, f'写入配置失败：{exc.strerror or exc}')
    return (True, f'数据目录已切换到 {target}。')


def clear_pointer() -> None:
    """清掉指针（测试与「恢复默认」用）。"""
    try:
        pointer_file().unlink()
    except OSError:
        pass


def _staging_conflicts(staging: Path, target: Path) -> list[str]:
    """目标目录里有没有「同名但类型不同」的阻挡物（文件挡文件夹，或反过来）。"""
    problems = []
    for item in sorted(staging.iterdir()):
        dest = target / item.name
        if not (dest.exists() or dest.is_symlink()):
            continue
        if dest.is_dir() != item.is_dir():
            kind = '文件' if dest.is_file() else '文件夹'
            want = '文件夹' if item.is_dir() else '文件'
            problems.append(f'新目录里已有一个同名{kind}「{item.name}」，而这里需要放{want}')
            continue
        if dest.is_dir() and item.is_dir():
            problems.extend(_staging_conflicts(item, dest))
    return problems


def _commit_staging(staging: Path, target: Path, stamp: str) -> None:
    """把临时目录里的东西提交到目标目录。

    同名文件不直接覆盖，先改名成 ``<名字>.old-<stamp>`` 留档 —— 用户原来的存档
    永远有据可查；同名目录则逐项合并，冲突的文件同样留档。
    """
    for item in sorted(staging.iterdir()):
        dest = target / item.name
        if dest.exists() or dest.is_symlink():
            if dest.is_dir() and item.is_dir():
                for sub in sorted(item.iterdir()):
                    sub_dest = dest / sub.name
                    if sub_dest.exists() or sub_dest.is_symlink():
                        sub_dest.replace(sub_dest.with_name(f'{sub_dest.name}.old-{stamp}'))
                    shutil.move(str(sub), str(sub_dest))
                shutil.rmtree(item, ignore_errors=True)
                continue
            dest.replace(dest.with_name(f'{dest.name}.old-{stamp}'))
        shutil.move(str(item), str(dest))


def active_dir_problem() -> str:
    """当前生效的数据目录是否可用。空串=正常。

    只检查「用户显式迁移过」的目录（指针里有 active）：全新安装时目录本来就不存在，
    那是正常状态，首次保存会自动创建。

    存在的意义：U 盘拔了 / 目录被删 / 被改成只读时，以前程序会当成「新装」用空白班级
    开局，用户随手加一次分就把空白存档写进那个目录，看起来像「数据全丢了」。
    现在改成明确报错并拒绝写入，让用户先处理目录。
    """
    data = _read_pointer()
    active = data.get('active')
    if not active:
        return ''
    target = Path(str(active))
    if not target.exists():
        return f'数据目录 {target} 不存在（U 盘可能已拔出，或目录被删除）'
    if not target.is_dir():
        return f'数据目录 {target} 不是一个文件夹'
    probe = target / '.write_test'
    try:
        probe.write_text('ok', encoding='utf-8')
        probe.unlink()
    except OSError as exc:
        return f'数据目录 {target} 不可写（{exc.strerror or exc}）'
    return ''
