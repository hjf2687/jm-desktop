"""搜索页：关键词/标签/作者/作品搜索，分类+排序+分页，结果网格。"""
import re

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QComboBox, QHBoxLayout, QLabel, QLineEdit,
                               QPushButton, QVBoxLayout, QWidget)

from ..async_util import run_async
from ..service import ORDERS, SEARCH_MODES
from .widgets import AlbumCard, SearchGrid

THUMB = '_3x4'


class SearchPage(QWidget):
    open_album = Signal(str)

    def __init__(self, service, settings, covers, parent=None):
        super().__init__(parent)
        self.service = service
        self.settings = settings
        self.covers = covers
        self._page = 1
        self._total = 0
        self._page_count = 1
        self._busy = False

        # 顶部控件
        self.input = QLineEdit()
        self.input.setPlaceholderText('输入关键词 / 标签 / 作者，或直接输入漫画编号（回车搜索）')
        self.input.setMinimumWidth(280)

        self.mode_combo = QComboBox()
        for name, _ in SEARCH_MODES:
            self.mode_combo.addItem(name)

        self.order_combo = QComboBox()
        for name, _ in ORDERS:
            self.order_combo.addItem(name)

        self.search_btn = QPushButton('搜索')
        self.search_btn.setProperty('cls', 'primary')

        self.prev_btn = QPushButton('上一页')
        self.page_label = QLabel('')
        self.page_label.setObjectName('muted')
        self.next_btn = QPushButton('下一页')

        top = QHBoxLayout()
        top.setContentsMargins(18, 16, 18, 0)
        top.addWidget(self.input, 1)
        top.addWidget(self.mode_combo)
        top.addWidget(self.order_combo)
        top.addWidget(self.search_btn)
        top.addSpacing(24)
        top.addWidget(self.prev_btn)
        top.addWidget(self.page_label)
        top.addWidget(self.next_btn)

        # 结果区
        self.grid = SearchGrid()
        self.msg = QLabel('输入关键词开始搜索')
        self.msg.setAlignment(Qt.AlignCenter)
        self.msg.setObjectName('muted')
        self.msg.setWordWrap(True)

        body = QVBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.addLayout(top)
        body.addWidget(self.grid, 1)
        body.addWidget(self.msg, 1)

        self.setLayout(body)
        self._show_msg('输入关键词开始搜索')

        # 事件
        self.input.returnPressed.connect(lambda: self.do_search(1))
        self.search_btn.clicked.connect(lambda: self.do_search(1))
        self.prev_btn.clicked.connect(lambda: self.do_search(self._page - 1))
        self.next_btn.clicked.connect(lambda: self.do_search(self._page + 1))
        self.covers.loaded.connect(self._on_cover)
        # 封面懒加载：卡片滚进可视区才去取封面，不再一页几十张一起请求
        self.grid.set_cover_provider(lambda aid: self.covers.request(aid, THUMB))

    # -- 对外 --
    def set_search(self, mode, query, order=None, category=None):
        """供详情页标签/作者点击等跳转调用。"""
        for i, (name, m) in enumerate(SEARCH_MODES):
            if m == mode:
                self.mode_combo.setCurrentIndex(i)
        if order:
            for i, (name, o) in enumerate(ORDERS):
                if o == order:
                    self.order_combo.setCurrentIndex(i)
        if category:
            for i, (name, c) in enumerate(CATEGORIES):
                if c == category:
                    self.category_combo.setCurrentIndex(i)
        self.input.setText(query)
        self.do_search(1)

    def invalidate_client(self):
        pass  # 客户端由 service 统一管理（shared_client / reset_client）

    def _ensure_client(self):
        return self.service.shared_client()

    # -- 搜索 --
    def do_search(self, page):
        if self._busy:
            return
        page = max(1, page)
        query = self.input.text().strip()
        mode = SEARCH_MODES[self.mode_combo.currentIndex()][1]
        if not query and mode == 'site':
            self._show_msg('请输入关键词')
            return
        # 编号直达
        if re.fullmatch(r'\d{4,10}', query):
            self.open_album.emit(query)
            return

        category = '0'
        order = ORDERS[self.order_combo.currentIndex()][1]
        self._set_busy(True)
        self._show_msg('搜索中…')
        self.grid.clear()

        def work():
            return self.service.search(self._ensure_client(), query, mode=mode,
                                       page=page, order=order, category=category)

        run_async(work, on_done=lambda r: self._on_result(r, page),
                  on_fail=lambda e: self._on_error(e))

    def _on_result(self, result, page):
        items, total, page_count = result
        self._set_busy(False)
        self._page = page
        self._total = total
        self._page_count = max(1, page_count)
        self.grid.clear()
        if not items:
            self._show_msg(f'没有找到相关结果（第 {page} 页）')
            self._update_nav()
            return
        for it in items:
            card = AlbumCard(it.id, it.title)
            card.clicked.connect(self.open_album.emit)
            self.grid.add_card(card)   # 封面由网格按可视区自行请求
        self._hide_msg()
        self._update_nav()

    def _on_error(self, err):
        self._set_busy(False)
        self._show_msg(f'搜索失败：{err}\n请在“设置”中检查接口/代理，或稍后重试。')
        self._update_nav()

    # -- 辅助 --
    def _on_cover(self, aid, size, path):
        if size != THUMB:
            return
        for card in self.grid.cards():
            if card.aid == aid:
                card.set_cover(path)
                return

    def _show_msg(self, text):
        self.msg.setText(text)
        self.msg.show()
        self.grid.hide()

    def _hide_msg(self):
        self.msg.hide()
        self.grid.show()

    def _set_busy(self, busy):
        self._busy = busy
        self.search_btn.setEnabled(not busy)
        self.input.setEnabled(not busy)

    def _update_nav(self):
        self.page_label.setText(f'第 {self._page} / {self._page_count} 页 · 共 {self._total} 条')
        self.prev_btn.setEnabled(self._page > 1 and not self._busy)
        self.next_btn.setEnabled(self._page < self._page_count and not self._busy)
