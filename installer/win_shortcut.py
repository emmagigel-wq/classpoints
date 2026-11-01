"""用 COM（IShellLinkW + IPersistFile）创建/删除 .lnk 快捷方式。

为什么不引 pywin32：打包环境刻意精简，目标机更不能联网装依赖。
为什么不用 PowerShell 的 WScript.Shell：要多起一个进程，中文路径经
命令行二次转码容易出问题（现有 install.ps1 就吃过这个亏）。
这里直接 ctypes 调 ole32，失败时再退回 PowerShell（双保险）。
"""

from __future__ import annotations

import base64
import ctypes
import subprocess
from ctypes import POINTER, byref, c_void_p, c_wchar_p
from pathlib import Path
from uuid import UUID

CLSID_SHELLLINK = UUID('00021401-0000-0000-C000-000000000046')
IID_ISHELLLINKW = UUID('000214F9-0000-0000-C000-000000000046')
IID_IPERSISTFILE = UUID('0000010B-0000-0000-C000-000000000046')

CLSCTX_INPROC_SERVER = 1
COINIT_APARTMENTTHREADED = 2
RPC_E_CHANGED_MODE = 2147549446
S_OK = 0
S_FALSE = 1

CREATE_NO_WINDOW = 134217728

_VT_QUERYINTERFACE = 0
_VT_RELEASE = 2
_VT_SETDESCRIPTION = 7
_VT_SETWORKINGDIRECTORY = 9
_VT_SETICONLOCATION = 17
_VT_SETPATH = 20
_VT_PERSIST_SAVE = 6


class GUID(ctypes.Structure):
    _fields_ = [
        ('Data1', ctypes.c_ulong),
        ('Data2', ctypes.c_ushort),
        ('Data3', ctypes.c_ushort),
        ('Data4', ctypes.c_ubyte * 8),
    ]

    @classmethod
    def from_uuid(cls, value: UUID) -> 'GUID':
        guid = cls()
        ctypes.memmove(byref(guid), value.bytes_le, 16)
        return guid


HRESULT = ctypes.c_long
ULONG = ctypes.c_ulong


def _vtable_method(ptr: c_void_p, index: int, restype, *argtypes):
    """取 COM 接口 vtable 第 index 个方法并转成可调用函数。"""
    vtable = ctypes.cast(ptr, POINTER(POINTER(c_void_p)))[0]
    prototype = ctypes.WINFUNCTYPE(restype, c_void_p, *argtypes)
    return prototype(vtable[index])


def _check(hr: int, what: str) -> None:
    if hr < 0:
        raise OSError(f'{what} 失败 HRESULT=0x{hr & 0xFFFFFFFF:08X}')


def _parse_dir(path: Path) -> Path:
    return Path(path).expanduser().resolve()


class _ComApartment:
    """RAII：确保当前线程初始化 COM。"""

    def __init__(self) -> None:
        self._needs_uninit = False

    def __enter__(self) -> '_ComApartment':
        hr = ctypes.windll.ole32.CoInitializeEx(None, COINIT_APARTMENTTHREADED)
        if hr in (S_OK, S_FALSE):
            self._needs_uninit = hr == S_OK
            return self
        if (hr & 0xFFFFFFFF) == RPC_E_CHANGED_MODE:
            self._needs_uninit = False
            return self
        _check(hr, 'CoInitializeEx')
        return self

    def __exit__(self, *exc_info) -> None:
        if self._needs_uninit:
            ctypes.windll.ole32.CoUninitialize()


def _release(ptr: c_void_p) -> None:
    if not ptr:
        return
    try:
        _vtable_method(ptr, _VT_RELEASE, ULONG)(ptr)
    except Exception:
        pass


def create_shortcut(
    lnk_path: Path,
    target: Path,
    working_dir: Path | None = None,
    description: str = '',
    icon: Path | None = None,
) -> None:
    """创建 .lnk 快捷方式（原生 COM 实现）。"""
    lnk_path = _parse_dir(lnk_path)
    target = _parse_dir(target)
    lnk_path.parent.mkdir(parents=True, exist_ok=True)

    with _ComApartment():
        ole32 = ctypes.windll.ole32

        clsid = GUID.from_uuid(CLSID_SHELLLINK)
        iid_link = GUID.from_uuid(IID_ISHELLLINKW)
        link = c_void_p()
        hr = ole32.CoCreateInstance(
            byref(clsid),
            None,
            CLSCTX_INPROC_SERVER,
            byref(iid_link),
            byref(link),
        )
        _check(hr, 'CoCreateInstance(ShellLink)')
        persist = c_void_p()
        try:
            _check(
                _vtable_method(link, _VT_SETPATH, HRESULT, c_wchar_p)(link, str(target)),
                'IShellLink::SetPath',
            )
            if working_dir is not None:
                _check(
                    _vtable_method(link, _VT_SETWORKINGDIRECTORY, HRESULT, c_wchar_p)(
                        link, str(_parse_dir(working_dir))
                    ),
                    'IShellLink::SetWorkingDirectory',
                )
            if description:
                _check(
                    _vtable_method(link, _VT_SETDESCRIPTION, HRESULT, c_wchar_p)(link, description),
                    'IShellLink::SetDescription',
                )
            icon_file = icon if icon is not None else target
            _check(
                _vtable_method(link, _VT_SETICONLOCATION, HRESULT, c_wchar_p, ctypes.c_int)(
                    link, str(icon_file), 0
                ),
                'IShellLink::SetIconLocation',
            )
            iid_persist = GUID.from_uuid(IID_IPERSISTFILE)
            _check(
                _vtable_method(
                    link,
                    _VT_QUERYINTERFACE,
                    HRESULT,
                    POINTER(GUID),
                    POINTER(c_void_p),
                )(link, byref(iid_persist), byref(persist)),
                'QueryInterface(IPersistFile)',
            )
            _check(
                _vtable_method(persist, _VT_PERSIST_SAVE, HRESULT, c_wchar_p, ctypes.c_int)(
                    persist, str(lnk_path), 1
                ),
                'IPersistFile::Save',
            )
        finally:
            _release(persist)
            _release(link)


def _create_shortcut_via_powershell(
    lnk_path: Path,
    target: Path,
    working_dir: Path | None,
    description: str,
    icon: Path | None,
) -> None:
    """兜底：起一个隐藏的 PowerShell 用 WScript.Shell 建快捷方式。"""

    def _q(value: Path | str) -> str:
        return "'" + str(value).replace("'", "''") + "'"

    icon_arg = icon if icon is not None else target

    lines = [
        '$shell = New-Object -ComObject WScript.Shell',
        f'$sc = $shell.CreateShortcut({_q(lnk_path)})',
        f'$sc.TargetPath = {_q(target)}',
        f'$sc.WorkingDirectory = {_q(working_dir if working_dir is not None else target.parent)}',
        f'$sc.IconLocation = {_q(str(icon_arg) + ",0")}',
        f'$sc.Description = {_q(description)}',
        '$sc.Save()',
    ]

    script = '; '.join(lines)
    encoded = base64.b64encode(script.encode('utf-16-le')).decode('ascii')

    result = subprocess.run(
        [
            'powershell.exe',
            '-NoProfile',
            '-NonInteractive',
            '-ExecutionPolicy',
            'Bypass',
            '-EncodedCommand',
            encoded,
        ],
        capture_output=True,
        creationflags=CREATE_NO_WINDOW,
        timeout=60,
    )

    if result.returncode != 0:
        raise OSError(
            'PowerShell 创建快捷方式失败：'
            + result.stderr.decode('utf-8', 'replace').strip()
        )


def create_shortcut_safe(
    lnk_path: Path,
    target: Path,
    working_dir: Path | None = None,
    description: str = '',
    icon: Path | None = None,
) -> None:
    """先试原生 COM，失败再退回 PowerShell；两条都失败才抛异常。"""
    try:
        create_shortcut(lnk_path, target, working_dir, description, icon)
    except Exception as native_error:
        try:
            _create_shortcut_via_powershell(lnk_path, target, working_dir, description, icon)
        except Exception as fallback_error:
            raise OSError(
                f'创建快捷方式失败：{lnk_path}\n  原生 COM：{native_error}\n  PowerShell 兜底：{fallback_error}'
            ) from fallback_error


def remove_shortcut(path: Path) -> bool:
    """删除一个快捷方式；文件本来就不在也算成功。"""
    try:
        Path(path).unlink()
        return True
    except FileNotFoundError:
        return True
    except OSError:
        return False
