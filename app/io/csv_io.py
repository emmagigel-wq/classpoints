"""CSV 导入导出：学生名单与积分记录。

只用标准库 csv（不引第三方），编码一律 **utf-8-sig**：
带 BOM 的 UTF-8 是 Excel 在中文 Windows 上唯一能正确认出的 CSV 编码，
少了那个 BOM，双击打开就是一堆「锟斤拷」。

读取时兼容 utf-8-sig / gbk（Excel「另存为 CSV」在老版本上默认 GBK），
由 `app.io.read_text_file` 负责双编码尝试。

对外函数：
    export_students(state, path) -> ExportResult
    export_history(state, path, limit=None) -> ExportResult
    import_students(path)        -> ImportResult     （只解析）
    import_history(path)         -> ImportResult     （只解析）
解析与落库分开，是「解析失败就整体拒绝、一个字节都不改」的实现方式：
调用方先 `import_*` 拿到结果，确认 ok 后再 `app.io.apply_students / apply_history`。
"""

from __future__ import annotations

import csv
import io
from pathlib import Path

from app.core import scoring
from app.core.models import AppState, source_label
from app.io import (
    ExportResult,
    HistoryRow,
    ImportResult,
    StudentRow,
    _export_fail,
    fmt_ts,
    parse_history_table,
    parse_student_table,
    read_text_file,
    write_via_temp,
)

__all__ = [
    'export_students',
    'export_history',
    'import_students',
    'import_history',
    'STUDENT_HEADER',
    'HISTORY_HEADER',
]

STUDENT_HEADER = ['姓名', '积分', '排名']
HISTORY_HEADER = ['序号', '时间', '学生', '变化', '原因', '来源', '学生ID']

CSV_ENCODING = 'utf-8-sig'


def _to_text(rows: list[list[object]]) -> str:
    """二维表 → CSV 文本。

    `lineterminator="\r\n"`：RFC 4180 规定，也是 Excel 认行尾的前提。
    """
    buf = io.StringIO(newline='')
    writer = csv.writer(buf, lineterminator='\r\n')
    writer.writerows(rows)
    return buf.getvalue()


def export_students(state: AppState, path: str | Path) -> ExportResult:
    """导出学生名单：列固定为 姓名 / 积分 / 排名。

    排名用 `scoring.rank_with_ties`（同分并列且跳号 1,1,3），与界面排行榜同一口径。
    """
    target = Path(path)
    rows = [list(STUDENT_HEADER)]
    for rank, student in scoring.rank_with_ties(state):
        rows.append([student.name, student.points, rank])

    text = _to_text(rows)
    try:
        size = write_via_temp(target, lambda tmp: _write_csv(tmp, text))
    except OSError as exc:
        return _export_fail('io', f'导出学生名单失败：{exc}', target)
    except Exception as exc:
        return _export_fail('unexpected', f'导出学生名单时出错：{exc}', target)

    return ExportResult(
        ok=True,
        reason='ok',
        path=target,
        rows=len(rows) - 1,
        bytes_written=size,
        message=f'已导出 {len(rows) - 1} 名学生的名单',
        warnings=[] if state.students else ['当前名单是空的，导出的文件只有表头。'],
    )


def export_history(
    state: AppState, path: str | Path, *, limit: int | None = None
) -> ExportResult:
    """导出积分记录：列固定为 序号 / 时间 / 学生 / 变化 / 原因 / 来源 / 学生ID。

    最后一列是为了重名——没有它，导入时两个「张伟」的记录会全挂到后一个头上。

    `limit` 只导出最近 N 条（按时间倒序取前 N，但文件里仍按时间**正序**排，
    符合人读表的习惯）。
    """
    target = Path(path)
    entries = scoring.history_sorted(state)
    if limit is not None and limit >= 0:
        entries = entries[:limit]
    entries = list(reversed(entries))

    rows = [list(HISTORY_HEADER)]
    for i, entry in enumerate(entries, start=1):
        rows.append(
            [
                entry.seq if entry.seq else i,
                fmt_ts(entry.ts),
                entry.student_name,
                entry.delta,
                entry.reason,
                source_label(entry.source),
                entry.student_id,
            ]
        )

    text = _to_text(rows)
    try:
        size = write_via_temp(target, lambda tmp: _write_csv(tmp, text))
    except OSError as exc:
        return _export_fail('io', f'导出积分记录失败：{exc}', target)
    except Exception as exc:
        return _export_fail('unexpected', f'导出积分记录时出错：{exc}', target)

    notes: list[str] = []
    if state.history and len(rows) - 1 < len(state.history):
        notes.append(f'共 {len(state.history)} 条记录，本次按上限导出最近 {len(rows) - 1} 条。')
    if not state.history:
        notes.append('当前没有任何积分记录，导出的文件只有表头。')

    return ExportResult(
        ok=True,
        reason='ok',
        path=target,
        rows=len(rows) - 1,
        bytes_written=size,
        message=f'已导出 {len(rows) - 1} 条积分记录',
        warnings=notes,
    )


def _write_csv(tmp: Path, text: str) -> None:
    with open(tmp, 'w', encoding=CSV_ENCODING, newline='') as fh:
        fh.write(text)
        fh.flush()


def _read_table(path: str | Path) -> tuple[list[list[str]] | None, ImportResult | None]:
    """读 CSV → 二维表。返回 (表, None) 或 (None, 失败结果)。"""
    text, _encoding, err = read_text_file(path)
    if text is None:
        return None, ImportResult(ok=False, reason='unreadable', error=err, source_path=Path(path))
    if not text.strip():
        return None, ImportResult(
            ok=False,
            reason='empty',
            error=f'文件是空的：{Path(path)}',
            source_path=Path(path),
        )
    reader = csv.reader(io.StringIO(text, newline=''))
    return [list(row) for row in reader], None


def import_students(path: str | Path) -> ImportResult:
    """解析学生名单 CSV。**不改任何数据**；结果里有 students 供 `apply_students` 使用。"""
    table, failure = _read_table(path)
    if failure is not None:
        return failure
    assert table is not None
    return parse_student_table(table, source_path=Path(path))


def import_history(path: str | Path) -> ImportResult:
    """解析积分记录 CSV。**不改任何数据**；结果里有 records 供 `apply_history` 使用。"""
    table, failure = _read_table(path)
    if failure is not None:
        return failure
    assert table is not None
    return parse_history_table(table, source_path=Path(path))
