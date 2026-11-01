"""二维码：把手机链接编成点阵，交给界面去画。

只用纯 Python 的 segno；没装就返回 None，界面上退化成「显示链接 + 复制」。
不引 Qt —— 这里只产出黑白点阵，怎么画是界面层的事（见
app/ui/dialogs/settings_dialog.py 里的 _paint_qr）。

为什么值得多一个依赖：公网地址长这样
    https://rude-cabinets-packing-album.trycloudflare.com/?t=k3m9pq2x
五十多个字符，让家长/学生照着敲几乎必错。扫一下就完事，所以「扫码配对」是硬需求。
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

try:
    import segno as _segno
except Exception:
    _segno = None


def available() -> bool:
    """有没有可用的编码器。"""
    return _segno is not None


def matrix(text: str) -> list[list[bool]] | None:
    """把 text 编成二维码点阵（True=黑）。编不出来返回 None。

    纠错级别选 M：手机摄像头隔着屏幕斜着拍也认得出，同时点数不至于太密，
    在设置弹窗那种 132px 的方块里还能扫。
    """
    if not text:
        return None
    if _segno is None:
        return None
    try:
        qr = _segno.make(text, error='m', boost_error=False)
        return [[bool(v) for v in row] for row in qr.matrix]
    except Exception as exc:
        logger.warning('二维码生成失败：%s', exc)
        return None


def best_url(lan: str, public: str, token: str) -> str:
    """手机该用哪个地址：有公网用公网（不在同一 WiFi 也能开），否则用局域网。

    口令拼进 query，扫码即用，不用再手输。
    """
    base = (public or lan or '').rstrip('/')
    if not base:
        return ''
    if not token:
        return base + '/'
    return f'{base}/?t={token}'


def url_for_wifi(url: str) -> str:
    """手机上「连同一个 WiFi」时用的说明文字，避免和公网地址搞混。

    只做展示用字符串处理，不含 Qt。
    """
    return (url or '').strip()


PAIR_SCHEME = 'classpoints'


def pair_payload(
    url: str,
    token: str,
    fingerprint: str,
    class_name: str = '',
    device_id: str = '',
) -> str:
    """生成「配对用」的二维码内容。

    为什么不用纯 URL：手机扫到 URL 只会打开网页，网页拿不到班级指纹，
    也就没法跟主机核验「这条链接是不是本班的主机」。所以配对码用自定义
    scheme，把地址、口令、指纹三样一次带全：

        classpoints://pair?u=<url>&t=<token>&f=<fingerprint>&c=<班级名>&d=<设备>

    没有 segno 时也能扫（扫码 App 会把整串原样显示给人看），
    但正常路径是安卓端自己解析这个 scheme。
    """
    from urllib.parse import quote, urlencode

    base = (url or '').rstrip('/')
    params = {'u': base}
    if token:
        params['t'] = token
    if fingerprint:
        params['f'] = fingerprint
    if class_name:
        params['c'] = class_name
    if device_id:
        params['d'] = device_id
    return f'{PAIR_SCHEME}://pair?{urlencode(params, quote_via=quote)}'


def parse_pair_payload(text: str) -> dict[str, str]:
    """把 pair_payload 生成的内容解析回来；不是配对码就返回空字典。"""
    from urllib.parse import parse_qs, unquote, urlparse

    raw = (text or '').strip()
    if not raw.lower().startswith(PAIR_SCHEME + '://'):
        return {}
    try:
        parsed = urlparse(raw)
        qs = parse_qs(parsed.query)
    except ValueError:
        return {}

    out = {}
    for key in ('u', 't', 'f', 'c', 'd'):
        if not qs.get(key):
            continue
        out[key] = unquote(qs[key][0])
    return out
