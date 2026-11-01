"""「使用说明」弹窗：把主界面上那几个按钮怎么用，讲清楚。

内容按程序真实功能写（座位表加减分 / 早读午读全屏 / 抽奖 / 记录 /
多端同步与手机扫码 / 数据备份），全部是本地静态文本：

* 不联网 —— 没有「在线文档」「检查更新」这类会卡住的按钮；
* 不跳浏览器 —— 不调用 QDesktopServices，也不放 http 链接；
* 只用 Qt 富文本 —— QTextBrowser 自带滚动条，长文本不会把弹窗撑破。
"""

from __future__ import annotations

from PyQt5.QtWidgets import QTextBrowser

from app.ui import theme
from app.ui.modals import ModalShell

MANUAL_WIDTH = 680
MANUAL_MIN_H = 420
MANUAL_MAX_H = 700

MANUAL_SECTIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "座位表加减分",
        (
            "单击座位选中学生，再点底栏「加分」/「扣分」，填分值确认即可。",
            "双击座位直接打开加分窗口；右键座位直接扣分；长按座位再拖到别处，可换位置。",
            "没选学生时点「加分」「扣分」，默认从第一个学生开始。",
            "顶栏「撤销上一步」可撤掉最近一次计分；「记录」里每条也能单独撤销。",
            "点座位表右上角「编辑座位」可添加 / 改名 / 删除座位，改完点「完成编辑」。",
        ),
    ),
    (
        "早读 / 午读（全屏）",
        (
            "底栏「早读」「午读」进入全屏舞台，适合投到教室大屏上点。",
            "全屏里单击座位 ＋、双击座位 －、长按座位撤销。",
            "按 Esc 或点右下角「退出全屏」回到座位表。",
        ),
    ),
    (
        "抽奖与随机",
        (
            "「抽奖」按抽奖规则抽取学生并加分；门槛分 / 排名名额在「设置 → 抽奖奖品」里配。",
            "「随机」等概率抽一个学生，只抽不加分，适合点名提问。",
        ),
    ),
    (
        "记录",
        (
            "「记录」按时间列出全部加减分，也能切到单个学生查看。",
            "每条记录右侧有「撤销」，撤错了可以再操作一次补回来。",
        ),
    ),
    (
        "多端同步（手机扫码）",
        (
            "「设置 → 多端同步」点「开启同步」，本机就是同步服务器。",
            "手机连同一个 WiFi，扫弹窗里的二维码（或「复制手机链接」发到手机）即可打开。",
            "手机端能看也能改分，写操作需要口令：在设置里「复制口令」发给手机端输入。",
            "不在同一 WiFi 时，点「生成公网地址」，用生成的公网链接访问（第一次需下载穿透组件）。",
            "用完点「关闭同步」即可断开，下次再开链接不变。",
        ),
    ),
    (
        "数据备份与目录",
        (
            "「设置 → 数据备份」可导出 / 导入备份（.json）；换电脑时先在旧机导出，再在新机导入。",
            "「清空全部数据」不能撤销，动手前先导出备份。",
            "「设置 → 班级与座位」下方能看到当前数据目录，也能改到别的位置；备份、图标都放在这里。",
        ),
    ),
    (
        "外观",
        (
            "「主题」页可换主界面背景图、启动动画和程序图标。",
            "背景图与程序图标换完立即生效；启动动画下次启动时生效。",
        ),
    ),
)

MANUAL_TIP = "以上都可以随时在「设置」里调整，改坏了不会丢数据。"


def _html() -> str:
    """拼成 QTextBrowser 认的 HTML（只用手写标签，不用 Markdown）。"""
    body = []
    for title, lines in MANUAL_SECTIONS:
        body.append(f'<h3 style="color:{theme.INK};">{title}</h3>')
        body.append('<ul style="margin-left:-18px;">')
        for line in lines:
            body.append(f'<li style="margin-bottom:6px;">{line}</li>')
        body.append('</ul>')
    body.append(
        f'<p style="color:{theme.INK3}; margin-top:10px;">{MANUAL_TIP}</p>'
    )
    return (
        f'<div style="color:{theme.INK2}; font-size:13px;">'
        + "".join(body)
        + "</div>"
    )


def manual_html() -> str:
    """公开给测试 / 探针：拿到「使用说明」正文的 HTML。"""
    return _html()


class ManualDialog(ModalShell):
    """只读的使用说明窗口。"""

    def __init__(self, parent=None) -> None:
        super().__init__("使用说明", "照着点就能用，不需要联网", parent,
                         width=MANUAL_WIDTH)
        self.view = QTextBrowser(self.content_widget())
        self.view.setObjectName("manualView")
        self.view.setOpenExternalLinks(False)
        self.view.setOpenLinks(False)
        self.view.setStyleSheet(
            f"QTextBrowser#manualView {{ background: {theme.CARD}"
            f"; border: 1px solid {theme.LINE}"
            f"; border-radius: 10px; padding: 10px 14px; font-size: 13px; color: {theme.INK2}"
            f"; }}"
        )
        self.view.setHtml(manual_html())
        self.view.setMinimumHeight(MANUAL_MIN_H - 150)
        self.body_layout().addWidget(self.view)
        self.add_foot_button("知道了").clicked.connect(self.reject)
        parent_h = parent.height() if parent is not None else 0
        limit = parent_h - 80 if parent_h > 480 else MANUAL_MAX_H
        self.setFixedHeight(min(MANUAL_MAX_H, max(MANUAL_MIN_H, limit)))
