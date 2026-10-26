# -*- coding: utf-8 -*-
# ==== §1 文件头：apply 的职责与两条输出（人读 / --json） ====

"""
mpltweak apply —— 「落实」助手（CLI：mpltweak apply <脚本.py>）
=====================================================================

**快速导航**（顶层横幅 `# ==== §N 名字 ====` 被文件尾索引收录；`main()` 体内的小节
用 `# ==== §8.N … ====`，工具不索引，跳转请 `grep -n '§8.5'`）：

- §1 文件头：apply 的职责与两条输出（就是本段）
- §2 人读输出：行格式（`_fmt` / `_num` / `_axlabel`）、错误行出口（`_emit_err`）、
  改动清单（`_print_changes`）、可复制骨架（`_snippet_*` / `--snippet`）
- §3 「验了哪几层」的说明文案：`_LAYERS_*`（经 `_t_new` 取词；文案键都在 messages 表里，
  这几个常量是 test_writeback 引用的稳定符号）
- §4 验证重跑判定与 auto 回退：`_verify_rerun_state`（**只认真凭据**，不按 flag 反推）/
  `_t_new` / `_auto_should_fallback` / `_success_tail`（无感化②：成功只报一行）
- §5 参数文件收集与失败错误码：`_all_params_files`（主文件 + `<stem>.fig<k>.json`）/
  `_OK_REASONS` / `_FAIL_ERROR_CODE` / `_failure_code`
- §6 `--json` 机器可读输出：`_emit_json`（每个 return 分支前都要调用一次）
- §7 验证失败归因与下一步：`_VERIFY_FAIL_*` / `_classify_verify_failure` /
  `_is_rerun_failure` / `_print_attribution` / `_next_step_cmd`
- §8 主流程 `main`：预备 → 逐份参数文件写回 → 输出。内分 6 小节 ——
  §8.1 入口与 `--json` 分流 / §8.2 前置检查（存在性、布局引擎冲突、可重跑预检）/
  §8.3 批次基准与备份透传（T36 / T23）/ §8.4 逐份校验与拒写 /
  §8.5 写回（串行锁、style 与 auto 回退）/ §8.6 收尾（mtime 同步 + 输出）

**与 `writeback.py` 的分区对照**（两边跳读用同一套词，不另造）：

| 这件事 | 这边 | writeback.py |
|---|---|---|
| 验证（重跑 / 语义 / 层级） | §4 判定 + `verify` 调用 | §22 验证：无头重跑与一遍式验证、§15 语义比对 |
| 备份（固定名 / 批前原件） | §8.3 透传本批第一份备份 | §12 备份（固定名 / pid 副本 / 裁剪） |
| 并发 | §8.5 串行化写回 | §13 并发锁与原子写 |
| 外部改动（脚本比参数新） | §8.4 拒写 | §14 外部改动检测（落盘前的指纹校验） |
| 真正改脚本 | 调 `writeback.writeback` | §16 原位写回主流程、§23 写回入口 |

把 .tweak_params/*.json 变成改动清单 / 纯 matplotlib 片段，或**确定性写回脚本**：

  mpltweak apply <脚本.py>                     # 改动清单（默认，只读不改）
  mpltweak apply <脚本.py> --snippet           # 额外输出可粘贴片段
  mpltweak apply <脚本.py> --write             # AST 确定性写回（见 mpltweak.writeback）
  mpltweak apply <脚本.py> --write --timeout 600   # 写回；脚本跑得久就放宽验证等待
  mpltweak apply <脚本.py> --write -- --data x.npz # 写回；`--` 后的参数透传给重跑验证
  mpltweak apply <脚本.py> --write --no-verify     # 跳过重跑与语义验证：跑不通/布局不对都不会被发现，也不会回滚（一般不要用）
  mpltweak apply <脚本.py> --params X          # 指定参数文件

它默认**不改脚本**（只读 + 只打印）：落实交给 AI 按脚本自身风格完成；
需要机械插入时用 ``--write``（AST 零 LLM，失败回退结构化提示）。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys

from . import layoutwarn
from . import messages as _msg
from . import params
from . import preflight
from . import verify as _verify
from . import writeback


# ==== §2 人读输出：行格式、改动清单与可复制骨架 ====

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


def _emit_err(*lines):
    """人读**错误**行的统一出口：逐行写 stderr 并 flush（A3 / MAINTENANCE §25.15）。

    为什么要一个统一出口：`apply` 不带 ``--json`` 时，人读错误行原先和正常报告一样
    走 stdout，于是 ``mpltweak apply foo.py > out.txt`` 会把错误也写进 out.txt ——
    管道/重定向无法干净区分"数据 / 错误"（2026-10-15 用户拍的板）。

    输出契约（0.1.12 起，见 docs/exec-boundaries.md 的表）：
      * **stdout** = 数据/报告：成功摘要、逐处清单、``--write`` 的落地行、预览的
        「会改：…」与骨架、进度行、``--json`` 的载荷；
      * **stderr** = 人读错误：失败、回滚、拒写（bad_params / 未写回 / 需要退块却
        没能落地）、参数文件坏、外部改动被跳过、验证失败及其归因、用法/输入错误。
    判据是"**这次请求有没有兑现**"，不是"消息长得凶不凶"：含糊的那几类
    （``--verify-fast`` 降级提醒、某字段没动、布局引擎提示）属于**过程披露**，
    仍走 stdout —— 它们既不宣告成功也不宣告失败，成功与否由 rc + stderr 是否为空
    共同决定，读者不会被误导；把它们也塞进 stderr 会让"stderr 非空 ⇒ 有错"这条
    管道规则失效。

    ``--json`` 下 stdout 已被换成 stderr（见 ``main`` 里那一步），因此这里继续写
    真正的 stderr：JSON 载荷不会被这些行污染。
    """
    for _l in lines:
        sys.stderr.write('%s\n' % (_l,))
    sys.stderr.flush()


def _print_changes(script, data, p):
    axes = data.get('axes', [])
    if not isinstance(axes, list):                 # 脏参数：不要在这里崩
        axes = []
    print(_msg.t('apply_params_label', path=p))
    print(_msg.t('apply_script_label', name=os.path.basename(script)))
    print(_msg.t('apply_canvas_label', inch=data.get('figsize_in'),
                 px=data.get('figsize_px')))
    # P2-11（t69）：明说**这次会改哪些字段** —— 与"没动"的字段区分开。
    # 报告现场：预览只打了"画布: figsize_in=[7.5, 4.75]"，用户/agent 看不出
    # "这一下会把我的画布改成一半"（P0-1 的背景）。figsize 若能从源码里静态读到
    # 当前字面量，就给 `旧 → 新`（报告要的形态）；读不到时只列字段名。
    _refs = _snippet_refs(data, None)
    if not _refs:
        print(_msg.t('apply_will_change_none'))
    else:
        _old_fs = None
        if 'figsize' in _refs:
            try:
                import ast as _ast
                with open(script, 'r', encoding='utf-8-sig') as _fsrc:
                    _ssrc = _fsrc.read()
                _tree = _ast.parse(_ssrc)
                _old_fs = writeback._figsize_from_source(_ssrc, _tree)
                if not (isinstance(_old_fs, (list, tuple))
                        and len(_old_fs) == 2):
                    # 最常见的写法是 `plt.figure(figsize=(w, h))` /
                    # `plt.subplots(figsize=…)`，而 _figsize_from_source 只认
                    # `set_size_inches(...)` 字面量 ⇒ 这里补一层（读不到就只列字段名）。
                    for _n in _ast.walk(_tree):
                        if not isinstance(_n, _ast.Call):
                            continue
                        for _kw in _n.keywords:
                            if _kw.arg != 'figsize':
                                continue
                            try:
                                _v = _ast.literal_eval(_kw.value)
                            except (ValueError, SyntaxError, TypeError):
                                _v = None
                            if isinstance(_v, (list, tuple)) and len(_v) == 2:
                                _old_fs = list(_v)
                        if _old_fs:
                            break
            except (OSError, SyntaxError, ValueError):
                _old_fs = None
        _new_fs = data.get('figsize_in') or []
        _items = []
        for _r in _refs:
            if _r != 'figsize':
                _items.append(_r)
            elif (isinstance(_old_fs, (list, tuple)) and len(_old_fs) == 2
                    and isinstance(_new_fs, (list, tuple))
                    and len(_new_fs) == 2):
                _items.append(_msg.t(
                    'apply_figsize_old_new',
                    old='(%s, %s)' % (_num(_old_fs[0]), _num(_old_fs[1])),
                    new='(%s, %s)' % (_num(_new_fs[0]), _num(_new_fs[1]))))
            else:
                _items.append('figsize')
        print(_msg.t('apply_will_change',
                     items=_msg.t('items_sep').join(_items)))
    print('-' * 62)
    for a in axes:
        if not isinstance(a, dict):                # L1：参数文件里有脏轴项 → 不崩溃
            print(_msg.t('apply_axis_item_bad', item=a))
            continue
        i = a.get('index')
        pos = a.get('pos')
        if pos is None:
            # 轴项缺 pos 是**通过官方 schema 校验的合法输入**（pos 不是必填）；
            # 原先直接 `for v in None` 会抛 TypeError，且 --json 下 stdout 为空。
            print(_msg.t('apply_axis_pos_missing', ax='%-4s' % _axlabel(i)))
        else:
            print(_msg.t('apply_axis_pos', ax='%-4s' % _axlabel(i),
                         pos=', '.join(_fmt(v) for v in pos)))
        _lfy = a.get('label_fontsize_y')
        if (a.get('title_fontsize') is not None
                or a.get('label_fontsize') is not None or _lfy is not None):
            # x/y 标签字号分开显示（T11）：只在 y 真给了值时多打一段，老参数文件
            # 的输出保持原样（不给既有断言/脚本添噪音）。
            print(_msg.t('apply_axis_fontsize',
                         title=a.get('title_fontsize'),
                         label=a.get('label_fontsize'),
                         labely='' if _lfy is None else ' label_y=%s' % _lfy,
                         tick=a.get('tick_fontsize')))
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
            loc = lg.get('loc') or _msg.t('apply_legend_free_anchor')
            print(_msg.t('apply_axis_legend', loc=loc, size=lg.get('fontsize')))


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


def _print_snippet_fields(data, fields=None, res=None, err=False):
    """打印 T70-A 的可复制骨架（拒绝路径自动调用；``--snippet`` 也走它）。

    * ① 已由调用方的 `not_written` 行 + warnings 说明原因，这里只出片段；
    * ② 片段 = 每个目标字段一行 + 一行可证形态的写法（占位符提醒换名字）；
    * ③ 中英双语走 `messages.t()`；
    * 内容**一个字节都不写进用户源码**；
    * ``err=True``：这段骨架是**拒写报告的一部分**（紧跟 ``not_written`` 那行），
      因此与错误行一起走 stderr —— 否则 `> out.txt` 会把"为什么没写回"留在终端、
      把骨架写进文件，报告被劈成两半。``--snippet`` 预览（不是错误）仍走 stdout。
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
    _p_out = _emit_err if err else print
    _p_out(_msg.t('snippet_head'))
    for _l in rows:
        _p_out('  ' + _l)
    have_cb = any(a.get('is_colorbar') and (axes.get(a.get('index')) is not None)
                  for a in (data.get('axes') or []) if isinstance(a, dict))
    _p_out(_msg.t('snippet_provable_head'))
    _p_out(_msg.t('snippet_provable_subplots'))
    if res is not None and res.get('fig_var') == 'plt.gcf()':
        _p_out(_msg.t('snippet_provable_figure'))
    if have_cb:
        _p_out(_msg.t('snippet_provable_colorbar'))


def _print_snippet(data):
    """`--snippet`：打印可复制的落实骨架（T70-A 起与拒绝路径**同一个渲染器**）。

    旧版正文是 `for _ax, _p in zip(fig.axes, [...])` + `fig.axes[i].xxx` —— 那是
    **运行期下标**寻址，正是 T70 明令禁止、且会写错目标对象的那种写法（"一边禁一边教"）。
    """
    _print_snippet_fields(data, None, None)


# ==== §3 兜底文案与「验了哪几层」的说明 ====

# REV1-7（t35 / 0.1.14）：这里原来有 4 个 `_FALLBACK_*` 过渡兜底（nothing_applied /
# dryrun_inplace / dryrun_block / needs_block_hint）+ 一句"T8 落地后可以删掉"的 TODO。
# T8 早已落地（这些键都在 messages.MESSAGES 里），兜底**永远不会被选中** ⇒ 死代码 +
# 假承诺（"接了线但永不触发"）。现在直接用 `_msg.t(key)`（键由 test_msg_key_audit
# 静态扫描兜住：漏登记就红），4 个常量与那句过期 TODO 一并删掉。
# T23 / 外部评审条目 3(d)：把"这次到底验了哪几层"如实打出来（快速档 / 没重跑 / 没语义）。
# TODO(i18n): 交给 cli-hunter 搬进 messages.py（键名建议 verify_layers_fast /
#             verify_layers_no_rerun / verify_layers_no_rerun_plain /
#             verify_layers_dryrun_maybe / verify_layers_no_semantic）。
_LAYERS_FAST_NOTE = ('[i] 本次只做了**语法检查**：没有重跑脚本、没有比对布局是否真的等于'
                     '参数（--verify-fast 的弱保证）')
_LAYERS_NO_RERUN = '[i] 本次**没有重跑**脚本（--no-verify）：写回结果未经运行验证'
# §20.3#4：同样是"没有重跑"，但**原因不是 --no-verify**（dry-run 里没有需要实测回比的
# 字段、或写回还没走到验证就早退）—— 别再把账记到 --no-verify 头上（旧判据的错挂）。
_LAYERS_NO_RERUN_PLAIN = '[i] 本次**没有重跑**脚本：写回结果未经运行验证'
# §20.3#4①：dry-run 的默认档**确实会跑脚本**（参数含"原位写不了"的字段时走
# writeback._pending_vs_live → verify_semantic 做实测回比），但 dry-run 走的是 writeback
# 的早退分支（writeback.py:2902-2910），返回的 res 里**没有 layers 这层真凭据** ——
# 所以这条路上只能如实说"不排除已经跑过"，**绝不许**反过来说"没有重跑"。
_LAYERS_DRYRUN_MAYBE = (
    '[i] dry-run 预览（未落盘）：这批参数含"原位写不了"的字段 —— 默认档会尝试**实测回比**'
    '（这一步会**重跑一遍你的脚本**），因此**不排除本次已经跑过它**；预览结果未经运行验证。'
    '（--verify-fast / --no-verify / --no-semantic 不做实测回比、不重跑）')
_LAYERS_NO_SEMANTIC = ('[i] 本次**没有做语义比对**（第③层没跑）：只确认了"脚本能跑通"，'
                       '不保证布局真的落到目标图上')


# ==== §4 验证重跑判定与 auto 回退（口径同 writeback §22 验证） ====

def _verify_rerun_state(res, args):
    """这次到底有没有**为验证重跑**脚本 —— 真凭据优先，**不按 flag 反推**。

    背景（MAINTENANCE §14.2#3 = §20.3#4，同一个根因）：``--write --dry-run`` 的默认档
    在参数含"原位写不了"的字段时会走 ``writeback._pending_vs_live`` → ``verify_semantic``
    （writeback.py:2659-2698 / :2694）——那一步会**真跑一遍用户的脚本**；而 dry-run 走的是
    writeback 的早退分支（writeback.py:2902-2910），返回的 res 里**根本没有 `layers`**。
    旧判据 ``_ly.get('rerun') is None and not args.no_verify`` 于是把"没有 layers"当成
    "没跑"，造成两个相反的错：① dry-run 真跑了却说"没有重跑"；② **唯一**真该说这句的
    ``--no-verify`` 反而被 ``not args.no_verify`` 压掉（该说时不说）。

    判据（每一档都只依据真凭据/档位本身能保证的事实）：
      * ``res['layers']`` 在 —— writeback 在**写盘之后**逐层填的真结果：
        ``rerun`` 为 None ⇒ 没跑；不是 None ⇒ 跑了（True 通过 / False 失败）。
      * ``layers`` 不在且是 dry-run：❌ 没有凭据。只有**档位本身保证不跑**的才敢说
        'not_ran'：fast / no-verify / no-semantic 走 writeback.py:2891-2893 的保守分支；
        没有 ``needs_block``（没有"原位写不了"的字段）就进不了实测回比。默认档且
        ``needs_block`` 为真时 ⇒ 'may_ran'（如实说"不排除跑过"）。
      * ``layers`` 不在又不是 dry-run：只可能是"还没走到验证就早退"的分支
        （no_axes / no_change / no_fig / stale / bad_params / 语法错前）⇒ 没跑过。
    """
    _ly = res.get('layers')
    if _ly is not None:
        return 'ran' if _ly.get('rerun') is not None else 'not_ran'
    if not args.dry_run:
        return 'not_ran'
    if args.verify_fast or args.no_verify or args.no_semantic:
        return 'not_ran'
    # "有没有原位写不了的字段"是这条路上的**真凭据**：auto 先按原位试一次、退块时把
    # 原位那次的结论留档在 res['inplace_needs_block']（apply.py:826）—— 实测回比发生在
    # **原位那次**里，块通道那次不做回比，所以不能只看最终那份 res。
    # 显式 --style block（没有原位那次）不进实测回比 ⇒ 也不该说"可能跑过"。
    if args.style not in ('auto', 'inplace'):
        return 'not_ran'
    return 'may_ran' if (res.get('needs_block')
                         or res.get('inplace_needs_block')) else 'not_ran'


def _t_new(key, fallback, **kw):
    """取词：``messages.t(key)`` 优先，键缺失时退回 ``fallback`` 模板。

    REV1-7（t35）：现在**只剩 verify_layers_* 一族**还走这里 —— 它们的兜底常量
    （``_LAYERS_*``）是 `tests/test_writeback.py` 引用的稳定符号，所以保留这层间接；
    `nothing_applied` / `dryrun_*` / `needs_block_hint` 四个键早已在 messages 表里，
    原先那 4 个 `_FALLBACK_*` 常量是"接了线但永不触发"的死代码，已删。
    注意别用它包"表里没有的新键"：`t()` 的容错是"缺键原样返回键名"，那会打到用户脸上。
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


def _success_tail(res):
    """成功路径收敛行的**后缀**（无感化②，2026-10-13 用户认可）。

    只在 ``verified is True``（重跑层真的跑过且通过）时才给结论：

      * ``semantic is True``  → "；图已更新（重跑 + 语义验证通过）"；
      * 其余（语义未判定/没跑）→ "；已重跑验证" —— **不许**说"图已更新"，
        那是第③层的结论；未判定的披露行随后照旧打印。

    ``verified`` 不是 True（None=没跑 / False=跑失败）时返回空串：此时要么本来就
    没有验证可报（`--no-verify` / `--verify-fast`，随后的分层提示行照旧打印），
    要么是失败/保底保留 —— 那条路径**不收敛**，必须照旧"响"。
    退出码与 `--json` 的字段集不受这里影响。
    """
    if res.get('verified') is not True:
        return ''
    if res.get('semantic') is True:
        return _msg.t('written_tail_ok')
    return _msg.t('written_tail_run')


# ==== §5 参数文件收集与失败错误码 ====

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


# ==== §6 `--json` 机器可读输出（每条失败出口都带稳定码） ====

# ---------------------------------------------------------------------------
# B9（t33 / V1-F4）：`apply --json` 的**每一条失败出口**都要带稳定、可分支的
# `error_code`。值域（全部 ASCII、snake_case、不随文案漂移；与 check/describe/revert
# 同名者复用，agent 可以跨命令统一分支）：
#
#   file_not_found            脚本不存在
#   no_params                 没有参数文件
#   mutually_exclusive_flags  --no-verify 与 --verify-fast 同时给
#   layout_conflict           --strict 下检测到布局引擎冲突，拒绝写回；
#                             默认档下"冲突 + 这次真要写轴位置"也归这一码（D1，0.1.14）
#   bad_params                参数文件结构/取值硬错误（缺 version、顶层非对象、字段非法）
#   stale_params              脚本比参数文件新（套用旧参数会覆盖手改，要 --force）
#   no_axes                   参数里没有可落地的轴/字段
#   no_fig                    找不到 figure/subplots 的赋值目标（纯 pyplot 流）
#   writeback_failed          写回/验证失败（含脚本跑不通的 fail）
#   external_modified         验证期间脚本被外部改过（已回滚，未写回）
#   apply_failed              兜底：出现了 ok 清单之外的 reason（新增 reason 忘登记的信号）
#
# 逐文件的细分原因照旧在 `files[].reason` / `files[].attribution` / `files[].err` 里；
# 顶层 error_code 取**第一个失败文件**的码（多文件批次里"有一份没成"就是没成）。
# 成功载荷**不加**这个键 —— 既有键名/语义、退出码、stdout/stderr 分流一字不变。
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# t16（0.1.18）：三张表**不再各写一份字面量** —— 唯一出处是 `messages.REASONS`
# （reason → kind / error_code / stage 一张表，见那里的长注释）。这里只做派生：
#   * `_OK_REASONS`  = 成功档（ok / best_effort / no_change / preview）
#   * `_FAIL_ERROR_CODE` = reason → 顶层 error_code
#   * `_FAIL_STAGE` = reason → 顶层 stage
# 新增一个 reason 只改 messages.REASONS 一处；漏登记会被 `_failure_code` 的
# `apply_failed` 兜底码照出来（那正是"忘了登记"的信号）。
# ---------------------------------------------------------------------------
_OK_REASONS = _msg.REASONS_OK
_FAIL_ERROR_CODE = dict(_msg.REASON_ERROR_CODE)


def _failure_code(results):
    """顶层 error_code：按**第一个失败文件**的 reason 取码；全成功返回 None。"""
    for _p, _r in results or []:
        _rs = _r.get('reason')
        if _rs in _OK_REASONS:
            continue
        return _FAIL_ERROR_CODE.get(_rs, 'apply_failed')
    return None


# ---------------------------------------------------------------------------
# N3（0.1.15）：顶层 `stage` / `next_step` —— "在哪一步没成" + 一句能**直接复制**的下一步。
#
# 为什么要有（用户 2026-10-24 批准的范围）：0.1.14 让"想写却零改动"不再假成功
# （`ok=false` + `error_code=nothing_applied`），但 agent 拿到码之后仍然不知道下一步
# 该干什么 —— 得去读中文文案。这两个键只**新增**，不改任何既有字段/退出码：
#   * `stage`：值域见 messages.STAGES（params / preflight / backup / writeback /
#     verify / finalize），口径与 `error_code` 一样是**机器分支用的稳定值**；
#   * `next_step`：一句可复制的命令（或一句动作），**是建议不是保证** —— 它按
#     "这条失败最常见的成因"给，工具没义务替你验证它一定跑得通（docs/agent.md 写清）。
# 成功载荷**不带**这两个键（键集与语义不变）。唯一例外（0.1.16 / S2②）：收尾步骤降级
# —— 写回已落盘但刷新参数文件 mtime 失败 —— 成功载荷也带 `stage=finalize` + `next_step`
# （`ok` 仍为 true；见下面的 `_finalize_stage` / `_finalize_next_step`）。
# ---------------------------------------------------------------------------
_FAIL_STAGE = dict(_msg.REASON_STAGE)


def _failure_stage(results):
    """顶层 stage：按**第一个失败文件**判（与 `_failure_code` 同一口径）；无失败 → None。

    `reason='fail'` 有两类：写回自己失败（写盘/语法/锚点）与**写回后的重跑验证**失败。
    后者 agent 该去查的是"我的脚本能不能跑"，不是"写回有没有坏" ⇒ 单独归 `verify`
    （判据复用 §7 的 `_is_rerun_failure`，按 writeback 的稳定信号判、不读文案）。
    """
    for _p, _r in results or []:
        if _r.get('reason') in _OK_REASONS:
            continue
        if _r.get('reason') == 'fail' and _is_rerun_failure(_r):
            return 'verify'
        return _FAIL_STAGE.get(_r.get('reason'), 'writeback')
    if _nothing_applied(results):
        # 写回走完了、盘上一个字节没动（fail-closed 拒写）：坏在 writeback 这一步。
        return 'writeback'
    return None


def _failure_next_step(results, script):
    """顶层 next_step：一句**可直接复制**的下一步；全成功 → None。

    `script` 用绝对路径并总是加双引号（`messages.shell_path`）：agent 拿到后**逐字**
    粘到 shell 里就该能跑，不受它自己的 cwd 与空格路径影响。失败文件自己带了
    `next_step`（验证失败归因那条路已在 0.1.10 算好）就原样透出，不再另编一份。
    """
    _cmd = _msg.shell_path(os.path.abspath(script))
    first = None
    for _p, _r in results or []:
        if _r.get('reason') not in _OK_REASONS:
            first = (_p, _r)
            break
    if first is None:
        if _nothing_applied(results):
            # AST 一处都落不下去（隐式建图 / for 里建图 / 包在函数里）⇒ 改走**手动片段**
            return 'mpltweak apply %s --snippet' % _cmd
        return None
    _p, _r = first
    _reason = _r.get('reason')
    if _reason == 'fail':
        # 验证失败那条路已经算好了具体命令（要参数 / 无头 / 跑不起来各不相同）；
        # 写回自己失败时没有现成命令 ⇒ 指向静态检查（不执行脚本，先分清病因）。
        return _r.get('next_step') or 'mpltweak check %s --static' % _cmd
    if _reason == 'bad_params':
        # 参数文件坏了/越界 ⇒ 重新导出一份（describe 会按脚本现状重算，含 -o 写文件）
        return 'mpltweak describe %s -o %s' % (_cmd,
                                               _msg.shell_path(os.path.abspath(_p)))
    if _reason == 'stale':
        return 'mpltweak apply %s --write --force' % _cmd
    if _reason == 'layout_conflict':
        return 'mpltweak apply %s --write --allow-layout-conflict' % _cmd
    if _reason == 'no_axes':
        # 参数里没有可落地的轴 ⇒ 先看工具到底把这张图认成了几张/哪些轴
        return 'mpltweak describe %s --all-figs' % _cmd
    if _reason == 'no_fig':
        return 'mpltweak apply %s --snippet' % _cmd
    if _reason == _msg.REASON_REFUSED:
        # t16：拒写**不是**"写回坏了"，别再让人去 check --static 查病因 —— 病因是
        # "这些字段按 AST 定位不了"，出路是拿片段手改（与 needs_block_hint / T70 同一条路）。
        return 'mpltweak apply %s --snippet' % _cmd
    if _reason == 'external_modified':
        # 用户保存完了就能原命令重跑（参数此刻仍与代码一致，不需要 --force）
        return 'mpltweak apply %s --write' % _cmd
    return 'mpltweak check %s --static' % _cmd


# J1（0.1.14，独立验证 P1 报的 F1）：`ok=true + applied=false + rc=0` 但**脚本一字节
# 没改** —— 只看 `ok`/`rc` 的 agent 会当成"已写回"。实测形态：隐式建图 / `for` 里建图 /
# 函数里建图 / 类里 `self.ax`（fail-closed 保守拒写）。
# 判据只认"**这次确实想写**却**一处都没落地**"：
#   * `no_change` **不算**（参数与源码本来就一致 = 正常成功，不是失败）；
#   * dry-run 不算（预览本来就不落盘，另有 `dry_run` 字段）；
#   * 部分落地（applied=true）不算（那是真·best_effort）。
# t16（0.1.18）：这类"想写却零落地"的条目，`files[].reason` 已改成 **`refused`**
# （见 `_normalize_reason`）—— 判据这边因此要把它一起算进"尝试过写"，否则整批的
# ok / error_code 会从"零改动"退回"看起来成功"。
# ---------------------------------------------------------------------------
def _nothing_applied(results):
    """整批**零改动**且确实尝试过写 ⇒ True（理由见上一条注释）。"""
    rows = [r for _p, r in results or []]
    if not rows:
        return False
    tried = [r for r in rows
             if r.get('reason') in (_msg.REASON_OK, _msg.REASON_BEST_EFFORT,
                                    _msg.REASON_REFUSED)]
    return bool(tried) and not any(r.get('applied') for r in rows)


# ---------------------------------------------------------------------------
# t16（0.1.18）：**拒写要有自己的 reason**。
#
# 0.1.17 之前 `reason=best_effort` 同时盖着两种结局：
#   * `applied=true`（写了一部分：add_axes 手写、colorbar cax=）；
#   * `applied=false`（**一个字节都没写**：循环建轴、函数封装多次调用）。
# 只看 `reason` 的消费方（agent / 脚本）分不出"写了一部分"和"什么都没写" ——
# 这正是文档里那句「best_effort ≠ 被拒」的现场反例：它当时**同时**意味着被拒。
#
# 修法只做**重命名**，不动任何判定：结果事实（applied 真假）本来就在，缺的只是名字。
# 判据刻意只看 `applied`，不看 pending/changes/warnings —— 那些是"为什么没落地"的解释，
# 不是"落没落地"的事实；用它们判会和写回引擎的内部口径分叉。
# `no_change` 不动：参数与源码本来一致是**正常成功**，不是拒写。
# ---------------------------------------------------------------------------
def _normalize_reason(res):
    """把"想写却一个字节没写"的 `best_effort` 改判成 `refused`（就地改 + 返回）。

    幂等：`refused` 再进来还是 `refused`；`ok`/`no_change`/`preview`/失败档一律不碰。
    """
    if res.get('reason') == _msg.REASON_BEST_EFFORT and not res.get('applied'):
        res['reason'] = _msg.REASON_REFUSED
    return res


# ---------------------------------------------------------------------------
# t16 / t15-F2：`fig_index` 不是整数 ⇒ `bad_params`（t15-F1 的 `pos: null` 曾按"非法值"
# 处理，**已被船长裁决推翻**：`null` = "未请求这个字段"，等同该键不存在 —— 见
# `_has_requested_fields` 的说明）。
#
# F2 的两种旧行为都实测过（0.1.18 改前，四种非整数值 `1.5 / '0' / null / true` 全部
# rc=0 + 写盘）：单图时静默退回 0 照写；某些形态还会错报 `no_fig`（用户以为脚本里没有
# 这张图）。
#
# 为什么放在 apply 侧而不是 `params.validate_errors`：`params.py` 的校验器**不认**这条
# （它只判结构/类型），而本任务的文件面只有 apply/messages/tests —— 在这里合并进同一条
# `_errs` 通道，走的还是既有的 bad_params 出口（rc=2、`not_written` 人读行、
# `error_code=bad_params`），**不新增出口、不改退出码**。
# ---------------------------------------------------------------------------
def _raw_params(path):
    """磁盘上的**原文**结构（`params.load` 之前的样子）；读不到/不是对象 ⇒ None。

    为什么要单独读一遍（本轮踩出来的坑，值得写下来）：`params.load()` 会把**每个已知
    字段补成 None** —— 实测 `{"version":4,"script":"fig.py","axes":[{"index":0,"grid":true}]}`
    读进来 axes[0] 有 18 个键、`pos` 是 `None`。

    所以"用户到底写了哪些字段"只能看**原文**：
      * 拿 load 之后的对象判（例如"有没有可落的字段"），会把**每一份最小参数**都判反
        （engine 的 §T7 夹具 35 条 + `test_writeback` 的 60d/64b/64c 都栽在这上面）；
      * `null` 的口径（船长裁决 F6）：**`null` = "未请求这个字段"**，等同该键不存在 ——
        不写、不报错、不 pending。它不是非法值：`describe` 从不产出 null，
        而手写"只想改标题字号"的最小参数时，其余字段本来就该留空。
    """
    try:
        _text, _enc = params.read_text(path)
        _obj = json.loads(_text)
    except (OSError, ValueError):
        # 读不到 / 解不出来：那是 `params.load` 那条路的事（它会报 bad_params），
        # 这里不重复报、更不许把 I/O 故障说成"参数语义非法"。
        return None
    return _obj if isinstance(_obj, dict) else None


def _has_requested_fields(raw):
    """这份参数**原文**里有没有"已请求"的字段（非 null / 非空容器）⇒ bool。

    判据刻意**通用**（不抄写回引擎的字段清单 —— 那会分叉）：除 `index` 之外，
    任何轴字段只要有非空值就算"请求了"；顶层 `figsize_in` / `figsize_px` 同理。
    用途只有一个：把"这份参数没有任何要改的字段"与 `no_change`（"要求了、但值本来
    就一样"）分开 —— 前者说成后者是**谎报**（见 `apply_no_fields` 的说明）。
    """
    if not isinstance(raw, dict):
        return False
    for _k in ('figsize_in', 'figsize_px'):
        if raw.get(_k) is not None:
            return True
    _axes = raw.get('axes')
    if not isinstance(_axes, list):
        return False
    for _ax in _axes:
        if not isinstance(_ax, dict):
            continue
        for _f, _v in _ax.items():
            if _f == 'index':
                continue
            if _v is None or _v == {} or _v == []:
                continue
            return True
    return False


def _extra_param_errors(path):
    """参数层"语义非法"检查 ⇒ 返回一组**点名到字段**的错误串（空列表 = 没问题）。

    判据落在**磁盘原文**上（见 `_raw_params`），只查一件事：
    `fig_index` **写了**但不是整数（点名收到的值，`repr` 保留类型感：`'0'` vs `0`）。
    `null` 不在这里：那是"未请求"，不是非法值（船长裁决 F6，见 `_raw_params` 说明）。
    """
    raw = _raw_params(path)
    if raw is None:
        return []
    errs = []
    if 'fig_index' in raw:
        _k = raw['fig_index']
        # `bool` 是 `int` 的子类 —— `True` 会被当成图 1，必须显式排除（同 isinstance 陷阱）
        if not isinstance(_k, int) or isinstance(_k, bool):
            errs.append(_msg.t('apply_params_bad_fig_index', got=repr(_k)))
    return errs


# ---------------------------------------------------------------------------
# S2②（0.1.16）：收尾步骤（`stage=finalize`）的**降级** —— 与"写回失败"是两件事。
#
# 场景：写回本身成功、源码已落盘，只有"把参数文件的 mtime 刷新成与代码一致"这一步
# 撞上**外部环境**失败（文件被别的程序占着 / 只读介质 / 杀软扫描）。0.1.15 之前那个
# per-file `os.utime` 裸抛 `OSError`，一路冒到顶层 `guard_json_main`，被报成
# `error_code=internal_error` + `attribution=tool_bug` + 「这是工具的 bug」（rc=2）——
# **一次文件系统失败被归因成工具缺陷**（0.1.15 静默 except 审计 §5 的遗留）。
# 现在：① 走既有 warnings 通道（`mtime_sync_fail`，与批量同步那一句**同源**）；
#      ② `attribution` 说清是哪一步没成（不再说 tool_bug）；
#      ③ 顶层 `stage` 落 `finalize` —— `messages.STAGES` 里那个占位值第一次真用上。
# 不动的：`ok` / `rc`（写回确实落了盘；判"写没写"仍看 applied / changes / reason）。
# ---------------------------------------------------------------------------
ATTRIB_MTIME_SYNC_FAILED = 'mtime_sync_failed'


def _mtime_next_step(script):
    """收尾降级后的下一步：带 `--force` 重跑（参数此刻仍与代码一致，只差时间戳）。"""
    return ('mpltweak apply %s --write --force'
            % _msg.shell_path(os.path.abspath(script)))


# F1 空行 / 纯白行的展示标记（diff 里要看得出"这行确实被动过"）：zh 只用 GBK 码表内的字
# —— cp936 控制台打不出 U+2205/U+2420，一 print 就崩并被兜成 internal_error + 误报工具 bug；
# en 纯 ASCII（V1-F5：英文界面不许出现中文）。两条都不进消息表（那里没有这两条）。
_DIFF_BLANK_MARK = {'zh': ('(空行)', '(纯空白 %d 个字符)'),
                    'en': ('(empty line)', '(whitespace %d chars)')}


def _clip_line(s, n=118):
    """单行 diff 文本的展示截断（末尾补一个省略号，别让长行把终端撑爆）。"""
    s = '' if s is None else str(s).rstrip('\r\n')
    if not s.strip():
        _mk = _DIFF_BLANK_MARK.get(_msg.current_lang()) or _DIFF_BLANK_MARK['zh']
        return _mk[0] if not s else _mk[1] % len(s)
    return s if len(s) <= n else s[:n] + '…'


def _diff_kind_label(t):
    """`files[].diff[].type` → 人读标签。

    三个 `_msg.t(...)` 调用**都写字面量键**：`t('diff_kind_' + 变量)` 会被
    tests/test_msg_key_audit.py 记成"未解析调用点"（那条棘轮只准降不准升）——
    键名永远是这三个，没必要拼串。
    """
    if t == 'inplace':
        return _msg.t('diff_kind_inplace')
    if t == 'insert_block':
        return _msg.t('diff_kind_insert_block')
    return _msg.t('diff_kind_replace')


def _print_preview_diff(script, res, verbose=False):
    """F1（0.1.16）`--diff` 的人读输出：文件 / 行号 / 类型 / 旧行 / 新行。

    纪律：**只说"将要"**，一处"已写回"字样都不许出现（脚本一个字节都没动）；
    条目默认只展开前 8 行，`-v` 全展开 —— 与其它"逐处清单进 -v"的口径一致。
    """
    _rows = res.get('diff') or []
    print(_msg.t('diff_preview_head', script=os.path.basename(script),
                 n=len(_rows), sha=(res.get('preview_sha256') or '')[:12],
                 lines=int(res.get('preview_lines') or 0)))
    _lim = len(_rows) if verbose else 8
    for _r in _rows[:_lim]:
        print(_msg.t('diff_preview_row', line=_r.get('line'),
                     kind=_diff_kind_label(_r.get('type') or 'replace'),
                     old=_clip_line(_r.get('old')), new=_clip_line(_r.get('new'))))
    if not _rows:
        print(_msg.t('diff_preview_same'))
    elif len(_rows) > _lim:
        print(_msg.t('diff_preview_more', n=len(_rows) - _lim))
    print(_msg.t('diff_preview_tail'))


def _finalize_stage(results):
    """收尾步骤降级 → ``'finalize'``；没有降级 → None。

    **唯一会出现在成功载荷里的 stage**：`ok` 仍可以是 true（写回落了盘），这个键在
    这里说的是"最后那一步没干净收尾"。没降级时**不出现在载荷里**（既有契约不变）。
    """
    for _p, _r in results or []:
        if _r.get('finalize_warned'):
            return 'finalize'
    return None


def _finalize_next_step(results, script):
    """收尾降级时的顶层 next_step（与逐文件 `files[].next_step` 同源）。"""
    for _p, _r in results or []:
        if _r.get('finalize_warned'):
            return _mtime_next_step(script)
    return None


def _print_finalize_attribution():
    """人读：收尾降级的归因行 + 下一步。

    走 **stdout**（与 `_print_attribution` 的 stderr 不同）：这一档 `ok`/`rc` 仍是
    成功档，"stderr 空 = 这次没有失败"的口径不能被一条降级提醒打破；它和 0.1.15 那条
    批量 mtime 警告一样属于"报告"而不是"错误"。
    """
    print(_msg.t('fail_attribution_head',
                 kind=_msg.t('fail_kind_mtime_sync_failed'), unknown=''))
    print(_msg.t('fail_next_mtime_sync_failed'))


def _emit_json(script, results, stdout_ref, ok=None, error=None, extra=None,
               preflight_res=None, error_code=None, args_style='auto',
               stage=None, next_step=None):
    """--json 的机器可读输出（每个 return 分支前都要调用一次）。

    ``ok`` 可显式覆盖：没有参数文件时 results 是空的，``not []`` 会算成 True，
    而退出码却是 2 —— 退出码与 ok 字段语义打架（t6-M7）。

    ``extra``：额外平铺进每个 files[] 条目的键（如 ``dry_run``）。``args_style``：只给"条目自己没带 style"兜底用（防以后新增早退路径又漏掉）。

    ``preflight_res``：可重跑预检结果（0.1.10），作为**顶层** ``preflight`` 键给出
    —— agent 在动笔写回之前就能知道"这个脚本重跑会卡在哪"，而不是等失败后靠
    attribution 反推。

    ``stage`` / ``next_step``（0.1.15 / N3）：失败发生在哪一步 + 一句可复制的下一步，
    取值与口径见 `_failure_stage` / `_failure_next_step`。传 None 时这两个键**不出现**
    （成功载荷的键集一个字都不变）。S2② 的收尾降级由 `_finalize_stage` /
    `_finalize_next_step` 供给 —— 那是唯一会让**成功载荷**带上 stage 的情形。
    """
    sys.stdout = stdout_ref
    # t16：成功档判据的**唯一出处**是 `messages.REASONS_OK`（别在这里再写一份元组：
    # 0.1.17 之前这行与 `_OK_REASONS` 各写一份，加 `refused` 时就会漏改一处）。
    _ok = (not [r for _, r in results
                if r['reason'] not in _OK_REASONS]) if ok is None else ok
    # REV1-1（t35）：**零改动**这件事在这里统一兜底，别只靠调用点传 ok/error_code ——
    # 判据只有 `_nothing_applied()` 一处（"确实想写、却一处都没落地"）；dry-run 不算
    # （预览本来就不落盘，另有 dry_run 字段）。漏传的调用点也因此不会退回"看起来成功"。
    _zero = _nothing_applied(results) and not bool((extra or {}).get('dry_run'))
    if ok is None and _zero:
        _ok = False
    if not error_code and _zero:
        error_code = 'nothing_applied'
    _payload = {
        'script': os.path.basename(script),
        'ok': _ok,
        'files': [
            {'params': os.path.basename(p),
             'reason': r.get('reason', 'unknown'),
             'style': r.get('style') or args_style,   # F1：永不为 null（请求的通道；走过写回则是实际通道），口径见 docs/agent.md
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
             # T7（t25）摘掉的字段必须**机器可读**：`delta_dropped` 是字段名清单，
             # `delta` 是计数/原因（{'dropped': n} 或 {'skipped': 'baseline_unavailable'}）。
             # 引擎（writeback）早就返回它们了，是这里的键集没透传（t25 复核发现的）。
             'delta_dropped': r.get('delta_dropped') or [],
             'delta': r.get('delta'),
             # S9（0.1.17）：**照着改**的指导（只在"函数里建图"那两类拒写时出现）。
             # agent 拿到这段就能直接改脚本，不用再从 pending 令牌反推原因。
             'refused_hint': r.get('refused_hint'),
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
             # 0.1.10 外部改动守卫：机器可读通道需要**逐字布尔键**（只有 reason
             # 字符串不够直观）—— agent 一眼就能判断"这次没成是因为用户在验证
             # 期间自己保存过"。正常路径恒为 false（行为不变，只多这一个键）。
             'external_modified': bool(r.get('external_modified')),
             'backup': r.get('backup'),
             'verified': r.get('verified'),
             'verify_mode': r.get('verify_mode'),
             # t45 / t39：这次验证在**写回后的脚本**上跑了几遍，以及语义判定的
             # 依据。两个键**恒存在**（agent 不必再从 warnings 的文本里猜"这次
             # 为什么回退到两遍"）：
             #   * verify_passes：1 = 默认单遍（t39 起）；2 = 单遍取不到目标图
             #     状态 → 已回退两遍；0 = 这次没有为验证跑脚本（--no-verify /
             #     --verify-fast / 预览 / 没有可写字段）。类型恒为 int。
             #   * semantic_basis：'live+savefig' = 图对象状态 + 磁盘那张图的
             #     追加判据；'live' = 只有图对象状态；'' = 这次没有语义判定依据
             #     （没跑 / 回退后如实记"没验到"）。类型恒为 str。
             # 值来自 writeback 的 res（那里可能是 None 或整键缺失），这里只做
             # 类型归一 —— 不改任何行为，退出码与其余字段一律不动。
             'verify_passes': (r.get('verify_passes')
                               if isinstance(r.get('verify_passes'), int)
                               and not isinstance(r.get('verify_passes'), bool)
                               else 0),
             'semantic_basis': (r.get('semantic_basis')
                                if isinstance(r.get('semantic_basis'), str)
                                else ''),
             'semantic': r.get('semantic'),
             'warnings': r.get('warnings') or [],
             'layout_conflicts': r.get('layout_conflicts') or [],
             # REV1-1b（t35）：**判据依据**一栏 —— 只抄 `layout_conflicts`（"哪一行开了
             # 引擎"）不足以让 agent 分支：真正决定拦不拦的是 `layout_why`
             # （no_pos / proved_equal / live_equal / live_differs / unjudged）。
             # 类型归一与 semantic_basis 同款：缺键给 None。
             'layout_why': r.get('layout_why'),
             # 0.1.10：失败归因（script_cannot_run / script_needs_args /
             # script_needs_gui_or_headless_impossible / tool_bug）—— agent 可
             # 据此自动分支，不再只靠读 err 文本猜。next_step 是可直接执行的命令。
             'attribution': r.get('attribution'),
             'attribution_detail': r.get('attribution_detail'),
             # 0.1.16 / F1：`--diff` 的行级差异（每行一条：行号 / 类型 / 旧行 / 新行）。
             # 没开 --diff 时恒为空列表 / None（只加不改：既有键的口径一字不动）。
             # `preview_sha256` = "将要写出的文件"的内容指纹 —— 与真写盘后的文件
             # 逐字节对得上（同源证明就靠它对拍）。
             'diff': r.get('diff') or [],
             'preview_sha256': r.get('preview_sha256'),
             # 同上，但抹掉了调整块说明行里的"生成于 <时间>"：跨分钟也稳定，
             # 是"预览 == 真写盘"的**确定性**对拍口径（严格指纹只在同一分钟内可比）。
             'preview_sha256_norm': r.get('preview_sha256_norm'),
             'preview_bytes': r.get('preview_bytes'),
             'next_step': r.get('next_step')}
            for p, r in results],
    }
    if extra:
        for _f in _payload['files']:
            _f.update(extra)
    if preflight_res is not None:
        _payload['preflight'] = preflight_res
    if error:
        _payload['error'] = error
    if error_code:
        # B9：失败载荷必须能机器分支（值域见 _FAIL_ERROR_CODE 的说明）。成功载荷
        # 不带这个键 —— 键集与语义不变。
        _payload['error_code'] = error_code
    # N3（0.1.15）：只加不改 —— 传了才出现，顺序在 error_code 之后（agent 不必关心顺序）
    if stage:
        _payload['stage'] = stage
    if next_step:
        _payload['next_step'] = next_step
    _msg.emit_json(_payload)


# ==== §7 验证失败归因与下一步（attribution / next_step） ====

# ---------------------------------------------------------------------------
# 失败归因（0.1.10）：验证重跑失败时把"脚本跑不起来"和"工具改坏了"彻底分开报。
#
# 判据在 verify.classify_verify_failure（子进程 stderr 模式匹配 + 脚本静态扫描，
# 保守：判不出归 script_cannot_run 并说明"无法确定原因"）：
#   * res['err'] 含 "stderr 尾部：" 前缀（写回后失败但原脚本可跑通）→ 把尾部
#     stderr 交给分类器做模式匹配；
#   * 否则（原脚本本身也跑不通，writeback 只给了一句笼统文案）→ 只传静态证据，
#     分类器用 ast 扫描脚本（读 argv / 交互标记）保守归类。
# 分类只动输出层：退出码口径见 messages.py 常量区（0.1.11 方案 A 统一为
# 0 / 1 / 2 / 4；3 已退役）——分类不改变任何退出码的数值归属。
# ---------------------------------------------------------------------------
_VERIFY_FAIL_MSG_KEY = {
    _verify.VERIFY_FAIL_CANNOT_RUN: 'fail_next_cannot_run',
    _verify.VERIFY_FAIL_NEEDS_ARGS: 'fail_next_needs_args',
    _verify.VERIFY_FAIL_NEEDS_GUI: 'fail_next_needs_gui',
    _verify.VERIFY_FAIL_TOOL_BUG: 'fail_next_tool_bug',
}
_VERIFY_FAIL_KIND_KEY = {
    _verify.VERIFY_FAIL_CANNOT_RUN: 'fail_kind_cannot_run',
    _verify.VERIFY_FAIL_NEEDS_ARGS: 'fail_kind_needs_args',
    _verify.VERIFY_FAIL_NEEDS_GUI: 'fail_kind_needs_gui',
    _verify.VERIFY_FAIL_TOOL_BUG: 'fail_kind_tool_bug',
}


def _classify_verify_failure(res, script, script_args):
    """从写回结果 res 归因验证失败（纯函数，不重跑脚本）。

    返回 ``(kind, detail)``；kind 取值见 verify.VERIFY_FAIL_* 常量。

    t65：证据**只取 writeback 给的稳定字段**（``verify_fail_tail``），不再从
    ``res['err']`` 里按中文标记（'stderr 尾部：' / '写回后脚本跑不通'）切文案 ——
    文案会随界面语言变（t63 之后 en 下那些标记不存在）。字段缺失（老调用方/单测
    手工构造的 res）时传空串：分类器会退到**静态证据**（ast 扫描脚本）保守归类，
    这也是原本 Case A 的路径。
    """
    tail = res.get('verify_fail_tail') or ''
    kind, detail = _verify.classify_verify_failure(
        tail, script_path=script, args_passed=bool(script_args))
    # 0.1.16：**原脚本能跑、写回后的不能跑**，而 stderr 又不命中任何已知模式 ⇒
    # 证据指着"我们生成的代码有问题"（writeback 只在"原文能跑、改写后的不能跑"时
    # 才给 tail —— 见 VF_AFTER_WRITE / VF_ANCHORS）。归因升级只在这一层做：分类器
    # 自身仍守"三分类、绝不从 stderr 判 tool_bug"的保守契约（test_agent_api §21 ⑦）。
    # 为什么必须说清：旧文案落到「脚本跑不起来（无法确定原因）」⇒ 用户读到"你的脚本
    # 有问题"⇒ 不报告（元组解包那个 bug 就这么躲过 0.1.13/0.1.14/0.1.15）。
    if (kind == _verify.VERIFY_FAIL_CANNOT_RUN and detail == 'unknown'
            and res.get('verify_fail_kind') in (writeback.VF_AFTER_WRITE,
                                                writeback.VF_ANCHORS)):
        return _verify.VERIFY_FAIL_TOOL_BUG, 'generated_code_failed'
    return kind, detail


# t65：哪些 kind 算"验证重跑失败"（要进归因分类）。取值来自 writeback 的稳定信号，
# **不匹配任何语言的文案** —— 旧实现按 err 的中文前缀反推，t63 把 err 翻成 en 之后
# 在 en 下永不成立 ⇒ 归因分类静默失效（人读归因行消失 + --json 的 attribution 变 null）。
_RERUN_FAIL_KINDS = (writeback.VF_HEADLESS, writeback.VF_AFTER_WRITE,
                     writeback.VF_ANCHORS)


def _is_rerun_failure(res):
    """res 是否是"验证重跑失败"（而非语法错误 / 无锚点 / 语义不达标）—— 按稳定信号判。"""
    return res.get('verify_fail_kind') in _RERUN_FAIL_KINDS


# detail 级的文案覆盖（同 kind 下要说不同的话时用）：键 = (kind, detail)。
# 0.1.16：`tool_bug` 有两种来源 —— 顶层 internal_error（工具抛异常）与**生成的代码
# 跑不起来**（原脚本能跑、写回后的不能跑）。后者必须明说"是我们的问题"，不能复用
# internal_error 那句，也不能落到"（无法确定原因）"。
_DETAIL_MSG_KEYS = {
    (_verify.VERIFY_FAIL_TOOL_BUG, 'generated_code_failed'):
        ('fail_kind_generated_code', 'fail_next_generated_code'),
}


def _print_attribution(kind, detail, script):
    """人读输出：归因行 + 一句可照抄的下一步。kind 为 None（分类未启用）→ 静默。

    A3：归因是**失败报告**的一部分 ⇒ 走 stderr（stdout 只留数据/报告）。
    """
    if kind not in _VERIFY_FAIL_KIND_KEY:
        return
    _kk = _DETAIL_MSG_KEYS.get((kind, detail))
    _kind_key, _next_key = (_kk if _kk
                            else (_VERIFY_FAIL_KIND_KEY[kind],
                                  _VERIFY_FAIL_MSG_KEY[kind]))
    _emit_err(_msg.t('fail_attribution_head',
                     kind=_msg.t(_kind_key),
                     unknown=_msg.t('fail_unknown_reason')
                     if detail == 'unknown' else ''),
              _msg.t(_next_key, script=os.path.basename(script)))


def _next_step_cmd(kind, script):
    """--json 的机器可读 next_step：给 agent 可直接执行的具体命令模板。"""
    if kind == _verify.VERIFY_FAIL_NEEDS_ARGS:
        return 'mpltweak apply %s --write -- <your args...>' % os.path.abspath(script)
    if kind in (_verify.VERIFY_FAIL_CANNOT_RUN,
                _verify.VERIFY_FAIL_NEEDS_GUI):
        return 'mpltweak check %s --static' % os.path.abspath(script)
    return None


@_msg.guard_json_main
# ==== §8 主流程：main（预备 → 逐份写回 → 输出） ====

def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    # ==== §8.1 入口：`--` 截断、选项解析与 --json 分流 ====
    # `--` 分隔（bug #6）：`--` 之后的内容是**用户脚本自己的参数**，不是 apply 的
    # 开关 —— 透传给写回后的重跑验证（verify_run / verify_semantic），依赖
    # sys.argv 的脚本（python fig.py --data x.npz）apply --write 时才能与基线
    # 一致地重跑，否则行为不一致 → 默认回滚 + 误归因。
    # 在 parse_args 之前截断：argparse 会把 `--` 后的内容当多余位置参数报错。
    script_args: list = []
    if '--' in argv:
        _sep = argv.index('--')
        script_args = argv[_sep + 1:]
        argv = argv[:_sep]
    ap = argparse.ArgumentParser(prog='mpltweak apply')
    ap.add_argument('script')
    ap.add_argument('--params', default=None)
    ap.add_argument('--snippet', action='store_true',
                    help='额外打印可直接粘贴的纯 matplotlib 片段')
    ap.add_argument('--write', action='store_true',
                    help='AST 确定性写回脚本（零 LLM）')
    ap.add_argument('--no-verify', action='store_true',
                    help='写回后跳过重跑与语义验证（写盘后的语法检查仍会做）：脚本跑不通、'
                         '布局没落到位都不会被发现，也不会自动回滚；这是写坏代码时唯一的'
                         '自动安全网，一般不建议用；仅在脚本确实无法无头重跑时使用，'
                         '用前请先提交到版本控制')
    ap.add_argument('--no-semantic', action='store_true',
                    help='跳过语义验证（默认会重跑并比对目标图的位置/字号等是否真的等于参数）')
    ap.add_argument('--verify-fast', action='store_true',
                    help='快速验证档：只做语法检查（**不重跑脚本**）—— 坏语法会当场回滚，'
                         '但"能跑通"和"布局真的落到目标图上"都不保证。'
                         '适合脚本重跑很贵、且你先要快速看一眼结果的场景')
    ap.add_argument('--strict', action='store_true',
                    help='布局引擎冲突（constrained_layout / autolayout）下**一律**拒绝写回'
                         '（含只改字号的那种）。默认档只在"这次真要写轴位置"时拒绝，'
                         '这条留作更保守的旧口径')
    ap.add_argument('--all-figs', action='store_true',
                    help='循环出图时统一应用到所有迭代（默认只对参数记录的图号加 if 守卫）')
    ap.add_argument('--keep-unverified', action='store_true',
                    help='脚本本身在无头环境跑不通（缺数据/依赖/需要 GUI）时，'
                         '**保留**这次写回（默认是回滚 + 明确失败：那时验证证明不了'
                         '写回是否可用，留下坏写回比失败更糟）')
    ap.add_argument('--allow-layout-conflict', action='store_true',
                    help='明知有布局引擎冲突也照样写回（与 --force 同效：放行"写轴位置"'
                         '那次写回并在输出里声明）')
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
    # 0.1.16 / F1：写回前 diff 预览（先看后改）。默认行为不变 —— 不传这个开关时
    # `--write` 照旧直接写；传了它就**走同一条决策链但不落盘**，只打印"要动哪几行、
    # 从什么改成什么"。--json 下给 files[].diff（机器可读 + preview_sha256 可对拍）。
    ap.add_argument('--diff', '--preview-diff', action='store_true', dest='diff',
                    help='写回前先看差异：引擎照真写回的路跑，只把目标换成同目录影子副本'
                         '（跑完即删，源码一个字节都不动）——逐个文件打印"行号 / 变化'
                         '类型（原位补参数·插入调整块·替换）/ 旧行 / 新行"，--json 下给'
                         'files[].diff + preview_sha256。预览不重跑脚本，所以真写回时'
                         '验证失败触发的回滚不会体现在这里（那一档以真写回为准）')
    ap.add_argument('--json', action='store_true',
                    help='以 JSON 输出结果（给 agent / 脚本消费；过程信息改走 stderr）')
    ap.add_argument('-v', '--verbose', action='store_true',
                    help='展开细节（逐处改动清单 / 备份路径 / 验证分层结论）；'
                         '默认成功只报一行，失败与降级信息一直都在')
    ap.add_argument('--force', action='store_true',
                    help='两处都放行：① 脚本在参数保存之后被改过，也照样套用参数'
                         '（默认会跳过，以免静默覆盖你的手动改动）；'
                         '② 布局引擎冲突下要写轴位置的那次写回（默认拒绝）。'
                         '两处都会在输出里声明"已按 --force 跳过"')
    ap.add_argument('--lang', default=None, choices=('auto', 'zh', 'en'),
                    help='界面语言：auto=跟随系统（默认），或强制 zh / en。'
                         '--json 的错误码始终是英文，不受此影响')
    args = ap.parse_args(argv)

    _msg.set_lang(args.lang)
    # 无感化②：成功路径默认只报一行（含关键数字），细节进 `-v`。
    # 只影响**成功**路径的人读输出：失败 / 回滚 / 降级 / 守卫命中 / 未判定
    # 一律照旧打印，退出码与 `--json` 字段集不变。
    _verbose = bool(getattr(args, 'verbose', False))

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
                       error=_err, error_code='mutually_exclusive_flags',
                       stage='params',
                       next_step=('mpltweak apply %s --write'
                                  % _msg.shell_path(os.path.abspath(args.script))))
        _emit_err(_msg.t('apply_mutex_verify'))
        return 2

    script = os.path.abspath(args.script)
    # ==== §8.2 前置检查：脚本/参数文件、布局引擎冲突、可重跑预检 ====
    # 顺序（方案 A，t58）：**脚本本身是主参数，参数文件是次要产物** —— 脚本不存在时先说
    # “脚本找不到”，再去查参数文件（否则先报 `no params:`，用户以为是自己没调过图）。
    # 与 describe / check / revert 同族：file_not_found 文案 + rc=2。
    if not os.path.exists(script):
        if args.json:
            _emit_json(script, [], _stdout, ok=False,
                       error=_msg.t_en('file_not_found', path=script),
                       error_code='file_not_found', stage='params',
                       next_step=('point it at an existing .py file, then: '
                                  'mpltweak apply %s'
                                  % _msg.shell_path(script)))
        else:
            _emit_err('[apply] %s' % _msg.t('file_not_found', path=script))
        return 2
    # 布局引擎冲突（constrained_layout / figure.autolayout）：位置写回会被引擎在每次
    # 绘制时覆盖。只扫一次，逐份参数文件复用到 res['layout_conflicts']。
    _findings = layoutwarn.scan(script)
    _conflicts = layoutwarn.messages_for(_findings)
    if _conflicts and args.strict and args.write and not args.allow_layout_conflict:
        # --strict：布局引擎会在**每次绘制**时重算轴位置，写回的位置/尺寸会被它覆盖，
        # 所以拒绝写回并给出可操作的办法（关掉引擎 / 用 add_axes 定位 / 显式放行）。
        if args.json:
            _emit_json(script, [], _stdout, ok=False,
                       error='layout conflict (use --allow-layout-conflict to '
                             'override)',
                       error_code='layout_conflict', stage='preflight',
                       next_step=('mpltweak apply %s --write '
                                  '--allow-layout-conflict'
                                  % _msg.shell_path(script)))
        _emit_err(*['[apply] %s' % _l for _l in _conflicts],
                  _msg.t('apply_strict_layout_refused'))
        return 2
    files = _all_params_files(script, args.params)
    if not files:
        _hint = 'no params: %s' % params.params_path(script, args.params)
        _emit_err(_hint)
        if args.json:
            _emit_json(script, [], _stdout, ok=False, error=_hint,
                       error_code='no_params', stage='params',
                       next_step='mpltweak launch %s'
                                 % _msg.shell_path(script))
        return 2
    if len(files) > 1:
        print(_msg.t('multi_files', n=len(files)))

    # 0.1.10 可重跑预检（pre-flight）：**在写回之前**把"这个脚本重跑会卡在哪"讲清楚。
    # 用户视角评审指出的最大留存瓶颈 —— 安全网要求在你的环境里重跑脚本（0.1.10 起
    # 写回默认只重跑 1 遍，取不到目标图状态时才回退 2 遍），而「要参数 / 要联网 /
    # 等人输入 / 要弹窗 / 缺依赖」的脚本
    # 会无声撞墙，用户事后归因成"这工具不行"。
    # 纪律（见 mpltweak.preflight）：纯静态（ast + find_spec，**绝不执行脚本**）、
    # **默认静默**（没风险一个字都不说）、**失败即放行**（预检自身出岔子当无风险）。
    # 只在"这次真的会重跑脚本"时才提示：--no-verify / --verify-fast / --dry-run
    # 都不重跑，说了只是噪音。
    _pf_res = None
    # 0.1.16 / F1：`--diff` 也不重跑脚本（预览只算不跑）⇒ 与 dry-run 同档，预检不说。
    if (args.write and not args.no_verify and not args.verify_fast
            and not args.dry_run and not args.diff):
        _pf_res = preflight.analyze(script, args_passed=bool(script_args))
        for _line in preflight.message_lines(_pf_res, script):
            print(_line)

    results = []
    _applied_files = []
    # D1（0.1.14）：冲突下强行走写回必须**说清**，不许静默 —— 用户得知道"位置写进去了，
    # 但绘制时仍会被引擎覆盖"。只在真的会写盘时打一次（预览/ dry-run 没这回事）。
    if _conflicts and args.write and not args.dry_run and not args.diff \
            and (args.force or args.allow_layout_conflict):
        print(_msg.t('apply_layout_conflict_skipped',
                     flag=('--force' if args.force
                           else '--allow-layout-conflict')))
    # ==== §8.3 批次基准与备份透传（T36 / T23；口径同 writeback §12 备份） ====
    # T36：本批次的"批前原件"备份。多图会话里一次 `apply --write` 会**连着改同一个
    # 脚本好几遍**（每份参数一次），每次写盘都会备一份 —— 固定名 `<stem>.tweak.bak`
    # 于是被后来的写盘**覆盖成中间态**：`revert` 还原出来的是"改了一半"的脚本，不是
    # 用户跑 apply 之前那份（和 L0-4 / inplace→block 完全同一个坑，只是发生在多参数
    # 循环里）。修法：把本批次**第一次**写回产生的备份沿循环逐份透传（与下面
    # inplace→block 的 `_bk` 同一机制）。
    _batch_backup = None
    # F1：预览内容与"磁盘原文"的对拍基准（必须是同一个绝对路径，别比 basename）
    _sabs = os.path.abspath(script)
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
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            # t56：参数文件**自己坏了**（JSON 截断 / 多余逗号 / 编码解不出来）——
            # 这是**用户输入**的问题，与"缺 version"同族 ⇒ 归 bad_params（rc 仍是 2）。
            # 绝不许让它冒到顶层 guard 被报成 internal_error + attribution='tool_bug'：
            # 判不出原因时不许猜"工具 bug"，那会把用户吓去报 issue，而不是修自己的
            # 参数文件（对外披露必须准确）。载荷键名/形状、stdout-stderr 分流都不变。
            _e = _msg.t('apply_params_unreadable', name=os.path.basename(p),
                        err=str(e)[:160])
            _emit_err(_msg.t('apply_params_errors', msg=_e),
                      _msg.t('not_written', reason='bad_params', script=script))
            results.append((p, {'reason': 'bad_params', 'err': _e, 'changes': [], 'style': args.style}))
            continue
        except OSError as e:
            _emit_err(_msg.t('skipped_file', name=os.path.basename(p), err=e))
            continue
        k = data.get('fig_index')
        # ==== §8.4 逐份参数文件：校验与拒写 ====
        # 参数比脚本旧 → 脚本在保存参数之后被手动改过，套用旧参数会**静默覆盖**
        # 那些改动（对照 launch.py 的同类保护；t3-M5 / t6-M8）。
        # 只读预览不拦（它本来就不写文件）。
        if args.write and not args.force and not args.dry_run and not args.diff:
            try:
                _stale = (_script_mtime0 is not None
                          and _script_mtime0 > os.path.getmtime(p) + 1.0)
            except OSError:
                _stale = False
            if _stale:
                _emit_err(_msg.t('apply_stale_params',
                                 name=os.path.basename(p)))
                results.append((p, {'reason': 'stale', 'changes': [],
                                    'block': None, 'backup': None,
                                    'warnings': [], 'style': args.style}))
                continue
        print('=' * 62)
        print(_msg.t('params_file_header', name=os.path.basename(p),
                     fig='' if not isinstance(k, int)
                     else _msg.t('fig_ordinal', n=k + 1)))
        # t56 附带实测发现（同一条路径）：`params.validate_errors` 的**顶层非对象**
        # 分支返回的是**一个字符串**（函数签名写的是 List[str]），于是
        #   * `for prob in params.validate(data)` 会**逐字符**打印（实测 541 行，
        #     每行一个字符）；
        #   * `'; '.join(_errs)` 会把字符用分隔符串起来，JSON 里的 err 变成乱码。
        # 这里在**调用侧**归一成 list（不动 params.py 的返回契约），人读输出与
        # `files[].err` 都恢复成一句话。rc / 键名 / 载荷形状不变。
        _warns = params.validate(data)
        if isinstance(_warns, str):
            _warns = [_warns]
        for prob in _warns:
            print(_msg.t('params_struct_warn', msg=prob))

        # E6：**硬错误**（error 级）在入口就拒写（rc=2）。旧行为是"print 一句警告
        # 后继续跑"，于是脏值一路走到下游：`'ax%-2d' % '0'`、`float('abc')`、
        # `pos[3]`、`int(nan)` 各自炸成 internal_error（连只读预览都崩），
        # 而这里的校验明明已经看出来了。
        _errs = params.validate_errors(data)
        if isinstance(_errs, str):              # 同上：顶层非对象分支返回 str
            _errs = [_errs]
        # t16 / t15-F1、t15-F2：`params.validate_errors` **不认**的两类"JSON 合法、语义
        # 非法"的值（**明文写了** `pos: null` / 非整数 `fig_index`）并进同一条通道 ——
        # 出口、rc、人读行、error_code 全部复用 bad_params 那套，不新增路径。
        # 判据只看**磁盘原文**（`_raw_params`）：load 之后每个字段都被补成 None，
        # 拿它判会把"没写 pos"的最小参数全部误伤。
        _errs = list(_errs) + _extra_param_errors(p)
        if _errs:
            _emit_err(*[_msg.t('apply_params_errors', msg=_e) for _e in _errs],
                      # T68：拒写就是拒写 —— 人读那行也必须是"未写回"+ 原因码，
                      # 不许出现"已写回"。
                      _msg.t('not_written', reason='bad_params', script=script))
            results.append((p, {'reason': 'bad_params',
                                'err': '; '.join(_errs), 'changes': [],
                                'style': args.style}))
            continue

        # t16（船长裁决 F6 的第三行）：整份参数**一个有效字段都没有** ⇒ 明说，别复用
        # `no_change` 那句"参数与原代码一致，无需改动" —— 那是两件事（见 apply_no_fields）。
        # 判据看**原文**：load 之后每个字段都被补成 None，拿它判会永远"没有字段"。
        # 范围刻意只盖"`axes` 声明了、但每根轴的字段全是 null/没写"这一种：
        # **连 axes 都没有的壳参数**仍走写回引擎原有的 no_axes/bad_params 档（那是
        # "这份参数说不清要改哪张图"，rc≠0 —— 与这里 rc=0 是两码事，
        # `tests/test_boundaries.py` 的 missing_axes 就钉着这条）。
        # 走 `print`（stdout）而不是 `_emit_err`：rc 仍是 0、没失败，stderr 必须保持空。
        _raw_p = _raw_params(p)
        if (isinstance(_raw_p, dict) and isinstance(_raw_p.get('axes'), list)
                and _raw_p['axes'] and not _has_requested_fields(_raw_p)):
            print(_msg.t('apply_no_fields'))
            results.append((p, {'reason': _msg.REASON_NO_FIELDS,
                                'changes': [], 'applied': False,
                                'style': args.style}))
            continue

        _print_changes(script, data, p)

        if not args.write and not args.diff:
            if args.snippet:
                print('-' * 62)
                _print_snippet(data)
            # 只读预览也计入结果（给 --json）。`style` 填**请求的模式**：
            # 预览阶段还没决定"这张图最后会不会退到块"，用 'auto' 如实表达"待定"；
            # 之前这里不带 style → 预览时 JSON 里 style=null，与写回时的取值口径不一致（F1）。
            results.append((p, {'reason': 'preview', 'style': args.style}))
            continue

        # ==== §8.4b D1 布局引擎冲突：这次真要写轴位置 ⇒ 默认拒绝（0.1.14，用户拍板 B） ====
        # 为什么拒绝（而不是继续只给 warning）：引擎在**每次绘制**时重算轴位置，
        # 写进源码的位置一定被它覆盖 —— 用户"拖完白拖"，而 0.1.13 拿到的往往只是
        # 写回后"验证不一致 → 自动回滚"的一句莫名结论。所以宁可在写盘之前拦下、把
        # 病因和两条出路说清。
        # 为什么**不是一刀切**：只改字号/线宽/cmap 的那次写回完全不受引擎影响（参数里
        # 根本没有 pos），拦了就是给本来好用的路添堵 —— 判据交给
        # `writeback.position_write_needed`（它只在"这次真的会改位置"时回 True）。
        # dry-run 不写盘 ⇒ 不拦（它本来就是预览）。
        if _conflicts and not args.dry_run and not args.diff \
                and not (args.force or args.allow_layout_conflict):
            if not args.no_verify and not args.verify_fast:
                # 判"这次会不会真改位置"要重跑一次脚本（网格轴的位置只有实测才知道）
                print(_msg.t('apply_verifying', sec=args.timeout))
            _need, _why = writeback.position_write_needed(
                script, data, p, python=args.python, timeout=args.timeout,
                verify=not args.no_verify and not args.verify_fast,
                fast_check=bool(args.verify_fast),
                script_args=script_args)
            if _need is not False:
                # 冲突行只取"哪一行开了什么"（hint=False）：下面已经有专门的两条出路，
                # 再叠一条同样意思的通用建议只会让人看不清重点。
                _lines = ['[apply] %s' % _l
                          for _l in layoutwarn.messages_for(_findings, hint=False)]
                _lines.append(_msg.t('apply_layout_conflict_refused', script=script))
                if _need is None:
                    # 判不了（--no-verify / --verify-fast / 脚本重跑不了）⇒ fail-closed，
                    # 但必须说清"为什么没给你放行"，否则用户以为工具在瞎拦。
                    _lines.append(_msg.t('apply_layout_conflict_unjudged'))
                _lines.append(_msg.t('apply_layout_conflict_ways_out'))
                _emit_err(*_lines)
                # 不写盘、脚本字节不变；退出码走"写回没落地"档（rc=1，见 §8.6）。
                results.append((p, {'reason': 'layout_conflict',
                                    'err': '; '.join(_conflicts),
                                    'changes': [], 'block': None, 'backup': None,
                                    'warnings': [], 'needs_block': False,
                                    'layout_conflicts': _conflicts,
                                    'layout_why': _why, 'style': args.style}))
                continue
        print('-' * 62)
        if not args.no_verify and not args.dry_run and not args.diff:
            # 验证是"子进程重跑整个脚本"：读数据的脚本可能要几分钟，而**期间零输出**，
            # 看起来像卡死（t6-M12）。先给一行预期，至少知道它在干什么。
            print(_msg.t('apply_verifying', sec=args.timeout))
        # --verify-fast **隐含** --no-verify：快速档要的就是"不重跑"。
        # 若 verify 仍为 True，writeback 里 `if fast_check and not verify` 的分支
        # 根本进不去（实测踩过：加了 --verify-fast 却照旧跑了完整验证）。
        # 0.1.16 / F1：`--diff` 钉死"不验证" —— 预览不重跑用户脚本（与 dry-run 同档代价）。
        # 这同时让引擎在**第一个锚点候选**就定稿（writeback 里 `if not verify: break`），
        # 预览内容因此确定可复现，而不是"跑一次一个样"。
        _kw = dict(verify=(not args.no_verify and not args.verify_fast
                           and not args.diff),
                   python=args.python,
                   timeout=args.timeout, dry_run=args.dry_run,
                   semantic=(not args.no_semantic and not args.diff),
                   only_fig=not args.all_figs,
                   fast_check=bool(args.verify_fast),
                   keep_unverified=bool(args.keep_unverified),
                   script_args=script_args)
        # F1：预览把**目标路径**换成同目录影子副本（`writeback.preview_shadow_path`）——
        # 引擎照真写回的路跑（写影子、auto 退块时第二次从影子重读源码），跑完读出来做
        # diff、删掉影子。第一版是"捕获住一个字节都不写"，被独立验证当场打回：
        # auto 退块是两次独立调用、第二次从磁盘重读，不落盘就会多写块内一行
        # （实测同一基线：预览 573 B / 真写 553 B）。影子副本让"文件状态会演进"这件事
        # 与真写回完全一致，这才是内容级保真。
        _sh = None
        _shnew = None      # 影子副本的最终内容（finally 里读出来、删影子）
        if args.diff:
            _sh = writeback.preview_shadow_path(script)
            try:
                shutil.copy2(script, _sh)
            except OSError as _e:
                _emit_err(_msg.t('write_failed', reason='preview', err=_e))
                results.append((p, {'reason': 'fail', 'err': str(_e),
                                    'changes': [], 'style': args.style}))
                continue
            writeback.preview_begin()
        _target = _sh or script
        _lk = None
        if args.write and not args.dry_run and not args.diff:
            # ==== §8.5 写回：串行锁、style 与 auto 回退（口径同 writeback §13 并发锁） ====
            # 串行化写回：并发跑两个 apply --write 时两边都基于同一份原文算替换，
            # 后写者会盖掉先写者，而且都会打印"✓ 已写回"（t5-S4 实测 7/7）。
            try:
                _lk = writeback.script_lock(script)
                _lk.__enter__()
            except RuntimeError as _e:
                _emit_err('!! %s' % _e)
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
            res = writeback.writeback(_target, data, p, style=_first,
                                      backup=_batch_backup, **_kw)
            if args.style == 'auto' and _auto_should_fallback(res):
                # RC-5：块模式那次的结果会**整份覆盖**原位那次 —— 覆盖前先把原位
                # 那次的结论留档，否则 agent 从 --json 里完全看不出"原位为什么没落地"
                # （只能靠 style==block 反推）。
                _ip = {k: res.get(k) for k in ('reason', 'changes', 'warnings',
                                               'needs_block', 'pending',
                                               # S2①（0.1.16）：原位那次"删不掉旧验证
                                               # 状态文件"的留痕要跟着退块走 —— 否则默认
                                               # auto 路径的 --json 里就看不见"这次判定
                                               # 可能不新鲜"（见下面的合并）。
                                               'probe_warnings',
                                               # L32（t26）：**验证凭据**也得跟着走。
                                               # 退块那次若因"值已经改对"走 no_change 早退，
                                               # 它的 res 里**根本没有 layers** ⇒ 外层报
                                               # layers={} / verified=null，而人读却打了
                                               # "正在验证"（同一次运行里自相矛盾）。
                                               # 真凭据在原位那次身上，必须继承。
                                               'layers', 'verified', 'verify_mode',
                                               'verify_passes', 'semantic',
                                               'semantic_basis', 'anchor')}
                # L0-4：原位那次**可能已经写过盘并备过份** —— 退块的第二次写盘必须
                # 复用那份备份，否则固定名备份被覆盖成"改过一次"的中间态，
                # `revert` 拿到的是半成品（主仓实测 20154193__RealSpace.py）。
                _bk = res.get('backup')
                res = writeback.writeback(_target, data, p, style='block',
                                          backup=(_batch_backup or _bk), **_kw)
                res['auto_fallback'] = True
                res['inplace_reason'] = _ip['reason']
                res['inplace_changes'] = _ip['changes'] or []
                res['inplace_warnings'] = _ip['warnings'] or []
                res['inplace_needs_block'] = bool(_ip['needs_block'])
                # L34（t26）：`pending` 是**本次最终没落地**的那一份（一个键，别两套）；
                # `inplace_pending` 只作"退块前原位那次的账"留档 —— 两个键的语义不再
                # 互换（旧行为让 `--style auto` 把真账放进 inplace_pending、`pending`
                # 留空，读 --json 的 agent 会误判"一项都没剩"）。
                res['inplace_pending'] = _ip['pending'] or []
                if not res.get('pending') and res.get('applied') is not True:
                    # 块**什么都写不出去**（no_change/早退）⇒ 原位那份就是最终口径
                    res['pending'] = list(res['inplace_pending'])
                # S2①（0.1.16）：原位那次"删不掉旧验证状态文件"的留痕并进块结果 ——
                # 这条说的是"退/不退块这个判定可能不新鲜"，退块之后更需要看见它。
                if _ip.get('probe_warnings'):
                    res['warnings'] = (list(res.get('warnings') or [])
                                       + list(_ip['probe_warnings']))
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
                # L32（t26）：**块自己没给凭据时才继承**（块真跑了并给了 layers 就以它为准，
                # 那是"写完之后"的更新结果）。**以 layers 为唯一闸门、整组继承**：
                #   * 块的 `layers is None` ⇒ 块这次没走到验证（no_change/早退）⇒
                #     这 7 个字段整组用原位那次的（不逐键混搭，免得凭据来自两次运行）；
                #   * 块的 `layers` 不为 None ⇒ 整组以块为准，一个字段都不碰。
                if res.get('layers') is None:
                    for _k in ('layers', 'verified', 'verify_mode', 'verify_passes',
                               'semantic', 'semantic_basis', 'anchor'):
                        if _ip.get(_k) is not None:
                            res[_k] = _ip[_k]
                # 人读/机器读同一口径：块没跑验证、而我们继承了原位的真凭据 ⇒
                # `_verify_rerun_state` 看的是 `layers['rerun']`，这里已经填好，
                # 那句"本次没有重跑脚本"自然不会再打出来。
        finally:
            if _sh is not None:
                # F1：先把影子读出来（这份就是"真写回会写成什么"），再删影子 +
                # 退出预览态。放在 finally 里 ⇒ 失败/异常路径也不会留下临时文件、
                # 更不会让后续文件的写回被误当成预览。
                try:
                    _shnew = params.read_text(_sh)
                except OSError:
                    _shnew = None
                try:
                    os.remove(_sh)
                except OSError:
                    pass
                writeback.preview_end()
            if _lk is not None:
                _lk.__exit__(None, None, None)
        # T36：记下本批次**第一次**真正做出的备份，后面的每一份写回都复用它
        # （固定名 `.tweak.bak` 从此始终是"批前原件"，`revert` 拿回的是完整原件）。
        if res.get('backup'):
            _batch_backup = _batch_backup or res['backup']
        # ==== §8.5b F1（0.1.16）写回预览：把"影子副本里最终长成的样子"打成行级 diff ====
        # 内容**不是**在这里另算的：引擎刚在影子副本上跑完了与真写回**完全相同**的
        # 决策链（含 auto 退块的第二次调用 —— 它会重读影子，所以块内不会多写一行）。
        if args.diff:
            if res.get('reason') in (_msg.REASON_OK, _msg.REASON_BEST_EFFORT,
                                     _msg.REASON_NO_CHANGE):
                # `params.read_text` 返回 `(text, encoding)` —— 原文与编码都拿到手
                _old, _oldenc = params.read_text(_sabs)
                _new, _enc = (_shnew if _shnew else (_old, _oldenc))
                _rows = writeback.preview_diff(_old, _new)
                _fp = writeback.preview_fingerprint(_new, _enc)
                res['diff'] = _rows
                res['preview_sha256'] = _fp['sha256']
                # 跨分钟也稳定的那一份指纹：调整块的说明行里带"生成于 <日期 时间>"，
                # 预览与真写回**必然**可能落在不同分钟 ⇒ 严格指纹只能同一分钟内对拍。
                # 这一份按引擎自己的 `_norm_ts`（把生成时间抹成 <ts>）算，
                # 用来做**确定性**的"预览 == 真写盘"对拍（tests §114② 用它）。
                res['preview_sha256_norm'] = _fp['sha256_norm']
                res['preview_bytes'] = _fp['bytes']
                res['preview_lines'] = len(_new.splitlines())
                # 归一成"预览"档：rc/ok 与只读预览同档，**且任何分支都不会说"已写回"**
                # （脚本一个字节都没动，applied 必须如实为 False）。
                res['reason'] = 'preview'
                res['applied'] = False
                res['verified'] = None
                res['semantic'] = None
                # 盘上**没有**备份（预览不复制；`_make_backup` 只报了路径）——
                # 不许把一条指向不存在文件的路径当"备份"交给 agent / 用户。
                res['backup'] = None
        if _conflicts and res.get('reason') in (_msg.REASON_OK,
                                                _msg.REASON_BEST_EFFORT,
                                                _msg.REASON_PREVIEW):
            # 只在**这次确实落地/确实有计划**时才提示（no_change / fail / no_fig 不提示，
            # 否则是跟当前动作无关的噪音）。
            # 注意别用 `res['changes']` 判：**块模式**的 changes 是空的（它插的是块，
            # 不是逐字段原位替换），而"块 + 布局引擎"恰恰是最该提示的组合 ——
            # 2026-10-10 端到端冒烟发现漏提示，这里按 reason 判。
            res['layout_conflicts'] = _conflicts
        # t16（0.1.18）：落定 reason 的**最后一步**。必须在这之后才 append / 打印 / 算
        # rc —— 否则 `files[].reason` 说 best_effort、人读行按 refused 说，两处打架。
        # 放在这里也保证"写回引擎内部口径"不被改动（writeback.py 仍报它自己的值，
        # CLI 契约层负责把它翻译成消费方能分支的三种 kind）。
        res = _normalize_reason(res)
        results.append((p, res))
        # E7：`best_effort` 曾经同时覆盖"改到一部分"和"一处都没改"两种情形 ——
        # 一处都没落地时绝不能打印"[OK] 已原位写回"（脚本一个字节都没动）。
        # t16 起后者有自己的名字 `refused`，所以这条分支要把它一起接住（否则拒写的
        # 人读行会掉到"失败档"分支，说得比事实更重或无话可说）。
        _applied = bool(res.get('applied')) and not args.dry_run
        if args.diff and res.get('reason') == 'preview':
            # F1：预览分支排在最前 —— 它一张"已写回"的嘴都不许张（脚本没动）。
            # 失败档（fail / no_fig / bad_params）不走这里，落到下面原有的失败分支，
            # 如实报（预览失败也得让人看见，不能因为加了开关就静音）。
            _print_preview_diff(script, res, verbose=_verbose)
            for w in res.get('warnings', []):
                print(_msg.t('kept_as_is', msg=w))
        elif res['reason'] in (_msg.REASON_OK, _msg.REASON_BEST_EFFORT,
                               _msg.REASON_REFUSED):
            if res.get('style') == 'inplace':
                ch = res.get('changes') or []
                if args.dry_run:
                    # E9：dry-run 下不得出现"已写回"字样（脚本一个字节没动）
                    print(_msg.t('dryrun_inplace', n=len(ch),
                                 items=', '.join(ch)[:160]))
                elif _applied and ch:
                    # 无感化②：默认收成一行（含"几处"这个关键数字），逐处清单进 -v。
                    if _verbose:
                        print(_msg.t('written_inplace', script=script))
                        print(_msg.t('written_inplace_count', n=len(ch),
                                     items=', '.join(ch)[:160]))
                    else:
                        print(_msg.t('written_inplace_summary', n=len(ch),
                                     script=script, tail=_success_tail(res)))
                elif _applied:
                    # T68：`applied=true` 但没有逐字段原位改动记录（改动是块通道做的）——
                    # 文件确实被动过，仍如实说"已写回"，只是没有"原位修改 N 处"可报。
                    if _verbose:
                        print(_msg.t('written_inplace', script=script))
                    else:
                        print(_msg.t('written_inplace_summary_any',
                                     script=script, tail=_success_tail(res)))
                else:
                    # T68：一个字节都没写（applied=false）⇒ 绝不许说"已写回"。
                    # 如实报"未写回"+ 原因码；没能落地的字段由下面的 needs_block 提示列出。
                    _emit_err(_msg.t('not_written', reason=res['reason'],
                                     script=script))
                    if res['reason'] == _msg.REASON_REFUSED:
                        _emit_err(_msg.t('refused_note',
                                         n=len(res.get('pending') or [])))
                    # T70 第 4 条：拒绝时**直接**给可复制的骨架（不要求 --snippet）
                    if res.get('pending') or res.get('warnings'):
                        _print_snippet_fields(data, res.get('pending'), res,
                                              err=True)
                # T15（INT-2 的 B 情形）：显式 --style inplace 时，**原位写不了**的字段
                # 这次没能落地 —— 必须说出来（rc 也会变 1），不能只埋在 warnings 里。
                if res.get('needs_block') and not args.dry_run:
                    _emit_err(_msg.t('needs_block_hint',
                                     items=_msg.t('items_sep').join(
                                         res.get('pending') or [])
                                     or _msg.t('apply_pending_see_warnings')))
            else:
                if args.dry_run:
                    print(_msg.t('dryrun_block', script=script))
                elif _applied:
                    # T68：**只有真的写进文件**才说"已写回"（旧实现只看 reason，
                    # 于是 applied=false 的拒绝路径也报 `[OK] 已写回`）。
                    # 无感化②：默认收成一行；fig 变量名/插入点/figsize 这些细节进 -v。
                    if _verbose:
                        print(_msg.t('written_block', script=script))
                    else:
                        print(_msg.t('written_block_summary', script=script,
                                     tail=_success_tail(res)))
                else:
                    # T68：块没写（例如"运行时轴数证不出 ⇒ 拒绝写位置块"的 fail-closed
                    # 路径）⇒ 如实报"未写回"+ 原因码。
                    _emit_err(_msg.t('not_written', reason=res['reason'],
                                     script=script))
                    if res['reason'] == _msg.REASON_REFUSED:
                        _emit_err(_msg.t('refused_note',
                                         n=len(res.get('pending') or [])))
                    # T70 第 4 条：拒绝时**直接**给可复制的骨架
                    if res.get('pending'):
                        _print_snippet_fields(data, res.get('pending'), res,
                                              err=True)
                if res.get('auto_fallback'):
                    # 降级披露（原位改不了 → 退块）：**不收敛**，两种模式都照打。
                    print(_msg.t('fallback_to_block'))
                if _verbose:
                    print(_msg.t('block_info', fig_var=res.get('fig_var'),
                                 anchor=res.get('anchor'),
                                 figsize=res.get('figsize_edited')))
            for _line in res.get('layout_conflicts', []):
                print(_line)
            if res.get('backup') and _verbose:
                # 备份路径是细节（revert 一条命令自己会找到它）→ 进 -v；
                # 注意"备份失败/没备份"这类异常仍由 writeback 的 warnings 通道响亮报出。
                print(_msg.t('backup_at', path=res['backup']))
            # 降级披露：这次**到底验了哪几层**（T23 / 外部评审条目 3(d) 的"不收敛"）。
            # 判据一律走真凭据（res['layers'] / verify_mode）与档位本身能保证的事实，
            # **不再按 flag 反推"跑没跑"** —— 见 _verify_rerun_state 的说明（§14.2#3）。
            _fast = (res.get('verify_mode') == 'fast') or bool(args.verify_fast)
            if _fast:
                # `--verify-fast` 这一档**从不**为验证重跑脚本（apply 传
                # verify=False / fast_check=True，writeback.py:2965-2977）—— 这是**档位**
                # 事实：dry-run 的早退分支里没有 verify_mode 时也要打对（§20.3#4③）。
                print(_msg.t('verify_fast_note'))
                # T23 / 3(d)：快速档实际只有"语法"这一层 —— 如实说清哪几层没跑，
                # 别让人从"fast"两字反推出"验证过"。
                print(_t_new('verify_layers_fast', _LAYERS_FAST_NOTE))
            else:
                _state = _verify_rerun_state(res, args)
                if _state == 'may_ran':
                    # 真跑了（dry-run 的实测回比那条路）⇒ **不许**说"没有重跑"
                    print(_t_new('verify_layers_dryrun_maybe',
                                 _LAYERS_DRYRUN_MAYBE))
                elif _state == 'not_ran':
                    # 真没跑：`--no-verify` 就点名它，别的档位原因各不相同，别错挂
                    print(_t_new('verify_layers_no_rerun', _LAYERS_NO_RERUN)
                          if args.no_verify
                          else _t_new('verify_layers_no_rerun_plain',
                                      _LAYERS_NO_RERUN_PLAIN))
                _ly = res.get('layers') or {}
                if _ly.get('semantic') is None and not args.no_semantic \
                        and _ly.get('rerun') is not None:
                    print(_t_new('verify_layers_no_semantic',
                                 _LAYERS_NO_SEMANTIC))
            if res.get('verified') is True:
                if _verbose:
                    print(_msg.t('verify_run_ok'))      # 成功确认 → 收敛行已含结论
            elif res.get('verified') is False:
                # 失败必须仍"响" —— 且这是**错误**（验证没通过）⇒ stderr
                _emit_err(_msg.t('verify_run_fail'))
            if res.get('semantic') is True:
                if _verbose:
                    print(_msg.t('semantic_ok'))        # 成功确认 → 收敛行已含结论
            elif 'semantic' in res and res.get('semantic') is None \
                    and not args.no_semantic and not _fast \
                    and res.get('verified') is not True:
                # 什么才算"真跑过但取不到状态"：**键**在 res 里、值为 None，**且**
                # `verified` 不是 True（writeback.py:3062-3069 的"②层没跑成"把
                # verified 降成 None；而 :3070-3076 的"原位没覆盖任何轴字段 ⇒ 有意跳过
                # 语义"那条路 verified 仍是 True，它自己的 warnings 已说清真实原因）。
                # 旧判据只看 `res.get('semantic')` 是不是 None，于是三类路径都被解释成
                # "脚本不存图/跑不通"：
                #   * dry-run 的早退分支（§20.3#4①，实测脚本跑得好好的）；
                #   * `--verify-fast`（语义层从没跑，writeback.py:2965-2977 不设这个键）；
                #   * 有意跳过的分支（:3073）与"一个字段都没落地"的提前返回（标 'skipped'）。
                # 这不是文案问题而是**判据**问题：缺字段 / 值为 None ≠ 判不出来。
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
            if not args.dry_run and not args.diff:
                try:
                    os.utime(p, None)
                except OSError as _e:
                    # S2②：写回**已经落了盘**，失败的只是"刷新这份参数文件的时间戳"
                    # 这一步（外部环境：文件被占 / 只读介质 / 杀软）。0.1.15 之前这里
                    # 直接抛 → 顶层 guard 报 internal_error + tool_bug +「这是工具的
                    # bug」（rc=2）：一次文件系统失败被误归因成工具缺陷。
                    # 现在：走既有 warnings 通道（同一个 `mtime_sync_fail` 键，与下面
                    # 批量同步那一句同源）+ 归因说清是这一步 + 顶层 stage 落 finalize。
                    _w = _msg.t('mtime_sync_fail', path=p, err=_e)
                    res.setdefault('warnings', []).append(_w)
                    res['attribution'] = ATTRIB_MTIME_SYNC_FAILED
                    res['attribution_detail'] = 'mtime_sync_failed'
                    res['next_step'] = _mtime_next_step(script)
                    # `--json` 的顶层 stage 靠这个键判（它本身**不进**载荷，
                    # 见 _emit_json 的显式键集）。
                    res['finalize_warned'] = True
                    print(_msg.t('kept_as_is', msg=_w))
                    _print_finalize_attribution()
                _applied_files.append(p)
        elif res['reason'] == 'no_change':
            print(_msg.t('no_change'))
            for w in res.get('warnings', []):
                print(_msg.t('kept_as_is', msg=w))
        elif res['reason'] == 'no_fig':
            _emit_err(_msg.t('no_fig', err=res['err']),
                      _msg.t('no_fig_hint'))
        elif res['reason'] == 'bad_params':
            # T61：引擎侧的越界判据（轴序号/图号）也归 bad_params 一档 ——
            # 拒绝发生在写盘之前（脚本一个字节没动），人读输出要说清是"参数不对"，
            # 而不是含糊的"写回失败"。
            _emit_err(_msg.t('apply_params_errors_nowrite',
                             msg=res.get('err') or ''))
        else:
            _emit_err(_msg.t('write_failed', reason=res['reason'],
                             err=res['err']))
            # 0.1.10：验证重跑失败 → 归因到脚本侧（跑不起来 / 需要参数 / 无头需
            # GUI）并各给一句可照抄的下一步；internal_error 是另一条路径（顶层
            # guard，归 tool_bug）。语义不达标 / 语法错误 / 无锚点不在此列
            # （_is_rerun_failure 只认重跑失败的三段文案）。
            if _is_rerun_failure(res):
                _kind, _detail = _classify_verify_failure(
                    res, script, script_args)
                res['attribution'] = _kind
                res['attribution_detail'] = _detail
                res['next_step'] = _next_step_cmd(_kind, script)
                _print_attribution(_kind, _detail, script)

    # ==== §8.6 收尾：mtime 同步与人读 / --json 输出 ====
    # T23：把这一批里**落地过**的参数文件互相同步 mtime —— 它们此刻都等于"写回后
    # 的代码状态"，下一次批量写回/开窗就该一致地被判为新鲜（而不是只有最后一份新鲜）。
    if args.write and not args.dry_run and len(_applied_files) > 1:
        _mtime_fail = []
        # 逐文件那一步已经报过归因行就不再重复（同一件事只说一次）
        _finalize_reported = any(_r.get('finalize_warned')
                                 for _p, _r in results)
        for _pf in _applied_files:
            try:
                os.utime(_pf, None)
            except OSError as _e:
                # 0.1.15 静默 except 审计（外部交互档）：过去这里 `pass`。刷不上的那份
                # 参数文件下一次会被判 stale，用户只会看到"莫名其妙让我重新 describe"。
                # 只加一条 warnings（进 --json 的 files[].warnings），rc 与既有字段不动。
                # S2②（0.1.16）：与上面 per-file 那条**同一步骤同一降级** ⇒ 机器口径
                # 也必须一样（attribution + stage=finalize），否则同一件事在 --json 里
                # 有时有 stage 有时没有。
                _mtime_fail.append((_pf, _e))
                for _p2, _r2 in results:
                    if _p2 == _pf:
                        _r2.setdefault('warnings', []).append(
                            _msg.t('mtime_sync_fail', path=_pf, err=_e))
                        _r2['attribution'] = ATTRIB_MTIME_SYNC_FAILED
                        _r2['attribution_detail'] = 'mtime_sync_failed'
                        _r2['next_step'] = _mtime_next_step(script)
                        _r2['finalize_warned'] = True
        for _pf, _e in _mtime_fail:
            print(_msg.t('kept_as_is',
                         msg=_msg.t('mtime_sync_fail', path=_pf, err=_e)))
        if _mtime_fail and not _finalize_reported:
            _print_finalize_attribution()

    if not args.write:
        if any(r.get('reason') == 'preview' for _, r in results):
            print('-' * 62)
            print(_msg.t('apply_preview_hint'))
        if args.json:
            # dry_run 必须能从 JSON 里看出来（预览没落盘，消费方不能当"已写回"）。
            _emit_json(script, results, _stdout,
                       extra={'dry_run': bool(args.dry_run)},
                       preflight_res=_pf_res,
                       error_code=_failure_code(results),
                       args_style=args.style,
                       stage=_failure_stage(results),
                       next_step=_failure_next_step(results, script))
        # 参数文件硬错误（error 级）→ 与写回路径同一档：rc=2（输入/用法错误）
        return 2 if any(r['reason'] == 'bad_params' for _, r in results) else 0

    # t16：`bad` 的判据改成 **kind != success**（唯一出处 messages.REASONS），
    # 于是 `refused` 天然算"没成"（多文件总结里不再把它数进"成功份数"）。
    bad = [r for _, r in results
           if _msg.reason_kind(r['reason']) != _msg.REASON_SUCCESS]
    # T15：显式 --style inplace 时，"有字段必须靠调整块才能落地"= 这次请求没有完全兑现
    # → rc=1（agent 不该从 rc=0 推断"都改好了"）。auto 模式不受影响：needs_block 会让
    # apply 自动退块，字段照样落地（见 _auto_should_fallback）。
    _unlanded = [r for _, r in results
                 if r.get('needs_block') and args.style == 'inplace'
                 and not args.dry_run]
    # J1：整批零改动（确实想写、却一处都没落地）⇒ 不能报 rc=0/ok=true。
    # 这类是"结果需要处理"档（与 stale / needs_block / layout_conflict 同档 = 1），
    # 并在 --json 里给独立 error_code='nothing_applied'。
    _nothing = _nothing_applied(results) and not args.dry_run
    if _nothing:
        for _p, _r in results:
            if not _r.get('applied') and _r.get('reason') in (
                    _msg.REASON_OK, _msg.REASON_BEST_EFFORT,
                    _msg.REASON_REFUSED):
                _emit_err(_msg.t('nothing_applied',
                                 script=os.path.basename(_p)))
    # 参数文件非法 → rc=2（"输入/用法错误"，与 --strict 拒绝、缺参数文件同一档），
    # 让 agent 能区分"你的参数不对"和"写回失败"（审计 E6 的修法）。
    _rc = (2 if any(r['reason'] == 'bad_params' for _, r in results)
           else (1 if (bad or _unlanded or _nothing) else 0))
    if args.json:
        _emit_json(script, results, _stdout, extra={'dry_run': bool(args.dry_run)},
                   preflight_res=_pf_res,
                   # REV1-1（t35）：ok / error_code 的"零改动"覆盖现在由 `_emit_json`
                   # 自己按 `_nothing_applied()` 兜底（单一判据），这里不再各传一份。
                   error_code=_failure_code(results),
                   args_style=args.style,
                   # N3（0.1.15）：在哪一步没成 + 一句可复制的下一步（成功载荷不带）。
                   # S2②（0.1.16）：收尾降级是唯一的例外 —— 写回成功但"刷参数 mtime"
                   # 那一步失败时，成功载荷也带 stage=finalize + next_step（见 _finalize_*）。
                   stage=(_failure_stage(results) or _finalize_stage(results)),
                   next_step=(_failure_next_step(results, script)
                              or _finalize_next_step(results, script)))
    if len(results) > 1:
        # 多份参数文件的收尾总结。**--json** 时必须走 stderr（否则会拼在 JSON 尾巴上，
        # 让 json.loads 报 "Extra data"，t3-H1 / t6-H3）；**非 --json** 时它是报告的
        # 一部分 ⇒ 走 stdout —— A3 起 stderr 只留人读错误，成功场景 stderr 必须是空的
        # （否则 `2>/dev/null` 的用户会平白丢掉这行总结）。
        _multi = ['=' * 62,
                  _msg.t('summary_multi', total=len(results),
                         ok=len(results) - len(bad), bad=len(bad))]
        _stream = sys.stderr if args.json else sys.stdout
        for _l in _multi:
            _stream.write('%s\n' % (_l,))
        _stream.flush()
    return _rc


if __name__ == '__main__':
    sys.exit(main())

# --- BEGIN AUTO NAV ---
# 自动生成，不要手改：`python tools/gen_nav.py --write` 重算、`--check` 校验
# （tests/test_hygiene.py 有一条同判据的用例）。行号 = 该 def / 小节的**定义行**（1-based）。
# 索引块固定放在文件末尾：它自己不占用任何函数之前的行，重算不会挤动行号。
# 缩进一级的 §N.M 是**类内/函数内小节**（挂在包含它的顶层定义或它的大号分区下）。
# §1 文件头：apply 的职责与两条输出（人读 / --json）（行 2-67，0 个顶层函数）
#     （本分区没有顶层函数）
# §2 人读输出：行格式、改动清单与可复制骨架（行 68-342，10 个顶层函数）
#     _fmt                                            70
#     _num                                            78
#     _axlabel                                        89
#     _emit_err                                       98
#     _print_changes                                 124
#     _snippet_line                                  226
#     _snippet_refs                                  262
#     _snip_val                                      282
#     _print_snippet_fields                          291
#     _print_snippet                                 334
# §3 兜底文案与「验了哪几层」的说明（行 343-371，0 个顶层函数）
#     （本分区没有顶层函数）
# §4 验证重跑判定与 auto 回退（口径同 writeback §22 验证）（行 372-466，4 个顶层函数）
#     _verify_rerun_state                            374
#     _t_new                                         412
#     _auto_should_fallback                          426
#     _success_tail                                  446
# §5 参数文件收集与失败错误码（行 467-504，1 个顶层函数）
#     _all_params_files                              469
# §6 `--json` 机器可读输出（每条失败出口都带稳定码）（行 505-1017，16 个顶层函数）
#     _failure_code                                  542
#     _failure_stage                                 569
#     _failure_next_step                             588
#     _nothing_applied                               646
#     _normalize_reason                              671
#     _raw_params                                    695
#     _has_requested_fields                          719
#     _extra_param_errors                            747
#     _mtime_next_step                               782
#     _clip_line                                     795
#     _diff_kind_label                               804
#     _print_preview_diff                            818
#     _finalize_stage                                840
#     _finalize_next_step                            852
#     _print_finalize_attribution                    860
#     _emit_json                                     872
# §7 验证失败归因与下一步（attribution / next_step）（行 1018-1123，4 个顶层函数）
#     _classify_verify_failure                      1046
#     _is_rerun_failure                             1080
#     _print_attribution                            1095
#     _next_step_cmd                                1113
# §8 主流程：main（预备 → 逐份写回 → 输出）（行 1124-1981，1 个顶层函数）
#     main                                          1126
#         §8.1 入口：`--` 截断、选项解析与 --json 分流           1128
#         §8.2 前置检查：脚本/参数文件、布局引擎冲突、可重跑预检            1230
#         §8.3 批次基准与备份透传（T36 / T23；口径同 writeback §12 备份）  1302
#         §8.4 逐份参数文件：校验与拒写                         1339
#         §8.5 写回：串行锁、style 与 auto 回退（口径同 writeback §13 并发锁）  1505
#         §8.6 收尾：mtime 同步与人读 / --json 输出           1878
# --- END AUTO NAV ---
