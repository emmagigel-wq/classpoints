"""程序装配层：加载数据、建主窗口、把退出时的清理收在一处。

刻意不做两件事，避免又回到「哪里都能改数据」的老路：
  * 不在这里改业务数据（一律交给 core/services）；
  * 不在这里挂定时器（一律交给 TimerRegistry）。
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path


def _ensure_import_path() -> None:
    """允许用 `python 班级积分系统.py` 直接从源码目录启动。"""
    root = Path(__file__).resolve().parent.parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))


# 单实例约定：本地套接字名，两端必须一致
SINGLE_SERVER = 'classpoints-single-instance-8f3a'


def _try_activate_existing(timeout_ms: int = 400) -> bool:
    """已有实例在跑就叫它到前台并返回 True（此时本进程应当直接退出）。"""
    try:
        from PyQt5.QtNetwork import QLocalSocket
    except Exception:
        return False

    sock = QLocalSocket()
    sock.connectToServer(SINGLE_SERVER)
    if not sock.waitForConnected(timeout_ms):
        return False
    try:
        sock.write(b'activate\n')
        sock.flush()
        sock.waitForBytesWritten(timeout_ms)
        sock.disconnectFromServer()
    except Exception:
        pass
    return True


def _start_single_server(window, app):
    """把自己注册成「主实例」，之后任何一次启动都会来敲这个门。"""
    try:
        from PyQt5.QtNetwork import QLocalServer
    except Exception:
        return

    QLocalServer.removeServer(SINGLE_SERVER)
    server = QLocalServer(app)
    if not server.listen(SINGLE_SERVER):
        import traceback

        traceback.print_exc()
        return

    def _on_new_connection() -> None:
        while server.hasPendingConnections():
            conn = server.nextPendingConnection()
            if conn is None:
                return
            try:
                conn.readyRead.connect(lambda c=conn: c.readAll())
                conn.disconnected.connect(conn.deleteLater)
            except Exception:
                pass
            window.activate_requested.emit()

    server.newConnection.connect(_on_new_connection)
    activate = getattr(window, 'activate_from_other_instance', None)
    if activate is not None:
        window.activate_requested.connect(activate)
    return server


def _set_app_user_model_id() -> None:
    """Windows 任务栏/通知按 AppUserModelID 分组取图标。

    不设的话进程沿用 python.exe（打包后是 bootloader）的图标，
    用户在任务栏上就看不出「改了图标」，还以为主题功能没生效。
    """
    if not sys.platform.startswith('win'):
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            'ClassPoints.ClassPointsSystem'
        )
    except Exception:
        pass


def load_store():
    """读存档；坏档时给可读提示而不是白屏。返回 (store, load_result)。"""
    from app.core.state import Store

    return Store.load()


def run(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)

    from PyQt5.QtCore import Qt
    from PyQt5.QtWidgets import QApplication, QMessageBox

    from app.services.timer_registry import TimerRegistry
    from app.ui import theme

    _set_app_user_model_id()
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    # Qt 5.10 起才有这个属性；老版本上 hasattr 兜底，别让它起不来
    if hasattr(Qt, 'AA_DisableWindowContextHelpButton'):
        QApplication.setAttribute(Qt.AA_DisableWindowContextHelpButton, True)
    app = QApplication(argv)
    app.setApplicationName('班级积分系统')
    app.setOrganizationName('ClassPoints')

    try:
        from app.version import __version__

        app.setApplicationVersion(__version__)
    except Exception:
        pass

    app.setStyleSheet(theme.APP_QSS)

    try:
        from app.services import assets

        app.setStyleSheet(
            theme.APP_QSS
            + theme.arrow_qss(assets.chevron_down())
            + theme.calendar_qss()
        )
    except Exception:
        import traceback

        traceback.print_exc()

    if _try_activate_existing():
        return 0

    from app.core import paths

    switched, switch_msg = paths.apply_pending_data_dir()

    store, loaded = load_store()
    warnings = []
    if switch_msg:
        # 数据目录切换（便携/固定之间）提示一次，免得用户以为数据丢了
        warnings.append(switch_msg)
    if not loaded.ok:
        warnings.append(f'存档读取失败：{loaded.message}')
        if getattr(loaded, 'recovered_from', None):
            warnings.append(f'已从备份恢复：{loaded.recovered_from}')

    registry = TimerRegistry()
    app.aboutToQuit.connect(registry.shutdown)

    # 退出前等在飞的后台任务（转码/同步），别让它们写坏文件
    from app.ui.dialogs.settings_dialog import wait_background_tasks

    app.aboutToQuit.connect(lambda: wait_background_tasks(1500))

    from app.ui.main_window import MainWindow

    window = MainWindow(store, registry)
    window.selftest_requested.connect(lambda: _show_selftest(window))
    _single_server = _start_single_server(window, app)

    try:
        from app.ui.widgets.launch import play_launch

        play_launch(app, store.state.settings, argv)
    except Exception:
        import traceback

        traceback.print_exc()

    window.show()

    if warnings:
        QMessageBox.warning(window, '启动提示', '\n'.join(warnings))

    return app.exec_()


def _show_selftest(window) -> None:
    """界面「自检」按钮的落点：跑一遍内置体检并把清单显示出来。"""
    from PyQt5.QtWidgets import QMessageBox

    try:
        from app.selftest.runner import run_all
    except Exception as exc:
        QMessageBox.information(window, '自检', f'自检模块不可用：{exc}')
        return

    report = run_all()
    lines = [report.summary(), '']
    for item in report.items:
        mark = '通过' if item.passed else ('跳过' if item.skipped else '失败')
        lines.append(f'[{mark}] {item.name} — {item.detail}')
    QMessageBox.information(window, '自检结果', '\n'.join(lines))


def selftest_main() -> int:
    """`--selftest`：不开窗口，只跑自检并打印清单。"""
    from app.selftest.runner import run_all

    report = run_all()
    print(report.summary())
    for item in report.items:
        mark = 'PASS' if item.passed else ('SKIP' if item.skipped else 'FAIL')
        print(f'[{mark}] {item.name} — {item.detail}')
    return 0 if report.ok else 1


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    _ensure_import_path()
    if '--selftest' in argv:
        return selftest_main()
    try:
        return run(argv)
    except Exception:
        traceback.print_exc()
        return 1
