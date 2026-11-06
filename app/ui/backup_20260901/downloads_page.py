"""下载管理页：本次下载任务列表 + 已下载漫画书架（封面卡片墙）。"""
import os
import shutil

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QCursor, QDesktopServices
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QMenu, QMessageBox,
                               QProgressBar, QPushButton, QScrollArea,
                               QVBoxLayout, QWidget)

from ..async_util import run_async
from ..downloader import JobState, STATE_CN, scan_local_albums
from .. import export as export_mod
from .widgets import AlbumCard, FlowLayout

THUMB = '_3x4'

STATE_COLOR = {
    JobState.WAITING: '#9aa0ad',
    JobState.FETCH: '#d8a13a',
    JobState.DOWN: '#e8556d',
    JobState.DONE: '#3fb97f',
    JobState.FAILED: '#e8556d',
    JobState.CANCEL: '#7d8491',
}


class JobRow(QFrame):
    def __init__(self, job, manager, parent=None):
        super().__init__(parent)
        self.setProperty('cls', 'card')
        self.job = job
        self.manager = manager

        self.state_label = QLabel()
        self.title = QLabel()
        self.title.setObjectName('bigTitle')

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)

        self.cancel_btn = QPushButton('取消')
        self.retry_btn = QPushButton('重试')
        self.folder_btn = QPushButton('打开文件夹')
        self.remove_btn = QPushButton('移除')

        row2 = QHBoxLayout()
        row2.addWidget(self.progress, 1)
        row2.addWidget(self.cancel_btn)
        row2.addWidget(self.retry_btn)
        row2.addWidget(self.folder_btn)
        row2.addWidget(self.remove_btn)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(8)
        lay.addWidget(self.state_label)
        lay.addWidget(self.title)
        lay.addLayout(row2)

        self.cancel_btn.clicked.connect(lambda: self.manager.cancel(self.job.aid))
        self.retry_btn.clicked.connect(
            lambda: self.manager.submit(self.job.aid, self.job.name,
                                        chapter_indices=getattr(self.job, '_chapter_indices', None)))
        self.folder_btn.clicked.connect(self._open_folder)
        self.remove_btn.clicked.connect(lambda: self.manager.remove(self.job.aid))

    def _open_folder(self):
        if self.job.save_dir:
            QDesktopServices.openUrl(QUrl.fromLocalFile(self.job.save_dir))

    def update_ui(self):
        job = self.job
        state = job.state
        self.state_label.setText(f'{STATE_CN.get(state, state)}'
                                 + (f'  · 失败 {job.failed} 张' if job.failed else ''))
        self.state_label.setStyleSheet(f'color:{STATE_COLOR.get(state, "#fff")}; font-weight:600;')
        self.title.setText(f'{job.name}  [{job.aid}]')
        if job.total > 0:
            self.progress.setValue(job.progress)
            self.progress.setFormat(f'{job.done}/{job.total}  {job.progress}%')
        else:
            self.progress.setValue(0)
            self.progress.setFormat('获取章节中…')
        active = state in (JobState.WAITING, JobState.FETCH, JobState.DOWN)
        self.cancel_btn.setVisible(active)
        self.retry_btn.setVisible(not active and (state == JobState.FAILED or job.failed > 0))
        self.folder_btn.setVisible(bool(job.save_dir))
        self.remove_btn.setVisible(not active)


class DownloadsPage(QWidget):
    open_local = Signal(dict)
    export_done = Signal(str)

    def __init__(self, manager, settings, covers, parent=None):
        super().__init__(parent)
        self.manager = manager
        self.settings = settings
        self.covers = covers
        self._rows = {}
        self._local_cards = []
        self._last_states = {}

        header = QHBoxLayout()
        header.setContentsMargins(18, 16, 18, 8)
        title = QLabel('下载管理')
        title.setObjectName('panelTitle')
        open_dir = QPushButton('打开下载目录')
        open_dir.setProperty('cls', 'ghost')
        open_dir.clicked.connect(self._open_download_dir)
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(open_dir)

        # 本次任务
        self.task_label = QLabel('本次任务')
        self.task_label.setObjectName('panelTitle')
        self.list_box = QVBoxLayout()
        self.list_box.setSpacing(10)
        self.task_msg = QLabel('暂无下载任务\n在漫画详情页点击“下载本子”即可开始')
        self.task_msg.setAlignment(Qt.AlignCenter)
        self.task_msg.setObjectName('muted')

        # 已下载书架
        self.local_label = QLabel('已下载漫画')
        self.local_label.setObjectName('panelTitle')
        self._flow_slot = QVBoxLayout()
        self._flow_slot.setSpacing(0)

        container = QWidget()
        body = QVBoxLayout(container)
        body.setContentsMargins(18, 0, 18, 24)
        body.setSpacing(10)
        body.addWidget(self.task_label)
        body.addLayout(self.list_box)
        body.addWidget(self.task_msg)
        body.addSpacing(18)
        body.addWidget(self.local_label)
        body.addLayout(self._flow_slot)
        body.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(container)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addLayout(header)
        root.addWidget(scroll, 1)

        self.manager.updated.connect(self.sync)
        self.covers.loaded.connect(self._on_cover)
        self.refresh_local()

    def _open_download_dir(self):
        d = self.settings['download_dir']
        os.makedirs(d, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(d))

    def showEvent(self, event):
        super().showEvent(event)
        self.sync()
        self.refresh_local()

    def sync(self, aid=None):
        for k in [k for k in self._rows if k not in self.manager._by_aid]:
            row = self._rows.pop(k)
            self.list_box.removeWidget(row)
            row.deleteLater()
        # 任务完成时自动刷新书架（新下载的漫画立即出现）
        finished = False
        for job in self.manager.jobs:
            prev = self._last_states.get(job.aid)
            self._last_states[job.aid] = job.state
            if (prev in (JobState.WAITING, JobState.FETCH, JobState.DOWN)
                    and job.state not in (JobState.WAITING, JobState.FETCH, JobState.DOWN)):
                finished = True
            row = self._rows.get(job.aid)
            if row is None:
                row = JobRow(job, self.manager)
                self._rows[job.aid] = row
                self.list_box.insertWidget(0, row)
            row.update_ui()
        for aid in [a for a in self._last_states if a not in self.manager._by_aid]:
            del self._last_states[aid]
        if finished:
            self.refresh_local()
        has = bool(self.manager.jobs)
        self.task_msg.setVisible(not has)
        self.task_label.setVisible(has)

    # -- 已下载书架 --
    def refresh_local(self):
        # 清空书架
        while self._flow_slot.count():
            item = self._flow_slot.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._local_cards = []

        # 只排除仍在下载的任务；已完成/失败的任务立即进入书架
        active_aids = {a for a, j in self.manager._by_aid.items()
                       if j.state in (JobState.WAITING, JobState.FETCH, JobState.DOWN)}
        items = [it for it in scan_local_albums(self.settings['download_dir'])
                 if it['aid'] not in active_aids]
        if not items:
            msg = QLabel('书架还空着——在漫画详情页点“下载本子”，完成后会出现在这里')
            msg.setAlignment(Qt.AlignCenter)
            msg.setObjectName('muted')
            msg.setMinimumHeight(120)
            self._flow_slot.addWidget(msg)
            return

        flow_host = QWidget()
        flow = FlowLayout(flow_host)
        for it in items:
            card = AlbumCard(it['aid'], it['name'])
            card.clicked.connect(lambda aid, d=it: self.open_local.emit(d))
            card.context_requested.connect(lambda aid, d=it: self._card_menu(d))
            # 优先用本地封面兜底，再异步换成 3:4 封面
            cover = os.path.join(it['dir'], 'cover.jpg')
            if os.path.exists(cover):
                card.set_cover(cover)
            self.covers.request(it['aid'], THUMB)
            flow.addWidget(card)
            self._local_cards.append((card, it))
        self._flow_slot.addWidget(flow_host)

    def _on_cover(self, aid, size, path):
        if size != THUMB:
            return
        for card, data in self._local_cards:
            if card.aid == aid:
                card.set_cover(path)
                return

    def _card_menu(self, data):
        menu = QMenu(self)
        act_read = menu.addAction('离线阅读')
        act_folder = menu.addAction('打开文件夹')
        menu.addSeparator()
        act_cbz = menu.addAction('导出 CBZ')
        act_pdf = menu.addAction('导出 PDF')
        menu.addSeparator()
        act_delete = menu.addAction('删除文件')
        chosen = menu.exec(QCursor.pos())
        if chosen == act_read:
            self.open_local.emit(data)
        elif chosen == act_folder:
            QDesktopServices.openUrl(QUrl.fromLocalFile(data['dir']))
        elif chosen == act_cbz:
            self._export(data, 'cbz')
        elif chosen == act_pdf:
            self._export(data, 'pdf')
        elif chosen == act_delete:
            self._delete_local(data)

    def _export(self, data, kind):
        export_dir = os.path.join(self.settings['download_dir'], '导出')
        label = 'CBZ' if kind == 'cbz' else 'PDF'

        def work():
            if kind == 'cbz':
                return export_mod.export_cbz(data, export_dir)
            return export_mod.export_pdf(data, export_dir)

        def done(path):
            self.export_done.emit(f'{label} 导出完成：{path}')

        def fail(err):
            self.export_done.emit(f'{label} 导出失败：{err[:80]}')

        self.export_done.emit(f'正在导出 {label}…')
        run_async(work, on_done=done, on_fail=fail)

    def _delete_local(self, data):
        ret = QMessageBox.question(
            self, '删除下载文件',
            f'确定要删除《{data["name"]}》的本地文件吗？\n{data["dir"]}\n\n此操作不可恢复。',
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ret == QMessageBox.Yes:
            shutil.rmtree(data['dir'], ignore_errors=True)
            self.refresh_local()
