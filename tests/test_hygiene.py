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

# 基线：以 `python tools/audit_exceptions.py` 的输出为准（2026-10 实测；下表合计
# 158 处 —— 其中 writeback.py 比 0.1.8 的 5 处多 4 处，见下面的登记理由）。
# 只允许降。要涨，必须在本表里改数字，并在 PR 里说明为什么无法窄化到具体异常。
# 已登记的例外：
#   * verify.py 15 → 19（2026-10-10）：新增 `_collect_texts` / `_ax_identity` 的
#     "尽力采集"兜底 —— 它们采的是**附加**信息（文字外框 / 轴身份），
#     采不到就该退化成空，绝不能因为某个 artist 取不到外框就让整个 check 失败。
#     每个点都写在 docstring / 行内注释里说明了这一点。
BASELINE = {
    'toolbox.py': 105,
    'launch.py': 16,
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
    env['PYTHONIOENCODING'] = 'gbk'          # 关键：逼真 GBK 控制台
    env['PYTHONPATH'] = os.path.join(ROOT, 'src')
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
