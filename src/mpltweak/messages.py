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
#   2  USAGE      "跑不起来"：参数非法、文件不存在、脚本报错、工具自身异常
#   3  SCRIPT     交互命令（launch）找不到要打开的脚本文件
#
# 为什么把「工具自身异常」也归 2 而不是 1：CI 里 1 代表"要去修图"，
# 工具自己炸了却报 1，用户会去看图找问题——找不到，因为问题在工具里（CH-6）。
#
# 0.1.10 起：同为 2，输出会把失败**归因**到脚本侧（跑不起来 / 需要参数 / 无头
# 跑不通需 GUI）还是工具侧（internal_error）分开报 —— 每类各给一句可照抄的
# 下一步；--json 用 `attribution` 字段区分（agent 可据此自动分支）。
# **退出码数值语义不变**（0.1.9 已对外文档化，改动会破坏用户 CI）。
# ---------------------------------------------------------------------------
EXIT_OK = 0
EXIT_PROBLEM = 1
EXIT_USAGE = 2
EXIT_INTERNAL = 2
EXIT_SCRIPT = 3


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
    # 无感化（0.1.11，用户 2026-10-13 认可）：**成功路径收成一行**，细节（逐处清单 /
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
    'chk_overlap': {
        'zh': '%(a)s 与 %(b)s 重叠（%(ox).3f × %(oy).3f）',
        'en': '%(a)s overlaps %(b)s (%.3f x %.3f)',
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
