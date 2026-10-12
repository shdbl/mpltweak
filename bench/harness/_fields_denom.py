#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""静态分母交叉核对：只用 fields_harness 的 AST 提取器扫全部语料（不跑任何 CLI）。

产出 `_fields_denom.json`：每族的
  scripts_with_literal / sites / reach_sites / unreach 原因分布 / 写法分布。
用于与全量跑出来的分母互相对照（两套独立数字必须一致）。
"""
import collections
import io
import json
import os
import sys
import ast

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import fields_harness as FH            # noqa: E402

rows = FH.load_manifest()
fam = collections.defaultdict(collections.Counter)
fam_unreach = collections.defaultdict(collections.Counter)
fam_kinds = collections.defaultdict(collections.Counter)
nonin = collections.defaultdict(collections.Counter)
parse_fail = []
arms = collections.Counter()
per_script = {}

for r in rows:
    p = os.path.join(FH.CORPUS, r['path'].replace('/', os.sep))
    raw = FH.read_bytes(p)
    text, enc = FH.decode_source(raw)
    try:
        tree = ast.parse(text)
    except SyntaxError as e:
        parse_fail.append(r['path'])
        continue
    ctx = FH.Ctx(text, tree)
    sites = FH.extract_sites(ctx)
    ni = FH.extract_non_inplace_sites(ctx)
    arms['n_figs_eq_0'] += 1 if ctx.n_figs == 0 else 0
    arms['n_figs_gt_1'] += 1 if ctx.n_figs > 1 else 0
    hit = {}
    for f, ss in sites.items():
        if not ss:
            continue
        fam[f]['literal_scripts'] += 1
        fam[f]['literal_sites'] += len(ss)
        fam[f]['reach_sites'] += sum(1 for s in ss if s['reach'])
        for s in ss:
            fam_kinds[f][s['kind']] += 1
            if not s['reach']:
                fam_unreach[f][s['reason']] += 1
        hit[f] = len(ss)
    for f, n in ni.items():
        if n:
            nonin[f]['literal_scripts'] += 1
            nonin[f]['literal_sites'] += len(n)
    per_script[r['path']] = hit

out = {
    'n_manifest': len(rows),
    'n_parse_ok': len(rows) - len(parse_fail),
    'parse_fail': parse_fail,
    'arms': dict(arms),
    'family': {f: dict(fam[f]) for f in FH.FAMILIES},
    'family_unreach': {f: dict(fam_unreach[f]) for f in FH.FAMILIES},
    'family_kinds': {f: dict(fam_kinds[f]) for f in FH.FAMILIES},
    'noninplace': {f: dict(nonin[f]) for f in FH.NONINPLACE_FAMILIES},
    'per_script': per_script,
}
io.open(os.path.join(HERE, '_fields_denom.json'), 'w',
        encoding='utf-8').write(json.dumps(out, ensure_ascii=False, indent=2))

lines = []
lines.append('n_manifest=%d parse_ok=%d parse_fail=%d' % (
    out['n_manifest'], out['n_parse_ok'], len(parse_fail)))
lines.append('arms=%s' % json.dumps(out['arms']))
lines.append('')
lines.append('%-18s %8s %8s %8s' % ('family', 'scripts', 'sites', 'reach'))
for f in FH.FAMILIES:
    lines.append('%-18s %8d %8d %8d' % (
        f, fam[f]['literal_scripts'], fam[f]['literal_sites'],
        fam[f]['reach_sites']))
lines.append('')
lines.append('--- unreachable reasons ---')
for f in FH.FAMILIES:
    if fam_unreach[f]:
        lines.append('%s: %s' % (f, json.dumps(dict(fam_unreach[f]),
                                                ensure_ascii=False)))
lines.append('')
lines.append('--- literal kinds ---')
for f in FH.FAMILIES:
    if fam_kinds[f]:
        lines.append('%s: %s' % (f, json.dumps(dict(fam_kinds[f]),
                                                ensure_ascii=False)))
lines.append('')
lines.append('--- non-inplace ---')
for f in FH.NONINPLACE_FAMILIES:
    lines.append('%s: %s' % (f, json.dumps(dict(nonin[f]), ensure_ascii=False)))
lines.append('')
lines.append('--- parse failures ---')
for p in parse_fail:
    lines.append(p)
io.open(os.path.join(HERE, '_fields_denom.txt'), 'w',
        encoding='utf-8').write('\n'.join(lines) + '\n')
print('done')
