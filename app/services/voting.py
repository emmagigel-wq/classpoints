"""投票控制器：早读 / 午读里「点座位给分」的唯一入口。

**为什么要有这一层**：上一代最要命的一处缺陷就在这里。座位格同时挂了
`click` 和 `pointerup` 两套判定，一次点击跑了两条路径，实测「单击后加分 0、
记录 0」——也就是用户说的「点赞偶尔没反应」。

还有一处相关的：待定分的定时器在舞台关闭后仍然触发，关掉页面后凭空补记
一次分（`phantomAfterClose=true`）。

**与原版 app.js 逐行对齐的语义**（ui_ref/app.js bindGrid，原版行为即规格）：
  * 单击 → 235ms 待定后 **加分**（pendingTimer）；
  * 235ms 内第二击 → 取消待定加分，改为 **减分**（双击=倒赞）；
  * 长按 550ms → **撤销**本次会话内该生最后一笔。
待定动作一律登记在 TimerRegistry 的会话作用域里：end_session 一次性收走，
关闭后不可能再凭空落分。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable

from PyQt5.QtCore import QObject, pyqtSignal

from app.core import scoring
from app.core.models import AppState, HistoryEntry
from app.services.timer_registry import TimerRegistry


class VoteKind(Enum):
    PLUS = 'plus'
    MINUS = 'minus'


class VoteSource(Enum):
    MORNING = 'morning'
    NOON = 'noon'

    @property
    def step_key(self) -> str:
        return 'step_morning' if self is VoteSource.MORNING else 'step_noon'

    @property
    def label(self) -> str:
        return '早读' if self is VoteSource.MORNING else '午读'


@dataclass
class VoteTicket:
    """一次投票的票据。高亮句柄挂在票据上，重入时按票据作废。"""

    student_id: str
    kind: VoteKind
    seq_at: int
    applied: bool = False
    entry: HistoryEntry | None = None


@dataclass
class StageSession:
    """一次全屏会话（早读或午读）。计数器与原版 session 对象一一对应。"""

    source: VoteSource
    min_seq: int
    scope: str
    step: int = 1
    plus: int = 0
    minus: int = 0
    undo: int = 0
    net: dict = field(default_factory=dict)
    plus_count: dict = field(default_factory=dict)
    recent: dict = field(default_factory=dict)


class VotingController(QObject):
    """投票的唯一入口。

    界面把「哪个座位被单击/双击/长按」告诉它，其余全由它决定：
    是否去重、待定多久、分加多少、撤销限定在哪个范围。
    """

    applied = pyqtSignal(str, int)
    pending = pyqtSignal(str, str)
    settled = pyqtSignal(str)
    undone = pyqtSignal(str, int)
    rejected = pyqtSignal(str, str)

    PENDING_MS = 235
    DEBOUNCE_MS = 45

    def __init__(
        self,
        registry: TimerRegistry,
        clock: Callable[[], float] | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._registry = registry
        self._clock = clock or (lambda: time.monotonic() * 1000.0)
        self._session = None
        self._state = None

    @property
    def session(self) -> StageSession | None:
        return self._session

    @property
    def active(self) -> bool:
        return self._session is not None

    def begin_session(self, state: AppState, source: VoteSource) -> StageSession:
        """进入全屏。min_seq 记下来，撤销被限定在本次会话内。"""
        self.end_session()
        self._state = state
        step = int(getattr(state.settings, source.step_key, 1) or 0)
        session = StageSession(
            source=source,
            min_seq=scoring.last_entry_seq(state) + 1,
            scope=f'stage:{source.value}',
            step=step if step > 0 else 1,
        )
        self._session = session
        return session

    def end_session(self) -> None:
        """离开全屏。

        **关键**：把属于本次会话的所有延迟动作一次性撤销。
        待定分的定时器若还挂着，就正是上一代「关页后凭空补记一次分」的成因。
        """
        if self._session is not None:
            self._registry.cancel_scope(self._session.scope)
            self._session = None

    def stage_scope(self) -> str:
        return self._session.scope if self._session else ''

    def tap(self, student_id: str, *, reason: str = '') -> VoteTicket | None:
        """**单击座位格**（原版语义）：235ms 后加分；窗口内第二击改判减分。

        界面只需挂一套事件、调这一个方法。上一代挂了两套（click 与 pointerup），
        一次物理点击送进来两份，才出现「点了没反应」。

        待定加分登记在会话作用域（key 含学生 id）：
          * end_session 时整体作废 → 不会有幽灵加分；
          * 第二击先 cancel 再落减分 → 单击+双击连手不会出现「先加后减」的闪。
        """
        if self._session is None or self._state is None:
            self.rejected.emit(student_id, '不在全屏模式内')
            return None
        if self._state.student_by_id(student_id) is None:
            self.rejected.emit(student_id, '没有找到这位同学')
            return None

        now = self._clock()
        last = self._session.recent.get(student_id)
        key = self._pending_key(student_id)

        if last is not None and now - last < self.DEBOUNCE_MS:
            return None
        self._session.recent[student_id] = now

        if self._registry.is_pending(key):
            self._registry.cancel(key)
            self.settled.emit(student_id)
            return self._commit(student_id, VoteKind.MINUS, reason=reason)

        self.pending.emit(student_id, 'plus')
        self._registry.schedule(
            key,
            self.PENDING_MS,
            lambda sid=student_id, r=reason: self._fire_single(sid, r),
            scope=self._session.scope,
            replace=True,
        )
        return None

    def _fire_single(self, student_id: str, reason: str) -> None:
        """待定窗口到期：真正落加分。会话可能已结束——作用域已收走，走不到这。"""
        if self._session is None or self._state is None:
            return
        if self._state.student_by_id(student_id) is None:
            return
        self.settled.emit(student_id)
        self._commit(student_id, VoteKind.PLUS, reason=reason)

    def long_press_undo(self, student_id: str) -> HistoryEntry | None:
        """长按撤销：撤销该生在**本次会话内**的最后一条记录。

        上一代此处留下模块级 longFired 标志，把下一次点击吞掉。
        这里不设任何跨调用残留标志——撤销就是撤销，不影响后续点击。
        """
        if self._session is not None:
            self._registry.cancel(self._pending_key(student_id))
        return self._undo(student_id)

    def undo_last(self, student_id: str) -> HistoryEntry | None:
        """撤销该生本次会话内最后一条。"""
        return self._undo(student_id)

    def undo_global(self) -> HistoryEntry | None:
        if self._state is None or self._session is None:
            return None
        entry = scoring.undo_global(self._state)
        if entry is not None:
            self.undone.emit(entry.student_id, entry.delta)
        return entry

    def vote(self, student_id: str, kind: VoteKind, *, reason: str = '') -> VoteTicket | None:
        """处理一次**显式**投票意图（界面上的加号 / 减号按钮走这里）。

        先收掉该生可能挂着的待定加分，避免「显式减分 + 稍后待定加分」打架。

        `kind` 允许直接给 "plus"/"minus" 字符串：`_commit` 是靠 `is VoteKind.PLUS`
        判断的，传字符串会静默落进减分分支——那是最难查的一类错（测试和调用方
        都以为自己在加分，界面上却是倒赞）。这里统一归一成枚举。
        """
        if not isinstance(kind, VoteKind):
            try:
                kind = VoteKind(str(kind).strip().lower())
            except ValueError:
                self.rejected.emit(student_id, '无法识别的投票类型')
                return None

        if self._session is None or self._state is None:
            self.rejected.emit(student_id, '不在全屏模式内')
            return None
        if self._state.student_by_id(student_id) is None:
            self.rejected.emit(student_id, '没有找到这位同学')
            return None
        key = self._pending_key(student_id)
        if self._registry.is_pending(key):
            self._registry.cancel(key)
            self.settled.emit(student_id)
        return self._commit(student_id, kind, reason=reason)

    def net_of(self, student_id: str) -> int:
        """本次会话该生的净分（决定常驻底色 got-plus/got-minus，原版 paintSession）。"""
        if self._session is None:
            return 0
        return self._session.net.get(student_id, 0)

    def plus_count_of(self, student_id: str) -> int:
        """本次会话给该生加过几次分（≥2 时全屏座位转金色，用户要求的「加两次变金」）。"""
        if self._session is None:
            return 0
        return self._session.plus_count.get(student_id, 0)

    def pending_ticket_keys(self, student_id: str) -> list[str]:
        """该生当前挂着的待定动作 key（测试用来核泄漏）。"""
        scope = self.stage_scope()
        prefix = f'vote:{student_id}:'
        return [k for k in self._registry.pending_keys(scope) if k.startswith(prefix)]

    def _pending_key(self, student_id: str) -> str:
        return f'vote:{student_id}:pending'

    def _commit(self, student_id: str, kind: VoteKind, *, reason: str) -> VoteTicket | None:
        """真正落分。调用方负责去重与双击判定，这里只做记分与统计。"""
        assert self._state is not None and self._session is not None
        session = self._session

        delta = session.step if kind is VoteKind.PLUS else -session.step

        if not reason:
            reason = session.source.label + ('表现' if delta > 0 else '待改进')

        ticket = VoteTicket(
            student_id=student_id,
            kind=kind,
            seq_at=self._state.seq,
        )

        entry = scoring.add_points(
            self._state,
            student_id,
            delta,
            reason=reason,
            source=session.source.value,
        )
        if entry is None:
            self.rejected.emit(student_id, '没有写入记录')
            return None

        ticket.applied = True
        ticket.entry = entry

        if delta > 0:
            session.plus += delta
            session.plus_count[student_id] = session.plus_count.get(student_id, 0) + 1
        else:
            session.minus += -delta
            session.plus_count.pop(student_id, None)

        session.net[student_id] = session.net.get(student_id, 0) + delta
        self.applied.emit(student_id, delta)
        return ticket

    def _undo(self, student_id: str) -> HistoryEntry | None:
        """撤销本次会话内的最后一笔——**不经过节流**，也不留任何标志位。"""
        if self._session is None or self._state is None:
            return None
        entry = scoring.undo_last_for(self._state, student_id, min_seq=self._session.min_seq)
        if entry is None:
            return None
        session = self._session
        session.undo += 1
        session.net[student_id] = session.net.get(student_id, 0) - entry.delta
        if entry.delta > 0:
            session.plus = max(0, session.plus - entry.delta)
            left = session.plus_count.get(student_id, 0) - 1
            if left > 0:
                session.plus_count[student_id] = left
            else:
                session.plus_count.pop(student_id, None)
            session.plus_count.pop(student_id, None)
        else:
            session.minus = max(0, session.minus + entry.delta)
        self.undone.emit(student_id, entry.delta)
        return entry
