# -*- coding: utf-8 -*-
"""tests/test_xreview_regressions.py —— t19 独立审查的 9 条发现固化成「失败优先」回归网（T22）

来源：`_team014/xreview.md`（t19 跨模型独立审查，发现 F1..F9）。
每条发现在这里变成一条**现在就红、修好后自动转绿**的用例：

* pytest 档：`@pytest.mark.xfail(strict=False, reason=...)` 挂着 ⇒ 套件全绿 = 该问题仍在；
  修好后会以 XPASS 冒出来（不红、但看得见）—— 那把 `strict` 翻成 True 就是"钉死"那一步。
* 脚本档：`python tests/test_xreview_regressions.py`（`tests/test_suites.py` 的调用约定）
  逐条跑、逐条打印 `XFAIL | ...` / `PASS | ...`，最后打印 `ALL PASS`（退出码 0）。

## 三条纪律（与 t19 报告的判据同源；违反其中一条，这条用例就不算证据）

1. **禁止手搭 actual 结构**。所有"实测"必须由真采集器给出 —— `verify.collect_isolated()`
   （子进程跑真脚本）或真 CLI `apply --write` 之后的重跑状态。t19 F2 那处死代码正是被
   "手搭 actual" 掩盖的：采集侧键名是 `_cmap`、比对侧读 `'cmap'`；手搭的字典两边都叫
   `cmap`，于是"看起来在比、其实永远比不到"。
2. **期望侧必须是真参数的形状**。参数一律由 `describe._to_params(真 state, script)` 产出，
   再改**用户会改的那一个字段**（t19 探针里手写 JSON 属"用户输入"，允许；但能用 describe
   产物当起点就别手写）。
3. **判据写"期望行为"，不写"现状"**。断言的是修好之后必须成立的事，所以现在红是设计。
   每条 docstring 写明：F 编号 / 当前现象 / 期望行为 / **修好后要做的动作**。

## 跑法

    python tests/test_xreview_regressions.py                      # 脚本档（套件入口）
    python -m pytest -q tests/test_xreview_regressions.py         # pytest 档（看 xfail）
    python tests/test_xreview_regressions.py test_f2_...          # 只跑一条

默认档 9 条全跑约 40 秒（每条 1~4 个真子进程：采集 / apply / 重跑）。
"""
import copy
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / 'src'
PY = sys.executable

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import mpltweak.describe as describe      # noqa: E402
import mpltweak.verify as verify          # noqa: E402
import mpltweak.writeback as writeback    # noqa: E402

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(errors='replace')
    except (AttributeError, ValueError, OSError):
        pass


# ---------------------------------------------------------------- 夹具源码

# F1 / F6：`set_xlim` 在死分支里 —— 参数要的 xlim 落不了地（原位改那个数字 = 改死代码）
_FIG_DEAD_XLIM = '''\
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

fig, ax = plt.subplots()
ax.plot([0, 1], [0, 1])
if False:
    ax.set_xlim(0, 9)
fig.savefig('o.png')
'''

# F2 / F3 / F7：imshow 轴（aspect 锁定 + 有 mappable；vmin/vmax 写在源码里）
_FIG_IMSHOW = '''\
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

fig, ax = plt.subplots()
ax.imshow(np.arange(9).reshape(3, 3), cmap='viridis', vmin=2, vmax=7)
fig.savefig('o.png')
'''

# F4：图例带参数里**没建模**的属性（ncol / title / frameon）
_FIG_LEGEND = '''\
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

fig, ax = plt.subplots()
ax.plot([0, 1], [0, 1], label='a')
ax.plot([1, 0], [0, 1], label='b')
ax.legend(ncol=2, title='组别', frameon=False, fontsize=9)
fig.savefig('o.png')
'''

# F5 / F9：源码本来就是"只开 x 向网格 + log base=2 + 固定 xlim"
_FIG_GRID_SCALE = '''\
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

fig, ax = plt.subplots()
ax.plot([1, 2, 4, 8, 16], [1, 2, 3, 4, 5])
ax.grid(True, axis='x')
ax.set_xscale('log', base=2)
ax.set_xlim(1, 16)
fig.savefig('o.png')
'''

# F8：图级图例（`fig.legend(...)`，与 ax.legend 不是同一个 store）
_FIG_FIGLEGEND = '''\
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

fig, ax = plt.subplots()
ax.plot([0, 1], [0, 1], label='a')
fig.legend(loc='upper right')
fig.savefig('o.png')
'''


# ---------------------------------------------------------------- 基础设施

def _env():
    """子进程环境：Agg + 中文 + 保证 dev 检出（没 pip install -e）也能 import 本包。"""
    e = dict(os.environ)
    e['MPLBACKEND'] = 'Agg'
    e['MPLTWEAK_LANG'] = 'zh'
    e['PYTHONUTF8'] = '1'
    parts = [p for p in (e.get('PYTHONPATH') or '').split(os.pathsep) if p]
    if str(SRC) not in parts:
        parts.insert(0, str(SRC))
    e['PYTHONPATH'] = os.pathsep.join(parts)
    return e


def _run(cwd, *args, **kw):
    """跑一次真 CLI（`python -m mpltweak.cli ...`），返回 (rc, 输出文本)。"""
    timeout = kw.pop('timeout', 180)
    r = subprocess.run([PY, '-m', 'mpltweak.cli', *args], cwd=str(cwd),
                       env=_env(), capture_output=True, timeout=timeout)
    return r.returncode, (r.stdout + r.stderr).decode('utf-8', 'replace')


def _write(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode('utf-8'))


def _write_json(path, obj):
    _write(path, json.dumps(obj, ensure_ascii=False, indent=2))


def _read(path):
    return Path(path).read_bytes().decode('utf-8')


def _load_json(text):
    """从 CLI 输出里抠出 JSON 载荷。

    `--json` 的载荷后面可能还跟着人读的一行（"图已更新…"之类），所以用 `raw_decode`
    只吃掉第一个完整 JSON 值，不要求整段输出都是 JSON。
    """
    dec = json.JSONDecoder()
    i = text.find('{')
    while i >= 0:
        try:
            obj, _end = dec.raw_decode(text[i:])
            return obj
        except ValueError:
            i = text.find('{', i + 1)
    raise AssertionError('CLI 输出里找不到 JSON 载荷：\n' + text[-2500:])


def _entry(payload):
    """`--json` 载荷里的单文件条目（apply 一次只跑一个脚本）。"""
    files = payload.get('files') or []
    assert files, 'JSON 载荷里没有 files 条目：%r' % (payload,)
    return files[0]


_STATE_CACHE = {}


def _real_state(script):
    """真采集：子进程跑真脚本，拿 verify 的实测状态（唯一允许的 actual 来源）。

    按**源码文本**做缓存（同一份代码只跑一次；改写过的源码自然换键）。
    """
    text = _read(script)
    key = hashlib.sha256(text.encode('utf-8')).hexdigest()
    if key not in _STATE_CACHE:
        state, err = verify.collect_isolated(str(script))
        assert state is not None, '采集实测状态失败（%s）：%s' % (script, err)
        _STATE_CACHE[key] = state
    return copy.deepcopy(_STATE_CACHE[key])


def _params_of(script):
    """真参数：采集真 state → describe 白名单 → 用户看到的那个参数 JSON。"""
    state = _real_state(script)
    params = describe._to_params(copy.deepcopy(state), str(script))
    return params, state


def _axis(state, index=0):
    for it in state.get('axes') or []:
        if it.get('index') == index:
            return it
    return None


def _close(got, want, rtol=1e-6, atol=1e-9):
    try:
        g = [float(v) for v in got]
        w = [float(v) for v in want]
    except (TypeError, ValueError):
        return False
    return len(g) == len(w) and all(abs(a - b) <= atol + rtol * abs(b)
                                    for a, b in zip(g, w))


_DUMP_LEGEND = '''\
import json, runpy, sys
runpy.run_path(sys.argv[1], run_name='__main__')
import matplotlib.pyplot as plt
fig = plt.gcf()
out = []
for ax in fig.axes:
    lg = ax.get_legend()
    if lg is not None:
        try:
            ncol = int(getattr(lg, '_ncols', -1))
        except Exception:
            ncol = -1
        out.append({'ncol': ncol, 'title': lg.get_title().get_text(),
                    'frameon': bool(lg.get_frame_on())})
json.dump(out[:1] or [None], sys.stdout)
'''


def _legend_props_after(script):
    """真重跑（独立子进程）读图上的真 legend 对象 —— 不手搭、不猜。"""
    script = Path(script)
    dump = script.parent / '_dump_legend.py'
    _write(dump, _DUMP_LEGEND)
    r = subprocess.run([PY, str(dump), script.name], cwd=str(script.parent),
                       env=_env(), capture_output=True, timeout=180)
    text = (r.stdout + r.stderr).decode('utf-8', 'replace')
    i = text.find('[')
    assert i >= 0, '重跑 dump 没给出 JSON：\n' + text[-2000:]
    props = json.loads(text[i:])
    return props[0] if props else None


def _find_legend_like(obj, path=''):
    """在状态结构里递归找"图例形状"的字典（键名不限，只看 labels/n_handles/loc）。"""
    hits = []
    if isinstance(obj, dict):
        if isinstance(obj.get('labels'), list) and (
                'loc' in obj or 'n_handles' in obj or 'fontsize' in obj):
            hits.append((path or '<root>', obj))
        for k, v in obj.items():
            hits.extend(_find_legend_like(v, '%s.%s' % (path, k)))
    elif isinstance(obj, list):
        for n, v in enumerate(obj):
            hits.extend(_find_legend_like(v, '%s[%d]' % (path, n)))
    return hits


# ---------------------------------------------------------------- F1..F9 用例体
# 说明：用例体只写**判据**；F 编号 / 当前现象 / 期望行为 / 修好后动作写在下面 pytest
# 包装函数的 docstring 里（pytest 档能直接看到）。

def _case_f1(work):
    """F1：参数要 xlim=[0,5]，实测那根轴压根没有 xlim ⇒ 必须不许报"通过"。"""
    script = work / 'fig.py'
    _write(script, _FIG_DEAD_XLIM)
    params, _ = _params_of(script)
    # 只改 xlim（不带 pos）：带上 pos 会让原位路径因为"位置写不进去"退块，块里的
    # set_xlim 落在死分支**之后**，xlim 反倒落地了 —— 那就绕开了 F1 要钉的那条路。
    params['axes'][0]['xlim'] = [0.0, 5.0]
    _write_json(work / 'p.json', params)

    rc, out = _run(work, 'apply', 'fig.py', '--params', 'p.json', '--write', '--json')
    entry = _entry(_load_json(out))
    state = _real_state(script)
    got = (_axis(state) or {}).get('xlim')

    landed = got is not None and _close(got, [0.0, 5.0])
    claims_ok = (rc == 0 and bool(entry.get('applied'))
                 and entry.get('verified') is True)
    assert landed or not claims_ok, (
        '参数要求 xlim=[0,5]，重跑实测拿到 %r（None = 那根轴还是 autoscale）——'
        '既没落地，CLI 又报了 applied=%r / verified=%r（rc=%s，reason=%r）。\n'
        'compare() 在"实测缺这个键"时是 continue（不判失败、也不进 unjudged），'
        '所以"改进了死分支没生效"这件事对用户完全不可见。\n'
        '---- CLI 输出尾巴 ----\n%s'
        % (got, entry.get('applied'), entry.get('verified'), rc,
           entry.get('reason'), out[-1500:]))


def _case_f2(work):
    """F2：参数把 cmap 改成 magma，实测 viridis ⇒ compare 必须报 cmap 不一致。"""
    script = work / 'fig.py'
    _write(script, _FIG_IMSHOW)
    params, state = _params_of(script)
    assert 'cmap' in (params.get('axes') or [{}])[0], (
        '前置条件不成立：describe 根本没导出 cmap（采集侧没给 _cmap）——'
        '先看 _FIG_IMSHOW 是不是没 imshow 成功')
    params['axes'][0]['cmap'] = 'magma'

    bad = verify.compare(params['axes'], state['axes'])
    assert any('cmap' in b for b in bad), (
        "参数要求 cmap='magma'，实测 'viridis'，compare() 一条不一致都没报：%r\n"
        '根因：compare 读 e.get("cmap")/a.get("cmap")，而实测状态的键名是 _cmap'
        '（verify._state → _artist_state 写的是下划线名，describe 导出时才映射成 cmap）。'
        % (bad,))


def _case_f3(work):
    """F3：等比例锁定轴的 pos 被改到别处 ⇒ compare 必须报 pos 不一致。"""
    script = work / 'fig.py'
    _write(script, _FIG_IMSHOW)
    params, state = _params_of(script)
    ax0 = params['axes'][0]
    assert ax0.get('aspect_locked') is True, (
        '前置条件不成立：imshow 轴本应 aspect_locked=True，实际 %r' % (ax0,))
    ax0['pos'] = [0.9, 0.9, 0.05, 0.05]

    bad = verify.compare(params['axes'], state['axes'])
    assert any('pos' in b for b in bad), (
        '参数把轴挪到 [0.9,0.9,0.05,0.05]，实测仍在原处，compare() 却没报 pos：%r\n'
        'compare 在 aspect_locked（或实测 aspect 非 auto）时**整段跳过 pos**，'
        '而写回侧 `_pv_live_field` 是比 pos 的 ⇒ "写回敢写、验证不敢判"。'
        % (bad,))


def _case_f4(work):
    """F4：改写 legend(loc/fontsize) 时，参数里没建模的属性（ncol/title/frameon）不许丢。"""
    script = work / 'fig.py'
    _write(script, _FIG_LEGEND)
    params, _ = _params_of(script)
    # 用户在 GUI 里把图例挪到右上角、字号提到 11（ncol/title/frameon 他没动）
    params['axes'][0]['legend'] = {'loc': 'upper right', 'fontsize': 11}
    _write_json(work / 'p.json', params)

    rc, out = _run(work, 'apply', 'fig.py', '--params', 'p.json', '--write',
                   '--style', 'block', '--json')
    entry = _entry(_load_json(out))
    assert rc == 0 and entry.get('applied'), (
        '前置条件不成立：这次应当写成功（rc=%s, reason=%r）：\n%s'
        % (rc, entry.get('reason'), out[-2000:]))

    props = _legend_props_after(script)
    assert props is not None, '写回并重跑后图上没有图例了（写坏）'
    assert props['title'] == '组别' and props['frameon'] is False, (
        '写回用 `%s.legend(loc=..., fontsize=...)\n` 整条重建图例，把参数里没建模的属性'
        '一起丢了：实测 %r（原来是 ncol=2 / title="组别" / frameon=False）。\n'
        'block 里那一行是整条重建 ⇒ 任何"参数没覆盖的属性"都被重置成默认值。'
        % ('ax', props))


def _case_f5(work):
    """F5：用户没改的字段（grid/xscale）不许因为跑在 --no-semantic 档就写进块。"""
    script = work / 'fig.py'
    _write(script, _FIG_GRID_SCALE)
    params, _ = _params_of(script)
    params['axes'][0]['title_fontsize'] = 14.0     # 用户只动了这一个字段
    _write_json(work / 'p.json', params)

    rc, out = _run(work, 'apply', 'fig.py', '--params', 'p.json', '--write',
                   '--style', 'block', '--no-semantic', '--json')
    entry = _entry(_load_json(out))
    assert rc == 0 and entry.get('applied'), (
        '前置条件不成立：这次应当写成功（rc=%s, reason=%r）：\n%s'
        % (rc, entry.get('reason'), out[-2000:]))

    text = _read(script)
    assert text.count('ax.grid(') == 1 and text.count('set_xscale(') == 1, (
        '源码本来就只有一处 `ax.grid(True, axis="x")` 和一处 `set_xscale("log", base=2)`，'
        '写回后又各多了一处（grid=%d 次 / set_xscale=%d 次）：\n'
        '`ax.grid(True)` 会把只开 x 向的网格开成两向；`set_xscale("log")` 不带 base=2 会'
        '把 base 从 2 改成 10（刻度从 [0.5,1,2,4,8,16] 变成 [0.1,1,10,100,1000]）。\n'
        '---- 写回后的源码 ----\n%s'
        % (text.count('ax.grid('), text.count('set_xscale('), text))


def _case_f6(work):
    """F6：`--diff` 预览指的那份产物，必须和真写盘的文件逐字节（归一后）是同一份。"""
    d_prev = work / 'prev'
    d_write = work / 'write'
    _write(d_prev / 'fig.py', _FIG_DEAD_XLIM)
    _write(d_write / 'fig.py', _FIG_DEAD_XLIM)
    params, _ = _params_of(d_prev / 'fig.py')
    params['axes'][0]['xlim'] = [0.0, 5.0]
    _write_json(d_prev / 'p.json', params)
    _write_json(d_write / 'p.json', params)

    rc1, out1 = _run(d_prev, 'apply', 'fig.py', '--params', 'p.json',
                     '--diff', '--json')
    e1 = _entry(_load_json(out1))
    assert e1.get('preview_sha256_norm'), (
        '前置条件不成立：--diff 没给 preview_sha256_norm（rc=%s）：\n%s'
        % (rc1, out1[-2000:]))
    assert not (d_prev / 'fig.py').read_bytes() == b'', '前置：预览档居然不读源码'

    rc2, out2 = _run(d_write, 'apply', 'fig.py', '--params', 'p.json',
                     '--write', '--json')
    e2 = _entry(_load_json(out2))
    assert rc2 == 0 and e2.get('applied'), (
        '前置条件不成立：真写档应当成功（rc=%s, reason=%r）：\n%s'
        % (rc2, e2.get('reason'), out2[-2000:]))

    real = writeback.preview_fingerprint(_read(d_write / 'fig.py'))
    assert e1['preview_sha256_norm'] == real['sha256_norm'], (
        '预览和真写不是同一份产物：\n'
        '  预览档：%r / diff 行数 %d / 说要在 %.60s 里写\n'
        '  真写档：%r\n'
        '预览档（apply.py 里把 verify/semantic 钉成 False）退成"插块"，真写档走原位 —— '
        '用户点"预览"看到的和点"应用"得到的是两件事。\n'
        '  真写后的源码：\n%s'
        % (e1.get('preview_sha256_norm'), len(e1.get('diff') or []),
           json.dumps(e1.get('diff') or [], ensure_ascii=False),
           real['sha256_norm'], _read(d_write / 'fig.py')))


def _case_f7(work):
    """F7：源码里 imshow(vmin=2, vmax=7) 是真的 ⇒ 实测状态必须带这根轴的 clim。"""
    script = work / 'fig.py'
    _write(script, _FIG_IMSHOW)
    state = _real_state(script)
    ax0 = _axis(state) or {}
    got = ax0.get('clim')
    assert _close(got, [2.0, 7.0]), (
        '源码 imshow(vmin=2, vmax=7)，实测状态里 clim=%r（None = 压根没采）。\n'
        '采集侧 `toolbox._clim_of` 只读 colorbar 轴的 mappable，而块生成对任意 mappable '
        '都会写 `im.set_clim(...)` ⇒ 这类轴的 clim **写了但永远验不到**。'
        % (got,))


def _case_f8(work):
    """F8：`fig.legend(...)` 真画得出来 ⇒ 状态里必须能找到这个图例。"""
    script = work / 'fig.py'
    _write(script, _FIG_FIGLEGEND)
    state = _real_state(script)
    hits = _find_legend_like(state)
    assert hits, (
        '图上有 `fig.legend(...)`（真画出来了，重跑后 fig.legends 非空），'
        '但采集到的状态里找不到任何图例形状的数据：\n%s'
        % json.dumps(state, ensure_ascii=False)[:1500])


def _case_f9(work):
    """F9：被 delta 过滤器判成"没变"而**既不写也不验**的字段，必须能在 --json 里读到。"""
    script = work / 'fig.py'
    _write(script, _FIG_GRID_SCALE)
    params, _ = _params_of(script)
    params['axes'][0]['title_fontsize'] = 14.0
    _write_json(work / 'p.json', params)

    rc, out = _run(work, 'apply', 'fig.py', '--params', 'p.json', '--write',
                   '--style', 'block', '--json')
    entry = _entry(_load_json(out))
    assert rc == 0 and entry.get('applied'), (
        '前置条件不成立：这次应当写成功（rc=%s, reason=%r）：\n%s'
        % (rc, entry.get('reason'), out[-2000:]))

    blob = json.dumps(entry, ensure_ascii=False)
    assert 'xscale' in blob and 'grid' in blob, (
        '这次 delta 过滤器把 ax0.grid / ax0.xscale / ax0.xlim 判成"和源码一样、不用写"，'
        '于是它们既不写也不验；而 --json 载荷里连"这些字段被跳过了"都读不到 —— '
        '外部无法判断 verified 到底覆盖了哪些字段。\n'
        '期望：载荷里出现非空的"未写/未验字段"清单（键名不限，字段名要在）。\n'
        '---- 载荷（截断）----\n%s' % blob[:2500])


# ---------------------------------------------------------------- pytest 包装
# 每条 docstring 按 t21 的格式写：F 编号 / 当前现象 / 期望行为 / 修好后要做的动作。

@pytest.fixture()
def work():
    """每条用例一个干净临时目录（系统临时区，不在仓库里留垃圾）。"""
    d = Path(tempfile.mkdtemp(prefix='mpltweak_xrev_'))
    try:
        yield d
    finally:
        shutil.rmtree(str(d), ignore_errors=True)


@pytest.mark.xfail(strict=False,
                   reason='F1 实测缺字段时 compare 直接跳过（verify.py:1305-1311）'
                          '⇒ 死分支里的"改动"被报成 verified=True')
def test_f1_missing_actual_xlim_is_not_a_pass(work):
    """F1（高）参数要 xlim=[0,5]，实测那根轴压根没有 xlim（autoscale）。

    当前现象：写回把 `if False:` 死分支里的数字 0 改成 5（源码确实变了），重跑后实测
    仍是 autoscale；`verify.compare` 在"实测缺这个键"时 `continue`（不判失败、也不进
    `unjudged`）⇒ rc=0、`verified=True`，用户看到"图已更新（重跑 + 语义验证通过）"。
    期望行为：要么 xlim 真落地（[0,5]），要么**不许**同时声称 applied 且 verified。
    修好后要做的动作：删掉本函数的 xfail 装饰器（或把 strict 翻成 True）钉死。
    """
    _case_f1(work)


@pytest.mark.xfail(strict=False,
                   reason='F2 compare 读 e/a.get("cmap")，实测键名是 _cmap ⇒ cmap 比对是死代码'
                          '（verify.py:1274-1278）')
def test_f2_cmap_mismatch_is_reported(work):
    """F2（高）参数把 cmap 改成 magma，实测仍是 viridis。

    当前现象：`compare()` 的 cmap 分支读 `e.get('cmap')` / `a.get('cmap')`，而实测状态
    的键名是 `_cmap`（`_artist_state` 写的是下划线名，`describe._to_params` 导出时才映射
    成 `cmap`；`writeback._pv_live_field` 两种拼写都认）⇒ 这条分支永远进不去，cmap 写错
    也不报。T8⑤ 的用例之所以绿，是因为它**手搭**了 `{index:0, cmap:'magma'}`。
    期望行为：写进参数的 cmap 与实测不一致时必须报出来。
    修好后要做的动作：删掉本函数的 xfail 装饰器钉死（同时把同时接受 `_cmap` 的别名逻辑
    收成一处，别让两套键名再漂）。
    """
    _case_f2(work)


@pytest.mark.xfail(strict=False,
                   reason='F3 aspect_locked 时 compare 整段跳过 pos（verify.py:1216-1220），'
                          '而写回侧是比 pos 的')
def test_f3_locked_axis_pos_is_judged(work):
    """F3（中高）等比例锁定轴的 pos 被改到 [0.9,0.9,0.05,0.05]。

    当前现象：`compare()` 在 `aspect_locked`（或实测 aspect 非 auto）时整段跳过 pos；
    而 `writeback._pv_live_field` 对同一根轴是比 pos 的 ⇒ "写回敢写、验证不判"。
    另一半（同一条发现的延伸，修的时候要一起想）：`apply_aspect` 会在 draw 时按 aspect
    重算位置（`adjustable='box'` 时 set_position 的值根本站不住），所以"跳过"掩盖的是
    "位置落地了没有"这个真问题。
    期望行为：锁定轴的 pos 变更必须被判定（要么按原始 pos 口径比，要么显式判"这台机器上
    比不了"并让 verified 不为 True）。
    修好后要做的动作：删掉本函数的 xfail 装饰器钉死。
    """
    _case_f3(work)


@pytest.mark.xfail(strict=False,
                   reason='F4 块里的 ax.legend(loc,bbox,fontsize) 是整条重建，'
                          'ncol/title/frameon 被重置（writeback.py:6950-6966）')
def test_f4_legend_rebuild_keeps_unmodelled_props(work):
    """F4（中高）改图例 loc/fontsize 时，ncol=2 / title='组别' / frameon=False 全丢。

    当前现象：`render_block` 里的图例只有 `ax.legend(loc=..., bbox_to_anchor=(x,y),
    fontsize=...)` 一条 —— 这是**整条重建**，参数里没建模的属性一律回到默认值；而
    `_legend_state` 只采 labels/n_handles/fontsize/loc，所以验证也判不出来。
    期望行为：只改 loc/fontsize 之后，ncol/title/frameon 必须原样保留（要么带着原值重建，
    要么改用 `lg.set_bbox_to_anchor` / 修改现有 legend 对象）。
    修好后要做的动作：删掉本函数的 xfail 装饰器钉死。
    """
    _case_f4(work)


@pytest.mark.xfail(strict=False,
                   reason='F5 _delta_allowed 受 --no-semantic/--verify-fast 影响'
                          '（writeback.py:7583-7584）⇒ 同一份参数写出的块内容不同')
def test_f5_block_does_not_write_unchanged_fields(work):
    """F5（中）用户只改了 title 字号，块里却多写了 grid / xscale。

    当前现象：`_delta_allowed = (not fast_check) and (_PREVIEW or (verify and semantic))`
    —— 差异化过滤被**验证档位**绑住了：默认档块里 1 行，`--no-semantic` / `--verify-fast`
    档块里 6 行，多出来的 `ax.grid(True)` 会把只开 x 向的网格开成两向，
    `ax.set_xscale('log')` 不带 `base=2` 会把 base 从 2 改成 10（刻度 0.5..16 → 0.1..1000）；
    这两件事验证都判不出来（grid 在采集侧是 bool 折叠、xscale 只写名字不比 base）。
    期望行为：源码本来就是那个值的字段，任何档位都不许写进块。
    修好后要做的动作：删掉本函数的 xfail 装饰器钉死。
    """
    _case_f5(work)


@pytest.mark.xfail(strict=False,
                   reason='F6 预览档把 verify/semantic 钉成 False（apply.py:1343-1347）⇒ '
                          '预览退成插块、真写走原位')
def test_f6_preview_equals_real_write(work):
    """F6（中）`--diff` 预览说的产物，和真写盘的文件不是同一份。

    当前现象：预览档把 `verify=False, semantic=False` 传下去，`writeback` 里
    `_needs=True` ⇒ 自动退成"插块"；真写档留在原位（改死分支里那个数字）。实测：预览说
    "插块 + ax.set_xlim(0, 5)（4 行）"，真写是"在原位改 1 个数"。
    期望行为：预览必须就是真写的那一份字节（`preview_sha256_norm` == 真写盘文件的归一指纹）。
    修好后要做的动作：删掉本函数的 xfail 装饰器钉死。
    """
    _case_f6(work)


@pytest.mark.xfail(strict=False,
                   reason='F7 clim 只采 colorbar 轴的 mappable（toolbox._clim_of:829-840），'
                          '写回却对任意 mappable 写 set_clim')
def test_f7_non_colorbar_mappable_clim_is_collected(work):
    """F7（中）`imshow(vmin=2, vmax=7)` 的轴，实测状态里没有 clim。

    当前现象：`toolbox._clim_of` 只从 `ax._colorbar.mappable` 取 clim，普通 imshow 轴
    取不到 ⇒ 状态里没有 clim ⇒ compare 无从比对；可块生成对任意 mappable 都会写
    `im.set_clim(...)`（"写了但永远验不到"）。
    期望行为：有 mappable 的轴，clim 必须采到并参与比对（或明令不许写这类轴的 clim）。
    修好后要做的动作：删掉本函数的 xfail 装饰器钉死。
    """
    _case_f7(work)


@pytest.mark.xfail(strict=False,
                   reason='F8 状态只读 ax.get_legend()，fig.legend(...) 完全不在视野里'
                          '（verify.py:651 顶层只有 figsize_px/figsize_in/axes）')
def test_f8_figure_level_legend_is_visible(work):
    """F8（中低）`fig.legend(...)` 画得出来，但采集/参数/验证里都没有它。

    当前现象：`_state` 每个轴只读 `ax.get_legend()`；图级图例存在 `fig.legends` 里，
    顶层结构没有对应键 ⇒ GUI 看不到、写回写不了、验证也判不了（一张"图例凭空消失"的图
    在契约上是合法的）。
    期望行为：状态里必须能定位到图级图例（键名不限，能表达 labels/loc 即可）。
    修好后要做的动作：删掉本函数的 xfail 装饰器钉死。
    """
    _case_f8(work)


@pytest.mark.xfail(strict=False,
                   reason='F9 delta 过滤器把自己摘掉的字段喂回验证（writeback.py:7971/8016），'
                          '而 --json 不暴露 delta/delta_dropped（apply.py:820-887）')
def test_f9_dropped_fields_are_visible(work):
    """F9（中）"被判成没变、既不写也不验"的字段，在 `--json` 里读不到。

    当前现象：块路径把 `_prune_refused(data, _unexpressed, _delta_dropped)` 的产物当验证
    输入 —— 写回侧自己裁掉 ax0.grid / ax0.xscale / ax0.xlim / ax0.pos，然后拿裁剪后的
    期望去验证，rc 恒 0；`res['delta_dropped']` / `res['delta']` 存在，但 `_emit_json`
    的键集里两个都不是 ⇒ 消费方无法判断 verified 覆盖了什么。
    期望行为：`--json` 必须暴露被跳过（未写/未验）的字段名。
    修好后要做的动作：删掉本函数的 xfail 装饰器钉死。
    """
    _case_f9(work)


# ---------------------------------------------------------------- 脚本档入口
# `tests/test_suites.py` 用 `python tests/<name>.py` 跑套件：要求 rc=0 且输出含 ALL PASS。

SCRIPT_CASES = [
    ('F1', 'test_f1_missing_actual_xlim_is_not_a_pass', _case_f1),
    ('F2', 'test_f2_cmap_mismatch_is_reported', _case_f2),
    ('F3', 'test_f3_locked_axis_pos_is_judged', _case_f3),
    ('F4', 'test_f4_legend_rebuild_keeps_unmodelled_props', _case_f4),
    ('F5', 'test_f5_block_does_not_write_unchanged_fields', _case_f5),
    ('F6', 'test_f6_preview_equals_real_write', _case_f6),
    ('F7', 'test_f7_non_colorbar_mappable_clim_is_collected', _case_f7),
    ('F8', 'test_f8_figure_level_legend_is_visible', _case_f8),
    ('F9', 'test_f9_dropped_fields_are_visible', _case_f9),
]


def _short(exc, limit=300):
    text = ' '.join(str(exc).split())
    return text if len(text) <= limit else text[:limit] + '…'


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    only = [a for a in argv if not a.startswith('-')]
    t0 = time.time()
    xfail, passed, errored = [], [], []
    for fid, name, fn in SCRIPT_CASES:
        if only and name not in only and fid not in only:
            continue
        d = Path(tempfile.mkdtemp(prefix='mpltweak_xrev_'))
        try:
            fn(d)
        except AssertionError as e:
            xfail.append(name)
            print('XFAIL | %s | %s | %s' % (fid, name, _short(e)))
        except Exception as e:                      # noqa: BLE001
            errored.append(name)
            print('ERROR | %s | %s | %s: %s' % (fid, name, type(e).__name__, e))
            traceback.print_exc()
        else:
            passed.append(name)
            print('PASS | %s | %s | 该问题看起来已修好（把 xfail 翻成 strict）'
                  % (fid, name))
        finally:
            shutil.rmtree(str(d), ignore_errors=True)
    total = len(xfail) + len(passed) + len(errored)
    print('-' * 72)
    print('t19 回归网：%d 条 | XFAIL %d（问题仍在，符合预期） | PASS %d（已修好，可翻 strict）'
          ' | ERROR %d | %.1f 秒'
          % (total, len(xfail), len(passed), len(errored), time.time() - t0))
    if errored:
        print('ERROR 表示**用例自身**出了问题（不是"问题仍在"），明细见上面的 traceback')
        return 1
    if not total:
        print('一条都没跑（--only 过滤没命中？）')
        return 2
    print('ALL PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
