"""应用设置：JSON 持久化，存放于项目 data 目录。"""
import json
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, 'data')
SETTINGS_PATH = os.path.join(DATA_DIR, 'settings.json')

DEFAULTS = {
    'adult_ack': False,                     # 已确认 18+
    'theme': 'dark',                        # dark / light
    'download_dir': os.path.join(BASE_DIR, '下载'),
    'client_impl': 'api',                   # api=移动端接口(稳定) / html=网页端(支持分类筛选)
    'retry_times': 5,                       # 请求重试次数
    'threads': 4,                           # 图片并发下载线程数
    'proxy': '',                            # HTTP 代理，如 http://127.0.0.1:7890
    'custom_domains': '',                   # 自定义域名（逗号分隔），留空自动获取
    'username': '',                         # 禁漫账号（可选，用于登录）
    'password': '',
    # 缓存上限（MB），0 = 不限制。超限时按"最久未访问"自动淘汰
    'cache_cover_limit_mb': 500,
    'cache_reader_limit_mb': 2048,
    'cache_auto_evict': True,               # 是否自动淘汰超限缓存
}


class Settings:
    def __init__(self):
        self.data = dict(DEFAULTS)
        self.load()

    def load(self):
        try:
            with open(SETTINGS_PATH, 'r', encoding='utf-8') as f:
                stored = json.load(f)
            for k, v in stored.items():
                if k in DEFAULTS:
                    self.data[k] = v
        except (OSError, ValueError):
            pass

    def save(self):
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(SETTINGS_PATH, 'w', encoding='utf-8') as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)

    def __getitem__(self, key):
        return self.data.get(key, DEFAULTS.get(key))

    def __setitem__(self, key, value):
        self.data[key] = value

    # -- 目录约定 --
    def reader_cache_dir(self) -> str:
        return os.path.join(DATA_DIR, 'reader-cache')

    def covers_dir(self) -> str:
        return os.path.join(DATA_DIR, 'covers')

    def log_path(self) -> str:
        return os.path.join(DATA_DIR, 'app.log')


def sanitize_name(name: str, limit: int = 80) -> str:
    for ch in '\\/:*?"<>|\n\r\t':
        name = name.replace(ch, '_')
    name = name.strip().strip('.')
    return name[:limit] or 'untitled'
