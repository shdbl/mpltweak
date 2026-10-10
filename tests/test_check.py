# -*- coding: utf-8 -*-
"""mpltweak check 测试（§1-§5）。

§1  整齐的 2x2 零误报（规则克制，宁漏不误）
§2  乱排能挑出问题（不齐 / 大小不一 / 重叠）
§3  越界 + 重叠
§4  对称的"不齐"只报一次
§5  --json 结构 + 退出码（0 无问题 / 1 有问题 / 2 采集失败）
"""
import json
import locale
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable

HEAD = "import matplotlib\nmatplotlib.use('Agg')\nimport matplotlib.pyplot as plt\n"

TIDY = HEAD + """
fig = plt.figure(figsize=(8, 6))
for _i, (_x, _y) in enumerate([(0.08, 0.56), (0.55, 0.56), (0.08, 0.10), (0.55, 0.10)]):
    _ax = fig.add_axes([_x, _y, 0.40, 0.38])
    _ax.set_title('P%d' % _i, fontsize=9.5)
    _ax.set_xlabel('x', fontsize=8.0)
    _ax.tick_params(labelsize=7.5)
    _ax.plot([1, 2, 3])
fig.savefig('t.png', dpi=60)
"""

MESSY = HEAD + """
fig = plt.figure(figsize=(8, 6))
for _i, (_x, _y, _w, _h) in enumerate([(0.060, 0.630, 0.300, 0.250),
                                       (0.415, 0.665, 0.335, 0.215),
                                       (0.118, 0.115, 0.275, 0.305),
                                       (0.048, 0.560, 0.300, 0.250)]):
    _ax = fig.add_axes([_x, _y, _w, _h])
    _ax.set_title('Q%d' % _i, fontsize=7.5 if _i else 9.5)
    _ax.tick_params(labelsize=6.0)
    _ax.plot([1, 2, 3])
fig.savefig('m.png', dpi=60)
"""

OVER = HEAD + """
fig = plt.figure(figsize=(8, 6))
_a = fig.add_axes([0.08, 0.55, 0.40, 0.38]); _a.plot([1, 2, 3])
_b = fig.add_axes([0.30, 0.60, 0.40, 0.30]); _b.plot([1, 2, 3])
_c = fig.add_axes([0.60, 0.10, 0.50, 0.30]); _c.plot([1, 2, 3])
fig.savefig('o.png', dpi=60)
"""

# 三个轴的 x0 相差不到一个 tol 网格、但首尾超过 tol（0.080 / 0.086 / 0.092）——
# 归并若只跟"组代表值"比，第三个轴就会另起一组，"间距不均"漏报。
COLGRID = HEAD + """
fig = plt.figure(figsize=(8, 6))
for _i, (_x, _y) in enumerate([(0.080, 0.74), (0.086, 0.40), (0.092, 0.12)]):
    _ax = fig.add_axes([_x, _y, 0.30, 0.22])
    _ax.set_title('C%d' % _i, fontsize=9.0)
    _ax.tick_params(labelsize=7.5)
    _ax.plot([1, 2, 3])
fig.savefig('c.png', dpi=60)
"""

# 开了 constrained_layout 的脚本：几何本身是整齐的（不该进 problems），
# 但布局引擎会在每次绘制时重算轴位置 → 必须在 layout_engine 里单独报出来。
CONSTRAINED = HEAD + """
fig, axes = plt.subplots(2, 2, figsize=(8, 6), layout='constrained')
for _i, _ax in enumerate(axes.ravel()):
    _ax.plot([1, 2, 3])
fig.savefig('k.png', dpi=60)
"""

# 标注被放到画布外：会被裁掉 → 期望 error（几何框永远查不出这类问题）
TEXT_OUT = HEAD + """
fig = plt.figure(figsize=(6, 4))
ax = fig.add_axes([0.10, 0.10, 0.80, 0.80])
ax.plot([1, 2, 3])
ax.text(-0.45, -0.30, 'way outside the canvas', transform=ax.transAxes, fontsize=12)
fig.savefig('to.png', dpi=60)
"""

# 超长刻度标签挤在小图上：相邻标签互相吃掉一半宽度 → 期望 warn
TICK_CROWD = HEAD + """
fig = plt.figure(figsize=(4, 3))
ax = fig.add_axes([0.30, 0.30, 0.60, 0.55])
ax.set_xticks([0, 1, 2, 3])
ax.set_xticklabels(['very-long-label-alpha', 'very-long-label-beta',
                    'very-long-label-gamma', 'very-long-label-delta'], fontsize=11)
ax.plot([1, 2, 3, 4])
fig.savefig('tc.png', dpi=60)
"""

# 两条标注压在一起（但都在画布内）→ 期望 warn、退出码仍为 0
TEXT_OVERLAP = HEAD + """
fig = plt.figure(figsize=(6, 4))
ax = fig.add_axes([0.12, 0.12, 0.80, 0.78])
ax.plot([1, 2, 3])
ax.text(0.50, 0.50, 'annotation AAAA', transform=ax.transAxes, fontsize=14)
ax.text(0.52, 0.51, 'annotation BBBB', transform=ax.transAxes, fontsize=14)
fig.savefig('tv.png', dpi=60)
"""

# 三个面板：两个 xlim 一致、第三个不同 → 期望 warn（不是 error）
LIM_MISMATCH = HEAD + """
fig, axes = plt.subplots(1, 3, figsize=(9, 3))
for _i, _ax in enumerate(axes):
    _ax.plot([1, 2, 3])
    _ax.set_xlim(0.0, 4.0)
axes[2].set_xlim(0.0, 2.0)
fig.savefig('lm.png', dpi=60)
"""

# 视图区间外刻度的经典构造：刻度落在 ylim 之外，标签外框贴到/越过画布上沿。
# 若按 get_yticklabels() 硬算就会误报"超出画布"——本夹具钉住这个假阳性。
OUT_OF_VIEW = HEAD + """
fig = plt.figure(figsize=(6, 2.5))
fig.subplots_adjust(top=0.98)
ax = fig.add_axes([0.12, 0.15, 0.80, 0.75])
ax.plot([1, 2, 3, 4])
ax.set_ylim(0.85, 4.15)
fig.savefig('oov.png', dpi=60)
"""

# 三个面板各自为政（每个范围都不同）→ 没有多数派，不该报
LIM_FREE = HEAD + """
fig, axes = plt.subplots(1, 3, figsize=(9, 3))
for _i, _ax in enumerate(axes):
    _ax.plot([1, 2, 3])
    _ax.set_xlim(0.0, float(_i + 1) * 3)
fig.savefig('lf.png', dpi=60)
"""

_fails = []


def check(cond, label, extra=''):
    print('%s %s%s' % ('  OK  ' if cond else ' FAIL ', label,
                       '' if cond else '   ' + str(extra)))
    if not cond:
        _fails.append(label)


def _dec(b):
    """按字节解码（不假设控制台编码，见 test_agent_api 里的同款说明）。"""
    raw = b or b''
    for enc in ('utf-8', locale.getpreferredencoding(False) or 'utf-8'):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode('utf-8', 'replace')


def run(*args):
    r = subprocess.run([PY, '-m', 'mpltweak.cli', *args], cwd=ROOT,
                       capture_output=True)
    return r.returncode, _dec(r.stdout), _dec(r.stderr)


def main():
    tmp = tempfile.mkdtemp(prefix='mpltweak_chk_')
    try:
        paths = {}
        for name, text in (('tidy', TIDY), ('messy', MESSY), ('over', OVER)):
            p = os.path.join(tmp, name + '.py')
            with open(p, 'w', encoding='utf-8') as f:
                f.write(text)
            paths[name] = p

        print('§1 整齐的 2x2 零误报')
        rc, out, err = run('check', paths['tidy'], '--json')
        d = json.loads(out)
        check(d['ok'] is True and not d['problems'],
              '没有问题', d['problems'])
        check(d['n_axes'] == 4, '读到 4 个轴', d.get('n_axes'))
        check(rc == 0, '退出码 0', rc)

        print('§2 乱排挑出问题')
        rc, out, err = run('check', paths['messy'], '--json')
        d = json.loads(out)
        rules = set(p['rule'] for p in d['problems'])
        check(len(d['problems']) >= 3, '至少 3 个问题', len(d['problems']))
        check('not_aligned' in rules or 'size_mismatch' in rules,
              '检出不对齐/大小不一', sorted(rules))
        check(rc == 1, '退出码 1', rc)

        print('§3 越界 + 重叠')
        rc, out, err = run('check', paths['over'], '--json')
        d = json.loads(out)
        rules = set(p['rule'] for p in d['problems'])
        check('out_of_canvas' in rules, '检出越界', sorted(rules))
        check('overlap' in rules, '检出重叠', sorted(rules))

        print('§4 对称的不齐只报一次')
        msgs = [p['msg'] for p in d['problems'] if p['rule'] == 'not_aligned']
        rc, out2, err = run('check', paths['messy'], '--json')
        d2 = json.loads(out2)
        pairs = []
        for p in d2['problems']:
            if p['rule'] == 'not_aligned':
                # 左边缘 / 下边缘是两个独立的维度，同一对轴在两个维度各报一次是对的
                dim = '左边缘' if '左边缘' in p['msg'] else '下边缘'
                pairs.append((dim,) + tuple(sorted(p['axes'])))
        check(len(pairs) == len(set(pairs)), '没有重复的对称问题',
              [x for x in pairs if pairs.count(x) > 1])

        print('§5 跨网格的 x0 不会被拆列（间距不均要报出来）')
        col = os.path.join(tmp, 'colgrid.py')
        with open(col, 'w', encoding='utf-8') as f:
            f.write(COLGRID)
        rc, out, err = run('check', col, '--json')
        d = json.loads(out)
        rules = set(p['rule'] for p in d['problems'])
        check('uneven_gap' in rules, '检出间距不均', sorted(rules))

        print('§6 边界')
        rc, out, err = run('check', os.path.join(tmp, 'nope.py'))
        check(rc == 2, '不存在的脚本 → 退出码 2', rc)
        check(out.strip() == '', 'stdout 干净（错误走 stderr）', repr(out[:60]))

        print('§7 布局引擎冲突（constrained_layout）单独报，且不改退出码')
        cst = os.path.join(tmp, 'constrained.py')
        with open(cst, 'w', encoding='utf-8') as f:
            f.write(CONSTRAINED)
        rc, out, err = run('check', cst, '--json')
        d = json.loads(out)
        findings = d.get('layout_engine') or []
        check(len(findings) == 1 and findings[0].get('kind') == 'constrained',
              '检出 constrained_layout', findings)
        check(isinstance(findings[0].get('line'), int) and findings[0]['line'] > 0,
              '带行号（用户才知道去哪改）', findings)
        check(d['ok'] is True and not d['problems'],
              '布局引擎不进 problems（整齐的图不该被骂）', d.get('problems'))
        check(rc == 0, '退出码仍是 0（不是 error 级）', rc)

        print('§7b 没开布局引擎的脚本 → layout_engine 为空')
        rc, out, err = run('check', paths['tidy'], '--json')
        check(json.loads(out).get('layout_engine') == [],
              '干净脚本无 layout_engine 噪音', json.loads(out).get('layout_engine'))
        rc, out, err = run('check', cst)
        check('布局引擎' in out and '第' in out,
              '人读输出里也有提示', repr(out[-200:]))

        print('§8 文字渲染尺寸（真实外框，不是几何框）')
        # 干净图：文字检查开着也**不能**报（否则这功能就是噪音）
        rc, out, err = run('check', paths['tidy'], '--json')
        d = json.loads(out)
        check(d.get('text_checked') is True and (d.get('n_texts') or 0) > 0,
              '干净图确实检查了文字（有 n_texts）',
              (d.get('text_checked'), d.get('n_texts')))
        check(not [x for x in d['problems'] + d['warnings']
                   if x['rule'].startswith('text')
                   or x['rule'] == 'tick_labels_crowded'],
              '干净图零文字误报', d['problems'] + d['warnings'])

        tout = os.path.join(tmp, 'textout.py')
        with open(tout, 'w', encoding='utf-8') as f:
            f.write(TEXT_OUT)
        rc, out, err = run('check', tout, '--json')
        d = json.loads(out)
        check('text_out_of_canvas' in [p['rule'] for p in d['problems']],
              '文字超出画布 → 进 problems（error）',
              [p['rule'] for p in d['problems']])
        check(rc == 1, '退出码 1（客观可判定，算真问题）', rc)

        tick = os.path.join(tmp, 'tickcrowd.py')
        with open(tick, 'w', encoding='utf-8') as f:
            f.write(TICK_CROWD)
        rc, out, err = run('check', tick, '--json')
        d = json.loads(out)
        check('tick_labels_crowded' in [w['rule'] for w in d['warnings']],
              '超长刻度标签互相压住 → warn',
              [w['rule'] for w in d['warnings']])
        check(not [p for p in d['problems']
                   if p['rule'] == 'tick_labels_crowded'],
              '互压只降 warn，不进 problems', [p['rule'] for p in d['problems']])

        ovl = os.path.join(tmp, 'textoverlap.py')
        with open(ovl, 'w', encoding='utf-8') as f:
            f.write(TEXT_OVERLAP)
        rc, out, err = run('check', ovl, '--json')
        d = json.loads(out)
        check(rc == 0 and d['ok']
              and any(w['rule'] == 'text_overlap' for w in d['warnings']),
              '标注互压 → 只提示、不失败', (rc, d['warnings']))

        rc, out, err = run('check', tout, '--json', '--no-text')
        d = json.loads(out)
        check(d.get('text_checked') is False and rc == 0 and not d['problems'],
              '--no-text 回到纯几何口径', (d.get('text_checked'), d['problems']))

        print('§9 轴范围不统一（多面板常见毛病）')
        lm = os.path.join(tmp, 'limmis.py')
        with open(lm, 'w', encoding='utf-8') as f:
            f.write(LIM_MISMATCH)
        rc, out, err = run('check', lm, '--json')
        d = json.loads(out)
        check('limits_mismatch' in [w['rule'] for w in d['warnings']],
              '2/3 面板范围一致、第三个不同 → 提示',
              [w['rule'] for w in d['warnings']])
        check(rc == 0 and d['ok'], '范围不统一只提示、不算问题（rc 仍 0）',
              (rc, d['ok']))
        lf = os.path.join(tmp, 'limfree.py')
        with open(lf, 'w', encoding='utf-8') as f:
            f.write(LIM_FREE)
        rc, out, err = run('check', lf, '--json')
        d = json.loads(out)
        check('limits_mismatch' not in [w['rule'] for w in d['warnings']],
              '各自为政（没有多数派）→ 不报',
              [w['rule'] for w in d['warnings']])

        print('\u00a77c 布局引擎启发式：不误报用户自己的 helper')
        hlp = os.path.join(tmp, 'helper.py')
        with open(hlp, 'w', encoding='utf-8') as f:
            # 夹具必须**自己能跑且真的建了图**（否则 collect 就失败、
            # 根本走不到布局引擎检查那一栏）
            f.write(HEAD + "fig, axes = plt.subplots(1, 2, figsize=(6, 3))\n"
                           "def save_plot(**kw):\n    pass\n\n"
                           "save_plot(constrained_layout=True)\n"
                           "fig.savefig('h.png', dpi=60)\n")
        rc, out, err = run('check', hlp, '--json')
        d = json.loads(out)
        check(d.get('layout_engine') == [],
              '用户自己的 save_plot(constrained_layout=True) 不该被当成布局引擎',
              d.get('layout_engine'))
        tp3 = os.path.join(tmp, 'thirdparty3.py')
        with open(tp3, 'w', encoding='utf-8') as f:
            f.write(HEAD + "import types\n"
                           "fig, axes = plt.subplots(1, 2, figsize=(6, 3))\n"
                           "mylib = types.SimpleNamespace(save_plot=lambda **kw: None)\n"
                           "mylib.save_plot(constrained_layout=True)\n"
                           "fig.savefig('t3.png', dpi=60)\n")
        rc, out, err = run('check', tp3, '--json')
        check(json.loads(out).get('layout_engine'),
              '第三方 helper 仍会被报（已知的保守启发式边界）',
              json.loads(out).get('layout_engine'))

        print('\u00a79b 视图外的刻度不能算"超出画布"（假阳性回归）')
        # 独立审阅实测：figsize=(6,2.5) + subplots_adjust(top=0.98) +
        # set_ylim(0.85,4.15) 时 ax.yaxis.get_major_ticks() 里含视图区间外的刻度
        # （0.5 / 4.5），其标签外框落在 y=[-0.016,0.04] 与 [1.038,1.094] ——
        # 若按 ax.get_yticklabels() 直接算外框，就会报出"越界 1.010"这种假阳性。
        oov = os.path.join(tmp, 'outofview.py')
        with open(oov, 'w', encoding='utf-8') as f:
            f.write(OUT_OF_VIEW)
        rc, out, err = run('check', oov, '--json')
        d = json.loads(out)
        check(rc == 0 and not d['problems'],
              '视图外的刻度不报越界（否则是假阳性）',
              (rc, [p['rule'] for p in d['problems']]))
        check((d.get('n_texts') or 0) > 0 and d.get('text_checked') is True,
              '文字检查确实跑了（不是靠跳过它蒙对）',
              (d.get('text_checked'), d.get('n_texts')))

        print('§10 check --strict：布局引擎冲突升级为问题（接 CI 用）')
        rc, out, err = run('check', cst, '--json')
        d = json.loads(out)
        check(rc == 0 and not d['problems'] and d['layout_engine'],
              '默认只是提示：rc=0 且不进 problems',
              (rc, d['problems'], len(d['layout_engine'])))
        rc, out, err = run('check', cst, '--json', '--strict')
        d = json.loads(out)
        check(rc == 1 and d['ok'] is False
              and [p['rule'] for p in d['problems']] == ['layout_engine_conflict'],
              '--strict → rc=1 且问题落在 problems 里',
              (rc, [p['rule'] for p in d['problems']]))
        check(d['layout_engine'] and d['problems'][0].get('msg'),
              '原始冲突信息仍然保留在 layout_engine 里', d['layout_engine'])
    except Exception as e:                                        # noqa: BLE001
        check(False, '测试执行', e)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    if _fails:
        print('FAILED: %d 项 -> %s' % (len(_fails), _fails))
        return 1
    print('ALL PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
