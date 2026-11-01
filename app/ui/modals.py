"""通用模态壳：原版 `.modal / .modal-head / .modal-body / .modal-foot` 的 1:1 复刻。

规格来自 ui_ref（ground-settings.json / ground-addscore.json 实测 + css-rules.txt:91-100）：

    .modal-back  全屏遮罩 rgba(16,24,40,.45) + blur(3px)，居中，padding 24
                 （Qt 里由模态对话框本身承担，不再画遮罩）
    .modal       白底，圆角 16，投影 rgba(16,24,40,.3) 0 20 60；
                 宽 520（.modal.wide → 1000），纵向 flex
    .modal-head  padding 16/18/12，下边框 LINE；h3 17px；p 12.5px INK3
    .modal-body  padding 16/18，可滚动，flex:1
    .modal-foot  padding 12/18/16，上边框 LINE，右对齐 gap 10

原版没有系统标题栏，标题在 .modal-head 里、关闭是右上角的「×」（.icon-btn 22px INK3）。
这里照做：窗口无边框，head 可拖动，Escape 关闭。

用法：
    dlg = ModalShell("设置", "班级、积分规则与数据", self)
    dlg.body_layout().addWidget(view)
    dlg.add_foot_button("取消", "ghost").clicked.connect(dlg.reject)
    ok = dlg.add_foot_button("保存", "primary")
    dlg.set_accept_enabled(False)
    dlg.exec_()
"""
from __future__ import annotations

from PyQt5.QtCore import QPoint, Qt
from PyQt5.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from app.ui import theme

NARROW_W = 520
WIDE_W = 1000
MIN_H = 220
MAX_H = 852
HEAD_H = 55
HEAD_H_SUB = 71
FOOT_H = 68
HEAD_PAD = (16, 18, 12, 18)
BODY_PAD = (16, 18, 16, 18)
FOOT_PAD = (12, 18, 16, 18)
TITLE_LINE_H = 22
SUB_LINE_H = 16
CANCEL_OBJECT = 'ghost'
ACCEPT_OBJECT = 'primary'


class ModalShell(QDialog):
    """一个模态窗口：head（标题 + 副题 + ×）+ 可滚动 body + 右对齐 foot。"""

    def __init__(
        self,
        title: str,
        subtitle: str = '',
        parent: QWidget | None = None,
        wide: bool = False,
        width: int | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setModal(True)
        self.setAttribute(Qt.WA_TranslucentBackground, False)
        self._drag_from = None
        # 外壳：白底圆角面板，四周不留缝（遮罩由模态本身承担）。
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self._shell = QFrame(self)
        self._shell.setObjectName('modal')
        self._shell.setStyleSheet(_SHELL_QSS)
        outer.addWidget(self._shell)
        lay = QVBoxLayout(self._shell)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self._build_head(title, subtitle))
        lay.addWidget(self._build_body(), 1)
        self._foot = self._build_foot()
        lay.addWidget(self._foot)
        w = int(width) if width else (WIDE_W if wide else NARROW_W)
        self._width = w
        # Qt 的 setFixedWidth 在窗口显示时会被平台层重置，showEvent 里再钉一次；
        # 这里先设一遍，让首次布局就拿到正确宽度。
        self.setFixedWidth(w)
        self.resize(w, MIN_H)

    def _build_head(self, title: str, subtitle: str) -> QFrame:
        head = QFrame(self._shell)
        head.setObjectName('modalHead')
        head.setStyleSheet(_HEAD_QSS)
        lay = QHBoxLayout(head)
        lay.setContentsMargins(*_ltr(HEAD_PAD))
        lay.setSpacing(12)
        texts = QVBoxLayout()
        texts.setContentsMargins(0, 0, 0, 0)
        texts.setSpacing(4)
        self._title = QLabel(title, head)
        self._title.setObjectName('modalTitle')
        self._title.setStyleSheet(
            f'QLabel#modalTitle {{ color: {theme.INK};'
            ' font-size: 17px; font-weight: 700; background: transparent; }'
        )
        # 行高钉死：h3 25.5px、p 12.5px，取整后用固定高度算窗口总高。
        self._title.setFixedHeight(TITLE_LINE_H)
        texts.addWidget(self._title)
        self._subtitle = QLabel(subtitle, head)
        self._subtitle.setObjectName('modalSub')
        self._subtitle.setStyleSheet(
            f'QLabel#modalSub {{ color: {theme.INK3};'
            ' font-size: 12px; background: transparent; }'
        )
        self._subtitle.setWordWrap(True)
        self._subtitle.setFixedHeight(SUB_LINE_H)
        self._subtitle.setVisible(bool(subtitle))
        texts.addWidget(self._subtitle)
        lay.addLayout(texts, 1)
        self._close_btn = QPushButton('×', head)
        self._close_btn.setCursor(Qt.PointingHandCursor)
        self._close_btn.setFixedSize(34, 26)
        self._close_btn.setStyleSheet(
            f'QPushButton {{ background: transparent; border: none; color: {theme.INK3};'
            f' font-size: 22px; padding: 0px; }}QPushButton:hover {{ background: {theme.PILL_GRAY_BG};'
            f' color: {theme.INK}; border-radius: 8px; }}'
        )
        self._close_btn.clicked.connect(self.reject)
        lay.addWidget(self._close_btn, 0, Qt.AlignTop)
        self._head = head
        return head

    def _build_body(self) -> QScrollArea:
        self._scroll = QScrollArea(self._shell)
        self._scroll.setObjectName('modalBody')
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        # 横向滚动条一律藏掉：宽已由 _width 钉死，出现横条就是布局在漏算。
        # 纵向保留——body 超高时在壳内滚，窗口本身不涨。
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._scroll.setStyleSheet(_BODY_QSS)
        self._content = QWidget(self._scroll)
        self._content.setStyleSheet('background: transparent;')
        self._body = QVBoxLayout(self._content)
        self._body.setContentsMargins(*_ltr(BODY_PAD))
        self._body.setSpacing(14)
        self._scroll.setWidget(self._content)
        return self._scroll

    def _build_foot(self) -> QFrame:
        foot = QFrame(self._shell)
        foot.setObjectName('modalFoot')
        foot.setStyleSheet(_FOOT_QSS)
        lay = QHBoxLayout(foot)
        lay.setContentsMargins(*_ltr(FOOT_PAD))
        lay.setSpacing(10)
        lay.addStretch(1)
        self._foot_lay = lay
        foot.setVisible(False)
        return foot

    def body_layout(self) -> QVBoxLayout:
        """内容区布局（padding 16/18 已设好）。"""
        return self._body

    def add_foot_button(self, text: str, object_name: str = '') -> QPushButton:
        """在右下角加一个按钮并返回它（默认风格 = .btn，'primary'=主按钮）。"""
        btn = QPushButton(text, self._foot)
        if object_name:
            btn.setObjectName(object_name)
        btn.setCursor(Qt.PointingHandCursor)
        self._foot_lay.addWidget(btn)
        self._foot.setVisible(True)
        return btn

    def set_accept_enabled(self, enabled: bool) -> None:
        """把 foot 里 objectName='primary' 的按钮置灰/启用。"""
        for btn in self._foot.findChildren(QPushButton):
            if btn.objectName() == ACCEPT_OBJECT:
                btn.setEnabled(bool(enabled))

    def content_widget(self) -> QWidget:
        return self._content

    def fit_to_content(self, max_h: int = MAX_H) -> int:
        """按内容高度收放窗口。

        原版 `.modal` 的高度由内容决定（`.modal-back` 居中、body 超出则内部滚动）。
        Qt 侧 QDialog 不会自动长高——`_panel()` 只设了宽度，窗口就一直停在
        MIN_H=220，用户看到的就是「弹窗开了但里面几乎空白」。
        """
        head_h = HEAD_H_SUB if self._subtitle.isVisible() else HEAD_H
        foot_h = FOOT_H if self._foot.isVisible() else 0
        hint = self._content.sizeHint().height()
        body_h = max(120, int(hint) + BODY_PAD[0] + BODY_PAD[2])
        total = min(int(max_h), max(MIN_H, head_h + body_h + foot_h))
        self.setFixedHeight(total)
        return total

    def foot_widget(self) -> QFrame:
        return self._foot

    def set_subtitle(self, text: str) -> None:
        self._subtitle.setText(text)
        self._subtitle.setVisible(bool(text))

    def set_button_order(self, accept_first: bool = True) -> None:
        """原版 modal-foot 里按钮的 DOM 顺序：取消先、确定后（right 定位）。"""
        lay = self._foot.layout()
        while lay.count():
            it = lay.takeAt(0)
            if it.widget() is not None:
                it.widget().setParent(None)
        order = [self._accept, self._cancel] if accept_first else [self._cancel, self._accept]
        for btn in order:
            lay.addWidget(btn)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        # 平台层会在首次显示时清掉固定宽度，这里补钉一次。
        self.setFixedWidth(self._width)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Escape:
            self.reject()
            return
        super().keyPressEvent(event)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton and self._in_head(event.pos()):
            self._drag_from = event.globalPos() - self.frameGeometry().topLeft()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._drag_from is not None and event.buttons() & Qt.LeftButton:
            self.move(event.globalPos() - self._drag_from)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        self._drag_from = None
        super().mouseReleaseEvent(event)

    def _in_head(self, pos: QPoint) -> bool:
        head = self._head
        top_left = head.mapTo(self, QPoint(0, 0))
        return head.rect().translated(top_left).contains(pos)


def _ltr(pad: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    """CSS 顺序（上/右/下/左）→ Qt 顺序（左/上/右/下）。"""
    top, right, bottom, left = pad
    return (left, top, right, bottom)


_SHELL_QSS = f"""
QFrame#modal {{
    background: {theme.CARD};
    border: none;
    border-radius: 16px;
}}
"""

_HEAD_QSS = f"""
QFrame#modalHead {{
    background: transparent;
    border: none;
    border-bottom: 1px solid {theme.LINE};
}}
"""

_BODY_QSS = """
QScrollArea#modalBody { background: transparent; border: none; }
QScrollArea#modalBody > QWidget > QWidget { background: transparent; }
"""

_FOOT_QSS = f"""
QFrame#modalFoot {{
    background: transparent;
    border: none;
    border-top: 1px solid {theme.LINE};
}}
"""
