# -*- coding: utf-8 -*-
"""对账：manifest 行数 vs 磁盘 .py 文件数 vs 各桶计数（排查 643/645 差异）。"""
import ast
import collections
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import l0_harness as H

rows = H.load_manifest()
print('load_manifest rows:', len(rows))
by_src = collections.Counter(r.get('source') for r in rows)
print('by source:', dict(by_src))
py = [r for r in rows if (r.get('path') or '').endswith('.py')]
print('py rows:', len(py), 'uniq:', len(set(r['path'] for r in py)))
non_py = [r.get('path') for r in rows if not (r.get('path') or '').endswith('.py')]
print('non_py rows:', len(non_py), non_py[:10])
missing = []
other_err = []
for r in py:
    fp = os.path.join(H.CORPUS, r['path'].replace('/', os.sep))
    if not os.path.exists(fp):
        missing.append(r['path'])
        continue
    raw = H.read_bytes(fp)
    try:
        ast.parse(raw)
    except SyntaxError as e:
        other_err.append((r['path'], 'SyntaxError %s' % e.msg))
    except Exception as e:                                     # noqa: BLE001
        other_err.append((r['path'], '%s: %s' % (type(e).__name__, e)))
print('missing on disk:', len(missing), missing[:10])
print('parse errors:', len(other_err), other_err[:10])
print('gallery py:', sum(1 for r in py if r['source'] == 'matplotlib-gallery'),
      'zenodo py:', sum(1 for r in py if r['source'] == 'zenodo'))
