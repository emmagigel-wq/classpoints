"""内置自测的每一条检查。

**给谁用**：最终用户（老师）。安装完打不开、分数没存上、U 盘没空间——
这些现场问题没法让老师去看日志，所以程序要能自己说清楚「哪儿坏了」。

三条硬规矩：

1. **纯本地**：不联网、不下模型、不读注册表。
2. **零额外依赖**：只用标准库与程序本来就带的库（PyQt5 只是那两条检查需要，
   取不到就诚实标成「跳过」，不许把整体判成失败）。
3. **绝不动用户的真实数据**：凡是需要读写存档的检查，一律把
   ``CLASSPOINTS_DATA_DIR`` 指到临时目录，跑完删掉。写真实数据目录的
   只有第 1 条，而且只写一个自检探针文件、随即删除。

每条检查是一个无参函数，返回 CheckResult；抛异常由 runner 统一转成失败，
所以这里不必到处 try/except。
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator

from app.core import layout as layout_mod
from app.core import scoring, storage
from app.core.models import (
    AppState,
    Layout,
    LotteryRecord,
    Podium,
    Prize,
    Student,
)

PASS = 'pass'
FAIL = 'fail'
WARN = 'warn'
SKIP = 'skip'

STATUS_LABELS = {PASS: '通过', FAIL: '失败', WARN: '警告', SKIP: '跳过'}

# 磁盘可用空间低于 200 MiB 就警告：分数文件很小，200 MiB 足够。
FREE_SPACE_WARN_BYTES = 200 * 1024 * 1024

DATA_DIR_ENV = 'CLASSPOINTS_DATA_DIR'


@dataclass
class CheckResult:
    """单条检查的结论。detail 给人看，advice 是「怎么办」。"""

    key: str
    name: str
    status: str
    detail: str = ''
    advice: str = ''
    duration_ms: float = 0.0

    @property
    def passed(self) -> bool:
        return self.status == PASS

    @property
    def skipped(self) -> bool:
        return self.status == SKIP

    @property
    def warned(self) -> bool:
        return self.status == WARN

    @property
    def status_label(self) -> str:
        return STATUS_LABELS.get(self.status, self.status)

    def line(self) -> str:
        text = f'[{self.status_label}] {self.name}'
        if self.detail:
            text += f' —— {self.detail}'
        return text

    def to_dict(self) -> dict:
        return {
            'key': self.key,
            'name': self.name,
            'status': self.status,
            'statusLabel': self.status_label,
            'passed': self.passed,
            'detail': self.detail,
            'advice': self.advice,
            'durationMs': round(self.duration_ms, 1),
        }


@dataclass
class CheckDef:
    key: str
    name: str
    func: Callable[[], CheckResult]
    why: str = ''


def _ok(key: str, name: str, detail: str = '') -> CheckResult:
    return CheckResult(key, name, PASS, detail)


def _bad(key: str, name: str, detail: str, advice: str = '') -> CheckResult:
    return CheckResult(key, name, FAIL, detail, advice)


def _warn(key: str, name: str, detail: str, advice: str = '') -> CheckResult:
    return CheckResult(key, name, WARN, detail, advice)


def _skip(key: str, name: str, detail: str) -> CheckResult:
    return CheckResult(key, name, SKIP, detail)


@contextlib.contextmanager
def temp_data_dir() -> Iterator[Path]:
    """把数据目录临时指到系统临时目录，跑完恢复环境变量并删干净。

    存档类检查必须走这里——自检绝不能碰老师的真实分数。
    """
    old = os.environ.get(DATA_DIR_ENV)
    tmp = Path(tempfile.mkdtemp(prefix='classpoints-selftest-'))
    os.environ[DATA_DIR_ENV] = str(tmp)
    try:
        yield tmp
    finally:
        if old is None:
            os.environ.pop(DATA_DIR_ENV, None)
        else:
            os.environ[DATA_DIR_ENV] = old
        shutil.rmtree(tmp, ignore_errors=True)


def sample_state() -> AppState:
    """一份覆盖各类字段的样例存档：中文姓名、加减分、抽奖、设置、布局。"""
    state = AppState(class_name='自检样例班')
    state.layout = Layout(
        rows=3,
        cols=5,
        col_groups=[2, 3],
        podium=Podium(enabled=True, position='bottom', span=3, align='right'),
    )
    state.settings.step_morning = 2
    state.settings.step_noon = 3
    state.settings.prize_threshold = 4
    state.settings.prize_cost = 4
    state.settings.lottery_animation = 'roll'
    state.settings.random_count = 2
    state.settings.plus_reasons = ['早读表现好', '课堂积极回答']
    state.settings.minus_reasons = ['迟到']
    state.prizes = [Prize(name='免作业券', weight=1.5), Prize(name='小零食', weight=2)]
    state.students = [
        Student(name='张三', row=0, col=0),
        Student(name='李四', row=0, col=1),
        Student(name='王五', row=1, col=0),
    ]
    scoring.add_points(state, state.students[0].id, 5, reason='早读表现好', source='morning')
    scoring.add_points(state, state.students[1].id, -2, reason='迟到', source='manual')
    scoring.add_points(state, state.students[2].id, 3, reason='课堂积极回答', source='noon')
    state.lottery.append(
        LotteryRecord(
            prize_name='小零食',
            winners=[state.students[0].id],
            winner_names=['张三'],
            cost=4,
        )
    )
    return state


def _diff_fields(expected, actual, path: str = '', out: list[str] | None = None) -> list[str]:
    """逐字段比对两份 dict / list，返回「路径: 期望≠实际」的可读差异。"""
    if out is None:
        out = []
    if type(expected) is not type(actual) and not isinstance(expected, type(actual)) and not isinstance(actual, type(expected)):
        out.append(
            f'{path or '<根>'}: 类型 {type(expected).__name__} ≠ {type(actual).__name__}'
        )
        return out
    if isinstance(expected, dict):
        for key in sorted(set(expected) | set(actual)):
            sub = f'{path}.{key}' if path else key
            if key not in expected:
                out.append(f'{sub}: 多出字段 {actual[key]!r}')
            elif key not in actual:
                out.append(f'{sub}: 缺少字段（期望 {expected[key]!r}）')
            else:
                _diff_fields(expected[key], actual[key], sub, out)
        return out
    if isinstance(expected, list):
        if len(expected) != len(actual):
            out.append(f'{path}: 条数 {len(expected)} ≠ {len(actual)}')
        for i, (e, a) in enumerate(zip(expected, actual)):
            _diff_fields(e, a, f'{path}[{i}]', out)
        return out
    if expected != actual:
        out.append(f'{path}: 期望 {expected!r}，实际 {actual!r}')
    return out


def _existing_ancestor(path: Path) -> Path:
    cur = path
    while not cur.exists() and cur != cur.parent:
        cur = cur.parent
    return cur


def _ensure_qt_app():
    """取（或建）一个 QCoreApplication；没有 PyQt5 时返回 None。

    自测可能被命令行调用（那时还没有 QApplication），也可能被界面的
    「帮助→自检」调用（那时已有 QApplication）。两种情况都要能用：
    QCoreApplication.instance() 在已有 QApplication 时会返回它。

    **建出来的实例必须保住强引用**：QCoreApplication 被回收后再启动
    QTimer 会刷一屏 `QObject::startTimer: Timers can only be used with
    threads started with QThread`——虽然不影响判定，但用户在自检输出里
    看到这么一段会以为程序坏了。所以存在模块级 `_QT_APP` 里不放。
    """
    global _QT_APP
    try:
        from PyQt5.QtCore import QCoreApplication
    except Exception:
        return None
    app = QCoreApplication.instance()
    if app is None:
        app = QCoreApplication([])
        _QT_APP = app
    return app


_QT_APP = None


def check_data_dir_writable() -> CheckResult:
    """数据目录能写能读能删——写不进去的话，分数全在内存里，一关就没。"""
    key, name = 'data-dir', '数据目录可写'
    base = storage.app_data_dir()
    try:
        base.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return _bad(key, name, f'无法创建数据目录 {base}：{exc}',
                    '检查该目录权限，或设置环境变量 CLASSPOINTS_DATA_DIR 指向可写目录。')
    probe = base / f'.selftest-probe-{os.getpid()}-{int(time.time() * 1000)}.tmp'
    try:
        probe.write_text('classpoints-selftest', encoding='utf-8')
        back = probe.read_text(encoding='utf-8')
        if back != 'classpoints-selftest':
            return _bad(key, name, '写入后读回的内容不一致',
                        '磁盘或安全软件可能拦截了写入，换一个数据目录试试。')
    except OSError as exc:
        return _bad(key, name, f'写入探针文件失败：{exc}', '检查数据目录权限与磁盘剩余空间。')
    finally:
        with contextlib.suppress(OSError):
            probe.unlink()
    if probe.exists():
        return _warn(key, name, f'探针文件未能删除：{probe}', '目录可能被只读策略或安全软件保护。')
    return _ok(key, name, f'数据目录 {base}')


def check_state_roundtrip() -> CheckResult:
    """写入 → 读回 → 逐字段比对。

    上一代是「界面上加了分、磁盘上没存」，而且不报错。这条检查就是钉死它：
    只要有一个字段对不上，就明确说是哪个字段。
    """
    key, name = 'state-roundtrip', '存档读回一致性'
    with temp_data_dir() as tmp:
        state = sample_state()
        written = storage.write_state(state)
        if not written.ok:
            return _bad(key, name, f'写入失败（{written.reason}）：{written.message}',
                        '见上一条「数据目录可写」的结果。')
        loaded = storage.read_state()
        if not (loaded.ok and loaded.state is not None):
            return _bad(key, name, f'读回失败（{loaded.reason}）：{loaded.message}')
        expected = state.to_dict()
        actual = loaded.state.to_dict()
        expected.pop('savedAt', None)
        actual.pop('savedAt', None)
        diffs = _diff_fields(expected, actual)
        if diffs:
            shown = '；'.join(diffs[:5])
            more = f'（共 {len(diffs)} 处）' if len(diffs) > 5 else ''
            return _bad(key, name, f'字段对不上：{shown}{more}',
                        '这是数据损坏的早期信号，请先导出备份再排查。')
        students = len(loaded.state.students)
        entries = len(loaded.state.history)
        total = sum(s.points for s in loaded.state.students)
        return _ok(key, name,
                   f'{students} 名学生 / {entries} 条记录 / 总分 {total}，逐字段一致（临时目录 {tmp.name}）')


def check_backup_readable() -> CheckResult:
    """第二次保存会先备份上一份存档；备份要存在、要能读、还要能当存档恢复。"""
    key, name = 'backup', '备份存在且可读'
    with temp_data_dir():
        first = sample_state()
        first.class_name = '备份前班级'
        r1 = storage.write_state(first)
        if not r1.ok:
            return _bad(key, name, f'首次写入失败：{r1.message}')
        second = sample_state()
        second.class_name = '备份后班级'
        r2 = storage.write_state(second)
        if not r2.ok:
            return _bad(key, name, f'第二次写入失败：{r2.message}')
        backups = storage.list_backups()
        if not backups:
            return _bad(key, name, '第二次保存后没有生成备份文件',
                        '检查数据目录下 backups/ 是否可写；备份失败不会阻断保存，所以要看这里。')
        newest = backups[0]
        try:
            raw = json.loads(newest.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError) as exc:
            return _bad(key, name, f'备份 {newest.name} 无法解析：{exc}',
                        '磁盘可能写坏，建议立刻导出一份整库备份。')
        if not isinstance(raw, dict) or 'students' not in raw:
            return _bad(key, name, f'备份 {newest.name} 结构不对（缺少 students）')
        storage.data_file().write_text(newest.read_text(encoding='utf-8'), encoding='utf-8')
        restored = storage.read_state()
        if not (restored.ok and restored.state is not None):
            return _bad(key, name, f'备份无法恢复：{restored.message}')
        if restored.state.class_name != '备份前班级':
            return _bad(key, name, f'备份内容不是上一份存档（读回 {restored.state.class_name!r}）')
        return _ok(key, name, f'共 {len(backups)} 份备份，最新 {newest.name} 可读且可恢复')


def check_scoring_undo_roundtrip() -> CheckResult:
    """加分再撤销，净额必须归零，且不留下记录。

    撤销的语义是「删掉那条记录并把分退回去」；只要这两者不同步，
    学生看到的加分次数就和座位上的分数对不上。
    """
    key, name = 'scoring-roundtrip', '记分—撤销往返净额为零'
    state = AppState(students=[Student(name='甲', row=0, col=0),
                               Student(name='乙', row=0, col=1)])
    a, b = state.students[0].id, state.students[1].id
    entries = [scoring.add_points(state, a, 5, reason='早读表现好', source='morning'),
               scoring.add_points(state, a, -1, reason='迟到', source='manual'),
               scoring.add_points(state, b, 3, reason='课堂积极回答', source='noon')]
    if any(e is None for e in entries):
        return _bad(key, name, '底层记分拒绝了一次正常写入')
    mid_points = sum(s.points for s in state.students)
    if mid_points != 7 or len(state.history) != 3:
        return _bad(key, name,
                    f'记分后总分 {mid_points}（期望 7）、记录 {len(state.history)} 条（期望 3）')
    for entry in reversed(entries):
        undone = scoring.undo_entry(state, entry.id)
        if undone is not None:
            continue
        return _bad(key, name, f'撤销记录 {entry.id} 失败（找不到该记录）')
    total = sum(s.points for s in state.students)
    if total != 0:
        return _bad(key, name, f'全部撤销后总分 {total}，未归零',
                    '记分与撤销不同步，会出现「撤销了分还在」。')
    if state.history:
        return _bad(key, name, f'全部撤销后仍残留 {len(state.history)} 条记录')
    scoring.add_points(state, a, 4, reason='作业优秀', source='manual')
    scoring.add_points(state, a, -2, reason='课堂讲话', source='manual')
    scoring.add_points(state, b, 6, reason='进步明显', source='manual')
    history_sum = sum(h.delta for h in state.history)
    live_sum = sum(s.points for s in state.students)
    if history_sum != live_sum:
        return _bad(key, name, f'积分之和 {live_sum} ≠ 历史变动之和 {history_sum}')
    scoring.recalc_points(state)
    if sum(s.points for s in state.students) != history_sum:
        return _bad(key, name, '按历史重算积分后与历史变动之和不一致')
    return _ok(key, name, f'3 笔记分全部撤销后归零；再接 3 笔，历史之和 {history_sum} 与积分之和一致')


def check_layout_math() -> CheckResult:
    """座位列 ↔ 网格线必须互逆，过道轨道数要守恒。

    错一位整个座位表就歪；而且这是纯几何，最容易在小改动里被悄悄改坏。
    """
    key, name = 'layout-math', '座位布局换算自洽'
    problems = []
    cases = [[2, 4, 2], [8], [3, 3, 3], [1], [5, 5, 5, 5, 5, 5]]
    for groups in cases:
        cols = sum(groups)
        lines = [layout_mod.seat_line(groups, c) for c in range(cols)]
        if layout_mod.seat_line(groups, 0) != 1:
            problems.append(f'{groups}: 首列不是网格线 1（{layout_mod.seat_line(groups, 0)}）')
        if any(x >= y for x, y in zip(lines, lines[1:])):
            problems.append(f'{groups}: 网格线不是严格递增 {lines}')
        total = layout_mod.total_grid_columns(groups)
        if lines[-1] != total:
            problems.append(f'{groups}: 末列线号 {lines[-1]} ≠ 网格总列数 {total}')
        spans = [layout_mod.seat_grid_span(groups, c) for c in range(cols)]
        if sum(spans) != total:
            problems.append(f'{groups}: 占格合计 {sum(spans)} ≠ 网格总列数 {total}')
        aisles = sum(1 for s in spans if s == 2)
        if aisles != max(0, len(groups) - 1):
            problems.append(f'{groups}: 过道轨道 {aisles} 条 ≠ 分组间空隙 {max(0, len(groups) - 1)} 条')
        ends = layout_mod.group_end_map(groups)
        if any(not 0 <= c < cols for c in ends):
            problems.append(f'{groups}: 块尾列越界 {sorted(ends)}')
        resolved = layout_mod.resolve_layout(Layout(col_groups=groups))
        if resolved.cols != cols or resolved.col_groups != groups:
            problems.append(f'{groups}: resolve_layout 后 {resolved.col_groups}/{resolved.cols} 不一致')
        rng = layout_mod.podium_range(resolved)
        if rng is not None and not (0 <= rng.start < rng.end <= cols):
            problems.append(f'{groups}: 讲台范围 [{rng.start},{rng.end}) 越界（共 {cols} 列）')
    if layout_mod.parse_col_groups('2-4-2') != [2, 4, 2]:
        problems.append("解析 '2-4-2' 失败")
    if layout_mod.parse_col_groups('2、4、2') != [2, 4, 2]:
        problems.append('解析中文顿号失败')
    if layout_mod.parse_col_groups('abc') != []:
        problems.append('非法输入应解析为空')
    if layout_mod.normalize_groups([], 7) != [7]:
        problems.append('空分组未回退为单块')
    if sum(layout_mod.normalize_groups([40, 40], 8)) != layout_mod.MAX_COLS:
        problems.append('分组总列数未被限制在 30')
    legacy = layout_mod.resolve_layout(Layout(cols=8, col_groups=[], aisle_every=3))
    if legacy.col_groups != [3, 3, 2] or legacy.cols != 8:
        problems.append(f'旧存档 aisleEvery 换算错误：{legacy.col_groups}')
    shrink = AppState(students=[
        Student(name='甲', row=0, col=0),
        Student(name='乙', row=5, col=7),
        Student(name='丙', row=9, col=3),
    ])
    layout_mod.apply_layout(shrink, 2, [2])
    if len(shrink.students) != 3:
        problems.append('布局缩小后学生数量变了')
    if any(not layout_mod.seat_is_valid(shrink.layout, s.row, s.col) for s in shrink.students):
        problems.append('布局缩小后有学生留在界外')
    if problems:
        return _bad(key, name, '；'.join(problems[:4]),
                    '座位表坐标算错会整体错位，请核对 app/core/layout.py。')
    return _ok(key, name, f'{len(cases)} 组分组换算互逆，旧存档换算与缩容挪座均正确')


def check_timer_scope() -> CheckResult:
    """按作用域撤销要精确：撤掉该场景的，不误伤别的场景。

    这是「关掉全屏后凭空补记一次分」的结构性对策。用 pending_keys 判断，
    **不等真实时间**（安排 10 分钟的定时器，检查期间根本不会到期）。
    """
    key, name = 'timer-scope', '定时器登记表按作用域撤销'
    if _ensure_qt_app() is None:
        return _skip(key, name, '当前环境没有 PyQt5，跳过（不影响其它检查）')
    from app.services.timer_registry import TimerRegistry
    reg = TimerRegistry()
    fired: list[str] = []
    try:
        reg.schedule('k1', 600000, lambda: fired.append('k1'), scope='stage:morning')
        reg.schedule('k2', 600000, lambda: fired.append('k2'), scope='stage:morning')
        reg.schedule('k3', 600000, lambda: fired.append('k3'), scope='lottery')
        if sorted(reg.pending_keys()) != ['k1', 'k2', 'k3']:
            return _bad(key, name, f'登记后待执行动作异常：{reg.pending_keys()}')
        removed = reg.cancel_scope('stage:morning')
        if removed != 2:
            return _bad(key, name, f'cancel_scope 撤销了 {removed} 个，期望 2 个')
        if reg.pending_keys('stage:morning'):
            return _bad(key, name, f'该作用域仍有残留：{reg.pending_keys("stage:morning")}')
        if reg.pending_keys() != ['k3']:
            return _bad(key, name, f'误伤了其它作用域：{reg.pending_keys()}')
        reg.schedule('dup', 600000, lambda: fired.append('old'), scope='lottery')
        reg.schedule('dup', 600000, lambda: fired.append('new'), scope='lottery')
        if reg.pending_keys('lottery').count('dup') != 1:
            return _bad(key, name, '同 key 重复登记没有被覆盖')
        if not reg.is_pending('dup'):
            return _bad(key, name, 'is_pending 对已登记的动作返回了假')
        if fired:
            return _bad(key, name, f'检查期间不应该有任何动作触发，却触发了 {fired}')
        left = reg.cancel_scope('lottery')
        if left != 2 or reg.pending_keys() != []:
            return _bad(key, name,
                        f'清空后仍有残留：{reg.pending_keys()}（本次撤销 {left} 个）')
        stats = reg.stats()
        if stats:
            return _bad(key, name, f'全部撤销后作用域计数未归零：{stats}',
                        '计数不归零说明登记与撤销不成对，长会话里会累积。')
        return _ok(key, name, '按作用域精确撤销 2/2，同 key 覆盖生效，不误伤其它作用域，计数归零')
    finally:
        reg.shutdown()


def check_pulse_generation() -> CheckResult:
    """新动画一开始，上一代的帧必须全部作废。

    这是「连点两下就闪一下」的结构性对策：不是让两个定时器互相抹，
    而是让旧代次的帧根本没有机会发出来。
    """
    key, name = 'pulse-generation', '动画代次推进后旧帧作废'
    if _ensure_qt_app() is None:
        return _skip(key, name, '当前环境没有 PyQt5，跳过（不影响其它检查）')
    from app.services.pulse import PulseController
    from app.services.timer_registry import TimerRegistry
    from app.services.voting import VoteKind

    reg = TimerRegistry()
    scope = 'selftest:stage'
    pulse = PulseController(reg)
    pulse.set_scope(scope)
    sid = 'stu-selftest'
    try:
        pulse.flash(sid, VoteKind.PLUS, amount=1)
        gen1 = pulse.generation_of(sid)
        keys1 = reg.pending_keys(scope)
        if gen1 != 1 or not keys1:
            return _bad(key, name, f'首次动画未登记帧（代次 {gen1}，待发帧 {len(keys1)}）')
        if any(f':{gen1}:' not in k for k in keys1):
            return _bad(key, name, f'首次动画的帧 key 不带代次标记：{keys1}')
        pulse.flash(sid, VoteKind.PLUS, amount=1)
        gen2 = pulse.generation_of(sid)
        keys2 = reg.pending_keys(scope)
        if gen2 != gen1 + 1:
            return _bad(key, name, f'代次未推进：{gen1} → {gen2}')
        stale = [k for k in keys2 if f':{gen1}:' in k]
        if stale:
            return _bad(key, name, f'上一代的帧仍然挂着，会与新帧互相抹：{stale}',
                        '这就是动画闪一下就没的成因。')
        if not keys2:
            return _bad(key, name, '第二次动画没有登记任何帧')

        cleared = reg.cancel_scope(scope)
        if cleared != len(keys2) or reg.pending_keys() != []:
            return _bad(key, name, f'离开场景后仍有残留帧：{reg.pending_keys()}')

        pulse.flash(sid, VoteKind.MINUS, amount=1)
        before = pulse.generation_of(sid)
        pulse.clear_all()
        if pulse.generation_of(sid) != before + 1:
            return _bad(key, name, 'clear_all 没有推进代次，残留帧仍可能生效')
        if reg.pending_keys() != []:
            return _bad(key, name, f'clear_all 后仍有残留帧：{reg.pending_keys()}')

        return _ok(key, name, f'代次 {gen1}→{gen2} 推进后旧帧归零；离场与 clear_all 均清空残留')
    finally:
        reg.shutdown()


def check_disk_space() -> CheckResult:
    """可用空间不够时要提前警告，而不是等存档写坏了才发现。"""
    key, name = 'disk-space', '磁盘剩余空间'
    base = storage.app_data_dir()
    target = _existing_ancestor(base)
    try:
        usage = shutil.disk_usage(str(target))
    except OSError as exc:
        return _warn(key, name, f'无法读取磁盘空间信息：{exc}', '可用其它工具确认所在磁盘的剩余空间。')
    free_gb = usage.free / 1073741824
    total_gb = usage.total / 1073741824
    detail = f'{target.drive or target} 可用 {free_gb:.1f} GB / 共 {total_gb:.1f} GB（警戒线 {FREE_SPACE_WARN_BYTES // 1048576} MB）'
    if usage.free < FREE_SPACE_WARN_BYTES:
        return _warn(key, name, detail,
                     '空间不足会让保存失败；请清理磁盘或把数据目录移到空间充足的盘。')
    return _ok(key, name, detail)


def check_qr_encoder() -> CheckResult:
    """扫码配对靠 segno 生成二维码；装了没装、能不能出点阵，这里一验就知道。

    打包成 exe 之后最容易出问题的就是「源码能跑、装完机子上缺模块」，
    所以这条必须走一遍真编码，而不是只 import 一下。
    """
    key, name = 'qr-encoder', '扫码组件可用'
    try:
        from app.services import qr as qr_mod
    except Exception as exc:
        return _warn(key, name, f'二维码模块导入失败：{exc}',
                     '扫码配对将退化为「显示链接 + 复制」；其它功能不受影响。')
    if not qr_mod.available():
        return _warn(key, name, '未安装 segno，扫码配对退化为复制链接',
                     '运行 pip install segno 即可恢复二维码。')
    try:
        matrix = qr_mod.matrix('https://example.trycloudflare.com/?t=k3m9pq2x')
    except Exception as exc:
        return _bad(key, name, f'生成点阵失败：{exc}', '扫码不可用，但设置里仍会显示链接。')
    size = len(matrix)
    if not matrix or size != len(matrix[0]) or not any(any(row) for row in matrix):
        return _bad(key, name, f'点阵形状异常：{size}×{len(matrix[0]) if matrix else 0}',
                    '二维码会是一张空图，请反馈。')
    return _ok(key, name, f'segno 就绪，{size}×{size} 点阵、纠错 M、含定位图形')


def check_media_backend() -> CheckResult:
    """内置播放器要能真播，打包时最容易漏的就是 Qt 的多媒体插件。

    「源码能放、装完机子放不了」是最难解释的一种故障：用户看到的是
    「视频打不开」，我们看到的是代码没问题。所以这里在打包产物里实探一次。
    """
    key, name = 'media-backend', '内置播放器可用'
    try:
        from app.services import media_probe
    except Exception as exc:
        return _bad(key, name, f'播放器模块导入失败：{exc}', '抽奖视频揭晓会退化成直接亮结果。')
    if not media_probe.MEDIA_AVAILABLE:
        return _bad(key, name, '缺少 PyQt5.QtMultimedia（打包时没带上）',
                    '抽奖视频揭晓会退化成直接亮结果；其它功能不受影响。')
    try:
        from PyQt5.QtMultimedia import QMediaPlayer
        player = QMediaPlayer(None)
        ok = bool(player.isAvailable())
        try:
            player.deleteLater()
        except Exception:
            pass
    except Exception as exc:
        return _bad(key, name, f'多媒体后端初始化失败：{exc}', '抽奖视频揭晓会退化成直接亮结果。')
    if not ok:
        return _warn(key, name, '系统没有可用的多媒体服务，播放器会退化为系统播放',
                     'Windows 的 Media Foundation 被精简过时会出现；视频仍可用「用系统播放器打开」兜底。')
    return _ok(key, name, 'QtMultimedia 就绪，可在程序内播放视频')


def check_transcoder() -> CheckResult:
    """内置转码器能不能跑：装了却用不了，用户导视频时才炸。

    只验「找得到 + 能执行 -version」，不做真转码（那要好几十秒）。
    """
    try:
        from app.services import transcode
    except Exception as exc:
        return _bad('transcoder', '内置转码器可用', f'转码模块不可用：{exc}')
    exe = transcode.ffmpeg_path()
    if not exe:
        return _warn('transcoder', '内置转码器可用', '没有内置转码组件，导入特殊格式的视频会提示下载')
    if not Path(exe).exists():
        return _bad('transcoder', '内置转码器可用', f'转码组件路径不存在：{exe}')
    try:
        proc = transcode.subprocess.run(
            [exe, '-hide_banner', '-version'],
            capture_output=True,
            timeout=8,
            creationflags=getattr(transcode.subprocess, 'CREATE_NO_WINDOW', 0),
        )
    except Exception as exc:
        return _bad('transcoder', '内置转码器可用', f'转码组件跑不起来：{exc}')
    if proc.returncode != 0:
        return _bad('transcoder', '内置转码器可用', f'转码组件返回 {proc.returncode}')
    head = (proc.stdout or b'').decode('utf-8', 'replace').splitlines()
    first = head[0].strip() if head else ''
    size_mb = Path(exe).stat().st_size / 1048576
    return _ok('transcoder', '内置转码器可用', f'内置转码器就绪（{size_mb:.0f} MB）：{first[:60]}')


def check_single_instance() -> CheckResult:
    """双击第二次不该再开一个窗口——靠 QtNetwork 的本地套接字实现。

    打包若漏了 Qt5Network.dll，这个能力会**静默失效**（导入失败就退化成
    「没有单实例」），用户只看到开了两个窗口，看不出哪里坏了，所以放进自检。
    """
    key, name = 'single-instance', '单实例可用'
    try:
        from PyQt5.QtNetwork import QLocalServer, QLocalSocket
    except Exception as exc:
        return _bad(key, name, f'缺少 QtNetwork：{exc}',
                    '双击图标会开出第二个窗口；打包时要把 Qt5Network.dll 带上')
    try:
        import uuid
        probe = f'cp-selftest-{uuid.uuid4().hex[:8]}'
        server = QLocalServer()
        if not server.listen(probe):
            return _warn(key, name, f'本地套接字监听失败：{server.errorString()}',
                         '可能是权限或安全软件拦截')
        sock = QLocalSocket()
        sock.connectToServer(probe)
        ok = sock.waitForConnected(500)
        sock.disconnectFromServer()
        server.close()
        QLocalServer.removeServer(probe)
    except Exception as exc:
        return _bad(key, name, f'本地套接字自测失败：{exc}')
    if not ok:
        return _warn(key, name, '本地套接字连不上（不影响主功能）')
    return _ok(key, name, '再点桌面图标会把已有窗口叫回前台，不会开第二个')


CHECKS: list[CheckDef] = [
    CheckDef('data-dir', '数据目录可写', check_data_dir_writable,
             '写不进去的话分数只在内存里，一关就没了'),
    CheckDef('state-roundtrip', '存档读回一致性', check_state_roundtrip,
             '上一代「界面加了分、磁盘没存上还不报错」的直接对策'),
    CheckDef('backup', '备份存在且可读', check_backup_readable,
             '存档写坏时能不能回到上一份'),
    CheckDef('scoring-roundtrip', '记分—撤销往返净额为零', check_scoring_undo_roundtrip,
             '撤销后分数不回落，学生会看到「撤销了分还在」'),
    CheckDef('layout-math', '座位布局换算自洽', check_layout_math,
             '座位列与网格线错一位，整张座位表就歪'),
    CheckDef('timer-scope', '定时器按作用域撤销', check_timer_scope,
             '「关掉全屏后凭空补记一次分」的对策是否还在'),
    CheckDef('pulse-generation', '动画代次作废旧帧', check_pulse_generation,
             '「连点两下闪一下」的对策是否还在'),
    CheckDef('disk-space', '磁盘剩余空间', check_disk_space,
             '空间不足时保存会失败'),
    CheckDef('qr-encoder', '扫码组件可用', check_qr_encoder,
             '手机扫码配对要用的二维码编码器是否随包带上'),
    CheckDef('media-backend', '内置播放器可用', check_media_backend,
             '打包时 Qt 的多媒体插件容易被漏掉，漏了就放不了视频'),
    CheckDef('transcoder', '内置转码器可用', check_transcoder,
             '导入 H.265 等放不了的视频时，靠它转成能播的格式'),
    CheckDef('single-instance', '单实例可用', check_single_instance,
             '再点桌面图标时唤回已有窗口，不再开第二个'),
]


def iter_checks() -> list[CheckDef]:
    return list(CHECKS)
