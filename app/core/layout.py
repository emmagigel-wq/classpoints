"""座位布局：列分组、过道、讲台、行列与网格换算。

上一代这些函数散在 core.js 里（resolveLayout / podiumRange / seatLine /
groupEndMap），与数据模型混在一起。这里独立出来，因为它是**纯几何**，
不碰学生数据，最容易测也最容易错。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from app.core.models import Layout, Podium, as_int

MAX_COLS = 30
MAX_ROWS = 30


def parse_col_groups(text: str | int | None) -> list[int]:
    """把 "2-4-2" / "2,4,2" / "2 4 2" / "2、4、2" 解析成正整数列表。

    上一代用 split(/[^0-9]+/) 实现；这里同样只取数字段。
    """
    if text is None:
        return []
    out = []
    for chunk in re.split('[^0-9]+', str(text)):
        if not chunk:
            continue
        try:
            n = int(chunk)
        except ValueError:
            continue
        if n > 0:
            out.append(n)
    return out


def normalize_groups(groups: list[int] | None, fallback_cols: int = 8) -> list[int]:
    """规整分组：每块 1..30，总列数不超过 30。空则退回单块。"""
    if not groups:
        groups = [as_int(fallback_cols, 1, MAX_COLS, 8)]
    out = []
    total = 0
    for raw in groups:
        n = as_int(raw, 1, MAX_COLS, 1)
        if total + n > MAX_COLS:
            n = MAX_COLS - total
        if n > 0:
            out.append(n)
            total += n
        if total >= MAX_COLS:
            break
    return out or [8]


def resolve_layout(layout: Layout | None) -> Layout:
    """由任意历史形态的 layout 推出规范 layout。

    兼容上一代只有 cols + aisleEvery 的存档：按 aisleEvery 切成多块。
    """
    if layout is None:
        layout = Layout()
    rows = as_int(layout.rows, 1, MAX_ROWS, 6)
    if layout.col_groups:
        groups = normalize_groups(layout.col_groups, 8)
    else:
        cols = as_int(layout.cols, 1, MAX_COLS, 8)
        every = as_int(layout.aisle_every, 0, 12, 0)
        groups = [cols]
        if every > 0 and every < cols:
            groups = []
            left = cols
            while left > 0:
                take = min(every, left)
                groups.append(take)
                left -= take
        groups = normalize_groups(groups, 8)
    total = sum(groups)
    podium = (layout.podium or Podium()).normalized(total)
    return Layout(rows=rows, cols=total, col_groups=groups, podium=podium)


def resolve_layout_dict(raw: dict) -> Layout:
    """给存档 dict 用的便捷入口。"""
    return resolve_layout(Layout.from_dict(raw or {}))


@dataclass(frozen=True)
class PodiumRange:
    start: int
    end: int
    span: int
    position: str


def podium_range(layout: Layout) -> PodiumRange | None:
    """讲台占哪几列；未启用返回 None。"""
    p = layout.podium
    cols = layout.cols
    if not p or not p.enabled or cols <= 0:
        return None
    span = as_int(p.span, 1, cols, min(4, cols))
    if p.align == 'center':
        start = (cols - span) // 2
    elif p.align == 'right':
        start = cols - span
    else:
        start = 0
    start = max(0, min(cols - span, start))
    return PodiumRange(
        start=start,
        end=start + span,
        span=span,
        position='bottom' if p.position == 'bottom' else 'top',
    )


def seat_line(groups: list[int], c: int) -> int:
    """第 c 个座位列（0-based）对应 GUI 网格线号（1-based），已计入过道轨道。

    例：分组 [2,4,2] 时，列 0→线1、列 2→线4、列 6→线9、列 8(末尾)→线11。

    界面用这个把「座位列」翻译成网格坐标；与业务无关，但错一位整个座位表就歪。
    """
    group_list = groups or [1]
    line = 1
    acc = 0
    for i, size in enumerate(group_list):
        if acc + size <= c:
            acc += size
            line += size
            if i < len(group_list) - 1:
                line += 1
        else:
            break
    return line + (c - acc)


def group_end_map(groups: list[int]) -> dict[int, bool]:
    """哪些列号是「所在块的最后一列」（这些列后面有一条过道）。"""
    result = {}
    acc = 0
    group_list = groups or [1]
    for size in group_list:
        acc += size
        result[acc - 1] = True
    return result


def seat_grid_span(groups: list[int], c: int) -> int:
    """第 c 列在网格里向右占几格（含其后的过道轨道）。"""
    if group_end_map(groups).get(c) and c < sum(groups) - 1:
        return 2
    return 1


def total_grid_columns(groups: list[int]) -> int:
    """座位列数 + 过道轨道数 = 网格总列数。"""
    n = len(groups or [1])
    return sum(groups or [1]) + max(0, n - 1)


def columns_of(layout: Layout) -> list[int]:
    return list(layout.col_groups)


def clamp_seats(state, layout: Layout) -> list[tuple[str, int, int]]:
    """布局缩小后，把越界的学生挪到最近的空格，避免数据丢失。

    返回被移动的 [(学生id, 新行, 新列)]，供界面提示。
    上一代这段逻辑写在 setLayout 里，与状态耦合；这里做成纯函数。
    """
    used = {}
    for s in state.students:
        if 0 <= s.row < layout.rows and 0 <= s.col < layout.cols:
            used[s.seat()] = s.id
    moved = []
    for s in state.students:
        in_range = 0 <= s.row < layout.rows and 0 <= s.col < layout.cols
        if in_range:
            continue
        placed = False
        for r in range(layout.rows):
            for c in range(layout.cols):
                if (r, c) not in used:
                    used[(r, c)] = s.id
                    s.row, s.col = r, c
                    moved.append((s.id, r, c))
                    placed = True
                    break
            if placed:
                break
        if not placed:
            s.row, s.col = 0, 0
            moved.append((s.id, 0, 0))
    return moved


def apply_layout(state, rows: int, col_groups: list[int] | str | None) -> list[tuple[str, int, int]]:
    """设置行列与分组，返回被挪动的学生。"""
    if isinstance(col_groups, str):
        groups = parse_col_groups(col_groups)
    elif isinstance(col_groups, list):
        groups = list(col_groups)
    else:
        groups = []
    old = resolve_layout(state.layout)
    groups = normalize_groups(groups, old.cols)
    layout = Layout(
        rows=as_int(rows, 1, MAX_ROWS, 6),
        cols=sum(groups),
        col_groups=groups,
        podium=old.podium,
    )
    layout = resolve_layout(layout)
    state.layout = layout
    return clamp_seats(state, layout)


def seat_is_valid(layout: Layout, row: int, col: int) -> bool:
    return 0 <= row < layout.rows and 0 <= col < layout.cols


def iter_seats(layout: Layout):
    """按行优先遍历所有座位坐标。"""
    for r in range(layout.rows):
        for c in range(layout.cols):
            yield (r, c)
