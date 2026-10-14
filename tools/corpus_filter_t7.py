#!/usr/bin/env python
# -*- coding: utf-8 -*-
# tools/corpus_filter_t7.py —— 全量静态筛选（t7）：量出"依赖齐 + 有可改目标"的语料脚本数。
#
# **纯静态**：绝不执行语料脚本（当前无文件系统沙箱，且用户在场不能弹窗）。
#
# 两层筛选（都是静态）：
#   A. 依赖可达性预检：ast 解析脚本**顶层** import（import X / from X import Y /
#      from X.Y import Z），对每个**顶层包名**用 importlib.util.find_spec(name) 判断
#      （find_spec 对顶层包只查规格、不执行模块体；异常/None = 缺失）。不 import 语料脚本。
#   B. 目标可定位性预检：跑真实 CLI
#         python -m mpltweak apply <脚本> --params <pp> --write --dry-run --no-verify --json
#      证据（无 --write 不分析、dry-run 不写不执行）：
#        - apply.py:543-551：无 --write 时 apply 直接返回 reason='preview'，**writeback
#          （真正做分析/写盘/验证的模块）根本没被调用** → 必须带 --write 才做分析；
#        - writeback.py:2835-2843：inplace 路径 `if dry_run: return` —— 在备份/写盘/
#          verify 之前早退，只返回完整分析结果（reason/changes/block/pending）；
#        - writeback.py:2815-2826：唯一会执行脚本的 `_pending_vs_live`（2821）只在
#          "轴级 pending 非空"时才可达；本任务参数只有 figsize（无轴字段）→ `_pending`
#          恒为空 → 即使 verify=True 也不会执行；再加 --no-verify 使 verify=False，
#          让该分支结构性不可达（双重保险）；
#        - writeback.py:4572/4803-4805：块路径 `if not dry_run:` 分支才是真实插入+重跑
#          （脚本执行所在），dry-run 走 else 的**纯文本预览**分支（静态）；
#        - apply.py:649：`_applied = ... and not args.dry_run` → dry-run 永不报"已写回"。
#      判定：files[0].changes 非空（原位计划）或 block_lines>0（块插入计划）= 有目标；
#      否则（no_fig/no_axes/fail/no_change/...）= 无目标。
#
# 自证（金丝雀）：两条 canary 断言写进 FILTER.md：
#   ① 脚本 canary：假脚本含 `open('CANARY_EXECUTED.txt','w')` 副作用，走同一筛选管线，
#      断言结束后该文件不存在；
#   ② 依赖 canary：假包 __init__.py 含写文件副作用，走同一 find_spec 路径，断言未执行。
#
# 用法：
#   python tools/corpus_filter_t7.py                  # 全量（plot==true 全部）
#   python tools/corpus_filter_t7.py --limit 30       # 冒烟小样
#   python tools/corpus_filter_t7.py --jobs 4         # 并发 apply 预演 worker 数（有界池）
#   python tools/corpus_filter_t7.py --out <dir>      # 产出目录（默认 _review/corpus_filter_t7）
#   python tools/corpus_filter_t7.py --workroot <dir> # 临时工作目录（默认 <tmp>/corpus_filter_work；
#                                                     #  <tmp> 跨平台取 TMPDIR→TEMP→TMP→gettempdir()）
#
# 语料目录只读；产出 filter.jsonl + FILTER.md。
import argparse
import ast
import collections
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from importlib.util import find_spec

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # mpltweak 树
# 语料与产出都在**仓库外的工作区**（`MAINTENANCE.md:295` §7「素材与工作脚本」；`_review/**`
# 是"本机工作记录、未随仓库发布"，见 `docs/reliability.md:242`）。这里**不写死盘符**：
# 按同族工具的既有写法从树位置推 —— `tools/cmp_plain_vs_instrumented.py:77-80`、
# `tools/probe_close_vs_wrapper.py:54`、`tools/probe_onepass_perf.py:141` 对**同两个路径**
# 都是这么写的，消费方 `tests/test_corpus_invariants.py:632-633` 的 WORKSPACE 也这么推；
# 本工具曾是 `tools/` 里唯一写死 `D:\` 的地方。
# 本机（`<ws> = D:\Agent\deepseek`）解析结果与旧的写死值**逐字相同**（t39 实测相等）；
# 换了布局或换机器请用 `--corpus` / `--out` 显式指定 —— 语料本身不随仓库发布。
WORKSPACE = os.path.dirname(ROOT)
DEFAULT_CORPUS = os.path.join(WORKSPACE, 'corpus_union')
DEFAULT_OUT = os.path.join(WORKSPACE, '_review', 'corpus_filter_t7')
PARAMS_TEMPLATE = {"version": 4, "fig_index": 0, "figsize_in": [8.5, 5.5]}
APPLY_TIMEOUT = 20.0          # 单份 apply 预演超时（秒）
_PY = sys.executable


def default_tmp_root():
    """跨平台取临时根目录 —— **保证返回绝对路径**。

    顺序：``TMPDIR`` → ``TEMP`` → ``TMP`` → ``tempfile.gettempdir()``。

    为什么不能沿用旧的 ``os.environ.get('TEMP', '.')``：POSIX 上 ``TEMP`` 通常
    **不存在** ⇒ 回落成 ``'.'`` ⇒ 产物写进**当前工作目录**（``./corpus_filter_work``），
    与 ``tests/test_sandbox.py:57-63`` 记录过的那次 CI 假红**同源**。
    故这里对每个环境变量候选都要求 ``os.path.isabs``：相对值（含 ``'.'`` / ``'..'``）
    一律**跳过**、继续找下一个，**绝不**静默落到 cwd。

    实测（2026-10-13，本机 longxia）：``tempfile.gettempdir()`` 对**存在的相对目录**
    （如 ``TMPDIR='reltmp'``）会返回它的 ``abspath``（锚到 cwd）；但对
    ``TMPDIR`` / ``TEMP`` / ``TMP`` 恰好等于 ``'.'`` 的情形，CPython 对
    ``os.curdir`` **不做** ``abspath`` 规范化、会把 ``'.'`` **原样返回**
    —— 所以末位回落**并非天然安全**，下面那道 ``isabs`` 断言是**真会触发**的防线
    （场景实测见 t22 验证记录）。宁可响亮失败，也绝不把产物写进 cwd。
    """
    for key in ('TMPDIR', 'TEMP', 'TMP'):
        val = os.environ.get(key)
        if val and os.path.isabs(val):
            return val
    fallback = tempfile.gettempdir()
    if not os.path.isabs(fallback):
        raise RuntimeError(
            '找不到绝对路径的临时目录（gettempdir()=%r）：请把 TMPDIR/TEMP/TMP '
            '设成绝对路径，或显式传 --workroot <绝对路径>' % fallback)
    return fallback

# ---------------------------------------------------------------------------
# manifest
# ---------------------------------------------------------------------------
def load_manifest(corpus_root):
    """读 manifest.jsonl，返回 plot==true 的 rows。"""
    mp = os.path.join(corpus_root, 'manifest.jsonl')
    rows = []
    with open(mp, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            o = json.loads(line)
            if o.get('plot') is True:
                rows.append(o)
    return rows


def stratum(source):
    if not source:
        return 'unknown'
    s = str(source)
    if s.startswith('github'):
        return 'github'
    if s.startswith('zenodo'):
        return 'zenodo'
    if 'gallery' in s:
        return 'gallery'
    return 'other'


# ---------------------------------------------------------------------------
# A. 依赖可达性预检（纯静态，in-process）
# ---------------------------------------------------------------------------
def module_imports(text):
    """收集脚本**模块级**顶层 import 的顶层包名（不含函数/类体内 import）。"""
    tree = ast.parse(text)
    names = set()

    def walk(node):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef, ast.Lambda)):
            return                      # 函数/类体内的 import 不算顶层
        if isinstance(node, ast.Import):
            for a in node.names:
                top = a.name.split('.')[0].strip()
                if top:
                    names.add(top)
        elif isinstance(node, ast.ImportFrom):
            m = node.module
            if m and not m.startswith('.'):   # 相对导入跳过
                top = m.split('.')[0].strip()
                if top:
                    names.add(top)
        for child in ast.iter_child_nodes(node):
            walk(child)

    walk(tree)
    return names


def find_spec_ok(name):
    """find_spec(顶层包名)：只查规格，不执行模块体。异常/None = 缺失。"""
    try:
        return find_spec(name) is not None
    except (ImportError, ModuleNotFoundError, AttributeError, ValueError):
        return False


def deps_check(text):
    """返回 (deps_ok, missing_list)。text 解析失败抛 SyntaxError/UnicodeDecodeError。"""
    missing = []
    try:
        tops = module_imports(text)
    except SyntaxError:
        raise
    for t in sorted(tops):
        if not find_spec_ok(t):
            missing.append(t)
    return (not missing), missing


# ---------------------------------------------------------------------------
# B. 目标可定位性预检（子进程 CLI：apply --write --dry-run --no-verify --json）
# ---------------------------------------------------------------------------
def run_apply_dryrun(script_path, params_path, cwd):
    """跑 CLI，返回 (json_obj_or_None, rc, tail, timed_out)。纯静态（dry-run）。"""
    env = dict(os.environ,
               PYTHONPATH=os.path.join(ROOT, 'src'),
               MPLBACKEND='Agg', MPLTWEAK_LANG='zh', PYTHONIOENCODING='utf-8')
    cmd = [_PY, '-m', 'mpltweak', 'apply', script_path,
           '--params', params_path, '--write', '--dry-run', '--no-verify', '--json']
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=APPLY_TIMEOUT,
                           env=env, cwd=cwd)
    except subprocess.TimeoutExpired:
        return None, -9, '', True
    out = (r.stdout or b'').decode('utf-8', 'replace').strip()
    tail = ((r.stderr or b'').decode('utf-8', 'replace').strip()[-400:])
    try:
        j = json.loads(out)
    except ValueError:
        j = None
    return j, r.returncode, tail, False


def classify_target(j):
    """从 apply --json 的 files[0] 判定目标可定位性。
    返回 (target_ok, detail)。"""
    if not j or not isinstance(j, dict) or not j.get('files'):
        return None, 'na_error: JSON 无 files'
    f0 = j['files'][0]
    changes = f0.get('changes') or []
    block_lines = f0.get('block_lines') or 0
    reason = f0.get('reason')
    pending = f0.get('pending') or []
    err = f0.get('err') or ''
    if changes or block_lines > 0:
        plan = '原位:%s 块:%d行' % (','.join(changes) if changes else '-',
                                   block_lines)
        return True, '%s | %s' % (reason, plan)
    detail = reason or '?'
    if pending:
        detail += ' | pending=%s' % ','.join(pending)
    if err:
        detail += ' | %s' % err[:160]
    return False, detail


# ---------------------------------------------------------------------------
# 金丝雀（自证：纯静态，无执行）
# ---------------------------------------------------------------------------
CANARY_SCRIPT = r'''# canary 假脚本：若被执行会写一个文件（副作用哨兵）。
import matplotlib.pyplot as plt
open('CANARY_EXECUTED.txt', 'w').write('executed!')
fig, ax = plt.subplots(figsize=(6, 4))
ax.plot([1, 2])
fig.savefig('out.png')
'''

CANARY_PKG_INIT = r'''# 假依赖包：若被 import 会写一个文件（副作用哨兵）。
open('CANARY_DEPS_EXECUTED.txt', 'w').write('deps executed!')
'''


def canary_deps(tmp):
    """依赖预检金丝雀：find_spec 一个含副作用的假包，断言未执行。"""
    pkg = os.path.join(tmp, 'canary_fakepkg')
    os.makedirs(pkg, exist_ok=True)
    with open(os.path.join(pkg, '__init__.py'), 'w', encoding='utf-8') as f:
        f.write(CANARY_PKG_INIT)
    sys.path.insert(0, tmp)
    try:
        spec = find_spec('canary_fakepkg')
        executed = os.path.exists(os.path.join(tmp, 'CANARY_DEPS_EXECUTED.txt'))
    finally:
        sys.path.remove(tmp)
    return (spec is not None) and (not executed), bool(spec), executed


def canary_script(tmp):
    """脚本金丝雀：把假脚本走完整条筛选管线（依赖预检 + apply dry-run），断言未执行。"""
    # 依赖预检
    try:
        ok, missing = deps_check(CANARY_SCRIPT)
    except SyntaxError:
        return False, 'canary 脚本语法错误（不应发生）'
    # 目标预检（同一 worker 逻辑）
    sub = os.path.join(tmp, 'canary_wd')
    os.makedirs(sub, exist_ok=True)
    script_path = os.path.join(sub, 'canary_script.py')
    with open(script_path, 'w', encoding='utf-8') as f:
        f.write(CANARY_SCRIPT)
    params_path = os.path.join(sub, 'params.json')
    pp = dict(PARAMS_TEMPLATE, script='canary_script.py')
    with open(params_path, 'w', encoding='utf-8') as f:
        json.dump(pp, f)
    j, rc, tail, timed_out = run_apply_dryrun(script_path, params_path, sub)
    t_ok, t_detail = classify_target(j)
    executed = os.path.exists(os.path.join(sub, 'CANARY_EXECUTED.txt'))
    ok = (not executed) and (rc == 0) and (not timed_out)
    note = 'deps_ok=%s target_ok=%s rc=%d timed_out=%s' % (ok, t_ok, rc, timed_out)
    return ok, note


# ---------------------------------------------------------------------------
# 语料只读快照
# ---------------------------------------------------------------------------
def corpus_snapshot(root):
    snap = {}
    for dp, _dn, fn in os.walk(root):
        for name in fn:
            p = os.path.join(dp, name)
            try:
                st = os.stat(p)
            except OSError:
                continue
            rel = os.path.relpath(p, root).replace('\\', '/')
            snap[rel] = (st.st_size, st.st_mtime_ns)
    return snap


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def read_text_any(path):
    """读脚本文本（UTF-8/UTF-8-BOM/GBK 兜底）。失败抛 UnicodeDecodeError。"""
    for enc in ('utf-8', 'utf-8-sig'):
        try:
            with open(path, 'r', encoding=enc) as f:
                return f.read()
        except UnicodeDecodeError:
            continue
    with open(path, 'r', encoding='gbk', errors='strict') as f:
        return f.read()


def main():
    ap = argparse.ArgumentParser(prog='corpus_filter_t7.py')
    ap.add_argument('--limit', type=int, default=0,
                    help='只筛前 N 份（0=全部；冒烟用）')
    ap.add_argument('--jobs', type=int, default=3, help='apply 预演并发 worker 数（有界池）')
    ap.add_argument('--corpus', default=None)
    ap.add_argument('--out', default=None)
    ap.add_argument('--workroot', default=os.path.join(
        default_tmp_root(), 'corpus_filter_work'))
    args = ap.parse_args()

    corpus_root = args.corpus or DEFAULT_CORPUS
    out_dir = args.out or DEFAULT_OUT
    workroot = args.workroot
    os.makedirs(out_dir, exist_ok=True)
    os.makedirs(workroot, exist_ok=True)

    # ---- 金丝雀（先跑，过不了直接失败，绝不进入筛选）----
    canary_tmp = os.path.join(workroot, 'canary')
    if os.path.isdir(canary_tmp):
        shutil.rmtree(canary_tmp)
    os.makedirs(canary_tmp, exist_ok=True)
    can_script_ok, can_script_note = canary_script(canary_tmp)
    can_deps_ok, can_deps_spec, can_deps_exec = canary_deps(canary_tmp)
    if not (can_script_ok and can_deps_ok):
        print('金丝雀失败：script_canary=%s deps_canary=%s（spec=%s exec=%s）'
              % (can_script_note, can_deps_ok, can_deps_spec, can_deps_exec))
        sys.exit(2)
    print('金丝雀通过：脚本副作用未触发；find_spec 假包未执行（spec=%s exec=%s）'
          % (can_deps_spec, can_deps_exec))

    # ---- manifest + plot==true ----
    rows = load_manifest(corpus_root)
    if args.limit:
        rows = rows[:args.limit]
    total_plot = len(rows)
    src_comp = collections.Counter(stratum(r.get('source')) for r in rows)
    print('语料 manifest plot==true: %d 份（分层: %s）'
          % (total_plot, dict(src_comp)))

    snap_before = corpus_snapshot(corpus_root)

    # ---- 先做 A（依赖预检，in-process，全量快）----
    deps_rows = []
    t0 = time.time()
    for i, r in enumerate(rows):
        path = os.path.join(corpus_root, r['path'])
        entry = {'path': r['path'], 'source': str(r.get('source') or ''),
                 'stratum': stratum(r.get('source'))}
        try:
            text = read_text_any(path)
            ok, missing = deps_check(text)
            entry['deps_ok'] = ok
            entry['missing_libs'] = missing
            entry['syntax_ok'] = True
        except (SyntaxError, UnicodeDecodeError) as e:
            entry['deps_ok'] = None      # na_skip（语料侧解析不了）
            entry['missing_libs'] = []
            entry['syntax_ok'] = False
            entry['deps_detail'] = '%s: %s' % (type(e).__name__, e)
        deps_rows.append(entry)
    print('依赖预检完成（%d 份，%.1fs）' % (total_plot, time.time() - t0))

    # ---- 再跑 B（目标预检，子进程 CLI，有界并发池）----
    # 每份：拷贝脚本到隔离 workdir + 写 params.json → apply --write --dry-run --no-verify
    # --json（20s 超时）。有界池：最多 jobs 个在跑，绝不一次性 spawn 全部
    # （t4 教训：spawn 并发打爆页面文件 WinError1455）。
    results = []
    running = {}          # seq -> (entry, Popen, started)
    it = iter(range(total_plot))

    def _parse(seq, entry, p, timed_out):
        out = (p.stdout.read() or b'').decode('utf-8', 'replace').strip()
        err = (p.stderr.read() or b'').decode('utf-8', 'replace').strip()
        if timed_out:
            entry['target_ok'] = None
            entry['target_detail'] = 'na_timeout'
            return
        try:
            j = json.loads(out)
        except ValueError:
            entry['target_ok'] = None
            entry['target_detail'] = ('na_error: rc=%d JSON 无法解析: %s'
                                      % (p.returncode, err[-200:]))
            return
        if p.returncode != 0 and not j.get('files'):
            entry['target_ok'] = None
            entry['target_detail'] = 'na_error: rc=%d %s' % (p.returncode, err[-200:])
            return
        t_ok, detail = classify_target(j)
        entry['target_ok'] = t_ok
        entry['target_detail'] = detail

    def _drain_finished(fin):
        for s in fin:
            _e, p, _st = running.pop(s)
            if p.returncode is None:
                p.kill()
                p.wait()
            _parse(s, _e, p, time.time() - _st > APPLY_TIMEOUT)
            results.append(_e)

    t0 = time.time()
    for seq in it:
        while len(running) >= args.jobs:
            fin = [s for s, (_e, p, _st) in running.items()
                   if p.poll() is not None or time.time() - _st > APPLY_TIMEOUT + 2]
            if fin:
                _drain_finished(fin)
                break
            time.sleep(0.2)
        entry = deps_rows[seq]
        subdir = os.path.join(workroot, 's_%05d' % seq)
        os.makedirs(subdir, exist_ok=True)
        src_path = os.path.join(corpus_root, entry['path'])
        base = os.path.basename(entry['path'])
        script_path = os.path.join(subdir, base)
        try:
            shutil.copyfile(src_path, script_path)
        except OSError as e:
            entry['target_ok'] = None
            entry['target_detail'] = 'na_error: 拷贝失败 %s' % e
            results.append(entry)
            continue
        params_path = os.path.join(subdir, 'params.json')
        pp = dict(PARAMS_TEMPLATE, script=base)
        with open(params_path, 'w', encoding='utf-8') as f:
            json.dump(pp, f)
        env = dict(os.environ,
                   PYTHONPATH=os.path.join(ROOT, 'src'),
                   MPLBACKEND='Agg', MPLTWEAK_LANG='zh', PYTHONIOENCODING='utf-8')
        cmd = [_PY, '-m', 'mpltweak', 'apply', script_path,
               '--params', params_path, '--write', '--dry-run', '--no-verify', '--json']
        try:
            p = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, env=env, cwd=subdir)
        except OSError as e:
            entry['target_ok'] = None
            entry['target_detail'] = 'na_error: spawn 失败 %s' % e
            results.append(entry)
            continue
        running[seq] = (entry, p, time.time())
    while running:
        fin = [s for s, (_e, p, _st) in running.items()
               if p.poll() is not None or time.time() - _st > APPLY_TIMEOUT + 2]
        if not fin:
            time.sleep(0.2)
            continue
        _drain_finished(fin)
    elapsed = time.time() - t0

    snap_after = corpus_snapshot(corpus_root)
    corpus_untouched = (snap_before == snap_after)

    # ---- 汇总 ----
    def cnt(pred, default_none=False):
        n = 0
        for e in results:
            v = e.get(pred)
            if v is True:
                n += 1
        return n

    deps_ok_n = cnt('deps_ok')
    deps_missing_n = sum(1 for e in results if e.get('deps_ok') is False)
    deps_skip_n = sum(1 for e in results if e.get('deps_ok') is None)
    target_ok_n = cnt('target_ok')
    target_none_n = sum(1 for e in results if e.get('target_ok') is False)
    target_na_n = sum(1 for e in results if e.get('target_ok') is None)
    both_ok = [e for e in results if e.get('deps_ok') is True
               and e.get('target_ok') is True]
    both_ok_n = len(both_ok)

    missing_counter = collections.Counter()
    for e in results:
        for lib in e.get('missing_libs') or []:
            missing_counter[lib] += 1

    # both_ok 分层
    both_strat = collections.Counter(e.get('stratum') for e in both_ok)

    # ---- 写 filter.jsonl ----
    with open(os.path.join(out_dir, 'filter.jsonl'), 'w', encoding='utf-8') as f:
        for e in results:
            rec = {
                'path': e['path'], 'source': e['source'], 'stratum': e['stratum'],
                'deps_ok': e['deps_ok'],
                'missing_libs': e.get('missing_libs') or [],
                'target_ok': e['target_ok'],
                'target_detail': e.get('target_detail') or '',
                'both_ok': (e.get('deps_ok') is True and e.get('target_ok') is True),
            }
            f.write(json.dumps(rec, ensure_ascii=False) + '\n')

    # ---- 写 FILTER.md ----
    lines = []
    a = lines.append
    a('# 语料全量静态筛选报告（t7）—— 公平口径真跑候选池')
    a('')
    a('> 纯静态筛选：**不执行任何语料脚本**（当前无文件系统沙箱）。两层筛选 + 金丝雀自证见下。')
    a('')
    a('## 0 配置与口径')
    a('- 语料: `%s`（只读；扫描前后快照逐项相等 = **未被修改**: %s）'
      % (corpus_root, '是' if corpus_untouched else '**否！**'))
    a('- plot==true: **%d 份**（manifest 口径；注意任务书写的 4,427 与实测 4,433 有差，'
      '以实测为准）。分层: %s' % (total_plot, dict(src_comp)))
    a('- 参数（与 t4 完全一致）: `%s`（单一字段 figsize）' % json.dumps(PARAMS_TEMPLATE))
    a('- 目标预检命令: `python -m mpltweak apply <脚本> --params <pp> --write --dry-run '
      '--no-verify --json`（%ds 超时）' % APPLY_TIMEOUT)
    a('- 并发: 有界 worker 池 jobs=%d（t4 教训：绝不一次 spawn 全部）' % args.jobs)
    a('- 解释器: %s；耗时: 目标预检 %.1fs' % (_PY, elapsed))
    a('')
    a('## 1 不执行证据（源码位置）')
    a('- apply.py:543-551：无 `--write` 时 apply 直接返回 `reason=preview`，**writeback 根本没被调用**'
      '（既不做分析也不执行）——所以预检必须带 `--write --dry-run` 才能拿到完整分析；')
    a('- writeback.py:2835-2843：inplace 路径 `if dry_run: return` 在备份/写盘/verify **之前**早退，'
      '只返回分析结果；')
    a('- writeback.py:2815-2826：唯一会执行脚本的 `_pending_vs_live`（2821）只在**轴级 pending 非空**'
      '时可达；本任务参数只有 figsize → `_pending` 恒空 → 永不执行；再加 `--no-verify` 使 verify=False，'
      '该分支结构性不可达（双重保险）；')
    a('- writeback.py:4572/4803-4805：块路径 `if not dry_run:` 才是真实插入+重跑（执行所在）；'
      'dry-run 走 else 纯文本预览（静态）；')
    a('- apply.py:649：`_applied = ... and not args.dry_run` → dry-run 永不报"已写回"。')
    a('')
    a('## 2 金丝雀自证（canary）')
    a('- 脚本 canary：假脚本含 `open(\'CANARY_EXECUTED.txt\',\'w\')` 副作用，走**同一**筛选管线'
      '（依赖预检 + apply dry-run）→ 结束后哨兵文件**不存在**：✅ %s' % can_script_note)
    a('- 依赖 canary：假包 `__init__.py` 含写文件副作用，走**同一** find_spec 路径 → 规格找到'
      '（spec=%s）但 **未执行**（exec=%s）：✅' % (can_deps_spec, can_deps_exec))
    a('')
    a('## 3 核心计数')
    a('| 指标 | 份数 | 说明 |')
    a('|---|---:|---|')
    a('| total_plot | %d | plot==true 全量 |' % total_plot)
    a('| deps_ok | %d | 依赖齐（全部顶层 import 可 find_spec） |' % deps_ok_n)
    a('| deps_missing | %d | 至少缺一个依赖（另解析失败 %d 份） |' % (deps_missing_n, deps_skip_n))
    a('| target_ok | %d | 静态可定位改写目标（原位或块计划） |' % target_ok_n)
    a('| target_none | %d | 定位不到（no_fig/no_axes/fail/no_change…） |' % target_none_n)
    a('| target_na | %d | 预检超时/异常/无法解析 |' % target_na_n)
    a('| **both_ok** | **%d** | **依赖齐 + 有目标（= 公平口径真跑候选池）** |' % both_ok_n)
    a('')
    a('占比：deps_ok %d/%d = %.1f%%；target_ok %d/%d = %.1f%%；both_ok %.1f%%'
      % (deps_ok_n, total_plot, 100.0 * deps_ok_n / total_plot if total_plot else 0,
         target_ok_n, total_plot,
         100.0 * target_ok_n / total_plot if total_plot else 0,
         100.0 * both_ok_n / total_plot if total_plot else 0))
    a('')
    a('## 4 both_ok 分层（来源）')
    for k, v in both_strat.most_common():
        a('- %s: %d' % (k, v))
    a('')
    a('## 5 最常缺失的库 Top 20（份数）')
    a('| 库 | 缺失份数 |')
    a('|---:|---:|')
    for lib, n in missing_counter.most_common(20):
        a('| `%s` | %d |' % (lib, n))
    a('')
    a('## 6 both_ok 样例（20 条，供后续真跑抽样）')
    for e in both_ok[:20]:
        a('- `%s`（%s）' % (e['path'], e['stratum']))
    a('')
    a('## 7 复现')
    a('```')
    a('set TEMP=D:\\Agent\\deepseek\\_tmp_tests& set TMP=D:\\Agent\\deepseek\\_tmp_tests')
    a('cd D:\\Agent\\deepseek\\mpltweak')
    a('D:\\Miniconda\\envs\\longxia\\python.exe tools/corpus_filter_t7.py --jobs 3')
    a('```')
    a('')
    a('产出: `filter.jsonl`（逐脚本 path/source/stratum/deps_ok/missing_libs/target_ok/'
      'target_detail/both_ok）+ 本文件。')
    with open(os.path.join(out_dir, 'FILTER.md'), 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')

    # ---- 控制台摘要 ----
    print('---- 筛选结果（%.1fs）----' % elapsed)
    print('判定分布: deps_ok=%d deps_missing=%d deps_skip=%d | target_ok=%d target_none=%d '
          'target_na=%d' % (deps_ok_n, deps_missing_n, deps_skip_n,
                            target_ok_n, target_none_n, target_na_n))
    print('** both_ok = %d **（依赖齐 + 有目标）' % both_ok_n)
    print('both_ok 分层: %s' % dict(both_strat))
    print('语料目录未被修改: %s' % ('是' if corpus_untouched else '否'))
    print('产出: %s' % out_dir)
    return 0


if __name__ == '__main__':
    sys.exit(main())
