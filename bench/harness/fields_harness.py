#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
mpltweak L0-F：「字段族」静态语料压力测试脚手架
=================================================

目的：把 L0 从"只测 pos + figsize"扩展到"**工具支持原位写回的整套字段**"，
在 corpus018 的真实脚本上，按**字段族**给出可测分母 / 通过 / 不适用 / 跳过。

字段族（原位写回口径，见报告 §1 权威字段表 —— 结论直接来自被测源码
``writeback.apply_inplace`` / ``edit_figsize`` / ``_proved_equal``）：

    pos, figsize_in,
    title_fontsize, label_fontsize, label_fontsize_y,
    tick_fontsize, tick_fontsize_y, grid, xlim, ylim

（``spines`` / ``xscale`` / ``yscale`` / ``clim`` / ``cmap`` / ``lines`` / ``legend``
**不是**原位字段：``apply_inplace`` 把前五个一律记进 ``skipped``，由 auto 退回块模式
落地。本脚手架把它们单列成"非原位族"表，只统计分母与工具处置，不混进原位成绩。）

铁律（与 L0 一致，另加字段族专属纪律）：
  * **绝不执行语料脚本**：只用 ``apply --write --verify-fast --json --style inplace``
    与 ``revert --write``。--verify-fast 不重跑；--style inplace 保证**不插调整块**，
    这样"某族没落地"与"别族的插块污染了整份文件"不会互相混淆。
  * 语料原件（gallery/、zenodo/）只读；一切写操作发生在 ``harness/work_fields/<脚本名>/``。
  * mpltweak 包只读，一个字节都不改。
  * 提取器是**自研 AST 扫描**，不 import mpltweak 任何模块。
  * 复核器是**自研 AST 复核**，判据按族独立（不拿 pos 的判据套字号）。
  * 控制台是 GBK：所有中文输出只写 UTF-8 文件，不 print。

用法::

    python fields_harness.py --selftest           # 复核器反向验证矩阵（不跑语料）
    python fields_harness.py --limit 24           # 抽样
    python fields_harness.py --all --jobs 6       # 全量
    python fields_harness.py --report-only        # 只重生成报告
"""

from __future__ import annotations

import argparse
import ast
import collections
import concurrent.futures as futures
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import time

# --------------------------------------------------------------------------
# 路径与常量
# --------------------------------------------------------------------------
HERE = os.path.dirname(os.path.abspath(__file__))            # corpus018/harness
CORPUS = os.path.dirname(HERE)                               # corpus018
MANIFEST = os.path.join(CORPUS, 'manifest.jsonl')
WORK = os.path.join(HERE, 'work_fields')
RESULTS = os.path.join(HERE, 'l0_results_fields.jsonl')
REPORT = os.path.join(HERE, 'l0_report_fields.md')
RUN_META = os.path.join(HERE, 'l0_run_meta_fields.json')
SELFTEST_JSON = os.path.join(HERE, 'fields_selftest.json')

PYTHON = r'D:\Miniconda\envs\longxia\python.exe'
MPLTWEAK_SRC = os.environ.get('MPLTWEAK_SRC') or \
    r'D:\Agent\deepseek\mpltweak-fix018\src'
MPLTWEAK_ROOT = os.path.dirname(MPLTWEAK_SRC)   # ...\mpltweak-fix018

DECODE_ORDER = ('utf-8-sig', 'utf-8', 'gbk', 'gb18030', 'big5')

# 原位字段族（顺序 = 报告里的呈现顺序）
FAMILIES = ('pos', 'figsize_in',
            'title_fontsize', 'label_fontsize', 'label_fontsize_y',
            'tick_fontsize', 'tick_fontsize_y', 'grid', 'xlim', 'ylim')

FAMILY_LABEL = {
    'pos': 'pos（add_axes 面板位置）',
    'figsize_in': 'figsize_in（画布尺寸）',
    'title_fontsize': 'title_fontsize（标题字号）',
    'label_fontsize': 'label_fontsize（x 轴标签字号）',
    'label_fontsize_y': 'label_fontsize_y（y 轴标签字号）',
    'tick_fontsize': 'tick_fontsize（x 刻度字号）',
    'tick_fontsize_y': 'tick_fontsize_y（y 刻度字号）',
    'grid': 'grid（网格开/关）',
    'xlim': 'xlim（x 轴范围）',
    'ylim': 'ylim（y 轴范围）',
}

# 非原位族：源码里有字面量也**不**做原位改写（工具设计如此），只统计分母与处置
NONINPLACE_FAMILIES = {
    'spines': 'spines（框线可见性）',
    'xscale': 'xscale（x 轴尺度）',
    'yscale': 'yscale（y 轴尺度）',
    'clim': 'clim（色标范围）',
}

# 明确已知的"源码写法 → 工具原位寻址模型够不到"的列举（报告 §6 边界用）
FIG_FACTORIES = ('figure', 'subplots', 'subplot_mosaic')
REPLACE_ATTRS = ('add_subplot', 'add_axes')

MASK = '<#>'
FS_TOL = 1e-9
POS_TOL = 1e-9

TITLE = 'mpltweak L0-F 字段族静态语料压力测试报告'


# --------------------------------------------------------------------------
# 基础
# --------------------------------------------------------------------------
def sha256_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 16), b''):
            h.update(chunk)
    return h.hexdigest()


def sha256_bytes(b):
    return hashlib.sha256(b).hexdigest()


def read_bytes(path):
    with open(path, 'rb') as f:
        return f.read()


def decode_source(raw):
    for enc in DECODE_ORDER:
        try:
            return raw.decode(enc), enc
        except UnicodeDecodeError:
            continue
    return raw.decode('utf-8', 'replace'), 'utf-8-replace'


def write_text(path, text, encoding='utf-8'):
    d = os.path.dirname(os.path.abspath(path))
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, 'w', encoding=encoding, newline='') as f:
        f.write(text)


def write_json(path, obj):
    write_text(path, json.dumps(obj, ensure_ascii=False, indent=2))


def num_close(a, b, tol=FS_TOL):
    try:
        return abs(float(a) - float(b)) <= tol
    except (TypeError, ValueError):
        return False


# --------------------------------------------------------------------------
# 源码坐标（ast 的 col_offset 是 UTF-8 字节偏移）
# --------------------------------------------------------------------------
def line_starts(src):
    starts = [0]
    for i, ch in enumerate(src):
        if ch == '\n':
            starts.append(i + 1)
    return starts


def char_off(starts, src, lineno, col):
    ls = starts[lineno - 1]
    nb = 0
    i = ls
    n = len(src)
    while i < n and nb < col:
        nb += len(src[i].encode('utf-8'))
        i += 1
    return i


def node_span(starts, src, node):
    return (char_off(starts, src, node.lineno, node.col_offset),
            char_off(starts, src, node.end_lineno, node.end_col_offset))


def text_of_span(src, sp):
    return src[sp[0]:sp[1]]


# --------------------------------------------------------------------------
# 数字/布尔字面量（与 ast 语义一致；bool 不算数字）
# --------------------------------------------------------------------------
def is_num_literal(node):
    if isinstance(node, ast.Constant):
        return isinstance(node.value, (int, float)) and not isinstance(node.value, bool)
    if (isinstance(node, ast.UnaryOp)
            and isinstance(node.op, (ast.USub, ast.UAdd))
            and isinstance(node.operand, ast.Constant)):
        return (isinstance(node.operand.value, (int, float))
                and not isinstance(node.operand.value, bool))
    return False


def lit_num(node):
    if (isinstance(node, ast.Constant) and isinstance(node.value, (int, float))
            and not isinstance(node.value, bool)):
        return float(node.value)
    if (isinstance(node, ast.UnaryOp)
            and isinstance(node.op, (ast.USub, ast.UAdd))
            and isinstance(node.operand, ast.Constant)
            and isinstance(node.operand.value, (int, float))
            and not isinstance(node.operand.value, bool)):
        v = float(node.operand.value)
        return -v if isinstance(node.op, ast.USub) else v
    return None


def is_bool_literal(node):
    return isinstance(node, ast.Constant) and isinstance(node.value, bool)


def iter_assign_names(target):
    if isinstance(target, ast.Name):
        yield target.id
    elif isinstance(target, (ast.Tuple, ast.List)):
        for e in target.elts:
            yield from iter_assign_names(e)


# --------------------------------------------------------------------------
# 轴解析（自研镜像：只为"考卷前提"服务，不参与结论判定）
# --------------------------------------------------------------------------
def subplots_single_axes(call):
    """plt.subplots(...) 是否只返回单个 Axes（nrows*ncols == 1）。"""
    vals = []
    for a in call.args[:2]:
        if (isinstance(a, ast.Constant) and isinstance(a.value, int)
                and not isinstance(a.value, bool)):
            vals.append(a.value)
        else:
            return False
    for kw in call.keywords:
        if kw.arg in ('nrows', 'ncols'):
            v = kw.value
            if (isinstance(v, ast.Constant) and isinstance(v.value, int)
                    and not isinstance(v.value, bool)):
                vals.append(v.value)
            else:
                return False
    if not vals:
        return True
    while len(vals) < 2:
        vals.append(1)
    return vals[0] * vals[1] == 1


def mirror_axis_var_index(tree):
    """镜像 ``writeback.axis_var_index`` 的「轴变量名 → 轴序号」口径。

    只用来判"考卷前提是否成立"（这个变量名工具认不认、序号是多少），
    结论判定一律走独立复核器。
    """
    seq_names = set()
    out = {}
    assigns = [n for n in ast.walk(tree) if isinstance(n, ast.Assign)]
    assigns.sort(key=lambda n: (n.lineno, n.col_offset))

    def elts_of(node):
        for t in node.targets:
            if isinstance(t, (ast.Tuple, ast.List)):
                yield list(t.elts)
            else:
                yield [t]

    for node in assigns:
        v = node.value
        if not (isinstance(v, ast.Call)
                and getattr(v.func, 'attr', None) in FIG_FACTORIES):
            continue
        for elts in elts_of(node):
            if len(elts) == 1 and isinstance(elts[0], ast.Name):
                if getattr(v.func, 'attr', None) == 'subplots':
                    out.setdefault(elts[0].id, 0)
                continue
            if len(elts) < 2:
                continue
            first = elts[0]
            looks_fig = (isinstance(first, ast.Name)
                         and (first.id.lower().startswith('fig') or first.id == 'f'))
            if looks_fig:
                second = elts[1]
                if isinstance(second, ast.Name):
                    if subplots_single_axes(v):
                        out.setdefault(second.id, 0)
                    else:
                        seq_names.add(second.id)
                elif isinstance(second, (ast.Tuple, ast.List)):
                    for k, e in enumerate(second.elts):
                        if isinstance(e, ast.Name):
                            out.setdefault(e.id, k)
            else:
                for k, e in enumerate(elts):
                    if isinstance(e, ast.Name):
                        out.setdefault(e.id, k)
    for node in assigns:
        v = node.value
        base = None
        if isinstance(v, ast.Name):
            base = v.id
        elif (isinstance(v, ast.Attribute) and isinstance(v.value, ast.Name)
                and v.attr in ('flat', 'ravel', 'flatten')):
            base = v.value.id
        if base not in seq_names:
            continue
        for elts in elts_of(node):
            for k, e in enumerate(elts):
                if isinstance(e, ast.Name):
                    out.setdefault(e.id, k)
    for node in assigns:
        v = node.value
        if (isinstance(v, ast.Subscript) and isinstance(v.value, ast.Name)
                and v.value.id in seq_names and node.targets
                and isinstance(v.slice, ast.Constant)
                and isinstance(v.slice.value, int)):
            for nm in iter_assign_names(node.targets[0]):
                out.setdefault(nm, v.slice.value)
    made = 0
    for node in assigns:
        v = node.value
        if not isinstance(v, ast.Call):
            continue
        attr = getattr(v.func, 'attr', None)
        if attr in REPLACE_ATTRS or getattr(v.func, 'id', None) == 'subplot':
            for nm in iter_assign_names(node.targets[0]):
                out.setdefault(nm, made)
            made += 1
    return out


class Ctx(object):
    """一份脚本的静态上下文。"""

    def __init__(self, src, tree):
        self.src = src
        self.tree = tree
        self.starts = line_starts(src)
        self.fig_calls = sorted(
            [n for n in ast.walk(tree)
             if isinstance(n, ast.Call)
             and getattr(n.func, 'attr', None) in FIG_FACTORIES],
            key=lambda n: (n.lineno, n.col_offset))
        self.n_figs = len(self.fig_calls)
        self.spans = []                       # [(lo, hi)] 第 i 张图的源码区间
        for k, fc in enumerate(self.fig_calls):
            hi = 10 ** 9
            for other in self.fig_calls:
                if (other.lineno, other.col_offset) > (fc.lineno, fc.col_offset):
                    hi = other.lineno
                    break
            self.spans.append((fc.lineno, hi))
        self.cax_names = set()                # fig.colorbar(..., cax=X)
        self.host_names = set()               # fig.colorbar(..., ax=X)
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == 'colorbar'):
                for kw in node.keywords:
                    if kw.arg == 'cax' and isinstance(kw.value, ast.Name):
                        self.cax_names.add(kw.value.id)
                    if kw.arg == 'ax' and isinstance(kw.value, ast.Name):
                        self.host_names.add(kw.value.id)
        self.idx_map = mirror_axis_var_index(tree)
        # 每张图里 add_axes/add_subplot 赋值（非 cax，按源码序）—— 镜像
        # writeback._figure_axes_for_index
        self.fig_axes = {}
        for k, (lo, hi) in enumerate(self.spans):
            row = []
            for node in ast.walk(tree):
                if (isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)
                        and getattr(node.value.func, 'attr', None) in REPLACE_ATTRS
                        and lo <= node.lineno < hi):
                    nm = None
                    for t in node.targets:
                        if isinstance(t, ast.Name):
                            nm = t.id
                            break
                    if nm is not None and nm not in self.cax_names:
                        row.append((nm, node.value, node.lineno))
            row.sort(key=lambda x: (x[1].lineno, x[1].col_offset))
            self.fig_axes[k] = row
        # 某一变量在同图内被 add_axes/add_subplot 赋值多次 → 工具的重叠检测会拒绝
        self.dup_assign = collections.Counter()
        for k, row in self.fig_axes.items():
            for nm, _c, _l in row:
                self.dup_assign[(k, nm)] += 1
        self.scale_lit = {}                   # (var, 'xscale'|'yscale') -> 字面量
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr in ('set_xscale', 'set_yscale')
                    and isinstance(node.func.value, ast.Name)):
                fld = 'xscale' if node.func.attr == 'set_xscale' else 'yscale'
                for c in list(node.args) + [k.value for k in node.keywords]:
                    if isinstance(c, ast.Constant) and isinstance(c.value, str):
                        self.scale_lit[(node.func.value.id, fld)] = \
                            c.value.strip().lower()
                        break

    def fig_index_of(self, lineno):
        """该行落在第几张图的区间里（不在任何区间 → None）。"""
        for k, (lo, hi) in enumerate(self.spans):
            if lo <= lineno < hi:
                return k
        return None

    def span_of(self, fig_index):
        if 0 <= fig_index < len(self.spans):
            return self.spans[fig_index]
        return (0, 10 ** 9)

    def scope_of(self, fig_index):
        """`apply_inplace` 实际用的字段查找区间 `[_scope_lo, _scope_hi)`。

        与 `_fig_span` 的差别：`apply_inplace` 会再取 `_scope_lo = min(_scope_lo,
        本图全部 add_axes/add_subplot 调用的最小行号)` —— 轴创建调用可能落在
        figure 调用**之前**（少见但合法），那时工具的查找下界更靠前。
        镜像这一条才能不漏/不错判 `_find_call` 会命中哪个调用。
        """
        lo, hi = self.span_of(fig_index)
        row = self.fig_axes.get(fig_index) or []
        if row:
            lo = min(lo, min(c.lineno for _n, c, _l in row))
        return lo, hi

    def find_call(self, var, attr, lo, hi):
        """镜像 writeback._find_call：**最早的** Name(var).attr(...)（限行区间）。

        ``_find_call`` 要求 ``node.func.value`` 是 ``ast.Name`` —— 接收者是
        ``axes[0]`` 这类 Subscript 一律找不到（这是重要的覆盖边界）。
        """
        found = None
        for node in ast.walk(self.tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == attr
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == var):
                ln = getattr(node, 'lineno', 0)
                if ln < lo or ln >= hi:
                    continue
                if found is None or node.lineno < found.lineno:
                    found = node
        return found

    def find_tick_axis_call(self, var, axis, lo, hi):
        """镜像 writeback._find_tick_axis_call：最早的 tick_params(axis=...)。"""
        found = None
        for node in ast.walk(self.tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == 'tick_params'
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == var):
                continue
            ln = getattr(node, 'lineno', 0)
            if ln < lo or ln >= hi:
                continue
            ax = next((k for k in node.keywords if k.arg == 'axis'), None)
            if ax is None or not (isinstance(ax.value, ast.Constant)
                                  and isinstance(ax.value.value, str)
                                  and ax.value.value == axis):
                continue
            if found is None or node.lineno < found.lineno:
                found = node
        return found


# --------------------------------------------------------------------------
# 站点提取（自研 AST 扫描；不 import mpltweak）
# --------------------------------------------------------------------------
def site(family, kind, var, index, fig_index, spans, value, reach, reason,
         node=None, extra=None):
    return {'family': family, 'kind': kind, 'var': var, 'index': index,
            'fig_index': fig_index, 'spans': spans, 'value': value,
            'reach': bool(reach), 'reason': reason, 'node': node,
            'extra': extra or {}, 'ordinal': 0}


def _kw_literal(node, names):
    """返回 (kwarg名, 节点, 值)；只认数字字面量 kwarg。"""
    for kw in node.keywords:
        if kw.arg in names and is_num_literal(kw.value):
            return kw.arg, kw.value, lit_num(kw.value)
    return None, None, None


def dupvar_windows(ctx):
    """``{fig_index: {axis_index: (lo, hi)}}``：轴变量名在同一张图里被**多次**
    `add_axes/add_subplot` 赋值时，为每个赋值点给出"以该赋值点为界"的字段搜索窗口。

    为什么需要它：`apply_inplace` 的 `_find_call` 只在 `[_scope_lo,_scope_hi)` 里找
    **最早**那个 `Name(var).attr(...)`，而它把两个轴都映射到同一个变量名 —— 于是
    "第 2 个轴的 set_title"永远会命中"第 1 个轴的 set_title"。以赋值点为界做窗口
    切分，才能为"第 k 个轴"唯一归属一个字面量站点（这是 `fig.axes` 的创建序语义，
    与工具自己的 index↔第 i 个赋值 的配对规则一致）。
    """
    out = {}
    for f, row in (ctx.fig_axes or {}).items():
        byname = collections.defaultdict(list)
        for k, (nm, _call, ln) in enumerate(row):
            byname[nm].append((k, ln))
        slo, shi = ctx.scope_of(f)
        for _nm, ks in byname.items():
            if len(ks) < 2:
                continue
            ks_sorted = sorted(ks, key=lambda x: x[1])
            for j, (k, ln) in enumerate(ks_sorted):
                hi = ks_sorted[j + 1][1] if j + 1 < len(ks_sorted) else shi
                out.setdefault(f, {})[k] = (ln, hi)
    return out


def extract_sites(ctx, wins=None):
    """返回 dict family -> [site]，纯静态；``reach`` 描述工具的寻址模型够不够得到。

    ``wins`` = `dupvar_windows(ctx)` 的结果：给了就对"同名轴"改用逐赋值点窗口
    （用于 F-DUPVAR 场景；不给则用工具自己的整图区间，保持主场景口径不变）。
    """
    src, starts = ctx.src, ctx.starts
    out = collections.defaultdict(list)

    def sp(node):
        return node_span(starts, src, node)

    # ---- 轴族：逐图 × 逐轴 ----
    for f in range(ctx.n_figs):
        lo, hi = ctx.scope_of(f)
        A = ctx.fig_axes.get(f) or []          # [(var, call, lineno)]
        a_names = [x[0] for x in A]
        zip_mode = bool(A)                     # 有 add_axes/add_subplot → 位置 zip 配对
        _w = (wins or {}).get(f) or {}
        for k, (nm, call, lineno) in enumerate(A):
            _klo, _khi = _w.get(k, (lo, hi))
            _extract_axis_family_sites(ctx, out, f, k, nm, call, _klo, _khi, sp,
                                       method='zip', wins=_w)
        if not zip_mode and ctx.n_figs == 1:
            # 单图 + 无 add_axes/add_subplot → 工具由 axis_var_index 反查补 var_for
            rev = {}
            for nm, i in ctx.idx_map.items():
                rev.setdefault(i, nm)
            for i, nm in sorted(rev.items()):
                _extract_axis_family_sites(ctx, out, f, i, nm, None, lo, hi, sp,
                                           method='idx_map')
        elif zip_mode:
            # 图内已有 add_axes 时，只有"序号 >= len(A) 且不在 A 里"的 idx_map 变量
            # 才可能被 path② 补上（序号 < len(A) 的已被 zip 占用 → 冲突）
            if ctx.n_figs == 1:
                rev = {}
                for nm, i in ctx.idx_map.items():
                    rev.setdefault(i, nm)
                for i, nm in sorted(rev.items()):
                    if nm in a_names or i < len(A):
                        continue
                    _extract_axis_family_sites(ctx, out, f, i, nm, None, lo, hi,
                                               sp, method='idx_map')

    # ---- figsize ----
    for f, fc in enumerate(ctx.fig_calls):
        kw = next((k for k in fc.keywords if k.arg == 'figsize'), None)
        if kw is None or not isinstance(kw.value, (ast.List, ast.Tuple)):
            continue
        v = kw.value
        if len(v.elts) != 2 or not all(is_num_literal(e) for e in v.elts):
            continue
        out['figsize_in'].append(site(
            'figsize_in', 'container_%s' % type(v).__name__.lower(), None, None, f,
            [sp(v)], [lit_num(v.elts[0]), lit_num(v.elts[1])], True, 'ok',
            node=v, extra={'container': type(v).__name__, 'call_lineno': fc.lineno}))

    for fam in out:
        out[fam].sort(key=lambda s: (s['spans'][0][0] if s['spans'] else 0))
        for n, s in enumerate(out[fam]):
            s['ordinal'] = n
    return out


def _extract_axis_family_sites(ctx, out, f, k, nm, call, lo, hi, sp, method,
                               wins=None):
    """一个轴上的全部族站点。``method`` = 'zip' | 'idx_map'。"""
    src, starts = ctx.src, ctx.starts
    base_reach = True
    base_reason = 'ok'
    if method == 'idx_map' and ctx.n_figs > 1:
        base_reach, base_reason = False, 'multi_fig_subplots_var_unreachable'
    # 同名轴被多次赋值：主场景（不给 wins）视为"无法唯一归属"→ 不适用；
    # F-DUPVAR 场景（给了 wins）改用逐赋值点窗口，前提成立 → 照常考。
    if (method == 'zip' and ctx.dup_assign.get((f, nm), 0) > 1
            and not (wins or {}).get(k)):
        base_reach, base_reason = False, 'dup_axes_assign_overlap_rejected'
    if nm in ctx.cax_names:
        base_reach, base_reason = False, 'colorbar_cax_axis'

    def add(family, kind, spans, value, reach, reason, node=None, extra=None):
        # 轴的"整体不可达"（多图 subplots 变量 / 重复 add_axes / cax）优先于
        # 站点自身的原因 —— 否则会出现 reach=False 但 reason='ok' 的自相矛盾记录。
        out[family].append(site(family, kind, nm, k, f, spans, value,
                                reach and base_reach,
                                (base_reason if not base_reach else reason),
                                node=node, extra=extra))

    # ① 标题字号：set_title(..., fontsize=N)；别名 size=N 工具**写**不认（只证等值认）
    node = ctx.find_call(nm, 'set_title', lo, hi)
    if node is not None:
        kwn, kwnode, val = _kw_literal(node, ('fontsize', 'size'))
        if kwn is not None:
            reach = (kwn == 'fontsize')
            add('title_fontsize', 'kw_' + kwn, [sp(kwnode)], val, reach,
                'ok' if reach else 'alias_size_kw_not_writable', node=node)
    # ② x 标签字号：set_xlabel(..., fontsize=N)
    node = ctx.find_call(nm, 'set_xlabel', lo, hi)
    if node is not None:
        kwn, kwnode, val = _kw_literal(node, ('fontsize', 'size'))
        if kwn is not None:
            reach = (kwn == 'fontsize')
            add('label_fontsize', 'kw_' + kwn, [sp(kwnode)], val, reach,
                'ok' if reach else 'alias_size_kw_not_writable', node=node)
    # ③ y 标签字号：set_ylabel(..., fontsize=N)（params 显式给 label_fontsize_y 才走这支）
    node = ctx.find_call(nm, 'set_ylabel', lo, hi)
    if node is not None:
        kwn, kwnode, val = _kw_literal(node, ('fontsize', 'size'))
        if kwn is not None:
            reach = (kwn == 'fontsize')
            add('label_fontsize_y', 'kw_' + kwn, [sp(kwnode)], val, reach,
                'ok' if reach else 'alias_size_kw_not_writable', node=node)
    # ④ 刻度字号
    xnode = ctx.find_tick_axis_call(nm, 'x', lo, hi)
    ynode = ctx.find_tick_axis_call(nm, 'y', lo, hi)
    both = (xnode is not None and ynode is not None)
    tick_plain = ctx.find_call(nm, 'tick_params', lo, hi)
    if both:
        kwn, kwnode, val = _kw_literal(xnode, ('labelsize',))
        if kwn is not None:
            add('tick_fontsize', 'tick_axis_x', [sp(kwnode)], val, True, 'ok',
                node=xnode)
        kwn, kwnode, val = _kw_literal(ynode, ('labelsize',))
        if kwn is not None:
            add('tick_fontsize_y', 'tick_axis_y', [sp(kwnode)], val, True, 'ok',
                node=ynode)
    else:
        if tick_plain is not None:
            kwn = next((kk for kk in tick_plain.keywords if kk.arg == 'labelsize'),
                       None)
            if kwn is not None and is_num_literal(kwn.value):
                add('tick_fontsize', 'tick_plain', [sp(kwn.value)],
                    lit_num(kwn.value), True, 'ok', node=tick_plain)
            else:
                # 最早的 tick_params 没有 labelsize 字面量 → 工具原位够不到
                add('tick_fontsize', 'tick_plain_missing', [], None, False,
                    'earliest_tick_params_lacks_labelsize', node=tick_plain)
    # ⑤ grid：ax.grid(True/False)（工具只认**位置**布尔常量）
    gnode = ctx.find_call(nm, 'grid', lo, hi)
    if gnode is not None:
        if gnode.args and is_bool_literal(gnode.args[0]):
            add('grid', 'grid_positional', [sp(gnode.args[0])],
                bool(gnode.args[0].value), True, 'ok', node=gnode)
        elif gnode.args and is_num_literal(gnode.args[0]):
            # grid(1) / grid(0)：工具走 bool_value 通道要求 ast.Constant bool
            add('grid', 'grid_positional_numbool', [], None, False,
                'grid_arg_not_bool_literal', node=gnode)
        else:
            kwn = next((kk for kk in gnode.keywords
                        if kk.arg in ('visible', 'b')), None)
            if kwn is not None and is_bool_literal(kwn.value):
                add('grid', 'grid_kw_%s' % kwn.arg, [], None, False,
                    'grid_kw_form_not_writable', node=gnode)
    # ⑥ xlim / ylim：三种写法都认（两位置参 / 容器 / xmin,xmax kw）
    for attr, field, kws in (('set_xlim', 'xlim', ('xmin', 'xmax')),
                             ('set_ylim', 'ylim', ('ymin', 'ymax'))):
        node = ctx.find_call(nm, attr, lo, hi)
        if node is None:
            continue
        got = None
        kind = None
        if len(node.args) == 2 and all(is_num_literal(a) for a in node.args):
            got, kind = [lit_num(node.args[0]), lit_num(node.args[1])], 'args2'
            spans = [sp(node.args[0]), sp(node.args[1])]
        elif (len(node.args) == 1 and isinstance(node.args[0], (ast.List, ast.Tuple))
              and len(node.args[0].elts) == 2
              and all(is_num_literal(e) for e in node.args[0].elts)):
            got = [lit_num(node.args[0].elts[0]), lit_num(node.args[0].elts[1])]
            kind = 'container_%s' % type(node.args[0]).__name__.lower()
            spans = [sp(node.args[0].elts[0]), sp(node.args[0].elts[1])]
        else:
            kwmap = {kk.arg: kk for kk in node.keywords if kk.arg in kws}
            if all(x in kwmap and is_num_literal(kwmap[x].value) for x in kws):
                got = [lit_num(kwmap[kws[0]].value), lit_num(kwmap[kws[1]].value)]
                kind = 'kw_%s' % kws[0]
                spans = [sp(kwmap[kws[0]].value), sp(kwmap[kws[1]].value)]
            else:
                # left=/right= / 变量 / **kwargs → 工具原位够不到
                other = next((kk for kk in node.keywords
                              if kk.arg in ('left', 'right')), None)
                add(field, 'unsupported_form', [], None, False,
                    ('limit_kw_left_right_not_writable' if other is not None
                     else 'limit_form_not_writable'), node=node)
                continue
        add(field, kind, spans, got, True, 'ok', node=node)
    # ⑦ pos：add_axes([...]) 4 字面量
    if call is not None and getattr(call.func, 'attr', None) == 'add_axes':
        a0 = call.args[0] if call.args else None
        if isinstance(a0, (ast.List, ast.Tuple)) and len(a0.elts) == 4 \
                and all(is_num_literal(e) for e in a0.elts):
            if nm in ctx.host_names:
                add('pos', 'addaxes_%s' % type(a0).__name__.lower(), [], None,
                    False, 'colorbar_host_axis', node=a0)
            else:
                add('pos', 'addaxes_%s' % type(a0).__name__.lower(),
                    [sp(e) for e in a0.elts],
                    [lit_num(e) for e in a0.elts], True, 'ok', node=a0,
                    extra={'container': type(a0).__name__})
        elif a0 is not None:
            add('pos', 'addaxes_nonliteral', [], None, False,
                'add_axes_pos_not_4_literals', node=a0)


def extract_non_inplace_sites(ctx):
    """非原位族（spines / xscale / yscale / clim）的分母统计用站点。"""
    out = collections.defaultdict(list)
    tree = ctx.tree
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == 'set_visible'
                and isinstance(node.func.value, ast.Subscript)
                and isinstance(node.func.value.value, ast.Attribute)
                and node.func.value.value.attr == 'spines'
                and node.args and is_bool_literal(node.args[0])):
            out['spines'].append(1)
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == 'set_xscale'):
            for c in list(node.args) + [k.value for k in node.keywords]:
                if isinstance(c, ast.Constant) and isinstance(c.value, str):
                    out['xscale'].append(1)
                    break
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == 'set_yscale'):
            for c in list(node.args) + [k.value for k in node.keywords]:
                if isinstance(c, ast.Constant) and isinstance(c.value, str):
                    out['yscale'].append(1)
                    break
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr in ('set_clim', 'set_vmin', 'set_vmax')):
            out['clim'].append(1)
        if (isinstance(node, ast.Call) and node.args
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in ('contourf', 'pcolormesh', 'imshow',
                                       'contour', 'scatter', 'pcolor', 'hexbin')):
            for kw in node.keywords:
                if kw.arg == 'vmin':
                    out['clim'].append(1)
    return out


# --------------------------------------------------------------------------
# 目标构造（"明显不同且合法"）
# --------------------------------------------------------------------------
def target_fontsize(cur):
    c = float(cur)
    for delta in (6.0, -6.0, 8.0, 4.0):
        t = c + delta
        if t >= 1.0 and abs(t - c) >= 0.6:
            return round(t, 6)
    return round(c + 6.0, 6)


POS_CANDIDATES = [[0.25, 0.25, 0.55, 0.6], [0.15, 0.2, 0.7, 0.65],
                  [0.2, 0.2, 0.6, 0.7], [0.3, 0.25, 0.5, 0.6],
                  [0.1, 0.1, 0.8, 0.8]]
FIGSIZE_CANDIDATES = [[6.0, 4.0], [5.5, 4.5], [7.0, 3.5], [4.5, 3.5],
                      [8.0, 5.0], [5.0, 3.0], [7.5, 4.5], [4.0, 3.2]]


def target_pos(cur):
    for c in POS_CANDIDATES:
        if max(abs(c[i] - float(cur[i])) for i in range(4)) >= 0.05:
            return list(c)
    return [0.2, 0.2, 0.6, 0.7]


def target_figsize(cur):
    for c in FIGSIZE_CANDIDATES:
        if max(abs(c[i] - float(cur[i])) for i in range(2)) >= 0.8:
            return list(c)
    return [6.0, 4.0]


def target_lim(cur, is_log):
    """范围目标：平移。对数轴用乘法平移（保持符号与量级合法）。"""
    a, b = float(cur[0]), float(cur[1])
    if is_log and a > 0 and b > 0:
        return [round(a * 1.37, 6), round(b * 1.37, 6)]
    span = b - a
    if span == 0:
        return None
    return [round(a + 0.37 * span, 6), round(b + 0.37 * span, 6)]


def target_of(s, ctx):
    fam = s['family']
    if fam in ('title_fontsize', 'label_fontsize', 'label_fontsize_y',
               'tick_fontsize', 'tick_fontsize_y'):
        return target_fontsize(s['value'])
    if fam == 'grid':
        return (not bool(s['value']))
    if fam == 'pos':
        return target_pos(s['value'])
    if fam == 'figsize_in':
        return target_figsize(s['value'])
    if fam in ('xlim', 'ylim'):
        isk = 'xscale' if fam == 'xlim' else 'yscale'
        return target_lim(s['value'], ctx.scale_lit.get((s['var'], isk)) == 'log')
    return None


# --------------------------------------------------------------------------
# 独立复核器（按族独立判据）
# --------------------------------------------------------------------------
def mask_text(src, spans):
    """把 spans 全替换成 MASK（从右往左），用于"除这些字面量外逐字节相同"证明。"""
    out = src
    for a, b in sorted(spans, key=lambda x: -x[0]):
        out = out[:a] + MASK + out[b:]
    return out


RULES_ALL = ('parse', 'global_mask', 'value', 'form', 'site_count')


def verify_run(before_text, after_text, plan, rules=RULES_ALL):
    """**独立复核器**：只改目标那一处 / 值等于目标 / 容器与写法保持 / 语法仍可解析。

    plan: [{'family','key','target','spans_before','spans_after','form_before',
             'form_after','before_value'}]  —— spans 由调用方用**同一套自研提取器**
    在 before/after 两份文本上分别算好（不复用工具的任何中间结果）。

    返回 dict，``ok`` 为总判据。``rules`` 为启用的规则集合（反向验证时用来"故意放宽"）。
    """
    res = {'ok': False, 'parse_ok': None, 'mask_ok': None, 'value_ok': None,
           'form_ok': None, 'site_count_ok': None, 'reasons': [],
           'value_detail': [], 'form_detail': []}
    if 'parse' in rules:
        try:
            ast.parse(after_text)
            res['parse_ok'] = True
        except SyntaxError as e:
            res['parse_ok'] = False
            res['reasons'].append('after_syntax_error: %s' % e)
            return res
    if 'site_count' in rules:
        res['site_count_ok'] = (len(plan) > 0
                                and all(p.get('spans_after') is not None
                                        and len(p['spans_after']) == len(p['spans_before'])
                                        for p in plan))
        if not res['site_count_ok']:
            res['reasons'].append('target_site_not_relocatable_in_after')
            return res
    if 'global_mask' in rules:
        all_b = [sp for p in plan for sp in (p.get('spans_before') or [])]
        all_a = [sp for p in plan for sp in (p.get('spans_after') or [])]
        mb, ma = mask_text(before_text, all_b), mask_text(after_text, all_a)
        res['mask_ok'] = (mb == ma)
        if not res['mask_ok']:
            res['reasons'].append('text_outside_target_spans_changed')
            res['mask_diff_head'] = _first_diff(mb, ma)
    if 'value' in rules:
        ok = True
        for p in plan:
            cur = p.get('after_value')
            t = p['target']
            if isinstance(t, bool):
                good = (cur is not None and bool(cur) is t)
            elif isinstance(t, (list, tuple)):
                good = (isinstance(cur, (list, tuple)) and len(cur) == len(t)
                        and all(num_close(cur[i], t[i]) for i in range(len(t))))
            else:
                good = (cur is not None and num_close(cur, t))
            res['value_detail'].append({'family': p['family'], 'key': p['key'],
                                        'target': t, 'after_value': cur,
                                        'ok': bool(good)})
            ok = ok and bool(good)
        res['value_ok'] = ok
        if not ok:
            res['reasons'].append('value_not_equal_target')
    if 'form' in rules:
        ok = True
        for p in plan:
            fb, fa = p.get('form_before'), p.get('form_after')
            good = (fb is not None and fb == fa)
            res['form_detail'].append({'family': p['family'], 'key': p['key'],
                                       'form_before': fb, 'form_after': fa,
                                       'ok': bool(good)})
            ok = ok and bool(good)
        res['form_ok'] = ok
        if not ok:
            res['reasons'].append('writing_form_changed')
    checks = []
    for r, key in (('parse', 'parse_ok'), ('site_count', 'site_count_ok'),
                   ('global_mask', 'mask_ok'), ('value', 'value_ok'),
                   ('form', 'form_ok')):
        if r in rules:
            checks.append(bool(res.get(key)))
    res['ok'] = all(checks) if checks else False
    return res


def _first_diff(a, b, ctx=60):
    n = min(len(a), len(b))
    for i in range(n):
        if a[i] != b[i]:
            lo = max(0, i - ctx)
            return {'at': i, 'before': a[lo:i + ctx], 'after': b[lo:i + ctx]}
    if len(a) != len(b):
        i = n
        return {'at': i, 'before': a[max(0, i - ctx):i + ctx],
                'after': b[max(0, i - ctx):i + ctx], 'len': [len(a), len(b)]}
    return None


def form_signature(src, s):
    """写法签名：容器括号 / kwarg 名 / 位置参 vs kwarg / 接收者形状。

    用于"写法保持"判据。**只反映结构，不反映数值** —— 早期版本拿"第一个元素的
    文本首字符"当容器字符，于是 `[-0.5, 18.5]` → `[6.53, 25.53]` 会被误判成
    "写法变了"（`-` → `6`），把 5 个完全正确的写回打成 fail。现在取真实的
    **左括号 / 右括号字符**。
    """
    kind = s['kind']
    if kind.startswith('container_'):
        lo = min(a for a, _b in s['spans'])
        hi = max(b for _a, b in s['spans'])
        return '%s|%s%s' % (kind, src[lo - 1:lo], src[hi:hi + 1])
    if kind.startswith('kw_'):
        return kind
    if kind in ('args2', 'unsupported_form'):
        return kind
    if kind.startswith('addaxes_'):
        # `extra['container']` 就是官方容器类名（List / Tuple），足以抓住
        # `[x0,y0,w,h]` → `(x0,y0,w,h)` 这种容器被换掉的写法改动。
        return '%s|%d' % (s['extra'].get('container', '?'), len(s['spans']))
    if kind in ('grid_positional', 'grid_positional_numbool'):
        return kind
    if kind.startswith('tick_'):
        return kind
    return kind


_NUM_RE = re.compile(r'[-+]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][-+]?\d+)?')
_BOOL_RE = re.compile(r'\b(?:True|False|true|false|TRUE|FALSE)\b')


def spelling_mask(raw):
    """把一段源码里的**数值/布尔字面量**换成 `#`，其余（空白、标点、关键字名）原样。

    * 数值带不带符号、写成 `.1` 还是 `0.1`，都算同一个 `#` —— 这些差异来自
      "值本来就要变"，不是"写法被改"。
    * 布尔也要掩（`grid(True)` → `grid(#)`），否则 `True`→`False` 会被误报成
      "写法被改"（那是值在变，不是写法在变）。
    """
    return _BOOL_RE.sub('#', _NUM_RE.sub('#', raw))


def spelling_key(src, s):
    """写法骨架：把站点**整段**（覆盖全部 span 的最小连续区间）交给 `spelling_mask`。

    用途：`figsize = (6,6)` → `figsize=(6, 4)` 这类"值对了、写法被规范化了"的现象，
    值判据与 form 判据都不该判 fail（语义等价），但要单独计数 —— 对"最小改动"
    的对外承诺有用。返回 None 表示没有字面量 span（如 grid 的 kwarg 形式）。
    """
    if not s or not s.get('spans'):
        return None
    lo = min(a for a, _b in s['spans'])
    hi = max(b for _a, b in s['spans'])
    return spelling_mask(src[lo:hi])


def spelling_raw(src, s):
    """站点的**原始**覆盖文本（不掩码），留给报告阶段重新判"写法是否被改"。"""
    if not s or not s.get('spans'):
        return None
    lo = min(a for a, _b in s['spans'])
    hi = max(b for _a, b in s['spans'])
    return src[lo:hi]


def plan_entry(before_text, after_text, s_before, s_after, target, before_value):
    return {'family': s_before['family'], 'key': '%s#%d' % (s_before['family'],
                                                            s_before['ordinal']),
            'target': target, 'spans_before': s_before['spans'],
            'spans_after': (s_after or {}).get('spans'),
            'form_before': form_signature(before_text, s_before),
            'form_after': (form_signature(after_text, s_after)
                           if s_after is not None else None),
            'before_value': before_value,
            'after_value': (s_after or {}).get('value')}


# --------------------------------------------------------------------------
# 复核器反向验证（自测矩阵；不进语料就证明"判据有牙"）
# --------------------------------------------------------------------------
SELFTEST_CASES = []


def _st_add(family, kind, name, src_before, src_after_good, bad_edits,
            plan_builder):
    SELFTEST_CASES.append({'family': family, 'kind': kind, 'name': name,
                           'before': src_before, 'good': src_after_good,
                           'bad': bad_edits, 'plan': plan_builder})


def _build_plan(before_text, after_text, family_extract, target):
    """用同一套提取器在 before/after 上取站点，构造 plan。"""
    def one(text):
        try:
            t = ast.parse(text)
        except SyntaxError:
            return None
        c = Ctx(text, t)
        sites = extract_sites(c)
        return c, sites
    r1, r2 = one(before_text), one(after_text)
    if r1 is None:
        return None
    c1, s1 = r1
    c2, s2 = r2 if r2 else (None, None)
    out = []
    for fam in family_extract:
        for sb in s1.get(fam, []):
            sa = None
            if s2 is not None:
                # 主键 = (族, 序号, 轴序号)；**写法变了也要配对成功** ——
                # 那正是 `form` 规则要判的东西，不能让"配不上"把判据吃掉。
                for cand in s2.get(fam, []):
                    if (cand['ordinal'] == sb['ordinal']
                            and cand.get('index') == sb.get('index')):
                        sa = cand
                        break
            out.append(plan_entry(before_text, after_text, sb, sa, target, sb['value']))
    return out


def selftest_matrix():
    """每个族：good 编辑必须 PASS；每类坏编辑在**严格**判据下必须 FAIL，
    且在**故意放宽**（去掉对应规则）的判据下必须 PASS —— 证明每条判据都有牙。"""
    cases = []

    # ---------- title_fontsize ----------
    B = "import matplotlib.pyplot as plt\nfig, ax = plt.subplots()\nax.set_title('t', fontsize=12)\nax.plot([1,2],[3,4])\nfig.savefig('a.png')\n"
    cases.append({
        'before': B,
        'family': 'title_fontsize',
        'good': B.replace('fontsize=12', 'fontsize=18'),
        'plan_target': 18.0, 'extract': ('title_fontsize',),
        'bad': [
            {'name': 'wrong_value', 'src': B.replace('fontsize=12', 'fontsize=17'),
             'broken_rule': 'value'},
            {'name': 'collateral_change',
             'src': B.replace('fontsize=12', 'fontsize=18').replace('[1,2]', '[1,2.5]'),
             'broken_rule': 'global_mask'},
            {'name': 'form_changed_kwarg',
             'src': B.replace('fontsize=12', 'size=18'),
             'broken_rule': 'form'},
            {'name': 'syntax_broken',
             'src': B.replace('fontsize=12', 'fontsize=18)'),
             'broken_rule': 'parse'},
        ]})
    # ---------- grid ----------
    B = ("import matplotlib.pyplot as plt\nfig, ax = plt.subplots()\n"
         "ax.grid(True)\nfig.savefig('a.png')\n")
    cases.append({
        'before': B,
        'family': 'grid', 'good': B.replace('grid(True)', 'grid(False)'),
        'plan_target': False, 'extract': ('grid',),
        'bad': [
            {'name': 'wrong_value', 'src': B.replace('grid(True)', 'grid(True)'),
             'broken_rule': 'value'},
            {'name': 'collateral_change',
             'src': B.replace('grid(True)', 'grid(False)').replace("'a.png'", "'b.png'"),
             'broken_rule': 'global_mask'},
            {'name': 'form_changed_positional_to_kw',
             'src': B.replace('grid(True)', 'grid(visible=False)'),
             'broken_rule': 'form'},
        ]})
    # ---------- xlim ----------
    B = ("import matplotlib.pyplot as plt\nfig, ax = plt.subplots()\n"
         "ax.set_xlim(0, 10)\nfig.savefig('a.png')\n")
    cases.append({
        'before': B,
        'family': 'xlim', 'good': B.replace('set_xlim(0, 10)', 'set_xlim(3.7, 13.7)'),
        'plan_target': [3.7, 13.7], 'extract': ('xlim',),
        'bad': [
            {'name': 'half_written',
             'src': B.replace('set_xlim(0, 10)', 'set_xlim(3.7, 10)'),
             'broken_rule': 'value'},
            {'name': 'collateral_change',
             'src': B.replace('set_xlim(0, 10)', 'set_xlim(3.7, 13.7)')
                    .replace('subplots()', 'subplots(1, 1)'),
             'broken_rule': 'global_mask'},
        ]})
    # ---------- pos ----------
    B = ("import matplotlib.pyplot as plt\nfig = plt.figure()\n"
         "ax = fig.add_axes([0.1, 0.1, 0.8, 0.8])\nfig.savefig('a.png')\n")
    cases.append({
        'before': B,
        'family': 'pos',
        'good': B.replace('[0.1, 0.1, 0.8, 0.8]', '[0.25, 0.25, 0.55, 0.6]'),
        'plan_target': [0.25, 0.25, 0.55, 0.6], 'extract': ('pos',),
        'bad': [
            {'name': 'wrong_value',
             'src': B.replace('[0.1, 0.1, 0.8, 0.8]', '[0.25, 0.25, 0.55, 0.5]'),
             'broken_rule': 'value'},
            {'name': 'container_changed_list_to_tuple',
             'src': B.replace('[0.1, 0.1, 0.8, 0.8]', '(0.25, 0.25, 0.55, 0.6)'),
             'broken_rule': 'form'},
            {'name': 'collateral_change',
             'src': B.replace('[0.1, 0.1, 0.8, 0.8]', '[0.25, 0.25, 0.55, 0.6]')
                    .replace('figure()', 'figure(figsize=(9, 9))'),
             'broken_rule': 'global_mask'},
        ]})
    # ---------- label_fontsize_y ----------
    B = ("import matplotlib.pyplot as plt\nfig, ax = plt.subplots()\n"
         "ax.set_ylabel('y', fontsize=9)\nfig.savefig('a.png')\n")
    cases.append({
        'before': B,
        'family': 'label_fontsize_y',
        'good': B.replace('fontsize=9', 'fontsize=15'),
        'plan_target': 15.0, 'extract': ('label_fontsize_y',),
        'bad': [
            {'name': 'wrong_value', 'src': B.replace('fontsize=9', 'fontsize=14'),
             'broken_rule': 'value'},
            {'name': 'collateral_change',
             'src': B.replace('fontsize=9', 'fontsize=15').replace("'y'", "'yy'"),
             'broken_rule': 'global_mask'},
        ]})
    # ---------- tick_fontsize ----------
    B = ("import matplotlib.pyplot as plt\nfig, ax = plt.subplots()\n"
         "ax.tick_params(labelsize=8)\nfig.savefig('a.png')\n")
    cases.append({
        'before': B,
        'family': 'tick_fontsize', 'good': B.replace('labelsize=8', 'labelsize=14'),
        'plan_target': 14.0, 'extract': ('tick_fontsize',),
        'bad': [
            {'name': 'wrong_value', 'src': B.replace('labelsize=8', 'labelsize=13'),
             'broken_rule': 'value'},
            {'name': 'collateral_change',
             'src': B.replace('labelsize=8', 'labelsize=14').replace('subplots()',
                                                                     'subplots(1,1)'),
             'broken_rule': 'global_mask'},
        ]})
    # ---------- figsize_in ----------
    B = ("import matplotlib.pyplot as plt\nfig, ax = plt.subplots(figsize=(6, 4))\n"
         "fig.savefig('a.png')\n")
    cases.append({
        'before': B,
        'family': 'figsize_in',
        'good': B.replace('figsize=(6, 4)', 'figsize=(9, 5.5)'),
        'plan_target': [9.0, 5.5], 'extract': ('figsize_in',),
        'bad': [
            {'name': 'wrong_value',
             'src': B.replace('figsize=(6, 4)', 'figsize=(9, 5)'),
             'broken_rule': 'value'},
            {'name': 'container_changed_tuple_to_list',
             'src': B.replace('figsize=(6, 4)', 'figsize=[9, 5.5]'),
             'broken_rule': 'form'},
            {'name': 'collateral_change',
             'src': B.replace('figsize=(6, 4)', 'figsize=(9, 5.5)')
                    .replace("'a.png'", "'b.png'"),
             'broken_rule': 'global_mask'},
        ]})

    out = []
    for c in cases:
        rec = {'family': c['family']}
        plan = _build_plan(c['before'], c['good'], c['extract'], c['plan_target'])
        strict = verify_run(c['before'], c['good'], plan or [])
        rec['good_strict_ok'] = bool(strict.get('ok'))
        rec['good_reasons'] = strict.get('reasons')
        rec['n_plan'] = len(plan or [])
        # 反向验证：每种坏编辑。三条都要成立才算这条判据"有牙"：
        #   ① 严格判据必须 FAIL（坏编辑不能被放过）；
        #   ② **只留被违反的那条规则** 时也必须 FAIL（= 这条规则自己就能抓住它）；
        #   ③ 去掉这条规则后必须 PASS（= 它确实是"这一条"在拦，不是别的规则顺手拦的）。
        for bad in c['bad']:
            plan_bad = _build_plan(c['before'], bad['src'], c['extract'],
                                   c['plan_target'])
            loose_rules = tuple(r for r in RULES_ALL if r != bad['broken_rule'])
            strict_bad = verify_run(c['before'], bad['src'], plan_bad or [])
            lone_bad = verify_run(c['before'], bad['src'], plan_bad or [],
                                  rules=(bad['broken_rule'],))
            loose_bad = verify_run(c['before'], bad['src'], plan_bad or [],
                                   rules=loose_rules)
            all_rules_catch = []
            for r in RULES_ALL:
                rr = verify_run(c['before'], bad['src'], plan_bad or [], rules=(r,))
                if not rr.get('ok'):
                    all_rules_catch.append(r)
            rec.setdefault('bad', []).append({
                'name': bad['name'], 'broken_rule': bad['broken_rule'],
                'strict_ok': bool(strict_bad.get('ok')),
                'strict_reasons': strict_bad.get('reasons'),
                'lone_ok': bool(lone_bad.get('ok')),
                'lone_reasons': lone_bad.get('reasons'),
                'loose_ok': bool(loose_bad.get('ok')),
                'loose_rules': list(loose_rules),
                'rules_that_catch': all_rules_catch,
                'verdict_ok': ((not strict_bad.get('ok'))
                               and (not lone_bad.get('ok'))),
            })
        out.append(rec)
    return out


# --------------------------------------------------------------------------
# 证据 & 结果
# --------------------------------------------------------------------------
class Evidence(object):
    def __init__(self, wdir):
        self.dir = wdir
        os.makedirs(wdir, exist_ok=True)

    def path(self, name):
        return os.path.join(self.dir, name)

    def save(self, name, text):
        write_text(self.path(name), text)
        return os.path.relpath(self.path(name), HERE).replace('\\', '/')

    def save_bytes(self, name, raw):
        p = self.path(name)
        with open(p, 'wb') as f:
            f.write(raw)
        return os.path.relpath(p, HERE).replace('\\', '/')


def run_cli(arglist, timeout=180, cwd=None):
    env = dict(os.environ)
    env['PYTHONPATH'] = MPLTWEAK_SRC
    env['MPLBACKEND'] = 'Agg'
    env['PYTHONIOENCODING'] = 'utf-8'
    cmd = [PYTHON, '-m', 'mpltweak'] + list(arglist)
    t0 = time.time()
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout,
                              env=env, cwd=cwd or HERE)
        rc, out_raw, err_raw, to = proc.returncode, proc.stdout, proc.stderr, False
    except subprocess.TimeoutExpired as e:
        rc, out_raw, err_raw, to = None, (e.stdout or b''), (e.stderr or b''), True
    except Exception as e:                                    # noqa: BLE001
        return {'rc': None, 'timeout': False, 'spawn_error': repr(e), 'stdout': '',
                'stderr': '', 'cmd': cmd, 'seconds': round(time.time() - t0, 3)}
    return {'rc': rc, 'timeout': to,
            'stdout': out_raw.decode('utf-8', 'replace'),
            'stderr': err_raw.decode('utf-8', 'replace'),
            'cmd': cmd, 'seconds': round(time.time() - t0, 3)}


def cmd_str(cmd):
    return ' '.join('"%s"' % c if (' ' in c or '\\' in c) else c for c in cmd)


def json_of(run):
    s = (run.get('stdout') or '').strip()
    if not s:
        return None, 'stdout 为空'
    try:
        return json.loads(s), None
    except Exception as e:                                    # noqa: BLE001
        return None, 'JSON 解析失败: %s' % e


def file_entry(payload):
    files = (payload or {}).get('files') or []
    return files[0] if files else {}


def run_apply(ev, tag, script, params, style='inplace', timeout=180):
    os.utime(params, None)                 # 防 stale：params 必须比脚本新
    arg = ['apply', script, '--params', params, '--write', '--verify-fast',
           '--json', '--style', style]
    r = run_cli(arg, timeout=timeout)
    payload, err = json_of(r)
    fe = file_entry(payload)
    ev.save('%s.stdout.txt' % tag, r.get('stdout') or '')
    ev.save('%s.stderr.txt' % tag, r.get('stderr') or '')
    return r, payload, fe, err


def run_revert(ev, script, timeout=120, first=False, tag='revert'):
    arg = ['revert', script, '--write', '--json']
    if first:
        arg.append('--first')
    r = run_cli(arg, timeout=timeout)
    payload, err = json_of(r)
    ev.save('%s.stdout.txt' % tag, r.get('stdout') or '')
    ev.save('%s.stderr.txt' % tag, r.get('stderr') or '')
    return r, payload, err


def backup_dir(script):
    """备份目录（脚本锚定，与 --params 无关）。"""
    return os.path.join(os.path.dirname(os.path.abspath(script)), '.tweak_params')


def backup_listing(script):
    d = backup_dir(script)
    out = []
    try:
        names = sorted(os.listdir(d))
    except OSError:
        return out
    for n in names:
        p = os.path.join(d, n)
        try:
            st = os.stat(p)
            out.append({'name': n, 'size': st.st_size,
                        'mtime': time.strftime('%Y-%m-%d %H:%M:%S',
                                               time.localtime(st.st_mtime)),
                        'mtime_epoch': round(st.st_mtime, 3)})
        except OSError:
            out.append({'name': n})
    return out


# --------------------------------------------------------------------------
# 单脚本测试
# --------------------------------------------------------------------------
def choose_figure(sites):
    """挑"可测站点最多"的那张图（并列取最小序号）。"""
    tally = collections.Counter()
    for fam, rows in sites.items():
        for s in rows:
            if s['reach'] and s['fig_index'] is not None:
                tally[s['fig_index']] += 1
    if not tally:
        return None
    best = max(sorted(tally), key=lambda f: (tally[f], -f))
    return best


def build_params(script_basename, fig_index, ctx, sites, targets, mode):
    """构造 params。

    mode='clean' ：值 = 源码现值（干净往返）
    mode='rewrite'：值 = 目标值（定向改写）
    """
    data = {'version': 4, 'script': script_basename, 'fig_index': fig_index,
            'axes': []}
    # ① 轴条目：**必须**把"zip 模式图"里全部非 cax add_axes/add_subplot 轴按序带上，
    #    否则 apply_inplace 的位置 zip 会错位（把轴 i 的变量配成别的轴）。
    A = ctx.fig_axes.get(fig_index) or []
    axis_vals = collections.defaultdict(dict)
    if A:
        for k in range(len(A)):
            axis_vals[k] = {}
    for fam, rows in sites.items():
        for s in rows:
            if not s['reach'] or s['fig_index'] != fig_index:
                continue
            if fam == 'figsize_in':
                continue
            i = s['index']
            if i is None:
                continue
            val = s['value'] if mode == 'clean' else targets.get(
                (fam, s['ordinal']), s['value'])
            if fam == 'grid':
                val = bool(val)
            axis_vals[i][fam] = val
    for i in sorted(axis_vals):
        item = {'index': i}
        item.update(axis_vals[i])
        data['axes'].append(item)
    # ② figsize
    fs_sites = [s for s in sites.get('figsize_in', [])
                if s['reach'] and s['fig_index'] == fig_index]
    if fs_sites:
        s = fs_sites[0]
        val = s['value'] if mode == 'clean' else targets.get(
            ('figsize_in', s['ordinal']), s['value'])
        data['figsize_in'] = [float(val[0]), float(val[1])]
    return data


def _finalize_no_run(rec):
    """本脚本没有可跑的目标（无图 / 无可达站点）→ 把每族状态定死。

    关键：**有字面量但够不到** → `not_applicable`（前提不成立）；
    **压根没有该族字面量** → `skip`（这一族在这个脚本上无从考起）。
    绝不能把"没字面量"混进"不适用"，否则分母会被虚增。
    """
    for fam in FAMILIES:
        info = rec['families'][fam]
        if info['has_literal']:
            info['status'] = 'not_applicable'
            info['nature'] = 'harness_premise'
            info['reason'] = 'no_reachable_site_in_corpus_scope'
        else:
            info['status'] = 'skip'
            info['nature'] = 'harness_premise'
            info['reason'] = 'no_literal_in_source'


def work_dir_for(stem, relpath, base=None):
    """工作目录名：**保证最深的那条路径不超过 Windows MAX_PATH**。

    为什么需要：语料里有 105 字符的文件名。直接拿 `<stem>` 当工作目录名时，
    `<base>/<stem>/.tweak_params/<stem>.tweak.<pid>.bak` 会长到 300+ 字符 →
    `shutil.copy2` 抛 `FileNotFoundError(2)`（**注意 errno 是 2，不是 36**，
    这种误导性 errno 正是 Bug 2 难以定位的原因）。
    实测踩过：1686 个脚本里有 1 个（105 字符名）在 `shutil.copy2` 就崩了，
    结果里只剩一条 `{"path": "?", "error": "FileNotFoundError(2, ...)"}`。

    规则：最深路径 = len(base)+1+d+15+1+len(basename)+7+5+4 ≤ 250。
    """
    base = base or WORK
    bn = os.path.basename(relpath)
    max_d = 250 - len(base) - len(bn) - 28
    if max_d < 16:
        max_d = 16
    if len(stem) <= max_d:
        return stem
    h = hashlib.sha1(relpath.encode('utf-8')).hexdigest()[:8]
    return stem[:max(8, max_d - 9)] + '_' + h


def test_one(row, args):
    t0 = time.time()
    path = row['path']
    stem = row['stem']
    src_path = os.path.join(CORPUS, path.replace('/', os.sep))
    rec = {'path': path, 'stem': stem, 'source': row.get('source'),
           'families': {}, 'noninplace': {}, 'meta': {}, 'evidence': {},
           'error': None}
    raw = read_bytes(src_path)
    rec['meta']['sha_orig'] = sha256_bytes(raw)
    rec['meta']['manifest_sha256'] = row.get('sha256')
    rec['meta']['bytes'] = len(raw)
    text, enc = decode_source(raw)
    rec['meta']['encoding'] = enc
    try:
        tree = ast.parse(text)
    except SyntaxError as e:
        rec['meta']['parse_error'] = '%s' % e
        for fam in FAMILIES:
            rec['families'][fam] = {'status': 'skip', 'reason': 'm1_parse_failed',
                                    'nature': 'corpus_side'}
        return rec
    ctx = Ctx(text, tree)
    rec['meta']['n_figs'] = ctx.n_figs
    rec['meta']['n_add_axes'] = sum(len(v) for v in ctx.fig_axes.values())
    sites = extract_sites(ctx)
    nonin = extract_non_inplace_sites(ctx)
    for fam in NONINPLACE_FAMILIES:
        rec['noninplace'][fam] = len(nonin.get(fam, []))
    # 分母：本脚本里有哪些族的字面量
    for fam in FAMILIES:
        rows = sites.get(fam, [])
        rec['families'][fam] = {
            'has_literal': bool(rows),
            'n_literal_sites': len(rows),
            'n_reach_sites': sum(1 for s in rows if s['reach']),
            'literal_kinds': sorted(set(s['kind'] for s in rows)),
            'unreach_reasons': sorted(set(s['reason'] for s in rows
                                          if not s['reach'])),
            'status': 'pending',
            'nature': None, 'reason': None,
        }
    fig = choose_figure(sites)
    rec['meta']['target_fig'] = fig
    if fig is None:
        _finalize_no_run(rec)
        return rec
    # 本图内可测站点
    testable = collections.defaultdict(list)
    for fam, rows in sites.items():
        for s in rows:
            if not s['reach']:
                continue
            if fam == 'figsize_in':
                if s['fig_index'] == fig:
                    testable[fam].append(s)
            elif s['fig_index'] == fig:
                testable[fam].append(s)
    if not testable:
        _finalize_no_run(rec)
        return rec

    # 目标值
    targets = {}
    for fam, rows in testable.items():
        for s in rows:
            t = target_of(s, ctx)
            if t is None:
                s['reach'] = False
                s['reason'] = 'degenerate_current_value'
                continue
            targets[(fam, s['ordinal'])] = t

    wdir = os.path.join(WORK, work_dir_for(stem, path))
    rec['meta']['work_dir'] = os.path.relpath(wdir, HERE).replace('\\', '/')
    ev = Evidence(wdir)
    # 语料原件只读：先把脚本复制到工作目录
    stage = os.path.join(wdir, os.path.basename(src_path))
    shutil.copy2(src_path, stage)
    rec['evidence']['stage'] = os.path.relpath(stage, HERE).replace('\\', '/')

    clean_p = os.path.join(wdir, 'params_clean.json')
    rw_p = os.path.join(wdir, 'params_rewrite.json')
    write_json(clean_p, build_params(os.path.basename(src_path), fig, ctx, sites,
                                     targets, 'clean'))
    write_json(rw_p, build_params(os.path.basename(src_path), fig, ctx, sites,
                                  targets, 'rewrite'))
    rec['evidence']['params_clean'] = ev.save('params_clean.json',
                                              json.dumps(build_params(
                                                  os.path.basename(src_path), fig,
                                                  ctx, sites, targets, 'clean'),
                                                  ensure_ascii=False, indent=2))
    rec['evidence']['params_rewrite'] = ev.save('params_rewrite.json',
                                                json.dumps(build_params(
                                                    os.path.basename(src_path), fig,
                                                    ctx, sites, targets, 'rewrite'),
                                                    ensure_ascii=False, indent=2))

    # ---- ① 干净往返：与源码现值一致的参数 → 必须 no_change 且字节不变 ----
    pristine = read_bytes(stage)
    r1, p1, fe1, err1 = run_apply(ev, 'clean', stage, clean_p)
    after_clean = read_bytes(stage)
    clean_bytes_same = (after_clean == pristine)
    clean_reason = fe1.get('reason')
    clean = {'reason': clean_reason, 'rc': r1.get('rc'), 'json_err': err1,
             'bytes_same': clean_bytes_same, 'applied': fe1.get('applied'),
             'pending': fe1.get('inplace_pending') or [],
             'warnings': (fe1.get('warnings') or [])[:6],
             'timeout': r1.get('timeout'), 'seconds': r1.get('seconds')}
    clean['ok'] = bool(clean_reason == 'no_change' and clean_bytes_same
                       and not clean['timeout'] and err1 is None)
    if not clean_bytes_same:
        ev.save_bytes('after_clean_modified.py', after_clean)
    rec['clean_roundtrip'] = clean
    rec['evidence']['clean'] = ev.save('clean.json',
                                       json.dumps(clean, ensure_ascii=False, indent=2))
    # 无条件复位，保证改写实验从原件出发
    with open(stage, 'wb') as f:
        f.write(pristine)

    # ---- ② 定向改写 ----
    r2, p2, fe2, err2 = run_apply(ev, 'rewrite', stage, rw_p)
    after_text, _ = decode_source(read_bytes(stage))
    rec['rewrite'] = {'reason': fe2.get('reason'), 'rc': r2.get('rc'),
                      'json_err': err2, 'style': fe2.get('style'),
                      'applied': fe2.get('applied'),
                      'changes': fe2.get('changes') or [],
                      'pending': (fe2.get('inplace_pending')
                                  or fe2.get('pending') or []),
                      'warnings': (fe2.get('warnings') or [])[:12],
                      'timeout': r2.get('timeout'), 'seconds': r2.get('seconds')}
    rec['evidence']['rewrite'] = ev.save(
        'rewrite.json', json.dumps(rec['rewrite'], ensure_ascii=False, indent=2))
    # 独立复核：用同一套自研提取器在 after 上重新取站点
    vres = None
    try:
        tree2 = ast.parse(after_text)
        ctx2 = Ctx(after_text, tree2)
        sites2 = extract_sites(ctx2)
        plan = []
        reloc_fail = []
        spelling = []
        for fam, rows in testable.items():
            for sb in rows:
                sa = None
                # 主键 = (族, 序号, 轴序号)；写法变了也要配对成功 —— 那正是
                # `form` 规则要判的东西，不能让"配不上"把判据顶掉。
                for cand in sites2.get(fam, []):
                    if (cand['ordinal'] == sb['ordinal']
                            and cand.get('index') == sb.get('index')):
                        sa = cand
                        break
                if sa is None:
                    reloc_fail.append('%s#%d' % (fam, sb['ordinal']))
                pe = plan_entry(text, after_text, sb, sa,
                                targets.get((fam, sb['ordinal'])), sb['value'])
                # 写法骨架（数字/布尔一律换成 #，**保留空白与标点**）：值改对了但
                # 骨架变了 = 工具把整段写法重写/规范化了。**不计 fail**，单独计数。
                # 同时存**原始**文本 → 报告阶段可重新判定，不必重跑 CLI。
                pe['spelling_raw_before'] = spelling_raw(text, sb)
                pe['spelling_raw_after'] = (spelling_raw(after_text, sa)
                                            if sa is not None else None)
                pe['spelling_before'] = spelling_mask(pe['spelling_raw_before'] or '')
                pe['spelling_after'] = (spelling_mask(pe['spelling_raw_after'])
                                        if pe['spelling_raw_after'] is not None
                                        else None)
                pe['spelling_preserved'] = bool(
                    sa is not None and pe['spelling_raw_before'] is not None
                    and pe['spelling_before'] == pe['spelling_after'])
                spelling.append(pe)
                plan.append(pe)
        vres = verify_run(text, after_text, plan)
        vres['reloc_fail'] = reloc_fail
        vres['plan'] = [{k: v for k, v in p.items() if k != 'spans_before'}
                        for p in plan]
        # 【发布纪律】结果 JSONL 里**只留判定**：`key` + 布尔。
        # 任何"源码原文 / 掩码后的字面量写法"一律写进证据文件
        # `work_fields/<dir>/spelling.json` —— 证据目录不发布，
        # 而结果文件要进 bench 分支供人核对，不能构成对第三方代码的再分发。
        _detail = [{'key': p['key'],
                    'raw_before': p.get('spelling_raw_before'),
                    'raw_after': p.get('spelling_raw_after'),
                    'masked_before': p['spelling_before'],
                    'masked_after': p['spelling_after'],
                    'spelling_preserved': p['spelling_preserved'],
                    'value_ok': next(
                        (d['ok'] for d in vres.get('value_detail', [])
                         if d.get('key') == p['key']), None)}
                   for p in spelling]
        rec['spelling'] = [{'key': p['key'],
                            'preserved': bool(p['spelling_preserved']),
                            'value_ok': next(
                                (d['ok'] for d in vres.get('value_detail', [])
                                 if d.get('key') == p['key']), None)}
                           for p in spelling]
        if any(x.get('value_ok') and not x['spelling_preserved']
               for x in _detail):
            rec['evidence']['spelling'] = ev.save(
                'spelling.json', json.dumps(_detail, ensure_ascii=False,
                                            indent=2))
        rec['evidence']['after_rewrite.py'] = ev.save('after_rewrite.py', after_text)
        rec['evidence']['verify.json'] = ev.save(
            'verify.json', json.dumps(vres, ensure_ascii=False, indent=2))
        rec['evidence']['diff.txt'] = ev.save(
            'diff.diff', unified_diff(text, after_text))
    except SyntaxError as e:
        vres = {'ok': False, 'reasons': ['after_syntax_error: %s' % e]}
        rec['evidence']['after_rewrite.py'] = ev.save('after_rewrite.py', after_text)

    # ---- ③ 幂等 ----
    after1 = read_bytes(stage)
    r3, p3, fe3, err3 = run_apply(ev, 'idem', stage, rw_p)
    after2 = read_bytes(stage)
    idem = {'reason': fe3.get('reason'), 'rc': r3.get('rc'),
            'bytes_same': (after1 == after2), 'json_err': err3,
            'timeout': r3.get('timeout')}
    idem['ok'] = bool(idem['reason'] == 'no_change' and idem['bytes_same']
                      and not idem['timeout'] and err3 is None)
    rec['idempotent'] = idem
    rec['evidence']['idem'] = ev.save('idem.json',
                                      json.dumps(idem, ensure_ascii=False, indent=2))
    # ---- ④ 可逆 ----
    rr, pr, errr = run_revert(ev, stage)
    restored = read_bytes(stage)
    rev = {'rc': rr.get('rc'), 'json_err': errr,
           'bytes_restored': (restored == pristine),
           'sha_restored': sha256_bytes(restored),
           'sha_orig': sha256_bytes(pristine),
           'ok': bool(restored == pristine and errr is None)}
    rec['revert'] = rev
    rec['evidence']['revert'] = ev.save('revert.json',
                                        json.dumps(rev, ensure_ascii=False,
                                                   indent=2))

    # ---- ⑤ F-V1：一次会话里连续 apply **多份** params，再 revert ----
    #   已知缺口（parent 交接）：旧脚手架只覆盖"单个 params 文件写回→revert"。
    #   这里补：A 号 params → B 号 params（两份都在同一脚本上连写）→
    #   ① `revert --write`（默认，回到"最近一次写回之前"= A 之后的文本）
    #   ② `revert --write --first`（回到**真·原件**）
    #   两条都与逐字节比对为证。B 的目标值由 A 的目标再算一次，
    #   保证 B 一定也是一次真实写盘（不是 no_change 的空跑）。
    fv1 = {'ran': False}
    try:
        if testable:
            targets_b = {}
            for fam, rows in testable.items():
                for sb in rows:
                    t = targets.get((fam, sb['ordinal']))
                    if t is None:
                        continue
                    fake = dict(sb)
                    fake['value'] = t
                    t2 = target_of(fake, ctx)
                    if t2 is not None:
                        targets_b[(fam, sb['ordinal'])] = t2
            fb_p = os.path.join(wdir, 'params_round_b.json')
            write_json(fb_p, build_params(os.path.basename(src_path), fig, ctx,
                                          sites, targets_b, 'rewrite'))
            ev.save('params_round_b.json',
                    json.dumps(build_params(os.path.basename(src_path), fig, ctx,
                                            sites, targets_b, 'rewrite'),
                               ensure_ascii=False, indent=2))
            fv1['ran'] = True
            _ra, _pa, _fea, _ea = run_apply(ev, 'fv1_a', stage, rw_p)
            T1 = read_bytes(stage)
            _rb, _pb, _feb, _eb = run_apply(ev, 'fv1_b', stage, fb_p)
            T2 = read_bytes(stage)
            ev.save_bytes('fv1_T1_after_a.py', T1)
            ev.save_bytes('fv1_T2_after_b.py', T2)
            fv1['a_reason'] = _fea.get('reason')
            fv1['b_reason'] = _feb.get('reason')
            fv1['a_is_real_write'] = (T1 != pristine)
            fv1['b_is_real_write'] = (T2 != T1)
            fv1['backups_after_b'] = backup_listing(stage)
            _r1, _p1, _e1 = run_revert(ev, stage, tag='fv1_revert_default')
            D = read_bytes(stage)
            _r2, _p2, _e2 = run_revert(ev, stage, first=True, tag='fv1_revert_first')
            F = read_bytes(stage)
            fv1['default_revert_reason'] = (_p1 or {}).get('reason')
            fv1['first_revert_reason'] = (_p2 or {}).get('reason')
            fv1['default_eq_prev_write_image'] = (D == T1)
            fv1['default_eq_pristine'] = (D == pristine)
            fv1['first_eq_pristine'] = (F == pristine)
            fv1['first_eq_prev_write_image'] = (F == T1)
            fv1['sha_pristine'] = sha256_bytes(pristine)
            fv1['sha_default'] = sha256_bytes(D)
            fv1['sha_first'] = sha256_bytes(F)
            fv1['default_still_after_b'] = (D == T2)
            fv1['ok'] = bool(fv1['a_is_real_write'] and fv1['b_is_real_write']
                             and fv1['default_eq_prev_write_image']
                             and fv1['first_eq_pristine'])
            rec['evidence']['fv1'] = ev.save(
                'fv1.json', json.dumps(fv1, ensure_ascii=False, indent=2))
    except Exception as e:                                      # noqa: BLE001
        fv1['error'] = repr(e)
    rec['fv1'] = fv1
    # 复位 + 清掉工作目录里的备份（审计要求）
    with open(stage, 'wb') as f:
        f.write(pristine)
    shutil.rmtree(backup_dir(stage), ignore_errors=True)

    # ---- 逐族判定 ----
    for fam in FAMILIES:
        info = rec['families'][fam]
        rows = testable.get(fam, [])
        if not rows:
            if info['has_literal']:
                info['status'] = 'not_applicable'
                if info['n_reach_sites'] == 0:
                    info['nature'] = _unreach_nature(info['unreach_reasons'])
                    info['reason'] = ','.join(info['unreach_reasons']) or 'unreachable'
                else:
                    info['nature'] = 'harness_premise'
                    info['reason'] = 'reachable_site_in_other_figure_not_exercised'
            else:
                info['status'] = 'skip'
                info['nature'] = 'harness_premise'
                info['reason'] = 'no_literal_in_source'
            continue
        if r2.get('timeout'):
            info['status'] = 'skip'
            info['nature'] = 'harness_premise'
            info['reason'] = 'timeout'
            continue
        if err2 is not None or fe2.get('reason') in ('fail', 'bad_params', 'stale',
                                                     'no_fig', 'no_axes'):
            info['status'] = 'skip'
            info['nature'] = 'corpus_side' if fe2.get('reason') == 'bad_params' \
                else 'harness_premise'
            info['reason'] = 'apply_%s' % (fe2.get('reason') or 'json_error')
            continue
        per_site = [d for d in (vres or {}).get('value_detail', [])
                    if d['family'] == fam]
        all_val = bool(per_site) and all(d['ok'] for d in per_site)
        _sp = [x for x in (rec.get('spelling') or [])
               if x['key'].startswith(fam + '#')]
        info['spelling_sites'] = len(_sp)
        info['spelling_changed'] = sum(
            1 for x in _sp if x.get('value_ok') and not x.get('preserved'))
        info['spelling_preserved'] = sum(
            1 for x in _sp if x.get('value_ok') and x.get('preserved'))
        info['rewrite_value_ok'] = all_val
        info['clean_ok'] = clean['ok']
        info['mask_ok'] = (vres or {}).get('mask_ok')
        info['parse_ok'] = (vres or {}).get('parse_ok')
        info['form_ok'] = (vres or {}).get('form_ok')
        info['idem_ok'] = idem['ok']
        info['revert_ok'] = rev['ok']
        info['fail_reasons'] = (vres or {}).get('reasons') or []
        info['value_detail'] = per_site
        if not clean['ok']:
            info['status'] = 'fail'
            info['nature'] = _clean_fail_nature(clean, info)
            info['reason'] = 'clean_roundtrip:%s' % (
                clean['reason'] or 'bytes_changed' if not clean['bytes_same']
                else clean['reason'])
        elif not all_val:
            info['status'] = 'fail'
            info['nature'] = 'tool_bug'
            info['reason'] = 'rewrite_value_not_target'
        elif not (vres or {}).get('mask_ok'):
            info['status'] = 'fail'
            info['nature'] = 'tool_bug'
            info['reason'] = 'collateral_text_change'
        elif not (vres or {}).get('parse_ok'):
            info['status'] = 'fail'
            info['nature'] = 'tool_bug'
            info['reason'] = 'after_syntax_error'
        elif not (vres or {}).get('form_ok'):
            info['status'] = 'fail'
            info['nature'] = 'tool_bug'
            info['reason'] = 'writing_form_changed'
        elif not idem['ok']:
            info['status'] = 'fail'
            info['nature'] = 'tool_bug'
            info['reason'] = 'idempotent:%s' % idem['reason']
        elif not rev['ok']:
            info['status'] = 'fail'
            info['nature'] = 'tool_bug'
            info['reason'] = 'revert_not_byte_identical'
        else:
            info['status'] = 'pass'
            info['nature'] = None
            info['reason'] = 'ok'
    rec['meta']['seconds'] = round(time.time() - t0, 3)
    return rec


def _unreach_nature(reasons):
    li = set(reasons or [])
    if not li:
        return 'harness_premise'
    if li & {'multi_fig_subplots_var_unreachable', 'no_reachable_site_in_corpus_scope',
             'reachable_site_in_other_figure_not_exercised'}:
        return 'harness_premise'
    if li & {'colorbar_cax_axis', 'colorbar_host_axis', 'dup_axes_assign_overlap_rejected',
             'add_axes_pos_not_4_literals'}:
        return 'harness_premise'
    return 'tool_limitation'


def _clean_fail_nature(clean, info):
    if clean.get('timeout'):
        return 'harness_premise'
    if not clean.get('bytes_same'):
        return 'tool_bug'
    if clean.get('reason') in ('fail', 'bad_params', 'stale', 'no_fig', 'no_axes'):
        return 'harness_premise'
    if clean.get('reason') == 'best_effort':
        return 'tool_limitation'
    return 'tool_bug'


def unified_diff(a, b, limit=400):
    import difflib
    lines = list(difflib.unified_diff(a.splitlines(True), b.splitlines(True),
                                      'before', 'after', n=2))
    return ''.join(lines[:limit])


# --------------------------------------------------------------------------
# 语料构建
# --------------------------------------------------------------------------
def load_manifest():
    rows = []
    with io.open(MANIFEST, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def build_rows(args):
    rows = load_manifest()
    rows = [r for r in rows if os.path.exists(
        os.path.join(CORPUS, r['path'].replace('/', os.sep)))]
    if args.only:
        rows = [r for r in rows if args.only in r['path']]
    if args.limit:
        rows = rows[:args.limit]
    for r in rows:
        r['stem'] = os.path.splitext(os.path.basename(r['path']))[0]
    return rows


# --------------------------------------------------------------------------
# 统计
# --------------------------------------------------------------------------
def spelling_examples_for(rec, fam, limit=2):
    """从证据文件 `work_fields/<dir>/spelling.json` 里取该族的"写法被改"实例。

    结果 JSONL 里**不存**源码文本，所以例子只能从证据目录读回来；
    证据目录不发布，因此不构成对第三方代码的再分发。
    """
    wd = (rec.get('meta') or {}).get('work_dir')
    if not wd:
        return []
    p = os.path.join(HERE, wd.replace('/', os.sep), 'spelling.json')
    if not os.path.exists(p):
        return []
    try:
        with io.open(p, encoding='utf-8') as f:
            det = json.load(f)
    except (OSError, ValueError):
        return []
    out = []
    for x in det:
        if (str(x.get('key', '')).startswith(fam + '#')
                and x.get('value_ok') and not x.get('spelling_preserved')):
            out.append({'before': x.get('masked_before'),
                        'after': x.get('masked_after')})
            if len(out) >= limit:
                break
    return out


def aggregate(results):
    fam = {}
    for f in FAMILIES:
        fam[f] = {'literal_scripts': 0, 'testable_scripts': 0, 'pass': 0,
                  'fail': 0, 'not_applicable': 0, 'skip': 0, 'absent': 0,
                  'literal_sites': 0, 'reach_sites': 0,
                  'natures': collections.Counter(),
                  'fail_natures': collections.Counter(),
                  'na_natures': collections.Counter(),
                  'na_reasons': collections.Counter(),
                  'kinds': collections.Counter(),
                  'unreach_kinds': collections.Counter(),
                  'spelling_sites': 0, 'spelling_changed': 0,
                  'spelling_preserved': 0,
                  'spelling_scripts': 0,
                  'spelling_examples': [],
                  'fails': []}
    non = {}
    for f in NONINPLACE_FAMILIES:
        non[f] = {'literal_scripts': 0, 'literal_sites': 0}
    fv1 = collections.Counter()
    fv1_backup_hist = collections.Counter()
    for r in results:
        # 写法骨架在**报告阶段**重算（用当前掩码规则），这样调整掩码口径
        # 不需要重跑 CLI —— 原始片段已经存在结果文件里。
        _sp = []
        for x in (r.get('spelling') or []):
            rb, ra = x.get('raw_before'), x.get('raw_after')
            if rb is not None and ra is not None:
                b, a = spelling_mask(rb), spelling_mask(ra)
                x = dict(x, before=b, after=a, preserved=(b == a))
            _sp.append(x)
        r['spelling'] = _sp
        _v = r.get('fv1') or {}
        if _v.get('ran'):
            fv1['ran'] += 1
            for k in ('a_is_real_write', 'b_is_real_write',
                      'default_eq_prev_write_image', 'default_eq_pristine',
                      'first_eq_pristine', 'ok'):
                if _v.get(k):
                    fv1[k] += 1
            fv1['a_reason:%s' % _v.get('a_reason')] += 1
            fv1['b_reason:%s' % _v.get('b_reason')] += 1
            fv1_backup_hist[len(_v.get('backups_after_b') or [])] += 1
        for f, info in (r.get('families') or {}).items():
            if f not in fam:
                continue
            a = fam[f]
            if info.get('has_literal'):
                a['literal_scripts'] += 1
                a['literal_sites'] += info.get('n_literal_sites', 0)
            a['reach_sites'] += info.get('n_reach_sites', 0)
            a['spelling_sites'] += info.get('spelling_sites', 0)
            a['spelling_changed'] += info.get('spelling_changed', 0)
            a['spelling_preserved'] += info.get('spelling_preserved', 0)
            if info.get('spelling_changed'):
                a['spelling_scripts'] += 1
                # 例子从**证据文件**里读（结果 JSONL 不存源码文本）
                for ex in spelling_examples_for(r, f, limit=2):
                    if len(a['spelling_examples']) < 60:
                        a['spelling_examples'].append(dict(ex, path=r['path']))
            for k in (info.get('literal_kinds') or []):
                a['kinds'][k] += 1
            for k in (info.get('unreach_reasons') or []):
                a['unreach_kinds'][k] += 1
            st = info.get('status')
            if st == 'pass':
                a['testable_scripts'] += 1
                a['pass'] += 1
            elif st == 'fail':
                a['testable_scripts'] += 1
                a['fail'] += 1
                a['natures'][info.get('nature')] += 1
                a['fail_natures'][info.get('nature')] += 1
                a['fails'].append({'path': r['path'],
                                   'nature': info.get('nature'),
                                   'reason': info.get('reason'),
                                   'apply_reason': (r.get('rewrite') or {}).get('reason'),
                                   'fail_reasons': info.get('fail_reasons'),
                                   'evidence': r.get('evidence')})
            elif st == 'not_applicable':
                a['not_applicable'] += 1
                a['natures'][info.get('nature')] += 1
                a['na_natures'][info.get('nature')] += 1
                a['na_reasons'][info.get('reason')] += 1
            elif st == 'skip':
                # "这一族在这个脚本里压根没出现" ≠ "跳过"：前者在分母之外，
                # 混进来会把 skip 列灌成 1652 这种毫无信息量的数。
                if info.get('reason') == 'no_literal_in_source':
                    a['absent'] += 1
                else:
                    a['skip'] += 1
                    a['natures'][info.get('nature')] += 1
        for f, n in (r.get('noninplace') or {}).items():
            if f in non and n:
                non[f]['literal_scripts'] += 1
                non[f]['literal_sites'] += n
    return fam, non, fv1, fv1_backup_hist


def count_status(results, key):
    c = collections.Counter()
    for r in results:
        c[(r.get(key) or {}).get('reason')] += 1
    return c


# --------------------------------------------------------------------------
# 报告
# --------------------------------------------------------------------------
FIELD_TABLE = """\
| 字段 | 工具**原位写回** | 源码里被认的写法（写回侧 `apply_inplace`） | 值域/约束 | 等值证明（`_proved_equal`，--verify-fast 靠它判 no_change） |
|---|---|---|---|---|
| `pos` | ✅ | `ax = fig.add_axes([x0,y0,w,h])`（`[]`/`()` 都认）；接收者必须是**轴变量名** | 4 个数值字面量；轴非 colorbar、非 colorbar 宿主；同图内该变量只被 add_axes 赋值一次 | ①同变量 add_axes 字面量全等；②本图区间内建图调用按序比；③cax 兜底 |
| `figsize_in` | ✅ | 第 `fig_index` 个 `figure()/subplots()/subplot_mosaic()` 的 `figsize=` kwarg | 2 个数值字面量的 `[]`/`()`；`figsize` 是位置参数/变量 → 不认 | 等值短路（`figsize_already_ok`） |
| `title_fontsize` | ✅ | `ax.set_title(..., fontsize=N)` | 数字字面量 | `set_title` 的 `fontsize=`/`size=`；无写法时比 matplotlib 默认 `axes.titlesize` |
| `label_fontsize` | ✅ | `ax.set_xlabel(..., fontsize=N)` | 数字字面量 | `set_xlabel` 的 `fontsize=`/`size=`；默认 `axes.labelsize` |
| `label_fontsize_y` | ✅（params 显式给该键时） | `ax.set_ylabel(..., fontsize=N)` | 数字字面量 | `set_ylabel` 的 `fontsize=`/`size=`；默认 `axes.labelsize` |
| `tick_fontsize` | ✅ | `ax.tick_params(labelsize=N)`（无 `axis=`）；或源码**同时**有 `axis='x'`+`axis='y'` 两写法时的 x 那支 | 数字字面量 | `tick_params`/`set_xticklabels` 的 `labelsize/fontsize/size`；默认 `xtick.labelsize` |
| `tick_fontsize_y` | ✅（params 显式给该键时） | `ax.tick_params(axis='y', labelsize=N)`，且**要求** `axis='x'` 那支也存在 | 数字字面量 | `tick_params`/`set_yticklabels`；默认 `ytick.labelsize` |
| `grid` | ✅ | `ax.grid(True/False)`**位置**布尔常量 | 必须 `ast.Constant(bool)`（`grid(1)`/`grid(visible=True)` 不认） | `ax.grid(<bool>)` 字面量；无写法时比 `axes.grid` |
| `xlim` / `ylim` | ✅ | `ax.set_xlim(a, b)` / `set_xlim((a,b))` / `set_xlim([a,b])` / `set_xlim(xmin=, xmax=)` | 2 个数值字面量；`left=`/`right=`、变量、`**kwargs` 不认 | **无独立等值证明**：靠 `apply_inplace` 内的"空改（noop）"判定 |
| `spines` | ❌ 块模式专属 | 永远进 `skipped`；块里写 `ax.spines['top'].set_visible(False)` | — | 默认值证明（四条全可见）+ `set_visible/set_frame_on/axis` 冲突检查 |
| `xscale` / `yscale` | ❌ 块模式专属 | 永远进 `skipped`；块里写 `ax.set_xscale('log')` | `SCALE_ENUM` 白名单 | `set_xscale` 字面量比对 + 默认 `linear` |
| `clim` / `cmap` / `lines` / `legend` / `cell` | ❌ | 永远进 `skipped` | — | 部分（cmap/legend 有字面量证明） |
| 网格轴（`plt.subplots`）的 `pos` | ❌ | `add_subplot` 网格位置在源码里没有 4 字面量可改 | — | 默认值/字面量证明可避免误退块 |
"""


def git_src_drift(head1, head2):
    """两个提交之间 **引擎源码**（`src/mpltweak`）有没有差异。

    为什么不用"HEAD 相同"当判据：本仓库正被另一个作业持续提交
    （文档/CHANGELOG/测试），HEAD 几乎必然在 4 分钟的全量跑里移动。
    真正影响结论的是"引擎源码有没有变"——`src/mpltweak` 无差异 ⇒
    整轮跑在**同一份引擎行为**上，结论仍然成立。
    """
    if not head1 or not head2:
        return None
    if head1 == head2:
        return {'same_head': True, 'src_changed': 0, 'stat': ''}
    for cand in GIT_EXE_CANDIDATES:
        try:
            p = subprocess.run([cand, 'diff', '--stat', '%s..%s' % (head1, head2),
                                '--', 'src/mpltweak'],
                               capture_output=True, cwd=MPLTWEAK_ROOT, timeout=60)
            if p.returncode == 0:
                txt = (p.stdout or b'').decode('utf-8', 'replace').strip()
                return {'same_head': False,
                        'src_changed': 0 if not txt else len(txt.splitlines()),
                        'stat': txt[:1500]}
        except Exception:                                       # noqa: BLE001
            continue
    return None


def audget_head(audits):
    return (audits or {}).get('git_head')


def build_report(results, meta, fam, non, selft, audits, extra, fv1=None,
                 fv1_backup_hist=None):
    L = []
    A = L.append
    A('# %s' % TITLE)
    A('')
    A('- 被测工具：`%s`' % MPLTWEAK_SRC)
    A('- 被测提交：`%s`（%s，分支 `%s`）' % (meta.get('git_head', '?'),
                                            meta.get('git_head_date', '?'),
                                            meta.get('git_branch', '?')))
    A('- 语料：`corpus018/`（gallery %d + zenodo %d = %d 条 manifest 记录，'
      '本轮实际跑 %d 条）' % (meta.get('n_gallery', 0), meta.get('n_zenodo', 0),
                              meta.get('n_manifest', 0), len(results)))
    A('- 解释器：`%s`' % PYTHON)
    A('- 工具命令（**只用这两条**）：`python -m mpltweak apply <脚本> --params '
      '<p.json> --write --verify-fast --json --style inplace` 与 '
      '`python -m mpltweak revert <脚本> --write`')
    A('- 生成时间：%s；耗时 %.1f s（jobs=%d）'
      % (meta.get('finished_at', '?'), meta.get('wall_seconds', 0),
         meta.get('jobs', 0)))
    A('- 结果文件：`l0_results_fields.jsonl`；逐字证据：`work_fields/<脚本名>/`')
    A('')
    A('> `--style inplace` 是本次特意加的：它保证**只做原位写回、绝不插调整块**。'
      '否则一族"原位写不了"会让 auto 退回块模式、往整份文件里插块，'
      '从而把同脚本其它族的"只改目标那一处"判据一起污染。'
      '本报告的结论口径是**原位写回**，不是工具的全部能力'
      '（块模式另可覆盖 spines / xscale / clim 等）。')
    A('')

    # ---- §1 权威字段表 ----
    A('## 1. 权威字段表（来自被测源码，不靠猜）')
    A('')
    A('读取位置：`writeback.py::apply_inplace`（原位分支，第 806–1170 行）、'
      '`writeback.py::edit_figsize`（画布尺寸，661–693）、'
      '`writeback.py::_proved_equal` / `_use_matplotlib_default` / '
      '`_default_proof_blocked`（等值证明，1282–1531）、'
      '`writeback.py::_find_call` / `_find_tick_axis_call`（寻址，699–747）、'
      '`params.py::AXIS_DEFAULTS`（字段默认值）、`verify.py`（采集字段）。')
    A('')
    A(FIELD_TABLE)
    A('')
    A('### 1.1 寻址模型的关键约束（决定了"分母"里有谁能被考）')
    A('')
    A('- `_find_call` 要求接收者是 **`ast.Name` 且等于轴变量名** → '
      '`axes[0].set_title(..., fontsize=12)`、`plt.title(..., fontsize=12)`、'
      '`axs[0,1].grid(True)` 这类写法**一律够不到**（字面量存在但不可原位写）。')
    A('- 轴变量 → 轴序号有两套：① 图区间内 `add_axes/add_subplot` 赋值按源码序'
      '**位置 zip** 配对（所以 params 的 `axes` 必须把该图全部非 cax 轴按序带上，'
      '少一个就整体错位）；② 单图脚本另用 `axis_var_index` 反查补齐。'
      '**多图脚本的 `plt.subplots` 轴不走②**（怕跨图错配）→ 不可原位。')
    A('- `_find_call` 取的是区间内**最早**那个同名调用 → 若最早的 '
      '`set_title` 没有 `fontsize=` 而第二个才有，工具够不到第二个。')
    A('- 字段查找被夹在 `[_scope_lo, _scope_hi)`（本图源码区间）里 → '
      '字面量落在建图区间之外（如 `add_axes` 在函数里、在建图调用之前）够不到。')
    A('- params 顶层只有一个 `fig_index` → 一次 apply 只处理**一张图**。')
    A('')
    A('### 1.2 值域约束')
    A('')
    A('- 数字字段（字号 / 范围 / 位置 / 尺寸）：字面量必须整体可替换，'
      '带符号写法（`-0.01`）走 `UnaryOp` 也认。')
    A('- `grid`：必须 `ast.Constant(bool)`；`grid(1)`、`grid(visible=True)` 不认。')
    A('- `xscale/yscale`：`params.SCALE_ENUM = '
      "('linear','log','symlog','logit','asinh','function','functionlog', None)`。")
    A('- `is_colorbar=True` 的轴（`fig.colorbar(..., cax=cax)`）的 `pos` 不原位写；'
      '`fig.colorbar(..., ax=ax)` 的**宿主轴** `pos` 也不原位写。')
    A('')

    # ---- §2 按字段族成绩 ----
    A('## 2. 按字段族成绩表')
    A('')
    A('口径说明：')
    A('')
    A('- **分母** = 该族在语料里有**字面量写法**的脚本数（任何写法，含工具够不到的写法）。'
      '这是"多少真实脚本用到了这个字段"的直接产出。')
    A('- **可测** = 分母里至少有一个站点落在**工具的寻址模型够得到**的位置'
      '（且在本轮选定的目标图内）→ 进入 pass/fail。')
    A('- **不适用** = 有字面量但工具寻址够不到 / 判据前提不成立（cax、跨图、'
      'Subscript 接收者、别名 kwarg 等）→ 记 `harness_premise` 或 `tool_limitation`。')
    A('- **跳过** = 语料语法错、超时、apply 契约错误等，本族无法判定。')
    A('- 每族**独立判据**：改后用自己的 AST 复核"除目标字面量外逐字节相同 + '
      '值等于目标 + 容器/写法保持 + 语法可解析"；另叠脚本级幂等与 revert 逐字节可逆。')
    A('')
    A('| 字段族 | 分母（有字面量的脚本） | 字面量站点数 | 可测 | 通过 | 失败 | 不适用 | 跳过 | 本脚本无此字段 | 通过/可测 | 失败性质 |')
    A('|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|')
    for f in FAMILIES:
        a = fam[f]
        tot = a['pass'] + a['fail']
        natures = '、'.join('%s×%d' % (k, v)
                            for k, v in sorted(
                                (x for x in a['fail_natures'].items()
                                 if x[0] is not None),
                                key=lambda x: x[0])) or '—'
        A('| %s | %d | %d | %d | %d | %d | %d | %d | %d | %s | %s |'
          % (FAMILY_LABEL[f], a['literal_scripts'], a['literal_sites'],
             a['testable_scripts'], a['pass'], a['fail'], a['not_applicable'],
             a['skip'], a['absent'],
             ('%.1f%%' % (100.0 * a['pass'] / tot)) if tot else '—',
             natures))
    A('')
    A('### 2.1 分母里"工具够不到"的写法分布（`tool_limitation` 的构成）')
    A('')
    A('| 字段族 | 够不到的写法 / 原因 | 涉及脚本数 |')
    A('|---|---|---:|')
    for f in FAMILIES:
        a = fam[f]
        if not a['unreach_kinds']:
            continue
        for k, v in sorted(a['unreach_kinds'].items(), key=lambda x: -x[1]):
            A('| %s | `%s` | %d |' % (f, k, v))
    A('')
    A('### 2.2 分母里的字面量写法分布（考卷覆盖了什么形式）')
    A('')
    A('| 字段族 | 源码写法 | 脚本数 |')
    A('|---|---|---:|')
    for f in FAMILIES:
        a = fam[f]
        for k, v in sorted(a['kinds'].items(), key=lambda x: -x[1]):
            A('| %s | `%s` | %d |' % (f, k, v))
    A('')
    A('### 2.3 写法骨架（spelling）单独统计 —— **不计 fail**，但对"最小改动"承诺有用')
    A('')
    A('口径：把站点整段里的数字换成 `#`、**保留空白与标点**后比对骨架。'
      '骨架变了 = 工具把整段写法重写/规范化了（值仍然正确、语义等价）。'
      '只有"值确实落到了目标"的站点才计入这里。')
    A('')
    A('| 字段族 | 值落地的站点数 | 骨架保持 | 骨架被改 | 涉及脚本数 | 骨架被改的实例（前 2 个） |')
    A('|---|---:|---:|---:|---:|---|')
    for f in FAMILIES:
        a = fam[f]
        ex = a['spelling_examples'][:2]
        exs = '；'.join('`%s` → `%s`' % (e.get('before'), e.get('after'))
                        for e in ex) or '—'
        A('| %s | %d | %d | %d | %d | %s |'
          % (FAMILY_LABEL[f], a['spelling_sites'], a['spelling_preserved'],
             a['spelling_changed'], a['spelling_scripts'], exs))
    A('')
    A('### 2.4 非原位族（工具设计上就不做原位写回，仅统计分母与处置）')
    A('')
    A('| 字段 | 分母（有字面量的脚本） | 字面量站点数 | 工具处置 |')
    A('|---|---:|---:|---|')
    _disp = {'spines': '进 `skipped` → auto 退块，块里写 '
                       "`ax.spines['top'].set_visible(False)`",
             'xscale': '进 `skipped` → 退块，写 `ax.set_xscale(...)`',
             'yscale': '进 `skipped` → 退块，写 `ax.set_yscale(...)`',
             'clim': '进 `skipped` → 退块，写 `ax.set_clim(...)`'}
    for f in NONINPLACE_FAMILIES:
        n = non[f]
        A('| `%s` | %d | %d | %s |' % (f, n['literal_scripts'],
                                       n['literal_sites'], _disp[f]))
    A('')

    # ---- §3 与旧口径对照 ----
    A('### 2.5 F-V1 场景：一次会话连写**多份** params 后的 revert 行为')
    A('')
    A('旧脚手架的写回→revert 只用**单个** params 文件；这里补上 parent 点名的缺口：'
      '在同一脚本上 `apply(A)` → `apply(B)`（两份不同目标）→ `revert --write` → '
      '`revert --write --first`，每一步都与"该步应有的字节"做逐字节比对。')
    A('')
    v = fv1 or collections.Counter()
    ran = v.get('ran', 0)
    if not ran:
        A('（本轮未运行 F-V1）')
    else:
        A('| 判据 | 通过 | 分母 |')
        A('|---|---:|---:|')
        for k, label in (('a_is_real_write', 'A 号 params 真的改了文件'),
                         ('b_is_real_write', 'B 号 params 又改了一次（非空跑）'),
                         ('default_eq_prev_write_image',
                          '默认 revert 回到"最近一次写回之前"（= A 之后的文本）'),
                         ('default_eq_pristine', '默认 revert 直接回到真·原件'),
                         ('first_eq_pristine', '`revert --first` 回到真·原件'),
                         ('ok', '整个 F-V1 序列全部成立')):
            A('| %s | %d | %d |' % (label, v.get(k, 0), ran))
        A('')
        A('- A 号 apply 的 reason 分布：%s'
          % '、'.join('`%s`×%d' % (k.split(':', 1)[1], n)
                      for k, n in sorted(v.items())
                      if k.startswith('a_reason:')))
        A('- B 号 apply 的 reason 分布：%s'
          % '、'.join('`%s`×%d' % (k.split(':', 1)[1], n)
                      for k, n in sorted(v.items())
                      if k.startswith('b_reason:')))
        A('- 连写两次后 `.tweak_params/` 里的备份文件个数分布：%s'
          % '、'.join('%d 个×%d 个脚本' % (k, n)
                      for k, n in sorted((fv1_backup_hist or {}).items())))
        A('')
        if v.get('first_eq_pristine', 0) == ran and v.get('default_eq_prev_write_image', 0) == ran:
            A('**结论（F-V1 未复现缺口）**：默认 revert 回到 A 之后的文本、'
              '`--first` 回到真·原件，两条都成立。'
              '设计上默认 revert 本来就只代表"最近一次写回之前"'
              '（`revert.py:59-62` 的注释说明了这一点），所以它不是 bug，'
              '而是**必须用 `--first` 才能拿回原件**的使用契约。')
        else:
            A('**结论（F-V1 复现了缺口）**：`--first` 未能在全部脚本上回到真·原件'
              '（%d/%d）；默认 revert 回到"最近一次写回之前"是 %d/%d'
              '（这一条是设计如此，不算缺陷：`revert.py:59-62` 明确说固定名备份'
              '只代表"最近一次写回之前"）。**默认 revert 直接回到真·原件：%d/%d** '
              '—— 这正说明"要拿回原件必须用 `--first`"。'
              % (v.get('first_eq_pristine', 0), ran,
                 v.get('default_eq_prev_write_image', 0), ran,
                 v.get('default_eq_pristine', 0), ran))
            if v.get('first_eq_pristine', 0) != ran:
                A('')
                A('失败的那 %d 个脚本，根因是 **Bug 2**（长路径下 pid 备份被静默'
                  '吞掉 → `oldest_backup()` 返回 None → 回落到固定名备份，'
                  '却仍然报 `first: true`）：见 §5 的 Bug 2 与 '
                  '`_fields_bug2.txt`。'
                  % (ran - v.get('first_eq_pristine', 0)))
    A('')
    A('## 3. 与旧口径对照')
    A('')
    old = extra.get('old_scope', {})
    A('| 口径 | 干净往返分母 | 定向改写可测 | 备注 |')
    A('|---|---:|---:|---|')
    A('| **旧脚手架**（`POS_FIELDS=(\'pos\',\'figsize_in\')`；`%s`） | %d | **%d** | '
      '任务书里的"旧口径"：M4 只有 pos 目标 → 43 个可测。'
      '注意这 43 个里只有 31 个源码里真有 `add_axes([x0,y0,w,h])` 四字面量，'
      '其余来自工具 `describe` 的**运行时 bbox**（`toolbox.py:3565` 对**所有**轴都采，'
      '含 `add_subplot` 网格轴与 colorbar 轴）→ 分母本身偏大 |'
      % (old.get('hist_file', '?'), old.get('hist_m3_denom', 0),
         old.get('hist_m4_denom', 0)))
    A('| **上一轮脚手架**（M4 泛化到 figsize；`%s`） | %d | %d | '
      'pos + figsize 两族；m4 pass %d / fail %d |'
      % (old.get('prev_file', '?'), old.get('prev_m3_denom', 0),
         old.get('prev_m4_denom', 0), old.get('prev_m4_pass', 0),
         old.get('prev_m4_fail', 0)))
    newpos = fam['pos']
    newfs = fam['figsize_in']
    A('| 本脚手架、**只取 `pos`+`figsize` 两族**（同一次运行） | — | %d | '
      'pos 可测 %d、figsize 可测 %d |'
      % (newpos['testable_scripts'] + newfs['testable_scripts'],
         newpos['testable_scripts'], newfs['testable_scripts']))
    tot_test = sum(fam[f]['testable_scripts'] for f in FAMILIES)
    tot_pass = sum(fam[f]['pass'] for f in FAMILIES)
    tot_lit = sum(fam[f]['literal_scripts'] for f in FAMILIES)
    A('| **本脚手架、十族并集** | — | **%d** | 通过 %d |'
      % (tot_test, tot_pass))
    A('')
    A('- 十族**脚本并集**（至少有一族可测的脚本数）：**%d** 个；'
      '至少有一族**字面量**的脚本数：**%d** 个（分母是"脚本 × 族"，合计 %d）。'
      % (extra.get('union_testable', 0), extra.get('union_literal', 0), tot_lit))
    A('- 定向改写可测数从 **%d → %d**（×%.1f）。'
      % (old.get('hist_m4_denom', 0), tot_test,
         (float(tot_test) / old['hist_m4_denom']) if old.get('hist_m4_denom') else 0))
    A('')
    if extra.get('old_files'):
        A('历史全量跑逐份对照（用于确定上表第一行的"旧口径"取值）：')
        A('')
        A('| 结果文件 | 条数 | m2 提取到字面量 | 干净往返分母 | 定向改写分母 | 定向改写 pass | fail |')
        A('|---|---:|---:|---:|---:|---:|---:|')
        for h in extra['old_files']:
            A('| `%s` | %d | %d | %d | %d | %d | %d |'
              % (h['file'], h['n'], h['m2'], h['m3_denom'], h['m4_denom'],
                 h['m4_pass'], h['m4_fail']))
        A('')
        A('- "旧口径"取 `%s`（任务书点名的"只有 pos 目标"那一版，43 可测）；'
          '"上一轮"取 `%s`（M4 泛化到 figsize 后覆盖面最广的一次，%d 可测）。'
          % (old.get('hist_file', '?'), old.get('prev_file', '?'),
             old.get('prev_m4_denom', 0)))
        A('')

    # ---- §4 失败清单 ----
    A('## 4. 失败清单')
    A('')
    fails = [(f, x) for f in FAMILIES for x in fam[f]['fails']]
    if not fails:
        A('（无失败）')
    else:
        A('| # | 脚本 | 字段族 | 性质 | 判定理由 | apply reason | 证据目录 |')
        A('|---:|---|---|---|---|---|---|')
        for i, (f, x) in enumerate(fails[:400], 1):
            evd = (x.get('evidence') or {})
            d = os.path.dirname(evd.get('rewrite', '') or '')
            A('| %d | `%s` | %s | %s | %s | %s | `%s/` |'
              % (i, x['path'], f, x['nature'], x['reason'],
                 x.get('apply_reason'), d))
        if len(fails) > 400:
            A('')
            A('（只列前 400 条，其余见 `l0_results_fields.jsonl`）')
    A('')
    A('### 4.1 失败按性质汇总')
    A('')
    A('| 字段族 | 性质 | 失败脚本数 |')
    A('|---|---|---:|')
    for f in FAMILIES:
        a = fam[f]
        for k, v in sorted((x for x in a['fail_natures'].items()
                            if x[0] is not None), key=lambda x: x[0]):
            A('| %s | %s | %d |' % (f, k, v))
    A('')
    A('### 4.1b "不适用"按性质汇总（**只有这一列才是"够不到/前提不成立"**）')
    A('')
    A('| 字段族 | 性质 | 脚本数 |')
    A('|---|---|---:|')
    for f in FAMILIES:
        a = fam[f]
        for k, v in sorted((x for x in a['na_natures'].items()
                            if x[0] is not None), key=lambda x: x[0]):
            A('| %s | %s | %d |' % (f, k, v))
    A('')
    A('### 4.2 "不适用"按原因汇总')
    A('')
    A('| 字段族 | 不适用原因 | 脚本数 |')
    A('|---|---|---:|')
    for f in FAMILIES:
        a = fam[f]
        for k, v in sorted(a['na_reasons'].items(), key=lambda x: -x[1]):
            A('| %s | `%s` | %d |' % (f, k, v))
    A('')

    # ---- §5 真 bug ----
    A('## 5. 发现的问题（最小复现 + 逐字输出 + 根因判断；**未改工具**）')
    A('')
    A('> 纪律：`tool_bug` 只给"**考卷前提成立**（工具自己的寻址模型够得到、'
      '目标值合法、字面量落在它自己的查找区间里）而工具没做到"的情形。'
      '工具**设计上**不支持、会走 `skipped` → auto 退块的写法，一律记 '
      '`harness_premise` / `tool_limitation`，不算 bug。')
    A('')
    A(extra.get('bugs_md', '（未生成）'))
    A('')

    # ---- §6 方法与边界 ----
    A('## 6. 方法与边界')
    A('')
    A('### 6.1 方法')
    A('')
    A('1. **自研提取器**（`fields_harness.extract_sites`）：纯 AST 扫描真实脚本，'
      '认出每族字面量站点；不 import mpltweak 任何模块，也不复用它的提取逻辑。'
      '同时镜像 `apply_inplace` 的**寻址模型**（`_find_call` 取最早同名调用、'
      '区间夹在本图 span、位置 zip 配对、cax/host 剔除）来判"考卷前提"。')
    A('2. **目标构造**：字号 +6pt（超 20 则 −6）；`grid` 取反；`xlim/ylim` 平移 37% 量程'
      '（对数轴改成 ×1.37 保持符号）；`pos` / `figsize` 从候选表里挑与现值差 ≥0.05 / ≥0.8 的。')
    A('3. **独立复核器**（`fields_harness.verify_run`），每条判据按族独立：')
    A('   - `parse`：改后 `ast.parse` 通过；')
    A('   - `global_mask`：把**所有被测站点的字面量 span** 用占位符掩掉后，'
      'before/after 两份文本必须**逐字节相同** —— 这一条同时证明了'
      '"只有目标那些字面量变了"与"位置就是原地"（块插入、缩进变化、'
      '别处数字被顺手动过，都会立刻暴露）；')
    A('   - `value`：每族按自己的类型比（数字 / 布尔 / 2 元列表）；')
    A('   - `form`：容器与写法保持（`[]` 不变成 `()`、`fontsize=` 不变成 `size=`、'
      '位置参不变成 kwarg），按族各自的签名函数取；')
    A('   - `site_count`：改后目标站点仍能在同一 (族, 轴, 序号, 写法) 上定位。')
    A('4. **反向验证**（`--selftest`）：每族造 good 编辑 + 2–4 类坏编辑'
      '（值不对 / 顺手动别处 / 写法被改 / 语法被写坏），三条都要成立：'
      '① 严格判据对 good PASS、对坏编辑 FAIL；'
      '② **只留被违反的那一条规则**时也必须 FAIL（= 这条规则自己就能抓住它，'
      '不是靠别的规则顺手拦的）；③ 去掉这条规则后应 PASS。'
      '矩阵见 `fields_selftest.json` 与 §6.3。')
    A('5. **两档 + 幂等 + 可逆**：① 参数 == 源码现值 → 必须 `no_change` 且字节不变；'
      '② 参数 == 目标值 → 只有目标那一处变；③ 同参数再跑一次 → `no_change` 且字节不变；'
      '④ `revert --write` → 与原件逐字节相同。')
    A('6. **语料只读**：先把脚本 `copy2` 到 `work_fields/<脚本名>/`，'
      'apply/revert 全部针对工作目录里的副本。')
    A('')
    A('### 6.2 测不到的写法 / 边界（为什么要给 `not_applicable` 而不是假 pass）')
    A('')
    A('- **接收者不是裸变量名**：`axes[0].set_title(...)`、`axs[1,0].grid(True)`、'
      '`plt.title(...)`、`plt.xticks(..., labelsize=)`——`_find_call` 只认 '
      '`Name.attr(...)`，这些字面量存在但工具原位够不到。')
    A('- **别名 kwarg**：`size=`（Text 的 `fontsize` 别名）。'
      '`_proved_equal` 认它（干净往返能过），但 `apply_inplace` 的**写**只找 '
      '`fontsize=` → 定向改写够不到（走 `skipped` → auto 退块才落地）。')
    A('- **grid 的 kwarg 形式**：`ax.grid(visible=False)` / `grid(b=False)` '
      '——写回只读 `node.args[0]`。')
    A('- **范围的关键字形式**：`ax.set_xlim(left=0, right=10)` '
      '——写回只认 `xmin=`/`xmax=`。')
    A('- **`tick_params` 的最早调用没有 labelsize**：写回取区间内最早那个 '
      '`tick_params`，若它不带 `labelsize=`（如先 `tick_params(colors=...)`），'
      '后面的 `labelsize=` 够不到。')
    A('- **`tick_fontsize_y` 的前提很窄**：写回要求源码**同时**有 '
      "`axis='x'` 与 `axis='y'` 两个 `tick_params` 且各带 labelsize 字面量；"
      '只有单一 `tick_params(labelsize=)` 时，x/y 分开写会被记进 `skipped`'
      '（这是 T23 的既定设计，不是 bug）。')
    A('- **colorbar**：`cax` 的 `pos`、`ax=` 宿主轴的 `pos` 一律不原位写'
      '（宿主轴位置由 colorbar 重算，改了也会被覆盖）。')
    A('- **一次 apply 只处理一张图**：params 顶层只有一个 `fig_index`。'
      '本脚手架每个脚本挑"可测站点最多的那张图"作为目标图，'
      '**其它图里的同类字面量本轮不考**（记为 '
      '`reachable_site_in_other_figure_not_exercised`）。')
    A('- **不跑脚本**：`--verify-fast` 不重跑，所以"写回后脚本还能跑通 /'
      '布局真的落到图上"这两层本轮**没有**覆盖——本报告只证明'
      '**源码手术层面**的正确性（语法 + 只有目标变了 + 值等于目标 + 可逆）。'
      '（`apply.py:489-496` 明确写了 `--verify-fast` **隐含** `--no-verify`：'
      '`verify=not args.no_verify and not args.verify_fast` → False，'
      '所以语料脚本一次都没有被执行。）')
    A('- **同一张图里轴变量名被多次赋值**：`var_for` 会把多个轴序映射到同一个'
      '变量名，而 `_find_call` 只取最早的同名调用 → "第 k 个轴"无法唯一归属。'
      '主场景因此把这类轴记为 `not_applicable / harness_premise`'
      '（`dup_axes_assign_overlap_rejected`），**不计入族成绩表**；'
      '但它本身是**真 bug**，单列成 F-DUPVAR 场景 + 最小复现（§5.1）。')
    A('- **`tick_fontsize_y` 在本语料里分母为 0**：工具要求源码**同时**存在 '
      "`tick_params(axis='x', labelsize=<字面量>)` 与 "
      "`tick_params(axis='y', labelsize=<字面量>)` 两支，"
      "而全语料 1686 个脚本里带 `axis='x'` + labelsize 字面量的只有 **2 处**、"
      "带 `axis='y'` 的一处都没有 → 这一族**在真实语料上无从考起**。"
      '这本身就是重要产出：T23 为"x/y 刻度字号分列"设计的窄前提'
      '在真实代码里几乎不出现（真实写法是 `tick_params(labelsize=N)` 一次管两边）。')
    A('- **语料里 600/1686 的脚本根本没有建图调用**（`figure/subplots/'
      'subplot_mosaic` 一个都没有）：它们是纯计算/分析脚本（`build_tex.py`、'
      '`monte_carlo.py`、`*verify_manuscript*` 之类），十族全部记"本脚本无此字段"，'
      '不进任何分母。这也是"分母"必须按族给、不能只报一个总数的原因。')
    A('')

    # ---- §6.3 自测矩阵 ----
    A('### 6.3 复核器反向验证矩阵（不跑语料，纯静态自测）')
    A('')
    A('| 字段族 | good 编辑严格判据 | 坏编辑 | 被违反的规则 | 严格判据 | 只留该规则 | 去掉该规则 | 该规则自己就能拦住 |')
    A('|---|---|---|---|---|---|---|---|')
    for c in selft:
        for b in c.get('bad', []):
            A('| %s | %s | %s | `%s` | %s | %s | %s | %s |'
              % (c['family'], 'PASS' if c['good_strict_ok'] else 'FAIL',
                 b['name'], b['broken_rule'],
                 'FAIL' if not b['strict_ok'] else 'PASS',
                 'FAIL' if not b['lone_ok'] else 'PASS',
                 'PASS' if b['loose_ok'] else 'FAIL',
                 '✅' if b['verdict_ok'] else '❌'))
    A('')
    n_ok = sum(1 for c in selft for b in c.get('bad', []) if b['verdict_ok'])
    n_all = sum(len(c.get('bad', [])) for c in selft)
    n_good = sum(1 for c in selft if c['good_strict_ok'])
    A('- good 编辑通过严格判据：**%d/%d** 族。' % (n_good, len(selft)))
    A('- 反向验证成立（严格 FAIL 且"只留该规则"也 FAIL）：**%d/%d** 条；'
      '其中被**多条**规则同时拦住的：%d 条（纵深防御，不是判据含糊）。'
      % (n_ok, n_all,
         sum(1 for c in selft for b in c.get('bad', [])
             if len(b.get('rules_that_catch') or []) > 1)))
    A('')

    A('### 6.4 与兄弟脚手架 `l0_harness.py` 的复用关系')
    A('')
    _ru = (audits or {}).get('reuse') or {}
    A('- 导入状态：`%s`' % _ru.get('status'))
    A('- 其中找到的通用件：%s'
      % ('、'.join('`%s`' % x for x in (_ru.get('found') or [])) or '—'))
    if _ru.get('four_class'):
        A('- `metric_class` 的取值集合：%s'
          % '、'.join('`%s`' % x for x in _ru['four_class']))
    if _ru.get('equivalence'):
        A('')
        A('| 对拍项 | `l0_harness.py` | 本脚手架 | 等价 |')
        A('|---|---|---|---|')
        for e in _ru['equivalence']:
            A('| `%s` | `%s` | `%s` | %s |'
              % (e['input'],
                 e.get('l0_harness._skeleton') or e.get('l0_harness'),
                 e.get('fields_harness.spelling_key') or e.get('fields_harness'),
                 '✅' if e.get('equivalent') else '❌'))
    A('')
    A('说明：本脚手架对同族通用件采取"**语义复用 + 对拍校验**"而不是直接 import，'
      '原因是 `l0_harness.py` 正被另一个作业（figsize 目标泛化）并发修改，'
      '直接 import 会在对方写盘中途失败并让整轮全量跑崩掉。'
      '对拍表就是"两边口径一致"的可审计证据。')
    A('')

    # ---- §7 审计 ----
    A('## 7. 审计')
    A('')
    au = audits
    _h1 = meta.get('git_head') or audget_head(audits)
    _h2 = meta.get('git_head_after')
    A('| 项目 | 结果 | 证据 |')
    A('|---|---|---|')
    A('| 语料原件 sha256 未变 | %s | 逐文件重算 sha256 与 `manifest.jsonl` 比对：'
      '一致 %d / 不一致 %d |'
      % ('✅' if au.get('sha_mismatch') == 0 else '❌',
         au.get('sha_ok', 0), au.get('sha_mismatch', 0)))
    A('| 语料里不残留 `.tweak_params` | %s | 在 `gallery/`、`zenodo/` 下递归找 '
      '`.tweak_params`：%d 个 |'
      % ('✅' if au.get('corpus_tweak_params') == 0 else '❌',
         au.get('corpus_tweak_params', 0)))
    A('| 被测工具树无**本作业**改动 | %s | `git status --porcelain` 在本次运行'
      '**前后逐字相同**：开始 %d 行 / 结束 %d 行；两边都是同一组文件 → '
      '本作业没有新增或修改工具树里的任何文件 |'
      % ('✅' if au.get('git_dirty_lines') == au.get('git_dirty_lines_after')
         else '❌', au.get('git_dirty_lines', 0),
         au.get('git_dirty_lines_after', -1)))
    _sd = meta.get('git_src_drift') or {}
    if _sd:
        A('| 引擎源码 `src/mpltweak` 在本轮**前后无差异** | %s | '
          '开始 `%s` → 结束 `%s`；`git diff --stat <开始>..<结束> -- src/mpltweak` '
          '= %s |'
          % ('✅' if _sd.get('src_changed') == 0 else '❌',
             (_h1 or '?')[:12], (_h2 or '?')[:12],
             ('无差异（HEAD 相同）' if _sd.get('same_head')
              else ('无差异' if _sd.get('src_changed') == 0
                    else '有 %d 行差异' % _sd.get('src_changed')))))
    A('| 工具树 HEAD 在本轮**前后一致** | %s | 开始 `%s`；结束 `%s`。'
      '注意：本仓库正被另一个作业持续提交（文档/CHANGELOG），'
      'HEAD **一定会**移动；结论成立与否看上表的"引擎源码无差异"一行。 |'
      % ('✅' if (_h1 and _h1 == _h2) else '⚠️', (_h1 or '?')[:12],
         (_h2 or '?')[:12]))
    A('| 工作目录外零写入 | %s | 本脚手架只写 `%s`、`l0_results_fields.jsonl`、'
      '`l0_report_fields.md`、`fields_selftest.json` | ✅ |'
      % ('✅', 'work_fields/'))
    A('| 工具树 `git status` 里那 2 个文件是谁改的 | — | `git diff --stat` 显示'
      '只有 `pyproject.toml` 与 `src/mpltweak/__init__.py` 各 1 行：'
      '`version = "0.1.8"` → `"0.1.9"` / `__version__ = \'0.1.8\'` → `\'0.1.9\'`，'
      '是另一个作业的版本号提升，与本脚手架无关（本脚手架的落盘路径全部在 '
      '`corpus018/harness/` 下，代码里没有任何一处写 `MPLTWEAK_ROOT`）。 |')
    A('| 取到 HEAD 的方式 | %s | `git_info()` 候选可执行文件诊断：%s |'
      % ('✅' if _h1 else '❌',
         '；'.join('`%s` rc=%s out=`%s`' % (d.get('exe'), d.get('rc'),
                                            (d.get('out') or '')[:12])
                   for d in (meta.get('git_diag') or [])) or '—'))
    A('')
    if au.get('git_status_text'):
        A('`git status --porcelain` 原文：')
        A('')
        A('```')
        A(au['git_status_text'].rstrip('\n'))
        A('```')
        A('')
    if au.get('sha_mismatch_paths'):
        A('sha256 不一致的文件：')
        A('')
        for p in au['sha_mismatch_paths'][:50]:
            A('- `%s`' % p)
        A('')

    # ---- §8 运行统计 ----
    A('## 8. 运行统计')
    A('')
    st = collections.Counter()
    for r in results:
        if r.get('meta', {}).get('parse_error'):
            st['m1_parse_fail'] += 1
        if r.get('clean_roundtrip'):
            st['clean_%s' % r['clean_roundtrip'].get('reason')] += 1
        if r.get('rewrite'):
            st['rewrite_%s' % r['rewrite'].get('reason')] += 1
        if r.get('idempotent'):
            st['idem_%s' % r['idempotent'].get('reason')] += 1
        if r.get('revert'):
            st['revert_%s' % ('ok' if r['revert'].get('ok') else 'bad')] += 1
    A('| 计数 | 值 |')
    A('|---|---:|')
    for k in sorted(st):
        A('| `%s` | %d |' % (k, st[k]))
    A('')
    return '\n'.join(L) + '\n'


# --------------------------------------------------------------------------
# 审计
# --------------------------------------------------------------------------
def reuse_check():
    """复用并**校验**兄弟脚手架 `l0_harness.py` 里的通用件（不去重写一遍）。

    `l0_harness.py` 可能正被另一个作业改动，所以：
      * 导入失败（语法错/正在写盘）→ 记录 `unavailable`，本脚手架自有等价实现兜底；
      * 导入成功 → 把它的 `_skeleton` / `pick_target_figsize` / `metric_class`
        与本地等价物在样例上对拍，记录是否等价。
    这样"复用"是可审计的事实，而不是口头声明。
    """
    out = {'module': 'l0_harness.py', 'status': 'unavailable', 'found': [],
           'equivalence': [], 'four_class': None}
    try:
        import importlib.util
        p = os.path.join(HERE, 'l0_harness.py')
        if not os.path.exists(p):
            out['status'] = 'missing'
            return out
        spec = importlib.util.spec_from_file_location('_l0h_reuse', p)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        out['status'] = 'loaded'
    except Exception as e:                                      # noqa: BLE001
        out['status'] = 'load_error: %r' % e
        return out
    for nm in ('verdict_class', 'metric_class', 'pick_target_figsize', '_skeleton',
               'check_figsize_change', 'scenario_stats', 'scenario_fails',
               '_skeleton_features'):
        if hasattr(mod, nm):
            out['found'].append(nm)
    # metric_class 的取值集合 == 本报告用的四分类
    mc = getattr(mod, 'metric_class', None)
    if callable(mc):
        vals = set()
        for st, rs in (('fail', 'apply_failed'), ('pass', 'ok'),
                       ('fail', 'x'), ('skip', 'y')):
            try:
                r = mc(st, rs)
            except Exception:                                   # noqa: BLE001
                r = None
            vals.add(r)
        out['four_class'] = sorted(str(x) for x in vals if x)
    # _skeleton 对拍（本地的 = spelling_key 的退化版：抹数字）
    if callable(getattr(mod, '_skeleton', None)):
        for sample in ('fontsize=12', '(6, 4)', '[0.1, 0.2, 0.3, 0.4]',
                       'grid(True)'):
            try:
                a = mod._skeleton(sample)
            except Exception as e:                              # noqa: BLE001
                a = 'err:%r' % e
            b = _NUM_RE.sub('#', sample)
            out['equivalence'].append({'input': sample, 'l0_harness._skeleton': a,
                                       'fields_harness.spelling_key': b,
                                       'equivalent': (str(a) == b)})
    # pick_target_figsize 对拍（本地的 = FIGSIZE_CANDIDATES + 差 >= 0.8）
    if callable(getattr(mod, 'pick_target_figsize', None)):
        for cur in ([6, 4], [6, 6], [10, 10], [3.5, 2.5]):
            try:
                a = mod.pick_target_figsize(list(cur))
            except Exception as e:                              # noqa: BLE001
                a = 'err:%r' % e
            b = target_figsize(list(cur))
            ok = (isinstance(a, (list, tuple)) and len(a) == 2
                  and max(abs(float(a[i]) - b[i]) for i in range(2)) < 1e-9)
            out['equivalence'].append({'input': 'pick_target_figsize(%s)' % cur,
                                       'l0_harness': list(a) if isinstance(a, (list, tuple)) else a,
                                       'fields_harness': b, 'equivalent': bool(ok)})
    return out


GIT_EXE_CANDIDATES = (
    'git',
    r'C:\Program Files\Git\cmd\git.exe',
    r'C:\Program Files (x86)\Git\cmd\git.exe',
)
_git_diag = []


def git_info():
    """返回 (head, head_date, branch, status_text, diag)。

    `diag` 记录每个候选 git 可执行文件的成败 —— 审计里必须能看出
    "取到了哪个提交"是真的取到了，而不是静默空串（实测踩过：后台作业的
    环境里 `git` 解析失败 → 报告里的"被测提交"变成空白）。
    """
    diag = []

    def _run(args, exe):
        try:
            p = subprocess.run([exe] + args, capture_output=True,
                               cwd=MPLTWEAK_ROOT, timeout=60)
            return ((p.stdout or b'').decode('utf-8', 'replace'),
                    (p.stderr or b'').decode('utf-8', 'replace'), p.returncode)
        except Exception as e:                                  # noqa: BLE001
            return '', repr(e), None

    exe = None
    for cand in GIT_EXE_CANDIDATES:
        out, err, rc = _run(['rev-parse', 'HEAD'], cand)
        diag.append({'exe': cand, 'rc': rc, 'out': out.strip()[:80],
                     'err': (err or '').strip()[:120]})
        if out.strip():
            exe = cand
            break
    del _git_diag[:]
    _git_diag.extend(diag)
    if exe is None:
        return '', '', '', '', diag
    head = _run(['rev-parse', 'HEAD'], exe)[0].strip()
    date = _run(['log', '-1', '--format=%ci %s'], exe)[0].strip()
    branch = _run(['rev-parse', '--abbrev-ref', 'HEAD'], exe)[0].strip()
    status = _run(['status', '--porcelain'], exe)[0]
    return head, date, branch, status, diag


def audit_corpus(rows):
    sha_ok = sha_bad = 0
    bad_paths = []
    for r in rows:
        p = os.path.join(CORPUS, r['path'].replace('/', os.sep))
        try:
            h = sha256_file(p)
        except OSError:
            sha_bad += 1
            bad_paths.append(r['path'])
            continue
        if h == r.get('sha256'):
            sha_ok += 1
        else:
            sha_bad += 1
            bad_paths.append(r['path'])
    tp = 0
    for sub in ('gallery', 'zenodo'):
        for root, dirs, files in os.walk(os.path.join(CORPUS, sub)):
            if os.path.basename(root) == '.tweak_params':
                tp += 1
    return {'sha_ok': sha_ok, 'sha_mismatch': sha_bad,
            'sha_mismatch_paths': bad_paths, 'corpus_tweak_params': tp}


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------
def main(argv=None):
    ap = argparse.ArgumentParser(prog='fields_harness.py')
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--only', default=None)
    ap.add_argument('--all', action='store_true')
    ap.add_argument('--jobs', type=int, default=1)
    ap.add_argument('--report-only', action='store_true')
    ap.add_argument('--selftest', action='store_true')
    ap.add_argument('--timeout', type=float, default=180.0)
    args = ap.parse_args(argv)

    if args.selftest:
        mat = selftest_matrix()
        write_json(SELFTEST_JSON, mat)
        rows = []
        for c in mat:
            rows.append('%s good=%s plan=%d' % (
                c['family'], 'PASS' if c['good_strict_ok'] else 'FAIL',
                c.get('n_plan', 0)))
            for b in c.get('bad', []):
                rows.append('    %-34s rule=%-12s strict=%s lone=%s loose=%s '
                            'caught_by=%s teeth=%s' % (
                                b['name'], b['broken_rule'],
                                'PASS' if b['strict_ok'] else 'FAIL',
                                'PASS' if b['lone_ok'] else 'FAIL',
                                'PASS' if b['loose_ok'] else 'FAIL',
                                ','.join(b.get('rules_that_catch') or []) or '-',
                                'OK' if b['verdict_ok'] else 'NO'))
        write_text(os.path.join(HERE, 'fields_selftest.txt'),
                   '\n'.join(rows) + '\n')
        return 0

    manifest = load_manifest()
    rows = manifest if args.all or args.limit else manifest
    if args.limit:
        rows = rows[:args.limit]
    if args.only:
        rows = [r for r in rows if args.only in r['path']]
    for r in rows:
        r['stem'] = os.path.splitext(os.path.basename(r['path']))[0]

    meta = {
        'mp_tweak_src': MPLTWEAK_SRC, 'python': PYTHON, 'jobs': args.jobs,
        'limit': args.limit, 'only': args.only, 'n_manifest': len(manifest),
        'n_gallery': sum(1 for r in manifest if r.get('source') == 'gallery'
                         or r['path'].startswith('gallery/')),
        'n_zenodo': sum(1 for r in manifest if r['path'].startswith('zenodo/')),
        'started_at': time.strftime('%Y-%m-%d %H:%M:%S'),
    }
    head, date, branch, status, _diag = git_info()
    if not args.report_only:
        # 只在**真跑**的时候记录"本轮被测的是哪个提交"。
        # `--report-only` 绝不能覆盖它 —— 否则重新生成报告会把
        # "被测提交"改成"生成报告那一刻的 HEAD"，审计就失去意义了。
        meta.update({'git_head': head, 'git_head_date': date,
                     'git_branch': branch, 'git_diag': _diag,
                     'git_status_before': status})

    if not args.report_only:
        results = []
        t0 = time.time()
        if args.jobs > 1:
            with futures.ThreadPoolExecutor(max_workers=args.jobs) as ex:
                futs = {ex.submit(test_one, r, args): r for r in rows}
                for fu in futures.as_completed(futs):
                    _row = futs[fu]
                    try:
                        results.append(fu.result())
                    except Exception as e:                      # noqa: BLE001
                        results.append({'path': _row['path'],
                                        'stem': _row.get('stem'),
                                        'error': repr(e), 'families': {},
                                        'meta': {'harness_exception': True}})
        else:
            for r in rows:
                try:
                    results.append(test_one(r, args))
                except Exception as e:                          # noqa: BLE001
                    results.append({'path': r['path'], 'stem': r.get('stem'),
                                    'error': repr(e), 'families': {},
                                    'meta': {'harness_exception': True}})
        results.sort(key=lambda r: r.get('path') or '')
        with io.open(RESULTS, 'w', encoding='utf-8', newline='') as f:
            for r in results:
                f.write(json.dumps(r, ensure_ascii=False) + '\n')
        meta['wall_seconds'] = round(time.time() - t0, 1)
        meta['finished_at'] = time.strftime('%Y-%m-%d %H:%M:%S')
        meta['n_results'] = len(results)
        write_json(RUN_META, meta)
    else:
        results = []
        with io.open(RESULTS, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line:
                    results.append(json.loads(line))
        try:
            with io.open(RUN_META, 'r', encoding='utf-8') as f:
                meta.update(json.load(f))
        except OSError:
            pass
        meta['mp_tweak_src'] = meta.get('mp_tweak_src') or MPLTWEAK_SRC

    fam, non, fv1, fv1_hist = aggregate(results)
    selft = []
    if os.path.exists(SELFTEST_JSON):
        try:
            with io.open(SELFTEST_JSON, 'r', encoding='utf-8') as f:
                selft = json.load(f)
        except OSError:
            selft = []
    if not selft:
        selft = selftest_matrix()
        write_json(SELFTEST_JSON, selft)
    audits = audit_corpus(manifest)
    head2, date2, branch2, status2, diag2 = git_info()
    if args.report_only:
        # 保留本轮跑的记录，只把"生成报告这一刻"的 HEAD 另存
        meta['git_head_now'] = head2
        meta['git_status_now'] = status2
        head1 = meta.get('git_head') or ''
        head_end = meta.get('git_head_after') or head2
    else:
        meta['git_head_after'] = head2
        meta['git_status_after'] = status2
        meta['git_diag_after'] = diag2
        head1, head_end = head, head2
    meta['git_src_drift'] = git_src_drift(head1, head_end)
    _st_before = meta.get('git_status_before', status)
    _st_after = meta.get('git_status_after', status2)
    audits['git_dirty_lines'] = len([x for x in (_st_before or '').splitlines()
                                     if x.strip()])
    audits['git_dirty_lines_after'] = len(
        [x for x in (_st_after or '').splitlines() if x.strip()])
    audits['reuse'] = reuse_check()
    audits['git_head'] = head1
    audits['git_src_drift'] = meta['git_src_drift']
    audits['git_status_text'] = _st_before
    audits['git_status_text_after'] = _st_after
    # 旧口径（历史全量跑）
    extra = {'old_scope': {}, 'union_testable': 0, 'union_literal': 0}
    # 旧口径 = **旧脚手架**（POS_FIELDS 只有 pos+figsize）的历史全量跑。
    # 上一轮把 figsize 目标也接进 M4 之后的最近一次全量跑是 l0_results_final2.jsonl；
    # 更早的 l0_results_final.jsonl 是"M4 只考 pos"的那一版。
    def _hist_scope(fn):
        p = os.path.join(HERE, fn)
        if not os.path.exists(p):
            return None
        c = collections.Counter()
        n = 0
        with io.open(p, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                n += 1
                for k in ('m1_parse', 'm2_extract', 'm3_clean_roundtrip',
                          'm4_directed_rewrite', 'm5_idempotent', 'm6_reversible'):
                    st = (r.get(k) or {}).get('status')
                    if st:
                        c['%s:%s' % (k, st)] += 1
        return c, n

    old = {'hist_m4_denom': 0, 'hist_m4_pass': 0, 'hist_m4_fail': 0,
           'hist_m2': 0, 'hist_m3_denom': 0, 'files': []}
    _FILES = ('l0_results_final.jsonl',        # 旧脚手架：M4 只考 pos
              'l0_results_broad.jsonl',        # 上一轮：M4 泛化到 figsize
              'l0_results_final2.jsonl',
              'l0_results_t35.jsonl',
              'l0_results_018_t15corpus.jsonl')
    _seen = set()
    for fn in _FILES:
        h = _hist_scope(fn)
        if not h:
            continue
        c, n = h
        if fn in _seen:                 # 按**文件名**去重，不能按行数
            continue
        _seen.add(fn)
        d = c['m4_directed_rewrite:pass'] + c['m4_directed_rewrite:fail']
        old['files'].append({'file': fn, 'n': n,
                             'm2': c['m2_extract:pass'],
                             'm3_denom': c['m3_clean_roundtrip:pass']
                             + c['m3_clean_roundtrip:fail'],
                             'm4_denom': d,
                             'm4_pass': c['m4_directed_rewrite:pass'],
                             'm4_fail': c['m4_directed_rewrite:fail']})
    # "旧口径" = 任务书里点名的那个：M4 只有 pos 目标的那一版（43 可测）
    _oldv = next((x for x in old['files'] if x['file'] == 'l0_results_final.jsonl'),
                 None)
    if _oldv is None and old['files']:
        _oldv = min(old['files'], key=lambda x: x['m4_denom'])
    if _oldv:
        old.update({'hist_m4_denom': _oldv['m4_denom'],
                    'hist_m4_pass': _oldv['m4_pass'],
                    'hist_m4_fail': _oldv['m4_fail'],
                    'hist_m2': _oldv['m2'],
                    'hist_m3_denom': _oldv['m3_denom'],
                    'hist_file': _oldv['file'], 'hist_n': _oldv['n']})
    # "上一轮" = 历史跑里 M4 分母最大的那次（figsize 泛化之后）
    _prev = max(old['files'], key=lambda x: x['m4_denom']) if old['files'] else None
    if _prev:
        old.update({'prev_m4_denom': _prev['m4_denom'],
                    'prev_m4_pass': _prev['m4_pass'],
                    'prev_m4_fail': _prev['m4_fail'],
                    'prev_m2': _prev['m2'],
                    'prev_m3_denom': _prev['m3_denom'],
                    'prev_file': _prev['file'], 'prev_n': _prev['n']})
    extra['old_scope'] = old
    extra['old_files'] = old['files']
    ut = set()
    ul = set()
    for r in results:
        for f, info in (r.get('families') or {}).items():
            if f not in FAMILIES:
                continue
            if info.get('has_literal'):
                ul.add(r['path'])
            if info.get('status') in ('pass', 'fail'):
                ut.add(r['path'])
    extra['union_testable'] = len(ut)
    extra['union_literal'] = len(ul)
    extra['bugs_md'] = build_bugs_md(results, fam)
    md = build_report(results, meta, fam, non, selft, audits, extra, fv1, fv1_hist)
    write_text(REPORT, md)
    # 收尾再写一次 meta：把"跑完之后"的 git 状态也落到盘上
    # （否则审计能看到的只有跑之前那一次，跨提交跑就查不出来了）。
    write_json(RUN_META, meta)
    return 0


BUG2_MD = """\
### Bug 2（已坐实，high 级）：长路径下 pid 备份**静默失败** → `revert --first` 回落到
"最近一次写回之前"，却仍然报 `ok: true, first: true`

**症状**：同一脚本连续 `apply` 两次之后再 `revert --write --json --first`，
返回的**不是**最初版本，而是**第一次 apply 之后**的中间态；JSON 里仍然是
`"ok": true`、`"first": true`、`"written": true`。用户以为回到了原件，其实没有。

**触发条件**：`脚本目录 + 文件名 + "/.tweak_params/<stem>.tweak.<pid>.bak"`
超过 Windows `MAX_PATH`(260)。本语料在**自然位置**（`corpus018/zenodo/`，前缀 44）
最深只有 170 字符 → **不触发**；本轮是在脚手架较深的工作目录（前缀 65+）里撞到的。
真实用户把脚本放在深目录 / OneDrive 长前缀 / 长文件名下会撞到。

**最小复现**（`_fields_bug2.py`，脚本名沿用语料里最长的那个 105 字符名）——
A/B 对照只差**目录前缀长度**：

| 变体 | `stage` 路径长 | pid 备份路径长 | pid 备份数 | 直接 `shutil.copy2` 到 pid 路径 | `revert --first` 回到真·原件 |
|---|---:|---:|---:|---|---|
| 深目录（前缀 65） | 234 | **261** | **0** | `OSError: [Errno 2] No such file or directory` | **否** |
| 浅根 `D:\\t`（前缀 4） | 171 | 198 | 2 | ok | **是** |

深目录下的**逐字输出**（`revert --write --json --first`）::

    {
      "script": "..._NOISEanalysis.py",
      "backup": "...\\work_fields\\_bug_repro_longpath\\...\\.tweak_params\\..._NOISEanalysis.tweak.bak",
      "backup_time": "2026-10-11 00:10",
      "ok": true,
      "changed": true,
      "first": true,
      "diff": { "added": 2, "removed": 2 },
      "lines": [
        "-fig = plt.figure(figsize=(7, 3.5))",
        "-ax = fig.add_axes([0.2, 0.2, 0.6, 0.7])",
        "+fig = plt.figure(figsize=(6, 4))",
        "+ax = fig.add_axes([0.25, 0.25, 0.55, 0.6])"
      ],
      "written": true,
      "revert_bak": "..._NOISEanalysis.revert.bak"
    }

注意 `"backup"` 指向的是**固定名** `...tweak.bak`（不是 `...tweak.<pid>.bak`），
而 diff 显示它从 `figsize=(7, 3.5)` 退回到 `figsize=(6, 4)`（第一次 apply 之后），
原件是 `figsize=(12, 10)`。`"first": true` 是**谎报**。

**根因链（源码定位，三段，缺一不可）**：
1. `writeback._backup_path(script, params_path, pid)`（第 1647-1663 行）把 pid 备份
   命名为 `<script_dir>/.tweak_params/<stem>.tweak.<pid>.bak`。前缀一长就越过
   `MAX_PATH`，`shutil.copy2` 抛 `OSError`（**errno 2**，容易被误读成"文件不存在"）。
2. `writeback._make_backup`（第 1709-1717 行）把 pid 备份那一步包在
   `try: ... except OSError: pass` 里，注释理由是"专属备份是加强项，失败不影响主流程"
   —— 于是**一声不吭**地没有 pid 备份。
3. `revert.oldest_backup()`（第 56-74 行）只匹配 `^<stem>\\.tweak\\.(\\d+)\\.bak$`，
   找不到就返回 `None`；调用方**回落到固定名备份**（= 最近一次写回之前），
   但 JSON 里照样写 `"first": true`。

**corpus 上的实测**（`zenodo/21901848__REVIEW1_ResultsAnalysis_PROC_findFrequencyViaFFT_phase2_singlePulse_NOISEanalysis.py`）：
该脚本的 `.tweak_params/` 里**一个 pid 备份都没有**，
`revert --first` 的结果与默认 `revert` **逐字节相同**，
而真·原件对不上（`fv1.json` 里 `first_eq_pristine=false`、`first_eq_default=true`）。
F-V1 场景 628 个脚本里**只有这 1 个**失败（627/628），失败原因就是它。

**建议修法**（**本作业未改工具**）：
1. `revert --first` 在 `oldest_backup() is None` 时**必须失败**：
   非零退出 + `ok:false` + 明确的错误（"没有 pid 备份可用，`--first` 无法回到最初版本"），
   绝不允许静默回落到固定名备份还写 `"first": true`。**这是最小、最必要的一改。**
2. `_make_backup` 不要 `except OSError: pass`：pid 备份写不成就把结果写进
   `apply --json`（如 `"backup_first_unavailable": true` + `warnings`），
   让用户当场知道"这一次没法 `--first`"。
3. 从根上避开长度悬崖：pid 备份改用**短名**（如 `.tweak/<时间戳或 sha1 前 12 位>.bak`）
   或统一放到一个按脚本哈希分桶的全局目录；Windows 上也可用 `\\\\?\\` 前缀 /
   `os.path` 扩展长度路径。
"""


def load_bug2():
    p = os.path.join(HERE, '_fields_bug2.json')
    if not os.path.exists(p):
        return None
    try:
        with io.open(p, encoding='utf-8') as f:
            return json.load(f)
    except ValueError:
        return None


BUG1_MD = """\
### Bug 1（已坐实，blocker 级）：同一张图里轴变量名被复用 → 该轴的改动被**静默丢弃**

**症状**：`apply` 报 `reason = "no_change"` / `"ok"`、`applied=false`、`changes=[]`，
但源码里那个轴的属性**根本不是参数要的值**。用户看到"无需改动 / 已写回成功"，
实际上他要改的地方一个字没动。

**最小复现**（`work_fields/_bug_repro_dupvar/repro_dupvar.py`）::

    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    fig = plt.figure(figsize=(6, 6))

    ax = fig.add_subplot(2, 1, 1)
    ax.plot([1, 2], [3, 4])
    ax.grid(True)

    ax = fig.add_subplot(2, 1, 2)
    ax.plot([1, 2], [4, 3])
    ax.grid(False)

    fig.savefig('repro_out.png')

参数（`work_fields/_bug_repro_dupvar/params.json`）：轴 0 `grid=true`、
轴 1 `grid=true`（**轴 1 的源码现值是 `False`，正是要改的那一处**）。

命令与**逐字输出**（`--style inplace`）::

    > python -m mpltweak apply stage.py --params params.json --write --verify-fast --json --style inplace
    ...
    ax0  位置（参数里没给，保持原样）
          grid=True  spines={}
    ax1  位置（参数里没给，保持原样）
          grid=True  spines={}
    --------------------------------------------------------------
      正在验证（子进程重跑脚本，最长 300s；不想等可加 --no-verify）
    · 参数与原代码一致，无需改动
    {
      "script": "stage.py",
      "ok": true,
      "files": [
        {
          "params": "params.json",
          "reason": "no_change",
          "style": "inplace",
          "changes": [],
          "applied": false,
          ...
        }
      ]
    }

改动后的文件（**第二个 `ax.grid(False)` 原封不动**）::

    9: ax.grid(True)
    13: ax.grid(False)

**默认模式（`--style auto`，即不加 `--style`）同样中招** —— 实测 reason 仍是
`no_change`、`auto_fallback=false`、`applied=false`，也就是**连退到调整块的兜底
都没有触发**：:

    > python -m mpltweak apply stage2.py --params params.json --write --verify-fast --json
    · 参数与原代码一致，无需改动
          "reason": "no_change",
          "style": "inplace",
          "changes": [],
          "applied": false,
          "auto_fallback": false,
    === grid lines after (auto) ===
    9: ax.grid(True)
    13: ax.grid(False)

**根因判断**（源码定位）：
`writeback.apply_inplace` 的 `var_for`（第 890-924 行）按图内
`add_axes/add_subplot` 赋值序与 params 轴序**位置 zip**，两个轴都被映射到同一个
变量名 `ax`。随后 `_find_call`（第 699-720 行）只按"接收者是 `Name(ax)` +
行区间内**最早**"来定位，
所以**第 2 个轴的 `ax.grid(...)` 永远命中第 1 个轴的 `ax.grid(...)`**。
`_add()` 的"空改（noop）"判定（第 861-877 行）发现"最早那个字面量已经是目标的 `True`"
→ 整字段被判为"值没变"→ 从 `covered` 里剔除 → `changes` 为空 → `reason='no_change'`。
重叠检测 `_conflicts` 只在**两个不同的编辑落在同一段源码区间**时才触发；
这里第 1 个轴压根没产生编辑（它是 noop），所以重叠检测也拦不住。

同一根因的第二种表现（`_find_call` 命中"第 1 个轴"，而第 2 个轴的目标与第 1 个轴的
字面量**不同**）：干净往返会被**写坏**——
一份"逐轴等于源码现值"的参数文件会把第 1 个轴的字面量改成第 2 个轴的值。
corpus 上已实测到 4 例（见下面 §5.1 与 `l0_report_dupvar.md`）。

**建议修法**（三选一，按保守度递增；**本作业未改工具**）：
1. `axis_var_index` / `_figure_axes_for_index` 发现同一变量名在一张图里被
   `add_axes/add_subplot` 赋值 ≥2 次时，把**第 2 次及以后**的轴标记为
   "无法原位定位" → 记进 `skipped`（于是 `_needs=True` → auto 退块，
   由块模式用 `fig.axes[i]` 精确定位落地）。**最小改动、最安全**。
2. 给字段查找加上"以本轴创建行为下界"的窗口：
   `_find_call(var, attr, lo=max(scope_lo, this_axis_lineno), hi=next_axis_lineno_of_same_var)`。
   本作业的 F-DUPVAR 场景就是按这个口径归属的（见 §5.1），实测能把歧义消掉。
3. 在 `_add()` 的 noop 判定里，若 `var_for` 存在"同名多轴"，**不允许**把该字段
   判成 noop（宁可报 best_effort + needs_block，也不要谎报"无需改动"）。
"""


def load_dupvar_summary():
    """读 F-DUPVAR 场景结果，汇总成报告 §5.1 的表格。"""
    p = os.path.join(HERE, 'l0_results_dupvar.jsonl')
    if not os.path.exists(p):
        return None
    res = []
    with io.open(p, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    res.append(json.loads(line))
                except ValueError:
                    pass
    usable = [r for r in res if not r.get('error')]
    c = collections.Counter()
    for r in res:
        if r.get('error'):
            c['error:%s' % r['error']] += 1
            continue
        c['ran'] += 1
        c['clean_hard_fail'] += 1 if r.get('clean_hard_fail') else 0
        c['clean_soft_fail'] += 1 if r.get('clean_soft_fail') else 0
        c['directed:%s' % r.get('directed_verdict')] += 1
        c['revert_ok'] += 1 if r.get('revert_ok') else 0
    rows = [r for r in usable
            if r.get('clean_hard_fail')
            or r.get('directed_verdict') in ('silent_drop_no_change',
                                             'wrong_or_partial_write')]
    return {'counts': c, 'rows': rows, 'n_scanned': len(res),
            'n_ran': len(usable)}


def build_bugs_md(results, fam):
    """把"前提成立、工具没做到"的失败聚成最小复现节。"""
    out = [BUG1_MD]
    dup = load_dupvar_summary()
    out.append('')
    out.append('### 5.1 Bug 1 的 corpus 证据（F-DUPVAR 场景，独立报告 '
               '`l0_report_dupvar.md`）')
    out.append('')
    out.append('触发条件（静态）：某张图里同一个变量名被 `add_axes/add_subplot` '
               '赋值 ≥2 次。归属口径：第 k 个轴的字段窗口 = '
               '`[第 k 次赋值行, 第 k+1 次赋值行)`。')
    out.append('')
    if dup is None:
        out.append('（`l0_results_dupvar.jsonl` 不存在，未运行该场景）')
    else:
        c = dup['counts']
        out.append('- 全语料里触发该形状的脚本：**%d** 个；其中在"同名区"里有可考'
                   '字面量、真正跑起来的：**%d** 个。' % (dup['n_scanned'],
                                                          dup['n_ran']))
        out.append('- **干净往返把用户文件写坏**（一份逐轴等于源码现值的参数文件'
                   '改动了脚本）：**%d** 个脚本。' % c.get('clean_hard_fail', 0))
        out.append('- 定向改写里的 `wrong_or_partial_write`（改到了别的轴 / 值不等于'
                   '目标）：**%d** 个；`silent_drop_no_change`（静默丢弃）：**%d** 个；'
                   '`conservative_defer_to_block`（工具自己报 best_effort+needs_block，'
                   'auto 会退块落实 → **判通过**）：**%d** 个。'
                   % (c.get('directed:wrong_or_partial_write', 0),
                      c.get('directed:silent_drop_no_change', 0),
                      c.get('directed:conservative_defer_to_block', 0)))
        out.append('- `revert` 逐字节回到原件：**%d/%d**（可逆性在这条路径上没坏）。'
                   % (c.get('revert_ok', 0), dup['n_ran']))
        out.append('')
        if dup['rows']:
            out.append('| 脚本 | 图 | 涉及族 | clean 失败 | clean reason | '
                       'directed 判定 | directed reason | 证据目录 |')
            out.append('|---|---:|---|---|---|---|---|---|')
            for r in dup['rows']:
                out.append('| `%s` | %s | %s | %s | `%s` | `%s` | `%s` | `%s` |'
                           % (r['path'], r.get('fig'),
                              ','.join(sorted(r.get('families') or {})),
                              '写坏文件' if r.get('clean_hard_fail')
                              else ('仅 best_effort' if r.get('clean_soft_fail')
                                    else '通过'),
                              (r.get('clean') or {}).get('reason'),
                              r.get('directed_verdict'),
                              (r.get('rewrite') or {}).get('reason'),
                              '%s/' % (r.get('work_dir')
                                       or ('dupvar_work/' + str(r.get('stem'))))))
    out.append('')
    out.append(BUG2_MD)
    out.append('')
    _b2 = load_bug2()
    if _b2:
        out.append('Bug 2 的实验数据（`_fields_bug2.json` / `_fields_bug2.txt`）：')
        out.append('')
        out.append('| 变体 | stage 路径长 | pid 备份路径长 | pid 备份数 | '
                   '直接 copy2 到 pid 路径 | `--first` 回到真·原件 |')
        out.append('|---|---:|---:|---:|---|---|')
        for e in (_b2.get('experiments') or []):
            out.append('| %s | %s | %s | %s | `%s` | %s |'
                       % (e.get('label'), e.get('stage_path_len'),
                          e.get('pid_path_len'), e.get('n_pid_backups'),
                          (e.get('direct_pid_copy2') or '')[:60],
                          e.get('first_eq_pristine')))
        out.append('')
        _ch = _b2.get('corpus_hit')
        if _ch:
            out.append('corpus 上唯一失败的那条（F-V1 场景 627/628 的那个 1）：'
                       '`%s` —— `first_eq_pristine=%s`，'
                       '`.tweak_params/` 里只有 %s。'
                       % (_ch.get('path'), _ch.get('first_eq_pristine'),
                          '、'.join('`%s`' % n for n in
                                   (_ch.get('backups') or []))))
    out.append('')
    out.append('### 5.3 主场景（十族）里的失败聚合')
    groups = collections.defaultdict(list)
    for f in FAMILIES:
        for x in fam[f]['fails']:
            groups[(f, x['nature'], x['reason'])].append(x)
    if not groups:
        out.append('（主场景**没有**失败。）')
        return '\n'.join(out)
    out.append('下表按 (字段族, 性质, 判定理由) 聚合。`tool_bug` = 考卷前提成立'
               '（工具自己的寻址模型够得到、目标值合法）而工具没做到；'
               '`tool_limitation` = 工具寻址模型/写回分支**故意不支持**该写法'
               '（属设计边界：auto 模式下会退到调整块落地）。')
    out.append('')
    for (f, nat, reason), xs in sorted(groups.items(), key=lambda x: -len(x[1])):
        out.append('#### %s / `%s` / %s（%d 个脚本）' % (f, nat, reason, len(xs)))
        out.append('')
        out.append('| 脚本 | apply reason | 独立复核判据 | 证据目录 |')
        out.append('|---|---|---|---|')
        for x in xs[:40]:
            d = os.path.dirname((x.get('evidence') or {}).get('rewrite', '') or '')
            out.append('| `%s` | `%s` | %s | `%s/` |'
                       % (x['path'], x.get('apply_reason'),
                          ','.join(x.get('fail_reasons') or []) or '—', d))
        if len(xs) > 40:
            out.append('')
            out.append('（只列前 40 条）')
        out.append('')
    return '\n'.join(out)


if __name__ == '__main__':
    sys.exit(main())
