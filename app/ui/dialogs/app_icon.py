"""自定义程序图标：选图 → 转 ICO → 原子落盘。

设置弹窗「主题 → 程序图标」用它；界面只管选文件与刷新预览，格式转换、落盘、
校验都在这里，方便单独测。

为什么自己拼 ICO：打包机里没有 Pillow，也不能为一个图标加第三方依赖。ICO
本身只是「一个目录 + 几张图」的容器，Qt 已经把 png/jpg 解码好了，这里把
QImage 缩成 16/32/48/64/256 五种尺寸，各自写成 **32 位 BMP(DIB)** 条目——
Windows 和 Qt 的 qico 读图器都认这个最老的写法，不依赖 PNG 压缩条目
（PNG 条目是 Vista 之后才允许的，兼容性差一截）。

落盘位置固定：`app.core.storage.app_data_dir() / "icon" / "app-icon.ico"`。
主窗口 / 打包脚本只需要认这一条路径；换图用「临时文件 + os.replace」原子
替换，中途失败不会留下半个图标（Windows 上 replace 是原子的）。

本模块属于 ui 层，只用 Qt 的**无窗口**类（QImage/QIcon），不建控件。
"""

from __future__ import annotations

import os
import struct
import threading
import time
from pathlib import Path

from PyQt5.QtCore import QSize, Qt
from PyQt5.QtGui import QIcon, QImage, QImageReader, QPainter

ICON_SUBDIR = "icon"
ICON_NAME = "app-icon.ico"

ICON_SIZES: tuple[int, ...] = (256, 64, 48, 32, 16)

MAX_SOURCE_PX = 4096

ALLOWED_SUFFIXES = (".ico", ".png")

_REPLACE_ATTEMPTS = 12
_REPLACE_DELAY = 0.02


def icon_dir() -> Path:
    """图标目录（`app_data_dir()/icon`），不存在就建。"""
    from app.core.storage import app_data_dir

    path = app_data_dir() / ICON_SUBDIR
    path.mkdir(parents=True, exist_ok=True)
    return path


def icon_file() -> Path:
    """固定路径的图标文件（可能还不存在）。"""
    return icon_dir() / ICON_NAME


def load_icon(path: str | Path | None) -> QIcon:
    """读一个图标文件；路径为空 / 文件不在 / 读不出来都返回空 QIcon。

    这里顺手取一张 32px 的 pixmap：既验证了「文件真能解码」，也让 Qt 把文件
    读完关掉 —— 否则 QIcon 懒加载会把句柄挂着，紧接着的覆盖写会失败。
    """
    if not path:
        return QIcon()
    try:
        p = Path(path)
        if not p.is_file():
            return QIcon()
    except (OSError, TypeError, ValueError):
        return QIcon()
    icon = QIcon(str(p))
    if icon.isNull() or icon.pixmap(32, 32).isNull():
        return QIcon()
    return icon


def remove_icon() -> None:
    """删掉落盘的图标（「恢复默认图标」用）；文件不在也算成功。"""
    try:
        target = icon_file()
    except OSError:
        return
    try:
        target.unlink()
    except FileNotFoundError:
        return
    except OSError:
        return


def apply_icon(path: str | Path | None) -> QIcon:
    """把图标设成整个程序的窗口图标，并返回它（读不出来就是空 QIcon）。

    两件事一起做：

    1. `QApplication.setWindowIcon()` —— 之后新建的窗口、任务栏、Alt-Tab 都
       默认用它（Qt 的窗口没有单独设过图标时回落到应用图标）；
    2. 遍历**已经开着**的顶层窗口补设一次 —— 只改应用图标的话，Windows 上
       已经在任务栏里的窗口不一定刷得过来（尤其设置了 AppUserModelID 之后）。

    主控在启动时调一次 `apply_icon(store.state.settings.app_icon)` 就能把上次
    选的图标带回来，不需要改窗口类。
    """
    from PyQt5.QtWidgets import QApplication

    icon = load_icon(path)
    app = QApplication.instance()
    if app is None:
        return icon
    icon = QIcon() if icon.isNull() else icon
    app.setWindowIcon(icon)
    for widget in QApplication.topLevelWidgets():
        try:
            widget.setWindowIcon(icon)
        except (AttributeError, RuntimeError):
            continue
    return icon


def build_ico(image: QImage, sizes: tuple[int, ...] = ICON_SIZES) -> bytes:
    """把一张 QImage 转成多尺寸 ICO 的字节串（纯内存，不碰磁盘）。"""
    entries = []
    for size in sizes:
        frame = _square(image, int(size))
        entries.append((int(size), _dib(frame)))
    count = len(entries)
    offset = 6 + 16 * count
    out = bytearray(struct.pack("<HHH", 0, 1, count))
    blob_start = offset
    for size, blob in entries:
        # 256px 在目录里记作 0
        dim = 0 if size >= 256 else size
        out += struct.pack(
            "<BBBBHHII",
            dim, dim,
            0, 0,
            1, 32,
            len(blob), blob_start,
        )
        blob_start += len(blob)
    for _size, blob in entries:
        out += blob
    return bytes(out)


def _square(image: QImage, size: int) -> QImage:
    """把任意比例的图贴到 size×size 的透明画布中央（不拉伸变形）。"""
    canvas = QImage(size, size, QImage.Format_ARGB32)
    canvas.fill(Qt.transparent)
    if image.isNull() or size <= 0:
        return canvas
    scaled = image.scaled(
        QSize(size, size),
        Qt.KeepAspectRatio,
        Qt.SmoothTransformation,
    )
    painter = QPainter(canvas)
    painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
    painter.drawImage(
        (size - scaled.width()) // 2,
        (size - scaled.height()) // 2,
        scaled,
    )
    painter.end()
    return canvas


def _dib(image: QImage) -> bytes:
    """一张正方形图 → ICO 里的 32 位 BMP(DIB) 条目。

    结构：BITMAPINFOHEADER(40) + XOR 位图（自底向上 BGRA）+ AND 掩码（全 0）。
    32 位图靠 alpha 通道透明，AND 掩码全 0 即「不遮」——Windows 与 Qt 都这么读。
    """
    img = image.convertToFormat(QImage.Format_ARGB32)
    width = img.width()
    height = img.height()
    stride = img.bytesPerLine()
    raw = img.constBits().asstring(img.byteCount())
    rows = [raw[y * stride : y * stride + width * 4] for y in range(height)]
    xor_bitmap = b"".join(reversed(rows))
    mask_stride = (width + 31) // 32 * 4
    and_mask = b"\x00" * (mask_stride * height)
    header = struct.pack(
        "<IiiHHIIiiII",
        40,
        width, height * 2,
        1, 32,
        0,
        len(xor_bitmap),
        0, 0, 0, 0,
    )
    return header + xor_bitmap + and_mask


def _read_image(path: Path) -> QImage | None:
    """读源图；超大图先让 reader 缩到上限再解码。"""
    reader = QImageReader(str(path))
    reader.setAutoTransform(True)
    size = reader.size()
    if not size.isValid() or size.width() <= 0 or size.height() <= 0:
        return None
    if max(size.width(), size.height()) > MAX_SOURCE_PX:
        reader.setScaledSize(size.scaled(
            MAX_SOURCE_PX, MAX_SOURCE_PX,
            Qt.KeepAspectRatio,
        ))
    image = reader.read()
    if image.isNull():
        return None
    return image


def _write_atomic(target: Path, data: bytes) -> None:
    """先写同目录临时文件再 replace：中途断电也不会留下半个 .ico。

    Windows 上只要还有别的东西开着旧图标（资源管理器生成缩略图、杀软扫描、
    Qt 刚加载的 QIcon 还没放手），replace 就会报 ACCESS_DENIED。这种锁通常
    几十毫秒就松开，所以照 storage 里那套做法重试几次；这里用 sleep 而不是
    QTimer —— UI 层不许自建计时器，而且等待总共不到 0.2 秒。
    """
    tmp = target.with_name(
        f".{target.name}.{os.getpid()}-{threading.get_ident()}.tmp"
    )
    try:
        tmp.write_bytes(data)
        for attempt in range(_REPLACE_ATTEMPTS):
            try:
                os.replace(str(tmp), str(target))
                return
            except PermissionError:
                if attempt == _REPLACE_ATTEMPTS - 1:
                    raise
                time.sleep(_REPLACE_DELAY)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def save_icon(src: str | Path) -> tuple[bool, str]:
    """把用户选的图落成 `app-icon.ico`。

    返回 ``(是否成功, 说明)``：成功时第二个值是**落盘后的绝对路径**，
    失败时是给人看的原因（直接显示在设置页的状态行里）。
    """
    try:
        source = Path(str(src))
    except (OSError, TypeError, ValueError):
        return False, "选择的文件路径无效。"
    if not source.is_file():
        return False, "选择的文件不存在，请重新选择。"
    suffix = source.suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        return False, "只支持 .ico 与 .png 图标。"
    try:
        target = icon_file()
    except OSError as exc:
        return False, f"数据目录不可用，图标存不下来：{exc}"
    if suffix == ".ico":
        # 已经是 .ico：校验能读出来再原样复制字节
        if load_icon(source).isNull():
            return False, "这个 .ico 读不出来（可能不是图标文件或已损坏）。"
        try:
            blob = source.read_bytes()
        except OSError as exc:
            return False, f"读取图标失败：{exc}"
    else:
        image = _read_image(source)
        if image is None:
            return False, "这个 png 读不出来（可能不是图片或已损坏）。"
        blob = build_ico(image)
    try:
        _write_atomic(target, blob)
    except OSError as exc:
        return False, f"保存图标失败：{exc}"
    if load_icon(target).isNull():
        remove_icon()
        return False, "图标生成失败，请换一张图片再试。"
    return True, str(target)
