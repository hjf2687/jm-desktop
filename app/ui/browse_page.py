"""主页：分类页。顶部一行分类按钮 + 一行排序按钮，点击立即加载，无需额外确认。

分类浏览统一走网页端接口（service.browse_html），分类项来自 service.CATEGORIES。
3D / Cosplay 不是顶层分类，是"其他类"的副分类，见 service.CATEGORIES 的说明。
"""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QButtonGroup, QHBoxLayout, QLabel, QPushButton,
                               QVBoxLayout, QWidget)

from ..async_util import run_async
from ..service import CATEGORIES
from .widgets import AlbumCard, SearchGrid

THUMB = '_3x4'

# 排序按钮: (显示名, order 值)
ORDERS = [('最新', 'mr'), ('最多点击', 'mv'), ('最多图片', 'mp'),
          ('最多爱心', 'tf')]
_ALL = CATEGORIES[0][1]        # '全部' 的 category 值


class BrowsePage(QWidget):
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

        title = QLabel('分类')
        title.setObjectName('panelTitle')

        # 分类按钮行（与卡片网格左对齐：左边距 18）
        self.cat_group = QButtonGroup(self)
        self.cat_group.setExclusive(True)
        self._cat_buttons = []
        cat_row = QHBoxLayout()
        cat_row.setContentsMargins(18, 0, 18, 0)
        cat_row.setSpacing(6)
        cat_row.addWidget(title)
        cat_row.addSpacing(8)
        for name, cat, sub in CATEGORIES:
            btn = QPushButton(name)
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            self.cat_group.addButton(btn)
            self._cat_buttons.append((btn, cat, sub))
            cat_row.addWidget(btn)
            btn.clicked.connect(lambda checked, c=cat, s=sub: self.do_browse(1, category=c, sub=s))
        cat_row.addStretch(1)
        self._cat_buttons[0][0].setChecked(True)

        # 排序按钮行（与分类按钮对齐）
        self.order_group = QButtonGroup(self)
        self.order_group.setExclusive(True)
        order_row = QHBoxLayout()
        order_row.setContentsMargins(18, 0, 18, 0)
        order_row.setSpacing(6)
        order_row.addSpacing(86)
        for name, value in ORDERS:
            btn = QPushButton(name)
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            self.order_group.addButton(btn)
            order_row.addWidget(btn)
            btn.clicked.connect(lambda checked, v=value: self.do_browse(1, order=v))
        order_row.addStretch(1)
        self.order_group.buttons()[0].setChecked(True)

        self.prev_btn = QPushButton('上一页')
        self.page_label = QLabel('')
        self.page_label.setObjectName('muted')
        self.next_btn = QPushButton('下一页')
        nav_row = QHBoxLayout()
        nav_row.addStretch(1)
        nav_row.addWidget(self.prev_btn)
        nav_row.addWidget(self.page_label)
        nav_row.addWidget(self.next_btn)

        self.grid = SearchGrid()
        self.msg = QLabel('加载中…')
        self.msg.setAlignment(Qt.AlignCenter)
        self.msg.setObjectName('muted')
        self.msg.setWordWrap(True)

        body = QVBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.addSpacing(14)
        body.addLayout(cat_row)
        body.addLayout(order_row)
        body.addWidget(self.grid, 1)
        body.addWidget(self.msg, 1)
        body.addLayout(nav_row)
        self.setLayout(body)

        self.prev_btn.clicked.connect(lambda: self.do_browse(self._page - 1))
        self.next_btn.clicked.connect(lambda: self.do_browse(self._page + 1))
        self.covers.loaded.connect(self._on_cover)
        # 封面懒加载：卡片滚进可视区才去取封面，不再一页 80 张一起请求
        self.grid.set_cover_provider(lambda aid: self.covers.request(aid, THUMB))

        # 默认加载（保持当前选择的分类/排序）
        self.do_browse(1)

    def current_category(self):
        """返回当前选中的 (category, sub_category)。"""
        for btn, cat, sub in self._cat_buttons:
            if btn.isChecked():
                return cat, sub
        return _ALL, None

    def current_order(self):
        for btn in self.order_group.buttons():
            if btn.isChecked():
                for name, value in ORDERS:
                    if btn.text() == name:
                        return value
        return 'mr'

    def do_browse(self, page, category=None, sub=None, order=None):
        if self._busy:
            return
        page = max(1, page)
        if category is None:
            category, sub = self.current_category()
        order = order or self.current_order()
        self._set_busy(True)
        self._show_msg('加载中…')
        self.grid.clear()

        def work():
            return self.service.browse_html(category=category, sub_category=sub,
                                            order=order, page=page)

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
            self._show_msg(f'这个分类暂时没有内容（第 {page} 页）')
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
        self._show_msg(f'加载失败：{err}\n请在“设置”中检查接口/代理，或稍后重试。')
        self._update_nav()

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
        for btn in self.cat_group.buttons():
            btn.setEnabled(not busy)
        for btn in self.order_group.buttons():
            btn.setEnabled(not busy)

    def _update_nav(self):
        # 网页端分类页不给总数（total 恒为 0），此时不显示"共 N 条"
        page_txt = f'第 {self._page} / {self._page_count} 页'
        if self._total:
            page_txt += f' · 共 {self._total} 条'
        self.page_label.setText(page_txt)
        self.prev_btn.setEnabled(self._page > 1 and not self._busy)
        self.next_btn.setEnabled(self._page < self._page_count and not self._busy)
