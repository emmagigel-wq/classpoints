"""学生下拉选择。itemData 存 student.id，不靠显示名反查（重名会串）。"""
from __future__ import annotations

from typing import Iterable

from PyQt5.QtWidgets import QComboBox, QWidget


class StudentPicker(QComboBox):
    """列出学生；提供 current_student_id()。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumWidth(140)
        self.set_students([])

    def set_students(self, students: Iterable, *, blank_label: str = '全部学生') -> None:
        current = self.current_student_id()
        self.blockSignals(True)
        self.clear()
        if blank_label:
            self.addItem(blank_label, '')
        for s in students:
            label = f'{s.name}（{s.points} 分）'
            self.addItem(label, s.id)
        idx = self.findData(current) if current else (0 if blank_label else -1)
        self.setCurrentIndex(idx if idx >= 0 else 0)
        self.blockSignals(False)

    def current_student_id(self) -> str:
        data = self.currentData()
        return str(data) if data else ''
