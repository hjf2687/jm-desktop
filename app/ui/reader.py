"""阅读器：在线/离线漫画阅读，支持章节跳转、预取、缩放、键盘翻页。
嵌入主窗口显示（不再弹独立窗口）。
支持两种阅读模式：翻页（单页显示）/ 滚动（整章纵向连续无缝滚动）。"""
import os

from PySide6.QtCore import QEvent, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QImage, QImageReader, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (QComboBox, QHBoxLayout, QLabel, QPushButton,
                               QScrollArea, QVBoxLayout, QWidget)

from .. import progress
from ..async_util import run_async
from ..downloader import list_images

# 模式默认缩放：翻页→适应窗口，滚动→适配宽度
PAGE_DEFAULT_ZOOM = 2
SCROLL_DEFAULT_ZOOM = 0


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
        self._mode = PAGE_DEFAULT_ZOOM  # 0 适配宽度 / 1 原始大小 / 2 适应窗口
        self._inflight = set()
        self._pending_last = False
        self._pending_page = None   # 打开时定位到的起始页（一次性）
        self._filling_combo = False

        # 滚动模式状态
        self._scroll_mode = False   # False=翻页 True=滚动
        self._strip = None          # 滚动模式的内容容器
        self._strip_lay = None
        self._strip_labels = []     # 每页一个 QLabel
        self._strip_imgs = {}       # pi -> QImage（原始图）
        self._strip_sizes = {}      # pi -> (w, h) 原始尺寸（读图片头所得）
        self._strip_est = (800, 1200)  # 未知尺寸时的估算值（取已知的中位数）
        self._strip_loaded = set()  # 已请求加载的 pi
        self._strip_token = 0       # 每次重建 strip 递增，用于丢弃过期回调

        # 顶栏
        self.chapter_combo = QComboBox()
        self.chapter_combo.setMinimumWidth(220)
        self.prev_btn = QPushButton('◀ 上一页')
        self.page_label = QLabel('')
        self.page_label.setObjectName('muted')
        self.page_label.setAlignment(Qt.AlignCenter)
        self.page_label.setMinimumWidth(180)
        self.next_btn = QPushButton('下一页 ▶')
        self.mode_combo = QComboBox()
        self.mode_combo.setToolTip('翻页：单页显示（默认适应窗口）\n滚动：整章连续滚动（默认适配宽度）')
        self.mode_combo.addItem('翻页')
        self.mode_combo.addItem('滚动')
        self.zoom_combo = QComboBox()
        for name in ('适配宽度', '原始大小', '适应窗口'):
            self.zoom_combo.addItem(name)
        self.zoom_combo.setCurrentIndex(PAGE_DEFAULT_ZOOM)
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
        top.addWidget(self.mode_combo)
        top.addSpacing(8)
        top.addWidget(self.zoom_combo)

        topbar = QWidget()
        topbar.setObjectName('sidebar')
        topbar.setLayout(top)

        # 图片区
        self.img_label = QLabel('')
        self.img_label.setAlignment(Qt.AlignCenter)
        self.img_label.setAttribute(Qt.WA_TransparentForMouseEvents)  # 鼠标事件穿透到视口（中键滚动）
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
        self.mode_combo.currentIndexChanged.connect(self._on_read_mode)
        self.zoom_combo.currentIndexChanged.connect(self._on_zoom)
        self._bind_shortcuts()

        # 阅读进度防抖写入（快速连续翻页只落盘最后一次）
        self._progress_timer = QTimer(self)
        self._progress_timer.setSingleShot(True)
        self._progress_timer.setInterval(600)
        self._progress_timer.timeout.connect(self._save_progress_now)

        # 中键自动滚动（Chrome 风格）：按住中键 -> 鼠标移出死区 -> 持续自动滚动，
        # 速度随偏移量增大；再点一下中键（或左键/滚轮/移出窗口）停止
        self._auto_on = False
        self._auto_anchor = None
        self._auto_dir = 0        # -1 向上 / 0 停 / 1 向下
        self._auto_speed = 0      # 每 tick 像素（翻页模式按此累积翻页）
        self._auto_page_acc = 0
        self._auto_timer = QTimer(self)
        self._auto_timer.setInterval(16)
        self._auto_timer.timeout.connect(self._auto_tick)
        self.scroll.viewport().installEventFilter(self)

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
        sc(Qt.Key_Down, lambda: self._key_vertical(120))
        sc(Qt.Key_PageDown, self._next)
        sc(Qt.Key_Space, self._next)
        sc(Qt.Key_Left, self._prev)
        sc(Qt.Key_Up, lambda: self._key_vertical(-120))
        sc(Qt.Key_PageUp, self._prev)
        sc(Qt.Key_Home, lambda: self._jump_page(0))
        sc(Qt.Key_End, lambda: self._jump_page(self._count() - 1))
        sc(Qt.Key_F, lambda: self.zoom_combo.setCurrentIndex(0))
        sc(Qt.Key_Escape, self.back_requested.emit)

    # -- 中键自动滚动 --
    def eventFilter(self, obj, ev):
        if obj is self.scroll.viewport():
            t = ev.type()
            if t == QEvent.Type.MouseButtonPress:
                if ev.button() == Qt.MiddleButton:
                    # 再按一次中键 -> 停止
                    if self._auto_on:
                        self._auto_stop()
                    else:
                        self._auto_start(ev.position())
                    return True
                if self._auto_on:  # 左键/右键按下 -> 停止并吞掉
                    self._auto_stop()
                    return True
            elif t == QEvent.Type.MouseMove and self._auto_on:
                self._auto_update(ev.position())
                return True
            elif t == QEvent.Type.MouseButtonRelease and self._auto_on:
                # 松手不停止（继续自动滚动，Chrome 风格）
                return True
            elif t == QEvent.Type.Wheel:
                if self._auto_on:
                    self._auto_stop()
            elif t == QEvent.Type.Leave and self._auto_on:
                self._auto_stop()
        return super().eventFilter(obj, ev)

    def _auto_start(self, pos):
        self._auto_on = True
        self._auto_anchor = pos
        self._auto_dir = 0
        self._auto_speed = 0
        self._auto_page_acc = 0
        self._auto_timer.start()
        self.scroll.viewport().setCursor(Qt.ClosedHandCursor)

    def _auto_update(self, pos):
        if self._auto_anchor is None:
            return
        dy = pos.y() - self._auto_anchor.y()
        if abs(dy) < 20:  # 死区：停在原地
            self._auto_dir = 0
            self._auto_speed = 0
            return
        self._auto_dir = 1 if dy > 0 else -1  # 鼠标往下 -> 内容往下滚
        self._auto_speed = min(28, int((abs(dy) - 20) / 25 * 1.2) + 1)

    def _auto_stop(self):
        if not self._auto_on:
            return
        self._auto_on = False
        self._auto_timer.stop()
        self._auto_dir = 0
        self._auto_speed = 0
        self.scroll.viewport().unsetCursor()

    def _auto_tick(self):
        if not self._auto_on or not self._auto_dir:
            return
        if self._scroll_mode:
            sb = self.scroll.verticalScrollBar()
            v = sb.value()
            if self._auto_dir > 0:
                if v >= sb.maximum() - 4:
                    self._goto_chapter(self._ci + 1)
                    return
            elif v <= sb.minimum() + 4:
                self._goto_chapter(self._ci - 1, at_end=True)
                return
            sb.setValue(v + self._auto_dir * self._auto_speed)
        else:
            # 翻页模式：按屏幕高度累计，滚满一屏翻一页
            self._auto_page_acc += self._auto_speed * 0.7
            if self._auto_page_acc >= self.scroll.viewport().height():
                self._auto_page_acc = 0
                if self._auto_dir > 0:
                    self._next()
                else:
                    self._prev()

    def hideEvent(self, event):
        self._auto_stop()
        super().hideEvent(event)

    # -- 打开 --
    def show_album(self, album, chapter_index=0, local_chapters=None, start_page=None):
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
        self._pending_page = start_page  # “继续阅读”：定位到上次页码
        self.chapter_combo.setCurrentIndex(self._ci)
        self._activate()

    def _start_pi(self):
        """章节起始页：末尾（_pending_last）/ 指定页（_pending_page，一次性）/ 第 0 页。"""
        if self._pending_last:
            return max(0, self._count() - 1)
        if self._pending_page is not None:
            p = self._pending_page
            self._pending_page = None
            return max(0, min(int(p), max(0, self._count() - 1)))
        return 0

    # -- 章节激活 --
    def _activate(self):
        ch = self._ch()
        if self._scroll_mode:
            if ch['files'] is not None or ch['photo'] is not None:
                self._build_strip()
                return
            self._set_status('章节加载中…')
            pid = ch['pid']

            def work():
                return self.service.get_photo(self._ensure_client(), pid)
            run_async(work, on_done=lambda p: self._on_photo_loaded(pid, p),
                      on_fail=lambda e: self._set_status(f'章节加载失败：{e[:60]}'))
            return
        if ch['files'] is not None:
            self._pi = self._start_pi()
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
            self._pi = self._start_pi()
            self._pending_last = False
            self._show_page(self._pi)

    def _on_photo_loaded(self, pid, photo):
        for i, ch in enumerate(self._chapters):
            if ch['pid'] != pid:
                continue
            ch['photo'] = photo
            ch['count'] = len(photo)
            if i == self._ci:
                if self._scroll_mode:
                    self._build_strip()
                else:
                    if self._pending_last:
                        self._pending_last = False
                        self._pi = len(photo) - 1
                    elif self._pending_page is not None:
                        self._pi = self._start_pi()
                        self._pi = min(self._pi, len(photo) - 1)
                    self._show_page(self._pi)
            return

    def _on_chapter_combo(self, idx):
        if self._filling_combo or idx < 0 or idx == self._ci:
            return
        self._ci = idx
        self._pending_last = False
        self._activate()

    def _goto_chapter(self, ci, at_end=False):
        if not (0 <= ci < len(self._chapters)):
            return
        self._ci = ci
        self._pending_last = at_end
        self.chapter_combo.setCurrentIndex(ci)
        self._activate()

    # -- 页面（翻页模式） --
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
        self._save_progress()
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
        if self._scroll_mode:
            if ci == self._ci:
                self._decode_async(pi, path)
        elif ci == self._ci and pi == self._pi:
            self._set_path(path)

    def _on_img_fail(self, ci, pi, err):
        self._inflight.discard((ci, pi))
        if self._scroll_mode:
            if ci == self._ci and pi < len(self._strip_labels):
                self._strip_labels[pi].setText(f'第 {pi + 1} 页加载失败')
        elif ci == self._ci and pi == self._pi:
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
        if self._scroll_mode:
            self.page_label.setText(text)
            return
        self.img_label.setText(text)
        self.img_label.setAlignment(Qt.AlignCenter)
        self._pix = None

    # -- 缩放与显示 --
    def _on_zoom(self, i):
        self._mode = i
        self._apply_mode()

    def _set_zoom(self, idx):
        """静默设置缩放（不触发重复刷新）。"""
        self.zoom_combo.blockSignals(True)
        self.zoom_combo.setCurrentIndex(idx)
        self.zoom_combo.blockSignals(False)
        self._mode = idx

    def _apply_mode(self):
        if self._scroll_mode:
            if self._strip_imgs:
                # 解码尺寸随缩放模式变化：作废在途解码、清掉已解码图，按新模式重解码
                self._strip_token += 1
                self._strip_imgs.clear()
                self._strip_loaded.clear()
                for pi, lab in enumerate(self._strip_labels):
                    lab.setPixmap(QPixmap())
                    lab.setText(f'{pi + 1} / {len(self._strip_labels)}')
            self._update_strip_heights()
            self._on_strip_scrolled()
            return
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

    # -- 阅读模式切换（各自恢复默认缩放） --
    def _on_read_mode(self, idx):
        scroll = idx == 1
        if scroll == self._scroll_mode:
            return
        self._scroll_mode = scroll
        if scroll:
            self._pending_page = self._pi  # 翻页切滚动：保持在当前页，不跳回开头
            self._set_zoom(SCROLL_DEFAULT_ZOOM)  # 滚动模式默认适配宽度
            self._swap_widget(self._ensure_strip())
            self._activate()
        else:
            self._set_zoom(PAGE_DEFAULT_ZOOM)    # 翻页模式默认适应窗口
            self._clear_strip()
            self._swap_widget(self.img_label)
            self._show_page(self._pi)

    def _swap_widget(self, w):
        self.scroll.takeWidget()
        self.scroll.setWidget(w)

    def _ensure_strip(self):
        if self._strip is None:
            self._strip = QWidget()
            self._strip_lay = QVBoxLayout(self._strip)
            self._strip_lay.setContentsMargins(0, 0, 0, 0)
            self._strip_lay.setSpacing(0)  # 页与页零间距，拼接被裁断的漫画
            self._strip.setStyleSheet('background:#101114;')
            self.scroll.verticalScrollBar().valueChanged.connect(self._on_strip_scrolled)
        return self._strip

    def _clear_strip(self):
        if self._strip_lay is not None:
            while self._strip_lay.count():
                item = self._strip_lay.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()
        self._strip_labels = []
        self._strip_imgs = {}
        self._strip_sizes = {}
        self._strip_loaded = set()
        self._strip_token += 1

    # -- 滚动模式 --
    @staticmethod
    def _read_size(path):
        """只读图片头拿尺寸，不解码整图，开销极小。"""
        try:
            r = QImageReader(path)
            s = r.size()
            if s.isValid() and s.width() > 0 and s.height() > 0:
                return (s.width(), s.height())
        except Exception:
            pass
        return None

    def _strip_path(self, pi):
        """该页的本地路径（本地章节或已缓存），没有则返回 None。"""
        ch = self._ch()
        if ch['files'] is not None:
            return ch['files'][pi]
        if ch['photo'] is not None:
            img = ch['photo'][pi]
            p = os.path.join(self._cache_dir(ch), img.img_file_name + img.img_file_suffix)
            return p if os.path.exists(p) else None
        return None

    def _strip_label_height(self, pi):
        """按当前缩放模式计算该页的精确占位高度（与解码行为保持一致）。"""
        size = self._strip_sizes.get(pi) or self._strip_est
        w, h = size
        if self._mode in (0, 2):
            vp_w = max(120, self.scroll.viewport().width() - 6)
            if w <= vp_w:
                return max(120, h)  # 窄图不放大，占位=原始高度
            return max(120, int(h * vp_w / w))
        return max(120, h)

    def _update_strip_heights(self):
        for i, lab in enumerate(self._strip_labels):
            if i in self._strip_imgs:
                continue  # 已显示的图片，高度由 pixmap 决定
            lab.setMinimumHeight(self._strip_label_height(i))

    def _build_strip(self):
        self._clear_strip()
        total = self._count()
        if total <= 0:
            self._set_status('章节暂无图片')
            return
        ch = self._ch()
        # 先用图片头读出每页真实尺寸，精确占位（防止加载后版面跳动）
        for i in range(total):
            path = self._strip_path(i)
            if path and os.path.exists(path):
                size = self._read_size(path)
                if size:
                    self._strip_sizes[i] = size
        if self._strip_sizes:
            ws = sorted(w for w, _ in self._strip_sizes.values())
            hs = sorted(h for _, h in self._strip_sizes.values())
            self._strip_est = (ws[len(ws) // 2], hs[len(hs) // 2])
        for i in range(total):
            lab = QLabel(f'{i + 1} / {total}')
            lab.setAlignment(Qt.AlignCenter)
            lab.setStyleSheet('color:#9aa0ad; background:#101114;')
            lab.setAttribute(Qt.WA_TransparentForMouseEvents)  # 鼠标事件穿透到视口（中键滚动）
            lab.setMinimumHeight(self._strip_label_height(i))
            self._strip_lay.addWidget(lab)
            self._strip_labels.append(lab)
        self._pi = 0
        self._update_page_label()
        # 等布局完成后再定位与按需加载
        token = self._strip_token
        QTimer.singleShot(30, lambda: self._after_strip_build(token))

    def _after_strip_build(self, token):
        if not self._scroll_mode or token != self._strip_token:
            return
        if self._pending_last:
            self._pending_last = False
            sb = self.scroll.verticalScrollBar()
            sb.setValue(sb.maximum())
        elif self._pending_page is not None:
            p, self._pending_page = self._pending_page, None
            self._jump_page(int(p))
        self._on_strip_scrolled()

    def _on_strip_scrolled(self, *_):
        if not self._scroll_mode or not self._strip_labels:
            return
        sb = self.scroll.verticalScrollBar()
        top = sb.value()
        vh = self.scroll.viewport().height()
        bottom = top + vh
        cur = self._pi
        load = []
        # 当前页 = 视口上部锚点所在的页（跳页/滚动能准确定位，不被邻页边缘干扰）
        anchor = top + max(16, int(vh * 0.12))
        found = False
        for i, lab in enumerate(self._strip_labels):
            y = lab.y()
            h = lab.height()
            if y <= anchor < y + h:
                cur = i
                found = True
            # 可见范围前后 3 屏预载
            if y + h >= top - 3 * vh and y <= bottom + 3 * vh:
                load.append(i)
        if not found:
            # 锚点未命中（如布局未完成）时退回"最后一个可见页"
            for i, lab in enumerate(self._strip_labels):
                if lab.y() + lab.height() >= top and lab.y() <= bottom:
                    cur = i
        if cur != self._pi:
            self._pi = cur
            self._update_page_label()
            self._save_progress()
        for i in load:
            self._strip_ensure_page(i)
        # 卸载远离当前页的图片，控制内存
        for i in list(self._strip_imgs):
            if abs(i - cur) > 8:
                self._strip_imgs.pop(i, None)
                self._strip_loaded.discard(i)
                lab = self._strip_labels[i]
                lab.setPixmap(QPixmap())
                lab.setMinimumHeight(self._strip_label_height(i))
                lab.setText(f'{i + 1} / {len(self._strip_labels)}')

    def _strip_ensure_page(self, pi):
        if pi in self._strip_loaded or pi >= len(self._strip_labels):
            return
        ch = self._ch()
        if ch['files'] is not None:
            path = ch['files'][pi]
        else:
            if ch['photo'] is None:
                return
            img = ch['photo'][pi]
            path = os.path.join(self._cache_dir(ch), img.img_file_name + img.img_file_suffix)
            if not os.path.exists(path):
                self._fetch_image(self._ci, pi, img)
                return
        self._strip_loaded.add(pi)
        self._decode_async(pi, path)

    def _decode_async(self, pi, path):
        token = self._strip_token
        # 适配宽度/适应窗口：解码时直接按目标宽度缩放（JPEG 走 DCT 快速缩放，
        # 比“全尺寸解码 + 再缩放”快数倍）；原始大小则全尺寸解码
        target_w = 0
        if self._mode in (0, 2):
            target_w = max(120, self.scroll.viewport().width() - 6)

        def work():
            r = QImageReader(path)
            raw = r.size()
            if target_w and raw.isValid() and raw.width() > target_w:
                h = max(1, int(raw.height() * target_w / raw.width()))
                r.setScaledSize(QSize(target_w, h))
            img = r.read()
            return img, (raw.width(), raw.height()) if raw.isValid() else None

        run_async(work, on_done=lambda res, p=pi, t=token: self._on_strip_decoded(p, res, t),
                  on_fail=lambda e, p=pi: self._on_strip_fail(p, e))

    def _on_strip_decoded(self, pi, result, token):
        image, raw = result
        if not self._scroll_mode or token != self._strip_token or image.isNull():
            return
        if pi >= len(self._strip_labels):
            return
        # 记录原始尺寸（读图片头所得），纠正估算误差
        if raw:
            self._strip_sizes[pi] = raw
        self._strip_imgs[pi] = image
        self._strip_apply(pi)

    def _on_strip_fail(self, pi, err):
        if self._scroll_mode and pi < len(self._strip_labels):
            self._strip_labels[pi].setText(f'第 {pi + 1} 页加载失败')

    def _strip_apply(self, pi):
        image = self._strip_imgs.get(pi)
        if image is None or image.isNull() or pi >= len(self._strip_labels):
            return
        lab = self._strip_labels[pi]
        pm = QPixmap.fromImage(image)  # 解码时已按当前缩放模式输出目标尺寸，无需再缩放
        sb = self.scroll.verticalScrollBar()
        top = sb.value()
        old_h = max(lab.minimumHeight(), lab.height())
        delta = pm.height() - old_h
        above = lab.y() < top  # 图在当前视口上方：加载后需补偿滚动位置
        lab.setMinimumHeight(0)
        lab.setText('')
        lab.setPixmap(pm)
        if above and delta:
            sb.setValue(top + delta)  # 保持画面不跳动

    # -- 导航 --
    def _next(self):
        if self._scroll_mode:
            self._strip_scroll_by(1)
            return
        if self._count() <= 0:
            return
        if self._pi + 1 < self._count():
            self._show_page(self._pi + 1)
        elif self._ci + 1 < len(self._chapters):
            self._goto_chapter(self._ci + 1)

    def _prev(self):
        if self._scroll_mode:
            self._strip_scroll_by(-1)
            return
        if self._pi > 0:
            self._show_page(self._pi - 1)
        elif self._ci > 0:
            self._goto_chapter(self._ci - 1, at_end=True)

    def _strip_scroll_by(self, step):
        sb = self.scroll.verticalScrollBar()
        if step > 0 and sb.value() >= sb.maximum() - 4:
            self._goto_chapter(self._ci + 1)
            return
        if step < 0 and sb.value() <= sb.minimum() + 4:
            self._goto_chapter(self._ci - 1, at_end=True)
            return
        sb.setValue(sb.value() + int(step * self.scroll.viewport().height() * 0.9))

    def _key_vertical(self, delta):
        # 滚动模式下 ↑↓ 细滚动；翻页模式下等同翻页键
        if self._scroll_mode:
            self.scroll.verticalScrollBar().setValue(
                self.scroll.verticalScrollBar().value() + delta)
        elif delta > 0:
            self._next()
        else:
            self._prev()

    def _jump_page(self, pi):
        if self._count() <= 0:
            return
        if self._scroll_mode:
            if not self._strip_labels:
                return
            pi = max(0, min(pi, len(self._strip_labels) - 1))
            sb = self.scroll.verticalScrollBar()
            sb.setValue(max(0, self._strip_labels[pi].y() - 8))
            self._on_strip_scrolled()
            return
        self._show_page(pi)

    def _update_page_label(self):
        total = self._count()
        self.page_label.setText(f'第 {self._pi + 1} / {total} 页' if total > 0 else '')

    # -- 阅读进度记忆 --
    def _save_progress(self):
        if self._album is None or self._count() <= 0:
            return
        self._progress_timer.start()  # 防抖：快速连续翻页只落盘最后一次

    def _save_progress_now(self):
        if self._album is None or self._count() <= 0:
            return
        try:
            ch = self._ch()
            progress.save(self._album.id, getattr(self._album, 'name', ''),
                          self._ci, self._pi, self._count(), ch.get('title') or '')
        except Exception:
            pass
