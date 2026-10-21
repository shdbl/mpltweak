# -*- coding: utf-8 -*-
"""对外消息表与语言检测。

**为什么分两层**：

- **人类可读输出**（终端里那几行字）：跟随系统语言最友好，可以用
  ``--lang`` / ``MPLTWEAK_LANG`` 覆盖。
- **机器可读输出**（``--json``）：永远用**稳定英文 + 结构化错误码**，不随系统
  语言变。否则同一条命令在中文机器和英文机器上输出不同，agent 的解析会崩。

**另一个必须守住的约束**：所有面向终端的字符串都必须是 **GBK 可编码**的。
中国 Windows 上 stdout 被捕获时编码是 cp936 且 ``errors='strict'``，
一个 U+2713（✓）就能让"写回成功"变成 `rc=1 + UnicodeEncodeError`，
而且崩在写回**落盘之后**——用户看到的是失败，磁盘上其实已经改了（t4-F2 / t6-H1）。
所以这里一律用 ASCII 标记（``[OK]`` / ``[X]``）而不是花体符号。
"""
import locale
import os
import sys

LANGS = ('zh', 'en')
_DEFAULT = 'en'
_lang = None

# ---------------------------------------------------------------------------
# 退出码矩阵（0.1.9 统一，CH-6）——**所有子命令都从这里取**，别再各写各的字面量。
#
#   0  OK         什么都没发现 / 动作成功
#   1  PROBLEM    "确实有问题"：check 检出了排版问题（这是唯一该用 1 的场景）
#   2  USAGE      "跑不起来"：参数非法、**脚本找不到**（所有入口统一）、**脚本自己报错**、
#                 **顶层未知参数**、工具自身异常 —— 方案 A（0.1.11）把这几类全部归 2
#   3  SCRIPT     **已退役**（方案 A，0.1.11）：常量保留（外部可能 `from mpltweak.messages
#                 import EXIT_SCRIPT`），但**任何路径都不再返回 3**。历史上它背着三件事
#                 （找不到脚本 / 脚本报错 / 顶层未知参数），现已全部并入 2。
#   4  BACKEND    找不到可用的图形后端（开窗）
#
# 为什么把「工具自身异常」也归 2 而不是 1：CI 里 1 代表"要去修图"，
# 工具自己炸了却报 1，用户会去看图找问题——找不到，因为问题在工具里（CH-6）。
#
# 0.1.10 起：同为 2，输出会把失败**归因**到脚本侧（跑不起来 / 需要参数 / 无头
# 跑不通需 GUI）还是工具侧（internal_error）分开报 —— 每类各给一句可照抄的
# 下一步；--json 用 `attribution` 字段区分（agent 可据此自动分支）。
# 方案 A（0.1.11，用户拍板）：对外口径收敛为 **0 / 1 / 2 / 4**；3 退役。
# 3→2 **不是软化**（仍是非零失败、不会把错误当成功），而是把"同一个脚本找不到"
# 在顶层与子命令之间的分歧抹平（0.1.9 起子命令早已是 2）。
# ---------------------------------------------------------------------------
EXIT_OK = 0
EXIT_PROBLEM = 1
EXIT_USAGE = 2
EXIT_INTERNAL = 2
EXIT_SCRIPT = 3   # 已退役（方案 A）：保留常量兼容外部 import；任何路径都不再返回它


def detect_lang():
    """按优先级推断界面语言：--lang > MPLTWEAK_LANG > locale > LANG/LC_ALL > en。

    本机实测（中国 Windows）：``locale.getdefaultlocale()`` 给 ``('zh_CN','cp936')``，
    而 ``LANG`` / ``LC_ALL`` / ``LC_CTYPE`` 全都是 None（Windows 没有这些变量）。
    """
    for var in ('MPLTWEAK_LANG', 'LANG', 'LC_ALL', 'LC_CTYPE'):
        v = os.environ.get(var)
        if v:
            v = v.strip().lower()
            if v.startswith('zh') or 'chinese' in v or v.startswith('cn'):
                return 'zh'
            if v[:2] in LANGS:
                return v[:2]
            return _DEFAULT
    loc = ''
    try:
        loc = (locale.getlocale()[0] or '')
    except Exception:                             # noqa: BLE001
        loc = ''
    if not loc:
        try:
            loc = (locale.getdefaultlocale()[0] or '')   # 3.10 起弃用但 Windows 最稳
        except Exception:                                # noqa: BLE001
            loc = ''
    loc = loc.lower()
    if loc.startswith('zh') or 'chinese' in loc:
        return 'zh'
    return _DEFAULT


def set_lang(lang):
    """显式设定语言（``--lang`` 用）。传 None / 'auto' 表示回到自动探测。"""
    global _lang
    _lang = None if lang in (None, '', 'auto') else (lang if lang in LANGS
                                                     else _DEFAULT)
    return current_lang()


def current_lang():
    global _lang
    if _lang is None:
        _lang = detect_lang()
    return _lang


# ---------------------------------------------------------------------------
# 消息表：key -> {语言: 模板}。模板用 % 插值（与项目既有风格一致）。
# 约定：key 同时就是**稳定的机器可读错误码**（英文小写），--json 直接用它，
# 不翻译、不受系统语言影响。
# ---------------------------------------------------------------------------
MESSAGES = {
    # --- apply：写回结果 ---
    'written_inplace': {
        'zh': '[OK] 已原位写回: %(script)s',
        'en': '[OK] Written back in place: %(script)s',
    },
    'written_inplace_count': {
        'zh': '  原位修改 %(n)d 处: %(items)s',
        'en': '  %(n)d in-place edits: %(items)s',
    },
    # 无感化（0.1.10，用户 2026-10-13 认可）：**成功路径收成一行**，细节（逐处清单 /
    # 备份路径 / 验证分层结论）挪到 `-v/--verbose`。
    # 约束（红线）：这一行必须仍然如实说明"验证到哪一层" —— 只有 verified 与
    # semantic 都真为 true 才说"图已更新"；verified 为 None（未跑）时后缀留空、
    # 为 True 而 semantic 未判定时只说"已重跑验证"，让下面那些"未判定 / 没跑 /
    # 保底保留"的披露行照旧出现。**失败与降级路径一个字都不收敛。**
    'written_inplace_summary': {
        'zh': '[OK] 已原位写回 %(n)d 处（%(script)s）%(tail)s',
        'en': '[OK] wrote back %(n)d edit(s) in place (%(script)s)%(tail)s',
    },
    'written_inplace_summary_any': {
        # applied=true 但没有逐字段原位记录（改动是块通道做的）：别丢了"已写回"。
        'zh': '[OK] 已原位写回（%(script)s）%(tail)s',
        'en': '[OK] wrote back in place (%(script)s)%(tail)s',
    },
    'written_tail_ok': {
        'zh': '；图已更新（重跑 + 语义验证通过）',
        'en': '; figure updated (re-run + semantic check passed)',
    },
    'written_tail_run': {
        # 只重跑过、语义层没给出结论 —— 不许说"图已更新"（那是语义层的结论）。
        'zh': '；已重跑验证',
        'en': '; re-run verified',
    },
    'written_block': {
        'zh': '[OK] 已写回: %(script)s',
        'en': '[OK] Written back: %(script)s',
    },
    'written_block_summary': {
        'zh': '[OK] 已写回（插入调整块，%(script)s）%(tail)s',
        'en': '[OK] written back (adjustment block inserted, %(script)s)%(tail)s',
    },
    # T68：**没写盘**的人读行。块通道旧实现只看 reason 不看 applied，于是"拒绝写回"
    # （applied=false，例如位置块目标轴证不出而 fail-closed）也打 `[OK] 已写回` ——
    # JSON 一直是诚实的，只有这一行人读不诚实。这里如实说"未写回"+ 原因码。
    'snippet_head': {
        'zh': '# --- 手动落实片段（可选；粘贴后把 <ax> / <fig> / <mappable> 换成你自己的变量名）---',
        'en': '# --- Manual snippet (optional; replace <ax> / <fig> / <mappable> with your own names) ---',
    },
    'snippet_provable_head': {
        'zh': '# --- 想让它自动落地？给 figure/axes 起个名字，再运行一次本工具 ---',
        'en': '# --- Want this applied automatically? Give the figure/axes a name, then run once more ---',
    },
    'snippet_provable_subplots': {
        'zh': '#   fig, ax = plt.subplots(...)      # 一次拿到 figure 和 axes，工具就能把改动落到目标上',
        'en': '#   fig, ax = plt.subplots(...)      # grabs figure and axes at once, so the change can land',
    },
    'snippet_provable_figure': {
        'zh': '#   fig = plt.figure(...)            # 现在是 plt.figure(...)（没接名字）',
        'en': '#   fig = plt.figure(...)            # currently plt.figure(...) (nothing is bound)',
    },
    'snippet_provable_colorbar': {
        'zh': '#   cbar = fig.colorbar(im, ax=ax)   # 色条接上名字，它那条轴才可证',
        'en': '#   cbar = fig.colorbar(im, ax=ax)   # bind the colorbar so its axes is provable',
    },
    'not_written': {
        'zh': '[--] 未写回[%(reason)s]：脚本未被改动（%(script)s）',
        'en': '[--] Not written back [%(reason)s]: file unchanged (%(script)s)',
    },
    'fallback_to_block': {
        'zh': '  · 这张图原位改不了（代码里没有可改的数字）→ 改用插入调整块',
        'en': '  - no numbers to edit in this figure -> inserting an '
              'adjustment block instead',
    },
    'block_info': {
        'zh': '  fig 变量名 = %(fig_var)s   插入点 = %(anchor)s   '
              'figsize 原位替换 = %(figsize)s',
        'en': '  fig var = %(fig_var)s   anchor = %(anchor)s   '
              'figsize replaced in place = %(figsize)s',
    },
    'backup_at': {
        'zh': '  备份: %(path)s',
        'en': '  backup: %(path)s',
    },
    'verify_run_ok': {
        'zh': '  [OK] Agg 重跑验证通过',
        'en': '  [OK] Agg re-run check passed',
    },
    'verify_fast_note': {
        # 快速档的**契约原话**：说清它保证什么、不保证什么。
        # 宁可啰嗦，也不能让用户以为"验证过了"（那是这个项目最容易犯的错）。
        'zh': '  [!] 快速验证：只检查了语法（源码能被 Python 解析，写坏会当场回滚）。'
              '**没有重跑脚本** —— 不保证能跑通、也不保证布局真的落到目标图上。'
              '正式落实请去掉 --verify-fast',
        'en': '  [!] Fast check: syntax only (the file still parses; a broken '
              'edit is rolled back). The script was NOT re-run - this does not '
              'prove it runs or that the layout actually landed. Drop '
              '--verify-fast for a real check',
    },
    'verify_run_fail': {
        'zh': '  [X] Agg 重跑验证失败（已回滚到备份）',
        'en': '  [X] Agg re-run check failed (rolled back to backup)',
    },
    # ---- 失败归因（0.1.10）：把"脚本跑不起来"和"工具改坏了"分开报，每类一句下一步 ----
    'fail_attribution_head': {
        'zh': '  归因：%(kind)s%(unknown)s',
        'en': '  Attribution: %(kind)s%(unknown)s',
    },
    'fail_kind_cannot_run': {
        'zh': '脚本跑不起来',
        'en': 'script cannot run',
    },
    'fail_kind_needs_args': {
        'zh': '脚本需要命令行参数',
        'en': 'script needs command-line arguments',
    },
    'fail_kind_needs_gui': {
        'zh': '无头环境跑不通（需要窗口/交互）',
        'en': 'cannot run headless (needs a window / interaction)',
    },
    'fail_kind_tool_bug': {
        'zh': '工具自身异常（internal_error）',
        'en': 'internal tool error (internal_error)',
    },
    'fail_unknown_reason': {
        'zh': '（无法确定原因）',
        'en': ' (reason could not be determined)',
    },
    'fail_next_cannot_run': {
        'zh': '  工具没有改动你的源码（已回滚）。下一步：`mpltweak check %(script)s --static`'
              '（不执行脚本，只做静态检查）',
        'en': '  Your source was not modified (rolled back). Next: run '
              '`mpltweak check %(script)s --static` (static check only, '
              'does not run the script)',
    },
    'fail_next_needs_args': {
        'zh': '  你的脚本需要参数。下一步：`mpltweak apply %(script)s --write -- <你的参数...>`'
              '（`--` 后的参数会原样透传给重跑验证）',
        'en': '  Your script expects command-line arguments. Next: run '
              '`mpltweak apply %(script)s --write -- <your args...>` (everything '
              'after `--` is passed verbatim to the verification re-run)',
    },
    # 你**已经**传过参数了，重跑还是 argparse 报错 → 别再叫他"去传参数"（那会
    # 变成第二轮"归因错误"）。这时只能指向静态检查，并说明参数已透传。
    'fail_next_needs_args_configured': {
        'zh': '  你的脚本需要命令行参数，本次参数**已经透传**给重跑验证，但它仍然'
              '报用法错误。下一步：先用 `mpltweak check %(script)s --static`'
              '（不执行脚本）确认脚本本身没有第二处缺参，或换一份参数再试',
        'en': '  Your script takes command-line arguments. This run DID pass your '
              'arguments through to the verification re-run, yet it still failed '
              'with a usage error. Next: use `mpltweak check %(script)s --static` '
              '(does not run the script) to confirm there is no second missing '
              'argument, or retry with different arguments',
    },
    'fail_next_needs_gui': {
        'zh': '  脚本在无头环境跑不通（要开窗口/交互）。下一步：改用 '
              '`mpltweak check %(script)s --static`；确要写回可用 `--verify-fast`'
              '（只查语法，弱保证）或 `--keep-unverified`（保留写回，请先用版本控制）',
        'en': '  The script cannot run headless (needs a window / interaction). '
              'Next: use `mpltweak check %(script)s --static`; to write back anyway, '
              'use `--verify-fast` (syntax only, weak guarantee) or '
              '`--keep-unverified` (keep the write; commit to version control first)',
    },
    'fail_next_tool_bug': {
        'zh': '  你的源码一个字节都未修改（已回滚）。这是工具的 bug，请把这段输出报告给我们。',
        'en': '  Your source was not modified at all (rolled back). This is a bug '
              'in the tool - please report this output to us.',
    },
    'semantic_ok': {
        'zh': '  [OK] 语义验证通过（目标图状态 == 参数）',
        'en': '  [OK] Semantic check passed (target figure matches params)',
    },
    'semantic_unknown': {
        'zh': '  · 语义验证未能判定（脚本不存图/跑不通），仅按"能跑通"判定',
        'en': '  - semantic check inconclusive (script does not save figures '
              'or cannot run); judged by "runs" only',
    },
    'kept_as_is': {
        'zh': '  · 保持原样: %(msg)s',
        'en': '  - kept as-is: %(msg)s',
    },
    'dry_run_inplace': {
        'zh': '  [dry-run] 未落盘；原位修改预览：',
        'en': '  [dry-run] nothing written; in-place preview:',
    },
    'dry_run_block': {
        'zh': '  [dry-run] 未落盘；生成的调整块：',
        'en': '  [dry-run] nothing written; generated block:',
    },
    # --- 跨组约定键（T7/T11 在 apply.py 里接的，键名按船长约定：无下划线）---
    # 注意：与上面 `dry_run_inplace`/`dry_run_block`（**带**下划线）不是一回事 ——
    # 那一组是"把预览块正文打出来"，这一组是"结尾的落地小结"。两组并存。
    # 参数签名由调用方（apply.py）定：nothing_applied(script) /
    # dryrun_inplace(n, items) / dryrun_block(script)。
    'dryrun_inplace': {
        'zh': '[预览] 将原位修改 %(n)d 处: %(items)s（未落盘）',
        'en': '[dry-run] would edit %(n)d place(s) in place: %(items)s '
              '(nothing written)',
    },
    'dryrun_block': {
        'zh': '[预览] 将插入调整块: %(script)s（未落盘）',
        'en': '[dry-run] would insert an auto-layout block: %(script)s '
              '(nothing written)',
    },
    'nothing_applied': {
        'zh': '[--] 本次没有可落地的字段：脚本未被改动（%(script)s）',
        'en': '[--] nothing could be applied; the script was left untouched '
              '(%(script)s)',
    },
    'no_change': {
        'zh': '· 参数与原代码一致，无需改动',
        'en': '- params already match the code; nothing to change',
    },
    'no_fig': {
        'zh': '[X] 无法确定性写回: %(err)s',
        'en': '[X] cannot rewrite deterministically: %(err)s',
    },
    'no_fig_hint': {
        'zh': '  建议：由 AI 按脚本风格落实，或 --snippet 拿片段手动粘贴',
        'en': '  suggestion: let an AI apply it in your style, or use '
              '--snippet to paste manually',
    },
    'write_failed': {
        'zh': '[X] 写回失败[%(reason)s]: %(err)s',
        'en': '[X] write-back failed [%(reason)s]: %(err)s',
    },
    # 外部改动守卫（0.1.10）：验证窗口可能长达几分钟，用户在编辑器里保存过一次，
    # 工具的后续落盘（回滚 / 块重试）绝不能把它抹掉 —— 停手 + 说清"你的改动还在"。
    'external_modified': {
        'zh': '检测到脚本在验证期间被外部修改；本次写回已放弃，你的改动未被覆盖。'
              '若仍要写回，请先保存并重跑本命令',
        'en': 'the script was modified externally during verification; this '
              'write-back was abandoned and your changes were NOT overwritten. '
              'Save first and re-run the command if you still want to write back',
    },
    'summary_multi': {
        'zh': '多图落实小结：共 %(total)d 张，成功 %(ok)d，失败 %(bad)d',
        'en': 'multi-figure summary: %(total)d total, %(ok)d ok, %(bad)d failed',
    },
    'skipped_file': {
        'zh': '!! 跳过 %(name)s: %(err)s',
        'en': '!! skipping %(name)s: %(err)s',
    },
    'params_file_header': {
        'zh': '# %(name)s%(fig)s',
        'en': '# %(name)s%(fig)s',
    },
    'fig_ordinal': {
        'zh': '  （第 %(n)d 张图）',
        'en': '  (figure %(n)d)',
    },
    'params_struct_warn': {
        'zh': '!! 参数文件结构警告: %(msg)s',
        'en': '!! params file structure warning: %(msg)s',
    },
    'multi_files': {
        'zh': '发现 %(n)d 份参数文件（一次会话改过多张图）——逐张落实：',
        'en': 'found %(n)d params files (several figures edited in one '
              'session) - applying one by one:',
    },
    'no_params': {
        'zh': '未找到参数文件: %(path)s',
        'en': 'no params found: %(path)s',
    },
    'busy_other_apply': {
        'zh': '!! 另一个 mpltweak 写回正在进行（pid=%(pid)s），已等待 %(sec)s 秒仍'
              '未释放。若确认它已不在运行，请手动删除 %(lock)s',
        'en': '!! another mpltweak write-back is running (pid=%(pid)s); waited '
              '%(sec)s s. If it is really gone, delete %(lock)s manually',
    },
    # --- launch ---
    'lang_hint': {
        'zh': '[launch] 提示：请用英文输入法（中文输入法下 c/s/g/x/y 等字母'
              '快捷键会直接上屏）',
        'en': '[launch] tip: use an English keyboard layout (with a Chinese IME '
              'active, letter shortcuts like c/s/g/x/y get typed into the IME)',
    },
    'stale_params': {
        'zh': '[launch] 注意：脚本在参数保存之后被修改过 → 本次不套用该参数'
              '（避免覆盖你的手动改动）；确要续调加 --force-resume',
        'en': '[launch] note: the script is newer than the saved params, so they '
              'are not applied (to avoid overwriting your manual edits); '
              'add --force-resume to continue anyway',
    },
    'no_backend': {
        'zh': '[launch] 找不到可用的交互后端（试过 QtAgg/Qt5Agg/Qt6Agg/TkAgg）：'
              '%(err)s\n'
              '[launch] 开窗调图需要一个图形后端，任选其一：\n'
              '           pip install "mpltweak[qt]"     # 推荐（装 PyQt5）\n'
              '           # Linux 也可以用系统包管理器装 python3-tk\n'
              '[launch] 若你只需要命令行用法（describe / check / apply / mcp），\n'
              '         则不需要图形后端，那些命令照常可用。\n',
        'en': '[launch] no usable interactive backend (tried '
              'QtAgg/Qt5Agg/Qt6Agg/TkAgg): %(err)s\n'
              '[launch] opening the window needs a GUI backend; pick one:\n'
              '           pip install "mpltweak[qt]"     # recommended (PyQt5)\n'
              '           # on Linux you can also install python3-tk\n'
              '[launch] if you only need the CLI (describe / check / apply / '
              'mcp),\n'
              '         no GUI backend is required - those keep working.\n',
    },
    'all_windows_closed': {
        'zh': '[launch] 共 %(n)d 个窗口，全部关闭后才退出',
        'en': '[launch] %(n)d window(s) - the process exits after all are closed',
    },
    # --- 布局引擎冲突（constrained_layout / autolayout）---
    'layout_engine_conflict': {
        'zh': '[!] 脚本第 %(line)d 行启用了 %(how)s：布局引擎会在**每次绘制**时'
              '重算轴位置，拖拽/写回的位置改动会被它覆盖'
              '（表现是"验证不一致"，而不是位置没改）。',
        'en': '[!] line %(line)d enables %(how)s: the layout engine recomputes '
              'axes positions on **every draw**, so dragged/written-back '
              'positions get overridden (it shows up as "verification '
              'mismatch", not as "position not written").',
    },
    'layout_engine_hint': {
        'zh': '    建议：调图前先关掉它（删掉该参数，或改成 layout=None），'
              '或把要精调的轴改成 add_axes([...]) 定位 —— 引擎不管这类轴。',
        'en': '    suggestion: turn it off before tweaking (drop the argument, '
              'or set layout=None), or position the axes you care about with '
              'add_axes([...]) - the engine does not manage those.',
    },
    'script_failed': {
        'zh': '[launch] 用户脚本执行出错，已中止',
        'en': '[launch] the script raised an error; aborted',
    },
    'no_interactive_fig': {
        'zh': '[launch] 脚本没有留下可交互的图（可能已 close 或只存盘）',
        'en': '[launch] the script left no interactive figure (maybe closed or '
              'save-only)',
    },
    # --- 写进用户脚本里的东西（也必须语言化；哨兵本身除外）---
    'block_comment': {
        'zh': '# 本块由 mpltweak 自动生成于 %(ts)s（可手动微调，勿删上下两行标记）',
        'en': '# auto-generated by mpltweak at %(ts)s (feel free to tweak; '
              'keep the marker lines above and below)',
    },
    # --- 通用 ---
    'file_not_found': {
        'zh': '找不到脚本: %(path)s',
        'en': 'script not found: %(path)s',
    },
    'collect_failed': {
        'zh': '采集失败: %(err)s',
        'en': 'collect failed: %(err)s',
    },
    'guard_error': {
        'zh': '[mpltweak] 未处理的错误: %(err)s',
        'en': '[mpltweak] unhandled error: %(err)s',
    },
    # --- check / describe 的采集边界（A1/A2：子进程 + 超时）---
    'collect_timeout': {
        'zh': '采集超时（超过 %(sec)d 秒；脚本是不是在等输入或在跑重活？'
              '可用 MPLTWEAK_TIMEOUT 放宽）',
        'en': 'collection timed out (over %(sec)d s; is the script waiting for '
              'input or doing heavy work? raise MPLTWEAK_TIMEOUT to allow more)',
    },
    'collect_no_state': {
        'zh': '未能取到图状态（子进程 exit %(rc)d）',
        'en': 'could not read the figure state (child exit %(rc)d)',
    },
    'collect_bad_state': {
        'zh': '子进程返回的状态文件无法解析: %(err)s',
        'en': 'the state file written by the child could not be parsed: %(err)s',
    },
    # --- check：几何规则（CH-12：机器可读的 msg 也要跟随语言，不再硬编码中文）---
    'chk_out_of_canvas': {
        'zh': '%(ax)s 超出画布：%(edges)s',
        'en': '%(ax)s is outside the canvas: %(edges)s',
    },
    'chk_edge_left': {'zh': '左边缘 %(v)s', 'en': 'left edge %(v)s'},
    'chk_edge_bottom': {'zh': '下边缘 %(v)s', 'en': 'bottom edge %(v)s'},
    'chk_edge_right': {'zh': '右边缘 %(v)s', 'en': 'right edge %(v)s'},
    'chk_edge_top': {'zh': '上边缘 %(v)s', 'en': 'top edge %(v)s'},
    'doctor_usage': {'zh': '用法: mpltweak doctor\n环境自检（后端 / Qt 绑定 / 版本 / 光标枚举）；不接受其它选项', 'en': 'usage: mpltweak doctor\nenvironment self-check (backend / Qt bindings / version / cursor); takes no other options'},
    'doctor_no_json': {'zh': 'doctor 没有 --json：它的输出是给人看的自检结果（不产出机器可读载荷）；请去掉 --json', 'en': 'doctor has no --json: its output is a human-readable self-check (nothing machine-readable is produced); drop --json'},
    'schema_unknown_args': {'zh': '无法识别的参数：%(args)s', 'en': 'unrecognized argument(s): %(args)s'},
    'chk_overlap': {
        'zh': '%(a)s 与 %(b)s 重叠（%(ox).3f × %(oy).3f）',
        # t67：en 模板原来写的是**位置参数** %.3f，而 t() 用 kwargs 插值 所以
        # en 下插值失败、原样吐出 '%(a)s overlaps %(b)s (%.3f x %.3f)'（实测）。
        # 改成与 zh 同形的命名占位符（只修 bug，不改文案语义）。
        'en': '%(a)s overlaps %(b)s (%(ox).3f x %(oy).3f)',
    },
    # t67：colorbar 与其它轴（含 aspect 锁定的 cartopy 地图）矩形相交。
    # 为什么单独一条规则：几何比对整体跳过 aspect_locked 轴，而"极地图 + colorbar"
    # 恰好落在盲区 所以 过去 check 对这条**最高频的真实排版事故**给 ok:true。
    'chk_colorbar_overlap': {
        'zh': '%(a)s（colorbar）压住了 %(b)s 的绘图区（重叠 %(ox).3f × %(oy).3f，'
              '按图宽/高归一）：导出图上这块区域真的会被盖住，'
              '请把 colorbar 挪开（或缩小它压住的那个轴）',
        'en': '%(a)s (colorbar) covers part of %(b)s (%(ox).3f x %(oy).3f of the '
              'figure): that area really is hidden in the exported figure - move '
              'the colorbar away (or shrink the axes it covers)',
    },
    'chk_not_aligned': {
        'zh': '%(ax)s 的%(edge)s是 %(v)s，与相邻面板 %(others)s 差 %(off)s',
        'en': '%(ax)s %(edge)s is %(v)s, off by %(off)s from neighbouring %(others)s',
    },
    'chk_size_mismatch': {
        'zh': '面板大小不一：多数是 %(mw).3f×%(mh).3f，另有 %(odd)s',
        'en': 'panels differ in size: most are %(mw).3f x %(mh).3f, but %(odd)s',
    },
    'chk_uneven_gap': {
        'zh': '同列垂直间距不均：%(gaps)s',
        'en': 'uneven vertical gaps in the same column: %(gaps)s',
    },
    'chk_font_mismatch': {
        'zh': '%(label)s字号不统一：多数 %(mode)spt，另有 %(odd)s',
        'en': 'inconsistent %(label)s font size: most are %(mode)spt, but %(odd)s',
    },
    'chk_limits_mismatch': {
        'zh': '%(label)s不统一：%(n)d 个面板是 %(mode)s，另有 %(odd)s'
              '（同组面板通常应一致；刻意放大某个面板时忽略本条）',
        'en': '%(label)s differ: %(n)d panels use %(mode)s, but %(odd)s '
              '(panels in one group usually share limits; ignore if intentional)',
    },
    'chk_limit_x': {'zh': 'x 轴范围', 'en': 'x-axis limits'},
    'chk_limit_y': {'zh': 'y 轴范围', 'en': 'y-axis limits'},
    'chk_font_title': {'zh': '标题', 'en': 'title'},
    'chk_font_label': {'zh': '轴标签', 'en': 'axis label'},
    'chk_font_tick': {'zh': '刻度', 'en': 'tick'},
    'chk_layout_conflict': {
        'zh': '第 %(line)d 行开了 %(how)s：布局引擎会在每次绘制时重算轴位置，'
              '写回的位置会被覆盖（--strict 视为问题）',
        'en': 'line %(line)d enables %(how)s: the layout engine recomputes axes '
              'positions on every draw, overriding written-back positions '
              '(--strict treats this as a problem)',
    },
    # --- check：文字渲染尺寸 ---
    'chk_kind_title': {'zh': '标题', 'en': 'title'},
    'chk_kind_xlabel': {'zh': 'x 轴标签', 'en': 'x label'},
    'chk_kind_ylabel': {'zh': 'y 轴标签', 'en': 'y label'},
    'chk_kind_xtick': {'zh': 'x 刻度', 'en': 'x tick'},
    'chk_kind_ytick': {'zh': 'y 刻度', 'en': 'y tick'},
    'chk_kind_text': {'zh': '文字', 'en': 'text'},
    'chk_kind_figtext': {'zh': '图注', 'en': 'figure text'},
    'chk_text_label': {
        'zh': '%(where)s 的%(kind)s「%(text)s」',
        'en': '%(kind)s "%(text)s" of %(where)s',
    },
    'chk_text_label_canvas': {
        'zh': '画布 的%(kind)s「%(text)s」',
        'en': '%(kind)s "%(text)s" on the canvas',
    },
    'chk_text_out': {
        'zh': '%(label)s 超出画布（%(edges)s），这段文字会被裁掉',
        'en': '%(label)s is outside the canvas (%(edges)s) and will be clipped',
    },
    'chk_text_out_tight': {
        # CH-4：脚本用 bbox_inches='tight' 导出时，画布会自适应扩张，
        # 超出 figure 框的文字**不会**被裁掉 —— 这条必须降级，否则 README 的 CI 配方永远红。
        'zh': '%(label)s 超出画布（%(edges)s）——脚本用 bbox_inches=\'tight\' 导出，'
              '画布会按内容扩张，通常不会被裁掉（故降级为提示；要按旧口径判加 --no-tight）',
        'en': '%(label)s is outside the canvas (%(edges)s) - the script saves with '
              'bbox_inches=\'tight\', so the canvas grows to fit and the text is '
              'usually NOT clipped (downgraded to a hint; add --no-tight for the '
              'old behaviour)',
    },
    'chk_text_unavailable': {
        'zh': '文字尺寸检测不可用（%(err)s），本次只做几何检查',
        'en': 'text-size check unavailable (%(err)s); geometry rules only',
    },
    'chk_tick_crowded': {
        'zh': '%(ax)s 的%(kind)s太密：「%(a)s」与「%(b)s」有 %(pct).0f%% 的宽度重叠，'
              '数字会看不清（抽稀刻度或缩小字号）',
        'en': '%(kind)s of %(ax)s are too dense: "%(a)s" and "%(b)s" overlap by '
              '%(pct).0f%% (thin out the ticks or shrink the font)',
    },
    'chk_text_overlap': {
        'zh': '%(a)s 与 %(b)s 压在一起（重叠 %(dx).3f×%(dy).3f）',
        'en': '%(a)s and %(b)s sit on top of each other (overlap %(dx).3f x %(dy).3f)',
    },
    # --- check：参数校验 / 人读输出 / --static（A4）---
    'chk_tol_invalid': {
        'zh': '容差必须是**有限正数**（收到 %(tol)s）：inf / nan / <= 0 会让几何检查'
              '静默失效（0.1.8 实测：--tol inf 时输出 `"tol": Infinity` 且 rc=0）',
        'en': 'tolerance must be a **finite positive number** (got %(tol)s): '
              'inf / nan / <= 0 silently disables every geometry rule',
    },
    'chk_header': {
        'zh': '检查 %(n)d 个轴%(cb)s，容差 %(tol).3f',
        'en': 'checked %(n)d axes%(cb)s, tolerance %(tol).3f',
    },
    'chk_cb_note': {
        'zh': '（其中 %(n)d 个 colorbar 不参与几何比对）',
        'en': ' (%(n)d colorbars excluded from the geometry rules)',
    },
    'chk_text_checked': {
        'zh': '文字 %(n)d 处已按**真实渲染尺寸**检查（超出画布 → 问题；互相压住 → 提示）',
        'en': '%(n)d text elements checked at their **real rendered size** '
              '(outside canvas -> problem; overlapping -> hint)',
    },
    'chk_text_skipped': {
        'zh': '（已用 --no-text 跳过文字尺寸检查）',
        'en': '(--no-text: text-size check skipped)',
    },
    'chk_tight_note': {
        'zh': '（脚本用 bbox_inches=\'tight\' 导出：超出画布的文字已按"画布会扩张"'
              '降级为提示；要看旧口径加 --no-tight）',
        'en': '(the script saves with bbox_inches=\'tight\': out-of-canvas text is '
              'downgraded to a hint because the canvas grows; add --no-tight for '
              'the old behaviour)',
    },
    'chk_all_clean': {
        'zh': '√ 没发现问题：对齐 / 等大 / 间距 / 字号 / 边界都正常',
        'en': 'OK - nothing found: alignment / equal size / gaps / fonts / '
              'bounds all look fine',
    },
    'chk_problem_line': {'zh': '× %(msg)s', 'en': 'X %(msg)s'},
    'chk_warn_line': {
        'zh': '· %(msg)s（若是有意为之可忽略）',
        'en': '- %(msg)s (ignore if intentional)',
    },
    'chk_summary_problems': {
        'zh': '%(n)d 个问题%(extra)s',
        'en': '%(n)d problem(s)%(extra)s',
    },
    'chk_summary_warns': {'zh': '，%(n)d 条提示', 'en': ', %(n)d hint(s)'},
    'chk_summary_clean': {
        'zh': '没发现问题%(extra)s',
        'en': 'nothing found%(extra)s',
    },
    'chk_static_header': {
        'zh': '静态检查（**不执行**脚本）：解析 %(parsed)s，布局引擎冲突 %(n)d 处',
        'en': 'static check (**script NOT executed**): parse %(parsed)s, '
              '%(n)d layout-engine conflict(s)',
    },
    'chk_static_parsed_ok': {'zh': '通过', 'en': 'ok'},
    'chk_static_parsed_fail': {'zh': '失败', 'en': 'failed'},
    'chk_syntax_error': {
        'zh': '脚本语法错误（第 %(line)s 行）：%(msg)s',
        'en': 'syntax error in the script (line %(line)s): %(msg)s',
    },
    'static_note': {
        # A4 契约：--static 必须**明说没检查什么**，别让用户以为"体检过了"。
        'zh': '注意：--static 只做了 AST 解析 + 布局引擎冲突扫描，**没有执行**你的脚本。'
              '因此对齐 / 等大 / 间距 / 字号 / 越界 / 文字重叠 / colorbar 都**没有检查**'
              '（这些必须真的跑一遍脚本）。要完整体检请去掉 --static。',
        'en': 'NOTE: --static only parses the AST and scans for layout-engine '
              'conflicts; it does **not run** your script. Alignment / sizes / '
              'gaps / fonts / bounds / text overlap / colorbars are therefore '
              '**not checked** (those need a real run). Drop --static for the '
              'full check.',
    },
    'static_ok_meaning': {
        'zh': '这里的 ok=true 只表示"脚本能解析、没发现布局引擎冲突"，'
              '不代表排版没问题。',
        'en': 'ok=true here only means "the script parses and no layout-engine '
              'conflict was found"; it does NOT mean the layout is fine.',
    },
    # --- MCP（机器可读：一律稳定英文，见模块开头两层设计）---
    'mcp_no_mcp_pkg': {
        'zh': '[mcp] 需要 mcp 包：pip install "mpltweak[mcp]"',
        'en': '[mcp] the mcp package is required: pip install "mpltweak[mcp]"',
    },
    'mcp_bad_tol': {
        'zh': '几何容差 tol 必须是有限正数（收到 %(tol)r）',
        'en': 'tol must be a finite positive number (got %(tol)r)',
    },
    # --- 窗口后端（gui-hunter 调用；P0-1 方案 B：回退到 Tk 必须告知，走 stderr）---
    'backend_tk_hint': {
        'zh': '当前是 Tk 后端（交互体验不如 Qt）；想要 Qt 界面：'
              'pip install "mpltweak[qt]"',
        'en': 'using the Tk backend (less polished than Qt); for the Qt UI: '
              'pip install "mpltweak[qt]"',
    },
    # --- 首次开窗（B 组，0.1.11：每个用户只打一次；新戳 first_open_shown）---
    # 与 backend_tk_hint 同一范式：launch._first_open_hint 优先取本键，键缺失时退回
    # launch 内置兜底（文案与这里逐字一致）。en 纯 ASCII、zh/en 都 GBK 可编码。
    'first_open_hint': {
        'zh': '[launch] 第一次开窗会等几秒（窗口要现建），不是卡住了；'
              '之后拖拽/缩放跟在 PPT 里拖图一样跟手。放心拖：关窗之前不会写你的脚本。',
        'en': '[launch] the first window takes a few seconds to build; it is not '
              'frozen. After that, dragging and resizing feel like moving shapes '
              'in a slide editor. Go ahead - nothing touches your script until '
              'you close the window.',
    },
    # 两段式的**早段**（跑用户脚本之前；只说不落戳，见 launch._emit_first_open_hint 的
    # stage 参数）：用户真正以为卡死的是这段静默 —— 后端 import + 跑用户脚本，而
    # first_open_hint 那句只覆盖"窗口要现建"的几秒。文案与 launch._FIRST_OPEN_EARLY_HINT_*
    # 逐字一致（键缺失时 launch 退回内置兜底）。en 纯 ASCII、zh/en 都 GBK 可编码。
    'first_open_early_hint': {
        'zh': '[launch] 正在加载绘图环境并执行你的脚本，第一次要等几秒到几十秒'
              '（脚本越大越久）——这段时间终端没有新输出是正常的，不是卡住了。',
        'en': '[launch] loading the plotting environment and running your script; '
              'the first time this can take from a few seconds to a minute '
              '(longer for big scripts) - no new output here is normal, not a '
              'freeze.',
    },
    # --- launch 关窗后的下一步（无感化①：省掉"记住怎么写回"这一步）---
    # 只在**这次真的存过参数**（用户拖过东西）时打；没拖过零噪音。
    # 动作仍要用户自己确认 —— 绝不自动写回用户源码（红线）。
    # 命令行单独占一行、**不带任何前缀**，确保能整行复制粘贴；
    # 路径按"用户敲命令时所在的目录"表达（见 launch._next_step_cmd）。
    'next_step_apply': {
        'zh': '[launch] 参数已存到 %(params)s（脚本未改动）；要写回源码：\n'
              '  %(cmd)s',
        'en': '[launch] params saved to %(params)s (script untouched); to write '
              'them back into the source:\n  %(cmd)s',
    },
    # --- schema（CH-9：schema 不再忽略参数）---
    'schema_usage': {
        'zh': '用法: mpltweak schema [--compact]\n'
              '  输出参数文件的 JSON Schema（draft-07），供 agent 校验自己生成的内容。',
        'en': 'usage: mpltweak schema [--compact]\n'
              '  print the params JSON Schema (draft-07) so agents can validate '
              'what they generate.',
    },
    # --- 可重跑预检（0.1.10 pre-flight；两个入口：apply 写回前 / launch 开窗前）---
    # 用户视角评审的最大留存瓶颈：本工具要求"在你的环境里重跑脚本"。0.1.10（t39 起）
    # 的口径是**写回默认只重跑 1 遍**（这一遍同时判定"脚本还跑得通吗"与"改动真落到
    # 目标图上了吗"），**只有目标图取不到状态时才回退 2 遍**并把原因打进 warnings，
    # 失败归因最多 3 遍。而「要参数 / 要联网 / 等人输入 / 要弹窗 / 缺依赖」的脚本会
    # 全线卡住。这里**事前**讲清楚 + 各给一句可照抄的下一步（与 t11 的事后归因同风格）。
    'preflight_head': {
        'zh': '[预检] 这个脚本重跑验证时可能失败或卡住（apply 默认只重跑 1 遍；'
              '目标图取不到状态时才回退 2 遍）：',
        'en': '[preflight] rerun verification may fail or hang on this script '
              '(apply reruns it once by default, twice only when the target '
              "figure's state cannot be captured):",
    },
    'preflight_missing_dependency': {
        'zh': '  [!] 缺依赖：找不到 %(packages)s —— 重跑验证会失败；建议先装依赖，'
              '或加 --verify-fast（只查语法）/ --keep-unverified'
              '（保留写回，先用版本控制）',
        'en': '  [!] missing dependency: %(packages)s -- rerun verification will '
              'fail; install it first, or use --verify-fast (syntax only) / '
              '--keep-unverified (keep the write-back, rely on version control)',
    },
    'preflight_needs_args': {
        'zh': '  [!] 需要参数：脚本会读命令行参数 —— 重跑必须把参数原样传给它：\n'
              '      mpltweak apply %(script)s --write -- <你的参数...>',
        'en': '  [!] needs arguments: the script reads command-line arguments -- '
              'pass them through so the rerun sees the same argv:\n'
              '      mpltweak apply %(script)s --write -- <your args...>',
    },
    'preflight_network': {
        'zh': '  [!] 联网：脚本会联网，无头重跑可能失败或变慢 —— 不想重跑就加 '
              '--verify-fast（只查语法）/ --keep-unverified'
              '（保留写回，先用版本控制）',
        'en': '  [!] network: this script uses the network; a headless rerun may '
              'fail or get slow -- use --verify-fast (syntax only) / '
              '--keep-unverified (keep the write-back, rely on version control)',
    },
    'preflight_stdin': {
        'zh': '  [!] 等输入：脚本会读 stdin / 调 input()，重跑时没人输入会一直等到'
              '超时 —— 不想重跑就加 --verify-fast（只查语法）/ --keep-unverified'
              '（保留写回，先用版本控制）',
        'en': '  [!] needs input: this script reads stdin / calls input(); the '
              'rerun has nobody to type and waits until the timeout -- use '
              '--verify-fast (syntax only) / --keep-unverified (keep the '
              'write-back, rely on version control)',
    },
    'preflight_gui': {
        'zh': '  [!] 弹窗：脚本会开窗口 / 交互显示，无头重跑跑不通（在有桌面的机器上'
              '还可能真的弹出窗口）—— 不想重跑就加 --verify-fast（只查语法）/ '
              '--keep-unverified（保留写回，先用版本控制）',
        'en': '  [!] GUI: this script opens a window / interactive display; a '
              'headless rerun cannot succeed (and it may really pop up a window '
              'on your desktop) -- use --verify-fast (syntax only) / '
              '--keep-unverified (keep the write-back, rely on version control)',
    },
    'preflight_tail': {
        'zh': '  [i] 以上只是预警，不影响本次动作；想跳过重跑就用 --verify-fast'
              '（只查语法）或 --keep-unverified（保留写回，先用版本控制）。',
        'en': '  [i] this is a heads-up only; it blocks nothing. To skip the '
              'rerun use --verify-fast (syntax only) or --keep-unverified '
              '(keep the write-back, rely on version control).',
    },
    # --- 0.1.11 收尾批：launch 的三处"人话"（rc=4 / 需要参数 / doctor 结论）---
    # 统一口径：每句都写清「**发生了什么**（含后果：脚本跑了没有、盘上有没有动）+
    # **你现在该做什么**（可照抄的下一步）」，与 apply.py 那批失败归因文案同风格。
    # 一律优先走 messages 表；en 纯 ASCII、zh/en 都必须 GBK 可编码（见模块开头）。
    'launch_backend_missing': {
        'zh': '[launch] 开不了窗：这台机器找不到可用的图形后端'
              '（试过 QtAgg/Qt5Agg/Qt6Agg/TkAgg，最后一个的报错：%(err)s）。\n'
              '         你的脚本没有执行，磁盘上也没有任何改动 —— 这是环境缺图形'
              '后端，不是脚本的问题。\n'
              '         现在两条路任选：\n'
              '           ① 要开窗调图：pip install "mpltweak[qt]"（推荐，装 PyQt5）；'
              'Linux 也可以装系统包 python3-tk\n'
              '              （没有桌面的服务器还要 DISPLAY / X 转发，否则装了也起不来）\n'
              '           ② 只用命令行：describe / check / apply / mcp 不需要图形'
              '后端，照常可用。',
        'en': '[launch] cannot open a window: this machine has no usable GUI '
              'backend (tried QtAgg/Qt5Agg/Qt6Agg/TkAgg; the last error: '
              '%(err)s).\n'
              '         Your script was NOT run and nothing on disk changed - the '
              'environment is missing a GUI backend, not a problem in the script.\n'
              '         Two ways out:\n'
              '           1) to open windows: pip install "mpltweak[qt]" '
              '(recommended, installs PyQt5); on Linux you can also install the '
              'system package python3-tk\n'
              '              (a machine with no desktop also needs DISPLAY / X '
              'forwarding, or it will still fail)\n'
              '           2) CLI only: describe / check / apply / mcp need no GUI '
              'backend and keep working.',
    },
    'launch_needs_args_action': {
        'zh': '[launch] 这个脚本要命令行参数：接下来会直接执行它，缺参数它会立刻'
              '报错、窗口开不出来（脚本本身没问题）。\n'
              '         带参数重来：mpltweak %(script)s -- <你的参数...>',
        'en': '[launch] this script needs command-line arguments: it will now be '
              'run as-is, and without them it fails at once and no window opens '
              '(the script itself is fine).\n'
              '         Rerun with them: mpltweak %(script)s -- <your args...>',
    },
    'doctor_verdict_no_matplotlib': {
        'zh': '环境不可用：没装 matplotlib —— 先 pip install matplotlib'
              '（或 pip install mpltweak，会一并装上）。',
        'en': 'not usable: matplotlib is not installed - run pip install '
              'matplotlib first (or pip install mpltweak, which brings it in).',
    },
    'doctor_verdict_ok_qt': {
        'zh': '环境可用：开窗走 %(backend)s，拖拽调图可用。',
        'en': 'usable: windows use %(backend)s; interactive tweaking works.',
    },
    'doctor_verdict_ok_tk': {
        'zh': '基本可用：能开窗，但只有 Tk 后端（交互体验不如 Qt）；'
              '想要 Qt 界面：pip install "mpltweak[qt]"。',
        'en': 'mostly usable: windows fall back to %(backend)s (less polished '
              'than Qt); for the Qt UI: pip install "mpltweak[qt]".',
    },
    # t96：tkinter 装了但**没有可用显示**（headless）—— 与 no_backend
    # （完全没有图形后端）是两回事，措辞必须能区分。
    'doctor_verdict_headless': {
        'zh': '环境不可用：这是**无头（headless）环境** —— tkinter 装了，但没有可用的显示（DISPLAY），开窗调图用不了；describe / check / apply / mcp 等命令行功能照常可用。要在服务器上开窗：用 X11 转发（ssh -X）或 xvfb-run。',
        'en': 'not usable: this is a headless environment - tkinter is present but there is no usable display (DISPLAY), so window-based tweaking will not work (CLI commands such as describe / check / apply / mcp keep working). To open a window on a server: use X11 forwarding (ssh -X) or xvfb-run.',
    },
    'doctor_verdict_no_backend': {
        'zh': '环境不可用：没有可用的图形后端，开窗调图用不了（describe / check / '
              'apply / mcp 等命令行功能照常可用）。装一个后端即可：'
              'pip install "mpltweak[qt]"；Linux 也可以装系统包 python3-tk。',
        'en': 'not usable: no GUI backend is available, so window-based tweaking '
              'will not work (CLI commands such as describe / check / apply / '
              'mcp keep working). Install one: pip install "mpltweak[qt]"; on '
              'Linux the system package python3-tk also works.',
    },
    # =====================================================================
    # B4（t33 / V1-F5）：apply **人读输出的固定文案** —— 过去这些是 apply.py 里的
    # 硬编码中文，`MPLTWEAK_LANG=en` 下照样打中文（V1 实测 `# p.json （第 1 张图）`、
    # `参数文件:`、`脚本:`、`画布:`、`  正在验证（…）`）。现在全部走本表，
    # en 一律**纯 ASCII**（CI 有"en 下 stdout/stderr 全 ASCII"的检查）。
    # 纪律：只**追加**新键，不重排/改名/改既有键文案；zh 逐字保持原样（既有测试与
    # 用户截图都对得上）。
    # =====================================================================
    'apply_params_label': {
        'zh': '参数文件: %(path)s',
        'en': 'params file: %(path)s',
    },
    'apply_script_label': {
        'zh': '脚本: %(name)s',
        'en': 'script: %(name)s',
    },
    'apply_canvas_label': {
        'zh': '画布: figsize_in=%(inch)s  (px=%(px)s)',
        'en': 'canvas: figsize_in=%(inch)s  (px=%(px)s)',
    },
    # t69 / P2-11：预览**明说这次会改哪些字段**（与"没动"的字段区分开）。
    # 报告现场：预览只打了"画布: figsize_in=[7.5, 4.75]"，用户/agent 看不出
    # "这一下会把我的画布改成一半"（P0-1 的背景）。
    'apply_will_change': {
        'zh': '会改：%(items)s（未列出的字段本次不动）',
        'en': 'will change: %(items)s (fields not listed are untouched)',
    },
    'apply_will_change_none': {
        'zh': '会改：无（这份参数与源码当前值一致）',
        'en': 'will change: none (this params file matches the current source)',
    },
    'apply_figsize_old_new': {
        'zh': 'figsize %(old)s → %(new)s',
        'en': 'figsize %(old)s -> %(new)s',
    },
    'apply_axis_pos': {
        'zh': '%(ax)s 位置 [%(pos)s]',
        'en': '%(ax)s pos [%(pos)s]',
    },
    'apply_axis_pos_missing': {
        'zh': '%(ax)s 位置（参数里没给，保持原样）',
        'en': '%(ax)s pos (not given in params; left as is)',
    },
    'apply_axis_fontsize': {
        'zh': '      字号 title=%(title)s label=%(label)s%(labely)s tick=%(tick)s',
        'en': '      font size title=%(title)s label=%(label)s%(labely)s '
              'tick=%(tick)s',
    },
    'apply_axis_legend': {
        'zh': '      图例 loc=%(loc)s fontsize=%(size)s',
        'en': '      legend loc=%(loc)s fontsize=%(size)s',
    },
    'apply_legend_free_anchor': {
        'zh': '（自由锚点）',
        'en': '(free anchor)',
    },
    'apply_axis_item_bad': {
        'zh': '  !! 参数文件里有一个非对象轴项，已跳过: %(item)r',
        'en': '  !! non-object axis entry in the params file, skipped: %(item)r',
    },
    'apply_stale_params': {
        'zh': '!! 跳过 %(name)s：脚本在参数保存之后被修改过（套用旧参数会覆盖你'
              '手改的内容）；确要套用请加 --force',
        'en': '!! skipping %(name)s: the script is newer than the saved params '
              '(applying them would overwrite your manual edits); add --force to '
              'apply them anyway',
    },
    'apply_params_errors': {
        'zh': '!! 参数文件错误（已拒写）: %(msg)s',
        'en': '!! params file error (write refused): %(msg)s',
    },
    'apply_params_errors_nowrite': {
        'zh': '!! 参数文件错误（已拒写，脚本未改动）: %(msg)s',
        'en': '!! params file error (write refused, script untouched): %(msg)s',
    },
    'apply_params_dropped': {
        'zh': '!! 参数文件里有内容已被丢弃脚本未改动: %(msg)s',
        'en': '!! params file has dropped content; the script was not modified: '
              '%(msg)s',
    },
    'apply_verifying': {
        'zh': '  正在验证（子进程重跑脚本，最长 %(sec)gs）',
        'en': '  verifying (re-running the script in a subprocess, up to %(sec)gs)',
    },
    # 互斥报错：三处详略一致（用法行 / --help / 这里），后果口径必须同款。
    'apply_mutex_verify': {
        'zh': '[apply] --no-verify 与 --verify-fast 不能同时用：\n'
              '  --no-verify   = 跳过重跑与语义验证（写盘后的语法检查仍会做）：\n'
              '                  脚本跑不通、布局没落到位都不会被发现，也不会自动回滚；\n'
              '                  这是写坏代码时唯一的自动安全网，一般不建议用。\n'
              '  --verify-fast = 只做语法检查（**不重跑脚本**）：坏语法会当场回滚，\n'
              '                  但"能跑通"和"布局真的落到目标图上"都不保证。\n'
              '  二选一。\n',
        'en': '[apply] --no-verify and --verify-fast cannot be used together:\n'
              '  --no-verify   = skip the rerun and the semantic check (the syntax\n'
              '                  check after writing still runs):\n'
              '                  a broken script or a layout that never landed goes\n'
              '                  unnoticed, and nothing is rolled back automatically;\n'
              '                  this is the only automatic safety net against\n'
              '                  writing broken code - generally not recommended.\n'
              '  --verify-fast = syntax check only (**no script rerun**): bad syntax\n'
              '                  is rolled back on the spot, but "it runs" and "the\n'
              '                  layout really landed on the target figure" are not\n'
              '                  guaranteed.\n'
              '  Pick one.\n',
    },
    'apply_strict_layout_refused': {
        'zh': '[apply] --strict：检测到布局引擎冲突，已拒绝写回。'
              '要照样写回请加 --allow-layout-conflict',
        'en': '[apply] --strict: layout engine conflict detected, write refused. '
              'Add --allow-layout-conflict to write anyway',
    },
    # 只读预览收尾的三行提示（原为 apply.py:1099-1101 的硬编码中文）。
    # t94（t84 取证后的收口，方案 b）：提示改成**可直接粘贴的两行片段**（①+②），并注明
    # `extend=` 情形必须两行都加 —— 实测（t84）：自动 cax 只 ① 可修好；带
    # `extend='both'` 的 colorbar 只 ① 仍回弹 `(0.33,0.5009,0.0007,0.0182)`。
    'apply_preview_hint': {
        'zh': '提示：以上数值需按脚本自身风格写回；colorbar 轴若改了位置，必须同时加两行：\n'
              '      cb.ax.set_box_aspect(None)\n'
              '      cb.ax.set_axes_locator(None)\n'
              '      否则 matplotlib 每次重绘会把宽度重置回自动值'
              '（带 extend= 的 colorbar 只加第一行仍会回弹 所以 两行都要加）。\n'
              '      机械插入可用 --write（AST 确定性写回，零 LLM）。',
        'en': 'note: the values above must be written back in your script\'s own '
              'style; if a colorbar axes moved, add both lines:\n'
              '      cb.ax.set_box_aspect(None)\n'
              '      cb.ax.set_axes_locator(None)\n'
              '      otherwise matplotlib resets its width on every redraw '
              '(with extend=, the first line alone still rebounds - add both).\n'
              '      mechanical insertion: use --write (deterministic AST '
              'write-back, no LLM).',
    },
    # 清单连接符（中文顿号在 en 下也是非 ASCII）。
    'items_sep': {
        'zh': '、',
        'en': ', ',
    },
    'apply_pending_see_warnings': {
        'zh': '(见 warnings)',
        'en': '(see warnings)',
    },
    # T15：显式 --style inplace 时"代码里没有可改的写法"的字段（键缺失时 apply.py
    # 退回内置兜底 _FALLBACK_NEEDS_BLOCK；补上本键后自动切到本表）。
    'needs_block_hint': {
        'zh': '[!] 这些字段代码里没有可改的写法、本次**没能落地**：%(items)s\n'
              '    → 用 --style auto（默认，会自动插入调整块）或 --style block '
              '重新落实；原位模式只改代码里已有的数字。',
        'en': '[!] there is nothing editable in the code for these fields, so they '
              'did **not land** this time: %(items)s\n'
              '    -> re-apply with --style auto (the default; inserts an '
              'adjustment block) or --style block; inplace mode only edits numbers '
              'that already exist in the code.',
    },
    # T23 / 3(d) + §20.3#4：这次到底验了哪几层（原为 apply.py:239-254 的硬编码中文，
    # 键名沿用那里的 TODO(i18n) 建议）。
    'verify_layers_fast': {
        'zh': '[i] 本次只做了**语法检查**：没有重跑脚本、没有比对布局是否真的等于'
              '参数（--verify-fast 的弱保证）',
        'en': '[i] syntax check only: the script was NOT re-run and the layout was '
              'not compared against the params (the weak guarantee of '
              '--verify-fast)',
    },
    'verify_layers_no_rerun': {
        'zh': '[i] 本次**没有重跑**脚本（--no-verify）：写回结果未经运行验证',
        'en': '[i] the script was NOT re-run (--no-verify): the write-back result '
              'was not runtime-verified',
    },
    'verify_layers_no_rerun_plain': {
        'zh': '[i] 本次**没有重跑**脚本：写回结果未经运行验证',
        'en': '[i] the script was NOT re-run: the write-back result was not '
              'runtime-verified',
    },
    'verify_layers_dryrun_maybe': {
        'zh': '[i] dry-run 预览（未落盘）：这批参数含"原位写不了"的字段 —— 默认档会'
              '尝试**实测回比**（这一步会**重跑一遍你的脚本**），因此**不排除本次已经'
              '跑过它**；预览结果未经运行验证。（--verify-fast / --no-verify / '
              '--no-semantic 不做实测回比、不重跑）',
        'en': '[i] dry-run preview (nothing written): these params contain fields '
              'that cannot be edited in place, so the default mode tries a '
              '**live comparison** (which **re-runs your script**); therefore this '
              'run **may already have run it** - the preview itself is not '
              'runtime-verified. (--verify-fast / --no-verify / --no-semantic do '
              'not compare live and do not re-run)',
    },
    'verify_layers_no_semantic': {
        'zh': '[i] 本次**没有做语义比对**（第③层没跑）：只确认了"脚本能跑通"，'
              '不保证布局真的落到目标图上',
        'en': '[i] no semantic comparison this time (layer 3 did not run): only '
              '"the script runs" was confirmed; the layout landing on the target '
              'figure is not guaranteed',
    },
    # =====================================================================
    # B4 + 原 t9（插入块文案语言化）：**writeback 生成的、会进 stdout/stderr 或
    # 写进用户源码**的文案。过去这些是 writeback.py 里的硬编码中文 所以
    # `MPLTWEAK_LANG=en` 下照样打中文（B4 残余）／把中文注释写进英文用户的脚本。
    # 纪律：zh 逐字保留（既有测试与用户截图按中文断言），en 纯 ASCII；
    # **哨兵行**（BLOCK_START/BLOCK_END/_LEGACY_*）是解析锚点，**绝不翻译**。
    # =====================================================================
    'wb_inplace_skipped': {
        'zh': 'ax%(ax)d 未原位应用：%(fields)s（原位写回只改代码里已有的数字，'
              '这些项代码里没有对应写法，保持原样；需要可用 --style block 或手动）',
        'en': 'ax%(ax)d not applied in place: %(fields)s (inplace write-back only '
              'edits numbers that already exist in the code; these have no '
              'editable form there and are left as is; use --style block or edit '
              'manually)',
    },
    'wb_needs_block': {
        'zh': '本图有 %(n)d 处目标值无法原位写回（%(fields)s）→ 需要调整块才能落地'
              '（%(why)s）',
        'en': 'this figure has %(n)d target value(s) that cannot be written back '
              'in place (%(fields)s) -> an adjustment block is needed to land '
              'them (%(why)s)',
    },
    'wb_needs_block_live': {
        'zh': '实测不一致',
        'en': 'live comparison disagreed',
    },
    'wb_needs_block_conservative': {
        'zh': '没重跑脚本，按保守判据',
        'en': 'no script rerun; conservative rule',
    },
    'wb_no_anchor_tail': {
        'zh': '脚本里没有 savefig/show/tight_layout 锚点，调整块已追加到脚本末尾'
              '（该脚本不存图时此块无实际效果）',
        'en': 'no savefig/show/tight_layout anchor in the script; the adjustment '
              'block was appended at the end (it has no effect if the script never '
              'saves a figure)',
    },
    'wb_block_refused_fields': {
        'zh': '块内只能按**静态可证的变量名**定位目标对象；这些字段证不出目标'
              '（没有可证的变量名，而运行期下标 fig.axes[i] / plt.gcf() 会写到别的轴'
              '或别的图上）→ 按 fail-closed 处理，本次不写：%(fields)s',
        'en': 'inside the block only **statically provable variable names** can '
              'locate the target objects; these fields have no provable target '
              '(no provable variable name, and runtime indices fig.axes[i] / '
              'plt.gcf() would write to another axes or another figure) -> '
              'fail-closed, not written this time: %(fields)s',
    },
    # --- A1（0.1.12）：`fig` 在函数里建、`return fig` 出函数、savefig 在模块层 ---
    # 一条能落的（锚点下沉到那条 return 之前）＋三条**不可证**形态的拒绝文案。
    # 纪律：每条都要给出**可照做的下一步**（不是"不支持"了事），且 pending 里带
    # 机器可读令牌 `fig.<reason>`（writeback.FA_*），人读这一行点名**具体原因**。
    'wb_func_anchor_multi_return': {
        'zh': '函数 %(func)s 里的 `return` 不是"函数末尾恰好一条"（有多条 return，'
              '或有提前 return）：figure 是在这个函数里建的，只有"末尾一条 `return fig`"'
              '才能证明存图前那份 fig 就是你要调的图 —— 本次**没有写回**（脚本一个字节'
              '都没动）。下一步：把多余的 return 合并成末尾一条 `return fig`'
              '（提前返回的分支改成 `fig = ...` 后继续往下走），再跑一次 apply。',
        'en': 'the `return` statements in %(func)s are not "exactly one, at the end '
              'of the function" (there are several, or an early return): the figure '
              'is created inside this function and only a single trailing '
              '`return fig` proves that the saved figure is the one you tweaked - '
              'nothing was written this time (the script is byte-for-byte '
              'unchanged). Next step: collapse the extra returns into one trailing '
              '`return fig` (make the early-return branches assign `fig = ...` and '
              'fall through), then run apply again.',
    },
    'wb_func_anchor_called_many': {
        'zh': '函数 %(func)s 在脚本里不是"恰好一次模块层调用"（当前能数到的调用点 '
              '%(n)d 个）：静态说不清哪一次调用出来的那张图才是目标图 —— 本次**没有'
              '写回**（脚本一个字节都没动）。下一步：只在模块层调用一次'
              '（只保留 `fig = %(func)s()` 这一处，别处改成复用同一个 fig），'
              '再跑一次 apply。',
        'en': 'the script does not call %(func)s exactly once at module level '
              '(call sites counted: %(n)d): statically there is no way to tell which '
              'call produced the target figure - nothing was written this time (the '
              'script is byte-for-byte unchanged). Next step: call it once at module '
              'level (keep a single `fig = %(func)s()` and reuse that figure '
              'elsewhere), then run apply again.',
    },
    'wb_func_anchor_method': {
        'zh': '%(func)s 是**类方法**（接收者写着 `self.ax` 这类）：方法里的轴变量身份'
              '静态证不出（同一个方法可能在别的实例/别处被调用，说不清哪一次的轴是目标）'
              '—— 本次**没有写回**（脚本一个字节都没动）。下一步：把建图那几行挪到'
              '模块层的普通函数里（`def make_plot(): ...`，不带 self），或直接在模块层'
              '建图，再跑一次 apply。',
        'en': '%(func)s is a **class method** (its receivers look like `self.ax`): the '
              'axis variables inside a method cannot be proven statically (the same '
              'method may be called on other instances or elsewhere, so which call '
              'produced the target axes is undecidable) - nothing was written this '
              'time (the script is byte-for-byte unchanged). Next step: move those '
              'figure-creating lines into a plain module-level function '
              '(`def make_plot(): ...`, without self), or create the figure at module '
              'level, then run apply again.',
    },
    'wb_block_figsize_runtime': {
        'zh': '# 提示：源码的 figsize 是变量/表达式，上面这行 set_size_inches 会在'
              '**运行时**把它覆盖成这里的值；要保留你的动态配置，删掉那一行',
        'en': '# note: the source figsize is a variable/expression, so the '
              'set_size_inches line above overrides it at runtime; delete that line '
              'to keep your dynamic setting',
    },
    # --- 写进用户源码的块注释（原 t9；哨兵行除外）---
    'wb_block_cells': {
        'zh': '# 轴身份（调图时）：%(cells)s',
        'en': '# axes identity (when tweaking): %(cells)s',
    },
    'wb_block_cell_item': {
        'zh': 'ax%(ax)s=网格%(cell)s',
        'en': 'ax%(ax)s=grid%(cell)s',
    },
    'wb_block_cells_sep': {
        'zh': '，',
        'en': ', ',
    },
    'wb_block_anchor_static': {
        'zh': '# 目标定位（T74）：建图调用可静态编号、模块层没有 close、'
              '也没有 colorbar/inset/twin/… 额外建轴，'
              '因此 %(fig)s.axes[k] 就是**目标图**静态第 k 条轴',
        'en': '# target location (T74): figure-creating calls are statically '
              'enumerable, there is no module-level close and no extra '
              'colorbar/inset/twin/... axes, so %(fig)s.axes[k] is the k-th axes '
              'of the **target figure** statically',
    },
    'wb_block_anchor_single': {
        'zh': '# 目标定位：本文件只有一次建图、没有 colorbar/inset/twin/… 额外建轴'
              '（也没有 close），因此 %(fig)s.axes[k] 就是静态第 k 条轴',
        'en': '# target location: this file creates a figure once, with no extra '
              'colorbar/inset/twin/... axes (and no close), so %(fig)s.axes[k] is '
              'the k-th axes statically',
    },
    'wb_block_colorbar': {
        'zh': '# colorbar 轴：先解除自动定位/盒比，否则重绘会重置位置与宽度',
        'en': '# colorbar axes: release auto-location / box aspect first, '
              'otherwise a redraw resets position and width',
    },
    'wb_block_tag': {
        'zh': '目标图：第 %(n)d 张',
        'en': 'target figure: #%(n)d',
    },
    # dry-run 会把人读的"这次会写什么"打出来（apply 打印 res['block']）——
    # 这两个值也必须语言化（t33 的回归 §23⑥ 实测逮到 en 下漏打中文「（无改动）」）。
    'wb_block_inplace': {
        'zh': '原位修改：%(items)s',
        'en': 'inplace edits: %(items)s',
    },
    'wb_block_none': {
        'zh': '（无改动）',
        'en': '(no changes)',
    },
    # =====================================================================
    # t55：params.py 的**校验/提示文案**（V1-F5 的最后一截：`MPLTWEAK_LANG=en`
    # 下 apply 人读输出仍有 5 行非 ASCII，全来自这里）。
    # 纪律：只搬文案，**不动任何校验逻辑/判定条件/异常类型/rc**；zh 逐字保留
    # （既有断言与用户截图按中文），en 纯 ASCII，全部 cp936 可编码。
    # 注意：`params.SCHEMA['description']` **故意不搬** —— 它是机器可读通道
    # （schema --json / describe --json）的既有值，且 test_agent_api §4 正是拿它
    # 的中文来验证 fd 级 UTF-8 输出，动了会改既有载荷语义。
    # =====================================================================
    'pv_decode_failed': {
        'zh': '无法识别的文本编码（已试 utf-8-sig / utf-8 / gbk / gb18030 / big5）',
        'en': 'unrecognized text encoding (tried utf-8-sig / utf-8 / gbk / '
              'gb18030 / big5)',
    },
    'pv_num_list_type': {
        'zh': '%(what)s 必须是 %(n)d 个数字的数组，实际是 %(v)r',
        'en': '%(what)s must be an array of %(n)d numbers, got %(v)r',
    },
    'pv_num_list_len': {
        'zh': '%(what)s 必须是 %(n)d 个数字（实际 %(len)d 个）: %(v)r',
        'en': '%(what)s must be %(n)d numbers (got %(len)d): %(v)r',
    },
    'pv_num_list_item': {
        'zh': '%(what)s 含非数值/非法数值元素: %(v)r',
        'en': '%(what)s contains a non-numeric / invalid element: %(v)r',
    },
    'pv_version_missing': {
        'zh': '参数文件缺少 version 字段（本规范要求 version=%(want)d）—— 这份文件'
              '可能不是本工具导出的，或被人手改掉了版本号；拒写。'
              '请用 `mpltweak describe <脚本>` 重新导出。',
        'en': 'the params file is missing the version field (this schema requires '
              'version=%(want)d) - the file was probably not exported by this tool, '
              'or its version was edited by hand; write refused. Re-export with '
              '`mpltweak describe <script>`.',
    },
    'pv_version_type': {
        'zh': 'version 缺失或非整数: %(v)r',
        'en': 'version missing or not an integer: %(v)r',
    },
    'pv_version_mismatch': {
        'zh': 'version=%(got)d 与当前规范 %(want)d 不符（旧版/未知版参数一律拒写，'
              '以免按错误语义改代码；请用 `mpltweak describe <脚本>` 重新导出）',
        'en': 'version=%(got)d does not match the current schema %(want)d '
              '(old/unknown params are always refused, so the code is not edited '
              'with the wrong semantics; re-export with `mpltweak describe '
              '<script>`)',
    },
    'pv_version_newer': {
        'zh': 'version=%(got)d 高于当前 %(want)d，按尽力读处理',
        'en': 'version=%(got)d is newer than the current %(want)d; reading it '
              'best-effort',
    },
    'pv_must_positive': {
        'zh': '%(what)s 必须 > 0，实际是 %(v)r',
        'en': '%(what)s must be > 0, got %(v)r',
    },
    'pv_width_label': {
        'zh': '%(what)s[2]（width）',
        'en': '%(what)s[2] (width)',
    },
    'pv_height_label': {
        'zh': '%(what)s[3]（height）',
        'en': '%(what)s[3] (height)',
    },
    'pv_pos_offscreen': {
        'zh': '%(what)s 整块都在画布外（left=%(left)g bottom=%(bottom)g '
              'w=%(w)g h=%(h)g）：写进源码后这块面板在图上看不到'
              '（left/bottom 为负但**有可见部分**是允许的，这里拒的是完全跑到画布外）',
        'en': '%(what)s is entirely outside the canvas (left=%(left)g '
              'bottom=%(bottom)g w=%(w)g h=%(h)g): after writing, this panel is '
              'invisible in the figure (a negative left/bottom with **some visible '
              'part** is allowed; only a fully off-canvas panel is refused)',
    },
    'pv_fontsize_negative': {
        'zh': '%(what)s 不能为负（实际 %(v)r）：matplotlib 渲染异常',
        'en': '%(what)s cannot be negative (got %(v)r): matplotlib renders it '
              'incorrectly',
    },
    'pv_index_negative': {
        'zh': '%(what)s 为负（%(idx)d）：轴序号是 0 基，负数会被 Python 当成'
              '"最后一张"，静默写到别的轴上',
        'en': '%(what)s is negative (%(idx)d): axis indices are 0-based, and '
              'Python treats a negative index as "the last one", silently writing '
              'to another axes',
    },
    'pv_top_not_object_inner': {
        'zh': '顶层不是对象: %(v)r',
        'en': 'the top level is not an object: %(v)r',
    },
    'pv_top_not_object_file': {
        'zh': '参数文件顶层不是对象（应为 JSON 对象，如 {"version": …}）'
              '—— 这份文件可能不是本工具导出的，或被人手改坏了结构；拒写。'
              '请用 `mpltweak describe <脚本>` 重新导出。实际内容: %(v)s',
        'en': 'the params file top level is not an object (it must be a JSON '
              'object such as {"version": ...}) - the file was probably not '
              'exported by this tool, or its structure was damaged by hand; write '
              'refused. Re-export with `mpltweak describe <script>`. Actual '
              'content: %(v)s',
    },
    'pv_axes_not_list': {
        'zh': 'axes 必须是数组，实际是 %(t)s',
        'en': 'axes must be an array, got %(t)s',
    },
    'pv_axis_not_object': {
        'zh': 'axes[%(i)d] 不是对象: %(v)r',
        'en': 'axes[%(i)d] is not an object: %(v)r',
    },
    'pv_index_not_int': {
        'zh': 'axes[%(i)d].index 必须是整数，实际是 %(v)r',
        'en': 'axes[%(i)d].index must be an integer, got %(v)r',
    },
    'pv_field_not_number': {
        'zh': 'axes[%(i)d].%(f)s 必须是数字或 null，实际是 %(v)r',
        'en': 'axes[%(i)d].%(f)s must be a number or null, got %(v)r',
    },
    'pv_grid_not_bool': {
        'zh': 'axes[%(i)d].grid 必须是布尔或 null，实际是 %(v)r',
        'en': 'axes[%(i)d].grid must be a boolean or null, got %(v)r',
    },
    'pv_scale_unsupported': {
        'zh': 'axes[%(i)d].%(f)s 不是支持的轴尺度: %(v)r（允许 %(allowed)s）',
        'en': 'axes[%(i)d].%(f)s is not a supported axis scale: %(v)r '
              '(allowed: %(allowed)s)',
    },
    'pv_lines_not_list': {
        'zh': 'axes[%(i)d].lines 必须是数组，实际是 %(t)s',
        'en': 'axes[%(i)d].lines must be an array, got %(t)s',
    },
    'pv_line_not_object': {
        'zh': 'axes[%(i)d].lines[%(j)d] 不是对象: %(v)r',
        'en': 'axes[%(i)d].lines[%(j)d] is not an object: %(v)r',
    },
    'pv_linewidth_not_number': {
        'zh': 'axes[%(i)d].lines[%(j)d].linewidth 必须是数字或 null，实际是 %(v)r',
        'en': 'axes[%(i)d].lines[%(j)d].linewidth must be a number or null, '
              'got %(v)r',
    },
    'pv_legend_not_object': {
        'zh': 'axes[%(i)d].legend 必须是对象或 null: %(v)r',
        'en': 'axes[%(i)d].legend must be an object or null: %(v)r',
    },
    'pv_legend_fontsize_not_number': {
        'zh': 'axes[%(i)d].legend.fontsize 必须是数字或 null，实际是 %(v)r',
        'en': 'axes[%(i)d].legend.fontsize must be a number or null, got %(v)r',
    },
    'pv_legend_loc_not_str': {
        'zh': 'axes[%(i)d].legend.loc 必须是字符串或 null，实际是 %(v)r',
        'en': 'axes[%(i)d].legend.loc must be a string or null, got %(v)r',
    },
    'pv_spines_not_object': {
        'zh': 'axes[%(i)d].spines 必须是对象: %(v)r',
        'en': 'axes[%(i)d].spines must be an object: %(v)r',
    },
    'pv_fontsize_zero': {
        'zh': 'axes[%(i)d].%(f)s = 0：这处文字会看不见（matplotlib 允许，故不拒写）',
        'en': 'axes[%(i)d].%(f)s = 0: that text will be invisible (matplotlib '
              'allows it, so the write is not refused)',
    },
    'pv_legend_fontsize_zero': {
        'zh': 'axes[%(i)d].legend.fontsize = 0：图例文字会看不见'
              '（matplotlib 允许，故不拒写）',
        'en': 'axes[%(i)d].legend.fontsize = 0: the legend text will be invisible '
              '(matplotlib allows it, so the write is not refused)',
    },
    # t56：参数文件**自己坏了**（JSON 截断 / 多余逗号 / 编码解不出来）。
    # 它是**用户输入**的问题，与"缺 version"同族 所以 必须归 bad_params，
    # 绝不许冒到顶层 guard 被报成 internal_error + attribution='tool_bug'
    # （判不出原因时不许猜工具 bug：那会把用户吓去报 issue，而不是修自己的参数文件）。
    'apply_params_unreadable': {
        'zh': '参数文件读不了（%(name)s）：%(err)s —— 这是**参数文件本身**的问题'
              '（截断 / 多余逗号 / 编码不对），不是本工具的 bug；请重新导出该参数文件。',
        'en': 'cannot read the params file (%(name)s): %(err)s - this is a problem '
              'with **the params file itself** (truncated / trailing comma / bad '
              'encoding), not a bug in this tool; re-export that params file.',
    },
}


def t(key, **kw):
    """取当前语言的消息并插值。

    找不到 key 时原样返回 key（宁可少一行字，也不要因为消息缺失而崩）。
    语言缺失时回落到英文——机器可读的那条路永远是英文。
    """
    item = MESSAGES.get(key)
    if not item:
        return key
    tpl = item.get(current_lang()) or item.get(_DEFAULT) or key
    try:
        return tpl % kw if kw else tpl
    except (KeyError, TypeError, ValueError):
        return tpl


def code(key):
    """稳定的机器可读标识：消息 key 本身就是错误码（不翻译、不受语言影响）。"""
    return key


def t_en(key, **kw):
    """永远取**英文**模板。

    MCP 工具与 ``--json`` 的字段是**机器可读**通道（模块开头那两层设计）：
    同一台 server 在中文机器和英文机器上必须给出同一串文本，否则客户端按关键字
    匹配就会坏。所以这些地方走 ``t_en`` 而不是 ``t``。
    """
    item = MESSAGES.get(key)
    if not item:
        return key
    tpl = item.get(_DEFAULT) or item.get('zh') or key
    try:
        return tpl % kw if kw else tpl
    except (KeyError, TypeError, ValueError):
        return tpl


def _finite(obj):
    """把非有限浮点（inf / nan）换成 ``None``。

    JSON（RFC 8259）**没有** Infinity / NaN 字面量：Python 的 json 会写出裸
    ``Infinity``，而 jq / JavaScript 的 ``JSON.parse`` / 严格 pydantic 客户端
    一律解析失败（0.1.8 实测：``check --json --tol inf`` 产出
    ``"tol": Infinity``）。这里先把它们降级成 ``null``，emit_json/fail_json
    再用 ``allow_nan=False`` 兜底。
    """
    import math
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {k: _finite(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_finite(v) for v in obj]
    return obj


def emit_json(obj, indent=2):
    """把机器可读结果以 **UTF-8 字节**写到 fd1（``--json`` 的**成功**路径）。

    **为什么不能直接 ``print(json.dumps(...))``**：Python 的 stdout 在被管道/重定向
    捕获时用的是**本地编码**（中国 Windows 上是 cp936/GBK）。JSON 里只要有一个中文
    （``check`` 的 ``msg``、``schema`` 的 description、``apply`` 的 warnings），产出
    就是 GBK 字节；按 UTF-8 解码的消费方（agent / MCP 客户端 / CI 测试）会直接崩。
    更糟的是**成功路径比失败路径更脆** —— ``fail_json`` 早就走 fd 级 UTF-8 了，
    于是"同一条命令，成功时解不开、失败时反而正常"。

    实测（本机 2026-10，子进程管道下 ``sys.stdout.encoding == 'gbk'``）：
    ``mpltweak check x.py --json`` 的 stdout 是 GBK，失败路径是 UTF-8。
    回归钉在 ``tests/test_agent_api.py`` 的 §9。

    注意：只影响**机器可读**输出。终端里给人看的那几行仍走 locale 编码（cp936 控制台
    能正常显示中文），这个分工与模块开头的两层设计一致。

    另一个副作用（目前无害，但改用时要知道）：fd 级直写**绕过
    ``contextlib.redirect_stdout``**，所以在进程内重定向 stdout 的调用者那里，
    JSON 仍会落到真实 fd1 上。现有调用者都满足：CLI 走子进程，
    ``mcp_server.apply_layout`` 走人读路径。
    """
    import json as _json
    # allow_nan=False（CH-3）：Python 的 json 默认会把 inf/nan 写成裸 `Infinity`
    # /`NaN`，那不是 RFC 8259 合法 JSON —— jq / JavaScript 的 JSON.parse /
    # 严格 pydantic 客户端会直接解析失败（0.1.8 实测 `check --json --tol inf`
    # 输出 `"tol": Infinity`）。这里先把非有限浮点降级成 null（JSON 有 null），
    # 再让 allow_nan=False 兜底：任何时候都不会吐出非法文档。
    text = _json.dumps(_finite(obj), ensure_ascii=False, indent=indent,
                       allow_nan=False) + '\n'
    try:
        sys.stdout.flush()          # 先排空 TextIO 缓冲，避免与 fd 直写交错
    except Exception:               # noqa: BLE001 - 排空失败不该影响结果输出
        pass
    try:
        os.write(1, text.encode('utf-8'))
    except OSError:
        # fd1 不可写（管道已关）：退回 print，至少别把命令弄崩
        try:
            sys.stdout.write(text)
        except Exception:           # noqa: BLE001
            pass


def fail_json(error_code, msg, attribution=None, next_step=None):
    """失败路径也要给出结构化结果（写 fd1，绕过 Python 层重定向）。

    契约：``--json``（以及 ``describe`` 这种"永远输出 JSON"的命令）在**任何**
    失败路径上都必须给一个可解析的 JSON。否则 agent 只能拿到空 stdout，而
    README 承诺的"过程信息走 stderr / 机器读 JSON"在失败路径上完全不成立
    （t3-#11 / t6-H2 的失败路径）。

    ``attribution`` / ``next_step``（0.1.10）：失败归因分类 + 一句可照抄的下一步，
    供 agent 据此自动分支（取值见 verify.py 的 ``VERIFY_FAIL_*`` 常量）。
    """
    import json as _json
    _payload = {'ok': False, 'error_code': error_code, 'error': msg}
    if attribution:
        _payload['attribution'] = attribution
    if next_step:
        _payload['next_step'] = next_step
    try:
        os.write(1, (_json.dumps(_payload, ensure_ascii=False,
                                 allow_nan=False) + '\n').encode('utf-8'))
    except OSError:
        pass


def guard_json_main(fn):
    """给 CLI 子命令的 ``main`` 加顶层兜底（t6-H2）。

    问题：`apply` / `check` / `describe` 遇到畸形输入（半截 JSON、GBK 参数文件、
    顶层数组、轴项缺 pos…）会抛裸 traceback，而 ``--json`` 契约（README 承诺
    "过程信息全走 stderr"）在**失败路径上完全不成立** —— stdout 一个字节都没有，
    消费它的 agent/MCP 只能拿到空字符串。

    这里统一成：任何未预期异常都转成结构化结果
      - ``--json`` → stdout 输出 ``{"ok": false, "error_code": ..., "error": ...}``，
        退出码非 0；
      - 否则 → stderr 打一行人话 + 退出码非 0。
    """
    import functools
    import json as _json
    import traceback

    @functools.wraps(fn)
    def wrapper(argv=None):
        try:
            return fn(argv)
        except SystemExit:
            raise                       # 子命令自己 sys.exit 的语义要保留
        except KeyboardInterrupt:
            raise
        except BaseException as e:      # noqa: BLE001 - 顶层兜底就是要抓所有
            _argv = list(sys.argv[1:] if argv is None else argv)
            _want_json = '--json' in _argv
            _err = '%s: %s' % (type(e).__name__, e)
            if _want_json:
                # 用 fd 层写：describe/check 在采集期间会把 sys.stdout 换成
                # stderr，若在那一刻崩，走 print 的 JSON 会跑到 stderr 去。
                # 0.1.10：internal_error = 工具自身 bug（tool_bug）——
                # --json 补上 attribution/next_step，agent 可据此自动分支。
                try:
                    os.write(1, (_json.dumps(
                        {'ok': False, 'error_code': 'internal_error',
                         'attribution': 'tool_bug',
                         'error': _err,
                         # 机器可读通道一律英文：跟随界面的中文会破坏客户端按
                         # 关键字匹配/展示的稳定性（见本模块 t_en 的 docstring）。
                         'next_step': t_en('fail_next_tool_bug')},
                        ensure_ascii=False, allow_nan=False) + '\n').encode('utf-8'))
                except OSError:
                    pass
            sys.stderr.write(t('guard_error', err=_err) + '\n')
            # 0.1.10：人读路径也点明归因（脚本侧 / 工具侧彻底分开报）。
            sys.stderr.write(t('fail_next_tool_bug') + '\n')
            if os.environ.get('MPLTWEAK_DEBUG'):
                traceback.print_exc(file=sys.stderr)
            # 退出码 2（"跑不起来"）而不是 1（"发现了问题"）：CI 里 1 的含义是
            # "去修你的图"，而这里的问题在工具自己身上 —— 混在一起用户会去图里
            # 找一个不存在的问题（CH-6）。
            return EXIT_INTERNAL

    return wrapper

# ===== t63: writeback 错误/边缘路径的 zh→en 表（自动生成，勿手改）=====

WB_ZH_TO_EN = {
    'axes[%d].index=%d 越界：这份参数里最多只有 %d 个轴槽（参数自报 %d 条 / 脚本里可寻址 %d 个），轴序号是 0 基。请用 `mpltweak describe <脚本>` 重新导出参数。':
        'axes[%d].index=%d is out of range: this params file has at most %d axis slots (params list %d / script addressable %d); axis indexes are 0-based. Re-export with `mpltweak describe <script> --all-axes`, or fix the params by hand.',
    'fig_index=%d 为负：图号是 0 基（-1 会被 Python 当成"最后一张"，静默写到别的图上）；拒写。':
        'fig_index=%d is negative: figure indexes are 0-based (-1 makes Python mean "the last one" and would silently write to another figure); refusing to write.',
    'fig_index=%d 越界：脚本里只有 %d 张图（参数自报 %s），图号是 0 基。请用 `mpltweak describe <脚本> --all-figs` 重新导出。':
        'fig_index=%d is out of range: the script has only %d figure(s) (params report %s); figure indexes are 0-based. Re-export with `mpltweak describe <script> --all-figs`.',
    '未记录':
        'not recorded',
    'pos（该轴是 colorbar 的宿主，位置由 colorbar 决定，保持原样；拖 colorbar 轴可调，或 --style block）':
        'pos (this axis hosts a colorbar, so its position is decided by the colorbar and is left as is; drag the colorbar axis instead, or use --style block)',
    'pos（替换位置重叠，已跳过）':
        'pos (the replacement range overlaps another edit; skipped)',
    '（替换位置重叠，已跳过）':
        '(the replacement range overlaps another edit; skipped)',
    'tick_fontsize（替换位置重叠，已跳过）':
        'tick_fontsize (the replacement range overlaps another edit; skipped)',
    'tick_fontsize_y（替换位置重叠，已跳过）':
        'tick_fontsize_y (the replacement range overlaps another edit; skipped)',
    'grid（替换位置重叠，已跳过）':
        'grid (the replacement range overlaps another edit; skipped)',
    # --- A2（MAINTENANCE §25.1）原位「补一个参数」的三条拒改说明 ---
    # 进 skipped 的字段名尾巴（与上面 `（替换位置重叠，已跳过）` 同一套写法；
    # 这就是 MAINTENANCE §25 登记过的"原因塞进字段名"老味道，留给 0.1.13 结构化）。
    '（这条调用带 **kwargs，认不出实际生效的参数，没有自动补参数；需要的话请手动把 fontsize=… 写进这条调用）':
        '(this call takes **kwargs, so which parameters actually take effect cannot '
        'be read; nothing was added - please write fontsize=... into this call by '
        'hand)',
    '（这条轴上有 %d 处 %s 调用，无法唯一确定该补哪一处，没有自动补；请手动改，或用 --style block）':
        '(%d calls to %s on this axis, so which one to extend cannot be determined; '
        'nothing was added - please edit by hand, or use --style block)',
    '（这份源码里该字号由 set_fontsize(变量) 决定，值不在字面量里，没有自动改写；请手动改）':
        '(in this source the size is decided by set_fontsize(<variable>), so the '
        'value is not a literal; nothing was rewritten - please edit by hand)',
    '另有 %d 处 %s 调用按"后写的生效"没改；要一并调整请手动改，或用 --style block':
        '%d other %s calls were left as-is because the later call wins; adjust '
        'them by hand, or use --style block',
    '已原位补上 %s：%s':
        'patched in place: %s - %s',
    'pid 专属备份写不出来（%s，errno=%s：%s）；`revert --first` 将没有可用副本，请用默认 revert':
        'could not write the pid-specific backup (%s, errno=%s: %s); `revert --first` will have no copy to use - please use the default revert',
    '另一个 mpltweak 写回正在进行（pid=%s）。若确认它已不在运行，请手动删除 %s':
        'another mpltweak write-back is in progress (pid=%s). If you are sure it is no longer running, delete %s by hand',
    '%s（本次未回滚；磁盘上保留的是你保存的版本，备份在 %s）':
        '%s (not rolled back this time; what is on disk is the version you saved, backup at %s)',
    '回滚也失败了（%s: %s）—— 脚本目前是改坏的状态，备份在 %s，请手动恢复':
        'the rollback failed too (%s: %s) - the script is in a broken state right now, backup at %s, please restore it by hand',
    '脚本有 %d 张图但参数没有 fig_index，无法确定该改哪张的画布尺寸 → 保持原样；请用 describe --fig N 重新导出，或手动改':
        'the script has %d figure(s) but the params carry no fig_index, so which canvas size to change is undecidable -> left as is; re-export with `describe --fig N`, or change it by hand',
    '画布尺寸保持原样（代码里 figsize 是位置参数或没有 figsize=）；用 --style block 或手动改':
        'canvas size left as is (the source passes figsize positionally, or has no figsize=); use --style block or change it by hand',
    '有 %d 处替换位置重叠，已跳过以免写坏源码（同一个 figure 里同名轴变量被 add_axes 赋值多次时会出现）；涉及的位置保持原样，可用 --style block 或手动改':
        '%d replacement ranges overlap and were skipped so the source is not broken (this happens when the same axis variable is assigned by add_axes more than once in one figure); those fields are left as is, use --style block if you need them',
    '备份失败: %s':
        'backup failed: %s',
    '写文件失败: %s（%s）':
        'writing the file failed: %s (%s)',
    '写回后源码语法错误（%s）：%s':
        'the written source has a syntax error (%s): %s',
    '快速验证失败：写回后的源码语法错误（%s）：%s':
        'fast check failed: the written source has a syntax error (%s): %s',
    '写回后验证异常（%s: %s）：%s':
        'verification after writing raised an exception (%s: %s): %s',
    '语义验证未执行':
        'semantic verification was not run',
    '写回后语义验证异常（%s: %s）：%s':
        'semantic verification after writing raised an exception (%s: %s): %s',
    '语义验证不一致（%s）：%s':
        'semantic verification mismatch (%s): %s',
    '语义验证未取到目标图状态（%s），本次只确认了"脚本能跑通"，**没有**验证布局是否落到目标图上':
        'semantic verification could not read the target figure state (%s); this run only confirms "the script still runs" - it does NOT verify that the layout landed on the target figure',
    '本图没有可原位写回的轴属性（轴是 add_subplot 网格位置 / colorbar / 无对应调用），只应用了画布尺寸；位置/字号请用 --style block 或手动改代码':
        'this figure has no axis attributes that can be written in place (the axes come from an add_subplot grid / a colorbar / have no matching call), so only the canvas size was applied; use --style block for position and font size',
    '脚本本身在无头环境跑不通（缺数据/依赖，或需要 GUI/交互），无法用 Agg 验证写回结果；按 --keep-unverified 保留本次写回，请自行运行确认':
        'the script itself cannot run headless (missing data/dependency, or it needs a GUI/interaction), so the write-back cannot be verified with Agg; keeping this write-back because of --keep-unverified, please run it yourself to confirm',
    '脚本本身在无头环境跑不通（缺数据/依赖，或需要 GUI/交互），无法用 Agg 验证这次写回是否可用 → 默认**不保留**：%s。确实要保留请显式加 --keep-unverified。':
        'the script itself cannot run headless (missing data/dependency, or it needs a GUI/interaction), so this write-back cannot be verified with Agg -> by default it is NOT kept: %s. To keep it anyway, pass --keep-unverified explicitly, or fix the script so it runs headless first.',
    '写回后脚本跑不通（%s）：%s':
        'the script no longer runs after the write-back (%s): %s',
    '验证超时（>%ss），未能自动验证；脚本保持写回后的状态，请手动重跑确认':
        'verification timed out (>%ss); it could not be verified automatically; the script keeps the written-back state, please rerun it by hand to confirm',
    'ax%s 存在多个 mappable 变量(%s)，已用 %s，请人工确认其它轴':
        'ax%s has several mappable variables (%s); used %s - please confirm the other axes by hand',
    'ax%s 的 mappable 未在赋值中解析到，用 %s.collections[0] 兜底，请人工确认':
        'the mappable of ax%s was not resolved from an assignment; falling back to %s.collections[0] - please confirm by hand',
    'ax%s 的 cmap %r 不是已注册 colormap（ListedColormap 之类返回 from_list 名），已跳过':
        'the cmap %r of ax%s is not a registered colormap (ListedColormap and friends report a from_list name); skipped',
    '生成的块标记没有独占一行（未写盘）':
        'the generated block marker does not occupy a line of its own (nothing written to disk)',
    '验证超时（>%ss）':
        'verification timed out (>%ss)',
    '语义验证超时':
        'semantic verification timed out',
    '未能取到图状态':
        'could not read the figure state',
    '单遍验证未取到目标图状态（%s）→ 已回退到两遍口径：脚本按原样重跑判定"还跑得通吗"，语义层如实记为"没有验到"':
        'the single-pass verification could not read the target figure state (%s) -> fell back to the two-pass mode: the script is rerun as-is to decide "does it still run", and the semantic layer honestly records "not verified"',
    '单遍验证没拿到结论（%s）→ 已回退到原样重跑判定':
        'the single-pass verification reached no conclusion (%s) -> fell back to rerunning the script as-is',
    '脚本有 %d 张图，但参数里没有 fig_index —— 无法确定这些改动该落到哪张上（凭猜会改错图）。请用 `mpltweak describe <脚本> --fig N` 重新导出参数后再写回。':
        'the script has %d figures but the params carry no fig_index - there is no way to tell which figure these changes belong to (guessing would change the wrong one). Re-export with `mpltweak describe <script> --fig N` and write back again.',
    '找不到 figure/subplots 的赋值目标，且脚本没有 import matplotlib.pyplot as plt（无法用 plt.gcf() 兜底）':
        'could not find an assignment target for figure/subplots, and the script does not import matplotlib.pyplot as plt (so plt.gcf() cannot be used as a fallback)',
    '脚本创建了 %d 张图，纯 pyplot 流按"最后一张图"写回（与启动器默认一致）':
        'the script creates %d figures; a pure-pyplot flow writes back to "the last figure" (same as the launcher default)',
    '第 %d 张（按记录定位）':
        'figure %d (located from the record)',
    '最后一张（参数未记录图号）':
        'the last figure (params did not record a figure index)',
    '脚本创建了 %d 处图（运行期共 %s 张），改用 plt.gcf() 定位%s':
        'the script creates %d figure call site(s) (runtime: %s figure(s)); switching to plt.gcf() to locate %s',
    '脚本创建了 %d 张图但未导入 pyplot，按 fig 变量统一应用，请人工确认':
        'the script creates %d figures but does not import pyplot; applying by the fig variable - please confirm by hand',
    '检测到 %d 处只有起始哨兵、没有收尾哨兵的调整块痕迹（第 %s 行）：本次会另插一块（不猜哪块该替哪块），落地后请手工删掉残缺那份':
        'found %d layout block(s) with only the opening sentinel and no closing sentinel (line %s): this run inserts another block (it will not guess which one to replace); after writing, please delete the broken one by hand',
    '这些字段调整块表达不了、本次**没能落地**：%s（可手动改，或把参数收敛到块能写的字段）':
        'these fields cannot be expressed by the layout block and did NOT land this time: %s (change them by hand, or narrow the params to fields the block can write)',
    '循环出图：已加守卫 `if %s == %d:`，只对该次迭代生效（想统一应用到所有图用 --all-figs）':
        'loop-generated figures: a guard `if %s == %d:` was added so this applies to that iteration only (use --all-figs to apply to every figure)',
    '锚点在循环里，但判不出图号对应的迭代值（非字面量 range / 一次迭代出多张图 / 变量迭代），已统一应用到每次迭代；要只改一张请人工确认':
        'the anchor sits inside a loop but the iteration value matching the figure index cannot be decided (non-literal range / several figures per iteration / a variable iterator), so it is applied to every iteration; confirm by hand if you wanted only one figure',
    '本脚本有 savefig/plt.show，而所有可用锚点都落在**最后一个 savefig 之后**：那样调整块写进去也不会反映到保存的图上':
        'this script has savefig/plt.show, and every usable anchor falls AFTER the last savefig: a layout block written there would not show up in the saved figure',
    '本脚本没有可用的插入锚点':
        'this script has no usable insertion anchor',
    '找不到合规锚点：%s，已拒绝写回（未改动脚本）。可手动加一句 `fig.savefig(...)` 之前的定位调用，或用 --style inplace 让可原位写的字段先落地。':
        'no compliant anchor found: %s; write-back refused (the script was not changed). You can add a positioning call before `fig.savefig(...)` by hand, or use --style inplace',
    '生成的调整块语法非法（未写盘，已换锚点重试）：%s':
        'the generated layout block is not valid Python (nothing written to disk; retrying with another anchor): %s',
    '验证超时（>%ss），未能自动验证；块已写入，请手动重跑确认':
        'verification timed out (>%ss); it could not be verified automatically; the block was written, please rerun it by hand to confirm',
    '锚点 %s 能跑通但布局没落到目标图（%s），换锚点重试':
        'anchor %s runs but the layout did not land on the target figure (%s); retrying with another anchor',
    '脚本本身在无头环境跑不通（缺数据/依赖，或需要 GUI/交互），无法用 Agg 验证这次写回是否可用 → 默认**不保留**：%s。确实要保留请显式加 --keep-unverified，或先修好脚本（让它能在无头环境跑通）再来写回。':
        'the script itself cannot run headless (missing data/dependency, or it needs a GUI/interaction), so this write-back cannot be verified with Agg -> by default it is NOT kept: %s. To keep it anyway, pass --keep-unverified explicitly, or fix the script so it runs headless first.',
    '换锚点重试前回滚未完成：%s':
        'the rollback before retrying another anchor did not complete: %s',
    '目标值在重跑后仍未达标：%s（脚本本身能跑通）':
        'the target values still do not match after the rerun: %s (the script itself runs fine)',
    '（逐字段回比不一致）':
        '(field-by-field comparison mismatch)',
    '。提示：脚本在第 %d 行（savefig/plt.show）之后可能还有会覆盖布局的调用；把那些调用移到 savefig 之前，或用 --style inplace。':
        '. Hint: the script may still have calls after line %d (savefig/plt.show) that override the layout; move them before savefig, or use --style inplace',
    '所有锚点写回后 Agg 重跑均失败（原脚本单独可跑通）':
        'every anchor failed the Agg rerun (the original script runs fine on its own)',
    '%s。%s。尝试：%s\nstderr 尾部：\n%s':
        '%s. %s. Attempts: %s\nstderr tail:\n%s',
    '本次锚点只能选"脚本尾"（脚本里没有 savefig/plt.show 可锚）——调整块会加在文件末尾':
        'this run could only pick "end of script" as the anchor (there is no savefig/plt.show to anchor on) - the layout block will be appended at the end of the file',
    '已回滚': 'rolled back',
    '语义验证不一致': 'semantic verification mismatch',
    '语义验证不一致：%s': 'semantic verification mismatch: %s',
    'figsize 参数自相矛盾：figsize_px=%s 按 dpi=%s 折算应是 %s in，'
    '但 figsize_in=%s（差成倍）—— 这通常来自"dpi 换算错一倍"，写回会把'
    '画布永久改小/改大。请用 `mpltweak describe <脚本>` 重新导出参数，'
    '或手工修正 figsize_in。':
        'figsize params contradict each other: figsize_px=%s with dpi=%s means %s in, '
        'but figsize_in=%s (off by a whole multiple) - this usually comes from a dpi '
        'conversion that is off by a factor, and writing it back would permanently '
        'resize the canvas. Re-export with `mpltweak describe <script>`, or fix '
        'figsize_in by hand.',
}


def t_wb(zh_template):
    """writeback 的 zh 模板 → 当前语言（t63）。

    zh：原样返回（**逐字不变**）；en：查 ``WB_ZH_TO_EN``，查不到原样返回
    （棘轮 test_writeback §98 会抓"漏登记的模板"）。只翻译文案，不动任何判定。
    """
    if current_lang() == 'zh':
        return zh_template
    return WB_ZH_TO_EN.get(zh_template, zh_template)

# ===== t63 表结束 =====


# ===== t64: launch / doctor 的 zh→en 表（与 t63 同一纪律）=====
# 为什么另立一张表而不是塞进 WB_ZH_TO_EN：t63 那张是 writeback 专用（且带 §98 的
# "writeback.py 覆盖率"棘轮）；launch/doctor 有自己的棘轮（test_launch §9），
# 两张表各自被自己的棘轮守着，混在一起会让"谁漏登记"分不清。
# 纪律（同 t63）：zh **逐字不变**（原样返回入参）；en 查本表；查不到原样返回
# （棘轮会抓"漏登记的模板"）。只翻译文案，不动任何判定。
# 【硬规则 · t78 血案】本表**只用于纯人读文本**。
# 协议载荷 / 日志分帧 / 被 `==`、`startswith` 比较或当字典键用的字符串，
# **一律不许套表** —— 真实反例：mcp_server.py:215 那条启动行套了 t_ui 之后
# MCP 握手直接断（`test_mcp` 红，尾行 `FAILED: 1 项 -> ['MCP 端到端']`），
# 写回 HEAD 后即绿。
UI_ZH_TO_EN = {
    # --- doctor 的列标签与括注（值是 messages 的 doctor_verdict_*，标签过去是硬编码中文）---
    'matplotlib   : 未安装 ->': 'matplotlib   : not installed ->',
    '             -> 先安装：pip install matplotlib（或直接 pip install mpltweak '
    '会一并装上）':
        '             -> install it first: pip install matplotlib (or pip install '
        'mpltweak, which brings it in)',
    '(可用: %s)': '(usable: %s)',
    '无': 'none',
    'backend 探测 :': 'backend probe:',
    '开窗后端   :': 'window backend:',
    'Qt 绑定      :': 'Qt binding   :',
    '光标枚举     :': 'cursor enums :',
    '             -> 有缺失：会静默退回 matplotlib 光标（功能不受影响，只是边框/角的'
    '形状提示弱一点）':
        '             -> some are missing: silently falls back to the matplotlib '
        'cursor (functionality is unaffected; only the edge/corner shape hint is '
        'weaker)',
    'Qt 绑定      : 不可用 ->': 'Qt binding   : unavailable ->',
    '(缺则 colorbar 宽度无法钉住，其余功能正常)':
        '(if missing, the colorbar width cannot be pinned; everything else works)',
    '结论       :': 'verdict      :',
    'Qt（PyQt5 可用）': 'Qt (PyQt5 available)',
    'Tk（未装 Qt；想要 Qt 界面：pip install "mpltweak[qt]"）':
        'Tk (Qt not installed; for the Qt UI: pip install "mpltweak[qt]")',
    '无（无法开窗调图，仅 CLI 可用）':
        'none (window-based tweaking is unavailable; CLI only)',
    # --- launch 的运行时人读输出 ---
    '[mpltweak] 参数文件顶层不是对象，跳过续调：%s':
        '[mpltweak] the params file top level is not an object; skipping the '
        'resume: %s',
    '[launch] 需要脚本路径，或用 --doctor 做环境自检\n':
        '[launch] a script path is required, or use --doctor for an environment '
        'self-check\n',
    '[launch] 找不到脚本: %s\n': '[launch] script not found: %s\n',
    '[launch] 用户脚本执行出错，已中止\n':
        '[launch] the user script raised an error; aborted\n',
    '[launch] 脚本没有留下可交互的图（可能已 close 或只存盘）\n':
        '[launch] the script left no interactive figure (maybe closed, or '
        'save-only)\n',
    '[launch] 脚本把后端设成了 %s，正在把已有图重新挂回 QtAgg…\n':
        '[launch] the script set the backend to %s; re-attaching the existing '
        'figures to QtAgg...\n',
    '[launch] 后端仍是非交互（%s），窗口无法显示。\n':
        '[launch] the backend is still non-interactive (%s); no window can be '
        'shown.\n',
    '[launch] 请临时注释脚本里的 matplotlib.use(...) 那行再试。\n':
        '[launch] comment out the matplotlib.use(...) line in your script and '
        'try again.\n',
    '[launch] 只有 %d 张图，取不到第 %d 张\n':
        '[launch] only %d figure(s); figure #%d is out of range\n',
    '[launch] 脚本: %s': '[launch] script: %s',
    '[launch] 共 %d 张图，全部可交互：%s':
        '[launch] %d figure(s) in total, all interactive: %s',
    '[launch] 正在调第 %d 张（共 %d 张）：%s':
        '[launch] tweaking figure %d of %d: %s',
    '[launch] 关窗后只写参数，不改代码: %s':
        '[launch] after closing, only params are written - your code is '
        'untouched: %s',
    '[launch] 提示：请用**英文输入法**（中文输入法下 c/s/g/x/y 等字母快捷键会直接上屏）':
        '[launch] tip: use an **English keyboard layout** (with a Chinese IME '
        'active, letter shortcuts like c/s/g/x/y go straight into the IME)',
    '[launch] 提示：%d 个窗口，**全部关闭**后本进程才会退出（窗口标题带 [图 k/%d]）。'
    '一次关完按 Ctrl+Q；每关一个就立刻存一份参数（没改过的不存），随时停也不会白改':
        '[launch] tip: %d window(s) - the process exits only after **all** are '
        'closed (window titles carry [figure k/%d]). Press Ctrl+Q to close them '
        'all at once; each close saves the params right away (unchanged ones are '
        'not saved), so stopping any time is safe',
    '[launch] 警告：关窗落参数失败（第 %d 张图，可能未保存）：%s':
        '[launch] warning: saving params on close failed (figure %d, possibly '
        'unsaved): %s',
    '[launch] 警告：关闭帮助窗口失败: %s':
        '[launch] warning: closing the help window failed: %s',
    '[launch] 注意：脚本在参数保存之后被修改过 → 本次不套用该参数（避免覆盖你的手动改动）；'
    '确要续调加 --force-resume':
        '[launch] note: the script was modified after the params were saved -> '
        'those params are not applied this time (to avoid overwriting your manual '
        'edits); add --force-resume to continue anyway',
    '[launch] 已套用上次参数：%s（接着上次调；删掉这些文件可从头开始）':
        '[launch] previous params applied: %s (continue from last time; delete '
        'these files to start over)',
    '第 %d 张 %d 项': 'figure %d: %d item(s)',
    '[launch] --dry-run：参数已写出（未开窗）-> %s':
        '[launch] --dry-run: params written (no window opened) -> %s',
    '[launch] 未检测到改动，未写参数文件':
        '[launch] no changes detected; no params file written',
    '[launch] 已记录第 %d 张图（共 %d 张）的参数: %s':
        '[launch] recorded the params of figure %d (of %d): %s',
    '[launch] 已记录 %d 张图的参数（%s）：':
        '[launch] recorded the params of %d figure(s) (%s):',
    '[launch]   第 %d 张 -> %s%s': '[launch]   figure %d -> %s%s',
    '（并另存主文件）': ' (also saved the main file)',
    '[launch] 参数摘要: 无改动': '[launch] params summary: no changes',
    '[launch] 位置变化: %s': '[launch] position changes: %s',
    '[launch] 参数摘要读取失败: %s': '[launch] failed to read the params summary: %s',
    # 图摘要（进上面两条 print 的 %s；窗口标题不带它）
    '图 %dx%dpx，%d 个轴': 'figure %dx%dpx, %d axes',
    # --- describe：写盘那一行（t64 船长授权的一行超范围改动）---
    '[describe] 已写入 %s\n': '[describe] written to %s\n',
    # --- toolbox：启动横幅那 2 行（t64 船长授权的超范围改动；其余 49 条只登记）---
    '要调图的用户脚本（里面不需要任何本工具的代码）': 'the user script to tweak (it needs no code from this tool)',
    '调第 N 张图（默认最后一张）': 'tweak figure N (default: the last one)',
    '参数输出路径': 'where to write the params file',
    '脚本在参数保存后被改过也强行套用旧参数': 'apply the saved params even if the script changed after they were saved',
    '只做环境自检（后端/绑定/版本/光标），不跑脚本': 'environment self-check only (backend / bindings / version / cursor); does not run the script',
    '不开窗，只验证能抓图并存参数': 'no window: only verify that figures can be captured and params saved',
    '打印全部交互日志（缩放开始 axN <手柄> 等），排查用': 'print the full interaction log (resize start axN <handle>, ...) for debugging',
    '[revert] 写文件失败: %s（原文件未改动）\n': '[revert] writing the file failed: %s (the original file is unchanged)\n',
    '[revert] 找不到脚本: %s\n': '[revert] script not found: %s\n',
    '[revert] 拿不到写回锁：%s\n': '[revert] cannot take the write-back lock: %s\n',
    '[mcp] mpltweak MCP server 已启动（stdio）\n': '[mcp] mpltweak MCP server started (stdio)\n',
    'mpltweak —— 像在 PPT 里调多图排版一样调 matplotlib（脚本零侵入）\n\n用法：\n  mpltweak <script.py> [--fig N] [--params X]     开窗调图\n  mpltweak launch <script.py> [--fig N]           同上（显式子命令）\n  mpltweak apply <script.py> [--write] [--json]   落实（默认只读预览）\n  mpltweak revert <script.py> [--write]           回退到上次写回之前\n  mpltweak describe <script.py> [--lang zh|en]    不开窗导出当前排版\n  mpltweak check <script.py> [--json]             排版体检（会执行脚本）\n  mpltweak check <script.py> --static             只做 AST + 布局引擎扫描\n  mpltweak schema [--compact]                     参数 JSON Schema\n  mpltweak mcp                                    启动 MCP server\n  mpltweak doctor                                 环境自检\n\n退出码（0.1.11 起统一为 0 / 1 / 2 / 4）：0 = 没问题 / 成功；1 = 发现问题（check）；\n  2 = 跑不起来（脚本找不到 / 脚本自己报错 / 参数非法 / 工具自身异常）；\n  4 = 没有可用图形后端。\n  （0.1.9 的退出码 3 已退役：常量保留，但不再由任何路径产生。）\n同为退出码 2，**"脚本侧失败"与"工具自身 bug"分开报**（0.1.10 起，只改文案）：\n  · 脚本侧：文件不存在 / 参数文件不合法（bad_params）/ 你的脚本自己报错\n    （缺依赖 / 缺数据 / 需要命令行参数 / 无头环境要开窗口）；\n  · 工具侧：internal_error —— 本工具自己抛了未预期的异常，与你的脚本无关。\n两类都印一行「归因」+ 一句可照抄的**下一步**；`--json` 下用 `attribution` 字段\n（script_cannot_run / script_needs_args / script_needs_gui_or_headless_impossible\n/ tool_bug）与 `next_step` 区分，agent 可据此自动分支。\n注意：describe / check / apply 会**执行你的脚本**（在独立子进程里，Agg 无头后端），\n      脚本的副作用（写文件 / 联网 / 缓存）都会真实发生；--static 是唯一不执行的选择。\n\n交互：拖面板移动 / PPT式缩放（Shift 等比、Alt 中心）/ Ctrl+点击多选 /\n      对齐均分 / 吸附 / Ctrl+F 裁白边 / 悬停±字号 / 方向键移动面板 /\n      空格线条模式 / Ctrl+Z 撤销，Ctrl+Y 或 Ctrl+Shift+Z 重做 / ? 看键位\n': 'mpltweak - tweak matplotlib layout like moving shapes in a slide editor (zero intrusion into your script)\n\nusage:\n  mpltweak <script.py> [--fig N] [--params X]     open the interactive window\n  mpltweak launch <script.py> [--fig N]           same (explicit subcommand)\n  mpltweak apply <script.py> [--write] [--json]   apply (preview by default)\n  mpltweak revert <script.py> [--write]           undo the last write-back\n  mpltweak describe <script.py> [--lang zh|en]    export the layout without a window\n  mpltweak check <script.py> [--json]             layout check (runs your script)\n  mpltweak check <script.py> --static             AST + layout engine only\n  mpltweak schema [--compact]                     params JSON Schema\n  mpltweak mcp                                    start the MCP server\n  mpltweak doctor                                 environment self-check\n\nexit codes (unified to 0 / 1 / 2 / 4 as of 0.1.11): 0 = fine / success; 1 = problems found (check);\n  2 = cannot run (script not found / your script raised / bad params / internal error);\n  4 = no usable GUI backend.\n  (exit code 3 from 0.1.9 is retired: the constant stays, but no path returns it.)\nFor the same exit code 2, "your script failed" and "the tool itself has a bug" are reported separately (since 0.1.10; wording only):\n  - script side: missing file / invalid params file (bad_params) / your script raised\n    (missing dependency / missing data / needs command-line arguments / needs a window in a headless env);\n  - tool side: internal_error - this tool raised an unexpected exception; unrelated to your script.\nBoth print one "attribution" line plus a copy-pasteable next step; with `--json` the\n`attribution` field (script_cannot_run / script_needs_args / script_needs_gui_or_headless_impossible\n/ tool_bug) and `next_step` let an agent branch automatically.\nNote: describe / check / apply DO execute your script (in a separate process, Agg headless);\n      side effects (writing files / network / cache) really happen; --static is the only\n      option that never executes it.\n\ninteraction: drag a panel to move / PPT-style resize (Shift keeps the ratio, Alt centers) /\n      Ctrl+click multi-select / align and distribute / snapping / Ctrl+F trim margins /\n      hover +/- to change font size / arrow keys move a panel /\n      space toggles line mode / Ctrl+Z undo, Ctrl+Y or Ctrl+Shift+Z redo / ? shows the keys\n',

    '[mpltweak] 模式=重图（ghost+blit）全量重绘=%s 吸附=%s 导出=%s':
        '[mpltweak] mode=full redraw (ghost+blit) full-redraw=%s snap=%s '
        'export=%s',
    '[mpltweak] 拖拽/框选/多选都走 blit 预览（松手或停顿才真正落位）；'
    '若下面出现 blit 降级提示，说明该后端不支持 blit，会明显卡':
        '[mpltweak] dragging / box-select / multi-select all use blit previews '
        '(positions settle on release or on a pause); if a blit-degradation '
        'notice shows up below, this backend does not support blit and will '
        'feel sluggish',
    '[describe] %s 是一个目录，请给出文件路径（例如 %s/params.json）\n': '[describe] %s is a directory; give a file path (e.g. %s/params.json)\n',
    '[describe] 写不进 %s：%s\n': '[describe] cannot write %s: %s\n',
    '  请确认该路径可写（父目录本命令会自动创建）；本命令不会改动你的脚本。\n': '  check that the path is writable (this command creates the parent directory itself); it never modifies your script.\n',
    '第 %d 张图不存在（脚本只有 1 张）；请用 0，或用 --all-figs 全部导出': 'figure %d does not exist (the script has only 1); use 0, or --all-figs to export it',
    '第 %d 张图不存在（脚本共留下 %d 张）；请用 0..%d 之间的序号，或用 --all-figs 全部导出': 'figure %d does not exist (the script left %d); use an index in 0..%d, or --all-figs to export them all',
    '第 %d 张图不存在（脚本共留下 %d 张）；请用 0..%d 之间的序号，或用 --all-figs 全部导出': 'figure %d does not exist (the script left %d); use an index in 0..%d, or --all-figs to export them all',
    '第 %d 张图不存在（脚本只有 %d 张）；请用 0，或用 --all-figs 全部导出': 'figure %d does not exist (the script has only %d); use 0, or --all-figs to export it',
    '  回退前的那份留档：%s（再回退一次就回到这里）': 'the pre-revert copy is kept at: %s (one more revert goes back here)',
    '√ 已回退：%s': 'reverted: %s',
    '  这是预览，你的代码没有被改动。确认回退：mpltweak revert %s --write': 'this is a preview; your code was NOT changed. to confirm the revert: %s',
    '  提示：脚本比参数新了，下次开窗不会自动套用旧参数；确要接着调加 --force-resume': 'note: the script is newer than the params, so the old params will not be applied automatically next time; add --force-resume to continue',
    '已一致：%s 与备份内容相同（没有可回退的写回）。': 'already identical: %s matches the backup (no write-back to revert).',
    '把脚本回退到备份里的状态（默认用固定名 .tweak_params/<脚本名>.tweak.bak；该备份首份优先 → 通常是你第一次改这张图之前的原件）': 'revert the script to the state in the backup (by default the fixed-name .tweak_params/<script>.tweak.bak; the first one wins -> usually the original from before your first edit of this figure)',
    '回退预览：%s  ←  %s（备份于 %s）': 'revert preview: %s  <-  %s (backed up at %s)',
    '  将恢复 %d 行、删除 %d 行：': '  will restore %d line(s) and delete %d line(s):',
    '最早一份专属副本（最早那次写回之前）': 'the earliest per-pid copy (before the earliest write-back)',
    '固定名备份（它建立那一刻的原文）': 'the fixed-name backup (the original text as of the moment it was created)',
    '没有可用的 pid 备份（%s 下没有 `<脚本名>.tweak.<pid>.bak`）：无法回到**最早**状态。\n  · 常见原因：备份目录路径过长（Windows MAX_PATH 260），pid 副本没写出来 —— 本次写回会给出相应 warning。\n  · 固定名备份回到的是它建立那一刻的原文（通常是你第一次改这张图之前），不要拿它当 --first 用；只想要这个默认回退，请去掉 --first。': 'no usable pid backup (no `<script>.tweak.<pid>.bak` under %s): cannot go back to the **earliest** state.\n  - common cause: the backup directory path is too long (Windows MAX_PATH 260), so the pid copy was not written - this write-back will report a warning.\n  - the fixed-name backup holds the original as of the moment it was created (usually before your first edit of this figure); do not use it as --first; if you only want this default revert, drop --first.',
    '  可逆范围：本次用的是%s —— 默认回退用**固定名**备份，它**首份优先**（第一次写回时建立、之后不再覆盖），所以通常回到你**第一次改这张图之前**的原件，而不是上一步的中间态；要拿"最早那次写回之前"的快照用 --first': 'reversible range: %s\n',
    '还没有可回退的备份：%s\n  revert 只能回退"做过 mpltweak apply --write 的脚本"——备份是写回时留下的写回前原文。': 'no write-back to revert yet: %s\n  revert only works on a script that `mpltweak apply --write` has touched - the backup is the pre-write original kept at write-back time.\\n',
    '脚本在参数保存后被改过也强行套用旧参数（默认不套用，避免静默覆盖你的手动改动）': 'apply the saved params even if the script changed after they were saved (by default they are NOT applied, to avoid silently overwriting your manual edits)',
    '[revert] %s\n': '[revert] %s\n',
    '[revert] 写文件失败: %s（原文件未改动）\n': '[revert] writing the file failed: %s (the original file is unchanged)\n',
    '[revert] 找不到脚本: %s\n': '[revert] script not found: %s\n',
    '[revert] 拿不到写回锁：%s\n': '[revert] cannot take the write-back lock: %s\n',
    '[revert] 找不到脚本: %s\n': '[revert] script not found: %s\n',
    '[revert] %s\n': '[revert] %s\n',
    '[revert] 拿不到写回锁：%s\n': '[revert] cannot take the write-back lock: %s\n',
    '[revert] 写文件失败: %s（原文件未改动）\n': '[revert] writing the file failed: %s (the original file is unchanged)\n',
    '[mcp] mpltweak MCP server 已启动（stdio）\n': '[mcp] mpltweak MCP server started (stdio)\n',
    # t69 / P2-10：被调脚本自己往 stderr 刷屏时，末尾给一句汇总（不吞它的输出）
    '[launch] 提示：上面的 stderr 里有 %d 行来自**你的脚本自身**'
    '（工具自己的提示都带 [launch]/[mpltweak] 前缀）':
        '[launch] note: %d line(s) above came from **your own script** (this '
        'tool prefixes its own messages with [launch]/[mpltweak])',
}


def t_ui(zh_template):
    """launch / doctor / describe 的 zh 模板 → 当前语言（t64）。

    zh：原样返回（**逐字不变**）；en：查 ``UI_ZH_TO_EN``，查不到原样返回
    （棘轮 test_launch §9 会抓"漏登记的模板"）。只翻译文案，不动任何判定。
    """
    if current_lang() == 'zh':
        return zh_template
    return UI_ZH_TO_EN.get(zh_template, zh_template)

# ===== t64 表结束 =====

# ---- t79：toolbox 交互/调试行（72 条，键取自源码原文；zh 逐字节不变）----
UI_ZH_TO_EN.update({
    '[mpltweak] 模式=重图（ghost+blit）全量重绘=%s 吸附=%s 导出=%s': '[mpltweak] mode=heavy (ghost+blit) full redraw=%s snap=%s export=%s',
    '[mpltweak] 拖拽/框选/多选都走 blit 预览（松手或停顿才真正落位）；若下面出现 blit 降级提示，说明该后端不支持 blit，会明显卡': '[mpltweak] dragging/box-select/multi-select all use a blit preview (the move lands on release or after a pause); if a blit-degraded notice appears below, this backend does not support blit and will feel laggy',
    '[mpltweak] 键位: 拖面板=移动 拖边/角=缩放 | 悬停标题/x轴/y轴/刻度/图例/colorbar 按 +/- 调字号 | Ctrl+Z撤销 | 悬停文字±字号 | l图例不再支持键位切换（拖图例=8位吸附） | 方向键=移动面板 | Shift+方向键=微调尺寸(中心不动) | 关窗自动导出 | 拖窗口=画布尺寸 | 模式固定=重图': '[mpltweak] keys: drag panel=move, drag edge/corner=resize | hover title/x/y/ticks/legend/colorbar and press +/- for fontsize | Ctrl+Z undo | hover text +/- fontsize | l: legend no longer switches by key (drag legend=snap) | arrow keys=move panel | Shift+arrows=fine resize (centre fixed) | auto export on close | drag window=canvas size | mode fixed=heavy',
    '[mpltweak] 画布 %dx%d 超出屏幕可用区 %dx%d，已自动缩放到屏内 (%.0f%%)': '[mpltweak] canvas %dx%d exceeds the usable screen area %dx%d; scaled to fit (%.0f%%)',
    '[mpltweak] 关窗自动导出失败:': '[mpltweak] auto export on close failed:',
    '[mpltweak] blit 不可用，已降级节流实时重绘（重图会明显卡）:': '[mpltweak] blit unavailable; degraded to throttled live redraw (heavy figures will feel laggy):',
    'blit 不可用：已降级为节流重绘（重图下会卡）': 'blit unavailable: degraded to throttled redraw (laggy on heavy figures)',
    '[mpltweak] 该轴没有可循环的图例': '[mpltweak] this axes has no cyclable legend',
    '[mpltweak] %s 图例 -> %s': '[mpltweak] %s legend -> %s',
    '[mpltweak] 对齐需要先选中 ≥2 个面板（Ctrl+点击 或 拖框选）': '[mpltweak] align needs >= 2 panels selected (Ctrl+click or box-select)',
    '对齐 %s（%d 个面板）': 'align %s (%d panels)',
    '[mpltweak] 均分需要先选中 ≥3 个面板': '[mpltweak] distribute needs >= 3 panels selected',
    '均分 %s（%d 个面板）': 'distribute %s (%d panels)',
    '垂直': 'vertical',
    '水平': 'horizontal',
    '[mpltweak] fit: 收到快捷键': '[mpltweak] fit: shortcut received',
    '[mpltweak] fit: 拿不到 renderer，无法适配': '[mpltweak] fit: no renderer available, cannot fit',
    '[mpltweak] fit: 无轴或画布无效 %dx%d': '[mpltweak] fit: no axes or invalid canvas %dx%d',
    '[mpltweak] fit: 内容盒为空，无法适配': '[mpltweak] fit: empty content box, cannot fit',
    '[mpltweak] fit: 内容已贴边，无需适配（%.2fx%.2f in）': '[mpltweak] fit: content already flush, nothing to fit (%.2fx%.2f in)',
    '[mpltweak] fit: set_size_inches 失败': '[mpltweak] fit: set_size_inches failed',
    '适配画布到内容 → %.2fx%.2f in': 'fit canvas to content -> %.2fx%.2f in',
    '[mpltweak] fit: 完成 → %.2fx%.2f in（原 %.2fx%.2f）': '[mpltweak] fit: done -> %.2fx%.2f in (was %.2fx%.2f)',
    '[mpltweak] 没有可撤销的操作': '[mpltweak] nothing to undo',
    '没有可撤销的操作': 'nothing to undo',
    '[mpltweak] 撤销：%s（剩 %d 步）': '[mpltweak] undo: %s (%d steps left)',
    '已撤销：': 'undone: ',
    '[mpltweak] 没有可重做的操作': '[mpltweak] nothing to redo',
    '没有可重做的操作': 'nothing to redo',
    '[mpltweak] 重做：%s': '[mpltweak] redo: %s',
    '已重做：': 'redone: ',
    '[mpltweak] 框选 %d 个面板': '[mpltweak] box-selected %d panels',
    '[mpltweak] 图例吸附到 %s': '[mpltweak] legend snapped to %s',
    '[mpltweak] 缩放开始 ax%d %s': '[mpltweak] resize start ax%d %s',
    '[mpltweak] 选中 %d 个面板': '[mpltweak] selected %d panels',
    '[mpltweak] 多选拖拽 %d 个面板': '[mpltweak] multi-drag %d panels',
    '[mpltweak] 拖轴开始 ax%d': '[mpltweak] drag axes start ax%d',
    '移动 %d 个面板': 'move %d panels',
    '[mpltweak] DBG ax%d 按下=%s 实际=%s locked=%s origin=%s resize=%s snap=%s (gx=%s gy=%s)': '[mpltweak] DBG ax%d press=%s actual=%s locked=%s origin=%s resize=%s snap=%s (gx=%s gy=%s)',
    '拖%s端 = 调长度（Alt = 中心对称缩放）｜ %s': 'drag the %s end = resize (Alt = scale about centre) | %s',
    '下/上': 'bottom/top',
    '左/右': 'left/right',
    '[mpltweak] 缩放结束 ax%d %s -> [%.3f,%.3f,%.3f,%.3f]': '[mpltweak] resize end ax%d %s -> [%.3f,%.3f,%.3f,%.3f]',
    '[mpltweak] 拖轴结束 ax%d -> [%.3f,%.3f,%.3f,%.3f]': '[mpltweak] drag axes end ax%d -> [%.3f,%.3f,%.3f,%.3f]',
    '[mpltweak] 标题字号 -> %s': '[mpltweak] title fontsize -> %s',
    '[mpltweak] x轴标签字号 -> %s': '[mpltweak] xlabel fontsize -> %s',
    '[mpltweak] y轴标签字号 -> %s': '[mpltweak] ylabel fontsize -> %s',
    '[mpltweak] x刻度字号 -> %s': '[mpltweak] xtick fontsize -> %s',
    '[mpltweak] y刻度字号 -> %s': '[mpltweak] ytick fontsize -> %s',
    '[mpltweak] 图例字号 -> %s': '[mpltweak] legend fontsize -> %s',
    '[mpltweak] colorbar 刻度字号 -> %s': '[mpltweak] colorbar tick fontsize -> %s',
    '[mpltweak] colorbar 标签字号 -> %s': '[mpltweak] colorbar label fontsize -> %s',
    '[mpltweak] 文本字号 -> %s': '[mpltweak] text fontsize -> %s',
    '[mpltweak] 字号调节失败:': '[mpltweak] fontsize adjust failed:',
    '线条模式 ON（空格恢复）': 'line mode ON (space to restore)',
    '[mpltweak] 方向键：先把鼠标移到要调的子图上': '[mpltweak] arrow keys: hover the panel you want to adjust first',
    '[mpltweak] 线宽：鼠标悬停在线条或先选中含线条的轴': '[mpltweak] linewidth: hover a line, or select an axes that has lines',
    '线宽 %.1f': 'linewidth %.1f',
    '[mpltweak] 线宽调节失败:': '[mpltweak] linewidth adjust failed:',
    '[mpltweak] 颜色：鼠标悬停到线条上（plot 线 / 等值线），或先选中含线条的轴': '[mpltweak] color: hover a line (plot line / contour), or select an axes that has lines',
    '颜色 %s': 'color %s',
    '[mpltweak] 线条颜色 -> %s': '[mpltweak] line color -> %s',
    '[mpltweak] 颜色调节失败（%s）: %s': '[mpltweak] color adjust failed (%s): %s',
    '[mpltweak] colormap：鼠标移到 colorbar 或含 mappable 的轴上': '[mpltweak] colormap: hover the colorbar or an axes with a mappable',
    '[mpltweak] colormap 调节失败:': '[mpltweak] colormap adjust failed:',
    '[mpltweak] %sscale 调节失败: %s': '[mpltweak] %sscale adjust failed: %s',
    '[mpltweak] 吸附=%s': '[mpltweak] snap=%s',
    '吸附': 'snap',
    '关': 'off',
    '开': 'on',
    '参数未保存：已被新窗口接管（见终端）': 'params not saved: taken over by a new window (see the terminal)',
    '[mpltweak] 已导出 %d 个子图 -> %s': '[mpltweak] exported %d panels -> %s',
})

# ---- t71：P0-2 逃生通道的交互提示（zh/en 双语走 toolbox 的 _u；这里只做 §9 覆盖登记）----
UI_ZH_TO_EN.update({
    '穿透选中 ax%d（候选 %d 个）': 'picked through to ax%d (%d candidates)',
    'Tab 循环：ax%d/%d': 'Tab cycle: ax%d/%d',
})
