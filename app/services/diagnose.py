"""多端连接的体检：把「为什么连不上」拆成几条能自己看的结论。

用户报「另一台电脑连不上」时，最耗时间的不是修，而是不知道断在哪一环：
端口没开、口令没设、隧道没起、域名被别的机器占着、还是对方根本不是跟随端。
这里每一项都独立可判，输出「结论 + 该怎么改」。
"""
from __future__ import annotations

import socket
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass


@dataclass
class Check:
    key: str
    title: str
    ok: bool
    detail: str


_UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) ClassPoints/1.0'


def _port_open(port: int, host: str = '127.0.0.1', timeout: float = 0.6) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _get_json(url: str, timeout: float = 4.0) -> tuple[int, dict | None]:
    req = urllib.request.Request(url, headers={'User-Agent': _UA, 'Accept': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            import json

            raw = resp.read(4096)
            return int(getattr(resp, 'status', 200) or 200), json.loads(raw.decode('utf-8', 'replace'))
    except urllib.error.HTTPError as exc:
        return int(exc.code), None
    except Exception:
        return 0, None


def run_checks(settings, *, tunnel_status: str = '', default_port: int = 8765) -> list[Check]:
    """给设置页用的体检。`settings` 是 Settings 实例（不落盘、不联网改状态）。"""
    out = []
    role = getattr(settings, 'sync_role', 'host') or 'host'
    out.append(
        Check(
            'role',
            '本机角色',
            True,
            '跟随另一台电脑（只从它取数据，自己不开公网）'
            if role == 'client'
            else '主机（开端口、开公网、别的设备连这台）',
        )
    )
    if role == 'client':
        url = (getattr(settings, 'sync_client_url', '') or '').strip()
        tok = (getattr(settings, 'sync_client_token', '') or '').strip()
        out.append(
            Check(
                'host_url',
                '主机地址',
                bool(url),
                url or '还没填。把主机「设置→多端同步」里的链接整条粘进来即可',
            )
        )
        out.append(
            Check(
                'host_token',
                '口令',
                bool(tok),
                '已设置' if tok else '还没填。口令在主机那台设置里',
            )
        )
        if url:
            code, info = _get_json(url.rstrip('/') + '/api/ping?t=' + urllib.parse.quote(tok))
            ok = code == 200 and isinstance(info, dict)
            auth = bool(info.get('auth')) if ok else False
            out.append(
                Check(
                    'host_ping',
                    '主机的接口',
                    ok,
                    f'通，auth={auth}'
                    if ok
                    else f'连不上（HTTP {code or "无响应"}）。确认主机那台程序开着、并且开了公网或同在一个网络',
                )
            )
        out.append(
            Check(
                'auto',
                '自动同步',
                bool(getattr(settings, 'sync_auto', False)),
                '已开（每 2 分钟一次）'
                if getattr(settings, 'sync_auto', False)
                else '未开。建议打开',
            )
        )
        return out

    port = int(getattr(settings, 'sync_port', 0) or default_port)
    out.append(
        Check(
            'port',
            '本机端口',
            _port_open(port),
            f'{port} 正在监听'
            if _port_open(port)
            else f'{port} 没在监听。到「多端同步」点『开始同步』',
        )
    )
    tok = (getattr(settings, 'sync_token', '') or '').strip()
    out.append(
        Check(
            'token',
            '访问口令',
            bool(tok),
            '已设置' if tok else '没设口令 —— 手机扫码会拿到 401',
        )
    )
    hostname = (getattr(settings, 'tunnel_hostname', '') or '').strip()
    token = (getattr(settings, 'tunnel_token', '') or '').strip()
    out.append(
        Check(
            'tunnel_cfg',
            '固定域名配置',
            bool(hostname and token),
            f'{hostname}' if hostname else '没配。要么用一键部署，要么用临时隧道',
        )
    )
    out.append(
        Check(
            'tunnel_run',
            '穿透进程',
            bool(tunnel_status),
            tunnel_status or '没在跑。点『开启公网连接』',
        )
    )
    if hostname:
        code, info = _get_json(f'https://{hostname}/api/ping?t=' + urllib.parse.quote(tok))
        ok = code == 200 and isinstance(info, dict)
        out.append(
            Check(
                'public',
                '公网可达',
                ok,
                '能访问'
                if ok
                else f'公网打不开（HTTP {code or "无响应"}）。若另一台电脑也开着同一个域名，会互相抢流量 —— 让那台改成「跟随本机」',
            )
        )
    return out


def as_text(checks: list[Check]) -> str:
    """给用户复制走的纯文本报告。"""
    lines = ['班级积分系统 · 多端连接体检', '']
    for c in checks:
        lines.append(f'[{"通过" if c.ok else "待处理"}] {c.title}：{c.detail}')
    return '\n'.join(lines)
