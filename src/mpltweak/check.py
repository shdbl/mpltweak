# -*- coding: utf-8 -*-
"""mpltweak check —— 排版体检：不看图，只按几何规则挑毛病。

这是"人 + agent 协作"里 **agent 自检** 那一步：AI 画完图看不到好坏，
但对齐、等大、间距均匀、字号统一这些是**几何规则**，用数字就能判断。

    mpltweak check fig1.py            # 人读报告
    mpltweak check fig1.py --json     # 机器可读（agent 用）
    mpltweak check fig1.py --tol 0.02 # 放宽容差

退出码：0 = 没发现问题；1 = 有 error 级问题（方便接 CI / 让 agent 判断）。

规则刻意"克制"：只在有把握时报，宁可漏报也不误报 ——
colorbar 轴、aspect 锁定的轴（cartopy 地图）不参与几何比对。
"""

from __future__ import annotations

import argparse
import ast
import math
import os
import sys

from . import layoutwarn
from . import messages as _msg
from . import verify

# 几何比对的容差
DEFAULT_TOL = 0.010
# 两个轴若"近到 0.2 以内"就认为它们本来该对齐（否则是分列/分区，不算不齐）
ALIGN_NEAR = 0.20
# --- 文字渲染尺寸检查（按真实像素外框，不是几何框）---
# 超出画布：留一点余量再判（文字贴边不算"被裁"）
TEXT_EDGE_EPS = 0.002
# 互压：重叠面积下限（figure 比例²）——碰一下不算压，肉眼看得出来才算
TEXT_OVERLAP_MIN = 1.5e-3
# 互压最多报几条（刻度标签多的图别刷屏）
TEXT_OVERLAP_REPORT = 6
# 同轴相邻刻度标签：沿刻度方向的重叠达到标签尺寸的这么多比例，才算"太密"
TICK_CROWD_RATIO = 0.20
# colorbar × 其它轴的矩形相交判定用**独立、更小**的阈值（t67 / 报告 P1-3）。
# 为什么不能沿用 --tol 的 0.010：这类碰撞常常只有 0.5%~1% 图高（报告实测 0.0059
# ≈ 0.59% 图高 ≈ 17px @300dpi），用 0.010 会**正好漏掉**；而 0.002（0.2% 图高
# ≈ 5.8px @6in/300dpi）仍远大于"仅相接"的浮点噪声。
# 口径：取 min(--tol, CB_OVERLAP_TOL) —— 用户可以把 tol 调得更严，但**调不松**
# 这条（"条带压在图上"是肉眼可见的排版事故，不该被一个宽松容差静默掉）。
CB_OVERLAP_TOL = 0.002
# 文字种类 → 人话 key（写进提示里，用户才知道是哪儿；CH-12：中英都要有）
_KIND_KEYS = ('title', 'xlabel', 'ylabel', 'xtick', 'ytick', 'text',
              'figtext', 'capped')


def _kind(kind):
    """文字种类的人话名（跟随当前语言；未知种类退回 '文字'）。"""
    k = kind if kind in _KIND_KEYS else 'text'
    return _msg.t('chk_kind_' + k)


def _join(items):
    """列表连接符：中文用「、」，英文用「, 」。"""
    return ('、' if _msg.current_lang() == 'zh' else ', ').join(items)


def _tol_positive(tol):
    """``tol`` 必须是**有限正数**（CH-3）。

    0.1.8 只挡了 ``tol <= 0``：``--tol inf`` 能过，于是 ``w/tol``、``round(w/tol)``
    全部退化成 0，越界/对齐/重叠/间距规则**同时静默失效**，JSON 里还吐出
    `"tol": Infinity`（不是合法 JSON，jq / JSON.parse 直接解析失败），命令却 rc=0。
    """
    return math.isfinite(tol) and tol > 0


def _normal(axes):
    """参与几何检查的轴：排除 colorbar 与 aspect 锁定的轴。"""
    return [a for a in axes
            if not a.get('is_colorbar') and a.get('aspect') in (None, 'auto')]


def _pos(a):
    p = a.get('pos') or [0, 0, 0, 0]
    return float(p[0]), float(p[1]), float(p[2]), float(p[3])


def _fmt(v):
    return ('%.3f' % v).rstrip('0').rstrip('.')


def _name(a):
    """轴的可读名字（用 fig.axes 序号，写回时也是这个序号）。"""
    return 'ax%s' % a.get('index')


def analyze(axes, tol=DEFAULT_TOL):
    """返回 (problems, warnings)。每条 = dict(rule/level/axes/msg/detail)。

    ``tol`` 必须大于 0：下面多处用 ``w / tol`` 做聚类，tol=0 会 ZeroDivisionError
    （t6-M6）。MCP 的 check_layout 也直接调这里，所以守在这一层而不是只守 CLI。
    """
    if not _tol_positive(tol):
        raise ValueError(_msg.t_en('chk_tol_invalid', tol=tol))
    problems, warns = [], []
    normal = _normal(axes)
    n = len(normal)

    # ---- 1. 越界（超出画布 0~1）----
    for a in normal:
        x0, y0, w, h = _pos(a)
        out = []
        if x0 < -tol:
            out.append(_msg.t('chk_edge_left', v=_fmt(x0)))
        if y0 < -tol:
            out.append(_msg.t('chk_edge_bottom', v=_fmt(y0)))
        if x0 + w > 1 + tol:
            out.append(_msg.t('chk_edge_right', v=_fmt(x0 + w)))
        if y0 + h > 1 + tol:
            out.append(_msg.t('chk_edge_top', v=_fmt(y0 + h)))
        if out:
            problems.append({
                'rule': 'out_of_canvas', 'level': 'error', 'axes': [_name(a)],
                'msg': _msg.t('chk_out_of_canvas', ax=_name(a),
                              edges=_join(out)),
            })

    # ---- 2. 重叠（两两相交，忽略仅相接的）----
    for i in range(n):
        for j in range(i + 1, n):
            ax0, ay0, aw, ah = _pos(normal[i])
            bx0, by0, bw, bh = _pos(normal[j])
            ox = min(ax0 + aw, bx0 + bw) - max(ax0, bx0)
            oy = min(ay0 + ah, by0 + bh) - max(ay0, by0)
            if ox > tol and oy > tol:
                problems.append({
                    'rule': 'overlap', 'level': 'error',
                    'axes': [_name(normal[i]), _name(normal[j])],
                    'msg': _msg.t('chk_overlap', a=_name(normal[i]),
                                  b=_name(normal[j]), ox=ox, oy=oy),
                })

    # ---- 2b. colorbar × **任意轴**（含 aspect 锁定的 cartopy 地图）矩形相交 ----
    #   几何比对整体跳过 aspect_locked 轴（它们的长宽比由数据/投影定死，动不了），
    #   但"colorbar 压在图上"是科研图里**最高频的真实排版事故**，而"极地图 +
    #   colorbar"这个最常见组合**恰好**落在那个盲区里（t67 / 报告 P1-3 实测：
    #   相交 0.2758 × 0.0059，check 却给 ok:true / problems:[] / warnings:[]）。
    #   所以这一条单独保留、只报 warn（不动 --strict 语义、不动任何既有规则）。
    _cb_eps = min(tol, CB_OVERLAP_TOL)
    for _cb in [a for a in axes if a.get('is_colorbar')]:
        cx0, cy0, cw, ch = _pos(_cb)
        for _b in axes:
            if _b is _cb:
                continue
            bx0, by0, bw, bh = _pos(_b)
            ox = min(cx0 + cw, bx0 + bw) - max(cx0, bx0)
            oy = min(cy0 + ch, by0 + bh) - max(cy0, by0)
            if ox > _cb_eps and oy > _cb_eps:
                warns.append({
                    'rule': 'colorbar_overlap', 'level': 'warn',
                    'axes': [_name(_cb), _name(_b)],
                    'msg': _msg.t('chk_colorbar_overlap', a=_name(_cb),
                                  b=_name(_b), ox=ox, oy=oy),
                    'overlap': [round(ox, 4), round(oy, 4)],
                })

    # ---- 3. 边缘不齐：某轴的左边（或下边）与"最近的另一个轴"差一点点 ----
    #        差得远 = 本来就在另一列/另一行，不算问题。
    seen_pairs = set()
    for key, label in ((0, 'chk_edge_left'), (1, 'chk_edge_bottom')):
        vals = [(a, _pos(a)[key]) for a in normal]
        for a, v in vals:
            near = [w for b, w in vals if b is not a and abs(w - v) <= ALIGN_NEAR]
            if not near:
                continue
            d = min(near, key=lambda w: abs(w - v))
            off = abs(d - v)
            if tol < off <= ALIGN_NEAR:
                same = [b for b, w in vals if b is not a and abs(w - d) < tol]
                if not same:
                    continue
                # 对称的这一对只报一次（ax0↔ax3 与 ax3↔ax0）
                pk = (key, tuple(sorted([a.get('index'), same[0].get('index')])))
                if pk in seen_pairs:
                    continue
                seen_pairs.add(pk)
                problems.append({
                    'rule': 'not_aligned', 'level': 'error',
                    'axes': [_name(a), _name(same[0])],
                    'msg': _msg.t('chk_not_aligned', ax=_name(a),
                                  edge=_msg.t(label), v=_fmt(v),
                                  others='/'.join(_name(b) for b in same[:3]),
                                  off=_fmt(off)),
                })

    # ---- 4. 大小不一：多数一致的尺寸里混进少数派 ----
    from collections import Counter
    sizes = Counter()
    for a in normal:
        _, _, w, h = _pos(a)
        sizes[(round(w / tol), round(h / tol))] += 1
    if len(sizes) > 1:
        (mw, mh), mc = sizes.most_common(1)[0]
        # 要有明确的"主流尺寸"才谈得上不一致：多数派至少 3 个、且占 2/3 以上。
        # 原先的 `mc >= 2 and mc >= n / 2` 会把"上宽下两窄"这种**合法的非对称
        # 排版**误报（n=3 时 2 个窄的就算主流，那个宽的被当成异类）——
        # 与"宁可漏报也不误报"的定位矛盾（t3-M3 / t6-M14）。
        if mc >= 3 and mc * 3 >= n * 2:
            odd = []
            for a in normal:
                _, _, w, h = _pos(a)
                if (round(w / tol), round(h / tol)) != (mw, mh):
                    odd.append('%s %.3f×%.3f' % (_name(a), w, h))
            if odd:
                problems.append({
                    'rule': 'size_mismatch', 'level': 'error',
                    'axes': [o.split()[0] for o in odd],
                    'msg': _msg.t('chk_size_mismatch', mw=mw * tol, mh=mh * tol,
                                  odd=_join(odd)),
                })

    # ---- 5. 间距不均：同一列（左边相同）的面板，垂直间隙应一致 ----
    groups = {}
    for a in normal:
        x0, y0, w, h = _pos(a)
        # 归到"最接近的已有列"（容差内取最近）。若只取第一个命中项，
        # x0 恰好跨网格（如 0.08 / 0.089 / 0.10，tol=0.01）时会被拆成两组，
        # 导致"间距不均"漏报。
        best_k, best_d = None, tol
        for k, members in groups.items():
            for m in members:        # 与组内**任一**成员接近即同一列（链式归并）
                d = abs(_pos(m)[0] - x0)
                if d < best_d:
                    best_k, best_d = k, d
        groups.setdefault(
            best_k if best_k is not None else round(x0 / tol), []).append(a)
    for col in groups.values():
        if len(col) < 3:
            continue
        col = sorted(col, key=lambda a: _pos(a)[1])
        gaps = []
        for i in range(1, len(col)):
            _, py, _, ph = _pos(col[i - 1])
            _, cy, _, _ = _pos(col[i])
            gaps.append(cy - (py + ph))
        if len(gaps) >= 2 and (max(gaps) - min(gaps)) > 2 * tol:
            problems.append({
                'rule': 'uneven_gap', 'level': 'error',
                'axes': [_name(a) for a in col],
                'msg': _msg.t('chk_uneven_gap',
                              gaps=' / '.join(_fmt(g) for g in gaps)),
            })

    # ---- 6. 字号不统一（降级为提示：有时是有意的）----
    for key, label in (('title_fontsize', 'chk_font_title'),
                       ('label_fontsize', 'chk_font_label'),
                       ('tick_fontsize', 'chk_font_tick')):
        vals = [(a, a.get(key)) for a in normal if a.get(key)]
        if len(vals) < 3:
            continue
        cnt = Counter(round(float(v), 1) for _, v in vals)
        if len(cnt) > 1:
            mode, mc = cnt.most_common(1)[0]
            if mc >= len(vals) / 2:
                odd = ['%s %spt' % (_name(a), v) for a, v in vals
                       if round(float(v), 1) != mode]
                if odd:
                    warns.append({
                        'rule': 'font_mismatch', 'level': 'warn',
                        'axes': [o.split()[0] for o in odd],
                        'msg': _msg.t('chk_font_mismatch',
                                      label=_msg.t(label), mode=mode,
                                      odd=_join(odd)),
                    })

    # ---- 7. 轴范围不统一（多面板常见毛病：同一组面板该共享 xlim/ylim）----
    # 只在"有明显多数派"时报（≥60% 的面板范围一致、另有少数不同）——各自为政的多面板
    # 图（每个面板画不同物理量）本来就不该统一，这种情况下不报。
    for key, label in (('xlim', 'chk_limit_x'), ('ylim', 'chk_limit_y')):
        vals = [(a, a.get(key)) for a in normal
                if isinstance(a.get(key), (list, tuple)) and len(a[key]) == 2]
        if len(vals) < 3:
            continue
        groups = {}                       # {范围: [轴, ...]}
        for a, v in vals:
            groups.setdefault((round(float(v[0]), 6), round(float(v[1]), 6)),
                              []).append(a)
        if len(groups) < 2:
            continue
        ranked = sorted(groups.items(), key=lambda kv: -len(kv[1]))
        mode, top = ranked[0]
        if len(top) < 0.6 * len(vals):
            continue
        odd = []
        for k, g in ranked[1:]:
            for a in g:
                odd.append('%s %s' % (_name(a), list(k)))
        if odd:
            warns.append({
                'rule': 'limits_mismatch', 'level': 'warn',
                'axes': [o.split()[0] for o in odd],
                'msg': _msg.t('chk_limits_mismatch', label=_msg.t(label),
                              n=len(top), mode=list(mode), odd=_join(odd)),
            })

    return problems, warns


def analyze_texts(state, tight=False):
    """按**真实渲染尺寸**查文字：超出画布（error）/ 互相压住（warn）。

    与几何规则的分工：几何规则只看轴的位置框，**看不到文字**——所以
    "刻度标签特别长、已经戳出画布"或"标题和邻图标题压在一起"这类问题它一律漏报。
    这里的输入是 ``verify._state(..., with_text=True)`` 采集的文字盒
    （figure 归一化坐标，由 ``Text.get_window_extent(renderer)`` 得到真实外框）。

    判定口径（刻意保守，宁可漏报也不刷屏）：
      * **超出画布 = error**：客观可判定——超出 0~1 的文字一定会被裁掉；
        但 ``bbox_inches='tight'`` 的脚本例外（``tight=True``）：那种导出方式会
        按内容把画布撑大，超出 figure 框的文字**不会**被裁掉，所以这里降级为
        **提示**（CH-4：0.1.8 对 tight 脚本恒定误报 8 条 error → README 推荐的
        `check --json` CI 配方在正常项目上永远红，用户只能把检查整个关掉）；
      * **互压 = warn**：文字挨着不等于难看，所以只报"两维都重叠且面积够大"的，
        同轴同类的刻度标签之间不算（相邻刻度轻微搭边是常态）；
      * 最多报 ``TEXT_OVERLAP_REPORT`` 条互压。

    ``tight=None`` 表示"不知道"（不降级），``tight=True/False`` 由调用方判定。
    """
    problems, warns = [], []
    err = state.get('_text_error')
    if err:
        warns.append({'rule': 'text_unavailable', 'level': 'warn', 'axes': [],
                      'msg': _msg.t('chk_text_unavailable', err=err)})
        return problems, warns

    entries = []          # [(轴名 or '', 文字盒 dict)]
    for a in state.get('axes') or []:
        for t in a.get('_texts') or []:
            entries.append((_name(a), t))
    for t in state.get('_fig_texts') or []:
        entries.append(('', t))

    def label(where, t):
        kind = _kind(t.get('kind'))
        if where:
            return _msg.t('chk_text_label', where=where, kind=kind,
                          text=t.get('s'))
        return _msg.t('chk_text_label_canvas', kind=kind, text=t.get('s'))

    # ---- 1. 超出画布（tight 脚本里降级为提示，见 docstring）----
    for where, t in entries:
        b = t.get('box') or [0, 0, 0, 0]
        out = []
        if b[0] < -TEXT_EDGE_EPS:
            out.append(_msg.t('chk_edge_left', v='%.3f' % b[0]))
        if b[1] < -TEXT_EDGE_EPS:
            out.append(_msg.t('chk_edge_bottom', v='%.3f' % b[1]))
        if b[2] > 1 + TEXT_EDGE_EPS:
            out.append(_msg.t('chk_edge_right', v='%.3f' % b[2]))
        if b[3] > 1 + TEXT_EDGE_EPS:
            out.append(_msg.t('chk_edge_top', v='%.3f' % b[3]))
        if out:
            item = {
                'rule': 'text_out_of_canvas',
                'level': 'warn' if tight else 'error',
                'axes': [where] if where else [],
                'msg': _msg.t('chk_text_out_tight' if tight else 'chk_text_out',
                              label=label(where, t), edges=_join(out)),
                'text': t.get('s'), 'box': b,
            }
            if tight:
                # 让消费方能区分"真的越界"与"tight 口径下不算问题"
                item['downgraded'] = 'tight_bbox'
                warns.append(item)
            else:
                problems.append(item)

    # ---- 2. 互相压住（warn）----
    # 分两类，判据不同：
    #   a) 跨轴 / 不同类型的文字：按**重叠面积**判（标题压邻图标题、标注压刻度……）；
    #   b) 同轴同类刻度标签：按**沿刻度方向的重叠占标签尺寸比例**判 —— 这正是
    #      外部审阅说的"刻度标签特别长"（相邻标签互相吃掉一半宽度，数字就看不清了）。
    #      轻微搭边（< TICK_CROWD_RATIO）是常态，不报。
    n_rep = 0
    for i in range(len(entries)):
        if n_rep >= TEXT_OVERLAP_REPORT:
            break
        wa, ta = entries[i]
        ba = ta.get('box')
        if not ba or ta.get('kind') == 'capped':
            continue
        for j in range(i + 1, len(entries)):
            if n_rep >= TEXT_OVERLAP_REPORT:
                break
            wb, tb = entries[j]
            bb = tb.get('box')
            if not bb or tb.get('kind') == 'capped':
                continue
            dx = min(ba[2], bb[2]) - max(ba[0], bb[0])
            dy = min(ba[3], bb[3]) - max(ba[1], bb[1])
            if dx <= 0 or dy <= 0:
                continue
            ka, kb = ta.get('kind'), tb.get('kind')
            if wa == wb and ka == kb and ka in ('xtick', 'ytick'):
                # b) 同轴同类刻度：沿刻度方向的重叠比例
                if ka == 'xtick':
                    ratio = dx / max(1e-9, min(ba[2] - ba[0], bb[2] - bb[0]))
                else:
                    ratio = dy / max(1e-9, min(ba[3] - ba[1], bb[3] - bb[1]))
                if ratio < TICK_CROWD_RATIO:
                    continue
                warns.append({
                    'rule': 'tick_labels_crowded', 'level': 'warn',
                    'axes': [wa],
                    'msg': _msg.t('chk_tick_crowded', ax=wa, kind=_kind(ka),
                                  a=ta.get('s'), b=tb.get('s'),
                                  pct=ratio * 100),
                    'overlap_ratio': round(ratio, 3)})
                n_rep += 1
                continue
            # a) 跨轴 / 跨类型：按面积
            if dx * dy < TEXT_OVERLAP_MIN:
                continue
            warns.append({
                'rule': 'text_overlap', 'level': 'warn',
                'axes': sorted({x for x in (wa, wb) if x}),
                'msg': _msg.t('chk_text_overlap', a=label(wa, ta),
                              b=label(wb, tb), dx=dx, dy=dy),
                'overlap': [round(dx, 4), round(dy, 4)]})
            n_rep += 1
    return problems, warns


def analyze_state(state, tol=DEFAULT_TOL, with_text=True, tight=False):
    """``check`` 与 MCP ``check_layout`` 的共同入口：几何规则 + 文字渲染尺寸规则。

    ``tight``：脚本用 ``bbox_inches='tight'`` 导出（CH-4）——那种导出方式会按内容
    撑大画布，超出 figure 框的文字不会被裁掉，所以文字越界要降级为提示。
    """
    problems, warns = analyze(state.get('axes') or [], tol)
    if with_text:
        tp, tw = analyze_texts(state, tight=tight)
        problems += tp
        warns += tw
    return problems, warns


def _const_str(node):
    return node.value if isinstance(node, ast.Constant) \
        and isinstance(node.value, str) else None


def has_tight_bbox(script):
    """脚本是不是用 ``bbox_inches='tight'`` 导出？（CH-4）

    tight 导出会按内容把画布**撑大**：落在 figure 框外的文字不会被裁掉。0.1.8 的
    check 不知道这件事，对这类脚本恒定报 8 条 ``text_out_of_canvas`` error（与
    不用 tight 的脚本逐字相同）→ README 推荐的 `check --json` CI 配方在正常项目上
    永远红，用户只能把检查整个关掉。

    纯 AST 扫描（**不执行**脚本），认三种写法：
      * ``fig.savefig(..., bbox_inches='tight')`` / ``plt.savefig(...)``；
      * ``savefig(..., **{'bbox_inches': 'tight'})``；
      * ``plt.rcParams['savefig.bbox'] = 'tight'``（全局口径）。
    宁可漏判一种罕见写法（退回 0.1.8 的旧口径 + ``--no-tight`` 手动指定），
    也不为此去执行用户的脚本。
    """
    try:
        with open(script, 'r', encoding='utf-8', errors='replace') as f:
            src = f.read()
    except OSError:
        return False
    try:
        tree = ast.parse(src)
    except (SyntaxError, ValueError):
        return False
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg == 'bbox_inches' and _const_str(kw.value) == 'tight':
                    return True
                if kw.arg is None and isinstance(kw.value, ast.Dict):
                    for k, v in zip(kw.value.keys, kw.value.values):
                        if _const_str(k) == 'bbox_inches' \
                                and _const_str(v) == 'tight':
                            return True
        if isinstance(node, ast.Assign) and _const_str(node.value) == 'tight':
            for tgt in node.targets:
                if isinstance(tgt, ast.Subscript) \
                        and _const_str(getattr(tgt, 'slice', None)) == 'savefig.bbox':
                    return True
    return False


def static_scan(script):
    """A4：**完全不执行**脚本的静态扫描。

    只给两件事：脚本能不能解析（语法）+ 布局引擎冲突（``layoutwarn`` 的 AST 扫描）
    + 几个粗口径的 AST 统计（add_axes / subplots / savefig / figsize 字面量）。
    "对齐 / 等大 / 间距 / 字号 / 越界 / 文字重叠"这些**都要真的跑一遍脚本**才知道，
    所以这里必须明说"没检查什么"（见 ``static_note``），否则用户会以为体检过了。
    """
    info = {'parsed': False, 'error': None, 'syntax_error': None,
            'layout_engine': layoutwarn.scan(script),
            'n_add_axes': 0, 'n_subplots': 0, 'n_savefig': 0,
            'figsize_literal': False}
    try:
        with open(script, 'r', encoding='utf-8', errors='replace') as f:
            src = f.read()
    except OSError as e:
        info['error'] = '%s: %s' % (type(e).__name__, e)
        return info
    try:
        tree = ast.parse(src)
    except (SyntaxError, ValueError) as e:
        # 语法错误时报不了"哪一行"就没意义（用户就是要去改那一行）
        info['syntax_error'] = (getattr(e, 'lineno', 0) or 0,
                                getattr(e, 'msg', None) or str(e))
        info['layout_engine'] = []
        return info
    info['parsed'] = True
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        attr = getattr(node.func, 'attr', None)
        if attr == 'add_axes':
            info['n_add_axes'] += 1
        elif attr == 'subplots':
            info['n_subplots'] += 1
        elif attr == 'savefig':
            info['n_savefig'] += 1
        if attr in ('figure', 'subplots'):
            for kw in node.keywords:
                if kw.arg == 'figsize' and isinstance(kw.value, (ast.Tuple, ast.List)):
                    info['figsize_literal'] = True
    return info


def _tol_arg(v):
    """``--tol`` 的取值校验（argparse 类型回调 → 非法参数给 usage + 退出码 2）。"""
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError(_msg.t('chk_tol_invalid', tol=v))
    if not _tol_positive(f):
        raise argparse.ArgumentTypeError(_msg.t('chk_tol_invalid', tol=v))
    return f


@_msg.guard_json_main
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog='mpltweak check',
        description='排版体检：按几何规则检查对齐 / 等大 / 间距 / 字号 / 越界 / 重叠')
    ap.add_argument('script')
    ap.add_argument('--fig', type=int, default=-1, help='第几张图（0-based，默认最后一张）')
    ap.add_argument('--tol', type=_tol_arg, default=DEFAULT_TOL,
                    help='几何容差（默认 0.010；必须是有限正数）')
    ap.add_argument('--json', action='store_true', help='机器可读输出')
    ap.add_argument('--no-text', action='store_true',
                    help='跳过文字渲染尺寸检查（刻度标签/标题/标注；'
                         '默认开启，重图少等一次绘制时可用）')
    ap.add_argument('--no-tight', action='store_true',
                    help="忽略脚本里的 bbox_inches='tight'：文字越界仍按 error 判"
                         '（0.1.8 的旧口径）')
    ap.add_argument('--static', action='store_true',
                    help='只做 AST + 布局引擎冲突扫描，**完全不执行**脚本'
                         '（快，但只查这两项；输出里会明说没检查什么）')
    ap.add_argument('--strict', action='store_true',
                    help='把布局引擎冲突（constrained_layout / figure.autolayout）'
                         '也当成问题：退出码变 1，适合接进 CI')
    ap.add_argument('--lang', choices=('auto', 'zh', 'en'), default='auto',
                    help='界面语言（默认自动：--lang > MPLTWEAK_LANG > locale）')
    args = ap.parse_args(argv if argv is not None else sys.argv[1:])
    _msg.set_lang(args.lang)

    script = os.path.abspath(args.script)

    # ---- A4：--static —— 一行脚本都不执行，所以**没有** stdout 隔离问题 ----
    if args.static:
        return _run_static(script, args)

    _real = sys.stdout
    sys.stdout = sys.stderr                    # 脚本自己的 print 不许混进来
    if not os.path.exists(script):
        sys.stdout = _real
        sys.stderr.write('[check] %s\n' % _msg.t('file_not_found', path=script))
        if args.json:
            _msg.fail_json('file_not_found',
                           _msg.t_en('file_not_found', path=script))
        return _msg.EXIT_USAGE
    # A1：执行用户脚本**在子进程**里 —— 同进程 runpy 会让 sys.path / sys.modules /
    # matplotlib rcParams 跨调用串味（MCP server 是长驻的，CH-2）。
    state, err = verify.collect_isolated(script, args.fig if args.fig >= 0 else None,
                                         with_text=not args.no_text)
    sys.stdout = _real
    if state is None:
        sys.stderr.write('[check] %s\n' % _msg.t('collect_failed', err=err))
        if args.json:
            _msg.fail_json('collect_failed', err)
        return _msg.EXIT_USAGE

    axes = state.get('axes') or []
    text_checked = not args.no_text and not state.get('_text_error')
    # CH-4：tight 导出的脚本，画布会按内容扩张 —— 文字"越界"要降级为提示。
    tight = (not args.no_tight) and has_tight_bbox(script)
    problems, warns = analyze_state(state, args.tol,
                                   with_text=not args.no_text, tight=tight)
    n_texts = sum(len(a.get('_texts') or []) for a in axes) \
        + len(state.get('_fig_texts') or [])
    n_cb = len([a for a in axes if a.get('is_colorbar')])
    # 布局引擎冲突（constrained_layout / autolayout）：单独一栏报，**不进 problems**
    # —— 它不影响退出码，也不该混进"几何问题"里（外部审阅第 4 条的落地）。
    conflicts = layoutwarn.scan(script)
    # --strict：把布局引擎冲突升级成**问题**（rc=1）—— 接 CI 的用户可以这样要求
    # "别用会被引擎覆盖的布局方式"。默认仍是提示：引擎本身是 matplotlib 的正常写法，
    # 不该因为用了它就让 check 红掉（见 README / 手册里的取舍说明）。
    if args.strict and conflicts:
        for _c in conflicts:
            problems.append({
                'rule': 'layout_engine_conflict', 'level': 'error',
                'axes': [],
                'msg': _msg.t('chk_layout_conflict', line=_c.get('line'),
                              how=_c.get('how')),
            })

    if args.json:
        _msg.emit_json({
            'script': os.path.basename(script),
            'n_axes': len(axes),
            'n_colorbar': n_cb,
            'tol': args.tol,
            'ok': not problems,
            'problems': problems,
            'warnings': warns,
            'layout_engine': conflicts,
            'text_checked': text_checked,
            'n_texts': n_texts,
            'tight_bbox': tight,
        })
        return _msg.EXIT_PROBLEM if problems else _msg.EXIT_OK

    print(_msg.t('chk_header', n=len(axes),
                 cb=_msg.t('chk_cb_note', n=n_cb) if n_cb else '',
                 tol=args.tol))
    if text_checked:
        print(_msg.t('chk_text_checked', n=n_texts))
    elif args.no_text:
        print(_msg.t('chk_text_skipped'))
    if tight:
        print(_msg.t('chk_tight_note'))
    print()
    if not problems and not warns:
        print(_msg.t('chk_all_clean'))
    for p in problems:
        print(_msg.t('chk_problem_line', msg=p['msg']))
    for w in warns:
        print(_msg.t('chk_warn_line', msg=w['msg']))
    if conflicts:
        print()
        for line in layoutwarn.messages_for(conflicts):
            print(line)
    print()
    _extra = _msg.t('chk_summary_warns', n=len(warns)) if warns else ''
    if problems:
        print(_msg.t('chk_summary_problems', n=len(problems), extra=_extra))
    else:
        print(_msg.t('chk_summary_clean', extra=_extra))
    return _msg.EXIT_PROBLEM if problems else _msg.EXIT_OK


def _run_static(script, args) -> int:
    """``check --static``：只做静态扫描，**不执行**脚本（A4）。"""
    if not os.path.exists(script):
        sys.stderr.write('[check] %s\n' % _msg.t('file_not_found', path=script))
        if args.json:
            _msg.fail_json('file_not_found',
                           _msg.t_en('file_not_found', path=script))
        return _msg.EXIT_USAGE
    info = static_scan(script)
    if info.get('error'):
        sys.stderr.write('[check] %s\n' % _msg.t('collect_failed', err=info['error']))
        if args.json:
            _msg.fail_json('read_failed', info['error'])
        return _msg.EXIT_USAGE
    if info['syntax_error'] is not None:
        line, msg = info['syntax_error']
        text = _msg.t_en('chk_syntax_error', line=line, msg=msg)
        sys.stderr.write('[check] %s\n' % _msg.t('chk_syntax_error', line=line,
                                                 msg=msg))
        if args.json:
            _msg.fail_json('syntax_error', text)
        return _msg.EXIT_USAGE

    arguments = getattr(args, 'tol', DEFAULT_TOL)
    conflicts = info['layout_engine']
    problems = []
    if args.strict and conflicts:
        for _c in conflicts:
            problems.append({
                'rule': 'layout_engine_conflict', 'level': 'error', 'axes': [],
                'msg': _msg.t('chk_layout_conflict', line=_c.get('line'),
                              how=_c.get('how')),
            })
    # 明说"没检查什么"：这是 A4 的契约（对 agent 尤其重要 —— 别把静态结论
    # 当成完整体检的结论）。
    not_checked = ['alignment', 'size_mismatch', 'uneven_gap', 'font_mismatch',
                   'out_of_canvas', 'text_out_of_canvas', 'text_overlap',
                   'tick_labels_crowded', 'colorbar']
    if args.json:
        _msg.emit_json({
            'script': os.path.basename(script),
            'static': True,
            'n_axes': None,
            'n_colorbar': None,
            'tol': arguments,
            'ok': not problems,
            'problems': problems,
            'warnings': [],
            'layout_engine': conflicts,
            'text_checked': False,
            'n_texts': None,
            'checked': ['syntax', 'layout_engine'],
            'not_checked': not_checked,
            'note': _msg.t('static_note'),
            'ast': {'n_add_axes': info['n_add_axes'],
                    'n_subplots': info['n_subplots'],
                    'n_savefig': info['n_savefig'],
                    'figsize_literal': info['figsize_literal']},
        })
        return _msg.EXIT_PROBLEM if problems else _msg.EXIT_OK

    print(_msg.t('chk_static_header',
                 parsed=_msg.t('chk_static_parsed_ok'), n=len(conflicts)))
    print()
    for p in problems:
        print(_msg.t('chk_problem_line', msg=p['msg']))
    if not problems:
        print(_msg.t('chk_all_clean'))
    if conflicts:
        for line in layoutwarn.messages_for(conflicts):
            print(line)
    print()
    print(_msg.t('static_note'))
    print(_msg.t('static_ok_meaning'))
    return _msg.EXIT_PROBLEM if problems else _msg.EXIT_OK


if __name__ == '__main__':
    sys.exit(main())
