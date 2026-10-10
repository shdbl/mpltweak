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
# 文字种类 → 人话（写进提示里，用户才知道是哪儿）
_KIND_ZH = {'title': '标题', 'xlabel': 'x 轴标签', 'ylabel': 'y 轴标签',
            'xtick': 'x 刻度', 'ytick': 'y 刻度', 'text': '文字',
            'figtext': '图注', 'capped': '文字'}


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
    if not (tol > 0):
        raise ValueError('几何容差 tol 必须大于 0（收到 %r）' % (tol,))
    problems, warns = [], []
    normal = _normal(axes)
    n = len(normal)

    # ---- 1. 越界（超出画布 0~1）----
    for a in normal:
        x0, y0, w, h = _pos(a)
        out = []
        if x0 < -tol:
            out.append('左边缘 %s' % _fmt(x0))
        if y0 < -tol:
            out.append('下边缘 %s' % _fmt(y0))
        if x0 + w > 1 + tol:
            out.append('右边缘 %s' % _fmt(x0 + w))
        if y0 + h > 1 + tol:
            out.append('上边缘 %s' % _fmt(y0 + h))
        if out:
            problems.append({
                'rule': 'out_of_canvas', 'level': 'error', 'axes': [_name(a)],
                'msg': '%s 超出画布：%s' % (_name(a), '、'.join(out)),
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
                    'msg': '%s 与 %s 重叠（%.3f × %.3f）'
                           % (_name(normal[i]), _name(normal[j]), ox, oy),
                })

    # ---- 3. 边缘不齐：某轴的左边（或下边）与"最近的另一个轴"差一点点 ----
    #        差得远 = 本来就在另一列/另一行，不算问题。
    seen_pairs = set()
    for key, label in ((0, '左边缘'), (1, '下边缘')):
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
                    'msg': '%s 的%s是 %s，与相邻面板 %s 差 %s'
                           % (_name(a), label, _fmt(v),
                              '/'.join(_name(b) for b in same[:3]), _fmt(off)),
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
                    'msg': '面板大小不一：多数是 %.3f×%.3f，另有 %s'
                           % (mw * tol, mh * tol, '、'.join(odd)),
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
                'msg': '同列垂直间距不均：%s'
                       % ' / '.join(_fmt(g) for g in gaps),
            })

    # ---- 6. 字号不统一（降级为提示：有时是有意的）----
    for key, label in (('title_fontsize', '标题'), ('label_fontsize', '轴标签'),
                       ('tick_fontsize', '刻度')):
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
                        'msg': '%s字号不统一：多数 %spt，另有 %s'
                               % (label, mode, '、'.join(odd)),
                    })

    # ---- 7. 轴范围不统一（多面板常见毛病：同一组面板该共享 xlim/ylim）----
    # 只在"有明显多数派"时报（≥60% 的面板范围一致、另有少数不同）——各自为政的多面板
    # 图（每个面板画不同物理量）本来就不该统一，这种情况下不报。
    for key, label in (('xlim', 'x 轴范围'), ('ylim', 'y 轴范围')):
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
                'msg': '%s不统一：%d 个面板是 %s，另有 %s（同组面板通常应一致；'
                       '刻意放大某个面板时忽略本条）'
                       % (label, len(top), list(mode), '、'.join(odd)),
            })

    return problems, warns


def analyze_texts(state):
    """按**真实渲染尺寸**查文字：超出画布（error）/ 互相压住（warn）。

    与几何规则的分工：几何规则只看轴的位置框，**看不到文字**——所以
    "刻度标签特别长、已经戳出画布"或"标题和邻图标题压在一起"这类问题它一律漏报。
    这里的输入是 ``verify._state(..., with_text=True)`` 采集的文字盒
    （figure 归一化坐标，由 ``Text.get_window_extent(renderer)`` 得到真实外框）。

    判定口径（刻意保守，宁可漏报也不刷屏）：
      * **超出画布 = error**：客观可判定——超出 0~1 的文字一定会被裁掉；
      * **互压 = warn**：文字挨着不等于难看，所以只报"两维都重叠且面积够大"的，
        同轴同类的刻度标签之间不算（相邻刻度轻微搭边是常态）；
      * 最多报 ``TEXT_OVERLAP_REPORT`` 条互压。
    """
    problems, warns = [], []
    err = state.get('_text_error')
    if err:
        warns.append({'rule': 'text_unavailable', 'level': 'warn', 'axes': [],
                      'msg': '文字尺寸检测不可用（%s），本次只做几何检查' % err})
        return problems, warns

    entries = []          # [(轴名 or '', 文字盒 dict)]
    for a in state.get('axes') or []:
        for t in a.get('_texts') or []:
            entries.append((_name(a), t))
    for t in state.get('_fig_texts') or []:
        entries.append(('', t))

    def label(where, t):
        return '%s 的%s「%s」' % (where or '画布', _KIND_ZH.get(t.get('kind'),
                                                          '文字'), t.get('s'))

    # ---- 1. 超出画布（error）----
    for where, t in entries:
        b = t.get('box') or [0, 0, 0, 0]
        out = []
        if b[0] < -TEXT_EDGE_EPS:
            out.append('左 %.3f' % b[0])
        if b[1] < -TEXT_EDGE_EPS:
            out.append('下 %.3f' % b[1])
        if b[2] > 1 + TEXT_EDGE_EPS:
            out.append('右 %.3f' % b[2])
        if b[3] > 1 + TEXT_EDGE_EPS:
            out.append('上 %.3f' % b[3])
        if out:
            problems.append({
                'rule': 'text_out_of_canvas', 'level': 'error',
                'axes': [where] if where else [],
                'msg': '%s 超出画布（%s），这段文字会被裁掉'
                       % (label(where, t), '、'.join(out)),
                'text': t.get('s'), 'box': b})

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
                    'msg': '%s 的%s太密：「%s」与「%s」有 %.0f%% 的宽度重叠，'
                           '数字会看不清（抽稀刻度或缩小字号）'
                           % (wa, _KIND_ZH.get(ka, '刻度'), ta.get('s'),
                              tb.get('s'), ratio * 100),
                    'overlap_ratio': round(ratio, 3)})
                n_rep += 1
                continue
            # a) 跨轴 / 跨类型：按面积
            if dx * dy < TEXT_OVERLAP_MIN:
                continue
            warns.append({
                'rule': 'text_overlap', 'level': 'warn',
                'axes': sorted({x for x in (wa, wb) if x}),
                'msg': '%s 与 %s 压在一起（重叠 %.3f×%.3f）'
                       % (label(wa, ta), label(wb, tb), dx, dy),
                'overlap': [round(dx, 4), round(dy, 4)]})
            n_rep += 1
    return problems, warns


def analyze_state(state, tol=DEFAULT_TOL, with_text=True):
    """``check`` 与 MCP ``check_layout`` 的共同入口：几何规则 + 文字渲染尺寸规则。"""
    problems, warns = analyze(state.get('axes') or [], tol)
    if with_text:
        tp, tw = analyze_texts(state)
        problems += tp
        warns += tw
    return problems, warns


@_msg.guard_json_main
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog='mpltweak check',
        description='排版体检：按几何规则检查对齐 / 等大 / 间距 / 字号 / 越界 / 重叠')
    ap.add_argument('script')
    ap.add_argument('--fig', type=int, default=-1, help='第几张图（0-based，默认最后一张）')
    ap.add_argument('--tol', type=float, default=DEFAULT_TOL, help='几何容差（默认 0.010）')
    ap.add_argument('--json', action='store_true', help='机器可读输出')
    ap.add_argument('--no-text', action='store_true',
                    help='跳过文字渲染尺寸检查（刻度标签/标题/标注；'
                         '默认开启，重图少等一次绘制时可用）')
    ap.add_argument('--strict', action='store_true',
                    help='把布局引擎冲突（constrained_layout / figure.autolayout）'
                         '也当成问题：退出码变 1，适合接进 CI')
    args = ap.parse_args(argv if argv is not None else sys.argv[1:])

    _real = sys.stdout
    sys.stdout = sys.stderr                    # 脚本自己的 print 不许混进来

    script = os.path.abspath(args.script)
    if not os.path.exists(script):
        sys.stdout = _real
        sys.stderr.write('[check] 找不到脚本: %s\n' % script)
        if args.json:
            _msg.fail_json('file_not_found', 'script not found: %s' % script)
        return 2
    state, err = verify.collect(script, args.fig if args.fig >= 0 else None,
                                with_text=not args.no_text)
    if state is None:
        sys.stdout = _real
        sys.stderr.write('[check] 采集失败: %s\n' % err)
        if args.json:
            _msg.fail_json('collect_failed', err)
        return 2

    axes = state.get('axes') or []
    text_checked = not args.no_text and not state.get('_text_error')
    problems, warns = analyze_state(state, args.tol,
                                    with_text=not args.no_text)
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
                'msg': '第 %d 行开了 %s：布局引擎会在每次绘制时重算轴位置，'
                       '写回的位置会被覆盖（--strict 视为问题）'
                       % (_c.get('line'), _c.get('how')),
            })

    if args.json:
        sys.stdout = _real
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
        })
        return 1 if problems else 0

    sys.stdout = _real
    print('检查 %d 个轴%s，容差 %.3f'
          % (len(axes), '（其中 %d 个 colorbar 不参与几何比对）' % n_cb
             if n_cb else '', args.tol))
    if text_checked:
        print('文字 %d 处已按**真实渲染尺寸**检查（超出画布 → 问题；互相压住 → 提示）'
              % n_texts)
    elif args.no_text:
        print('（已用 --no-text 跳过文字尺寸检查）')
    print()
    if not problems and not warns:
        print('√ 没发现问题：对齐 / 等大 / 间距 / 字号 / 边界都正常')
    for p in problems:
        print('× %s' % p['msg'])
    for w in warns:
        print('· %s（若是有意为之可忽略）' % w['msg'])
    if conflicts:
        print()
        for line in layoutwarn.messages_for(conflicts):
            print(line)
    print()
    if problems:
        print('%d 个问题%s' % (len(problems),
                             '，%d 条提示' % len(warns) if warns else ''))
    else:
        print('没发现问题%s' % ('，%d 条提示' % len(warns) if warns else ''))
    return 1 if problems else 0


if __name__ == '__main__':
    sys.exit(main())
