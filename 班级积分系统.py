"""班级积分系统 · 启动入口。

直接用不带窗口的 Python 启动即可：
    python 班级积分系统.py
    python 班级积分系统.py --selftest

开发期若使用内嵌运行库，请先设好 PYTHONHOME（见 README）。

为什么要在这里兜一层 try：
    打包成 `--noconsole` 后，Python 的 sys.stdout / sys.stderr 是 None，
    `traceback.print_exc()` 没有任何地方可写，用户只看到一个空窗口或者
    一闪而过的报错框，完全无从下手。所以未捕获的异常一律落一份 crash.log，
    提示里带上路径——老师把那个文件发过来就能定位。
"""
import os
import sys
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _log_dir() -> str:
    base = os.environ.get('APPDATA') or os.path.expanduser('~')
    folder = os.path.join(base, '班级积分系统')
    try:
        os.makedirs(folder, exist_ok=True)
    except OSError:
        folder = os.path.dirname(os.path.abspath(__file__))
    return folder


def _crash_log_path() -> str:
    return os.path.join(_log_dir(), 'crash.log')


def _ensure_streams() -> None:
    """打包成 `--noconsole` 后 sys.stdout / sys.stderr 都是 None。

    这不是小事：PyQt5 的「槽里出现未捕获异常」流程是先把 traceback 打到
    sys.stderr，再决定是否 qFatal。stderr 为 None 时打印本身抛异常，于是
    进程直接被 abort —— 用户看到的就是「什么都没说就闪退了」，而且留不下
    任何日志。把这两个流接到文件上，同一个错误就变成「记一笔 + 界面继续用」。
    """
    if sys.stdout is not None and sys.stderr is not None:
        return
    path = os.path.join(_log_dir(), 'app.log')
    try:
        sink = open(path, 'a', encoding='utf-8', buffering=1)
    except OSError:
        return
    if sys.stdout is None:
        sys.stdout = sink
    if sys.stderr is None:
        sys.stderr = sink


def _install_excepthook() -> None:
    """未捕获异常一律落 crash.log，并在控制台/日志里留完整栈。"""
    def _hook(exc_type, exc, tb):
        text = ''.join(traceback.format_exception(exc_type, exc, tb))
        stamp = time.strftime('%Y-%m-%d %H:%M:%S')
        try:
            with open(_crash_log_path(), 'a', encoding='utf-8') as fh:
                fh.write(f'\n===== {stamp} =====\n{text}')
        except OSError:
            pass
        try:
            stream = sys.stderr
            if stream is not None:
                stream.write(f'\n[{stamp}] 未捕获异常：\n{text}')
                stream.flush()
        except Exception:
            return

    sys.excepthook = _hook
    try:
        import threading
        threading.excepthook = lambda args: _hook(args.exc_type, args.exc_value, args.exc_traceback)
    except Exception:
        return


def _report_crash(exc: BaseException) -> str:
    path = _crash_log_path()
    text = (f'\n===== {time.strftime("%Y-%m-%d %H:%M:%S")} =====\n'
            f'frozen={getattr(sys, "frozen", False)}  exe={sys.executable}\n'
            + ''.join(traceback.format_exception(type(exc), exc, exc.__traceback__)))
    try:
        with open(path, 'a', encoding='utf-8') as fh:
            fh.write(text)
    except OSError:
        pass
    return path


def _show(title: str, body: str) -> None:
    """尽量弹出可读提示；连 Qt 都起不来时静默放弃。"""
    try:
        from PyQt5.QtWidgets import QApplication, QMessageBox
        app = QApplication.instance() or QApplication([sys.executable])
        QMessageBox.critical(None, title, body)
    except Exception:
        return


if __name__ == '__main__':
    _ensure_streams()
    _install_excepthook()
    try:
        from app.main import main
        raise SystemExit(main())
    except SystemExit:
        raise
    except BaseException as exc:
        log_path = _report_crash(exc)
        _show('启动失败', f'程序没能启动。\n\n{type(exc).__name__}: {exc}\n\n详细信息已写入：\n{log_path}\n\n把这个文件发给维护者即可定位问题。')
        raise SystemExit(1) from None
