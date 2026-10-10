# -*- coding: utf-8 -*-
"""
mpltweak apply —— 「落实」助手（CLI：mpltweak apply <脚本.py>）
=====================================================================

把 .tweak_params/*.json 变成改动清单 / 纯 matplotlib 片段，或**确定性写回脚本**：

  mpltweak apply <脚本.py>                     # 改动清单（默认，只读不改）
  mpltweak apply <脚本.py> --snippet           # 额外输出可粘贴片段
  mpltweak apply <脚本.py> --write             # AST 确定性写回（见 mpltweak.writeback）
  mpltweak apply <脚本.py> --write --timeout 600   # 写回；脚本跑得久就放宽验证等待
  mpltweak apply <脚本.py> --write --no-verify     # 完全跳过重跑验证（安全网，慎用）
  mpltweak apply <脚本.py> --params X          # 指定参数文件

它默认**不改脚本**（只读 + 只打印）：落实交给 AI 按脚本自身风格完成；
需要机械插入时用 ``--write``（AST 零 LLM，失败回退结构化提示）。
"""

from __future__ import annotations

import argparse
import os
import sys

from . import layoutwarn
from . import messages as _msg
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
        if pos is None:
            # 轴项缺 pos 是**通过官方 schema 校验的合法输入**（pos 不是必填）；
            # 原先直接 `for v in None` 会抛 TypeError，且 --json 下 stdout 为空。
            print('ax%-2d 位置（参数里没给，保持原样）' % i)
        else:
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


def _inplace_fell_flat(res):
    """原位写回是否"什么都没落到代码里"——auto 模式据此决定要不要改用块模式。

    判据：
      no_change   = 参数与原代码一致（代码里根本没有任何可改的数字）
      best_effort = 有目标值，但一个字段都没能落到代码里（典型：位置来自
                    plt.subplots() / plt.subplot(1,2,n) / GridSpec 的网格轴）
      fail        = 验证没过、已回滚（块模式换锚点的策略不同，值得再试一次）
    """
    # 注意 best_effort 要区别对待：它既可能是"一个字段都没改到"（该退到块模式），
    # 也可能是"改到了一部分、其余字段代码里没有对应写法"（不该退 —— 那会把已经
    # 干净改好的位置也变成插块）。所以只有"没有任何改动落地"才算真的落空。
    if res.get('reason') in ('no_change', 'fail'):
        return True
    return not res.get('changes')


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


def _emit_json(script, results, stdout_ref, ok=None, error=None):
    """--json 的机器可读输出（每个 return 分支前都要调用一次）。

    ``ok`` 可显式覆盖：没有参数文件时 results 是空的，``not []`` 会算成 True，
    而退出码却是 2 —— 退出码与 ok 字段语义打架（t6-M7）。
    """
    sys.stdout = stdout_ref
    _ok = (not [r for _, r in results
                if r['reason'] not in ('ok', 'best_effort', 'no_change',
                                       'preview')]) if ok is None else ok
    _payload = {
        'script': os.path.basename(script),
        'ok': _ok,
        'files': [
            {'params': os.path.basename(p),
             'reason': r.get('reason', 'unknown'),
             'style': r.get('style'),
             'changes': r.get('changes') or [],
             'backup': r.get('backup'),
             'verified': r.get('verified'),
             'verify_mode': r.get('verify_mode'),
             'semantic': r.get('semantic'),
             'warnings': r.get('warnings') or [],
             'layout_conflicts': r.get('layout_conflicts') or []}
            for p, r in results],
    }
    if error:
        _payload['error'] = error
    _msg.emit_json(_payload)


@_msg.guard_json_main
def main(argv=None):
    ap = argparse.ArgumentParser(prog='mpltweak apply')
    ap.add_argument('script')
    ap.add_argument('--params', default=None)
    ap.add_argument('--snippet', action='store_true',
                    help='额外打印可直接粘贴的纯 matplotlib 片段')
    ap.add_argument('--write', action='store_true',
                    help='AST 确定性写回脚本（零 LLM）')
    ap.add_argument('--no-verify', action='store_true',
                    help='写回后跳过 Agg 无头重跑验证（注意：这是写坏代码时唯一的自动'
                         '安全网，仅在脚本确实无法无头重跑时使用；用前请先提交到版本控制）')
    ap.add_argument('--no-semantic', action='store_true',
                    help='跳过语义验证（默认会重跑并比对目标图的位置/字号等是否真的等于参数）')
    ap.add_argument('--verify-fast', action='store_true',
                    help='快速验证档：只做语法检查（**不重跑脚本**）—— 坏语法会当场回滚，'
                         '但"能跑通"和"布局真的落到目标图上"都不保证。'
                         '适合脚本重跑很贵、且你先要快速看一眼结果的场景')
    ap.add_argument('--strict', action='store_true',
                    help='遇到布局引擎冲突（constrained_layout / autolayout）拒绝写回：'
                         '引擎会在每次绘制时重算轴位置，写回的位置会被它覆盖。'
                         '确要写回加 --allow-layout-conflict')
    ap.add_argument('--all-figs', action='store_true',
                    help='循环出图时统一应用到所有迭代（默认只对参数记录的图号加 if 守卫）')
    ap.add_argument('--allow-layout-conflict', action='store_true',
                    help='与 --strict 配对：明知有布局引擎冲突也照样写回')
    ap.add_argument('--style', default='auto', choices=('auto', 'inplace', 'block'),
                    help='写回方式：auto=每张图各自决定（默认，能改代码里已有的数字就改，'
                         '改不了才插入调整块）；inplace=只用原位改数字（改不了就保持原样）；'
                         'block=一律插入调整块')
    ap.add_argument('--python', default=None,
                    help='验证用解释器（默认当前进程的解释器）')
    ap.add_argument('--timeout', type=float, default=300,
                    help='验证重跑超时秒数（默认 300）')
    ap.add_argument('--dry-run', action='store_true',
                    help='只生成调整块预览，不落盘（调试用）')
    ap.add_argument('--json', action='store_true',
                    help='以 JSON 输出结果（给 agent / 脚本消费；过程信息改走 stderr）')
    ap.add_argument('--force', action='store_true',
                    help='即便脚本在参数保存之后被改过，也照样套用参数（默认会跳过，'
                         '以免静默覆盖你的手动改动）')
    ap.add_argument('--lang', default=None, choices=('auto', 'zh', 'en'),
                    help='界面语言：auto=跟随系统（默认），或强制 zh / en。'
                         '--json 的错误码始终是英文，不受此影响')
    args = ap.parse_args(argv)

    _msg.set_lang(args.lang)

    # --json：人读的过程信息全部走 stderr，stdout 只留最后那一段 JSON
    _stdout = sys.stdout
    if args.json:
        sys.stdout = sys.stderr

    if args.no_verify and args.verify_fast:
        # 参数互斥错误也**必须**在 --json 下给出可解析 JSON（与下面的 --strict 拒绝、
        # 以及 messages.fail_json 的约定一致）。这段原先在 stdout 交换**之前**就 return，
        # 于是 --json 时 stdout 是空的（独立审阅 F4）。
        _err = 'no_verify and verify_fast are mutually exclusive'
        if args.json:
            _emit_json(os.path.abspath(args.script), [], _stdout, ok=False,
                       error=_err)
        sys.stderr.write('[apply] --no-verify 与 --verify-fast 不能同时用：\n'
                         '  --no-verify   = 完全不做检查（连语法都不查）；\n'
                         '  --verify-fast = 只做语法检查。二选一。\n')
        return 2

    script = os.path.abspath(args.script)
    # 布局引擎冲突（constrained_layout / figure.autolayout）：位置写回会被引擎在每次
    # 绘制时覆盖。只扫一次，逐份参数文件复用到 res['layout_conflicts']。
    _conflicts = layoutwarn.messages_for(layoutwarn.scan(script))
    if _conflicts and args.strict and args.write and not args.allow_layout_conflict:
        # --strict：布局引擎会在**每次绘制**时重算轴位置，写回的位置/尺寸会被它覆盖，
        # 所以拒绝写回并给出可操作的办法（关掉引擎 / 用 add_axes 定位 / 显式放行）。
        if args.json:
            _emit_json(script, [], _stdout, ok=False,
                       error='layout conflict (use --allow-layout-conflict to override)')
        for _line in _conflicts:
            sys.stderr.write('[apply] %s\n' % _line)
        sys.stderr.write('[apply] --strict：检测到布局引擎冲突，已拒绝写回。'
                         '要照样写回请加 --allow-layout-conflict\n')
        return 2
    files = _all_params_files(script, args.params)
    if not files:
        _hint = 'no params: %s' % params.params_path(script, args.params)
        print(_hint)
        if args.json:
            _emit_json(script, [], _stdout, ok=False, error=_hint)
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
        # 参数比脚本旧 → 脚本在保存参数之后被手动改过，套用旧参数会**静默覆盖**
        # 那些改动（对照 launch.py 的同类保护；t3-M5 / t6-M8）。
        # 只读预览不拦（它本来就不写文件）。
        if args.write and not args.force and not args.dry_run:
            try:
                _stale = os.path.getmtime(script) > os.path.getmtime(p) + 1.0
            except OSError:
                _stale = False
            if _stale:
                print('!! 跳过 %s：脚本在参数保存之后被修改过（套用旧参数会覆盖你'
                      '手改的内容）；确要套用请加 --force' % os.path.basename(p))
                results.append((p, {'reason': 'stale', 'changes': [],
                                    'block': None, 'backup': None,
                                    'warnings': []}))
                continue
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
            # 只读预览也计入结果（给 --json）。`style` 填**请求的模式**：
            # 预览阶段还没决定"这张图最后会不会退到块"，用 'auto' 如实表达"待定"；
            # 之前这里不带 style → 预览时 JSON 里 style=null，与写回时的取值口径不一致。
            results.append((p, {'reason': 'preview', 'style': args.style}))
            continue

        print('-' * 62)
        if not args.no_verify and not args.dry_run:
            # 验证是"子进程重跑整个脚本"：读数据的脚本可能要几分钟，而**期间零输出**，
            # 看起来像卡死（t6-M12）。先给一行预期，至少知道它在干什么。
            print('  正在验证（子进程重跑脚本，最长 %gs；不想等可加 --no-verify）'
                  % args.timeout)
        # --verify-fast **隐含** --no-verify：快速档要的就是"不重跑"。
        # 若 verify 仍为 True，writeback 里 `if fast_check and not verify` 的分支
        # 根本进不去（实测踩过：加了 --verify-fast 却照旧跑了完整验证）。
        _kw = dict(verify=not args.no_verify and not args.verify_fast,
                   python=args.python,
                   timeout=args.timeout, dry_run=args.dry_run,
                   semantic=not args.no_semantic, only_fig=not args.all_figs,
                   fast_check=bool(args.verify_fast))
        _lk = None
        if args.write and not args.dry_run:
            # 串行化写回：并发跑两个 apply --write 时两边都基于同一份原文算替换，
            # 后写者会盖掉先写者，而且都会打印"✓ 已写回"（t5-S4 实测 7/7）。
            try:
                _lk = writeback.script_lock(script)
                _lk.__enter__()
            except RuntimeError as _e:
                print('!! %s' % _e)
                _lk = None
                res = {'reason': 'fail', 'err': str(_e), 'changes': [],
                       'block': None, 'backup': None, 'warnings': []}
                results.append((p, res))
                continue
        try:
            # auto（默认）：先按"原位改代码里的数字"试一次；若这张图什么都没落地
            # （典型：位置来自 plt.subplots()/plt.subplot(1,2,n)/GridSpec，源码里
            # 根本没有可改的数字），再改用插入调整块重试。
            # 这样同一个脚本里"能干净改的图"和"只能插块的图"各得其所，而不是因为
            # 少数几张图就把整份脚本都变成插块。
            # 注意：--style 显式指定时必须直接用那个模式，不要先试 inplace。
            _first = 'inplace' if args.style == 'auto' else args.style
            res = writeback.writeback(script, data, p, style=_first, **_kw)
            if args.style == 'auto' and _inplace_fell_flat(res):
                res = writeback.writeback(script, data, p, style='block', **_kw)
                res['auto_fallback'] = True
        finally:
            if _lk is not None:
                _lk.__exit__(None, None, None)
        if _conflicts and res.get('reason') in ('ok', 'best_effort', 'preview'):
            # 只在**这次确实落地/确实有计划**时才提示（no_change / fail / no_fig 不提示，
            # 否则是跟当前动作无关的噪音）。
            # 注意别用 `res['changes']` 判：**块模式**的 changes 是空的（它插的是块，
            # 不是逐字段原位替换），而"块 + 布局引擎"恰恰是最该提示的组合 ——
            # 2026-10-10 端到端冒烟发现漏提示，这里按 reason 判。
            res['layout_conflicts'] = _conflicts
        results.append((p, res))
        if res['reason'] in ('ok', 'best_effort'):
            if res.get('style') == 'inplace':
                ch = res.get('changes') or []
                print(_msg.t('written_inplace', script=script))
                print(_msg.t('written_inplace_count', n=len(ch),
                             items=', '.join(ch)[:160]))
            else:
                print(_msg.t('written_block', script=script))
                if res.get('auto_fallback'):
                    print(_msg.t('fallback_to_block'))
                print(_msg.t('block_info', fig_var=res.get('fig_var'),
                             anchor=res.get('anchor'),
                             figsize=res.get('figsize_edited')))
            for _line in res.get('layout_conflicts', []):
                print(_line)
            if res.get('backup'):
                print(_msg.t('backup_at', path=res['backup']))
            if res.get('verify_mode') == 'fast':
                print(_msg.t('verify_fast_note'))
            if res.get('verified') is True:
                print(_msg.t('verify_run_ok'))
            elif res.get('verified') is False:
                print(_msg.t('verify_run_fail'))
            if res.get('semantic') is True:
                print(_msg.t('semantic_ok'))
            elif res.get('semantic') is None and not args.no_semantic:
                print(_msg.t('semantic_unknown'))
            for w in res.get('warnings', []):
                print(_msg.t('kept_as_is', msg=w))
            if args.dry_run:
                print(_msg.t('dry_run_inplace'
                             if res.get('style') == 'inplace'
                             else 'dry_run_block'))
                print(res['block'])
            # 写回成功 → 刷新参数文件的修改时间。此刻"参数 == 代码"，重新开窗
            # 应当能正常接着上次调；而如果之后你手动改了脚本，脚本就会比它新，
            # 下次开窗便会拒绝套用旧参数（见 launch.py 的 mtime 校验）。
            if not args.dry_run:
                try:
                    os.utime(p, None)
                except OSError:
                    pass
        elif res['reason'] == 'no_change':
            print(_msg.t('no_change'))
            for w in res.get('warnings', []):
                print(_msg.t('kept_as_is', msg=w))
        elif res['reason'] == 'no_fig':
            print(_msg.t('no_fig', err=res['err']))
            print(_msg.t('no_fig_hint'))
        else:
            print(_msg.t('write_failed', reason=res['reason'], err=res['err']))

    if not args.write:
        print('-' * 62)
        print('提示：以上数值需按脚本自身风格写回；colorbar 轴若改了位置，必须同时')
        print('      cb.ax.set_box_aspect(None)，否则 matplotlib 每次重绘会把宽度重置回自动值。')
        print('      机械插入可用 --write（AST 确定性写回，零 LLM）。')
        if args.json:
            _emit_json(script, results, _stdout)
        return 0

    bad = [r for _, r in results
           if r['reason'] not in ('ok', 'best_effort', 'no_change')]
    if args.json:
        _emit_json(script, results, _stdout)
    if len(results) > 1:
        # 这段在 _emit_json 之后打 —— 必须显式走 stderr，否则会拼在 JSON 尾巴
        # 上，让 json.loads 报 "Extra data"（t3-H1 / t6-H3）。
        print('=' * 62, file=sys.stderr)
        print(_msg.t('summary_multi', total=len(results),
                     ok=len(results) - len(bad), bad=len(bad)), file=sys.stderr)
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
