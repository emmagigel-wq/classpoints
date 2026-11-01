"""全屏座位格（早读/午读舞台专用）：对齐原版 .stage-seat。

原版实测（ui_ref/ground-stage-zaodu.json @1440x900）：
  * 152.25x112 白底、2px #e4e7ec 边、r14、overflow hidden；
  * 姓名 1em（随 fitFont 的网格字号）w800 单行居中，字距 .02em；
  * 空格：虚线 2px #eef0f4、透明底、不响应点击；
  * 悬停：边框变 #c7d2fe。

动画（原版 flash + stamp）：
  * flash-plus：0% 内发光 rgba(18,183,106,.6) 7px + scale(1.05)，
    420ms ease-out 到无；minus 同理（红、0.95）；undo 是橙色 + 旋转 -1.2°；
  * stamp：姓名底下盖一层 👍/👎 水印（1.15em、opacity .92），
    弹入 .3s 回弹曲线，1500ms 后 .18s 淡出；
  * got-plus/got-minus 会话底色：整场保留（#c9f5df/#12b76a、#fdd7d4/#f04438）。

**为什么自己画而不用 QSS**：box-shadow inset 与缩放动画 Qt 样式表做不到；
自绘一次绘制 = 一次确定性输出，不存在「两个定时器互相抹颜色」——
那正是报障「动画闪烁」的成因。每个格子的动画状态都是自己的属性，
跨格子零共享，跨会话零残留。
"""

from __future__ import annotations

import time

from PyQt5.QtCore import QPointF, QRectF, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PyQt5.QtWidgets import QWidget

from app.ui import theme


LONG_PRESS_MS = 550
FLASH_MS = 420
STAMP_MS = 1500
STAMP_FADE_MS = 180
BADGE_IN_MS = 300

_FLASH_COLORS = {
    'plus': QColor(18, 183, 106),
    'minus': QColor(240, 68, 56),
    'undo': QColor(247, 144, 9),
}
_FLASH_TRANSFORM = {'plus': 1.03, 'minus': 0.975, 'undo': 1.0}
_FLASH_ROTATE = {'plus': 0.0, 'minus': 0.0, 'undo': -1.2}


class StageSeatCell(QWidget):
    """一个全屏座位格。空格子也用它（student_id=""）。"""

    tapped = pyqtSignal(str)
    long_pressed = pyqtSignal(str)

    def __init__(
        self,
        student_id: str = '',
        name: str = '',
        row: int = 0,
        col: int = 0,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.student_id = student_id
        self.name = name
        self.row, self.col = row, col
        self.setObjectName('stageSeat')
        self.setMinimumSize(0, 0)
        if student_id:
            self.setCursor(Qt.PointingHandCursor)

        self.font_px = 40.0
        self.name_px = 40.0
        self.tint = ''
        self._hover = False

        self._flash_kind = ''
        self._flash_t = 0.0
        self._flash_start = 0.0
        self._flash_timer = QTimer(self)
        self._flash_timer.setInterval(16)
        self._flash_timer.timeout.connect(self._step_flash)

        self._badge = ''
        self._badge_alpha = 0.0
        self._badge_scale = 0.35
        self._badge_start = 0.0
        self._badge_timer = QTimer(self)
        self._badge_timer.setInterval(16)
        self._badge_timer.timeout.connect(self._step_badge_in)
        self._fade_timer = QTimer(self)
        self._fade_timer.setInterval(16)
        self._fade_timer.timeout.connect(self._step_badge_out)
        self._fade_start = 0.0
        self._stamp_timer = QTimer(self)
        self._stamp_timer.setSingleShot(True)
        self._stamp_timer.timeout.connect(self._fade_badge)

        self._long_timer = QTimer(self)
        self._long_timer.setSingleShot(True)
        self._long_timer.timeout.connect(self._fire_long)
        self._long_fired = False

    def set_fonts(self, base_px: float, name_px: float) -> None:
        self.font_px = float(base_px)
        self.name_px = float(name_px)
        self.update()

    def set_tint(self, tint: str) -> None:
        """会话底色（原版 paintSession）：整场保留，直到撤销归零。"""
        self.tint = tint or ''
        self.update()

    def flash(self, kind: str) -> None:
        """重放一次 420ms 内发光（原版 flash：先清 class 再重挂）。

        同一个格子连点：动画从头重放，旧帧作废——
        上一代「两个定时器互相抹」的闪烁在这里结构上不可能发生。
        """
        if kind not in _FLASH_COLORS:
            return
        self._flash_kind = kind
        self._flash_start = time.monotonic()
        self._flash_t = 1.0
        self._flash_timer.start()
        self.update()

    def stamp(self, kind: str) -> None:
        """盖水印：👍/👎 弹入，1500ms 后淡出（原版 stamp）。"""
        if kind not in ('plus', 'minus'):
            return
        self._badge = '👍' if kind == 'plus' else '👎'
        self._badge_alpha = 0.92
        self._badge_scale = 0.35
        self._badge_start = time.monotonic()
        self._badge_timer.start()
        self._fade_timer.stop()
        self._stamp_timer.start(STAMP_MS)
        self.update()

    def clear_fx(self) -> None:
        """退出舞台：所有动画与水印立即归零（不留残影）。"""
        for t in (
            self._flash_timer,
            self._badge_timer,
            self._fade_timer,
            self._stamp_timer,
            self._long_timer,
        ):
            t.stop()
        self._badge = ''
        self._badge_alpha = 0.0
        self._flash_kind = ''
        self._flash_t = 0.0
        self.tint = ''
        self.update()

    def _step_flash(self) -> None:
        k = (time.monotonic() - self._flash_start) * 1000.0 / FLASH_MS
        if k >= 1.0:
            self._flash_timer.stop()
            self._flash_t = 0.0
        else:
            self._flash_t = (1.0 - k) ** 2
        self.update()

    def _step_badge_in(self) -> None:
        k = (time.monotonic() - self._badge_start) * 1000.0 / BADGE_IN_MS
        if k >= 1.0:
            self._badge_timer.stop()
            self._badge_scale = 1.0
        else:
            s = 1.70158
            u = k - 1.0
            self._badge_scale = 0.35 + 0.65 * (1 + (s + 1) * u ** 3 + s * u ** 2)
        self.update()

    def _fade_badge(self) -> None:
        self._fade_start = time.monotonic()
        self._fade_timer.start()

    def _step_badge_out(self) -> None:
        k = (time.monotonic() - self._fade_start) * 1000.0 / STAMP_FADE_MS
        if k >= 1.0:
            self._fade_timer.stop()
            self._badge = ''
            self._badge_alpha = 0.0
        else:
            self._badge_alpha = 0.92 * (1.0 - k)
        self.update()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton and self.student_id:
            self._long_fired = False
            self._long_timer.start(LONG_PRESS_MS)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() != Qt.LeftButton or not self.student_id:
            super().mouseReleaseEvent(event)
            return
        self._long_timer.stop()
        if self._long_fired:
            self._long_fired = False
            super().mouseReleaseEvent(event)
            return
        if self.rect().contains(event.pos()):
            self.tapped.emit(self.student_id)
        super().mouseReleaseEvent(event)

    def _fire_long(self) -> None:
        self._long_fired = True
        self.long_pressed.emit(self.student_id)

    def enterEvent(self, event) -> None:
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def _frame_colors(self) -> tuple[QColor, QColor, bool]:
        """(底色, 边色, 虚线?) —— 与原版 CSS 一一对应。"""
        if not self.student_id:
            return QColor(0, 0, 0, 0), QColor(theme.SEAT_EMPTY_BORDER), True
        if self.tint == 'got_gold':
            return QColor(theme.GOT_GOLD_BG), QColor(theme.GOT_GOLD_LINE), False
        if self.tint == 'got_plus':
            return QColor(theme.GOT_PLUS_BG), QColor(theme.OK), False
        if self.tint == 'got_minus':
            return QColor(theme.GOT_MINUS_BG), QColor(theme.BAD), False
        edge = (
            QColor(theme.SEAT_HOVER_BORDER)
            if self._hover
            else QColor(theme.LINE)
        )
        return QColor(theme.CARD), edge, False

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(1.0, 1.0, -1.0, -1.0)
        path = QPainterPath()
        path.addRoundedRect(rect, 14, 14)

        bg, border, dashed = self._frame_colors()

        t = self._flash_t
        if t > 0.001 and self._flash_kind in _FLASH_TRANSFORM:
            sc = 1.0 + (_FLASH_TRANSFORM[self._flash_kind] - 1.0) * t
            p.translate(rect.center())
            p.rotate(_FLASH_ROTATE[self._flash_kind] * t)
            p.scale(sc, sc)
            p.translate(-rect.center())

        p.setPen(QPen(border, 2, Qt.DashLine if dashed else Qt.SolidLine))
        p.setBrush(bg)
        p.drawPath(path)

        if t > 0.001 and self._flash_kind in _FLASH_COLORS:
            color = QColor(_FLASH_COLORS[self._flash_kind])
            wash = QColor(color)
            wash.setAlphaF(0.15 * t)
            p.setPen(Qt.NoPen)
            p.setBrush(wash)
            p.drawPath(path)
            ring = QColor(color)
            ring.setAlphaF(0.55 * t)
            grow = 7.0 * (1.0 - t)
            halo = QPainterPath()
            halo.addRoundedRect(
                rect.adjusted(-grow, -grow, grow, grow),
                14 + grow,
                14 + grow,
            )
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(ring, max(1.5, 3.0 * t)))
            p.drawPath(halo)

        if self.student_id:
            self._paint_badge(p, rect)
            self._paint_name(p, rect)
        p.end()

    def _paint_badge(self, p: QPainter, rect) -> None:
        """👍/👎 印在右上角的小圆牌上。

        原版是把一个 1.15em 的大拇指平铺满整格（.ss-badge inset 0），压住名字，
        看着糊;这里改成右上角小圆牌 + 白底描边,任何底色上都清楚,也不挡字。
        """
        if not self._badge or self._badge_alpha <= 0.001:
            return
        size = max(10, int(round(self.font_px * 0.44)))
        plate = size * 1.62
        cx = rect.right() - plate * 0.62
        cy = rect.top() + plate * 0.62
        center = QPointF(cx, cy)
        p.save()
        p.setOpacity(min(1.0, self._badge_alpha))
        p.translate(center)
        sc = max(0.01, self._badge_scale)
        p.scale(sc, sc)
        p.translate(-center)
        p.setPen(QPen(QColor(16, 24, 40, 30), 1))
        p.setBrush(QColor(255, 255, 255, 238))
        p.drawEllipse(center, plate / 2.0, plate / 2.0)
        f = QFont(theme.FONT_FAMILY_QT)
        f.setPixelSize(max(8, int(round(size * 0.96))))
        p.setFont(f)
        p.setPen(QColor(theme.INK))
        p.drawText(
            QRectF(cx - plate / 2.0, cy - plate / 2.0, plate, plate),
            Qt.AlignCenter,
            self._badge,
        )
        p.restore()

    def _paint_name(self, p: QPainter, rect) -> None:
        f = QFont(theme.FONT_FAMILY_QT)
        f.setPixelSize(max(8, int(round(self.name_px))))
        f.setWeight(QFont.Bold)
        f.setLetterSpacing(QFont.AbsoluteSpacing, self.name_px * 0.02)
        p.setFont(f)
        p.setPen(QColor(theme.INK))
        p.drawText(
            rect,
            Qt.AlignCenter | Qt.TextSingleLine,
            self.name or '（未命名）',
        )
