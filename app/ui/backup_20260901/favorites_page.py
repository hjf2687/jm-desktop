"""收藏页：本地收藏的漫画网格。"""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QMenu, QVBoxLayout, QWidget)

from .widgets import AlbumCard, SearchGrid

THUMB = '_3x4'


class FavoritesPage(QWidget):
    open_album = Signal(str)

    def __init__(self, store, covers, parent=None):
        super().__init__(parent)
        self.store = store
        self.covers = covers

        header = QHBoxLayout()
        header.setContentsMargins(18, 16, 18, 0)
        title = QLabel('我的收藏')
        title.setObjectName('panelTitle')
        hint = QLabel('右键卡片可移除')
        hint.setObjectName('hint')
        header.addWidget(title)
        header.addWidget(hint)
        header.addStretch(1)

        self.grid = SearchGrid()
        self.msg = QLabel('还没有收藏任何漫画')
        self.msg.setAlignment(Qt.AlignCenter)
        self.msg.setObjectName('muted')

        body = QVBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.addLayout(header)
        body.addWidget(self.grid, 1)
        body.addWidget(self.msg, 1)
        self.setLayout(body)

        self.covers.loaded.connect(self._on_cover)

    def refresh(self):
        self.grid.clear()
        items = list(self.store.items)
        if not items:
            self.grid.hide()
            self.msg.setText('还没有收藏任何漫画')
            self.msg.show()
            return
        self.msg.hide()
        self.grid.show()
        for it in items:
            card = AlbumCard(it['id'], it['name'])
            card.clicked.connect(self.open_album.emit)
            card.context_requested.connect(self._remove_menu)
            self.grid.add_card(card)
            self.covers.request(it['id'], THUMB)

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh()

    def _on_cover(self, aid, size, path):
        if size != THUMB:
            return
        for card in self.grid.cards():
            if card.aid == aid:
                card.set_cover(path)
                return

    def _remove_menu(self, aid):
        menu = QMenu(self)
        act = menu.addAction('移除收藏')
        chosen = menu.exec(self.mapToGlobal(self.grid.pos()))
        if chosen == act:
            self.store.remove(aid)
            self.refresh()
