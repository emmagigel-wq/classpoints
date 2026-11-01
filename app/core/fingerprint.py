"""班级指纹：一串能让人念出来、也能机器比对的短码。

用途：换地址、断线重连、另一台电脑加入时，用它确认「连的还是同一个班」。
它不是密码，不承担鉴权职责——鉴权靠 8 位同步口令。

材料（顺序固定）：班级名 + 学生（按 row,col,id 排序）+ 座位布局 + 主题文件哈希。
**主题文件由宿主计算**：用户端下载内容不含主题文件，所以它不需要重算，
只把配对时拿到的指纹存下来，重连时跟宿主 ``/api/whoami`` 给的值比字符串即可。
"""

from __future__ import annotations

import base64
import hashlib
import json
import uuid
from typing import Any

_ALPHABET = '23456789ABCDEFGHJKMNPQRSTUVWXYZ'


def _digest(state: Any, theme: str) -> bytes:
    students = []
    for s in getattr(state, 'students', []) or []:
        students.append([
            getattr(s, 'id', ''),
            getattr(s, 'name', ''),
            int(getattr(s, 'row', 0) or 0),
            int(getattr(s, 'col', 0) or 0),
            bool(getattr(s, 'active', True)),
        ])
    students.sort(key=lambda r: (r[2], r[3], r[0]))
    lay = getattr(state, 'layout', None)
    layout = {
        'rows': int(getattr(lay, 'rows', 0) or 0),
        'cols': int(getattr(lay, 'cols', 0) or 0),
        'colGroups': list(getattr(lay, 'col_groups', []) or []),
        'aisleEvery': int(getattr(lay, 'aisle_every', 0) or 0),
        'podium': {
            'enabled': bool(getattr(getattr(lay, 'podium', None), 'enabled', False)),
            'position': str(getattr(getattr(lay, 'podium', None), 'position', 'top')),
            'span': int(getattr(getattr(lay, 'podium', None), 'span', 0) or 0),
        },
    }
    payload = {
        'v': 1,
        'className': str(getattr(state, 'class_name', '') or ''),
        'students': students,
        'layout': layout,
        'theme': theme or '',
    }
    text = json.dumps(payload, ensure_ascii=False, separators=(',', ':'), sort_keys=True)
    return hashlib.sha256(text.encode('utf-8')).digest()


def _encode(raw: bytes, length: int = 9) -> str:
    """把摘要压成无歧义的大写短码（base32 再去掉易混字符）。"""
    b32 = base64.b32encode(raw).decode('ascii').rstrip('=')
    out = []
    for ch in b32:
        if ch in _ALPHABET and ch not in out[-1:]:
            out.append(ch)
        if len(out) >= length:
            break
    while len(out) < length:
        out.append(_ALPHABET[len(out) % len(_ALPHABET)])
    return ''.join(out)


def format_code(digest: bytes) -> str:
    """``CP7-K3M9-P2X4`` 形式：CP + 1 位 + 4 位 + 4 位，共 13 个字符。"""
    code = _encode(digest, 9)
    return f'CP{code[0]}-{code[1:5]}-{code[5:9]}'


def fingerprint_for(state: Any, theme: str = '') -> str:
    """按班级**内容**算指纹（兼容旧调用；``theme`` 已不参与计算）。

    .. deprecated::
       多端比对请用 :func:`class_fingerprint`。内容指纹会随着改人数、
       换座位而变化，不适合当「这个班是谁」的身份码。
    """
    return format_code(_digest(state, ''))


def new_code() -> str:
    """生成一个新的班级指纹（随机、无歧义字符）。"""
    return format_code(uuid.uuid4().bytes + uuid.uuid4().bytes)


def is_code(value: str) -> bool:
    """判断字符串是不是 ``CP#-XXXX-XXXX`` 形状。"""
    text = (value or '').strip().upper()
    if len(text) != 13 or not text.startswith('CP') or text[3] != '-' or text[8] != '-':
        return False
    rest = text[2:3] + text[4:8] + text[9:13]
    return all(ch in _ALPHABET for ch in rest)


def class_fingerprint(state: Any, *, root: str | None = None, refresh: bool = False) -> str:
    """取本班的身份指纹（``CP#-XXXX-XXXX``）。

    这是**班级**的身份证，不是内容摘要、也不是主题摘要：

    * 创建后就写进 ``settings.class_fingerprint``，长驻在数据里；
    * 加人、删人、换座位、换主题、加分扣分都**不会**让它变——多端比对要的
      正是「同班就同码、异班就异码」；
    * 存档里没有（旧数据、导入的新班）就现场生成一个并写回；
    * 各端通过配对/下载数据继承同一个码，不需要重算，也不用带主题文件。

    ``root`` 与 ``refresh`` 仅为兼容旧调用保留：``refresh=True`` 会强制重新
    生成（会打破已配对设备的一致性，只在「另起一个新班」时用）。
    """
    del root
    settings = getattr(state, 'settings', None)
    current = '' if settings is None else str(getattr(settings, 'class_fingerprint', '') or '')
    if refresh or not is_code(current):
        current = new_code()
        if settings is not None:
            try:
                settings.class_fingerprint = current
            except AttributeError:
                pass
    return current
