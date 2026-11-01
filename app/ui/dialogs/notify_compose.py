"""发送通知弹窗：选收件人、选强度、写标题正文、可带一个附件。

为什么要单独一个对话框：设置页里只有一个「发送通知」按钮，而通知的三档
（轻度/重要/紧急）在手机端的呈现完全不同，得让发的人明确选，不能猜。
"""

from __future__ import annotations

import base64
from pathlib import Path

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QComboBox,
    QFileDialog,
    QLabel,
    QLineEdit,
    QPushButton,
    QTextEdit,
    QWidget,
)

from app.ui import theme
from app.ui.modals import ModalShell

MAX_FILE_BYTES = 8388608

MODES = (
    ("mild", "轻度提醒", "手机顶部滑出一条横幅，3 秒自动消失"),
    ("severe", "重要", "手机弹出全屏提示，必须点「知道了」"),
    ("urgent", "紧急", "全屏提示 + 震动 + 提示音"),
)

FIELD_LABEL_QSS = (
    f"QLabel {{ color: {theme.INK2}"
    "; font-size: 12px; font-weight: 600; background: transparent; }"
)

INPUT_QSS = (
    f"QLineEdit, QTextEdit, QComboBox {{ background: {theme.CARD}"
    f"; border: 1px solid {theme.LINE}"
    f"; border-radius: 9px; color: {theme.INK}"
    f"; font-size: 13px; padding: 0 11px; }}QTextEdit {{ padding: 8px 11px; }}"
    f"QLineEdit:focus, QTextEdit:focus, QComboBox:focus {{ border-color: {theme.PRIMARY}"
    "; }"
)


def _label(text: str, parent: QWidget) -> QLabel:
    lab = QLabel(text, parent)
    lab.setStyleSheet(FIELD_LABEL_QSS)
    lab.setFixedHeight(16)
    return lab


class NotifyComposeDialog(ModalShell):
    """返回 ``result_payload()`` 给设置页，由主窗口投递给同步服务。"""

    WIDTH = 560

    def __init__(self, registry=None, parent: QWidget | None = None) -> None:
        super().__init__("发送通知", "选收件人与提醒强度，发送后手机端会按强度提示",
                         parent, width=self.WIDTH)
        self._registry = registry
        self._file_bytes = b""
        self._file_name = ""
        self._build()
        self.add_foot_button("取消", "ghost")
        self.add_foot_button("发送", "primary")

    def _build(self) -> None:
        body = self.body_layout()
        content = self.content_widget()
        content.setStyleSheet(INPUT_QSS)

        body.addWidget(_label("收件人", content))
        self._target = QComboBox(content)
        self._target.setObjectName("ncTarget")
        self._target.setFixedHeight(41)
        self._target.addItem("全部设备", "*")
        for dev in self._devices():
            name = str(dev.get("name") or dev.get("deviceId") or "未命名设备")
            self._target.addItem(
                f"{name}（{dev.get('deviceId', '')}）",
                str(dev.get("deviceId") or ""),
            )
        body.addWidget(self._target)
        body.addSpacing(2)

        body.addWidget(_label("提醒强度", content))
        self._mode = QComboBox(content)
        self._mode.setObjectName("ncMode")
        self._mode.setFixedHeight(41)
        for key, name, desc in MODES:
            self._mode.addItem(f"{name}——{desc}", key)
        body.addWidget(self._mode)
        body.addSpacing(2)

        body.addWidget(_label("标题", content))
        self._title = QLineEdit(content)
        self._title.setObjectName("ncTitle")
        self._title.setFixedHeight(39)
        self._title.setMaxLength(120)
        self._title.setPlaceholderText("例如：第三节改到实验室上课")
        body.addWidget(self._title)
        body.addSpacing(2)

        body.addWidget(_label("正文（可留空）", content))
        self._text = QTextEdit(content)
        self._text.setObjectName("ncText")
        self._text.setFixedHeight(96)
        self._text.setPlaceholderText("补充说明，最多 2000 字")
        body.addWidget(self._text)
        body.addSpacing(2)

        body.addWidget(_label("附件（可留空，最大 8 MB）", content))
        self._file_label = QLabel("未选择", content)
        self._file_label.setObjectName("ncFile")
        self._file_label.setStyleSheet(
            f"QLabel {{ color: {theme.INK3}"
            "; font-size: 12.5px; background: transparent; }"
        )
        body.addWidget(self._file_label)
        self._pick = QPushButton("选择文件…", content)
        self._pick.setObjectName("ncPick")
        self._pick.setCursor(Qt.PointingHandCursor)
        self._pick.setFixedHeight(31)
        self._pick.setStyleSheet(
            f"QPushButton {{ background: {theme.PILL_GRAY_BG}"
            f"; color: {theme.INK}"
            f"; border: none; border-radius: 8px; font-size: 12.5px; font-weight: 600; padding: 0 12px; }}"
            f"QPushButton:hover {{ background: {theme.SEAT_HOVER_BG}"
            "; }"
        )
        self._pick.clicked.connect(self._pick_file)
        body.addWidget(self._pick)

        self._title.textChanged.connect(self._refresh_ok)
        self._refresh_ok()
        self.fit_to_content()

    def _devices(self) -> list[dict]:
        try:
            return list(self._registry.as_list()) if self._registry else []
        except (AttributeError, TypeError, ValueError):
            return []

    def _pick_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "选择附件")
        if not path:
            return
        try:
            size = Path(path).stat().st_size
        except OSError as exc:
            self._file_label.setText(f"读取失败：{exc}")
            return
        if size > MAX_FILE_BYTES:
            self._file_label.setText(
                f"文件太大（{size / 1048576:.1f} MB），上限 8 MB"
            )
            self._file_bytes = b""
            self._file_name = ""
            return
        try:
            self._file_bytes = Path(path).read_bytes()
        except OSError as exc:
            self._file_label.setText(f"读取失败：{exc}")
            return
        self._file_name = Path(path).name
        self._file_label.setText(f"{self._file_name}（{size / 1024:.0f} KB）")

    def _refresh_ok(self) -> None:
        self.set_accept_enabled(bool(self._title.text().strip()))

    def result_payload(self) -> dict:
        payload = {
            "targets": [self._target.currentData() or "*"],
            "mode": self._mode.currentData() or "mild",
            "title": self._title.text().strip()[:120],
            "text": self._text.toPlainText().strip()[:2000],
            "fileName": self._file_name,
            "fileDataB64": "",
        }
        if self._file_bytes:
            payload["fileDataB64"] = base64.b64encode(self._file_bytes).decode("ascii")
        return payload

    def content_widget(self) -> QWidget:
        return super().content_widget()
