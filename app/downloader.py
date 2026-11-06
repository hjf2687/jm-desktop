"""下载管理器：后台线程逐章节取详情，线程池并发下载图片；支持取消/断点续传/失败重试。"""
import json
import os
import shutil
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor

from PySide6.QtCore import QObject, Signal

from .settings import sanitize_name


class JobState:
    WAITING = 'waiting'
    FETCH = 'fetch'
    DOWN = 'down'
    DONE = 'done'
    FAILED = 'failed'
    CANCEL = 'cancel'


STATE_CN = {
    JobState.WAITING: '排队中',
    JobState.FETCH: '获取详情',
    JobState.DOWN: '下载中',
    JobState.DONE: '已完成',
    JobState.FAILED: '失败',
    JobState.CANCEL: '已取消',
}


class DownloadJob(QObject):
    updated = Signal()

    def __init__(self, aid, name, service, settings):
        super().__init__()
        self.aid = str(aid)
        self.name = name
        self.service = service
        self.settings = settings
        self.state = JobState.WAITING
        self.error = ''
        self.total = 0
        self.done = 0
        self.failed = 0
        self.cancel = False
        self.save_dir = ''
        self._last_emit = 0.0
        self.lock = threading.Lock()

    @property
    def progress(self):
        if self.total <= 0:
            return 0
        return min(100, int(self.done * 100 / self.total))

    def _notify(self):
        now = time.monotonic()
        with self.lock:
            if self.done < self.total and now - self._last_emit < 0.12:
                return
            self._last_emit = now
        self.updated.emit()


def find_album_dir(download_dir: str, aid) -> str:
    """按编号查找已下载的本子目录（不存在返回 ''）。

    注意不能用 glob：目录名可能含方括号（如 [KawaGawa]），会被 glob 当作字符类。
    """
    aid = str(aid)
    if not os.path.isdir(download_dir):
        return ''
    try:
        names = os.listdir(download_dir)
    except OSError:
        return ''
    for name in names:
        if name == aid or name.startswith(aid + ' '):
            return os.path.join(download_dir, name)
    return ''


def list_images(directory: str):
    """列出目录中的图片文件（按文件名排序）。

    注意不能用 glob：目录名可能含方括号（如 [KawaGawa]），glob 会把它当字符类导致匹配失败。
    """
    if not directory or not os.path.isdir(directory):
        return []
    exts = ('.jpg', '.jpeg', '.webp', '.png', '.gif')
    files = []
    for name in os.listdir(directory):
        if name.lower().endswith(exts) and name.lower() != 'cover.jpg':
            files.append(os.path.join(directory, name))
    files.sort(key=lambda p: os.path.basename(p))
    return files


def scan_local_albums(download_dir: str):
    """扫描下载目录，返回所有已下载本子的信息（按下载时间倒序）。"""
    if not os.path.isdir(download_dir):
        return []
    result = []
    for name in os.listdir(download_dir):
        album_dir = os.path.join(download_dir, name)
        if not os.path.isdir(album_dir):
            continue
        info_path = os.path.join(album_dir, 'info.json')
        if not os.path.exists(info_path):
            continue
        try:
            with open(info_path, 'r', encoding='utf-8') as f:
                info = json.load(f)
        except (OSError, ValueError):
            continue
        chapters = info.get('chapters', [])
        result.append({
            'aid': str(info.get('id', '')),
            'name': info.get('name', name),
            'dir': album_dir,
            'downloaded_at': info.get('downloaded_at', ''),
            'chapter_count': len(chapters),
            'authors': info.get('authors', []),
        })
    result.sort(key=lambda x: x['downloaded_at'], reverse=True)
    return result


def read_local_chapters(album_dir: str):
    """读取本地已下载本子的章节信息: [(pid, index, title, 目录)]"""
    info_path = os.path.join(album_dir, 'info.json')
    try:
        with open(info_path, 'r', encoding='utf-8') as f:
            info = json.load(f)
    except (OSError, ValueError):
        return []
    chapters = info.get('chapters', [])
    multi = len(chapters) > 1
    try:
        subdirs = os.listdir(album_dir)
    except OSError:
        subdirs = []
    result = []
    for ch in chapters:
        pid = str(ch.get('id', ''))
        index = int(ch.get('index', 1))
        title = ch.get('title', '')
        if multi:
            ch_dir = ''
            for name in subdirs:
                if name.startswith(f'第{index:02d}话'):
                    ch_dir = os.path.join(album_dir, name)
                    break
        else:
            ch_dir = album_dir
        result.append((pid, index, title, ch_dir))
    return result


class DownloadManager(QObject):
    updated = Signal(str)  # aid，某个任务进度/状态变化

    def __init__(self, service, settings):
        super().__init__()
        self.service = service
        self.settings = settings
        self.jobs = []
        self._by_aid = {}

    def get(self, aid):
        return self._by_aid.get(str(aid))

    def submit(self, aid, name, album=None, chapter_indices=None) -> DownloadJob:
        """加入下载队列。chapter_indices 为可选章节下标列表（None=整本）。"""
        aid = str(aid)
        existing = self._by_aid.get(aid)
        if existing and existing.state in (JobState.WAITING, JobState.FETCH, JobState.DOWN):
            return existing
        if existing:
            self._remove_job(existing)
        job = DownloadJob(aid, name, self.service, self.settings)
        job._album = album
        job._chapter_indices = chapter_indices
        self.jobs.append(job)
        self._by_aid[aid] = job
        job.updated.connect(lambda a=aid: self.updated.emit(a))
        threading.Thread(target=self._run, args=(job,), daemon=True).start()
        return job

    def cancel(self, aid):
        job = self._by_aid.get(str(aid))
        if job:
            job.cancel = True

    def remove(self, aid):
        job = self._by_aid.get(str(aid))
        if job and job.state in (JobState.DONE, JobState.FAILED, JobState.CANCEL):
            self._remove_job(job)

    def _remove_job(self, job):
        self.jobs.remove(job)
        self._by_aid.pop(job.aid, None)
        job.updated.emit()

    # -- 任务主流程 --
    def _run(self, job):
        job.state = JobState.FETCH
        job._notify()
        try:
            client = self.service.shared_client()
            album = getattr(job, '_album', None)
            if album is None:
                album = client.get_album_detail(job.aid)
            job.name = album.name or job.name
            job.save_dir = os.path.join(self.settings['download_dir'], f'{job.aid} {sanitize_name(job.name)}')
            os.makedirs(job.save_dir, exist_ok=True)

            chapters = album.episode_list  # (pid, index, title[, pub_date])
            indices = getattr(job, '_chapter_indices', None)
            if indices is not None:
                chapters = [chapters[i] for i in indices if 0 <= i < len(chapters)]
            if not chapters:
                job.state = JobState.FAILED
                job.error = '没有要下载的章节'
                return

            self._write_meta(job, album, chapters, client)

            multi = len(chapters) > 1
            tasks = []
            for item in chapters:
                if job.cancel:
                    break
                pid, idx, title = item[0], item[1], item[2]
                photo = client.get_photo_detail(pid, fetch_album=False, fetch_scramble_id=True)
                if not multi:
                    ch_dir = job.save_dir
                else:
                    ch_dir = os.path.join(job.save_dir, f'第{int(idx):02d}话 {sanitize_name(title, 40)}')
                os.makedirs(ch_dir, exist_ok=True)
                for img in photo:
                    tasks.append((img, ch_dir))
            if job.cancel:
                job.state = JobState.CANCEL
                return
            job.total = len(tasks)
            job.state = JobState.DOWN
            job._notify()

            max_workers = max(1, min(8, int(self.settings['threads'])))
            with ThreadPoolExecutor(max_workers=max_workers) as pool:
                for _ in pool.map(lambda t: self._download_one(job, client, t), tasks):
                    pass

            if job.cancel:
                job.state = JobState.CANCEL
            elif job.failed and job.failed >= job.total:
                job.state = JobState.FAILED
                job.error = '图片全部下载失败'
            else:
                job.state = JobState.DONE
        except Exception as e:
            traceback.print_exc()
            job.state = JobState.FAILED
            job.error = str(e)
        finally:
            job._notify()

    def _download_one(self, job, client, item):
        img, ch_dir = item
        if job.cancel:
            return
        path = os.path.join(ch_dir, img.img_file_name + img.img_file_suffix)
        if os.path.exists(path) and os.path.getsize(path) > 0:
            pass
        else:
            # 优先复用阅读缓存（在线阅读过就不重复下载）
            cache_path = os.path.join(
                job.settings.reader_cache_dir(), job.aid, img.aid,
                img.img_file_name + img.img_file_suffix)
            if os.path.exists(cache_path) and os.path.getsize(cache_path) > 0:
                try:
                    shutil.copy2(cache_path, path)
                except OSError:
                    self._fetch_image(job, client, img, path)
            else:
                self._fetch_image(job, client, img, path)
        with job.lock:
            job.done += 1
        job._notify()

    @staticmethod
    def _fetch_image(job, client, img, path):
        try:
            job.service.save_image(client, img, path)
        except Exception:
            try:
                time.sleep(0.8)
                job.service.save_image(client, img, path)
            except Exception:
                with job.lock:
                    job.failed += 1

    def _write_meta(self, job, album, chapters, client):
        info = {
            'id': album.album_id,
            'name': album.name,
            'authors': album.authors,
            'tags': album.tags,
            'chapters': [{'id': c[0], 'index': int(c[1]), 'title': c[2]} for c in chapters],
            'downloaded_at': time.strftime('%Y-%m-%d %H:%M:%S'),
        }
        with open(os.path.join(job.save_dir, 'info.json'), 'w', encoding='utf-8') as f:
            json.dump(info, f, ensure_ascii=False, indent=2)
        cover_dst = os.path.join(job.save_dir, 'cover.jpg')
        cover_src = os.path.join(job.settings.covers_dir(), f'{album.album_id}_3x4.jpg')
        if os.path.exists(cover_src):
            # 封面已在缓存里，直接复制，不再联网
            try:
                shutil.copy2(cover_src, cover_dst)
            except OSError:
                pass
        else:
            try:
                self.service.fetch_cover(client, album.album_id, cover_dst, size='_3x4')
            except Exception:
                pass
