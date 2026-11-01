"""座位表（主界面）：对齐原版 .seatmap 网格。

原版几何（1440x900 实测，ui_ref/nodes-default.txt + css-rules.txt）：
  * grid-template-columns: repeat(cols, minmax(64px,1fr))，块间插 22px 过道轨；
    gap 10px；行高 min 66px，align-content:start（**行不摊高**）；
  * **空格也渲染**（.seat.empty 虚线 + 浅灰「+」），不是跳过；
  * 讲台 .podium-row 与网格共用列轨道，span 含尾部过道轨，min-height 42，mb 10；
  * 首格实测 x=37 y=204（wrap 内相对 0/52），行距 76（66+10），格宽 112。

轨道几何**手动摆位**（fit 里 setGeometry）：与 stage.py 同一结论——
QGridLayout 摊富余的规则和 CSS minmax(0,1fr) 对不上（实测把 112 摊成
102/112 混排、行高 66 摊成 88），几何只能有一个来源。

**历史缺陷 4 的修补点**：记分只 update_seat 单格文字，绝不整表重建。
rebuild() 只在布局/名单真变了时由 MainWindow 调用。
"""
from __future__ import annotations

from PyQt5.QtCore import Qt, QTimer, pyqtSignal
from PyQt5.QtWidgets import QFrame, QLabel, QSizePolicy, QWidget

from app.core import layout as layout_mod
from app.core.models import AppState
from app.services.pulse import CellVisual
from app.ui import theme
from app.ui.seat_map_cell import SeatCell

GAP = theme.SEAT_GAP
AISLE = theme.AISLE_W
ROW_H = theme.SEAT_MIN_H
PODIUM_H = 42
PODIUM_MB = 10


class PodiumBar(QLabel):
    """讲台条：渐变底 + 虚线金边 + 「讲 台」宽字距（原版 .podium）。"""

    def __init__(self, parent=None) -> None:
        super().__init__('讲 台', parent)
        self.setObjectName('podium')
        self.setStyleSheet(theme.podium_qss(stage=False))
        self.setMinimumHeight(PODIUM_H)
        self.setAlignment(Qt.AlignCenter)


class SeatMap(QWidget):
    """整个班级的座位网格（含空格、过道、讲台）——手动摆位。"""

    rebuilt = pyqtSignal()
    seat_tapped = pyqtSignal(str)
    seat_double = pyqtSignal(str)
    seat_right = pyqtSignal(str)
    seat_long = pyqtSignal(str)
    empty_clicked = pyqtSignal(int, int)
    seat_dropped = pyqtSignal(str, int, int)
    editing_changed = pyqtSignal(bool)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName('seatMap')
        self._cells = {}
        self._by_seat = {}
        self._all = []
        self._aisles = []
        self._podium = None
        self._groups = [8]
        self._lay = None
        self._state = None
        self._editing = False
        self._selected = ''
        self._rebuild_count = 0
        self._refit_timer = QTimer(self)
        self._refit_timer.setSingleShot(True)
        self._refit_timer.setInterval(0)
        self._refit_timer.timeout.connect(self._relayout)

        self._drag_sid = ''
        self._drag_ghost = None
        self._drag_hover = None

    def set_background(self, path: str | None) -> None:
        """座位表底图。原版 `.seatmap.has-bg` 就是这么做的：整块铺一张图，
        座位格自己是不透明底，所以只在格间缝与过道里透出来（照片当底纹用）。"""
        if not path:
            self.setStyleSheet('')
            return
        try:
            from pathlib import Path
            if not Path(path).is_file():
                self.setStyleSheet('')
                return
        except OSError:
            self.setStyleSheet('')
            return
        p = str(path).replace('\\', '/')
        self.setStyleSheet("QWidget#seatMap { border-image: url('%s') 0 0 0 0 stretch stretch; }" % p)

    def rebuild(self, state: AppState) -> None:
        """全量重建。只在布局/名单真的变了时调用（缺陷 4 守卫：计数可测）。"""
        self._rebuild_count += 1
        self._state = state
        lay = layout_mod.resolve_layout(state.layout)
        self._lay = lay
        self._groups = lay.col_groups

        for w in self._all + self._aisles:
            w.setParent(None)
            w.deleteLater()
        if self._podium is not None:
            self._podium.setParent(None)
            self._podium.deleteLater()
        self._all.clear()
        self._aisles.clear()
        self._cells.clear()
        self._by_seat.clear()
        self._podium = None

        by_seat = {(s.row, s.col): s for s in state.students}
        ends = layout_mod.group_end_map(self._groups)

        # 过道轨：块末列之后插一条（最后一列不插）
        aisle_cols = [c for c in range(lay.cols) if ends.get(c) and c < lay.cols - 1]
        for r in range(lay.rows):
            for c in aisle_cols:
                aisle = QFrame(self)
                aisle.setObjectName('aisle')
                aisle.setStyleSheet('background: transparent; border: none;')
                self._aisles.append(aisle)

        # 空格也建（.seat.empty 虚线 + 浅灰「+」）
        for r in range(lay.rows):
            for c in range(lay.cols):
                student = by_seat.get((r, c))
                cell = SeatCell(student, r, c, parent=self)
                cell.set_editing(self._editing)
                self._all.append(cell)
                self._by_seat[(r, c)] = cell
                if student is None:
                    continue
                self._cells[student.id] = cell
                if student.id == self._selected:
                    cell.set_selected(True)

        pr = layout_mod.podium_range(lay)
        if pr is not None:
            self._podium = PodiumBar(self)
            self._podium_pr = pr
        else:
            self._podium_pr = None

        self._wire_cells()
        self._relayout()
        self.rebuilt.emit()

    def _track_xs(self, w: int):
        """按 CSS 算式排轨道：返回 (座位轨 float 宽, {列: x 取整})。"""
        cols = max(1, self._lay.cols)
        ends = layout_mod.group_end_map(self._groups)
        n_aisle = sum(1 for c in range(cols) if ends.get(c) and c < cols - 1)
        tracks = cols + n_aisle
        tile = (w - n_aisle * AISLE - GAP * (tracks - 1)) / cols
        xs = {}
        x = 0.0
        for c in range(cols):
            xs[c] = round(x)
            x += tile
            if ends.get(c) and c < cols - 1:
                x += GAP + AISLE
            if c < cols - 1:
                x += GAP
        return (tile, xs)

    def _relayout(self) -> None:
        lay = self._lay
        if lay is None or self.width() <= 0:
            return

        w = self.width()
        tile, xs = self._track_xs(w)
        ends = layout_mod.group_end_map(self._groups)

        top = 0
        pr = self._podium_pr
        podium_bottom = self._podium is not None and pr is not None and pr.position == 'bottom'
        # 讲台在上方时网格整体下移
        if self._podium is not None and not podium_bottom:
            top = PODIUM_H + PODIUM_MB

        grid_h_full = lay.rows * ROW_H + (lay.rows - 1) * GAP

        # 末行补 21px（原版留给虚线框的高度）
        last_row_h = ROW_H + 21 if lay.rows > 1 else ROW_H

        # 过道轨
        aisle_cols = [c for c in range(lay.cols) if ends.get(c) and c < lay.cols - 1]
        idx = 0
        for r in range(lay.rows):
            for c in aisle_cols:
                x = xs.get(c, 0) + round(tile) + GAP
                h = last_row_h if r == lay.rows - 1 else ROW_H
                y = top + r * (ROW_H + GAP)
                self._aisles[idx].setGeometry(x, y, AISLE, h)
                idx += 1

        # 座位格
        for cell in self._all:
            x = xs.get(cell.col)
            if x is None:
                cell.hide()
                continue
            y = top + cell.row * (ROW_H + GAP)
            h = last_row_h if cell.row == lay.rows - 1 else ROW_H
            cell.setGeometry(x, y, round(x + tile) - x, h)
            cell.show()

        def podium_x_span():
            last = pr.end - 1
            x0 = xs.get(pr.start, 0)
            x1 = round(xs.get(last, 0) + tile)
            if ends.get(last) and last < lay.cols - 1:
                x1 += GAP + AISLE
            return (x0, max(0, x1 - x0))

        if self._podium is not None and pr is not None and not podium_bottom:
            x0, pw = podium_x_span()
            self._podium.setGeometry(x0, 0, pw, PODIUM_H)

        total_h = top + grid_h_full + 21
        if self._podium is not None and podium_bottom:
            x0, pw = podium_x_span()
            self._podium.setGeometry(x0, total_h + PODIUM_MB, pw, PODIUM_H)
            total_h += PODIUM_MB + PODIUM_H
        self.setMinimumHeight(total_h)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._refit_timer.start()

    def _wire_cells(self) -> None:
        for cell in self._all:
            cell.tapped.connect(self.seat_tapped)
            cell.double_tapped.connect(self.seat_double)
            cell.right_clicked.connect(self.seat_right)
            cell.long_pressed.connect(self.seat_long)
            cell.empty_clicked.connect(self.empty_clicked)
            cell.dropped_on.connect(self.seat_dropped)
            cell.drag_started.connect(self._on_drag_started)
            cell.drag_moved.connect(self._on_drag_moved)
            cell.drag_finished.connect(self._on_drag_finished)

    def _cell_at_global(self, gpos) -> SeatCell | None:
        """全局坐标 → 座位格（会从子标签往上找，seatName 等子控件也覆盖到）。

        `gpos` 可能是 None：Qt 在拖拽被取消/窗口已销毁时会给到空坐标。这里宽进严出地
        兜住——槽函数里抛异常在 PyQt5 里不是「报个错」，是直接 abort 整个进程，
        用户看到的就是「软件闪退」。
        """
        if gpos is None:
            return None
        try:
            w = self.childAt(self.mapFromGlobal(gpos))
        except (TypeError, RuntimeError):
            return None
        while w is not None and not isinstance(w, SeatCell):
            w = w.parent()
        if isinstance(w, SeatCell):
            return w
        return None

    def _on_drag_started(self, student_id: str) -> None:
        cell = self._cells.get(student_id)
        if cell is None:
            return
        self._drag_sid = student_id
        shot = cell.grab()
        ghost = QLabel(self)
        ghost.setPixmap(shot)
        ghost.setFixedSize(cell.size())
        ghost.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        ghost.setStyleSheet('background: transparent;')
        ghost.raise_()
        ghost.show()
        self._drag_ghost = ghost

    def _on_drag_moved(self, student_id: str, gpos) -> None:
        ghost = self._drag_ghost
        if ghost is not None and gpos is not None:
            try:
                local = self.mapFromGlobal(gpos)
            except (TypeError, RuntimeError):
                local = None
            if local is not None:
                ghost.move(
                    local.x() - ghost.width() // 2,
                    local.y() - ghost.height() // 2,
                )
        hover = self._cell_at_global(gpos)
        if hover is not None and hover is self._cells.get(student_id):
            hover = None
        if hover is not self._drag_hover:
            if self._drag_hover is not None:
                self._drag_hover.set_drop_target(False)
            self._drag_hover = hover
            if hover is not None:
                hover.set_drop_target(True)

    def _on_drag_finished(self, student_id: str, gpos) -> None:
        ghost, self._drag_ghost = self._drag_ghost, None
        if ghost is not None:
            ghost.hide()
            ghost.deleteLater()
        hover, self._drag_hover = self._drag_hover, None
        if hover is not None:
            hover.set_drop_target(False)
        target = self._cell_at_global(gpos)
        sid, self._drag_sid = self._drag_sid, ''
        if target is None or sid != student_id:
            return
        if target is self._cells.get(student_id):
            return
        self.seat_dropped.emit(student_id, target.row, target.col)

    def update_seat(self, state: AppState, student_id: str) -> bool:
        """只刷一格文字。返回是否命中（名单里没有=需要 rebuild）。"""
        cell = self._cells.get(student_id)
        student = state.student_by_id(student_id)
        if cell is None or student is None:
            return False
        cell.bind(student)
        return True

    def apply_visual(self, student_id: str, visual: CellVisual) -> bool:
        cell = self._cells.get(student_id)
        if cell is None:
            return False
        cell.apply_visual(visual)
        return True

    def refresh_all_text(self, state: AppState) -> int:
        n = 0
        for sid, cell in self._cells.items():
            student = state.student_by_id(sid)
            if student is None:
                continue
            cell.bind(student)
            n += 1
        return n

    def reset_visuals(self) -> None:
        if self._state is None:
            return
        self.refresh_all_text(self._state)

    def set_selected(self, student_id: str) -> None:
        if self._selected and (old := self._cells.get(self._selected)):
            old.set_selected(False)
        self._selected = student_id or ''
        if student_id and (cell := self._cells.get(student_id)):
            cell.set_selected(True)

    def set_editing(self, on: bool) -> None:
        """编辑模式：只切样式，不重建（重建会吞掉刚按下的指针）。"""
        self._editing = on
        for cell in self._all:
            cell.set_editing(on)
        self.editing_changed.emit(on)

    def set_font_scale(self, px: int) -> None:
        for cell in self._all:
            cell.set_font_scale(px)

    def cell_of(self, student_id: str) -> SeatCell | None:
        return self._cells.get(student_id)

    def cell_at(self, row: int, col: int) -> SeatCell | None:
        return self._by_seat.get((row, col))

    @property
    def cell_count(self) -> int:
        return len(self._cells)

    @property
    def rebuild_count(self) -> int:
        return self._rebuild_count

    def student_ids(self) -> list[str]:
        return list(self._cells.keys())
