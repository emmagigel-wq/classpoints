"""「更新日志」弹窗：把 CHANGELOG.md 按版本列出来，一条版本一条记录。

为什么单独做成一个弹窗而不是塞进「关于软件」页：

* CHANGELOG.md 有几十 KB，摆进设置页会把整页撑得很长；
* 老师通常只想知道「这次更新改了什么」，左边选版本、右边读内容最快。

内容全部来自仓库根目录的 `CHANGELOG.md`（打包时随程序一起携带，
见 `packaging/*.spec` 的 datas）。解析失败不抛异常，只显示一句提示——
一个只读的说明窗口，不值得为它挡住主流程。
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from PyQt5.QtWidgets import QHBoxLayout, QListWidget, QListWidgetItem, QTextBrowser

from app.ui import theme
from app.ui.modals import ModalShell

CHANGELOG_WIDTH = 760
CHANGELOG_MIN_H = 520

_VERSION_RE = re.compile(r"^##\s+v?([0-9][0-9A-Za-z.\-]*)\s*(.*)$")
_SECTION_RE = re.compile(r"^###\s+(.+?)\s*$")

_ITEM_RE = re.compile(r"^[-*]\s+(.*)$")

# 找不到/解析不出内容时的兜底文案
FALLBACK_TEXT = "这个版本没有附带更新日志文件。"


@dataclass
class VersionEntry:
    """一个版本的更新内容。"""

    version: str
    note: str = ""
    sections: list[tuple[str, list[str]]] = field(default_factory=list)
    loose: list[str] = field(default_factory=list)

    @property
    def label(self) -> str:
        return f"v{self.version}"

    @property
    def item_count(self) -> int:
        return len(self.loose) + sum(len(lines) for _, lines in self.sections)


def changelog_path() -> Path | None:
    """按「打包内 → 仓库根 → 源码目录」的顺序找 CHANGELOG.md。"""
    candidates = []
    base = getattr(sys, "_MEIPASS", "")
    if base:
        candidates.append(Path(base) / "CHANGELOG.md")
    here = Path(__file__).resolve()
    # 仓库根：app/ui/dialogs/changelog_dialog.py → parents[3]
    if len(here.parents) >= 4:
        candidates.append(here.parents[3] / "CHANGELOG.md")
    candidates.append(Path.cwd() / "CHANGELOG.md")
    for path in candidates:
        try:
            if path.is_file():
                return path
        except OSError:
            continue
    return None


def read_changelog() -> str:
    """读原始 Markdown；读不到返回空串（调用方会显示 FALLBACK_TEXT）。"""
    path = changelog_path()
    if path is None:
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def parse_changelog(text: str) -> list[VersionEntry]:
    """把 Markdown 解析成按文件顺序排列的版本列表（新版本在前）。"""
    entries: list[VersionEntry] = []
    current: VersionEntry | None = None
    section = ""
    for raw in (text or "").splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        if line.startswith("## "):
            match = _VERSION_RE.match(line)
            if not match:
                current = None
                continue
            current = VersionEntry(version=match.group(1),
                                   note=match.group(2).strip())
            entries.append(current)
            section = ""
            continue
        if current is None:
            continue
        if line.startswith("### "):
            match = _SECTION_RE.match(line)
            if match:
                section = match.group(1)
                current.sections.append((section, []))
            continue
        item = _ITEM_RE.match(line)
        if item:
            text_line = _clean(item.group(1))
            if current.sections:
                current.sections[-1][1].append(text_line)
            else:
                current.loose.append(text_line)
            continue
        body = _clean(line)
        if not body:
            continue
        if not (current.sections or current.loose or current.note):
            current.note = body
            continue
        if current.sections and current.sections[-1][1]:
            current.sections[-1][1][-1] += " " + body
        elif current.sections:
            current.sections[-1][1].append(body)
        else:
            current.loose.append(body)
    return entries


def _clean(line: str) -> str:
    """去掉 Markdown 强调标记，保留纯文本（QTextBrowser 里不用再解析）。"""
    text = line.strip()
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"`(.+?)`", r"\1", text)
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def entry_html(entry: VersionEntry) -> str:
    """把一个版本渲染成 QTextBrowser 认的 HTML。"""
    parts = [
        f'<div style="color:{theme.INK2}; font-size:13px;">',
        f'<h2 style="color:{theme.INK}; margin:0 0 6px 0;">v{entry.version}</h2>',
    ]
    if entry.note:
        parts.append(
            f'<p style="color:{theme.INK3}; margin:0 0 10px 0;">{entry.note}</p>'
        )
    if entry.loose:
        parts.append('<ul style="margin-left:-16px;">')
        for line in entry.loose:
            parts.append(f'<li style="margin-bottom:6px;">{line}</li>')
        parts.append('</ul>')
    for title, lines in entry.sections:
        parts.append(
            f'<h3 style="color:{theme.INK}; margin:12px 0 6px 0;">{title}</h3>'
        )
        if not lines:
            continue
        parts.append('<ul style="margin-left:-16px;">')
        for line in lines:
            parts.append(f'<li style="margin-bottom:6px;">{line}</li>')
        parts.append('</ul>')
    parts.append('</div>')
    return "".join(parts)


def changelog_html(version: str = "", text: str | None = None) -> str:
    """公开给测试 / 探针：拿到某个版本（默认最新）的 HTML。"""
    entries = parse_changelog(read_changelog() if text is None else text)
    if not entries:
        return f'<p style="color:{theme.INK3};">{FALLBACK_TEXT}</p>'
    if not version:
        return entry_html(entries[0])
    for entry in entries:
        if entry.version == version:
            return entry_html(entry)
    return f'<p style="color:{theme.INK3};">没有找到 v{version} 的记录。</p>'


class ChangelogDialog(ModalShell):
    """左边选版本、右边读内容。"""

    def __init__(self, parent=None, text: str | None = None) -> None:
        super().__init__("更新日志", "这里记录每个版本改了什么", parent,
                         width=CHANGELOG_WIDTH)
        self._entries = parse_changelog(read_changelog() if text is None else text)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(12)

        self.versions = QListWidget(self.content_widget())
        self.versions.setObjectName("changelogVersions")
        self.versions.setFixedWidth(150)
        self.versions.setStyleSheet(
            f"QListWidget#changelogVersions {{ background: {theme.CARD}"
            f"; border: 1px solid {theme.LINE}"
            f"; border-radius: 10px; padding: 4px; font-size: 13px; color: {theme.INK2}"
            f"; }} QListWidget#changelogVersions::item {{ padding: 7px 8px; border-radius: 7px; }}"
            f" QListWidget#changelogVersions::item:selected {{ background: {theme.SEL_ACTIVE_BG}"
            f"; color: {theme.PRIMARY}"
            "; }"
        )

        self.view = QTextBrowser(self.content_widget())
        self.view.setObjectName("changelogView")
        self.view.setOpenExternalLinks(False)
        self.view.setOpenLinks(False)
        self.view.setStyleSheet(
            f"QTextBrowser#changelogView {{ background: {theme.CARD}"
            f"; border: 1px solid {theme.LINE}"
            f"; border-radius: 10px; padding: 10px 14px; font-size: 13px; color: {theme.INK2}"
            f"; }}"
        )

        row.addWidget(self.versions)
        row.addWidget(self.view, 1)
        self.body_layout().addLayout(row)

        for entry in self._entries:
            item = QListWidgetItem(entry.label)
            item.setToolTip(entry.note or f"{entry.item_count} 条改动")
            self.versions.addItem(item)
        if self._entries:
            self.versions.setCurrentRow(0)
            self.view.setHtml(entry_html(self._entries[0]))
        else:
            self.view.setHtml(
                f'<p style="color:{theme.INK3};">{FALLBACK_TEXT}</p>'
            )
        self.versions.currentRowChanged.connect(self._on_version_changed)

        self.add_foot_button("关闭").clicked.connect(self.reject)
        parent_h = parent.height() if parent is not None else 0
        if parent_h > CHANGELOG_MIN_H + 80:
            self.setFixedHeight(min(parent_h - 80, 720))
        else:
            self.setFixedHeight(CHANGELOG_MIN_H)

    def _on_version_changed(self, index: int) -> None:
        if 0 <= index < len(self._entries):
            self.view.setHtml(entry_html(self._entries[index]))

    def version_labels(self) -> list[str]:
        """公开给测试 / 探针：左侧列出的版本标签。"""
        return [self.versions.item(i).text() for i in range(self.versions.count())]
