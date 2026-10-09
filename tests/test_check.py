# -*- coding: utf-8 -*-
"""mpltweak check 测试（§1-§5）。

§1  整齐的 2x2 零误报（规则克制，宁漏不误）
§2  乱排能挑出问题（不齐 / 大小不一 / 重叠）
§3  越界 + 重叠
§4  对称的"不齐"只报一次
§5  --json 结构 + 退出码（0 无问题 / 1 有问题 / 2 采集失败）
"""
import json
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

_fails = []


def check(cond, label, extra=''):
    print('%s %s%s' % ('  OK  ' if cond else ' FAIL ', label,
                       '' if cond else '   ' + str(extra)))
    if not cond:
        _fails.append(label)


def run(*args):
    r = subprocess.run([PY, '-m', 'mpltweak.cli', *args], cwd=ROOT,
                       capture_output=True, text=True, encoding='utf-8')
    return r.returncode, (r.stdout or ''), (r.stderr or '')


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
