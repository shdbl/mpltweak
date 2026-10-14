# -*- coding: utf-8 -*-
"""记号纪律检查器（**只报告，不修复**）—— tools/notation_discipline.py

口径来源
========
* ``MAINTENANCE.md:336``（§8.5）：「字体缺字形会显示成方块：微软雅黑没有 ``✓``(U+2713)、
  ``↺``(U+21BA) → 用 ``√``(U+221A)、``×``(U+00D7)」——**禁用** U+2713 / U+21BA，
  ``√`` / ``×`` 是**受准**的字形替换。
* ``CONTRIBUTING.md:71-74``：「Every message template must be GBK-encodable」——中国 Windows 上
  管道里的 stdout 是 cp936 + ``errors='strict'``，一个 ``✓`` 就能把「写回成功」变成
  ``rc=1 + UnicodeEncodeError``，**而且发生在文件已经写完之后**。
* 记号约定：终端状态标记一律 ASCII ``[OK]`` / ``[X]``（``MAINTENANCE.md:665`` 的措辞同源）。

与既有棘轮的分工（**不重复**）
============================
``tests/test_hygiene.py`` 已有两条：
1. ``test_messages_are_gbk_encodable``（:176）——只查 ``src/mpltweak/messages.py`` 的
   ``MESSAGES`` 模板表；
2. ``test_test_suites_cannot_crash_gbk_console``（:536）——只查 ``tests/test_*.py``
   「有没有装控制台兜底」；
本检查器补的是**第三条缝**：``src/`` 与 ``tools/`` 里**直接 ``print`` 出去的字符串**
（既不在消息表里、也不属于测试套件），外加记号字形纪律（前两条都不管记号）。

扫描面：哪些文件**不**扫（t98 补）
================================
``iter_py()`` 的跳过规则 —— 刻意**很窄**，只认仓库既有的临时/探针命名约定：

* **目录**：``SKIP_DIRS`` 里的（``.git`` / ``__pycache__`` / ``.pytest_cache`` /
  ``node_modules`` / ``.eggs`` …）+ 名字以 ``.tmp`` / ``tmp_`` 开头的；
* **文件**（t98 新增）：名字以 ``.tmp`` / ``tmp_`` / ``_jsonenc`` **开头**，
  或以 ``_probe.py`` **结尾**的。

**为什么文件级也要跳**：别的测试会往仓库里写"用完就删"的探针 ``.py``
（``tests/test_agent_api.py:309`` → ``tests/.tmp_dpi.py``、``:445`` → ``tests/_jsonenc9.py``）；
**并发跑两条臂**（或与别的 agent 的跑重叠）时，A 臂的探针文件正被 B 臂的本次扫描看到
⇒ **假红**（t87 那次"两臂都红、分离跑都过"就有这个成分）。原来只跳**目录**、文件不跳。

⚠️ **给新套件/新脚本起名时不要用上面这些临时/探针前缀** —— 否则它**不会被本检查器扫到**
（等于悄悄退出记号纪律）。这条登记规则同时写在 ``CONTRIBUTING.md`` 的套件登记一节。
反过来：**绝不**按"未跟踪 / 新文件"整片跳过 —— 那会把刚加的正式套件也漏掉。

判据：什么算「终端输出字符串」
============================
**核心原则：不靠函数名白名单**。仓库里有叫 ``check`` / ``fail`` / ``error`` 的辅助函数
并不打印，按名字猜必然误报 —— 而误报会让检查器废掉。改为**按函数体判定**：

* 种子（sink）：``print(...)``、``sys.stdout.write(...)``、``sys.stderr.write(...)``；
* **打印助手**：某函数体内（任意深度）调用了已知 sink 或已知打印助手 → 该函数名加入打印者
  集合（同文件**不动点**迭代）。``_p()`` / ``check()`` 这类因此被**自动发现**，不是被列举；
* **输出字符串** = 所有「sink 或打印助手」调用实参子树里的字符串字面量
  （f-string 常量段、``%`` / ``.format()`` / ``+`` 拼接里的字面量由 AST 遍历天然覆盖）；
* **消息表**：模块级 ``XXX_MESSAGES = {...}`` 里的字符串取值（模板必然上终端）；
* **一跳跨函数**：被当作打印实参调用的函数（``print(hint())``）或打印者本身，其
  ``return "字面量"`` 也算输出字符串；若它还 return 另一个函数调用，继续传导（不动点）。

**明确不判**（避免误报）：
* 注释、docstring、类型注解里的任何字符（中文注释当然不是 ASCII）；
* ``raise XxxError("…")`` 的异常消息 —— CPython 打 traceback 用 ``backslashreplace``，
  不会把进程打崩，属另一个判据域；
* 字节串（``b"…"``）与 ``sys.stdout.buffer.write``（绕开文本编码层）。

规则
====
* **R1 mark**：输出串里出现**非 ASCII 记号字符**，且该字符不在受准名单（``√`` / ``×``）
  —— 一律应写成 ASCII ``[OK]`` / ``[X]``。
* **R2 gbk**：输出串里有任何 **cp936(cp936) 编不出**的字符 —— 在中文 Windows 上就是
  ``UnicodeEncodeError`` → ``rc=1``。
* 一个字符**可以同时**犯两条（``✓`` U+2713 既是记号、又编不出 GBK）⇒ 两条都报，见
  ``rules_for()``；只报一条会让「换个记号」掩盖掉编码问题。

分级（决定判不判红）
==================
* ``error``（**判红**）：``src/mpltweak/`` 里的命中 —— 那是**用户**的控制台；
* ``exempt``（只报告，属**刻意例外**）：文件自带控制台兜底（含 ``reconfigure(errors=``，
  与 ``tests/test_hygiene.py`` 的 ``_CONSOLE_MARK`` **同一判据**）⇒ 输出已被清洗；
  或该命中已被 ``NOTATION_BASELINE`` 冻结（**只拦新增**，与仓库其它棘轮同语义）；
* 其余（``tests/`` / ``tools/`` 里既没兜底也没登记）同样算 ``error``。

用法
====
    python tools/notation_discipline.py              # 人读报告（有 error 则 rc=1）
    python tools/notation_discipline.py --markdown   # 交派修用的 markdown 清单
    python tools/notation_discipline.py --json
    python tools/notation_discipline.py --root DIR   # 扫别的目录（自测用）
"""
import argparse
import ast
import io
import json
import os
import sys
import unicodedata

ROOT_DEFAULT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SKIP_DIRS = {'.git', '__pycache__', '.pytest_cache', 'node_modules', '.eggs',
             'build', 'dist', '.hypothesis', '.idea', '.vscode', '.tweak_params'}

# 与 tests/test_hygiene.py 的 _CONSOLE_MARK **同一个字符串**（改一处必须改两处）
CONSOLE_FALLBACK_MARK = 'reconfigure(errors='

# MAINTENANCE §8.5 明确批准的字形替换（**受准**，不算违规）
SANCTIONED_MARKS = {
    '\u221a': '\u221a U+221A SQUARE ROOT — MAINTENANCE §8.5 批准的替换字形',
    '\u00d7': '\u00d7 U+00D7 MULTIPLICATION SIGN — MAINTENANCE §8.5 批准的替换字形',
}

# MAINTENANCE §8.5 点名的禁用字形（微软雅黑无此字形 → 显示成方块）
BANNED_MARKS = {
    '\u2713': '\u2713 U+2713 CHECK MARK',
    '\u21ba': '\u21ba U+21BA ANTICLOCKWISE OPEN CIRCLE ARROW',
}

# 其它「非 ASCII 记号族」：不是状态标记就该是普通文字，出现在 `[OK]` 该在的位置一律可疑。
# 只收**记号语义**的字符；汉字/全角标点/中文引号不在内（它们本来就该出现在中文文案里）。
MARK_FAMILY = {
    '\u2714': 'U+2714 HEAVY CHECK MARK',
    '\u2717': 'U+2717 BALLOT X',
    '\u2718': 'U+2718 HEAVY BALLOT X',
    '\u2705': 'U+2705 WHITE HEAVY CHECK MARK',
    '\u274c': 'U+274C CROSS MARK',
    '\u26a0': 'U+26A0 WARNING SIGN',
    '\u2b50': 'U+2B50 WHITE MEDIUM STAR',
    '\u2728': 'U+2728 SPARKLES',
    '\u23ce': 'U+23CE RETURN SYMBOL',
    '\u270d': 'U+270D WRITING HAND',
    '\u2b06': 'U+2B06 UPWARDS BLACK ARROW',
    '\u2b07': 'U+2B07 DOWNWARDS BLACK ARROW',
    '\u25cf': 'U+25CF BLACK CIRCLE',
    '\u25cb': 'U+25CB WHITE CIRCLE',
    '\u25b6': 'U+25B6 BLACK RIGHT-POINTING TRIANGLE',
    '\u2605': 'U+2605 BLACK STAR',
    '\u2606': 'U+2606 WHITE STAR',
    '\u2757': 'U+2757 HEAVY EXCLAMATION MARK SYMBOL',
    '\u2753': 'U+2753 BLACK QUESTION MARK ORNAMENT',
    '\U0001f44d': 'U+1F44D THUMBS UP SIGN',
    '\U0001f44e': 'U+1F44E THUMBS DOWN SIGN',
    '\U0001f680': 'U+1F680 ROCKET',
}
MARK_FAMILY.update(BANNED_MARKS)

# ---------------------------------------------------------------------------
# 冻结基线（**只拦新增**，与 tests/test_hygiene.py:BASELINE /
# tests/test_docs.py:AMBIG_BASELINE 同语义）。
#
# 键 = (仓库相对路径, 命中字符, 该行 strip 后的原文[:120])。**刻意不用行号** —— 行号会漂。
# 语义：
#   * 出现基线之外的命中 ⇒ 判红（新增即红）；
#   * 基线里已不复存在的条目 ⇒ 打印「请从基线里删掉」，**不判红**（与 hygiene 同款）；
#   * 修好一处就删一条 —— 本检查器**不会**替你去改代码。
#
# 当前仓库跑出来是**空的**：所有 U+2713 等命中都落在注释 / docstring 里，
# 而注释/docstring **不在**判据内（见文件头「明确不判」）⇒ 没有需要冻结的东西。
# ---------------------------------------------------------------------------
NOTATION_BASELINE = set()


# ---------------------------------------------------------------- AST 工具
def _callee_name(node):
    """调用的被调者名字：``print`` / ``_p`` / ``sys.stdout.write``；取不到 → None。"""
    if not isinstance(node, ast.Call):
        return None
    f = node.func
    if isinstance(f, ast.Name):
        return f.id
    if isinstance(f, ast.Attribute):
        if (f.attr in ('write', 'flush') and isinstance(f.value, ast.Attribute)
                and f.value.attr in ('stdout', 'stderr')
                and isinstance(f.value.value, ast.Name)
                and f.value.value.id == 'sys'):
            return 'sys.%s.%s' % (f.value.attr, f.attr)
    return None


SINK_NAMES = {'print', 'sys.stdout.write', 'sys.stderr.write'}


def _calls_any(node, names):
    """子树里是否有调用被调者 ∈ names。"""
    for n in ast.walk(node):
        if _callee_name(n) in names:
            return True
    return False


def _printers(tree):
    """按**函数体**发现打印者（不动点）。不靠名字白名单。"""
    funcs = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            funcs.setdefault(node.name, node)
    printers = set(SINK_NAMES)
    changed = True
    while changed:
        changed = False
        for name, fn in funcs.items():
            if name in printers:
                continue
            if _calls_any(fn, printers):
                printers.add(name)
                changed = True
    return printers, funcs


def _strings_in(node):
    """子树里的所有字符串字面量（docstring 过滤在 output_strings 里统一做）。"""
    out = []
    for n in ast.walk(node):
        if isinstance(n, ast.Constant) and isinstance(n.value, str):
            out.append(n)
    return out


def _docstring_nodes(tree):
    """模块/类/函数的第一条 Expr(Constant(str)) 的常量节点集合。"""
    docs = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            body = getattr(node, 'body', None)
            if body and isinstance(body[0], ast.Expr) \
                    and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                docs.add(id(body[0].value))
    return docs


def output_strings(tree):
    """→ [(常量节点, 来源说明)]：所有算「终端输出字符串」的字面量。"""
    printers, funcs = _printers(tree)
    docs = _docstring_nodes(tree)
    found = {}

    def add(node, origin):
        if id(node) in docs:
            return
        found.setdefault(id(node), (node, origin))

    # ① sink / 打印助手的实参
    for n in ast.walk(tree):
        cname = _callee_name(n)
        if cname is None or cname not in printers or cname == 'sys.stdout.flush':
            continue
        for arg in list(n.args) + [k.value for k in n.keywords]:
            for s in _strings_in(arg):
                add(s, '打印助手 %s() 的实参' % cname)

    # ② 消息表（模块级 XXX_MESSAGES = {...} 的字符串取值）
    for n in ast.walk(tree):
        if not isinstance(n, (ast.Assign, ast.AnnAssign)):
            continue
        targets = n.targets if isinstance(n, ast.Assign) else [n.target]
        names = [t.id for t in targets if isinstance(t, ast.Name)]
        if not any(x.endswith('MESSAGES') for x in names):
            continue
        for s in _strings_in(n.value):
            add(s, '消息表 %s' % (names[0] if names else '?'))

    # ③ 一跳跨函数：被当作打印实参调用 / 打印者本身的 `return "字面量"`
    tainted = set()
    for n in ast.walk(tree):
        cname = _callee_name(n)
        if cname is None or cname not in printers:
            continue
        for arg in list(n.args) + [k.value for k in n.keywords]:
            for sub in ast.walk(arg):
                if isinstance(sub, ast.Call):
                    callee = _callee_name(sub)
                    if callee in funcs:
                        tainted.add(callee)
    for name in list(printers):
        if name in funcs:
            tainted.add(name)
    changed = True
    while changed:
        changed = False
        for name in sorted(tainted):
            fn = funcs.get(name)
            if fn is None:
                continue
            for sub in ast.walk(fn):
                if isinstance(sub, ast.Return) and sub.value is not None:
                    for c in ast.walk(sub.value):
                        if isinstance(c, ast.Call):
                            callee = _callee_name(c)
                            if callee in funcs and callee not in tainted:
                                tainted.add(callee)
                                changed = True
    for name in sorted(tainted):
        fn = funcs.get(name)
        if fn is None:
            continue
        for sub in ast.walk(fn):
            if isinstance(sub, ast.Return) and sub.value is not None:
                for s in _strings_in(sub.value):
                    add(s, '函数 %s() 的 return（其返回值上终端）' % name)

    return [found[k] for k in sorted(found, key=lambda k: (found[k][0].lineno,
                                                           found[k][0].col_offset))]


# ---------------------------------------------------------------- 检查
def rules_for(ch):
    """→ [] / ['mark'] / ['gbk'] / ['mark', 'gbk']。

    一个字符可以**同时**犯两条：``✓``(U+2713) 既是「非 ASCII 记号」、又是「cp936 编不出」。
    两条都报 —— 只报一条的话，换掉记号（改成 ASCII ``[OK]``）之后另一条就看不见了。
    """
    out = []
    if ch not in SANCTIONED_MARKS and ch in MARK_FAMILY:
        out.append('mark')
    try:
        ch.encode('gbk')
    except UnicodeEncodeError:
        out.append('gbk')
    return out


def scan_file(path, rel):
    """→ [finding dict]（一个命中一条）。"""
    try:
        text = io.open(path, encoding='utf-8-sig').read()
    except (OSError, UnicodeDecodeError):
        return []
    try:
        tree = ast.parse(text)
    except SyntaxError as e:
        return [{'rel': rel, 'line': getattr(e, 'lineno', 0) or 0, 'char': '',
                 'codepoint': '', 'name': 'ParseError', 'rule': 'parse',
                 'origin': 'ast.parse 失败', 'snippet': str(e)[:120],
                 'severity': 'error'}]
    lines = text.splitlines()
    out = []
    for node, origin in output_strings(tree):
        val = node.value
        if not isinstance(val, str):
            continue
        for ch in val:
            if ord(ch) < 128:
                continue
            rules = rules_for(ch)
            if not rules:
                continue
            ln = getattr(node, 'lineno', 0)
            snippet = (lines[ln - 1].strip() if 0 < ln <= len(lines) else '')[:120]
            for rule in rules:
                out.append({
                    'rel': rel, 'line': ln, 'char': ch,
                    'codepoint': 'U+%04X' % ord(ch),
                    'name': unicodedata.name(ch, '?'),
                    'rule': rule, 'origin': origin, 'snippet': snippet,
                })
    return out


def iter_py(root):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames
                       if d not in SKIP_DIRS and not d.startswith('.tmp')
                       and not d.startswith('tmp_')]
        for f in sorted(filenames):
            if not f.endswith('.py'):
                continue
            # **文件级**也要跳过临时/探针名（t98 修）—— 原来只跳**目录**，于是"别的测试
            # 写进仓库、用完就删"的探针文件会被扫到：
            #   `tests/test_agent_api.py:309` 写 `tests/.tmp_dpi.py`
            #   `tests/test_agent_api.py:445` 写 `tests/_jsonenc9.py`
            # 并发跑两条臂（或与别的 agent 的跑重叠）时，**A 臂的探针文件正被 B 臂的
            # notation 扫描看到** ⇒ 假红（两条臂都红、分离跑都过 —— t87 那次就是这个机制）。
            # 跳过面刻意**很窄**：只认仓库既有的临时/探针命名约定，绝不按"未跟踪"整片跳过
            # （那会把新加的正式套件也漏掉）。`tests/test_notation_discipline.py` 的
            # teeth 17 钉住"探针名不报、正常名照报"。
            if (f.startswith('.tmp') or f.startswith('tmp_')
                    or f.startswith('_jsonenc') or f.endswith('_probe.py')):
                continue
            p = os.path.join(dirpath, f)
            yield p, os.path.relpath(p, root).replace('\\', '/')


def severity(rel, text, f):
    """→ 'error' / 'exempt'（刻意例外）。"""
    if rel.replace('\\', '/').startswith('src/mpltweak/'):
        return 'error'                       # 产品面：用户的控制台，零容忍
    if CONSOLE_FALLBACK_MARK in text:
        return 'exempt'                      # 已装控制台兜底（清洗后才 print）
    if (rel, f['char'], f['snippet']) in NOTATION_BASELINE:
        return 'exempt'                      # 已冻结（只拦新增）
    return 'error'


def run(root=ROOT_DEFAULT):
    findings, texts = [], {}
    for p, rel in iter_py(root):
        try:
            texts[rel] = io.open(p, encoding='utf-8-sig').read()
        except (OSError, UnicodeDecodeError):
            texts[rel] = ''
        findings.extend(scan_file(p, rel))
    for f in findings:
        f['severity'] = severity(f['rel'], texts.get(f['rel'], ''), f)
    return findings


RULE_LABEL = {
    'mark': 'R1 非 ASCII 记号（应写 ASCII [OK]/[X]）',
    'gbk': 'R2 终端字符串不是 GBK 可编码（cp936 上会 UnicodeEncodeError）',
    'parse': '文件无法解析',
}


def report_lines(findings):
    errs = [f for f in findings if f['severity'] == 'error']
    exs = [f for f in findings if f['severity'] == 'exempt']
    out = []
    out.append('记号纪律检查（MAINTENANCE §8.5 + CONTRIBUTING「GBK 可编码」）')
    out.append('总命中 %d：真违规 %d / 刻意例外 %d' % (len(findings), len(errs), len(exs)))
    for title, group in (('真违规（判红）', errs), ('刻意例外（只报告）', exs)):
        out.append('')
        out.append('---- %s：%d 处 ----' % (title, len(group)))
        if not group:
            out.append('     （无）')
        for f in group:
            out.append('     %s:%d  [%s] %s %s  <%s>'
                       % (f['rel'], f['line'], f['codepoint'], f['char'], f['name'],
                          RULE_LABEL.get(f['rule'], f['rule'])))
            out.append('        来源：%s' % f['origin'])
            out.append('        原文：%s' % f['snippet'])
    return out, errs, exs


def to_markdown(findings):
    errs = [f for f in findings if f['severity'] == 'error']
    exs = [f for f in findings if f['severity'] == 'exempt']
    lines = ['# 记号纪律违规清单（tools/notation_discipline.py 生成）', '',
             '口径：`MAINTENANCE.md:336`（§8.5 字形）+ `CONTRIBUTING.md:71-74`（GBK 可编码）'
             '+ ASCII `[OK]`/`[X]`。', '',
             '**总命中 %d：真违规 %d / 刻意例外 %d**' % (len(findings), len(errs), len(exs)),
             '']
    for title, group in (('真违规（需派修）', errs), ('刻意例外（无需修）', exs)):
        lines += ['## %s（%d）' % (title, len(group)), '']
        if not group:
            lines += ['（无）', '']
            continue
        lines += ['| 位置 | 字符 | 规则 | 来源 | 原文 |', '|---|---|---|---|---|']
        for f in group:
            lines.append('| `%s:%d` | `%s` %s | %s | %s | `%s` |'
                         % (f['rel'], f['line'], f['char'], f['codepoint'],
                            RULE_LABEL.get(f['rule'], f['rule']), f['origin'],
                            f['snippet'].replace('|', '\\|')))
        lines.append('')
    return lines


def main(argv=None):
    ap = argparse.ArgumentParser(description='记号纪律检查器（只报告，不修复）')
    ap.add_argument('--root', default=ROOT_DEFAULT, help='扫描根目录（默认仓库根）')
    ap.add_argument('--markdown', action='store_true', help='输出 markdown 清单')
    ap.add_argument('--json', action='store_true', help='输出 JSON')
    ap.add_argument('--quiet', action='store_true', help='只输出一行摘要')
    args = ap.parse_args(argv)

    findings = run(args.root)
    errs = [f for f in findings if f['severity'] == 'error']
    if args.json:
        sys.stdout.write(json.dumps(findings, ensure_ascii=False, indent=2) + '\n')
    elif args.markdown:
        sys.stdout.write('\n'.join(to_markdown(findings)) + '\n')
    elif args.quiet:
        sys.stdout.write('记号纪律：总命中 %d，真违规 %d\n' % (len(findings), len(errs)))
    else:
        lines, _e, _x = report_lines(findings)
        sys.stdout.write('\n'.join(lines) + '\n')
        sys.stdout.write('\n结论：%s\n' % ('FAIL —— 有真违规' if errs else 'OK —— 无真违规'))
    return 1 if errs else 0


if __name__ == '__main__':
    sys.exit(main())
