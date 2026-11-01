"""启动动画：应用程序起来后先全屏播一段动画（或用户自备视频），再进主界面。

设计约束（每一条都对应踩过的坑）：
1. **绝不能让启动卡住**：任何异常、解码失败、缺后端都直接收尾；所有路径都有一个
   硬上限 `HARD_CAP_MS`，到点必定关闭。
2. 没有配置视频、或视频打不开 → 退回内置品牌动画（不弹任何对话框，用户不该在
   开机时先看到一个错误框）。
3. `offscreen` 平台与 `--selftest` 不播放：否则测试会被动画拖慢，几何探针也会
   把动画窗口拍进去。
4. 界面层不许直接用 `QTimer`（`tests/test_layering.py` 会拦），所以计时全部用
   `QPropertyAnimation` / `QVariantAnimation` 的时间轴推进。
"""

from __future__ import annotations

import os
from pathlib import Path
from PyQt5.QtCore import (
    QEasingCurve,
    QEventLoop,
    QPointF,
    QRectF,
    Qt,
    QVariantAnimation,
    pyqtSignal,
)
from PyQt5.QtGui import (
    QColor,
    QFont,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
)
from PyQt5.QtWidgets import QVBoxLayout, QWidget

BRAND_MS = 1500
HARD_CAP_MS = 12000
BRAND_CAP_MS = 3000

_BG_TOP = QColor(238, 242, 248)
_BG_BOTTOM = QColor(226, 232, 244)
_BRAND_A = QColor(76, 110, 245)
_BRAND_B = QColor(124, 58, 237)
_INK = QColor(16, 24, 40)
_INK_2 = QColor(71, 84, 103)


def _media_available() -> bool:
    """能不能播视频。

    先看内置的解码器（ffmpeg）：这才是真正能解 H.264 的那条路——Qt5 在干净
    Windows 上用的是 DirectShow，系统不自带 H.264 解码器，QMediaPlayer 会直接
    报 InvalidMedia，用户看到的就是「启动动画没了」。
    """
    try:
        from app.services import media_decode

        if media_decode.available():
            return True
    except Exception:
        pass

    try:
        from app.ui.widgets.video_picker import MEDIA_AVAILABLE

        if not MEDIA_AVAILABLE:
            return False

        from PyQt5.QtMultimedia import QMediaPlayer

        return bool(QMediaPlayer().isAvailable())
    except Exception:
        return False


def should_play(settings, argv: list[str] | None = None) -> bool:
    """是否该播启动动画。argv 里带 `--no-splash` 可强制跳过（排障用）。"""
    argv = list(argv or [])
    if '--no-splash' in argv or '--selftest' in argv:
        return False
    if not getattr(settings, 'launch_enabled', True):
        return False
    try:
        from PyQt5.QtWidgets import QApplication

        name = QApplication.platformName() if QApplication.instance() else ''
    except Exception:
        name = ''
    if name in ('offscreen', 'minimal'):
        return False
    if os.environ.get('CLASSPOINTS_NO_SPLASH') == '1':
        return False
    return True


def _video_path(settings) -> str:
    raw = str(getattr(settings, 'launch_animation', '') or '').strip()
    if not raw:
        return ''

    from app.core import theme_store

    fixed = theme_store.resolve(raw)
    if not fixed:
        return ''

    path = Path(fixed)
    try:
        if not path.is_file() or path.stat().st_size <= 0:
            return ''
    except OSError:
        return ''
    return str(path)


class LaunchScreen(QWidget):
    """全屏启动画面。播完 / 被点掉 / 出错，都会 emit `finished`。"""

    finished = pyqtSignal()

    def __init__(self, settings, parent=None) -> None:
        super().__init__(parent)
        self.setWindowFlags(
            Qt.Window | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WA_DeleteOnClose, False)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet('background: #eef2f8;')

        self._progress = 0.0
        self._title = '班级积分系统'
        self._subtitle = str(getattr(settings, 'class_name', '') or '').strip()
        self._video_path = _video_path(settings)
        self._player = None
        self._video_widget = None
        self._done = False
        self._anim = None
        self._cap = None

        if self._video_path and _media_available():
            self._build_video()
        if self._player is None:
            self._build_brand()

    def _build_video(self) -> None:
        try:
            from app.services import media_decode

            if media_decode.available():
                from app.ui.widgets.ffmpeg_player import FfmpegMediaPlayer, VideoSurface

                holder = QVBoxLayout(self)
                holder.setContentsMargins(0, 0, 0, 0)
                holder.setSpacing(0)
                surface = VideoSurface(self)
                holder.addWidget(surface)
                player = FfmpegMediaPlayer(self)
                player.setVideoOutput(surface)
                player.mediaStatusChanged.connect(self._on_media_status)
                player.errorOccurred.connect(lambda *_: self._fallback_to_brand())
                player.setMedia(self._video_path)
                self._player = player
                self._video_widget = surface
                return
        except Exception:
            self._player = None
            self._video_widget = None

        try:
            from PyQt5.QtMultimedia import QMediaContent, QMediaPlayer
            from PyQt5.QtMultimediaWidgets import QVideoWidget

            holder = QVBoxLayout(self)
            holder.setContentsMargins(0, 0, 0, 0)
            holder.setSpacing(0)
            view = QVideoWidget(self)
            view.setStyleSheet('background: #101828;')
            holder.addWidget(view)
            player = QMediaPlayer(self)
            player.setVideoOutput(view)
            player.setMedia(QMediaContent())
            player.mediaStatusChanged.connect(
                lambda status: self._on_media_status(status)
            )
            player.error.connect(lambda *_: self._fallback_to_brand())
            player.setMedia(
                QMediaContent(__import__('PyQt5.QtCore', fromlist=['QUrl']).QUrl.fromLocalFile(self._video_path))
            )
            self._player = player
            self._video_widget = view
        except Exception:
            self._player = None
            self._video_widget = None

    def _build_brand(self) -> None:
        if self._video_widget is not None:
            self._video_widget.hide()
            try:
                self.layout().removeWidget(self._video_widget)
            except Exception:
                pass
        self.update()

    def start(self) -> None:
        """开始播放。视频路径给 HARD_CAP_MS 上限，品牌动画给 BRAND_CAP_MS。"""
        cap_ms = HARD_CAP_MS if self._player is not None else BRAND_CAP_MS
        cap = QVariantAnimation(self)
        cap.setStartValue(0.0)
        cap.setEndValue(1.0)
        cap.setDuration(cap_ms)
        cap.finished.connect(self._close_once)
        self._cap = cap
        cap.start()
        if self._player is not None:
            try:
                self._player.play()
            except Exception:
                self._fallback_to_brand()
        if self._player is None:
            self._start_brand_anim()

    def _start_brand_anim(self) -> None:
        anim = QVariantAnimation(self)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setDuration(BRAND_MS)
        anim.setEasingCurve(QEasingCurve.Linear)
        anim.valueChanged.connect(self._on_brand_tick)
        anim.finished.connect(self._close_once)
        self._anim = anim
        anim.start()

    def _on_brand_tick(self, value) -> None:
        try:
            self._progress = float(value)
        except (TypeError, ValueError):
            self._progress = 1.0
        self.update()

    def _fallback_to_brand(self) -> None:
        """视频出问题时退回品牌动画，并把硬上限缩短到品牌动画的量级。"""
        if self._done or self._anim is not None:
            return
        try:
            if self._player is not None:
                self._player.stop()
        except Exception:
            pass
        self._player = None
        if self._cap is not None:
            self._cap.stop()
        self._build_brand()
        self._start_brand_anim()

    def _on_media_status(self, status) -> None:
        """用播放器自己的枚举值比较。

        以前固定拿 `QMediaPlayer.EndOfMedia` 比，而内置播放器的常量一度是手抄的，
        两边对不上就永远等不到「播完」。
        """
        player = self._player
        end = getattr(player, 'EndOfMedia', 7)
        bad = (getattr(player, 'InvalidMedia', 8), getattr(player, 'NoMedia', 1))
        if status == end:
            self._close_once()
            return
        if status in bad:
            self._fallback_to_brand()
            return

    def mousePressEvent(self, event) -> None:
        self._close_once()

    def keyPressEvent(self, event) -> None:
        self._close_once()

    def closeEvent(self, event) -> None:
        self._shutdown()
        super().closeEvent(event)

    def _shutdown(self) -> None:
        for attr in ('_anim', '_cap'):
            anim = getattr(self, attr, None)
            if anim is None:
                continue
            try:
                anim.stop()
            except Exception:
                pass
        if self._player is not None:
            try:
                self._player.stop()
            except Exception:
                pass
            self._player = None

    def _close_once(self) -> None:
        if self._done:
            return
        self._done = True
        self._shutdown()
        self.close()
        self.finished.emit()

    def paintEvent(self, event) -> None:
        if self._player is not None:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.TextAntialiasing, True)
        w, h = self.width(), self.height()
        bg = QLinearGradient(QPointF(0, 0), QPointF(0, h))
        bg.setColorAt(0.0, _BG_TOP)
        bg.setColorAt(1.0, _BG_BOTTOM)
        p.fillRect(self.rect(), bg)
        t = max(0.0, min(1.0, self._progress))
        if t < 0.45:
            k = t / 0.45
            alpha = k
            scale = 0.92 + 0.08 * k
        elif t < 0.82:
            alpha = 1.0
            scale = 1.0
        else:
            k = (t - 0.82) / 0.18
            alpha = 1.0 - k
            scale = 1.0 + 0.04 * k
        p.setOpacity(max(0.0, min(1.0, alpha)))
        p.translate(w / 2.0, h / 2.0)
        p.scale(scale, scale)
        p.translate(-w / 2.0, -h / 2.0)
        side = max(96.0, min(w, h) * 0.16)
        box = QRectF(w / 2.0 - side / 2.0, h / 2.0 - side * 1.05, side, side)
        grad = QLinearGradient(box.topLeft(), box.bottomRight())
        grad.setColorAt(0.0, _BRAND_A)
        grad.setColorAt(1.0, _BRAND_B)
        path = QPainterPath()
        path.addRoundedRect(box, side * 0.26, side * 0.26)
        p.fillPath(path, grad)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255))
        f = QFont('Microsoft YaHei', -1, QFont.Bold)
        f.setPixelSize(max(28, int(side * 0.5)))
        p.setFont(f)
        p.drawText(box, Qt.AlignCenter, '分')
        title_y = box.bottom() + side * 0.42
        f2 = QFont('Microsoft YaHei', -1, QFont.Bold)
        f2.setPixelSize(max(20, int(side * 0.3)))
        p.setFont(f2)
        p.setPen(QPen(_INK))
        p.drawText(QRectF(0, title_y, w, side * 0.42), Qt.AlignHCenter | Qt.AlignTop, self._title)
        if self._subtitle:
            f3 = QFont('Microsoft YaHei')
            f3.setPixelSize(max(13, int(side * 0.16)))
            p.setFont(f3)
            p.setPen(QPen(_INK_2))
            p.drawText(QRectF(0, title_y + side * 0.46, w, side * 0.3), Qt.AlignHCenter | Qt.AlignTop, self._subtitle)
        p.end()


def play_launch(app, settings, argv: list[str] | None = None) -> bool:
    """播启动动画。返回是否真的播了；任何异常都静默跳过，绝不挡启动。"""
    if not should_play(settings, argv):
        return False
    try:
        screen = LaunchScreen(settings)
        screen.showFullScreen()
        app.processEvents()
        loop = QEventLoop()
        screen.finished.connect(loop.quit)
        screen.start()
        app.processEvents()
        if not screen._done:
            loop.exec_()
        else:
            loop.quit()
        try:
            screen.deleteLater()
        except Exception:
            return True
        return True
    except Exception:
        import traceback
        traceback.print_exc()
        return False
