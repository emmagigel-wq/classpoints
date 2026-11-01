"""安装向导主窗口（QWizard 四页流程 + 按钮文案 + 安装态约束）。"""
from __future__ import annotations

import sys
from pathlib import Path

from PyQt5.QtGui import QIcon
from PyQt5.QtWidgets import QApplication, QMessageBox, QWizard

from installer import bundle, install_core
from installer.constants import APP_NAME
from installer.ui.pages import (PAGE_DIRECTORY, PAGE_FINISH, PAGE_PROGRESS,
                                PAGE_WELCOME, DirectoryPage, FinishPage,
                                ProgressPage, WelcomePage)
from installer.ui.style import STYLE_SHEET


class InstallWizard(QWizard):
    def __init__(self, manifest: dict, initial_dir: Path | str | None = None,
                 parent=None) -> None:
        super().__init__(parent)
        self.manifest = manifest
        self.version = str(manifest['version'])
        self.install_result = None
        self.clean_existing = False
        self._installing = False

        self.setWindowTitle(f'{APP_NAME} v{self.version} 安装程序')
        icon_path = bundle.icon_path()
        if icon_path is not None:
            self.setWindowIcon(QIcon(str(icon_path)))
        self.setWizardStyle(QWizard.ModernStyle)
        self.setOption(QWizard.NoBackButtonOnStartPage, True)
        self.setOption(QWizard.NoBackButtonOnLastPage, True)
        self.setOption(QWizard.HaveHelpButton, False)
        self.setOption(QWizard.NoCancelButtonOnLastPage, True)
        self.setButtonText(QWizard.BackButton, '< 上一步')
        self.setButtonText(QWizard.NextButton, '下一步 >')
        self.setButtonText(QWizard.FinishButton, '完成')
        self.setButtonText(QWizard.CancelButton, '取消')
        self.setStyleSheet(STYLE_SHEET)
        self.resize(720, 560)
        self.setMinimumSize(660, 520)

        payload = install_core.inspect_payload()
        if initial_dir is not None:
            start_dir = Path(initial_dir).expanduser()
        else:
            start_dir = install_core.installed_location() or install_core.default_install_dir()

        self._welcome = WelcomePage(manifest, payload, self)
        self._directory = DirectoryPage(manifest, payload, start_dir, self)
        self._progress = ProgressPage(self)
        self._finish = FinishPage(self)
        self.setPage(PAGE_WELCOME, self._welcome)
        self.setPage(PAGE_DIRECTORY, self._directory)
        self.setPage(PAGE_PROGRESS, self._progress)
        self.setPage(PAGE_FINISH, self._finish)
        self.setStartId(PAGE_WELCOME)

    def install_options(self) -> install_core.InstallOptions:
        return install_core.InstallOptions(
            target_dir=self._directory.install_dir(),
            version=self.version,
            create_desktop_shortcut=False,
            clean_existing=self.clean_existing,
        )

    def set_installing(self, installing: bool) -> None:
        self._installing = installing
        for button_id in (QWizard.BackButton, QWizard.NextButton, QWizard.CancelButton):
            button = self.button(button_id)
            if button is None:
                continue
            button.setEnabled(not installing)

    def accept(self) -> None:
        errors = []
        if self.install_result is not None:
            errors = self._finish.apply_choices()
        if errors:
            QMessageBox.warning(self, '提示', '\n'.join(errors))
        super().accept()

    def closeEvent(self, event) -> None:
        if self._installing:
            QMessageBox.information(self, '正在安装',
                                    '安装正在进行，请等待程序文件写入完成（通常只需几秒）。')
            event.ignore()
            return
        event.accept()


def run_wizard(manifest: dict, initial_dir: Path | str | None = None) -> int:
    """创建 QApplication 并显示安装向导，返回退出码。"""
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName(f'{APP_NAME} 安装程序')
    icon_path = bundle.icon_path()
    if icon_path is not None:
        app.setWindowIcon(QIcon(str(icon_path)))
    wizard = InstallWizard(manifest, initial_dir)
    wizard.show()
    return int(app.exec_())
