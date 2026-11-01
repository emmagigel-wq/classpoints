"""「关于软件」页用到的元信息。

设置弹窗左侧导航的最后一项（第 9 项）是「关于软件」，页内容由
`SettingsDialog._build_about_section()` 组装，元信息统一放在本模块：

* 程序名 / 版本号：**只从 `app.version` 读**，这里绝不硬写版本字符串；
* 作者：「汝州二高一名学生」。

「使用说明」不在这里：它是独立弹窗，见 `app/ui/dialogs/manual_dialog.py`。
"""

from __future__ import annotations

from app import version as version_mod

APP_NAME = version_mod.APP_NAME
AUTHOR = "doufu_make"
TAGLINE = "面向班级的积分工具"


def version_text() -> str:
    """当前版本号（含 v 前缀）。唯一来源是 app.version.__version__。"""
    return f"v{version_mod.__version__}"
