"""封面加载器：磁盘缓存 + 本地下载目录复用 + 去重 + 后台抓取。"""
import os
import shutil

from PySide6.QtCore import QObject, Signal

from .async_util import run_async


class CoverLoader(QObject):
    loaded = Signal(str, str, str)  # aid, size, 本地路径（失败为 ''）

    def __init__(self, service, settings):
        super().__init__()
        self.service = service
        self.settings = settings
        self.dir = settings.covers_dir()
        os.makedirs(self.dir, exist_ok=True)
        self.inflight = set()

    def _client(self):
        return self.service.shared_client()

    def cache_path(self, aid, size=''):
        return os.path.join(self.dir, f'{aid}{size}.jpg')

    def request(self, aid, size=''):
        aid = str(aid)
        path = self.cache_path(aid, size)
        if os.path.exists(path):
            self.loaded.emit(aid, size, path)
            return
        key = (aid, size)
        if key in self.inflight:
            return
        # 已下载本子：直接复用下载目录里的 cover.jpg（不联网）
        if size == '_3x4':
            from .downloader import find_album_dir
            album_dir = find_album_dir(self.settings['download_dir'], aid)
            local = os.path.join(album_dir, 'cover.jpg') if album_dir else ''
            if local and os.path.exists(local):
                try:
                    shutil.copy2(local, path)
                except OSError:
                    pass
                if os.path.exists(path):
                    self.loaded.emit(aid, size, path)
                    return
        self.inflight.add(key)

        def fetch():
            try:
                self.service.fetch_cover(self._client(), aid, path, size=size)
                return path
            except Exception:
                return ''

        run_async(fetch, on_done=lambda p: self._finish(aid, size, key, p))

    def _finish(self, aid, size, key, path):
        self.inflight.discard(key)
        self.loaded.emit(aid, size, path)
