# -*- coding: utf-8 -*-
"""
mpltweak.writeback —— AST 确定性写回（零 LLM 路径）
=====================================================================

把 ``.tweak_params/*.json`` 里的数值**确定性地**写回用户脚本，产出**纯 matplotlib
代码**：不 import 任何库、不引入本工具任何符号、用户脚本零依赖。原理（讨论文档 §5.4）：

  1. AST 扫脚本找锚点：
     - ``fig`` 变量名：``fig = plt.figure(...)`` / ``fig, ax = plt.subplots(...)``
       的赋值目标；**找不到时若是 pyplot 风格（import ... as plt）则用
       ``plt.gcf()`` 兜底**（单图无歧义；多图配"最后一个 savefig 之前"锚点）
     - 插入点：第一个 ``savefig``/``show`` 调用**之前**（保证改动发生在存图之前，
       必然进保存的图）；没有则最后一个 ``tight_layout`` 之后；再退脚本尾。
       锚点嵌在循环/if 里时，整块按锚点行缩进插入（不破坏语法）
     - mappable 变量：``cs = ax.contourf(...)`` 的赋值目标 + ``fig.colorbar(<name>)``
       的第一个实参
  2. 生成「自动调整块」：
     - 位置/字号/刻度/grid/spines/scale/线/图例全部用 ``fig.axes[i]`` 索引寻址
       —— 不依赖脚本里的任何变量名
     - colorbar 轴的 clim/cmap 用结构导航 ``fig.axes[i]._colorbar.mappable``
       —— 连变量名都不用（colorbar 对象创建时即挂在轴上的 ``_colorbar`` 属性）
     - 非 colorbar 轴的 clim/cmap 用 AST 扫到的 mappable 变量（``cs.set_clim(...)``）
  3. ``figsize``：能原位替换 ``subplots()/figure()`` 的 ``figsize=`` kwarg 就按源码
     偏移精确切片替换；否则写进调整块 ``fig.set_size_inches(...)``。
  4. 幂等：调整块首尾带哨兵注释；重复写回 = 整体替换旧块（不叠加）。块行号另存
     侧边元数据 ``.tweak_params/<名>.writeback.json``，双保险。
  5. 安全网（三层，绝不把改坏的脚本留在磁盘上）：
     写前备份 ``<脚本>.tweak.bak``；
     ① **能跑通**：Agg 无头重跑，exit 0；
     ② **改对了**：语义验证（``mpltweak.verify``）重跑并 dump 目标图状态，与参数的
        位置/字号/grid/spines/clim 逐项比对——"能跑通但布局没落到目标图"判失败；
     ③ **换锚点重试**：候选顺序 = 参数记录的图号对应的 savefig → 主锚点 → 脚本尾；
        全部失败才回滚。原脚本自己就跑不通（缺数据/依赖）时不冤枉写回，
        如实标注"未能验证"。

失败可预期：结构无法确定时返回结构化失败码（no_fig / fail），绝不静默写错。
mappable 走结构兜底时给出 best_effort 警告，由人工确认。
"""

from __future__ import annotations

import ast
import contextlib
import errno
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any, Dict, List, Tuple

from . import messages as _msg
from . import params
from . import verify as _verify

# ---- 调整块哨兵（幂等替换依据；中性命名，不在用户脚本里留品牌痕迹）----
# **必须语言无关**：哨兵同时是"识别旧块、整体替换"的锚点。若让它跟随系统语言，
# 中文系统写一次、英文系统再写一次就认不出旧块 → 堆两个块互相打架。
# 语言相关的那句说明写在哨兵**下面**，随便变都不影响识别。
BLOCK_START = '# ===== auto layout (generated) - do not edit inside ====='
BLOCK_END = '# ===== end auto layout ====='
# 旧版中文哨兵（0.1.x 已发布，用户脚本里可能已有）必须继续认，否则会被当成
# 普通代码、在它旁边再插一个新块。
_LEGACY_STARTS = ('# ===== 自动布局调整（由调图工具生成，勿手改；'
                  '重复写回会整体替换）=====',)
_LEGACY_ENDS = ('# ===== 自动布局调整结束 =====',)
_ALL_STARTS = (BLOCK_START,) + _LEGACY_STARTS
_ALL_ENDS = (BLOCK_END,) + _LEGACY_ENDS

# 结构化结果码（全小写，公共 CLI 输出契约）
OK = 'ok'
NO_FIG = 'no_fig'            # 找不到 figure/subplots 赋值目标（纯 pyplot 流）
NO_AXES = 'no_axes'          # 参数文件没有 axes
BEST_EFFORT = 'best_effort'  # 已写回，但有需人工确认处（mappable 兜底等）
FAIL = 'fail'                # 写文件/验证失败



FIG_FACTORIES = ('figure', 'subplots')
MAPPABLE_FACTORIES = ('contourf', 'pcolormesh', 'imshow', 'contour',
                      'scatter', 'bar', 'tripcolor', 'quiver', 'streamplot')
SAVE_ANCHORS = ('savefig', 'show')


# --------------------------------------------------------------------------
# 数值格式化（与 apply 的打印口径一致）
# --------------------------------------------------------------------------
def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return repr(v)
    if f == int(f) and abs(f) < 1e15:
        return '%d' % int(f)
    return '%.6g' % f


def _fmt_pos(v):
    return '%.4f' % v


def _is_num_literal(node) -> bool:
    """是否是"可整体替换的数字字面量"（含带符号写法）。

    ``-0.01`` 在 AST 里是 ``UnaryOp(USub, Constant(0.01))``，**不是** ``Constant`` ——
    只认 Constant 的话，`add_axes([-0.01, 0.1, ...])`、`set_xlim(-0.01, 1.01)`
    这些"越界一点"的常见写法会被静默丢进 skipped（外部审阅第 2 条点名的就是它）。
    替换是按节点整个源区间做的，所以带符号的字面量整体替换没问题。
    """
    if isinstance(node, ast.Constant):
        return isinstance(node.value, (int, float))
    if (isinstance(node, ast.UnaryOp)
            and isinstance(node.op, (ast.USub, ast.UAdd))
            and isinstance(node.operand, ast.Constant)):
        return isinstance(node.operand.value, (int, float))
    return False


def _lit_num(node):
    """把数字字面量节点取值（含带符号写法）；取不到返回 None。"""
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) \
            and not isinstance(node.value, bool):
        return float(node.value)
    if (isinstance(node, ast.UnaryOp)
            and isinstance(node.op, (ast.USub, ast.UAdd))
            and isinstance(node.operand, ast.Constant)
            and isinstance(node.operand.value, (int, float))
            and not isinstance(node.operand.value, bool)):
        v = float(node.operand.value)
        return -v if isinstance(node.op, ast.USub) else v
    return None


def _is_registered_cmap(name):
    '''name 是否是 matplotlib 已注册的 colormap 名（'from_list' 这类内建名不算）。'''
    try:
        import matplotlib
        return name in matplotlib.colormaps
    except Exception:                     # noqa: BLE001 - 探测失败就放行，交给运行时验证
        return True


# --------------------------------------------------------------------------
# AST 扫描
# --------------------------------------------------------------------------
def _iter_assign_names(target):
    if isinstance(target, ast.Name):
        yield target.id
    elif isinstance(target, (ast.Tuple, ast.List)):
        for elt in target.elts:
            yield from _iter_assign_names(elt)


def find_fig_var(tree):
    """找 fig 变量名。返回 (name, Assign节点) 或 (None, None)。

    多候选取字面 'fig'；否则取最后一个 figure/subplots 赋值的名字。
    """
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            fn = node.value.func
            if getattr(fn, 'attr', None) in FIG_FACTORIES and node.targets:
                found.extend((nm, node) for nm in _iter_assign_names(node.targets[0]))
    if not found:
        return None, None
    for nm, node in found:
        if nm == 'fig':
            return nm, node
    return found[-1]


def find_anchor(tree, last=False):
    """插入锚点。返回 (kind, node|None)：kind in ('before', 'after', 'tail')。
    last=True 用最后一个 savefig/show（纯 pyplot 多图脚本配 plt.gcf() 用：默认调的是
    最后一张图，块插在最后一个 savefig 之前，gcf() 此刻正是那张图）。"""
    calls = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
             and n.func.attr in ('savefig', 'show', 'tight_layout')]
    calls.sort(key=lambda n: (n.lineno, n.col_offset))
    saves = [n for n in calls if n.func.attr in SAVE_ANCHORS]
    if saves:
        return 'before', (saves[-1] if last else saves[0])
    if calls:                                    # 只有 tight_layout
        return 'after', calls[-1]
    return 'tail', None


def find_savefigs(tree):
    """按源码顺序返回所有 savefig/show 调用节点（多图脚本按图号定位用）。"""
    calls = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
             and n.func.attr in SAVE_ANCHORS]
    calls.sort(key=lambda n: (n.lineno, n.col_offset))
    return calls


def _has_pyplot_plt(tree):
    """脚本是否把 pyplot 导入为 ``plt``（gcf 兜底的前提）。"""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name == 'matplotlib.pyplot' and (a.asname or 'pyplot') == 'plt':
                    return True
        elif isinstance(node, ast.ImportFrom):
            if node.module == 'matplotlib':
                for a in node.names:
                    if a.name == 'pyplot' and (a.asname or 'pyplot') == 'plt':
                        return True
            elif node.module == 'matplotlib.pyplot':
                if any(a.name in ('*', 'gcf', 'figure', 'subplots') for a in node.names):
                    return True
    return False


def count_figures(tree):
    """脚本里 figure/subplots 调用次数（判断多图歧义）。"""
    return sum(1 for n in ast.walk(tree)
               if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
               and n.func.attr in FIG_FACTORIES)


def find_mappable_names(tree):
    """返回 (assigned, cb_args)：
    assigned = ``cs = ax.contourf(...)`` 的变量名集合；
    cb_args  = ``fig.colorbar(<name>, ...)`` 的第一个实参变量名集合。"""
    assigned, cb_args = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            fn = node.value.func
            if getattr(fn, 'attr', None) in MAPPABLE_FACTORIES and node.targets:
                assigned.update(_iter_assign_names(node.targets[0]))
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == 'colorbar' and node.args
                and isinstance(node.args[0], ast.Name)):
            cb_args.add(node.args[0].id)
    return assigned, cb_args


# --------------------------------------------------------------------------
# B2/B3 共用的解析器：轴变量 → 轴序号；轴序号 → mappable；循环守卫
# --------------------------------------------------------------------------
def axis_var_index(tree):
    """解析「轴变量名 → 轴序号(0-based)」。覆盖常见形态（按赋值顺序扫）：

      fig, ax = plt.subplots(...)          → ax → 0
      fig, axes = plt.subplots(1, 2, ...)  → axes 记为"轴序列名"
      ax1, ax2 = axes / axes.flat|ravel|flatten()  → ax1→0, ax2→1
      ax2 = axes[1]                        → ax2 → 1
      ax = fig.add_subplot(...) / fig.add_axes([...]) / plt.subplot(...) → 按创建序
    """
    seq_names = set()
    out: Dict[str, int] = {}
    assigns = [n for n in ast.walk(tree) if isinstance(n, ast.Assign)]
    assigns.sort(key=lambda n: (n.lineno, n.col_offset))

    def _elts(node):
        for t in node.targets:
            if isinstance(t, (ast.Tuple, ast.List)):
                yield list(t.elts)
            else:
                yield [t]

    # ① subplots：第二个目标 = 轴序列（或内联解包）；无 fig 名则全是轴
    for node in assigns:
        v = node.value
        if not (isinstance(v, ast.Call)
                and getattr(v.func, 'attr', None) in FIG_FACTORIES):
            continue
        for elts in _elts(node):
            if len(elts) == 1 and isinstance(elts[0], ast.Name):
                if getattr(v.func, 'attr', None) == 'subplots':
                    out.setdefault(elts[0].id, 0)          # ax = plt.subplots()（罕见）
                continue
            if len(elts) < 2:
                continue
            first = elts[0]
            looks_fig = (isinstance(first, ast.Name)
                         and (first.id.lower().startswith('fig') or first.id == 'f'))
            if looks_fig:
                second = elts[1]
                if isinstance(second, ast.Name):
                    seq_names.add(second.id)               # fig, axes = subplots(...)
                elif isinstance(second, (ast.Tuple, ast.List)):
                    for k, e in enumerate(second.elts):    # fig, (ax1, ax2) = ...
                        if isinstance(e, ast.Name):
                            out.setdefault(e.id, k)
            else:
                for k, e in enumerate(elts):               # ax1, ax2 = subplots(...)
                    if isinstance(e, ast.Name):
                        out.setdefault(e.id, k)
    # ② 解包轴序列：ax1, ax2 = axes / axes.flat / axes.ravel()
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
        for elts in _elts(node):
            for k, e in enumerate(elts):
                if isinstance(e, ast.Name):
                    out.setdefault(e.id, k)
    # ③ 下标取轴：ax2 = axes[1]
    for node in assigns:
        v = node.value
        if (isinstance(v, ast.Subscript) and isinstance(v.value, ast.Name)
                and v.value.id in seq_names and node.targets
                and isinstance(v.slice, ast.Constant)
                and isinstance(v.slice.value, int)):
            for nm in _iter_assign_names(node.targets[0]):
                out.setdefault(nm, v.slice.value)
    # ④ 逐个创建：ax = fig.add_subplot(...) / add_axes([...]) / plt.subplot(...)
    made = 0
    for node in assigns:
        v = node.value
        if not isinstance(v, ast.Call):
            continue
        attr = getattr(v.func, 'attr', None)
        if attr in ('add_subplot', 'add_axes') or getattr(v.func, 'id', None) == 'subplot':
            for nm in _iter_assign_names(node.targets[0]):
                out.setdefault(nm, made)
            made += 1
    return out


def mappable_by_axis(tree):
    """解析「轴序号 → 该轴 mappable 的变量名」（B3：多 mappable 精确配对）。

    两条信号，后者（显式 colorbar）优先级更高：
      cs = ax1.contourf(...)     → 用接收者 ax1 查 axis_var_index
      fig.colorbar(cs, ax=ax1)   → 同时点名 mappable 与父轴，最强信号
    """
    idx_map = axis_var_index(tree)
    out: Dict[int, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            call = node.value
            recv = call.func.value if isinstance(call.func, ast.Attribute) else None
            if (getattr(call.func, 'attr', None) in MAPPABLE_FACTORIES
                    and isinstance(recv, ast.Name) and recv.id in idx_map
                    and node.targets):
                names = list(_iter_assign_names(node.targets[0]))
                if names:
                    out.setdefault(idx_map[recv.id], names[0])
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == 'colorbar' and node.args
                and isinstance(node.args[0], ast.Name)):
            for kw in node.keywords:
                if (kw.arg == 'ax' and isinstance(kw.value, ast.Name)
                        and kw.value.id in idx_map):
                    out[idx_map[kw.value.id]] = node.args[0].id
    return out


def find_enclosing_loop(tree, node):
    """返回包住 node 的最内层 for/while 节点（没有则 None）。"""
    if node is None:
        return None
    best = None
    for n in ast.walk(tree):
        if not isinstance(n, (ast.For, ast.While)):
            continue
        if n.lineno <= node.lineno <= (n.end_lineno or n.lineno):
            if best is None or n.lineno >= best.lineno:
                best = n
    return best


def loop_guard(tree, anchor_node, fig_index):
    """B2：循环出图时推断「只对第 fig_index 张生效」的守卫 → (循环变量, 值)。

    仅在**可判定**时才给：``for i in range(a, b)``（边界是字面量、步长 1）、循环变量是
    普通名字、且循环体内**恰好一处** figure 创建（一次迭代出一张图）。
    判不出返回 (None, None)——绝不瞎猜（宁可统一应用 + 警告）。
    """
    if not isinstance(fig_index, int):
        return None, None
    loop = find_enclosing_loop(tree, anchor_node)
    if not isinstance(loop, ast.For) or not isinstance(loop.target, ast.Name):
        return None, None
    it = loop.iter
    if not (isinstance(it, ast.Call) and isinstance(it.func, ast.Name)
            and it.func.id == 'range'):
        return None, None
    vals = []
    for a in it.args:
        if not (isinstance(a, ast.Constant) and isinstance(a.value, int)):
            return None, None                      # 含变量 → 判不出
        vals.append(a.value)
    if len(vals) == 1:
        start, stop, step = 0, vals[0], 1
    elif len(vals) == 2:
        start, stop, step = vals[0], vals[1], 1
    elif len(vals) == 3:
        start, stop, step = vals[0], vals[1], vals[2]
    else:
        return None, None
    if step != 1:
        return None, None
    made = sum(1 for n in ast.walk(loop)
               if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
               and n.func.attr in FIG_FACTORIES)
    if made != 1:                                  # 一次迭代可能出多张 → 图号对不上
        return None, None
    # fig_index 是"运行期第几张图"; 循环之前已经出过的图要把号扣掉，
    # 否则脚本里前面还有图时守卫会指到错的迭代（前面若也是循环会偏，靠语义验证兜底）。
    prior = sum(1 for n in ast.walk(tree)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr in FIG_FACTORIES and n.lineno < loop.lineno)
    val = start + (fig_index - prior)
    if not (start <= val < stop):
        return None, None
    return loop.target.id, val


# --------------------------------------------------------------------------
# 源码切片（figsize 原位替换）
# --------------------------------------------------------------------------
def _line_starts(src):
    starts = [0]
    for i, ch in enumerate(src):
        if ch == '\n':
            starts.append(i + 1)
    return starts


def _offset(starts, lineno, col, src=None):
    """ast 节点的 col_offset/end_col_offset 是 **UTF-8 字节偏移**（Python 3.8+），
    而 Python 字符串切片按字符。行内有中文等多字节字符时直接相加会错位
    （实测 '(a) 三角函数' 的 fontsize 替换偏 8 个字符 → ax_d.bar 被写成
    ax_d.12r）。传 src 时把行内字节偏移换算成字符偏移。"""
    line_start = starts[lineno - 1]
    if src is None:
        return line_start + col
    nb = 0
    i = line_start
    n = len(src)
    while i < n and nb < col:
        nb += len(src[i].encode('utf-8'))
        i += 1
    return i


def edit_figsize(src, tree, figsize_in, fig_index=None):
    """原位替换第 fig_index 个 subplots()/figure() 调用的 figsize= kwarg 值。

    fig_index 为 int 时定位到**对应图**的调用（多图脚本每张图的画布各自记）；
    越界时用最后一个调用点（循环出图近似）；None 用第一个（单图/旧调用）。
    返回 (new_src, edited)。figsize 是位置参数/缺失时返回原样 + False。
    """
    if not figsize_in or len(figsize_in) < 2:
        return src, False
    starts = _line_starts(src)
    new_txt = '(%s, %s)' % (_num(figsize_in[0]), _num(figsize_in[1]))
    nodes = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
             and n.func.attr in FIG_FACTORIES]
    nodes.sort(key=lambda n: (n.lineno, n.col_offset))
    if not nodes:
        return src, False
    if isinstance(fig_index, int) and 0 <= fig_index < len(nodes):
        node = nodes[fig_index]
    elif isinstance(fig_index, int):
        node = nodes[-1]                     # 越界（循环出图）→ 最后一个调用点
    else:
        node = nodes[0]
    for kw in node.keywords:
        if kw.arg == 'figsize' and kw.value is not None:
            v = kw.value
            s = _offset(starts, v.lineno, v.col_offset, src)
            e = _offset(starts, v.end_lineno, v.end_col_offset, src)
            return src[:s] + new_txt + src[e:], True
    return src, False


# --------------------------------------------------------------------------
# 原位写回：直接改原代码里的数字（不加调整块）—— 用户主推方式
# --------------------------------------------------------------------------
def _find_call(tree, recv_name, attr, after_lineno=0, before_lineno=10 ** 9):
    """找 ``Name(recv_name).attr(...)`` 的调用节点（没有返回 None）。

    ``after_lineno`` / ``before_lineno``：只接受落在 [after, before) 里的节点。
    多图脚本里同一个轴变量名（两张图都用 ``ax``）会在不同 figure 里各出现一次：
    只限下界能避开**上一张图**的同名调用（t1-E2 / t6-B1），但**挡不住下一张图**
    —— 只有上界才挡得住。独立审阅 C2 实测：第一张图的 ``ax`` 没有 set_xlim、
    第二张图有，改第一张图的 xlim 会写到第二张图那一行上（set_title /
    tick_params / grid 同样中招）。
    """
    found = None
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == attr
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == recv_name):
            _ln = getattr(node, 'lineno', 0)
            if _ln < after_lineno or _ln >= before_lineno:
                continue
            if found is None or node.lineno < found.lineno:
                found = node
    return found


def _fig_span(tree, fig_index):
    """第 fig_index 个 figure 的源码区间 [lo, hi)：本图创建行 → 下一张图创建行。"""
    fig_calls = [n for n in ast.walk(tree)
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                 and n.func.attr in FIG_FACTORIES]
    fig_calls.sort(key=lambda n: (n.lineno, n.col_offset))
    if not (isinstance(fig_index, int) and 0 <= fig_index < len(fig_calls)):
        return 0, 10 ** 9
    f0 = fig_calls[fig_index]
    hi = 10 ** 9
    for fc in fig_calls:
        if (fc.lineno, fc.col_offset) > (f0.lineno, f0.col_offset):
            hi = fc.lineno
            break
    return f0.lineno, hi


def _figure_axes_for_index(tree, fig_index):
    """第 fig_index 个 figure 创建调用**所辖**的 add_axes/add_subplot 赋值（按源码序）。

    范围 = 该 figure 调用之后、下一个 figure 调用之前（面板通常紧跟其 figure 创建）。
    这样把轴匹配限定在目标图内，避免多图脚本里把别的图的轴变量（如循环里的 'ax'）
    误配到本图、改到别的图上去。
    """
    fig_calls = [n for n in ast.walk(tree)
                 if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Attribute)
                 and n.func.attr in FIG_FACTORIES]
    fig_calls.sort(key=lambda n: (n.lineno, n.col_offset))
    if not (isinstance(fig_index, int) and 0 <= fig_index < len(fig_calls)):
        return []
    lo, hi = _fig_span(tree, fig_index)
    # colorbar 槽轴（fig.colorbar(..., cax=cax) 的 cax）也是 add_axes 创建的，
    # 但它是 colorbar 轴、params 里 is_colorbar=True；配对"非 colorbar 轴"时必须
    # 剔除，否则数据轴序号会错位（cax 夹在中间时 ax1 会被误配到 cax 的源码）。
    cax_names = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == 'colorbar'):
            for kw in node.keywords:
                if kw.arg == 'cax' and isinstance(kw.value, ast.Name):
                    cax_names.add(kw.value.id)
    out = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Attribute)
                and node.value.func.attr in ('add_axes', 'add_subplot')
                and lo <= node.lineno < hi):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id not in cax_names:
                    out.append((t.id, node.value))
                    break
    out.sort(key=lambda x: (x[1].lineno, x[1].col_offset))
    return out


def apply_inplace(src, tree, data, idx_map=None, overlaps=None):
    """原位写回：直接改原代码里的数字，不插调整块。

    覆盖（按"轴变量名 → 轴序号"精确映射，只动非 colorbar 轴）：
      * ``ax = fig.add_axes([x0, y0, w, h])`` 的 4 个数字
      * ``ax.set_title / set_xlabel / set_ylabel(..., fontsize=N)``
      * ``ax.tick_params(labelsize=N)``
      * ``ax.grid(True/False)``
    其余（clim/cmap/lines/legend/spines、colorbar 轴位置、add_subplot 网格轴的
    pos 等）无法在调用参数里原位表达 → 记入 skipped，由调用方转成警告。

    返回 (new_src, covered, skipped)：
      covered = {轴序号: {字段, ...}}；skipped = [(轴序号, 字段, 目标值)]。

    注意：所有替换先按**原始** src 坐标登记，最后统一按位置从右往左落——
    否则先改的位置会改变后续行的长度，导致后面的坐标错位（实测 fontsize=14 → 116）。
    """
    if idx_map is None:
        idx_map = axis_var_index(tree)
    starts = _line_starts(src)
    edits: List[tuple] = []          # (start, end, 新文本)，坐标基于原始 src
    covered: Dict[int, set] = {}
    skipped: List[tuple] = []
    axes = data.get('axes') or []

    # 重叠检测：同一段源码区间只能被替换一次。
    # 触发场景（t1 审计的 L / blocker）：同一个 figure 里**同名轴变量被 add_axes
    # 赋值多次**（如 `ax2 = fig.add_axes([...])` 出现两回）时，var_for 会把两个轴项
    # 都映射到同一个变量名，于是同一段 `[...]` 被登记两次；落盘按"从右往左"遍历
    # 时第二次替换用的还是原始坐标，会切出 `ax2=fig.add_axes([0.2ojection=...]`
    # 这种语法坏掉的源码。这里直接拒绝重复登记——宁可少改一处，也不写坏代码。
    _conflicts: List[tuple] = []

    # 按 (轴序号, 字段) 记：登记了几次、其中几次是"值没变"的空改。
    # 整字段都空改 → 从 covered 里拿掉：否则 changes 非空会把 auto 模式骗住
    # （它据此决定"要不要退到块模式"），也会把 no_change 谎报成"改了 N 处"。
    _reg: Dict[tuple, int] = {}
    _noop: Dict[tuple, int] = {}

    def _add(node, txt, key=None, value=None):
        """登记一处替换；被重叠检测拒绝时返回 False。

        调用方必须据此决定是否把该字段记进 covered —— 否则会出现
        "声称原位修改了 N 处、磁盘上只落了 N-1 处"（t6-M16）。

        key=(轴序号, 字段名)、value=目标数值时，若原字面量的**数值**已等于目标
        （注意 `0.10` 与 `0.1` 文本不同但数值相同），这处就不登记为改动 ——
        字段是否算"真改到"由 _reg/_noop 的计数在收尾处判定。
        """
        _s = _offset(starts, node.lineno, node.col_offset, src)
        _e = _offset(starts, node.end_lineno, node.end_col_offset, src)
        if key is not None:
            _reg[key] = _reg.get(key, 0) + 1
            _lit = _lit_num(node)
            if (value is not None and _lit is not None
                    and abs(_lit - float(value)) <= 1e-12):
                _noop[key] = _noop.get(key, 0) + 1
                return True
        for _s0, _e0, _t0 in edits:
            if _s < _e0 and _s0 < _e:
                _conflicts.append((_s, _e, txt))
                return False
        edits.append((_s, _e, txt))
        return True

    # ① 目标图（第 fig_index 个 figure 调用）所辖的 add_axes/add_subplot 创建调用。
    #    非 colorbar 轴按 index 序一一对应 —— 限定在当前图内，避免跨图误匹配。
    #    参数没记 fig_index（老参数/单图）时按第 0 张处理。
    _fi = data.get('fig_index')
    axes_calls = _figure_axes_for_index(tree, _fi if isinstance(_fi, int) else 0)
    var_for: Dict[int, str] = {}
    for a, (nm, _call) in zip((x for x in axes if not x.get('is_colorbar')),
                              axes_calls):
        if isinstance(a.get('index'), int):
            var_for[a['index']] = nm
    addaxes: Dict[str, ast.expr] = {}
    for nm, call in axes_calls:
        # 位置字面量两种写法都要认：`add_axes([x0, y0, w, h])`（ast.List）与
        # `add_axes((x0, y0, w, h))`（ast.Tuple）。只认列表时，元组写法会被静默
        # 丢进 skipped —— 用户看到的是"拖了没反应"（外部审阅第 2 条）。
        # 元素的登记/替换按节点源区间做（_add），与容器是 List 还是 Tuple 无关。
        if (isinstance(call.func, ast.Attribute) and call.func.attr == 'add_axes'
                and call.args and isinstance(call.args[0], (ast.List, ast.Tuple))
                and len(call.args[0].elts) == 4):
            addaxes.setdefault(nm, call.args[0])
    # 目标图内第一个轴创建的行号：轴属性（set_title / tick_params / grid）的查找
    # 必须限定在它之后，否则多图脚本复用同名变量时会命中**上一张图**的同名调用。
    # 字段查找必须夹在**本图源码区间**里：只有下界时，后面那张图的同名轴调用
    # 会被误命中（独立审阅 C2 实测把 xlim 写到了第二张图上）。
    _scope_lo, _scope_hi = _fig_span(tree, _fi if isinstance(_fi, int) else 0)
    if axes_calls:
        _scope_lo = min(_scope_lo,
                        min(getattr(c, 'lineno', 0) for _n, c in axes_calls))
    # ② subplots 网格轴：add_axes/add_subplot 序列覆盖不到（subplots(2,2) 不产生
    #    add_axes 节点）。单图脚本里 axis_var_index 的序号就是本图的轴序，补上变量
    #    映射，让 set_title/tick_params/grid 能原位改（pos 仍是网格位置、进 skipped）。
    #    多图脚本怕跨图错配，不补（字段会进 skipped 警告，不静默）。
    if count_figures(tree) <= 1:
        _rev: Dict[int, str] = {}
        for _nm, _i in (idx_map or {}).items():
            _rev.setdefault(_i, _nm)
        for a in axes:
            _i = a.get('index')
            if isinstance(_i, int) and _i not in var_for and _i in _rev:
                var_for[_i] = _rev[_i]
    # ③ colorbar 宿主轴：fig.colorbar(..., ax=<var>) 的 ax 实参变量。宿主轴的位置
    #    由 colorbar(fraction/pad) 在创建时重定位，add_axes 数字重跑时会被覆盖 →
    #    原位改无效（实测 ax_c 拖到 w=0.354，重跑被挤成 0.244）。
    host_names = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == 'colorbar'):
            for kw in node.keywords:
                if kw.arg == 'ax' and isinstance(kw.value, ast.Name):
                    host_names.add(kw.value.id)
    # ④ colorbar fraction 写回：用户拖 colorbar 轴宽度 → fraction（≈ colorbar 宽 /
    #    宿主轴 add_axes 原始宽，fraction 语义就是相对宿主轴的占比）→ 原位改
    #    fig.colorbar(..., fraction=N) 的数字。这样"拖 colorbar 控制主图宽窄"生效；
    #    pad 布局复杂先不动；宿主轴/colorbar 轴的 pos 仍走 ② 的 skipped。
    cb_calls = [n for n in ast.walk(tree)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr == 'colorbar']
    cb_calls.sort(key=lambda n: (n.lineno, n.col_offset))
    cb_param = sorted((a for a in axes if a.get('is_colorbar')
                       and isinstance(a.get('index'), int)),
                      key=lambda a: a['index'])
    for _call, _pa in zip(cb_calls, cb_param):
        _host = next((kw.value.id for kw in _call.keywords
                      if kw.arg == 'ax' and isinstance(kw.value, ast.Name)),
                     None)
        if not _host:
            continue
        _w0_list = addaxes.get(_host)
        _pos = _pa.get('pos') or [None] * 4
        _cw = _pos[2]
        _frac = next((kw for kw in _call.keywords
                      if kw.arg == 'fraction' and isinstance(kw.value, ast.Constant)),
                     None)
        if (_w0_list is not None and len(_w0_list.elts) == 4
                and isinstance(_w0_list.elts[2], ast.Constant)
                and isinstance(_cw, (int, float)) and _cw > 0
                and _frac is not None):
            _w0 = _w0_list.elts[2].value
            if isinstance(_w0, (int, float)) and _w0 > 0:
                _fr = _cw / _w0
                if 0 < _fr < 1:
                    if _add(_frac.value, _num(_fr), (_pa['index'], 'fraction'), _fr):
                        covered.setdefault(_pa['index'], set()).add('fraction')

    for a in axes:
        i = a.get('index')
        if not isinstance(i, int):
            continue
        nm = var_for.get(i)
        pos = a.get('pos')
        # ② 轴位置：add_axes 的 [x0,y0,w,h]（colorbar 轴 / add_subplot 网格轴改不了）
        if pos and a.get('is_colorbar'):
            skipped.append((i, 'pos', pos))
        elif pos and nm is not None and nm in host_names:
            skipped.append((i, 'pos（该轴是 colorbar 的宿主，位置由 colorbar 决定，'
                               '保持原样；拖 colorbar 轴可调，或 --style block）', pos))
        elif pos and nm is not None:
            lst = addaxes.get(nm)
            # 字面量判定要认带符号写法（-0.01 → UnaryOp）：见 _is_num_literal
            if lst is not None and all(_is_num_literal(e) for e in lst.elts):
                # 四个数字要**全部**登记成功才算改到（all() 会短路，不能直接用）
                _oks = [_add(lst.elts[k], _num(pos[k]), (i, 'pos'), pos[k])
                        for k in range(4)]
                if all(_oks):
                    covered.setdefault(i, set()).add('pos')
                else:
                    skipped.append((i, 'pos（替换位置重叠，已跳过）', pos))
            else:
                skipped.append((i, 'pos', pos))
        if nm is None:
            # 没有变量映射（多图 subplots 网格轴等）→ 这些字段也进 skipped，
            # 避免"拖了字号却没写回也无提示"的静默丢失。
            # pos 同样要记：否则网格轴的拖动是 100% 静默丢弃（t1-P / t6-M3）。
            if pos:
                skipped.append((i, 'pos', pos))
            for field in ('title_fontsize', 'label_fontsize', 'tick_fontsize',
                          'xlim', 'ylim'):
                if a.get(field) is not None:
                    skipped.append((i, field, a[field]))
            if a.get('grid') is not None:
                skipped.append((i, 'grid', a['grid']))
            continue
        # ③ 字号：set_title / set_xlabel / set_ylabel 的 fontsize kwarg
        for attr, field in (('set_title', 'title_fontsize'),
                            ('set_xlabel', 'label_fontsize'),
                            ('set_ylabel', 'label_fontsize')):
            val = a.get(field)
            if val is None:
                continue
            node = _find_call(tree, nm, attr, _scope_lo, _scope_hi)
            kw = None
            if node is not None:
                kw = next((k for k in node.keywords if k.arg == 'fontsize'), None)
            if kw is not None and isinstance(kw.value, ast.Constant):
                if _add(kw.value, _num(val), (i, field), val):
                    covered.setdefault(i, set()).add(field)
                else:
                    skipped.append((i, field + '（替换位置重叠，已跳过）', val))
            elif field not in covered.get(i, ()):
                skipped.append((i, field, val))
        # ④ tick_params(labelsize=)
        tf = a.get('tick_fontsize')
        if tf is not None:
            node = _find_call(tree, nm, 'tick_params', _scope_lo, _scope_hi)
            kw = None
            if node is not None:
                kw = next((k for k in node.keywords if k.arg == 'labelsize'), None)
            if kw is not None and isinstance(kw.value, ast.Constant):
                if _add(kw.value, _num(tf), (i, 'tick_fontsize'), tf):
                    covered.setdefault(i, set()).add('tick_fontsize')
                else:
                    skipped.append((i, 'tick_fontsize（替换位置重叠，已跳过）', tf))
            else:
                skipped.append((i, 'tick_fontsize', tf))
        # ⑤ grid：ax.grid(True/False)
        g = a.get('grid')
        if g is not None:
            node = _find_call(tree, nm, 'grid', _scope_lo, _scope_hi)
            if (node is not None and node.args
                    and isinstance(node.args[0], ast.Constant)):
                if _add(node.args[0], 'True' if bool(g) else 'False',
                         (i, 'grid'), 1.0 if bool(g) else 0.0):
                    covered.setdefault(i, set()).add('grid')
                else:
                    skipped.append((i, 'grid（替换位置重叠，已跳过）', g))
            else:
                skipped.append((i, 'grid', g))
        # ⑤b 轴范围 set_xlim / set_ylim（外部审阅第 2 条：这类常见写法原先完全不在
        #     覆盖里）。三种写法都认：
        #       ax.set_xlim(a, b)          → 改两个位置参数
        #       ax.set_xlim((a, b)) / [..] → 改容器里的两个元素（元组列表都认）
        #       ax.set_xlim(xmin=a, xmax=b) → 改两个关键字
        #     找不到对应调用（范围来自 autoscale / set_xlim 在别处）→ 进 skipped，
        #     由 auto 模式决定是否退到块模式（块里会带 set_xlim）。
        for attr, field, kws in (('set_xlim', 'xlim', ('xmin', 'xmax')),
                                 ('set_ylim', 'ylim', ('ymin', 'ymax'))):
            rng = a.get(field)
            if not (isinstance(rng, (list, tuple)) and len(rng) == 2):
                continue
            node = _find_call(tree, nm, attr, _scope_lo, _scope_hi)
            dst = None
            if node is not None:
                if (len(node.args) == 2
                        and all(_is_num_literal(v) for v in node.args)):
                    dst = [(node.args[0], _num(rng[0]), rng[0]),
                           (node.args[1], _num(rng[1]), rng[1])]
                elif (len(node.args) == 1
                      and isinstance(node.args[0], (ast.List, ast.Tuple))
                      and len(node.args[0].elts) == 2
                      and all(_is_num_literal(v)
                              for v in node.args[0].elts)):
                    dst = [(node.args[0].elts[0], _num(rng[0]), rng[0]),
                           (node.args[0].elts[1], _num(rng[1]), rng[1])]
                else:
                    _kw = {k.arg: k for k in node.keywords if k.arg in kws}
                    if all(k in _kw and _is_num_literal(_kw[k].value)
                           for k in kws):
                        dst = [(_kw[kws[0]].value, _num(rng[0]), rng[0]),
                               (_kw[kws[1]].value, _num(rng[1]), rng[1])]
            if dst and all(_add(nd, tx, (i, field), val)
                           for nd, tx, val in dst):
                covered.setdefault(i, set()).add(field)
            elif field not in covered.get(i, ()):
                skipped.append((i, field, rng))
        # ⑥ 其余无法原位表达的字段（有目标值时记 skipped）
        for field in ('clim', 'cmap', 'lines', 'legend', 'spines'):
            v = a.get(field)
            if v in (None, {}, []):
                continue
            if field not in covered.get(i, ()):
                skipped.append((i, field, v))
        # ⑥b xscale/yscale：非 linear 时原位写回也没有对应写法（只能进块），
        # 原先一声不响 —— 用户设了 log 轴又调刻度，看不出"没生效"（t1-C / t6-M2）。
        if not a.get('is_colorbar'):
            for _f in ('xscale', 'yscale'):
                _v = a.get(_f)
                if _v and _v != 'linear' and _f not in covered.get(i, ()):
                    skipped.append((i, _f, _v))
    # 整字段都是"值没变"的空改 → 不算改到（否则 auto 模式会以为有改动落地而
    # 不退到块模式，网格轴图里用户拖的位置就落不了地；重复 apply 也会谎报改动）
    for _key, _n in _reg.items():
        if _n and _noop.get(_key, 0) == _n:
            _i, _f = _key
            if _i in covered:
                covered[_i].discard(_f)
    # 按原始位置**从右往左**落：后面的替换不影响前面位置，偏移永不失效
    edits.sort(key=lambda x: -x[0])
    out = src
    for s, e, t in edits:
        out = out[:s] + t + out[e:]
    if overlaps is not None:
        overlaps.extend(_conflicts)
    return out, covered, skipped


def _backup_path(script, params_path, pid=None):
    """备份文件放 `.tweak_params/` 里，不散落在代码目录。

    pid=None → 固定名 `<stem>.tweak.bak`：README、测试和用户习惯都依赖它，
    代表"最近一次备份"。
    传 pid → `<stem>.tweak.<pid>.bak`：本次专属副本。并发跑两个 apply 时固定名
    会被后写者覆盖（t5-S4 / t6-B2 实测：出事后唯一的恢复点已经不可信），所以
    再留一份谁也覆盖不了的。
    """
    d = os.path.dirname(os.path.abspath(params_path))
    stem = os.path.splitext(os.path.basename(script))[0]
    if pid is None:
        return os.path.join(d, stem + '.tweak.bak')
    return os.path.join(d, '%s.tweak.%s.bak' % (stem, pid))


def _prune_backups(script, params_path, keep=5):
    """只保留最近 keep 份带 pid 的备份；固定名那份不动。"""
    d = os.path.dirname(os.path.abspath(params_path))
    stem = os.path.splitext(os.path.basename(script))[0]
    pat = re.compile(r'^%s\.tweak\.(\d+)\.bak$' % re.escape(stem))
    try:
        files = [os.path.join(d, n) for n in os.listdir(d) if pat.match(n)]
    except OSError:
        return
    files.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    for old in files[keep:]:
        try:
            os.remove(old)
        except OSError:
            pass


def _make_backup(script, params_path, keep=5):
    """写备份并返回固定名路径；失败抛 OSError。

    写两份：固定名（最近一次，兼容既有约定）+ 带 pid 的专属副本（并发时不互相
    覆盖），并把旧的 pid 副本裁到最近 keep 份。
    """
    fixed = _backup_path(script, params_path)
    os.makedirs(os.path.dirname(fixed), exist_ok=True)
    shutil.copy2(script, fixed)
    try:
        shutil.copy2(script, _backup_path(script, params_path, os.getpid()))
    except OSError:
        pass                     # 专属副本是加强项，失败不影响主流程
    _prune_backups(script, params_path, keep)
    return fixed


def _pid_alive(pid):
    """进程是否还在。

    Windows 上**不能**用 os.kill(pid, 0) 探活 —— 那会真的去终止目标进程。
    这里改用 OpenProcess + GetExitCodeProcess 只读查询。
    """
    if not pid:
        return False
    if os.name == 'nt':
        try:
            import ctypes
            k = ctypes.windll.kernel32
            h = k.OpenProcess(0x1000, False, int(pid))   # QUERY_LIMITED_INFORMATION
            if not h:
                return False
            try:
                code = ctypes.c_ulong()
                if k.GetExitCodeProcess(h, ctypes.byref(code)):
                    return code.value == 259              # STILL_ACTIVE
                return True
            finally:
                k.CloseHandle(h)
        except Exception:                                 # noqa: BLE001
            return True                                   # 查不出来就当成活着，稳妥
    try:
        os.kill(pid, 0)
    except OSError as e:
        return getattr(e, 'errno', None) == errno.EPERM
    return True


def _lock_holder(lock):
    try:
        with open(lock, 'r', encoding='utf-8') as f:
            return int((f.read() or '').strip() or 0) or None
    except (OSError, ValueError):
        return None


@contextlib.contextmanager
def script_lock(script, lock_dir=None, timeout=10.0, poll=0.2):
    """同一脚本的写回互斥锁。

    并发跑两个 `apply --write` 时，两边都基于同一份原始内容算替换，后写者盖掉
    先写者，且都会打印"✓ 已写回"（t5-S4 实测 7/7 一份改动凭空消失）。

    用 O_EXCL 原子创建 `<stem>.apply.lock` 并写入自己的 pid：
      - 锁被活着的进程持有 → 短暂等待（并发多是"两个命令几乎同时起"），
        超过 timeout 抛 RuntimeError；
      - 持锁进程已死（上次崩了留下的陈旧锁）→ 删掉重来。
    """
    lock = os.path.join(lock_dir or os.path.join(
        os.path.dirname(os.path.abspath(script)), '.tweak_params'),
        os.path.splitext(os.path.basename(script))[0] + '.apply.lock')
    os.makedirs(os.path.dirname(lock), exist_ok=True)
    deadline = time.time() + max(0.0, timeout)
    while True:
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            holder = _lock_holder(lock)
            if holder is None or not _pid_alive(holder):
                try:
                    os.remove(lock)          # 陈旧锁 → 清掉重试
                except OSError:
                    pass
                continue
            if time.time() >= deadline:
                raise RuntimeError(
                    '另一个 mpltweak 写回正在进行（pid=%s）。若确认它已不在运行，'
                    '请手动删除 %s' % (holder, lock))
            time.sleep(poll)
            continue
        except OSError:
            raise
        else:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                f.write(str(os.getpid()))
            break
    try:
        yield lock
    finally:
        try:
            os.remove(lock)
        except OSError:
            pass


def _atomic_write(path, text, encoding='utf-8'):
    """原子地把 text 写进 path。

    直接 `open(path, 'w')` 会**立即把目标截断为 0 字节**；进程若恰好在
    "已截断、还没写完"的窗口里消失（断电 / OOM / taskkill），用户的脚本就
    永久变成空文件 —— 而本项目对外的承诺是"绝不把改坏的脚本留在磁盘上"。
    这里先写同目录临时文件，再用 os.replace 原子替换：同一文件系统内
    os.replace 是原子操作，目标文件要么是旧内容、要么是新内容，不会半截。

    newline='' 保持文本原样：不传的话 Python 会把 '\\n' 翻译成 os.linesep，
    在 Windows 上等于把**整个文件**的换行符悄悄改成 CRLF。
    """
    d = os.path.dirname(os.path.abspath(path)) or '.'
    # os.replace 换的是 inode，新文件权限来自 mkstemp（0600）。不显式恢复的话，
    # 用户脚本的权限位会被改窄（t6-M18）。注意：硬链接在替换后仍指向旧 inode
    # —— 这是"原子替换"的固有代价，用 os.replace 就换不回硬链接，已在 README 说明。
    try:
        _mode = os.stat(path).st_mode & 0o7777
    except OSError:
        _mode = None
    fd, tmp = tempfile.mkstemp(prefix='.mpltweak-', suffix='.tmp', dir=d)
    try:
        with os.fdopen(fd, 'w', encoding=encoding, newline='') as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())               # 先落盘，再替换
        if _mode is not None:
            try:
                os.chmod(tmp, _mode)
            except OSError:
                pass
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _rollback_note(backup, script, expect=None):
    """把备份拷回脚本，返回一句可直接写进结论的说明。

    成功 → '已回滚'；失败 → 如实说明"回滚也失败了"并给出备份路径。

    必须这么做的原因：这里过去是 `except OSError: pass`，回滚失败被吞掉，
    而提示语照旧写"（已回滚）"—— 用户以为安全了，磁盘上却仍是改坏的脚本。
    结论必须与磁盘的真实状态一致。

    ``expect`` 是我们刚写进去的文本。若磁盘上的内容已经和它不同，说明用户
    （或编辑器）在验证期间改过这个文件 —— 这时**不能**拿备份整体覆盖，那会把
    用户刚写的改动一起抹掉而且只字不提（t5-S5 / t6-M13）。改为保留现状 + 告知。
    """
    if expect is not None:
        try:
            _now, _ = params.read_text(script)
        except (OSError, UnicodeDecodeError):
            _now = None
        if _now is not None and _now != expect:
            return ('检测到脚本在验证期间被外部修改，已跳过自动回滚以免抹掉你的改动；'
                    '备份在 %s' % backup)
    try:
        shutil.copy2(backup, script)
        return '已回滚'
    except OSError as e:
        return ('回滚也失败了（%s: %s）—— 脚本目前是改坏的状态，'
                '备份在 %s，请手动恢复' % (type(e).__name__, e, backup))


def _writeback_inplace(script, src, tree, data, params_path, verify,
                       python, timeout, dry_run, semantic, encoding='utf-8',
                       fast_check=False):
    """原位写回主流程：改数字 → 备份 → 写文件 → Agg + 语义验证（只比被覆盖字段）。"""
    res: Dict[str, Any] = {'reason': FAIL, 'changes': [], 'block': None,
                           'backup': None, 'warnings': [], 'verified': None,
                           'err': '', 'style': 'inplace'}
    warnings: List[str] = []
    figsize_in = data.get('figsize_in')
    # 多图脚本但参数没带 fig_index 时，"改哪张图的画布尺寸"无从确定：edit_figsize
    # 会退化成改第 1 张 —— 那正是"写错对象图"（t1-A / t6-M1）。宁可不改并说明。
    _n_fig = count_figures(tree)
    _fig_idx = data.get('fig_index')
    _fs_ambiguous = bool(figsize_in) and _n_fig > 1 \
        and not isinstance(_fig_idx, int)
    new_src, figsize_edited = (
        (src, False) if _fs_ambiguous
        else (edit_figsize(src, tree, figsize_in, _fig_idx)
              if figsize_in else (src, False)))
    if _fs_ambiguous:
        warnings.append('脚本有 %d 张图但参数没有 fig_index，无法确定该改哪张的'
                        '画布尺寸 → 保持原样；请用 describe --fig N 重新导出，'
                        '或手动改' % _n_fig)
    if figsize_edited:
        # figsize 原位替换改变了文件长度 → 原 tree 的节点坐标全部失效 →
        # 重新 parse，让 apply_inplace 的新坐标与新文件对齐（否则后续替换错位）。
        try:
            tree = ast.parse(new_src)
        except SyntaxError:                       # 理论上不会，稳妥回退
            figsize_edited = False
            new_src, tree = src, ast.parse(src)
    if figsize_in and not figsize_edited:
        warnings.append('画布尺寸保持原样（代码里 figsize 是位置参数或没有 '
                        'figsize=）；用 --style block 或手动改')
    _overlaps: List[tuple] = []
    in_src, covered, skipped = apply_inplace(new_src, tree, data,
                                             overlaps=_overlaps)
    if _overlaps:
        warnings.append('有 %d 处替换位置重叠，已跳过以免写坏源码（同一个 figure 里'
                        '同名轴变量被 add_axes 赋值多次时会出现）；涉及的位置保持'
                        '原样，可用 --style block 或手动改' % len(_overlaps))
    changes = ['ax%d.%s' % (i, f) for i in sorted(covered)
               for f in sorted(covered[i])]
    # 无法原位写回、但参数里有目标值的项 → 聚合成警告（不静默）
    if skipped:
        by_axis: Dict[int, set] = {}
        for i, f, _v in skipped:
            by_axis.setdefault(i, set()).add(f)
        for i in sorted(by_axis):
            warnings.append('ax%d 未原位应用：%s（原位写回只改代码里已有的数字，'
                            '这些项代码里没有对应写法，保持原样；需要可用 '
                            '--style block 或手动）'
                            % (i, '、'.join(sorted(by_axis[i]))))
    if dry_run:
        res.update({'reason': OK, 'changes': changes,
                    'block': ('原位修改：' + ', '.join(changes)) if changes
                    else '（无改动）',
                    'warnings': warnings, 'backup': None,
                    'figsize_edited': figsize_edited, 'anchor': 'inplace'})
        return res
    if in_src == src and not changes:
        if skipped:
            warnings.append('本图轴是 subplots 网格位置 / 无对应写法，位置与字号'
                            '保持原样；用 --style block 或手动改')
            res.update({'reason': 'best_effort', 'changes': changes,
                        'warnings': warnings, 'figsize_edited': figsize_edited,
                        'anchor': 'inplace'})
            return res
        warnings.append('参数与原代码一致，无需改动')
        res.update({'reason': 'no_change', 'changes': changes,
                    'warnings': warnings, 'figsize_edited': figsize_edited,
                    'anchor': 'inplace'})
        return res
    try:
        backup = _make_backup(script, params_path)
    except OSError as e:
        res['err'] = '备份失败: %s' % e
        return res
    res['backup'] = backup
    try:
        _atomic_write(script, in_src, encoding=encoding)
    except OSError as e:
        res['err'] = '写文件失败: %s（%s）' % (e, _rollback_note(backup, script))
        return res
    if fast_check and not verify:
        # 快速验证档（--verify-fast）：**不重跑**，只做静态检查。
        # 它的价值在于源码手术最容易坏的地方（缩进/括号/引号）是**免费**可查的，
        # 坏在这里当场回滚，比"明明写坏了却报成功"强得多。
        # 但它给出的是**弱保证**：脚本能不能跑通、布局有没有真的落到目标图上，
        # 它一概不知道 —— 所以结论里必须原话说清，别让用户误以为"验证过了"。
        try:
            ast.parse(in_src)
            res['verified'] = None
            res['verify_mode'] = 'fast'
        except SyntaxError as e:
            res.update({'reason': FAIL, 'verify_mode': 'fast',
                        'err': '快速验证失败：写回后的源码语法错误（%s）：%s'
                               % (_rollback_note(backup, script, expect=in_src), e),
                        'verified': False})
            return res
    if verify:
        vok, verr = verify_run(script, python, timeout)
        res['verified'] = vok
        if vok is True and semantic:
            if covered:
                # 原位覆盖了字段 → 语义只比对被覆盖的字段。**每个轴**都要在
                # fields_by_axis 里有条目（未覆盖的轴给空集 = 什么都不比）——
                # 否则 compare 会把没列出的轴当"全字段比对"，colorbar 等未覆盖
                # 轴的 clim/位置会误报不一致而回滚。
                fba = {}
                for _a in data.get('axes') or []:
                    _i = _a.get('index')
                    if isinstance(_i, int):
                        fba[_i] = set(covered.get(_i, ()))
                sok, sdetail = _verify.verify_semantic(
                    script, data, python, timeout, fields_by_axis=fba,
                    figsize_edited=figsize_edited)
                res['semantic'] = sok
                if sok is False:
                    res.update({'reason': FAIL,
                                'err': '语义验证不一致（%s）：%s'
                                       % (_rollback_note(backup, script,
                                                         expect=in_src),
                                          sdetail),
                                'verified': True, 'semantic': False})
                    return res
                if sok is None:
                    warnings.append('语义验证未取到目标图状态（%s），'
                                    '本次仅按"能跑通"判定' % sdetail)
            else:
                # 原位没覆盖任何轴字段（如 add_subplot 网格轴 / colorbar 轴位置）：
                # 位置本就没被改，语义比对必然失败无意义 → 跳过，Agg 跑通即可。
                res['semantic'] = None
                warnings.append('本图没有可原位写回的轴属性（轴是 add_subplot 网格位置 / '
                                'colorbar / 无对应调用），只应用了画布尺寸；'
                                '位置/字号请用 --style block 或手动改代码')
        elif vok is False:
            res.update({'reason': FAIL,
                        'err': '写回后脚本跑不通（%s）：%s'
                               % (_rollback_note(backup, script,
                                                 expect=in_src),
                                  (verr or '').strip()[:300]),
                        'verified': False})
            return res
        elif vok is None:
            warnings.append('验证超时（>%ss），未能自动验证；脚本保持写回后的状态，'
                            '请手动重跑确认' % timeout)
    res.update({'reason': BEST_EFFORT if warnings else OK,
                'changes': changes, 'block': in_src, 'warnings': warnings,
                'fig_var': None, 'gcf_fallback': False,
                'figsize_edited': figsize_edited, 'anchor': 'inplace'})
    return res


# --------------------------------------------------------------------------
# 调整块生成
# --------------------------------------------------------------------------
def _mappable_ref(ax_expr, axis_item, is_cb, mappables, cb_args, mappable_map=None,
                  multi_fig=False):
    """返回 (mappable 引用表达式 | None, warning | None)。

    优先级：
      ① colorbar 轴 → ``ax._colorbar.mappable`` 结构导航（不依赖变量名、不依赖 draw）
      ② **本轴精确配对**（B3：``cs = ax1.contourf(...)`` 的接收者 /
         ``fig.colorbar(cs, ax=ax1)`` 的显式父轴）
      ③ 全局 mappable 变量（**仅单图脚本**；多图时全局集合可能混着别的图的变量，
         拿它兜底会把别的图的 mappable 打上 set_clim/set_cmap → 跨图污染）
      ④ ``collections[0]`` 兜底（结构上就是本轴的，不会跨图）+ 警告
    """
    if is_cb:
        # colorbar 轴：走 ax._colorbar.mappable（colorbar 创建时即设置，不依赖首次
        # draw；images[0].colorbar 在 draw 之前为空，不能用作结构导航）
        return '%s._colorbar.mappable' % ax_expr, None
    i = axis_item.get('index')
    if mappable_map and isinstance(i, int) and i in mappable_map:
        return mappable_map[i], None
    if not multi_fig and mappables:
        nm = sorted(mappables)[0]
        if len(mappables) > 1:
            return nm, ('ax%s 存在多个 mappable 变量(%s)，已用 %s，请人工确认其它轴'
                        % (i, ','.join(sorted(mappables)), nm))
        return nm, None
    return ('%s.collections[0]' % ax_expr,
            'ax%s 的 mappable 未在赋值中解析到，用 %s.collections[0] 兜底，请人工确认'
            % (i, ax_expr))


def render_block(data, fig_var, mappables, cb_args, figsize_in=None,
                 mappable_map=None, tag=None, multi_fig=False):
    """从参数数据生成纯 matplotlib 调整块文本。返回 (text, warnings)。"""
    axes = data.get('axes', [])
    warnings: List[str] = []
    out = [BLOCK_START]
    # 说明行跟随界面语言（哨兵本身固定 ASCII，见 BLOCK_START 处的说明）
    out.append(_msg.t('block_comment', ts=time.strftime('%Y-%m-%d %H:%M')))
    if tag:
        out.append('# ' + tag)          # 多图会话：标明本块属于哪张图（兜底定位用）
    # 轴身份线索：下面所有 `fig.axes[i]` 都是**下标寻址**，脚本一改（加 colorbar、
    # 条件分支少建一个轴）下标就可能漂。把调图时的网格身份写进注释，出问题时人一眼
    # 能看出"这个下标原本是哪个面板"；真正的兜底是写回后的语义验证 —— 它会把 cell
    # 身份回比一遍，不匹配就直接回滚（verify.compare 里的 cell 比对）。
    _cells = [(a.get('index'), a.get('cell')) for a in axes if a.get('cell')]
    if _cells:
        out.append('# 轴身份（调图时）：%s'
                   % '，'.join('ax%s=网格%s' % (i, c) for i, c in _cells))
    if figsize_in:
        out.append('%s.set_size_inches(%s, %s)'
                   % (fig_var, _num(figsize_in[0]), _num(figsize_in[1])))
    # 1) 位置：zip 循环（fig.axes 顺序 = 参数顺序，逐轴 set_position）
    poss = [a.get('pos') for a in axes if a.get('pos') is not None]
    if poss and len(poss) < len(axes):
        warnings.append('参数里 %d 个轴有位置、共 %d 个轴：zip 只设置前 %d 个'
                        '（其余轴位置未覆盖，脚本轴数与调图时不一致？）'
                        % (len(poss), len(axes), len(poss)))
    if poss:
        out.append('for _ax, _p in zip(%s.axes, [' % fig_var)
        for p in poss:
            out.append('    [%s],' % ', '.join(_fmt_pos(v) for v in p))
        out.append(']):')
        out.append('    _ax.set_position(_p)')
    # 2) 逐轴属性
    for a in axes:
        i = a.get('index')
        if i is None:
            continue
        ax = '%s.axes[%d]' % (fig_var, i)
        is_cb = bool(a.get('is_colorbar'))
        if is_cb:
            out.append('# colorbar 轴：先解除自动定位/盒比，否则重绘会重置位置与宽度')
            out.append('%s.set_axes_locator(None)' % ax)
            out.append('%s.set_box_aspect(None)' % ax)
        if a.get('title_fontsize') is not None:
            out.append('%s.title.set_fontsize(%s)' % (ax, _num(a['title_fontsize'])))
        if a.get('label_fontsize') is not None:
            out.append('%s.xaxis.label.set_fontsize(%s)' % (ax, _num(a['label_fontsize'])))
            out.append('%s.yaxis.label.set_fontsize(%s)' % (ax, _num(a['label_fontsize'])))
        if a.get('tick_fontsize') is not None:
            out.append('%s.tick_params(labelsize=%s)' % (ax, _num(a['tick_fontsize'])))
        if a.get('grid') is not None:
            out.append('%s.grid(%s)' % (ax, 'True' if a['grid'] else 'False'))
        # 轴范围：块是"源码里没有 set_xlim 写法"时的兜底，所以这里必须带上，
        # 否则 --style block 会静默丢掉用户设的范围（grid 之后、spines 之前）。
        for field, meth in (('xlim', 'set_xlim'), ('ylim', 'set_ylim')):
            rng = a.get(field)
            if isinstance(rng, (list, tuple)) and len(rng) == 2:
                out.append('%s.%s(%s, %s)' % (ax, meth, _num(rng[0]), _num(rng[1])))
        for k, vis in (a.get('spines') or {}).items():
            if not vis:
                out.append('%s.spines[%r].set_visible(False)' % (ax, k))
        # 只认 log 切换：colorbar 轴的 function 刻度等内部刻度一律不碰——
        # 裸调 set_xscale('function') 运行时会报错（缺 functions 实参）
        if not is_cb and a.get('xscale') == 'log':
            out.append('%s.set_xscale(%r)' % (ax, 'log'))
        if not is_cb and a.get('yscale') == 'log':
            out.append('%s.set_yscale(%r)' % (ax, 'log'))
        for line in a.get('lines', []):
            j = line.get('index')
            if j is None:
                continue
            if line.get('linewidth') is not None:
                out.append('%s.lines[%d].set_linewidth(%s)'
                           % (ax, j, _num(line['linewidth'])))
            if line.get('color'):
                out.append('%s.lines[%d].set_color(%r)' % (ax, j, line['color']))
        if a.get('clim') is not None or a.get('cmap'):
            mref, warn = _mappable_ref(ax, a, is_cb, mappables, cb_args,
                                       mappable_map, multi_fig)
            if warn:
                warnings.append(warn)
            if a.get('clim') is not None and mref:
                out.append('%s.set_clim(%s, %s)'
                           % (mref, _num(a['clim'][0]), _num(a['clim'][1])))
            if a.get('cmap') and mref:
                if _is_registered_cmap(a['cmap']):
                    out.append('%s.set_cmap(%r)' % (mref, a['cmap']))
                else:
                    warnings.append('ax%s 的 cmap %r 不是已注册 colormap'
                                    '（ListedColormap 之类返回 from_list 名），已跳过'
                                    % (i, a['cmap']))
        lg = a.get('legend')
        if lg:
            loc = lg.get('loc')
            anchor = lg.get('anchor')
            fs = ', fontsize=%s' % _num(lg['fontsize']) if lg.get('fontsize') else ''
            if loc:
                out.append('%s.legend(loc=%r%s)' % (ax, loc, fs))
            elif anchor:
                out.append('%s.legend(bbox_to_anchor=(%s, %s)%s)'
                           % (ax, _num(anchor[0]), _num(anchor[1]), fs))
    out.append(BLOCK_END)
    return '\n'.join(out) + '\n', warnings


# --------------------------------------------------------------------------
# 幂等插入/替换 + 侧边元数据
# --------------------------------------------------------------------------
def _meta_path(params_path):
    d = os.path.dirname(params_path)
    stem = os.path.splitext(os.path.basename(params_path))[0]
    return os.path.join(d, stem + '.writeback.json')


def _block_spans(lines):
    """返回文件里所有调整块的 (start, end) 1-based 区间（按出现顺序）。"""
    spans, start = [], None
    for idx, line in enumerate(lines):
        if any(s in line for s in _ALL_STARTS):
            start = idx + 1
        elif any(e in line for e in _ALL_ENDS) and start is not None:
            spans.append((start, idx + 1))
            start = None
    return spans


def _existing_span(lines, meta_path, tag=None):
    """找**本参数文件对应的**已有调整块区间 (start, end) 1-based；没有返回 None。

    多图会话里一个脚本会有多个调整块（每个改过的图一个），所以"属于谁"必须能区分：
      ① 侧边元数据（且首尾是哨兵、且含本次的 tag）——最可靠；
      ② 兜底按哨兵扫描：**只在能唯一定位时**才认（带 tag 就找含该 tag 的块，
         没有 tag 就要求全文件只有一块）。定位不了就返回 None（宁可插新块，
         也绝不误替换别的图的块）。
    """
    if meta_path and os.path.exists(meta_path):
        try:
            with open(meta_path, 'r', encoding='utf-8') as f:
                m = json.load(f)
            s, e = m.get('span')
            if (isinstance(s, int) and isinstance(e, int)
                    and 1 <= s <= e <= len(lines)
                    and any(x in lines[s - 1] for x in _ALL_STARTS)
                    and any(x in lines[e - 1] for x in _ALL_ENDS)
                    and (not tag or any(tag in ln for ln in lines[s - 1:e]))):
                return s, e
        except Exception:
            pass
    spans = _block_spans(lines)
    if tag:
        hit = [(s, e) for (s, e) in spans
               if any(tag in ln for ln in lines[s - 1:e])]
        return hit[0] if len(hit) == 1 else None
    return spans[0] if len(spans) == 1 else None


def _reindent(block, indent):
    """给整块加缩进（锚点嵌在循环/if 里时，块必须同缩进才不破坏语法）。"""
    if not indent:
        return block
    return ''.join((indent + ln) if ln.strip() else ln
                   for ln in block.splitlines(keepends=True))


def insert_or_replace(src, block, anchor, meta_path, tag=None):
    """把 block 插入 src：已有调整块则整体替换，否则按锚点插入（缩进对齐锚点行）。
    返回 (new_src, new_span)。"""
    lines = src.splitlines(keepends=True)
    span = _existing_span(lines, meta_path, tag)
    if span is not None:
        s, e = span
        old = lines[s - 1]
        block = _reindent(block, old[:len(old) - len(old.lstrip())])
        new_src = ''.join(lines[:s - 1]) + block + ''.join(lines[e:])
        new_span = (s, s + len(block.splitlines()) - 1)
    else:
        if anchor[0] == 'before':
            idx = anchor[1].lineno - 1
            indent = ' ' * anchor[1].col_offset
        elif anchor[0] == 'after':
            idx = anchor[1].end_lineno
            indent = ' ' * anchor[1].col_offset
        else:
            idx, indent = len(lines), ''
        block = _reindent(block, indent)
        new_src = ''.join(lines[:idx]) + block + ''.join(lines[idx:])
        new_span = (idx + 1, idx + len(block.splitlines()))
    if meta_path:
        try:
            os.makedirs(os.path.dirname(meta_path), exist_ok=True)
            with open(meta_path, 'w', encoding='utf-8') as f:
                json.dump({'span': list(new_span)}, f)
        except OSError:
            pass
    return new_src


# --------------------------------------------------------------------------
# 验证（Agg 无头重跑）
# --------------------------------------------------------------------------
def verify_run(script, python=None, timeout=300):
    """Agg 无头重跑脚本：exit 0 = 通过。返回 (ok, err_tail)。

    cwd 切到脚本目录——脚本里的相对路径（pd.read_csv('data.csv') / np.load /
    open(...)）必须在脚本目录下解析，与 verify_semantic 的 collect() 口径一致，
    否则会被误判"写回后跑不通"而回滚。
    """
    python = python or sys.executable
    env = dict(os.environ)
    env['MPLBACKEND'] = 'Agg'
    cwd = os.path.dirname(os.path.abspath(script))
    try:
        r = subprocess.run([python, script], capture_output=True,
                           timeout=timeout, env=env, cwd=cwd)
        err = (r.stderr or b'').decode('utf-8', 'replace')[-2000:]
        return r.returncode == 0, err
    except subprocess.TimeoutExpired:
        return None, '验证超时（>%ss）' % timeout
    except Exception as e:                       # noqa: BLE001
        return False, str(e)


# --------------------------------------------------------------------------
# 主入口
# --------------------------------------------------------------------------
def writeback(script, data, params_path, verify=True, python=None,
              timeout=300, dry_run=False, semantic=True, only_fig=True,
              style='block', fast_check=False):
    """确定性写回。返回结果 dict：
      {reason, changes, block, backup, warnings, verified, semantic, err}
    reason：ok / best_effort / no_change / no_fig / no_axes / fail。
    semantic=True 时在"能跑通"之后再比对目标图状态是否真的等于参数。
    only_fig=True（默认）循环出图时按 fig_index 加守卫只改目标那张；False = 统一应用。
    style：'block'（默认，插调整块）/ 'inplace'（直接改原代码数字，用户主推）。
    fast_check=True 且 verify=False 时走**快速验证档**：只做语法静态检查 + 必要时回滚，
    **不重跑脚本**（保证很弱，见 _FAST_VERIFY_NOTE 的原话）。
    """
    script = os.path.abspath(script)
    # style 必须如实回报：apply 用它选"已原位改 N 处"还是"已插入调整块"两种消息，
    # --json 里 agent 也靠它判断这次到底动了源码的哪个形态。原先这条路径漏设，
    # 结果**块模式以外的写回在 JSON 里 style=None**、人读消息也永远说"插入调整块"
    # （2026-10-10 写 §11 用例时抓到）。
    res: Dict[str, Any] = {'reason': FAIL, 'changes': [], 'block': None,
                           'backup': None, 'warnings': [], 'verified': None,
                           'err': '', 'style': 'block'}
    axes = data.get('axes') or []
    if not axes:
        res['reason'] = NO_AXES
        return res
    try:
        # 用 params.read_text 兜编码：GBK / UTF-8-BOM 的老脚本也要能读能写回
        # （t1-J：原先是裸 open(encoding='utf-8')，GBK 脚本直接抛 UnicodeDecodeError，
        #  CLI 下 --json 的 stdout 全空）。
        src, _src_enc = params.read_text(script)
        tree = ast.parse(src)
    except (OSError, SyntaxError, UnicodeDecodeError) as e:
        res['err'] = str(e)
        return res

    # 多图脚本却没有 fig_index：候选搜索只能退化成"按第 0 张处理"，而语义验证
    # 看的是最后一张 → 参数会落到**另一张图**上（t6-B1 的 P0）。这种情况必须
    # 拒绝，不能猜：让调用方用 describe --fig N 重新导出，或手工补上图号。
    _n_fig0 = count_figures(tree)
    if _n_fig0 > 1 and not isinstance(data.get('fig_index'), int):
        res['reason'] = NO_FIG
        res['err'] = ('脚本有 %d 张图，但参数里没有 fig_index —— 无法确定这些改动'
                      '该落到哪张上（凭猜会改错图）。请用 '
                      '`mpltweak describe <脚本> --fig N` 重新导出参数后再写回。'
                      % _n_fig0)
        return res

    if style == 'inplace':
        # 原位写回：直接改原代码数字，不插调整块（用户主推方式）
        return _writeback_inplace(script, src, tree, data, params_path,
                                  verify, python, timeout, dry_run, semantic,
                                  encoding=_src_enc, fast_check=fast_check)

    fig_var, _ = find_fig_var(tree)
    pre_warnings: List[str] = []
    gcf_fallback = False
    n_fig = count_figures(tree)
    has_plt = _has_pyplot_plt(tree)
    if fig_var is None:
        # 纯 pyplot 流（plt.figure() 不赋值）：用 plt.gcf() 兜底。
        if not has_plt:
            res['reason'] = NO_FIG
            res['err'] = ('找不到 figure/subplots 的赋值目标，且脚本没有 '
                          'import matplotlib.pyplot as plt（无法用 plt.gcf() 兜底）')
            return res
        fig_var = 'plt.gcf()'
        gcf_fallback = True
        if n_fig > 1:
            pre_warnings.append(
                '脚本创建了 %d 张图，纯 pyplot 流按"最后一张图"写回（与启动器默认一致）'
                % n_fig)
    elif n_fig > 1:
        # 多图 + fig 变量：变量可能停在早先那张图上（实测 fig.axes 为空 → IndexError）。
        # 改用 plt.gcf() 在目标 savefig/show 之前定位（gcf 此刻正是要调的那张）。
        if has_plt:
            fig_index = data.get('fig_index')
            which = ('第 %d 张（按记录定位）' % (fig_index + 1)
                     if isinstance(fig_index, int) else '最后一张（参数未记录图号）')
            pre_warnings.append(
                '脚本创建了 %d 处图（运行期共 %s 张），改用 plt.gcf() 定位%s'
                % (n_fig, data.get('n_figs') or '?', which))
            fig_var = 'plt.gcf()'
            gcf_fallback = True
        else:
            pre_warnings.append(
                '脚本创建了 %d 张图但未导入 pyplot，按 fig 变量统一应用，请人工确认' % n_fig)
    mappables, cb_args = find_mappable_names(tree)
    mappable_map = mappable_by_axis(tree)          # B3：轴序号 → 本轴 mappable 变量
    # 多图会话：给块打"目标图"标记，让幂等替换能精确认领自己的块（不误替换别人的）
    _fi, _nf = data.get('fig_index'), data.get('n_figs')
    tag = ('目标图：第 %d 张' % (_fi + 1)
           if isinstance(_fi, int) and isinstance(_nf, int) and _nf > 1 else None)
    anchor = find_anchor(tree, last=(n_fig > 1 or gcf_fallback))
    if anchor[0] == 'tail':
        pre_warnings.append('脚本里没有 savefig/show/tight_layout 锚点，'
                            '调整块已追加到脚本末尾（该脚本不存图时此块无实际效果）')

    figsize_in = data.get('figsize_in')
    new_src = src
    figsize_edited = False
    # 必须按数据里的 fig_index 定位（不给就退化成改第 1 张，即 t1-A / t6-M1）；
    # 多图脚本又没带图号时无从确定改哪张 → 不原位改，交给块里的
    # set_size_inches（块用 fig_var 寻址，与"验证取最后一张"的口径一致）。
    _fig_idx_b = data.get('fig_index')
    _fs_amb = bool(figsize_in) and n_fig > 1 \
        and not isinstance(_fig_idx_b, int)
    if figsize_in and not _fs_amb:
        new_src, figsize_edited = edit_figsize(src, tree, figsize_in, _fig_idx_b)
    figsize_in_block = figsize_in if (figsize_in and not figsize_edited) else None

    block, warnings = render_block(data, fig_var, mappables, cb_args,
                                   figsize_in_block, mappable_map, tag,
                                   multi_fig=(n_fig > 1))
    warnings = pre_warnings + warnings

    # B2：循环出图 → 按**实际候选锚点**给块加"只对第 fig_index 张生效"的守卫。
    # 守卫必须对着块真正插入的位置算：按 fig_index 定位到的锚点可能不在循环里
    #（如这次图 1 的 savefig 在循环外），那种情况下既不该加守卫、也不该报循环警告。
    fig_index = data.get('fig_index')

    def _guarded(block0, cand):
        if (not only_fig or not isinstance(fig_index, int)
                or cand[1] is None
                or find_enclosing_loop(tree, cand[1]) is None):
            return block0, ''
        gname, gval = loop_guard(tree, cand[1], fig_index)
        if gname is not None:
            return ('if %s == %d:\n' % (gname, gval) + _reindent(block0, '    '),
                    '循环出图：已加守卫 `if %s == %d:`，只对该次迭代生效'
                    '（想统一应用到所有图用 --all-figs）' % (gname, gval))
        return block0, ('锚点在循环里，但判不出图号对应的迭代值（非字面量 range / '
                        '一次迭代出多张图 / 变量迭代），已统一应用到每次迭代；'
                        '要只改一张请人工确认')

    backup = None
    attempts: List[Dict[str, Any]] = []
    # 候选锚点（按可能性排序，逐个试到"写回后能跑通"为止）：
    #   ① 参数记录的图号对应的 savefig —— 多图脚本的正确定位
    #   ② 主锚点（单图 / 最后一张时的常规位置）
    #   ③ 脚本尾（参数反映"跑完后的最终状态"，主锚点可能偏早）
    def _key(c):
        return (c[0], c[1].lineno if c[1] is not None else None)

    cands = []
    if isinstance(fig_index, int):
        saves = find_savefigs(tree)
        if 0 <= fig_index < len(saves):
            cands.append(('before', saves[fig_index]))
    for c in (anchor, ('tail', None)):
        if _key(c) not in [_key(x) for x in cands]:
            cands.append(c)

    chosen_block = block
    guard_note = ''
    if not dry_run:
        try:
            backup = _make_backup(script, params_path)
        except OSError as e:
            res['err'] = '备份失败: %s' % e
            return res
        meta = _meta_path(params_path)
        chosen = None
        last_err = ''
        baseline = None
        for cand in cands:
            blk, note = _guarded(block, cand)
            content = insert_or_replace(new_src, blk, cand, meta, tag)
            try:
                _atomic_write(script, content, encoding=_src_enc)
            except OSError as e:
                res['err'] = '写文件失败: %s（%s）' % (
                    e, _rollback_note(backup, script))
                return res
            if not verify:
                chosen = cand
                chosen_block = blk
                guard_note = note
                break
            vok, verr = verify_run(script, python, timeout)
            attempts.append({'anchor': cand[0],
                             'line': cand[1].lineno if cand[1] is not None else None,
                             'ok': vok})
            if vok is None:
                # 超时无法判定 → 不回滚、不换锚点（重跑还会超时），按写回成功处理
                warnings.append('验证超时（>%ss），未能自动验证；块已写入，'
                                '请手动重跑确认' % timeout)
                chosen = cand
                chosen_block = blk
                guard_note = note
                res['verified'] = None
                break
            if vok:
                if semantic:
                    # 块路径会把画布尺寸写进块里（set_size_inches），所以只要参数
                    # 带了 figsize 就该比对，而不是只在"原位替换过 figsize"时才比。
                    sok, sdetail = _verify.verify_semantic(
                        script, data, python, timeout,
                        figsize_edited=bool(data.get('figsize_in')))
                    attempts[-1]['semantic'] = sok
                    res['semantic'] = sok
                    if sok is False:
                        last_err = '语义验证不一致：%s' % sdetail
                        warnings.append('锚点 %s 能跑通但布局没落到目标图（%s），换锚点重试'
                                        % (cand[0], sdetail))
                        continue
                    if sok is None:
                        warnings.append('语义验证未取到目标图状态（%s），本次仅按"能跑通"判定'
                                        % sdetail)
                chosen = cand
                chosen_block = blk
                guard_note = note
                res['verified'] = True
                break
            last_err = verr
            # 验证失败：先判别是"我们的块弄坏了"还是"脚本本来就无头跑不通"。
            # 跑原脚本（备份即原文）——它要也跑不通，就不该怪写回。
            if baseline is None:
                baseline, _ = verify_run(backup, python, timeout)
                res['baseline_ok'] = baseline
                if not baseline:
                    warnings.append(
                        '脚本本身在无头环境跑不通（缺数据/依赖，或需要 GUI/交互），'
                        '无法用 Agg 验证写回结果；块已按主锚点写入，请自行运行确认')
                    chosen = cand
                    chosen_block = blk
                    guard_note = note
                    res['verified'] = None
                    break
        if chosen is None:
            # 全部候选都跑不通 → 回滚；回滚是否真的成功要如实写进结论。
            # 传 expect：若用户在我们验证期间改过这个文件，就别拿备份整体覆盖。
            res.update({'reason': FAIL, 'attempts': attempts, 'verified': False,
                        'err': ('所有锚点写回后 Agg 重跑均失败（原脚本单独可跑通），'
                                '%s。尝试：%s\n'
                                'stderr 尾部：\n%s'
                                % (_rollback_note(backup, script,
                                                  expect=content),
                                   attempts, last_err.strip()))})
            return res
        anchor = chosen
        res['attempts'] = attempts
    else:
        # dry-run：按第一个候选锚点预览（含守卫）
        chosen_block, guard_note = _guarded(block, cands[0])
    if guard_note:
        warnings.append(guard_note)

    if fast_check and not verify and not dry_run:
        # 快速验证档（块路径）：与 _writeback_inplace 同一套弱保证 —— 只查语法，
        # 坏在这里当场回滚；不重跑，所以"能跑通/布局对不对"它不负责（原话说清）。
        # **`not dry_run` 是必须的**：dry-run 走上面的 else 分支，`content` 根本没绑定，
        # 漏掉这个条件就是 UnboundLocalError（独立审阅 F2 实测复现：
        # `apply g.py --write --style block --dry-run --verify-fast` → rc=1）。
        try:
            ast.parse(content)
            res['verify_mode'] = 'fast'
        except SyntaxError as e:
            res.update({'reason': FAIL, 'verify_mode': 'fast',
                        'err': '快速验证失败：写回后的源码语法错误（%s）：%s'
                               % (_rollback_note(backup, script, expect=content), e),
                        'verified': False})
            return res

    res.update({'reason': BEST_EFFORT if warnings else OK,
                'block': chosen_block, 'backup': backup, 'warnings': warnings,
                'fig_var': fig_var,
                'gcf_fallback': gcf_fallback,
                'figsize_edited': figsize_edited,
                'anchor': anchor[0],
                'anchor_line': anchor[1].lineno if anchor[1] is not None else None})
    return res
