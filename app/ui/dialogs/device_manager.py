"""已配对设备管理：看有哪些设备连过、重命名、吊销、移除。

吊销只是把设备标记成不可用（保留记录，方便日后认出来是哪台），
移除则是从名单里彻底删掉——两个动作分开，避免误点把历史抹掉。
"""

from __future__ import annotations

import time

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from app.ui import theme
from app.ui.modals import ModalShell

ROW_H = 44


def _fmt_seen(ts: float) -> str:
    if not ts:
        return "从未连接"
    delta = max(0.0, time.time() - float(ts))
    if delta < 90:
        return "刚刚在线"
    if delta < 3600:
        return f"{int(delta // 60)} 分钟前"
    if delta < 86400:
        return f"{int(delta // 3600)} 小时前"
    return time.strftime("%m-%d %H:%M", time.localtime(ts))


def _mini(text: str, parent: QWidget, danger: bool = False) -> QPushButton:
    btn = QPushButton(text, parent)
    btn.setCursor(Qt.PointingHandCursor)
    btn.setFixedHeight(26)
    ink = theme.DANGER if danger else theme.INK
    hover = theme.BAD_BG if danger else theme.SEAT_HOVER_BG
    btn.setStyleSheet(
        f"QPushButton {{ background: {theme.PILL_GRAY_BG}"
        f"; color: {ink}"
        f"; border: none; border-radius: 7px; font-size: 12px; font-weight: 600; padding: 0 10px; }}"
        f"QPushButton:hover {{ background: {hover}"
        "; }"
    )
    return btn


class DeviceManagerDialog(ModalShell):
    """设备列表 + 每行三个动作。任何改动立刻落盘。"""

    changed = pyqtSignal()

    WIDTH = 620

    def __init__(self, registry, parent: QWidget | None = None) -> None:
        super().__init__("已配对设备", "这些设备连接过本机的手机端页面",
                         parent, width=self.WIDTH)
        self._registry = registry
        self._build()
        self.add_foot_button("关闭", "primary")

    def _build(self) -> None:
        body = self.body_layout()
        self._count = QLabel("", self.content_widget())
        self._count.setStyleSheet(
            f"QLabel {{ color: {theme.INK3}"
            "; font-size: 12.5px; background: transparent; }"
        )
        body.addWidget(self._count)

        self._scroll = QScrollArea(self.content_widget())
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setStyleSheet(
            "QScrollArea { border: none; background: transparent; }"
        )
        holder = QWidget(self._scroll)
        holder.setStyleSheet("background: transparent;")
        self._rows = QVBoxLayout(holder)
        self._rows.setContentsMargins(0, 0, 0, 0)
        self._rows.setSpacing(6)
        self._rows.addStretch(1)
        self._scroll.setWidget(holder)
        body.addWidget(self._scroll, 1)
        self.refresh()
        self.fit_to_content()

    def refresh(self) -> None:
        while self._rows.count() > 1:
            item = self._rows.takeAt(0)
            w = item.widget()
            if w is None:
                continue
            w.setParent(None)
            w.deleteLater()
        devices = []
        try:
            devices = list(self._registry.as_list()) if self._registry else []
        except (AttributeError, TypeError, ValueError):
            devices = []
        self._count.setText(
            f"共 {len(devices)} 台设备" if devices
            else "还没有设备连过。用手机扫码或输入地址首次打开后就会出现在这里。"
        )
        for dev in devices:
            self._rows.insertWidget(self._rows.count() - 1, self._row(dev))
        self._scroll.setFixedHeight(min(360, max(120, len(devices) * (ROW_H + 6))))

    def _row(self, dev: dict) -> QWidget:
        did = str(dev.get("id") or "")
        revoked = bool(dev.get("revoked"))
        online = bool(dev.get("online"))
        name = str(dev.get("name") or "") or "未命名设备"
        row = QFrame(self)
        row.setObjectName("devRow")
        row.setFixedHeight(ROW_H)
        row.setStyleSheet(
            f"QFrame#devRow {{ background: {theme.CARD}"
            f"; border: 1px solid {theme.LINE}"
            "; border-radius: 10px; }QLabel { background: transparent; }"
        )

        lay = QHBoxLayout(row)
        lay.setContentsMargins(12, 6, 10, 6)
        lay.setSpacing(10)
        dot = QLabel("●", row)
        color = theme.BAD if revoked else (theme.SUCCESS if online else theme.INK3)
        dot.setStyleSheet(
            f"QLabel {{ color: {color}"
            "; font-size: 12px; background: transparent; }"
        )
        lay.addWidget(dot)
        col = QVBoxLayout()
        col.setSpacing(0)
        title = QLabel(name + ("（已吊销）" if revoked else ""), row)
        title.setStyleSheet(
            f"QLabel {{ color: {theme.INK}"
            "; font-size: 13px; font-weight: 600; background: transparent; }"
        )
        col.addWidget(title)
        sub = QLabel(f"{did[:18]}… · {_fmt_seen(dev.get('lastSeen'))}", row)
        sub.setStyleSheet(
            f"QLabel {{ color: {theme.INK3}"
            "; font-size: 12px; background: transparent; }"
        )
        col.addWidget(sub)
        lay.addLayout(col, 1)

        rename = _mini("重命名", row)
        rename.clicked.connect(lambda _=False, i=did: self._rename(i))
        lay.addWidget(rename)
        toggle = _mini("恢复" if revoked else "吊销", row, danger=not revoked)
        toggle.clicked.connect(lambda _=False, i=did, r=revoked: self._revoke(i, not r))
        lay.addWidget(toggle)
        drop = _mini("移除", row, danger=True)
        drop.clicked.connect(lambda _=False, i=did: self._remove(i))
        lay.addWidget(drop)
        return row

    def _rename(self, device_id: str) -> None:
        current = ""
        try:
            current = self._registry.label(device_id) or ""
        except (AttributeError, TypeError):
            current = ""
        text, ok = QInputDialog.getText(self, "重命名设备", "设备名称：",
                                        text=current)
        if not ok:
            return
        name = (text or "").strip()[:40]
        if not name:
            return
        if not self._registry.rename(device_id, name):
            QMessageBox.warning(self, "重命名失败", "写不进设备名单，请检查数据目录权限。")
        self._after_change()

    def _revoke(self, device_id: str, revoked: bool) -> None:
        if revoked:
            ans = QMessageBox.question(
                self,
                "吊销设备",
                "吊销后这台设备将无法再读写本机数据（可以随时恢复）。继续吗？",
            )
            if ans != QMessageBox.Yes:
                return
        self._registry.revoke(device_id, revoked)
        self._after_change()

    def _remove(self, device_id: str) -> None:
        ans = QMessageBox.question(
            self,
            "移除设备",
            "从名单里彻底删除这台设备（历史记录不再显示）。继续吗？",
        )
        if ans != QMessageBox.Yes:
            return
        self._registry.remove(device_id)
        self._after_change()

    def _after_change(self) -> None:
        try:
            self._registry.save()
        except (AttributeError, OSError):
            pass
        self.refresh()
        self.changed.emit()
