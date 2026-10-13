# -*- coding: utf-8 -*-
"""工程卫生棘轮：把两条"只写在文档里、没有任何测试钉住"的约束变成可执行的。

1. **宽兜底 ``except Exception`` 不许不登记地增长。**
   这类兜底在 GUI 回调与运行时探测里是**对的**（一个坏回调不该弄死整个调图会话），
   但堆到一百多处之后，读代码的人分不清"预期内降级"和"在掩盖 bug"。一次性收窄的
   回归风险太大，所以这里只做棘轮：每个文件的处数**不得超过基线**（降下来是好事，
   不拦），新模块一律从 0 起算。要新增一处，先在 ``BASELINE`` 里登记理由。
   完整清单与"可窄化"提示：``python tools/audit_exceptions.py --markdown``。

2. **消息表必须 GBK 可编码。**
   ``messages.py`` 顶部写明：中国 Windows 上 stdout 被捕获时编码是 cp936 且
   ``errors='strict'``，一个 U+2713（✓）就能把"写回成功"变成 rc=1 +
   UnicodeEncodeError——而且**崩在写回落盘之后**（用户看到失败，磁盘上其实已经改了）。
   这条约束原先只存在于注释里，加进来之后新消息写错符号会立刻被拦下。

3. **测试套件自己也不许在 GBK 控制台把套件打挂**（T13 / 集成期真失败）。
   集成时在**不设** ``PYTHONIOENCODING`` 的 GBK 控制台上跑
   ``python tests/test_writeback.py``：§52 的 detail 里混进 ``U+FFFD``
   （子进程 GBK 字节被 ``decode('utf-8','replace')`` 解出来）→ ``print`` 抛
   UnicodeEncodeError → 整套用例从 ALL PASS 变成 rc=1 的**假红**（CI 却永远是绿的，
   因为 CI 是 UTF-8）。这里用两条守卫钉住：静态（危险文件必须装输出兜底）
   + 动态（真的在 ``PYTHONIOENCODING=gbk`` 的子进程里跑一遍清洗函数）。
"""

import ast
import glob
import os
import re
import subprocess
import sys

import pytest

# 本文件自己的字符串字面量里就有一个 U+2713（上面那段说明）→ 按下面第 3 条守卫的
# 规则，本文件也要装输出兜底（pytest 捕获时它不会崩，但规则要一致、也防手工直跑）。
try:
    sys.stdout.reconfigure(errors='replace')
    sys.stderr.reconfigure(errors='replace')
except (AttributeError, ValueError, OSError):
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, 'src', 'mpltweak')
TESTS = os.path.dirname(os.path.abspath(__file__))
if os.path.join(ROOT, 'src') not in sys.path:
    sys.path.insert(0, os.path.join(ROOT, 'src'))


def _pin_child_env(base=None, **overrides):
    """起子进程前的**最后一道闸**：硬钉 ``MPLBACKEND=Agg``（覆盖宿主投毒）。

    为什么硬钉而不是 ``setdefault``：宿主环境若把 ``MPLBACKEND`` 设成
    ``Qt5Agg`` / ``TkAgg``，``setdefault`` 会**原样保留** → 子进程落到交互后端 →
    脚本里任何 ``plt.show()`` / 建图都真开窗、抢焦点与鼠标（2026-10-12 用户当天报
    "有人抢鼠标"；产品侧已在 verify/writeback 里钉死，测试侧的级联入口是同一天补的）。
    ``test_sandbox._pin_child_env()`` 与 ``sandbox_env`` 是同一个闸门的不同副本；
    ``tests/test_hygiene.py`` 的棘轮按 AST 调用点逐个检查"有没有经过闸门"。

    **只钉后端**，不替调用方决定输出编码：本文件唯一的调用点要的正是
    ``PYTHONIOENCODING=gbk``（逼真 GBK 控制台），塞一个 utf-8 默认值会把那条用例
    变成恒真。需要别的键就用 ``**overrides`` 显式给。
    """
    out = dict(os.environ if base is None else base)
    out['MPLBACKEND'] = 'Agg'
    out.update(overrides)
    return out


# 基线：以 `python tools/audit_exceptions.py` 的输出为准（2026-10 实测；下表合计
# 158 处 —— 其中 writeback.py 比 0.1.8 的 5 处多 4 处，见下面的登记理由）。
# 只允许降。要涨，必须在本表里改数字，并在 PR 里说明为什么无法窄化到具体异常。
# 已登记的例外：
#   * verify.py 15 → 19（2026-10-10）：新增 `_collect_texts` / `_ax_identity` 的
#     "尽力采集"兜底 —— 它们采的是**附加**信息（文字外框 / 轴身份），
#     采不到就该退化成空，绝不能因为某个 artist 取不到外框就让整个 check 失败。
#     每个点都写在 docstring / 行内注释里说明了这一点。
BASELINE = {
    # toolbox.py 105 → 112（2026-10-12，0.1.10 实机 bug 修复，**船长已裁决批准**）：
    #   +1  `_fit_to_content` 的 fig.get_tightbbox 并入内容盒（0.1.10#1-①）——
    #       不同 matplotlib 版本的 fig-level tightbbox 可能抛不同异常，采不到就
    #       退化成"不含 fig 级文字"，绝不能因此让 Ctrl+F 崩掉；
    #   +1  `_on_resize` 的钳制检测（0.1.10#1-③）—— 窗口被系统钳制时恢复 figsize，
    #       任何后端/画布异常都只该跳过（宽兜底，否则 resize 事件里一崩就挂会话）；
    #   +3  `_screen_available`（0.1.10#2-①）—— Qt availableGeometry 与 Tk
    #       winfo_screen 两套探测各自 try（外层 Qt 1 + 内层 w.screen() 1 + Tk 1），
    #       后端差异（无 screen() / 无 winfo_*）都必须静默跳过；
    #   +1  `_fit_to_screen`（0.1.10#2-①）—— 开窗自动缩放：无窗口管理器/拿不到
    #       屏幕尺寸/缩放失败 都应静默跳过（headless 与无窗口环境不打扰）；
    #   +1  `__init__` 开窗后的缩放+记录（0.1.10#2-①/#1-③）—— 无窗口管理器时
    #       不打扰（Agg 测试/headless 直接跳过）。
    'toolbox.py': 112,
    # launch.py 16 → 18（2026-10-12，**船长已裁决批准**）：t12（可重跑预检）在
    # launch.py 新增两处**刻意的防御性宽兜底**
    # （`# noqa: BLE001 - 预检绝不让开窗失败` / `- 输出失败也不许影响开窗`）。
    # 此处不宜收窄 —— 预检自身失败的原因不可穷举，而契约就是"绝不让预检破坏开窗"。
    # 船长 2026-10-12 授权，审计已登记。**这一行归属 t12，别人不要再动**（避免重复计数）。
    'launch.py': 18,
    # preflight.py：新模块，登记 1 处（2026-10-12，**船长已裁决批准**）。
    # 那一处是 `analyze()` @469 的"失败即放行"兜底（`# noqa: BLE001 - 预检绝不让
    # apply/开窗失败`）。**不宜收窄**：t12 的验收要求"预检自身异常不得导致
    # apply/开窗失败（失败即视为无风险继续）"，而预检失败的原因**不可穷举**
    # （读文件 / ast / find_spec / 用户畸形源码…）—— 收窄成具体异常就等于把未知异常
    # 变成**新的失败点**，正好违背该条。与 launch.py 那两处同性质：
    # 是"有理由的防御性兜底"，不是"顺手包的宽兜底"。
    # 船长 2026-10-12 授权，审计已登记。**这一行归属 t12，别人不要再动**。
    'preflight.py': 1,
    'verify.py': 19,
    # writeback.py 5 → 9（2026-10-10，**船长已裁决批准**）：写盘**之后**的验证步骤
    # （verify_run / verify_semantic，原位与块两条路径各两处）改成
    # `except BaseException` + 回滚。
    # 这四处**必须**是"回滚兜底，必须宽"：写盘已经发生，任何异常（含 verify.compare
    # 里的 float(None) TypeError）都只能靠"整段兜住再回滚"来保证"报失败就一定不留
    # 半成品"（审计 E3：过去异常会穿过所有 except，脚本里留下一整块调整块，而命令
    # 只报 internal_error）。窄化到具体异常类型就会漏掉下一种未知异常 —— 正是这条
    # 纪律要防的事。四处源码注释里都写明了"回滚兜底，必须宽"。
    'writeback.py': 9,
    'messages.py': 5,
    'apply.py': 1,
    'layoutwarn.py': 1,
    # revert.py：2 处（拿写回锁失败、释放锁失败）—— 都是"锁不可用也不能崩"的兜底，
    # 与 apply.py 里同类处理一致；锁本身是尽力串行化，不是正确性依赖。
    'revert.py': 2,
    'params.py': 0,
    'check.py': 0,
    'describe.py': 0,
    'mcp_server.py': 0,
    'cli.py': 0,
    '__init__.py': 0,
    '__main__.py': 0,
}


def count_broad_excepts(path):
    """数"宽兜底"：``except:`` / ``except Exception`` / ``except BaseException``。"""
    with open(path, 'r', encoding='utf-8') as f:
        tree = ast.parse(f.read())
    n = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        if node.type is None:
            n += 1
        elif (isinstance(node.type, ast.Name)
              and node.type.id in ('Exception', 'BaseException')):
            n += 1
    return n


def test_all_modules_registered():
    """新增模块必须登记：新文件默认 0 处，出现兜底就得说明理由。"""
    files = sorted(f for f in os.listdir(SRC) if f.endswith('.py'))
    missing = [f for f in files if f not in BASELINE]
    assert not missing, (
        'src/mpltweak 下有未登记的新模块：%s —— 请在 tests/test_hygiene.py 的 '
        'BASELINE 里登记（通常应为 0：新模块不该一上来就用宽兜底）' % missing)


@pytest.mark.parametrize('name', sorted(BASELINE))
def test_except_exception_ratchet(name):
    path = os.path.join(SRC, name)
    if not os.path.exists(path):
        pytest.skip('%s 不存在了（已删除/改名 → 记得同步 BASELINE）' % name)
    n = count_broad_excepts(path)
    assert n <= BASELINE[name], (
        '%s 的宽兜底从 %d 涨到 %d。要么收窄到具体异常（ValueError / OSError / '
        'matplotlib 具体类型），要么在 BASELINE 里登记并说明理由。'
        '清单：python tools/audit_exceptions.py --markdown'
        % (name, BASELINE[name], n))
    if n < BASELINE[name]:
        # 降下来不拦，但提醒把基线一起降掉，免得棘轮越跑越松
        print('%s: 宽兜底 %d < 基线 %d —— 请顺手把 BASELINE 改成 %d'
              % (name, n, BASELINE[name], n))


def test_messages_are_gbk_encodable():
    """面向终端的所有消息模板都要能编成 GBK（t4-F2 那类崩在落盘之后的坑）。"""
    from mpltweak import messages as _msg
    bad = []
    for key, item in _msg.MESSAGES.items():
        for lang, tpl in item.items():
            try:
                tpl.encode('gbk')
            except UnicodeEncodeError as e:
                bad.append('%s[%s]: %s' % (key, lang, e))
    assert not bad, (
        '这些消息模板不能编成 GBK，会在 cp936 终端上把命令变成 rc=1 + '
        'UnicodeEncodeError：\n  ' + '\n  '.join(bad))


def test_message_lookup_never_raises():
    """``t()`` 的容错契约：key 缺失 / 参数缺失都返回文本，绝不抛异常。"""
    from mpltweak import messages as _msg
    old = _msg.current_lang()
    try:
        _msg.set_lang('zh')
        assert _msg.t('written_inplace', script='a.py').endswith('a.py')
        assert _msg.t('no_such_key') == 'no_such_key'
        # 少给参数时**原样返回模板**（不抛异常、也不丢成空串）—— 这条能失败：
        # 返回 None / 抛 KeyError / 返回空串都会被抓住。
        _tpl = _msg.MESSAGES['written_inplace'][_msg.current_lang()[:2]] \
            if _msg.current_lang()[:2] in _msg.MESSAGES['written_inplace'] \
            else _msg.MESSAGES['written_inplace']['zh']
        assert _msg.t('written_inplace') == _tpl
    finally:
        _msg.set_lang(old)


# --- P0-1（方案 B）：无 PyQt5 → Tk 开窗 + stderr 人话提示；有 PyQt5 → 不提示 ---
def test_detect_backend_qt_preferred(monkeypatch):
    """Qt 可用时优先选 Qt（现行为），不落 Tk。"""
    import matplotlib
    import matplotlib.pyplot as plt
    import mpltweak.launch as launch
    calls = []
    monkeypatch.setattr(matplotlib, 'use',
                        lambda backend, force=True: calls.append(backend))
    monkeypatch.setattr(plt, 'figure', lambda *a, **k: object())
    monkeypatch.setattr(plt, 'close', lambda *a, **k: None)
    name, err = launch._detect_backend()
    assert name == 'QtAgg' and err is None
    assert calls == ['QtAgg']                    # 第一次命中即止，不继续试探


def test_detect_backend_tk_fallback(monkeypatch):
    """PyQt5/PySide 全 import 失败（monkeypatch 模拟）→ 退回 TkAgg。"""
    import matplotlib
    import matplotlib.pyplot as plt
    import mpltweak.launch as launch
    calls = []

    def fake_use(backend, force=True):
        calls.append(backend)
        if str(backend).lower().startswith('qt'):
            raise ImportError('no qt binding')
        return backend

    monkeypatch.setattr(matplotlib, 'use', fake_use)
    monkeypatch.setattr(plt, 'figure', lambda *a, **k: object())
    monkeypatch.setattr(plt, 'close', lambda *a, **k: None)
    name, err = launch._detect_backend()
    assert name == 'TkAgg' and err is None
    assert calls == ['QtAgg', 'Qt5Agg', 'Qt6Agg', 'TkAgg']


def test_emit_backend_hint_cases(monkeypatch):
    """提示只在"Tk 且非 dry-run 且未静默"时出现；Qt / dry-run / NO_HINT 都静默。"""
    import mpltweak.launch as launch
    import mpltweak.messages as _msg
    lines = []

    class _Sink:
        def write(self, s):
            lines.append(s)

    # launch._backend_tk_hint() 内部 `from . import messages`，patch 同一个模块的
    # MESSAGES 表即可让文案确定可断言（cli-hunter 尚未补 backend_tk_hint 键）。
    monkeypatch.setattr(_msg, 'MESSAGES', {
        'backend_tk_hint': {'zh': 'HINT-ZH', 'en': 'HINT-EN'}})

    sink = _Sink()
    assert launch._emit_backend_hint('TkAgg', environ={}, stderr=sink) is True
    assert ''.join(lines) in ('HINT-ZH\n', 'HINT-EN\n')

    lines.clear()
    assert launch._emit_backend_hint('QtAgg', environ={}, stderr=sink) is False
    assert lines == []

    lines.clear()
    assert launch._emit_backend_hint('TkAgg', dry_run=True, environ={},
                                     stderr=sink) is False
    assert lines == []

    lines.clear()
    assert launch._emit_backend_hint('TkAgg', environ={'MPLTWEAK_NO_HINT': '1'},
                                     stderr=sink) is False
    assert lines == []
# --------------------------------------------------------------------------
# 第 3 条棘轮：测试套件自己也不许在 GBK 控制台上把套件打挂（T13）
# --------------------------------------------------------------------------
_CONSOLE_MARK = 'reconfigure(errors='

# 已知的历史豁免（T13/T15 收口后只剩 test_suites.py 一个，**不在我的改动范围内**）：
# 登记以免守卫对"别人负责的文件"误伤。**新文件一律不许加进这里** —— 守卫就是为拦住
# 新文件才存在的；某个文件一旦补上输出兜底，就从这张表里删掉（本次已删掉
# test_agent_api.py 与 test_check.py，见 §T15）。
#
# * test_suites.py —— cli-hunter 的范围（T8 起的 MCP/env 收口也归他），理由如实记录：
#   它是 pytest 的入口包装，自带"多编码依次试"的解码器（`raw.decode(_enc)`，
#   `decode('utf-8','replace')` 只是最后兜底），且子进程输出**只进 AssertionError**
#   （由 pytest 的捕获层负责编码，不自己 print 到控制台）→ GBK 控制台下不会把套件打挂。
#   若要彻底一致，补上同样两行 `reconfigure(errors='replace')` 即可删表。
LEGACY_NO_CONSOLE_FALLBACK = {
    'test_suites.py': 'cli-hunter 范围：多编码解码器 + 输出只进断言消息（pytest 捕获层编码）',
}


def _str_literals(text):
    """文件里所有字符串字面量的内容（注释不算）——用于"会不会打印出不可编码字符"。"""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    return [n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)]


def _uses_replace_codec(text):
    """文件里是否有 ``decode/encode(..., 'replace')``（含 ``errors='replace'``）。

    这一类调用会把非法字节变成 U+FFFD —— 拿去 print 就是 GBK 控制台上的
    UnicodeEncodeError（T13 的实际根因）。用 AST 判，避免注释/文档里的字样误伤。
    """
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return "errors='replace'" in text or 'errors="replace"' in text
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call)
                and getattr(node.func, 'attr', None) in ('decode', 'encode')):
            continue
        vals = [a for a in node.args if isinstance(a, ast.Constant)]
        vals += [k.value for k in node.keywords if isinstance(k.value, ast.Constant)]
        if any(v.value == 'replace' for v in vals):
            return True
    return False


def _gbk_unencodable(chars_text):
    bad = set()
    for ch in chars_text:
        try:
            ch.encode('gbk')
        except UnicodeEncodeError:
            bad.add(ch)
    return bad


def _read_test_file(path):
    """读测试文件：容忍 UTF-8 BOM（PowerShell 的 Set-Content -Encoding UTF8 会写 BOM，
    带 BOM 的 .py 对 Python 合法，但 `ast.parse` 会报 invalid non-printable character）。"""
    with open(path, encoding='utf-8-sig') as f:
        return f.read()


@pytest.mark.parametrize('name', sorted(
    os.path.basename(p)
    for p in glob.glob(os.path.join(TESTS, 'test_*.py'))))
def test_test_suites_cannot_crash_gbk_console(name):
    """测试文件必须自带输出兜底，否则一个字符就能让套件在本地**假红**。

    危险信号二选一，命中就必须在文件里装 ``reconfigure(errors=...)``：
      * 用 ``errors='replace'`` 解子进程输出（会产出 U+FFFD，集成期实际踩到）；
      * 字符串字面量里有 GBK 编不出的字符（例如 U+2713 ✓ / U+26A0 ⚠）。
    背景：CI 是 UTF-8 永远绿，本地 GBK 控制台一跑就 UnicodeEncodeError → rc=1，
    而结论其实是 ALL PASS（T13 逐字证据见 fix_engine.md §T13）。
    """
    path = os.path.join(TESTS, name)
    text = _read_test_file(path)
    if name in LEGACY_NO_CONSOLE_FALLBACK:
        pytest.skip('历史豁免：%s' % LEGACY_NO_CONSOLE_FALLBACK[name])
    hazard = _uses_replace_codec(text) or _gbk_unencodable(
        ''.join(_str_literals(text)))
    if not hazard:
        pytest.skip('没有把任意文本交给 print 的迹象')
    assert _CONSOLE_MARK in text, (
        '%s 可能把不可编码字符交给 print（GBK 控制台下会 UnicodeEncodeError 把'
        '整套用例打挂）。请在文件开头加：\n'
        "    for _s in (sys.stdout, sys.stderr):\n"
        "        try:\n"
        "            _s.reconfigure(errors='replace')\n"
        "        except (AttributeError, ValueError, OSError):\n"
        "            pass" % name)


def test_writeback_console_sanitizers_work_on_gbk():
    """动态守卫：在 ``PYTHONIOENCODING=gbk`` 的子进程里真跑一遍 test_writeback 的清洗。

    覆盖两件事（任何一件回退都会让本用例失败）：
      ① 打印含 ``U+FFFD`` 的文本（照 check() 的格式）不许抛；
      ② ``_decode_out`` 对 GBK 字节要解出**真正的中文**，而不是整片 '?'。
    """
    src_path = os.path.join(TESTS, 'test_writeback.py')
    src = _read_test_file(src_path)
    tree = ast.parse(src)
    funcs = {n.name: ast.get_source_segment(src, n) for n in tree.body
             if isinstance(n, ast.FunctionDef)}
    for fn in ('_gbk_safe', '_decode_out'):
        assert fn in funcs, 'test_writeback.py 里的 %s 不见了（T13 的兜底被删）' % fn
    probe = '\n'.join([
        'import sys',
        "sys.stdout.reconfigure(errors='replace')",
        funcs['_gbk_safe'],
        funcs['_decode_out'],
        # ① 清洗后的文本必须**编得出来**（这就是"清洗"的定义）——`_gbk_safe` 被改成
        #    原样返回时，这一行会在 GBK 控制台下抛 UnicodeEncodeError。
        "s = _gbk_safe('\\ufffd\\u26a0 mixed 中文')",
        "s.encode('gbk')",
        "assert '\\ufffd' not in s, 'U+FFFD 没被清洗掉'",
        "print('PASS | ' + s)",
        # ② GBK 的 "\xd6\xd0\xce\xc4" == '中文'；解不出来就是丢了信息
        "assert _decode_out(b'\\xd6\\xd0\\xce\\xc4') == '\\u4e2d\\u6587', 'gbk 解码退化'",
        # ③ 非法 utf-8 字节序列也要能兜住（不抛、不崩）
        "assert isinstance(_decode_out(b'\\xff\\xfe\\x00abc'), str)",
        "print('PROBE_OK')",
    ])
    env = {k: v for k, v in os.environ.items()
           if k not in ('PYTHONIOENCODING', 'PYTHONUTF8')}
    # 逼真 GBK 控制台 + src 路径：都经闸门**显式覆盖**（不再手改 dict）
    # 级联闸门（2026-10-12 抢鼠标事故）：上面那个 dict 是从 os.environ 手搓出来的，
    # 会**原样保留宿主的 MPLBACKEND** —— 宿主设成 Qt5Agg/TkAgg 时，这个子进程
    # （以及它拉起的每个孙进程）就可能落到交互后端。一律经 _pin_child_env 硬钉 Agg。
    env = _pin_child_env(env, PYTHONIOENCODING='gbk', PYTHONPATH=os.path.join(ROOT, 'src'))
    r = subprocess.run([sys.executable, '-c', probe], capture_output=True, env=env)
    out = (r.stdout + r.stderr).decode('gbk', 'replace')
    assert r.returncode == 0, 'GBK 子进程里清洗函数崩了：\n' + out
    assert 'PROBE_OK' in out, out
def test_ci_builds_the_distributions_and_guards_the_sdist():
    """T26：CI 必须**构建**发行物，并挡住 sdist 里的陈旧构建元数据。

    为什么把 CI 文件也纳入棘轮：0.1.8 的 `src/mpltweak.egg-info/*` 是"从不构建
    wheel/sdist 就永远发现不了"的那类问题 —— 修了 MANIFEST.in 之后，本地也没有
    任何地方能证明它真的还在生效。这里只做静态断言（真正的构建由 workflow 自己跑），
    但足以挡住"以后有人把这一步删掉/改坏"。
    """
    wf = os.path.join(ROOT, '.github', 'workflows', 'tests.yml')
    assert os.path.exists(wf), 'CI 工作流不见了：%s' % wf
    text = open(wf, encoding='utf-8').read()
    assert 'python -m pip install build' in text, \
        'CI 没有安装 build（构建步骤缺失或不完整）'
    assert 'python -m build' in text, \
        'CI 从不构建 wheel/sdist —— 打包缺件类问题只能等到发版才发现'
    for _key in ('egg-info', 'PKG-INFO', 'requires.txt', 'top_level.txt'):
        assert _key in text, \
            'CI 里没有对 sdist 陈旧元数据的断言（缺少 %r）' % _key
    assert 'KiB' in text, 'CI 没有打印发行物体积（以后没法对比大小）'


# ==========================================================================
# 第 4 / 5 条棘轮：测试侧不许再"抢鼠标"（2026-10-12 用户报"有人抢鼠标"）
# ==========================================================================
# 事故的三条真实路径（这就是为什么是**两道**棘轮，而不是一道）：
#   ① 某成员测试夹具里一句 `tk.Tk()` —— tkinter **直接建窗**，`MPLBACKEND=Agg`
#      挡不住（那管的是 matplotlib 后端，与 tkinter 无关）→ 第 5 条棘轮管它。
#   ② 有成员跑过 4 次真窗口探针（`win.show()` + `win.raise_()`）→ 真窗口测试必须
#      留白名单（它是**故意**要窗口的）。
#   ③ `tests/test_hygiene.py` 的级联 env 用 `{k: v for k, v in os.environ.items() …}`
#      从宿主环境手搓 → **原样保留宿主的 MPLBACKEND** → 它拉起的每个套件子进程都
#      可能落到 Qt/Tk → 第 4 条棘轮管它（每个子进程调用点都必须过闸门）。
#
# 【判据：AST 调用点级，不是词元/文本计数】
# 文本计数会被三类噪声骗过（实测，2026-10-12）：
#   * `import subprocess`  —— Import 语句，不是调用；
#   * `subprocess.DEVNULL` —— Attribute 取属性，不是调用；
#   * 夹具里作为**被测文本**的调用字符串，例如 `test_sandbox.py:181` 的
#     `"subprocess.run([%r, '-c', 'pass'])"` —— 那是 ast.Constant，不是调用。
# 实测对照：`tests/test_sandbox.py` 文本命中 12 处 / AST 调用点**只有 3 处**
# （129 / 423 / 452，3/3 都过了 `_pin_child_env` 闸门）。
# **文件级命中数仅供参考，调用点级才是判据** —— 下面两个棘轮都按调用点判。
#
# 【反向验证（两条都给了执行版；想在真实树上手工复现可照做，看完记得改回）】
#   ① 去掉一处真需要的 pin：临时删掉 `tests/test_sandbox.py` 的
#      `_pin_child_env()` 里那行 `env['MPLBACKEND'] = 'Agg'` → 本文件的棘轮一
#      应报 `test_sandbox.py` 的 3 处调用点"没过级联闸门"。
#   ② 在测试里加一行 `tk.Tk()`：临时往任一**非白名单**测试文件里加 → 棘轮二应报红。
#   执行版（不改真文件、可反复跑）：`test_ratchet_reverse_probe_unpinned_child_call`
#   与 `test_ratchet_reverse_probe_real_gui_object`（各自还验证"补回去/加白名单后复绿"）。
#
# 【子进程调用点对账表（2026-10-12 逐处人工核对；"pin"=该 env 显式含 MPLBACKEND='Agg'）】
# | 文件 | 调用点 | 用途 | env 来源 | pin |
# |---|---|---|---|---|
# | test_writeback.py | 50 | 线性套件：apply / revert / describe | `_env15`（模块级 dict 含 Agg）/ `_env52` / 4 处内联 dict 含 Agg | 50/50 |
# | test_agent_api.py | 7 | CLI / API 驱动 | 380、754 内联含 Agg；100（`run_cli(env=None)`）/437/1031/1152/1167 走继承 | 2/7（5 处登记在 UNPINNED_CALLSITES） |
# | test_corpus_invariants.py | 2 | 语料 worker / apply CLI | `_cli_env()`（函数内 dict 含 Agg）/ `py_env`（dict 含 Agg） | 2/2 |
# | test_sandbox.py | 3 | 沙箱捣蛋脚本 / apply | `_pin_child_env(dict(env))` | 3/3 |
# | test_mcp.py | 2 | describe / apply（MCP 夹具） | `env5`（dict 含 Agg） | 2/2 |
# | test_check.py | 1 | CLI `run()` | `_env`（函数内 `_env['MPLBACKEND']='Agg'`） | 1/1 |
# | test_suites.py | 1 | 逐套件子进程 | `env`（dict 含 Agg） | 1/1 |
# | test_fuzz_params.py | 1 | 端到端 apply | `env`（dict 含 Agg） | 1/1 |
# | test_property_writeback.py | 1 | apply 子进程 | 内联 dict 含 Agg | 1/1 |
# | test_hygiene.py | 1 | GBK 控制台探针（本次修） | `_pin_child_env(...)` | 1/1 |
# | test_real_window.py | 2 | 真窗口探针 / taskkill | 故意**不带** Agg（要真窗口） | 白名单（见下） |
# | tests/sandbox/sitecustomize.py | 0 | 审计钩子本体 | —— 只拦别人的子进程，自己不起进程 | 不需要 env pin |
# | tests/sandbox/sandbox_env.py | 0 | env 构造器 | —— 构造 env，不起进程 | 不需要 env pin |
_SUBPROC_FUNCS = ('run', 'Popen', 'call', 'check_output', 'check_call')

# 真实 GUI 对象（建窗/事件循环）——命中即报红；白名单是**唯一**豁免口
GUI_CONSTRUCTORS = ('Tk', 'Toplevel', 'QApplication', 'QWidget', 'QMainWindow',
                    'QDialog', 'QMessageBox', 'QFileDialog',
                    'FigureCanvasQTAgg', 'FigureCanvasTkAgg',
                    'FigureManagerQT', 'FigureManagerTkAgg')
GUI_LOOP_ATTRS = ('mainloop', 'exec_')
# "抢焦点/抢鼠标"那一类（用户 2026-10-12 报的正是这个）：任何接收者上一出现就报红
GUI_FOCUS_ATTRS = ('raise_', 'activateWindow', 'showMaximized', 'lift')
GUI_SHOW_BASES = ('plt', 'pyplot', 'fig', 'figure', 'canvas')
GUI_BACKEND_PREFIXES = ('qt', 'tk', 'gtk', 'wx', 'macosx', 'webagg', 'nbagg',
                        'ipympl')

GUI_WHITELIST = {
    'test_real_window.py':
        '真窗口自动化测试（按约定在用户不在电脑前时跑）：它**必须**真开窗，所以既不钉 '
        'MPLBACKEND=Agg，也不受禁 GUI 约束 —— 这是"有意为之"，不是漏网。',
}

# 未过闸门的调用点：**只许这份清单变短**。任何新文件/新调用点想走继承，必须先在这里
# 登记并写清理由（棘轮会核对"实测未 pin 数 <= 上限"）。
UNPINNED_CALLSITES = {
    'test_agent_api.py': (5,
        'run_cli(env=None) 走继承（1 处）+ 两个 `python -c` 驱动只 import '
        'mpltweak.messages（不起图，2 处）+ attr 夹具的 `_env21 = dict(os.environ)` '
        '（1 处）+ `--help` 取文本（1 处）。都不建图，风险为 0；但**继承口径**'
        '仍是缺口，已报船长（本文件不在本次改动范围，未改）。'),
}

# 「级联闸门」的**结构白名单**（t19，船长裁定 B）：整个文件豁免"未 pin 必须登记 + 计数
# 上限"这一套，因为它的**设计前提**就是让子进程能看到窗口 —— 对"未过级联闸门"这件事，
# 它的答案是"本来就不该 pin"，而不是"忘了 pin"。
# 为什么不用"数字上限"（t18 曾用 `test_real_window.py: 2`）：那个数字会被 t16 每加一个
# 探针就顶破一次，典型的脆弱闸门（本轮实测：加了 2 处 git 基线探针 → 2 涨到 4 → 假红）。
# **白名单不是免检**：下面 `audit_whitelisted_child_argv()` 是一道轻量结构守卫，
# 将来谁在这个文件里加一个"本该 pin 的后端相关子进程"照样报红。
CHILD_ENV_WHITELIST = {
    'test_real_window.py':
        '真窗口自动化测试（用户不在电脑前才跑）：它**必须**让子进程能开窗 —— '
        '`_run_proc` 的 env 显式 pop 掉 MPLBACKEND（见该文件顶部说明），所以'
        '"未过级联闸门"对它没有意义。它的子进程只有三类：① 场景子进程（故意的）、'
        '② `taskkill`、③ `git` 基线探针（rev-parse / status）—— 都与 matplotlib '
        '后端无关。',
}

# 结构守卫的允许集合（只对 CHILD_ENV_WHITELIST 里的文件生效）：
#   ① 系统工具（argv 首元素是字面量即可）；
#   ② Python 解释器 —— **必须显式传 env=**（谁启动解释器，谁负责那套环境变量）；
#   ③ 非字面量 argv（如 `Popen(cmd, ...)`）—— 同样**必须显式传 env=**。
# 这样"本该 pin 的后端相关子进程"（例如新写一处
# `subprocess.run([sys.executable, 'x.py'])` 且不传 env）仍会被抓住。
CHILD_ARGV0_TOOLS = ('taskkill', 'git', 'where', 'cmd', 'cmd.exe', 'sh', 'bash',
                     # tasklist: 只读的进程枚举（等价 `ps`），供真窗口测试做机器
                     # 负载自检用。与清单里已有的 `where` 同类（只读、非解释器），
                     # 把它当作"需要显式 pin 的危险 spawn"是棘轮的过紧，不是
                     # 调用点的错（t23 第 2 次修订，船长授权）。
                     'tasklist')
CHILD_ARGV0_PYTHONISH = ('sys.executable', 'PY', 'PYTHON', 'python', 'python3',
                         'python.exe', 'pythonw', 'pythonw.exe')


# 扫描时**跳过运行时/生成目录**（船长 2026-10-12 授权）：`tests/.tmp_writeback_*` 这类
# 目录是套件**跑的时候**建的（`.gitignore` 里同款 `__pycache__/`、`.tmp_*/`），里面是
# 生成的临时脚本；棘轮扫到它们会误报（多套件并发跑时尤其明显 —— t24 挂入 6 个新套件后
# 首轮全量就出现过一次瞬时红，复跑即绿）。**只排除目录，不放松任何判据**：
# 真实源文件（tests/*.py 与 tests/sandbox/*.py）照旧全覆盖。
_SKIP_DIR_PREFIXES = ('.tmp_', '__pycache__', '.pytest_cache', '.hypothesis')


def _iter_py_files(root):
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs
                   if not d.startswith(_SKIP_DIR_PREFIXES)]
        for fn in sorted(files):
            if fn.endswith('.py'):
                yield os.path.join(dirpath, fn)


def _subprocess_aliases(tree):
    """`from subprocess import run as r` 这类别名也要认得出来（别名 -> 原名）。"""
    out = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == 'subprocess':
            for a in node.names:
                if a.name in _SUBPROC_FUNCS:
                    out[a.asname or a.name] = a.name
    return out


def _call_qual(call, aliases):
    """``subprocess.run`` / 别名 → 限定名；不是 subprocess 调用返回 None。"""
    f = call.func
    if isinstance(f, ast.Attribute) and f.attr in _SUBPROC_FUNCS:
        if isinstance(f.value, ast.Name) and f.value.id == 'subprocess':
            return 'subprocess.%s' % f.attr
        return None
    if isinstance(f, ast.Name) and f.id in aliases:
        return 'subprocess.%s' % aliases[f.id]
    return None


def iter_subprocess_calls(tree, aliases=None):
    """**调用点级**清单（AST）：字符串字面量里的调用文本天然不算（那是 Constant）。"""
    aliases = _subprocess_aliases(tree) if aliases is None else aliases
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            q = _call_qual(node, aliases)
            if q:
                out.append((node, q))
    return sorted(out, key=lambda t: t[0].lineno)


def _scope_chain(tree, target):
    """从最内层到模块的包含作用域链（最后一项 None = 模块级）。"""
    chain = [n for n in ast.walk(tree)
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
             and n.lineno <= target.lineno <= (n.end_lineno or n.lineno)]
    chain.sort(key=lambda n: (n.end_lineno or n.lineno) - n.lineno)
    return chain + [None]


def _collect_scope(node, assigns, pin_names):
    """收集作用域内的赋值与 ``x['MPLBACKEND'] = 'Agg'``（**不进**嵌套 def/class）。"""
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef,
                         ast.Lambda)):
        return
    if isinstance(node, ast.Assign):
        for t in node.targets:
            if isinstance(t, ast.Name):
                assigns.setdefault(t.id, []).append(node.value)
            elif (isinstance(t, ast.Subscript) and isinstance(t.slice, ast.Constant)
                  and t.slice.value == 'MPLBACKEND'
                  and isinstance(node.value, ast.Constant)
                  and str(node.value.value).lower() == 'agg'):
                if isinstance(t.value, ast.Name):
                    pin_names.add(t.value.id)
    elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        assigns.setdefault(node.target.id, []).append(node.value)
    for ch in ast.iter_child_nodes(node):
        _collect_scope(ch, assigns, pin_names)


def _scope_facts(scope, tree):
    assigns, pin_names = {}, set()
    body = tree.body if scope is None else scope.body
    for st in body:
        _collect_scope(st, assigns, pin_names)
    return assigns, pin_names


def _dict_pins_agg(node):
    """Dict 字面量 / ``dict(...)`` 调用里是否显式含 ``MPLBACKEND='Agg'``。"""
    if isinstance(node, ast.Dict):
        for k, v in zip(node.keys, node.values):
            if (isinstance(k, ast.Constant) and str(k.value).upper() == 'MPLBACKEND'
                    and isinstance(v, ast.Constant)
                    and str(v.value).lower() == 'agg'):
                return True
        return False
    if isinstance(node, ast.Call) and getattr(node.func, 'id', None) == 'dict':
        for kw in node.keywords:
            if (kw.arg == 'MPLBACKEND' and isinstance(kw.value, ast.Constant)
                    and str(kw.value.value).lower() == 'agg'):
                return True
    return False


def _module_pins_environ(tree):
    """本文件模块级是否钉了 ``os.environ['MPLBACKEND'] = 'Agg'``（或 setdefault）。"""
    for st in tree.body:
        for node in ast.walk(st):
            if isinstance(node, ast.Assign) and len(node.targets) == 1:
                t = node.targets[0]
                if (isinstance(t, ast.Subscript)
                        and isinstance(t.value, ast.Attribute)
                        and t.value.attr == 'environ'
                        and isinstance(t.slice, ast.Constant)
                        and t.slice.value == 'MPLBACKEND'
                        and isinstance(node.value, ast.Constant)
                        and str(node.value.value).lower() == 'agg'):
                    return True
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == 'setdefault'
                    and isinstance(node.func.value, ast.Attribute)
                    and node.func.value.attr == 'environ'
                    and len(node.args) > 1
                    and isinstance(node.args[0], ast.Constant)
                    and node.args[0].value == 'MPLBACKEND'
                    and isinstance(node.args[1], ast.Constant)
                    and str(node.args[1].value).lower() == 'agg'):
                return True
    return False


def _local_func_verdict(tree, name, depth=0):
    """本地 def 是否是"钉 Agg 的闸门"（函数体里钉了后端）→ 'pinned' / None。"""
    if depth > 3:
        return None
    fn = None
    for node in ast.walk(tree):
        if (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == name):
            fn = node
            break
    if fn is None:
        return None
    assigns, pin_names = {}, set()
    for st in fn.body:
        _collect_scope(st, assigns, pin_names)
    # pin_names 里放的是"被钉的那个 env 变量名"（如 `out`），键与值已由 _collect_scope
    # 校验过必须是 `MPLBACKEND` + `'Agg'` —— 所以**非空即代表这个函数钉了后端**。
    # （首轮实测踩到的坑：写成 `if 'MPLBACKEND' in pin_names` 恒为假 → `_pin_child_env`
    # 闸门被误判成 unresolved，棘轮把自己仓库里已 pin 的 3 处也标红了。）
    if pin_names:
        return 'pinned'
    for vals in assigns.values():
        for v in vals:
            if _dict_pins_agg(v):
                return 'pinned'
    return None


def _resolve_env(tree, chain, node, call_lineno, depth=0):
    """把 ``env=`` 实参**溯源到定义处** → 'pinned' / 'unpinned' / 'unresolved' / 'inherited'。

    规则（与船长 2026-10-12 拍板的判据一致）：
      * 显式含 ``MPLBACKEND='Agg'`` 的 dict（字面量 / ``dict(..., MPLBACKEND='Agg')``）
        → pinned；
      * 手搓的 dict / ``dict(os.environ)`` **不含** Agg → unpinned（这正是级联入口的缺口）；
      * ``env=None`` / 缺省 → inherited（调用方再判"本文件模块级是否已 pin"）；
      * Name → 到本作用域链里找**调用点之前最后一次**赋值；同一作用域里有
        ``x['MPLBACKEND']='Agg'``（`test_check.run()` 那种两步写法）也算 pinned；
      * Call → 本地 def 闸门（函数体里钉了后端，如 `_pin_child_env` / `_cli_env`）
        → pinned；
      * 溯源不到（函数参数、未知构造器）→ unresolved —— **fail-closed**：
        和 unpinned 一样要求登记，宁可不放行。
    """
    if depth > 4:
        return 'unresolved'
    if node is None or (isinstance(node, ast.Constant) and node.value is None):
        return 'inherited'
    if _dict_pins_agg(node):
        return 'pinned'
    if isinstance(node, (ast.Dict, ast.DictComp)):
        return 'unpinned'            # 手搓的 dict：没有显式 pin 就是"继承宿主"缺口
    if isinstance(node, ast.Name):
        for scope in chain:
            assigns, pin_names = _scope_facts(scope, tree)
            if node.id in pin_names:
                return 'pinned'
            if node.id in assigns:
                before = [v for v in assigns[node.id]
                          if getattr(v, 'lineno', 0) <= call_lineno]
                val = (before or assigns[node.id])[-1]     # 最后一次赋值
                return _resolve_env(tree, chain, val, call_lineno, depth + 1)
        return 'unresolved'
    if isinstance(node, ast.Call):
        if getattr(node.func, 'id', None) == 'dict':
            return 'unpinned'        # `dict(os.environ)` / `dict(别的)`：没有 Agg
        if isinstance(node.func, ast.Name):
            v = _local_func_verdict(tree, node.func.id, depth + 1)
            if v:
                return v
    return 'unresolved'


def audit_child_calls(root):
    """每个 subprocess 调用点一条记录：{file, lineno, func, env, verdict}。"""
    rows = []
    for path in sorted(_iter_py_files(root)):
        rel = os.path.relpath(path, root).replace('\\', '/')
        text = _read_test_file(path)
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        aliases = _subprocess_aliases(tree)
        host_pinned = _module_pins_environ(tree)
        for call, qual in iter_subprocess_calls(tree, aliases):
            env_node = next((k.value for k in call.keywords if k.arg == 'env'), None)
            verdict = _resolve_env(tree, _scope_chain(tree, call), env_node,
                                   call.lineno)
            if verdict == 'inherited':
                verdict = 'inherited_host_pinned' if host_pinned else 'unpinned'
            seg = ast.get_source_segment(text, env_node) if env_node is not None \
                else None
            rows.append({
                'file': rel, 'lineno': call.lineno, 'func': qual,
                'env': (seg or '(缺省/None)').replace('\n', ' ')[:120],
                'verdict': verdict,
            })
    return rows


def _argv0_symbol(node):
    """argv 首元素的可读符号（``sys.executable`` / ``'taskkill'``）；非字面量 → None。"""
    if not isinstance(node, (ast.List, ast.Tuple)) or not node.elts:
        return None
    a0 = node.elts[0]
    if isinstance(a0, ast.Constant) and isinstance(a0.value, str):
        return a0.value
    try:
        return ast.unparse(a0)
    except Exception:                                            # noqa: BLE001
        return None


def audit_whitelisted_child_argv(root, whitelist=None):
    """``CHILD_ENV_WHITELIST`` 那些文件的**结构守卫**（白名单≠免检）。

    规则（见常量处注释）：系统工具放行；Python 解释器与非字面量 argv **必须显式传
    ``env=``**。返回 [(file, lineno, func, 原因)]。
    """
    whitelist = CHILD_ENV_WHITELIST if whitelist is None else whitelist
    names = set(whitelist)
    bad = []
    for path in sorted(_iter_py_files(root)):
        rel = os.path.relpath(path, root).replace('\\', '/')
        if os.path.basename(rel) not in names:
            continue
        text = _read_test_file(path)
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        for call, qual in iter_subprocess_calls(tree):
            argv = call.args[0] if call.args else None
            a0 = _argv0_symbol(argv)
            env_ok = any(k.arg == 'env' for k in call.keywords)
            if a0 is None:
                if not env_ok:
                    bad.append((rel, call.lineno, qual,
                                'argv 不是字面量、又没显式传 env= → 本该 pin'))
                continue
            if a0 in CHILD_ARGV0_PYTHONISH:
                if not env_ok:
                    bad.append((rel, call.lineno, qual,
                                '本处启动 Python 解释器却没显式传 env= → 本该 pin'))
                continue
            if a0 not in CHILD_ARGV0_TOOLS:
                bad.append((rel, call.lineno, qual,
                            'argv 首元素 %r 不在允许集合 %s'
                            % (a0, list(CHILD_ARGV0_TOOLS))))
    return bad


def _looks_like_matplotlib(node):
    if isinstance(node, ast.Name):
        return node.id in ('mpl', 'matplotlib')
    if isinstance(node, ast.Attribute):
        try:
            return 'matplotlib' in ast.unparse(node)
        except Exception:                                        # noqa: BLE001
            return False
    return False


def _gui_call_symbol(call):
    """这个调用是不是"创建真实 GUI 对象"（返回可读符号名，否则 None）。"""
    f = call.func
    if isinstance(f, ast.Attribute):
        try:
            text = ast.unparse(f)
        except Exception:                                        # noqa: BLE001
            text = f.attr
        if f.attr in GUI_CONSTRUCTORS or f.attr in GUI_LOOP_ATTRS \
                or f.attr in GUI_FOCUS_ATTRS:
            return text
        if f.attr == 'show':
            base = f.value
            if (isinstance(base, ast.Name) and base.id in GUI_SHOW_BASES) or \
                    (isinstance(base, ast.Attribute)
                     and base.attr in GUI_SHOW_BASES):
                return text
        if f.attr == 'use' and _looks_like_matplotlib(f.value) and call.args:
            a0 = call.args[0]
            if isinstance(a0, ast.Constant) and isinstance(a0.value, str) \
                    and a0.value.lower().startswith(GUI_BACKEND_PREFIXES):
                return '%s(%r)' % (text, a0.value)
        return None
    if isinstance(f, ast.Name) and f.id in GUI_CONSTRUCTORS:
        return f.id
    return None


def scan_gui_objects(root, whitelist=()):
    """扫"创建真实 GUI 对象"的**调用点**（注释/字符串里的字样天然不算）。"""
    bad = []
    for path in sorted(_iter_py_files(root)):
        rel = os.path.relpath(path, root).replace('\\', '/')
        if os.path.basename(rel) in whitelist:
            continue
        text = _read_test_file(path)
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                sym = _gui_call_symbol(node)
                if sym:
                    bad.append({'file': rel, 'lineno': node.lineno, 'symbol': sym})
    return bad


# --- 棘轮一：每个 subprocess 调用点都必须过"级联闸门" ------------------------
def test_every_child_process_call_site_is_pinned_or_ledgered():
    """AST **调用点级**棘轮：新调用点必须显式 env= 且含 MPLBACKEND=Agg。

    为什么按调用点而不按文件计数：见文件里那段"判据"注释（文本计数被
    `import subprocess` / `subprocess.DEVNULL` / 夹具里的调用字符串骗过，实测
    test_sandbox.py 是 12 文本命中 vs 3 调用点）。
    """
    rows = audit_child_calls(TESTS)
    assert rows, '一个 subprocess 调用点都没扫到 —— 扫描器坏了（不是好事）'
    bad = [r for r in rows
           if r['verdict'] not in ('pinned', 'inherited_host_pinned')
           and os.path.basename(r['file']) not in CHILD_ENV_WHITELIST]
    by_file = {}
    for r in bad:
        by_file.setdefault(r['file'], []).append(r)
    for f, rs in sorted(by_file.items()):
        detail = '\n'.join('  %s:%d %s  env=%s'
                           % (r['file'], r['lineno'], r['func'], r['env'])
                           for r in rs)
        assert f in UNPINNED_CALLSITES, (
            '%s 有 %d 处子进程调用点没过级联闸门（宿主 MPLBACKEND 会被原样继承 → '
            '子进程可能落到 Qt/Tk → 抢鼠标）：\n%s\n'
            '修法：env=_pin_child_env(dict(os.environ), ...)（见本文件顶部闸门；'
            'test_sandbox.py 的 3/3 是范式）。要么就把它登记进 UNPINNED_CALLSITES '
            '并写清"为什么不需要 pin"。' % (f, len(rs), detail))
        allowed, why = UNPINNED_CALLSITES[f]
        assert len(rs) <= allowed, (
            '%s 未过闸门的调用点从 %d 涨到 %d（棘轮只许降）：\n%s\n'
            '登记理由（只对已登记的那些有效）：%s' % (f, allowed, len(rs), detail, why))
    # 白名单（CHILD_ENV_WHITELIST）不是免检：结构守卫仍要过 —— 系统工具放行，
    # 启动 Python 解释器 / 非字面量 argv 必须显式传 env=。
    _wbad = audit_whitelisted_child_argv(TESTS, CHILD_ENV_WHITELIST)
    assert not _wbad, (
        '白名单文件里的子进程调用点没通过**结构守卫**（白名单只豁免"必须 pin Agg"\n'
        '这一条，不豁免"别偷偷起一个后端相关的子进程"）：\n' +
        '\n'.join('  %s:%d %s  %s' % (b[0], b[1], b[2], b[3]) for b in _wbad))


def test_child_whitelist_structural_guard_bites(tmp_path):
    """反向验证：白名单文件里加一个"本该 pin 的后端相关子进程" → 结构守卫报红。

    这是 t19 船长裁定 B 的配套：白名单豁免的是"必须 pin Agg"这一条，
    **不豁免**"别偷偷起一个后端相关的子进程"。
    """
    root = tmp_path / 'tests'
    root.mkdir()
    probe = root / 'test_real_window.py'          # 用白名单里的同名文件
    probe.write_text(
        'import subprocess\nimport sys\n\n'
        'def ok_tool():\n'
        "    return subprocess.run(['git', 'status'])\n\n\n"
        'def ok_py_with_env():\n'
        "    return subprocess.run([sys.executable, 'x.py'],\n"
        "                          env={'MPLBACKEND': 'Agg'})\n\n\n"
        'def ok_nonliteral_with_env(cmd, env):\n'
        '    return subprocess.Popen(cmd, env=env)\n\n\n'
        'def bad_py_no_env():\n'
        "    return subprocess.run([sys.executable, 'x.py'])\n\n\n"
        'def bad_unknown_exe():\n'
        "    return subprocess.run(['curl', 'http://x'])\n\n\n"
        'def bad_nonliteral_no_env(cmd):\n'
        '    return subprocess.Popen(cmd)\n',
        encoding='utf-8')
    bad = audit_whitelisted_child_argv(str(root), CHILD_ENV_WHITELIST)
    assert len(bad) == 3, bad                     # 三处违规、三处合法
    reasons = ' | '.join(b[3] for b in bad)
    assert '本该 pin' in reasons and '允许集合' in reasons, reasons
    # 真仓库里那份白名单文件本身必须过结构守卫
    # （当前 4 处：taskkill / _run_proc(cmd, env) / 2× git 基线探针）
    assert not audit_whitelisted_child_argv(TESTS, CHILD_ENV_WHITELIST)


def _text_hits(text):
    """**仅供参考**的文本命中数（正则）——判据是调用点级，见下面的对拍用例。"""
    return len(re.findall(r'subprocess\.(?:%s)' % '|'.join(_SUBPROC_FUNCS), text))


def test_call_site_audit_is_ast_not_text():
    """把"调用点级才是判据"钉成可执行断言：三类噪声都必须被 AST 过滤掉。"""
    sample = (
        'import subprocess\n'                                   # 噪声①：import 语句
        'PAYLOAD = "subprocess.run([%r, \'-c\', \'pass\'])"\n'   # 噪声②：被测文本
        'DEVNULL = subprocess.DEVNULL\n'                        # 噪声③：取属性
        'def go():\n'
        "    return subprocess.run(['x'], env={'MPLBACKEND': 'Agg'},\n"
        '                          stdin=DEVNULL)\n'
    )
    # 同一份代码的三种口径：粗粒度文本计数 4 / 正则命中 2 / AST 调用点 1
    assert sample.count('subprocess') == 4
    assert _text_hits(sample) == 2
    rows = iter_subprocess_calls(ast.parse(sample))
    assert len(rows) == 1 and rows[0][1] == 'subprocess.run', [q for _n, q in rows]
    # 真实文件对拍：test_sandbox.py 的粗粒度文本计数（12）远多于它的真实调用点（3），
    # **文件级命中数仅供参考，调用点级才是判据**。
    text = _read_test_file(os.path.join(TESTS, 'test_sandbox.py'))
    real = [r for r in audit_child_calls(TESTS) if r['file'] == 'test_sandbox.py']
    assert text.count('subprocess') > len(real) >= 1, (text.count('subprocess'),
                                                       len(real))
    assert all(r['verdict'] == 'pinned' for r in real), real


def test_pin_child_env_overrides_host_poison():
    """闸门契约：**硬钉**而不是 setdefault（宿主投毒必须被覆盖）。"""
    got = _pin_child_env({'MPLBACKEND': 'Qt5Agg', 'KEEP': '1'})
    assert got['MPLBACKEND'] == 'Agg'
    assert got['KEEP'] == '1'                    # 保留必要变量（不是整份丢掉）
    assert _pin_child_env()['MPLBACKEND'] == 'Agg'
    # 显式 overrides 优先（本文件唯一调用点要的是 PYTHONIOENCODING=gbk）
    assert _pin_child_env({}, PYTHONIOENCODING='gbk')['PYTHONIOENCODING'] == 'gbk'


def test_ratchet_reverse_probe_unpinned_child_call(tmp_path):
    """反向验证①：把一处真需要的 pin 拿掉 → 棘轮必须报红（且补回去必须复绿）。"""
    root = tmp_path / 'tests'
    root.mkdir()
    probe = root / 'test_probe_child.py'
    probe.write_text(
        'import os\nimport subprocess\n\n'
        'def go():\n'
        "    env = {k: v for k, v in os.environ.items()}\n"
        "    return subprocess.run(['python', '-c', 'pass'], env=env)\n",
        encoding='utf-8')
    rows = audit_child_calls(str(root))
    assert len(rows) == 1 and rows[0]['verdict'] == 'unpinned', rows
    # 补上闸门（本地同名 helper 与 test_sandbox._pin_child_env 同款）→ 必须复绿
    probe.write_text(
        'import os\nimport subprocess\n\n'
        'def _pin_child_env(env):\n'
        "    env['MPLBACKEND'] = 'Agg'\n"
        '    return env\n\n'
        'def go():\n'
        "    env = _pin_child_env({k: v for k, v in os.environ.items()})\n"
        "    return subprocess.run(['python', '-c', 'pass'], env=env)\n",
        encoding='utf-8')
    rows = audit_child_calls(str(root))
    assert len(rows) == 1 and rows[0]['verdict'] == 'pinned', rows


# --- 棘轮二：不许创建真实 GUI 对象（tkinter 建窗，MPLBACKEND 挡不住）--------
def test_no_real_gui_objects_in_tests():
    """扫 tests/ 下"创建真实 GUI 对象"的调用点；白名单只有 test_real_window.py。"""
    bad = scan_gui_objects(TESTS, whitelist=tuple(GUI_WHITELIST))
    assert not bad, (
        '这些地方会**真开窗**（tkinter/PyQt 直接建窗，MPLBACKEND=Agg 挡不住，'
        '会抢焦点与鼠标）：\n' +
        '\n'.join('  %s:%d  %s' % (b['file'], b['lineno'], b['symbol'])
                  for b in bad) +
        '\n修法：测试里不许建真实窗口对象；真窗口测试请放进 %s（唯一白名单，'
        '且按约定只在用户不在电脑前时跑）。' % ', '.join(GUI_WHITELIST))


def test_gui_ratchet_whitelist_is_still_needed():
    """白名单必须"还活着"：被豁免的文件确实含 GUI 对象，否则该删掉豁免。"""
    for name, why in sorted(GUI_WHITELIST.items()):
        path = os.path.join(TESTS, name)
        assert os.path.exists(path), '白名单里的 %s 不存在了（删掉豁免）' % name
        hits = scan_gui_objects(TESTS, whitelist=())
        assert any(b['file'] == name for b in hits), (
            '%s 里已经没有真实 GUI 对象了 → 它不再需要豁免（删掉 GUI_WHITELIST '
            '里的这条）。原理由：%s' % (name, why))


def test_ratchet_reverse_probe_real_gui_object(tmp_path):
    """反向验证②：在测试里加一行 `tk.Tk()` → 棘轮必须报红（白名单能豁免）。"""
    root = tmp_path / 'tests'
    root.mkdir()
    probe = root / 'test_probe_gui.py'
    probe.write_text(
        'import tkinter\n\n\ndef go():\n    return tkinter.Tk()\n',
        encoding='utf-8')
    bad = scan_gui_objects(str(root))
    assert len(bad) == 1 and bad[0]['symbol'] == 'tkinter.Tk', bad
    assert not scan_gui_objects(str(root), whitelist=('test_probe_gui.py',))
    # 字符串/注释里的同款字样不许误报（夹具里常作为"被测文本"出现）
    probe.write_text(
        'import tkinter  # noqa: F401\n\n'
        'PAYLOAD = "win = tkinter.Tk()"\n\n\n'
        'def go():\n    return PAYLOAD\n',
        encoding='utf-8')
    assert not scan_gui_objects(str(root)), scan_gui_objects(str(root))
