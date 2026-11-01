"""加分 / 扣分弹窗：原版 `openScore()` 的 1:1 复刻。

规格来自 ui_ref（nodes-addscore.txt 实测 + css-rules.txt:91-118 + app.js:2665-2747）：

    .modal        width 560（app.js:2699），head 71 / body 356 / foot 68
    .form         flex column，gap 14；body padding 16/18 → 内容 524 宽
    .field        竖排，gap 6；标签 12.5px w600 INK2
    .field-row    横排 3 列（数值 164.5 / 时间 287.5 / 「此刻」52），gap 10
    .field input  边框 LINE，r9，padding 9/11，白底
    .chips        理由胶囊行，gap 8
    .chip         边框 LINE，底 #f9fafb，r999，padding 5/12，12.5px INK2
    .preview      13px INK2，「当前 N 分 → 记分后 M 分」
    .btn          14px w600，padding 9/16，r10；foot 右对齐 gap 10（取消/保存）

只做一件事：问清「给谁、加减多少、什么理由、什么时候」，然后**调 scoring 落账**。
不自己算分、不自己写 history —— 记分入口只有一个，撤销与对账才对得上。
"""
from __future__ import annotations

import time

from PyQt5.QtCore import QDateTime, Qt
from PyQt5.QtWidgets import (
    QComboBox,
    QDateTimeEdit,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from app.core import scoring
from app.core.models import AppState, Student
from app.ui import theme
from app.ui.modals import ModalShell
from app.ui.widgets.calendar import attach_white_calendar
from app.ui.widgets.flow_layout import FlowLayout, flow_height

WIDTH = 560
FORM_GAP = 14
FIELD_GAP = 6
ROW_GAP = 10
CHIP_GAP = 8
CONTENT_W = WIDTH - 36
ROW_AMOUNT_W = 164.5
ROW_BUTTON_W = 52
HEAD_H = 71
FOOT_H = 68


def _field_label(text: str, parent: QWidget) -> QLabel:
    """`.field > span`：12.5px w600 INK2，行盒高 16（ground 实测 span 16）。"""
    lb = QLabel(text, parent)
    lb.setStyleSheet(
        f'QLabel {{ color: {theme.INK2};'
        ' font-size: 12px; font-weight: 700; background: transparent; }'
    )
    # 标签高度钉死，弹窗总高才能按规格算准（head 71 + body 356 + foot 68）。
    lb.setFixedHeight(16)
    return lb


def _input_qss(object_name: str) -> str:
    """`.field input/select`：边框 LINE、r9、padding 7/11、白底。

    高度不在这里钉：原版 select/datetime 是 41、text/number 是 39，
    由各控件自己 setFixedHeight；QSS 里的 min/max-height 会覆盖掉它。"""
    return ''.join([
        'QComboBox#',
        f'{object_name}',
        ', QSpinBox#',
        f'{object_name}',
        ', QLineEdit#',
        f'{object_name}',
        ', QDateTimeEdit#',
        f'{object_name}',
        ' { background: ',
        f'{theme.CARD}',
        '; border: 1px solid ',
        f'{theme.LINE}',
        '; border-radius: 9px; padding: 0px 11px; font-size: 13px; color: ',
        f'{theme.INK}',
        '; }QComboBox#',
        f'{object_name}',
        ':focus, QSpinBox#',
        f'{object_name}',
        ':focus, QLineEdit#',
        f'{object_name}',
        ':focus, QDateTimeEdit#',
        f'{object_name}',
        ':focus { border-color: ',
        f'{theme.BRAND}',
        '; }QComboBox#',
        f'{object_name}',
        '::drop-down { border: none; width: 18px; }QComboBox#',
        f'{object_name}',
        ' QAbstractItemView { background: ',
        f'{theme.CARD}',
        '; border: 1px solid ',
        f'{theme.LINE}',
        '; border-radius: 8px; }QSpinBox#',
        f'{object_name}',
        '::up-button, QSpinBox#',
        f'{object_name}',
        '::down-button, QDateTimeEdit#',
        f'{object_name}',
        '::up-button, QDateTimeEdit#',
        f'{object_name}',
        '::down-button { width: 0px; height: 0px; border: none; background: transparent; }',
    ])


CHIP_QSS = (
    f"""
QPushButton {{
    background: {theme.BTN_GHOST_BG}; border: 1px solid {theme.LINE};
    border-radius: 999px; padding: 5px 12px; font-size: 12px;
    color: {theme.INK2}; min-height: 16px; max-height: 16px;
}}
QPushButton:hover {{
    background: #eef3ff; border-color: #c7d2fe; color: {theme.BRAND_DARK};
}}
"""
)

FOOT_BTN_QSS = (
    f"""
QPushButton {{
    background: {theme.CARD}; border: 1px solid {theme.LINE};
    border-radius: 10px; padding: 9px 16px; font-size: 14px;
    font-weight: 700; color: {theme.INK};
}}
QPushButton:hover {{ background: {theme.BTN_GHOST_BG}; }}
QPushButton#primary {{
    background: {theme.BRAND}; border-color: {theme.BRAND}; color: #ffffff;
}}
QPushButton#primary:hover {{
    background: {theme.BRAND_DARK}; border-color: {theme.BRAND_DARK};
}}
"""
)


class ManualScoreDialog(ModalShell):
    """给单个学生记一笔分。exec_() 返回后读 result_entry / result_message。"""

    def __init__(
        self,
        state: AppState,
        student: Student | None,
        sign: int = 1,
        parent: QWidget | None = None,
    ) -> None:
        self._state = state
        self._student = student
        self._sign = 1 if sign >= 0 else -1
        self.result_entry = None
        self.result_message = ''
        self.result_student_id = ''
        title = '加分' if self._sign > 0 else '扣分'
        super().__init__(title, '记分时间默认取系统当前时间，可手动修改',
                         parent, width=WIDTH)
        self._build()
        self._load(student)

    def _build(self) -> None:
        body = self.body_layout()
        body.setSpacing(FORM_GAP)
        form = QWidget(self.content_widget())
        form.setStyleSheet('background: transparent;')
        form_lay = QVBoxLayout(form)
        form_lay.setContentsMargins(0, 0, 0, 0)
        form_lay.setSpacing(FORM_GAP)
        # .form 的三个 .field（学生 / 数值+时间+此刻 / 原因+胶囊）
        form_lay.addWidget(self._build_student_field(form))
        form_lay.addWidget(self._build_row(form))
        form_lay.addWidget(self._build_reason_field(form))
        form_lay.addWidget(self._build_chips(form))
        # .preview 13px，行盒 17（18 实测取整由 356 的总高吸收）
        self._preview = QLabel('', form)
        self._preview.setObjectName('scPreview')
        self._preview.setStyleSheet(
            f'QLabel {{ color: {theme.INK2};'
            ' font-size: 13px; background: transparent; }'
        )
        self._preview.setFixedHeight(17)
        form_lay.addWidget(self._preview)
        # 内容不足 356 时把空隙压到底部，保持与原版同样的顶部对齐。
        form_lay.addStretch(1)
        body.addWidget(form, 1)
        cancel = self.add_foot_button('取消')
        cancel.setStyleSheet(FOOT_BTN_QSS)
        cancel.clicked.connect(self.reject)
        save = self.add_foot_button('保存', 'primary')
        save.setStyleSheet(FOOT_BTN_QSS)
        save.clicked.connect(self._commit)
        self._save_btn = save
        # 打开即定高：560 x (71 + 356 + 68) = ground 的 .modal 尺寸。
        self.resize(WIDTH, HEAD_H + 356 + FOOT_H)

    def _build_student_field(self, parent: QWidget) -> QWidget:
        wrap = QWidget(parent)
        wrap.setStyleSheet('background: transparent;')
        lay = QVBoxLayout(wrap)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(FIELD_GAP)
        lay.addWidget(_field_label('学生', wrap))
        self._student_box = QComboBox(wrap)
        self._student_box.setObjectName('scStudent')
        self._student_box.setStyleSheet(_input_qss('scStudent'))
        for s in self._state.students:
            self._student_box.addItem(
                f'{s.name}（第{s.row + 1}排{s.col + 1}座 · {s.points}分）', s.id
            )
        self._student_box.setFixedHeight(41)
        lay.addWidget(self._student_box)
        return wrap

    def _build_row(self, parent: QWidget) -> QWidget:
        row = QFrame(parent)
        row.setObjectName('scFieldRow')
        row.setStyleSheet('QFrame#scFieldRow { background: transparent; border: none; }')
        grid = QGridLayout(row)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(ROW_GAP)
        grid.setVerticalSpacing(0)
        # .field-row：数值 164.5 / 时间 287.5 / 此刻 52（ground 实测），gap 10。
        amount_wrap = QWidget(row)
        amount_wrap.setStyleSheet('background: transparent;')
        al = QVBoxLayout(amount_wrap)
        al.setContentsMargins(0, 0, 0, 0)
        al.setSpacing(FIELD_GAP)
        label = '加分数值' if self._sign > 0 else '扣分数值'
        al.addWidget(_field_label(label, amount_wrap))
        self._amount = QSpinBox(amount_wrap)
        self._amount.setObjectName('scValue')
        self._amount.setRange(1, 999)
        self._amount.setValue(1)
        self._amount.setStyleSheet(_input_qss('scValue'))
        self._amount.setFixedHeight(39)
        al.addWidget(self._amount)
        time_wrap = QWidget(row)
        time_wrap.setStyleSheet('background: transparent;')
        tl = QVBoxLayout(time_wrap)
        tl.setContentsMargins(0, 0, 0, 0)
        tl.setSpacing(FIELD_GAP)
        tl.addWidget(_field_label('时间', time_wrap))
        self._time = QDateTimeEdit(time_wrap)
        self._time.setObjectName('scTime')
        self._time.setCalendarPopup(True)
        # 日历弹窗单独白底：应用级深色 QSS 会把它染黑（QDateTimeEdit 的坑）。
        self._calendar = attach_white_calendar(self._time)
        self._time.setDisplayFormat('yyyy-MM-dd HH:mm')
        self._time.setDateTime(QDateTime.currentDateTime())
        self._time.setStyleSheet(_input_qss('scTime'))
        self._time.setFixedHeight(41)
        tl.addWidget(self._time)
        self._now_btn = QPushButton('此刻', row)
        self._now_btn.setObjectName('scNow')
        self._now_btn.setCursor(Qt.PointingHandCursor)
        self._now_btn.setFixedHeight(31)
        self._now_btn.setStyleSheet(
            f'QPushButton {{ background: transparent; border: 1px solid {theme.LINE};'
            f' border-radius: 9px; padding: 6px 12px; font-size: 13px; font-weight: 700; color: {theme.INK};'
            f' }}QPushButton:hover {{ background: {theme.PILL_GRAY_BG}; }}'
        )
        self._now_btn.clicked.connect(
            lambda: self._time.setDateTime(QDateTime.currentDateTime())
        )
        grid.addWidget(amount_wrap, 0, 0)
        grid.addWidget(time_wrap, 0, 1)
        grid.addWidget(self._now_btn, 0, 2, Qt.AlignBottom)
        amount_wrap.setFixedWidth(int(ROW_AMOUNT_W))
        time_wrap.setFixedWidth(
            int(CONTENT_W - ROW_AMOUNT_W - ROW_BUTTON_W - ROW_GAP * 2)
        )
        self._now_btn.setFixedWidth(ROW_BUTTON_W)
        grid.setColumnStretch(2, 1)
        return row

    def _build_reason_field(self, parent: QWidget) -> QWidget:
        wrap = QWidget(parent)
        wrap.setStyleSheet('background: transparent;')
        lay = QVBoxLayout(wrap)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(FIELD_GAP)
        lay.addWidget(_field_label('原因', wrap))
        self._reason = QLineEdit(wrap)
        self._reason.setObjectName('scReason')
        self._reason.setPlaceholderText('例如：课堂积极回答')
        self._reason.setMaxLength(80)
        self._reason.setStyleSheet(_input_qss('scReason'))
        self._reason.setFixedHeight(39)
        lay.addWidget(self._reason)
        return wrap

    def _build_chips(self, parent: QWidget) -> QWidget:
        """`.chips` 理由胶囊行：原版 flex-wrap，一行放不下就换行。

        ground 实测：容器 524x64（两行 28 + gap 8），第 6 个胶囊落到第二行。
        这里用 FlowLayout 复刻换行；容器高度由内容算，不再钉死 31。
        """
        wrap = QWidget(parent)
        wrap.setStyleSheet('background: transparent;')
        flow = FlowLayout(wrap, h_spacing=CHIP_GAP, v_spacing=CHIP_GAP)
        self._chips = []
        for text in self._presets()[:8]:
            chip = QPushButton(text, wrap)
            chip.setObjectName('scChip')
            chip.setCursor(Qt.PointingHandCursor)
            chip.setStyleSheet(CHIP_QSS)
            chip.setFixedHeight(28)
            # 宽度按字数估：12.5px 字 + padding 12/12，中文近似等宽（ground 33~107）。
            chip.setFixedWidth(int(round(26 + 12.5 * len(text))))
            chip.clicked.connect(lambda _=False, t=text: self._reason.setText(t))
            flow.addWidget(chip)
            self._chips.append(chip)
        # 高度用真实 sizeHint 逐行算，两行时 = 28*2 + 8。
        wrap.setFixedHeight(flow_height(self._chips, CONTENT_W, CHIP_GAP, CHIP_GAP))
        return wrap

    def _presets(self) -> list[str]:
        st = self._state
        return list(st.settings.plus_reasons if self._sign > 0
                    else st.settings.minus_reasons)

    def _load(self, student: Student | None) -> None:
        if student is not None:
            idx = self._student_box.findData(student.id)
            if idx >= 0:
                self._student_box.setCurrentIndex(idx)
        self._amount.valueChanged.connect(self._refresh_preview)
        self._student_box.currentIndexChanged.connect(self._refresh_preview)
        self._reason.returnPressed.connect(self._commit)
        self._refresh_preview()

    def _current_student(self) -> Student | None:
        sid = self._student_box.currentData()
        return self._state.student_by_id(sid) if sid else None

    def _refresh_preview(self) -> None:
        """原版 updatePreview()：`当前 N 分 → 记分后 M 分`。"""
        s = self._current_student()
        if s is None:
            self._preview.setText('')
            self._save_btn.setEnabled(False)
            return
        v = self._amount.value()
        after = s.points + (v if self._sign > 0 else -v)
        color = theme.OK if after >= s.points else theme.BAD
        self._preview.setText(
            f"当前 <b>{s.points}</b> 分 → 记分后 <b style='color:"
            f"{color}'>"
            f'{after}</b> 分'
        )
        self._preview.setTextFormat(Qt.RichText)
        self._save_btn.setEnabled(True)

    def _timestamp(self) -> int:
        """QDateTime → 毫秒时间戳（原版 fromLocalInput）。"""
        dt = self._time.dateTime()
        if dt.isValid():
            return int(dt.toMSecsSinceEpoch())
        return int(time.time() * 1000)

    def _commit(self) -> None:
        s = self._current_student()
        if s is None:
            return
        v = self._amount.value()
        if v <= 0:
            return
        delta = v if self._sign > 0 else -v
        reason = self._reason.text().strip()
        entry = scoring.add_points(
            self._state, s.id, delta, reason=reason, source='manual'
        )
        if entry is None:
            self.result_message = '没有记上分（分值为 0 或学生不存在）'
            self.accept()
            return
        self.result_entry = entry
        self.result_student_id = s.id
        tail = f'（{reason}）' if reason else ''
        self.result_message = f'{entry.student_name} {delta:+d} 分{tail}'
        self.accept()
