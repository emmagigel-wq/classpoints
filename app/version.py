"""版本号的唯一来源。

改版本只改这里；界面标题、导出文件名、备份文件头都读这个模块，
避免出现「顶栏写一个版本、导出文件里写另一个」的老问题。
"""
from __future__ import annotations

APP_NAME = '班级积分系统'
APP_ID = 'classpoints'

__version__ = '2.13.16'

LEGACY_WEB_VERSION = '1.2.201'

DATA_SCHEMA = 2


def version_tuple() -> tuple[int, int, int]:
    """把 __version__ 拆成 (主, 次, 修订)，便于比较。"""
    parts = __version__.split('.')
    while len(parts) < 3:
        parts.append('0')
    return tuple(int(p) for p in parts[:3])


def version_string() -> str:
    return __version__


def window_title() -> str:
    return f'{APP_NAME} v{__version__}'
