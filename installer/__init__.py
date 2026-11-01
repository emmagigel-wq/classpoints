"""班级积分系统 Windows 安装程序。

包结构：
  * bundle            资源定位（payload.zip / manifest / icon）
  * winsys            Win32 小工具（系统文件夹、磁盘空间、结束进程）
  * win_shortcut      COM 创建 .lnk 快捷方式（不依赖 pywin32）
  * install_core      安装/卸载的全部落盘逻辑（无 Qt，Uninstall.exe 复用）
  * ui/               PyQt5 安装向导（欢迎 → 选目录 → 进度 → 完成）
  * main.py           入口：GUI / --silent / --version
  * uninstaller.py    Uninstall.exe 入口
  * build_payload.py  构建期：把 onedir 产物压成 payload.zip + manifest.json

注意：这里刻意不 import app 包——安装器独立于被安装的程序，
app/version.py 的版本号由构建脚本读出来写进 manifest.json。
"""
