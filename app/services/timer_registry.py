"""延迟动作的统一登记表。

**为什么需要这个东西**：上一代有一个实测确认的缺陷——
待定分的定时器在舞台关闭后仍然触发，关掉页面后会凭空补记一次分
（`phantomAfterClose=true`）。根因是 `setTimeout` 句柄散在各处，
没有任何地方知道「这个定时器属于哪个场景」。

这里把「登记 / 取消 / 按作用域批量取消」收成一处，并规定：
**界面代码不许直接调 QTimer.singleShot**，一律走这里。
于是「离开场景时集中撤销」成为默认行为，忘记清理会由
tests/test_layering.py 的静态扫描抓出来。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from PyQt5.QtCore import QObject, QTimer, pyqtSignal


@dataclass
class _Task:
    key: str
    scope: str
    timer: QTimer
    callback: Callable[[], None]
    single_shot: bool
    cancelled: bool = False


class TimerRegistry(QObject):
    """所有延迟动作都必须在此登记才允许存在。

    scope 是「作用域」，例如 'stage:morning'、'lottery'。离开该场景时
    调 cancel_scope('stage:morning')，属于它的待执行动作全部作废。
    """

    fired = pyqtSignal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._tasks = {}
        self._scope_counts = {}

    def schedule(
        self,
        key: str,
        delay_ms: int,
        callback: Callable[[], None],
        *,
        scope: str = 'global',
        replace: bool = True,
    ) -> None:
        """安排一个一次性延迟动作。

        key 是同一逻辑动作的标识。replace=True 时，同 key 的旧动作会被取消
        ——这正是上一代「高亮清除句柄串号」问题的对策：新一次投票安排高亮
        清除时，会把上一次的清除作废，而不是让两个定时器互相抹。

        replace=False 用于「确实需要多个并行计时」的场合（如逐格错峰动画），
        此时 key 必须各不相同。
        """
        if replace:
            self.cancel(key)
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.setInterval(max(0, int(delay_ms)))

        task = _Task(key=key, scope=scope, timer=timer, callback=callback, single_shot=True)

        def _on_timeout() -> None:
            if task.cancelled:
                return
            self._tasks.pop(key, None)
            self._dec_scope(scope)
            task.cancelled = True
            self.fired.emit(key)
            callback()

        timer.timeout.connect(_on_timeout)
        self._tasks[key] = task
        self._inc_scope(scope)
        timer.start()

    def schedule_repeating(
        self,
        key: str,
        interval_ms: int,
        callback: Callable[[], None],
        *,
        scope: str = 'global',
    ) -> None:
        """安排一个重复动作（如倒计时刷新）。"""
        self.cancel(key)
        timer = QTimer(self)
        timer.setSingleShot(False)
        timer.setInterval(max(1, int(interval_ms)))
        task = _Task(key=key, scope=scope, timer=timer, callback=callback, single_shot=False)
        timer.timeout.connect(lambda: not task.cancelled and callback())
        self._tasks[key] = task
        self._inc_scope(scope)
        timer.start()

    def cancel(self, key: str) -> bool:
        task = self._tasks.pop(key, None)
        if task is None:
            return False
        task.cancelled = True
        task.timer.stop()
        task.timer.deleteLater()
        self._dec_scope(task.scope)
        return True

    def cancel_scope(self, scope: str) -> int:
        """撤销某个作用域下的全部动作，返回撤销数量。

        进入或离开全屏场景时必须调用。
        """
        keys = [k for k, t in self._tasks.items() if t.scope == scope]
        for k in keys:
            self.cancel(k)
        return len(keys)

    def cancel_all(self) -> int:
        keys = list(self._tasks.keys())
        for k in keys:
            self.cancel(k)
        return len(keys)

    def is_pending(self, key: str) -> bool:
        task = self._tasks.get(key)
        return task is not None and not task.cancelled

    def pending_keys(self, scope: str | None = None) -> list[str]:
        return [
            k
            for k, t in self._tasks.items()
            if not t.cancelled and (scope is None or t.scope == scope)
        ]

    def scope_pending(self, scope: str) -> int:
        return len(self.pending_keys(scope))

    def stats(self) -> dict[str, int]:
        return dict(self._scope_counts)

    def _inc_scope(self, scope: str) -> None:
        self._scope_counts[scope] = self._scope_counts.get(scope, 0) + 1

    def _dec_scope(self, scope: str) -> None:
        n = self._scope_counts.get(scope, 0) - 1
        if n <= 0:
            self._scope_counts.pop(scope, None)
            return
        self._scope_counts[scope] = n

    def shutdown(self) -> None:
        """退出程序前调用，确保没有残留定时器在事件循环外面触发。"""
        self.cancel_all()
