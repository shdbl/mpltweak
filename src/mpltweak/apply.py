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
    try:
        return '%.4f' % float(v)
    except (TypeError, ValueError):        # 脏参数不该让预览崩（审计 E6）
        return str(v)


def _num(v):
    '''数值字面量：整数不带小数点，浮点保留 6 位有效数字。'''
    try:
        f = float(v)
    except (TypeError, ValueError):
        return repr(v)
    if f == int(f) and abs(f) < 1e15:
        return '%d' % int(f)
    return '%.6g' % f


def _axlabel(i):
    """人读输出里的轴标号。`index` 正常是整数（脏值由 params.validate_errors 拦），
    但预览这条路径也得自己扛得住 —— 旧代码用 ``'ax%-2d' % i``，遇到字符串 index
    直接 TypeError 崩成 internal_error（审计 E6 的第 ① 类）。"""
    if isinstance(i, int) and not isinstance(i, bool):
        return 'ax%d' % i
    return 'ax%s' % (i,)


def _print_changes(script, data, p):
    axes = data.get('axes', [])
    if not isinstance(axes, list):                 # 脏参数：不要在这里崩
        axes = []
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
            print('%-4s 位置（参数里没给，保持原样）' % _axlabel(i))
        else:
            print('%-4s 位置 [%s]' % (_axlabel(i),
                                     ', '.join(_fmt(v) for v in pos)))
        _lfy = a.get('label_fontsize_y')
        if (a.get('title_fontsize') is not None
                or a.get('label_fontsize') is not None or _lfy is not None):
            # x/y 标签字号分开显示（T11）：只在 y 真给了值时多打一段，老参数文件
            # 的输出保持原样（不给既有断言/脚本添噪音）。
            print('      字号 title=%s label=%s%s tick=%s'
                  % (a.get('title_fontsize'), a.get('label_fontsize'),
                     '' if _lfy is None else ' label_y=%s' % _lfy,
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


def _snippet_line(field, val):
    """一个"没能落地"的字段 -> 一行**可证形态**的写法；不确定就返回 None（Q3 ①）。

    T70-A：这里**只打印**、绝不写进用户源码；也**绝不再打印运行期下标** ——
    旧 `--snippet` 里 `zip(fig.axes, ...)` / `fig.axes[i]...` 就是"一边禁一边教"。
    占位符 `<ax>` / `<fig>` / `<mappable>` 提醒用户换成自己的变量名。
    """
    if field == 'pos' and isinstance(val, (list, tuple)) and len(val) == 4:
        return '<ax>.set_position([%s])' % ', '.join(_num(v) for v in val)
    if field in ('xlim', 'ylim') and isinstance(val, (list, tuple)) and len(val) == 2:
        return '<ax>.set_%s(%s, %s)' % (field, _num(val[0]), _num(val[1]))
    if field == 'title_fontsize':
        return '<ax>.title.set_fontsize(%s)' % _num(val)
    if field == 'label_fontsize':
        return '<ax>.xaxis.label.set_fontsize(%s)' % _num(val)
    if field == 'label_fontsize_y':
        return '<ax>.yaxis.label.set_fontsize(%s)' % _num(val)
    if field == 'tick_fontsize':
        return '<ax>.tick_params(labelsize=%s)' % _num(val)
    if field == 'tick_fontsize_y':
        return "<ax>.tick_params(axis='y', labelsize=%s)" % _num(val)
    if field == 'grid':
        return '<ax>.grid(%s)' % ('True' if val else 'False')
    if field in ('xscale', 'yscale'):
        return '<ax>.set_%s(%r)' % (field, val)
    if field == 'clim' and isinstance(val, (list, tuple)) and len(val) == 2:
        return '<mappable>.set_clim(%s, %s)' % (_num(val[0]), _num(val[1]))
    if field == 'cmap':
        return '<mappable>.set_cmap(%r)' % (val,)
    if field == 'legend' and isinstance(val, dict) and val.get('loc'):
        return '<ax>.legend(loc=%r)' % (val['loc'],)
    if field == 'figsize':
        return '<fig>.set_size_inches(%s, %s)' % (_num(val[0]), _num(val[1]))
    return None                       # 不确定就不生成（Q3 ①）


def _snippet_refs(data, fields):
    """要打印的字段清单：``fields`` 为 None ⇒ 参数里所有目标字段（--snippet 预览用）。"""
    axes = [a for a in (data.get('axes') or []) if isinstance(a, dict)]
    if fields is not None:
        return list(fields)
    refs = []
    for a in axes:
        i = a.get('index')
        if i is None:
            continue
        for f in ('pos', 'title_fontsize', 'label_fontsize', 'label_fontsize_y',
                  'tick_fontsize', 'tick_fontsize_y', 'grid', 'xlim', 'ylim',
                  'xscale', 'yscale', 'clim', 'cmap', 'legend'):
            if a.get(f) not in (None, {}, []):
                refs.append('ax%d.%s' % (i, f))
    if data.get('figsize_in'):
        refs.append('figsize')
    return refs


def _snip_val(val):
    """片段里"原目标值"的短显示（列表 -> `[a, b, c]`，字典 -> repr，标量 -> %g）。"""
    if isinstance(val, (list, tuple)):
        return '[%s]' % ', '.join(str(v) for v in val)
    if isinstance(val, dict):
        return repr(val)
    return str(val)


def _print_snippet_fields(data, fields=None, res=None):
    """打印 T70-A 的可复制骨架（拒绝路径自动调用；``--snippet`` 也走它）。

    * ① 已由调用方的 `not_written` 行 + warnings 说明原因，这里只出片段；
    * ② 片段 = 每个目标字段一行 + 一行可证形态的写法（占位符提醒换名字）；
    * ③ 中英双语走 `messages.t()`；
    * 内容**一个字节都不写进用户源码**。
    """
    axes = {a.get('index'): a for a in (data.get('axes') or []) if isinstance(a, dict)}
    rows = []
    for ref in _snippet_refs(data, fields):
        if ref == 'figsize':
            i, f, val = None, 'figsize', data.get('figsize_in')
        else:
            _p = ref.split('.', 1)
            if len(_p) != 2 or not _p[0].startswith('ax')                     or not _p[0][2:].isdigit():
                continue
            i, f = int(_p[0][2:]), _p[1]
            val = (axes.get(i) or {}).get(f)
        sug = _snippet_line(f, val)
        if sug is None:
            continue
        rows.append('# %s = %s' % (ref, _snip_val(val)))
        rows.append('#     -> %s' % sug)
    if not rows:
        return
    print(_msg.t('snippet_head'))
    for _l in rows:
        print('  ' + _l)
    have_cb = any(a.get('is_colorbar') and (axes.get(a.get('index')) is not None)
                  for a in (data.get('axes') or []) if isinstance(a, dict))
    print(_msg.t('snippet_provable_head'))
    print(_msg.t('snippet_provable_subplots'))
    if res is not None and res.get('fig_var') == 'plt.gcf()':
        print(_msg.t('snippet_provable_figure'))
    if have_cb:
        print(_msg.t('snippet_provable_colorbar'))


def _print_snippet(data):
    """`--snippet`：打印可复制的落实骨架（T70-A 起与拒绝路径**同一个渲染器**）。

    旧版正文是 `for _ax, _p in zip(fig.axes, [...])` + `fig.axes[i].xxx` —— 那是
    **运行期下标**寻址，正是 T70 明令禁止、且会写错目标对象的那种写法（"一边禁一边教"）。
    """
    _print_snippet_fields(data, None, None)


# 这几条新文案走 messages.t()（key：nothing_applied / dryrun_inplace / dryrun_block，
# 由 cli-hunter 在 T8 里补进中英表）。下面三个 `_FALLBACK_*` 只是**过渡兜底**：
# messages.py 由 cli-hunter 独占（并发写坏风险），所以这里只**读**它的 MESSAGES 表 ——
# key 还没落地时用兜底文案，等 T8 合并后自动切到 messages.py，不必再动本文件。
# TODO(i18n): T8 落地后这三个兜底常量可以删掉（保留也无害）。
_FALLBACK_NOTHING_APPLIED = '[--] 本次没有可落地的字段：脚本未被改动（%(script)s）'
_FALLBACK_DRYRUN_INPLACE = '[预览] 将原位修改 %(n)d 处: %(items)s（未落盘）'
_FALLBACK_DRYRUN_BLOCK = '[预览] 将插入调整块: %(script)s（未落盘）'
# T15：显式 --style inplace 时，有些字段"代码里没有可改的写法"，只能靠调整块落地。
# 这时必须明确说出来（并让 rc≠0），绝不能让 agent 从 rc=0/applied=true 推断"都改好了"。
_FALLBACK_NEEDS_BLOCK = ('[!] 这些字段代码里没有可改的写法、本次**没能落地**：%(items)s\n'
                         '    → 用 --style auto（默认，会自动插入调整块）或 --style block '
                         '重新落实；原位模式只改代码里已有的数字。')
# T23 / 外部评审条目 3(d)：把"这次到底验了哪几层"如实打出来（快速档 / 没重跑 / 没语义）。
# TODO(i18n): 交给 cli-hunter 搬进 messages.py（键名建议 verify_layers_fast /
#             verify_layers_no_rerun / verify_layers_no_semantic）。
_LAYERS_FAST_NOTE = ('[i] 本次只做了**语法检查**：没有重跑脚本、没有比对布局是否真的等于'
                     '参数（--verify-fast 的弱保证）')
_LAYERS_NO_RERUN = '[i] 本次**没有重跑**脚本（--no-verify）：写回结果未经运行验证'
_LAYERS_NO_SEMANTIC = ('[i] 本次**没有做语义比对**（第③层没跑）：只确认了"脚本能跑通"，'
                       '不保证布局真的落到目标图上')


def _t_new(key, fallback, **kw):
    """新文案：优先 ``messages.t(key)``，key 还没搬进消息表时用兜底模板。

    不能只写 `_msg.t(key)` 就完事：`t()` 的容错契约是"key 缺失就原样返回 key"，
    在 T8 合并之前终端上会出现 `nothing_applied` 这种裸 key 行（不崩，但很难看）。
    """
    if key in getattr(_msg, 'MESSAGES', {}):
        return _msg.t(key, **kw)
    return fallback % kw


def _auto_should_fallback(res):
    """auto 模式是否该改用块模式重试 —— **显式依据**（不再看 ``not changes``）。

    判据（审计 E1 的修法；旧实现把 ``no_change`` 也当成"原位落空"，于是
    "参数与源码完全一致"的干净往返也会被插一块调整块，连带幂等/可逆全灭）：

      * ``no_change`` → False：参数已经与源码一致，一个字节都不该动；
      * ``fail``      → True ：原位那次验证没过（已回滚），块模式的锚点策略不同，
        值得再试一次；
      * 其余（ok / best_effort）→ 看 writeback 明确给出的 ``needs_block``：它只在
        "参数里有目标值、原位确实写不了、**且该字段与源码现值不同**"时才为真
        （判不了的情形由 writeback 保守置真，绝不静默丢请求）。
    """
    if res.get('reason') == 'no_change':
        return False
    if res.get('reason') == 'fail':
        return True
    return bool(res.get('needs_block'))


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


def _emit_json(script, results, stdout_ref, ok=None, error=None, extra=None):
    """--json 的机器可读输出（每个 return 分支前都要调用一次）。

    ``ok`` 可显式覆盖：没有参数文件时 results 是空的，``not []`` 会算成 True，
    而退出码却是 2 —— 退出码与 ok 字段语义打架（t6-M7）。

    ``extra``：额外平铺进每个 files[] 条目的键（如 ``dry_run``）。
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
             # RC-9：块模式的 changes 是空的，agent 完全看不出"写了什么"——
             # 用 block_lines（块行数）与 applied（这次到底改没改磁盘）补上。
             'applied': bool(r.get('applied')),
             'block_lines': r.get('block_lines') or 0,
             # RC-5：auto 退回块模式时，把"原位那次为什么没落地"一并给出，
             # 否则只能靠 style=='block' 反推。
             'auto_fallback': bool(r.get('auto_fallback')),
             'inplace_reason': r.get('inplace_reason'),
             'inplace_changes': r.get('inplace_changes') or [],
             'needs_block': bool(r.get('needs_block')),
             # T15：needs_block 的口径 ——
             #   * 最终结果里的 needs_block=true ⇒ **这次请求还有字段没能落地**
             #     （只有显式 --style inplace 才会留着 true；auto 会退块把它清掉）；
             #   * 退过块时，原位那次的判断留在 inplace_needs_block / inplace_pending，
             #     以及"哪些字段当时写不进去"的 pending 清单。
             'inplace_needs_block': bool(r.get('inplace_needs_block')),
             'pending': r.get('pending') or [],
             'inplace_pending': r.get('inplace_pending') or [],
             # T23：块模式的落点也必须可见（T22 实测：块落到 savefig 之后的那个 bug
             # 在 --json 里连 anchor/attempts 都没有，消费方无从判断块插到了哪里）。
             'anchor': r.get('anchor'),
             'attempts': r.get('attempts') or [],
             # T23 / 3(a)(d)：三层验证（语法 / 重跑 / 语义）各自的真实状态，
             # None = 这一层**没跑**（别再靠 verify_mode 反推"到底验了什么"）。
             'layers': r.get('layers') or {},
             # T23：失败原因也要能从 JSON 里读到（过去只有人读 stderr 一行，agent
             # 从 --json 完全看不出为什么 fail）。
             'err': r.get('err') or None,
             'backup': r.get('backup'),
             'verified': r.get('verified'),
             'verify_mode': r.get('verify_mode'),
             'semantic': r.get('semantic'),
             'warnings': r.get('warnings') or [],
             'layout_conflicts': r.get('layout_conflicts') or []}
            for p, r in results],
    }
    if extra:
        for _f in _payload['files']:
            _f.update(extra)
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
    ap.add_argument('--keep-unverified', action='store_true',
                    help='脚本本身在无头环境跑不通（缺数据/依赖/需要 GUI）时，'
                         '**保留**这次写回（默认是回滚 + 明确失败：那时验证证明不了'
                         '写回是否可用，留下坏写回比失败更糟）')
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
    _applied_files = []
    # T36：本批次的"批前原件"备份。多图会话里一次 `apply --write` 会**连着改同一个
    # 脚本好几遍**（每份参数一次），每次写盘都会备一份 —— 固定名 `<stem>.tweak.bak`
    # 于是被后来的写盘**覆盖成中间态**：`revert` 还原出来的是"改了一半"的脚本，不是
    # 用户跑 apply 之前那份（和 L0-4 / inplace→block 完全同一个坑，只是发生在多参数
    # 循环里）。修法：把本批次**第一次**写回产生的备份沿循环逐份透传（与下面
    # inplace→block 的 `_bk` 同一机制）。
    _batch_backup = None
    # 批次基准（T23 / 外部评审条目 2）：**一次 apply 里脚本会被我们自己改**（每落地
    # 一张图就写一次盘 → 脚本 mtime 变新），若每条参数都拿"此刻的脚本 mtime"去比，
    # 那第 2 张图必然被判 stale（还会打印"脚本在参数保存之后被修改过"这个与事实不符的
    # 理由）。所以基准取**本批次开始时刻**：它只反映用户自己的手改，不含我们刚写的。
    try:
        _script_mtime0 = os.path.getmtime(script)
    except OSError:
        _script_mtime0 = None
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
                _stale = (_script_mtime0 is not None
                          and _script_mtime0 > os.path.getmtime(p) + 1.0)
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

        # E6：**硬错误**（error 级）在入口就拒写（rc=2）。旧行为是"print 一句警告
        # 后继续跑"，于是脏值一路走到下游：`'ax%-2d' % '0'`、`float('abc')`、
        # `pos[3]`、`int(nan)` 各自炸成 internal_error（连只读预览都崩），
        # 而这里的校验明明已经看出来了。
        _errs = params.validate_errors(data)
        if _errs:
            for _e in _errs:
                print('!! 参数文件错误（已拒写）: %s' % _e)
            # T68：拒写就是拒写 —— 人读那行也必须是"未写回"+ 原因码，不许出现"已写回"。
            print(_msg.t('not_written', reason='bad_params', script=script))
            results.append((p, {'reason': 'bad_params',
                                'err': '; '.join(_errs), 'changes': []}))
            continue

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
                   fast_check=bool(args.verify_fast),
                   keep_unverified=bool(args.keep_unverified))
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
            res = writeback.writeback(script, data, p, style=_first,
                                      backup=_batch_backup, **_kw)
            if args.style == 'auto' and _auto_should_fallback(res):
                # RC-5：块模式那次的结果会**整份覆盖**原位那次 —— 覆盖前先把原位
                # 那次的结论留档，否则 agent 从 --json 里完全看不出"原位为什么没落地"
                # （只能靠 style==block 反推）。
                _ip = {k: res.get(k) for k in ('reason', 'changes', 'warnings',
                                               'needs_block', 'pending')}
                # L0-4：原位那次**可能已经写过盘并备过份** —— 退块的第二次写盘必须
                # 复用那份备份，否则固定名备份被覆盖成"改过一次"的中间态，
                # `revert` 拿到的是半成品（主仓实测 20154193__RealSpace.py）。
                _bk = res.get('backup')
                res = writeback.writeback(script, data, p, style='block',
                                          backup=(_batch_backup or _bk), **_kw)
                res['auto_fallback'] = True
                res['inplace_reason'] = _ip['reason']
                res['inplace_changes'] = _ip['changes'] or []
                res['inplace_warnings'] = _ip['warnings'] or []
                res['inplace_needs_block'] = bool(_ip['needs_block'])
                res['inplace_pending'] = _ip['pending'] or []
                # T42（Bug 3）：原位通道**已经把能写的那几处写进文件了** —— 退块分支
                # 的结果对象不得把这件事抹掉，否则 JSON 报 `no_change`/`applied=false`/
                # `changes=[]`/`backup=null`，而盘上文件真的被改过（gallery/artists.py
                # 实测：`ax1.pos` 落了盘、`ax0.pos` 被丢，reason 却是 no_change）。
                _ip_done = list(_ip['changes'] or [])
                _ip_bk = _bk
                if _ip_done:
                    res['changes'] = sorted(set(res.get('changes') or [])
                                            | set(_ip_done))
                    if res.get('reason') == 'no_change':
                        # 块通道没有新东西可写，但原位已经落地 → 如实报 best_effort
                        res['reason'] = ('ok' if res.get('block_lines')
                                         else 'best_effort')
                    if res.get('applied') is False:
                        res['applied'] = True
                    if res.get('pending') is None:
                        res['pending'] = []
                    res['needs_block'] = bool(res.get('needs_block')
                                              or _ip.get('needs_block'))
                if res.get('backup') is None and _ip_bk:
                    res['backup'] = _ip_bk      # 备份如实报告（盘上确实有）
        finally:
            if _lk is not None:
                _lk.__exit__(None, None, None)
        # T36：记下本批次**第一次**真正做出的备份，后面的每一份写回都复用它
        # （固定名 `.tweak.bak` 从此始终是"批前原件"，`revert` 拿回的是完整原件）。
        if res.get('backup'):
            _batch_backup = _batch_backup or res['backup']
        if _conflicts and res.get('reason') in ('ok', 'best_effort', 'preview'):
            # 只在**这次确实落地/确实有计划**时才提示（no_change / fail / no_fig 不提示，
            # 否则是跟当前动作无关的噪音）。
            # 注意别用 `res['changes']` 判：**块模式**的 changes 是空的（它插的是块，
            # 不是逐字段原位替换），而"块 + 布局引擎"恰恰是最该提示的组合 ——
            # 2026-10-10 端到端冒烟发现漏提示，这里按 reason 判。
            res['layout_conflicts'] = _conflicts
        results.append((p, res))
        # E7：`best_effort` 同时覆盖"改到一部分"和"一处都没改"两种情形 ——
        # 一处都没落地时绝不能打印"[OK] 已原位写回"（脚本一个字节都没动）。
        _applied = bool(res.get('applied')) and not args.dry_run
        if res['reason'] in ('ok', 'best_effort'):
            if res.get('style') == 'inplace':
                ch = res.get('changes') or []
                if args.dry_run:
                    # E9：dry-run 下不得出现"已写回"字样（脚本一个字节没动）
                    print(_t_new('dryrun_inplace', _FALLBACK_DRYRUN_INPLACE,
                                 n=len(ch), items=', '.join(ch)[:160]))
                elif _applied and ch:
                    print(_msg.t('written_inplace', script=script))
                    print(_msg.t('written_inplace_count', n=len(ch),
                                 items=', '.join(ch)[:160]))
                elif _applied:
                    # T68：`applied=true` 但没有逐字段原位改动记录（改动是块通道做的）——
                    # 文件确实被动过，仍如实说"已写回"，只是没有"原位修改 N 处"可报。
                    print(_msg.t('written_inplace', script=script))
                else:
                    # T68：一个字节都没写（applied=false）⇒ 绝不许说"已写回"。
                    # 如实报"未写回"+ 原因码；没能落地的字段由下面的 needs_block 提示列出。
                    print(_msg.t('not_written', reason=res['reason'],
                                 script=script))
                    # T70 第 4 条：拒绝时**直接**给可复制的骨架（不要求 --snippet）
                    if res.get('pending') or res.get('warnings'):
                        _print_snippet_fields(data, res.get('pending'), res)
                # T15（INT-2 的 B 情形）：显式 --style inplace 时，**原位写不了**的字段
                # 这次没能落地 —— 必须说出来（rc 也会变 1），不能只埋在 warnings 里。
                if res.get('needs_block') and not args.dry_run:
                    print(_t_new('needs_block_hint', _FALLBACK_NEEDS_BLOCK,
                                 items='、'.join(res.get('pending') or [])
                                 or '(见 warnings)'))
            else:
                if args.dry_run:
                    print(_t_new('dryrun_block', _FALLBACK_DRYRUN_BLOCK,
                                 script=script))
                elif _applied:
                    # T68：**只有真的写进文件**才说"已写回"（旧实现只看 reason，
                    # 于是 applied=false 的拒绝路径也报 `[OK] 已写回`）。
                    print(_msg.t('written_block', script=script))
                else:
                    # T68：块没写（例如"运行时轴数证不出 ⇒ 拒绝写位置块"的 fail-closed
                    # 路径）⇒ 如实报"未写回"+ 原因码。
                    print(_msg.t('not_written', reason=res['reason'],
                                 script=script))
                    # T70 第 4 条：拒绝时**直接**给可复制的骨架
                    if res.get('pending'):
                        _print_snippet_fields(data, res.get('pending'), res)
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
                # T23 / 3(d)：快速档实际只有"语法"这一层 —— 如实说清哪几层没跑，
                # 别让人从"fast"两字反推出"验证过"。
                print(_LAYERS_FAST_NOTE)
            else:
                _ly = res.get('layers') or {}
                if _ly.get('rerun') is None and not args.no_verify:
                    print(_LAYERS_NO_RERUN)
                if _ly.get('semantic') is None and not args.no_semantic \
                        and _ly.get('rerun') is not None:
                    print(_LAYERS_NO_SEMANTIC)
            if res.get('verified') is True:
                print(_msg.t('verify_run_ok'))
            elif res.get('verified') is False:
                print(_msg.t('verify_run_fail'))
            if res.get('semantic') is True:
                print(_msg.t('semantic_ok'))
            elif res.get('semantic') is None and not args.no_semantic:
                # `semantic is None` 有两种来源：真跑过但取不到状态、以及**压根没跑**
                # （原位一个字段都没落地时提前返回）。后者不该打印"未能判定"这种
                # 假解释 —— writeback 会把它标成 'skipped'（审计 E7）。
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
            # T23：这一批里**真正落地过**的每一份参数都刷新（不只当前那份）——
            # 多图会话是同一个脚本、连着写几次，只刷新当前那份会让"还在这一批里
            # 没轮到的"看起来比脚本旧（外部评审条目 2 的现场）。**没落地**的参数
            # （stale / bad_params / fail）不动：它们本来就不该被当成"与代码一致"。
            if not args.dry_run:
                os.utime(p, None)
                _applied_files.append(p)
        elif res['reason'] == 'no_change':
            print(_msg.t('no_change'))
            for w in res.get('warnings', []):
                print(_msg.t('kept_as_is', msg=w))
        elif res['reason'] == 'no_fig':
            print(_msg.t('no_fig', err=res['err']))
            print(_msg.t('no_fig_hint'))
        elif res['reason'] == 'bad_params':
            # T61：引擎侧的越界判据（轴序号/图号）也归 bad_params 一档 ——
            # 拒绝发生在写盘之前（脚本一个字节没动），人读输出要说清是"参数不对"，
            # 而不是含糊的"写回失败"。
            print('!! 参数文件错误（已拒写，脚本未改动）: %s' % (res.get('err') or ''))
        else:
            print(_msg.t('write_failed', reason=res['reason'], err=res['err']))

    # T23：把这一批里**落地过**的参数文件互相同步 mtime —— 它们此刻都等于"写回后
    # 的代码状态"，下一次批量写回/开窗就该一致地被判为新鲜（而不是只有最后一份新鲜）。
    if args.write and not args.dry_run and len(_applied_files) > 1:
        for _pf in _applied_files:
            try:
                os.utime(_pf, None)
            except OSError:
                pass

    if not args.write:
        if any(r.get('reason') == 'preview' for _, r in results):
            print('-' * 62)
            print('提示：以上数值需按脚本自身风格写回；colorbar 轴若改了位置，必须同时')
            print('      cb.ax.set_box_aspect(None)，否则 matplotlib 每次重绘会把宽度重置回自动值。')
            print('      机械插入可用 --write（AST 确定性写回，零 LLM）。')
        if args.json:
            # dry_run 必须能从 JSON 里看出来（预览没落盘，消费方不能当"已写回"）。
            _emit_json(script, results, _stdout,
                       extra={'dry_run': bool(args.dry_run)})
        # 参数文件硬错误（error 级）→ 与写回路径同一档：rc=2（输入/用法错误）
        return 2 if any(r['reason'] == 'bad_params' for _, r in results) else 0

    bad = [r for _, r in results
           if r['reason'] not in ('ok', 'best_effort', 'no_change')]
    # T15：显式 --style inplace 时，"有字段必须靠调整块才能落地"= 这次请求没有完全兑现
    # → rc=1（agent 不该从 rc=0 推断"都改好了"）。auto 模式不受影响：needs_block 会让
    # apply 自动退块，字段照样落地（见 _auto_should_fallback）。
    _unlanded = [r for _, r in results
                 if r.get('needs_block') and args.style == 'inplace'
                 and not args.dry_run]
    # 参数文件非法 → rc=2（"输入/用法错误"，与 --strict 拒绝、缺参数文件同一档），
    # 让 agent 能区分"你的参数不对"和"写回失败"（审计 E6 的修法）。
    _rc = (2 if any(r['reason'] == 'bad_params' for _, r in results)
           else (1 if (bad or _unlanded) else 0))
    if args.json:
        _emit_json(script, results, _stdout, extra={'dry_run': bool(args.dry_run)})
    if len(results) > 1:
        # 这段在 _emit_json 之后打 —— 必须显式走 stderr，否则会拼在 JSON 尾巴
        # 上，让 json.loads 报 "Extra data"（t3-H1 / t6-H3）。
        print('=' * 62, file=sys.stderr)
        print(_msg.t('summary_multi', total=len(results),
                     ok=len(results) - len(bad), bad=len(bad)), file=sys.stderr)
    return _rc


if __name__ == '__main__':
    sys.exit(main())
