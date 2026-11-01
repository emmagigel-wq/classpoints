"""底部浮出的短提示（原版 #toast 的 1:1 复刻）。

规格来自 ui_ref（1440x900 实测 ground-default.json + css-rules.txt:273-277）：

    #toast      position fixed，bottom 96px，水平居中，flex column gap 8
    .toast      bg rgba(16,24,40,.92) 白字 13.5px w600 padding 10/18
                border-radius 999px（胶囊）box-shadow rgba(16,24,40,.25) 0 8px 24px
                max-width 80vw，文字居中
    .toast.ok   bg #027a48      .toast.bad bg #b42318      .toast.warn bg #b54708

两条硬规矩（分层测试会抓）：
  * 自动消失一律走 TimerRegistry，界面层不许自己开 QTimer；
  * 连发多次是**叠成多条**（app.js 每次 appendChild），不是替换——
    每条一个独立的 hide key（toast:{n}:hide），所以后一条不会把前一条提前收走。

叠加上限 MAX_STACK：原版不设限，但同一秒连点十几次会糊满半屏，这里截到 4 条封顶。
"""
from __future__ import annotations

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont, QFontMetrics
from PyQt5.QtWidgets import QLabel, QVBoxLayout, QWidget

from app.services.timer_registry import TimerRegistry
from app.ui import theme

SCOPE = 'toast'
BOTTOM_GAP = 96
GAP = 8
MAX_STACK = 4
MAX_WIDTH_VW = 0.8
CAPSULE_FONT_PX = 13
CAPSULE_PAD_X = 18

KIND_BG = {
    '': theme.TOAST_BG,
    'ok': theme.TOAST_OK,
    'bad': theme.TOAST_BAD,
    'warn': theme.TOAST_WARN,
}


class Toast(QLabel):
    """底部提示栈：一个容器 + 若干条胶囊。

    容器本身是个 QLabel（主窗口按 label 定位：adjustSize()/move()/raise_()），
    里面用 QVBoxLayout 自下往上叠条目。
    """

    def __init__(self, registry: TimerRegistry, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._registry = registry
        self._items = []
        self._seq = 0
        self.setObjectName('toast')
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(0, 0, 0, 0)
        self._lay.setSpacing(GAP)
        self._lay.addStretch(1)
        self.setVisible(False)

    def show_message(self, text: str, ms: int = 2200, kind: str = '') -> None:
        """追加一条提示；到点自己消失（TimerRegistry，scope='toast'）。"""
        text = (text or '').strip()
        if not text:
            return
        capsule = QLabel(text, self)
        capsule.setObjectName('toastCapsule')
        capsule.setAlignment(Qt.AlignCenter)
        capsule.setWordWrap(True)
        capsule.setStyleSheet(_capsule_qss(kind))
        # 宽度按文字量出来钉死：不钉的话 QSS 的 padding 会把胶囊撑得忽宽忽窄。
        capsule.setFixedWidth(self._measure_width(text))
        self._lay.insertWidget(self._lay.count() - 1, capsule)
        self._items.append(capsule)
        self._seq += 1
        key = f'toast:{self._seq}:hide'
        self._registry.schedule(
            key, max(400, int(ms)), lambda: self._expire(capsule), scope=SCOPE
        )
        self._trim()
        self.setVisible(True)
        self.raise_()

    def hide_message(self) -> None:
        """清空整个提示栈（主窗口收尾 / 关机时用）。"""
        for capsule in list(self._items):
            self._drop(capsule)
        self.setVisible(False)

    def current_text(self) -> str:
        """**最新一条**的文字；没有可见条目时返回空串。"""
        if not self.isVisible():
            return ''
        for capsule in reversed(self._items):
            if capsule.text():
                return capsule.text()
        return ''

    def toast_count(self) -> int:
        return len(self._items)

    def messages(self) -> list[str]:
        return [c.text() for c in self._items]

    def _expire(self, capsule: QLabel) -> None:
        self._drop(capsule)
        if not self._items:
            self.setVisible(False)

    def _drop(self, capsule: QLabel) -> None:
        if capsule not in self._items:
            return
        self._items.remove(capsule)
        self._lay.removeWidget(capsule)
        capsule.setParent(None)
        capsule.deleteLater()

    def _trim(self) -> None:
        while len(self._items) > MAX_STACK:
            self._drop(self._items[0])

    def _measure_width(self, text: str) -> int:
        """胶囊宽度 = 文字宽度 + 左右内边距，并限制在 max-width 80vw 内。"""
        fm = QFontMetrics(self._capsule_font())
        natural = fm.horizontalAdvance(text) + CAPSULE_PAD_X * 2
        # 容器还没 show 时 width() 是默认值，优先拿父窗口的宽当基准。
        parent_w = self.parentWidget().width() if self.parentWidget() else 0
        base = parent_w or self.width() or 960
        limit = max(160, int(base * MAX_WIDTH_VW))
        return max(80, min(natural, limit))

    @staticmethod
    def _capsule_font() -> QFont:
        """和胶囊 QSS 里的字号/字重保持一致，量宽度才准。"""
        font = QFont()
        font.setPixelSize(CAPSULE_FONT_PX)
        font.setWeight(QFont.Bold)
        return font

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        limit = int(self.width() * MAX_WIDTH_VW) or self.width()
        for capsule in self._items:
            capsule.setMaximumWidth(max(80, limit))


def _capsule_qss(kind: str) -> str:
    bg = KIND_BG.get(kind, KIND_BG[''])
    return (
        'QLabel#toastCapsule { background: '
        f'{bg}'
        '; color: #ffffff; padding: 10px 18px; border-radius: 19px; font-size: '
        # 999px 圆角在 Qt 里会连成整圆，用「高的一半」=19px 更像原版。
        f'{CAPSULE_FONT_PX}'
        'px; font-weight: 700;}'
    )
