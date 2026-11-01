"""安装程序共享常量。

版本号**不在这里**：唯一来源是 app/version.py，构建期由
packaging/build_installer.ps1 读出来写进 manifest.json，
运行时从安装清单读取，避免「exe 文件名一个版本、程序里一个版本」。
"""
from __future__ import annotations

APP_NAME = '班级积分系统'
APP_ID = 'ClassPoints'
APP_EXE_NAME = APP_NAME + '.exe'
UNINSTALLER_NAME = 'Uninstall.exe'
PUBLISHER = 'ClassPoints'

#: 卸载信息注册表项（HKCU，免管理员安装就写这里）
REG_UNINSTALL_SUBKEY = (
    'Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\班级积分系统'
)

#: 数据目录名：与 app/core/paths.py 的默认值保持一致
DATA_DIR_NAME = APP_NAME

#: 默认装在「用户\\AppData\\Local\\Programs」下，不需要管理员权限
DEFAULT_INSTALL_PARENT = 'Programs'

PAYLOAD_ZIP = 'payload.zip'
MANIFEST_JSON = 'manifest.json'
APP_ICON = 'app.ico'

START_MENU_DIR_NAME = APP_NAME
DESKTOP_LNK_NAME = APP_NAME + '.lnk'
