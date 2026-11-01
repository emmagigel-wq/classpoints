"""桌面端「各存副本、互相同步」的客户端。

设计要点（与手机端 viewer.html 的 CP 模块同一套语义）：

1. **只搬「发生过的事」，不搬「算出来的分」**。积分由 history 现算，
   所以两边把记录并到一起后再重算，天然不会有「扣了两次」这类错。
2. **每条记录带稳定 id**，合并时按 id 去重 —— 同一批记录重发多少次都只算一次。
3. **两个水位分别记**：`sync_pull_seq` 是我已经从主机读到哪一条，
   `sync_push_seq` 是我已经推到主机的本地序号。合并会重排 seq，
   所以每次重新推送几条重复记录是允许的（服务端按 id 去重），宁可多推不可漏推。
4. 撤销类操作依赖主机侧的未撤销记录，**不能**离线推；本模块只同步加减分记录。

用法：
    client = SyncClient(address, token, store)
    result = client.sync_once()          # 一次往返，双向
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

from app.core import oplog


class SyncError(RuntimeError):
    """连不上、口令不对、地址写错都归到这里，文案直接给用户看。"""


@dataclass
class SyncResult:
    pulled: int = 0
    pushed: int = 0
    students_added: int = 0
    className: str = ''
    fingerprint: str = ''
    errors: list = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.pulled or self.pushed or self.students_added)

    def summary(self) -> str:
        if not self.changed:
            return '已是最新'
        bits = []
        if self.pulled:
            bits.append(f'收下 {self.pulled} 条')
        if self.pushed:
            bits.append(f'送出 {self.pushed} 条')
        if self.students_added:
            bits.append(f'新增 {self.students_added} 人')
        return '、'.join(bits)


def normalize_address(address: str) -> str:
    """把用户粘进来的地址收拾干净：去空格、补协议、去路径与查询串。"""
    text = (address or '').strip()
    if not text:
        return ''
    if '://' not in text:
        text = 'http://' + text
    parts = urllib.parse.urlsplit(text)
    if not parts.netloc:
        return ''
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, '', '', ''))


def parse_pair_link(text: str) -> tuple[str, str, str]:
    """把「手机端那条链接」拆成 (地址, 口令, 配对令牌)。

    第二台电脑最常见的做法是把主机给的链接整条粘进地址框。整条链接里带着
    `?t=口令&k=令牌`，但 normalize_address 会把查询串丢掉 —— 于是口令没了，
    连上去只会 401「口令不对」。这里先把它们抠出来再用。
    """
    raw = (text or '').strip()
    if not raw:
        return ('', '', '')
    addr = normalize_address(raw)
    if '?' not in raw:
        return (addr, '', '')
    query = urllib.parse.urlsplit(raw if '://' in raw else 'http://' + raw).query
    params = urllib.parse.parse_qs(query)
    token = (params.get('t', [''])[0] or '').strip()
    pair = (params.get('k', [''])[0] or '').strip()
    return (addr, token, pair)


class SyncClient:
    """一台桌面机对主机的双向同步器（线程无关，调用方负责放到后台线程）。"""

    TIMEOUT_S = 8.0

    def __init__(
        self,
        address: str,
        token: str,
        store,
        *,
        pair_token: str = '',
        device_id: str = '',
    ):
        address, link_token, link_pair = parse_pair_link(address)
        self.address = address
        self.token = (token or '').strip() or link_token
        pair_token = (pair_token or '').strip() or link_pair
        self.pair_token = (pair_token or '').strip()
        self.device_id = (device_id or '').strip()
        self.notify_since = 0
        self._store = store

    def _url(self, path: str, query: dict | None = None) -> str:
        if not self.address:
            raise SyncError('还没有填写主机地址')
        params = dict(query or {})
        if self.token:
            params['t'] = self.token
        if self.pair_token:
            params['k'] = self.pair_token
        qs = urllib.parse.urlencode(params)
        return f'{self.address}{path}' + (f'?{qs}' if qs else '')

    def _request(self, path: str, *, query: dict | None = None, body: dict | None = None) -> dict:
        url = self._url(path, query)
        data = None
        headers = {'Accept': 'application/json'}
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode('utf-8')
            headers['Content-Type'] = 'application/json'
        if self.token:
            headers['X-CP-Token'] = self.token
            headers['X-Sync-Token'] = self.token
        req = urllib.request.Request(
            url, data=data,
            headers=headers,
            method='POST' if body is not None else 'GET',
        )
        try:
            with urllib.request.urlopen(req, timeout=self.TIMEOUT_S) as resp:
                raw = resp.read().decode('utf-8', 'replace')
        except urllib.error.HTTPError as exc:
            detail = ''
            try:
                detail = exc.read().decode('utf-8', 'replace')
            except Exception:
                pass
            if exc.code in (401, 403):
                raise SyncError('主机拒绝了这次连接：口令不对，或者这台设备被吊销了') from exc
            raise SyncError(f'主机返回 {exc.code}：{detail[:120]}') from exc
        except urllib.error.URLError as exc:
            raise SyncError(f'连不上主机：{exc.reason}') from exc
        except OSError as exc:
            raise SyncError(f'连不上主机：{exc}') from exc
        try:
            if raw.strip():
                return json.loads(raw)
            return {}
        except json.JSONDecodeError as exc:
            raise SyncError('主机回的不是 JSON，地址可能填错了（指向了别的服务）') from exc

    def sync_once(self) -> SyncResult:
        """推一次、拉一次。推送先做，这样主机合并后的回包里就带着我的记录。"""
        return self.apply(self.collect())

    def collect(self) -> dict:
        """只做网络往返，不碰本地数据。返回一个可以交给主线程的包裹。"""
        settings = self._store.state.settings
        since_pull = max(0, int(getattr(settings, 'sync_pull_seq', 0) or 0))
        since_push = max(0, int(getattr(settings, 'sync_push_seq', 0) or 0))
        pending = oplog.ops_since(self._store.state, since=since_push)
        pushed = self._request('/api/ops', body={'ops': pending})
        pulled = self._request('/api/ops', query={'since': since_pull})
        return {
            'since_pull': since_pull,
            'had_pending': bool(pending),
            'push_seq': oplog.max_seq(self._store.state),
            'pushed': pushed,
            'pulled': pulled,
        }

    def apply(self, plan: dict) -> SyncResult:
        """在主线程落库：合并记录、重算积分、推进两个水位。"""
        result = SyncResult()
        settings = self._store.state.settings
        pushed = plan.get('pushed') or {}
        pulled = plan.get('pulled') or {}
        result.pushed = int(pushed.get('added', 0) or 0)
        if plan.get('had_pending'):
            settings.sync_push_seq = max(
                int(plan.get('push_seq', 0) or 0),
                int(getattr(settings, 'sync_push_seq', 0) or 0),
            )
        result.className = str(pulled.get('className', '') or '')
        result.fingerprint = str(pulled.get('fingerprint', '') or '')
        added, new_students = oplog.apply_remote(
            self._store.state,
            ops=pulled.get('ops') or [],
            students=pulled.get('students') or [],
        )
        result.pulled = added
        result.students_added = new_students
        seq = int(pulled.get('seq', 0) or 0)
        if seq > int(plan.get('since_pull', 0) or 0):
            settings.sync_pull_seq = seq
        return result

    def probe(self) -> dict:
        """只探活，不动数据：用于「测试连接」按钮。"""
        return self._request('/api/ping')

    def notifications(self, since: int | None = None) -> list[dict]:
        """取主机发给这台电脑的通知（只增量，不会重复弹）。"""
        if not self.device_id:
            return []
        if since is None:
            since = self.notify_since
        info = self._request(
            '/api/notifications',
            query={'device': self.device_id, 'since': int(since)},
        )
        items = info.get('items') or []
        last = info.get('lastId')
        if last is not None:
            self.notify_since = max(int(since), int(last))
        elif items:
            self.notify_since = max(int(since), max(int(i.get('id') or 0) for i in items))
        return items

    def ack(self, ids: list[int]) -> int:
        """回执：告诉主机「这台电脑看到了」，主机才好统计谁没看。"""
        if not self.device_id or not ids:
            return 0
        info = self._request(
            '/api/ack',
            body={
                'deviceId': self.device_id,
                'ids': [int(i) for i in ids],
            },
        )
        return int(info.get('acked', 0) or 0)

    def send_notification(self, payload: dict) -> int:
        """把通知交给主机，由主机按 targets 分发（含手机网页与其它电脑）。"""
        body = dict(payload or {})
        body.setdefault('from', self.device_id or 'client')
        if not body.get('file') and body.get('fileName') and body.get('fileDataB64'):
            body['file'] = {
                'name': body.get('fileName') or '附件',
                'dataB64': body.get('fileDataB64') or '',
            }
        body.pop('fileName', None)
        body.pop('fileDataB64', None)
        info = self._request('/api/notify', body=body)
        return int(info.get('id', 0) or 0)

    def fetch_file(self, file_id: str) -> bytes:
        """下载通知附件（走主机的 /api/file/<id>）。"""
        if not file_id:
            return b''
        url = self._url(f'/api/file/{urllib.parse.quote(str(file_id))}')
        headers = {'User-Agent': 'ClassPoints/client'}
        if self.token:
            headers['X-CP-Token'] = self.token
            headers['X-Sync-Token'] = self.token
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.TIMEOUT_S) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            raise SyncError(f'附件下载失败（HTTP {exc.code}）') from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise SyncError(f'附件下载失败：{exc}') from exc
