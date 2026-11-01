"""早读 / 午读全屏舞台：对齐原版 .stage（实测 ground-stage-zaodu.json）。

**这里就是用户报障的地方**：「在早读或午读功能内点赞或点倒赞时偶尔出现
动画闪烁或加载失败。」四条根因，逐条在这里堵死：

1. 一次点击两条事件路径 → 格子只发 `tapped` 一个意图，加减判定全在
   VotingController（235ms 待定窗，与原版一致）。
2. 高亮清除定时器是全局共享的 → 闪/水印动画挂在**每个格子自己**的
   QTimer 上，格子销毁动画即销毁，跨格子零共享。
3. 舞台关闭后待定定时器仍触发（凭空补分）→ close_stage 先 end_session
   按作用域整体撤销，再 clear_fx 归零动画。
4. 每次记分整表重建 → 打开只 rebuild 一次，之后只改单个格子。

版式全部按 ground 数字 @1440x900：head 60（图标 26 / 标题 20 / 副题 12.5 /
pill 13 / 退出 90x39），body = 讲台行（独立 8 轨网格，span4 → 697px）
+ 座位网格（10 轨含过道，tile 152.25x112，gap10，过道 0.6em）；
fitFont = max(10, min(tileW*0.92/units, tileH*0.78, 80))。
"""
from __future__ import annotations

import datetime as _dt
from PyQt5.QtCore import Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QFont, QFontMetricsF
from PyQt5.QtWidgets import QGridLayout, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from app.core import layout as layout_mod
from app.core.models import AppState
from app.services.timer_registry import TimerRegistry
from app.services.voting import VoteSource, VotingController
from app.ui import theme
from app.ui.stage_seat_cell import StageSeatCell

GAP = 10
PODIUM_GAP = 8
TITLE = {VoteSource.MORNING: '早读', VoteSource.NOON: '午读'}
ICON = {VoteSource.MORNING: '☀', VoteSource.NOON: '☕'}
_WEEK = '一二三四五六日'


def _pill(text: str, kind: str) -> QLabel:
    l = QLabel(text)
    l.setStyleSheet(theme.pill_qss(kind))
    widths = {'legend': 74, 'legend_undo': 78, 'plus': 74, 'minus': 74, 'undo': 64}
    l.setFixedHeight(31)
    l.setMinimumWidth(widths.get(kind, 0))
    return l


def _grow_pill(label: QLabel) -> None:
    """按当前文字重算宽度（不会小于 ground 下限），避免数字进位后被裁。"""
    label.setFixedHeight(31)
    label.adjustSize()
    if label.width() < label.minimumWidth():
        label.resize(label.minimumWidth(), 31)


class StageView(QWidget):
    """早读 / 午读共用的全屏视图（原版整屏覆盖层）。"""

    closed = pyqtSignal()
    toast_message = pyqtSignal(str, str, int)

    def __init__(self, state: AppState, registry: TimerRegistry, parent=None) -> None:
        super().__init__(parent)
        self._state = state
        self._registry = registry
        self._source = None
        self._cells = {}
        self._all_cells = []
        self._podium_pr = None
        self._fit_size = 80.0
        self.rebuild_count = 0
        self.voting = VotingController(registry, parent=self)
        self.setObjectName('stage')
        self.setStyleSheet(f'QWidget#stage {{ background: {theme.STAGE_BG}; }}')
        self._build_ui()
        self._wire()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(theme.STAGE_PAD_LEFT, theme.STAGE_PAD_TOP, theme.STAGE_PAD_LEFT, 18)
        root.setSpacing(0)

        # --- head ---
        self._head_widget = QWidget()
        self._head_widget.setObjectName('stageHead')
        self._head_widget.setStyleSheet('background: transparent;')
        head = QHBoxLayout(self._head_widget)
        head.setContentsMargins(4, 4, 4, 12)
        head.setSpacing(18)
        self._head_widget.setFixedHeight(60)

        title_box = QHBoxLayout()
        title_box.setSpacing(10)
        self._icon = QLabel('☀')
        self._icon.setObjectName('stageIcon')
        self._icon.setStyleSheet(f'font-size: 26px; color: {theme.INK}; background: transparent;')
        titles = QVBoxLayout()
        titles.setSpacing(2)
        self._h2 = QLabel('早读')
        self._h2.setStyleSheet(f'font-size: 20px; font-weight: 700; color: {theme.INK}; background: transparent;')

        self._sub = QLabel('')
        self._sub.setStyleSheet(f'font-size: 12px; color: {theme.INK3}; background: transparent;')
        titles.addWidget(self._h2)
        titles.addWidget(self._sub)
        title_box.addWidget(self._icon)
        title_box.addLayout(titles)
        head.addLayout(title_box)

        legend = QHBoxLayout()
        legend.setSpacing(8)
        for t, k in (('单击 ＋', 'legend'), ('双击 －', 'legend'), ('长按撤销', 'legend_undo')):
            legend.addWidget(_pill(t, k), 0, Qt.AlignVCenter)
        head.addLayout(legend)

        head.addStretch(1)
        self._stat_box = QHBoxLayout()
        self._stat_box.setSpacing(8)
        self._pill_plus = _pill('本次 +0', 'plus')
        self._pill_minus = _pill('本次 −0', 'minus')
        self._pill_undo = _pill('撤销 0', 'undo')
        for p in (self._pill_plus, self._pill_minus, self._pill_undo):
            self._stat_box.addWidget(p, 0, Qt.AlignVCenter)
        head.addLayout(self._stat_box)

        self._btn_exit = QPushButton('退出全屏')
        self._btn_exit.setObjectName('ghost')
        self._btn_exit.setCursor(Qt.PointingHandCursor)
        self._btn_exit.clicked.connect(self.close_stage)
        head.addWidget(self._btn_exit, 0, Qt.AlignVCenter)
        root.addWidget(self._head_widget)

        # --- body：讲台行 + 座位网格 ---
        self._body = QVBoxLayout()
        self._body.setSpacing(PODIUM_GAP)
        self._podium_row = QWidget()
        self._podium_row.setObjectName('podiumRow')
        self._podium_row.setStyleSheet('background: transparent;')
        self._podium = QLabel('讲 台')
        self._podium.setObjectName('stagePodium')
        self._podium.setParent(self._podium_row)
        self._podium.setStyleSheet(theme.podium_qss(stage=True, obj='stagePodium'))
        self._podium.setAlignment(Qt.AlignCenter)
        self._grid_host = QWidget()
        self._grid_host.setObjectName('stageGrid')
        self._grid_host.setStyleSheet('background: transparent;')
        self._grid_host.installEventFilter(self)
        self._body.addWidget(self._podium_row)
        self._body.addWidget(self._grid_host, 1)
        root.addLayout(self._body, 1)

        self._refit_timer = QTimer(self)
        self._refit_timer.setSingleShot(True)
        self._refit_timer.setInterval(0)
        self._refit_timer.timeout.connect(self.fit_font)

    def set_state(self, state: AppState) -> None:
        """导入/替换整份状态后重新绑定（rebuild 前必须调）。"""
        self._state = state

    def open_stage(self, source: VoteSource) -> None:
        self._source = source
        self._h2.setText(TITLE[source])
        self._icon.setText(ICON[source])
        today = _dt.date.today()
        self._sub.setText(f'{today.month}月{today.day}日 · 周{_WEEK[today.weekday()]}')
        self.voting.begin_session(self._state, source)
        self.rebuild()
        self._refresh_stat()

    def close_stage(self) -> None:
        """退出：先收定时器（防幽灵分），再归零所有格子动画。"""
        s = self.voting.session
        self.voting.end_session()
        for c in self._cells.values():
            c.clear_fx()
        if s is not None:
            summary = f'{TITLE[s.source]}结束：+{s.plus} / −{s.minus}' + (f' / 撤销 {s.undo}' if s.undo else '')
            self.toast_message.emit(summary, 'ok', 2600)
        self._source = None
        self.closed.emit()

    @property
    def source(self) -> VoteSource | None:
        return self._source

    def rebuild(self) -> None:
        """整表重建——**只在进入舞台时发生一次**（缺陷 4 的对策）。"""
        lay = layout_mod.resolve_layout(self._state.layout)
        rows, cols = lay.rows, lay.cols
        groups = lay.col_groups

        for w in self._all_cells:
            w.setParent(None)
            w.deleteLater()
        self._all_cells = []
        self._cells = {}

        by_seat = {(s.row, s.col): s for s in self._state.students}
        for r in range(rows):
            for c in range(cols):
                s = by_seat.get((r, c))
                cell = StageSeatCell(
                    student_id=s.id if s else '',
                    name=s.name if s else '',
                    row=r, col=c, parent=self._grid_host,
                )
                self._all_cells.append(cell)
                if s is None:
                    continue
                self._cells[s.id] = cell
                cell.tapped.connect(self._on_tap)
                cell.long_pressed.connect(self._on_long_press)
        self.rebuild_count += 1

        self._build_podium(lay)
        self.fit_font()

    def _build_podium(self, lay) -> None:
        """讲台行：跨度含**尾部过道轨**（原版实测如此）。
        ground：span4 → x=400.5(相对18为382.5)=track(col2)、
        w=697=4×152.25+3×10+48+10。"""
        pr = layout_mod.podium_range(lay)
        self._podium_pr = pr
        if pr is None:
            self._podium_row.setVisible(False)
            self._podium.hide()
            return
        self._podium_row.setVisible(True)
        self._podium.show()

        self._body.removeWidget(self._podium_row)
        if pr.position == 'bottom':
            self._body.addWidget(self._podium_row)
            return
        self._body.insertWidget(0, self._podium_row)

    @property
    def _font_px(self) -> float:
        return self._fit_size

    @_font_px.setter
    def _font_px(self, v: float) -> None:
        self._fit_size = float(v)

    def fit_font(self) -> None:
        """原版 fitFont：size = max(10, min(tileW*0.92/units, tileH*0.78, 80))，
        tileW = clientW/cols、tileH = clientH/rows（1440x900 下 8 列 6 行 = 80）。

        轨道几何**手动摆位**（不用 QGridLayout）：实测布局器把富余空间按
        自己的规则摊（152.25 摊成 155、48 过道摊成 38），与 CSS
        minmax(0,1fr) 对不上。这里逐轨算 float 再取整，resize 时随
        fit_font 一起重算——几何只有这一个来源。
        """
        st = self._state
        lay = layout_mod.resolve_layout(st.layout)
        cols, rows = max(1, lay.cols), max(1, lay.rows)
        w, h = self._grid_host.width(), self._grid_host.height()
        if w <= 0 or h <= 0:
            return
        units = _name_units_p75(st)
        size = max(10.0, min(w / cols * 0.92 / units, h / rows * 0.78, 80.0))
        self._fit_size = size
        self._podium.setStyleSheet(theme.podium_qss(stage=True, font_px=size * 0.5, obj='stagePodium'))
        self._podium_row.setFixedHeight(int(size * 0.5 * 1.9) + 2)
        ends = layout_mod.group_end_map(lay.col_groups)
        aisle_px = max(4, int(round(size * theme.AISLE_W_STAGE_EM)))
        tile_w, xs = self._layout_tracks(lay, w, h, aisle_px, ends)

        # 行高：最后一行的余量留给虚线框
        row_h = (h - GAP * (rows - 1)) / rows
        hh = int(round(row_h))
        for cell in self._all_cells:
            x = xs.get(cell.col)
            if x is None:
                cell.hide()
                continue
            y = int(round(cell.row * (row_h + GAP)))
            cell.setGeometry(x, y, int(round(x + tile_w)) - x, hh)
            cell.show()
            cell.set_fonts(size, self._name_size(cell.name, size, tile_w))

        # 讲台：跨度含尾部过道轨
        pr = getattr(self, '_podium_pr', None)
        if pr is not None and self._podium_row.isVisible():
            rw = self._podium_row.width()
            last = pr.end - 1
            x0 = xs.get(pr.start, 0)
            x1 = round(xs.get(last, 0) + tile_w)
            if ends.get(last) and last < cols - 1:
                x1 = round(xs.get(last, 0) + tile_w + GAP + aisle_px)
            self._podium.setGeometry(x0, 0, max(0, x1 - x0), self._podium_row.height())

    def _layout_tracks(self, lay, w: int, h: int, aisle_px: int, ends: dict):
        """按 CSS 算式排轨道，返回 (座位轨 float 宽, {列: x 取整})。"""
        cols = max(1, lay.cols)
        groups = lay.col_groups
        n_aisle = sum(1 for c in range(cols) if ends.get(c) and c < cols - 1)
        tracks = cols + n_aisle
        tile = (w - n_aisle * aisle_px - GAP * (tracks - 1)) / cols

        xs = {}
        x = 0.0
        c = 0
        t = 0
        while c < cols:
            xs[c] = round(x)
            x += tile
            if ends.get(c) and c < cols - 1:
                x += GAP + aisle_px
            c += 1
            if c < cols:
                x += GAP
        return (tile, xs)

    _NAME_EM_SCALE = 1.02058

    @classmethod
    def _name_size(cls, name: str, base: float, avail_px: float) -> float:
        """原版 shrinkLongNames：avail = clientWidth(去 2px 边框) − 6，
        放不下就按 0.97 比例缩，最低 8px。"""
        avail = avail_px - 4 - 6
        if avail <= 0:
            return base
        f = QFont(theme.FONT_FAMILY_QT)
        f.setPixelSize(int(round(base)))

        # 与原版权重一致（Bold），否则度量偏窄、仍会溢出
        f.setWeight(QFont.Bold)
        need = QFontMetricsF(f).horizontalAdvance(name or '') * cls._NAME_EM_SCALE
        if need <= avail:
            return base
        return max(8.0, base * (avail / need) * 0.97)

    def _on_tap(self, student_id: str) -> None:
        self.voting.tap(student_id)

    def _on_long_press(self, student_id: str) -> None:
        entry = self.voting.long_press_undo(student_id)
        st = self._state.student_by_id(student_id)
        name = st.name if st else '该同学'
        if entry is None:
            self.toast_message.emit(
                f'{name} 本次{TITLE.get(self._source, "")}还没有操作',
                'warn',
                1400,
            )
            return
        self.toast_message.emit(f'已撤销 {name} {entry.delta:+d}', 'ok', 1200)

    def _wire(self) -> None:
        self.voting.applied.connect(self._on_applied)
        self.voting.undone.connect(self._on_undone)
        self.voting.settled.connect(lambda _sid: self._refresh_stat())
        self.voting.rejected.connect(lambda _sid, why: self.toast_message.emit(why, 'warn', 1400))

    def _on_applied(self, student_id: str, delta: int) -> None:
        kind = 'plus' if delta > 0 else 'minus'
        cell = self._cells.get(student_id)
        if cell is not None:
            cell.flash(kind)
            cell.stamp(kind)
            cell.set_tint(
                _tint(
                    self.voting.net_of(student_id),
                    self.voting.plus_count_of(student_id),
                )
            )
        self._refresh_stat()

    def _on_undone(self, student_id: str, _delta: int) -> None:
        cell = self._cells.get(student_id)
        if cell is not None:
            cell.flash('undo')
            cell.set_tint(
                _tint(
                    self.voting.net_of(student_id),
                    self.voting.plus_count_of(student_id),
                )
            )
        self._refresh_stat()

    def _refresh_stat(self) -> None:
        s = self.voting.session
        if s is None:
            return
        self._pill_plus.setText(f'本次 +{s.plus}')
        self._pill_minus.setText(f'本次 −{s.minus}')
        self._pill_undo.setText(f'撤销 {s.undo}')
        for pill in (self._pill_plus, self._pill_minus, self._pill_undo):
            _grow_pill(pill)

    def eventFilter(self, obj, event) -> bool:
        from PyQt5.QtCore import QEvent
        if obj is self._grid_host and event.type() == QEvent.Resize:
            self._refit_timer.start()
        return super().eventFilter(obj, event)

    def resizeEvent(self, event) -> None:
        self.fit_font()
        super().resizeEvent(event)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.fit_font()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Escape:
            self.close_stage()
            return
        super().keyPressEvent(event)


def _name_units_p75(state) -> float:
    """原版 nameUnitsP75：CJK 每字 1、其他 0.58，取名单的 75 分位。"""
    vals = []
    for s in state.students:
        u = sum(1.0 if ord(ch) > 11904 else 0.58 for ch in s.name or '')
        vals.append(max(1.0, u))
    if not vals:
        return 1.0
    vals.sort()
    return vals[min(len(vals) - 1, int(0.75 * (len(vals) - 1)))]


def _tint(net: int, plus_count: int = 0) -> str:
    """本次会话常驻底色。加过 2 次及以上优先金色（用户要求「加两次变金」）。"""
    if plus_count >= 2:
        return 'got_gold'
    if net > 0:
        return 'got_plus'
    if net < 0:
        return 'got_minus'
    return ''
