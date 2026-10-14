# -*- coding: utf-8 -*-
"""断言形状的结构性护栏（t76）。

为什么需要：2026-10-14 实测到"某断言段 9 条断言因实参顺序写反而**全部空转**、一直绿着"。
本仓库的 `check` 有两个家族、参数顺序**相反**（见下 `_ORDER`），所以顺序写错**不会**
报错，只会静默恒真。这里用静态扫描把这种形状钉死：

  * `(name, cond, …)` 家族：第 2 个位置实参**不许**是字符串字面量
    （写成了 `check(cond, name, …)` 就会命中）。
  * `(cond, label, …)` 家族：第 1 个位置实参**不许**是字符串字面量。

**为什么不做 keyword-only**：全仓 1,176 处 `check(...)` 调用分布在 17 个测试文件、
属多个成员的文件域；改成 keyword-only 要动全部调用点（且与"只做增量编辑"纪律冲突）。
静态护栏 + 各文件 helper 里的运行时护栏（见 tests/test_launch.py 的 `check`）合起来
达到同样的结构性保护：写错**立刻**红（静态闸门）或**立刻** TypeError（运行时）。

用法：`python tools/assert_shape_guard.py [--json]`；违规时退出码 1。
"""
import argparse
import ast
import io
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HELPERS = ('check', '_check')
# 家族判据：helper 的前两个形参名
_ORDER = {
    ('name', 'cond'): 'name_first',
    ('cond', 'label'): 'cond_first',
    ('cond', 'name'): 'cond_first',
    ('cid', 'layer'): 'name_first',      # test_real_window 的方法版
}


def _helper_orders(tree):
    out = {}
    for n in ast.walk(tree):
        if isinstance(n, ast.FunctionDef) and n.name in HELPERS:
            a = [x.arg for x in n.args.args]
            if len(a) >= 2:
                out[n.name] = _ORDER.get((a[0], a[1]), 'unknown')
    return out


def scan(path):
    """返回该文件的违规列表 [(行号, helper, 说明)]。"""
    try:
        tree = ast.parse(io.open(path, encoding='utf-8').read())
    except SyntaxError as e:
        return [('syntax', '', '语法错误：%s' % e)]
    orders = _helper_orders(tree)
    bad = []
    for n in ast.walk(tree):
        if not isinstance(n, ast.Call):
            continue
        fname = getattr(n.func, 'id', '') or getattr(n.func, 'attr', '')
        if fname not in HELPERS or fname not in orders:
            continue
        order = orders[fname]
        if order == 'unknown' or not n.args:
            continue
        idx = 1 if order == 'name_first' else 0
        if len(n.args) <= idx:
            continue
        a = n.args[idx]
        if isinstance(a, ast.Constant) and isinstance(a.value, str):
            bad.append((n.lineno, fname,
                        '%s 家族的第 %d 个位置实参是字符串字面量'
                        % (order, idx + 1)))
    return bad


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--json', action='store_true')
    args = ap.parse_args(argv)
    tdir = os.path.join(ROOT, 'tests')
    total, files = 0, []
    for fn in sorted(os.listdir(tdir)):
        if not fn.endswith('.py'):
            continue
        bad = scan(os.path.join(tdir, fn))
        if bad:
            total += len(bad)
            files.append({'file': 'tests/' + fn, 'violations': [
                {'line': ln, 'helper': h, 'why': w} for ln, h, w in bad]})
    if args.json:
        print(json.dumps({'violations': total, 'files': files},
                         ensure_ascii=False, indent=2))
    else:
        print('断言形状护栏：扫描 tests/*.py 共 %d 个文件' %
              len([f for f in os.listdir(tdir) if f.endswith('.py')]))
        for f in files:
            print('  %s：%d 处' % (f['file'], len(f['violations'])))
            for v in f['violations'][:5]:
                print('     L%-5d %s  %s' % (v['line'], v['helper'], v['why']))
        print('结论：%s（违规 %d 处）' % ('OK' if total == 0 else 'FAIL', total))
    return 1 if total else 0


if __name__ == '__main__':
    sys.exit(main())
