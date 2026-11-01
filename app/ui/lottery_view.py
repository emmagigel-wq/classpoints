"""抽奖 / 随机抽人：原版「抽奖」与「随机抽人」两个弹窗内容的 1:1 复刻。

规格来自 ui_ref（ground-lottery.json 实测 438 节点 + css-rules.txt:145-199 + app.js:3140-3434）。

    .lottery        grid 1fr / 280px，gap 18
    .lottery-config 整行，flex wrap，gap 14
    .lq-anim-tag    12.5px INK2，#f2f4f7，r999，padding 5/12
    .lq-pool        13.5px INK2
    .lq-picker      白名单网格：gap8 padding10，max-h 190，边框 LINE，r12，#fbfcfe
    .lq-person      竖排 chip，min-w 76，边框 1.5 LINE，r10，13.5px；选中 BRAND/#eef2ff
    .lq-slot        最小高 120，2px 虚线，r16，34px w800 INK3；rolling #c7d2fe/#f5f8ff；
                    done 实线 #bdf0d6 + 渐变底；.win-name 40px #027a48
    .prize-list li  flex 两端对齐，7/10，r9，#f9fafb，13px
    .lq-history     max-h 200，12.5px，虚线分隔
    .rd-*          随机抽人：rd-result min-h150 虚线 r16；.rd-name 胶囊 20px w800

两个变体共用一个组件（`set_mode("draw" | "random")`），因为原版也是两个入口、
同一套 modal 壳。动画只用 TimerRegistry 排帧（scope='lottery'），离开界面时
按作用域整批撤销——抽完关掉界面还在跑帧这种事不会发生。

揭晓动画的优先级：**奖项自己的设置**（videos.json 里的绑定）> 全局设置
（`Settings.lottery_animation`）。奖品侧栏每一行都能单独选动画与视频；
选中「视频揭晓」但没指定视频时，从视频仓库里随机挑一段（原版视频是全局
单文件，这里升级成「多视频 + 每奖项可选」）。
"""
from __future__ import annotations

import os

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from app.core import lottery, videos
from app.core.state import ChangeEvent
from app.services.timer_registry import TimerRegistry
from app.ui import theme
from app.ui.widgets import video_picker as video_widgets

TICK_KEY = 'lottery:tick'
SCOPE = 'lottery'
TICK_MS = 90
TOTAL_FRAMES = 12
VIDEO_TICK_MS = 55
VIDEO_FRAMES = 0

SIDE_W = 280
PICKER_MAX_H = 128
# 候选名单区的高度上限：超过就内部滚动，别把弹窗撑出屏幕。
PRIZE_BOX_MAX_H = 132
HISTORY_MAX_H = 118
CHIP_MIN_W = 72

DRAW_TITLE = '抽奖'
RANDOM_TITLE = '随机抽人'

IDLE_HINT = '点「抽奖」开始'
IDLE_SUB = '不点选同学时，会从够门槛的名单里等概率自动抽一位'

SLOT_DONE_QSS = (
    'QFrame#lqSlot { background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #f2fdf7, stop:1 '
    f'{theme.OK_BG}'
    '); border: 2px solid #bdf0d6; border-radius: 16px; }'
)
SLOT_ROLLING_QSS = (
    'QFrame#lqSlot { background: #f5f8ff; border: 2px dashed '
    f'{theme.SEAT_HOVER_BORDER}'
    '; border-radius: 16px; }'
)
SLOT_IDLE_QSS = (
    'QFrame#lqSlot { background: '
    f'{theme.SEAT_BG}'
    '; border: 2px dashed '
    f'{theme.LINE}'
    '; border-radius: 16px; }'
)
RESULT_DONE_QSS = (
    'QFrame#rdResult { background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #f2fdf7, stop:1 '
    f'{theme.OK_BG}'
    '); border: 2px solid #bdf0d6; border-radius: 16px; }'
)
RESULT_ROLLING_QSS = (
    'QFrame#rdResult { background: #f5f8ff; border: 2px dashed '
    f'{theme.SEAT_HOVER_BORDER}'
    '; border-radius: 16px; }'
)
RESULT_IDLE_QSS = (
    'QFrame#rdResult { background: '
    f'{theme.SEAT_BG}'
    '; border: 2px dashed '
    f'{theme.LINE}'
    '; border-radius: 16px; }'
)


def _lottery_mode(state) -> str:
    """当前抽奖资格判定方式（"threshold"/"rank"）。

    老存档的 Settings 可能没有该字段，按门槛处理——不能因为缺字段就崩。
    """
    return getattr(state.settings, 'lottery_mode', 'threshold') or 'threshold'


def _lottery_top_n(state) -> int:
    """排名模式的「取前几名」（非法值按 10 兜底，与 Settings 默认一致）。"""
    try:
        return int(getattr(state.settings, 'lottery_top_n', 10))
    except (TypeError, ValueError):
        return 10


class LotteryView(QWidget):
    """抽奖页（draw）与随机抽人页（random）。"""

    changed = pyqtSignal()
    announced = pyqtSignal(str, str)
    prizes_edit_requested = pyqtSignal()

    def __init__(self, store, registry: TimerRegistry, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._store = store
        self._registry = registry
        self._mode = 'draw'
        self._frame = 0
        self._rolling = False
        self._pending_final = ''
        self._pending_sub = ''
        self._pending_animation = ''
        self._pending_video = None
        self._last_video = None
        self._player_dialog = None
        self._player_shell = None
        self._selected_id = ''
        self._auto_selected = False
        self._seeded = False
        self._mode_seen = ''
        self._empty_pool_hint = '还没有同学达到判定分数'
        # 本轮已抽中的学生 id：灰显、不可再抽，`reset_drawn()` 从存档恢复。
        self._drawn = set()
        self._chips = {}
        self._anim_combos = {}
        self._video_combos = {}
        self._build()
        self.refresh()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(18)
        self._draw_page = self._build_draw_page()
        root.addWidget(self._draw_page, 1)
        self._random_page = self._build_random_page()
        root.addWidget(self._random_page, 1)
        self._random_page.setVisible(False)

    def _build_draw_page(self) -> QWidget:
        page = QWidget(self)
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(14)
        outer.addWidget(self._build_config())
        body = QHBoxLayout()
        body.setSpacing(18)
        body.addWidget(self._build_stage(), 1)
        body.addWidget(self._build_side(), 0)
        outer.addLayout(body, 1)
        return page

    def _build_config(self) -> QFrame:
        frame = QFrame(self)
        bar = QHBoxLayout(frame)
        bar.setContentsMargins(0, 0, 0, 0)
        bar.setSpacing(14)
        # 顶部一条：判定分 / 每次扣除 / 同步按钮 / 名次标签 / 动画标签。
        self._threshold_label = _field_label('判定分数', frame)
        bar.addWidget(self._threshold_label)
        self._threshold = QSpinBox(frame)
        self._threshold.setRange(0, 9999)
        self._threshold.setFixedWidth(84)
        self._threshold.valueChanged.connect(self._on_threshold_changed)
        bar.addWidget(self._threshold)
        bar.addWidget(_field_label('每次扣除', frame))
        self._cost = QSpinBox(frame)
        self._cost.setRange(0, 9999)
        self._cost.setFixedWidth(84)
        self._cost.valueChanged.connect(self._on_cost_changed)
        bar.addWidget(self._cost)
        self._sync_btn = QPushButton('扣除=判定分', frame)
        self._sync_btn.setObjectName('ghost')
        self._sync_btn.setStyleSheet(_mini_qss())
        self._sync_btn.clicked.connect(self._sync_cost_to_threshold)
        bar.addWidget(self._sync_btn)
        # 排名模式顶替「判定分数」的一枚胶囊标签。
        self._rank_tag = QLabel('', frame)
        self._rank_tag.setStyleSheet(
            f'color: {theme.BRAND_DARK}; background: {theme.SEL_ACTIVE_BG};'
            ' border-radius: 999px; padding: 5px 12px; font-size: 12px; font-weight: 700;'
        )
        self._rank_tag.setVisible(False)
        bar.addWidget(self._rank_tag)
        self._anim_tag = QLabel('', frame)
        self._anim_tag.setStyleSheet(
            f'color: {theme.INK2}; background: {theme.PILL_GRAY_BG};'
            ' border-radius: 999px; padding: 5px 12px; font-size: 12px;'
        )
        bar.addWidget(self._anim_tag)
        bar.addStretch(1)
        return frame

    def _build_stage(self) -> QWidget:
        stage = QWidget(self)
        lay = QVBoxLayout(stage)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        self._pool_label = QLabel('', stage)
        self._pool_label.setStyleSheet(
            f'color: {theme.INK2}'
            '; background: transparent; font-size: 14px;'
        )
        lay.addWidget(self._pool_label)
        self._picker = QScrollArea(stage)
        self._picker.setWidgetResizable(True)
        self._picker.setFixedHeight(PICKER_MAX_H)
        self._picker.setFrameShape(QFrame.NoFrame)
        self._picker.setStyleSheet(
            'QScrollArea { background: '
            f'{theme.SEAT_BG!s}'
            '; border: 1px solid '
            f'{theme.LINE!s}'
            '; border-radius: 12px; }'
        )
        self._picker_host = QWidget(self._picker)
        self._picker_grid = QGridLayout(self._picker_host)
        self._picker_grid.setContentsMargins(10, 10, 10, 10)
        self._picker_grid.setSpacing(8)
        self._picker.setWidget(self._picker_host)
        lay.addWidget(self._picker)
        self._slot = QFrame(stage)
        self._slot.setObjectName('lqSlot')
        self._slot.setMinimumHeight(88)
        self._slot.setStyleSheet(SLOT_IDLE_QSS)
        slot_lay = QVBoxLayout(self._slot)
        slot_lay.setContentsMargins(14, 10, 14, 10)
        slot_lay.setSpacing(6)
        self._slot_big = QLabel(IDLE_HINT, self._slot)
        self._slot_big.setAlignment(Qt.AlignCenter)
        self._slot_big.setWordWrap(True)
        self._slot_big.setStyleSheet(_slot_text_qss(theme.INK3, 26, 800))
        self._slot_small = QLabel('', self._slot)
        self._slot_small.setAlignment(Qt.AlignCenter)
        self._slot_small.setWordWrap(True)
        self._slot_small.setStyleSheet(_slot_text_qss(theme.INK2, 13, 500))
        slot_lay.addWidget(self._slot_big, 1)
        slot_lay.addWidget(self._slot_small)
        lay.addWidget(self._slot)
        self._draw_btn = QPushButton('抽奖', stage)
        self._draw_btn.setStyleSheet(_big_qss('primary'))
        self._draw_btn.setCursor(Qt.PointingHandCursor)
        self._draw_btn.clicked.connect(self._do_draw)
        lay.addWidget(self._draw_btn)
        self._tip = QLabel(
            '点「抽奖」直接抽：不点选同学时，从够门槛的名单里等概率自动抽一位；'
            '先点选同学则指定该同学。中奖者扣除设定分数，揭晓动画按右侧奖品设置。',
            stage,
        )
        self._tip.setWordWrap(True)
        self._tip.setStyleSheet(
            f'color: {theme.INK3}'
            '; background: transparent; font-size: 12px;'
        )
        lay.addWidget(self._tip)
        return stage

    def _build_side(self) -> QWidget:
        side = QWidget(self)
        side.setFixedWidth(SIDE_W)
        lay = QVBoxLayout(side)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        lay.addWidget(_side_title('奖品与权重', side))
        self._prize_box = QWidget(side)
        self._prize_lay = QVBoxLayout(self._prize_box)
        self._prize_lay.setContentsMargins(0, 0, 0, 0)
        self._prize_lay.setSpacing(4)
        self._prize_scroll = QScrollArea(side)
        self._prize_scroll.setWidgetResizable(True)
        self._prize_scroll.setFrameShape(QFrame.NoFrame)
        self._prize_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._prize_scroll.setMaximumHeight(PRIZE_BOX_MAX_H)
        self._prize_scroll.setWidget(self._prize_box)
        lay.addWidget(self._prize_scroll)
        self._edit_prizes = QPushButton('编辑奖品', side)
        self._edit_prizes.setObjectName('mini')
        self._edit_prizes.setStyleSheet(_mini_qss())
        self._edit_prizes.clicked.connect(self.prizes_edit_requested.emit)
        lay.addWidget(self._edit_prizes)
        self._video_btn = QPushButton('视频库（0）', side)
        self._video_btn.setObjectName('mini')
        self._video_btn.setStyleSheet(_mini_qss())
        self._video_btn.setCursor(Qt.PointingHandCursor)
        self._video_btn.clicked.connect(self._open_video_manager)
        lay.addWidget(self._video_btn)
        history_head = QHBoxLayout()
        history_head.setContentsMargins(0, 0, 0, 0)
        history_head.setSpacing(6)
        history_head.addWidget(_side_title('抽奖记录', side), 1)
        self._btn_clear_history = QPushButton('清空', side)
        self._btn_clear_history.setObjectName('stClearLottery')
        self._btn_clear_history.setStyleSheet(_mini_qss())
        self._btn_clear_history.setCursor(Qt.PointingHandCursor)
        self._btn_clear_history.setToolTip('清空抽奖记录（学生、积分、奖品都不受影响）')
        self._btn_clear_history.clicked.connect(self._clear_history)
        history_head.addWidget(self._btn_clear_history, 0)
        lay.addLayout(history_head)
        self._history = QLabel('', side)
        self._history.setWordWrap(True)
        self._history.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self._history.setFixedHeight(HISTORY_MAX_H)
        self._history.setStyleSheet(
            f'color: {theme.INK2}'
            '; background: transparent; font-size: 12px;'
        )
        lay.addWidget(self._history)
        lay.addStretch(1)
        return side

    def _build_random_page(self) -> QWidget:
        page = QWidget(self)
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(14)
        config = QHBoxLayout()
        config.setSpacing(12)
        config.addWidget(_field_label('抽取人数', page))
        self._rd_count = QSpinBox(page)
        self._rd_count.setRange(1, 999)
        self._rd_count.setFixedWidth(84)
        config.addWidget(self._rd_count)
        for text, fn in (
            ('1 人', lambda: self._set_count(1)),
            ('一半', self._set_count_half),
            ('全部', self._set_count_all),
        ):
            btn = QPushButton(text, page)
            btn.setObjectName('ghost')
            btn.setStyleSheet(_mini_qss())
            btn.clicked.connect(fn)
            config.addWidget(btn)
        self._rd_total = QLabel('', page)
        self._rd_total.setStyleSheet(
            f'color: {theme.INK3}'
            '; background: transparent; font-size: 12px;'
        )
        config.addWidget(self._rd_total)
        config.addStretch(1)
        lay.addLayout(config)
        self._rd_result = QFrame(page)
        self._rd_result.setObjectName('rdResult')
        self._rd_result.setMinimumHeight(104)
        self._rd_result.setStyleSheet(RESULT_IDLE_QSS)
        rlay = QVBoxLayout(self._rd_result)
        rlay.setContentsMargins(16, 16, 16, 16)
        rlay.setSpacing(10)
        self._rd_names = QLabel('还没开始，点下面的按钮随机抽人', self._rd_result)
        self._rd_names.setAlignment(Qt.AlignCenter)
        self._rd_names.setWordWrap(True)
        self._rd_names.setStyleSheet(_slot_text_qss(theme.INK3, 13.5, 500))
        self._rd_sum = QLabel('', self._rd_result)
        self._rd_sum.setAlignment(Qt.AlignCenter)
        self._rd_sum.setStyleSheet(_slot_text_qss(theme.INK2, 13.5, 500))
        rlay.addWidget(self._rd_names, 1)
        rlay.addWidget(self._rd_sum)
        lay.addWidget(self._rd_result, 1)
        self._rd_go = QPushButton('开始随机', page)
        self._rd_go.setStyleSheet(_big_qss('primary'))
        self._rd_go.setCursor(Qt.PointingHandCursor)
        self._rd_go.clicked.connect(self._do_random)
        lay.addWidget(self._rd_go, 0, Qt.AlignHCenter)
        return page

    def set_mode(self, mode: str) -> None:
        """'draw' 抽奖 / 'random' 随机抽人。"""
        self._mode = 'random' if mode == 'random' else 'draw'
        self._draw_page.setVisible(self._mode == 'draw')
        self._random_page.setVisible(self._mode == 'random')
        if self._mode == 'random':
            self.refresh()
            return

    def mode(self) -> str:
        return self._mode

    def refresh(self) -> None:
        state = self._store.state
        self._apply_lottery_mode(state)
        # 首次进入把 settings 灌进输入口；之后尊重用户手敲的值。
        if not self._seeded:
            self._threshold.blockSignals(True)
            self._threshold.setValue(int(state.settings.prize_threshold))
            self._threshold.blockSignals(False)
            self._cost.blockSignals(True)
            self._cost.setValue(int(state.settings.prize_cost))
            self._cost.blockSignals(False)
            self._seeded = True
        label = {
            'none': '无动画（默认）',
            'roll': '滚动',
            'cards': '翻牌',
            'wheel': '转盘',
            'video': '视频揭晓',
        }.get(state.settings.lottery_animation, '无动画（默认）')
        if videos.list_bindings():
            self._anim_tag.setText('动画：按奖品设置')
        else:
            self._anim_tag.setText(f'动画：{label}')
        self._anim_tag.setToolTip(
            f'全局设置：{label}'
            '；每个奖项可在右侧单独覆盖，选「视频揭晓」未指定视频时从视频库随机挑一段'
        )
        self._render_prizes(state)
        self._video_btn.setText(f'视频库（{len(videos.list_videos())}）')
        self._render_picker(state)
        self._render_history(state)
        self._render_random(state)
        self._render_slot(state)

    def resync_from_settings(self) -> None:
        """设置弹窗里改了判定分/扣分后，把新值灌回输入口。"""
        self._seeded = False
        self.refresh()

    def _apply_lottery_mode(self, state) -> None:
        """按资格判定方式切换顶部控件（排名模式没有「判定分数」可填）。

        模式切换时把输入口重新灌一次 settings：否则在设置里改完判定分/取前几名
        再切回门槛模式，输入框还留着切换前手敲的旧值（settings 才是唯一真相）。
        """
        mode = _lottery_mode(state)
        ranked = mode == 'rank'
        if mode != self._mode_seen:
            self._mode_seen = mode
            self._seeded = False
        self._threshold_label.setVisible(not ranked)
        self._threshold.setVisible(not ranked)
        self._sync_btn.setVisible(not ranked)
        self._rank_tag.setVisible(ranked)
        if ranked:
            self._rank_tag.setText(f'资格：积分前 {_lottery_top_n(state)} 名')
            self._rank_tag.setToolTip(
                '排名模式下只有积分前 N 名有资格（并列同进）；N 与判定方式在「设置 → 抽奖」里改'
            )
            self._tip.setText(
                '点「抽奖」直接抽：不点选同学时，从积分前 %d 名的名单里等概率自动抽一位；'
                '先点选同学则指定该同学。中奖者扣除设定分数，揭晓动画按右侧奖品设置。'
                % _lottery_top_n(state)
            )
            return
        self._rank_tag.setText('')
        self._rank_tag.setToolTip('')
        self._tip.setText(
            '点「抽奖」直接抽：不点选同学时，从够门槛的名单里等概率自动抽一位；'
            '先点选同学则指定该同学。中奖者扣除设定分数，揭晓动画按右侧奖品设置。'
        )

    def _render_prizes(self, state) -> None:
        """奖品列表：每项两行——名字/权重 + 「揭晓动画 / 视频」两个下拉框。"""
        while self._prize_lay.count():
            item = self._prize_lay.takeAt(0)
            widget = item.widget()
            if widget is None:
                continue
            widget.setParent(None)
            widget.deleteLater()
        self._anim_combos = {}
        self._video_combos = {}
        if not state.prizes:
            self._prize_lay.addWidget(_muted('还没有配置奖品'))
            return
        clips = videos.list_videos()
        for prize in state.prizes:
            binding = videos.get_prize_binding(prize.id)
            row = QFrame(self._prize_box)
            row.setStyleSheet(
                f'QFrame {{ background: {theme.BTN_GHOST_BG}; border-radius: 9px; }}'
            )
            rl = QVBoxLayout(row)
            rl.setContentsMargins(9, 4, 9, 5)
            rl.setSpacing(4)
            ctl = QHBoxLayout()
            ctl.setSpacing(6)
            name = QLabel(prize.name, row)
            name.setStyleSheet(
                f'color: {theme.INK}'
                '; background: transparent; font-size: 13px;'
            )
            name.setMinimumWidth(56)
            ctl.addWidget(name, 1)
            weight = QLabel(f'{prize.weight:g}', row)
            weight.setStyleSheet(
                f'color: {theme.INK3}'
                '; background: transparent; font-size: 12px;'
            )
            weight.setToolTip('抽中权重')
            ctl.addWidget(weight)
            anim = QComboBox(row)
            for key in ('', 'none', 'roll', 'cards', 'wheel', 'video'):
                anim.addItem(video_widgets.animation_label(key), key)
            anim.setStyleSheet(_combo_qss())
            index = anim.findData(binding.animation)
            anim.setCurrentIndex(index if index >= 0 else 0)
            anim.setFixedWidth(92)
            anim.setToolTip('这个奖项的揭晓动画；默认跟随设置里的全局动画')
            ctl.addWidget(anim)
            clip = QComboBox(row)
            clip.addItem('随机视频', '')
            for info in clips:
                clip.addItem(info.name, info.id)
            clip.setStyleSheet(_combo_qss())
            clip_index = clip.findData(binding.video_id)
            clip.setCurrentIndex(clip_index if clip_index >= 0 else 0)
            clip.setVisible(binding.animation == 'video')
            clip.setEnabled(binding.animation == 'video')
            clip.setToolTip('选「视频揭晓」后播放的视频；选「随机视频」则从视频库随机挑一段')
            ctl.addWidget(clip, 1)
            rl.addLayout(ctl)
            self._anim_combos[prize.id] = anim
            self._video_combos[prize.id] = clip
            anim.currentIndexChanged.connect(
                lambda _i, pid=prize.id, box=anim: self._on_prize_animation_changed(pid, box)
            )
            clip.currentIndexChanged.connect(
                lambda _i, pid=prize.id, box=clip: self._on_prize_video_changed(pid, box)
            )
            self._prize_lay.addWidget(row)

    def _on_prize_animation_changed(self, prize_id: str, combo: QComboBox) -> None:
        key = combo.currentData() or ''
        videos.set_prize_animation(prize_id, key)
        clip = self._video_combos.get(prize_id)
        if clip is not None:
            clip.setVisible(key == 'video')
            clip.setEnabled(key == 'video')
            if key != 'video':
                clip.blockSignals(True)
                clip.setCurrentIndex(0)
                clip.blockSignals(False)
        if key == 'video' and not videos.list_videos():
            self._announce('还没有导入视频：点右侧「视频库」导入后才能播放', 'warn')
        self._refresh_anim_tag()

    def _on_prize_video_changed(self, prize_id: str, combo: QComboBox) -> None:
        video_id = combo.currentData() or ''
        videos.set_prize_video(prize_id, video_id)
        if video_id:
            anim = self._anim_combos.get(prize_id)
            if anim is not None:
                index = anim.findData('video')
                if index >= 0:
                    anim.blockSignals(True)
                    anim.setCurrentIndex(index)
                    anim.blockSignals(False)
        self._refresh_anim_tag()

    def _refresh_anim_tag(self) -> None:
        label = {
            'none': '无动画（默认）',
            'roll': '滚动',
            'cards': '翻牌',
            'wheel': '转盘',
            'video': '视频揭晓',
        }.get(self._store.state.settings.lottery_animation, '无动画（默认）')
        if videos.list_bindings():
            self._anim_tag.setText('动画：按奖品设置')
            return
        self._anim_tag.setText(f'动画：{label}')

    def _open_video_manager(self) -> None:
        dialog = video_widgets.VideoManageDialog(self.window())
        dialog.videos_changed.connect(self.refresh)
        try:
            dialog.exec_()
        finally:
            self.refresh()

    def _remaining_pool(self) -> list:
        """本轮还能被抽到的人（资格池 − 已抽中，且扣得起分）。

        抽奖按钮与自动选中都走这份名单：已抽中的人不能再被抽第二次，
        积分不够扣的人也不该被抽中再判定失败。
        """
        state = self._store.state
        pool = lottery.eligible_pool(state, int(self._threshold.value()))
        pool = [s for s in pool if s.id not in self._drawn]
        cost = 0 if _lottery_mode(state) == 'rank' else int(self._cost.value())
        if cost > 0:
            pool = [s for s in pool if int(s.points) >= cost]
        return pool

    def _render_picker(self, state) -> None:
        pool = lottery.eligible_pool(state, int(self._threshold.value()))
        if self._selected_id and state.student_by_id(self._selected_id) is None:
            self._selected_id = ''
        total = len(state.students)
        left = len([s for s in pool if s.id not in self._drawn])
        if _lottery_mode(state) == 'rank':
            self._pool_label.setText(
                f'资格：积分前 {_lottery_top_n(state)} 名 · 当前 {len(pool)} 人（已抽 {len(self._drawn)} 人，剩 {left} 人）'
            )
            self._empty_pool_hint = (
                '还没有同学，先去座位表加人' if total == 0
                else f'没有符合条件的同学（积分前 {_lottery_top_n(state)} 名）'
            )
        else:
            threshold = int(self._threshold.value())
            self._pool_label.setText(
                f'够门槛的同学 {len(pool)} 人（全班级 {total} 人） · 门槛 {threshold} 分 · 剩 {left} 人'
            )
            self._empty_pool_hint = '还没有同学达到判定分数'
        ids = [s.id for s in pool]
        if ids != list(self._chips):
            self._rebuild_picker(pool)
        for student in pool:
            chip = self._chips.get(student.id)
            if chip is None:
                continue
            self._style_chip(chip, student)

    def reset_drawn(self) -> None:
        """重开抽奖界面：从存档里的抽奖记录恢复「已抽中」标记。

        以前这里直接 clear()，于是关掉窗口再进来，所有人又变亮——用户以为
        「已抽中」根本没生效。现在以 state.lottery 为准：抽过的人一直是灰的，
        想让大家重新可选，用「清空抽奖记录」。
        """
        state = self._store.state
        self._drawn = {sid for rec in state.lottery for sid in rec.winners or []}
        self._auto_selected = False
        for sid, chip in self._chips.items():
            student = state.student_by_id(sid)
            if student is None:
                continue
            self._style_chip(chip, student)
        self._render_picker(state)
        self._render_slot(state)

    def _rebuild_picker(self, pool) -> None:
        while self._picker_grid.count():
            item = self._picker_grid.takeAt(0)
            widget = item.widget()
            if widget is None:
                continue
            widget.setParent(None)
            widget.deleteLater()
        self._chips = {}
        if not pool:
            self._picker_grid.addWidget(_muted(self._empty_pool_hint), 0, 0, 1, 4)
            return
        for index, student in enumerate(pool):
            chip = QPushButton(self._picker_host)
            chip.setCursor(Qt.PointingHandCursor)
            chip.clicked.connect(lambda _c=False, sid=student.id: self.select_student(sid))
            self._style_chip(chip, student)
            self._picker_grid.addWidget(chip, index // 4, index % 4)
            self._chips[student.id] = chip
        self._picker_grid.setColumnStretch(4, 1)
        self._picker_grid.setRowStretch(self._picker_grid.rowCount(), 1)

    def _style_chip(self, chip: QPushButton, student) -> None:
        drawn = student.id in self._drawn
        selected = (student.id == self._selected_id) and not drawn
        if drawn:
            # 已抽中：整枚变灰、禁用，但仍留在网格里（顺序不变）。
            border = theme.PILL_GRAY_BG
            bg = theme.PILL_GRAY_BG
            ink = theme.INK3
        else:
            border = theme.BRAND if selected else theme.LINE
            bg = theme.SEL_ACTIVE_BG if selected else theme.CARD
            ink = theme.BRAND_DARK if selected else theme.INK
        chip.setEnabled(not drawn)
        chip.setCursor(Qt.ForbiddenCursor if drawn else Qt.PointingHandCursor)
        chip.setText(f'{student.name}\n{student.points} 分')
        chip.setMinimumWidth(CHIP_MIN_W)
        chip.setStyleSheet(
            'QPushButton { background: '
            f'{bg}'
            '; border: 1.5px solid '
            f'{border}'
            '; border-radius: 10px; padding: 7px 12px; font-size: 14px; font-weight: 700; color: '
            f'{ink}'
            '; }QPushButton:disabled { color: '
            f'{theme.INK3}'
            '; background: '
            f'{theme.PILL_GRAY_BG}'
            '; border-color: '
            f'{theme.PILL_GRAY_BG}'
            '; }'
        )

    def _clear_history(self) -> None:
        """清空抽奖记录（只清记录，不动学生、积分与奖品）。"""
        removed = lottery.clear_records(self._store.state)
        if not removed:
            self._announce('还没有抽奖记录', 'warn')
            self._render_history(self._store.state)
            return
        self._store.save_now()
        self._render_history(self._store.state)
        self._announce(f'已清空 {removed} 条抽奖记录', 'ok')

    def _render_history(self, state) -> None:
        records = list(reversed(state.lottery))[:8]
        if not records:
            self._history.setText('还没有抽奖记录')
            return
        lines = []
        for rec in records:
            names = '、'.join(rec.winner_names) or '—'
            lines.append(f'{rec.prize_name} → {names}（-{rec.cost}）')
        self._history.setText('\n'.join(lines))

    def _render_random(self, state) -> None:
        total = len(state.students)
        self._rd_count.setMaximum(max(1, total))
        current = int(state.settings.random_count or 1)
        self._rd_count.blockSignals(True)
        self._rd_count.setValue(min(max(1, current), max(1, total)))
        self._rd_count.blockSignals(False)
        self._rd_total.setText(f'共 {total} 人 · 机会均等、不重复')

    def _render_slot(self, state) -> None:
        if self._registry.is_pending(TICK_KEY):
            return
        self._rolling = False
        student = state.student_by_id(self._selected_id) if self._selected_id else None
        if student is None:
            self._slot.setStyleSheet(SLOT_IDLE_QSS)
            self._slot_big.setStyleSheet(_slot_text_qss(theme.INK3, 26, 800))
            self._slot_big.setText(IDLE_HINT)
            self._slot_small.setText(self._idle_sub_text(state))
        else:
            cost = 0 if _lottery_mode(state) == 'rank' else int(self._cost.value())
            self._slot.setStyleSheet(SLOT_IDLE_QSS)
            self._slot_big.setStyleSheet(_slot_text_qss(theme.BRAND_DARK, 24, 800))
            self._slot_big.setText(f'已选：{student.name}')
            tail = (
                f' · 抽奖后扣 {cost} 分，剩余 {student.points - cost} 分' if cost > 0
                else ' · 不扣分'
            )
            self._slot_small.setText(f'当前 {student.points} 分{tail}')
        self._draw_btn.setEnabled(not self._registry.is_pending(TICK_KEY))

    def select_student(self, student_id: str) -> None:
        """点选 / 再点一次取消（app.js:3185 selectedId）。"""
        self._auto_selected = False
        self._selected_id = '' if student_id == self._selected_id else (student_id or '')
        state = self._store.state
        for sid, chip in self._chips.items():
            student = state.student_by_id(sid)
            if student is None:
                continue
            self._style_chip(chip, student)
        self._render_slot(state)

    def selected_id(self) -> str:
        return self._selected_id

    def _idle_sub_text(self, state) -> str:
        """结果区空闲提示：说清「自动抽一位」是从哪个池子里抽的。"""
        if _lottery_mode(state) == 'rank':
            return f'不点选同学时，会从积分前 {_lottery_top_n(state)} 名的名单里等概率自动抽一位'
        return IDLE_SUB

    def _on_threshold_changed(self) -> None:
        """判定分改动：只重列候选名单（app.js:3295 renderPool）。"""
        self._render_picker(self._store.state)
        self._render_slot(self._store.state)

    def _on_cost_changed(self) -> None:
        """扣分改动：只刷新结果区预览（app.js:3296）。"""
        self._render_slot(self._store.state)

    def _sync_cost_to_threshold(self) -> None:
        self._cost.setValue(int(self._threshold.value()))

    def _do_draw(self) -> None:
        """抽奖：点选过同学就抽该同学；没点选则从够门槛的名单里等概率自动抽一位。

        原版这里是「先点选再抽」，但用户会把它当按钮直接点——旧实现未选中时
        按钮置灰、`_do_draw` 也只弹一句「先点选」，看起来就是「点击无效」。
        现在保证点一次一定出结果（除非班/奖品为空，那也要给出明确文案）。
        """
        if self._registry.is_pending(TICK_KEY):
            return
        state = self._store.state
        threshold = int(self._threshold.value())
        cost = int(self._cost.value())
        rank_mode = _lottery_mode(state) == 'rank'
        if rank_mode:
            # 排名模式不扣分（原版随机抽人语义），阈值标签也不显示。
            cost = 0
        settings = state.settings
        if not rank_mode:
            settings.prize_threshold = threshold
        settings.prize_cost = cost
        if not state.prizes:
            reason = '还没有配置奖品，先点右侧「编辑奖品」加一个'
            self._show_error(reason)
            self._announce(reason, 'warn')
            return
        student_id = self._selected_id
        if self._auto_selected:
            # 上一次是自动选中的：用户再点按钮时改走随机，别绑死在同一个人身上。
            student_id = ''
            self._auto_selected = False
        if student_id and student_id in self._drawn:
            reason = '这位同学本轮已经抽中过了，换一位吧'
            self._show_error(reason)
            self._announce(reason, 'warn')
            return
        auto = False
        if not student_id:
            pool = lottery.eligible_pool(state, threshold)
            pool = [s for s in pool if s.id not in self._drawn]
            affordable = [s for s in pool if int(s.points) >= cost] if cost > 0 else pool
            if not affordable:
                if pool:
                    if rank_mode:
                        reason = '积分前 %d 名里的同学积分不足以扣除，请先加分或调低「每次扣除」' % _lottery_top_n(state)
                    else:
                        reason = '够判定分的同学积分不足以扣除，请先加分或调低「每次扣除」'
                elif self._drawn and not [
                    s
                    for s in lottery.eligible_pool(state, threshold)
                    if s.id not in self._drawn
                ]:
                    reason = f'本轮有资格的同学都抽过了（共 {len(self._drawn)} 人）'
                elif rank_mode:
                    reason = self._rank_empty_reason(state)
                else:
                    reason = f'还没有同学达到判定分（{threshold} 分），请先加分或调低判定分'
                self._show_error(reason)
                self._announce(reason, 'warn')
                return
            chosen = lottery.sample_students(affordable, 1)[0]
            student_id = chosen.id
            auto = True
            self._force_select(student_id)
            self._auto_selected = True
        result = lottery.draw_for_student(state, student_id, cost=cost)
        if not result.ok:
            reason = result.reason or '抽奖失败'
            self._show_error(reason)
            self._announce(reason, 'bad')
            return
        self._drawn.update(s.id for s in result.winners)
        prize_name = result.prize.name if result.prize is not None else '奖品'
        names = '、'.join(s.name for s in result.winners)
        self._store.emit(ChangeEvent(reason='lottery', scope='lottery'))
        self.changed.emit()
        final = f'{names} 抽中 {prize_name}'
        sub = (
            f'{prize_name} · 不扣分' if cost <= 0
            else f'{prize_name} · 扣除 {cost} 分'
        )
        self._animate(final, sub, prize=result.prize)
        prefix = '自动选中 ' if auto else ''
        self._announce(f'{prefix}{final}', 'ok')
        # 已抽中的置灰后重列；结果区交给动画收尾。
        self._render_picker(state)

    def _rank_empty_reason(self, state) -> str:
        """排名模式下池子为空时给一句能照着做的提示（不能是灰按钮死路）。"""
        n = _lottery_top_n(state)
        if not state.students:
            return '班里还没有同学，先去座位表加人'
        if n <= 0:
            return '设置里的「取前几名」是 0，排名模式下没人有资格，请到「设置 → 抽奖」调成 1 或更大'
        return f'排名模式下没有符合条件的同学（积分前 {n} 名），请先在设置里调整名次或给同学加分'

    def _force_select(self, student_id: str) -> None:
        """程序性选中（不触发「再点取消」的语义）。"""
        self._selected_id = student_id or ''
        state = self._store.state
        for sid, chip in self._chips.items():
            student = state.student_by_id(sid)
            if student is None:
                continue
            self._style_chip(chip, student)
        self._render_slot(state)

    def _set_count(self, value: int) -> None:
        self._rd_count.setValue(max(1, int(value)))

    def _set_count_half(self) -> None:
        total = len(self._store.state.students)
        self._set_count(max(1, (total + 1) // 2))

    def _set_count_all(self) -> None:
        self._set_count(max(1, len(self._store.state.students)))

    def _do_random(self) -> None:
        state = self._store.state
        count = max(1, int(self._rd_count.value()))
        picks = lottery.pick_random_winners(state, count)
        state.settings.random_count = count
        if not picks:
            self._rd_result.setStyleSheet(RESULT_IDLE_QSS)
            self._rd_names.setStyleSheet(_slot_text_qss(theme.INK3, 13.5, 500))
            self._rd_names.setText('班里还没有学生')
            self._rd_sum.setText('')
            return
        names = '、'.join(f'{s.name}（{s.points} 分）' for s in picks)
        self._rd_result.setStyleSheet(RESULT_DONE_QSS)
        self._rd_names.setStyleSheet(_slot_text_qss(theme.OK_BTN_INK, 17, 800))
        self._rd_names.setText(names)
        self._rd_sum.setText(f'共抽中 {len(picks)} 人')
        self._store.emit(ChangeEvent(reason='random', scope='lottery'))
        self._announce(f'随机抽中 {len(picks)} 人', 'ok')

    def _animate(self, final_text: str, sub_text: str = '', prize=None) -> None:
        """按「奖项设置 > 全局设置」决定揭晓方式，并起帧动画。

        prize 是本次抽中的 Prize；没有奖项时（理论上不会发生）回落到全局设置。
        """
        state = self._store.state
        prize_id = prize.id if prize is not None else ''
        animation, video = videos.resolve_for_prize(
            prize_id, state.settings.lottery_animation
        )
        self._pending_final = final_text
        self._pending_sub = sub_text
        self._pending_animation = animation
        self._pending_video = video
        self._registry.cancel_scope(SCOPE)
        self._draw_btn.setEnabled(False)
        if animation == 'video':
            # 视频分支：直接进「视频揭晓中」态，等播放器回调收尾。
            self._frame = 0
            self._rolling = True
            self._slot.setStyleSheet(SLOT_ROLLING_QSS)
            self._slot_big.setStyleSheet(_slot_text_qss(theme.BRAND_DARK, 24, 800))
            self._slot_big.setText('视频揭晓中…')
            if video is not None:
                self._slot_small.setText(f'正在播放：{video.name}')
            else:
                self._slot_small.setText('视频库里还没有视频，马上直接揭晓结果')
            self._show_video_final()
            return
        if animation not in {'wheel', 'roll', 'cards'}:
            self._show_final(final_text, sub_text)
            return
        self._frame = 0
        self._rolling = True
        self._slot.setStyleSheet(SLOT_ROLLING_QSS)
        self._slot_big.setStyleSheet(_slot_text_qss(theme.BRAND_DARK, 24, 800))
        self._slot_big.setText('抽取中…')
        self._slot_small.setText('')
        self._registry.schedule_repeating(TICK_KEY, TICK_MS, self._tick, scope=SCOPE)

    def _tick(self) -> None:
        self._frame += 1
        video_mode = self._pending_animation == 'video'
        total = VIDEO_FRAMES if video_mode else TOTAL_FRAMES
        if self._frame >= total:
            self._registry.cancel(TICK_KEY)
            if video_mode:
                self._show_video_final()
            else:
                self._show_final(self._pending_final, self._pending_sub)
            return
        dots = '.' * (self._frame % 3 + 1)
        base = '视频揭晓中' if video_mode else '抽取中'
        self._slot_big.setText(f'{base}{dots}')

    def _finish_slot(self, text: str, sub_text: str = '') -> None:
        """把结果区切到「已完成」态。

        注意：这里**不能**再调 `_render_slot()`——那会把刚写进去的结果立刻
        覆盖成「已选：某某」，用户就会觉得抽奖没反应（历史 bug）。
        """
        self._rolling = False
        self._slot.setStyleSheet(SLOT_DONE_QSS)
        self._slot_big.setStyleSheet(_slot_text_qss(theme.OK_BTN_INK, 30, 800))
        self._slot_big.setText(text or '—')
        self._slot_small.setText(sub_text)
        self._draw_btn.setEnabled(True)

    def _show_final(self, text: str, sub_text: str = '') -> None:
        self._last_video = None
        self._finish_slot(text, sub_text)

    def _show_video_final(self) -> None:
        video = self._pending_video
        self._last_video = video
        # 自动播放失败（无播放器 / 环境禁用）时也必须把结果揭出来，别卡在「视频揭晓中」。
        if video is None or not self._maybe_autoplay(video):
            self._reveal_pending()

    def _reveal_pending(self) -> None:
        """把待揭晓的结果真正显示出来（视频播完 / 中途关掉 / 播不了）。"""
        self._finish_slot(self._pending_final, videos.reveal_subtitle(self._last_video))

    def _show_error(self, text: str) -> None:
        if self._registry.is_pending(TICK_KEY):
            return
        self._rolling = False
        self._last_video = None
        self._slot.setStyleSheet(SLOT_IDLE_QSS)
        self._slot_big.setStyleSheet(_slot_text_qss(theme.BAD, 17, 700))
        self._slot_big.setText(text or '抽奖失败')
        self._slot_small.setText('')
        self._draw_btn.setEnabled(True)

    def _maybe_autoplay(self, video: videos.VideoInfo | None) -> bool:
        """抽完自动全屏播放揭晓视频。

        注意两件事（都是实测出来的）：
        1. 播放器**不能**挂在模态弹窗下面——真实桌面上它会被模态窗压在后面，
           用户看到的就是「点了抽奖什么也没发生」。所以播放器做成**独立顶层窗口**
           （parent=None）全屏置顶盖上去；抽奖窗不再隐藏，播完关掉播放器就回来了，
           用户能看到「谁抽中了什么」。
        2. 播放器对象必须留一个强引用，否则 Python 侧一回收，
           解码线程还在跑就会抛 "wrapped C/C++ object has been deleted"。
        """
        if video is None:
            return False
        if os.environ.get('CLASSPOINTS_VIDEO_AUTOPLAY', '1').strip().lower() in {'no', 'false', 'off', '0'}:
            return False
        shell = self.window()
        player = video_widgets.make_player_dialog(
            video.path, None, fullscreen=True, probe=False
        )
        if player is None:
            self._announce('当前环境无法在程序内播放这段视频，结果已揭晓', 'warn')
            return False
        player.playback_finished.connect(self._on_player_finished)
        self._player_dialog = player
        self._player_shell = shell
        # ffmpeg 合成器带全屏入口；不可用（缺 ffmpeg）时退回普通 show + play。
        starter = getattr(player, 'start_fullscreen', None)
        if starter is not None:
            starter()
        else:
            player.show()
            player.raise_()
            player.activateWindow()
        player.play()
        return True

    def _on_player_finished(self) -> None:
        self._restore_shell()
        self._player_dialog = None
        self._reveal_pending()

    def _restore_shell(self) -> None:
        """把播放期间藏起来的抽奖弹窗放回前台。"""
        shell = getattr(self, '_player_shell', None)
        self._player_shell = None
        if shell is None:
            return
        try:
            shell.show()
            shell.raise_()
            shell.activateWindow()
        except Exception:
            pass

    def _close_player(self) -> None:
        player = self._player_dialog
        self._player_dialog = None
        if player is not None:
            try:
                player.close()
            except Exception:
                pass
        self._restore_shell()
        if self._rolling and self._pending_video is not None:
            self._reveal_pending()
            return

    def stop_animation(self) -> None:
        self._registry.cancel_scope(SCOPE)
        self._close_player()
        if self._rolling:
            self._rolling = False
            if self._pending_animation == 'video':
                self._reveal_pending()
                return
            self._show_final(self._pending_final, self._pending_sub)

    def hideEvent(self, event) -> None:
        # 窗口被关掉时：播放器一起收掉，帧动画整批撤销（不留幽灵回调）。
        if self._player_dialog is None:
            self._close_player()
        if self._rolling:
            self._registry.cancel_scope(SCOPE)
            self._rolling = False
        super().hideEvent(event)

    def is_animating(self) -> bool:
        return self._registry.is_pending(TICK_KEY)

    def chip_count(self) -> int:
        return len(self._chips)

    def pool_text(self) -> str:
        return self._pool_label.text()

    def rank_tag_text(self) -> str:
        """顶部「资格：积分前 N 名」胶囊文案；门槛模式下为空串。"""
        return self._rank_tag.text()

    def threshold_visible(self) -> bool:
        """判定分输入框是否可见（排名模式下 False）。"""
        return not self._threshold.isHidden()

    def slot_text(self) -> str:
        return self._slot_big.text()

    def slot_sub_text(self) -> str:
        return self._slot_small.text()

    def last_video(self) -> videos.VideoInfo | None:
        """最近一次「视频揭晓」实际选中的视频（未走视频分支时为 None）。"""
        return self._last_video

    def prize_rows(self) -> dict[str, tuple[QComboBox, QComboBox]]:
        """{prize_id: (动画下拉, 视频下拉)}，供探针/测试检查界面绑定。"""
        return {
            pid: (anim, self._video_combos[pid])
            for pid, anim in self._anim_combos.items()
            if pid in self._video_combos
        }

    def video_manager_open(self) -> bool:
        """打开视频库弹窗（同步，close 后返回）。"""
        self._open_video_manager()
        return True

    def random_names(self) -> str:
        return self._rd_names.text()

    def _announce(self, text: str, kind: str) -> None:
        self.announced.emit(text, kind)


def _slot_text_qss(color: str, size: float, weight: int) -> str:
    return (
        f'color: {color}'
        f'; background: transparent; font-size: {size}'
        f'px; font-weight: {weight};'
    )


def _field_label(text: str, parent: QWidget) -> QLabel:
    lab = QLabel(text, parent)
    lab.setStyleSheet(
        f'color: {theme.INK2}'
        '; background: transparent; font-size: 12px; font-weight: 700;'
    )
    # 标签行盒 16（ground），与弹窗内其它字段一致。
    return lab


def _side_title(text: str, parent: QWidget) -> QLabel:
    lab = QLabel(text, parent)
    lab.setStyleSheet(
        f'color: {theme.INK2}'
        '; background: transparent; font-size: 13px; font-weight: 700;'
    )
    # 侧栏小标题 13px，比字段标签重一档。
    return lab


def _muted(text: str) -> QLabel:
    lab = QLabel(text)
    lab.setWordWrap(True)
    lab.setStyleSheet(
        f'color: {theme.INK3}'
        '; background: transparent; font-size: 12px; padding: 8px;'
    )
    return lab


def _mini_qss() -> str:
    return 'QPushButton { padding: 6px 12px; font-size: 13px; border-radius: 9px; }'


def _combo_qss() -> str:
    return (
        f'QComboBox {{ background: {theme.CARD}; color: {theme.INK};'
        f' border: 1px solid {theme.LINE}; border-radius: 8px; padding: 3px 6px; font-size: 11px; }}'
        f'QComboBox:hover {{ border-color: {theme.BRAND}; }}'
        f'QComboBox:disabled {{ background: {theme.PILL_GRAY_BG}; color: {theme.INK3}; }}'
        f'QComboBox QAbstractItemView {{ background: {theme.CARD}; color: {theme.INK};'
        ' selection-background-color: #eaf0ff; selection-color: #3b5bdb; font-size: 12px; }'
    )


def _elide(text: str, limit: int) -> str:
    text = (text or '').strip()
    if len(text) <= limit:
        return text
    return text[:max(1, limit - 1)] + '…'


def _big_qss(variant: str) -> str:
    bg = theme.BRAND if variant == 'primary' else theme.CARD
    hover = theme.BRAND_DARK if variant == 'primary' else theme.BTN_GHOST_BG
    ink = '#ffffff' if variant == 'primary' else theme.INK
    return (
        'QPushButton { background: '
        f'{bg}'
        '; color: '
        f'{ink}'
        '; border: 1px solid '
        f'{bg}'
        '; border-radius: 12px; padding: 14px 30px; font-size: 17px; font-weight: 700; }'
        'QPushButton:hover { background: '
        f'{hover}'
        '; border-color: '
        f'{hover}'
        '; }QPushButton:disabled { background: '
        f'{theme.PILL_GRAY_BG}'
        '; color: '
        f'{theme.INK3}'
        '; border-color: '
        f'{theme.LINE}'
        '; }'
    )
