# -*- mode: python ; coding: utf-8 -*-
"""Uninstall.exe —— 卸载器独立打包（onedir，无 Qt）。

还原依据：setup.exe 内嵌资源里有独立的 `Uninstall.exe`（type=b 的 PyInstaller
二进制，build_payload.py 反汇编常量 `'[警告] 还没有 Uninstall.exe（先打卸载器）'`
说明构建顺序是「先卸载器、后 payload」）；入口 = `installer/uninstaller.py`
（37 对象、字节一致，支持 --silent / --dir / --cleanup --wait-pid）。
它刻意只用 install_core（无 Qt 的落盘逻辑），体积小、启动快——见 install_core
docstring「这样 Uninstall.exe 可以只带这一层」。

用法（Windows，classpoints-py/ 下）：
    pyinstaller --noconfirm packaging\\uninstaller.spec
产出：dist\\Uninstall\\Uninstall.exe（build_payload --dist 之外，由
      build_installer.ps1 拷到 build\\installer\\Uninstall.exe）
"""
import os

ROOT = os.path.abspath(os.path.join(SPECPATH, os.pardir))   # classpoints-py/

a = Analysis(
    [os.path.join(ROOT, 'installer', 'uninstaller.py')],
    pathex=[ROOT],
    binaries=[],
    datas=[],
    hiddenimports=[
        # winreg/ctypes 都是标准库； installer 包本身被自动发现
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=['PyQt5', 'tkinter', 'test'],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    exclude_binaries=True,
    name='Uninstall',
    debug=False,
    strip=False,
    upx=False,
    console=False,                     # 确认框走 ctypes MessageBoxW（uninstaller.py
    #  L56 ctypes.windll.user32.MessageBoxW），全程无 Qt —— 故 excludes 里去掉 PyQt5。
    icon=None,
)

coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='Uninstall')
