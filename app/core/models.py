"""数据模型：纯数据类，不含业务规则。

上一代全是裸对象（`{id, name, row, col, points}`），字段拼错要到运行时才炸。
这里用 dataclass 固定字段，并提供 from_dict / to_dict 负责与存档互转。
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Iterable

from app.version import DATA_SCHEMA

_seq_counter = 0


def new_id(prefix: str) -> str:
    """短 id：前缀 + 时间戳 + 序号，便于人眼排查，无需 uuid4 的长度。"""
    global _seq_counter
    _seq_counter += 1
    return f'{prefix}_{int(time.time() * 1000):x}_{_seq_counter:x}'


def new_uuid() -> str:
    return uuid.uuid4().hex


def as_int(value: Any, low: int, high: int, default: int) -> int:
    """把任意值夹到 [low, high]；无法解析时返回 default。"""
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, n))


def as_str(value: Any, default: str = '') -> str:
    if value is None:
        return default
    return str(value)


@dataclass
class Student:
    id: str = ''
    name: str = ''
    row: int = 0
    col: int = 0
    points: int = 0

    def __post_init__(self) -> None:
        if not self.id:
            self.id = new_id('stu')
        self.name = as_str(self.name).strip()
        self.row = as_int(self.row, 0, 999, 0)
        self.col = as_int(self.col, 0, 999, 0)
        try:
            self.points = int(self.points)
        except (TypeError, ValueError):
            self.points = 0

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> 'Student':
        return cls(
            id=as_str(raw.get('id')),
            name=raw.get('name'),
            row=raw.get('row'),
            col=raw.get('col'),
            points=raw.get('points'),
        )

    def to_dict(self) -> dict[str, Any]:
        return {'id': self.id, 'name': self.name, 'row': self.row, 'col': self.col, 'points': self.points}

    def seat(self) -> tuple[int, int]:
        return (self.row, self.col)


SOURCE_LABELS: dict[str, str] = {
    'morning': '早读',
    'noon': '午读',
    'manual': '手动',
    'lottery': '抽奖',
    'import': '导入',
}


def source_label(source: str) -> str:
    return SOURCE_LABELS.get(source, source or '手动')


@dataclass
class HistoryEntry:
    id: str = ''
    seq: int = 0
    ts: int = 0
    student_id: str = ''
    student_name: str = ''
    delta: int = 0
    reason: str = ''
    source: str = 'manual'

    def __post_init__(self) -> None:
        if not self.id:
            self.id = new_id('h')
        if not self.ts:
            self.ts = int(time.time() * 1000)
        try:
            self.delta = int(self.delta)
        except (TypeError, ValueError):
            self.delta = 0
        self.student_name = as_str(self.student_name)
        self.reason = as_str(self.reason)
        self.source = as_str(self.source, 'manual') or 'manual'

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> 'HistoryEntry':
        return cls(
            id=as_str(raw.get('id')),
            seq=as_int(raw.get('seq'), 0, 1000000000000, 0),
            ts=as_int(raw.get('ts'), 0, 1000000000000000, 0),
            student_id=as_str(raw.get('studentId')),
            student_name=raw.get('studentName'),
            delta=raw.get('delta'),
            reason=raw.get('reason'),
            source=as_str(raw.get('source'), 'manual'),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            'id': self.id,
            'seq': self.seq,
            'ts': self.ts,
            'studentId': self.student_id,
            'studentName': self.student_name,
            'delta': self.delta,
            'reason': self.reason,
            'source': self.source,
        }


@dataclass
class Prize:
    id: str = ''
    name: str = ''
    weight: float = 1.0

    def __post_init__(self) -> None:
        if not self.id:
            self.id = new_id('pz')
        self.name = as_str(self.name).strip()
        try:
            self.weight = float(self.weight)
        except (TypeError, ValueError):
            self.weight = 1.0
        if self.weight < 0:
            self.weight = 0.0

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> 'Prize':
        return cls(id=as_str(raw.get('id')), name=raw.get('name'), weight=raw.get('weight', 1))

    def to_dict(self) -> dict[str, Any]:
        return {'id': self.id, 'name': self.name, 'weight': self.weight}


@dataclass
class Podium:
    enabled: bool = True
    position: str = 'top'
    span: int = 4
    align: str = 'center'

    def normalized(self, cols: int) -> 'Podium':
        span = as_int(self.span, 1, max(1, cols), min(4, max(1, cols)))
        return Podium(
            enabled=bool(self.enabled),
            position='bottom' if self.position == 'bottom' else 'top',
            span=span,
            align=self.align if self.align in ('left', 'center', 'right') else 'center',
        )

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> 'Podium':
        return cls(
            enabled=bool(raw.get('enabled', True)),
            position=as_str(raw.get('position'), 'top'),
            span=as_int(raw.get('span'), 1, 30, 4),
            align=as_str(raw.get('align'), 'center'),
        )

    def to_dict(self) -> dict[str, Any]:
        return {'enabled': self.enabled, 'position': self.position, 'span': self.span, 'align': self.align}


@dataclass
class Layout:
    rows: int = 6
    cols: int = 8
    col_groups: list[int] = field(default_factory=lambda: [2, 4, 2])
    aisle_every: int = 0
    podium: Podium = field(default_factory=Podium)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> 'Layout':
        """宽松读取：只认需要的键，其余交给 resolve_layout 规整。"""
        groups = raw.get('colGroups')
        return cls(
            rows=as_int(raw.get('rows'), 1, 30, 6),
            cols=as_int(raw.get('cols'), 1, 30, 8),
            col_groups=[v for v in (as_int(g, 0, 999, 0) for g in groups) if v > 0] if isinstance(groups, list) and groups else [],
            aisle_every=as_int(raw.get('aisleEvery'), 0, 12, 0),
            podium=Podium.from_dict(raw.get('podium') or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            'rows': self.rows,
            'cols': self.cols,
            'colGroups': list(self.col_groups),
            'aisleEvery': 0,
            'podium': self.podium.to_dict(),
        }


DEFAULT_PLUS_REASONS = ['早读表现好', '午读表现好', '课堂积极回答', '作业优秀', '帮助同学', '进步明显']

DEFAULT_MINUS_REASONS = ['未完成作业', '课堂讲话', '迟到', '早读不认真', '午读不认真', '违反纪律']

ANIMATIONS = ['none', 'roll', 'cards', 'wheel', 'video']


@dataclass
class Settings:
    step_morning: int = 1
    step_noon: int = 1
    prize_threshold: int = 10
    prize_cost: int = 10
    lottery_animation: str = 'none'
    lottery_mode: str = 'threshold'
    lottery_top_n: int = 10
    random_count: int = 1
    plus_reasons: list[str] = field(default_factory=lambda: list(DEFAULT_PLUS_REASONS))
    minus_reasons: list[str] = field(default_factory=lambda: list(DEFAULT_MINUS_REASONS))
    sync_token: str = ''
    sync_port: int = 8765
    launch_animation: str = ''
    launch_enabled: bool = True
    app_icon: str = ''
    backup_include_media: bool = False
    backup_include_theme: bool = False
    class_fingerprint: str = ''
    sync_fixed_url: str = ''
    tunnel_token: str = ''
    tunnel_hostname: str = ''
    pair_token: str = ''
    sync_master_only: bool = False
    sync_client_url: str = ''
    sync_client_token: str = ''
    sync_auto: bool = False
    sync_pull_seq: int = 0
    sync_push_seq: int = 0
    sync_role: str = 'host'
    client_device_id: str = ''
    device_id: str = ''
    device_name: str = ''

    def ensure_sync_token(self) -> str:
        """同步口令：首次启用同步时生成，之后一直复用（换了手机不用重新告诉）。"""
        if not self.sync_token:
            self.sync_token = new_sync_token()
        return self.sync_token

    def ensure_device_identity(self) -> tuple[str, str]:
        """本机在星座里的身份。首次调用生成 id 与默认名（用电脑名），之后复用。"""
        import os
        import secrets
        if not self.device_id:
            self.device_id = secrets.token_hex(8)
        if not self.device_name:
            try:
                self.device_name = (os.environ.get('COMPUTERNAME') or '这台电脑')[:40]
            except Exception:
                self.device_name = '这台电脑'
        return (self.device_id, self.device_name)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> 'Settings':
        base = cls()
        plus = raw.get('plusReasons')
        minus = raw.get('minusReasons')
        return cls(
            step_morning=as_int(raw.get('stepMorning'), 1, 999, base.step_morning),
            step_noon=as_int(raw.get('stepNoon'), 1, 999, base.step_noon),
            prize_threshold=as_int(raw.get('prizeThreshold'), 0, 1000000, base.prize_threshold),
            prize_cost=as_int(raw.get('prizeCost'), 0, 1000000, base.prize_cost),
            lottery_animation=normalize_animation(raw.get('lotteryAnimation')),
            lottery_mode=normalize_lottery_mode(raw.get('lotteryMode')),
            lottery_top_n=as_int(raw.get('lotteryTopN'), 1, 999, base.lottery_top_n),
            random_count=as_int(raw.get('randomCount'), 1, 999, 1),
            plus_reasons=[as_str(x) for x in plus] if isinstance(plus, list) and plus else list(DEFAULT_PLUS_REASONS),
            minus_reasons=[as_str(x) for x in minus] if isinstance(minus, list) and minus else list(DEFAULT_MINUS_REASONS),
            sync_token=as_str(raw.get('syncToken'), '')[:64],
            sync_port=as_int(raw.get('syncPort'), 1024, 65535, base.sync_port),
            launch_animation=as_str(raw.get('launchAnimation'), ''),
            launch_enabled=as_bool(raw.get('launchEnabled'), True),
            app_icon=as_str(raw.get('appIcon'), ''),
            backup_include_media=as_bool(raw.get('backupIncludeMedia'), False),
            backup_include_theme=as_bool(raw.get('backupIncludeTheme'), False),
            class_fingerprint=as_str(raw.get('classFingerprint'), '')[:32],
            sync_fixed_url=as_str(raw.get('syncFixedUrl'), '')[:300],
            tunnel_token=as_str(raw.get('tunnelToken'), '')[:400],
            tunnel_hostname=as_str(raw.get('tunnelHostname'), '')[:120].lower(),
            pair_token=as_str(raw.get('pairToken'), '')[:64],
            sync_master_only=as_bool(raw.get('syncMasterOnly'), False),
            sync_client_url=as_str(raw.get('syncClientUrl'), '')[:512],
            sync_client_token=as_str(raw.get('syncClientToken'), '')[:128],
            sync_auto=as_bool(raw.get('syncAuto'), False),
            sync_pull_seq=as_int(raw.get('syncPullSeq'), 0, 1000000000000, 0),
            sync_push_seq=as_int(raw.get('syncPushSeq'), 0, 1000000000000, 0),
            sync_role=normalize_sync_role(raw.get('syncRole')),
            client_device_id=str(raw.get('clientDeviceId') or '')[:64],
            device_id=as_str(raw.get('deviceId'), '')[:64],
            device_name=as_str(raw.get('deviceName'), '')[:40],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            'stepMorning': self.step_morning,
            'stepNoon': self.step_noon,
            'prizeThreshold': self.prize_threshold,
            'prizeCost': self.prize_cost,
            'lotteryAnimation': self.lottery_animation,
            'lotteryMode': self.lottery_mode,
            'lotteryTopN': self.lottery_top_n,
            'randomCount': self.random_count,
            'plusReasons': list(self.plus_reasons),
            'minusReasons': list(self.minus_reasons),
            'syncToken': self.sync_token,
            'syncPort': self.sync_port,
            'launchAnimation': self.launch_animation,
            'launchEnabled': self.launch_enabled,
            'appIcon': self.app_icon,
            'backupIncludeMedia': self.backup_include_media,
            'backupIncludeTheme': self.backup_include_theme,
            'classFingerprint': self.class_fingerprint,
            'syncFixedUrl': self.sync_fixed_url,
            'tunnelToken': self.tunnel_token,
            'tunnelHostname': self.tunnel_hostname,
            'pairToken': self.pair_token,
            'syncMasterOnly': self.sync_master_only,
            'syncClientUrl': self.sync_client_url,
            'syncClientToken': self.sync_client_token,
            'syncAuto': self.sync_auto,
            'syncPullSeq': self.sync_pull_seq,
            'syncPushSeq': self.sync_push_seq,
            'syncRole': self.sync_role,
            'clientDeviceId': self.client_device_id,
            'deviceId': self.device_id,
            'deviceName': self.device_name,
        }


def new_sync_token() -> str:
    """生成同步口令：只留易读字符，方便老师口述/手输（去掉 0O1lI 之类）。"""
    import secrets
    alphabet = 'abcdefghijkmnpqrstuvwxyz23456789'
    return ''.join(secrets.choice(alphabet) for _ in range(8))


def normalize_animation(value: Any) -> str:
    text = as_str(value, 'none')
    if text in ANIMATIONS:
        return text
    return 'none'


def as_bool(value: Any, default: bool = False) -> bool:
    """宽松布尔：兼容 "1"/"0"/"true"/"false" 这类写进 JSON 又被手改过的情况。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in ('1', 'true', 'yes', 'on'):
            return True
        if text in ('0', 'false', 'no', 'off', ''):
            return False
    return default


LOTTERY_MODES = ('threshold', 'rank')

SYNC_ROLES = ('host', 'client')


def normalize_sync_role(value) -> str:
    """这台机器是「自己有数据的主机」还是「跟着别人跑」的客户端。"""
    text = (value or '').strip().lower()
    if text in SYNC_ROLES:
        return text
    return 'host'


def normalize_lottery_mode(value: Any) -> str:
    text = as_str(value, 'threshold')
    if text in LOTTERY_MODES:
        return text
    return 'threshold'


@dataclass
class MediaInfo:
    """只存元信息；视频本体另存，不进主数据（上一代正是因为塞一起才写挂）。"""
    filename: str = ''
    size: int = 0
    duration_ms: int = 0
    mime: str = ''

    @property
    def present(self) -> bool:
        return bool(self.filename)

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> 'MediaInfo | None':
        if not isinstance(raw, dict):
            return None
        info = cls(
            filename=as_str(raw.get('filename') or raw.get('name')),
            size=as_int(raw.get('size'), 0, 1000000000000, 0),
            duration_ms=as_int(raw.get('durationMs') or raw.get('duration'), 0, 1000000000000, 0),
            mime=as_str(raw.get('mime') or raw.get('type')),
        )
        if info.present:
            return info
        return None

    def to_dict(self) -> dict[str, Any]:
        return {'filename': self.filename, 'size': self.size, 'durationMs': self.duration_ms, 'mime': self.mime}


@dataclass
class LotteryRecord:
    id: str = ''
    ts: int = 0
    prize_name: str = ''
    winners: list[str] = field(default_factory=list)
    winner_names: list[str] = field(default_factory=list)
    cost: int = 0

    def __post_init__(self) -> None:
        if not self.id:
            self.id = new_id('lot')
        if not self.ts:
            self.ts = int(time.time() * 1000)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> 'LotteryRecord':
        winners = raw.get('winners')
        names = raw.get('winnerNames')
        return cls(
            id=as_str(raw.get('id')),
            ts=as_int(raw.get('ts'), 0, 1000000000000000, 0),
            prize_name=as_str(raw.get('prizeName')),
            winners=[as_str(x) for x in winners] if isinstance(winners, list) else [],
            winner_names=[as_str(x) for x in names] if isinstance(names, list) else [],
            cost=as_int(raw.get('cost'), 0, 1000000, 0),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            'id': self.id,
            'ts': self.ts,
            'prizeName': self.prize_name,
            'winners': list(self.winners),
            'winnerNames': list(self.winner_names),
            'cost': self.cost,
        }


def default_prizes() -> list[Prize]:
    return [
        Prize(name='免作业券', weight=1),
        Prize(name='小零食', weight=3),
        Prize(name='文具礼包', weight=2),
        Prize(name='谢谢参与', weight=4),
    ]


@dataclass
class AppState:
    schema: int = DATA_SCHEMA
    class_name: str = '我的班级'
    layout: Layout = field(default_factory=Layout)
    students: list[Student] = field(default_factory=list)
    history: list[HistoryEntry] = field(default_factory=list)
    lottery: list[LotteryRecord] = field(default_factory=list)
    prizes: list[Prize] = field(default_factory=default_prizes)
    settings: Settings = field(default_factory=Settings)
    background: str | None = None
    media: MediaInfo | None = None
    seq: int = 0
    saved_at: int = 0
    source_version: str = ''

    def student_by_id(self, student_id: str) -> Student | None:
        for s in self.students:
            if s.id == student_id:
                return s
        return None

    def student_by_name(self, name: str) -> Student | None:
        target = as_str(name).strip()
        if not target:
            return None
        for s in self.students:
            if s.name.strip() == target:
                return s
        return None

    def student_at(self, row: int, col: int) -> Student | None:
        for s in self.students:
            if s.row == row:
                if s.col == col:
                    return s
        return None

    def occupied_seats(self) -> dict[tuple[int, int], Student]:
        return {s.seat(): s for s in self.students}

    def next_seq(self) -> int:
        self.seq += 1
        return self.seq

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> 'AppState':
        students = raw.get('students')
        history = raw.get('history')
        lottery = raw.get('lottery')
        prizes = raw.get('prizes')
        state = cls(
            schema=as_int(raw.get('schema'), 0, 1000000, DATA_SCHEMA),
            class_name=as_str(raw.get('className'), '我的班级') or '我的班级',
            layout=Layout.from_dict(raw.get('layout') or {}),
            students=[Student.from_dict(x) for x in students if isinstance(x, dict)] if isinstance(students, list) else [],
            history=[HistoryEntry.from_dict(x) for x in history if isinstance(x, dict)] if isinstance(history, list) else [],
            lottery=[LotteryRecord.from_dict(x) for x in lottery if isinstance(x, dict)] if isinstance(lottery, list) else [],
            prizes=[Prize.from_dict(x) for x in prizes if isinstance(x, dict)] if isinstance(prizes, list) and prizes else default_prizes(),
            settings=Settings.from_dict(raw.get('settings') or {}),
            background=as_str(raw.get('background')) or None,
            media=MediaInfo.from_dict(raw.get('media', {}).get('lotteryVideo') if isinstance(raw.get('media'), dict) else None),
            seq=as_int(raw.get('seq'), 0, 1000000000000, 0),
            saved_at=as_int(raw.get('savedAt'), 0, 1000000000000000, 0),
            source_version=as_str(raw.get('appVersion')),
        )
        return state

    def to_dict(self) -> dict[str, Any]:
        from app.version import __version__
        return {
            'schema': DATA_SCHEMA,
            'appVersion': __version__,
            'className': self.class_name,
            'layout': self.layout.to_dict(),
            'students': [s.to_dict() for s in self.students],
            'history': [h.to_dict() for h in self.history],
            'lottery': [r.to_dict() for r in self.lottery],
            'prizes': [p.to_dict() for p in self.prizes],
            'settings': self.settings.to_dict(),
            'background': self.background,
            'media': {'lotteryVideo': self.media.to_dict() if self.media else None},
            'seq': self.seq,
            'savedAt': self.saved_at,
        }


def empty_state() -> AppState:
    return AppState()


def state_from_history(students: Iterable[Student]) -> AppState:
    """便捷构造：只给一批学生。"""
    return AppState(students=list(students))
