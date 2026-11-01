"""奖品池维护：增删改 + 权重。

权重是「相对值」，抽奖时按总和归一；全为 0 时抽奖会明确失败而不是硬塞一个，
所以这里允许填 0，但会在提示里说明后果。
"""

from __future__ import annotations

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.core.models import Prize
from app.ui import theme

HEADERS = ("奖品名", "权重")


class PrizeDialog(QDialog):
    """奖品增删改。"""

    changed = pyqtSignal()

    def __init__(self, store, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._store = store
        self.setWindowTitle("奖品")
        self.setModal(True)
        self.setMinimumWidth(460)
        self._build()
        self._load()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 12)
        root.setSpacing(8)

        self._table = QTableWidget(0, len(HEADERS), self)
        self._table.setHorizontalHeaderLabels(list(HEADERS))
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SingleSelection)
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(1, QHeaderView.Fixed)
        self._table.setColumnWidth(1, 96)
        root.addWidget(self._table, 1)

        row = QHBoxLayout()
        self._name = QLineEdit(self)
        self._name.setPlaceholderText("新奖品名")
        row.addWidget(self._name, 1)
        self._weight = QDoubleSpinBox(self)
        self._weight.setRange(0.0, 1000.0)
        self._weight.setSingleStep(0.5)
        self._weight.setValue(1.0)
        row.addWidget(self._weight)
        add = QPushButton("添加", self)
        add.clicked.connect(self._add)
        row.addWidget(add)
        remove = QPushButton("删除选中", self)
        remove.setObjectName("danger")
        remove.clicked.connect(self._remove)
        row.addWidget(remove)
        root.addLayout(row)

        self._hint = QLabel("", self)
        self._hint.setWordWrap(True)
        self._hint.setStyleSheet(
            f"color: {theme.TEXT_WEAK}; background: transparent; font-size: 12px;"
        )
        root.addWidget(self._hint)

        box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, self)
        box.button(QDialogButtonBox.Ok).setText("保存")
        box.button(QDialogButtonBox.Cancel).setText("取消")
        box.accepted.connect(self._save)
        box.rejected.connect(self.reject)
        root.addWidget(box)

    def _load(self) -> None:
        self._table.setRowCount(len(self._store.state.prizes))
        for i, prize in enumerate(self._store.state.prizes):
            self._put(i, prize.name, prize.weight)
        self._refresh_hint()

    def _put(self, row: int, name: str, weight: float) -> None:
        name_item = QTableWidgetItem(name)
        weight_item = QTableWidgetItem(f"{weight:g}")
        weight_item.setTextAlignment(Qt.AlignCenter)
        self._table.setItem(row, 0, name_item)
        self._table.setItem(row, 1, weight_item)

    def _refresh_hint(self) -> None:
        total = sum(p.weight for p in self._store.state.prizes)
        if not self._store.state.prizes:
            self._hint.setText("还没有奖品；抽奖会直接失败并提示「还没配置奖品」。")
            return
        if total <= 0:
            self._hint.setText("所有奖品权重都是 0，抽奖会失败。至少给一个奖品正权重。")
            return
        self._hint.setText(
            f"共 {len(self._store.state.prizes)} 种，权重合计 {total:g}"
        )

    def _add(self) -> None:
        name = self._name.text().strip()
        if not name:
            self._hint.setText("先填奖品名")
            return
        row = self._table.rowCount()
        self._table.insertRow(row)
        self._put(row, name, self._weight.value())
        self._name.clear()

    def _remove(self) -> None:
        row = self._table.currentRow()
        if row < 0:
            return
        self._table.removeRow(row)

    def _save(self) -> None:
        prizes = []
        for row in range(self._table.rowCount()):
            name_item = self._table.item(row, 0)
            weight_item = self._table.item(row, 1)
            name = name_item.text().strip() if name_item is not None else ""
            if not name:
                continue
            try:
                weight = float(weight_item.text()) if weight_item is not None else 1.0
            except (TypeError, ValueError):
                weight = 1.0
            prizes.append(Prize(name=name, weight=weight))
        self._store.state.prizes = prizes
        self.changed.emit()
        self.accept()
