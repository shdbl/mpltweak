# -*- coding: utf-8 -*-
"""tests/test_xreview_regressions.py —— t19 独立审查的 9 条发现固化成回归网（T22；0.1.22 收敛）

来源：`_team014/xreview.md`（t19 跨模型独立审查，发现 F1..F9）。
0.1.18 挂进来时 9 条全是 `xfail(strict=False)`（"现在就红、修好自动转绿"）。0.1.22 起分两档，
**判据不变，变的是"标记过期会不会有人知道"**：

* **已修好 → 硬断言（8 条：F1 / F2 / F3 / F5 / F6 / F7 / F8 / F9）**：xfail 装饰器已删掉，
  函数体就是普通断言 —— 产品侧哪天回退，这几条直接红。各带**反向牙齿**（见各函数体末尾的
  "反向牙齿"段 + 每条 docstring 末尾那句"反向牙齿"：副本里把对应判据改坏 ⇒ 必须红）。
  教训：F2/F6/F9 在 0.1.19~0.1.21 期间"已修但标记没翻"，pytest 一直以 XPASS 报绿 ——
  **XPASS 混在绿里 = 没人会去翻标记**。这就是那三个版本漂过来的原因；0.1.22 一口气把
  F1/F3/F5/F7/F8 修完的同时当场翻掉，不留"下个版本再说"。
* **仍未修 → `xfail(strict=True)`（只剩 F4：图例整条重建丢 ncol/title/frameon）**：修好后
  XPASS 在 pytest 里**直接判红**，逼修的人在同一次改动里把标记翻掉（而不是留到下一个版本）。
* **脚本档**（`python tests/test_xreview_regressions.py`，`tests/test_suites.py` 的调用约定）
  与 pytest 同口径：`SCRIPT_CASES` 每条自带期望（`'pass'` = 已修 / `'xfail'` = 仍未修），
  期望 `xfail` 的**通过了** ⇒ 打 `XPASS` 并以 rc=1 退出；期望 `pass` 的失败 ⇒ 打 `FAIL`。

## 三条纪律（与 t19 报告的判据同源；违反其中一条，这条用例就不算证据）

1. **禁止手搭 actual 结构**。所有"实测"必须由真采集器给出 —— `verify.collect_isolated()`
   （子进程跑真脚本）或真 CLI `apply --write` 之后的重跑状态。t19 F2 那处死代码正是被
   "手搭 actual" 掩盖的：采集侧键名是 `_cmap`、比对侧读 `'cmap'`；手搭的字典两边都叫
   `cmap`，于是"看起来在比、其实永远比不到"。
2. **期望侧必须是真参数的形状**。参数一律由 `describe._to_params(真 state, script)` 产出，
   再改**用户会改的那一个字段**（t19 探针里手写 JSON 属"用户输入"，允许；但能用 describe
   产物当起点就别手写）。
3. **判据写"期望行为"，不写"现状"**。已翻的 8 条现在是**产品契约**（红 = 回退）；只剩 F4
   挂着，它的红是设计。每条 docstring 写明：F 编号 / 这条钉的是什么 / 反向牙齿怎么咬。

## 跑法

    python tests/test_xreview_regressions.py                      # 脚本档（XPASS 判失败）
    python -m pytest -q tests/test_xreview_regressions.py         # pytest 档（只剩 F4 是 strict xfail）
    python tests/test_xreview_regressions.py test_f2_...          # 只跑一条
    python -m pytest -q --runxfail tests/test_xreview_regressions.py
                                                                  # 把 xfail 当普通用例跑，
                                                                  # 看"到底过没过"（strict 下
                                                                  # 只看 XPASS 字符看不出原因）

默认档 9 条全跑约 30~40 秒（每条 1~5 个真子进程：采集 / apply / 重跑）。
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
        '契约：**请求了 xlim、实测没有这个键**必须判不一致（`verify._MISSING_KEY_KIND`'
        ' 里 xlim 是 bad），同时常量假分支里的写法不算候选（`writeback._dead_spans`）'
        '⇒ 该字段进 skipped、退块落地。两条都不许回退。\n'
        '---- CLI 输出尾巴 ----\n%s'
        % (got, entry.get('applied'), entry.get('verified'), rc,
           entry.get('reason'), out[-1500:]))


def _case_f2(work):
    """F2（已修，0.1.22 翻标记）：参数 cmap='magma' vs 实测 viridis ⇒ compare 必须报不一致。

    这条现在钉的是：`verify.compare` 的 cmap 分支必须**摸得到实测侧的名字**。
    0.1.18 第一版读 `e.get('cmap')` / `a.get('cmap')`，而真采集写的是 `_cmap`
    （`describe._to_params` 导出时才映射成 `cmap`）⇒ 那段是**死代码**：手搭字典的用例绿、
    真采集一次都不触发。现在走 `verify._act()`（`_ACTUAL_ALIAS['cmap'] = ('cmap','_cmap')`），
    且"参数要 cmap 但实测没 mappable"进 `unjudged`（不是静默放行）。
    """
    script = work / 'fig.py'
    _write(script, _FIG_IMSHOW)
    params, state = _params_of(script)
    assert 'cmap' in (params.get('axes') or [{}])[0], (
        '前置条件不成立：describe 根本没导出 cmap（采集侧没给 _cmap）——'
        '先看 _FIG_IMSHOW 是不是没 imshow 成功')
    params['axes'][0]['cmap'] = 'magma'

    unjudged = []
    bad = verify.compare(params['axes'], state['axes'], unjudged=unjudged)
    assert any('cmap' in b for b in bad), (
        "参数要求 cmap='magma'，实测 'viridis'，compare() 一条不一致都没报：%r\n"
        '根因（0.1.18 的现场）：compare 读 e.get("cmap")/a.get("cmap")，而实测状态的键名是'
        ' _cmap（verify._state → _artist_state 写的是下划线名，describe 导出时才映射成'
        ' cmap）⇒ 那段是死代码。修复口径见 verify._ACTUAL_ALIAS + verify._act()。'
        % (bad,))
    assert not any('cmap' in u for u in unjudged), (
        '两边都有 cmap 时不许把它记成"没判"（unjudged=%r）—— "比不了"和"不一致"是两件事'
        % (unjudged,))

    # ---- 反向牙齿①（内置、零成本）：cmap 一致时**一条都不许报** --------------------
    # 只钉"不一致要报"很容易写歪成"永远报"（判据恒真时正向照样绿）；这一半把它钉回去。
    same = copy.deepcopy(params)
    same['axes'][0]['cmap'] = (_axis(state) or {}).get('_cmap') or 'viridis'
    bad_same = verify.compare(same['axes'], state['axes'])
    assert not any('cmap' in b for b in bad_same), (
        '参数与实测 cmap 相同时 compare() 仍报不一致（判据恒真）：%r\n'
        '实测 _cmap=%r' % (bad_same, (_axis(state) or {}).get('_cmap')))
    # ---- 反向牙齿②（手工，副本里做）：把 verify._ACTUAL_ALIAS['cmap'] 改回只认
    #      'cmap' ⇒ 本用例立刻红（实测侧 _cmap 读不到 ⇒ bad 为空）。
    #      读数见 D:\\Agent\\deepseek\\_team022\\tests-ci\\report.md。


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
        '契约（0.1.22 起）：锁定轴的 pos 两侧都有值且确实不同 ⇒ **bad**（"你要的位置没落地"），'
        '不许再整段跳过；实测缺 pos 才记 unjudged。写回侧同一口径（锁定轴 pos 进 pending）。'
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
    """F6（已修，0.1.22 翻标记）：`--diff` 预览的那份产物 == 真写盘的文件（归一指纹对拍）。

    这条现在钉的是：预览与真写**走同一条决策链**。0.1.18 的现场是预览档把
    `verify=False, semantic=False` 钉死 ⇒ `writeback` 里 `_needs=True` ⇒ 预览静默退成
    "插块"，而真写在原位改那个数字 —— 用户点"预览"看到的和点"应用"得到的不是同一件事。
    修复口径：预览在一份**影子副本**上跑与真写完全相同的决策链，指纹从影子读出来
    （`apply.py` §8.5b，`preview_sha256_norm` 抹掉块说明行里的生成时间 ⇒ 跨分钟稳定）。
    `writeback.preview_fingerprint(真写后的源码)['sha256_norm']` 是那个"真写盘"口径。
    """
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
        '预览档（老实现把 verify/semantic 钉成 False）退成"插块"，真写档走原位 —— '
        '用户点"预览"看到的和点"应用"得到的是两件事。\n'
        '  真写后的源码：\n%s'
        % (e1.get('preview_sha256_norm'), len(e1.get('diff') or []),
           json.dumps(e1.get('diff') or [], ensure_ascii=False),
           real['sha256_norm'], _read(d_write / 'fig.py')))
    # 预览档自己**一个字节都不许动**盘上的源码（F6 的另一个半：预览 = 只读）
    assert (d_prev / 'fig.py').read_bytes() == _FIG_DEAD_XLIM.encode('utf-8'), (
        '预览档把源码写脏了（--diff 必须是只读的）：\n' + _read(d_prev / 'fig.py'))

    # ---- 反向牙齿（内置）：换一个参数值 ⇒ 预览指纹必须跟着变 -----------------------
    # 证明这个对拍**能区分**两份不同产物（不是两边都取同一个常量的恒等断言）。
    params['axes'][0]['xlim'] = [0.0, 9.0]
    _write_json(d_prev / 'p2.json', params)
    rc3, out3 = _run(d_prev, 'apply', 'fig.py', '--params', 'p2.json',
                     '--diff', '--json')
    e3 = _entry(_load_json(out3))
    assert e3.get('preview_sha256_norm'), (
        '前置条件不成立：第二个参数档的 --diff 也没指纹（rc=%s）：\n%s'
        % (rc3, out3[-1500:]))
    assert e3['preview_sha256_norm'] != e1['preview_sha256_norm'], (
        'xlim 从 [0,5] 改成 [0,9]，预览指纹却没变（%r == %r）—— 对拍判据分不出两份不同的'
        '产物，等于恒真' % (e3['preview_sha256_norm'], e1['preview_sha256_norm']))
    # ---- 反向牙齿②（手工，副本里做）：把预览档重新钉回 verify=False/semantic=False
    #      ⇒ 指纹回到"插块"那份，本用例红。读数见 _team022/tests-ci/report.md。


def _case_f7(work):
    """F7：源码里 imshow(vmin=2, vmax=7) 是真的 ⇒ 实测状态必须带这根轴的 clim。"""
    script = work / 'fig.py'
    _write(script, _FIG_IMSHOW)
    state = _real_state(script)
    ax0 = _axis(state) or {}
    got = ax0.get('clim')
    assert _close(got, [2.0, 7.0]), (
        '源码 imshow(vmin=2, vmax=7)，实测状态里 clim=%r（None = 压根没采）。\n'
        '契约（0.1.22 起）：`toolbox._clim_of` 对**任意** mappable 都要采 clim'
        '（colorbar 槽轴 → images → collections），否则块生成写的 `im.set_clim(...)` '
        '永远验不到。'
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
    """F9（已修，0.1.22 翻标记）：被 delta 过滤器摘掉的字段必须出现在 `--json` 里。

    这条现在钉的是：`files[].delta_dropped`（字段名清单，形如 `ax0.grid`）与
    `files[].delta`（计数/原因，`{'dropped': n}` 或 `{'skipped': 'baseline_unavailable'}`）
    必须真的透传出来。0.1.18 的现场：块路径把 `_prune_refused(data, _unexpressed,
    _delta_dropped)` 的产物当验证输入（写回侧自己裁掉 ax0.grid / ax0.xscale / ax0.xlim /
    ax0.pos，再拿裁剪后的期望去验证 ⇒ rc 恒 0），而 `_emit_json` 的键集里两个键都没有
    ⇒ 消费方无法判断 verified 到底覆盖了什么。
    """
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

    dropped = entry.get('delta_dropped')
    assert isinstance(dropped, list) and dropped, (
        '这次 delta 过滤器把 ax0.grid / ax0.xscale / ax0.xlim / ax0.pos 判成"和源码一样、'
        '不用写"，于是它们既不写也不验；而 --json 载荷里连"这些字段被跳过了"都读不到'
        '（delta_dropped=%r）—— 外部无法判断 verified 到底覆盖了哪些字段。\n'
        '---- 载荷（截断）----\n%s'
        % (dropped, json.dumps(entry, ensure_ascii=False)[:2500]))
    names = [str(x) for x in dropped]
    for want in ('grid', 'xscale'):
        assert any(n.endswith('.' + want) for n in names), (
            'delta_dropped 里没有 ax0.%s（拿到 %r）—— 被摘掉的危险字段必须逐名可见'
            % (want, names))
    delta = entry.get('delta')
    assert isinstance(delta, dict) and int(delta.get('dropped') or 0) >= 2, (
        'delta 计数没透传（delta=%r）—— "摘了几个"要和清单对得上' % (delta,))

    # ---- 反向牙齿（内置）：用户**真改了**的字段不许出现在"被摘掉"清单里 ---------
    # 否则这份清单就成了"整张参数表都摘了"的假披露，上面几条会被它一起骗过。
    assert not any('title_fontsize' in n for n in names), (
        '用户这次真改了 title_fontsize（块里也确实写了 ax.title.set_fontsize(14)），'
        '却被报成"被摘掉"：%r —— delta_dropped 必须只列**没动**的字段' % (names,))
    # ---- 反向牙齿②（手工，副本里做）：把 apply._emit_json 里 delta / delta_dropped
    #      两个键删掉（回到 0.1.18 的键集）⇒ 本用例立刻红。读数见
    #      _team022/tests-ci/report.md。


# ---------------------------------------------------------------- pytest 包装
# 每条 docstring 按 t21 的格式写：F 编号 / 这条钉的是什么 / 反向牙齿怎么咬。
#
# 标记状态（0.1.22）：8 条已修（F1/F2/F3/F5/F6/F7/F8/F9）⇒ **没有 xfail 装饰器**（红 = 产品回退）；
# 只剩 F4（图例整条重建）仍未修 ⇒ `xfail(strict=True)`（修好后 XPASS 直接判红）。

@pytest.fixture()
def work():
    """每条用例一个干净临时目录（系统临时区，不在仓库里留垃圾）。"""
    d = Path(tempfile.mkdtemp(prefix='mpltweak_xrev_'))
    try:
        yield d
    finally:
        shutil.rmtree(str(d), ignore_errors=True)


def test_f1_missing_actual_xlim_is_not_a_pass(work):
    """F1（高，0.1.22 已修并翻标记）参数要 xlim=[0,5]，实测那根轴压根没有 xlim（autoscale）。

    这条现在钉的是什么：**"请求了、实测没有这个键"不许静默放行**。0.1.18 的现场：
    参数要 xlim，写回把 `if False:` 死分支里的 0 改成 5（源码确实变了），重跑实测仍是
    autoscale（死分支不执行），而 `compare()` 在"实测缺这个键"时直接 `continue`
    ⇒ rc=0、`verified=True`，用户看到"图已更新（重跑 + 语义验证通过）"。
    0.1.22 起两件事一起改：① 缺键语义**逐键定**（`verify._MISSING_KEY_KIND`：
    `xlim`/`ylim` = bad，结构性不可判的只记 unjudged）；② 常量假分支（`if False:` 等）
    里的调用**不算该字段的写法**（`writeback._dead_spans`）⇒ 该字段进 skipped、退块。
    本用例的判据保持"两选一"的宽口径：**要么 xlim 真落地，要么不许同时声称 applied 且
    verified**（换实现不许把这条契约做回去）。

    反向牙齿：副本里把 `_MISSING_KEY_KIND` 的 xlim 改回 `unjudged` **且**把
    `_dead_spans` 的过滤关掉（= 恢复 0.1.21 的现场）⇒ 本用例立刻红
    （读数见 `_team022/tests-ci/report.md` 的 R10）。
    """
    _case_f1(work)


def test_f2_cmap_mismatch_is_reported(work):
    """F2（高，0.1.22 已修并翻标记）参数把 cmap 改成 magma，实测仍是 viridis。

    这条现在钉的是什么：`compare()` 的 cmap 分支必须**读得到实测状态里的名字**。
    0.1.18 那版读 `e.get('cmap')` / `a.get('cmap')`，而真采集写的是 `_cmap`
    （`describe._to_params` 导出时才映射成 cmap）⇒ 那条分支是**死代码**：
    手搭 `{index:0, cmap:'magma'}` 的用例能绿，真采集一次都不触发。
    现在统一走 `verify._act()`（`_ACTUAL_ALIAS = {'cmap': ('cmap','_cmap'), ...}`），
    且"参数要 cmap、实测没 mappable"进 `unjudged`（"比不了"≠"不一致"）。

    反向牙齿：① 内置在 `_case_f2` 末尾 —— cmap 一致时一条都不许报（防判据写成恒真）；
    ② 手工：把 `verify._ACTUAL_ALIAS['cmap']` 改回 `('cmap',)` ⇒ 本用例立刻红
    （副本里做，读数见 `_team022/tests-ci/report.md`）。
    """
    _case_f2(work)


def test_f3_locked_axis_pos_is_judged(work):
    """F3（中高，0.1.22 已修并翻标记）等比例锁定轴的 pos 被改到 [0.9,0.9,0.05,0.05]。

    这条现在钉的是什么：**写回敢写/敢提示的东西，验证必须敢判**。0.1.18 的现场：
    `compare()` 在 `aspect_locked`（或实测 aspect 非 auto）时**整段跳过 pos**，而写回侧
    （`writeback._pv_live_field`）对同一根轴是比 pos 的 ⇒ "写回敢写、验证不判"，
    拖了锁定轴却报 verified=True 是可能的谎报。
    0.1.22 起：两侧都有 pos 且确实不同 ⇒ **bad**（"你要的位置没落地"，绝不 verified）；
    实测缺 pos ⇒ unjudged（采集异常，判不了）；写回侧把锁定轴的 pos 记进 pending
    （`axN.pos`）+ 一句人话说明"位置由长宽比决定"。
    另一半（同一条发现的延伸）：`apply_aspect` 会在 draw 时按 aspect 重算位置（
    `adjustable='box'` 时 set_position 的值根本站不住）—— 所以这里判的是"参数与实测
    对不上就别说通过"，而不是"把位置硬塞进去"。

    反向牙齿：副本里把 pos 比对改回 `if not locked and ep and ap:`（锁定轴整段跳过）
    ⇒ 本用例立刻红（读数见 report.md 的 R11）。
    """
    _case_f3(work)


@pytest.mark.xfail(strict=True,
                   reason='F4 块里的 ax.legend(loc,bbox,fontsize) 是整条重建，'
                          'ncol/title/frameon 被重置（writeback.render_block 的图例分支；'
                          '行号每轮都漂，按符号查）；'
                          'strict=True：修好后 XPASS 直接红，逼着同一次改动里翻标记')
def test_f4_legend_rebuild_keeps_unmodelled_props(work):
    """F4（中高，仍未修）改图例 loc/fontsize 时，ncol=2 / title='组别' / frameon=False 全丢。

    当前现象：`render_block` 里的图例只有 `ax.legend(loc=..., bbox_to_anchor=(x,y),
    fontsize=...)` 一条 —— 这是**整条重建**，参数里没建模的属性一律回到默认值；而
    `_legend_state` 只采 labels/n_handles/fontsize/loc，所以验证也判不出来。
    期望行为：只改 loc/fontsize 之后，ncol/title/frameon 必须原样保留（要么带着原值重建，
    要么改用 `lg.set_bbox_to_anchor` / 修改现有 legend 对象）。
    修好后要做的动作：删掉本函数的 xfail 装饰器、改写 docstring 并补反向牙齿。
    """
    _case_f4(work)


def test_f5_block_does_not_write_unchanged_fields(work):
    """F5（中，0.1.22 已修并翻标记）用户只改了 title 字号，块里却多写了 grid / xscale。

    这条现在钉的是什么：**没有基线时也不许重写危险字段**。0.1.18 的现场：
    `_delta_allowed = (not fast_check) and (_PREVIEW or (verify and semantic))`
    —— 差异化过滤被**验证档位**绑住了：默认档块里 1 行，`--no-semantic` / `--verify-fast`
    档块里 6 行，多出来的 `ax.grid(True)` 会把只开 x 向的网格开成两向，
    `ax.set_xscale('log')` 不带 `base=2` 会把 base 从 2 改成 10（刻度 0.5..16 → 0.1..1000），
    而这两件事验证都判不出来（grid 在采集侧是 bool 折叠、xscale 只写名字不比 base）
    ⇒ **静默的数据损坏级重写**。
    0.1.22 起：没有基线（`--no-semantic` / `--verify-fast` / `--no-verify` 这类不重跑脚本
    的档）时，`writeback._strip_nonidempotent` 把"重复应用不幂等"的字段（grid / scale /
    lines / 锁定轴 pos）从块计划里摘掉，改记 **`unjudged`** + 一句人话（要写就用默认档）；
    默认档仍走基线差异过滤，块里只写用户真改的那一个字段。

    反向牙齿：副本里让 `_strip_nonidempotent` 直接 `return needed, []`（= 不摘字段，
    恢复 0.1.21 的行为）⇒ 本用例立刻红（源码里 `ax.grid(` / `set_xscale(` 又各 2 次；
    读数见 report.md 的 R12）。
    """
    _case_f5(work)


def test_f6_preview_equals_real_write(work):
    """F6（中，0.1.22 已修并翻标记）`--diff` 预览的那份产物，和真写盘的文件是同一份。

    这条现在钉的是什么：预览与真写**走同一条决策链**。0.1.18 的现场是预览档把
    `verify=False, semantic=False` 钉死 ⇒ `writeback._needs=True` ⇒ 预览静默退成"插块"，
    真写却留在原位改那个数字 —— 用户点"预览"看到的和点"应用"得到的不是同一件事。
    现在预览在一份**影子副本**上跑与真写完全相同的决策链，指纹从影子读出来；
    `preview_sha256_norm` 抹掉块说明行里的生成时间 ⇒ 跨分钟也可对拍。

    反向牙齿：① 内置 —— 把参数值从 xlim=[0,5] 换成 [0,9]，预览指纹必须跟着变
    （防"两边都取同一个常量"的恒等对拍）；② 手工：把预览档重新钉回
    `verify=False/semantic=False` ⇒ 本用例红（副本里做，读数见 report.md）。
    """
    _case_f6(work)


def test_f7_non_colorbar_mappable_clim_is_collected(work):
    """F7（中，0.1.22 已修并翻标记）`imshow(vmin=2, vmax=7)` 的轴，实测状态里没有 clim。

    这条现在钉的是什么：**写了的东西必须验得到**。0.1.18 的现场：`toolbox._clim_of` 只从
    `ax._colorbar.mappable` 取 clim，普通 imshow 轴取不到 ⇒ 状态里没有 clim ⇒ compare 无从
    比对；可块生成对任意 mappable 都会写 `im.set_clim(...)`（"写了但永远验不到"）。
    0.1.22 起：`_clim_of` 对**任意** mappable 都采（顺序：colorbar 槽轴 → images → collections），
    同时 `writeback._clim_literal_equal` 补上静态等价证明（源码本来就写着
    `vmin=/vmax=` 或 `set_clim(lo, hi)` ⇒ 块里不重写；只写一个端点时静态证不出，
    照写 —— 绝不猜）。

    反向牙齿：副本里让 `_clim_of` 在"没有 colorbar mappable"时直接 `return None`
    （= 恢复 0.1.21 的采集口径）⇒ 本用例立刻红（读数见 report.md 的 R13）。
    """
    _case_f7(work)


def test_f8_figure_level_legend_is_visible(work):
    """F8（中低，0.1.22 已修并翻标记）`fig.legend(...)` 画得出来，但采集/参数/验证里都没有它。

    这条现在钉的是什么：**图上的东西不许"凭空消失"**。0.1.18 的现场：`_state` 每个轴只读
    `ax.get_legend()`，而图级图例存在 `fig.legends` 里 ⇒ 顶层结构没有对应键 ⇒ GUI 看不到、
    写回写不了、验证也判不了（一张"图例凭空消失"的图在契约上是合法的）。
    0.1.22 起：`verify._state` 在**真有**图级图例时加 `fig_legends` 键（没有则一个字节不加），
    每条图例给 `labels` / `n_handles` / 能取到就带 `fontsize` / `loc`（与轴级图例同一套
    `_legend_item` 口径）。**只做到"能定位"**：参数/写回仍只覆盖轴级图例（新增
    `fig_legend` 参数是 0.2.0 的事），describe/check 会明说"图级图例不可编辑"——
    这样"图上有几个图例"至少不再失踪，也不会把 fig_legends 混进参数文件。

    反向牙齿：副本里让 `_state` 不输出 `fig_legends`（`if False:`）⇒ 本用例立刻红
    （读数见 report.md 的 R14）。
    """
    _case_f8(work)


def test_f9_dropped_fields_are_visible(work):
    """F9（中，0.1.22 已修并翻标记）"被判成没变、既不写也不验"的字段，必须能在 --json 里读到。

    这条现在钉的是什么：`files[].delta_dropped`（逐名字段清单，形如 `ax0.grid`）与
    `files[].delta`（计数/原因）必须透传。0.1.18 的现场：块路径把
    `_prune_refused(data, _unexpressed, _delta_dropped)` 的产物当验证输入（写回侧自己
    裁掉 ax0.grid / ax0.xscale / ax0.xlim / ax0.pos，再拿裁剪后的期望去验证 ⇒ rc 恒 0），
    而 `_emit_json` 的键集里两个都不是 ⇒ 消费方无法判断 verified 覆盖了什么。

    反向牙齿：① 内置 —— 用户真改了的 `title_fontsize` 不许出现在"被摘掉"清单里
    （防清单退化成"整张参数表都摘了"的假披露）；② 手工：把 `_emit_json` 里
    `delta` / `delta_dropped` 两个键删掉（回到 0.1.18 的键集）⇒ 本用例红
    （副本里做，读数见 report.md）。
    """
    _case_f9(work)


# ---------------------------------------------------------------- 脚本档入口
# `tests/test_suites.py` 用 `python tests/<name>.py` 跑套件：要求 rc=0 且输出含 ALL PASS。
# 0.1.22：脚本档与 pytest 的 strict 同口径 —— 期望 `xfail` 的**通过了** ⇒ XPASS ⇒ rc=1
# （0.1.18~0.1.21 那段"XPASS 也算绿"就是 F2/F6/F9 漂三个版本的直接原因）。

SCRIPT_CASES = [
    # (F 编号, 用例名, 用例体, 期望) —— 'pass' = 已修（硬断言）；'xfail' = 仍未修（挂 strict）
    ('F1', 'test_f1_missing_actual_xlim_is_not_a_pass', _case_f1, 'pass'),
    ('F2', 'test_f2_cmap_mismatch_is_reported', _case_f2, 'pass'),
    ('F3', 'test_f3_locked_axis_pos_is_judged', _case_f3, 'pass'),
    ('F4', 'test_f4_legend_rebuild_keeps_unmodelled_props', _case_f4, 'xfail'),
    ('F5', 'test_f5_block_does_not_write_unchanged_fields', _case_f5, 'pass'),
    ('F6', 'test_f6_preview_equals_real_write', _case_f6, 'pass'),
    ('F7', 'test_f7_non_colorbar_mappable_clim_is_collected', _case_f7, 'pass'),
    ('F8', 'test_f8_figure_level_legend_is_visible', _case_f8, 'pass'),
    ('F9', 'test_f9_dropped_fields_are_visible', _case_f9, 'pass'),
]


def _short(exc, limit=300):
    text = ' '.join(str(exc).split())
    return text if len(text) <= limit else text[:limit] + '…'


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    only = [a for a in argv if not a.startswith('-')]
    t0 = time.time()
    xfailed, passed, xpassed, failed, errored = [], [], [], [], []
    for fid, name, fn, expect in SCRIPT_CASES:
        if only and name not in only and fid not in only:
            continue
        d = Path(tempfile.mkdtemp(prefix='mpltweak_xrev_'))
        try:
            fn(d)
        except AssertionError as e:
            if expect == 'xfail':
                xfailed.append(name)
                print('XFAIL | %s | %s | 问题仍在（符合预期）| %s'
                      % (fid, name, _short(e)))
            else:
                failed.append((fid, name, _short(e)))
                print('FAIL | %s | %s | 已修的问题回退了 | %s'
                      % (fid, name, _short(e)))
        except Exception as e:                      # noqa: BLE001
            errored.append(name)
            print('ERROR | %s | %s | %s: %s' % (fid, name, type(e).__name__, e))
            traceback.print_exc()
        else:
            if expect == 'pass':
                passed.append(name)
                print('PASS | %s | %s | 契约成立（这条现在是硬断言）' % (fid, name))
            else:
                xpassed.append((fid, name))
                print('XPASS | %s | %s | 已经修好了但标记没翻 ⇒ 必须翻掉'
                      '（pytest 的 strict=True 下这就是红）' % (fid, name))
        finally:
            shutil.rmtree(str(d), ignore_errors=True)
    total = len(xfailed) + len(passed) + len(xpassed) + len(failed) + len(errored)
    print('-' * 72)
    print('t19 回归网：%d 条 | 已修 PASS %d | XFAIL %d（问题仍在，符合预期） | '
          'XPASS %d | FAIL %d | ERROR %d | %.1f 秒'
          % (total, len(passed), len(xfailed), len(xpassed), len(failed),
             len(errored), time.time() - t0))
    if errored:
        print('ERROR 表示**用例自身**出了问题（不是"问题仍在"），明细见上面的 traceback')
    # 失败明细按 `FAIL |` 开头再打一遍：`tests/test_suites.py` 的包装器只把 FAIL/FAILED
    # 开头的行贴进 CI 注解（输出被截断时这些短行是唯一还能读到的证据）。
    for fid, name, why in failed:
        print('FAIL | %s | %s | %s' % (fid, name, why))
    for fid, name in xpassed:
        print('FAIL | %s | %s | XPASS：期望仍红却已经通过 —— 翻掉 xfail 并把 reason/'
              'docstring 改写成"现在防的是什么"，同时补一条反向牙齿' % (fid, name))
    if errored or failed or xpassed:
        print('FAILED: %d 项（FAIL %d / XPASS %d / ERROR %d）'
              % (len(failed) + len(xpassed) + len(errored),
                 len(failed), len(xpassed), len(errored)))
        return 1
    if not total:
        print('一条都没跑（--only 过滤没命中？）')
        return 2
    print('ALL PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
