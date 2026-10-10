# -*- coding: utf-8 -*-
"""统计 ``except Exception`` 的分布，并给出"看起来可窄化"的提示。

    python tools/audit_exceptions.py            # 控制台表格
    python tools/audit_exceptions.py --markdown # Markdown 表格（贴 issue/PR 用）

**为什么需要这个工具**：这类兜底在 GUI 回调与运行时探测里是**对的**（一个坏回调不该
弄死整个调图会话、探测失败要能降级），但一百多处堆在同一个文件里，读代码的人分不清
"预期内降级"和"在掩盖 bug"。与其一次性收窄（回归风险大），不如两条腿走：

1. ``tests/test_hygiene.py`` 里的**棘轮**：禁止新增未登记的兜底（降下来是好事，不拦）；
2. 本工具把现有清单与"可窄化"提示列出来，让贡献者一处一处按需收。

``可窄化`` 列是**启发式提示**，不是判决：它只看 try 体里出现的调用名。
收窄前请确认新写法覆盖了原先所有会被吞掉的异常（GUI 路径少捕一类异常就是崩窗口）。
"""

import argparse
import ast
import os
import sys

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   'src', 'mpltweak')

NARROW_HINTS = (
    (('float', 'int', 'json.loads', 'literal_eval', 'parse', 'int('),
     'ValueError/TypeError'),
    (('open', 'os.path', 'os.stat', 'stat(', 'remove', 'makedirs', 'utime',
      'getmtime', 'listdir', 'isdir', 'exists'),
     'OSError'),
)


def _enclosing(tree):
    """返回 {子节点 id: 最近的函数名}（用于把行号翻译成人话）。"""
    out = {}

    def walk(node, fn):
        for child in ast.iter_child_nodes(node):
            name = fn
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = child.name
            out[id(child)] = name
            walk(child, name)

    walk(tree, '<module>')
    return out


def scan_file(path):
    """扫一个文件，返回 [{line, func, hint}]。"""
    with open(path, 'r', encoding='utf-8') as f:
        src = f.read()
    tree = ast.parse(src)
    owner = _enclosing(tree)
    # 先建 try 节点的父映射：except 属于 try，要拿 try 体里的调用名做提示
    try_of = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Try):
            for h in node.handlers:
                try_of[id(h)] = node
    rows = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        is_bare_or_exception = (node.type is None
                                or (isinstance(node.type, ast.Name)
                                    and node.type.id in ('Exception',
                                                         'BaseException')))
        if not is_bare_or_exception:
            continue
        parent = try_of.get(id(node))
        body_src = ast.dump(parent) if parent is not None else ''
        hint = ''
        for keys, suggestion in NARROW_HINTS:
            if any(k in body_src for k in keys):
                hint = suggestion
                break
        rows.append({'line': node.lineno,
                     'func': owner.get(id(node), '?'),
                     'hint': hint or '需要人工判断'})
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(prog='audit_exceptions.py')
    ap.add_argument('--markdown', action='store_true',
                    help='输出 Markdown 表格（默认控制台表格）')
    args = ap.parse_args(argv if argv is not None else sys.argv[1:])

    total = 0
    files = sorted(f for f in os.listdir(SRC) if f.endswith('.py'))
    if args.markdown:
        print('| 文件 | 行 | 所在函数 | 可窄化提示 |')
        print('|---|---|---|---|')
    for name in files:
        rows = scan_file(os.path.join(SRC, name))
        if not rows:
            continue
        total += len(rows)
        if args.markdown:
            for r in rows:
                print('| `%s` | %d | `%s` | %s |'
                      % (name, r['line'], r['func'], r['hint']))
        else:
            print('%s  (%d 处)' % (name, len(rows)))
            for r in rows:
                print('    L%-5d %-28s %s' % (r['line'], r['func'], r['hint']))
    print()
    print('合计 %d 处。棘轮基线在 tests/test_hygiene.py 的 BASELINE；'
          '只允许降，不允许不登记地涨。' % total)
    return 0


if __name__ == '__main__':
    sys.exit(main())
