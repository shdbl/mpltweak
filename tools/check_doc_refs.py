# -*- coding: utf-8 -*-
"""文档里的 `文件.py:行号` 引用检查器（**只报告，不修改**）。

## 这个工具为什么存在

对外文档 / 手册里写死的 `文件:行号` 会随代码改动漂移。2026-10-13 那一夜，同一批
`writeback.py` 引用**漂移了两次**（t30 刷过一遍，之后 t36 / t51 / t52 / t54 又让它变长）。
所以它**故意不做成 CI 测试** —— 代码一改行号就漂，做成断言只会天天红、最后被
"放宽"掉。定位是**发布期跑一次、人工过目**：输出每条引用**当前那一行到底是什么**，
让人一眼看出"文档说的和代码说的还是不是一回事"。

**它只能发现三类硬伤**（解析不到目标文件 / 行号越界 / 指向空行）；
**行号"在范围内但指错了函数"它判不出来** —— 那需要人读输出，这正是"人工过目"的意思。

## 用法

    python tools/check_doc_refs.py                    # 默认扫 docs/** + README*.md + skills/**
    python tools/check_doc_refs.py --with-maintenance # 追加 MAINTENANCE.md（未入库，默认不扫）
    python tools/check_doc_refs.py --json             # 机器可读
    python tools/check_doc_refs.py --root DIR         # 扫别的目录（自测 / 验证"故意造坏"的检出）

退出码：**有可疑引用（解析不到 / 越界 / 空行）时 rc=1**，便于当发布闸门；干净时 rc=0。
`文件:行号` 的两种形态都认：`writeback.py:2888` 与 `writeback.py:2126-2165`（区间只查起点行）。

范围与判据的边界（写清以免误用）：
* 只认 `*.py:行号`；`README*.md` 里若引用别的语言的行号（如 `foo.rs:12`）不在范围内；
* 目标文件解析顺序：相对仓库根 → **裸 basename 优先 `<root>/src/mpltweak/<basename>`** →
  仓库内**唯一**同名命中 → matplotlib 自带的 `backend_qt.py`（文档里确实引过它的行号）；
  都没有就报"解析不到"；**同名多命中一律报 `ambiguous_basename`（不许静默取第一个）**。
* **不索引构建产物 / 缓存**（`build/`、`dist/`、`*.egg-info`、`__pycache__`、`.git` 等，t103）：
  发布流程是**先 build wheel 再跑闸门**，而 `build/lib/mpltweak/*` 是 `.gitignore` 忽略的
  **陈旧副本** —— 索引它会让闸门"校验一个旧树"（t73 的 F1' 就是这么发生的：41 条引用里 38 条
  解析到了 `build\lib\mpltweak\*`）。
* **历史留痕放行（`HISTORICAL_ALLOW`，t72）**：`MAINTENANCE.md` 是**日志型**手册，里面大量
  `文件:行号` 记的是**当时现场**（缺陷记录 / 纠偏段 / 交接清单）。这些行号**不该被改** —— 改了
  就是篡改历史。所以按 `(文档, 引用)` 的**精确对**逐条登记、**每条带一行理由**；命中时单独打印为
  "放行"、**不计入可疑**（`rc` 仍由真可疑决定）。**放行只针对这些精确对**：判据（解析不到 /
  越界 / 空行）与退出码语义一字未改，也**不许**按文件整片跳过 —— 新出现的漂移照样报红。
* **只报告，不改任何文件**。
"""
import argparse
import io
import json
import os
import re
import sys

ROOT_DEFAULT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_INCLUDES = ('docs/**/*.md', 'README*.md', 'skills/**/*.md')

CITE_RE = re.compile(r'([A-Za-z0-9_./\\-]+\.py):(\d+)(?:-(\d+))?')

# 不索引的目录（构建产物 / 缓存）与"裸 basename 的首选解析位置"（t103）。
# `build/` 是 `.gitignore:5` 忽略的**陈旧构建副本**；发布流程先 build wheel 再跑闸门 ⇒
# 索引它 = 闸门在校验旧树（t73 的 F1'：41 条引用里 38 条解析到 `build\lib\mpltweak\*`）。
SKIP_DIRS = {'.git', 'build', 'dist', '__pycache__', '.pytest_cache', '.mypy_cache',
             '.tox', '.eggs', 'node_modules', '.venv', 'venv', '.ruff_cache'}
SRC_PKG = os.path.join('src', 'mpltweak')

# 历史留痕放行（t72）：`(文档相对路径, 引用串)` → **一行理由**。命中的引用单独打印为"放行"、
# 不计入可疑（判据与 rc 语义不变）。**只加"确实是当时现场"的条目，不许为了让闸门变绿而放宽**：
# 这些行号一旦被"改对"就等于篡改历史（MAINTENANCE 是日志型手册）；现行行号见各节正文与 ★ 段。
# **只准缩小**（t103 缩小记录）：`('MAINTENANCE.md', 'writeback.py:4432')` 曾在本表里 —— 那是
# 闸门解析到旧 `build/` 副本时的产物（该副本那行是空行）；修解析后它命中 `src/mpltweak/writeback.py`
# 的非空行 ⇒ 不再可疑，条目已删。表里现存 **8 键 / 9 条被放行引用**（`verify.py:1061` 两处同引）。
HISTORICAL_ALLOW = {
    ('MAINTENANCE.md', 'toolbox.py:2070-2072'):
        '§13.1（2026-10-12）实机 bug 的**修复前现场**记录',
    ('MAINTENANCE.md', 'launch.py:359'):
        '§12.1（2026-10-12）文档去重时的现场行号',
    ('MAINTENANCE.md', 'src/mpltweak/launch.py:760'):
        '§14.2 第 1 条的**原判据**（当时现场；现行行号见该条 ★ 段）',
    ('MAINTENANCE.md', 'verify.py:1061'):
        '§14.2 第 2 条 ★ 纠偏段 / §20.1 交接清单的当时行号（两处同引；现行见 `verify.py` 的 `_child_env`）',
    ('MAINTENANCE.md', 'apply.py:363'):
        '§14.2 第 5 条的**原判据**（当时现场）',
    ('MAINTENANCE.md', 'toolbox.py:1232'):
        '§14.2 第 5 条的**原判据**（当时现场）',
    ('MAINTENANCE.md', 'launch.py:760'):
        '§14.2 第 7 条的**原判据**（当时现场）',
    ('MAINTENANCE.md', 'launch.py:74-81'):
        '§20.1（2026-10-13 交接清单）的当时行号',
}


def _flag(rec, why, susp, items):
    """记一条可疑；`(doc, ref)` 在 `HISTORICAL_ALLOW` 里则改记"放行"、不计入可疑。"""
    rec['why'] = why
    key = (rec['doc'], rec['ref'])
    if key in HISTORICAL_ALLOW:
        rec['allowed'] = HISTORICAL_ALLOW[key]
    else:
        susp.append(rec)
    items.append(rec)


def _setup_console():
    """控制台兜底：GBK 控制台下打印中文 / 任意字节不许崩（与仓库既有 tools/ 同款）。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors='replace')
        except (AttributeError, ValueError):
            pass


def _iter_docs(root, patterns):
    """展开 glob（** 递归），返回去重后的文件列表。"""
    import glob as _glob
    out = []
    for pat in patterns:
        for p in _glob.glob(os.path.join(root, pat), recursive=True):
            if os.path.isfile(p) and p not in out:
                out.append(p)
    return sorted(out)


def _index_py(root):
    idx = {}
    for d, subs, fs in os.walk(root):
        subs[:] = [s for s in subs
                   if s not in SKIP_DIRS and not s.endswith('.egg-info')]
        if any(part in SKIP_DIRS for part in d.split(os.sep)):
            continue
        for f in fs:
            if f.endswith('.py'):
                idx.setdefault(f, []).append(os.path.join(d, f))
    try:                                  # 文档里引过 matplotlib 自己的 backend_qt.py
        import matplotlib.backends.backend_qt as _bq
        idx.setdefault('backend_qt.py', []).append(_bq.__file__)
    except Exception:                     # noqa: BLE001 - 没装 matplotlib 就少一类可解析目标
        pass
    return idx


class _Cache(object):
    def __init__(self):
        self._d = {}

    def lines(self, path):
        if path not in self._d:
            try:
                with io.open(path, encoding='utf-8', errors='replace') as f:
                    self._d[path] = f.read().split('\n')
            except OSError:
                self._d[path] = None
        return self._d[path]


def _resolve(root, idx, path):
    """解析引用 → `(target, ambiguous)`。

    `ambiguous` 非空 = **同名多命中且首选位置没有**：返回候选相对路径列表（调用方报
    `ambiguous_basename`），**绝不静默取 `hits[0]`**（t103：那正是"闸门读错树"的机制）。
    """
    if os.path.isabs(path):
        return (path if os.path.exists(path) else None), []
    cand = os.path.join(root, path.replace('/', os.sep))
    if os.path.exists(cand):
        return cand, []
    base = os.path.basename(path)
    if os.sep not in path.replace('/', os.sep):
        # 裸 basename：本仓库的产品源码位置优先（`src/mpltweak/`）
        preferred = os.path.join(root, SRC_PKG, base)
        if os.path.isfile(preferred):
            return preferred, []
    hits = [h for h in idx.get(base, []) if os.path.isfile(h)]
    if not hits:
        return None, []
    if len(hits) == 1:
        return hits[0], []
    return hits[0], [os.path.relpath(h, root) for h in sorted(hits)]


def scan(root, patterns):
    """返回 (条目列表, 可疑列表)。每条目：doc / doc_line / ref / target / line_no / code / why。"""
    cache = _Cache()
    idx = _index_py(root)
    items, susp = [], []
    for doc in _iter_docs(root, patterns):
        rel_doc = os.path.relpath(doc, root)
        for i, line in enumerate(cache.lines(doc) or [], 1):
            for m in CITE_RE.finditer(line):
                ref, a, b = m.group(1), int(m.group(2)), m.group(3)
                rec = {'doc': rel_doc, 'doc_line': i,
                       'ref': '%s:%d%s' % (ref, a, ('-' + b) if b else ''),
                       'target': None, 'line': a, 'code': '', 'why': ''}
                tgt, ambig = _resolve(root, idx, ref)
                if ambig:
                    rec['target'] = os.path.relpath(tgt, root)
                    rec['candidates'] = ambig
                    _flag(rec, 'ambiguous_basename', susp, items)
                    continue
                if tgt is None:
                    _flag(rec, 'target_not_found', susp, items)
                    continue
                rec['target'] = os.path.relpath(tgt, root)
                lines = cache.lines(tgt)
                if lines is None or a > len(lines):
                    _flag(rec, 'line_out_of_range', susp, items)
                    continue
                code = lines[a - 1].strip()
                rec['code'] = code
                if not code:
                    _flag(rec, 'blank_line', susp, items)
                else:
                    items.append(rec)
    return items, susp


def main(argv=None):
    _setup_console()
    ap = argparse.ArgumentParser(description='检查文档里的 文件.py:行号 引用（只报告，不修改）')
    ap.add_argument('--root', default=ROOT_DEFAULT, help='仓库根（默认：本脚本的上一级）')
    ap.add_argument('--include', action='append', default=None,
                    help='额外 / 覆盖的扫描 glob（可重复）；给了就只用给的那些')
    ap.add_argument('--with-maintenance', action='store_true',
                    help='追加 MAINTENANCE.md（未入库文件，默认不扫）')
    ap.add_argument('--json', action='store_true', help='输出 JSON')
    args = ap.parse_args(argv)

    root = os.path.abspath(args.root)
    patterns = list(args.include) if args.include else list(DEFAULT_INCLUDES)
    if args.with_maintenance:
        patterns.append('MAINTENANCE.md')

    items, susp = scan(root, patterns)
    allowed = [r for r in items if r.get('allowed')]

    if args.json:
        sys.stdout.write(json.dumps({'root': root, 'patterns': patterns,
                                     'items': items, 'suspicious': susp,
                                     'allowed': allowed},
                                    ensure_ascii=False, indent=2) + '\n')
    else:
        print('文档引用检查（只报告）：root=%s' % root)
        print('扫描面：%s' % '、'.join(patterns))
        print('')
        for r in items:
            if not r['why']:
                mark = '   '
            elif r.get('allowed'):
                mark = ' ~ '
            else:
                mark = ' ! '
            if r['target'] is None:
                detail = '<解析不到该文件>'
            elif r['why'] == 'line_out_of_range':
                detail = '<越界>'
            elif r['why'] == 'blank_line':
                detail = '<空行>'
            else:
                detail = r['code'][:100]
            print('%s%s:%d | %s | %s' % (mark, r['doc'], r['doc_line'], r['ref'], detail))
        print('')
        print('引用条数=%d 可疑=%d 放行（历史留痕）=%d'
              % (len(items), len(susp), len(allowed)))
        if allowed:
            print('')
            print('放行（历史留痕 · 当时现场；逐条带理由，不计入可疑）：')
            for r in allowed:
                print('  [~] %s:%d | %s | %s —— %s'
                      % (r['doc'], r['doc_line'], r['ref'], r['why'], r['allowed']))
        if susp:
            print('')
            print('可疑引用（需要人工确认）：')
            for r in susp:
                print('  [X] %s:%d | %s | %s' % (r['doc'], r['doc_line'], r['ref'], r['why']))
            print('')
            print('提示：这些引用可能只是指向了已删除 / 移位的代码 —— 请人工判断'
                  '"文档想指的那段代码现在在哪"，本工具不改任何文件。')
        else:
            print('[OK] 没有"解析不到 / 越界 / 空行"的引用%s。'
                  '注意：行号在范围内但指错函数的，本工具判不出来，仍需人工过目上面每一行。'
                  % ('（放行的 %d 条是历史留痕，理由见上）' % len(allowed) if allowed else ''))
    return 1 if susp else 0


if __name__ == '__main__':
    sys.exit(main())
