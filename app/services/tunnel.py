"""公网隧道：让手机不在同一 WiFi（异网）也能访问班级积分。

实现方式是调用 Cloudflare 官方的 `cloudflared` 做「快速隧道」
（`cloudflared tunnel --url http://127.0.0.1:端口`），
它会给一个临时的 https://xxx.trycloudflare.com 地址，
不需要注册账号、不需要域名、不需要路由器端口映射。

本模块做四件事：
1. **体检** cloudflared.exe：不只看文件大小，还读 PE 头确认它真是本机可执行的
   程序（安装包里的旧组件曾出现「PE 头缺可执行标志、Windows 报 WinError 193」
   的坏文件，只按大小判断会一直拿到坏 exe，点按钮必然没反应）；
2. 按 安装包自带(可用) → PATH → 程序数据目录 → 常见安装目录 找组件；
3. 需要时下载它（必须由用户点按钮触发，不静默联网），下载后先校验 PE 再试运行
   `--version`，坏文件绝不替换到正式位置；
4. 起/停隧道进程：**重复点击不会重复拉隧道**（并发调用共享同一个启动结果），
   退出时用进程树方式清掉 cloudflared 及其子进程，不留僵尸。
"""
from __future__ import annotations

import atexit
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
import weakref
from collections import deque
from pathlib import Path

logger = logging.getLogger(__name__)

_MIRROR_PREFIXES = ('', 'https://ghproxy.net/')
_AMD64_ASSET = 'cloudflared-windows-amd64.exe'
_ARM64_ASSET = 'cloudflared-windows-arm64.exe'
DOWNLOAD_URLS = tuple(prefix + 'https://github.com/cloudflare/cloudflared/releases/latest/download/' +
                      _AMD64_ASSET
                      for prefix in _MIRROR_PREFIXES)
MIN_EXE_BYTES = 1_000_000
_URL_RE = re.compile(r'https://(?!api\.|www\.)[a-z0-9][a-z0-9-]*\.trycloudflare\.com', re.I)
_PE_MACHINES = {0x14c: 'x86', 0x8664: 'x64', 0xaa64: 'ARM64'}
_PE_EXECUTABLE_IMAGE = 2
_CREATE_NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0) if os.name == 'nt' else 0


def extract_public_url(line: str) -> str | None:
    "从 cloudflared 的一行日志里抠出公网地址（纯函数，便于测试）。"
    m = _URL_RE.search(line or '')
    return m.group(0).rstrip('/') if m else None


def app_dir() -> Path:
    """穿透组件放哪：程序数据目录（和 data.json 放一起，不污染安装目录）。

    `CLASSPOINTS_TOOLS_DIR` 供测试/便携版指定别的目录。
    """
    override = (os.environ.get('CLASSPOINTS_TOOLS_DIR') or '').strip()
    if override:
        p = Path(override)
    else:
        base = os.environ.get('APPDATA') or str(Path.home())
        p = Path(base) / '班级积分系统' / 'tools'
    try:
        p.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return p


def platform_supported() -> bool:
    return sys.platform.startswith('win') or sys.platform == 'darwin' or sys.platform.startswith('linux')


def _exe_names() -> tuple[str, ...]:
    if sys.platform.startswith('win'):
        return ('cloudflared.exe',)
    return ('cloudflared', 'cloudflared.exe')


def _host_machine() -> int | None:
    "本机 CPU 架构（PE Machine 口径）；判断不了就返回 None（不拦）。"
    if not sys.platform.startswith('win'):
        return None
    arch = (os.environ.get('PROCESSOR_ARCHITECTURE') or '').upper()
    if arch == 'AMD64':
        return 0x8664
    if arch == 'X86':
        return 0x14c
    if arch == 'ARM64':
        return 0xaa64
    return None


def _host_is_64bit() -> bool:
    return sys.maxsize > 2**32

_inspect_cache: dict[tuple[str, int, int], tuple[bool, str]] = {}
_inspect_lock = threading.Lock()
_warned_bad: set[tuple[str, int]] = set()
_warn_lock = threading.Lock()


def _warn_bad_once(path: Path, reason: str) -> None:
    "坏组件只提醒一次，避免设置页每次刷新都把 app.log 刷满。"
    try:
        key = (str(path), path.stat().st_size)
    except OSError:
        return
    with _warn_lock:
        if key in _warned_bad:
            return
        if len(_warned_bad) > 16:
            _warned_bad.clear()
        _warned_bad.add(key)
    logger.warning('忽略不可用的内置穿透组件 %s：%s', path, reason)


def inspect_pe(path: Path | str) -> tuple[bool, str]:
    """只看 PE 头判断「这个文件在本机能不能跑」，不启动进程。

    返回 (ok, 原因)。坏文件（比如安装包里那份被截断、节表数据段超出
    文件末尾的）会得到 False + 中文原因，调用方据此跳过它并去下载/找别的副本。
    """
    p = Path(path)
    try:
        st = p.stat()
    except OSError as exc:
        return (False, f'读不到文件：{exc}')
    key = (str(p), st.st_size, st.st_mtime_ns)
    with _inspect_lock:
        cached = _inspect_cache.get(key)
    if cached is not None:
        return cached
    ok, reason = _inspect_pe_uncached(p, st.st_size)
    with _inspect_lock:
        if len(_inspect_cache) > 32:
            _inspect_cache.clear()
        _inspect_cache[key] = (ok, reason)
    return (ok, reason)


def _inspect_pe_uncached(path: Path, size: int) -> tuple[bool, str]:
    if size < MIN_EXE_BYTES:
        return (False, f'文件只有 {size / 1024 / 1024:.1f} MB，可能没下载完整')
    try:
        with open(path, 'rb') as fh:
            head = fh.read(64)
            if len(head) < 64 or head[:2] != b'MZ':
                return (False, '不是 Windows 可执行文件（缺少 MZ 头）')
            pe_off = int.from_bytes(head[60:64], 'little')
            if pe_off <= 0 or pe_off > size - 64:
                return (False, '不是有效的 Windows 可执行文件（PE 头偏移损坏）')
            fh.seek(pe_off)
            coff = fh.read(24)
            if len(coff) < 24 or coff[:4] != b'PE\0\0':
                return (False, '不是有效的 Windows 可执行文件（缺少 PE 签名）')
            machine = int.from_bytes(coff[4:6], 'little')
            sections = int.from_bytes(coff[6:8], 'little')
            opt_size = int.from_bytes(coff[20:22], 'little')
            characteristics = int.from_bytes(coff[22:24], 'little')
            if machine not in _PE_MACHINES:
                return (False, f'CPU 架构不支持（Machine=0x{machine:04X}），请下载 x64 版本')
            if not characteristics & _PE_EXECUTABLE_IMAGE:
                return (False, '文件头不完整（缺少可执行标志），可能下载被拦截或截断')
            if sections <= 0 or sections > 96 or opt_size < 64:
                return (False, '文件头不完整（节表为空），请重新下载')
            optional = fh.read(opt_size)
            if len(optional) < opt_size:
                return (False, '文件头不完整（可选头被截断），请重新下载')
            magic = int.from_bytes(optional[0:2], 'little')
            if magic not in (0x10b, 0x20b):
                return (False, '不是有效的 PE32/PE32+ 文件')
            size_headers = int.from_bytes(optional[60:64], 'little')
            size_image = int.from_bytes(optional[56:60], 'little')
            entry = int.from_bytes(optional[16:20], 'little')
            if size_headers < 64 or size_headers > size:
                return (False, '文件头不完整（文件头长度异常），请重新下载')
            if size_image < 4096:
                return (False, '文件头不完整（映像大小异常），请重新下载')
            table = fh.read(40 * sections)
    except OSError as exc:
        return (False, f'读不到文件：{exc}')
    if len(table) < 40 * sections:
        return (False, '文件不完整（节表超出文件末尾），可能下载中断')
    tail_end = 0
    for index in range(sections):
        off = 40 * index
        raw_size = int.from_bytes(table[off + 16:off + 20], 'little')
        raw_ptr = int.from_bytes(table[off + 20:off + 24], 'little')
        if raw_size == 0:
            continue
        if raw_ptr < 64 or raw_ptr + raw_size > size:
            return (False, '文件不完整（数据段超出文件末尾），可能下载中断，请重新下载')
        tail_end = max(tail_end, raw_ptr + raw_size)
    if tail_end and tail_end > size:
        return (False, '文件不完整（数据段超出文件末尾），可能下载中断，请重新下载')
    if entry < 4096 or entry >= size_image:
        return (False, '文件头不完整（入口地址异常），请重新下载')
    host = _host_machine()
    if host is not None:
        is64 = magic == 0x20b
        if machine == 0xaa64 and host != 0xaa64:
            return (False, '这是 ARM64 版本，当前电脑是 x64/32 位')
        if host == 0x14c and is64:
            return (False, '这是 64 位程序，当前系统是 32 位 Windows')
        if machine == 0x8664 and host == 0x14c:
            return (False, '这是 x64 程序，当前系统是 32 位 Windows')
    return (True, '')


def _is_runnable(path: Path) -> tuple[bool, str]:
    "跨平台的可执行性判断：Windows 看 PE 头，其它系统看权限位。"
    if not path.is_file():
        return (False, '文件不存在')
    if sys.platform.startswith('win'):
        return inspect_pe(path)
    try:
        if path.stat().st_size < MIN_EXE_BYTES:
            return (False, '文件太小，可能没下载完整')
        if not os.access(path, os.X_OK):
            return (False, '文件没有执行权限')
    except OSError as exc:
        return (False, f'读不到文件：{exc}')
    return (True, '')



def _bundled_candidates() -> list[Path]:
    "安装包里可能放组件的所有位置（不判断是否可用）。"
    roots = []
    meipass = getattr(sys, '_MEIPASS', None)
    if meipass:
        roots.append(Path(meipass))
        try:
            roots.append(Path(sys.executable).resolve().parent)
        except OSError:
            pass
        roots.append(Path(sys.executable).resolve().parent / '_internal')
    else:
        here = Path(__file__).resolve()
        roots.append(here.parents[2])
    out = []
    for root in roots:
        for name in _exe_names():
            for cand in (root / name, root / 'assets' / name, root / '_internal' / name, root / '_internal' / 'assets' / name):
                if cand not in out:
                    out.append(cand)
    return out


def _bundled_report() -> list[tuple[Path, bool, str]]:
    "[(路径, 是否可用, 原因)]，给组件体检和错误信息用。"
    report = []
    for cand in _bundled_candidates():
        try:
            if not cand.is_file():
                continue
        except OSError:
            continue
        ok, reason = _is_runnable(cand)
        report.append((cand, ok, reason))
    return report


def bundled_cloudflared() -> Path | None:
    """安装包自带的**可用**穿透组件；坏文件当作不存在。

    这样设置页会露出「下载穿透组件」按钮 —— 用户点一下就能自愈，而不是
    永远拿着一个 WinError 193 的坏 exe 反复失败。
    """
    for cand, ok, reason in _bundled_report():
        if ok:
            return cand
        _warn_bad_once(cand, reason)
    return None


def _path_candidate() -> Path | None:
    exe = shutil.which('cloudflared') or shutil.which('cloudflared.exe')
    if exe:
        return Path(exe)
    return None


def _download_candidates() -> list[Path]:
    candidates = [app_dir() / 'cloudflared.exe']
    local = os.environ.get('LOCALAPPDATA', '')
    if local:
        candidates.append(Path(local) / 'cloudflared' / 'cloudflared.exe')
    candidates.extend([Path(r'C:\Program Files\cloudflared\cloudflared.exe'), Path(r'C:\Program Files (x86)\cloudflared\cloudflared.exe')])
    return candidates


def find_all_cloudflared() -> list[Path]:
    "所有**通过体检**的组件副本，按优先级排序（去重）。"
    found = []
    bundled = bundled_cloudflared()
    if bundled is not None:
        found.append(bundled)
    path_exe = _path_candidate()
    if path_exe is not None:
        found.append(path_exe)
    found.extend(_download_candidates())
    out = []
    seen = set()
    for cand in found:
        try:
            key = str(cand.resolve()).lower()
        except OSError:
            key = str(cand).lower()
        if key in seen:
            continue
        seen.add(key)
        ok, reason = _is_runnable(cand)
        if ok:
            out.append(cand)
        elif cand.is_file():
            logger.warning('跳过不可用的穿透组件 %s：%s', cand, reason)
    return out


def hostname_alive(hostname: str, timeout: float = 2.5, base_url: str | None = None) -> bool:
    """域名上是否已经有活的同步服务。

    用途：同一台命名隧道被两台电脑同时连接时，Cloudflare 会把请求轮转给两边，
    用户看到的就是「有时能开、有时打不开」。启动前先探一下，已经在跑就不要再抢。
    """
    host = (hostname or '').strip().strip('/')
    if not host and not base_url:
        return False
    root = (base_url or f'https://{host}').rstrip('/')
    req = urllib.request.Request(root + '/api/ping', method='GET')
    req.add_header('User-Agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)')
    req.add_header('Accept', 'application/json')
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read(2000).decode('utf-8', 'replace')
            return resp.status == 200 and '"ok"' in body.replace(' ', '')
    except Exception:
        return False


def find_cloudflared() -> Path | None:
    "按 安装包自带(可用) → PATH → 程序数据目录 → 常见目录 找。"
    bundled = bundled_cloudflared()
    if bundled is not None:
        return bundled
    path_exe = _path_candidate()
    if path_exe is not None:
        ok, reason = _is_runnable(path_exe)
        if ok:
            return path_exe
        logger.warning('PATH 里的 cloudflared 不可用：%s', reason)
    for cand in _download_candidates():
        ok, reason = _is_runnable(cand)
        if ok:
            return cand
        if cand.is_file():
            logger.warning('跳过不可用的穿透组件 %s：%s', cand, reason)
    return None


def component_status() -> dict:
    "组件体检结果，给 UI 直接显示（不启动进程）。"
    bundled = _bundled_report()
    found = find_cloudflared()
    source = ''
    if found is not None:
        for cand, ok, _ in bundled:
            if ok and cand == found:
                source = 'bundled'
                break
        if not source:
            if _path_candidate() == found:
                source = 'path'
            elif str(found) == str(app_dir() / 'cloudflared.exe'):
                source = 'downloaded'
            else:
                source = 'system'
    return {'found': str(found) if found else '', 'source': source,
            'bundled_valid': any(ok for _, ok, _ in bundled),
            'bad': [{'path': str(p), 'reason': r} for p, ok, r in bundled if not ok],
            'platform_supported': platform_supported()}


def _arch_asset() -> str:
    if (os.environ.get('PROCESSOR_ARCHITECTURE') or '').upper() == 'ARM64':
        return _ARM64_ASSET
    return _AMD64_ASSET


def _download_urls() -> tuple[str, ...]:
    asset = _arch_asset()
    return tuple(prefix + 'https://github.com/cloudflare/cloudflared/releases/latest/download/' +
                 asset
                 for prefix in _MIRROR_PREFIXES)


def _probe_exe(exe: Path) -> tuple[bool, str]:
    "真的跑一次 `cloudflared --version`，确认它在本机能启动。"
    try:
        proc = subprocess.run([str(exe), '--version'], capture_output=True, text=True,
                              encoding='utf-8', errors='replace', timeout=20,
                              creationflags=_CREATE_NO_WINDOW)
    except (OSError, subprocess.SubprocessError) as exc:
        return (False, str(exc))
    out = ((proc.stdout or '') + (proc.stderr or '')).strip()
    if proc.returncode != 0:
        return (False, out[:200] or f'退出码 {proc.returncode}')
    if 'cloudflared' not in out.lower():
        return (False, f'不是 cloudflared：{out[:120]}')
    return (True, out.splitlines()[0][:200])


def download_cloudflared(progress=None) -> Path:
    """下载 cloudflared 到程序数据目录。失败抛 RuntimeError。

    progress: 可选回调 (已下载字节, 总字节) → None
    下载过程先写 .part，校验 PE 头 + `--version` 都通过后才原子替换正式文件；
    任何一步失败都保留/清理临时文件并抛中文错误，绝不把坏文件留下。
    """
    if not platform_supported():
        raise RuntimeError('当前系统不支持自动下载穿透组件，请手动安装 cloudflared')
    dest = app_dir() / 'cloudflared.exe'
    part = dest.with_name(dest.name + '.part')
    last_error = ''
    for url in _download_urls():
        try:
            logger.info('开始下载穿透组件：%s', url)
            req = urllib.request.Request(url, headers={'User-Agent': 'classpoints/2.2'})
            with urllib.request.urlopen(req, timeout=60) as resp:
                total = int(resp.headers.get('Content-Length') or 0)
                done = 0
                with open(part, 'wb') as fh:
                    while True:
                        chunk = resp.read(262144)
                        if not chunk:
                            break
                        fh.write(chunk)
                        done += len(chunk)
                        if callable(progress):
                            # 进度回调可能抛错，不能因此中断下载
                            try:
                                progress(done, total)
                            except Exception:
                                continue
            ok, reason = inspect_pe(part)
            if not ok:
                raise RuntimeError(f'下载到的文件不可用：{reason}')
            ok, detail = _probe_exe(part)
            if not ok:
                raise RuntimeError(f'下载到的组件启动失败：{detail}')
            os.replace(part, dest)
            logger.info('cloudflared 已下载到 %s（%s）', dest, detail)
            return dest
        except Exception as exc:
            last_error = str(exc) or exc.__class__.__name__
            logger.warning('从 %s 下载穿透组件失败：%s', url, last_error)
            try:
                part.unlink()
            except OSError:
                pass
    raise RuntimeError('下载穿透组件失败：' + (last_error or '网络不可用') +
                       '。请检查网络后重试，或手动把 cloudflared.exe 放到 ' + str(app_dir()))


def _humanize_failure(lines, returncode: int | None = None) -> str:
    "把 cloudflared 的英文日志归纳成一句老师看得懂的中文。"
    text = '\n'.join(lines or [])
    low = text.lower()
    if not text.strip():
        if returncode not in (None, 0):
            return f'穿透组件刚启动就退出了（退出码 {returncode}），请重新下载后再试'
        return '无法连接到穿透服务，请检查网络'
    if 'quick tunnel' in low and 'failed' in low or 'failed to request' in low:
        return '无法连接到穿透服务，请检查网络（校园网/公司网可能拦截了隧道请求）'
    if any(k in low for k in ('i/o timeout', 'context deadline', 'timeout', 'no such host', 'network is unreachable', 'connection refused', 'tls handshake')):
        return '无法连接到穿透服务，请检查网络（连接超时或被防火墙拦截）'
    if any(k in low for k in ('429', 'too many requests', 'rate limit')):
        return '穿透服务限流了（同一网络申请太频繁），请等一两分钟再试'
    if '401' in low or 'unauthorized' in low:
        return '穿透服务拒绝了请求（401），请稍后再试'
    if 'usurped' in low or 'already connected' in low:
        return '这个网络里已有同名隧道在运行，请关掉其它设备的穿透后重试'
    if any(k in low for k in ('no ingress', 'ingress', 'no configuration', 'no rules', 'missing config')):
        return '隧道起来了但没有入口规则，无法转发到本机。请重新点一次「一键部署固定域名」，或检查隧道配置里是否指向了本机端口'
    if 'failed to dial' in low or 'dial tcp' in low or 'connection' in low:
        return '无法连接到穿透服务，请检查网络'
    for line in reversed(lines or []):
        s = line.strip()
        if s:
            return '无法连接到穿透服务：' + s[:160]
    return '无法连接到穿透服务，请检查网络'


def _startup_error(exc: OSError | None) -> str:
    if exc is None:
        return ''
    winerror = getattr(exc, 'winerror', None)
    if winerror == 193:
        return '穿透组件已损坏（不是本机可用的 Windows 程序），请点「下载穿透组件」重新下载。'
    if winerror == 5:
        return '没有权限启动穿透组件，请把程序放到有权限的目录后重试'
    return f'启动穿透组件失败：{exc}'


def _no_component_message() -> str:
    return '没找到可用的 cloudflared（穿透组件）。请点「下载穿透组件」自动下载，或重新安装程序；如果之前下载过，可能是文件损坏，重新下载即可。'


def _kill_tree(pid: int, timeout: float = 6.0) -> None:
    "Windows 上 taskkill /T 才会连子进程一起收掉（cloudflared 会带子进程）。"
    if os.name == 'nt':
        try:
            subprocess.run(['taskkill', '/F', '/T', '/PID', str(int(pid))],
                           capture_output=True, timeout=timeout,
                           creationflags=_CREATE_NO_WINDOW)
        except (OSError, subprocess.SubprocessError):
            return
        return
    try:
        os.kill(int(pid), 15)
    except OSError:
        return


_MANAGERS: 'weakref.WeakSet[TunnelManager]' = weakref.WeakSet()


def _stop_all_tunnels() -> None:
    "进程退出（包括崩溃之外的正常退出）时兜底关掉 cloudflared。"
    for tm in list(_MANAGERS):
        try:
            tm.stop()
        except Exception:
            pass


atexit.register(_stop_all_tunnels)


class TunnelManager:
    "管理一个 cloudflared 快速隧道进程（线程安全、可重复调用）。"

    def __init__(self) -> None:
        self._proc = None
        self._url = ''
        self._error = ''
        self._detail = ''
        self._ready = threading.Event()
        self._pump_thread = None
        self._lock = threading.RLock()
        self._log_tail = deque(maxlen=60)
        self._named_host = ''
        _MANAGERS.add(self)

    @property
    def running(self) -> bool:
        proc = self._proc
        return proc is not None and proc.poll() is None

    @property
    def public_url(self) -> str:
        return self._url

    @property
    def error(self) -> str:
        "最近一次失败的中文原因（成功时为空）。"
        return self._error

    @property
    def last_error_detail(self) -> str:
        "原始错误日志尾巴，给「复制诊断信息」用。"
        return self._detail

    @property
    def log_tail(self) -> list[str]:
        with self._lock:
            return list(self._log_tail)

    def start(self, local_port: int, wait_seconds: float = 60.0, token: str = '', hostname: str = '') -> str:
        """起隧道并等到拿到公网地址；返回地址。失败抛 RuntimeError。

        契约（UI 按这个接）：
        - 已经在跑：立刻返回同一个 URL，不会起第二个进程；
        - 正在启动：并发调用一起等**同一个**结果，不重复拉隧道；
        - 成功返回 ``https://xxx.trycloudflare.com``（不带结尾斜杠）；
        - 失败抛 RuntimeError，``str(exc)`` 是可以直接显示给老师的中文；
        - 超时/断网时 ``error``/``last_error_detail`` 里有更细的原因。

        传 ``token`` 时走**固定隧道**（在 cloudflare 零信任控制台建好隧道、
        配好公开主机名，把令牌贴进来），此时公网地址恒定为 ``hostname``，
        与临时隧道那种每次随机的地址不同。固定隧道必须占用控制台里配的
        那个本地端口（默认 8765），否则 cloudflared 连上了也转发不到程序。
        """
        try:
            port = int(local_port)
        except (TypeError, ValueError):
            port = 0
        if port <= 0:
            raise RuntimeError('同步服务还没启动，无法生成公网地址')
        if not platform_supported():
            raise RuntimeError('当前系统不支持公网穿透，请让手机连同一 WiFi 使用')
        token = (token or '').strip()
        hostname = (hostname or '').strip().lower()
        if token and not hostname:
            raise RuntimeError('固定隧道需要填「固定域名」，例如 jf.example.com（在 cloudflare 控制台里配的那个）')
        with self._lock:
            proc = self._proc
            if proc is not None and proc.poll() is None:
                if self._url:
                    return self._url
                event = self._ready
                owner = False
            else:
                self._proc = None
                self._url = ''
                self._error = ''
                self._detail = ''
                self._log_tail.clear()
                event = threading.Event()
                self._ready = event
                self._spawn_locked(port, token=token, hostname=hostname)
                owner = True
        wait = max(1.0, float(wait_seconds))
        got = event.wait(wait)
        with self._lock:
            url = self._url
            error = self._error
            detail = self._detail
        if url:
            return url
        if got:
            msg = error or '穿透组件退出了，没拿到公网地址'
        else:
            msg = f'等了 {int(wait)} 秒还没拿到公网地址。' + (error or '无法连接到穿透服务，请检查网络')
        if owner:
            self.stop()
            with self._lock:
                self._error = msg
                if detail and not self._detail:
                    self._detail = detail
        logger.warning('公网隧道启动失败：%s', msg)
        raise RuntimeError(msg)

    def _spawn_locked(self, port: int, token: str = '', hostname: str = '') -> None:
        candidates = self._candidates()
        if not candidates:
            msg = _no_component_message()
            self._error = msg
            raise RuntimeError(msg)
        self._named_host = hostname if token else ''
        last_error = None
        for exe in candidates:
            creation = _CREATE_NO_WINDOW
            if token:
                cmd = [str(exe), 'tunnel', '--no-autoupdate',
                       '--protocol', 'http2', '--edge-ip-version', '4',
                       'run', '--token', token,
                       '--url', f'http://127.0.0.1:{port}']
            else:
                cmd = [str(exe), 'tunnel', '--no-autoupdate', '--protocol', 'http2', '--edge-ip-version', '4', '--url', f'http://127.0.0.1:{port}']
            try:
                proc = subprocess.Popen(
                    cmd,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding='utf-8',
                    errors='replace',
                    bufsize=1,
                    creationflags=creation,
                )
            except OSError as exc:
                last_error = exc
                logger.warning('用 %s 启动 cloudflared 失败：%s', exe, exc)
                continue
            self._proc = proc
            event = self._ready
            self._pump_thread = threading.Thread(
                target=self._pump,
                args=(proc, event),
                name='cloudflared-log',
                daemon=True,
            )
            self._pump_thread.start()
            logger.info('cloudflared 已启动：%s', exe)
            break
        else:
            msg = _startup_error(last_error) or _no_component_message()
            self._error = msg
            raise RuntimeError(msg)

    @staticmethod
    def _candidates() -> list[Path]:
        "先问 find_cloudflared（测试/外部可替换），再补所有可用副本。"
        out = []
        first = find_cloudflared()
        if first is not None:
            out.append(first)
        for cand in find_all_cloudflared():
            if cand not in out:
                out.append(cand)
        return out

    def _pump(self, proc: subprocess.Popen, ready: threading.Event) -> None:
        tail = deque(maxlen=40)
        try:
            if proc.stdout is not None:
                for raw in proc.stdout:
                    line = (raw or '').rstrip()
                    if not line:
                        continue
                    tail.append(line)
                    with self._lock:
                        self._log_tail.append(line[:400])
                        if not self._url:
                            found = extract_public_url(line)
                            if found:
                                self._url = found
                                self._error = ''
                                ready.set()
                            elif self._named_host and ('registered tunnel connection' in line.lower() or 'connection registered' in line.lower()):
                                self._url = 'https://' + self._named_host
                                self._error = ''
                                ready.set()
                        low = line.lower()
                        if any(k in low for k in ('err', 'error', 'failed', 'timeout', 'unable', 'refused')):
                            self._detail = line.strip()[:500]
        except (OSError, ValueError):
            pass
        finally:
            try:
                returncode = proc.wait()
            except OSError:
                returncode = -1
            with self._lock:
                if self._proc is proc:
                    if self._url:
                        self._url = ''
                        self._error = '公网隧道已断开（cloudflared 退出），请重新生成地址'
                    elif not self._error:
                        self._error = _humanize_failure(tail, returncode)
                    if tail and not self._detail:
                        self._detail = tail[-1][:500]
                    ready.set()

    def stop(self) -> None:
        "关掉隧道进程树；没启动过/重复调用都安全。"
        with self._lock:
            proc = self._proc
            self._proc = None
            self._url = ''
            self._ready.set()
            thread = self._pump_thread
            self._pump_thread = None
        if proc is None:
            return
        try:
            if proc.poll() is None:
                _kill_tree(proc.pid)
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    try:
                        proc.kill()
                    except OSError:
                        pass
                    try:
                        proc.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        logger.warning('cloudflared 进程没能及时退出：pid=%s', proc.pid)
        except OSError:
            pass
        finally:
            if proc.stdout is not None:
                try:
                    proc.stdout.close()
                except OSError:
                    pass
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=3)
        logger.info('公网隧道已关闭')
