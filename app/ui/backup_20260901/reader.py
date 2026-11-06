"""阅读器：在线/离线漫画阅读，支持章节跳转、预取、缩放、键盘翻页。
嵌入主窗口显示（不再弹独立窗口）。"""
import os

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (QComboBox, QHBoxLayout, QLabel, QPushButton,
                               QScrollArea, QVBoxLayout, QWidget)

from ..async_util import run_async
from ..downloader import list_images


class ReaderView(QWidget):
    back_requested = Signal()

    def __init__(self, service, settings, parent=None):
        super().__init__(parent)
        self.service = service
        self.settings = settings

        self._client = None
        self._album = None
        self._chapters = []   # 每项: {pid, index, title, photo, count, files}
        self._ci = 0
        self._pi = 0
        self._pix = None
        self._mode = 2        # 0 适配宽度 / 1 原始大小 / 2 适应窗口（默认）
        self._inflight = set()
        self._pending_last = False
        self._filling_combo = False

        # 顶栏
        self.chapter_combo = QComboBox()
        self.chapter_combo.setMinimumWidth(220)
        self.prev_btn = QPushButton('◀ 上一页')
        self.page_label = QLabel('')
        self.page_label.setObjectName('muted')
        self.page_label.setAlignment(Qt.AlignCenter)
        self.page_label.setMinimumWidth(180)
        self.next_btn = QPushButton('下一页 ▶')
        self.zoom_combo = QComboBox()
        for name in ('适配宽度', '原始大小', '适应窗口'):
            self.zoom_combo.addItem(name)
        self.zoom_combo.setCurrentIndex(2)
        self.back_btn = QPushButton('← 返回')
        self.back_btn.setProperty('cls', 'ghost')

        top = QHBoxLayout()
        top.setContentsMargins(10, 8, 10, 8)
        top.addWidget(self.back_btn)
        top.addSpacing(8)
        top.addWidget(self.chapter_combo)
        top.addSpacing(8)
        top.addWidget(self.prev_btn)
        top.addWidget(self.page_label)
        top.addWidget(self.next_btn)
        top.addStretch(1)
        top.addWidget(self.zoom_combo)

        topbar = QWidget()
        topbar.setObjectName('sidebar')
        topbar.setLayout(top)

        # 图片区
        self.img_label = QLabel('')
        self.img_label.setAlignment(Qt.AlignCenter)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setWidget(self.img_label)
        self.scroll.setStyleSheet('background:#101114; border:none;')
        self.img_label.setStyleSheet('background:#101114;')

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(topbar)
        lay.addWidget(self.scroll, 1)

        # 事件
        self.back_btn.clicked.connect(self.back_requested.emit)
        self.chapter_combo.currentIndexChanged.connect(self._on_chapter_combo)
        self.prev_btn.clicked.connect(self._prev)
        self.next_btn.clicked.connect(self._next)
        self.zoom_combo.currentIndexChanged.connect(self._on_zoom)
        self._bind_shortcuts()

    # -- 工具 --
    def _ensure_client(self):
        return self.service.shared_client()

    def _ch(self):
        return self._chapters[self._ci]

    def _count(self):
        ch = self._ch()
        if ch['files'] is not None:
            return len(ch['files'])
        return ch['count'] or 0

    def _cache_dir(self, ch):
        return os.path.join(self.settings.reader_cache_dir(), self._album.id, ch['pid'])

    def _bind_shortcuts(self):
        def sc(key, fn):
            QShortcut(QKeySequence(key), self, fn)
        sc(Qt.Key_Right, self._next)
        sc(Qt.Key_Down, self._next)
        sc(Qt.Key_PageDown, self._next)
        sc(Qt.Key_Space, self._next)
        sc(Qt.Key_Left, self._prev)
        sc(Qt.Key_Up, self._prev)
        sc(Qt.Key_PageUp, self._prev)
        sc(Qt.Key_Home, lambda: self._jump_page(0))
        sc(Qt.Key_End, lambda: self._jump_page(self._count() - 1))
        sc(Qt.Key_F, lambda: self.zoom_combo.setCurrentIndex(0))
        sc(Qt.Key_Escape, self.back_requested.emit)

    # -- 打开 --
    def show_album(self, album, chapter_index=0, local_chapters=None):
        self._album = album
        self._chapters = []
        for ch in album.chapters:
            self._chapters.append({'pid': ch.pid, 'index': ch.index, 'title': ch.title,
                                   'photo': None, 'count': None, 'files': None})
        if not self._chapters:
            self._set_status('本子没有可阅读的章节')
            return
        if local_chapters:
            for i, (pid, index, title, ch_dir) in enumerate(local_chapters):
                if i < len(self._chapters):
                    self._chapters[i]['files'] = list_images(ch_dir)
        self._filling_combo = True
        self.chapter_combo.clear()
        for ch in self._chapters:
            if len(self._chapters) > 1:
                label = f'第{ch["index"]}话 {ch["title"]}'
            else:
                label = ch['title'] or album.name
            self.chapter_combo.addItem(label)
        self._filling_combo = False
        self._ci = max(0, min(chapter_index, len(self._chapters) - 1))
        self._pending_last = False
        self.chapter_combo.setCurrentIndex(self._ci)
        self._activate()

    # -- 章节激活 --
    def _activate(self):
        ch = self._ch()
        if ch['files'] is not None:
            self._pi = self._count() - 1 if self._pending_last else 0
            self._pending_last = False
            self._show_page(self._pi)
            return
        if ch['photo'] is None:
            self._set_status('章节加载中…')
            self._pi = 0
            pid = ch['pid']
            def work():
                return self.service.get_photo(self._ensure_client(), pid)
            run_async(work, on_done=lambda p: self._on_photo_loaded(pid, p),
                      on_fail=lambda e: self._set_status(f'章节加载失败：{e[:60]}'))
        else:
            self._pi = self._count() - 1 if self._pending_last else 0
            self._pending_last = False
            self._show_page(self._pi)

    def _on_photo_loaded(self, pid, photo):
        for i, ch in enumerate(self._chapters):
            if ch['pid'] != pid:
                continue
            ch['photo'] = photo
            ch['count'] = len(photo)
            if i == self._ci:
                if self._pending_last:
                    self._pending_last = False
                    self._pi = len(photo) - 1
                self._show_page(self._pi)
            return

    def _on_chapter_combo(self, idx):
        if self._filling_combo or idx < 0 or idx == self._ci:
            return
        self._ci = idx
        self._pending_last = False
        self._activate()

    # -- 页面 --
    def _show_page(self, pi):
        ch = self._ch()
        total = self._count()
        if total <= 0:
            self._set_status('章节暂无图片')
            return
        pi = max(0, min(pi, total - 1))
        self._pi = pi
        if ch['files'] is not None:
            self._set_path(ch['files'][pi])
        else:
            img = ch['photo'][pi]
            path = os.path.join(self._cache_dir(ch), img.img_file_name + img.img_file_suffix)
            if os.path.exists(path):
                self._set_path(path)
            else:
                self._set_status(f'加载中… {pi + 1}/{total}')
                self._fetch_image(self._ci, pi, img)
        self._update_page_label()
        self._prefetch()

    def _fetch_image(self, ci, pi, img):
        key = (ci, pi)
        if key in self._inflight:
            return
        self._inflight.add(key)
        ch = self._chapters[ci]
        cache_dir = self._cache_dir(ch)
        os.makedirs(cache_dir, exist_ok=True)
        path = os.path.join(cache_dir, img.img_file_name + img.img_file_suffix)

        def work():
            if not os.path.exists(path):
                self.service.save_image(self._ensure_client(), img, path)
            return path

        run_async(work, on_done=lambda p: self._on_img_ready(ci, pi, p),
                  on_fail=lambda e: self._on_img_fail(ci, pi, e))

    def _on_img_ready(self, ci, pi, path):
        self._inflight.discard((ci, pi))
        if ci == self._ci and pi == self._pi:
            self._set_path(path)

    def _on_img_fail(self, ci, pi, err):
        self._inflight.discard((ci, pi))
        if ci == self._ci and pi == self._pi:
            self._set_status(f'图片加载失败：{err[:60]}')

    def _prefetch(self):
        ch = self._ch()
        # 本地文件模式读取很快，无需预取（photo 也为空，不可预取）
        if ch['files'] is not None:
            return
        count = self._count()
        for offset in (1, 2, 3):
            pi = self._pi + offset
            if pi >= count:
                break
            if (self._ci, pi) in self._inflight:
                continue
            img = ch['photo'][pi]
            self._fetch_image(self._ci, pi, img)

    def _set_path(self, path):
        pm = QPixmap(path)
        if pm.isNull():
            self._set_status('图片解码失败')
            return
        self._pix = pm
        self._apply_mode()

    def _set_status(self, text):
        self.img_label.setText(text)
        self.img_label.setAlignment(Qt.AlignCenter)
        self._pix = None

    # -- 缩放与显示 --
    def _on_zoom(self, i):
        self._mode = i
        self._apply_mode()

    def _apply_mode(self):
        if self._pix is None:
            return
        vp = self.scroll.viewport().size()
        if self._mode == 0:
            w = max(120, vp.width() - 6)
            scaled = self._pix.scaledToWidth(w, Qt.SmoothTransformation)
        elif self._mode == 2:
            scaled = self._pix.scaled(vp, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        else:
            scaled = self._pix
        self.img_label.setPixmap(scaled)
        self.scroll.verticalScrollBar().setValue(0)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._apply_mode()

    # -- 导航 --
    def _next(self):
        if self._count() <= 0:
            return
        if self._pi + 1 < self._count():
            self._show_page(self._pi + 1)
        elif self._ci + 1 < len(self._chapters):
            self._ci += 1
            self._pending_last = False
            self.chapter_combo.setCurrentIndex(self._ci)
            self._activate()

    def _prev(self):
        if self._pi > 0:
            self._show_page(self._pi - 1)
        elif self._ci > 0:
            self._ci -= 1
            self._pending_last = True
            self.chapter_combo.setCurrentIndex(self._ci)
            self._activate()

    def _jump_page(self, pi):
        if self._count() > 0:
            self._show_page(pi)

    def _update_page_label(self):
        total = self._count()
        self.page_label.setText(f'第 {self._pi + 1} / {total} 页' if total > 0 else '')
