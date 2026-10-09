# -*- coding: utf-8 -*-
"""
mpltweak apply —— 「落实」助手（CLI：mpltweak apply <脚本.py>）
=====================================================================

把 .tweak_params/*.json 变成改动清单 / 纯 matplotlib 片段，或**确定性写回脚本**：

  mpltweak apply <脚本.py>                     # 改动清单（默认，只读不改）
  mpltweak apply <脚本.py> --snippet           # 额外输出可粘贴片段
  mpltweak apply <脚本.py> --write             # AST 确定性写回（见 mpltweak.writeback）
  mpltweak apply <脚本.py> --write --no-verify # 写回但跳过 Agg 重跑验证
  mpltweak apply <脚本.py> --params X          # 指定参数文件

它默认**不改脚本**（只读 + 只打印）：落实交给 AI 按脚本自身风格完成；
需要机械插入时用 ``--write``（AST 零 LLM，失败回退结构化提示）。
"""

from __future__ import annotations

import argparse
import os
import sys

from . import params
from . import writeback


def _fmt(v):
    return '%.4f' % v


def _num(v):
    '''数值字面量：整数不带小数点，浮点保留 6 位有效数字。'''
    try:
        f = float(v)
    except (TypeError, ValueError):
        return repr(v)
    if f == int(f) and abs(f) < 1e15:
        return '%d' % int(f)
    return '%.6g' % f


def _print_changes(script, data, p):
    axes = data.get('axes', [])
    print('参数文件: %s' % p)
    print('脚本: %s' % os.path.basename(script))
    print('画布: figsize_in=%s  (px=%s)'
          % (data.get('figsize_in'), data.get('figsize_px')))
    print('-' * 62)
    for a in axes:
        if not isinstance(a, dict):                # L1：参数文件里有脏轴项 → 不崩溃
            print('  !! 参数文件里有一个非对象轴项，已跳过: %r' % (a,))
            continue
        i = a.get('index')
        pos = a.get('pos')
        print('ax%-2d 位置 [%s]' % (i, ', '.join(_fmt(v) for v in pos)))
        if a.get('title_fontsize') is not None or a.get('label_fontsize') is not None:
            print('      字号 title=%s label=%s tick=%s'
                  % (a.get('title_fontsize'), a.get('label_fontsize'),
                     a.get('tick_fontsize')))
        if a.get('clim') is not None:
            print('      clim=%s' % a.get('clim'))
        if a.get('cmap'):
            print('      colormap=%s' % a.get('cmap'))
        if a.get('grid') is not None:
            print('      grid=%s  spines=%s' % (a.get('grid'), a.get('spines')))
        if a.get('xscale') or a.get('yscale'):
            print('      scale x=%s y=%s' % (a.get('xscale'), a.get('yscale')))
        for line in a.get('lines', []):
            print('      line%d linewidth=%s color=%s'
                  % (line.get('index'), line.get('linewidth'), line.get('color')))
        lg = a.get('legend')
        if lg:
            loc = lg.get('loc') or '（自由锚点）'
            print('      图例 loc=%s fontsize=%s' % (loc, lg.get('fontsize')))


def _print_snippet(data):
    print('# --- 纯 matplotlib 落实片段（放在 tight_layout() 之后）---')
    axes = data.get('axes', [])
    print('for _ax, _p in zip(fig.axes, [')
    for a in axes:
        print('    [%s],' % ', '.join(_fmt(v) for v in a.get('pos')))
    print(']):')
    print('    _ax.set_position(_p)')
    fs = data.get('figsize_in')
    if fs:
        print('# 画布: figsize=(%s, %s)' % (fs[0], fs[1]))
    # 属性片段：clim / cmap / grid / spines / scale / 每根线
    attr_blocks = []
    for a in axes:
        i = a.get('index')
        rows = []
        is_cb = a.get('is_colorbar')
        if is_cb is None:
            is_cb = a.get('clim') is not None      # 旧参数文件兼容
        if is_cb:
            rows.append('# ax%d 是 colorbar 轴：先解除自动定位，否则重绘会重置位置/宽度' % i)
            rows.append('fig.axes[%d].set_axes_locator(None)' % i)
            rows.append('fig.axes[%d].set_box_aspect(None)' % i)
        if a.get('clim') is not None:
            rows.append('# clim：_mappable / _cb 换成你脚本里的变量名')
            rows.append('# _mappable.set_clim(%s, %s)'
                        % (_num(a['clim'][0]), _num(a['clim'][1])))
            rows.append('# _cb.update_normal(_mappable)   # Colorbar 自己没有 set_clim')
        if a.get('cmap'):
            rows.append('# %s.set_cmap(%r)'
                        % ('_mappable' if is_cb else 'fig.axes[%d] 的 mappable' % i,
                           a['cmap']))
        if a.get('grid') is not None:
            rows.append('fig.axes[%d].grid(%s)' % (i, bool(a['grid'])))
        for k, vis in (a.get('spines') or {}).items():
            if not vis:
                rows.append('fig.axes[%d].spines[%r].set_visible(False)' % (i, k))
        if a.get('xscale'):
            rows.append('fig.axes[%d].set_xscale(%r)' % (i, a['xscale']))
        if a.get('yscale'):
            rows.append('fig.axes[%d].set_yscale(%r)' % (i, a['yscale']))
        for line in a.get('lines', []):
            j = line.get('index')
            if line.get('linewidth') is not None:
                rows.append('fig.axes[%d].lines[%d].set_linewidth(%s)'
                            % (i, j, _num(line['linewidth'])))
            if line.get('color'):
                rows.append('fig.axes[%d].lines[%d].set_color(%r)' % (i, j, line['color']))
        if rows:
            attr_blocks.append((i, rows))
    if attr_blocks:
        print('# ---- 属性（#8）：逐条按需粘贴 ----')
        for i, rows in attr_blocks:
            print('# ax%d' % i)
            for r in rows:
                print(r)


def _all_params_files(script, override=None):
    """收集脚本的全部参数文件（主文件 + ``<stem>.fig<k>.json``），按图号排序。

    一次会话改多张图时每张各留一份（文件名带图号）→ 这里全收上来逐张落实。
    显式 ``--params`` 优先级最高：给了就只用它。
    """
    if override:
        p = params.params_path(script, override)
        return [p] if os.path.exists(p) else []
    primary = params.params_path(script)
    stem = os.path.splitext(primary)[0]
    d, base = os.path.dirname(stem), os.path.basename(stem)
    order = []
    if os.path.isdir(d):
        for fn in sorted(os.listdir(d)):
            if (fn.startswith(base + '.fig') and fn.endswith('.json')
                    and not fn.endswith('.writeback.json')):
                order.append((fn, os.path.join(d, fn)))
    if os.path.exists(primary):
        order.append((base + '.json', primary))

    # 按 fig_index 去重：带图号的文件排在前面（`.fig0.json` < `.json`），
    # 所以同一个图号优先用 <stem>.fig<k>.json（主文件只是它的副本）。
    by_idx, fallback = {}, []
    for _fn, pth in order:
        try:
            k = params.load(pth).get('fig_index')
        except Exception:                        # noqa: BLE001
            k = None
        if isinstance(k, int):
            by_idx.setdefault(k, pth)
        else:
            fallback.append(pth)
    return [by_idx[k] for k in sorted(by_idx)] + fallback


def main(argv=None):
    ap = argparse.ArgumentParser(prog='mpltweak apply')
    ap.add_argument('script')
    ap.add_argument('--params', default=None)
    ap.add_argument('--snippet', action='store_true',
                    help='额外打印可直接粘贴的纯 matplotlib 片段')
    ap.add_argument('--write', action='store_true',
                    help='AST 确定性写回脚本（零 LLM）')
    ap.add_argument('--no-verify', action='store_true',
                    help='写回后跳过 Agg 无头重跑验证')
    ap.add_argument('--no-semantic', action='store_true',
                    help='跳过语义验证（默认会重跑并比对目标图的位置/字号等是否真的等于参数）')
    ap.add_argument('--all-figs', action='store_true',
                    help='循环出图时统一应用到所有迭代（默认只对参数记录的图号加 if 守卫）')
    ap.add_argument('--style', default='inplace', choices=('inplace', 'block'),
                    help='写回方式：inplace=直接改原代码数字（默认，不加调整块）；'
                         'block=插调整块（旧方式，原位改不了时用）')
    ap.add_argument('--python', default=None,
                    help='验证用解释器（默认当前进程的解释器）')
    ap.add_argument('--timeout', type=float, default=300,
                    help='验证重跑超时秒数（默认 300）')
    ap.add_argument('--dry-run', action='store_true',
                    help='只生成调整块预览，不落盘（调试用）')
    args = ap.parse_args(argv)

    script = os.path.abspath(args.script)
    files = _all_params_files(script, args.params)
    if not files:
        print('no params: %s' % params.params_path(script, args.params))
        return 2
    if len(files) > 1:
        print('发现 %d 份参数文件（一次会话改过多张图）——逐张落实：' % len(files))

    results = []
    for p in files:
        try:
            data = params.load(p)
        except OSError as e:
            print('!! 跳过 %s: %s' % (os.path.basename(p), e))
            continue
        k = data.get('fig_index')
        print('=' * 62)
        print('# %s%s' % (os.path.basename(p),
                          '' if not isinstance(k, int)
                          else '  （第 %d 张图）' % (k + 1)))
        for prob in params.validate(data):
            print('!! 参数文件结构警告: %s' % prob)

        _print_changes(script, data, p)

        if not args.write:
            if args.snippet:
                print('-' * 62)
                _print_snippet(data)
            continue

        print('-' * 62)
        res = writeback.writeback(script, data, p,
                                  verify=not args.no_verify,
                                  python=args.python,
                                  timeout=args.timeout,
                                  dry_run=args.dry_run,
                                  semantic=not args.no_semantic,
                                  only_fig=not args.all_figs,
                                  style=args.style)
        results.append((p, res))
        if res['reason'] in ('ok', 'best_effort'):
            if res.get('style') == 'inplace':
                ch = res.get('changes') or []
                print('✓ 已原位写回: %s' % script)
                print('  原位修改 %d 处: %s' % (len(ch), ', '.join(ch)[:160]))
            else:
                print('✓ 已写回: %s' % script)
                print('  fig 变量名 = %s   插入点 = %s   figsize 原位替换 = %s'
                      % (res.get('fig_var'), res.get('anchor'),
                         res.get('figsize_edited')))
            if res.get('backup'):
                print('  备份: %s' % res['backup'])
            if res.get('verified') is True:
                print('  ✓ Agg 重跑验证通过')
            elif res.get('verified') is False:
                print('  ✗ Agg 重跑验证失败（已回滚到备份）')
            if res.get('semantic') is True:
                print('  ✓ 语义验证通过（目标图状态 == 参数）')
            elif res.get('semantic') is None and not args.no_semantic:
                print('  · 语义验证未能判定（脚本不存图/跑不通），仅按"能跑通"判定')
            for w in res.get('warnings', []):
                print('  · 保持原样: %s' % w)
            if args.dry_run:
                print('  [dry-run] 未落盘；%s：' % ('原位修改预览'
                      if res.get('style') == 'inplace' else '生成的调整块'))
                print(res['block'])
        elif res['reason'] == 'no_change':
            print('· 参数与原代码一致，无需改动')
            for w in res.get('warnings', []):
                print('  · 保持原样: %s' % w)
        elif res['reason'] == 'no_fig':
            print('✗ 无法确定性写回: %s' % res['err'])
            print('  建议：由 AI 按脚本风格落实，或 --snippet 拿片段手动粘贴')
        else:
            print('✗ 写回失败[%s]: %s' % (res['reason'], res['err']))

    if not args.write:
        print('-' * 62)
        print('提示：以上数值需按脚本自身风格写回；colorbar 轴若改了位置，必须同时')
        print('      cb.ax.set_box_aspect(None)，否则 matplotlib 每次重绘会把宽度重置回自动值。')
        print('      机械插入可用 --write（AST 确定性写回，零 LLM）。')
        return 0

    bad = [r for _, r in results
           if r['reason'] not in ('ok', 'best_effort', 'no_change')]
    if len(results) > 1:
        print('=' * 62)
        print('多图落实小结：共 %d 张，成功 %d，失败 %d'
              % (len(results), len(results) - len(bad), len(bad)))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
