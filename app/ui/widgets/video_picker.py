"""抽奖视频相关的界面部件：视频库管理弹窗 + 视频播放弹窗。

分两层的理由：
  * `app/core/videos.py` 只管文件与绑定表（纯 Python，能无界面单测）；
  * 这里只放 Qt，最上面探测一次 QtMultimedia 是否可用，不可用就整体退化——
    抽奖页照旧先播 1.2 秒「揭晓中」，然后亮出结果 + 「打开视频」按钮，
    绝不因为缺少多媒体后端把抽奖流程卡死。

播放为什么走 QMediaPlayer 而不是自己写解码：PyQt5 的 Windows 轮子自带
DirectShow 后端，能直接播 mp4；播不了（编码不支持/文件损坏）时 QMediaPlayer
会发 error / InvalidMedia 信号，本弹窗收到就自己关掉，让抽奖结果继续显示。
"""

from __future__ import annotations

from pathlib import Path
import shutil
import tempfile
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QBrush, QColor
from PyQt5.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QVBoxLayout,
    QWidget,
)
from app.core import videos
from app.services import transcode
from app.ui import theme
from app.ui.widgets.video_player import (
    MEDIA_AVAILABLE,
    VideoPlayerDialog,
    make_player_dialog,
    open_video_external,
)

ANIMATION_LABELS = {
    '': '跟随全局设置',
    'none': '无动画',
    'roll': '滚动',
    'cards': '翻牌',
    'wheel': '转盘',
    'video': '视频揭晓',
}
VIDEO_FILTER = '视频文件 (*.mp4 *.webm *.mov *.avi *.mkv *.m4v *.ogv)'
TRANSCODE_HINT = '点「下载转码组件」可自动转换'
WARN_SUFFIX = '  ⚠'


def animation_label(key: str) -> str:
    return ANIMATION_LABELS.get(key, ANIMATION_LABELS.get('none', '无动画'))


def format_size(size_bytes: int) -> str:
    size = float(max(0, int(size_bytes)))
    for unit in ('B', 'KB', 'MB', 'GB'):
        if size < 1024 or unit == 'GB':
            return f'{size:.0f} {unit}' if unit == 'B' else f'{size:.1f} {unit}'
        size /= 1024
    return f'{size:.1f} GB'


class VideoManageDialog(QDialog):
    """视频库：导入多个视频、删除、试播。导入成功后发 videos_changed。"""

    videos_changed = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle('抽奖视频')
        self.setModal(True)
        self.resize(560, 420)
        self.setStyleSheet(f'QDialog {{ background: {theme.CARD}; }}QLabel {{ color: {theme.INK}; }}QListWidget {{ background: {theme.SEAT_BG}; border: 1px solid {theme.LINE}; border-radius: 10px; font-size: 13px; }}QPushButton {{ padding: 7px 14px; font-size: 13px; border-radius: 9px; border: 1px solid {theme.LINE}; background: {theme.BTN_GHOST_BG}; color: {theme.INK}; }}QPushButton:hover {{ background: {theme.CARD}; border-color: {theme.BRAND}; }}QPushButton#primary {{ background: {theme.BRAND}; border-color: {theme.BRAND}; color: #ffffff; font-weight: 700; }}QPushButton#primary:hover {{ background: {theme.BRAND_DARK}; border-color: {theme.BRAND_DARK}; }}')
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 14)
        lay.setSpacing(10)
        title = QLabel('抽奖视频（可导入多个，奖项可各选一段）', self)
        title.setStyleSheet(f'color: {theme.INK}; background: transparent; font-size: 15px; font-weight: 700;')
        lay.addWidget(title)
        self._list = QListWidget(self)
        self._list.setStyleSheet(f'QListWidget::item {{ padding: 7px 10px; }}QListWidget::item:selected {{ background: {theme.SEL_ACTIVE_BG}; color: {theme.BRAND_DARK}; }}')
        lay.addWidget(self._list, 1)
        self._status = QLabel('', self)
        self._status.setWordWrap(True)
        self._status.setStyleSheet(f'color: {theme.INK3}; background: transparent; font-size: 12px;')
        lay.addWidget(self._status)
        bar = QHBoxLayout()
        bar.setSpacing(8)
        add = QPushButton('导入视频…', self)
        add.setObjectName('primary')
        add.clicked.connect(self._on_import)
        bar.addWidget(add)
        play = QPushButton('试播', self)
        play.clicked.connect(self._on_play)
        bar.addWidget(play)
        remove = QPushButton('删除选中', self)
        remove.clicked.connect(self._on_remove)
        bar.addWidget(remove)
        tools = QPushButton('下载转码组件', self)
        tools.setToolTip('内置播放器只认 H.264 编码的 mp4。下载一次（约 30~80MB）之后，\n导入的其它编码视频会自动转换成能播的格式。')
        tools.clicked.connect(self._on_download_tools)
        bar.addWidget(tools)
        bar.addStretch(1)
        close = QPushButton('关闭', self)
        close.clicked.connect(self.accept)
        bar.addWidget(close)
        lay.addLayout(bar)
        self._warned = {}
        self._busy = False
        self._buttons = [add, play, remove, tools, close]
        self._reload()

    def _reload(self) -> None:
        self._list.clear()
        items = videos.list_videos()
        for info in items:
            item = QListWidgetItem(self._item_text(info))
            item.setData(Qt.UserRole, info.id)
            if info.id in self._warned:
                self._mark_unplayable(item, info)
            self._list.addItem(item)
        if items:
            self._list.setCurrentRow(0)
            self._status.setText(f'共 {len(items)} 段视频。')
        else:
            self._status.setText('还没有视频。点「导入视频…」选一个或多个视频文件。')

    def _item_text(self, info: videos.VideoInfo) -> str:
        text = f'{info.name}    ·  {format_size(info.size_bytes)}'
        return text + WARN_SUFFIX if info.id in self._warned else text

    def _mark_unplayable(self, item: QListWidgetItem, info: videos.VideoInfo) -> None:
        """把可能播不出来的条目标红并挂上说明（颜色取 theme，不写死色值）。"""
        item.setForeground(QBrush(QColor(theme.DANGER)))
        reason = self._warned.get(info.id) or '编码可能不受支持'
        item.setToolTip(f'这段视频内置播放器可能播不出来（{reason}）。\n' + TRANSCODE_HINT + '。')

    def _refresh_list(self) -> None:
        """重画列表。转码/下载组件之后调用；包一层是为了不动原来的 _reload 语义。"""
        self._reload()

    def video_ids(self) -> list[str]:
        return [self._list.item(i).data(Qt.UserRole) for i in range(self._list.count())]

    def _selected(self) -> videos.VideoInfo | None:
        row = self._list.currentRow()
        if row < 0:
            return None
        return videos.get_video(self._list.item(row).data(Qt.UserRole) or '')

    def _on_import(self) -> None:
        if self._busy:
            return
        paths, _ = QFileDialog.getOpenFileNames(self, '选择抽奖视频', '', VIDEO_FILTER)
        if not paths:
            return
        added, errors = 0, []
        pending = []
        for path in paths:
            try:
                info = videos.add_video(path)
                added += 1
            except videos.VideoError as exc:
                errors.append(str(exc))
                continue
            need, reason = transcode.needs_transcode(info.path)
            if not need:
                continue
            pending.append(info)
            self._warned[info.id] = reason or '编码可能不受支持'
        self._reload()
        if errors:
            self._status.setText(f'导入成功 {added} 个；失败 {len(errors)} 个：' + '；'.join(errors))
        else:
            self._status.setText(f'已导入 {added} 段视频。')
        if added:
            self.videos_changed.emit()
        if not pending:
            return
        if transcode.available():
            self._transcode_batch(pending)
            return
        lines = [f'{info.name} 的编码内置播放器可能播不出来，{TRANSCODE_HINT}'
                 for info in pending]
        self._status.setText(self._status.text() + '\n⚠ ' + '\n⚠ '.join(lines))

    def _transcode_batch(self, pending: list[videos.VideoInfo]) -> None:
        """逐条转码：成功后用新文件替换旧条目，失败把原因写进状态栏（不静默）。"""
        total = len(pending)
        header = self._status.text()
        dialog = QProgressDialog(f'正在转码 1/{total}…', '取消', 0, total * 100, self)
        dialog.setWindowTitle('正在转换视频')
        dialog.setWindowModality(Qt.WindowModal)
        dialog.setMinimumDuration(0)
        dialog.setAutoClose(False)
        dialog.setAutoReset(False)
        dialog.setValue(0)
        dialog.show()
        converted = []
        failed = []
        skipped = 0
        self._set_busy(True)
        try:
            for index, info in enumerate(pending):
                if dialog.wasCanceled():
                    skipped = total - index
                    break
                base = index * 100
                dialog.setLabelText(f'正在转码 {index + 1}/{total}…\n{info.name}')
                tmp_dir = Path(tempfile.mkdtemp(prefix='cp-tc-'))
                dst = tmp_dir / f'{Path(info.name).stem}-h264.mp4'
                try:
                    ok, out_path, message = transcode.transcode_to_mp4(
                        info.path,
                        dst,
                        on_progress=lambda pct, base=base: self._pump_progress(dialog, base + pct),
                    )
                finally:
                    pass
                if not ok:
                    failed.append(f'{info.name}：{message or "转码失败"}')
                    shutil.rmtree(tmp_dir, ignore_errors=True)
                    dialog.setValue(base + 100)
                    continue
                self._replace_with_converted(info, Path(out_path), converted, failed)
                shutil.rmtree(tmp_dir, ignore_errors=True)
                dialog.setValue(base + 100)
                self._status.setText(f'已转换：{info.name}')
        finally:
            dialog.close()
            dialog.deleteLater()
            self._set_busy(False)
        self._reload()
        lines = []
        if converted:
            lines.append('已转换 ' + str(len(converted)) + ' 段：' + '；'.join(converted))
        if failed:
            lines.append('⚠ 转码失败 ' + str(len(failed)) + ' 段：' + '；'.join(failed))
        if skipped:
            lines.append(f'已取消，还有 {skipped} 段没转换（仍标 ⚠，可以重新导入试一次）。')
        if lines:
            self._status.setText((header + '\n' if header else '') + '\n'.join(lines))
        if converted:
            self.videos_changed.emit()

    def _set_busy(self, busy: bool) -> None:
        """转码/下载期间禁用动作按钮：processEvents 会把点击送进来，得防重入。"""
        self._busy = busy
        for button in self._buttons:
            button.setEnabled(not busy)

    @staticmethod
    def _pump_progress(dialog: QProgressDialog, value: int) -> None:
        """转码回调只会在本线程执行：设值后让事件循环跑一下，进度条才会动。"""
        dialog.setValue(max(0, min(dialog.maximum(), int(value))))
        QApplication.processEvents()

    def _replace_with_converted(self, info: videos.VideoInfo, new_file: Path, converted: list[str], failed: list[str]) -> None:
        """把仓库条目换成转码后的文件：先删旧条目（原件随之删除），再入库新文件。"""
        try:
            videos.remove_video(info.id)
            new_info = videos.add_video(new_file)
        except (videos.VideoError, OSError) as exc:
            failed.append(f'{info.name}：已转码到 {new_file}，但替换旧条目失败（{exc}）')
            return
        try:
            new_file.unlink()
        except OSError:
            pass
        self._warned.pop(info.id, None)
        converted.append(new_info.name)

    def _on_download_tools(self) -> None:
        """下载 ffmpeg 转码组件（约 30~80MB）；成功/失败都要说清楚，不静默。"""
        if self._busy:
            return
        if transcode.available():
            self._refresh_list()
            self._status.setText('转码组件已就绪，导入非常规编码的视频会自动转换。')
            return
        answer = QMessageBox.question(self, '下载转码组件', '内置播放器只认 H.264 编码的 mp4。\n下载转码组件（约 30~80MB，只需一次）之后，导入的其它编码视频会自动转换成能播的格式。\n\n现在下载吗？', QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
        if answer != QMessageBox.Yes:
            return
        dialog = QProgressDialog('正在下载转码组件…', '', 0, 100, self)
        dialog.setCancelButton(None)
        dialog.setWindowTitle('下载转码组件')
        dialog.setWindowModality(Qt.WindowModal)
        dialog.setMinimumDuration(0)
        dialog.setAutoClose(False)
        dialog.setAutoReset(False)
        dialog.setValue(0)
        dialog.show()

        def _progress(pct: int) -> None:
            value = max(0, min(100, int(pct)))
            dialog.setValue(value)
            dialog.setLabelText(f'正在下载转码组件… {value}%')
            QApplication.processEvents()

        ok, message = False, ''
        self._set_busy(True)
        try:
            ok, message = transcode.download_ffmpeg(_progress)
        finally:
            dialog.close()
            dialog.deleteLater()
            self._set_busy(False)
        self._refresh_list()
        if ok:
            text = message or '转码组件已就绪。'
            self._status.setText(text + '\n之前标 ⚠ 的视频重新导入一次就会自动转换。')
            QMessageBox.information(self, '下载完成', text)
            return
        reason = message or '原因未知'
        self._status.setText('下载转码组件失败：' + reason)
        QMessageBox.warning(self, '下载转码组件失败', reason)

    def _on_play(self) -> None:
        info = self._selected()
        if info is None:
            self._status.setText('先在列表里选一段视频。')
            return
        player = make_player_dialog(info.path, self, auto_close=False)
        if player is None:
            if not open_video_external(info.path):
                self._status.setText('没有可用的视频播放器，无法试播。')
            return
        player.show()
        player.play()

    def _on_remove(self) -> None:
        info = self._selected()
        if info is None:
            self._status.setText('先在列表里选一段视频。')
            return
        answer = QMessageBox.question(self, '删除视频', f'从仓库删除「{info.name}」？\n绑定它的奖项会回到随机视频。', QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer != QMessageBox.Yes:
            return
        videos.remove_video(info.id)
        self._warned.pop(info.id, None)
        self._reload()
        self._status.setText(f'已删除「{info.name}」。')
        self.videos_changed.emit()
