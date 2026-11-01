"""「一键部署固定域名」弹窗。

做三件事：
1. 让用户写一个主机名（比如 ``classpoints.你的域名.com``）；
2. 后台把 Cloudflare 的登录/建隧道/域名解析/取令牌一条龙跑完，输出实时刷在新窗口里；
3. 成功后把地址与令牌填回设置，并提供「复制链接 / 复制令牌 / 复制全部结果」。

注意：UI 层不碰数据落盘（分层测试会拦），本弹窗只把结果发出去，
由设置弹窗负责写进 settings 并启动隧道。
"""
from __future__ import annotations

import threading

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (QFrame, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit,
                             QPushButton, QVBoxLayout, QWidget)

from app.services import domain_setup
from app.ui import theme as th
from app.ui.modals import ModalShell


def _mono() -> str:
    return "Consolas, 'Courier New', monospace"


def _copy_qss() -> str:
    return (
        '\n    QPlainTextEdit#dsLog, QLineEdit#dsUrl, QLineEdit#dsToken {\n        background: #fbfcfe; border: 1px solid '
        f'{th.LINE}'
        '; border-radius: 9px;\n        padding: 7px 9px; font-family: '
        f'{_mono()}'
        '; font-size: 12px;\n        color: '
        f'{th.INK}'
        '; selection-background-color: '
        f'{th.SEL_ACTIVE_BG}'
        ';\n    }\n    QLabel#dsHint { color: '
        f'{th.INK3}'
        '; font-size: 12px; }\n    QLabel#dsWarn { color: #b54708; font-size: 12px; }\n    QLabel#dsKey { color: '
        f'{th.INK2}'
        '; font-size: 12px; font-weight: 600; }\n    QFrame#dsBox {\n        background: #f9fafb; border: 1px solid '
        f'{th.LINE}'
        '; border-radius: 10px;\n    }\n    QPushButton#dsMini {\n        font-size: 12px; padding: 6px 10px; border-radius: 8px;\n        background: '
        f'{th.CARD}'
        '; border: 1px solid '
        f'{th.LINE}'
        '; color: '
        f'{th.INK}'
        ';\n    }\n    QPushButton#dsMini:hover { background: '
        f'{th.PILL_GRAY_BG}'
        '; }\n    '
    )


class DomainSetupDialog(ModalShell):
    """部署结果通过 `deployed(dict)` 交给调用方落盘。"""

    deployed = pyqtSignal(dict)
    line_ready = pyqtSignal(str)
    step_done = pyqtSignal(object)
    finished_ok = pyqtSignal(object)

    WIDTH = 640

    def __init__(self, parent: QWidget | None = None, hostname: str = '') -> None:
        super().__init__('一键部署固定域名',
                         '程序会调用你本机的 cloudflared，在你的 Cloudflare 账号下建隧道',
                         parent, width=self.WIDTH)
        self._running = False
        self._result = None
        self._build(hostname)
        self.line_ready.connect(self._append)
        self.step_done.connect(self._on_step)
        self.finished_ok.connect(self._on_finished)
        self.setStyleSheet(_copy_qss())

    def _build(self, hostname: str) -> None:
        body = self.body_layout()

        tip = QLabel('固定域名的前提：你有一个已经在 Cloudflare 托管的域名。点「开始部署」后程序会打开浏览器让你授权，之后建隧道、加解析、取令牌全自动。只需要做一次，以后换电脑也不用重做。')
        tip.setWordWrap(True)
        tip.setObjectName('dsHint')
        body.addWidget(tip)

        row = QHBoxLayout()
        row.setSpacing(8)
        key = QLabel('主机名')
        key.setObjectName('dsKey')
        key.setFixedWidth(52)
        self._host = QLineEdit()
        self._host.setObjectName('dsHost')
        self._host.setPlaceholderText('classpoints.你的域名.com')
        self._host.setText(hostname or f'{domain_setup.TUNNEL_NAME}.你的域名.com')
        self._host.setMinimumHeight(36)
        row.addWidget(key)
        row.addWidget(self._host, 1)
        body.addLayout(row)

        hint = QLabel('写你想要的完整网址，例如 classpoints.example.com 或 jifen.example.com。')
        hint.setObjectName('dsHint')
        body.addWidget(hint)

        self._warn = QLabel('')
        self._warn.setObjectName('dsWarn')
        self._warn.setWordWrap(True)
        self._warn.hide()
        body.addWidget(self._warn)

        btns = QHBoxLayout()
        btns.setSpacing(8)
        self._btn_go = QPushButton('开始部署')
        self._btn_go.setObjectName('primary')
        self._btn_go.setMinimumHeight(36)
        self._btn_go.setMinimumWidth(110)
        self._btn_go.clicked.connect(self.start_deploy)
        btns.addWidget(self._btn_go)
        btns.addStretch(1)
        body.addLayout(btns)

        self._log = QPlainTextEdit()
        self._log.setObjectName('dsLog')
        self._log.setReadOnly(True)
        self._log.setMinimumHeight(150)
        self._log.setMaximumHeight(190)
        self._log.setPlaceholderText('部署过程会显示在这里')
        body.addWidget(self._log)

        self._box = QFrame()
        self._box.setObjectName('dsBox')
        box = QVBoxLayout(self._box)
        box.setContentsMargins(12, 10, 12, 12)
        box.setSpacing(6)
        title = QLabel('部署结果')
        title.setObjectName('dsKey')
        box.addWidget(title)

        for label, attr in (('公网地址', '_url'), ('隧道令牌', '_token')):
            line = QHBoxLayout()
            line.setSpacing(8)
            name = QLabel(label)
            name.setObjectName('dsKey')
            name.setFixedWidth(56)
            edit = QLineEdit()
            edit.setReadOnly(True)
            edit.setObjectName('dsUrl' if attr == '_url' else 'dsToken')
            edit.setMinimumHeight(32)
            if attr == '_url':
                self._url = edit
            else:
                self._token = edit
            line.addWidget(name)
            line.addWidget(edit, 1)
            box.addLayout(line)

        self._box.hide()
        body.addWidget(self._box)

        self._add_copy_row(body, '复制链接', self._copy_url)
        self._add_copy_row(body, '复制令牌', self._copy_token)
        self._add_copy_row(body, '复制全部结果', self._copy_all)

    def _add_copy_row(self, body, text: str, slot) -> None:
        row = QHBoxLayout()
        row.setSpacing(8)
        btn = QPushButton(text)
        btn.setObjectName('dsMini')
        btn.setMinimumHeight(30)
        btn.setMinimumWidth(110)
        btn.clicked.connect(slot)
        btn.setEnabled(False)
        row.addWidget(btn)
        row.addStretch(1)
        body.addLayout(row)
        if text == '复制链接':
            self._btn_copy_url = btn
            return
        if text == '复制令牌':
            self._btn_copy_token = btn
            return
        self._btn_copy_all = btn

    def start_deploy(self) -> None:
        if self._running:
            return
        host = domain_setup.normalize_hostname(self._host.text())
        if not domain_setup.hostname_ok(host):
            self._show_warn('主机名不合法：要写成 classpoints.你的域名.com 这种形式。')
            return
        self._host.setText(host)
        self._warn.hide()
        self._log.clear()
        self._box.hide()
        self._btn_copy_url.setEnabled(False)
        self._btn_copy_token.setEnabled(False)
        self._btn_copy_all.setEnabled(False)

        if not domain_setup.cloudflared():
            self._show_warn('找不到 cloudflared：请使用安装包版本，或把 cloudflared.exe 放进程序目录。')
            return

        self._running = True
        self._btn_go.setEnabled(False)
        self._btn_go.setText('部署中…')
        self._append(f'目标主机名：{host}')

        def worker() -> None:
            try:
                res = domain_setup.deploy(
                    host,
                    on_line=lambda t: self.line_ready.emit(str(t)),
                    on_step=lambda s: self.step_done.emit(s),
                )
            except Exception as exc:
                self.line_ready.emit(f'[错误] 部署中断：{exc}')
                res = domain_setup.DeployResult(False, [], error=str(exc))
            self.finished_ok.emit(res)

        threading.Thread(target=worker, name='cp-domain-deploy', daemon=True).start()

    def _on_step(self, step) -> None:
        mark = '✓' if getattr(step, 'ok', False) else '✗'
        title = getattr(step, 'title', '')
        detail = getattr(step, 'detail', '')
        self._append(f'{mark} {title}' + (f'：{detail}' if detail else ''))

    def _on_finished(self, result) -> None:
        self._running = False
        self._result = result
        self._btn_go.setEnabled(True)
        self._btn_go.setText('重新部署' if not result.ok else '再部署一次')
        if result.ok:
            self._url.setText(result.url)
            self._token.setText(result.token)
            self._box.show()
            self._btn_copy_url.setEnabled(True)
            self._btn_copy_token.setEnabled(True)
            self._btn_copy_all.setEnabled(True)
            self._append('完成：地址与令牌已填好，可以直接复制。')
            self.deployed.emit({
                'hostname': result.hostname,
                'url': result.url,
                'token': result.token,
                'tunnel_id': result.tunnel_id,
            })
            return
        self._show_warn(result.error or '部署没有完成，看看上面的输出。')
        self._btn_copy_all.setEnabled(True)

    def _append(self, text: str) -> None:
        self._log.appendPlainText(text)

    def _show_warn(self, text: str) -> None:
        self._warn.setText(text)
        self._warn.show()

    def _copy(self, text: str, done: str) -> None:
        from PyQt5.QtWidgets import QApplication

        QApplication.clipboard().setText(text or '')
        self._append(done)

    def _copy_url(self) -> None:
        if self._result is not None:
            self._copy(self._result.url, '已复制公网地址。')

    def _copy_token(self) -> None:
        if self._result is not None:
            self._copy(self._result.token, '已复制隧道令牌。')

    def _copy_all(self) -> None:
        if self._result is not None:
            self._copy(self._result.copy_text(), '已复制全部结果（地址 + 令牌 + 隧道 ID）。')

    def result_text(self) -> str:
        if self._result is not None:
            return self._result.copy_text()
        return ''

    def is_running(self) -> bool:
        return self._running


__all__ = ['DomainSetupDialog']
