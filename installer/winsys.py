"""Windows 系统交互（纯 stdlib，不依赖 Qt / pywin32）。

安装器是 PyInstaller 打出来的 exe，目标机没有 Python 也没有 pywin32，
所以系统文件夹、磁盘空间、结束进程这些事全部用 ctypes 直接调 Win32。
"""

from __future__ import annotations
import ctypes
import os
import shutil
import subprocess
import time
from ctypes import wintypes
from pathlib import Path

CSIDL_PROGRAMS = 0x02
CSIDL_STARTMENU = 0x0B
CSIDL_DESKTOPDIRECTORY = 0x10
CSIDL_APPDATA = 0x1A
CSIDL_LOCAL_APPDATA = 0x1C
CSIDL_PROFILE = 0x28
SHGFP_TYPE_CURRENT = 0x00

CREATE_NO_WINDOW = 0x08000000
DETACHED_PROCESS = 0x00000008

MAX_PATH = 260


def _shell_folder(csidl: int) -> Path:
    """用 SHGetFolderPathW 取系统文件夹（比拼 %USERPROFILE% 可靠：
    桌面/开始菜单可能被 OneDrive 或组策略重定向）。"""
    buf = ctypes.create_unicode_buffer(MAX_PATH + 260)
    hr = ctypes.windll.shell32.SHGetFolderPathW(None, csidl, None, SHGFP_TYPE_CURRENT, buf)
    if hr != 0:
        raise OSError(f'SHGetFolderPathW(csidl=0x{csidl:X}) 失败 HRESULT=0x{hr & 0xFFFFFFFF:08X}')
    return Path(buf.value)


def local_appdata() -> Path:
    return _shell_folder(CSIDL_LOCAL_APPDATA)


def appdata() -> Path:
    return _shell_folder(CSIDL_APPDATA)


def user_profile() -> Path:
    return _shell_folder(CSIDL_PROFILE)


def programs_menu() -> Path:
    """开始菜单「程序」目录。"""
    return _shell_folder(CSIDL_PROGRAMS)


def desktop_dir() -> Path:
    return _shell_folder(CSIDL_DESKTOPDIRECTORY)


def data_dir() -> Path:
    """班级数据目录（%APPDATA%\\班级积分系统），卸载时保留。"""
    from installer.constants import DATA_DIR_NAME
    return appdata() / DATA_DIR_NAME


def format_bytes(size: float) -> str:
    """给人看的体积：1.2 GB / 125.3 MB / 512 KB。"""
    units = ('B', 'KB', 'MB', 'GB', 'TB')
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            if unit == 'B':
                return f'{int(value)} B'
            else:
                return f'{value:.1f} {unit}'
        value /= 1024.0
    return f'{value:.1f} TB'


def nearest_existing(path: Path) -> Path:
    """向上找到第一个存在的目录（用户可能输入了一个还没建的深层路径）。"""
    current = path
    while not current.exists():
        parent = current.parent
        if parent == current:
            break
        current = parent
    return current


def free_bytes(path: Path) -> int:
    """指定路径所在磁盘的可用空间。"""
    try:
        return shutil.disk_usage(str(nearest_existing(path))).free
    except OSError:
        return -1


def can_write_under(path: Path) -> bool:
    """在最近的已存在父目录里试建一个临时文件，判断是否可写。

    比 os.access 可靠：Windows 上 os.access 对 ACL 的判断经常是错的。
    """
    probe_dir = nearest_existing(path)
    if not probe_dir.is_dir():
        return False
    probe = probe_dir / f'.cp_write_probe_{os.getpid()}'
    try:
        probe.write_bytes(b'probe')
        probe.unlink()
        return True
    except OSError:
        try:
            probe.unlink()
        except OSError:
            pass
        return False


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ('dwSize', wintypes.DWORD),
        ('cntUsage', wintypes.DWORD),
        ('th32ProcessID', wintypes.DWORD),
        ('th32DefaultHeapID', ctypes.POINTER(ctypes.c_ulong)),
        ('th32ModuleID', wintypes.DWORD),
        ('cntThreads', wintypes.DWORD),
        ('th32ParentProcessID', wintypes.DWORD),
        ('pcPriClassBase', ctypes.c_long),
        ('dwFlags', wintypes.DWORD),
        ('szExeFile', wintypes.WCHAR * 260),
    ]


TH32CS_SNAPPROCESS = 2
_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


def _process_ids(image_name: str) -> list[int]:
    """按 exe 名找进程 PID（CreateToolhelp32Snapshot，不依赖 tasklist 的文字输出）。"""
    kernel32 = ctypes.windll.kernel32
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.Process32FirstW.restype = wintypes.BOOL
    kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    kernel32.Process32NextW.restype = wintypes.BOOL
    kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snapshot or snapshot == _INVALID_HANDLE_VALUE:
        return []
    entry = PROCESSENTRY32W()
    entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
    pids = []
    try:
        ok = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        target = image_name.casefold()
        while ok:
            if entry.szExeFile.casefold() == target:
                pids.append(int(entry.th32ProcessID))
            ok = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)
    return pids


def is_process_running(image_name: str) -> bool:
    return bool(_process_ids(image_name))


def is_pid_alive(pid: int) -> bool:
    """pid 对应的进程是否还活着（卸载程序的延迟清理用它等父进程退出）。"""
    if pid <= 0:
        return False
    kernel32 = ctypes.windll.kernel32
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    PROCESS_QUERY_LIMITED_INFORMATION = 4096
    STILL_ACTIVE = 259
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not handle:
        return False
    try:
        code = wintypes.DWORD(0)
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return False
        return code.value == STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def stop_running_app(image_name: str, timeout: float = 6.0) -> list[str]:
    """结束正在运行的班级积分系统，返回失败说明（空列表=全部结束干净）。

    覆盖安装/卸载前必须做：exe 和 _internal 里的 dll 被占用时既删不掉也覆盖不了。
    """
    messages = []
    if not is_process_running(image_name):
        return messages
    try:
        subprocess.run(
            ['taskkill', '/F', '/T', '/IM', image_name],
            capture_output=True,
            creationflags=CREATE_NO_WINDOW,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        messages.append(f'taskkill 调用失败：{exc}')
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not is_process_running(image_name):
            return messages
        time.sleep(0.2)
    messages.append(f'{image_name} 仍在运行，无法安全覆盖/卸载')
    return messages


def open_folder(path: Path) -> None:
    """在资源管理器里打开目录（安装完的「打开文件夹」用）。"""
    try:
        os.startfile(str(path))
    except OSError:
        return None
