"""构建期工具：把 onedir 产物压成 payload.zip，并写 manifest.json。

由 packaging/build_installer.ps1 调用，不依赖第三方库：
  python installer\\build_payload.py --dist "dist\\班级积分系统" --out build\\installer [--version <版本号>]
  （不传 --version 时自动从 app/version.py 读取，版本号只有那一个来源）

为什么要在构建期现压成 zip：
  * PyInstaller 的 --add-data 只接受文件/目录，直接把 125MB 的 onedir 目录
    当 data 收进去，onefile 解包会变成几千个小文件，冷启动很慢；
  * 单个 zip 进 exe，安装时用 Python zipfile 解压（中文名按 UTF-8 处理，
    不用 PowerShell Expand-Archive），快且稳。
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERSION_FILE = ROOT / 'app' / 'version.py'
ASSETS_ICON = ROOT / 'assets' / 'app.ico'

APP_ID = 'classpoints'
APP_NAME = '班级积分系统'
PAYLOAD_ZIP = 'payload.zip'
MANIFEST_JSON = 'manifest.json'
APP_ICON = 'app.ico'
UNINSTALLER_NAME = 'Uninstall.exe'


def read_version_from_source() -> str:
    """直接解析 app/version.py 源码取 __version__。

    installer/ 刻意不 import app 包（安装器独立于被安装的程序），
    这里只做一次性源码解析，不执行 app 的代码。
    """
    if not VERSION_FILE.is_file():
        raise SystemExit(f'[错误] 找不到版本号唯一来源：{VERSION_FILE}')
    tree = ast.parse(VERSION_FILE.read_text(encoding='utf-8'))
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == '__version__':
                value = ast.literal_eval(node.value)
                if not (isinstance(value, str) and value.strip()):
                    raise SystemExit('[错误] app/version.py 里的 __version__ 不是有效字符串')
                return value.strip()
    raise SystemExit('[错误] app/version.py 里没有找到 __version__ 赋值')


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, 'rb') as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _iter_dist_files(dist: Path) -> list[Path]:
    files = [p for p in sorted(dist.rglob('*')) if p.is_file()]
    return files


def build_payload(dist: Path, out_dir: Path, version: str) -> dict:
    if not dist.is_dir():
        raise SystemExit(f'[错误] onedir 产物不存在：{dist}\n先运行 packaging\\build.ps1（或给本脚本加 -BuildApp）再打安装包。')
    app_exe = dist / f'{APP_NAME}.exe'
    if not app_exe.is_file():
        raise SystemExit(f'[错误] {dist} 里没有主程序 {app_exe.name}，不是有效的 onedir 产物')
    files = _iter_dist_files(dist)
    total_bytes = sum(p.stat().st_size for p in files)
    out_dir.mkdir(parents=True, exist_ok=True)
    payload_path = out_dir / PAYLOAD_ZIP
    if payload_path.exists():
        payload_path.unlink()
    extra_icon = ASSETS_ICON if ASSETS_ICON.is_file() else None
    has_icon = (dist / APP_ICON).is_file()
    print(f'[payload] 源目录：{dist}')
    print(f'[payload] {len(files)} 个文件，共 {total_bytes / 1024 / 1024:.1f} MB，正在压缩…')
    written = 0
    last_percent = -1
    started = time.time()
    with zipfile.ZipFile(payload_path, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for index, path in enumerate(files, start=1):
            arcname = path.relative_to(dist).as_posix()
            zf.write(path, arcname)
            written += path.stat().st_size
            percent = int(written * 100 / max(1, total_bytes))
            if percent != last_percent and (percent % 10 == 0 or index == len(files)):
                print(f'\r[payload] 压缩进度 {percent:3d}%  ({index}/{len(files)})', end='')
                sys.stdout.flush()
                last_percent = percent
        if extra_icon is not None and not has_icon:
            zf.write(extra_icon, APP_ICON)
            print(f'\r[payload] 追加图标 {APP_ICON}', end='')
    print()
    elapsed = time.time() - started
    print(f'[payload] 完成：{payload_path.name} {payload_path.stat().st_size / 1024 / 1024:.1f} MB（压缩 {elapsed:.1f}s）')
    uninstaller_path = out_dir / UNINSTALLER_NAME
    manifest = {
        'schema': 1,
        'app_id': APP_ID,
        'name': APP_NAME,
        'version': version,
        'built_at': time.strftime('%Y-%m-%dT%H:%M:%S%z'),
        'payload': {
            'file': PAYLOAD_ZIP,
            'files': len(files) + (1 if extra_icon is not None and not has_icon else 0),
            'uncompressed_bytes': total_bytes,
            'zip_bytes': payload_path.stat().st_size,
            'sha256': sha256_of(payload_path),
        },
        'source': {
            'dist': str(dist),
            'python': sys.version.split()[0],
        },
    }
    if uninstaller_path.is_file():
        manifest['uninstaller'] = {
            'file': UNINSTALLER_NAME,
            'bytes': uninstaller_path.stat().st_size,
            'sha256': sha256_of(uninstaller_path),
        }
    else:
        print(f'[警告] 还没有 {UNINSTALLER_NAME}（先打卸载器）')
    manifest_path = out_dir / MANIFEST_JSON
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f'[manifest] {manifest_path}（v{version}）')
    if ASSETS_ICON.is_file():
        target_icon = out_dir / APP_ICON
        if not target_icon.is_file() or target_icon.stat().st_size != ASSETS_ICON.stat().st_size:
            target_icon.write_bytes(ASSETS_ICON.read_bytes())
            print(f'[icon] 复制 {ASSETS_ICON.name} -> {target_icon}')
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='把 onedir 产物压成安装器 payload')
    parser.add_argument('--dist', required=True, help='onedir 产物目录，例如 dist\\班级积分系统')
    parser.add_argument('--out', required=True, help='资源输出目录，例如 build\\installer')
    parser.add_argument('--version', default=None, help='版本号；缺省从 app/version.py 读取')
    args = parser.parse_args(argv)
    version = args.version or read_version_from_source()
    build_payload(Path(args.dist).resolve(), Path(args.out).resolve(), version)
    return 0


if __name__ == '__main__':
    sys.exit(main())
