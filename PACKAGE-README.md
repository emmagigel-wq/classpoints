# 班级积分系统 · 源码还原包

这份包是从 `setup.exe` / `app.exe` 的 PyInstaller 产物（`.pyc`）逐字节还原出来的 Python 源码，
**主程序 78 个模块 + 安装器 16 个模块全部通过字节码校验**，并附上打包所需的二进制资源。

## 目录

```
班级积分系统.py          入口（sys.path 注入、日志、--selftest）
app/                     主程序：core（纯逻辑）/ io / services / ui / selftest / main
installer/               独立安装器：wizard、pages、install_core、uninstaller、winsys、win_shortcut、build_payload
packaging/               PyInstaller 三个 spec + 两个构建脚本（.ps1，必须 UTF-8 BOM）
assets/ffmpeg.exe        内置视频解码器（抽奖揭晓视频用它逐帧解码 + 抽音频）
assets/app.ico           程序图标（app.exe / setup.exe 的 icon=）
cloudflared.exe          公网隧道客户端（tunnel.py 调它开 trycloudflare 地址）
android/viewer.html      手机端页面（sync_server 直接返回，扫码即看）
CHANGELOG.md / README.md 程序自带的文档（更新日志弹窗读 CHANGELOG.md）
docs/SOURCE-GUIDE.md     源码导读（推荐先读这份，另附同名 .html）
docs/BUILD.md            打包说明与 manifest.json 字段
_tools/ensure_bom.py     给 .ps1 补 BOM
tools/                   还原校验工具：verify.py / verify_installer.py（不属于交付物）
```

> `assets/ffmpeg.exe`（约 62 MB）与 `cloudflared.exe`（约 53 MB）是原安装包
> `payload/_internal/` 里的同一份二进制，已原样随包提供，可直接构建；不需要再从
> 取证目录里翻。`README.md` 与 `CHANGELOG.md` 是程序自己的文档，本文件才是这份包的说明。

## 校验

需要 CPython 3.14（与原包一致的字节码版本）：

```bash
/opt/pythons/cpython-3.14.8-linux-aarch64-gnu/bin/python3.14 tools/verify.py --all
/opt/pythons/cpython-3.14.8-linux-aarch64-gnu/bin/python3.14 tools/verify_installer.py --all
```

当前状态：

| 范围 | 结果 | 说明 |
| --- | --- | --- |
| 主程序 `app.*` + 入口 | **78 / 78** | 26 个字节一致、52 个结构一致 |
| 安装器 `installer.*` | **16 / 16** | 10 个字节一致、6 个结构一致 |

判定分两级：**结构一致**（code object 集合、指令序列、names/varnames/参数个数全同）与
**字节一致**（`co_code` 完全相同）。两者都通过才算还原成功。

## 运行

```bash
python 班级积分系统.py              # 正常启动（需要 PyQt5）
python 班级积分系统.py --selftest   # 只跑 12 项自检，不打开界面
```

数据目录：`%APPDATA%\班级积分系统`（Windows）/ `~/.local/share/班级积分系统`（Linux）。

## 重新打包（Windows）

```powershell
powershell -ExecutionPolicy Bypass -File packaging\build.ps1            # 打 app.exe + Uninstall.exe
powershell -ExecutionPolicy Bypass -File packaging\build_installer.ps1  # 压 payload.zip 并打 setup.exe
```

产物：`dist\班级积分系统\`（onedir）、`dist\Uninstall.exe`、`dist\setup.exe`。
`build_installer.ps1` 会调用 `installer\build_payload.py` 生成 `payload.zip` 与 `manifest.json`。

详细说明见 `docs/SOURCE-GUIDE.md` 与 `docs/BUILD.md`。
