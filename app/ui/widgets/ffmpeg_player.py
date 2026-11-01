"""自绘视频播放器：用内置 ffmpeg 解帧 + 自己画，绕开 DirectShow 没有 H.264 解码器的问题。

背景（实测）：Qt5 在 Windows 只有 DirectShow 后端，干净的 Win10/11 里 DirectShow
侧不带 H.264 解码器，任何 mp4 都会 `doRender: Unresolved error code 0x80040266`
→ InvalidMedia，连 320×240 的极小 h264 也一样。而音频（mp3）能播。
所以我们不再依赖系统解码器：ffmpeg 解 RGB24 帧 → QImage → QPainter 画；
音频另起 ffmpeg 解 s16le 交给 QAudioOutput。

`FfmpegMediaPlayer` 故意做成 QMediaPlayer 的形状（同名方法/信号/常量），
这样 `VideoPlayer` 里原有的调用点一行都不用改。
"""

from __future__ import annotations

import collections
import threading
import time
from pathlib import Path

from PyQt5.QtCore import QObject, QRect, QTimer, QUrl, pyqtSignal
from PyQt5.QtGui import QImage, QPainter
from PyQt5.QtMultimedia import QAudioFormat, QAudioOutput, QMediaPlayer
from PyQt5.QtWidgets import QWidget

from app.services import media_decode

# 播放器对象偶尔要在 Python 侧多留一会儿，避免 QAudioOutput 被 GC 得太早。
_KEEP_ALIVE: list = []


class VideoSurface(QWidget):
    """只负责把一帧 QImage 等比画在黑底上。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._frame = None
        self._caption = ''
        self.setAutoFillBackground(False)
        self.setMouseTracking(True)

    def set_frame(self, image: QImage | None) -> None:
        self._frame = image
        self.update()

    def _draw_caption(self, painter, target) -> None:
        if not self._caption or target.isEmpty():
            return
        from PyQt5.QtCore import Qt as _Qt
        from PyQt5.QtGui import QColor, QFont

        bar_h = max(44, int(target.height() * 0.13))
        bar = QRect(
            target.left(), target.bottom() - bar_h + 1,
            target.width(), bar_h,
        )
        painter.fillRect(bar, QColor(0, 0, 0, 150))
        font = QFont(painter.font())
        font.setPixelSize(max(15, int(bar_h * 0.4)))
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor('#ffffff'))
        painter.drawText(bar, _Qt.AlignCenter, self._caption)

    def set_caption(self, text: str) -> None:
        """画面底部压一条说明（揭晓全屏时用来写「谁抽中了什么」）。"""
        self._caption = str(text or '')
        self.update()

    def frame(self) -> QImage | None:
        return self._frame

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), self.palette().window())
        painter.fillRect(self.rect(), _BLACK)
        frame = self._frame
        if frame is None or frame.isNull():
            return
        target = _fit(frame.width(), frame.height(), self.width(), self.height())
        if target.isEmpty():
            return
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        painter.drawImage(target, frame)
        self._draw_caption(painter, target)


from PyQt5.QtGui import QBrush, QColor

_BLACK = QBrush(QColor('#000000'))


def _fit(sw: int, sh: int, dw: int, dh: int) -> QRect:
    """等比缩放，居中，留黑边。"""
    if sw <= 0 or sh <= 0 or dw <= 0 or dh <= 0:
        return QRect()
    scale = min(dw / sw, dh / sh)
    w = max(1, int(sw * scale))
    h = max(1, int(sh * scale))
    return QRect((dw - w) // 2, (dh - h) // 2, w, h)


class FfmpegMediaPlayer(QObject):
    """QMediaPlayer 的替身：ffmpeg 解码 + 自绘。

    只实现 VideoPlayer 用到的那部分接口。
    """

    positionChanged = pyqtSignal(int)
    durationChanged = pyqtSignal(int)
    stateChanged = pyqtSignal(int)
    mediaStatusChanged = pyqtSignal(int)

    errorOccurred = pyqtSignal(int)

    _frame_ready = pyqtSignal(object)

    # 同名常量：调用点写 QMediaPlayer.PlayingState 时不用改。
    StoppedState = QMediaPlayer.StoppedState
    PlayingState = QMediaPlayer.PlayingState
    PausedState = QMediaPlayer.PausedState
    NoMedia = QMediaPlayer.NoMedia
    LoadingMedia = QMediaPlayer.LoadingMedia
    LoadedMedia = QMediaPlayer.LoadedMedia
    BufferedMedia = QMediaPlayer.BufferedMedia
    EndOfMedia = QMediaPlayer.EndOfMedia
    InvalidMedia = QMediaPlayer.InvalidMedia
    ResourceError = QMediaPlayer.ResourceError
    FormatError = QMediaPlayer.FormatError
    NetworkError = QMediaPlayer.NetworkError
    AccessDeniedError = QMediaPlayer.AccessDeniedError

    # 解码宽度：原版 VideoPlayer 的舞台区域也就这么宽，再高没意义还费 CPU。
    TARGET_W = 960

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._surface = None
        self._path = ''
        self._meta = {'duration_ms': 0, 'fps': 25.0, 'width': 0, 'height': 0}
        self._state = self.StoppedState
        self._status = self.NoMedia
        self._err = 0
        self._err_str = ''
        self._vol = 80
        self._muted = False
        self._pos_ms = 0
        self._stopping = False

        self._video_thread = None
        self._audio_thread = None
        self._audio_proc = None
        self._audio_out = None
        self._audio_dev = None
        self._audio_buf = collections.deque()
        self._audio_done = False
        self._audio_timer = QTimer(self)
        self._audio_timer.setInterval(20)
        self._audio_timer.timeout.connect(self._pump_audio)

        self._frame_ready.connect(self._show_frame)

    def setVideoOutput(self, widget) -> None:
        if isinstance(widget, VideoSurface):
            self._surface = widget
            return
        self._surface = None

    def setMedia(self, content, probe: bool = True) -> None:
        """content 是 QMediaContent；我们只取本地文件路径。

        probe=False 跳过「先解一帧验证」——揭晓自动播放要抢时间，
        真解不开会由解码线程报错并走失败态。
        """
        path = ''
        try:
            url = content.canonicalUrl()
            if url.isLocalFile():
                path = url.toLocalFile()
        except Exception:
            path = ''
        if not path and isinstance(content, str):
            path = content
        self._path = path
        self._err, self._err_str = 0, ''
        self._pos_ms = 0
        if not path or not Path(path).is_file():
            self._status = self.InvalidMedia
            self._err = self.ResourceError
            self._err_str = f'找不到文件：{path}'
            self.mediaStatusChanged.emit(self.InvalidMedia)
            self.errorOccurred.emit(self._err)
            return
        if probe:
            ok, reason = media_decode.probe_playable(path)
        else:
            ok, reason = True, ''
        if not ok:
            self._status = self.InvalidMedia
            self._err = self.FormatError
            self._err_str = reason
            self.mediaStatusChanged.emit(self.InvalidMedia)
            self.errorOccurred.emit(self._err)
            return
        self._meta = media_decode.info(path)
        self._status = self.LoadedMedia
        self.durationChanged.emit(int(self._meta.get('duration_ms') or 0))
        self.mediaStatusChanged.emit(self.LoadedMedia)

    def isAvailable(self) -> bool:
        return media_decode.available()

    def play(self) -> None:
        if not self._path or self._status == self.InvalidMedia:
            return
        if self._state == self.PlayingState:
            return
        self._start_at(self._pos_ms)

    def pause(self) -> None:
        if self._state != self.PlayingState:
            return
        self._pos_ms = self.position()
        self._stop_workers()
        self._set_state(self.PausedState)

    def stop(self) -> None:
        self._stop_workers()
        self._pos_ms = 0
        self._set_state(self.StoppedState)
        self.positionChanged.emit(0)

    def setPosition(self, ms: int) -> None:
        value = max(0, min(int(ms), int(self._meta.get('duration_ms') or 0)))
        was_playing = self._state == self.PlayingState
        self._stop_workers()
        self._pos_ms = value
        self.positionChanged.emit(value)
        if was_playing:
            self._start_at(value)
            return

        self._set_state(self.PausedState)

    def position(self) -> int:
        return int(self._pos_ms)

    def duration(self) -> int:
        return int(self._meta.get('duration_ms') or 0)

    def state(self) -> int:
        return self._state

    def error(self) -> int:
        return int(self._err)

    def errorString(self) -> str:
        return self._err_str

    def isVideoAvailable(self) -> bool:
        return self._status in (self.LoadedMedia, self.BufferedMedia)

    def isAudioAvailable(self) -> bool:
        return self.isVideoAvailable()

    def setVolume(self, value: int) -> None:
        self._vol = max(0, min(100, int(value)))
        if self._audio_out is not None:
            try:
                self._audio_out.setVolume(0.0 if self._muted else self._vol / 100.0)
            except Exception:
                pass

    def setMuted(self, muted: bool) -> None:
        self._muted = bool(muted)
        if self._audio_out is not None:
            try:
                self._audio_out.setVolume(0.0 if self._muted else self._vol / 100.0)
            except Exception:
                pass

    def _set_state(self, state: int) -> None:
        self._state = state
        self.stateChanged.emit(state)

    def _show_frame(self, image) -> None:
        if self._surface is not None and image is not None:
            self._surface.set_frame(image)

    def _target_size(self) -> tuple[int, int]:
        w0 = int(self._meta.get('width') or 0)
        h0 = int(self._meta.get('height') or 0)
        if w0 <= 0 or h0 <= 0:
            return (self.TARGET_W, 0)
        height = max(2, int(round(self.TARGET_W * h0 / w0 / 2)) * 2)
        return (self.TARGET_W, height)

    def _start_at(self, start_ms: int) -> None:
        # 上一轮的线程还没退干净就再开一条，会让两路 ffmpeg 抢同一个声卡。
        self._stop_workers()
        self._stopping = False
        self._pos_ms = max(0, int(start_ms))
        fps = float(self._meta.get('fps') or 25.0)
        self._set_state(self.PlayingState)
        self._start_audio(self._pos_ms)
        width, height = self._target_size()
        self._video_thread = threading.Thread(
            target=self._decode_loop, args=(self._pos_ms, fps, width, height),
            name='cp-video', daemon=True,
        )
        self._video_thread.start()

    def _decode_loop(self, start_ms: int, fps: float, width: int, height: int) -> None:
        src = media_decode.FrameSource(self._path, width, height, start_ms)
        if not src.open():
            self.errorOccurred.emit(self.ResourceError)
            self.mediaStatusChanged.emit(self.InvalidMedia)
            self._set_state(self.StoppedState)
            return
        started = time.monotonic()
        last_emit = 0.0
        index = 0
        frame_bytes = src.width * src.height * 3
        # stride 单独算：QImage 要求每行字节数对齐，跟帧总长不是一回事。
        stride = src.width * 3
        try:
            while not self._stopping:
                data = src.read()
                if data is None:
                    break
                image = QImage(data, src.width, src.height, stride, QImage.Format_RGB888).copy()
                try:
                    self._frame_ready.emit(image)
                except RuntimeError:
                    return
                index += 1
                now_ms = start_ms + int(index * 1000.0 / fps)
                self._pos_ms = now_ms
                now = time.monotonic()
                if now - last_emit >= 0.15:
                    last_emit = now
                    self.positionChanged.emit(now_ms)
                media_decode.pace_sleep(index, fps, started)
        except RuntimeError:
            return
        finally:
            src.close()
        if not self._stopping:
            self._pos_ms = int(self._meta.get('duration_ms') or self._pos_ms)
            try:
                self.positionChanged.emit(self._pos_ms)
                self._stop_audio()
                self.mediaStatusChanged.emit(self.EndOfMedia)
                self._set_state(self.StoppedState)
            except RuntimeError:
                return

    def _start_audio(self, start_ms: int) -> None:
        if self._muted or self._vol <= 0:
            return
        if not media_decode.transcode.ffmpeg_path():
            return
        fmt = QAudioFormat()
        fmt.setSampleRate(media_decode.AudioSource.RATE)
        fmt.setChannelCount(media_decode.AudioSource.CHANNELS)
        fmt.setSampleSize(16)
        fmt.setCodec('audio/pcm')
        fmt.setByteOrder(QAudioFormat.LittleEndian)
        fmt.setSampleType(QAudioFormat.SignedInt)
        try:
            out = QAudioOutput(fmt, self)
            out.setVolume(self._vol / 100.0)
            dev = out.start()
        except Exception:
            return
        if dev is None:
            return
        self._audio_out, self._audio_dev = out, dev
        self._audio_buf.clear()
        self._audio_done = False
        self._audio_thread = threading.Thread(
            target=self._audio_reader, args=(start_ms,),
            name='cp-audio-read', daemon=True,
        )
        self._audio_thread.start()
        self._audio_timer.start()

    def _audio_reader(self, start_ms: int) -> None:
        """只负责从 ffmpeg 管道读 PCM 塞进队列，绝不碰 Qt 设备。"""
        src = media_decode.AudioSource(self._path, start_ms)
        if not src.open():
            self._audio_done = True
            return
        chunk = src.chunk_bytes
        try:
            while not self._stopping:
                if len(self._audio_buf) > 24:
                    time.sleep(0.02)
                    continue
                data = src.read(chunk)
                if not data:
                    break
                self._audio_buf.append(data)
        finally:
            src.close()
            self._audio_done = True

    def _pump_audio(self) -> None:
        """主线程把队列里的 PCM 写进音频设备（QIODevice 不能跨线程用）。"""
        dev = self._audio_dev
        if dev is None:
            self._audio_timer.stop()
            return
        budget = 4
        while budget > 0 and self._audio_buf:
            data = self._audio_buf.popleft()
            budget -= 1
            try:
                n = dev.write(data)
            except (OSError, RuntimeError):
                self._stop_audio()
                return
            if n is None or n < 0:
                self._audio_buf.appendleft(data)
                return
            if int(n) < len(data):
                self._audio_buf.appendleft(data[int(n):])
                return
        if self._audio_done and not self._audio_buf:
            self._audio_timer.stop()

    def _stop_audio(self) -> None:
        self._audio_timer.stop()
        self._audio_buf.clear()
        self._audio_done = True
        out, dev = self._audio_out, self._audio_dev
        self._audio_out, self._audio_dev = None, None
        if out is not None:
            try:
                out.stop()
            except Exception:
                pass
            try:
                out.deleteLater()
            except Exception:
                pass
            # 立刻释放 QAudioOutput 会让 Windows 侧的音频后端偶发崩，先留着。
            _KEEP_ALIVE.append(out)

    def _stop_workers(self) -> None:
        self._stopping = True
        threads = [t for t in (self._video_thread, self._audio_thread) if t]
        for t in threads:
            t.join(timeout=2.0)
        self._video_thread = None
        self._audio_thread = None
        self._stop_audio()


__all__ = ['FfmpegMediaPlayer', 'VideoSurface']
