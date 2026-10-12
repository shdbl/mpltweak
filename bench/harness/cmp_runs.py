# -*- coding: utf-8 -*-
"""修复后复测：基线与 engine 副本的交集对比 + 残余失败清单（只读结果文件）。"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
M = ('m1_parse', 'm2_extract', 'm3_clean_roundtrip', 'm4_directed_rewrite',
     'm5_idempotent', 'm6_revert')


def load(name):
    p = os.path.join(HERE, name)
    return {r['path']: r for r in
            (json.loads(l) for l in open(p, encoding='utf-8') if l.strip())}


base = load('l0_results_018baseline.jsonl')
eng = load('l0_results_engine.jsonl')
common = sorted(set(base) & set(eng))
out = []
out.append('base=%d engine=%d common=%d  only_base=%d only_engine=%d'
           % (len(base), len(eng), len(common), len(base) - len(common), len(eng) - len(common)))
for k in M:
    row = []
    for nm, d in (('base', base), ('engine', eng)):
        c = {}
        for p in common:
            c[d[p][k]['status']] = c.get(d[p][k]['status'], 0) + 1
        row.append('%s=%s' % (nm, json.dumps(c, ensure_ascii=False)))
    out.append('%-22s %s | %s' % (k, row[0], row[1]))

out.append('')
out.append('===== engine: M6 skip 原因分布 =====')
c = {}
for p in common:
    m = eng[p]['m6_revert']
    c['%s|%s' % (m['status'], (m.get('reason') or '')[:40])] = \
        c.get('%s|%s' % (m['status'], (m.get('reason') or '')[:40]), 0) + 1
for k, v in sorted(c.items(), key=lambda x: -x[1]):
    out.append('  %-70s %d' % (k, v))

out.append('')
out.append('===== engine: M4/M5 状态与 backup_exists =====')
for p in common:
    m4 = eng[p]['m4_directed_rewrite']
    m5 = eng[p]['m5_idempotent']
    if m4['status'] not in ('skipped',):
        out.append('  %-58s M4=%-6s(model=%s style=%s changes=%s) M5=%-6s(%s)' % (
            p, m4['status'], m4.get('params_model'), m4.get('style'),
            json.dumps(m4.get('changes'), ensure_ascii=False), m5['status'],
            m5.get('apply_reason')))

out.append('')
out.append('===== engine: M3 残余 FAIL（逐条） =====')
n = 0
for p in common:
    m = eng[p]['m3_clean_roundtrip']
    if m['status'] != 'fail':
        continue
    n += 1
    out.append('%2d. %s' % (n, p))
    out.append('    %s' % json.dumps({k: v for k, v in m.items() if k != 'warnings'},
                                     ensure_ascii=False))
    for w in (m.get('warnings') or []):
        out.append('      warn: %s' % w)
    out.append('    meta: model=%s axes_aligned=%s aa_lit=%s fig=%s/%s span=%s before_fig=%s' % (
        eng[p]['meta'].get('params_model'), eng[p]['meta'].get('n_axes_aligned'),
        eng[p]['meta'].get('n_add_axes_literals'), eng[p]['meta'].get('fig_index_used'),
        eng[p]['meta'].get('n_fig_calls'), eng[p]['meta'].get('fig_span'),
        eng[p]['meta'].get('pos_before_fig_call')))

out.append('')
out.append('===== engine: M5 残余 FAIL =====')
for p in common:
    m = eng[p]['m5_idempotent']
    if m['status'] == 'fail':
        out.append('  %s' % p)
        out.append('    %s' % json.dumps({k: v for k, v in m.items() if k != 'warnings'},
                                         ensure_ascii=False))
        for w in (m.get('warnings') or []):
            out.append('      warn: %s' % w)

out.append('')
out.append('===== engine: M4 残余 FAIL =====')
for p in common:
    m = eng[p]['m4_directed_rewrite']
    if m['status'] == 'fail':
        out.append('  %s' % p)
        out.append('    %s' % json.dumps({k: v for k, v in m.items() if k != 'warnings'},
                                         ensure_ascii=False))
        for w in (m.get('warnings') or []):
            out.append('      warn: %s' % w)

out.append('')
out.append('===== 回归检查：base pass -> engine fail/timeout =====')
reg = 0
for p in common:
    for k in M:
        b, e = base[p][k]['status'], eng[p][k]['status']
        if b == 'pass' and e in ('fail', 'timeout'):
            reg += 1
            out.append('  %-58s %-22s pass -> %s (%s)' % (p, k, e, e and eng[p][k].get('reason')))
out.append('  回归条数 = %d' % reg)

with open(os.path.join(HERE, '_cmp.txt'), 'w', encoding='utf-8') as f:
    f.write('\n'.join(out) + '\n')
print('ok')
