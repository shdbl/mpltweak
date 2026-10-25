#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""变异闸门：**改坏一个判据，看测试是否真的变红**。

为什么要有这个东西
==================
"一个永远通过的检查，跟没有检查是一回事。" 这句不是口号，0.1.17 到 0.1.18 之间踩过
至少三次"看着有断言、其实没牙"的现场：

* 改坏 `label_fontsize` 注入旧 bug，形状矩阵 32 条全绿（走的是原位路径，压根没经过寻址）；
* `compare()` 号称"三档验证"，其实不比对 lines / legend / xscale；
* 轴身份错位时 `semantic check passed`。

所以这里把"反向验证"自动化：往**判据本身**注入变异（恒真 / 恒假 / 比较符翻转 /
常数替换），再看声明的那几条测试会不会变红。变红 = 这条判据有牙；一直绿 = 没牙。

一条重要纪律：**抓住它的必须是"声明里的测试"**
================================================
光是"有测试变红"不够 —— 变异了轴身份闸门，可能是 xlim 比对那条测试连带变红。那不是
"这判据有牙"，而是"碰巧有别的测试兜住了"。所以每条判据都显式声明：

    expect=['T8⑥ 值跑到隔壁轴', '轴身份错位']

只有声明的测试（按 check 名字的子串匹配）变红才算「抓住」；红在声明外记「**疑似**」——
照样打印出来（信息不许藏），但不计入"有牙"，也不算通过。

安全边界
========
* **仓库零改动**：所有变异只发生在 `%TEMP%` 下的副本里。收尾时拿"本工具写过的每一个
  绝对路径"当硬证据：只要有一条落在仓库里或临时目录外，直接判红（比"跑前跑后比树指纹"
  精确 —— 变异测试要跑十几分钟，队友随时可能在改同一棵树，指纹变红不能都算到本工具头上）。
  树指纹仍会打印，只作参考。
* **默认 dry-run**：不加 `--apply` 只打印计划（改哪个文件、哪一行、跑哪些套件、估时）。
* 变异体写完先 `ast.parse` 一遍：语法坏掉的变异体记「无效」，不当成"没牙"；
* 每个套件有超时；超时/崩溃记「错误」，不当成"抓住"。
* 副本里跑套件前先做一次**导入探针**：确认 `import mpltweak` 落到副本（本机常见
  `__editable__.mpltweak-*.pth` 把仓库 src 顶在最前面，不加 PYTHONPATH 就会拿到
  **没被变异的源码**，整张表的意义会反过来）。探针不过 ⇒ 记「错误」，不许记「没牙」。
* 建议在**树稳定**时跑：一趟要跑十几分钟，期间队友改源码会让个别行变成噪声（树指纹
  变了只能说明"树在变"，不能说明"变异体没生效"）。为此还有两道**前置闸门**：
  - **基线语法守卫**：开跑前只读解析 `src/**/*.py` + 本轮声明的套件；语法坏 ⇒ 直接
    rc=2 退出（"树没定稿，先别跑"），不让一分钟后的噪声行冒充结论；
  - **一次性快照**：整轮所有变异体都从同一份快照复制，跑动期间队友改树不会让不同行
    看到不同的源码；
  - **提前退出检测**：某个套件实测耗时 < 估时 × 60% ⇒ 记「错误」（= **没测成**），
    绝不当「没牙」，也不当「抓住」——"跑太快"通常意味着它根本没跑到该跑的地方
    （实测：正常一跑 590–605s，崩在半路是 5.6s / 7.4s，差两个数量级）。
    阈值可用 `--early-ratio` 调，`0` = 关闭。

用法
====
（Windows 控制台是 GBK 时先 `set PYTHONIOENCODING=utf-8`，否则中文表会乱码；
 输出的表格与失败名本身是 UTF-8。）
    python tools/mutate_gate.py                       # 默认 dry-run：只打表计划
    python tools/mutate_gate.py --apply               # 真跑（%TEMP% 副本里）
    python tools/mutate_gate.py --apply --jobs 3      # 并行（每个变异体一份副本）
    python tools/mutate_gate.py --apply --only cmap   # 只跑某条判据（子串匹配 id）
    python tools/mutate_gate.py --apply --kind always_false   # 只跑某一类变异体
    python tools/mutate_gate.py --apply --json out.json
    python tools/mutate_gate.py --apply --early-ratio 0        # 关掉"提前退出"判定
    python tools/mutate_gate.py --apply --allow-broken-tree    # 语法坏也强跑（调试）
    python tools/mutate_gate.py --self-test           # 自检"有牙"：真变异必须被抓住，
                                                      # 空变异必须变「没牙」且 rc=1

退出码：0 = 每个变异体都被**声明内的测试**抓住；1 = 有没牙 / 疑似（红了但红在声明外）/
无效变异体 / 提前退出 / 套件错误；2 = 用法问题或**基线语法坏掉**（树没定稿，先别跑）。
"""

import argparse
import ast
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor

# ------------------------------------------------ 控制台兜底（GBK 控制台不许炸）
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(errors='replace')
    except (AttributeError, ValueError, OSError):
        pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: 本工具**写过**的每一个绝对路径（收尾时用来证明"仓库零改动"）
_WRITTEN = []

#: --verbose：JSON 里连**全部**失败名一起记（校准 expect 用）
VERBOSE = False

#: 副本里要带过去的目录（`docs/` 不带：40 MB 的图，与本工具的套件无关）
COPY_DIRS = ('src', 'tests', 'tools')
COPY_FILES = ('pyproject.toml', 'README.md', 'CONTRIBUTING.md')
IGNORE = shutil.ignore_patterns('__pycache__', '*.pyc', '*.pyo', '.tmp_*', 'tmp_*',
                                'build', 'dist', '*.egg-info', '.pytest_cache',
                                '.hypothesis', '.mypy_cache', '*.png', '*.pdf')

#: 套件在**本机**的实测耗时（秒），只用于 dry-run 估时 + 「提前退出」的第二判据。
#: 2026-10-25 本机 longxia，`python tests/<suite>` 单跑 / 变异体（rc=0）取样：
#:   test_writeback        512–605 s（多次真跑）→ 记 530
#:   test_property_writeback 52–139 s（树改过之后明显变快，早期 139 已过时）→ 记 85
#:   test_fuzz_params       10.6–25.4 s → 记 17
#: 注意：估时**过时**会让「提前退出」误触发（实测踩过：property 估 139、实际 71 ⇒ 空变异
#: 被误判「错误」）。所以 v2 的提前退出判定改成"以收尾标记为主、耗时为辅"；估时仍应随树重记。
EST_SECS = {
    'test_writeback.py': 530.0,
    'test_property_writeback.py': 85.0,
    'test_fuzz_params.py': 17.0,
    'test_shapes.py': 56.9,
    'test_verify_onepass.py': 16.9,
    'test_tweak.py': 6.5,
    'test_apply_layout_conflict.py': 39.1,
}

#: 子进程环境：与 CI / 其它套件同一口径（Agg + 中文 + UTF-8 输出）
SUITE_ENV = {
    'MPLBACKEND': 'Agg',
    'MPLTWEAK_LANG': 'zh',
    'PYTHONIOENCODING': 'utf-8',
    'PYTHONUTF8': '1',
}

#: 提前退出阈值：套件实测耗时 < 估时 × 该比例 ⇒ 记「错误」（没测成），不记「没牙」
#: 依据（2026-10-25 两轮真跑）：正常一跑是 590–605s，而被改坏后"早崩"的一跑是 5.6s / 7.4s
#: —— 两者差两个数量级，0.6 这个阈值留的余量足够（队友改树导致的 ±10% 波动不会被误判）。
#: 若某天套件真的整体变快了（删了大量用例），把 EST_SECS 重新记一遍，或临时 `--early-ratio 0`。
EARLY_RATIO = 0.6

#: 一条 `FAIL | <名字> | <细节>` 行（多数脚本套件的统一输出格式）
_FAIL_RE = re.compile(r'^FAIL\s*\|\s*(.+?)\s*(?:\||$)')
#: 两栏式：`  FAIL  <名字>`（test_fuzz_params 这类）
_FAIL2_RE = re.compile(r'^\s{0,6}FAIL\s{2,}(.+)$')
#: 汇总行：`FAILED: 93 项 -> ['名字1', '名字2', ...]`
_SUMMARY_RE = re.compile(r'FAILED:\s*\d+\s*[^\n]*?->\s*(\[[^\n]*?\])\s*$', re.M)
#: pytest / hypothesis 风格：失败节点或函数名（如 `prop_invalid_rejected`）
_NODE_RE = re.compile(r'FAILED\s+(\S+)')
_DEFNAME_RE = re.compile(r'\b(?:def\s+)?((?:test|prop|TEETH)[A-Za-z0-9_]+)\b')
#: 脚本套件的**正常收尾标记**（三个在用的套件都有：`print('FAILED: %d' if fail else 'ALL PASS')`）。
#: 有它 = 这一跑跑到了末尾；没有它 + 又快 ⇒ 崩在半路（见 classify 的提前退出判定）。
#: 用"语义证据"而不是纯靠 EST_SECS 猜：估时是某天的采样，套件一变快就会误判。
_DONE_RE = re.compile(r'^(?:ALL PASS|FAILED:\s*\d+)', re.M)


# =============================================================== 判据声明
# 每条判据 = 一个"如果说谎就会让人放心地写坏交付物"的地方。
#   id      —— 稳定标识（--only 用）
#   title   —— 人读的名字
#   file    —— 判据所在的源文件（仓内相对路径）
#   expr    —— 【推荐】一个 `if <条件>:` 的**整行原文**：工具据此自动派生
#              恒真 / 恒假 / 比较符翻转 三种变异体
#   mutants —— 【可选】显式变异体（当判据不是 `if` 条件时用）：
#              {kind, note, find, replace}
#   consts  —— 【可选】常数替换用 [('原文', '替换')]（工具派生 const_replace 变异体）
#   suites  —— 允许跑的测试子集（按顺序，第一个抓住就停）
#   expect  —— **声明**：抓住它的 check 名字子串。只有命中这里的 FAIL 才算"抓住"。
JUDGES = [
    dict(
        id='verify.cell_identity',
        title='身份校验：网格身份（cell）回比 —— 轴序变了必须指名道姓',
        file='src/mpltweak/verify.py',
        expr="        if isinstance(_ecell, list) and 'cell' in a:",
        kinds=['always_false'],
        mutants=[
            dict(kind='flip_cmp',
                 note='比较符翻转：`_acell != _ecell` → `==` ⇒ 一致也报（误伤正常写回）',
                 find='            elif _acell != _ecell:\n',
                 replace='            elif _acell == _ecell:  # MUTANT: 翻转\n'),
        ],
        suites=['test_writeback.py'],
        # 三个方向各有一条**声明内**的检查：该报的要报（恒假）、不该报的不许报（翻转）。
        # 注 1：本判据的"恒真"方向（`if True:`）实测把整套打崩在 5.6s（红在 9 条与 cell
        #       无关的名字上）—— 那是"改坏了整套"，不是"这条判据有牙"，故不列。
        # 注 2：曾试过一个 const_replace（`'cell' in a` → `'clim' in a`，读错键），但它把
        #       注释插在行内 `:` 前面 ⇒ 语法坏掉 ⇒ 工具判「无效变异体」而不是「没牙」
        #       （这正是 ast.parse 兜底存在的意义）。要加常数替换，锚点必须整行、注释
        #       只能挂在行尾且不能在 `:` 之前。
        expect=['cell 身份不一致', '网格身份对不上', 'cell 不同 → 判失败',
                'cell 一致 → 不报'],
    ),
    dict(
        id='verify.axis_identity_confusion',
        title='轴身份闸门：值跑到隔壁轴（错位）必须点名"轴身份错位"',
        file='src/mpltweak/verify.py',
        mutants=[dict(
            kind='always_false',
            note='恒假：错位交叉核对整块不执行',
            find="    for _k, _e in _exps:\n",
            replace="    for _k, _e in ():  # MUTANT: 恒假\n",
        )],
        suites=['test_writeback.py'],
        expect=['T8⑥ 值跑到隔壁轴'],
    ),
    dict(
        id='verify.cmap',
        title='cmap 比对：mappable 身份被弄串时必须报出来（T8⑤）',
        file='src/mpltweak/verify.py',
        expr="            if ec_nm and ac_nm and str(ec_nm) != str(ac_nm):",
        kinds=['always_false', 'flip_cmp'],
        suites=['test_writeback.py'],
        # 这条判据的两个方向里，**只有"漏报"方向有牙**（恒假 ⇒ T8⑤ 立刻红）。
        # "误报"方向（`!=` → `==`，把相等的 cmap 也报成不等）实测**没牙**，原因已查实
        # 并写进 _team014/mutation.md：T8⑤ 的两个轴是 (viridis vs magma) 与
        # (plasma vs plasma)，而断言只写 `any('cmap' in b for b in bad)` —— 翻转后
        # 第 1 轴（plasma==plasma）照样产出一条含 'cmap' 的消息，断言就满足了。
        # 这不是"工具漏报"，是用例的断言太软：要让它长出这颗牙，得补一句"相等的轴
        # 不许出现在 bad 里"（`not any('ax1' in b ...)`）。工具不替用例造牙。
        expect=['T8⑤ cmap'],
    ),
    dict(
        id='verify.xlim',
        title='xlim 比对：轴范围容差判据（反过来永远报错 = 干净往返会被误判失败）',
        file='src/mpltweak/verify.py',
        mutants=[dict(
            kind='always_true',
            note='容差换成负数 = 判据永远报错（恒真方向的变异体）',
            find="            _span = abs(float(ex[1]) - float(ex[0])) or 1.0",
            replace="            _span = -1.0  # MUTANT: 容差变负，永远判失败",
        )],
        suites=['test_writeback.py'],
        # 第一轮实测（2026-10-25）校准过这条声明：真正抓住它的不是 set_xlim 那组
        # 原位改写检查（那组走 writeback(verify=False)，根本不过比对层），而是
        # **干净往返**那组 —— 判据反过来永远报错时，"参数与源码一致 => no_change"
        # 会变成"拒绝写回"。声明必须指向真的在断言这条判据的测试。
        #
        # 已知缺口（写进 _team014/mutation.md，本工具只能如实报"没牙"不能自己造牙）：
        # **"漏报"方向**（容差被放大到吃掉真实差异）目前没有任何用例钉住 ——
        # 全套 tests/ 里找不到一条"xlim 不一致必须报出来"。等那条用例补上，在本判据
        # 的 mutants 里加一个 const_replace（`or 1.0` → `or 1e12`）即可长出第二颗牙。
        expect=['44 E5：干净往返', '44 E5/幂等', 'T7④ 原样回写',
                'T8① 恒等 apply', '61d describe→原样 apply 干净往返'],
    ),
    dict(
        id='writeback.noop_filter',
        title='差异过滤：值没变的字段不许算成"改了 N 处"（no_change 判据）',
        file='src/mpltweak/writeback.py',
        expr='        if _n and _noop.get(_key, 0) == _n:',
        kinds=['always_false', 'flip_cmp'],
        suites=['test_property_writeback.py', 'test_writeback.py'],
        # 试过 `_noop.get(_key, 0)` → `_noop.get(_key, 1)` 这个常数替换（把"空转计数"
        # 的默认值从 0 改成 1），实测红在 `41 E2b 显式 grid=false 仍然生效` 与
        # `49a T11：changes 只报 y 那一个字` 上 —— 那不是"差异过滤"这条判据的声明范围
        # （它动的是计数阈值语义），把它塞进 expect 等于给声明注水，故剔除。
        expect=['四条铁律全过', 'T57-稀疏轴回归', '幂等', 'reason=no_change'],
    ),
    dict(
        id='params.refuse_gate',
        title='拒写闸门：硬错误参数必须拒写（rc=2 / bad_params）',
        file='src/mpltweak/params.py',
        mutants=[dict(
            kind='always_false',
            note='恒假：硬错误清单永远为空（坏参数一路放行）',
            find='    errs: List[str] = []\n',
            replace='    errs: List[str] = []\n    return []  # MUTANT: 恒假\n',
        )],
        suites=['test_fuzz_params.py', 'test_property_writeback.py'],
        expect=['apply 拒写 rc=2', '报 bad_params', '被硬错误拦下', '拒写'],
    ),
]

#: 变异体种类（工具必须支持的四种，外加一个自检用的空变异）
KINDS = ('always_true', 'always_false', 'flip_cmp', 'const_replace', 'noop')
KIND_LABEL = {
    'always_true': '恒真',
    'always_false': '恒假',
    'flip_cmp': '比较符翻转',
    'const_replace': '常数替换',
    'noop': '空变异（自检用：只加注释，语义不变）',
}


# =============================================================== 变异体派生
_CMP_SWAP = [('!=', '=='), ('==', '!='), ('>=', '<'), ('<=', '>'),
             ('>', '<='), ('<', '>=')]


def _indent_of(line):
    return line[:len(line) - len(line.lstrip())]


def _flip_first_cmp(expr):
    """把条件里的**第一个**比较符翻转（保守：只动一个，别把判据改成另一种判据）。"""
    for op, new in _CMP_SWAP:
        idx = expr.find(op)
        if idx >= 0:
            # `>=` / `<=` 要先于 `>` / `<` 匹配，避免把 `>=` 拆成 `>`+`=`
            if op in ('>', '<') and expr[idx + 1:idx + 2] == '=':
                continue
            return expr[:idx] + new + expr[idx + len(op):]
    return None


def derive_mutants(judge, with_noop=False):
    """判据声明 -> 变异体列表（受 judge['kinds'] 过滤；末尾永远补一个空变异体）。"""
    out = []
    derived = []
    expr = judge.get('expr')
    if expr:
        ind = _indent_of(expr)
        head = expr.split(':', 1)[0].strip()          # `if <条件>`
        cond = head[3:].strip() if head.startswith('if ') else head
        derived.append(dict(kind='always_true', note='恒真：判据永不判失败',
                            find=expr, replace='%sif True:  # MUTANT: 恒真' % ind))
        derived.append(dict(kind='always_false', note='恒假：判据永不触发',
                            find=expr, replace='%sif False:  # MUTANT: 恒假' % ind))
        flipped = _flip_first_cmp(cond)
        if flipped:
            derived.append(dict(kind='flip_cmp', note='比较符翻转：%s -> %s'
                                % (cond.strip(), flipped.strip()),
                                find=expr,
                                replace='%sif %s:  # MUTANT: 翻转' % (ind, flipped)))
    # kinds 只筛"从 expr 自动派生"的那几种（恒真/恒假/翻转）；显式 mutants 与 consts
    # 是作者手写、kind 自带的，不受 kinds 影响（否则 kinds=['always_false'] 会把作者
    # 特意加的"翻转 / 常数"一起筛掉 —— 踩过这个坑）。
    wanted = judge.get('kinds')
    if wanted:
        derived = [m for m in derived if m['kind'] in wanted]
    out = derived
    for m in judge.get('mutants') or []:
        out.append(dict(m))
    for find, repl in judge.get('consts') or []:
        out.append(dict(kind='const_replace', note='常数替换：%s -> %s' % (find, repl),
                        find=find, replace=repl))
    # 空变异体（自检用）：语义不变 ⇒ 必须报「没牙」。挂在第一个真变异体的锚点上。
    # 只在 --self-test / --kind noop 时生成：每次真跑都带一个 6 分钟的对照组太贵。
    if not with_noop:
        return out
    if out:
        first = out[0]
        find = first['find']
        if '\n' in find:                       # 整行锚点：把注释挂在行尾
            repl = find.replace('\n', '  # MUTANT: noop\n', 1)
        else:                                  # 行内锚点：原样 + 注释
            repl = find + '  # MUTANT: noop'
        out.append(dict(kind='noop', note='空变异：只加注释，语义不变',
                        find=find, replace=repl))
    return out


# =============================================================== 副本与指纹
def _tree_fp(root):
    """仓库指纹：src/ tools/ tests/ 下每个 .py 的 (路径, 大小, sha256)。"""
    h = hashlib.sha256()
    for dirname in ('src', 'tools', 'tests'):
        base = os.path.join(root, dirname)
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = sorted(d for d in dirnames
                                 if d not in ('__pycache__',) and not d.startswith('.tmp'))
            for f in sorted(filenames):
                if not f.endswith('.py'):
                    continue
                p = os.path.join(dirpath, f)
                rel = os.path.relpath(p, root).replace('\\', '/')
                h.update(rel.encode('utf-8'))
                h.update(str(os.path.getsize(p)).encode('ascii'))
                with open(p, 'rb') as fh:
                    while True:
                        chunk = fh.read(1 << 16)
                        if not chunk:
                            break
                        h.update(chunk)
    return h.hexdigest()


def make_copy(dst):
    """把仓里必要的部分抄到 dst（仓外副本）。"""
    for d in COPY_DIRS:
        s = os.path.join(ROOT, d)
        if os.path.isdir(s):
            shutil.copytree(s, os.path.join(dst, d), ignore=IGNORE, dirs_exist_ok=True)
    for f in COPY_FILES:
        s = os.path.join(ROOT, f)
        if os.path.isfile(s):
            shutil.copy2(s, os.path.join(dst, f))
    return dst


def apply_mutant(copy_root, judge, mutant):
    """把变异写进副本里的那一份文件 -> (ok, msg)。

    每一次写入都登记到 `_WRITTEN`，收尾时用它证明"本工具从来没写过仓库"
    （比"跑前跑后比指纹"精确：取证期间别的 agent 可能在改同一棵树，指纹会误报）。
    """
    path = os.path.join(copy_root, judge['file'])
    try:
        with io.open(path, encoding='utf-8') as f:
            text = f.read()
    except OSError as e:
        return False, '读不到 %s（%s）' % (judge['file'], e)
    # 基线守卫：**未变异**的原文自己都 parse 不过 => 仓库在制品（队友正在改这一行），
    # 这时候所有变异体都会"语法坏掉"，那是假象，不许记成"变异体无效"。
    try:
        ast.parse(text, path)
    except SyntaxError as e:
        return False, ('基线文件语法坏掉（仓库在制品，不是变异体的问题）：%s' % e)
    find, repl = mutant['find'], mutant['replace']
    n = text.count(find)
    if n != 1:
        return False, '锚点出现 %d 次（要求恰好 1 次）：%r' % (n, find.strip()[:60])
    new = text.replace(find, repl, 1)
    if new == text:
        return False, '替换后文件没变化（空变异不算变异体）'
    try:
        ast.parse(new, path)
    except SyntaxError as e:
        return False, '变异体语法坏掉：%s' % e
    with io.open(path, 'w', encoding='utf-8', newline='') as f:
        f.write(new)
    _WRITTEN.append(os.path.abspath(path))
    return True, ''


def _py_exe():
    return sys.executable


def copy_env(copy_root):
    """子进程环境：**必须让 `import mpltweak` 落到副本里**，否则整个工具是个笑话。

    本机（以及 CI 之外的任何开发机）通常装有 editable 安装：
    `site-packages/__editable__.mpltweak-*.pth` 里写死了**仓库**的 `src` 路径。
    那种情况下副本里跑套件，`import mpltweak` 拿到的是**仓库里那份没被变异的源码**
    —— 变异体一个字节都没生效，表上全是"没牙"，而结论完全反了（比没有工具更糟）。

    `PYTHONPATH` 排在 `.pth` 追加项之前，所以把 `<copy>/src` 顶上去就能盖住它；
    同时把宿主环境里指向**仓库 src** 的那条 PYTHONPATH 剔掉（照抄会覆盖自己）。
    """
    env = dict(os.environ)
    env.update(SUITE_ENV)
    env.pop('MPLTWEAK_PARAMS', None)
    repo_src = os.path.abspath(os.path.join(ROOT, 'src'))
    prev = [p for p in (env.get('PYTHONPATH') or '').split(os.pathsep)
            if p and os.path.abspath(p) != repo_src]
    env['PYTHONPATH'] = os.pathsep.join(
        [os.path.join(copy_root, 'src'), copy_root] + prev)
    return env


def probe_import(copy_root, timeout=120):
    """凭证：副本里 `import mpltweak` 到底导入的是谁？-> (ok, msg)。

    这是"变异真的生效了吗"的前置探针。导入的不是副本 ⇒ 后面跑出来的所有绿/红都
    与本变异无关，必须报错（不许记成"没牙"，那会把环境问题说成判据问题）。
    """
    code = 'import mpltweak, sys; sys.stdout.write(mpltweak.__file__ or "")'
    try:
        r = subprocess.run([_py_exe(), '-c', code], cwd=copy_root,
                           env=copy_env(copy_root), capture_output=True,
                           timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, '导入探针超时（>%ss）' % timeout
    got = (r.stdout or b'').decode('utf-8', 'replace').strip()
    if r.returncode != 0:
        tail = (r.stderr or b'').decode('utf-8', 'replace').strip().splitlines()[-2:]
        return False, '副本里 import mpltweak 失败：%s' % ' / '.join(tail)
    if not os.path.abspath(got).startswith(os.path.abspath(copy_root) + os.sep):
        return False, ('副本里的包没被导入：import mpltweak -> %s（应为 %s 下的副本；'
                       '变异不会生效，整张表无效）'
                       % (got, os.path.join(copy_root, 'src')))
    return True, got


def preflight_syntax(suites=(), package_dir='src'):
    """基线语法守卫（只读）：仓库在制品语法坏掉时，跑变异没有意义 —— 直接 rc=2 退出。

    为什么必须前置：树在变的时候（队友正在改 `verify.py`），一个语法半成品会让**每一个**
    变异体都"语法坏掉"，于是跑出一堆「无效变异体 / 错误」行，看上去像工具的毛病。
    与其跑十分钟噪声，不如 1 秒内说清"树没定稿，先别跑"。

    只解析真正会用到的文件：`<repo>/src/**/*.py` + 本轮声明的套件（不碰 `tests/` 里
    与本闸门无关的文件 —— 队友在改别的测试不该拦住我们）。

    返回 [(文件, 错误)]；空列表 = 语法都过。
    """
    bad = []
    targets = []
    base = os.path.join(ROOT, package_dir)
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = sorted(d for d in dirnames if d != '__pycache__')
        for f in sorted(filenames):
            if f.endswith('.py'):
                targets.append(os.path.join(dirpath, f))
    for suite in suites:
        p = os.path.join(ROOT, 'tests', suite)
        if os.path.isfile(p):
            targets.append(p)
    for path in targets:
        rel = os.path.relpath(path, ROOT).replace('\\', '/')
        try:
            with io.open(path, encoding='utf-8') as fh:
                ast.parse(fh.read(), rel)
        except SyntaxError as e:
            bad.append(('%s:%s: %s' % (rel, e.lineno, e.msg), ''))
        except OSError as e:                       # 被占用/删掉：也记下（不假装通过）
            bad.append((rel, str(e)))
    return [b[0] if not b[1] else '%s（%s）' % b for b in bad]


def run_suite(copy_root, suite, timeout):
    """在副本里跑一个套件 -> dict(rc, secs, fails, extra, tail)。

    ``fails``  = `FAIL | <名字>` 这种统一格式里的名字（脚本套件）；
    ``extra``  = pytest / hypothesis 套件失败时留下的节点名或函数名（traceback 里的
                 `FAILED ...::x` 或 `prop_xxx`），只用于"这条套件红了、红在哪"。
    """
    path = os.path.join(copy_root, 'tests', suite)
    if not os.path.isfile(path):
        return dict(rc=None, secs=0.0, fails=[], extra=[], tail='套件文件不存在',
                    error='missing')
    env = copy_env(copy_root)
    t0 = time.time()
    try:
        r = subprocess.run([_py_exe(), path], cwd=copy_root, env=env,
                           capture_output=True, timeout=timeout)
        secs = time.time() - t0
        out = (r.stdout or b'').decode('utf-8', 'replace') + \
              (r.stderr or b'').decode('utf-8', 'replace')
        fails, extra = [], []
        for line in out.splitlines():
            s = line.strip()
            if not s:
                continue
            m = _FAIL_RE.match(s)
            if m:
                fails.append(m.group(1).strip())
                continue
            if r.returncode != 0:
                m = _FAIL2_RE.match(line)
                if m and not m.group(1).lstrip().startswith('|'):
                    fails.append(m.group(1).strip())
                    continue
                for m2 in _NODE_RE.findall(s):
                    extra.append(m2.split('::')[-1])
                for m3 in _DEFNAME_RE.findall(s):
                    extra.append(m3)
        # 汇总行：`FAILED: N 项 -> ['名字1', '名字2', ...]`（一次给出全部失败名）
        for blob in _SUMMARY_RE.findall(out):
            try:
                names = ast.literal_eval(blob)
            except (ValueError, SyntaxError):
                continue
            if isinstance(names, (list, tuple)):
                fails.extend(str(x) for x in names)
        return dict(rc=r.returncode, secs=secs, fails=fails,
                    extra=list(dict.fromkeys(extra)),
                    done=bool(_DONE_RE.search(out)),
                    tail='\n'.join(out.splitlines()[-3:]), error='')
    except subprocess.TimeoutExpired:
        return dict(rc=None, secs=time.time() - t0, fails=[], extra=[],
                    done=False, tail='超时（>%ss）' % timeout, error='timeout')


def classify(res, expect, suite, expect_secs=0.0, early_ratio=EARLY_RATIO):
    """把一个变异体的运行结果分类 -> (verdict, caught_by, hit, suspected)。"""
    if res.get('error') == 'missing':
        return 'error', '', [], '套件文件不存在：%s' % suite
    if res.get('error') == 'timeout':
        return 'error', '', [], '超时'
    if res['rc'] is None:
        return 'error', '', [], '跑不起来'
    fails = res['fails']
    names = fails + (res.get('extra') or [])
    hit = [n for n in names if any(e in n for e in expect)]
    if hit:
        return 'caught', '%s :: %s' % (suite, hit[0]), hit, ''
    # 提前退出：**没打出正常收尾标记**（`ALL PASS` / `FAILED: N`）**且**耗时 < 估时的 60%
    # ⇒ 这次跑的"深度"不可信（断言根本没跑到那么远）。
    # 两个条件缺一不可：只看耗时会被"套件真的变快了 / 估时过时"误伤（实测踩过：
    # test_property_writeback 估 139s、实际 52–85s ⇒ 空变异被误判「错误」）；
    # 只看标记则会把"跑完但很慢"也误判。以"标记"为主、"耗时"为辅。
    early = (bool(expect_secs) and not res.get('done')
             and res['secs'] < early_ratio * expect_secs)
    if early and not fails:
        return 'error', '', [], ('套件提前退出（跑了 %.1fs / 预期约 %.0fs，短于 %.0f%%，'
                                 '且没打出正常收尾标记）—— 这次不能记「没牙」，也不许当「抓住」'
                                 % (res['secs'], expect_secs, early_ratio * 100))
    if early and fails:
        return 'error', '', [], ('套件提前退出（跑了 %.1fs / 预期约 %.0fs，短于 %.0f%%，'
                                 '且没打出正常收尾标记）—— 崩在中途，后面的检查没跑到，结论不可用'
                                 % (res['secs'], expect_secs, early_ratio * 100))
    # 红了，但红在声明外（或者干脆没有可解析的名字 = 崩了）—— 记「疑似」，不算抓住
    if fails or (res.get('extra') or []):
        first = (fails or res.get('extra') or [''])[0]
        return 'suspected', '%s :: %s' % (suite, first), [], ', '.join(names[:3])
    if res['rc'] == 0 and not fails:
        return 'toothless', '', [], ''
    return 'error', '', [], '套件 rc=%s 但输出里没有 FAIL 行（崩了？）：%s' % (
        res['rc'], (res.get('tail') or '')[-160:])


# =============================================================== 跑一条变异体
def run_mutant(judge, mutant, timeout, keep_copy=False, snapshot_root=None):
    """一条变异体 = 一份副本 + 依次跑声明的套件（第一个抓住就停）。

    ``snapshot_root``：整轮共用的**一次性快照**（见 main()）。有它就从快照复制，
    没有就直接从仓库抄 —— 前者让"跑中队友改了树"不影响本轮结论。
    """
    copy_root = tempfile.mkdtemp(prefix='mpltweak_mut_')
    row = dict(judge=judge['id'], kind=mutant['kind'], note=mutant.get('note', ''),
               verdict='', caught_by='', suspected='', secs=0.0, msg='',
               anchor=mutant['find'].strip()[:70],
               suites=[])
    try:
        if snapshot_root:
            shutil.copytree(snapshot_root, copy_root, dirs_exist_ok=True, ignore=IGNORE)
        else:
            make_copy(copy_root)
        ok, msg = apply_mutant(copy_root, judge, mutant)
        if not ok:
            row['verdict'] = 'invalid'
            row['msg'] = msg
            return row
        # 前置探针：变异生效的前提是副本里的包**真的**被导入（editable 安装会抢路径）
        ok, msg = probe_import(copy_root)
        row['imported'] = msg if ok else ''
        if not ok:
            row['verdict'] = 'error'
            row['msg'] = msg
            return row
        for suite in judge['suites']:
            res = run_suite(copy_root, suite, timeout)
            row['secs'] += res['secs']
            est = EST_SECS.get(suite, 0.0)
            verdict, caught_by, _hit, sus = classify(
                res, judge['expect'], suite, est, EARLY_RATIO)
            early = bool(est) and not res.get('done') and res['secs'] < EARLY_RATIO * est
            row['suites'].append(dict(suite=suite, rc=res['rc'], secs=res['secs'],
                                      n_fails=len(res['fails']), early=early,
                                      fails=res['fails'][:5]))
            # 只有「抓住」不受提前退出影响：声明里的 check 真的红了就是红了（红得早不等于
            # 假红）。其余情况跑太快 ⇒ 记为提前退出噪声，由 print_report 单列出来。
            if early and verdict != 'caught':
                row['early'] = True
            if VERBOSE:
                # 校准用：把**全部**失败名记下来（用来把 expect 声明收紧到语义对应的那几条，
                # 也用来在报告里列"红在哪"）。只进 JSON，不打印。
                row['suites'][-1]['fails_all'] = res['fails']
            if verdict == 'caught':
                row.update(verdict='caught', caught_by=caught_by)
                return row
            if verdict == 'error':
                row.update(verdict='error', msg=sus)
                return row
            if verdict == 'suspected' and not row['verdict']:
                row.update(verdict='suspected', caught_by=caught_by, suspected=sus)
        if not row['verdict']:
            row['verdict'] = 'toothless'
        return row
    finally:
        if not keep_copy:
            shutil.rmtree(copy_root, ignore_errors=True)


# =============================================================== 呈现
VERDICT_LABEL = {
    'caught': '抓住',
    'suspected': '疑似（声明外）',
    'toothless': '**没牙**',
    'invalid': '无效变异体',
    'error': '错误',
}


def print_plan(judges, jobs, want=None):
    total = 0.0
    n = 0
    print('== 变异闸门 dry-run（没有写任何文件；加 --apply 才真跑）')
    print()
    print('仓库：%s' % ROOT)
    print('副本：%TEMP%\\mpltweak_mut_*（每个变异体一份，跑完即删；仓库零改动）')
    print('并行：%d' % jobs)
    if want:
        print('变异种类过滤：%s' % ', '.join(sorted(want)))
    print()
    for j in judges:
        ms = derive_mutants(j, with_noop=bool(want and 'noop' in want))
        if want:
            ms = [m for m in ms if m['kind'] in want]
        if not ms:
            continue
        cost = sum(EST_SECS.get(s, 60.0) for s in j['suites'])
        total += cost * len(ms)
        n += len(ms)
        print('-- %s  %s' % (j['id'], j['title']))
        print('   文件：%s' % j['file'])
        print('   声明抓住它的测试：%s' % ' / '.join(repr(e) for e in j['expect']))
        print('   测试子集：%s（估 %.1fs/变异体）'
              % (', '.join(j['suites']), cost))
        for m in ms:
            print('     * %-14s %s' % (KIND_LABEL.get(m['kind'], m['kind']),
                                       m.get('note', '')))
            print('       锚点：%s' % m['find'].strip()[:90])
            print('       改为：%s' % m['replace'].strip()[:90])
        print()
    print('合计：%d 条判据 / %d 个变异体 / 串行估时约 %.1f 分钟（并行 %d 约 %.1f 分钟）'
          % (len(judges), n, total / 60.0, jobs, total / max(1, jobs) / 60.0))
    print('判据：只有**声明内**的测试变红才算抓住；红在声明外记「疑似」，不算通过。')
    return 0


def print_report(rows, judges, elapsed, jobs):
    print()
    print('== 结果：判据 | 变异体 | 是否被抓住 | 抓住它的测试')
    print()
    print('| 判据 | 变异体 | 是否被抓住 | 抓住它的测试（声明内） | 疑似声明外 | 秒 |')
    print('|---|---|---|---|---|---:|')
    for r in rows:
        sus = ('是：%s' % r['suspected'][:70]) if r['suspected'] else '-'
        print('| `%s` | %s | %s | %s | %s | %.1f |'
              % (r['judge'], KIND_LABEL.get(r['kind'], r['kind']),
                 VERDICT_LABEL.get(r['verdict'], r['verdict']),
                 r['caught_by'] or r.get('msg', '')[:70] or '-', sus, r['secs']))
    print()
    # 每条判据的"有牙"结论：至少一个变异体被声明内测试抓住
    per = {}
    for r in rows:
        per.setdefault(r['judge'], []).append(r)
    toothless = []
    for j in judges:
        rs = per.get(j['id']) or []
        teeth = [r for r in rs if r['verdict'] == 'caught']
        if teeth:
            print('[有牙] %s —— 抓住它的测试：%s'
                  % (j['id'], ' / '.join(sorted({r['caught_by'] for r in teeth}))))
        else:
            toothless.append(j['id'])
            print('[没牙] %s —— %d 个变异体没有一个被声明内的测试抓住'
                  % (j['id'], len(rs)))
            for r in rs:
                print('        %-14s %s%s' % (KIND_LABEL.get(r['kind'], r['kind']),
                                              VERDICT_LABEL.get(r['verdict'], ''),
                                              (' ；' + r.get('msg', '')) if r.get('msg') else ''))
    print()
    early_rows = [r for r in rows if r.get('early')]
    if early_rows:
        print('!! 提前退出 %d 行（耗时 < 估时×%.0f%%）—— 这些行是"没测成"，不是"没牙"：'
              % (len(early_rows), EARLY_RATIO * 100))
        for r in early_rows:
            print('   %-8s %-30s %s' % (VERDICT_LABEL.get(r['verdict'], r['verdict']),
                                        r['judge'], (r.get('msg') or '')[:110]))
        print('   先确认：①树在这一行跑的瞬间是否被改（看指纹复核）②EST_SECS 是否过时'
              '（套件真变快了就重记估时，或临时 --early-ratio 0）；然后再重跑该行。')
        print()
    bad = [r for r in rows if r['verdict'] in ('invalid', 'error', 'suspected')]
    print('判据 %d 条 / 变异体 %d 个 / 有牙 %d / 没牙 %d / 疑似或无效 %d / 耗时 %.1f 秒（并行 %d）'
          % (len(judges), len(rows), len(judges) - len(toothless), len(toothless),
             len(bad), elapsed, jobs))
    print()
    if toothless:
        print('=> FAILED：%d 条判据没牙（改坏了测试也不红 = 这条判据等于没有）'
              % len(toothless))
        return 1
    if bad:
        print('=> FAILED：有 %d 个变异体是"无效/错误/疑似声明外"，不算通过（见上表）'
              % len(bad))
        return 1
    print('=> PASS：%d 条判据全部被**声明内**的测试抓住' % len(judges))
    return 0


# =============================================================== 自检
def self_test(timeout, verbose=True):
    """自检有牙：真变异必须被抓住；同一位置的**空变异**必须变成「没牙」且 rc=1。

    这是防"工具自己永远说 PASS"的那颗牙：如果一个变异体语义没变、测试当然不红，
    工具就必须老老实实报「没牙」。两条都成立，才说明这张表有区分力。
    """
    judge = None
    for j in JUDGES:
        cost = sum(EST_SECS.get(s, 60.0) for s in j['suites'])
        if judge is None or cost < sum(EST_SECS.get(s, 60.0) for s in judge['suites']):
            judge = j
    if judge is None:
        print('== 自检：没有判据声明，跳过')
        return 2
    ms = derive_mutants(judge, with_noop=True)
    real = [m for m in ms if m['kind'] != 'noop']
    noop = [m for m in ms if m['kind'] == 'noop']
    if not real or not noop:
        print('== 自检：判据 %s 没有可用的真/空变异体对' % judge['id'])
        return 2
    print('== 自检（工具自己有没有牙）：judge=%s，套件=%s'
          % (judge['id'], ', '.join(judge['suites'])))
    # 自检要证明的是"语义没变 ⇒ 判没牙 / 语义变了 ⇒ 判抓住"这两条**判定本身**。
    # 提前退出判定（v2 新增）依赖 EST_SECS，会在这台机器某次跑得偏快时把空变异改判
    # 「错误」，于是自检在没有任何人改代码的情况下变红（实测：52.8s vs 估 139s）。
    # 所以自检期间临时关掉它 —— 时序噪声不许冒充"工具的判定不可信"。
    global EARLY_RATIO
    saved_ratio = EARLY_RATIO
    EARLY_RATIO = 0.0
    try:
        r_real = run_mutant(judge, real[0], timeout)
        print('   真变异 %-14s -> %s %s（%.1fs）'
              % (KIND_LABEL[real[0]['kind']], VERDICT_LABEL.get(r_real['verdict']),
                 r_real['caught_by'], r_real['secs']))
        r_noop = run_mutant(judge, noop[0], timeout)
    finally:
        EARLY_RATIO = saved_ratio
    print('   空变异 %-14s -> %s %s（%.1fs）'
          % (KIND_LABEL[noop[0]['kind']], VERDICT_LABEL.get(r_noop['verdict']),
             r_noop['caught_by'], r_noop['secs']))
    rc_pretend = print_report([r_noop], [judge], r_noop['secs'], 1)
    ok_caught = r_real['verdict'] == 'caught'
    ok_toothless = r_noop['verdict'] in ('toothless', 'suspected') and rc_pretend == 1
    print()
    print('   判据：真变异被声明内测试抓住 = %s；同一个判据换成空变异（语义不变）'
          '⇒ 表里变「没牙」且 rc=%d = %s'
          % (ok_caught, rc_pretend, ok_toothless))
    if ok_caught and ok_toothless:
        print('=> SELF-TEST PASS（这张表有区分力：能抓住真变异，也能认出"永不报错"）')
        return 0
    print('=> SELF-TEST FAILED（工具自己的判据不可信）')
    return 1


# =============================================================== main
def main(argv=None):
    global VERBOSE, EARLY_RATIO
    ap = argparse.ArgumentParser(
        prog='mutate_gate.py',
        description='变异闸门：往判据里注入变异，看声明的测试是否变红（默认 dry-run）。')
    ap.add_argument('--apply', action='store_true',
                    help='真跑（默认 dry-run：只打印计划）')
    ap.add_argument('--only', default='',
                    help='只跑 id 含该子串的判据（可重复用逗号分隔）')
    ap.add_argument('--kind', default='',
                    help='只跑某几种变异（逗号分隔：always_true,always_false,'
                         'flip_cmp,const_replace,noop）')
    ap.add_argument('--jobs', type=int, default=1, help='并行度（每个变异体一份副本）')
    ap.add_argument('--timeout', type=float, default=1200.0,
                    help='单个套件的超时秒数（默认 1200）')
    ap.add_argument('--early-ratio', type=float, default=EARLY_RATIO,
                    help='提前退出阈值：实测耗时 < 估时×该比例 ⇒ 记「错误」（默认 %.1f；'
                         '0 = 关闭该判定）' % EARLY_RATIO)
    ap.add_argument('--allow-broken-tree', action='store_true',
                    help='基线语法坏掉也照样跑（默认 rc=2 直接退出；调试用）')
    ap.add_argument('--json', default='', help='把结果写成 JSON')
    ap.add_argument('--verbose', action='store_true',
                    help='JSON 里连全部失败名一起记（校准 expect 声明用）')
    ap.add_argument('--keep-copy', action='store_true',
                    help='保留最后一份副本（调试用）')
    ap.add_argument('--self-test', action='store_true',
                    help='自检：真变异必须被抓住、空变异必须变没牙')
    args = ap.parse_args(argv)

    VERBOSE = bool(args.verbose)
    EARLY_RATIO = float(args.early_ratio)


    judges = list(JUDGES)
    if args.only:
        keys = [k.strip() for k in args.only.split(',') if k.strip()]
        judges = [j for j in judges if any(k in j['id'] for k in keys)]
        if not judges:
            print('没有匹配 --only 的判据：%s' % args.only)
            return 2
    if args.kind:
        want = {k.strip() for k in args.kind.split(',') if k.strip()}
        unknown = want - set(KINDS)
        if unknown:
            print('未知变异种类：%s（可选：%s）' % (', '.join(sorted(unknown)),
                                                  ', '.join(KINDS)))
            return 2
    else:
        want = None

    # ---- 基线语法守卫（只读）：树没定稿就别跑（否则每一行都是噪声）
    suites = sorted({s for j in judges for s in j['suites']})
    broken = preflight_syntax(suites)
    if broken:
        print('!! 基线语法坏掉 —— 仓库在制品（多半是队友正在改这些文件），共 %d 处：'
              % len(broken))
        for b in broken[:10]:
            print('   %s' % b)
        if broken and len(broken) > 10:
            print('   …（还有 %d 处）' % (len(broken) - 10))
        if args.apply and not args.allow_broken_tree:
            print('   这时跑出来的每个变异体都会"语法坏掉"，结论没有意义。'
                  '请等树稳定后再跑（本轮不做任何变异；确要强跑加 --allow-broken-tree）。')
            return 2
        print('   （dry-run 继续；真跑会被这一关拦下）')

    if args.self_test:
        return self_test(args.timeout)

    if not args.apply:
        return print_plan(judges, max(1, args.jobs), want)

    # ---- 真跑：先记树指纹（只做参考），跑完再核（自己守卫自己）
    _WRITTEN.clear()
    fp_before = _tree_fp(ROOT)
    t0 = time.time()
    work = []
    for j in judges:
        for m in derive_mutants(j, with_noop=(want is not None and 'noop' in want)):
            if want and m['kind'] not in want:
                continue
            work.append((j, m))
    print('== 真跑：%d 条判据 / %d 个变异体 / 并行 %d / 超时 %.0fs/套件'
          % (len(judges), len(work), max(1, args.jobs), args.timeout))
    # ---- 一次性快照：整轮所有变异体都从这一份复制（跑中队友改树不影响本轮结论）
    snapshot_root = tempfile.mkdtemp(prefix='mpltweak_mut_snap_')
    make_copy(snapshot_root)
    bad_base = []
    for _f in sorted({j['file'] for j, _m in work}):
        try:
            with io.open(os.path.join(snapshot_root, _f), encoding='utf-8') as _fh:
                ast.parse(_fh.read(), _f)
        except (SyntaxError, OSError) as e:
            bad_base.append('%s: %s' % (_f, e))
    if bad_base:
        print('!! 基线语法坏掉 —— 仓库在制品（多半是队友正在改这个文件）：')
        for _b in bad_base:
            print('   %s' % _b)
        print('   这时跑出来的每个变异体都会"语法坏掉"，结论没有意义。'
              '请等树稳定后再跑（本轮不做任何变异）。')
        if not args.allow_broken_tree:
            shutil.rmtree(snapshot_root, ignore_errors=True)
            return 2
        print('   （--allow-broken-tree：照跑，但别拿这轮的结果当证据）')
    rows = []
    if args.jobs > 1:
        with ThreadPoolExecutor(max_workers=args.jobs) as ex:
            futs = [ex.submit(run_mutant, j, m, args.timeout, args.keep_copy,
                              snapshot_root) for j, m in work]
            for fut in futs:
                row = fut.result()
                rows.append(row)
                print('  [%-8s] %-30s %s' % (row['verdict'], row['judge'],
                                             row['caught_by'] or row.get('msg', '')[:60]))
    else:
        for j, m in work:
            row = run_mutant(j, m, args.timeout, args.keep_copy, snapshot_root)
            rows.append(row)
            print('  [%-8s] %-30s %s' % (row['verdict'], row['judge'],
                                         row['caught_by'] or row.get('msg', '')[:60]))
    elapsed = time.time() - t0
    shutil.rmtree(snapshot_root, ignore_errors=True)
    rc = print_report(rows, judges, elapsed, max(1, args.jobs))
    # ---- 仓库零改动：以"本工具写过的路径"为硬证据（比指纹精确：取证期间别的 agent
    #      可能在改同一棵树，指纹会误报成"本工具改了仓库"）。
    tmp_root = os.path.abspath(tempfile.gettempdir())
    outside = [p for p in _WRITTEN
               if os.path.abspath(p).startswith(ROOT + os.sep)
               or not os.path.abspath(p).startswith(tmp_root + os.sep)]
    print('仓库零改动：写过的路径 %d 个，全部在临时目录副本里 = %s'
          % (len(_WRITTEN), 'OK' if not outside else '**FAILED**'))
    if outside:
        print('  越界写入：%s' % ', '.join(outside[:5]))
        rc = 1
    fp_after = _tree_fp(ROOT)
    if fp_before == fp_after:
        print('仓库指纹复核：%s（未变）' % fp_before[:12])
    else:
        print('仓库指纹复核：%s -> %s（变了；但本工具一条仓库路径都没写过——'
              '这是别的 agent 在并发改树，不算本工具的问题）'
              % (fp_before[:12], fp_after[:12]))
    # ---- 空变异体被"抓住" = 套件不稳定（会假红），单独报出来
    flaky = [r for r in rows if r['kind'] == 'noop' and r['verdict'] == 'caught']
    if flaky:
        print('=> 警告：%d 个空变异体（语义没变）居然让测试变红 —— 套件本身不稳定，'
              '这张表不可信' % len(flaky))
        rc = 1
    if args.json:
        with io.open(args.json, 'w', encoding='utf-8') as f:
            json.dump(dict(rc=rc, elapsed=elapsed, jobs=max(1, args.jobs),
                           repo_untouched=fp_before == fp_after,
                           judges=[j['id'] for j in judges], rows=rows),
                      f, ensure_ascii=False, indent=2)
        print('JSON：%s' % args.json)
    return rc


if __name__ == '__main__':
    sys.exit(main())
