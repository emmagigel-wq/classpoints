# 打包成安装程序（构建链说明）

> 本文与 `packaging/` 下脚本是**依据取证材料重建**的构建文档（原 `packaging/`、
> `docs/` 没被打进 exe，无法逐字节还原）；exe 侧的一切行为判据仍以
> `tools/verify_installer.py`（16 个 installer 模块）与 `tools/verify.py`
> （78 个 app 模块）的字节码校验为准。

## 产物形态（从 app.exe / setup.exe 反推的既成事实）

- `班级积分系统.exe`：PyInstaller **onedir**（py 3.14 x64，`python314.dll`，
  入口脚本 `班级积分系统.py`，`console=False`，图标 `assets/app.ico`），
  连同 `_internal/`（PyQt5 / lxml / docx / setuptools / segno(进 PYZ) /
  `assets/ffmpeg.exe` / `android/viewer.html` / `cloudflared.exe` /
  CHANGELOG.md / README.md）。
- `setup.exe`：**单个自包含安装程序**——也是 PyInstaller 打的
  （入口 `installer/main.py`，onefile，内嵌资源 `payload.zip` +
  `Uninstall.exe` + `manifest.json` + `app.ico`）。安装/卸载全部由
  `installer` 包（本仓库 `classpoints-py/installer/`，16 模块 284 对象，
  `verify_installer.py` 可验）完成。**不依赖 Inno Setup**（README 原话）。

## 构建步骤（Windows）

```bat
:: 0) 前置：Python 3.14 x64 + pyinstaller + PyQt5 + openpyxl + python-docx + lxml
::    用户名含中文时用 ASCII 目录联接绕开 Qt 路径解析坑（README 原话）：
mklink /J C:\cpbuild %USERPROFILE%\cpbuild

:: 1) 应用：onedir
cd classpoints-py
pyinstaller --noconfirm packaging\classpoints.spec
::    → dist\班级积分系统\班级积分系统.exe (+ _internal)

:: 2) 卸载器：独立小 exe（build_payload 会把它作为资源收进 setup）
pyinstaller --noconfirm packaging\uninstaller.spec
::    → dist\Uninstall\Uninstall.exe

:: 3) 压 payload + 写 manifest（安装器资源的直接构造者，逐指令还原自 app 内 pyc）
python installer\build_payload.py --dist "dist\班级积分系统" --out build\installer
::    --version 缺省从 app\version.py 读取（版本号唯一来源）
::    → build\installer\{payload.zip, manifest.json, app.ico, Uninstall.exe}

:: 4) 安装器：onefile，注入第 3 步产物
pyinstaller --noconfirm packaging\installer.spec
::    → dist\安装程序\setup.exe，发布时改名
::      dist\班级积分系统-安装程序-vX.Y.Z.exe
```

构建输入资源（不重复入库，直接引用取证副本）：`recover/payload/app.ico`、
`recover/payload/_internal/assets/ffmpeg.exe`、`_internal/cloudflared.exe`、
`_internal/android/viewer.html`、`_internal/{CHANGELOG.md,README.md}`——在 Windows
上构建前把它们拷回 classpoints-py/ 的 assets/、android/ 等原位（或改 spec 路径）。

也可以一步到位：`powershell -ExecutionPolicy Bypass -File packaging\build_installer.ps1`
（它按 1→4 顺序调上面各步；`.ps1` 必须 UTF-8 带 BOM，见 `_tools/ensure_bom.py`）。

## manifest.json 字段（逐字取自 `installer/build_payload.py` 的反汇编常量表）

```json
{
  "schema": 1,
  "app_id": "classpoints",
  "name": "班级积分系统",
  "version": "<app/version.py 读出>",
  "built_at": "<%Y-%m-%dT%H:%M:%S%z>",
  "payload": { "file": "payload.zip", "files": <n>,
               "uncompressed_bytes": <n>, "zip_bytes": <n>, "sha256": "<hex>" },
  "source": { "dist": "<onedir 绝对路径>", "python": "<sys.version>" },
  "uninstaller": { "file": "Uninstall.exe", "bytes": <n>, "sha256": "<hex>" }
}
```

## 已知的坑（原文出自 README/CHANGELOG，构建脚本按此设计）

- `--add-data` 直接收 125MB onedir 目录 → onefile 冷启动要解几千个小文件；
  所以构建期压成**单个 payload.zip**，安装时用 Python `zipfile` 解压
  （中文名按 UTF-8，不用 PowerShell `Expand-Archive`）。
- `.bat` 只放 ASCII；`.ps1` 必须 UTF-8 带 BOM。
- 版本号唯一来源 `app/version.py`：构建期读出写进 manifest，运行时读 manifest，
  避免「文件名一个版本、程序里一个版本」。
- 装完必须 `_verify_install`（缺主程序 / 缺 `_internal` / 缺 `qwindows.dll`
  直接报「安装不完整」；缺 cloudflared 只是 warning）——逻辑在
  `installer/install_core.py`，已按字节码还原可验。
