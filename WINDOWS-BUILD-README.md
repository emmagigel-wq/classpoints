# Windows 一键构建说明

目标产物：`dist\\班级积分系统-安装程序-v2.13.16.exe`

## 环境要求
- Windows 10/11，64 位
- 64 位 CPython 3.14
- 可联网安装 Python 包
- 建议至少预留 2 GB 可用磁盘空间；首次构建会下载/解压 Qt 与打包依赖

## 构建步骤
1. 将整个源码目录解压到本地磁盘，避免放在网盘同步目录或路径过深的位置。
2. 安装 Python 3.14 x64。默认路径 `C:\\Python314\\python.exe`，也支持 Python Launcher 的 `py -3.14`。
3. 双击 `Windows一键构建.bat`。
4. 等待构建结束，在 `dist` 目录取得 `班级积分系统-安装程序-v2.13.16.exe`。

脚本会安装 PyInstaller、PyQt5、openpyxl、python-docx、lxml、segno，并调用项目原有的
`packaging\\build_installer.ps1` 完成主程序、卸载器、payload 和安装器的构建。

## 注意
- 本工程的 spec 和构建说明指定 Python 3.14 x64。不要用 Linux/macOS 的构建结果替代 Windows exe。
- 首次构建需要联网下载 Python 包。
- 如果 Windows 用户目录含中文，原有 `build.ps1` 会尝试创建 `C:\\cpbuild` 目录联接来规避 Qt 路径问题。
- 构建完成后，建议在一台未安装 Python 的 Windows 电脑上测试安装、启动、加分保存、卸载与数据保留。
- 杀毒软件可能对自打包的 PyInstaller 程序进行启发式扫描；发布前建议进行多引擎扫描并保留构建日志。
