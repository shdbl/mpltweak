# -*- coding: utf-8 -*-
"""T15 / 0.1.18：边界与兼容 —— 五条"用户真能撞到、我们却从没测"的入口。

来源：CC 第三轮复核提的"第六件事"清单。五条互不相干，但共同点是**都不在 happy path 上**：

1. **畸形参数文件**：字段乱序 / 非法值（字符串塞数值位、空对象、null）/ 缺键 / 多余键
   ⇒ 必须 rc 非 0 或优雅降级并给人话，**绝不许 traceback**（栈是给开发者的，用户看不懂）。
2. **多图切换**：`plt.figure(1)…plt.figure(2)…` 之后 apply `foo.fig0.json`
   ⇒ 改动必须落到**第 0 张图**（`plt.gcf()` 这时指向 fig2，写错图是静默事故）。
3. **插入点语法安全**：脚本里有 f-string / 三引号多行字符串 / lambda / 嵌套括号 / 行内分号
   ⇒ apply 之后 `ast.parse()` 必须通过，且**非目标区域逐字节不变**（写回最容易在字符串里劈叉）。
4. **`--diff` 与实际写入一致**：预览说改哪儿、真写就必须只改哪儿。
   判据用最硬的两条：`preview_sha256` == 真写后的文件 sha256；diff 条数 == 实际变化行数。
5. **向后兼容**：旧版参数文件（缺后来新增的身份键）⇒ 降级到 `index` 对齐或明确报错，
   **不得 KeyError**。

判据里反复出现的三条底线（所有用例共用）：
  * 输出里不许出现 `Traceback`（人话 vs 栈，这是 T13 立的规矩）；
  * rc≠0 时**脚本一个字节都不许变**（拒绝就得是干净的拒绝）；
  * rc 只许 0 / 1 / 2（方案 A：3 已退役）。

调用方式
--------
`python -m pytest -q tests/test_boundaries.py`（也可 `python tests/test_boundaries.py`）。
"""
import ast
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile

import pytest

# GBK 控制台兜底（与 test_cli_output.py 同款）
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(errors='replace')
    except (AttributeError, ValueError, OSError):
        pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, 'src')
PY = sys.executable


# =====================================================================
# §1 脚手架
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


def _write(path, text):
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        f.write(text)


def _read_bytes(path):
    with open(path, 'rb') as f:
        return f.read()


def _write_json(path, payload):
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(payload, f, ensure_ascii=False)


_SCRATCH_SEQ = [0]


def _scratch_dir():
    base = tempfile.gettempdir()
    while True:
        _SCRATCH_SEQ[0] += 1
        d = os.path.join(base, 'mpltweak_bound_%d_%d'
                         % (os.getpid(), _SCRATCH_SEQ[0]))
        try:
            os.makedirs(d)
            return d
        except FileExistsError:
            continue


@pytest.fixture()
def work():
    d = _scratch_dir()
    try:
        yield d
    finally:
        shutil.rmtree(d, ignore_errors=True)


#: 所有用例共用的三条底线
def _assert_no_traceback(rc, out, err, what):
    blob = out + err
    assert 'Traceback' not in blob, (
        '%s 抛栈了（栈是给开发者的，用户看不懂）：rc=%d\n%s'
        % (what, rc, blob[-1500:]))
    assert rc in (0, 1, 2), (
        '%s 的退出码 %d 不在 0/1/2 里（方案 A：3 已退役）' % (what, rc))


def _describe(d, script, out_name, *extra):
    rc, out, err = _run(d, 'describe', script, '-o', out_name, '--compact', *extra)
    assert rc == 0, ('describe 失败 rc=%d\n%s\n%s' % (rc, out[-600:], err[-600:]))
    with open(os.path.join(d, out_name), encoding='utf-8') as f:
        return json.load(f)


def _apply(d, script, params, *extra):
    """跑一次 apply（--json），返回 (rc, payload 或 None, stdout, stderr)。"""
    rc, out, err = _run(d, 'apply', script, '--params', params, '--json', *extra)
    try:
        payload = json.loads(out)
    except ValueError:
        payload = None
    return rc, payload, out, err


def _entry(payload):
    if not payload:
        return {}
    files = payload.get('files') or []
    return files[0] if files else {}


def _delta_pos(params, k, dx=0.02):
    """把第 k 根轴的 pos[0] 挪 dx（4 位小数，与写回块的打印精度对齐）。"""
    out = json.loads(json.dumps(params))
    for ax in out.get('axes') or []:
        if int(ax['index']) == k:
            pos = list(ax['pos'])
            pos[0] = round(float(pos[0]) + dx, 4)
            ax['pos'] = pos
            return out, pos
    raise AssertionError('参数里没有 index=%d 的轴' % k)


# =====================================================================
# §2 夹具脚本
# =====================================================================
_ONE_FIG = '''\
import matplotlib

matplotlib.use('Agg')
import matplotlib.pyplot as plt

fig = plt.figure(figsize=(4, 3))
ax = fig.add_axes([0.10, 0.10, 0.40, 0.40])
ax.plot([0, 1], [0, 1])
fig.savefig('out.png')
'''

#: 两张图：`plt.gcf()` 在结尾指向 fig2（这正是"写错图"的温床）
_TWO_FIGS = '''\
import matplotlib

matplotlib.use('Agg')
import matplotlib.pyplot as plt

fig1 = plt.figure(1, figsize=(4, 3))
ax1 = fig1.add_axes([0.10, 0.10, 0.40, 0.40])
ax1.plot([0, 1], [0, 1])

fig2 = plt.figure(2, figsize=(5, 4))
ax2 = fig2.add_axes([0.20, 0.20, 0.50, 0.50])
ax2.plot([0, 1], [1, 0])
fig2.savefig('out.png')
'''

#: 语法地雷：f-string / 三引号多行 / lambda / 嵌套括号 / 行内分号
_TRICKY = '''\
"""模块文档：这里有一行看起来像坐标的数字 [0.10, 0.10, 0.40, 0.40] 也不许被改。"""
import matplotlib

matplotlib.use('Agg')
import matplotlib.pyplot as plt

TITLE = 'fixed title'
TPL = f'panel-{"a"}-{1 + 1}'
BIG_TEXT = """
多行字符串里也有 add_axes([0.99, 0.99, 0.01, 0.01]) 这样的字样
第二行：f{TPL} 与 lambda x: x * 2
"""

fig = plt.figure(figsize=(4, 3))
ax = fig.add_axes([0.10, 0.10, 0.40, 0.40])
ax.plot([0, 1], [0, 1])
ax.set_title(TPL + str((lambda x: x * 2)(2)))
label = 'inline'; ax.set_xlabel(label); ax.set_ylabel(BIG_TEXT.strip()[:4])
fig.savefig('out.png')
'''

#: 合法但少见：`add_axes((x, y), width=…, height=…)` —— 0.1.18 的采集器会崩在这里
#: （`Bbox.from_bounds()` 缺两个参数），见报告 F4。这里钉的是"崩得体面"：
#: rc=2 + error_code + 人话 + 不 traceback + 不动文件。
_UNUSUAL_ADD_AXES = '''\
import matplotlib

matplotlib.use('Agg')
import matplotlib.pyplot as plt

fig = plt.figure(figsize=(4, 3))
ax = fig.add_axes((0.10, 0.10), width=0.40, height=0.40)
ax.plot([0, 1], [0, 1])
fig.savefig('out.png')
'''


# =====================================================================
# §3 第五件事要用的"旧版参数文件"（向后兼容）
#
# 版本沿革：v4 起 axes[] 里除了 `index` / `pos` 还陆续多了 `cell` / `clim` /
# `cmap` / `aspect_locked` 等只读观测量；t14 之后还可能再加**身份键**（`role`）。
# 旧版参数文件 = 只带**最小集**（`index` + `pos`）甚至缺 `fig_index` / `n_figs`。
# 要求：降级到 `index` 对齐，或者明确报错；**不许 KeyError**。
# =====================================================================
def _minimal_params(**over):
    p = {'version': 4, 'script': 'one.py', 'fig_index': 0, 'n_figs': 1,
         'axes': [{'index': 0, 'pos': [0.12, 0.11, 0.40, 0.40]}]}
    p.update(over)
    return p


# =====================================================================
# §4 第一件：畸形参数文件（四类各 ≥2 例）
# =====================================================================
#: name -> (payload, 期望档次)。`lenient` = 允许 rc=0（参数够用就照办），
#: `strict` = 必须 rc≠0（这份参数表达不出用户想要的改动）。
_DIRTY_CASES = {
    # (a) 字段乱序：键的先后不是语义 ⇒ 应当照办
    'reordered_keys': (
        {'axes': [{'pos': [0.12, 0.11, 0.40, 0.40], 'index': 0}],
         'n_figs': 1, 'fig_index': 0, 'script': 'one.py', 'version': 4},
        'lenient'),
    'reordered_axis_keys': (
        {'version': 4, 'script': 'one.py', 'fig_index': 0, 'n_figs': 1,
         'axes': [{'pos': [0.12, 0.11, 0.40, 0.40], 'index': 0}]},
        'lenient'),
    # (b) 非法值：字符串塞进数值位 / 空对象 / null
    'string_in_pos': (
        {'version': 4, 'script': 'one.py', 'fig_index': 0, 'n_figs': 1,
         'axes': [{'index': 0, 'pos': ['abc', 0.1, 0.4, 0.4]}]},
        'strict'),
    'string_in_fig_index': (
        {'version': 4, 'script': 'one.py', 'fig_index': 'zero', 'n_figs': 1,
         'axes': [{'index': 0, 'pos': [0.12, 0.11, 0.40, 0.40]}]},
        # t15-F2 裁决（t16 复核后维持）：`fig_index` 读不成整数就**一律** `bad_params`
        # 并点名收到的值 —— 不许"单图时悄悄退回 0"。理由：单图退化只是巧合，
        # 而"猜一个图号"和"报没有这张图（no_fig）"都是用户无法从输出里分辨的猜测；
        # 多图形态另有专测（见 §5 末尾）。
        'strict'),
    'empty_axes_object': (
        {'version': 4, 'script': 'one.py', 'fig_index': 0, 'n_figs': 1,
         'axes': {}},
        'strict'),
    # 空对象必须 red；null 会被当成"没要求"（记为 noop，见 §8 的 F2）
    'null_pos': (
        {'version': 4, 'script': 'one.py', 'fig_index': 0, 'n_figs': 1,
         'axes': [{'index': 0, 'pos': None}]},
        'noop'),
    # (c) 缺键：没有 axes / 只有一个空壳
    'missing_axes': ({'version': 4, 'script': 'one.py'}, 'strict'),
    'empty_object': ({}, 'strict'),
    # (d) 多余键：不认识但要容忍（不许因为多一个键就当"没有这张图"）
    'extra_top_key': (
        {'version': 4, 'script': 'one.py', 'fig_index': 0, 'n_figs': 1,
         'axes': [{'index': 0, 'pos': [0.12, 0.11, 0.40, 0.40]}],
         'zzz_unknown': {'nested': 1}},
        'lenient'),
    'extra_axis_key': (
        {'version': 4, 'script': 'one.py', 'fig_index': 0, 'n_figs': 1,
        'axes': [{'index': 0, 'pos': [0.12, 0.11, 0.40, 0.40],
                  'role': 'primary', 'zzz': None}]},
        'lenient'),
}


@pytest.mark.parametrize('name', sorted(_DIRTY_CASES))
def test_dirty_params_never_crash(work, name):
    """畸形参数：rc 只许 0/1/2、不许 traceback、拒了就得字节不动。

    `lenient` 的用例允许 rc=0（参数够用就照办），但**必须真的把改动写下去**；
    `strict` 的用例必须 rc≠0 —— 一份表达不出"要改什么"的参数，静默成功是最坏的。
    """
    payload, mode = _DIRTY_CASES[name]
    _write(os.path.join(work, 'one.py'), _ONE_FIG)
    params = name + '.json'
    _write_json(os.path.join(work, params), payload)
    before = _read_bytes(os.path.join(work, 'one.py'))

    rc, p, out, err = _apply(work, 'one.py', params, '--write')
    _assert_no_traceback(rc, out, err, '脏参数 %s' % name)
    code = (p or {}).get('error_code')
    entry = _entry(p)

    if mode == 'strict':
        assert rc != 0, (
            '(%s) 这份参数表达不出"要改什么"，却给了 rc=0（静默成功）：\n%s'
            % (name, out[-800:]))
        assert _read_bytes(os.path.join(work, 'one.py')) == before, (
            '(%s) 拒绝了却改了脚本 —— 拒绝必须干净' % name)
        assert code or (err or out).strip(), (
            '(%s) 拒了但既没有 error_code 也没有人话' % name)
        assert err.strip() or 'error' in (out or '').lower(), (
            '(%s) 失败信息没走 stderr / 也没有 error 字样：%r' % (name, out[:200]))
    elif mode == 'noop':
        # 允许"把 null 当成没要求"这种降级，但**必须是无操作**：字节不变、
        # applied=false、而且不许拿"参数已一致"以外的话糊弄（这一点记在 F2 里）。
        assert _read_bytes(os.path.join(work, 'one.py')) == before, (
            '(%s) `pos: null` 居然改动了脚本：%s'
            % (name, _read_bytes(os.path.join(work, 'one.py'))
               .decode('utf-8', 'replace')[-600:]))
        assert not entry.get('applied'), (
            '(%s) `pos: null` 被当成"写了"（%r）' % (name, entry))
        assert rc == 0, ('(%s) 降级后应当 rc=0（无操作），实际 %d' % (name, rc))
    else:
        assert rc in (0, 1), (name, rc, out[-400:], err[-400:])
        if rc == 0:
            assert entry.get('applied'), (
                '(%s) rc=0 却 applied=false（参数明明够用）：%r'
                % (name, entry.get('reason')))
            after = _describe(work, 'one.py', 'after.json')
            got = [ax['pos'] for ax in after['axes'] if ax['index'] == 0][0]
            assert abs(got[0] - 0.12) < 1e-9, (
                '(%s) 说写了，但读回来的 pos 不是要求的值：%r' % (name, got))
        else:
            assert _read_bytes(os.path.join(work, 'one.py')) == before


def test_dirty_params_are_reported_with_a_human_line(work):
    """挑最典型的两种脏法，确认**人话**说得清楚（不是只给一个错误码）。"""
    _write(os.path.join(work, 'one.py'), _ONE_FIG)
    before = _read_bytes(os.path.join(work, 'one.py'))
    for name, payload in (
            ('string_in_pos', {'version': 4, 'script': 'one.py',
                               'fig_index': 0, 'n_figs': 1,
                               'axes': [{'index': 0, 'pos': ['abc', 0, 0, 0]}]}),
            ('missing_axes', {'version': 4, 'script': 'one.py'})):
        params = name + '.json'
        _write_json(os.path.join(work, params), payload)
        rc, p, out, err = _apply(work, 'one.py', params, '--write')
        _assert_no_traceback(rc, out, err, name)
        assert rc != 0, name
        blob = (err or out)
        assert any(k in blob for k in ('pos', 'axes', 'param', '参数', 'numbers',
                                       'number')), (
            '(%s) 人话里没点出是哪儿不对：%r' % (name, blob[-500:]))
        assert _read_bytes(os.path.join(work, 'one.py')) == before


# =====================================================================
# §5 第二件：多图切换（apply foo.fig0.json 必须落到第 0 张图）
# =====================================================================
def test_all_figs_split_files_target_fig0_only(work):
    """`describe --all-figs` 产出 `foo.fig0.json` / `foo.fig1.json`；
    对 `foo.fig0.json` 做写回 ⇒ **只有 fig0 变**，fig1 一个是字段都不许动。

    这是"多图切换"的核心风险：脚本结尾 `plt.gcf()` 指向 fig2，写回若按 gcf 定位，
    改动就会落到错图上 —— 而且 rc=0、人读输出还说"写好了"。
    """
    _write(os.path.join(work, 'two.py'), _TWO_FIGS)
    _describe(work, 'two.py', 'two.json', '--all-figs')
    for i in (0, 1):
        assert os.path.exists(os.path.join(work, 'two.fig%d.json' % i)), (
            'describe --all-figs 没产出 two.fig%d.json（命名口径变了？）' % i)

    before0 = json.load(open(os.path.join(work, 'two.fig0.json'),
                             encoding='utf-8'))
    before1 = json.load(open(os.path.join(work, 'two.fig1.json'),
                             encoding='utf-8'))
    want, new_pos = _delta_pos(before0, 0)
    _write_json(os.path.join(work, 'p0.json'), want)

    rc, p, out, err = _apply(work, 'two.py', 'p0.json', '--write')
    _assert_no_traceback(rc, out, err, '多图切换')
    assert rc == 0 and _entry(p).get('applied'), (
        '多图写回没成功：rc=%d reason=%r\n%s\n%s'
        % (rc, _entry(p).get('reason'), out[-800:], err[-800:]))

    after0 = _describe(work, 'two.py', 'a0.json', '--fig', '0')
    after1 = _describe(work, 'two.py', 'a1.json', '--fig', '1')
    got0 = [ax['pos'] for ax in after0['axes'] if ax['index'] == 0][0]
    assert abs(got0[0] - new_pos[0]) < 1e-9, (
        '改动没落到 fig0：要求 %r，读回 %r' % (new_pos, got0))
    got1 = [ax['pos'] for ax in after1['axes'] if ax['index'] == 0][0]
    assert got1 == before1['axes'][0]['pos'], (
        'fig1 被顺带改了（写到了错图）：%r -> %r（它原本是 %r）'
        % (before1['axes'][0]['pos'], got1, before1['axes'][0]['pos']))
    # 脚本里必须点名 fig1/fig2 的定位方式，而不是指望 gcf()
    src = _read_bytes(os.path.join(work, 'two.py')).decode('utf-8')
    assert ('set_position' in src) or ('fig1.add_axes([0.12' in src
                                       or 'fig1.add_axes([0.12' in src
                                       or '0.12' in src), (
        '写回了，但脚本里既没有 set_position 也没有新数值（改动去哪了？）：\n%s'
        % src[-800:])
    assert '[0.20, 0.20, 0.50, 0.50]' in src, (
        'fig2 的 add_axes 字面量被改了（写到了错图）：\n%s' % src[-800:])


def test_two_figs_second_figure_still_writable(work):
    """反向对照：改 fig1（第 2 张）也要落到它身上，别串到 fig0。"""
    _write(os.path.join(work, 'two.py'), _TWO_FIGS)
    _describe(work, 'two.py', 'two.json', '--all-figs')
    before0 = json.load(open(os.path.join(work, 'two.fig0.json'),
                             encoding='utf-8'))
    before1 = json.load(open(os.path.join(work, 'two.fig1.json'),
                             encoding='utf-8'))
    want, new_pos = _delta_pos(before1, 0)
    _write_json(os.path.join(work, 'p1.json'), want)
    rc, p, out, err = _apply(work, 'two.py', 'p1.json', '--write')
    _assert_no_traceback(rc, out, err, '多图切换（fig1）')
    if rc != 0 or not _entry(p).get('applied'):
        # 写不进去也是合法结果（fail-closed），但必须说清原因、且字节不动
        assert _entry(p).get('reason') or (p or {}).get('error_code'), (
            'fig1 写回失败却没说原因：%s' % out[-600:])
        return
    after0 = _describe(work, 'two.py', 'a0.json', '--fig', '0')
    after1 = _describe(work, 'two.py', 'a1.json', '--fig', '1')
    got1 = [ax['pos'] for ax in after1['axes'] if ax['index'] == 0][0]
    got0 = [ax['pos'] for ax in after0['axes'] if ax['index'] == 0][0]
    assert abs(got1[0] - new_pos[0]) < 1e-9, (
        '改动没落到 fig1：要求 %r，读回 %r' % (new_pos, got1))
    assert got0 == before0['axes'][0]['pos'], '改 fig1 却动了 fig0：%r' % (got0,)


def test_bad_fig_index_on_two_figures_never_writes_the_wrong_one(work):
    """二图脚本 + 非数值 `fig_index`（`'zero'`）：**不许猜着写错图**。

    单图脚本里 fig_index 无关紧要，退回 0 是合理降级；但两张图时它决定写到哪张图上。
    要求：要么明确报错（rc≠0 + 人话），要么写进**唯一一张**图且值就是要求的；
    **绝不允许两张图都被写、或者写进一张而值不对**。
    （0.1.18 现状：它把 `'zero'` 当成 0 静默照办 —— 记为报告里的 F3。）
    """
    _write(os.path.join(work, 'two.py'), _TWO_FIGS)
    _describe(work, 'two.py', 'two.json', '--all-figs')
    before0 = json.load(open(os.path.join(work, 'two.fig0.json'), encoding='utf-8'))
    before1 = json.load(open(os.path.join(work, 'two.fig1.json'), encoding='utf-8'))
    bad = json.loads(json.dumps(before0))
    bad['fig_index'] = 'zero'
    for ax in bad['axes']:
        if int(ax['index']) == 0:
            ax['pos'] = [round(float(ax['pos'][0]) + 0.02, 4)] + list(ax['pos'][1:])
    _write_json(os.path.join(work, 'bad.json'), bad)

    rc, p, out, err = _apply(work, 'two.py', 'bad.json', '--write')
    _assert_no_traceback(rc, out, err, '非数值 fig_index（二图）')
    entry = _entry(p)
    print('BOUNDARY | fig_index=%r 二图 | rc=%d applied=%s reason=%s'
          % (bad['fig_index'], rc, entry.get('applied'), entry.get('reason')))
    if rc != 0 or not entry.get('applied'):
        assert entry.get('reason') or (p or {}).get('error_code') or (err or out), (
            '拒了却没说原因：%s' % out[-600:])
        return

    after0 = _describe(work, 'two.py', 'a0.json', '--fig', '0')
    after1 = _describe(work, 'two.py', 'a1.json', '--fig', '1')
    got0 = [ax['pos'] for ax in after0['axes'] if ax['index'] == 0][0]
    got1 = [ax['pos'] for ax in after1['axes'] if ax['index'] == 0][0]
    moved0 = got0 != before0['axes'][0]['pos']
    moved1 = got1 != before1['axes'][0]['pos']
    assert not (moved0 and moved1), (
        '两张图都被改了（写回在二图之间串了）：fig0=%r fig1=%r' % (got0, got1))
    assert moved0 or moved1, 'rc=0 且 applied=true，但两张图一个都没变'
    landed = got0 if moved0 else got1
    assert abs(float(landed[0]) - float(bad['axes'][0]['pos'][0])) < 1e-9, (
        '写进了某张图，但值不是参数里要求的：要求 %r，实际 %r'
        % (bad['axes'][0]['pos'], landed))


# =====================================================================
# §6 第三件：插入点语法安全（语法地雷 + 非目标区域逐字节不变）
# =====================================================================
def test_insertion_point_survives_syntactic_landmines(work):
    """f-string / 三引号 / lambda / 嵌套括号 / 行内分号：写完必须还能 parse。

    判据三条：
      ① `ast.parse()` 通过（写回不许在字符串/括号里劈叉）；
      ② 脚本里的**字符串字面量**（含那个长得像坐标的多行字符串）内容一字不差；
      ③ 目标行以外**逐字节不变**（把改过的行挖掉之后 compare 字节）。
    """
    _write(os.path.join(work, 'one.py'), _TRICKY)
    before = _read_bytes(os.path.join(work, 'one.py'))
    base = _describe(work, 'one.py', 'a.json')
    want, new_pos = _delta_pos(base, 0)
    _write_json(os.path.join(work, 'p.json'), want)

    rc, p, out, err = _apply(work, 'one.py', 'p.json', '--write')
    _assert_no_traceback(rc, out, err, '语法地雷')
    assert rc == 0 and _entry(p).get('applied'), (
        '语法地雷夹具写回失败：rc=%d reason=%r\n%s\n%s'
        % (rc, _entry(p).get('reason'), out[-800:], err[-800:]))

    after = _read_bytes(os.path.join(work, 'one.py'))
    text = after.decode('utf-8')
    try:
        ast.parse(text)
    except SyntaxError as exc:
        raise AssertionError('写回把脚本改成了语法错误：%s\n%s\n--- 写后全文 ---\n%s'
                             % (exc, str(exc.offset), text[-1500:]))

    # ② 字符串字面量一字不差（写回最容易在字符串里劈叉）
    for literal in ('"""模块文档：这里有一行看起来像坐标的数字 [0.10, 0.10, 0.40, 0.40] 也不许被改。"""',
                    '"""\n多行字符串里也有 add_axes([0.99, 0.99, 0.01, 0.01]) 这样的字样\n'
                    '第二行：f{TPL} 与 lambda x: x * 2\n"""',
                    "TPL = f'panel-{\"a\"}-{1 + 1}'",
                    "label = 'inline'",
                    "ax.set_title(TPL + str((lambda x: x * 2)(2)))"):
        assert literal in text, (
            '写回把字符串/表达式改坏了，找不到原文：%r\n写后全文尾部：\n%s'
            % (literal, text[-1200:]))

    # ③ 非目标区域逐字节不变：用 difflib 找"真正变了的地方"，逐个核对
    #    —— 只允许两种变化：目标那一行（含 add_axes / set_position），
    #       或者自动块的一整段（`# ===== auto layout ... ===== end auto layout`）。
    b_lines = before.decode('utf-8').splitlines()
    a_lines = text.splitlines()
    bad = _non_target_changes(b_lines, a_lines)
    assert not bad, (
        '非目标区域被改了（这些行既不是目标行、也不在自动块里）：\r\n%s\r\n'
        '--- 写后全文 ---\r\n%s' % ('\r\n'.join(bad[:8]), text[-1200:]))
    # 目标值确实落下去了
    after_params = _describe(work, 'one.py', 'c.json')
    got = [ax['pos'] for ax in after_params['axes'] if ax['index'] == 0][0]
    assert abs(got[0] - new_pos[0]) < 1e-9, (new_pos, got)


def test_unusual_but_legal_add_axes_form_fails_gracefully(work):
    """`add_axes((x, y), width=…, height=…)` 是 matplotlib 的合法写法，但采集器会崩。

    本单不修（归 engine）。这条钉的是**体面**：rc=2（跑不起来）+ 稳定 error_code +
    人话 + 不出 traceback + 不改文件。**发现已记录为报告里的 F4。**
    """
    _write(os.path.join(work, 'odd.py'), _UNUSUAL_ADD_AXES)
    before = _read_bytes(os.path.join(work, 'odd.py'))
    rc, out, err = _run(work, 'describe', 'odd.py', '-o', 'a.json', '--compact')
    _assert_no_traceback(rc, out, err, 'add_axes(2 元组 + kwargs)')
    print('BOUNDARY | add_axes((x,y), width=, height=) | describe rc=%d' % rc)
    if rc != 0:
        blob = out + err
        assert 'Traceback' not in blob
        assert 'collect_failed' in blob or 'error' in blob.lower(), (
            '崩了但没给 error_code / 人话：%s' % blob[-600:])
        assert _read_bytes(os.path.join(work, 'odd.py')) == before, (
            'describe 竟然改了脚本')


# =====================================================================
# §7 第四件：`--diff` 与实际写入一致
# =====================================================================
def test_diff_preview_matches_the_real_write(work):
    """预览骗人是最难查的一类：`--diff` 说改 A，真写却改了 B。

    两条硬判据：
      ① `preview_sha256` == 真写后的文件 sha256（逐字节等价，最强的一条）；
      ② diff 条目数 == 实际变化的行数，且每条的 `new` 真的出现在写后的文件里。
    另外：`--diff` 本身**不许落盘**（预览完脚本必须一字不变）。
    """
    _write(os.path.join(work, 'one.py'), _ONE_FIG)
    before = _read_bytes(os.path.join(work, 'one.py'))
    base = _describe(work, 'one.py', 'a.json')
    want, new_pos = _delta_pos(base, 0)
    _write_json(os.path.join(work, 'p.json'), want)

    rc_d, pd, out_d, err_d = _apply(work, 'one.py', 'p.json', '--diff')
    _assert_no_traceback(rc_d, out_d, err_d, '--diff 预览')
    assert _read_bytes(os.path.join(work, 'one.py')) == before, (
        '--diff 预览把脚本写了 —— 预览必须零写入')
    e_d = _entry(pd)
    diffs = e_d.get('diff') or []
    assert diffs, ('--diff 没给出任何 diff 条目（预览等于空转）：%r'
                   % (e_d.get('reason'),))
    assert e_d.get('preview_sha256'), '--diff 没给 preview_sha256，无法核对一致性'
    for d in diffs:
        assert set(d) >= {'line', 'type', 'old', 'new'}, d

    rc_w, pw, out_w, err_w = _apply(work, 'one.py', 'p.json', '--write')
    _assert_no_traceback(rc_w, out_w, err_w, '真写')
    assert rc_w == 0 and _entry(pw).get('applied'), (
        '真写失败：rc=%d reason=%r\n%s' % (rc_w, _entry(pw).get('reason'),
                                          out_w[-800:]))
    after = _read_bytes(os.path.join(work, 'one.py'))
    got_sha = hashlib.sha256(after).hexdigest()
    assert got_sha == e_d['preview_sha256'], (
        '预览与实际写入不一致：preview_sha256=%s，真写后=%s\n'
        '（这就是"预览骗人"：用户看到要改的那几行，和真正落盘的不一样）\n%s'
        % (e_d['preview_sha256'][:16], got_sha[:16],
           after.decode('utf-8', 'replace')[-800:]))

    b_lines = before.decode('utf-8').splitlines()
    a_lines = after.decode('utf-8').splitlines()
    changed_lines = [l for l in a_lines if l not in b_lines]
    assert len(changed_lines) == len(diffs), (
        'diff 说改 %d 处，实际写了 %d 行变化：\n diff=%r\n 实际=%r'
        % (len(diffs), len(changed_lines),
           [d.get('new') for d in diffs], changed_lines))
    for d in diffs:
        assert d['new'] in a_lines, (
            'diff 承诺的新行没出现在写后的文件里：%r' % (d['new'],))
        if d.get('old'):
            assert d['old'] not in a_lines, (
                'diff 里被替换掉的旧行还在文件里：%r' % (d['old'],))


def _non_target_changes(before_lines, after_lines):
    """返回"不该变的行"清单（空 = 非目标区域逐字节未变）。

    允许的变化只有两类：
      * **目标行**：含 `add_axes` / `set_position` 的那些行（写回本来就该改它们）；
      * **自动块**：`# ===== auto layout` 到 `# ===== end auto layout` 之间整段
        （插入块通道会整段新增，那一段是生成物，不是用户代码）。
    其余任何增删改都算"非目标区域被动了"。
    """
    import difflib
    bad = []
    sm = difflib.SequenceMatcher(None, before_lines, after_lines, autojunk=False)
    in_block = False
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == 'equal':
            # 顺便更新块状态
            for line in after_lines[j1:j2]:
                if '# ===== auto layout' in line:
                    in_block = True
                elif '# ===== end auto layout' in line:
                    in_block = False
            continue
        for line in after_lines[j1:j2] + before_lines[i1:i2]:
            if '# ===== auto layout' in line:
                in_block = True
                continue
            if '# ===== end auto layout' in line:
                in_block = False
                continue
            if in_block:
                continue
            if ('add_axes' in line) or ('set_position' in line):
                continue
            bad.append('%s %r' % (tag, line))
    return bad


# =====================================================================
# §5 第五件：向后兼容（旧版参数文件，缺后来新增的键）
# =====================================================================
_BACKCOMPAT_CASES = {
    # 最小集：只有 index + pos（最早期的参数文件形状）
    'minimal_index_pos': _minimal_params(),
    # 缺 fig_index / n_figs（v3 之前的形状）
    'missing_fig_keys': {'version': 4, 'script': 'one.py',
                         'axes': [{'index': 0, 'pos': [0.12, 0.11, 0.40, 0.40]}]},
    # 未来的身份键（t14/t21 可能会加）：现在喂进来必须被**忽略**，不许报错
    'future_identity_keys': _minimal_params(axes=[
        {'index': 0, 'pos': [0.12, 0.11, 0.40, 0.40],
         'role': 'primary', 'identity': 'fig.axes[0]', 'anchor_name': 'ax'}]),
    # 老版本号
    'old_version_number': _minimal_params(version=3),
}


@pytest.mark.parametrize('name', sorted(_BACKCOMPAT_CASES))
def test_old_param_files_are_understood_or_refused_clearly(work, name):
    """旧版参数文件：降级到 `index` 对齐，或明确报错；**不得 KeyError**。

    这一条要挡的是"加了新键就把老文件读崩"：老文件是用户手上真实存在的东西
    （他们上一次 describe 出来的），一升级就 KeyError 是不可接受的。
    """
    _write(os.path.join(work, 'one.py'), _ONE_FIG)
    params = name + '.json'
    _write_json(os.path.join(work, params), _BACKCOMPAT_CASES[name])
    before = _read_bytes(os.path.join(work, 'one.py'))

    rc, p, out, err = _apply(work, 'one.py', params, '--write')
    blob = out + err
    _assert_no_traceback(rc, out, err, '旧参数 %s' % name)
    assert 'KeyError' not in blob, (
        '(%s) 旧参数文件撞出 KeyError（加新键就会挤掉老用户）：\n%s'
        % (name, blob[-1200:]))

    if rc == 0 and _entry(p).get('applied'):
        after = _describe(work, 'one.py', 'a.json')
        got = [ax['pos'] for ax in after['axes'] if ax['index'] == 0][0]
        assert abs(got[0] - 0.12) < 1e-9, (
            '(%s) 说写好了，但读回来的值不是参数里的：%r' % (name, got))
    else:
        # 明确报错也算合格，但得说得出话，且不许动文件
        assert _entry(p).get('reason') or (p or {}).get('error_code') or blob.strip()
        assert _read_bytes(os.path.join(work, 'one.py')) == before, (
            '(%s) 拒绝了却改了脚本' % name)
    print('BACKCOMPAT | %-22s rc=%d applied=%s reason=%s'
          % (name, rc, _entry(p).get('applied'), _entry(p).get('reason')))


def test_backward_compat_gap_is_documented():
    """把"等 t14/t21 定稿后要补的那一例"钉成一条**显式**断言，而不是口头 TODO。

    现在的参数文件还没有 t14 计划新增的身份键，所以"缺新键的老文件"这件事
    **暂时测不到真实形态**（上面的 `future_identity_keys` 只是模拟）。这条断言的作用是：
    等 describe/apply 真的开始写新键时，它会提醒把它换成一例真夹具。
    """
    d = _scratch_dir()
    try:
        _write(os.path.join(d, 'one.py'), _ONE_FIG)
        real = _describe(d, 'one.py', 'a.json')
    finally:
        shutil.rmtree(d, ignore_errors=True)
    keys = sorted(set(real.get('axes', [{}])[0]))
    future = [k for k in keys if k in ('role', 'identity', 'anchor_name')]
    assert not future, (
        'describe 已经开始产出身份键 %r —— 请把第 5 件事换成真夹具：\n'
        '① 用**加了新键**的 describe 产物手改掉那个键，模拟"老文件"；\n'
        '② 断言 apply 仍按 index 对齐（或明确报错），不许 KeyError。'
        % (future,))
    print('BACKCOMPAT | 当前 describe 的轴字段 = %r（无身份键，保留未来断言）' % keys)


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
