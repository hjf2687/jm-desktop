"""阅读进度持久化：data/progress.json。

记录每本漫画读到的章节/页码，用于"继续阅读"与书架进度角标。
结构: {aid: {name, chapter_index, page, total, chapter_title, ts}}
（chapter_index/page 从 0 起；ts 为最后阅读的 Unix 时间戳）
"""
import json
import os
import threading
import time

from .settings import DATA_DIR

PATH = os.path.join(DATA_DIR, 'progress.json')

_lock = threading.Lock()
_cache = None  # 首次访问后缓存全量数据，避免每次写盘前重读


def _load():
    global _cache
    if _cache is None:
        try:
            with open(PATH, 'r', encoding='utf-8') as f:
                _cache = json.load(f)
        except (OSError, ValueError):
            _cache = {}
        if not isinstance(_cache, dict):
            _cache = {}
    return _cache


def get(aid):
    """返回该本的进度 dict，无记录返回 None。"""
    with _lock:
        return _load().get(str(aid))


def save(aid, name, chapter_index, page, total, chapter_title=''):
    """记录/更新阅读进度（内存缓存 + 原子写盘）。"""
    with _lock:
        data = _load()
        data[str(aid)] = {
            'name': name or '',
            'chapter_index': int(chapter_index),
            'page': int(page),
            'total': int(total),
            'chapter_title': chapter_title or '',
            'ts': int(time.time()),
        }
        _flush(data)


def _flush(data):
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        tmp = PATH + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, PATH)  # 原子替换，避免写一半损坏
    except OSError:
        pass


def progress_text(p):
    """把进度 dict 转成书架角标文字，如 '第2话 5/30页' / '5/30页'；无有效进度返回 ''。"""
    if not p:
        return ''
    try:
        total = int(p.get('total', 0))
        if total <= 0:
            return ''
        page = min(int(p.get('page', 0)) + 1, total)
        ci = int(p.get('chapter_index', 0)) + 1
        title = (p.get('chapter_title') or '').strip()
        loc = f'第{ci}话 ' if (title or ci > 1) else ''
        return f'{loc}{page}/{total}页'
    except (TypeError, ValueError):
        return ''
