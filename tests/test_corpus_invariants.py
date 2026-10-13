# -*- coding: utf-8 -*-
"""语料级不变量扫描（⑥）—— 复用 t2（test_property_writeback）的四条铁律断言函数，
对真实语料子集（corpus_union，只读）逐份跑不变量扫描。

模块级脚本，风格同七套件 + test_property_writeback.py：
``MPLTWEAK_LANG=zh`` 钉死 + 末尾打印 ALL PASS / FAILED 并 ``sys.exit``。

四条铁律（来自 t2，判据实现一致，仅把"④ 只动目标"改成无模板的语料版）：
  ① 幂等（idempotent）：apply --write 后脚本字节不变；再 apply = no_change。
  ② 可逆（reversible）：revert --write 逐字节还原写回前的原文。
  ③ 语法守恒（syntax）：写回后 ast.parse 必须通过 —— **任何**结果都成立。
  ④ 只动目标（target-only）：声称落地的字段值必须以数值字面量出现在源码；
     未写回（字节未变）时必须一个字节都不动（承诺违约 = 声称写回但字节未变）。

语料口径（对齐 _numbers_e28a608.md + 0.1.10 运行时验证落地）：
  * 语料目录只读，**绝不写入**；每次扫描只把目标脚本**拷贝**到隔离 workdir 再跑。
  * **默认 = 真跑档（完整验证，0.1.10 主口径）**：apply --write 默认档，verify
    重跑脚本做语义比对 —— 这正是 0.1.9 拍板"推迟到这一版"的运行时验证证据。
    真跑档配**防弹窗守卫**：worker 先**静态**扫源码，命中
    ``matplotlib.use('TkAgg')``/``tkinter``/``filedialog``/``PyQt``/``plt.show()``
    即判"判据不适用"（na_skip），绝不真跑（0.1.9 教训：MPLBACKEND=Agg 挡不住
    脚本自己开 GUI/文件框）。
  * ``--fast`` = 静态档（补充视图，非替代）：apply --verify-fast，只静态写回 +
    ast.parse，不执行语料脚本。真跑档才是完整验证。
  * 语料里大量脚本是 plt.subplots 网格 / 依赖本地数据 —— apply 会退块或拒绝，
    属"判据不适用"，不是违约。只有四律被违反才算"真违约"（normal violation）。
  * 「幂等/可逆」只对"成功写回"的那批（reason ∈ ok/best_effort/no_change）断言；
    「语法守恒/只动目标」对**任何**结果成立（含拒写批：拒写必须字节不动）。

子集抽样（覆盖不同来源）：
  * 从 manifest.jsonl（10,158 份 sha256 唯一）按 `plot==true` 分层抽样：
    zenodo / github-* / gallery(matplotlib-gallery + gallery-*) 三来源，确定性种子。
  * 默认 240 份（>=200 验收），每份一个**独立子进程 worker**（脚本跑在子进程里，
    超时即杀，语料脚本的任意行为都污染不了扫描进程），--jobs 路并发。

用法：
    python tests/test_corpus_invariants.py                # 默认 240 份 / 4 并发，真跑档
    python tests/test_corpus_invariants.py --fast         # 静态档（不执行语料脚本）
    python tests/test_corpus_invariants.py --limit 20     # 冒烟小样
    python tests/test_corpus_invariants.py --jobs 1       # 串行调试
    MPLTWEAK_CORPUS=... MPLTWEAK_SCAN_OUT=... python tests/test_corpus_invariants.py

产出（OUTDIR，默认 <workspace>/_review/corpus_invariants_t4/）：
    results.jsonl  —— 逐份结果（可复现）
    SUMMARY.md     —— 摘要交付物（通过数/违约数/违约样例/判据不适用明细）
"""

import argparse
import ast
import contextlib
import difflib
import hashlib
import io
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time

# ---- 输出兜底（T13 纪律）：GBK 控制台 / 管道下 print 中文不崩 ----
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(errors='replace')
    except (AttributeError, ValueError, OSError):
        pass


def _gbk_safe(s):
    s = str(s)
    enc = getattr(sys.stdout, 'encoding', None) or 'utf-8'
    try:
        return s.encode(enc, 'replace').decode(enc, 'replace')
    except (LookupError, UnicodeDecodeError):
        return s.encode('ascii', 'backslashreplace').decode('ascii')


# ---- 语言 / 后端钉死：别让宿主 locale 决定结论 ----
os.environ.setdefault('MPLTWEAK_LANG', 'zh')
os.environ.setdefault('MPLBACKEND', 'Agg')

fail = 0


def check(name, cond, detail=''):
    global fail
    print(('PASS' if cond else 'FAIL') + ' | ' + _gbk_safe(name)
          + ((' | ' + _gbk_safe(detail)) if detail else ''))
    if not cond:
        fail += 1


# ---- 沙箱坑：%TEMP% 写入被拦 → 把 TEMP/TMP 指到可写目录（worker 的 verify 子进程继承）----
_TMPROOT = None


def _ensure_tmp_writable():
    global _TMPROOT
    base = tempfile.gettempdir()
    root = os.path.join(base, '.mpltweak_corpus_%d' % os.getpid())
    try:
        os.makedirs(root, exist_ok=True)
        probe = os.path.join(root, '.w')
        open(probe, 'w').close()
        os.unlink(probe)
    except OSError:
        root = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            '.tmp_corpus_%d' % os.getpid())
        os.makedirs(root, exist_ok=True)
    _TMPROOT = root
    os.environ['TEMP'] = root
    os.environ['TMP'] = root
    tempfile.tempdir = root
    return root


_ensure_tmp_writable()


# ---------------------------------------------------------------------------
# 复用自 tests/test_property_writeback.py（t2）的纯函数 —— 判据实现必须与 t2 一致，
# 这里**不** import 该文件（它是模块级脚本，import 会直接跑 main + sys.exit）。
# 如需单点更新（如判据语义变化），必须同步改这两处。
# ---------------------------------------------------------------------------
def _read_bytes(path):
    with open(path, 'rb') as f:
        return f.read()


def _read_text_any(path):
    """按引擎同一套编码探测读文本（GBK/UTF-8-BOM/…都行），保留 \\r\\n。"""
    from mpltweak import params
    return params.read_text_ex(path)[0]


def _check_syntax_text(text):
    """③ 语法守恒：ast.parse 必须通过。"""
    try:
        ast.parse(text)
        return True
    except (SyntaxError, ValueError):
        return False


def _check_idempotent(bytes_after_first, bytes_after_second, reason2):
    """① 幂等：第二次 apply 后字节不变，且 reason 是 no_change。"""
    return bytes_after_first == bytes_after_second and reason2 == 'no_change'


def _check_reversible(original_bytes, current_bytes):
    """② 可逆：revert --write 之后当前字节 == 写回前的原文。"""
    return original_bytes == current_bytes


def _numeric_literals(text):
    vals = []
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return vals
    for n in ast.walk(tree):
        if (isinstance(n, ast.Constant)
                and isinstance(n.value, (int, float))
                and not isinstance(n.value, bool)):
            vals.append(float(n.value))
        elif isinstance(n, ast.UnaryOp) \
                and isinstance(n.op, (ast.USub, ast.UAdd)) \
                and isinstance(n.operand, ast.Constant) \
                and isinstance(n.operand.value, (int, float)) \
                and not isinstance(n.operand.value, bool):
            v = float(n.operand.value)
            vals.append(-v if isinstance(n.op, ast.USub) else v)
    return vals


def _values_present(text, values, tol=1e-6):
    nums = _numeric_literals(text)
    for v in values:
        if not any(abs(float(v) - x) <= tol for x in nums):
            return False
    return True


def _need_block_fallback(res):
    if res.get('reason') == 'no_change':
        return False
    if res.get('reason') == 'fail':
        return True
    return bool(res.get('needs_block'))


def _apply_auto(script, data, pp, verify=True, timeout=60):
    """复刻 apply.py 的 auto 语义（先原位、落不了再退块；退块复用备份），
    与 t2 的 _apply_auto 一致，只是给 writeback 传了 timeout（语料脚本可能慢）。
    注意：writeback 返回的是**原始 dict**，不是 apply CLI 的最终报告（apply.main
    会做 enrichment：figsize 原位落地 + 轴字段 pending 时，raw dict 报 applied=False
    而 CLI 会如实补报 applied=true + changes=[figsize]）。本扫描的**判定一律走
    CLI --json**（_cli_apply），原始 dict 只用于对照。"""
    from mpltweak import writeback
    res = writeback.writeback(script, data, pp, verify=verify, style='inplace',
                              timeout=timeout)
    if _need_block_fallback(res):
        bk = res.get('backup')
        res = writeback.writeback(script, data, pp, verify=verify,
                                  style='block', timeout=timeout, backup=bk)
    return res


def _decode_out(raw):
    for enc in ('utf-8', 'gbk', 'gb18030'):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode('utf-8', 'replace')


def _extract_json(stdout):
    """从 apply --json 的 stdout 里抽出**单文件**结果对象。
    CLI 输出结构是 ``{ok, files:[{reason, applied, changes, pending, ...}], ...}``
    （多文件命令时 files 有多个；本扫描一次只 apply 一个脚本 → 取 files[0]）。
    容错：跳过前导杂讯；解析不出 files 数组时退回顶层对象本身。"""
    s = stdout.strip()
    obj = None
    if s.startswith('{'):
        try:
            obj = json.loads(s)
        except ValueError:
            pass
    if obj is None:
        i = s.find('{')
        if i >= 0:
            try:
                obj = json.loads(s[i:])
            except ValueError:
                return None
    if isinstance(obj, dict) and obj.get('files'):
        return obj['files'][0]
    return obj


def _cli_env(cwd):
    """构造 apply CLI 子进程环境。

    **沙箱注入（t9）**：当环境里存在 ``MPLTWEAK_SANDBOX_ROOT`` 时，把沙箱目录
    （tests/sandbox，含 sitecustomize.py）**并进** PYTHONPATH（**不能覆盖** <树>/src
    —— mpltweak 还要 import），并指好游乐场内的 MPLCONFIGDIR / TMP / TEMP /
    PYTHONDONTWRITEBYTECODE。这样 apply → verify → 脚本 整条子进程链都继承守卫。
    未设 MPLTWEAK_SANDBOX_ROOT → 保持原行为（不注入）。
    """
    env = dict(os.environ,
               MPLBACKEND='Agg', MPLTWEAK_LANG='zh', PYTHONIOENCODING='utf-8')
    sb_root = os.environ.get('MPLTWEAK_SANDBOX_ROOT')
    pp_parts = []
    if sb_root:
        sb_dir = os.path.join(ROOT, 'tests', 'sandbox')
        if os.path.isdir(sb_dir):
            pp_parts.append(sb_dir)
        sb_root = os.path.abspath(sb_root)
        env['MPLTWEAK_SANDBOX_ROOT'] = sb_root
        env['MPLCONFIGDIR'] = os.path.join(sb_root, '.mplconfig')
        env.setdefault('PYTHONDONTWRITEBYTECODE', '1')
        _ptmp = os.path.join(sb_root, 'tmp')
        env['TMP'] = env['TEMP'] = env['TMPDIR'] = _ptmp
        # 守卫的**开与关必须由本测试说了算**（t15/t17 的教训：宿主残留的
        # MPLTWEAK_SANDBOX_NOGUI=1 会把"禁 GUI"整层悄悄关掉 → 等于没防护）。
        # 同时强制打开无人值守层：否则 input() 会一直等到 worker 超时，
        # 既浪费配额，na_stdin 这一类也数不出来。
        env.pop('MPLTWEAK_SANDBOX_NOGUI', None)
        env['MPLTWEAK_SANDBOX_NO_STDIN'] = '1'
    pp_parts.append(os.path.join(ROOT, 'src'))
    env['PYTHONPATH'] = os.pathsep.join(pp_parts)
    return env


def _cli_apply(script, pp, cwd, fast=False):
    """跑真实 CLI `apply <脚本> --params <pp> --write --json`。

    fast=False（**默认，真跑档**）：完整验证档（verify 重跑脚本做语义比对）——
    这是 0.1.10 的主口径（0.1.9 拍板把运行时验证落地到这一版）。真跑档必须配合
    worker 里的**防弹窗守卫**（_POPUP_RISK 静态扫描，命中即判据不适用，绝不真跑）。
    沙箱（MPLTWEAK_SANDBOX_ROOT 已设）时 verify 重跑在审计钩子守卫内执行。

    fast=True（静态档）：``--verify-fast`` —— 只静态写回 + ast.parse，**不执行
    语料脚本**。作为补充视图，不是替代（真跑档才是完整验证）。

    返回 (json_obj_or_None, returncode, combined_output)。"""
    env = _cli_env(cwd)
    cmd = [sys.executable, '-m', 'mpltweak', 'apply', script,
           '--params', pp, '--write', '--json']
    if fast:
        cmd.append('--verify-fast')
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=60, env=env,
                           cwd=cwd)
    except subprocess.TimeoutExpired:
        return None, -9, 'apply CLI 超时'
    out_stdout = r.stdout
    out = _decode_out(r.stdout + r.stderr)
    j = _extract_json(_decode_out(out_stdout))
    return j, r.returncode, out


# ---- 联网 / 起子进程的静态预筛（t23 试点实测后加的）----
# 试点实测发现：**真跑链路里 socket/subprocess 那一层没有生效**。根因是沙箱判断
# "这是不是在跑用户脚本"看的是 `argv[0]` 是不是游乐场内的真实文件，而 verify 子进程
# 的 argv[0] 是 mpltweak 模块路径（`python -m mpltweak.verify ...`）→ 被当成 harness，
# 只剩文件那一层（文件层对 harness 也生效，所以**越界读写依然是拦住的**，实测见试点证据）。
# 按 0.1.9 的教训：指望不上的层就别让它有机会发生 —— 这类脚本静态预筛、不真跑。
_NET_MODULES = ('socket', 'requests', 'urllib', 'urllib2', 'urllib3', 'httpx',
                'aiohttp', 'http', 'ftplib', 'smtplib', 'telnetlib', 'paramiko',
                'boto3', 'pycurl', 'subprocess')
_NET_OS_CALLS = ('system', 'popen', 'execv', 'execvp', 'execve', 'spawnv',
                 'spawnvp', 'spawnl', 'startfile')
_NET_SUBPROC_CALLS = ('run', 'Popen', 'call', 'check_output', 'check_call')


def _has_net_risk(text):
    """AST 静态：脚本是否**联网 / 起子进程**（不用正则扫源码 —— 那样会把注释和
    字符串里的词也算进去；t17/t20 的棘轮已经栽过一次"词元计数 ≠ 调用点"）。"""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return False
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if (a.name or '').split('.')[0] in _NET_MODULES:
                    return True
        elif isinstance(node, ast.ImportFrom):
            if (node.module or '').split('.')[0] in _NET_MODULES:
                return True
        elif isinstance(node, ast.Call):
            fn = node.func
            if isinstance(fn, ast.Name):
                if fn.id in ('system', 'popen'):
                    return True
                continue
            if not isinstance(fn, ast.Attribute):
                continue
            attr = fn.attr
            base = fn.value
            if attr in ('urlopen', 'urlretrieve'):
                return True
            if isinstance(base, ast.Name):
                if base.id == 'os' and attr in _NET_OS_CALLS:
                    return True
                if base.id == 'subprocess' and attr in _NET_SUBPROC_CALLS:
                    return True
    return False


# ---- 防弹窗守卫（0.1.9 教训：MPLBACKEND=Agg 挡不住脚本自己开 GUI/文件框）----
# 真跑档（verify 重跑）执行语料脚本前，**静态**扫描源码，命中任一危险调用即把
# 该脚本判为"判据不适用"（na_skip），绝不真跑。正则只匹配字面量调用，不做执行。
_POPUP_PATTERNS = [
    r"matplotlib\s*\.\s*use\s*\(\s*['\"](?:Tk|Qt|GTK|wx|MacOSX|WebAgg)",
    r"tkinter",
    r"filedialog",
    r"PyQt|PySide",
    r"QtAgg|TkAgg",
    r"plt\s*\.\s*show\s*\(",
    r"\.show\s*\(\s*\)",
]
_POPUP_RE = [re.compile(p) for p in _POPUP_PATTERNS]


def _has_popup_risk(text):
    """粗筛（正则、含注释与字符串）：历史上用来一把判掉"可能开窗"的脚本。

    ⚠️ t23 复核后它**不再**作为真跑前的判据：它的 `plt.show\\(` / `\\.show\\(\\)` 两条
    把大量"只是 plt.show()"的无害脚本也扫进来了（MPLBACKEND=Agg 下 show() 是 no-op）。
    真跑前请用 :func:`_window_risk`（A 档，AST 精确）。本函数保留给"两档对比"的普查。
    """
    return any(pat.search(text) for pat in _POPUP_RE)


# ---------------------------------------------------------------------------
# t23 两档口径（船长裁定 4）：**A 档真会开窗**（静态排除）vs **B 档 Agg 下无害**（可真跑）
#
# 为什么要把 plt.show() 从"危险"里摘出来：`MPLBACKEND=Agg` 下 `plt.show()` 是**无副作用的
# no-op**（Agg 没有窗口系统）。真正会开窗的是脚本**自己**要 GUI 后端或直接 import GUI 库；
# 而本版沙箱已经把这两条通道都堵死了（t15 实测 D1：GUI 库导入被拦；D2/D2b：
# `matplotlib.use(<GUI 后端>)` 被拦；再叠加 env 硬钉 Agg + `_poison_tk_windows` 事后补丁）。
# 把"只是 plt.show()"也算成危险，等于白白丢掉六成样本，口径上站不住。
# 不确定的一律归 A 档（宁可少跑，也不弹窗）。
# ---------------------------------------------------------------------------
_WINDOW_MODULES = ('tkinter', '_tkinter', 'tk', 'pyqt5', 'pyqt6', 'pyqt4',
                   'pyside', 'pyside2', 'pyside6', 'wx', 'gi', 'gtk', 'qtpy',
                   'pyqtgraph')
_WINDOW_BACKEND_HINTS = ('tk', 'qt', 'gtk', 'wx', 'macosx', 'cocoa',
                         'webagg', 'nbagg', 'widget', 'ipympl', 'notebook')
_MPL_BASES = ('plt', 'pyplot', 'fig', 'figure', 'mpl', 'matplotlib')
_MPL_INTERACTIVE_ATTRS = ('pause', 'ion', 'iwait', 'waitforbuttonpress',
                          'ginput', 'get_current_fig_manager', 'switch_backend')
_DIALOG_BASES = ('filedialog', 'messagebox', 'simpledialog', 'colorchooser',
                 'tkinter')
_DIALOG_ATTRS = ('askopenfilename', 'asksaveasfilename', 'askdirectory',
                 'askopenfile', 'askopenfilenames', 'askstring', 'askinteger',
                 'askfloat', 'showwarning', 'showerror', 'showinfo', 'askyesno',
                 'askokcancel', 'askretrycancel')


def _window_risk(text):
    """A 档判据：脚本是否**真的会开窗**（AST 静态，零执行）。返回证据列表。

    归 A 档的信号：
      * ``import tkinter/_tkinter/PyQt*/PySide*/wx/gi/gtk/...``（GUI 库本身）；
      * ``matplotlib.use('<GUI 后端>')`` / ``plt.switch_backend('<GUI 后端>')``
        （TkAgg / Qt5Agg / GTK3Agg / wx / macosx / WebAgg / nbagg / widget …）；
      * 文件对话框 / 提示框（``filedialog`` / ``messagebox`` / ``askopenfilename`` …）；
      * ``webbrowser.open()``（会拉起浏览器窗口）；
      * **交互式显示调用**（``plt.pause`` / ``plt.ion`` / ``waitforbuttonpress`` /
        ``ginput`` / ``get_current_fig_manager``）—— Agg 下多半无害，但**不确定**，
        按"宁可少跑也不弹窗"保守归 A。

    **不含** ``plt.show()`` / ``fig.show()`` / ``plt.draw()``：Agg 下那是 no-op，
    属 B 档（可真跑）。没有证据 → 返回空列表。
    """
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    ev = []

    def _mark(s):
        if s not in ev:
            ev.append(s)

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if (a.name or '').split('.')[0] in _WINDOW_MODULES:
                    _mark('import %s' % a.name)
        elif isinstance(node, ast.ImportFrom):
            if (node.module or '').split('.')[0] in _WINDOW_MODULES:
                _mark('from %s import ...' % node.module)
        elif isinstance(node, ast.Call):
            fn = node.func
            attr = getattr(fn, 'attr', '')
            base = getattr(fn, 'value', None)
            # ① GUI 后端切换
            if attr in ('use', 'switch_backend') and node.args:
                a0 = node.args[0]
                if isinstance(a0, ast.Constant) and isinstance(a0.value, str):
                    if any(h in a0.value.lower()
                           for h in _WINDOW_BACKEND_HINTS):
                        _mark("%s('%s')" % (getattr(base, 'id', 'matplotlib'),
                                            a0.value))
            if isinstance(base, ast.Name):
                b = base.id
                if b in _MPL_BASES and attr in _MPL_INTERACTIVE_ATTRS:
                    _mark('%s.%s()' % (b, attr))
                if b == 'webbrowser' and attr == 'open':
                    _mark('webbrowser.open()')
                if b in _DIALOG_BASES and attr in _DIALOG_ATTRS:
                    _mark('%s.%s()' % (b, attr))
            elif isinstance(base, ast.Attribute) and base.attr in _DIALOG_BASES:
                if attr in _DIALOG_ATTRS:
                    _mark('%s.%s()' % (base.attr, attr))
            # 裸 askopenfilename()（from tkinter.filedialog import ...）
            if isinstance(fn, ast.Name) and fn.id in _DIALOG_ATTRS:
                _mark('%s()' % fn.id)
    return ev


# ---------------------------------------------------------------------------
# ④ 只动目标 —— 语料版（无模板知识）。三条子判据：
#   a) 承诺违约：声称写回（applied/changes/block_lines）但字节一个没变。
#   b) 落地字面量：声称落地的字段值必须以数值字面量出现在源码（_values_present）。
#   c) diff 外科手术：删除行 = 违约；替换行的新行必须含某个落地值；新增行只允许
#      在块写回时出现（块行可自由改动）。
# ---------------------------------------------------------------------------
BLOCK_START = '# ===== auto layout (generated) - do not edit inside ====='
BLOCK_END = '# ===== end auto layout ====='

_NUM_RE = re.compile(r'[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?')


def _line_has_value(line, vals, tol=1e-6):
    for tok in _NUM_RE.findall(line):
        try:
            x = float(tok)
        except ValueError:
            continue
        if any(abs(float(v) - x) <= tol for v in vals):
            return True
    return False


def _block_line_idxs(lines):
    idxs = set()
    inside = False
    for k, ln in enumerate(lines):
        s = ln.strip()
        if BLOCK_START in s:
            inside = True
        if inside:
            idxs.add(k)
        if BLOCK_END in s and inside:
            inside = False
    return idxs


def _target_values_for_fields(data, pending):
    """参数里**不在 pending 中**的那些字段的目标值（按字段判，值无歧义）。"""
    vals = []
    for axitem in data.get('axes') or []:
        i = axitem.get('index')
        if not isinstance(i, int):
            continue
        if axitem.get('pos') is not None and 'ax%d.pos' % i not in pending:
            vals += [v for v in axitem['pos'] if isinstance(v, (int, float))]
        for f in ('title_fontsize', 'tick_fontsize', 'label_fontsize'):
            v = axitem.get(f)
            if v is not None and isinstance(v, (int, float)) \
                    and 'ax%d.%s' % (i, f) not in pending:
                vals.append(v)
    if isinstance(data.get('figsize_in'), (list, tuple)) \
            and 'figsize' not in pending:
        vals += [v for v in data['figsize_in'] if isinstance(v, (int, float))]
    return vals


def _check_target_only_corpus(orig_text, cur_text, data, res):
    """④ 只动目标（语料版，无模板知识）。返回 (ok, detail)。"""
    if cur_text == orig_text:
        # 一个字节没动 → ④ 平凡成立；但"声称写回却字节未变"是承诺违约
        if res.get('applied') or res.get('changes') or res.get('block_lines'):
            return False, ('承诺违约：声称写回（applied=%s changes=%r block_lines=%s）'
                           '但字节一个没变'
                           % (res.get('applied'), res.get('changes'),
                              res.get('block_lines')))
        return True, ''
    # 字节真的变了 → 引擎声称写入了。
    block_style = (res.get('style') == 'block') or bool(res.get('block_lines')) \
        or (BLOCK_START in cur_text)
    pending = set(res.get('pending') or [])
    if block_style:
        # 块写回：块会落地"除 pending 外"的全部字段（含原位已落地 + 块新增）
        landed = _target_values_for_fields(data, pending)
    else:
        # 纯原位：只核对 res['changes']（covered）声称落地的字段，其余跳过
        landed = []
        covered = set(res.get('changes') or [])
        for axitem in data.get('axes') or []:
            i = axitem.get('index')
            if not isinstance(i, int):
                continue
            if axitem.get('pos') is not None and 'ax%d.pos' % i in covered:
                landed += [v for v in axitem['pos'] if isinstance(v, (int, float))]
            for f in ('title_fontsize', 'tick_fontsize', 'label_fontsize'):
                v = axitem.get(f)
                if v is not None and 'ax%d.%s' % (i, f) in covered:
                    landed.append(v)
        if 'figsize' in covered and isinstance(data.get('figsize_in'), (list, tuple)):
            landed += [v for v in data['figsize_in'] if isinstance(v, (int, float))]
    if not landed:
        return False, ('字节变了但没有可核对的落地字段（style=%s changes=%r '
                       'pending=%r）' % (res.get('style'), res.get('changes'),
                                        sorted(pending)))
    if not _values_present(cur_text, landed):
        return False, '声称落地的字段值没以数值字面量出现在源码: %r' % (landed,)
    # c) diff 外科手术
    orig_lines = orig_text.split('\n')
    cur_lines = cur_text.split('\n')
    ob = _block_line_idxs(orig_lines)
    cb = _block_line_idxs(cur_lines)
    sm = difflib.SequenceMatcher(None, orig_lines, cur_lines, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == 'equal':
            continue
        if tag == 'delete':
            if all(k in ob for k in range(i1, i2)):
                continue                       # 旧块被移除，允许
            return False, ('引擎删除了行 %d..%d（原位/新增不应删行）: %r'
                           % (i1 + 1, i2, orig_lines[i1][:80]))
        if tag == 'replace':
            if all(k in ob for k in range(i1, i2)) \
                    and all(k in cb for k in range(j1, j2)):
                continue                       # 块内容被重写，允许
            for ln in cur_lines[j1:j2]:
                if not _line_has_value(ln, landed):
                    return False, ('替换后的行 %d 不含任何落地值（疑似动了非目标代码）: %r'
                                   % (j1 + 1, ln.strip()[:90]))
        if tag == 'insert':
            if block_style:
                continue                       # 块插入（含块守卫），允许
            return False, ('非块写回却新增了行 %d..%d（原位写回不应增行）: %r'
                           % (j1 + 1, j2, cur_lines[j1].strip()[:90]))
    return True, ''


# ---------------------------------------------------------------------------
# 语料发现与分层抽样（只读 manifest.jsonl）
# ---------------------------------------------------------------------------
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)                     # <树> = D:\Agent\deepseek\mpltweak
WORKSPACE = os.path.dirname(ROOT)                # D:\Agent\deepseek
CORPUS_DEFAULT = os.environ.get(
    'MPLTWEAK_CORPUS', os.path.join(WORKSPACE, 'corpus_union'))
OUTDIR_DEFAULT = os.environ.get(
    'MPLTWEAK_SCAN_OUT', os.path.join(WORKSPACE, '_review', 'corpus_invariants_t4'))
MANIFEST_NAME = 'manifest.jsonl'

MAX_BYTES = 500 * 1024                            # >500KB 的脚本跳过（罕见）
WORKER_TIMEOUT = 90                               # 每份 worker 墙钟上限（秒）


def _stratum(source):
    if source == 'zenodo':
        return 'zenodo'
    if source.startswith('github-'):
        return 'github'
    if source.startswith('matplotlib-gallery') or source.startswith('gallery-'):
        return 'gallery'
    return 'other'


def load_manifest(corpus_root):
    path = os.path.join(corpus_root, MANIFEST_NAME)
    rows = []
    with open(path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            o = json.loads(line)
            rows.append(o)
    return rows


def sample_rows(rows, limit, seed):
    """按来源分层抽样 plot==true 的脚本。返回 (chosen, composition)。"""
    by_src = {'zenodo': [], 'github': [], 'gallery': [], 'other': []}
    for r in rows:
        if not r.get('plot'):
            continue
        by_src[_stratum(r.get('source', ''))].append(r)
    rng = random.Random(seed)
    weights = {'zenodo': 0.4, 'github': 0.3, 'gallery': 0.3}
    chosen = []
    comp = {}
    for k in ('zenodo', 'github', 'gallery'):
        pool = by_src[k]
        n = int(round(limit * weights[k]))
        if len(pool) < n:
            n = len(pool)
        picked = rng.sample(pool, n)
        chosen.extend(picked)
        comp[k] = len(pool)
    # 不足时从 other 补齐
    if len(chosen) < limit and by_src['other']:
        need = limit - len(chosen)
        picked = rng.sample(by_src['other'], min(need, len(by_src['other'])))
        chosen.extend(picked)
    rng.shuffle(chosen)
    return chosen[:limit], comp


def sha256_bytes(raw):
    return hashlib.sha256(raw).hexdigest()


# ---------------------------------------------------------------------------
# t23：全量候选池（t7 的 both_ok 筛选结果）——**确定性顺序**是中断续跑的前提
# ---------------------------------------------------------------------------
POOL_DEFAULT = os.environ.get(
    'MPLTWEAK_POOL',
    os.path.join(WORKSPACE, '_review', 'corpus_filter_t7', 'filter.jsonl'))


def pool_sha256(path):
    try:
        with open(path, 'rb') as f:
            return hashlib.sha256(f.read()).hexdigest()
    except OSError:
        return None


def load_pool(pool_path):
    """读 t7 筛选池：只取 ``both_ok=true``，按 ``path`` 排序（确定序 → 续跑不重不漏）。

    为什么按 path 排序而不是沿用文件行序：行序取决于 t7 当时写文件的顺序（并发
    worker 完成序），随时可能变；排序后 seq ↔ 脚本的映射只取决于内容，续跑才对得上。
    """
    rows = []
    with open(pool_path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                o = json.loads(line)
            except ValueError:
                continue
            if o.get('both_ok'):
                rows.append(o)
    rows.sort(key=lambda r: str(r.get('path', '')))
    return rows


# 判定层分类（t10 口径）：把"脚本自己需要 GUI / 等人输入"从 na_reject 里分出来
# —— 分层分母表要求 na_gui 与 na_stdin **分开计数**。
_GUI_MARKERS = ('禁止 gui', '只允许无窗口后端',
                'script_needs_gui_or_headless_impossible')
_STDIN_MARKERS = ('禁止等待 stdin', 'eoferror', 'no_stdin')


def _na_class(text, attribution=''):
    """返回 ``'na_gui'`` / ``'na_stdin'`` / ``None``。

    只看**守卫自己的行为信号文案**（沙箱禁用文案 / verify 的归因码），不做语义猜测：
      * 禁 GUI 层 → '沙箱：禁止 GUI 库导入'；
      * 后端锁层 → '沙箱：只允许无窗口后端'；
      * 无人值守层 → '沙箱：禁止等待 stdin' / EOFError。
    """
    low = (text or '').lower()
    if (str(attribution or '') == 'script_needs_gui_or_headless_impossible'
            or any(m in low for m in _GUI_MARKERS)):
        return 'na_gui'
    if any(m in low for m in _STDIN_MARKERS):
        return 'na_stdin'
    return None


# ---------------------------------------------------------------------------
# worker：单份脚本的完整管线（apply×2 → revert → 四律），结果写 result_file
# ---------------------------------------------------------------------------
def _worker(relpath, corpus_root, workdir, result_file, fast=False):
    res = {'path': relpath, 'source': '', 'verdict': 'na_error',
           'laws': {}, 'checks': {}, 'detail': ''}
    src_path = os.path.join(corpus_root, relpath.replace('/', os.sep))
    try:
        raw = _read_bytes(src_path)
    except OSError as e:
        res['detail'] = '读语料失败: %s' % e
        _write_json(result_file, res)
        return
    res['size'] = len(raw)
    res['sha256'] = sha256_bytes(raw)
    if not relpath.endswith('.py'):
        res['verdict'] = 'na_skip'
        res['detail'] = '非 .py'
        _write_json(result_file, res)
        return
    if len(raw) > MAX_BYTES:
        res['verdict'] = 'na_skip'
        res['detail'] = '超限（%d B > %d B）' % (len(raw), MAX_BYTES)
        _write_json(result_file, res)
        return
    # 语料侧可解析性（M1 口径）：语法错/编码错 = 判据不适用
    from mpltweak import params, revert, writeback
    try:
        orig_text, enc, bom = params.read_text_ex(src_path)
        ast.parse(orig_text)
    except UnicodeDecodeError as e:
        res['verdict'] = 'na_skip'
        res['detail'] = '编码无法识别: %s' % e
        _write_json(result_file, res)
        return
    except SyntaxError as e:
        res['verdict'] = 'na_skip'
        res['detail'] = '语料侧语法错（line %s: %s）' % (e.lineno, e.msg)
        _write_json(result_file, res)
        return
    # 防弹窗守卫（只对真跑档生效）：脚本含 GUI/文件对话框调用 → 判据不适用，
    # 绝不真跑（0.1.9 教训：MPLBACKEND=Agg 挡不住 TkAgg/filedialog/PyQt）。
    if not fast:
        # A 档（真会开窗）静态排除；B 档（只有 plt.show() 等 Agg 下 no-op 的信号）**真跑**
        # —— 口径见 _window_risk 的注释（船长裁定 4，2026-10-12）。
        _w = _window_risk(orig_text)
        if _w:
            res['verdict'] = 'na_gui'
            res['gui_static'] = True
            res['gui_tier'] = 'A'
            res['detail'] = ('A 档静态防弹窗：真会开窗的信号 %s（不真跑）'
                             % ', '.join(_w[:4]))
            _write_json(result_file, res)
            return
        if _has_popup_risk(orig_text):
            # 只命中粗筛（plt.show() / .show()）：Agg 下无害，照常真跑，留个档
            res['gui_tier'] = 'B'
    if not fast and _has_net_risk(orig_text):
        # 试点实测：真跑链路里 socket/subprocess 层不生效（verify 子进程被当 harness）。
        # 这类脚本一律**不真跑** —— 不能拿"期望它会拦住"去赌陌生脚本的行为。
        res['verdict'] = 'na_skip'
        res['na_reason'] = 'net_risk'
        res['detail'] = ('静态预筛：脚本含联网/起子进程调用（真跑链路里 socket/'
                         'subprocess 层不生效，见 SUMMARY 试点证据）→ 不真跑')
        _write_json(result_file, res)
        return
    # 拷贝到隔离 workdir（语料目录本身只读，绝不写入）
    os.makedirs(workdir, exist_ok=True)
    stem = os.path.splitext(os.path.basename(relpath))[0]
    script = os.path.join(workdir, os.path.basename(relpath))
    with open(script, 'wb') as f:
        f.write(raw)
    pp = os.path.join(workdir, stem + '.json')
    # 目标字段选"语料覆盖最广"的 figsize（_numbers_e28a608.md 口径：figsize 站点
    # 2,256/2,256 可达、99.6% 原位；title 只有 193 站点且常因"轴变量证不出"进
    # pending → 永久 best_effort 判定层边界，见下）。figsize 用明显偏离常见的值。
    data = {'version': 4, 'script': os.path.basename(relpath), 'fig_index': 0,
            'figsize_in': [8.5, 5.5]}
    with open(pp, 'w', encoding='utf-8') as f:
        json.dump(data, f)
    orig_bytes = _read_bytes(script)
    orig_text = _read_text_any(script)
    # ---- ① apply：真实 CLI（apply --write --json，含 auto 退块；fast=静态档）----
    j1, rc1, out1 = _cli_apply(script, pp, workdir, fast=fast)
    if j1 is None:
        # CLI 自己没给出 JSON（超时 / 崩）——先看守卫文案里有没有可归类的行为信号
        # （例如禁 GUI 层直接掐掉导入，CLI 也可能拿不到 JSON），再退到 timeout/error。
        _nac0 = _na_class(out1)
        if _nac0:
            res['verdict'] = _nac0
            res['na_reason'] = 'guard_message'
            res['detail'] = '守卫拦截: %s' % _gbk_safe(out1)[-200:]
            _write_json(result_file, res)
            return
        res['verdict'] = 'na_error' if rc1 != -9 else 'na_timeout'
        res['detail'] = 'apply CLI %s: rc=%d %s' % (
            '超时' if rc1 == -9 else 'JSON 解析失败',
            rc1, _gbk_safe(out1)[-200:])
        _write_json(result_file, res)
        return
    reason1 = j1.get('reason')
    cur_bytes = _read_bytes(script)
    cur_text = _read_text_any(script)
    res['reason1'] = reason1
    res['style'] = j1.get('style')
    res['bytes_before'] = len(orig_bytes)
    res['bytes_after'] = len(cur_bytes)
    no_tamper = (cur_bytes == orig_bytes)
    res['no_tamper'] = no_tamper
    res['checks']['syntax'] = _check_syntax_text(cur_text)
    res['pending1'] = sorted(j1.get('pending') or [])
    _REJECT = ('no_fig', 'no_axes', 'bad_params', 'fail', 'no_figure')
    if reason1 in _REJECT or rc1 != 0:
        # 拒写批：必须字节不动（no-tamper）；幂等/可逆不适用（判据不适用）
        if not no_tamper:
            res['verdict'] = 'violation'
            res['detail'] = ('拒写（rc=%d reason=%s）却改了字节（no-tamper 违约）'
                             % (rc1, reason1))
        else:
            # t23：把"脚本自己需要 GUI / 等人输入"从 na_reject 里分出来单独计数
            # （分层分母表要求 na_gui / na_stdin 分开）——判据是**守卫自己的文案**。
            _nac = _na_class('%s %s %s' % (j1.get('err') or '',
                                           j1.get('attribution') or '',
                                           out1 or ''), j1.get('attribution'))
            res['verdict'] = _nac or 'na_reject'
            res['na_reason'] = _nac or 'engine_rejected'
            res['attribution'] = j1.get('attribution')
            res['detail'] = 'rc=%d reason=%s err=%s' % (
                rc1, reason1, (j1.get('err') or '')[:200])
        _write_json(result_file, res)
        return
    # 引擎是否声称"写入了磁盘"（CLI enrichment 后的 applied / block_lines / changes）
    claimed_write = bool(j1.get('applied')) or bool(j1.get('block_lines')) \
        or bool(j1.get('changes'))
    if not claimed_write and reason1 != 'no_change':
        # 引擎如实报告没写（目标证不出/块表达不了 → fail-closed）：
        # 判据不适用，不是违约。字节必须不动。
        if not no_tamper:
            res['verdict'] = 'violation'
            res['detail'] = ('引擎报未写（reason=%r pending=%r）却改了字节'
                             % (reason1, sorted(j1.get('pending') or [])))
            res['dbg'] = {'j1_keys': sorted(j1.keys()),
                          'out_tail': _gbk_safe(out1)[-300:]}
        else:
            _nac2 = _na_class('%s %s' % (j1.get('attribution') or '', out1 or ''),
                              j1.get('attribution'))
            res['verdict'] = _nac2 or 'na_reject'
            res['na_reason'] = _nac2 or 'engine_not_written'
            res['attribution'] = j1.get('attribution')
            res['detail'] = ('engine_not_written: reason=%s pending=%r'
                             % (reason1, sorted(j1.get('pending') or [])))[:240]
        _write_json(result_file, res)
        return
    # ---- 成功写回批（applied=true 已写 / no_change 已一致）：四律 ----
    ok_syntax = res['checks']['syntax']
    # 承诺违约：声称写回（applied/changes/block_lines）但字节一个没变
    breach = bool(claimed_write and no_tamper)
    res['checks']['promise'] = not breach
    # ① 幂等：第二次 apply 后字节必须不变；reason 期望 no_change
    j2, rc2, out2 = _cli_apply(script, pp, workdir, fast=fast)
    cur2_bytes = _read_bytes(script)
    bytes_stable = (cur2_bytes == cur_bytes)
    reason2 = j2.get('reason') if j2 else None
    res['reason2'] = reason2
    res['bytes_after2'] = len(cur2_bytes)
    if not bytes_stable:
        res['checks']['idempotent'] = False       # 真违约：二次 apply 重写字节
        res['detail'] = '二次 apply 重写了字节（%d → %d B，幂等破功）' % (
            len(cur_bytes), len(cur2_bytes))
    elif reason2 == 'no_change':
        res['checks']['idempotent'] = True
    else:
        # 字节稳定但 reason 不是 no_change（如永久 pending 的 best_effort）：
        # 判定层非幂等 = _numbers_e28a608.md m5 的"已知边界"，不是字节级违约
        res['checks']['idempotent'] = 'boundary'
        res['idem_boundary'] = 'reason2=%s pending2=%r' % (
            reason2, sorted((j2 or {}).get('pending') or []))
    # ② 可逆：revert --write 逐字节还原（有备份）或 rc=2 且不动（无备份）
    from mpltweak import revert, writeback
    bak = writeback._backup_path(script, pp)
    have_backup = os.path.exists(bak)
    res['have_backup'] = have_backup
    _cap, _cerr = io.StringIO(), io.StringIO()
    try:
        with contextlib.redirect_stdout(_cap), contextlib.redirect_stderr(_cerr):
            rc_rev = revert.main([script, '--write'])
    except BaseException as e:                     # noqa: BLE001
        res['verdict'] = 'na_error'
        res['detail'] = 'revert 异常: %s: %s' % (type(e).__name__, str(e)[:300])
        _write_json(result_file, res)
        return
    rev_bytes = _read_bytes(script)
    res['rc_revert'] = rc_rev
    if have_backup:
        ok_rev = _check_reversible(orig_bytes, rev_bytes) and rc_rev == 0
    else:
        ok_rev = rc_rev == 2 and rev_bytes == cur2_bytes
    res['checks']['reversible'] = ok_rev
    # ④ 只动目标（语料版，用 CLI enrichment 后的 j1）
    ok_tgt, tgt_detail = _check_target_only_corpus(orig_text, cur_text, data, j1)
    res['checks']['target_only'] = ok_tgt
    if not ok_tgt:
        res['detail'] = tgt_detail[:400]
    idem_ok = res['checks']['idempotent'] is True
    all_ok = ok_syntax and idem_ok and ok_rev and ok_tgt and not breach
    res['verdict'] = 'pass' if all_ok else 'violation'
    if not all_ok and not res.get('detail'):
        res['detail'] = '四律未全过: 语法=%s 幂等=%s 可逆=%s 只动目标=%s 承诺=%s' \
                        % (ok_syntax, res['checks']['idempotent'], ok_rev,
                           ok_tgt, not breach)
    _write_json(result_file, res)


def _write_json(path, obj):
    """**原子**落盘：同目录临时文件 → fsync → os.replace。

    为什么必须原子（t23 全量真跑）：一次全量 1,837 份、可能跑 30~60 分钟，用户随时
    可能中断（Ctrl+C / 关机 / 进程被杀）。**半截 JSON 比没有文件更糟** —— 续跑时
    会被当成"已完成"读进来，解析失败又得靠猜。os.replace 在同一文件系统内是原子的，
    所以"要么是上一版完整结果、要么是本版完整结果"，不存在中间态。
    """
    d = os.path.dirname(os.path.abspath(path)) or '.'
    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(prefix='.res-', suffix='.tmp', dir=d)
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(obj, f, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        tmp = None
    except OSError:
        pass                       # 落盘失败不该把 worker 弄崩（结果丢了会重跑）
    finally:
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass


def _corpus_snapshot(corpus_root):
    snap = {}
    for dirpath, dirnames, filenames in os.walk(corpus_root):
        dirnames[:] = [d for d in dirnames
                       if d not in ('.git', '__pycache__', '_logs')]
        for fn in filenames:
            p = os.path.join(dirpath, fn)
            try:
                st = os.stat(p)
                snap[os.path.relpath(p, corpus_root)] = (st.st_size, st.st_mtime_ns)
            except OSError:
                pass
    return snap


def _load_result(path):
    """读一份逐份结果；解析不了/不存在 → None（续跑时按"未完成"重跑）。"""
    try:
        with open(path, 'r', encoding='utf-8') as f:
            o = json.load(f)
        return o if isinstance(o, dict) and o.get('verdict') else None
    except (OSError, ValueError):
        return None


def _ensure_sandbox():
    """保证全量真跑在沙箱内：环境没指游乐场就**自建**一个（绝不放任裸跑）。

    为什么自建而不是报错退出：`--resume` 是给"中断后随手续跑"用的命令，要求用户
    每次都手写环境变量太脆；而"没有沙箱就跑 1,837 份陌生脚本"是不可接受的。
    自建位置固定在 _TMPROOT 下（测试自己的临时区），并写回 os.environ 让
    `_cli_env` 给子进程注入。
    """
    root = os.environ.get('MPLTWEAK_SANDBOX_ROOT')
    if not root:
        root = os.path.join(_TMPROOT, 'sandbox')
        os.environ['MPLTWEAK_SANDBOX_ROOT'] = root
    root = os.path.abspath(root)
    for sub in ('', 'tmp', '.mplconfig', 'play'):
        os.makedirs(os.path.join(root, sub), exist_ok=True)
    return root


# ---------------------------------------------------------------------------
# 父扫描：抽样 → 并发 worker → 汇总 → SUMMARY.md + stdout 报告
# ---------------------------------------------------------------------------
def _src_provenance():
    """本次运行的 **src 溯源**：关键文件 md5 + git HEAD（只读命令）。"""
    keys = ('apply.py', 'writeback.py', 'verify.py', 'messages.py', 'preflight.py',
            'params.py', 'revert.py', 'launch.py', 'cli.py')
    out = {}
    for k in keys:
        try:
            with open(os.path.join(ROOT, 'src', 'mpltweak', k), 'rb') as f:
                out[k] = hashlib.md5(f.read()).hexdigest()
        except OSError:
            out[k] = None
    out['git_head'] = None
    out['git_dirty'] = None
    # 子进程 env 一律显式钉 MPLBACKEND=Agg（hygiene 棘轮口径：任何 subprocess
    # 调用点都要有可识别的"钉后端"来源；git 用不上后端，但口径统一比例外好）
    _genv = dict(os.environ, MPLBACKEND='Agg')
    try:
        _g = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT,
                            capture_output=True, timeout=15, env=_genv)
        out['git_head'] = (re.sub(r'\s+', '', _g.stdout.decode('ascii', 'replace'))
                           or None)
        _g2 = subprocess.run(['git', 'status', '--porcelain'], cwd=ROOT,
                             capture_output=True, timeout=15, env=_genv)
        out['git_dirty'] = len([x for x in
                                _g2.stdout.decode('utf-8', 'replace').splitlines()
                                if x.strip()])
    except (OSError, subprocess.SubprocessError):
        pass
    return out


def _scan_main(args):
    corpus_root = os.path.abspath(args.corpus or CORPUS_DEFAULT)
    # 产出目录：池模式另开一个（不动 t4 那 240 份的台账）；试点再单独一个 ——
    # 否则试点的 limit 会写进全量的 RUN_META，后续 --resume 对账会被卡住。
    if getattr(args, 'pilot_phase', False):
        _out_default = os.path.join(WORKSPACE, '_review',
                                    'corpus_invariants_t23_pilot')
    elif (args.pool or args.all or args.resume):
        _out_default = os.path.join(WORKSPACE, '_review', 'corpus_invariants_t23')
    else:
        _out_default = OUTDIR_DEFAULT
    outdir = os.path.abspath(args.out or _out_default)
    os.makedirs(outdir, exist_ok=True)
    workroot = os.path.join(_TMPROOT, 'work')
    os.makedirs(workroot, exist_ok=True)
    resdir = os.path.join(outdir, 'results')
    os.makedirs(resdir, exist_ok=True)
    manifest_path = os.path.join(corpus_root, MANIFEST_NAME)
    rows = None
    comp = {}
    # ---- 候选取自哪里（t23）：t7 的 both_ok 全量池，或旧的 manifest 分层抽样 ----
    pool_mode = bool(args.pool or args.all or args.pilot or args.resume)
    pool_path = None
    pool_sha = None
    if pool_mode:
        pool_path = os.path.abspath(args.pool or POOL_DEFAULT)
        if not os.path.exists(pool_path):
            print('SKIP | 候选池不存在: %s' % pool_path)
            return 0
        chosen = load_pool(pool_path)
        pool_sha = pool_sha256(pool_path)
        for r in chosen:
            k = r.get('stratum') or _stratum(r.get('source', ''))
            comp[k] = comp.get(k, 0) + 1
        sb_root = _ensure_sandbox()
        # 工作目录必须**在游乐场内**（t23 试点实测踩出来的）：
        #   ① sitecustomize 判定"这是不是在跑用户脚本"看的是 `argv[0]` 是不是游乐场
        #      内的真实文件 —— 直接跑脚本时只有落在游乐场内才拿到**完整**守卫
        #      （子进程 + socket），否则只剩文件那一层；
        #   ② 脚本的相对路径写入（savefig('out.png')）落在 cwd，cwd 在游乐场外会被
        #      文件守卫拦掉 → 每一份都会被判"跑不通"，全量结果毫无意义。
        workroot = os.path.join(sb_root, 'work')
        os.makedirs(workroot, exist_ok=True)
        print('候选池: %s' % pool_path)
        print('        both_ok=%d 份（分层: %s）pool_sha256=%s'
              % (len(chosen), comp, (pool_sha or '?')[:16]))
        print('沙箱: MPLTWEAK_SANDBOX_ROOT=%s（禁 GUI 层 + 无人值守层强制开）'
              % sb_root)
        if getattr(args, 'pilot_phase', False):
            # 试点第一件事：**实测**沙箱四类拦截 + 不误拦对照。没拦住就停手，
            # 绝不放开语料真跑（陌生脚本越界成功 = 用户机器被写坏）。
            _ev, _all_blocked = _pilot_canaries(workroot, sb_root)
            args._pilot_evidence = _ev
            print('[试点] 沙箱逐类拦截实测（与语料真跑同一条环境注入路径）：')
            for _l in _ev:
                print('  ' + _l)
            if not _all_blocked:
                print('!! 试点发现沙箱没拦住 —— **立即停手**（不放开语料真跑）；'
                      '把上面这几行证据报给船长。')
                return 3
    else:
        if not os.path.exists(manifest_path):
            print('SKIP | 语料 manifest 不存在: %s（本机没有 corpus_union，跳过扫描）'
                  % manifest_path)
            return 0
        rows = load_manifest(corpus_root)
        chosen, comp = sample_rows(rows, args.limit, args.seed)
        print('语料 manifest: %d 份唯一脚本（plot==true 分层: %s）'
              % (len(rows), comp))
        print('抽样: %d 份（zenodo=%d github=%d gallery=%d, seed=%d, jobs=%d, '
              'worker_timeout=%ds）'
              % (len(chosen),
                 sum(1 for r in chosen if _stratum(r.get('source', '')) == 'zenodo'),
                 sum(1 for r in chosen if _stratum(r.get('source', '')) == 'github'),
                 sum(1 for r in chosen if _stratum(r.get('source', '')) == 'gallery'),
                 args.seed, args.jobs, WORKER_TIMEOUT))
    if pool_mode and args.limit:
        # 试点/限量：取**确定序**的前 N 份（与续跑的 seq 映射一致）
        chosen = chosen[:args.limit]

    # ---- 中断续跑（t23）----
    # seq ↔ 脚本的映射只取决于「排序后的池 + limit」这个确定序；结果文件按 seq 命名，
    # 所以"哪些跑过了"可以直接从磁盘看出来，不需要额外台账。
    n_all = len(chosen)
    done = set()
    if args.resume:
        for _seq in range(n_all):
            if _load_result(os.path.join(resdir, 's_%03d.json' % _seq)) is not None:
                done.add(_seq)
    todo = [s for s in range(n_all) if s not in done]
    print('本次跑 %d 份 / 已跳过 %d 份' % (len(todo), len(done)))
    # 续跑安全阀：候选池/limit/验证档变了还续跑，会"漏跑一批 + 重跑一批"却看不出来。
    meta_path = os.path.join(outdir, 'RUN_META.json')
    _old_meta = None
    if args.resume and os.path.exists(meta_path):
        try:
            with open(meta_path, 'r', encoding='utf-8') as _f:
                _old_meta = json.load(_f)
        except (OSError, ValueError):
            _old_meta = None
    if _old_meta and pool_mode and (_old_meta.get('pool_sha256') != pool_sha
                                    or _old_meta.get('limit') != args.limit
                                    or bool(_old_meta.get('fast'))
                                    != bool(args.fast)):
        print('!! 续跑中止：结果目录里的 RUN_META 与本次候选池/limit/fast 不一致 ——')
        print('   续跑对不上会"漏跑或重跑"，所以宁可停手。换 --out 目录，'
              '或去掉 --resume 重跑。')
        print('   RUN_META = %s' % _old_meta)
        return 2
    _write_json(meta_path, {'pool': pool_path, 'pool_sha256': pool_sha,
                            'limit': args.limit, 'corpus': corpus_root,
                            'fast': bool(args.fast), 'jobs': args.jobs,
                            'n_all': n_all})
    # 语料目录只读证据：扫描前后快照对比
    snap_before = _corpus_snapshot(corpus_root)
    proc = sys.executable
    # worker 自身也要能继承沙箱注入（MPLTWEAK_SANDBOX_ROOT 已设时把 tests/sandbox
    # 并进 PYTHONPATH，见 _cli_env；worker 是 harness 级进程，只受文件守卫约束）。
    py_env = dict(os.environ,
                  MPLBACKEND='Agg', MPLTWEAK_LANG='zh')
    # worker 是**纯 harness**：它要往 `_review/...`（游乐场外）原子写结果，而文件
    # 守卫对 harness 进程同样生效 —— 所以**不能**把 sandbox 并进 worker 的
    # PYTHONPATH（t23 试点实测：并进去之后结果文件全被守卫拦掉）。
    # 真正执行语料脚本的是 CLI 子进程链（apply → verify → 脚本），那条链由
    # `_cli_env` 注入守卫，sandbox / 游乐场 / 禁 GUI / 无人值守都在那里生效。
    _pp = [os.path.join(ROOT, 'src')]
    py_env['PYTHONPATH'] = os.pathsep.join(_pp)
    # ---- 有界 worker 池（jobs 路并发）----
    # 关键：**绝不能一次把所有 worker 都 spawn 出去** —— 每个 worker 会再拉起
    # `python -m mpltweak apply` 子进程（含 matplotlib），240 路同时 spawn 会打爆
    # Windows 页面文件（实测 WinError 1455）。必须用槽位池：最多 jobs 个在跑，
    # 跑完一个补一个。
    running = {}          # seq -> (rel, Popen, started_at)
    results = []
    it = iter(list(todo))          # 只跑"还没有结果文件"的那些（--resume）

    def _collect(seq):
        rel, p, _st = running.pop(seq)
        if p.returncode is None:
            p.kill()
            p.wait()
        rf = os.path.join(resdir, 's_%03d.json' % seq)
        if not os.path.exists(rf):
            return {'seq': seq, 'path': rel, 'verdict': 'na_error',
                    'detail': 'worker 异常退出（rc=%d，未产出结果）' % p.returncode}
        try:
            with open(rf, 'r', encoding='utf-8') as f:
                r = json.load(f)
            r.setdefault('seq', seq)
            r.setdefault('path', rel)
            return r
        except (OSError, ValueError):
            return {'seq': seq, 'path': rel, 'verdict': 'na_error',
                    'detail': 'worker 结果缺失/损坏'}

    def _finished_any():
        """返回已结束/超时的 seq 列表（不收集）。"""
        fin = []
        for s, (_r, p, st) in list(running.items()):
            if p.poll() is not None or time.time() - st > WORKER_TIMEOUT:
                fin.append(s)
        return fin

    t0 = time.time()
    for seq in it:
        row = chosen[seq]
        while len(running) >= args.jobs:
            fin = _finished_any()
            if fin:
                for s in fin:
                    results.append(_collect(s))
                break
            time.sleep(0.25)
        rel = row['path']
        wd = os.path.join(workroot, 's_%03d' % seq)
        rf = os.path.join(resdir, 's_%03d.json' % seq)
        if os.path.exists(rf):
            os.remove(rf)      # 防 stale：上一轮崩溃遗留的结果不许被误读
        p = subprocess.Popen(
            [proc, os.path.abspath(__file__), '--worker', rel,
             corpus_root, wd, rf] + (['--fast'] if args.fast else []),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            env=py_env, cwd=workroot)
        running[seq] = (rel, p, time.time())
    while running:
        fin = _finished_any()
        if not fin:
            time.sleep(0.25)
            continue
        for s in fin:
            results.append(_collect(s))
    snap_after = _corpus_snapshot(corpus_root)
    corpus_untouched = (snap_before == snap_after)
    elapsed = time.time() - t0
    # ---- 汇总：**以磁盘上的逐份结果为准**（--resume 时上一轮跑过的也在里面）----
    _mem = {}
    for r in results:
        if isinstance(r.get('seq'), int):
            _mem[r['seq']] = r
    results = []
    for _seq in range(n_all):
        _r = _load_result(os.path.join(resdir, 's_%03d.json' % _seq))
        if _r is None:
            _r = _mem.get(_seq) or {
                'seq': _seq, 'path': chosen[_seq].get('path'),
                'verdict': 'na_error', 'detail': '无结果文件（worker 未产出）'}
        _r.setdefault('seq', _seq)
        _r.setdefault('path', chosen[_seq].get('path'))
        results.append(_r)
    # 汇总
    vcount = {}
    for r in results:
        v = r.get('verdict', 'na_error')
        vcount[v] = vcount.get(v, 0) + 1
    # 成功写回批 = 四律真正跑过的那批（checks 里有 idempotent 即成功批）
    succ = [r for r in results if 'idempotent' in (r.get('checks') or {})]
    viol = [r for r in results if r.get('verdict') == 'violation']
    na_skip = [r for r in results if r.get('verdict') == 'na_skip']
    na_reject = [r for r in results if r.get('verdict') == 'na_reject']
    na_timeout = [r for r in results if r.get('verdict') == 'na_timeout']
    na_error = [r for r in results if r.get('verdict') == 'na_error']
    law_ok = {k: sum(1 for r in succ
                     if r.get('checks', {}).get(k) is True)
              for k in ('syntax', 'idempotent', 'reversible', 'target_only')}
    idem_boundary = sum(1 for r in succ
                        if r.get('checks', {}).get('idempotent') == 'boundary')
    promise_ok = sum(1 for r in succ
                     if r.get('checks', {}).get('promise') is True)
    # 拒写批（引擎拒绝/未写）必须字节不动
    no_tamper_ok = sum(1 for r in na_reject if r.get('no_tamper') is True)
    no_tamper_total = len(na_reject)
    # 写 results.jsonl + SUMMARY.md
    with open(os.path.join(outdir, 'results.jsonl'), 'w', encoding='utf-8') as f:
        for r in sorted(results, key=lambda x: x.get('seq', 0)):
            f.write(json.dumps(r, ensure_ascii=False) + '\n')
    _obs_path = os.path.join(outdir, 'RUN_OBSERVATIONS.json')
    _obs = None
    if os.path.exists(_obs_path):
        try:
            with open(_obs_path, 'r', encoding='utf-8') as _f:
                _obs = json.load(_f)
        except (OSError, ValueError):
            _obs = None
    _extra = {
        'observations': _obs,
        'pool_path': pool_path, 'pool_sha256': pool_sha, 'pool_mode': pool_mode,
        'n_all': n_all, 'n_skipped_prev': len(done), 'resumed': bool(args.resume),
        'gui_static': sum(1 for r in results if r.get('gui_static')),
        'provenance': _src_provenance(),
        'pilot_evidence': getattr(args, '_pilot_evidence', None),
    }
    summary = _build_summary(
        corpus_root, manifest_path,
        (len(rows) if rows is not None else len(chosen)),
        comp, chosen, results,
        vcount, succ, viol, na_skip, na_reject, na_timeout, na_error,
        law_ok, idem_boundary, promise_ok, no_tamper_ok, no_tamper_total,
        corpus_untouched, elapsed, args, extra=_extra)
    with open(os.path.join(outdir, 'SUMMARY.md'), 'w', encoding='utf-8') as f:
        f.write(summary)
    # stdout 报告
    print('---- 扫描结果（%ds）----' % elapsed)
    print('判定分布: %s' % ', '.join('%s=%d' % (k, v) for k, v in sorted(vcount.items())))
    print('分层分母: pass=%d / violation=%d / na_gui=%d（其中静态未真跑 %d）/ '
          'na_stdin=%d / na_reject=%d / na_skip=%d / na_timeout=%d / na_error=%d'
          % (vcount.get('pass', 0), vcount.get('violation', 0),
             vcount.get('na_gui', 0),
             sum(1 for r in results if r.get('gui_static')),
             vcount.get('na_stdin', 0), vcount.get('na_reject', 0),
             vcount.get('na_skip', 0), vcount.get('na_timeout', 0),
             vcount.get('na_error', 0)))
    print('成功写回批（四律断言对象）: %d 份' % len(succ))
    for k in ('syntax', 'idempotent', 'reversible', 'target_only'):
        print('  %-12s %d/%d' % (k, law_ok[k], len(succ)))
    print('  幂等判定层边界       %d' % idem_boundary)
    print('  承诺违约=0            %d/%d' % (promise_ok, len(succ)))
    print('  拒写不改字节          %s'
          % ('%d/%d' % (no_tamper_ok, no_tamper_total) if no_tamper_total
             else 'n/a（本轮 na_reject = 0，此判据没有对象）'))
    print('语料目录未被修改: %s' % ('是' if corpus_untouched else '否（异常！）'))
    print('违约数: %d' % len(viol))
    for r in viol:
        print('  VIOLATION %s | %s' % (_gbk_safe(r.get('path', '?')),
                                       _gbk_safe(r.get('detail', ''))))
    print('产出: %s' % outdir)
    # 清理 workroot
    shutil.rmtree(workroot, ignore_errors=True)
    return 1 if viol else 0


def _build_summary(corpus_root, manifest_path, n_rows, comp, chosen, results,
                   vcount, succ, viol, na_skip, na_reject, na_timeout, na_error,
                   law_ok, idem_boundary, promise_ok, no_tamper_ok,
                   no_tamper_total, corpus_untouched, elapsed, args, extra=None):
    lines = []
    a = lines.append
    extra = extra or {}
    na_gui = [r for r in results if r.get('verdict') == 'na_gui']
    na_stdin = [r for r in results if r.get('verdict') == 'na_stdin']
    a('# 语料级不变量扫描报告（t4 / ⑥；t23 扩到全量池）')
    a('')
    a('> 复用 t2（tests/test_property_writeback.py）四条铁律断言函数，对语料'
      '（corpus_union，只读）逐份跑不变量扫描。语料目录**未被修改**：%s。'
      % ('✅' if corpus_untouched else '❌（异常）'))
    a('')
    a('## 0 扫描配置')
    if extra.get('pool_mode'):
        a('- 候选池: `%s`' % extra.get('pool_path'))
        a('- 池 sha256: `%s`（续跑对账依据，见 `RUN_META.json`）'
          % (extra.get('pool_sha256') or '?'))
        a('- 本次分母: **%d 份**（池内 both_ok 全量%s）'
          % (extra.get('n_all') or len(chosen),
             ('；本轮实跑 %d 份、续跑跳过 %d 份'
              % (len(chosen) - (extra.get('n_skipped_prev') or 0),
                 extra.get('n_skipped_prev') or 0))
             if extra.get('resumed') else ''))
        a('- 分层（来源）: %s' % comp)
    else:
        a('- 语料: `%s`（manifest %d 份 sha256 唯一脚本）' % (corpus_root, n_rows))
        a('- 抽样: %d 份（plot==true 分层; zenodo=%d / github=%d / gallery=%d），'
          'seed=%d，jobs=%d，worker 超时 %ds'
          % (len(chosen),
             sum(1 for r in chosen
                 if _stratum(r.get('source', '')) == 'zenodo'),
             sum(1 for r in chosen
                 if _stratum(r.get('source', '')) == 'github'),
             sum(1 for r in chosen
                 if _stratum(r.get('source', '')) == 'gallery'),
             args.seed, args.jobs, WORKER_TIMEOUT))
    a('- 沙箱: `MPLTWEAK_SANDBOX_ROOT=%s`（禁 GUI 层 + 无人值守层强制开；'
      '文件/子进程/网络由 audit hook 守卫）'
      % (os.environ.get('MPLTWEAK_SANDBOX_ROOT') or '**未设**'))
    a('- 每份参数: `{version:4, fig_index:0, figsize_in:[8.5,5.5]}`；验证档: %s'
      % ('**真跑档**（apply --write 默认，verify 重跑 + 语义比对，防弹窗守卫生效）'
         if not args.fast else '静态档（apply --verify-fast，不执行语料脚本）'))
    a('- 解释器: %s' % sys.executable)
    a('- 耗时: %.0fs' % elapsed)
    a('')
    a('## 0b 沙箱边界（本次运行的已知边界，如实说明）')
    a('')
    a('- 沙箱判定"是否在跑用户脚本"依据 **`argv[0]` 是否落在 `MPLTWEAK_SANDBOX_ROOT` 内**；')
    a('  **真跑链路**（`apply` → `python -m mpltweak.verify …` → 脚本）里那个进程的')
    a('  `argv[0]` 是 mpltweak 模块路径 ⇒ 落在 **harness 侧，只有"文件层"生效**。')
    a('- 因此：**越界读写删在真跑链路里实测被拦住**（试点证据 §4c：'
      '`PermissionError: 沙箱：open(write) 被禁止`，磁盘无越界文件）；')
    a('  但 **socket/subprocess 层在链路里不生效** ⇒ 本版靠**静态预筛**兜住：')
    a('  含联网/起子进程调用的脚本一律 `na_skip`（`na_reason=net_risk`）**不真跑**，')
    a('  所以本次全量**在构造上不会碰到网络**。')
    a('- B 档口径：只调 `plt.show()` / `fig.show()` 的脚本**照常真跑** —— `MPLBACKEND=Agg`')
    a('  下 show() 是无害 no-op，且沙箱还硬钉 Agg、拦 GUI 库导入与 `matplotlib.use(<GUI后端>)`'
      '（t15 D1/D2 与本次 §4c/B 档实测）。真正会开窗的（A 档）静态排除、不真跑。')
    a('- 精细判定规则（"argv 里出现游乐场内的脚本路径也算脚本进程"，需避免 CLI 自身被')
    a('  误判而拦死 verify 的 spawn）列入 **0.1.11 候选**。')
    a('')
    a('## 1 判定分布（判据不适用 vs 真违约）')
    a('| 判定 | 份数 | 说明 |')
    a('|---|---:|---|')
    a('| **pass** | %d | 适用四律全部通过 |' % vcount.get('pass', 0))
    a('| **violation** | %d | **真违约**（见 §3） |' % vcount.get('violation', 0))
    a('| **na_gui** | %d | 判据不适用：脚本要开窗/需要 GUI（其中静态防弹窗未真跑 '
      '%d 份，运行时禁 GUI 层拦截见 §4b） |'
      % (vcount.get('na_gui', 0), extra.get('gui_static', 0)))
    a('| （对照）B 档 plt.show() | %d | **已真跑**：只调 `plt.show()` 的脚本在 '
      '`MPLBACKEND=Agg` 下无害（沙箱还硬钉 Agg + 拦 GUI 库导入），计入上面各档 |'
      % sum(1 for r in results if r.get('gui_tier') == 'B'))
    a('| **na_stdin** | %d | 判据不适用：脚本等人输入（沙箱无人值守层让 '
      'input() 直接 EOF） |' % vcount.get('na_stdin', 0))
    a('| na_skip | %d | 判据不适用：语料侧语法错/编码/超限/非 py（其中联网/子进程'
      '静态预筛 %d 份，见 §4d） |'
      % (vcount.get('na_skip', 0),
         sum(1 for r in na_skip if r.get('na_reason') == 'net_risk')))
    a('| na_reject | %d | 判据不适用：引擎拒写（no_fig/no_axes/fail），字节未动 |'
      % vcount.get('na_reject', 0))
    a('| na_timeout | %d | 判据不适用：worker 超时被杀（无法判定） |'
      % vcount.get('na_timeout', 0))
    a('| na_error | %d | 判据不适用：worker 异常（引擎抛错） |'
      % vcount.get('na_error', 0))
    a('')
    a('口径说明（对齐 _numbers_e28a608.md）：「幂等/可逆」只对**成功写回**批'
      '（reason ∈ ok/best_effort/no_change）断言；「语法守恒/只动目标」对**任何**'
      '结果成立（拒写批要求字节不动）。退块（best_effort + block）是引擎的正常'
      '降级路径，不是违约。')
    a('')
    a('## 2 四条铁律（成功写回批 %d 份）' % len(succ))
    a('| 铁律 | 通过 | 说明 |')
    a('|---|---:|---|')
    a('| ① 幂等 | %d/%d | 二次 apply 字节不变 + reason=no_change；另 %d 份判定层边界'
      '（字节稳定但 reason≠no_change，见 §2b） |'
      % (law_ok['idempotent'], len(succ), idem_boundary))
    a('| ② 可逆 | %d/%d | revert --write 逐字节还原（无备份时 rc=2 且不动） |'
      % (law_ok['reversible'], len(succ)))
    a('| ③ 语法守恒 | %d/%d | 写回后 ast.parse（**任何**结果都成立，含拒写批） |'
      % (law_ok['syntax'], len(succ)))
    a('| ④ 只动目标 | %d/%d | 落地字段值以字面量出现 + diff 外科手术（**任何**结果） |'
      % (law_ok['target_only'], len(succ)))
    a('| 承诺违约 | %d/%d | 声称写回但字节未变（应恒为 0） |'
      % (promise_ok, len(succ)))
    a('| 拒写不改字节 | %s | 拒写批脚本一个字节都不动 |'
      % ('%d/%d' % (no_tamper_ok, no_tamper_total) if no_tamper_total
         else 'n/a（本轮 na_reject = 0，此判据没有对象）'))
    a('')
    a('### 2b 幂等判定层边界（字节稳定但 reason≠no_change，%d 份，**已知边界**）'
      % idem_boundary)
    a('按 _numbers_e28a608.md m5 口径：二次 apply 后**字节不变**即字节级幂等成立；'
      '仅当引擎因永久 pending 字段（如轴变量证不出）反复报 best_effort 时 reason '
      '≠ no_change，属"判定层非幂等"的已知边界，**不计为违约**。明细见 results.jsonl '
      '的 idem_boundary 字段。')
    a('')
    if viol:
        a('## 3 真违约样例（%d 份）' % len(viol))
        a('| 脚本路径 | 症状 |')
        a('|---|---|')
        for r in viol:
            a('| `%s` | %s |' % (r.get('path', '?'), r.get('detail', '?')))
        a('')
    else:
        a('## 3 真违约样例')
        a('**0 份**（本子集内承诺违约 0 / 幂等 0 / 可逆 0 / 语法 0 / 只动目标 0）。')
        a('')
    if na_skip:
        a('## 4 判据不适用明细（na_skip %d）' % len(na_skip))
        a('| 脚本路径 | 原因 |')
        a('|---|---|')
        for r in na_skip[:40]:
            a('| `%s` | %s |' % (r.get('path', '?'), r.get('detail', '?')))
        if len(na_skip) > 40:
            a('| … | 共 %d 份，其余见 results.jsonl |' % len(na_skip))
        a('')
    if na_reject:
        a('## 5 拒写批样例（na_reject %d，均为判据不适用）' % len(na_reject))
        a('| 脚本路径 | 原因 |')
        a('|---|---|')
        for r in na_reject[:20]:
            a('| `%s` | %s |' % (r.get('path', '?'), r.get('detail', '?')))
        if len(na_reject) > 20:
            a('| … | 共 %d 份，其余见 results.jsonl |' % len(na_reject))
        a('')
    if na_timeout:
        a('## 6 超时明细（na_timeout %d）' % len(na_timeout))
        for r in na_timeout:
            a('- `%s`' % r.get('path', '?'))
        a('')
    if na_error:
        a('## 7 worker 异常明细（na_error %d）' % len(na_error))
        for r in na_error:
            a('- `%s`: %s' % (r.get('path', '?'), r.get('detail', '?')))
        a('')
    if na_gui or na_stdin:
        a('## 4b 判定层拦截明细（na_gui %d / na_stdin %d）'
          % (len(na_gui), len(na_stdin)))
        a('| 脚本路径 | 判定 | 守卫证据（截断） |')
        a('|---|---|---|')
        for r in (na_gui + na_stdin)[:30]:
            a('| `%s` | %s | %s |'
              % (r.get('path', '?'), r.get('verdict'),
                 str(r.get('detail', '')).replace('|', '/')[:120]))
        if len(na_gui) + len(na_stdin) > 30:
            a('| … | | 共 %d 份，其余见 results.jsonl |'
              % (len(na_gui) + len(na_stdin)))
        a('')
    _net_skip = [r for r in na_skip if r.get('na_reason') == 'net_risk']
    if _net_skip:
        a('## 4d 联网/子进程静态预筛（na_skip %d 份，**不真跑**）' % len(_net_skip))
        a('')
        a('原因：试点实测发现**真跑链路里 socket/subprocess 那一层没有生效** ——')
        a('沙箱判断"是不是在跑用户脚本"看 `argv[0]` 是否为游乐场内的真实文件，而')
        a('verify 子进程的 argv[0] 是 mpltweak 模块路径（`python -m mpltweak.verify`），')
        a('于是被当作 harness 进程，只剩文件层（文件层对 harness 也生效，所以')
        a('**越界读写删依然是拦得住的**，试点证据见 §4c）。按"指望不上的层就别给它')
        a('机会"的口径，这类脚本静态预筛、不真跑。')
        a('')
        a('> 边界（如实说明）：① 沙箱"是否脚本进程"以 `argv[0]` 判定，**真跑链路'
          '（`python -m mpltweak.verify …`）落在 harness 侧，只有文件层生效**；')
        a('> ② 联网/子进程层在本版靠**静态预筛**兜住（命中即 `na_skip`，不真跑）；')
        a('> ③ 精细设计判定规则（例如"argv 里出现游乐场内的脚本路径也算脚本进程"，'
          '需同时避免 CLI 自身被误判而拦死 verify 的 spawn）列入 **0.1.11 候选**。')
        a('')
        a('| 脚本路径 | 原因 |')
        a('|---|---|')
        for r in _net_skip[:30]:
            a('| `%s` | %s |' % (r.get('path', '?'),
                                 str(r.get('detail', '')).replace('|', '/')[:120]))
        if len(_net_skip) > 30:
            a('| … | 共 %d 份，其余见 results.jsonl |' % len(_net_skip))
        a('')
    if extra.get('pilot_evidence'):
        a('## 4c 试点：沙箱逐类拦截实测证据（脚本越界即被拦）')
        a('')
        a('```')
        for _ln in extra['pilot_evidence']:
            a(_ln)
        a('```')
        a('')
    a('## 9 src 溯源（本次运行时的源码状态）')
    _pv = extra.get('provenance') or {}
    a('- git HEAD: `%s`；工作区未提交改动 %s 个文件（提交闸门归船长，'
      '本次运行就在这个工作区状态下进行）'
      % (_pv.get('git_head') or '?', _pv.get('git_dirty')))
    for _k in ('apply.py', 'writeback.py', 'verify.py', 'messages.py',
               'preflight.py', 'params.py', 'revert.py', 'launch.py', 'cli.py'):
        a('- `src/mpltweak/%s` md5 `%s`' % (_k, _pv.get(_k) or '?'))
    a('')
    a('## 10 复现')
    a('```')
    a('set MPLBACKEND=Agg')
    a('set PYTHONPATH=%s\\src' % ROOT)
    a('cd %s' % ROOT)
    if extra.get('pool_mode'):
        a(':: 试点：前 48 份 + 沙箱逐类拦截证据（先跑这个）')
        a('%s tests/test_corpus_invariants.py --pilot 48' % sys.executable)
        a(':: 全量 1,837 份（随时可中断；再跑同一条命令自动续跑，不重不漏）')
        a('%s tests/test_corpus_invariants.py --resume' % sys.executable)
    else:
        a('%s tests/test_corpus_invariants.py --limit %d --seed %d'
          % (sys.executable, len(chosen), args.seed))
    a('```')
    a('')
    if extra.get('observations'):
        _o = extra['observations']
        a('## 11 运行观察（内存与并发；外部采样）')
        a('')
        a('%s' % _o.get('note', ''))
        a('')
        a('| 阶段 | 已完成份数 | 进程数 | 进程树总内存 |')
        a('|---|---:|---:|---:|')
        for _s in _o.get('samples') or []:
            a('| %s | %s | %s | %s MB |'
              % (_s.get('phase'), _s.get('done'), _s.get('procs'),
                 _s.get('total_mb')))
        a('')
        a('- **动作线**：进程树总内存 ~%s MB ⇒ 干净停手（按自己的路径特征精确匹配，'
          '未触碰他人进程）→ 降并发续跑。%s' % (_o.get('threshold_mb'),
                                                 _o.get('action', '')))
        a('- **成因**：%s' % _o.get('cause', ''))
        for _ln in _o.get('timing') or []:
            a('- %s' % _ln)
        a('- **下次跑的调参建议**：%s' % _o.get('next_run_hint', ''))
        if _o.get('other'):
            a('- 其它：%s' % _o['other'])
        a('')
    a('## 12 本轮的口径与给下一步的提示')
    a('')
    a('- **本轮是"修复前基线"**：违约 3 例（同一签名）如实记录，**未删除、未改标**；'
      '最小复现见交付说明（四时刻哈希 → 块模式重试循环里的守卫基准缺陷）。')
    a('- **修复后重跑必须换新目录、禁用 `--resume`**：例如 '
      '`--out <workspace>/_review/corpus_invariants_t23_postfix`（**不要**加 '
      '`--resume`）—— `RUN_META` 的池指纹**不跟踪 src 版本**，续跑会把"修复前/'
      '修复后"的结果混进同一份统计，数字就废了。')
    _base = (extra.get('observations') or {}).get('baseline_counts')
    if _base:
        a('')
        a('## 13 与 t23（修复前基线）的逐桶对照')
        a('')
        a('> 两轮口径完全一致（同一候选池 / 同一分层分母 / 同一四律统计方式 / '
          '显式 `MPLBACKEND=Agg` / 零 GUI 对象），**唯一差异 = 本轮用的是 t27 '
          '修复后的 `writeback.py`**。')
        a('')
        a('| 判定 | t23（修复前） | 本轮 postfix（修复后） | 差 |')
        a('|---|---:|---:|---:|')
        _tot_b = 0
        _tot_c = 0
        for _k in ('pass', 'violation', 'na_gui', 'na_stdin', 'na_skip',
                   'na_timeout', 'na_reject', 'na_error'):
            _b = int(_base.get(_k, 0))
            _c = int(vcount.get(_k, 0))
            _tot_b += _b
            _tot_c += _c
            a('| **%s** | %d | %d | %+d |' % (_k, _b, _c, _c - _b))
        a('| 合计 | %d | %d | %+d |' % (_tot_b, _tot_c, _tot_c - _tot_b))
        a('')
        for _ln in (extra.get('observations') or {}).get('baseline', []):
            a('- %s' % _ln)
    a('')
    a('产出: `results.jsonl`（逐份结果）、`results/s_%03d.json`（逐份原子落盘，'
      '续跑依据）、`RUN_META.json`（池指纹对账）+ 本文件。')
    return '\n'.join(lines) + '\n'


# ---------------------------------------------------------------------------
# t23 试点：沙箱逐类拦截**实测**（不是"读代码觉得应该拦得住"，而是真跑一遍捣蛋脚本）
# ---------------------------------------------------------------------------
def _outside_dir(sb_root):
    """一个**确定在游乐场外**的目录（越界金丝雀的目标）。"""
    cand = os.path.join(_TMPROOT, 'outside_canary')
    if os.path.abspath(cand).startswith(os.path.abspath(sb_root) + os.sep):
        cand = os.path.join(tempfile.gettempdir(),
                            'mpltweak_outside_%d' % os.getpid())
    os.makedirs(cand, exist_ok=True)
    return cand


def _pilot_canaries(workdir, sandbox_root):
    """四类"越界"金丝雀 + 不误拦对照 + **真跑链路**里的越界/联网实测。

    返回 ``(证据行列表, 是否全部符合预期)``。

    ① 直接跑（`python canary.py`）：`argv[0]` 在游乐场内 → 拿到**完整**守卫
       （文件 + 子进程 + socket），这是 t9 设计里"跑用户脚本的进程"。
    ② 走真跑链路（`apply --write` → verify 子进程 → 脚本）：测的是**全量真跑真正
       执行语料脚本的那条路**上，越界写/联网到底会不会被拦 —— 脚本把结果写进游乐场
       内的日志文件（合法路径），由本函数读回，所以拿到的是"实际发生了什么"而不是推测。
    """
    outside = _outside_dir(sandbox_root)
    victim = os.path.join(outside, 'victim.txt')
    with open(victim, 'w', encoding='utf-8') as f:
        f.write('语料目录以外的真实文件（守卫应保住它）\n')
    evil = os.path.join(outside, 'evil.txt')
    evil_dir = os.path.join(outside, 'evil_dir')
    canaries = (
        ('越界写', '写 playground 外的文件',
         "import os\nopen(%r, 'w').write('x')\n" % evil, evil, 'absent'),
        ('越界删', '删 playground 外的文件',
         "import os\nos.remove(%r)\n" % victim, victim, 'present'),
        ('越界建目录', '在 playground 外建目录',
         "import os\nos.makedirs(%r)\n" % evil_dir, evil_dir, 'absent'),
        ('联网', '向外部地址发起连接（RFC5737 文档网段，不会真连到谁）',
         "import socket\nsocket.create_connection(('192.0.2.1', 80), 3)\n",
         None, None),
        ('开窗', '导入 GUI 库（禁 GUI 层）',
         "import tkinter\n", None, None),
    )
    ev = []
    all_ok = True
    env = _cli_env(workdir)
    for label, what, body, probe, expect in canaries:
        spath = os.path.join(workdir, 'canary_%s.py' % label)
        with open(spath, 'w', encoding='utf-8') as f:
            f.write(body)
        try:
            r = subprocess.run([sys.executable, spath], capture_output=True,
                               timeout=90, env=env, cwd=workdir)
            rc = r.returncode
            out = _decode_out(r.stdout + r.stderr)
        except subprocess.TimeoutExpired:
            rc, out = -9, '（超时 —— 可能真的放行了）'
        # 拦截判据 = 非零退出 + 守卫自己说的话（沙箱文案 / BLOCK 日志 / PermissionError）
        blocked = (rc != 0) and ('沙箱' in out or 'BLOCK' in out
                                 or 'PermissionError' in out)
        if probe and expect == 'absent':
            blocked = blocked and not os.path.exists(probe)
        elif probe and expect == 'present':
            blocked = blocked and os.path.exists(probe)
        _line = ''
        for _l in out.splitlines():
            if '沙箱' in _l or 'BLOCK' in _l or 'PermissionError' in _l:
                _line = _l.strip()
                break
        if not _line:
            _line = (out.strip().splitlines() or ['（无输出）'])[-1].strip()
        ev.append('%-5s %-9s rc=%-3s %s | %s'
                  % (label, 'BLOCKED' if blocked else '**未拦住**', rc, what,
                     _line[:160]))
        all_ok = all_ok and blocked

    # ---- 不误拦对照：游乐场内的正常绘图必须跑得通 ----
    ok_script = os.path.join(workdir, 'canary_正常绘图.py')
    with open(ok_script, 'w', encoding='utf-8') as f:
        f.write("import matplotlib\nmatplotlib.use('Agg')\n"
                "import matplotlib.pyplot as plt\n"
                "fig, ax = plt.subplots()\nax.plot([1, 2, 3])\n"
                "fig.savefig('ok.png')\nprint('NORMAL-OK')\n")
    try:
        r = subprocess.run([sys.executable, ok_script], capture_output=True,
                           timeout=180, env=env, cwd=workdir)
        rc, out = r.returncode, _decode_out(r.stdout + r.stderr)
    except subprocess.TimeoutExpired:
        rc, out = -9, '（超时）'
    no_false_block = (rc == 0 and 'NORMAL-OK' in out)
    ev.append('%-5s %-9s rc=%-3s %s | %s'
              % ('正常绘图', 'NOT-BLOCKED' if no_false_block else '**误拦**', rc,
                 '游乐场内 savefig（不该被拦）',
                 (out.strip().splitlines() or ['（无输出）'])[-1].strip()[:120]))
    all_ok = all_ok and no_false_block

    # ---- 真跑链路实测：apply → verify → 脚本（全量真跑真正执行脚本的那条路）----
    try:
        pdir = os.path.join(workdir, 'pipe_case')
        os.makedirs(pdir, exist_ok=True)
        plog = os.path.join(pdir, 'pipeline_probe.json')
        pscript = os.path.join(pdir, 'pipe_canary.py')
        with open(pscript, 'w', encoding='utf-8') as f:
            f.write(
                "import json, os, socket\n"
                "import matplotlib\nmatplotlib.use('Agg')\n"
                "import matplotlib.pyplot as plt\n"
                "fig, ax = plt.subplots(figsize=(6, 4))\nax.plot([1, 2, 3])\n"
                "fig.savefig('pipe_out.png')\n"          # 游乐场内：合法
                "_log = {'write_blocked': False, 'net_blocked': False,\n"
                "        'err_write': '', 'err_net': ''}\n"
                "try:\n"
                "    open(%r, 'w').write('x')\n"
                "except BaseException as e:\n"
                "    _log['write_blocked'] = True\n"
                "    _log['err_write'] = '%%s: %%s' %% (type(e).__name__, e)\n"
                "try:\n"
                "    socket.create_connection(('192.0.2.1', 80), 3)\n"
                "except BaseException as e:\n"
                "    _log['net_blocked'] = ('沙箱' in str(e)\n"
                "                            or type(e).__name__ == 'PermissionError')\n"
                "    _log['err_net'] = '%%s: %%s' %% (type(e).__name__, e)\n"
                "open(%r, 'w', encoding='utf-8').write(\n"
                "    json.dumps(_log, ensure_ascii=False))\n"
                % (evil, plog))
        pjson = os.path.join(pdir, 'pipe_canary.json')
        with open(pjson, 'w', encoding='utf-8') as f:
            json.dump({'version': 4, 'script': 'pipe_canary.py', 'fig_index': 0,
                       'figsize_in': [8.5, 5.5]}, f)
        _j, _rc, _out = _cli_apply(pscript, pjson, pdir, fast=False)
        _log = {}
        try:
            with open(plog, 'r', encoding='utf-8') as f:
                _log = json.load(f)
        except (OSError, ValueError):
            _log = {}
        _wblocked = bool(_log.get('write_blocked'))
        ev.append('%-5s %-9s rc=%-3s %s | %s'
                  % ('链路越界写',
                     'BLOCKED' if _wblocked else '**未拦住**', _rc,
                     'apply→verify→脚本 里写 playground 外文件',
                     str(_log.get('err_write') or '（脚本没留下记录）')[:150]))
        ev.append('%-5s %-9s rc=%-3s %s | %s'
                  % ('链路联网',
                     'BLOCKED' if _log.get('net_blocked') else '未拦住',
                     _rc, 'apply→verify→脚本 里连外网',
                     str(_log.get('err_net') or '（脚本没留下记录）')[:150]))
        ev.append('%-5s %-9s rc=%-3s %s | 磁盘上 %s'
                  % ('链路副作用', 'OK' if not os.path.exists(evil) else '**越界成功**',
                     _rc, '游乐场外的 evil.txt 是否出现',
                     '未出现（安全）' if not os.path.exists(evil) else '出现了（异常）'))
        # 这条是**硬指标**：真跑链路里脚本绝不能在游乐场外留下文件。
        all_ok = all_ok and _wblocked and not os.path.exists(evil)
    except Exception as _e:                                          # noqa: BLE001
        ev.append('链路实测   **未能完成** | %s: %s'
                  % (type(_e).__name__, str(_e)[:150]))
        all_ok = False
    # ---- B 档实测：只调 plt.show() 的脚本在真跑链路里必须"跑得通、不弹窗" ----
    # 这条是 A/B 分档口径的**依据**：把 plt.show() 从"危险"里摘出来，前提是它在
    # Agg + 沙箱下确实无害且能正常验证通过（不能只靠"读文档觉得没事"）。
    try:
        bdir = os.path.join(workdir, 'tier_b_case')
        os.makedirs(bdir, exist_ok=True)
        bscript = os.path.join(bdir, 'tier_b.py')
        with open(bscript, 'w', encoding='utf-8') as f:
            f.write("import matplotlib\n"
                    "import matplotlib.pyplot as plt\n"
                    "fig, ax = plt.subplots(figsize=(6, 4))\n"
                    "ax.plot([1, 2, 3])\nfig.savefig('tier_b.png')\n"
                    "plt.show()\n")
        bjson = os.path.join(bdir, 'tier_b.json')
        with open(bjson, 'w', encoding='utf-8') as f:
            json.dump({'version': 4, 'script': 'tier_b.py', 'fig_index': 0,
                       'figsize_in': [8.5, 5.5]}, f)
        _bj, _brc, _bout = _cli_apply(bscript, bjson, bdir, fast=False)
        _bwin = any(k in (_bout or '') for k in ('禁止 GUI', '只允许无窗口后端'))
        _b_ok = (_brc == 0 and not _bwin
                 and (_bj or {}).get('reason') in ('ok', 'no_change',
                                                   'best_effort'))
        ev.append('%-13s %-9s rc=%-3s %s | %s'
                  % ('B档 plt.show()', 'RUNS-FINE' if _b_ok else '**异常**', _brc,
                     '只有 plt.show()（Agg 下应无害）→ 照常真跑',
                     ('reason=%s verified=%s' % ((_bj or {}).get('reason'),
                                                 (_bj or {}).get('verified')))
                     if _bj else (_bout or '')[-120:]))
        all_ok = all_ok and _b_ok
    except Exception as _e:                                          # noqa: BLE001
        ev.append('B档 plt.show() **未能完成** | %s: %s'
                  % (type(_e).__name__, str(_e)[:120]))
        all_ok = False
    return ev, all_ok


# ---------------------------------------------------------------------------
def _dispatch(argv):
    if len(argv) >= 2 and argv[1] == '--worker':
        rel, corpus_root, workdir, result_file = argv[2:6]
        fast = '--fast' in argv[6:]
        try:
            _worker(rel, corpus_root, workdir, result_file, fast=fast)
        except BaseException as e:                 # noqa: BLE001 - 兜底：崩溃也要留结果
            _write_json(result_file, {
                'path': rel, 'verdict': 'na_error',
                'detail': 'worker 崩溃: %s: %s' % (type(e).__name__,
                                                   str(e)[:300])})
        return 0
    ap = argparse.ArgumentParser(prog='test_corpus_invariants.py')
    ap.add_argument('--limit', type=int, default=None,
                    help='只跑确定序的前 N 份（旧路径默认 240 抽样）')
    ap.add_argument('--jobs', type=int, default=4, help='并发 worker 数（默认 4）')
    ap.add_argument('--seed', type=int, default=20261013)
    ap.add_argument('--corpus', default=None)
    ap.add_argument('--out', default=None)
    ap.add_argument('--pool', default=None,
                    help='全量候选池（t7 的 filter.jsonl，只取 both_ok=true）；'
                         '默认 <workspace>/_review/corpus_filter_t7/filter.jsonl')
    ap.add_argument('--all', action='store_true',
                    help='池内全量（不按来源分层抽样）')
    ap.add_argument('--resume', action='store_true',
                    help='**中断续跑**：跳过已有结果文件的脚本；结果逐份原子落盘、'
                         '顺序确定，所以续跑不重不漏')
    ap.add_argument('--pilot', type=int, nargs='?', const=48, default=None,
                    help='试点：先实测沙箱四类拦截（越界写/删/建目录、联网、开窗）'
                         '+ 不误拦对照，再跑前 N 份（默认 48）')
    ap.add_argument('--fast', action='store_true',
                    help='静态档：apply --verify-fast（不执行语料脚本）。'
                         '默认=真跑档（完整验证，0.1.10 主口径），配合防弹窗守卫')
    args = ap.parse_args(argv[1:])
    if args.pilot is not None:
        # 试点 = 池模式 + 拦截实测 + 只跑前 N 份
        args.limit = args.limit or args.pilot
        args.pilot_phase = True
    else:
        args.pilot_phase = False
    if not args.pool and not args.all and not args.resume \
            and not args.pilot_phase:
        args.limit = args.limit or 240          # 旧路径（manifest 分层抽样）默认值
    return _scan_main(args)


if __name__ == '__main__':
    sys.exit(_dispatch(sys.argv))
