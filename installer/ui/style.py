"""安装向导的统一观感（QSS + 字体）。"""
from __future__ import annotations

ACCENT = '#2d7ff9'
ACCENT_DARK = '#1f66d0'
TEXT = '#1f2937'
TEXT_SECONDARY = '#5b6675'
WARNING = '#b45309'
ERROR = '#c0392b'

STYLE_SHEET = ''.join([
    """
QWizard {
    background: #f5f7fb;
}
QWizardPage {
    background: #f5f7fb;
}
QWidget {
    color: """,
    f'{TEXT}',
    """;
    font-family: "Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI", sans-serif;
    font-size: 13px;
}
QLabel#heroTitle {
    font-size: 24px;
    font-weight: 600;
    color: """,
    f'{TEXT}',
    """;
}
QLabel#heroSubtitle {
    font-size: 14px;
    color: """,
    f'{TEXT_SECONDARY}',
    """;
}
QLabel#sectionTitle {
    font-size: 15px;
    font-weight: 600;
    color: """,
    f'{TEXT}',
    """;
}
QLabel#hintLabel {
    color: """,
    f'{TEXT_SECONDARY}',
    """;
}
QLabel#warningLabel {
    color: """,
    f'{WARNING}',
    """;
}
QLabel#errorLabel {
    color: """,
    f'{ERROR}',
    """;
}
QLabel#pathLabel {
    color: """,
    f'{TEXT_SECONDARY}',
    """;
    font-size: 12px;
}
QFrame#card {
    background: #ffffff;
    border: 1px solid #e2e8f1;
    border-radius: 8px;
}
QLineEdit {
    background: #ffffff;
    border: 1px solid #c8d2e0;
    border-radius: 5px;
    padding: 6px 9px;
    selection-background-color: """,
    f'{ACCENT}',
    """;
}
QLineEdit:focus {
    border: 1px solid """,
    f'{ACCENT}',
    """;
}
QPushButton {
    background: #ffffff;
    border: 1px solid #c8d2e0;
    border-radius: 5px;
    padding: 6px 16px;
    min-height: 18px;
}
QPushButton:hover {
    border-color: """,
    f'{ACCENT}',
    """;
    color: """,
    f'{ACCENT_DARK}',
    """;
}
QPushButton:disabled {
    color: #a8b1bf;
    border-color: #e2e8f1;
}
QPushButton#primaryButton, QWizard QPushButton:default {
    background: """,
    f'{ACCENT}',
    """;
    border: 1px solid """,
    f'{ACCENT}',
    """;
    color: #ffffff;
    font-weight: 600;
}
QPushButton#primaryButton:hover, QWizard QPushButton:default:hover {
    background: """,
    f'{ACCENT_DARK}',
    """;
    border-color: """,
    f'{ACCENT_DARK}',
    """;
    color: #ffffff;
}
QProgressBar {
    background: #e8edf5;
    border: 1px solid #d4dce9;
    border-radius: 7px;
    height: 20px;
    text-align: center;
    color: """,
    f'{TEXT}',
    """;
}
QProgressBar::chunk {
    background: """,
    f'{ACCENT}',
    """;
    border-radius: 6px;
}
QListWidget {
    background: #ffffff;
    border: 1px solid #dde4ee;
    border-radius: 5px;
    font-size: 12px;
}
QCheckBox {
    spacing: 8px;
    font-size: 14px;
}
QCheckBox::indicator {
    width: 17px;
    height: 17px;
}
QTextBrowser {
    background: transparent;
    border: none;
}
""",
])
