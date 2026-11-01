"""core：纯逻辑层。

本包内**不得** import 任何界面库（PyQt5/tkinter）或 app.ui / app.services。
由 tests/test_layering.py 静态扫描强制。
"""

from app.core import layout, lottery, models, scoring, state, storage

__all__ = ['layout', 'lottery', 'models', 'scoring', 'state', 'storage']
