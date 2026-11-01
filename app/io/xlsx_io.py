"""XLSX 导入导出：学生名单 + 积分记录两个 sheet。

用 openpyxl。相比 CSV 多做三件事（也正是用户选 xlsx 的原因）：
表头样式、按内容估的列宽、冻结首行。

导入侧与 CSV **共用** `app.io.parse_student_table` / `parse_history_table`，
所以两种格式对「缺列怎么算、坏行怎么报」的判定完全一致——
两套解析逻辑迟早会分叉，这里从结构上避免。

对外函数：
    export_workbook(state, path)          -> ExportResult（两个 sheet 一次导出）
    import_students(path, sheet=None)     -> ImportResult（只解析）
    import_history(path, sheet=None)      -> ImportResult（只解析）
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path
from typing import Any

from app.core import scoring
from app.core.models import AppState, source_label
from app.io import (
    ExportResult,
    ImportResult,
    _export_fail,
    fmt_ts,
    parse_history_table,
    parse_student_table,
    write_via_temp,
)

__all__ = [
    'export_workbook',
    'import_students',
    'import_history',
    'STUDENT_SHEET',
    'HISTORY_SHEET',
    'STUDENT_HEADER',
    'HISTORY_HEADER',
]

STUDENT_SHEET = '学生名单'
HISTORY_SHEET = '积分记录'
STUDENT_HEADER = ['姓名', '积分', '排名']
HISTORY_HEADER = ['序号', '时间', '学生', '变化', '原因', '来源', '学生ID']

# 表头底色 / 字色（ARGB，openpyxl 要带 FF 前缀）
HEADER_FILL = 'FFDDEBF7'
HEADER_FONT_COLOR = 'FF1F3864'
MIN_WIDTH = 8
MAX_WIDTH = 42


def _load_openpyxl() -> tuple[Any, str]:
    """延迟导入 openpyxl。缺库时返回可读错误而不是 ImportError 崩掉程序。"""
    try:
        import openpyxl
    except ImportError as exc:
        return None, (
            f'缺少 openpyxl，无法读写 Excel 文件：{exc}'
            '。请安装后重试（pip install openpyxl）。'
        )
    return openpyxl, ''


def _display_width(value: Any) -> int:
    """估算显示宽度：中日韩字符算 2 格，其余算 1 格。"""
    text = '' if value is None else str(value)
    width = 0
    for ch in text:
        width += 2 if ('ᄀ' <= ch <= '\ud7ff' or '\uf900' <= ch <= 'ￜ'
                       or '\u3000' <= ch <= '\u303f') else 1
    return width


def _style_sheet(ws: Any, header: list[str], table: list[list[Any]]) -> None:
    """套表头样式、按内容定列宽、冻结首行。"""
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    thin = Side(style='thin', color='FFB4C6E7')
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    for idx, _name in enumerate(header, start=1):
        cell = ws.cell(row=1, column=idx)
        cell.fill = PatternFill('solid', fgColor=HEADER_FILL)
        cell.font = Font(bold=True, color=HEADER_FONT_COLOR)
        cell.alignment = Alignment(horizontal='center', vertical='center')
        cell.border = border
    ws.row_dimensions[1].height = 22

    widths = [_display_width(name) + 4 for name in header]
    for row in table:
        for i, value in enumerate(row):
            if i < len(widths):
                widths[i] = max(widths[i], _display_width(value) + 3)
    for i, width in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = max(MIN_WIDTH, min(MAX_WIDTH, width))

    ws.freeze_panes = 'A2'


def _build_workbook(state: AppState) -> tuple[Any, int, int]:
    """构造工作簿，返回 (wb, 学生行数, 记录行数)。"""
    openpyxl, err = _load_openpyxl()
    if openpyxl is None:
        raise RuntimeError(err)

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = STUDENT_SHEET

    student_rows = []
    for rank, student in scoring.rank_with_ties(state):
        student_rows.append([student.name, student.points, rank])
    ws.append(list(STUDENT_HEADER))
    for row in student_rows:
        ws.append(row)
    _style_sheet(ws, STUDENT_HEADER, student_rows)

    ws2 = wb.create_sheet(HISTORY_SHEET)
    entries = list(reversed(scoring.history_sorted(state)))
    history_rows = []
    for i, entry in enumerate(entries, start=1):
        history_dt = None
        if entry.ts:
            try:
                history_dt = _dt.datetime.fromtimestamp(entry.ts / 1000)
            except (OverflowError, OSError, ValueError):
                history_dt = None
        history_rows.append(
            [
                entry.seq if entry.seq else i,
                history_dt if history_dt is not None else fmt_ts(entry.ts),
                entry.student_name,
                entry.delta,
                entry.reason,
                source_label(entry.source),
                # 学生ID 单独一列，重名时导入才挂得对
                entry.student_id,
            ]
        )
    ws2.append(list(HISTORY_HEADER))
    for row in history_rows:
        ws2.append(row)
    _style_sheet(ws2, HISTORY_HEADER, history_rows)

    for r in range(2, len(history_rows) + 2):
        cell = ws2.cell(row=r, column=2)
        if isinstance(cell.value, _dt.datetime):
            cell.number_format = 'yyyy-mm-dd hh:mm:ss'
    ws2.column_dimensions['B'].width = 20

    return wb, len(student_rows), len(history_rows)


def export_workbook(state: AppState, path: str | Path) -> ExportResult:
    """导出 xlsx：`学生名单` 与 `积分记录` 两个 sheet。"""
    target = Path(path)
    counts: list[tuple[int, int]] = []

    def _write(tmp: Path) -> None:
        wb, n_students, n_history = _build_workbook(state)
        counts.append((n_students, n_history))
        wb.save(tmp)

    try:
        size = write_via_temp(target, _write)
    except RuntimeError as exc:
        return _export_fail('missing-dependency', str(exc), target)
    except OSError as exc:
        return _export_fail(
            'io', f'导出 Excel 失败：{exc}（文件可能正被 Excel 打开）', target
        )
    except Exception as exc:
        return _export_fail('unexpected', f'导出 Excel 时出错：{exc}', target)

    n_students, n_history = counts[0] if counts else (0, 0)
    notes: list[str] = []
    if not state.students:
        notes.append('当前名单是空的，`学生名单` 表只有表头。')
    if not state.history:
        notes.append('当前没有积分记录，`积分记录` 表只有表头。')

    return ExportResult(
        ok=True, reason='ok', path=target, rows=n_students + n_history, bytes_written=size,
        message=f'已导出 {n_students} 名学生、{n_history} 条记录',
        warnings=notes,
    )


def _read_sheet(path: str | Path, sheet: str | None) -> tuple[list[list[Any]] | None, ImportResult | None]:
    """读 xlsx 的一个 sheet → 二维表。返回 (表, None) 或 (None, 失败结果)。"""
    p = Path(path)
    if not p.exists():
        return None, ImportResult(
            ok=False, reason='unreadable', error=f'文件不存在：{p}', source_path=p
        )
    if p.is_dir():
        return None, ImportResult(
            ok=False, reason='unreadable', error=f'这是一个目录，不是文件：{p}', source_path=p
        )

    openpyxl, err = _load_openpyxl()
    if openpyxl is None:
        return None, ImportResult(ok=False, reason='missing-dependency', error=err, source_path=p)

    try:
        wb = openpyxl.load_workbook(p, read_only=True, data_only=True)
    except Exception as exc:
        return None, ImportResult(
            ok=False,
            reason='not-xlsx',
            error=f'打不开这个 Excel 文件：{exc}。请确认它是 .xlsx 格式且没有损坏。',
            source_path=p,
        )

    try:
        if sheet:
            if sheet not in wb.sheetnames:
                return None, ImportResult(
                    ok=False,
                    reason='no-sheet',
                    error=f'工作簿里没有名为「{sheet}」的工作表。现有工作表：'
                          f'{"、".join(wb.sheetnames)}',
                    source_path=p,
                )
            ws = wb[sheet]
        else:
            ws = wb[wb.sheetnames[0]]
        table = [list(row) for row in ws.iter_rows(values_only=True)]
    except Exception as exc:
        return None, ImportResult(
            ok=False,
            reason='unreadable',
            error=f'读取工作表内容失败：{exc}',
            source_path=p,
        )
    finally:
        try:
            wb.close()
        except Exception:
            pass

    if not table:
        return None, ImportResult(
            ok=False,
            reason='empty',
            error=f'工作表「{sheet or ""}」是空的（连表头都没有）。',
            source_path=p,
        )
    return table, None


def import_students(path: str | Path, *, sheet: str | None = None) -> ImportResult:
    """解析 xlsx 里的学生名单。**不改任何数据**。

    不给 sheet 时读第一个工作表，并按表头自动判断这是名单还是记录。
    """
    table, failure = _read_sheet(path, sheet)
    if failure is not None:
        return failure
    assert table is not None
    return parse_student_table(table, source_path=Path(path))


def import_history(path: str | Path, *, sheet: str | None = None) -> ImportResult:
    """解析 xlsx 里的积分记录。**不改任何数据**。

    默认找名为 `积分记录` 的工作表；没有就退回第一个。
    """
    p = Path(path)
    if sheet is None:
        openpyxl, err = _load_openpyxl()
        if openpyxl is None:
            return ImportResult(ok=False, reason='missing-dependency', error=err, source_path=p)
        try:
            wb = openpyxl.load_workbook(p, read_only=True, data_only=True)
            names = list(wb.sheetnames)
            wb.close()
        except Exception as exc:
            return ImportResult(
                ok=False,
                reason='not-xlsx',
                error=f'打不开这个 Excel 文件：{exc}。请确认它是 .xlsx 格式且没有损坏。',
                source_path=p,
            )
        sheet = HISTORY_SHEET if HISTORY_SHEET in names else None

    table, failure = _read_sheet(p, sheet)
    if failure is not None:
        return failure
    assert table is not None
    return parse_history_table(table, source_path=p)
