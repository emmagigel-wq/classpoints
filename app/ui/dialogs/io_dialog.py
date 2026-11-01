"""导入导出中心。

一个刻意的小设计：**app.io 是可选依赖**（openpyxl / python-docx 可能没装）。
所以这里全部用 try import 兜住，缺依赖时给出可读提示，而不是让主窗口在
启动阶段就因为缺一个库而白屏——那是上一代最容易踩的坑。
"""

from __future__ import annotations

from pathlib import Path

from PyQt5.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from app.ui import theme


def _msg(result) -> str:
    """把 io 层的结果对象翻成人话。"""
    if result is None:
        return '没有返回结果'
    ok = bool(getattr(result, 'ok', False))
    if ok:
        parts = []
        for key in ('added', 'updated', 'skipped', 'moved'):
            value = getattr(result, key, 0)
            if not value:
                continue
            parts.append(f'{key}={value}')
        tail = '，'.join(parts) if parts else ''
        message = str(getattr(result, 'message', '') or '完成')
        return f'成功：{message} {tail}'.strip()
    reason = getattr(result, 'reason', '') or ''
    error = getattr(result, 'error', '') or '操作失败'
    return f'失败（{reason}）：{error}' if reason else f'失败：{error}'


class ImportExportDialog(QDialog):
    """导出 / 导入 / 备份恢复。"""

    def __init__(self, store, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._store = store
        self.setWindowTitle('导入导出')
        self.setModal(True)
        self.setMinimumWidth(520)
        self._io = None
        self._build()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 12)
        root.setSpacing(10)

        export_box = QGroupBox('导出', self)
        export_lay = QHBoxLayout(export_box)
        export_lay.addWidget(self._btn('学生名单 CSV', self._export_students_csv, export_box))
        export_lay.addWidget(self._btn('积分记录 CSV', self._export_history_csv, export_box))
        export_lay.addWidget(self._btn('Excel 工作簿', self._export_xlsx, export_box))
        export_lay.addWidget(self._btn('Word 报告', self._export_docx, export_box))
        root.addWidget(export_box)

        import_box = QGroupBox('导入（合并，不覆盖现有记录）', self)
        import_lay = QHBoxLayout(import_box)
        import_lay.addWidget(self._btn('导入学生名单', self._import_students, import_box))
        import_lay.addWidget(self._btn('导入积分记录', self._import_history, import_box))
        root.addWidget(import_box)

        backup_box = QGroupBox('备份与恢复', self)
        backup_lay = QHBoxLayout(backup_box)
        backup_lay.addWidget(self._btn('立即备份', self._backup, backup_box))
        backup_lay.addWidget(self._btn('从备份恢复…', self._restore, backup_box))
        root.addWidget(backup_box)

        self._log = QTextEdit(self)
        self._log.setReadOnly(True)
        self._log.setMinimumHeight(130)
        self._log.setStyleSheet(
            f'background: {theme.CARD}; border: 1px solid {theme.BORDER}; border-radius: 6px; font-size: 12px;'
        )
        root.addWidget(self._log, 1)

        self._hint = QLabel('', self)
        self._hint.setWordWrap(True)
        self._hint.setStyleSheet(
            f'color: {theme.TEXT_WEAK}; background: transparent; font-size: 12px;'
        )
        root.addWidget(self._hint)

        box = QDialogButtonBox(QDialogButtonBox.Close, self)
        box.button(QDialogButtonBox.Close).setText('关闭')
        box.rejected.connect(self.reject)
        root.addWidget(box)

        self._probe_io()

    def _btn(self, text: str, slot, parent: QWidget) -> QPushButton:
        b = QPushButton(text, parent)
        b.clicked.connect(slot)
        return b

    def _probe_io(self) -> None:
        try:
            from app import io as io_pkg
            self._io = io_pkg
        except Exception as exc:
            self._io = None
            self._write(f'import 层不可用：{exc}')
            self._hint.setText('导入导出在当前安装里不可用（缺少依赖），其余功能不受影响。')
            return None

    def _write(self, text: str) -> None:
        self._log.append(text)

    def _need_io(self) -> bool:
        if self._io is None:
            self._write('导入导出层不可用，已跳过')
            return False
        return True

    def _save_path(self, title: str, default_name: str, pattern: str) -> str:
        path, _ = QFileDialog.getSaveFileName(self, title, default_name, pattern)
        return path or ''

    def _open_path(self, title: str, pattern: str) -> str:
        path, _ = QFileDialog.getOpenFileName(self, title, '', pattern)
        return path or ''

    def _export_students_csv(self) -> None:
        if not self._need_io():
            return None
        path = self._save_path('导出学生名单', '学生名单.csv', 'CSV 文件 (*.csv)')
        if not path:
            return None
        from app.io import csv_io
        self._write(_msg(csv_io.export_students(self._store.state, path)))

    def _export_history_csv(self) -> None:
        if not self._need_io():
            return None
        path = self._save_path('导出积分记录', '积分记录.csv', 'CSV 文件 (*.csv)')
        if not path:
            return None
        from app.io import csv_io
        self._write(_msg(csv_io.export_history(self._store.state, path)))

    def _export_xlsx(self) -> None:
        if not self._need_io():
            return None
        path = self._save_path('导出 Excel', '班级积分.xlsx', 'Excel 文件 (*.xlsx)')
        if not path:
            return None
        try:
            from app.io import xlsx_io
        except Exception as exc:
            self._write(f'Excel 导出不可用（需要 openpyxl）：{exc}')
            return None
        self._write(_msg(xlsx_io.export_workbook(self._store.state, path)))

    def _export_docx(self) -> None:
        if not self._need_io():
            return None
        path = self._save_path('导出 Word 报告', '班级积分报告.docx', 'Word 文件 (*.docx)')
        if not path:
            return None
        try:
            from app.io import docx_io
        except Exception as exc:
            self._write(f'Word 导出不可用（需要 python-docx）：{exc}')
            return None
        self._write(_msg(docx_io.export_report(self._store.state, path)))

    def _import_students(self) -> None:
        if not self._need_io():
            return None
        path = self._open_path('选择学生名单', '表格文件 (*.csv *.xlsx);;所有文件 (*.*)')
        if not path:
            return None
        result = self._parse_students(path)
        if result is None:
            return None
        applied = self._io.apply_students(
            self._store.state,
            result,
            mode='merge',
            auto_expand_seats=True,
        )
        self._write(_msg(applied))

    def _parse_students(self, path: str):
        from app.io import csv_io
        suffix = Path(path).suffix.lower()
        if suffix in {'.xlsx', '.xlsm'}:
            try:
                from app.io import xlsx_io
            except Exception as exc:
                self._write(f'Excel 读取不可用（需要 openpyxl）：{exc}')
                return None
            return xlsx_io.import_students(path)
        return csv_io.import_students(path)

    def _import_history(self) -> None:
        if not self._need_io():
            return None
        path = self._open_path('选择积分记录', '表格文件 (*.csv *.xlsx);;所有文件 (*.*)')
        if not path:
            return None
        result = self._parse_history(path)
        if result is None:
            return None
        applied = self._io.apply_history(self._store.state, result)
        self._write(_msg(applied))

    def _parse_history(self, path: str):
        from app.io import csv_io
        suffix = Path(path).suffix.lower()
        if suffix in {'.xlsx', '.xlsm'}:
            try:
                from app.io import xlsx_io
            except Exception as exc:
                self._write(f'Excel 读取不可用（需要 openpyxl）：{exc}')
                return None
            return xlsx_io.import_history(path)
        return csv_io.import_history(path)

    def _backup(self) -> None:
        try:
            from app.io import backup
        except Exception as exc:
            self._write(f'备份不可用：{exc}')
            return None
        self._write(_msg(backup.create_backup(self._store.state)))

    def _restore(self) -> None:
        try:
            from app.io import backup
        except Exception as exc:
            self._write(f'恢复不可用：{exc}')
            return None
        path = self._open_path(
            '选择备份',
            '备份包或数据文件 (*.zip *.json);;备份包 (*.zip);;数据文件 (*.json);;所有文件 (*.*)',
        )
        if not path:
            return None
        if path.lower().endswith('.json'):
            self._restore_json(path)
            return None
        check = backup.inspect_backup(path)
        if not getattr(check, 'ok', False):
            self._write(_msg(check))
            return None
        result = backup.restore_backup(path)
        self._write(_msg(result))
        if getattr(result, 'ok', False) and getattr(result, 'state', None) is not None:
            self._store.replace_state(result.state, event_reason='restore')
            self._write('已用备份内容替换当前数据')

    def _restore_json(self, path: str) -> None:
        """导入 `.json`：既认程序自己的 data.json，也认网页版导出的备份。

        网页版的文件外面套了一层 `{"app":"class-points","state":{…}}` 信封，
        识别与剥壳统一放在 `app/io/legacy.py`，这里只负责把结果换成班级状态。
        先完整解析成 AppState 再替换——解析失败一个字节都不动。
        """
        from app.io import legacy
        try:
            state = legacy.load_state_file(path)
        except legacy.LegacyError as exc:
            self._write(str(exc))
            return None
        self._store.replace_state(state, event_reason='restore')
        self._write(f'已从 JSON 导入：{state.class_name} · {len(state.students)} 人 · {len(state.history)} 条记录')
