"""一键部署固定域名：把 Cloudflare 固定隧道的那几步串成一条流水线。

为什么需要这一层：固定域名不是「填个字符串」就有的——它必须在用户的
Cloudflare 账号下真的建一条隧道、把域名指向它、拿到令牌，程序才有东西可用。
手动做要开浏览器、点授权、抄一串 UUID，几乎没人能一次弄对。

诚实边界（必须让用户知道）：这一键仍然是**用他本人的 Cloudflare 账号**在部署。
程序不能凭空注册域名，没有账号、域名也不在 Cloudflare 上的话，这一步做不了。
所以流程里第一步就是浏览器授权，界面会明确写清楚。

对外只有 `deploy()` 一个入口，其余步骤函数都可单独测（可注入 runner）。
"""
from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from app.services import tunnel as tunnel_mod

CRED_DIR = Path.home() / '.cloudflared'
CERT_FILE = CRED_DIR / 'cert.pem'

TUNNEL_NAME = 'classpoints'

_ID_RE = re.compile(r'with id\s+([0-9a-fA-F-]{36})')
_CREDS_RE = re.compile(r'credentials written to\s+(.+?\.json)', re.IGNORECASE)
_UUID_RE = re.compile(r'([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})')
_TABLE_RE = re.compile(
    r'([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})\s+'
    + re.escape(TUNNEL_NAME)
    + r'\b'
)


@dataclass
class Step:
    """流水线里的一个步骤。`lines` 是给它自己看的输出，用于界面滚动显示。"""

    key: str
    title: str
    ok: bool = False
    detail: str = ''
    lines: list[str] = field(default_factory=list)


@dataclass
class DeployResult:
    ok: bool
    steps: list[Step]
    hostname: str = ''
    url: str = ''
    token: str = ''
    tunnel_id: str = ''
    error: str = ''

    def copy_text(self) -> str:
        """给「复制结果」按钮用的纯文本。"""
        rows = [
            '班级积分系统 · 固定域名部署结果',
            f'公网地址：{self.url or "（未生成）"}',
            f'主机名：{self.hostname or "（未设置）"}',
            f'隧道令牌：{self.token or "（未生成）"}',
            f'隧道 ID：{self.tunnel_id or "（未生成）"}',
        ]
        if self.error:
            rows.append(f'失败原因：{self.error}')
        return '\n'.join(rows)


def cloudflared() -> str:
    return tunnel_mod.find_cloudflared()


def has_login() -> bool:
    return CERT_FILE.is_file() and CERT_FILE.stat().st_size > 0


_PUBLIC_TLDS = {
    'cn', 'co', 'me', 'store', 'online', 'com', 'vip', 'xyz', 'tech',
    'site', 'net', 'cc', 'org', 'app', 'top', 'info', 'io', 'dev',
}


def _strip_duplicate_tld(raw: str) -> str:
    """把 `jifen.tians.ccwu.cc.com` 这种「尾巴上又套了一个顶级域」的写法纠回来。

    实测踩到过：用户照抄示例时多打了 `.com`，域名里本来就有 `cc`，于是存进设置的
    是 `...cc.com`——route dns 会照着这个建记录，能成功，但地址是错的、手机打不开。
    只在「最后两段都是顶级域」时才动手，正常的 `a.b.com` 不会被动。
    """
    labels = raw.split('.')
    if len(labels) >= 4 and labels[-1] in _PUBLIC_TLDS and labels[-2] in _PUBLIC_TLDS:
        return '.'.join(labels[:-1])
    return raw


def normalize_hostname(text: str) -> str:
    """把用户输入收拾成主机名。

    接受 `classpoints.example.com`、`https://classpoints.example.com/abc`、
    `classpoints.example.com?t=x` 这些写法——用户从地址栏复制过来就是这样。
    """
    raw = (text or '').strip()
    if not raw:
        return ''
    raw = raw.split('://', 1)[-1]
    raw = raw.split('/', 1)[0]
    raw = raw.split('?', 1)[0].split('#', 1)[0]
    raw = raw.split('@')[-1]
    if ':' in raw:
        head, tail = raw.rsplit(':', 1)
        if tail.isdigit():
            raw = head
    return _strip_duplicate_tld(raw.strip().strip('.').lower())


def hostname_ok(hostname: str) -> bool:
    """只做形状校验：至少两段，允许的字符，段长 1-63。"""
    if not hostname or len(hostname) > 253:
        return False
    parts = hostname.split('.')
    if len(parts) < 2:
        return False
    for part in parts:
        if not part or len(part) > 63:
            return False
        if part[0] == '-' or part[-1] == '-':
            return False
        if not re.fullmatch('[a-z0-9-]+', part):
            return False
    return True


def token_from_credentials(raw: dict) -> str:
    """从 cloudflared 的凭据 json 拼出 `tunnel run --token` 要的那串。

    cloudflared 的令牌就是 base64(JSON{"a":账号标签,"t":隧道ID,"s":隧道密钥})，
    参数名不是随便起的：a=account tag，t=tunnel id，s=secret。
    """
    account = str(raw.get('AccountTag') or raw.get('a') or '').strip()
    tunnel_id = str(raw.get('TunnelID') or raw.get('t') or '').strip()
    secret = str(raw.get('TunnelSecret') or raw.get('s') or '').strip()
    if not (account and tunnel_id and secret):
        return ''
    blob = json.dumps({'a': account, 't': tunnel_id, 's': secret}, separators=(',', ':'))
    return base64.b64encode(blob.encode('utf-8')).decode('ascii')


def token_for_tunnel(tunnel_id: str) -> str:
    """按隧道 ID 找凭据文件，返回令牌；找不到返回空串。"""
    if not tunnel_id:
        return ''
    path = CRED_DIR / f'{tunnel_id}.json'
    try:
        raw = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return ''
    return token_from_credentials(raw)


def _default_runner(args, timeout, on_line):
    """跑一条 cloudflared 命令，逐行回调。返回 (ok, 全文)。"""
    exe = cloudflared()
    if not exe:
        on_line('[错误] 找不到 cloudflared（既没有内置，也不在 PATH 里）')
        return (False, '')
    buf = []
    try:
        proc = subprocess.Popen(
            [exe] + list(args),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding='utf-8',
            errors='replace',
            bufsize=1,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
        )
    except OSError as exc:
        on_line(f'[错误] 启动 cloudflared 失败：{exc}')
        return (False, '')
    deadline = time.monotonic() + timeout
    assert proc.stdout is not None
    for line in proc.stdout:
        text = line.rstrip('\r\n')
        if text:
            buf.append(text)
            on_line(text)
        if time.monotonic() > deadline:
            proc.kill()
            on_line('[错误] 超时')
            return (False, '\n'.join(buf))
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
    return (proc.returncode == 0, '\n'.join(buf))


def step_login(on_line, runner=_default_runner, timeout=180) -> Step:
    step = Step('login', '登录 Cloudflare 账号')
    if has_login():
        step.ok = True
        step.detail = '已经授权过，跳过'
        on_line('已经授权过 Cloudflare，跳过登录。')
        return step
    on_line('即将打开浏览器，请登录并选择你的域名（这一步只需要做一次）。')
    ok, text = runner(['tunnel', 'login'], timeout, on_line)
    step.lines = text.splitlines()
    step.ok = ok and has_login()
    if step.ok:
        step.detail = '授权完成'
        return step
    step.detail = '没有完成授权'
    return step


def _resolve_tunnel_id(on_line=None, runner=None, timeout=120) -> str:
    """查本地已有隧道的 ID。

    为什么要有三种查法：`cloudflared tunnel list --output json` 内部要打 Cloudflare
    REST 接口，偶尔会单次失败（日志里是 `Error Parsing page 1: REST request failed`）。
    一旦这次查询失败，老实现就以为「没有隧道」，转去 `tunnel create`，而 create 会
    报 `tunnel with name already exists` —— 于是整条部署卡死在中间。用户看到的就是
    「读取隧道令牌：凭据文件里没有账号标签或密钥」。
    """
    say = on_line or (lambda _t: None)
    run = runner or _default_runner
    for attempt in (1, 2):
        ok, text = run(['tunnel', 'list', '--output', 'json'], timeout, say)
        if ok and text and '[' in text:
            try:
                items = json.loads(text[text.index('['):])
            except (ValueError, IndexError):
                items = []
            for item in (items if isinstance(items, list) else []):
                if str(item.get('name') or '') == TUNNEL_NAME:
                    tid = str(item.get('id') or '')
                    if tid:
                        return tid
            if items:
                return ''
            continue
        if attempt == 1:
            say('隧道列表这次没取到，重试一次…')
    ok, text = run(['tunnel', 'list'], timeout, say)
    if ok and text:
        match = _TABLE_RE.search(text)
        if match:
            return match.group(1)
    ok, text = run(['tunnel', 'info', TUNNEL_NAME], timeout, say)
    if ok and text:
        match = _UUID_RE.search(text)
        if match:
            return match.group(1)
    return ''


def step_create(on_line, runner=_default_runner, timeout=120) -> Step:
    step = Step('create', '创建（或复用）隧道')
    tunnel_id = _resolve_tunnel_id(on_line, runner=runner, timeout=timeout)
    if tunnel_id:
        step.ok = True
        step.detail = f'复用已有隧道 {tunnel_id[:8]}…'
        on_line(f'已经有隧道 {TUNNEL_NAME}（{tunnel_id}），直接复用。')
        step.lines = [f'Reusing tunnel {TUNNEL_NAME} with id {tunnel_id}']
        return step
    ok, text = runner(['tunnel', 'create', TUNNEL_NAME], timeout, on_line)
    step.lines = text.splitlines()
    match = _ID_RE.search(text)
    if match:
        tunnel_id = match.group(1)
    if not tunnel_id:
        creds = _CREDS_RE.search(text)
        if creds:
            tunnel_id = Path(creds.group(1).strip()).stem
    if not tunnel_id and 'already exist' in text.lower():
        tunnel_id = _resolve_tunnel_id(on_line, runner=runner, timeout=timeout)
        if tunnel_id:
            on_line(f'隧道本来就在（{tunnel_id}），直接复用。')
            step.lines = [f'Reusing tunnel {TUNNEL_NAME} with id {tunnel_id}']
    step.ok = bool(tunnel_id)
    if step.ok:
        step.detail = f'隧道 ID {tunnel_id}'
        return step
    step.detail = '创建隧道失败（常见原因：账号下没有可用域名）'
    return step


def step_route(hostname, on_line, runner=_default_runner, timeout=120) -> Step:
    step = Step('route', f'把 {hostname} 指向隧道')
    ok, text = runner(['tunnel', 'route', 'dns', TUNNEL_NAME, hostname], timeout, on_line)
    step.lines = text.splitlines()
    lowered = text.lower()
    already = ('already' in lowered and 'exist' in lowered) or ('cname' in lowered and 'exist' in lowered)
    step.ok = bool(ok or already)
    if step.ok:
        step.detail = '域名已指向隧道'
    else:
        step.detail = '域名解析没建成'
    return step


def deploy(hostname: str, on_line=None, runner=_default_runner, on_step=None) -> DeployResult:
    """跑完整条流水线。任何一步失败就停在那里，并把已拿到的信息带回去。"""
    say = on_line or (lambda _t: None)
    notify = on_step or (lambda _s: None)
    host = normalize_hostname(hostname)
    result = DeployResult(ok=False, steps=[])
    result.hostname = host

    if not cloudflared():
        result.steps.append(Step('check', '检查 cloudflared', False, '找不到 cloudflared，请先安装或使用安装包版本'))
        result.error = '找不到 cloudflared'
        notify(result.steps[-1])
        return result
    result.steps.append(Step('check', '检查 cloudflared', True, '已找到'))
    notify(result.steps[-1])

    if not hostname_ok(host):
        step = Step('hostname', '校验主机名', False, '主机名要写成 classpoints.你的域名.com 这种形式')
        result.steps.append(step)
        result.error = '主机名不合法'
        notify(step)
        return result
    result.steps.append(Step('hostname', '校验主机名', True, host))
    notify(result.steps[-1])

    create_step = None
    for maker in (step_login, step_create):
        step = maker(say, runner=runner)
        result.steps.append(step)
        notify(step)
        if not step.ok:
            result.error = f'{step.title}：{step.detail}'
            return result
        if maker is step_create:
            create_step = step

    joined = '\n'.join(create_step.lines if create_step else [])
    match = _ID_RE.search(joined)
    result.tunnel_id = match.group(1) if match else ''
    if not result.tunnel_id and create_step is not None:
        creds = _CREDS_RE.search(joined)
        if creds:
            result.tunnel_id = Path(creds.group(1).strip()).stem

    step = step_route(host, say, runner=runner)
    result.steps.append(step)
    notify(step)
    if not step.ok:
        result.error = f'{step.title}：{step.detail}'
        return result

    result.token = token_for_tunnel(result.tunnel_id)
    if not result.token:
        if not result.tunnel_id:
            detail = '没查到隧道 ID（隧道列表这次查询失败），请再点一次重新部署'
        else:
            detail = '凭据文件里没有账号标签或密钥，无法生成令牌'
        step = Step('token', '读取隧道令牌', False, detail)
        result.steps.append(step)
        result.error = '生成令牌失败'
        notify(step)
        return result
    step = Step('token', '读取隧道令牌', True, '已生成（可复制）')
    result.steps.append(step)
    notify(step)
    result.url = f'https://{host}'
    result.ok = True
    return result


__all__ = [
    'Step',
    'DeployResult',
    'deploy',
    'cloudflared',
    'has_login',
    'normalize_hostname',
    'hostname_ok',
    'token_from_credentials',
    'token_for_tunnel',
    'CRED_DIR',
    'TUNNEL_NAME',
]
