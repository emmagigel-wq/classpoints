"""局域网同步服务端：HTTP REST（GET /api/state /api/students /api/leaderboard）。

同一台教师机广播 JSON；查看端（手机/浏览器）连同一 WiFi 即可拉取最新积分。
用标准库 http.server，无第三方依赖。

v1.2 起手机端不再只读：新增 **POST /api/score、/api/undo、/api/student_add、
/api/student_remove**，与桌面端同权。写操作**一律要求口令**（服务端没配口令
也拒绝，避免同步误开时被人匿名改分）；读接口行为保持不变。

线程模型：请求在后台线程里处理，只碰 app/core 的纯逻辑，绝不碰任何 Qt 对象。
改完状态后用 ChangeEvent 走 Store.emit 通知界面，再 save_now() 立即落盘。
Qt 界面应当用 ``SyncServer.set_on_change()`` 把这个通知转投主线程（见该方法说明）。
"""
from __future__ import annotations

import hmac
import json
import logging
import os
import secrets
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Callable

from app.core import scoring
from app.core.models import as_int
from app.core.state import ChangeEvent
from app.services.sync_v27 import NotifyStore, V27Mixin

logger = logging.getLogger(__name__)

TOKEN_ALPHABET = 'abcdefghijkmnpqrstuvwxyz23456789'

_WRITE_LOCK = threading.RLock()

MAX_BODY_BYTES = 65536


def new_token(length: int = 8) -> str:
    """生成一个 8 位口令（纯小写+数字，方便老师在手机上口述）。"""
    return ''.join(secrets.choice(TOKEN_ALPHABET) for _ in range(length))


def token_path() -> Path:
    """口令存到程序数据目录，和 data.json 放一起。"""
    from app.core.storage import app_data_dir

    return app_data_dir() / 'sync_token.txt'


def load_or_create_token() -> str:
    """读上次的口令；没有就生成一个并存盘（跨重启保持不变）。"""
    path = token_path()
    try:
        saved = path.read_text(encoding='utf-8').strip()
        if saved:
            return saved
    except OSError:
        pass
    token = new_token()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(token, encoding='utf-8')
    except OSError:
        logger.warning('口令没法落盘，本次重启后需要重新配对')
    return token


def save_token(token: str) -> None:
    path = token_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(token, encoding='utf-8')
    except OSError:
        logger.warning('口令没法落盘')


def _json_bytes(obj) -> bytes:
    return json.dumps(obj, ensure_ascii=False, separators=(',', ':')).encode('utf-8')


OP_LOG_KEEP = 2000
OP_LOG_TTL = 604800
OP_ID_MAX = 128


def op_log_path() -> Path:
    from app.core.storage import app_data_dir

    return app_data_dir() / 'sync_ops.json'


def _valid_op_id(value) -> str:
    """opId 只接受 1~128 位字母/数字/-/_/.，避免写进 json 的键里出怪东西。"""
    if not isinstance(value, str):
        return ''
    value = value.strip()
    if not value or len(value) > OP_ID_MAX:
        return ''
    if not all(ch.isalnum() or ch in '-_.' for ch in value):
        return ''
    return value


class OpLog:
    """已处理 opId → 结果摘要 的持久集合（线程安全、原子落盘、坏文件不致命）。"""

    def __init__(self, path: Path | None = None) -> None:
        self._explicit = path
        self._path = Path(path) if path is not None else None
        self._ops = {}
        self._loaded = False
        self._lock = threading.RLock()
        self.last_error = ''

    @property
    def path(self) -> Path:
        if self._path is None:
            self._path = op_log_path()
        return self._path

    def _persistence_allowed(self) -> bool:
        """测试环境没指定数据目录时只留在内存，别污染老师的真实目录。"""
        if self._explicit is not None:
            return True
        if os.environ.get('CLASSPOINTS_DATA_DIR') or os.environ.get('CLASSPOINTS_PROBE'):
            return True
        import sys

        return 'pytest' not in sys.modules and not os.environ.get('PYTEST_CURRENT_TEST')

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        if not self._persistence_allowed():
            return
        try:
            raw = json.loads(self.path.read_text(encoding='utf-8'))
        except FileNotFoundError:
            return
        except (OSError, ValueError):
            logger.warning('sync_ops.json 读不出来，本次从空日志开始', exc_info=True)
            return
        ops = raw.get('ops') if isinstance(raw, dict) else None
        if not isinstance(ops, dict):
            return
        now = time.time()
        for key, value in ops.items():
            if not isinstance(value, dict):
                continue
            try:
                ts = float(value.get('ts') or 0)
            except (TypeError, ValueError):
                continue
            if now - ts <= OP_LOG_TTL:
                self._ops[str(key)] = value

    def get(self, op_id: str) -> dict | None:
        with self._lock:
            self._ensure_loaded()
            record = self._ops.get(op_id)
            if record is not None:
                try:
                    expired = time.time() - float(record.get('ts') or 0) > OP_LOG_TTL
                except (TypeError, ValueError):
                    expired = True
                if expired:
                    self._ops.pop(op_id, None)
                    return None
            return dict(record) if record is not None else None

    def record(self, op_id: str, route: str, result: dict) -> None:
        with self._lock:
            self._ensure_loaded()
            self._ops[op_id] = {
                'ts': int(time.time()),
                'route': route,
                'result': result,
            }
            self._prune_locked()
            self._save_locked()

    def clear(self) -> None:
        with self._lock:
            self._ops.clear()
            self._loaded = True
            self._save_locked()

    @property
    def count(self) -> int:
        with self._lock:
            self._ensure_loaded()
            return len(self._ops)

    def _prune_locked(self) -> None:
        now = time.time()
        expired = []
        for key, value in self._ops.items():
            try:
                if now - float(value.get('ts') or 0) > OP_LOG_TTL:
                    expired.append(key)
            except (TypeError, ValueError):
                expired.append(key)
        for key in expired:
            self._ops.pop(key, None)
        if len(self._ops) > OP_LOG_KEEP:
            ordered = sorted(self._ops.items(), key=lambda item: float(item[1].get('ts') or 0))
            for key, _ in ordered[:len(self._ops) - OP_LOG_KEEP]:
                self._ops.pop(key, None)

    def _save_locked(self) -> None:
        if not self._persistence_allowed():
            return
        path = self.path
        tmp = path.with_name(f'{path.name}.{os.getpid()}.{threading.get_ident()}.tmp')
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(tmp, 'w', encoding='utf-8') as fh:
                json.dump({'version': 1, 'ops': self._ops}, fh, ensure_ascii=False, separators=(',', ':'))
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, path)
        except OSError as exc:
            self.last_error = f'已处理记录没法落盘：{exc}'
            logger.warning('sync_ops.json 落盘失败（本次仍会在内存里去重）：%s', exc)
            try:
                tmp.unlink()
            except OSError:
                pass


def _recent_history(state, limit: int = 100) -> list[dict]:
    """最近的操作记录，手机端离线撤销靠它算 delta 和 expect_seq。"""
    history = list(getattr(state, 'history', None) or [])
    history.sort(key=lambda h: h.seq)
    return [
        {
            'seq': h.seq,
            'student_id': h.student_id,
            'student_name': h.student_name,
            'delta': h.delta,
            'reason': h.reason,
            'source': h.source,
            'ts': h.ts,
        }
        for h in history[-limit:]
    ]


def _last_entry_for(state, student_id: str):
    """该生当前最新的一条记录（按 seq，和 scoring.undo_last_for 同口径）。"""
    best = None
    for h in getattr(state, 'history', None) or []:
        if h.student_id != student_id:
            continue
        if best is None or h.seq > best.seq:
            best = h
    return best


def _leaderboard(state, limit: int | None = None) -> list[dict]:
    rows = sorted(state.students, key=lambda s: (-s.points, s.name))
    if limit:
        rows = rows[:limit]
    return [
        {
            'rank': i + 1,
            'name': s.name,
            'points': s.points,
            'seat': f'{s.row + 1}排{s.col + 1}座',
        }
        for i, s in enumerate(rows)
    ]


def _student_payload(student) -> dict:
    return {
        'id': student.id,
        'name': student.name,
        'points': student.points,
        'row': student.row,
        'col': student.col,
    }


def _state_payload(store) -> dict:
    return {
        'version': 1,
        'className': getattr(store.state, 'class_name', None)
        or getattr(store.state.settings, 'class_name', None)
        or '班级',
        'layout': store.state.layout.to_dict(),
        'students': [_student_payload(s) for s in store.state.students],
        'leaderboard': _leaderboard(store.state),
        'recent': _recent_history(store.state),
        'ts': int(time.time()),
    }


def _viewer_path():
    """手机端页面在开发态/打包态下的真实位置。"""
    from pathlib import Path
    import sys

    base = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[2]))
    for cand in (base / 'android' / 'viewer.html', Path(__file__).resolve().parents[2] / 'android' / 'viewer.html'):
        if cand.exists():
            return cand
    return None


def _viewer_bytes() -> bytes | None:
    path = _viewer_path()
    if path is None:
        return None
    try:
        return path.read_bytes()
    except OSError:
        return None


class _SyncHTTPServer(HTTPServer):
    """不共享端口的 HTTP 服务。

    标准库 HTTPServer 默认 allow_reuse_address=True，在 Windows 上这会让第二个
    实例也能绑上同一个 8765，于是手机可能落到那个旧实例上（旧数据、旧口令），
    用户看到的就是「有时能开、有时打不开 / 口令不对」。这里显式关掉复用。
    """

    allow_reuse_address = False


class _Handler(V27Mixin, BaseHTTPRequestHandler):
    server_version = 'ClassPointsSync/1.2'

    _body_too_large = False

    def log_message(self, fmt, *args):
        logger.debug('sync %s', fmt % args)
        return None

    def _pair_ok(self, stored: str) -> bool:
        """请求里的 ?k= 是否等于本班的配对令牌。

        只用于回报「这台手机是不是扫的我们班的码」，**不作为写权限门禁**：
        真正的权限仍然是口令（?t= / X-CP-Token）。令牌只管认门牌，不管发钥匙。
        """
        if not stored:
            return False
        from urllib.parse import parse_qs

        query = self.path.split('?', 1)[1] if '?' in self.path else ''
        try:
            given = (parse_qs(query).get('k') or [''])[0]
        except ValueError:
            return False
        return given.strip() == stored

    def _token_ok(self) -> bool:
        """口令校验：URL 上的 ?t= / ?token=，或请求头 X-Sync-Token。

        没设口令时一律放行（只在本机自用/测试时才会这样）。
        """
        expected = getattr(self.server, 'token', '') or ''
        if not expected:
            return True
        from urllib.parse import parse_qs, unquote

        got = self.headers.get('X-CP-Token', '') or self.headers.get('X-Sync-Token', '') or ''
        if not got and '?' in self.path:
            qs = parse_qs(self.path.split('?', 1)[1])
            for key in ('t', 'token'):
                if key in qs and qs[key]:
                    got = qs[key][0]
                    break
        return hmac.compare_digest(unquote(got).strip(), expected)

    def _respond(self, code: int, body: bytes, content_type: str = 'application/json'):
        self.send_response(code)
        self.send_header('Content-Type', content_type + '; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Cache-Control', 'no-cache')
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        store = getattr(self.server, 'store', None)
        if store is None:
            self._respond(503, b'{"error":"no store"}')
            return None
        path = self.path.split('?', 1)[0].rstrip('/')
        if path in ('', '/index.html', '/viewer.html'):
            page = _viewer_bytes()
            if page is None:
                self._respond(404, b'{"error":"viewer.html not found"}')
                return None
            self._respond(200, page, 'text/html')
            return None
        if path == '/api/ping':
            ok = self._token_ok()
            info = {'ok': True, 'auth': ok}
            if ok:
                settings = getattr(getattr(store, 'state', None), 'settings', None)
                stored = (getattr(settings, 'pair_token', '') or '').strip()
                info['pairToken'] = stored
                info['paired'] = bool(stored) and self._pair_ok(stored)
            body = json.dumps(info, ensure_ascii=False).encode('utf-8')
            self._respond(200, body)
            return None
        if self.route_v27_get(path):
            return None
        if not self._token_ok():
            self._respond(401, b'{"error":"unauthorized"}')
            return None
        if path == '/api/state':
            self._respond(200, _json_bytes(_state_payload(store)))
            return None
        if path == '/api/students':
            data = [
                {
                    'id': s.id,
                    'name': s.name,
                    'points': s.points,
                    'row': s.row,
                    'col': s.col,
                }
                for s in store.state.students
            ]
            self._respond(200, _json_bytes(data))
            return None
        if path.startswith('/api/leaderboard'):
            limit = None
            if '?' in self.path:
                qs = self.path.split('?', 1)[1]
                for part in qs.split('&'):
                    k, _, v = part.partition('=')
                    if k == 'limit':
                        try:
                            limit = max(1, int(v))
                        except ValueError:
                            continue
            self._respond(200, _json_bytes(_leaderboard(store.state, limit)))
            return None
        self._respond(404, b'{"error":"not found"}')
        return None

    def do_OPTIONS(self):
        """跨域预检：手机页面 POST JSON 时会先发 OPTIONS。"""
        self.send_response(204)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, X-Sync-Token')
        self.send_header('Access-Control-Max-Age', '600')
        self.send_header('Content-Length', '0')
        self.end_headers()
        return None

    def _write_allowed(self) -> bool:
        """写操作的口令校验：必须**真的配了口令**且请求带上正确口令。"""
        expected = getattr(self.server, 'token', '') or ''
        if not expected:
            return False
        return self._token_ok()

    def _device_revoked(self, body: dict) -> bool:
        """请求方设备若已被吊销，返回 True（用于所有写接口的统一门禁）。"""
        did = str(
            body.get('deviceId')
            or body.get('device_id')
            or body.get('from')
            or self.headers.get('X-CP-Device')
            or ''
        ).strip()
        if not did:
            return False
        try:
            from app.core.devices import DeviceRegistry

            return DeviceRegistry.load().is_revoked(did)
        except Exception:
            return False

    def _drain_body(self) -> None:
        """把请求体读掉丢弃。

        拒绝请求（401/404/格式错）时如果直接回包就关连接，客户端可能还在
        发 body，会收到 connection reset 而不是我们写的 JSON —— 手机端就会
        变成「网络不通」的误报。所以错误路径也要先把 body 消费干净。
        """
        try:
            length = int(self.headers.get('Content-Length') or 0)
        except (TypeError, ValueError):
            return None
        if length <= 0:
            return None
        want = min(length, MAX_BODY_BYTES)
        try:
            self.rfile.read(want)
        except OSError:
            return None
        if length > MAX_BODY_BYTES:
            self.close_connection = True
            return None
        return None

    def _read_body(self) -> dict | None:
        """读 JSON 请求体；格式不对/超长/不是对象一律返回 None。

        超长时把 ``_body_too_large`` 置真，让调用方回 413 而不是笼统的
        「请求体必须是 JSON 对象」—— 后者会让人以为是自己发错了格式。
        """
        self._body_too_large = False
        try:
            length = int(self.headers.get('Content-Length') or 0)
        except (TypeError, ValueError):
            return None
        if length <= 0:
            return None
        if length > MAX_BODY_BYTES:
            self._body_too_large = True
            self._drain_body()
            return None
        try:
            raw = self.rfile.read(length)
            data = json.loads(raw.decode('utf-8'))
        except (OSError, UnicodeDecodeError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    def _fail(self, code: int, error: str, message: str = '') -> None:
        self._respond(code, _json_bytes({'ok': False, 'error': error, 'message': message or error}))

    def _commit(self, store, event: ChangeEvent) -> tuple[bool, str]:
        """写完状态后的「通知 + 落盘」。

        通知走 Store 现有的 ChangeEvent 语义（和桌面端同一套监听器），
        落盘走 Store.save_now() —— 手机可能马上就关页面，不能等 20 秒自动保存。
        返回 (是否保存成功, 给用户看的文案)。
        """
        notifier = getattr(self.server, 'on_change', None)
        try:
            if callable(notifier):
                notifier(event)
            else:
                store.emit(event)
        except Exception:
            logger.exception('sync 写操作：通知界面失败')
        try:
            result = store.save_now()
            return (bool(result.ok), result.message)
        except Exception as exc:
            logger.exception('sync 写操作：落盘失败')
            return (False, str(exc))

    @staticmethod
    def _err(code: int, error: str, message: str = '') -> tuple[int, dict]:
        return (code, {'ok': False, 'error': error, 'message': message or error})

    def _ok_payload(self, payload: dict) -> dict:
        """统一成功响应：{"ok": true, ..., "saved": bool}。

        saved=False 时额外带 message，手机端据此提示「已改分但没存上」。
        处理函数只返回数据，真正的响应由 do_POST 发（那里要记 opId）。
        """
        saved = bool(payload.pop('_saved', True))
        message = payload.pop('_message', '')
        body = {'ok': True, **payload, 'saved': saved}
        if not saved:
            body['message'] = message or '已改动，但保存到磁盘失败'
        return body

    @staticmethod
    def _op_summary(route: str, payload: dict) -> dict:
        """存进 opId 日志的结果摘要（只留重放时回话需要的字段）。"""
        if route in ('/api/score', '/api/undo'):
            return {
                'student_id': payload.get('student_id', ''),
                'name': payload.get('name', ''),
                'delta': payload.get('delta', 0),
                'seq': payload.get('seq', 0),
            }
        if route == '/api/student_add':
            return {'student': payload.get('student') or {}}
        if route == '/api/student_remove':
            return {
                'student_id': payload.get('student_id', ''),
                'name': payload.get('name', ''),
            }
        return dict(payload)

    @staticmethod
    def _duplicate_payload(store, cached: dict) -> dict:
        """重复 opId 的响应：告诉手机端「早就处理过」，并附上当前真实状态。

        关键点：这里绝不再次执行操作，只读当前 state —— 所以同一 opId 重放
        多少次，积分都只变一次；同时手机端拿到的 points 是最新值，可以直接
        用它校正本地缓存。
        """
        route = cached.get('route', '')
        result = cached.get('result') or {}
        base = {
            'ok': True,
            'duplicate': True,
            'saved': True,
            'message': '这条操作之前已经同步过了',
        }
        if route in ('/api/score', '/api/undo'):
            sid = str(result.get('student_id') or '')
            student = store.state.student_by_id(sid) if sid else None
            if student is None:
                return {
                    **base,
                    'student_id': sid,
                    'name': result.get('name', ''),
                    'delta': result.get('delta', 0),
                    'points': None,
                    'missing': True,
                }
            return {
                **base,
                'student_id': sid,
                'name': student.name,
                'delta': result.get('delta', 0),
                'points': student.points,
            }
        if route == '/api/student_add':
            saved_student = result.get('student') or {}
            sid = str(saved_student.get('id') or '')
            student = store.state.student_by_id(sid) if sid else None
            return {
                **base,
                'student': _student_payload(student) if student else saved_student,
                'count': len(store.state.students),
                'removed': student is None,
            }
        if route == '/api/student_remove':
            return {
                **base,
                'student_id': result.get('student_id', ''),
                'name': result.get('name', ''),
                'count': len(store.state.students),
            }
        return base

    def _post_score(self, store, body: dict) -> tuple[int, dict]:
        """POST /api/score：给某学生加/扣分。幂等键：body.opId。"""
        sid = str(body.get('student_id') or '').strip()
        if not sid:
            return self._err(400, 'missing_student_id', '缺少 student_id')
        raw_delta = body.get('delta')
        if isinstance(raw_delta, bool):
            return self._err(400, 'bad_delta', 'delta 必须是整数')
        try:
            delta = int(raw_delta)
        except (TypeError, ValueError):
            return self._err(400, 'bad_delta', 'delta 必须是整数')
        if delta == 0:
            return self._err(400, 'bad_delta', 'delta 不能是 0')
        reason = str(body.get('reason') or '').strip()[:200]
        if not reason:
            reason = '手机加分' if delta > 0 else '手机扣分'
        with _WRITE_LOCK:
            student = store.state.student_by_id(sid)
            if student is None:
                return self._err(404, 'student_not_found', '没有找到这位同学')
            entry = scoring.add_points(store.state, sid, delta, reason=reason, source='manual')
            if entry is None:
                return self._err(400, 'not_applied', '没有写入记录')
            saved, msg = self._commit(
                store,
                ChangeEvent(reason='mobile', scope='seat', student_ids=[sid], extra={'source': 'mobile'}),
            )
        return (
            200,
            self._ok_payload(
                {
                    'student_id': sid,
                    'name': student.name,
                    'delta': entry.delta,
                    'points': student.points,
                    'seq': entry.seq,
                    '_saved': saved,
                    '_message': msg,
                }
            ),
        )

    def _post_undo(self, store, body: dict) -> tuple[int, dict]:
        """POST /api/undo：撤销某学生最近一条记录（按 seq 倒序，和桌面端一致）。

        手机端离线撤销会带 expect_seq（入队时看到的记录 seq）。同步时如果该生
        最新记录已经不是这一条，说明别的端改过了 —— 返回 409，让手机端丢掉这
        条排队操作、以服务端数据为准，而不是误撤销别人刚加的分。
        """
        sid = str(body.get('student_id') or '').strip()
        if not sid:
            return self._err(400, 'missing_student_id', '缺少 student_id')
        expect_seq = None
        raw_expect = body.get('expect_seq')
        if raw_expect not in (None, ''):
            if isinstance(raw_expect, bool):
                return self._err(400, 'bad_expect_seq', 'expect_seq 必须是整数')
            try:
                expect_seq = int(raw_expect)
            except (TypeError, ValueError):
                return self._err(400, 'bad_expect_seq', 'expect_seq 必须是整数')
            if expect_seq < 0:
                return self._err(400, 'bad_expect_seq', 'expect_seq 必须是整数')
        with _WRITE_LOCK:
            student = store.state.student_by_id(sid)
            if student is None:
                return self._err(404, 'student_not_found', '没有找到这位同学')
            latest = _last_entry_for(store.state, sid)
            if expect_seq is not None and (latest is None or latest.seq != expect_seq):
                return self._err(409, 'stale_undo', '这条记录已经被其他端改过了，已按服务端最新数据为准')
            entry = scoring.undo_last_for(store.state, sid)
            if entry is None:
                return self._err(404, 'nothing_to_undo', '这位同学还没有可撤销的记录')
            saved, msg = self._commit(
                store,
                ChangeEvent(reason='undo', scope='seat', student_ids=[sid], extra={'source': 'mobile'}),
            )
        return (
            200,
            self._ok_payload(
                {
                    'student_id': sid,
                    'name': student.name,
                    'delta': entry.delta,
                    'points': student.points,
                    'seq': entry.seq,
                    '_saved': saved,
                    '_message': msg,
                }
            ),
        )

    def _post_student_add(self, store, body: dict) -> tuple[int, dict]:
        """POST /api/student_add：新增学生（row/col 与 /api/state 同为 0 基）。"""
        name = str(body.get('name') or '').strip()
        if not name:
            return self._err(400, 'missing_name', '请填写学生姓名')
        row = as_int(body.get('row'), 0, 999, 0)
        col = as_int(body.get('col'), 0, 999, 0)
        with _WRITE_LOCK:
            before = len(store.state.students)
            student = scoring.add_student(store.state, name, row=row, col=col)
            if student is None:
                return self._err(400, 'not_applied', '没有新增学生')
            if len(store.state.students) == before:
                return self._err(409, 'seat_taken', f'{row + 1}排{col + 1}座已经有「{student.name}」了')
            saved, msg = self._commit(
                store,
                ChangeEvent(
                    reason='seat_edit',
                    scope='all',
                    student_ids=[student.id],
                    extra={'source': 'mobile'},
                ),
            )
        return (
            200,
            self._ok_payload(
                {
                    'student': _student_payload(student),
                    'count': len(store.state.students),
                    '_saved': saved,
                    '_message': msg,
                }
            ),
        )

    def _post_student_remove(self, store, body: dict) -> tuple[int, dict]:
        """POST /api/student_remove：按 id 删除学生（历史记录保留，和桌面端一致）。"""
        sid = str(body.get('student_id') or '').strip()
        if not sid:
            return self._err(400, 'missing_student_id', '缺少 student_id')
        with _WRITE_LOCK:
            student = store.state.student_by_id(sid)
            if student is None:
                return self._err(404, 'student_not_found', '没有找到这位同学')
            if not scoring.remove_student(store.state, sid):
                return self._err(404, 'student_not_found', '没有找到这位同学')
            saved, msg = self._commit(
                store,
                ChangeEvent(
                    reason='seat_edit',
                    scope='all',
                    extra={'source': 'mobile', 'student_name': student.name},
                ),
            )
        return (
            200,
            self._ok_payload(
                {
                    'student_id': sid,
                    'name': student.name,
                    'count': len(store.state.students),
                    '_saved': saved,
                    '_message': msg,
                }
            ),
        )

    def do_POST(self):
        store = getattr(self.server, 'store', None)
        if store is None:
            self._drain_body()
            self._respond(503, b'{"error":"no store"}')
            return None
        path = self.path.split('?', 1)[0].rstrip('/')
        if self.route_v27_post(path):
            return None
        if not self._write_allowed():
            self._drain_body()
            self._fail(
                401,
                'unauthorized',
                '写操作需要口令：请用 ?t=口令 或 X-Sync-Token 请求头；服务端未设置口令时写操作一律拒绝',
            )
            return None
        routes = {
            '/api/score': self._post_score,
            '/api/undo': self._post_undo,
            '/api/student_add': self._post_student_add,
            '/api/student_remove': self._post_student_remove,
        }
        handler = routes.get(path)
        if handler is None:
            self._drain_body()
            self._fail(404, 'not_found', '未知接口')
            return None
        body = self._read_body()
        if body is None:
            if getattr(self, '_body_too_large', False):
                self._fail(
                    413,
                    'too_large',
                    f'请求内容太大（上限 {MAX_BODY_BYTES // 1024} KB）。手机端请分批提交；发文件请走「通知附件」接口。',
                )
            else:
                self._fail(400, 'bad_json', '请求体必须是 JSON 对象')
            return None
        if self._device_revoked(body):
            self._fail(403, 'device_revoked', '这台设备已被主机端吊销，请重新配对')
            return None
        raw_op_id = body.get('opId')
        if raw_op_id in (None, ''):
            raw_op_id = body.get('op_id')
        op_id = _valid_op_id(raw_op_id)
        if raw_op_id not in (None, '') and not op_id:
            self._fail(400, 'bad_op_id', 'opId 必须是 1~128 位字母/数字/-/_/.')
            return None
        oplog = getattr(self.server, 'oplog', None)
        if op_id and oplog is not None:
            cached = oplog.get(op_id)
            if cached is not None:
                logger.info('sync 写操作重复提交，按已处理返回：%s', op_id)
                self._respond(200, _json_bytes(self._duplicate_payload(store, cached)))
                return None
        try:
            code, payload = handler(store, body)
        except Exception:
            logger.exception('sync 写操作失败')
            self._fail(500, 'internal_error', '服务端处理失败')
            return None
        if code == 200 and op_id and oplog is not None and payload.get('ok'):
            oplog.record(op_id, path, self._op_summary(path, payload))
        self._respond(code, _json_bytes(payload))
        return None


def _local_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(('8.8.8.8', 80))
            return s.getsockname()[0]
    except OSError:
        try:
            return socket.gethostbyname(socket.gethostname())
        except OSError:
            return '127.0.0.1'


class SyncServer:
    """挂在 Store 上的轻量 HTTP 同步服务。"""

    DEFAULT_PORT = 8765

    def __init__(
        self,
        store,
        port: int | None = None,
        token: str = '',
        *,
        on_change: Callable[[ChangeEvent], None] | None = None,
        data_dir: str | Path | None = None,
    ) -> None:
        self._store = store
        self._port = self.DEFAULT_PORT if port is None else port
        self._token = token
        self._on_change = on_change
        self._httpd = None
        self._thread = None
        self._notifications = None
        self._devices = None
        if data_dir:
            self._oplog = OpLog(Path(data_dir) / 'sync_ops.json')
        else:
            self._oplog = None

    @property
    def oplog(self) -> OpLog:
        if self._oplog is None:
            self._oplog = OpLog()
        return self._oplog

    @property
    def notifications(self) -> NotifyStore:
        """通知存储。界面要读它显示浮层，HTTP 处理器要写它，所以必须是同一个实例。"""
        http = self._httpd
        ns = getattr(self, '_notifications', None)
        if ns is None and http is not None:
            ns = getattr(http, 'notifications', None)
        if ns is None:
            ns = NotifyStore()
        self._notifications = ns
        if http is not None and getattr(http, 'notifications', None) is None:
            http.notifications = ns
        return ns

    @property
    def devices(self):
        from app.core.devices import DeviceRegistry

        http = self._httpd
        reg = getattr(self, '_devices', None)
        if reg is None and http is not None:
            reg = getattr(http, 'devices', None)
        if reg is None:
            reg = DeviceRegistry.load()
        self._devices = reg
        if http is not None and getattr(http, 'devices', None) is None:
            http.devices = reg
        return reg

    @property
    def token(self) -> str:
        return self._token

    def set_token(self, token: str) -> None:
        self._token = token or ''
        if self._httpd is not None:
            self._httpd.token = self._token

    def set_on_change(self, fn: Callable[[ChangeEvent], None] | None) -> None:
        """指定写操作后的变更通知方式；不设则直接 store.emit(event)。

        HTTP 请求跑在后台线程，而 Store 的监听器里有 Qt 界面：直接在后台线程
        调 store.emit 会让界面对象被非 UI 线程碰。Qt 启动处应传一个「把事件
        投递到主线程再 store.emit」的可调用对象（例如 QMetaObject.invokeMethod
        或自定义 pyqtSignal）。headless/测试环境保持默认即可。
        """
        self._on_change = fn
        if self._httpd is not None:
            self._httpd.on_change = fn

    def start(self, port: int | None = None) -> int:
        if self._httpd is not None:
            return self._port
        p = port if port is not None else self._port
        if not self._token:
            settings = getattr(getattr(self._store, 'state', None), 'settings', None)
            self._token = (getattr(settings, 'sync_token', '') or '').strip()
        httpd = None
        if p:
            import time as _time

            for _ in range(10):
                try:
                    httpd = _SyncHTTPServer(('0.0.0.0', p), _Handler)
                    break
                except OSError:
                    _time.sleep(0.2)
        if httpd is None:
            httpd = _SyncHTTPServer(('0.0.0.0', 0), _Handler)
        httpd.store = self._store
        httpd.token = self._token
        httpd.on_change = self._on_change
        httpd.notifications = getattr(self, '_notifications', None)
        httpd.devices = getattr(self, '_devices', None)
        httpd.oplog = self.oplog
        self._httpd = httpd
        self._port = httpd.server_address[1]
        self._thread = threading.Thread(target=httpd.serve_forever, name='classpoints-sync', daemon=True)
        self._thread.start()
        logger.info('sync server on http://%s:%d', _local_ip(), self._port)
        return self._port

    def stop(self) -> None:
        if self._httpd is None:
            return None
        self._httpd.shutdown()
        self._httpd.server_close()
        self._httpd = None
        self._thread = None
        return None

    @property
    def running(self) -> bool:
        return self._httpd is not None

    @property
    def url(self) -> str:
        if self.running:
            return f'http://{_local_ip()}:{self._port}'
        return ''

    @property
    def port(self) -> int:
        return self._port
