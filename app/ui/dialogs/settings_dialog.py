"""设置弹窗：原版 `openSettings()` 的 1:1 复刻（app.js:3517-3645）。

原版是**一个**宽 820 的设置弹窗，里面 7 个 `.set-sec` 分区：
    班级与座位 / 导入座位表 / 抽奖奖品与权重 / 抽奖动画视频 /
    导出积分记录（文档）/ 一键导出排行榜表格 / 数据备份
本文件按原版 DOM 顺序与 CSS 逐条还原；多端同步是本次新增能力，作为第 8 个分区追加。

本次改版把「一根长滚动列」换成「左侧导航 + 右侧单页」：左导航见
`app/ui/dialogs/settings_nav.py`，右侧每个分区一个 QScrollArea（切页各自回顶部），
外框仍是 820×852、`.set-sec` 的控件 / objectName / 信号 / 逻辑全部不变。

之后又补了两项（导航从 8 项扩到 9 项）：
    1) 「班级与座位」页底部追加「数据目录」分区（`_build_dirs_section`）：
       显示 / 更改数据目录，调 `app/core/paths.py` 的 API，改完提示重启；
    2) 新增第 9 页「关于软件」（`_build_about_section`，元信息见
       `app/ui/dialogs/about_dialog.py`，版本号只读 `app.version.__version__`）。

关键数（ground-settings.json 实测，modal 820x852 @1440x900）：
    .modal-head  820x55（只有标题，无副题）
    .modal-body  820x797，内容 `.settings` 769x1798.5（可滚动）
    .set-sec     宽 769，border 1 LINE，r12，padding 14/16；相邻间距 18
    h4           14px 700，margin 0 0 10；行盒 19
    .grid-form   3 列 minmax(180,1fr)，gap 12
    .field       竖排 gap 6（标签 16 + 输入 39 = 61）
    .group-editor margin-top 14，padding 12/14，底 #f9fafb，r12
    .tip         12.5px INK3，margin 8 0 10
    .row-btns    flex-wrap gap 10，margin-top 10
    .btn.mini    13px，padding 6/12，r9 → 高 31
    .btn.tiny    12px，padding 4/9，r7 → 高 26
"""

from __future__ import annotations

from pathlib import Path

from PyQt5.QtCore import QRect, Qt, QThread, QUrl, pyqtSignal
from PyQt5.QtGui import QColor, QDesktopServices, QIcon, QImage, QPainter, QPixmap
from PyQt5.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QStackedWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from app.core.models import ANIMATIONS, normalize_lottery_mode
from app.services import qr as qr_mod


def apply_clear_lottery(store) -> int:
    """清空抽奖记录，返回被清掉的条数（学生、积分、奖品都不动）。"""
    from app.core import lottery as lottery_mod

    removed = lottery_mod.clear_records(store.state)
    if removed:
        store.save_now()
    return removed


def apply_clear_records(store) -> int:
    """清空积分记录（保留座次表），返回被清掉的条数。

    真正的活儿在 `scoring.clear_history` —— 界面层不许直接改 .history/.points，
    这条规则由 tests/test_layering.py 强制。
    """
    removed = scoring.clear_history(store.state)
    store.save_now()
    return removed


from app.services import tunnel as tunnel_mod
from app.core import theme_store
from app.core import pairing as pairing_mod
from app.services import sync_client as client_mod
from app.core import scoring
from app.services.sync_server import SyncServer
from app.ui import theme
from app.ui.dialogs import about_dialog as about_mod
from app.ui.dialogs import app_icon as app_icon_mod
from app.ui.dialogs.settings_nav import SettingsNav
from app.ui.modals import ModalShell
from app.ui.widgets.flow_layout import FlowLayout

WIDTH = 820
SEC_GAP = 18
SEC_PAD = (14, 16, 14, 16)
H4_H = 19
H4_GAP = 10
ROW_BTNS_GAP = 10
FIELD_GAP = 6
LABEL_H = 16
INPUT_H = 39
GRID_GAP = 12
CONTENT_W = WIDTH - 36 - 15
QR_PX = 132

ANIMATION_LABELS = {
    "none": "不做动画",
    "roll": "滚动揭晓",
    "cards": "卡片翻牌",
    "wheel": "转盘",
    "video": "视频揭晓",
}

PAGE_QSS = """
QScrollArea#setPage { background: transparent; border: none; }
QScrollArea#setPage > QWidget > QWidget { background: transparent; }
"""

NAV_GAP = 14
DIR_TEXT_W = 470
__version__ = "2.4.0"


def _paths_mod():
    """数据目录 API（`app/core/paths.py`）。

    Lead 正在并行落盘这个模块；它还没出现时返回 None，界面退化成
    「数据目录接口尚未就绪」，不要因为 ImportError 把整个设置弹窗拖崩。
    """
    try:
        from app.core import paths as paths_module

        return paths_module
    except ImportError:
        return None


def _label(
    text: str,
    parent: QWidget,
    *,
    size: float = 12,
    weight: int = 600,
    color: str | None = None,
    height: int = LABEL_H,
) -> QLabel:
    lb = QLabel(text, parent)
    lb.setStyleSheet(
        f"QLabel {{ color: {color or theme.INK2}; font-size: {size}px; font-weight: {weight}"
        f"; background: transparent; }}"
    )
    if height:
        lb.setFixedHeight(height)
    return lb


def _tip(text: str, parent: QWidget) -> QLabel:
    """`.tip`：12.5px INK3，行高 1.7，可换行。"""
    lb = QLabel(text, parent)
    lb.setWordWrap(True)
    lb.setStyleSheet(
        f"QLabel {{ color: {theme.INK3}; font-size: 12px; background: transparent; }}"
    )
    return lb


def _input_qss() -> str:
    return (
        "QLineEdit, QSpinBox, QComboBox { background: "
        f"{theme.CARD}; border: 1px solid {theme.LINE}"
        f"; border-radius: 9px; padding: 0px 11px; font-size: 13px; color: "
        f"{theme.INK}; }}QLineEdit:focus, QSpinBox:focus, QComboBox:focus {{ border-color: "
        f"{theme.BRAND}; }}QComboBox::drop-down {{ border: none; width: 18px; }}QComboBox QAbstractItemView {{ background: "
        f"{theme.CARD}; border: 1px solid "
        f"{theme.LINE}; border-radius: 8px; }}QSpinBox::up-button, QSpinBox::down-button {{ width: 0px; height: 0px; border: none; background: transparent; }}"
    )


MINI_QSS = f"""
QPushButton {{
    background: {theme.CARD}; border: 1px solid {theme.LINE};
    border-radius: 9px; padding: 6px 12px; font-size: 13px;
    font-weight: 700; color: {theme.INK};
}}
QPushButton:hover {{ background: {theme.BTN_GHOST_BG}; }}
QPushButton#primary {{
    background: {theme.BRAND}; border-color: {theme.BRAND}; color: #ffffff;
}}
QPushButton#primary:hover {{
    background: {theme.BRAND_DARK}; border-color: {theme.BRAND_DARK};
}}
QPushButton#danger {{ color: {theme.BAD}; border-color: #fbd3d0; }}
QPushButton#danger:hover {{ background: #feeceb; }}
"""

TINY_QSS = f"""
QPushButton {{
    background: {theme.CARD}; border: 1px solid {theme.LINE};
    border-radius: 7px; padding: 4px 9px; font-size: 12px;
    font-weight: 700; color: {theme.INK2};
}}
QPushButton:hover {{ background: {theme.PILL_GRAY_BG}; color: {theme.INK}; }}
"""

MODE_QSS = MINI_QSS + f"""
QPushButton:checked {{
    background: {theme.SEL_ACTIVE_BG}; border-color: {theme.BRAND};
    color: {theme.BRAND_DARK};
}}
QPushButton:checked:hover {{
    background: {theme.SEL_ACTIVE_BG}; border-color: {theme.BRAND_DARK};
}}
"""


def _field(text: str, parent: QWidget) -> tuple[QWidget, QVBoxLayout]:
    """`.field`：竖排 标签 + 输入框；返回 (容器, 布局)。"""
    wrap = QWidget(parent)
    wrap.setStyleSheet("background: transparent;")
    lay = QVBoxLayout(wrap)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(FIELD_GAP)
    lay.addWidget(_label(text, wrap))
    return wrap, lay


def _inline_field(text: str, parent: QWidget, widget: QWidget) -> QWidget:
    """`.field.inline`：标签与控件同一行，gap 6。"""
    wrap = QWidget(parent)
    wrap.setStyleSheet("background: transparent;")
    lay = QHBoxLayout(wrap)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(6)
    lay.addWidget(_label(text, wrap, height=0))
    lay.addWidget(widget)
    return wrap


def _row_btns(parent: QWidget) -> tuple[QWidget, FlowLayout]:
    """`.row-btns`：横向换行、gap 10、margin-top 10。"""
    wrap = QWidget(parent)
    wrap.setStyleSheet("background: transparent;")
    flow = FlowLayout(
        wrap, h_spacing=ROW_BTNS_GAP, v_spacing=ROW_BTNS_GAP
    )
    flow.setContentsMargins(0, 0, 0, 0)
    return wrap, flow


def _mini(
    text: str, parent: QWidget, *, primary: bool = False, danger: bool = False
) -> QPushButton:
    btn = QPushButton(text, parent)
    btn.setCursor(Qt.PointingHandCursor)
    btn.setFixedHeight(31)
    if primary:
        btn.setObjectName("primary")
    elif danger:
        btn.setObjectName("danger")
    btn.setStyleSheet(MINI_QSS)
    return btn


def _tiny(text: str, parent: QWidget) -> QPushButton:
    btn = QPushButton(text, parent)
    btn.setCursor(Qt.PointingHandCursor)
    btn.setFixedHeight(26)
    btn.setStyleSheet(TINY_QSS)
    return btn


class _Section(QFrame):
    """`.set-sec`：边框圆角容器，内含 h4 + 若干块。"""

    def __init__(self, title: str, parent: QWidget, name: str = "setSec") -> None:
        super().__init__(parent)
        self.setObjectName(name)
        self.setStyleSheet(
            f"QFrame#{name} {{ background: {theme.CARD}"
            f"; border: 1px solid {theme.LINE}"
            "; border-radius: 12px; }"
        )
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(*_ltr(SEC_PAD))
        self.lay.setSpacing(0)
        head = _label(
            title, self, size=14, weight=700, color=theme.INK, height=H4_H
        )
        self.lay.addWidget(head)
        self.lay.addSpacing(H4_GAP)
        self._filled = False

    def add(self, widget: QWidget, gap: int = 0) -> QWidget:
        if self._filled and gap:
            self.lay.addSpacing(gap)
        self.lay.addWidget(widget)
        self._filled = True
        return widget


def _ltr(pad: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    top, right, bottom, left = pad
    return left, top, right, bottom


class _BgTask(QThread):
    """把一个会阻塞的操作（起隧道/下载）丢到后台线程，别卡住界面。

    这里**故意不给 parent**：以前 `_BgTask(fn, self)` 把线程挂在设置弹窗下面，
    用户点完「生成公网地址」（10～30 秒）或「下载穿透组件」（~30MB）后随手关掉
    弹窗，Qt 销毁弹窗时就会连带销毁仍在运行中的 QThread —— 那是 qFatal，
    进程当场 abort：没有 traceback、没有 crash.log，用户看到的就是「闪退」。
    """

    done = pyqtSignal(bool, str)

    def __init__(self, fn) -> None:
        super().__init__()
        self._fn = fn

    def run(self) -> None:
        try:
            out = self._fn()
        except Exception as exc:
            self.done.emit(False, str(exc))
            return
        self.done.emit(True, out if isinstance(out, str) else "")


_RUNNING_TASKS: set["_BgTask"] = set()


def wait_background_tasks(timeout_ms: int = 1500) -> None:
    """退出前把后台线程收干净，避免 Qt 报 QThread 被销毁时仍在运行。"""
    for task in list(_RUNNING_TASKS):
        try:
            if task.isRunning():
                if not task.wait(timeout_ms):
                    task.terminate()
                    task.wait(800)
        except RuntimeError:
            pass
        _RUNNING_TASKS.discard(task)


class SettingsDialog(ModalShell):
    """改全局设置：班级 / 座位 / 计分 / 奖品 / 导入导出 / 备份。"""

    changed = pyqtSignal()
    # 主题页换程序图标：主窗口收到后刷新任务栏/窗口图标
    icon_changed = pyqtSignal(str)
    # 「发送通知」：弹窗只发信号，投递由主窗口交给同步服务
    notify_requested = pyqtSignal(dict)

    def __init__(
        self,
        store,
        parent: QWidget | None = None,
        sync: SyncServer | None = None,
        tunnel=None,
    ) -> None:
        self._store = store
        self._sync = sync
        self._tunnel = tunnel
        self._tunnel_error = ""
        self._tasks = []
        super().__init__("设置", "", parent, width=WIDTH)
        self._build()
        self._load()
        from PyQt5.QtWidgets import QApplication

        screen = QApplication.primaryScreen()
        avail = screen.availableGeometry().height() if screen else 900
        parent_h = parent.height() if parent is not None else 0
        if parent_h > 320:
            avail = parent_h
        self.setFixedHeight(min(852, max(320, avail - 48)))

    def _build(self) -> None:
        body = self.body_layout()
        body.setSpacing(0)
        body.setContentsMargins(*_ltr((16, 18, 16, 18)))

        self._sections = []
        self._page_boxes = []
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(NAV_GAP)
        self._nav = SettingsNav(self.content_widget())
        self._nav.selected.connect(self._select_section)
        row.addWidget(self._nav, 0)
        self._pages = QStackedWidget(self.content_widget())
        self._pages.setStyleSheet("background: transparent;")
        row.addWidget(self._pages, 1)
        body.addLayout(row)

        self._build_class_section()
        self._build_import_section()
        self._build_prize_section()
        self._build_video_section()
        self._build_export_section()
        self._build_rank_export_section()
        self._build_backup_section()
        self._build_sync_section()
        self._build_about_section()
        self._build_theme_section()
        self._select_section(0)

    def _add_page(self, sec: _Section, label: str) -> None:
        """把一个分区装进右侧单页滚动区，并在左导航加一项。"""
        page = QScrollArea(self._pages)
        page.setObjectName("setPage")
        page.setWidgetResizable(True)
        page.setFrameShape(QFrame.NoFrame)
        page.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        page.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        page.setStyleSheet(PAGE_QSS)
        inner = QWidget(page)
        inner.setStyleSheet("background: transparent;")
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(sec)
        lay.addStretch(1)
        page.setWidget(inner)
        self._pages.addWidget(page)
        self._nav.add_item(label)
        self._sections.append(sec)
        self._page_boxes.append(lay)

    def _append_to_page(self, index: int, sec: _Section, gap: int = SEC_GAP) -> None:
        """往已有分页的底部追加一个分区（第 1 页：班级与座位 + 数据目录）。

        布局最后一项是 addStretch，所以要把新控件插在它**前面**，
        否则分区会被压到页面底部、中间留一大片空白。
        """
        if not (0 <= index < len(self._page_boxes)):
            return
        box = self._page_boxes[index]
        box.insertWidget(max(0, box.count() - 1), sec)
        box.insertSpacing(max(0, box.count() - 2), gap)

    def _select_section(self, index: int) -> None:
        """切到第 index 个分区：右侧换页、左导航高亮、滚动条回顶部。"""
        if not (0 <= index < self._pages.count()):
            return
        self._pages.setCurrentIndex(index)
        self._nav.set_current(index)
        page = self._pages.widget(index)
        if isinstance(page, QScrollArea):
            page.verticalScrollBar().setValue(0)

    def _build_class_section(self) -> None:
        sec = _Section("班级与座位", self.content_widget())
        st = self._store.state

        grid = QWidget(sec)
        gl = QGridLayout(grid)
        gl.setContentsMargins(0, 0, 0, 0)
        gl.setHorizontalSpacing(GRID_GAP)
        gl.setVerticalSpacing(GRID_GAP)

        self._name = QLineEdit(grid)
        self._name.setMaxLength(24)
        self._name.setFixedHeight(INPUT_H)
        self._name.setStyleSheet(_input_qss())
        w, lay = _field("班级名称", grid)
        lay.addWidget(self._name)

        self._rows = QSpinBox(grid)
        self._rows.setRange(1, 30)
        self._rows.setFixedHeight(INPUT_H)
        self._rows.setStyleSheet(_input_qss())
        w2, lay2 = _field("座位行数", grid)
        lay2.addWidget(self._rows)

        self._cols = QSpinBox(grid)
        self._cols.setRange(1, 30)
        self._cols.setFixedHeight(INPUT_H)
        self._cols.setStyleSheet(_input_qss())
        w3, lay3 = _field("座位列数", grid)
        lay3.addWidget(self._cols)

        self._step_m = QSpinBox(grid)
        self._step_m.setRange(1, 20)
        self._step_m.setFixedHeight(INPUT_H)
        self._step_m.setStyleSheet(_input_qss())
        w4, lay4 = _field("早读每次分值", grid)
        lay4.addWidget(self._step_m)

        self._step_n = QSpinBox(grid)
        self._step_n.setRange(1, 20)
        self._step_n.setFixedHeight(INPUT_H)
        self._step_n.setStyleSheet(_input_qss())
        w5, lay5 = _field("午读每次分值", grid)
        lay5.addWidget(self._step_n)

        for idx, widget in enumerate((w, w2, w3, w4, w5)):
            gl.addWidget(widget, idx // 3, idx % 3)
        for c in range(3):
            gl.setColumnStretch(c, 1)
        gl.setRowStretch(2, 1)
        sec.add(grid)

        ge = QFrame(sec)
        ge.setObjectName("groupEditor")
        ge.setStyleSheet(
            f"QFrame#groupEditor {{ background: #f9fafb; border: 1px solid {theme.LINE}"
            "; border-radius: 12px; }"
        )
        gel = QVBoxLayout(ge)
        gel.setContentsMargins(14, 12, 14, 12)
        gel.setSpacing(0)

        self._groups = QLineEdit(ge)
        self._groups.setMaxLength(40)
        self._groups.setFixedHeight(INPUT_H)
        self._groups.setPlaceholderText("例如 2-4-2")
        self._groups.setStyleSheet(_input_qss())
        gw, glay = _field("列分组 —— 决定过道在哪（每块几列）", ge)
        glay.addWidget(self._groups)
        gel.addWidget(gw)

        self._group_preview = _label(
            "", ge, size=12, weight=400, color=theme.INK3, height=0
        )
        gel.addSpacing(12)
        gel.addWidget(self._group_preview)

        presets, pflow = _row_btns(ge)
        for text, value in (
            ("无过道", "none"),
            ("均分 2 块", "2"),
            ("均分 3 块", "3"),
            ("左 2 ｜ 中 4 ｜ 右 2", "242"),
            ("3 ｜ 3 ｜ 2", "332"),
            ("4 ｜ 4", "44"),
        ):
            btn = _tiny(text, presets)
            btn.clicked.connect(lambda _=False, v=value: self._apply_group_preset(v))
            pflow.addWidget(btn)
        gel.addSpacing(10)
        gel.addWidget(presets)
        sec.add(ge, 14)

        ge2 = QFrame(sec)
        ge2.setObjectName("groupEditor")
        ge2.setStyleSheet(ge.styleSheet())
        gel2 = QVBoxLayout(ge2)
        gel2.setContentsMargins(14, 12, 14, 12)
        gel2.setSpacing(0)
        gel2.addWidget(_label(
            "讲台", ge2, size=12, weight=600, color=theme.INK2, height=16
        ))
        gel2.addSpacing(8)

        ctrl = QWidget(ge2)
        cf = FlowLayout(
            ctrl, h_spacing=14, v_spacing=10
        )
        self._podium_on = QCheckBox("在座位表上显示讲台", ctrl)
        self._podium_on.setStyleSheet(
            f"QCheckBox {{ font-size: 13px; color: {theme.INK2}"
            "; background: transparent; }"
        )
        cf.addWidget(self._podium_on)

        self._podium_pos = QComboBox(ctrl)
        self._podium_pos.addItem("上方（讲台在前）", "top")
        self._podium_pos.addItem("下方", "bottom")
        self._podium_pos.setFixedHeight(31)
        self._podium_pos.setStyleSheet(_input_qss())
        cf.addWidget(_inline_field("位置", ctrl, self._podium_pos))

        self._podium_span = QSpinBox(ctrl)
        self._podium_span.setRange(1, 30)
        self._podium_span.setFixedHeight(31)
        self._podium_span.setStyleSheet(_input_qss())
        cf.addWidget(_inline_field("占几格", ctrl, self._podium_span))

        self._podium_align = QComboBox(ctrl)
        for text, key in (("居左", "left"), ("居中", "center"), ("居右", "right")):
            self._podium_align.addItem(text, key)
        self._podium_align.setFixedHeight(31)
        self._podium_align.setStyleSheet(_input_qss())
        cf.addWidget(_inline_field("对齐", ctrl, self._podium_align))
        gel2.addWidget(ctrl)

        self._podium_preview = _label(
            "", ge2, size=12, weight=400, color=theme.INK3, height=0
        )
        gel2.addSpacing(12)
        gel2.addWidget(self._podium_preview)
        sec.add(ge2, 14)

        apply_btn = _mini("应用", sec, primary=True)
        apply_btn.clicked.connect(self._apply_class)
        wrap = QWidget(sec)
        wrap.setStyleSheet("background: transparent;")
        wl = QHBoxLayout(wrap)
        wl.setContentsMargins(0, 0, 0, 0)
        wl.addWidget(apply_btn)
        wl.addStretch(1)
        sec.add(wrap, 14)

        self._class_sec = sec
        self._add_page(sec, "班级与座位")

        self._build_dirs_section()

    def _build_dirs_section(self) -> None:
        sec = _Section("数据目录", self.content_widget())
        sec.add(_tip(
            "班级、学生积分、积分记录和备份都存在这个目录里。换电脑或想放到同步盘时，改到别处即可。",
            sec,
        ))

        self._dir_label = QLabel("", sec)
        self._dir_label.setObjectName("dirPath")
        self._dir_label.setFixedHeight(INPUT_H)
        self._dir_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._dir_label.setStyleSheet(
            f"QLabel#dirPath {{ background: {theme.PILL_GRAY_BG}"
            f"; border: 1px solid {theme.LINE}"
            f"; border-radius: 9px; padding: 0px 11px; font-size: 13px; color: {theme.INK}"
            "; }"
        )
        sec.add(self._dir_label)

        btns, flow = _row_btns(sec)
        self._dir_change = _mini("更改…", btns, primary=True)
        self._dir_change.clicked.connect(self._pick_data_dir)
        self._dir_open = _mini("打开当前目录", btns)
        self._dir_open.clicked.connect(self._open_data_dir)
        for b in (self._dir_change, self._dir_open):
            flow.addWidget(b)
        sec.add(btns, 10)

        sec.add(_tip("更改后需要重启程序生效，现有数据会自动复制过去。", sec), 10)

        self._dir_status = QLabel("", sec)
        self._dir_status.setWordWrap(True)
        self._dir_status.setStyleSheet(
            f"QLabel {{ color: {theme.INK3}; font-size: 12px; background: transparent; }}"
        )
        sec.add(self._dir_status, 6)
        self._append_to_page(0, sec)

    def _refresh_data_dir(self) -> None:
        """刷新「数据目录」分区的显示（当前目录 / 待生效目录 / 按钮可用性）。"""
        mod = _paths_mod()
        if mod is None:
            self._dir_current = None
            self._dir_label.setText("（数据目录接口尚未就绪）")
            self._dir_label.setToolTip("")
            self._dir_status.setText("未找到 app/core/paths.py，暂时无法查看或更改数据目录。")
            self._dir_change.setEnabled(False)
            self._dir_open.setEnabled(False)
            return
        try:
            cur = Path(mod.current_data_dir())
        except Exception as exc:
            self._dir_current = None
            self._dir_label.setText("（读取数据目录失败）")
            self._dir_label.setToolTip(str(exc))
            self._dir_status.setText(f"读取数据目录失败：{exc}")
            self._dir_change.setEnabled(False)
            self._dir_open.setEnabled(False)
            return
        self._dir_current = cur
        self._dir_label.setText(self._elide_dir_path(str(cur)))
        self._dir_label.setToolTip(str(cur))
        self._dir_change.setEnabled(True)
        self._dir_open.setEnabled(True)

        pending = None
        try:
            pending = mod.pending_data_dir()
        except Exception:
            pending = None
        if pending:
            self._dir_status.setText(f"下次启动生效：{pending}（现有数据会自动复制过去）。")
            return
        self._dir_status.setText("")

    def _elide_dir_path(self, text: str) -> str:
        """目录太长就用中间省略号，完整路径放 tooltip。"""
        width = DIR_TEXT_W
        btn_w = getattr(self, "_pages", None)
        if isinstance(btn_w, QWidget) and btn_w.width() > 0:
            width = max(220, btn_w.width() - 32)
        fm = self._dir_label.fontMetrics()
        return fm.elidedText(text, Qt.ElideMiddle, width)

    def _pick_data_dir(self) -> None:
        """「更改…」：选目录 → 交给 app/core/paths.request_data_dir 落盘。"""
        mod = _paths_mod()
        if mod is None:
            self._dir_status.setText("数据目录接口尚未就绪，暂时改不了。")
            return
        start = str(getattr(self, "_dir_current", None) or "")
        picked = QFileDialog.getExistingDirectory(self, "选择数据目录", start)
        if not picked:
            return
        try:
            ok, msg = mod.request_data_dir(Path(picked), migrate=True)
        except Exception as exc:
            ok, msg = False, f"设置失败：{exc}"
        if not ok:
            self._dir_status.setText(msg or "设置失败，请换个目录再试。")
            return
        self._refresh_data_dir()
        self._show_info(
            "更改文件目录",
            (msg or "已记录新的数据目录。") + "\n\n更改后需要重启程序生效，现有数据会自动复制过去。",
        )

    def _open_data_dir(self) -> None:
        """「打开当前目录」：用系统文件管理器打开数据目录。"""
        cur = getattr(self, "_dir_current", None)
        if cur is None:
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(cur)))

    def _show_info(self, title: str, text: str) -> None:
        """信息提示；单独成方法，探针可替身掉（不弹真对话框）。"""
        from PyQt5.QtWidgets import QMessageBox

        QMessageBox.information(self, title, text)

    def showEvent(self, event) -> None:
        """窗口真正显示时重新读一次数据目录。

        用户可能「改完目录但没重启」又打开设置，所以每次显示都重读
        `paths.current_data_dir()` / `pending_data_dir()`；同时这时才量得到
        实际宽度，顺便按新宽度重算目录的省略号。
        """
        super().showEvent(event)
        if hasattr(self, "_dir_label"):
            self._refresh_data_dir()
            if self._dir_current is not None:
                self._dir_label.setText(self._elide_dir_path(str(self._dir_current)))

    def _build_about_section(self) -> None:
        sec = _Section("关于软件", self.content_widget())

        self._about_name = QLabel(about_mod.APP_NAME, sec)
        self._about_name.setStyleSheet(
            f"QLabel {{ color: {theme.INK}"
            "; font-size: 18px; font-weight: 700; background: transparent; }"
        )
        sec.add(self._about_name)

        self._about_version = QLabel(f"版本 {about_mod.version_text()}", sec)
        self._about_version.setStyleSheet(
            f"QLabel {{ color: {theme.INK2}"
            "; font-size: 13px; background: transparent; }"
        )
        sec.add(self._about_version, 8)

        self._about_author = QLabel(f"作者：{about_mod.AUTHOR}", sec)
        self._about_author.setStyleSheet(
            f"QLabel {{ color: {theme.INK2}"
            "; font-size: 13px; background: transparent; }"
        )
        sec.add(self._about_author, 6)

        sec.add(_tip(about_mod.TAGLINE, sec), 10)

        btns, flow = _row_btns(sec)
        self._about_manual = _mini("使用说明", btns, primary=True)
        self._about_manual.setObjectName("aboutManual")
        self._about_manual.clicked.connect(self._open_manual)
        flow.addWidget(self._about_manual)

        self._about_changelog = _mini("更新日志", btns)
        self._about_changelog.setObjectName("aboutChangelog")
        self._about_changelog.clicked.connect(self._open_changelog)
        flow.addWidget(self._about_changelog)
        sec.add(btns, 10)

        sec.lay.addStretch(1)
        self._add_page(sec, "关于软件")

    def _open_manual(self) -> None:
        """「使用说明」：本地富文本弹窗，不联网、不跳浏览器。"""
        from app.ui.dialogs.manual_dialog import ManualDialog

        ManualDialog(self).exec_()

    def _open_changelog(self) -> None:
        """「更新日志」：左侧选版本、右侧读内容，内容来自随包的 CHANGELOG.md。"""
        from app.ui.dialogs.changelog_dialog import ChangelogDialog

        ChangelogDialog(self).exec_()

    def _build_theme_section(self) -> None:
        """第 10 页「主题」：背景图、启动动画、程序图标共用同一页。"""
        self._build_background_section()

        page_index = len(self._page_boxes) - 1
        self._build_launch_section(page_index)
        self._build_icon_section(page_index)

    def _build_background_section(self) -> None:
        sec = _Section("主界面背景图", self.content_widget())
        sec.add(_tip(
            "选一张图片当主界面（座位表）的底图，图片会从座位缝里透出来。建议 1600×900 或同比例；只记录文件路径，原图别删、别移动。",
            sec,
        ))

        self._bg_label = QLabel("未设置", sec)
        self._bg_label.setObjectName("bgPath")
        self._bg_label.setFixedHeight(INPUT_H)
        self._bg_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._bg_label.setStyleSheet(
            f"QLabel#bgPath {{ background: {theme.PILL_GRAY_BG}"
            f"; border: 1px solid {theme.LINE}"
            f"; border-radius: 9px; padding: 0px 11px; font-size: 13px; color: {theme.INK}"
            "; }"
        )
        sec.add(self._bg_label)

        btns, flow = _row_btns(sec)
        self._bg_pick = _mini("选择图片…", btns, primary=True)
        self._bg_pick.clicked.connect(self._pick_background)
        self._bg_clear = _mini("清除", btns)
        self._bg_clear.clicked.connect(self._clear_background_image)
        for b in (self._bg_pick, self._bg_clear):
            flow.addWidget(b)
        sec.add(btns, 10)

        self._bg_status = QLabel("", sec)
        self._bg_status.setWordWrap(True)
        self._bg_status.setStyleSheet(
            f"QLabel {{ color: {theme.INK3}; font-size: 12px; background: transparent; }}"
        )
        sec.add(self._bg_status, 6)
        self._add_page(sec, "主题")

    def _build_launch_section(self, page_index: int) -> None:
        sec = _Section("启动动画", self.content_widget())
        sec.add(_tip(
            "选一段短视频，程序启动时播放。只记录文件路径，视频请放在固定位置、别删。",
            sec,
        ))

        self._launch_on = QCheckBox("启动时播放动画", sec)
        self._launch_on.setStyleSheet(
            f"QCheckBox {{ color: {theme.INK}"
            "; font-size: 13px; font-weight: 600; spacing: 8px; background: transparent; }"
        )
        self._launch_on.toggled.connect(self._on_launch_toggled)
        sec.add(self._launch_on, 6)

        self._launch_label = QLabel("未选择", sec)
        self._launch_label.setObjectName("launchPath")
        self._launch_label.setFixedHeight(INPUT_H)
        self._launch_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._launch_label.setStyleSheet(
            f"QLabel#launchPath {{ background: {theme.PILL_GRAY_BG}"
            f"; border: 1px solid {theme.LINE}"
            f"; border-radius: 9px; padding: 0px 11px; font-size: 13px; color: {theme.INK}"
            "; }"
        )
        sec.add(self._launch_label, 10)

        btns, flow = _row_btns(sec)
        self._launch_pick = _mini("选择视频…", btns, primary=True)
        self._launch_pick.clicked.connect(self._pick_launch_video)
        self._launch_preview = _mini("预览", btns)
        self._launch_preview.clicked.connect(self._preview_launch_video)
        self._launch_clear = _mini("清除", btns)
        self._launch_clear.clicked.connect(self._clear_launch_video)
        for b in (self._launch_pick, self._launch_preview, self._launch_clear):
            flow.addWidget(b)
        sec.add(btns, 10)

        sec.add(_tip(
            "支持 mp4 / mov / wmv / avi / mkv；改完将在下次启动时生效。",
            sec,
        ), 10)

        self._launch_status = QLabel("", sec)
        self._launch_status.setWordWrap(True)
        self._launch_status.setStyleSheet(
            f"QLabel {{ color: {theme.INK3}; font-size: 12px; background: transparent; }}"
        )
        sec.add(self._launch_status, 6)
        self._append_to_page(page_index, sec)

    def _build_icon_section(self, page_index: int) -> None:
        """程序图标：选图 → 转 ico → 存数据目录 → 立即换成窗口 / 任务栏图标。

        这块必须是 `setSecIcon` 而不是 `setSec`：主题页的老测试按 objectName
        数「两块分区」（背景图 + 启动动画），换个名字就不会被数成第三块。
        """
        sec = _Section("程序图标", self.content_widget(), name="setSecIcon")
        sec.add(_tip(
            "窗口与任务栏上的图标。支持 .ico 和 .png（png 会自动转成 多尺寸 ico），换完立即生效；文件存在数据目录的 icon 目录里。",
            sec,
        ))

        holder = QWidget(sec)
        row = QHBoxLayout(holder)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(12)
        self._icon_preview = QLabel(sec)
        self._icon_preview.setObjectName("iconPreview")
        self._icon_preview.setFixedSize(44, 44)
        self._icon_preview.setAlignment(Qt.AlignCenter)
        self._icon_preview.setStyleSheet(
            f"QLabel#iconPreview {{ background: {theme.PILL_GRAY_BG}"
            f"; border: 1px solid {theme.LINE}"
            "; border-radius: 9px; }"
        )
        row.addWidget(self._icon_preview, 0, Qt.AlignVCenter)

        self._icon_label = QLabel("默认图标", sec)
        self._icon_label.setObjectName("iconName")
        self._icon_label.setWordWrap(True)
        self._icon_label.setStyleSheet(
            f"QLabel#iconName {{ color: {theme.INK}"
            "; font-size: 13px; background: transparent; }"
        )
        row.addWidget(self._icon_label, 1)
        sec.add(holder, 10)

        btns, flow = _row_btns(sec)
        self._icon_pick = _mini("更换程序图标", btns, primary=True)
        self._icon_pick.clicked.connect(self._pick_app_icon)
        self._icon_clear = _mini("恢复默认", btns)
        self._icon_clear.clicked.connect(self._reset_app_icon)
        for b in (self._icon_pick, self._icon_clear):
            flow.addWidget(b)
        sec.add(btns, 10)

        sec.add(_tip(
            "图标存在数据目录里，不会动你选的原图；换电脑时数据目录一起拷走即可。",
            sec,
        ), 10)

        self._icon_status = QLabel("", sec)
        self._icon_status.setWordWrap(True)
        self._icon_status.setStyleSheet(
            f"QLabel {{ color: {theme.INK3}; font-size: 12px; background: transparent; }}"
        )
        sec.add(self._icon_status, 6)
        self._append_to_page(page_index, sec)

    def _refresh_theme_ui(self) -> None:
        """按 store 里的状态刷新主题页（背景图 / 启动动画的勾选与文件名）。"""
        st = self._store.state
        bg = getattr(st, "background", None)
        if bg:
            bg_path = Path(str(bg))
            self._bg_label.setText(f"已设置：{bg_path.name or str(bg)}")
            self._bg_label.setToolTip(str(bg))
            self._bg_clear.setEnabled(True)
        else:
            self._bg_label.setText("未设置")
            self._bg_label.setToolTip("")
            self._bg_clear.setEnabled(False)

        s = st.settings
        path = str(getattr(s, "launch_animation", "") or "")
        self._launch_on.blockSignals(True)
        self._launch_on.setChecked(bool(getattr(s, "launch_enabled", True)))
        self._launch_on.blockSignals(False)
        if path:
            video = Path(path)
            self._launch_label.setText(f"已选择：{video.name or path}")
            self._launch_label.setToolTip(path)
        else:
            self._launch_label.setText("未选择")
            self._launch_label.setToolTip("")
        self._launch_preview.setEnabled(True)
        self._launch_clear.setEnabled(bool(path))
        self._refresh_icon_ui()

    def _refresh_icon_ui(self) -> None:
        """按设置里的 app_icon / 数据目录里的图标文件刷新预览。"""
        s = self._store.state.settings
        path = str(getattr(s, "app_icon", "") or "")
        icon = app_icon_mod.load_icon(path) if path else QIcon()
        if icon.isNull():
            try:
                fallback = app_icon_mod.icon_file()
            except OSError:
                fallback = None
            if fallback is not None and fallback.is_file():
                icon = app_icon_mod.load_icon(fallback)
                path = str(fallback) if not icon.isNull() else ""
        if icon.isNull():
            self._icon_preview.clear()
            self._icon_preview.setText("默认")
            self._icon_preview.setToolTip("")
            self._icon_label.setText("默认图标")
            self._icon_clear.setEnabled(False)
            return
        self._icon_preview.setPixmap(icon.pixmap(32, 32))
        self._icon_preview.setToolTip(path)
        self._icon_label.setText("自定义图标（窗口与任务栏已生效）")
        self._icon_clear.setEnabled(True)

    def _pick_app_icon(self) -> None:
        """「更换程序图标」：选 .ico / .png，落盘成固定的 app-icon.ico。"""
        s = self._store.state.settings
        start = ""
        cur = str(getattr(s, "app_icon", "") or "")
        if cur:
            start = str(Path(cur).parent)
        path, _flt = QFileDialog.getOpenFileName(self, "选择程序图标", start,
                                                 "图标文件 (*.ico *.png)")
        if not path:
            return
        self._apply_app_icon(path)

    def _apply_app_icon(self, path: str) -> bool:
        """把图标设成全局窗口图标、写进设置、通知外面；返回是否成功。

        外面传进来的可以是任意 .ico / .png：只要不是数据目录里那个固定文件名，
        先转成 / 拷成 app-data/icon/app-icon.ico 再应用。这样设置里永远只记这
        一个路径，换电脑时把数据目录一起拷走，图标还在。
        """
        raw = str(path or "")
        if not raw:
            self._icon_status.setText("图标读取失败，请换一张图片再试。")
            return False
        try:
            target = app_icon_mod.icon_file()
        except OSError:
            self._icon_status.setText("数据目录不可用，图标没法保存。")
            return False
        try:
            same = Path(raw).resolve() == target.resolve()
        except OSError:
            same = False
        if not same:
            ok, saved = app_icon_mod.save_icon(raw)
            if not ok:
                self._icon_status.setText(saved)
                return False
            target = Path(saved)
        icon = app_icon_mod.load_icon(str(target))
        if icon.isNull():
            self._icon_status.setText("图标读取失败，请换一张图片再试。")
            return False
        app_icon_mod.apply_icon(str(target))
        self._store.state.settings.app_icon = str(target)
        self._refresh_icon_ui()
        self._icon_status.setText("已更换，窗口与任务栏图标立即生效。")
        self.icon_changed.emit(str(target))
        self.changed.emit()
        return True

    def _reset_app_icon(self) -> None:
        """「恢复默认」：删掉数据目录里的图标文件，图标回到程序默认。"""
        app_icon_mod.remove_icon()
        self._store.state.settings.app_icon = ""
        app_icon_mod.apply_icon("")
        self._refresh_icon_ui()
        self._icon_status.setText("已恢复默认图标。")
        self.icon_changed.emit("")
        self.changed.emit()

    def _pick_background(self) -> None:
        """「选择图片…」：把绝对路径写进 state.background（和主窗口同一字段）。"""
        path, _flt = QFileDialog.getOpenFileName(self, "选择主界面背景图", "",
                                                 "图片 (*.png *.jpg *.jpeg *.bmp *.webp)")
        if not path:
            return
        p = Path(path)
        if not p.is_file():
            self._bg_status.setText("选择的文件不存在，请重新选。")
            return
        rel = theme_store.import_file(p, "media")
        self._store.state.background = rel or str(p)
        self._refresh_theme_ui()
        self._bg_status.setText("已设置背景图，主界面会立即重绘。")
        self.changed.emit()

    def _clear_background_image(self) -> None:
        if not getattr(self._store.state, "background", None):
            self._bg_status.setText("当前没有设置背景图。")
            return
        self._store.state.background = None
        self._refresh_theme_ui()
        self._bg_status.setText("已清除背景图。")
        self.changed.emit()

    def _on_launch_toggled(self, checked: bool) -> None:
        self._store.state.settings.launch_enabled = bool(checked)
        self._launch_status.setText(
            "已开启：启动时播放动画。" if checked else "已关闭启动动画。"
        )
        self.changed.emit()

    def _pick_launch_video(self) -> None:
        s = self._store.state.settings
        start = ""
        cur = str(getattr(s, "launch_animation", "") or "")
        if cur:
            start = str(Path(cur).parent)
        path, _flt = QFileDialog.getOpenFileName(self, "选择启动动画视频", start,
                                                 "视频文件 (*.mp4 *.mov *.wmv *.avi *.mkv);;所有文件 (*)")
        if not path:
            return
        p = Path(path)
        if not p.is_file():
            self._launch_status.setText("选择的视频文件不存在，请重新选。")
            return
        rel = theme_store.import_file(p, "splash")
        s.launch_animation = rel or str(p)
        self._refresh_theme_ui()
        self._launch_status.setText("已选择视频，将在下次启动时生效。")
        self.changed.emit()

    def _preview_launch_video(self) -> None:
        """「预览」：借 video_picker 的播放弹窗；任何失败都只提示，不抛异常。"""
        raw = str(getattr(self._store.state.settings,
                          "launch_animation", "") or "")
        path = theme_store.resolve(raw)
        if not path:
            if raw:
                self._launch_status.setText("视频文件不在了，请重新选择。")
                self._show_info("预览启动动画",
                                "这个视频文件已经不在了（可能被删掉或移走了），请重新选择。")
                return
            self._show_info("预览启动动画",
                            "还没有选择视频文件。\n先点「选择视频…」挑一段；选好后会在下次启动时生效。")
            return
        if not Path(path).is_file():
            self._launch_status.setText("视频文件不在了，请重新选择。")
            self._show_info("预览启动动画",
                            "这个视频文件已经不在了（可能被删掉或移走了），请重新选择。")
            return
        try:
            from app.ui.widgets.video_picker import make_player_dialog

            player = make_player_dialog(path, self)
        except Exception as exc:
            player = None
            self._launch_status.setText(f"预览失败：{exc}")
        if player is None:
            self._show_info("无法预览",
                            "当前环境没有可用的视频播放组件，或这段视频无法解码。\n文件已保留，程序启动时会再试。")
            return
        try:
            player.exec_()
        except Exception as exc:
            self._launch_status.setText(f"预览失败：{exc}")

    def _clear_launch_video(self) -> None:
        s = self._store.state.settings
        if not str(getattr(s, "launch_animation", "") or ""):
            self._launch_status.setText("当前没有选择启动动画视频。")
            return
        s.launch_animation = ""
        self._refresh_theme_ui()
        self._launch_status.setText("已清除启动动画视频。")
        self.changed.emit()

    def _build_import_section(self) -> None:
        sec = _Section("导入座位表", self.content_widget())
        sec.add(_tip(
            "方式一：在 Excel 里选中座位区域复制，粘贴到下面的框里（每个格子一个姓名，空格子留空）。<br>方式二：选择 .xlsx / .csv 文件。同名同学会保留原有积分，只更新座位。",
            sec,
        ))
        self._paste = QTextEdit(sec)
        self._paste.setPlaceholderText("张伟\t王芳\t李娜\n刘洋\t陈静\t杨帆")
        self._paste.setFixedHeight(126)
        self._paste.setStyleSheet(
            f"QTextEdit {{ background: {theme.CARD}"
            f"; border: 1px solid {theme.LINE}"
            f"; border-radius: 10px; padding: 10px; font-size: 13px; color: {theme.INK}"
            "; }"
        )
        sec.lay.addSpacing(6)
        sec.add(self._paste)

        btns, flow = _row_btns(sec)
        b1 = _mini("按粘贴内容生成座位表", btns, primary=True)
        b1.clicked.connect(lambda: self._delegate("import_paste"))
        b2 = _mini("选择表格文件", btns)
        b2.clicked.connect(lambda: self._delegate("import_file"))
        b3 = _mini("导入座位表照片（作底图）", btns)
        b3.clicked.connect(lambda: self._delegate("import_photo"))
        b4 = _mini("清除底图", btns)
        b4.clicked.connect(lambda: self._delegate("clear_background"))
        for b in (b1, b2, b3, b4):
            flow.addWidget(b)
        sec.add(btns, 10)
        sec.add(_tip(
            "照片仅作为座位表底图显示，方便你照着手动排座位；点击座位表空白格可以直接添加学生，点击已有学生可改名或删除。",
            sec,
        ), 10)
        self._add_page(sec, "导入座位表")

    def _build_prize_section(self) -> None:
        sec = _Section("抽奖奖品与权重", self.content_widget())
        self._prize_mini = _label(
            "", sec, size=12, weight=400, color=theme.INK3, height=0
        )
        sec.add(self._prize_mini)

        btns, flow = _row_btns(sec)
        b1 = _mini("编辑奖品与权重", btns)
        b1.clicked.connect(lambda: self._delegate("edit_prizes"))
        flow.addWidget(b1)

        self._threshold = QSpinBox(btns)
        self._threshold.setRange(0, 1000000)
        self._threshold.setFixedHeight(31)
        self._threshold.setStyleSheet(_input_qss())
        self._threshold_box = _inline_field("判定分数 ≥", btns, self._threshold)
        flow.addWidget(self._threshold_box)

        self._cost = QSpinBox(btns)
        self._cost.setRange(0, 1000000)
        self._cost.setFixedHeight(31)
        self._cost.setStyleSheet(_input_qss())
        flow.addWidget(_inline_field("每次扣除", btns, self._cost))

        self._animation = QComboBox(btns)
        for key in ANIMATIONS:
            self._animation.addItem(ANIMATION_LABELS.get(key, key), key)
        self._animation.setFixedHeight(31)
        self._animation.setStyleSheet(_input_qss())
        flow.addWidget(_inline_field("抽奖动画", btns, self._animation))
        sec.add(btns, 10)

        sec.add(_label(
            "抽奖资格判定", sec, size=13, weight=700, color=theme.INK, height=0
        ), 12)
        mode_btns, mode_flow = _row_btns(sec)
        self._mode_group = QButtonGroup(sec)
        self._mode_group.setExclusive(True)
        self._mode_threshold = _mini("按门槛分", mode_btns)
        self._mode_rank = _mini("按积分排名", mode_btns)
        for btn in (self._mode_threshold, self._mode_rank):
            btn.setCheckable(True)
            btn.setStyleSheet(MODE_QSS)
            self._mode_group.addButton(btn)
            mode_flow.addWidget(btn)
        self._mode_threshold.clicked.connect(lambda: self._apply_lottery_mode("threshold"))
        self._mode_rank.clicked.connect(lambda: self._apply_lottery_mode("rank"))

        self._top_n = QSpinBox(mode_btns)
        self._top_n.setRange(1, 999)
        self._top_n.setValue(10)
        self._top_n.setFixedHeight(31)
        self._top_n.setStyleSheet(_input_qss())
        self._rank_box = _inline_field("前 N 名", mode_btns, self._top_n)
        mode_flow.addWidget(self._rank_box)
        sec.add(mode_btns, 10)
        sec.add(_tip(
            "按门槛分：积分达到「判定分数」就能抽奖；按积分排名：只有积分前 N 名能抽奖（并列按排名顺序）。",
            sec,
        ), 10)

        save_row, save_flow = _row_btns(sec)
        b2 = _mini("保存抽奖设置", save_row)
        b2.clicked.connect(self._save_lottery)
        save_flow.addWidget(b2)
        b3 = _mini("清空抽奖记录", save_row, danger=True)
        b3.setObjectName("stClearLottery")
        b3.clicked.connect(self._clear_lottery_records)
        save_flow.addWidget(b3)
        sec.add(save_row, 10)
        sec.add(_tip(
            "动画用于「抽奖」与「随机」揭晓结果；没有设置时默认无动画。",
            sec,
        ), 10)
        self._add_page(sec, "抽奖奖品与权重")

    def _apply_lottery_mode(self, mode: str) -> None:
        """按资格判定模式切换控件可见性（门槛分 ⇄ 排名名额）。"""
        mode = normalize_lottery_mode(mode)
        is_rank = mode == "rank"
        self._mode_rank.setChecked(is_rank)
        self._mode_threshold.setChecked(not is_rank)
        for box in (self._threshold_box, self._rank_box):
            box.setVisible(False)
        show = self._rank_box if is_rank else self._threshold_box
        show.setVisible(True)
        show.setEnabled(True)

    def _current_lottery_mode(self) -> str:
        return "rank" if self._mode_rank.isChecked() else "threshold"

    def _build_video_section(self) -> None:
        sec = _Section("抽奖动画视频", self.content_widget())
        sec.add(_tip(
            "抽奖动画是<b>按奖项</b>设置的：在抽奖弹窗右侧给每个奖项选「视频揭晓」，再指定要用哪一段；不指定就每次从视频库随机挑一段。<br>支持一次导入多个 mp4 / webm / mov（建议单段 3～10 秒、10MB 以内）。视频存在本机，不随备份导出，换设备后需要重新导入。",
            sec,
        ))
        btns, flow = _row_btns(sec)
        b1 = _mini("管理视频库…", btns, primary=True)
        b1.clicked.connect(lambda: self._delegate("pick_video"))
        b2 = _mini("清空视频库", btns, danger=True)
        b2.clicked.connect(lambda: self._delegate("clear_video"))
        flow.addWidget(b1)
        flow.addWidget(b2)
        sec.add(btns, 10)
        self._video_info = _label(
            "", sec, size=12, weight=400, color=theme.INK3, height=0
        )
        sec.lay.addSpacing(12)
        sec.add(self._video_info)
        self._add_page(sec, "抽奖动画视频")

    def _build_export_section(self) -> None:
        sec = _Section("导出积分记录（文档）", self.content_widget())
        btns, flow = _row_btns(sec)
        b1 = _mini("导出 Excel (.xlsx)", btns, primary=True)
        b1.clicked.connect(lambda: self._delegate("export_xlsx"))
        b2 = _mini("导出 Word (.docx)", btns)
        b2.clicked.connect(lambda: self._delegate("export_docx"))
        b3 = _mini("导出 CSV", btns)
        b3.clicked.connect(lambda: self._delegate("export_csv"))
        for b in (b1, b2, b3):
            flow.addWidget(b)
        sec.add(btns, ROW_BTNS_GAP)
        self._add_page(sec, "导出积分记录")

    def _build_rank_export_section(self) -> None:
        sec = _Section("一键导出排行榜表格", self.content_widget())
        sec.add(_tip(
            "按当前积分从高到低导出全班名次，含座位、积分、加分合计、扣分合计、记录条数；同分并列同名次。表格可直接打印或在 Excel 里继续统计。",
            sec,
        ))
        btns, flow = _row_btns(sec)
        b1 = _mini("导出排行榜 Excel (.xlsx)", btns, primary=True)
        b1.clicked.connect(lambda: self._delegate("rank_xlsx"))
        b2 = _mini("导出排行榜 Word (.docx)", btns)
        b2.clicked.connect(lambda: self._delegate("rank_docx"))
        b3 = _mini("导出排行榜 CSV", btns)
        b3.clicked.connect(lambda: self._delegate("rank_csv"))
        for b in (b1, b2, b3):
            flow.addWidget(b)
        sec.add(btns, 10)
        self._add_page(sec, "一键导出排行榜")

    def _build_backup_section(self) -> None:
        sec = _Section("数据备份", self.content_widget())
        sec.add(_tip("数据保存在这台电脑里。换电脑或清理前，请先导出备份文件。", sec))
        btns, flow = _row_btns(sec)
        b1 = _mini("导出备份 (.zip)", btns)
        b1.clicked.connect(lambda: self._delegate("backup_export"))
        b2 = _mini("导入备份", btns)
        b2.clicked.connect(lambda: self._delegate("backup_import"))
        b3 = _mini("清空全部数据", btns, danger=True)
        b3.clicked.connect(self._clear_all)
        b4 = _mini("清空积分记录（保留座位表）", btns, danger=True)
        b4.clicked.connect(self._clear_records)
        for b in (b1, b2, b3, b4):
            flow.addWidget(b)
        sec.add(btns, 10)
        self._backup_media = QCheckBox("导出备份时包含抽奖视频", sec)
        self._backup_media.setObjectName("stBackupMedia")
        self._backup_media.setStyleSheet(
            f"QCheckBox {{ color: {theme.INK}"
            "; font-size: 13px; background: transparent; }QCheckBox::indicator { width: 16px; height: 16px; }"
        )
        self._backup_media.toggled.connect(self._on_backup_media_toggled)
        self._backup_media.setChecked(
            bool(getattr(self._store.state.settings, "backup_include_media", False))
        )
        sec.add(self._backup_media, 10)
        sec.add(_tip(
            "勾选后备份包里会带 <app_data>/videos 里的抽奖视频，包会明显变大；不勾选只备份数据，换设备后需要重新导入视频。",
            sec,
        ), 8)
        self._backup_theme = QCheckBox("导出备份时包含主题文件（图标/背景/界面小图）", sec)
        self._backup_theme.setObjectName("stBackupTheme")
        self._backup_theme.setStyleSheet(
            f"QCheckBox {{ color: {theme.INK}"
            "; font-size: 13px; background: transparent; }QCheckBox::indicator { width: 16px; height: 16px; }"
        )
        self._backup_theme.toggled.connect(self._on_backup_theme_toggled)
        self._backup_theme.setChecked(
            bool(getattr(self._store.state.settings, "backup_include_theme", False))
        )
        sec.add(self._backup_theme, 8)
        sec.add(_tip(
            "勾选后备份包里会带 <app_data>/theme 里的图标、背景图与界面小图；换机器时连主题一起带过去，但要稍大一点。",
            sec,
        ), 8)
        sec.add(_tip(
            "「清空全部数据」会把班级、座位、学生积分、积分记录和抽奖结果全部恢复为全新班级，无法撤销；清空前请先「导出备份 (.zip)」。",
            sec,
        ), 10)
        self._add_page(sec, "数据备份")

    def _on_backup_media_toggled(self, checked: bool) -> None:
        self._store.state.settings.backup_include_media = bool(checked)
        self.changed.emit()

    def _on_backup_theme_toggled(self, checked: bool) -> None:
        self._store.state.settings.backup_include_theme = bool(checked)
        self.changed.emit()

    def _build_sync_section(self) -> None:
        sec = _Section("多端同步（手机 / 其他电脑查看积分）", self.content_widget())
        self._sync_hint = _tip(
            "开启后本机就是同步服务器：手机连同一 WiFi 直接打开链接即可。不在同一 WiFi 也能看，点「生成公网地址」。",
            sec,
        )
        sec.add(self._sync_hint)

        btns, flow = _row_btns(sec)
        self._sync_on = _mini("开启同步", btns, primary=True)
        self._sync_on.clicked.connect(self._toggle_sync)
        self._sync_off = _mini("关闭同步", btns)
        self._sync_off.clicked.connect(self._toggle_sync)
        self._copy_btn = _mini("复制手机链接", btns)
        self._copy_btn.clicked.connect(self._copy_sync_url)
        for b in (self._sync_on, self._sync_off, self._copy_btn):
            flow.addWidget(b)
        sec.add(btns, 10)

        tok = QWidget(sec)
        tl = QHBoxLayout(tok)
        tl.setContentsMargins(0, 0, 0, 0)
        tl.setSpacing(10)
        self._token_label = QLabel("口令：—", tok)
        self._token_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._token_label.setFixedHeight(LABEL_H)
        self._token_label.setStyleSheet(
            f"QLabel {{ color: {theme.INK2}"
            "; font-size: 13px; font-weight: 700; background: transparent; }"
        )
        tl.addWidget(self._token_label)
        tl.addStretch(1)
        self._token_copy = _mini("复制口令", tok)
        self._token_copy.clicked.connect(self._copy_token)
        self._token_new = _mini("重新生成", tok)
        self._token_new.clicked.connect(self._new_token)
        tl.addWidget(self._token_copy)
        tl.addWidget(self._token_new)
        sec.add(tok, 10)

        pair = QWidget(sec)
        pl = QHBoxLayout(pair)
        pl.setContentsMargins(0, 0, 0, 0)
        pl.setSpacing(10)
        self._pair_label = QLabel("配对令牌：—", pair)
        self._pair_label.setObjectName("stPairToken")
        self._pair_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._pair_label.setFixedHeight(LABEL_H)
        self._pair_label.setStyleSheet(
            f"QLabel {{ color: {theme.INK2}"
            "; font-size: 13px; font-weight: 700; background: transparent; }"
        )
        pl.addWidget(self._pair_label)
        pl.addStretch(1)
        self._pair_copy = _mini("复制配对令牌", pair)
        self._pair_copy.clicked.connect(self._copy_pair)
        self._pair_new = _mini("换一个", pair)
        self._pair_new.clicked.connect(self._new_pair)
        pl.addWidget(self._pair_copy)
        pl.addWidget(self._pair_new)
        sec.add(pair, 8)
        sec.add(_tip(
            "配对令牌在第一次开公网时自动生成，跟着链接和二维码走：手机拿它证明「我扫的是本班的码」。它不会随「重新生成口令」失效。",
            sec,
        ), 8)

        h5c = _label(
            "这台电脑跟着另一台跑（各存副本、互相同步）", sec, size=13, weight=700,
            color=theme.INK, height=H4_H,
        )
        sec.add(h5c, 12)
        crow = QWidget(sec)
        cl = QHBoxLayout(crow)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(10)
        st_now = self._store.state.settings
        self._srv_url = QLineEdit(crow)
        self._srv_url.setObjectName("stServerUrl")
        self._srv_url.setPlaceholderText("主机地址，例如 http://192.168.1.9:8765")
        self._srv_url.setFixedHeight(INPUT_H)
        self._srv_url.setText(getattr(st_now, "sync_client_url", "") or "")
        cl.addWidget(self._srv_url, 2)
        self._srv_tok = QLineEdit(crow)
        self._srv_tok.setObjectName("stServerToken")
        self._srv_tok.setPlaceholderText("主机口令")
        self._srv_tok.setFixedHeight(INPUT_H)
        self._srv_tok.setEchoMode(QLineEdit.Password)
        self._srv_tok.setText(getattr(st_now, "sync_client_token", "") or "")
        cl.addWidget(self._srv_tok, 1)
        sec.add(crow, 8)

        cbtns, cflow = _row_btns(sec)
        self._srv_test = _mini("测试连接", cbtns)
        self._srv_test.clicked.connect(self._test_client)
        self._srv_sync = _mini("立即同步", cbtns, primary=True)
        self._srv_sync.clicked.connect(self._sync_client_now)
        self._srv_diag = _mini("连接体检", self._srv_sync.parentWidget() or row)
        self._srv_diag.clicked.connect(self._run_diagnose)
        cflow.addWidget(self._srv_test)
        cflow.addWidget(self._srv_sync)
        self._srv_auto = QCheckBox("启动时自动同步，并每 2 分钟同步一次", cbtns)
        self._srv_auto.setObjectName("stServerAuto")
        self._srv_auto.setChecked(bool(getattr(st_now, "sync_auto", False)))
        self._srv_auto.stateChanged.connect(self._save_client_settings)
        cflow.addWidget(self._srv_auto)
        sec.add(cbtns, 8)
        sec.add(_tip(
            "两边各自保留一份完整数据：同步时只交换「谁加了谁几分」这类记录，积分由记录重新算出来，所以不会出现重复扣分。撤销类操作仍以本机为准。",
            sec,
        ), 8)
        self._srv_status = _tip("", sec)
        sec.add(self._srv_status, 8)

        pub, pflow = _row_btns(sec)
        self._pub_on = _mini("生成公网地址", pub, primary=True)
        self._pub_on.clicked.connect(self._start_tunnel)
        self._pub_off = _mini("停止公网", pub)
        self._pub_off.clicked.connect(self._stop_tunnel)
        self._pub_get = _mini("下载穿透组件", pub)
        self._pub_get.clicked.connect(self._download_tunnel)
        for b in (self._pub_on, self._pub_off, self._pub_get):
            pflow.addWidget(b)
        sec.add(pub, 10)

        self._sync_status = _tip("", sec)
        sec.add(self._sync_status, 10)

        self._qr_label = QLabel(sec)
        self._qr_label.setFixedSize(QR_PX, QR_PX)
        self._qr_label.setAlignment(Qt.AlignCenter)
        self._qr_label.setStyleSheet(
            f"QLabel {{ background: #ffffff; border: 1px solid {theme.LINE}"
            "; border-radius: 8px; }"
        )
        self._qr_label.setVisible(False)
        qr_row = QWidget(sec)
        qrl = QHBoxLayout(qr_row)
        qrl.setContentsMargins(0, 0, 0, 0)
        qrl.setSpacing(12)
        qrl.addWidget(self._qr_label)
        self._qr_hint = QLabel("", qr_row)
        self._qr_hint.setWordWrap(True)
        self._qr_hint.setStyleSheet(
            f"QLabel {{ color: {theme.INK3}"
            "; font-size: 12px; background: transparent; }"
        )
        qrl.addWidget(self._qr_hint, 1)
        sec.add(qr_row, 10)

        self._fixed_hint = _tip(
            "公网地址默认是随机的，每次重启都变。想让它固定：在 cloudflare 零信任控制台建一条隧道，公开主机名指向 http://localhost:8765，然后把隧道令牌和域名填在下面。填了之后就用固定地址，不再生成随机地址。",
            sec,
        )
        sec.add(self._fixed_hint, 10)

        host_row = QWidget(sec)
        hl = QHBoxLayout(host_row)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(10)
        self._fixed_host = QLineEdit(host_row)
        self._fixed_host.setObjectName("stTunnelHost")
        self._fixed_host.setPlaceholderText("固定域名，例如 jf.example.com")
        self._fixed_host.setFixedHeight(INPUT_H)
        self._fixed_host.setStyleSheet(_input_qss())
        hl.addWidget(self._fixed_host, 1)
        self._fixed_token = QLineEdit(host_row)
        self._fixed_token.setObjectName("stTunnelToken")
        self._fixed_token.setPlaceholderText("隧道令牌（一长串，控制台里复制）")
        self._fixed_token.setFixedHeight(INPUT_H)
        self._fixed_token.setEchoMode(QLineEdit.Password)
        self._fixed_token.setStyleSheet(_input_qss())
        hl.addWidget(self._fixed_token, 1)
        sec.add(host_row, 10)

        fixbtns, fixflow = _row_btns(sec)
        self._fixed_auto = _mini("一键部署固定域名", fixbtns, primary=True)
        self._fixed_auto.clicked.connect(self._open_domain_setup)
        self._fixed_save = _mini("保存固定域名", fixbtns)
        self._fixed_save.clicked.connect(self._apply_fixed_domain)
        self._fixed_clear = _mini("清除固定域名", fixbtns)
        self._fixed_clear.clicked.connect(self._clear_fixed_domain)
        for b in (self._fixed_auto, self._fixed_save, self._fixed_clear):
            fixflow.addWidget(b)
        sec.add(fixbtns, 10)

        peers, pflow2 = _row_btns(sec)
        self._peer_btn = _mini("已配对设备", peers)
        self._peer_btn.clicked.connect(self._open_devices)
        self._notify_btn = _mini("发送通知", peers, primary=True)
        self._notify_btn.clicked.connect(self._open_notify)
        for b in (self._peer_btn, self._notify_btn):
            pflow2.addWidget(b)
        sec.add(peers, 10)

        self._peer_hint = _tip("", sec)
        sec.add(self._peer_hint, 8)

        self._master_only = QCheckBox("仅主机可改数据（从端只读，防误操作）", sec)
        self._master_only.setObjectName("stMasterOnly")
        self._master_only.setStyleSheet(
            f"QCheckBox {{ color: {theme.INK}"
            "; font-size: 13px; background: transparent; }QCheckBox::indicator { width: 16px; height: 16px; }"
        )
        self._master_only.setChecked(
            bool(getattr(self._store.state.settings, "sync_master_only", False))
        )
        self._master_only.toggled.connect(self._on_master_only_toggled)
        sec.add(self._master_only, 8)
        sec.add(_tip(
            "不勾选时手机与电脑同权（都能加分/扣分）。勾选后只有这台主机能改数据，其余设备只能查看——多人同时操作时建议勾上，避免互相覆盖。",
            sec,
        ), 8)
        self._add_page(sec, "多端同步")

    def _load(self) -> None:
        st = self._store.state
        s = st.settings
        lay = st.layout
        self._name.setText(st.class_name)
        self._rows.setValue(int(lay.rows))
        self._cols.setValue(int(lay.cols))
        self._step_m.setValue(int(s.step_morning))
        self._step_n.setValue(int(s.step_noon))
        self._groups.setText("-".join(str(x) for x in lay.col_groups or []))
        pod = lay.podium
        self._podium_on.setChecked(bool(getattr(pod, "enabled", True)))
        pos = getattr(pod, "position", "top")
        idx = self._podium_pos.findData(pos)
        self._podium_pos.setCurrentIndex(idx if idx >= 0 else 0)
        self._podium_span.setValue(int(getattr(pod, "span", 4)))
        idx = self._podium_align.findData(getattr(pod, "align", "center"))
        self._podium_align.setCurrentIndex(idx if idx >= 0 else 1)
        self._threshold.setValue(int(s.prize_threshold))
        self._cost.setValue(int(s.prize_cost))
        idx = self._animation.findData(s.lottery_animation)
        self._animation.setCurrentIndex(idx if idx >= 0 else 0)
        self._top_n.setValue(int(getattr(s, "lottery_top_n", 10)))
        self._apply_lottery_mode(normalize_lottery_mode(
            getattr(s, "lottery_mode", "threshold")))
        self._refresh_group_preview()
        self._refresh_podium_preview()
        self._refresh_prize_mini()
        self._refresh_video_info()
        self._refresh_sync_ui()
        self._refresh_data_dir()
        self._refresh_theme_ui()
        self._fixed_host.setText(getattr(s, "tunnel_hostname", "") or "")
        self._fixed_token.setText(getattr(s, "tunnel_token", "") or "")

    def _parsed_groups(self) -> list[int]:
        raw = self._groups.text().strip()
        if raw in ("", "none", "0"):
            return []
        out = []
        for part in raw.replace("，", "-").replace("、", "-").split("-"):
            part = part.strip()
            if not part:
                continue
            try:
                v = int(part)
            except ValueError:
                return []
            if v > 0:
                out.append(v)
        return out

    def _refresh_group_preview(self) -> None:
        groups = self._parsed_groups()
        cols = self._cols.value()
        if not groups:
            self._group_preview.setText(f"无条件过道（共 {cols} 列连续排列）")
            return
        total = sum(groups)
        extra = f"（合计 {total} 列" + (
            f"，与实际 {cols} 列不一致，将按实际列数截断/补齐）" if total != cols else "）"
        )
        self._group_preview.setText(
            "过道位置：" + " ｜ ".join(str(g) for g in groups) + extra
        )

    def _refresh_podium_preview(self) -> None:
        self._podium_preview.setText(
            f"讲台占 {self._podium_span.value()} 格，"
            f"{self._podium_pos.currentText()}"
        )

    def _refresh_prize_mini(self) -> None:
        prizes = getattr(self._store.state, "prizes", None) or []
        if not prizes:
            self._prize_mini.setText("还没有设置奖品（点「编辑奖品与权重」添加）。")
            return
        self._prize_mini.setText(" ｜ ".join(
            f"{getattr(p, 'name', '?')} {getattr(p, 'weight', 1):g}" for p in prizes[:6]
        ))

    def _refresh_video_info(self) -> None:
        self._video_info.setText("当前未导入视频（本版本暂不支持视频揭晓，会直接亮出结果）。")

    def _apply_group_preset(self, value: str) -> None:
        if value == "none":
            self._groups.setText("")
        elif value == "242":
            self._groups.setText("2-4-2")
        elif value == "332":
            self._groups.setText("3-3-2")
        elif value == "44":
            self._groups.setText("4-4")
        else:
            n = int(value)
            cols = self._cols.value()
            base = cols // n
            rest = cols - base * n
            parts = [base + (1 if i < rest else 0) for i in range(n)]
            self._groups.setText("-".join(str(p) for p in parts if p > 0))
        self._refresh_group_preview()

    def _delegate(self, action: str) -> None:
        """其余分区的按钮交给主窗口里既有的对话框/流程去执行。"""
        host = self.parent()
        hook = getattr(host, "settings_action", None)
        if callable(hook):
            hook(action)

    def _apply_class(self) -> None:
        st = self._store.state
        st.class_name = self._name.text().strip() or "我的班级"
        lay = st.layout
        lay.rows = self._rows.value()
        lay.cols = self._cols.value()
        groups = self._parsed_groups()
        lay.col_groups = groups or [lay.cols]
        pod = lay.podium
        if hasattr(pod, "enabled"):
            pod.enabled = self._podium_on.isChecked()
            pod.position = str(self._podium_pos.currentData() or "top")
            pod.span = self._podium_span.value()
            pod.align = str(self._podium_align.currentData() or "center")
        st.settings.step_morning = self._step_m.value()
        st.settings.step_noon = self._step_n.value()
        self._refresh_group_preview()
        self._refresh_podium_preview()
        self.changed.emit()

    def _save_lottery(self) -> None:
        s = self._store.state.settings
        s.prize_threshold = self._threshold.value()
        s.prize_cost = self._cost.value()
        s.lottery_animation = str(self._animation.currentData() or "none")
        s.lottery_mode = normalize_lottery_mode(self._current_lottery_mode())
        s.lottery_top_n = int(self._top_n.value())
        self.changed.emit()

    def _confirm_clear(self) -> bool:
        """清空前的二次确认；单独成方法，探针可以替身掉（不弹真对话框）。"""
        from PyQt5.QtWidgets import QMessageBox

        box = QMessageBox(self)
        box.setWindowTitle("清空全部数据")
        box.setIcon(QMessageBox.Warning)
        box.setText("确定清空全部数据吗？")
        box.setInformativeText(
            "班级、座位、学生积分、积分记录和抽奖结果都会清空，恢复为全新班级。\n此操作无法撤销，建议先「导出备份 (.json)」。"
        )
        clear_btn = box.addButton("清空全部数据", QMessageBox.DestructiveRole)
        cancel_btn = box.addButton("取消", QMessageBox.RejectRole)
        box.setDefaultButton(cancel_btn)
        box.exec_()
        return box.clickedButton() is clear_btn

    def _clear_all(self) -> None:
        """把状态重置为出厂值（原版「清空全部数据」的语义）。

        走 core 现成的 `Store.replace_state` + `AppState()`：班级名回到
        「我的班级」、座位回到默认布局（6×8、讲台在顶部）、学生/积分记录/
        抽奖结果清空；`default_prizes()` 的默认奖品保留（抽奖还需要它）。
        `changed` 只发一次：主窗口那边会据此落盘并刷新，循环发会让手机端
        读到「清了一半」的中间状态。
        """
        if not self._confirm_clear():
            return
        from app.core.models import AppState

        self._store.replace_state(AppState(), event_reason="wipe")
        self._store.save_now()
        self._load()
        self.changed.emit()

    def _clear_lottery_records(self) -> None:
        """清空抽奖记录（学生、积分、奖品都不受影响）。"""
        from PyQt5.QtWidgets import QMessageBox

        count = len(self._store.state.lottery)
        if not count:
            QMessageBox.information(self, "清空抽奖记录", "现在还没有抽奖记录。")
            return
        box = QMessageBox(self)
        box.setWindowTitle("清空抽奖记录")
        box.setIcon(QMessageBox.Warning)
        box.setText(f"要清空全部 {count} 条抽奖记录吗？")
        box.setInformativeText("学生名单、积分和奖品设置都会保留，只是把「谁抽到过什么」的记录清掉。")
        yes = box.addButton("清空记录", QMessageBox.DestructiveRole)
        box.addButton("取消", QMessageBox.RejectRole)
        box.exec_()
        if box.clickedButton() is not yes:
            return
        removed = apply_clear_lottery(self._store)
        self._load()
        self.changed.emit()
        self._notify(f"已清空 {removed} 条抽奖记录", "ok")

    def _clear_records(self) -> None:
        """只清积分记录，座次表/学生名单/布局原样保留（确认后调 apply_clear_records）。"""
        from PyQt5.QtWidgets import QMessageBox

        box = QMessageBox(self)
        box.setWindowTitle("清空积分记录")
        box.setIcon(QMessageBox.Warning)
        box.setText("要清空所有积分记录吗？")
        box.setInformativeText(
            "座位表、学生名单和班级设置都会保留，但所有人的当前积分会归零，且无法撤销。"
        )
        yes = box.addButton("清空记录", QMessageBox.DestructiveRole)
        box.addButton("取消", QMessageBox.RejectRole)
        box.exec_()
        if box.clickedButton() is not yes:
            return
        apply_clear_records(self._store)
        self._load()
        self.changed.emit()

    def _ensure_token(self) -> str:
        """口令必须稳定：第一次开启时读盘/生成，之后跨重启不变。"""
        if self._sync is None:
            return ""
        cur = self._sync.token
        if cur:
            return cur
        from app.services.sync_server import load_or_create_token

        token = load_or_create_token()
        self._sync.set_token(token)
        return token

    def _sync_link(self) -> str:
        """手机可直接打开的链接（优先公网，其次局域网），带口令。"""
        base = ""
        if self._tunnel is not None and self._tunnel.running and self._tunnel.public_url:
            base = self._tunnel.public_url
        elif self._sync is not None and self._sync.running:
            base = self._sync.url
        if not base:
            return ""
        token = self._sync.token if self._sync is not None else ""
        link = f"{base}/?t={token}" if token else base + "/"
        pair_tok = getattr(self._store.state.settings, "pair_token", "") or ""
        return pairing_mod.attach(link, pair_tok)

    def _paint_qr(self, url: str) -> bool:
        """把手机链接画成二维码。画不出来就返回 False（界面退化成只给文字）。"""
        m = qr_mod.matrix(url)
        if not m:
            self._qr_label.setVisible(False)
            return False
        n = len(m)
        quiet = 2
        scale = max(1, QR_PX // (n + quiet * 2))
        side = (n + quiet * 2) * scale
        off = (QR_PX - side) // 2
        img = QImage(QR_PX, QR_PX, QImage.Format_RGB32)
        img.fill(QColor("#ffffff"))
        p = QPainter(img)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#101828"))
        for y, row in enumerate(m):
            for x, dark in enumerate(row):
                if not dark:
                    continue
                p.drawRect(QRect(
                    off + (x + quiet) * scale,
                    off + (y + quiet) * scale,
                    scale, scale,
                ))
        p.end()
        self._qr_label.setPixmap(QPixmap.fromImage(img))
        self._qr_label.setVisible(True)
        return True

    def _refresh_pair_label(self) -> None:
        """配对令牌可能在任何入口被生成，所以每次刷新都重读一次存档。"""
        if not hasattr(self, "_pair_label"):
            return
        pair_now = (getattr(self._store.state.settings, "pair_token", "") or "").strip()
        self._pair_label.setText(
            f"配对令牌：{pairing_mod.short(pair_now)}" if pair_now
            else "配对令牌：开公网时自动生成"
        )

    def _refresh_sync_ui(self) -> None:
        self._refresh_pair_label()
        if self._sync is None:
            self._sync_hint.setText("同步服务未初始化。")
            self._sync_status.setText("")
            self._qr_label.setVisible(False)
            self._qr_hint.setText("")
            for w in (self._sync_on, self._sync_off, self._copy_btn,
                      self._token_copy, self._token_new,
                      self._pub_on, self._pub_off, self._pub_get):
                w.setEnabled(False)
            return
        token = self._ensure_token()
        self._token_label.setText(f"口令：{token or '—'}")

        if hasattr(self, "_pair_label"):
            pair_now = (getattr(self._store.state.settings, "pair_token", "") or "").strip()
            self._pair_label.setText(
                f"配对令牌：{pairing_mod.short(pair_now)}" if pair_now
                else "配对令牌：开公网时自动生成"
            )
        lan = self._sync.url if self._sync.running else ""
        pub = self._tunnel.public_url if self._tunnel is not None and self._tunnel.running else ""

        self._sync_on.setEnabled(not self._sync.running)
        self._sync_off.setEnabled(self._sync.running)
        self._copy_btn.setEnabled(bool(lan or pub))
        self._token_copy.setEnabled(bool(token))
        self._token_new.setEnabled(True)

        have_exe = bool(tunnel_mod.find_cloudflared())
        # 随包带了 cloudflared 时，「下载穿透组件」按钮没意义
        bundled = tunnel_mod.bundled_cloudflared() is not None
        self._pub_get.setVisible(not bundled)
        self._pub_get.setEnabled(not have_exe)
        self._pub_on.setEnabled(have_exe and not pub)
        self._pub_off.setEnabled(bool(pub))

        if not self._sync.running:
            self._sync_hint.setText(
                "开启后本机就是同步服务器：手机连同一 WiFi 直接打开链接即可。不在同一 WiFi 也能看，点「生成公网地址」。"
            )
            err = str(self._tunnel_error or "")
            if not err and self._tunnel is not None:
                err = str(getattr(self._tunnel, "error", "") or "")
            self._sync_status.setText(f"穿透失败：{err}" if err else "当前未开启。")
            self._qr_label.setVisible(False)
            self._qr_hint.setText("")
            return
        lines = [f"同一 WiFi（局域网）：{lan}/?t={token}"]
        if pub:
            lines.append(f"任意网络（公网）：{pub}/?t={token}")
            self._sync_hint.setText("公网地址已经生成，手机不在同一 WiFi 也能打开上面的链接。这个地址是临时的，关掉就失效。")
        else:
            self._sync_hint.setText(
                "同步已开启，手机连同一 WiFi 即可。不在同一 WiFi 时，点「生成公网地址」就能外网访问。"
                if have_exe
                else "同步已开启（仅限同一 WiFi）。要外网远程查看，点「下载穿透组件」。"
            )
        self._sync_status.setText("\n".join(lines))
        url = qr_mod.best_url(lan, pub, token)
        from app.core import fingerprint as fp_mod
        fpr = fp_mod.class_fingerprint(self._store.state)
        peers = 0
        try:
            peers = len(self._registry().devices)
        except (OSError, ValueError):
            peers = 0
        self._peer_hint.setText(
            f"班级指纹：{fpr}　已配对设备：{peers} 台\n这是这个班自己的身份证：建好就固定，加减人、换座位、换主题都不会变；手机重连时会拿它核对，防止连错班。"
        )
        if self._paint_qr(url):
            self._qr_hint.setText(
                "手机扫码直接打开" + ("（公网地址，外网也能用）" if pub else "（需连同一个 WiFi）")
                + "。二维码里已经带了口令，不用再手输。"
            )
            return
        self._qr_hint.setText("装不上二维码组件（segno）时只能手动输上面的链接；「复制」按钮可以先把链接发到手机上。")

    def _toggle_sync(self) -> None:
        if self._sync is None:
            return
        if self._sync.running:
            if self._tunnel is not None and self._tunnel.running:
                self._tunnel.stop()
            self._sync.stop()
        else:
            self._ensure_token()
            try:
                self._sync.start()
            except OSError as exc:
                self._sync_status.setText(f"开启失败：{exc}")
                return
            self._notify_sync_started()
        self._refresh_sync_ui()

    def _copy_sync_url(self) -> None:
        link = self._sync_link()
        if not link:
            self._sync_status.setText("先点「开启同步」。")
            return
        from PyQt5.QtWidgets import QApplication

        QApplication.clipboard().setText(link)
        self._sync_status.setText(f"已复制手机链接：\n{link}")

    def _client(self):
        from app.services.sync_client import SyncClient

        st = self._store.state.settings
        return SyncClient(
            getattr(st, "sync_client_url", "") or "",
            getattr(st, "sync_client_token", "") or "",
            self._store,
            pair_token=getattr(st, "pair_token", "") or "",
        )

    def _save_client_settings(self, *_args) -> None:
        st = self._store.state.settings
        raw = self._srv_url.text().strip()
        addr, link_token, link_pair = client_mod.parse_pair_link(raw)
        st.sync_client_url = addr or raw
        st.sync_client_token = self._srv_tok.text().strip() or link_token
        if link_pair:
            st.pair_token = link_pair
        st.sync_auto = bool(self._srv_auto.isChecked())
        if st.sync_client_url:
            st.sync_role = "client"
        if addr and addr != raw:
            self._srv_url.setText(addr)
        if link_token and not self._srv_tok.text().strip():
            self._srv_tok.setText(link_token)
        self._store.save_now()

    def _run_diagnose(self) -> None:
        """把「连不上」拆成几条能自己对上的结论，并顺手复制到剪贴板。"""
        from PyQt5.QtWidgets import QApplication, QMessageBox
        from app.services import diagnose

        self._save_client_settings()
        status = ""
        try:
            srv = self._sync
            status = getattr(srv, "status_text", "") or getattr(srv, "status", "") or ""
            status = status if isinstance(status, str) else ""
        except Exception:
            status = ""
        checks = diagnose.run_checks(self._store.state.settings, tunnel_status=status)
        report = diagnose.as_text(checks)
        QApplication.clipboard().setText(report)
        QMessageBox.information(self, "连接体检", report + "\n\n（以上内容已复制到剪贴板）")

    def _test_client(self) -> None:
        self._save_client_settings()
        try:
            info = self._client().probe()
        except Exception as exc:
            self._srv_status.setText(f"连不上：{exc}")
            return
        name = info.get("className") or "（未命名班级）"
        fp = info.get("fingerprint") or "—"
        self._srv_status.setText(f"连接正常：{name} · 班级指纹 {fp}")

    def _sync_client_now(self) -> None:
        self._save_client_settings()
        self._srv_sync.setEnabled(False)
        self._srv_status.setText("正在同步…")
        client = self._client()
        self._run_bg(client.sync_once, self._on_client_synced)

    def _on_client_synced(self, result) -> None:
        self._srv_sync.setEnabled(True)
        if isinstance(result, Exception):
            self._srv_status.setText(f"同步失败：{result}")
            return
        self._store.save_now()
        self._srv_status.setText(
            f"同步完成：{result.summary()}　（{result.className or '主机'}）"
        )
        self.changed.emit()

    def _copy_pair(self) -> None:
        """复制配对令牌（32 位十六进制），供手动输入用。"""
        from PyQt5.QtWidgets import QApplication

        pair_tok = pairing_mod.ensure_token(self._store.state)
        self._store.save_now()
        QApplication.clipboard().setText(pair_tok)
        self._sync_status.setText(f"已复制配对令牌：{pair_tok}")

    def _new_pair(self) -> None:
        """换一个配对令牌：已发出去的二维码/链接都要重新发。"""
        from PyQt5.QtWidgets import QApplication

        pair_tok = pairing_mod.clear_token(self._store.state)
        self._store.save_now()
        QApplication.clipboard().setText(pair_tok)
        self._refresh_sync_ui()
        self._sync_status.setText(
            f"已换新配对令牌（旧二维码与链接需要重新发）：{pair_tok}"
        )

    def _copy_token(self) -> None:
        from PyQt5.QtWidgets import QApplication

        token = self._ensure_token()
        QApplication.clipboard().setText(token)
        self._sync_status.setText(f"已复制口令：{token}")

    def _new_token(self) -> None:
        from app.services.sync_server import new_token, save_token

        token = new_token()
        save_token(token)
        if self._sync is not None:
            self._sync.set_token(token)
        self._refresh_sync_ui()
        self._sync_status.setText(
            f"新口令已生效：{token}\n手机上需要重新输入一次（旧链接会失效）。"
        )

    def _run_bg(self, fn, on_done) -> None:
        task = _BgTask(fn)
        task.done.connect(on_done)
        task.finished.connect(lambda: self._drop_task(task))
        _RUNNING_TASKS.add(task)
        self._tasks.append(task)
        task.start()

    def _drop_task(self, task: QThread) -> None:
        if task in self._tasks:
            self._tasks.remove(task)
        _RUNNING_TASKS.discard(task)

    def _start_tunnel(self) -> None:
        if self._tunnel is None:
            self._sync_status.setText("隧道组件未初始化。")
            return
        if self._sync is not None and not self._sync.running:
            self._ensure_token()
            self._sync.start()
        self._notify_sync_started()
        port = self._sync.port if self._sync is not None else 0
        s = self._store.state.settings
        if not pairing_mod.ensure_token(self._store.state):
            self._sync_status.setText("配对令牌没能生成：班级设置不可写。")
        else:
            self._store.save_now()
        tok = getattr(s, "tunnel_token", "") or ""
        host = getattr(s, "tunnel_hostname", "") or ""
        self._tunnel_error = ""
        self._pub_on.setEnabled(False)
        self._sync_status.setText(
            f"正在连接固定隧道 {host}…" if tok and host
            else "正在生成公网地址…第一次通常要 10～30 秒。"
        )
        self._run_bg(
            lambda: self._tunnel.start(port, token=tok, hostname=host),
            self._on_tunnel_done,
        )

    def _on_tunnel_done(self, ok: bool, payload: str) -> None:
        if ok:
            self._tunnel_error = ""
            self._refresh_sync_ui()
            self._sync_status.setText(f"公网地址已就绪：\n{self._sync_link()}")
            return
        self._tunnel_error = str(payload or "未知错误")
        self._refresh_sync_ui()
        self._sync_status.setText(f"生成失败：{self._tunnel_error}")

    def _stop_tunnel(self) -> None:
        if self._tunnel is not None:
            self._tunnel.stop()
        self._refresh_sync_ui()
        self._sync_status.setText("公网地址已停止（局域网同步不受影响）。")

    def _download_tunnel(self) -> None:
        self._pub_get.setEnabled(False)
        self._sync_status.setText("开始下载穿透组件（约 55 MB），请稍等…")
        self._run_bg(tunnel_mod.download_cloudflared, self._on_download_done)

    def _on_download_done(self, ok: bool, payload: str) -> None:
        if ok:
            self._refresh_sync_ui()
            self._sync_status.setText("穿透组件已就绪，现在可以点「生成公网地址」。")
            return
        self._refresh_sync_ui()
        self._sync_status.setText(f"下载失败：{payload}")

    def _apply_fixed_domain(self) -> None:
        """保存固定隧道令牌与域名，之后点「生成公网地址」走固定隧道。"""
        host = self._fixed_host.text().strip().lower()
        token = self._fixed_token.text().strip()
        host = host.replace("https://", "").replace("http://", "").strip("/")
        if not host:
            self._sync_status.setText("请先填固定域名，例如 jf.example.com。")
            return
        if not token:
            self._sync_status.setText("请把 cloudflare 控制台里的隧道令牌贴进来。")
            return
        if " " in host or "." not in host:
            self._sync_status.setText("固定域名看起来不对，形如 jf.example.com。")
            return
        s = self._store.state.settings
        s.tunnel_hostname = host[:120]
        s.tunnel_token = token[:400]
        self._store.save_now()
        self._peer_hint.setText(f"固定域名已保存：https://{host}")
        self._refresh_sync_ui()
        self._sync_status.setText(
            f"固定域名已保存：https://{host}\n点「生成公网地址」就会用这条隧道，地址以后不再变。"
        )

    def _open_domain_setup(self) -> None:
        """一键部署固定域名：浏览器授权 → 建隧道 → 加解析 → 取令牌，全自动。"""
        from app.ui.dialogs.domain_setup import DomainSetupDialog

        cur = str(getattr(self._store.state.settings, "tunnel_hostname", "") or "")
        dlg = DomainSetupDialog(self, hostname=cur)
        dlg.deployed.connect(self._on_domain_deployed)
        dlg.exec_()
        dlg.deleteLater()

    def _on_domain_deployed(self, info: dict) -> None:
        """部署成功：把地址与令牌写进设置，并立刻用起来。"""
        host = str(info.get("hostname") or "").strip().lower()
        token = str(info.get("token") or "").strip()
        if not host or not token:
            return
        s = self._store.state.settings
        s.tunnel_hostname = host[:120]
        s.tunnel_token = token[:400]
        self._store.save_now()
        self._fixed_host.setText(host)
        self._fixed_token.setText(token)
        self._peer_hint.setText(f"固定域名已配置：https://{host}")
        self._refresh_sync_ui()
        self._sync_status.setText(
            f"固定域名已配置：https://{host}\n隧道令牌已经自动填好，点「生成公网地址」即可使用，地址以后不再变。"
        )
        self.changed.emit()

    def _clear_fixed_domain(self) -> None:
        s = self._store.state.settings
        s.tunnel_hostname = ""
        s.tunnel_token = ""
        self._store.save_now()
        self._fixed_host.setText("")
        self._fixed_token.setText("")
        self._peer_hint.setText("已清除固定域名，下次生成的是随机地址。")
        self._refresh_sync_ui()

    def _notify_sync_started(self) -> None:
        """告诉主窗口「同步已起来，可以开始收通知了」。"""
        win = self.parent()
        hook = getattr(win, "on_sync_started", None)
        if callable(hook):
            hook()

    def _on_master_only_toggled(self, on: bool) -> None:
        self._store.state.settings.sync_master_only = bool(on)
        self._store.save_now()

    def _registry(self) -> 'DeviceRegistry':
        from app.core.devices import DeviceRegistry

        reg = getattr(self, "_device_registry", None)
        if reg is None:
            reg = DeviceRegistry.load()
            self._device_registry = reg
        return reg

    def _open_devices(self) -> None:
        from app.ui.dialogs.device_manager import DeviceManagerDialog

        dlg = DeviceManagerDialog(self._registry(), self)
        dlg.changed.connect(self._refresh_sync_ui)
        dlg.exec_()
        self._refresh_sync_ui()

    def _open_notify(self) -> None:
        from app.ui.dialogs.notify_compose import NotifyComposeDialog

        dlg = NotifyComposeDialog(self._registry(), self)
        if dlg.exec_() != dlg.Accepted:
            return
        payload = dlg.result_payload()
        if not payload:
            return
        if not hasattr(self, "notify_requested"):
            self._sync_status.setText("通知发送未接线，请更新到最新版本。")
            return
        self.notify_requested.emit(payload)
        self._sync_status.setText("通知已发出。")

    def viewer_path(self) -> str:
        """随包附带的手机查看端网页（找不到返回空串）。"""
        import sys
        from pathlib import Path

        base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[3]))
        for cand in (base / "android" / "viewer.html",
                     Path(__file__).resolve().parents[3] / "android" / "viewer.html"):
            if not cand.exists():
                continue
            return str(cand)
        return ""
