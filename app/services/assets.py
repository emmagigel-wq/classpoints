"""运行时生成界面用的小图标。

为什么不在 assets/ 里放现成的 png：下拉箭头要跟主题色走（深灰 ``#475467``），
而且 QSS 的 ``image: url(...)`` 只认**文件路径**或 Qt 资源，不认 QIcon。
放源码目录里打包后会落在 ``sys._MEIPASS``（只读、每次启动解压），
所以统一生成到用户数据目录下的 ``ui/`` 里，一次生成、长期复用。

两个已踩过的坑，都在这里挡住：
* **并发写同一个 .tmp**：多个线程同时要图标时，它们抢同一个临时文件名，
  互相把对方正在写的文件 rename 走，抛 PermissionError。→ 临时名带线程 id。
* **路径里有单引号**：QSS 的 ``url('...')`` 不支持转义，路径带 ``'`` 会让
  **整张样式表**解析失败（所有箭头、下拉箭头全没了）。→ 落到一个安全的
  临时目录里再用。

本模块属于 services 层：允许 import Qt（同 TimerRegistry 用 QTimer），
但不碰任何界面控件，也不参与状态管理。
"""
from __future__ import annotations

import os
import shutil
import tempfile
import threading
from pathlib import Path

ARROW_SIZE = 10

_UNSAFE_IN_QSS = '\'"'

_DRAW_LOCK = threading.Lock()


def ui_icon_dir() -> Path:
    """图标目录。路径若含 QSS 无法表达的字符，就退到临时目录。"""
    from app.core.storage import app_data_dir

    try:
        path = app_data_dir() / 'ui'
    except Exception:
        path = Path(tempfile.gettempdir()) / 'classpoints-ui'
    if any(ch in str(path) for ch in _UNSAFE_IN_QSS):
        path = Path(tempfile.gettempdir()) / 'classpoints-ui'
    path.mkdir(parents=True, exist_ok=True)
    return path


def _moved_from_unsafe_dir(name: str) -> Path | None:
    """数据目录路径本身带引号时，数据目录里的那份仍然有效，拷过来用。"""
    from app.core.storage import app_data_dir

    try:
        src = app_data_dir() / 'ui' / name
    except Exception:
        return None
    if src.is_file():
        return src
    return None


def chevron_down(color: str = '#98a2b3', size: int = ARROW_SIZE) -> str:
    """画一个向下的 V 形箭头，返回 PNG 绝对路径（QSS 可直接用）。

    颜色/尺寸变化会生成不同文件，避免互相覆盖。
    """
    safe = color.lstrip('#').lower()
    name = f'chevron-down-{safe}-{size}.png'
    target = ui_icon_dir() / name
    if target.is_file() and target.stat().st_size > 0:
        return str(target)
    with _DRAW_LOCK:
        if target.is_file() and target.stat().st_size > 0:
            return str(target)
        return _draw_chevron(target, name, color, size)


def _draw_chevron(target: Path, name: str, color: str, size: int) -> str:
    """真正的生成逻辑（必须在 ``_DRAW_LOCK`` 里调用）。"""
    if target.is_file():
        try:
            target.unlink()
        except OSError:
            pass

    donor = _moved_from_unsafe_dir(name)
    if donor is not None and donor.stat().st_size > 0:
        try:
            shutil.copy2(donor, target)
            return str(target)
        except OSError:
            pass

    from PyQt5.QtCore import QPointF, Qt
    from PyQt5.QtGui import QColor, QImage, QPainter, QPen

    scale = 3
    img = QImage(size * scale, size * scale, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    painter = QPainter(img)
    painter.setRenderHint(QPainter.Antialiasing, True)
    pen = QPen(QColor(color))
    pen.setWidthF(1.6 * scale)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    painter.setPen(pen)
    w = size * scale
    left = QPointF(w * 0.24, w * 0.4)
    mid = QPointF(w * 0.5, w * 0.64)
    right = QPointF(w * 0.76, w * 0.4)
    painter.drawPolyline(left, mid, right)
    painter.end()

    tmp = target.with_name(f'.{target.name}.{os.getpid()}-{threading.get_ident()}.tmp')
    if not img.save(str(tmp), 'PNG'):
        raise OSError(f'无法写入图标：{tmp}')
    try:
        tmp.replace(target)
    except OSError:
        if not target.is_file():
            raise
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
    return str(target)


def qss_path(path: str) -> str:
    """把 Windows 路径转成 QSS url() 能吃的形式。"""
    return str(path).replace('\\', '/')
