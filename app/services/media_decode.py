"""用内置 ffmpeg 解视频：探测、时长/帧率、逐帧解码、PCM 音频。

为什么不用 QtMultimedia 播放：Qt5 在 Windows 上只有 DirectShow 后端，
而干净的 Win10/11 里 DirectShow 侧**不带 H.264 解码器**，于是任何 mp4 都
`doRender: Unresolved error code 0x80040266` → InvalidMedia（实测 320x240 的
极小 h264 也一样）。我们本来就随包带了 ffmpeg，所以干脆自己解帧、自己画。

这一层不含 Qt：只负责起进程、切帧、报信息。
"""
from __future__ import annotations

import re
import os
import subprocess
import threading
import time
from pathlib import Path

from app.services import transcode


_DUR_RE = re.compile(r'Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)')
_FPS_RE = re.compile(r'([\d.]+)\s*fps')
_SIZE_RE = re.compile(r'Video:.*?(\d{2,5})x(\d{2,5})')


def available() -> bool:
    """ffmpeg 在不在（内置或 PATH）。"""
    return bool(transcode.ffmpeg_path())


def _run(args: list[str], timeout: float = 20.0) -> subprocess.CompletedProcess:
    return subprocess.run(
        args,
        capture_output=True,
        timeout=timeout,
        creationflags=transcode._no_window_flags(),
    )


_INFO_CACHE: dict[tuple, dict] = {}


def info(path: str | Path) -> dict:
    """读时长 / 帧率 / 分辨率。读不出来时给保守默认值，不抛异常。

    结果按（路径, mtime, 大小）缓存：一次抽奖只解析一遍 ffmpeg 的 stderr，
    1080p 素材那一次要 200ms 左右，重播/再抽同一段就不必再付。
    """
    key = None
    try:
        st = os.stat(str(path))
        key = (str(path), int(st.st_mtime), int(st.st_size))
        hit = _INFO_CACHE.get(key)
        if hit is not None:
            return dict(hit)
    except OSError:
        key = None

    out = {'duration_ms': 0, 'fps': 25.0, 'width': 0, 'height': 0}
    ff = transcode.ffmpeg_path()
    if not ff:
        return out
    try:
        proc = _run([ff, '-nostdin', '-hide_banner', '-i', str(path)])
    except (OSError, subprocess.SubprocessError):
        return out

    text = (proc.stderr or b'').decode('utf-8', 'replace')

    m = _DUR_RE.search(text)
    if m:
        hours, mins, secs = int(m.group(1)), int(m.group(2)), float(m.group(3))
        out['duration_ms'] = int((hours * 3600 + mins * 60 + secs) * 1000)

    m = _FPS_RE.search(text)
    if m:
        try:
            fps = float(m.group(1))
            if 1.0 <= fps <= 240.0:
                out['fps'] = fps
        except ValueError:
            pass

    m = _SIZE_RE.search(text)
    if m:
        out['width'], out['height'] = int(m.group(1)), int(m.group(2))
    if key is not None:
        _INFO_CACHE[key] = dict(out)
    return out


def probe_playable(path: str | Path) -> tuple[bool, str]:
    """真解一帧来判断「这段视频我们能不能放」。

    比 QtMultimedia 的 LoadedMedia 判定可靠：后者只说明容器能打开，
    解码器缺失时照样报"能放"（实测假阳性）。
    """
    p = Path(path)
    if not p.is_file():
        return False, f'找不到文件：{p}'
    if p.stat().st_size <= 0:
        return False, '文件是空的'
    ff = transcode.ffmpeg_path()
    if not ff:
        return False, '缺少内置转码组件（ffmpeg），无法播放视频'
    try:
        proc = _run(
            [
                ff,
                '-nostdin',
                '-hide_banner',
                '-loglevel',
                'error',
                '-ss',
                '0',
                '-i',
                str(p),
                '-frames:v',
                '1',
                '-f',
                'rawvideo',
                '-pix_fmt',
                'rgb24',
                '-',
            ],
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        return False, '解码超时：文件可能已损坏'
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f'无法调用转码组件：{exc}'
    if proc.returncode != 0 or not proc.stdout:
        detail = (proc.stderr or b'').decode('utf-8', 'replace').strip()
        first = detail.splitlines()[-1] if detail else ''
        return False, '解不开这段视频的画面' + (
            f'：{first[:120]}' if first else '（编码不受支持，或文件已损坏）'
        )
    return True, ''


class FrameSource:
    """逐帧解码：把每一帧读成 RGB24 原始字节。

    用法：
        src = FrameSource(path, width=960)
        src.open()          # 起 ffmpeg
        frame = src.read()  # bytes 或 None（结束）
        src.close()
    """

    def __init__(self, path: str | Path, width: int = 0, height: int = 0, start_ms: int = 0) -> None:
        self.path = str(path)
        self.start_ms = max(0, int(start_ms))
        self.width = int(width)
        self.height = int(height)
        self._proc = None
        self._frame_bytes = 0

    def _args(self) -> list[str]:
        ff = transcode.ffmpeg_path()
        args = [ff, '-nostdin', '-hide_banner', '-loglevel', 'error']
        if self.start_ms:
            args += ['-ss', f'{self.start_ms / 1000.0:.3f}']
        args += ['-i', self.path]
        if self.width and self.height:
            args += ['-vf', f'scale={self.width}:{self.height}']
        args += ['-an', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-']
        return args

    def open(self) -> bool:
        """起解码进程。宽高未指定时先问 info()，避免帧长度算错。"""
        if not transcode.ffmpeg_path():
            return False
        if not self.width or not self.height:
            meta = info(self.path)
            w, h = meta.get('width') or 640, meta.get('height') or 360
            self.width, self.height = w, h
        self._frame_bytes = self.width * self.height * 3
        try:
            self._proc = subprocess.Popen(
                self._args(),
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                bufsize=self._frame_bytes * 2,
                creationflags=transcode._no_window_flags(),
            )
        except OSError:
            self._proc = None
        return self._proc is not None

    def read(self) -> bytes | None:
        if self._proc is None or self._proc.stdout is None:
            return None
        chunk = self._proc.stdout.read(self._frame_bytes)
        if not chunk or len(chunk) < self._frame_bytes:
            return None
        return chunk

    def close(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None:
            return
        try:
            if proc.stdout is not None:
                proc.stdout.close()
        except OSError:
            pass
        try:
            proc.kill()
        except OSError:
            pass
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            return None


class AudioSource:
    """把音轨解成 s16le 立体声 PCM，供 QAudioOutput 推送。"""

    RATE = 44100
    CHANNELS = 2
    SAMPLE_BYTES = 2

    def __init__(self, path: str | Path, start_ms: int = 0) -> None:
        self.path = str(path)
        self.start_ms = max(0, int(start_ms))
        self._proc = None

    @property
    def chunk_bytes(self) -> int:
        return self.RATE * self.CHANNELS * self.SAMPLE_BYTES // 10

    def _args(self) -> list[str]:
        ff = transcode.ffmpeg_path()
        args = [ff, '-nostdin', '-hide_banner', '-loglevel', 'error']
        if self.start_ms:
            args += ['-ss', f'{self.start_ms / 1000.0:.3f}']
        args += [
            '-i',
            self.path,
            '-vn',
            '-ac',
            str(self.CHANNELS),
            '-ar',
            str(self.RATE),
            '-f',
            's16le',
            '-',
        ]
        return args

    def open(self) -> bool:
        if not transcode.ffmpeg_path():
            return False
        try:
            self._proc = subprocess.Popen(
                self._args(),
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                bufsize=self.chunk_bytes * 4,
                creationflags=transcode._no_window_flags(),
            )
        except OSError:
            self._proc = None
        return self._proc is not None

    def read(self, size: int) -> bytes:
        if self._proc is None or self._proc.stdout is None:
            return b''
        try:
            return self._proc.stdout.read(size) or b''
        except (OSError, ValueError):
            return b''

    def close(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None:
            return
        try:
            if proc.stdout is not None:
                proc.stdout.close()
        except OSError:
            pass
        try:
            proc.kill()
        except OSError:
            return None


def pace_sleep(index: int, fps: float, started: float) -> float:
    """按帧号把解码速度压到实时，返回实际睡眠秒数。

    不压的话 ffmpeg 会以 2~50 倍速把帧灌满管道，画面一闪到底。
    """
    if fps <= 0:
        return 0.0
    target = started + index / fps
    delta = target - time.monotonic()
    if delta <= 0:
        return 0.0
    delta = min(delta, 0.2)
    time.sleep(delta)
    return delta


def no_window_flags() -> int:
    """转发 transcode 的窗口抑制标志，方便调用方省一个 import。"""
    return transcode._no_window_flags()


__all__ = ['AudioSource', 'FrameSource', 'available', 'info', 'no_window_flags', 'pace_sleep', 'probe_playable']

_ = threading
