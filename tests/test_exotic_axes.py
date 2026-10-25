# -*- coding: utf-8 -*-
"""T20（0.1.18）：**非常规轴形态** —— `Axes3D` / `PolarAxes` / cartopy `GeoAxes`。

为什么要有这套
==============
0.1.18 之前所有夹具都是**2D 笛卡尔轴**。外部审查点名三类真实形态从没覆盖过，而它们的
失败形态特别难看：**工具生成一段非法代码 -> 重跑崩 -> 回滚 -> 用户看到一坨 traceback**。
用户看不懂 traceback，也学不到"该怎么改"。

四条硬判据（一条都不放宽，也不许降级成 skip）
============================================
  A. **不许生成非法代码**：写回后的脚本必须还能跑通（子进程 rc=0）。
  B. **不许把 traceback 丢给用户**：apply 的 stdout/stderr 里不许出现
     `Traceback (most recent call last)`。
  C. **拒写必须 fail-closed 且是人话**：rc∈{1,2} 时脚本必须**一个字节都没动**（回滚干净），
     且输出里有中文提示（不能只丢英文异常名）。
  D. **不存在的属性不许被写出来**：3D 只有 `left/right/bottom/top` 四根 spine，polar 只有
     `polar/start/end/inner`。写出来的每一根 spine 名都必须是**该轴真实的** keys，
     否则运行期就是 `KeyError` —— 这正是外部审查担心的那条。

每个场景的结局必须落在四档之一（`classify_outcome`）
====================================================
  * `landed`   —— 目标字段真的写进源码，且读回对得上；
  * `refused`  —— rc∈{1,2} + 字节不变 + 人话（= fail-closed，合格）；
  * `disclosed`—— rc=0 但明确说"没落地"（`best_effort` 且 pending/warning 点名该字段）；
  * `silent`   —— rc=0、字节不变、却报 `no_change`（"参数与原代码一致，无需改动"），
                  而参数**明明和代码不一样** ⇒ 用户的请求被静默丢弃，还被告知"没东西要改"。

`silent` 是**违约**（既没写对，也没 fail-closed，更没有说清楚）。但它是**当前树的已知缺口**
（polar / GeoAxes 上改 `pos`、请求 polar 没有的 `spines['left']`），所以按本仓库既有做法
（`tests/test_shapes.py` 的 `KNOWN_GAP`）记成 `GAP` 行：**照样逐条打印、进报告**，但今天不判红；
把 `MPLTWEAK_STRICT_EXOTIC=1` 打开就全部升级成 FAIL（engine 修完就该这么跑）。

参数怎么造（踩过才知道）
========================
**不发"最小参数"**（只有 `index` + 目标字段）：实测（2026-10-25 当前树）稀疏条目的 `pos`
会被当成非法值判 `bad_params` 拒写，于是这一格什么都测不到。这里改成**用 describe 的全量
条目**、只覆盖要改的那一个字段 —— describe 的其它值本来就与源码一致，真正落地的仍然只有
目标字段（"只改一处"的语义不变）。**每个场景用一份全新夹具**（同一个脚本反复改会让
"字节变了没有"这个判据失真）。

cartopy **默认 skip 安全**：缺 cartopy 的机器（含 CI）打印 SKIP 原因、**不判红**。
解释器次序：`MPLTWEAK_CARTOPY_PY`（设成 `none` 可强制走 skip 分支自检）-> 当前解释器 ->
`D:\\Miniconda\\envs\\keyan\\python.exe`。

跑法：`python tests/test_exotic_axes.py`
"""
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

# 控制台兜底（仓库口径：断言里的中文/箭头不许把 GBK 控制台打挂）
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors='replace')
    except (AttributeError, ValueError, OSError):
        pass

PY = sys.executable
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, 'src')
KEYAN_PY = r'D:\Miniconda\envs\keyan\python.exe'
#: 打开后 GAP 升级为 FAIL（engine 修完 polar/GeoAxes 的 pos 之后就该这么跑）
STRICT = os.environ.get('MPLTWEAK_STRICT_EXOTIC', '') not in ('', '0', 'no', 'false')

fail = 0
gap = 0
skip = 0
rows = []

HEAD = ("# -*- coding: utf-8 -*-\n"
        "import matplotlib\n"
        "matplotlib.use('Agg')\n"
        "import matplotlib.pyplot as plt\n")

#: 三种形态 + 一个"两根 GeoAxes"（验 fig.axes 顺序与身份）
FIXTURES = {
    '3d': HEAD + (
        "fig = plt.figure(figsize=(5, 4))\n"
        "ax = fig.add_subplot(111, projection='3d')\n"
        "ax.plot([1, 2, 3], [3, 2, 1], [1, 2, 3])\n"
        "ax.set_title('cube', fontsize=10)\n"
        "ax.grid(True)\n"
        "fig.savefig('out.png')\n"
    ),
    'polar': HEAD + (
        "import numpy as np\n"
        "fig = plt.figure(figsize=(5, 5))\n"
        "ax = fig.add_subplot(111, projection='polar')\n"
        "th = np.linspace(0, 6.28, 50)\n"
        "ax.plot(th, np.cos(th))\n"
        "ax.set_title('rose', fontsize=10)\n"
        "fig.savefig('out.png')\n"
    ),
    # 注意：**不用 ax.coastlines()** —— 它要下 Natural Earth 数据，离线机器会红。
    'geo': HEAD + (
        "import cartopy.crs as ccrs\n"
        "fig = plt.figure(figsize=(6, 4))\n"
        "ax = fig.add_subplot(111, projection=ccrs.PlateCarree())\n"
        "ax.set_extent([100, 130, 20, 45], crs=ccrs.PlateCarree())\n"
        "ax.plot([100, 120], [20, 40], transform=ccrs.PlateCarree())\n"
        "ax.set_title('domain', fontsize=10)\n"
        "fig.savefig('out.png')\n"
    ),
    'geo2': HEAD + (
        "import cartopy.crs as ccrs\n"
        "fig = plt.figure(figsize=(8, 4))\n"
        "ax1 = fig.add_subplot(121, projection=ccrs.PlateCarree())\n"
        "ax1.set_extent([100, 130, 20, 45], crs=ccrs.PlateCarree())\n"
        "ax2 = fig.add_subplot(122, projection=ccrs.PlateCarree())\n"
        "ax2.set_extent([0, 30, -20, 10], crs=ccrs.PlateCarree())\n"
        "fig.savefig('out.png')\n"
    ),
}

SPINE_RE = re.compile(r"spines\[['\"]([A-Za-z_]+)['\"]\]")
TRACE_RE = re.compile(r'Traceback \(most recent call last\)')
CJK = re.compile(r'[\u4e00-\u9fff]')
#: 人话提示：本仓库的失败/拒写行以方括号标签起头（[X] / [--] / [!]）且含中文
HUMAN_RE = re.compile(r'\[(X|x|--|!)\][^\n]*')


def _gbk_safe(s):
    s = str(s)
    enc = getattr(sys.stdout, 'encoding', None) or 'utf-8'
    try:
        return s.encode(enc, 'replace').decode(enc, 'replace')
    except (LookupError, UnicodeDecodeError):
        return s.encode('ascii', 'backslashreplace').decode('ascii')


def check(name, cond, detail=''):
    global fail
    print(('PASS' if cond else 'FAIL') + ' | ' + _gbk_safe(name)
          + ((' | ' + _gbk_safe(detail)) if detail else ''))
    if not cond:
        fail += 1


def known_gap(name, cond, detail=''):
    """已知缺口：不合格就打印 GAP（今天不判红；STRICT=1 时升级成 FAIL）。"""
    global gap
    if cond:
        return
    if STRICT:
        check(name + '（STRICT）', False, detail)
        return
    gap += 1
    print('GAP  | ' + _gbk_safe(name) + ' | ' + _gbk_safe(detail))


def skip_check(name, why):
    global skip
    skip += 1
    print('SKIP | ' + _gbk_safe(name) + ' | ' + _gbk_safe(why))


def record(shape, case, verdict, extra=''):
    rows.append((shape, case, verdict, extra))


def _env():
    """子进程环境：**硬钉** Agg（不是 setdefault）+ 输出编码口径。

    写法与 `tests/test_sandbox.py::_pin_child_env` 同构（先拷贝再逐项赋值）：
    hygiene 的级联闸门按"函数体里有没有 ``env['MPLBACKEND'] = 'Agg'`` 这种**赋值**"
    判 pinned；写成 `dict(os.environ, MPLBACKEND='Agg')` 会被当成 `dict(os.environ)`
    一类判 unpinned（宿主若把 MPLBACKEND 设成 Qt5Agg/TkAgg，子进程就会真开窗抢鼠标）。
    """
    env = dict(os.environ)
    env['MPLBACKEND'] = 'Agg'
    env['MPLTWEAK_LANG'] = 'zh'
    env['PYTHONIOENCODING'] = 'utf-8'
    env['PYTHONUTF8'] = '1'
    env['PYTHONPATH'] = SRC
    return env


def run(cmd, cwd, py=None, timeout=300):
    try:
        r = subprocess.run([py or PY] + list(cmd), cwd=cwd, env=_env(),
                           capture_output=True, text=True, encoding='utf-8',
                           errors='replace', timeout=timeout)
        return r.returncode, r.stdout or '', r.stderr or ''
    except subprocess.TimeoutExpired:
        return None, '', 'TIMEOUT %ss' % timeout


def has_cartopy(py):
    rc, _so, _se = run(['-c', 'import cartopy'], tempfile.gettempdir(), py=py)
    return rc == 0


def pick_cartopy_py():
    forced = os.environ.get('MPLTWEAK_CARTOPY_PY')
    if forced and forced.lower() == 'none':
        return None, 'MPLTWEAK_CARTOPY_PY=none（自检"缺 cartopy 时是否 skip 安全"）'
    cand = [p for p in (forced, PY, KEYAN_PY) if p]
    tried = []
    for py in cand:
        if py != PY and not os.path.isfile(py):
            continue
        if has_cartopy(py):
            return py, ''
        tried.append(py)
    return None, ('找不到装了 cartopy 的解释器（试过：%s；可用 MPLTWEAK_CARTOPY_PY 指定）'
                  % (', '.join(tried) or '无'))


def new_case(kind):
    """一份**全新**夹具（每个场景一份，避免"字节变了没有"被判歪）。"""
    d = tempfile.mkdtemp(prefix='t20_exotic_%s_' % kind)
    script = os.path.join(d, 'fig.py')
    io.open(script, 'w', encoding='utf-8', newline='\n').write(FIXTURES[kind])
    return d, script


def read(p):
    with io.open(p, encoding='utf-8') as f:
        return f.read()


def describe(script, d, py, out='p.json'):
    rc, so, se = run(['-m', 'mpltweak', 'describe', script, '-o', out], d, py=py)
    path = os.path.join(d, out)
    if rc != 0 or not os.path.isfile(path):
        return None, (so + se)[-300:]
    try:
        return json.load(io.open(path, encoding='utf-8')), ''
    except ValueError as e:
        return None, str(e)


def apply_params(script, d, py, pj, mutate, name='pp.json'):
    params = json.loads(json.dumps(pj))
    mutate(params)
    pp = os.path.join(d, name)
    io.open(pp, 'w', encoding='utf-8').write(json.dumps(params, ensure_ascii=False))
    before = read(script)
    rc, so, se = run(['-m', 'mpltweak', 'apply', script, '--params', pp,
                      '--write', '--json'], d, py=py)
    out = {'rc': rc, 'stdout': so, 'stderr': se, 'changed': read(script) != before,
           'src': read(script), 'json': {}}
    try:
        out['json'] = json.loads(so)
    except ValueError:
        out['json'] = {}
    res = (out['json'].get('results') or out['json'].get('files') or [{}])[0]
    out['reason'] = res.get('reason')
    out['error_code'] = res.get('error_code')
    out['changes'] = res.get('changes') or []
    out['pending'] = (res.get('pending') or []) + (res.get('inplace_pending') or [])
    out['warnings'] = [str(w) for w in (res.get('warnings') or [])]
    blob = so + se
    out['traceback'] = bool(TRACE_RE.search(blob))
    out['human'] = bool(HUMAN_RE.search(blob) and CJK.search(blob)) or bool(
        CJK.search(str(res.get('error') or '')))
    return out


def axes_of(pj):
    return (pj or {}).get('axes') or []


def spines_of(pj, idx=0):
    axes = axes_of(pj)
    return (axes[idx].get('spines') if idx < len(axes) else None) or {}


def classify_outcome(r, landed):
    """把一个变更尝试归到 landed / refused / disclosed / silent（见模块 docstring）。

    `landed` 由调用方给：**源码里真的写出了目标语句**（必要时再看读回）。
    **不用 `changes` 判落地** —— 块通道落地时 `changes` 是空的（harness 在 t17 实测过），
    拿它判会把"写成功"误判成"没写"。
    """
    if r['rc'] in (1, 2):
        if (not r['changed']) and r['human']:
            return 'refused', 'rc=%s reason=%r' % (r['rc'], r['reason'])
        return 'bad-refuse', ('rc=%s changed=%r human=%r'
                              % (r['rc'], r['changed'], r['human']))
    if r['rc'] != 0:
        return 'error', 'rc=%s' % r['rc']
    if landed:
        return 'landed', ('changes=%s pending=%s'
                          % (r['changes'][:3], r['pending'][:3]))
    if r['reason'] == 'best_effort' or r['pending'] or r['warnings']:
        return 'disclosed', ('reason=%r pending=%s warn=%d'
                             % (r['reason'], r['pending'][:3], len(r['warnings'])))
    # rc=0、字节没变、却报 no_change：参数明明与代码不同 ⇒ 静默丢弃
    return 'silent', ('reason=%r changed=%r changes=%s'
                      % (r['reason'], r['changed'], r['changes']))


def assert_no_illegal_code(shape, case, script, d, py, r):
    rc, so, se = run([script], d, py=py)
    tail = [ln for ln in (se + so).splitlines() if ln.strip()][-1:]
    check('%s/%s：写回后脚本仍能跑通（不许生成非法代码）' % (shape, case), rc == 0,
          'rc=%s %s' % (rc, tail))
    check('%s/%s：apply 输出里没有 traceback（不许把异常糊给用户）' % (shape, case),
          not r['traceback'], (r['stderr'] or r['stdout']).strip()[-160:])
    return rc


def assert_spine_names_real(shape, case, src, real_spines):
    names = sorted(set(SPINE_RE.findall(src)))
    bad = [n for n in names if n not in set(real_spines or {})]
    check('%s/%s：写出的 spine 名都在真实 spines 键里（%s）'
          % (shape, case, '/'.join(sorted(real_spines or {})) or '无'), not bad,
          '写出=%s 非法=%s' % (names, bad))
    return names


# ------------------------------------------------------------------ 各形态
def case_3d():
    d, script = new_case('3d')
    try:
        rc, so, se = run([script], d)
        check('3D：夹具本身能跑通', rc == 0, (se or so)[-160:])
        pj, err = describe(script, d, PY)
        if not axes_of(pj):
            check('3D：describe rc=0 且读到 1 根轴', False, err)
            record('3D', '-', 'BLOCKED', err)
            return
        check('3D：describe 读到 1 根轴', len(axes_of(pj)) == 1, err)
        real = spines_of(pj)
        check('3D：真实 spines 键含 left/right/bottom/top（3D 有这四根，不是 KeyError 源头）',
              set(real) >= {'left', 'right', 'bottom', 'top'}, str(sorted(real)))

        # --- 只改 title 字号 10 -> 15
        d2, s2 = new_case('3d')                      # 每个场景一份新夹具
        try:
            pj2, _e = describe(s2, d2, PY)
            r = apply_params(s2, d2, PY, pj2,
                             lambda p: p['axes'][0].__setitem__('title_fontsize', 15),
                             't.json')
            check('3D/title：rc=0 且确实落盘', r['rc'] == 0 and r['changed'],
                  'rc=%s changed=%r reason=%s' % (r['rc'], r['changed'], r['reason']))
            if r['rc'] == 0:
                assert_no_illegal_code('3D', 'title', s2, d2, PY, r)
                pj3, err3 = describe(s2, d2, PY, 'p3.json')
                got = (axes_of(pj3) or [{}])[0].get('title_fontsize')
                check('3D/title：读回是 15.0（写对了，不只是"写成功"）', got == 15.0,
                      'got=%r %s' % (got, err3))
                assert_spine_names_real('3D', 'title', r['src'], real)
                record('3D', 'title 10->15', 'PASS' if got == 15.0 else 'FAIL',
                       'rc=0 got=%r' % (got,))
            else:
                record('3D', 'title 10->15', 'BLOCKED(rc=%s)' % r['rc'],
                       'reason=%r' % (r['reason'],))
        finally:
            shutil.rmtree(d2, ignore_errors=True)

        # --- pos：3D 的位置由 aspect 重排决定
        d3, s3 = new_case('3d')
        try:
            pj3, _e = describe(s3, d3, PY)
            r = apply_params(s3, d3, PY, pj3,
                             lambda p: p['axes'][0].__setitem__(
                                 'pos', [0.20, 0.15, 0.60, 0.70]), 'pos.json')
            outcome, why = classify_outcome(
                r, 'set_position(' in r['src'] and r['changed'])
            if outcome == 'landed':
                check('3D/pos：落地时必须写 set_position 且重跑 rc=0',
                      'set_position(' in r['src'], r['src'][-160:])
                assert_no_illegal_code('3D', 'pos', s3, d3, PY, r)
                assert_spine_names_real('3D', 'pos', r['src'], real)
                record('3D', 'pos', 'PASS(landed)', why)
            elif outcome == 'refused':
                assert_no_illegal_code('3D', 'pos', s3, d3, PY, r)
                record('3D', 'pos', 'PASS(refused)', why)
            elif outcome == 'disclosed':
                known_gap('3D/pos：没落地时必须说清（现在只给了 best_effort/pending）',
                          False, why)
                assert_no_illegal_code('3D', 'pos', s3, d3, PY, r)
                record('3D', 'pos', 'GAP(disclosed)', why)
            else:
                check('3D/pos：改 pos 必须"写对 / fail-closed / 明确说没落地"，'
                      '不许静默丢弃（当前：%s）' % outcome, False, why)
                record('3D', 'pos', 'FAIL(%s)' % outcome, why)
        finally:
            shutil.rmtree(d3, ignore_errors=True)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def case_polar():
    d, script = new_case('polar')
    try:
        rc, so, se = run([script], d)
        check('polar：夹具本身能跑通', rc == 0, (se or so)[-160:])
        pj, err = describe(script, d, PY)
        if not axes_of(pj):
            check('polar：describe rc=0 且读到 1 根轴', False, err)
            record('polar', '-', 'BLOCKED', err)
            return
        check('polar：describe 读到 1 根轴', len(axes_of(pj)) == 1, err)
        real = spines_of(pj)
        check('polar：真实 spines 键含 polar、且**没有** left/right（与 3D 不同）',
              'polar' in real and 'left' not in real, str(sorted(real)))

        # --- 只改 title
        d2, s2 = new_case('polar')
        try:
            pj2, _e = describe(s2, d2, PY)
            r = apply_params(s2, d2, PY, pj2,
                             lambda p: p['axes'][0].__setitem__('title_fontsize', 15),
                             't.json')
            check('polar/title：rc=0 且确实落盘', r['rc'] == 0 and r['changed'],
                  'rc=%s changed=%r reason=%s' % (r['rc'], r['changed'], r['reason']))
            if r['rc'] == 0:
                assert_no_illegal_code('polar', 'title', s2, d2, PY, r)
                pj3, err3 = describe(s2, d2, PY, 'p3.json')
                got = (axes_of(pj3) or [{}])[0].get('title_fontsize')
                check('polar/title：读回是 15.0', got == 15.0, 'got=%r %s' % (got, err3))
                assert_spine_names_real('polar', 'title', r['src'], real)
                record('polar', 'title 10->15', 'PASS' if got == 15.0 else 'FAIL',
                       'rc=0 got=%r' % (got,))
            else:
                record('polar', 'title 10->15', 'BLOCKED(rc=%s)' % r['rc'],
                       'reason=%r' % (r['reason'],))
        finally:
            shutil.rmtree(d2, ignore_errors=True)

        # --- 要求改 polar 上**不存在**的 spines['left']
        d4, s4 = new_case('polar')
        try:
            pj4, _e = describe(s4, d4, PY)

            def mut_left(p):
                sp = dict(p['axes'][0].get('spines') or {})
                sp['left'] = True
                p['axes'][0]['spines'] = sp

            r = apply_params(s4, d4, PY, pj4, mut_left, 'sp.json')
            names = assert_spine_names_real('polar', 'spines[left]', r['src'], real)
            check("polar/spines[left]：**不许**写出 spines['left']（polar 没有这根，"
                  "写出来就是运行期 KeyError）", 'left' not in names, '写出=%s' % (names,))
            outcome, why = classify_outcome(
                r, bool(SPINE_RE.findall(r['src'])) and r['changed'])
            if outcome in ('landed', 'refused', 'disclosed'):
                assert_no_illegal_code('polar', 'spines[left]', s4, d4, PY, r)
                record('polar', "spines['left'] 请求（该轴没有）",
                       'PASS(%s)' % outcome, why)
            else:
                known_gap("polar/spines[left]：请求了该轴没有的 spine，必须说清"
                          "（现在报 no_change = 静默丢弃）", False, why)
                record('polar', "spines['left'] 请求（该轴没有）", 'GAP(silent)', why)
        finally:
            shutil.rmtree(d4, ignore_errors=True)

        # --- pos
        d5, s5 = new_case('polar')
        try:
            pj5, _e = describe(s5, d5, PY)
            r = apply_params(s5, d5, PY, pj5,
                             lambda p: p['axes'][0].__setitem__(
                                 'pos', [0.20, 0.15, 0.60, 0.70]), 'pos.json')
            outcome, why = classify_outcome(
                r, 'set_position(' in r['src'] and r['changed'])
            if outcome == 'landed':
                check('polar/pos：落地时必须写 set_position 且重跑 rc=0',
                      'set_position(' in r['src'], r['src'][-160:])
                assert_no_illegal_code('polar', 'pos', s5, d5, PY, r)
                assert_spine_names_real('polar', 'pos', r['src'], real)
                record('polar', 'pos', 'PASS(landed)', why)
            elif outcome == 'refused':
                assert_no_illegal_code('polar', 'pos', s5, d5, PY, r)
                record('polar', 'pos', 'PASS(refused)', why)
            elif outcome == 'disclosed':
                known_gap('polar/pos：没落地时必须说清', False, why)
                record('polar', 'pos', 'GAP(disclosed)', why)
            else:
                known_gap('polar/pos：改 pos 必须"写对 / fail-closed / 明确说没落地"，'
                          '不许静默丢弃（当前：%s）' % outcome, False, why)
                record('polar', 'pos', 'GAP(%s)' % outcome, why)
        finally:
            shutil.rmtree(d5, ignore_errors=True)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def case_geo(py):
    d, script = new_case('geo')
    try:
        rc, so, se = run([script], d, py=py)
        check('GeoAxes：夹具本身能跑通（cartopy 解释器）', rc == 0, (se + so)[-200:])
        pj, err = describe(script, d, py)
        if not axes_of(pj):
            check('GeoAxes：describe rc=0 且读到 1 根轴', False, err)
            record('GeoAxes', '-', 'BLOCKED', err)
            return
        a0 = axes_of(pj)[0]
        check('GeoAxes：describe 读到 1 根轴', True)
        check('GeoAxes：aspect 锁定被认出来（aspect_locked 或 aspect≠auto）',
              bool(a0.get('aspect_locked')) or a0.get('aspect') not in (None, 'auto'),
              'locked=%r aspect=%r' % (a0.get('aspect_locked'), a0.get('aspect')))
        lon0 = a0.get('xlim')
        check('GeoAxes：describe 读到经度范围（≈[100,130]）',
              bool(lon0) and abs(lon0[0] - 100.0) < 1.0 and abs(lon0[1] - 130.0) < 1.0,
              'xlim=%r' % (lon0,))
        sp = spines_of(pj)
        check("GeoAxes：describe 认出 geo spine（spines['geo']=True）",
              sp.get('geo') is True, str(sorted(sp.items())))

        # --- 经纬度范围：改 xlim（PlateCarree 上就是经度）
        d2, s2 = new_case('geo')
        try:
            pj2, _e = describe(s2, d2, py)
            r = apply_params(s2, d2, py, pj2,
                             lambda p: p['axes'][0].__setitem__('xlim', [110.0, 120.0]),
                             'gx.json')
            outcome, why = classify_outcome(
                r, bool(re.search(r'set_xlim\(\s*110', r['src']))
                and r['changed'])
            if outcome == 'landed':
                check('GeoAxes/xlim：源码里写出 set_xlim(110, 120)（是度数，不是像素）',
                      bool(re.search(r'set_xlim\(\s*110(\.0)?\s*,\s*120(\.0)?\s*\)',
                                     r['src'])),
                      [ln.strip() for ln in r['src'].splitlines() if 'xlim' in ln])
                assert_no_illegal_code('GeoAxes', 'xlim', s2, d2, py, r)
                pj3, err3 = describe(s2, d2, py, 'p3.json')
                got = (axes_of(pj3) or [{}])[0].get('xlim')
                check('GeoAxes/xlim：读回 ≈[110,120]（经纬度真的落对了，不是只写了代码）',
                      bool(got) and abs(got[0] - 110.0) < 2.0
                      and abs(got[1] - 120.0) < 2.0, 'got=%r %s' % (got, err3))
                record('GeoAxes', 'xlim(经度) 110-120', 'PASS' if got else 'FAIL',
                       'rc=0 got=%r' % (got,))
            else:
                check('GeoAxes/xlim：经度范围必须写对或明确拒写（当前：%s）' % outcome,
                      outcome == 'refused', why)
                record('GeoAxes', 'xlim(经度) 110-120',
                       'PASS(%s)' % outcome if outcome == 'refused' else 'FAIL(%s)' % outcome,
                       why)
        finally:
            shutil.rmtree(d2, ignore_errors=True)

        # --- pos
        d3, s3 = new_case('geo')
        try:
            pj3, _e = describe(s3, d3, py)
            r = apply_params(s3, d3, py, pj3,
                             lambda p: p['axes'][0].__setitem__(
                                 'pos', [0.20, 0.15, 0.60, 0.70]), 'gp.json')
            outcome, why = classify_outcome(
                r, 'set_position(' in r['src'] and r['changed'])
            if outcome == 'landed':
                check('GeoAxes/pos：落地时必须写 set_position 且重跑 rc=0',
                      'set_position(' in r['src'], r['src'][-160:])
                assert_no_illegal_code('GeoAxes', 'pos', s3, d3, py, r)
                record('GeoAxes', 'pos', 'PASS(landed)', why)
            elif outcome == 'refused':
                assert_no_illegal_code('GeoAxes', 'pos', s3, d3, py, r)
                record('GeoAxes', 'pos', 'PASS(refused)', why)
            elif outcome == 'disclosed':
                known_gap('GeoAxes/pos：没落地时必须说清', False, why)
                record('GeoAxes', 'pos', 'GAP(disclosed)', why)
            else:
                known_gap('GeoAxes/pos：改 pos 必须"写对 / fail-closed / 明确说没落地"，'
                          '不许静默丢弃（当前：%s）' % outcome, False, why)
                record('GeoAxes', 'pos', 'GAP(%s)' % outcome, why)
        finally:
            shutil.rmtree(d3, ignore_errors=True)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def case_geo_two_panels(py):
    d, script = new_case('geo2')
    try:
        rc, so, se = run([script], d, py=py)
        check('GeoAxes×2：夹具本身能跑通', rc == 0, (se + so)[-200:])
        pj, err = describe(script, d, py)
        axes = axes_of(pj)
        check('GeoAxes×2：describe 读到 2 根轴，且 fig.axes 顺序与源码建轴点一致'
              '（cell 1,2,0,0 / 1,2,0,1）',
              len(axes) == 2 and axes[0].get('cell') == [1, 2, 0, 0]
              and axes[1].get('cell') == [1, 2, 0, 1],
              'cells=%r %s' % ([a.get('cell') for a in axes], err))
        if len(axes) != 2:
            record('GeoAxes×2', '-', 'BLOCKED', err)
            return
        lon1 = axes[0].get('xlim')
        r = apply_params(script, d, py, pj,
                         lambda p: p['axes'][1].__setitem__('xlim', [10.0, 20.0]),
                         'g2.json')
        check('GeoAxes×2：只改 ax2 ⇒ rc=0 且落盘', r['rc'] == 0 and r['changed'],
              'rc=%s changed=%r reason=%r code=%r'
              % (r['rc'], r['changed'], r['reason'], r['error_code']))
        if r['rc'] == 0:
            assert_no_illegal_code('GeoAxes×2', 'ax2.xlim', script, d, py, r)
        pj2, err2 = describe(script, d, py, 'p2.json')
        axes2 = axes_of(pj2)
        got0 = axes2[0].get('xlim') if axes2 else None
        got1 = axes2[1].get('xlim') if len(axes2) > 1 else None
        check('GeoAxes×2：ax1 的经度范围**一个数都没动**（只改目标轴，不许连坐）',
              got0 == lon1, 'before=%r after=%r' % (lon1, got0))
        check('GeoAxes×2：ax2 的经度范围落到 ≈[10,20]',
              bool(got1) and abs(got1[0] - 10.0) < 2.0 and abs(got1[1] - 20.0) < 2.0,
              'got=%r %s' % (got1, err2))
        check('GeoAxes×2：写回后 fig.axes 顺序不变（cell 仍是 1,2,0,0 / 1,2,0,1）',
              len(axes2) == 2 and axes2[0].get('cell') == [1, 2, 0, 0]
              and axes2[1].get('cell') == [1, 2, 0, 1],
              'cells=%r' % ([a.get('cell') for a in axes2],))
        record('GeoAxes×2', '只改 ax2.xlim',
               'PASS' if got0 == lon1 and got1 else 'FAIL',
               'ax1=%r ax2=%r' % (got0, got1))
    finally:
        shutil.rmtree(d, ignore_errors=True)


def main():
    t0 = time.time()
    print('== T20 非常规轴形态：Axes3D / PolarAxes / cartopy GeoAxes')
    print('   解释器：%s；STRICT=%s' % (PY, STRICT))
    case_3d()
    case_polar()
    cpy, why = pick_cartopy_py()
    if cpy:
        print('   cartopy 解释器：%s' % cpy)
        case_geo(cpy)
        case_geo_two_panels(cpy)
    else:
        skip_check('GeoAxes 三组用例（xlim 经度 / pos / 双面板身份）', why)
        record('GeoAxes', '-', 'SKIP', why)

    print()
    print('== 三形态 × 结果')
    print('| 形态 | 场景 | 结局 | 备注 |')
    print('|---|---|---|---|')
    for shape, case, verdict, extra in rows:
        print('| %s | %s | %s | %s |'
              % (_gbk_safe(shape), _gbk_safe(case), _gbk_safe(verdict),
                 _gbk_safe(extra)[:110]))
    print()
    print('耗时 %.1f 秒；FAIL %d / GAP %d / SKIP %d'
          % (time.time() - t0, fail, gap, skip))
    if gap and not STRICT:
        print('（GAP = 已知缺口：照样逐条打印、进报告；MPLTWEAK_STRICT_EXOTIC=1 时升级为 FAIL）')
    print('FAILED: %d' % fail if fail else 'ALL PASS')
    return 1 if fail else 0


if __name__ == '__main__':
    sys.exit(main())
