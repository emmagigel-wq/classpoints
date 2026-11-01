"""视频「能不能播」的预检与错误翻译。

为什么不写在 app/ui 里：`tests/test_layering.py` 禁止 ui 层自己建 QTimer，
而预检必须等一个很短的媒体加载窗口；这一层不碰任何控件，只碰 QtMultimedia，
所以放在 services（和 app/services/assets.py 一样可以 import Qt）。

设计要点：**预检永远不阻断业务**。环境没有多媒体后端、或者预检超时，都返回
「当作能播」，让真正的播放器去报错——预检只是尽量提前把问题说出来。
"""
from __future__ import annotations

from pathlib import Path

MEDIA_AVAILABLE = False
try:
    from PyQt5.QtCore import QEventLoop, QTimer, QUrl
    from PyQt5.QtMultimedia import QMediaContent, QMediaPlayer

    MEDIA_AVAILABLE = True
except Exception:
    QEventLoop = None
    QTimer = None
    QUrl = None
    QMediaContent = None
    QMediaPlayer = None


VIDEO_SUFFIXES = ('.mp4', '.m4v', '.mov', '.webm', '.mkv', '.avi', '.ogv', '.wmv', '.flv')


_ERROR_REASONS = {
    0: '',
    1: '播放器打不开这个文件（文件可能损坏、编码不受支持，或被其它程序占用）',
    2: '视频格式不受支持（常见于 H.265/HEVC、AV1，或 4K/10bit 素材）',
    3: '网络地址不可用（请用本地文件）',
    4: '没有权限读取这个文件（可能被其它程序独占）',
    5: '系统缺少可用的多媒体服务（Windows 的 Media Foundation 被精简过）',
}

_JUNK_DETAILS = {'error', 'error.', 'invalidmedia', 'nomedia', 'unknown'}


def error_reason(code: int) -> str:
    """把 QMediaPlayer 的错误码翻成一句能照着处理的话。"""
    return _ERROR_REASONS.get(int(code or 0), '')


def describe_failure(code: int, raw: str = '') -> str:
    """错误码 + 驱动原话，拼成给人看的说明（原话放括号里便于排查）。"""
    reason = error_reason(code)
    detail = (raw or '').strip()
    if detail.lower() in _JUNK_DETAILS or len(detail) < 4:
        detail = ''
    if reason and detail:
        return f'{reason}（{detail}）'
    if reason:
        return reason
    if detail:
        return f'播放失败：{detail}'
    return '播放失败，原因未知'


def quick_check(path: str | Path) -> tuple[bool, str]:
    """不建播放器也能判死的几种情况：文件在不在、后缀、大小。"""
    p = Path(str(path))
    if not p.exists():
        return False, '视频文件不存在（可能已被移动或删除）'
    if not p.is_file():
        return False, '这个路径不是文件'
    try:
        size = p.stat().st_size
    except OSError as exc:
        return False, f'读不到文件信息：{exc}'

    if size <= 0:
        return False, '视频文件是空的（0 字节）'
    if p.suffix.lower() not in VIDEO_SUFFIXES:
        return False, f'不是支持的视频扩展名：{p.suffix or "（无后缀）"}'
    return True, ''


def probe_video(path: str | Path, timeout_ms: int = 1500) -> tuple[bool, str]:
    """尽量提前确认这段视频播得了。

    返回 `(ok, reason)`：ok=False 时 reason 是可读的原因。
    预检失败不等于一定播不了（可能是环境限制），所以调用方应当只做提示。
    """
    ok, reason = quick_check(path)
    if not ok:
        return False, reason

    try:
        from app.services import media_decode

        if media_decode.available():
            return media_decode.probe_playable(path)
    except Exception:
        pass

    if not MEDIA_AVAILABLE:
        return True, ''

    try:
        from PyQt5.QtWidgets import QApplication

        if QApplication.instance() is None:
            return True, ''
    except Exception:
        return True, ''

    p = Path(str(path))
    try:
        player = QMediaPlayer(None)
        if not player.isAvailable():
            return True, ''
        loop = QEventLoop()
        state = {'status': None}

        def _on_status(status) -> None:
            state['status'] = status
            if status in (QMediaPlayer.LoadedMedia, QMediaPlayer.BufferedMedia,
                          QMediaPlayer.InvalidMedia):
                loop.quit()

        player.mediaStatusChanged.connect(_on_status)
        player.setMedia(QMediaContent(QUrl.fromLocalFile(str(p))))
        timer = QTimer()
        timer.setSingleShot(True)
        timer.timeout.connect(loop.quit)
        timer.start(max(200, int(timeout_ms)))
        loop.exec_()
        try:
            timer.stop()
            player.stop()
        except Exception:
            pass
        status = state['status']
        if status in (QMediaPlayer.LoadedMedia, QMediaPlayer.BufferedMedia):
            return True, ''
        if status == QMediaPlayer.InvalidMedia:
            return False, '这个视频解不开（常见于 H.265/HEVC 或损坏的文件），换一段 mp4（H.264）试试'
        return True, ''
    except Exception:
        return True, ''
