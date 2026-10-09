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
import json
import os
import sys

from . import verify

# 几何比对的容差
DEFAULT_TOL = 0.010
# 两个轴若"近到 0.2 以内"就认为它们本来该对齐（否则是分列/分区，不算不齐）
ALIGN_NEAR = 0.20


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
    """返回 (problems, warnings)。每条 = dict(rule/level/axes/msg/detail)。"""
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
        if mc >= 2 and mc >= n / 2:                 # 有"主流尺寸"才谈得上不一致
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

    return problems, warns


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog='mpltweak check',
        description='排版体检：按几何规则检查对齐 / 等大 / 间距 / 字号 / 越界 / 重叠')
    ap.add_argument('script')
    ap.add_argument('--fig', type=int, default=-1, help='第几张图（0-based，默认最后一张）')
    ap.add_argument('--tol', type=float, default=DEFAULT_TOL, help='几何容差（默认 0.010）')
    ap.add_argument('--json', action='store_true', help='机器可读输出')
    args = ap.parse_args(argv if argv is not None else sys.argv[1:])

    _real = sys.stdout
    sys.stdout = sys.stderr                    # 脚本自己的 print 不许混进来

    script = os.path.abspath(args.script)
    if not os.path.exists(script):
        sys.stdout = _real
        sys.stderr.write('[check] 找不到脚本: %s\n' % script)
        return 2
    state, err = verify.collect(script, args.fig if args.fig >= 0 else None)
    if state is None:
        sys.stdout = _real
        sys.stderr.write('[check] 采集失败: %s\n' % err)
        return 2

    axes = state.get('axes') or []
    problems, warns = analyze(axes, args.tol)
    n_cb = len([a for a in axes if a.get('is_colorbar')])

    if args.json:
        sys.stdout = _real
        print(json.dumps({
            'script': os.path.basename(script),
            'n_axes': len(axes),
            'n_colorbar': n_cb,
            'tol': args.tol,
            'ok': not problems,
            'problems': problems,
            'warnings': warns,
        }, ensure_ascii=False, indent=2))
        return 1 if problems else 0

    sys.stdout = _real
    print('检查 %d 个轴%s，容差 %.3f'
          % (len(axes), '（其中 %d 个 colorbar 不参与几何比对）' % n_cb
             if n_cb else '', args.tol))
    print()
    if not problems and not warns:
        print('√ 没发现问题：对齐 / 等大 / 间距 / 字号 / 边界都正常')
    for p in problems:
        print('× %s' % p['msg'])
    for w in warns:
        print('· %s（若是有意为之可忽略）' % w['msg'])
    print()
    if problems:
        print('%d 个问题%s' % (len(problems),
                             '，%d 条提示' % len(warns) if warns else ''))
    else:
        print('没发现问题%s' % ('，%d 条提示' % len(warns) if warns else ''))
    return 1 if problems else 0


if __name__ == '__main__':
    sys.exit(main())
