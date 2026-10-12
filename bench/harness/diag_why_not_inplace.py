# -*- coding: utf-8 -*-
"""根因诊断（**纯 AST，不执行任何脚本**）：直接调 apply_inplace 看 covered/skipped 从哪来。

这是诊断脚本，不属于 L0 判据链（L0 的 M4 复核器完全独立、不 import mpltweak）。
用法: python diag_why_not_inplace.py <语料相对路径> <params.json>
"""
import ast
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import l0_harness as H

sys.path.insert(0, H.MPLTWEAK_SRC)
from mpltweak import writeback as W           # noqa: E402  （纯 AST 模块，不执行脚本）
from mpltweak import params as P              # noqa: E402


def run(rel, params_path):
    fp = os.path.join(H.CORPUS, rel.replace('/', os.sep))
    src, enc = P.read_text(fp)
    tree = ast.parse(src)
    data = P.load(params_path)
    fi = data.get('fig_index')
    print('=== %s   fig_index=%s  n_axes_in_params=%d  figsize_in=%s' % (
        rel, fi, len(data.get('axes') or []), data.get('figsize_in')))
    fig_calls = [n for n in ast.walk(tree)
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                 and n.func.attr in W.FIG_FACTORIES]
    fig_calls.sort(key=lambda n: (n.lineno, n.col_offset))
    print('FIG_FACTORIES =', W.FIG_FACTORIES)
    print('fig_calls at lines:', [n.lineno for n in fig_calls])
    print('_fig_span(%s) = %s' % (fi, W._fig_span(tree, fi if isinstance(fi, int) else 0)))
    calls = W._figure_axes_for_index(tree, fi if isinstance(fi, int) else 0)
    print('_figure_axes_for_index -> %s' % [(nm, c.lineno, c.func.attr) for nm, c in calls])
    new_src, covered, skipped = W.apply_inplace(src, tree, data)
    print('covered =', {k: sorted(v) for k, v in covered.items()})
    print('skipped =', json.dumps(skipped, ensure_ascii=False))
    print('in_src == src ?', new_src == src)
    # 逐轴映射细节
    print('axis_var_index =', W.axis_var_index(tree))
    mapp, cb = W.find_mappable_names(tree)
    print('mappables =', dict(list(mapp.items())[:8]), ' cb_args =', cb)
    hosts = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr == 'colorbar':
            for kw in node.keywords:
                if kw.arg == 'ax' and isinstance(kw.value, ast.Name):
                    hosts.add(kw.value.id)
    print('host_names =', hosts)
    for a in (data.get('axes') or []):
        print('  param ax%s pos=%s' % (a.get('index'), a.get('pos')))
    print('')


if __name__ == '__main__':
    run(sys.argv[1], sys.argv[2])
