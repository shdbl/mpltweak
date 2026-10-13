# -*- coding: utf-8 -*-
"""生成证据清单 MANIFEST.json：每个文件的 sha256 + 声明映射（含自校验）。

用法：
    python bench/tools/build_evidence_manifest.py --root bench/evidence/0.1.10

产出 MANIFEST.json：
    {
      "version": "0.1.10",
      "generated_at": "...",
      "files": [{"path", "bytes", "sha256"}],     ← 覆盖 root 下所有文件（不含 MANIFEST.json 自身）
      "claims": [...],                            ← 原样收录 CLAIMS.json 的 claims
      "guard": "python bench/tools/check_evidence_publishable.py --root <root>"
    }

为什么要有它：光有文件还不够 —— 得能证明"你下载到的文件就是当时那份"。
守卫 `check_evidence_publishable.py` 会拿这份清单逐个校验 sha256，不一致即失败。
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import sys
import time


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b''):
            h.update(chunk)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--root', required=True)
    ap.add_argument('--version', default='')
    args = ap.parse_args()
    root = args.root
    if not os.path.isdir(root):
        print('!! 目录不存在: %s' % root)
        return 1

    version = args.version
    claims = []
    claims_path = os.path.join(root, 'CLAIMS.json')
    if os.path.exists(claims_path):
        with io.open(claims_path, 'r', encoding='utf-8') as fh:
            data = json.load(fh)
        claims = data.get('claims') or []
        version = version or str(data.get('version') or data.get('_version') or '')

    files = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != '__pycache__']
        for fn in sorted(filenames):
            p = os.path.join(dirpath, fn)
            rel = os.path.relpath(p, root).replace('\\', '/')
            if rel == 'MANIFEST.json':
                continue
            files.append({
                'path': rel,
                'bytes': os.path.getsize(p),
                'sha256': sha256(p),
            })

    manifest = {
        'version': version,
        'generated_at': time.strftime('%Y-%m-%dT%H:%M:%S%z'),
        'generator': 'bench/tools/build_evidence_manifest.py',
        'guard': 'python bench/tools/check_evidence_publishable.py --root %s' % root.replace('\\', '/'),
        'files': files,
        'claims': claims,
    }
    out = os.path.join(root, 'MANIFEST.json')
    with io.open(out, 'w', encoding='utf-8', newline='\n') as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=2)
        fh.write('\n')

    total = sum(f['bytes'] for f in files)
    print('已写 %s' % out)
    print('  版本     : %s' % version)
    print('  文件数   : %d' % len(files))
    print('  合计大小 : %.1f MB' % (total / 1048576.0))
    print('  声明条数 : %d' % len(claims))
    for f in files:
        print('    %-46s %9d B  %s' % (f['path'], f['bytes'], f['sha256'][:16]))
    return 0


if __name__ == '__main__':
    sys.exit(main())
