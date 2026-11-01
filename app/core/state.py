"""状态容器：把 AppState 与「变更通知 / 脏标记 / 保存节奏」绑在一起。

上一代是模块级 state + emit() 广播，任何一处改动都会触发全部监听器。
这里保留「一个真相源」的好处，但加了：
- 变更来源标签（便于界面判断要不要整块重绘）；
- 脏标记与保存结果（落盘失败能被界面感知，见缺陷 5）。

注意：本模块**不含界面代码**，自动化保存节奏由 services/autosave 驱动。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from app.core import storage
from app.core.models import AppState
from app.core.scoring import recalc_points
from app.core.layout import resolve_layout

Listener = Callable[['ChangeEvent'], None]


@dataclass
class ChangeEvent:
    """一次变更。scope 让界面能只刷新受影响的部分（缺陷 4 的结构性规避）。"""

    reason: str = 'update'
    scope: str = 'all'
    student_ids: list[str] = field(default_factory=list)
    extra: dict = field(default_factory=dict)


class Store:
    """应用状态容器。"""

    def __init__(self, state: AppState | None = None) -> None:
        self.state = resolve_state(state) if state is not None else AppState()
        self.state.layout = resolve_layout(self.state.layout)
        recalc_points(self.state)
        self._listeners = []
        self.dirty = False
        self.last_save_ok = None
        self.last_save_message = ''
        self.last_save_reason = ''

    def on_change(self, fn: Listener) -> None:
        self._listeners.append(fn)

    def off_change(self, fn: Listener) -> None:
        if fn in self._listeners:
            self._listeners.remove(fn)

    def emit(self, event: ChangeEvent | None = None) -> None:
        """广播变更。监听器抛错不影响其它监听器（上一代也是这个约定）。"""
        event = event or ChangeEvent()
        self.dirty = True
        for fn in list(self._listeners):
            try:
                fn(event)
            except Exception:
                import traceback

                traceback.print_exc()

    def save_now(self) -> storage.WriteResult:
        """立即落盘；结果被记录下来，界面可以查 last_save_ok。"""
        result = storage.write_state(self.state)
        self.last_save_ok = result.ok
        self.last_save_message = result.message
        self.last_save_reason = result.reason
        if result.ok:
            self.dirty = False
        return result

    def pending_failure(self) -> bool:
        """是否有「改了但没存上」的情况——界面据此显示未保存标记。"""
        return self.dirty and self.last_save_ok is False

    @classmethod
    def load(cls) -> tuple['Store', storage.LoadResult]:
        result = storage.read_state()
        state = result.state if result.state is not None else AppState()
        store = cls(state)
        store.last_save_ok = True if result.ok else False
        store.last_save_message = result.message
        return store, result

    def replace_state(self, state: AppState, event_reason: str = 'replace') -> None:
        self.state = resolve_state(state)
        recalc_points(self.state)
        self.emit(ChangeEvent(reason=event_reason, scope='all'))

    def snapshot(self) -> dict:
        return self.state.to_dict()


def resolve_state(state: AppState) -> AppState:
    """把任意状态规整成可用状态：布局规范化、seq 补正。"""
    state.layout = resolve_layout(state.layout)
    max_seq = 0
    for h in state.history:
        if h.seq > max_seq:
            max_seq = h.seq
    if state.seq < max_seq:
        state.seq = max_seq
    return state
