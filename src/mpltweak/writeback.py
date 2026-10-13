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
import hashlib
import json
import math
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
# T61：参数本身不可安全落地（轴序号/图号越界等）—— 与 apply 的
# `params.validate_errors` 拒写同一档：reason='bad_params' → rc=2 + ok=false。
# 这样"引擎能看出问题"的情况不再伪装成 no_change/ok。
BAD_PARAMS = 'bad_params'
# 0.1.10 外部改动守卫：源码读取之后、任何**落盘动作**之前发现磁盘内容已经变了
# （用户在编辑器里保存过）→ 停手，绝不覆盖。人读文案见 messages 的
# 'external_modified'；机器可读字段见 _external_result（reason + external_modified）。
EXTERNAL_MODIFIED = 'external_modified'



FIG_FACTORIES = ('figure', 'subplots', 'subplot_mosaic')
# ^ `subplot_mosaic` 也是**建图**调用（返回 (Figure, dict[str, Axes])，与 subplots
#   同级）。0.1.8 漏了它 → 全引擎的"第 fig_index 张图"口径与用户/agent 的自然理解
#   差一位：L0 语料 `image_demo.py`（5 张图：33 subplots / 54 subplot_mosaic /
#   121 subplots / 141 subplots / 163 subplots）里，params 说的第 1 张图是
#   mosaic（figsize=(7, 3.5)），而 edit_figsize 的节点表里第 1 个是第 121 行的
#   subplots（figsize=(10, 3)）→ 干净往返把**另一张图**的画布尺寸改掉了
#   （L0 复测 case 6，(10, 3) → (7, 3.5)，真·数据损坏）。
MAPPABLE_FACTORIES = ('contourf', 'pcolormesh', 'imshow', 'contour',
                      'scatter', 'bar', 'tripcolor', 'quiver', 'streamplot')
SAVE_ANCHORS = ('savefig', 'show')


# --------------------------------------------------------------------------
# 数值格式化（与 apply 的打印口径一致）
# --------------------------------------------------------------------------
def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError, OverflowError):
        return repr(v)
    if not math.isfinite(f):
        # NaN / ±Inf 落不成合法的 Python 数字字面量（`int(nan)` 直接抛
        # ValueError，`%.6g` 会写出 `nan`），也不能进 JSON。审计 E6 的第 ⑤ 类
        # 脏输入（figsize_in:[NaN,5]）就崩在这里。
        return repr(v)
    if f == int(f) and abs(f) < 1e15:
        return '%d' % int(f)
    return '%.6g' % f


def _fmt_pos(v):
    try:
        return '%.4f' % float(v)
    except (TypeError, ValueError):
        return repr(v)


def _label_fontsizes(a):
    """返回 ``(x 标签字号, y 标签字号)``；**y 没写就是 None，一个字节都不动 y**。

    为什么单列一个 y 字段（T11）：GUI 能分别调 x/y 标签字号（gui-fixer 的 F3）。

    T73（用户裁定，零兼容）：**删掉"y 缺失时沿用 x"这条兼容老参数文件的回退** ——
    参数里没写 ``label_fontsize_y`` 时，写回侧**不填、不写、也不把它算进 changes**。
    旧回退的后果是"只调 x 却把 y 也改了"（t72 ② 报的 130/131 例）；现在缺 y 就
    根本不碰 y（问题从"改了但会说"变成"不会改"）。`describe`/GUI 导出的参数**本来
    就带 y**，所以正常流程零变化。
    """
    return a.get('label_fontsize'), a.get('label_fontsize_y')


def _tick_fontsizes(a):
    """返回 ``(x 刻度字号, y 刻度字号)``；**y 没写就是 None**（T23，与标签字号同构）。

    T25（GUI）导出侧已经分列 ``tick_fontsize`` / ``tick_fontsize_y``。
    T73（零兼容）：与 `_label_fontsizes` 同一套逻辑 —— 删掉"y 缺失时沿用 x"的回退，
    缺 y 就不写 y 侧那一行（`axis='y'` 形式只在显式给了 y 时才出现）。
    """
    return a.get('tick_fontsize'), a.get('tick_fontsize_y')


def _axis_items(data):
    """参数里的轴项，**只收字典**。

    脏项（``axes: [null]`` / ``axes: {"0": ...}`` / ``axes: ["0"]``）由
    ``params.validate_errors`` 在入口一律拒写；这里再兜一层是因为写回引擎也可能
    被库调用方（toolbox / 以后的新前端）直接用，而 `.get()` 打在 None/str 上会抛
    AttributeError 变成 internal_error（审计 E6 的第 ②/③ 类）。
    """
    axes = data.get('axes') if isinstance(data, dict) else None
    return [a for a in (axes or []) if isinstance(a, dict)]


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


# 语句节点（锚点提升用；int/float 这类不算）
_STMT_TYPES = (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.Expr, ast.If,
               ast.For, ast.AsyncFor, ast.While, ast.With, ast.AsyncWith,
               ast.Try, ast.Return, ast.Delete, ast.Assert, ast.Raise,
               ast.Import, ast.ImportFrom, ast.FunctionDef, ast.ClassDef)


def _parent_map(tree):
    """{id(子节点): 父节点} —— 一次 ast.walk 建好，供"提升到语句"用。"""
    parents = {}
    for n in ast.walk(tree):
        for c in ast.iter_child_nodes(n):
            parents[id(c)] = n
    return parents


def _stmt_node(node, parents):
    """把一个表达式/调用节点提升到它所属的**语句**节点。

    行内复合语句（`if flag: fig.savefig(...)` / `try: fig.savefig(...)`）里，
    表达式节点的 col_offset 是"算式起点"（`if flag: ` 之后），拿它当插入缩进会写出
    非法代码（IndentationError，外部评审条目 3(c)）；插入位置也该按整条语句算。
    """
    cur = node
    for _ in range(64):                       # 深度上限，防意外环
        if cur is None:
            return node
        if isinstance(cur, _STMT_TYPES):
            # 行内复合语句（`if flag: fig.savefig(...)`）里，body 语句与 `if` **同一行**，
            # 它自己的 col_offset 是"算式起点"（9 格）→ 拿它当缩进会写出 IndentationError。
            # 继续往上提升到那条复合语句（col_offset 才是真正的行首缩进）。
            par = parents.get(id(cur))
            if par is not None and isinstance(par, _STMT_TYPES) \
                    and getattr(par, 'lineno', None) == cur.lineno:
                cur = par
                continue
            return cur
        cur = parents.get(id(cur))
    return node


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


# --------------------------------------------------------------------------
# T61：脚本侧的越界判据（各自独立成函数，便于单测与反向验证）
# --------------------------------------------------------------------------
def _axis_index_beyond(tree, data) -> str:
    """``axes[].index`` 越界的拒绝理由（不越界返回 ''）。

    上限取两者的**较大值**（保守）：
      * ``len(axes)`` —— 参数文件自报的轴数。describe / GUI 导出的文件里，
        axes 就是该图轴的**全集**（下标恰好 0..n-1，含 colorbar 槽轴），
        所以它是可靠的上界；
      * 本图内**可静态寻址**的轴创建调用数（``_figure_axes_for_index``）。
    取大是为了不误伤"参数只覆盖部分轴"的合法用法（T57：只给 index=1 时
    第 0 轴不许被改 —— 那种文件里 axes 只有 1 条，但脚本里有 2 个轴）。

    为什么必须拒：越界下标要么让块引用 ``fig.axes[99]``（脚本一跑就 IndexError），
    要么在 -1 时被 Python 当成"最后一张"——**静默改到别的轴上**（对象错）。
    """
    axes = _axis_items(data)
    if not axes:
        return ''
    fi = data.get('fig_index')
    slots = 0
    try:
        slots = len(_figure_axes_for_index(tree, fi if isinstance(fi, int) else 0))
    except (SyntaxError, ValueError, AttributeError, TypeError):
        slots = 0
    bound = max(len(axes), slots)
    for i, a in enumerate(axes):
        idx = a.get('index')
        if isinstance(idx, bool) or not isinstance(idx, int):
            continue                      # 类型问题由 params.validate_errors 报
        if idx >= bound:
            return ('axes[%d].index=%d 越界：这份参数里最多只有 %d 个轴槽'
                    '（参数自报 %d 条 / 脚本里可寻址 %d 个），轴序号是 0 基。'
                    '请用 `mpltweak describe <脚本>` 重新导出参数。'
                    % (i, idx, bound, len(axes), slots))
    return ''


def _fig_index_beyond(tree, data) -> str:
    """``fig_index`` 越界的拒绝理由（不越界/无从判断返回 ''）。

    上界取"参数自报的 ``n_figs``"与"脚本里 figure 创建调用数"的较大值：
      * 参数自报优先（describe/GUI 都会写 ``n_figs``，循环出图脚本也准）；
      * 都没有（或 AST 找不到建图调用，例如 OO 风格 ``Figure()``）时**不判** ——
        宁可漏判，也不能把合法图号拒掉。
    为什么必须拒：图号越界时引擎的候选搜索全部落空 → 老行为是
    ``no_change`` + rc=0（用户以为写回成功，其实一个字都没改）。
    """
    fi = data.get('fig_index')
    if not isinstance(fi, int) or isinstance(fi, bool):
        return ''
    if fi < 0:
        return ('fig_index=%d 为负：图号是 0 基（-1 会被 Python 当成"最后一张"，'
                '静默写到别的图上）；拒写。' % fi)
    try:
        n_static = count_figures(tree)
    except (SyntaxError, ValueError, AttributeError, TypeError):
        n_static = 0
    n_rec = data.get('n_figs')
    if isinstance(n_rec, bool) or not isinstance(n_rec, int):
        n_rec = 0
    bound = max(n_static, n_rec)
    if bound >= 1 and fi >= bound:
        return ('fig_index=%d 越界：脚本里只有 %d 张图'
                '（参数自报 %s），图号是 0 基。请用 '
                '`mpltweak describe <脚本> --all-figs` 重新导出。'
                % (fi, bound, n_rec or '未记录'))
    return ''


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
def _subplots_single_axes(call) -> bool:
    """``plt.subplots(...)`` 是否只返回**单个** Axes（而不是 Axes 数组）。

    matplotlib 的 squeeze 规则：``nrows * ncols == 1`` → 单个 Axes。参数可能是位置
    （``subplots()`` / ``subplots(1, 1)`` / ``subplots(2, 2)``）或关键字
    （``nrows=`` / ``ncols=``）。判不出来（含变量/星号展开）返回 False —— 保守地
    继续当成轴序列，保持旧行为。审计 E13：``fig, ax = plt.subplots()`` 这条最常见的
    单轴写法过去被当成"轴序列"，于是该轴**永远没有变量映射**，set_title /
    tick_params / grid 全部进 skipped，警告还甩锅给源码。
    """
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
        return True                      # subplots() → 1×1 → 单个 Axes
    while len(vals) < 2:
        vals.append(1)
    return vals[0] * vals[1] == 1


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
                    if _subplots_single_axes(v):
                        # `fig, ax = plt.subplots()`：第二个目标就是那个唯一的 Axes
                        # 本体，不是轴序列（审计 E13，见 _subplots_single_axes）。
                        out.setdefault(second.id, 0)
                    else:
                        seq_names.add(second.id)           # fig, axes = subplots(...)
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


def _seq_lit_nums(node):
    """``(a, b)`` 两个数值字面量的元组/列表 → ``[float, float]``；否则 None。

    只认"元素全是数值字面量"的写法（``(6.4, 4)`` / ``[6, 3]`` / ``(4., 4.)``）；
    含变量/表达式（``figsize=plsz``、``(w, h)``）就返回 None —— 判不了是否等值。
    """
    if not isinstance(node, (ast.Tuple, ast.List)) or len(node.elts) != 2:
        return None
    out = []
    for el in node.elts:
        if not _is_num_literal(el):
            return None
        out.append(float(_lit_num(el)))
    return out


def _num_close(a, b) -> bool:
    """数值相等判定（容差 1e-9 相对量级）：``6.4`` 与 ``6.4000001`` 视为相等。"""
    try:
        a, b = float(a), float(b)
    except (TypeError, ValueError):
        return False
    return abs(a - b) <= 1e-9 * max(1.0, abs(a), abs(b))


def _fmt_figsize(node, figsize_in):
    """按**原容器写法**生成新的 figsize 文本：原值是列表就保持 ``[...]``。

    值确实变了才走到这里（等值已在 edit_figsize 里短路返回）—— 旧实现无条件写成
    ``(a, b)``，把用户的 ``figsize=[6, 3]`` 列表写法也顺手改成元组（L0-1 的伴生现象）。
    """
    a, b = _num(figsize_in[0]), _num(figsize_in[1])
    if isinstance(node, ast.List):
        return '[%s, %s]' % (a, b)
    return '(%s, %s)' % (a, b)


def _fig_call_node(tree, fig_index=None):
    """第 fig_index 个建图调用节点（按源码序；越界用最后一个，None 用第一个）。

    `subplot_mosaic` 也算建图调用（见 FIG_FACTORIES）—— 少算它会让"第 N 张图"
    整体错位一位，把 A 图的画布尺寸写到 B 图的调用上（L0 case 6）。
    """
    nodes = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
             and n.func.attr in FIG_FACTORIES]
    nodes.sort(key=lambda n: (n.lineno, n.col_offset))
    if not nodes:
        return None
    if isinstance(fig_index, int) and 0 <= fig_index < len(nodes):
        return nodes[fig_index]
    if isinstance(fig_index, int):
        return nodes[-1]                     # 越界（循环出图）→ 最后一个调用点
    return nodes[0]


def _call_assign_name(tree, node):
    """建图调用被赋给的名字（``fig = plt.figure(...)`` / ``fig, ax = plt.subplots(...)``）。

    T67/C：要判"后面那条 ``set_size_inches`` 覆盖的是不是**这张图**"，得先知道目标图
    叫什么。找不到名字（调用没赋值 / 赋给下标）→ None（调用方保持保守）。
    """
    for _n in ast.walk(tree):
        if isinstance(_n, ast.Assign) and _n.value is node:
            for t in _n.targets:
                if isinstance(t, ast.Name):
                    return t.id
                if isinstance(t, ast.Tuple):
                    for e in t.elts:
                        if isinstance(e, ast.Name):
                            return e.id        # 元组解包：第一个名字就是 fig
    return None


def _fig_aliases(tree, name):
    """``X = <name>`` 形式的别名闭包（图对象换过名字后仍要认得出是同一张图）。"""
    if not name:
        return set()
    names = {name}
    for _ in range(4):          # 别名层次很浅；限定轮数，防病态源码拖住
        grew = False
        for _n in ast.walk(tree):
            if (isinstance(_n, ast.Assign) and isinstance(_n.value, ast.Name)
                    and _n.value.id in names):
                for t in _n.targets:
                    if isinstance(t, ast.Name) and t.id not in names:
                        names.add(t.id)
                        grew = True
        if not grew:
            break
    return names


def _later_size_override(tree, after_lineno, fig_names=None):
    """``after_lineno`` **之后**、**作用在目标图那个对象上**的最后一条 ``set_size_inches``。

    ``fig_names``（T67/C）：目标图的变量名集合（含别名）。给了它，就只看
    ``接收者 ∈ fig_names`` 的那几条 —— ``fig_bee.set_size_inches(10, 7)`` 改的是
    **另一张图**，不能当成目标图 ``fig = plt.figure(figsize=...)`` 的尺寸覆盖。
    语料实测（``big/zenodo/21785935__03_ml_pipeline.py``，136 KB / 20 张图）：第 1 次
    apply 原位改了 ``fig = plt.figure(figsize=(18, 12))`` 的尺寸；第 2 次却被另一张图
    的 ``fig_bee.set_size_inches(10, 7)`` 误判成"目标尺寸已被覆盖" ⇒ 等值证明失败 ⇒
    原位判"没有可改的数字" ⇒ auto 退块又插一遍 ``plt.gcf().set_size_inches(6, 4)``
    （同一参数两次走两条路，幂等破功）。
    接收者是 ``plt.gcf()`` / 属性 / 下标时静态判不出它是不是目标图 ⇒ 保持保守
    （算作覆盖）；``fig_names`` 为 None 时同旧行为。

    T63 探针 P3'：源码里 ``subplots(figsize=(9,5))`` 之后又 ``fig.set_size_inches(4,3)``，
    运行时生效的是**后一条** 4x3。过去只比 keyword ⇒ 目标 9x5 被判"已经对了" ⇒
    no_change ⇒ 用户改的尺寸被静默丢掉（inplace 与 block 两条路径共用 figsize_already_ok）。
    静态判据必须与运行时"最后一条生效"一致。
    """
    got = None
    got_ln = None
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == 'set_size_inches'
                and len(node.args) >= 2
                and getattr(node, 'lineno', 0) > after_lineno):
            continue
        if fig_names is not None:
            # T71：接收者要看**根名字**。`tree_disp.figure_.set_size_inches(10, 6)` 的接收者
            # 是 Attribute（根 'tree_disp' 根本不是本图的别名），旧判据却把它当成"本图被
            # 后续覆盖" ⇒ `figsize_already_ok` 误判 False ⇒ 第二次 apply 又插块、又改字节、
            # 还重写 `.tweak.bak`（revert 回不到原件）。根名字不是本图别名 ⇒ 不是覆盖。
            _recv = node.func.value
            _root = _recv
            while isinstance(_root, ast.Attribute):
                _root = _root.value
            if isinstance(_root, ast.Name):
                if _root.id not in fig_names:
                    continue                # 明确是**另一张图**/别的对象的尺寸 → 不是覆盖
                # T71：名字在"目标调用 → 这条 set_size_inches"之间被**重绑**到别的图上
                # （同一份脚本里 `fig, ax = plt.subplots(...)` 反复出现）⇒ 不是本图覆盖。
                if _rebound_between(tree, after_lineno, node.lineno, _root.id):
                    continue
            else:
                # T71：接收者**不是变量名**（`plt.gcf()` / `obj.figure_` / `axes[0].figure` …）
                # ⇒ 不可证是本图。多图脚本里 `plt.gcf()` 恰恰是**最后一张**（实测第 652 行
                # `plt.gcf().set_size_inches(12, 6)` 被误当成本图覆盖，于是第二次 apply 认不出
                # "figsize 已到位"）。只有"全文件仅一次建图"时 gcf() 才等价于本图（T70-B 口径）。
                if _fig_call_count(tree) != 1:
                    continue
        try:
            vals = [float(ast.literal_eval(node.args[0])),
                    float(ast.literal_eval(node.args[1]))]
        except (ValueError, TypeError, SyntaxError):
            continue
        if got_ln is None or node.lineno > got_ln:
            got, got_ln = vals, node.lineno
    return got


def _fig_call_count(tree):
    """全文件建图调用次数（T71：判 `plt.gcf()` 能不能当本用 —— 仅一次时才行）。"""
    return sum(1 for n in ast.walk(tree)
               if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
               and n.func.attr in FIG_FACTORIES)


def _rebound_between(tree, lo, hi, name):
    """``(lo, hi)`` 之间是否有把 ``name`` 重新绑定的语句（赋值 / for / with / del / def…）。

    T71：`fig, ax = plt.subplots(...)` 在同一份脚本里会**反复重绑** `fig` ——
    之后那条 `fig.set_size_inches(...)` 属于**另一张图**，不能当成"本图被覆盖"。
    """
    for n in ast.walk(tree):
        if not isinstance(n, (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.For,
                              ast.With, ast.AsyncWith, ast.Delete,
                              ast.FunctionDef, ast.ClassDef,
                              ast.Import, ast.ImportFrom)):
            continue
        ln = getattr(n, 'lineno', None)
        if ln is None or not (lo < ln < hi):
            continue
        if name in _assign_names_of(tree, n):
            return True
    return False


def figsize_already_ok(tree, figsize_in, fig_index=None) -> bool:
    """源码里目标图的 ``figsize=`` 是否**已经**是这两个数（静态等价证明）。

    L0-1/L0-3 的分水岭：等值时 `edit_figsize` 什么都不改 —— 那"画布尺寸没落地"
    的警告、以及块模式里补一条 `set_size_inches` 的兜底就都不该再出现，
    否则第二次 apply 会因为块内容多了一行而重新写盘（幂等破功）。
    """
    if not figsize_in or len(figsize_in) < 2:
        return False
    node = _fig_call_node(tree, fig_index)
    if node is None:
        node_kws = ()
    else:
        node_kws = node.keywords
    for kw in node_kws:
        if kw.arg == 'figsize' and kw.value is not None:
            have = _seq_lit_nums(kw.value)
            if not (have is not None
                    and _num_close(have[0], figsize_in[0])
                    and _num_close(have[1], figsize_in[1])):
                break       # T71：证不出**不等于**证伪 —— 交给下面"本图范围"的兜底再判
            # T64/D2：这一处虽等于目标，但**后面**若有 set_size_inches 覆盖，
            # 生效值就是那一条 —— 必须拿它比，否则"被覆盖掉的旧值"会被当成
            # "已经对了"（T63 探针 P3'：目标 9x5、源码 4x3 ⇒ 静默 no_change）。
            # T67/C：只看**这张图自己**的后续覆盖（别的图的 set_size_inches 不算）。
            _fignm = _call_assign_name(tree, node)
            ov = _later_size_override(tree, kw.value.lineno,
                                      _fig_aliases(tree, _fignm) if _fignm else None)
            if ov is None:
                return True
            return bool(_num_close(ov[0], figsize_in[0])
                        and _num_close(ov[1], figsize_in[1]))
    # T71：`_fig_call_node` 的序号口径与"写回时真正改的那张图"可能不一致（多图脚本实测：
    # 写回改的是图 2 内的第 338 行，这里却拿不到那个节点）⇒ 退回**本图范围内**再找一遍
    # 字面量；命中同样要过 `_later_size_override` 那道闸（T64 的等值证明）。
    if isinstance(fig_index, int):
        lo, hi = _fig_span(tree, fig_index)
        for n in ast.walk(tree):
            if not (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)):
                continue
            if n.func.attr not in FIG_FACTORIES:
                continue
            ln = getattr(n, 'lineno', 0)
            if not (lo <= ln < hi):
                continue
            for kw in n.keywords:
                if kw.arg != 'figsize' or kw.value is None:
                    continue
                have = _seq_lit_nums(kw.value)
                if not (have is not None
                        and _num_close(have[0], figsize_in[0])
                        and _num_close(have[1], figsize_in[1])):
                    continue
                _fignm = _call_assign_name(tree, n)
                ov = _later_size_override(
                    tree, kw.value.lineno,
                    _fig_aliases(tree, _fignm) if _fignm else None)
                if ov is None or (_num_close(ov[0], figsize_in[0])
                                  and _num_close(ov[1], figsize_in[1])):
                    return True
    return False


def edit_figsize(src, tree, figsize_in, fig_index=None):
    """原位替换第 fig_index 个 subplots()/figure() 调用的 figsize= kwarg 值。

    fig_index 为 int 时定位到**对应图**的调用（多图脚本每张图的画布各自记）；
    越界时用最后一个调用点（循环出图近似）；None 用第一个（单图/旧调用）。
    返回 (new_src, edited)。figsize 是位置参数/缺失时返回原样 + False。

    L0-1 / L0-3：**先比对原值**。两个字面量数值已经等于目标值时**完全不编辑** ——
    不写盘、不进 changes、不报 figsize。旧实现无条件把整个 kwarg 重写成规范形式
    （``figsize=[6, 3]`` → ``(6, 3)``、``(6.4,  4)`` → ``(6.4, 4)``、
    ``(4., 4.)`` → ``(4, 4)``、``(16,10)`` → ``(16, 10)``），值一个没变却改了
    用户源码的字节 —— L0 语料里 27/30 条 M3（干净往返）残余就是这个。
    """
    if not figsize_in or len(figsize_in) < 2:
        return src, False
    starts = _line_starts(src)
    node = _fig_call_node(tree, fig_index)
    if node is None:
        return src, False
    for kw in node.keywords:
        if kw.arg == 'figsize' and kw.value is not None:
            v = kw.value
            # 等值短路：源码里已经是这两个数 → 本次**没有任何改动**
            _have = _seq_lit_nums(v)
            if (_have is not None
                    and _num_close(_have[0], figsize_in[0])
                    and _num_close(_have[1], figsize_in[1])):
                return src, False
            new_txt = _fmt_figsize(v, figsize_in)
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


def _find_tick_call_for_axis(tree, recv_name, axis, after_lineno=0,
                             before_lineno=10 ** 9):
    """T78：找 ``recv.tick_params(..., labelsize=...)`` 作为 ``axis`` 侧的**原位目标**。

    优先级：
      ① **显式** ``axis='<axis>'``（最忠实）；
      ② 不带 ``axis=``、或 ``axis='both'``——源码自己就是"两边一起设"，改它那一个字面量
         就是它原本的语义（不是我们偷偷多改了什么）。
    **排除另一侧的显式写法**：``axis='y'`` 永远不能当 x 的目标。旧代码这里用不过滤 axis 的
    `_find_call`，会命中同变量名下**最早**的那条 —— 实测（zenodo
    ``22254023__figure_lib.py``）：把第 812 行 ``axis='y'`` 的 labelsize 改成目标值，
    而该改的第 833 行 ``axis='x'`` 一个字没动，工具却报 ``ok``。
    ``axis=`` 是变量/表达式 ⇒ 证不出 ⇒ **不当候选**（fail-closed，宁可退块）。
    没有 ``labelsize`` 的 ``tick_params`` 也不作候选（它改不了刻度字号）。
    """
    found = None
    plain = None
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == 'tick_params'
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == recv_name):
            continue
        _ln = getattr(node, 'lineno', 0)
        if _ln < after_lineno or _ln >= before_lineno:
            continue
        if not any(k.arg == 'labelsize' for k in node.keywords):
            continue
        _ax = next((k for k in node.keywords if k.arg == 'axis'), None)
        if _ax is None:
            bucket = 'plain'
        elif not (isinstance(_ax.value, ast.Constant)
                  and isinstance(_ax.value.value, str)):
            continue                     # axis= 非字面量：证不出，不当候选
        elif _ax.value.value == axis:
            bucket = 'explicit'
        elif _ax.value.value == 'both':
            bucket = 'plain'
        else:
            continue                     # ★ 另一侧（axis='y' 之于 x）：排除
        if bucket == 'explicit':
            if found is None or node.lineno < found.lineno:
                found = node
        else:
            if plain is None or node.lineno < plain.lineno:
                plain = node
    return found if found is not None else plain


def _find_tick_axis_call(tree, recv_name, axis, after_lineno=0,
                         before_lineno=10 ** 9):
    """找 ``Name(recv).tick_params(axis='<axis>', ...)``（T23 的 x/y 刻度分列用）。

    **必须显式带 `axis=`**：没有 axis 的 `tick_params(labelsize=…)` 一次改 x 和 y
    两边，把它当成"x 专属写法"再改它，就正是"y 的改动被吃掉"那个 bug。
    """
    found = None
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == 'tick_params'
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == recv_name):
            continue
        _ln = getattr(node, 'lineno', 0)
        if _ln < after_lineno or _ln >= before_lineno:
            continue
        _ax = next((k for k in node.keywords if k.arg == 'axis'), None)
        if _ax is None or not (isinstance(_ax.value, ast.Constant)
                               and isinstance(_ax.value.value, str)
                               and _ax.value.value == axis):
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
    axes = _axis_items(data)

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

    def _add(node, txt, key=None, value=None, bool_value=None):
        """登记一处替换；被重叠检测拒绝时返回 False。

        调用方必须据此决定是否把该字段记进 covered —— 否则会出现
        "声称原位修改了 N 处、磁盘上只落了 N-1 处"（t6-M16）。

        key=(轴序号, 字段名)、value=目标数值时，若原字面量的**数值**已等于目标
        （注意 `0.10` 与 `0.1` 文本不同但数值相同），这处就不登记为改动 ——
        字段是否算"真改到"由 _reg/_noop 的计数在收尾处判定。

        布尔字段必须走 ``bool_value``：``_lit_num`` 为避开 ``True == 1`` 明确排除了
        bool，走数值通道时空改判定永远不命中 → `ax.grid(True)` 每次都被谎报成
        "改了 1 处"、重复 apply 永远到不了 no_change（审计 E8）。
        """
        _s = _offset(starts, node.lineno, node.col_offset, src)
        _e = _offset(starts, node.end_lineno, node.end_col_offset, src)
        if key is not None:
            _reg[key] = _reg.get(key, 0) + 1
            _same = False
            if bool_value is not None:
                _same = (isinstance(node, ast.Constant)
                         and isinstance(node.value, bool)
                         and node.value is bool_value)
            elif value is not None:
                _lit = _lit_num(node)
                try:
                    _same = (_lit is not None
                             and abs(_lit - float(value)) <= 1e-12)
                except (TypeError, ValueError):        # 脏参数：字符串当数字
                    _same = False
            if _same:
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
    # T57-稀疏轴（t2 fuzz high）：**按轴序号 index 直接索引** —— index 的语义就是
    # "第 index 个非 colorbar 轴创建调用"（与 collect 侧一致，见 _figure_axes_for_index）。
    # 旧实现把「参数里的非 colorbar 轴（按参数出现序）」与 axes_calls **按位置 zip**：
    # 参数稀疏（只给 index=1、不带 0）时参数第 0 项（index=1）被配到 axes_calls[0]
    # （ax0）→ var_for[1] 错绑 ax0 → 原位写回改错轴（t2 实测：第一次错写 ax0 被语义
    # 回滚，第二次又被块"掩盖" → ax0 被改坏 + 幂等破功）。越界 / 负 / 非 int 一律
    # 不登记 var_for（fail-closed）→ nm=None → 进 skipped → 退块，不崩不静默写错。
    for _a in axes:
        if _a.get('is_colorbar'):
            continue
        _ai = _a.get('index')
        if isinstance(_ai, int) and 0 <= _ai < len(axes_calls):
            var_for[_ai] = axes_calls[_ai][0]
    # T41（blocker）：同一张图里**同一个变量名被赋值 ≥2 次**（`ax = fig.add_subplot(2,1,k)`
    # 写两遍）时，第 2 个及以后的轴**无法原位定位** —— `_find_call` 只取"本图区间内
    # 最早"的同名调用，于是第 2 个轴的属性永远命中第 1 个轴的调用：
    #   * 目标值恰好等于第 1 个轴的字面量 → noop 判定把整个字段从 covered 剔除 →
    #     谎报 `reason=no_change`、`changes=[]`，而源码**一个字节都没改**（静默丢改动）；
    #   * 目标值与第 1 个轴不同 → 把**第 1 个轴改坏**（语料 F-DUPVAR 实测 4 例）。
    # 把重名轴的映射删掉，让它们落入下面"没有变量映射"那一支 → 进 skipped →
    # `_needs=True` → auto 退块（块用 `fig.axes[i]` 精确定位落地）。宁可退块，
    # 也**绝不允许静默丢改动**。相应地，重名名字也不进 `addaxes`（位置字面量同样会
    # 指错调用：dict 按名字存，后一次赋值会覆盖前一次）。
    # T41：重名轴的字段查找改用**本轴窗口**（见下面的 _win_for），所以这里
    # 只算出重名集合给 addaxes 用 —— 位置字面量按名字存会指错调用，重名一律
    # 走 skipped/块；属性类字段（grid/字号/范围/刻度）由窗口精确归属。
    _dup_names = _dup_name_set(var_for)
    # T57（high）：参数只覆盖**部分**同名轴时，`_find_call` 会命中本图区间内**最早**的
    # 同名调用 ⇒ 改动被写到第 1 个轴那一行；`--verify-fast`/`--no-verify` 不跑语义层，
    # 于是「写错行」被**静默保留**（T56 矩阵实测：只给 index=1 时第 1 轴被改）。
    # 这里按**脚本里的轴创建序**（axes_calls）检测重名：某名字绑了 ≥2 个轴、而参数没有
    # 覆盖这些轴的全部 ⇒ 该名字下的轴一律不进 var_for（→ 进 skipped → 退块，块用
    # `fig.axes[i]` 精确定位）。参数覆盖全部同名轴的正常场景不受影响。
    _name_slots: Dict[str, List[int]] = {}
    for _k, (_nm, _c) in enumerate(axes_calls):
        _name_slots.setdefault(_nm, []).append(_k)
    _param_idx = set()
    for _a in axes:
        if isinstance(_a.get('index'), int):
            _param_idx.add(_a['index'])
    _partial_dups = set()
    for _nm, _ks in _name_slots.items():
        if len(_ks) > 1 and not set(_ks).issubset(_param_idx):
            _partial_dups.add(_nm)
            for _i in list(var_for):
                if var_for[_i] == _nm:
                    var_for.pop(_i, None)
    # T84（blocker）：`fig, (ax, ax) = plt.subplots(1, 2)` 这种**同名解包**不在
    # `axes_calls` 里 ⇒ T57 的检测漏掉它。这里按"建图赋值语句里的名字出现次数"补判：
    # 一个名字出现 ≥2 次、而参数没有覆盖这些轴的全部 ⇒ 与 T57 同口径：一律 fail-closed。
    for _nm_a, _cnt_a in _dup_names_in_fig_assign(
            tree, _fi if isinstance(_fi, int) else 0).items():
        if _cnt_a > 1 and not set(range(_cnt_a)).issubset(_param_idx):
            _partial_dups.add(_nm_a)
            for _i in list(var_for):
                if var_for[_i] == _nm_a:
                    var_for.pop(_i, None)
    _dup_names = set(_dup_names) | _partial_dups
    # T84（blocker）兜底：`var_for` 里凡是"部分重名"的名字一律摘掉（上面刚摘过一轮，
    # 这里再收口一次，防止后面任何补映射的路径把它加回来）。
    for _i in list(var_for):
        if var_for[_i] in _partial_dups:
            var_for.pop(_i, None)
    addaxes: Dict[str, ast.expr] = {}
    for nm, call in axes_calls:
        # 位置字面量两种写法都要认：`add_axes([x0, y0, w, h])`（ast.List）与
        # `add_axes((x0, y0, w, h))`（ast.Tuple）。只认列表时，元组写法会被静默
        # 丢进 skipped —— 用户看到的是"拖了没反应"（外部审阅第 2 条）。
        # 元素的登记/替换按节点源区间做（_add），与容器是 List 还是 Tuple 无关。
        if nm in _dup_names:
            continue                    # T41：重名 → 位置字面量无从归属，走 skipped
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
            # T84（blocker）：**部分重名**（名字绑 ≥2 条轴、参数没覆盖全部）的名字
            # 绝不许在这里被补回来 —— 它是 T57 刚刚故意摘掉的（见上面 `_partial_dups`）。
            # 补回来就会让轴 0 的字段按名字去 `_find_call`，而 `fig, (ax, ax) = ...`
            # 里 `ax` 在运行期认的是**第二条**轴 ⇒ 改到别的对象上却报 applied=true。
            if isinstance(_i, int) and _i not in var_for and _i in _rev \
                    and _rev[_i] not in _partial_dups:
                var_for[_i] = _rev[_i]
    # T41（blocker）：**同名多轴**的本轴窗口 —— 第 2 个及以后的轴不能再用
    # "本图区间内最早的 `ax.xxx(...)`"来定位（那会命中第 1 个轴的调用：目标值恰好
    # 等于第 1 个轴的字面量时就判成 noop → 谎报 no_change、源码一字未改）。
    # 归属口径：本轴窗口 = [本轴创建行, 下一次用同一变量名创建轴的行)。名字唯一时
    # 窗口与整图区间一致（行为不变）。
    _win_for: Dict[int, tuple] = {}
    _nb_axes = [x for x in axes if not x.get('is_colorbar')]
    for _k, (_a_w, (_nm_w, _c_w)) in enumerate(zip(_nb_axes, axes_calls)):
        _i_w = _a_w.get('index')
        if not isinstance(_i_w, int):
            continue
        _own = getattr(_c_w, 'lineno', 0) or 0
        _hi_w = _scope_hi
        for _k2 in range(_k + 1, min(len(_nb_axes), len(axes_calls))):
            _nm2, _c2 = axes_calls[_k2]
            if _nm2 == _nm_w:
                _hi_w = getattr(_c2, 'lineno', _scope_hi) or _scope_hi
                break
        _win_for[_i_w] = (max(_scope_lo, _own), _hi_w)
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
    # ④ 【已移除】colorbar `fraction` 的"反推写回"。
    #    旧实现把 colorbar 的实测宽度除以宿主轴 add_axes 的原始宽度，当成
    #    `fig.colorbar(..., fraction=N)` 的新值写回源码。它只在**画之前**取值时才
    #    成立：实测（matplotlib 3.10.8）`cb_w / w0 == fraction` 仅对 draw 之前的
    #    位置成立；一旦脚本自己 savefig/show 过（几乎每个脚本都会），colorbar 的
    #    宽度就被 `_box_aspect=20` 接管成 `高/20`（fraction 0.05/0.15/0.28/0.5 全都
    #    变成 0.026），于是"反推"出来的是垃圾值：干净往返把 `fraction=0.15` 写成
    #    `fraction=0.0410769`（审计 E5），而且这个伪字段永远算"真改动"→ `changes`
    #    恒非空 → auto 永不退块 → 用户拖的位置被静默丢弃还报 ok（审计 E4）。
    #    拖 colorbar 的正解是块模式（它先 `set_axes_locator(None)` +
    #    `set_box_aspect(None)` 再 `set_position`，改的是真实几何而不是反推参数），
    #    所以 colorbar 轴的 pos 照旧进 skipped，由 auto 退回块模式落地。

    for a in axes:
        i = a.get('index')
        if not isinstance(i, int):
            continue
        nm = var_for.get(i)
        _w_lo, _w_hi = _win_for.get(i, (_scope_lo, _scope_hi))
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
            # T42（Bug 5，blocker）：这一段过去只列了字号/范围/grid —— **块专属**的
            # spines / xscale / yscale / clim / cmap / lines / legend 一个都没记，
            # 于是 `plt.subplots(2, 3)`（一个变量名绑多个轴 → 拿不到变量名）这种形状下
            # `skipped` 是空的 ⇒ needs_block=false ⇒ auto 不退块 ⇒ 报 `no_change`、
            # 文件一个字节没改（语料 A 场景 486 个可测里 238 个落这一类）。
            # 这里把这些字段**无条件**记进 skipped：宁可退块，也不静默丢。
            for field in ('title_fontsize', 'label_fontsize', 'label_fontsize_y',
                          'tick_fontsize', 'tick_fontsize_y', 'xlim', 'ylim',
                          'xscale', 'yscale', 'clim'):
                if a.get(field) is not None:
                    skipped.append((i, field, a[field]))
            if a.get('grid') is not None:
                skipped.append((i, 'grid', a['grid']))
            for field in ('spines', 'lines', 'legend'):
                if a.get(field):
                    skipped.append((i, field, a[field]))
            continue
        # ③ 字号：set_title / set_xlabel / set_ylabel 的 fontsize kwarg
        #   x 用 label_fontsize、y 用 label_fontsize_y（T11）。
        #   T73（零兼容）：**y 没写时 _ly 就是 None** ⇒ 下面 `if val is None: continue`
        #   直接跳过 set_ylabel —— 不动 y、不填、changes 里也没有 y 侧。
        _lx, _ly = _label_fontsizes(a)
        _yfield = 'label_fontsize_y'
        for attr, field, val in (('set_title', 'title_fontsize',
                                  a.get('title_fontsize')),
                                 ('set_xlabel', 'label_fontsize', _lx),
                                 ('set_ylabel', _yfield, _ly)):
            if val is None:
                continue
            node = _find_call(tree, nm, attr, _w_lo, _w_hi)
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
        # ④ tick_params(labelsize=)：x/y 刻度字号分开写（T23，与 label 同构）。
        #   T73（零兼容）：没有 `tick_fontsize_y` 时**不写 y 侧那一行** —— 只处理
        #   `tick_fontsize`（源码里那个单一 labelsize 字面量），y 侧不填也不报。
        #   显式给了 y 时：
        #     * 源码里**已经有** axis='x' / axis='y' 两个写法 → 各自改字面量；
        #     * 只有单一 labelsize（一次改两边）→ 原位表达不了差异 → 两个字段都进
        #       skipped（→ 退块，块里写 `axis='x'` + `axis='y'` 两行）。
        _tx, _ty = _tick_fontsizes(a)
        _ty_explicit = a.get('tick_fontsize_y') is not None
        if _ty_explicit:
            _xnode = _find_tick_axis_call(tree, nm, 'x', _w_lo, _w_hi)
            _ynode = _find_tick_axis_call(tree, nm, 'y', _w_lo, _w_hi)
            _xkw = next((k for k in (_xnode.keywords or [])
                         if k.arg == 'labelsize'), None) if _xnode else None
            _ykw = next((k for k in (_ynode.keywords or [])
                         if k.arg == 'labelsize'), None) if _ynode else None
            _both = (_xkw is not None and isinstance(_xkw.value, ast.Constant)
                     and _ykw is not None and isinstance(_ykw.value, ast.Constant))
            if _both:
                if _tx is not None:
                    if _add(_xkw.value, _num(_tx), (i, 'tick_fontsize'), _tx):
                        covered.setdefault(i, set()).add('tick_fontsize')
                    else:
                        skipped.append((i, 'tick_fontsize（替换位置重叠，已跳过）',
                                        _tx))
                if _add(_ykw.value, _num(_ty), (i, 'tick_fontsize_y'), _ty):
                    covered.setdefault(i, set()).add('tick_fontsize_y')
                else:
                    skipped.append((i, 'tick_fontsize_y（替换位置重叠，已跳过）',
                                    _ty))
            else:
                if _tx is not None:
                    skipped.append((i, 'tick_fontsize', _tx))
                skipped.append((i, 'tick_fontsize_y', _ty))
        else:
            tf = a.get('tick_fontsize')
            if tf is not None:
                # T78：必须按 axis 过滤（优先显式 `axis='x'`，其次不带 axis / 'both'；
                # **`axis='y'` 永远排除**）。旧代码用不过滤 axis 的 `_find_call`，
                # 会命中同变量名下最早的 `tick_params(...labelsize=)` —— 实测把 y 那条
                # 改成目标值、该改的 x 那条没动，却报 ok（zenodo 22254023__figure_lib.py）。
                node = _find_tick_call_for_axis(tree, nm, 'x', _w_lo, _w_hi)
                kw = None
                if node is not None:
                    kw = next((k for k in node.keywords
                               if k.arg == 'labelsize'), None)
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
            node = _find_call(tree, nm, 'grid', _w_lo, _w_hi)
            if (node is not None and node.args
                    and isinstance(node.args[0], ast.Constant)):
                # 走 bool_value 通道：`ax.grid(True)` 对目标 True 必须判成"值没变"，
                # 否则每次 apply 都谎报"改了 1 处"、永远到不了 no_change（审计 E8）。
                if _add(node.args[0], 'True' if bool(g) else 'False',
                         (i, 'grid'), bool_value=bool(g)):
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
            node = _find_call(tree, nm, attr, _w_lo, _w_hi)
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
        # ⑥b xscale/yscale：**任何"和源码现值不同"的目标值都要落地**。
        # 旧实现只在 `_v != 'linear'` 时才登记 skipped → "源码是 log、参数要回 linear"
        # 这个方向既不在 covered、也不在 skipped，被**静默吞掉**（T18 §54j 实测：
        # `ax.set_xscale('log')` + 参数 `xscale='linear'` → reason=no_change、源码没动）。
        # 现在：源码里有可比的 set_xscale/set_yscale 字面量且与目标不同 → 登记 skipped；
        # 源码里没有写法时，只有"目标非默认(linear)"才登记（linear 是默认值，交给
        # 默认值证明去判，免得 describe 直出的 linear 每次都退块）。
        if not a.get('is_colorbar'):
            for _f in ('xscale', 'yscale'):
                _v = a.get(_f)
                if _v is None or _f in covered.get(i, ()):
                    continue
                _want = str(_v).strip().lower()
                _lit = _scale_literal(tree, var_for.get(i), _f)
                if (_lit is not None and _lit != _want) \
                        or (_lit is None and _want != 'linear'):
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
    # ---------------------------------------------------------------------
    # L0-2：把"**已能静态证明与源码现值相等**"的 skipped 字段剔除。
    #
    # 为什么必须剔：`skipped` 是"原位写不了"的清单，调用方据此判 best_effort +
    # needs_block → auto 模式退到块模式**改写源码**。但"写不了"不等于"有差异"：
    # 目标值就是源码里那个字面量时（describe 导出的参数喂回来就是这种情况），
    # 插块只会往一份本来无需改动的脚本里加东西（L0 复测 3 条 M3 残余：
    # `gallery/misc/logos2.py` 的 add_axes 在建图区间之外、`20154193__Cylinder.py`
    # 与 `23013498__zndo_figs_pca.py` 的 cax）。证明不了（找不到字面量/值不同）
    # 就原样保留 —— 保守退块的旧行为不变。
    # ---------------------------------------------------------------------
    _kept: List[tuple] = []
    # 轴的 is_colorbar 标记（cax 的"建图调用"常落在函数里，pos 证明需要这个标记，
    # 见 _pos_literal_equal 的 (d) 路径）
    _cb_by_i = {}
    for _a in (data.get('axes') or []):
        if isinstance(_a.get('index'), int):
            _cb_by_i[_a['index']] = bool(_a.get('is_colorbar'))
    for _i, _field, _val in skipped:
        if _proved_equal(tree, _i, _field, _val, var_for, idx_map, _fi, src=src,
                         is_cb=bool(_cb_by_i.get(_i))):
            continue
        _kept.append((_i, _field, _val))
    return out, covered, _kept


# --------------------------------------------------------------------------
# 默认值等价证明（T15 / T14-D1）
# --------------------------------------------------------------------------
# 源码里**压根没有**任何能决定这个字段的写法时，它的实际渲染值就是 matplotlib 的默认值；
# 目标值若等于该默认值，就能在**不重跑脚本**（--verify-fast）的前提下判"与源码现值相同"。
#
# 这是 T14-D1 的核心：describe 导出的参数把 title_fontsize / label_fontsize /
# tick_fontsize / spines / grid / xscale… 的**当前值**都写进去了，而大量脚本里根本没有
# 对应写法（例如没有 set_title(..., fontsize=)）—— 旧行为一律判"写不了 + 证不出相等"
# → auto 退块 → 往一份**无需改动**的脚本里插调整块（describe→apply 这条主推 agent
# 路径上很常见）。
#
# 前提检查用**白名单 + AST 精确匹配**（T18：原来是子串黑名单，"size" 当子串会误伤
# figsize=/markersize= 之类，把所有默认值证明一次性打死）。
#
# 允许"等于默认值"的结论，当且仅当源码里**所有相关写法**要么不存在、要么带的字面量
# 恰好等于目标值；认不出（变量、表达式、**kwargs）一律放弃证明 → 回退保守退块。
_FONT_KWARGS = ('fontsize', 'size', 'labelsize', 'titlesize', 'fontsize_y')
# 每个字号字段"可能改到它"的调用（attr 或裸名）
_FONT_CALLS = {
    'title_fontsize': ('set_title', 'title', 'suptitle'),
    # label_fontsize 未显式给 y 时 _label_fontsizes 会用 x 的值同时写 y ⇒ 两边都看
    'label_fontsize': ('set_xlabel', 'xlabel', 'set_ylabel', 'ylabel'),
    'label_fontsize_y': ('set_ylabel', 'ylabel'),
    'tick_fontsize': ('tick_params', 'set_xticklabels', 'set_yticklabels'),
    # T23：y 刻度字号（单一 labelsize 同时管两边 ⇒ 也要看 tick_params）
    'tick_fontsize_y': ('tick_params', 'set_yticklabels'),
}
_SAFE_SCALES = ('linear', 'log', 'symlog', 'logit')


def _is_rcparams_receiver(node):
    """``rcParams`` / ``mpl.rcParams`` / ``plt.rcParams`` 这类接收者。"""
    if isinstance(node, ast.Name):
        return node.id == 'rcParams'
    return (isinstance(node, ast.Attribute) and node.attr == 'rcParams'
            and isinstance(node.value, ast.Name))


def _is_style_receiver(node):
    if getattr(node, 'attr', None) == 'style':
        return True
    return isinstance(node, ast.Name) and node.id in ('style', 'sns', 'seaborn')


def _style_or_rc_write(tree):
    """源码里有没有能改默认样式/默认字号的写法（AST 精确识别）。

    覆盖：``rcParams[...] = ...`` / ``rcParams.update({...})`` / ``plt.rc(...)`` /
    ``mpl.rc(...)`` / ``plt.style.use(...)`` / ``sns.*``（seaborn 的任何调用都会
    动全局样式）。这些一律让"等于默认值"的结论失效。
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            attr = getattr(f, 'attr', None)
            name = f.id if isinstance(f, ast.Name) else None
            if attr == 'rc' or name == 'rc':
                return True
            if attr == 'update' and _is_rcparams_receiver(f.value):
                return True
            if attr in ('set_style', 'set_theme', 'set_context', 'reset_orig',
                        'set_palette'):
                return True
            if attr == 'use' and _is_style_receiver(f.value):
                return True
            if _is_style_receiver(f.value if isinstance(f, ast.Attribute) else f):
                return True          # sns.xxx(...) / seaborn.xxx(...)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Subscript) and _is_rcparams_receiver(t.value):
                    return True
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Subscript) \
                    and _is_rcparams_receiver(node.target.value):
                return True
    return False


def _kw_font_conflict(node, val):
    """这个调用里带的字号关键字是否与目标值冲突（认不出一律算冲突）。"""
    for kw in node.keywords:
        if kw.arg is None:
            return True                       # **kwargs：认不出
        if kw.arg in _FONT_KWARGS:
            if _is_num_literal(kw.value):
                if not _num_close(_lit_num(kw.value), val):
                    return True
            else:
                return True                   # 变量/表达式：认不出
        elif kw.arg in ('fontproperties', 'fontdict', 'font_properties'):
            # fontdict/fontproperties 里也可能带 size；能静态读出来的才放行
            got = _font_size_from_dictlike(kw.value)
            if got is None or not _num_close(got, val):
                return True
    return False


def _font_size_from_dictlike(node):
    """从 ``{'size': 12}`` / ``fontdict`` 里取出 size 字面量；取不到返回 None。"""
    if isinstance(node, ast.Dict):
        for k, v in zip(node.keys, node.values):
            if isinstance(k, ast.Constant) and k.value in ('size', 'fontsize') \
                    and _is_num_literal(v):
                return _lit_num(v)
        return None
    return None


def _default_proof_blocked(tree, field, val):
    """默认值证明的**前提检查**：源码里有没有"能改这个字段、而值又不是目标值"的写法。

    返回 True = 有冲突或认不出 → 放弃证明（回退保守退块）。
    """
    if _style_or_rc_write(tree):
        return True
    calls = _FONT_CALLS.get(field)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        key = getattr(f, 'attr', None) or (f.id if isinstance(f, ast.Name) else None)
        if calls is not None:
            if key in calls and _kw_font_conflict(node, val):
                return True
        elif field == 'spines':
            if key == 'set_visible':
                return not _bool_literal_eq(node, val_all_true=True)
            if key == 'set_frame_on':
                return not _bool_literal_eq(node, val_all_true=True)
            if key == 'axis':
                lit = _first_str_literal(node)
                return lit is None or lit == 'off'
        elif field == 'grid':
            if key == 'grid' and not _bool_literal_eq(node, target=bool(val)):
                return True
        elif field in ('xscale', 'yscale'):
            if key in ('set_xscale', 'set_yscale', 'xscale', 'yscale'):
                lit = _first_str_literal(node)
                if lit is None or lit != str(val).strip().lower():
                    return True
        elif field == 'cmap':
            # T23/T26：源码里任何 cmap= / set_cmap(...) 都必须"恰等于目标"，否则放弃
            # "等于默认值"的结论（认不出一律算冲突）。
            if key == 'set_cmap':
                lit = _first_str_literal(node)
                if lit is None or lit != str(val).strip().lower():
                    return True
            elif key in ('imshow', 'pcolormesh', 'contourf', 'contour', 'scatter',
                         'hexbin', 'pcolor', 'matshow', 'colorbar'):
                for kw in node.keywords:
                    if kw.arg == 'cmap':
                        if not (isinstance(kw.value, ast.Constant)
                                and isinstance(kw.value.value, str)
                                and kw.value.value.strip().lower()
                                == str(val).strip().lower()):
                            return True
    return False


def _bool_literal_eq(node, target=None, val_all_true=False):
    """调用的布尔实参是否是**字面量且等于期望**（认不出一律 False）。"""
    want = True if val_all_true else bool(target)
    cands = list(node.args) + [k.value for k in node.keywords
                               if k.arg in ('b', 'visible', 'on')]
    for c in cands:
        if isinstance(c, ast.Constant) and isinstance(c.value, bool):
            return c.value is want
    return False


def _first_str_literal(node):
    for c in list(node.args) + [k.value for k in node.keywords]:
        if isinstance(c, ast.Constant) and isinstance(c.value, str):
            return c.value.lower()
    return None


def _default_pt(val):
    """rcParams 里的字号（数字或 'large'/'medium' 这类相对名）→ 点值。

    相对名必须走 matplotlib 自己的换算（`large` = 1.2 × `font.size`），不要自己写表。
    """
    try:
        return float(val)
    except (TypeError, ValueError):
        pass
    try:
        from matplotlib.font_manager import FontProperties
        return float(FontProperties(size=str(val)).get_size_in_points())
    except (ImportError, ValueError, TypeError, KeyError):
        return None


def _use_matplotlib_default(field, target, src, tree=None):
    """源码里没有任何相关写法时，目标值是否**就是** matplotlib 默认值。"""
    if not src or target is None:
        return False
    if field not in ('title_fontsize', 'label_fontsize', 'label_fontsize_y',
                     'tick_fontsize', 'tick_fontsize_y', 'spines', 'grid',
                     'xscale', 'yscale', 'cmap'):
        return False
    if tree is not None and _default_proof_blocked(tree, field, target):
        return False
    try:
        import matplotlib as _mpl
    except ImportError:
        return False
    rc = _mpl.rcParams
    if field == 'title_fontsize':
        pt = _default_pt(rc['axes.titlesize'])
    elif field in ('label_fontsize', 'label_fontsize_y'):
        pt = _default_pt(rc['axes.labelsize'])
    elif field == 'tick_fontsize':
        ptx, pty = _default_pt(rc['xtick.labelsize']), _default_pt(rc['ytick.labelsize'])
        return bool(ptx is not None and pty is not None
                    and _num_close(target, ptx) and _num_close(target, pty))
    elif field == 'tick_fontsize_y':
        # T23：y 刻度字号的默认值（与 xtick/ytick.labelsize 同源）
        pty = _default_pt(rc['ytick.labelsize'])
        return bool(pty is not None and _num_close(target, pty))
    elif field == 'cmap':
        # T26：没写 cmap 时 imshow 用的就是 rcParams['image.cmap']（默认 'viridis'）。
        # 冲突检查（_default_proof_blocked）已经确保源码里没有别的 cmap= 写法。
        return bool(isinstance(target, str)
                    and target.strip().lower()
                    == str(rc['image.cmap']).strip().lower())
    elif field == 'grid':
        # 进程里的 rcParams 若被改过（别的模块动过样式），就没资格代表"脚本的默认"
        return bool(target) is bool(rc['axes.grid']) \
            and bool(rc['axes.grid']) is bool(_mpl.rcParamsDefault['axes.grid'])
    elif field == 'spines':
        # 四条 spine 默认都可见；目标里任何一条要隐藏 → 证不出（必须退块去写）
        return isinstance(target, dict) and all(
            bool(target.get(k, True)) for k in ('left', 'right', 'bottom', 'top'))
    else:                                     # xscale / yscale
        return str(target).strip().lower() == 'linear'
    return bool(pt is not None and _num_close(target, pt))


def _cmap_literal_equal(tree, val, nm):
    """静态证明：源码里这个轴的 mappable 已经用着目标 colormap（T23/T26）。

    认这些写法：``imshow/pcolormesh/contourf/scatter/... (cmap='X')`` 与
    ``set_cmap('X')``。只在**能确认接收者是本轴变量**时认（``nm`` 不是字符串 → 不证），
    免得拿别的轴的 cmap 当本轴的现值。
    """
    if not isinstance(nm, str) or not isinstance(val, str):
        return False
    want = val.strip().lower()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        recv = node.func.value
        if not (isinstance(recv, ast.Name) and recv.id == nm):
            continue
        if node.func.attr == 'set_cmap':
            cands = list(node.args)
        else:
            cands = [k.value for k in node.keywords if k.arg == 'cmap']
        for c in cands:
            if isinstance(c, ast.Constant) and isinstance(c.value, str) \
                    and c.value.strip().lower() == want:
                return True
    return False


def _legend_literal_equal(tree, val, nm):
    """静态证明：图例的 loc 已经等于目标（T23/T26）。

    **只证 loc**：labels / n_handles 是运行时从图元收集的，静态读不出来 —— 那部分交给
    实测回比（`_pv_live_field`，需要采集侧给 legend 状态）。loc 缺省（None）时不证。
    """
    if not isinstance(nm, str) or not isinstance(val, dict):
        return False
    want = val.get('loc')
    if not isinstance(want, str):
        return False
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == 'legend'
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == nm):
            continue
        cands = [k.value for k in node.keywords if k.arg == 'loc'] + list(node.args)
        for c in cands:
            if isinstance(c, ast.Constant) and isinstance(c.value, str) \
                    and c.value.strip().lower() == want.strip().lower():
                return True
    return False


def _proved_equal(tree, axis_i, field, val, var_for, idx_map, fig_index, src=None,
                  is_cb=False):
    """静态证明"参数里的这个字段值**已经等于**源码现值"（不跑脚本）。

    只做**等价证明**，不做不等价证明：返回 True 表示"这次没有任何差异"，返回 False
    表示"证不出来"（调用方保持旧行为）。见 apply_inplace 末尾的 L0-2 说明。

    两条取证路径：
      ① **字面量证明**：源码里那个写法（``add_axes`` 的 pos / ``set_title(fontsize=)``
         / ``grid(True)``）的字面量已经等于目标值 ⇒ 现值就是目标值；
      ② **默认值证明**（T15）：源码里压根没有能决定该字段的写法 ⇒ 实际渲染值就是
         matplotlib 默认值；目标值等于默认值也算"已经一致"（T14-D1 的
         title/label/tick_fontsize 与 spines 就靠它，`--verify-fast` 下不重跑脚本）。
    """
    f = _bare_field(field)
    nm0 = var_for.get(axis_i)
    if f == 'cmap':
        # ① 源码里写了 cmap=… 且与目标一致；② 没写 → 用默认 colormap 判等
        return (_cmap_literal_equal(tree, val, nm0)
                or _use_matplotlib_default(f, val, src, tree=tree))
    if f == 'legend':
        return (_legend_literal_equal(tree, val, nm0)
                or _use_matplotlib_default(f, val, src, tree=tree))
    if f == 'pos':
        if _pos_literal_equal(tree, val, var_for.get(axis_i), idx_map,
                              axis_i, fig_index, is_cb=is_cb):
            return True
        return _use_matplotlib_default(f, val, src, tree=tree)
    # 字号 / grid：同一变量、同一调用、字面量已等于目标值 ⇒ 源码现值就是目标值。
    # T18：matplotlib 的 Text 同时接受 `fontsize=` 与 `size=`（后者是别名）——
    # 字面量路径必须两个都认，否则 `set_title(..., size=12)` 会被当成"没有写法"。
    _attr_kw = {'title_fontsize': (('set_title',), ('fontsize', 'size')),
                'label_fontsize': (('set_xlabel',), ('fontsize', 'size')),
                'label_fontsize_y': (('set_ylabel',), ('fontsize', 'size')),
                'tick_fontsize': (('tick_params', 'set_xticklabels'),
                                  ('labelsize', 'fontsize', 'size')),
                # T23：y 刻度字号的等值证明 —— 单一 `tick_params(labelsize=N)` 同时管
                # x/y，所以那个字面量等于目标时，y 的现值也就是目标（T26 的干净往返
                # 就是这一支：describe 导出 tick_fontsize/tick_fontsize_y 都一样）。
                'tick_fontsize_y': (('tick_params', 'set_yticklabels'),
                                    ('labelsize', 'fontsize', 'size'))}
    nm = var_for.get(axis_i)
    if f in _attr_kw and isinstance(nm, str):
        attrs, kwargs = _attr_kw[f]
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr in attrs
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == nm):
                continue
            for kw in node.keywords:
                if kw.arg in kwargs and _is_num_literal(kw.value) \
                        and _num_close(_lit_num(kw.value), val):
                    return True
    elif f == 'grid' and isinstance(nm, str):
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == 'grid'
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == nm
                    and node.args and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, bool)
                    and node.args[0].value is bool(val)):
                return True
    # ② 路径没有命中 → 试默认值证明（源码里没有相关写法时才算数）
    return _use_matplotlib_default(f, val, src, tree=tree)


def _pos_literal_equal(tree, pos, nm, idx_map, axis_i, fig_index, is_cb=False):
    """``pos`` 的静态等价证明（L0-2 三种形态）。返回 True = 源码现值已等于目标值。

    三条取证路径（任一成立即可）：
      (a) 轴变量名已知 → 找**全文**里该变量的 ``add_axes/add_subplot`` 字面量
          （`logos2.py`：`ax = fig.add_axes((0, 0, 1, 1))` 在函数里、位于建图区间**之前**，
          原位查找够不到，但字面量就是目标值）；
      (b) 轴变量名未知 → 用全局「轴变量 → 序号」映射反查同名变量，再走 (a)；
      (c) 仍证不出 → 只看**本图区间**内的建图调用按源码序的第 axis_i 个（或唯一一个）
          （`Cylinder.py` / `zndo_figs_pca.py`：colorbar 槽轴 cax，字面量就在本图里）。
    值不同 / 找不到字面量 → False（保持"保守退块"的旧行为）。
    """
    if not pos or len(pos) < 4:
        return False
    want = [float(pos[k]) for k in range(4)]

    def _hit(nums):
        return all(_num_close(nums[k], want[k]) for k in range(4))

    names = set()
    if isinstance(nm, str):
        names.add(nm)
    if not names and isinstance(axis_i, int) and idx_map:
        for _nm, _i in (idx_map or {}).items():
            if _i == axis_i:
                names.add(_nm)
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Attribute)
                and node.value.func.attr in ('add_axes', 'add_subplot')):
            continue
        if not any(isinstance(t, ast.Name) and t.id in names
                   for t in node.targets):
            continue
        _a = node.value.args[0] if node.value.args else None
        _nums = _seq4_nums(_a)
        if _nums is not None and _hit(_nums):
            return True
    # (c) 本图区间内的建图调用（含 cax）
    _span_seq = []
    if isinstance(fig_index, int):
        lo, hi = _fig_span(tree, fig_index)
        for node in ast.walk(tree):
            if (isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)
                    and isinstance(node.value.func, ast.Attribute)
                    and node.value.func.attr in ('add_axes', 'add_subplot')
                    and lo <= node.lineno < hi):
                _a = node.value.args[0] if node.value.args else None
                _nums = _seq4_nums(_a)
                if _nums is not None:
                    _span_seq.append(_nums)
        if len(_span_seq) == 1 and _hit(_span_seq[0]):
            return True
        if (isinstance(axis_i, int) and 0 <= axis_i < len(_span_seq)
                and _hit(_span_seq[axis_i])):
            return True
    # (d) colorbar 宿主轴（cax）常**在函数里**创建（`cbar_ax = fig.add_axes([...])` 用的
    #     是传进来的 fig），静态"建图区间"根本圈不到它 —— T15 语料实测：
    #     `gallery/scikitlearn/model_selection/plot_successive_halving_heatmap.py`
    #     （唯一那张图的 cax 在 make_heatmap() 里）。cax 的身份标记是它被
    #     `fig.colorbar(..., cax=X)` 引用 —— 从这里反查名字，再按源码序比字面量。
    #     触发条件二选一：参数标了 is_colorbar；或本图区间里**根本没有**可比字面量
    #     （seq 空 —— 轴是 subplots 网格 / 或整个落在区间外）。区间里已经有字面量时
    #     不走这条，避免把"普通轴"的 pos 误判成 cax 的字面量。
    cax_names = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == 'colorbar'):
            for kw in node.keywords:
                if kw.arg == 'cax' and isinstance(kw.value, ast.Name):
                    cax_names.add(kw.value.id)
    if cax_names and (is_cb or not _span_seq):
        calls = []
        for node in ast.walk(tree):
            if (isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)
                    and isinstance(node.value.func, ast.Attribute)
                    and node.value.func.attr == 'add_axes'
                    and any(isinstance(t, ast.Name) and t.id in cax_names
                            for t in node.targets)):
                _a = node.value.args[0] if node.value.args else None
                _nums = _seq4_nums(_a)
                if _nums is not None:
                    calls.append((node.lineno, _nums))
        calls.sort(key=lambda x: x[0])
        nums = [c[1] for c in calls]
        if len(nums) == 1 and _hit(nums[0]):
            return True
        if (isinstance(axis_i, int) and 0 <= axis_i < len(nums)
                and _hit(nums[axis_i])):
            return True
    # (e) **内联**（没赋值给名字）的 add_axes 字面量：``plt.colorbar(thePlot,
    #     cax=fig.add_axes([0.78, 0.18, 0.2, 0.03]))`` —— 上面 (a)~(d) 全都够不到它，
    #     因为它们都要求字面量挂在 ``ast.Assign`` 上。T67/A 的语料
    #     ``big/zenodo/3490985__cloudFractionZonalMeanProfiles.py`` 正是这个形态：
    #     参数给的 axes[0].pos 与源码现值逐位相同，却因为"证不出"而走 best_effort →
    #     自动退块 → 往文件里插一段 zip 循环（值本来就没变，白改文件；而且 zip 打到
    #     的是运行时第一张轴，见 _pos_block_target_ok 的说明）。
    _inline = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == 'colorbar'):
            continue
        for kw in node.keywords:
            if kw.arg != 'cax' or not isinstance(kw.value, ast.Call):
                continue
            _a = kw.value.args[0] if kw.value.args else None
            _nums = _seq4_nums(_a)
            if _nums is not None:
                _inline.append((kw.value.lineno, _nums))
    _inline.sort(key=lambda x: x[0])
    _inline_nums = [n for _, n in _inline]
    if len(_inline_nums) == 1 and _hit(_inline_nums[0]):
        return True
    if (isinstance(axis_i, int) and 0 <= axis_i < len(_inline_nums)
            and _hit(_inline_nums[axis_i])):
        return True
    return False


def _seq4_nums(node):
    """4 个数值字面量（``add_axes([...])`` / ``add_axes((...))``）→ list；否则 None。"""
    if not isinstance(node, (ast.List, ast.Tuple)) or len(node.elts) != 4:
        return None
    if not all(_is_num_literal(e) for e in node.elts):
        return None
    return [float(_lit_num(e)) for e in node.elts]


def _backup_dir(script, params_path=None):
    """备份目录：**永远**是脚本旁边的 ``.tweak_params``。

    审计 RC-10：备份路径过去跟着 ``--params`` 走（``dirname(params_path)``），于是
    "用自定义参数文件写回 → 直接 revert"会 rc=2 报"还没有可回退的备份"，用户以为
    这个功能不可用。参数文件可以来自任何地方，而"备份属于这个脚本"——定位规则必须
    以**脚本**为锚，且 apply 与 revert 共用同一套（revert.backup_path 走的就是这里）。
    """
    return os.path.dirname(os.path.abspath(params.params_path(script)))


def _backup_path(script, params_path=None, pid=None):
    """备份文件放脚本旁的 `.tweak_params/` 里，不散落在代码目录。

    pid=None → 固定名 `<stem>.tweak.bak`：README、测试和用户习惯都依赖它，
    代表"最近一次备份"。
    传 pid → `<stem>.tweak.<pid>.bak`：本次专属副本。并发跑两个 apply 时固定名
    会被后写者覆盖（t5-S4 / t6-B2 实测：出事后唯一的恢复点已经不可信），所以
    再留一份谁也覆盖不了的。

    ``params_path`` 只为**向后兼容**保留（0.1.8 曾经按它定位，见
    ``_backup_path_legacy``）；新备份一律落在脚本旁。
    """
    d = _backup_dir(script, params_path)
    stem = os.path.splitext(os.path.basename(script))[0]
    if pid is None:
        return os.path.join(d, stem + '.tweak.bak')
    return os.path.join(d, '%s.tweak.%s.bak' % (stem, pid))


def _backup_path_legacy(script, params_path):
    """0.1.8 的定位规则：备份落在 ``--params`` 文件旁边（RC-10 的兼容读口）。

    只被 revert 当作"主位置找不到时再试一次"的兜底，新写回不再往这里放东西。
    """
    d = os.path.dirname(os.path.abspath(params_path))
    stem = os.path.splitext(os.path.basename(script))[0]
    return os.path.join(d, stem + '.tweak.bak')


def _prune_backups(script, params_path, keep=5):
    """裁掉多余带 pid 的备份 —— 但**永远保留最早那一份**；固定名那份也不动。

    保留最早一份是 ``revert --first`` 的前提：固定名备份只代表"最近一次写回之前"，
    反复 apply 之后"最初的原文"就再也拿不回来了（审计 RC-7：这是 M6 可逆性
    全灭的级联源头）。多留一份的成本是几十 KB。
    """
    d = _backup_dir(script, params_path)
    stem = os.path.splitext(os.path.basename(script))[0]
    pat = re.compile(r'^%s\.tweak\.(\d+)\.bak$' % re.escape(stem))
    try:
        files = [os.path.join(d, n) for n in os.listdir(d) if pat.match(n)]
    except OSError:
        return
    files.sort(key=lambda p: (os.path.getmtime(p), p))       # 早 → 晚
    if not files:
        return
    oldest = files[0]
    for old in (files[:-keep] if keep > 0 else files):
        if old == oldest:
            continue
        try:
            os.remove(old)
        except OSError:
            pass


def _long_path(p):
    """Windows 上给绝对路径加 ``\\\\?\\`` 前缀，绕开 MAX_PATH(260) 限制。

    T41：深目录 / OneDrive 长前缀下，`<stem>.tweak.<pid>.bak` 会超过 260 字符，
    `shutil.copy2` 直接抛 `OSError`（还常带误导性的 errno 2） —— pid 副本静默缺失，
    `revert --first` 于是回落到固定名备份却仍报 `first: true`。
    `\\\\?\\` 前缀把上限抬到约 32767 字符，路径长度问题从根上消掉（只在 Windows、
    且路径是绝对路径时加；UNC 用 `\\\\?\\UNC\\` 形式）。
    """
    if os.name != 'nt' or not isinstance(p, str) or p.startswith('\\\\?\\'):
        return p
    ap = os.path.abspath(p)
    if ap.startswith('\\\\'):                     # UNC：\\server\share → \\?\UNC\server\share
        return '\\\\?\\UNC' + ap[1:]
    if len(ap) < 240:
        # 只在**确实可能超限**时才加：`\\?\\` 会绕开 Win32 的路径规范化，
        # 短路径没必要冒这个风险（也让 _long_path 对普通路径保持恒等）。
        return p
    if len(ap) >= 3 and ap[1] == ':':             # 本地驱动器路径才加前缀
        return '\\\\?\\' + ap
    return p


def _make_backup(script, params_path, keep=5, warnings=None):
    """写备份并返回固定名路径；失败抛 OSError。

    写两份：固定名（最近一次，兼容既有约定）+ 带 pid 的专属副本（并发时不互相
    覆盖），并把旧的 pid 副本裁到最近 keep 份。

    ``warnings``（T41）：pid 副本失败时**不再静默吞掉**（过去是 `except OSError: pass`，
    长路径下副本根本写不出来而无人知晓）—— 把路径与 errno 记进调用方的 warning 列表。
    """
    fixed = _backup_path(script, params_path)
    os.makedirs(os.path.dirname(fixed), exist_ok=True)
    _fixed_long = _long_path(fixed)
    if os.path.exists(_fixed_long):
        # T71：固定名备份是"**批前原件**"。跨**独立 apply 调用**也必须首份优先 ——
        # 第二次写盘再复制一次就把中间态盖了上去，`revert` 因此只能回到"第一次写回后"
        # 的状态（t69 的 2 例实测就是这个）。用户 revert 之后备份被消费，下次会自然重备。
        pass
    else:
        shutil.copy2(script, _fixed_long)
    _pid_bak = _backup_path(script, params_path, os.getpid())
    try:
        shutil.copy2(script, _long_path(_pid_bak))
    except OSError as e:
        # 专属副本是加强项（并发保护 / `revert --first` 的取数来源），失败不影响
        # 主流程，但**必须让人知道**：`--first` 会因此没有可用副本。
        if warnings is not None:
            warnings.append('pid 专属备份写不出来（%s，errno=%s：%s）；'
                            '`revert --first` 将没有可用副本，请用默认 revert'
                            % (_pid_bak, getattr(e, 'errno', '?'), e))
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
    判据用与正向写盘同一套 **sha256 内容指纹**（两侧不对称是最容易漏的 bug）。
    """
    if expect is not None:
        _now = _disk_fingerprint(script)
        if _now is not None and _now != _sha256_text(expect):
            # 外部改动守卫（0.1.10）：不回滚 = 磁盘上留下的是**用户保存的版本**。
            return ('%s（本次未回滚；磁盘上保留的是你保存的版本，备份在 %s）'
                    % (_msg.t('external_modified'), backup))
    d = os.path.dirname(os.path.abspath(script)) or '.'
    try:
        _mode = os.stat(script).st_mode & 0o7777
    except OSError:
        _mode = None
    fd, tmp = tempfile.mkstemp(prefix='.mpltweak-', suffix='.tmp', dir=d)
    try:
        with os.fdopen(fd, 'wb') as f:
            with open(backup, 'rb') as _b:
                shutil.copyfileobj(_b, f)
            f.flush()
            os.fsync(f.fileno())               # 先落盘，再替换
        if _mode is not None:
            try:
                os.chmod(tmp, _mode)
            except OSError:
                pass
        os.replace(tmp, script)
        return '已回滚'
    except OSError as e:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        return ('回滚也失败了（%s: %s）—— 脚本目前是改坏的状态，'
                '备份在 %s，请手动恢复' % (type(e).__name__, e, backup))


# ---------------------------------------------------------------------------
# 外部改动守卫（0.1.10）：把"用户在编辑器里的保存"保住。
#
# 为什么必须做：`apply --write` 的源码在流程**开头**读一次，之后最长要等（重跑验证的）
# 300 秒才落盘。用户在这段时间里回编辑器保存一次是极自然的动作（他以为工具已经改完），
# 而工具的**后续落盘**（验证失败后的回滚、块重试的二次写盘）会把它**静默覆盖** ——
# 对科研用户这是最不可接受的一类数据丢失。回滚路径早有 expect 检测（_rollback_note），
# 正向写盘路径过去反而没有，两侧不对称。
#
# 判据 = **内容指纹（sha256）**：读源码时算一份，落盘前重算一份，不一致就停手。
# 为什么用指纹而不是 size+mtime：编辑器保存常常只差一个字符，而 mtime 精度、
# "保存了但内容没变"（编辑器整体重写盘）都会骗过粗判据；sha256 只认内容。
# ---------------------------------------------------------------------------
def _sha256_text(text):
    """文本内容的 sha256 指纹（十六进制）。``None`` → ``None``。"""
    if text is None:
        return None
    return hashlib.sha256(text.encode('utf-8', 'surrogatepass')).hexdigest()


def _disk_fingerprint(script):
    """磁盘上脚本当前的 sha256（与 params.read_text 同一解码口径）。

    读不了（不存在 / 全是坏字节）→ ``None``：调用方按"没改过"放行，真写不进去
    会由写盘异常兜底 —— 守卫不该在读取失败时把正常写回拦下。
    """
    try:
        cur, _ = params.read_text(script)
    except (OSError, UnicodeDecodeError):
        return None
    return _sha256_text(cur)


def _external_result(script, expect_sha, found_sha, backup=None):
    """外部改动 → 统一的拒绝结果（人读文案 + 机器可读字段）。

    调用方直接 ``res.update(...)``：``reason`` 变成 ``external_modified``，
    ``err`` 是给人看的一句话（含可照抄的下一步），另有 ``external_modified``
    布尔标记与两侧指纹，供 agent / 嵌入式调用者复核"到底哪边变了"。
    """
    return {
        'reason': EXTERNAL_MODIFIED,
        'external_modified': True,
        'err': _msg.t('external_modified'),
        'next_step': 'mpltweak apply %s --write' % os.path.abspath(script),
        'sha256_expected': expect_sha,
        'sha256_found': found_sha,
        'changes': [],
        'applied': False,
        'verified': None,
        'backup': backup,
    }


def _check_external_edit(script, src, src_sha=None, backup=None):
    """落盘前的核心判据。返回 ``None`` = 可以落盘；返回 dict = 拒绝（直接 update）。

    ``src``：流程**开头**读到的那份源码（可能已经等了几分钟验证）。
    ``src_sha``：调用方已经算过指纹就传进来，省一次哈希。
    """
    expect = src_sha if src_sha else _sha256_text(src)
    found = _disk_fingerprint(script)
    if expect is None or found is None:
        return None                     # 读不了 → 不拦（见 _disk_fingerprint）
    if found == expect:
        return None
    return _external_result(script, expect, found, backup=backup)


def _guard_external_edit(script, src):
    """兼容入口：返回一句原因文本（``None`` = 放行）。

    保留旧签名是因为它已被 tests/test_writeback.py §86 直接调用；新代码请用
    :func:`_check_external_edit`（能拿到 reason / 指纹等结构化字段）。
    """
    _r = _check_external_edit(script, src)
    return None if _r is None else _r['err']


def _scale_literal(tree, nm, field):
    """源码里能改这个轴刻度的 ``set_xscale(...)`` / ``set_yscale(...)`` 字面量。

    只在**能确认接收者是本轴变量**时返回（``nm`` 不是字符串 → None）：判不出归属时
    宁可不认（回退保守路径），也不要拿别的轴的刻度当本轴的现值。
    """
    if not isinstance(nm, str):
        return None
    meth = 'set_' + field
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == meth
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == nm):
            continue
        for c in list(node.args) + [k.value for k in node.keywords]:
            if isinstance(c, ast.Constant) and isinstance(c.value, str):
                return c.value.strip().lower()
    return None


def _bare_field(f):
    """skipped 里的字段名可能带括号说明（``'pos（该轴是 colorbar 的宿主…）'``），
    分派/回比时只认裸字段名。"""
    return str(f).split('（')[0].strip()


def _pending_fields(skipped):
    """把 skipped ``[(轴序号, 字段, 目标值)]`` 聚成 ``{轴序号: {裸字段名}}``。"""
    out: Dict[int, set] = {}
    for i, f, _v in skipped:
        if isinstance(i, int):
            out.setdefault(i, set()).add(_bare_field(f))
    return out


# ``verify.compare`` 能逐字段回比的字段（判不判得动另说：aspect 锁定轴的 pos、
# cmap/lines/legend 都不能按它的口径比 —— 我在 _pv_live_field 里自己处理）。
_COMPARABLE_FIELDS = {'pos', 'title_fontsize', 'label_fontsize', 'tick_fontsize',
                      'tick_fontsize_y', 'grid', 'spines', 'clim', 'xlim', 'ylim',
                      'cell',
                      # T26 新采集的图元级字段（cmap / lines / legend）：写回侧原位
                      # 写不了它们，但**等值时不该退块** —— 描述产物本就是从实测来的
                      # （T23 跨线回归：干净往返被判 best_effort + 插块）。
                      'cmap', 'lines', 'legend'}
_LIVE_ONLY_FIELDS = {'xscale', 'yscale'}


def _legend_differs(live, want):
    """图例状态是否不同（labels / n_handles / loc / fontsize，缺项不比）。"""
    if not isinstance(live, dict) or not isinstance(want, dict):
        return True
    if 'labels' in want \
            and list(live.get('labels') or []) != list(want.get('labels') or []):
        return True
    if 'n_handles' in want and live.get('n_handles') != want.get('n_handles'):
        return True
    if want.get('loc') is not None and live.get('loc') != want.get('loc'):
        return True
    if want.get('fontsize') is not None:
        try:
            if abs(float(live.get('fontsize')) - float(want['fontsize'])) \
                    > _verify.FS_TOL:
                return True
        except (TypeError, ValueError):
            return True
    return False


def _lines_differ(live, want):
    """逐条比 linewidth / color（缺项不比）。"""
    if not isinstance(live, list) or not isinstance(want, list):
        return True
    if len(live) != len(want):
        return True
    for ev_, av_ in zip(live, want):
        if not isinstance(ev_, dict) or not isinstance(av_, dict):
            return True
        if av_.get('linewidth') is not None:
            try:
                if abs(float(ev_.get('linewidth')) - float(av_['linewidth'])) > 0.01:
                    return True
            except (TypeError, ValueError):
                return True
        if av_.get('color') is not None \
                and str(ev_.get('color')) != str(av_.get('color')):
            return True
    return False


def _pv_live_field(field, e, a, data, i):
    """单个字段"参数值 vs 实测值"是否不同；判不了返回 None。

    与 ``verify.compare`` 的口径一致（同一批容差常量），但**多覆盖两处它不比的**：
      * ``pos``：即使轴是 aspect 锁定的（imshow / cartopy）也要比 —— compare 因为
        "锁定轴的位置会被 apply_aspect 重算、比了必假失败"而不比，但这里的用途是
        "参数是否已经描述当前状态"，两次运行同一脚本的 pos 是可比的（确定性）。
        不比的话，"拖了 imshow 轴的位置"会被判成"参数与源码一致"→ 静默丢（E4）。
      * ``xscale`` / ``yscale``：compare 完全不比，这里按字符串比。
    """
    if field == 'pos':
        ep, ap = e.get('pos'), a.get('pos')
        if not (isinstance(ep, (list, tuple)) and isinstance(ap, (list, tuple))):
            return None
        if len(ep) != len(ap):
            return True
        for x, y in zip(ep, ap):
            try:
                if abs(float(x) - float(y)) > _verify.POS_TOL:
                    return True
            except (TypeError, ValueError):
                return None
        return False
    if field in ('title_fontsize', 'tick_fontsize'):
        ev, av = e.get(field), a.get(field)
        if ev is None or av is None:
            return None
        try:
            return abs(float(ev) - float(av)) > _verify.FS_TOL
        except (TypeError, ValueError):
            return None
    if field == 'tick_fontsize_y':
        # T73（船长裁决 #1：判据层也零兼容）：**不再拿 x 的实测值当 y 的** ——
        # 实测侧没有 `tick_fontsize_y` 就"没证据"（返回 None = 不判），而不是借 x：
        # 借 x 会把"其实没落地/落地成别的值"误判成"已经落地"（只修写回不修判据＝骗自己）。
        ev = e.get('tick_fontsize_y')
        if ev is None:
            return None
        av = a.get('tick_fontsize_y')
        if av is None:
            return None
        try:
            return abs(float(ev) - float(av)) > _verify.FS_TOL
        except (TypeError, ValueError):
            return None
    if field == 'cmap':
        # T26：图元级字段。**实测侧**的键名带下划线（`_cmap`/`_legend`/`_lines`），
        # describe 才把它们映射成 cmap/legend/lines —— 参数侧与实测侧两种拼法都认
        # （两边都试，因为 _pv_live_field 的 e=参数项、a=实测项）。
        ev, av = e.get('cmap', e.get('_cmap')), a.get('cmap', a.get('_cmap'))
        if ev is None or av is None:
            return None
        return str(ev) != str(av)
    if field == 'legend':
        ev, av = (e.get('legend', e.get('_legend')),
                  a.get('legend', a.get('_legend')))
        if ev is None or av is None:
            return None
        return _legend_differs(av, ev)
    if field == 'lines':
        ev, av = (e.get('lines', e.get('_lines')),
                  a.get('lines', a.get('_lines')))
        if not isinstance(ev, list) or not isinstance(av, list):
            return None
        return _lines_differ(av, ev)
    if field == 'label_fontsize':
        ev = e.get('label_fontsize')
        if ev is None:
            return None
        # T73（零兼容）：y 没写时**不把 x 的值当 y 的合法值** —— 候选只剩 x。
        if a.get('label_fontsize_y') is None:
            cands = [a.get('label_fontsize')]
        else:
            cands = [a.get('label_fontsize'), a.get('label_fontsize_y')]
        cands = [c for c in cands if c is not None]
        if not cands:
            return None
        for c in cands:
            try:
                if abs(float(ev) - float(c)) <= _verify.FS_TOL:
                    return False
            except (TypeError, ValueError):
                return None
        return True
    if field == 'grid':
        ev = e.get('grid')
        if ev is None or a.get('grid') is None:
            return None
        return bool(ev) != bool(a.get('grid'))
    if field == 'spines':
        for k, v in (e.get('spines') or {}).items():
            if k in (a.get('spines') or {}):
                if bool(v) != bool(a['spines'][k]):
                    return True
        return False
    if field == 'clim':
        ec, ac = e.get('clim'), a.get('clim')
        if not ec or not ac:
            return None
        try:
            span = abs(float(ec[1]) - float(ec[0])) or 1.0
            return (abs(float(ec[0]) - float(ac[0])) > _verify.CLIM_REL_TOL * span
                    or abs(float(ec[1]) - float(ac[1])) > _verify.CLIM_REL_TOL * span)
        except (TypeError, ValueError):
            return None
    if field in ('xlim', 'ylim'):
        ex, ac = e.get(field), a.get(field)
        if not (isinstance(ex, (list, tuple)) and len(ex) == 2
                and isinstance(ac, (list, tuple)) and len(ac) == 2):
            return None
        try:
            span = abs(float(ex[1]) - float(ex[0])) or 1.0
            return (abs(float(ex[0]) - float(ac[0])) > _verify.CLIM_REL_TOL * span
                    or abs(float(ex[1]) - float(ac[1])) > _verify.CLIM_REL_TOL * span)
        except (TypeError, ValueError):
            return None
    if field in ('xscale', 'yscale'):
        ev, av = e.get(field), a.get(field)
        if ev is None or av is None:
            return None
        return str(ev) != str(av)
    if field == 'cell':
        ec, ac = e.get('cell'), a.get('cell')
        if not isinstance(ec, list) or 'cell' not in a:
            return None
        return list(ec) != list(ac) if isinstance(ac, list) else True
    # cmap / lines / legend：采集侧没有可比状态 → 判不了（保守退块）
    return None


def _pending_vs_live(script, data, pending, python, timeout, params_path,
                     script_args=None):
    """参数里"原位写不了"的字段，是否**确实与源码现值不同**。

    这是 E1 的判据核心：自动退块（apply 的 auto）只有在"参数里有目标值、
    原位确实写不了、**且该字段与源码现值不同**"时才该发生。干净往返
    （describe 直出 → apply）里 grid/spines/clim/pos 全都与源码现值一致，
    退块只会把用户脚本改脏。

    返回 True（确实不同 → 该退块）/ False（已经一致 → 什么都不用做）/
    None（重跑不了、取不到实测状态 → 判不了）。

    ``script_args``：验证重跑要透传给用户脚本的 ``sys.argv[1:]``（bug #6）——
    依赖 ``sys.argv`` 的脚本在这里拿不到参数就判不了，会保守退块。
    """
    if not pending:
        return False
    has_comparable = any(f in _COMPARABLE_FIELDS
                         for fs in pending.values() for f in fs)
    has_live_only = any(f in _LIVE_ONLY_FIELDS
                        for fs in pending.values() for f in fs)
    if not (has_comparable or has_live_only):
        # 全是采集侧没有可比状态的字段（cmap/lines/legend）：证明不了"已一致"，
        # 只能保守当成"要落地"（宁可多插一次块，也绝不静默丢请求）。
        return True
    out_path = os.path.join(os.path.dirname(os.path.abspath(params_path)),
                            '_verify_state.json')
    try:
        os.remove(out_path)
    except OSError:
        pass
    # fields_by_axis=None：让 compare 先做一遍全字段回比，它的 verdict 当**快路径**
    # （任何不一致都说明"参数≠当前状态"，那就该退块）；真正的判定在下面，
    # 用 _pv_live_field 逐字段比 —— 它比 compare 多覆盖 aspect 锁定轴的 pos
    # 与 xscale/yscale（见 _pv_live_field 的说明）。
    ok, detail = _verify.verify_semantic(script, data, python, timeout,
                                         out_path=out_path,
                                         fields_by_axis=None,
                                         figsize_edited=False,
                                         script_args=script_args)
    if ok is False:
        return True                       # 实测与参数不一致 → 这里有真改动
    if ok is None:
        return None                       # 取不到状态（脚本无头跑不通/超时）
    try:
        with open(out_path, 'r', encoding='utf-8') as f:
            actual = json.load(f)
    except (OSError, ValueError):
        return None
    live = {}
    for it in actual.get('axes') or []:
        if isinstance(it, dict) and isinstance(it.get('index'), int):
            live[it['index']] = it
    for i, fs in sorted(pending.items()):
        a = live.get(i)
        if a is None:
            # 参数里的轴在目标图里不存在（结构变了）→ 这里确实有东西要落地
            return True
        e = next((x for x in data.get('axes') or []
                  if isinstance(x, dict) and x.get('index') == i), None) or {}
        for f in sorted(fs):
            r = _pv_live_field(f, e, a, data, i)
            if r is None:
                return True               # 判不了 → 保守退块，绝不静默丢
            if r:
                return True
    return False


def _figsize_from_source(src, tree):
    """静态读出"这张图实际会用的画布尺寸"（英寸）。

    优先级：`set_size_inches(w, h)` 字面量（取最后一次） → `rcParams['figure.figsize']`
    赋值 → matplotlib 默认值。读不出字面量时返回 None（调用方按"证不出"保守处理）。
    """
    got = None
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == 'set_size_inches'
                and len(node.args) >= 2):
            vals = []
            for a in node.args[:2]:
                try:
                    vals.append(float(ast.literal_eval(a)))
                except (ValueError, TypeError, SyntaxError):
                    vals = None
                    break
            if vals and len(vals) == 2:
                got = vals          # 取最后一次（多图时保守）
    if got:
        return got
    for node in ast.walk(tree):
        if (isinstance(node, ast.Assign)
                and isinstance(node.value, (ast.List, ast.Tuple))
                and len(node.value.elts) >= 2):
            for t in node.targets:
                if not isinstance(t, ast.Subscript):
                    continue
                try:
                    if ast.literal_eval(t.slice) != 'figure.figsize':
                        continue
                    vals = [float(ast.literal_eval(x)) for x in node.value.elts[:2]]
                except (ValueError, TypeError, SyntaxError):
                    continue
                return vals
    # T64/D1：源码里存在"设定尺寸但我们读不出字面量"的语句时**弃权**，不要落回
    # matplotlib 默认值 —— 落回默认值会把"读不到"当成"等于默认值"，目标恰为默认值时
    # 就静默不改（零侵入换成了静默丢改动，方向错）。返回 None ⇒ 调用方按"证不出"
    # 保守处理（退块）：宁可多插一个幂等块，也不静默丢改动。
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and any(
                getattr(kw, 'arg', None) == 'figsize' for kw in node.keywords):
            return None
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == 'set_size_inches'):
            return None
    try:
        import matplotlib
        return [float(x) for x in matplotlib.rcParams['figure.figsize'][:2]]
    except (ImportError, KeyError, ValueError, TypeError):
        return None


def _writeback_inplace(script, src, tree, data, params_path, verify,
                       python, timeout, dry_run, semantic, encoding='utf-8',
                       fast_check=False, backup=None, keep_unverified=False,
                       script_args=None):
    """原位写回主流程：改数字 → 备份 → 写文件 → Agg + 语义验证（只比被覆盖字段）。

    backup：复用调用方已做好的备份（L0-4，见 writeback() 的同名参数）。
    script_args：验证重跑透传给用户脚本的 ``sys.argv[1:]``（bug #6）——
    verify_run / verify_semantic / baseline 重跑全都带上，依赖 ``sys.argv``
    的脚本（``python fig.py --data x.npz``）才能与基线一致地重跑。
    """
    res: Dict[str, Any] = {'reason': FAIL, 'changes': [], 'block': None,
                           'backup': None, 'warnings': [], 'verified': None,
                           'err': '', 'style': 'inplace',
                           # applied：这次**到底有没有改动磁盘上的脚本**。
                           # 人读输出与 --json 都要靠它区分"写了"和"一处都没写"
                           # （审计 E7：一处没写也打印"[OK] 已原位写回"）。
                           # needs_block：显式的退块依据（apply 的 auto 用它，
                           # 不再用 `not changes` —— 审计 E1）。
                           'applied': False, 'needs_block': False,
                           'block_lines': 0, 'pending': []}
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
    # figsize 是否**已经在源码里正确**（这次原位改了、或者本来就是这几个数）：
    # 两种情况都不该再警告"尺寸没落地"，块模式也不该再补 set_size_inches
    # ——否则第二次 apply 的块会多一行 → 幂等破功（L0-1 的连带效应）。
    _fs_handled = bool(figsize_in) and (
        figsize_edited or figsize_already_ok(tree, figsize_in, _fig_idx))
    if figsize_edited:
        # figsize 原位替换改变了文件长度 → 原 tree 的节点坐标全部失效 →
        # 重新 parse，让 apply_inplace 的新坐标与新文件对齐（否则后续替换错位）。
        try:
            tree = ast.parse(new_src)
        except SyntaxError:                       # 理论上不会，稳妥回退
            figsize_edited = False
            new_src, tree = src, ast.parse(src)
    if figsize_in and not _fs_handled:
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
    if figsize_edited and in_src != src:
        # RC-9：changes 必须与实际改动一致。只改画布尺寸时它过去是空的 ——
        # agent 从 --json 里看不出"到底改了什么"，auto 的退块判定也被它带偏（E1）。
        changes.append('figsize')
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
    # ---- 逐字段三态判据（T15）------------------------------------------------
    # 每个"有目标值"的字段只有三种状态：
    #   ① 已原位落地（covered）
    #   ② 与源码现值相同（T12 的字面量证明 + T15 的默认值证明已把它从 skipped 里剔掉）
    #   ③ 有目标值、与源码现值不同、但原位写不了（= `_pending`）
    # **只要存在第三个态就必须退块** —— 这与"原位有没有落地别的字段"完全无关。
    # 旧判据是"原位一个字段都没落地才退块"，于是 INT-2 的 B 情形（figsize 落地了、
    # pos 写不了）被漏掉：rc=0 + applied=true，pos 静默丢失（只剩一条 warnings）。
    _pending = _pending_fields(skipped)
    # FIX(Bug 6)：图级尺寸目标若**无法原位落地**（源码里没有可写的 figsize=），
    # 必须与轴级 pending 同等对待 —— 否则这里会得出 _needs=False ⇒ reason=no_change，
    # 而 apply 的 auto 见到 no_change 就不退块，用户改的尺寸被静默忽略
    # （完整档忽略、快速档因走另一条分支会落地）。见 audit_018/fix_engine.md §T52–§T55。
    _fig_pending = bool(figsize_in) and not _fs_handled
    if _fig_pending:
        # v2：给"图级尺寸是否需要改"加**等值证明** —— 源码里没有 figsize= 字面量时，
        # 静态读出"这张图实际会用的尺寸"（set_size_inches / rcParams / 默认值）；
        # 与目标相同 ⇒ 干净往返，不该插块（否则违反"零侵入"：参数与现状一致时也动文件）。
        _src_fs = _figsize_from_source(src, tree)
        if (_src_fs and len(figsize_in) >= 2
                and _num_close(_src_fs[0], figsize_in[0])
                and _num_close(_src_fs[1], figsize_in[1])):
            _fig_pending = False
    if not _pending and not _fig_pending:
        _needs = False
    elif _fig_pending:
        _needs = True
    elif verify and semantic:
        # 允许重跑 → 用实测状态逐字段回比（判不了时 _pending_vs_live 返回 None → 保守）
        _needs = _pending_vs_live(script, data, _pending, python, timeout,
                                  params_path,
                                  script_args=script_args) is not False
    else:
        # --verify-fast / --no-verify：不重跑脚本就证不出"相等" → 保守退块（T7 既有设计）
        _needs = True
    _plist = sorted('ax%d.%s' % (i, f) for i, fs in _pending.items() for f in fs)
    if _fig_pending:
        _plist = sorted(_plist + ['figsize'])
    if _needs and _plist:
        warnings.append('本图有 %d 处目标值无法原位写回（%s）→ 需要调整块才能落地（%s）'
                        % (len(_plist), '、'.join(_plist),
                           '实测不一致' if verify and semantic
                           else '没重跑脚本，按保守判据'))
    if dry_run:
        res.update({'reason': OK, 'changes': changes,
                    'block': ('原位修改：' + ', '.join(changes)) if changes
                    else '（无改动）',
                    'warnings': warnings, 'backup': None,
                    'figsize_edited': figsize_edited, 'anchor': 'inplace',
                    'applied': False, 'needs_block': _needs,
                    'pending': _plist if _needs else None})
        return res
    if in_src == src and not changes:
        # 原位一个字段都没写进去。两种完全不同的情形（审计 E1 + T15）：
        #   (a) 全部字段都属①②态 → no_change，一个字节都不该动；
        #   (b) 存在③态 → best_effort + needs_block，交给 apply 的 auto 退到块模式
        #       落地（E4：不许静默丢）。
        if _needs:
            res.update({'reason': BEST_EFFORT, 'changes': changes,
                        'warnings': warnings, 'figsize_edited': figsize_edited,
                        'anchor': 'inplace', 'applied': False,
                        'needs_block': True, 'pending': _plist})
            return res
        # 参数与源码**已经一致** → 不带警告（T11）：上面那几条按轴聚合的
        # "axN 未原位应用：pos、grid…（这些项代码里没有对应写法，保持原样）"在这条
        # 路径上是**误导** —— 读者会以为有改动没生效，而实测证明那些字段的现值与
        # 参数完全一致（`_differs is False`）。`no_change` 自己的文案已经说清了。
        res.update({'reason': 'no_change', 'changes': changes,
                    'warnings': [], 'figsize_edited': figsize_edited,
                    'anchor': 'inplace', 'applied': False, 'needs_block': False,
                    # 'skipped'：这次**没跑**语义验证，别让 apply 打印
                    # "语义验证未能判定（脚本不存图/跑不通）"这种假解释（审计 E7）。
                    'semantic': 'skipped'})
        return res
    try:
        # L0-4：调用方给了备份就复用（同一条 apply 里的第二次写盘不得覆盖它）。
        # T41：pid 副本写不出来时不再静默（warnings 里会带上路径与 errno）。
        backup = backup or _make_backup(script, params_path, warnings=warnings)
    except OSError as e:
        res['err'] = '备份失败: %s' % e
        return res
    res['backup'] = backup
    _guard = _check_external_edit(script, src, backup=backup)
    if _guard:
        res.update(_guard)
        return res
    try:
        _atomic_write(script, in_src, encoding=encoding)
    except OSError as e:
        res['err'] = '写文件失败: %s（%s）' % (e, _rollback_note(backup, script))
        return res
    # T23 / 外部评审条目 3(c) 的廉价兜底：**写盘后无条件 ast.parse 一次**。
    # 语法坏了的写回绝不该留在磁盘上（"一次解析的钱"），而过去的语法检查只在
    # --verify-fast 档做；完整验证档靠"重跑"发现语法错，一旦脚本本来就跑不通
    # （缺数据/依赖）就会被 baseline 分支当成"不是我们的锅"而保留下来。
    # 注意：快速档自己有同款检查（文案点名"快速验证"），这里别抢它的活。
    if not (fast_check and not verify):
        try:
            ast.parse(in_src)
            res['layers'] = dict(res.get('layers') or {}, syntax=True)
        except SyntaxError as e:
            res.update({'reason': FAIL, 'applied': False, 'verified': False,
                        'layers': dict(res.get('layers') or {}, syntax=False),
                        'err': '写回后源码语法错误（%s）：%s'
                               % (_rollback_note(backup, script, expect=in_src), e)})
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
            # T23 / 外部评审条目 3(d)：快速档实际只有"语法"这一层 —— 如实标出来，
            # 别让人从 `verify_mode=fast` 反推不出"重跑/语义都没做"。
            res['layers'] = {'syntax': True, 'rerun': None, 'semantic': None}
        except SyntaxError as e:
            res.update({'reason': FAIL, 'verify_mode': 'fast',
                        'layers': {'syntax': False, 'rerun': None,
                                   'semantic': None},
                        'err': '快速验证失败：写回后的源码语法错误（%s）：%s'
                               % (_rollback_note(backup, script, expect=in_src), e),
                        'verified': False})
            return res
    if verify:
        try:
            vok, verr = verify_run(script, python, timeout,
                                   script_args=script_args)
        except BaseException as e:                         # noqa: BLE001
            # **回滚兜底，必须宽**（棘轮登记的 9 处宽捕获之一）：脚本已经写盘了 →
            # 这里起任何异常都必须回滚，窄化会漏掉下一种未知异常（E3 的现场）。
            # 与块路径同一条纪律。
            res.update({'reason': FAIL, 'verified': False, 'applied': False,
                        'err': '写回后验证异常（%s: %s）：%s'
                               % (type(e).__name__, e,
                                  _rollback_note(backup, script,
                                                 expect=in_src))})
            return res
        res['verified'] = vok
        # T23：三层验证各自的状态（rerun 这一层就是"重跑"）
        res['layers'] = dict(res.get('layers') or {}, rerun=vok)
        if vok is True and semantic:
            if covered or figsize_edited:
                # 原位覆盖了字段 → 语义只比对被覆盖的字段。**每个轴**都要在
                # fields_by_axis 里有条目（未覆盖的轴给空集 = 什么都不比）——
                # 否则 compare 会把没列出的轴当"全字段比对"，colorbar 等未覆盖
                # 轴的 clim/位置会误报不一致而回滚。
                fba = {}
                for _a in data.get('axes') or []:
                    _i = _a.get('index')
                    if isinstance(_i, int):
                        fba[_i] = set(covered.get(_i, ()))
                        # T73（船长裁决 #2）：**拆开 x/y 的耦合** —— 原来"改了 y 就把
                        # x 的门也打开"（`fba |= {label_fontsize,…}` → 再 add
                        # 'label_fontsize'），于是自检比渲染更宽松（x 的字段顺带把 y 的
                        # 字段纳入检查）。现在 `fba[_i]` 只含**真正写进去的那些字段**。
                        # 已知边界（记 0.1.10，不在本任务扩范围）：verify.compare 目前
                        # 只认 'label_fontsize'/'tick_fontsize'，所以纯 y 侧的改动在语义
                        # 层仍是"不判"（不是判过）——宁可如实不判，也不假装覆盖。
                sok, sdetail = None, '语义验证未执行'
                try:
                    sok, sdetail = _verify.verify_semantic(
                        script, data, python, timeout, fields_by_axis=fba,
                        figsize_edited=figsize_edited,
                        script_args=script_args)
                except BaseException as e:                 # noqa: BLE001
                    # **回滚兜底，必须宽**（棘轮登记的 9 处宽捕获之一）：写盘之后
                    # 任何异常都必须回滚，窄化会漏掉下一种未知异常（E3；块路径同款）。
                    res.update({'reason': FAIL, 'verified': True,
                                'applied': False,
                                'err': '写回后语义验证异常（%s: %s）：%s'
                                       % (type(e).__name__, e,
                                          _rollback_note(backup, script,
                                                         expect=in_src))})
                    return res
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
                    # T23 / 外部评审条目 3(a)：②层**没跑成**（取不到图状态）时不能报
                    # verified=True —— 那会让人读成"全都验证过了"。如实降成 None
                    # （三态：True 验证通过 / None 验证没跑 / False 验证不通过）。
                    res['verified'] = None
                    warnings.append('语义验证未取到目标图状态（%s），'
                                    '本次只确认了"脚本能跑通"，**没有**验证布局是否'
                                    '落到目标图上' % sdetail)
            else:
                # 原位没覆盖任何轴字段（如 add_subplot 网格轴 / colorbar 轴位置）：
                # 位置本就没被改，语义比对必然失败无意义 → 跳过，Agg 跑通即可。
                res['semantic'] = None
                warnings.append('本图没有可原位写回的轴属性（轴是 add_subplot 网格位置 / '
                                'colorbar / 无对应调用），只应用了画布尺寸；'
                                '位置/字号请用 --style block 或手动改代码')
        elif vok is False:
            # T23 / 3(c)：先分清"我们把它写坏了"和"它本来就跑不通"（缺数据/依赖）。
            # 后者**证明不了**写回是否可用 → 默认回滚；显式 --keep-unverified 才保留。
            _base = None
            if backup:
                try:
                    _base, _ = verify_run(backup, python, timeout,
                                          script_args=script_args)
                except OSError:
                    _base = None      # 起不了子进程 → 判不了，按"跑不通"处理
            if _base is False:
                if keep_unverified:
                    warnings.append(
                        '脚本本身在无头环境跑不通（缺数据/依赖，或需要 GUI/交互），'
                        '无法用 Agg 验证写回结果；按 --keep-unverified 保留本次写回，'
                        '请自行运行确认')
                    res['verified'] = None
                else:
                    res.update({
                        'reason': FAIL, 'applied': False, 'verified': False,
                        'err': ('脚本本身在无头环境跑不通（缺数据/依赖，或需要 GUI/交互），'
                                '无法用 Agg 验证这次写回是否可用 → 默认**不保留**：%s。'
                                '确实要保留请显式加 --keep-unverified。'
                                % _rollback_note(backup, script, expect=in_src))})
                    return res
            else:
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
                'figsize_edited': figsize_edited, 'anchor': 'inplace',
                # 走到这里 = 文件确实改过（备份 + 写盘都在上面）
                # needs_block：**这次也写不进去**的字段（T15 —— 不能因为别的字段落地了
                # 就把它当"完成了"）。true 时 auto 会退到块模式补上，`--style inplace`
                # 显式指定时由 apply 明确报"没落地"（rc≠0），不静默丢。
                'applied': True, 'needs_block': _needs,
                'pending': _plist if _needs else None,
                # T23 / 3(a)(d)：三层验证各自的真实状态（True/False/None=没跑），
                # 供 --json 与"人读"如实说明这次到底验了哪几层。
                'layers': dict(res.get('layers') or {},
                               rerun=res.get('layers', {}).get('rerun'),
                               semantic=res.get('semantic')
                               if isinstance(res.get('semantic'), bool) else None)})
    return res

# --------------------------------------------------------------------------
# 调整块生成
# --------------------------------------------------------------------------
# --------------------------------------------------------------------------
# 块自检（T18）：块**表达不了**的目标字段必须如实报出来
# --------------------------------------------------------------------------
# 每个"参数里有目标值"的字段都必须在生成的块里找到对应写法。找不到 = 用户这次请求
# 落不了地 —— 绝不能报 ok / applied=true / needs_block=false（agent 会以为都改好了）。
# 这是一张**安全网**：目前块渲染器覆盖了下面这些字段的全部取值，正常路径自检为空；
# 一旦将来加了新字段/新取值而忘了补块写法，这里会立刻把它抓出来（而不是静默丢）。
_BLOCK_MARKERS = {
    'title_fontsize': r'\.title\.set_fontsize\(',
    'label_fontsize': r'\.xaxis\.label\.set_fontsize\(',
    'label_fontsize_y': r'\.yaxis\.label\.set_fontsize\(',
    'tick_fontsize': r'\.tick_params\(',
    'tick_fontsize_y': r"\.tick_params\(axis='y'",
    'grid': r'\.grid\(',
    'xlim': r'\.set_xlim\(',
    'ylim': r'\.set_ylim\(',
    'spines': r'\.spines\[',
    'xscale': r'\.set_xscale\(',
    'yscale': r'\.set_yscale\(',
    'lines': r'\.lines\[\d+\]\.',
    'legend': r'\.legend\(',
}
_BLOCK_MARKERS_FIG = {'clim': r'\.set_clim\(', 'cmap': r'\.set_cmap\('}


def _dup_names_in_fig_assign(tree, fig_index=0):
    """T84：``fig, (ax, ax) = plt.subplots(1, 2)`` —— 数**同一条赋值语句**里每个名字出现几次。

    为什么需要它：``_figure_axes_for_index`` 只收 ``add_axes`` / ``add_subplot`` 调用，
    **看不见**这种"一条语句里同名解包" ⇒ T57 的 ``_partial_dups`` 永远是空集，
    轴 0 的字段就继续按名字 ``ax`` 去 ``_find_call`` 定位；而运行期 ``ax`` 只认**最后一次**
    解包（第二条轴）⇒ 改动落到别的对象上却报 ``applied=true``（Codex r4 §2.3）。
    返回 ``{名字: 次数}``（只统计出现在该建图调用所在赋值语句目标里的名字）。
    """
    node = _fig_call_node(tree, fig_index)
    if node is None:
        return {}
    cnt = {}
    for a in ast.walk(tree):
        if not isinstance(a, ast.Assign):
            continue
        if not any(x is node for x in ast.walk(a.value)):
            continue                        # 不是这次建图的赋值语句
        for t in a.targets:
            for x in ast.walk(t):
                if isinstance(x, ast.Name):
                    cnt[x.id] = cnt.get(x.id, 0) + 1
    return cnt


def _dup_name_set(var_for):
    """哪些变量名在同一张图里被赋给了 ≥2 个轴（T41）。"""
    _ids: Dict[str, List[int]] = {}
    for _i in sorted(var_for):
        _ids.setdefault(var_for[_i], []).append(_i)
    return {nm for nm, idxs in _ids.items() if len(idxs) > 1}


def _drop_dup_var_names(var_for):
    """同一张图里同名变量被赋值 ≥2 次时，**只保留第一个轴**的映射（T41，就地改）。

    返回被剔除的名字集合。理由见 `apply_inplace` 里的长注释：`_find_call` 只命中
    最早的调用，第 2 个及以后的轴若继续"可原位定位"，字段就会被判成 noop（静默丢
    改动）或改到第 1 个轴上（写坏）。原位路径与块的 needed 计算**必须同一口径**，
    否则块会把"其实要改"的字段判成"已等值"（T41 §64b 就是这么抓出来的）。
    """
    _ids: Dict[str, List[int]] = {}
    for _i in sorted(var_for):
        _ids.setdefault(var_for[_i], []).append(_i)
    _dups = {nm for nm, idxs in _ids.items() if len(idxs) > 1}
    for _nm, _idxs in _ids.items():
        for _i in _idxs[1:]:
            var_for.pop(_i, None)
    return _dups


# 显式建轴工厂（每次调用确定地创建 1 条轴，`subplots` 是 nrows×ncols 条）
_AXES_FACTORY_ATTRS = ('add_axes', 'add_subplot', 'subplot', 'axes', 'subplots',
                       'subplot_mosaic',
                       # 隐式建轴：这些调用不带 cax 时都会**新建**一条轴
                       # （套件 §1 的 `fig.colorbar(im, ax=ax2)` 就是 2 面板 + 1 条）
                       'colorbar', 'twinx', 'twiny', 'inset_axes',
                       'secondary_xaxis', 'secondary_yaxis')


def _grid_size(call):
    """``subplots(...)`` 的 (行, 列)：参数是字面量才算（读不出 → None）。"""
    nrows, ncols = 1, 1
    for k, a in enumerate(call.args[:2]):
        if not _is_num_literal(a):
            return None
        if k == 0:
            nrows = int(_lit_num(a))
        else:
            ncols = int(_lit_num(a))
    for kw in call.keywords:
        if kw.arg in ('nrows', 'ncols'):
            if not _is_num_literal(kw.value):
                return None
            if kw.arg == 'nrows':
                nrows = int(_lit_num(kw.value))
            else:
                ncols = int(_lit_num(kw.value))
    return max(1, nrows), max(1, ncols)


def _runtime_axes_count(tree, fig_index):
    """本图区间内"运行时会创建多少张轴"——**静态可数**才返回 int，否则 None。

    T67/A-2：调整块里的位置用 ``zip(fig.axes, [...])`` 寻址，即**运行时下标**。只有
    "会创建哪些轴、按什么顺序创建"全都静态可数时，``fig.axes[i]`` 才必然就是参数里
    第 i 个轴；循环里建轴（``for i in range(11): plt.subplot(3,4,i+1)``）静态数不出来
    ⇒ 返回 None，调用方 fail-closed 拒绝写位置。
    """
    if isinstance(fig_index, int):
        lo, hi = _fig_span(tree, fig_index)
    else:
        lo, hi = 0, 10 ** 9
    total = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        _ln = getattr(node, 'lineno', None)
        if _ln is None or not (lo <= _ln < hi):
            continue
        f = node.func
        if isinstance(f, ast.Attribute):
            attr = f.attr
        elif isinstance(f, ast.Name):
            attr = f.id
        else:
            continue
        if attr not in _AXES_FACTORY_ATTRS:
            continue
        if attr == 'subplot_mosaic':
            return None                     # 拼图布局：轴的集合/顺序静态读不出来
        if attr == 'colorbar' and any(kw.arg == 'cax' for kw in node.keywords):
            continue                        # 传了 cax：轴是那条 add_axes 建的，别重复数
        if find_enclosing_loop(tree, node) is not None:
            return None                     # 循环建轴 → 数量与顺序都不可数
        if attr == 'subplots':
            g = _grid_size(node)
            if g is None:
                return None
            total += g[0] * g[1]
        else:
            total += 1
    return total


def _pos_block_target_ok(tree, data, fig_index):
    """块里的 ``fig.axes[i]`` 能否**证明**就是参数里第 i 个轴对应的运行时对象。

    T67/A-2：证不出就不许把位置写进块（fail-closed）。T67/A 语料的现场：
    11 个面板由 ``for i in range(11): plt.subplot(3,4,i+1)`` 建出、色条 cax 是内联
    ``fig.add_axes([...])``（运行时下标 11），而参数记的轴 0 就是这个 cax ——
    zip 只会打 ``fig.axes[0]``（主图）⇒ 把主图改成色条的几何。
    """
    idxs = [a.get('index') for a in _axis_items(data) if a.get('pos') is not None]
    if not idxs:
        return True
    if not all(isinstance(i, int) and i >= 0 for i in idxs):
        return False
    cnt = _runtime_axes_count(tree, fig_index)
    if cnt is None:
        return False
    if cnt == 0:
        # 没有显式建轴语句：唯一的轴来自 `plt.plot(...)` 触发的隐式 gca（§6 老形态）
        return max(idxs) == 0
    return max(idxs) < cnt


def _needed_block_fields(tree, data, src, fig_index):
    """块**确实需要**写的字段：``{轴序号: {字段}}``（T35）。

    判据 = 调用方显式给了目标值 ∧ 证明不了"它已经等于源码现值"（与原位路径同一套
    等价证明 `_proved_equal`）。两个后果：

      * normalize 补出来的默认值（旧版 `xscale/yscale='linear'`）不算目标 ⇒ 不会再被
        写进块 —— 否则源码明写 `set_xscale("log")` 的轴会被 `set_xscale('linear')`
        覆盖（L0 复测 E5：23192393__figure_panels / 5809269__O3_FYSP_paper）；
      * 参数里每个字段都已等值时返回空 ⇒ 调用方不插块（M4 的次级现象）。
    """
    _axes_calls = _figure_axes_for_index(tree, fig_index)
    var_for: Dict[int, str] = {}
    # T57-稀疏轴（t2 fuzz high）：与 apply_inplace 同口径 —— 按轴序号 index 直接
    # 索引（index → 第 index 个非 colorbar 轴创建调用），不再按位置 zip（稀疏参数
    # 只给 index=1 时旧代码把 index=1 错配到 axes_calls[0] → _proved_equal 拿**错的
    # 变量**证明"已等值"，目标值与 ax0 恰巧相等时会静默漏写 ax1）。越界/负/非 int
    # 不登记（fail-closed，由调用方转 skipped/块）。
    for _a in _axis_items(data):
        if _a.get('is_colorbar'):
            continue
        _ai = _a.get('index')
        if isinstance(_ai, int) and 0 <= _ai < len(_axes_calls):
            var_for[_ai] = _axes_calls[_ai][0]
    _drop_dup_var_names(var_for)      # T41：与 apply_inplace 同一口径（见该函数注释）
    idx_map = axis_var_index(tree)
    out: Dict[int, set] = {}
    for a in _axis_items(data):
        i = a.get('index')
        if not isinstance(i, int):
            continue
        fs = set()
        for f in ('pos', 'title_fontsize', 'label_fontsize', 'label_fontsize_y',
                  'tick_fontsize', 'tick_fontsize_y', 'grid', 'xlim', 'ylim',
                  'spines', 'xscale', 'yscale', 'clim', 'cmap', 'lines',
                  'legend'):
            v = a.get(f)
            if v is None or v == {} or v == []:
                continue
            if _proved_equal(tree, i, f, v, var_for, idx_map, fig_index,
                             src=src, is_cb=bool(a.get('is_colorbar'))):
                continue
            fs.add(f)
        if fs:
            out[i] = fs
    return out


def _block_unexpressed_fields(block, data, fig_var, needed=None, targets=None):
    """返回块里**没有对应写法**的目标字段清单（形如 ``ax0.xscale``）。

    ``needed``（T35）：只检查**确实需要落地**的字段（``{轴序号: {字段}}``），
    为 None 时保持老行为。否则"调用方从没要求、只是 normalize 补的默认值"会被当成
    目标字段而误报；反过来"参数里所有字段都与源码一致"时也不该报任何东西。

    ``targets``（T70）：块里真正用的**目标表达式**（`ax` / `axes[0]` / …）。
    自检必须按它判接收者 —— 过去这里拼的是 `fig.axes[i]`，块改成可证名字之后就会
    误报"字段没写进去"（T70 实测：§43 E3 的回滚用例因此被躲过）。
    目标证不出的轴，其字段已被调用方摘出 ``needed`` ⇒ ``_want`` 为假 ⇒ 不会进来。
    """
    out: List[str] = []

    def _want(i, field):
        return needed is None or field in (needed.get(i) or ())

    axes = _axis_items(data)
    if any(a.get('pos') is not None and _want(a.get('index'), 'pos')
           for a in axes) and '.set_position(' not in block:
        out.append('fig.pos')
    for a in axes:
        i = a.get('index')
        if i is None:
            continue
        _tgt = (targets or {}).get(i)
        ax_re = re.escape(_tgt if _tgt else '%s.axes[%d]' % (fig_var, i))
        checks = []
        if a.get('title_fontsize') is not None and _want(i, 'title_fontsize'):
            checks.append(('title_fontsize', _BLOCK_MARKERS['title_fontsize']))
        _lx, _ly = _label_fontsizes(a)
        if _lx is not None and _want(i, 'label_fontsize'):
            checks.append(('label_fontsize', _BLOCK_MARKERS['label_fontsize']))
        if _ly is not None and _want(i, 'label_fontsize_y'):
            checks.append(('label_fontsize_y', _BLOCK_MARKERS['label_fontsize_y']))
        _txc, _tyc = _tick_fontsizes(a)
        if _txc is not None and _want(i, 'tick_fontsize'):
            checks.append(('tick_fontsize', _BLOCK_MARKERS['tick_fontsize']))
        if a.get('tick_fontsize_y') is not None and _want(i, 'tick_fontsize_y'):
            checks.append(('tick_fontsize_y', _BLOCK_MARKERS['tick_fontsize_y']))
        if a.get('grid') is not None and _want(i, 'grid'):
            checks.append(('grid', _BLOCK_MARKERS['grid']))
        for f in ('xlim', 'ylim'):
            rng = a.get(f)
            if isinstance(rng, (list, tuple)) and len(rng) == 2 and _want(i, f):
                checks.append((f, _BLOCK_MARKERS[f]))
        if a.get('spines') and _want(i, 'spines'):
            checks.append(('spines', _BLOCK_MARKERS['spines']))
        for f in ('xscale', 'yscale'):
            sc = a.get(f)
            if isinstance(sc, str) and sc.strip() and _want(i, f):
                checks.append((f, _BLOCK_MARKERS[f]))
        if a.get('lines') and _want(i, 'lines'):
            checks.append(('lines', _BLOCK_MARKERS['lines']))
        if a.get('legend') and _want(i, 'legend'):
            checks.append(('legend', _BLOCK_MARKERS['legend']))
        for field, pat in checks:
            if not re.search(ax_re + pat, block):
                out.append('ax%d.%s' % (i, field))
        # mappable 上的字段（clim/cmap）在块里是别的表达式（im.set_clim(...)），全局找
        for field in ('clim', 'cmap'):
            if a.get(field) is not None and _want(i, field) \
                    and not re.search(_BLOCK_MARKERS_FIG[field], block):
                out.append('ax%d.%s' % (i, field))
    # 去重保序（同一字段可能被多张图提到）
    seen = set()
    return [x for x in out if not (x in seen or seen.add(x))]


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


# --------------------------------------------------------------------------
# T70：块内**只许用静态可证的对象名**定位目标
#
# 为什么：块里过去的 `fig.axes[i]` 是**运行期列表下标**，而运行期 fig.axes 还包含
# colorbar 轴 cax / inset / twin / parasite 轴 —— 静态轴数一漂，改动就落到别的轴上
# （T67 实证：`zip(fig.axes, [[0.78,0.18,0.2,0.03]])` 把主图改成了色条那么大）。
# 现在的判据把"目标是谁"变成**能证的**：证不出就不写（fail-closed），绝不退回下标。
# --------------------------------------------------------------------------
_SCOPE_TYPES = (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef)
# 会创建 Axes 的调用（用于"目标图里第 k 次建轴是谁"的枚举）
_AXES_CREATORS = ('add_axes', 'add_subplot', 'subplot', 'axes', 'subplots',
                  'subplot_mosaic', 'colorbar', 'twinx', 'twiny', 'inset_axes',
                  'secondary_xaxis', 'secondary_yaxis')
_IMPLICIT_AXES = ('twinx', 'twiny', 'inset_axes', 'secondary_xaxis',
                  'secondary_yaxis')
# pyplot 级**隐式建轴**：没有轴时它建一条（gca 语义），已有轴时复用当轴 —— 必须进
# 建轴枚举，否则"第 k 条轴是谁"数不对（T70-B：纯 pyplot 单图那一档就靠它）。
_IMPLICIT_CREATORS = ('plot', 'imshow', 'contour', 'contourf', 'pcolor',
                      'pcolormesh', 'scatter', 'bar', 'barh', 'hist', 'hist2d',
                      'pie', 'errorbar', 'fill_between', 'stackplot', 'step',
                      'stem', 'quiver', 'streamplot', 'tricontour', 'tricontourf',
                      'semilogx', 'semilogy', 'loglog', 'gca', 'ginput')
# 返回值**就是 Axes** 的调用：只有这些才允许"用接收者变量名寻址"。
# （`im = ax.imshow(...)` 拿到的是 AxesImage、`plt.plot(...)` 拿到的是 Line2D 列表
#  —— 按名字寻址会把它们当成轴，实测写坏过块：`im.set_position(...)` ⇒ AttributeError。）
_AXES_RETURNING = ('add_axes', 'add_subplot', 'subplot', 'axes', 'twinx', 'twiny',
                   'inset_axes', 'secondary_xaxis', 'secondary_yaxis')
# 静态**认不出会建出几条轴**的调用：出现在某条轴之前 ⇒ 那条轴（含其后）的运行期序号
# 可能被顶掉 ⇒ 一律拒绝。
_SUSPECT_ATTRS = ('subplot_mosaic', 'append_axes', 'make_axes_locatable',
                  'indicate_inset', 'indicate_inset_zoom', 'inset_locator')
# T70-B 条件③：这些"额外建轴来源"一律关掉纯 pyplot 单图的可证子集（船长批示的口径）。
_EXTRA_AXES_ATTRS = ('colorbar', 'inset_axes', 'inset_locator', 'twinx', 'twiny',
                     'make_axes_locatable', 'append_axes', 'secondary_xaxis',
                     'secondary_yaxis', 'indicate_inset', 'indicate_inset_zoom',
                     'subplot_mosaic')


def _node_scope(tree, lineno):
    """lineno 所在的**作用域节点**（Module / 函数），取最内层。"""
    best, best_lo = None, -1
    for n in ast.walk(tree):
        if not isinstance(n, _SCOPE_TYPES):
            continue
        # ast.Module 没有 lineno/end_lineno（实测 AttributeError）⇒ 一律 getattr。
        lo = getattr(n, 'lineno', 0)
        hi = getattr(n, 'end_lineno', None) or 10 ** 9
        if lo <= lineno <= hi and lo >= best_lo:
            best, best_lo = n, lo
    return best


def _direct_assign_linenos(scope, name):
    """作用域里**直接语句**级的 `name = ...` 行号（`if:` / 循环 / `try` 里的不算）。"""
    out = []
    for st in getattr(scope, 'body', []):
        if isinstance(st, ast.Assign):
            for tgt in st.targets:
                if name in set(_iter_assign_names(tgt)):
                    out.append(st.lineno)
                    break
    return out


def _rebound_before(scope, lineno, name, allow=()):
    """[作用域起点, lineno) 里 name 是否被重新绑定/删除/被 import 遮蔽。

    ``allow``：允许的行号（绑定语句本身）。只认 `name = ... / for name in /
    with ... as name / del name / def name / import name`——这些都会让"名字仍然
    指目标对象"这句话失效；名字在**别的作用域**里被重绑定不算（块只引用同作用域）。
    """
    for n in ast.walk(scope):
        ln = getattr(n, 'lineno', 0)
        if ln >= lineno or ln in allow:
            continue
        if isinstance(n, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            tgs = n.targets if isinstance(n, ast.Assign) else [n.target]
            for t in tgs:
                if name in set(_iter_assign_names(t)):
                    return True
        elif isinstance(n, (ast.For, ast.AsyncFor)):
            if name in set(_iter_assign_names(n.target)):
                return True
        elif isinstance(n, (ast.With, ast.AsyncWith)):
            for it in n.items:
                if it.optional_vars is not None                         and name in set(_iter_assign_names(it.optional_vars)):
                    return True
        elif isinstance(n, ast.Delete):
            for t in n.targets:
                if name in set(_iter_assign_names(t)):
                    return True
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if n.name == name:
                return True
        elif isinstance(n, ast.Import):
            if any((a.asname or a.name.split('.')[0]) == name for a in n.names):
                return True
        elif isinstance(n, ast.ImportFrom):
            if any((a.asname or a.name) == name for a in n.names):
                return True
    return False


def _name_still_bound(scope0, bind_lineno, name, positions):
    """name 在 bind_lineno 绑定，且在**每个候选锚点位置**上都仍是那个对象。"""
    if scope0 is None:
        return False
    if len(_direct_assign_linenos(scope0, name)) != 1:
        return False                # 同作用域里赋过多次 ⇒ 证不出指向哪个
    for sc, ln in positions:
        if sc is not scope0 or ln <= bind_lineno:
            return False
        if _rebound_before(scope0, ln, name, allow=(bind_lineno,)):
            return False
    return True


def _assign_names_of(tree, call):
    """call 作为某条 Assign 右值时被赋的名字（按顺序；没有 → []）。"""
    for n in ast.walk(tree):
        if isinstance(n, ast.Assign) and n.value is call:
            out = []
            for t in n.targets:
                out.extend(_iter_assign_names(t))
            return out
    return []


def _inline_cax_of(tree, call):
    """内联 `fig.colorbar(m, cax=fig.add_axes([...]))` 的宿主 name（cbar → 'cbar.ax'）。

    cax 由 add_axes 建、却**没有变量名**时，唯一可证的引用是 colorbar 的返回值：
    `fig.colorbar()` 一定返回 Colorbar，其 `.ax` 就是那条 cax（matplotlib 契约）。
    """
    for n in ast.walk(tree):
        if not (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr == 'colorbar'):
            continue
        for kw in n.keywords:
            if kw.arg == 'cax' and kw.value is call:
                nm = _assign_names_of(tree, n)
                if nm:
                    return '%s.ax' % nm[0]
    return None


def _grid_squeeze(call):
    """``subplots(..., squeeze=)`` 的字面量取值（缺省 True；非字面量 → None）。"""
    for kw in call.keywords:
        if kw.arg == 'squeeze':
            if isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, bool):
                return kw.value.value
            return None
    return True


def _grid_element(container, nrows, ncols, squeeze, k):
    """单次字面量形状 `subplots()` 的第 k 条轴 → 静态元素表达式。"""
    if nrows * ncols == 1:
        return container
    if squeeze and (nrows == 1 or ncols == 1):
        return '%s[%d]' % (container, k)
    return '%s[%d, %d]' % (container, k // ncols, k % ncols)


def _axes_binding_entries(tree, fig_index, fig_var=None):
    """目标图内**按源码序**的每一次建轴 → (表达式, 根名字, 绑定行号, 是否匿名)；匿名 → None。

    返回 (entries, ok, suspect)。ok=False ⇒ 静态**数不全**（循环里建轴 / subplot_mosaic /
    网格尺寸不是字面量）；suspect=行号 ⇒ 该行之前没有认不出的建轴调用，之前之后的轴不可证。
    索引与运行期创建序一致：枚举按源码序、且把"会建轴的调用"都算上（含 pyplot 隐式建轴），
    "已有轴时 pyplot 只是复用当轴"的不重复计数。
    """
    lo, hi = _fig_span(tree, fig_index)
    found = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        attr = node.func.attr
        if attr not in _AXES_CREATORS and attr not in _IMPLICIT_CREATORS:
            continue
        ln = getattr(node, 'lineno', None)
        if ln is None or not (lo <= ln < hi):
            continue
        if find_enclosing_loop(tree, node) is not None:
            return [], False, None
        if attr == 'subplot_mosaic':
            return [], False, None
        if attr == 'colorbar' and any(kw.arg == 'cax' for kw in node.keywords):
            continue                    # cax 由它内部那条 add_axes 建，别重复计数
        if fig_var is not None and attr in ('subplot', 'axes') \
                and not isinstance(node.func.value, ast.Name):
            return [], False, None
        found.append((ln, node.col_offset, attr, node))
    suspect = None
    _known = [f[3] for f in found]
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        if node.func.attr not in _SUSPECT_ATTRS or node in _known:
            continue
        if node.func.attr in _AXES_CREATORS or node.func.attr in _IMPLICIT_CREATORS:
            continue
        ln = getattr(node, 'lineno', None)
        if ln is None or not (lo <= ln < hi):
            continue
        suspect = ln if suspect is None else min(suspect, ln)
    found.sort(key=lambda x: (x[0], x[1]))
    entries, sigs = [], set()
    for _ln, _co, attr, node in found:
        if attr == 'subplots':
            shape = _grid_size(node)
            sq = _grid_squeeze(node)
            if shape is None or sq is None:
                return [], False, None
            names = _assign_names_of(tree, node)
            if len(names) >= 2:
                container = names[1]
            elif names:
                container = '%s[1]' % names[0]     # 单名接的是 (fig, axs) 元组
            else:
                container = None
            nr, nc = shape
            for k in range(nr * nc):
                entries.append(None if container is None
                               else (_grid_element(container, nr, nc, sq, k),
                                     container, _ln, False))
            continue
        names = _assign_names_of(tree, node)
        if attr == 'colorbar':
            entries.append(('%s.ax' % names[0], names[0], _ln, False) if names
                           else None)
            continue
        if attr in _AXES_RETURNING:
            if names:
                entries.append((names[0], names[0], _ln, False))
                continue
            if attr in ('add_axes', 'add_subplot'):
                expr = _inline_cax_of(tree, node)
                if expr:
                    entries.append((expr, expr.split('.')[0], _ln, False))
                    continue
                entries.append((None, None, _ln, True))  # 匿名但**确实建了一条轴**
                continue
            # t8 修复：`_call_sig(node)` 返回的是**字符串列表**，不能喂给 ast.dump()
            # （TypeError: expected AST, got 'list'）→ 无赋值名的 plt.subplot(1,2,1)
            # 让 apply 崩成 internal_error。join 成字符串即达目的；此行语义=「槽位
            # 签名」去重（相同 subplot 调用=复用不新建轴，参数不同=新建轴）保持不变。
            _sig = '%s|%s' % (attr, '|'.join(_call_sig(node)))
            if _sig in sigs:
                continue                                # 同一个槽位是"复用"，不新建
            sigs.add(_sig)
            entries.append((None, None, _ln, True))
            continue
        # pyplot 隐式建轴（含 plt.gca()）：只有在"之前一条轴都没有"时才真的建轴；
        # **不接受**它们的赋值名字（返回值不是 Axes / 身份依赖"当前图"）。
        if entries:
            continue
        entries.append((None, None, _ln, True))
    return entries, True, suspect


def _call_sig(node):
    """调用的"槽位签名"（去掉 lineno 的字面参数），用来判 `plt.subplot` 是不是复用。"""
    return [ast.dump(a) for a in node.args] + \
           ['%s=%s' % (k.arg, ast.dump(k.value)) for k in node.keywords]

def _anchor_positions(tree, src, data, anchor):
    """块**可能落在**的位置 [(作用域, 行号), ...] —— 目标证明必须对每个位置都成立。

    来源与真实候选点一致（insert 前的 `cands`）：第 fig_index 个 savefig、`find_anchor`
    选出的锚点、以及"追加在文件末尾"（模块作用域，行号 = 行数+1）。比真实候选集合更
    保守（真实 cands 还会做同一性去重），保守方向 = 宁可不写。
    """
    out = []
    saves = find_savefigs(tree)
    fi = data.get('fig_index') if isinstance(data, dict) else None
    if not isinstance(fi, int):
        fi = 0
    if 0 <= fi < len(saves):
        out.append(saves[fi])
    if anchor and anchor[1] is not None:
        out.append(anchor[1])
    pos = [(_node_scope(tree, n.lineno), n.lineno) for n in out]
    pos.append((tree, len(src.splitlines()) + 1))
    seen, uniq = set(), []
    for sc, ln in pos:
        k = (id(sc), ln)
        if k not in seen:
            seen.add(k)
            uniq.append((sc, ln))
    return uniq


def _prune_refused(data, unexpressed):
    """把"块表达不了/目标证不出"的字段从**语义比对目标**里摘掉。

    只影响语义层的比对目标，不影响 pending/warnings 的如实报告（见 §T70）：
    `ax%d.<字段>` 摘字段、`figsize` 摘画布尺寸、`fig.pos` 只是"块里没有 set_position"
    的标记（不是某个轴的字段），忽略。
    """
    drop: Dict[int, set] = {}
    figsize = False
    for it in unexpressed or ():
        m = re.match(r'ax(\d+)\.([A-Za-z_]+)$', it)
        if m:
            drop.setdefault(int(m.group(1)), set()).add(m.group(2))
        elif it == 'figsize':
            figsize = True
    if not drop and not figsize:
        return data
    out = dict(data or {})
    if drop:
        axes = []
        for a in _axis_items(out):
            b = dict(a)
            for f in drop.get(b.get('index'), ()):
                b.pop(f, None)
            axes.append(b)
        out['axes'] = axes
    if figsize:
        out['figsize_in'] = None
        out['figsize'] = None
    return out


def _gcf_provable(tree, positions, fig_call):
    """T70-B：纯 pyplot 单图是否**可证**（`plt.gcf()` 就是那张图、`.axes[k]` 就是第 k 条）。

    条件（船长 T70 批示逐字）：
      ① 全文件只有**一次**建图调用、且就是本次目标那张、不在循环里、与每个候选锚点同
         作用域、位在锚点之前；
      ② 没有 `plt.close(...)`（关掉之后 `gcf()` 会新建一张空图 ⇒ 目标就不是它了）；
      ③ 没有额外建轴来源（colorbar / inset_axes / inset_locator / twinx / twiny /
         make_axes_locatable / append_axes / secondary_* / indicate_inset* /
         subplot_mosaic）—— 它们会让运行期 axes 的条数或顺序与静态枚举不一致。
    """
    if fig_call is None:
        return False
    figs = [n for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            and n.func.attr in FIG_FACTORIES]
    if len(figs) != 1 or figs[0] is not fig_call:
        return False
    if find_enclosing_loop(tree, fig_call) is not None:
        return False
    scope0 = _node_scope(tree, fig_call.lineno)
    for sc, ln in positions:
        if sc is not scope0 or ln <= fig_call.lineno:
            return False
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr in ('close',) + _EXTRA_AXES_ATTRS:
            return False
    return True


def _numbered_fig_expr(tree, positions, fig_call):
    """T74：多图纯 pyplot 的**可证**定位 —— 返回 ``'plt.figure(<n>)'``，否则 None。

    **为什么 `plt.figure(n)` 可证、而 `plt.gcf()` 不可证**：
      * ``plt.gcf()`` = "**当前活动**的那张图" ⇒ 脚本末尾通常是**最后一张**，不等于目标图
        （T70 禁掉的正是它；实测多图脚本里 gcf() 就是最后一张）；
      * ``plt.figure(n)`` = "**编号为 n 的那张图**"（已存在则激活并返回）⇒ 只要 n 能在块
        位置**静态枚举**、且期间没被 ``close()`` 释放，它**就是**目标图 ⇒ **可证**。

    条件（船长 T74 批示四条边界，一条不放宽）：
      ① 目标那次建图调用在**每个**候选锚点之前；**目标之前**的建图调用（含自身）可
         **按执行顺序**静态编号：全在模块层作用域、不在循环里；编号口径 = `plt.figure(<int
         字面量>)` / `num=<int>` 用该值并把上界抬高，其余（`plt.figure()` / `plt.subplots()`）
         取上界+1（matplotlib 的自动编号）；`num='title'` 之类**不可枚举** ⇒ None；
      ② **每个锚点之前**没有 ``close(...)``（关图会让编号被复用/重置）；
      ③ 建轴来源可完全枚举由调用方 `_axes_binding_entries` 保证（数不全 ⇒ 那些轴根本进不了
         axes_expr，仍按 T70 拒绝）；
      ④ 块仍在最后一个 savefig/show 之前 —— 由 `_anchor_positions` 的候选集合保证。

    注：目标**之后**的建图调用不影响它的编号（自动编号只影响"下一条"），故不作约束。
    """
    if fig_call is None or positions is None or not fig_call.lineno:
        return None
    _lim = min(ln for _sc, ln in positions)
    if _lim <= fig_call.lineno:
        return None                          # 目标必须在每个候选锚点之前
    for sc, ln in positions:
        if sc is not tree:
            return None                      # 块可能落在非模块层作用域 ⇒ 拒绝
        for n in ast.walk(tree):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) \
                    and n.func.attr == 'close' \
                    and _node_scope(tree, n.lineno) is tree \
                    and n.lineno < ln:
                return None                  # ② 锚点之前有 close ⇒ 编号可能被复用
    # 目标之前（含自身）的建图调用：必须全部可枚举
    before = []
    for n in ast.walk(tree):
        if not (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)):
            continue
        if n.func.attr not in FIG_FACTORIES:
            continue
        if getattr(n, 'lineno', None) is None or n.lineno > fig_call.lineno:
            continue
        if _node_scope(tree, n.lineno) is not tree:
            return None
        if find_enclosing_loop(tree, n) is not None:
            return None
        before.append(n)
    if fig_call not in before:
        return None
    before.sort(key=lambda n: (n.lineno, n.col_offset))
    num, target = 1, None
    for n in before:
        explicit = None
        kw = next((k for k in n.keywords if k.arg == 'num'), None)
        if kw is not None:
            if isinstance(kw.value, ast.Constant) \
                    and isinstance(kw.value.value, int) \
                    and not isinstance(kw.value.value, bool):
                explicit = kw.value.value
            else:
                return None                  # num='title' 之类编号不可枚举
        elif n.args and isinstance(n.args[0], ast.Constant) \
                and isinstance(n.args[0].value, int) \
                and not isinstance(n.args[0].value, bool):
            explicit = n.args[0].value
        if explicit is not None:
            num = max(num, explicit)
            _cur = explicit
        else:
            _cur = num
            num += 1
        if n is fig_call:
            target = _cur
    if target is None:
        return None
    return 'plt.figure(%d)' % target

def _block_targets(tree, data, fig_index, positions, needed, fig_var=None):
    """T70：块内**允许**的目标表达式表；证不出的字段进 refused（调用方摘出并如实报）。

    允许（逐字见报告 §T70）：
      ① 目标对象自己的**变量名**（同一作用域、绑定在锚点之前、中途没被重新赋值/删除/
         被 import 遮蔽）：`ax = fig.add_axes(...)`、`ax = fig.add_subplot(...)`、
         `plt.subplot(...)` 的接收者、`fig, ax = plt.subplots()` 的 `ax`、
         `cbar = fig.colorbar(...)` 的 `cbar.ax`、`ax2 = ax.twinx()` 的 `ax2`；
      ② 单次**字面量形状** `subplots(...)` 返回容器的元素：`axs[k]` / `axs[r, c]`
         —— 元素身份由那次调用唯一确定，与运行期 `fig.axes` 下标无关；
      ③ figure 级（`set_size_inches`）只认 figure 自己的变量名；`plt.gcf()` 一律拒绝。
    """
    refused: List[str] = []
    axes_expr: Dict[int, str] = {}
    items = _axis_items(data)
    entries, ok, suspect = _axes_binding_entries(tree, fig_index, fig_var)
    if not ok:
        entries, suspect = [], 0                # 循环建轴 / mosaic / 形状不是字面量
    _fig_node = _fig_call_node(tree, fig_index)
    _fig_nm = _call_assign_name(tree, _fig_node) if _fig_node is not None else None
    if _fig_nm and not _name_still_bound(_node_scope(tree, _fig_node.lineno),
                                         _fig_node.lineno, _fig_nm, positions):
        _fig_nm = None
    _gcf_ok = _gcf_provable(tree, positions, _fig_node)
    # T74：多图纯 pyplot（图没接名字）的**可证**定位 —— `plt.figure(<n>)`。
    # 单图可证（_gcf_ok）时优先沿用旧路（输出字节不变）；单图不成立才试编号路。
    _num_expr = None
    if _fig_nm is None and not _gcf_ok:
        _num_expr = _numbered_fig_expr(tree, positions, _fig_node)
    for a in items:
        i = a.get('index')
        if not isinstance(i, int):
            continue
        ent = entries[i] if 0 <= i < len(entries) else None
        if ent is None:
            continue
        # 参数只列"被调过的轴"，条数天然对不上 ⇒ 不能拿它当完整性判据（实测：2 条网格
        # 轴 + 1 条 cax 的脚本、参数只列 2 条，会被全部误拒）。完整性改成"这条轴之前
        # 没有认不出的建轴调用"（认不出的调用会顶掉运行期序号 ⇒ 那条轴不可证）。
        if suspect is not None and ent[2] > suspect:
            continue
        expr, root, bln, _anon = ent
        if expr is not None:
            # ① 目标对象自己的变量名 / 单次字面量容器的元素：根名字必须仍绑在同一个对象上
            scope0 = _node_scope(tree, bln)
            if _name_still_bound(scope0, bln, root, positions):
                axes_expr[i] = expr
            continue
        # ② T70-B 纯 pyplot 单图可证子集：`<figure>.axes[k]` / `plt.gcf().axes[k]`
        if _gcf_ok:
            axes_expr[i] = '%s.axes[%d]' % (_fig_nm or 'plt.gcf()', i)
        elif _num_expr:
            # T74：`plt.figure(<n>).axes[k]` —— n 静态可枚举（`_numbered_fig_expr`），
            # `.axes[k]` 的顺序与条数由 `_axes_binding_entries` 的完全枚举保证。
            axes_expr[i] = '%s.axes[%d]' % (_num_expr, i)
    for a in items:
        i = a.get('index')
        if not isinstance(i, int) or i in axes_expr:
            continue
        for f in sorted((needed or {}).get(i) or ()):
            refused.append('ax%d.%s' % (i, f))
    # figure 级：能证 figure 变量名就用它；T70-B 条件下 `plt.gcf()` 也可证（同一张图）
    fig_expr = _fig_nm
    if fig_expr is None and _gcf_ok:
        fig_expr = 'plt.gcf()'
    if fig_expr is None and _num_expr:
        fig_expr = _num_expr                 # T74：figure 级也走编号可证（如 set_size_inches）
    return axes_expr, fig_expr, refused


def render_block(data, fig_var, mappables, cb_args, figsize_in=None,
                 mappable_map=None, tag=None, multi_fig=False, needed=None,
                 targets=None, fig_target=None):
    """从参数数据生成纯 matplotlib 调整块文本。返回 (text, warnings)。

    ``needed``（T35）：``{轴序号: {字段}}`` —— **只有这些字段才写进块**。
    为 None 时保持老行为（把参数里所有非空字段都写出来）。为什么要它：
    describe 产物带着"调用方从没要求过"的默认字段（normalize 补的 xscale/yscale
    旧默认是 'linear'），旧渲染器**无条件**给每个轴写 `set_xscale/set_yscale` ⇒
    源码明写 log 的轴被改成 linear（语义被改，L0 复测 E5）；反过来，当参数里
    所有字段都与源码一致时，块也不该凭空写 9-10 行（M4 的次级现象）。
    """
    axes = _axis_items(data)
    warnings: List[str] = []

    def _want(i, field):
        """这个字段是否**确实需要落地**（needed=None → 老行为：参数里有值就写）。"""
        return needed is None or field in (needed.get(i) or ())

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
    if any('.axes[' in (v or '') for v in (targets or {}).values()):
        # T70-B / T74：为什么 `.axes[k]` 在这里是**可证**的（写进块里，人和审阅者都能核）
        if (fig_target or '').startswith('plt.figure('):
            # T74：多图纯 pyplot —— 编号可静态枚举，所以 `plt.figure(n)` 指的就是目标图
            out.append('# 目标定位（T74）：建图调用可静态编号、模块层没有 close、'
                       '也没有 colorbar/inset/twin/… 额外建轴，'
                       '因此 %s.axes[k] 就是**目标图**静态第 k 条轴'
                       % fig_target)
        else:
            out.append('# 目标定位：本文件只有一次建图、没有 colorbar/inset/twin/… 额外建轴'
                       '（也没有 close），因此 %s.axes[k] 就是静态第 k 条轴'
                       % (fig_target or 'plt.gcf()'))
    if figsize_in and fig_target:
        # T70：figure 级也**只认可证的 figure 变量名** —— `plt.gcf()` 在多图脚本里
        # 指的是"此刻的当前图"，可能根本不是目标图（T67 的 C 缺陷就是这么来的）。
        out.append('%s.set_size_inches(%s, %s)'
                   % (fig_target, _num(figsize_in[0]), _num(figsize_in[1])))
    # 1) 位置：**逐轴按证明过的表达式**写（T70：不再用 `zip(fig.axes, ...)` 运行期下标）
    poss = [(a.get('index'), a.get('pos')) for a in axes
            if a.get('pos') is not None and _want(a.get('index'), 'pos')]
    if poss:
        for _i, _p in poss:
            _ex = (targets or {}).get(_i)
            if _ex is None:
                continue            # 证不出目标轴的字段已被调用方摘出 needed
            out.append('%s.set_position([%s])'
                       % (_ex, ', '.join(_fmt_pos(v) for v in _p)))
    # 2) 逐轴属性
    for a in axes:
        i = a.get('index')
        if i is None:
            continue
        ax = (targets or {}).get(i)
        if ax is None:
            # T70：这个轴的目标对象证不出（没有可证的变量名）—— 它的字段已被调用方
            # 摘出 needed 并记 pending；这里再兜一道，**绝不退回 `fig.axes[i]` 寻址**。
            continue
        is_cb = bool(a.get('is_colorbar'))
        if is_cb and _want(i, 'pos'):
            out.append('# colorbar 轴：先解除自动定位/盒比，否则重绘会重置位置与宽度')
            out.append('%s.set_axes_locator(None)' % ax)
            out.append('%s.set_box_aspect(None)' % ax)
        if a.get('title_fontsize') is not None and _want(i, 'title_fontsize'):
            out.append('%s.title.set_fontsize(%s)' % (ax, _num(a['title_fontsize'])))
        # x/y 标签字号分开写（T11）；T73（零兼容）：y 没写就**没有**这一行。
        _lx, _ly = _label_fontsizes(a)
        if _lx is not None and _want(i, 'label_fontsize'):
            out.append('%s.xaxis.label.set_fontsize(%s)' % (ax, _num(_lx)))
        if _ly is not None and _want(i, 'label_fontsize_y'):
            out.append('%s.yaxis.label.set_fontsize(%s)' % (ax, _num(_ly)))
        _tx, _ty = _tick_fontsizes(a)
        if a.get('tick_fontsize_y') is None:
            if _tx is not None and _want(i, 'tick_fontsize'):
                # T78：**必须写显式 `axis='x'`**。不带 axis 的 `tick_params(labelsize=N)`
                # 运行期一次改 x 和 y —— 用户只说改 x，块却顺手把 y 也改了（与 T73
                # "没说 y 就不动 y" 直接矛盾）。原位路径同理（见 `_find_tick_call_for_axis`）。
                out.append("%s.tick_params(axis='x', labelsize=%s)"
                           % (ax, _num(_tx)))
        else:
            # T23/T73：y 显式给了才分两行写（`axis='x'` / `axis='y'`）。
            if _tx is not None and _want(i, 'tick_fontsize'):
                out.append("%s.tick_params(axis='x', labelsize=%s)"
                           % (ax, _num(_tx)))
            if _ty is not None and _want(i, 'tick_fontsize_y'):
                out.append("%s.tick_params(axis='y', labelsize=%s)"
                           % (ax, _num(_ty)))
        if a.get('grid') is not None and _want(i, 'grid'):
            out.append('%s.grid(%s)' % (ax, 'True' if a['grid'] else 'False'))
        # 轴范围：块是"源码里没有 set_xlim 写法"时的兜底，所以这里必须带上，
        # 否则 --style block 会静默丢掉用户设的范围（grid 之后、spines 之前）。
        for field, meth in (('xlim', 'set_xlim'), ('ylim', 'set_ylim')):
            rng = a.get(field)
            if isinstance(rng, (list, tuple)) and len(rng) == 2 \
                    and _want(i, field):
                out.append('%s.%s(%s, %s)' % (ax, meth, _num(rng[0]), _num(rng[1])))
        if _want(i, 'spines'):
            for k, vis in (a.get('spines') or {}).items():
                # T18：True 也要写成一行 —— 用户在调图时把某条 spine 重新打开
                # （False→True）时，过去块里什么都不写，于是这个字段**永远落不了地**。
                out.append('%s.spines[%r].set_visible(%s)'
                           % (ax, k, 'True' if vis else 'False'))
        # 刻度：只写"安全"的取值 —— colorbar 轴的 function 刻度等内部刻度一律不碰
        # （裸调 set_xscale('function') 运行时会报错，缺 functions 实参）。
        for field, meth in (('xscale', 'set_xscale'), ('yscale', 'set_yscale')):
            sc = a.get(field)
            if isinstance(sc, str) and sc.strip().lower() in _SAFE_SCALES \
                    and _want(i, field):
                out.append('%s.%s(%r)' % (ax, meth, sc.strip().lower()))
        for line in (a.get('lines') if _want(i, 'lines') else None) or []:
            j = line.get('index')
            if j is None:
                continue
            if line.get('linewidth') is not None:
                out.append('%s.lines[%d].set_linewidth(%s)'
                           % (ax, j, _num(line['linewidth'])))
            if line.get('color'):
                out.append('%s.lines[%d].set_color(%r)' % (ax, j, line['color']))
        if (a.get('clim') is not None and _want(i, 'clim')) \
                or (a.get('cmap') and _want(i, 'cmap')):
            mref, warn = _mappable_ref(ax, a, is_cb, mappables, cb_args,
                                       mappable_map, multi_fig)
            if warn:
                warnings.append(warn)
            if a.get('clim') is not None and mref and _want(i, 'clim'):
                out.append('%s.set_clim(%s, %s)'
                           % (mref, _num(a['clim'][0]), _num(a['clim'][1])))
            if a.get('cmap') and mref and _want(i, 'cmap'):
                if _is_registered_cmap(a['cmap']):
                    out.append('%s.set_cmap(%r)' % (mref, a['cmap']))
                else:
                    warnings.append('ax%s 的 cmap %r 不是已注册 colormap'
                                    '（ListedColormap 之类返回 from_list 名），已跳过'
                                    % (i, a['cmap']))
        lg = a.get('legend') if _want(i, 'legend') else None
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
    _txt = '\n'.join(out) + '\n'
    for _ln in _txt.splitlines():
        if _ln.strip() and (BLOCK_START in _ln or BLOCK_END in _ln):
            if not _ln.lstrip().startswith((BLOCK_START[:8], BLOCK_END[:8])):
                # T51 STEP2 自检：块**自己**的哨兵行必须"行首即哨兵"（只查新插入的块体，
                # 不在整份 content 上 find —— 那会命中本来就在的旧块哨兵/被替换块）。
                return '', ['生成的块标记没有独占一行（未写盘）']
    return _txt, warnings


# --------------------------------------------------------------------------
# 幂等插入/替换 + 侧边元数据
# --------------------------------------------------------------------------
def _unclosed_block_lines(lines):
    """返回"只有起始哨兵、没有收尾哨兵"的起始行号（1-based）列表。

    这类残缺标记（块被手工截断 / 只剩一行 START）会让"整体替换旧块"的幂等机制失效：
    `_block_spans` 只认成对的哨兵，于是写回会**再插一份块**，两份块互相打架而命令报
    全绿（审计 E12）。这里只负责如实报告，不自动猜哪块该替哪块。
    """
    out, start = [], None
    for idx, line in enumerate(lines):
        if any(s in line for s in _ALL_STARTS):
            if start is not None:
                out.append(start)
            start = idx + 1
        elif any(e in line for e in _ALL_ENDS) and start is not None:
            start = None
    if start is not None:
        out.append(start)
    return out


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


_TS_RE = re.compile(r'\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}')


def _norm_ts(text):
    """抹掉调整块说明行里的"生成于 <日期 时间>"：只差这一次生成时间的两次写回，
    应当被判成"没有改动"（否则重复 apply 永远报 ok/已写回，而不是 no_change）。"""
    return _TS_RE.sub('<ts>', text)


def insert_or_replace(src, block, anchor, meta_path, tag=None):
    """把 block 插入 src：已有调整块则整体替换，否则按锚点插入（缩进对齐锚点行）。

    返回 ``new_src``（**只有源码**；旧的 docstring 写成"返回 (new_src, new_span)"，
    与实现不符，审计 E14）。
    """
    lines = src.splitlines(keepends=True)
    # 调整块必须**沿用原文件的换行风格**：`render_block` 用 '\n' 拼接，直接 join 进
    # 一个 CRLF 文件会得到"正文 CRLF + 块内 LF"的混行文件（审计 E11）。
    _nl = '\r\n' if '\r\n' in src else '\n'
    if _nl != '\n':
        block = block.replace('\r\n', '\n').replace('\n', _nl)
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
        # T42（Bug 4）：锚点落在**行内复合语句**（`fig.tight_layout(); fig.savefig(...);
        # plt.close()`）里时，按 col_offset 缩进会把整块塞进那一行中间 —— 生成代码
        # 相对后续顶格注释产生意外缩进（实测 `unexpected indent (line 33)`，合法请求
        # 直接失败）。改成插到**整行行首**，缩进用该行自己的前导空白。
        _l_txt = lines[idx] if 0 <= idx < len(lines) else ''
        if ';' in _l_txt.split('#')[0]:
            indent = _l_txt[:len(_l_txt) - len(_l_txt.lstrip())]
        block = _reindent(block, indent)
        if (idx == len(lines) and lines
                and not lines[-1].endswith(('\n', '\r\n'))):
            # T51 STEP1：追加到文件尾、且末行不带换行 → 先补一个换行，否则块起始哨兵被
            # 黏到上一行末尾，工具认不出自己的块（第二次 apply 再追加一遍）。
            new_src = ''.join(lines[:idx]) + _nl + block + ''.join(lines[idx:])
        else:
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
def verify_run(script, python=None, timeout=300, script_args=None):
    """Agg 无头重跑脚本：exit 0 = 通过。返回 (ok, err_tail)。

    cwd 切到脚本目录——脚本里的相对路径（pd.read_csv('data.csv') / np.load /
    open(...)）必须在脚本目录下解析，与 verify_semantic 的 collect() 口径一致，
    否则会被误判"写回后跑不通"而回滚。

    ``script_args``：追加到 ``[python, script]`` 之后（bug #6）。依赖
    ``sys.argv`` 的脚本（``python fig.py --data x.npz``）重跑时拿不到参数
    就与基线行为不一致 → 默认回滚 + 误归因"缺数据/依赖"。``python script args``
    的 argv 本来就是 ``[script] + args``，直接拼进命令行即可。
    """
    python = python or sys.executable
    env = dict(os.environ)
    env['MPLBACKEND'] = 'Agg'
    cwd = os.path.dirname(os.path.abspath(script))
    try:
        cmd = [python, script] + list(script_args or [])
        r = subprocess.run(cmd, capture_output=True,
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
              style='block', fast_check=False, backup=None,
              keep_unverified=False, script_args=None):
    """确定性写回。返回结果 dict：
      {reason, changes, block, backup, warnings, verified, semantic, err}
    reason：ok / best_effort / no_change / no_fig / no_axes / fail。
    semantic=True 时在"能跑通"之后再比对目标图状态是否真的等于参数。
    only_fig=True（默认）循环出图时按 fig_index 加守卫只改目标那张；False = 统一应用。
    style：'block'（默认，插调整块）/ 'inplace'（直接改原代码数字，用户主推）。
    fast_check=True 且 verify=False 时走**快速验证档**：只做语法静态检查 + 必要时回滚，
    **不重跑脚本**（保证很弱，见 _FAST_VERIFY_NOTE 的原话）。
    backup：**复用**上次已经做好的备份路径，而不是再备一份（L0-4）。`--style auto`
    在一条命令里可能写两次（先原位、再退块），第二次再备份就会把第一次的备份
    **覆盖**成"已经改过一次"的中间态 → `revert` 拿不回原件（主仓实测 20154193__RealSpace.py：
    写回 20567 B → revert 后 20568 B）。apply 把第一次的 `res['backup']` 传进来即可。
    script_args：验证重跑透传给用户脚本的 ``sys.argv[1:]``（bug #6，``apply --write
    -- <脚本参数...>`` 的 CLI 契约）。原位与块两条路径的 verify_run /
    verify_semantic / baseline 重跑全部带上，依赖 ``sys.argv`` 的脚本才能与基线
    一致地重跑（否则行为不一致 → 默认回滚 + 误归因"缺数据/依赖"）。
    """
    script = os.path.abspath(script)
    # style 必须如实回报：apply 用它选"已原位改 N 处"还是"已插入调整块"两种消息，
    # --json 里 agent 也靠它判断这次到底动了源码的哪个形态。原先这条路径漏设，
    # 结果**块模式以外的写回在 JSON 里 style=None**、人读消息也永远说"插入调整块"
    # （2026-10-10 写 §11 用例时抓到）。
    res: Dict[str, Any] = {'reason': FAIL, 'changes': [], 'block': None,
                           'backup': None, 'warnings': [], 'verified': None,
                           'err': '', 'style': 'block'}
    axes = _axis_items(data)
    if not axes and not data.get('figsize_in'):
        # RC-8：只改画布尺寸也必须能落地。`figsize=(w,h)` 字面量是绘图脚本里最常见
        # 的可构造目标（论文型脚本 91.8% 有它），而参数里没有 axes 项时过去**直接**
        # NO_AXES 早退，整份改动被丢弃（实测 rc=1 / reason=no_axes / 脚本未改）。
        res['reason'] = NO_AXES
        return res
    try:
        # 用 params.read_text 兜编码：GBK / UTF-8-BOM 的老脚本也要能读能写回
        # （t1-J：原先是裸 open(encoding='utf-8')，GBK 脚本直接抛 UnicodeDecodeError，
        #  CLI 下 --json 的 stdout 全空）。
        src, _src_enc, _src_bom = params.read_text_ex(script)
        if _src_bom:
            # 原文件带 UTF-8 BOM → 写回也必须带（read_text 把 'utf-8-sig' 规范成
            # 'utf-8' 只是为"不凭空加 BOM"，不能反过来把本来有的 BOM 删掉，E10）。
            _src_enc = 'utf-8-sig'
        tree = ast.parse(src)
    except (OSError, SyntaxError, UnicodeDecodeError) as e:
        res['err'] = str(e)
        return res

    # ------------------------------------------------------------------
    # T61：坏参数 fail-closed —— 脚本侧的两条越界判据
    #
    # 为什么放在这里（而不是 params.validate_errors）：这两条要看**脚本**才判得出来，
    # 而 writeback() 是原位/块两条写回路径**唯一**的共同入口 —— 判在这里，两条路径
    # 不会各抄一份，也保证"先校验完再动文件"（下面才开始算备份/写盘）。
    # 两个判据各自独立成函数，便于反向验证（把函数体换成 return None 即可）。
    # ------------------------------------------------------------------
    _bad = _axis_index_beyond(tree, data)
    if _bad:
        res['reason'] = BAD_PARAMS
        res['err'] = _bad
        return res
    _bad = _fig_index_beyond(tree, data)
    if _bad:
        res['reason'] = BAD_PARAMS
        res['err'] = _bad
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
                                  encoding=_src_enc, fast_check=fast_check,
                                  backup=backup,
                                  keep_unverified=keep_unverified,
                                  script_args=script_args)

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

    _frag = _unclosed_block_lines(src.splitlines())
    if _frag:
        # E12：残缺标记 → 幂等替换认不出旧块 → 会再插一份。这里如实警告（不去猜）。
        pre_warnings.append(
            '检测到 %d 处只有起始哨兵、没有收尾哨兵的调整块痕迹（第 %s 行）：'
            '本次会另插一块（不猜哪块该替哪块），落地后请手工删掉残缺那份'
            % (len(_frag), '、'.join(str(x) for x in _frag)))
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
    # 只有"源码里**没有**可写的 figsize 字面量（或它是变量/位置参数）"时，块才补一条
    # `set_size_inches`。等值（本来就对）时补这条有两个坏处：① 第二次 apply 的块多一行
    # → `_norm_ts` 比对不等 → 幂等破功；② 位置上重复表达同一件事。
    _fs_ok_b = bool(figsize_in) and (
        figsize_edited or figsize_already_ok(tree, figsize_in, _fig_idx_b))
    figsize_in_block = figsize_in if (figsize_in and not _fs_ok_b) else None

    # T35：块只写"确实需要落地"的字段 —— 定义 = **调用方显式给了目标值** ∧
    # **证明不了它已等于源码现值**（与原位路径同一套等价证明）。
    # normalize 补的默认值（旧版 xscale/yscale='linear'）既不是调用方要求的、也不该
    # 被当目标 ⇒ 不会再被写进块（否则源码明写 log 的轴会被改成 linear，L0 复测 E5）；
    # 全部字段都已等值时 needed 为空 ⇒ 不插块（M4 的次级现象：字面量没改却仍写块）。
    # T35：等价证明必须只看**用户源码** —— 我们自己的调整块（若已存在）里带着
    # `fig.axes[0].grid(True)` / `fig.axes[0].lines[0]...` 这类写法，会被"源码里有
    # 这种写法"的检查当成用户写的，于是**第二次 apply 算出的 needed 比第一次大**，
    # 生成的块多出几行 → 幂等破功（tests §1 实测：26 行 → 28 行，reason 从
    # no_change 变回 ok）。所以这里把已有块摘掉再证明。
    _src_nb, _tree_nb = src, tree
    try:
        _span_nb = _existing_span(src.splitlines(keepends=True),
                                  _meta_path(params_path), tag)
    except (OSError, ValueError, KeyError):
        _span_nb = None
    if _span_nb:
        _ls_nb = src.splitlines(keepends=True)
        _cand_nb = ''.join(_ls_nb[:_span_nb[0] - 1] + _ls_nb[_span_nb[1]:])
        try:
            _tree_nb = ast.parse(_cand_nb)
            _src_nb = _cand_nb
        except SyntaxError:
            _src_nb, _tree_nb = src, tree
    _needed_b = _needed_block_fields(_tree_nb, data, _src_nb,
                                     data.get('fig_index')
                                     if isinstance(data.get('fig_index'), int) else 0)
    # ---- T70：块内只许用**静态可证的对象名**定位（T67 的 pos 防线推广到所有族）----
    # 过去块的每一行都是 `fig.axes[i].xxx()`（或 `zip(fig.axes, ...)`）—— 那是
    # **运行期列表下标**：运行期 fig.axes 还包含 cax/inset/twin/parasite 轴，静态轴数
    # 一漂，改动就落到别的轴（甚至别的图）。现在先算"目标对象有没有可证的变量名"：
    # 证得出 ⇒ 块里用那个名字；证不出 ⇒ 该字段**不写**（fail-closed），如实报 pending。
    _positions = _anchor_positions(tree, src, data, anchor)
    _targets, _fig_target, _refused = _block_targets(
        tree, data, _fig_idx_b if isinstance(_fig_idx_b, int) else 0, _positions,
        _needed_b, fig_var)
    _fig_refused = bool(figsize_in_block and not _fig_target)
    if _fig_refused:
        figsize_in_block = None
    if _fig_refused and _needed_b:
        # 块路径确实要写、figure 级目标又证不出 ⇒ 这个字段本次落不了地（如实报）。
        # 反过来：块里一个字段都不剩时，画布尺寸还有**原位**那条路（_writeback_inplace
        # 改字面量），那条路能落地就不该报"没落地"（否则又是 T68 那种谎报）。
        _refused.append('figsize')
    if _refused:
        for _ref in _refused:
            _mm = re.match(r'ax(\d+)\.(.+)$', _ref)
            if _mm:
                _ri, _rf = int(_mm.group(1)), _mm.group(2)
                if _ri in _needed_b:
                    _needed_b[_ri].discard(_rf)
                    if not _needed_b[_ri]:
                        del _needed_b[_ri]
        pre_warnings.append(
            '块内只能按**静态可证的变量名**定位目标对象；这些字段证不出目标'
            '（没有可证的变量名，而运行期下标 fig.axes[i] / plt.gcf() 会写到别的轴'
            '或别的图上）⇒ 按 fail-closed 处理，本次不写：%s'
            % '、'.join(_refused))
    if not _needed_b and not figsize_in_block:
        if data.get('figsize_in'):
            # 只有画布尺寸要改（块里没有别的字段）：交给原位路径写 figsize 字面量 ——
            # 不能因为"块里没东西可写"就把这次改动丢掉（RC-8b：只有 figsize 的参数
            # 也必须能落地）。返回的 style 会是 'inplace'，如实反映"实际是原位改的"。
            _r2 = _writeback_inplace(script, src, tree, data, params_path,
                                     verify, python, timeout, dry_run, semantic,
                                     encoding=_src_enc, fast_check=fast_check,
                                     backup=backup,
                                     keep_unverified=keep_unverified)
            if _refused:
                _r2['warnings'] = (list(_r2.get('warnings') or [])
                                   + pre_warnings[-1:])
                _r2['needs_block'] = True
                _r2['pending'] = sorted(set(list(_r2.get('pending') or [])
                                            + _refused))
                if _r2.get('reason') in ('no_change', 'ok'):
                    _r2['reason'] = BEST_EFFORT
            return _r2
        if _refused:
            # 目标证不出 ⇒ **一个字节都不写**，如实报"没能落地"（人读走 T68 的
            # not_written 通道：applied=false + reason=best_effort + pending 非空）。
            res.update({'reason': BEST_EFFORT, 'changes': [], 'block': None,
                        'backup': None, 'warnings': pre_warnings,
                        'verified': None, 'semantic': 'skipped',
                        'applied': False, 'needs_block': True,
                        'pending': list(_refused), 'block_lines': 0,
                        'style': 'block', 'anchor': None})
            return res
        res.update({'reason': 'no_change', 'changes': [], 'block': None,
                    'backup': None, 'warnings': pre_warnings,
                    'verified': None, 'semantic': 'skipped',
                    'applied': False, 'needs_block': False,
                    'block_lines': 0, 'style': 'block', 'anchor': None})
        return res
    block, warnings = render_block(data, fig_var, mappables, cb_args,
                                   figsize_in_block, mappable_map, tag,
                                   multi_fig=(n_fig > 1), needed=_needed_b,
                                   targets=_targets, fig_target=_fig_target)
    warnings = pre_warnings + warnings
    # ---- T18 自检：块表达不了的目标字段 -------------------------------------
    # 每个 pending 字段都必须能在块行里找到对应写法；找不到就不能报
    # ok/applied=true/needs_block=false，而要 needs_block=true + pending=[该字段]
    # （显式 --style inplace 由 apply 判成 rc=1）。见 _block_unexpressed_fields。
    _unexpressed = _block_unexpressed_fields(block, data, fig_var,
                                             needed=_needed_b,
                                             targets=_targets)
    if _refused:
        _unexpressed = list(_refused) + [x for x in _unexpressed
                                         if x not in _refused]
    if _unexpressed:
        warnings.append('这些字段调整块表达不了、本次**没能落地**：%s'
                        '（可手动改，或把参数收敛到块能写的字段）'
                        % '、'.join(_unexpressed))

    def _mark_unexpressed(res0):
        """把"块表达不了"如实写进结论（只在成功/无改动路径上生效）。"""
        if _unexpressed and res0.get('reason') in ('ok', 'best_effort',
                                                   'no_change'):
            res0['reason'] = BEST_EFFORT
            res0['needs_block'] = True
            res0['pending'] = _unexpressed
        return res0

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

    # 注意：**不要**在这里把 backup 重置成 None —— 它是调用方传进来的"本次 apply
    # 已经备好份"的复用凭据（L0-4）。旧实现这行 `backup = None` 把入参悄悄丢掉，
    # 于是 auto 的第二次写盘又备了一份、把固定名备份覆盖成中间态：备份内容测试
    # （tests/test_writeback.py §52a）能当场抓到。
    attempts: List[Dict[str, Any]] = []
    # 候选锚点（按可能性排序，逐个试到"写回后能跑通"为止）：
    #   ① 参数记录的图号对应的 savefig —— 多图脚本的正确定位
    #   ② 主锚点（单图 / 最后一张时的常规位置）
    #   ③ 脚本尾（参数反映"跑完后的最终状态"，主锚点可能偏早）
    def _key(c):
        return (c[0], c[1].lineno if c[1] is not None else None)

    # T23：锚点节点提升到它所在的**语句**（决定插入位置与缩进）。
    # 行内复合语句 `if flag: fig.savefig('o.png')` 里 savefig 的 col_offset 是 10，
    # 过去直接拿它当缩进 → 块被缩进 10 格插到 `if` 行**前面** → IndentationError
    # （外部评审条目 3(c)）。按语句定位/缩进才是对的：块插在整条语句之前、同缩进。
    _parents = _parent_map(tree)

    def _promote(n):
        if n is None:
            return None
        st = _stmt_node(n, _parents)
        return st if st is not None else n

    cands = []
    if isinstance(fig_index, int):
        saves = find_savefigs(tree)
        if 0 <= fig_index < len(saves):
            cands.append(('before', _promote(saves[fig_index])))
    for c in (anchor, ('tail', None)):
        c = (c[0], _promote(c[1]))
        if _key(c) not in [_key(x) for x in cands]:
            cands.append(c)
    if anchor[1] is not None:
        anchor = (anchor[0], _promote(anchor[1]))
    # T23 / 外部评审条目 3(b)：块锚点**不得落在最后一个 savefig/show 之后**。
    # ②层语义比对跑的是"整脚本结束后内存里的图"，而用户看的是保存出来的 PNG ——
    # 块插在 savefig 之后时，语义比对照样通过、还报"已验证/语义一致"，但 out.png
    # 根本没变。所以：有 savefig/show 时，只有"插在最后一个 savefig/show 之前"的
    # 锚点才算合规；`('tail', None)`（脚本尾）在这种脚本里一律不合格。
    _last_out = max([n.lineno for n in find_savefigs(tree)], default=None)
    if _last_out is not None:
        cands = [c for c in cands
                 if c[1] is not None and c[1].lineno <= _last_out]
    if not cands:
        _why = ('本脚本有 savefig/plt.show，而所有可用锚点都落在**最后一个 savefig '
                '之后**：那样调整块写进去也不会反映到保存的图上'
                if _last_out is not None else '本脚本没有可用的插入锚点')
        res.update({'reason': FAIL, 'applied': False, 'verified': False,
                    'backup': backup, 'needs_block': True,
                    'pending': _unexpressed or None,
                    'err': '找不到合规锚点：%s，已拒绝写回（未改动脚本）。'
                           '可手动加一句 `fig.savefig(...)` 之前的定位调用，'
                           '或用 --style inplace 让可原位写的字段先落地。' % _why})
        return res

    chosen_block = block
    guard_note = ''
    if not dry_run:
        meta = _meta_path(params_path)
        _src_norm = _norm_ts(src)
        # 先算第一个候选的内容：与磁盘上的**完全一致**（只差说明行里的生成时间）
        # 就是"重复 apply 同一份参数"—— 这时连备份都不该写，直接 no_change。
        _blk0, _note0 = _guarded(block, cands[0])
        _content0 = insert_or_replace(new_src, _blk0, cands[0], meta, tag)
        if _norm_ts(_content0) == _src_norm:
            res.update({'reason': 'no_change', 'changes': [], 'block': _blk0,
                        'backup': None, 'warnings': warnings,
                        'verified': None, 'semantic': 'skipped',
                        'applied': False, 'needs_block': False,
                        'block_lines': len(_blk0.splitlines()),
                        'style': 'block', 'anchor': cands[0][0]})
            return _mark_unexpressed(res)
        try:
            # L0-4：`--style auto` 的第二次写盘（原位落空 → 退块）必须**复用**原位
            # 那次已经做好的备份，否则固定名备份被覆盖成"改过一次"的中间态，
            # revert 拿不回原件。
            # T41：pid 副本写不出来时不再静默（带路径与 errno 进 pre_warnings）。
            backup = backup or _make_backup(script, params_path,
                                            warnings=pre_warnings)
        except OSError as e:
            res['err'] = '备份失败: %s' % e
            return res
        chosen = None
        last_err = ''
        last_sem = ''
        baseline = None
        content = None
        # t27：`_expected` = **我们最后一次写进磁盘的内容**，也是外部改动守卫的比对基准。
        # 为什么不能拿入口读的 `src` 当基准：块循环每个候选失败后都可能换锚点重试，
        # 而上一轮**可能已经写过盘**。拿入口 `src` 去比，就会把"我们自己刚写进去的块"
        # 误判成"用户在编辑器里改过" ⇒ 报 external_modified 并把块留在磁盘上
        # （t23 全量真跑实测的 no-tamper 违约，三例都恰好 +231 字节 = 一个调整块）。
        _expected = src
        for cand in cands:
            blk, note = _guarded(block, cand)
            content = insert_or_replace(new_src, blk, cand, meta, tag)
            if _norm_ts(content) == _src_norm:
                # 候选换成别的锚点时也可能落成"与磁盘一致" → 同样 no_change
                # （此时备份已写，如实回传路径，revert 会看到"已一致"）。
                res.update({'reason': 'no_change', 'changes': [], 'block': blk,
                            'backup': backup, 'warnings': warnings,
                            'verified': None, 'semantic': 'skipped',
                            'applied': False, 'needs_block': False,
                            'block_lines': len(blk.splitlines()),
                            'style': 'block', 'anchor': cand[0]})
                return _mark_unexpressed(res)
            try:
                try:
                    ast.parse(content)
                except SyntaxError as _se:
                    # T42（Bug 4）：块生成后、**写盘前**先自检语法。过去是写盘后由
                    # 语法层拦下再回滚（数据安全，但白写一次盘、日志还像"工具出错"）。
                    # 现在直接把该候选判掉、换下一个锚点重试。
                    last_err = ('生成的调整块语法非法（未写盘，已换锚点重试）：%s'
                                % _se)
                    last_sem = last_err
                    continue
                _guard = _check_external_edit(script, _expected, backup=backup)
                if _guard:
                    # t27 修复②：命中守卫 = 磁盘与我们**最后一次写入**不一致（真外部改动）。
                    # 若我们先前写过盘，先把干净状态还回去（no-tamper：说没写就真没写）；
                    # 若磁盘已被用户改过，_rollback_note 的 expect 保护会**跳过**覆盖并
                    # 如实说明 —— 绝不抹掉用户保存的那一版（t14 建立的不变量）。
                    if _expected != src:
                        _guard['err'] = '%s（%s）' % (
                            _guard.get('err'),
                            _rollback_note(backup, script, expect=_expected))
                    res.update(_guard)
                    return res
                _atomic_write(script, content, encoding=_src_enc)
                # t27：写进去了 → 基准跟着走。下一轮候选的守卫要比的是**我们现在
                # 写进去的这份**，而不是入口那份 `src`（否则同一轮内的自身写入会被
                # 误判成外部改动）。写盘失败走 except，基准不动（磁盘没变）。
                _expected = content
            except OSError as e:
                res['err'] = '写文件失败: %s（%s）' % (
                    e, _rollback_note(backup, script))
                return res
            # T23 / 外部评审条目 3(c)：写盘后**无条件** ast.parse 一次（"一次解析的钱"）。
            # 语法坏了的写回绝不留盘 —— 完整验证档过去靠"重跑"发现语法错，可脚本本来就
            # 跑不通（缺数据/依赖）时会被 baseline 分支判成"不是我们的锅"而保留坏写回
            # （评审现场：`if flag: fig.savefig(...)` + 缩进取锚点 col_offset → 写回后的
            # 文件 `ast.parse` 直接报 unexpected indent，而结论仍是 ok）。
            try:
                ast.parse(content)
            except SyntaxError as e:
                res.update({'reason': FAIL, 'attempts': attempts,
                            'applied': False, 'verified': False,
                            'layers': {'syntax': False, 'rerun': None,
                                       'semantic': None},
                            'err': '写回后源码语法错误（%s）：%s'
                                   % (_rollback_note(backup, script,
                                                     expect=content), e)})
                return res
            if not verify:
                chosen = cand
                chosen_block = blk
                guard_note = note
                res['layers'] = dict(res.get('layers') or {}, syntax=True,
                                     rerun=None, semantic=None)
                break
            try:
                vok, verr = verify_run(script, python, timeout,
                                       script_args=script_args)
            except BaseException as e:                     # noqa: BLE001
                # **回滚兜底，必须宽**（棘轮登记的 9 处宽捕获之一）：E3 的现场 ——
                # 脚本**已经写盘**了 → 这一步之后任何异常都必须回滚，绝不能把
                # 半成品留在用户磁盘上（写盘后这一段的异常过去会穿过所有 except，
                # 只留一句 internal_error，而脚本里已经多出一整块调整块）。
                res.update({'reason': FAIL, 'attempts': attempts,
                            'verified': False, 'applied': False,
                            'err': '写回后验证异常（%s: %s）：%s'
                                   % (type(e).__name__, e,
                                      _rollback_note(backup, script,
                                                     expect=content))})
                return res
            attempts.append({'anchor': cand[0],
                             'line': cand[1].lineno if cand[1] is not None else None,
                             'ok': vok})
            res.setdefault('layers', {'syntax': True})['rerun'] = vok
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
                    try:
                        # T70：语义层只对**真正写进去的**目标负责（证不出/块表达
                        # 不了的字段已进 pending，见 _prune_refused 的说明）。
                        sok, sdetail = _verify.verify_semantic(
                            script, _prune_refused(data, _unexpressed),
                            python, timeout,
                            figsize_edited=bool(figsize_in_block
                                                or data.get('figsize_in')),
                            script_args=script_args)
                    except BaseException as e:                 # noqa: BLE001
                        # **回滚兜底，必须宽**（棘轮登记的 9 处宽捕获之一）：E3 的
                        # 现场 —— 参数里 xlim 带 null 时 verify.compare 里
                        # `float(None)` 抛 TypeError → 块已写进脚本、异常却穿到顶层
                        # 兜底（rc=1 + internal_error，无回滚）。写盘之后**任何**
                        # 异常都必须走 _rollback_note。
                        res.update({'reason': FAIL, 'attempts': attempts,
                                    'verified': False, 'applied': False,
                                    'err': '写回后语义验证异常（%s: %s）：%s'
                                           % (type(e).__name__, e,
                                              _rollback_note(
                                                  backup, script,
                                                  expect=content))})
                        return res
                    attempts[-1]['semantic'] = sok
                    res['semantic'] = sok
                    res.setdefault('layers', {'syntax': True})['semantic'] = sok
                    if sok is False:
                        last_err = '语义验证不一致：%s' % sdetail
                        last_sem = sdetail
                        warnings.append('锚点 %s 能跑通但布局没落到目标图（%s），换锚点重试'
                                        % (cand[0], sdetail))
                        continue
                    if sok is None:
                        # T23 / 3(a)：②层没跑成 → verified 必须降成 None（不许报 True）
                        res['verified'] = None
                        warnings.append('语义验证未取到目标图状态（%s），'
                                        '本次只确认了"脚本能跑通"，**没有**验证布局是否'
                                        '落到目标图上' % sdetail)
                chosen = cand
                chosen_block = blk
                guard_note = note
                # T23 / 3(a)：只有"②层真的跑过且通过"（或本来没要求②层）才报
                # verified=True；②层没跑成（semantic is None）→ None，别谎报全绿。
                # T42（Bug 7）：与 T23 同一条契约 —— 语义层**没跑**（semantic 为
                # None/'skipped'）时不得报 verified=True。这里再兜一道：只要本次要求了
                # 语义层而它没给出 True，verified 就是 None。
                res['verified'] = True if ((not semantic)
                                           or res.get('semantic') is True) else None
                if res.get('verified') is None and not semantic:
                    res['verified'] = True
                if semantic and res.get('semantic') is not True:
                    res['verified'] = None
                break
            last_err = verr
            # 验证失败：先判别是"我们的块弄坏了"还是"脚本本来就无头跑不通"。
            # 跑原脚本（备份即原文）——它要也跑不通，就不该怪写回。
            if baseline is None:
                baseline, _ = verify_run(backup, python, timeout,
                                         script_args=script_args)
                res['baseline_ok'] = baseline
                if not baseline:
                    # T23 / 外部评审条目 3(c)：脚本本来就在无头环境跑不通（缺数据/依赖）
                    # 时，验证**证明不了**这次写回是好的 —— 旧实现照旧把写回留在磁盘上
                    # 并报 ok（评审的现场：块插进行内复合语句写坏了源码，而原脚本也跑不通
                    # → 坏写回被静默留下）。默认改为**回滚 + 明确失败**；
                    # 确实想保留这次写回的人显式加 --keep-unverified。
                    if keep_unverified:
                        warnings.append(
                            '脚本本身在无头环境跑不通（缺数据/依赖，或需要 GUI/交互），'
                            '无法用 Agg 验证写回结果；按 --keep-unverified 保留本次写回，'
                            '请自行运行确认')
                        chosen = cand
                        chosen_block = blk
                        guard_note = note
                        res['verified'] = None
                        break
                    res.update({
                        'reason': FAIL, 'attempts': attempts, 'applied': False,
                        'verified': False,
                        'err': ('脚本本身在无头环境跑不通（缺数据/依赖，或需要 GUI/交互），'
                                '无法用 Agg 验证这次写回是否可用 → 默认**不保留**：%s。'
                                '确实要保留请显式加 --keep-unverified，'
                                '或先修好脚本（让它能在无头环境跑通）再来写回。'
                                % _rollback_note(backup, script, expect=content))})
                    return res
            # t27 修复③：这一轮验证失败、要换锚点重试 → **先把批前状态还回去**（与
            # inplace 路径对齐）。否则上一候选的块会留在磁盘上，成为下一轮的污染基准
            # —— 那正是 t23 全量真跑 3 例 no-tamper 违约的现场（守卫把自身写入误判成
            # 外部改动，块却留在盘上）。回滚成功才把基准重置回 `src`；回滚失败则保持
            # `_expected`（磁盘上仍是我们写的那份），让下一轮守卫判定保持正确。
            _rnote = _rollback_note(backup, script, expect=_expected)
            if _rnote == '已回滚':
                _expected = src
            else:
                warnings.append('换锚点重试前回滚未完成：%s' % _rnote)
        if chosen is None:
            # 全部候选都跑不通 → 回滚；回滚是否真的成功要如实写进结论。
            # 传 expect：若用户在我们验证期间改过这个文件，就别拿备份整体覆盖。
            if last_err.startswith('语义验证不一致'):
                # T18：语义回比失败 ≠ "重跑失败"。真实原因是"脚本能跑通，但目标值没达标"，
                # 旧文案（所有锚点写回后 Agg 重跑均失败）会把排查方向带偏。
                _head = '目标值在重跑后仍未达标：%s（脚本本身能跑通）' % (
                    last_sem.strip() or '（逐字段回比不一致）')
                if _last_out is not None:
                    # T23/3(b)：这种"插在 savefig 之前也白改"的场景，多半是脚本在 savefig
                    # **之后**还有会覆盖布局的调用（subplots_adjust / tight_layout / 布局引擎）。
                    # 说明清楚：我们**不会**把块插到 savefig 之后（那样存出来的图不反映改动）。
                    _head += ('。提示：脚本在第 %d 行（savefig/plt.show）之后可能还有会覆盖'
                              '布局的调用；把那些调用移到 savefig 之前，或用 --style inplace。'
                              % _last_out)
            else:
                _head = '所有锚点写回后 Agg 重跑均失败（原脚本单独可跑通）'
            res.update({'reason': FAIL, 'attempts': attempts, 'verified': False,
                        'applied': False,
                        'err': ('%s。%s。尝试：%s\n'
                                'stderr 尾部：\n%s'
                                % (_head,
                                   _rollback_note(backup, script,
                                                  expect=_expected),
                                   attempts, last_err.strip()))})
            return res
        anchor = chosen
        res['attempts'] = attempts
    else:
        # dry-run：按第一个候选锚点预览（含守卫）
        chosen_block, guard_note = _guarded(block, cands[0])
    if guard_note:
        warnings.append(guard_note)
    if anchor[0] == 'tail':
        # T23 / 外部评审条目 3(b)：锚点落到"脚本尾"必须在结论里说出来。有 savefig/show
        # 的脚本已经在上面的合规过滤里排除了 tail，所以走到这里只可能是"脚本不存图"——
        # 但仍然要让人一眼看出"块是在脚本最后面、不是在保存之前"。
        warnings.append('本次锚点只能选"脚本尾"（脚本里没有 savefig/plt.show 可锚）'
                        '——调整块会加在文件末尾')

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
                'applied': True, 'needs_block': False,
                # RC-9：agent 要能看出"这次写了什么块"（过去块模式的 changes 是空的，
                # JSON 里完全看不出改了什么）。block_lines = 块的行数。
                'block_lines': len((chosen_block or '').splitlines()),
                'anchor_line': anchor[1].lineno if anchor[1] is not None else None})
    return _mark_unexpressed(res)
