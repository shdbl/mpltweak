# -*- coding: utf-8 -*-
"""把 l0_results.jsonl 里的关键信息打成 UTF-8 文本，供 read 工具查看（绕开控制台代码页）。"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
HERE = os.path.dirname(os.path.abspath(__file__))

path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, 'l0_results.jsonl')
out_path = sys.argv[2] if len(sys.argv) > 2 else os.path.join(HERE, '_inspect.txt')
mode = sys.argv[3] if len(sys.argv) > 3 else 'fails'

rows = [json.loads(l) for l in open(path, encoding='utf-8') if l.strip()]
M = ('m3_clean_roundtrip', 'm4_directed_rewrite', 'm5_idempotent', 'm6_revert')
out = []
out.append('总行数 %d' % len(rows))
out.append('M1 fail: %d | M2 skipped: %d | M2 pass: %d' % (
    sum(1 for r in rows if r['m1_parse']['status'] == 'fail'),
    sum(1 for r in rows if r['m2_extract']['status'] == 'skipped'),
    sum(1 for r in rows if r['m2_extract']['status'] == 'pass')))

if mode in ('fails', 'all'):
    out.append('\n===== 所有含 fail 的行 =====')
    for r in rows:
        if not any(r[k]['status'] == 'fail' for k in M):
            continue
        out.append('\n--- %s  (%s, layout=%s, aa_all=%s, aa_in_fig=%s, fig=%s/%s)' % (
            r['path'], r['source'], r['meta'].get('layout_tags'),
            r['meta'].get('n_add_axes_literals'), r['meta'].get('n_add_axes_in_target_fig'),
            r['meta'].get('fig_index_used'), r['meta'].get('n_fig_calls')))
        out.append('  workdir: %s' % r['workdir'])
        for k in M:
            m = r[k]
            if m['status'] == 'skipped':
                out.append('  %-22s skipped  %s' % (k, m.get('reason')))
                continue
            keep = {kk: vv for kk, vv in m.items() if kk not in ('warnings',)}
            out.append('  %-22s %s' % (k, json.dumps(keep, ensure_ascii=False)))
            for w in (m.get('warnings') or []):
                out.append('        warn: %s' % w)
        out.append('  evidence: %s' % json.dumps(r.get('evidence', {}), ensure_ascii=False))

if mode in ('detail', 'all'):
    sel = sys.argv[4] if len(sys.argv) > 4 else ''
    out.append('\n===== 明细 %s =====' % sel)
    for r in rows:
        if sel and sel not in r['path']:
            continue
        out.append('\n--- %s' % r['path'])
        out.append(json.dumps(r, ensure_ascii=False, indent=1))

if mode == 'm2skip':
    out.append('\n===== M2 skipped 清单 =====')
    for r in rows:
        if r['m2_extract']['status'] == 'skipped':
            out.append('  %s | layout=%s | figs=%s' % (
                r['path'], r['meta'].get('layout_tags'), r['meta'].get('n_fig_calls')))

if mode == 'summary':
    for r in rows:
        marks = ''.join({'pass': '.', 'fail': 'F', 'skipped': 's', 'timeout': 'T'}
                        .get(r[k]['status'], '?') for k in M)
        out.append('%s  aa=%s/%s fig=%s/%s  %s' % (
            marks, r['meta'].get('n_add_axes_literals'),
            r['meta'].get('n_add_axes_in_target_fig'),
            r['meta'].get('fig_index_used'), r['meta'].get('n_fig_calls'), r['path']))

with open(out_path, 'w', encoding='utf-8') as f:
    f.write('\n'.join(out) + '\n')
print('wrote', out_path, len(out), 'lines')
