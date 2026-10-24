# -*- coding: utf-8 -*-
"""S4（0.1.16）：形状矩阵 —— 10 种常见建轴写法 x「describe -> 改一个字段 -> apply --write -> 重跑」。

为什么要这套：元组解包写回 bug（`fig, (ax1, ax2) = plt.subplots(1, 2)`）藏了三个版本，
根因是**测试只问"成没成"，没问"为什么没成"** —— 写回被拒/写错都被笼统算成"不支持"，
于是"工具生成了跑不通的代码"永远不红。本套件把分类规则写死：

判据（**不许放宽，也不许降级成 skip**）：
  * 原脚本单独跑必须 rc=0（跑不通 = 夹具本身坏了，本套件红）
  * 写回成功后，写回后的脚本**必须能跑**（子进程 rc=0）
  * **原脚本能跑 + 写回后跑不通 => 一律 FAIL（工具生成错代码）**，见 `classify()`
  * 写回被拒**只允许**"结构上明确表达不了"，且必须带明确的 reason/error_code，
    白名单（船长 2026-10-24 定死，四条）见 `REFUSE_OK`；
    凡不在这四条里的失败，一律按"生成错"判 FAIL

`describe` 的角色：只用来**读当前值**（它给的是"现在长什么样"）；真正交给 apply 的是
**最小参数文件**（只声明要改的那一项）。把 describe 的全量参数直接回灌会连带 `pos` 等
十几个字段，测的就不是"改一个字段"了（实测：mosaic / loop 会被 `pos` 拖出 best_effort，
真正原因却是你改的那一项证不出变量名 —— 两种原因混在一起就再也分不清）。

`7-mosaic` 与 `9-loop` 的处理不同（**这两格别混为一谈**）：
  * `9-loop` 属白名单③"循环建图"（`ax` 每轮被重绑，证不出指向哪根轴）⇒ 拒写正确，判 ALLOWED；
  * `7-mosaic` 的 `axd['a']` 是**字典常量下标**轴 —— 船长 2026-10-24 裁定它**判 FAIL**
    （不是"结构上表达不了"：同代码里 `axs[0]` 列表下标已经能正确寻址）⇒ 它的 `EXPECT`
    是 `'write'`，即**本套件在 engine 补上字典下标寻址之前是红的**。见 `KNOWN_GAP`。

跑法（脚本式，与 CI 一致）：`python tests/test_shapes.py`
反向验证（仓外副本里把元组解包寻址改回 0.1.15 行为 => 本套件必红）：
`python _team014/shapes_reverse.py`
"""
import ast
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

PY = sys.executable
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, 'src')
sys.path.insert(0, SRC)

from mpltweak import writeback as _wb            # noqa: E402

# 控制台兜底（GBK 控制台下 print 中文会 UnicodeEncodeError 把整套用例打挂；
# tests/test_hygiene.py 的 `test_test_suites_cannot_crash_gbk_console` 认这个标记）
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(errors='replace')
    except (AttributeError, ValueError, OSError):
        pass

ENV = dict(os.environ, MPLBACKEND='Agg', MPLTWEAK_LANG='zh', PYTHONUTF8='1',
           PYTHONIOENCODING='utf-8', PYTHONPATH=SRC)

FAILED = []
PASSED = []

HDR = ('import matplotlib\n'
       'matplotlib.use("Agg")\n'
       'import matplotlib.pyplot as plt\n')

# ---------------------------------------------------------------------------
# 白名单：**只有**这四条算"结构上明确表达不了"（船长 2026-10-24 定死）。
# 除此之外任何失败都按"生成错"判 FAIL —— 这一句就是本套件的法律。
# ---------------------------------------------------------------------------
REFUSE_OK = {
    'layout_conflict': '白名单①：开了 tight_layout/constrained_layout 又要写位置',
    'best_effort': '白名单③：不可证的多图/循环建图（fail-closed 保守拒写）',
    'colorbar_pos_unsupported': '白名单②：colorbar 位置（需额外两行才生效）',
}

# 每格：名字 -> (脚本正文, 期望)
#   'write'  = 必须写成功且写回后能跑
#   'refuse' = 允许拒写，但必须带白名单理由、脚本零改动、仍能跑
EXPECT = {
    '1-single': (
        'fig, ax = plt.subplots(figsize=(6, 4))\n'
        'ax.plot([1, 2, 3], [1, 4, 9])\n'
        'ax.set_xlabel("x")\n'
        'fig.savefig("out.png", dpi=80)\n', 'write'),
    '2-array': (
        'fig, axs = plt.subplots(1, 2, figsize=(8, 3))\n'
        'axs[0].plot([1, 2, 3], [1, 4, 9])\n'
        'axs[1].plot([3, 2, 1], [1, 2, 3])\n'
        'axs[0].set_xlabel("a")\n'
        'fig.savefig("out.png", dpi=80)\n', 'write'),
    '3-tuple-unpack': (
        'fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(8, 3))\n'
        'ax1.plot([1, 2, 3], [1, 4, 9])\n'
        'ax2.plot([3, 2, 1], [1, 2, 3])\n'
        'ax1.set_xlabel("a")\n'
        'ax2.set_xlabel("b")\n'
        'fig.savefig("out.png", dpi=80)\n', 'write'),
    '4-add-subplot-221': (
        'fig = plt.figure(figsize=(6, 4))\n'
        'ax = fig.add_subplot(221)\n'
        'ax.plot([1, 2, 3], [1, 4, 9])\n'
        'ax2 = fig.add_subplot(222)\n'
        'ax2.plot([3, 2, 1], [1, 2, 3])\n'
        'fig.savefig("out.png", dpi=80)\n', 'write'),
    '5a-2d-chained': (
        'fig, axs = plt.subplots(2, 2, figsize=(6, 6))\n'
        'axs[0][0].plot([1, 2], [2, 1])\n'
        'axs[1][1].plot([1, 2], [1, 2])\n'
        'axs[0][0].set_xlabel("a")\n'
        'fig.savefig("out.png", dpi=80)\n', 'write'),
    '5b-2d-tuple': (
        'fig, axs = plt.subplots(2, 2, figsize=(6, 6))\n'
        'axs[0, 0].plot([1, 2], [2, 1])\n'
        'axs[1, 1].plot([1, 2], [1, 2])\n'
        'axs[0, 0].set_xlabel("a")\n'
        'fig.savefig("out.png", dpi=80)\n', 'write'),
    '6-add-axes': (
        'fig = plt.figure(figsize=(6, 4))\n'
        'ax = fig.add_axes([0.15, 0.15, 0.7, 0.7])\n'
        'ax.plot([1, 2, 3], [3, 1, 2])\n'
        'fig.savefig("out.png", dpi=80)\n', 'write'),
    '7-mosaic': (
        'fig, axd = plt.subplot_mosaic([["a", "b"], ["c", "c"]], figsize=(6, 5))\n'
        'axd["a"].plot([1, 2], [2, 1])\n'
        'axd["b"].plot([1, 2], [1, 2])\n'
        'axd["c"].plot([2, 1], [1, 2])\n'
        'fig.savefig("out.png", dpi=80)\n', 'write'),
    '8-gridspec': (
        'fig = plt.figure(figsize=(6, 4))\n'
        'gs = fig.add_gridspec(2, 2)\n'
        'ax1 = fig.add_subplot(gs[0, 0])\n'
        'ax1.plot([1, 2], [2, 1])\n'
        'ax2 = fig.add_subplot(gs[0, 1])\n'
        'ax2.plot([1, 2], [1, 2])\n'
        'fig.savefig("out.png", dpi=80)\n', 'write'),
    '9-loop': (
        'fig = plt.figure(figsize=(8, 3))\n'
        'for i in range(2):\n'
        '    ax = fig.add_subplot(1, 2, i + 1)\n'
        '    ax.plot([1, 2, 3], [i, i + 1, i + 2])\n'
        '    ax.set_title("p%d" % i)\n'
        'fig.savefig("out.png", dpi=80)\n', 'refuse'),
    '10-pure-plt': (
        'plt.figure(figsize=(6, 4))\n'
        'plt.plot([1, 2, 3], [2, 3, 1])\n'
        'plt.xlabel("x")\n'
        'plt.ylabel("y")\n'
        'plt.savefig("out.png", dpi=80)\n', 'write'),
}

# 已知缺口（**不是**白名单）：船长 2026-10-24 裁定 7-mosaic 判 FAIL ——
# `axd['a']`（字典常量下标）寻址走不通，而同代码里 `axs[0]`（列表下标）可以，
# 所以这是**能力缺口**不是"结构上表达不了"。EXPECT 里它已改成 'write'。
# **2026-10-24 / S7（t13）已补上**：`writeback._mosaic_keys` 读字面量布局的键表 +
# 字典常量键寻址，块里写 `axd['a'].set_position(...)`；7-mosaic 已转 PASS ⇒ 本 dict 清空。
KNOWN_GAP = {}


def check(name, cond, detail=''):
    if cond:
        PASSED.append(name)
        print('  OK   %s%s' % (name, (' | %s' % detail) if detail else ''))
    else:
        FAILED.append(name)
        print('  FAIL %s%s' % (name, (' | %s' % detail) if detail else ''))


def classify(rc_orig, rc_apply, applied, rc_after, reason, error_code):
    """把一格观测判成 PASS / ALLOWED / FAIL —— **本单的法律条文就在这里**。

    铁律：原脚本能跑（rc_orig==0）而写回后跑不通（rc_after!=0）=> FAIL(生成错代码)。
    这条在函数里排最前，先于任何"写回自己怎么报"，所以任何口径都绕不过去。
    """
    if rc_orig != 0:
        return 'FIXTURE_BROKEN'          # 夹具问题：不是工具的错，但必须重做夹具
    if rc_after != 0:
        return 'FAIL(生成错代码)'         # <-- 本单核心：原能跑，写回后跑不通
    if rc_apply == 0 and applied is True:
        return 'PASS'
    if rc_apply == 0 and applied is False:
        return 'PASS(no_change)' if reason == 'no_change' else 'FAIL(没写却 rc=0)'
    key = [k for k in (reason, error_code) if isinstance(k, str) and k in REFUSE_OK]
    return ('ALLOWED(%s)' % key[0]) if key else 'FAIL(拒绝但不在白名单)'


def _cli(cwd, args):
    r = subprocess.run([PY, '-m', 'mpltweak.cli', *args], cwd=cwd,
                       capture_output=True, env=ENV)
    out = (r.stdout + r.stderr).decode('utf-8', 'replace')
    try:
        return r.returncode, out, json.loads(r.stdout.decode('utf-8', 'replace'))
    except Exception:                                                 # noqa: BLE001
        return r.returncode, out, {}


def _py(cwd):
    r = subprocess.run([PY, 'fig.py'], cwd=cwd, capture_output=True, env=ENV)
    return r.returncode, (r.stdout + r.stderr).decode('utf-8', 'replace')


def _cell(name, body, base, field='pos', value=(0.15, 0.15, 0.75, 0.75)):
    """跑一格：原脚本 -> describe -> 最小参数（只改一个字段）-> apply --write -> 重跑。

    默认改的是 `pos`（**位置**）：这是唯一必须"生成新代码"的字段 —— 原位改数字改不出来，
    只能插入调整块，而"插入块往哪个对象上写"正是元组解包 bug 的出事点（旧代码把
    `fig, (ax1, ax2)` 当成"轴数组"写 `ax1[0].set_position(...)`，重跑 TypeError）。
    `label_fontsize` 走的是原位改写（改 `ax.set_xlabel(...)` 里的数字/补关键字），
    不经过寻址 —— 所以单测它抓不到这个 bug（实测：只改字号时注入旧行为仍然全绿）。
    """
    d = os.path.join(base, name)
    os.makedirs(d, exist_ok=True)
    sp = os.path.join(d, 'fig.py')
    with open(sp, 'w', encoding='utf-8', newline='\n') as f:
        f.write(HDR + body)
    os.utime(sp, (time.time() - 60, time.time() - 60))
    before = open(sp, 'rb').read()

    rc_orig, out0 = _py(d)
    check('§A[%s/%s] 原脚本单独跑 rc=0（夹具本身没错，跑不通就没资格说工具）'
          % (name, field), rc_orig == 0,
          (out0.strip().splitlines() or [''])[-1][:160])
    if rc_orig != 0:
        return {'verdict': 'FIXTURE_BROKEN'}

    rc_d, out_d, desc = _cli(d, ['describe', 'fig.py'])
    check('§A[%s/%s] describe rc=0 且读到轴' % (name, field),
          rc_d == 0 and bool(desc.get('axes')),
          'rc=%s axes=%s' % (rc_d, len(desc.get('axes') or [])))
    if rc_d != 0 or not desc.get('axes'):
        return {'verdict': 'FAIL(describe)', 'reason': None, 'error_code': None}

    # 最小参数：只声明要改的那一个字段（describe 全量回灌会连带 pos 等，见文件头）
    one = {'index': 0, field: list(value) if field == 'pos' else value}
    params = {'version': desc['version'], 'script': desc['script'],
              'fig_index': desc.get('fig_index', 0),
              'n_figs': desc.get('n_figs', 1),
              'axes': [one]}
    pp = os.path.join(d, 'p_%s.json' % field)
    with open(pp, 'w', encoding='utf-8') as f:
        json.dump(params, f, ensure_ascii=False)
    os.utime(pp, None)

    rc_a, out_a, j = _cli(d, ['apply', 'fig.py', '--params', os.path.basename(pp),
                              '--write', '--json'])
    f0 = (j.get('files') or [{}])[0]
    after_src = open(sp, encoding='utf-8').read()
    changed = open(sp, 'rb').read() != before
    rc_after, out_after = _py(d)
    verdict = classify(rc_orig, rc_a, f0.get('applied'), rc_after,
                       f0.get('reason'), j.get('error_code') or f0.get('error_code'))
    return {'verdict': verdict, 'rc_apply': rc_a, 'rc_after': rc_after,
            'applied': f0.get('applied'), 'reason': f0.get('reason'),
            'error_code': j.get('error_code') or f0.get('error_code'),
            'pending': f0.get('pending'), 'warnings': f0.get('warnings'),
            'changed': changed, 'src': after_src, 'out_a': out_a,
            'out_after': out_after, 'dir': d, 'field': field}


def case_matrix(base):
    print('\n== A. 形状矩阵（11 格：10 种写法，二维数组两种下标）'
          '—— 改 pos：必须生成代码的那条路 ==')
    got = {}
    for name, (body, expect) in EXPECT.items():
        r = _cell(name, body, base)
        got[name] = r
        v = r['verdict']
        if expect == 'write':
            check('§A[%s/pos] 写回 rc=0 且 applied=true（该写的必须真写）' % name,
                  r.get('rc_apply') == 0 and r.get('applied') is True,
                  'rc=%s applied=%s reason=%s' % (r.get('rc_apply'),
                                                  r.get('applied'), r.get('reason')))
            check('§A[%s/pos] 写回后子进程重跑 rc=0（生成的代码必须能跑）' % name,
                  r.get('rc_after') == 0,
                  ' | '.join((r.get('out_after') or '').strip().splitlines()[-2:])[:220])
            check('§A[%s/pos] 判据 = PASS（不是 ALLOWED/skip/降级）%s' % (
                name, ('  [已知缺口：%s]' % KNOWN_GAP[name]) if name in KNOWN_GAP else ''),
                  v == 'PASS', v)
        else:
            check('§A[%s/pos] 拒写但理由在白名单里（%s）' % (name, '/'.join(sorted(REFUSE_OK))),
                  isinstance(r.get('reason'), str) and r['reason'] in REFUSE_OK,
                  'reason=%s error_code=%s' % (r.get('reason'), r.get('error_code')))
            check('§A[%s/pos] 拒写时脚本零改动且仍能跑（fail-closed 不许留半成品）' % name,
                  r.get('changed') is False and r.get('rc_after') == 0,
                  'changed=%s rc_after=%s' % (r.get('changed'), r.get('rc_after')))
            check('§A[%s/pos] 判据 = ALLOWED(白名单)' % name,
                  v.startswith('ALLOWED('), v)

    # 元组解包那格的专属牙：位置必须写在**每根轴自己的名字**上，不是 `ax1[0]`
    r3 = got.get('3-tuple-unpack') or {}
    src3 = r3.get('src') or ''
    check('§A[3-tuple-unpack/pos] 位置写回落在裸名字上（有 set_position(，且不出现 '
          'ax1[0]/ax2[0] —— 旧 bug 就是把它当成轴数组下标）',
          'set_position(' in src3 and 'ax1[' not in src3 and 'ax2[' not in src3,
          src3[-160:])
    r2 = got.get('2-array') or {}
    check('§A[2-array/pos] 数组写法仍按容器下标写（axs[...] 是正常通道，不许被顺手禁掉）',
          'axs[' in (r2.get('src') or ''),
          (r2.get('src') or '')[-160:])
    return got


def case_inline(base):
    """§A2：同一格的**原位改写**通道（改字号）—— 必须只改 ax1，不碰 ax2。"""
    print('\n== A2. 原位改写通道（元组解包 + 字号）：ax1 的字号只改 ax1 ==')
    r = _cell('3-tuple-unpack-inline', EXPECT['3-tuple-unpack'][0], base,
              field='label_fontsize', value=18)
    src = r.get('src') or ''
    check('§A2[3-tuple-unpack/label] 写回 rc=0 + 重跑 rc=0 + 判据 PASS',
          r.get('verdict') == 'PASS',
          '%s rc_apply=%s rc_after=%s' % (r.get('verdict'), r.get('rc_apply'),
                                          r.get('rc_after')))
    check('§A2[3-tuple-unpack/label] 字号只改在 ax1 上（fontsize=18 恰好一处、'
          'ax2 那行原样、且不出现 ax1[0]）',
          'fontsize=18' in src and src.count('fontsize=18') == 1
          and 'ax1[' not in src and 'ax2[' not in src, src[-200:])
    return r


def case_rule():
    """§B：分类规则本身（不依赖子进程，穷举组合，保证"生成错代码"永远判 FAIL）。"""
    print('\n== B. 分类规则（防复发：原能跑 + 写回后跑不通 => FAIL，绝不是 skip/降级） ==')
    bad = []
    seen = set()
    for rc_orig in (0, 1):
        for rc_apply in (0, 1, 2):
            for applied in (True, False, None):
                for rc_after in (0, 1, 2, 127):
                    v = classify(rc_orig, rc_apply, applied, rc_after, 'ok', None)
                    seen.add(v)
                    if rc_orig == 0 and rc_after != 0 and not v.startswith('FAIL'):
                        bad.append((rc_apply, applied, rc_after, v))
    check('§B1 穷举（%d 种组合）：只要"原脚本能跑 + 写回后跑不通"，判据必然是 FAIL'
          % (2 * 3 * 3 * 4), not bad, str(bad[:4]))
    check('§B2 写回被拒也救不了它：reason=best_effort 但写回后跑不通 => 仍是 FAIL',
          classify(0, 1, False, 1, 'best_effort', 'nothing_applied').startswith('FAIL'),
          classify(0, 1, False, 1, 'best_effort', 'nothing_applied'))
    check('§B3 白名单只在"写回后能跑"时才放行',
          classify(0, 1, False, 0, 'best_effort', 'nothing_applied')
          == 'ALLOWED(best_effort)',
          classify(0, 1, False, 0, 'best_effort', 'nothing_applied'))
    check('§B4 不在白名单里的拒绝 => FAIL（不许含糊成"不支持"）',
          classify(0, 2, False, 0, 'bad_params', 'bad_params')
          == 'FAIL(拒绝但不在白名单)',
          classify(0, 2, False, 0, 'bad_params', 'bad_params'))
    check('§B5 判据的取值域只有 PASS / ALLOWED / FAIL / FIXTURE_BROKEN'
          '（没有任何 skip/降级分支）',
          all(v.split('(')[0] in ('PASS', 'PASS', 'ALLOWED', 'FAIL', 'FIXTURE_BROKEN')
              for v in seen) and not any('SKIP' in v.upper() for v in seen),
          'seen=%s' % sorted(seen)[:6])


def case_teeth():
    """§C：机制牙 —— 把"元组解包寻址"关掉，寻址立刻退化成旧 bug 的 `ax1[0]`。"""
    print('\n== C. 机制牙：元组解包寻址是承重的（关掉它就退回旧 bug） ==')
    body = HDR + EXPECT['3-tuple-unpack'][0]
    tree = ast.parse(body)
    call = [n for n in ast.walk(tree)
            if isinstance(n, ast.Call) and getattr(n.func, 'attr', '') == 'subplots'][0]
    names = _wb._unpacked_axis_names(tree, call)
    check('§C1 元组解包 => 每根轴自己的名字（ax1/ax2，行号=赋值那行）',
          names == ([('ax1', call.lineno), ('ax2', call.lineno)], call.lineno),
          str(names))
    entries_now = _wb._axes_binding_entries(tree, 0)[0]
    check('§C2 寻址条目用的是裸名字（ax1 / ax2）',
          [e[0] for e in entries_now] == ['ax1', 'ax2'], str(entries_now))
    orig = _wb._unpacked_axis_names
    try:
        _wb._unpacked_axis_names = lambda *a, **k: None      # 模拟"改回 0.1.15 行为"
        entries_old = _wb._axes_binding_entries(tree, 0)[0]
    finally:
        _wb._unpacked_axis_names = orig
    names_old = [e[0] for e in (entries_old or []) if e]
    check('§C3 关掉它 => 再也证不出 ax1/ax2 两个裸名字（条目退化成匿名 None ⇒ 只能 '
          'fail-closed 拒写；修复是承重的）',
          names_old != ['ax1', 'ax2'], repr(entries_old))
    check('§C4 恢复后与关掉前一致（牙是干净的，不污染后续调用）',
          _wb._axes_binding_entries(tree, 0)[0] == entries_now)


def case_summary(got):
    """§D：把矩阵结果打成一张表（失败格必须能一眼看出是"工具的问题"还是"结构不支持"）。"""
    print('\n== D. 矩阵表（每格：原脚本 rc / 写回 rc / 写回后 rc / 判据） ==')
    print('  %-18s %-9s %-9s %-10s %s' % ('格', '原脚本', '写回', '写回后', '判据'))
    rows_ok = True
    for name in EXPECT:
        r = got.get(name) or {}
        print('  %-18s %-9s %-9s %-10s %s' % (
            name, r.get('rc_orig', 0), r.get('rc_apply'), r.get('rc_after'),
            r.get('verdict')))
        if str(r.get('verdict', '')).startswith(('FAIL', 'FIXTURE')):
            rows_ok = False
    check('§D1 矩阵里没有任何 FAIL/FIXTURE 格', rows_ok)
    allowed = [n for n in EXPECT if str((got.get(n) or {}).get('verdict', ''))
               .startswith('ALLOWED')]
    check('§D2 允许拒写的格只有白名单里的（当前：%s）'
          % (', '.join(allowed) or '(无)'),
          all((got.get(n) or {}).get('reason') in REFUSE_OK for n in allowed),
          str([(n, (got.get(n) or {}).get('reason')) for n in allowed]))


def main():
    base = tempfile.mkdtemp(prefix='mpltweak_shapes_')
    try:
        got = case_matrix(base)
        case_inline(base)
        case_rule()
        case_teeth()
        case_summary(got)
    finally:
        shutil.rmtree(base, ignore_errors=True)
    print('\n%s：%d 项通过，%d 项失败' %
          ('FAILED' if FAILED else 'ALL PASS', len(PASSED), len(FAILED)))
    if FAILED:
        for name in FAILED:
            print('  - ' + name)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
