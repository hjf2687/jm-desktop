"""无头功能测试：验证 jmcomic 调用链（搜索→详情→章节→图片解码→封面）。"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

if sys.stdout:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

from app.settings import Settings, BASE_DIR
from app import service as svc


def log(*a):
    print(*a, flush=True)


def main():
    settings = Settings()
    svc_ = svc.JmService(settings)

    # 1. 客户端
    log('== 创建客户端 (impl=%s) ==' % settings['client_impl'])
    client = svc_.new_client()
    log('   ok')

    # 2. 搜索
    log('== 搜索: "hololive" page=1 ==')
    items, total, page_count = svc_.search(client, 'hololive', mode='site', page=1)
    log(f'   total={total} page_count={page_count} 本页{len(items)}条')
    if not items:
        log('   [警告] 搜索无结果')
        return
    for it in items[:3]:
        log(f'   - {it.id}  {it.title[:40]}')

    # 3. 详情
    aid = items[0].id
    log(f'== 详情 aid={aid} ==')
    album = svc_.get_album(client, aid)
    log(f'   名称: {album.name[:50]}')
    log(f'   作者: {album.authors}  页数: {album.page_count}  章节: {len(album.chapters)}')
    log(f'   标签: {album.tags[:6]}')

    # 4. 章节图片详情
    ch = album.chapters[0]
    log(f'== 章节 {ch.pid} "{ch.title[:30]}" ==')
    photo = svc_.get_photo(client, ch.pid)
    log(f'   本页 {len(photo)} 张')
    if len(photo) == 0:
        log('   [警告] 章节无图片')
        return

    # 5. 下载+解码第一张图
    tmp = tempfile.mkdtemp()
    img = photo[0]
    out = os.path.join(tmp, 'test' + img.img_file_suffix)
    log(f'== 下载并解码图片 -> {out} (suffix={img.img_file_suffix}) ==')
    svc_.save_image(client, img, out)
    size = os.path.getsize(out)
    log(f'   已保存 {size} 字节')
    from PIL import Image
    im = Image.open(out)
    log(f'   PIL 校验: {im.format} {im.size}')

    # 6. 封面
    cover = os.path.join(tmp, 'cover.jpg')
    log('== 下载封面 ==')
    try:
        svc_.fetch_cover(client, aid, cover, size='_3x4')
        log(f'   已保存 {os.path.getsize(cover)} 字节')
        im2 = Image.open(cover)
        log(f'   PIL 校验: {im2.format} {im2.size}')
    except Exception as e:
        log(f'   [警告] 封面失败: {e}')

    log('== 全部通过 ==')


if __name__ == '__main__':
    main()
