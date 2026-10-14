# -*- coding: utf-8 -*-
"""静态扫描：`src/mpltweak/**` 里 `_msg.t('键')` / `_msg.t_ui('模板')` 引用的键
**必须**在 `messages` 的注册表里 —— 否则 `t()` 的容错契约会把**键名回显给用户**。

背景（t93 的真实根因，code-owner 查出）
--------------------------------------
`cli.py` 的 doctor 分支调了**从未登记**的键 `_msg.t('schema_unknown_args', …)`，
而 `messages.t()` 的容错契约是"**key 缺失就原样返回 key**" ⇒ 用户在终端看到的是
字面量 **`schema_unknown_args`**，而不是"无法识别的参数：--nope"。
那次补了登记，但**这一类风险仍敞着**：任何一处漏登记都会把内部键名漏给用户。
本检查器把"漏登记"提前到**静态阶段**抓。

判据（写清不藏）
----------------
* **只认能静态解析的实参**，绝不猜：
  ① 字符串字面量；② `IfExp`（三元）的**两个字面量分支都查**；
  ③ `BinOp(+)` 且**所有**操作数都是字面量（拼接后查）。
  其它形态（变量、dict 查表、`'前缀' + 变量`）⇒ 归入 **`unresolved`** 并**显式列出**
  —— 检查器**不许假装全覆盖**（本仓实测有一批：`_msg.t(key)` 的透传助手、
  `_msg.t(_VERIFY_FAIL_KIND_KEY[kind])` 的查表、`'chk_kind_' + k` 的族前缀）。
* **前缀拼接**额外做一次"族检查"：注册表里**至少要有**一个键以该前缀开头
  （抓"整族没登记 / 前缀写错"），同时仍列进 `unresolved`。
* 注册表用 **AST 解析** `messages.py` 的 `MESSAGES = {...}` / `UI_ZH_TO_EN = {...}`
  取键，**不 import** —— 这样对"被变异的副本"同样成立（teeth 要的就是这个），
  也不会踩 import 缓存。
* **`t(key)` 与 `t_ui(zh_template)` 的"缺登记"后果**完全不同**，必须分开报**：
  * `t(key)`：键是**内部标识符**；缺 ⇒ `t()` 的容错契约**原样返回 key** ⇒
    用户在终端看到字面量键名（**t93 那一类**）⇒ **违规**。
  * `t_ui(zh_template)`：键**就是面向用户的中文模板本身**；缺 ⇒
    `UI_ZH_TO_EN.get(zh, zh)` 返回**中文原文**（`messages.py:1812-1817` 明写的契约）
    ⇒ 后果是"**en 下漏翻**"（i18n 缺口，已由 en-ASCII 棘轮盯），
    **不是键名回显** ⇒ 归 `ui_untranslated`，**只统计不判红**（否则检查器一上来
    就有 60+ 条误报，直接废掉 —— 这正是"判据要区分终端串与别的串"的老教训）。
* 只认接收者是 `_msg` / `messages` / `msg` / `_messages` 的调用 —— 免得把
  `something.t(...)`（别的对象的方法）算进来。

用法
----
    python tools/msg_key_audit.py [--root <仓库根>] [--json]
"""

import ast
import json
import os
import sys

# ---- 输出兜底（T13 纪律）：GBK 控制台 / 管道下 print 中文与记号不崩 ----
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(errors='replace')
    except (AttributeError, ValueError, OSError):
        pass

RECEIVERS = ('_msg', 'messages', 'msg', '_messages')
FUNCS = ('t', 't_ui')


def _dict_keys(path, name):
    """AST 解析 `path` 里 `name = {...}` 的**字符串键**集合（不 import）。"""
    try:
        with open(path, encoding='utf-8') as f:
            tree = ast.parse(f.read())
    except (OSError, SyntaxError):
        return set()
    keys = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for tgt in node.targets:
            if isinstance(tgt, ast.Name) and tgt.id == name \
                    and isinstance(node.value, ast.Dict):
                for k in node.value.keys:
                    if isinstance(k, ast.Constant) and isinstance(k.value, str):
                        keys.add(k.value)
    return keys


def load_registry(repo_root):
    """→ (MESSAGES 键集合, UI_ZH_TO_EN 键集合)。"""
    p = os.path.join(repo_root, 'src', 'mpltweak', 'messages.py')
    return _dict_keys(p, 'MESSAGES'), _dict_keys(p, 'UI_ZH_TO_EN')


def _resolve(node):
    """→ (能解析出的键列表, 未解析的族前缀 or None)。**解析不出就是解析不出**。"""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value], None
    if isinstance(node, ast.IfExp):                     # 三元：两个分支都要查
        a, pa = _resolve(node.body)
        b, pb = _resolve(node.orelse)
        if pa or pb:
            return [], (pa or pb)
        return a + b, None
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        parts = []
        for side in (node.left, node.right):
            if isinstance(side, ast.Constant) and isinstance(side.value, str):
                parts.append(side.value)
            else:
                return [], ''.join(parts)               # 拼到了变量 ⇒ 只留下前缀
        return [''.join(parts)], None
    return [], None


def scan(repo_root):
    """→ {'bad': [...], 'unresolved': [...], 'prefix_bad': [...], 'stats': {...}}"""
    src = os.path.join(repo_root, 'src', 'mpltweak')
    msg_keys, ui_keys = load_registry(repo_root)
    bad, unresolved, prefix_bad, ui_missing = [], [], [], []
    n_calls = n_resolved = 0
    for dirpath, _dirs, files in os.walk(src):
        for fn in sorted(files):
            if not fn.endswith('.py'):
                continue
            path = os.path.join(dirpath, fn)
            rel = os.path.relpath(path, repo_root).replace('\\', '/')
            try:
                with open(path, encoding='utf-8') as f:
                    tree = ast.parse(f.read())
            except (OSError, SyntaxError):
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or not node.args:
                    continue
                fn_node = node.func
                if not isinstance(fn_node, ast.Attribute):
                    continue
                if fn_node.attr not in FUNCS:
                    continue
                recv = getattr(fn_node.value, 'id', None)
                if recv not in RECEIVERS:
                    continue
                n_calls += 1
                which = fn_node.attr
                reg = msg_keys if which == 't' else ui_keys
                keys, prefix = _resolve(node.args[0])
                if prefix is not None:
                    unresolved.append((rel, node.lineno, which,
                                       "'%s' + <非字面量>" % prefix))
                    if not any(k.startswith(prefix) for k in reg):
                        prefix_bad.append((rel, node.lineno, which, prefix))
                    continue
                if not keys:
                    unresolved.append((rel, node.lineno, which,
                                       ast.unparse(node.args[0])[:60]))
                    continue
                for k in keys:
                    n_resolved += 1
                    if k in reg:
                        continue
                    if which == 't':
                        bad.append((rel, node.lineno, which, k))
                    else:
                        ui_missing.append((rel, node.lineno, k))
    return {'bad': bad, 'unresolved': unresolved, 'prefix_bad': prefix_bad,
            'ui_untranslated': ui_missing,
            'stats': {'calls': n_calls, 'resolved_keys': n_resolved,
                      'unresolved': len(unresolved),
                      'ui_untranslated': len(ui_missing),
                      'messages_keys': len(msg_keys), 'ui_keys': len(ui_keys)}}


def to_markdown(res):
    out = ['# 消息键登记审计', '']
    s = res['stats']
    out.append('- 调用点 %d（可解析键 %d / 未解析 %d）'
               % (s['calls'], s['resolved_keys'], s['unresolved']))
    out.append('- 注册表：MESSAGES %d 键 / UI_ZH_TO_EN %d 键'
               % (s['messages_keys'], s['ui_keys']))
    out.append('- **漏登记（`t()`，会把键名回显给用户）%d 处**' % len(res['bad']))
    for rel, ln, which, k in res['bad']:
        out.append('  - `%s:%d` `%s(%r)` —— 注册表里没有这个键 ⇒ 会把键名回显给用户'
                   % (rel, ln, which, k))
    out.append('- `t_ui()` 漏翻 %d 处（**只统计不判红**：缺登记 ⇒ 返回中文原文，'
               '是"en 下漏翻"而不是键名回显；由 en-ASCII 棘轮盯）' % len(res['ui_untranslated']))
    for rel, ln, k in res['ui_untranslated'][:5]:
        out.append('  - `%s:%d` %s' % (rel, ln, k[:60]))
    out.append('- 族前缀失配 %d 处' % len(res['prefix_bad']))
    for rel, ln, which, p in res['prefix_bad']:
        out.append("  - `%s:%d` `%s('%s' + …)` —— 注册表里没有任何键以该前缀开头"
                   % (rel, ln, which, p))
    out.append('- 未解析（**不假装覆盖**）%d 处' % len(res['unresolved']))
    for rel, ln, which, expr in res['unresolved'][:40]:
        out.append('  - `%s:%d` `%s(%s)`' % (rel, ln, which, expr))
    return '\n'.join(out)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if '--root' in argv:
        root = argv[argv.index('--root') + 1]
    res = scan(root)
    if '--json' in argv:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        print(to_markdown(res))
    return 1 if (res['bad'] or res['prefix_bad']) else 0


if __name__ == '__main__':
    sys.exit(main())
