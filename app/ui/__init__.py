"""界面层。

依赖方向见 docs/ARCHITECTURE.md：ui 可以依赖 core / services / io，
反向一律禁止，由 tests/test_layering.py 静态强制。
"""

from __future__ import annotations

__all__ = ["theme"]
