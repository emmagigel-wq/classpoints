"""协调层：把界面意图翻译成 core 的纯函数调用，并管住一切延迟动作。

这一层存在的主要理由是**副作用需要归属**：定时器要有作用域、
记分要有唯一入口、动画要有代次。这些都放在这里，
界面就只剩下「显示」和「发意图」。
"""

from __future__ import annotations

__all__ = ['timer_registry', 'voting', 'pulse', 'autosave']
