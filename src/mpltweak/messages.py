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
    'written_block': {
        'zh': '[OK] 已写回: %(script)s',
        'en': '[OK] Written back: %(script)s',
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
    text = _json.dumps(obj, ensure_ascii=False, indent=indent) + '\n'
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


def fail_json(error_code, msg):
    """失败路径也要给出结构化结果（写 fd1，绕过 Python 层重定向）。

    契约：``--json``（以及 ``describe`` 这种"永远输出 JSON"的命令）在**任何**
    失败路径上都必须给一个可解析的 JSON。否则 agent 只能拿到空 stdout，而
    README 承诺的"过程信息走 stderr / 机器读 JSON"在失败路径上完全不成立
    （t3-#11 / t6-H2 的失败路径）。
    """
    import json as _json
    try:
        os.write(1, (_json.dumps({'ok': False, 'error_code': error_code,
                                  'error': msg}, ensure_ascii=False)
                     + '\n').encode('utf-8'))
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
                try:
                    os.write(1, (_json.dumps(
                        {'ok': False, 'error_code': 'internal_error',
                         'error': _err}, ensure_ascii=False) + '\n').encode('utf-8'))
                except OSError:
                    pass
            sys.stderr.write('[mpltweak] 未处理的错误: %s\n' % _err)
            if os.environ.get('MPLTWEAK_DEBUG'):
                traceback.print_exc(file=sys.stderr)
            return 1

    return wrapper
