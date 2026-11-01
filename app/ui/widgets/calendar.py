"""白底日历弹窗。

为什么单独写一个类：``QDateTimeEdit`` 的 ``calendarPopup`` 弹出来的是一个
**独立顶层窗口**，它拿的是系统配色。应用层的 ``QCalendarWidget`` 规则能盖住
大部分子控件，但弹窗自己的背景、月份下拉菜单、以及 Windows 深色模式下的
标题条并不总被盖住——用户看到的就是「选时间弹出来的日历是黑的/灰的」。

这里做三件事，保证不管系统是深色还是高对比，弹出来都是白底：
1. 给日历自身设**本地**样式表（本地表优先级高于应用表，且不会漏掉顶层窗口）；
2. 强制一套浅色调色板（QSS 管不到一些原生绘制的部分）；
3. ``paintEvent`` 先铺一层白，再交给基类画内容——双保险。
"""
from __future__ import annotations

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor, QPainter, QPalette
from PyQt5.QtWidgets import QCalendarWidget, QDateTimeEdit, QMenu, QWidget

from app.ui import theme

# 日历弹窗自己的样式表：本地设置，优先于应用级 QSS。
POPUP_QSS = ''.join([
    'QCalendarWidget#cpCalendar { background: ',
    f'{theme.CARD}',
    '; border: 1px solid ',
    f'{theme.LINE}',
    '; border-radius: 10px; }QCalendarWidget#cpCalendar QWidget { background: ',
    f'{theme.CARD}',
    '; color: ',
    f'{theme.INK}',
    '; }QWidget#qt_calendar_navigationbar { background: ',
    f'{theme.CARD}',
    '; border-bottom: 1px solid ',
    f'{theme.LINE}',
    '; }QToolButton { background: transparent; color: ',
    f'{theme.INK}',
    '; border: none; border-radius: 6px; padding: 2px 6px; }QToolButton:hover { background: ',
    f'{theme.PILL_GRAY_BG}',
    '; }QToolButton:disabled { color: ',
    f'{theme.INK3}',
    '; }QSpinBox, QSpinBox:focus { background: ',
    f'{theme.CARD}',
    '; color: ',
    f'{theme.INK}',
    '; border: none; }QAbstractItemView { background: ',
    f'{theme.CARD}',
    '; color: ',
    f'{theme.INK}',
    '; outline: none; selection-background-color: ',
    f'{theme.PRIMARY}',
    '; selection-color: #ffffff; }QAbstractItemView:disabled { color: ',
    f'{theme.INK3}',
    '; }QMenu { background: ',
    f'{theme.CARD}',
    '; color: ',
    f'{theme.INK}',
    '; border: 1px solid ',
    f'{theme.LINE}',
    '; }QMenu::item:selected { background: ',
    f'{theme.PILL_GRAY_BG}',
    '; color: ',
    f'{theme.INK}',
    '; }',
])


def light_palette() -> QPalette:
    """一套固定的浅色调色板（不跟随系统深色模式）。"""
    pal = QPalette()
    white = QColor(theme.CARD)
    ink = QColor(theme.INK)
    for role in (QPalette.Window, QPalette.Base, QPalette.Button,
                 QPalette.ToolTipBase, QPalette.AlternateBase):
        pal.setColor(role, white)
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText,
                 QPalette.ToolTipText):
        pal.setColor(role, ink)
    pal.setColor(QPalette.Highlight, QColor(theme.PRIMARY))
    pal.setColor(QPalette.HighlightedText, QColor('#ffffff'))
    pal.setColor(QPalette.Disabled, QPalette.Text, QColor(theme.INK3))
    pal.setColor(QPalette.Disabled, QPalette.WindowText, QColor(theme.INK3))
    return pal


class WhiteCalendar(QCalendarWidget):
    """始终白底的日历，用于加分/扣分里的时间选择。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName('cpCalendar')
        self.setGridVisible(False)
        self.setVerticalHeaderFormat(QCalendarWidget.NoVerticalHeader)
        self.setFirstDayOfWeek(Qt.Monday)
        self.setPalette(light_palette())
        self.setStyleSheet(POPUP_QSS)
        # 月份/年份下拉是 QMenu，不吃父级样式，单独贴一遍。
        self._style_menus()

    def _style_menus(self) -> None:
        for menu in self.findChildren(QMenu):
            menu.setStyleSheet(POPUP_QSS)
            menu.setPalette(light_palette())

    def showEvent(self, event) -> None:
        self._style_menus()
        super().showEvent(event)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(theme.CARD))
        painter.end()
        super().paintEvent(event)


def attach_white_calendar(edit: QDateTimeEdit) -> WhiteCalendar:
    """把 ``edit`` 的日历弹窗换成白底版，并返回该日历。"""
    cal = WhiteCalendar(edit)
    edit.setCalendarPopup(True)
    edit.setCalendarWidget(cal)
    return cal
