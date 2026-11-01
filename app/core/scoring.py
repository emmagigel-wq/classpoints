"""记分 / 撤销 / 排行 / 记录筛选排序。

上一代这些函数与 state 变量耦合（直接读写模块级 state 并 emit）。
这里全部改成**接收 state 参数**的纯函数，谁调用谁负责通知，
于是可以在没有界面的环境里直接测。
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from app.core.models import AppState, HistoryEntry, Student, as_int


def add_points(state: AppState, student_id: str, delta: int, *, reason: str = '', source: str = 'manual', ts: int | None = None) -> HistoryEntry | None:
    """记一次分；返回写入的记录，学生不存在或 delta 为 0 时返回 None。"""
    student = state.student_by_id(student_id)
    try:
        d = int(delta)
    except (TypeError, ValueError):
        return None
    if student is None or d == 0:
        return None
    entry = HistoryEntry(
        seq=state.next_seq(),
        ts=ts if ts else int(time.time() * 1000),
        student_id=student.id,
        student_name=student.name,
        delta=d,
        reason=reason,
        source=source,
    )
    state.history.append(entry)
    student.points += d
    return entry


def add_points_batch(state: AppState, student_ids: list[str], delta: int, *, reason: str = '', source: str = 'manual') -> list[HistoryEntry]:
    """给多个学生同一笔分（抽奖团体、批量奖励）。"""
    out = []
    now = int(time.time() * 1000)
    for sid in student_ids:
        entry = add_points(state, sid, delta, reason=reason, source=source, ts=now)
        if entry is None:
            continue
        out.append(entry)
    return out


def undo_entry(state: AppState, entry_id: str) -> HistoryEntry | None:
    """按记录 id 撤销；找不到返回 None。"""
    idx = -1
    for i, h in enumerate(state.history):
        if h.id == entry_id:
            idx = i
            break
    if idx < 0:
        return None
    entry = state.history.pop(idx)
    student = state.student_by_id(entry.student_id)
    if student is not None:
        student.points -= entry.delta
    return entry


def undo_last_for(state: AppState, student_id: str, *, min_seq: int = 0) -> HistoryEntry | None:
    """撤销某学生的最后一条记录。

    按 **seq**（操作顺序）倒序而非时间——所以补录一条昨天的时间，
    撤销时仍会先撤销「最近操作的」那条。min_seq 用于把撤销限定在
    本次全屏会话内（上一代的 minSeq 语义）。
    """
    best = None
    for h in state.history:
        if h.student_id != student_id:
            continue
        if h.seq < min_seq:
            continue
        if best is None or h.seq > best.seq:
            best = h
    if best is None:
        return None
    return undo_entry(state, best.id)


def undo_global(state: AppState) -> HistoryEntry | None:
    """撤销全局最后一条（按 seq 倒序，与补录时间无关）。"""
    best = None
    for h in state.history:
        if best is None or h.seq > best.seq:
            best = h
    if best is None:
        return None
    return undo_entry(state, best.id)


def last_entry_seq(state: AppState) -> int:
    """当前最大 seq —— 进入全屏会话时记下来，作为 min_seq。"""
    return state.seq


def recalc_points(state: AppState) -> AppState:
    """用历史记录重算所有学生积分（导入/加载后统一口径）。

    学生积分是历史之和的冗余字段；只要两者可能不同步，就以历史为准。
    """
    sums = {s.id: 0 for s in state.students}
    for h in state.history:
        if h.student_id in sums:
            sums[h.student_id] += h.delta
    for s in state.students:
        s.points = sums.get(s.id, 0)
    return state


@dataclass
class HistoryFilter:
    student_id: str = ''
    source: str = ''
    keyword: str = ''
    ts_from: int = 0
    ts_to: int = 0
    sort: str = 'time-desc'


SORT_LABELS: dict[str, str] = {
    'time-desc': '时间（新→旧）',
    'time-asc': '时间（旧→新）',
    'name-asc': '人名（A→Z）',
    'name-desc': '人名（Z→A）',
}


def sort_label(key: str) -> str:
    return SORT_LABELS.get(key, SORT_LABELS['time-desc'])


def history_sorted(state: AppState, flt: HistoryFilter | None = None) -> list[HistoryEntry]:
    """按筛选与排序取记录。

    排序键刻意与上一代一致：时间优先、seq 兜底，保证同一毫秒内的顺序稳定。
    """
    flt = flt or HistoryFilter()
    items = list(state.history)
    if flt.student_id:
        items = [h for h in items if h.student_id == flt.student_id]
    if flt.source:
        items = [h for h in items if h.source == flt.source]
    if flt.ts_from:
        items = [h for h in items if h.ts >= flt.ts_from]
    if flt.ts_to:
        items = [h for h in items if h.ts <= flt.ts_to]
    if flt.keyword:
        kw = flt.keyword.lower()
        items = [h for h in items
                 if kw in (h.student_name or '').lower() or kw in (h.reason or '').lower()]
    if flt.sort == 'name-asc':
        items.sort(key=lambda h: (h.student_name, -h.ts, -h.seq))
        return items
    if flt.sort == 'name-desc':
        items.sort(key=lambda h: h.student_name, reverse=True)
        items.sort(key=lambda h: (-h.ts, -h.seq))
        items.sort(key=lambda h: h.student_name, reverse=True)
        return items
    if flt.sort == 'time-asc':
        items.sort(key=lambda h: (h.ts, h.seq))
        return items
    items.sort(key=lambda h: (-h.ts, -h.seq))
    return items


def leaderboard(state: AppState) -> list[Student]:
    """积分降序；同分按姓名升序（本地化比较交给界面或调用方）。"""
    return sorted(state.students, key=lambda s: (-s.points, s.name))


def rank_with_ties(state: AppState) -> list[tuple[int, Student]]:
    """带名次的排行；同分并列且跳号（1,1,3）。"""
    rows = leaderboard(state)
    out = []
    last_points = None
    last_rank = 0
    for i, s in enumerate(rows, start=1):
        if last_points is None or s.points != last_points:
            last_rank = i
            last_points = s.points
        out.append((last_rank, s))
    return out


def stats_of(state: AppState, student_id: str) -> dict[str, int]:
    """单个学生的加分合计 / 扣分合计 / 记录条数。"""
    plus = minus = count = 0
    for h in state.history:
        if h.student_id != student_id:
            continue
        count += 1
        if h.delta > 0:
            plus += h.delta
            continue
        minus += -h.delta
    return {'plus': plus, 'minus': minus, 'count': count}


def eligible_students(state: AppState, threshold: int | None = None) -> list[Student]:
    """抽奖资格池：积分 ≥ 门槛，按积分降序。"""
    t = state.settings.prize_threshold if threshold is None else threshold
    pool = [s for s in state.students if s.points >= t]
    return sorted(pool, key=lambda s: (-s.points, s.name))


def _in_bounds(state: AppState, row: int, col: int) -> bool:
    """行列是否落在座位表里。

    早先这里用 `as_int(row, 0, 999, 0)` 兜底：越界坐标被夹成 999（画在座位表外面，
    看不见但数据里存在），负数被夹成 (0,0) 并把人挤走——都算「静默写坏数据」。
    现在越界直接拒绝，调用方拿到 False 再决定怎么提示。
    """
    lay = state.layout
    return 0 <= row < max(1, lay.rows) and 0 <= col < max(1, lay.cols)


def _seat_of(state: AppState, row: object, col: object) -> tuple[int, int] | None:
    """把外部传入的行列收敛成合法座位坐标；越界一律 None。

    **先判范围再取整**：早先的顺序是先 `as_int` 夹到 [0,999] 再判，
    于是 -3 被夹成 0 当成了合法坐标（还把人挤走），999 被夹成 999 当成了真值。
    """
    try:
        r, c = int(row), int(col)
    except (TypeError, ValueError):
        return None
    if _in_bounds(state, r, c):
        return (r, c)
    return None


def add_student(state: AppState, name: str, row: int, col: int) -> Student | None:
    """落座新增；该格已有人则返回那个人（不覆盖，与上一代一致）。"""
    n = (name or '').strip()
    if not n:
        return None
    seat = _seat_of(state, row, col)
    if seat is None:
        return None
    row, col = seat
    occupied = state.student_at(row, col)
    if occupied is not None:
        return occupied
    st = Student(name=n, row=row, col=col)
    state.students.append(st)
    return st


def remove_student(state: AppState, student_id: str) -> bool:
    before = len(state.students)
    state.students = [s for s in state.students if s.id != student_id]
    return len(state.students) != before


def rename_student(state: AppState, student_id: str, name: str) -> bool:
    st = state.student_by_id(student_id)
    n = (name or '').strip()
    if st is None or not n:
        return False
    st.name = n
    for h in state.history:
        if h.student_id == student_id:
            h.student_name = n
    return True


def move_student(state: AppState, student_id: str, row: int, col: int) -> bool:
    """落座；目标格已有人则**交换座位**。越界返回 False（不改数据）。"""
    st = state.student_by_id(student_id)
    if st is None:
        return False
    seat = _seat_of(state, row, col)
    if seat is None:
        return False
    row, col = seat
    if st.row == row and st.col == col:
        return True
    other = state.student_at(row, col)
    if other is not None and other.id != student_id:
        other.row, other.col = st.row, st.col
    st.row, st.col = row, col
    return True


def swap_students(state: AppState, id_a: str, id_b: str) -> bool:
    """交换两个人的座位。

    * 同一个人 → True（请求本身就满足，不需要动作）；
    * 两个不同的人却挤在同一格 → False（数据是坏的，谎报成功只会让坏数据留着）。
    """
    a = state.student_by_id(id_a)
    b = state.student_by_id(id_b)
    if a is None or b is None:
        return False
    if a.id == b.id:
        return True
    if a.row == b.row and a.col == b.col:
        return False
    a.row, b.row = b.row, a.row
    a.col, b.col = b.col, a.col
    return True


def clear_history(state: AppState) -> int:
    """清空全部积分记录并把所有人归零，返回清掉的条数。

    积分是记录算出来的，所以「清记录」必须同时归零，否则会留下
    「0 条记录但某人 12 分」的假账。座次表 / 名单 / 抽奖历史都不动。
    """
    removed = len(state.history)
    state.history = []
    for stu in state.students:
        stu.points = 0
    recalc_points(state)
    return removed
