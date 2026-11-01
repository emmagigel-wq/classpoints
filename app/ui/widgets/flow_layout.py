"""流式布局：原版 `.chips` 的 `flex-wrap: wrap`，一行放不下就换行。

Qt 没有内置的 flow layout，`QHBoxLayout` 永远单行、会被子项最小宽撑爆父容器
——这正是加分弹窗被撑到 588 宽、出现水平滚动条的根因。这里按可用宽度逐行摆放。

用法：
    flow = FlowLayout(parent, h_spacing=8, v_spacing=8)
    for w in widgets:
        flow.addWidget(w)
"""
from __future__ import annotations

from PyQt5.QtCore import QPoint, QRect, QSize, Qt
from PyQt5.QtWidgets import QLayout, QSizePolicy, QWidget


class FlowLayout(QLayout):
    """按行摆放、超宽自动换行；`hasHeightForWidth()` 为 True。"""

    def __init__(
        self,
        parent: QWidget | None = None,
        h_spacing: int = 8,
        v_spacing: int = 8,
        margins: tuple[int, int, int, int] = (0, 0, 0, 0),
    ) -> None:
        super().__init__(parent)
        self._items = []
        self._h = int(h_spacing)
        self._v = int(v_spacing)
        self.setContentsMargins(*margins)

    def addItem(self, item) -> None:
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int):
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index: int):
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def expandingDirections(self):
        return Qt.Orientations(Qt.Orientation(0))

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._do_layout(QRect(0, 0, width, 0), apply=False)

    def setGeometry(self, rect: QRect) -> None:
        super().setGeometry(rect)
        self._do_layout(rect, apply=True)

    def sizeHint(self) -> QSize:
        return self.minimumSize()

    def minimumSize(self) -> QSize:
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        m = self.contentsMargins()
        return size + QSize(m.left() + m.right(), m.top() + m.bottom())

    def _do_layout(self, rect: QRect, apply: bool) -> int:
        m = self.contentsMargins()
        area = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        x, y, line_h = area.x(), area.y(), 0
        for item in self._items:
            hint = item.sizeHint()
            w, h = hint.width(), hint.height()
            if x + w > area.right() + 1 and line_h > 0:
                x = area.x()
                y += line_h + self._v
                line_h = 0
            if apply:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x += w + self._h
            line_h = max(line_h, h)
        return y + line_h - rect.y()


def flow_height(
    items: list[QWidget], width: int, h_spacing: int = 8, v_spacing: int = 8
) -> int:
    """预先算出流式布局在给定宽度下的总高（用于钉死容器高度）。"""
    x, y, line_h = 0, 0, 0
    for w in items:
        hint = w.sizeHint()
        if x + hint.width() > width and line_h > 0:
            x = 0
            y += line_h + v_spacing
            line_h = 0
        x += hint.width() + h_spacing
        line_h = max(line_h, hint.height())
    return y + line_h


__all__ = ['FlowLayout', 'flow_height']
