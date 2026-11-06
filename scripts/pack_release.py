# -*- coding: utf-8 -*-
"""一键打包发布：生成"最干净"的 zip —— 只含程序代码，绝不含任何个人数据与缓存。

用法：
    python scripts/pack_release.py              # 打包
    python scripts/pack_release.py --list       # 只列出会进包的文件，不生成 zip
    python scripts/pack_release.py --out DIR    # 指定输出目录
    python scripts/pack_release.py --no-open    # 打包后不自动打开输出文件夹

设计原则（白名单式）：
    只有明确列出的文件/目录才会进包，其余一律不进。
    所以将来新增了目录（比如 log/、temp/），默认就是被排除的，不需要维护黑名单。
"""
import argparse
import datetime
import os
import re
import subprocess
import sys
import zipfile

try:  # 保证中文在 Windows 控制台正常显示
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

# ---------------------------------------------------------------- 路径

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_OUT_ROOT = os.path.join(os.path.dirname(BASE_DIR), 'JM桌面端_发布')
TOP_NAME = 'JM桌面端'          # zip 内的顶层文件夹名

# ---------------------------------------------------------------- 打包规则

# 根目录下要包含的文件（相对 BASE_DIR）
INCLUDE_ROOT_FILES = [
    'main.py',
    'requirements.txt',
    'README.md',
    '启动JM桌面端.bat',
]

# 要包含的目录（递归，.py 等源码文件）
INCLUDE_DIRS = [
    'app',
]

# 路径中任何一层命中这些名字就整块跳过
EXCLUDE_DIRS = {
    '__pycache__',
    'backup_20260901',      # 旧版本备份
    'data',                 # 设置 / 收藏 / 进度 / 封面缓存 / 阅读缓存 / 日志
    '下载',                  # 下载的漫画本体
    '发布', 'dist', 'build', '_发布',
    '.git', '.idea', '.vscode', '.workbuddy', '.venv', 'venv',
}

# 后缀命中就跳过
EXCLUDE_SUFFIX = (
    '.pyc', '.pyo', '.pyd',
    '.log', '.tmp', '.bak', '.zip', '.7z', '.rar',
)

# 隐私哨兵：这些文件即使被误加进白名单，也一律拒绝打包
FORBIDDEN_NAMES = {
    'settings.json',    # 含账号 / 密码 / 代理
    'favorites.json',   # 收藏列表（含本子标题）
    'progress.json',    # 阅读进度（含本子标题）
    'app.log', 'error.log', 'gui_run.log', 'gui_run2.log',
}
FORBIDDEN_SUFFIX = ('.json',)   # 本项目源码不需要任何 .json，兜底拦一道


# ---------------------------------------------------------------- 收集

def rel_posix(path):
    return os.path.relpath(path, BASE_DIR).replace('\\', '/')


def _is_excluded_dir(name):
    """目录是否要跳过：名单命中，或名字以 backup 开头（任何日期的备份都不进包）。"""
    return name in EXCLUDE_DIRS or name.lower().startswith('backup')


def collect():
    """返回 (files, missing, skipped)。files 为 [(绝对路径, zip 内相对路径)]。"""
    files, missing, skipped = [], [], []

    for name in INCLUDE_ROOT_FILES:
        p = os.path.join(BASE_DIR, name)
        if os.path.isfile(p):
            files.append((p, name))
        else:
            missing.append(name)

    for d in INCLUDE_DIRS:
        root = os.path.join(BASE_DIR, d)
        if not os.path.isdir(root):
            missing.append(d + '/')
            continue
        for cur, dirs, names in os.walk(root):
            # 原地裁剪 dirs，walk 就不会进这些子目录
            keep = [x for x in dirs if not _is_excluded_dir(x)]
            for x in set(dirs) - set(keep):
                skipped.append(rel_posix(os.path.join(cur, x)) + '/')
            dirs[:] = keep
            for f in names:
                ap = os.path.join(cur, f)
                rel = rel_posix(ap)
                if f.endswith(EXCLUDE_SUFFIX):
                    skipped.append(rel)
                    continue
                if _is_forbidden(rel):
                    skipped.append(rel)
                    continue
                files.append((ap, rel))

    files.sort(key=lambda t: t[1])
    return files, missing, skipped


def _is_forbidden(rel):
    """隐私 / 路径安全检查：命中任一条件就拒绝进包。"""
    parts = rel.split('/')
    if any(_is_excluded_dir(p) for p in parts[:-1]):
        return True
    base = parts[-1]
    if base in FORBIDDEN_NAMES:
        return True
    if base.lower().endswith(FORBIDDEN_SUFFIX):
        return True
    return False


def scan_leftovers():
    """统计项目里"没进包"的东西有多少，让"干净"看得见。"""
    included = set(INCLUDE_ROOT_FILES) | set(INCLUDE_DIRS)
    rows = []
    for name in sorted(os.listdir(BASE_DIR)):
        if name in included:
            continue
        p = os.path.join(BASE_DIR, name)
        n = sz = 0
        if os.path.isdir(p):
            for r, dirs, fs in os.walk(p):
                # 这里统计的是"整个没进包的东西"，所以不裁剪子目录
                for f in fs:
                    try:
                        sz += os.path.getsize(os.path.join(r, f))
                        n += 1
                    except OSError:
                        pass
        else:
            try:
                sz = os.path.getsize(p)
                n = 1
            except OSError:
                pass
        rows.append((name, n, sz))
    return rows


# ---------------------------------------------------------------- 打包

def unique_path(dirpath, stem, ext):
    p = os.path.join(dirpath, stem + ext)
    n = 2
    while os.path.exists(p):
        p = os.path.join(dirpath, f'{stem}_{n}{ext}')
        n += 1
    return p


def human(n):
    for unit in ('B', 'KB', 'MB', 'GB'):
        if n < 1024 or unit == 'GB':
            return f'{n:.0f} {unit}' if unit == 'B' else f'{n:.1f} {unit}'
        n /= 1024.0


def build_zip(files, out_root):
    os.makedirs(out_root, exist_ok=True)
    stamp = datetime.datetime.now().strftime('%Y%m%d')
    zip_path = unique_path(out_root, f'{TOP_NAME}_{stamp}', '.zip')

    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for ap, rel in files:
            zf.write(ap, f'{TOP_NAME}/{rel}')

    return zip_path


def verify_zip(zip_path, expect_count):
    """把 zip 读回来复检一遍，确保没有不该进去的东西。"""
    problems = []
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
        bad = zf.testzip()
    if bad is not None:
        problems.append(f'压缩包内文件损坏: {bad}')
    if len(names) != expect_count:
        problems.append(f'文件数不符：预期 {expect_count}，实际 {len(names)}')

    for n in names:
        parts = n.split('/')
        if len(parts) < 2 or parts[0] != TOP_NAME:
            problems.append(f'顶层结构异常: {n}')
            continue
        inner = '/'.join(parts[1:])
        if _is_forbidden(inner):
            problems.append(f'混入不应打包的文件: {n}')
    return names, problems


# ---------------------------------------------------------------- 输出

def print_manifest(files, missing, skipped):
    print()
    print('=' * 62)
    print(f'  打包清单  ——  {TOP_NAME}')
    print('=' * 62)

    groups = {}
    for _, rel in files:
        head = rel.split('/')[0] if '/' in rel else '(根目录)'
        groups.setdefault(head, []).append(rel)

    total = 0
    for head in sorted(groups, key=lambda h: (h != '(根目录)', h)):
        items = groups[head]
        print(f'\n  [{head}]  {len(items)} 个文件')
        for rel in items:
            size = os.path.getsize(os.path.join(BASE_DIR, rel))
            total += size
            print(f'      {rel:<44} {human(size):>9}')

    print()
    print(f'  合计：{len(files)} 个文件，未压缩 {human(total)}')

    if skipped:
        print()
        print(f'  打包时跳过 {len(skipped)} 项（缓存 / 备份 / 编译产物）')

    leftovers = scan_leftovers()
    if leftovers:
        ln = sum(r[1] for r in leftovers)
        lsz = sum(r[2] for r in leftovers)
        print()
        print(f'  以下内容留在本地、不会进包（合计 {ln} 个文件 / {human(lsz)}）：')
        for name, n, sz in sorted(leftovers, key=lambda r: -r[2]):
            print(f'      · {name:<30} {n:>5} 个   {human(sz):>9}')

    if missing:
        print()
        print('  ⚠ 清单里有文件缺失（不影响打包，但请确认）：')
        for m in missing:
            print(f'      ? {m}')


def main():
    ap = argparse.ArgumentParser(description='一键打包发布（只含代码，不含个人数据）')
    ap.add_argument('--out', default=DEFAULT_OUT_ROOT, help='输出目录')
    ap.add_argument('--list', action='store_true', help='只列出文件，不生成 zip')
    ap.add_argument('--no-open', action='store_true', help='打包后不自动打开输出文件夹')
    args = ap.parse_args()

    files, missing, skipped = collect()

    # 打包前的最后一道闸：任何一个文件命中隐私规则就整体中止
    fatal = [rel for _, rel in files if _is_forbidden(rel)]
    if fatal:
        print('\n[x] 中止：以下文件命中隐私规则，不允许打包：')
        for f in fatal:
            print('    ', f)
        return 2

    print_manifest(files, missing, skipped)

    if args.list:
        print('\n（--list 模式，未生成 zip）')
        return 0

    out_root = os.path.abspath(args.out)
    try:
        zip_path = build_zip(files, out_root)
    except OSError as e:
        print(f'\n[x] 写入失败：{e}')
        return 1

    names, problems = verify_zip(zip_path, len(files))
    size = os.path.getsize(zip_path)

    print()
    print('=' * 62)
    if problems:
        print('  [x] 复检未通过：')
        for p in problems:
            print('     ', p)
        print('=' * 62)
        return 3

    print('  [√] 打包完成，复检通过')
    print('=' * 62)
    print(f'  压缩包：{zip_path}')
    print(f'  大  小：{human(size)}（{len(names)} 个文件）')
    print()
    print(f'  解压后是一个干净的 “{TOP_NAME}” 文件夹，双击其中的')
    print('  “启动JM桌面端.bat” 即可运行。')
    print('  接收方首次使用需先装依赖：pip install -r requirements.txt')
    print()

    if not args.no_open:
        try:
            subprocess.Popen(['explorer', os.path.normpath(out_root)])
        except Exception:
            pass
    return 0


if __name__ == '__main__':
    sys.exit(main())
