"""DOCX 导出：`班级积分报告`。

结构（对应需求 3）：
    标题「班级积分报告」
    班级名 + 生成时间
    概览（学生数 / 记录数 / 平均分）
    积分排行榜表格（名次 / 姓名 / 积分 / 加分量 / 扣分量）
    最近积分记录表格（时间 / 学生 / 变化 / 原因 / 来源）

只导出，不导入——docx 是给人看的交付物，不是数据往返格式。

python-docx 是可选依赖：缺库时返回 `ok=False, reason="missing-dependency"` 的可读错误，
不是抛 ImportError 把界面带崩（与本项目「不许静默失败、也不许炸给用户看」的约定一致）。
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path

from app.core import scoring
from app.core.models import AppState, source_label
from app.io import ExportResult, _export_fail, fmt_ts, write_via_temp

__all__ = ['export_report', 'REPORT_TITLE', 'RECENT_LIMIT']

REPORT_TITLE = '班级积分报告'
RECENT_LIMIT = 20


def _load_docx() -> tuple[object | None, str]:
    """延迟导入 python-docx；缺库时给出可读错误。"""
    try:
        import docx
    except ImportError as exc:
        return None, (
            f'缺少 python-docx，无法生成 Word 报告：{exc}'
            '。请安装后重试（pip install python-docx）。'
        )
    return docx, ''


def _fill_report(document: object, state: AppState, recent_limit: int) -> tuple[int, int]:
    """往 document 里填内容。返回 (排行榜行数, 记录行数)。"""
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Pt, RGBColor

    # 标题：居中
    heading = document.add_heading(REPORT_TITLE, level=0)
    heading.alignment = WD_ALIGN_PARAGRAPH.CENTER

    # 班级名 + 生成时间
    subtitle = document.add_paragraph()
    subtitle.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = subtitle.add_run(state.class_name)
    run.bold = True
    run.font.size = Pt(14)
    stamp = document.add_paragraph()
    stamp.alignment = WD_ALIGN_PARAGRAPH.CENTER
    stamp_run = stamp.add_run(
        '生成时间：' + _dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    )
    stamp_run.font.size = Pt(9)
    stamp_run.font.color.rgb = RGBColor(128, 128, 128)

    # 概览
    total_points = sum(s.points for s in state.students)
    average = total_points / len(state.students) if state.students else 0.0
    summary = document.add_paragraph()
    summary.add_run('概览：').bold = True
    summary.add_run(
        f'共 {len(state.students)} 名学生，{len(state.history)} 条积分记录，人均 '
        f'{average:.1f} 分。'
    )

    # 排行榜
    document.add_heading('积分排行榜', level=1)
    ranked = scoring.rank_with_ties(state)
    if not ranked:
        empty = document.add_paragraph('（当前还没有学生）')
        empty.runs[0].italic = True
    else:
        board = document.add_table(rows=1, cols=4)
        board.style = 'Table Grid'
        for i, text in enumerate(('名次', '姓名', '积分', '加/扣分')):
            cell = board.rows[0].cells[i]
            cell.text = text
            for para in cell.paragraphs:
                for r in para.runs:
                    r.bold = True
        for rank, student in ranked:
            stat = scoring.stats_of(state, student.id)
            cells = board.add_row().cells
            cells[0].text = str(rank)
            cells[1].text = student.name
            cells[2].text = str(student.points)
            cells[3].text = f'+{stat["plus"]} / -{stat["minus"]}'

    # 最近记录
    document.add_heading(f'最近 {recent_limit} 条积分记录', level=1)
    entries = scoring.history_sorted(state)[None:recent_limit]
    if not entries:
        empty2 = document.add_paragraph('（当前还没有积分记录）')
        empty2.runs[0].italic = True
    else:
        table = document.add_table(rows=1, cols=5)
        table.style = 'Table Grid'
        for i, text in enumerate(('时间', '学生', '变化', '原因', '来源')):
            cell = table.rows[0].cells[i]
            cell.text = text
            for para in cell.paragraphs:
                for r in para.runs:
                    r.bold = True
        for entry in entries:
            cells = table.add_row().cells
            cells[0].text = fmt_ts(entry.ts)
            cells[1].text = entry.student_name
            cells[2].text = f'{entry.delta:+d}' if entry.delta else '0'
            cells[3].text = entry.reason or '—'
            cells[4].text = source_label(entry.source)

    # 脚注
    footer = document.add_paragraph()
    footer_run = footer.add_run(
        '本报告由「班级积分系统」导出；学生积分为积分记录的累计结果。'
    )
    footer_run.font.size = Pt(8)
    footer_run.font.color.rgb = RGBColor(128, 128, 128)

    return len(ranked), len(entries)


def export_report(
    state: AppState, path: str | Path, *, recent_limit: int = RECENT_LIMIT
) -> ExportResult:
    """导出「班级积分报告」docx。

    `recent_limit` 控制最近记录表的条数。名单为空不算失败——
    报告照样生成，只是表格里写「（当前还没有学生）」，并在 warnings 里说明。
    """
    target = Path(path)
    limit = max(0, int(recent_limit))

    docx, err = _load_docx()
    if docx is None:
        return _export_fail('missing-dependency', err, target)

    counts: list[tuple[int, int]] = []

    def _write(tmp: Path) -> None:
        document = docx.Document()
        counts.append(_fill_report(document, state, limit))
        document.save(str(tmp))

    try:
        size = write_via_temp(target, _write)
    except OSError as exc:
        return _export_fail(
            'io', f'导出 Word 报告失败：{exc}（文件可能正被 Word 打开）', target
        )
    except Exception as exc:
        return _export_fail('unexpected', f'生成 Word 报告时出错：{exc}', target)

    n_students, n_records = counts[0] if counts else (0, 0)
    notes: list[str] = []
    if not state.students:
        notes.append('当前名单是空的，报告里的排行榜只有表头说明。')
    if not state.history:
        notes.append('当前没有积分记录，报告里的记录表只有表头说明。')
    if state.history and len(state.history) > n_records:
        notes.append(f'报告只列最近 {n_records} 条，共 {len(state.history)} 条。')

    return ExportResult(
        ok=True,
        reason='ok', path=target, rows=n_students + n_records, bytes_written=size,
        message=f'已生成报告：{n_students} 名学生、{n_records} 条近期记录',
        warnings=notes,
    )
