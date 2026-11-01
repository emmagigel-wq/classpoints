"""座位高亮与加/减分动画的唯一调度者。

**这是「动画闪烁」那一条的正面对策。**

上一代的闪烁机制是这样的：每次点击都会 `setStyleSheet` 改背景色，再排一个
`setTimeout` 把颜色改回去。同一个座位连点两下，就变成两个定时器互相抹——
第二个把第一个的颜色提前清掉，视觉上就是抖一下、闪一下。而高亮清除的句柄
还存在全局变量里，A 座位的高亮可能被 B 座位的清除动作顺手清掉。

这里的规矩只有三条：

1. 每个座位只有一个「动画代次」（generation）。任何新动画把代次加一，
   上一代的所有延迟动作按 key 直接作废，不给它们互相抹的机会。
2. 颜色由 `CellVisual` 这个纯数据对象算出来，界面只负责把算好的颜色贴上，
   **不在回调里读当前界面状态**——所以无论谁先谁后，结果都由输入决定。
3. 一切延迟动作走 TimerRegistry，作用域跟着舞台会话走。
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Callable

from PyQt5.QtCore import QObject, pyqtSignal

from app.services.timer_registry import TimerRegistry
from app.services.voting import VoteKind


@dataclass(frozen=True)
class CellVisual:
    """一个座位在某一时刻应该长什么样。不可变——状态是算出来的，不是改出来的。"""

    background: str = '#FFFFFF'
    border: str = '#D8DEE9'
    scale: float = 1.0
    badge: str = ''
    badge_color: str = '#2E7D32'
    glow: float = 0.0

    def merged(self, **kw) -> 'CellVisual':
        return replace(self, **kw)


PALETTE = {
    'plus': CellVisual(background='#DFF5E1', border='#4CAF50', badge_color='#2E7D32'),
    'minus': CellVisual(background='#FDE7E7', border='#E57373', badge_color='#C62828'),
    'pending': CellVisual(background='#FFF6DA', border='#F0B429', glow=1.0),
    'selected': CellVisual(background='#E3F0FF', border='#3B82F6'),
    'leader': CellVisual(background='#FFF8E1', border='#F2B705'),
}


@dataclass
class _Anim:
    student_id: str
    generation: int
    kind: str
    steps: list[CellVisual]
    interval_ms: int
    scope: str


class PulseController(QObject):
    """按座位发动画帧，界面照着画就行。

    `frame(student_id, visual)` 是唯一出口。界面不许自己改颜色。
    """

    frame = pyqtSignal(str, object)
    settled = pyqtSignal(str, object)

    HOLD_MS = 460
    BADGE_MS = 780
    PULSE_MS = 42

    def __init__(self, registry: TimerRegistry, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._registry = registry
        self._generation = {}
        self._scope = 'global'
        self._baseline = {}

    def set_scope(self, scope: str) -> None:
        """绑定到当前舞台会话的作用域，离开时统一撤销。"""
        self._scope = scope or 'global'

    def clear_all(self) -> None:
        """离开舞台：把所有座位的代次推进一格，残留动画全部失效。"""
        for sid in list(self._generation):
            self._bump(sid)
        self._registry.cancel_scope(self._scope)
        self._baseline.clear()

    def set_baseline(self, student_id: str, visual: CellVisual) -> None:
        """登记该生的稳态外观（如排行榜第一名的金底）。"""
        self._baseline[student_id] = visual

    def baseline_of(self, student_id: str) -> CellVisual:
        return self._baseline.get(student_id, CellVisual())

    def flash(self, student_id: str, kind: VoteKind | str, *, amount: int = 1) -> None:
        """播放一次加/减分动画。

        重入安全：同一个座位连点，新的调用会把上一代的帧全部作废——
        所以不会出现「上一下的高亮把这一下抹掉」的闪烁。
        """
        name = kind.value if isinstance(kind, VoteKind) else str(kind)
        gen = self._bump(student_id)
        tone = PALETTE['plus'] if name == 'plus' else PALETTE['minus']
        sign = '+' if name == 'plus' else '-'

        steps = [
            tone.merged(scale=1.12, badge=f'{sign}{abs(amount)}'),
            tone.merged(scale=1.04, badge=f'{sign}{abs(amount)}'),
            CellVisual(border=tone.border, background=tone.background, scale=1.0),
        ]
        self._run(_Anim(student_id, gen, name, steps, 60, self._scope))

        self._registry.schedule(
            f'pulse:{student_id}:{gen}:hold',
            self.HOLD_MS,
            lambda: self._settle(student_id, gen),
            scope=self._scope,
        )
        self._registry.schedule(
            f'pulse:{student_id}:{gen}:badge',
            self.BADGE_MS,
            lambda: self._clear_badge(student_id, gen),
            scope=self._scope,
        )

    def pending(self, student_id: str, on: bool = True) -> None:
        """待定分状态：呼吸光晕，明确告诉用户「这一下还没定」。"""
        gen = self._bump(student_id)
        if not on:
            self._settle(student_id, gen)
            return
        tone = PALETTE['pending']
        self.frame.emit(student_id, tone.merged(scale=1.02, glow=1.0))
        for i in range(1, 6):
            self._registry.schedule(
                f'pulse:{student_id}:{gen}:breath{i}',
                i * self.PULSE_MS * 8,
                lambda s=student_id, g=gen, k=i: self._breath(s, g, k),
                scope=self._scope,
            )

    def pulse_leader(self, student_id: str) -> None:
        """排行榜头名的缓慢呼吸——与待定分的快呼吸区分开。"""
        gen = self._bump(student_id)
        base = PALETTE['leader']
        for i in range(1, 4):
            self._registry.schedule(
                f'pulse:{student_id}:{gen}:lead{i}',
                i * 420,
                lambda s=student_id, g=gen, k=i: self._leader_frame(s, g, k, base),
                scope=self._scope,
            )

    def _run(self, anim: _Anim) -> None:
        """把 steps 逐帧发出去；帧序号进 key，代次变了就自然不再发。"""
        delay = 0
        for idx, visual in enumerate(anim.steps):
            if idx == 0:
                self.frame.emit(anim.student_id, visual)
                continue
            delay += anim.interval_ms
            self._registry.schedule(
                f'pulse:{anim.student_id}:{anim.generation}:f{idx}',
                delay,
                lambda s=anim.student_id, g=anim.generation, v=visual: self._emit_if_current(s, g, v),
                scope=anim.scope,
            )

    def _emit_if_current(self, student_id: str, gen: int, visual: CellVisual) -> None:
        if self._generation.get(student_id) == gen:
            self.frame.emit(student_id, visual)

    def _settle(self, student_id: str, gen: int) -> None:
        if self._generation.get(student_id) != gen:
            return
        self.frame.emit(student_id, self.baseline_of(student_id))
        self.settled.emit(student_id, self.baseline_of(student_id))

    def _clear_badge(self, student_id: str, gen: int) -> None:
        if self._generation.get(student_id) != gen:
            return
        base = self.baseline_of(student_id)
        self.frame.emit(student_id, base)

    def _breath(self, student_id: str, gen: int, k: int) -> None:
        if self._generation.get(student_id) != gen:
            return
        tone = PALETTE['pending']
        glow = 1.0 if k % 2 else 0.45
        scale = 1.02 if k % 2 else 1.0
        self.frame.emit(student_id, tone.merged(glow=glow, scale=scale))

    def _leader_frame(self, student_id: str, gen: int, k: int, base: CellVisual) -> None:
        if self._generation.get(student_id) != gen:
            return
        scale = 1.03 if k % 2 else 1.0
        self.frame.emit(student_id, base.merged(scale=scale))

    def _bump(self, student_id: str) -> int:
        """推进代次：上一代所有按 key 挂着或即将触发的动作全部作废。"""
        gen = self._generation.get(student_id, 0) + 1
        self._generation[student_id] = gen
        prefix = f'pulse:{student_id}:'
        for key in self._registry.pending_keys(self._scope):
            if key.startswith(prefix):
                self._registry.cancel(key)
        return gen

    def generation_of(self, student_id: str) -> int:
        return self._generation.get(student_id, 0)
