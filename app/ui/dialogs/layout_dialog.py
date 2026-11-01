"""座位布局：行数与列分组（如 2-4-2）。

真正的换算交给 app.core.layout.apply_layout —— 对话框只负责把用户输入
翻译成 (rows, groups) 两个参数，并如实报告「有没有同学因为缩容被挪了座」。
"""

from __future__ import annotations

from PyQt5.QtCore import pyqtSignal
from PyQt5.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from app.core import layout as layout_mod
from app.ui import theme


class LayoutDialog(QDialog):
    """调整座位表行列。"""

    changed = pyqtSignal()

    def __init__(self, store, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._store = store
        self.setWindowTitle("座位布局")
        self.setModal(True)
        self.setMinimumWidth(400)
        self._build()
        self._load()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 12)
        root.setSpacing(10)

        form = QFormLayout()
        self._rows = QSpinBox(self)
        self._rows.setRange(1, 30)
        form.addRow("行数", self._rows)

        self._groups = QLineEdit(self)
        self._groups.setPlaceholderText("例：2-4-2 或 2,4,2")
        form.addRow("列分组", self._groups)
        root.addLayout(form)

        self._preview = QLabel("", self)
        self._preview.setWordWrap(True)
        self._preview.setStyleSheet(
            f"color: {theme.TEXT_WEAK}; background: transparent; font-size: 12px;"
        )
        root.addWidget(self._preview)

        self._rows.valueChanged.connect(self._refresh_preview)
        self._groups.textChanged.connect(self._refresh_preview)

        box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, self)
        box.button(QDialogButtonBox.Ok).setText("应用")
        box.button(QDialogButtonBox.Cancel).setText("取消")
        box.accepted.connect(self._apply)
        box.rejected.connect(self.reject)
        root.addWidget(box)

    def _load(self) -> None:
        lay = self._store.state.layout
        self._rows.setValue(lay.rows)
        self._groups.setText("-".join(str(g) for g in lay.col_groups))
        self._refresh_preview()

    def _parsed(self) -> tuple[int, list[int]]:
        rows = self._rows.value()
        groups = layout_mod.parse_col_groups(self._groups.text())
        return rows, groups

    def _refresh_preview(self) -> None:
        rows, groups = self._parsed()
        cols = sum(groups) if groups else 0
        seats = rows * cols
        people = len(self._store.state.students)
        text = f"{rows} 行 × {cols} 列 = {seats} 个座位，当前 {people} 人"
        if seats < people:
            text += f"；座位不够，会有 {people - seats} 人放不下（应用时会被拒绝）"
        self._preview.setText(text)

    def _apply(self) -> None:
        rows, groups = self._parsed()
        state = self._store.state
        if rows * sum(groups or [0]) < len(state.students):
            self._preview.setText("座位数少于学生数，请先加行或加列")
            return
        moved = layout_mod.apply_layout(state, rows, groups)
        self.changed.emit()
        if moved:
            self._preview.setText(f"已应用；{len(moved)} 位同学的座位被调整")
        self.accept()
