# -*- coding: utf-8 -*-
"""发布红线守卫：bench 证据目录**能不能对外发布**，由它说了算。

用法：
    python bench/tools/check_evidence_publishable.py                # 扫 bench/evidence/**
    python bench/tools/check_evidence_publishable.py --root bench/evidence/0.1.10

红线（任一命中即失败，退出码 1）：
  1. `本机绝对路径`：出现盘符路径（`X:\\` / `X:/`）——证据里只允许 `<work>` 这类占位符；
  2. `用户名 / 家目录`：出现 `Users\\<name>` 形式的路径；
  3. `第三方脚本正文`：`.jsonl/.json` 里出现 `after_text`/`before_text`/`pristine`/
     `source_text`/`diff_head` 这些**正文键**（与静态材料的既有守卫同口径）；
  4. `MANIFEST.json 校验`：清单里登记的文件必须存在，且 sha256 与清单一致（防发布后被改动）。

其余（`.md/.py/.txt/.log` 里出现 `import matplotlib` 这类代码串）**只计数、不判失败** ——
因为诊断文案里会引用这些词（例如"脚本没有 import matplotlib.pyplot as plt"），那不是正文。
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import sys

# 本机绝对路径的三种真实形态（必须避开 JSON 转义序列的误报）：
#   ① JSON 转义形态：`d:\\agent\\...`（两个反斜杠）
#   ② 裸形态：`D:\Agent\...`（一个反斜杠）
#   ③ 正斜杠形态：`D:/x`（排除 `http://` 里的 `p:/`）
# 关键坑（实测踩过）：`PEG:\n`（冒号后接 JSON 转义的换行）会被朴素正则 `[A-Za-z]:[\\/]` 误判成
# 盘符路径 —— 一次误报 20 处。所以裸形态要排除"反斜杠 + 转义字母 + 非路径字符"的情形，
# 同时**不能**误伤 `D:\numpy`（`n` 后面还有路径字符 ⇒ 是真路径）。
ABSOLUTE = re.compile(
    r'[A-Za-z]:\\\\[^\s"]'                       # ①  JSON 转义形态
    r'|[A-Za-z]:/(?!/)'                          # ③  正斜杠形态（`(?!/)` 排除 http://）
    r'|[A-Za-z]:\\(?![nrtbfu](?![A-Za-z0-9_]))'  # ②  裸形态（排除 \n \t \r \b \f \u 转义）
)
USERS = re.compile(r'[Uu]sers[\\/]{1,2}[A-Za-z0-9_.\-]+')
TEXT_KEYS = ['after_text', 'before_text', 'pristine', 'source_text', 'diff_head']
CODE_HINTS = ['import matplotlib', 'plt.subplots(', 'plt.figure(', 'fig.add_axes(',
              'savefig(', 'import numpy']

REQUIRED = [
    'README.md',
    'CLAIMS.json',
    'corpus/results.jsonl',
    'corpus/SUMMARY.md',
    'corpus/RUN_META.json',
    'window/NUMBERS.md',
    'window/run_qt9.log',
    'window/run_tk7.log',
    'window/selftest_t22.log',
    'window/ledger_drylist.txt',
]


def read_text(path):
    with open(path, 'rb') as fh:
        raw = fh.read()
    for enc in ('utf-8', 'gbk'):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode('utf-8', errors='replace')


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b''):
            h.update(chunk)
    return h.hexdigest()


def walk(root):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in ('__pycache__',)]
        for fn in filenames:
            yield os.path.join(dirpath, fn)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--root', default=os.path.join('bench', 'evidence'))
    args = ap.parse_args()
    root = args.root
    if not os.path.isdir(root):
        print('!! 目录不存在: %s' % root)
        return 1

    fails = []
    warns = 0
    n_files = 0

    print('=== 发布红线守卫: %s ===' % root)
    for path in sorted(walk(root)):
        rel = os.path.relpath(path, root).replace('\\', '/')
        if rel == 'MANIFEST.json':
            continue
        n_files += 1
        text = read_text(path)
        for i, line in enumerate(io.StringIO(text), 1):
            m = ABSOLUTE.search(line)
            if m:
                fails.append('%s:%d 本机绝对路径: %s' % (rel, i, m.group(0)[:60]))
            m = USERS.search(line)
            if m:
                fails.append('%s:%d 用户名/家目录: %s' % (rel, i, m.group(0)[:60]))
            if rel.endswith(('.jsonl', '.json')):
                for key in TEXT_KEYS:
                    if ('"%s"' % key) in line:
                        fails.append('%s:%d 疑似第三方脚本正文键: %s' % (rel, i, key))
        if not rel.endswith(('.jsonl', '.json')):
            for hint in CODE_HINTS:
                if hint in text:
                    warns += 1
                    break

    # 必备文件：**优先用包自己声明的** `_required_files`（CLAIMS.json）——
    # 这样每个版本的增量包可以自报"我该有哪些文件"，而守卫仍然**真的逐个检查**
    # （不是把检查关掉）。没有声明时退回 0.1.10 的固定清单 ⇒ 既有行为一字不变。
    required = REQUIRED
    _cp = os.path.join(root, 'CLAIMS.json')
    if os.path.exists(_cp):
        try:
            with io.open(_cp, 'r', encoding='utf-8') as _fh:
                _decl = json.load(_fh).get('_required_files')
            if _decl:
                required = list(_decl)
                print('必备文件清单：来自 CLAIMS.json 的 _required_files（%d 项）'
                      % len(required))
        except (ValueError, OSError):
            pass
    # 必备文件
    for req in required:
        if not os.path.exists(os.path.join(root, req)):
            fails.append('缺少必备文件: %s' % req)

    # MANIFEST 校验
    manifest_path = os.path.join(root, 'MANIFEST.json')
    if os.path.exists(manifest_path):
        try:
            with io.open(manifest_path, 'r', encoding='utf-8') as fh:
                manifest = json.load(fh)
            listed = manifest.get('files') or []
            for item in listed:
                p = os.path.join(root, item['path'].replace('/', os.sep))
                if not os.path.exists(p):
                    fails.append('清单登记的文件不存在: %s' % item['path'])
                    continue
                got = sha256(p)
                if got != item.get('sha256'):
                    fails.append('sha256 与清单不符: %s（清单 %s… 实得 %s…）'
                                 % (item['path'], str(item.get('sha256'))[:12], got[:12]))
            print('MANIFEST: 登记 %d 个文件' % len(listed))
        except Exception as exc:  # noqa: BLE001
            fails.append('MANIFEST.json 无法解析: %s' % exc)
    else:
        print('（无 MANIFEST.json：跳过清单校验，可用 build_evidence_manifest.py 生成）')

    print('扫描文件 %d 个；代码串提示 %d 处（不计失败）' % (n_files, warns))
    if fails:
        print('')
        print('!! 未通过 %d 条红线：' % len(fails))
        for f in fails[:40]:
            print('   - %s' % f)
        return 1
    print('OK：未发现本机绝对路径 / 用户名 / 脚本正文；必备文件齐全。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
