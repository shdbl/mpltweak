# -*- coding: utf-8 -*-
"""模块「自动导航索引」生成器 / 校验器（**只读源码，不 import 被检查的模块**）。

## 这个工具为什么存在

`writeback.py` 6,267 行、157 个顶层函数（`toolbox.py` 3,996 / `messages.py` 1,930 /
`verify.py` 1,442 / `apply.py` 1,156 / `launch.py` 1,028 同理）。用户 2026-10-21 拍板
**不拆文件**（全拆 6–9 天，而且会把 tests 里 23 处 `monkeypatch` 缝变成假绿），改为
**加导航**：文件头目录 + 功能分区横幅 + 自动索引块。

**铁律：手写索引必然过期，所以索引只能由本工具生成、并且能校验。**
谁都不许人工誊抄行号；改完代码跑一次 `--write`，CI/卫生棘轮用 `--check` 钉住。

## 用法

    python tools/gen_nav.py --check                 # 校全部默认清单（rc=0 最新 / 1 过期）
    python tools/gen_nav.py --write                 # 重算默认清单的索引块
    python tools/gen_nav.py src/mpltweak/toolbox.py --write   # 接别的模块：一行就够
    python tools/gen_nav.py --check src/mpltweak/verify.py    # 也可以只校一个

退出码（与仓库其它工具同口径）：
    0 = 全部最新；1 = 有模块过期/缺索引块；2 = 用法或格式错（认不出文件、两个开关同给、
        横幅编号乱序/重复、模块里**一个分区横幅都没有**）。

## 索引块长什么样（写进模块**文件尾部**）

    # --- BEGIN AUTO NAV ---
    # 自动生成，不要手改：`python tools/gen_nav.py --write` 重算，`--check` 校验
    # §4 figure dpi 静态推断与 figsize 交叉校验（行 467-797）
    #     _call_leaf                                 467
    # --- END AUTO NAV ---

行号 = 该 `def` 的**定义行**（1-based）。块放在**文件末尾**是刻意的：它自己占的行都在
所有函数之后，重算不会把前面的行号挤动（否则生成-校验会互相打架、永远不一致）。

## 分区怎么来（为什么不许自动按 500 行切）

分区横幅是人按**真实功能边界**写的注释行：

    # ==== §4 figure dpi 静态推断与 figsize 交叉校验 ====

本工具只做两件事：把横幅**下面的**顶层函数归给该分区、把 `def` 行号填进索引。
**分组本身由人判断** —— 机械地每 N 行插一条只会造出"看起来有导航、其实都在瞎指"的假
导览（任务书原话：判不准宁可不插，错分组比没有更糟）。所以横幅缺失/乱序时本工具直接
报错（rc=2），不替你猜。

## 为什么不是 pytest 里的断言、而是一个工具

索引**必然**随任何一次代码改动过期（改一行就漂）。所以真正的守门人是
`tests/test_hygiene.py` 里那条用例（与 `--check` 同判据），工具本身负责在修复时给出
"哪里不一致、该跑什么命令"的可复制提示。
"""

import argparse
import ast
import io
import os
import re
import sys

BEGIN_MARK = '# --- BEGIN AUTO NAV ---'
END_MARK = '# --- END AUTO NAV ---'
#: 分区横幅：`# ==== §4 分区名 ====`（§ 后面是编号，名称里不许再出现 `====`）
BANNER_RE = re.compile(r'^# ==== §(?P<num>\d+)\s+(?P<name>[^=].*?)\s*====\s*$')

#: 默认清单：本批（0.1.13）只给 writeback.py 落地；其余按文件大小排队，见交付说明。
DEFAULT_MODULES = ('src/mpltweak/writeback.py',)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read_lines(path):
    with io.open(path, encoding='utf-8') as f:
        return f.read().splitlines()


def _rel(path):
    try:
        return os.path.relpath(path, ROOT).replace(os.sep, '/')
    except ValueError:                              # Windows 跨盘符
        return path.replace(os.sep, '/')


def find_block(lines):
    """索引块的行区间 ``(begin, end)``（0-based，**闭区间**，含两条标记行）。

    返回 ``None`` = 没有索引块；只有一个标记 ⇒ ``ValueError``（块被写坏了，不是"过期"）。
    """
    begins = [i for i, ln in enumerate(lines) if ln.strip() == BEGIN_MARK]
    ends = [i for i, ln in enumerate(lines) if ln.strip() == END_MARK]
    if not begins and not ends:
        return None
    if len(begins) != 1 or len(ends) != 1 or ends[0] < begins[0]:
        raise ValueError('索引块标记不完整/重复（BEGIN %d 处、END %d 处）'
                         % (len(begins), len(ends)))
    return begins[0], ends[0]


def _banners(lines):
    """所有分区横幅 → ``[(行号(1-based), 编号, 名称), ...]``（按出现顺序）。"""
    out = []
    for i, ln in enumerate(lines):
        m = BANNER_RE.match(ln)
        if m:
            out.append((i + 1, int(m.group('num')), m.group('name').strip()))
    return out


def _top_defs(src):
    """顶层 ``def`` / ``class`` → ``[(行号(1-based), 名字), ...]``（按源码序）。"""
    tree = ast.parse(src)
    out = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.append((node.lineno, node.name))
    return out


def _validate_banners(banners):
    """编号必须严格递增（不许重复/倒序）—— 乱序的分区导航比没有更糟。"""
    seen = None
    for line, num, _name in banners:
        if seen is not None and num <= seen:
            raise ValueError('分区横幅编号必须严格递增：第 %d 行的 §%d 不大于上一个 §%d'
                             % (line, num, seen))
        seen = num


def build_block(src, path):
    """按现场源码算出索引块的**全部行**（含两条标记行）。"""
    lines = src.splitlines()
    banners = _banners(lines)
    if not banners:
        raise ValueError('一个分区横幅都没有（形如 `# ==== §1 … ====`）—— '
                         '先按真实功能边界加横幅，本工具不替你猜分组')
    _validate_banners(banners)
    defs = _top_defs(src)

    # **关键**：算分区区间时要把**已有的索引块本身**排除掉。索引块在文件末尾，否则
    # 最后一个分区的"结束行 = 文件总行数"会随索引块自己的长短而变 —— 写完再校验就永远
    # 不一致（自指死循环）。所以内容区在 BEGIN 标记之前结束。
    span = find_block(lines)          # 写坏的块直接抛给调用方（路径由它加一次）
    # 内容区的**结束行**：索引块之前（没有块就是文件末尾）最后一个**非空**行。
    # 不能用「文件总行数」：块的长度会改总行数 ⇒ 写完再 --check 永远不一致（自指）。
    total = span[0] if span is not None else len(lines)
    while total > 0 and not lines[total - 1].strip():
        total -= 1

    groups = []
    for line, num, name in banners:
        groups.append({'num': num, 'name': name, 'line': line, 'defs': []})
    leading = []
    for dline, dname in defs:
        owner = None
        for g in groups:
            if g['line'] < dline:
                owner = g
            else:
                break
        (owner['defs'] if owner is not None else leading).append((dline, dname))

    out = [BEGIN_MARK,
           '# 自动生成，不要手改：`python tools/gen_nav.py --write` 重算、`--check` 校验',
           '# （tests/test_hygiene.py 有一条同判据的用例）。行号 = 该 def 的定义行（1-based）。',
           '# 索引块固定放在文件末尾：它自己不占用任何函数之前的行，重算不会挤动行号。']
    if leading:
        out.append('# （§1 之前还有 %d 个顶层定义，未归入任何分区 —— 请检查横幅位置）'
                   % len(leading))
    for idx, g in enumerate(groups):
        end = (groups[idx + 1]['line'] - 1) if idx + 1 < len(groups) else total
        out.append('# §%d %s（行 %d-%d，%d 个顶层函数）'
                   % (g['num'], g['name'], g['line'], end, len(g['defs'])))
        for dline, dname in g['defs']:
            out.append('#     %-44s %5d' % (dname, dline))
        if not g['defs']:
            out.append('#     （本分区没有顶层函数）')
    out.append(END_MARK)
    return out


def check_module(path):
    """→ ``(ok, 消息)``；块缺失/过期都算不 ok。"""
    lines = _read_lines(path)
    src = '\n'.join(lines) + '\n'
    # 格式错（没横幅 / 编号乱序 / 标记写坏）**故意**不在这里吞掉：那是 rc=2 的用法错，
    # 不是「索引过期」（rc=1）—— 混在一起会让人以为跑一下 --write 就好。
    want = build_block(src, path)
    span = find_block(lines)
    if span is None:
        return False, ('%s：**没有**索引块 → 跑 `python tools/gen_nav.py --write`'
                       % _rel(path))
    have = lines[span[0]:span[1] + 1]
    if have == want:
        return True, '%s：索引块最新（%d 行）' % (_rel(path), len(want))
    n = 0
    for a, b in zip(have, want):
        if a != b:
            break
        n += 1
    detail = ''
    if n < len(have) and n < len(want):
        detail = '；首个不同：第 %d 行（块内）\n  现在：%s\n  应为：%s' % (
            n + 1, have[n], want[n])
    elif len(have) != len(want):
        detail = '；行数不同（现在 %d 行 / 应为 %d 行）' % (len(have), len(want))
    return False, ('%s：索引块**过期**%s → 跑 `python tools/gen_nav.py --write`'
                   % (_rel(path), detail))


def write_module(path):
    """重算并就地替换索引块（没有就追加到文件末尾）→ ``(ok, 消息)``。"""
    lines = _read_lines(path)
    src = '\n'.join(lines) + '\n'
    # 格式错（没横幅 / 编号乱序 / 标记写坏）**故意**不在这里吞掉：那是 rc=2 的用法错，
    # 不是「索引过期」（rc=1）—— 混在一起会让人以为跑一下 --write 就好。
    want = build_block(src, path)
    span = find_block(lines)
    if span is None:
        while lines and not lines[-1].strip():
            lines.pop()
        lines = lines + [''] + want
    else:
        lines = lines[:span[0]] + want + lines[span[1] + 1:]
    text = '\n'.join(lines) + '\n'
    with io.open(path, 'w', encoding='utf-8', newline='\n') as f:
        f.write(text)
    return True, '%s：索引块已重算（%d 行）' % (_rel(path), len(want))


def _modules(args):
    return [_resolve(a) for a in args] if args else [_resolve(p) for p in DEFAULT_MODULES]


def _resolve(p):
    """模块路径 → 绝对路径（相对路径先按 repo 根解析，再按 CWD 解析）。"""
    if os.path.isabs(p):
        return p
    cand = os.path.join(ROOT, p)
    if os.path.exists(cand):
        return cand
    return os.path.abspath(p)


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog='gen_nav.py',
        description='生成/校验模块的自动导航索引块（分区横幅 → 顶层函数 → 行号）。')
    ap.add_argument('modules', nargs='*', default=None,
                    help='要处理的模块（相对 repo 根或绝对路径）；不给就用默认清单')
    ap.add_argument('--check', action='store_true', help='只校验（默认行为）')
    ap.add_argument('--write', action='store_true', help='重算并写回索引块')
    args = ap.parse_args(argv)
    if args.check and args.write:
        sys.stderr.write('[gen_nav] 用法错：--check 与 --write 不能同时给\n')
        return 2
    paths = _modules(args.modules)
    missing = [p for p in paths if not os.path.isfile(p)]
    if missing:
        sys.stderr.write('[gen_nav] 用法错：找不到模块 %s\n' % ', '.join(missing))
        return 2
    rc = 0
    for p in paths:
        try:
            ok, msg = (write_module if args.write else check_module)(p)
        except (SyntaxError, ValueError) as e:
            sys.stderr.write('[gen_nav] %s：%s\n' % (_rel(p), e))
            rc = 2
            continue
        print('[gen_nav] ' + msg)
        if not ok and not rc:
            rc = 1
    return rc


if __name__ == '__main__':
    sys.exit(main())
