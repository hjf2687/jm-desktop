"""用应用自身的下载管理器下载指定本子，验证完整下载链路。"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ['QT_QPA_PLATFORM'] = 'offscreen'

from PySide6.QtWidgets import QApplication

app = QApplication([])

from app.downloader import DownloadManager, JobState
from app.service import JmService
from app.settings import Settings

TARGETS = [
    ('1205735', '姐姐大賽 (上)'),
    ('1203880', '姐姐大賽 (下)'),
]

s = Settings()
svc = JmService(s)
mgr = DownloadManager(svc, s)
print(f'下载目录: {s["download_dir"]}', flush=True)

jobs = [(aid, mgr.submit(aid, name)) for aid, name in TARGETS]

t0 = time.time()
while True:
    active = [j for _, j in jobs if j.state in (JobState.WAITING, JobState.FETCH, JobState.DOWN)]
    if not active:
        break
    line = '  |  '.join(f'{aid}: {j.state} {j.done}/{j.total} 失败{j.failed}'
                        for aid, j in jobs)
    print(f'\r[{int(time.time() - t0)}s] ' + line, end='', flush=True)
    time.sleep(1)

print()
for aid, j in jobs:
    print(f'[{aid}] 最终状态={j.state}  成功={j.done} 失败={j.failed}  保存目录={j.save_dir or "(无)"}')

# 校验保存的文件
ok = True
for aid, j in jobs:
    if not j.save_dir or not os.path.isdir(j.save_dir):
        print(f'[{aid}] 目录不存在！')
        ok = False
        continue
    exts = ('*.jpg', '*.jpeg', '*.webp', '*.png', '*.gif')
    files = []
    for e in exts:
        files.extend(__import__('glob').glob(os.path.join(j.save_dir, e)))
    print(f'[{aid}] 实际文件数={len(files)}')
    if len(files) != j.done:
        print(f'[{aid}] 文件数与进度不一致！')
        ok = False
    info = os.path.join(j.save_dir, 'info.json')
    print(f'[{aid}] info.json 存在={os.path.exists(info)}  cover.jpg 存在={os.path.exists(os.path.join(j.save_dir, "cover.jpg"))}')

print('全部下载完成' if ok else '有异常，请检查')
