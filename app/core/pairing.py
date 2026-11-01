"""配对令牌：给「这一台主机 + 这一个班」发一张可以复制的入场券。

和原来的「口令」不是一回事，所以单独一套：

* **口令**（`settings.sync_token`）是**每台设备都要输入的密码**，可以
  重新生成，重新生成后所有设备都要重输；
* **配对令牌**（`settings.pair_token`）是**写进链接和二维码里的那张票**，
  第一次开公网时自动生成，之后一直不变；手机扫码或拿到链接时把它带上来，
  主机就能判断「这个请求来自扫过我家二维码的设备」，而不是从别处抄来的地址。

为什么不拿口令兼任：口令要能「重新生成以踢掉旧设备」，一旦重新生成，二维码
就失效了，得重新扫；而配对令牌要求稳定，两者生命周期相反。

算法刻意做得简单（`secrets.token_hex`，不引第三方库），只在主机与手机之间
当作编号用，不承担加密职责 —— 真正的门禁仍然是口令。
"""
from __future__ import annotations

import re
import secrets

TOKEN_BYTES = 16
PARAM = 'k'
_TOKEN_RE = re.compile('^[0-9a-f]{16,64}$')


def new_token() -> str:
    """生成一个新的配对令牌（32 位小写十六进制）。"""
    return secrets.token_hex(TOKEN_BYTES)


def is_token(value: str) -> bool:
    """形状校验：只认十六进制，避免把口令误当配对令牌用。"""
    return bool(_TOKEN_RE.match((value or '').strip().lower()))


def ensure_token(state) -> str:
    """确保班级里有一个配对令牌；没有就生成并写回。返回当前值。

    写回后由调用方负责保存（`store.save_now()`）——本模块不碰磁盘，
    这样也能在只读探针里安全调用。
    """
    settings = getattr(state, 'settings', None)
    if settings is None:
        return ''
    current = (getattr(settings, 'pair_token', '') or '').strip()
    if is_token(current):
        return current
    current = new_token()
    settings.pair_token = current
    return current


def clear_token(state) -> str:
    """作废旧令牌并立刻换一个新的（已扫过的设备需要重新扫码）。"""
    settings = getattr(state, 'settings', None)
    if settings is None:
        return ''
    settings.pair_token = new_token()
    return settings.pair_token


def attach(url: str, token: str) -> str:
    """把配对令牌拼进手机链接（已有 query 时用 &，没有时用 ?）。"""
    base = (url or '').strip()
    if not base or not token:
        return base
    sep = '&' if '?' in base else '?'
    if f'{PARAM}=' in base:
        return base
    return f'{base}{sep}{PARAM}={token}'


def extract(query: str) -> str:
    """从 `a=1&k=xxx` 这样的查询串里取配对令牌；没有就返回空串。"""
    from urllib.parse import parse_qs
    try:
        qs = parse_qs((query or '').lstrip('?'))
    except ValueError:
        return ''
    values = qs.get(PARAM) or []
    if values:
        return values[0]
    return ''


def short(token: str, head: int = 8) -> str:
    """界面上显示的短码，避免把 32 位全铺出来。"""
    text = (token or '').strip()
    if len(text) <= head:
        return text
    return text[:head] + '…'
