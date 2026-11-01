"""安装/卸载的核心逻辑（纯 stdlib，不含 Qt，也不 import app 包）。

分成两层：
  * 本模块 —— 所有会写磁盘/注册表/快捷方式的事，GUI 与静默模式共用；
  * installer/ui/* —— 只负责界面与进度展示。
这样 Uninstall.exe 可以只带这一层，体积小、启动快。
"""

from __future__ import annotations

import os
import shutil
import sys
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from installer import bundle, win_shortcut, winsys
from installer.constants import (
    APP_EXE_NAME,
    APP_ICON,
    APP_NAME,
    DESKTOP_LNK_NAME,
    PUBLISHER,
    REG_UNINSTALL_SUBKEY,
    START_MENU_DIR_NAME,
    UNINSTALLER_NAME,
)


@dataclass(frozen=True)
class Step:
    key: str
    label: str
    weight: float


STEPS: tuple[Step, ...] = (
    Step('prepare', '准备安装目录', 3),
    Step('stop', '关闭正在运行的程序', 2),
    Step('extract', '解压程序文件', 79),
    Step('uninstaller', '写入卸载程序', 4),
    Step('shortcuts', '创建快捷方式', 4),
    Step('registry', '写入卸载信息', 4),
    Step('verify', '校验安装完整性', 4),
)
_STEP_TOTAL = len(STEPS)
_STEP_WEIGHT_BY_KEY = {step.key: step.weight for step in STEPS}


@dataclass
class InstallProgress:
    """一次进度回调的内容。step_percent=0 表示步骤开始，=100 表示步骤完成。"""

    step_index: int
    step_total: int
    step_key: str
    step_label: str
    step_percent: int
    overall_percent: int
    message: str = ''


ProgressCallback = Callable[[InstallProgress], None]


class InstallError(RuntimeError):
    """安装失败（消息直接给用户看）。"""


@dataclass
class InstallOptions:
    target_dir: Path
    version: str
    create_desktop_shortcut: bool = True
    clean_existing: bool = False


@dataclass
class PayloadInfo:
    files: int
    uncompressed_bytes: int
    zip_bytes: int
    path: Path


@dataclass
class TargetCheck:
    path: Path
    ok: bool
    message: str = ''
    warning: str = ''
    exists: bool = False
    nonempty: bool = False
    is_previous_install: bool = False
    free_bytes: int = -1


@dataclass
class InstallResult:
    target_dir: Path
    version: str
    files_installed: int = 0
    installed_bytes: int = 0
    start_menu_lnk: Path | None = None
    desktop_lnk: Path | None = None
    uninstaller: Path | None = None
    registry_key: str = REG_UNINSTALL_SUBKEY
    warnings: list[str] = field(default_factory=list)


def _progress_reporter(
    callback: ProgressCallback | None,
):
    """把「步骤内进度」换算成「整体进度」再回调，统一节流逻辑。"""
    if callback is None:
        return lambda **kwargs: None

    def report(
        step: Step,
        step_percent: float,
        message: str = '',
        step_index: int | None = None,
    ) -> None:
        index = step_index if step_index is not None else [s.key for s in STEPS].index(step.key) + 1
        done = sum(_STEP_WEIGHT_BY_KEY[s.key] for s in STEPS[:index - 1])
        percent = int(round(step_percent))
        overall = done + step.weight * max(0, min(100, percent)) / 100.0
        callback(
            InstallProgress(
                step_index=index,
                step_total=_STEP_TOTAL,
                step_key=step.key,
                step_label=step.label,
                step_percent=max(0, min(100, percent)),
                overall_percent=max(0, min(100, int(round(overall)))),
                message=message,
            )
        )

    return report


def required_bytes(payload: PayloadInfo | None = None) -> int:
    """安装所需磁盘空间：payload 解压后大小 + 卸载程序 + 一点余量。"""
    info = payload or inspect_payload()
    extra = 0
    uninstaller = bundle.uninstaller_source()
    if uninstaller.is_file():
        extra += uninstaller.stat().st_size
    else:
        extra += 8388608
    return info.uncompressed_bytes + extra + 2097152


def inspect_payload() -> PayloadInfo:
    """读安装包里的 payload.zip，统计文件数/解压后体积（用于显示所需空间）。"""
    path = bundle.payload_path()
    if not path.is_file():
        raise InstallError(
            f'安装包损坏：缺少程序数据 {path.name}。\n请重新下载完整的安装程序。')
    files = 0
    total = 0
    try:
        with zipfile.ZipFile(path) as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                files += 1
                total += info.file_size
    except zipfile.BadZipFile as exc:
        raise InstallError(f'安装包损坏：{path.name} 不是有效的 zip（{exc}）') from exc
    return PayloadInfo(
        files=files,
        uncompressed_bytes=total,
        zip_bytes=path.stat().st_size,
        path=path,
    )


def default_install_dir() -> Path:
    return winsys.local_appdata() / 'Programs' / APP_NAME


def read_registry_value(name: str) -> str | None:
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_UNINSTALL_SUBKEY) as key:
            value, _kind = winreg.QueryValueEx(key, name)
            return str(value)
    except OSError:
        return None


def installed_location() -> Path | None:
    """上一版装在哪（覆盖安装时用来预填目录）。"""
    value = read_registry_value('InstallLocation')
    if not value:
        return None
    path = Path(value)
    return path if path.parent != path else None


def installed_version() -> str | None:
    return read_registry_value('DisplayVersion')


def _write_registry(
    target_dir: Path,
    version: str,
    uninstaller: Path,
    estimated_kb: int,
) -> None:
    import winreg

    app_exe = target_dir / APP_EXE_NAME
    key = winreg.CreateKeyEx(
        winreg.HKEY_CURRENT_USER, REG_UNINSTALL_SUBKEY, 0, winreg.KEY_WRITE)
    try:
        values = [
            ('DisplayName', APP_NAME, winreg.REG_SZ),
            ('DisplayVersion', version, winreg.REG_SZ),
            ('DisplayIcon', f'{app_exe},0', winreg.REG_SZ),
            ('InstallLocation', str(target_dir), winreg.REG_SZ),
            ('UninstallString', f'"{uninstaller}"', winreg.REG_SZ),
            ('QuietUninstallString', f'"{uninstaller}" --silent', winreg.REG_SZ),
            ('Publisher', PUBLISHER, winreg.REG_SZ),
            ('InstallDate', time.strftime('%Y%m%d'), winreg.REG_SZ),
            ('EstimatedSize', int(estimated_kb), winreg.REG_DWORD),
            ('NoModify', 1, winreg.REG_DWORD),
            ('NoRepair', 1, winreg.REG_DWORD),
        ]
        for name, value, kind in values:
            winreg.SetValueEx(key, name, 0, kind, value)
    finally:
        key.Close()


def remove_registry() -> bool:
    """删除 HKCU 卸载登记，返回是否真的删了/本来就没有。"""
    import winreg

    def _delete_tree(root, subkey: str) -> bool:
        try:
            with winreg.OpenKey(root, subkey, 0, winreg.KEY_READ | winreg.KEY_WRITE) as key:
                while True:
                    try:
                        child = winreg.EnumKey(key, 0)
                    except OSError:
                        break
                    _delete_tree(root, subkey + '\\' + child)
        except FileNotFoundError:
            return True
        except OSError:
            return False
        try:
            winreg.DeleteKey(root, subkey)
            return True
        except FileNotFoundError:
            return True
        except OSError:
            return False

    return _delete_tree(winreg.HKEY_CURRENT_USER, REG_UNINSTALL_SUBKEY)


def start_menu_lnk_path() -> Path:
    return winsys.programs_menu() / START_MENU_DIR_NAME / f'{APP_NAME}.lnk'


def desktop_lnk_path() -> Path:
    return winsys.desktop_dir() / DESKTOP_LNK_NAME


def create_shortcuts(target_dir: Path, version: str, desktop: bool) -> tuple[Path, Path | None]:
    """创建开始菜单快捷方式（总是）+ 桌面快捷方式（可选）。"""
    app_exe = target_dir / APP_EXE_NAME
    icon = target_dir / APP_ICON
    description = f'{APP_NAME} v{version}'
    if not icon.is_file():
        icon = app_exe if app_exe.is_file() else None

    menu_lnk = start_menu_lnk_path()
    win_shortcut.create_shortcut_safe(menu_lnk, app_exe, target_dir, description, icon)

    desktop_lnk = None
    if desktop:
        desktop_lnk = desktop_lnk_path()
        win_shortcut.create_shortcut_safe(desktop_lnk, app_exe, target_dir, description, icon)

    return menu_lnk, desktop_lnk


def create_desktop_shortcut(target_dir: Path, version: str) -> Path:
    """单独补一个桌面快捷方式（完成页勾选后调用）。"""
    app_exe = target_dir / APP_EXE_NAME
    icon = target_dir / APP_ICON
    if not icon.is_file():
        icon = app_exe
    lnk = desktop_lnk_path()
    win_shortcut.create_shortcut_safe(lnk, app_exe, target_dir, f'{APP_NAME} v{version}', icon)
    return lnk


def remove_shortcuts() -> list[str]:
    """删除桌面/开始菜单快捷方式，返回失败的路径说明。"""
    problems = []
    if not win_shortcut.remove_shortcut(desktop_lnk_path()):
        problems.append(f'删不掉桌面快捷方式：{desktop_lnk_path()}')
    menu_dir = winsys.programs_menu() / START_MENU_DIR_NAME
    menu_lnk = menu_dir / f'{APP_NAME}.lnk'
    if not win_shortcut.remove_shortcut(menu_lnk):
        problems.append(f'删不掉开始菜单快捷方式：{menu_lnk}')
    try:
        if menu_dir.is_dir() and not any(menu_dir.iterdir()):
            menu_dir.rmdir()
    except OSError:
        pass
    return problems


_PROTECTED_PREFIXES = (
    os.environ.get('WINDIR', 'C:\\Windows'),
    os.environ.get('ProgramFiles', 'C:\\Program Files'),
    os.environ.get('ProgramFiles(x86)', 'C:\\Program Files (x86)'),
    os.environ.get('ProgramData', 'C:\\ProgramData'),
)


def validate_target(path: Path, check_free: bool = True) -> TargetCheck:
    """校验安装目录是否可用（不修改磁盘）。"""
    try:
        target = Path(path).expanduser()
    except (TypeError, ValueError):
        return TargetCheck(Path(str(path)), False, '安装目录不合法')

    raw = str(target).strip()
    if not raw:
        return TargetCheck(target, False, '请选择安装目录')
    try:
        target = target.resolve()
    except OSError:
        target = Path(os.path.abspath(raw))

    if target.parent == target:
        return TargetCheck(target, False, '不能直接装到磁盘根目录，请建一个子文件夹')
    lower = str(target).casefold().rstrip('\\')
    for prefix in _PROTECTED_PREFIXES:
        if not prefix:
            continue
        prefix_lower = os.path.abspath(prefix).casefold().rstrip('\\')
        if lower == prefix_lower or lower.startswith(prefix_lower + '\\'):
            return TargetCheck(
                target, False,
                '这个位置通常需要管理员权限。安装程序按「当前用户」安装，\n请选择用户目录（默认位置即可）。')

    exists = target.exists()
    nonempty = False
    previous = installed_location()
    is_previous = False
    if exists:
        if not target.is_dir():
            return TargetCheck(target, False, '目标路径已经是一个文件，不能装在这里')
        try:
            nonempty = any(target.iterdir())
        except OSError as exc:
            return TargetCheck(target, False, f'读不了目标目录：{exc}')
        app_exe = target / APP_EXE_NAME
        is_previous = app_exe.is_file() or (previous is not None and previous == target)
    if not winsys.can_write_under(target):
        return TargetCheck(target, False, '没有写入权限，请换一个目录或检查磁盘')
    free = winsys.free_bytes(target) if check_free else -1
    warning = ''
    if is_previous:
        version = installed_version() or '未知版本'
        warning = f'检测到已安装（v{version}），继续将覆盖安装'
    elif nonempty:
        warning = '目标目录不是空的，安装时会清空其中的文件'
    return TargetCheck(
        path=target,
        ok=True,
        warning=warning,
        exists=exists,
        nonempty=nonempty,
        is_previous_install=is_previous,
        free_bytes=free,
    )


def _clean_dir(target: Path) -> None:
    """清空目标目录里的内容（保留目录本身：它可能是挂载点/被占用的父目录）。"""
    for child in target.iterdir():
        try:
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child, ignore_errors=False)
                continue
            child.unlink()
        except OSError as exc:
            raise InstallError(
                f'无法清理旧文件：{child}\n{exc}\n请先关闭正在运行的班级积分系统，或换一个安装目录。'
            ) from exc


def _extract_payload(
    target: Path,
    report,
    step: Step,
) -> tuple[int, int]:
    """解压 payload.zip 到 target，返回 (文件数, 字节数)。

    坑：中文文件名。这里只用 Python zipfile（不走 PowerShell Expand-Archive），
    构建时 zipfile 已按 UTF-8 打标，解压出来的中文名不会乱码。
    """
    payload = bundle.payload_path()
    info = inspect_payload()
    total = max(1, info.uncompressed_bytes)
    files = 0
    written = 0
    target_resolved = target.resolve()
    last_percent = -1
    with zipfile.ZipFile(payload) as zf:
        for entry in zf.infolist():
            name = entry.filename
            if entry.is_dir():
                continue
            dest = (target / name).resolve()
            try:
                dest.relative_to(target_resolved)
            except ValueError:
                raise InstallError(f'安装包内容异常（路径越界）：{name}')
            dest.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(entry) as src, open(dest, 'wb') as out:
                shutil.copyfileobj(src, out, 1048576)
            files += 1
            written += entry.file_size
            percent = int(written * 100 / total)
            if percent != last_percent:
                report(step, percent, f'{files}/{info.files} 个文件')
                last_percent = percent
    report(step, 100, f'{files} 个文件')
    return files, written


def _install_uninstaller(target: Path) -> Path:
    source = bundle.uninstaller_source()
    if not source.is_file():
        raise InstallError(
            '安装包损坏：缺少卸载程序组件（Uninstall.exe）。\n请重新下载完整的安装程序。')
    dest = target / UNINSTALLER_NAME
    shutil.copy2(source, dest)
    return dest


def _verify_install(target: Path) -> list[str]:
    """装完必须验目录结构：结构错了比缺文件更难查（历次踩坑）。"""
    warnings = []
    app_exe = target / APP_EXE_NAME
    if not app_exe.is_file():
        raise InstallError(f'安装不完整：缺少主程序 {app_exe.name}')
    internal = target / '_internal'
    if not internal.is_dir():
        raise InstallError('安装不完整：缺少 _internal 目录')
    shutil_which = internal / 'qwindows.dll'
    if not shutil_which.is_file() and not any(internal.rglob('qwindows.dll')):
        raise InstallError('安装不完整：缺少 Qt 平台插件 qwindows.dll')
    if not (internal / 'cloudflared.exe').is_file():
        warnings.append('安装包里没有内置穿透组件（cloudflared.exe），公网访问需手动下载')
    if (target / APP_ICON).is_file() is False:
        pass
    return warnings


def install(
    options: InstallOptions,
    progress: ProgressCallback | None = None,
) -> InstallResult:
    """执行完整安装。GUI 与 --silent 共用这一条通路。"""
    target = Path(options.target_dir).expanduser().resolve()
    report = _progress_reporter(progress)
    warnings = []

    step = STEPS[0]
    report(step, 0)
    check = validate_target(target)
    if not check.ok:
        raise InstallError(check.message)
    if check.warning:
        pass
    try:
        if check.exists and (check.nonempty or check.is_previous_install):
            if not check.is_previous_install and not options.clean_existing:
                raise InstallError(
                    f'目标目录不是空目录，且不是已安装的{APP_NAME}：\n{target}\n'
                    '请换一个空目录，或确认清空后再装。')
            _clean_dir(target)
        target.mkdir(parents=True, exist_ok=True)
    except InstallError:
        raise
    except OSError as exc:
        raise InstallError(f'创建安装目录失败：{target}\n{exc}') from exc

    report(step, 100)

    step = STEPS[1]
    report(step, 0)
    stop_problems = winsys.stop_running_app(APP_EXE_NAME)
    warnings.extend(stop_problems)
    report(step, 100)

    step = STEPS[2]
    report(step, 0)
    files, written = _extract_payload(target, report, step)

    step = STEPS[3]
    report(step, 0)
    uninstaller = _install_uninstaller(target)
    report(step, 100)

    step = STEPS[4]
    report(step, 0)
    menu_lnk, desktop_lnk = create_shortcuts(
        target, options.version, desktop=options.create_desktop_shortcut)
    report(step, 100)

    step = STEPS[5]
    report(step, 0)
    estimated_kb = max(1, int(round(written / 1024)))
    try:
        _write_registry(target, options.version, uninstaller, estimated_kb)
    except OSError as exc:
        raise InstallError(f'写入卸载登记失败（HKCU）：{exc}') from exc
    report(step, 100)

    step = STEPS[6]
    report(step, 0)
    warnings.extend(_verify_install(target))
    report(step, 100)

    return InstallResult(
        target_dir=target,
        version=options.version,
        files_installed=files,
        installed_bytes=written,
        start_menu_lnk=menu_lnk,
        desktop_lnk=desktop_lnk,
        uninstaller=uninstaller,
        warnings=warnings,
    )


def delete_install_files(target: Path, keep: set[Path] | None = None) -> list[str]:
    """删除安装目录里的所有内容，但保留 keep 里的文件（通常是正在运行的卸载程序自己）。"""
    problems = []
    keep_resolved = {p.resolve() for p in (keep or set())}

    def _is_kept(path: Path) -> bool:
        try:
            return path.resolve() in keep_resolved
        except OSError:
            return False

    if not target.is_dir():
        return problems
    for child in sorted(target.iterdir(), reverse=True):
        if _is_kept(child):
            continue
        try:
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child, ignore_errors=False)
                continue
            child.unlink()
        except OSError as exc:
            problems.append(f'{child.name}: {exc.strerror or exc}')
    return problems


def remove_uninstall_artifacts() -> list[str]:
    """删除快捷方式 + 注册表卸载项，返回失败说明。"""
    problems = remove_shortcuts()
    if not remove_registry():
        problems.append('删除注册表卸载项失败')
    return problems


def format_registry_display() -> str:
    return f'HKCU\\{REG_UNINSTALL_SUBKEY}'
