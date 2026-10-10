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

子进程入口：``python -m mpltweak.verify <script> <out.json> [fig_index]``
"""

from __future__ import annotations

import json
import os
import runpy
import subprocess
import sys
import threading
from typing import Any, Dict, List, Optional, Tuple

POS_TOL = 2e-3        # 位置容差（figure 比例，参数存 4 位小数）
FS_TOL = 0.51         # 字号容差（磅）
CLIM_REL_TOL = 0.01   # clim 容差（相对量程）
TEXT_BOX_LIMIT = 600  # 文字盒条数上限（超多刻度的图别把状态 JSON 撑爆）


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


def collect_all(script: str, with_text: bool = False
                ) -> Tuple[Optional[List[Dict[str, Any]]], str]:
    """跑一次脚本，采集**所有**图的状态。返回 (list|None, err)。

    与 collect 同一套拦截口径（plt.show / plt.close / matplotlib.use /
    switch_backend 全拦掉，cwd 切到脚本目录），只是把每张图都抽出来，
    每张附加 fig_index（0-based，顺序同 plt.get_fignums()）与 n_figs。
    """
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.switch_backend('Agg')

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
    d = os.path.dirname(os.path.abspath(script))
    _CHDIR_LOCK.acquire()          # cwd 是进程级全局状态，见 _CHDIR_LOCK 处说明
    old = os.getcwd()
    os.chdir(d)
    if d not in sys.path:
        sys.path.insert(0, d)
    try:
        runpy.run_path(script, run_name='__main__')
        nums = plt.get_fignums()
        if not nums:
            return None, '脚本没有留下任何图（可能存完图就 close 了）'
        out = []
        for _k, _num in enumerate(nums):
            _st = _state(plt.figure(_num), with_text=with_text)
            _st['fig_index'] = _k
            _st['n_figs'] = len(nums)
            out.append(_st)
        return out, ''
    except Exception as e:                       # noqa: BLE001
        return None, '%s: %s' % (type(e).__name__, e)
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


def collect(script: str, fig_index: Optional[int] = None,
            with_text: bool = False) -> Tuple[Optional[Dict], str]:
    """在当前进程跑脚本并抽取目标图状态。返回 (state|None, err)。

    与 launch 一样拦掉 plt.show / plt.close / matplotlib.use / switch_backend，
    并以脚本所在目录为 cwd（脚本里的相对路径/存图位置才和平时一致）。
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
    d = os.path.dirname(os.path.abspath(script))
    _CHDIR_LOCK.acquire()          # cwd 是进程级全局状态，见 _CHDIR_LOCK 处说明
    old = os.getcwd()
    os.chdir(d)
    if d not in sys.path:
        sys.path.insert(0, d)
    try:
        runpy.run_path(script, run_name='__main__')
        nums = plt.get_fignums()
        if not nums:
            return None, '脚本没有留下任何图（可能存完图就 close 了）'
        # 越界必须报错，不能静默返回最后一张：调用方（agent）会拿到"另一张图"的
        # 数据却以为是自己要的那张，而返回体里原本连 fig_index 都没有可判别。
        # 负值（含 verify 子进程约定的 -1）表示"最后一张"，不是越界。
        # 越界只针对 >= 0 的序号：静默返回最后一张会让调用方拿到另一张图的
        # 数据却以为是自己要的那张，而返回体里原本连 fig_index 都没有可判别。
        if isinstance(fig_index, int) and fig_index >= 0 \
                and not (0 <= fig_index < len(nums)):
            return None, ('第 %d 张图不存在（脚本共留下 %d 张）；'
                          '请用 0..%d 之间的序号，或用 --all-figs 全部导出'
                          % (fig_index, len(nums), len(nums) - 1))
        idx = (fig_index if isinstance(fig_index, int) and fig_index >= 0
               else len(nums) - 1)
        st = _state(plt.figure(nums[idx]), with_text=with_text)
        # 必须自报家门：产物会被直接喂回 apply，而 apply 在没有 fig_index 时
        # 只能按第 0 张处理 → 参数会落到错的图上（多图脚本的 agent 闭环曾 100% 中招）。
        st['fig_index'] = idx
        st['n_figs'] = len(nums)
        return st, ''
    except SystemExit:
        # 脚本自己 sys.exit()：不算采集失败 —— 图还挂在 Gcf 上，继续往下取。
        # 原先只捕 Exception，SystemExit 会直接穿出去：CLI 退出码被顶成脚本的
        # 退出码，而 --json 下 stdout 一个字节都没有（t3-#9 / t6）。
        try:
            nums = plt.get_fignums()
        except Exception:                        # noqa: BLE001
            nums = []
        if not nums:
            return None, '脚本 sys.exit 退出，且没有留下任何图'
        idx = (fig_index if isinstance(fig_index, int) and fig_index >= 0
               else len(nums) - 1)
        if not (0 <= idx < len(nums)):
            return None, ('第 %d 张图不存在（脚本共留下 %d 张）'
                          % (fig_index, len(nums)))
        st = _state(plt.figure(nums[idx]), with_text=with_text)
        st['fig_index'] = idx
        st['n_figs'] = len(nums)
        return st, ''
    except Exception as e:                       # noqa: BLE001
        return None, '%s: %s' % (type(e).__name__, e)
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
            fields_by_axis: Optional[Dict[int, set]] = None) -> List[str]:
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
        for key in ('title_fontsize', 'tick_fontsize'):
            if fields is not None and key not in fields:
                continue
            ev, av = e.get(key), a.get(key)
            if ev is None or av is None:
                continue
            if abs(float(ev) - float(av)) > FS_TOL:
                bad.append('ax%d %s %s≠%s' % (i, key, ev, av))
        # label_fontsize：x/y 任一等于参数即通过（原位写回可能只找到 set_xlabel 或
        # 只找到 set_ylabel；脚本 x/y 字号可能本来就不同——只比 x 会漏检 y 异值）
        if fields is None or 'label_fontsize' in fields:
            ev = e.get('label_fontsize')
            if ev is not None:
                avx, avy = a.get('label_fontsize'), a.get('label_fontsize_y')
                if (avx is not None and abs(float(ev) - float(avx)) <= FS_TOL) \
                        or (avy is not None and abs(float(ev) - float(avy)) <= FS_TOL):
                    pass
                elif avx is not None or avy is not None:
                    bad.append('ax%d label_fontsize %s≠%s/%s'
                               % (i, ev, avx, avy))
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
                    figsize_edited: bool = False
                    ) -> Tuple[Optional[bool], str]:
    """子进程重跑脚本 → dump 目标图状态 → 与参数比对。

    返回 ``(ok, detail)``：True 一致 / False 不一致（detail 为明细）/ None 无法判定。
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
    try:
        r = subprocess.run([python, '-m', 'mpltweak.verify', script, out_path, idx_arg],
                           capture_output=True, timeout=timeout, env=env)
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
    bad = compare(data.get('axes') or [], actual.get('axes') or [],
                  fields_by_axis=fields_by_axis)
    # figsize 也必须比对：它同样会被写回，而"改到另一张图"恰恰在这条线上最容易
    # 静默通过（块路径的 edit_figsize 曾不传 fig_index，见 t1-A / t6-M1）。
    # 只在这次确实原位改了 figsize 时才比 —— 否则参数里的目标值本来就与当前
    # 代码不一致，会把"本图不改画布"误判成失败。
    if figsize_edited:
        ev_fs, av_fs = data.get('figsize_in'), actual.get('figsize_in')
        if ev_fs and av_fs and any(abs(float(a) - float(b)) > 0.01
                                   for a, b in zip(ev_fs, av_fs)):
            bad.append('figsize %s≠%s' % (_r(ev_fs), _r(av_fs)))
    return (not bad), ('; '.join(bad[:4]) if bad else '')


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) < 2:
        sys.stderr.write('用法: python -m mpltweak.verify <script> <out.json> [fig_index]\n')
        return 2
    script, out = argv[0], argv[1]
    try:
        idx: Optional[int] = int(argv[2])
    except (IndexError, ValueError):
        idx = None
    state, err = collect(script, idx)
    if state is None:
        sys.stderr.write('[verify] %s\n' % err)
        return 3
    with open(out, 'w', encoding='utf-8') as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    return 0


if __name__ == '__main__':
    sys.exit(main())
