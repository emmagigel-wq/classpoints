# -*- mode: python ; coding: utf-8 -*-
"""setup.exe —— 单个自包含安装程序（PyInstaller onefile）。

还原依据（recover/setup.exe 的 PyInstaller cookie/TOC，已逐条解析）：
  * pyver=314 / python314.dll，onefile（TOC 里有 pyi-contents-directory 且
    入口脚本为 **main**，即 spec 的入口脚本；与 installer/main.pyc 字节相同）
  * 内嵌资源（type=b）：payload.zip、Uninstall.exe、manifest.json、app.ico、
    整套 PyQt5 二进制（Qt5Core/Gui/Widgets/Network/Multimedia/MultimediaWidgets…）
  * 模块区（PYZ）含 installer 包全部 16 个模块 + app 无关的纯 stdlib
    → 印证 installer/constants.py 的说法：安装器不 import app 包。

用法（Windows，classpoints-py/ 下，先跑完 build_payload 生成 build/installer/*）：
    pyinstaller --noconfirm packaging\\installer.spec
产出：dist\\setup.exe（发布改名 班级积分系统-安装程序-vX.Y.Z.exe）
"""
import os

ROOT = os.path.abspath(os.path.join(SPECPATH, os.pardir))   # classpoints-py/
BUILD = os.path.join(ROOT, 'build', 'installer')            # build_payload --out

a = Analysis(
    [os.path.join(ROOT, 'installer', 'main.py')],           # 入口=main（与顶层 main.pyc 对应）
    pathex=[ROOT],
    binaries=[],
    datas=[
        (os.path.join(BUILD, 'payload.zip'), '.'),
        (os.path.join(BUILD, 'manifest.json'), '.'),
        (os.path.join(BUILD, 'Uninstall.exe'), '.'),
        (os.path.join(ROOT, 'assets', 'app.ico'), '.'),
    ],
    hiddenimports=[
        'installer.bundle',
        'installer.install_core',
        'installer.ui.pages',
        'installer.ui.style',
        'installer.ui.wizard',
        'installer.ui.worker',
        'installer.win_shortcut',
        'installer.winsys',
        'PyQt5.QtWidgets',
        'PyQt5.QtGui',
        'PyQt5.QtCore',
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=['tkinter'],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,                       # onefile：全部收进单 exe
    a.datas,
    name='setup',
    debug=False,
    strip=False,
    upx=False,
    console=False,                    # 双击即向导；--silent 走命令行分支
    icon=os.path.join(ROOT, 'assets', 'app.ico'),
)
