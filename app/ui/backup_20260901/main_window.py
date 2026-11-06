"""主窗口：侧边栏导航 + 各页面组装。"""
import json
import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QApplication, QButtonGroup, QHBoxLayout, QLabel,
                               QMainWindow, QPushButton, QStackedWidget,
                               QVBoxLayout, QWidget)

from ..async_util import run_async
from ..covers import CoverLoader
from ..downloader import DownloadManager, find_album_dir, read_local_chapters
from ..service import AlbumInfo, JmService
from ..settings import DATA_DIR, Settings
from ..store import FavoritesStore
from ..theme import apply_theme
from .album_page import AlbumPage
from .browse_page import BrowsePage
from .downloads_page import DownloadsPage
from .favorites_page import FavoritesPage
from .reader import ReaderView
from .search_page import SearchPage
from .settings_page import SettingsPage

NAV = [('主页', 'home'), ('搜索', 'search'), ('收藏', 'favorites'),
       ('下载', 'downloads'), ('设置', 'settings')]


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('JM 桌面端')
        self.resize(1280, 820)
        self.setMinimumSize(1100, 720)

        self.settings = Settings()
        self.store = FavoritesStore(os.path.join(DATA_DIR, 'favorites.json'))
        self.service = JmService(self.settings)
        self.covers = CoverLoader(self.service, self.settings)
        self.manager = DownloadManager(self.service, self.settings)
        self.reader = ReaderView(self.service, self.settings)

        # 侧边栏
        self.side = QWidget()
        self.side.setObjectName('sidebar')
        self.side.setFixedWidth(158)
        side_lay = QVBoxLayout(self.side)
        side_lay.setContentsMargins(12, 16, 12, 12)
        side_lay.setSpacing(4)

        title = QLabel('JM 桌面端')
        title.setObjectName('sideTitle')
        sub = QLabel('在线漫画 · 下载')
        sub.setObjectName('sideSub')
        side_lay.addWidget(title)
        side_lay.addWidget(sub)
        side_lay.addSpacing(10)

        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)
        self._nav_buttons = {}
        for name, key in NAV:
            btn = QPushButton(name)
            btn.setProperty('cls', 'nav')
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            self.nav_group.addButton(btn)
            self._nav_buttons[key] = btn
            side_lay.addWidget(btn)
            btn.clicked.connect(lambda checked, k=key: self.switch_page(k))
        side_lay.addStretch(1)
        version = QLabel('基于 jmcomic 开源库')
        version.setObjectName('hint')
        side_lay.addWidget(version)

        # 页面
        self.stack = QStackedWidget()
        self.browse_page = BrowsePage(self.service, self.settings, self.covers)
        self.search_page = SearchPage(self.service, self.settings, self.covers)
        self.favorites_page = FavoritesPage(self.store, self.covers)
        self.downloads_page = DownloadsPage(self.manager, self.settings, self.covers)
        self.settings_page = SettingsPage(self.settings)
        self.album_page = AlbumPage(self.service, self.settings, self.covers, self.store)
        for p in (self.browse_page, self.search_page, self.favorites_page,
                  self.downloads_page, self.settings_page, self.album_page,
                  self.reader):
            self.stack.addWidget(p)

        central = QWidget()
        lay = QHBoxLayout(central)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self.side)
        lay.addWidget(self.stack, 1)
        self.setCentralWidget(central)

        self._last_nav = 'home'
        self._nav_buttons['home'].setChecked(True)

        # 事件接线
        self.browse_page.open_album.connect(self.open_album)
        self.search_page.open_album.connect(self.open_album)
        self.favorites_page.open_album.connect(self.open_album)
        self.album_page.back.connect(self._album_back)
        self.album_page.read_requested.connect(self._on_read)
        self.album_page.download_requested.connect(self._on_download)
        self.album_page.search_requested.connect(self._on_search_redirect)
        self.reader.back_requested.connect(self._album_back)
        self.downloads_page.open_local.connect(self._on_open_local)
        self.downloads_page.export_done.connect(self.status)
        self.settings_page.saved.connect(self._on_settings_saved)
        self.settings_page.toast.connect(self.status)

        # 启动即后台预热共享客户端，后续点击无需等待握手
        run_async(lambda: self.service.shared_client(), None, None)

    # -- 导航 --
    def switch_page(self, key):
        pages = {
            'home': self.browse_page,
            'search': self.search_page,
            'favorites': self.favorites_page,
            'downloads': self.downloads_page,
            'settings': self.settings_page,
        }
        page = pages.get(key)
        if page is None:
            return
        if key in pages:
            self._last_nav = key
        self.stack.setCurrentWidget(page)
        if key == 'favorites':
            self.favorites_page.refresh()
        elif key == 'downloads':
            self.downloads_page.refresh_local()
        elif key == 'home':
            if not self.browse_page.grid.cards():
                self.browse_page.do_browse(1)

    def _album_back(self):
        btn = self._nav_buttons.get(self._last_nav)
        if btn:
            btn.setChecked(True)
        self.switch_page(self._last_nav)

    def open_album(self, aid):
        self.album_page.load(aid)
        self.stack.setCurrentWidget(self.album_page)

    # -- 详情页事件 --
    def _on_read(self, aid, chapter_index):
        info = self.album_page._info
        if info is None:
            return
        local = self.album_page._local_dir
        local_chapters = read_local_chapters(local) if local else None
        self.open_reader(info, chapter_index, local_chapters)

    def open_reader(self, info, chapter_index=0, local_chapters=None):
        self.reader.show_album(info, chapter_index, local_chapters)
        self.stack.setCurrentWidget(self.reader)

    def _on_download(self, aid, indices=None):
        self.manager.submit(aid, '', chapter_indices=indices)
        self.status(f'已加入下载队列：{aid}  →  [下载] 页查看进度')

    def _on_search_redirect(self, mode, query):
        self.switch_page('search')
        self.search_page.set_search(mode, query)

    def _on_open_local(self, data):
        # data 是书架扫描的简版信息，需重新读完整 info.json 获取章节列表
        meta_path = os.path.join(data['dir'], 'info.json')
        try:
            with open(meta_path, 'r', encoding='utf-8') as f:
                meta = json.load(f)
        except (OSError, ValueError):
            self.status(f'无法读取本地元数据：{meta_path}')
            return
        info = AlbumInfo.from_local(meta)
        chapters = read_local_chapters(data['dir'])
        self.open_reader(info, 0, chapters)

    def _on_settings_saved(self):
        self.service.reset_client()
        apply_theme(QApplication.instance(), self.settings['theme'] == 'light')
        self.status('设置已保存：主题已即时切换，其余配置作用于新任务')

    def status(self, text, timeout=5000):
        self.statusBar().showMessage(text, timeout)

    def closeEvent(self, event):
        self.settings.save()
        super().closeEvent(event)
