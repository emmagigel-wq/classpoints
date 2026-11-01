"""把「内置播放器解不开」的视频转成 H.264/AAC 的 mp4。

为什么要单独一层：`tests/test_layering.py` 禁止 ui 层自己碰外部进程，
而转码必须起 ffmpeg；这里不 import 任何 Qt 控件（连 Qt 都不 import），
所以界面层可以放心调用，也能在没有显示器的环境里单测。

三条设计底线：

1. **没有 ffmpeg 也能用**。本机不存在 ffmpeg 时 `available()` 返回 False，
   `ensure_playable()` 给出一句人话而不是抛异常；界面据此提示「下载转码组件」。
   绝不把「装个 ffmpeg 吧」这种要求甩给老师。
2. **下载地址只有一个常量**。`DOWNLOAD_URL` 是**可替换**的（内网镜像、
   离线包、以后换别家的静态构建），换源只改这一行。下载走标准库
   `urllib.request`，解压走 `zipfile`，不新增第三方依赖。
3. **失败必须说清原因**。转码/下载失败都把 ffmpeg 或网络的最后一段原话
   塞进返回值，界面照抄给用户；绝不静默失败。

进程读法：ffmpeg 的 `-progress pipe:1` 把进度写 stdout，真正的日志写 stderr。
两根管道都得读，否则日志一多就把子进程堵死（经典 PIPE 死锁）。这里用两根
读取线程 + 一个队列，**主线程**取数据并回调 `on_progress`——回调很可能直接
改 QProgressDialog，只能发生在调用方线程里。

关于 `TOOLS_DIR`：数据目录可以在设置里被用户搬走，所以模块导入时算出来的
路径只是一份**快照**（给日志/展示用）。所有判断、下载一律走 `tools_dir()`
现算，不然换过目录之后会指到一个已经不用的地方。
"""
from __future__ import annotations

import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable

from app.core.storage import app_data_dir
from app.services import media_probe


FFMPEG_EXE = 'ffmpeg.exe'

_REPO_ASSETS = Path(__file__).resolve().parents[2] / 'assets'

TOOLS_SUBDIR = 'tools'

DOWNLOAD_URL = 'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip'

_STDERR_TAIL = 400

_CHUNK = 65536

_DURATION_RE = re.compile(r'Duration:\s*(\d+):(\d{2}):(\d{2}(?:\.\d+)?)')

_PROGRESS_RE = re.compile(r'^out_time_(ms|us)=(-?\d+)$')

_CODEC_RE = re.compile(r'Stream #\d+:\d+.*?: Video: ([A-Za-z0-9_]+)')

ProgressFn = Callable[[int], None]


def tools_dir() -> Path:
    """转码组件目录：``<数据目录>/tools/``。

    每次调用都重新解析数据目录：用户可以在设置里把它搬到别的盘，
    模块导入时算一次会一直指到旧位置。
    """
    return app_data_dir() / TOOLS_SUBDIR


TOOLS_DIR = tools_dir()


def ffmpeg_path() -> str:
    """找一个能用的 ``ffmpeg.exe``，返回绝对路径；找不到返回空串。

    查找顺序：
    1. 打包内：``sys._MEIPASS/assets/ffmpeg.exe``（PyInstaller 解压目录）；
    2. 用户数据目录：``<数据目录>/tools/ffmpeg.exe``（「下载转码组件」放的）；
    3. PATH 上的 ``ffmpeg``（照顾已经自己装过的机器）。

    前两处按顺序优先，保证「随包带/用户下载」的版本一定压过系统里的旧版本。
    """
    candidates = []
    meipass = getattr(sys, '_MEIPASS', '')
    if meipass:
        candidates.append(Path(meipass) / 'assets' / FFMPEG_EXE)
    candidates.append(tools_dir() / FFMPEG_EXE)
    candidates.append(_REPO_ASSETS / FFMPEG_EXE)
    for candidate in candidates:
        try:
            if candidate.is_file():
                return str(candidate)
        except OSError:
            continue
    found = shutil.which('ffmpeg')
    return found or ''


def available() -> bool:
    """本机有没有能用的转码组件。"""
    return bool(ffmpeg_path())


def _ffmpeg_member(zf: zipfile.ZipFile) -> str | None:
    """在 zip 里挑出 ffmpeg.exe 的成员名；没有则返回 None。

    gyan 的包结构是 ``ffmpeg-<ver>-essentials_build/bin/ffmpeg.exe``，
    所以优先认 ``/bin/ffmpeg.exe`` 结尾那一项（不写死版本号）。
    兜底再放宽到任何 ``ffmpeg.exe``，免得换一家打包布局就整包作废。
    """
    names = [info.filename for info in zf.infolist() if not info.is_dir()]
    for name in names:
        if name.replace('\\', '/').lower().endswith('/bin/ffmpeg.exe'):
            return name
    for name in names:
        if name.replace('\\', '/').lower().endswith('ffmpeg.exe'):
            return name
    return None


def _report(progress: ProgressFn | None, pct: int) -> None:
    """调用进度回调；回调出错绝不能把下载/转码搞挂。"""
    if progress is None:
        return
    try:
        progress(max(0, min(100, int(pct))))
    except Exception:
        return


def download_ffmpeg(progress: ProgressFn | None = None) -> tuple[bool, str]:
    """下载并安装 ffmpeg 到 ``<数据目录>/tools/ffmpeg.exe``。

    返回 ``(ok, message)``：ok=False 时 message 是给用户看的中文原因。
    **任何情况下都不抛异常**——网络断了、地址失效、压缩包结构变了，
    都只是让界面提示一句「下载失败：…」。

    ``progress`` 可选，收到 0..100 的整数；下载占 2~60，解压占 60~98，
    装好之后报 100。
    """
    _report(progress, 0)
    target = tools_dir() / FFMPEG_EXE
    zip_path = None
    part_path = None
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, raw_zip = tempfile.mkstemp(prefix='classpoints-ffmpeg-', suffix='.zip')
        os.close(fd)
        zip_path = Path(raw_zip)
        _report(progress, 2)

        with urllib.request.urlopen(DOWNLOAD_URL, timeout=60) as resp:
            total_bytes = int(resp.headers.get('Content-Length') or 0)
            got = 0
            with open(zip_path, 'wb') as fh:
                while True:
                    chunk = resp.read(_CHUNK)
                    if not chunk:
                        break
                    fh.write(chunk)
                    got += len(chunk)
                    if total_bytes > 0:
                        _report(progress, 2 + int(got * 58 / total_bytes))
        _report(progress, 60)

        with zipfile.ZipFile(zip_path) as zf:
            member = _ffmpeg_member(zf)
            if member is None:
                return False, '下载的压缩包里没有 ffmpeg.exe（下载地址可能失效，或拿到的是精简包）'
            _report(progress, 70)
            part_path = target.with_name(f'.{target.name}.{os.getpid()}-{threading.get_ident()}.tmp')
            with zf.open(member) as src, open(part_path, 'wb') as dst:
                shutil.copyfileobj(src, dst)
        _report(progress, 92)

        os.replace(part_path, target)
        part_path = None
        _report(progress, 100)
        return True, f'转码组件已就绪：{target}'
    except urllib.error.URLError as exc:
        return False, f'下载失败（网络不可用或地址已失效）：{exc}'
    except zipfile.BadZipFile as exc:
        return False, f'下载的文件不是有效的压缩包（可能被网络拦截）：{exc}'
    except OSError as exc:
        return False, f'下载或安装转码组件失败：{exc}'
    except Exception as exc:
        return False, f'下载转码组件失败：{exc}'
    finally:
        for leftover in (zip_path, part_path):
            if leftover is None:
                continue
            try:
                if leftover.exists():
                    leftover.unlink()
            except OSError:
                pass


def video_codec(path: str | Path) -> str:
    """用内置 ffmpeg 读视频编码名（小写，如 ``h264`` / ``hevc``）。

    读不到返回空串。没有 ffmpeg 时直接返回空串，调用方回落到预检判定。
    """
    exe = ffmpeg_path()
    if not exe:
        return ''
    src = Path(str(path))
    if not src.is_file():
        return ''
    try:
        proc = subprocess.run(
            [exe, '-nostdin', '-hide_banner', '-i', str(src)],
            capture_output=True,
            timeout=20,
            creationflags=_no_window_flags(),
        )
        raw = proc.stderr or b''
    except Exception:
        return ''
    text = raw if isinstance(raw, str) else raw.decode('utf-8', 'replace')
    match = _CODEC_RE.search(text)
    if match:
        return match.group(1).lower()
    return ''


SAFE_CODECS = frozenset({'vp8', 'h264', 'vp9', 'theora', 'mpeg4'})


def needs_transcode(path: str | Path) -> tuple[bool, str]:
    """这段视频需不需要转码？返回 ``(需要, 原因)``。

    判定顺序：

    1. 文件层面的问题（不存在/0 字节/后缀不对）→ 需要处理，原因照抄预检文案；
    2. 能读到编码名 → **按编码判定**：``h264`` 之类放行，``hevc``/``av1`` 等
       一律转码。这一步是确定性的，不依赖这台机器装没装解码器；
    3. 读不到编码名（没有 ffmpeg）→ 退回 ``media_probe.probe_video`` 的预检。
    """
    ok, reason = media_probe.quick_check(path)
    if not ok:
        return True, reason or '这个文件不能直接播放'
    codec = video_codec(path)
    if codec:
        if codec in SAFE_CODECS:
            return False, ''
        return True, f'这段视频用的是 {codec.upper()} 编码，内置播放器解不开'
    ok, reason = media_probe.probe_video(path)
    if not ok:
        return True, reason or '内置播放器解不开这段视频的编码'
    return False, ''


def _default_dst(src: Path) -> Path:
    """默认产物位置。

    普通文件放同目录（``<名字>-h264.mp4``），源文件本身就叫 ``xxx-h264.mp4``
    时加 ``-2`` 免得自我覆盖。

    **但源文件在视频仓库里时一律改投临时目录**：仓库文件名是
    ``<vid>__<名字>.mp4``，产物 ``<vid>__<名字>-h264.mp4`` 与原文件共享同一个
    ``vid__`` 前缀，会被 ``videos.list_videos()`` 解析成同一条记录，
    删除原件时把刚转好的文件一起带走。
    """
    from app.core.videos import videos_dir

    candidate = src.with_name(f'{src.stem}-h264.mp4')
    if candidate == src:
        candidate = src.with_name(f'{src.stem}-h264-2.mp4')
    try:
        inside = src.resolve().parent == videos_dir().resolve()
    except OSError:
        inside = False
    if inside:
        import tempfile

        return Path(tempfile.mkdtemp(prefix='cp-tc-')) / candidate.name
    return candidate


def _build_command(exe: str, src: Path, dst: Path) -> list[str]:
    """拼 ffmpeg 参数。抽出来是为了单测能直接检查关键选项。"""
    return [
        exe,
        '-nostdin',
        '-hide_banner',
        '-i',
        str(src),
        '-c:v',
        'libx264',
        '-preset',
        'veryfast',
        '-crf',
        '23',
        '-pix_fmt',
        'yuv420p',
        '-c:a',
        'aac',
        '-b:a',
        '160k',
        '-movflags',
        '+faststart',
        '-progress',
        'pipe:1',
        '-y',
        str(dst),
    ]


def _no_window_flags() -> int:
    """Windows 下别弹黑色控制台窗口（GUI 程序里很扎眼）。"""
    return int(getattr(subprocess, 'CREATE_NO_WINDOW', 0))


def _duration_ms(text: str) -> int:
    """从 ffmpeg 的一行日志里取总时长（毫秒）；取不到返回 0。"""
    match = _DURATION_RE.search(text)
    if not match:
        return 0
    hours = int(match.group(1))
    minutes = int(match.group(2))
    seconds = float(match.group(3))
    return int((hours * 3600 + minutes * 60 + seconds) * 1000)


def _pump(stream, sink: 'queue.Queue[tuple[str, str]]', tag: str) -> None:
    """把子进程一根管道里的输出逐行推进队列（读取线程里跑）。

    读线程存在的唯一理由：stdout 与 stderr 必须同时读，否则任一管道写满
    子进程就会阻塞，转码永远跑不完。
    """
    try:
        while True:
            raw = stream.readline()
            if not raw:
                break
            if isinstance(raw, bytes):
                text = raw.decode('utf-8', 'replace')
            else:
                text = str(raw)
            sink.put((tag, text))
    except Exception:
        pass
    finally:
        try:
            stream.close()
        except Exception:
            pass


def transcode_to_mp4(
    src: str | Path,
    dst: str | Path | None = None,
    on_progress: ProgressFn | None = None,
    timeout_s: int = 900,
) -> tuple[bool, str, str]:
    """把任意视频转成「内置播放器能播」的 H.264/AAC mp4。

    参数：
        src: 源文件。``dst`` 为空时输出到同目录的 ``<名字>-h264.mp4``。
        dst: 目标路径（可省略）。
        on_progress: 可选回调，收 0..100 的整数百分比；总时长从 ffmpeg 的
            ``Duration:`` 日志里取（本机没有 ffprobe，只能这样退化）。
        timeout_s: 超时秒数，超时 kill 掉子进程并返回失败。

    返回 ``(ok, out_path, message)``：成功时 out_path 是目标绝对路径、
    message 为空；失败时 out_path 为空串、message 是中文原因（含 ffmpeg
    stderr 的末 400 字符，便于排查）。
    """
    exe = ffmpeg_path()
    if not exe:
        return False, '', '本机没有转码组件（ffmpeg），无法转换'
    src_path = Path(str(src))
    if not src_path.is_file():
        return False, '', f'找不到要转换的视频：{src_path}'
    out = Path(str(dst)) if dst else _default_dst(src_path)
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return False, '', f'无法创建输出目录：{exc}'

    part = out.with_name(f'{out.stem}.part{out.suffix}')
    _report(on_progress, 0)

    try:
        proc = subprocess.Popen(
            _build_command(exe, src_path, part),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=_no_window_flags(),
        )
    except OSError as exc:
        return False, '', f'无法启动转码组件：{exc}'

    events = queue.Queue()
    readers = [
        threading.Thread(target=_pump, args=(proc.stdout, events, 'out'), daemon=True),
        threading.Thread(target=_pump, args=(proc.stderr, events, 'err'), daemon=True),
    ]
    for reader in readers:
        reader.start()

    state = {'total_ms': 0, 'last_us': 0}
    err_lines = []

    def _flush_progress() -> None:
        total = state['total_ms']
        if total <= 0:
            return
        pct = int(state['last_us'] / 1000.0 * 100.0 / total)
        _report(on_progress, max(0, min(99, pct)))

    def _consume(kind: str, text: str) -> None:
        if kind == 'err':
            err_lines.append(text)
            if not state['total_ms']:
                total = _duration_ms(text)
                if total:
                    state['total_ms'] = total
                    _flush_progress()
            return
        match = _PROGRESS_RE.match(text.strip())
        if not match:
            return
        state['last_us'] = max(0, int(match.group(2)))
        _flush_progress()

    timed_out = False
    deadline = time.monotonic() + max(1, int(timeout_s))
    while proc.poll() is None:
        if time.monotonic() > deadline:
            timed_out = True
            try:
                proc.kill()
            except OSError:
                pass
            break
        try:
            kind, text = events.get(timeout=0.1)
        except queue.Empty:
            continue
        _consume(kind, text)

    for reader in readers:
        reader.join(timeout=5.0)
    while True:
        try:
            kind, text = events.get_nowait()
        except queue.Empty:
            break
        _consume(kind, text)

    try:
        code = proc.wait()
    except OSError:
        code = -1

    if timed_out:
        _unlink_quietly(part)
        return False, '', f'转码超时（超过 {int(timeout_s)} 秒）。视频可能太长或文件已经损坏。'
    if code != 0:
        tail = ''.join(err_lines).strip()[-_STDERR_TAIL:]
        _unlink_quietly(part)
        return False, '', f'转码失败（ffmpeg 退出码 {code}）：{tail or "没有更多信息"}'
    if not part.is_file():
        return False, '', '转码似乎完成了，但没有生成输出文件（转码组件被中途结束？）'
    try:
        os.replace(part, out)
    except OSError as exc:
        _unlink_quietly(part)
        return False, '', f'转码完成但无法写入目标文件：{exc}'
    _report(on_progress, 100)
    return True, str(out), ''


def _unlink_quietly(path: Path) -> None:
    """删临时产物；删不掉也不该影响主流程。"""
    try:
        if path.exists():
            path.unlink()
    except OSError:
        return None


def ensure_playable(src: str | Path, on_progress: ProgressFn | None = None) -> tuple[bool, str, str]:
    """「给内置播放器一个能播的路径」的高层入口。

    返回 ``(ok, path_to_use, message)``：

    * 预检通过 → ``(True, src, "")``，原样播；
    * 预检失败且本机没有 ffmpeg → ``(False, src, 中文提示)``，
      界面据此提示「下载转码组件」，而不是让老师自己去装东西；
    * 预检失败但有 ffmpeg → 转成同目录 ``<名字>-h264.mp4``，
      成功返回新路径，失败返回原路径 + 原因。
    """
    path = str(src)
    need, why = needs_transcode(path)
    if not need:
        return True, path, ''
    if not ffmpeg_path():
        return False, path, '这段视频内置播放器解不开，需要转码，但本机没有转码组件'
    done, out_path, message = transcode_to_mp4(path, on_progress=on_progress)
    if done:
        return True, out_path, ''
    return False, path, message or '转码失败'
