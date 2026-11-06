"""把已下载的本子导出为 CBZ（zip 打包）或 PDF（img2pdf）。"""
import os
import zipfile

from .downloader import list_images, read_local_chapters
from .settings import sanitize_name


def chapter_images(album_dir):
    """返回 [(章节序号, 章节标题, [图片路径...]), ...]"""
    result = []
    for pid, index, title, ch_dir in read_local_chapters(album_dir):
        imgs = list_images(ch_dir) if ch_dir else []
        if imgs:
            result.append((index, title, imgs))
    return result


def _flat_images(album_dir):
    imgs = []
    for _idx, _title, chapter_imgs in chapter_images(album_dir):
        imgs.extend(chapter_imgs)
    return imgs


def _export_path(data, export_dir, suffix):
    os.makedirs(export_dir, exist_ok=True)
    return os.path.join(export_dir, f'{data["aid"]} {sanitize_name(data["name"])}{suffix}')


def export_cbz(data, export_dir: str) -> str:
    """导出为 CBZ：章节为子目录，图片按序号命名，保证阅读器排序正确。"""
    album_dir = data['dir']
    path = _export_path(data, export_dir, '.cbz')
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_STORED) as zf:
        for index, title, imgs in chapter_images(album_dir):
            folder = f'第{index:02d}话 {sanitize_name(title, 40)}' if title else f'第{index:02d}话'
            for i, img in enumerate(imgs, 1):
                ext = os.path.splitext(img)[1]
                zf.write(img, f'{folder}/{i:05d}{ext}')
    return path


def export_pdf(data, export_dir: str) -> str:
    """导出为 PDF：图片按章节顺序拼接，使用 img2pdf 直接嵌入原图（快、无损）。"""
    import img2pdf
    album_dir = data['dir']
    imgs = _flat_images(album_dir)
    if not imgs:
        raise ValueError('没有可导出的图片')
    path = _export_path(data, export_dir, '.pdf')
    with open(path, 'wb') as f:
        f.write(img2pdf.convert(imgs))
    return path
