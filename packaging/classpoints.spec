# -*- mode: python ; coding: utf-8 -*-
"""班级积分系统 · 应用打包（PyInstaller onedir）。

还原依据（recover/app.exe 的 PyInstaller cookie/TOC 与 recover/payload 目录）：
  * pyver=314 / python314.dll            → Python 3.14 x64
  * onedir 布局：班级积分系统.exe 与 _internal/ 同级
  * 入口 PYZ 模块名「班级积分系统」      → 入口脚本 班级积分系统.py
  * _internal 里带 PyQt5 / docx / lxml / setuptools / segno(进 PYZ) /
    assets/ffmpeg.exe / android/viewer.html / app.ico / CHANGELOG.md / README.md /
    cloudflared.exe（顶层随包二进制）
  * 无控制台：入口 docstring 明说「打包成 --noconsole 后 sys.stdout 是 None，
    未捕获异常落 crash.log」——正是取证行为。

在 Windows、classpoints-py/ 目录下执行：
    pyinstaller --noconfirm packaging\\classpoints.spec
产出：dist\\班级积分系统\\班级积分系统.exe（+ _internal\\）
随后 packaging\\build_installer.ps1 把它压进安装器（见 docs/BUILD.md）。
"""
import os

ROOT = os.path.abspath(os.path.join(SPECPATH, os.pardir))   # classpoints-py/

# 第三方可选二进制不随公开源码仓库分发。文件存在时才嵌入安装包；
# 缺失时由应用在需要相应功能时提示/下载，避免构建因可选组件缺失而失败。
optional_binaries = [
    (os.path.join(ROOT, 'assets', 'ffmpeg.exe'), 'assets'),
    (os.path.join(ROOT, 'cloudflared.exe'), '.'),
]
bundled_binaries = []
for source_path, destination in optional_binaries:
    if os.path.isfile(source_path):
        bundled_binaries.append((source_path, destination))
    else:
        print('[spec] 可选第三方组件未随包提供，构建时跳过：' + source_path)

a = Analysis(
    [os.path.join(ROOT, '班级积分系统.py')],
    pathex=[ROOT],
    binaries=bundled_binaries,
    datas=[
        (os.path.join(ROOT, 'assets', 'app.ico'), '.'),
        (os.path.join(ROOT, 'android', 'viewer.html'), 'android'),
        (os.path.join(ROOT, 'CHANGELOG.md'), '.'),
        (os.path.join(ROOT, 'README.md'), '.'),
    ],
    hiddenimports=[
        'PyQt5.QtMultimedia',
        'PyQt5.QtMultimediaWidgets',
        'PyQt5.QtNetwork',
        'segno',
        'docx',
        'openpyxl',
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
    exclude_binaries=True,
    name='班级积分系统',
    debug=False,
    strip=False,
    upx=False,
    console=False,                 # 无控制台窗口；崩溃信息落 crash.log
    icon=os.path.join(ROOT, 'assets', 'app.ico'),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name='班级积分系统',
)
