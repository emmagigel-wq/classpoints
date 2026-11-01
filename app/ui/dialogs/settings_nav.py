"""设置弹窗左侧的分区导航（本次改版新增）。

原版设置是一根长滚动列：8 个 `.set-sec` 从上到下堆着，想改「多端同步」要滚很久。
新版把它拆成「左侧导航 + 右侧单页」：导航竖排 N 项、当前项淡蓝底 + 品牌色字，
右侧只显示选中分区（每页各自滚动，切页时回到顶部）。

尺寸沿用 ground-settings.json 的弹窗规格（modal 820x852）：
    .settings-nav  宽 190，白底卡片感（BTN_GHOST_BG）+ 1px LINE 边 + r12
    .nav-item      高 38，13px 700，r9，左对齐
    .nav-item.on   淡蓝底 SEL_ACTIVE_BG + 品牌色字 BRAND_DARK

刻意不写死颜色：全部取 app/ui/theme.py 的令牌，和项目里其它控件保持一致。
"""

from __future__ import annotations

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import QFrame, QPushButton, QVBoxLayout, QWidget

from app.ui import theme

NAV_W = 190
ITEM_H = 38
ITEM_GAP = 4
NAV_PAD = 8

NAV_QSS = (
    f"\nQFrame#setNav {{\n    background: {theme.BTN_GHOST_BG}"
    f";\n    border: 1px solid {theme.LINE}"
    f";\n    border-radius: 12px;\n}}\n"
)

ITEM_QSS = (
    f"\nQPushButton#setNavItem {{\n    background: transparent; border: none; border-radius: 9px;\n"
    f"    padding: 0px 12px; text-align: left; font-size: 13px;\n"
    f"    font-weight: 700; color: {theme.INK2}"
    f";\n}}\nQPushButton#setNavItem:hover {{\n    background: {theme.CARD}"
    f"; color: {theme.INK}"
    f";\n}}\nQPushButton#setNavItem:checked {{\n    background: {theme.SEL_ACTIVE_BG}"
    f"; color: {theme.BRAND_DARK}"
    f";\n}}\n"
)


class SettingsNav(QFrame):
    """竖排分区导航：点第 i 项就发 `selected(i)`；高亮由 `set_current` 管。"""

    selected = pyqtSignal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("setNav")
        self.setStyleSheet(NAV_QSS)
        self.setFixedWidth(NAV_W)
        self._items = []
        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(NAV_PAD, NAV_PAD, NAV_PAD, NAV_PAD)
        self._lay.setSpacing(ITEM_GAP)
        self._lay.addStretch(1)

    def add_item(self, text: str) -> QPushButton:
        """追加一项到导航末尾；返回按钮本身（探针/测试可以直接 click）。"""
        idx = len(self._items)
        btn = QPushButton(text, self)
        btn.setObjectName("setNavItem")
        btn.setCheckable(True)
        btn.setAutoExclusive(True)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setFixedHeight(ITEM_H)
        btn.setStyleSheet(ITEM_QSS)
        btn.clicked.connect(lambda _=False, i=idx: self.selected.emit(i))
        self._lay.insertWidget(idx, btn)
        self._items.append(btn)
        return btn

    def count(self) -> int:
        return len(self._items)

    def item(self, index: int) -> QPushButton:
        return self._items[index]

    def current_index(self) -> int:
        for i, btn in enumerate(self._items):
            if btn.isChecked():
                return i
        return -1

    def set_current(self, index: int) -> None:
        """只改高亮，不发信号（切换由外部 `_select_section` 统一驱动）。"""
        for i, btn in enumerate(self._items):
            btn.setChecked(i == index)
