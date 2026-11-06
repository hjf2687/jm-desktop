"""封装 jmcomic 库：搜索、详情、取图、下载。所有方法均为同步阻塞，由调用方放到后台线程。"""
import re
import threading
import time
from urllib.parse import parse_qsl, urlencode, urlsplit

from jmcomic import JmcomicText, JmMagicConstants, JmModuleConfig, JmOption, JmPageTool

# 网页端分类页的分页块，用来读真实总页数（jmcomic 的解析函数不处理它）
_PAGINATION_RE = re.compile(r'<ul class="pagination">(.*?)</ul>', re.S)
_PAGE_NUM_RE = re.compile(r'[?&]page=(\d+)')


def _parse_page_count(html):
    """从分页块里取最大页码当作总页数。

    `<ul class="pagination">` 会列出开头几页和末页（实测 1,2,3,4,5,125），
    最大值就是总页数。解析不出来返回 0，由调用方回退。
    """
    nums = [int(n) for block in _PAGINATION_RE.findall(html)
            for n in _PAGE_NUM_RE.findall(block)]
    return max(nums) if nums else 0


# -- 请求缓存 --
# 只缓存参数稳定、内容短期内不变的几个接口，且跳过带 v= 时间戳的 URL
# （那些每次都不同，缓存了也复读不到，只会把缓存冲掉）。
RESP_CACHE_PATHS = frozenset(('/album', '/chapter', '/categories/filter', '/search'))
RESP_CACHE_TTL = 60.0     # 秒：同一 URL 在这个窗口内只真正请求一次
RESP_CACHE_MAX = 120      # 条：上限，防止长时间运行后占内存


def _cache_key(url, params):
    """把 URL 和 params 归一成同一个 key。

    同一个接口在 jmcomic 里有两种写法：
      append_params_to_url  -> '/album?id=123'
      req_api('/album', params={'id': '123'})
    不归一的话两者算成两个 key，缓存就白加了。
    """
    split = urlsplit(url)
    pairs = parse_qsl(split.query, keep_blank_values=True)
    if params:
        pairs += list(params.items())
    if 'v' in dict(pairs):          # 带时间戳的 URL 每次都不一样，不缓存
        return None
    if split.path not in RESP_CACHE_PATHS:
        return None
    return split.path + '?' + urlencode(sorted(pairs))


class _RespCache:
    """包在 client.req_api 外面的一层短时响应缓存。

    主要收益：jmcomic 的 get_album_detail 内部已经请求过 /album?id=，
    而 service._fill_api_fields 又拿同一个接口去补 page_count/pub_date，
    以前会白跑一次完整往返（实测约 0.8~1s）。现在第二次直接命中缓存。

    只包 GET、且 kwargs 只含 params 的请求；POST（登录等）一律透传。
    """

    def __init__(self, req_fn, ttl=RESP_CACHE_TTL, maxsize=RESP_CACHE_MAX):
        self._req = req_fn
        self._ttl = ttl
        self._max = maxsize
        self._store = {}
        self._lock = threading.Lock()

    def __call__(self, url, get=True, require_success=True, **kwargs):
        key = None
        if get and set(kwargs) <= {'params'}:
            key = _cache_key(url, kwargs.get('params'))
        if key is None:
            return self._req(url, get=get, require_success=require_success, **kwargs)

        now = time.monotonic()
        with self._lock:
            hit = self._store.get(key)
            if hit is not None and hit[0] > now:
                return hit[1]

        resp = self._req(url, get=get, require_success=require_success)

        with self._lock:
            if len(self._store) >= self._max:
                # 先清过期条目；还是满就直接清空（简单策略，避免无限增长）
                self._store = {k: v for k, v in self._store.items() if v[0] > now}
                if len(self._store) >= self._max:
                    self._store.clear()
            self._store[key] = (now + self._ttl, resp)
        return resp

# 搜索类型: (中文名, 方法后缀)
SEARCH_MODES = [('关键词', 'site'), ('标签', 'tag'), ('作者', 'author'),
                ('作品', 'work'), ('人物', 'actor')]
# 分类（主页分类按钮用）：(显示名, category, sub_category)
# 只有网页端能完整区分这些分类，见 browse_html。
# 注意 3D / Cosplay 不是顶层分类：站点分类导航只有 同人/单本/短篇/韩漫/美漫/其他，
# 它俩是"其他类"的副分类（jm_config.SUB_ANOTHER_3D / SUB_ANOTHER_COSPLAY），
# 路径为 /albums/another/sub/{3d,cosplay}。传成顶层 /albums/3D 会被站点回退成首页列表
# （实测：返回内容与"全部"完全一致，这就是之前"3D/Cosplay 点进去还是全部"的原因）。
CATEGORIES = [
    ('全部', JmMagicConstants.CATEGORY_ALL, None),
    ('同人志', 'doujin', None), ('单本', 'single', None), ('短篇', 'short', None),
    ('韩漫', 'hanman', None), ('美漫', 'meiman', None),
    ('3D', 'another', '3d'),
    ('Cosplay', 'another', 'cosplay'),
    ('其他', 'another', None),
]
# 排序
ORDERS = [('最新', 'mr'), ('最多点击', 'mv'), ('最多图片', 'mp'),
          ('最多喜欢', 'tf'), ('最高评分', 'tr'), ('最多评论', 'md')]


class SearchItem:
    __slots__ = ('id', 'title', 'tags')

    def __init__(self, aid, title, tags):
        self.id = str(aid)
        self.title = title
        self.tags = tags or []


class ChapterInfo:
    __slots__ = ('pid', 'index', 'title')

    def __init__(self, pid, index, title):
        self.pid = str(pid)
        self.index = int(index)
        self.title = title


class AlbumInfo:
    def __init__(self, album):
        self.id = album.album_id
        self.name = album.name
        self.description = getattr(album, 'description', '') or ''
        self.authors = album.authors
        self.tags = album.tags
        self.works = album.works
        self.actors = album.actors
        self.page_count = album.page_count
        self.pub_date = getattr(album, 'pub_date', '') or ''
        self.update_date = getattr(album, 'update_date', '') or ''
        self.likes = album.likes
        self.views = album.views
        self.comment_count = album.comment_count
        self.chapters = [ChapterInfo(c[0], c[1], c[2]) for c in album.episode_list]

    @classmethod
    def from_local(cls, data) -> 'AlbumInfo':
        """从本地 info.json 构造（用于离线阅读已下载的本子）。"""
        info = cls.__new__(cls)
        info.id = str(data.get('id', ''))
        info.name = data.get('name', '')
        info.description = ''
        info.authors = data.get('authors', []) or []
        info.tags = data.get('tags', []) or []
        info.works = []
        info.actors = []
        info.page_count = 0
        info.pub_date = ''
        info.update_date = ''
        info.likes = ''
        info.views = ''
        info.comment_count = None
        info.chapters = [ChapterInfo(ch.get('id'), ch.get('index', 1), ch.get('title', ''))
                         for ch in data.get('chapters', [])]
        return info


class JmService:
    def __init__(self, settings):
        self.settings = settings
        self._option = None
        self._shared = None
        self._html_client = None
        self._client_lock = threading.RLock()
        self._tl_client = threading.local()

    # -- 配置 --
    def _build_option(self) -> JmOption:
        """构建并缓存 option。

        用双检锁：启动时"共享客户端预热"和主页分类加载会同时走到这里，
        不加锁会各构造一份（后写覆盖先写，白做一次构造）。
        _client_lock 是可重入锁，shared_client() 已持锁时再调进来不会死锁。

        代理只能通过进程级全局项 JmModuleConfig.DEFAULT_PROXIES 设置，绝不能写进
        conf 的 postman.meta_data：jmcomic 的 merge_default_dict 遇到"两边都是 dict"
        会递归**就地**合并，而 option_default_dict() 是把 DEFAULT_PROXIES 这个对象
        按引用放进默认配置的 —— 于是填一次代理就把全局对象改掉了，之后所有新建的
        option 都带着它，清空设置也救不回来（表现：必须重启程序）。
        走全局项则每次重建都会覆盖成当前设置，填错再清空即可立即恢复。
        （顺带：往 meta_data 里传 proxies 本身也没用 —— JmModuleConfig.new_postman
        是 kwargs.setdefault('proxies', DEFAULT_PROXIES)，只有全局项说了算。）

        "留空＝直连"必须写成空字符串。libcurl 在**没有显式设置** CURLOPT_PROXY 时
        会自己去读 http_proxy/https_proxy 环境变量，所以 {} 和 {'http': None} 都挡不住
        代理（实测：设了坏的环境变量代理后这两种写法照样失败），只有空字符串是
        libcurl 语义里的"显式禁用代理"。
        """
        if self._option is not None:
            return self._option
        with self._client_lock:
            if self._option is not None:
                return self._option
            s = self.settings
            proxy = (s['proxy'] or '').strip()
            # 留空＝真直连（空字符串，不能用 {} / None，见 docstring）
            JmModuleConfig.DEFAULT_PROXIES = (
                {'http': proxy, 'https': proxy} if proxy else {'http': '', 'https': ''})
            conf = {
                'download': {
                    'cache': True,
                    'image': {'suffix': None, 'suffix_keep': True, 'unzip': False},
                },
                'client': {
                    'impl': s['client_impl'],
                    'retry_times': int(s['retry_times']),
                },
            }
            self._option = JmOption.construct(conf)
            return self._option

    def custom_domains(self):
        text = (self.settings['custom_domains'] or '').strip()
        if not text:
            return None
        return [d.strip() for d in text.split(',') if d.strip()]

    def new_client(self):
        domains = self.custom_domains()
        client = self._build_option().new_jm_client(domain_list=domains)
        user, pwd = (self.settings['username'] or '').strip(), self.settings['password']
        if user and pwd:
            try:
                client.login(user, pwd)
            except Exception:
                pass
        self._install_cache(client)
        return client

    @staticmethod
    def _install_cache(client):
        """给 api 客户端装上请求缓存；html 客户端没有 req_api，自动跳过。

        直接挂到实例上覆盖类方法 —— jmcomic 内部都是 self.req_api(...) 调用，
        实例属性优先生效，所以能拦到。
        """
        req = getattr(client, 'req_api', None)
        if req is None or isinstance(req, _RespCache):
            return
        try:
            client.req_api = _RespCache(req)
        except AttributeError:
            pass  # 万一某个客户端实现定义了 __slots__，退化为不缓存

    def shared_client(self):
        """全局共享客户端。

        创建客户端本身要发多个网络请求（更新域名、setting、登录），
        逐次新建会让每次点击都卡几秒。jmcomic 官方下载器就是
        单 client 多线程使用的，这里同样全局复用。
        """
        with self._client_lock:
            if self._shared is None:
                self._shared = self.new_client()
            return self._shared

    def reset_client(self):
        """设置变更后丢弃共享客户端与缓存的 option，下次用新配置重建。

        只清 _shared 是不够的：代理 / 接口类型 / 重试次数都写在 _option 里，
        而 _build_option() 会直接返回已缓存的旧 option，
        于是重建出来的客户端仍是旧配置 —— 表现就是"改了设置不生效"。
        _html_client（分类浏览用）同样是按旧配置建的，一并作废。
        """
        with self._client_lock:
            self._shared = None
            self._option = None
            self._html_client = None

    # -- 常用操作 --
    @staticmethod
    def parse_id(text: str) -> str:
        try:
            return JmcomicText.parse_to_jm_id(text)
        except Exception as e:
            raise ValueError(f'无法识别漫画编号: {text}') from e

    def search(self, client, query, mode='site', page=1, order='mr', category='0'):
        """返回 (items, total, page_count)"""
        method = getattr(client, f'search_{mode}')
        page_obj = method(query, page=page, order_by=order, time=JmMagicConstants.TIME_ALL, category=category)
        items = [SearchItem(aid, info.get('name', ''), info.get('tags', []))
                 for aid, info in page_obj.content]
        return items, page_obj.total, page_obj.page_count

    def get_album(self, client, aid) -> AlbumInfo:
        album = client.get_album_detail(aid)
        info = AlbumInfo(album)
        self._fill_api_fields(client, aid, info)
        return info

    def _fill_api_fields(self, client, aid, info):
        """api 客户端适配后 page_count/pub_date/update_date 被 jmcomic 置为 '0'，
        用原始接口数据补齐这些字段（html 客户端字段正常，跳过）。

        这里的 URL 和 get_album_detail 内部请求的完全一样，会命中 _RespCache，
        不再产生第二次网络往返。
        """
        req = getattr(client, 'req_api', None)
        if req is None:
            return
        try:
            data = req('/album', params={'id': str(aid)}).res_data
        except Exception:
            return
        if data.get('total_photos') and not info.page_count:
            try:
                info.page_count = int(data['total_photos'])
            except (TypeError, ValueError):
                pass
        addtime = data.get('addtime')
        if addtime:
            try:
                from datetime import datetime
                day = datetime.fromtimestamp(int(addtime)).strftime('%Y-%m-%d')
                info.pub_date = day
                info.update_date = day
            except (TypeError, ValueError):
                pass
        if data.get('total_views'):
            info.views = data['total_views']
        if data.get('comment_total') is not None:
            try:
                info.comment_count = int(data['comment_total'])
            except (TypeError, ValueError):
                pass
        if data.get('author') and not info.authors:
            info.authors = [data['author']]

    def browse_html(self, category='0', sub_category=None, order='mr', page=1):
        """分类浏览：必须走网页端（html）客户端。

        移动端 api 的 categories/filter 不区分分类（实测 doujin_cosplay 与 doujin
        返回同一批、'3D' 与'其他'也串），网页端按
        /albums[/{category}[/sub/{sub_category}]] 取页才准。
        客户端懒创建后复用（分类页不含登录态，复用是安全的）。

        没有直接用 client.categories_filter()：它返回的 page_count 由
        parse_html_to_category_page 解出的 total 推出来，而网页端分类页根本不显示总数
        （total 恒为 0）→ 总页数 0 → 分页按钮全灰。这里自己取一次 html，顺手把分页块
        里的最大页码读出来当总页数。
        """
        client = self._html_client
        if client is None:
            client = self._build_option().new_jm_client(impl='html')
            self._html_client = client
        url = client.build_search_url(client.API_CATEGORY, category, sub_category)
        resp = client.get_jm_html(url, params={'page': page, 'o': order,
                                               't': JmMagicConstants.TIME_ALL},
                                  allow_redirects=True)
        page_obj = JmPageTool.parse_html_to_category_page(resp.text)
        items = [SearchItem(aid, info.get('name', ''), info.get('tags', []))
                 for aid, info in page_obj.content]
        return items, page_obj.total, _parse_page_count(resp.text) or page_obj.page_count

    def get_photo(self, client, pid, fetch_scramble=True):
        return client.get_photo_detail(pid, fetch_album=False, fetch_scramble_id=fetch_scramble)

    def fetch_cover(self, client, aid, save_path, size=''):
        client.download_album_cover(aid, save_path, size=size)

    def save_image(self, client, img, path):
        client.download_by_image_detail(img, path, decode_image=not img.is_gif)
