#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""t39 阶段 1 只读探针：**原样跑** vs **套壳跑** 的等价性对比。

两路跑法（cwd 均为该份副本所在目录，``MPLBACKEND=Agg``）：

* **A 路 = 原样跑**：``python <脚本>``
  这就是 ``writeback.py`` 的 ``verify_run``（第 1 遍）在做的事 —— 只看退出码。
* **B 路 = 套壳跑**：``python -m mpltweak.verify <脚本> <out.json> 0``
  这就是 ``verify.py`` 的 ``verify_semantic``（第 2 遍）在做的事 —— 为了在脚本跑完后
  还能拿到 figure 对象，它**必须改动行为**（拦 ``plt.show`` / ``plt.close`` /
  ``matplotlib.use`` / ``switch_backend``、把 ``sys.stdout`` 接到 stderr）。

对比四项：**退出码 / stdout+stderr（归一化后）/ 产出文件（存在·大小·sha256）/ 墙钟耗时**。
目的：给"这两遍能不能合成一遍"一个**数据**回答，而不是靠推理。

只读保证（语料零改动）
--------------------
``corpus_union`` 一个字节都不动：每份脚本先复制到临时工作区，且在**两个内容完全相同的
副本目录**里分别跑 A 与 B ——

    <work>/<id>/a/   ← 副本，只在 A 跑之前快照
    <work>/<id>/b/   ← 同一份副本（重新铺设），只在 B 跑之前快照

产出文件（脚本自己 savefig / to_csv / open('w') 出来的东西）全部落在临时工作区里，
所以"产出文件对比"是可算的，同时语料保持只读。脚本间不共享目录 ⇒ 天然无交叉污染。

防弹窗门禁（**硬门禁，先筛后跑**）
--------------------------------
``MPLBACKEND=Agg`` 挡不住脚本**自己** ``matplotlib.use("TkAgg")`` /
``switch_backend(...)`` —— 而本探针的 **A 路是裸跑**。所以：

* 抽样阶段就用 ``tests/test_corpus_invariants.py`` 的 ``_window_risk()``（**按字节复用**，
  不 import 那个模块级脚本）把"真会开窗"与"不确定"（``plt.pause``/``ion``/
  ``waitforbuttonpress``/``ginput``/``get_current_fig_manager``）**全部排除**；
* 开跑前**自证样本内命中数 = 0**，非 0 直接 ``rc=2`` 拒跑；
* 每份在真正 spawn 之前**再筛一次**（纵深防御）。

``--screen-only`` 只做验证与留档、不跑任何脚本。

用法
----
    D:\\Miniconda\\envs\\longxia\\python.exe tools/cmp_plain_vs_instrumented.py
    ... --sample 120 --jobs 2 --timeout 60 --out <results.jsonl> --summary <SUMMARY.md>
    ... --resume          # 断点续跑（已完成的 id 直接跳过）

退出码：0 = 探针跑完（**不**代表等价）；2 = 参数/环境错。
"""

from __future__ import annotations

import argparse
import ast
import concurrent.futures as futures
import hashlib
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time

try:                                     # GBK 控制台下打印别崩（项目纪律）
    sys.stdout.reconfigure(errors='replace')
    sys.stderr.reconfigure(errors='replace')
except Exception:                        # noqa: BLE001 老解释器没有 reconfigure
    pass


HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
SRC = os.path.join(REPO, 'src')

DEFAULT_CORPUS = os.path.join(os.path.dirname(REPO), 'corpus_union')
DEFAULT_POOL = os.path.join(os.path.dirname(REPO), '_review', 'corpus_filter_t7',
                            'filter.jsonl')
DEFAULT_OUT_DIR = os.path.join(os.path.dirname(REPO), '_review',
                               'cmp_plain_vs_instrumented')
DEFAULT_WORK = os.path.join(os.path.dirname(REPO), '.tmp_t39', 'cmp_work')

SEED = 20261013

# 静态预筛：这些模式在真跑链路里会联网 / 起子进程 / 等人输入，探针不跑（计入 na_skip）。
NET_RISK = re.compile(
    r'\b(requests|urllib|urlopen|httpx|aiohttp|socket\.|ftplib|boto3|paramiko'
    r'|subprocess|os\.system|os\.popen|popen|wget|curl)\b')
STDIN_RISK = re.compile(r'\b(input\s*\(|sys\.stdin|getpass)')

# 静态标签（选样本用；也用于把"必须包含的类型"钉进样本里）
RE_CLOSE = re.compile(r'\bplt\.close\s*\(|\bfig\.close\s*\(|\bpyplot\.close\s*\(')
RE_SAVEFIG = re.compile(r'\bsavefig\s*\(')
RE_SHOW = re.compile(r'\.show\s*\(')
RE_GRID = re.compile(r'\bsubplots\s*\(|\bGridSpec\b|\badd_gridspec\b'
                     r'|\bsubplot_mosaic\b')
RE_SIDE = re.compile(r"open\s*\([^)]*['\"][wa]|to_csv\s*\(|to_excel\s*\('?|"
                     r"\bnp\.save\b|\bnp\.savez\b|savez_compressed|write_text\s*\(|"
                     r"\bos\.remove\s*\(|\bshutil\.|\bmakedirs\s*\(|\btempfile\.")

# 已知"套壳造成"的输出差异（B 路把 show / use / switch_backend 拦掉了 ⇒ A 的那些
# matplotlib 警告在 B 里不出现）。注意 matplotlib 的警告是**两行**（消息 + 源码回显），
# 所以比对单位要按"警告块"（见 _units）而不是单行，否则回显行会落进"待解释"。
BENIGN_PATTERNS = (
    ('W_SHOW_SILENCED', re.compile(r'non-GUI backend|non-interactive'
                                   r'|Matplotlib is currently using agg')),
    ('W_USE_SILENCED', re.compile(r'cannot switch to|cannot be used with'
                                  r'|matplotlib\.use')),
    ('W_VERIFY_PREFIX', re.compile(r'^\[verify\] ')),
    ('W_BACKEND', re.compile(r'FigureCanvasAgg|backend_agg')),
)

WARN_HEAD = re.compile(r'(UserWarning|RuntimeWarning|DeprecationWarning'
                       r'|MatplotlibDeprecationWarning|Warning):')

RE_ADDR = re.compile(r'0x[0-9a-fA-F]{6,}')
RE_FILEQ = re.compile(r'File "([^"]*)"')
RE_CACHE = re.compile(r'font_manager|fontlist|Building font cache')

# 归一化版本（改判据/改归一化就 +1；逐份结果里记下来，报告才能说清用的是哪一版）
NORM_VERSION = 3

# 进程级临时目录（A/B 两个子进程各自的 TMP，名字里带随机串/PID）
RE_TMPPATH = re.compile(r"[A-Za-z]:\\[^\s'\"<>]*\\Temp\\[^\s'\"]*")
RE_JOBLIB_TMP = re.compile(r'joblib_memmapping_folder_\d+_[0-9a-f]+')
RE_LEAKED_N = re.compile(r'to be \d+ leaked')
# 我们自己的两级子工作目录残留（<WORK>\a\... / <WORK>\b\...）——A 与 B 的目录名
# 本来就不同，不抹掉就会把"路径差异"误报成"行为差异"。
RE_SUBDIR_A = re.compile(r'<WORK>[\\/]a[\\/]')
RE_SUBDIR_B = re.compile(r'<WORK>[\\/]b[\\/]')

# ---------------------------------------------------------------------------
# A 档静态防弹窗门禁（硬门禁，先筛后跑）
#
# 教训（0.1.9 / 2026-10-13 本探针第一轮）：`MPLBACKEND=Agg` 挡不住脚本**自己**
# `matplotlib.use("TkAgg")` / `switch_backend(...)` —— 探针的 **A 路是裸跑**，
# 于是真在用户机器上弹了窗。从此"凡跑真实语料/陌生脚本，先过 A 档静态筛"是显式门禁。
#
# 判据**不另造一套**：按字节复用 ``tests/test_corpus_invariants.py`` 的
# ``_window_risk()`` 与它依赖的模块级常量（t23 口径：连 `plt.pause`/`ion`/
# `waitforbuttonpress`/`ginput`/`get_current_fig_manager` 这种"不确定"的也归 A 档）。
# 不 import 那个模块 —— 它是"模块级脚本"，import 会把整轮扫描真跑一遍。
# ---------------------------------------------------------------------------
TESTS_INVARIANTS = os.path.join(REPO, 'tests', 'test_corpus_invariants.py')
_WR_CONSTS = ('_WINDOW_MODULES', '_WINDOW_BACKEND_HINTS', '_MPL_BASES',
              '_MPL_INTERACTIVE_ATTRS', '_DIALOG_BASES', '_DIALOG_ATTRS')
_WR_FUNC = '_window_risk'

# 额外的"不确定"信号：动态导入 / 动态执行 —— 静态判据看不进字符串里到底导了什么。
# 归 A 档（宁可少跑，也不弹窗）。
RE_DYNAMIC = re.compile(r'\bimportlib\b|\b__import__\s*\(|\bexec\s*\(|\beval\s*\(')


def load_window_risk():
    """把 tests/ 里的 ``_window_risk`` 摘出来编译执行。返回 (fn, prov, err)。"""
    try:
        with open(TESTS_INVARIANTS, 'r', encoding='utf-8') as f:
            src = f.read()
        sha = hashlib.sha256(src.encode('utf-8')).hexdigest()
        tree = ast.parse(src)
    except (OSError, SyntaxError, UnicodeDecodeError) as e:
        return None, {}, '%s: %s' % (type(e).__name__, e)
    pieces, found = [], set()
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == _WR_FUNC:
            pieces.append(node)
            found.add(_WR_FUNC)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id in _WR_CONSTS:
                    pieces.append(node)
                    found.add(t.id)
    missing = sorted(({_WR_FUNC} | set(_WR_CONSTS)) - found)
    if missing:
        return None, {}, '复用失败：tests 里找不到 %s' % missing
    ns = {'ast': ast}
    try:
        exec(compile(ast.Module(body=pieces, type_ignores=[]),
                     TESTS_INVARIANTS, 'exec'), ns)
    except Exception as e:                       # noqa: BLE001
        return None, {}, '%s: %s' % (type(e).__name__, e)
    prov = {'tests_src': os.path.relpath(TESTS_INVARIANTS, REPO).replace('\\', '/'),
            'tests_src_sha256': sha, 'reused': sorted(found)}
    return ns[_WR_FUNC], prov, ''


def screen_script(text: str, window_risk_fn):
    """A 档门禁：返回 (ok, reason, evidence)。ok=False 的脚本**一律不跑**。"""
    if window_risk_fn is None:
        return False, 'no_screen_available', ['_window_risk 复用失败']
    try:
        ast.parse(text)
    except SyntaxError as e:
        # 解析不了 = 判不了 = 不跑（"不确定也排除"）。这类脚本在 A 路也只会 SyntaxError。
        return False, 'syntax_unparseable', ['SyntaxError: %s' % e]
    ev = window_risk_fn(text)
    if ev:
        return False, 'window_risk_A', list(ev)
    m = RE_DYNAMIC.search(text)
    if m:
        return False, 'dynamic_code_unknown', [m.group(0)]
    return True, '', []


# ---------------------------------------------------------------------------
# 语料 / 样本
# ---------------------------------------------------------------------------
def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def load_pool(pool_path: str):
    rows = []
    with open(pool_path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    return rows


def read_text(path: str) -> str:
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return f.read()
    except (OSError, UnicodeDecodeError):
        try:
            with open(path, 'r', encoding='utf-8', errors='replace') as f:
                return f.read()
        except OSError:
            return ''


def static_tags(src: str):
    tags = set()
    if RE_CLOSE.search(src):
        tags.add('close_call')
    if RE_SAVEFIG.search(src):
        tags.add('savefig')
    if RE_SHOW.search(src):
        tags.add('show')
    if RE_GRID.search(src):
        tags.add('grid')
    if RE_SIDE.search(src):
        tags.add('side')
    # "结尾就是 close(fig)" —— 取最后一行非空非注释
    tail = None
    for ln in reversed(src.splitlines()):
        s = ln.strip()
        if s and not s.startswith('#'):
            tail = s
            break
    if tail and RE_CLOSE.search(tail):
        tags.add('ends_with_close')
    if RE_SAVEFIG.search(src) and not RE_SHOW.search(src) and not RE_CLOSE.search(src):
        tags.add('savefig_only')
    if RE_SHOW.search(src) and not RE_SAVEFIG.search(src):
        tags.add('show_only')
    if NET_RISK.search(src):
        tags.add('net_risk')
    if STDIN_RISK.search(src):
        tags.add('stdin_risk')
    return sorted(tags)


def build_sample(rows, corpus: str, sample_n: int, per_stratum: int,
                 window_risk_fn):
    """分层抽样 + 把验收点名的六类**钉**进样本里。

    **先筛后抽**：只有过了 A 档防弹窗门禁（``screen_script``）的脚本才进候选，
    所以样本里 A 档命中恒为 0 —— 这是硬门禁，没有放行开关。
    """
    rng = random.Random(SEED)
    by_stratum = {}
    for r in rows:
        by_stratum.setdefault(r.get('stratum') or 'unknown', []).append(r)

    picked = {}          # path -> record

    def take(rec, why):
        it = picked.setdefault(rec['path'], {'rec': rec, 'why': set()})
        it['why'].add(why)

    # 1) 六个必含类：先把满足条件的脚本钉进去（每类最多 6 份，优先 both_ok）
    need = {
        'ends_with_close': lambda t, r: 'ends_with_close' in t,
        'savefig_only': lambda t, r: 'savefig_only' in t,
        'show_only': lambda t, r: 'show_only' in t,
        'deps_missing': lambda t, r: not r.get('deps_ok'),
        'grid': lambda t, r: 'grid' in t,
        'side_effect': lambda t, r: 'side' in t,
    }
    screen_stats = {'n_rows': len(rows), 'n_missing': 0, 'excluded': {},
                    'n_screened_in': 0}
    cands = {}
    for r in rows:
        p = os.path.join(corpus, r['path'])
        if not os.path.isfile(p):
            screen_stats['n_missing'] += 1
            continue
        text = read_text(p)
        ok, reason, _ev = screen_script(text, window_risk_fn)
        if not ok:
            screen_stats['excluded'][reason] = \
                screen_stats['excluded'].get(reason, 0) + 1
            continue
        screen_stats['n_screened_in'] += 1
        rr = dict(r)
        rr['_tags'] = static_tags(text)
        cands[rr['path']] = rr
    for label, pred in need.items():
        hit = [r for r in cands.values()
               if pred(r['_tags'], r) and 'net_risk' not in r['_tags']]
        hit.sort(key=lambda r: (not r.get('both_ok'), r['path']))
        for r in hit[:6]:
            take(r, label)

    # 2) 分层填充：三类都要有，both_ok 优先
    per = {}
    for st, lst in sorted(by_stratum.items()):
        lst2 = [cands[r['path']] for r in lst if r['path'] in cands
                and 'net_risk' not in cands[r['path']]['_tags']]
        lst2.sort(key=lambda r: (not r.get('both_ok'), r['path']))
        rng.shuffle(lst2)
        lst2.sort(key=lambda r: not r.get('both_ok'))   # 稳定：both_ok 在前
        per[st] = lst2

    for st, lst in per.items():
        n = 0
        for r in lst:
            if n >= per_stratum:
                break
            if r['path'] not in picked:
                take(r, 'stratum:%s' % st)
                n += 1
        # 该层还没填满（picked 里已有）也要保证"三类都有"
        if not any(k.startswith('stratum:%s' % st) for it in picked.values()
                   for k in it['why']):
            for r in lst:
                take(r, 'stratum:%s' % st)
                break

    # 3) 补足总数
    if len(picked) < sample_n:
        pool_all = [r for r in cands.values() if 'net_risk' not in r['_tags']]
        pool_all.sort(key=lambda r: (not r.get('both_ok'), r['path']))
        rng.shuffle(pool_all)
        pool_all.sort(key=lambda r: not r.get('both_ok'))
        for r in pool_all:
            if len(picked) >= sample_n:
                break
            if r['path'] not in picked:
                take(r, 'fill')

    out = []
    for path in sorted(picked):
        it = picked[path]
        out.append({'path': path, 'rec': it['rec'], 'why': sorted(it['why']),
                    'tags': it['rec']['_tags']})
    return out, screen_stats


# ---------------------------------------------------------------------------
# 跑一份脚本（A / B 共用）
# ---------------------------------------------------------------------------
def _seed_dir(dst: str, script_src: str, budget_bytes: int = 12 << 20):
    """把脚本 + 同目录兄弟文件复制进 dst（保持相对路径能解析）。

    同目录的 ``.py``（本地 import）全拷；非 ``.py``（数据）单文件 ≤2MB 且总量有预算。
    超出预算就不拷非 py —— 这只会让"缺数据"在 A/B 两侧**同样**失败，不制造虚假差异。
    """
    os.makedirs(dst, exist_ok=True)
    src_dir = os.path.dirname(script_src)
    total = 0
    n_py = n_other = 0
    for name in sorted(os.listdir(src_dir)):
        sp = os.path.join(src_dir, name)
        if not os.path.isfile(sp):
            continue
        try:
            size = os.path.getsize(sp)
        except OSError:
            continue
        if name.endswith('.py'):
            shutil.copy2(sp, os.path.join(dst, name))
            n_py += 1
            continue
        if size > (2 << 20) or total + size > budget_bytes:
            continue
        shutil.copy2(sp, os.path.join(dst, name))
        total += size
        n_other += 1
    return n_py, n_other, total


def _snapshot(root: str):
    """产出文件快照：相对路径 -> (size, sha256)。跳过缓存目录。"""
    out = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames
                       if d not in ('__pycache__', '.tweak_params', '.git')]
        for fn in filenames:
            fp = os.path.join(dirpath, fn)
            rel = os.path.relpath(fp, root).replace('\\', '/')
            try:
                out[rel] = (os.path.getsize(fp), _sha256_file(fp))
            except OSError:
                out[rel] = (-1, '')
    return out


def _artifacts(before, after):
    """before/after 快照 → 本次运行"产出/改动"的文件。"""
    out = {}
    for rel, meta in after.items():
        if rel.endswith('.json') and rel == 'state.json':
            continue
        if before.get(rel) != meta:
            out[rel] = list(meta)
    return out


def _run(cmd, cwd, env, timeout):
    t0 = time.time()
    try:
        r = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True,
                           timeout=timeout)
        wall = time.time() - t0
        return {'rc': r.returncode, 'out': r.stdout, 'err': r.stderr,
                'wall': wall, 'timeout': False, 'spawn_err': ''}
    except subprocess.TimeoutExpired as e:
        wall = time.time() - t0
        return {'rc': None, 'out': e.stdout or b'', 'err': e.stderr or b'',
                'wall': wall, 'timeout': True, 'spawn_err': ''}
    except Exception as e:                       # noqa: BLE001
        return {'rc': None, 'out': b'', 'err': b'', 'wall': time.time() - t0,
                'timeout': False, 'spawn_err': '%s: %s' % (type(e).__name__, e)}


def _dec(b) -> str:
    if not b:
        return ''
    return b.decode('utf-8', 'replace')


def _decode_enc(b) -> str:
    """探针自诊断用：记录字节流实际用的编码（不参与判等）。"""
    if not b:
        return 'empty'
    try:
        b.decode('utf-8')
        return 'utf-8'
    except UnicodeDecodeError:
        pass
    for enc in ('cp936', 'latin-1'):
        try:
            b.decode(enc)
            return enc
        except UnicodeDecodeError:
            continue
    return 'mixed'


# ---------------------------------------------------------------------------
# 归一化 / 比对
# ---------------------------------------------------------------------------
def normalize(text: str, repl) -> list:
    """把一段输出归一化成"可比的行表"。

    只抹掉**已知的量具噪声**（临时工作区路径 / 脚本绝对路径 / 内存地址 /
    traceback 里的文件路径），以及套壳自己加的那一行 ``[verify] ...``。
    绝不抹掉脚本自己的 print 内容 —— 那是要看的东西。
    """
    t = text.replace('\r\n', '\n').replace('\r', '\n')
    for a, b in repl:
        if a:
            t = t.replace(a, b)
    # 子工作目录（a / b）残留：`repl` 里 case_dir 是 d 的前缀，先换长的那条会把
    # "a"/"b" 留在路径里 —— 这里再兜一次底，A 与 B 的目录差异必须抹掉。
    t = RE_SUBDIR_A.sub('<WORK>/', t)
    t = RE_SUBDIR_B.sub('<WORK>/', t)
    t = RE_ADDR.sub('0xADDR', t)
    t = RE_FILEQ.sub('File "<F>"', t)
    t = RE_JOBLIB_TMP.sub('<JOBLIB_TMP>', t)
    t = RE_LEAKED_N.sub('to be N leaked', t)
    t = RE_TMPPATH.sub('<TMP>', t)
    lines = []
    for ln in t.split('\n'):
        ln = ln.rstrip()
        if not ln:
            continue
        # `[verify] ...` **必须留着**：它是套壳自己的错误通道，也正是
        # "B 为什么取不到图状态"的唯一线索（早先版本把它滤掉了，结果 8 份
        # "A 跑通、B 取不到状态"的样本全被判成"待解释"）。它在 benign_class
        # 里归 `W_VERIFY_PREFIX`，不会污染"待解释"清单。
        lines.append(ln)
    return lines


def benign_class(line: str):
    for label, rx in BENIGN_PATTERNS:
        if rx.search(line):
            return label
    return None


def _units(lines):
    """把行表折成"比对单位"：一条警告（消息 + 源码回显 + 后续缩进续行）算一个单位。

    不折的话，`UserWarning: FigureCanvasAgg is non-interactive...` 的回显行
    `  plt.show()` 会单独留着，看起来像"待解释的差异"，而它其实同一因。
    """
    units, i, n = [], 0, len(lines)
    while i < n:
        ln = lines[i]
        if WARN_HEAD.search(ln) or ln.lstrip().startswith('Traceback'):
            u = [ln]
            i += 1
            while i < n and lines[i][:1] in (' ', '\t'):
                u.append(lines[i])
                i += 1
            units.append('\n'.join(u))
        else:
            units.append(ln)
            i += 1
    return units


def _ms_diff(a_lines, b_lines):
    from collections import Counter
    ua, ub = _units(a_lines), _units(b_lines)
    ca, cb = Counter(ua), Counter(ub)
    only_a = sorted((ca - cb).elements())
    only_b = sorted((cb - ca).elements())
    return only_a, only_b


def _benign_split(lines_a_only, lines_b_only):
    """把差异行分成"已知套壳造成"与"待解释"。"""
    def split(lst):
        known, unknown = [], []
        for ln in lst:
            k = benign_class(ln)
            (known if k else unknown).append({'class': k or '', 'line': ln[:2000]})
        return known, unknown
    ka, ua = split(lines_a_only)
    kb, ub = split(lines_b_only)
    return ka, ua, kb, ub


# --- 二级归一化：套壳的 runpy 多包了一层，警告的"源码位置"行号会漂 ------------
RE_WARN_FRAME = re.compile(r'File "<F>", line \d+')
RE_WARN_COLON_N = re.compile(r':\d+:')
RE_WARN_LINENO = re.compile(r'\bline \d+\b')

# --- 脚本自身报错的诊断输出（A 路直接可见；B 路被套壳收成一行 [verify] ...）----
RE_TB = re.compile(r'^(Traceback \(most recent call last\):|'
                   r'The above exception was the direct cause of the following '
                   r'exception:|'
                   r'During handling of the above exception, another exception '
                   r'occurred:)')
RE_EXC = re.compile(r'^[A-Za-z_][\w.]*(Error|Exception|Warning|Interrupt|Exit)\b')
RE_EXC_IND = re.compile(r'^\s+[A-Za-z_][\w.]*(Error|Exception|Warning)\b')

# 墙上时钟（statsmodels 的 summary 会把当前时间打进输出）——纯环境噪声
RE_CLOCK = re.compile(r'\b\d{1,2}:\d{2}:\d{2}\b')

# --- 非确定性产出格式：matplotlib 会往文件里写时间戳 / 随机 id ---------------
# 实测：同一份脚本、同一份副本目录，SVG/EPS/PDF 的**大小完全相同**但 sha256 不同
# （SVG 里 <dc:date> 与元素 id 随机；EPS/PDF 里 CreationDate）。PNG 不带时间戳，
# 逐字节可复现。所以这几个格式按"大小相同"判等，并把豁免记进报告。
NON_DET_EXTS = ('.svg', '.eps', '.pdf', '.ps', '.pgf')


def _norm2(unit):
    lines_u = unit.split('\n')
    # 警告的"源码回显"行随**调用栈**变化：同一句 plt.show() 在 A 路回显用户脚本那行，
    # 在 B 路（runpy 多包一层）回显 runpy 那行。只留警告消息本身，回显整行丢掉。
    if WARN_HEAD.search(lines_u[0]):
        lines_u = lines_u[:1]
    u = '\n'.join(lines_u)
    u = RE_WARN_FRAME.sub('File "<F>", line N', u)
    u = RE_WARN_COLON_N.sub(':<N>:', u)
    u = RE_WARN_LINENO.sub('line N', u)
    u = RE_CLOCK.sub('<TIME>', u)
    return u


def _diag_only(units):
    """整批单位是否都是"脚本自己报错"的诊断输出？是就返回 True。"""
    if not units:
        return False
    for u in units:
        head = u.split('\n')[0]
        if RE_TB.match(head) or RE_EXC.match(head) or RE_EXC_IND.match(head):
            continue
        return False
    return True


def _art_eq(art_a, art_b):
    """产出文件判等。返回 (same, reason, nondet_exempt, only_a, only_b)。"""
    only_a = {k: v for k, v in art_a.items() if art_b.get(k) != v}
    only_b = {k: v for k, v in art_b.items() if art_a.get(k) != v}
    if set(art_a) != set(art_b):
        return (False, 'keys:%s|%s' % (sorted(art_a), sorted(art_b)), [],
                only_a, only_b)
    exempt = []
    for k in sorted(art_a):
        sa, ha = art_a[k][0], art_a[k][1]
        sb, hb = art_b[k][0], art_b[k][1]
        if sa != sb:
            return False, 'size:%s %s≠%s' % (k, sa, sb), exempt, only_a, only_b
        if ha != hb:
            if os.path.splitext(k)[1].lower() in NON_DET_EXTS:
                exempt.append(k)
                continue
            return False, 'sha256:%s' % k, exempt, only_a, only_b
    return True, '', exempt, only_a, only_b


def _art_from_record(cmp_):
    """从已落盘的记录重建 _art_eq 的结果。

    **优先用直接落盘的判决**（``artifacts_same`` / ``artifacts_reason`` /
    ``artifacts_nondet_exempt``）——豁免已经烘进那个布尔里了，不需要重算；
    ``art_only_a/b`` 只在确实是"文件→[size,sha]"映射时才拿来列明细。
    """
    only_a = cmp_.get('art_only_a') or {}
    only_b = cmp_.get('art_only_b') or {}
    if not _looks_like_art_map(only_a) or not _looks_like_art_map(only_b):
        only_a, only_b = {}, {}
    if 'artifacts_same' in cmp_:
        return (bool(cmp_.get('artifacts_same')), cmp_.get('artifacts_reason') or '',
                list(cmp_.get('artifacts_nondet_exempt') or []), only_a, only_b)
    if not only_a and not only_b:
        return True, '', [], only_a, only_b
    exempt = []
    for k in sorted(set(only_a) | set(only_b)):
        if k not in only_a or k not in only_b:
            return False, 'keys:%s' % k, exempt, only_a, only_b
        if only_a[k][0] != only_b[k][0]:
            return False, 'size:%s' % k, exempt, only_a, only_b
        if os.path.splitext(k)[1].lower() in NON_DET_EXTS:
            exempt.append(k)
            continue
        return False, 'sha256:%s' % k, exempt, only_a, only_b
    return True, '', exempt, only_a, only_b


def _looks_like_art_map(d):
    """``{相对路径: [size, sha256]}`` 的形状校验（防止把别的映射误当产出文件表）。"""
    if not isinstance(d, dict):
        return False
    for k, v in d.items():
        if not isinstance(v, list) or len(v) != 2:
            return False
        if not isinstance(v[0], int) or not isinstance(v[1], str):
            return False
        if '\n' in k:
            return False
    return True


def classify(a, b, art_result):
    """把一份样本的 A/B 观测折成 (cmp, verdict)。**唯一**的判定实现。"""
    art_same, art_reason, art_exempt, art_only_a, art_only_b = art_result
    cmp_ = {}
    if a.get('timeout') or b.get('timeout'):
        return ({'timeout_a': a.get('timeout'), 'timeout_b': b.get('timeout'),
                 'wall_a': a.get('wall'), 'wall_b': b.get('wall')},
                'na_timeout')

    # --- 1) 退出码语义 -----------------------------------------------------
    a_ok = a.get('rc') == 0
    b_ok = b.get('rc') == 0
    if a_ok and b_ok:
        exit_class = 'EQ_OK'
    elif not a_ok and not b_ok:
        exit_class = 'EQ_FAIL'
    elif a_ok and not b_ok:
        txt = b.get('stderr_head') or ''
        exit_class = ('B_NOSTATE_CLOSED' if '没有留下任何图' in txt
                      else 'B_NOSTATE_OTHER')
    else:
        exit_class = 'B_OK_BUT_A_FAIL'      # 不该出现：套壳反而"跑通"了
    cmp_['a_rc'] = a.get('rc')
    cmp_['b_rc'] = b.get('rc')
    cmp_['exit_class'] = exit_class
    cmp_['exit_same'] = (a_ok == b_ok)

    # --- 2) stdout+stderr（合流后按"警告块"为单位比）------------------------
    only_a, only_b = _ms_diff(a.get('lines') or [], b.get('lines') or [])
    ka, ua, kb, ub = _benign_split(only_a, only_b)
    ua_txt = [x['line'] for x in ua]
    ub_txt = [x['line'] for x in ub]
    cmp_['pad_only_a'] = len(only_a)
    cmp_['pad_only_b'] = len(only_b)
    cmp_['pad_benign_a'] = ka
    cmp_['pad_benign_b'] = kb
    cmp_['pad_strict_same'] = not only_a and not only_b

    # 二级：抹掉"套壳多包一层 runpy"造成的警告行号漂移
    from collections import Counter
    lvl2_same = Counter(map(_norm2, ua_txt)) == Counter(map(_norm2, ub_txt))
    cmp_['pad_lvl2_same'] = lvl2_same
    if lvl2_same:
        cmp_['pad_unknown_a'] = []
        cmp_['pad_unknown_b'] = []
        cmp_['pad_class'] = 'frame_number_drift'
    elif exit_class == 'EQ_FAIL' and not ub_txt and _diag_only(ua_txt):
        # 两侧都跑不通；A 路直接吐完整 traceback，B 路把它收成一行 [verify] ...
        cmp_['pad_unknown_a'] = ua
        cmp_['pad_unknown_b'] = ub
        cmp_['pad_class'] = 'wrapper_err_channel'
    else:
        cmp_['pad_unknown_a'] = ua
        cmp_['pad_unknown_b'] = ub
        cmp_['pad_class'] = 'unexplained'
    cmp_['pad_core_same'] = (cmp_['pad_class'] in
                             ('frame_number_drift', 'wrapper_err_channel'))

    # --- 3) 产出文件 -------------------------------------------------------
    cmp_['artifacts_same'] = art_same
    cmp_['artifacts_reason'] = art_reason
    cmp_['artifacts_nondet_exempt'] = art_exempt
    cmp_['art_only_a'] = art_only_a
    cmp_['art_only_b'] = art_only_b

    # --- 4) 墙钟 -----------------------------------------------------------
    cmp_['wall_a'] = a.get('wall')
    cmp_['wall_b'] = b.get('wall')
    cmp_['wall_sum'] = round((a.get('wall') or 0) + (b.get('wall') or 0), 3)
    cmp_['wall_ratio'] = (round(b['wall'] / a['wall'], 3)
                          if (a.get('wall') or 0) > 0.02 else None)

    # --- 判定 --------------------------------------------------------------
    if exit_class == 'EQ_OK' and cmp_['pad_core_same'] and art_same:
        verdict = 'equivalent'
    elif exit_class == 'EQ_FAIL' and cmp_['pad_core_same'] and art_same:
        verdict = 'equivalent_fail'
    elif (exit_class == 'B_NOSTATE_CLOSED' and cmp_['pad_core_same']
          and art_same):
        verdict = 'diff_wrapper_nostate'
    elif cmp_['pad_core_same'] and not art_same:
        verdict = 'diff_artifacts'
    elif cmp_['pad_core_same'] and exit_class == 'EQ_OK':
        verdict = 'diff_wrapper_benign'
    elif cmp_['pad_class'] == 'unexplained':
        verdict = 'diff_unknown'
    else:
        verdict = 'diff_wrapper'
    return cmp_, verdict


def one_case(sample, corpus: str, python: str, work_root: str, timeout: float):
    """跑一份脚本的 A/B 两路，返回一条结果记录。"""
    rel = sample['path']
    rec = sample['rec']
    src_abs = os.path.join(corpus, rel)
    pid = hashlib.sha256(rel.encode('utf-8')).hexdigest()[:16]
    case_dir = os.path.join(work_root, pid)
    dir_a = os.path.join(case_dir, 'a')
    dir_b = os.path.join(case_dir, 'b')

    base = {
        'id': pid, 'path': rel, 'stratum': rec.get('stratum'),
        'source': rec.get('source'), 'why': sample['why'], 'tags': sample['tags'],
        'both_ok': bool(rec.get('both_ok')), 'deps_ok': bool(rec.get('deps_ok')),
        'missing_libs': rec.get('missing_libs') or [],
        'corpus_sha256': _sha256_file(src_abs),
        'norm_version': NORM_VERSION,
    }
    if 'net_risk' in sample['tags'] or 'stdin_risk' in sample['tags']:
        base.update({'verdict': 'na_skip',
                     'na_reason': ('net_risk' if 'net_risk' in sample['tags']
                                   else 'stdin_risk')})
        return base
    # 纵深防御：**跑之前再筛一次**（抽样过筛 ≠ 这份此刻仍过筛）。命中即不跑。
    _ok, _reason, _ev = screen_script(read_text(src_abs), WINDOW_RISK)
    if not _ok:
        base.update({'verdict': 'na_skip',
                     'na_reason': 'rescreen:%s' % _reason,
                     'screen_evidence': _ev})
        return base

    env = dict(os.environ)
    env['MPLBACKEND'] = 'Agg'
    # 硬钉输出编码（**不能**依赖调用方 shell）：Windows GBK 控制台下，脚本若打印
    # U+2011 这类字符会在写 stdout 时 UnicodeEncodeError ⇒ A 臂 rc=1 ⇒ 被当成
    # "两侧退出码不一致"的噪声（t40 复核复跑时正是这么拿到噪声的；阶段 1 钉了它
    # 才是干净的）。
    env['PYTHONIOENCODING'] = 'utf-8'
    env.pop('MPLTWEAK_SANDBOX_ROOT', None)     # 沙箱会拦脚本写盘，探针要放行
    parts = [p for p in (env.get('PYTHONPATH') or '').split(os.pathsep) if p]
    if SRC not in parts:
        parts.insert(0, SRC)
    env['PYTHONPATH'] = os.pathsep.join(parts)

    results = {}
    artifacts = {}
    seeds = {}
    try:
        for tag, d in (('a', dir_a), ('b', dir_b)):
            if os.path.isdir(d):
                shutil.rmtree(d, ignore_errors=True)
            n_py, n_other, tot = _seed_dir(d, src_abs)
            seeds[tag] = {'n_py': n_py, 'n_other': n_other, 'other_bytes': tot}
            script_abs = os.path.join(d, os.path.basename(rel))
            before = _snapshot(d)
            if tag == 'a':
                cmd = [python, script_abs]
            else:
                state_path = os.path.join(case_dir, 'state.json')
                if os.path.exists(state_path):
                    os.remove(state_path)
                cmd = [python, '-m', 'mpltweak.verify', script_abs, state_path, '0']
            r = _run(cmd, d, env, timeout)
            after = _snapshot(d)
            artifacts[tag] = _artifacts(before, after)
            repl = [(script_abs, '<SCRIPT>'), (d, '<WORK>'), (case_dir, '<WORK>')]
            out_lines = normalize(_dec(r['out']), repl)
            err_lines = normalize(_dec(r['err']), repl)
            results[tag] = {
                'rc': r['rc'], 'timeout': r['timeout'], 'spawn_err': r['spawn_err'],
                'wall': round(r['wall'], 3),
                'n_out_lines': len(out_lines), 'n_err_lines': len(err_lines),
                'enc_out': _decode_enc(r['out']), 'enc_err': _decode_enc(r['err']),
                'lines': out_lines + err_lines,
                'stderr_head': '\n'.join(err_lines[-12:])[:1500],
                'out_head': '\n'.join(out_lines[:12])[:800],
            }
            if tag == 'b':
                tot_b = 0
                try:
                    with open(state_path, 'r', encoding='utf-8') as f:
                        envj = json.load(f)
                    tot_b = len((envj.get('axes') or []))
                except Exception:                # noqa: BLE001 取不到就是取不到
                    tot_b = -1
                results['b']['state_axes'] = tot_b
        base['seeds'] = seeds

        a, b = results['a'], results['b']
        if a['timeout'] or b['timeout']:
            cmp_, verdict = classify(a, b, (True, '', [], {}, {}))
            base.update({'a': a, 'b': b, 'cmp': cmp_, 'verdict': verdict})
            return base
        cmp_, verdict = classify(a, b,
                                 _art_eq(artifacts['a'], artifacts['b']))
        base.update({'a': a, 'b': b, 'cmp': cmp_, 'verdict': verdict})
        return base
    except Exception as e:                       # noqa: BLE001 单份崩了不能带走整轮
        base.update({'verdict': 'na_error',
                     'na_reason': '%s: %s' % (type(e).__name__, e)})
        return base
    finally:
        if not KEEP_WORK:
            shutil.rmtree(case_dir, ignore_errors=True)


KEEP_WORK = False
WINDOW_RISK = None          # main() 里装载（tests/ 版本）；one_case 纵深防御复用
WINDOW_RISK_PROV = {}


# ---------------------------------------------------------------------------
# 汇总
# ---------------------------------------------------------------------------
def _load_nondet(args):
    """读 A vs A′ 对照（若已跑过），返回 {'paths': set, 'rate': float|None}。"""
    p = os.path.join(os.path.dirname(os.path.abspath(args.out)), 'NONDET.jsonl')
    if not os.path.isfile(p):
        return {'paths': set(), 'rate': None, 'n': 0}
    rows = []
    try:
        with open(p, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
    except (OSError, ValueError):
        return {'paths': set(), 'rate': None, 'n': 0}
    ok = [r for r in rows if not str(r.get('verdict', '')).startswith('na_')]
    eq = [r for r in ok if r.get('verdict') in ('equivalent', 'equivalent_fail')]
    diff = {r['path'] for r in ok
            if r.get('verdict') not in ('equivalent', 'equivalent_fail')}
    return {'paths': diff, 'rate': (100.0 * len(eq) / len(ok)) if ok else None,
            'n': len(ok), 'n_diff': len(diff)}


def summarize(recs, args, screen_stats=None):
    from collections import Counter, defaultdict
    done = [r for r in recs if r.get('verdict') not in ('na_skip', 'na_error',
                                                        'na_timeout')]
    n = len(done)
    vc = Counter(r['verdict'] for r in recs)
    strat = Counter(r.get('stratum') for r in recs)
    cat = Counter()
    for r in recs:
        for t in r.get('tags') or []:
            cat[t] += 1

    eq_ok = sum(1 for r in done if r['verdict'] in ('equivalent',))
    eq_fail = sum(1 for r in done if r['verdict'] == 'equivalent_fail')
    nonstate = sum(1 for r in done if r['verdict'] == 'diff_wrapper_nostate')
    benign = sum(1 for r in done if r['verdict'] == 'diff_wrapper_benign')
    unknown = [r for r in done if r['verdict'] in ('diff_unknown', 'diff_wrapper')]
    artdiff = [r for r in done if r['verdict'] == 'diff_artifacts']
    exit_same = sum(1 for r in done if r['cmp'].get('exit_same'))
    core_same = sum(1 for r in done if r['cmp'].get('pad_core_same'))
    strict_same = sum(1 for r in done if r['cmp'].get('pad_strict_same'))
    art_same = sum(1 for r in done if r['cmp'].get('artifacts_same'))

    walls_a = [r['cmp']['wall_a'] for r in done]
    walls_b = [r['cmp']['wall_b'] for r in done]
    walls_sum = [r['cmp']['wall_sum'] for r in done]

    def med(v):
        if not v:
            return 0.0
        s = sorted(v)
        return s[len(s) // 2]

    lines = []
    A = lines.append
    A('# 探针：原样跑 vs 套壳跑 等价性（t39 阶段 1）')
    A('')
    A('> **只读**：语料 `corpus_union` 逐份 sha256 前后对账见 §6；脚本一律复制到临时')
    A('> 工作区（A/B 两个内容相同的副本目录）后分别跑，产出文件落在临时区。')
    A('')
    A('## 0 配置')
    A('- 候选池: `%s`（sha256 `%s`）' % (args.pool, _sha256_file(args.pool)))
    A('- 语料根: `%s`' % args.corpus)
    A('- 解释器: `%s`' % args.python)
    A('- 抽样: seed=%d，目标 %d 份，每层 ≥%d' % (SEED, args.sample,
                                                 args.per_stratum))
    A('- 单份超时: %.0fs，jobs=%d' % (args.timeout, args.jobs))
    A('- A 路: `python <脚本>`（cwd=副本目录，MPLBACKEND=Agg）')
    A('- B 路: `python -m mpltweak.verify <脚本> <state.json> 0`（同 cwd/同 env）')
    A('')
    A('## 0b A 档静态防弹窗门禁（硬门禁：先筛后跑）')
    A('')
    A('`MPLBACKEND=Agg` **挡不住脚本自己** `matplotlib.use("TkAgg")` / ')
    A('`switch_backend(...)` —— 而本探针的 **A 路是裸跑**，所以静态筛是主防线')
    A('（本探针第一轮就是因此真弹了窗，轮次作废重跑）。')
    A('')
    A('- 判据来源：按字节复用 `%s` 的 `_window_risk()` + 其模块级常量'
      % (WINDOW_RISK_PROV.get('tests_src') or '(未装载)'))
    A('   （sha256 `%s`；**不 import 该模块** —— 它是模块级脚本，import 会真跑整轮）'
      % (WINDOW_RISK_PROV.get('tests_src_sha256') or ''))
    A('- 口径（t23，与 tests 完全一致）：GUI 库 `import`；`use(<GUI后端>)` /')
    A('  `switch_backend(<GUI后端>)`；`filedialog`/`messagebox`/`askopenfilename` 等；')
    A('  `webbrowser.open()`；**以及"不确定"的** `plt.pause|ion|iwait|')
    A('  waitforbuttonpress|ginput|get_current_fig_manager`。**宁可少跑，也不弹窗。**')
    A('- 探针额外加严（比 tests 更保守）：`ast.parse` 失败 ⇒ `syntax_unparseable` 排除；')
    A('  源码里出现 `importlib` / `__import__(` / `exec(` / `eval(`（静态判据看不进')
    A('  字符串里到底导了什么）⇒ `dynamic_code_unknown` 排除。')
    if screen_stats:
        A('- 池 %d 行 → 过筛 **%d** 份；排除明细：%s（另 %d 行文件不存在）'
          % (screen_stats.get('n_rows', 0), screen_stats.get('n_screened_in', 0),
             screen_stats.get('excluded') or {}, screen_stats.get('n_missing', 0)))
    A('- **跑前自证**：最终样本内 A 档命中数 = **0**（非 0 时探针 rc=2 直接拒跑）；')
    A('  且每份在真正 spawn 之前**再筛一次**（纵深防御，见 `one_case`）。')
    A('- 补证：A/B 两路子进程 env 均显式 `MPLBACKEND=Agg`。')
    A('- 轮次留档（同一目录下的历史文件，**不作为结论依据**）：')
    A('  `results_UNSCREENED_ROUND1_VOID.jsonl`（未过筛轮次，跑到 70/150 时被')
    A('  终止，原因：A 路裸跑导致真实语料脚本开窗）；')
    A('  `results_ROUND2_partial_superseded.jsonl`（已过筛，但探针 normalize() 把')
    A('  子工作目录 a/b 残留在输出里 ⇒ 误报路径差异，作废重跑）。')
    A('  判定实现/归一化版本见每份记录里的 `norm_version`（本版 = %d）。'
      % NORM_VERSION)
    A('')
    A('- 判定与观测分离：判定实现只此一份（`classify()`），既在跑的时候用，也能对')
    A('  已落盘的原始观测 `--reclassify` 重算 —— 改判据不必重跑语料（省时间、也少')
    A('  一次"跑真实脚本"的风险面）。')
    A('')
    A('## 1 样本')
    A('')
    A('### 1a 抽样规则（`build_sample(sample_n, per_stratum, …)` 的确切语义）')
    A('')
    A('先过 A 档门禁（§0b），再按**三步**抽，**全是"保底/上限"而不是"先到先得"**：')
    A('')
    A('1. **类别钉选**：对六个必含类各钉最多 **6** 份（按 both_ok 优先、再按路径排序）；')
    A('   同一份脚本可同时命中多类，**只计一次**（所以 pick 次数 > 去重份数）。')
    A('2. **分层新增**：gallery / zenodo / github 三层各再补最多 **`per_stratum` 份**')
    A('   **新出现的**脚本（已在步骤 1 钉住的不会占这个名额）。')
    A('3. **补足**：若去重总数仍 < **`sample_n`**，从全池随机补到 `sample_n`。')
    A('')
    A('⇒ `sample_n` 是**总数保底**（≥ 这么多），**不是上限**；`per_stratum` 是**每层新增**')
    A('的**上限**。本次 `--sample 120 --per-stratum 40` 的实际算式：')
    A('')
    _picks = sum(1 for r in recs for w in (r.get('why') or [])
                 if not w.startswith('stratum:') and w != 'fill')
    _catpaths = [r for r in recs
                 if any(not w.startswith('stratum:') and w != 'fill'
                        for w in (r.get('why') or []))]
    _strpaths = [r for r in recs
                 if any(w.startswith('stratum:') for w in (r.get('why') or []))]
    _bothp = [r for r in recs if r in _catpaths and r in _strpaths]
    _fillp = [r for r in recs if 'fill' in (r.get('why') or [])]
    A('```')
    A('类别钉选：6 类 × ≤6 次 = %d 次 pick，去重后 %d 份（%d 份跨类重复，只算一次）'
      % (_picks, len(_catpaths) - len(_bothp), _picks - (len(_catpaths) - len(_bothp))))
    _per_add = {}
    for _st in ('gallery', 'zenodo', 'github'):
        _per_add[_st] = (sum(1 for r in _strpaths if r.get('stratum') == _st),
                         sum(1 for r in _catpaths
                             if r.get('stratum') == _st and r not in _strpaths))
    for _st, (_a, _b) in _per_add.items():
        A('分层新增 %-7s：%2d（步骤 2）+ %2d（步骤 1 落在该层）= %2d'
          % (_st, _a, _b, _a + _b))
    A('步骤 3 补足：%d 份（去重总数已达 %d ≥ sample_n=120，未触发补足）'
      % (len(_fillp), len(recs)))
    A('**合计 = %d（类别去重）+ %d（分层新增）= %d 份**'
      % (len(_catpaths) - len(_bothp), len(_strpaths), len(recs)))
    A('```')
    A('')
    A('（注意：类别钉选与分层新增**互不重叠**，因为步骤 2 只收"还没被钉住"的脚本；'
      '而"类别去重"里那 %d 份重复，来自同一份脚本同时命中多类。）'
      % (_picks - (len(_catpaths) - len(_bothp))))
    A('')
    A('### 1b 计数与不适用明细（150 − 6 = 144）')
    A('- 全部记录: **%d**；参与一致率统计: **%d**；不适用: **%d**'
      % (len(recs), n, len(recs) - n))
    A('- 分层: %s' % dict(sorted(strat.items())))
    A('- 六类必含覆盖（去重份数）: %s' % {k: cat[k] for k in
                                          ('ends_with_close', 'savefig_only',
                                           'show_only', 'grid', 'side',
                                           'close_call', 'savefig', 'show')})
    A('- 池内 deps_missing 抽样: %d 份' % sum(1 for r in recs
                                              if not r.get('deps_ok')))
    A('')
    A('| # | 路径 | 归类 | 原因（一句话）|')
    A('|---:|---|---|---|')
    for _i, r in enumerate([x for x in recs
                            if x.get('verdict') not in
                            ('equivalent', 'equivalent_fail',
                             'diff_unknown', 'diff_wrapper',
                             'diff_artifacts', 'diff_wrapper_nostate',
                             'diff_wrapper_benign')], 1):
        A('| %d | `%s` | %s | %s |'
          % (_i, r['path'], r.get('verdict'),
             (r.get('na_reason') or '').replace('|', '/')[:120] or '—'))
    A('')
    A('- **分母声明**：本报告 §2 / §3 / §4 的**所有**分母都是 **%d**'
      '（= 全部 %d 份 − %d 份不适用），**没有任何指标偷用 %d**。'
      % (n, len(recs), len(recs) - n, len(recs)))
    A('')
    A('## 2 判定分布')
    A('')
    A('| 判定 | 份数 | 含义 |')
    A('|---|---:|---|')
    A('| equivalent | %d | 两侧同样跑通，输出/产出文件一致 |' % eq_ok)
    A('| equivalent_fail | %d | 两侧同样**跑不通**（缺数据/依赖），一致 |' % eq_fail)
    A('| diff_wrapper_benign | %d | 只有已知的套壳噪声差异（show 被拦掉等） |' % benign)
    A('| diff_wrapper_nostate | %d | A 跑通、B **取不到图状态**（脚本自己 close 了图）|'
      % nonstate)
    A('| diff_artifacts | %d | 产出文件不一致 |' % len(artdiff))
    A('| diff_unknown / diff_wrapper | %d | **待解释**，见 §4 |' % len(unknown))
    A('| na_skip / na_timeout / na_error | %d | 不适用 |'
      % (len(recs) - n))
    A('')
    A('## 3 四项逐项一致率（分母 %d）' % n)
    A('')
    A('| 项 | 一致 | 占比 |')
    A('|---|---:|---:|')
    A('| 退出码语义（两侧同为跑通/同为跑不通）| %d | %.1f%% |'
      % (exit_same, 100.0 * exit_same / max(n, 1)))
    A('| stdout+stderr（严格，合流后多重集）| %d | %.1f%% |'
      % (strict_same, 100.0 * strict_same / max(n, 1)))
    A('| stdout+stderr（抹掉已知套壳噪声）| %d | %.1f%% |'
      % (core_same, 100.0 * core_same / max(n, 1)))
    A('| 产出文件（存在/大小/sha256）| %d | %.1f%% |'
      % (art_same, 100.0 * art_same / max(n, 1)))
    A('')
    A('## 4 不一致逐条分类')
    A('')
    A('### 4a 退出码分类计数')
    A('')
    A('| exit_class | 份数 |')
    A('|---|---:|')
    for k, v in sorted(Counter(r['cmp'].get('exit_class') for r in done).items()):
        A('| %s | %d |' % (k, v))
    A('')
    A('### 4a2 输出差异分类计数（pad_class）')
    A('')
    A('| pad_class | 份数 | 含义 |')
    A('|---|---:|---|')
    _pcm = {'frame_number_drift': '同一警告，仅"源码位置行号"漂移（套壳多包一层 runpy）',
            'wrapper_err_channel': '两侧都跑不通；A 直吐完整 traceback，'
                                   'B 收成一行 `[verify] ...`',
            'unexplained': '**待解释**，见逐条明细'}
    for k, v in sorted(Counter(r['cmp'].get('pad_class') for r in done).items(),
                       key=lambda kv: -kv[1]):
        A('| %s | %d | %s |' % (k, v, _pcm.get(k, '')))
    A('')
    _exempt = Counter()
    for r in done:
        for k in r['cmp'].get('artifacts_nondet_exempt') or []:
            _exempt[os.path.splitext(k)[1].lower()] += 1
    A('### 4a3 产出文件：非确定性格式豁免（size 同、sha 不同，且格式自带时间戳/随机 id）')
    A('')
    A('- 豁免格式计数: %s（0 份 = 无豁免）' % (dict(_exempt) or {}))
    A('- 说明：SVG/EPS/PDF 里 matplotlib 会写创建时间与随机元素 id ⇒ 逐字节不可复现；'
      'PNG 不带时间戳，仍按 sha256 严格判等。')
    A('')
    for r in done:
        if r['verdict'] in ('diff_unknown', 'diff_wrapper'):
            A('### [%s] `%s`' % (r['verdict'], r['path']))
            A('- stratum=%s tags=%s both_ok=%s' % (r.get('stratum'),
                                                   ','.join(r.get('tags') or []),
                                                   r.get('both_ok')))
            A('- a_rc=%s b_rc=%s exit_class=%s'
              % (r['cmp'].get('a_rc'), r['cmp'].get('b_rc'),
                 r['cmp'].get('exit_class')))
            for ln in (r['cmp'].get('pad_unknown_a') or [])[:5]:
                A('  - 只在 A: %s' % ln['line'])
            for ln in (r['cmp'].get('pad_unknown_b') or [])[:5]:
                A('  - 只在 B: %s' % ln['line'])
            A('')
    if artdiff:
        A('### 4b 产出文件不一致（已扣除非确定性格式豁免）')
        for r in artdiff:
            A('- `%s`: reason=%s；只在 A=%s；只在 B=%s'
              % (r['path'], r['cmp'].get('artifacts_reason'),
                 sorted(r['cmp'].get('art_only_a') or {}),
                 sorted(r['cmp'].get('art_only_b') or {})))
        A('')

    # ---- A vs A′ 噪声地板：区分"套壳造成"与"环境/非确定性造成" ----------------
    nd = _load_nondet(args)
    env_nondet = [r for r in unknown if r['path'] in nd['paths']]
    residual = [r for r in unknown if r['path'] not in nd['paths']]
    A('### 4b2 A vs A′ 噪声地板（同一路"原样跑"跑两次）')
    A('')
    if nd['rate'] is None:
        A('- 未跑（`--nondet` 未执行）；"环境造成 vs 套壳造成"暂无基准，见 §6 未验证项。')
    else:
        A('- 对照分母 %d 份；**A vs A′ 自身一致率 = %.1f%%**；A vs A′ 就不一致的有 %d 份'
          % (nd['n'], nd['rate'], nd.get('n_diff', 0)))
        A('- 结论：**A vs B 的不一致不能全记在套壳头上** —— 下面把二者分开：')
        A('')
        A('| 分组 | 份数 | 说明 |')
        A('|---|---:|---|')
        A('| 环境/非确定性（A vs A′ **同样**不一致）| %d | 时钟、无种子随机数、'
          'joblib 资源追踪竞态、语料目录遮蔽标准库等 |' % len(env_nondet))
        A('| 仍待解释（A vs A′ **一致**，只有套壳那次不同）| %d | 见下逐条 |'
          % len(residual))
        A('')
        for r in env_nondet:
            A('- (环境/非确定性) `%s` — pad_class=%s exit=%s'
              % (r['path'], r['cmp'].get('pad_class'),
                 r['cmp'].get('exit_class')))
        A('')
        for r in residual:
            A('- (**待解释**) `%s` — pad_class=%s exit=%s'
              % (r['path'], r['cmp'].get('pad_class'),
                 r['cmp'].get('exit_class')))
        A('')

    # 已知套壳噪声按类聚合
    noise = defaultdict(int)
    for r in done:
        for k in ('pad_benign_a', 'pad_benign_b'):
            for item in r['cmp'].get(k) or []:
                noise[item['class']] += 1
    A('### 4c 已知套壳噪声（不计入不一致）')
    A('')
    if noise:
        for k, v in sorted(noise.items(), key=lambda kv: -kv[1]):
            A('- `%s`: %d 行' % (k, v))
    else:
        A('- 无')
    A('')
    A('### 4d 套壳取不到图状态（B_NOSTATE_CLOSED）逐条')
    A('')
    nl = [r for r in done if r['verdict'] == 'diff_wrapper_nostate']
    for r in nl:
        A('- `%s`（tags=%s）' % (r['path'], ','.join(r.get('tags') or [])))
    if not nl:
        A('- 无')
    A('')
    A('## 5 耗时')
    A('')
    A('- A 路中位: %.2fs；B 路中位: %.2fs；两路合计中位: %.2fs'
      % (med(walls_a), med(walls_b), med(walls_sum)))
    A('- A 路总和: %.1fs；B 路总和: %.1fs；两路合计: %.1fs'
      % (sum(walls_a), sum(walls_b), sum(walls_sum)))
    A('- **B 单遍 / 两遍现况** = %.2f（越低越省；理想 ≈ B/(A+B)）'
      % (sum(walls_b) / max(sum(walls_sum), 1e-9)))
    A('')
    A('## 6 语料只读对账（跑前 / 跑后逐份 sha256）')
    A('')
    A('- **核对方式**：每份脚本在**被跑之前**记下 `sha256(原文件)` 存进该记录的')
    A('  `corpus_sha256` 字段；本报告生成时对**同一批路径**重新算一遍 sha256 并与')
    A('  之逐个比对，不一致就列出来。')
    A('- 另：脚本从不就地跑 —— 一律复制到 `<work>/<id>/{a,b}/` 两个内容相同的副本')
    A('  目录再跑，产出文件（savefig/to_csv/open(\'w\')）全部落在临时工作区。')
    A('- 故"语料未被修改"有两重证据：① 逐份 sha256 对账；② 产出文件根本不在语料树里。')
    A('')
    changed = [r for r in recs
               if r.get('corpus_sha256')
               and os.path.exists(os.path.join(corpus_of(args), r['path']))
               and _sha256_file(os.path.join(corpus_of(args), r['path']))
               != r['corpus_sha256']]
    A('- 抽样脚本 sha256 前后不一致: **%d** 份' % len(changed))
    for r in changed:
        A('  - `%s`' % r['path'])
    A('')
    return '\n'.join(lines), {
        'n': n, 'verdicts': dict(vc), 'exit_same': exit_same,
        'strict_same': strict_same, 'core_same': core_same,
        'art_same': art_same, 'unknown': len(unknown),
        'nostate': nonstate, 'benign': benign, 'artdiff': len(artdiff),
        'wall_a': sum(walls_a), 'wall_b': sum(walls_b),
        'wall_sum': sum(walls_sum),
    }


def corpus_of(args):
    return args.corpus


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def _child_env() -> dict:
    env = dict(os.environ)
    env['MPLBACKEND'] = 'Agg'
    env['PYTHONIOENCODING'] = 'utf-8'          # 硬钉编码：见上面同名字段的说明（GBK 噪声）
    env.pop('MPLTWEAK_SANDBOX_ROOT', None)     # 沙箱会拦脚本写盘，探针要放行
    parts = [p for p in (env.get('PYTHONPATH') or '').split(os.pathsep) if p]
    if SRC not in parts:
        parts.insert(0, SRC)
    env['PYTHONPATH'] = os.pathsep.join(parts)
    return env


def _run_plain_case(src_abs, case_dir, sub, python, timeout):
    """在 ``<case_dir>/<sub>/`` 里**原样跑一次**，返回 (obs, artifacts)。"""
    d = os.path.join(case_dir, sub)
    if os.path.isdir(d):
        shutil.rmtree(d, ignore_errors=True)
    _seed_dir(d, src_abs)
    script_abs = os.path.join(d, os.path.basename(src_abs))
    before = _snapshot(d)
    r = _run([python, script_abs], d, _child_env(), timeout)
    after = _snapshot(d)
    repl = [(script_abs, '<SCRIPT>'), (d, '<WORK>'), (case_dir, '<WORK>')]
    lines = normalize(_dec(r['out']), repl) + normalize(_dec(r['err']), repl)
    obs = {'rc': r['rc'], 'timeout': r['timeout'], 'spawn_err': r['spawn_err'],
           'wall': round(r['wall'], 3), 'lines': lines,
           'stderr_head': '\n'.join(lines[-12:])[:1500]}
    return obs, _artifacts(before, after)


def nondet_control(args) -> int:
    """**噪声地板**：同一路（原样跑）跑**两次**，量测探针/环境的自身非确定性。

    没有这个对照，"A vs B 不一致"这句话就没有基准 —— 如果 A vs A' 本身就有 20%
    不一致，那 A vs B 的不一致率就不能全记在套壳头上。
    """
    path = args.nondet
    if not os.path.isfile(path):
        sys.stderr.write('无此文件: %s\n' % path)
        return 2
    recs = [json.loads(l) for l in open(path, 'r', encoding='utf-8') if l.strip()]
    interesting = [r['path'] for r in recs
                   if r.get('verdict') not in ('equivalent', 'equivalent_fail',
                                               'na_skip', 'na_error', 'na_timeout')]
    same_pool = [r['path'] for r in recs
                 if r.get('verdict') in ('equivalent', 'equivalent_fail')]
    if args.nondet_limit:
        interesting = interesting[:args.nondet_limit]
    ctrl = same_pool[:max(0, args.nondet_control)]
    targets = interesting + ctrl
    sys.stderr.write('[nondet] A vs A′ 对照：%d 份（争议 %d + 对照 %d）\n'
                     % (len(targets), len(interesting), len(ctrl)))
    out_rows = []
    for i, rel in enumerate(targets, 1):
        src_abs = os.path.join(args.corpus, rel)
        if not _screen_ok(src_abs):
            out_rows.append({'path': rel, 'verdict': 'na_skip'}); continue
        case_dir = os.path.join(args.work, 'nondet_' + hashlib.sha256(
            rel.encode('utf-8')).hexdigest()[:16])
        try:
            o1, a1 = _run_plain_case(src_abs, case_dir, 'a', args.python,
                                     args.timeout)
            o2, a2 = _run_plain_case(src_abs, case_dir, 'a2', args.python,
                                     args.timeout)
            cmp_, verdict = classify(o1, o2, _art_eq(a1, a2))
            row = {'path': rel, 'verdict': verdict, 'cmp': cmp_,
                   'a': o1, 'b': o2}
        except Exception as e:                   # noqa: BLE001
            row = {'path': rel, 'verdict': 'na_error',
                   'na_reason': '%s: %s' % (type(e).__name__, e)}
        out_rows.append(row)
        sys.stderr.write('[%3d/%3d] %-22s %s\n'
                         % (i, len(targets), row['verdict'], rel))
        if not args.keep_work:
            shutil.rmtree(case_dir, ignore_errors=True)
    outp = os.path.join(os.path.dirname(os.path.abspath(path)), 'NONDET.jsonl')
    with open(outp, 'w', encoding='utf-8') as f:
        for r in out_rows:
            f.write(json.dumps(r, ensure_ascii=False) + '\n')
    from collections import Counter
    vc = Counter(r['verdict'] for r in out_rows)
    n = sum(v for k, v in vc.items() if not k.startswith('na_'))
    eq = vc.get('equivalent', 0) + vc.get('equivalent_fail', 0)
    print('# A vs A′（同一路跑两次）—— 噪声地板\n')
    print('- 分母: %d；判定分布: %s' % (n, dict(vc)))
    print('- **A vs A′ 一致率 = %.1f%%**（%d/%d）'
          % (100.0 * eq / max(n, 1), eq, n))
    for r in out_rows:
        c = r.get('cmp') or {}
        if r['verdict'] not in ('equivalent', 'equivalent_fail'):
            print('\n## [%s] `%s`  pad_class=%s'
                  % (r['verdict'], r['path'], c.get('pad_class')))
            for x in (c.get('pad_unknown_a') or [])[:4]:
                print('  - 只在 A : %s' % x['line'][:220])
            for x in (c.get('pad_unknown_b') or [])[:4]:
                print('  - 只在 A′: %s' % x['line'][:220])
            if not c.get('artifacts_same'):
                print('  - 产出: %s' % c.get('artifacts_reason'))
    print('\n[nondet] 明细=%s' % outp)
    return 0


def _screen_ok(src_abs: str) -> bool:
    ok, _reason, _ev = screen_script(read_text(src_abs), WINDOW_RISK)
    return ok


def refine(args) -> int:
    """**只对争议份**重跑 A+B 并就地更新结果 —— 改判据/改归一化后不必重跑全量。

    观测（跑）与判定（`classify`）分离：全量跑一次拿到观测；之后每次改归一化或
    判据，只需要把"判不干净"的那几十份重跑一遍（几份 vs 150 份）。
    """
    global WINDOW_RISK, WINDOW_RISK_PROV
    path = args.refine
    if not os.path.isfile(path):
        sys.stderr.write('无此文件: %s\n' % path)
        return 2
    WINDOW_RISK, WINDOW_RISK_PROV, werr = load_window_risk()
    if WINDOW_RISK is None:
        sys.stderr.write('[门禁] **拒绝开跑**：A 档判据复用失败（%s）\n' % werr)
        return 2
    recs = [json.loads(l) for l in open(path, 'r', encoding='utf-8') if l.strip()]
    keep_v = ('equivalent', 'equivalent_fail')
    targets = [r for r in recs
               if r.get('verdict') not in keep_v
               or r.get('verdict') in ('na_timeout', 'na_error')]
    if args.refine_only:
        want = [s for s in args.refine_only.split(',') if s]
        targets = [r for r in targets if any(w in r['path'] for w in want)]
    sys.stderr.write('[refine] 重跑 %d 份（争议 + 不适用）\n' % len(targets))
    os.makedirs(args.work, exist_ok=True)
    by_path = {r['path']: r for r in recs}
    n = 0
    for i, rec in enumerate(targets, 1):
        src_abs = os.path.join(args.corpus, rec['path'])
        if not os.path.isfile(src_abs):
            sys.stderr.write('[%3d/%3d] 语料里没有: %s\n' % (i, len(targets),
                                                             rec['path']))
            continue
        _ok, _reason, _ev = screen_script(read_text(src_abs), WINDOW_RISK)
        if not _ok:
            by_path[rec['path']].update({'verdict': 'na_skip',
                                         'na_reason': 'rescreen:%s' % _reason})
            continue
        sample = {'path': rec['path'], 'rec': {
            'stratum': rec.get('stratum'), 'source': rec.get('source'),
            'both_ok': rec.get('both_ok'), 'deps_ok': rec.get('deps_ok'),
            'missing_libs': rec.get('missing_libs') or []},
            'why': rec.get('why') or [], 'tags': rec.get('tags') or []}
        new = one_case(sample, args.corpus, args.python, args.work, args.timeout)
        new['why'] = rec.get('why') or new.get('why')
        by_path[rec['path']] = new
        n += 1
        sys.stderr.write('[%3d/%3d] %-22s %s\n'
                         % (i, len(targets), new.get('verdict'), rec['path']))
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        for r in recs:
            f.write(json.dumps(by_path[r['path']], ensure_ascii=False) + '\n')
    os.replace(tmp, path)
    sys.stderr.write('[refine] 已更新 %d 份 → %s\n' % (n, path))
    return 0


def reclassify(args) -> int:
    """用**当前**判定实现重算已落盘 JSONL 的 cmp/verdict，并重出 SUMMARY。

    原始观测（A/B 两路的 rc / 归一化行 / 产出文件差异 / 墙钟）是**跑出来的、昂贵的**；
    判定逻辑是**便宜的、可迭代的**。这两件事分开，改判据就不必重跑语料。
    """
    global WINDOW_RISK_PROV
    path = args.reclassify
    if not os.path.isfile(path):
        sys.stderr.write('无此文件: %s\n' % path)
        return 2
    recs = []
    with open(path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line:
                recs.append(json.loads(line))
    n = 0
    for r in recs:
        if r.get('a') is None or r.get('b') is None:
            continue
        cmp_, verdict = classify(r['a'], r['b'],
                                 _art_from_record(r.get('cmp') or {}))
        r['cmp'] = cmp_
        r['verdict'] = verdict
        n += 1
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        for r in recs:
            f.write(json.dumps(r, ensure_ascii=False) + '\n')
    os.replace(tmp, path)

    meta_path = os.path.join(os.path.dirname(os.path.abspath(path)),
                             'RUN_META.json')
    screen_stats = None
    try:
        with open(meta_path, 'r', encoding='utf-8') as f:
            meta = json.load(f)
        screen_stats = meta.get('screen_stats')
        WINDOW_RISK_PROV = meta.get('criterion_source') or {}
    except (OSError, ValueError):
        pass
    text, stats = summarize(recs, args, screen_stats)
    with open(args.summary, 'w', encoding='utf-8') as f:
        f.write(text)
    sys.stderr.write('[reclassify] 重算 %d 份（判定实现 = 当前代码）\n' % n)
    print(text)
    print('\n[reclassify] results=%s\n[reclassify] summary=%s'
          % (path, args.summary))
    return 0


def main(argv=None) -> int:
    global KEEP_WORK, WINDOW_RISK, WINDOW_RISK_PROV
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--corpus', default=DEFAULT_CORPUS)
    ap.add_argument('--pool', default=DEFAULT_POOL)
    ap.add_argument('--python', default=sys.executable)
    ap.add_argument('--sample', type=int, default=120)
    ap.add_argument('--per-stratum', type=int, default=40)
    ap.add_argument('--timeout', type=float, default=60.0)
    ap.add_argument('--jobs', type=int, default=2)
    ap.add_argument('--out', default=os.path.join(DEFAULT_OUT_DIR,
                                                  'results.jsonl'))
    ap.add_argument('--summary', default=os.path.join(DEFAULT_OUT_DIR,
                                                      'SUMMARY.md'))
    ap.add_argument('--work', default=DEFAULT_WORK)
    ap.add_argument('--resume', action='store_true')
    ap.add_argument('--keep-work', action='store_true')
    ap.add_argument('--screen-only', action='store_true',
                    help='只做 A 档防弹窗门禁自证 + 留档，不跑任何脚本')
    ap.add_argument('--reclassify', default='',
                    help='用当前判定实现重算既有 results.jsonl 的判定并重出 SUMMARY')
    ap.add_argument('--nondet', default='',
                    help='噪声地板对照：对既有 results.jsonl 里的脚本走 A vs A′（同路两次）')
    ap.add_argument('--nondet-limit', type=int, default=0,
                    help='--nondet 时争议路径的条数上限（0 = 全部争议份）')
    ap.add_argument('--nondet-control', type=int, default=10,
                    help='--nondet 时额外抽多少份"本来等价"的做对照（默认 10）')
    ap.add_argument('--refine', default='',
                    help='只把既有 results.jsonl 里"判不干净/不适用"的份重跑 A+B 并就地更新')
    ap.add_argument('--refine-only', default='',
                    help='--refine 时的路径子串白名单（逗号分隔）')
    ap.add_argument('--only', default='', help='只跑 path 含该子串的份数')
    args = ap.parse_args(argv)
    KEEP_WORK = args.keep_work

    if args.reclassify:
        return reclassify(args)
    if args.refine:
        if not os.path.isdir(args.corpus):
            sys.stderr.write('语料根不存在: %s\n' % args.corpus)
            return 2
        return refine(args)
    if args.nondet:
        if not os.path.isdir(args.corpus):
            sys.stderr.write('语料根不存在: %s\n' % args.corpus)
            return 2
        os.makedirs(args.work, exist_ok=True)
        global_setup = load_window_risk()
        WINDOW_RISK, WINDOW_RISK_PROV, _e = global_setup
        if WINDOW_RISK is None:
            sys.stderr.write('[门禁] **拒绝开跑**：A 档判据复用失败（%s）\n' % _e)
            return 2
        return nondet_control(args)

    if not os.path.isdir(args.corpus):
        sys.stderr.write('语料根不存在: %s\n' % args.corpus)
        return 2
    if not os.path.isfile(args.pool):
        sys.stderr.write('候选池不存在: %s\n' % args.pool)
        return 2

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    os.makedirs(args.work, exist_ok=True)

    # ---- A 档防弹窗门禁：先装载判据，再抽样（抽样阶段就已排除）----------------
    WINDOW_RISK, WINDOW_RISK_PROV, werr = load_window_risk()
    if WINDOW_RISK is None:
        sys.stderr.write('[门禁] **拒绝开跑**：A 档判据复用失败（%s）\n' % werr)
        return 2

    rows = load_pool(args.pool)
    sample, screen_stats = build_sample(rows, args.corpus, args.sample,
                                        args.per_stratum, WINDOW_RISK)
    if args.only:
        sample = [s for s in sample if args.only in s['path']]

    # ---- 跑前自证：样本内 A 档命中数必须 = 0，否则拒跑 -----------------------
    hits = []
    for s in sample:
        ok, reason, ev = screen_script(
            read_text(os.path.join(args.corpus, s['path'])), WINDOW_RISK)
        if not ok:
            hits.append({'path': s['path'], 'reason': reason, 'evidence': ev})
    sys.stderr.write(
        '[门禁] A 档静态筛：池 %d 行 → 过筛 %d 份；排除 %s\n'
        % (screen_stats['n_rows'], screen_stats['n_screened_in'],
           screen_stats['excluded']))
    sys.stderr.write('[门禁] 判据来源 %s（sha256 %s）\n'
                     % (WINDOW_RISK_PROV.get('tests_src'),
                        (WINDOW_RISK_PROV.get('tests_src_sha256') or '')[:16]))
    sys.stderr.write('[门禁] 样本 %d 份，A 档命中 = **%d**（必须为 0）\n'
                     % (len(sample), len(hits)))
    if hits:
        sys.stderr.write('[门禁] **拒绝开跑**：样本内仍有 A 档命中：\n')
        for h in hits[:20]:
            sys.stderr.write('   %s  %s  %s\n' % (h['reason'], h['path'],
                                                  h['evidence'][:3]))
        return 2
    sys.stderr.write('[门禁] 子进程 env：A 路与 B 路均为 MPLBACKEND=%s'
                     '（主防线是上面的静态筛，这条只是补证）\n' % 'Agg')
    sys.stderr.write('[probe] 样本 %d 份（seed=%d）\n' % (len(sample), SEED))

    meta = {'gate': 'A 档静态防弹窗（先筛后跑）',
            'criterion_source': WINDOW_RISK_PROV,
            'pool': args.pool, 'pool_sha256': _sha256_file(args.pool),
            'corpus': args.corpus, 'python': args.python,
            'sample_target': args.sample, 'per_stratum': args.per_stratum,
            'seed': SEED, 'n_sample': len(sample),
            'screen_stats': screen_stats,
            'window_risk_hits_in_sample': len(hits),
            'env_mpbackend': 'Agg',
            'sampled_paths': [s['path'] for s in sample]}
    meta_path = os.path.join(os.path.dirname(os.path.abspath(args.out)),
                             'RUN_META.json')
    try:
        with open(meta_path, 'w', encoding='utf-8') as f:
            json.dump(meta, f, ensure_ascii=False, indent=1)
    except OSError as e:
        sys.stderr.write('[门禁] RUN_META 写不了：%s\n' % e)
    if args.screen_only:
        sys.stderr.write('[门禁] --screen-only：自证通过，未跑任何脚本；'
                         '留档 %s\n' % meta_path)
        return 0

    done = {}
    if args.resume and os.path.isfile(args.out):
        with open(args.out, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if r.get('verdict') not in ('na_error',):
                    done[r['path']] = r
        sys.stderr.write('[probe] 续跑：已有 %d 份\n' % len(done))

    todo = [s for s in sample if s['path'] not in done]
    recs = list(done.values())
    t0 = time.time()
    with open(args.out, 'a', encoding='utf-8') as fout:
        with futures.ThreadPoolExecutor(max_workers=max(1, args.jobs)) as ex:
            futs = {ex.submit(one_case, s, args.corpus, args.python, args.work,
                              args.timeout): s for s in todo}
            n_done = 0
            for fut in futures.as_completed(futs):
                s = futs[fut]
                try:
                    r = fut.result()
                except Exception as e:            # noqa: BLE001
                    r = {'id': '', 'path': s['path'], 'stratum': None,
                         'verdict': 'na_error',
                         'na_reason': '%s: %s' % (type(e).__name__, e)}
                recs.append(r)
                fout.write(json.dumps(r, ensure_ascii=False) + '\n')
                fout.flush()
                n_done += 1
                sys.stderr.write('[%3d/%3d] %-14s %s\n'
                                 % (n_done, len(todo), r.get('verdict'),
                                    r['path']))
    # 去重（续跑 + 本轮）
    by_path = {}
    for r in recs:
        by_path[r['path']] = r
    recs = list(by_path.values())

    text, stats = summarize(recs, args, screen_stats)
    with open(args.summary, 'w', encoding='utf-8') as f:
        f.write(text)
    sys.stderr.write('\n[probe] 用时 %.1fs\n' % (time.time() - t0))
    print(text)
    print('\n[probe] results=%s\n[probe] summary=%s' % (args.out, args.summary))
    return 0


if __name__ == '__main__':
    sys.exit(main())
