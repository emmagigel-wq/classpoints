"""安装向导的四个页面：欢迎 → 选择目录 → 安装进度 → 完成。"""

from __future__ import annotations

import subprocess
from pathlib import Path

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QIcon, QPixmap
from PyQt5.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWizardPage,
)

from installer import bundle, install_core, win_shortcut, winsys
from installer.constants import APP_EXE_NAME, APP_NAME
from installer.ui.style import ACCENT
from installer.ui.worker import InstallThread

PAGE_WELCOME = 0
PAGE_DIRECTORY = 1
PAGE_PROGRESS = 2
PAGE_FINISH = 3

_PAGE_TITLES = {
    PAGE_WELCOME: '欢迎使用',
    PAGE_DIRECTORY: '选择安装位置',
    PAGE_PROGRESS: '正在安装',
    PAGE_FINISH: '安装完成',
}


def _logo_pixmap(size: int) -> QPixmap | None:
    icon_path = bundle.icon_path()
    if icon_path is None:
        return None
    pixmap = QIcon(str(icon_path)).pixmap(size, size)
    return pixmap if not pixmap.isNull() else None


def _section_label(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName('sectionTitle')
    return label


class WelcomePage(QWizardPage):
    """欢迎页：介绍装什么、装多大、数据放哪。"""

    def __init__(
        self,
        manifest: dict,
        payload: install_core.PayloadInfo,
        parent=None,
    ) -> None:
        super().__init__(parent)
        version = manifest['version']
        self.setTitle(_PAGE_TITLES[PAGE_WELCOME])
        self.setSubTitle(f'{APP_NAME} v{version} 安装程序')

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 8, 4, 4)
        layout.setSpacing(14)

        header = QHBoxLayout()
        header.setSpacing(16)
        pixmap = _logo_pixmap(72)
        if pixmap is not None:
            logo = QLabel()
            logo.setPixmap(pixmap)
            logo.setFixedSize(76, 76)
            logo.setAlignment(Qt.AlignCenter)
            header.addWidget(logo)
        title_box = QVBoxLayout()
        title_box.setSpacing(4)
        hero = QLabel(APP_NAME)
        hero.setObjectName('heroTitle')
        subtitle = QLabel('班级评分 · 座位管理 · 手机查看')
        subtitle.setObjectName('heroSubtitle')
        title_box.addWidget(hero)
        title_box.addWidget(subtitle)
        title_box.addStretch(1)
        header.addLayout(title_box, 1)
        layout.addLayout(header)

        intro = QLabel(
            '本向导将把「' + APP_NAME + '」安装到你的电脑，整个过程不需要管理员权限。安装前请关闭正在运行的旧版本。'
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        card = QFrame()
        card.setObjectName('card')
        card_layout = QGridLayout(card)
        card_layout.setContentsMargins(18, 14, 18, 14)
        card_layout.setHorizontalSpacing(18)
        card_layout.setVerticalSpacing(8)
        rows = [
            ('要安装的程序', f'{APP_NAME} v{version}'),
            ('所需磁盘空间', winsys.format_bytes(install_core.required_bytes(payload))),
            ('安装位置', '下一步选择（默认装到当前用户目录）'),
            ('班级数据', f'{winsys.data_dir()}（卸载时保留）'),
        ]
        for row_index, (key, value) in enumerate(rows):
            key_label = QLabel(key)
            key_label.setObjectName('pathLabel')
            value_label = QLabel(value)
            value_label.setWordWrap(True)
            card_layout.addWidget(key_label, row_index, 0, Qt.AlignTop)
            card_layout.addWidget(value_label, row_index, 1)
        card_layout.setColumnStretch(1, 1)
        layout.addWidget(card)
        layout.addStretch(1)

        tip = QLabel('点击「下一步」开始安装。')
        tip.setObjectName('hintLabel')
        layout.addWidget(tip)

    def nextId(self) -> int:
        return PAGE_DIRECTORY


class DirectoryPage(QWizardPage):
    """目录选择页：可改路径、显示所需空间/剩余空间、覆盖安装提醒。"""

    def __init__(
        self,
        manifest: dict,
        payload: install_core.PayloadInfo,
        initial_dir: Path,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._version = manifest['version']
        self._required = install_core.required_bytes(payload)
        self._ok = False
        self._target_check = None

        self.setTitle(_PAGE_TITLES[PAGE_DIRECTORY])
        self.setSubTitle('程序会安装到下面的文件夹，点「浏览…」可以换一个位置。')

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 8, 4, 4)
        layout.setSpacing(10)

        layout.addWidget(_section_label('安装目录'))
        path_row = QHBoxLayout()
        path_row.setSpacing(8)
        self._edit = QLineEdit(str(initial_dir))
        self._edit.setClearButtonEnabled(True)
        self._browse = QPushButton('浏览…')
        self._browse.setFixedWidth(92)
        path_row.addWidget(self._edit, 1)
        path_row.addWidget(self._browse)
        layout.addLayout(path_row)

        self._space = QLabel()
        layout.addWidget(self._space)

        self._warning = QLabel()
        self._warning.setObjectName('warningLabel')
        self._warning.setWordWrap(True)
        self._warning.setVisible(False)
        layout.addWidget(self._warning)

        self._error = QLabel()
        self._error.setObjectName('errorLabel')
        self._error.setWordWrap(True)
        self._error.setVisible(False)
        layout.addWidget(self._error)

        layout.addStretch(1)

        hint = QLabel(
            f'班级数据保存在 {winsys.data_dir()}。升级、重装、卸载都不会删除它。'
        )
        hint.setObjectName('hintLabel')
        hint.setWordWrap(True)
        layout.addWidget(hint)

        # 路径输入连着磁盘查询，敲一个字查一次太奢侈：220ms 合并一次
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(220)
        self._debounce.timeout.connect(self._refresh)
        self._edit.textChanged.connect(self._schedule_refresh)
        self._browse.clicked.connect(self._on_browse)
        self._edit.returnPressed.connect(self._refresh)

        self._refresh()

    def nextId(self) -> int:
        return PAGE_PROGRESS

    def isComplete(self) -> bool:
        return self._ok

    def validatePage(self) -> bool:
        self._refresh()
        check = self._target_check
        if check is None or not check.ok:
            QMessageBox.warning(
                self,
                '安装目录不可用',
                (check.message if check else '请选择安装目录') or '请选择安装目录',
            )
            return False
        if check.free_bytes >= 0 and check.free_bytes < self._required:
            QMessageBox.warning(
                self,
                '磁盘空间不足',
                f'安装需要约 {winsys.format_bytes(self._required)}，所选磁盘只剩 '
                f'{winsys.format_bytes(check.free_bytes)}。',
            )
            return False
        wizard = self.wizard()
        if check.is_previous_install or check.nonempty:
            if check.is_previous_install:
                title = '覆盖安装'
                text = (
                    f'检测到该目录已安装 {APP_NAME}（v'
                    f'{install_core.installed_version() or "未知版本"}）。\n\n继续安装会覆盖程序文件，班级数据不受影响。是否继续？'
                )
            else:
                title = '目录不是空的'
                text = f'目录 {check.path} 不是空的，也不是{APP_NAME}的安装目录。\n\n继续安装会清空该目录中的内容，是否继续？'
            answer = QMessageBox.question(
                self,
                title,
                text,
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return False
            if wizard is not None:
                wizard.clean_existing = True
        elif wizard is not None:
            wizard.clean_existing = False
        return True

    def install_dir(self) -> Path:
        return Path(self._edit.text().strip()).expanduser()

    def _schedule_refresh(self) -> None:
        self._debounce.start()

    def _refresh(self) -> None:
        self._debounce.stop()
        raw = self._edit.text().strip()
        if raw:
            check = install_core.validate_target(Path(raw), check_free=True)
        else:
            check = install_core.TargetCheck(path=Path(''), ok=False, message='请选择安装目录')
        self._target_check = check

        free = check.free_bytes
        insufficient = False
        space_text = f'所需空间：{winsys.format_bytes(self._required)}'
        if free >= 0:
            space_text += f'\u3000\u3000{check.path.anchor or "目标磁盘"} 可用空间：{winsys.format_bytes(free)}'
            insufficient = free < self._required
        self._space.setText(space_text)
        self._space.setStyleSheet('color: #c0392b;' if insufficient else '')

        warning = check.warning
        self._warning.setText(warning)
        self._warning.setVisible(bool(warning))

        show_error = bool(raw) and not check.ok and bool(check.message)
        self._error.setText(check.message if show_error else '')
        self._error.setVisible(show_error)

        ok = bool(check.ok) and not insufficient
        if ok != self._ok:
            self._ok = ok
            self.completeChanged.emit()

    def _on_browse(self) -> None:
        current = self._edit.text().strip()
        start = current if current and Path(current).is_dir() else str(install_core.default_install_dir())
        chosen = QFileDialog.getExistingDirectory(
            self,
            '选择安装目录',
            start,
            QFileDialog.ShowDirsOnly,
        )
        if chosen:
            self._edit.setText(chosen)


class ProgressPage(QWizardPage):
    """进度页：进页面即启动后台线程，逐项显示进度。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._thread = None
        self._started = False

        self.setTitle(_PAGE_TITLES[PAGE_PROGRESS])
        self.setSubTitle('正在把程序文件写入磁盘，请稍候（通常 5~15 秒）。')

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 8, 4, 4)
        layout.setSpacing(12)

        self._status = QLabel('准备开始…')
        self._status.setObjectName('sectionTitle')
        layout.addWidget(self._status)

        self._bar = QProgressBar()
        self._bar.setRange(0, 100)
        self._bar.setValue(0)
        self._bar.setTextVisible(True)
        self._bar.setFormat('整体进度 %p%')
        layout.addWidget(self._bar)

        self._log = QListWidget()
        self._log.setFocusPolicy(Qt.NoFocus)
        self._log.setSelectionMode(QListWidget.NoSelection)
        layout.addWidget(self._log, 1)

        self._hint = QLabel('安装过程中请不要关闭窗口。')
        self._hint.setObjectName('hintLabel')
        layout.addWidget(self._hint)

    def nextId(self) -> int:
        return PAGE_FINISH

    def isComplete(self) -> bool:
        return False

    def initializePage(self) -> None:
        if self._started:
            return None
        self._started = True
        wizard = self.wizard()
        wizard.set_installing(True)
        self._bar.setValue(0)
        self._log.clear()
        self._status.setText('准备开始…')
        self._thread = InstallThread(wizard.install_options(), self)
        self._thread.progressed.connect(self._on_progress)
        self._thread.succeeded.connect(self._on_success)
        self._thread.failed.connect(self._on_failure)
        self._thread.start()

    def _on_progress(self, progress: install_core.InstallProgress) -> None:
        self._bar.setValue(int(progress.overall_percent))
        if progress.step_percent <= 0:
            self._status.setText(
                f'[{progress.step_index}/{progress.step_total}] {progress.step_label}…'
            )
        else:
            detail = f'（{progress.message}）' if progress.message else ''
            self._status.setText(
                f'[{progress.step_index}/{progress.step_total}] {progress.step_label}… '
                f'{progress.step_percent}%{detail}'
            )
        if progress.step_percent >= 100:
            self._log.addItem(f'✔ {progress.step_label}')
            self._log.scrollToBottom()

    def _on_success(self, result: install_core.InstallResult) -> None:
        wizard = self.wizard()
        wizard.install_result = result
        wizard.set_installing(False)
        wizard.next()

    def _on_failure(self, message: str) -> None:
        wizard = self.wizard()
        wizard.set_installing(False)
        self._started = False
        self._status.setText('安装失败，请根据提示处理后重试。')
        self._bar.setValue(0)
        QMessageBox.critical(self, '安装失败', message)
        wizard.back()


class FinishPage(QWizardPage):
    """完成页：勾选「立即运行」/「创建桌面快捷方式」，点完成时生效。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setTitle(_PAGE_TITLES[PAGE_FINISH])
        self.setSubTitle('')

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 8, 4, 4)
        layout.setSpacing(14)

        self._headline = QLabel(f'✔  {APP_NAME} 已成功安装')
        self._headline.setObjectName('heroTitle')
        layout.addWidget(self._headline)

        self._summary = QLabel()
        self._summary.setWordWrap(True)
        layout.addWidget(self._summary)

        self._desktop = QCheckBox('创建桌面快捷方式')
        self._desktop.setChecked(True)
        self._run = QCheckBox(f'立即运行 {APP_NAME}')
        self._run.setChecked(True)
        layout.addWidget(self._desktop)
        layout.addWidget(self._run)

        note = QLabel(
            f'班级数据保存在 {winsys.data_dir()}，卸载程序不会删除它。'
        )
        note.setObjectName('hintLabel')
        note.setWordWrap(True)
        layout.addWidget(note)
        layout.addStretch(1)

    def nextId(self) -> int:
        return -1

    def initializePage(self) -> None:
        wizard = self.wizard()
        result = getattr(wizard, 'install_result', None)
        if result is None:
            self._summary.setText('')
            return
        self._summary.setText(
            f'安装位置：{result.target_dir}'
            f'\n版本：v{result.version}\u3000\u3000文件：{result.files_installed} 个'
        )

    def apply_choices(self) -> list[str]:
        """点「完成」时执行勾选项；返回失败说明（空列表=全部成功）。"""
        errors = []
        wizard = self.wizard()
        result = getattr(wizard, 'install_result', None)
        if result is None:
            return errors
        try:
            if self._desktop.isChecked():
                install_core.create_desktop_shortcut(result.target_dir, result.version)
            else:
                win_shortcut.remove_shortcut(install_core.desktop_lnk_path())
        except Exception as exc:
            errors.append(f'桌面快捷方式处理失败：{exc}')
        if self._run.isChecked():
            error = _launch_installed(result.target_dir)
            if error:
                errors.append(error)
        return errors


def _launch_installed(target_dir: Path) -> str:
    exe = Path(target_dir) / APP_EXE_NAME
    try:
        subprocess.Popen([str(exe)], cwd=str(target_dir), close_fds=True)
        return ''
    except OSError as exc:
        return f'启动 {exe.name} 失败：{exc}'
