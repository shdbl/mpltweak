# -*- coding: utf-8 -*-
"""T17 / 0.1.18：语料**身份级**回归 —— 验「写对没」，不只是「写成功没」。

为什么要这份用例（本轮最根本的缺口）
====================================
过去的语料验证只统计**「写成功没」**：`apply --write` 返回 rc=0 就记为成功。
**写错也算成功。** 0.1.17 的轴身份错位（`_team014/axis_identity.md` §0）就是这么漏了
三个版本：`describe → apply --write`（本该是一次恒等变换）把 `ax2` 的矩形写进了
`ax3 = fig.add_axes(...)`、把 `ax3.set_xlim(1961, 2021)` 改成 `(0, 2)`，**rc=0**，
还打印验证通过 —— 也就是说，光看 `rc` 这条路根本不长牙。

本文件只问一件事：**我们要求的那个值，落在**它自己那根轴**上了吗？**

    档位 A（每份语料脚本）：
        d1 = describe(S)                      # 号牌表：axes[].index = fig.axes 序号
        P  = 给第 k 根轴各下一个**互不相同**的值（k 取 2~3 根），其余字段不动
        apply(S, P, --write)                  # 只记录它**声称**改了什么
        d2 = describe(S)                      # 重跑脚本读回（不是读源码，是读运行期）
        判据：d2 的第 k 根轴 == 我们要的值，且**那串值不出现在别的轴身上**

    档位 B（正对照，`--control`）：
        拿 `_team014/reviewer_repro.py`（0.1.17 事故现场的最小复现）跑同一条判据：
          · 修复前形态（`_team014/wt_017` 那个 v0.1.17 worktree）**必须**被判 WRONG；
          · 当前树**必须** ok 或 refused。
        判据没有牙（在旧树上也判 ok），正对照当场红 —— 这条比"我这版全绿"更重要。

三栏口径（**只有这三栏**，多一栏都不许）
========================================
``ok``      写了，且读回**对得上**（请求的值落在同一身份上，没漏到隔壁）。
``refused`` fail-closed 的失败即合法：工具明确说"没写/写不了"（`no_change`、
            `bad_params`、轴号超出现有建轴点、布局冲突回滚…）。**拒写不是事故。**
``WRONG``   rc=0、**声称成功**，而读回不对 —— 事故本体。**必须为 0，非 0 即 rc=1。**
            本文件认三种 WRONG（判据实现见 ``_judge``）：
              ① ``leak``：给 ax_k 的值出现在 ax_j（j≠k）身上 —— 值跑到隔壁去了；
              ② ``claimed_not_landed``：原位通道**声称**改了 ``ax_k.f``（在 changes 里），
                 读回却是原值 —— 0.1.17 的 `ax3.xlim` 就是这一档；
              ③ ``write_broke_script``：rc=0 写了，重跑读回时脚本跑不通（生成了错代码）。

两份**补充计数**（不进三栏，但必须单独报，不许混进 ok）：
``n/a``           脚本根本跑不起来（缺库 / 缺数据 / 需要命令行参数 / 有开窗风险 /
                  超时）—— **不算 WRONG**，但也不能算"验过了"。
``silent_drop``   块通道没落地、也没进 ``pending``、也没声称改过的键（只会出现在
                  `block_lines>0` 的写法里）。这是"悄悄少写一个"的口径，本文件不判
                  WRONG（块通道本来就按"能表达几个写几个"工作），但计数必须透明。

两条口径纪律（0.1.18 §⑥，别再用错）
===================================
* **`best_effort` ≠ 被拒**：`best_effort` 是**写了**（原位落了一部分 + 退块补了其余），
  它属于"声称改了"的一档 ⇒ 本文件对它照样做读回断言；把它当拒写放过去，就等于
  把 0.1.17 那类事故放过去。
* **`block_lines` 的含义**：插入/替换的**调整块行数**（块通道才有；原位通道恒 0）。
  它**不是**"被拒的行数"，也不是"改动的行数"。本文件只用它判断"这次是不是块通道"，
  从而决定 `changes`（原位通道的逐字段声称）能不能用。

抽样策略（写死、可复现）
========================
* **候选池**：`<语料根>/manifest.jsonl` 里 ``plot == true`` 的行，且只取 ``018`` 与
  ``big`` 两族（= 第三方公开绘图脚本；``harness/`` 是本项目脚手架，不抽）。
  manifest 缺失时退化为"遍历 ``018/`` ``big/`` 下的 ``*.py``"。
* **分层**：按**目录**分 5 层 —— ``018/gallery`` / ``018/zenodo`` / ``big/gallery`` /
  ``big/github`` / ``big/zenodo``（= ``<族>/<一级子目录>``，与语料自身组织方式一致）。
* **配额**：按各层候选数**最大余数法**分配（N≥层数时 5 层都有份，不会只抽到 zenodo）。
* **随机**：固定种子（默认 ``20261025``），每层独立 ``random.Random('<seed>|<层名>')``
  ⇒ 与进程哈希种子无关，换机器、换 Python 版本抽到的都是同一批。
* **进样前的三道静态闸**（``_gate_script``，一次读盘 + 一次 ``ast.parse``）：
  ``missing_deps``（import 的第三方包本机没装）/ ``gui_risk``（tkinter / input()）/
  ``axes_in_loop``（建轴调用落在 for-while 里 ⇒ T8 身份闸门按设计 fail-closed 拒写，
  跑得通也拿不到身份信息）。**挡掉多少条、分别多少，全部计数进报告**（``gates``）。
  实测（种子 20261025）：4433 条候选 → 过闸 1576 条（缺库 1753 / 要 GUI 236 /
  建轴在循环 156 / 无可静态识别的建轴 617 / 解析不了 95）。
* **"可运行"怎么办**：过闸的候选按上面的顺序排好（层内已洗牌、层间交错），
  **按序尝试**，取前 N 份 describe 能跑通的（跑不动的记 n/a 继续往后拿），尝试上限 ``3N``。
  这样"N 份可运行脚本"是真的 N 份，不是"N 份里有 8 份能跑"。
* N 默认 **12**：实测 36 次尝试 → 9 份跑通 / 98 s 重跑 / 整轮 142 s；跑满 12 份约 3~4 min
  （含抽样扫描 + 正对照），低于"超过 10 分钟就减小 N"的红线
  （``MPLTWEAK_CORPUS_N`` / ``--limit`` 可改）。

用法
====
    # 默认**跳过**（不设语料根就什么都不跑，rc=0）
    python tests/test_corpus_identity.py

    # 真跑（默认 N=20，6 并发，约 4~5 分钟）
    MPLTWEAK_CORPUS_WORK=D:/Agent/deepseek/corpus_union python tests/test_corpus_identity.py

    # 冒烟 / 正对照
    MPLTWEAK_CORPUS_WORK=... python tests/test_corpus_identity.py --limit 6
    MPLTWEAK_CORPUS_WORK=... python tests/test_corpus_identity.py \\
        --control D:/Agent/deepseek/_team014/wt_017      # v0.1.17 树：必须判 WRONG

    # 换被测树（默认 = 本文件所在仓库）
    MPLTWEAK_CORPUS_WORK=... python tests/test_corpus_identity.py --tree <另一份工作树>

产出（默认 ``<workspace>/_review/corpus_identity/``，``--out`` 可改）：
    results.jsonl  逐份结果（含请求值、读回值、判据、耗时 —— 可复现、可指证）
    SUMMARY.md     摘要（N / ok / refused / WRONG / n/a / 耗时 / WRONG 清单）
"""

import argparse
import ast
import hashlib
import importlib.util
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

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


# ---- 沙箱坑：%TEMP% 写入可能被拦 → 把 TEMP/TMP 指到可写目录（子进程继承）----
_TMPROOT = None


def _ensure_tmp_writable():
    global _TMPROOT
    base = tempfile.gettempdir()
    root = os.path.join(base, '.mpltweak_corpus_id_%d' % os.getpid())
    try:
        os.makedirs(root, exist_ok=True)
        probe = os.path.join(root, '.w')
        open(probe, 'w').close()
        os.unlink(probe)
    except OSError:
        root = os.path.join(HERE, '.tmp_corpus_id_%d' % os.getpid())
        os.makedirs(root, exist_ok=True)
    _TMPROOT = root
    os.environ['TEMP'] = root
    os.environ['TMP'] = root
    os.environ['TMPDIR'] = root
    tempfile.tempdir = root
    return root


HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)                      # <树> = .../mpltweak
WORKSPACE = os.path.dirname(ROOT)                 # .../deepseek
CORPUS_DEFAULT = os.environ.get('MPLTWEAK_CORPUS_WORK')
OUTDIR_DEFAULT = os.environ.get('MPLTWEAK_CORPUS_ID_OUT',
                                os.path.join(WORKSPACE, '_review',
                                             'corpus_identity'))
REPRO_DEFAULT = os.environ.get(
    'MPLTWEAK_CORPUS_REPRO', os.path.join(WORKSPACE, '_team014',
                                          'reviewer_repro.py'))

#: 抽样：固定种子 + 层（= <族>/<一级子目录>）+ 默认 N + 尝试上限倍数
SEED_DEFAULT = 20261025
#: 默认 N：实测取 12 —— 40 次尝试里约 3~4 份是 n/a（缺数据/缺参数），跑满 12 份可运行
#: 约需 48~72 次尝试 ≈ 3~4 min（含抽样扫描与正对照），低于"超过 10 分钟就减小 N"的红线。
#: 实测锚点：种子 20261025 / N=12 / 36 次尝试 → 9 份跑通、98 s 重跑、整轮 142 s。
#: `MPLTWEAK_CORPUS_N` / `--limit` 可改。
N_DEFAULT = 12
ATTEMPT_CAP_X = 6
FAMILIES = ('018', 'big')

#: 判据容差：请求值一律给到 4 位小数（写回块也是 4 位），所以 1e-6 足够严；
#: 再放宽容差会把"偏了一点"当真，那正是要抓的东西。
TOL = 1e-6

#: 每根目标轴各下一个**互不相同**的值 —— 这是判据的牙：
#: 只要发生"轴错位/整体平移一格"，读回必然对不上（相同值会互相掩盖）。
_POS_DELTAS = (0.011, 0.017, 0.023)
_TITLE_SIZES = (21.5, 17.5, 13.5)


# ---------------------------------------------------------------------------
# §1 子进程封装（一律 pin Agg：宿主 MPLBACKEND 不许漏进子进程）
# ---------------------------------------------------------------------------
def _pin_cli_env(tree):
    """构造 CLI 子进程环境。``env=`` 必须**显式钉住**后端（test_hygiene 的
    调用点级棘轮按 AST 溯源到这里，函数体里出现 ``MPLBACKEND='Agg'`` 即判 pinned）。

    ``PYTHONPATH`` 指到**被测树**的 ``src``（``--tree`` 换树时就是靠这条生效：
    正对照要在 v0.1.17 的 worktree 上跑同一条判据）。
    """
    env = dict(os.environ, MPLBACKEND='Agg', MPLTWEAK_LANG='zh',
               PYTHONIOENCODING='utf-8', PYTHONUTF8='1')
    env['PYTHONPATH'] = os.path.join(tree, 'src')
    # 语料脚本常自带 matplotlib 配置/缓存写入；一律收回本次的临时根，别污染真家目录
    if _TMPROOT:
        env['MPLCONFIGDIR'] = os.path.join(_TMPROOT, '.mplconfig')
        env['TMP'] = env['TEMP'] = env['TMPDIR'] = _TMPROOT
    env.pop('MPLTWEAK_SANDBOX_ROOT', None)      # 语料扫描不带沙箱钩子（只读语料已隔离）
    return env


def _decode_out(raw):
    for enc in ('utf-8', 'gbk', 'gb18030'):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode('utf-8', 'replace')


def _run_cli(tree, argv, cwd, timeout):
    """跑一次 ``python -m mpltweak <argv>``，返回 (rc, stdout, stderr)。超时 rc=-9。"""
    cmd = [sys.executable, '-m', 'mpltweak'] + list(argv)
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout,
                           env=_pin_cli_env(tree), cwd=cwd)
    except subprocess.TimeoutExpired:
        return (-9, '', 'CLI 超时（%ss）' % timeout)
    return (r.returncode, _decode_out(r.stdout), _decode_out(r.stderr))


def _extract_json(stdout):
    """从 ``--json`` 的 stdout 里抽对象（前导杂讯容错）。"""
    s = (stdout or '').strip()
    if s.startswith('{'):
        try:
            return json.loads(s)
        except ValueError:
            pass
    i = s.find('{')
    if i >= 0:
        try:
            return json.loads(s[i:])
        except ValueError:
            return None
    return None


def _sha256(path):
    """脚本字节指纹：判断 ``apply --write`` **到底动没动磁盘**（不信 JSON 的自述）。"""
    h = hashlib.sha256()
    try:
        with open(path, 'rb') as f:
            for chunk in iter(lambda: f.read(65536), b''):
                h.update(chunk)
    except OSError:
        return ''
    return h.hexdigest()


def _describe(tree, script, cwd, timeout):
    """describe → (payload, why)。失败时 payload=None，why 写清是哪一档。

    失败原因分两类（``--json`` 的 ``error_code``）：脚本侧（缺库/缺数据/要参数）与
    工具侧（internal_error）。**两类都归 n/a**：本文件不拿"脚本跑不起来"当 WRONG。
    """
    rc, out, err = _run_cli(tree, ['describe', script, '--compact'], cwd, timeout)
    if rc == 0:
        j = _extract_json(out)
        if isinstance(j, dict) and isinstance(j.get('axes'), list):
            return (j, '')
        return (None, 'describe_json_unparsable')
    j = _extract_json(out)
    code = ''
    if isinstance(j, dict):
        code = str(j.get('error_code') or j.get('attribution') or '')
    if rc == -9:
        return (None, 'timeout')
    return (None, 'describe_rc%d%s' % (rc, (':' + code) if code else ''))


# ---------------------------------------------------------------------------
# §2 抽样（固定种子 / 按目录分层 / 最大余数配额 / 可复现）
# ---------------------------------------------------------------------------
def _load_manifest_paths(corpus_root):
    """``manifest.jsonl`` 里 ``plot==true`` 的 ``018/`` ``big/`` 两族相对路径。"""
    man = os.path.join(corpus_root, 'manifest.jsonl')
    if not os.path.isfile(man):
        return None
    out = []
    try:
        with open(man, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if not row.get('plot'):
                    continue
                rel = (row.get('path') or '').replace('\\', '/')
                if not rel.endswith('.py'):
                    continue
                fam = rel.split('/')[0]
                if fam not in FAMILIES:
                    continue
                out.append(rel)
    except OSError:
        return None
    return out


def _walk_paths(corpus_root):
    """manifest 缺失时的退化路径：遍历 ``018/`` ``big/`` 下的 ``*.py``。"""
    out = []
    for fam in FAMILIES:
        base = os.path.join(corpus_root, fam)
        if not os.path.isdir(base):
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in sorted(dirnames) if not d.startswith('.')]
            for fn in sorted(filenames):
                if fn.endswith('.py'):
                    rel = os.path.relpath(os.path.join(dirpath, fn),
                                          corpus_root).replace('\\', '/')
                    out.append(rel)
    return out


def _stratum_of(rel):
    """分层 = ``<族>/<一级子目录>``（例：``018/gallery``、``big/zenodo``）。"""
    parts = rel.split('/')
    if len(parts) >= 3:
        return parts[0] + '/' + parts[1]
    return parts[0]


#: 建轴调用的名字（静态认这几种；`subplot_mosaic` 也算 —— 它同样在循环里就废）
_AXES_CREATORS = frozenset(('add_axes', 'add_subplot', 'subplot', 'subplots',
                            'subplot_mosaic'))


def _loop_lines(tree):
    """所有 ``for`` / ``while`` 循环体（含 else 分支）覆盖的行号集合。"""
    lines = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.For, ast.AsyncFor, ast.While)):
            for stmt in list(node.body) + list(node.orelse):
                end = getattr(stmt, 'end_lineno', None) or stmt.lineno
                lines.update(range(stmt.lineno, end + 1))
    return lines


def _gate_script(path):
    """进样前的三道**静态**闸（一次读盘、一次 ast.parse）→ 不合格理由（合格返回 ''）。

    这三道闸挡掉的都不是"判据问题"，是**跑不出身份信息**的脚本：

    * ``missing_deps``：import 的第三方包本机没装（sunpy / scikit-image / metpy …）。
      一次 describe 要 3~10 s，这里 1 ms 就能判死；实测 N=12 时 9 份 n/a 里一半是缺库。
    * ``gui_risk``：tkinter / input() / filedialog 之类，无头跑必挂。
    * ``axes_in_loop``：**建轴调用落在 for/while 里**。T8 的身份闸门对这类脚本
      fail-closed 拒写（引擎原文："运行期会按迭代次数反复建轴，'第 k 条是谁'取决于
      循环次数 ⇒ 号牌不稳"），所以它永远拿不到身份信息，只会吃掉一次 30~50 s 的重跑。
      这是本任务实测出来的语料形态（matplotlib gallery 那批普遍如此）。

    挡掉多少、分别多少，全部进 ``gates`` 计数写进报告 —— 不是偷偷换样本。
    结果按绝对路径缓存：自检里同一次运行会反复抽样（同种子 / 换种子），
    全量扫一遍 ≈ 数秒，重复扫就不必了。
    """
    key = os.path.abspath(path)
    if key in _GATE_CACHE:
        return _GATE_CACHE[key]
    why = _gate_script_uncached(key)
    _GATE_CACHE[key] = why
    return why


#: 闸门结果缓存（同一次运行内复用；键 = 绝对路径）
_GATE_CACHE = {}


def _gate_script_uncached(path):
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            text = f.read()
    except OSError:
        return 'unreadable'
    low = text.lower()
    for pat in _GUI_RISK:
        if pat.lower() in low:
            return 'gui_risk:%s' % pat
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return 'unparsable'
    miss = _missing_deps_of(tree)
    if miss:
        return 'missing_deps:%s' % ','.join(miss[:3])
    loop = _loop_lines(tree)
    hits = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if isinstance(fn, ast.Attribute):
            name = fn.attr
        elif isinstance(fn, ast.Name):
            name = fn.id
        else:
            continue
        if name in _AXES_CREATORS:
            hits.append(node.lineno)
    if not hits:
        return 'no_static_axes'
    if any(ln in loop for ln in hits):
        return 'axes_in_loop'
    return ''


def _allocate(sizes, n):
    """最大余数法：把 n 个配额按各层候选数等比分配，余数给小数部分最大的层。

    保证"层里有候选 ⇒ 至少 1 份"（前提 n >= 层数），避免小层永远抽不到。
    """
    total = float(sum(sizes.values())) or 1.0
    if n >= len(sizes):
        for k in sizes:
            sizes[k] = max(sizes[k], 1)          # 先把"每层至少 1"占上
        base = {k: 1 for k in sizes}
        rest = n - len(sizes)
    else:
        base = {k: 0 for k in sizes}
        rest = n
    quota = {k: base[k] + int(rest * sizes[k] / total) for k in sizes}
    used = sum(quota.values())
    remain = n - used
    if remain > 0:
        order = sorted(sizes, key=lambda k: (-(rest * sizes[k] / total
                                               - int(rest * sizes[k] / total)),
                                             k))
        for k in order[:remain]:
            quota[k] += 1
    # 配额不许超过该层候选数；溢出的挪给还有余量的层
    for k in list(quota):
        quota[k] = min(quota[k], sizes[k])
    short = n - sum(quota.values())
    if short > 0:
        for k in sorted(sizes, key=lambda x: -(sizes[x] - quota[x])):
            if short <= 0:
                break
            add = min(short, sizes[k] - quota[k])
            quota[k] += add
            short -= add
    return quota


def _build_sample(corpus_root, n, seed):
    """→ (候选列表 [(层, rel)], 配额 by 层, 池大小 by 层, 来源, 闸门计数)。

    候选列表**层间交错**：这样"按序尝试取前 N 份跑通的"天然跨层均匀，
    不会因为 big/zenodo 排前面就退化成单层抽样。

    进样前先过 ``_gate_script`` 三道静态闸（缺库 / 要 GUI / 建轴在循环里）：
    挡掉的是"跑不出身份信息"的脚本，计数照样进报告（``gates``）。
    """
    paths = _load_manifest_paths(corpus_root)
    src = 'manifest'
    if not paths:
        paths = _walk_paths(corpus_root)
        src = 'walk'
    gates = {'candidates': 0, 'eligible': 0, 'missing_deps': 0, 'gui_risk': 0,
             'axes_in_loop': 0, 'no_static_axes': 0, 'unparsable': 0,
             'unreadable': 0}
    eligible = []
    for rel in sorted(set(paths)):
        gates['candidates'] += 1
        why = _gate_script(os.path.join(corpus_root, rel))
        if why:
            key = why.split(':')[0]
            gates[key] = gates.get(key, 0) + 1
            continue
        gates['eligible'] += 1
        eligible.append(rel)
    by_stratum = {}
    for rel in eligible:
        by_stratum.setdefault(_stratum_of(rel), []).append(rel)
    pools = {k: len(v) for k, v in by_stratum.items()}
    quota = _allocate(dict(pools), n)
    picked = {}
    for stratum in sorted(by_stratum):
        rng = random.Random('%d|%s' % (seed, stratum))
        rows = list(by_stratum[stratum])
        rng.shuffle(rows)
        # 每层多备些候选（配额 × ATTEMPT_CAP_X）：跑不动的记 n/a 之后还能往后拿，
        # 不然"按序尝试取前 N 份跑得通的"会卡在 N 份候选上（层内洗牌顺序即尝试顺序）。
        want = max(quota.get(stratum, 0), 1) * ATTEMPT_CAP_X
        picked[stratum] = rows[:min(len(rows), want)]
    order = []
    idx = 0
    while True:
        added = False
        for stratum in sorted(picked):
            if idx < len(picked[stratum]):
                order.append((stratum, picked[stratum][idx]))
                added = True
        if not added:
            break
        idx += 1
    return (order, quota, pools, src, gates)


# ---------------------------------------------------------------------------
# §3 身份判据（纯函数 —— 收尾有一组"判据有牙"的自检直接打这些函数）
# ---------------------------------------------------------------------------
def _num_eq(a, b):
    try:
        return abs(float(a) - float(b)) <= TOL
    except (TypeError, ValueError):
        return False


def _val_eq(a, b):
    """标量或等长数值数组逐项相等（pos/xlim/ylim 用）。"""
    if isinstance(a, (list, tuple)) or isinstance(b, (list, tuple)):
        if not isinstance(a, (list, tuple)) or not isinstance(b, (list, tuple)):
            return False
        if len(a) != len(b):
            return False
        return all(_num_eq(x, y) for x, y in zip(a, b))
    return _num_eq(a, b)


def _pick_requests(payload):
    """从 describe 产物里挑**目标轴 + 每根轴的请求值** → (requests, why)。

    requests = ``{ax_index: {'pos': [x, y, w, h], 'title_fontsize': 17.5}}``
    （字段"能给就给"：pos 只给非 colorbar、非 aspect 锁定的轴；title 字号都给）。

    为什么是这两个字段：
      * ``pos`` 是 0.1.17 事故的现场字段（矩形写到隔壁轴）；
      * ``title_fontsize`` 是**每根轴都有值**的标量，给判据加第二条独立通路 ——
        两条都错位的概率远低于一条。
    为什么不碰 ``aspect_locked`` 轴：锁定长宽比的轴运行时 pos 会被 matplotlib
    重新算过（`_team014/axis_identity.md` §5.1 已登记为既有行为），拿它当判据会
    造出假 WRONG —— 判据要干净，宁可不测这一根。
    """
    axes = [a for a in payload.get('axes') or []
            if isinstance(a.get('index'), int)
            and not isinstance(a.get('index'), bool)]
    cand = [a for a in axes if not a.get('is_colorbar') and a.get('pos')]
    cand.sort(key=lambda a: a['index'])
    picked = cand[:3]
    if len(picked) < 2:
        return (None, 'identifiable_axes_lt2')

    req = {}
    seen_pos_x = []
    for slot, ax in enumerate(picked):
        k = ax['index']
        ent = {}
        if not ax.get('aspect_locked'):
            p = [float(x) for x in ax['pos'][:4]]
            if len(p) == 4:
                d = _POS_DELTAS[slot % len(_POS_DELTAS)]
                x = p[0] + d
                if x < 0 or x + p[2] > 1.0:
                    x = p[0] - d
                if not (x < 0 or x + p[2] > 1.0):
                    # 唯一化：同一网格里同一列的轴 x 相同，撞了就再错开一点
                    for _ in range(8):
                        if all(abs(x - v) > 1e-4 for v in seen_pos_x):
                            break
                        x = x + 0.001 if (x + 0.001 + p[2] <= 1.0) else x - 0.001
                    if all(abs(x - v) > 1e-4 for v in seen_pos_x):
                        p[0] = round(x, 4)
                        seen_pos_x.append(p[0])
                        ent['pos'] = p
        t = _TITLE_SIZES[slot % len(_TITLE_SIZES)]
        if _num_eq(ax.get('title_fontsize'), t):
            t = round(t + 0.5, 4)               # 别跟现值撞（否则"没写"和"已等值"分不开）
        ent['title_fontsize'] = t
        if ent:
            req[k] = ent
    if len(req) < 2:
        return (None, 'requests_lt2')
    return (req, '')


#: 读回比对的字段（describe 认得的、能逐轴比的数值/枚举字段；判据只挑其中被请求的）
_READBACK_FIELDS = ('pos', 'xlim', 'ylim', 'clim', 'title_fontsize',
                    'label_fontsize', 'tick_fontsize', 'label_fontsize_y',
                    'tick_fontsize_y', 'cmap', 'xscale', 'yscale', 'grid',
                    'spines', 'cell', 'is_colorbar', 'aspect_locked')


def _readback_map(payload):
    """读回 → ``{ax_index: {字段: 值}}``（字段见 ``_READBACK_FIELDS``）。"""
    out = {}
    for a in payload.get('axes') or []:
        i = a.get('index')
        if isinstance(i, int) and not isinstance(i, bool):
            out[i] = {f: a.get(f) for f in _READBACK_FIELDS}
    return out


def _judge(req, readback, applied_res):
    """核心判据 → 结果字典（verdict ∈ ok/refused/WRONG，外加明细）。

    ``applied_res`` 是 ``apply --json`` 的 ``files[0]``（含 reason/changes/
    pending/block_lines/style）。承诺的判据只有一句：
    **声称改了 ``ax_k.f`` ⇒ 读回 ``ax_k.f`` 必须等于请求值，且请求值不许出现在别的轴上。**
    """
    changes = [str(c) for c in (applied_res.get('changes') or [])]
    pending = [str(p) for p in (applied_res.get('pending') or [])]
    block = int(applied_res.get('block_lines') or 0)
    style = str(applied_res.get('style') or '')
    landed, unlanded, leaked, claimed_nl, silent_drop = [], [], [], [], []

    for k, fields in sorted(req.items()):
        for fname, want in sorted(fields.items()):
            key = 'ax%d.%s' % (k, fname)
            got = (readback.get(k) or {}).get(fname)
            if _val_eq(got, want):
                landed.append(key)
            else:
                unlanded.append(key)
                if key in changes:                     # ① 原位通道声称改了却没落地
                    claimed_nl.append('%s 声称改了，读回是 %r（要 %r）'
                                      % (key, got, want))
                elif block and key not in pending:
                    silent_drop.append(key)            # 见文件头：不判 WRONG，只计数
            # ② 值跑到别的轴上（与"有没有声称"无关 —— 出现即错位）
            for j, rb in sorted(readback.items()):
                if j == k:
                    continue
                if _val_eq((req.get(j) or {}).get(fname), want):
                    continue                # 那根轴本来也要同一个值（构造上不会，保险）
                if _val_eq((rb or {}).get(fname), want):
                    leaked.append('%s 的 %r 出现在 ax%d 身上' % (key, want, j))

    if leaked:
        return {'verdict': 'WRONG', 'kind': 'leak', 'why': '；'.join(leaked[:4]),
                'landed': landed, 'unlanded': unlanded, 'silent_drop': silent_drop,
                'style': style, 'block_lines': block}
    if claimed_nl:
        return {'verdict': 'WRONG', 'kind': 'claimed_not_landed',
                'why': '；'.join(claimed_nl[:4]),
                'landed': landed, 'unlanded': unlanded, 'silent_drop': silent_drop,
                'style': style, 'block_lines': block}
    if landed:
        return {'verdict': 'ok', 'kind': 'landed',
                'why': '落地 %d 个键' % len(landed),
                'landed': landed, 'unlanded': unlanded, 'silent_drop': silent_drop,
                'style': style, 'block_lines': block}
    return {'verdict': 'refused', 'kind': 'nothing_landed',
            'why': '没有一个键落地（%s）' % (applied_res.get('reason') or '?'),
            'landed': landed, 'unlanded': unlanded, 'silent_drop': silent_drop,
            'style': style, 'block_lines': block}


# ---------------------------------------------------------------------------
# §4 单份脚本跑一遍（describe → apply --write → 重跑读回）
# ---------------------------------------------------------------------------
#: 静态预筛：这些形态一跑就可能开窗/等输入（Agg 挡不住脚本自己开 GUI）——
#: 判据不适用，记 n/a，绝不让它真跑（0.1.9 的教训）。
_GUI_RISK = ('tkinter', 'filedialog', 'messagebox', 'PyQt', 'PySide', 'wx.Frame',
             'wx.App', 'simplegui', 'input(', 'raw_input(')


def _gui_risk(script):
    try:
        with open(script, 'r', encoding='utf-8', errors='replace') as f:
            text = f.read()
    except OSError:
        return 'unreadable'
    low = text.lower()
    for pat in _GUI_RISK:
        if pat.lower() in low:
            return pat
    return ''


#: 依赖预筛的模块名缓存（find_spec 一次就够；整个抽样过程共享）
_SPEC_CACHE = {}


def _missing_deps(script):
    """静态预筛：脚本 import 的**第三方包**里，本机没装的有哪些（→ 直接记 n/a）。

    这是**省时间**用的，不是省事：语料里大量脚本跑不动的头号原因是缺库
    （sunpy / scikit-image / metpy …），一次 describe 要 3~10 s，而这里只要 1 ms。
    真跑实测（N=12）：12 次尝试里 9 次是 n/a、其中一半以上是缺库/缺数据 ——
    缺库这半可以先挡掉，把预算留给真能跑的那批。

    解析不了就返回 ``['<unparsable>']`` —— 那种脚本跑起来也多半是 SyntaxError，
    同样不该占预算。
    """
    if os.environ.get('MPLTWEAK_CORPUS_NO_DEPS'):
        return []
    try:
        with open(script, 'r', encoding='utf-8', errors='replace') as f:
            tree = ast.parse(f.read())
    except (OSError, SyntaxError, ValueError):
        return ['<unparsable>']
    return _missing_deps_of(tree)


def _missing_deps_of(tree):
    """``_missing_deps`` 的核心：给定已解析的 AST，返回没装的顶层第三方模块名。

    只看**顶层模块名**（``sklearn.cluster`` → ``sklearn``），标准库与 ``mpltweak``
    自身跳过；``from . import x`` 这类相对导入跳过。
    """
    if os.environ.get('MPLTWEAK_CORPUS_NO_DEPS'):
        return []
    names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names += [a.name.split('.')[0] for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                names.append(node.module.split('.')[0])
    miss = []
    for name in sorted(set(names)):
        if not name or name in sys.stdlib_module_names or name == 'mpltweak':
            continue
        if name not in _SPEC_CACHE:
            try:
                _SPEC_CACHE[name] = importlib.util.find_spec(name) is not None
            except (ImportError, ValueError, AttributeError):
                _SPEC_CACHE[name] = False
        if not _SPEC_CACHE[name]:
            miss.append(name)
    return miss




#: 脚本侧跑不起来的档（记 n/a —— 那种"没写"不是工具拒写，是脚本根本跑不通）
_NA_CODES = ('script_cannot_run', 'script_needs_args',
             'script_needs_gui_or_headless_impossible')

#: 拒写档（fail-closed，**合法**）。口径正在变（t16 会把"reason=best_effort 但一个字都
#: 没落地"拆成独立的 `refused`），所以这里**新旧值都认**；认不出的新值一律按 refused
#: 处理 —— 没落地 + 字节未变，怎么解释都不是 WRONG，但也不许悄悄算成 n/a。
_REFUSED_REASONS = ('refused', 'fail_closed', 'rejected', 'best_effort', 'fail',
                    'bad_params', 'stale', 'preview', 'layout_conflict', 'no_axes',
                    'no_change')


def _classify_no_write(reason, error_code, attribution, rc):
    """「什么都没写」→ (verdict, why)。纯函数，自检⑧直接钉它（口径新旧都要认）。"""
    for tag in (error_code, attribution):
        if str(tag or '') in _NA_CODES:
            return ('na', 'apply_%s' % tag)
    label = str(reason or error_code or 'rc%s' % rc)
    if label in _REFUSED_REASONS or reason is None:
        return ('refused', 'apply_%s' % label)
    # 认不出的新口径：没落地 + 字节未变 ⇒ 合法拒写（不是 WRONG），但把原词留在 why 里
    return ('refused', 'apply_%s' % label)


def _identity_one(tree, stratum, rel, corpus_root, workdir, timeout):
    """一份脚本的完整身份级回归 → 结果 dict（含 verdict / 请求 / 读回 / 耗时）。"""
    t0 = time.time()
    res = {'stratum': stratum, 'path': rel, 'verdict': 'na', 'why': '',
           'requests': {}, 'readback': {}, 'apply': {}, 'seconds': 0.0,
           'write_seconds': 0.0}
    src = os.path.join(corpus_root, rel)
    if not os.path.isfile(src):
        res['why'] = 'missing_file'
        res['seconds'] = round(time.time() - t0, 2)
        return res
    risk = _gui_risk(src)
    if risk:
        res['why'] = 'gui_risk:%s' % risk
        res['seconds'] = round(time.time() - t0, 2)
        return res
    miss = _missing_deps(src)
    if miss:
        res['why'] = 'missing_deps:%s' % ','.join(miss[:4])
        res['seconds'] = round(time.time() - t0, 2)
        return res
    # 语料目录**只读**：一律拷到隔离 workdir 再跑（脚本的副产物也留在这里）
    local = os.path.join(workdir, os.path.basename(rel))
    try:
        shutil.copy2(src, local)
    except OSError as e:
        res['why'] = 'copy_failed:%s' % e.__class__.__name__
        res['seconds'] = round(time.time() - t0, 2)
        return res
    # 写回前的字节指纹：后面判断"到底动没动磁盘"只看它（不信 JSON 的自述）
    sha_before = _sha256(local)

    # ① describe：拿到号牌表（axes[].index = fig.axes 序号）与当前值
    d1, why = _describe(tree, local, workdir, timeout)
    if d1 is None:
        res['why'] = why
        res['seconds'] = round(time.time() - t0, 2)
        return res
    # ② 造请求：每根目标轴一个**互不相同**的值
    req, why = _pick_requests(d1)
    res['requests'] = req or {}
    if req is None:
        res['why'] = why
        res['seconds'] = round(time.time() - t0, 2)
        return res
    pfile = os.path.join(workdir, os.path.basename(rel) + '.params.json')
    payload = {'version': 4}
    if isinstance(d1.get('fig_index'), int):
        payload['fig_index'] = d1['fig_index']
    payload['axes'] = [dict({'index': k}, **fields)
                       for k, fields in sorted(req.items())]
    try:
        with open(pfile, 'w', encoding='utf-8') as f:
            json.dump(payload, f, ensure_ascii=False, indent=1)
    except OSError as e:
        res['why'] = 'params_write_failed:%s' % e.__class__.__name__
        res['seconds'] = round(time.time() - t0, 2)
        return res

    # ③ apply --write（这是**唯一**会改磁盘的一步）
    tw = time.time()
    rc, out, err = _run_cli(tree, ['apply', local, '--params', pfile,
                                   '--write', '--json'], workdir, max(timeout, 300))
    res['write_seconds'] = round(time.time() - tw, 2)
    j = _extract_json(out)
    f0 = {}
    if isinstance(j, dict):
        f0 = (j.get('files') or [{}])[0] or {}
        res['apply'] = {'rc': rc, 'reason': f0.get('reason'),
                        'applied': f0.get('applied'),
                        'style': f0.get('style'),
                        'changes': f0.get('changes') or [],
                        'pending': f0.get('pending') or [],
                        'block_lines': f0.get('block_lines'),
                        'error_code': j.get('error_code'),
                        'attribution': f0.get('attribution'),
                        'verified': f0.get('verified'),
                        'semantic': f0.get('semantic')}
    else:
        res['apply'] = {'rc': rc, 'reason': None, 'applied': None,
                        'raw_tail': (out or err or '')[-300:]}
    if rc == -9:
        res['why'] = 'apply_timeout'
        res['seconds'] = round(time.time() - t0, 2)
        return res
    applied = bool(f0.get('applied'))
    # **不许只信 `applied`**：0.1.17 的病根就是"JSON 说改了"被当成"真的改了"。
    # 反过来也一样 —— 现在的引擎在块通道落地的同时会报 `changes: []`（块通道没有
    # 逐字段声称），若只看 `applied/changes` 就会把"真写进去了"当成"没写"。
    # 所以这里只用一个**客观事实**做分叉：磁盘上那份脚本的字节变没变。
    changed = _sha256(local) != sha_before
    res['changed'] = changed
    if not applied and not changed:
        # 没写 = 拒写（fail-closed 合法）或脚本侧跑不起来（n/a）——**都不算 WRONG**
        verdict, why = _classify_no_write(
            f0.get('reason'), (j or {}).get('error_code') if isinstance(j, dict) else None,
            f0.get('attribution'), rc)
        res['verdict'], res['why'] = verdict, why
        res['seconds'] = round(time.time() - t0, 2)
        return res

    # ④ **重跑并读回**：不改源码文本比对，读的是运行期状态（脚本被重跑一遍）
    d2, why2 = _describe(tree, local, workdir, timeout)
    if d2 is None:
        res['verdict'] = 'WRONG'
        res['why'] = 'write_broke_script:%s' % why2
        res['seconds'] = round(time.time() - t0, 2)
        return res
    res['readback'] = _readback_map(d2)
    verdict = _judge(req, res['readback'], f0)
    res.update({k: v for k, v in verdict.items() if k != 'verdict'})
    res['verdict'] = verdict['verdict']
    res['seconds'] = round(time.time() - t0, 2)
    return res


# ---------------------------------------------------------------------------
# §5 正对照：`reviewer_repro.py`（0.1.17 事故的最小复现）
# ---------------------------------------------------------------------------
#: 内嵌副本：与 `_team014/reviewer_repro.py` **逐字一致**（22 行，发布窗口里那份
#: 探针目录可能被清理，正对照不能因此断掉；文件在就用文件，不在就用这份）。
_REPRO_SRC = '''import matplotlib.pyplot as plt
import numpy as np
fig = plt.figure(figsize=(12, 7))
x = np.linspace(0, 1, 100)
ax1 = fig.add_axes([0.125, 0.594, 0.352, 0.353])
m1 = ax1.contourf(np.random.rand(10, 10))
ax1.set_title("(a)")
ax_inset = fig.add_axes([0.427, 0.599, 0.045, 0.095])
ax_inset.contourf(np.random.rand(10, 10))
ax_inset.set_title("inset")
cax1 = fig.add_axes([0.140, 0.535, 0.320, 0.015])
plt.colorbar(m1, orientation="horizontal", cax=cax1)
ax2 = fig.add_axes([0.525, 0.597, 0.371, 0.347])
m2 = ax2.contourf(np.random.rand(10, 10))
ax2.set_title("(b)")
cax2 = fig.add_axes([0.535, 0.535, 0.350, 0.015])
plt.colorbar(m2, orientation="horizontal", cax=cax2)
ax3 = fig.add_axes([0.125, 0.060, 0.770, 0.397])
ax3.plot(x, np.sin(6 * x))
ax3.set_title("(c)")
ax3.set_xlim(1961, 2021)
fig.savefig("repro.png", dpi=100)
'''

#: 正对照的请求：0.1.17 在这里把**第 3 根轴**（ax2，"(b)" 面板）的 xlim 写进了
#: `ax3 = fig.add_axes(...)` 那根轴（运行期 index 5）—— rc=0 且声称改了 ax3。
_CONTROL_REQ = {'version': 4, 'fig_index': 0,
                'axes': [{'index': 3, 'xlim': [0.0, 2.0]}]}


def _control_case(tree, workdir, timeout):
    """在一棵树上跑正对照 → (verdict_dict|None, why)。"""
    d = os.path.join(workdir, 'repro')
    shutil.rmtree(d, ignore_errors=True)
    os.makedirs(d)
    local = os.path.join(d, 'reviewer_repro.py')
    if os.path.isfile(REPRO_DEFAULT):
        shutil.copy2(REPRO_DEFAULT, local)
        origin = REPRO_DEFAULT
    else:
        with open(local, 'w', encoding='utf-8') as f:
            f.write(_REPRO_SRC)
        origin = '内嵌副本'
    pfile = os.path.join(d, 'control.json')
    with open(pfile, 'w', encoding='utf-8') as f:
        json.dump(_CONTROL_REQ, f, ensure_ascii=False, indent=1)
    sha_before = _sha256(local)
    rc, out, err = _run_cli(tree, ['apply', local, '--params', pfile,
                                   '--write', '--json'], d, max(timeout, 300))
    j = _extract_json(out)
    f0 = ((j or {}).get('files') or [{}])[0] or {}
    if rc == -9:
        return (None, 'control_apply_timeout')
    changed = _sha256(local) != sha_before
    if not f0.get('applied') and not changed:
        return ({'verdict': 'refused', 'why': 'apply_%s'
                 % (f0.get('reason') or (j or {}).get('error_code') or rc),
                 'origin': origin, 'changed': changed,
                 'apply': {'rc': rc, 'reason': f0.get('reason')}}, '')
    d2, why2 = _describe(tree, local, d, timeout)
    if d2 is None:
        return ({'verdict': 'WRONG', 'why': 'write_broke_script:%s' % why2,
                 'origin': origin, 'changed': changed}, '')
    rb = _readback_map(d2)
    req = {3: {'xlim': [0.0, 2.0]}}
    out_j = _judge(req, rb, f0)
    out_j['origin'] = origin
    out_j['changed'] = changed
    out_j['apply'] = {'rc': rc, 'reason': f0.get('reason'),
                      'changes': f0.get('changes') or [],
                      'style': f0.get('style'),
                      'block_lines': f0.get('block_lines')}
    out_j['readback'] = {'ax3.xlim': rb.get(3, {}).get('xlim'),
                         'ax5.xlim': rb.get(5, {}).get('xlim')}
    return (out_j, '')


# ---------------------------------------------------------------------------
# §6 判据自检（不跑语料，直接打纯函数 —— "判据有没有牙"的第一道保险）
# ---------------------------------------------------------------------------
def _selfcheck():
    """三组合成输入：对得上的不许报 WRONG；漏到隔壁 / 声称改了没落地必须报 WRONG。"""
    req = {0: {'pos': [0.1, 0.1, 0.2, 0.2], 'title_fontsize': 21.5},
           1: {'pos': [0.5, 0.1, 0.2, 0.2], 'title_fontsize': 17.5}}
    good_rb = {0: {'pos': [0.1, 0.1, 0.2, 0.2], 'title_fontsize': 21.5},
               1: {'pos': [0.5, 0.1, 0.2, 0.2], 'title_fontsize': 17.5}}
    ok_res = {'changes': ['ax0.pos', 'ax0.title_fontsize',
                          'ax1.pos', 'ax1.title_fontsize'],
              'pending': [], 'block_lines': 0, 'style': 'inplace', 'reason': 'ok'}
    v = _judge(req, good_rb, ok_res)
    check('自检①：读回全对 ⇒ 必须判 ok（判据不许反向报警）',
          v['verdict'] == 'ok', v.get('kind', ''))

    # ② 值漏到隔壁轴（原位通道 changes 也写着它 —— 正是 0.1.17 的形态）
    leak_rb = {0: {'pos': [0.1, 0.1, 0.2, 0.2], 'title_fontsize': 21.5},
               1: {'pos': [0.1, 0.1, 0.2, 0.2], 'title_fontsize': 17.5}}
    v2 = _judge(req, leak_rb, ok_res)
    check('自检②：ax1 的值漏到 ax0 身上 ⇒ 必须判 WRONG（轴身份错位）',
          v2['verdict'] == 'WRONG', v2.get('kind', ''))

    # ③ 原位通道声称改了、读回没变（0.1.17 的 `ax3.xlim` 就是这一档）
    same_rb = {0: {'pos': [0.125, 0.594, 0.352, 0.353], 'title_fontsize': 12.0},
               1: {'pos': [0.427, 0.599, 0.045, 0.095], 'title_fontsize': 12.0}}
    v3 = _judge(req, same_rb, ok_res)
    check('自检③：声称改了却读回原值 ⇒ 必须判 WRONG（写成功≠写对）',
          v3['verdict'] == 'WRONG' and v3.get('kind') == 'claimed_not_landed',
          v3.get('kind', ''))

    # ④ 块通道：没落地、没声称（也不在 pending）⇒ 只记 silent_drop，不判 WRONG
    v4 = _judge(req, same_rb, {'changes': [], 'pending': [], 'block_lines': 6,
                               'style': 'block', 'reason': 'ok'})
    check('自检④：块通道悄悄少写 ⇒ 只计 silent_drop，不许冒充 WRONG',
          v4['verdict'] == 'refused' and len(v4['silent_drop']) == 4,
          'silent_drop=%d' % len(v4['silent_drop']))

    # ⑤ 抽样的确定性：同种子两次抽到的必须是同一批
    corpus = os.environ.get('MPLTWEAK_CORPUS_WORK')
    if corpus and os.path.isdir(corpus):
        a = _build_sample(corpus, 12, SEED_DEFAULT)[0]
        b = _build_sample(corpus, 12, SEED_DEFAULT)[0]
        other = _build_sample(corpus, 12, SEED_DEFAULT + 1)[0]
        check('自检⑤：同种子抽样可复现（并且换种子会变）',
              a == b and a != other, '%d 份' % len(a))
    else:
        print('SKIP | 自检⑤（需要语料根）')

    # ⑥ 读回字段表必须覆盖正对照要看的字段（xlim）—— 少了它，正对照会**假绿**
    #    （实测踩过：readback 里没有 xlim ⇒ 请求值一律读成 None ⇒ 修复后的树被误判 refused）
    rb = _readback_map({'axes': [{'index': 3, 'xlim': [0.0, 2.0], 'pos': [0, 0, 1, 1],
                                  'title_fontsize': 12.0}]})
    check('自检⑥：读回字段表覆盖 xlim/pos/title_fontsize（否则正对照假绿）',
          _val_eq(rb.get(3, {}).get('xlim'), [0.0, 2.0])
          and rb.get(3, {}).get('pos') is not None
          and _num_eq(rb.get(3, {}).get('title_fontsize'), 12.0),
          str(sorted(rb.get(3, {}))))

    # ⑦ "JSON 说没写" 不许当结论：字节没变才是真没写（写成功≠写对，反向也成立）
    here = os.path.abspath(__file__)
    before = _sha256(here)
    check('自检⑦：字节指纹能分辨"改过 / 没改过"（判分叉只看磁盘，不信 JSON 自述）',
          bool(before) and _sha256(here) == before
          and _sha256(os.path.join(HERE, 'no_such_file_%d.py' % os.getpid())) == '',
          before[:12])

    # ⑧ 拒写档的**新旧口径都要认**：t16 会把"best_effort 但一个字都没落地"拆成独立的
    #    `refused`；旧引擎报的是 `reason=best_effort` + `error_code=nothing_applied`。
    #    两种都必须归 refused（合法拒写），不许因为认不出新词就漏判成 WRONG 或 n/a。
    old_a = _classify_no_write('best_effort', 'nothing_applied', None, 1)
    old_b = _classify_no_write('best_effort', None, None, 1)
    new_a = _classify_no_write('refused', None, None, 1)
    new_b = _classify_no_write('fail_closed', 'refused', None, 1)
    future = _classify_no_write('some_future_tag_t16_might_use', None, None, 1)
    na_c = _classify_no_write('fail', 'script_cannot_run', None, 1)
    check('自检⑧：拒写档新旧口径都归 refused（t16 拆词后不许漏判）',
          old_a[0] == old_b[0] == new_a[0] == new_b[0] == future[0] == 'refused'
          and na_c[0] == 'na',
          '%s / %s / %s / %s / %s / %s'
          % (old_a[0], old_b[0], new_a[0], new_b[0], future[0], na_c[0]))


# ---------------------------------------------------------------------------
# §7 汇总与产出
# ---------------------------------------------------------------------------
def _write_outputs(out_dir, results, meta):
    try:
        os.makedirs(out_dir, exist_ok=True)
    except OSError:
        return (None, None)
    jsonl = os.path.join(out_dir, 'results.jsonl')
    with open(jsonl, 'w', encoding='utf-8') as f:
        f.write(json.dumps({'meta': meta}, ensure_ascii=False) + '\n')
        for r in results:
            f.write(json.dumps(r, ensure_ascii=False) + '\n')
    md = os.path.join(out_dir, 'SUMMARY.md')
    cnt = {}
    for r in results:
        cnt[r['verdict']] = cnt.get(r['verdict'], 0) + 1
    lines = ['# T17 语料身份级回归（SUMMARY）', '',
             '* 语料根：`%s`' % meta['corpus'],
             '* 被测树：`%s`' % meta['tree'],
             '* 种子 / N / 尝试上限：`%s` / `%s` / `%s`'
             % (meta['seed'], meta['n'], meta['attempt_cap']),
             '* 抽样来源 / 池：`%s` / `%s`' % (meta['source'], meta['pools']),
             '* 配额：`%s`' % meta['quota'],
             '* 并发 / 单份超时：`%s` / `%ss`' % (meta['jobs'], meta['timeout']),
             '* 总耗时：**%.1fs**（写盘 %.1fs）'
             % (meta['seconds'], meta['write_seconds']),
             '* describe 尝试份数 / 真跑通份数：`%d` / `%d`'
             % (meta['attempts'], meta['runnable']), '',
             '## 三栏', '',
             '| 栏 | 数 |', '|----|----|']
    for k in ('ok', 'refused', 'WRONG', 'na'):
        lines.append('| `%s` | %d |' % (k, cnt.get(k, 0)))
    lines += ['', '## WRONG 清单', '']
    wrong = [r for r in results if r['verdict'] == 'WRONG']
    if not wrong:
        lines.append('（无 —— WRONG = 0）')
    else:
        lines.append('| 脚本 | 类 | 证据 |')
        lines.append('|------|----|------|')
        for r in wrong:
            lines.append('| `%s` | `%s` | %s |'
                         % (r['path'], r.get('kind') or r.get('why'),
                            (r.get('why') or '').replace('|', '\\|')))
    lines += ['', '## 逐层统计', '', '| 层 | ok | refused | WRONG | na |',
              '|----|----|---------|-------|----|']
    strata = sorted({r['stratum'] for r in results})
    for s in strata:
        g = [r for r in results if r['stratum'] == s]
        lines.append('| `%s` | %d | %d | %d | %d |'
                     % (s,
                        sum(1 for r in g if r['verdict'] == 'ok'),
                        sum(1 for r in g if r['verdict'] == 'refused'),
                        sum(1 for r in g if r['verdict'] == 'WRONG'),
                        sum(1 for r in g if r['verdict'] == 'na')))
    with open(md, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')
    return (jsonl, md)


def _main(argv):
    ap = argparse.ArgumentParser(prog='test_corpus_identity.py')
    ap.add_argument('--limit', type=int, default=None,
                    help='临时压小 N（冒烟用）')
    ap.add_argument('--seed', type=int, default=SEED_DEFAULT)
    ap.add_argument('--jobs', type=int, default=4, help='并发数（默认 4）')
    ap.add_argument('--corpus', default=None,
                    help='语料根（默认取 MPLTWEAK_CORPUS_WORK）')
    ap.add_argument('--tree', default=None,
                    help='被测树（默认 = 本文件所在仓库；正对照时传 v0.1.17 worktree）')
    ap.add_argument('--out', default=None, help='产出目录')
    ap.add_argument('--timeout', type=int, default=120,
                    help='单份脚本单次 CLI 的墙钟上限（秒，默认 120）')
    ap.add_argument('--control', default=None, metavar='TREE',
                    help='额外跑正对照：给一棵**修复前**的树（v0.1.17），'
                         '断言同一条判据在它上面必须判 WRONG')
    ap.add_argument('--keep', action='store_true', help='保留临时工作目录（排查用）')
    args = ap.parse_args(argv)

    corpus = args.corpus or CORPUS_DEFAULT
    if not corpus:
        print('SKIP | 未设 MPLTWEAK_CORPUS_WORK（或 --corpus）—— '
              '语料身份级回归需要仓库外的真实语料，默认不跑。')
        # 包装器契约（tests/test_suites.py）：「rc==0 **且**输出含 `ALL PASS`」。
        # **跳过一个依赖外部语料的套件 ≠ 失败** —— 与 test_gui_smoke.py 的 skip 分支
        # 同一写法；少了这行，CI 上（本来就没有这个环境变量）凡跑 pytest 的腿全会红。
        print('ALL PASS（0 条真跑：未设 MPLTWEAK_CORPUS_WORK，语料级回归默认跳过）')
        return 0
    corpus = os.path.abspath(corpus)
    if not os.path.isdir(corpus):
        print('SKIP | 语料根不存在：%s' % corpus)
        print('ALL PASS（0 条真跑：语料根不存在，语料级回归跳过）')
        return 0
    tree = os.path.abspath(args.tree or ROOT)
    out_dir = os.path.abspath(args.out or OUTDIR_DEFAULT)
    n = args.limit or int(os.environ.get('MPLTWEAK_CORPUS_N') or N_DEFAULT)
    cap = n * ATTEMPT_CAP_X
    _ensure_tmp_writable()

    print('=' * 72)
    print('T17 语料身份级回归：验「写对没」，不只是「写成功没」')
    print('  语料根   : %s' % corpus)
    print('  被测树   : %s' % tree)
    print('  种子 / N : %s / %s（尝试上限 %s）' % (args.seed, n, cap))
    print('  并发     : %s（单份单次超时 %ss）' % (args.jobs, args.timeout))
    print('  产出     : %s' % out_dir)
    print('=' * 72)

    # ---- 判据自检（先证明"判据有牙"，再拿它跑语料）----
    _selfcheck()

    order, quota, pools, source, gates = _build_sample(corpus, n, args.seed)
    print('抽样：来源 %s / 候选 %d → 过闸合格 %d（缺库 %d / 要 GUI %d / 建轴在循环 %d /'
          ' 无可静态识别的建轴 %d / 解析不了 %d）'
          % (source, gates['candidates'], gates['eligible'],
             gates['missing_deps'], gates['gui_risk'], gates['axes_in_loop'],
             gates['no_static_axes'], gates['unparsable']))
    print('      层 %d / 配额 %s' % (len(quota), quota))
    if not order:
        check('抽样非空（语料根下 018/0018 两族至少有 *.py）', False, corpus)
        print('FAILED')
        return 1

    workroot = os.path.join(_TMPROOT, 'corpus_id_%d' % int(time.time()))
    os.makedirs(workroot, exist_ok=True)
    results = []
    runnable = 0
    submitted = 0
    tw_all = 0.0
    t_all = time.time()

    def _one(idx_rel):
        idx, (stratum, rel) = idx_rel
        wd = os.path.join(workroot, 'w%03d' % idx)
        os.makedirs(wd, exist_ok=True)
        try:
            r = _identity_one(tree, stratum, rel, corpus, wd, args.timeout)
        except BaseException as e:                          # noqa: BLE001 - 兜底
            r = {'stratum': stratum, 'path': rel, 'verdict': 'na',
                 'why': 'harness_error:%s:%s' % (type(e).__name__, str(e)[:200]),
                 'seconds': 0.0, 'write_seconds': 0.0}
            if os.environ.get('MPLTWEAK_DEBUG'):
                traceback.print_exc()
        finally:
            if not args.keep:
                shutil.rmtree(wd, ignore_errors=True)
        return r

    # 按序尝试、并发执行：只在"已跑通 + 在途"还不足 N 时才继续下料，
    # 这样"取前 N 份跑得通的"这层口径成立（并发窗口最多多跑 jobs-1 份，报告如实写）。
    with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as ex:
        qi = 0
        futs = {}
        while True:
            while (qi < len(order) and len(futs) < args.jobs
                   and runnable + len(futs) < n):
                fut = ex.submit(_one, (qi, order[qi]))
                futs[fut] = qi
                qi += 1
                submitted += 1
            if not futs:
                break
            done, _ = wait(list(futs), return_when=FIRST_COMPLETED)
            for fut in done:
                futs.pop(fut, None)
                r = fut.result()
                results.append(r)
                tw_all += float(r.get('write_seconds') or 0)
                if r['verdict'] != 'na':
                    runnable += 1
            if runnable >= n:
                # 凑够 N 份跑得通的就收工（已提交的照样收尸，不许留悬空线程）
                break
        for fut in list(futs):
            r = fut.result()
            results.append(r)
            tw_all += float(r.get('write_seconds') or 0)
            if r['verdict'] != 'na':
                runnable += 1
    t_all = time.time() - t_all
    # 结果按"层 + 路径"排序：并发完成的先后不影响产出（results.jsonl 可复现）
    results.sort(key=lambda r: (r['stratum'], r['path']))

    cnt = {}
    for r in results:
        cnt[r['verdict']] = cnt.get(r['verdict'], 0) + 1
    ok = cnt.get('ok', 0)
    refused = cnt.get('refused', 0)
    wrong = cnt.get('WRONG', 0)
    na = cnt.get('na', 0)
    silent_drop = sum(len(r.get('silent_drop') or []) for r in results)
    keys_total = sum(sum(len(v) for v in (r.get('requests') or {}).values())
                     for r in results)

    print('-' * 72)
    print('三栏：ok=%d  refused=%d  WRONG=%d   （补充：n/a=%d  静默丢弃键=%d）'
          % (ok, refused, wrong, na, silent_drop))
    print('尝试 %d 份 / 真跑通 %d 份（目标 N=%d）；请求键 %d 个；总耗时 %.1fs'
          % (len(results), runnable, n, keys_total, t_all))
    for r in results:
        if r['verdict'] == 'WRONG':
            print('  WRONG | %-46s | %s | %s'
                  % (r['path'], r.get('kind') or '', _gbk_safe(r.get('why'))))
    print('-' * 72)

    # ---- 断言（这一层才是"套件"）----
    check('样本有效性：拿到 %d 份**跑得通**的语料脚本（目标 %d）' % (runnable, n),
          runnable >= n,
          'n/a=%d（缺库/缺数据/要参数/开窗风险，不算 WRONG）' % na)
    check('三栏口径完备：ok + refused + WRONG == 跑得通的份数',
          ok + refused + wrong == runnable,
          '%d + %d + %d vs %d' % (ok, refused, wrong, runnable))
    check('**WRONG == 0**（rc=0 但读回不对 = 轴身份事故）', wrong == 0,
          '清单见上；明细见 %s' % os.path.join(out_dir, 'results.jsonl'))
    check('判据不是空转：至少有一份真的**写进去并对得上**（ok > 0）',
          ok > 0, 'ok=%d' % ok)

    # ---- 正对照（可选但强烈建议：证明判据在修复前会红）----
    if args.control:
        ctree = os.path.abspath(args.control)
        if not os.path.isdir(ctree):
            check('正对照树存在：%s' % ctree, False, '目录不存在')
        else:
            ctrl_wd = os.path.join(workroot, 'control')
            os.makedirs(ctrl_wd, exist_ok=True)
            old, why_old = _control_case(ctree, ctrl_wd, args.timeout)
            now, why_now = _control_case(tree, ctrl_wd, args.timeout)
            check('正对照①：**修复前**（%s）同一判据必须判 WRONG（判据有牙）'
                  % os.path.basename(ctree.rstrip('/\\')),
                  old is not None and old.get('verdict') == 'WRONG',
                  (old or {}).get('why') or why_old)
            check('正对照②：**当前树**同一夹具必须 ok 或 refused（修复有效）',
                  now is not None and now.get('verdict') in ('ok', 'refused'),
                  (now or {}).get('why') or why_now)
            print('  正对照明细：前=%s / 后=%s'
                  % (json.dumps(old, ensure_ascii=False)[:400],
                     json.dumps(now, ensure_ascii=False)[:400]))
            if not args.keep:
                shutil.rmtree(ctrl_wd, ignore_errors=True)

    meta = {'corpus': corpus, 'tree': tree, 'seed': args.seed, 'n': n,
            'attempt_cap': cap, 'source': source, 'pools': pools,
            'quota': quota, 'gates': gates, 'jobs': args.jobs,
            'timeout': args.timeout,
            'seconds': round(t_all, 1), 'write_seconds': round(tw_all, 1),
            'attempts': len(results), 'submitted': submitted,
            'runnable': runnable,
            'counts': {'ok': ok, 'refused': refused, 'WRONG': wrong, 'na': na},
            'silent_drop_keys': silent_drop, 'requested_keys': keys_total,
            'control_tree': args.control}
    jsonl, md = _write_outputs(out_dir, results, meta)
    if jsonl:
        print('产出：%s / %s' % (jsonl, md))
    if not args.keep:
        shutil.rmtree(workroot, ignore_errors=True)

    print('=' * 72)
    if fail:
        print('FAILED（%d 条）' % fail)
        return 1
    print('ALL PASS')
    return 0


def _dispatch(argv):
    try:
        return _main(argv[1:])
    except SystemExit:
        raise
    except BaseException:                                   # noqa: BLE001
        traceback.print_exc()
        print('FAILED')
        return 1


if __name__ == '__main__':
    sys.exit(_dispatch(sys.argv))
