"""安装程序入口：带参数时走命令行（--silent / --version），不带参数时弹安装向导。

为什么入口单独一个文件：PyInstaller 的 spec 需要一个干净的脚本入口；
GUI 相关 import 全部放在函数里，保证 --silent 模式在无显示环境也能跑。
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from installer import bundle, install_core
from installer.constants import APP_EXE_NAME, APP_NAME


def _fix_stdio() -> None:
    """windowed exe 被重定向时能正常输出中文（编码跟随系统时会是 GBK）。"""
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is None:
            continue
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=f"{APP_NAME}-安装程序",
        description=f"{APP_NAME} Windows 安装程序",
        add_help=True,
    )
    parser.add_argument("--version", action="store_true", help="打印版本号后退出")
    parser.add_argument("--silent", action="store_true", help="静默安装，不显示任何窗口")
    parser.add_argument(
        "--dir",
        metavar="路径",
        default=None,
        help="安装目录（默认 %%LOCALAPPDATA%%\\Programs\\" + APP_NAME + "）",
    )
    parser.add_argument(
        "--no-desktop-shortcut",
        action="store_true",
        help="静默安装时不创建桌面快捷方式",
    )
    parser.add_argument(
        "--force-clean",
        action="store_true",
        help="静默安装时允许清空非空的非安装目录",
    )
    parser.add_argument(
        "--run",
        action="store_true",
        help="静默安装完成后启动程序",
    )
    return parser


def _silent_progress_printer():
    """静默模式输出：每个步骤一行，末尾带「完成」。"""
    state = {"open": False, "step": None}

    def emit(progress: install_core.InstallProgress) -> None:
        if progress.step_percent <= 0:
            if state["step"] == progress.step_key:
                return
            state["step"] = progress.step_key
            sys.stdout.write(
                f"[{progress.step_index}/{progress.step_total}] {progress.step_label} ... "
            )
            sys.stdout.flush()
            state["open"] = True
        elif progress.step_percent >= 100 and state["open"]:
            sys.stdout.write("完成\n")
            sys.stdout.flush()
            state["open"] = False

    return emit


def _launch(target: Path) -> str:
    exe = Path(target) / APP_EXE_NAME
    try:
        subprocess.Popen([str(exe)], cwd=str(target), close_fds=True)
        return ""
    except OSError as exc:
        return f"启动 {exe.name} 失败：{exc}"


def _silent_install(args, version: str) -> int:
    target = (
        Path(args.dir).expanduser()
        if args.dir
        else install_core.default_install_dir()
    )
    options = install_core.InstallOptions(
        target_dir=target,
        version=version,
        create_desktop_shortcut=not args.no_desktop_shortcut,
        clean_existing=args.force_clean,
    )
    print(f"开始静默安装 {APP_NAME} v{version} -> {target}")
    try:
        result = install_core.install(options, progress=_silent_progress_printer())
    except install_core.InstallError as exc:
        print(f"[错误] {exc}")
        return 1
    except Exception as exc:
        print(f"[错误] 安装失败：{exc}")
        return 1
    for warning in result.warnings:
        print(f"[警告] {warning}")
    print(
        f"安装完成：{result.target_dir}（{result.files_installed} 个文件，"
        f"{result.installed_bytes / 1024 / 1024:.1f} MB）"
    )
    if args.run:
        error = _launch(result.target_dir)
        if error:
            print(f"[警告] {error}")
    return 0


def _run_gui(args, manifest: dict) -> int:
    try:
        from installer.ui.wizard import run_wizard
    except Exception as exc:
        print(f"[错误] 无法加载安装向导界面：{exc}")
        return 1
    initial = Path(args.dir).expanduser() if args.dir else None
    return run_wizard(manifest, initial)


def main(argv: list[str] | None = None) -> int:
    _fix_stdio()
    argv = list(sys.argv[1:] if argv is None else argv)
    args = _build_parser().parse_args(argv)
    try:
        manifest = bundle.load_manifest()
    except (FileNotFoundError, ValueError) as exc:
        print(f"[错误] {exc}")
        return 1
    version = str(manifest["version"])
    if args.version:
        print(version)
        return 0
    if args.silent:
        return _silent_install(args, version)
    return _run_gui(args, manifest)


if __name__ == "__main__":
    sys.exit(main())
