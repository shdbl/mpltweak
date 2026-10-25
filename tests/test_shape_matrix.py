# -*- coding: utf-8 -*-
"""T12 / 0.1.18：「对不上」形状矩阵 —— 故意让数量/顺序/名字错位的夹具生成器。

为什么要这份用例
================
今晚三个 bug 全长在"对不上"上：轴数与源码建轴点对不上（`getattr` 隐形轴）、
名字与编号对不上（`ax_inset`）、对象与写入对不上（cmap）。而**测试夹具永远干净**，
所以这类缺陷从来没被触发过 —— 每一份手写夹具都是"三根轴、名字规整、顺序友好"。

这里的做法是**生成**而不是手写：把一个脚本拆成三个维度，按组合拼出来，
专门制造"数量/顺序/名字"的不一致，然后逐个跑 `describe → apply --write`，判据只有两条：

    rc=0（写了）  ⇒ 必须**身份级写对**：重跑读回，第 k 根轴的目标字段 == 参数里
                    第 k 根的值，**并且该值没有出现在别的轴上**（没写串轴）
    rc=1（拒写）  ⇒ 通过（fail-closed 是合法结果，静默改坏图才是事故）
    rc=0 但写错   ⇒ **失败**（这是本用例唯一要抓的东西）

维度
----
A 建轴方式（8）：`subplots` / `add_axes` / `add_subplot` / `getattr` 隐形 /
                函数封装且调用多次 / 循环建轴 / `subplot_mosaic` / `gridspec`
B 特殊轴（6）：无 / colorbar `ax=` / colorbar `cax=` / `inset_axes` / `twinx` /
              `secondary_yaxis`
C 命名（3）：规整 `ax1..ax3` / 混搭 `ax1 + ax_inset` / 全非常规 `ax_a..ax_c`

A×B×C = 8×6×3 = 144 组。全量跑一次约 8 分钟（每组 = describe + apply + describe），
超出"3 分钟"的硬上限，所以按下面的**显式抽样规则**取 57 组（39.6%）：

  1. **C=混搭（事故同款命名）全覆盖**：8(A) × 6(B) = 48 组
     —— 命名混搭是今晚那两起身份事故的现场，优先全覆盖；
  2. **C=规整 / C=非常规** 只与两种"身份高危"建轴方式
     （`subplots`、`getattr` 隐形）× 两种特殊轴（无、colorbar `cax=`）交叉：2×2×2 = 8 组
     —— 钉住"换个命名方式结论是否变了"；
  3. **`reviewer_repro`**（外部评审给的 6 轴形态：`ax1 / ax_inset / cax1 / ax2 / cax2 /
     ax3`）手写 1 组 —— 任务书点名必须覆盖，且它就是"名字与编号对不上"的原始现场。

抽样是**确定性**的（不是随机）：同样的输入永远生成同样的组合，可复现。
要跑全量：`MPLTWEAK_SHAPE_MATRIX=full python tests/test_shape_matrix.py`（144 组，约 8 分钟）。

判据里刻意不含"能写就过"：`should_write` / `should_refuse` 每组**预先声明**
（`DECLARED` 表 + 生成规则），未声明或声明与实际不符都算失败。否则「正确拒写」与
「该写却没写」在结果上长得一模一样（都是没写成），两种都能冒充通过。

调用方式
--------
`python -m pytest -q tests/test_shape_matrix.py`（也可 `python tests/test_shape_matrix.py`，
直接跑会打印矩阵表：每组的 A/B/C、结局、reason、身份判据是否通过）。
"""
import concurrent.futures
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import traceback

import pytest

# GBK 控制台兜底（与 test_cli_output.py / test_roundtrip.py 同款）
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(errors='replace')
    except (AttributeError, ValueError, OSError):
        pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, 'src')
PY = sys.executable

#: 墙钟上限（秒）：超过就说明组合太多/机器太慢，必须先改抽样规则再提交
TIME_BUDGET = 180.0


# =====================================================================
# §1 跑 CLI 的脚手架（环境必须 pin：tests/test_hygiene.py 的级联闸门按调用点核对）
# =====================================================================
def _env(**over):
    out = dict(os.environ)
    out['MPLBACKEND'] = 'Agg'
    out['MPLTWEAK_LANG'] = 'en'
    out['PYTHONIOENCODING'] = 'utf-8'
    out['PYTHONUTF8'] = '1'
    out['PYTHONPATH'] = SRC + (os.pathsep + out['PYTHONPATH']
                               if out.get('PYTHONPATH') else '')
    out.update(over)
    return out


def _run(cwd, *args):
    r = subprocess.run([PY, '-m', 'mpltweak.cli', *args], cwd=cwd,
                       capture_output=True, env=_env())
    return (r.returncode,
            (r.stdout or b'').decode('utf-8', 'replace'),
            (r.stderr or b'').decode('utf-8', 'replace'))


_SCRATCH_SEQ = [0]


def _scratch_dir():
    base = tempfile.gettempdir()
    while True:
        _SCRATCH_SEQ[0] += 1
        d = os.path.join(base, 'mpltweak_shape_%d_%d'
                         % (os.getpid(), _SCRATCH_SEQ[0]))
        try:
            os.makedirs(d)
            return d
        except FileExistsError:
            continue


# =====================================================================
# §2 生成器：三维度拼脚本
# =====================================================================
#: 维度 A：建轴方式（值 = 生成函数名，见下面 _A_*）
A_MODES = ('subplots', 'add_axes', 'add_subplot', 'getattr_hidden',
           'func_multi', 'loop', 'mosaic', 'gridspec')
#: 维度 B：特殊轴（`none` = 基线）
B_SPECIALS = ('none', 'colorbar_ax', 'colorbar_cax', 'inset', 'twinx',
              'secondary_y')
#: 维度 C：命名
C_NAMINGS = ('regular', 'mixed', 'exotic')

#: 每根常规轴要画的线（保证 describe 有东西可报；模块层，不带缩进）
_PLOT = '{n}.plot([0.0, 1.0], [0.0, 1.0])\n'

#: 三种命名给三根常规轴取什么名字。混搭 = 把**普通轴**起成 `ax_inset`
#: （这正是"名字与编号对不上"的原始现场：引擎容易把它当成 inset 特殊轴处理）。
_NAMES = {
    'regular': ('ax1', 'ax2', 'ax3'),
    'mixed': ('ax1', 'ax_inset', 'ax3'),
    'exotic': ('ax_a', 'ax_b', 'ax_c'),
}


def _a_subplots(names):
    n0, n1, n2 = names
    return (
        'fig, _axes = plt.subplots(1, 3, figsize=(9, 3))\n'
        '%s, %s, %s = _axes[0], _axes[1], _axes[2]\n' % (n0, n1, n2))


def _a_add_axes(names):
    n0, n1, n2 = names
    return (
        'fig = plt.figure(figsize=(9, 3))\n'
        '%s = fig.add_axes([0.06, 0.12, 0.24, 0.74])\n'
        '%s = fig.add_axes([0.38, 0.12, 0.24, 0.74])\n'
        '%s = fig.add_axes([0.70, 0.12, 0.24, 0.74])\n' % (n0, n1, n2))


def _a_add_subplot(names):
    n0, n1, n2 = names
    return (
        'fig = plt.figure(figsize=(9, 3))\n'
        '%s = fig.add_subplot(1, 3, 1)\n'
        '%s = fig.add_subplot(1, 3, 2)\n'
        '%s = fig.add_subplot(1, 3, 3)\n' % (n0, n1, n2))


def _a_getattr_hidden(names):
    """建轴点用 `getattr(fig, 'add_axes')` 藏起来 —— 静态扫描数不到它。"""
    n0, n1, n2 = names
    return (
        'fig = plt.figure(figsize=(9, 3))\n'
        "_add = getattr(fig, 'add_axes')\n"
        '%s = _add([0.06, 0.12, 0.24, 0.74])\n'
        '%s = _add([0.38, 0.12, 0.24, 0.74])\n'
        '%s = _add([0.70, 0.12, 0.24, 0.74])\n' % (n0, n1, n2))


def _a_func_multi(names):
    """函数封装建图，且**被调用多次**（两张图）—— 目标图是后建的那张。"""
    n0, n1, n2 = names
    return (
        'def build(k):\n'
        '    fig, axes = plt.subplots(1, 3, figsize=(9, 3))\n'
        '    for j, axe in enumerate(axes):\n'
        '        axe.plot([0.0, 1.0], [0.0, float(j + k)])\n'
        '    return fig, axes\n'
        '\n'
        '_spare_fig, _spare = build(1)\n'
        'fig, _axes = build(2)\n'
        '%s, %s, %s = _axes[0], _axes[1], _axes[2]\n' % (n0, n1, n2))


def _a_loop(names):
    n0, n1, n2 = names
    return (
        'fig = plt.figure(figsize=(9, 3))\n'
        '_made = []\n'
        'for i in range(3):\n'
        '    _made.append(fig.add_axes([0.06 + i * 0.32, 0.12, 0.24, 0.74]))\n'
        '%s, %s, %s = _made[0], _made[1], _made[2]\n' % (n0, n1, n2))


def _a_mosaic(names):
    n0, n1, n2 = names
    return (
        "fig, _axd = plt.subplot_mosaic([['a', 'b', 'c']], figsize=(9, 3))\n"
        "%s, %s, %s = _axd['a'], _axd['b'], _axd['c']\n" % (n0, n1, n2))


def _a_gridspec(names):
    n0, n1, n2 = names
    return (
        'fig = plt.figure(figsize=(9, 3))\n'
        '_gs = fig.add_gridspec(1, 3)\n'
        '%s = fig.add_subplot(_gs[0, 0])\n'
        '%s = fig.add_subplot(_gs[0, 1])\n'
        '%s = fig.add_subplot(_gs[0, 2])\n' % (n0, n1, n2))


_A_BUILDERS = {
    'subplots': _a_subplots,
    'add_axes': _a_add_axes,
    'add_subplot': _a_add_subplot,
    'getattr_hidden': _a_getattr_hidden,
    'func_multi': _a_func_multi,
    'loop': _a_loop,
    'mosaic': _a_mosaic,
    'gridspec': _a_gridspec,
}


def _b_snippet(special, names):
    """维度 B：在第 0 根常规轴上挂一个特殊轴（colorbar / inset / twin / secondary）。"""
    n0 = names[0]
    if special == 'none':
        return ''
    if special == 'colorbar_ax':
        return ('_im = %s.imshow([[0.0, 1.0], [2.0, 3.0]])\n'
                'fig.colorbar(_im, ax=%s)\n' % (n0, n0))
    if special == 'colorbar_cax':
        return ('_cax = fig.add_axes([0.92, 0.12, 0.02, 0.74])\n'
                '_im = %s.imshow([[0.0, 1.0], [2.0, 3.0]])\n'
                'fig.colorbar(_im, cax=_cax)\n' % (n0,))
    if special == 'inset':
        return '_ins = %s.inset_axes([0.60, 0.62, 0.30, 0.30])\n' % (n0,)
    if special == 'twinx':
        return '_tw = %s.twinx()\n' % (n0,)
    if special == 'secondary_y':
        return "_sec = %s.secondary_yaxis('right')\n" % (n0,)
    raise ValueError(special)


def build_script(a, b, c):
    """按 (建轴方式, 特殊轴, 命名) 生成一份脚本源码。"""
    names = _NAMES[c]
    head = ('import matplotlib\n'
            '\n'
            "matplotlib.use('Agg')\n"
            'import matplotlib.pyplot as plt\n'
            '\n')
    body = _A_BUILDERS[a](names)
    body += ''.join(_PLOT.format(n=n) for n in names)
    body += _b_snippet(b, names)
    return head + body + "fig.savefig('out.png')\n"


#: 任务书点名的**已知事故形态**（外部评审的复现夹具）：6 根轴、名字混搭、
#: 两根 cax 色条插在中间 —— "名字与编号对不上"的原始现场。
REVIEWER_REPRO = '''\
import matplotlib

matplotlib.use('Agg')
import matplotlib.pyplot as plt

fig = plt.figure(figsize=(10, 4))
ax1 = fig.add_axes([0.06, 0.12, 0.20, 0.74])
ax_inset = fig.add_axes([0.29, 0.12, 0.20, 0.74])
ax2 = fig.add_axes([0.52, 0.12, 0.20, 0.74])
ax3 = fig.add_axes([0.75, 0.12, 0.18, 0.74])
cax1 = fig.add_axes([0.285, 0.90, 0.02, 0.06])
cax2 = fig.add_axes([0.515, 0.90, 0.02, 0.06])
im1 = ax1.imshow([[0.0, 1.0], [2.0, 3.0]])
im2 = ax_inset.imshow([[0.0, 2.0], [4.0, 6.0]])
fig.colorbar(im1, cax=cax1)
fig.colorbar(im2, cax=cax2)
fig.savefig('out.png')
'''


# =====================================================================
# §3 抽样规则（确定性）+ 预先声明的预期
# =====================================================================
def matrix_cases(full=False):
    """返回要跑的 (a, b, c) 列表。默认按文件头写的抽样规则取 57 组。"""
    if full or os.environ.get('MPLTWEAK_SHAPE_MATRIX', '').lower() == 'full':
        return [(a, b, c) for a in A_MODES for b in B_SPECIALS
                for c in C_NAMINGS]
    cases = []
    # 规则 1：命名混搭（事故同款）全覆盖
    for a in A_MODES:
        for b in B_SPECIALS:
            cases.append((a, b, 'mixed'))
    # 规则 2：另两种命名只与两种"身份高危"建轴方式 × 两种特殊轴交叉
    for c in ('regular', 'exotic'):
        for a in ('subplots', 'getattr_hidden'):
            for b in ('none', 'colorbar_cax'):
                cases.append((a, b, c))
    return cases


#: 预先声明的预期：`should_refuse` 的集合（按规则生成，不是事后标注）。
#:
#: 规则来源（0.1.18 现状 + 生成器维度）：
#:   * **建轴点在源码里数不到 / 会重复执行**的三种：`getattr` 隐形建轴、循环建轴、
#:     函数封装且被多次调用 —— 轴身份证不出来 ⇒ 必须 fail-closed 拒写；
#:     这三种"该拒写却没拒"比"该写却没写"危险得多（前者会静默改坏图）。
#:   * 其余建轴方式（subplots / add_axes / add_subplot / mosaic / gridspec）**每一根
#:     常规轴在源码里都有唯一、静态可见的建轴点** ⇒ 应当写得进去。
#:
#: 特殊轴（B）与命名（C）**不改**这个判断：它们变的是"图里有几根额外轴"和"变量叫什么"，
#: 而写回的目标是第一根常规轴 —— 那根轴的建轴点没变。这条假设本身也是被测对象：
#: 若某组 declared=write 实际拒写，用例会红并点名，届时再把它显式登记进下面这张表。
DECLARED_REFUSE_A = frozenset({'getattr_hidden', 'loop', 'func_multi'})

#: 事先写死的例外（(a, b, c) -> 理由）。目前为空：第一轮全量实测后没有任何一组
#: 落在"按规则该写、实际却拒写"或"按规则该拒、实际却写了"上。
DECLARED_OVERRIDE = {}


def declared_outcome(a, b, c):
    """(a, b, c) 的预期结局：'should_write' 或 'should_refuse'。"""
    if (a, b, c) in DECLARED_OVERRIDE:
        return DECLARED_OVERRIDE[(a, b, c)]
    return 'should_refuse' if a in DECLARED_REFUSE_A else 'should_write'


# =====================================================================
# §4 单组跑法 + 身份级判据
# =====================================================================
def _describe(d, script, out_name):
    rc, out, err = _run(d, 'describe', script, '-o', out_name, '--compact')
    if rc != 0:
        raise AssertionError('describe 失败 rc=%d\n%s\n%s'
                             % (rc, out[-600:], err[-600:]))
    with open(os.path.join(d, out_name), encoding='utf-8') as f:
        return json.load(f)


def _target_pos(k):
    """第 k 根轴的**目标位置**：4 位小数、按 k 唯一、不与常见排版重合。

    唯一性是判据的一部分："这个值只许出现在第 k 根轴上"—— 若出现两次，说明写串了。
    """
    return [round(0.06 + 0.11 * k, 4), 0.13, 0.30, 0.30]


def _pos_of(axis):
    return [round(float(v), 6) for v in (axis.get('pos') or [])]


def _primary_axes(axes):
    """可作写回目标的"主图轴"：排除 colorbar，以及**位置跟别人重合**的轴。

    为什么第二条必需：`twinx()` / `secondary_yaxis()` 造出来的轴在 describe 里是**独立的
    index**，且和父轴**共用同一个矩形**（matplotlib 的 twinned axes 共享定位），
    而 describe 没有 `is_twin` 这样的字段（见报告里的 F2）。所以"最后那根轴"很容易
    选中一根 twin/ secondary 轴 —— 拿它当目标去写位置，等于要求"只挪孪生轴不挪父轴"，
    物理上做不到（父轴会跟着动，语义校验随即报"轴身份错位"并回滚）。
    这份矩阵要测的是**主图轴的身份寻址**，所以目标只在"位置唯一"的轴里挑。
    """
    out, seen = [], []
    for ax in axes:
        if ax.get('is_colorbar'):
            continue
        pos = _pos_of(ax)
        if pos in seen:
            continue                      # 与前面某根轴共位 ⇒ twin / secondary
        seen.append(pos)
        out.append(ax)
    return out


def _judge_identity(before_axes, after_axes, k, want_pos, followers=()):
    """身份级判据（纯函数，可单独喂合成数据做反向验证）。

    返回 {'identity_ok', 'collision', 'collateral', 'detail'}：
      * `identity_ok`：第 k 根轴的目标字段 == 参数里第 k 根的值；
      * `collision`：**写完之后才出现**的同值重合（写之前的 twin/secondary 不算）
        —— "该值出现在别的轴上" = 写串轴；
      * `collateral`：没要求的轴被挪了。
    """
    now = {int(ax['index']): ax for ax in after_axes}
    got = _pos_of(now[k]) if k in now else None
    identity_ok = bool(got) and all(
        abs(g - w) <= max(1e-9 * abs(w), 1e-12) for g, w in zip(got, want_pos))
    collision = sorted(
        int(i) for i, ax in now.items()
        if int(i) != k and int(i) not in tuple(followers)
        and _pos_of(ax) == [round(float(v), 6) for v in want_pos])
    before_map = {int(ax['index']): ax for ax in before_axes}
    collateral = sorted(
        int(i) for i, ax in now.items()
        if i != k and i in before_map
        and _pos_of(before_map[i]) != _pos_of(ax))
    detail = ''
    if not identity_ok:
        detail = '身份不对：want=%r got=%r' % (want_pos, got)
    elif collision:
        detail = '目标值出现在了别的轴上：%r' % (collision,)
    elif collateral:
        detail = '没要求的轴被挪了：%r' % (collateral,)
    return {'identity_ok': identity_ok, 'collision': collision,
            'collateral': collateral, 'detail': detail}


def run_case(a, b, c, source=None):
    """跑一组；返回结果 dict（不抛断言，由 test 决定怎么判）。

    返回字段：outcome（'wrote' / 'refused' / 'error'）、reason、target_index、
    identity_ok、collision、collateral、detail。
    """
    d = _scratch_dir()
    script = 'shape.py'
    try:
        src = source if source is not None else build_script(a, b, c)
        with open(os.path.join(d, script), 'w', encoding='utf-8',
                  newline='\n') as f:
            f.write(src)
        res = {'case': (a, b, c), 'outcome': 'error', 'reason': '',
               'detail': '', 'identity_ok': None, 'collision': None,
               'collateral': None, 'target_index': None}

        before = _describe(d, script, 'a.json')
        axes = before.get('axes') or []
        ordinary = _primary_axes(axes)
        if not ordinary:
            res['detail'] = 'describe 里没有可作目标的主图轴（夹具本身有问题）'
            return res
        # 目标：**最后一根**主图轴（写串轴时最容易露馅的位置）
        target = ordinary[-1]
        k = int(target['index'])
        want_pos = _target_pos(k)
        res['target_index'] = k
        # 写之前，这组里"与目标共位"的轴（twin / secondary）：它们跟着父轴动是**应有**
        # 行为，不算"写串轴"。判据只看"写完之后才出现的重合"。
        followers = sorted(int(ax['index']) for ax in axes
                           if int(ax['index']) != k
                           and _pos_of(ax) == _pos_of(target))

        params = json.loads(json.dumps(before))
        for ax in params.get('axes') or []:
            if int(ax['index']) == k:
                ax['pos'] = list(want_pos)
        with open(os.path.join(d, 'p.json'), 'w', encoding='utf-8',
                  newline='\n') as f:
            json.dump(params, f, ensure_ascii=False)

        rc, out, err = _run(d, 'apply', script, '--params', 'p.json',
                            '--write', '--json')
        payload = None
        try:
            payload = json.loads(out)
        except ValueError:
            payload = None
        entry = ((payload or {}).get('files') or [{}])[0]
        applied = bool(entry.get('applied'))
        res['reason'] = (entry.get('reason') or (payload or {}).get('error_code')
                         or '')

        if rc != 0 or not applied:
            res['outcome'] = 'refused'
            res['detail'] = 'rc=%d reason=%r' % (rc, res['reason'])
            return res

        res['outcome'] = 'wrote'
        after = _describe(d, script, 'c.json')
        verdict = _judge_identity(axes, after.get('axes') or [], k, want_pos,
                                  followers)
        res.update(verdict)
        return res
    except Exception:                                            # noqa: BLE001
        res = locals().get('res') or {'case': (a, b, c)}
        res['outcome'] = 'error'
        res['detail'] = traceback.format_exc()[-1200:]
        return res
    finally:
        shutil.rmtree(d, ignore_errors=True)


# =====================================================================
# §5 用例：矩阵（并行跑，墙钟 < TIME_BUDGET）
# =====================================================================
def _workers():
    """并发度：每组都是独立子进程（读脚本 + 跑 matplotlib），所以线程并发是安全的。

    上限 6：再多就只是抢 CPU（每组进程自己就要 1 核左右），并不会更快；
    下限 2：单核机器上至少让"写文件 + 起进程"的空档被利用起来。
    实测（开发机 8 核）：4 线程 113s，6 线程 79s。
    """
    cpu = os.cpu_count() or 2
    return max(2, min(6, cpu))


def _run_all(cases, workers=None):
    results = []
    with concurrent.futures.ThreadPoolExecutor(
            max_workers=workers or _workers()) as pool:
        futures = [pool.submit(run_case, a, b, c) for (a, b, c) in cases]
        for fut in futures:
            results.append(fut.result())
    results.sort(key=lambda r: (r['case'][0], r['case'][1], r['case'][2]))
    return results


def _print_matrix(results, elapsed):
    print('SHAPE | %-14s %-13s %-8s | %-8s | %-16s | %s'
          % ('A(建轴)', 'B(特殊轴)', 'C(命名)', '结局', 'reason', '身份判据'))
    for r in results:
        a, b, c = r['case']
        print('SHAPE | %-14s %-13s %-8s | %-8s | %-16s | %s'
              % (a, b, c, r['outcome'], r['reason'] or '-',
                 'ok' if r.get('identity_ok') else
                 ('n/a' if r['outcome'] != 'wrote' else 'BAD')))
    print('SHAPE | 共 %d 组，墙钟 %.1fs' % (len(results), elapsed))


def test_shape_matrix_never_writes_wrong_identity():
    """核心判据：每一组「要么写对、要么拒写」，且结局与**预先声明**一致。

    - rc=0 ⇒ 身份级写对（第 k 根轴 == 参数第 k 根；该值不在别的轴上；没要求的轴没动）；
    - rc=1 ⇒ 通过（fail-closed）；
    - rc=0 但写错 / 声明与实际不符 / 跑挂了 ⇒ 红，并点名是哪一组。
    """
    cases = matrix_cases()
    t0 = time.time()
    results = _run_all(cases)
    elapsed = time.time() - t0
    _print_matrix(results, elapsed)

    bad = []
    for r in results:
        a, b, c = r['case']
        declared = declared_outcome(a, b, c)
        actual = 'should_refuse' if r['outcome'] == 'refused' else 'should_write'
        if r['outcome'] == 'error':
            bad.append((r['case'], 'error', r['detail']))
            continue
        if r['outcome'] == 'wrote':
            if not r.get('identity_ok'):
                bad.append((r['case'], 'rc=0 但写错了（身份不对）', r['detail']))
                continue
            if r.get('collision'):
                bad.append((r['case'], 'rc=0 但写串了轴（目标值出现在别的轴上）',
                            r['detail']))
                continue
            if r.get('collateral'):
                bad.append((r['case'], 'rc=0 但把没要求的轴挪了', r['detail']))
                continue
        if actual != declared:
            bad.append((r['case'], '结局与预先声明不符（声明 %s，实际 %s：reason=%r）'
                        % (declared, actual, r['reason']), r['detail']))

    assert not bad, (
        '%d / %d 组没过判据：\n%s'
        % (len(bad), len(results),
           '\n'.join('  %r -> %s\n      %s' % (case, why, det[:600])
                     for case, why, det in bad[:12])))
    assert elapsed < TIME_BUDGET, (
        '矩阵跑了 %.1fs，超过 %ss 的上限 —— 请改抽样规则（见文件头），'
        '而不是把 CI 拖成 5 分钟' % (elapsed, TIME_BUDGET))


def test_matrix_covers_the_known_reviewer_repro():
    """任务书点名的 `reviewer_repro`（ax1/ax_inset/cax1/ax2/cax2/ax3）必须被覆盖且通过。

    这份形态是"名字与编号对不上"的原始现场（普通轴叫 `ax_inset`、两根 cax 色条夹在
    中间）。它同时也是**抽样规则**的守门人：如果哪天真把这条形态从抽样里挤掉，
    这条用例会红。
    """
    r = run_case('reviewer_repro', 'reviewer-repro', 'mixed', source=REVIEWER_REPRO)
    print('SHAPE | reviewer_repro | 结局=%s | reason=%s | 身份=%s | %s'
          % (r['outcome'], r['reason'] or '-',
             'ok' if r.get('identity_ok') else 'n/a', r.get('detail', '')))
    assert r['outcome'] != 'error', 'reviewer_repro 跑挂了：\n%s' % r['detail']
    if r['outcome'] == 'wrote':
        assert r.get('identity_ok'), (
            'reviewer_repro 写了但写错（这正是外部评审报的那类）：%s' % r['detail'])
        assert not r.get('collision'), (
            'reviewer_repro 写串了轴：%s' % r['detail'])
    # 拒写也接受（fail-closed），但必须说得清原因
    if r['outcome'] == 'refused':
        assert r['reason'], 'reviewer_repro 拒写了却没说原因'


def test_generator_really_produces_mismatched_shapes():
    """生成器自身的护栏：它必须真的产出"对不上"的形态，否则整张矩阵是在测空气。

    三条：① 三个维度都非空、组合数 = 抽样规则算出来的数；② 三种命名真的不同，
    且"混搭"里确实有一根**普通轴**叫 `ax_inset`；③ 生成出来的每个脚本都能被
    `ast.parse()`（语法合法），且 `getattr_hidden` 那种形态里 `add_axes` 是以
    `getattr(...)` 形式出现的（静态扫描数不到它 —— 这才是那个维度的意义）。
    """
    cases = matrix_cases()
    assert A_MODES and B_SPECIALS and C_NAMINGS
    full_n = len(A_MODES) * len(B_SPECIALS) * len(C_NAMINGS)
    assert 0 < len(cases) <= full_n
    assert len(cases) == (8 * 6 + 2 * 2 * 2), (
        '抽样规则变了却没更新这条断言：现在 %d 组，规则算出来应为 %d 组'
        % (len(cases), 8 * 6 + 2 * 2 * 2))
    # 三种命名互不相同，且混搭确实把普通轴起成 ax_inset
    assert len({_NAMES[c] for c in C_NAMINGS}) == 3
    assert 'ax_inset' in _NAMES['mixed']
    assert all(n.startswith('ax') for n in _NAMES['exotic'])
    # 每个组合的脚本都得是合法 Python，且隐形建轴那种真的藏起来了
    import ast
    for (a, b, c) in cases:
        src = build_script(a, b, c)
        ast.parse(src)
    hidden = build_script('getattr_hidden', 'none', 'regular')
    assert "getattr(fig, 'add_axes')" in hidden
    assert 'fig.add_axes(' not in hidden, (
        '这个维度的意义就是"静态扫描数不到建轴点"，不许出现直白的 fig.add_axes(')


def test_identity_judge_has_teeth():
    """给**判据本身**做牙：喂 4 组合成数据，错的那种必须被判错。

    没有这一条，`test_shape_matrix_never_writes_wrong_identity` 可能只是"永远绿"
    （引擎写错、而判据也一起错，就看不出来）。四组：

      ① 写对了 ⇒ identity_ok，无 collision、无 collateral（正对照）；
      ② 值落到了**别的轴**上（第 k 根没变）⇒ identity_ok=False；
      ③ 第 k 根对了，但**另一个轴也拿到了同一个值**（写串轴）⇒ collision 非空；
      ④ 第 k 根对了，但**第三个轴被顺手挪了**（附带损伤）⇒ collateral 非空。
    """
    before = [{'index': 0, 'pos': [0.10, 0.10, 0.30, 0.30]},
              {'index': 1, 'pos': [0.50, 0.10, 0.30, 0.30]},
              {'index': 2, 'pos': [0.10, 0.50, 0.30, 0.30]}]
    want = [0.39, 0.13, 0.3, 0.3]

    def after_with(k_pos, extra=None):
        out = []
        for ax in before:
            pos = list(ax['pos'])
            if ax['index'] == k_pos[0]:
                pos = list(k_pos[1])
            out.append({'index': ax['index'], 'pos': pos})
        for idx, pos in (extra or {}).items():
            out.append({'index': idx, 'pos': list(pos)})
        return out

    # ① 正对照：第 1 根写对了
    ok = _judge_identity(before, after_with((1, want)), 1, want)
    assert ok['identity_ok'] and not ok['collision'] and not ok['collateral'], ok

    # ② 值落到了第 0 根上（第 1 根没变）⇒ 身份错
    wrong = _judge_identity(before, after_with((0, want)), 1, want)
    assert not wrong['identity_ok'], wrong
    assert wrong['detail'], wrong

    # ③ 第 1 根对了，另一根也拿到了同一个值 ⇒ 写串轴
    bleed = _judge_identity(before, after_with((1, want), {2: want}), 1, want)
    assert bleed['identity_ok'], bleed
    assert bleed['collision'] == [2], bleed

    # ④ 第 1 根对了，但第 0 根被挪了 ⇒ 附带损伤
    moved = _judge_identity(before, after_with((1, want), {0: [0.2, 0.2, 0.1, 0.1]}),
                            1, want)
    assert moved['identity_ok'], moved
    assert moved['collateral'] == [0], moved

    # ⑤ twin 情形：写之前就与目标共位的轴跟着动 ⇒ **不算**写串轴
    twin_before = before + [{'index': 3, 'pos': [0.10, 0.10, 0.30, 0.30]}]
    twin_after = after_with((0, want), {3: want})
    twin = _judge_identity(twin_before, twin_after, 0, want, followers=(3,))
    assert twin['identity_ok'] and not twin['collision'], twin


if __name__ == '__main__':
    # 脚本模式（`python tests/<这个文件名>.py`，与 tests/test_suites.py 的 SUITES
    # 调用方式一致）：退出码与 pytest 完全一致；**全部断言通过时**补一行 `ALL PASS`。
    #
    # 为什么必须补：包装器（tests/test_suites.py::test_suite）对每个套件有**两条**
    # 判据 —— 退出码 0 **且** stdout 里出现 `ALL PASS`。纯 pytest 式套件即使全绿也
    # 一个词都不打，于是被包装器判成"可能中途崩了"（0.1.18 的 CI 就红在这里）。
    #
    # 为什么只在 `_rc == 0` 时打：无条件打就等于把双判据退化成单判据 —— 失败也带
    # `ALL PASS`，那正是本项目反复咬到的"假绿"。范本见 tests/test_gui_smoke.py 尾部。
    _rc = pytest.main([os.path.abspath(__file__), '-q', '--no-header'])
    if _rc == 0:
        print('ALL PASS')
    sys.exit(_rc)
