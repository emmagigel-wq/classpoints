"""安装包资源定位：打包态读 PyInstaller 的 _MEIPASS，开发态读构建暂存目录。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from installer.constants import APP_ICON, MANIFEST_JSON, PAYLOAD_ZIP, UNINSTALLER_NAME


def frozen() -> bool:
    """当前进程是否由 PyInstaller 打出来的 exe 启动。"""
    return bool(getattr(sys, 'frozen', False))


def bundle_dir() -> Path:
    """随 exe 打包进去的资源所在目录。

    打包态：PyInstaller onefile 会解压到 sys._MEIPASS（临时目录）。
    开发态：构建脚本把 payload 写在 build/installer/ 下，源码直接跑也能用。
    """
    meipass = getattr(sys, '_MEIPASS', None)
    if meipass:
        return Path(meipass)
    return Path(__file__).resolve().parent.parent / 'build' / 'installer'


def resource(name: str) -> Path:
    return bundle_dir() / name


def payload_path() -> Path:
    return resource(PAYLOAD_ZIP)


def uninstaller_source() -> Path:
    return resource(UNINSTALLER_NAME)


def icon_path() -> Path | None:
    path = resource(APP_ICON)
    if path.is_file():
        return path
    return None


def load_manifest() -> dict:
    """读取构建期写入的安装清单（版本号、payload 体积等）。"""
    path = resource(MANIFEST_JSON)
    if not path.is_file():
        raise FileNotFoundError(
            f'缺少安装清单：{path}\n请先运行 packaging\\build_installer.ps1 生成安装包。')
    with open(path, encoding='utf-8') as fh:
        data = json.load(fh)
    if not data.get('version'):
        raise ValueError(f'安装清单缺少 version 字段：{path}')
    return data
