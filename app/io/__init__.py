"""io 层：导入导出（CSV / XLSX / DOCX / 整库备份）。

分层契约（docs/ARCHITECTURE.md）：本层**只依赖 app.core**，
不许 import PyQt5 / app.ui / app.services —— 由 tests/test_layering.py 静态强制。
所以本层可以在没有显示器的环境里完整跑单测。

三条全层通用的硬规矩：

1. 每个对外函数返回结果对象（`IoResult` 及其子类），必有 `ok` 与 `error`；
   **不用异常做正常控制流，也绝不静默失败**——失败一定带上给人看的 `error` 文案。
2. 导入一律「先整体解析 → 校验 → 再改数据」。校验不过就整体拒绝，
   调用方的 state 一个字节都不动（见 `apply_students` / `apply_history`）。
3. 中文一律 utf-8-sig（Excel 打开不乱码）；BOM、空行、缺列、全角数字都要容忍。

本模块同时收纳两类东西：

* 结果对象与行结构（各格式子模块共用）；
* 与格式无关的**解析原语**（数字/时间/表头/编码）与**落库动作**
  （`apply_students` / `apply_history`）——CSV 与 XLSX 解析出来的行，
  都走同一套落库逻辑，避免两条导入路径行为不一致。
"""

from __future__ import annotations

import datetime as _dt
import os
import re
import time
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Sequence

from app.core import scoring
from app.core.layout import resolve_layout
from app.core.models import SOURCE_LABELS, AppState, HistoryEntry, Layout, Student, source_label


@dataclass
class IoResult:
    """所有 io 操作的公共结果。

    ok=False 时 `error` 一定有内容（给用户看的），`reason` 是给程序判断的短标记。
    """

    ok: bool = True
    error: str = ''
    reason: str = 'ok'
    message: str = ''
    warnings: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return self.ok


@dataclass
class ExportResult(IoResult):
    path: Path | None = None
    rows: int = 0
    bytes_written: int = 0


@dataclass
class ImportResult(IoResult):
    """解析结果。此时**还没有**碰过任何 AppState。"""

    students: list['StudentRow'] = field(default_factory=list)
    records: list['HistoryRow'] = field(default_factory=list)
    total_rows: int = 0
    skipped_rows: int = 0
    source_path: Path | None = None


@dataclass
class ApplyResult(IoResult):
    """落库结果。added/updated/skipped 的含义见各函数文档。"""

    added: int = 0
    updated: int = 0
    skipped: int = 0
    moved: int = 0
    details: list[str] = field(default_factory=list)


def _export_fail(reason: str, error: str, path: Path | None = None) -> ExportResult:
    return ExportResult(ok=False, error=error, reason=reason, path=path)


@dataclass
class StudentRow:
    """从文件里解析出来的一行学生。`row/col` 为 None 表示文件没给座位。"""

    name: str
    points: int = 0
    points_given: bool = False
    row: int | None = None
    col: int | None = None
    rank: int | None = None
    line: int = 0


@dataclass
class HistoryRow:
    """从文件里解析出来的一条积分记录。"""

    student_name: str
    delta: int
    ts: int = 0
    reason: str = ''
    source: str = 'import'
    seq: int | None = None
    line: int = 0
    student_id: str = ''


# 全角 / 特殊字符 → 半角。解析数字与来源时统一先过一遍。
_EXTRA_TRANS = str.maketrans({
    '＋': '+', '－': '-', '—': '-', '–': '-', '﹣': '-',
    '．': '.', '％': '%', '，': ',', '\u3000': ' ',
})


def normalize_text(value: Any) -> str:
    """单元格 → 干净的字符串：去 BOM、全角转半角、去首尾空白。

    Excel 里从中文输入法敲出来的「１２３」与「123」在这里被拉齐，
    这是「全角数字要能导」的第一道闸。
    """
    if value is None:
        return ''
    text = value if isinstance(value, str) else str(value)
    text = text.replace('\ufeff', '')
    text = unicodedata.normalize('NFKC', text)
    text = text.translate(_EXTRA_TRANS)
    return text.strip()


def parse_int(value: Any, default: int | None = None) -> int | None:
    """把任意单元格解析成整数；解析不出来返回 default（不抛异常）。

    容忍：全角数字、千分位逗号、前导 +、`12.0`、结尾的「分」。
    """
    if value is None:
        return default
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    text = normalize_text(value)
    if not text:
        return default
    text = text.replace(',', '').replace(' ', '')
    if text.endswith('分'):
        text = text[:-1]
    try:
        return int(text)
    except ValueError:
        pass
    try:
        num = float(text)
    except ValueError:
        return default
    if num != num or num in (float('inf'), float('-inf')):
        return default
    return int(num)


_TS_FORMATS = (
    '%Y-%m-%d %H:%M:%S',
    '%Y-%m-%d %H:%M',
    '%Y/%m/%d %H:%M:%S',
    '%Y/%m/%d %H:%M',
    '%Y-%m-%dT%H:%M:%S',
    '%Y.%m.%d %H:%M:%S',
    '%Y年%m月%d日 %H:%M:%S',
    '%Y-%m-%d',
    '%Y/%m/%d',
    '%Y.%m.%d',
    '%Y年%m月%d日',
)


def parse_ts(value: Any, default: int | None = None) -> int | None:
    """任意时间表示 → 毫秒时间戳。

    能吃：Excel 的 datetime、`2026-10-23 21:00:00`、`2026/10/23`、纯数字（秒或毫秒）。
    吃不动返回 default，由调用方决定是拒绝整行还是补当前时间。
    """
    if value is None or value == '':
        return default
    if isinstance(value, _dt.datetime):
        return int(value.timestamp() * 1000)
    if isinstance(value, _dt.date):
        return int(_dt.datetime(value.year, value.month, value.day).timestamp() * 1000)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        num = int(value)
        if num <= 0:
            return default
        if num > 100000000000:
            return num
        return num * 1000
    text = normalize_text(value)
    if not text:
        return default
    if text.isdigit():
        num = int(text)
        if num > 100000000000:
            return num
        return num * 1000
    text = text.replace('T', ' ')
    for fmt in _TS_FORMATS:
        try:
            return int(_dt.datetime.strptime(text, fmt).timestamp() * 1000)
        except ValueError:
            pass
    return default


def fmt_ts(ms: int) -> str:
    """毫秒时间戳 → `2026-10-23 21:00:00`（本地时区）。"""
    if not ms:
        return ''
    try:
        return time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(ms / 1000))
    except (OverflowError, OSError, ValueError):
        return str(ms)


def read_text_file(path: str | Path) -> tuple[str | None, str, str]:
    """读文本文件：依次尝试 utf-8-sig → gbk。返回 (文本, 编码, 错误)。

    中文 Windows 上 Excel「另存为 CSV」默认 GBK，所以这里是双编码而不是死认 UTF-8；
    两种都解不开时明确报错，不返回半个乱码文件。
    """
    p = Path(path)
    if not p.exists():
        return None, '', f'文件不存在：{p}'
    if p.is_dir():
        return None, '', f'这是一个目录，不是文件：{p}'
    try:
        raw = p.read_bytes()
    except OSError as exc:
        return None, '', f'无法读取文件：{exc}'
    if not raw:
        return '', 'utf-8-sig', ''
    problems: list[str] = []
    for enc in ('utf-8-sig', 'gbk'):
        try:
            return raw.decode(enc), enc, ''
        except UnicodeDecodeError as exc:
            problems.append(f'{enc}（{exc.reason}）')
    return None, '', '文件编码无法识别，已尝试 ' + '、'.join(problems) + '。请把文件另存为「CSV UTF-8」或 GBK 编码后重试。'


STUDENT_ALIASES: dict[str, tuple[str, ...]] = {
    'name': ('姓名', '名字', '学生', '学生姓名', 'name', 'student', 'studentname'),
    'points': ('积分', '分数', '分值', 'points', 'score'),
    'rank': ('排名', '名次', 'rank'),
    'row': ('座位行', '行', 'row'),
    'col': ('座位列', '列', 'col', 'column'),
}

HISTORY_ALIASES: dict[str, tuple[str, ...]] = {
    'id': ('学生id', '学生ID', 'id', '学号', '学生编号'),
    'seq': ('序号', '编号', 'seq', 'no'),
    'ts': ('时间', '日期', '时刻', 'time', 'ts', 'datetime'),
    'name': ('学生', '姓名', '学生姓名', 'name', 'student'),
    'delta': ('变化', '变动', '分数变化', '加减分', 'delta', 'change'),
    'reason': ('原因', '理由', '事由', 'reason', 'note', '备注'),
    'source': ('来源', '渠道', 'source', 'from'),
}

_PAREN_RE = re.compile(r'[（(].*?[)）]')


def _header_key(raw: Any) -> str:
    """表头单元格 → 比较用的键：去括号注释、去「*」「:」、转小写。"""
    text = normalize_text(raw).lower()
    text = _PAREN_RE.sub('', text)
    return text.strip(' :：*\u3000')


def resolve_columns(header: Sequence[Any], aliases: dict[str, tuple[str, ...]]) -> dict[str, int]:
    """表头行 → {字段名: 列号}。缺列不报错（由调用方决定哪个字段是必须的）。"""
    out: dict[str, int] = {}
    for idx, raw in enumerate(header):
        key = _header_key(raw)
        if not key:
            continue
        for fname, names in aliases.items():
            if fname in out:
                continue
            if key in names:
                out[fname] = idx
                break
    return out


def cell_at(row: Sequence[Any], cols: dict[str, int], fname: str) -> Any:
    """按字段名取单元格；列不存在或行不够长都返回 None（不抛 IndexError）。"""
    idx = cols.get(fname)
    if idx is None or idx >= len(row):
        return None
    return row[idx]


def _strip_blank(rows: Sequence[Sequence[Any]]) -> list[tuple[int, Sequence[Any]]]:
    """去掉整行空白的行，保留原行号（1 基），便于报错指位置。"""
    out: list[tuple[int, Sequence[Any]]] = []
    for i, row in enumerate(rows, start=1):
        if any(normalize_text(c) for c in row):
            out.append((i, row))
    return out


SOURCE_KEY_BY_LABEL: dict[str, str] = {label: key for key, label in SOURCE_LABELS.items()}


def parse_source(value: Any) -> str:
    """「来源」列 → 内部键。认中文标签（早读/手动…），也认英文键；都不认就原样留着。"""
    text = normalize_text(value)
    if not text:
        return 'import'
    if text in SOURCE_KEY_BY_LABEL:
        return SOURCE_KEY_BY_LABEL[text]
    if text in SOURCE_LABELS:
        return text
    return text


def parse_student_table(
    rows: Sequence[Sequence[Any]], *, source_path: Path | None = None
) -> ImportResult:
    """解析学生名单表（第一行是表头）。只解析，不改数据。"""
    body = _strip_blank(rows)
    if not body:
        return ImportResult(
            ok=False,
            reason='empty',
            error='文件里没有任何内容（连表头都没有）。',
            source_path=source_path,
        )
    header_line, header = body[0]
    cols = resolve_columns(header, STUDENT_ALIASES)
    if 'name' not in cols:
        seen = '、'.join(x for x in (normalize_text(c) for c in header) if x) or '（空表头）'
        return ImportResult(
            ok=False,
            reason='no-name-column',
            error=f'第{header_line}行是表头，但里面找不到「姓名」列，读到的是：{seen}。学生名单至少要有「姓名」列。',
            source_path=source_path,
        )

    students: list[StudentRow] = []
    warnings: list[str] = []
    seen_names: set[str] = set()
    total = 0
    skipped = 0

    for line_no, row in body[1:]:
        total += 1
        name = normalize_text(cell_at(row, cols, 'name'))
        if not name:
            skipped += 1
            warnings.append(f'第{line_no}行：「姓名」为空，已跳过。')
            continue
        if name in seen_names:
            skipped += 1
            warnings.append(f'第{line_no}行：{name} 在文件里重复出现，只保留第一条，已跳过。')
            continue
        seen_names.add(name)

        raw_points = cell_at(row, cols, 'points')
        points_given = bool(normalize_text(raw_points))
        points = parse_int(raw_points, 0) if not points_given else parse_int(raw_points)
        if points is None:
            warnings.append(
                f'第{line_no}行：{name} 的积分「{normalize_text(raw_points)}」看不懂，按 0 处理。'
            )
            points = 0

        row_i = parse_int(cell_at(row, cols, 'row'))
        col_i = parse_int(cell_at(row, cols, 'col'))
        if (row_i is None) != (col_i is None):
            warnings.append(
                f'第{line_no}行：{name} 只给了行或只给了列，座位信息不完整，改为自动落座。'
            )
            row_i = col_i = None

        students.append(
            StudentRow(
                name=name,
                points=points,
                points_given=points_given,
                row=row_i,
                col=col_i,
                rank=parse_int(cell_at(row, cols, 'rank')),
                line=line_no,
            )
        )

    if total == 0:
        return ImportResult(
            ok=False,
            reason='empty',
            error=f'表头在第{header_line}行，但下面一条数据也没有。',
            source_path=source_path,
        )
    if not students:
        head = '；'.join(warnings[:3])
        return ImportResult(
            ok=False,
            reason='no-valid-row',
            error=f'{total} 行数据没有一行能用（{head}）。请检查「姓名」列是否整列都是空的。',
            warnings=warnings, total_rows=total,
            skipped_rows=skipped,
            source_path=source_path,
        )
    return ImportResult(
        ok=True,
        message=f'解析到 {len(students)} 名学生',
        warnings=warnings, students=students,
        total_rows=total, skipped_rows=skipped,
        source_path=source_path,
    )


def parse_history_table(
    rows: Sequence[Sequence[Any]], *, source_path: Path | None = None
) -> ImportResult:
    """解析积分记录表（第一行是表头）。只解析，不改数据。"""
    body = _strip_blank(rows)
    if not body:
        return ImportResult(
            ok=False,
            reason='empty',
            error='文件里没有任何内容（连表头都没有）。',
            source_path=source_path,
        )
    header_line, header = body[0]
    cols = resolve_columns(header, HISTORY_ALIASES)
    missing = [f for f in ('name', 'delta') if f not in cols]
    if missing:
        seen = '、'.join(x for x in (normalize_text(c) for c in header) if x) or '（空表头）'
        need = '、'.join('「学生」' if f == 'name' else '「变化」' for f in missing)
        return ImportResult(
            ok=False,
            reason='missing-column',
            error=f'第{header_line}行是表头，但缺少 {need} 列，读到的是：{seen}。积分记录至少要有「学生」和「变化」两列。',
            source_path=source_path,
        )

    records: list[HistoryRow] = []
    warnings: list[str] = []
    total = 0
    skipped = 0
    now = int(time.time() * 1000)

    for line_no, row in body[1:]:
        total += 1
        name = normalize_text(cell_at(row, cols, 'name'))
        if not name:
            skipped += 1
            warnings.append(f'第{line_no}行：「学生」为空，已跳过。')
            continue

        raw_delta = cell_at(row, cols, 'delta')
        delta = parse_int(raw_delta)
        if delta is None:
            skipped += 1
            warnings.append(
                f'第{line_no}行：{name} 的变化「{normalize_text(raw_delta)}」看不懂，已跳过。'
            )
            continue
        if delta == 0:
            skipped += 1
            warnings.append(f'第{line_no}行：{name} 的变化是 0，没有意义，已跳过。')
            continue

        raw_ts = cell_at(row, cols, 'ts')
        ts = parse_ts(raw_ts, None)
        if ts is None:
            if normalize_text(raw_ts):
                warnings.append(
                    f'第{line_no}行：时间「{normalize_text(raw_ts)}」看不懂，按当前时间处理。'
                )
            ts = now

        records.append(
            HistoryRow(
                student_id=normalize_text(cell_at(row, cols, 'id')),
                student_name=name,
                delta=delta,
                ts=ts,
                reason=normalize_text(cell_at(row, cols, 'reason')),
                source=parse_source(cell_at(row, cols, 'source')),
                seq=parse_int(cell_at(row, cols, 'seq')),
                line=line_no,
            )
        )

    if total == 0:
        return ImportResult(
            ok=False,
            reason='empty',
            error=f'表头在第{header_line}行，但下面一条数据也没有。',
            source_path=source_path,
        )
    if not records:
        head = '；'.join(warnings[:3])
        return ImportResult(
            ok=False,
            reason='no-valid-row',
            error=f'{total} 行数据没有一行能用（{head}）。',
            warnings=warnings, total_rows=total,
            skipped_rows=skipped,
            source_path=source_path,
        )
    return ImportResult(
        ok=True,
        message=f'解析到 {len(records)} 条记录',
        warnings=warnings, records=records,
        total_rows=total, skipped_rows=skipped,
        source_path=source_path,
    )


def _free_seats(state: AppState, taken: set[tuple[int, int]]) -> list[tuple[int, int]]:
    layout = state.layout
    return [
        (r, c)
        for r in range(layout.rows)
        for c in range(layout.cols)
        if (r, c) not in taken
    ]


def _seat_capacity(state: AppState) -> int:
    return max(0, state.layout.rows) * max(0, state.layout.cols)


def _expand_layout_for(state: AppState, need_total: int) -> None:
    """座位不够时按需加行（只加行，不动列分组，避免座位表变形）。"""
    layout = state.layout
    cols = max(1, layout.cols)
    rows = max(layout.rows, -(-need_total // cols))
    state.layout = resolve_layout(
        Layout(
            rows=rows, cols=cols, col_groups=list(layout.col_groups), podium=layout.podium
        )
    )


def apply_students(
    state: AppState,
    result: ImportResult,
    *,
    mode: str = 'merge',
    force: bool = False,
    auto_expand_seats: bool = False,
    apply_points: bool = False,
) -> ApplyResult:
    """把解析好的学生名单落进 state。

参数：
    mode="merge"   同名已存在则跳过（不动现有积分），新名字追加并按需落座（默认）。
    mode="replace" 整体替换学生名单。**保护性闸门**：现有 history 非空时拒绝
                   （替换会让记录的 studentId 失去归属、积分重算时被忽略），
                   确要替换须 force=True，并在 details 里看到孤儿记录条数。
    auto_expand_seats=True  座位不够时自动加行；默认 False，直接拒绝并说明缺多少。
    apply_points=True  把文件里的「积分」列写进学生积分。

关于「积分」列：本系统的学生积分是**历史记录的推导值**（见 core/scoring.recalc_points，
Store 加载时无条件重算），所以默认**不写入**文件里的积分，只给警告，
免得用户以为导进去了、下次开程序又变 0。要还原积分请导入「积分记录」；
确实只需要一个快照（例如导出给人看）时可显式 apply_points=True，
此时仍会警告「下次加载会被历史重算覆盖」。
校验不过时 state 完全不被修改。
"""
    if not result.ok:
        return ApplyResult(
            ok=False,
            error=result.error,
            reason=result.reason or 'import-failed',
        )
    if mode not in ('merge', 'replace'):
        return ApplyResult(
            ok=False,
            reason='bad-mode',
            error=f'不认识的导入模式「{mode}」，只支持 merge（合并）或 replace（替换）。',
        )

    warnings = list(result.warnings)
    details: list[str] = []

    if mode == 'replace' and state.history and not force:
        return ApplyResult(
            ok=False,
            reason='history-guard',
            error=f'当前有 {len(state.history)} 条积分记录。整体替换学生名单后，这些记录的归属学生会消失、积分重算时被忽略。若确定要替换，请确认风险后重试（force）。',
        )

    existing_names = {s.name.strip() for s in state.students}
    new_students: list[Student] = []
    taken = set() if mode == 'replace' else {s.seat() for s in state.students}

    skipped = 0
    moved = 0
    points_notice = 0

    for row in result.students:
        if row.name in existing_names:
            skipped += 1
            details.append(f'第{row.line}行：{row.name} 已在名单中，跳过（未改动其积分）。')
            continue
        if row.points_given and row.points != 0:
            points_notice += 1

        seat = None
        if row.row is not None and row.col is not None:
            if 0 <= row.row < state.layout.rows and 0 <= row.col < state.layout.cols:
                if (row.row, row.col) not in taken:
                    seat = (row.row, row.col)
                else:
                    moved += 1
                    details.append(
                        f'第{row.line}行：{row.name} 的座位（{row.row},{row.col}）已被占用，改为自动落座。'
                    )
            else:
                moved += 1
                details.append(
                    f'第{row.line}行：{row.name} 的座位（{row.row},{row.col}）超出当前布局（{state.layout.rows} 行 × {state.layout.cols} 列），改为自动落座。'
                )
        if seat is None:
            free = _free_seats(state, taken)
            if not free:
                if not auto_expand_seats:
                    need = len(new_students) + 1
                    return ApplyResult(
                        ok=False,
                        reason='no-seat',
                        error=f'座位不够：还要放 {need} 人以上，而当前布局 {state.layout.rows} 行 × {state.layout.cols} 列只剩 {len(_free_seats(state, taken))} 个空位。请先在「布局」里增加行列，或改用自动扩展座位。',
                        warnings=warnings, details=details,
                        skipped=skipped,
                    )
                _expand_layout_for(state, len(taken) + len(result.students) + 1)
                free = _free_seats(state, taken)
                if not free:
                    return ApplyResult(
                        ok=False,
                        reason='no-seat',
                        error='自动扩展座位后仍然找不到空位，已中止导入（数据未改动）。',
                        warnings=warnings, details=details,
                        skipped=skipped,
                    )
            seat = free[0]
        taken.add(seat)
        existing_names.add(row.name)
        new_students.append(
            Student(
                name=row.name,
                row=seat[0],
                col=seat[1],
                points=row.points if apply_points else 0,
            )
        )
        details.append(f'第{row.line}行：新增 {row.name} → 座位（{seat[0]},{seat[1]}）。')

    if not new_students and mode == 'merge':
        return ApplyResult(
            ok=False,
            reason='nothing-to-do',
            error='文件里的学生都已经在名单里了，没有任何变化。',
            warnings=warnings, details=details,
            skipped=skipped,
        )

    if points_notice:
        if apply_points:
            warnings.append(
                f'已按文件写入 {points_notice} 人的「积分」。注意：本系统积分由「积分记录」推导，下次加载存档时会按记录重算，这里写入的数值会被覆盖。'
            )
        else:
            warnings.append(
                f'文件里有 {points_notice} 人带了非 0 的「积分」，但本系统的积分由「积分记录」推导，导入学生名单不会写入积分（否则下次启动重算就归零）。要保留积分请一并导入积分记录。'
            )

    if mode == 'replace':
        orphan = len(state.history)
        state.students = new_students
        if orphan:
            details.append(
                f'已替换名单；原有 {orphan} 条记录暂时失去归属学生（记录本身未删除）。'
            )
        return ApplyResult(
            ok=True,
            reason='ok',
            message=f'已替换为 {len(new_students)} 名学生',
            added=len(new_students), updated=0, skipped=skipped, moved=moved,
            warnings=warnings, details=details,
        )
    state.students.extend(new_students)
    return ApplyResult(
        ok=True,
        reason='ok',
        message=f'新增 {len(new_students)} 名学生',
        added=len(new_students), updated=0, skipped=skipped, moved=moved,
        warnings=warnings, details=details,
    )


def apply_history(
    state: AppState,
    result: ImportResult,
    *,
    create_missing: bool = False,
    skip_duplicates: bool = True,
    auto_expand_seats: bool = False,
) -> ApplyResult:
    """把解析好的积分记录落进 state。

* 记录里的学生名先在名单里找；找不到时默认**拒绝这一行**，
  `create_missing=True` 才会自动建档落座（会写进 details，看得见）。
* `skip_duplicates=True`：与学生名、时间、变化、原因都相同的已有记录视为重复导入，跳过。
  （同一份文件导两遍不会把分加两遍。）
* 落库后调用 `scoring.recalc_points` 对齐积分——这是本系统积分的唯一口径。
* 一行都落不下时整体拒绝，state 不动。
"""
    if not result.ok:
        return ApplyResult(
            ok=False,
            error=result.error,
            reason=result.reason or 'import-failed',
        )

    warnings = list(result.warnings)
    details: list[str] = []

    # 同名学生在名单里可能出现多次；这些名字下的记录无法定位到具体学生。
    _name_counts: dict[str, int] = {}
    for _s in state.students:
        _k = _s.name.strip()
        _name_counts[_k] = _name_counts.get(_k, 0) + 1
    dup_names = {k for k, v in _name_counts.items() if v > 1}
    by_id = {s.id: s for s in state.students}
    by_name = {
        s.name.strip(): s
        for s in state.students
        if _name_counts[s.name.strip()] == 1
    }
    taken = {s.seat() for s in state.students}
    dup_keys = (
        {(h.student_name, h.ts, h.delta, h.reason) for h in state.history}
        if skip_duplicates
        else set()
    )

    planned: list[tuple[str, int, int, str, str, int]] = []
    pending_create: list[str] = []
    skipped = 0
    dup = 0
    unknown = 0

    for row in result.records:
        key = (row.student_name, row.ts, row.delta, row.reason)
        if skip_duplicates and key in dup_keys:
            skipped += 1
            dup += 1
            continue
        dup_keys.add(key)

        sid = (getattr(row, 'student_id', '') or '').strip()
        if sid and sid in by_id and row.student_name in dup_names:
            # 重名学生 + 文件里带了学生ID：用「名字#ID」这个别名精确定位
            alias = f'{row.student_name}#{sid}'
            by_name[alias] = by_id[sid]
            row.student_name = alias

        if row.student_name not in by_name and row.student_name not in pending_create:
            if row.student_name in dup_names:
                skipped += 1
                unknown += 1
                details.append(
                    f'第{row.line}行：名单里有 {_name_counts[row.student_name]} 个「{row.student_name}」，文件里又没带「学生ID」，无法判定这条该记给谁，已跳过。（重新导出时会自动带上学生ID）'
                )
                continue
            if not create_missing:
                skipped += 1
                unknown += 1
                details.append(f'第{row.line}行：名单里没有「{row.student_name}」，已跳过。')
                continue
            pending_create.append(row.student_name)

        planned.append(
            (row.student_name, row.delta, row.ts, row.reason, row.source, row.line)
        )

    if not planned:
        why: list[str] = []
        if unknown:
            why.append(f'{unknown} 条记录的学生不在名单里')
        if dup:
            why.append(f'{dup} 条与现有记录重复')
        head = '；'.join(why) or '没有可用记录'
        return ApplyResult(
            ok=False,
            reason='no-valid-row',
            error=f'没有任何记录能导入（{head}）。若名单还没建，请先导入学生名单，或允许自动为学生建档。',
            warnings=warnings, details=details,
            skipped=skipped,
        )

    if pending_create:
        free = _free_seats(state, taken)
        if len(free) < len(pending_create):
            if not auto_expand_seats:
                return ApplyResult(
                    ok=False,
                    reason='no-seat',
                    error=f'需要为 {len(pending_create)} 名学生自动建档，但当前布局只剩 {len(free)} 个空位（{state.layout.rows} 行 × {state.layout.cols} 列）。请先扩大布局，或改用自动扩展座位。',
                    warnings=warnings, details=details,
                    skipped=skipped,
                )
            _expand_layout_for(state, len(taken) + len(pending_create) + 1)
            free = _free_seats(state, taken)
            if len(free) < len(pending_create):
                return ApplyResult(
                    ok=False,
                    reason='no-seat',
                    error='自动扩展座位后仍然放不下要建档的学生，已中止导入（数据未改动）。',
                    warnings=warnings, details=details,
                    skipped=skipped,
                )

    created = 0
    for i, name in enumerate(pending_create):
        seat = free[i]
        taken.add(seat)
        student = Student(name=name, row=seat[0], col=seat[1])
        state.students.append(student)
        by_name[name] = student
        created += 1
        details.append(f'自动建档：{name} → 座位（{seat[0]},{seat[1]}）。')

    for name, delta, ts, reason, source, _line in planned:
        student = by_name[name]
        state.history.append(
            HistoryEntry(
                seq=state.next_seq(),
                ts=ts,
                student_id=student.id,
                student_name=student.name,
                delta=delta,
                reason=reason,
                source=source,
            )
        )

    scoring.recalc_points(state)

    return ApplyResult(
        ok=True,
        reason='ok',
        message=f'导入 {len(planned)} 条记录' + (f'，自动建档 {created} 人' if created else ''),
        added=len(planned),
        updated=created,
        skipped=skipped,
        warnings=warnings, details=details,
    )


def write_via_temp(path: Path, writer: Callable[[Path], None]) -> int:
    """先写临时文件再 os.replace —— 写到一半失败不会留下半边文件。

writer 拿到临时文件路径，自己决定怎么写（csv 文本 / openpyxl 工作簿 / docx）。
返回最终文件字节数。
"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f'.tmp{os.getpid()}')
    try:
        writer(tmp)
        size = tmp.stat().st_size
        os.replace(tmp, path)
        return size
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def atomic_write_text(path: Path, text: str) -> int:
    """原子写文本（UTF-8 with BOM —— Excel 打开 CSV 不乱码的关键）。"""

    def _write(tmp: Path) -> None:
        with open(tmp, 'w', encoding='utf-8-sig', newline='') as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())

    return write_via_temp(path, _write)


from app.io import backup, csv_io, docx_io, xlsx_io

__all__ = [
    'IoResult',
    'ExportResult',
    'ImportResult',
    'ApplyResult',
    'StudentRow',
    'HistoryRow',
    'normalize_text',
    'parse_int',
    'parse_ts',
    'fmt_ts',
    'read_text_file',
    'resolve_columns',
    'cell_at',
    'parse_source',
    'parse_student_table',
    'parse_history_table',
    'apply_students',
    'apply_history',
    'write_via_temp',
    'atomic_write_text',
    'STUDENT_ALIASES',
    'HISTORY_ALIASES',
    'SOURCE_LABELS',
    'SOURCE_KEY_BY_LABEL',
    'source_label',
    'backup',
    'csv_io',
    'docx_io',
    'xlsx_io',
]
