"""操作流水账（op log）：多端各自留副本时，用它把各端的改动合并起来。

设计要点（为什么这样做）
------------------------
1. **只合并「发生过的事」，不合并「算出来的分」**。积分是由记录累加出来的
   （``scoring.recalc_points``），所以两端只要记录集合一致，分数自然一致，
   不会出现「这边 12 分那边 10 分」的拉锯。
2. **记录按 id 去重**。每条加分/扣分在产生时就带一个 id；两端互推时同一个
   id 只留一份，天然幂等——同一条推十次也只加一次。
3. **名单按 id 合并，冲突让主机说了算**。学生改名/换座位这类字段没有时间戳
   可依赖，多端同时改必然要有人让步；这里规定「主机端已有的一律沿用主机」，
   只补对方有、主机没有的学生（例如手机端新建的同学）。
4. **序号（seq）由主机统一重发**。各端的 seq 只是本地排序用，合并后凡是从
   对方收来的记录都重新编号，避免两端 seq 撞车。
"""
from __future__ import annotations

from typing import Any, Iterable

from app.core import scoring
from app.core.models import AppState, HistoryEntry, Student

MAX_TEXT = 200


def entry_to_dict(entry: HistoryEntry) -> dict[str, Any]:
    """把一条记录压成可传输的 dict（键名与存档一致）。"""
    return {
        'id': entry.id,
        'seq': int(entry.seq),
        'ts': int(entry.ts),
        'studentId': entry.student_id,
        'studentName': entry.student_name,
        'delta': int(entry.delta),
        'reason': entry.reason,
        'source': entry.source,
    }


def entry_from_dict(raw: dict[str, Any]) -> HistoryEntry | None:
    """从 dict 还原一条记录；脏数据返回 None（宁可丢一条，也不要写坏存档）。"""
    if not isinstance(raw, dict):
        return None
    eid = str(raw.get('id') or '').strip()
    sid = str(raw.get('studentId') or raw.get('student_id') or '').strip()
    if not eid or not sid:
        return None
    try:
        delta = int(raw.get('delta') or 0)
        ts = int(raw.get('ts') or 0)
        seq = int(raw.get('seq') or 0)
    except (TypeError, ValueError):
        return None
    if delta == 0:
        return None
    return HistoryEntry(
        id=eid[:64],
        seq=seq,
        ts=ts,
        student_id=sid[:64],
        student_name=str(raw.get('studentName') or raw.get('student_name') or '')[:MAX_TEXT],
        delta=delta,
        reason=str(raw.get('reason') or '')[:MAX_TEXT],
        source=str(raw.get('source') or 'manual')[:32],
    )


def entry_ids(state: AppState) -> set[str]:
    """当前已有的记录 id 集合。"""
    return {e.id for e in getattr(state, 'history', []) or [] if e.id}


def ops_since(state: AppState, since: int = 0, limit: int = 0) -> list[dict[str, Any]]:
    """取出 ``seq > since`` 的记录，按 seq 升序（供对端增量拉取）。"""
    rows = [e for e in getattr(state, 'history', []) or [] if int(e.seq) > int(since)]
    rows.sort(key=lambda e: (int(e.seq), int(e.ts), e.id))
    if limit and len(rows) > limit:
        rows = rows[:limit]
    return [entry_to_dict(e) for e in rows]


def max_seq(state: AppState) -> int:
    """当前最大序号。"""
    top = int(getattr(state, 'seq', 0) or 0)
    for e in getattr(state, 'history', []) or []:
        top = max(top, int(e.seq))
    return top


def _next_seq(state: AppState) -> int:
    state.seq = int(getattr(state, 'seq', 0) or 0) + 1
    return state.seq


def merge_students(state: AppState, incoming: Iterable[dict[str, Any]]) -> list[Student]:
    """把对方有、本地没有的学生补进来；已存在的沿用本地（主机优先）。"""
    have = {s.id for s in state.students}
    added = []
    for raw in incoming:
        if not isinstance(raw, dict):
            continue
        sid = str(raw.get('id') or '').strip()
        name = str(raw.get('name') or '').strip()
        if not sid or not name or sid in have:
            continue
        try:
            row = int(raw.get('row') or 0)
            col = int(raw.get('col') or 0)
            points = int(raw.get('points') or 0)
        except (TypeError, ValueError):
            continue

        # 对方给的座位可能越界，或与本地已有学生撞车
        occupied = {(s.row, s.col) for s in state.students}
        if (row, col) in occupied or row < 0 or col < 0 or row >= state.layout.rows or col >= state.layout.cols:
            row, col = _first_free_seat(state, occupied)
        student = Student(
            id=sid[:64],
            name=name[:MAX_TEXT],
            row=max(0, row),
            col=max(0, col),
            points=points,
        )
        state.students.append(student)
        have.add(student.id)
        added.append(student)
    return added


def _first_free_seat(state: AppState, occupied: set[tuple[int, int]]) -> tuple[int, int]:
    """按行优先找一个空座位；整张表都满了就往下加一行（不丢人）。"""
    rows = max(1, int(state.layout.rows))
    cols = max(1, int(state.layout.cols))
    for r in range(rows):
        for c in range(cols):
            if (r, c) not in occupied:
                return (r, c)
    state.layout.rows = rows + 1
    return (rows, 0)


def merge_ops(state: AppState, incoming: Iterable[dict[str, Any]]) -> int:
    """合并对方推来的记录，返回真正新增的条数（幂等）。

    只有「本地没有的 id」才会被收下，并被重新编号到当前 seq 之后。
    """
    known = entry_ids(state)
    added = 0
    for raw in incoming:
        entry = entry_from_dict(raw)
        if entry is None or entry.id in known:
            continue
        entry.seq = _next_seq(state)
        state.history.append(entry)
        known.add(entry.id)
        added += 1
    if added:
        state.history.sort(key=lambda e: (int(e.seq), int(e.ts), e.id))
        scoring.recalc_points(state)
    return added


def apply_remote(
    state: AppState,
    ops: Iterable[dict[str, Any]] | None = None,
    students: Iterable[dict[str, Any]] | None = None,
) -> tuple[int, int]:
    """合并一次远端数据，返回 ``(新增记录数, 新增学生数)``。"""
    added_students = merge_students(state, students or []) if students else []
    added_ops = merge_ops(state, ops or []) if ops else 0
    if added_students and not added_ops:
        # 只补了学生：他们的历史分是直接带过来的，需要重算
        scoring.recalc_points(state)
    return added_ops, len(added_students)
