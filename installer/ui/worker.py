"""把安装逻辑放到后台线程，避免解压 100+ MB 时界面假死。"""

from __future__ import annotations

from PyQt5.QtCore import QThread, pyqtSignal

from installer import install_core


class InstallThread(QThread):
    """执行 install_core.install() 的线程。

    progress 信号携带 InstallProgress 对象（跨线程排队投递，Qt 负责编组）。
    """

    progressed = pyqtSignal(object)
    succeeded = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, options: install_core.InstallOptions, parent=None) -> None:
        super().__init__(parent)
        self._options = options

    def run(self) -> None:
        try:
            result = install_core.install(self._options, progress=self.progressed.emit)
        except install_core.InstallError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:
            self.failed.emit(f'安装过程中出现意外错误：{exc}')
        else:
            self.succeeded.emit(result)
