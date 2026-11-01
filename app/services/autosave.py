"""自动保存：把「保存」变成一件不需要人操心、但失败一定看得见的事。

上一代没有自动保存的节流，也没有失败反馈：写 localStorage 满了会静默失败，
界面上分照加，关掉页面全没了（缺陷 5）。

这里做两件事：
  * 定时检查脏标记，脏了才写，不脏不写（避免无谓的磁盘写与备份轮转）；
  * 每次写完把结果发出来，界面据此显示「已保存 / 未保存（原因）」。

节奏走 TimerRegistry —— 和别处一样，退出时能被统一收走。
"""
from __future__ import annotations

from PyQt5.QtCore import QObject, pyqtSignal

from app.core.state import Store
from app.services.timer_registry import TimerRegistry

SCOPE = 'autosave'
TICK_KEY = 'autosave:tick'
DEFAULT_INTERVAL_MS = 20000


class Autosave(QObject):
    """周期性落盘。"""

    saved = pyqtSignal(bool, str)

    def __init__(
        self,
        store: Store,
        registry: TimerRegistry,
        *,
        interval_ms: int = DEFAULT_INTERVAL_MS,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._store = store
        self._registry = registry
        self._interval = max(1000, int(interval_ms))
        self._running = False

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._registry.schedule_repeating(
            TICK_KEY,
            self._interval,
            self._tick,
            scope=SCOPE,
        )

    def stop(self) -> None:
        self._running = False
        self._registry.cancel(TICK_KEY)

    @property
    def running(self) -> bool:
        return self._running

    def save_now(self) -> bool:
        """立即保存（退出前、用户点保存时用）。结果同样通过 saved 信号发出。"""
        result = self._store.save_now()
        self.saved.emit(result.ok, result.message)
        return result.ok

    def save_if_dirty(self) -> bool:
        if not self._store.dirty:
            return True
        return self.save_now()

    def _tick(self) -> None:
        if not self._store.dirty:
            return
        self.save_now()
