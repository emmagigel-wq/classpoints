"""已配对设备（「星座」成员表）。

存 `<数据目录>/devices.json`。宿主端是唯一写入方；用户端只读宿主给的列表。

一条记录长这样::

    {"id": "7c1e...", "name": "李老师的手机", "kind": "phone",
     "pairedAt": 1761300000.0, "lastSeen": 1761300600.0, "revoked": false,
     "fingerprint": "CP7-K3M9-P2X4"}

``revoked`` 为真的设备后续请求一律 403——手机上丢了一台，家长也不用重装。
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

FILE_NAME = 'devices.json'
MAX_DEVICES = 64
MAX_FILE_NOTIFICATIONS = 200


@dataclass
class Device:
    id: str
    name: str = ''
    kind: str = 'phone'
    paired_at: float = 0.0
    last_seen: float = 0.0
    revoked: bool = False
    fingerprint: str = ''

    def to_dict(self) -> dict[str, Any]:
        return {
            'id': self.id,
            'name': self.name,
            'kind': self.kind,
            'pairedAt': self.paired_at,
            'lastSeen': self.last_seen,
            'revoked': self.revoked,
            'fingerprint': self.fingerprint,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> 'Device':
        return cls(
            id=str(raw.get('id') or ''),
            name=str(raw.get('name') or '')[:40],
            kind=str(raw.get('kind') or 'phone')[:16],
            paired_at=float(raw.get('pairedAt') or 0.0),
            last_seen=float(raw.get('lastSeen') or 0.0),
            revoked=bool(raw.get('revoked')),
            fingerprint=str(raw.get('fingerprint') or ''),
        )


@dataclass
class DeviceRegistry:
    """设备表的读写门面。线程内用；跨线程访问由调用方加锁。"""
    path: Path
    devices: list[Device] = field(default_factory=list)

    @classmethod
    def load(cls, root: str | Path | None = None) -> 'DeviceRegistry':
        p = _file(root)
        reg = cls(path=p)
        try:
            raw = json.loads(p.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return reg
        items = raw.get('devices') if isinstance(raw, dict) else None
        if isinstance(items, list):
            for item in items:
                if not isinstance(item, dict):
                    continue
                if not item.get('id'):
                    continue
                reg.devices.append(Device.from_dict(item))
        return reg

    def save(self) -> bool:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(self.path.name + '.tmp')
            tmp.write_text(
                json.dumps({'version': 1, 'devices': [d.to_dict() for d in self.devices]}, ensure_ascii=False, indent=2),
                encoding='utf-8',
            )
            os.replace(tmp, self.path)
            return True
        except OSError:
            return False

    def get(self, device_id: str) -> Device | None:
        for d in self.devices:
            if d.id == device_id:
                return d
        return None

    def is_revoked(self, device_id: str) -> bool:
        d = self.get(device_id)
        return bool(d and d.revoked)

    def online(self, device_id: str, window: float = 90.0) -> bool:
        d = self.get(device_id)
        return bool(d and not d.revoked and d.last_seen and time.time() - d.last_seen <= window)

    def as_list(self) -> list[dict[str, Any]]:
        out = []
        now = time.time()
        for d in self.devices:
            item = d.to_dict()
            item['online'] = bool(not d.revoked and d.last_seen and now - d.last_seen <= 90)
            out.append(item)
        out.sort(key=lambda r: (r['revoked'], -float(r['lastSeen'] or 0)))
        return out

    def upsert(self, device_id: str, name: str = '', kind: str = 'phone', fingerprint: str = '') -> Device:
        """登记或更新一台设备（配对时调用）。已吊销的设备**不会**被复活。"""
        now = time.time()
        d = self.get(device_id)
        if d is None:
            if len(self.devices) >= MAX_DEVICES:
                victims = sorted(self.devices, key=lambda x: (not x.revoked, x.last_seen))
                if victims:
                    self.devices.remove(victims[0])
            d = Device(id=device_id, paired_at=now)
            self.devices.append(d)
        if name:
            d.name = name[:40]
        if kind:
            d.kind = kind[:16]
        if fingerprint:
            d.fingerprint = fingerprint
        d.last_seen = now
        self.save()
        return d

    def touch(self, device_id: str) -> None:
        """记录一次活动（每个请求都调，但只改内存，每 20 秒才落盘一次）。"""
        d = self.get(device_id)
        if d is None:
            return None
        now = time.time()
        if now - d.last_seen >= 20:
            d.last_seen = now
            if int(now) % 60 < 20:
                self.save()

    def rename(self, device_id: str, name: str) -> bool:
        d = self.get(device_id)
        if d is None:
            return False
        d.name = (name or '').strip()[:40]
        self.save()
        return True

    def revoke(self, device_id: str, revoked: bool = True) -> bool:
        d = self.get(device_id)
        if d is None:
            return False
        d.revoked = bool(revoked)
        self.save()
        return True

    def remove(self, device_id: str) -> bool:
        d = self.get(device_id)
        if d is None:
            return False
        self.devices.remove(d)
        self.save()
        return True

    def label(self, device_id: str) -> str:
        d = self.get(device_id)
        if d and d.name:
            return d.name
        return '未知设备'


def _file(root: str | Path | None) -> Path:
    if root is not None:
        return Path(root) / FILE_NAME
    from app.core import paths
    return Path(paths.current_data_dir()) / FILE_NAME
