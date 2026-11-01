"""座位格（主界面）：对齐原版 .seat —— 112x66、r12、底色 #fbfcfe。

原版结构：姓名 15px/600 + 分数 15px/700（正绿负红），空格只画一个浅灰「+」。
交互（原版）：单击选中（230ms 双击窗口内再点=双击加分）、右键扣分、
编辑模式下点击开编辑框、长按 320ms 起拖。

**这是历史缺陷 1 的正面修补点**：只重写 mouseReleaseEvent 一个事件出口，
一次物理点击只产生一个意图；不缓存任何跨调用的标志位。
"""

from __future__ import annotations

import time

from PyQt5.QtCore import QSize, Qt, QTimer, pyqtSignal
from PyQt5.QtWidgets import QFrame, QLabel, QVBoxLayout

from app.core.models import Student
from app.services.pulse import CellVisual
from app.ui import theme

LONG_PRESS_MS = 620
CLICK_WINDOW_MS = 230


class SeatCell(QFrame):
    """一个学生座位；空格子也用它（student=None）。"""

    tapped = pyqtSignal(str)
    double_tapped = pyqtSignal(str)
    right_clicked = pyqtSignal(str)
    long_pressed = pyqtSignal(str)
    empty_clicked = pyqtSignal(int, int)
    dropped_on = pyqtSignal(str, int, int)
    drag_started = pyqtSignal(str)
    drag_moved = pyqtSignal(str, object)
    drag_finished = pyqtSignal(str, object)

    def __init__(
        self,
        student: Student | None,
        row: int = 0,
        col: int = 0,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName('seat')
        self.student_id = student.id if student else ''
        self.row, self.col = row, col
        self._press_ms = 0.0
        self._font_px = theme.FONT_BASE
        self._points_sign = 0
        self._state = 'normal'
        self._editing = False
        self._selected = False
        self._override = None
        self._click_timer = None
        self._drag_from = None
        self._drag_armed = False
        self._drag_active = False
        self._drop_target = False

        self.setAcceptDrops(False)

        self.setMinimumSize(QSize(theme.SEAT_MIN_W, theme.SEAT_MIN_H))
        self.setCursor(Qt.PointingHandCursor)

        self._name = QLabel(self)
        self._name.setObjectName('seatName')
        self._name.setAlignment(Qt.AlignCenter)
        self._name.setWordWrap(True)

        self._name.setFixedHeight(17)

        self._points = QLabel(self)
        self._points.setObjectName('seatPoints')
        self._points.setAlignment(Qt.AlignCenter)

        self._points.setFixedHeight(20)

        self._add = QLabel('+', self)
        self._add.setObjectName('seatAdd')
        self._add.setAlignment(Qt.AlignCenter)

        self._add.setFixedHeight(29)

        lay = QVBoxLayout(self)

        lay.setContentsMargins(4, 3, 4, 3)
        lay.setSpacing(1)
        lay.addWidget(self._name)
        lay.addWidget(self._points)
        lay.addWidget(self._add)

        self.bind(student)

    def bind(self, student: Student | None) -> None:
        """贴数据：只改文字与显隐，不动状态机。"""
        if student is None:
            self.student_id = ''

            self._name.setText('')
            self._name.setVisible(False)
            self._points.setText('')
            self._points.setVisible(False)
            self._add.setVisible(False)
            self._points_sign = 0
        else:
            self.student_id = student.id
            self._name.setText(student.name or '（未命名）')
            self._name.setVisible(True)
            self._points.setText(str(student.points))
            self._points.setVisible(True)
            self._add.setVisible(False)
            self._points_sign = (student.points > 0) - (student.points < 0)

        self._repaint()

    def set_editing(self, on: bool) -> None:
        self._editing = on
        self._repaint()

    def set_selected(self, on: bool) -> None:
        self._selected = on
        self._repaint()

    def set_drop_target(self, on: bool) -> None:
        """拖拽经过时高亮：告诉用户「松手就落在这里」。"""
        if getattr(self, '_drop_target', False) == on:
            return
        self._drop_target = on
        self._repaint()

    def apply_visual(self, visual: CellVisual) -> None:
        """动画帧出口（兼容旧接口）：pulse 的 CellVisual 映射到覆盖底色。

        原版语义：闪一下（420ms）后回常态；本次会话的净加减分用
        set_session_tint 常驻表示。覆盖只在 visual 非 None 时存在。
        """
        if visual is None:
            self._override = None
            self._repaint()
            return
        self._override = (visual.background, visual.border)
        self._repaint()

    def set_session_tint(self, kind: str) -> None:
        """got-plus / got-minus 常驻底色（原版 paintSession，整场保留）。"""
        if kind == 'got_plus':
            self._override = (theme.GOT_PLUS_BG, theme.OK)
        elif kind == 'got_minus':
            self._override = (theme.GOT_MINUS_BG, theme.BAD)
        else:
            self._override = None
        self._repaint()

    def _repaint(self) -> None:
        if self._editing and not self.student_id:
            st = 'empty'
        elif self._editing:
            st = 'editing'
        elif self._selected:
            st = 'selected'
        elif not self.student_id:
            st = 'empty'
        else:
            st = 'normal'
        self._state = st
        qss = theme.seat_qss(
            st, points_sign=self._points_sign, font_px=self._font_px
        )
        if getattr(self, '_drop_target', False):
            qss = qss.replace(
                'border: 1px solid %s;' % theme.LINE,
                'border: 2px solid %s;' % theme.BRAND,
                1,
            )
            qss = qss.replace(
                'background: %s;' % theme.SEAT_BG,
                'background: %s;' % theme.CARD,
                1,
            )
        if self._override is not None:
            bg, border = self._override
            qss = qss.replace(
                'background: %s;' % theme.SEAT_BG,
                'background: %s;' % bg,
                1,
            )
            qss = qss.replace(
                'border: 1px solid %s;' % theme.LINE,
                'border: 1px solid %s;' % border,
                1,
            )
        self.setStyleSheet(qss)

    def set_font_scale(self, px: int) -> None:
        self._font_px = max(11, min(46, px))
        self._repaint()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self._press_ms = time.monotonic() * 1000.0
            self._drag_from = event.pos()
            self._drag_armed = bool(self._editing and self.student_id)
            self._drag_active = False
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if (
            self._drag_armed
            and self.student_id
            and self._drag_from is not None
            and event.buttons() & Qt.LeftButton
        ):
            moved = (event.pos() - self._drag_from).manhattanLength()
            if moved > 8:
                self._drag_armed = False
                self._drag_active = True
                self.drag_started.emit(self.student_id)
        if self._drag_active:
            self.drag_moved.emit(self.student_id, event.globalPos())
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        """**唯一**的点击出口：一次物理点击只发一个意图。"""
        self._drag_from = None
        self._drag_armed = False
        if getattr(self, '_drag_active', False):
            self._drag_active = False
            self.drag_finished.emit(self.student_id, event.globalPos())
            super().mouseReleaseEvent(event)
            return
        if event.button() == Qt.RightButton and self.student_id:
            self.right_clicked.emit(self.student_id)
            super().mouseReleaseEvent(event)
            return
        if event.button() != Qt.LeftButton:
            super().mouseReleaseEvent(event)
            return
        held = (time.monotonic() * 1000.0 - self._press_ms) if self._press_ms else 0.0
        self._press_ms = 0.0
        if not self.rect().contains(event.pos()):
            super().mouseReleaseEvent(event)
            return
        if held >= LONG_PRESS_MS and self.student_id:
            self.long_pressed.emit(self.student_id)
        elif not self.student_id:
            self.empty_clicked.emit(self.row, self.col)
        elif self._editing:
            self.tapped.emit(self.student_id)
        elif self._click_timer is not None and self._click_timer.isActive():
            self._click_timer.stop()
            self._click_timer = None
            self.double_tapped.emit(self.student_id)
            return
        else:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(lambda sid=self.student_id: self._fire_single(sid))
            self._click_timer = timer
            timer.start(CLICK_WINDOW_MS)
        super().mouseReleaseEvent(event)

    def _fire_single(self, student_id: str) -> None:
        if self._click_timer is not None:
            self._click_timer = None
        if student_id == self.student_id:
            self.tapped.emit(student_id)

    def enterEvent(self, event) -> None:
        if not self._selected:
            self.setStyleSheet(
                theme.seat_qss(
                    'hover' if self.student_id else 'empty',
                    points_sign=self._points_sign,
                    font_px=self._font_px,
                )
            )
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._repaint()
        super().leaveEvent(event)

    @property
    def visual_state(self) -> str:
        return self._state

    def is_highlighted(self) -> bool:
        return self._selected
