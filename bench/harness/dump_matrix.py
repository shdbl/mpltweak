# -*- coding: utf-8 -*-
"""紧凑表格：M3/M5/M6 全量 fail 的形态，以及 M4 fail 的细节。"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
rows = [json.loads(l) for l in open(os.path.join(HERE, 'l0_results.jsonl'), encoding='utf-8')
        if l.strip()]
out = []
out.append('===== 24 个可测脚本的全景 =====')
out.append('%-62s %-14s %-14s %-14s %s' % ('path', 'M3', 'M4', 'M5', 'M6'))
for r in rows:
    if r['m2_extract']['status'] != 'pass' or r['meta']['family'] != 'add_axes':
        continue
    g = lambda k: r[k]['status']
    out.append('%-62s %-14s %-14s %-14s %s' % (r['path'], g('m3_clean_roundtrip'),
                                               g('m4_directed_rewrite'), g('m5_idempotent'),
                                               g('m6_revert')))
out.append('')
out.append('===== M3 fail 的形态（style / changes / block / apply_reason / warnings数）=====')
for r in rows:
    m = r['m3_clean_roundtrip']
    if m['status'] != 'fail':
        continue
    out.append('%-58s style=%-8s reason=%-12s changes=%-14s block=%-5s warns=%d npairs=%s inplace_only=%s'
               % (r['path'], m.get('style'), m.get('apply_reason'), json.dumps(m.get('changes')),
                  m.get('block_inserted'), len(m.get('warnings') or []),
                  r['meta'].get('n_add_axes_in_target_fig'),
                  json.dumps(m.get('inplace_only'), ensure_ascii=False)))
out.append('')
out.append('===== M4 fail 细节 =====')
for r in rows:
    m = r['m4_directed_rewrite']
    if m['status'] != 'fail':
        continue
    out.append(json.dumps({'path': r['path'], 'm4': m, 'meta': {
        k: r['meta'].get(k) for k in ('n_add_axes_literals', 'n_add_axes_in_target_fig',
                                      'fig_index_used', 'n_fig_calls')}}, ensure_ascii=False, indent=1))
out.append('')
out.append('===== M6 fail 的 sha 关系 =====')
for r in rows:
    m = r['m6_revert']
    if m['status'] != 'fail':
        continue
    out.append('%-58s cascade=%-5s sha_orig=%s sha_after=%s' % (
        r['path'], m.get('cascade_from_m5'), (m.get('sha_original') or '')[:12],
        (m.get('sha_after_revert') or '')[:12]))
out.append('')
out.append('===== M1 fail =====')
for r in rows:
    if r['m1_parse']['status'] == 'fail':
        out.append('%s | %s' % (r['path'], r['m1_parse']['reason']))
with open(os.path.join(HERE, '_matrix.txt'), 'w', encoding='utf-8') as f:
    f.write('\n'.join(out) + '\n')
print('ok')
