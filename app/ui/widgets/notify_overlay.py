"""通知浮层：轻度的顶上滑一条，严重的/紧急的占满窗口。

为什么不用 QTimer：项目分层测试禁止 ui 层直接建计时器，所有延时统一走
TimerRegistry，这样窗口关闭时 ``shutdown()`` 能把待执行的回调一起收掉。
"""
from __future__ import annotations

from typing import Any

from PyQt5.QtCore import QEvent, Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.ui import theme

# 横幅自动收起的时长：原版 mild 通知 3 秒。
BANNER_MS = 3000

MODE_LABELS = {
    'mild': ('提醒', theme.PRIMARY),
    'severe': ('重要', theme.WARNING),
    'urgent': ('紧急', theme.DANGER),
}


def _mode_labels(mode: str) -> tuple[str, str]:
    return MODE_LABELS.get(mode, MODE_LABELS['mild'])


def _norm_mode(mode: Any) -> str:
    text = str(mode or '').strip().lower()
    return text if text in MODE_LABELS else 'mild'


class _Banner(QFrame):
    """顶部横幅：点一下看详情，不点就自己在 BANNER_MS 后消失。"""

    clicked = pyqtSignal(int)

    def __init__(self, item: dict, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._id = int(item.get('id') or 0)
        name, color = _mode_labels(_norm_mode(item.get('mode')))
        self.setObjectName('notifyBanner')
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet(
            f'QFrame#notifyBanner {{ background: {theme.CARD}; border: 1px solid {theme.LINE};'
            f' border-left: 4px solid {color}; border-radius: 12px; }}'
            f'QLabel#nbTitle {{ color: {theme.INK}; font-size: 14px; font-weight: 700; background: transparent; }}'
            f'QLabel#nbText {{ color: {theme.INK2}; font-size: 12.5px; background: transparent; }}'
            f'QLabel#nbMode {{ color: {color}; font-size: 12px; font-weight: 700; background: transparent; }}'
        )
        # 单行横幅只留「徽标 + 标题（+副题）」，放不下更多。
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(10)
        badge = QLabel(name, self)
        badge.setObjectName('nbMode')
        lay.addWidget(badge)
        col = QVBoxLayout()
        col.setSpacing(2)
        title = QLabel(str(item.get('title') or '（无标题）')[:60], self)
        title.setObjectName('nbTitle')
        col.addWidget(title)
        text = str(item.get('text') or '').strip()
        if text:
            sub = QLabel(text[:80], self)
            sub.setObjectName('nbText')
            col.addWidget(sub)
        lay.addLayout(col, 1)

    def item_id(self) -> int:
        return self._id

    def mousePressEvent(self, event) -> None:
        self.clicked.emit(self._id)
        super().mousePressEvent(event)


class NotifyOverlay(QWidget):
    """铺在主窗口上的通知层。平时透明且不吃鼠标事件。"""

    acknowledged = pyqtSignal(int)
    download_requested = pyqtSignal(int)

    def __init__(self, parent: QWidget | None = None, registry=None) -> None:
        super().__init__(parent)
        self._registry = registry if registry is not None else getattr(
            parent, '_registry', None
        )
        self._banner = None
        self._mask = None
        self._queue = []
        self._banner_queue = []
        # id -> 原始通知 dict：横幅点击时要靠它找回详情。
        self._items = {}
        self._clearing = False
        self._presenting = False
        self.setObjectName('notifyOverlay')
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setStyleSheet('QWidget#notifyOverlay { background: transparent; }')
        self.hide()
        if parent is not None:
            parent.installEventFilter(self)

    def eventFilter(self, obj, event):
        if obj is self.parent() and event.type() in (QEvent.Resize, QEvent.Show):
            self.setGeometry(self.parent().rect())
        return super().eventFilter(obj, event)

    def _sync_geometry(self) -> None:
        parent = self.parent()
        if parent is not None:
            self.setGeometry(parent.rect())

    def present_many(self, items: list[dict]) -> int:
        """按顺序处理一批通知，返回真正显示出来的条数。

        同批里的「轻微」通知以前是一条盖一条，用户只看得到最后一条；
        现在全部排队，当前横幅消失后依次弹出来。
        """
        shown = 0
        for item in items or []:
            if not isinstance(item, dict):
                continue
            notify_id = int(item.get('id') or 0)
            if notify_id:
                self._items[notify_id] = item
            if _norm_mode(item.get('mode')) == 'mild':
                self._banner_queue.append(item)
                shown += 1
            else:
                self._queue.append(item)
        if self._banner is None:
            self._pump_banners()
        if self._queue and self._mask is None:
            self._present_mask(self._queue.pop(0))
        return shown + len(self._queue)

    def _pump_banners(self) -> None:
        """当前没有横幅时，从队列里取出下一条显示。"""
        if self._clearing or self._presenting:
            return
        if self._banner is not None or not self._banner_queue:
            return
        self._present_banner(self._banner_queue.pop(0))

    def _present_banner(self, item: dict) -> None:
        # 显示横幅期间挡住 _dismiss_banner 里的续弹，避免递归弹下一条。
        self._presenting = True
        try:
            self._dismiss_banner()
        finally:
            self._presenting = False
        banner = _Banner(item, self)
        banner.clicked.connect(self._on_banner_clicked)
        self._banner = banner
        self._sync_geometry()
        self.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        self.show()
        self.raise_()
        self._layout_banner()
        if self._registry is not None:
            self._registry.schedule(
                'notify:banner',
                BANNER_MS,
                self._dismiss_banner,
                scope='global',
                replace=True,
            )

    def _layout_banner(self) -> None:
        if self._banner is None:
            return
        width = min(460, max(280, self.width() - 40))
        self._banner.setFixedWidth(width)
        self._banner.adjustSize()
        self._banner.move((self.width() - width) // 2, 18)

    def _dismiss_banner(self) -> None:
        if self._registry is not None:
            self._registry.cancel('notify:banner')
        if self._banner is not None:
            self._banner.setParent(None)
            self._banner.deleteLater()
            self._banner = None
        if self._mask is None and self._banner_queue:
            # 上一条收掉后接着弹队列里的下一条。
            self._pump_banners()
            return
        if self._mask is None:
            self.hide()
            self.setAttribute(Qt.WA_TransparentForMouseEvents, True)

    def _on_banner_clicked(self, notify_id: int) -> None:
        # 点横幅 = 直接展开详情卡片（轻度的也进遮罩，看完整内容再确认）。
        item = self._items.get(notify_id)
        if not item:
            return
        self._dismiss_banner()
        self._present_mask(item, lightweight=True)

    def _present_mask(self, item: dict, lightweight: bool = False) -> None:
        if self._mask is not None:
            self._mask.setParent(None)
            self._mask.deleteLater()
            self._mask = None
        notify_id = int(item.get('id') or 0)
        mode = _norm_mode(item.get('mode'))
        name, color = _mode_labels(mode)
        # 遮罩 + 卡片：卡片固定 440 宽，水平居中，上下留弹性。
        mask = QWidget(self)
        mask.setObjectName('notifyMask')
        mask.setStyleSheet(
            'QWidget#notifyMask { background: rgba(16,24,40,120); }'
            f'QFrame#notifyCard {{ background: {theme.CARD};'
            ' border: none; border-radius: 16px; }'
            f'QLabel#nmMode {{ color: {color}; font-size: 12px; font-weight: 700; background: transparent; }}'
            f'QLabel#nmTitle {{ color: {theme.INK}; font-size: 20px; font-weight: 700; background: transparent; }}'
            f'QLabel#nmText {{ color: {theme.INK2}; font-size: 14px; background: transparent; }}'
            f'QLabel#nmFrom {{ color: {theme.INK3}; font-size: 12px; background: transparent; }}'
        )
        outer = QVBoxLayout(mask)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addStretch(1)
        card = QFrame(mask)
        card.setObjectName('notifyCard')
        card.setFixedWidth(440)
        col = QVBoxLayout(card)
        col.setContentsMargins(24, 22, 24, 20)
        col.setSpacing(10)
        badge = QLabel(name, card)
        badge.setObjectName('nmMode')
        col.addWidget(badge)
        title = QLabel(str(item.get('title') or '（无标题）')[:80], card)
        title.setObjectName('nmTitle')
        title.setWordWrap(True)
        col.addWidget(title)
        text = str(item.get('text') or '').strip()
        if text:
            body = QLabel(text[:600], card)
            body.setObjectName('nmText')
            body.setWordWrap(True)
            col.addWidget(body)
        sender = str(item.get('from') or '')
        if sender:
            from_label = QLabel(f'来自 {sender}', card)
            from_label.setObjectName('nmFrom')
            col.addWidget(from_label)
        if str(item.get('fileId') or ''):
            btn = QPushButton('下载附件', card)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setStyleSheet(
                f'QPushButton {{ background: {theme.PRIMARY}; color: #ffffff; border: none;'
                ' border-radius: 8px; font-size: 13px; padding: 7px 14px; }'
                f'QPushButton:hover {{ background: {theme.PRIMARY_DARK}; }}'
            )
            btn.clicked.connect(lambda _=False, i=notify_id: self.download_requested.emit(i))
            row = QHBoxLayout()
            row.addWidget(btn)
            row.addStretch(1)
            col.addLayout(row)
        col.addSpacing(4)
        ack = QPushButton('知道了', card)
        ack.setCursor(Qt.PointingHandCursor)
        ack.setStyleSheet(
            f'QPushButton {{ background: {theme.PILL_GRAY_BG}; color: {theme.INK};'
            ' border: none; border-radius: 8px; font-size: 13px; font-weight: 600; padding: 8px 16px; }'
            f'QPushButton:hover {{ background: {theme.SEAT_HOVER_BG}; }}'
        )
        ack.clicked.connect(lambda _=False, i=notify_id: self._ack(i))
        ack_row = QHBoxLayout()
        ack_row.addStretch(1)
        ack_row.addWidget(ack)
        col.addLayout(ack_row)
        hrow = QHBoxLayout()
        hrow.addStretch(1)
        hrow.addWidget(card)
        hrow.addStretch(1)
        outer.addLayout(hrow)
        outer.addStretch(1)
        self._mask = mask
        self._sync_geometry()
        mask.setGeometry(self.rect())
        self.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        self.show()
        self.raise_()
        if lightweight and not text:
            return

    def _ack(self, notify_id: int) -> None:
        self.acknowledged.emit(int(notify_id))
        if self._mask is not None:
            self._mask.setParent(None)
            self._mask.deleteLater()
            self._mask = None
        if self._queue:
            self._present_mask(self._queue.pop(0))
            return
        if self._banner is None:
            self.hide()
            self.setAttribute(Qt.WA_TransparentForMouseEvents, True)

    def clear(self) -> None:
        self._clearing = True
        try:
            if self._registry is not None:
                self._registry.cancel('notify:banner')
            self._dismiss_banner()
            if self._mask is not None:
                self._mask.setParent(None)
                self._mask.deleteLater()
                self._mask = None
            self._queue.clear()
            self._banner_queue.clear()
            self.hide()
        finally:
            self._clearing = False
