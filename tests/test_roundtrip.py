# -*- coding: utf-8 -*-
"""T11 / 0.1.18：`describe` → `apply --write` → `describe` 的**往返恒等**（metamorphic）。

为什么要这份用例
================
"describe → apply 应当是一次恒等变换"这条要求一直是硬要求（describe 报的值就是
apply 要落的值，落完再 describe 必须还是那些值），但**从来没有被系统测过**。
0.1.17 那几起事故——轴身份错位、diff 脏（改一根轴却动 30 行）、cmap 错位——本质
都是这条不成立：**写回去的东西，读回来对不上**。单看某一条用例"写回成功"是看不出来的，
因为写坏了也 rc=0。

所以这里不测"某个夹具的具体文案/具体行号"，只钉**元性质（metamorphic）**：

    A  = describe(S)            # 地面真值
    P  = A 里挑一个字段改成新值  # 我们**要求的**值
    apply(S, P, --write)        # 落盘（写不成必须显式说"没写"，不许静默）
    C  = describe(S)            # 再读一次
    C  ≈ P                      # ① 保真：要什么就是什么（1e-9 相对容差）
    C \\ P ≈ A \\ P              # ② 无附带损伤：没要求的字段一个都不许动
    apply(S, P, --write) 再来一次 ⇒ 字节不变（幂等）

三条一起才是"恒等"：只测 ① 会漏附带损伤，只测 ② 会漏写不进去。

口径（读断言前先看这三条）
--------------------------
1. **顺序**：`axes[]` / `lines[]` 按 `index` 归位后再比（`index` 是身份，JSON 数组的
   先后不是"图的样子"）。
2. **豁免**：只有一张**逐项写理由**的白名单（`EXEMPT_KEYS`，目前只有 `script` 一项：
   它是"这份参数从哪个文件导出的"，换目录/换机器就变）。**没有"含 time 就丢"这种
   模式匹配** —— 将来 describe 多一个 `generated_at`，它会留在比较里并当场变红，逼着
   人来补一行理由；`test_exemption_list_is_explicit_and_closed` 反向钉住这一点。
3. **浮点**：`rtol=1e-9` **加** `atol=1e-12` 组合。纯相对容差在近零上失效（`0` 与
   `5e-13` 的浮点噪声会被判成 bug），纯绝对容差在大数上失效；两条各管一段，
   `test_tolerance_is_rtol_plus_atol` 逐条钉住（含 `0.001 → 0.002` 必须判**不等**）。
   另外：写回块里的数字按 4 位小数打印（`set_position([0.1400, ...])`），所以夹具的
   "目标值"必须落在 4 位小数上 —— 否则往返差的是"打印精度"而不是 bug。
4. **先正确、后幂等**：**错得一致也算幂等**，所以"第二次 apply 字节不变"根本证明不了
   写对了。顺序是：① 一次就对（写完后 describe == 我们要求的 P）→ ② 无附带损伤
   （没要求的字段 == 写之前）→ ③ 才谈幂等。另有一张**预先声明**的预期表（`EXPECT`），
   把"该写却没写"与"该拒写却写了"分别钉死——不声明的话这两种在结果上长得一模一样，
   都能冒充"通过"。

夹具（9 个，覆盖现实形态）
--------------------------
subplots 网格 / `add_axes` 手写 / colorbar（`ax=`）/ colorbar（`cax=`）/ `inset_axes` /
循环建轴 / 函数封装建图且被多次调用 / `subplot_mosaic` / `gridspec`。

调用方式
--------
`python -m pytest -q tests/test_roundtrip.py`（也可 `python tests/test_roundtrip.py`，
直接跑会打印每夹具的往返结果表）。
"""
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile

import pytest

# GBK 控制台兜底（与 test_cli_output.py 同款，理由见那边的注释）：
# 本文件按 utf-8 解码子进程输出、失败时会把原文打进断言消息，本地 GBK 控制台一
# print 到非法字符就 UnicodeEncodeError —— 那是"假红"（CI 绿、本地崩）。
# 守护它的是 tests/test_hygiene.py::test_test_suites_cannot_crash_gbk_console。
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(errors='replace')
    except (AttributeError, ValueError, OSError):
        pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, 'src')
PY = sys.executable


# =====================================================================
# §1 跑 CLI 的脚手架（与 test_cli_output.py 同一口径：环境必须 pin 死）
# =====================================================================
def _env(**over):
    """子进程环境：**硬钉 Agg**（宿主 MPLBACKEND 可能是 Qt5Agg/TkAgg）+ 钉语言与编码。

    tests/test_hygiene.py 的"级联闸门"棘轮按**调用点**核对这件事（每个
    `subprocess` 调用点都必须显式传 env= 且含 MPLBACKEND=Agg），所以这里统一走
    同一个构造器，不在调用点现拼 dict。
    """
    out = dict(os.environ)
    out['MPLBACKEND'] = 'Agg'
    out['MPLTWEAK_LANG'] = 'en'
    out['PYTHONIOENCODING'] = 'utf-8'
    out['PYTHONUTF8'] = '1'
    out['PYTHONPATH'] = SRC + (os.pathsep + out['PYTHONPATH']
                               if out.get('PYTHONPATH') else '')
    out.update(over)
    return out


def _run(cwd, *args, **over):
    """跑一次真 CLI，返回 (rc, stdout, stderr)（都按 utf-8 解）。"""
    r = subprocess.run([PY, '-m', 'mpltweak.cli', *args], cwd=cwd,
                       capture_output=True, env=_env(**over))
    return (r.returncode,
            (r.stdout or b'').decode('utf-8', 'replace'),
            (r.stderr or b'').decode('utf-8', 'replace'))


def _write(path, text):
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        f.write(text)


def _read_bytes(path):
    with open(path, 'rb') as f:
        return f.read()


_SCRATCH_SEQ = [0]


def _scratch_dir():
    """进程内唯一、且**可写**的临时目录（不用 mkdtemp：沙箱里它能建不能写）。"""
    base = tempfile.gettempdir()
    while True:
        _SCRATCH_SEQ[0] += 1
        d = os.path.join(base, 'mpltweak_rt_%d_%d' % (os.getpid(),
                                                      _SCRATCH_SEQ[0]))
        try:
            os.makedirs(d)
            return d
        except FileExistsError:
            continue


@pytest.fixture()
def work():
    """一个空的可写目录（夹具脚本、参数 JSON、产物都放这里）。"""
    d = _scratch_dir()
    try:
        yield d
    finally:
        shutil.rmtree(d, ignore_errors=True)


# =====================================================================
# §2 参数 JSON 的比较器：**逐项列出的**豁免 + rtol/atol 组合容差
#
# 为什么"忽略时间戳"这种笼统说法不合格：它等于给比较器开了一个**模式匹配**的后门 ——
# 将来 describe 多一个 `generated_at` / `sha` / `ids` 字段，会被静默丢掉，往返"绿"着
# 却什么都没验。这里反过来：**白名单 + 逐项理由**，不在名单里的字段一个都不许丢
# （丢了就会在断言里现形，逼着人来加一行并写理由）。
# =====================================================================
#: 比较前整个丢掉的字段 —— **每项必须写理由**。describe 目前的产物里只有 `script`
#: 属于这一类（它现在是唯一一个"跟图无关"的键）；将来若 describe 加了时间戳/内部 id，
#: 应当**在这里加一条并写清为什么它不是"图的样子"**，而不是放开一个模式匹配。
EXEMPT_KEYS = {
    'script': '脚本路径/文件名：它是"这份参数是从哪个文件导出的"，换目录/换机器就变，'
              '不是图本身的形状；本轮每个夹具文件名都不同，比它等于比夹具名',
}

#: 顺序无关的集合（不是豁免内容，只是**归位后再比**）：列出键名 + 理由。
ORDER_INSENSITIVE = {
    'axes': '按 `index` 归位后再比：`index` 是轴的身份（写回也按它寻址），'
            '而 JSON 数组的先后不是"图的样子"',
    'lines': '按 `index` 归位后再比：图元的绘制顺序不影响排版，身份是 `index`',
}

#: describe 产物的**已知字段全集**（顶层 / 单轴 / 单图元）。
#: 用途：`test_exemption_list_is_explicit_and_closed` 据此证明"没有字段被悄悄丢掉"。
#: describe 新增字段时这条断言会红 —— 那不是麻烦，那就是这次要做的决定：
#: 要么加进比较（通常如此），要么加进 EXEMPT_KEYS 并写理由。
KNOWN_KEYS = frozenset({'version', 'figsize_px', 'figsize_in', 'fig_index',
                        'n_figs', 'axes', 'layout_engine'})
KNOWN_AXIS_KEYS = frozenset({'index', 'pos', 'aspect_locked', 'title_fontsize',
                             'label_fontsize', 'label_fontsize_y', 'tick_fontsize',
                             'tick_fontsize_y', 'is_colorbar', 'clim', 'cell',
                             'xlim', 'ylim', 'xscale', 'yscale', 'grid', 'spines',
                             'cmap', 'lines'})
KNOWN_LINE_KEYS = frozenset({'index', 'linewidth', 'color'})

#: 容差：**相对 + 绝对**组合。纯相对容差在近零值上失效
#: （`0.0` 与 `1e-15` 的相对误差是"无穷大" ⇒ 噪声被当成 bug；反过来 `0.001 → 0.002`
#: 这种真实的 1e-3 偏差不能被判相等）。两条都要有，`ABS_TOL` 只兜"0 附近的浮点噪声"。
REL_TOL = 1e-9
ABS_TOL = 1e-12


def _norm(obj):
    """标准化：按 EXEMPT_KEYS 丢字段、按 ORDER_INSENSITIVE 归位；**其它一律保留**。"""
    if isinstance(obj, dict):
        out = {}
        for k in sorted(obj):
            if k in EXEMPT_KEYS:
                continue
            sub = obj[k]
            if k in ORDER_INSENSITIVE and isinstance(sub, list):
                items = [_norm(x) for x in sub]
                if items and all(isinstance(x, dict) and 'index' in x
                                 for x in items):
                    items.sort(key=lambda x: x['index'])
                out[k] = items
            else:
                out[k] = _norm(sub)
        return out
    if isinstance(obj, list):
        return [_norm(x) for x in obj]
    return obj


def _same_num(a, b, rel=REL_TOL, abs_=ABS_TOL):
    """数值比较：relative **和** absolute 两个容差同时给（见上面两条常量的理由）。"""
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b
    return math.isclose(float(a), float(b), rel_tol=rel, abs_tol=abs_)


def _diff(exp, act, path='', acc=None):
    """逐字段比较，返回人话差异列表（空列表 = 相等）。"""
    acc = [] if acc is None else acc
    here = path or '<root>'
    if isinstance(exp, dict) and isinstance(act, dict):
        for k in sorted(set(exp) | set(act)):
            sub = ('%s.%s' % (path, k)) if path else k
            if k not in exp:
                acc.append('%s: 多出来的字段（期望里没有）= %r' % (sub, act[k]))
            elif k not in act:
                acc.append('%s: 丢了的字段（期望 %r）' % (sub, exp[k]))
            else:
                _diff(exp[k], act[k], sub, acc)
        return acc
    if isinstance(exp, list) and isinstance(act, list):
        if len(exp) != len(act):
            acc.append('%s: 条数不同（期望 %d，实际 %d）'
                       % (here, len(exp), len(act)))
        for i in range(min(len(exp), len(act))):
            _diff(exp[i], act[i], '%s[%d]' % (path, i), acc)
        return acc
    if isinstance(exp, bool) or isinstance(act, bool):
        if exp is not act:
            acc.append('%s: 期望 %r，实际 %r' % (here, exp, act))
        return acc
    if isinstance(exp, (int, float)) and isinstance(act, (int, float)):
        if not _same_num(exp, act):
            acc.append('%s: 期望 %r，实际 %r（差 %r）'
                       % (here, exp, act, float(act) - float(exp)))
        return acc
    if exp != act:
        acc.append('%s: 期望 %r，实际 %r' % (here, exp, act))
    return acc


# =====================================================================
# §3 夹具：9 个现实形态（每个都只改 `axes[index==0].pos` 的 x +0.01）
# =====================================================================
_GRID = '''\
import matplotlib

matplotlib.use('Agg')
import matplotlib.pyplot as plt

fig, axes = plt.subplots(2, 2, figsize=(6, 5))
for i, ax in enumerate(axes.ravel()):
    ax.plot([0, 1], [0, i + 1])
    ax.set_title('panel %d' % i, fontsize=11)
fig.savefig('out.png')
'''

_ADD_AXES = '''\
import matplotlib

matplotlib.use('Agg')
import matplotlib.pyplot as plt

fig = plt.figure(figsize=(6, 4))
left = fig.add_axes([0.08, 0.55, 0.38, 0.36])
right = fig.add_axes([0.55, 0.10, 0.38, 0.36])
left.plot([0, 1], [0, 1])
right.plot([0, 1], [1, 0])
fig.savefig('out.png')
'''

_CB_AX = '''\
import matplotlib

matplotlib.use('Agg')
import matplotlib.pyplot as plt

fig, ax = plt.subplots(figsize=(5, 4))
im = ax.imshow([[1.0, 2.0], [3.0, 4.0]])
fig.colorbar(im, ax=ax)
fig.savefig('out.png')
'''

_CB_CAX = '''\
import matplotlib

matplotlib.use('Agg')
import matplotlib.pyplot as plt

fig = plt.figure(figsize=(5, 4))
ax = fig.add_axes([0.10, 0.12, 0.62, 0.78])
cax = fig.add_axes([0.80, 0.12, 0.06, 0.78])
im = ax.imshow([[1.0, 2.0], [3.0, 4.0]])
fig.colorbar(im, cax=cax)
fig.savefig('out.png')
'''

_INSET = '''\
import matplotlib

matplotlib.use('Agg')
import matplotlib.pyplot as plt

fig, ax = plt.subplots(figsize=(5, 4))
ax.plot([0, 1], [0, 1])
ins = ax.inset_axes([0.60, 0.60, 0.30, 0.30])
ins.plot([0, 1], [1, 0])
fig.savefig('out.png')
'''

_LOOP = '''\
import matplotlib

matplotlib.use('Agg')
import matplotlib.pyplot as plt

fig = plt.figure(figsize=(6, 3))
for i in range(3):
    ax = fig.add_axes([0.05 + i * 0.31, 0.12, 0.26, 0.76])
    ax.plot([0, 1], [0, i + 1])
    ax.set_title('t%d' % i, fontsize=10)
fig.savefig('out.png')
'''

_FUNC = '''\
import matplotlib

matplotlib.use('Agg')
import matplotlib.pyplot as plt


def build(offset):
    fig, axes = plt.subplots(1, 2, figsize=(6, 3))
    for i, ax in enumerate(axes):
        ax.plot([0, 1], [0, i + offset])
        ax.set_title('p%d' % i, fontsize=10)
    return fig, axes


first, _ = build(1)
second, _ = build(2)
second.savefig('out.png')
'''

_MOSAIC = '''\
import matplotlib

matplotlib.use('Agg')
import matplotlib.pyplot as plt

fig, axd = plt.subplot_mosaic([['a', 'b'], ['c', 'c']], figsize=(6, 5))
for name, ax in axd.items():
    ax.plot([0, 1], [0, 1])
    ax.set_title(name, fontsize=10)
fig.savefig('out.png')
'''

_GRIDSPEC = '''\
import matplotlib

matplotlib.use('Agg')
import matplotlib.pyplot as plt

fig = plt.figure(figsize=(6, 5))
gs = fig.add_gridspec(2, 3)
top = fig.add_subplot(gs[0, :])
left = fig.add_subplot(gs[1, 0])
right = fig.add_subplot(gs[1, 1:])
for ax in (top, left, right):
    ax.plot([0, 1], [0, 1])
fig.savefig('out.png')
'''

#: 夹具名 -> 源码。**9 个**，按任务书点名的形态一一对应。
FIXTURES = {
    'subplots_grid': _GRID,
    'add_axes_manual': _ADD_AXES,
    'colorbar_ax': _CB_AX,
    'colorbar_cax': _CB_CAX,
    'inset_axes': _INSET,
    'loop_axes': _LOOP,
    'function_built_twice': _FUNC,
    'subplot_mosaic': _MOSAIC,
    'gridspec': _GRIDSPEC,
}

#: 每个夹具"要求的新值"：把 `axes[index==0].pos` 的 x 挪 +0.01。
#: 特意留在 **4 位小数**上（写回块按 4 位小数打印，见文件头的口径 1）。
SHIFT = 0.01


def _mutate(params, shift=SHIFT, index=None, meta=None):
    """返回 (P, 期望的 pos)：在 describe 产物上挑一个字段改成新值。

    ``index`` 给了就**只改那根轴**（夹具需要指定目标时用，见 `MUTATE_INDEX`）；
    不给则按「第一根既不是 colorbar、也没锁长宽比」的轴来挑。
    """
    out = json.loads(json.dumps(params))          # 深拷贝
    target = None
    if index is not None:
        for ax in out.get('axes') or []:
            if int(ax.get('index')) == int(index):
                target = ax
                break
        assert target is not None, (
            '参数里没有 index=%r 的轴（可选 %r）'
            % (index, [a.get('index') for a in out.get('axes') or []]))

    # T10（0.1.18）注：**跳过长宽比锁定的轴**（imshow / 地图）。这类轴的位置由
    # matplotlib 按数据比例现算 —— T10 起"锁定轴不写 pos"（与 verify.compare 的
    # "锁定轴不比 pos"同口径），拿它当"要改位置"的靶子只会得到 no_change，
    # 于是"拒写 ⇒ reason=refused"这一档就验不到了（colorbar_cax 夹具的 ax 正是
    # 一个 imshow 轴）。改成挑**第一根没被锁定的轴**，验的东西不变：仍然是
    # "改一根轴的位置 ⇒ 写不进去时必须如实拒写"。
    for ax in ([] if target is not None else out.get('axes') or []):
        if ax.get('is_colorbar'):
            continue
        if ax.get('aspect_locked') or ax.get('aspect') not in (None, 'auto'):
            continue
        target = ax
        break
    if target is None and index is None:         # 兜底：退回 index==0（老夹具）
        for ax in out.get('axes') or []:
            if ax.get('index') == 0:
                target = ax
                break
    assert target is not None, '夹具里没有 index==0 的轴，没法挑目标：%r' % (
        [a.get('index') for a in out.get('axes') or []],)
    pos = list(target.get('pos') or [])
    assert len(pos) == 4, 'pos 不是四元组：%r' % (pos,)
    pos[0] = round(float(pos[0]) + shift, 4)
    target['pos'] = pos
    if meta is not None:                     # 回传"改的是哪根轴"（判据要用）
        meta['index'] = int(target.get('index') or 0)
    return out, pos


def _describe(work, script, out_name):
    out_path = os.path.join(work, out_name)
    rc, out, err = _run(work, 'describe', script, '-o', out_name, '--compact')
    assert rc == 0, ('describe 失败 rc=%d\nstdout=%s\nstderr=%s'
                     % (rc, out[-800:], err[-800:]))
    with open(out_path, encoding='utf-8') as f:
        return json.load(f)


def _apply(work, script, params_name):
    """跑一次真写回；返回 (rc, payload 或 None, stdout, stderr)。"""
    rc, out, err = _run(work, 'apply', script, '--params', params_name,
                        '--write', '--json')
    payload = None
    try:
        payload = json.loads(out)
    except ValueError:
        payload = None
    return rc, payload, out, err


def _entry(payload):
    """--json 载荷里这一次写回的那条记录（兼容单文件/多文件两种形状）。"""
    if not payload:
        return {}
    files = payload.get('files') or []
    return files[0] if files else {}


def _dump(work, name, payload):
    path = os.path.join(work, name)
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(payload, f, ensure_ascii=False, sort_keys=True, indent=2)
    return name


#: 每个夹具**预先声明的预期行为**（写在跑之前，不是事后解释）。
#:
#: 为什么必须声明：从结果上看，「正确拒写」和「该写却没写」**长得一模一样**（都是
#: applied=False）。不先声明，两种都能被当成"通过"——那这份用例就成了摆设。
#: 所以两种偏差各有一条红：声明 write 却拒写 = 该写没写；声明 refuse 却写了 =
#: 在我们认定"证不出身份"的形态上仍然动了文件（更危险）。
#:
#: `loop_axes` / `function_built_twice` 声明为 refuse 的依据（0.1.18 现状实测）：
#: 循环体里建轴、以及"函数封装建图且被调用多次"这两种形态，写回块要到运行期才知道
#: 该落在哪一次调用上 ⇒ fail-closed 拒写。**若 0.1.18 之后 engine 让它们可写了**
#: （L3 位置身份推进的方向之一），把这里改成 'write' 即可 —— 那是好消息，不是回归；
#: 反过来，若某个本该能写的形态突然拒写，这张表就是唯一能照出它的地方。
#: 需要**指定瞄准哪根轴**的夹具（其余按 `_mutate` 的默认规则挑）。
#: `colorbar_cax`：ax0 是 imshow（`aspect_locked`），T10 起锁定轴不写 pos ⇒
#: 拿它当靶子只会得到 `no_change`；改挑 cax 那根（矩形来自源码，可写）。
MUTATE_INDEX = {'colorbar_cax': 1}

EXPECT = {
    'subplots_grid': 'write',
    'add_axes_manual': 'write',
    # 0.1.18（t16 之后）：挂了 colorbar 的**宿主轴**，它的位置由 colorbar 决定
    # （`make_axes` 会重排宿主的 rect），所以"改宿主 pos"被明确拒写，人话是
    # `ax0.pos (this axis hosts a colorbar, so its position is decided by the
    # colorbar and is left as is; drag the colorbar axis instead, or use
    # --style block)` —— 这是比"照写然后被 colorbar 覆盖"更诚实的语义。
    # 两个 colorbar 夹具因此改声明为 refuse；"同一件事两种答复"（cax 那档回
    # `no_change`）已记进 `_team014/roundtrip.md` 的 F4。
    'colorbar_ax': 'refuse',
    # `cax=` 那档不一样：cax 的矩形就是源码里的字面量（`fig.add_axes([0.80, ...])`），
    # 所以**可写** —— 本夹具通过 `MUTATE_INDEX` 瞄 cax 轴（ax0 是 imshow、锁定长宽比，
    # 宿主轴的位置又由 colorbar 决定）。一句口径：**"colorbar 形态能不能写"取决于你改哪根轴**，
    # 宿主轴不行、cax 轴行；两档各留一个夹具，别用一个替代另一个。
    'colorbar_cax': 'write',
    'inset_axes': 'write',
    'loop_axes': 'refuse',
    'function_built_twice': 'refuse',
    'subplot_mosaic': 'write',
    'gridspec': 'write',
}


#: 每个夹具跑完的记录（打印成表，供 `_team014/roundtrip.md` 指证）
RESULTS = {}


# =====================================================================
# §4 主判据：往返恒等 + 幂等（每个夹具一条用例）
# =====================================================================
@pytest.mark.parametrize('name', sorted(FIXTURES))
def test_roundtrip_is_identity(work, name):
    """describe → apply --write → describe：**一次就对** + 无附带损伤 + 幂等。

    顺序刻意是"先正确、后幂等"——**错得一致也算幂等**，所以只证"第二次不变"根本证明
    不了写对了。三步各自的判据：

      ① 一次就对（保真）：写完之后 describe 读回来的值 == 我们**要求**的值（C ≈ P）；
      ② 无附带损伤：没要求的字段 == 写之前的值（A \\ P ≈ C \\ P）；
      ③ 幂等：同一份参数再写一次 ⇒ 字节不变，且（第一次真写过时）第二次必须 no_change。

    并且每一步都先过**预先声明的预期行为**（EXPECT）：声明 write 却拒写 = 该写没写；
    声明 refuse 却写了 = 在证不出身份的形态上动了文件。两种都是红。
    """
    script = name + '.py'
    _write(os.path.join(work, script), FIXTURES[name])
    declared = EXPECT[name]

    a = _describe(work, script, 'a.json')
    meta = {}
    want, new_pos = _mutate(a, index=MUTATE_INDEX.get(name), meta=meta)
    k = meta['index']                    # 被要求改的那根轴（摘 pos 时用）
    params_name = _dump(work, 'p.json', want)
    before = _read_bytes(os.path.join(work, script))

    rc, payload, out, err = _apply(work, script, params_name)
    entry = _entry(payload)
    applied = bool(entry.get('applied'))
    reason = entry.get('reason') or (payload or {}).get('error_code') or ''
    after_bytes = _read_bytes(os.path.join(work, script))

    c = _describe(work, script, 'c.json')
    record = {'applied': applied, 'reason': reason, 'rc': rc,
              'declared': declared, 'requested_pos': new_pos}

    # 0) 先核对"结果是否符合预先声明"，再谈细节
    if declared == 'write':
        assert applied, (
            '（%s）声明了"该写"，实际没写成（rc=%d reason=%r）—— 这是**该写却没写**。\n'
            'stdout 尾部：\n%s' % (name, rc, reason, out[-1200:]))
    else:
        assert not applied, (
            '（%s）声明了"应当拒写"（因为身份证不出来、要 fail-closed），实际却写了 —— '
            '那等于在我们认定不可证的形态上改用户文件。rc=%d reason=%r\n%s\n尾部：\n%s'
            % (name, rc, reason, out[-800:],
               after_bytes.decode('utf-8', 'replace')[-800:]))

    if applied:
        # ① 一次就对：要什么就是什么（这一步才是"往返恒等"的本体）
        diffs = _diff(_norm(want), _norm(c))
        assert not diffs, (
            '往返不恒等（%s）：apply 写完之后 describe 读回来的值与我们要求的**不一致**。\n'
            '要求的 axes[%d].pos = %r，读回来 = %r\n差别：\n  %s\n'
            '脚本尾部：\n%s' % (name, k, new_pos,
                                [ax.get('pos') for ax in c.get('axes') or []
                                 if int(ax.get('index')) == k],
                                '\n  '.join(diffs[:20]),
                                after_bytes.decode('utf-8', 'replace')[-1200:]))
        # ② 无附带损伤：没要求的字段必须与原来一模一样
        strip = json.loads(json.dumps(want))
        for ax in strip.get('axes') or []:
            if int(ax.get('index')) == k:
                ax.pop('pos', None)
        a_stripped = json.loads(json.dumps(a))
        for ax in a_stripped.get('axes') or []:
            if int(ax.get('index')) == k:
                ax.pop('pos', None)
        c_stripped = json.loads(json.dumps(c))
        for ax in c_stripped.get('axes') or []:
            if int(ax.get('index')) == k:
                ax.pop('pos', None)
        collateral = _diff(_norm(a_stripped), _norm(c_stripped))
        assert not collateral, (
            '往返有**附带损伤**（%s）：只要求改 axes[0].pos，别的字段却被改了：\n  %s'
            % (name, '\n  '.join(collateral[:20])))
    else:
        # 拒写分支：必须一个字节都没动，且理由说得出来（静默才是红的）
        assert after_bytes == before, (
            '（%s）apply 说"没写"，但脚本字节变了 —— 这是最坏的静默改坏图。\n'
            'rc=%d reason=%r\nstdout=%s' % (name, rc, reason, out[-800:]))
        no_damage = _diff(_norm(a), _norm(c))
        assert not no_damage, (
            '（%s）apply 没写回，但 describe 读回来的值变了（说明脚本被别的路改了）：\n  %s'
            % (name, '\n  '.join(no_damage[:20])))
        assert bool(reason) or rc != 0, (
            '（%s）apply 既没写也没说清为什么（rc=%d、payload 里没有 reason）'
            % (name, rc))

    # ③ 幂等：同一份参数再写一次 ⇒ 字节不变；第一次真写过的话第二次必须 no_change
    snapshot = after_bytes
    rc2, payload2, out2, err2 = _apply(work, script, params_name)
    entry2 = _entry(payload2)
    assert _read_bytes(os.path.join(work, script)) == snapshot, (
        '（%s）第二次 apply 改了字节 —— 幂等性破了。第二次输出：\n%s\n%s'
        % (name, out2[-800:], err2[-400:]))
    if applied:
        assert not entry2.get('applied'), (
            '（%s）第一次写过了，第二次又写了一遍（应报 no_change）：%r'
            % (name, entry2))
        assert (entry2.get('reason') or '') in ('no_change', ''), (
            '（%s）第二次 apply 的 reason 不是 no_change：%r'
            % (name, entry2.get('reason')))
    record['idempotent'] = True
    RESULTS[name] = record
    print('ROUNDTRIP | %-22s | declared=%-6s | %-8s | reason=%-12s | pos=%r'
          % (name, declared, 'applied' if applied else 'refused',
             reason or '-', new_pos))


# =====================================================================
# §4b t16（0.1.18）：**拒写要有自己的 reason**
#
# 0.1.17 之前 `reason=best_effort` 同时盖着两种结局：
#   * `applied=true`（写了一部分：add_axes 手写位置 / colorbar `cax=`）；
#   * `applied=false`（**一个字节都没写**：循环建轴、函数封装建图且被调用多次）。
# 只看 `reason` 的消费方（agent / 脚本）分不出"写了一部分"和"什么都没写" ——
# 文档那句「`best_effort` ≠ 被拒」当时是**现场反例**：它同时还意味着被拒。
#
# 这一节盯的就是"reason 能不能分辨这两种结局"，而不是"写没写"（那是 §4 主判据的事，
# 它对两种结局都断言 applied=false ⇒ 两个 reason 长得一样也照过）。
#
# 期望值一律从 `mpltweak.messages` 的具名常量 / 取值表取（**单一出处**），测试里不写
# reason 的裸字面量 —— 否则"收拢到一处"这句话在测试这边就破功了。
# =====================================================================
if SRC not in sys.path:            # 本文件只在子进程里跑 CLI，父进程这边要自己挂 src
    sys.path.insert(0, SRC)
import mpltweak.messages as _msg    # noqa: E402  （放在这里：要等上面定好 SRC）


def _reasons_in(work, fixtures):
    """跑一批夹具，返回 {夹具名: (rc, payload, entry, 写前字节, 写后字节)}。

    describe → 把 axes[0].pos 挪 SHIFT → apply --write --json，与 §4 主判据同一条链路。
    """
    out = {}
    for name in fixtures:
        assert name in FIXTURES, '没有这个夹具：%r' % (name,)
        sub = os.path.join(work, 'r_' + name)
        os.makedirs(sub, exist_ok=True)
        script = name + '.py'
        path = os.path.join(sub, script)
        _write(path, FIXTURES[name])
        a = _describe(sub, script, 'a.json')
        want, _pos = _mutate(a)
        params_name = _dump(sub, 'p.json', want)
        before = _read_bytes(path)
        rc, payload, _out, _err = _apply(sub, script, params_name)
        out[name] = (rc, payload, _entry(payload), before, _read_bytes(path))
    return out


def test_refused_has_its_own_reason(work):
    """声明为 refuse 的夹具（循环建轴 / 函数里建图多次调用）⇒ `reason=refused`。

    三条一起断言，因为"拒写"是**三件事同时成立**：
      ① `reason == 'refused'`（消费方据此分支，不再与 best_effort 混在一起）；
      ② `applied is False` + 字节一个都没动（拒写是事实，不是标签）；
      ③ `error_code == 'nothing_applied'` + `rc == 1`（机器码与退出码跟着同一个事实走）。

    反向验证（teeth，手工做一次即可，命令见 `_team014/reason_vocab.md`）：
    把 `apply._normalize_reason` 的判据去掉（退回 0.1.17 的"一律 best_effort"）⇒ 本用例红。
    """
    refuse = sorted(n for n, v in EXPECT.items() if v == 'refuse')
    if not refuse:
        # EXPECT 全变成 'write' = **engine 把这两个形态写通了**（L3 位置身份推进的方向）。
        # 那是好消息，不是回归：这条用例此时没有对象可验 ⇒ skip 并说清原因，
        # 绝不 assert 红（否则"改进"会被误报成"退步"）。
        pytest.skip('EXPECT 里已经没有 refuse 档（engine 把循环建轴/函数建图写通了？）'
                    '—— 此用例当前无对象可验')
    got = _reasons_in(work, refuse)
    for name in refuse:
        rc, payload, entry, before, after = got[name]
        assert entry.get('reason') == _msg.REASON_REFUSED, (
            '（%s）拒写的 reason 必须是 %r（独立取值），实际 %r —— '
            'best_effort 只表示"写了一部分"，不许再兼着"什么都没写"。\n'
            'entry=%r' % (name, _msg.REASON_REFUSED, entry.get('reason'), entry))
        assert entry.get('applied') is False, (
            '（%s）reason=refused 却报 applied=%r —— 两个字段自相矛盾'
            % (name, entry.get('applied')))
        assert after == before, (
            '（%s）说拒写，脚本字节却变了 —— 静默改坏图' % (name,))
        assert (payload or {}).get('error_code') == _msg.ERROR_CODE_NOTHING_APPLIED, (
            '（%s）拒写档的顶层 error_code 必须是 %r，实际 %r'
            % (name, _msg.ERROR_CODE_NOTHING_APPLIED,
               (payload or {}).get('error_code')))
        assert rc == 1, '（%s）拒写档的退出码应为 1（结果需要处理），实际 %d' % (name, rc)


def test_partial_write_keeps_best_effort(work):
    """真落了地但只落了一部分的夹具 ⇒ `applied=true` 且 reason **不是** refused。

    `add_axes([...])` 手写位置这一类：原位能改的改了、块补了其余，`applied=true` ——
    这正是 `best_effort` 该表示的意思（有一件事没做到，但**确实写了**）。
    这里只钉"它与拒写不共用取值"这一条契约；`ok` 还是 `best_effort` 由引擎按当时能证到
    什么决定（两者都是成功档，消费方按 kind 分支即可）。
    """
    name = 'add_axes_manual'
    rc, payload, entry, before, after = _reasons_in(work, [name])[name]
    assert entry.get('applied') is True, (
        '（%s）这个夹具应当真写进去（t11 实测：add_axes 手写位置可原位改），'
        '实际 applied=%r reason=%r\npayload=%r'
        % (name, entry.get('applied'), entry.get('reason'), payload))
    assert _msg.reason_kind(entry.get('reason')) == _msg.REASON_SUCCESS, (
        '（%s）部分写回属于成功档，reason 却不在成功档里：%r'
        % (name, entry.get('reason')))
    assert entry.get('reason') != _msg.REASON_REFUSED, (
        '（%s）写了却报 refused —— 那会把"写了一部分"误报成"什么都没写"' % (name,))
    assert after != before, '（%s）applied=true 但字节没变，自相矛盾' % (name,)
    assert rc == 0, '（%s）部分写回仍是成功档（rc=0），实际 %d' % (name, rc)


def test_reason_vocabulary_is_closed_and_disjoint():
    """吃 §4 已经跑完的 9 个夹具：reason 值域封闭 + 两档**互斥**。

    ① 值域封闭：出现的每个 reason 都在 `messages.REASONS` 里登记过
       （漏登记 ⇒ 消费方没有口径可依，`_failure_code` 也会退回 `apply_failed`）；
    ② 互斥：`best_effort` 绝不再与 `applied=false` 成对；`refused` 绝不与 `applied=true` 成对；
    ③ 覆盖：`EXPECT` 声明 refuse 的那些夹具，实测正好落在 `refused` 上。

    为什么蹭 §4 的记录而不是自己再跑一遍：9 个夹具再跑一遍要 20 多秒，而 §4 刚跑完，
    `RESULTS` 就在手边 —— 这条用例的职责是"判 reason 的口径"，不是"再证一次写没写"。
    单独 `-k` 跑这条（§4 没跑）时会 skip，而不是假装通过；但只要**声明 refuse 的那几个**
    有记录，本用例就照常判（§4 里别的夹具红了是 §4 的事，不该连带把这条也静音）。
    """
    if not RESULTS:
        pytest.skip('需要先跑 §4 主判据（RESULTS 空）；整份跑即可')
    declared = set(n for n, v in EXPECT.items() if v == 'refuse')
    assert declared <= set(RESULTS), (
        '声明 refuse 的夹具缺记录 %r —— §4 主判据没跑到它们，reason 口径无从判起'
        % (sorted(declared - set(RESULTS)),))
    for name in sorted(RESULTS):
        rec = RESULTS[name]
        reason = rec['reason']
        assert _msg.is_known_reason(reason), (
            '（%s）出现了没登记的 reason %r —— 新增取值必须先进 messages.REASONS'
            '（值域的唯一出处），否则文档/测试/消费方各写一份必然分叉'
            % (name, reason))
        assert _msg.reason_kind(reason) is not None, (name, reason)
        if reason == _msg.REASON_BEST_EFFORT:
            assert rec['applied'], (
                '（%s）best_effort + applied=false 又回来了 —— 这正是 t16 拆开的那种混用'
                % (name,))
        if reason == _msg.REASON_REFUSED:
            assert not rec['applied'], (
                '（%s）refused 却说写了 —— 两个字段自相矛盾' % (name,))
    refused = set(n for n, rec in RESULTS.items()
                  if rec['reason'] == _msg.REASON_REFUSED)
    assert refused == declared, (
        'refuse 档与实测 refused 对不上：EXPECT 声明 %r，实测 %r'
        % (sorted(declared), sorted(refused)))


# ---------------------------------------------------------------------
# §4c t15-F1 / t15-F2：`null` 的口径与 `fig_index` 的闸门
# ---------------------------------------------------------------------
def test_null_field_means_not_requested(work):
    """`axes[0].pos = null` ⇒ **等同该键不存在**：不写、不报错、不 pending。

    船长裁决（F6）：`null` = "未请求这个字段"，**不是**非法值。理由：最小参数文件
    （"我只想改标题字号"）是受支持的写法，其余字段留空是自然的；而 `describe` 从不产出
    null，所以 null 只可能来自人手写 —— 它表达"不关心"。早年 0.1.18 初版把 null 判
    `bad_params`，直接把 `tests/test_shapes.py` 那种"只声明改一项"的用法顶红（35+ 条）。

    这条用例同时钉住**反方向**：不许因为收紧而把 null 判红（那是刚被推翻的写法），
    也**不许**把"null 那根轴"偷偷当成"要求它挪到某处"。
    """
    name = 'subplots_grid'
    script = name + '.py'
    path = os.path.join(work, script)
    _write(path, FIXTURES[name])
    a = _describe(work, script, 'a.json')
    touched = False
    for ax in a.get('axes') or []:
        if ax.get('index') == 0:
            ax['pos'] = None                    # = 未请求（不是非法值）
            touched = True
        elif ax.get('index') == 1:
            ax['pos'] = [0.55, 0.55, 0.4, 0.4]  # 另一根轴是**正常**要求
    assert touched, '夹具里没有 index==0 的轴'
    params_name = _dump(work, 'p.json', a)
    rc, payload, out, err = _apply(work, script, params_name)
    entry = _entry(payload)
    assert entry.get('reason') != _msg.REASON_BAD_PARAMS, (
        '`pos: null` 被当成非法值了 —— 船长裁决 F6 已推翻那个口径'
        '（null = 未请求，等同键不存在）。reason=%r\npayload=%r\n输出：\n%s'
        % (entry.get('reason'), payload, out[-800:]))
    assert rc == 0, '`pos: null` 不该让整份参数失败：rc=%d\n输出：\n%s' % (rc, out[-600:])
    assert entry.get('applied') is True, (
        '另一根轴的正常改动应当照样落地（null 那根轴不参与）：%r' % (entry,))
    assert not any('pos' in str(x) and 'ax0' in str(x)
                   for x in (entry.get('pending') or [])), (
        '`pos: null` 不该进 pending（未请求 ≠ 请求了但写不了）：%r'
        % (entry.get('pending'),))


def test_params_without_any_field_says_so(work):
    """整份参数**一个有效字段都没有** ⇒ 明确说"没有任何要改的字段"，不许说"参数与原代码一致"。

    这是 F6 裁决的第三行。`no_change` 的文案是「参数与原代码一致，无需改动」—— 它意味着
    "我逐项比过了，你要的值就是现值"；而空壳参数（只写了 `version`/`script`/`axes[].index`）
    根本**没有要求**，说成前者就是谎报（用户会以为工具验过了）。
    两者都不写盘、都 rc=0，区别**只在** reason 与人读那句话。
    """
    name = 'subplots_grid'
    script = name + '.py'
    path = os.path.join(work, script)
    _write(path, FIXTURES[name])
    a = _describe(work, script, 'a.json')
    shell = {'version': a.get('version'), 'script': a.get('script'),
             'fig_index': a.get('fig_index'), 'n_figs': a.get('n_figs'),
             'axes': [{'index': ax.get('index')} for ax in a.get('axes') or []]}
    params_name = _dump(work, 'empty.json', shell)
    before = _read_bytes(path)
    rc, payload, out, err = _apply(work, script, params_name)
    entry = _entry(payload)
    blob = out + err
    assert entry.get('reason') == _msg.REASON_NO_FIELDS, (
        '空壳参数（没有任何要改的字段）必须报 %r，实际 %r —— 报 no_change 等于谎称'
        '"我比过了、值一样"\npayload=%r\n输出：\n%s'
        % (_msg.REASON_NO_FIELDS, entry.get('reason'), payload, blob[-800:]))
    assert _read_bytes(path) == before, '空壳参数不该动文件'
    assert rc == 0, '空壳参数不是错误（rc=0），实际 %d' % (rc,)
    assert ('no changes at all' in blob) or ('没有任何要改的字段' in blob), (
        '必须有一句"这份参数没有任何要改的字段"，实际输出：\n%s' % (blob[-900:],))
    assert 'already match the code' not in blob and '参数与原代码一致' not in blob, (
        '不许复用 no_change 那句（"参数与原代码一致"）—— 那是"比过了"，这是"没要求"：\n%s'
        % (blob[-900:],))


def test_non_integer_fig_index_is_bad_params(work):
    """`fig_index` 不是整数 ⇒ `bad_params` + 点名收到的值；不猜 0、不报 `no_fig`。

    0.1.17 实测（改前）：`1.5` / `'0'` / `null` / `true` 四种全部 `rc=0` + 写盘
    （单图时静默退回 0 照写；某些形态还会错报 `no_fig`，让用户以为脚本里没有这张图）。
    这里取最典型的两种（浮点、字符串数字）—— 其余两种的原始读数在
    `_team014/reason_vocab.md` §2 的探针输出里。
    """
    name = 'subplots_grid'
    for value in (1.5, '0'):
        sub = os.path.join(work, 'k_%s' % (str(value).replace('.', '_')))
        os.makedirs(sub, exist_ok=True)
        script = name + '.py'
        path = os.path.join(sub, script)
        _write(path, FIXTURES[name])
        a = _describe(sub, script, 'a.json')
        a['fig_index'] = value
        for ax in a.get('axes') or []:
            if ax.get('index') == 0:
                ax['pos'] = [0.2, 0.2, 0.5, 0.5]
        params_name = _dump(sub, 'p.json', a)
        before = _read_bytes(path)
        rc, payload, out, err = _apply(sub, script, params_name)
        entry = _entry(payload)
        assert entry.get('reason') == _msg.REASON_BAD_PARAMS, (
            'fig_index=%r 不是整数，却被接受并照写：reason=%r applied=%r\n'
            'payload=%r\nstdout 尾部：\n%s'
            % (value, entry.get('reason'), entry.get('applied'), payload, out[-600:]))
        assert repr(value) in (entry.get('err') or ''), (
            'bad_params 必须**点名收到的值**（%r），实际 err=%r'
            % (repr(value), entry.get('err')))
        assert rc == 2, '（fig_index=%r）rc 应为 2，实际 %d' % (value, rc)
        assert _read_bytes(path) == before, (
            '（fig_index=%r）参数非法却写了盘' % (value,))


# =====================================================================
# §4d 口径：**消息键名不许漏到用户眼前**（与 t9 修过的 `%(v)s` 同一族）
#
# `messages.t()` 对**没登记**的键是"原样返回键名"（不抛，见 messages.t 的容错契约）——
# 所以只要有一处引用了不存在的键，用户终端上就会蹦出 `wb_aspect_locked_pos` 这种裸键名：
# 既看不懂，也**不会让任何既有测试变红**。
#
# 静态那一半归 `tests/test_msg_key_audit.py`（AST 扫 `t('键')` 的字面量实参）。这条盯的是
# **运行期真正打出来的字**：只有"跑一次真命令、把 stdout+stderr 全抓下来"才盖得住
# "键登记了但名字拼错""前缀族写错""消息被拼进别的模板"这几种。
#
# 为什么正反两条断言都要：只查"没有裸键名"的话，**把整条提示删掉**也能过 ——
# 那是比漏键更坏的"修法"（该说的话不说了）。所以另一半必须**正向**确认人话在场。
# =====================================================================
#: 裸消息键名的形状：`<族>_<小写词>`，族取自 messages.py 里真实用到的前缀。
#: 刻意限定 `\b` + 至少 4 个字符的下划线段，避免误伤 `--snippet` / `apply.lock` 这类词。
_BARE_MSG_KEY = re.compile(r'\b(?:wb|chk|cli|err|apply|verify|msg|ui)_[a-z][a-z0-9_]{3,}\b')

#: 人读输出里**允许**出现的机器值（它们是 `files[].reason` / `error_code` 的取值，
#: 有意让人看见，不是键名）。命中白名单的字符串先摘掉，再查裸键名。
_HUMAN_OK_TOKENS = ('no_change', 'best_effort', 'bad_params', 'layout_conflict',
                    'no_fig', 'no_axes', 'stale_params', 'writeback_failed',
                    'external_modified', 'nothing_applied', 'apply_failed')


def _human_run(work, script, params_name, *flags):
    """跑一次**不带 `--json`** 的 apply，返回 (rc, stdout+stderr 全文)。

    人读输出才查裸键名：`--json` 载荷里全是机器键，混在一起查没有意义。
    """
    rc, out, err = _run(work, 'apply', script, '--params', params_name,
                        '--write', *flags)
    return rc, out + err


def _bare_keys_in(blob):
    """从一段人读输出里找出**裸消息键名**（先摘掉合法的机器值）。"""
    for _tok in _HUMAN_OK_TOKENS:
        blob = blob.replace(_tok, ' ')
    return sorted(set(_BARE_MSG_KEY.findall(blob)))


def test_no_bare_message_key_leaks_on_refused(work):
    """拒写档的人读输出：**不出现裸键名**，且必须有一段能看懂的人话。

    正向那一半盯的是本轮新加的 `refused_note`（"整份拒写，不是改了一部分"）——
    它是 `refused` 这个新 reason 唯一的人读解释，把它删掉就等于让用户只看到
    `未写回[refused]` 四个字。
    """
    name = 'loop_axes'
    script = name + '.py'
    _write(os.path.join(work, script), FIXTURES[name])
    a = _describe(work, script, 'a.json')
    want, _pos = _mutate(a)
    params_name = _dump(work, 'p.json', want)
    rc, blob = _human_run(work, script, params_name)
    leaks = _bare_keys_in(blob)
    assert not leaks, (
        '人读输出里出现了裸消息键名 %r —— 那是 `messages.t()` 找不到键时"原样返回键名"'
        '的后果（用户看到的应该是人话）。\n输出：\n%s' % (leaks, blob[-1500:]))
    assert 'the whole write was refused' in blob, (
        '拒写档必须有一句"整份拒写"的解释（%r），实际输出：\n%s'
        % ('refused_note', blob[-1500:]))
    assert rc == 1, '拒写档 rc 应为 1，实际 %d' % (rc,)


#: t16 附带：aspect 锁定的轴（`imshow` / `set_aspect('equal')`）拖位置 ⇒
#: **不写 + 如实说清"长宽比锁了，写位置不生效"**（engine 的 `wb_aspect_locked_pos`）。
#: 这条先按 xfail 挂着：文案键已登记（messages.py），engine 侧接上就自动转绿，
#: 届时把装饰器删掉即可（`strict=False`，转绿时不会假红）。
_ASPECT_LOCKED_SCRIPT = '''\
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

fig, (ax0, ax1) = plt.subplots(1, 2, figsize=(6.0, 3.0))
ax0.imshow(np.arange(16).reshape(4, 4))
ax0.set_aspect("equal")
ax1.plot([0.0, 1.0], [0.0, 1.0])
fig.savefig("out.png")
print("ok")
'''


@pytest.mark.xfail(strict=False, reason='engine t10 尚未接上 wb_aspect_locked_pos（0.1.18）')
def test_aspect_locked_pos_says_why_nothing_happened(work):
    """aspect 锁定的轴改位置 ⇒ 输出必须解释"为什么没生效"（不是沉默地跳过）。

    这条同时是"合法但不生效不许静默"那条口径的运行期守卫：
      * 正向：出现"长宽比锁定 / aspect ratio"这句人话（英文档下 matches `aspect ratio`）；
      * 反向：不出现裸键名（见 §4d 的说明）。
    位置**写不进去是正常的**（matplotlib 会按数据比例重算）—— 所以这里不断言 rc，
    只断言"用户被告知了原因"。
    """
    script = 'aspect_locked.py'
    _write(os.path.join(work, script), _ASPECT_LOCKED_SCRIPT)
    a = _describe(work, script, 'a.json')
    # 只请求"把这根 imshow 轴挪个位置"，别的字段一概不动（最小参数：别拖出别的拒写）
    minimal = {'version': a.get('version'), 'script': a.get('script'),
               'fig_index': a.get('fig_index'), 'n_figs': a.get('n_figs'),
               'axes': [{'index': 0, 'pos': [0.12, 0.12, 0.5, 0.5]}]}
    params_name = _dump(work, 'p.json', minimal)
    rc, blob = _human_run(work, script, params_name)
    leaks = _bare_keys_in(blob)
    assert not leaks, (
        'aspect 锁定那条提示漏了裸键名 %r\n输出：\n%s' % (leaks, blob[-1500:]))
    low = blob.lower()
    assert ('aspect ratio' in low) or ('长宽比' in blob), (
        '轴的长宽比锁着、位置写不进去时，必须说明原因（`wb_aspect_locked_pos`），'
        '否则用户只会看到"没写"而不知道是 aspect 干的。rc=%d\n输出：\n%s'
        % (rc, blob[-1500:]))


# =====================================================================
# §5 有牙：把写回块里的数值改动 1e-3 ⇒ 往返断言必须红
# =====================================================================
_POS_IN_BLOCK = re.compile(r'set_position\(\[([-0-9.eE,\s]+)\]\)')


def test_roundtrip_assertion_bites_when_block_is_off_by_1e3(work):
    """反向验证：块里的数字被人为改动 1e-3 ⇒ 我们的比较器必须当场看见。

    只测正向的往返断言会退化成"永远绿"：写回引擎坏了、块写歪了，只要 describe
    与 apply 一起坏，正向断言照样过。这里模拟"块被写坏 1e-3"，比较器**必须**报差异。
    """
    name = 'subplots_grid'
    script = name + '.py'
    _write(os.path.join(work, script), FIXTURES[name])
    a = _describe(work, script, 'a.json')
    want, new_pos = _mutate(a)
    params_name = _dump(work, 'p.json', want)
    rc, payload, out, err = _apply(work, script, params_name)
    assert _entry(payload).get('applied'), (
        '这个夹具本该写得进去（反向验证要在"能写"的形态上做）: rc=%d\n%s\n%s'
        % (rc, out[-800:], err[-400:]))

    # 正向先绿一遍（否则下面的"红"说明不了问题）
    c = _describe(work, script, 'c.json')
    assert not _diff(_norm(want), _norm(c)), '正向都不过，先修正向'

    # 人为把块里第一个数字改 +1e-3（模拟一次"写歪了"的写回）
    path = os.path.join(work, script)
    text = _read_bytes(path).decode('utf-8')
    m = _POS_IN_BLOCK.search(text)
    assert m, '块里没找到 set_position([...])，反向验证无从下手：\n%s' % text[-800:]
    nums = [float(x) for x in m.group(1).split(',')]
    nums[0] = round(nums[0] + 1e-3, 6)
    bumped = 'set_position([%s])' % ', '.join('%.4f' % x for x in nums)
    _write(path, text[:m.start()] + bumped + text[m.end():])

    d = _describe(work, script, 'd.json')
    diffs = _diff(_norm(want), _norm(d))
    assert diffs, (
        '把块里的数值改了 1e-3，往返断言却是绿的 —— 这条判据没牙（它分不出 1e-3 的偏差）')
    assert any('pos' in x for x in diffs), (
        '报红了但没指出是 pos 变了：%r' % (diffs[:5],))
    got = [ax.get('pos') for ax in d.get('axes') or [] if ax.get('index') == 0]
    assert got and abs(float(got[0][0]) - float(new_pos[0]) - 1e-3) < 1e-9, (
        '读回来的值不是"要求值 + 1e-3"，说明反向改的不是同一处：要 %r 读回 %r'
        % (new_pos, got))
    print('TEETH | 块里 +1e-3 ⇒ 比较器报 %d 处差异（首条：%s）'
          % (len(diffs), diffs[0]))


# =====================================================================
# §7 判据自身的两条护栏：容差怎么组合、豁免清单是不是闭环
# =====================================================================
def test_tolerance_is_rtol_plus_atol():
    """容差必须是 **rtol + atol 组合**，且两条都在起作用（各有反例）。

    纯相对容差（`abs(a-b) <= rtol*max(|a|,|b|)`）在**近零**上两种翻车：
      * 假红：`0.0` 与 `1e-15` 的相对误差算不出来（或无穷大）⇒ 浮点噪声被当成 bug；
      * 假绿：把容差写成"相对 max"时，两个都是 0 附近的量会被放大到能盖住真实偏差。
    纯绝对容差则在**大数**上失效（0.6 与 0.600001 的 1e-6 偏差在大图坐标下不算事，
    但固定 atol 无法表达这个意思）。所以两条一起给，且这里**逐条钉住它们各自管什么**。
    """
    assert REL_TOL == 1e-9 and ABS_TOL == 1e-12, (
        '容差口径被改了（REL_TOL=%r / ABS_TOL=%r）：改它要同时改本用例的理由'
        % (REL_TOL, ABS_TOL))
    # ① 相对容差管"大数上的比例噪声"
    assert _same_num(1.0, 1.0 + 5e-13) is True
    assert _same_num(0.6, 0.6 + 1e-12) is True
    assert _same_num(0.6, 0.6 + 1e-6) is False, '1e-6 的真偏差不许被容差吃掉'
    # ② 绝对容差管"0 附近的浮点噪声"：这两条**只有 atol 在时**才是 True
    assert _same_num(0.0, 5e-13) is True, (
        '0 与 5e-13 被判不等 —— 说明绝对容差没起作用（纯相对容差在 0 上必然红）')
    assert _same_num(0.0, 9e-13) is True
    assert _same_num(0.0, 1e-11) is False, 'atol 不许变成"0 附近一律相等"的挡箭牌'
    # ③ 队长点名的那个例子：0.001 -> 0.002（相对误差 100%）必须判不等
    assert _same_num(0.001, 0.002) is False, (
        '0.001 与 0.002 被判相等 —— 容差被放大了，往返断言会漏掉真实偏差')
    # ④ 反向：把 atol 拿掉，近零那条必须**变红**（证明 atol 不是摆设）
    assert _same_num(0.0, 5e-13, abs_=0.0) is False, (
        'abs_=0 时 0 与 5e-13 仍判相等 —— 说明这个比较器根本没在用 rtol，判据是假的')


def test_exemption_list_is_explicit_and_closed():
    """豁免清单必须**闭环**：每个被丢的字段都有理由，且没有字段被悄悄丢掉。

    三条断言分别防三件事：
      ① `EXEMPT_KEYS` / `ORDER_INSENSITIVE` 的每一项**都带非空理由**（不许空话）；
      ② 拿一份**真的 describe 产物**核对：顶层 / 单轴 / 单图元的字段全部落在
         `KNOWN_*` 里 —— describe 新增字段时会红，逼人来决定"比较它还是豁免它"；
      ③ 反向验证"没有模式匹配后门"：塞一个不在清单里的时间戳样式字段
         （`generated_at`）⇒ `_norm` **不许**把它丢掉，`_diff` 必须看见它。
    """
    for table_name, table in (('EXEMPT_KEYS', EXEMPT_KEYS),
                              ('ORDER_INSENSITIVE', ORDER_INSENSITIVE)):
        for key, why in table.items():
            assert isinstance(why, str) and len(why.strip()) >= 12, (
                '%s[%r] 没写清理由（这一栏是给人看的，不是装饰）：%r'
                % (table_name, key, why))

    # 预期表必须与夹具表**一一对应**：未声明预期的组合不得算通过（否则"没写成"会被
    # 当成"本来就该拒写"混过去），声明值也只许是这两档。
    assert set(EXPECT) == set(FIXTURES), (
        'EXPECT 与 FIXTURES 不是一一对应：多 %r / 少 %r'
        % (sorted(set(EXPECT) - set(FIXTURES)),
           sorted(set(FIXTURES) - set(EXPECT))))
    assert all(v in ('write', 'refuse') for v in EXPECT.values()), (
        'EXPECT 里出现了未定义档位：%r' % (EXPECT,))

    # 用真产物核对字段全集（挑一个多轴 + 有 colorbar 的形态，覆盖最多的键）
    d = _scratch_dir()
    try:
        _write(os.path.join(d, 'cb.py'), FIXTURES['colorbar_cax'])
        real = _describe(d, 'cb.py', 'a.json')
    finally:
        shutil.rmtree(d, ignore_errors=True)
    unknown = sorted(set(real) - KNOWN_KEYS - set(EXEMPT_KEYS))
    assert not unknown, (
        'describe 产出了没登记的顶层字段 %r —— 请决定：比较它（通常如此）还是加进 '
        'EXEMPT_KEYS 并写理由' % (unknown,))
    for ax in real.get('axes') or []:
        bad = sorted(set(ax) - KNOWN_AXIS_KEYS)
        assert not bad, '单轴字段没登记：%r（轴 index=%r）' % (bad, ax.get('index'))
        for line in ax.get('lines') or []:
            bad = sorted(set(line) - KNOWN_LINE_KEYS)
            assert not bad, '图元字段没登记：%r' % (bad,)

    # 反向：不在清单里的字段不许被 `_norm` 吞掉
    probe = {'figsize_in': [6.0, 5.0], 'generated_at': '2026-10-25 12:00:00',
             'axes': [{'index': 0, 'pos': [0.1, 0.1, 0.2, 0.2]}]}
    normed = _norm(probe)
    assert 'generated_at' in normed, (
        '`_norm` 把一个没在 EXEMPT_KEYS 里登记的字段丢掉了 —— 豁免必须是白名单，'
        '不许用模式匹配（否则将来加个时间戳字段就静默不验了）')
    assert _diff(_norm(probe), _norm(dict(probe, generated_at='2026-10-25 13:00:00'))), (
        '同一份参数里 `generated_at` 不同，比较器却报"相等" —— 说明它被静默忽略了')


# =====================================================================
# §6 附带判据：参数与当前脚本"已经一致"时，apply 一个字节都不许动
# =====================================================================
def test_apply_with_unchanged_params_writes_nothing(work):
    """把 describe 的产物**原样**喂回去 ⇒ 应当 no_change、脚本字节不变。

    这一条是 §4 的对照：它保证"没要改什么就不许动文件"（否则每次 apply 都会在文件尾部
    多插一个带时间戳的块 —— 那正是 diff 脏的来源）。
    """
    name = 'add_axes_manual'
    script = name + '.py'
    _write(os.path.join(work, script), FIXTURES[name])
    a = _describe(work, script, 'a.json')
    params_name = _dump(work, 'same.json', a)
    before = _read_bytes(os.path.join(work, script))
    rc, payload, out, err = _apply(work, script, params_name)
    after = _read_bytes(os.path.join(work, script))
    entry = _entry(payload)
    assert after == before, (
        '参数与脚本已经一致，apply 却改了文件（diff 脏的来源）：rc=%d\n'
        '尾部：\n%s' % (rc, after.decode('utf-8', 'replace')[-1200:]))
    assert not entry.get('applied'), '已经一致却报 applied=True：%r' % (entry,)
    assert (entry.get('reason') or '') in ('no_change', ''), (
        'reason 不是 no_change：%r（rc=%d）' % (entry.get('reason'), rc))


# ----------------------------------------------------------------------
# 脚本入口：`python tests/test_roundtrip.py`（直接跑会打印每夹具的往返结果表）
# ----------------------------------------------------------------------
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
