"""允许开发态用 `python -m installer` 直接跑安装向导/命令行模式。"""
from __future__ import annotations

import sys

from installer.main import main

if __name__ == '__main__':
    sys.exit(main())
