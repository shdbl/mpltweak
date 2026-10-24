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

# 测试断言的是中文人读文案：把界面语言**钉死**，别让宿主 locale 决定结论。
# messages 的顺序是 --lang > MPLTWEAK_LANG > locale > LANG > en：CI(Linux,
# LANG=C) 走英文、本地(zh-CN)走中文 —— 不钉住就会"本地全绿、CI 全红"（0.1.8 真栽过）。
# 需要英文分支的用例，自行 run(..., env={'MPLTWEAK_LANG': 'en'}) 覆盖即可。
os.environ['MPLTWEAK_LANG'] = 'zh'

# 后端也钉死 Agg（0.1.10 补，船长 2026-10-12 审计：本文件原先**缺**这个 pin）。
# 本机同时装了 PyQt5 与 tkinter：后端若落到 Qt/Tk，被执行的用户脚本里一次
# plt.show() / 开窗就真抢焦点和鼠标（用户当天报"有人抢鼠标"）。Agg 是纯无头、
# 不弹窗的后端。模块级这一行覆盖 `subprocess` 不传 env 的路径，`run()` 里再显式
# 写进 env 字典覆盖传参路径 —— 两层都不依赖"外层环境碰巧设了"。
os.environ['MPLBACKEND'] = 'Agg'
PY = sys.executable

# GBK 控制台兜底（T13/T15）：CI 是 UTF-8 永远绿，本地 cp936 上一次 UnicodeEncodeError
# 就把整个套件从 ALL PASS 变成 rc=1 的假红。两层：① 输出流 errors='replace'；
# ② 进 print 前先清洗（_gbk_safe）。
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(errors='replace')
    except (AttributeError, ValueError, OSError):
        pass          # 被重定向成不支持 reconfigure 的对象时忽略即可


def _gbk_safe(s):
    """把字符串清洗成"当前输出流编得出来"的形式（U+FFFD 之类换成 ?）。"""
    s = str(s)
    enc = getattr(sys.stdout, 'encoding', None) or 'utf-8'
    try:
        return s.encode(enc, 'replace').decode(enc, 'replace')
    except (LookupError, UnicodeDecodeError):
        return s.encode('ascii', 'backslashreplace').decode('ascii')


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

# 与 TEXT_OUT 的**唯一差别**是 bbox_inches='tight'：tight 会按内容把画布撑大，
# 框外的文字不会真的被裁掉 → 期望降级为提示（CH-4）。
# 0.1.8 对这个脚本报 8 条 error、rc=1（与上面那条逐字相同），README 推荐的
# `check --json` CI 配方在正常项目上于是永远红。
TEXT_OUT_TIGHT = HEAD + """
fig = plt.figure(figsize=(6, 4))
ax = fig.add_axes([0.10, 0.10, 0.80, 0.80])
ax.plot([1, 2, 3])
ax.text(-0.45, -0.30, 'way outside the canvas', transform=ax.transAxes, fontsize=12)
fig.savefig('tot.png', dpi=60, bbox_inches='tight')
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
    # 输出前清洗：extra 里可能混进子进程乱码（U+FFFD）→ GBK 控制台下 print 会崩（T13/T15）
    print('%s %s%s' % ('  OK  ' if cond else ' FAIL ', _gbk_safe(label),
                       '' if cond else '   ' + _gbk_safe(extra)))
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


def run(*args, env=None):
    """跑一次 CLI。env 里的键**并入**当前环境 —— 用来把界面语言钉死。

    messages 的语言探测顺序是 ``--lang > MPLTWEAK_LANG > locale > LANG > en``：
    CI（Linux，LANG=C）走英文、本地（Windows zh-CN）走中文。断言人读文案却不钉住
    语言，就会"本地全绿、CI 全红"（0.1.8 发布时真栽过，见 test_agent_api 同款说明）。

    **后端也必须钉死 Agg**（0.1.10 补，船长 2026-10-12 审计）：本机同时装了 PyQt5
    与 tkinter，一旦后端落到 Qt/Tk，被执行的用户脚本里任何 ``plt.show()`` / 开窗都会
    真抢焦点与鼠标（用户当天报"有人抢鼠标"）。Agg 是**不弹窗**的无头后端，测试必须
    显式写进 env，不依赖外层环境或默认值。这里写两处（模块级 + 本函数）与
    test_agent_api.py 同款，见文件顶部 ``os.environ['MPLBACKEND']`` 的说明。
    """
    _env = dict(os.environ)
    _env['MPLBACKEND'] = 'Agg'          # ← 硬钉：子进程一律无头，绝不开窗
    if env:
        _env.update(env)
    r = subprocess.run([PY, '-m', 'mpltweak.cli', *args], cwd=ROOT,
                       capture_output=True, env=_env)
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

        print('§7 布局引擎冲突（constrained_layout）：D1 起默认就是 error 级问题')
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
        check(d['ok'] is False
              and [p['rule'] for p in d['problems']] == ['layout_engine_conflict'],
              'D1：冲突**默认**进 problems（不再需要 --strict）',
              (d['ok'], [p['rule'] for p in d['problems']]))
        check([p.get('level') for p in d['problems']] == ['error'],
              '等级是 error（用户只看 check 也得知道"写了不生效"）', d.get('problems'))
        check(rc == 1, '退出码 1（发现问题）', rc)

        print('§7b 没开布局引擎的脚本 → layout_engine 为空')
        rc, out, err = run('check', paths['tidy'], '--json')
        check(json.loads(out).get('layout_engine') == [],
              '干净脚本无 layout_engine 噪音', json.loads(out).get('layout_engine'))
        # 人读输出：语言钉死，中英两种都断言（英文宿主上只断言中文必然落空）
        rc, out, err = run('check', cst, env={'MPLTWEAK_LANG': 'zh'})
        check('布局引擎' in out and '第' in out,
              '人读输出里也有提示（zh，带行号）', repr(out[-200:]))
        rc_en, out_en, err_en = run('check', cst, env={'MPLTWEAK_LANG': 'en'})
        check('layout engine' in out_en and 'line ' in out_en,
              '人读输出里也有提示（en，同一处冲突带行号）', repr(out_en[-200:]))

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

        print('\u00a77d 布局引擎：裸名 / 关键字 / set_layout_engine 形式（T11 / E15）')
        # 0.1.8 只认 `xxx.yyy(constrained_layout=True)`：`from matplotlib.pyplot
        # import subplots` 后直接 `subplots(layout='constrained')`、
        # `fig.set_layout_engine('tight')`、`set_layout_engine(layout='constrained')`
        # 全都扫不到 → 写回的位置被布局引擎吃掉却一句提示都没有。
        # 反向验证：把 layoutwarn.scan_source 里 T11 新增的规则删掉 → 本段 FAIL。
        from mpltweak import layoutwarn as _lw
        _e15 = {
            "裸名 subplots(layout='constrained')":
                HEAD + "from matplotlib.pyplot import subplots\n"
                       "fig, axes = subplots(1, 2, layout='constrained')\n",
            "裸名 figure(constrained_layout=True)":
                HEAD + "from matplotlib.pyplot import figure\n"
                       "fig = figure(constrained_layout=True)\n",
            "关键字 set_layout_engine(layout='compressed')":
                HEAD + "fig = plt.figure()\n"
                       "fig.set_layout_engine(layout='compressed')\n",
            "位置 set_layout_engine('tight')（每次绘制重跑 → autolayout）":
                HEAD + "fig = plt.figure()\nfig.set_layout_engine('tight')\n",
            "layout='tight'（TightLayoutEngine）":
                HEAD + "fig, axes = plt.subplots(1, 2, layout='tight')\n",
            "rcParams 下标赋值 figure.autolayout":
                HEAD + "plt.rcParams['figure.autolayout'] = True\n",
            "rcParams 下标赋值 constrained_layout.use":
                HEAD + "plt.rcParams['figure.constrained_layout.use'] = True\n",
        }
        for _name, _src in _e15.items():
            _found = _lw.scan_source(_src)
            check(len(_found) >= 1 and all(f.get('line') for f in _found),
                  'E15 检出：%s' % _name, _found)
        # 不能回退成假阳性：非白名单的裸名 helper 与关闭引擎都不算冲突
        _neg = {
            "用户自己的 save_plot(constrained_layout=True)":
                HEAD + "def save_plot(**kw):\n    pass\n"
                       "save_plot(constrained_layout=True)\n",
            "用户自己的 show_it(layout='constrained')":
                HEAD + "def show_it(**kw):\n    pass\n"
                       "show_it(layout='constrained')\n",
            "关掉布局引擎 set_layout_engine(None)":
                HEAD + "fig = plt.figure()\nfig.set_layout_engine(None)\n",
            "layout='none'（不启用引擎）":
                HEAD + "fig, axes = plt.subplots(1, 2, layout='none')\n",
            "变量布局参数（静态扫不到，已知边界）":
                HEAD + "fig, axes = plt.subplots(1, 2, layout=LAYOUT)\n",
        }
        for _name, _src in _neg.items():
            check(_lw.scan_source(_src) == [],
                  'E15 不误报（假阳性回归）：%s' % _name,
                  _lw.scan_source(_src))
        # 端到端：CLI 也要真的报出来（kind/行号/文案三件套）
        e15 = os.path.join(tmp, 'e15_bare.py')
        with open(e15, 'w', encoding='utf-8') as f:
            f.write(HEAD + "from matplotlib.pyplot import subplots\n"
                           "fig, axes = subplots(1, 2, figsize=(6, 3), "
                           "layout='constrained')\n"
                           "axes[0].plot([1, 2, 3])\n"
                           "fig.savefig('e15.png', dpi=60)\n")
        rc, out, err = run('check', e15, '--json')
        d15 = json.loads(out).get('layout_engine') or []
        # HEAD 占 1-3 行 → `layout='constrained'` 在第 5 行
        check(len(d15) == 1 and d15[0].get('kind') == 'constrained'
              and d15[0].get('line') == 5,
              'E15 端到端：裸名 subplots(layout=...) 被 check 报出（带行号）', d15)
        rc, out, err = run('check', e15, env={'MPLTWEAK_LANG': 'zh'})
        check('布局引擎' in out and '第 5 行' in out,
              'E15 端到端：人读输出指到第 5 行', repr(out[-200:]))

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

        print('§10 check --strict：兼容开关 —— 默认档已经是同一结论（D1）')
        rc, out, err = run('check', cst, '--json')
        d = json.loads(out)
        check(rc == 1 and d['ok'] is False
              and [p['rule'] for p in d['problems']] == ['layout_engine_conflict'],
              'D1：默认档 rc=1 且问题落在 problems 里',
              (rc, d['problems']))
        rc_st, out_st, err_st = run('check', cst, '--json', '--strict')
        d_st = json.loads(out_st)
        check(rc_st == rc and d_st['problems'] == d['problems'],
              '--strict 不再改变结论（老 CI 的命令行照旧能用）',
              (rc_st, [p['rule'] for p in d_st['problems']]))
        check(d['layout_engine'] and d['problems'][0].get('msg'),
              '原始冲突信息仍然保留在 layout_engine 里', d['layout_engine'])

        print('§11 CH-4：bbox_inches=tight 的图不再误报 text_out_of_canvas')
        tgt = os.path.join(tmp, 'textout_tight.py')
        with open(tgt, 'w', encoding='utf-8') as f:
            f.write(TEXT_OUT_TIGHT)
        rc, out, err = run('check', tgt, '--json')
        d = json.loads(out)
        check(d.get('tight_bbox') is True, '识别出 tight 口径', d.get('tight_bbox'))
        check(not [p for p in d['problems'] if p['rule'] == 'text_out_of_canvas'],
              '越界文字不再进 problems（0.1.8 是 8 条 error）',
              [p['rule'] for p in d['problems']])
        _dw = [w for w in d['warnings'] if w['rule'] == 'text_out_of_canvas']
        check(_dw and all(w.get('downgraded') == 'tight_bbox' for w in _dw),
              '降级成 warning 且标明 downgraded', _dw[:1])
        check(rc == 0, 'rc=0（README 的 CI 配方不再恒红）', rc)
        rc, out, err = run('check', tgt, '--json', '--no-tight')
        d2 = json.loads(out)
        check('text_out_of_canvas' in [p['rule'] for p in d2['problems']],
              '--no-tight 回到 0.1.8 口径（error）',
              [p['rule'] for p in d2['problems']])
        check(rc == 1, '--no-tight → rc=1（这是"真的会被裁"的旧口径）', rc)
        # 反向保护：不做 tight 的同类脚本**仍然**是 error —— 这条修复不许把规则整体放过
        rc, out, err = run('check', tout, '--json')
        check(json.loads(out).get('tight_bbox') is False
              and 'text_out_of_canvas' in [p['rule'] for p in json.loads(out)['problems']],
              '非 tight 脚本不受影响（仍然 error）', json.loads(out).get('tight_bbox'))

        print('§12 A4：check --static 完全不执行脚本，并明说没检查什么')
        marker = os.path.join(tmp, 'STATIC_RAN.txt')
        sst = os.path.join(tmp, 'static.py')
        with open(sst, 'w', encoding='utf-8') as f:
            f.write(CONSTRAINED + 'open(%r, "w").write("ran")\n' % marker)
        rc, out, err = run('check', sst, '--static', '--json')
        d = json.loads(out)
        check(d.get('static') is True, '输出标了 static', sorted(d)[:6])
        check(not os.path.exists(marker), '脚本**真的没有被执行**（marker 文件不存在）')
        check(len(d.get('layout_engine') or []) == 1,
              '静态也能报出布局引擎冲突', d.get('layout_engine'))
        check(bool(d.get('note')) and '--static' in d['note'],
              'note 明说"没执行、这些都没查"', str(d.get('note'))[:60])
        check(len(d.get('not_checked') or []) >= 5,
              'not_checked 列出没检查的规则', d.get('not_checked'))
        check(d.get('ok') is False and rc == 1
              and [p['rule'] for p in d['problems']] == ['layout_engine_conflict'],
              'D1：静态档同样把冲突当 error（ok=false / rc=1）',
              (d.get('ok'), rc, [p['rule'] for p in d.get('problems') or []]))
        rc, out, err = run('check', sst, '--static', '--strict', '--json')
        check(rc == 1, '--static --strict + 冲突 → rc=1（与默认档一致）', rc)
        _bad = os.path.join(tmp, 'badsyntax.py')
        with open(_bad, 'w', encoding='utf-8') as f:
            f.write('import matplotlib\nx = (1,\n')
        rc, out, err = run('check', _bad, '--static', '--json')
        check(rc == 2 and json.loads(out).get('error_code') == 'syntax_error',
              '语法错误 → rc=2 + syntax_error（"跑不起来"）', rc)

        print('§13 CH-3：--tol 只收有限正数（不再吐 Infinity / 静默关掉检查）')
        for _t in ('inf', '-inf', 'nan', '0', '-1'):
            rc, out, err = run('check', paths['tidy'], '--json', '--tol', _t)
            check(rc == 2, '--tol %s → rc=2（用法错误）' % _t, rc)
            check('Infinity' not in out and 'NaN' not in out,
                  '--tol %s 的 stdout 不含非法 JSON 字面量' % _t, out[:120])
            # 关键区分：**参数层**拒绝（stdout 干净、用法进 stderr）还是
            # 落到 analyze 的兜底（stdout 是 internal_error 的 JSON）。
            # 只断言 rc 会让"把 type=_tol_arg 改回 float"的反向验证漏网 —— 那条路
            # 也恰好返回 2（ValueError 被顶层 guard 兜成 internal_error）。
            check(out.strip() == '' and 'error_code' not in out,
                  '--tol %s → 参数层拒绝（不是 internal_error JSON）' % _t,
                  out[:120])
        rc, out, err = run('check', paths['tidy'], '--json', '--tol', '0.02')
        check(rc == 0 and json.loads(out)['tol'] == 0.02, '合法 tol 照常工作', rc)

        print('§14 CH-12：机器可读的 msg 也跟随语言（--lang 进了 check/describe）')
        rc, out, err = run('check', paths['over'], '--json', '--lang', 'en')
        _msgs = [p['msg'] for p in json.loads(out)['problems']]
        check(bool(_msgs) and all(ord(c) < 128 for c in ''.join(_msgs)),
              '--lang en → problems[].msg 全 ASCII', _msgs[:2])
        rc, out, err = run('check', paths['over'], '--json', '--lang', 'zh')
        _msgs_zh = [p['msg'] for p in json.loads(out)['problems']]
        check(any(ord(c) > 127 for c in ''.join(_msgs_zh)),
              '--lang zh → 中文（默认行为不变）', _msgs_zh[:1])
        rc, out, err = run('describe', paths['tidy'], '--lang', 'en')
        check(rc == 0, 'describe 也接受 --lang（SKILL.md 一直列着它）', rc)

        print('§15 归因改动不越界：check 的失败路径与退出码语义都不变')
        # 背景（0.1.10 t11）：失败的**归因**（谁错了 + 下一步）只属于 apply 的
        # 写回验证重跑那一条链。check 的失败路径（跑不起来 / 语法错 / 文件不存在）
        # 必须**保持原样**，尤其不能凭空长出一个 attribution 字段让消费方误判
        # "这是写回失败"。这里钉住三件事：① 退出码口径不变（0.1.11 方案 A：0 / 1 / 2 / 4，
        # 3 已退役；"跑不起来"仍是 2）—— 0.1.9 已对外
        # 文档化，用户 CI 依赖）；② check 的失败 JSON 仍只有 error_code；
        # ③ 分类器不会把"脚本需要参数"说成"缺数据"（评审点名的伪装问题）。
        _s15 = os.path.join(tmp, 'attr_gone_dep.py')
        with open(_s15, 'w', encoding='utf-8') as f:
            f.write("import matplotlib\nmatplotlib.use('Agg')\n"
                    "import matplotlib.pyplot as plt\n"
                    "import t11_check_gone_pkg          # 本环境没有这个包\n"
                    "fig, ax = plt.subplots()\n"
                    "fig.savefig('x.png')\n")
        rc, out, err = run('check', _s15, '--json')
        _d15 = json.loads(out)
        check(rc == 2 and _d15.get('error_code') == 'collect_failed',
              '脚本跑不起来 → rc=2 + error_code=collect_failed（方案 A 后仍是 2）',
              (rc, _d15.get('error_code')))
        check('attribution' not in _d15 and 'failure_kind' not in _d15,
              'check 的失败 JSON **不带**写回归因字段（归因只属于 apply 那条链）',
              sorted(_d15)[:8])
        rc, out, err = run('check', _s15, '--static', '--json')
        _st15 = json.loads(out)
        check(rc == 0 and _st15.get('static') is True,
              '--static 是"脚本跑不起来"时的出路：不执行脚本 → rc=0',
              (rc, _st15.get('static')))
        rc, out, err = run('check', _s15, '--static', '--json', '--lang', 'en')
        check(rc == 0, '--static + --lang en 同样可用', rc)

        rc, out, err = run('check',
                           os.path.join(tmp, 'definitely_missing_t11.py'),
                           '--json')
        check(rc == 2 and json.loads(out).get('error_code') == 'file_not_found',
              '文件不存在 → 仍 rc=2 + file_not_found', rc)

        # 分类器（纯函数）保守性：这条链上判不出原因时**绝不**说"缺依赖/缺数据"
        # —— 那正是评审点名的"把需要参数伪装成缺数据"。
        _attr15 = os.path.join(tmp, 'attr_probe.py')
        with open(_attr15, 'w', encoding='utf-8') as f:
            f.write('import sys\n_x = sys.argv[1]\n')
        sys.path.insert(0, os.path.join(ROOT, 'src'))
        try:
            from mpltweak import verify as _v15
            _miss = ("usage: attr_probe.py [-h]\n"
                     "attr_probe.py: error: the following arguments are "
                     "required: --data")
            check('缺数据' not in _miss and '缺依赖' not in _miss,
                  '§15 夹具的"需要参数"报错文本本身**不含**缺数据字样（断言可信）')
            check(_v15.classify_verify_failure(_miss, script_path=_attr15,
                                               args_passed=False)
                  != (_v15.VERIFY_FAIL_CANNOT_RUN, 'missing_dependency'),
                  '§15 需要参数的报错**不**被归成缺依赖（伪装问题的反向锁）',
                  _v15.classify_verify_failure(_miss, script_path=_attr15))
            _k15 = _v15.classify_verify_failure(
                _miss, script_path=_attr15, args_passed=False)[0]
            check(_k15 in (_v15.VERIFY_FAIL_NEEDS_ARGS,
                           _v15.VERIFY_FAIL_CANNOT_RUN),
                  '§15 判据只会落在 needs_args / cannot_run 两档之一', _k15)
            check(_v15.VERIFY_FAIL_TOOL_BUG not in (
                _v15.classify_verify_failure('', script_path=_attr15,
                                             args_passed=False)[0], _k15),
                  '§15 判不出原因时**绝不**误报 tool_bug（不吓用户）')
        except ImportError as _e:                                  # noqa: BLE001
            print('  --  跳过分类器断言：%s' % _e)

        # ------------------------------------------------------------------
        # §16 t67（报告 P1-3）：colorbar × aspect_locked 轴的**矩形相交**必须报出来。
        #
        # 为什么单列：几何比对整体跳过 aspect_locked 轴（它们的长宽比由投影/数据定死，
        # 动不了），而"极地图 + colorbar"这个科研图里最常见的组合**恰好**落进那个盲区
        # ⇒ 过去 check 对这条最高频的真实排版事故给 ok:true / problems:[] / warnings:[]。
        # 现在单独保留一条 colorbar 规则（warn，不动 --strict、不动任何既有规则）。
        #
        # 几何：报告那张表是 get_tightbbox（真实渲染）量的，而 check 用 pos（轴位置
        # 矩形，aspect 锁定轴经 apply_aspect 调整后的值）—— 两者不是一回事（实测：
        # 报告 y 0.4651-0.900 vs pos 0.4757-0.8894）。所以夹具按 **pos 空间**构造：
        # map [0.3621, 0.4550, 0.2758, 0.4349] + set_aspect('equal') → pos y0=0.4656
        # /h=0.4137；colorbar [0.33, 0.455, 0.34, 0.016] → 相交 x 0.2758 × y 0.0054
        # （0.54% 图高，与报告 0.59% 同量级，且**小于**默认 --tol 0.010 ⇒ 专门验
        #  那条更小的独立阈值 CB_OVERLAP_TOL）。
        # 反向验证（teeth）：删掉 check.analyze 里那段 2b 规则 ⇒ 本节的断言必红。
        # ------------------------------------------------------------------
        _cb_head = (
            "import os\n"
            "import matplotlib\n"
            "matplotlib.use('Agg')\n"
            "import matplotlib.pyplot as plt\n"
            "from matplotlib.cm import ScalarMappable\n"
            "from matplotlib.colors import Normalize\n"
            "fig = plt.figure(figsize=(6.0, 4.0))\n"
            "ax_map = fig.add_axes([0.3621, 0.4550, 0.2758, 0.4349])\n"
            "ax_map.set_aspect('equal')\n"
            "ax_map.plot([0, 1], [0, 1])\n"
            "cax = fig.add_axes([0.33, %s, 0.34, 0.016])\n"
            "fig.colorbar(ScalarMappable(norm=Normalize(0, 1), cmap='viridis'),"
            " cax=cax)\n"
            "fig.savefig(os.path.join(os.path.dirname(os.path.abspath(__file__)),"
            " 'o.png'), dpi=100)\n")

        def _cb_json(name, cby, lang):
            p = os.path.join(tmp, name + '.py')
            with open(p, 'w', encoding='utf-8', newline='\n') as f:
                f.write(_cb_head % cby)
            rc, out, _err = run('check', p, '--json',
                                env={'MPLTWEAK_LANG': lang})
            try:
                return rc, json.loads(out), out
            except Exception:                                      # noqa: BLE001
                return rc, {}, out

        for _lang in ('zh', 'en'):
            _rc16, _j16, _o16 = _cb_json('cb_overlap_%s' % _lang, '0.455', _lang)
            _w16 = [w for w in (_j16.get('warnings') or [])
                    if w.get('rule') == 'colorbar_overlap']
            check(_rc16 == 0 and len(_w16) == 1 and _w16[0]['level'] == 'warn'
                  and sorted(_w16[0]['axes']) == ['ax0', 'ax1'],
                  '§16[%s] colorbar × aspect_locked 轴相交 → 1 条 colorbar_overlap '
                  'warn（过去静默通过）' % _lang,
                  (_rc16, [w.get('rule') for w in (_j16.get('warnings') or [])]))
            if _w16:
                check('colorbar_overlap' in json.dumps(_w16[0], ensure_ascii=False)
                      and bool(_w16[0].get('msg')),
                      '§16[%s] 这条 warn 带人话 msg + 稳定 rule 名' % _lang,
                      _w16[0].get('msg'))
        _rc16b, _j16b, _o16b = _cb_json('cb_overlap_en_txt', '0.455', 'en')
        check(_o16b.isascii(),
              '§16 en 下 check 的整份输出仍是纯 ASCII（含新规则）', _o16b[:120])

        # 反例：colorbar 离地图很远（不相交）⇒ **不许**报这条
        for _lang in ('zh', 'en'):
            _rc17, _j17, _o17 = _cb_json('cb_apart_%s' % _lang, '0.10', _lang)
            check(_rc17 == 0 and not [w for w in (_j17.get('warnings') or [])
                                      if w.get('rule') == 'colorbar_overlap'],
                  '§16[%s] 反例：不重叠的 colorbar **不**误报' % _lang,
                  [w.get('rule') for w in (_j17.get('warnings') or [])])

        # 既有设计不许被顺手改掉：几何比对**仍然**跳过 aspect_locked 轴
        # （两个 aspect 锁定的轴互相重叠 → 不报 overlap；没有 colorbar → 不报新规则）
        _lock = os.path.join(tmp, 'two_locked.py')
        with open(_lock, 'w', encoding='utf-8', newline='\n') as f:
            f.write("import os\n"
                    "import matplotlib\n"
                    "matplotlib.use('Agg')\n"
                    "import matplotlib.pyplot as plt\n"
                    "fig = plt.figure(figsize=(6.0, 4.0))\n"
                    "for _x in (0.10, 0.20):\n"
                    "    _a = fig.add_axes([_x, 0.20, 0.40, 0.40])\n"
                    "    _a.set_aspect('equal')\n"
                    "    _a.plot([0, 1], [0, 1])\n"
                    "fig.savefig(os.path.join(os.path.dirname("
                    "os.path.abspath(__file__)), 'o.png'), dpi=100)\n")
        _rc18, _o18, _e18 = run('check', _lock, '--json',
                                env={'MPLTWEAK_LANG': 'zh'})
        try:
            _j18 = json.loads(_o18)
        except Exception:                                          # noqa: BLE001
            _j18 = {}
        _rules18 = [p.get('rule') for p in (_j18.get('problems') or [])] + \
                   [w.get('rule') for w in (_j18.get('warnings') or [])]
        check('overlap' not in _rules18 and 'colorbar_overlap' not in _rules18,
              '§16 既有设计不变：aspect_locked 轴仍不进几何比对（也不误报新规则）',
              _rules18)
    except Exception as e:                                        # noqa: BLE001
        check(False, '测试执行', e)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # =================================================================
    # §17 t75：**跨读者几何/画布一致性矩阵**（把"两条路径各算一遍"变成可检出的红）
    #
    # 读者（都不开窗，Agg 离屏）：
    #   R1 `toolbox._bbox(ax)`          —— 窗口/交互侧读轴 rect（colorbar 用 original 口径）
    #   R2 `verify._state(fig)['axes']` —— describe/verify 采集侧（**用的就是 R1 的函数**）
    #   R3 `check._pos(item)`           —— check 侧：**只消费 R2 的 pos**，没有独立几何读者
    #   R4 `_canvas_geometry(fig, px)`  —— 窗口侧画布（R2 的画布在 `verify._state` 里另算）
    #   R5 `writeback.count_figures(tree)` —— 静态 AST 侧图数量（R2 给运行时图数量）
    # 容差（依据写在 docs/consistency-ledger.md）：
    #   TOL_RECT = 5e-5  —— R2/R3 把 rect 四舍五入到 **4 位小数**（量子 1e-4 ⇒ 半量子 5e-5）；
    #                       R1 是全精度。这是**已知且刻意**的差异，不是 bug。
    #   TOL_INCH = 5e-3  —— 窗口侧导出 `figsize_in` 保留 **2 位小数**（量子 1e-2 ⇒ 半量子 5e-3）；
    #                       R2 保留 4 位。同上，刻意。
    #   TOL_PX   = 1     —— 像素取整。
    # 接缝覆盖：① colorbar rect（R1 用 `_position` 会与 R2 差 0.087 ⇒ 红）；
    #           ② polar×colorbar 的 ink 重叠在**读者数据**里可见（把该轴从数据里去掉 ⇒ 红）；
    #           ③ 画布 px/in 同源（把 in 算成 px/100 ⇒ dpi=200 时红）。
    # =================================================================
    import ast as _ast17
    import matplotlib.pyplot as _plt17
    from mpltweak import check as _ck17, toolbox as _tb17, verify as _vf17, \
        writeback as _wb17

    TOL_RECT, TOL_INCH, TOL_PX = 5e-5, 5e-3, 1

    _S17 = {
        'polar_cb': ("import matplotlib\nmatplotlib.use('Agg')\n"
                     "import matplotlib.pyplot as plt\nimport numpy as np\n\n"
                     "fig = plt.figure(figsize=(8, 6))\n"
                     "ax = fig.add_subplot(111, projection='polar')\n"
                     "th = np.linspace(0, 6.28, 40)\n"
                     "sc = ax.scatter(th, np.cos(th) * 3, c=np.cos(th))\n"
                     "cb = fig.colorbar(sc, ax=ax)\n"),
        'multi': ("import matplotlib\nmatplotlib.use('Agg')\n"
                  "import matplotlib.pyplot as plt\nimport numpy as np\n\n"
                  "fig, axs = plt.subplots(2, 2, figsize=(10, 7))\n"
                  "im = axs[0, 0].imshow(np.arange(9).reshape(3, 3))\n"
                  "fig.colorbar(im, ax=axs[0, 0])\n"),
        'gridspec': ("import matplotlib\nmatplotlib.use('Agg')\n"
                     "import matplotlib.pyplot as plt\nimport numpy as np\n\n"
                     "fig = plt.figure(figsize=(9, 6))\n"
                     "gs = fig.add_gridspec(2, 3)\n"
                     "axs = [fig.add_subplot(gs[i, j]) for i in range(2) for j in range(3)]\n"),
        'figsize_only': ("import matplotlib\nmatplotlib.use('Agg')\n"
                         "import matplotlib.pyplot as plt\n\n"
                         "fig = plt.figure(figsize=(6, 4))\n"
                         "ax = fig.add_subplot(111)\n"),
        'two_figs': ("import matplotlib\nmatplotlib.use('Agg')\n"
                     "import matplotlib.pyplot as plt\n\n"
                     "fig1 = plt.figure(figsize=(6, 4))\n"
                     "fig1.add_subplot(111)\n"
                     "fig2 = plt.figure(figsize=(6, 4))\n"
                     "fig2.add_subplot(111)\n"),
        'fig_subplots': ("import matplotlib\nmatplotlib.use('Agg')\n"
                         "import matplotlib.pyplot as plt\n\n"
                         "fig = plt.figure(figsize=(8, 6))\n"
                         "axs = fig.subplots(2, 2)\n"),
    }

    def _build17(name):
        _plt17.close('all')
        _ns17 = {'__name__': 'shape17'}
        exec(compile(_S17[name], name, 'exec'), _ns17)
        return _plt17.gcf()

    _bad_rect17, _bad_canvas17, _bad_count17, _rows17 = [], [], [], []
    for _name17 in _S17:
        _f17 = _build17(_name17)
        _f17.canvas.draw()                    # 最坏时刻：locator/aspect 都跑过
        _st17 = _vf17._state(_f17)
        for _i17, _ax17 in enumerate(_f17.axes):
            _r1 = [float(v) for v in _tb17._bbox(_ax17)]
            _r2 = [float(v) for v in (_st17['axes'][_i17].get('pos') or [])]
            _r3 = [float(v) for v in _ck17._pos(_st17['axes'][_i17])]
            _d12 = max(abs(a - b) for a, b in zip(_r1, _r2)) if len(_r2) == 4 else 9e9
            _d23 = max(abs(a - b) for a, b in zip(_r2, _r3)) if len(_r3) == 4 else 9e9
            _rows17.append((_name17, _i17, bool(_tb17._is_colorbar_ax(_ax17)),
                            _d12, _d23))
            if _d12 > TOL_RECT or _d23 > TOL_RECT:
                _bad_rect17.append('%s ax%d cb=%s d12=%.2e d23=%.2e'
                                   % (_name17, _i17,
                                      _tb17._is_colorbar_ax(_ax17), _d12, _d23))
        _cg17 = _tb17._canvas_geometry(_f17, None)
        _px17 = _st17.get('figsize_px')
        _in17 = _st17.get('figsize_in')
        _d_in17 = (max(abs(a - b) for a, b in zip(_cg17['figsize_in'], _in17))
                   if (_cg17['figsize_in'] and _in17) else 9e9)
        # px：窗口侧"没 resize 过"就是 None（参数规范允许）；此时 verify 的 live px 必须
        # 等于 英寸 × fig.dpi（同一物理量的兜底口径）
        _exp_px17 = [int(round(v * float(_f17.dpi))) for v in _f17.get_size_inches()]
        _px_ok17 = True
        if _px17:
            _px_ok17 = (_cg17['figsize_px'] is None
                        and all(abs(a - b) <= TOL_PX for a, b in zip(_px17, _exp_px17)))
        if _d_in17 > TOL_INCH or not _px_ok17:
            _bad_canvas17.append('%s d_in=%.2e px=%s exp=%s'
                                 % (_name17, _d_in17, _px17, _exp_px17))
        _n_ast17 = _wb17.count_figures(_ast17.parse(_S17[_name17]), figure_only=True)
        _n_run17 = len(_plt17.get_fignums())
        if _n_ast17 != _n_run17:
            _bad_count17.append('%s AST=%s 运行时=%s' % (_name17, _n_ast17, _n_run17))
        _plt17.close(_f17)

    check(not _bad_rect17,
          '§17① 轴 rect 三读者一致（R1 `toolbox._bbox` / R2 `verify._state` / R3 '
          '`check._pos`，容差 %g = 采集侧 4 位小数半量子）' % TOL_RECT,
          _bad_rect17[:3])
    check(any(r[2] for r in _rows17),
          '§17② 矩阵**含 colorbar 轴**（接缝①的载体；%d 个形状里 %d 个 colorbar 轴）'
          % (len(_S17), sum(1 for r in _rows17 if r[2])),
          _rows17[:2])
    # 接缝①的**常驻检查**：colorbar 的 rect 必须等于**源码声明值**
    # （`get_position(original=True)`）。依据：`_ColorbarAxesLocator` 每次重绘会改写
    # active position（实测宽度 0.1162 → 0.0289）⇒ 只要某个读者读 active，就会与源码差
    # 一个数量级，`describe → apply` 就会凭空改一个用户没动过的 colorbar（t66/P1-4）。
    _bad_cb17, _n_cb17 = [], 0
    for _name17 in _S17:
        _f17x = _build17(_name17)
        _f17x.canvas.draw()               # 最坏时刻
        _st17x = _vf17._state(_f17x)
        for _i17x, _ax17x in enumerate(_f17x.axes):
            if not _tb17._is_colorbar_ax(_ax17x):
                continue
            _n_cb17 += 1
            _decl17 = [float(v) for v in _ax17x.get_position(original=True).bounds]
            _r1x = [float(v) for v in _tb17._bbox(_ax17x)]
            _r2x = [float(v) for v in (_st17x['axes'][_i17x].get('pos') or [])]
            _dx1 = max(abs(a - b) for a, b in zip(_decl17, _r1x))
            _dx2 = max(abs(a - b) for a, b in zip(_decl17, _r2x))
            if _dx1 > TOL_RECT or _dx2 > TOL_RECT:
                _bad_cb17.append('%s ax%d decl=%s R1=%s R2=%s d1=%.2e d2=%.2e'
                                 % (_name17, _i17x, _decl17, _r1x, _r2x, _dx1, _dx2))
        _plt17.close(_f17x)
    check(_n_cb17 > 0 and not _bad_cb17,
          '§17②b【接缝①·常驻】colorbar rect 的**两个读者都等于源码声明值**'
          '（`get_position(original=True)`，容差 %g）—— %d 个 colorbar 轴'
          % (TOL_RECT, _n_cb17), _bad_cb17[:2])
    check(not _bad_canvas17,
          '§17③ 画布两读者一致（R4 `toolbox._canvas_geometry` vs R2 `verify._state`，'
          '英寸容差 %g = 窗口导出 2 位小数半量子；px 按"没 resize 过为 None"的规范口径）'
          % TOL_INCH, _bad_canvas17[:3])
    check(not _bad_count17,
          '§17④ 图数量两读者一致（R5 静态 `count_figures(figure_only=True)` vs R2 运行时）',
          _bad_count17[:3])

    # --- 接缝②（数据级）：polar × colorbar 的**重叠在读者数据里可见** --------------
    # 报告 P1-3 的现场形状：极地图 rect [0.039, 0.4651, 0.9224, 0.4349] + colorbar
    # rect [0.33, 0.455, 0.34, 0.016] ⇒ y 方向真重叠 [0.4651, 0.471]。
    _f17b = _plt17.figure(figsize=(15, 9.5))
    _pax17 = _f17b.add_axes([0.039, 0.4651, 0.9224, 0.4349], projection='polar')
    _cax17 = _f17b.add_axes([0.33, 0.455, 0.34, 0.016])
    _f17b.canvas.draw()
    _st17b = _vf17._state(_f17b)
    _pb17 = [float(v) for v in _tb17._bbox(_pax17)]
    _cb17 = [float(v) for v in _tb17._bbox(_cax17)]
    _ov_y = min(_pb17[1] + _pb17[3], _cb17[1] + _cb17[3]) - max(_pb17[1], _cb17[1])
    _ov_x = min(_pb17[0] + _pb17[2], _cb17[0] + _cb17[2]) - max(_pb17[0], _cb17[0])
    _ov_state = None
    if len(_st17b['axes']) == 2:
        _s0 = [float(v) for v in _st17b['axes'][0]['pos']]
        _s1 = [float(v) for v in _st17b['axes'][1]['pos']]
        _ov_state = (min(_s0[0] + _s0[2], _s1[0] + _s1[2]) - max(_s0[0], _s1[0]) > 0
                     and min(_s0[1] + _s0[3], _s1[1] + _s1[3]) - max(_s0[1], _s1[1]) > 0)
    check(_ov_x > 0 and _ov_y > 0 and _ov_state is True,
          '§17⑤【接缝②·数据级】报告现场的 polar×colorbar 重叠在**两个读者的数据里都可见**'
          '（R1 x/y 重叠 = %.4f/%.4f；R2 判定 = %s）—— check 侧"看不见"的原因是它把 '
          'aspect_locked 轴排除在几何比对之外（§16 设计），不是数据缺失'
          % (_ov_x, _ov_y, _ov_state),
          (_ov_x, _ov_y, _ov_state))
    _plt17.close(_f17b)

    # --- teeth：单点变异 ⇒ 矩阵必红（三个接缝各一条 + 图数量一条）-----------------
    _orig_bbox17 = _tb17._bbox
    _f17c = _build17('polar_cb')
    try:
        _tb17._bbox = lambda ax: list(ax._position.bounds)      # 改回"只读 active"
        _f17c.canvas.draw()
        _st17c = _vf17._state(_f17c)
        _cax17c = [a for a in _f17c.axes if _tb17._is_colorbar_ax(a)][0]
        _decl17c = [float(v) for v in _cax17c.get_position(original=True).bounds]
        _d17c = max(abs(float(a) - float(b)) for a, b in
                    zip(_tb17._bbox(_cax17c), _decl17c))
    finally:
        _tb17._bbox = _orig_bbox17
        _plt17.close(_f17c)
    check(_d17c > TOL_RECT,
          '§17⑥【有牙·接缝①】把 R1 改回 `ax._position`（active）⇒ colorbar rect 与**源码'
          '声明值**差 %.4f > 容差 %g ⇒ §17②b 必红' % (_d17c, TOL_RECT), _d17c)

    _orig_cg17 = _tb17._canvas_geometry
    _f17d = _plt17.figure(figsize=(6, 4))
    try:
        _tb17._canvas_geometry = lambda fig, px: {
            'figsize_px': list(px) if px else None,
            'figsize_in': [round(v / 100.0, 2) for v in
                           (list(px) if px
                            else [100.0 * x for x in fig.get_size_inches()])]}
        _f17d.dpi = 200.0                                        # dpi=200 现场
        # 现场：英寸 (6,4) × dpi 200 ⇒ 画布 px (1200,800)；历史写法 `px/100` 会算出 12 英寸
        _d17d = abs(_tb17._canvas_geometry(_f17d, (1200, 800))['figsize_in'][0]
                    - _vf17._state(_f17d)['figsize_in'][0])
    finally:
        _tb17._canvas_geometry = _orig_cg17
        _plt17.close(_f17d)
    check(_d17d > TOL_INCH,
          '§17⑦【有牙·接缝③】把 R4 的英寸改回 `px/100`（历史写法；英寸 (6,4) × dpi=200 ⇒ '
          'px (1200,800) ⇒ 历史写法算出 12 英寸）⇒ 与 R2 差 %.3f > 容差 %g ⇒ §17③ 必红'
          % (_d17d, TOL_INCH), _d17d)

    _orig_cf17 = _wb17.count_figures
    try:
        _wb17.count_figures = lambda tree, figure_only=False: \
            _orig_cf17(tree, figure_only=False)                  # 改回 t36 前的口径
        _n17e = _wb17.count_figures(_ast17.parse(_S17['fig_subplots']),
                                    figure_only=True)
    finally:
        _wb17.count_figures = _orig_cf17
    check(_n17e != 1,
          '§17⑧【有牙·图数量】把 R5 改回 t36 前口径（`fig.subplots` 也算新图）⇒ '
          'fig_subplots 报 %s 张而运行时只有 1 张 ⇒ §17④ 必红' % (_n17e,), _n17e)

    print()
    if _fails:
        print('FAILED: %d 项 -> %s' % (len(_fails), _fails))
        return 1
    print('ALL PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
