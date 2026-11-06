"""磁盘缓存管理：占用统计 + 超限自动淘汰（LRU）。

策略对齐主流实现（浏览器磁盘缓存 / Mihon 的 DiskLruCache）：

- **只动缓存，不碰用户数据**：仅操作 covers/ 与 reader-cache/。
  下载目录、收藏、阅读进度、设置永不触碰。
- **容量上限 + LRU**：超限时按"最后访问时间"淘汰最久未使用的条目。
- **粒度对齐使用单元**：封面逐张删（互相独立，删一张不影响其他）；
  阅读缓存整本删（删半本会导致下次阅读前几页命中、后几页重下，体验割裂）。
- **上限不严格**：删到水位线即停（不追求卡准数字）；所有磁盘操作都是阻塞的，
  调用方负责丢到后台线程，不要在 UI 线程调用。

最后访问时间取 max(atime, mtime)：本机 NTFS 上 atime 实测可靠，
但外置盘/网络盘可能不更新 atime，取 max 兜底。
"""
import os
import shutil
import time

WATERMARK = 0.85        # 淘汰后降到上限的 85%，避免刚删完又立刻触发
MIN_KEEP_SEC = 300      # 最近 5 分钟内动过的缓存不淘汰（防正在访问的被删）


def dir_usage(path):
    """返回 (文件数, 字节数)。目录不存在返回 (0, 0)。"""
    n = size = 0
    if not os.path.isdir(path):
        return 0, 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                st = os.stat(os.path.join(root, f))
            except OSError:
                continue
            n += 1
            size += st.st_size
    return n, size


def fmt_size(n):
    n = float(n)
    for unit in ('B', 'KB', 'MB', 'GB'):
        if n < 1024 or unit == 'GB':
            return ('%.0f %s' % (n, unit)) if unit == 'B' else ('%.1f %s' % (n, unit))
        n /= 1024.0


def _last_used(st):
    return max(st.st_atime, st.st_mtime)


def _is_under(root, path):
    """确认 path 确实在 root 之下（防止意外删到缓存目录外的文件）。"""
    try:
        root = os.path.realpath(root)
        path = os.path.realpath(path)
        if path == root:
            return False
        return os.path.commonpath([root, path]) == root
    except (OSError, ValueError):
        return False


def _remove_path(root, path):
    """删除 root 之下的一个文件或子目录，返回释放的字节数。"""
    if not _is_under(root, path):
        return 0
    if os.path.isdir(path):
        _n, size = dir_usage(path)
        try:
            shutil.rmtree(path)
        except OSError:
            return 0
        return size
    try:
        size = os.path.getsize(path)
        os.remove(path)
    except OSError:
        return 0
    return size


class CacheManager:
    """封面缓存与阅读缓存的统计、淘汰、清理。

    protect_fn: 可选回调，返回"当前不该被淘汰"的 aid 集合
    （正在阅读的本子、正在下载的本子）。
    """

    def __init__(self, settings, protect_fn=None):
        self.settings = settings
        self._protect_fn = protect_fn

    # -- 路径 --

    @property
    def covers_dir(self):
        return self.settings.covers_dir()

    @property
    def reader_dir(self):
        return self.settings.reader_cache_dir()

    # -- 上限（字节；<=0 表示不限制） --

    @property
    def cover_limit(self):
        return max(0, int(self.settings['cache_cover_limit_mb'] or 0)) * 1024 * 1024

    @property
    def reader_limit(self):
        return max(0, int(self.settings['cache_reader_limit_mb'] or 0)) * 1024 * 1024

    def _protected(self):
        if self._protect_fn is None:
            return set()
        try:
            return {str(a) for a in self._protect_fn()}
        except Exception:
            return set()

    # -- 统计 --

    def stats(self):
        cn, cb = dir_usage(self.covers_dir)
        rn, rb = dir_usage(self.reader_dir)
        return {
            'covers': {'files': cn, 'bytes': cb, 'limit': self.cover_limit},
            'reader': {'files': rn, 'bytes': rb, 'limit': self.reader_limit},
        }

    # -- 自动淘汰 --

    def sweep(self):
        """按上限淘汰两个缓存。返回 {'covers': (删除数, 释放字节), ...}。"""
        return {'covers': self._sweep_covers(), 'reader': self._sweep_reader()}

    def _sweep_covers(self):
        limit = self.cover_limit
        d = self.covers_dir
        if limit <= 0 or not os.path.isdir(d):
            return 0, 0
        now = time.time()
        entries = []
        total = 0
        with os.scandir(d) as it:
            for e in it:
                try:
                    if not e.is_file():
                        continue
                    st = e.stat()
                except OSError:
                    continue
                total += st.st_size
                entries.append((_last_used(st), st.st_size, e.path))
        if total <= limit:
            return 0, 0
        target = int(limit * WATERMARK)
        entries.sort(key=lambda x: x[0])        # 最久未用的排前面
        freed = n = 0
        for ts, size, path in entries:
            if total - freed <= target:
                break
            if now - ts < MIN_KEEP_SEC:         # 刚写入的保护
                continue
            got = _remove_path(d, path)
            if got:
                freed += got
                n += 1
        return n, freed

    def _sweep_reader(self):
        limit = self.reader_limit
        d = self.reader_dir
        if limit <= 0 or not os.path.isdir(d):
            return 0, 0
        protect = self._protected()
        now = time.time()
        albums = []
        total = 0
        with os.scandir(d) as it:
            for e in it:
                try:
                    if not e.is_dir():
                        continue
                except OSError:
                    continue
                size = 0
                last = 0.0
                for root, _dirs, files in os.walk(e.path):
                    for f in files:
                        try:
                            st = os.stat(os.path.join(root, f))
                        except OSError:
                            continue
                        size += st.st_size
                        last = max(last, _last_used(st))
                total += size
                albums.append((last, size, e.name, e.path))
        if total <= limit:
            return 0, 0
        target = int(limit * WATERMARK)
        albums.sort(key=lambda x: x[0])         # 最久没读的整本排前面
        freed = n = 0
        for last, size, aid, path in albums:
            if total - freed <= target:
                break
            if aid in protect:                  # 正在阅读 / 下载中的本子跳过
                continue
            if now - last < MIN_KEEP_SEC:
                continue
            got = _remove_path(d, path)
            if got:
                freed += got
                n += 1
        return n, freed

    # -- 手动清理 --

    def clear_covers(self):
        return self._clear(self.covers_dir, protect=None)

    def clear_reader(self):
        # 保留正在阅读的那一本，避免读到一半缓存被清掉
        return self._clear(self.reader_dir, protect=self._protected())

    def _clear(self, d, protect=None):
        if not os.path.isdir(d):
            return 0, 0
        protect = protect or set()
        freed = n = 0
        with os.scandir(d) as it:
            entries = list(it)
        for e in entries:
            if e.name in protect:
                continue
            freed += _remove_path(d, e.path)
            if not os.path.exists(e.path):      # 删掉了才计数（含 0 字节/空目录）
                n += 1
        return n, freed
