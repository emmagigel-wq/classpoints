"""内置视频播放器：预览、试播、抽奖揭晓都用它播，播放失败要说清原因。

之前的做法只有「一个 QVideoWidget + 跳过按钮」，播不出来时直接关窗走人，
用户看到的是窗口一闪就没了、抽奖结果还被挡在后面——这正是「播放失误」。

这里把它做成一个真正可控的播放器：
  * 进度条可拖、可点，拖的时候不会被 positionChanged 抢回去；
  * 音量 + 静音、全屏、重播、键盘（空格 / ←→ / F / Esc）；
  * **出错不关窗**：在画面区盖一层说明（格式不支持 / 文件损坏 / 被占用…），
    给出「用系统播放器打开」和「关闭」两条出路，让用户知道发生了什么；
  * 只在正常播完时自动收窗（揭晓流程要的是「播完继续」）。

计时一律走 TimerRegistry（`tests/test_layering.py` 禁止 ui 层直接建 QTimer）；
没传 registry 时不自动隐藏控制条，功能不受影响。
"""
from __future__ import annotations

from pathlib import Path

from PyQt5.QtCore import QEvent, Qt, QUrl, pyqtSignal
from PyQt5.QtGui import QCursor, QDesktopServices
from PyQt5.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from app.services import media_decode, media_probe
from app.ui.widgets.ffmpeg_player import FfmpegMediaPlayer, VideoSurface

# 有内置 ffmpeg 就优先 —— 不依赖系统 Qt 的多媒体后端。
MEDIA_AVAILABLE = media_probe.MEDIA_AVAILABLE or media_decode.available()

try:  # 没有 QtMultimedia 也能加载本模块（降级成「不可用」）
    from PyQt5.QtMultimedia import QMediaContent, QMediaPlayer
    from PyQt5.QtMultimediaWidgets import QVideoWidget
except Exception:  # pragma: no cover
    QMediaContent = None
    QMediaPlayer = None
    QVideoWidget = None

BAR_QSS = '''
QFrame#vpBar { background: #101828; border-top: 1px solid #1f2a3d; }
QLabel#vpTime { color: #d0d5dd; font-size: 12px; }
QLabel#vpName { color: #98a2b3; font-size: 12px; }
QPushButton#vpBtn {
    background: #1d2939; color: #eef2f8; border: 1px solid #344054;
    border-radius: 7px; padding: 3px 9px; font-size: 12px;
}
QPushButton#vpBtn:hover { background: #344054; }
QPushButton#vpBtn:disabled { color: #667085; border-color: #344054; }
QSlider#vpSeek::groove:horizontal { height: 4px; background: #344054; border-radius: 2px; }
QSlider#vpSeek::sub-page:horizontal { background: #4c6ef5; border-radius: 2px; }
QSlider#vpSeek::handle:horizontal {
    background: #eef2f8; width: 11px; margin: -4px 0; border-radius: 5px;
}
QSlider#vpVol::groove:horizontal { height: 3px; background: #344054; border-radius: 2px; }
QSlider#vpVol::sub-page:horizontal { background: #98a2b3; border-radius: 2px; }
QSlider#vpVol::handle:horizontal {
    background: #eef2f8; width: 9px; margin: -3px 0; border-radius: 4px;
}
QFrame#vpError { background: #101828; }
QLabel#vpErrTitle { color: #eef2f8; font-size: 15px; font-weight: 700; }
QLabel#vpErrText { color: #98a2b3; font-size: 12.5px; }
'''


def fmt_ms(ms: int | float) -> str:
    """毫秒 → mm:ss（超过一小时给 h:mm:ss）。"""
    total = max(0, int(ms or 0) // 1000)
    hours, rest = divmod(total, 3600)
    minutes, seconds = divmod(rest, 60)
    if hours:
        return f'{hours}:{minutes:02d}:{seconds:02d}'
    return f'{minutes:02d}:{seconds:02d}'


def open_video_external(path: str | Path) -> bool:
    """用系统默认播放器打开（只在用户明确要求时作为兜底）。"""
    p = Path(str(path))
    if not p.is_file():
        return False
    try:
        return bool(QDesktopServices.openUrl(QUrl.fromLocalFile(str(p))))
    except Exception:
        return False


class VideoPlayer(QWidget):
    """视频画面 + 控制条。可单独塞进任意弹窗，也可当独立窗口用。"""

    failed = pyqtSignal(str)
    finished = pyqtSignal()
    closed = pyqtSignal()

    def __init__(self, parent: QWidget | None = None, registry=None) -> None:
        super().__init__(parent)
        self._registry = registry
        self._path = ''
        self._dur = 0
        self._failed = ''
        self._sliding = False
        self._muted = False
        self._vol = 80
        self._backend = ''

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # 画面区（错误说明盖在它上面）
        self._stage = QWidget(self)
        self._stage.setStyleSheet('background: #000000;')
        stage_lay = QVBoxLayout(self._stage)
        stage_lay.setContentsMargins(0, 0, 0, 0)
        stage_lay.setSpacing(0)

        self._video = VideoSurface(self._stage)
        self._video.setStyleSheet('background: #000000;')
        self._video.setMouseTracking(True)
        stage_lay.addWidget(self._video, 1)

        self._error = self._build_error_panel(self._stage)
        stage_lay.addWidget(self._error, 1)
        self._error.hide()

        outer.addWidget(self._stage, 1)
        self._bar = self._build_bar()
        outer.addWidget(self._bar)

        self._player = None
        self.setStyleSheet(BAR_QSS)
        self._install_media()

    def _build_error_panel(self, parent: QWidget) -> QFrame:
        panel = QFrame(parent)
        panel.setObjectName('vpError')
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(28, 28, 28, 28)
        lay.setSpacing(10)
        lay.addStretch(1)
        self._err_title = QLabel('这段视频播不出来', panel)
        self._err_title.setObjectName('vpErrTitle')
        self._err_title.setAlignment(Qt.AlignCenter)
        lay.addWidget(self._err_title)
        self._err_text = QLabel('', panel)
        self._err_text.setObjectName('vpErrText')
        self._err_text.setAlignment(Qt.AlignCenter)
        self._err_text.setWordWrap(True)
        lay.addWidget(self._err_text)
        row = QHBoxLayout()
        row.setSpacing(10)
        row.addStretch(1)
        self._err_external = QPushButton('用系统播放器打开', panel)
        self._err_external.setObjectName('vpBtn')
        self._err_external.clicked.connect(self._on_error_external)
        row.addWidget(self._err_external)
        self._err_close = QPushButton('关闭', panel)
        self._err_close.setObjectName('vpBtn')
        self._err_close.clicked.connect(self._on_error_close)
        row.addWidget(self._err_close)
        row.addStretch(1)
        lay.addLayout(row)
        lay.addStretch(1)
        return panel

    def _build_bar(self) -> QFrame:
        bar = QFrame(self)
        bar.setObjectName('vpBar')
        bar.setFixedHeight(46)
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(10, 6, 10, 6)
        lay.setSpacing(8)

        self._btn_play = QPushButton('▶', bar)
        self._btn_play.setObjectName('vpBtn')
        self._btn_play.setFixedWidth(38)
        self._btn_play.setToolTip('播放 / 暂停（空格）')
        self._btn_play.clicked.connect(self.toggle)
        lay.addWidget(self._btn_play)

        self._lbl_pos = QLabel('00:00', bar)
        self._lbl_pos.setObjectName('vpTime')
        lay.addWidget(self._lbl_pos)

        self._seek = QSlider(Qt.Horizontal, bar)
        self._seek.setObjectName('vpSeek')
        self._seek.setRange(0, 0)
        self._seek.setSingleStep(1000)
        self._seek.sliderPressed.connect(self._on_seek_press)
        self._seek.sliderReleased.connect(self._on_seek_release)
        self._seek.sliderMoved.connect(self._on_seek_move)
        lay.addWidget(self._seek, 1)

        self._lbl_dur = QLabel('00:00', bar)
        self._lbl_dur.setObjectName('vpTime')
        lay.addWidget(self._lbl_dur)

        self._btn_mute = QPushButton('🔊', bar)
        self._btn_mute.setObjectName('vpBtn')
        self._btn_mute.setFixedWidth(38)
        self._btn_mute.setToolTip('静音 / 恢复（M）')
        self._btn_mute.clicked.connect(self.toggle_mute)
        lay.addWidget(self._btn_mute)

        self._vol_slider = QSlider(Qt.Horizontal, bar)
        self._vol_slider.setObjectName('vpVol')
        self._vol_slider.setRange(0, 100)
        self._vol_slider.setValue(self._vol)
        self._vol_slider.setFixedWidth(76)
        self._vol_slider.setToolTip('音量')
        self._vol_slider.valueChanged.connect(self._on_volume)
        lay.addWidget(self._vol_slider)

        self._btn_full = QPushButton('⛶', bar)
        self._btn_full.setObjectName('vpBtn')
        self._btn_full.setFixedWidth(38)
        self._btn_full.setToolTip('全屏（F）')
        self._btn_full.clicked.connect(self.toggle_fullscreen)
        lay.addWidget(self._btn_full)

        self._lbl_name = QLabel('', bar)
        self._lbl_name.setObjectName('vpName')
        self._lbl_name.setMinimumWidth(0)
        lay.addWidget(self._lbl_name)
        return bar

    def _install_media(self) -> None:
        """优先用 ffmpeg 自绘后端；它不可用时才退回 QMediaPlayer（多半放不出来）。"""
        # fmt: off
        if media_decode.available():
            try:
                player = FfmpegMediaPlayer(self)
                player.setVideoOutput(self._video)
                player.errorOccurred.connect(self._on_error)
                player.mediaStatusChanged.connect(self._on_status)
                player.positionChanged.connect(self._on_position)
                player.durationChanged.connect(self._on_duration)
                player.stateChanged.connect(lambda _s: self._sync_play_button())
                player.setVolume(self._vol)
                self._player = player
                self._backend = 'ffmpeg'
                return
            except Exception:
                import traceback

                traceback.print_exc()
                self._player = None

        if not MEDIA_AVAILABLE or QMediaPlayer is None:
            return
        try:
            player = QMediaPlayer(self)
            self._backend = 'qt'
            player.setVideoOutput(self._video)
            player.error.connect(self._on_error)
            player.mediaStatusChanged.connect(self._on_status)
            player.positionChanged.connect(self._on_position)
            player.durationChanged.connect(self._on_duration)
            player.stateChanged.connect(lambda _s: self._sync_play_button())
            player.setVolume(self._vol)
            self._player = player
        except Exception:
            self._player = None
            return

    def available(self) -> bool:
        if media_decode.available():
            return True
        if self._player is None:
            return False
        try:
            return bool(self._player.isAvailable())
        except Exception:
            return False

    def load(self, path: str | Path, probe: bool = True) -> bool:
        """加载一段视频。文件层面就不成立时立刻显示说明，返回 False。"""
        self._path = str(path)
        self._failed = ''
        self._dur = 0
        self._seek.setRange(0, 0)
        self._lbl_pos.setText('00:00')
        self._lbl_dur.setText('00:00')
        self._lbl_name.setText(Path(self._path).name)
        self._error.hide()
        self._video.show()

        ok, reason = media_probe.quick_check(self._path)
        if not ok:
            self.show_failure(reason)
            return False
        if self._player is None:
            return False
        try:
            content = QMediaContent(QUrl.fromLocalFile(self._path))
            try:
                self._player.setMedia(content, probe=probe)
            except TypeError:
                self._player.setMedia(content)
        except Exception as exc:
            self.show_failure(f'加载失败：{exc}')
            return False
        return True

    def is_playing(self) -> bool:
        if self._player is None:
            return False
        try:
            return self._player.state() == QMediaPlayer.PlayingState
        except Exception:
            return False

    def is_failed(self) -> bool:
        return bool(self._failed)

    def failure_reason(self) -> str:
        return self._failed

    def play(self) -> None:
        if self._player is None or self._failed:
            return
        try:
            self._player.play()
        except Exception:
            pass
        self._sync_play_button(True)

    def pause(self) -> None:
        if self._player is None:
            return
        try:
            self._player.pause()
        except Exception:
            pass
        self._sync_play_button(False)

    def toggle(self) -> None:
        if self.is_playing():
            self.pause()
        else:
            self.play()

    def stop(self) -> None:
        if self._player is None:
            return
        try:
            self._player.stop()
        except Exception:
            pass
        self._sync_play_button(False)

    def seek_ms(self, ms: int) -> None:
        if self._player is None:
            return
        value = max(0, min(int(ms), max(0, self._dur)))
        try:
            self._player.setPosition(value)
        except Exception:
            return
        self._seek.setValue(value)
        self._lbl_pos.setText(fmt_ms(value))

    def seek_rel(self, delta_ms: int) -> None:
        cur = self._seek.value() if self._dur else 0
        self.seek_ms(cur + int(delta_ms))

    def set_volume(self, value: int) -> None:
        self._vol = max(0, min(100, int(value)))
        self._vol_slider.setValue(self._vol)
        if self._player is not None:
            try:
                self._player.setVolume(self._vol)
            except Exception:
                return

    def toggle_mute(self) -> None:
        self._muted = not self._muted
        self._btn_mute.setText('🔇' if self._muted else '🔊')
        if self._player is not None:
            try:
                self._player.setMuted(self._muted)
            except Exception:
                return

    def set_caption(self, text: str) -> None:
        """把说明文字交给画面控件画在底部（全屏揭晓时用）。"""
        self._video.set_caption(text)

    def set_bar_visible(self, on: bool) -> None:
        """显隐底部控制条（全屏揭晓时先藏起来，动一下鼠标再出来）。"""
        self._bar.setVisible(bool(on))

    def toggle_fullscreen(self) -> None:
        window = self.window()
        try:
            if window.isFullScreen():
                window.showNormal()
                return
            window.showFullScreen()
        except Exception:
            return

    def duration_ms(self) -> int:
        return int(self._dur)

    def position_ms(self) -> int:
        return int(self._seek.value())

    def _sync_play_button(self, playing: bool | None = None) -> None:
        if playing is None:
            playing = self.is_playing()
        self._btn_play.setText('⏸' if playing else '▶')

    def _on_duration(self, duration: int) -> None:
        self._dur = max(0, int(duration or 0))
        self._seek.setRange(0, self._dur)
        self._lbl_dur.setText(fmt_ms(self._dur))

    def _on_position(self, position: int) -> None:
        if self._sliding:
            return
        self._seek.setValue(int(position or 0))
        self._lbl_pos.setText(fmt_ms(position))

    def _on_seek_press(self) -> None:
        self._sliding = True

    def _on_seek_release(self) -> None:
        self._sliding = False
        self.seek_ms(self._seek.value())

    def _on_seek_move(self, value: int) -> None:
        self._lbl_pos.setText(fmt_ms(value))

    def _on_volume(self, value: int) -> None:
        self._vol = int(value)
        if self._muted and value > 0:
            self._muted = False
            self._btn_mute.setText('🔊')
        if self._player is not None:
            try:
                self._player.setVolume(self._vol)
                self._player.setMuted(self._muted)
            except Exception:
                return

    def _on_status(self, status) -> None:
        if status == QMediaPlayer.EndOfMedia:
            self._sync_play_button(False)
            self.finished.emit()
            return
        if status == QMediaPlayer.InvalidMedia:
            self._fail_with_player('InvalidMedia')

    def _on_error(self, *_args) -> None:
        self._fail_with_player('error')

    def _fail_with_player(self, raw: str) -> None:
        code = 0
        detail = raw
        if self._player is not None:
            try:
                code = int(self._player.error())
                detail = self._player.errorString() or raw
            except Exception:
                pass
        self.show_failure(media_probe.describe_failure(code, detail))

    def show_failure(self, reason: str) -> None:
        """把失败原因摊在画面区，而不是关窗走人。"""
        self._failed = reason or '播放失败'
        self._err_text.setText(self._failed)
        self._err_title.setText('这段视频播不出来')
        self._err_external.setVisible(Path(self._path).is_file())
        self._video.hide()
        self._error.show()
        self._sync_play_button(False)
        self.failed.emit(self._failed)

    def clear_failure(self) -> None:
        self._failed = ''
        self._error.hide()
        self._video.show()

    def _on_error_external(self) -> None:
        if not open_video_external(self._path):
            self._err_text.setText(
                (self._failed + '\n\n（系统播放器也打不开这个文件）').strip()
            )

    def _on_error_close(self) -> None:
        window = self.window()
        if window is not self:
            window.close()
        self.closed.emit()

    def mouseMoveEvent(self, event) -> None:
        self._bar.setVisible(True)
        super().mouseMoveEvent(event)

    def keyPressEvent(self, event) -> None:
        key = event.key()
        if key == Qt.Key_Space:
            self.toggle()
        elif key == Qt.Key_Left:
            self.seek_rel(-5000)
        elif key == Qt.Key_Right:
            self.seek_rel(5000)
        elif key == Qt.Key_F:
            self.toggle_fullscreen()
        elif key == Qt.Key_M:
            self.toggle_mute()
        elif key == Qt.Key_Escape:
            window = self.window()
            if getattr(window, '_exit_fullscreen_closes', False):
                window.close()
            elif window.isFullScreen():
                window.showNormal()
            else:
                self._on_error_close()
        else:
            super().keyPressEvent(event)
            return
        event.accept()


class VideoPlayerDialog(QDialog):
    """独立播放窗口。`auto_close=True`（揭晓用）播完自动关；试播则留着。"""

    playback_finished = pyqtSignal()

    def __init__(
        self,
        path: str | Path,
        parent: QWidget | None = None,
        auto_close: bool = True,
        registry=None,
        fullscreen: bool = False,
        probe: bool = True,
    ) -> None:
        super().__init__(parent)
        self._path = str(path)
        self._probe = bool(probe)
        self._auto_close = bool(auto_close)
        self._done = False
        self._fullscreen = bool(fullscreen)
        # 全屏揭晓：Esc 直接关窗（用户已经在「退出全屏」和「×」之外给了明确意图）
        self._exit_fullscreen_closes = self._fullscreen
        self._fs_applied = False
        self.setWindowTitle('视频预览')
        self.setModal(False)
        if self._fullscreen:
            self.setWindowFlags(
                Qt.Window
                | Qt.FramelessWindowHint
                | Qt.WindowStaysOnTopHint
            )
            self.setStyleSheet('QDialog { background: #000000; }')
        else:
            self.resize(760, 500)
            self.setStyleSheet('QDialog { background: #101828; }')
        self.setAttribute(Qt.WA_DeleteOnClose, True)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.player = VideoPlayer(self, registry=registry)
        lay.addWidget(self.player, 1)

        self.player.finished.connect(self._on_finished)
        self.player.failed.connect(self._on_failed)
        self.player.closed.connect(self._on_closed)

        self.player.load(self._path, probe=getattr(self, '_probe', True))
        self.player.setFocusPolicy(Qt.StrongFocus)
        self.player.setFocus()
        if self._fullscreen:
            # 控制条藏起来，动一下鼠标再出来
            self.player.set_bar_visible(False)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self._fullscreen and not self._fs_applied:
            self._fs_applied = True
            return

    def start_fullscreen(self) -> None:
        """铺满整屏并抢到前台。

        不在 showEvent 里做：窗口内部再调 showFullScreen() 有不被采纳的情况，
        而且换过 windowFlags 的窗口是先隐藏再重建的。这里显式走一遍
        showFullScreen + raise + 激活，行为可测。
        """
        self._fs_applied = True
        self.showFullScreen()
        self.raise_()
        self.activateWindow()
        handle = self.windowHandle()
        if handle is not None:
            try:
                handle.requestActivate()
            except Exception:
                return

    def available(self) -> bool:
        return self.player.available()

    def play(self) -> None:
        self.player.play()

    def failure_reason(self) -> str:
        return self.player.failure_reason()

    def is_failed(self) -> bool:
        return self.player.is_failed()

    def _on_failed(self, reason: str) -> None:
        # 失败也要把「播完了」这件事放出去，调用方才能继续流程
        self.playback_finished.emit()

    def _on_finished(self) -> None:
        self.playback_finished.emit()
        if self._auto_close:
            self._mark_done_and_close()

    def set_caption(self, text: str) -> None:
        """播放窗口上的说明文字（谁抽中了什么），由画面控件负责绘制。"""
        setter = getattr(self.player, 'set_caption', None)
        if setter is not None:
            setter(text)

    def _on_closed(self) -> None:
        self._mark_done_and_close()

    def _mark_done_and_close(self) -> None:
        if self._done:
            return
        self._done = True
        try:
            self.player.stop()
        except Exception:
            pass
        self.close()

    def closeEvent(self, event) -> None:
        try:
            self.player.stop()
        except Exception:
            pass
        try:
            self.playback_finished.emit()
        except Exception:
            pass
        super().closeEvent(event)


def make_player_dialog(path: str | Path, parent: QWidget | None = None,
                       auto_close: bool = True, registry=None,
                       fullscreen: bool = False, probe: bool = True):
    """尽力造一个内置播放器；文件不存在或没有多媒体后端时返回 None。"""
    if not MEDIA_AVAILABLE:
        return None
    if not Path(str(path)).is_file():
        return None
    try:
        dialog = VideoPlayerDialog(path, parent, auto_close=auto_close,
                                   registry=registry, fullscreen=fullscreen, probe=probe)
    except Exception:
        return None
    return dialog
