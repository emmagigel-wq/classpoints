"""主题文件：本机「主题相关」的那几组文件。

为什么要单独一层：备份要能选择「带不带主题」、班级指纹要把主题内容算进去、
用户端下载时要明确排除它们。三处都要用同一份定义，否则口径会漂。

主题文件 = 数据目录下这些子目录里的全部文件（递归）：

- ``icon/``  自定义程序图标
- ``media/`` 背景图等媒体副本
- ``ui/``    生成出来的界面小图（下拉箭头等）

外加 ``AppState.background`` 指向的图片——**当且仅当**它躺在数据目录里才计入
（用户从桌面直接选一张图时我们只记路径，那种文件不归我们管，备份也不该夹带）。
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

THEME_DIRS: tuple[str, ...] = ('icon', 'media', 'ui')
ZIP_PREFIX = 'theme'
_IMPORT_DIRS: tuple[str, ...] = THEME_DIRS + ('splash',)
BACKUP_DIRS: tuple[str, ...] = _IMPORT_DIRS
MAX_SPLASH_BYTES = 33554432
_CHUNK = 65536


def _root(root: str | Path | None = None) -> Path:
    if root is not None:
        return Path(root)
    from app.core import paths
    return Path(paths.current_data_dir())


def theme_splash_files(root: str | Path | None = None) -> list[Path]:
    """列出启动动画目录里的文件（``splash/``）。

    单独一个函数而不是塞进 ``theme_files``：``theme_files`` 同时被「主题是否变化」
    的哈希用，把几十 MB 的视频算进去会让每次检查都去读大文件。
    """
    base = _root(root)
    d = base / 'splash'
    if not d.is_dir():
        return []
    return [p for p in sorted(d.rglob('*')) if p.is_file()]


def theme_files(root: str | Path | None = None) -> list[Path]:
    """列出全部主题文件（按路径排序，保证哈希稳定）。"""
    base = _root(root)
    out = []
    for name in THEME_DIRS:
        d = base / name
        if not d.is_dir():
            continue
        for p in sorted(d.rglob('*')):
            if p.is_file():
                out.append(p)
    out.sort(key=lambda p: p.as_posix())
    return out


def theme_relative(path: Path, root: str | Path | None = None) -> str:
    """主题文件相对数据目录的路径（正斜杠），用作 zip 内路径与哈希输入。"""
    base = _root(root)
    try:
        return Path(path).resolve().relative_to(base.resolve()).as_posix()
    except (ValueError, OSError):
        return Path(path).name


def theme_hash(root: str | Path | None = None) -> str:
    """主题内容哈希。没有主题文件时返回空串（指纹里该字段也就是空串）。

    算法：按相对路径排序，逐个把 ``相对路径 + b"\\0" + 内容`` 喂进 sha256。
    文件读不出来就跳过（不让一张坏图把整块指纹搞挂）。
    """
    base = _root(root)
    files = theme_files(base)
    if not files:
        return ''
    h = hashlib.sha256()
    for p in files:
        rel = theme_relative(p, base)
        h.update(rel.encode('utf-8'))
        h.update(b'\x00')
        try:
            with open(p, 'rb') as fh:
                while True:
                    chunk = fh.read(_CHUNK)
                    if not chunk:
                        break
                    h.update(chunk)
        except OSError:
            continue
        h.update(b'\x00')
    return h.hexdigest()


def read_theme(root: str | Path | None = None) -> dict[str, bytes]:
    """读出全部主题文件内容（相对路径 → 字节），供备份/克隆使用。

    包含 ``splash/``（启动动画）：它也在数据目录里、也是用户选的素材，
    以前没带上，换台电脑恢复备份后启动动画就静默失效。
    """
    base = _root(root)
    out = {}
    for p in theme_files(base):
        rel = theme_relative(p, base)
        try:
            out[rel] = p.read_bytes()
        except OSError:
            continue
    for p in theme_splash_files(base):
        rel = theme_relative(p, base)
        try:
            data = p.read_bytes()
        except OSError:
            continue
        if len(data) > MAX_SPLASH_BYTES:
            continue
        out[rel] = data
    return out


def restore_theme(files: dict[str, bytes], root: str | Path | None = None) -> int:
    """把主题文件写回数据目录，返回写入个数。

    只接受落在 ``THEME_DIRS`` 或 ``splash/`` 里的相对路径，别的一律丢弃——
    防止有人拿一个恶意备份把文件写到数据目录外面去（``../`` 之类的穿越）。
    """
    base = _root(root)
    allowed = tuple(f'{name}/' for name in BACKUP_DIRS)
    count = 0
    for rel, data in files.items():
        rel = rel.replace('\\', '/').lstrip('/')
        if not rel.startswith(allowed) or '/../' in rel or '..' in rel.split('/'):
            continue
        target = base / rel
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_name(target.name + '.tmp')
            tmp.write_bytes(data)
            os.replace(tmp, target)
            count += 1
        except OSError:
            continue
    return count


def import_file(src: str | Path, kind: str = 'media') -> str:
    """把用户选的文件复制进数据目录的 ``kind`` 子目录，返回相对路径。

    为什么不再直接记用户选的绝对路径：他很可能选的是下载目录/临时目录里的
    文件，过几天被删掉或换电脑就没了，界面上表现为「主题设置过但没生效」。
    收进来之后主题才能真正跟着数据走（备份、搬家、同步副本都认它）。
    """
    src_path = Path(src)
    if not src_path.is_file():
        return ''
    sub = kind if kind in _IMPORT_DIRS else 'media'
    base = _root() / sub
    try:
        base.mkdir(parents=True, exist_ok=True)
        data = src_path.read_bytes()
        target = base / src_path.name
        if target.exists():
            try:
                if target.read_bytes() == data:
                    return f'{sub}/{target.name}'
            except OSError:
                pass
            stamp = hashlib.sha256(data).hexdigest()[:8]
            target = base / f'{src_path.stem}-{stamp}{src_path.suffix or ".bin"}'
            if target.exists():
                return f'{sub}/{target.name}'
        tmp = target.with_name(target.name + '.tmp')
        tmp.write_bytes(data)
        os.replace(tmp, target)
        return f'{sub}/{target.name}'
    except OSError:
        return ''


def resolve(value: str | Path | None) -> str:
    """把主题字段解析成现在真能用的绝对路径；找不到就返回空串。

    兼容两种历史写法：相对路径（新，收进数据目录的）与绝对路径（老，用户
    当初选的位置）。文件已经不在了就返回空串，让界面按「没设置」处理，
    而不是拿着一个死路径去设样式表。
    """
    text = str(value or '').strip()
    if not text:
        return ''
    cand = Path(text)
    if cand.is_absolute():
        if cand.is_file():
            return str(cand)
        return ''
    target = _root() / text
    if target.is_file():
        return str(target)
    return ''
