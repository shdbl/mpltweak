# -*- coding: utf-8 -*-
"""
mpltweak.verify —— 语义验证：写回后重跑脚本，dump **目标图的真实状态**并与参数逐项比对
=======================================================================================

"能跑通"（exit 0）只证明写回没把脚本改坏，**不能证明布局真的落在了你要调的那张图上**。
本模块补上这一环：在一个干净子进程里重跑写回后的脚本 → 抓取目标图（按
``fig_index``，与 launch 选图口径一致）→ 把位置/字号/grid/spines/clim 与参数 JSON
逐项比对。

判定：
  * 全部一致            → ``(True, '')``
  * 有不一致项          → ``(False, '明细')`` —— 调用方据此换锚点重试
  * 取不到图状态        → ``(None, '原因')`` —— **不否决**（脚本可能存完图就 close，
                          或需要数据/依赖跑不起来；此时退回"仅按能跑通判定"）

取数口径与 ``toolbox.export()`` 完全一致（都用 toolbox 的 ``_bbox``/``_title_fs``/
``_clim_of``），保证比对的是同一套定义。

子进程入口：``python -m mpltweak.verify <script> <out.json> [fig_index] [-- <脚本参数...>]``
（``--`` 之后的内容原样透传给用户脚本的 ``sys.argv``，与 ``apply --write -- <参数>`` 同一条链。）
"""

from __future__ import annotations

import ast
import contextlib
import json
import os
import runpy
import subprocess
import sys
import tempfile
import threading
from typing import Any, Dict, List, Optional, Tuple

from . import messages as _msg

POS_TOL = 2e-3        # 位置容差（figure 比例，参数存 4 位小数）
FS_TOL = 0.51         # 字号容差（磅）
CLIM_REL_TOL = 0.01   # clim 容差（相对量程）
TEXT_BOX_LIMIT = 600  # 文字盒条数上限（超多刻度的图别把状态 JSON 撑爆）

# ---------------------------------------------------------------------------
# 失败归因（0.1.10）：把"脚本跑不起来"和"工具改坏了"彻底分开报。
#
# apply 写回后的重跑验证失败时，用户必须能一眼看出**是谁的错、下一步怎么办**。
# 四类（机器可读取值 = 这里定义的常量，写进 --json 的 `attribution` 字段）：
#   script_cannot_run                       脚本自身跑不通（缺依赖 / 缺数据…）
#   script_needs_args                       脚本需要命令行参数（argv 未透传）
#   script_needs_gui_or_headless_impossible 无头环境跑不通（要开窗口 / 交互）
#   tool_bug                                internal_error（工具自身异常）
#
# 分类要**保守**：判不出时回退 script_cannot_run 并在文案里说明"无法确定原因"，
# 绝不误报成 tool_bug（吓到用户）或 script_needs_args（把用户带偏去传参数）。
# 判据 = 子进程 stderr 模式匹配 + 脚本静态扫描（是否读 sys.argv / argparse
# required / 交互标记）综合；有 stderr 时以 stderr 为准，静态只补无 stderr 的空档。
# ---------------------------------------------------------------------------
VERIFY_FAIL_CANNOT_RUN = 'script_cannot_run'
VERIFY_FAIL_NEEDS_ARGS = 'script_needs_args'
VERIFY_FAIL_NEEDS_GUI = 'script_needs_gui_or_headless_impossible'
VERIFY_FAIL_TOOL_BUG = 'tool_bug'

# stderr 里的**强证据**（小写匹配）。needs_args 必须是很具体的 argparse 报错
# 或带 usage 的用法错误 —— 泛化的 "usage:" 单凭一行不归类（有些脚本自己打印用法）。
_ARGS_ERR_PATTERNS = (
    'the following arguments are required',
    'unrecognized arguments',
    'expected one argument',
    'too few arguments',
    'invalid choice',
    'no such option',
    'argumenterror',
)
# GUI / 无头环境类报错：Tcl 的 "no display"、Qt 插件加载失败等。
_GUI_ERR_PATTERNS = (
    'no display',
    'cannot connect to display',
    'cannot open display',
    'no $display',
    'qt.qpa',
    'qt platform plugin',
    'wayland',
    'xcb',
    'tclerror',
    'qapplication',
    'no available display',
)
# 缺依赖 / 缺数据（归 script_cannot_run，但要能点出具体是什么）。
_MISSING_DEP_PATTERNS = (
    'modulenotfounderror',
    'importerror',
    'no module named',
    'cannot import name',
)
_MISSING_DATA_PATTERNS = (
    'filenotfounderror',
    'no such file or directory',
    'is not a directory',
)


def _script_static_evidence(script_path: str) -> Dict[str, bool]:
    """纯静态扫描（ast，**不执行脚本**）：脚本是否读 argv / 是否带交互（GUI）标记。

    返回 ``{'uses_args': bool, 'uses_args_strong': bool, 'needs_gui': bool}``。
      * ``uses_args``：任何读 ``sys.argv`` / 用 argparse 的迹象；
      * ``uses_args_strong``：**明确**需要参数（``required=True`` 或直接
        ``sys.argv[N]``（N>=1）取值）—— 只有强证据才够在"无 stderr"时归
        needs_args（保守：可选读 argv 的脚本不该被误归"缺参数"）；
      * ``needs_gui``：import tkinter/PyQt*/PySide*、plt.ion/pause/waitforbuttonpress、
        ``input()``/``getpass``、matplotlib.use('Qt/Tk/WebAgg')。
    读不了 / 解析不了 → 全 False（没证据就不归类，保守）。
    """
    out = {'uses_args': False, 'uses_args_strong': False, 'needs_gui': False}
    src = None
    for enc in ('utf-8', 'gbk', 'gb18030'):
        try:
            with open(script_path, 'r', encoding=enc) as f:
                src = f.read()
            break
        except (OSError, UnicodeDecodeError):
            continue
    if src is None:
        return out
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return out

    imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update((a.name or '').lower().split('.')[0]
                           for a in node.names if a.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module.lower().split('.')[0])

    def _is_sys_argv(node):
        return (isinstance(node, ast.Attribute) and node.attr == 'argv'
                and isinstance(node.value, ast.Name) and node.value.id == 'sys')

    for node in ast.walk(tree):
        # 交互（GUI）标记
        if isinstance(node, ast.Call):
            fn = node.func
            if (isinstance(fn, ast.Attribute) and getattr(fn, 'attr', '') == 'use'
                    and isinstance(fn.value, ast.Name) and fn.value.id == 'matplotlib'):
                if node.args and isinstance(node.args[0], ast.Constant):
                    _arg = str(node.args[0].value).lower()
                    if any(_k in _arg for _k in ('qt', 'tk', 'webagg')):
                        out['needs_gui'] = True
            if (isinstance(fn, ast.Attribute) and getattr(fn, 'attr', '')
                    in ('ion', 'pause', 'interactive', 'waitforbuttonpress',
                        'get_current_fig_manager')):
                out['needs_gui'] = True
            if isinstance(fn, ast.Name) and fn.id == 'input':
                out['needs_gui'] = True
            if isinstance(fn, ast.Attribute) and getattr(fn, 'attr', '') == 'getpass':
                out['needs_gui'] = True
            if (isinstance(fn, ast.Attribute)
                    and getattr(fn, 'attr', '') == 'ArgumentParser'):
                out['uses_args'] = True
            if (isinstance(fn, ast.Attribute)
                    and getattr(fn, 'attr', '') in ('parse_args', 'add_argument')):
                out['uses_args'] = True
                if getattr(fn, 'attr', '') == 'add_argument':
                    for kw in node.keywords:
                        if (kw.arg == 'required' and isinstance(kw.value, ast.Constant)
                                and kw.value.value is True):
                            out['uses_args_strong'] = True
            # len(sys.argv) 比较
            if (isinstance(fn, ast.Name) and fn.id == 'len' and node.args
                    and _is_sys_argv(node.args[0])):
                out['uses_args'] = True
        # sys.argv[N] 或 sys.argv[N:] —— 直接取值是"无条件需要参数"的强证据
        if isinstance(node, ast.Subscript) and _is_sys_argv(node.value):
            _sl = node.slice
            if isinstance(_sl, ast.Constant) and isinstance(_sl.value, int) \
                    and _sl.value >= 1:
                out['uses_args'] = True
                out['uses_args_strong'] = True
            else:
                out['uses_args'] = True
    if ('tkinter' in imports or any(_m in imports
                                    for _m in ('pyqt5', 'pyqt6', 'pyside2',
                                               'pyside6'))):
        out['needs_gui'] = True
    return out


def classify_verify_failure(err_text: str, script_path: Optional[str] = None,
                            args_passed: bool = False
                            ) -> Tuple[str, str]:
    """把验证重跑的失败归因到四类之一。返回 ``(kind, detail)``。

    ``err_text``：子进程 stderr 尾部（有则模式匹配优先）；没有时（如"原脚本
    本来也跑不通"的路径只给了笼统文案）用 ``script_path`` 的静态扫描补位。
    ``args_passed``：本次是否透传了脚本参数（``apply --write -- <args>``）。

    ``detail``：稳定英文短原因，写进 --json 的 ``attribution_detail``：
    'unknown' / 'argparse' / 'missing_dependency' / 'missing_data' / 'display' /
    'static_args' / 'static_gui' / 'configured_args'。

    ``args_passed=True``（``apply --write -- <参数>`` 已透传）时，**静态**"读
    argv"证据不再归 ``script_needs_args`` —— 用户已经把参数给了，再叫他"去传
    参数"是误导（脚本跑不通另有原因）。真拿到 argparse 报错时仍归
    ``script_needs_args``，但 detail 改成 ``configured_args`` 并换一句文案
    （见 apply.py），不重复叫他传参数。有 stderr 时一律以 stderr 为准。
    """
    err = (err_text or '').strip()
    low = err.lower()
    if low:
        if any(_p in low for _p in _ARGS_ERR_PATTERNS):
            return (VERIFY_FAIL_NEEDS_ARGS,
                    'configured_args' if args_passed else 'argparse')
        if any(_p in low for _p in _GUI_ERR_PATTERNS):
            return VERIFY_FAIL_NEEDS_GUI, 'display'
        if any(_p in low for _p in _MISSING_DEP_PATTERNS):
            return VERIFY_FAIL_CANNOT_RUN, 'missing_dependency'
        if any(_p in low for _p in _MISSING_DATA_PATTERNS):
            return VERIFY_FAIL_CANNOT_RUN, 'missing_data'
        # 有 stderr 但没对上任何已知模式：不靠静态猜（写回后失败而原脚本可跑通
        # 时，脚本静态读 argv 也说明不了问题）→ 保守归 cannot_run。
        return VERIFY_FAIL_CANNOT_RUN, 'unknown'
    # 无 stderr（原脚本本身跑不通 → writeback 只给笼统文案）：静态证据补位。
    if script_path:
        ev = _script_static_evidence(script_path)
        if ev['uses_args_strong'] and not args_passed:
            return VERIFY_FAIL_NEEDS_ARGS, 'static_args'
        if ev['needs_gui']:
            return VERIFY_FAIL_NEEDS_GUI, 'static_gui'
    return VERIFY_FAIL_CANNOT_RUN, 'unknown'


def _ax_identity(ax) -> Optional[Dict[str, Any]]:
    """轴的**稳定身份**：网格位置（subplotspec）优先，其次标题/标签文字。

    ``fig.axes`` 的下标是**运行期顺序**，脚本一变（加个 colorbar、条件分支少建一个轴）
    就会变 —— 调整块若按下标寻址就会静默错位。这里采一份与顺序无关的身份：
      * ``grid``：[行数, 列数, 起始行, 起始列]（网格轴非常稳定）；
      * ``title`` / ``xlabel`` / ``ylabel``：文字身份（非空时才记）。
    取不到就返回 None（写成块时退回下标，行为与旧版一致）。

    整段包一个兜底：这是**尽力而为**的附加信息，任何取不到都不该影响主流程
    （主流程的轴状态在上面已经采完了）。
    """
    try:
        out: Dict[str, Any] = {}
        sp = ax.get_subplotspec()
        # grid 显式给 None = "这个轴本来就不是网格轴"（手工 add_axes）；
        # 整函数返回 None = "**取不到**"（异常兜底）。两者必须能区分开：
        # compare 只在"期望是网格轴、实测明确不是网格轴"时判失败（独立审阅 C1）。
        out['grid'] = None
        if sp is not None:
            nrow, ncol = sp.get_geometry()[0], sp.get_geometry()[1]
            out['grid'] = [int(nrow), int(ncol),
                           int(sp.rowspan.start), int(sp.colspan.start)]
        for key, getter in (('title', ax.get_title),
                            ('xlabel', ax.get_xlabel),
                            ('ylabel', ax.get_ylabel)):
            v = (getter() or '').strip()
            if v:
                out[key] = v[:80]
        return out if (out.get('grid') is not None or len(out) > 1) else out
    except Exception:                             # noqa: BLE001 尽力采集，见 docstring
        return None


def _collect_texts(fig, axes_items) -> Tuple[List[Dict], str]:
    """draw 之后采集文字盒（figure 归一化坐标），写进 ``axes_items[i]['_texts']``。

    **为什么必须 draw**：``Text.get_window_extent()`` 需要真实 renderer，刻度标签的
    尺寸要等排版算完才知道。这也是 check 能看出"刻度标签太长互相压/超出画布"的唯一
    途径（纯几何框查不到）。代价是一次绘制——所以只在 ``check`` 里按需开启，
    ``describe`` / 写回验证都不付这个成本。
    """
    out: List[Dict] = []
    try:
        fig.canvas.draw()
        renderer = fig.canvas.get_renderer()
        inv = fig.transFigure.inverted()
    except Exception as e:                        # noqa: BLE001
        return [], '%s: %s' % (type(e).__name__, e)

    def box_of(t):
        bb = t.get_window_extent(renderer=renderer)
        (x0, y0) = inv.transform((bb.x0, bb.y0))
        (x1, y1) = inv.transform((bb.x1, bb.y1))
        return [round(min(x0, x1), 4), round(min(y0, y1), 4),
                round(max(x0, x1), 4), round(max(y0, y1), 4)]

    def usable(t):
        return bool(t.get_visible()) and bool((t.get_text() or '').strip())

    n = 0
    for i, ax in enumerate(fig.axes):
        if i >= len(axes_items):
            break
        boxes = []
        try:
            cand = [(ax.title, 'title'), (ax.xaxis.label, 'xlabel'),
                    (ax.yaxis.label, 'ylabel')]
            # 刻度标签必须**逐个问 Tick 对象**，不能用 ax.get_xticklabels()：
            # 那个列表里包含落在**视图外**的刻度（实测 ylim=(0.85, 4.15) 时仍给出
            # 位置为 5 的刻度），而视图外的刻度 matplotlib 根本不画 —— 直接拿它的
            # 外框就会报出"越界 1.010"这种假阳性（真实探针抓到过）。
            for axis, kind in ((ax.xaxis, 'xtick'), (ax.yaxis, 'ytick')):
                lo, hi = axis.get_view_interval()
                for tick in axis.get_major_ticks():
                    t = getattr(tick, 'label1', None)
                    if t is None:
                        continue
                    loc = float(tick.get_loc())
                    if not (min(lo, hi) - 1e-9 <= loc <= max(lo, hi) + 1e-9):
                        continue                  # 视图外的刻度不画，别算它
                    cand.append((t, kind))
            for t in getattr(ax, 'texts', []):
                cand.append((t, 'text'))
            for t, kind in cand:
                if not usable(t) or n >= TEXT_BOX_LIMIT:
                    continue
                boxes.append({'kind': kind, 's': t.get_text()[:60],
                              'box': box_of(t)})
                n += 1
        except Exception:                         # noqa: BLE001 尽力采集：某个轴取不到
            boxes = []                            # 外框不该让整个 check 失败
        axes_items[i]['_texts'] = boxes
    try:
        for t in getattr(fig, 'texts', []):
            if not usable(t) or n >= TEXT_BOX_LIMIT:
                continue
            out.append({'kind': 'figtext', 's': t.get_text()[:60],
                        'box': box_of(t)})
            n += 1
    except Exception:                             # noqa: BLE001 同上（尽力采集）
        pass
    if n >= TEXT_BOX_LIMIT:
        out.append({'kind': 'capped', 's': '文字盒超过 %d 条，已截断' % TEXT_BOX_LIMIT,
                    'box': [0, 0, 0, 0]})
    return out, ''


# ---------------------------------------------------------------------------
# T26：图元级状态（cmap / legend / lines）
#
# 为什么要在**这里**采：describe 的产物是 `_state()` 的白名单搬运 —— 采集侧不给，
# describe 就永远拿不到，只能硬编码一个假的 `lines: []`（0.1.8 就是这样）。
#
# 口径与 GUI（``toolbox._snapshot``）对齐，字段名/取值风格一致，免得同一个参数
# 文件在"开窗存的"与"describe 导出的"之间出现两套格式：
#   * ``_cmap``   —— 该轴上有 mappable 时给 colormap 名（imshow / pcolormesh /
#                    contourf 分别注册在 ax.images / ax.collections，统一扫一遍）；
#   * ``_lines``  —— ``ax.get_lines()`` 每根线一项 {index, linewidth, color}，
#                    color 归一成 '#rrggbb'（与 toolbox 的导出格式一致）；
#   * ``_legend`` —— **从图例对象自己身上取**（``get_texts()`` / ``legend_handles``）。
#                    不用 ``ax.get_legend_handles_labels()``：那个会把"图里所有带
#                    label 的图元"都算进来，与用户看到的图例内容不是一回事
#                    （GUI 侧刚抓到同款坑：拖动后把手刻意排除的图元又冒出来了）。
#
# 三条纪律：
#   1. **不额外 draw** —— 全部是反射式读取，沿用现有采集时机；
#   2. **取不到就不写键** —— 绝不写 None / 空壳（消费者要能区分"没有"与"没采到"）；
#   3. **绝不参与比对** —— ``compare()`` 不读这三个字段（把 cmap/lines/legend 纳入
#      语义比对是下一批的事，本轮只采集与输出）。
# ---------------------------------------------------------------------------
_LEGEND_LOC_NAMES = ('best', 'upper right', 'upper left', 'lower left',
                     'lower right', 'right', 'center left', 'center right',
                     'lower center', 'upper center', 'center')


def _line_width(line) -> Optional[float]:
    """线的粗细（取不到给 None）。"""
    try:
        v = line.get_linewidth()
    except (AttributeError, ValueError, TypeError):
        return None
    if isinstance(v, (tuple, list)):
        v = v[0] if v else None
    try:
        return round(float(v), 4)
    except (TypeError, ValueError):
        return None


def _line_color(line) -> Optional[str]:
    """线的颜色 → ``'#rrggbb'``（取不到给 None）。

    统一成十六进制字符串：JSON 里可以直接比较，也能原样喂给 ``set_color('#rrggbb')``；
    numpy 数组/元组直接 json.dump 会失败（与 toolbox._line_color 同一理由）。
    """
    import matplotlib.colors as mcolors
    for getter in ('get_color', 'get_edgecolor'):
        fn = getattr(line, getter, None)
        if fn is None:
            continue
        try:
            v = fn()
        except (AttributeError, ValueError, TypeError):
            continue
        if isinstance(v, str):
            if not mcolors.is_color_like(v):
                continue
            try:
                return mcolors.to_hex(v, keep_alpha=False)
            except (ValueError, TypeError):
                continue
        if isinstance(v, (tuple, list)) and len(v) in (3, 4):
            try:
                return mcolors.to_hex([float(x) for x in v[:3]],
                                      keep_alpha=False)
            except (ValueError, TypeError):
                continue
        # numpy 数组（imshow 之类给的 RGBA 数组）：显式转 list 再试一次
        try:
            vals = [float(x) for x in v[:3]]
        except (TypeError, ValueError, IndexError):
            continue
        try:
            return mcolors.to_hex(vals, keep_alpha=False)
        except (ValueError, TypeError):
            return None
    return None


def _lines_state(ax) -> Optional[List[Dict[str, Any]]]:
    """``ax.get_lines()`` → ``[{index, linewidth, color}, ...]``（取不到给 None）。"""
    try:
        lines = list(ax.get_lines())
    except (AttributeError, ValueError, TypeError):
        return None
    out = []
    for j, line in enumerate(lines):
        item = {'index': j, 'linewidth': _line_width(line)}
        color = _line_color(line)
        if color:
            item['color'] = color
        out.append(item)
    return out


def _legend_state(ax) -> Optional[Dict[str, Any]]:
    """图例的**实际内容**（取不到给 None）。

    给的是"用户在图上看到的那些条目"：``labels``（文字）+ ``n_handles``（句柄数）
    + 能取到就带 ``fontsize`` / ``loc``。

    ``loc`` 只在能解析成 matplotlib 标准位名时才写；**不写 ``anchor``** ——
    GUI 侧的 ``anchor`` 是"用户拖到哪"的会话状态，verify 这里根本无从得知，
    硬填 bbox_to_anchor 会把它变成另一个含义（宁缺勿假）。
    """
    get_legend = getattr(ax, 'get_legend', None)
    if get_legend is None:
        return None
    try:
        leg = get_legend()
    except (AttributeError, ValueError, TypeError):
        return None
    if leg is None:
        return None
    out: Dict[str, Any] = {}
    labels: List[str] = []
    try:
        labels = [t.get_text() for t in leg.get_texts()]
    except (AttributeError, ValueError, TypeError):
        labels = []
    out['labels'] = labels
    handles = getattr(leg, 'legend_handles', None)
    if handles is None:                       # matplotlib < 3.7 的旧名
        handles = getattr(leg, 'legendHandles', None)
    try:
        out['n_handles'] = len(handles) if handles is not None else len(labels)
    except TypeError:
        out['n_handles'] = len(labels)
    if labels:
        try:
            fs = labels and leg.get_texts()[0].get_fontsize()
            if fs is not None:
                out['fontsize'] = round(float(fs), 4)
        except (AttributeError, ValueError, TypeError, IndexError):
            pass
    loc = None
    for getter in ('get_loc', '_get_loc'):    # 公开名优先，私名兜底
        fn = getattr(leg, getter, None)
        if fn is None:
            continue
        try:
            loc = fn()
        except (AttributeError, ValueError, TypeError):
            loc = None
        if loc is not None:
            break
    if isinstance(loc, int) and 0 <= loc < len(_LEGEND_LOC_NAMES):
        out['loc'] = _LEGEND_LOC_NAMES[loc]
    elif isinstance(loc, str):
        out['loc'] = loc
    return out


def _artist_state(ax) -> Dict[str, Any]:
    """图元级状态（cmap / legend / lines）：取不到的项**不写键**（见本节开头）。"""
    out: Dict[str, Any] = {}
    # --- cmap：imshow / pcolormesh → ax.images；contourf → ax.collections；
    #     colorbar 槽轴的图元也在同样的两个容器里 ---
    for pool in (getattr(ax, 'images', None), getattr(ax, 'collections', None)):
        if not pool:
            continue
        for artist in pool:
            get_cmap = getattr(artist, 'get_cmap', None)
            if get_cmap is None:
                continue
            try:
                name = getattr(get_cmap(), 'name', None)
            except (AttributeError, ValueError, TypeError):
                continue
            if name:
                out['_cmap'] = name
                break
        if '_cmap' in out:
            break
    # --- lines ---
    lines = _lines_state(ax)
    if lines is not None:
        out['_lines'] = lines
    # --- legend ---
    legend = _legend_state(ax)
    if legend is not None:
        out['_legend'] = legend
    return out


def _state(fig, with_text: bool = False) -> Dict[str, Any]:
    """抽取图形的可比对状态（口径同 toolbox.export）。"""
    from .toolbox import _bbox, _clim_of, _title_fs
    axes = []
    for i, ax in enumerate(fig.axes):
        x0, y0, w, h = _bbox(ax)
        item = {
            'index': i,
            'pos': [round(x0, 4), round(y0, 4), round(w, 4), round(h, 4)],
            'title_fontsize': _title_fs(ax),
            'label_fontsize': ax.xaxis.label.get_fontsize(),
            'label_fontsize_y': ax.yaxis.label.get_fontsize(),
            'grid': any(line.get_visible() for line in
                        list(ax.get_xgridlines()) + list(ax.get_ygridlines())),
            'spines': {k: bool(v.get_visible()) for k, v in ax.spines.items()},
        }
        tls = ax.get_xticklabels()
        if tls:
            item['tick_fontsize'] = tls[0].get_fontsize()
        # T75b：y 刻度字号**独立采集**（与 label_fontsize_y 同构）。
        # T73 之后"参数没写 y 就一个字节都不动 y"，比对侧要能真的判 y 侧；
        # 不采的话 `tick_fontsize_y` 只有一个期望值、没有实际值 —— 那正是
        # "没比却说通过"的来源。
        yls = ax.get_yticklabels()
        if yls:
            item['tick_fontsize_y'] = yls[0].get_fontsize()
        try:
            from .toolbox import _is_colorbar_ax
            item['is_colorbar'] = bool(_is_colorbar_ax(ax))
        except Exception:                         # noqa: BLE001
            item['is_colorbar'] = False
        try:
            item['aspect'] = ax.get_aspect()      # 'auto' / 'equal' / 数值
        except Exception:                         # noqa: BLE001
            item['aspect'] = None
        clim = _clim_of(ax)
        if clim is not None:
            item['clim'] = [round(float(clim[0]), 6), round(float(clim[1]), 6)]
        # 轴范围：**只在脚本显式固定过时才采**。判据是 matplotlib 自己的开关 ——
        # set_xlim/set_ylim 会关掉该轴的 autoscale（get_autoscalex_on() → False）。
        # 全采的话：每张 autoscale 的图都会带一组"当前自动范围"，而 apply 在源码里
        # 找不到对应写法 → 每个轴都进"未原位应用"清单，噪音淹没真问题；而且自动范围
        # 随数据变，写回去没有意义（这是参数语义问题，不只是省事）。
        try:
            if not ax.get_autoscalex_on():
                _lo, _hi = ax.get_xlim()
                item['xlim'] = [round(float(_lo), 6), round(float(_hi), 6)]
            if not ax.get_autoscaley_on():
                _lo, _hi = ax.get_ylim()
                item['ylim'] = [round(float(_lo), 6), round(float(_hi), 6)]
        except (AttributeError, ValueError, TypeError):
            pass                                  # 取不到就不记（可选字段）
        # 坐标轴尺度：不采集的话 describe 只能靠默认值，会把 log 轴**谎报**成
        # linear（t3-M4 / t6-M4），agent 拿到的"图状态"就是错的。
        try:
            item['xscale'] = ax.get_xscale()
        except Exception:                         # noqa: BLE001
            item['xscale'] = 'linear'
        try:
            item['yscale'] = ax.get_yscale()
        except Exception:                         # noqa: BLE001
            item['yscale'] = 'linear'
        # 稳定身份：``cell`` 是**正式字段**（进参数文件，写回后可回比）；
        # ``_identity`` 是内部合并视图（给以后按身份寻址用，不进参数）。
        ident = _ax_identity(ax)
        if ident is not None:
            item['_identity'] = ident
            # 显式写 None：compare 需要区分"这个轴不是网格轴"与"没采到"
            _g = ident.get('grid')
            item['cell'] = list(_g) if _g else None
        # T26：图元级状态（cmap / legend / lines）。取不到的项**不写键** ——
        # describe 过去硬编码 `lines: []`，图里明明有线却报"没有线"，那是假字段。
        item.update(_artist_state(ax))
        axes.append(item)
    try:
        px = list(fig.canvas.get_width_height())
    except Exception:                            # noqa: BLE001
        px = None
    # 英寸数直接问 figure：`px / 100` 只在 dpi == 100 时才成立，
    # 脚本一旦 set_dpi / 改 rcParams['figure.dpi'] 就会差整数倍。
    try:
        inch = [round(float(v), 4) for v in fig.get_size_inches()]
    except Exception:                            # noqa: BLE001
        inch = None
    out = {'figsize_px': px, 'figsize_in': inch, 'axes': axes}
    if with_text:
        # 注意顺序：上面的位置/字号是 **draw 之前** 采的（与 describe 口径一致），
        # 文字盒则必须在 draw 之后 —— 所以补在最后，不改动已有字段。
        fig_texts, err = _collect_texts(fig, axes)
        out['_fig_texts'] = fig_texts
        if err:
            out['_text_error'] = err
    return out


# os.chdir 是**进程级**全局状态，而 MCP server 会用线程并发执行同步工具
# （mcp 2.3 走 anyio.to_thread）。两个工具同时采集时，A 的脚本会在 B 的目录里
# 执行 —— 实测表现为读到错误的数据且毫无提示（t3-M15 / t6-G4）。
# 同一进程内用一把锁把"chdir + 跑脚本 + 还原 cwd"整段串行化即可；
# 不同进程各有自己的 cwd，不受影响。
_CHDIR_LOCK = threading.Lock()


def _fd_to_stderr():
    """把 **fd 1** 也接到 stderr 上，返回保存的原 fd（失败返回 None）。

    只在 Python 对象层换 ``sys.stdout`` 是拦不住脚本里的 ``os.write(1, ...)``、
    C 扩展或子进程继承 fd1 的输出的 —— 那会把 stdout 上的 JSON 污染成不可解析
    （t3-H3 / t6-H4）。这里直接 dup2 到 fd 层。
    """
    try:
        sys.stdout.flush()
        saved = os.dup(1)
        os.dup2(2, 1)
        return saved
    except OSError:
        return None


def _fd_restore(saved):
    if saved is None:
        return
    try:
        sys.stdout.flush()
    except Exception:                             # noqa: BLE001
        pass
    try:
        os.dup2(saved, 1)
        os.close(saved)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# A3：进程全局状态的快照 / 还原
#
# sys.path / sys.modules / matplotlib rcParams 都是**进程级**状态，而 MCP server
# 与"把 verify 当库用"的调用方是长驻的 —— 不回收就会跨界污染（CH-2，T6 独立复现）：
#   * sys.path 留着上一个脚本的目录 → B 脚本能 import 到只存在于 A 目录的模块，
#     同一个脚本的判定随调用历史翻转（先失败、后成功）；
#   * sys.modules 留着旧模块 → 改了 helper 之后仍读旧值（1 vs 干净的 2）；
#   * rcParams 串味 → 量出的字号是上一个脚本的值（12.0 → 26.4），而这份数值会被
#     `apply --write` 如实写进用户源码。
# A1 把用户脚本挪进子进程之后，CLI / MCP 路径上已经是双保险；但库嵌入场景
# （直接调 verify.collect）只有这一层，所以两层都要有。
# ---------------------------------------------------------------------------
def _snapshot_process_state() -> Dict[str, Any]:
    import matplotlib
    return {'path': list(sys.path),
            'modules': set(sys.modules),
            'rc': dict(matplotlib.rcParams)}


def _restore_process_state(snap: Dict[str, Any]) -> None:
    """把快照还原（只做"删掉新增的 / 改回改动的"，不动调用方自己的状态）。"""
    import matplotlib
    try:
        sys.path[:] = snap['path']
    except (TypeError, ValueError):
        pass
    for name in [k for k in sys.modules if k not in snap['modules']]:
        del sys.modules[name]
    rc = matplotlib.rcParams
    changed = {k: v for k, v in snap['rc'].items() if k in rc and rc[k] != v}
    if changed:
        try:
            rc.update(changed)
        except (KeyError, ValueError, TypeError):
            # 个别键在当前 matplotlib 上是只读/已弃用的：尽力还原，不为它崩掉采集
            pass


@contextlib.contextmanager
def _collect_session(script: str):
    """一次"跑用户脚本"的进程状态边界：cwd / sys.stdout / fd1 / 全局状态。

    进块之前摆好拦截（plt.show / plt.close / matplotlib.use / switch_backend
    全拦掉，cwd 切到脚本目录），出块时**无论成功失败**都还原 —— 包括 A3 的
    sys.path / sys.modules / rcParams 快照。yield 出 ``pyplot`` 给调用方用。
    """
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.switch_backend('Agg')       # 真初始化后端，之后再拦（否则 pyplot 内部取不到）

    real = (plt.show, plt.close, matplotlib.use, plt.switch_backend)
    plt.show = lambda *a, **k: None
    plt.close = lambda *a, **k: None
    matplotlib.use = lambda *a, **k: None
    plt.switch_backend = lambda *a, **k: None
    # 脚本自己的 print 绝不能污染调用方的 stdout —— MCP server 正是用 stdout 传
    # JSON-RPC，混进一行 "savefig done" 就会让客户端解析失败。一律改道 stderr。
    _real_stdout = sys.stdout
    sys.stdout = sys.stderr
    _fd_saved = _fd_to_stderr()     # fd 层也接过去：脚本 os.write(1,...) 拦得住
    snap = _snapshot_process_state()
    d = os.path.dirname(os.path.abspath(script))
    _CHDIR_LOCK.acquire()          # cwd 是进程级全局状态，见 _CHDIR_LOCK 处说明
    old = os.getcwd()
    os.chdir(d)
    try:
        if d not in sys.path:
            sys.path.insert(0, d)
        yield plt
    finally:
        plt.show, plt.close, matplotlib.use, plt.switch_backend = real
        try:
            plt.close('all')            # 先做可能打印的清理，再恢复 stdout
        except Exception:                        # noqa: BLE001
            pass
        _fd_restore(_fd_saved)
        sys.stdout = _real_stdout
        os.chdir(old)
        _CHDIR_LOCK.release()
        _restore_process_state(snap)


def _states_of_all_figs(plt, with_text: bool, exited: bool = False
                        ) -> Tuple[Optional[List[Dict[str, Any]]], str]:
    """把所有 figure 采成状态列表（``collect_all`` 的正常 / SystemExit 两条路共用）。"""
    try:
        nums = list(plt.get_fignums())
        if not nums:
            return None, ('脚本 sys.exit 退出，且没有留下任何图' if exited
                          else '脚本没有留下任何图（可能存完图就 close 了）')
        out = []
        for k, num in enumerate(nums):
            st = _state(plt.figure(num), with_text=with_text)
            st['fig_index'] = k
            st['n_figs'] = len(nums)
            out.append(st)
        return out, ''
    except Exception as e:                       # noqa: BLE001 采集失败要给人话，不是裸栈
        return None, '%s: %s' % (type(e).__name__, e)


def _state_of_one_fig(plt, fig_index, with_text: bool, exited: bool = False
                      ) -> Tuple[Optional[Dict], str]:
    """取目标图状态（``collect`` 的正常 / SystemExit 两条路共用）。"""
    try:
        nums = list(plt.get_fignums())
    except Exception as e:                       # noqa: BLE001
        return None, '%s: %s' % (type(e).__name__, e)
    if not nums:
        return None, ('脚本 sys.exit 退出，且没有留下任何图' if exited
                      else '脚本没有留下任何图（可能存完图就 close 了）')
    # 越界必须报错，不能静默返回最后一张：调用方（agent）会拿到"另一张图"的
    # 数据却以为是自己要的那张，而返回体里原本连 fig_index 都没有可判别。
    # 负值（含 verify 子进程约定的 -1）表示"最后一张"，不是越界。
    if not exited and isinstance(fig_index, int) and fig_index >= 0 \
            and not (0 <= fig_index < len(nums)):
        return None, ('第 %d 张图不存在（脚本共留下 %d 张）；'
                      '请用 0..%d 之间的序号，或用 --all-figs 全部导出'
                      % (fig_index, len(nums), len(nums) - 1))
    idx = (fig_index if isinstance(fig_index, int) and fig_index >= 0
           else len(nums) - 1)
    if exited and not (0 <= idx < len(nums)):
        return None, ('第 %d 张图不存在（脚本共留下 %d 张）'
                      % (fig_index, len(nums)))
    st = _state(plt.figure(nums[idx]), with_text=with_text)
    # 必须自报家门：产物会被直接喂回 apply，而 apply 在没有 fig_index 时
    # 只能按第 0 张处理 → 参数会落到错的图上（多图脚本的 agent 闭环曾 100% 中招）。
    st['fig_index'] = idx
    st['n_figs'] = len(nums)
    return st, ''


def collect_all(script: str, with_text: bool = False,
                script_args: Optional[List[str]] = None
                ) -> Tuple[Optional[List[Dict[str, Any]]], str]:
    """跑一次脚本，采集**所有**图的状态。返回 (list|None, err)。

    与 collect 同一套拦截口径（plt.show / plt.close / matplotlib.use /
    switch_backend 全拦掉，cwd 切到脚本目录），只是把每张图都抽出来，
    每张附加 fig_index（0-based，顺序同 plt.get_fignums()）与 n_figs。

    ``script_args``：透传给用户脚本的 ``sys.argv[1:]``（bug #6 —— 依赖
    ``sys.argv`` 的脚本（``python fig.py --data x.npz``）在验证重跑里拿不到
    自己的参数，行为与基线不一致 → 误回滚）。runpy 只覆盖 ``argv[0]``、
    不动 ``argv[1:]``，所以这里显式把 ``sys.argv`` 摆成
    ``[script] + script_args`` 再跑，``finally`` 里还原。

    注意：这是**同进程**执行（副作用由 ``_collect_session`` 的快照兜底）。
    CLI / MCP 走的是 ``collect_isolated`` —— 进程级边界，见那里的说明。
    """
    with _collect_session(script) as plt:
        exited = False
        _argv0 = list(sys.argv)
        try:
            if script_args:
                sys.argv = [os.path.abspath(script)] + list(script_args)
            runpy.run_path(script, run_name='__main__')
        except SystemExit:
            # 脚本自己 sys.exit()：图还挂在 Gcf 上，继续往下取（A2 / CH-1）。
            # 0.1.8 只给 collect 加了这条分支，collect_all 漏了 —— 于是
            # `describe --all-figs` 对 sys.exit(0) 的脚本零输出（rc=0 却什么都没打），
            # MCP 的 describe_layout(all_figs=True) 更会把 server 进程直接带走
            # （SystemExit 穿过 anyio TaskGroup → BaseExceptionGroup([SystemExit(0)])）。
            exited = True
        except Exception as e:                       # noqa: BLE001
            return None, '%s: %s' % (type(e).__name__, e)
        finally:
            sys.argv = _argv0
        return _states_of_all_figs(plt, with_text, exited=exited)


def collect(script: str, fig_index: Optional[int] = None,
            with_text: bool = False,
            script_args: Optional[List[str]] = None
            ) -> Tuple[Optional[Dict], str]:
    """在当前进程跑脚本并抽取目标图状态。返回 (state|None, err)。

    与 launch 一样拦掉 plt.show / plt.close / matplotlib.use / switch_backend，
    并以脚本所在目录为 cwd（脚本里的相对路径/存图位置才和平时一致）。

    ``script_args``：透传给用户脚本的 ``sys.argv[1:]``（bug #6，语义与
    ``collect_all`` 相同 —— runpy 只覆盖 ``argv[0]``，依赖 ``sys.argv`` 的
    脚本必须在跑之前把 ``sys.argv`` 摆成 ``[script] + script_args``）。

    CLI / MCP 用 ``collect_isolated``（子进程）；这里的同进程版本留给
    已经在子进程里的调用方（``verify.py main`` 自己）与库嵌入场景。
    """
    with _collect_session(script) as plt:
        exited = False
        _argv0 = list(sys.argv)
        try:
            if script_args:
                sys.argv = [os.path.abspath(script)] + list(script_args)
            runpy.run_path(script, run_name='__main__')
        except SystemExit:
            # 脚本自己 sys.exit()：不算采集失败 —— 图还挂在 Gcf 上，继续往下取。
            # 原先只捕 Exception，SystemExit 会直接穿出去：CLI 退出码被顶成脚本的
            # 退出码，而 --json 下 stdout 一个字节都没有（t3-#9 / t6）。
            exited = True
        except Exception as e:                       # noqa: BLE001
            return None, '%s: %s' % (type(e).__name__, e)
        finally:
            sys.argv = _argv0
        return _state_of_one_fig(plt, fig_index, with_text, exited=exited)


# ---------------------------------------------------------------------------
# A1：把用户脚本的执行挪进**子进程**
# ---------------------------------------------------------------------------
ISOLATED_TIMEOUT = 600      # 秒；子进程采集的默认超时（可用 MPLTWEAK_TIMEOUT 放宽）


def _isolated_timeout() -> float:
    raw = os.environ.get('MPLTWEAK_TIMEOUT')
    if raw:
        try:
            v = float(raw)
            if v > 0:
                return v
        except (TypeError, ValueError):
            pass
    return float(ISOLATED_TIMEOUT)


def _child_env() -> Dict[str, str]:
    """子进程环境：继承 + 保证"没 pip install -e 的 dev 检出"也能 import 本包。"""
    env = dict(os.environ, MPLBACKEND='Agg')
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    parts = [p for p in (env.get('PYTHONPATH') or '').split(os.pathsep) if p]
    if root not in parts:
        parts.insert(0, root)
    env['PYTHONPATH'] = os.pathsep.join(parts)
    return env


def collect_isolated(script: str, fig_index: Optional[int] = None,
                     with_text: bool = False, all_figs: bool = False,
                     python: Optional[str] = None,
                     timeout: Optional[float] = None
                     ) -> Tuple[Optional[Any], str]:
    """A1：在**独立子进程**里执行用户脚本并采集状态。

    返回契约与 ``collect`` / ``collect_all`` 完全一致：成功给
    ``(state, '')`` 或 ``(states, '')``，失败给 ``(None, err)``。

    为什么必须是"进程边界"而不是"跑完再清理"：清理只能覆盖**已知**的那几项全局
    状态，而脚本还能注册 atexit、起线程、monkeypatch 模块、往 sys.modules 里塞东西、
    甚至 ``os._exit()``。CLI / MCP server 都是**一次命令跑多个脚本**的场景，
    子进程是唯一能一次性解决全部污染、并让 MCP server 不被 `sys.exit()` 带走的边界
    （apply 的验证链早就是这么做的）。
    """
    python = python or sys.executable
    timeout = timeout if timeout is not None else _isolated_timeout()
    fd, out_path = tempfile.mkstemp(prefix='mpltweak_state_', suffix='.json')
    os.close(fd)
    cmd = [python, '-m', 'mpltweak.verify', os.path.abspath(script), out_path,
           '--envelope']
    if all_figs:
        cmd.append('--all-figs')
    if with_text:
        cmd.append('--text')
    if isinstance(fig_index, int):
        cmd += ['--fig', str(fig_index)]
    try:
        # stdout 丢弃（子进程内部已把 fd1 接到 stderr，脚本的 print 仍能被用户看到），
        # stderr 继承：脚本报错/打印照旧出现在终端，和同进程时代的行为一致。
        proc = subprocess.run(cmd, env=_child_env(), stdout=subprocess.DEVNULL,
                              timeout=timeout)
    except subprocess.TimeoutExpired:
        _unlink(out_path)
        return None, _msg.t('collect_timeout', sec=int(timeout))
    except OSError as e:
        _unlink(out_path)
        return None, str(e)
    try:
        with open(out_path, 'r', encoding='utf-8') as f:
            env = json.load(f)
    except (OSError, ValueError) as e:
        return None, _msg.t('collect_bad_state', err=e) if isinstance(
            e, ValueError) else _msg.t('collect_no_state', rc=proc.returncode)
    finally:
        _unlink(out_path)
    if not env.get('ok'):
        return None, env.get('error') or _msg.t('collect_no_state',
                                                rc=proc.returncode)
    if all_figs:
        return env.get('states') or [], ''
    state = env.get('state')
    if state is None:
        return None, _msg.t('collect_no_state', rc=proc.returncode)
    return state, ''


def _unlink(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


def _r(v):
    """把一串数字格式化进提示里：整数就显示成整数（网格身份 [1,3,0,0] 更好读）。"""
    try:
        out = []
        for x in v:
            fx = float(x)
            out.append(int(fx) if fx.is_integer() else round(fx, 3))
        return out
    except Exception:                            # noqa: BLE001
        return v


def compare(expected: List[Dict[str, Any]], actual: List[Dict[str, Any]],
            pos_tol: float = POS_TOL,
            fields_by_axis: Optional[Dict[int, set]] = None,
            unjudged: Optional[List[str]] = None) -> List[str]:
    # T75b：`unjudged` 是**出参**（纯增量，默认 None = 旧行为不变）：
    # 调用方传一个 list 进来，这里把"**参数里请求了、但实际值取不到/比不了**"
    # 的字段名塞进去 —— 禁止"没比却说通过"。
    """参数（期望）与目标图实测状态比对，返回不一致明细（空 = 一致）。

    fields_by_axis：{轴序号: {字段名, ...}} 时，每轴只比对列出的字段（原位写回
    模式下"没被原位改写的项"不比对，避免因刻意跳过而误判失败）。
    """
    bad: List[str] = []
    for e in expected or []:
        i = e.get('index')
        if not isinstance(i, int) or i >= len(actual or []):
            bad.append('ax%s 在目标图里不存在' % (i,))
            continue
        a = actual[i]
        fields = (fields_by_axis or {}).get(i)
        ep, ap = e.get('pos'), a.get('pos')
        # aspect 锁定的轴（imshow / cartopy 地图）位置由长宽比约束决定：draw 时
        # matplotlib 的 apply_aspect 会重调位置，比"设进去的位置"必然对不上，
        # 所以这类轴只比字号/grid/spines/clim，不比位置。
        locked = (bool(e.get('aspect_locked'))
                  or a.get('aspect') not in (None, 'auto'))
        if fields is None or 'pos' in fields:
            if not locked and ep and ap and any(abs(x - y) > pos_tol for x, y in zip(ep, ap)):
                bad.append('ax%d 位置 %s≠%s' % (i, _r(ep), _r(ap)))
        # T75b：字号按 **x/y 各自独立** 比对 —— `tick_fontsize` 是 x 刻度、
        # `tick_fontsize_y` 是 y 刻度；参数里带了哪个就比哪个。
        for key in ('title_fontsize', 'tick_fontsize', 'tick_fontsize_y'):
            if fields is not None and key not in fields:
                continue
            ev, av = e.get(key), a.get(key)
            if ev is None:
                continue
            if av is None:
                if unjudged is not None:
                    unjudged.append('ax%d.%s' % (i, key))
                continue
            if abs(float(ev) - float(av)) > FS_TOL:
                bad.append('ax%d %s %s≠%s' % (i, key, ev, av))
        # label_fontsize：x/y 任一等于参数即通过（原位写回可能只找到 set_xlabel 或
        # 只找到 set_ylabel）。T73（零兼容）：**参数里没写 y 时不再拿 x 的值当 y 的
        # 合法值** —— 只按 x 比（带 y 的文件仍走原口径，正常流程零变化）。
        # label_fontsize（x 侧）：**只比 x**。T73 之后"参数没写 y 就一个字节都不动
        # y"，所以**绝不能再拿 x 的请求值去当 y 的期望**（那是被 T73 删掉的兼容
        # 回退在比对层的镜像：x 没落地、只要 y 恰好等于 x 的请求值就会"通过"）。
        if fields is None or 'label_fontsize' in fields:
            ev = e.get('label_fontsize')
            if ev is not None:
                avx = a.get('label_fontsize')
                if avx is None:
                    if unjudged is not None:
                        unjudged.append('ax%d.label_fontsize' % i)
                elif abs(float(ev) - float(avx)) > FS_TOL:
                    bad.append('ax%d label_fontsize %s≠%s' % (i, ev, avx))
        # label_fontsize_y（y 侧）：**参数里带了才比**，比的是 y 自己的期望值。
        # 带了 y 却不比 = "没比却说通过" —— T75b 要消灭的正是这个。
        if fields is None or 'label_fontsize_y' in fields:
            evy = e.get('label_fontsize_y')
            if evy is not None:
                avy = a.get('label_fontsize_y')
                if avy is None:
                    if unjudged is not None:
                        unjudged.append('ax%d.label_fontsize_y' % i)
                elif abs(float(evy) - float(avy)) > FS_TOL:
                    bad.append('ax%d label_fontsize_y %s≠%s'
                               % (i, evy, avy))
        if (fields is None or 'grid' in fields) \
                and e.get('grid') is not None \
                and bool(e['grid']) != bool(a.get('grid')):
            bad.append('ax%d grid %s≠%s' % (i, e['grid'], a.get('grid')))
        if fields is None or 'spines' in fields:
            for k, v in (e.get('spines') or {}).items():
                if k in (a.get('spines') or {}) and bool(v) != bool(a['spines'][k]):
                    bad.append('ax%d spines[%s] %s≠%s' % (i, k, v, a['spines'][k]))
        if fields is None or 'clim' in fields:
            ec, ac = e.get('clim'), a.get('clim')
            if ec and ac:
                span = abs(float(ec[1]) - float(ec[0])) or 1.0
                if (abs(float(ec[0]) - float(ac[0])) > CLIM_REL_TOL * span
                        or abs(float(ec[1]) - float(ac[1])) > CLIM_REL_TOL * span):
                    bad.append('ax%d clim %s≠%s' % (i, _r(ec), _r(ac)))
        # 轴范围：容差按**量程的相对量**给（范围可能很大，如时间轴 1e9），
        # 与 clim 同一口径；只在参数里有值时比对（autoscale 的轴压根不记）。
        # 网格身份：调整块按下标（fig.axes[i]）寻址，脚本一变（加个 colorbar、
        # 条件分支少建一个轴）下标与面板的对应关系就会漂 —— 这里把"调图时这个下标
        # 是哪个网格格位"回比一遍，好让验证给出**指名道姓**的失败，而不是默默改错面板。
        # 变量名独立（_ecell/_acell）：上面 clim 分支已经用过 `ec`，串了就会拿 clim
        # 当网格身份比（补丁里真犯过这个错，§1 全绿变全红）。
        _ecell, _acell = e.get('cell'), a.get('cell')
        if isinstance(_ecell, list) and 'cell' in a:
            if _acell is None:
                # 期望是网格轴、实测这条轴不再是网格轴（被手工 add_axes 顶掉了）——
                # 轴身份已经变了，必须判失败（独立审阅 C1：原先这种情况被默默放行）。
                bad.append('ax%d 的网格身份对不上：期望 %s，实测该轴不是网格轴'
                           '（轴序/结构变了：块按下标寻址会改错面板）'
                           % (i, _r(_ecell)))
            elif _acell != _ecell:
                bad.append('ax%d 的网格身份 %s≠%s（轴序变了：块按下标寻址会改错面板）'
                           % (i, _r(_ecell), _r(_acell)))
        # 'cell' 不在实测里 = 采集侧没读到（旧参数/异常兜底）→ 不判，保持向后兼容
        for key in ('xlim', 'ylim'):
            if fields is not None and key not in fields:
                continue
            ex, ac2 = e.get(key), a.get(key)
            if not (isinstance(ex, (list, tuple)) and len(ex) == 2
                    and isinstance(ac2, (list, tuple)) and len(ac2) == 2):
                continue
            _span = abs(float(ex[1]) - float(ex[0])) or 1.0
            if (abs(float(ex[0]) - float(ac2[0])) > CLIM_REL_TOL * _span
                    or abs(float(ex[1]) - float(ac2[1])) > CLIM_REL_TOL * _span):
                bad.append('ax%d %s %s≠%s' % (i, key, _r(ex), _r(ac2)))
    return bad


def verify_semantic(script: str, data: Dict[str, Any], python: Optional[str] = None,
                    timeout: float = 300,
                    out_path: Optional[str] = None,
                    fields_by_axis: Optional[Dict[int, set]] = None,
                    figsize_edited: bool = False,
                    script_args: Optional[List[str]] = None
                    ) -> Tuple[Optional[bool], str]:
    """子进程重跑脚本 → dump 目标图状态 → 与参数比对。

    返回 ``(ok, detail)``：True 一致 / False 不一致（detail 为明细）/ None 无法判定。

    ``script_args``：透传给用户脚本的 ``sys.argv[1:]``（bug #6）。验证子进程的
    命令行在 ``--`` 之后追加这些参数（``verify.py main`` 把 ``--`` 之后的内容
    原样交给用户脚本），依赖 ``sys.argv`` 的脚本（``python fig.py --data x.npz``）
    重跑时才能拿到与基线一致的参数，否则行为不一致 → 误回滚 + 误归因。
    """
    python = python or sys.executable
    if out_path is None:
        d = os.path.dirname(os.path.abspath(script))
        out_path = os.path.join(d, '.tweak_params', '_verify_state.json')
    try:
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
    except OSError:
        pass
    try:
        os.remove(out_path)
    except OSError:
        pass
    fig_index = data.get('fig_index')
    idx_arg = str(fig_index) if isinstance(fig_index, int) else '-1'
    env = dict(os.environ, MPLBACKEND='Agg')
    cmd = [python, '-m', 'mpltweak.verify', script, out_path, idx_arg]
    if script_args:
        # `--` 分隔：子进程的 main() 看到它就不再解析后面的参数，原样透传
        # （否则 ``--data`` 之类会被子进程当成自己的开关而报用法错误）。
        cmd += ['--'] + list(script_args)
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        return None, '语义验证超时'
    except Exception as e:                       # noqa: BLE001
        return None, str(e)
    if not os.path.exists(out_path):
        tail = (r.stderr or b'').decode('utf-8', 'replace').strip()[-200:]
        return None, '未能取到图状态（%s）' % (tail or 'exit %d' % r.returncode)
    try:
        with open(out_path, 'r', encoding='utf-8') as f:
            actual = json.load(f)
    except Exception as e:                       # noqa: BLE001
        return None, '读取状态失败: %s' % e
    unjudged: List[str] = []
    bad = compare(data.get('axes') or [], actual.get('axes') or [],
                  fields_by_axis=fields_by_axis, unjudged=unjudged)
    # figsize 也必须比对：它同样会被写回，而"改到另一张图"恰恰在这条线上最容易
    # 静默通过（块路径的 edit_figsize 曾不传 fig_index，见 t1-A / t6-M1）。
    # 只在这次确实原位改了 figsize 时才比 —— 否则参数里的目标值本来就与当前
    # 代码不一致，会把"本图不改画布"误判成失败。
    if figsize_edited:
        ev_fs, av_fs = data.get('figsize_in'), actual.get('figsize_in')
        if ev_fs and av_fs and any(abs(float(a) - float(b)) > 0.01
                                   for a, b in zip(ev_fs, av_fs)):
            bad.append('figsize %s≠%s' % (_r(ev_fs), _r(av_fs)))
    # T75b：已请求但**判不了**的字段必须如实列出来（不许"没比却说通过"）。
    # 注意 ok 仍然只由 bad 决定：判不了 ≠ 判失败。
    _uj = ''
    if unjudged:
        _uj = '未判定字段（已请求但比不了）: %s' % ', '.join(
            sorted(set(unjudged))[:8])
    if bad:
        return False, ('; '.join(bad[:4]) + (' | ' + _uj if _uj else ''))
    return True, _uj


_USAGE = ('用法: python -m mpltweak.verify <script> <out.json> [fig_index] '
          '[--fig N] [--all-figs] [--text] [--envelope] [-- <脚本参数...>]\n')


def _int_or_none(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def main(argv=None) -> int:
    """子进程驱动（A1）：把图状态 dump 成 JSON，让父进程读。

    两种输出模式：

    * **旧模式**（不给 ``--envelope``，``verify_semantic`` 在用）：成功时把状态
      本身写进 ``out``；失败**不写文件** + 退出码 3 —— 这个契约不能动，
      写回引擎的"取不到状态 → 不否决"判定依赖它。
    * **信封模式**（``--envelope``，``collect_isolated`` 在用）：**永远**写一个
      ``{"ok": bool, "error": str, ...}``，父进程据此拿失败原因（子进程的 stderr
      混着用户脚本自己的 print，没法拿来解析）。

    ``--`` 分隔（bug #6）：``--`` 之后的内容不再当成本工具的开关/位置参数，
    原样收进 ``script_args`` 并透传给用户脚本的 ``sys.argv`` —— 依赖
    ``sys.argv`` 的脚本在验证重跑里才能拿到与基线一致的参数。
    """
    argv = list(sys.argv[1:] if argv is None else argv)
    pos = []
    all_figs = with_text = envelope = False
    fig: Optional[int] = None
    script_args: List[str] = []
    passthrough = False
    i = 0
    while i < len(argv):
        a = argv[i]
        if passthrough:
            script_args.append(a)
        elif a == '--':
            passthrough = True
        elif a == '--all-figs':
            all_figs = True
        elif a == '--text':
            with_text = True
        elif a == '--envelope':
            envelope = True
        elif a == '--fig':
            i += 1
            if i >= len(argv):
                sys.stderr.write(_USAGE)
                return 2
            fig = _int_or_none(argv[i])
        elif a.startswith('--'):
            sys.stderr.write(_USAGE)
            return 2
        else:
            pos.append(a)
        i += 1
    if len(pos) < 2:
        sys.stderr.write(_USAGE)
        return 2
    script, out = pos[0], pos[1]
    if fig is None and len(pos) > 2:
        fig = _int_or_none(pos[2])

    if all_figs:
        states, err = collect_all(script, with_text=with_text,
                                  script_args=script_args)
        state = None
    else:
        state, err = collect(script, fig, with_text=with_text,
                             script_args=script_args)
        states = None
    ok = not err

    if envelope:
        payload = {'ok': ok, 'error': '' if ok else err, 'all_figs': all_figs}
        if ok:
            if all_figs:
                payload['states'] = states
            else:
                payload['state'] = state
        try:
            with open(out, 'w', encoding='utf-8') as f:
                json.dump(payload, f, ensure_ascii=False)
        except OSError as e:
            sys.stderr.write('[verify] %s\n' % e)
            return 3
        return 0 if ok else 3

    if not ok:
        sys.stderr.write('[verify] %s\n' % err)
        return 3
    with open(out, 'w', encoding='utf-8') as f:
        json.dump(states if all_figs else state, f, ensure_ascii=False, indent=2)
    return 0


if __name__ == '__main__':
    sys.exit(main())
