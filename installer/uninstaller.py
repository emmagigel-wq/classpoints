"""Uninstall.exe 入口：删除程序文件 / 快捷方式 / HKCU 卸载登记，保留班级数据。

为什么要单独一个 exe：
  * 安装器带 Qt + payload.zip（100MB+），卸载程序只需要几十 KB 的逻辑，
    单独打包成精简 exe 放进安装目录，用户从「应用和功能」卸载时启动快；
  * 卸载程序不能删自己（Windows 会锁住正在运行的 exe 映像），
    所以先把自己复制到 %TEMP%，由副本等原进程退出后清空安装目录并自删。

命令行：
  Uninstall.exe                     图形确认后卸载
  Uninstall.exe --silent            静默卸载（供自动化测试，退出码 0=成功）
  Uninstall.exe --dir <path>        指定安装目录（默认取 exe 所在目录）
  Uninstall.exe --cleanup --wait-pid <pid> --dir <path>
                                    内部用：延迟清理模式（由副本执行）
"""

from __future__ import annotations

import argparse
import ctypes
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from installer import install_core, winsys
from installer.constants import APP_EXE_NAME, APP_NAME, UNINSTALLER_NAME

MB_OK = 0
MB_YESNO = 4
MB_ICONERROR = 16
MB_ICONQUESTION = 32
MB_ICONINFORMATION = 64
IDYES = 6

LOG_NAME = f"{APP_NAME}-卸载.log"


def _fix_stdio() -> None:
    """windowed exe 被重定向时输出 UTF-8 中文（否则按系统 GBK 编码）。"""
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is None:
            continue
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def _msgbox(text: str, title: str, flags: int) -> int:
    try:
        return int(ctypes.windll.user32.MessageBoxW(None, text, title, flags))
    except Exception:
        return 0


def _log_path() -> Path:
    return Path(tempfile.gettempdir()) / LOG_NAME


def _write_log(lines: list[str]) -> None:
    try:
        with open(_log_path(), "a", encoding="utf-8") as fh:
            fh.write(time.strftime("\n[%Y-%m-%d %H:%M:%S] ") + "\n".join(lines) + "\n")
    except OSError:
        pass


def _frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def _schedule_self_delete(path: Path) -> None:
    """用延迟的 cmd 删掉自己：进程退出后 ping 等两秒再 del。"""
    if not _frozen():
        return
    target = Path(path)
    if not target.is_file():
        return
    command = f'ping -n 3 127.0.0.1 >nul & del /f /q "{target}"'
    try:
        subprocess.Popen(
            ["cmd.exe", "/c", command],
            creationflags=winsys.CREATE_NO_WINDOW,
            close_fds=True,
        )
    except OSError:
        return


def _default_install_dir(args: argparse.Namespace) -> Path:
    if args.dir:
        return Path(args.dir).expanduser()
    if _frozen():
        return Path(sys.executable).resolve().parent
    from_registry = install_core.installed_location()
    return from_registry or install_core.default_install_dir()


def _looks_like_install(install_dir: Path) -> bool:
    """安装目录里应当有主程序或卸载程序；防止误删用户选错的目录。"""
    for name in (UNINSTALLER_NAME, APP_EXE_NAME):
        if (install_dir / name).is_file():
            return True
    recorded = install_core.installed_location()
    try:
        return recorded is not None and recorded.resolve() == install_dir.resolve()
    except OSError:
        return False


def _count_files(install_dir: Path) -> int:
    try:
        return sum(1 for p in install_dir.rglob("*") if p.is_file())
    except OSError:
        return 0


def _confirm(install_dir: Path) -> bool:
    version = install_core.installed_version()
    text = (
        f"确定要卸载「{APP_NAME}」吗？\n\n安装位置：{install_dir}\n"
        + (f"已安装版本：v{version}\n" if version else "")
        + "\n将删除：\n  · 程序文件与运行库\n  · 开始菜单快捷方式（以及桌面快捷方式）\n"
          "  · 控制面板 / 应用和功能里的卸载登记\n\n班级数据保存在："
          f"{winsys.data_dir()}\n卸载**不会**删除班级数据，重新安装后可继续使用。"
    )
    return _msgbox(text, f"卸载 {APP_NAME}", MB_YESNO | MB_ICONQUESTION) == IDYES


def _show_result(problems: list[str], silent: bool) -> None:
    if silent or problems:
        return
    _msgbox(
        f"{APP_NAME} 已卸载。\n\n班级数据已保留：{winsys.data_dir()}",
        f"卸载 {APP_NAME}",
        MB_OK | MB_ICONINFORMATION,
    )


def _show_error(problems: list[str], silent: bool) -> None:
    if silent:
        for problem in problems:
            print(f"[警告] {problem}")
        return
    _msgbox(
        "卸载没有完全完成：\n\n"
        + "\n".join(f"  · {p}" for p in problems[:8])
        + "\n\n请关掉占用文件的程序后重试；日志见：\n" + str(_log_path()),
        f"卸载 {APP_NAME}",
        MB_OK | MB_ICONERROR,
    )


def _cleanup(install_dir: Path, wait_pid: int, silent: bool) -> int:
    """临时副本执行的延迟清理：等原卸载进程退出 → 删安装目录 → 自删。"""
    deadline = time.time() + 30.0
    while wait_pid > 0 and time.time() < deadline and winsys.is_pid_alive(wait_pid):
        time.sleep(0.1)
    problems = []
    for _attempt in range(12):
        problems = install_core.delete_install_files(install_dir)
        if not problems:
            break
        time.sleep(0.25)
    if install_dir.is_dir():
        try:
            install_dir.rmdir()
        except FileNotFoundError:
            pass
        except OSError as exc:
            problems.append(f"删除安装目录失败：{exc}")
    elif not problems:
        problems = []
    if problems and not silent:
        _show_error(problems, silent)
    if silent:
        if problems:
            for problem in problems:
                print(f"[警告] {problem}")
        else:
            print("卸载完成：安装目录已删除")
    _write_log(["卸载清理完成" if not problems else f"卸载清理有残留：{problems}"])
    _schedule_self_delete(Path(sys.executable))
    return 0 if not problems else 1


def _uninstall(install_dir: Path, silent: bool) -> int:
    lines = []
    problems = []

    def note(message: str) -> None:
        lines.append(message)
        if silent:
            print(message)

    version = install_core.installed_version()
    note(f"卸载 {APP_NAME} v{version or '(未知版本)'}：{install_dir}")

    if winsys.is_process_running(APP_EXE_NAME):
        note(f"正在关闭 {APP_NAME} ...")
        problems.extend(winsys.stop_running_app(APP_EXE_NAME))

    artifacts = install_core.remove_uninstall_artifacts()
    problems.extend(artifacts)
    if silent and not artifacts:
        note("已删除快捷方式与卸载登记")

    if not install_dir.is_dir():
        note("安装目录不存在，跳过文件删除")
    else:
        running_dir = Path(sys.executable).resolve().parent if _frozen() else None
        try:
            in_place = running_dir is not None and running_dir == install_dir.resolve()
        except OSError:
            in_place = False
        if in_place:
            temp_copy = Path(tempfile.gettempdir()) / f"cp_uninstall_{os.getpid()}.exe"
            try:
                shutil.copy2(sys.executable, temp_copy)
                arguments = [
                    str(temp_copy),
                    "--cleanup",
                    "--dir",
                    str(install_dir),
                    "--wait-pid",
                    str(os.getpid()),
                ]
                if silent:
                    arguments.append("--silent")
                subprocess.Popen(
                    arguments,
                    close_fds=True,
                    creationflags=winsys.DETACHED_PROCESS,
                )
                note(f"已启动清理进程：{temp_copy.name}（等待删除安装目录）")
            except OSError as exc:
                problems.append(f"启动清理进程失败：{exc}")
        else:
            problems.extend(install_core.delete_install_files(install_dir))
            try:
                install_dir.rmdir()
            except FileNotFoundError:
                pass
            except OSError as exc:
                problems.append(f"删除安装目录失败：{exc}")
            if not problems:
                note("安装目录已删除")
    _write_log(lines + [f"问题：{p}" for p in problems])
    if problems:
        _show_error(problems, silent)
        return 0 if silent else 1
    _show_result(problems, silent)
    if silent:
        note("卸载完成")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=UNINSTALLER_NAME,
        description=f"{APP_NAME} 卸载程序",
    )
    parser.add_argument("--silent", action="store_true", help="静默卸载，不弹任何窗口")
    parser.add_argument("--dir", metavar="路径", default=None, help="安装目录")
    parser.add_argument("--cleanup", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--wait-pid", type=int, default=0, help=argparse.SUPPRESS)
    return parser


def main(argv: list[str] | None = None) -> int:
    _fix_stdio()
    args = _build_parser().parse_args(sys.argv[1:] if argv is None else argv)
    install_dir = _default_install_dir(args)
    if args.cleanup:
        return _cleanup(install_dir, args.wait_pid, args.silent)
    if not install_dir.is_dir() or not _looks_like_install(install_dir):
        if args.silent:
            print(f"未检测到安装记录：{install_dir}")
            install_core.remove_uninstall_artifacts()
            return 0
        if _msgbox(
            f"未找到「{APP_NAME}」的安装信息：\n{install_dir}\n\n可能已经卸载过了。是否只清理快捷方式和卸载登记？",
            f"卸载 {APP_NAME}",
            MB_YESNO | MB_ICONQUESTION,
        ) != IDYES:
            return 0
        install_dir = Path("")
    if args.silent:
        return _uninstall(install_dir, silent=True)
    if not _confirm(install_dir):
        return 0
    return _uninstall(install_dir, silent=False)


if __name__ == "__main__":
    sys.exit(main())
