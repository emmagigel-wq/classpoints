"""抽奖视频仓库 + 「奖项 → 动画/视频」绑定表。

为什么要单独一个仓库，而不是塞进主数据：
  * 视频是几 MB 的大文件，`storage.read_state/write_state` 走的是主存档，
    上一代把大媒体塞进主数据的后果是整份存档写不进去（见 storage.py 头注释）；
  * 一次抽奖可能要准备多段视频（不同奖项各一段），所以是「仓库 + 多对一绑定」；
  * 视频按应用数据目录的 `videos/` 子目录存放，文件本体不随备份导出，
    换设备后重新导入即可（与 storage.media_dir 的思路一致）。

绑定表 `videos.json` 的形状（放在同一应用数据目录下，不进主数据）：

    {
      "pz_xxx": {"animation": "video", "video_id": "vid_yyy"},
      "pz_zzz": {"animation": "roll"}
    }

`animation` 为空串 = 跟随全局设置（`Settings.lottery_animation`）；
`video_id` 为空串 = 该奖项没有指定视频，揭晓时从仓库里**随机挑一段**。
奖项 id 来自 `Prize.id`，所以不需要改 models.py 的 Prize 结构。

本模块是纯 Python（没有 Qt 依赖），界面层才能 import——见 tests/test_layering.py。
"""
from __future__ import annotations

import json
import os
import random
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from app.core.models import ANIMATIONS, as_str, new_id, normalize_animation
from app.core.storage import app_data_dir

VIDEO_SUBDIR = 'videos'
BINDINGS_NAME = 'videos.json'
VIDEO_EXTS = ('.mp4', '.webm', '.mov', '.avi', '.mkv', '.m4v', '.ogv')

Rng = Callable[[], float]

_ILLEGAL = '<>:"/\\|?*'


class VideoError(Exception):
    """导入/删除视频失败。message 是给用户看的文案。"""


@dataclass(frozen=True)
class VideoInfo:
    """仓库里的一段视频。path 是仓库内的绝对路径。"""

    id: str
    name: str
    path: Path
    size_bytes: int


@dataclass
class PrizeBinding:
    """某个奖项的揭晓方式。animation 空串 = 跟随全局。"""

    animation: str = ''
    video_id: str = ''

    @property
    def inherit_global(self) -> bool:
        return not self.animation


def videos_dir() -> Path:
    """视频仓库目录：<应用数据目录>/videos/。"""
    return app_data_dir() / VIDEO_SUBDIR


def bindings_file() -> Path:
    """绑定表文件：<应用数据目录>/videos.json。"""
    return app_data_dir() / BINDINGS_NAME


def ensure_dirs() -> None:
    videos_dir().mkdir(parents=True, exist_ok=True)


def _safe_name(raw: str) -> str:
    """只留文件名本身（去掉目录部分），并清洗 Windows 非法字符。"""
    name = Path(as_str(raw)).name.strip()
    for ch in _ILLEGAL:
        name = name.replace(ch, '_')
    name = name.strip(' .')
    return name or 'video'


def _unique_name(name: str, existing: set[str]) -> str:
    """重名加序号：a.mp4 → a (2).mp4 → a (3).mp4。"""
    if name not in existing:
        return name
    stem = Path(name).stem
    suffix = Path(name).suffix
    n = 2
    while f'{stem} ({n}){suffix}' in existing:
        n += 1
    return f'{stem} ({n}){suffix}'


def _parse(path: Path) -> VideoInfo | None:
    """从 `<id>__<name>` 文件名还原 VideoInfo；不合规的文件直接忽略。"""
    name = path.name
    if '__' not in name:
        return None
    vid, display = name.split('__', 1)
    if not vid.startswith('vid_') or not display:
        return None
    if path.suffix.lower() not in VIDEO_EXTS:
        return None
    try:
        size = path.stat().st_size
    except OSError:
        return None
    return VideoInfo(id=vid, name=display, path=path, size_bytes=size)


def list_videos() -> list[VideoInfo]:
    """列出仓库里的全部视频（按文件名排序，结果稳定）。不存在目录时返回 []。"""
    directory = videos_dir()
    if not directory.is_dir():
        return []
    out = []
    for path in sorted(directory.glob('*'), key=lambda p: p.name):
        if not path.is_file():
            continue
        info = _parse(path)
        if info is None:
            continue
        out.append(info)
    return out


def get_video(video_id: str) -> VideoInfo | None:
    if not video_id:
        return None
    for info in list_videos():
        if info.id == video_id:
            return info
    return None


def add_video(src_path: str | Path) -> VideoInfo:
    """把外部视频复制进仓库，返回 VideoInfo。重名自动加序号。

    失败抛 VideoError（带用户可读文案），不静默。
    """
    src = Path(src_path)
    if not src.is_file():
        raise VideoError(f'找不到视频文件：{src}')
    ext = src.suffix.lower()
    if ext not in VIDEO_EXTS:
        raise VideoError(
            '不支持的视频格式：{}（支持 {}）'.format(
                ext or '无扩展名',
                '、'.join(x.lstrip('.') for x in VIDEO_EXTS),
            )
        )
    try:
        size = src.stat().st_size
    except OSError as exc:
        raise VideoError(f'无法读取视频文件：{exc}') from exc

    if size <= 0:
        raise VideoError('视频文件是空的')

    try:
        ensure_dirs()
    except OSError as exc:
        raise VideoError(f'无法创建视频目录：{exc}') from exc

    existing = {info.name for info in list_videos()}
    display = _unique_name(_safe_name(src.name), existing)
    vid = new_id('vid')
    dest = videos_dir() / f'{vid}__{display}'
    tmp = dest.with_name(dest.name + f'.tmp{os.getpid()}')
    try:
        shutil.copyfile(src, tmp)
        os.replace(tmp, dest)
    except OSError as exc:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
        raise VideoError(f'视频复制失败：{exc}') from exc
    return VideoInfo(id=vid, name=display, path=dest, size_bytes=size)


def remove_video(video_id: str) -> bool:
    """删除仓库里的视频，并清掉所有指向它的绑定。返回是否删到了文件。"""
    info = get_video(video_id)
    if info is None:
        return False
    removed = False
    try:
        info.path.unlink()
        removed = True
    except OSError:
        removed = False

    bindings = _load_bindings()
    changed = False
    for binding in bindings.values():
        if binding.video_id == video_id:
            binding.video_id = ''
            changed = True
    if changed:
        _save_bindings(bindings)
    return removed


def _load_bindings() -> dict[str, PrizeBinding]:
    path = bindings_file()
    try:
        raw = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    out = {}
    for prize_id, value in raw.items():
        if not isinstance(prize_id, str) or not isinstance(value, dict):
            continue
        animation = as_str(value.get('animation'))
        if animation and animation not in ANIMATIONS:
            animation = 'none'
        video_id = as_str(value.get('video_id'))
        if video_id and not video_id.startswith('vid_'):
            video_id = ''
        if not animation and not video_id:
            continue
        out[prize_id] = PrizeBinding(animation=animation, video_id=video_id)
    return out


def _save_bindings(bindings: dict[str, PrizeBinding]) -> None:
    payload = {}
    for prize_id, binding in bindings.items():
        if not prize_id:
            continue
        entry = {}
        if binding.animation:
            entry['animation'] = binding.animation
        if binding.video_id:
            entry['video_id'] = binding.video_id
        if not entry:
            continue
        payload[prize_id] = entry
    path = bindings_file()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + f'.tmp{os.getpid()}')
        with open(tmp, 'w', encoding='utf-8', newline='\n') as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except OSError:
        # 绑定表坏掉不该拦住抽奖
        return None


def list_bindings() -> dict[str, PrizeBinding]:
    """读全部绑定（副本，调用方随便改）。"""
    return _load_bindings()


def get_prize_binding(prize_id: str) -> PrizeBinding:
    return _load_bindings().get(prize_id, PrizeBinding())


def set_prize_animation(prize_id: str, animation: str) -> PrizeBinding:
    """设置某奖项的揭晓动画。animation="" 表示跟随全局设置。"""
    if not prize_id:
        return PrizeBinding()
    animation = as_str(animation)
    if animation and animation not in ANIMATIONS:
        animation = 'none'
    bindings = _load_bindings()
    binding = bindings.get(prize_id, PrizeBinding())
    binding.animation = animation
    if animation and animation != 'video':
        binding.video_id = ''
    bindings[prize_id] = binding
    _save_bindings(bindings)
    return binding


def set_prize_video(prize_id: str, video_id: str) -> PrizeBinding:
    """指定某奖项用的视频。video_id="" 表示揭晓时随机挑一段。"""
    if not prize_id:
        return PrizeBinding()
    video_id = as_str(video_id)
    if video_id and get_video(video_id) is None:
        video_id = ''
    bindings = _load_bindings()
    binding = bindings.get(prize_id, PrizeBinding())
    binding.video_id = video_id
    if video_id:
        # 指定了视频就自动切到视频揭晓
        binding.animation = 'video'
    bindings[prize_id] = binding
    _save_bindings(bindings)
    return binding


def clear_prize_binding(prize_id: str) -> None:
    if not prize_id:
        return None
    bindings = _load_bindings()
    if bindings.pop(prize_id, None) is not None:
        _save_bindings(bindings)


def pick_for_prize(prize_id: str, rand: Rng | None = None) -> VideoInfo | None:
    """给奖项挑一段视频：有绑定且文件在就用它，否则从仓库随机挑；没有视频返回 None。"""
    videos = list_videos()
    if not videos:
        return None
    bound = get_prize_binding(prize_id).video_id
    if bound:
        info = get_video(bound)
        if info is not None:
            return info
    r = rand if rand is not None else random.random
    index = int(r() * len(videos))
    if index >= len(videos):
        index = len(videos) - 1
    if index < 0:
        index = 0
    return videos[index]


def resolve_for_prize(
    prize_id: str,
    global_animation: str = 'none',
    rand: Rng | None = None,
) -> tuple[str, VideoInfo | None]:
    """抽中某奖项后该怎么揭晓：返回 (动画名, 要播的视频 or None)。

    优先级：奖项自己的 animation > 全局设置；video 时优先绑定视频，
    没有绑定就随机挑一段（用户要求的「未指定时随机挑一个视频」）。
    """
    binding = get_prize_binding(prize_id)
    animation = normalize_animation(
        binding.animation or normalize_animation(global_animation)
    )
    if animation != 'video':
        return animation, None
    video = None
    if binding.video_id:
        video = get_video(binding.video_id)
    if video is None:
        video = pick_for_prize(prize_id, rand)
    return 'video', video


def reveal_subtitle(video: VideoInfo | None) -> str:
    """视频揭晓时结果区下面那行说明。"""
    if video is None:
        return '视频揭晓（还没有导入视频，已直接揭晓结果）'
    return f'视频揭晓：{video.name}'
