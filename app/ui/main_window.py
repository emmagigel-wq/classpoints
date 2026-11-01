"""主窗口：1:1 复刻原版 HTML 的三段式骨架（topbar / board / bottombar）。

原版结构（ui_ref/app.js init，实测 ground-default.json @1440x900）：
  header.topbar  h68   左 brand(logo42 + 班名19/副题12.5) 右 selection胶囊 + 撤销按钮
  main.board     grid 1fr+320 gap16 pad16/20：座位表卡 + 排行榜卡
  footer.bottombar h83 pad 12/20/16 gap10：早读 午读 | 抽奖 随机 | 加分 扣分 | 记录 设置
  #layer 全屏覆盖层（早读/午读舞台）；#toast 底部 96px 居中胶囊。

行为对齐原版（app.js bindSeatMap / bindRank / bindBottom）：
  * 主界面座位：单击=选中（230ms 窗口内第二击=加分弹窗）；右键=扣分弹窗；
    编辑模式点格=改座位；长按=拖动换位的起点。
  * 排行榜：单击=选中，双击=加分弹窗。
  * 记分只刷单元格文字，绝不整表重建（用户报障「闪烁/加载失败」根因之一）。
"""
from __future__ import annotations

import random
import threading
import time
from pathlib import Path

from PyQt5.QtCore import QEvent, QRect, pyqtSignal, Qt
from PyQt5.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from app.core import scoring
from app.core import theme_store
from app.core.state import ChangeEvent, Store
from app.services.autosave import Autosave
from app.services.timer_registry import TimerRegistry
from app.services.sync_server import SyncServer
from app.services.tunnel import TunnelManager
from app.ui import theme
from app.ui.history_view import HistoryView
from app.ui.leaderboard import LeaderboardView
from app.ui.lottery_view import LotteryView
from app.ui.manual_dialog import ManualScoreDialog
from app.ui.seat_map import SeatMap
from app.ui.stage import StageView
from app.ui.widgets.toast import Toast

PAGE_BOARD = 0
PAGE_STAGE = 1

HINT_NORMAL = '单击选中 · 双击加分 · 右键扣分'
HINT_EDITING = '编辑模式：点击可添加 / 改名 / 删除 · 长按座位可拖动换位置'


class _HintOverlay(QFrame):
    """原版 .empty-hint：盖在卡片上（inset 52/0/0），半透明白 + 引导。"""

    def __init__(self, parent: QFrame, on_import) -> None:
        super().__init__(parent)
        self.setObjectName('emptyHint')
        self.setStyleSheet(
            'QFrame#emptyHint { background: rgba(255,255,255,0.86); border: none; }'
        )
        lay = QVBoxLayout(self)
        lay.setSpacing(14)
        lay.setAlignment(Qt.AlignCenter)
        p = QLabel('还没有座位表')
        p.setStyleSheet(f'font-size: 15px; color: {theme.INK2}; background: transparent;')
        p.setAlignment(Qt.AlignCenter)
        btn = QPushButton('导入座位表')
        btn.setObjectName('primary')
        btn.setFixedWidth(120)
        btn.clicked.connect(on_import)
        lay.addWidget(p)
        lay.addWidget(btn, alignment=Qt.AlignCenter)
        self.hide()

    def resizeEvent(self, e) -> None:
        par = self.parentWidget()
        if par is not None:
            self.setGeometry(0, 52, par.width(), max(0, par.height() - 52))
        super().resizeEvent(e)


class _SeatEditDialog(QDialog):
    """编辑模式点座位的小弹窗：改名 / 删除 / 添加（原版 editSeat 的等价物）。"""

    def __init__(
        self, state, student, row: int, col: int, parent=None
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle('编辑座位')
        self.setModal(True)
        self.student = student
        self.delete_requested = False
        self.new_name = ''
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(10)
        title = QLabel(('改名：' if student else '新座位：') + f'第 {row + 1} 排 第 {col + 1} 列')
        title.setStyleSheet(f'font-size: 17px; font-weight: 700; color: {theme.INK};')
        lay.addWidget(title)
        self._edit = QLineEdit(student.name if student else '')
        self._edit.setPlaceholderText('输入姓名，留空=删除（新座位留空=取消）')
        lay.addWidget(self._edit)
        foot = QHBoxLayout()
        foot.setSpacing(10)
        if student:
            bad = QPushButton('删除座位')
            bad.setObjectName('danger')
            bad.clicked.connect(self._do_delete)
            foot.addWidget(bad)
        foot.addStretch(1)
        cancel = QPushButton('取消')
        cancel.setObjectName('ghost')
        cancel.clicked.connect(self.reject)
        ok = QPushButton('确定')
        ok.setObjectName('primary')
        ok.clicked.connect(self._do_ok)
        foot.addWidget(cancel)
        foot.addWidget(ok)
        lay.addLayout(foot)

    def _do_delete(self) -> None:
        self.delete_requested = True
        self.accept()

    def _do_ok(self) -> None:
        self.new_name = self._edit.text().strip()
        self.accept()


class MainWindow(QMainWindow):
    """班级积分系统主窗口（原版 1:1）。"""

    selftest_requested = pyqtSignal()
    sync_changed = pyqtSignal(object)

    activate_requested = pyqtSignal()
    client_synced = pyqtSignal(object)
    tunnel_ready = pyqtSignal(object)
    client_notify = pyqtSignal(object)

    def __init__(self, store: Store, registry: TimerRegistry, parent=None) -> None:
        super().__init__(parent)
        self._store = store
        self._registry = registry
        self._selected = ''
        self._editing = False
        self._drag_source = None

        self._autosave = Autosave(self._store, self._registry, parent=self)

        self._sync = SyncServer(store, on_change=self._emit_sync_change)
        self._tunnel = TunnelManager()

        self._build_ui()
        self._wire()

        try:
            self.on_sync_started()
        except Exception:
            pass

        self._autosave.start()
        self._refresh_all()

    def _init_tray_state(self) -> None:
        """托盘相关的初始状态要在 _build_ui 之前就位（closeEvent 随时可能被调用）。"""
        self._quitting = False
        self._tray_hinted = False
        self._tray = None
        self._client_syncing = False
        self._tunnel_syncing = False
        self._tunnel_notified = False
        self._notif_fetching = False
        self._notif_cache = {}
        self._pending_acks = []
        self._ack_wired = False

    def _client_device_id(self) -> str:
        """客户端身份：从备份恢复过来的第二台机器不会与主机撞号。"""
        import uuid

        st = self._store.state.settings
        did = (getattr(st, 'client_device_id', '') or '').strip()
        if not did:
            did = 'pc-' + uuid.uuid4().hex[:12]
            st.client_device_id = did
            self._store.save_now()
        return did

    def _client(self):
        from app.services.sync_client import SyncClient

        st = self._store.state.settings
        return SyncClient(
            getattr(st, 'sync_client_url', '') or '',
            getattr(st, 'sync_client_token', '') or '',
            self._store,
            pair_token=getattr(st, 'pair_token', '') or '',
            device_id=self._client_device_id(),
        )

    def _maybe_auto_notif(self) -> None:
        """客户端角色每 4 秒向主机要一次通知（网络在后台线程）。"""
        st = self._store.state.settings
        if getattr(st, 'sync_role', 'host') != 'client':
            return
        if not (getattr(st, 'sync_auto', False) and getattr(st, 'sync_client_url', '')):
            return
        if self._notif_fetching:
            return
        self._notif_fetching = True
        client = self._client()

        def work() -> None:
            try:
                self.client_notify.emit(('items', client.notifications()))
            except Exception as exc:
                self.client_notify.emit(('error', str(exc)))
            finally:
                self._notif_fetching = False

        threading.Thread(target=work, daemon=True).start()

    def _on_client_notify(self, payload) -> None:
        kind, data = payload if isinstance(payload, tuple) else ('error', str(payload))
        if kind == 'download':
            self._save_client_file(data)
            return
        if kind == 'download_error':
            self._toast_msg(f'取附件失败：{data}')
            return
        if kind == 'sent':
            if data:
                self._toast_msg(f'通知已发出：{data}')
            else:
                self._toast_msg('通知已交给主机分发', 'ok')
            return
        if kind != 'items' or not data:
            return
        for it in data:
            try:
                self._notif_cache.setdefault(int(it.get('id') or 0), it)
            except (TypeError, ValueError):
                continue
        overlay = self._notify_overlay()
        overlay.present_many(data)

        ids = [int(i.get('id') or 0) for i in data if i.get('id')]
        if not ids:
            return
        client = self._client()
        if client is None:
            return
        self._pending_acks.extend(ids)
        if not getattr(self, '_ack_wired', False):
            self._ack_wired = True
            overlay.acknowledged.connect(self._flush_client_acks)
            return

    def _flush_client_acks(self, _notify_id: int = 0) -> None:
        """把已经展示过（用户看过/点过）的通知回执批量交给主机。"""
        ids = list(dict.fromkeys(self._pending_acks))
        self._pending_acks.clear()
        if not ids:
            return
        client = self._client()
        if client is None:
            return

        def ack() -> None:
            try:
                client.ack(ids)
            except Exception:
                return

        threading.Thread(target=ack, daemon=True).start()

    def _maybe_auto_sync(self) -> None:
        """开着的机器每 2 分钟、启动后 2 秒各同步一次（仅客户端角色且开了自动）。"""
        st = self._store.state.settings
        if getattr(st, 'sync_role', 'host') != 'client':
            return
        if not (getattr(st, 'sync_auto', False) and getattr(st, 'sync_client_url', '')):
            return
        self.run_client_sync()

    def _maybe_auto_tunnel(self) -> None:
        """主机端：启动后自动把同步服务与固定隧道拉起来。

        以前这两步必须进「设置 → 多端同步」手点，用户装完只打开界面，手机就是连不上
        （现场现象：桌面图标点了没反应 / 手机一直转圈）。现在只要「自动开启」勾着、
        固定域名与隧道令牌都填好了，启动 3 秒后就自己在后台起，界面右下角会提示结果。
        """
        st = self._store.state.settings
        if getattr(st, 'sync_role', 'host') != 'host':
            return
        if not getattr(st, 'sync_auto', False):
            return
        if not ((st.tunnel_hostname or '').strip() and (st.tunnel_token or '').strip()):
            return
        if self._tunnel_syncing:
            return
        if getattr(self._tunnel, 'running', False):
            return
        self._tunnel_syncing = True

        def work() -> None:
            try:
                from app.services.sync_server import load_or_create_token
                from app.services.tunnel import hostname_alive

                if hostname_alive(st.tunnel_hostname):
                    self.tunnel_ready.emit(('occupied', st.tunnel_hostname))
                    return
                if not self._sync.running:
                    port = self._sync.start()
                    self._sync.set_token(load_or_create_token())
                else:
                    port = self._sync.port
                url = self._tunnel.start(
                    port,
                    wait_seconds=45.0,
                    token=st.tunnel_token,
                    hostname=st.tunnel_hostname,
                )
                self.tunnel_ready.emit(('ok', url))
            except Exception as exc:
                self.tunnel_ready.emit(('error', str(exc)))
            finally:
                self._tunnel_syncing = False

        threading.Thread(target=work, daemon=True).start()

    def _on_tunnel_ready(self, payload) -> None:
        kind, data = payload if isinstance(payload, tuple) else ('error', str(payload))
        if kind == 'ok':
            if not self._tunnel_notified:
                self._tunnel_notified = True
                self._toast_msg(f'公网已就绪：{data}', 'ok', 4000)
                return
            return
        if kind == 'occupied':
            self._toast_msg(
                f'{data} 已经在别处运行（多半是另一台电脑占着这个域名），本机没有重复启动；如需改由本机提供，请在设置里手动「开启公网」。',
                'warn',
                7000,
            )
            return
        self._toast_msg(f'公网没起来：{data}', 'warn', 5000)

    def run_client_sync(self) -> None:
        """网络在后台线程跑，落库必回主线程 —— 否则跨线程改数据会崩。"""
        if self._client_syncing:
            return
        self._client_syncing = True
        client = self._client()

        def work() -> None:
            try:
                self.client_synced.emit(('plan', client.collect()))
            except Exception as exc:
                self.client_synced.emit(('error', exc))

        threading.Thread(target=work, daemon=True).start()

    def _on_client_plan(self, payload) -> None:
        self._client_syncing = False
        kind, data = payload if isinstance(payload, tuple) else ('error', payload)
        if kind != 'plan':
            return
        try:
            result = self._client().apply(data)
        except Exception:
            return
        self._store.save_now()
        if result.changed:
            self._toast_msg(f'已与主机同步：{result.summary()}', 'ok')
            return

    def _build_ui(self) -> None:
        self._init_tray_state()
        self.resize(1440, 900)
        self.setMinimumSize(960, 620)
        self.setObjectName('main')
        self._retitle()

        central = QWidget(self)
        self._central = central
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._stack = QStackedWidget(central)
        self._stack.addWidget(self._build_board())
        self._stage = StageView(self._store.state, self._registry, central)
        self._stack.addWidget(self._stage)
        root.addWidget(self._stack, 1)
        self.setCentralWidget(central)

        self._toast = Toast(self._registry, central)
        self._toast.hide()
        self._setup_tray()
        try:
            self.apply_app_icon(
                theme_store.resolve(getattr(self._store.state.settings, 'app_icon', '')))
        except Exception:
            import traceback

            traceback.print_exc()

    def _build_board(self) -> QWidget:
        page = QWidget(self)
        col = QVBoxLayout(page)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)

        self._topbar = self._build_topbar(page)
        col.addWidget(self._topbar, 0)
        col.addWidget(self._build_cards(page), 1)
        col.addWidget(self._build_bottombar(page))
        return page

    def _build_topbar(self, parent: QWidget) -> QFrame:
        bar = QFrame(parent)
        bar.setObjectName('topbar')
        bar.setFixedHeight(theme.TOPBAR_H)
        self._topbar = bar
        self._sync_topbar_border()
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(20, 12, 20, 12)
        lay.setSpacing(16)

        brand = QHBoxLayout()
        brand.setSpacing(12)
        logo = QLabel('分')
        logo.setObjectName('logo')
        logo.setFixedSize(42, 42)
        logo.setAlignment(Qt.AlignCenter)
        logo.setStyleSheet(
            'QLabel#logo { background: %s; border-radius: 12px; color: white; font-size: 20px; font-weight: 700; }'
            % theme.GRAD_LOGO
        )
        text = QVBoxLayout()
        text.setSpacing(2)
        self._class_label = QLabel('')
        self._class_label.setObjectName('h1')
        self._class_label.setStyleSheet(
            f'QLabel#h1 {{ background: transparent; color: {theme.INK}; font-size: 19px; font-weight: 700; }}'
        )

        self._class_label.setFixedHeight(25)
        self._class_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self._subtitle = QLabel('')
        self._subtitle.setObjectName('subtitle')
        self._subtitle.setStyleSheet(
            f'QLabel#subtitle {{ background: transparent; color: {theme.INK2}; font-size: 12px; }}'
        )

        self._subtitle.setFixedHeight(16)
        self._subtitle.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        text.addWidget(self._class_label)
        text.addWidget(self._subtitle)
        brand.addWidget(logo)
        brand.addLayout(text)
        lay.addLayout(brand)
        lay.addStretch(1)

        self._selection = QLabel('未选中学生 · 点击座位表选择')
        self._selection.setObjectName('selection')
        self._selection.setStyleSheet(
            f'QLabel#selection {{ background: {theme.PILL_GRAY_BG}; color: {theme.INK3}; border-radius: 999px; padding: 7px 12px; font-size: 13px; }}'
        )

        self._selection.setFixedHeight(31)
        lay.addWidget(self._selection)

        self._btn_undo = QPushButton('撤销上一步')
        self._btn_undo.setObjectName('ghost')
        self._btn_undo.clicked.connect(self._undo_last)
        lay.addWidget(self._btn_undo)
        return bar

    def _build_cards(self, parent: QWidget) -> QWidget:
        board = QWidget(parent)
        board.setStyleSheet('background: transparent;')
        lay = QHBoxLayout(board)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(theme.BOARD_GAP)

        # —— 左卡：座位表 ——
        seat_card = QFrame(board)
        seat_card.setObjectName('card')
        seat_card.setStyleSheet(
            'QFrame#card { background: %s; border: 1px solid %s; border-radius: %dpx; }'
            % (theme.CARD, theme.LINE, theme.RADIUS)
        )
        sc = QVBoxLayout(seat_card)
        sc.setContentsMargins(1, 1, 1, 1)
        sc.setSpacing(0)
        head = QHBoxLayout()
        head.setContentsMargins(16, 12, 16, 12)
        head.setSpacing(8)
        t = QLabel('座位表')
        t.setObjectName('cardTitle')
        # 副题：随编辑模式切换文案（原版 toggleEdit 里的 hint 更新）
        self._seat_hint = QLabel(HINT_NORMAL)
        self._seat_hint.setObjectName('seatHint')
        self._seat_hint.setFixedHeight(16)
        self._seat_hint.setStyleSheet(
            f'QLabel#seatHint {{ background: transparent; color: {theme.INK3}; font-size: 12px; }}'
        )
        self._btn_edit = QPushButton('编辑座位')
        self._btn_edit.setObjectName('tiny')
        self._btn_edit.setCheckable(True)
        self._btn_edit.clicked.connect(self._toggle_editing)
        head.addWidget(t)
        head.addSpacing(8)
        head.addWidget(self._seat_hint)
        head.addStretch(1)
        head.addWidget(self._btn_edit)
        head_frame = QFrame(seat_card)
        hf = QHBoxLayout(head_frame)
        hf.setContentsMargins(0, 0, 0, 0)
        hf.addLayout(head)
        head_frame.setStyleSheet(
            'QFrame { background: transparent; border-bottom: 1px solid %s; }' % theme.LINE
        )
        head_frame.setFixedHeight(theme.CARD_HEAD_SEAT_H)
        sc.addWidget(head_frame)

        host = QWidget(seat_card)
        host.setObjectName('seatmapHost')
        host.setStyleSheet('background: transparent;')
        hl = QVBoxLayout(host)
        hl.setContentsMargins(theme.SEATMAP_PAD, theme.SEATMAP_PAD,
                              theme.SEATMAP_PAD, theme.SEATMAP_PAD)
        self._map = SeatMap(host)
        hl.addWidget(self._map, 1)
        sc.addWidget(host, 1)
        self._empty_hint = _HintOverlay(seat_card, self._open_settings)
        lay.addWidget(seat_card, 1)

        # —— 右卡：排行榜 ——
        rank_card = QFrame(board)
        rank_card.setObjectName('rankCard')
        rank_card.setFixedWidth(theme.RANK_W)
        rank_card.setStyleSheet(
            'QFrame#rankCard { background: %s; border: 1px solid %s; border-radius: %dpx; }'
            % (theme.CARD, theme.LINE, theme.RADIUS)
        )
        rc = QVBoxLayout(rank_card)
        rc.setContentsMargins(1, 1, 1, 1)
        rc.setSpacing(0)
        rhead = QFrame(rank_card)
        rh = QHBoxLayout(rhead)
        rh.setContentsMargins(16, 12, 16, 12)
        rh.setSpacing(8)
        rt = QLabel('排行榜')
        rt.setObjectName('cardTitle')
        self._rank_hint = QLabel('')
        self._rank_hint.setObjectName('rankHint')
        self._rank_hint.setFixedHeight(16)
        self._rank_hint.setStyleSheet(
            f'QLabel#rankHint {{ background: transparent; color: {theme.INK3}; font-size: 12px; }}'
        )
        # 排行榜头部没有编辑按钮（原版如此）
        rh.addWidget(rt)
        rh.addWidget(self._rank_hint)
        rh.addStretch(1)
        rhead.setFixedHeight(theme.CARD_HEAD_RANK_H)
        rhead.setStyleSheet(
            'QFrame { background: transparent; border-bottom: 1px solid %s; }' % theme.LINE
        )
        rc.addWidget(rhead)
        rhost = QWidget(rank_card)
        rhost.setStyleSheet('background: transparent;')
        rl = QVBoxLayout(rhost)
        # 顶部留 8（头部已分割），两侧 10、底部 16：与原版 .rank-card 内边距一致
        rl.setContentsMargins(10, 8, 10, 16)
        rl.setSpacing(0)
        self._board_view = LeaderboardView(self._store, rhost)
        rl.addWidget(self._board_view, 0, Qt.AlignTop)
        rl.addStretch(1)
        rc.addWidget(rhost, 1)
        lay.addWidget(rank_card)
        return board

    def _build_bottombar(self, parent: QWidget) -> QFrame:
        bar = QFrame(parent)
        bar.setObjectName('bottombar')
        bar.setFixedHeight(theme.BOTTOMBAR_H)
        bar.setStyleSheet(
            f'QFrame#bottombar {{ background: {theme.CARD!s}; border: none; border-top: 1px solid {theme.LINE!s}; }}'
        )
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(20, 12, 20, 16)
        lay.setSpacing(10)
        specs = (
            ('早读', 'big', self._open_morning),
            ('午读', 'big', self._open_noon),
            ('抽奖', '', self._open_lottery),
            ('随机', '', self._open_random),
            ('加分', 'ok', lambda: self._manual(1)),
            ('扣分', 'bad', lambda: self._manual(-1)),
            ('记录', '', self._open_records),
            ('设置', '', self._open_settings),
        )
        for text, variant, slot in specs:
            b = QPushButton(text)
            if variant:
                b.setObjectName(variant)
            b.setStyleSheet(theme.bottom_button(variant))
            b.setCursor(Qt.PointingHandCursor)
            b.setMinimumHeight(54)
            b.setSizePolicy(b.sizePolicy().Expanding, b.sizePolicy().Preferred)
            b.clicked.connect(slot)
            lay.addWidget(b, 1)
        return bar

    def _wire(self) -> None:
        self._store.on_change(self._on_change)
        # 跨线程信号：同步结果从后台线程 emit，槽固定在主线程跑
        self.sync_changed.connect(self._on_change)
        self.client_synced.connect(self._on_client_plan)
        self.tunnel_ready.connect(self._on_tunnel_ready)
        self.client_notify.connect(self._on_client_notify)
        self._registry.schedule('client:auto', 2000, self._maybe_auto_sync,
                                scope='client', replace=True)
        self._registry.schedule_repeating('notify:client', 4000,
                                          self._maybe_auto_notif, scope='notify')
        self._registry.schedule('tunnel:auto', 3000, self._maybe_auto_tunnel, scope='tunnel')
        self._registry.schedule_repeating('client:tick', 120000, self._maybe_auto_sync,
                                          scope='client')
        self._map.seat_tapped.connect(self._on_seat_tapped)
        self._map.seat_double.connect(self._on_seat_double)
        self._map.seat_right.connect(lambda sid: self._open_score('minus', sid))
        self._map.seat_long.connect(self._on_seat_long)
        self._map.empty_clicked.connect(self._on_empty_clicked)
        self._map.seat_dropped.connect(self._on_seat_dropped)
        self._board_view.item_clicked.connect(self._select_student)
        self._board_view.item_double.connect(lambda sid: self._open_score('plus', sid))
        self._stage.closed.connect(self._on_stage_closed)
        self._stage.toast_message.connect(self._toast_msg)
        self._stage.voting.applied.connect(
            lambda sid, _d: self._touch('seat', reason='vote', student_ids=[sid]))
        self._stage.voting.undone.connect(
            lambda sid, _d: self._touch('seat', reason='undo', student_ids=[sid]))
        self._autosave.saved.connect(self._on_saved)
        self._warn_broken_pointer()

    def _warn_broken_pointer(self) -> None:
        """数据目录配置坏掉时，必须当场告诉用户。

        否则 current_data_dir() 会静默回落到默认目录：用户看到的是默认目录里的
        旧班级甚至空班级，而他的真实数据其实还在自定义目录里——一次不提示，
        就等于让用户以为「数据没了」。
        """
        from app.core import paths as paths_mod

        try:
            warning = paths_mod.consume_pointer_warning()
        except Exception:
            return
        if not warning:
            return
        self._registry.schedule(
            'warn:data_dir', 900,
            lambda: self._toast_msg(warning, 'warn', 9000),
            scope='global',
        )

    def _emit_sync_change(self, event: ChangeEvent) -> None:
        """HTTP 线程调用的投递口：只发信号，绝不在这里碰任何控件。"""
        self.sync_changed.emit(event)

    def _on_change(self, event: ChangeEvent) -> None:
        st = self._store.state

        # 结构性变更才整表重建；记分只刷单元格
        if event.scope == 'layout' or event.reason in ('layout', 'import', 'replace', 'seat_edit'):
            self._stage.set_state(st)
            self._map.rebuild(st)
        else:
            for sid in (event.student_ids or []):
                self._map.update_seat(st, sid)
            self._map.refresh_all_text(st)
        self._refresh_head(st)
        self._map.set_background(theme_store.resolve(getattr(st, 'background', None)) or None)
        self._board_view.refresh()
        self._empty_hint.setVisible(not st.students)
        for view in (getattr(self, '_history', None), getattr(self, '_lottery', None)):
            if view is None:
                continue
            if not view.isVisible():
                continue
            try:
                view.refresh()
            except Exception:
                import traceback
                traceback.print_exc()

    def _touch(self, scope: str, *, reason: str, student_ids=()) -> None:
        self._store.emit(ChangeEvent(reason=reason, scope=scope,
                                     student_ids=list(student_ids)))

    def _refresh_head(self, st) -> None:
        self._class_label.setText(st.class_name or '我的班级')
        total = sum(s.points for s in st.students)
        avg = total / len(st.students) if st.students else 0
        import datetime as _dt
        today = _dt.date.today().isoformat()
        today_delta = 0
        for h in st.history:
            if _dt.datetime.fromtimestamp(h.ts / 1000.0).date().isoformat() == today:
                today_delta += h.delta
        self._subtitle.setText(
            f'共 {len(st.students)} 人 · 总分 {total} · 人均 {avg:.1f} · 今日 '
            + (f'+{today_delta}' if today_delta > 0 else str(today_delta))
        )
        sel = st.student_by_id(self._selected) if self._selected else None
        if sel:
            self._selection.setText(f'已选中：{sel.name}（{sel.points} 分）')
            self._selection.setStyleSheet(
                'QLabel#selection { background: #eaf0ff; color: %s; border-radius: 999px; padding: 7px 12px; font-size: 13px; font-weight: 700; }'
                % theme.BRAND_DARK
            )
            return
        self._selection.setText('未选中学生 · 点击座位表选择')
        self._selection.setStyleSheet(
            f'QLabel#selection {{ background: {theme.PILL_GRAY_BG!s}; color: {theme.INK3!s}; border-radius: 999px; padding: 7px 12px; font-size: 13px; }}'
        )

    def _refresh_all(self) -> None:
        st = self._store.state
        self._stage.set_state(st)
        self._map.rebuild(st)
        self._map.set_background(theme_store.resolve(getattr(st, 'background', None)) or None)
        self._map.set_selected(self._selected)
        self._board_view.refresh()
        self._refresh_head(st)
        self._empty_hint.setVisible(not st.students)

    def _select_student(self, student_id: str) -> None:
        self._selected = student_id
        self._map.set_selected(student_id)
        self._refresh_head(self._store.state)

    def _on_seat_tapped(self, student_id: str) -> None:
        if self._editing:
            student = self._store.state.student_by_id(student_id)
            if student:
                self._edit_seat_dialog(student, student.row, student.col)
            return
        if self._drag_source and self._drag_source != student_id:
            self._swap_seats(self._drag_source, student_id)
            self._drag_source = None
            return
        self._drag_source = None
        self._select_student(student_id)

    def _on_seat_double(self, student_id: str) -> None:
        """230ms 内第二击 = 加分弹窗（原版 openScore('plus')）。"""
        self._drag_source = None
        self._open_score('plus', student_id)

    def _on_seat_long(self, student_id: str) -> None:
        """长按 = 换位起点（原版拖动换位的等价实现：再点目标格完成交换）。"""
        if self._editing:
            self._drag_source = student_id
            self._toast_msg('已拿起座位，再点一个格子放下', 'warn', 1600)
            return

    def _on_seat_dropped(self, student_id: str, row: int, col: int) -> None:
        """编辑模式下把 A 拖到 B：A 与 B 的学生互换；拖到空格则整体挪过去。"""
        if not self._editing:
            return
        target = next((s for s in self._store.state.students
                       if s.row == row and s.col == col), None)
        self._drag_source = None
        if target is None:
            self._move_seat(student_id, row, col)
            return
        if target.id == student_id:
            return
        self._swap_seats(student_id, target.id)

    def _on_empty_clicked(self, row: int, col: int) -> None:
        if self._editing:
            if self._drag_source:
                self._move_seat(self._drag_source, row, col)
                self._drag_source = None
                return
            self._edit_seat_dialog(None, row, col)

    def _edit_seat_dialog(self, student, row: int, col: int) -> None:
        dlg = _SeatEditDialog(self._store.state, student, row, col, self)
        if dlg.exec_() != QDialog.Accepted:
            return
        if dlg.delete_requested and student:
            scoring.remove_student(self._store.state, student.id)
            if self._selected == student.id:
                self._selected = ''
            self._touch('all', reason='seat_edit')
            return
        name = dlg.new_name
        if student:
            if name:
                if name != student.name:
                    scoring.rename_student(self._store.state, student.id, name)
                    self._touch('seat', reason='seat_edit', student_ids=[student.id])
                    return
                return
            return
        if name:
            scoring.add_student(self._store.state, name, row=row, col=col)
            self._touch('all', reason='seat_edit')

    def _swap_seats(self, id_a: str, id_b: str) -> None:
        if scoring.swap_students(self._store.state, id_a, id_b):
            self._touch('all', reason='seat_edit')
            self._toast_msg('已交换座位', 'ok', 1200)

    def _move_seat(self, student_id: str, row: int, col: int) -> None:
        if scoring.move_student(self._store.state, student_id, row, col):
            self._touch('all', reason='seat_edit')

    def _toggle_editing(self, on: bool | None = None) -> None:
        """切换编辑座位。`on` 省略或给按钮信号里的 checked(False) 时按「取反」处理——
        原版按钮就是个开关；直接 connect 会在每次点击传进 False，导致永远进不了编辑态。"""
        if on is None:
            on = not self._editing
        elif on is False and not self._editing:
            on = True
        self._editing = bool(on)
        self._drag_source = None
        self._btn_edit.setText('完成编辑' if on else '编辑座位')
        self._btn_edit.setProperty('active', 'true' if on else '')
        self._btn_edit.style().polish(self._btn_edit)
        self._map.set_editing(on)
        self._seat_hint.setText(HINT_EDITING if on else HINT_NORMAL)

    def _open_score(self, which: str, student_id: str | None = None) -> None:
        sid = student_id or self._selected
        student = self._store.state.student_by_id(sid) if sid else None
        if student is None:
            # 没选中就按当前列表第一个来（原版行为：弹窗里可以换人）
            students = self._store.state.students
            if not students:
                self._toast_msg('还没有学生，先到「设置 → 导入座位表」添加', 'warn', 1800)
                return
            student = students[0]
        sign = 1 if which == 'plus' else -1
        dlg = ManualScoreDialog(self._store.state, student, sign, self)
        if dlg.exec_() != QDialog.Accepted or dlg.result_entry is None:
            return
        picked = dlg.result_student_id or student.id
        self._touch('seat', reason='manual', student_ids=[picked])
        self._toast_msg(dlg.result_message, 'ok', 1600)

    def _manual(self, sign: int) -> None:
        self._open_score('plus' if sign > 0 else 'minus')

    def _undo_last(self) -> None:
        entry = scoring.undo_global(self._store.state)
        if entry is None:
            self._toast_msg('没有可撤销的记录', 'warn', 1400)
            return
        self._touch('all', reason='undo')
        from app.core.models import source_label
        self._toast_msg(f'已撤销：{entry.student_name} '
                        + (f'+{entry.delta}' if entry.delta > 0 else str(entry.delta))
                        + f'（{source_label(entry.source)}）', 'ok', 1600)

    def _open_morning(self) -> None:
        self._open_stage('morning')

    def _open_noon(self) -> None:
        self._open_stage('noon')

    def _open_stage(self, which: str) -> None:
        from app.services.voting import VoteSource

        source = VoteSource.MORNING if which == 'morning' else VoteSource.NOON
        self._stack.setCurrentIndex(PAGE_STAGE)
        # 全屏进讲台：Windows 上最大化窗口再盖全屏，退出时才能回到原状态
        self._stage_was_max = self.isMaximized()
        self._stage_entered_fullscreen = False
        if QApplication.platformName() != 'offscreen' and self.isVisible():
            self.showFullScreen()
            self._stage_entered_fullscreen = True
        self._stage.open_stage(source)
        self._stage.setFocus()

    def _on_stage_closed(self) -> None:
        self._stack.setCurrentIndex(PAGE_BOARD)
        if getattr(self, '_stage_entered_fullscreen', False):
            self._stage_entered_fullscreen = False
            if getattr(self, '_stage_was_max', False):
                self.showMaximized()
            else:
                self.showNormal()
        self._refresh_all()
        self._save()

    def _panel(self, widget: QWidget, title: str, subtitle: str = '',
               wide: bool = False, width: int | None = None,
               max_h: int | None = None) -> None:
        """把内容组件装进原版模态壳（.modal：520 / .modal.wide：1000）。

        原版 `.modal` 的尺寸来自 app.js 里每处 `U.modal({width: N})`：
        加分/扣分 560、记录 1000、抽奖 900、随机抽人 620、设置 820、奖品 620、编辑座位 420。
        `max_h` 用来压高度（抽奖窗口用户嫌太大，单独限高）。
        """
        from app.ui.modals import ModalShell

        dlg = ModalShell(title, subtitle, self, wide=wide, width=width)
        box = dlg.body_layout()
        widget.setParent(dlg.content_widget())
        widget.setStyleSheet('background: transparent;')
        box.addWidget(widget, 1)
        # 弹窗里嵌内容组件：必须先 show，sizeHint 才有意义
        widget.show()
        if max_h:
            dlg.fit_to_content(max_h=max_h)
        else:
            dlg.fit_to_content()
        try:
            dlg.exec_()
        finally:
            # 内容组件归位，别让它在对话框销毁时被连带删掉
            widget.setParent(self)
            widget.hide()
            dlg.deleteLater()

    def _open_records(self) -> None:
        if not hasattr(self, '_history'):
            self._history = HistoryView(self._store, self)
        # 每次打开重拉数据：历史在别处（导入/撤销）也会变
        self._history.refresh()
        self._panel(self._history, '积分记录',
                    '可按时间看全部记录，也可单独查看某个学生', width=1000)

    def _open_lottery(self) -> None:
        if not hasattr(self, '_lottery'):
            self._lottery = LotteryView(self._store, self._registry, self)
        self._lottery.set_mode('draw')
        # 每次开面板都重置「已抽过」名单：老师一节课抽一轮，下一节课要能重抽
        self._lottery.reset_drawn()
        self._lottery.refresh()
        self._panel(self._lottery, '抽奖', '', width=700, max_h=548)

    def _open_random(self) -> None:
        """原版「随机」：打开同一个面板的「随机抽人」页（不改动积分）。

        以前这里只弹一条 toast，看起来像按钮失灵——原版是 620 宽的弹窗。
        """
        if not hasattr(self, '_lottery'):
            self._lottery = LotteryView(self._store, self._registry, self)
        self._lottery.set_mode('random')
        self._lottery.refresh()
        self._panel(self._lottery, '随机抽人', '等概率抽取，不加减积分', width=620)

    def _open_settings(self) -> None:
        from app.ui.dialogs.settings_dialog import SettingsDialog

        dlg = SettingsDialog(self._store, self, sync=self._sync, tunnel=self._tunnel)
        dlg.changed.connect(lambda: self._touch('settings', reason='settings'))
        # 图标即时生效；通知编辑器把待发内容交回主窗口发送
        icon_sig = getattr(dlg, 'icon_changed', None)
        if icon_sig is not None:
            icon_sig.connect(self.apply_app_icon)
        notify_sig = getattr(dlg, 'notify_requested', None)
        if notify_sig is not None:
            notify_sig.connect(self.send_notification)
        if dlg.exec_() == QDialog.Accepted:
            self._touch('settings', reason='settings')

    def _notify_overlay(self):
        """懒建通知浮层（挂在主窗口上，铺满窗口）。"""
        ov = getattr(self, '_overlay', None)
        if ov is None:
            from app.ui.widgets.notify_overlay import NotifyOverlay

            ov = NotifyOverlay(self, registry=self._registry)
            ov.acknowledged.connect(self._on_notify_ack)
            ov.download_requested.connect(self._on_notify_download)
            self._overlay = ov
        return ov

    def send_notification(self, payload: dict) -> bool:
        """把界面里填的通知投递到本机同步服务的通知存储。"""
        payload = dict(payload or {})
        # 跟随端没有本机存储：直接把通知交给主机代发（后台线程，别卡界面）
        if getattr(self._store.state.settings, 'sync_role', 'host') == 'client':
            client = self._client()

            def send() -> None:
                try:
                    nid = client.send_notification(payload)
                    self.client_notify.emit(('sent', f'编号 {nid}' if nid else ''))
                except Exception as exc:
                    self.client_notify.emit(('error', f'通知没发出去：{exc}'))

            threading.Thread(target=send, daemon=True).start()
            return True
        server = getattr(self, '_sync', None)
        store = getattr(server, 'notifications', None) if server is not None else None
        if store is None:
            self._toast_msg('通知服务未就绪，请先开启同步')
            return False
        import base64
        blob = None
        raw_b64 = payload.get('fileDataB64') or ''
        if raw_b64:
            try:
                blob = base64.b64decode(raw_b64)
            except (ValueError, TypeError):
                blob = None
        targets = payload.get('targets') or ['*']
        item = store.add(sender='host',
                         targets=[str(t) for t in targets] or ['*'],
                         mode=str(payload.get('mode') or 'mild'),
                         title=str(payload.get('title') or '')[:120],
                         text=str(payload.get('text') or '')[:2000],
                         file_name=str(payload.get('fileName') or '')[:120],
                         file_bytes=blob)
        self._toast_msg(f'已发送通知：{item.title or "（无标题）"}')
        return True

    def poll_notifications(self) -> int:
        """拉一次「发给本机」的通知并交给浮层显示。返回新显示条数。"""
        since = getattr(self, '_notify_since', 0)
        try:
            items = self.handle_notifications(since)
        except (OSError, ValueError):
            return 0
        if not items:
            return 0
        self._notify_since = max(int(i.get('id') or 0) for i in items)
        self._notify_overlay().present_many(items)
        return len(items)

    def start_notification_poll(self, interval_ms: int = 4000) -> None:
        """每隔几秒拉一次通知。

        用 TimerRegistry 而不是 QTimer：项目分层测试禁止 ui 层直接建计时器，
        统一走 registry 才能在窗口关闭时被 shutdown() 干净收掉。
        """
        self._notify_poll_ms = max(1000, int(interval_ms))

        def tick() -> None:
            self.poll_notifications()
            self._registry.schedule('notify:poll', self._notify_poll_ms, tick,
                                    scope='global', replace=True)

        self._registry.schedule('notify:poll', self._notify_poll_ms, tick,
                                scope='global', replace=True)

    def on_sync_started(self) -> None:
        """同步服务启动后拉起通知轮询（多处都会 start 同步，所以幂等）。"""
        if getattr(self, '_notify_poll_ms', 0):
            return
        self.start_notification_poll()

    def handle_notifications(self, since: int = 0) -> list[dict]:
        """从同步服务的通知存储里取「发给本机」的通知。

        本机在星座里的身份用 settings.device_id；主机自己发的通知不回弹，
        NotifyStore 里已经有这个规则，这里只负责按 id 增量取。
        """
        server = getattr(self, '_sync', None)
        store = getattr(server, 'notifications', None) if server is not None else None
        if store is None:
            return []
        did = ''
        try:
            did, _ = self._store.state.settings.ensure_device_identity()
        except (AttributeError, TypeError):
            did = ''
        items = store.pending(did or 'host', int(since or 0))
        return [i.to_dict() for i in items]

    def _notif_client(self):
        """跟随端：返回指向主机的 SyncClient；本机是主机则返回 None。"""
        st = self._store.state
        if getattr(st.settings, 'sync_role', 'host') != 'client':
            return None
        if not getattr(st.settings, 'sync_client_url', ''):
            pass
            return None
        try:
            return self._client()
        except (AttributeError, TypeError, ValueError):
            return None

    def _on_notify_ack(self, notify_id: int) -> None:
        client = self._notif_client()
        if client is not None:
            self._send_ack_in_background(client, int(notify_id))
            return
        server = getattr(self, '_sync', None)
        store = getattr(server, 'notifications', None) if server is not None else None
        if store is None:
            return
        did = 'host'
        try:
            did, _ = self._store.state.settings.ensure_device_identity()
        except (AttributeError, TypeError):
            pass
        store.ack(did, [int(notify_id)])

    def _send_ack_in_background(self, client, notify_id: int) -> None:
        import threading

        def work() -> None:
            try:
                client.ack([int(notify_id)])
            except Exception as exc:
                self.client_notify.emit(('error', str(exc)))

        threading.Thread(target=work, daemon=True).start()

    def _on_notify_download(self, notify_id: int) -> None:
        from PyQt5.QtWidgets import QFileDialog

        # 跟随端：附件在主机上，走主机下载通道
        if self._notif_client() is not None:
            self._download_client_file(notify_id)
            return
        server = getattr(self, '_sync', None)
        store = getattr(server, 'notifications', None) if server is not None else None
        if store is None:
            return
        target = None
        for it in store.as_list():
            if int(it.get('id') or 0) == int(notify_id):
                target = it
                break
        if not target or not target.get('fileId'):
            self._toast_msg('这条通知没有附件')
            return
        found = store.find_file(target['fileId'])
        if found is None:
            self._toast_msg('附件已不存在')
            return
        src, name = found
        path, _ = QFileDialog.getSaveFileName(self, '保存附件', name or '附件')
        if not path:
            return
        try:
            with open(src, 'rb') as fh:
                blob = fh.read()
            with open(path, 'wb') as fh:
                fh.write(blob)
            self._toast_msg(f'附件已保存：{path}')
        except OSError as exc:
            self._toast_msg(f'保存失败：{exc}')

    def _download_client_file(self, notify_id: int) -> None:
        """跟随端：附件存在主机上，先按 file_id 从主机取回字节再落盘。"""
        from PyQt5.QtWidgets import QFileDialog

        client = self._notif_client()
        if client is None:
            self._toast_msg('这条通知没有附件')
            return
        target = (self._notif_cache or {}).get(int(notify_id)) or {}
        file_id = str(target.get('fileId') or '')
        if not file_id:
            self._toast_msg('这条通知没有附件')
            return
        name = str(target.get('fileName') or '') or '附件'
        path, _ = QFileDialog.getSaveFileName(self, '保存附件', name)
        if not path:
            return
        import threading

        dest = path

        def work() -> None:
            try:
                blob = client.fetch_file(file_id)
            except Exception as exc:
                self.client_notify.emit(('download_error', str(exc)))
                return
            # 回主线程落盘（QFileDialog 选好的目标路径随载荷一起带回）
            self.client_notify.emit(('download', (dest, blob)))

        threading.Thread(target=work, daemon=True).start()

    def _save_client_file(self, payload) -> None:
        """主线程落盘：后台取回的字节写进用户选的位置。"""
        from pathlib import Path

        try:
            dest, blob = payload
            Path(dest).write_bytes(blob)
        except Exception as exc:
            self._toast_msg(f'保存附件失败：{exc}')
            return
        self._toast_msg('附件已保存', 'ok')

    def apply_app_icon(self, path: str | None = None) -> bool:
        """把用户选的 .ico 应用到窗口/托盘（任务栏会跟着窗口图标变）。

        路径为空或文件不存在时回退到程序自带图标——绝不能因为图标坏了
        让窗口没有图标（Windows 上会显示成默认的空白方块）。
        """
        from PyQt5.QtGui import QIcon
        from PyQt5.QtWidgets import QApplication

        icon = QIcon()
        if path:
            try:
                if Path(path).is_file():
                    icon = QIcon(str(path))
            except OSError:
                icon = QIcon()
        if icon.isNull():
            icon = (self.style().standardIcon(self.style().SP_ComputerIcon)
                    if self.windowIcon().isNull() else self.windowIcon())
        self.setWindowIcon(icon)
        app = QApplication.instance()
        if app is not None:
            app.setWindowIcon(icon)
        tray = getattr(self, '_tray', None)
        if tray is not None:
            try:
                tray.setIcon(icon)
            except Exception:
                pass
        return not icon.isNull()

    def _open_layout(self) -> None:
        from app.ui.dialogs.layout_dialog import LayoutDialog

        dlg = LayoutDialog(self._store, self)
        dlg.changed.connect(lambda: self._touch('layout', reason='layout'))
        dlg.exec_()

    def _open_prizes(self) -> None:
        from app.ui.dialogs.prize_dialog import PrizeDialog

        dlg = PrizeDialog(self._store, self)
        dlg.changed.connect(lambda: self._touch('lottery', reason='prizes'))
        dlg.exec_()

    def _open_io(self) -> None:
        from app.ui.dialogs.io_dialog import ImportExportDialog

        dlg = ImportExportDialog(self._store, self)
        dlg.exec_()
        self._touch('all', reason='import')
        self._refresh_all()

    def settings_action(self, action: str) -> None:
        """设置弹窗里其余分区按钮的落点（原版这些按钮都在同一个设置弹窗里）。

        这里不改界面，只把动作接到既有的对话框/流程上；视频揭晓本版本不支持，
        明确提示而不是静默失败。
        """
        if action == 'edit_prizes':
            self._open_prizes()
            return
        if action == 'pick_video':
            from app.ui.widgets.video_picker import VideoManageDialog

            VideoManageDialog(self).exec_()
            if getattr(self, '_lottery', None) is not None:
                self._lottery.refresh()
            return
        if action == 'clear_video':
            from PyQt5.QtWidgets import QMessageBox
            from app.core import videos as video_store

            items = video_store.list_videos()
            if not items:
                self._toast_msg('视频库本来就是空的', 'warn', 1600)
                return
            answer = QMessageBox.question(
                self, '清空视频库',
                '确定从本机删除 %d 段抽奖视频？\n奖项绑定会回到「随机视频」。' % len(items))
            if answer != QMessageBox.Yes:
                return
            stuck = [info for info in items if not video_store.remove_video(info.id)]
            self._toast_msg('已清空视频库' if not stuck
                            else '有 %d 个文件删不掉（可能正被占用）' % len(stuck),
                            'ok' if not stuck else 'warn', 2200)
            if getattr(self, '_lottery', None) is not None:
                self._lottery.refresh()
            return
        from app.ui.dialogs.io_dialog import ImportExportDialog

        routes = {
            'import_paste': '_import_students',
            'import_file': '_import_students',
            'export_xlsx': '_export_xlsx',
            'export_docx': '_export_docx',
            'export_csv': '_export_history_csv',
            'rank_xlsx': '_export_xlsx',
            'rank_docx': '_export_docx',
            'rank_csv': '_export_students_csv',
            'backup_export': '_export_backup',
            'backup_import': '_restore',
        }
        method = routes.get(action)
        if method is not None:
            dlg = ImportExportDialog(self._store, self)
            handler = getattr(dlg, method, None)
            if callable(handler):
                handler()
            else:
                dlg.exec_()
            self._touch('all', reason=action)
            self._refresh_all()
            return
        if action == 'clear_background':
            self._clear_background()
            return
        if action == 'import_photo':
            self._import_background()
            return
        self._toast_msg(f'该设置项在当前版本暂无对应操作：{action}', 'warn', 2200)

    def _export_backup(self) -> None:
        """导出备份：让用户自己选存哪儿（原版这里是直接落到应用目录，用户找不到）。"""
        from PyQt5.QtWidgets import QFileDialog
        from app.io import backup as backup_mod

        default = f'班级积分备份-{time.strftime("%Y%m%d-%H%M%S")}.zip'
        path, _ = QFileDialog.getSaveFileName(
            self, '导出备份', default, '备份包 (*.zip)')
        if not path:
            return
        settings = getattr(self._store.state, 'settings', None)
        with_media = bool(getattr(settings, 'backup_include_media', False))
        with_theme = bool(getattr(settings, 'backup_include_theme', False))
        result = backup_mod.create_backup(self._store.state,
                                          archive_dir=str(Path(path).parent),
                                          include_media=with_media, include_theme=with_theme)
        if not result.ok or not result.path:
            self._toast_msg(f'备份失败：{getattr(result, "error", "")}', 'bad', 2600)
            return
        try:
            Path(path).write_bytes(Path(result.path).read_bytes())
            Path(result.path).unlink(missing_ok=True)
        except OSError as exc:
            self._toast_msg(f'备份写入失败：{exc}', 'bad', 2600)
            return
        # 成功提示里带上打包内容，让用户知道体积大的原因
        extras = []
        if with_media:
            extras.append('抽奖视频')
        if with_theme:
            extras.append('主题文件')
        if extras:
            self._toast_msg('备份已导出（含' + '、'.join(extras) + '）', 'ok', 2200)
        self._toast_msg(f'已导出备份：{Path(path).name}', 'ok', 2600)

    def _import_background(self) -> None:
        """选一张图片当座位表底图（只存路径，不内联字节——存档别被图片撑爆）。"""
        from PyQt5.QtWidgets import QFileDialog

        path, _ = QFileDialog.getOpenFileName(
            self, '选择座位表照片', '',
            '图片 (*.png *.jpg *.jpeg *.bmp *.webp)')
        if not path:
            return
        self._store.state.background = path
        self._touch('all', reason='import_background')
        self._refresh_all()
        self._toast_msg('已设置座位表底图（会从座位缝里透出来）', 'ok', 1800)

    def _clear_background(self) -> None:
        """清掉座位表底图（`AppState.background` 存的是图片路径，不内联字节）。"""
        if not getattr(self._store.state, 'background', None):
            self._toast_msg('当前没有设置底图', 'warn', 1600)
            return
        self._store.state.background = None
        self._touch('all', reason='clear_background')
        self._refresh_all()
        self._toast_msg('已清除座位表底图', 'ok', 1600)

    def _run_selftest(self) -> None:
        self.selftest_requested.emit()

    def _toast_msg(self, text: str, kind: str = '', ms: int = 2200) -> None:
        try:
            self._toast.show_message(text, ms, kind=kind or '')
        except TypeError:
            self._toast.show_message(text, ms)
        self._reposition_toast()

    def _reposition_toast(self) -> None:
        """把提示条摆到底部居中。

        注意顺序：必须先 activate() 让里面的胶囊量出真实尺寸，再 adjustSize()。
        早先直接 adjustSize()，布局还没跑完，取到的是 5×20 这种未定尺寸，
        提示条就贴在错位置上（表现为「操作后下方提示异常/位置乱跳」）。
        """
        c = self._central
        lay = self._toast.layout()
        if lay is not None:
            lay.activate()
        hint = lay.sizeHint() if lay is not None else self._toast.sizeHint()
        self._toast.resize(max(1, hint.width()), max(1, hint.height()))
        if lay is not None:
            lay.activate()
        sz = self._toast.size()
        self._toast.move(max(0, (c.width() - sz.width()) // 2),
                         max(0, c.height() - 96 - sz.height()))
        self._toast.raise_()

    def _sync_topbar_border(self) -> None:
        """窗口最大化/全屏时去掉顶栏的 1px 分隔线。

        窗口贴边时那条线会贴着屏幕边缘，看着像「界面没铺满」的一道缝（用户报障）。
        """
        bar = getattr(self, '_topbar', None)
        if bar is None:
            return
        edge = '' if self.isMaximized() or self.isFullScreen() else 'border-bottom: 1px solid %s;' % theme.LINE
        bar.setStyleSheet(
            f'QFrame#topbar {{ background: {theme.CARD!s}; border: none; {edge!s} }}'
        )

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.WindowStateChange:
            self._sync_topbar_border()

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        if hasattr(self, '_toast'):
            self._reposition_toast()
        self._sync_topbar_border()

    def _save(self) -> None:
        self._autosave.save_if_dirty()

    def _on_saved(self, ok: bool, message: str) -> None:
        if not ok:
            self._toast_msg(f'未保存：{message}', 'bad', 2600)

    def _retitle(self) -> None:
        try:
            from app.version import __version__

            self.setWindowTitle(f'班级积分系统 · v{__version__}')
        except Exception:
            self.setWindowTitle('班级积分系统')

    def closeEvent(self, event) -> None:
        if not self._quitting and self._tray_ready():
            event.ignore()
            self._hide_to_tray()
            return
        if self._stage.source is not None:
            try:
                self._stage.close_stage()
            except Exception:
                pass
        for step in (
            lambda: self._sync.stop(),
            lambda: self._tunnel.stop(),
            lambda: self._autosave.save_if_dirty(),
            lambda: self._autosave.stop(),
            lambda: self._registry.shutdown(),
        ):
            try:
                step()
            except Exception:
                pass
        super().closeEvent(event)

    def _tray_ready(self) -> bool:
        """托盘可用吗？不可用时 closeEvent 必须走正常关闭流程。"""
        tray = getattr(self, '_tray', None)
        if tray is None:
            return False
        try:
            from PyQt5.QtWidgets import QSystemTrayIcon

            return bool(QSystemTrayIcon.isSystemTrayAvailable())
        except Exception:
            return False

    def _setup_tray(self) -> None:
        """建系统托盘：关闭窗口后仍能在后台同步/计分，从托盘一键唤回。"""
        from PyQt5.QtWidgets import QMenu, QSystemTrayIcon

        if not QSystemTrayIcon.isSystemTrayAvailable():
            self._tray = None
            return
        tray = QSystemTrayIcon(self.windowIcon(), self)
        tray.setToolTip('班级积分系统')
        menu = QMenu(self)
        act_show = menu.addAction('显示主界面')
        act_show.triggered.connect(self._restore_from_tray)
        menu.addSeparator()
        act_quit = menu.addAction('退出')
        act_quit.triggered.connect(self._quit_app)
        tray.setContextMenu(menu)
        tray.activated.connect(self._on_tray_activated)
        tray.show()
        self._tray = tray

    def _on_tray_activated(self, reason) -> None:
        from PyQt5.QtWidgets import QSystemTrayIcon

        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self._restore_from_tray()

    def activate_from_other_instance(self) -> None:
        """第二个实例点桌面图标：把已有窗口叫回前台。

        三种状态都要处理：藏在托盘里、最小化了、被别的窗口盖住了。
        """
        try:
            self._quitting = False
            if self.isHidden() or not self.isVisible():
                self.show()
            if self.isMinimized():
                self.showNormal()
            self.raise_()
            self.activateWindow()
            self.setWindowState(self.windowState() | Qt.WindowActive)
            tray = getattr(self, '_tray', None)
            if tray is not None:
                try:
                    tray.hide()
                    return
                except Exception:
                    pass
        except Exception:
            import traceback

            traceback.print_exc()

    def _restore_from_tray(self) -> None:
        if self.isMinimized():
            self.showNormal()
        self.show()
        self.raise_()
        self.activateWindow()

    def _hide_to_tray(self) -> None:
        tray = getattr(self, '_tray', None)
        self.hide()
        if tray is not None and not getattr(self, '_tray_hinted', False):
            self._tray_hinted = True
            try:
                tray.showMessage(
                    '班级积分系统',
                    '已最小化到托盘，程序仍在后台运行；右键托盘图标可以退出。',
                    tray.Information, 3000)
            except Exception:
                pass

    def _quit_app(self) -> None:
        self._quitting = True
        tray = getattr(self, '_tray', None)
        if tray is not None:
            tray.hide()
        from PyQt5.QtWidgets import QApplication

        self.close()
        QApplication.instance().quit()

    @property
    def store(self) -> Store:
        return self._store

    @property
    def stage(self) -> StageView:
        return self._stage

    @property
    def seat_map(self) -> SeatMap:
        return self._map
