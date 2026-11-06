"""详情页：本子信息、章节列表、下载/阅读/收藏操作。"""
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QListWidget,
                               QListWidgetItem, QMessageBox, QPushButton,
                               QScrollArea, QVBoxLayout, QWidget)

from .. import progress
from ..async_util import run_async
from ..downloader import find_album_dir
from .widgets import COVER_W, COVER_H

COVER_BIG_W, COVER_BIG_H = 220, 300


def _linkify(items, prefix=''):
    """把字符串列表拼成可点击的富文本。"""
    if not items:
        return ''
    return '  '.join(f'<a href="{it}" style="color:#ff9fae;">{prefix}{it}</a>' for it in items)


class AlbumPage(QWidget):
    back = Signal()
    read_requested = Signal(str, int, object)  # aid, chapter_index, start_page(None=从头)
    download_requested = Signal(str, object)  # aid, 章节下标列表（None=整本）
    search_requested = Signal(str, str)    # mode, query

    def __init__(self, service, settings, covers, store, parent=None):
        super().__init__(parent)
        self.service = service
        self.settings = settings
        self.covers = covers
        self.store = store
        self.aid = None
        self._info = None
        self._local_dir = ''

        # 加载中/错误
        self.state_label = QLabel('加载中…')
        self.state_label.setAlignment(Qt.AlignCenter)
        self.state_label.setObjectName('muted')
        self.state_label.setWordWrap(True)

        # 内容
        self.content = self._build_content()
        self.stack_body = QWidget()
        lay = QVBoxLayout(self.stack_body)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.state_label, 1)
        lay.addWidget(self.content, 1)
        self.state_label.show()
        self.content.hide()

        body = QVBoxLayout(self)
        body.setContentsMargins(0, 0, 0, 0)
        body.addWidget(self.stack_body)
        self.covers.loaded.connect(self._on_cover)

        # 信号连接只做一次（避免每次渲染重复绑定导致点击多次触发）
        self.read_btn.clicked.connect(lambda: self.read_requested.emit(self.aid or '', 0, None))
        self.resume_btn.clicked.connect(self._on_resume)
        self.dl_btn.clicked.connect(lambda: self.download_requested.emit(self.aid or '', None))
        self.dl_sel_btn.clicked.connect(self._on_download_selected)
        self.fav_btn.clicked.connect(self._toggle_fav)
        self.local_btn.clicked.connect(self._open_local_dir)
        self.tags.linkActivated.connect(self._on_link)
        self.meta.linkActivated.connect(self._on_link)

    def _build_content(self):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        vbox = QVBoxLayout(inner)
        vbox.setContentsMargins(26, 14, 26, 30)
        vbox.setSpacing(12)

        # 返回行
        back_btn = QPushButton('← 返回')
        back_btn.setProperty('cls', 'ghost')
        back_btn.clicked.connect(self.back.emit)
        back_row = QHBoxLayout()
        back_row.addWidget(back_btn)
        back_row.addStretch(1)
        vbox.addLayout(back_row)

        # 头部
        self.cover = QLabel()
        self.cover.setFixedSize(COVER_BIG_W, COVER_BIG_H)
        self.cover.setAlignment(Qt.AlignCenter)
        self.cover.setStyleSheet('background:#17181d; border-radius:10px; color:#7d8491;')
        self.cover.setText('封面加载中…')

        self.title = QLabel()
        self.title.setObjectName('bigTitle')
        self.title.setWordWrap(True)

        self.meta = QLabel()
        self.meta.setObjectName('muted')
        self.meta.setWordWrap(True)
        self.meta.setTextFormat(Qt.RichText)
        self.meta.setOpenExternalLinks(False)

        self.desc = QLabel()
        self.desc.setObjectName('muted')
        self.desc.setWordWrap(True)
        self.desc.setTextFormat(Qt.RichText)

        self.tags = QLabel()
        self.tags.setWordWrap(True)
        self.tags.setTextFormat(Qt.RichText)
        self.tags.setOpenExternalLinks(False)

        self.read_btn = QPushButton('在线阅读')
        self.read_btn.setProperty('cls', 'primary')
        self.resume_btn = QPushButton()
        self.resume_btn.setProperty('cls', 'primary')
        self.resume_btn.setToolTip('回到上次阅读的位置')
        self.resume_btn.hide()
        self.dl_btn = QPushButton('下载本子')
        self.dl_sel_btn = QPushButton('下载所选章节')
        self.dl_sel_btn.setProperty('cls', 'ghost')
        self.dl_sel_btn.hide()
        self.fav_btn = QPushButton()
        self.local_btn = QPushButton('打开本地目录')

        btns = QHBoxLayout()
        btns.addWidget(self.read_btn)
        btns.addWidget(self.resume_btn)
        btns.addWidget(self.dl_btn)
        btns.addWidget(self.dl_sel_btn)
        btns.addWidget(self.fav_btn)
        btns.addWidget(self.local_btn)
        btns.addStretch(1)

        info_col = QVBoxLayout()
        info_col.setSpacing(10)
        info_col.addWidget(self.title)
        info_col.addWidget(self.meta)
        info_col.addWidget(self.desc)
        info_col.addWidget(self.tags)
        info_col.addLayout(btns)
        info_col.addStretch(1)

        header = QHBoxLayout()
        header.setSpacing(22)
        header.addWidget(self.cover, 0, Qt.AlignTop)
        header.addLayout(info_col, 1)
        vbox.addLayout(header)

        # 章节
        self.chap_label = QLabel('章节')
        self.chap_label.setObjectName('panelTitle')
        self.chap_list = QListWidget()
        self.chap_list.setMaximumHeight(240)
        self.chap_hint = QLabel('双击章节可直接从该章节开始阅读')
        self.chap_hint.setObjectName('hint')
        vbox.addWidget(self.chap_label)
        vbox.addWidget(self.chap_list)
        vbox.addWidget(self.chap_hint)
        vbox.addStretch(1)

        scroll.setWidget(inner)
        return scroll

    # -- 加载 --
    def load(self, aid):
        self.aid = str(aid)
        self._info = None
        self._local_dir = ''
        self.content.hide()
        self.state_label.setText('加载中…')
        self.state_label.show()

        def work():
            client = self.service.shared_client()
            info = self.service.get_album(client, aid)
            local = find_album_dir(self.settings['download_dir'], aid)
            return info, local

        run_async(work, on_done=lambda r: self._render(*r),
                  on_fail=lambda e: self._load_error(e))

    def _load_error(self, err):
        self.state_label.setText(f'加载失败：{err}\n\n请检查网络 / 接口设置，或确认编号是否正确。')
        self.state_label.show()
        self.content.hide()

    def _render(self, info, local_dir):
        self._info = info
        self._local_dir = local_dir
        self.state_label.hide()
        self.content.show()

        self.title.setText(info.name)
        self.cover.setText('封面加载中…')
        self.covers.request(info.id, '_3x4')

        authors = _linkify(info.authors, '')
        lines = [f'编号：{info.id}']
        if authors:
            lines.append(f'作者：{authors}')
        ch_count = len(info.chapters)
        lines.append(f'章节：{ch_count}  ·  总页数：{info.page_count}')
        if info.pub_date:
            lines.append(f'发布：{info.pub_date}')
        if info.update_date:
            lines.append(f'更新：{info.update_date}')
        stats = []
        if info.views:
            stats.append(f'观看 {info.views}')
        if info.likes:
            stats.append(f'喜欢 {info.likes}')
        if info.comment_count is not None:
            stats.append(f'评论 {info.comment_count}')
        if stats:
            lines.append('  ·  '.join(stats))
        self.meta.setText('<br>'.join(lines))

        self.desc.setText(info.description if info.description else '')

        self.tags.setText(_linkify(info.tags, '#'))

        if self.store.has(info.id):
            self.fav_btn.setText('★ 已收藏')
        else:
            self.fav_btn.setText('☆ 收藏')

        # 继续阅读：有阅读进度且章节下标仍有效时显示
        p = progress.get(info.id)
        if p and p.get('total', 0) > 0:
            ci = int(p['chapter_index'])
            if 0 <= ci < max(1, len(info.chapters)):
                loc = progress.progress_text(p)  # 如 '第2话 5/30页'
                self.resume_btn.setText(f'▶ 继续阅读（{loc}）')
                self.resume_btn.show()
            else:
                self.resume_btn.hide()
        else:
            self.resume_btn.hide()

        if local_dir:
            self.local_btn.show()
            self.read_btn.setText('在线阅读（本地可用）')
        else:
            self.local_btn.hide()
            self.read_btn.setText('在线阅读')

        # 章节（默认不勾选，按需勾选要下的部分）
        self.chap_list.blockSignals(True)
        self.chap_list.clear()
        for i, ch in enumerate(info.chapters):
            item = QListWidgetItem(f'第 {ch.index} 话  {ch.title}')
            item.setData(Qt.UserRole, i)  # 章节在列表中的行号（序号可能不连续）
            if ch_count > 1:
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                item.setCheckState(Qt.Unchecked)
            self.chap_list.addItem(item)
        self.chap_list.blockSignals(False)
        if ch_count > 1:
            self.chap_label.show()
            self.chap_list.show()
            self.chap_hint.setText('勾选要下载的章节后点“下载所选”；“下载全部”整本下载。双击章节可从此处开始阅读')
            self.chap_hint.show()
            self.dl_btn.setText('下载全部')
            self.dl_sel_btn.show()
            self.chap_list.itemDoubleClicked.connect(self._on_chapter_double)
        else:
            self.chap_label.hide()
            self.chap_list.hide()
            self.chap_hint.hide()
            self.dl_sel_btn.hide()
            self.dl_btn.setText('下载本子')

    def _on_download_selected(self):
        """收集勾选的章节下标下载；未勾选时提示。"""
        if self._info is None:
            return
        checked = [i for i in range(self.chap_list.count())
                   if self.chap_list.item(i).checkState() == Qt.Checked]
        if not checked:
            QMessageBox.information(self, '提示', '请先勾选要下载的章节，或用“下载全部”')
            return
        self.download_requested.emit(self.aid, checked)

    def _on_link(self, query):
        # 标签/作者/作品/人物点击 → 跳转搜索
        if self._info is None:
            return
        if query in self._info.tags:
            self.search_requested.emit('tag', query)
        elif query in self._info.authors:
            self.search_requested.emit('author', query)
        elif query in self._info.works:
            self.search_requested.emit('work', query)
        elif query in self._info.actors:
            self.search_requested.emit('actor', query)

    def _on_resume(self):
        p = progress.get(self.aid or '')
        if p:
            self.read_requested.emit(self.aid, int(p['chapter_index']), int(p['page']))

    def _on_chapter_double(self, item):
        idx = item.data(Qt.UserRole)
        self.read_requested.emit(self.aid, idx, None)

    def _toggle_fav(self):
        if self._info is None:
            return
        if self.store.has(self._info.id):
            self.store.remove(self._info.id)
            self.fav_btn.setText('☆ 收藏')
        else:
            self.store.add(self._info.id, self._info.name)
            self.fav_btn.setText('★ 已收藏')

    def _open_local_dir(self):
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        if self._local_dir:
            QDesktopServices.openUrl(QUrl.fromLocalFile(self._local_dir))

    def _on_cover(self, aid, size, path):
        if aid != self.aid or size != '_3x4':
            return
        if not path:
            self.cover.setText('封面不可用')
            return
        pm = QPixmap(path)
        if pm.isNull():
            self.cover.setText('封面不可用')
            return
        pm = pm.scaled(COVER_BIG_W, COVER_BIG_H, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.cover.setPixmap(pm)
