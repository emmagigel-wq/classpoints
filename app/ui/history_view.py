"""加减分记录 / 记录详情：原版「记录」弹窗内容的 1:1 复刻。

规格来自 ui_ref（ground-history.json 实测 477 节点 + css-rules.txt:121-144）：

    .tabs / .tab      胶囊标签，r999，padding 7/16，13.5px w600，选中 = BRAND 白字
    .filters          底色 #f9fafb，边框 LINE，r12，padding 10/12，gap10，下距 12
    .field.inline     横排标签 + 控件，标签 12.5px w600 INK2，控件 padding 6/9 r9
    .tbl / th / td    13.5px；th 底 #f7f9fc、INK2、w700、padding 9/12；
                      td padding 8/12、下边框 #f2f4f7；行 hover #fbfcfe
    .tag              来源胶囊：#f2f4f7 / INK2 / 12px / padding 2/8
    .stat-cards       4 个统计卡（当前积分 / 累计加分 / 累计扣分 / 记录条数）
    .table-foot       12.5px INK3      .empty  padding 28 居中 INK3 13.5px

这是**弹窗内容组件**，不带窗口标题与关闭按钮（外壳归主窗口 / ModalShell）。
撤销只走 scoring.undo_entry —— 界面不自己从 history 里删条目，
否则「积分」与「记录」两个真相源会分叉。
"""
from __future__ import annotations

import time

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QColor, QPainter, QPen
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QStyleOptionViewItem,
    QStyledItemDelegate,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.core import scoring
from app.core.models import SOURCE_LABELS, HistoryEntry, source_label
from app.ui import theme
from app.ui.widgets.student_picker import StudentPicker

HEADERS = ('时间', '学生', '变动', '原因', '来源', '')
SORT_KEYS = tuple(scoring.SORT_LABELS.keys())
MAX_ROWS = 2000
UNDO_COL = 5
STAT_LABELS = (
    ('points', '当前积分'),
    ('plus', '累计加分'),
    ('minus', '累计扣分'),
    ('count', '记录条数'),
)


class _RowDelegate(QStyledItemDelegate):
    """行分隔线只画在「行与行之间」。

    原版 CSS 明确写了 `.tbl tr:last-child td { border-bottom: none }`，
    而 qss 的 `::item { border-bottom }` 会连最后一行也画上一横，
    看起来就是「最后那一排的边框没消除」。这里自己画线，跳过最后一行。
    """

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index,
    ) -> None:
        super().paint(painter, option, index)
        model = index.model()
        if model is None or index.row() >= model.rowCount() - 1:
            return
        painter.save()
        painter.setPen(QPen(QColor(theme.PILL_GRAY_BG), 1))
        y = option.rect.bottom()
        painter.drawLine(option.rect.left(), y, option.rect.right(), y)
        painter.restore()


TAB_QSS = f"""
QPushButton {{
    background: {theme.CARD}; border: 1px solid {theme.LINE};
    border-radius: 999px; padding: 7px 16px; font-size: 14px;
    font-weight: 700; color: {theme.INK2};
}}
QPushButton[active="true"] {{
    background: {theme.BRAND}; border-color: {theme.BRAND}; color: #ffffff;
}}
"""


def _fmt_time(ts_ms: int) -> str:
    """原版 fmtDateTimeSec：`MM-DD HH:MM:SS`。"""
    try:
        return time.strftime('%m-%d %H:%M:%S', time.localtime(ts_ms / 1000.0))
    except (OverflowError, OSError, ValueError):
        return '-'


def _sign(delta: int) -> str:
    return f'+{delta}' if delta > 0 else str(delta)


class HistoryView(QWidget):
    """记录列表 + 筛选栏 + 撤销。"""

    changed = pyqtSignal()
    undone = pyqtSignal(str)

    def __init__(self, store, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._store = store
        self._mode = 'all'
        self._entries = []
        self._build()
        self.refresh()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(14)

        root.addLayout(self._build_tabs())

        self._stats = self._build_stats()
        root.addWidget(self._stats)

        root.addWidget(self._build_filters())

        self._table = QTableWidget(0, len(HEADERS), self)
        self._table.setObjectName('tbl')
        self._table.setHorizontalHeaderLabels(list(HEADERS))
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setDefaultSectionSize(40)
        self._table.verticalHeader().setMinimumSectionSize(40)
        self._table.setShowGrid(False)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SingleSelection)
        self._table.setFocusPolicy(Qt.NoFocus)
        header = self._table.horizontalHeader()
        header.setHighlightSections(False)
        for col in (0, 1, 2, 4, UNDO_COL):
            header.setSectionResizeMode(col, QHeaderView.Fixed)
        header.setSectionResizeMode(3, QHeaderView.Stretch)
        self._table.setColumnWidth(0, 132)
        self._table.setColumnWidth(1, 110)
        self._table.setColumnWidth(2, 76)
        self._table.setColumnWidth(4, 76)
        self._table.setColumnWidth(UNDO_COL, 68)
        self._table.setStyleSheet(_TABLE_QSS)
        self._table.setItemDelegate(_RowDelegate(self._table))
        root.addWidget(self._table, 1)

        self._empty = QLabel('没有匹配的记录', self)
        self._empty.setObjectName('empty')
        self._empty.setAlignment(Qt.AlignCenter)
        self._empty.setStyleSheet(
            f'QLabel#empty {{ color: {theme.INK3}; font-size: 14px; padding: 28px; background: transparent; }}'
        )
        root.addWidget(self._empty)

        self._summary = QLabel('', self)
        self._summary.setStyleSheet(
            f'color: {theme.INK3}; background: transparent; font-size: 12px;'
        )
        root.addWidget(self._summary)

    def _build_tabs(self) -> QHBoxLayout:
        bar = QHBoxLayout()
        bar.setSpacing(6)
        self._tab_buttons = {}
        for key, text in (('all', '全部记录'), ('one', '某个学生')):
            btn = QPushButton(text, self)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setStyleSheet(TAB_QSS)
            btn.clicked.connect(lambda _c=False, k=key: self.set_mode(k))
            bar.addWidget(btn)
            self._tab_buttons[key] = btn
        bar.addStretch(1)
        self._sync_tabs()
        return bar

    def _build_stats(self) -> QFrame:
        frame = QFrame(self)
        lay = QGridLayout(frame)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        self._stat_values = {}
        for col, (key, label) in enumerate(STAT_LABELS):
            card = QFrame(frame)
            card.setStyleSheet(
                f'QFrame {{ background: {theme.CARD}; border: none; border-radius: 12px; }}'
            )
            inner = QVBoxLayout(card)
            inner.setContentsMargins(12, 12, 12, 12)
            inner.setSpacing(2)
            value = QLabel('0', card)
            value.setAlignment(Qt.AlignCenter)
            value.setStyleSheet(
                f'color: {_stat_color(key)}; font-size: 22px; font-weight: 700; background: transparent;'
            )
            caption = QLabel(label, card)
            caption.setAlignment(Qt.AlignCenter)
            caption.setStyleSheet(
                f'color: {theme.INK3}; font-size: 12px; background: transparent;'
            )
            inner.addWidget(value)
            inner.addWidget(caption)
            lay.addWidget(card, 0, col)
            self._stat_values[key] = value
        frame.setVisible(False)
        return frame

    def _build_filters(self) -> QFrame:
        frame = QFrame(self)
        frame.setObjectName('filters')
        frame.setStyleSheet(
            f'QFrame#filters {{ background: {theme.BTN_GHOST_BG}; border: 1px solid '
            f'{theme.LINE}; border-radius: 12px; }}'
        )
        bar = QHBoxLayout(frame)
        bar.setContentsMargins(12, 10, 12, 10)
        bar.setSpacing(10)

        bar.addWidget(_label('学生', frame))
        self._student = StudentPicker(frame)
        self._student.setMinimumWidth(120)
        self._student.currentIndexChanged.connect(self._on_filter_changed)
        bar.addWidget(self._student)

        bar.addWidget(_label('来源', frame))
        self._source = QComboBox(frame)
        self._source.addItem('全部', '')
        for key, label in SOURCE_LABELS.items():
            self._source.addItem(label, key)
        self._source.currentIndexChanged.connect(self._on_filter_changed)
        bar.addWidget(self._source)

        bar.addWidget(_label('关键词', frame))
        self._keyword = QLineEdit(frame)
        self._keyword.setPlaceholderText('姓名或原因')
        self._keyword.setClearButtonEnabled(True)
        self._keyword.textChanged.connect(self._on_filter_changed)
        bar.addWidget(self._keyword, 1)

        bar.addWidget(_label('排序', frame))
        self._sort = QComboBox(frame)
        for key in SORT_KEYS:
            self._sort.addItem(scoring.sort_label(key), key)
        self._sort.currentIndexChanged.connect(self._on_filter_changed)
        bar.addWidget(self._sort)

        self._reset_btn = QPushButton('重置', frame)
        self._reset_btn.setObjectName('ghost')
        self._reset_btn.setStyleSheet(
            'QPushButton { padding: 6px 12px; font-size: 13px; border-radius: 9px; }'
        )
        self._reset_btn.clicked.connect(self.reset_filters)
        bar.addWidget(self._reset_btn)
        bar.addStretch(1)
        return frame

    def set_mode(self, mode: str) -> None:
        """'all' = 全部记录；'one' = 某个学生（多出 4 张统计卡）。"""
        self._mode = 'one' if mode == 'one' else 'all'
        self._sync_tabs()
        self._stats.setVisible(self._mode == 'one')
        self.refresh()

    def mode(self) -> str:
        return self._mode

    def _sync_tabs(self) -> None:
        for key, btn in getattr(self, '_tab_buttons', {}).items():
            btn.setProperty('active', 'true' if key == self._mode else 'false')
            btn.style().unpolish(btn)
            btn.style().polish(btn)

    def set_student_filter(self, student_id: str) -> None:
        """外部指定学生；给了 id 就切到「某个学生」模式。"""
        student_id = student_id or ''
        if student_id:
            self.set_mode('one')
        else:
            self.refresh()
            return
        idx = self._student.findData(student_id)
        self._student.blockSignals(True)
        self._student.setCurrentIndex(idx if idx >= 0 else 0)
        self._student.blockSignals(False)
        self.refresh()

    def reset_filters(self) -> None:
        for widget in (self._source, self._sort):
            widget.blockSignals(True)
            widget.setCurrentIndex(0)
            widget.blockSignals(False)
        self._keyword.blockSignals(True)
        self._keyword.clear()
        self._keyword.blockSignals(False)
        self._student.blockSignals(True)
        self._student.setCurrentIndex(0)
        self._student.blockSignals(False)
        self.refresh()

    def _on_filter_changed(self, *_: object) -> None:
        self.refresh()

    def _current_filter(self) -> scoring.HistoryFilter:
        sid = self._student.current_student_id()
        if self._mode == 'all':
            sid = ''
        return scoring.HistoryFilter(
            student_id=sid,
            source=str(self._source.currentData() or ''),
            keyword=self._keyword.text().strip(),
            sort=str(self._sort.currentData() or 'time-desc'),
        )

    def refresh(self) -> None:
        state = self._store.state
        self._sync_students(state)

        if self._mode == 'one':
            sid = self._student.current_student_id()
            stats = scoring.stats_of(state, sid) if sid else {
                'plus': 0, 'minus': 0, 'count': 0,
            }
            student = state.student_by_id(sid) if sid else None
            self._stat_values['points'].setText(str(student.points if student else 0))
            self._stat_values['points'].setStyleSheet(
                f'color: {_stat_color('points')}; font-size: 22px; font-weight: 700; background: transparent;'
            )
            for key in ('plus', 'minus', 'count'):
                self._stat_values[key].setText(str(stats[key]))
        else:
            plus = sum(h.delta for h in state.history if h.delta > 0)
            minus = sum(h.delta for h in state.history if h.delta < 0)
            self._stat_values['points'].setText(str(sum(s.points for s in state.students)))
            self._stat_values['points'].setStyleSheet(
                f'color: {_stat_color('points')}; font-size: 22px; font-weight: 700; background: transparent;'
            )
            self._stat_values['plus'].setText(str(plus))
            self._stat_values['minus'].setText(str(minus))
            self._stat_values['count'].setText(str(len(state.history)))

        entries = scoring.history_sorted(state, self._current_filter())
        self._entries = entries[:MAX_ROWS]
        self._render()
        total, shown = len(entries), len(self._entries)
        if not shown:
            self._summary.setText('')
            return
        if shown < total:
            self._summary.setText(
                f'共 {total} 条 · 列表显示最近 {shown} 条 · 排序：{scoring.sort_label(self._current_filter().sort)}'
            )
            return
        self._summary.setText(f'共 {total} 条 · 排序：{scoring.sort_label(self._current_filter().sort)}')

    def _sync_students(self, state) -> None:
        current = self._student.current_student_id()
        self._student.blockSignals(True)
        self._student.set_students(state.students, blank_label='全部')
        if current:
            idx = self._student.findData(current)
            if idx >= 0:
                self._student.setCurrentIndex(idx)
        self._student.blockSignals(False)

    def _render(self) -> None:
        table = self._table
        table.setRowCount(len(self._entries))
        for row, entry in enumerate(self._entries):
            self._fill_row(row, entry)
        empty = not self._entries
        self._table.setVisible(not empty)
        self._empty.setVisible(empty)
        if empty and self._mode == 'one':
            sid = self._student.current_student_id()
            student = self._store.state.student_by_id(sid) if sid else None
            name = student.name if student else '该同学'
            self._empty.setText(f'{name}还没有记录')
            return

    def _fill_row(self, row: int, entry: HistoryEntry) -> None:
        table = self._table

        texts = (
            _fmt_time(entry.ts),
            entry.student_name,
            _sign(entry.delta),
            entry.reason or '—',
            source_label(entry.source),
        )
        for col, text in enumerate(texts):
            item = table.item(row, col)
            if item is None:
                item = QTableWidgetItem('')
                table.setItem(row, col, item)
            if item.text() != text:
                item.setText(text)
            item.setTextAlignment(
                Qt.AlignVCenter | (Qt.AlignRight if col == 2 else Qt.AlignLeft)
            )
            if col == 2:
                item.setForeground(QColor(theme.OK if entry.delta > 0 else theme.BAD))
            elif col in (0, 4):
                item.setForeground(QColor(theme.INK2))
            else:
                item.setForeground(QColor(theme.INK))
        if table.cellWidget(row, UNDO_COL) is None:
            btn = QPushButton('撤销', table)
            btn.setObjectName('tiny')
            btn.setCursor(Qt.PointingHandCursor)
            btn.setFixedHeight(26)
            btn.setMinimumWidth(52)
            btn.clicked.connect(lambda _c=False, r=row: self._undo_row(r))
            table.setCellWidget(row, UNDO_COL, btn)

    def _undo_row(self, row: int) -> None:
        if not 0 <= row < len(self._entries):
            return
        entry = self._entries[row]
        removed = scoring.undo_entry(self._store.state, entry.id)
        self.refresh()
        if removed is None:
            self._summary.setText('这条记录已经不存在了（可能刚被撤销）')
            return
        message = f'已撤销 {removed.student_name} {_sign(removed.delta)}'
        self._summary.setText(message)
        self.undone.emit(message)
        self.changed.emit()

    def undo_current(self) -> bool:
        """供主窗口「撤销上一条」按钮调用：撤销当前选中行。"""
        self._undo_row(self._table.currentRow())
        return True

    def entry_count(self) -> int:
        return len(self._entries)

    def row_texts(self, row: int) -> tuple[str, ...]:
        """该行 5 列文本（测试用，不含撤销按钮列）。"""
        if not 0 <= row < len(self._entries):
            return ()
        return tuple(
            self._table.item(row, col).text() if self._table.item(row, col) else ''
            for col in range(5)
        )


def _stat_color(key: str) -> str:
    if key == 'points':
        return theme.INK
    if key == 'plus':
        return theme.OK
    if key == 'minus':
        return theme.BAD
    return theme.INK


def _label(text: str, parent: QWidget) -> QLabel:
    lab = QLabel(text, parent)
    lab.setStyleSheet(
        f'color: {theme.INK2}; font-size: 12px; font-weight: 700; background: transparent;'
    )

    return lab


_TABLE_QSS = f"""
QTableWidget#tbl {{
    background: {theme.CARD};
    border: 1px solid {theme.LINE};
    border-radius: 12px;
    font-size: 14px;
    gridline-color: transparent;
}}
QTableWidget#tbl::item {{ padding: 8px 12px; border: none; }}
QTableWidget#tbl::item:selected {{ background: {theme.SEL_ACTIVE_BG}; color: {theme.INK}; }}
QHeaderView::section {{
    background: {theme.BTN_GHOST_BG};
    color: {theme.INK2};
    font-weight: 700;
    font-size: 13px;
    border: none;
    border-bottom: 1px solid {theme.LINE};
    padding: 9px 12px;
}}
"""
