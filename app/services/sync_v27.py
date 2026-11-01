"""v2.7 多端互联的 HTTP 路由：配对 / 设备管理 / 导出 / 通知 / 附件。

单独一个模块的原因：``sync_server._Handler`` 已经够长了（880 行），
再加十来个端点会彻底读不动。这里用 mixin 挂进去，只依赖 handler 上的
``_respond`` / ``_read_body`` / ``_token_ok`` / ``_write_allowed``。

协议见 ``docs/sync-protocol-v27.md``，改协议先改那份文档。
"""
from __future__ import annotations

import base64
import binascii
import json
import os
import secrets
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

MAX_NOTIFY = 200
MAX_FILE_BYTES = 8 * 1024 * 1024

JSON = 'application/json'


@dataclass
class NotifyItem:
    id: int
    at: float
    sender: str
    targets: list[str]
    mode: str
    title: str
    text: str
    file_name: str = ''
    file_id: str = ''
    acked: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            'id': self.id,
            'at': self.at,
            'from': self.sender,
            'targets': list(self.targets),
            'mode': self.mode,
            'title': self.title,
            'text': self.text,
            'fileName': self.file_name,
            'fileId': self.file_id,
            'acked': list(self.acked),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> 'NotifyItem':
        return cls(
            id=int(raw.get('id') or 0),
            at=float(raw.get('at') or 0.0),
            sender=str(raw.get('from') or ''),
            targets=[str(x) for x in (raw.get('targets') or ['*'])],
            mode=str(raw.get('mode') or 'mild'),
            title=str(raw.get('title') or '')[:120],
            text=str(raw.get('text') or '')[:2000],
            file_name=str(raw.get('fileName') or '')[:120],
            file_id=str(raw.get('fileId') or ''),
            acked=[str(x) for x in (raw.get('acked') or [])],
        )


class NotifyStore:
    """通知与附件的落盘存储（线程安全）。"""

    def __init__(self, root: str | Path | None = None) -> None:
        self._root = Path(root) if root else None
        self._lock = threading.RLock()
        self._items = []
        self._next_id = 1
        self._load()

    @property
    def root(self) -> Path:
        if self._root is None:
            from app.core import paths

            self._root = Path(paths.current_data_dir())
        return self._root

    @property
    def json_path(self) -> Path:
        return self.root / 'notifications.json'

    @property
    def file_dir(self) -> Path:
        return self.root / 'notify_files'

    def _load(self) -> None:
        try:
            raw = json.loads(self.json_path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return
        items = raw.get('items') if isinstance(raw, dict) else None
        if isinstance(items, list):
            for it in items:
                if not isinstance(it, dict):
                    continue
                if not it.get('id'):
                    continue
                self._items.append(NotifyItem.from_dict(it))
        self._items.sort(key=lambda x: x.id)
        if self._items:
            self._next_id = self._items[-1].id + 1

    def _save(self) -> None:
        try:
            self.json_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.json_path.with_name(self.json_path.name + '.tmp')
            tmp.write_text(
                json.dumps(
                    {'version': 1, 'items': [i.to_dict() for i in self._items]},
                    ensure_ascii=False,
                ),
                encoding='utf-8',
            )
            os.replace(tmp, self.json_path)
        except OSError:
            return

    def add(
        self,
        *,
        sender: str,
        targets: list[str],
        mode: str,
        title: str,
        text: str,
        file_name: str = '',
        file_bytes: bytes | None = None,
    ) -> NotifyItem:
        with self._lock:
            file_id = ''
            if file_bytes:
                file_id = secrets.token_hex(8)
                self.file_dir.mkdir(parents=True, exist_ok=True)
                safe_name = Path(file_name or '附件').name or '附件'
                try:
                    (self.file_dir / f'{file_id}__{safe_name}').write_bytes(file_bytes)
                except OSError:
                    file_id = ''
            item = NotifyItem(
                id=self._next_id,
                at=time.time(),
                sender=sender,
                targets=[t or '*' for t in targets] or ['*'],
                mode=mode if mode in ('mild', 'severe', 'urgent') else 'mild',
                title=title, text=text,
                file_name=file_name if file_id else '',
                file_id=file_id,
            )
            self._next_id += 1
            self._items.append(item)
            if len(self._items) > MAX_NOTIFY:
                for dead in self._items[:-MAX_NOTIFY]:
                    self._drop_file(dead)
                self._items = self._items[-MAX_NOTIFY:]
            self._save()
            return item

    def _drop_file(self, item: NotifyItem) -> None:
        if not item.file_id:
            return
        d = self.file_dir
        try:
            for p in d.glob(f'{item.file_id}__*'):
                p.unlink(missing_ok=True)
        except OSError:
            return

    def pending(self, device_id: str, since: int = 0) -> list[NotifyItem]:
        with self._lock:
            out = []
            for it in self._items:
                if it.id <= since:
                    continue
                if it.sender == device_id:
                    continue
                if '*' not in it.targets and device_id not in it.targets:
                    continue
                if device_id in it.acked:
                    continue
                out.append(it)
            return out

    def ack(self, device_id: str, ids: list[int]) -> int:
        with self._lock:
            n = 0
            for it in self._items:
                if it.id in ids:
                    if device_id not in it.acked:
                        it.acked.append(device_id)
                        n += 1
            if n:
                self._save()
            return n

    def find_file(self, file_id: str) -> tuple[Path, str] | None:
        if not file_id:
            return None
        d = self.file_dir
        try:
            for p in d.glob(f'{file_id}__*'):
                if not p.is_file():
                    continue
                return (p, p.name.split('__', 1)[-1])
            return None
        except OSError:
            return None

    def as_list(self) -> list[dict[str, Any]]:
        with self._lock:
            return [i.to_dict() for i in self._items]


class V27Mixin:
    """挂在 ``_Handler`` 上，处理 `/api/whoami|pair|devices|export|notify…`。

    返回 True 表示已经回过包，调用方直接 return。
    """

    def _q(self) -> dict[str, list[str]]:
        if '?' not in self.path:
            return {}
        return parse_qs(self.path.split('?', 1)[1])

    def _device_id(self) -> str:
        did = (self.headers.get('X-CP-Device') or '').strip()
        if not did:
            qs = self._q()
            if qs.get('device'):
                did = qs['device'][0].strip()
        return did[:64]

    def _settings(self):
        st = getattr(self._store_obj(), 'state', None)
        return getattr(st, 'settings', None)

    def _master_only(self) -> bool:
        s = self._settings()
        return bool(getattr(s, 'sync_master_only', False))

    def _is_host_device(self) -> bool:
        """主机的判定：请求方 device 为空或等于本机 device_id 就算主机。"""
        did = self._device_id()
        if not did:
            return True
        s = self._settings()
        host = getattr(s, 'device_id', '') or ''
        return bool(host) and did == host

    def _reject_if_revoked(self) -> bool:
        """已吊销的设备，任何请求都 403。"""
        did = self._device_id()
        if not did:
            return False
        reg = getattr(self.server, 'devices', None)
        if reg is not None and reg.is_revoked(did):
            self._fail(403, 'device_revoked', '这台设备已被主机端吊销，请重新配对')
            return True
        return False

    def _store_obj(self):
        return getattr(self.server, 'store', None)

    def _notify_store(self) -> NotifyStore:
        ns = getattr(self.server, 'notifications', None)
        if ns is None:
            ns = NotifyStore()
            self.server.notifications = ns
        return ns

    def _devices(self):
        from app.core.devices import DeviceRegistry

        reg = getattr(self.server, 'devices', None)
        if reg is None:
            reg = DeviceRegistry.load()
            self.server.devices = reg
        return reg

    def _fingerprint(self) -> str:
        from app.core import fingerprint

        st = getattr(self._store_obj(), 'state', None)
        if st is None:
            return ''
        return fingerprint.class_fingerprint(st)

    def route_v27_get(self, path: str) -> bool:
        store_obj = self._store_obj()
        if store_obj is None:
            return False

        if path == '/api/whoami':
            reg = self._devices()
            body = {
                'ok': True,
                'className': getattr(store_obj.state, 'class_name', ''),
                'fingerprint': self._fingerprint(),
                'deviceCount': len(reg.devices),
                'auth': self._token_ok(),
            }
            self._respond(200, _j(body))
            return True

        if self._reject_if_revoked():
            return True
        if not self._token_ok():
            self._fail(401, 'unauthorized', '写操作需要口令：请用 ?t=口令 或 X-Sync-Token 请求头')
            return True

        if path == '/api/devices':
            self._respond(200, _j({'ok': True, 'devices': self._devices().as_list()}))
            return True

        if path == '/api/ops':
            from app.core import oplog

            st = store_obj.state
            qs = self._q()
            try:
                since = int((qs.get('since') or ['0'])[0] or 0)
            except (TypeError, ValueError):
                since = 0
            try:
                limit = int((qs.get('limit') or ['0'])[0] or 0)
            except (TypeError, ValueError):
                limit = 0
            reg = self._devices()
            did = self._device_id()
            if did:
                reg.touch(did)
            self._respond(
                200,
                _j(
                    {
                        'ok': True,
                        'className': getattr(st, 'class_name', ''),
                        'fingerprint': self._fingerprint(),
                        'seq': oplog.max_seq(st),
                        'ops': oplog.ops_since(st, since, limit),
                        'students': [s.to_dict() for s in st.students],
                    }
                ),
            )
            return True

        if path == '/api/export':
            qs = self._q()
            media = (qs.get('media', ['0'])[0] or '0') in ('1', 'true', 'yes')
            state = store_obj.state
            try:
                payload = state.to_dict()
            except (TypeError, ValueError) as exc:
                self._respond(500, _j({'error': f'serialize failed: {exc}'}))
                return True
            if not media:
                payload.pop('background', None)
                settings = payload.get('settings')
                if isinstance(settings, dict):
                    settings.pop('appIcon', None)
                    settings.pop('launchAnimation', None)
            body = {
                'ok': True,
                'fingerprint': self._fingerprint(),
                'media': bool(media),
                'exportedAt': time.time(),
                'state': payload,
            }
            self._respond(200, _j(body))
            return True

        if path == '/api/notifications':
            qs = self._q()
            dev = self._device_id()
            if not dev:
                self._respond(400, _j({'error': 'device required'}))
                return True
            try:
                since = int(qs.get('since', ['0'])[0] or '0')
            except ValueError:
                since = 0
            reg = self._devices()
            reg.touch(dev)
            items = self._notify_store().pending(dev, since)
            self._respond(
                200,
                _j(
                    {
                        'ok': True,
                        'device': dev,
                        'items': [i.to_dict() for i in items],
                        'lastId': items[-1].id if items else since,
                    }
                ),
            )
            return True

        if path.startswith('/api/file/'):
            file_id = path.rsplit('/', 1)[-1]
            found = self._notify_store().find_file(file_id)
            if found is None:
                self._respond(404, b'{"error":"file not found"}')
                return True
            fp, name = found
            try:
                blob = fp.read_bytes()
            except OSError:
                self._respond(500, b'{"error":"read failed"}')
                return True
            self._respond(200, blob, 'application/octet-stream')
            return True

        return False

    def _body_or_fail(self) -> dict | None:
        """取 JSON 请求体；取不到就当场回错并返回 None（调用方 return True）。

        以前各处写 `self._read_body() or {}`：请求体超过上限时 _read_body 返回 None，
        于是拿空 dict 继续执行 —— 主机回 200、什么都没存，客户端还推进了同步水位，
        那批记录就永久消失了。这里必须硬失败。
        """
        body = self._read_body()
        if body is None:
            if getattr(self, '_body_too_large', False):
                self._fail(
                    413,
                    'too_large',
                    '请求内容太大，主机没有接收（手机上请分批提交，发视频请用「导入视频」而不是通知附件）。',
                )
            else:
                self._fail(400, 'bad_json', '请求体必须是 JSON 对象')
            return None
        if self._device_revoked(body):
            self._fail(403, 'device_revoked', '这台设备已被主机端吊销，请重新配对')
            return None
        return body

    def route_v27_post(self, path: str) -> bool:
        store_obj = self._store_obj()
        if store_obj is None:
            return False

        if path == '/api/pair':
            body = self._body_or_fail()
            if body is None:
                return True
            if not self._write_allowed():
                if not getattr(self.server, 'token', ''):
                    pass
                else:
                    self._fail(401, 'unauthorized', '写操作需要口令：请用 ?t=口令 或 X-Sync-Token 请求头')
                    return True
            did = str(body.get('deviceId') or '')[:64]
            if not did:
                self._respond(400, _j({'error': 'deviceId required'}))
                return True
            reg = self._devices()
            if reg.is_revoked(did):
                self._fail(403, 'device_revoked', '这台设备已被主机端吊销，请重新配对')
                return True
            fp = self._fingerprint()
            reg.upsert(
                did,
                name=str(body.get('deviceName') or '')[:40],
                kind=str(body.get('kind') or 'phone'),
                fingerprint=fp,
            )
            self._respond(
                200,
                _j(
                    {
                        'ok': True,
                        'fingerprint': fp,
                        'className': getattr(store_obj.state, 'class_name', ''),
                        'deviceId': did,
                        'devices': len(reg.devices),
                    }
                ),
            )
            return True

        if self._reject_if_revoked():
            return True
        if not self._write_allowed():
            self._drain_body()
            self._fail(401, 'unauthorized', '写操作需要口令：请用 ?t=口令 或 X-Sync-Token 请求头')
            return True
        if self._master_only() and not self._is_host_device():
            self._drain_body()
            self._fail(403, 'read_only_slave', '主机端开启了「仅主机可改数据」，这台设备只能查看')
            return True

        if path == '/api/ops':
            from app.core import oplog
            from app.core.state import ChangeEvent

            body = self._body_or_fail()
            if body is None:
                return True
            st = store_obj.state
            did = self._device_id() or str(body.get('deviceId') or '')[:64]
            added_ops, added_students = oplog.apply_remote(
                st, body.get('ops') or [], body.get('students') or []
            )
            seq = oplog.max_seq(st)
            if added_ops or added_students:
                store_obj.emit(ChangeEvent(reason='sync_merge', scope='all'))
                store_obj.save_now()
            reg = self._devices()
            if did:
                reg.upsert(
                    did,
                    name=str(body.get('deviceName') or '')[:40],
                    kind=str(body.get('kind') or 'phone'),
                    fingerprint=self._fingerprint(),
                )
            try:
                since = int(body.get('since') or 0)
            except (TypeError, ValueError):
                since = 0
            self._respond(
                200,
                _j(
                    {
                        'ok': True,
                        'added': added_ops,
                        'addedStudents': added_students,
                        'seq': seq,
                        'fingerprint': self._fingerprint(),
                        'ops': oplog.ops_since(st, since),
                        'students': [s.to_dict() for s in st.students],
                    }
                ),
            )
            return True

        if path in ('/api/device_rename', '/api/device_revoke', '/api/device_forget'):
            body = self._body_or_fail()
            if body is None:
                return True
            did = str(body.get('deviceId') or '')[:64]
            if not did:
                self._respond(400, _j({'error': 'deviceId required'}))
                return True
            reg = self._devices()
            if path == '/api/device_rename':
                ok = reg.rename(did, str(body.get('name') or ''))
            elif path == '/api/device_revoke':
                ok = reg.revoke(did, bool(body.get('revoked', True)))
            else:
                ok = reg.remove(did)
            self._respond(200 if ok else 404, _j({'ok': ok, 'devices': reg.as_list()}))
            return True

        if path == '/api/notify':
            body = self._body_or_fail()
            if body is None:
                return True
            sender = str(body.get('from') or self._device_id() or 'host')[:64]
            targets = body.get('targets') or ['*']
            if isinstance(targets, str):
                targets = [targets]
            targets = [str(t)[:64] for t in targets][:64] or ['*']
            mode = str(body.get('mode') or 'mild')
            file_info = body.get('file') if isinstance(body.get('file'), dict) else None
            file_bytes = None
            file_name = ''
            if file_info:
                file_name = str(file_info.get('name') or '附件')[:120]
                raw_b64 = file_info.get('dataB64') or ''
                if raw_b64:
                    try:
                        file_bytes = base64.b64decode(raw_b64, validate=False)
                    except (binascii.Error, ValueError):
                        self._respond(400, b'{"error":"bad base64"}')
                        return True
                    if len(file_bytes) > MAX_FILE_BYTES:
                        self._respond(
                            413,
                            _j({'error': 'file too large', 'maxBytes': MAX_FILE_BYTES}),
                        )
                        return True
            item = self._notify_store().add(
                sender=sender,
                targets=targets,
                mode=mode,
                title=str(body.get('title') or '')[:120],
                text=str(body.get('text') or '')[:2000],
                file_name=file_name,
                file_bytes=file_bytes,
            )
            hook = getattr(self.server, 'on_notify', None)
            if callable(hook):
                try:
                    hook(item.to_dict())
                except Exception:
                    pass
            self._respond(200, _j({'ok': True, 'id': item.id}))
            return True

        if path == '/api/ack':
            body = self._body_or_fail()
            if body is None:
                return True
            dev = str(body.get('deviceId') or self._device_id())[:64]
            ids = body.get('ids') or []
            try:
                ids = [int(x) for x in ids][:200]
            except (TypeError, ValueError):
                ids = []
            if not dev:
                self._respond(400, b'{"error":"deviceId required"}')
                return True
            n = self._notify_store().ack(dev, ids)
            self._respond(200, _j({'ok': True, 'acked': n}))
            return True

        return False


def _j(obj: Any) -> bytes:
    return json.dumps(obj, ensure_ascii=False, separators=(',', ':')).encode('utf-8')


__all__ = ['V27Mixin', 'NotifyStore', 'NotifyItem', 'MAX_FILE_BYTES']
