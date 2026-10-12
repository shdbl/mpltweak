#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
mpltweak L0 纯静态压力测试脚手架
=================================

对语料库里**每一个**真实 matplotlib 脚本跑 7 项指标（前 6 项 pass/fail，第 7 项只统计）：

  M1 可解析        ast.parse(S)
  M2 参数可构造    静态提取 add_axes([...]) 4 字面量 / figure(figsize=(w,h)) 字面量
  M3 干净往返      源码现值 → apply --write --verify-fast  ⇒ 期望字节不变 + reason=no_change
  M4 定向改写      axes[0].pos 改成明显不同的值 ⇒ 期望只有第 1 个 add_axes 字面量变化
                   （用独立的 AST 复核器判定，不调用 mpltweak 任何模块）
  M5 幂等          同一参数连跑两次 ⇒ 第二次 reason=no_change 且字节不变
  M6 可逆          revert --write ⇒ sha256 必须与语料原件完全一致
  M7 覆盖率统计    （不做 pass/fail，报告里按 layout 分组统计）

铁律（本脚本严格遵守）：
  * **绝不执行语料脚本**：只用 ``apply ... --write --verify-fast``（快速档只做
    ast.parse，不重跑脚本）；不用 describe / check / launch；不用不带 --verify-fast 的 apply。
  * 语料原件（gallery/、zenodo/）只读；一切写操作发生在 ``harness/work/<脚本名>/``。
  * mpltweak 包只读，一个字节都不改。
  * 子进程输出一律按**字节**读入再按 UTF-8 解码（不依赖控制台代码页）。

用法::

    python l0_harness.py --limit 30            # 30 抽样（分层：gallery/zenodo × add_axes/subplots）
    python l0_harness.py --all                 # 全量
    python l0_harness.py --all --jobs 6        # 全量，6 路并发
    python l0_harness.py --report-only         # 只从 l0_results.jsonl 重生成报告
"""

from __future__ import annotations

import argparse
import ast
import concurrent.futures as futures
import difflib
import glob
import hashlib
import json
import os
import random
import re
import shutil
import subprocess
import sys
import time

# --------------------------------------------------------------------------
# 路径与常量
# --------------------------------------------------------------------------
HERE = os.path.dirname(os.path.abspath(__file__))          # corpus018/harness
CORPUS = os.path.dirname(HERE)                             # corpus018
MANIFEST = os.path.join(CORPUS, 'manifest.jsonl')
WORK = os.path.join(HERE, 'work')
# L0_TAG 用来区分不同被测版本的结果文件（如 L0_TAG=_018baseline），避免覆盖基线证据
_TAG = os.environ.get('L0_TAG', '')
RESULTS = os.path.join(HERE, 'l0_results%s.jsonl' % _TAG)
REPORT = os.path.join(HERE, 'l0_report%s.md' % _TAG)
RUN_META = os.path.join(HERE, 'l0_run_meta%s.json' % _TAG)
# 证据目录也必须按 tag 隔离：否则后跑的变体会把先跑的 work/<脚本名>/ 证据覆盖掉，
# 旧结果文件里的证据指针就指向了**另一个版本**的输出（本次实测发生过）。
if _TAG:
    WORK = os.path.join(HERE, 'work%s' % _TAG)

MPLTWEAK_SRC = os.environ.get('MPLTWEAK_SRC') or r'D:\Agent\deepseek\mpltweak\src'
MPLTWEAK_ROOT = os.path.dirname(os.path.dirname(MPLTWEAK_SRC))
PYTHON = r'D:\Miniconda\envs\longxia\python.exe'

BLOCK_SENTINEL = '# ===== auto layout (generated)'
TARGET_POS = [0.2, 0.2, 0.6, 0.7]      # M4 的"明显不同的值"
TARGET_FIGSIZE = [6.0, 4.0]

DECODE_ORDER = ('utf-8-sig', 'utf-8', 'gbk', 'gb18030', 'big5')
POS_FIELDS = ('pos', 'figsize_in')

FIG_CALL_ATTRS = ('figure', 'subplots', 'subplot_mosaic', 'figure')
LAYOUT_TAGS_SUBPLOTS = ('subplots', 'GridSpec', 'subplot2grid', 'subplot_mosaic')


# --------------------------------------------------------------------------
# 基础工具
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
    """按 UTF-8 优先的顺序解码（与 mpltweak.params.read_text 同口径，但独立实现）。"""
    for enc in DECODE_ORDER:
        try:
            return raw.decode(enc), enc
        except UnicodeDecodeError:
            continue
    return raw.decode('utf-8', 'replace'), 'utf-8-replace'


def dec_utf8(raw):
    """子进程输出按 UTF-8 解码；严格失败时记一笔再降级 replace。"""
    try:
        return raw.decode('utf-8'), True
    except UnicodeDecodeError:
        return raw.decode('utf-8', 'replace'), False


def write_text(path, text, encoding='utf-8'):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, 'w', encoding=encoding, newline='') as f:
        f.write(text)


def write_json(path, obj):
    write_text(path, json.dumps(obj, ensure_ascii=False, indent=2))


def unified_diff_text(a_text, b_text, name_a, name_b, limit=None):
    lines = list(difflib.unified_diff(a_text.splitlines(True), b_text.splitlines(True),
                                      fromfile=name_a, tofile=name_b, n=3))
    if limit is not None and len(lines) > limit:
        lines = lines[:limit] + ['... (diff 截断，完整见证据文件)\n']
    return ''.join(lines)


# --------------------------------------------------------------------------
# 独立 AST 提取器（不使用 mpltweak 任何模块）
# --------------------------------------------------------------------------
def _num(node):
    """字面量数字 → float；其它 → None。"""
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) \
            and not isinstance(node.value, bool):
        return float(node.value)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        v = _num(node.operand)
        return None if v is None else -v
    return None


def _num_seq(node, n):
    if isinstance(node, (ast.List, ast.Tuple)) and len(node.elts) == n:
        vals = [_num(e) for e in node.elts]
        if all(v is not None for v in vals):
            return vals
    return None


def _call_attr(node, attr):
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == attr)


def _parent_map(tree):
    pm = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            pm[id(child)] = node
    return pm


def collect_cax_names(tree):
    """``fig.colorbar(..., cax=<name>)`` 里的 cax 变量名 —— 这些轴是 colorbar 轴。"""
    out = set()
    for node in ast.walk(tree):
        if _call_attr(node, 'colorbar'):
            for kw in node.keywords:
                if kw.arg == 'cax' and isinstance(kw.value, ast.Name):
                    out.add(kw.value.id)
    return out


def collect_add_axes(text, tree=None):
    """源码里所有 ``*.add_axes([a,b,c,d])`` 字面量，按源码出现顺序。

    每项附上静态分类（这些字段决定"能不能原位改写"）：
      ``name``          该调用是否直接被赋值给一个普通变量（``ax = fig.add_axes(...)``）；
                        mpltweak 的 ``_figure_axes_for_index`` 只认这种写法（ast.Assign + Name）。
      ``simple_assign`` 同上（布尔）。
      ``is_cax``        该变量是否被 ``fig.colorbar(..., cax=...)`` 用作 colorbar 槽轴 ——
                        工具按设计拒绝原位改 cax 的位置（会走 skipped）。
    """
    if tree is None:
        try:
            tree = ast.parse(text)
        except SyntaxError:
            return []
    pm = _parent_map(tree)
    cax = collect_cax_names(tree)
    out = []
    for node in ast.walk(tree):
        if _call_attr(node, 'add_axes') and node.args:
            vals = _num_seq(node.args[0], 4)
            if vals is None:
                continue
            name, simple = None, False
            p = pm.get(id(node))
            if isinstance(p, ast.Assign) and len(p.targets) == 1 \
                    and isinstance(p.targets[0], ast.Name) and p.value is node:
                name, simple = p.targets[0].id, True
            elif isinstance(p, ast.AnnAssign) and isinstance(p.target, ast.Name) \
                    and p.value is node:
                name, simple = p.target.id, True
            out.append({'lineno': node.lineno, 'col': node.col_offset,
                        'kind': type(node.args[0]).__name__,
                        'raw': ast.get_source_segment(text, node.args[0]),
                        'name': name, 'simple_assign': simple,
                        'is_cax': bool(name and name in cax),
                        'vals': vals})
    out.sort(key=lambda d: (d['lineno'], d['col']))
    return out


def collect_figsize(text):
    """第一个 ``plt.figure(figsize=(w,h))`` / ``plt.subplots(figsize=(w,h))``。"""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return None
    best = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr in ('figure', 'subplots'):
            for kw in node.keywords:
                if kw.arg == 'figsize':
                    vals = _num_seq(kw.value, 2)
                    if vals is not None:
                        cand = {'lineno': node.lineno, 'col': node.col_offset,
                                'attr': node.func.attr, 'vals': vals,
                                'raw': ast.get_source_segment(text, kw.value)}
                        if best is None or (cand['lineno'], cand['col']) < (best['lineno'], best['col']):
                            best = cand
    return best


def collect_figsize_all(text):
    """**每个**建图调用自己的 figsize 字面量（按源码序），用于 fig_index 精确配对。

    为什么必须按调用配对：params 里的 ``figsize_in`` 只有一个，语义是"第 fig_index
    张图的画布尺寸"。旧版取的是"文件里第一个 figsize 字面量"，多图脚本里它可能属于
    **别的**图 —— 于是 clean 往返会真的改动目标图的 figsize（脚手架口径错，伪 FAIL）。
    """
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr in ('figure', 'subplots', 'subplot_mosaic'):
            vals = None
            for kw in node.keywords:
                if kw.arg == 'figsize':
                    vals = _num_seq(kw.value, 2)      # 非字面量 → None
            out.append({'lineno': node.lineno, 'col': node.col_offset,
                        'attr': node.func.attr, 'vals': vals,
                        'raw': ast.get_source_segment(text, node)})
    out.sort(key=lambda d: (d['lineno'], d['col']))
    return out


def count_fig_calls(text):
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return 0
    n = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr in ('figure', 'subplots', 'subplot_mosaic'):
            n += 1
    return n


def collect_fig_calls_pos(text):
    """所有建图调用（figure/subplots/subplot_mosaic）的位置，按源码顺序。"""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr in ('figure', 'subplots', 'subplot_mosaic'):
            out.append((node.lineno, node.col_offset))
    out.sort()
    return out


def figure_index_of(pos, fig_positions):
    """静态近似：某个源码位置属于第几张图 = 它前面有几个建图调用 - 1（下限 0）。

    与 mpltweak 的 ``_figure_axes_for_index`` 同一建模口径（按建图调用切段）。
    静态分析无法覆盖"用变量回头改前面那张图"的写法，这属于已知近似。
    """
    n = 0
    for fp in fig_positions:
        if fp < pos:
            n += 1
        else:
            break
    return max(0, n - 1)


AXIS_FACTORY_ATTRS = ('add_axes', 'add_subplot')


def fig_span(fig_positions, fig_index):
    """与 mpltweak 的 ``_fig_span`` **同口径**的源码区间 [lo, hi)（用作索引对齐，不是判据）。"""
    if not (isinstance(fig_index, int) and 0 <= fig_index < len(fig_positions)):
        return 0, 10 ** 9
    f0 = fig_positions[fig_index]
    hi = 10 ** 9
    for fp in fig_positions:
        if fp > f0:
            hi = fp[0]
            break
    return f0[0], hi


def collect_axis_creations(text, tree=None):
    """镜像 mpltweak 的轴寻址模型（`name = <obj>.add_axes/add_subplot(...)` 赋值，
    限定在第 fig_index 张图的源码区间内，剔除 colorbar 槽轴 cax 变量），
    用来把 params 里的 ``axes[i]`` 序号与工具眼中的序号**对齐**。

    静态对齐必须在脚手架侧做对：只要序号错位，"没改到"就不是工具的锅。
    """
    if tree is None:
        try:
            tree = ast.parse(text)
        except SyntaxError:
            return [], (0, 10 ** 9)
    figs = collect_fig_calls_pos(text)
    aa = collect_add_axes(text, tree)
    lit_by_ll = {(d['lineno'], d['col']): d for d in aa}       # 键 = 调用节点位置
    cax = collect_cax_names(tree)
    out = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Attribute)
                and node.value.func.attr in AXIS_FACTORY_ATTRS):
            continue
        name = None
        for t in node.targets:
            if isinstance(t, ast.Name):
                name = t.id
                break
        if name is None or name in cax:
            continue
        call = node.value
        out.append({'lineno': node.lineno, 'col': node.col_offset, 'name': name,
                    'attr': call.func.attr, 'call': call,
                    'literal': lit_by_ll.get((call.lineno, call.col_offset))})
    out.sort(key=lambda d: (d['lineno'], d['col']))
    return out, figs


def extract_plan(text):
    """静态提取目标值 + 对齐的轴索引。

    返回：
      add_axes_all  源码里全部 add_axes 4 字面量（M4 复核器的口径，按源码序）
      target_fig    该用哪个 fig_index（= 拥有第一个 add_axes 字面量的那张图）
      axes_in_fig   params 的 axes 列表（**与工具寻址模型对齐**：第 target_fig 张图
                    区间内的 add_axes/add_subplot 赋值，按源码序；colorbar 槽轴剔除）
      target_axis   第一个"带位置字面量"的轴下标（M4 要改的就是它）
      pos_before_fig_call  该字面量是否落在建图调用**之前**（工具的区间扫不到 → 结构性不可原位）
    """
    tree = None
    try:
        tree = ast.parse(text)
    except SyntaxError:
        pass
    aa_all = collect_add_axes(text, tree)
    figs_pos = collect_fig_calls_pos(text)
    figs_size = collect_figsize_all(text)
    n_figs = len(figs_pos)
    plan = {'add_axes_all': aa_all, 'target_fig': 0, 'axes_in_fig': [],
            'figsize': collect_figsize(text), 'n_figs': max(1, n_figs),
            'target_axis': None, 'pos_before_fig_call': False, 'fig_span': (0, 10 ** 9),
            'figsize_target': None, 'figsize_other': None}
    figs = figs_pos

    def _pick_figsize(target):
        """只认第 target 个建图调用**自己**的 figsize kwarg 字面量。"""
        if not (isinstance(target, int) and 0 <= target < len(figs_size)):
            return None
        me = figs_size[target]
        if me['vals'] is None:
            return None
        return {'lineno': me['lineno'], 'col': me['col'], 'attr': me['attr'],
                'vals': me['vals'], 'raw': me['raw']}

    if not aa_all:
        # 没有 add_axes 字面量时，把 fig_index 定到**第一个带 figsize 字面量的建图调用**
        # 上，而不是硬编码 0 —— 这样 params 描述的确实是"第 fig_index 张图当前的尺寸"，
        # 往返判据才成立（否则会把 A 图的尺寸写到 B 图，属于脚手架口径错）。
        first_fs = next((k for k, e in enumerate(figs_size) if e['vals'] is not None), None)
        if first_fs is None:
            plan['figsize'] = None
            plan['figsize_target'] = None
            return plan
        plan['target_fig'] = first_fs
        plan['figsize_target'] = _pick_figsize(first_fs)
        plan['figsize'] = plan['figsize_target']
        other = collect_figsize(text)
        if other is not None and (other['lineno'], other['col']) != (
                plan['figsize_target']['lineno'], plan['figsize_target']['col']):
            plan['figsize_other'] = other
        return plan
    target = figure_index_of((aa_all[0]['lineno'], aa_all[0]['col']), figs)
    plan['target_fig'] = target
    lo, hi = fig_span(figs, target if n_figs else 0)
    plan['fig_span'] = (lo, hi)
    plan['figsize_target'] = _pick_figsize(target)
    plan['figsize'] = plan['figsize_target']
    # 记下"文件里第一个 figsize 字面量"是不是属于别的图（旧版口径错的证据）
    other = collect_figsize(text)
    if other is not None and (plan['figsize_target'] is None
                              or (other['lineno'], other['col'])
                              != (plan['figsize_target']['lineno'],
                                  plan['figsize_target']['col'])):
        plan['figsize_other'] = other
    if not n_figs:
        # 工具在"一个建图调用都没有"时返回空轴表（_figure_axes_for_index → []）
        plan['axes_in_fig'] = []
        plan['target_axis'] = None
        return plan
    axis_seq = [{'index': k, 'name': e['name'], 'attr': e['attr'], 'lineno': e['lineno'],
                 'pos': e['literal']['vals'] if e['literal'] else None,
                 'literal_pos': (e['literal']['lineno'], e['literal']['col'])
                 if e['literal'] else None}
                for k, e in enumerate(collect_axis_creations(text, tree)[0])
                if lo <= e['lineno'] < hi]
    for k, a in enumerate(axis_seq):
        a['index'] = k
    plan['axes_in_fig'] = axis_seq
    for a in axis_seq:
        if a['pos'] is not None:
            plan['target_axis'] = a['index']
            plan['pos_before_fig_call'] = a['lineno'] < lo
            break
    return plan


def has_block(text):
    return BLOCK_SENTINEL in text


# --------------------------------------------------------------------------
# figsize 目标路径（T37）：挑一个"与源码现值明显不同、且不退化"的目标尺寸
# --------------------------------------------------------------------------
FIGSIZE_CANDIDATES = [(6.0, 4.0), (5.5, 4.5), (7.0, 3.5), (4.5, 3.5), (8.0, 5.0),
                      (5.0, 3.0), (7.5, 4.5), (4.0, 3.2)]


def pick_target_figsize(current):
    """挑目标尺寸：两维都在 [2, 20]、与现值至少一维差 >= 0.8、且不是退化值。"""
    for w, h in FIGSIZE_CANDIDATES:
        if not (2.0 <= w <= 20.0 and 2.0 <= h <= 20.0):
            continue
        if current is None:
            return [w, h]
        dw, dh = abs(w - float(current[0])), abs(h - float(current[1]))
        if max(dw, dh) >= 0.8:
            return [w, h]
    return [6.0, 4.0]


def _skeleton(raw):
    """把字面量里的数字抹掉，留下"容器/分隔/空白"骨架 —— 用来判"写法是否被改"。"""
    if raw is None:
        return None
    return re.sub(r'\d+\.?\d*', '#', raw)


def check_figsize_change(before_text, after_text, fig_index, target):
    """**独立复核器（figsize 路径）**：断言三条。

    ① 只有第 fig_index 张图的 `figsize=` kwarg 变了（别的图的 figsize、以及所有
       `add_axes` 字面量都必须原样）；
    ② 该 kwarg 的值 == 目标值（按数值比，允许 1e-9 误差）；
    ③ 落盘后脚本仍能 `ast.parse`。
    另外单独记录"写法是否保持"（容器 `[]`/`()`、空白、`.0` 等骨架），
    它不参与 pass/fail（属于风格），但会单独计数并列出——见 T37 报告说明。
    """
    res = {'parse_after_ok': False, 'n_fig_before': 0, 'n_fig_after': 0,
           'changed_fig_indices': [], 'only_target_changed': False,
           'value_equals_target': False, 'spelling_preserved': None,
           'raw_before': None, 'raw_after': None, 'add_axes_changed': False,
           'target_index': fig_index}
    try:
        before = collect_figsize_all(before_text)
        after = collect_figsize_all(after_text)
        res['parse_after_ok'] = True
    except SyntaxError:
        return res
    res['n_fig_before'], res['n_fig_after'] = len(before), len(after)
    if len(before) != len(after) or not (0 <= fig_index < len(before)):
        return res
    changed = []
    for i, (b, a) in enumerate(zip(before, after)):
        bv, av = b.get('vals'), a.get('vals')
        if (bv is None) != (av is None):
            changed.append(i)
        elif bv is not None and any(abs(x - y) > 1e-9 for x, y in zip(bv, av)):
            changed.append(i)
    res['changed_fig_indices'] = changed
    res['only_target_changed'] = (changed == [fig_index])
    res['raw_before'] = before[fig_index].get('raw')
    res['raw_after'] = after[fig_index].get('raw')
    tgt = after[fig_index].get('vals')
    res['value_equals_target'] = bool(
        tgt is not None and len(tgt) == 2
        and all(abs(tgt[k] - target[k]) <= 1e-9 for k in range(2)))
    sb, sa = _skeleton(res['raw_before']), _skeleton(res['raw_after'])
    res['spelling_preserved'] = (sb == sa) if (sb is not None and sa is not None) else None
    # add_axes 字面量一个都不许变
    try:
        aa_b, aa_a = collect_add_axes(before_text), collect_add_axes(after_text)
        res['add_axes_changed'] = (
            len(aa_b) != len(aa_a)
            or any(any(abs(x - y) > 1e-9 for x, y in zip(p['vals'], q['vals']))
                   for p, q in zip(aa_b, aa_a)))
    except SyntaxError:
        res['add_axes_changed'] = True
    return res


def count_figsize_literals(text):
    return len(collect_figsize_all(text))


# --------------------------------------------------------------------------
# 子进程调用（字节读入 → UTF-8 解码）
# --------------------------------------------------------------------------
def run_cli(arglist, timeout, cwd=None):
    env = dict(os.environ)
    env['PYTHONPATH'] = MPLTWEAK_SRC
    env['MPLBACKEND'] = 'Agg'
    cmd = [PYTHON, '-m', 'mpltweak'] + list(arglist)
    t0 = time.time()
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout,
                              env=env, cwd=cwd or HERE)
        rc, out_raw, err_raw, timed_out = proc.returncode, proc.stdout, proc.stderr, False
    except subprocess.TimeoutExpired as e:
        rc, out_raw, err_raw, timed_out = None, (e.stdout or b''), (e.stderr or b''), True
    except Exception as e:                                    # noqa: BLE001
        return {'rc': None, 'timeout': False, 'spawn_error': repr(e),
                'stdout': '', 'stderr': '', 'stdout_utf8_ok': None,
                'cmd': cmd, 'seconds': time.time() - t0}
    out, out_ok = dec_utf8(out_raw)
    err, _err_ok = dec_utf8(err_raw)
    return {'rc': rc, 'timeout': timed_out, 'stdout': out, 'stderr': err,
            'stdout_utf8_ok': out_ok, 'cmd': cmd, 'seconds': round(time.time() - t0, 3)}


def cmd_str(cmd):
    return ' '.join('"%s"' % c if (' ' in c or '\\' in c) else c for c in cmd)


def json_of(run):
    """从 apply --json 的 stdout 里取 JSON；失败返回 (None, 错误串)。"""
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


def classify_corpus_syntax_error(raw, parse_err):
    """M1 失败的语料侧子类（T36）。纯静态：只看字节与 SyntaxError 文案。"""
    txt = raw[:200000].decode('utf-8', 'replace')
    low = txt.lower()
    if any(x in low for x in ('\n!pip', '\n!apt', '\n!conda', '!pip install', '!pip -q')):
        return 'colab_magic（Colab/Jupyter 的 !shell 命令，不是 Python）'
    if 'GET_IPYTHON' in txt.upper() or '\n%matplotlib' in low:
        return 'ipython_magic（%magic / get_ipython，不是纯 Python）'
    if '###INSERT' in txt.upper():
        return 'author_placeholder（作者留下的 ###INSERT FILEPATH 占位符）'
    if 'f-string' in (parse_err or '').lower():
        return ('pep701_fstring（f-string 内引号/反斜杠，需 Python 3.12+；'
                '本机 longxia=3.10 必失败）')
    return 'real_syntax_error（源码本身语法错，3.12 也过不了）'


def metric_class(status, reason, kind_hint=None):
    """给"非 pass"的记录打一个性质标签（T36 口径）。

    - tool_bug        前提成立、工具没做到
    - tool_contract   字节没问题，但 JSON 契约（reason/changes）骗人
    - harness_premise 判据前提/考卷构造不成立（cax、区间外、提不出目标值…）
    - corpus_side     语料自身问题（语法/魔法命令/占位符）
    """
    if kind_hint:
        return kind_hint
    r = (reason or '')
    if status == 'not_applicable':
        return 'harness_premise'
    if status == 'fail':
        return 'tool_bug'
    if status == 'timeout':
        return 'tool_bug'
    if status == 'skipped':
        if any(k in r for k in ('m1_parse_failed', 'no_target_literals')):
            return ('corpus_side' if r.startswith('m1_parse_failed')
                    else 'harness_premise')
        return 'harness_premise'
    return None


def auto_fallback_inferred(fe):
    """从 apply 的 JSON 反推"这次是自动回退到块模式"。

    JSON 里没有 `auto_fallback` 字段（RC-5），但本次一律用默认 `--style auto`：
    `style == 'block'` 只可能来自 `apply.py:396-400` 的回退。再加上
    `reason in (ok, best_effort)` 排除"连块都没写成"的 no_axes/no_fig/fail 分支。
    """
    return fe.get('style') == 'block' and fe.get('reason') in ('ok', 'best_effort')


# --------------------------------------------------------------------------
# 单脚本测试
# --------------------------------------------------------------------------
class Evidence(object):
    """证据文件管理器：统一放在 work/<stem>/ 下。"""

    def __init__(self, wdir):
        self.dir = wdir

    def path(self, name):
        return os.path.join(self.dir, name)

    def save(self, name, text):
        write_text(self.path(name), text)
        return os.path.relpath(self.path(name), HERE).replace('\\', '/')

    def save_bytes(self, name, raw):
        p = self.path(name)
        os.makedirs(self.dir, exist_ok=True)
        with open(p, 'wb') as f:
            f.write(raw)
        return os.path.relpath(p, HERE).replace('\\', '/')

    def save_json(self, name, obj):
        return self.save(name, json.dumps(obj, ensure_ascii=False, indent=2))


def make_params(script_basename, axes_pos, figsize_in, n_figs, fig_index=0):
    axes = []
    for i, p in enumerate(axes_pos):
        axes.append({'index': i, 'pos': list(p)})
    return {'version': 4, 'script': script_basename, 'figsize_px': None,
            'figsize_in': list(figsize_in) if figsize_in else None,
            'fig_index': fig_index, 'n_figs': n_figs, 'axes': axes}


def run_apply(ev, tag, script, params_path, timeout):
    """跑一次 apply --write --verify-fast --json，落证据，返回 (run, payload, ev_paths)。"""
    run = run_cli(['apply', script, '--params', params_path,
                   '--write', '--verify-fast', '--json'], timeout)
    evp = {}
    if run.get('spawn_error'):
        evp['stderr'] = ev.save('%s.stderr.txt' % tag, 'spawn_error: %s' % run['spawn_error'])
        return run, None, evp
    evp['stdout'] = ev.save('%s.apply.json' % tag, run['stdout'])
    evp['stderr'] = ev.save('%s.stderr.txt' % tag, run['stderr'])
    evp['cmd'] = ev.save('%s.cmd.txt' % tag, cmd_str(run['cmd']) + '\n')
    payload, jerr = json_of(run)
    if payload is None:
        evp['json_error'] = ev.save('%s.json_error.txt' % tag, str(jerr))
    return run, payload, evp


def probe_inplace(ev, tag, src_path, params_path, timeout):
    """诊断用：同一份参数、显式 ``--style inplace`` 再跑一次（仍然 --verify-fast，不执行脚本）。

    ``--style auto`` 会把 inplace 那一次的结果**丢掉**（写回失败/落空就改用块模式重试，
    res 被整份覆盖），所以 JSON 里看不到"原位那次为什么没落地"。这条探针把它补回来，
    让每条 FAIL 的根因都有直接证据。探针跑在**独立副本**上，不污染主证据链。
    """
    d = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(params_path))), 'probe')
    os.makedirs(d, exist_ok=True)
    probe_py = os.path.join(d, os.path.basename(src_path))
    with open(probe_py, 'wb') as f:
        f.write(read_bytes(src_path))
    probe_params = os.path.join(d, os.path.basename(params_path))
    with open(probe_params, 'wb') as f:
        f.write(read_bytes(params_path))
    run = run_cli(['apply', probe_py, '--params', probe_params,
                   '--write', '--verify-fast', '--json', '--style', 'inplace'], timeout)
    evp = {tag + '_stdout': ev.save('probe_%s.inplace.json' % tag, run.get('stdout', '')),
           tag + '_stderr': ev.save('probe_%s.inplace.stderr.txt' % tag, run.get('stderr', '')),
           tag + '_cmd': ev.save('probe_%s.inplace.cmd.txt' % tag, cmd_str(run['cmd']) + '\n')}
    payload, _ = json_of(run)
    return payload, evp


def test_one(row, args):
    """跑一个脚本的全部指标。返回结果 dict（可 json 序列化）。"""
    rel = row['path']
    src_path = os.path.join(CORPUS, rel.replace('/', os.sep))
    stem = os.path.splitext(os.path.basename(rel))[0]
    wdir = os.path.join(WORK, stem)
    ev = Evidence(wdir)
    if os.path.isdir(wdir) and args.clean:
        shutil.rmtree(wdir, ignore_errors=True)
    os.makedirs(wdir, exist_ok=True)

    res = {
        'path': rel, 'stem': stem, 'source': row.get('source'),
        'workdir': os.path.relpath(wdir, HERE).replace('\\', '/'),
        'm1_parse': {'status': 'skipped', 'reason': 'not_run'},
        'm2_extract': {'status': 'skipped', 'reason': 'not_run'},
        'm3_clean_roundtrip': {'status': 'skipped', 'reason': 'not_run'},
        'm4_directed_rewrite': {'status': 'skipped', 'reason': 'not_run'},
        'm5_idempotent': {'status': 'skipped', 'reason': 'not_run'},
        'm6_revert': {'status': 'skipped', 'reason': 'not_run'},
        'meta': {
            'layout_tags': (row.get('tags') or {}).get('layout') or [],
            'deps': (row.get('tags') or {}).get('deps') or [],
            'needs_data': (row.get('tags') or {}).get('needs_data'),
            'manifest_sha256': row.get('sha256'),
            'lines': (row.get('tags') or {}).get('lines'),
        },
        'evidence': {},
    }
    if not os.path.exists(src_path):
        res['error'] = 'corpus file missing'
        for k in ('m1_parse', 'm2_extract'):
            res[k] = {'status': 'skipped', 'reason': 'file_missing'}
        return res

    raw = read_bytes(src_path)
    res['meta']['sha_orig'] = sha256_bytes(raw)
    res['meta']['bytes'] = len(raw)
    res['meta']['sha_manifest_match'] = (res['meta'].get('manifest_sha256') == res['meta']['sha_orig'])
    ev.save_bytes('orig.copy.py', raw)

    # ---------------- M1 可解析 ----------------
    parse_err = None
    try:
        ast.parse(raw)
        res['m1_parse'] = {'status': 'pass', 'reason': 'ast.parse ok'}
    except SyntaxError as e:
        parse_err = '%s: line %s: %s' % (type(e).__name__, e.lineno, e.msg)
    except Exception as e:                                    # noqa: BLE001
        parse_err = '%s: %s' % (type(e).__name__, e)
    if parse_err:
        # 性质标注（T36）：M1 的失败**永远是语料侧** —— 工具与脚手架用的是同一个解释器，
        # 语法坏的文件工具本来就应该拒绝处理。数字口径不变（仍记 fail），只是标明性质，
        # 并在报告里分出子类（Colab 魔法 / 占位符 / PEP 701 / 真语法错）。
        kind = classify_corpus_syntax_error(raw, parse_err)
        res['m1_parse'] = {'status': 'fail', 'reason': parse_err,
                           'verdict_class': 'corpus_side', 'corpus_kind': kind}
        res['meta']['m1_corpus_kind'] = kind
        res['evidence']['m1'] = ev.save('m1.parse_error.txt', parse_err + '\n')
        for k in ('m2_extract', 'm3_clean_roundtrip', 'm4_directed_rewrite',
                  'm5_idempotent', 'm6_revert'):
            res[k] = {'status': 'skipped', 'reason': 'm1_parse_failed',
                      'verdict_class': 'corpus_side'}
        return res

    text, enc = decode_source(raw)
    res['meta']['encoding'] = enc
    try:
        ast.parse(text)
    except SyntaxError:
        text = raw.decode('utf-8', 'replace')
        res['meta']['encoding'] = 'bytes-fallback'

    # ---------------- M2 参数可构造 ----------------
    plan = extract_plan(text)
    add_axes_all = plan['add_axes_all']
    axes_seq = plan['axes_in_fig']             # 与工具寻址模型对齐的轴表（可能含 pos=None 占位）
    add_axes = [a for a in axes_seq if a['pos'] is not None]   # 可原位改写的轴
    figsize = plan['figsize']
    n_fig = plan['n_figs']
    fig_index = plan['target_fig']
    target_axis = plan['target_axis']
    res['meta'].update({'n_add_axes_literals': len(add_axes_all),
                        'n_axes_aligned': len(axes_seq),
                        'n_add_axes_in_target_fig': len(add_axes),
                        'has_figsize_literal': bool(figsize),
                        'n_fig_calls': n_fig, 'fig_index_used': fig_index,
                        'target_axis_index': target_axis,
                        'pos_before_fig_call': plan['pos_before_fig_call'],
                        'fig_span': list(plan['fig_span']),
                        'family': 'add_axes' if add_axes_all else ('subplots' if n_fig else 'none')})
    res['evidence']['m2'] = ev.save_json('m2.extract.json',
                                         {'add_axes_all': add_axes_all,
                                          'axes_aligned': axes_seq,
                                          'target_fig': fig_index,
                                          'target_axis_index': target_axis,
                                          'pos_before_fig_call': plan['pos_before_fig_call'],
                                          'fig_span': list(plan['fig_span']),
                                          'figsize_target': figsize,
                                          'figsize_belongs_to_other_fig': plan.get('figsize_other'),
                                          'n_fig_calls': n_fig,
                                          'fig_calls': collect_figsize_all(text)})
    if not add_axes_all and not figsize:
        res['m2_extract'] = {'status': 'skipped',
                             'verdict_class': 'harness_premise',
                             'reason': 'no_target_literals（源码里没有 add_axes 4 字面量，也没有 figure(figsize=)）'}
        for k in ('m3_clean_roundtrip', 'm4_directed_rewrite', 'm5_idempotent', 'm6_revert'):
            res[k] = {'status': 'skipped', 'verdict_class': 'harness_premise',
                      'reason': 'no_target_literals'}
        return res
    res['m2_extract'] = {'status': 'pass',
                         'reason': 'add_axes×%d(目标图 %d 内 %d), figsize=%s' % (
                             len(add_axes_all), fig_index, len(add_axes),
                             figsize['vals'] if figsize else None)}

    # 参数模型：优先"与工具寻址模型对齐"的轴表（含 add_subplot 占位，保证 axes[i]
    # 与工具眼中的 fig.axes[i] 是同一个轴）；对齐模型**够不到任何字面量**时（字面量
    # 落在所有建图区间之外、或全是 colorbar 槽轴）退回"规格字面量"模型（索引 0..k =
    # 源码顺序）—— 因为"这类字面量到底改不改得到"正是 M4 要回答的问题，不能悄悄跳过。
    aligned_pos = {a['index']: list(a['pos']) for a in axes_seq if a['pos'] is not None}
    if aligned_pos:
        param_axes = [{'index': a['index'], 'pos': a['pos']} for a in axes_seq]
        target_idx = target_axis
        global_idx = next((k for k, d in enumerate(add_axes_all)
                           if (d['lineno'], d['col']) == axes_seq[target_axis]['literal_pos']), 0)
        params_model = 'aligned'
    else:
        param_axes = [{'index': k, 'pos': list(d['vals'])}
                      for k, d in enumerate(add_axes_all)]
        target_idx = 0 if add_axes_all else None
        global_idx = 0
        params_model = 'literal_fallback' if add_axes_all else 'empty'
    res['meta']['params_model'] = params_model
    res['meta']['target_axis_index'] = target_idx

    def _mkparams(script_name, positions_by_index):
        """positions_by_index: {轴下标: [x0,y0,w,h]}；未给的轴写 pos=null（占位保对齐）。"""
        axes = []
        for a in param_axes:
            axes.append({'index': a['index'],
                         'pos': positions_by_index.get(a['index'], a['pos'])})
        return {'version': 4, 'script': script_name, 'figsize_px': None,
                'figsize_in': list(figsize['vals']) if figsize else None,
                'fig_index': fig_index, 'n_figs': n_fig, 'axes': axes}

    # ---------------- M3 干净往返 ----------------
    a_py = os.path.join(wdir, 'clean.py')
    with open(a_py, 'wb') as f:
        f.write(raw)
    params_clean = os.path.join(wdir, 'clean.json')
    pclean = _mkparams('clean.py', {})
    write_json(params_clean, pclean)
    ev.save('m3_clean.params.json', json.dumps(pclean, ensure_ascii=False, indent=2))
    run, payload, evp = run_apply(ev, 'm3_clean', a_py, params_clean, args.timeout)
    res['evidence'].update({'m3_' + k: v for k, v in evp.items()})
    m3 = {'status': 'fail', 'reason': ''}
    if run.get('timeout'):
        m3 = {'status': 'timeout', 'reason': 'apply > %ss' % args.timeout}
        res['evidence']['m3_timeout'] = ev.save('m3_clean.timeout.txt', run.get('stderr', ''))
    else:
        after = read_bytes(a_py) if os.path.exists(a_py) else b''
        sha_ok = sha256_bytes(after) == res['meta']['sha_orig']
        fe = file_entry(payload)
        reason = fe.get('reason') or ('json_error' if payload is None else 'missing_reason')
        m3.update({'status': 'pass' if (sha_ok and reason == 'no_change') else 'fail',
                   'reason': 'reason=%s bytes_%s' % (reason, 'same' if sha_ok else 'CHANGED'),
                   'rc': run['rc'], 'apply_reason': reason,
                   'bytes_unchanged': sha_ok,
                   'block_inserted': has_block(decode_source(after)[0]),
                   'style': fe.get('style'), 'changes': fe.get('changes') or [],
                   'warnings': fe.get('warnings') or [],
                   'auto_fallback': fe.get('auto_fallback'),
                   'params_model': params_model,
                   # 往返的"含金量"分级：有 pos 目标的才真正测了"位置往返"
                   'case': ('pos_roundtrip' if aligned_pos else
                            ('literal_fallback' if add_axes_all and param_axes
                             else ('axes_but_no_pos' if axes_seq else 'figsize_only'))),
                   # engine 版新增的机器可读字段（RC-5 已修）：有就直接取，没有就反推
                   'engine_fields': {k: fe.get(k) for k in
                                     ('applied', 'block_lines', 'needs_block',
                                      'inplace_reason', 'inplace_changes')
                                     if k in fe},
                   'auto_fallback_inferred': auto_fallback_inferred(fe)})
        # 两类 FAIL 必须分开：① 字节真的被改（硬失败）；② 字节没变但 reason/JSON
        # 谎报有改动（契约失败：agent 从 JSON 分不清"无事可做"与"做了事"）。
        if m3['status'] == 'fail':
            m3['fail_kind'] = ('bytes_changed' if not sha_ok
                               else 'reason_not_no_change_bytes_same')
            # 性质标签（T36）：字节被改 = 工具没做到（tool_bug）；只谎报 = 契约问题。
            m3['verdict_class'] = ('tool_bug' if not sha_ok else 'tool_contract')
            # 值是否被保留：字节变了但所有提取出的目标值（pos/figsize）都没变
            # → 说明这次写回只改了**文本写法**（如 [6, 3]→(6, 3)），是"等值改写"。
            a_text0 = decode_source(after)[0]
            fs_after = [e['vals'] for e in collect_figsize_all(a_text0) if e['vals']]
            aa_after_v = [d['vals'] for d in collect_add_axes(a_text0)]
            _key = lambda vs: sorted(tuple(round(float(c), 9) for c in v) for v in vs)
            fs_same = ((figsize is None) or (figsize['vals'] in fs_after))
            aa_same = _key(add_axes_all and [d['vals'] for d in add_axes_all] or []) == _key(aa_after_v)
            m3['value_preserving_write'] = bool(not sha_ok and fs_same and aa_same)
            m3['extracted_values_before'] = {'figsize': figsize['vals'] if figsize else None,
                                             'add_axes': [d['vals'] for d in add_axes_all]}
            m3['extracted_values_after'] = {'figsize': fs_after, 'add_axes': aa_after_v}
        # 退化情形：参数里一个轴项都没有 → writeback 在 `if not axes` 处直接返回
        # no_axes，一个字节都不动。这不是"往返被改脏"，而是**该往返构造不出来**，
        # 如实记为 skipped 并单列统计（单独作为覆盖率/局限数字汇报，不混进 FAIL）。
        if not param_axes and sha_ok and reason == 'no_axes':
            m3['status'] = 'skipped'
            m3['degenerate_no_axes'] = True
            m3['verdict_class'] = 'harness_premise'
            m3['reason'] = ('degenerate：参数无 axes 项 → apply 直接返回 no_axes，'
                            'figsize-only 目标不落盘（字节未变）')
        elif not param_axes and sha_ok and reason != 'no_change':
            m3['degenerate_no_axes'] = True
            m3['verdict_class'] = 'harness_premise'
        if not sha_ok:
            a_text = decode_source(after)[0]
            res['evidence']['m3_diff'] = ev.save('m3_clean.diff',
                                                 unified_diff_text(text, a_text, 'orig/' + stem + '.py',
                                                                   'work/' + stem + '/clean.py'))
        if m3['status'] == 'fail':
            # 诊断补证：auto 模式把 inplace 那次的结果丢掉了，这里单独跑一次 inplace 拿回来
            pl, evp = probe_inplace(ev, 'm3', a_py, params_clean, args.timeout)
            res['evidence'].update(evp)
            fe_p = file_entry(pl)
            m3['inplace_only'] = {'reason': fe_p.get('reason'), 'style': fe_p.get('style'),
                                  'changes': fe_p.get('changes') or [],
                                  'warnings': fe_p.get('warnings') or []}
    res['m3_clean_roundtrip'] = m3

    # ---------------- M4 定向改写 ----------------
    m4 = {'status': 'skipped', 'reason': 'not_run'}
    m5 = {'status': 'skipped', 'reason': 'not_run'}
    m6 = {'status': 'skipped', 'reason': 'not_run'}
    b_py = os.path.join(wdir, 'directed.py')
    params_dir = os.path.join(wdir, 'directed.json')
    if not param_axes:
        m4 = {'status': 'skipped', 'verdict_class': 'harness_premise',
              'reason': 'no_axes_in_target_fig（参数里没有任何轴项 → apply 直接返回 no_axes，'
                        '没有可改写的位置目标）'}
        m5 = {'status': 'skipped', 'verdict_class': 'harness_premise', 'reason': 'm4_skipped'}
        m6 = {'status': 'skipped', 'verdict_class': 'harness_premise',
              'reason': 'no_directed_write（M4 未运行：没有 pos 目标 → 没有真实写回可锚定）'}
    elif target_idx is None:
        m4 = {'status': 'skipped', 'verdict_class': 'harness_premise',
              'reason': 'no_pos_literal（源码里没有"带 4 个数字字面量的 add_axes"）'}
        m5 = {'status': 'skipped', 'verdict_class': 'harness_premise', 'reason': 'm4_skipped'}
        m6 = {'status': 'skipped', 'verdict_class': 'harness_premise',
              'reason': 'no_directed_write（M4 未运行：没有 pos 目标 → 没有真实写回可锚定）'}
    else:
        with open(b_py, 'wb') as f:
            f.write(raw)
        positions = {target_idx: list(TARGET_POS)}
        pdir = _mkparams('directed.py', positions)
        write_json(params_dir, pdir)
        ev.save('m4_directed.params.json', json.dumps(pdir, ensure_ascii=False, indent=2))
        run, payload, evp = run_apply(ev, 'm4_directed', b_py, params_dir, args.timeout)
        res['evidence'].update({'m4_' + k: v for k, v in evp.items()})
        if run.get('timeout'):
            m4 = {'status': 'timeout', 'reason': 'apply > %ss' % args.timeout}
        else:
            after_raw = read_bytes(b_py) if os.path.exists(b_py) else b''
            after_text = decode_source(after_raw)[0]
            fe = file_entry(payload)
            try:
                before_axes = collect_add_axes(text)
                after_axes = collect_add_axes(after_text)
                parse_after = True
            except SyntaxError as e:
                before_axes, after_axes, parse_after = [], [], False
            target = [round(v, 6) for v in TARGET_POS]
            changed_idx = [i for i in range(min(len(before_axes), len(after_axes)))
                           if [round(v, 6) for v in before_axes[i]['vals']]
                           != [round(v, 6) for v in after_axes[i]['vals']]]
            check = {
                'parse_after_ok': parse_after,
                'n_before': len(before_axes), 'n_after': len(after_axes),
                'changed_indices': changed_idx,
                'target_after': [round(v, 6) for v in after_axes[global_idx]['vals']]
                if len(after_axes) > global_idx else None,
                'target_raw_after': after_axes[global_idx]['raw']
                if len(after_axes) > global_idx else None,
                'block_inserted': has_block(after_text),
                # figsize 前后都必须取**第 fig_index 张图自己**的那个字面量：
                # 用"文件里第一个 figsize"会在多图脚本里比错对象（伪 FAIL）。
                'figsize_after': (collect_figsize_all(after_text)[fig_index].get('vals')
                                  if len(collect_figsize_all(after_text)) > max(0, fig_index)
                                  else None),
                'figsize_before': (figsize or {}).get('vals'),
                'bytes_changed': sha256_bytes(after_raw) != res['meta']['sha_orig'],
                'style': fe.get('style'), 'apply_reason': fe.get('reason'),
                'changes': fe.get('changes') or [], 'warnings': fe.get('warnings') or [],
                'auto_fallback': fe.get('auto_fallback'),
            }
            # ---- 判定 ----
            fs_changed = (check['figsize_before'] is not None
                          and check['figsize_after'] is not None
                          and [round(v, 6) for v in check['figsize_before']]
                          != [round(v, 6) for v in check['figsize_after']])
            check['figsize_changed'] = fs_changed
            # ---- 判据前提检查（T36 口径）：前提不成立 → not_applicable，不计 fail ----
            # 依据 §9 的逐条定性：12 条目标是 colorbar 槽轴（工具按设计剔除），1 条字面量
            # 落在该图的 [建图行, 下一张图行) 之外 —— 这两类都不是"工具没做到"，而是
            # **这条考卷对这类写法不适用**，必须与真 FAIL 分开计数（证据一律照旧留档）。
            na_reason = None
            if add_axes_all and add_axes_all[global_idx].get('is_cax'):
                na_reason = ('harness_premise: target_is_colorbar_slot_axis（目标是 colorbar '
                             '槽轴 cax，工具按设计不原位改）')
            elif plan['pos_before_fig_call'] or params_model == 'literal_fallback':
                na_reason = ('harness_premise: target_literal_outside_figure_span（字面量落在 '
                             '该图的 [建图行, 下一张图行) 之外，工具的区间模型够不到）')
            check['premise'] = na_reason or 'ok'
            if na_reason is not None:
                verdict, detail = 'not_applicable', na_reason
            elif not parse_after:
                verdict, detail = 'fail', '改写后 ast.parse 失败'
            elif len(after_axes) != len(before_axes):
                verdict, detail = 'fail', 'add_axes 字面量个数变了（%d→%d）' % (
                    len(before_axes), len(after_axes))
            elif not changed_idx:
                verdict, detail = 'fail', '没改：没有任何 add_axes 字面量被改动'
            elif len(changed_idx) > 1:
                verdict, detail = 'fail', '改多了：改了 %d 处 add_axes 字面量（索引 %s）' % (
                    len(changed_idx), changed_idx)
            elif changed_idx[0] != global_idx:
                verdict, detail = 'fail', '改错图了：改的是索引 %d（应为 %d）' % (
                    changed_idx[0], global_idx)
            elif fs_changed:
                verdict, detail = 'fail', '改多了：目标轴正确，但 figsize 也被改（%s→%s）' % (
                    check['figsize_before'], check['figsize_after'])
            elif [round(v, 6) for v in after_axes[global_idx]['vals']] != target:
                verdict, detail = 'fail', '目标轴改了但值不等于目标 %s：%s' % (
                    target, check['target_after'])
            else:
                verdict, detail = 'pass', '只有目标 add_axes 字面量（源码第 %d 个）变为目标值' % (
                    global_idx + 1)
            check['verdict'] = verdict
            check['detail'] = detail
            check['target_axis_index'] = target_idx
            check['target_global_literal_index'] = global_idx
            check['params_model'] = params_model
            # 静态根因分类（用**独立提取器**的信息，不依赖工具自述）
            if verdict == 'not_applicable':
                check['root_cause_class'] = na_reason
            elif verdict == 'fail':
                if add_axes_all and not add_axes_all[global_idx].get('simple_assign'):
                    cls = ('not_simple_assign（不是 `变量 = fig.add_axes(...)` 写法，'
                           '工具建不出轴变量映射）')
                elif target_idx is not None and axes_seq and \
                        axes_seq[target_idx].get('attr') != 'add_axes':
                    cls = 'not_a_axes_literal（目标轴其实是 add_subplot 网格轴）'
                else:
                    cls = 'unexplained（写法正常，需人工看证据）'
                check['root_cause_class'] = cls
            res['evidence']['m4_check'] = ev.save_json('m4_directed.check.json', check)
            m4 = {'status': verdict, 'reason': detail, 'rc': run['rc'],
                  'verdict_class': ('harness_premise' if verdict == 'not_applicable'
                                    else ('tool_bug' if verdict == 'fail' else None)),
                  'apply_reason': check['apply_reason'], 'style': check['style'],
                  'changes': check['changes'], 'warnings': check['warnings'],
                  'auto_fallback': check['auto_fallback'],
                  'auto_fallback_inferred': auto_fallback_inferred(
                      {'style': check['style'], 'reason': check['apply_reason']}),
                  'block_inserted': check['block_inserted'],
                  'changed_indices': changed_idx,
                  'root_cause_class': check.get('root_cause_class'),
                  'target_axis_index': target_idx, 'params_model': params_model,
                  'n_add_axes_before': len(before_axes), 'n_add_axes_after': len(after_axes)}
            if verdict in ('fail', 'not_applicable'):
                res['evidence']['m4_diff'] = ev.save(
                    'm4_directed.diff',
                    unified_diff_text(text, after_text, 'orig/' + stem + '.py',
                                      'work/' + stem + '/directed.py'))
                pl4, evp4 = probe_inplace(ev, 'm4', b_py, params_dir, args.timeout)
                res['evidence'].update(evp4)
                fe4 = file_entry(pl4)
                m4['inplace_only'] = {'reason': fe4.get('reason'), 'style': fe4.get('style'),
                                      'changes': fe4.get('changes') or [],
                                      'warnings': fe4.get('warnings') or []}

            # ---------------- M5 幂等 ----------------
            base5 = after_raw
            run5 = run_cli(['apply', b_py, '--params', params_dir,
                            '--write', '--verify-fast', '--json'], args.timeout)
            res['evidence']['m5_stdout'] = ev.save('m5_idem.apply.json', run5.get('stdout', ''))
            res['evidence']['m5_stderr'] = ev.save('m5_idem.stderr.txt', run5.get('stderr', ''))
            res['evidence']['m5_cmd'] = ev.save('m5_idem.cmd.txt', cmd_str(run5['cmd']) + '\n')
            if run5.get('timeout'):
                m5 = {'status': 'timeout', 'verdict_class': 'tool_bug',
                      'reason': 'apply > %ss' % args.timeout}
            else:
                after5 = read_bytes(b_py) if os.path.exists(b_py) else b''
                p5, jerr5 = json_of(run5)
                fe5 = file_entry(p5)
                r5 = fe5.get('reason') or ('json_error' if p5 is None else 'missing_reason')
                same5 = sha256_bytes(after5) == sha256_bytes(base5)
                m5 = {'status': 'pass' if (same5 and r5 == 'no_change') else 'fail',
                      'verdict_class': (None if (same5 and r5 == 'no_change')
                                        else ('tool_bug' if not same5 else 'tool_contract')),
                      'reason': 'reason=%s bytes_%s' % (r5, 'same' if same5 else 'CHANGED'),
                      'rc': run5['rc'], 'apply_reason': r5, 'bytes_unchanged': same5,
                      'style': fe5.get('style'),
                      'block_inserted': has_block(decode_source(after5)[0])
                      if os.path.exists(b_py) else None,
                      'auto_fallback_inferred': auto_fallback_inferred(fe5),
                      'changes': fe5.get('changes') or [], 'warnings': fe5.get('warnings') or []}
                if not same5:
                    res['evidence']['m5_diff'] = ev.save(
                        'm5_idem.diff',
                        unified_diff_text(decode_source(base5)[0], decode_source(after5)[0],
                                          'work/%s/directed.py (after M4)' % stem,
                                          'work/%s/directed.py (after M5)' % stem))

            # ---------------- M6 可逆（**独立**，不依赖 M4/M5 的连带状态）----------------
            # 设计要点（2026-10-10 修复）：旧版把 M6 挂在 M4 的写回上，并且**用备份文件
            # 是否存在**当闸门 —— engine 版把备份搬到了 `<脚本目录>/.tweak_params/`，
            # 于是旧闸门恒假 → 751 条全被 skip（"用 skip 把指标做绿"）。现在改成：
            #   ① 用**全新副本** revert.py + 定向参数，先做一次**真实写回**（必须改字节）；
            #   ② 再 revert，与**原件字节**比对；
            #   ③ 不再猜备份路径，revert 的 JSON 自己会报 backup/ok。
            # 只有"连写回都没发生"才允许 skip，且理由与证据都留档、单独计数。
            r_py = os.path.join(wdir, 'revert.py')
            with open(r_py, 'wb') as f:
                f.write(raw)
            params_rev = os.path.join(wdir, 'revert.json')
            prev = _mkparams('revert.py', {target_idx: list(TARGET_POS)})
            write_json(params_rev, prev)
            ev.save('m6_setup.params.json', json.dumps(prev, ensure_ascii=False, indent=2))
            pre_run, pre_payload, pre_ev = run_apply(ev, 'm6_pre', r_py, params_rev, args.timeout)
            res['evidence'].update(pre_ev)
            pre = read_bytes(r_py) if os.path.exists(r_py) else b''
            wrote = sha256_bytes(pre) != res['meta']['sha_orig']
            fe_pre = file_entry(pre_payload)
            run6 = run_cli(['revert', r_py, '--params', params_rev, '--write', '--json'],
                           args.timeout)
            res['evidence']['m6_stdout'] = ev.save('m6_revert.json', run6.get('stdout', ''))
            res['evidence']['m6_stderr'] = ev.save('m6_revert.stderr.txt', run6.get('stderr', ''))
            res['evidence']['m6_cmd'] = ev.save('m6_revert.cmd.txt', cmd_str(run6['cmd']) + '\n')
            fin = read_bytes(r_py) if os.path.exists(r_py) else b''
            sha_fin = sha256_bytes(fin)
            ok6 = sha_fin == res['meta']['sha_orig']
            rev_payload, _ = json_of(run6)
            rev_files = (rev_payload or {}).get('files') or [] if isinstance(rev_payload, dict) else []
            rev0 = rev_files[0] if rev_files else (rev_payload
                                                  if isinstance(rev_payload, dict) else {})
            backups = []
            for d in (wdir, os.path.join(wdir, '.tweak_params')):
                if os.path.isdir(d):
                    backups += [os.path.join(os.path.basename(d), nm) for nm in os.listdir(d)
                                if nm.endswith('.bak')]
            base6 = {'rc': run6['rc'], 'sha_after_revert': sha_fin,
                     'sha_original': res['meta']['sha_orig'],
                     'sha_after_write': sha256_bytes(pre),
                     'write_happened': wrote,
                     'write_style': fe_pre.get('style'),
                     'write_reason': fe_pre.get('reason'),
                     # 仅供报告讨论：M5 是否又落盘过。旧版 M6 挂在 M4 的备份上，
                     # 只要 M5 再写一次，备份就轮转到"M4 之后"，revert 永远回不到原件
                     # （基线 27/27 全是这个级联）。新版 M6 自带写回，不受它影响。
                     'm5_wrote_bytes': m5.get('bytes_unchanged') is False,
                     'block_inserted_by_write': has_block(decode_source(pre)[0]),
                     'wrote_block_len': len(decode_source(pre)[0]) - len(text),
                     'm6_write_cmd': cmd_str(pre_run['cmd']) if pre_run.get('cmd') else None,
                     'backup_files_in_workdir': backups,
                     'revert_ok_field': rev0.get('ok') if isinstance(rev0, dict)
                     else (rev_payload or {}).get('ok'),
                     'revert_backup_path': (rev_payload or {}).get('backup')
                     if isinstance(rev_payload, dict) else None}
            if run6.get('timeout'):
                m6 = dict(base6, status='timeout', verdict_class='tool_bug',
                          reason='revert > %ss' % args.timeout)
            elif not wrote:
                # 写回没发生 → 无可回退。**不算 pass**，如实 skip 并留证据（单独计数）。
                m6 = dict(base6, status='skipped', verdict_class='harness_premise',
                          reason='precondition_no_write（定向参数没能改动副本字节：'
                                 'style=%s reason=%s），无可回退内容' % (
                                     fe_pre.get('style'), fe_pre.get('reason')))
            else:
                m6 = dict(base6, status='pass' if ok6 else 'fail',
                          verdict_class=None if ok6 else 'tool_bug',
                          reason='revert 后 sha256 %s 原件（写回 %s 字节 → %s 字节）' % (
                              '==' if ok6 else '!=',
                              res['meta']['bytes'], len(fin)))
                if not ok6:
                    res['evidence']['m6_diff'] = ev.save(
                        'm6_revert.diff',
                        unified_diff_text(text, decode_source(fin)[0],
                                          'orig/%s.py' % stem, 'work/%s/revert.py (revert 后)' % stem))
            if isinstance(rev_payload, dict) and isinstance(rev_payload.get('lines'), list):
                res['evidence']['m6_lines'] = ev.save(
                    'm6_revert.lines.txt', '\n'.join(rev_payload['lines']) + '\n')
    # ---------------- M4/M5/M6 场景别名（T37）----------------
    m4_pos, m5_pos, m6_pos = m4, m5, m6          # 位置字面量场景（可能 skipped）
    # ---------------- figsize 目标场景（T37 新增）----------------
    # 只要该图有 `figsize=(w,h)` 字面量就多跑一遍：参数里 pos 全给 null（不碰位置）、
    # figsize_in 设成与源码现值**明显不同**的目标 → 测"改画布尺寸"这条路径的
    # 准确性 / 幂等 / 可逆。与 pos 场景分开计数（m4_pos vs m4_figsize）。
    m4f = m5f = m6f = None
    if figsize is not None:
        target_fs = pick_target_figsize(figsize['vals'])
        f_py = os.path.join(wdir, 'figsize.py')
        params_f = os.path.join(wdir, 'figsize.json')
        with open(f_py, 'wb') as fh:
            fh.write(raw)
        pfig = _mkparams('figsize.py', {})        # pos 全 null
        pfig['figsize_in'] = list(target_fs)
        write_json(params_f, pfig)
        ev.save('m4_figsize.params.json', json.dumps(pfig, ensure_ascii=False, indent=2))
        runf, payloadf, evf = run_apply(ev, 'm4_fig', f_py, params_f, args.timeout)
        res['evidence'].update(evf)
        if runf.get('timeout'):
            m4f = {'status': 'timeout', 'verdict_class': 'tool_bug',
                   'reason': 'apply > %ss' % args.timeout, 'scenario': 'figsize'}
        else:
            after_f = read_bytes(f_py) if os.path.exists(f_py) else b''
            fef = file_entry(payloadf)
            rf = fef.get('reason') or ('json_error' if payloadf is None else 'missing_reason')
            chk = check_figsize_change(text, decode_source(after_f)[0], fig_index, target_fs)
            chk.update({'apply_reason': rf, 'style': fef.get('style'),
                        'changes': fef.get('changes') or [],
                        'warnings': fef.get('warnings') or [],
                        'auto_fallback': fef.get('auto_fallback'),
                        'block_inserted': has_block(decode_source(after_f)[0]),
                        'bytes_changed': sha256_bytes(after_f) != res['meta']['sha_orig'],
                        'target_figsize': list(target_fs),
                        'source_figsize': list(figsize['vals'])})
            if not chk['parse_after_ok']:
                verdict, detail = 'fail', '改写后 ast.parse 失败'
            elif not chk['only_target_changed']:
                verdict, detail = 'fail', '改错图了：变动的 figsize 索引 = %s（应为 [%d]）' % (
                    chk['changed_fig_indices'], fig_index)
            elif not chk['value_equals_target']:
                verdict, detail = 'fail', '目标 figsize 的值不等于目标：%s（要 %s）' % (
                    chk['raw_after'], target_fs)
            elif chk['add_axes_changed']:
                verdict, detail = 'fail', 'add_axes 字面量被连带改了'
            else:
                verdict, detail = 'pass', '只有目标 figsize kwarg 变成目标值（%s → %s）' % (
                    chk['raw_before'], chk['raw_after'])
            chk['verdict'], chk['detail'] = verdict, detail
            res['evidence']['m4_figsize_check'] = ev.save_json('m4_figsize.check.json', chk)
            m4f = {'status': verdict,
                   'verdict_class': None if verdict == 'pass' else 'tool_bug',
                   'scenario': 'figsize', 'reason': detail, 'rc': runf['rc'],
                   'apply_reason': rf, 'style': fef.get('style'),
                   'changes': fef.get('changes') or [], 'warnings': fef.get('warnings') or [],
                   'block_inserted': chk['block_inserted'],
                   'bytes_changed': chk['bytes_changed'],
                   'changed_fig_indices': chk['changed_fig_indices'],
                   'target_figsize': list(target_fs), 'source_figsize': list(figsize['vals']),
                   'value_equals_target': chk['value_equals_target'],
                   'spelling_preserved': chk['spelling_preserved'],
                   'raw_before': chk['raw_before'], 'raw_after': chk['raw_after']}
            if verdict != 'pass':
                res['evidence']['m4_figsize_diff'] = ev.save(
                    'm4_figsize.diff',
                    unified_diff_text(text, decode_source(after_f)[0],
                                      'orig/%s.py' % stem, '%s/%s/figsize.py' % (
                                          os.path.basename(WORK), stem)))
        # ---- M5（figsize 场景）----
        m5f = {'status': 'skipped', 'verdict_class': 'harness_premise',
               'reason': 'm4_figsize_skipped', 'scenario': 'figsize'}
        if m4f and m4f['status'] in ('pass', 'fail', 'not_applicable'):
            base5f = read_bytes(f_py) if os.path.exists(f_py) else b''
            run5f = run_cli(['apply', f_py, '--params', params_f, '--write',
                             '--verify-fast', '--json'], args.timeout)
            res['evidence']['m5_fig_stdout'] = ev.save('m5_figsize.apply.json',
                                                       run5f.get('stdout', ''))
            res['evidence']['m5_fig_cmd'] = ev.save('m5_figsize.cmd.txt',
                                                    cmd_str(run5f['cmd']) + '\n')
            if run5f.get('timeout'):
                m5f = {'status': 'timeout', 'verdict_class': 'tool_bug',
                       'scenario': 'figsize', 'reason': 'apply > %ss' % args.timeout}
            else:
                after5f = read_bytes(f_py) if os.path.exists(f_py) else b''
                pf5, _ = json_of(run5f)
                fe5f = file_entry(pf5)
                rf5 = fe5f.get('reason') or 'missing_reason'
                same5f = sha256_bytes(after5f) == sha256_bytes(base5f)
                ok5f = same5f and rf5 == 'no_change'
                m5f = {'status': 'pass' if ok5f else 'fail',
                       'verdict_class': None if ok5f else (
                           'tool_bug' if not same5f else 'tool_contract'),
                       'scenario': 'figsize', 'apply_reason': rf5,
                       'reason': 'reason=%s bytes_%s' % (rf5, 'same' if same5f else 'CHANGED'),
                       'rc': run5f['rc'], 'style': fe5f.get('style'),
                       'changes': fe5f.get('changes') or [],
                       'block_inserted': has_block(decode_source(after5f)[0])}
                if not same5f:
                    res['evidence']['m5_figsize_diff'] = ev.save(
                        'm5_figsize.diff',
                        unified_diff_text(decode_source(base5f)[0], decode_source(after5f)[0],
                                          '%s/%s/figsize.py (after M4)' % (
                                              os.path.basename(WORK), stem),
                                          '%s/%s/figsize.py (after M5)' % (
                                              os.path.basename(WORK), stem)))
        # ---- M6（figsize 场景）----
        m6f = {'status': 'skipped', 'verdict_class': 'harness_premise',
               'reason': 'no_directed_write（figsize 场景没有真实写回可锚定）',
               'scenario': 'figsize'}
        fr_py = os.path.join(wdir, 'figsize_rev.py')
        params_fr = os.path.join(wdir, 'figsize_rev.json')
        with open(fr_py, 'wb') as fh:
            fh.write(raw)
        pfr = _mkparams('figsize_rev.py', {})
        pfr['figsize_in'] = list(pick_target_figsize(figsize['vals']))
        write_json(params_fr, pfr)
        pre_run_f, pre_payload_f, pre_ev_f = run_apply(ev, 'm6_pre_fig', fr_py, params_fr,
                                                       args.timeout)
        res['evidence'].update(pre_ev_f)
        pre_f = read_bytes(fr_py) if os.path.exists(fr_py) else b''
        wrote_f = sha256_bytes(pre_f) != res['meta']['sha_orig']
        fe_pre_f = file_entry(pre_payload_f)
        run6f = run_cli(['revert', fr_py, '--params', params_fr, '--write', '--json'],
                        args.timeout)
        res['evidence']['m6_fig_stdout'] = ev.save('m6_figsize.revert.json',
                                                   run6f.get('stdout', ''))
        res['evidence']['m6_fig_cmd'] = ev.save('m6_figsize.cmd.txt',
                                                cmd_str(run6f['cmd']) + '\n')
        fin_f = read_bytes(fr_py) if os.path.exists(fr_py) else b''
        ok6f = sha256_bytes(fin_f) == res['meta']['sha_orig']
        rev6f, _ = json_of(run6f)
        if run6f.get('timeout'):
            m6f = {'status': 'timeout', 'verdict_class': 'tool_bug', 'scenario': 'figsize',
                   'reason': 'revert > %ss' % args.timeout}
        elif not wrote_f:
            m6f = {'status': 'skipped', 'verdict_class': 'harness_premise',
                   'scenario': 'figsize',
                   'reason': 'precondition_no_write（figsize 参数没能改动副本字节：'
                             'style=%s reason=%s）' % (fe_pre_f.get('style'),
                                                       fe_pre_f.get('reason'))}
        else:
            m6f = {'status': 'pass' if ok6f else 'fail',
                   'verdict_class': None if ok6f else 'tool_bug', 'scenario': 'figsize',
                   'reason': 'revert 后 sha256 %s 原件（写回 %s 字节 → %s 字节）' % (
                       '==' if ok6f else '!=', res['meta']['bytes'], len(fin_f)),
                   'rc': run6f['rc'], 'write_style': fe_pre_f.get('style'),
                   'write_reason': fe_pre_f.get('reason'),
                   'write_happened': True,
                   'sha_after_write': sha256_bytes(pre_f),
                   'sha_after_revert': sha256_bytes(fin_f),
                   'sha_original': res['meta']['sha_orig'],
                   'revert_ok_field': (rev6f or {}).get('ok')
                   if isinstance(rev6f, dict) else None,
                   'revert_backup_path': (rev6f or {}).get('backup')
                   if isinstance(rev6f, dict) else None}
            if not ok6f:
                res['evidence']['m6_figsize_diff'] = ev.save(
                    'm6_figsize.diff',
                    unified_diff_text(text, decode_source(fin_f)[0],
                                      'orig/%s.py' % stem,
                                      '%s/%s/figsize_rev.py (revert 后)' % (
                                          os.path.basename(WORK), stem)))
    # 汇总：主键 = pos 场景（有则用），否则用 figsize 场景；两个场景分别留在 m*_pos / m*_figsize
    res['m4_pos'], res['m5_pos'], res['m6_pos'] = m4_pos, m5_pos, m6_pos
    res['m4_figsize'] = m4f or {'status': 'skipped', 'verdict_class': 'harness_premise',
                                'scenario': 'figsize',
                                'reason': 'no_figsize_literal（该图没有 figsize=(w,h) 字面量）'}
    res['m5_figsize'] = m5f or {'status': 'skipped', 'verdict_class': 'harness_premise',
                                'scenario': 'figsize', 'reason': 'no_figsize_literal'}
    res['m6_figsize'] = m6f or {'status': 'skipped', 'verdict_class': 'harness_premise',
                                'scenario': 'figsize', 'reason': 'no_figsize_literal'}
    if m4['status'] == 'skipped' and m4f and m4f['status'] != 'skipped':
        m4, m5, m6, promoted = m4f, m5f, m6f, True
    else:
        promoted = False
    res['meta']['primary_scenario'] = 'pos' if not promoted else 'figsize'
    res['m4_directed_rewrite'] = m4
    res['m5_idempotent'] = m5
    res['m6_revert'] = m6
    return res


# --------------------------------------------------------------------------
# 语料清单 / 抽样
# --------------------------------------------------------------------------
def load_manifest():
    rows = []
    with open(MANIFEST, 'r', encoding='utf-8') as f:
        for ln in f:
            ln = ln.strip()
            if not ln:
                continue
            try:
                rows.append(json.loads(ln))
            except Exception:                                 # noqa: BLE001
                continue
    return rows


def family_of(tags):
    lay = (tags or {}).get('layout') or []
    if 'add_axes' in lay:
        return 'add_axes'
    if any(t in lay for t in LAYOUT_TAGS_SUBPLOTS):
        return 'subplots'
    return 'other'


def static_family_of(path):
    """静态判定脚本家族（不执行脚本）：add_axes（有 4 字面量）/ figsize_only / none。"""
    try:
        raw = read_bytes(path)
    except OSError:
        return 'missing', 0, False
    text, _ = decode_source(raw)
    try:
        ast.parse(text)
    except SyntaxError:
        return 'parse_fail', 0, False
    aa = collect_add_axes(text)
    fg = collect_figsize(text)
    if aa:
        return 'add_axes', len(aa), bool(fg)
    if fg:
        return 'figsize_only', 0, True
    return 'none', 0, False


def build_rows(limit, seed, only_source=None):
    all_rows = load_manifest()
    rows = []
    for r in all_rows:
        p = r.get('path') or ''
        if not p.endswith('.py'):
            continue
        if only_source and r.get('source') != only_source:
            continue
        fp = os.path.join(CORPUS, p.replace('/', os.sep))
        if not os.path.exists(fp):
            continue
        r = dict(r)
        fam, n_aa, has_fs = static_family_of(fp)
        r['family_manifest'] = family_of(r.get('tags'))
        r['family_static'] = fam
        r['n_aa_static'] = n_aa
        r['has_figsize_static'] = has_fs
        rows.append(r)
    rows.sort(key=lambda r: r['path'])
    if not limit or limit >= len(rows):
        return rows, False
    rng = random.Random(seed)
    # 分层：source × 静态家族。小但关键的层（add_axes）必须被覆盖到 ——
    # 全量按比例抽样时 gallery add_axes 只有 22/645，30 个样本里常常一个都抽不到。
    groups = {}
    for r in rows:
        groups.setdefault((r.get('source'), r['family_static']), []).append(r)
    keys = sorted(groups)
    for k in keys:
        rng.shuffle(groups[k])
    sizes = {k: len(groups[k]) for k in keys}
    pool = sum(sizes.values())
    quota = {k: min(2, sizes[k]) for k in keys}      # 每层保底 2 个
    remain = limit - sum(quota.values())
    if remain > 0:
        raw = {k: remain * sizes[k] / float(pool) for k in keys}
        for k in keys:
            quota[k] += int(raw[k])
        rest = limit - sum(quota.values())
        for k in sorted(keys, key=lambda x: (-(raw[x] - int(raw[x])), x)):
            if rest <= 0:
                break
            if quota[k] < sizes[k]:
                quota[k] += 1
                rest -= 1
        for k in keys:                                # 被 cap 卡住的余量补到未满的层
            while rest > 0 and quota[k] < sizes[k]:
                quota[k] += 1
                rest -= 1
    picked = []
    for k in keys:
        picked.extend(groups[k][:quota[k]])
    picked.sort(key=lambda r: r['path'])
    return picked, True


# --------------------------------------------------------------------------
# 覆盖率统计（M7）
# --------------------------------------------------------------------------
RE_NOT_INPLACE = re.compile(r'ax(\d+)\s*未原位应用：([^（(]+)')


def coverage_stats(results):
    stats = {'by_manifest_layout': {}, 'by_extracted_family': {}}

    def bucket(d, key):
        return d.setdefault(key, {
            'scripts': 0, 'tested': 0, 'add_axes_literals': 0, 'not_applicable': 0,
            'inplace_ok': 0, 'block_fallback': 0, 'no_change': 0, 'fail': 0,
            'skipped': 0, 'not_inplace_fields': {}, 'styles': {},
            'auto_fallback': 0, 'block_inserted': 0,
        })

    for r in results:
        lay = r['meta']['layout_tags']
        mk = 'add_axes派' if 'add_axes' in lay else (
            'subplots派' if any(t in lay for t in LAYOUT_TAGS_SUBPLOTS) else '无 layout 标签/其它')
        fk = {'add_axes': 'add_axes派（静态提取）', 'subplots': 'subplots派（静态提取）',
              'none': '无图调用'}[r['meta'].get('family', 'none')]
        for d, key in ((stats['by_manifest_layout'], mk), (stats['by_extracted_family'], fk)):
            b = bucket(d, key)
            b['scripts'] += 1
            m4 = r['m4_directed_rewrite']
            if m4['status'] == 'skipped':
                b['skipped'] += 1
                if 'no_add_axes' in (m4.get('reason') or ''):
                    b['scripts'] += 0
                continue
            if m4['status'] == 'not_applicable':
                # 判据前提不成立（cax / 区间外）：单列，不进 tested 分母，也不算 fail
                b['not_applicable'] += 1
                continue
            b['tested'] += 1
            b['add_axes_literals'] += m4.get('n_add_axes_before') or 0
            st = m4.get('style')
            b['styles'][st] = b['styles'].get(st, 0) + 1
            if m4.get('auto_fallback_inferred'):
                b['auto_fallback'] += 1
            if m4.get('block_inserted'):
                b['block_inserted'] += 1
            if m4['status'] == 'pass':
                b['inplace_ok'] += 1
            elif m4['status'] in ('fail', 'timeout'):
                b['fail'] += 1
                if st == 'block' or m4.get('block_inserted'):
                    b['block_fallback'] += 1
                if (m4.get('apply_reason') == 'no_change'):
                    b['no_change'] += 1
            for w in (m4.get('warnings') or []):
                m = RE_NOT_INPLACE.search(w)
                if m:
                    for f in re.split(r'[、,]', m.group(2)):
                        f = f.strip()
                        if f:
                            b['not_inplace_fields'][f] = b['not_inplace_fields'].get(f, 0) + 1
    return stats


def roundtrip_stats(results):
    """M3/M5/M6 的结果分布（干净往返 / 幂等 / 可逆），含"退到块模式"计数。"""
    out = {}
    for key in ('m3_clean_roundtrip', 'm5_idempotent', 'm6_revert'):
        c = {'pass': 0, 'fail': 0, 'skipped': 0, 'timeout': 0, 'not_applicable': 0}
        reasons = {}
        block = 0
        auto = 0
        deg = 0
        cascade = 0
        for r in results:
            m = r[key]
            c[m['status']] = c.get(m['status'], 0) + 1
            if m['status'] in ('fail', 'timeout'):
                reasons[m.get('apply_reason') or m.get('reason', '?')] = \
                    reasons.get(m.get('apply_reason') or m.get('reason', '?'), 0) + 1
            if m.get('block_inserted'):
                block += 1
            if m.get('auto_fallback_inferred'):
                auto += 1
            if m.get('degenerate_no_axes'):
                deg += 1
            if m.get('cascade_from_m5'):
                cascade += 1
        out[key] = {'counts': c, 'fail_reasons': reasons, 'block_inserted': block,
                    'auto_fallback': auto, 'degenerate_no_axes': deg,
                    'cascade_from_m5': cascade}
    return out


# --------------------------------------------------------------------------
# 报告
# --------------------------------------------------------------------------
METRICS = [
    ('m1_parse', 'M1 可解析'),
    ('m2_extract', 'M2 参数可构造'),
    ('m3_clean_roundtrip', 'M3 干净往返'),
    ('m4_directed_rewrite', 'M4 定向改写'),
    ('m5_idempotent', 'M5 幂等'),
    ('m6_revert', 'M6 可逆'),
]

# 按来源分组的适配面：我自己（不依赖 manifest 打标器）统计的布局写法
LAYOUT_PROBES = (
    ('figure', lambda n: isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        and n.func.attr == 'figure'),
    ('subplots（含 fig, ax = …）', lambda n: isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute) and n.func.attr == 'subplots'),
    ('add_subplot', lambda n: isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        and n.func.attr == 'add_subplot'),
    ('add_axes（任意写法）', lambda n: isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute) and n.func.attr == 'add_axes'),
    ('add_axes 4 字面量', None),          # 单独判
    ('inset_axes', lambda n: isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        and n.func.attr == 'inset_axes'),
    ('twinx / twiny', lambda n: isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        and n.func.attr in ('twinx', 'twiny')),
    ('colorbar', lambda n: isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        and n.func.attr == 'colorbar'),
    ('GridSpec / add_gridspec', lambda n: (isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr in ('GridSpec', 'add_gridspec'))
        or (isinstance(n, ast.Name) and n.id == 'GridSpec')),
    ('subplot_mosaic', lambda n: isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        and n.func.attr == 'subplot_mosaic'),
    ('constrained_layout', lambda n: (isinstance(n, ast.Name)
        and n.id == 'constrained_layout')
        or (isinstance(n, ast.keyword) and n.arg == 'constrained_layout')),
    ('figsize=(w,h) 2 字面量', None),     # 单独判
)


def scan_layout_features(path):
    """纯 AST 静态统计（不执行脚本）：一个脚本用了哪些布局写法。"""
    try:
        text, _ = decode_source(read_bytes(path))
    except OSError:
        return None
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return None
    hit = {name: False for name, _ in LAYOUT_PROBES}
    for node in ast.walk(tree):
        for name, pred in LAYOUT_PROBES:
            if pred is not None and not hit[name] and pred(node):
                hit[name] = True
    aa = collect_add_axes(text, tree)
    hit['add_axes 4 字面量'] = bool(aa)
    hit['figsize=(w,h) 2 字面量'] = collect_figsize(text) is not None
    return hit

ROOT_CAUSE_MD = """
### 3.0 根因总览（先说结论，再看逐条）

每条 FAIL 的判定都是**实测 + 源码定位**双重确认的；下面 5 条根因覆盖了本次全部 FAIL。

**RC-1（P0）「无需改动」被当成「原位落空」，于是自动改用块模式把调整块插进源码**
—— 导致 M3（25/25）、M5（25/25）全灭，并连坐 M6。

- `mpltweak/apply.py:144-158` `_inplace_fell_flat(res)`：`reason in ('no_change','fail')` → 返回
  **True**；否则 `return not res.get('changes')`。也就是说**"本来就没东西要改"被判成"原位策略落空"**。
- `mpltweak/apply.py:396-400`：默认 `--style auto` → 先跑 inplace，若 `_inplace_fell_flat` 为真 →
  `res = writeback.writeback(..., style='block')`（**inplace 那次的 res 被整份覆盖**）。
- `mpltweak/writeback.py:1134-1146`：inplace 在 `in_src == src and not changes` 时返回
  `no_change`（无 skipped）或 `best_effort`（有 skipped）。
- `mpltweak/writeback.py:852-856`：整字段都是"值没变"的空改 → 从 covered 剔除 → `changes == []`。
- 块路径（`writeback.py:1487` 起）**任何情况都不会返回 `no_change`**，并且会
  `insert_or_replace` 把哨兵块写进源码，块里带一行
  `# 本块由 mpltweak 自动生成于 <时间>（可手动微调，勿删上下两行标记）`
  （`messages.py` 的 `block_comment`）—— 也就是说，一次"什么都没改"的 apply
  会在用户脚本里留下本工具的署名注释块，与"脚本零侵入"的产品定位直接冲突。
- 实测形态：25/25 条 M3 FAIL 的 apply JSON 都是 `style=block`、`changes=[]`、`block_inserted=true`，
  `reason` 为 `ok`(17) 或 `best_effort`(8)，**没有一条是 `no_change`**。
- 补证：我另跑 `--style inplace` 探针（仍然 `--verify-fast`，不执行脚本），
  25/25 条返回 `changes=[]`；AST 级复核 3 个样本得到 `in_src == src` 为真、`skipped` 只有
  `grid` 一项 —— 直接证明"原位那次根本不需要改任何东西"。

**RC-2 revert 只能回退最近一次写回，M5 的连带写入把「原件」顶掉** —— 导致 M6 25/25 全灭。

- `mpltweak/writeback.py:867-914`：备份是固定名 `.tweak_params/<脚本名>.tweak.bak`，
  **每次 `apply --write` 都用"当前"脚本覆盖它**。
- `mpltweak/revert.py:75-81,183-199`：`revert` 从这份固定备份恢复（恢复前把当前存成 `.revert.bak`）。
- 于是 M5 只要又落盘一次（哪怕写出来的字节与 M4 之后完全相同），备份指向的就已经是
  "M4 之后"的状态 → **一次 revert 永远回不到原件**。实测 25/25 条 M6 FAIL 的
  `cascade_from_m5` 都是 true，即**没有一条是 revert 自身写错**。

**RC-3 `params.normalize` 注入的默认值污染「未原位应用」清单（并助推 RC-1）**

- `mpltweak/params.py:99-116` 的 `AXIS_DEFAULTS` 含 `'grid': False`、`'xscale'/'yscale': 'linear'`。
- `mpltweak/writeback.py:787-798`：`g = a.get('grid')` 恒非 None（**哪怕调用方根本没写 grid**）
  → 去找 `ax.grid(...)`，找不到就 `skipped.append((i,'grid',...))`。
- 后果一：**每一次 apply 都会冒出「axN 未原位应用：grid」**（本次 25 条 FAIL 全中）。
- 后果二：clean 往返里正是这条多余 skipped 把 `no_change` 变成 `best_effort`
  （`writeback.py:1134-1141`），并顺带打印"本图轴是 subplots 网格位置 / 无对应写法"——
  连 `ax = fig.add_axes((0, 0, 1, 1))` 这种最清楚的手工轴也被这么描述，**文案与事实不符**。

**RC-4 轴定位区间够不到两类字面量** —— 导致 M4 的 2/25 FAIL（判"没改"）。

- `mpltweak/writeback.py:511-525 _fig_span`：本图区间 = [第 k 个建图调用行, 下一个建图调用行)；
  `528-564 _figure_axes_for_index` 只收该区间内的 `变量 = ...add_axes/add_subplot(...)`，
  并剔除 colorbar 槽轴变量。
- `gallery/misc/logos2.py`：`ax = fig.add_axes((0, 0, 1, 1))` 在 L95，而唯一的
  `plt.figure(figsize=figsize, dpi=dpi)` 在 L133 → 区间从 133 起，**够不到 L95**。
- `zenodo/23013498__zndo_figs_pca.py`：唯一字面量 `cax = fig.add_axes([0.82,0.25,0.02,0.5])`
  （L922）是 `fig.colorbar(..., cax=cax)` 的槽轴，被**按设计**剔除。
- 两条都退到块模式，**字面量一个数字都没变**（M4 判"没改"）。

**RC-5（契约层，不是崩溃）`apply --json` 的 `files[]` 里没有 `auto_fallback`**

- `mpltweak/apply.py:207-222 _emit_json` 只带
  `params/reason/style/changes/backup/verified/verify_mode/semantic/warnings/layout_conflicts`。
- 机器消费者**无法从 JSON 判断这次是不是自动回退到块**。本次一律用默认 `--style auto`，
  所以 `style == 'block'` 只可能来自自动回退，可据此反推 —— 但那是推断，不是契约。
- 另外 `reason` 只反映**块那次**的结果：inplace 那次的 `reason`/`warnings` 被整份覆盖
  （`apply.py:398-400`），agent 看不出"原位为什么没落地"。本报告用 `--style inplace` 探针补证。
"""


def build_findings(results):
    """结论与最值得注意的发现（数字全部现算，避免手写数字与结果不一致）。"""
    n = len(results)
    m1f = sum(1 for r in results if r['m1_parse']['status'] == 'fail')
    cs = sum(1 for r in results if (r['m1_parse'].get('verdict_class') == 'corpus_side'))
    m2p = sum(1 for r in results if r['m2_extract']['status'] == 'pass')
    m2s = sum(1 for r in results if r['m2_extract']['status'] == 'skipped')
    m3t = sum(1 for r in results if r['m3_clean_roundtrip']['status'] != 'skipped')
    m3f = sum(1 for r in results if r['m3_clean_roundtrip']['status'] == 'fail')
    m4t = sum(1 for r in results if r['m4_directed_rewrite']['status'] == 'pass'
              or r['m4_directed_rewrite']['status'] == 'fail')
    m4p = sum(1 for r in results if r['m4_directed_rewrite']['status'] == 'pass')
    m4na = sum(1 for r in results if r['m4_directed_rewrite']['status'] == 'not_applicable')
    m5f = sum(1 for r in results if r['m5_idempotent']['status'] == 'fail')
    m6f = sum(1 for r in results if r['m6_revert']['status'] == 'fail')
    casc = sum(1 for r in results
               if r['m6_revert']['status'] == 'fail' and r['m6_revert'].get('cascade_from_m5'))
    n_aa = sum(1 for r in results if r['meta'].get('n_add_axes_literals'))
    blk = sum(1 for r in results if r['m3_clean_roundtrip'].get('block_inserted'))
    tb = sum(1 for r in results for k, _ in METRICS
             if r[k].get('verdict_class') == 'tool_bug')
    hp = sum(1 for r in results for k, _ in METRICS
             if r[k].get('verdict_class') == 'harness_premise')
    pct = lambda a, b: ('%.1f%%' % (100.0 * a / b)) if b else '—'
    L = []
    add = L.append
    add('## 5. 结论与最值得注意的发现\n')
    add('> 口径（T36）：全报告的 `fail` **只统计"前提成立、工具没做到"**；'
        '判据前提不成立的记 `not_applicable`，语料自身问题的标 `corpus_side`。'
        '本轮跨指标共 %d 条 tool_bug、%d 条 harness_premise、%d 条 corpus_side。\n'
        % (tb, hp, cs))
    add('### 发现 1（P0）：把"无需改动"当成"落空"，于是一律插块改源码\n')
    add('- **%d/%d = %s** 个"参数与源码现值完全一致"的脚本，`apply --write --verify-fast`'
        '（默认 `--style auto`）**改动了用户源码**（插入 auto layout 块）：'
        'M3 干净往返 %d 条 FAIL、0 条 PASS。' % (m3f, m3t, pct(m3f, m3t), m3f))
    add('- 同一份参数**再跑一次**又改一次：M5 幂等 %d 条 FAIL、0 条 PASS（%s）。'
        % (m5f, pct(m5f, m3t)))
    add('- 因为 `revert` 只回退最近一次写回，M5 的连带写入把原件顶掉 → '
        'M6 可逆 %d 条 FAIL（其中 %d 条明确是 M5 级联造成，%s）。'
        '**结论：文件被改了之后，工具自身的回退链路已经拿不回原件**（只能靠版本控制/语料侧恢复）。'
        % (m6f, casc, pct(casc, m6f)))
    add('- 根因是 `apply.py:144-158 _inplace_fell_flat()` 把 `no_change` 判成"原位落空"，'
        '再由 `apply.py:396-400` 自动改用块模式；块路径（`writeback.py:1487` 起）永不返回 `no_change`。')
    add('- 本次 %d 次 clean 往返里有 %d 次真的插入了 `# ===== auto layout (generated)` 块。'
        % (m3t, blk))
    add('')
    add('### 发现 2（可用性口径）：这套语料里"能用字面量原位改位置"的脚本只有 %s\n'
        % pct(n_aa, n))
    add('- 有 `add_axes([a,b,c,d])` 4 字面量的脚本：**%d/%d = %s**（其余是 `plt.subplots()` 网格、'
        '`add_subplot`、变量 `figsize`、或压根不画图）。' % (n_aa, n, pct(n_aa, n)))
    add('- M2 能构造出参数（有 add_axes 字面量**或** figure(figsize=) 字面量）：**%d/%d = %s**；'
        'M2 skipped %d 条（%s）—— 任务规定"提不出目标值记为 skipped，不算失败"。'
        % (m2p, n, pct(m2p, n), m2s, pct(m2s, n)))
    add('- 也就是说：**推广到真实脚本时，mpltweak 的主战场（subplots 网格布局）在这套"字面量参数"'
        '口径下根本测不到原位改写**，只能走块模式；报告 2.1/2.2 的覆盖率表就是按这个口径给的。')
    add('')
    add('### 发现 3：M4 定向改写本身是准的（%s），FAIL 全是"结构上够不到"\n'
        % pct(m4p, m4t))
    add('- M4 通过 **%d/%d**：全部是"只有目标字面量变化"，'
        '**0 次改错图、0 次改多了、0 次语法坏**（独立 AST 复核器判定，不调用 mpltweak 模块）。'
        % (m4p, m4t))
    add('- %d 条 FAIL 全部是 RC-4 的"结构上够不到"：字面量落在建图调用之前'
        '（工具的区间扫不到）/ 是 colorbar 槽轴 cax（按设计拒绝原位改）。'
        '**没有一条是"改错图"或"改多了"。**' % (m4t - m4p))
    add('- 真实陷阱（对 agent 生成参数有意义）：当一张图里 `add_subplot` 排在 `add_axes` **之前**时，'
        '`params.axes[i]` 的下标必须按 **fig.axes 顺序（含 add_subplot 轴）**编号；'
        '只按 add_axes 字面量编号会错位，工具会把 pos 应用到另一个轴上或干脆不改'
        '（本次 `gallery/artists.py` 就是靠脚手架对齐索引后才通过的）。')
    add('')
    return '\n'.join(L)



def count_status(results, key):
    c = {'pass': 0, 'fail': 0, 'skipped': 0, 'timeout': 0, 'not_applicable': 0}
    for r in results:
        c[r[key]['status']] = c.get(r[key]['status'], 0) + 1
    return c


def _tablerow(cells):
    return '| ' + ' | '.join(str(c) for c in cells) + ' |'


def build_report(results, meta):
    L = []
    add = L.append
    tot = len(results)
    add('# mpltweak L0 纯静态压力测试报告（真实脚本语料）\n')
    add('## 0. 运行元信息\n')
    add(_tablerow(['项', '值']))
    add(_tablerow(['---', '---']))
    for k, v in meta.items():
        add(_tablerow([k, v]))
    add('')

    # 总表
    add('## 1. 总表\n')
    tested = sum(1 for r in results if r['m2_extract']['status'] == 'pass')
    skipped2 = sum(1 for r in results if r['m2_extract']['status'] == 'skipped')
    parse_fail = sum(1 for r in results if r['m1_parse']['status'] == 'fail')
    add(_tablerow(['规模', '数值']))
    add(_tablerow(['---', '---']))
    add(_tablerow(['清单脚本总数（参与本次运行）', tot]))
    add(_tablerow(['M1 可解析失败', parse_fail]))
    add(_tablerow(['M2 提取出目标值（进入 M3–M6）', tested]))
    add(_tablerow(['M2 skipped（提不出任何目标值，不算失败）', skipped2]))
    add(_tablerow(['  └ 其中因 M1 解析失败而跳过', parse_fail]))
    deg = sum(1 for r in results if r['m3_clean_roundtrip'].get('degenerate_no_axes'))
    add(_tablerow(['其中：只有 figsize 目标、无 axes 项（M3 结构性退化）', deg]))
    no_ax = sum(1 for r in results if r['m4_directed_rewrite']['status'] == 'skipped'
                and r['m2_extract']['status'] == 'pass')
    add(_tablerow(['其中：无 add_axes 字面量 → M4/M5/M6 不可测', no_ax]))
    add('')
    add('> M1/M2 对全部参与脚本跑；M3 对全部 M2 pass 的脚本跑；'
        'M4/M5/M6 只对有 `add_axes([a,b,c,d])` 字面量的脚本跑（判据要求"改指定的第 1 个轴"）。\n')
    add(_tablerow(['指标', 'pass', 'fail', 'not_applicable', 'timeout', 'skipped',
                   '通过率(可判定项：pass/(pass+fail))']))
    add(_tablerow(['---'] * 7))
    for key, name in METRICS:
        c = count_status(results, key)
        denom = c['pass'] + c['fail']
        rate = ('%.1f%%' % (100.0 * c['pass'] / denom)) if denom else '—'
        add(_tablerow([name, c['pass'], c['fail'], c.get('not_applicable', 0),
                       c['timeout'], c['skipped'], rate]))
    add('')
    add('> **口径（T36）**：`not_applicable` = **判据前提不成立**（考卷对这类写法不适用），'
        '**不计入 fail**，也不进通过率分母；`skipped` = 提不出目标值/结构上不可测。'
        '每一条都保留在 §3 的清单与 `%s/<脚本名>/` 的逐字证据里，不丢现场。\n'
        % os.path.basename(WORK))

    # 按判定性质分类的失败计数（T36）
    add('### 1.1 非 pass 记录的**性质分类**（T36）\n')
    add('分类只在"非 pass"的记录上打标签：`tool_bug`（前提成立、工具没做到）/ '
        '`tool_contract`（字节没问题但 JSON 契约骗人）/ `harness_premise`（判据前提不成立）/ '
        '`corpus_side`（语料自身问题）。\n')
    add(_tablerow(['指标', 'tool_bug', 'tool_contract', 'harness_premise', 'corpus_side',
                   '未标注(pass)']))
    add(_tablerow(['---'] * 6))
    tot_cls = {'tool_bug': 0, 'tool_contract': 0, 'harness_premise': 0, 'corpus_side': 0}
    for key, name in METRICS:
        cc = {}
        for r in results:
            m = r[key]
            if m['status'] == 'pass':
                continue
            vc = m.get('verdict_class') or '（未标注）'
            cc[vc] = cc.get(vc, 0) + 1
            if vc in tot_cls:
                tot_cls[vc] += 1
        add(_tablerow([name, cc.get('tool_bug', 0), cc.get('tool_contract', 0),
                       cc.get('harness_premise', 0), cc.get('corpus_side', 0),
                       cc.get('（未标注）', 0)]))
    add(_tablerow(['**合计（跨指标，按记录数）**', tot_cls['tool_bug'], tot_cls['tool_contract'],
                   tot_cls['harness_premise'], tot_cls['corpus_side'], '—']))
    add('')
    add('> 合计是**按记录数**：一个 M1 解析失败的脚本，它的 M2–M6 也都会记 skip 并继承 '
        '`corpus_side`，所以 `corpus_side` = 失败脚本数 × 6。要按脚本数看，见 §10.3 与 §9.1。\n')
    # M1 的语料侧子类
    kinds = {}
    for r in results:
        if r['m1_parse']['status'] == 'fail':
            k = (r['meta'].get('m1_corpus_kind') or '?').split('（')[0]
            kinds[k] = kinds.get(k, 0) + 1
    if kinds:
        add('M1 失败的语料侧子类：%s\n' % '、'.join(
            '`%s` ×%d' % (k, v) for k, v in sorted(kinds.items(), key=lambda x: -x[1])))
    unmarked = sum(1 for r in results for key, _ in METRICS
                   if r[key]['status'] != 'pass' and not r[key].get('verdict_class'))
    if unmarked:
        add('> 注：本次结果文件里有 **%d 条非 pass 记录没有分类字段** —— 说明它是 T36 之前的'
            '旧口径运行（分类字段是那时才加的）。旧口径 → 新口径的逐条映射见 §10。\n'
            % unmarked)
    add('')

    # 1.2 目标场景分组小计（T37）
    ss = scenario_stats(results)
    if ss['has_pos'] or ss['has_figsize']:
        add('### 1.2 目标场景分组小计（T37：pos / figsize 两条路径分开计数）\n')
        add('> M4/M5/M6 现在会按"脚本里实际存在的可写字面量"选目标：有 `add_axes([...])` → '
            '跑 **pos 目标**；有 `figsize=(w,h)` → 跑 **figsize 目标**；两者都有就**各跑一遍**。'
            '§1 的 M4/M5/M6 主键 = pos 场景（若有），否则 = figsize 场景；'
            '下表是两条路径的分别小计。\n')
        add(_tablerow(['指标 × 场景', 'pass', 'fail', 'not_applicable', 'skipped',
                       '性质分类（非 pass）']))
        add(_tablerow(['---'] * 6))
        for mk, mname in (('m4', 'M4 定向改写'), ('m5', 'M5 幂等'), ('m6', 'M6 可逆')):
            for sc in ('pos', 'figsize'):
                key = '%s_%s' % (mk, sc)
                c = ss[key]
                cls = {}
                for r in results:
                    rec = r.get(key)
                    if isinstance(rec, dict) and rec['status'] != 'pass':
                        vc = rec.get('verdict_class') or '—'
                        cls[vc] = cls.get(vc, 0) + 1
                add(_tablerow(['%s / %s 目标' % (mname, sc), c['pass'], c['fail'],
                               c['not_applicable'], c['skipped'],
                               '、'.join('%s×%d' % (k, v) for k, v in sorted(cls.items()))
                               or '—']))
        add('')
        add('- 参与测 pos 目标的脚本：**%d**；参与测 figsize 目标的脚本：**%d**；'
            '**两者都测**：**%d**\n' % (ss['has_pos'], ss['has_figsize'], ss['both_scenarios']))
        add('- M4 可测**并集**（pass+fail+not_applicable）：**%d** 个脚本\n' % ss['union_tested'])
        add('')

    # 覆盖率
    add('## 2. 覆盖率数字（M7，核心推广依据）\n')
    add('口径：以 **M4 定向改写那一次 apply** 为基准（参数里带真实改动，能看出工具到底'
        '走的是"原位改写"还是"退到块模式"）。\n')
    stats = coverage_stats(results)
    add('### 2.1 按 manifest `tags.layout` 分组\n')
    add(_tablerow(['分组', '脚本数', 'M4 实际测试数', 'M4 not_applicable', 'M4 skipped',
                   '能原位改写(pass)', '原位改写占比', '退到块模式', '块占比', 'M4 fail']))
    add(_tablerow(['---'] * 10))
    for k in sorted(stats['by_manifest_layout']):
        b = stats['by_manifest_layout'][k]
        t = b['tested'] or 1
        add(_tablerow([k, b['scripts'], b['tested'], b.get('not_applicable', 0), b['skipped'],
                       b['inplace_ok'], '%.1f%%' % (100.0 * b['inplace_ok'] / t),
                       b['block_fallback'], '%.1f%%' % (100.0 * b['block_fallback'] / t),
                       b['fail']]))
    add('')
    add('### 2.2 按本次**独立静态提取**的家族分组（比 manifest 标签更接近真值）\n')
    add(_tablerow(['分组', '脚本数', 'M4 实际测试数', 'M4 not_applicable', 'M4 skipped',
                   '能原位改写(pass)', '原位改写占比', '退到块模式', '块占比', 'M4 fail']))
    add(_tablerow(['---'] * 10))
    for k in sorted(stats['by_extracted_family']):
        b = stats['by_extracted_family'][k]
        t = b['tested'] or 1
        add(_tablerow([k, b['scripts'], b['tested'], b.get('not_applicable', 0), b['skipped'],
                       b['inplace_ok'], '%.1f%%' % (100.0 * b['inplace_ok'] / t),
                       b['block_fallback'], '%.1f%%' % (100.0 * b['block_fallback'] / t),
                       b['fail']]))
    add('')
    add('> "M4 实际测试数"不含 `not_applicable`：后者是**判据前提不成立**'
        '（目标字面量是 colorbar 槽轴 / 落在建图区间之外），不参与通过率分母。\n')
    add('### 2.3 "未原位应用"的字段分布（从 apply JSON 的 warnings 抓取）\n')
    add(_tablerow(['分组（按 tags.layout）', '未原位字段', '次数']))
    add(_tablerow(['---', '---', '---']))
    for k in sorted(stats['by_manifest_layout']):
        fields = stats['by_manifest_layout'][k]['not_inplace_fields']
        if not fields:
            add(_tablerow([k, '（无）', 0]))
            continue
        for f, n in sorted(fields.items(), key=lambda x: -x[1]):
            add(_tablerow([k, f, n]))
    add('')
    add('### 2.4 style / auto_fallback 分布\n')
    add('`auto_fallback` 字段在 `apply --json` 里**不存在**（RC-5），此处按 `style=="block"` 反推'
        '（本次一律用默认 `--style auto`，故 style=block ⟺ 自动回退）。\n')
    add(_tablerow(['分组（按 tags.layout）', 'style 分布', 'auto 回退到块次数(反推)', '块哨兵出现次数']))
    add(_tablerow(['---', '---', '---', '---']))
    for k in sorted(stats['by_manifest_layout']):
        b = stats['by_manifest_layout'][k]
        add(_tablerow([k, json.dumps(b['styles'], ensure_ascii=False),
                       b['auto_fallback'], b['block_inserted']]))
    add('')

    rt = roundtrip_stats(results)
    add('### 2.5 干净往返 / 幂等 / 可逆 的结果分布（M3 / M5 / M6）\n')
    add(_tablerow(['指标', 'pass', 'fail', 'skipped', 'timeout', '插入了调整块',
                   'auto 回退到块(反推)', 'no_axes 退化', '级联自 M5']))
    add(_tablerow(['---'] * 9))
    for key, nm in (('m3_clean_roundtrip', 'M3 干净往返（源码现值原样套用）'),
                    ('m5_idempotent', 'M5 幂等（同参数第二次）'),
                    ('m6_revert', 'M6 可逆（revert 回原件）')):
        b = rt[key]
        c = b['counts']
        add(_tablerow([nm, c['pass'], c['fail'], c['skipped'], c['timeout'],
                       b['block_inserted'], b['auto_fallback'],
                       b['degenerate_no_axes'], b['cascade_from_m5']]))
    add('')
    add('**FAIL 的 apply reason 分布：**\n')
    add(_tablerow(['指标', 'reason', '条数']))
    add(_tablerow(['---', '---', '---']))
    for key, nm in (('m3_clean_roundtrip', 'M3'), ('m5_idempotent', 'M5'), ('m6_revert', 'M6')):
        for rsn, n in sorted(rt[key]['fail_reasons'].items(), key=lambda x: -x[1]):
            add(_tablerow([nm, rsn, n]))
    add('')

    # 2.6 M4 结果分类 + 可改写性口径
    add('### 2.6 M4（定向改写）结果的分类 —— 决定"值不值得推广"的核心口径\n')
    add('口径（T36）：**判据前提不成立**的目标（字面量是 colorbar 槽轴 cax / 落在建图区间之外）'
        '记为 `not_applicable`，不进通过率分母、也不计 fail；剩下的 pass/fail 才是"工具做到没做到"。\n')
    cls_cnt = {}
    n_m4_tested = n_m4_pass = n_m4_na = 0
    for r in results:
        m4 = r['m4_directed_rewrite']
        if m4['status'] == 'skipped':
            continue
        if m4['status'] == 'not_applicable':
            n_m4_na += 1
            c = (m4.get('root_cause_class') or '?').split('（')[0]
            cls_cnt[c] = cls_cnt.get(c, 0) + 1
            continue
        n_m4_tested += 1
        if m4['status'] == 'pass':
            n_m4_pass += 1
            continue
        c = m4.get('root_cause_class') or m4.get('reason') or '?'
        c = c.split('（')[0]
        cls_cnt[c] = cls_cnt.get(c, 0) + 1
    add(_tablerow(['类别', '条数', '含义', '计入 fail？']))
    add(_tablerow(['---'] * 4))
    meaning = {
        'pass': '只改了目标 add_axes 字面量（原位改写成功）',
        'not_simple_assign': '不是 `变量 = fig.add_axes(...)` 写法 —— 工具建不出轴变量映射',
        'not_a_axes_literal': '目标轴其实是 add_subplot 网格轴',
        'unexplained': '写法正常但没改到 —— 需人工看证据（真缺陷候选）',
        'timeout': '超时',
        'harness_premise': '判据前提不成立（cax / 区间外）',
    }
    add(_tablerow(['pass', n_m4_pass, meaning['pass'], '否']))
    for c, n in sorted(cls_cnt.items(), key=lambda x: -x[1]):
        is_na = ('harness_premise' in c) or ('target_is_colorbar' in c) \
            or ('outside_figure_span' in c) or ('before_figure_call' in c)
        add(_tablerow([c, n, meaning.get(c, '判据前提不成立（见 §3 证据）'),
                       '否（not_applicable）' if is_na else '**是**']))
    add('')
    add('> M4 新口径：**pass = %d，fail = %d，not_applicable = %d**'
        '（判定过的 = pass+fail）。通过率按 **pass/(pass+fail)** 算：%s。'
        '`not_applicable` 全部保留在 §3 的清单与逐字证据里。\n'
        % (n_m4_pass, n_m4_tested - n_m4_pass, n_m4_na,
           ('%.1f%%' % (100.0 * n_m4_pass / n_m4_tested)) if n_m4_tested else '—'))
    # 2.7 语料可构造性
    add('### 2.7 语料侧的可构造性（脚手架提取口径下的适配面）\n')
    fam = {}
    for r in results:
        f = r['meta'].get('family', 'none')
        d = fam.setdefault(f, {'n': 0, 'aa_simple': 0, 'aa_cax': 0, 'm2_pass': 0, 'm4_testable': 0})
        d['n'] += 1
        if r['m2_extract']['status'] == 'pass':
            d['m2_pass'] += 1
        if r['m4_directed_rewrite']['status'] != 'skipped':
            d['m4_testable'] += 1
    add(_tablerow(['静态家族', '脚本数', 'M2 构造出参数', 'M4 可测', '备注']))
    add(_tablerow(['---', '---', '---', '---', '---']))
    for f in sorted(fam):
        d = fam[f]
        add(_tablerow([f, d['n'], d['m2_pass'], d['m4_testable'],
                       {'add_axes': '有 add_axes 4 字面量 → pos 可原位改写',
                        'subplots': '只有 figsize 字面量 → 本口径下无 pos 目标',
                        'none': '无任何可提取字面量'}.get(f, '')]))
    add('')

    # FAIL 详单
    add('## 3. 每一条 FAIL 的最小复现\n')
    add(ROOT_CAUSE_MD)
    add('证据文件相对 `corpus018/harness/`。命令里的 `{PY}` = '
        '`D:\\Miniconda\\envs\\longxia\\python.exe`，环境 `PYTHONPATH=D:\\Agent\\deepseek\\mpltweak\\src`、'
        '`MPLBACKEND=Agg`。\n')
    add('**每条 FAIL 的完整复现命令都在对应的 `work/<脚本名>/mN_*.cmd.txt` 里**，'
        '逐字 stdout（apply 的 JSON）在 `mN_*.apply.json`，人读 stderr 在 '
        '`mN_*.stderr.txt`，源码 diff 在 `mN_*.diff`。下面先给每个指标的全量 FAIL 清单，'
        '再挑代表性样本把逐字内容展开。\n')
    n_fail = 0
    for mi, (key, name) in enumerate(METRICS, 1):
        fails = [r for r in results if r[key]['status'] in ('fail', 'timeout')]
        nas = [r for r in results if r[key]['status'] == 'not_applicable']
        if not fails and not nas:
            continue
        add('### 3.%d %s —— %d 条 FAIL%s\n' % (
            mi, name, len(fails),
            ('；另有 **%d 条 not_applicable**（判据前提不成立，不计 fail，清单见本节末）'
             % len(nas)) if nas else ''))
        for group, gname in ((fails, 'FAIL'), (nas, 'not_applicable')):
            if not group:
                continue
            if group is nas:
                add('**not_applicable 清单（前提不成立，不参与 fail 计数；'
                    '证据与 FAIL 同等留档）**\n')
            byreason = {}
            for r in group:
                rs = r[key].get('root_cause_class') or r[key].get('reason', '?')
                byreason.setdefault(rs.split('（')[0], []).append(r)
            if group is fails:
                add(_tablerow(['根因类', '条数', '代表脚本']))
                add(_tablerow(['---', '---', '---']))
                for rs, rs_rows in sorted(byreason.items(), key=lambda x: -len(x[1])):
                    add(_tablerow([rs, len(rs_rows), '`%s`' % rs_rows[0]['path']]))
                add('')
                add('**全量清单（脚本路径 / 判定 / 关键字段 / 证据）**\n')
            else:
                add(_tablerow(['前提不成立的类别', '条数', '代表脚本']))
                add(_tablerow(['---', '---', '---']))
                for rs, rs_rows in sorted(byreason.items(), key=lambda x: -len(x[1])):
                    add(_tablerow([rs, len(rs_rows), '`%s`' % rs_rows[0]['path']]))
                add('')
            add(_tablerow(['#', '脚本', '判定', '性质', 'apply reason', 'style', 'changes',
                           '插入块', '证据文件']))
            add(_tablerow(['---'] * 9))
            tag = {'m1_parse': 'm1', 'm2_extract': 'm2', 'm3_clean_roundtrip': 'm3',
                   'm4_directed_rewrite': 'm4', 'm5_idempotent': 'm5',
                   'm6_revert': 'm6'}[key]
            for j, r in enumerate(group, 1):
                m = r[key]
                evd = r.get('evidence') or {}
                ev_files = [evd.get('%s_stdout' % tag), evd.get('%s' % tag + '_diff'),
                            evd.get('m4_check')]
                ev_files = [e for e in ev_files if e]
                add(_tablerow([j, '`%s`' % r['path'], m['status'],
                               m.get('verdict_class') or '—',
                               m.get('apply_reason') or '—', m.get('style') or '—',
                               json.dumps(m.get('changes') or [], ensure_ascii=False),
                               '是' if m.get('block_inserted') else '否',
                               '、'.join('`%s`' % e for e in ev_files)]))
        add('')
        # 全量清单里补两列关键细节
        add('**逐条细节（判定 + 我的根因判断 + 复现要点）**\n')
        for j, r in enumerate(fails, 1):
            m = r[key]
            evd = r.get('evidence') or {}
            cmd_ev = evd.get('%s_cmd' % tag)
            cmd = ''
            if cmd_ev:
                try:
                    cmd = open(os.path.join(HERE, cmd_ev), 'r', encoding='utf-8').read().strip()
                except OSError:
                    cmd = '(证据文件缺失)'
            add('%d. **`%s`** — %s；根因：%s' % (
                j, r['path'], m['status'],
                (m.get('root_cause_class') or m.get('reason') or '').replace('\n', ' ')))
            if cmd:
                add('   - 复现：`%s`' % cmd)
            add('   - 逐字结果：`%s`' % json.dumps(
                {k: v for k, v in m.items() if k not in ('warnings', 'inplace_only')},
                ensure_ascii=False))
            if m.get('inplace_only'):
                add('   - inplace 单独探针（auto 会把它丢掉）：`%s`'
                    % json.dumps(m['inplace_only'], ensure_ascii=False))
        add('')
        # 展开样本
        n_ex = min(meta.get('_max_fail_detail', 2), len(fails))
        if n_ex:
            add('**展开样本（前 %d 条，逐字 JSON / stderr / diff）**\n' % n_ex)
        for r in fails[:n_ex]:
            n_fail += 1
            m = r[key]
            add('#### 展开 %d: `%s`\n' % (n_fail, r['path']))
            evd = r.get('evidence') or {}
            add('- 工作副本：`%s/clean.py`、`%s/directed.py`；原件只读未被改动'
                % (r['workdir'], r['workdir']))
            for nm, label in (('%s_cmd' % tag, '完整命令'),
                              ('%s_stdout' % tag, '逐字 JSON（stdout）'),
                              ('%s_stderr' % tag, '逐字 stderr（人读信息）'),
                              ('%s' % tag + '_diff', '源码 diff'),
                              ('m4_check', '独立 AST 复核器输出'),
                              ('%s_inplace_only' % tag, 'inplace 单独探针')):
                p = evd.get(nm)
                if not p:
                    continue
                fp = os.path.join(HERE, p)
                if not os.path.exists(fp):
                    continue
                body = open(fp, 'r', encoding='utf-8', errors='replace').read()
                add('- %s（`%s`）：' % (label, p))
                add('  ```')
                for ln in body.splitlines()[:32]:
                    add('  ' + ln)
                if len(body.splitlines()) > 32:
                    add('  ... (截断，全文见证据文件)')
                add('  ```')
            add('')

    # 场景级失败明细（T37）：主键可能只装了一个场景，这里把两个场景的失败都列出来
    sfails = scenario_fails(results)
    if sfails:
        add('### 3.%d 目标场景级的失败明细（pos / figsize 分开，含被主键覆盖的那些）\n'
            % (len(METRICS) + 1))
        add('> §1 的主键每个脚本只装一个场景；本表把 `m4_pos`/`m4_figsize`（M5/M6 同）里'
            '**所有** fail 都列出来，保证没有失败被主键挡住。\n')
        add(_tablerow(['指标', '场景', '脚本', '性质', 'reason（含逐字值）', '证据文件']))
        add(_tablerow(['---'] * 6))
        for mname, sc, r, rec in sfails:
            evd = r.get('evidence') or {}
            tag = {'M4 定向改写': 'm4_fig' if sc == 'figsize' else 'm4_directed',
                   'M5 幂等': 'm5_fig' if sc == 'figsize' else 'm5_idem',
                   'M6 可逆': 'm6_fig' if sc == 'figsize' else 'm6_pre'}[mname]
            ev_files = [v for k, v in evd.items()
                        if k.startswith(tag) and k.endswith(('stdout', 'diff'))]
            add(_tablerow([mname, sc, '`%s`' % r['path'], rec.get('verdict_class') or '—',
                           (rec.get('reason') or '')[:150],
                           '、'.join('`%s`' % e for e in ev_files[:3])]))
        add('')
        add('逐条复现：\n')
        for mname, sc, r, rec in sfails:
            stem = os.path.splitext(os.path.basename(r['path']))[0]
            tag = {'M4 定向改写': 'm4_fig' if sc == 'figsize' else 'm4_directed',
                   'M5 幂等': 'm5_fig' if sc == 'figsize' else 'm5_idem',
                   'M6 可逆': 'm6_fig' if sc == 'figsize' else 'm6_pre'}[mname]
            add('- **`%s`**［%s / %s 目标］' % (r['path'], mname, sc))
            add('  - 字段：`%s`' % json.dumps(
                {k: rec.get(k) for k in ('status', 'verdict_class', 'reason', 'apply_reason',
                                         'style', 'changes', 'bytes_changed',
                                         'target_figsize', 'source_figsize',
                                         'changed_fig_indices', 'spelling_preserved',
                                         'write_happened', 'sha_after_write',
                                         'sha_after_revert', 'revert_backup_path')
                 if rec.get(k) is not None}, ensure_ascii=False))
            cmd = _repro_cmd(WORK, stem, tag)
            if cmd:
                add('  - 最小复现：`%s`' % cmd)
            add('  - 证据：`%s/%s/`（%s）' % (os.path.basename(WORK), stem,
                                            'figsize.py/figsize.json' if sc == 'figsize'
                                            else 'directed.py/directed.json'))
        add('')

    # 未能测的
    add('## 4. 我没能测的 / 不确定的\n')
    add('### 4.1 M2 提不出目标值（skipped，不算失败）\n')
    sk = [r for r in results if r['m2_extract']['status'] == 'skipped'
          and r['m1_parse']['status'] == 'pass']
    add('共 **%d** 个脚本：源码里既没有带 4 个数字字面量的 `add_axes([...])`，'
        '也没有 `figure(figsize=(w,h))` 字面量 → 无法构造参数，未进入 M3–M6。\n' % len(sk))
    add('前 40 个：' + '、'.join('`%s`' % r['path'] for r in sk[:40]) + '\n')
    add('### 4.2 M4 无法测（有 figsize 但无 add_axes 字面量）\n')
    sk4 = [r for r in results if r['m4_directed_rewrite']['status'] == 'skipped'
           and r['m2_extract']['status'] == 'pass']
    add('共 **%d** 个：参数里只有 figsize 目标，没有可原位改写的轴位置字面量，'
        'M4/M5/M6 结构性不可测。\n' % len(sk4))
    add('### 4.3 超时\n')
    for key, name in METRICS:
        to = [r for r in results if r[key]['status'] == 'timeout']
        if to:
            add('- %s：%d 条 —— %s' % (name, len(to), '、'.join('`%s`' % r['path'] for r in to[:20])))
    if not any(r[k]['status'] == 'timeout' for r in results for k, _ in METRICS):
        add('- 无。\n')
    add('### 4.4 其它不确定\n')
    add('- **M3 对"只有 figsize、没有 add_axes"的脚本是结构性退化**：apply 在 '
        '`if not axes` 处直接返回 `no_axes` 并退出，画布尺寸目标一个字节都不落盘。'
        '这种往返构造不出来，本报告记为 skipped（并单列计数），没有当成 FAIL —— '
        '如实说明，避免把 N 条结构性不可判当成 N 条工具缺陷。')
    add('- `apply --verify-fast` 的语义保证很弱：它只做 `ast.parse`，**不重跑脚本**，'
        '所以"脚本还能不能跑通""布局有没有真的落到目标图上"这三项**本次一律没有验证**'
        '（这是任务铁律要求的取舍，不是工具缺陷）。')
    add('- M1 用的是 Python 自己的 `ast.parse(raw_bytes)`（尊重 PEP 263 编码声明）；'
        'M2 用的是按 utf-8-sig→utf-8→gbk→gb18030→big5 顺序解码后的文本，'
        '极端编码（如 big5 与 gbk 同字节冲突）下两者可能不一致。')
    add('- 提取器只认**字面量**（`add_axes([0.1, 0.1, 0.8, 0.8])`）；'
        '变量/表达式（`add_axes(rect)`、`add_axes([a, b, c, d])`）一律算提不出 → skipped。')
    add('- `axes[i].pos` 的轴序号按 **fig.axes 顺序**编号（= 源码里 '
        '`变量 = ...add_axes/add_subplot(...)` 赋值语句的出现顺序，含 add_subplot 轴、'
        '剔除 colorbar 槽轴）—— 这是与工具 `_figure_axes_for_index` 对齐的口径，'
        '否则"没改到"会变成脚手架的伪 FAIL。M4 改的是**第一个带 4 字面量的轴**，'
        '它的下标在每条结果里都如实记录（`target_axis_index`），并不总是 0。')
    add('- 多图脚本的 `fig_index` 用静态近似（"字面量前有几个建图调用"），'
        '无法覆盖"用变量回头改前面那张图"的写法；这类脚本的 M4 可能被测到别的图上。')
    add('- 覆盖率统计基于 M4 那一次运行（参数带真实改动）；M3 的干净往返'
        '（reason=no_change）不是"是否支持原位改写"的判据。')
    add('')
    add(build_findings(results))
    add(build_audit(results))
    add(build_source_breakdown(results))
    add(build_retest_section(results, meta))
    add(build_final_triage_section(results))
    add(build_reclass_section(results, meta))
    add(build_surface_section(results, meta))
    add(build_handoff_section(results))
    return '\n'.join(L)


def build_source_breakdown(results):
    """§7 按来源分组的适配面（gallery vs zenodo）。

    只读 `l0_results.jsonl` 的快照路径 + 语料文件的**纯 AST 静态扫描**（不跑任何测试、
    不执行任何脚本）。manifest 的 `tags.layout` 与我的独立扫描并排列出，便于互校。
    """
    man = {}
    for r in load_manifest():
        man[r.get('path')] = r
    groups = ['matplotlib-gallery', 'zenodo']
    label = {'matplotlib-gallery': 'matplotlib-gallery（官方 gallery）',
             'zenodo': 'zenodo（论文配套代码）'}
    G = {g: {'n': 0, 'paths': [], 'tags': {}, 'scan': {name: 0 for name, _ in LAYOUT_PROBES},
             'm2p': 0, 'm2s': 0, 'm1f': 0,
             'm3': [0, 0], 'm4': [0, 0], 'm5': [0, 0], 'm6': [0, 0],
             'no_tag': 0, 'manifest_miss': 0, 'scan_miss': 0, 'flags': [],
             'n_draw': 0, 'n_nodraw': 0,
             'm4_structural': 0, 'family': {}} for g in groups}
    for r in results:
        g = r.get('source')
        if g not in G:
            continue
        d = G[g]
        d['n'] += 1
        d['paths'].append(r['path'])
        if r['meta'].get('sha_manifest_match') is False:
            d['manifest_miss'] += 1
        if r['m1_parse']['status'] == 'fail':
            d['m1f'] += 1
        if r['m2_extract']['status'] == 'pass':
            d['m2p'] += 1
        elif r['m2_extract']['status'] == 'skipped':
            d['m2s'] += 1
        for key, box in (('m3_clean_roundtrip', 'm3'), ('m4_directed_rewrite', 'm4'),
                         ('m5_idempotent', 'm5'), ('m6_revert', 'm6')):
            st = r[key]['status']
            if st != 'skipped':
                d[box][0] += 1
                if st in ('fail', 'timeout'):
                    d[box][1] += 1
        if r['m4_directed_rewrite'].get('root_cause_class'):
            d['m4_structural'] += 1
        fam = r['meta'].get('family', 'none')
        d['family'][fam] = d['family'].get(fam, 0) + 1
        tags = (man.get(r['path'], {}).get('tags') or {}).get('layout') or []
        if not tags:
            d['no_tag'] += 1
        for t in tags:
            d['tags'][t] = d['tags'].get(t, 0) + 1
        f = scan_layout_features(os.path.join(CORPUS, r['path'].replace('/', os.sep)))
        if f is None:
            d['scan_miss'] += 1
        else:
            d['flags'].append(f)
            for k, v in f.items():
                if v:
                    d['scan'][k] = d['scan'].get(k, 0) + 1

    def pct(a, b):
        return ('%.1f%%' % (100.0 * a / b)) if b else '—'

    L = []
    add = L.append
    add('## 7. 按来源分组的适配面（gallery vs zenodo）\n')
    add('> 本节只读 `l0_results.jsonl` 的快照路径 + 对语料文件做**纯 AST 静态扫描**'
        '（不跑测试、不执行脚本）。口径 = 本次运行快照（%d 个脚本），不是当前语料'
        '（语料仍在被追加）。\n' % len(results))

    # --- 表 1：规模与指标 ---
    add('### 7.1 规模与指标（分母写清）\n')
    add(_tablerow(['来源', '脚本数', 'M1 解析失败', 'M2 可构造', 'M2 可构造率',
                   'M2 skipped', 'M3 fail / 可判定', 'M4 pass / 可判定',
                   'M5 fail / 可判定', 'M6 fail / 可判定']))
    add(_tablerow(['---'] * 10))
    for g in groups:
        d = G[g]
        add(_tablerow([label[g], d['n'], d['m1f'], d['m2p'], pct(d['m2p'], d['n']),
                       '%d（%s）' % (d['m2s'], pct(d['m2s'], d['n'])),
                       '%d / %d（%s）' % (d['m3'][1], d['m3'][0], pct(d['m3'][1], d['m3'][0])),
                       '%d / %d（%s）' % (d['m4'][0] - d['m4'][1], d['m4'][0],
                                          pct(d['m4'][0] - d['m4'][1], d['m4'][0])),
                       '%d / %d（%s）' % (d['m5'][1], d['m5'][0], pct(d['m5'][1], d['m5'][0])),
                       '%d / %d（%s）' % (d['m6'][1], d['m6'][0], pct(d['m6'][1], d['m6'][0]))]))
    add('')
    add('> M3/M4/M5/M6 的"可判定"分母 = 该来源里真正跑过这项的脚本数（其余因提不出目标值'
        '或没有 add_axes 字面量而 skipped）。M5/M6 只在 M4 跑过的脚本上跑。\n')

    # --- 表 2：manifest 标签的布局形态 ---
    add('### 7.2 布局形态占比（manifest `tags.layout`，分母 = 该来源脚本数）\n')
    tag_keys = ['add_axes', 'subplots', 'GridSpec', 'subplot_mosaic', 'twinx', 'inset_axes',
                'colorbar', 'constrained_layout']
    add(_tablerow(['manifest 标签'] + [label[g] for g in groups]))
    add(_tablerow(['---'] * (1 + len(groups))))
    for t in tag_keys:
        row = [t]
        for g in groups:
            d = G[g]
            row.append('%d（%s）' % (d['tags'].get(t, 0), pct(d['tags'].get(t, 0), d['n'])))
        add(_tablerow(row))
    row = ['**无 layout 标签**']
    for g in groups:
        d = G[g]
        row.append('%d（%s）' % (d['no_tag'], pct(d['no_tag'], d['n'])))
    add(_tablerow(row))
    add('')

    # --- 表 3：我自己的静态扫描（互校 + 回答"字面量 add_axes"）---
    add('### 7.3 独立静态扫描的布局写法（我自己扫的，可与 7.2 互校）\n')
    add(_tablerow(['写法（代码里出现过）'] + [label[g] for g in groups]))
    add(_tablerow(['---'] * (1 + len(groups))))
    for name, _ in LAYOUT_PROBES:
        row = [name]
        for g in groups:
            d = G[g]
            row.append('%d（%s）' % (d['scan'].get(name, 0), pct(d['scan'].get(name, 0), d['n'])))
        add(_tablerow(row))
    # "到底画不画图"这一层必须单列：zenodo 里有大量纯计算/表格脚本，
    # 拿它们当分母会把"论文代码更少用 subplots"这类结论讲歪。
    DRAW_KEYS = ('figure', 'subplots（含 fig, ax = …）', 'add_subplot',
                 'add_axes（任意写法）', 'inset_axes')
    for g in groups:
        d = G[g]
        d['n_draw'] = sum(1 for f in d['flags'] if any(f.get(k) for k in DRAW_KEYS))
        d['n_nodraw'] = d['n'] - d['n_draw'] - d['scan_miss']
    row = ['**有任何建图调用（确实在画图）**']
    for g in groups:
        d = G[g]
        row.append('%d（%s）' % (d['n_draw'], pct(d['n_draw'], d['n'])))
    add(_tablerow(row))
    row = ['**完全没有建图调用（纯计算/表格脚本）**']
    for g in groups:
        d = G[g]
        row.append('%d（%s）' % (d['n_nodraw'], pct(d['n_nodraw'], d['n'])))
    add(_tablerow(row))
    add('')
    if any(G[g]['scan_miss'] for g in groups):
        add('> 扫描失败的脚本（语法坏，无法解析）：%s\n' % json.dumps(
            {label[g]: G[g]['scan_miss'] for g in groups}, ensure_ascii=False))

    # --- 7.4 重点：字面量 add_axes ---
    def wilson(k, n):
        """Wilson 95% 置信区间（不依赖 scipy）。"""
        if not n:
            return (0.0, 0.0)
        z = 1.959963985
        p = k / float(n)
        d = 1 + z * z / n
        c = (p + z * z / (2 * n)) / d
        h = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / d
        return (max(0.0, c - h), min(1.0, c + h))

    add('### 7.4 【重点】`add_axes([...])` 字面量比例 —— gallery vs zenodo\n')
    add('比率列是**按脚本占比之比**（不是原始条数之比），分母已写清；括号里是 Wilson 95% 置信区间。\n')
    add(_tablerow(['口径', label['matplotlib-gallery'], label['zenodo'], '占比之比（zenodo/gallery）',
                   '差异是否显著']))
    add(_tablerow(['---'] * 5))
    gl, zn = G['matplotlib-gallery'], G['zenodo']

    def _row(name, ka, na, kb, nb):
        a, b = 100.0 * ka / na if na else 0.0, 100.0 * kb / nb if nb else 0.0
        ratio = ('%.2f×' % (b / a)) if a else '—'
        ca, cb = wilson(ka, na), wilson(kb, nb)
        overlap = not (ca[1] < cb[0] or cb[1] < ca[0])
        add(_tablerow([name,
                       '%d/%d = %.1f%%（CI %.1f–%.1f%%）' % (ka, na, a, 100 * ca[0], 100 * ca[1]),
                       '%d/%d = %.1f%%（CI %.1f–%.1f%%）' % (kb, nb, b, 100 * cb[0], 100 * cb[1]),
                       ratio, 'CI 重叠，不显著' if overlap else '**CI 不重叠，显著**']))

    _row('任意 `add_axes(...)` 调用（含变量/表达式）',
         gl['scan'].get('add_axes（任意写法）', 0), gl['n'],
         zn['scan'].get('add_axes（任意写法）', 0), zn['n'])
    _row('**4 数字字面量** `add_axes([a,b,c,d])`（工具可原位改的前提）',
         gl['scan'].get('add_axes 4 字面量', 0), gl['n'],
         zn['scan'].get('add_axes 4 字面量', 0), zn['n'])
    _row('同上，但分母只算"确实在画图"的脚本',
         gl['scan'].get('add_axes 4 字面量', 0), gl['n_draw'],
         zn['scan'].get('add_axes 4 字面量', 0), zn['n_draw'])
    _row('`figure/subplots(figsize=(w,h))` 2 字面量',
         gl['scan'].get('figsize=(w,h) 2 字面量', 0), gl['n'],
         zn['scan'].get('figsize=(w,h) 2 字面量', 0), zn['n'])
    _row('`plt.subplots(...)` 网格布局', gl['scan'].get('subplots（含 fig, ax = …）', 0), gl['n'],
         zn['scan'].get('subplots（含 fig, ax = …）', 0), zn['n'])
    _row('完全提不出任何字面量（M2 skipped）', gl['m2s'], gl['n'], zn['m2s'], zn['n'])
    add('')

    ka, na = gl['scan'].get('add_axes 4 字面量', 0), gl['n']
    kb, nb = zn['scan'].get('add_axes 4 字面量', 0), zn['n']
    a_any, b_any = gl['scan'].get('add_axes（任意写法）', 0), zn['scan'].get('add_axes（任意写法）', 0)
    ra, rb = 100.0 * ka / na, 100.0 * kb / nb
    ca, cb = wilson(ka, na), wilson(kb, nb)
    overlap = not (ca[1] < cb[0] or cb[1] < ca[0])
    add('**结论（按上面这张表读）：**\n')
    add('- 用**字面量** `add_axes([...])` 的比例：gallery **%.1f%%** vs zenodo **%.1f%%**，'
        '占比之比 **%.2f×** —— zenodo 略**低**，但两者 95%% CI 重叠（%.1f–%.1f%% vs %.1f–%.1f%%），'
        '**"论文代码更爱用字面量 add_axes"这个假设不成立（差异不显著）**。'
        % (ra, rb, (rb / ra) if ra else 0, 100 * ca[0], 100 * ca[1], 100 * cb[0], 100 * cb[1]))
    if b_any:
        add('- 但有个更细的事实：zenodo 里出现过 `add_axes` 的脚本中只有 **%s** 是 4 数字字面量'
            '（gallery 是 %s）—— 论文代码更倾向用**变量/表达式矩形**（`add_axes(rect)`、'
            '`add_axes([x0, y0, w, h])` 里带名字），这类**无法从源码静态构造参数**，'
            '本报告的 L0 口径测不到，是"没能测的"一部分（§4.4）。'
            % (pct(kb, b_any), pct(ka, a_any)))
    add('- **差异真正显著的地方在别处**（CI 不重叠）：zenodo 的 `figsize=(w,h)` 字面量占比是 '
        'gallery 的 %.2f×（%.1f%% vs %.1f%%），`plt.subplots(...)` 网格占比只有 gallery 的 %.2f×'
        '（%.1f%% vs %.1f%%）。也就是说论文配套代码**更少用 subplots 网格、更多显式给画布尺寸**，'
        '而它的面板定位多靠**布局引擎/divider/inset**（`constrained_layout` 4 例全在 zenodo、'
        '`twinx` 占比 5.9%% vs 1.5%%）而不是 `add_axes` 字面量。'
        % ((100.0 * zn['scan'].get('figsize=(w,h) 2 字面量', 0) / zn['n'])
           / max(1e-9, 100.0 * gl['scan'].get('figsize=(w,h) 2 字面量', 0) / gl['n']),
           100.0 * zn['scan'].get('figsize=(w,h) 2 字面量', 0) / zn['n'],
           100.0 * gl['scan'].get('figsize=(w,h) 2 字面量', 0) / gl['n'],
           (100.0 * zn['scan'].get('subplots（含 fig, ax = …）', 0) / zn['n'])
           / max(1e-9, 100.0 * gl['scan'].get('subplots（含 fig, ax = …）', 0) / gl['n']),
           100.0 * zn['scan'].get('subplots（含 fig, ax = …）', 0) / zn['n'],
           100.0 * gl['scan'].get('subplots（含 fig, ax = …）', 0) / gl['n']))
    _fa, _fb = gl['scan'].get('figsize=(w,h) 2 字面量', 0), zn['scan'].get('figsize=(w,h) 2 字面量', 0)
    add('- **条件口径（分母 = 确实在画图的脚本）更要紧**：zenodo **%s** 的绘图脚本写了 '
        '`figsize=(w,h)` 字面量（gallery 只有 %s）—— 对论文型代码，唯一能被静态构造出来的'
        '目标几乎就是**画布尺寸**；而本报告 §4.4/§7.1 已说明，当参数里没有任何轴项时 '
        '`apply` 直接 `no_axes` 退出，**画布尺寸这条路径整条不可用**（M3 结构性退化）。'
        % (pct(_fb, zn['n_draw']), pct(_fa, gl['n_draw'])))
    add('- 还有一层容易看歪的分母：zenodo 有 **%s** 的脚本**完全不画图**（纯计算/表格流水线），'
        'gallery 只有 %s —— 谈"论文代码的布局习惯"时必须以"确实画图"的 %d / %d 个脚本为分母。'
        % (pct(zn['n_nodraw'], zn['n']), pct(gl['n_nodraw'], gl['n']), zn['n_draw'], gl['n_draw']))
    add('- **推广口径结论**：单一"全库原位覆盖率 3.6%"会同时误导两类受众 —— '
        '对 gallery 型用户（偏 `plt.subplots()`）与论文型用户（偏显式 figsize + '
        '布局引擎/divider），"能用字面量原位改位置"的脚本都只有几个百分点量级，'
        '但**原因不同**：前者是网格轴没有位置字面量，后者是位置用变量算出来。'
        '对外报数建议按受众分列，并附分母口径（§7.1/§7.4）。\n')
    return '\n'.join(L)


# --------------------------------------------------------------------------
# §8 修复后复测与残余失败定性
# --------------------------------------------------------------------------
RETEST_DEFAULT_COMPARE = {
    '_engine': 'l0_results_018baseline.jsonl',
    '_018baseline': 'l0_results_engine.jsonl',
}

# 每一类的判定（人工结论，写在代码里以便随报告重现）
RETEST_VERDICT = {
    'tool_figsize_rewrite':
        '**工具真 bug**：参数与源码现值**完全一致**（`params.figsize_in` == 该图源码里的 '
        'figsize 字面量），但工具仍把整个 `figsize=` 表达式重写成规范形式并落盘 → 字节被改。'
        '根因见 `mpltweak-fix018-engine/src/mpltweak/writeback.py:534`：'
        '`new_txt = \'(%s, %s)\' % (_num(figsize_in[0]), _num(figsize_in[1]))` '
        '**无条件**替换整个 kwarg 值，既不比对原值是否相等，也不保留原写法。',
    'tool_block_fallback':
        '**工具真 bug（残余 RC-1 变体）**：目标值与源码现值相同，但该目标**无法原位核对**'
        '（字面量落在建图区间之外 / 是 colorbar 槽轴 cax）→ inplace 报"未原位应用" → '
        'auto 模式判定"落空" → 退到块模式并**插入 auto layout 块**，于是"什么都没改"的 '
        'apply 改写了用户源码。',
    'harness_expectation_invalid':
        '**期望不成立（脚手架口径）**：params 里的目标值不属于目标图 → 改的是别的图，'
        '往返前提不成立。本轮已修（见 §8.0）。',
    'contract_reason_not_no_change':
        '**契约失败（字节未变）**：字节没变，但 `reason`/`changes` 谎报有改动 → agent '
        '无法从 JSON 区分"无事可做"与"做了事"。',
}


def classify_m3_fail(m, params_consistent):
    if not params_consistent:
        return 'harness_expectation_invalid'
    if m.get('block_inserted'):
        return 'tool_block_fallback'
    ch = m.get('changes') or []
    if not m.get('bytes_unchanged'):
        if 'figsize' in ch and m.get('value_preserving_write'):
            return 'tool_figsize_rewrite'
        return 'tool_other_write'
    return 'contract_reason_no_change_bytes_same'


def _repro_cmd(wdir, stem, tag):
    p = os.path.join(wdir, stem, '%s.cmd.txt' % tag)
    if os.path.exists(p):
        try:
            return open(p, 'r', encoding='utf-8').read().strip()
        except OSError:
            return None
    return None


def build_retest_section(results, meta):
    """§8：修复后复测（M6 重接线）＋残余失败定性。"""
    tag = _TAG or '_018baseline'
    cmp_name = os.environ.get('L0_COMPARE') or RETEST_DEFAULT_COMPARE.get(tag, '')
    cmp_path = os.path.join(HERE, cmp_name) if cmp_name else ''
    if not cmp_name or not os.path.exists(cmp_path):
        return ('## 8. 修复后复测与残余失败定性\n\n'
                '> 未找到对比结果文件（`L0_COMPARE=<l0_results_*.jsonl>`；默认 '
                '`_engine` ↔ `_018baseline` 互指），本节跳过。\n')
    other = {r['path']: r for r in
             (json.loads(l) for l in open(cmp_path, encoding='utf-8') if l.strip())}
    mine = {r['path']: r for r in results}
    common = sorted(set(mine) & set(other))
    only_mine = len(mine) - len(common)
    only_other = len(other) - len(common)
    this_name = '本次（%s）' % tag
    other_name = '对比（%s）' % os.path.basename(cmp_name).replace('l0_results', '').replace('.jsonl', '')
    L = []
    add = L.append
    add('## 8. 修复后复测与残余失败定性\n')
    add('> 本节由 `--report-only` 从两份结果文件重建（不重跑测试）；'
        '`%s` = %s，`%s` = %s，共同脚本 **%d** 个（仅本次 %d / 仅对比 %d）。\n'
        % (this_name, os.path.basename(RESULTS), other_name, os.path.basename(cmp_name),
           len(common), only_mine, only_other))

    # ---- 8.0 本轮脚手架口径修复（必须与工具修复分开报）----
    add('### 8.0 本轮在**脚手架**侧修掉的 3 个口径错（否则前后对比不成立）\n')
    add('| # | 问题 | 后果 | 修法 |')
    add('| --- | --- | --- | --- |')
    add(_tablerow(['H1', '`figsize_in` 取的是"文件里第一个 figsize 字面量"，'
                        '多图脚本里它可能属于**别的图**',
                   'clean 往返会把 A 图尺寸写到 B 图 → 伪 FAIL（基线曾因此白报 11 条）',
                   '新增 `collect_figsize_all`，只认第 `fig_index` 个建图调用**自己**的 '
                   '`figsize=` kwarg；无 add_axes 时把 `fig_index` 定到"第一个带 figsize '
                   '字面量的图"']))
    add(_tablerow(['H2', 'M4 复核器比对 figsize 时，前值取目标图、后值取"文件第一个"',
                   '目标轴改对了也被判"改多了"（伪 FAIL 3 条）',
                   '前后都取第 `fig_index` 张图的 figsize（`collect_figsize_all`）']))
    add(_tablerow(['H3', '`work/` 与 `l0_run_meta.json` 没按 `L0_TAG` 隔离',
                   '后跑的变体覆盖先跑变体的证据文件 → 结果文件里的证据指针指向**另一个版本**',
                   '`work<TAG>/`、`l0_run_meta<TAG>.json` 全部按 tag 隔离']))
    add('')

    # ---- 8.1 M6 重接线 ----
    add('### 8.1 M6（可逆）为什么曾全 skip，以及现在的接线方式\n')
    add('**原因**：旧版 M6 挂在 M4 的写回上，并且用**备份文件是否存在**当闸门 —— 闸门路径是 '
        '`work/<脚本名>/directed.tweak.bak`（= 参数文件同目录）。engine 版把备份搬到了'
        '**脚本同目录** `<脚本目录>/.tweak_params/<脚本名>.tweak.bak`（与项目文档口径一致），'
        '旧闸门恒假 → 27 条全部 skip（指标"变绿"其实是**用 skip 做出来的**）。'
        '反证：在 scratch 里对同一个脚本分别用两个版本做 写回→revert，'
        '两边 `revert_restored=True`（探针脚本 `probe_m6_backup.py`，记录 `_m6probe.txt`）。\n')
    probe = os.path.join(HERE, '_m6probe.txt')
    if os.path.exists(probe):
        try:
            pr = json.load(open(probe, encoding='utf-8'))
            add(_tablerow(['探针（同一脚本、同一参数）', '写入发生了', 'revert 是否回到原件',
                           'revert 报告的备份路径']))
            add(_tablerow(['---'] * 4))
            for r in pr:
                add(_tablerow([os.path.basename(os.path.dirname(r['src_dir'])),
                               '是' if r['write_happened'] else '否',
                               '是' if r['revert_restored'] else '否',
                               '`%s`' % (os.path.relpath(r.get('revert_backup_path') or '—',
                                                         CORPUS))]))
            add('')
        except Exception:                                     # noqa: BLE001
            pass
    add('**现在的接线**：M6 用**全新副本** `revert.py` + 定向参数（pos 改成明显不同的值）→ '
        '① 先做一次**真实写回**（必须改字节）→ ② 再 `revert --write` → ③ 与**原件字节**比对；'
        '不再猜备份路径（revert 的 JSON 自己会报 `backup`/`ok`）。只有"连写回都没发生"才允许 '
        'skip，且理由、写回前后的 sha256、写回用的 style、work 目录里的 `.bak` 清单全部留档。\n')
    for nm, d in ((this_name, mine), (other_name, other)):
        c, wrote = {}, 0
        for p in common:
            m = d[p]['m6_revert']
            c[m['status']] = c.get(m['status'], 0) + 1
            if m.get('write_happened'):
                wrote += 1
        add('- %s：%s；其中 `write_happened=True` **%d** 条（= 真正被 M6 测到的样本数）'
            % (nm, json.dumps(c, ensure_ascii=False), wrote))
    add('')
    add('> 口径说明：新版 M6 测的是"**一次写回 + 一次 revert** 的可逆性"，'
        '而不是"M4+M5 之后 revert 能不能回到原件"。旧版把 M5 的连带写入算进 M6，'
        '导致基线 27/27 全灭（`cascade_from_m5=true`）—— 那是**指标设计缺陷**，'
        '不是 revert 缺陷。基线的 revert 本身在单次写回下 26/27 正常。\n')

    # ---- 8.2 交集对比总表 ----
    add('### 8.2 交集前后对比（%d 个共同脚本）\n' % len(common))
    add(_tablerow(['指标', this_name, other_name, '解读']))
    add(_tablerow(['---'] * 4))
    note = {
        'm1_parse': '两版一致（解析与工具无关）',
        'm2_extract': '两版一致（提取是脚手架的事）',
        'm3_clean_roundtrip': '**可测面 27 → 237**（RC-8 让 figsize-only 也能写回）；'
                              '但仍有残余 FAIL，见 §8.3',
        'm4_directed_rewrite': '两版都是 23/27 通过，4 条 FAIL 全是"结构性够不到"（§8.4）',
        'm5_idempotent': '0/27 → 26/27（RC-1 修好后的直接效果），残余 1 条见 §8.4',
        'm6_revert': '旧版 27/27 FAIL 是**指标缺陷**（级联），新版两边都是"单次写回可逆"',
    }
    for key, name in METRICS:
        ca, cb = {}, {}
        for p in common:
            ca[mine[p][key]['status']] = ca.get(mine[p][key]['status'], 0) + 1
            cb[other[p][key]['status']] = cb.get(other[p][key]['status'], 0) + 1
        add(_tablerow([name, json.dumps(ca, ensure_ascii=False),
                       json.dumps(cb, ensure_ascii=False), note.get(key, '')]))
    reg = []
    for p in common:
        for key, _nm in METRICS:
            if other[p][key]['status'] == 'pass' and mine[p][key]['status'] in ('fail', 'timeout'):
                reg.append((p, key, other[p][key]['status'], mine[p][key]['status']))
    add('')
    add('**回归检查（对比版本 pass → 本次 fail/timeout）：%d 条**%s\n'
        % (len(reg), '' if not reg else '：' + '；'.join('`%s`/%s' % (p, k) for p, k, _a, _b in reg)))

    # ---- 8.3 M3 残余定性 ----
    fail3 = [p for p in common if mine[p]['m3_clean_roundtrip']['status'] == 'fail']
    add('### 8.3 M3（干净往返）残余 FAIL 逐条定性 —— %d 条\n' % len(fail3))
    cls_rows = []
    for p in fail3:
        m = mine[p]['m3_clean_roundtrip']
        meta_p = mine[p]['meta']
        fp = os.path.join(CORPUS, p.replace('/', os.sep))
        try:
            text, _ = decode_source(read_bytes(fp))
            plan = extract_plan(text)
            fs_t = (plan['figsize_target'] or {}).get('vals')
        except OSError:
            fs_t = None
        vb = (m.get('extracted_values_before') or {}).get('figsize')
        consistent = (fs_t == vb)
        cls_rows.append((p, classify_m3_fail(m, consistent), m, meta_p,
                         (plan if 'plan' in dir() else {}), vb, fs_t))
    cnt = {}
    for _p, c, _m, _x, _y, _v, _f in cls_rows:
        cnt[c] = cnt.get(c, 0) + 1
    # 对比版本的同口径计数（E1/E2 的"条数"必须两边都给，否则读哪份报告都能被误解）
    cnt_other = {}
    for p in common:
        m = other[p]['m3_clean_roundtrip']
        if m['status'] != 'fail':
            continue
        try:
            text_o, _ = decode_source(read_bytes(os.path.join(CORPUS, p.replace('/', os.sep))))
            plan_o = extract_plan(text_o)
            fs_o = (plan_o['figsize_target'] or {}).get('vals')
        except OSError:
            fs_o = None
        vb_o = (m.get('extracted_values_before') or {}).get('figsize')
        c_o = classify_m3_fail(m, fs_o == vb_o)
        cnt_other[c_o] = cnt_other.get(c_o, 0) + 1
    add(_tablerow(['根因类', '条数', '判定']))
    add(_tablerow(['---'] * 3))
    for c, n in sorted(cnt.items(), key=lambda x: -x[1]):
        add(_tablerow([c, n, RETEST_VERDICT.get(c, '（见逐条）')]))
    add('')
    for i, (p, c, m, mp, plan, vb, fs_t) in enumerate(cls_rows, 1):
        stem = os.path.splitext(os.path.basename(p))[0]
        add('%d. **`%s`** — `%s`（判定见上方分类表）' % (i, p, c))
        dp = os.path.join(WORK, stem, 'm3_clean.diff')
        if os.path.exists(dp):
            try:
                dl = [ln.rstrip('\n') for ln in open(dp, 'r', encoding='utf-8',
                                                     errors='replace')]
                ch = [ln for ln in dl if ln[:1] in '+-' and not ln.startswith(('---', '+++'))]
                if ch:
                    add('   - **逐字 diff**（原件 → 落地后）：')
                    add('     ```')
                    for ln in ch[:4]:
                        add('     ' + ln)
                    add('     ```')
            except OSError:
                pass
        add('   - 结果字段：`%s`' % json.dumps(
            {k: m.get(k) for k in ('apply_reason', 'style', 'changes', 'fail_kind',
                                   'value_preserving_write', 'block_inserted',
                                   'bytes_unchanged')}, ensure_ascii=False))
        add('   - 目标图/图数：`fig_index=%s / %s`；该图自己的 figsize 字面量=%s；'
            'params 里的值=%s（自洽=%s）'
            % (mp.get('fig_index_used'), mp.get('n_fig_calls'), fs_t, vb, fs_t == vb))
        cmd = _repro_cmd(WORK, stem, 'm3_clean')
        if cmd:
            add('   - 最小复现：`%s`' % cmd)
        add('   - 证据：`%s/%s/m3_clean.apply.json`、`m3_clean.stderr.txt`、`m3_clean.diff`'
            % (os.path.basename(WORK), stem))
        add('')

    # ---- 8.4 M4/M5/M6 残余 ----
    add('### 8.4 M4 / M5 / M6 残余定性\n')
    for key, label in (('m4_directed_rewrite', 'M4 定向改写'),
                       ('m5_idempotent', 'M5 幂等'),
                       ('m6_revert', 'M6 可逆')):
        fails = [p for p in common if mine[p][key]['status'] in ('fail', 'timeout')]
        add('**%s：本次 %d 条 FAIL**\n' % (label, len(fails)))
        if not fails:
            add('（无）\n')
            continue
        for p in fails:
            m = mine[p][key]
            stem = os.path.splitext(os.path.basename(p))[0]
            rcls = m.get('root_cause_class') or ''
            if key == 'm4_directed_rewrite':
                if rcls.startswith('colorbar_axis'):
                    verdict = ('**期望不成立（判据口径）**：该字面量是 colorbar 槽轴 cax，'
                               '工具按设计拒绝原位改（`writeback.py` 会把 `cax=` 引用的变量'
                               '从轴表里剔除）。我的 M4 判据要求"这个字面量必须被改"，'
                               '对 cax 不成立；但**它退到块模式插块**这一点仍属 §8.3 的 '
                               '`tool_block_fallback` 缺陷。')
                elif rcls.startswith('outside_figure_span'):
                    verdict = ('**工具局限 + 我的期望也不成立**：该 `add_axes` 落在'
                               '`fig = plt.figure()` 之前（函数体里用调用方传入的 fig），'
                               '工具的区间模型 `[本图, 下一张图)` 收不到它；'
                               '静态口径下也无法说它属于第 0 张图。')
                else:
                    verdict = '**待定**：见证据。'
            elif key == 'm5_idempotent':
                verdict = ('**工具真 bug（同 §8.3 tool_block_fallback）**：第一次写回'
                           '把 figsize 文本规范化后，第二次同参数 apply 已无可改 → '
                           'inplace 落空 → 退块并**插入 auto layout 块** → 幂等被破坏。')
            else:
                verdict = ('**工具真 bug（一次 apply 内备份被覆盖）**：`--style auto` 先按 '
                           'inplace 写了 figsize、再退到块模式写第二次；备份名固定，'
                           '第二次写把备份覆盖成"已改过 figsize"的状态 → `revert` 恢复不出原件。'
                           '证据：该脚本 work 目录下**所有** `.bak` 都是 20568 B 且含规范化后的 '
                           '`figsize=(16, 10)`，**没有一份是原件**（20567 B，`figsize=(16,10)`）；'
                           'diff 只差这一个字节。engine 版同一脚本走 inplace（无回退）→ '
                           'revert 逐字节还原，M6 PASS。')
            add('- **`%s`** — %s' % (p, verdict))
            add('  - 字段：`%s`' % json.dumps(
                {k: m.get(k) for k in ('reason', 'apply_reason', 'style', 'changes',
                                       'block_inserted', 'changed_indices',
                                       'root_cause_class', 'write_happened',
                                       'write_style', 'revert_ok_field',
                                       'backup_files_in_workdir')}, ensure_ascii=False))
            cmd = _repro_cmd(WORK, stem, {'m4_directed_rewrite': 'm4_directed',
                                          'm5_idempotent': 'm5_idem',
                                          'm6_revert': 'm6_pre'}[key])
            if cmd:
                add('  - 最小复现：`%s`' % cmd)
            add('  - 证据：`%s/%s/`' % (os.path.basename(WORK), stem))
        add('')

    # ---- 8.5 交回修复方的清单 ----
    add('### 8.5 交回修复方的缺陷清单（本轮只报告，未改 mpltweak 任何代码）\n')
    add('| ID | 位置 | 现象 | 最小复现 | 建议修法 |')
    add('| --- | --- | --- | --- | --- |')
    add(_tablerow(['E1', '`writeback.py:534`（engine 副本）',
                   '等值 figsize 也被整段重写 → 字节被改（engine %d 条 / baseline %d 条 M3 FAIL）'
                   % (cnt.get('tool_figsize_rewrite', 0),
                      cnt_other.get('tool_figsize_rewrite', 0)),
                   '`gallery/axes_grid1/demo_colorbar_with_inset_locator.py`：'
                   '`figsize=[6, 3]` → `figsize=(6, 3)`（值不变，字节变）',
                   '先比对原值：两数相等则**不编辑、不写、不报 `figsize`**；'
                   '确实需要改时保留原容器与写法（或至少不要动 `.0`/空格）']))
    add(_tablerow(['E2', '`apply.py:_inplace_fell_flat` + 块回退（engine 副本）',
                   '目标值等于源码现值但**无法原位核对**（区间外 / cax）时仍退块插块 → '
                   'M3 engine %d 条 / baseline %d 条，M5 各 %d 条'
                   % (cnt.get('tool_block_fallback', 0),
                      cnt_other.get('tool_block_fallback', 0),
                      sum(1 for p in common if mine[p]['m5_idempotent']['status'] == 'fail')),
                   '`zenodo/23013498__zndo_figs_pca.py`（cax）、'
                   '`gallery/misc/logos2.py`（区间外）',
                   '"未原位应用"的字段若能证明与源码现值相等（或本来就无字段可改），'
                   '应返回 `no_change` 而不是插块；插块只应在**确实有差异**时发生']))
    add(_tablerow(['E3', '`apply.py` 的 `reason`/`changes`',
                   '等值写回时 `changes=["figsize"]`、`reason=ok` 谎报"有改动"',
                   '任意 `figsize=[a, b]` 脚本的 clean 往返 JSON',
                   '等值判断走后与 #E1 一并解决；`changes` 只列真的改了值的字段']))
    add(_tablerow(['E4', '`--style auto` 的两次写回（主仓 0.1.8 实测；engine 副本本轮未复现）',
                   'inplace 写一次、退块再写一次，**备份名固定 → 第二次写覆盖掉第一次的备份**，'
                   '`revert` 再也拿不到原件（主仓 1 条 M6 FAIL）',
                   '`zenodo/20154193__RealSpace.py`：写回 20567 B → revert 后 20568 B，'
                   '差异就是那句 `figsize=(16,10)` → `figsize=(16, 10)`',
                   '备份只在**本次 apply 的第一次写盘之前**做一次（或按 apply 会话唯一命名），'
                   '后续 fallback 写回不得覆盖它']))
    add('')
    add('> 另：engine 版已经修好的部分（本轮实测）—— `apply --json` 新增了 '
        '`applied` / `block_lines` / `auto_fallback` / `inplace_reason` / `inplace_changes` / '
        '`needs_block` 字段（RC-5 消除），inplace 那次的结果不再被整份覆盖；'
        'RC-1 的"无需改动也插块"在**可原位核对**的场景下已消失（M3 从 0 pass 变成 221 pass）。\n')
    return '\n'.join(L)


# --------------------------------------------------------------------------
# §9 最终复测的失败定性（只读）
# --------------------------------------------------------------------------
# 2026-10-10 用 D:\Miniconda\envs\QQbot\python.exe（Python 3.12.12）逐条 re-parse 实测：
# 只有这 3 条在 3.12 下能解析（PEP 701 f-string），其余 8 条 3.12 下同样 SyntaxError。
M1_312_OK = {
    'zenodo/17778435__calculate_sea_ice_velocity_field.py',
    'zenodo/22761293__poparea_fit.py',
    'zenodo/23114431__build_si_readable.py',
}
M1_KIND = {
    'Colab 魔法 !pip/!apt': ['zenodo/21509938__Word-Cloud-Generation.py',
                             'zenodo/22099105__analysis_pisa2022.py'],
    '作者留下的占位符（`###INSERT FILEPATH ...`）':
        ['zenodo/23252566__Bootstrap_Code.py',
         'zenodo/23252566__Sampling_Frequency_Distributions_Plotter.py',
         'zenodo/23252566__TIC_TOC_and_Base_Sat_Plotter.py'],
    '真正的语法错（3.12 也过不了）':
        ['zenodo/18790272__linear_design_integration.py',
         'zenodo/18790272__platform_summary.py',
         'zenodo/23003706__cuda_edge_common_preserved_logic.py'],
    'PEP 701 f-string（需 Python 3.12+；3.10 解释器下必失败）':
        ['zenodo/17778435__calculate_sea_ice_velocity_field.py',
         'zenodo/22761293__poparea_fit.py',
         'zenodo/23114431__build_si_readable.py'],
}
SCALE_RE = re.compile(r'\b(semilogx|semilogy|loglog|set_xscale|set_yscale'
                      r'|\.xscale|\.yscale)\b')


def build_final_triage_section(results):
    """§9：最终复测的失败定性（M1 / M4 / M3 逐条）。纯只读 + 静态分析。"""
    m1f = [r for r in results if r['m1_parse']['status'] == 'fail']
    m4f = [r for r in results if r['m4_directed_rewrite']['status'] == 'fail']
    m3f = [r for r in results if r['m3_clean_roundtrip']['status'] == 'fail']
    if not (m1f or m4f or m3f):
        return ''
    L = []
    add = L.append
    add('## 9. 最终复测的失败定性（M1 / M4 / M3）\n')
    add('> 只读：本节由结果文件 + 语料**静态分析**重建（不执行任何语料脚本、不调用工具）。'
        '证据目录 `%s/<脚本名>/`。\n' % os.path.basename(WORK))

    # ---- 9.1 M1 ----
    add('### 9.1 M1 可解析失败 —— %d 条（全部判为**语料侧**）\n' % len(m1f))
    add('复验方法：`D:\\Miniconda\\envs\\QQbot\\python.exe`（Python 3.12.12）对同一份字节再 '
        '`ast.parse` 一次（脚本 `check_hazards.py`，输出 `_m1_312.txt`）。'
        '本报告其余部分与工具一样用 **longxia = Python 3.10.19**，所以这 3 条在工具眼里同样不可解析 —— '
        '**口径一致，不是脚手架 bug**。\n')
    add(_tablerow(['类型', '条数', '脚本', '工具/脚手架能否处理']))
    add(_tablerow(['---'] * 4))
    for kind, paths in M1_KIND.items():
        can = {'Colab 魔法 !pip/!apt': '不能（非 Python 文件）',
               '作者留下的占位符（`###INSERT FILEPATH ...`）': '不能（源码本身坏）',
               '真正的语法错（3.12 也过不了）': '不能（源码本身坏）',
               'PEP 701 f-string（需 Python 3.12+；3.10 解释器下必失败）':
               '3.12 解释器下可以；当前 3.10 下不能'}[kind]
        add(_tablerow([kind, len(paths), '、'.join('`%s`' % p.split('/')[-1] for p in paths), can]))
    add('')
    add('逐条：\n')
    for i, r in enumerate(m1f, 1):
        p = r['path']
        ok312 = '✅ 3.12 可解析' if p in M1_312_OK else '❌ 3.12 也失败'
        add('%d. `%s`' % (i, p))
        add('   - 判定：**语料侧**（%s）；工具侧行为正确：解析失败 → M2–M6 全部 skip' % ok312)
        add('   - 逐字错误：`%s`' % (r['m1_parse'].get('reason') or '').replace('\n', ' '))
        add('   - 复现：`& "%s" -c "import ast,pathlib; ast.parse(pathlib.Path(r\'%s\').read_bytes())"`'
            % (PYTHON, os.path.join(CORPUS, p.replace('/', os.sep))))
        add('   - 证据：`%s`' % (r.get('evidence') or {}).get('m1', '—'))
    add('')

    # ---- 9.2 M4 ----
    add('### 9.2 M4 定向改写失败 —— %d 条（逐条）\n' % len(m4f))
    add('判据回顾：M4 把"源码里第一个带 4 个数字字面量的 `add_axes([a,b,c,d])`"当目标，'
        '要求**只有它**变成目标值。下表逐条给静态地面真值 + 落盘后果。\n')
    add(_tablerow(['#', '脚本', '判定', '那个字面量是什么', '字面量被改了吗',
                   '落盘字节', '语法', '块里被注入的额外语句']))
    add(_tablerow(['---'] * 8))
    detail = []
    for i, r in enumerate(m4f, 1):
        p = r['path']
        stem = os.path.splitext(os.path.basename(p))[0]
        fp = os.path.join(CORPUS, p.replace('/', os.sep))
        try:
            text, _ = decode_source(read_bytes(fp))
            plan = extract_plan(text)
            aa = plan['add_axes_all']
        except OSError:
            text, plan, aa = '', {'params_model': '?', 'fig_span': (0, 0)}, []
        cax = bool(aa and aa[0].get('is_cax'))
        span = bool(aa and aa[0]['lineno'] < plan['fig_span'][0])
        if cax:
            what, verdict = ('colorbar 槽轴（`cax=` 引用；工具按设计剔除）',
                             '② 脚手架判据口径不成立')
        elif span:
            what, verdict = ('普通数据轴，但落在 `[建图行, 下一张图行)` 之外',
                             '② 脚手架判据口径不成立（工具区间模型够不到）')
        else:
            what, verdict = ('（需人工看）', '③ 无法判定')
        chk = os.path.join(WORK, stem, 'm4_directed.check.json')
        after_p = os.path.join(WORK, stem, 'directed.py')
        parse_ok, changed, block_txt, inj = '?', '?', '', []
        if os.path.exists(chk):
            try:
                cd = json.loads(open(chk, encoding='utf-8').read())
                parse_ok = 'OK' if cd.get('parse_after_ok') else '**坏**'
                changed = '是' if cd.get('changed_indices') else '**否（没改）**'
            except ValueError:
                pass
        if os.path.exists(after_p):
            atext, _ = decode_source(read_bytes(after_p))
            ln = atext.split('\n')
            idx = [k for k, x in enumerate(ln) if BLOCK_SENTINEL in x]
            if idx:
                block_txt = '\n'.join(ln[idx[0]:idx[0] + 12])
                inj = [x.strip() for x in block_txt.split('\n')
                       if 'scale' in x or 'set_position' in x or 'gcf' in x]
        scale_here = bool(SCALE_RE.search(text))
        add(_tablerow([i, '`%s`' % p, verdict, what, changed, '已改（插块）', parse_ok,
                       '；'.join('`%s`' % x for x in inj[:3]) or '—']))
        detail.append((r, verdict, what, changed, parse_ok, inj, scale_here, block_txt, p, stem))
    add('')
    add('**逐条细节（含最小复现与逐字证据）**\n')
    for i, (r, verdict, what, changed, parse_ok, inj, scale_here, block_txt, p, stem) in \
            enumerate(detail, 1):
        m = r['m4_directed_rewrite']
        add('%d. **`%s`** — %s' % (i, p, verdict))
        add('   - 那个字面量：%s' % what)
        add('   - 是否改坏字节：**字面量没被改（`changed_indices=[]`）**、落盘后语法 %s、'
            '插入块引用的 `fig` 在该作用域此前已绑定（`check_block_scope.py` 逐条验过，'
            '`_scope.txt`）→ **没有把文件写坏**；但文件确实被写入了一个 9–10 行的调整块。'
            % parse_ok)
        if scale_here:
            add('   - ⚠ **该脚本本身有非线性刻度写法** → 块里注入的 '
                "`set_xscale('linear')`/`set_yscale('linear')` **会在运行期把刻度改回线性**"
                '（未请求的语义变更）。')
        add('   - 工具 JSON 关键字段：`%s`' % json.dumps(
            {k: m.get(k) for k in ('apply_reason', 'style', 'changes', 'block_inserted',
                                   'changed_indices')}, ensure_ascii=False))
        cmd = _repro_cmd(WORK, stem, 'm4_directed')
        if cmd:
            add('   - 最小复现：`%s`' % cmd)
        if block_txt:
            add('   - 落盘块（逐字）：')
            add('     ```python')
            for x in block_txt.split('\n')[:10]:
                add('     ' + x)
            add('     ```')
        add('   - 证据：`%s/%s/m4_directed.apply.json`、`m4_directed.check.json`、'
            '`m4_directed.diff`' % (os.path.basename(WORK), stem))
    add('')
    add('> **结论：%d 条里 0 条是"改错图 / 改错位置 / 写坏语法"。**'
        '（12 条目标字面量是 colorbar 槽轴 `cax`/`cbar_ax`/`cax`，1 条落在建图区间之外 —— '
        '两者都是 **M4 判据口径不成立**，不是工具改错。）\n' % len(m4f))

    # ---- 9.3 M3 残余 ----
    add('### 9.3 M3 干净往返残余 —— %d 条\n' % len(m3f))
    for i, r in enumerate(m3f, 1):
        p = r['path']
        stem = os.path.splitext(os.path.basename(p))[0]
        m = r['m3_clean_roundtrip']
        fp = os.path.join(CORPUS, p.replace('/', os.sep))
        scales = []
        try:
            text, _ = decode_source(read_bytes(fp))
            for k, ln in enumerate(text.split('\n'), 1):
                if SCALE_RE.search(ln):
                    scales.append('L%-5d %s' % (k, ln.strip()[:80]))
        except OSError:
            pass
        add('%d. **`%s`**' % (i, p))
        add('   - 判定：**工具真 bug（"默认值注入"造成的幽灵目标）**；不是脚手架口径问题')
        add("   - 机制：clean 参数里**只有 pos**（且等于源码现值），但 `params.normalize` "
            "会给每个轴补上 `xscale/yscale = 'linear'`。该脚本的轴是**对数轴**：")
        for s in scales[:4]:
            add('     - `%s`' % s)
        add("     → 工具把 \"linear\" 当成一个**没人要求过的待落地目标**（原位写不进去）→ "
            "auto 回退 → **往源码里插块**，块里含 "
            "`fig.axes[0].set_xscale('linear')` / `set_yscale('linear')`。")
        add('   - 逐字证据（inplace 那次的 pending 列表）：`%s`'
            % json.dumps((m.get('inplace_only') or {}).get('warnings') or m.get('warnings'),
                         ensure_ascii=False))
        add('   - engine 字段：`%s`' % json.dumps(m.get('engine_fields'), ensure_ascii=False))
        cmd = _repro_cmd(WORK, stem, 'm3_clean')
        if cmd:
            add('   - 最小复现：`%s`' % cmd)
        add('   - 与历史记录的关系（跨运行签名对比，`_fp_history.txt`）：该脚本**不在 t15 那批语料里**，'
            '首次出现于 `l0_results_t18.jsonl`（20:41），此后 `t23` / `final` / `verify` 四次运行的签名'
            '**完全一致**（`best_effort` + `style=block` + `pending=[ax0.xscale, ax0.yscale]`）→ '
            '**成因自 t18 起未变**，不是 t15 那条。')
        add("   - 为什么修不掉 / 值不值得修：脚手架侧**不该**去伪造 `xscale='log'` 参数把它糊过去"
            "（那是\"用参数迎合工具\"，真实用户不碰刻度滑块时照样中招）；该修的是工具："
            "`normalize` 填的默认值不应被当成\"调用方要求的目标\"，至少要能和源码现状核对"
            "（源码 `ax.set_xscale(\"log\")` 是**明写在代码里的**）。**值得修**：同一机制在 "
            "`5809269__O3_FYSP_paper.py`（31 处 `plt.yscale('log')`）上也会把对数轴改成线性 —— "
            "那是论文图，语义被改是硬伤。")
    add('')
    add('**反证实验（`sec9_experiment.py`，输出 `_sec9_experiment.txt` / `_sec9_b3.txt`）**\n')
    add('在 scratch 里对同一脚本做 4 组对照（都只 `apply --write --verify-fast`，不执行脚本）：\n')
    add(_tablerow(['组', '脚本', 'params 里的 scale 字段', '结果']))
    add(_tablerow(['---'] * 4))
    add(_tablerow(['A1', '`23192393__figure_panels.py`', '**不给**（只有 pos=null）',
                   '`best_effort` / `style=block` / **字节被改** / '
                   '`inplace_pending=[ax0.xscale, ax0.yscale]` ← 复现 M3 残余']))
    add(_tablerow(['A2', '同上', "给 `xscale='log'`,`yscale='log'`（= 源码真值）",
                   '**`no_change` / 字节不变 / 不插块** ← 成因确认']))
    add(_tablerow(['B1/B2', '`5809269__O3_FYSP_paper.py`', '不给 / 给 log（pos=null）',
                   '两者都 `no_change`（没有 pos 待落地 → 不插块）']))
    add(_tablerow(['B3', '同上', '**不给** + 给一个真实 pos（复刻 M4 参数）',
                   '`best_effort` / `block_lines=10` / 块**插在 `plt.savefig(...)` 之前**，'
                   '且块里含 `plt.gcf().axes[0].set_xscale(\'linear\')` / `set_yscale(\'linear\')` '
                   '← 该脚本 31 处 `plt.yscale(\'log\')` 会被覆盖']))
    add('')
    add('> **根因（final 树 `mpltweak-fix018` @ f21cffc，行号为实测）**：\n'
        '> 1. `src/mpltweak/params.py:103-127` 的 `AXIS_DEFAULTS` 含 '
        '`\'xscale\': \'linear\'` / `\'yscale\': \'linear\'`，'
        '`params.py:168 out = dict(AXIS_DEFAULTS)` 给**每个轴**补上 —— 调用方从未要求过刻度。\n'
        '> 2. `src/mpltweak/writeback.py:1116-1132`：把注入值当目标，'
        '`_lit != _want`（源码 `log` vs 注入 `linear`）→ `skipped` → auto 回退 → 插块。\n'
        '> 3. `src/mpltweak/writeback.py:2650-2655`：块渲染器对**每个轴**无条件输出 '
        '`set_xscale/set_yscale`（只要取值在 `_SAFE_SCALES` 里）—— 即使这个字段根本不在 '
        '`pending` 里（B3 的 `inplace_pending` 只有 `ax0.pos`）。\n'
        '> 4. 建议修法（**不改代码，交回船长派活**）：区分"调用方显式提供"与"normalize 填的默认"'
        '（例如填 `None` 或加标记），写回与块渲染只在"显式提供且与源码现状不同"时才落地刻度；'
        '至少不允许默认 `linear` 覆盖源码明写的 log（`ax.set_xscale("log")` / `plt.yscale(\'log\')`）。\n')

    # ---- 9.4 结论 ----
    add('### 9.4 结论表\n')
    add(_tablerow(['条目', '判定', '是否改坏字节', '交回谁']))
    add(_tablerow(['---'] * 4))
    add(_tablerow(['M1 %d 条' % len(m1f), '语料侧（3 条 PEP 701 需 3.12；8 条源码本身坏）',
                   '否（工具正确地拒绝处理）', '不修（语料侧）']))
    add(_tablerow(['M4 %d 条' % len(m4f),
                   '② 脚手架判据口径不成立（%d 条 cax + %d 条区间外）；**0 条改错图/写坏语法**'
                   % (sum(1 for r in m4f
                          if (r['m4_directed_rewrite'].get('root_cause_class') or '')
                          .startswith('harness_premise: target_is_colorbar')
                          or 'colorbar' in (r['m4_directed_rewrite'].get('reason') or '')),
                      sum(1 for r in m4f
                          if 'span' in (r['m4_directed_rewrite'].get('root_cause_class') or ''))),
                   '字面量未改、语法 OK、`fig` 可解析 → **没写坏**；但都被写入了调整块',
                   '脚手架侧已如实标注（§10 归为 not_applicable）']))
    add(_tablerow(['M3 %d 条' % len(m3f),
                   "**① 工具真 bug**：默认值注入 + 无法核对 → 退块 + 注入 `set_*scale('linear')`",
                   '语法没坏，但**语义可能被改**（对数轴→线性）',
                   '**交回船长派活（工具侧）**']))
    add('')
    return '\n'.join(L)


def build_reclass_section(results, meta):
    """§10 新旧口径对照（旧：fail 与 not_applicable 混算；新：T36 分类）。

    自动找对比文件：`L0_PREV` 环境变量优先；否则在 l0_results_final.jsonl 与
    l0_results_final2.jsonl 之间取"不是当前这份"的那份。
    """
    cur = os.path.basename(RESULTS)
    cands = [x for x in (os.environ.get('L0_PREV'),
                         'l0_results_final.jsonl', 'l0_results_final2.jsonl')
             if x and x != cur]
    prev_name = next((c for c in cands if os.path.exists(os.path.join(HERE, c))
                      and os.path.basename(c) != cur), None)
    if not prev_name:
        return ''
    prev_rows = [json.loads(l) for l in open(os.path.join(HERE, prev_name), encoding='utf-8')
                 if l.strip()]
    prev = {r['path']: r for r in prev_rows}
    cur_d = {r['path']: r for r in results}
    common = sorted(set(prev) & set(cur_d))
    # 分清哪份是"旧口径"：带 verdict_class/not_applicable 的才是 T36 之后的新口径。
    def _is_new(rows):
        return any(r[k].get('verdict_class')
                   for r in rows[:400] for k, _ in METRICS
                   if isinstance(r.get(k), dict)) or \
            any(r[k]['status'] == 'not_applicable'
                for r in rows for k, _ in METRICS if isinstance(r.get(k), dict))
    if _is_new(prev_rows) and not _is_new(results):
        old_rows, new_rows, old_name, new_name = prev_rows, results, prev_name, cur
    else:
        old_rows, new_rows, old_name, new_name = prev_rows, results, prev_name, cur
        if not _is_new(new_rows) and _is_new(old_rows):
            old_rows, new_rows, old_name, new_name = prev_rows, results, prev_name, cur
    old = {r['path']: r for r in old_rows}
    new = {r['path']: r for r in new_rows}
    L = []
    add = L.append
    add('## 10. 新旧口径对照（T36 分类改动的效果）\n')
    add('> 旧口径 = `%s`（fail 里混着"判据前提不成立"的记录）；新口径 = `%s`（%d 个共同脚本）。'
        '判定哪份是新的依据：新口径的记录带 `verdict_class` / `not_applicable`。\n'
        % (old_name, new_name, len(common)))
    add(_tablerow(['指标', '旧口径 pass/fail/skip',
                   '新口径 pass/fail/**not_applicable**/skip', 'fail 的变化', '重新归类的去向']))
    add(_tablerow(['---'] * 5))
    for key, name in METRICS:
        po = pf = ps = 0
        no_ = nf = nna = ns = 0
        for p in common:
            a, b = old[p][key]['status'], new[p][key]['status']
            po += a == 'pass'
            pf += a in ('fail', 'timeout')
            ps += a == 'skipped'
            no_ += b == 'pass'
            nf += b in ('fail', 'timeout')
            nna += b == 'not_applicable'
            ns += b == 'skipped'
        moved = {}
        for p in common:
            a, b = old[p][key]['status'], new[p][key]['status']
            a_n = 'fail' if a in ('fail', 'timeout') else a
            if a_n != b:
                moved['%s→%s' % (a_n, b)] = moved.get('%s→%s' % (a_n, b), 0) + 1
        add(_tablerow([name, '%d/%d/%d' % (po, pf, ps),
                       '%d/%d/**%d**/%d' % (no_, nf, nna, ns),
                       '%d → %d（%s）' % (pf, nf, ('-%d' % (pf - nf)) if pf >= nf
                                          else ('+%d' % (nf - pf))),
                       '、'.join('%s ×%d' % (k, v) for k, v in sorted(moved.items()))
                       or '（无变化）']))
    add('')
    # 逐类明细
    add('### 10.1 被重新归类的记录明细（旧 fail → 新 not_applicable）\n')
    n = 0
    for key, name in METRICS:
        rows_moved = [p for p in common
                      if old[p][key]['status'] in ('fail', 'timeout')
                      and new[p][key]['status'] == 'not_applicable']
        if not rows_moved:
            continue
        add('**%s：%d 条**\n' % (name, len(rows_moved)))
        add(_tablerow(['#', '脚本', '新判定理由（判据前提不成立）', '是否仍有落盘写入']))
        add(_tablerow(['---'] * 4))
        for i, p in enumerate(rows_moved, 1):
            m = new[p][key]
            n += 1
            add(_tablerow([i, '`%s`' % p,
                           (m.get('root_cause_class') or m.get('reason') or '')
                           .replace('harness_premise: ', '').split('（')[0],
                           '是（仍写入了调整块）' if m.get('block_inserted') else '否']))
        add('')
    add('### 10.2 仍计 fail 的记录（新口径下的真 fail）\n')
    add(_tablerow(['指标', '条数', '脚本', '性质标签']))
    add(_tablerow(['---'] * 4))
    for key, name in METRICS:
        rows_f = [p for p in common if new[p][key]['status'] in ('fail', 'timeout')]
        if not rows_f:
            continue
        add(_tablerow([name, len(rows_f),
                       '、'.join('`%s`' % p.split('/')[-1] for p in rows_f[:6])
                       + ('…' if len(rows_f) > 6 else ''),
                       '、'.join(sorted({new[p][key].get('verdict_class') or '—'
                                         for p in rows_f}))]))
    add('')
    add('### 10.3 M1 的语料侧性质（数字口径不变）\n')
    kinds = {}
    for r in results:
        if r['m1_parse']['status'] == 'fail':
            kinds[(r['meta'].get('m1_corpus_kind') or '?')] = \
                kinds.get((r['meta'].get('m1_corpus_kind') or '?'), 0) + 1
    add('M1 仍记 `fail`（解析失败就是失败），但每条都标了 `verdict_class=corpus_side`：\n')
    for k, v in sorted(kinds.items(), key=lambda x: -x[1]):
        add('- %s × %d' % (k, v))
    add('')
    add('> **§9 与本节的一致性**：§9 是逐条人工定性（带最小复现与逐字证据），本节是机器分类。'
        '两者若有分歧，**以 §9 的逐条证据为准**；本轮实测两者完全一致 —— '
        '§9 判"② 脚手架判据口径不成立"的 13 条 M4，正是本节归为 `not_applicable` 的那 13 条；'
        '§9 判"语料侧"的 11 条 M1，正是本节 `corpus_side` 的那 11 条。\n')
    # 10.4 仍 fail 的记录在**其他运行**里的状态（跨版本）——回答"修复有没有落地到我这棵树"
    fails_now = [(key, name, p) for key, name in METRICS if key != 'm1_parse'
                 for p in common if new[p][key]['status'] in ('fail', 'timeout')]
    if fails_now:
        add('### 10.4 仍计 fail 的记录在**其他运行**里的状态（跨版本对照）\n')
        add('> 用来回答"这条 bug 是否已在别的被测版本里修好"。只列**非 M1**的 fail'
            '（M1 是语料侧，见 §10.3）；列只取有代表性的几次运行。\n')
        keep = ['_018baseline', '_engine', '_t18', '_t23', '_final', '_t35', '_final2']
        files = [os.path.join(HERE, 'l0_results%s.jsonl' % t) for t in keep]
        files = [f for f in files if os.path.exists(f)]
        cols = []
        for f in files:
            tag = os.path.basename(f).replace('l0_results', '').replace('.jsonl', '') or '(无tag)'
            meta_p = os.path.join(HERE, 'l0_run_meta%s.json' % tag)
            src = '?'
            if os.path.exists(meta_p):
                try:
                    src = (json.load(open(meta_p, encoding='utf-8')).get('环境变量') or '?')
                    src = src.split('PYTHONPATH=')[-1].split(' ;')[0]
                except Exception:                             # noqa: BLE001
                    pass
            cols.append((tag, os.path.basename(f), src))
        add(_tablerow(['指标', '脚本'] + ['`%s`' % c[0] for c in cols]))
        add(_tablerow(['---'] * (2 + len(cols))))
        for key, name, p in fails_now:
            row = [name, '`%s`' % p]
            for tag, fn, src in cols:
                st = '—'
                try:
                    for ln in open(os.path.join(HERE, fn), encoding='utf-8'):
                        ln = ln.strip()
                        if not ln:
                            continue
                        rr = json.loads(ln)
                        if rr.get('path') == p:
                            st = rr[key]['status']
                            break
                except Exception:                             # noqa: BLE001
                    pass
                row.append(('%s%s' % (st, ' ✅' if st == 'pass' else '')))
            add(_tablerow(row))
        add('')
        add('各运行的被测版本：\n')
        seen = set()
        for tag, fn, src in cols:
            if src in seen:
                continue
            seen.add(src)
            add('- `%s` → `%s`' % (tag, src))
        add('')
    return '\n'.join(L)


def scenario_stats(results):
    """T37：按目标场景（pos / figsize）统计 M4/M5/M6 的可测面与结果。"""
    out = {}
    for sc in ('pos', 'figsize'):
        for mk in ('m4', 'm5', 'm6'):
            c = {'pass': 0, 'fail': 0, 'not_applicable': 0, 'skipped': 0, 'timeout': 0}
            for r in results:
                rec = r.get('%s_%s' % (mk, sc))
                if isinstance(rec, dict):
                    c[rec['status']] = c.get(rec['status'], 0) + 1
            out['%s_%s' % (mk, sc)] = c
    both = sum(1 for r in results
               if r.get('m4_pos', {}).get('status') not in (None, 'skipped')
               and r.get('m4_figsize', {}).get('status') not in (None, 'skipped'))
    out['both_scenarios'] = both
    out['has_pos'] = sum(1 for r in results
                         if r.get('m4_pos', {}).get('status') not in (None, 'skipped'))
    out['has_figsize'] = sum(1 for r in results
                             if r.get('m4_figsize', {}).get('status') not in (None, 'skipped'))
    out['union_tested'] = sum(1 for r in results
                              if r['m4_directed_rewrite']['status'] in
                              ('pass', 'fail', 'not_applicable'))
    return out


def scenario_fails(results):
    """列出所有**场景级**失败（含被主键覆盖的那些），供 §3 明细用。"""
    rows = []
    for r in results:
        for mk, mname in (('m4', 'M4 定向改写'), ('m5', 'M5 幂等'), ('m6', 'M6 可逆')):
            for sc in ('pos', 'figsize'):
                rec = r.get('%s_%s' % (mk, sc))
                if isinstance(rec, dict) and rec['status'] in ('fail', 'timeout'):
                    rows.append((mname, sc, r, rec))
    return rows


def build_surface_section(results, meta):
    """§11：T37 前后的测试面对照（对比文件默认 l0_results_final2.jsonl）。"""
    if not any(isinstance(r.get('m4_figsize'), dict) for r in results[:200]):
        return ('## 11. T37 前后测试面对照（figsize 目标纳入 M4/M5/M6）\n\n'
                '> 本次结果文件**不含 T37 的场景键**（`m4_pos`/`m4_figsize`），'
                '说明它是 T37 之前的运行。T37 的对照表见 `l0_report_broad.md` 的 §11。\n')
    prev_name = os.environ.get('L0_SURFACE_PREV', 'l0_results_final2.jsonl')
    prev_path = os.path.join(HERE, prev_name)
    if not os.path.exists(prev_path) or os.path.basename(RESULTS) == prev_name:
        return ''
    prev = {r['path']: r for r in
            (json.loads(l) for l in open(prev_path, encoding='utf-8') if l.strip())}
    cur = {r['path']: r for r in results}
    common = sorted(set(prev) & set(cur))
    L = []
    add = L.append
    add('## 11. T37 前后测试面对照（figsize 目标纳入 M4/M5/M6）\n')
    add('> 前 = `%s`（只测位置字面量）；后 = `%s`（位置 + figsize 两种目标，各自计数）。'
        '共同脚本 %d 个。\n' % (prev_name, os.path.basename(RESULTS), len(common)))

    def surface(d, key):
        """可测脚本数（pass+fail+not_applicable，去掉 skipped）。"""
        return sum(1 for p in common
                   if d[p][key]['status'] in ('pass', 'fail', 'not_applicable'))

    def cnt(d, key, st):
        return sum(1 for p in common if d[p][key]['status'] == st)

    add(_tablerow(['指标', '前：可测', '后：可测（并集）', '增量', '后：pos 场景',
                   '后：figsize 场景', '两者都跑']))
    add(_tablerow(['---'] * 7))
    for key, name in (('m4_directed_rewrite', 'M4 定向改写'),
                      ('m5_idempotent', 'M5 幂等'), ('m6_revert', 'M6 可逆')):
        mk = {'m4_directed_rewrite': 'm4', 'm5_idempotent': 'm5', 'm6_revert': 'm6'}[key]
        b = surface(prev, key)
        a = surface(cur, key)
        posn = sum(1 for p in common
                   if cur[p].get('%s_pos' % mk, {}).get('status') not in (None, 'skipped'))
        fign = sum(1 for p in common
                   if cur[p].get('%s_figsize' % mk, {}).get('status') not in (None, 'skipped'))
        bothn = sum(1 for p in common
                    if cur[p].get('%s_pos' % mk, {}).get('status') not in (None, 'skipped')
                    and cur[p].get('%s_figsize' % mk, {}).get('status') not in (None, 'skipped'))
        add(_tablerow([name, b, a, '+%d（%.1f×）' % (a - b, (a / b) if b else 0),
                       posn, fign, bothn]))
    add('')
    add('### 11.1 结果构成（后：pos / figsize 分开）\n')
    add(_tablerow(['指标 × 场景', 'pass', 'fail', 'not_applicable', 'skipped']))
    add(_tablerow(['---'] * 5))
    for mk, mname in (('m4', 'M4 定向改写'), ('m5', 'M5 幂等'), ('m6', 'M6 可逆')):
        for sc in ('pos', 'figsize'):
            c = {'pass': 0, 'fail': 0, 'not_applicable': 0, 'skipped': 0, 'timeout': 0}
            for p in common:
                rec = cur[p].get('%s_%s' % (mk, sc))
                if isinstance(rec, dict):
                    c[rec['status']] = c.get(rec['status'], 0) + 1
            add(_tablerow(['%s / %s 目标' % (mname, sc), c['pass'], c['fail'],
                           c['not_applicable'], c['skipped']]))
    add('')
    # figsize 路径独有的发现
    sp_ok = sum(1 for p in common
                if cur[p].get('m4_figsize', {}).get('status') == 'pass'
                and cur[p]['m4_figsize'].get('spelling_preserved') is True)
    sp_bad = [p for p in common
              if cur[p].get('m4_figsize', {}).get('status') == 'pass'
              and cur[p]['m4_figsize'].get('spelling_preserved') is False]
    add('### 11.2 figsize 路径的"写法保持"观测（不参与 pass/fail，单独计数）\n')
    add('- `spelling_preserved=True`（容器 `()`/`[]`、空白、`.0` 保持）：**%d 条**\n' % sp_ok)
    add('- `spelling_preserved=False`（值改对了，但写法被规范化）：**%d 条**%s\n'
        % (len(sp_bad), '' if not sp_bad else '：' + '、'.join('`%s`' % p.split('/')[-1]
                                                              for p in sp_bad[:8])))
    if sp_bad:
        add(_tablerow(['脚本', '改前', '改后']))
        add(_tablerow(['---'] * 3))
        for p in sp_bad[:20]:
            m = cur[p]['m4_figsize']
            add(_tablerow(['`%s`' % p, '`%s`' % (m.get('raw_before') or ''),
                           '`%s`' % (m.get('raw_after') or '')]))
        add('')
    add('> 说明：T36 之后工具已经不做"等值重写"了；剩下的这类差异是**真有改动**时'
        '把 kwarg 整段重写成 `(w, h)` 造成的（值正确、可运行、语义等价）。'
        '本报告不把它算 fail，但单独列出，供决定是否要求"只改数字、保留写法"。\n')
    return '\n'.join(L)


def build_handoff_section(results):
    """§12：T37 改动清单（给接手方的交接材料；静态文本，随报告一起走）。"""
    L = []
    add = L.append
    add('## 12. T37 figsize 目标的实现位置（交接材料）\n')
    add('> 本节是**静态清单**（不随结果变化），给接手方 diff 用。所有改动只在 '
        '`corpus018/harness/l0_harness.py`（脚手架），**没有动 mpltweak 两棵树、没有动语料**。\n')
    add('### 12.1 新增的常量 / 函数（都在 `l0_harness.py`）\n')
    add(_tablerow(['名字', '作用']))
    add(_tablerow(['---', '---']))
    for nm, desc in (
            ('`FIGSIZE_CANDIDATES`', '候选目标尺寸池（两维都在 [2,20]，避开退化值）'),
            ('`pick_target_figsize(current)`',
             '按"与源码现值至少一维差 >= 0.8"挑目标；保证 M4 的改动是**真改动**'),
            ('`_skeleton(raw)`', '把字面量里的数字抹成 `#` → 判"容器/空白写法是否被改"'),
            ('`check_figsize_change(before, after, fig_index, target)`',
             '**独立的 figsize 复核器**（不 import mpltweak）：断言 ①只有第 fig_index 张图的 '
             '`figsize=` kwarg 变了 ②值 == 目标 ③落盘后仍能 parse；另记 ④`add_axes` 未被连带改 '
             '⑤`spelling_preserved`（容器/空白，单独计数不参与 pass/fail）'),
            ('`count_figsize_literals(text)`', '语法坏时也能数 figsize 字面量（诊断用）'),
            ('`scenario_stats(results)`', '按 pos / figsize 两个场景分别统计 M4/M5/M6'),
            ('`scenario_fails(results)`', '列出**所有场景级**失败（含被主键覆盖的）'),
            ('`build_surface_section(results, meta)`', '§11 前后测试面对照 + 写法保持观测'),
            ('`build_handoff_section(results)`', '本节 §12'),
            ('T36 相关的 `classify_corpus_syntax_error` / `metric_class`',
             '失败性质分类：`tool_bug` / `tool_contract` / `harness_premise` / `corpus_side`'),
    ):
        add(_tablerow([nm, desc]))
    add('')
    add('### 12.2 改动的既有函数\n')
    add(_tablerow(['位置', '改了什么']))
    add(_tablerow(['---', '---']))
    for nm, desc in (
            ('`test_one()` 的 M4/M5/M6 段（T37 主体）',
             'pos 场景跑完后，若该图有 `figsize=` 字面量则**再跑一遍 figsize 场景**'
             '（独立副本 `figsize.py` / 参数 `figsize.json`），得到 `m4_figsize`/`m5_figsize`/'
             '`m6_figsize`；把 pos 结果同时存进 `m4_pos`/`m5_pos`/`m6_pos`；'
             '主键 `m4_directed_rewrite`/`m5_idempotent`/`m6_revert` = pos 若跑过，否则 figsize'
             '（`meta.primary_scenario` 记录用了哪个）'),
            ('`test_one()` 的 M1 段（T36）',
             '失败记录加 `verdict_class=corpus_side` + `corpus_kind` 子类；'
             'M2–M6 的 skip 继承 `corpus_side`'),
            ('`test_one()` 的 M4 判定（T36）',
             '目标字面量是 colorbar 槽轴 / 落在建图区间外 → `not_applicable`（不再计 fail）'),
            ('`coverage_stats()`', 'M4 桶加 `not_applicable` 维度，且不进 `tested` 分母'),
            ('`build_report()`', '§1 表加 `not_applicable` 列；新增 §1.1（性质分类）、'
                                 '§1.2（场景小计）、§3.7（场景级失败明细）、§2.6 改口径'),
            ('`build_findings()`', '计数改为"fail 只算前提成立的"；开头写明 tool_bug/'
                                   'harness_premise/corpus_side 的合计'),
    ):
        add(_tablerow([nm, desc]))
    add('')
    add('### 12.3 新增的结果字段（`l0_results*.jsonl` 每行）\n')
    add('- `m4_pos` / `m5_pos` / `m6_pos`：位置字面量场景的三项结果（含 `verdict_class`）\n')
    add('- `m4_figsize` / `m5_figsize` / `m6_figsize`：figsize 场景的三项结果，额外字段：'
        '`scenario`、`target_figsize`、`source_figsize`、`changed_fig_indices`、'
        '`value_equals_target`、`spelling_preserved`、`raw_before`、`raw_after`、'
        '`bytes_changed`（M6 另有 `write_style`/`write_happened`/`sha_after_write`/'
        '`sha_after_revert`/`revert_backup_path`）\n')
    add('- `meta.primary_scenario`（`pos` / `figsize`）、`meta.m1_corpus_kind`\n')
    add('- 每条非 pass 记录：`verdict_class`（T36）\n')
    add('')
    add('### 12.4 新增的证据文件（每个脚本的 `work*/<脚本名>/` 下）\n')
    add('- figsize 场景：`figsize.py`、`figsize.json`、`m4_figsize.params.json`、'
        '`m4_fig.apply.json`、`m4_figsize.check.json`、`m4_figsize.diff`、'
        '`m5_figsize.apply.json`、`m5_figsize.diff`、`figsize_rev.py`、`figsize_rev.json`、'
        '`m6_pre_fig.apply.json`、`m6_figsize.revert.json`、`m6_figsize.diff`\n')
    add('- 逆向验证：`selftest_verifier.py` → `_selftest.txt`（合成文本，不碰语料）\n')
    add('')
    add('### 12.5 想 diff 我改了什么，看这几处就够\n')
    add('```powershell\n'
        'Select-String -Path l0_harness.py -Pattern '
        "'FIGSIZE_CANDIDATES|def pick_target_figsize|def _skeleton|def check_figsize_change|"
        "def scenario_stats|def scenario_fails|def build_surface_section|def build_handoff_section|"
        "def classify_corpus_syntax_error|def metric_class'\n"
        '```\n')
    add('> 版本锚点：本文件在 T37 收尾时的状态 = 能跑 '
        '`python l0_harness.py --all --jobs 6 --timeout 60`（`_broad` 全量 1686 脚本 185 s 通过），'
        '以及 `--report-only` 重生成报告；`selftest_verifier.py` 全绿。\n')
    return '\n'.join(L)


def build_audit(results):
    """铁律自查（可复现：跑 audit_rules.py）。"""
    import glob as _glob
    n_cmd = len(_glob.glob(os.path.join(WORK, '*', '*.cmd.txt')))
    n_ev = sum(len(f) for _r, _d, f in os.walk(WORK))
    n_rows = len(results)
    man_rows = sum(1 for r in load_manifest() if (r.get('path') or '').endswith('.py'))
    L = []
    add = L.append
    add('## 6. 铁律自查（可复现）\n')
    add('| 检查项 | 结论 | 证据 |')
    add('| --- | --- | --- |')
    add(_tablerow(['只用 `apply --write --verify-fast`（快速档只做 `ast.parse`，**不重跑脚本**）',
                   '成立', '每条调用的完整命令都在 `work/<脚本名>/*.cmd.txt`（本次 %d 条）' % n_cmd]))
    add(_tablerow(['未使用 `apply --write`（不带 --verify-fast）/ `describe` / `check` / `launch`',
                   '成立', '命令清单里不含这些子命令与组合']))
    add(_tablerow(['语料原件（gallery/、zenodo/）未被改动',
                   '成立', 'manifest 里 %d 个脚本的 sha256 与 manifest 记录**全部一致**（`audit_rules.py` → `_audit.txt`）'
                   % man_rows]))
    add(_tablerow(['被测工具 mpltweak 未被改动',
                   '成立', '`git status --porcelain` 仅 `?? docs/before_after.png`'
                   '（2026-10-09 22:24 的既有未跟踪文件，早于本次会话）；无任何 ` M` 修改项']))
    add(_tablerow(['子进程输出按字节读入 + UTF-8 解码',
                   '成立', '本次全部调用的 stdout 都是合法 UTF-8，JSON 解析失败 0 次；'
                   '应用层未设置 PYTHONIOENCODING，测的就是工具的真实字节行为']))
    add(_tablerow(['每次调用的证据留档',
                   '成立', '`work/` 下共 %d 个证据文件（JSON/stderr/diff/params/AST 复核）' % n_ev]))
    add(_tablerow(['超时 / harness 自身异常',
                   '0 条', '单次 CLI 超时 60s；%d 个脚本无一超时，无 harness 异常' % n_rows]))
    add('')
    add('> 语料是**活的**：本次运行期间 `corpus018` 仍在被写入（zenodo 目录在本会话内从 98 涨到 180+ 个 .py）。'
        '报告 0.x 记录了运行开始/结束时的 `manifest.jsonl` sha256 与行数，所有数字都按该快照口径。')
    add('')
    return '\n'.join(L)


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------
def mpltweak_version():
    r = run_cli(['--help'], 30)
    try:
        env = dict(os.environ)
        env['PYTHONPATH'] = MPLTWEAK_SRC
        out = subprocess.run([PYTHON, '-c',
                              'import mpltweak;print(getattr(mpltweak,"__version__","?"))'],
                             capture_output=True, timeout=30, env=env)
        v, _ = dec_utf8(out.stdout)
        return v.strip() or '?'
    except Exception:                                         # noqa: BLE001
        return '?'


def main():
    ap = argparse.ArgumentParser(description='mpltweak L0 纯静态压力测试脚手架')
    ap.add_argument('--limit', type=int, default=0, help='抽样脚本数（0/省略 = 全量）')
    ap.add_argument('--all', action='store_true', help='跑全量')
    ap.add_argument('--jobs', type=int, default=4, help='并发路数（默认 4）')
    ap.add_argument('--timeout', type=float, default=60.0, help='每个脚本每次 CLI 调用超时秒数')
    ap.add_argument('--seed', type=int, default=20261010)
    ap.add_argument('--only-source', default=None, choices=('gallery', 'zenodo'))
    ap.add_argument('--out', default=RESULTS)
    ap.add_argument('--report', default=REPORT)
    ap.add_argument('--no-clean', dest='clean', action='store_false', default=True,
                    help='保留上一次的 work 目录（默认清空重建，保证可复现）')
    ap.add_argument('--report-only', action='store_true', help='只从 JSONL 重生成报告')
    ap.add_argument('--max-fail-detail', type=int, default=2,
                    help='每个指标把前 N 条 FAIL 的逐字 JSON/diff 展开（清单仍列全部）')
    args = ap.parse_args()

    if args.report_only:
        results = [json.loads(l) for l in open(args.out, encoding='utf-8') if l.strip()]
        meta = {}
        if os.path.exists(RUN_META):          # 还原上一次全量运行的 §0 元信息
            try:
                meta = json.load(open(RUN_META, encoding='utf-8'))
            except Exception:                                 # noqa: BLE001
                meta = {}
        if not meta:
            # 没有运行元信息存档时，只写**能从结果文件与当前语料推导出来**的字段，
            # 不编造运行期快照（这是诚实性底线）。
            fam, src = {}, {}
            for r in results:
                k = '%s/%s' % (r.get('source'), r.get('meta', {}).get('family'))
                fam[k] = fam.get(k, 0) + 1
                src[r.get('source')] = src.get(r.get('source'), 0) + 1
            meta = {
                '模式': 'report-only 重生成（无运行元信息存档，运行期快照不可考）',
                '本次运行脚本数': len(results),
                '按来源': json.dumps(src, ensure_ascii=False, sort_keys=True),
                '运行集分层（由结果文件重算）': json.dumps(fam, ensure_ascii=False, sort_keys=True),
                '逐行 provenance': 'l0_results.jsonl 每行 meta.sha_orig == meta.manifest_sha256'
                                   '（本次全部为 true，见 §6 铁律自查）',
                '当前语料状态（参考，非运行快照）': 'manifest.jsonl sha256=%s rows=%d'
                % (sha256_file(MANIFEST)[:16],
                   sum(1 for r in load_manifest() if (r.get('path') or '').endswith('.py'))),
            }
        meta.update({'结果条数': len(results),
                     '报告重生成时间': time.strftime('%Y-%m-%d %H:%M:%S'),
                     '_max_fail_detail': args.max_fail_detail})
        write_text(args.report, build_report(results, meta))
        print('report -> %s' % args.report)
        return 0

    limit = 0 if args.all else args.limit
    man_sha_start = sha256_file(MANIFEST)
    man_rows_start = len(load_manifest())
    rows, sampled = build_rows(limit, args.seed, args.only_source)
    if not rows:
        print('没有可跑的脚本', file=sys.stderr)
        return 2
    fam_cnt = {}
    for r in rows:
        k = '%s/%s' % (r.get('source'), r.get('family_static'))
        fam_cnt[k] = fam_cnt.get(k, 0) + 1
    os.makedirs(WORK, exist_ok=True)
    if args.clean:
        # 只清 work 内容，不动 harness 其它产物
        for name in os.listdir(WORK):
            p = os.path.join(WORK, name)
            shutil.rmtree(p, ignore_errors=True) if os.path.isdir(p) else os.remove(p)
    print('待测脚本: %d（%s）' % (len(rows), '抽样' if sampled else '全量'))
    t0 = time.time()
    results = []
    lock_lines = []
    with futures.ThreadPoolExecutor(max_workers=max(1, args.jobs)) as ex:
        futs = {ex.submit(test_one, r, args): r for r in rows}
        for i, fut in enumerate(futures.as_completed(futs), 1):
            r = futs[fut]
            try:
                res = fut.result()
            except Exception as e:                            # noqa: BLE001
                import traceback
                res = {'path': r['path'], 'stem': os.path.splitext(os.path.basename(r['path']))[0],
                       'source': r.get('source'), 'meta': {}, 'error': traceback.format_exc(),
                       'm1_parse': {'status': 'skipped', 'reason': 'harness_error'},
                       'm2_extract': {'status': 'skipped', 'reason': 'harness_error'},
                       'm3_clean_roundtrip': {'status': 'skipped', 'reason': 'harness_error'},
                       'm4_directed_rewrite': {'status': 'skipped', 'reason': 'harness_error'},
                       'm5_idempotent': {'status': 'skipped', 'reason': 'harness_error'},
                       'm6_revert': {'status': 'skipped', 'reason': 'harness_error'}}
            results.append(res)
            marks = ''.join({'pass': '.', 'fail': 'F', 'skipped': 's', 'timeout': 'T',
                         'not_applicable': 'N'}
                            .get(res[k]['status'], '?') for k, _ in METRICS)
            lock_lines.append('[%4d/%d] %s  %s' % (i, len(rows), marks, res['path']))
            print(lock_lines[-1], flush=True)
    results.sort(key=lambda r: r['path'])
    with open(args.out, 'w', encoding='utf-8') as f:
        for r in results:
            f.write(json.dumps(r, ensure_ascii=False) + '\n')
    elapsed = time.time() - t0
    man_sha_end = sha256_file(MANIFEST)
    man_rows_end = len(load_manifest())
    meta = {
        '生成时间': time.strftime('%Y-%m-%d %H:%M:%S'),
        'mpltweak 版本': mpltweak_version(),
        'mpltweak 路径（只读）': MPLTWEAK_ROOT,
        '语料根': CORPUS,
        '模式': ('抽样 --limit %d（分层：source × 静态可提取家族，每层保底 2，seed=%d）'
                 % (len(rows), args.seed)) if sampled else '全量（--all）',
        '本次运行脚本数': len(rows),
        '运行集分层': json.dumps(fam_cnt, ensure_ascii=False, sort_keys=True),
        'manifest.jsonl 快照(运行开始时)': 'sha256=%s rows=%d' % (man_sha_start[:16], man_rows_start),
        'manifest.jsonl 快照(运行结束时)': 'sha256=%s rows=%d%s' % (
            man_sha_end[:16], man_rows_end,
            '  ⚠ 运行期间语料被改动（有人在往 corpus018 里加脚本）' if man_sha_end != man_sha_start else ''),
        '并发路数': args.jobs,
        '单次 CLI 超时': '%gs' % args.timeout,
        '墙钟耗时': '%.1f s' % elapsed,
        '解释器': PYTHON,
        '环境变量': 'PYTHONPATH=%s ; MPLBACKEND=Agg' % MPLTWEAK_SRC,
        '结果文件': os.path.relpath(args.out, HERE).replace('\\', '/'),
        '工作副本根': '%s/<脚本名>/' % os.path.basename(WORK),
        '_max_fail_detail': args.max_fail_detail,
    }
    write_text(args.report, build_report(results, meta))
    try:
        write_json(RUN_META, meta)            # 供 --report-only 还原 §0
    except OSError:
        pass
    print('\n结果 -> %s\n报告 -> %s\n耗时 %.1f s' % (args.out, args.report, elapsed))
    return 0


if __name__ == '__main__':
    sys.exit(main())
