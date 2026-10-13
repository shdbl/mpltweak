# -*- coding: utf-8 -*-
"""可重跑预检（pre-flight，0.1.10）：**只读源码**，在下笔写回 / 开窗之前告诉
用户"这个脚本重跑会卡在哪"。

**为什么需要它**（用户视角评审指出的最大留存瓶颈）：本工具的安全网建立在
"在**你的环境里反复重跑脚本**"之上 —— 一次 ``apply --write`` 默认把脚本跑
1 遍（这一遍同时回答「还跑得通吗」与「改的字段真落到目标图上了吗」；取不到目标图状态时回退 2 遍，
失败归因最多 3 遍）。于是「要命令行参数 / 要联网 / 要人工输入 /
要弹窗 / 缺依赖」的脚本全线卡住，而用户事先**完全不知道**，撞上之后归因成
"这工具不行"。t11 管的是**失败之后**怎么说（四类归因），本模块管**失败之前**预警。

三条纪律（与 t7 语料筛选同一套）：

1. **绝不执行脚本**：全部判据都是 ``ast`` 静态读源码 + ``importlib.util.find_spec``
   查"这个包在不在"（find_spec 只查规格、**不执行模块体**，t7 已用副作用金丝雀验证）。
   与 ``verify``/``writeback`` 的"真跑"完全不同 —— 预检本身不产生任何副作用。
2. **默认静默**：没有风险就一个字都不说，不许变成新噪音。
3. **失败即放行**：预检自己出任何岔子（脚本读不了 / 语法错误 / find_spec 抛错）
   一律当"无风险"返回 —— 预检绝不能成为新的失败点。

风险标签与 t11 的失败归因标签**对齐**（见 ``ATTRIBUTION``），agent 可以用同一套
标签自动分支；``kind`` 是预检自己的稳定英文码，进 ``--json`` 的
``preflight.risks[].kind``。
"""
from __future__ import annotations

import ast
import os
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# 风险标签（稳定英文小写，机器可读；进 --json 的 preflight.risks[].kind）
# ---------------------------------------------------------------------------
RISK_MISSING_DEP = 'missing_dependency'   # 顶层 import 的包在本机 find_spec 不到
RISK_NEEDS_ARGS = 'needs_args'            # 读 sys.argv / 用 argparse
RISK_NETWORK = 'network'                  # requests / urllib / socket / http ...
RISK_STDIN = 'stdin'                      # input() / 读 sys.stdin / getpass
RISK_GUI = 'gui'                          # tkinter / PyQt / filedialog / plt.show

#: 每个风险对应 t11 的失败归因标签（verify.VERIFY_FAIL_*）——
#: agent 用同一套标签就能把"事前预警"和"事后归因"串起来。取值与 verify.py 的常量
#: **同值**；这里不 import verify，是为了让本模块保持"纯静态、零重依赖"（launch 也
#: 要 import 它）。等值关系由回归测试 tests/test_agent_api.py §22 钉住。
ATTRIBUTION = {
    RISK_MISSING_DEP: 'script_cannot_run',
    RISK_NEEDS_ARGS: 'script_needs_args',
    RISK_NETWORK: 'script_cannot_run',
    RISK_STDIN: 'script_cannot_run',
    RISK_GUI: 'script_needs_gui_or_headless_impossible',
}

#: 人读文案的 messages.py 键（中英双语）。缺 key 时 messages.t 原样返回 key，
#: 不会崩 —— 但回归测试会断言这些 key 都真实存在。
MSG_KEY = {
    RISK_MISSING_DEP: 'preflight_missing_dependency',
    RISK_NEEDS_ARGS: 'preflight_needs_args',
    RISK_NETWORK: 'preflight_network',
    RISK_STDIN: 'preflight_stdin',
    RISK_GUI: 'preflight_gui',
}

#: 机器可读的英文短句（--json 的 preflight.risks[].hint；永远英文，见 messages 的
#: 两层设计：机器可读通道不随系统语言变）。
_HINT_EN = {
    RISK_MISSING_DEP: 'packages imported by this script are missing here; '
                      'rerun verification will fail',
    RISK_NEEDS_ARGS: 'this script reads command-line arguments; pass them after '
                      '`--` so the rerun sees the same argv',
    RISK_NETWORK: 'this script uses the network; headless rerun may fail or get '
                  'slow',
    RISK_STDIN: 'this script reads stdin / calls input(); rerun has nobody to '
                'type and will hang until timeout',
    RISK_GUI: 'this script opens a window / interactive display; headless rerun '
              'cannot succeed (and may really pop up a window)',
}

#: 联网足迹（顶层包名，小写）。命中即提示"会联网"。
NETWORK_MODULES = frozenset((
    'requests', 'urllib', 'urllib2', 'urllib3', 'httpx', 'aiohttp', 'socket',
    'http', 'ftplib', 'smtplib', 'telnetlib', 'xmlrpc', 'websockets',
    'websocket', 'paramiko', 'boto3', 'pycurl', 'ssl',
))

#: GUI / 弹窗足迹（顶层包名，**全小写**；比对时统一 ``.lower()``，这样
#: ``import Tkinter`` / ``import PyQt5`` / ``from PySide6 import ...`` 都命中）。
GUI_MODULES = frozenset((
    'tkinter', '_tkinter', 'tk', 'pyqt5', 'pyqt6', 'pyqt4',
    'pyside', 'pyside2', 'pyside6', 'wx', 'gi', 'gtk', 'kivy', 'pyglet',
    'webbrowser',
))

#: 「交互/弹窗」调用点（无差别判定，任何对象上的这些方法都算；与 verify.py 的
#: 保守口径一致）。
GUI_CALL_ATTRS = frozenset((
    'ion', 'pause', 'interactive', 'waitforbuttonpress', 'ginput',
    'get_current_fig_manager', 'mainloop', 'filedialog', 'messagebox',
    'askopenfilename', 'asksaveasfilename', 'Tk', 'Toplevel',
))

#: ``plt.show()`` / ``fig.show()`` 这类"显示窗口"调用：只认 matplotlib 家族的
#: 接收者（``widget.show()`` 之类的自定义方法不误报）。
_MPL_SHOW_BASE = frozenset(('plt', 'pyplot', 'fig', 'figure', 'mgr'))


# ---------------------------------------------------------------------------
# 读源码 / 解析（读不了、解析不了 → 返回 None，调用方当"无风险"）
# ---------------------------------------------------------------------------
def _read_source(path: str) -> Optional[str]:
    """按 utf-8 / gbk / gb18030 依次试读（与 verify.py 同一口径）。

    用户的科研脚本在中文 Windows 上常是 GBK —— 读不出来就当没风险，绝不抛。
    """
    for enc in ('utf-8', 'gbk', 'gb18030'):
        try:
            with open(path, 'r', encoding=enc) as f:
                return f.read()
        except (OSError, UnicodeDecodeError, LookupError, ValueError):
            continue
    return None


def _parse(src: str) -> Optional[ast.AST]:
    try:
        return ast.parse(src)
    except (SyntaxError, ValueError, MemoryError, RecursionError):
        return None


def _parents(tree: ast.AST) -> Dict[ast.AST, ast.AST]:
    """父节点表（用来判"这个 import 是不是在 try 块里"）。"""
    par: Dict[ast.AST, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            par[child] = node
    return par


def _in_try(node: ast.AST, par: Dict[ast.AST, ast.AST]) -> bool:
    """node 是否位于某个 try 块内。

    ``try: import x`` / ``except ImportError: x = None`` 是**合法且常见**的写法
    （可选依赖）—— 这种 import 找不到包不算风险，否则预检会天天误报。
    """
    cur = par.get(node)
    while cur is not None:
        if isinstance(cur, ast.Try):
            return True
        cur = par.get(cur)
    return False


def _top_of(dotted: Optional[str]) -> str:
    return (dotted or '').split('.')[0].strip()


def _module_level_imports(tree: ast.AST, par: Dict[ast.AST, ast.AST]) -> set:
    """**模块级**（非函数/类体内、非 try 块内）import 的顶层包名集合。

    与 t7 的 ``module_imports`` 同一口径：函数体里的延迟 import 不算"缺依赖"
    （很多脚本把重依赖放在函数里按需 import）。
    """
    tops = set()

    def walk(node):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef,
                                  ast.ClassDef, ast.Lambda)):
                continue                     # 函数/类体内的 import 不算顶层
            if isinstance(child, ast.Import):
                if not _in_try(child, par):
                    for a in child.names:
                        if a.name:
                            tops.add(_top_of(a.name))
            elif isinstance(child, ast.ImportFrom):
                if (child.module and not child.module.startswith('.')
                        and not _in_try(child, par)):
                    tops.add(_top_of(child.module))
            walk(child)

    walk(tree)
    tops.discard('')
    return tops


def _all_imports(tree: ast.AST) -> set:
    """**所有** import 的顶层包名（含函数体内 / try 内）。

    联网与 GUI 用这个口径：`try: import tkinter` 依然是"会弹窗"的强信号 —— 缺依赖
    才需要保守（有 try 兜底就不算风险），"会不会联网/弹窗"不需要保守。
    """
    tops = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name:
                    tops.add(_top_of(a.name))
        elif isinstance(node, ast.ImportFrom):
            if node.module and not node.module.startswith('.'):
                tops.add(_top_of(node.module))
    tops.discard('')
    return tops


def _find_spec_ok(name: str) -> bool:
    """``find_spec(顶层包名)``：只查规格、**不执行模块体**（t7 金丝雀已验证）。

    异常 / None 一律当"缺失"。注意只用**顶层**包名（``a.b`` 只查 ``a``）：
    find_spec 对顶层名不会去执行任何模块，副作用为零。
    """
    if not name or name.startswith('.'):
        return True
    try:
        from importlib.util import find_spec
        return find_spec(name) is not None
    except (ImportError, ModuleNotFoundError, AttributeError, ValueError,
            TypeError, SyntaxError):
        return False


def _local_module(script_dir: str, name: str) -> bool:
    """同目录（或当前目录）的**本地**模块/包不算缺依赖。

    ``import my_utils``（旁边就是 my_utils.py）在 `python fig.py` 下完全能跑通 ——
    不查这一步就会出现最讨人厌的一类误报。
    """
    for base in (script_dir, os.getcwd()):
        if not base:
            continue
        try:
            if os.path.isfile(os.path.join(base, name + '.py')):
                return True
            if (os.path.isdir(os.path.join(base, name))
                    and os.path.isfile(os.path.join(base, name, '__init__.py'))):
                return True
        except OSError:
            continue
    return False


# ---------------------------------------------------------------------------
# 逐类判据（全部静态）
# ---------------------------------------------------------------------------
def _is_sys_argv(node: ast.AST) -> bool:
    return (isinstance(node, ast.Attribute) and node.attr == 'argv'
            and isinstance(node.value, ast.Name) and node.value.id == 'sys')


def _args_evidence(tree: ast.AST) -> Optional[Dict[str, Any]]:
    """② 需要参数：读 ``sys.argv``（或 ``from sys import argv``）/ 用 argparse。

    返回 ``None``（没证据）或 ``{'strong': bool, 'evidence': [...]}``。
    ``strong`` = **无条件需要**参数（``sys.argv[N]`` N>=1 / ``required=True``）——
    只有强证据才足以断言"你就是缺参数"，弱证据（例如只 ``len(sys.argv)`` 判断）
    在文案里口径更软。
    """
    has_argv_name = False
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and _top_of(node.module) == 'sys':
            if any((a.name or '') == 'argv' for a in node.names):
                has_argv_name = True

    ev: List[str] = []
    strong = False

    def _mark(what, is_strong=False):
        nonlocal strong
        if what not in ev:
            ev.append(what)
        if is_strong:
            strong = True

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            attr = getattr(fn, 'attr', '')
            if attr == 'ArgumentParser':
                _mark('argparse.ArgumentParser')
            elif attr in ('parse_args', 'parse_known_args'):
                _mark('argparse.%s' % attr)
            elif attr == 'add_argument':
                _mark('argparse.add_argument')
                for kw in node.keywords:
                    if (kw.arg == 'required'
                            and isinstance(kw.value, ast.Constant)
                            and kw.value.value is True):
                        _mark('add_argument(required=True)', True)
            elif isinstance(fn, ast.Name) and fn.id == 'len' and node.args:
                if _is_sys_argv(node.args[0]):
                    _mark('len(sys.argv)')
            # 裸 `argv`（from sys import argv）
            if has_argv_name and isinstance(fn, ast.Name) and fn.id == 'argv':
                _mark('argv')
        if isinstance(node, ast.Subscript):
            if _is_sys_argv(node.value):
                sl = node.slice
                if (isinstance(sl, ast.Constant) and isinstance(sl.value, int)
                        and sl.value >= 1):
                    _mark('sys.argv[N]', True)       # 直接取值 = 无条件需要参数
                else:
                    _mark('sys.argv[...]')
            elif (has_argv_name and isinstance(node.value, ast.Name)
                    and node.value.id == 'argv'):
                _mark('argv[...]')
    if not ev:
        return None
    return {'strong': strong, 'evidence': ev}


def _is_mpl_show(fn: ast.AST) -> bool:
    """``plt.show()`` / ``pyplot.show()`` / ``fig.show()``（不误伤 widget.show()）。"""
    if not (isinstance(fn, ast.Attribute) and fn.attr == 'show'):
        return False
    base = fn.value
    if isinstance(base, ast.Name):
        return base.id in _MPL_SHOW_BASE
    if isinstance(base, ast.Attribute):
        return base.attr in _MPL_SHOW_BASE
    return False


def _gui_calls(tree: ast.AST) -> List[str]:
    """⑤ GUI/弹窗的**调用点**证据（import 由 ``_all_imports`` 另判）。"""
    ev: List[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        attr = getattr(fn, 'attr', '')
        if _is_mpl_show(fn):
            ev.append('plt.show()')
        elif attr in GUI_CALL_ATTRS:
            ev.append('%s()' % attr)
        elif attr == 'use':
            # matplotlib.use('TkAgg' / 'Qt5Agg' / 'WebAgg' ...)：显式要交互后端
            if (isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name)
                    and fn.value.id == 'matplotlib' and node.args
                    and isinstance(node.args[0], ast.Constant)):
                arg = str(node.args[0].value).lower()
                if any(k in arg for k in ('qt', 'tk', 'webagg', 'nbagg',
                                          'widget', 'notebook')):
                    ev.append("matplotlib.use('%s')" % arg)
    # 去重但保持顺序
    out = []
    for e in ev:
        if e not in out:
            out.append(e)
    return out


def _stdin_calls(tree: ast.AST) -> List[str]:
    """④ 人工输入：``input()`` / 读 ``sys.stdin`` / ``getpass``。"""
    ev: List[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            if isinstance(fn, ast.Name) and fn.id in ('input', 'raw_input'):
                ev.append('%s()' % fn.id)
            elif getattr(fn, 'attr', '') == 'getpass':
                ev.append('getpass()')
        elif isinstance(node, ast.Attribute) and node.attr == 'stdin':
            if isinstance(node.value, ast.Name) and node.value.id == 'sys':
                ev.append('sys.stdin')
    out = []
    for e in ev:
        if e not in out:
            out.append(e)
    return out


# ---------------------------------------------------------------------------
# 对外的两个函数
# ---------------------------------------------------------------------------
def _next_step(script: str, kind: str) -> str:
    """机器可读的下一步命令（**ASCII 英文**，可直接执行）。

    与 apply.py 的 ``_next_step_cmd`` 同一风格：给 agent 一条能照抄的命令。
    """
    if kind == RISK_NEEDS_ARGS:
        return 'mpltweak apply %s --write -- <your args...>' % script
    return 'mpltweak apply %s --write --verify-fast' % script


def _risk(kind: str, script: str, detail: str,
          evidence: Optional[List[str]] = None,
          packages: Optional[List[str]] = None) -> Dict[str, Any]:
    return {
        'kind': kind,
        'attribution': ATTRIBUTION[kind],
        'detail': detail,
        # 静态证据（源码里的 import / 调用点字样），给人看也方便 agent 复核
        'evidence': list(evidence or []),
        'next_step': _next_step(script, kind),
        'hint': _HINT_EN[kind],
        'packages': list(packages or []),
    }


def _analyze(script: str, args_passed: bool) -> List[Dict[str, Any]]:
    """真正的判据（可能抛异常，由 analyze 兜住 → 无风险）。"""
    src = _read_source(script)
    if src is None:
        return []
    tree = _parse(src)
    if tree is None:
        return []          # 语法都解析不了 → 不猜（apply 自己的语法层会如实报）
    par = _parents(tree)
    risks: List[Dict[str, Any]] = []

    # ① 缺依赖：**模块级** import 的顶层包 find_spec 不到（且不是同目录本地模块）
    script_dir = os.path.dirname(os.path.abspath(script))
    missing = [n for n in sorted(_module_level_imports(tree, par))
               if not _find_spec_ok(n) and not _local_module(script_dir, n)]
    if missing:
        risks.append(_risk(RISK_MISSING_DEP, script, 'missing_packages',
                           evidence=missing, packages=missing))

    # ② 需要参数：`--` 后面已经给了参数就不再提示（用户已经照做了，
    #    再叫他"去传参数"是误导 —— 与 t11 的 configured_args 同一口径）
    if not args_passed:
        ev = _args_evidence(tree)
        if ev:
            risks.append(_risk(RISK_NEEDS_ARGS, script,
                               'static_args_strong' if ev['strong']
                               else 'static_args',
                               evidence=ev['evidence']))

    # ③ 联网（比对统一小写：`import urllib.request` / `import SocketServer` 都算）
    net = sorted(n for n in _all_imports(tree) if n.lower() in NETWORK_MODULES)
    if net:
        risks.append(_risk(RISK_NETWORK, script, 'static_network',
                           evidence=net))

    # ④ 人工输入（重跑没人输入 → 会一直等到超时）
    stdin_ev = _stdin_calls(tree)
    if stdin_ev:
        risks.append(_risk(RISK_STDIN, script, 'static_stdin',
                           evidence=stdin_ev))

    # ⑤ GUI/弹窗（无头重跑跑不通；在**有**桌面的机器上还可能真的抢窗口）
    gui_imports = sorted(n for n in _all_imports(tree)
                         if n.lower() in GUI_MODULES)
    gui_calls = _gui_calls(tree)
    if gui_imports or gui_calls:
        risks.append(_risk(RISK_GUI, script, 'static_gui',
                           evidence=gui_imports + gui_calls))

    # 风险按固定顺序输出（缺依赖 → 参数 → 联网 → 输入 → GUI），确保可断言、
    # 可 diff；同类不重复。
    order = {RISK_MISSING_DEP: 0, RISK_NEEDS_ARGS: 1, RISK_NETWORK: 2,
             RISK_STDIN: 3, RISK_GUI: 4}
    risks.sort(key=lambda r: order.get(r['kind'], 9))
    return risks


def analyze(script_path: str, args_passed: bool = False) -> Dict[str, Any]:
    """静态预检一个脚本，返回 **JSON-ready** 结果::

        {'ok': bool, 'risks': [{'kind', 'attribution', 'detail', 'evidence',
                                'next_step', 'hint', 'packages'}, ...]}

    ``args_passed``：本次调用是否已经把用户脚本参数透传（``apply --write -- ...``
    或 launch 的透传参数）—— 是的话不再提示"需要参数"。

    **绝不执行脚本**；**任何异常都当"无风险"**（失败即放行）——预检不是新的失败点。
    """
    out: Dict[str, Any] = {'ok': True, 'risks': []}
    try:
        path = os.path.abspath(script_path)
        risks = _analyze(path, bool(args_passed))
    except Exception:                     # noqa: BLE001 - 预检绝不让 apply/开窗失败
        return out
    out['risks'] = risks
    out['ok'] = not risks
    return out


def message_lines(res: Optional[Dict[str, Any]], script: str = '') -> List[str]:
    """把预检结果变成**人读**的中英双语提示（无风险 → 空列表 = 默认静默）。

    文案一律走 messages.py（``preflight_*`` 键），与 t11 的归因文案同一风格：
    每条风险后面都给一句**可照抄**的下一步。
    """
    from . import messages
    risks = (res or {}).get('risks') or []
    if not risks:
        return []
    _script = os.path.abspath(script) if script else '<script>'
    lines = [messages.t('preflight_head')]
    for r in risks:
        key = MSG_KEY.get(r.get('kind'))
        if not key:
            continue
        lines.append(messages.t(key, script=_script,
                                packages=', '.join(r.get('packages') or []),
                                evidence=', '.join(r.get('evidence') or [])))
    lines.append(messages.t('preflight_tail'))
    return lines
