"""旧版（网页版）备份文件的兼容解析。

网页版「导出备份」写的不是一个裸 state，而是套了一层信封：

    {"app": "class-points", "schema": 1, "exportedAt": 1792720733024,
     "state": {"className": "高三(20)班", "students": [...], ...}}

程序自己的 `data.json` 只有里面那一层。两层内层字段完全一致（`className` /
`students` / `history` / `layout` / `settings` / `prizes` / `seq` ...），所以这里
只做三件事：剥信封、认出「这到底是不是班级数据」、交给 `AppState.from_dict`。
不做字段搬运——搬运规则只该有一处，就在 models 里。

认文件比认扩展名靠谱：用户手上的文件可能叫 `xxx.json`、也可能叫 `xxx 备份.json`，
甚至是从聊天软件里存下来的（文件名带「(1)」）。所以一律按内容判断。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.core.models import AppState

# 网页版信封里装着 state 的那一层
WRAPPER_KEY = 'state'

# 认出「这是班级数据」的两个字段，任一命中即可
MARKERS = ('students', 'className')

# 网页版在信封里写的固定标识
WEB_APP_ID = 'class-points'


class LegacyError(Exception):
    """解析失败；`str(exc)` 就是给用户看的那句话。"""


def looks_like_state(raw: Any) -> bool:
    """这个字典是不是「一层 state」（含 students 或 className）。"""
    if not isinstance(raw, dict):
        return False
    return any(key in raw for key in MARKERS)


def unwrap_payload(raw: Any) -> dict[str, Any]:
    """剥掉网页版的信封，返回真正的 state 字典。

    仍然认不出班级数据时抛 `LegacyError`，错误文案直接面向用户。
    """
    if not isinstance(raw, dict):
        raise LegacyError('文件内容不是班级数据（顶层既不是对象）')

    if looks_like_state(raw):
        return raw

    inner = raw.get(WRAPPER_KEY)
    if isinstance(inner, dict) and looks_like_state(inner):
        return inner

    for key in ('data', 'backup', 'result'):
        mid = raw.get(key)
        if not isinstance(mid, dict):
            continue
        if looks_like_state(mid):
            return mid
        deeper = mid.get(WRAPPER_KEY)
        if not isinstance(deeper, dict):
            continue
        if looks_like_state(deeper):
            return deeper

    if raw.get('app') == WEB_APP_ID:
        raise LegacyError('这是网页版的备份，但里面没有班级数据（state 为空）')
    raise LegacyError('这个 JSON 里没有班级字段，看起来不是本程序的数据文件')


def state_from_payload(raw: Any) -> AppState:
    """把任意形态的备份字典解析成 `AppState`。"""
    payload = unwrap_payload(raw)
    try:
        state = AppState.from_dict(payload)
    except Exception as exc:
        raise LegacyError(f'数据解析失败：{exc}') from exc
    if not state.students:
        raise LegacyError('备份里没有任何学生，导入后会是空班级，已取消')
    return state


def read_json_file(path: str | Path) -> Any:
    """读 JSON 文件（兼容 UTF-8 BOM 与 GBK 落盘的老文件）。"""
    p = Path(path)
    try:
        blob = p.read_bytes()
    except OSError as exc:
        raise LegacyError(f'读不到这个文件：{exc}') from exc

    text = None
    for encoding in ('utf-8-sig', 'utf-8', 'gb18030'):
        try:
            text = blob.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise LegacyError('文件编码认不出来（既不是 UTF-8 也不是 GBK）')
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise LegacyError(f'这不是有效的 JSON：{exc}') from exc


def load_state_file(path: str | Path) -> AppState:
    """一步到位：读文件 → 剥信封 → 解析。"""
    return state_from_payload(read_json_file(path))
