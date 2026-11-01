"""排行榜：原版 `.rank-list / li.rank-item` 的 1:1 复刻。

规格来自 ui_ref（1440x900 实测 ground-default.json + css-rules.txt 第 55-68 行）：

    .rank-item  高 40，grid 30px/1fr/auto，gap 8，padding 8/10，圆角 10
    .rk         24x24 圆角 8，12px w700（top1/2/3 用渐变 + 白字，其余 #f2f4f7/#98a2b3）
    .nm         14px w600，超长省略号
    .pt         15px w700（正 #12b76a / 负 #f04438 / 零 #101828）
    .bar        贴底 h3，宽 = |pts|/max*100%（min 2%），90deg #a5b4fc→#4c6ef5，
                圆角 0/3/3/0，opacity .55
    hover       #f7f9fc        selected  #eef3ff

刷新纪律与上一代同：**就地改文本**，行数变了才重建（快速连点不掉帧）。

名次用**顺序号 i+1**（app.js:1888 buildRank 的 `var rank = i + 1`），
不用 scoring.rank_with_ties 的并列跳号——ground 实测 24 人全 0 分就是 1..24。
"""
from __future__ import annotations

from PyQt5.QtCore import QPointF, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QLinearGradient, QPainter, QPainterPath
from PyQt5.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from app.core import scoring
from app.ui import theme

ROW_H = theme.RANK_ITEM_H
ROW_W = 283
RK_COL = 30
GAP = 8
PAD_X = 10
PAD_Y = 8
BAR_H = 3
BAR_RADIUS = BAR_H / 2.0
HOVER_BG = '#f7f9fc'
SELECTED_BG = '#eef3ff'
MIN_PCT = 2.0
MAX_PCT = 100.0
EMPTY_TEXT = '还没有学生，去「设置」导入座位表'


class _ElideLabel(QLabel):
    """超长用省略号——保留全名，只在显示时截断（cell_text 要拿全名）。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._full = ''

    def set_full_text(self, text: str) -> None:
        self._full = text
        self._elide()

    def full_text(self) -> str:
        return self._full

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._elide()

    def _elide(self) -> None:
        width = max(0, self.width())
        text = self.fontMetrics().elidedText(self._full, Qt.ElideRight, width)
        if text != self.text():
            self.setText(text)


class _RankItem(QWidget):
    """一行 li.rank-item。背景与进度条自己画（Qt 的 QSS 没有绝对定位的伪元素）。

    原版 li 是 grid 30px/1fr/auto，width=283 (= rankHost.width - padding 20)。
    Qt 这里让宽度 = 宿主宽度 - padding（外部负责把宿主宽度对齐 ground），避免用
    手动 fixed width（那样会写死数字，不复用 rankHost 布局）。
    """

    clicked = pyqtSignal(str)
    double_clicked = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._sid = ''
        self._name = ''
        self._points = 0
        self._pct = MIN_PCT
        self._hover = False
        self._pressed = False
        self._selected = False
        self.setFixedHeight(ROW_H)
        self.setCursor(Qt.PointingHandCursor)
        self.setAttribute(Qt.WA_StyledBackground, False)
        # 背景由 paintEvent 画（hover/selected 圆角矩形 + 底部进度条），
        # 这里只放子控件。
        # 原版 grid 是 30px/1fr/auto：名次列固定 30，名字列吃掉剩余，
        # 分数列按内容。holder 用来夹住 rk 的 24px，别被拉伸。
        self.setFixedWidth(ROW_W)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(PAD_X, PAD_Y, PAD_X, PAD_Y)
        lay.setSpacing(GAP)
        holder = QWidget(self)
        holder.setFixedWidth(RK_COL)
        holder.setStyleSheet('background: transparent;')
        hl = QHBoxLayout(holder)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(0)
        self.rk = QLabel('', holder)
        self.rk.setObjectName('rk')
        self.rk.setFixedSize(theme.RANK_RK, theme.RANK_RK)
        self.rk.setAlignment(Qt.AlignCenter)
        hl.addWidget(self.rk, 0, Qt.AlignLeft)
        hl.addStretch(1)
        lay.addWidget(holder)
        self.nm = _ElideLabel(self)
        self.nm.setObjectName('nm')
        self.nm.setStyleSheet(
            f'QLabel#nm {{ background: transparent; color: {theme.INK};'
            ' font-size: 14px; font-weight: 700; }'
        )
        lay.addWidget(self.nm, 1)
        self.pt = QLabel('', self)
        self.pt.setObjectName('pt')
        self.pt.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        lay.addWidget(self.pt, 0)

    def apply(self, rank: int, student, max_abs: int) -> None:
        self._sid = student.id
        self._name = student.name
        self._points = int(student.points)
        self.nm.set_full_text(student.name)
        self.pt.setText(str(self._points))
        self.pt.setStyleSheet(
            f'QLabel#pt {{ background: transparent; font-size: 15px; font-weight: 700;'
            f' color: {_point_color(self._points)}; }}'
        )
        self._style_rank(rank)
        self._pct = _bar_pct(self._points, max_abs)
        self.update()

    def _style_rank(self, rank: int) -> None:
        top = theme.RANK_TOP.get(rank)
        if top is None:
            self.rk.setStyleSheet(
                f'QLabel#rk {{ background: {theme.PILL_GRAY_BG}; color: {theme.INK3};'
                ' font-size: 12px; font-weight: 700; border-radius: 8px; }'
            )
        else:
            self.rk.setStyleSheet(
                f'QLabel#rk {{ background: {theme.rank_top_grad(rank)};'
                ' color: #ffffff; font-size: 12px; font-weight: 700; border-radius: 8px; }'
            )
        self.rk.setText(str(rank))

    def set_selected(self, selected: bool) -> None:
        if selected != self._selected:
            self._selected = selected
            self.update()

    def student_id(self) -> str:
        return self._sid

    def name_text(self) -> str:
        return self._name

    def rank_text(self) -> str:
        return self.rk.text()

    def point_text(self) -> str:
        return str(self._points)

    def bar_ratio(self) -> float:
        return self._pct

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = self.rect()
        if self._selected:
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(SELECTED_BG))
            painter.drawRoundedRect(rect, 10, 10)
        elif self._hover:
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(HOVER_BG))
            painter.drawRoundedRect(rect, 10, 10)
        width = rect.width() * self._pct / 100.0
        if width > 0:
            grad = QLinearGradient(0, 0, width, 0)
            grad.setColorAt(0.0, QColor(theme.RANK_BAR_A))
            grad.setColorAt(1.0, QColor(theme.RANK_BAR_B))
            painter.setPen(Qt.NoPen)
            painter.setBrush(grad)
            painter.setOpacity(0.55)
            painter.drawPath(_bar_path(width, rect.height()))
        painter.end()

    def enterEvent(self, event) -> None:
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:
        self._pressed = event.button() == Qt.LeftButton
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self._pressed and event.button() == Qt.LeftButton and self.rect().contains(event.pos()):
            self.clicked.emit(self._sid)
        self._pressed = False
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self.double_clicked.emit(self._sid)
        super().mouseDoubleClickEvent(event)


def _point_color(points: int) -> str:
    if points > 0:
        return theme.OK
    if points < 0:
        return theme.BAD
    return theme.INK


def _bar_pct(points: int, max_abs: int) -> float:
    """min 2%、max 100%（app.js:1892）。"""
    ratio = abs(int(points)) / float(max(1, max_abs)) * 100.0
    return max(MIN_PCT, min(MAX_PCT, ratio))


def _bar_path(width: float, height: float) -> QPainterPath:
    """圆角 0/3/3/0：左边直角、右边圆角。"""
    radius = min(BAR_RADIUS, width / 2.0)
    top = height - BAR_H
    path = QPainterPath()
    path.moveTo(0.0, top)
    path.lineTo(width - radius, top)
    if radius > 0:
        path.arcTo(width - 2 * radius, top, 2 * radius, BAR_H, 90.0, -90.0)
    path.lineTo(QPointF(width, top + BAR_H))
    path.lineTo(QPointF(0.0, top + BAR_H))
    path.closeSubpath()
    return path


class LeaderboardView(QWidget):
    """积分排行榜（顺序名次 + 进度条）。"""

    item_clicked = pyqtSignal(str)
    item_double = pyqtSignal(str)

    def __init__(self, store, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._store = store
        self._rows = []
        self._selected = ''
        self._build()
        self.refresh()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self._empty = QLabel(EMPTY_TEXT, self)
        self._empty.setObjectName('rankEmpty')
        self._empty.setAlignment(Qt.AlignCenter)
        self._empty.setWordWrap(True)
        self._empty.setStyleSheet(
            f'QLabel#rankEmpty {{ color: {theme.INK3};'
            ' font-size: 13px; padding: 24px 12px; background: transparent; }'
        )
        root.addWidget(self._empty)
        self._list = QWidget(self)
        self._list.setStyleSheet('background: transparent;')
        self._lay = QVBoxLayout(self._list)
        self._lay.setContentsMargins(0, 0, 0, 0)
        self._lay.setSpacing(0)
        # 列表高度 = 行数×行高，由外部滚动区负责裁切。
        self._list.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        root.addWidget(self._list, 0, Qt.AlignTop)
        root.addStretch(1)

    def _on_row_clicked(self, sid: str) -> None:
        self.set_selected(sid)
        self.item_clicked.emit(sid)

    def _on_row_double(self, sid: str) -> None:
        self.item_double.emit(sid)

    def set_selected(self, student_id: str) -> None:
        self._selected = student_id or ''
        for row in self._rows:
            row.set_selected(row.student_id() == self._selected)

    def selected_id(self) -> str:
        return self._selected

    def refresh(self) -> None:
        students = scoring.leaderboard(self._store.state)
        if not students:
            self._set_empty(True)
            return
        self._set_empty(False)
        max_abs = max(1, abs(int(students[0].points)))
        if len(students) != len(self._rows):
            self._rebuild(students, max_abs)
            return
        for index, student in enumerate(students):
            self._rows[index].apply(index + 1, student, max_abs)

    def _set_empty(self, empty: bool) -> None:
        self._empty.setVisible(empty)
        self._list.setVisible(not empty)

    def _rebuild(self, students: list, max_abs: int) -> None:
        for row in self._rows:
            self._lay.removeWidget(row)
            row.setParent(None)
            row.deleteLater()
        self._rows = []
        for index, student in enumerate(students):
            row = _RankItem(self._list)
            row.clicked.connect(self._on_row_clicked)
            row.double_clicked.connect(self._on_row_double)
            row.apply(index + 1, student, max_abs)
            row.set_selected(student.id == self._selected)
            self._lay.insertWidget(index, row)
            self._rows.append(row)
        # 行数变了才走这里；就地刷新见 refresh()。
        self._list.setFixedHeight(len(self._rows) * ROW_H)

    def row_count(self) -> int:
        return len(self._rows)

    def cell_text(self, row: int, col: int | None = None):
        """col 省略 → (名次, 姓名, 积分)；给了 col → 单个字符串。"""
        if not (0 <= row < len(self._rows)):
            return ('', '', '') if col is None else ''
        item = self._rows[row]
        cells = (item.rank_text(), item.name_text(), item.point_text())
        return cells if col is None else cells[col]
