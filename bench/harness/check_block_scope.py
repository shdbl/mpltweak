# -*- coding: utf-8 -*-
"""§9 用：插入的调整块在**落盘位置**能否解析名字（`fig` 是否在该作用域内已绑定）。

纯 AST；不执行脚本。这是"字节是否被改坏"的关键一环：语法没坏 ≠ 脚本还能跑。
"""
import ast
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import l0_harness as H

WORKF = os.path.join(H.HERE, 'work_final')
CASES = ['gallery/misc/logos2.py',
         'gallery/scikitlearn/classification/plot_classification_probability.py',
         'gallery/scikitlearn/model_selection/plot_successive_halving_heatmap.py',
         'zenodo/10635817__main.py', 'zenodo/17295616__correlation_plot.py',
         'zenodo/17295616__sla_contour.py', 'zenodo/18150811__correlation_plot.py',
         'zenodo/18508640__Zenodo_Santer_AGU_Advances_Matplotlib_Figure_03.py',
         'zenodo/20154193__Cylinder.py', 'zenodo/20154193__RealSpace.py',
         'zenodo/23013498__zndo_figs_pca.py', 'zenodo/23192393__figure_panels.py',
         'zenodo/5809269__O3_FYSP_paper.py']


def scopes(tree):
    """line -> 该行所处的（最内层）作用域节点。"""
    m = {}

    def walk(node, cur):
        for ch in ast.iter_child_nodes(node):
            if isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                m[id(ch)] = ch
                walk(ch, ch)
            else:
                walk(ch, cur)
    walk(tree, tree)
    return m


def scope_of(tree, line):
    """返回包含该行的最内层函数/类/模块节点。"""
    best = tree
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) \
                and n.lineno <= line <= (getattr(n, 'end_lineno', 10 ** 9) or 10 ** 9):
            if n.lineno >= getattr(best, 'lineno', 0):
                best = n
    return best


def bound_before(scope, line):
    """该作用域内、line 之前被绑定的名字（赋值/for/with/import/参数）。"""
    names = set()
    if isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef)):
        for a in list(scope.args.args) + list(scope.args.posonlyargs) + \
                list(scope.args.kwonlyargs):
            names.add(a.arg)
        if scope.args.vararg:
            names.add(scope.args.vararg.arg)
        if scope.args.kwarg:
            names.add(scope.args.kwarg.arg)
    for n in ast.walk(scope):
        for tgt in (getattr(n, 'targets', None) or []):
            for sub in ast.walk(tgt):
                if isinstance(sub, ast.Name) and n.lineno < line:
                    names.add(sub.id)
        if isinstance(n, (ast.For, ast.AsyncFor)) and n.lineno < line:
            for sub in ast.walk(n.target):
                if isinstance(sub, ast.Name):
                    names.add(sub.id)
        if isinstance(n, ast.withitem) and n.optional_vars is not None and n.lineno < line:
            for sub in ast.walk(n.optional_vars):
                if isinstance(sub, ast.Name):
                    names.add(sub.id)
        if isinstance(n, (ast.Import, ast.ImportFrom)) and n.lineno < line:
            if isinstance(n, ast.Import):
                for a in n.names:
                    names.add((a.asname or a.name).split('.')[0])
            else:
                for a in n.names:
                    names.add(a.asname or a.name)
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store) and n.lineno < line:
            names.add(n.id)
    return names


out = []
for p in CASES:
    stem = os.path.splitext(os.path.basename(p))[0]
    fp = os.path.join(WORKF, stem, 'directed.py')
    if not os.path.exists(fp):
        out.append('- %s：工作副本缺失' % p)
        continue
    text, _ = H.decode_source(H.read_bytes(fp))
    tree = ast.parse(text)
    lines = text.split('\n')
    marks = [i for i, ln in enumerate(lines) if H.BLOCK_SENTINEL in ln]
    i0 = marks[0]
    blk = '\n'.join(lines[i0:i0 + 12])
    uses_fig = 'fig.axes' in blk or 'fig.axes[' in blk
    uses_gcf = 'plt.gcf()' in blk
    sc = scope_of(tree, i0 + 1)
    kind = ('module' if isinstance(sc, ast.Module)
            else '%s %s (L%d-L%s)' % (type(sc).__name__, getattr(sc, 'name', '?'),
                                      sc.lineno, getattr(sc, 'end_lineno', '?')))
    names = bound_before(sc, i0 + 1)
    ok = None
    if uses_gcf:
        ok = 'n/a（用 plt.gcf()，不需要 fig 绑定；但依赖"当前图"是运行时状态）'
    elif uses_fig:
        ok = '有' if 'fig' in names else '**没有 → 该块在运行时 NameError**'
    out.append('- %s' % p)
    out.append('    块插入行 L%d，缩进 %d，作用域 %s' % (
        i0 + 1, len(lines[i0]) - len(lines[i0].lstrip()), kind))
    out.append('    引用 fig=%s / plt.gcf()=%s；`fig` 在该作用域此前是否已绑定：%s' % (
        uses_fig, uses_gcf, ok))
    # 该作用域里 fig 的绑定位置
    binds = [n.lineno for n in ast.walk(sc)
             if isinstance(n, (ast.Assign, ast.AnnAssign))
             and any(isinstance(s, ast.Name) and s.id == 'fig'
                     for t in (n.targets if isinstance(n, ast.Assign) else [n.target])
                     for s in ast.walk(t))]
    out.append('    作用域内 `fig = ...` 行号：%s（块在 L%d）' % (sorted(binds)[:6], i0 + 1))
with open(os.path.join(H.HERE, '_scope.txt'), 'w', encoding='utf-8') as f:
    f.write('\n'.join(out) + '\n')
print('ok')
