# -*- coding: utf-8 -*-
"""语料静态诊断：add_axes / figsize 的写法分布，以及"提不出目标值"的原因归类。
不调用 mpltweak，不执行任何脚本。"""
import ast
import collections
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import l0_harness as H


def calls(text, attr):
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    out = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == attr:
            out.append(n)
    return out


def describe_arg(node):
    if node is None:
        return 'missing'
    if isinstance(node, (ast.List, ast.Tuple)):
        kinds = set(type(e).__name__ for e in node.elts)
        return '%s(len=%d,%s)' % (type(node).__name__, len(node.elts), ','.join(sorted(kinds)))
    return type(node).__name__


def main():
    rows = H.load_manifest()
    with open(H.MANIFEST, 'rb') as f:
        n_lines = f.read().count(b'\n') + 1
    print('manifest bytes-lines:', n_lines, ' parsed rows:', len(rows))
    paths = [r.get('path') for r in rows if (r.get('path') or '').endswith('.py')]
    print('manifest .py rows:', len(paths), ' unique:', len(set(paths)))
    disk = []
    for root, _d, files in os.walk(H.CORPUS):
        if os.sep + 'harness' in root:
            continue
        for fn in files:
            if fn.endswith('.py'):
                disk.append(os.path.relpath(os.path.join(root, fn), H.CORPUS).replace('\\', '/'))
    print('disk .py files:', len(disk), ' gallery:', sum(1 for p in disk if p.startswith('gallery')),
          ' zenodo:', sum(1 for p in disk if p.startswith('zenodo')))
    print('on disk but not in manifest:', len(set(disk) - set(paths)))
    print('in manifest but not on disk:', len(set(paths) - set(disk)))

    c = collections.Counter()
    aa_shapes = collections.Counter()
    fs_shapes = collections.Counter()
    other = collections.Counter()
    for r in rows:
        p = r.get('path') or ''
        if not p.endswith('.py'):
            continue
        fp = os.path.join(H.CORPUS, p.replace('/', os.sep))
        if not os.path.exists(fp):
            continue
        text, _ = H.decode_source(H.read_bytes(fp))
        for n in calls(text, 'add_axes'):
            if n.args:
                aa_shapes[describe_arg(n.args[0])] += 1
            else:
                aa_shapes['no_args'] += 1
            if n.args and isinstance(n.args[0], ast.Name):
                c['add_axes_var_arg'] += 1
        # figsize
        n_figsize_lit = 0
        for attr in ('figure', 'subplots'):
            for n in calls(text, attr):
                for kw in n.keywords:
                    if kw.arg == 'figsize':
                        c['figsize_kw'] += 1
                        if isinstance(kw.value, (ast.List, ast.Tuple)) and len(kw.value.elts) == 2 \
                                and all(isinstance(e, ast.Constant) for e in kw.value.elts):
                            c['figsize_kw_literal2'] += 1
                            n_figsize_lit += 1
                        else:
                            fs_shapes[type(kw.value).__name__] += 1
        for pat in ('add_subplot', 'subplot', 'subplots', 'subplot_mosaic', 'inset_axes',
                    'subplot2grid', 'GridSpec', 'gridspec', 'set_size_inches', 'tight_layout',
                    'constrained_layout', 'twinx', 'colorbar', 'add_gridspec', 'axes'):
            k = len(calls(text, pat))
            if k:
                other[pat] += k
        if n_figsize_lit == 0:
            c['no_figsize_literal'] += 1
    print('\n-- add_axes 第一个实参形态 --')
    for k, v in aa_shapes.most_common():
        print('  %-40s %d' % (k, v))
    print('\n-- figsize 表达式形态（非 2 字面量）--')
    for k, v in fs_shapes.most_common():
        print('  %-40s %d' % (k, v))
    print('\n-- 计数 --')
    for k, v in sorted(c.items()):
        print('  %-30s %d' % (k, v))
    print('\n-- 其它布局调用总频次 --')
    for k, v in other.most_common():
        print('  %-20s %d' % (k, v))


if __name__ == '__main__':
    main()
