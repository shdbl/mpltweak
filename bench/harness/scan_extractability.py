# -*- coding: utf-8 -*-
"""语料全量静态可提取性扫描（不调用 mpltweak，不执行脚本）。"""
import ast
import collections
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import l0_harness as H

rows = H.load_manifest()
cnt = collections.Counter()
ax_total = 0
fs_total = 0
per_source = collections.defaultdict(collections.Counter)
parse_fails = []
for r in rows:
    p = r.get('path', '')
    if not p.endswith('.py'):
        cnt['non_py'] += 1
        continue
    fp = os.path.join(H.CORPUS, p.replace('/', os.sep))
    if not os.path.exists(fp):
        cnt['missing'] += 1
        continue
    raw = H.read_bytes(fp)
    try:
        ast.parse(raw)
    except SyntaxError as e:
        cnt['parse_fail'] += 1
        parse_fails.append((p, '%s line %s: %s' % (type(e).__name__, e.lineno, e.msg)))
        per_source[r.get('source')]['parse_fail'] += 1
        continue
    text, enc = H.decode_source(raw)
    try:
        ast.parse(text)
    except SyntaxError:
        text = raw.decode('utf-8', 'replace')
    aa = H.collect_add_axes(text)
    fg = H.collect_figsize(text)
    src = r.get('source')
    cnt['files'] += 1
    if aa:
        cnt['aa_' + str(src)] += 1
        ax_total += len(aa)
        per_source[src]['add_axes'] += 1
    elif fg:
        cnt['fs_' + str(src)] += 1
        fs_total += 1
        per_source[src]['figsize_only'] += 1
    else:
        cnt['none_' + str(src)] += 1
        per_source[src]['none'] += 1
    if aa and fg:
        cnt['both'] += 1
print(json.dumps(dict(cnt), ensure_ascii=False, indent=1, sort_keys=True))
print('per_source:', json.dumps({k: dict(v) for k, v in per_source.items()},
                                ensure_ascii=False, indent=1))
print('total add_axes literals:', ax_total, ' figsize-only scripts:', fs_total)
print('parse fails (%d):' % len(parse_fails))
for p, m in parse_fails[:15]:
    print('  ', p, '|', m)
