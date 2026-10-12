# -*- coding: utf-8 -*-
"""逐族回答"为什么没到 100%"：把非原位落地的组合按原因分类。只读。

分类（互斥，优先顺序固定）：
  A 真原位落地     style=inplace & applied & bytes_changed
  B 改为块落地     style=block   & applied & bytes_changed   ← 改动**落地了**，只是不在原位
  C 证不出而拒绝   applied=false & pending 非空（T70 fail-closed；字节不变）
  D 参数非法被拒   rc != 0（bad_params 等；字节不变）
  E 其它未落地     applied=false 且无 pending
用法：python _cu_why_not100.py [<scan.jsonl>]
"""
from __future__ import annotations

import collections
import io
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
P = sys.argv[1] if len(sys.argv) > 1 else \
    os.path.normpath(os.path.join(_HERE, '..', 'verdicts', 'scan_full.jsonl'))

FAMS = ['figsize_in', 'pos', 'title_fontsize', 'label_fontsize', 'label_fontsize_y',
        'tick_fontsize', 'tick_fontsize_y', 'grid', 'xlim', 'ylim',
        'spines', 'xscale', 'yscale', 'clim']
CN = {'figsize_in': 'figsize', 'pos': 'pos', 'title_fontsize': '标题字号',
      'label_fontsize': '标签x', 'label_fontsize_y': '标签y',
      'tick_fontsize': '刻度x', 'tick_fontsize_y': '刻度y', 'grid': 'grid',
      'xlim': 'xlim', 'ylim': 'ylim', 'spines': 'spines',
      'xscale': 'xscale', 'yscale': 'yscale', 'clim': 'clim'}

rows = []
with io.open(P, encoding='utf-8') as f:
    for line in f:
        line = line.strip()
        if line:
            try:
                rows.append(json.loads(line))
            except Exception:
                pass
print('scan = %s\n脚本行数 = %d' % (P, len(rows)))

stat = collections.defaultdict(collections.Counter)
reasons = collections.defaultdict(collections.Counter)
pending = collections.defaultdict(collections.Counter)
samples = collections.defaultdict(list)

for r in rows:
    fams = r.get('families') or {}
    path = r.get('path') or ''
    for fam in FAMS:
        d = fams.get(fam)
        if not isinstance(d, dict):
            continue
        stat[fam]['site'] += 1 if (d.get('n_sites') or 0) > 0 else 0
        stat[fam]['reach'] += 1 if (d.get('n_reach') or 0) > 0 else 0
        if (d.get('n_reach') or 0) <= 0 and (d.get('n_sites') or 0) <= 0:
            continue
        rc = d.get('rc')
        style = str(d.get('decl_style'))
        applied = d.get('decl_applied')
        chg = d.get('bytes_changed')
        pend = d.get('decl_pending') or []
        reason = str(d.get('decl_reason'))
        af = d.get('decl_auto_fallback')
        if style == 'inplace' and applied and chg:
            k = 'A 真原位'
        elif style == 'block' and applied and chg:
            k = 'B 改为块落地'
        elif rc not in (0, None):
            k = 'D 参数非法被拒'
        elif pend:
            k = 'C 证不出而拒绝'
        elif applied is False:
            k = 'E 其它未落地'
        else:
            k = 'E 其它未落地'
        stat[fam][k] += 1
        if k in ('B 改为块落地', 'C 证不出而拒绝', 'D 参数非法被拒', 'E 其它未落地'):
            reasons[fam]['%s | reason=%s | af=%s' % (k, reason, af)] += 1
            if pend:
                pending[fam][str(pend[:2])] += 1
            if len(samples[fam]) < 4:
                samples[fam].append((k, path[-58:], reason))

print('\n' + '=' * 108)
hdr = '%-9s %6s %6s %8s %8s %8s %8s %8s' % ('族', '有站点', '可达', 'A真原位',
                                            'B块落地', 'C拒落地', 'D参数拒', 'E其它')
print(hdr)
print('-' * 108)
tot = collections.Counter()
for fam in FAMS:
    s = stat[fam]
    if not any(s.values()):
        print('%-9s %6d %6d %8s' % (CN[fam], 0, 0, '（本次无站点）'))
        continue
    print('%-9s %6d %6d %8d %8d %8d %8d %8d' % (
        CN[fam], s['site'], s['reach'], s['A 真原位'], s['B 改为块落地'],
        s['C 证不出而拒绝'], s['D 参数非法被拒'], s['E 其它未落地']))
    for k in ('A 真原位', 'B 改为块落地', 'C 证不出而拒绝', 'D 参数非法被拒', 'E 其它未落地'):
        tot[k] += s[k]
print('-' * 108)
print('%-9s %6s %6s %8d %8d %8d %8d %8d' % (
    '合计', '', '', tot['A 真原位'], tot['B 改为块落地'], tot['C 证不出而拒绝'],
    tot['D 参数非法被拒'], tot['E 其它未落地']))

print('\n' + '=' * 108)
print('非原位落地的**原因字符串**（逐族，前 3 类）')
for fam in FAMS:
    if not reasons[fam]:
        continue
    print('\n[%s]' % CN[fam])
    for k, n in reasons[fam].most_common(3):
        print('   %3d  %s' % (n, k[:120]))
    for k, n in pending[fam].most_common(2):
        print('        pending=%s  ×%d' % (k, n))
    for k, p, rs in samples[fam][:2]:
        print('        例: %s | %s | %s' % (k, p, rs[:40]))
