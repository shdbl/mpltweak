# -*- coding: utf-8 -*-
# writeback 引擎 golden 测试（自包含：临时目录现场造脚本 + 参数 JSON）：
#   1. 零 LLM 写回：fig 变量解析 / savefig 前插入 / figsize 原位替换
#   2. colorbar 轴用 _colorbar.mappable 结构导航（不依赖首次 draw）
#   3. 幂等：重复写回 = 整体替换旧块，不叠加
#   4. 安全网：写回后 Agg 重跑失败 → 自动回滚备份
import os
import shutil
import sys
import json
import tempfile

from mpltweak import apply, launch, writeback

fail = 0

# 沙箱：测试只能写 workspace 内；用测试文件旁目录做临时区
_TMPROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.tmp_writeback')


def check(name, cond, detail=''):
    global fail
    print(('PASS' if cond else 'FAIL') + ' | ' + name + ((' | ' + detail) if detail else ''))
    if not cond:
        fail += 1


SCRIPT = '''# 测试脚本
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

fig, axes = plt.subplots(1, 2, figsize=(10, 4))
ax1, ax2 = axes
ax1.plot([1, 2, 3], [1, 4, 9], label='series')
ax1.legend(loc='upper left')
im = ax2.imshow(np.arange(12).reshape(3, 4), cmap='viridis')
fig.colorbar(im, ax=ax2)
plt.tight_layout()
fig.savefig('out.png')
print('done')
'''

PARAMS = {
    'version': 3,
    'script': 'target.py',
    'figsize_px': [1200, 480],
    'figsize_in': [12.0, 4.8],
    'axes': [
        {'index': 0, 'pos': [0.05, 0.1, 0.42, 0.78], 'aspect_locked': False,
         'title_fontsize': None, 'label_fontsize': 12, 'tick_fontsize': 10,
         'is_colorbar': False, 'clim': None, 'xscale': 'linear',
         'yscale': 'linear', 'grid': True,
         'spines': {'top': False, 'right': False, 'bottom': True, 'left': True},
         'lines': [{'index': 0, 'linewidth': 2.5, 'color': '#d62728'}],
         'legend': {'loc': 'upper right', 'anchor': None, 'fontsize': 11}},
        {'index': 1, 'pos': [0.55, 0.1, 0.36, 0.78], 'aspect_locked': True,
         'title_fontsize': 13, 'label_fontsize': None, 'tick_fontsize': None,
         'is_colorbar': False, 'clim': [0.0, 15.0], 'cmap': 'plasma',
         'xscale': 'linear', 'yscale': 'linear', 'grid': False,
         'spines': {}, 'lines': []},
        {'index': 2, 'pos': [0.93, 0.1, 0.03, 0.78], 'aspect_locked': False,
         'title_fontsize': None, 'label_fontsize': None, 'tick_fontsize': None,
         'is_colorbar': True, 'clim': [0.0, 15.0], 'cmap': 'plasma',
         'xscale': 'linear', 'yscale': 'linear', 'grid': False,
         'spines': {}, 'lines': []},
    ],
}


_case = [0]


def setup():
    _case[0] += 1
    os.makedirs(_TMPROOT, exist_ok=True)
    d = os.path.join(_TMPROOT, 'case_%d' % _case[0])
    os.makedirs(d, exist_ok=True)     # 沙箱坑：tempfile.mkdtemp 建的目录写不进，makedirs 可以
    script = os.path.join(d, 'target.py')
    with open(script, 'w', encoding='utf-8') as f:
        f.write(SCRIPT)
    pp = os.path.join(d, 'target.json')          # 侧边元数据同目录
    return d, script, pp


def read(script):
    with open(script, 'r', encoding='utf-8') as f:
        return f.read()


# ---- 1. 主流程 ----
d, script, pp = setup()
res = writeback.writeback(script, PARAMS, pp, verify=True)
check('reason=OK', res['reason'] == 'ok', res['reason'])
check('fig 变量=fig', res.get('fig_var') == 'fig', str(res.get('fig_var')))
check('figsize 原位替换', res.get('figsize_edited') is True)
check('插入点=before(savefig)', res.get('anchor') == 'before', str(res.get('anchor')))
check('Agg 验证通过', res.get('verified') is True)
src = read(script)
check('figsize 已替换 (12, 4.8)',
      "figsize=(12, 4.8)" in src, 'figsize=(10, 4)' in src and 'FAIL 未替换')
check('colorbar 用 _colorbar.mappable 结构导航',
      'fig.axes[2]._colorbar.mappable.set_clim(0, 15)' in src)
check('非 colorbar mappable 用变量 im',
      'im.set_clim(0, 15)' in src and "im.set_cmap('plasma')" in src)
check('块在 savefig 之前', src.index(writeback.BLOCK_START) < src.index('fig.savefig'))
check('备份已生成', res.get('backup') and os.path.exists(res['backup']))

# ---- 2. 幂等：重复写回不叠加 ----
res2 = writeback.writeback(script, PARAMS, pp, verify=True)
src2 = read(script)
check('再次写回 reason=OK', res2['reason'] == 'ok', res2['reason'])
check('块标记仍各 1 个',
      src2.count(writeback.BLOCK_START) == 1 and src2.count(writeback.BLOCK_END) == 1,
      'start=%d end=%d' % (src2.count(writeback.BLOCK_START),
                           src2.count(writeback.BLOCK_END)))

# ---- 3. 安全网：写回后运行失败 → 回滚 ----
d3, script3, pp3 = setup()
# 造一个"写回后必炸"的脚本：纯折线图无 mappable → clim 写回走 collections[0] 兜底
# → 运行期 collections 为空 IndexError → 验证失败 → 自动回滚
with open(script3, 'w', encoding='utf-8') as f:
    f.write('import matplotlib\nmatplotlib.use("Agg")\n'
            'import matplotlib.pyplot as plt\n'
            'fig, ax = plt.subplots(figsize=(5, 3))\n'
            'ax.plot([1, 2, 3])\n'
            'fig.savefig("o.png")\n')
params_bad = {
    'version': 3, 'script': 'target.py', 'figsize_px': None, 'figsize_in': None,
    'axes': [
        {'index': 0, 'pos': None, 'aspect_locked': False,
         'title_fontsize': None, 'label_fontsize': None, 'tick_fontsize': None,
         'is_colorbar': False, 'clim': [0.0, 15.0], 'cmap': 'plasma',
         'xscale': 'linear', 'yscale': 'linear', 'grid': False,
         'spines': {}, 'lines': []},
    ],
}
before = read(script3)
res3 = writeback.writeback(script3, params_bad, pp3, verify=True)
check('坏参数写回 reason=FAIL', res3['reason'] == 'fail', res3['reason'])
check('验证失败已标记', res3['verified'] is False)
check('已回滚到原内容', read(script3) == before, '')

# ---- 4a. 纯 pyplot 流 → plt.gcf() 兜底（真实老代码的主力形态），且写回后真能跑 ----
d4, script4, pp4 = setup()
with open(script4, 'w', encoding='utf-8') as f:
    f.write('import matplotlib\nmatplotlib.use("Agg")\n'
            'import matplotlib.pyplot as plt\n'
            'plt.figure(figsize=(6, 4))\n'
            'plt.plot([1, 2, 3])\n'
            'plt.savefig("o.png")\n')
params_gcf = {
    'version': 3, 'script': 'target.py', 'figsize_px': [600, 400],
    'figsize_in': [6.0, 4.0],
    'axes': [{'index': 0, 'pos': [0.15, 0.15, 0.75, 0.75], 'aspect_locked': False,
              'title_fontsize': 14, 'label_fontsize': None, 'tick_fontsize': None,
              'is_colorbar': False, 'clim': None, 'cmap': None,
              'xscale': 'linear', 'yscale': 'linear', 'grid': True,
              'spines': {}, 'lines': []}],
}
res4 = writeback.writeback(script4, params_gcf, pp4, verify=True)
check('pyplot 流走 gcf 兜底且运行通过',
      res4.get('gcf_fallback') is True and res4['reason'] == 'ok'
      and res4.get('verified') is True,
      'gcf=%s reason=%s verified=%s'
      % (res4.get('gcf_fallback'), res4['reason'], res4.get('verified')))
blk4 = res4['block'] or ''
check('gcf 兜底块用 plt.gcf() 寻址',
      'plt.gcf().axes[0]' in blk4 and 'zip(plt.gcf().axes' in blk4)
src4 = read(script4)
check('gcf 兜底块插在 savefig 之前',
      src4.index(writeback.BLOCK_START) < src4.index('plt.savefig'))

# ---- 4b. 无 plt 导入且无 fig 变量 → NO_FIG（不瞎猜）----
d4b, script4b, pp4b = setup()
with open(script4b, 'w', encoding='utf-8') as f:
    f.write('from matplotlib.figure import Figure\n'
            'obj = Figure()\n'
            'obj.savefig("o.png")\n')
res4b = writeback.writeback(script4b, PARAMS, pp4b, verify=False)
check('无 plt 导入 → NO_FIG', res4b['reason'] == 'no_fig', res4b['reason'])
check('NO_FIG 不落盘', read(script4b).startswith('from matplotlib.figure'))
shutil.rmtree(d4b, ignore_errors=True)

# ---- 5. 真实脚本边角（2026-10 真实 NWCP 脚本 dry-run 发现）----
#   colorbar 轴 xscale='function'：裸 set_xscale('function') 运行时报错 → 不生成
#   cmap='from_list'（ListedColormap 名）：不是已注册 colormap → 跳过 + 警告
d5, script5, pp5 = setup()
params_edge = {
    'version': 3, 'script': 'target.py', 'figsize_px': None, 'figsize_in': None,
    'axes': [
        {'index': 0, 'pos': None, 'aspect_locked': False,
         'title_fontsize': None, 'label_fontsize': None, 'tick_fontsize': None,
         'is_colorbar': True, 'clim': [0.0, 1.0], 'cmap': 'from_list',
         'xscale': 'function', 'yscale': 'linear', 'grid': False,
         'spines': {}, 'lines': []},
        {'index': 1, 'pos': None, 'aspect_locked': False,
         'title_fontsize': None, 'label_fontsize': None, 'tick_fontsize': None,
         'is_colorbar': False, 'clim': None, 'cmap': None,
         'xscale': 'log', 'yscale': 'linear', 'grid': False,
         'spines': {}, 'lines': []},
    ],
}
res5 = writeback.writeback(script5, params_edge, pp5, verify=False)
block5 = res5['block'] or ''
check('colorbar function 刻度不生成', "set_xscale('function')" not in block5, '')
check('from_list cmap 跳过', "set_cmap('from_list')" not in block5, '')
check('from_list cmap 有警告', any('from_list' in w for w in res5['warnings']), '')
check('普通轴 log 刻度仍生成', "set_xscale('log')" in block5, '')
shutil.rmtree(d5, ignore_errors=True)

# ---- 6. 多图脚本：改用 plt.gcf() + 最后一个 savefig 锚点 ----
#   （fig 变量可能停在早先那张图上 → fig.axes 空 → IndexError；实测踩到）
d6, script6, pp6 = setup()
with open(script6, 'w', encoding='utf-8') as f:
    f.write('import matplotlib\nmatplotlib.use("Agg")\n'
            'import matplotlib.pyplot as plt\n'
            'fig = plt.figure(figsize=(5, 3))\n'
            'ax0 = fig.add_subplot(111)\n'
            'fig.savefig("a.png")\n'
            'plt.figure(figsize=(5, 3))\n'
            'ax1 = plt.gca()\n'
            'plt.savefig("b.png")\n')
res6 = writeback.writeback(script6, params_gcf, pp6, verify=True)
check('多图脚本改用 plt.gcf()',
      res6.get('gcf_fallback') is True and res6.get('fig_var') == 'plt.gcf()',
      'gcf=%s fig=%s' % (res6.get('gcf_fallback'), res6.get('fig_var')))
check('多图锚点 = 最后一个 savefig',
      res6.get('anchor') == 'before' and res6.get('anchor_line') == 9,
      'anchor=%s line=%s' % (res6.get('anchor'), res6.get('anchor_line')))
check('多图写回 Agg 验证通过', res6.get('verified') is True, str(res6.get('verified')))
shutil.rmtree(d6, ignore_errors=True)

# ---- 7. 多图 + fig_index：按记录的图号定位（不再只会调最后一张）----
d7, script7, pp7 = setup()
with open(script7, 'w', encoding='utf-8') as f:
    f.write('import matplotlib\nmatplotlib.use("Agg")\n'
            'import matplotlib.pyplot as plt\n'
            'fig = plt.figure(figsize=(5, 3))\n'
            'ax0 = fig.add_subplot(111)\n'
            'ax0.plot([1, 2, 3])\n'
            'fig.savefig("a.png")\n'
            'fig2 = plt.figure(figsize=(5, 3))\n'
            'ax1 = fig2.add_subplot(111)\n'
            'ax1.plot([3, 2, 1])\n'
            'fig2.savefig("b.png")\n')
params_first = dict(params_gcf)
params_first['fig_index'] = 0            # 本次调的是第一张图
params_first['n_figs'] = 2
res7 = writeback.writeback(script7, params_first, pp7, verify=True)
check('fig_index=0 → 锚定第一个 savefig（第 7 行）',
      res7.get('anchor') == 'before' and res7.get('anchor_line') == 7,
      'anchor=%s line=%s' % (res7.get('anchor'), res7.get('anchor_line')))
check('fig_index=0 写回验证通过', res7.get('verified') is True, str(res7.get('verified')))
src7 = read(script7)
check('块确实插在 a.png 的 savefig 之前',
      src7.index(writeback.BLOCK_START) < src7.index('fig.savefig("a.png")'))
shutil.rmtree(d7, ignore_errors=True)

# ---- 8. 语义验证 A/B：布局被后续代码覆盖 ----
#   脚本存图后又调 subplots_adjust 把位置重置 → "插在 savefig 前"的块能跑通但白改。
#   无语义验证：静默放过；有语义验证：判失败 → 换到脚本尾锚点 → 位置真的生效。
SCRIPT8 = ('import matplotlib\nmatplotlib.use("Agg")\n'
           'import matplotlib.pyplot as plt\n'
           'fig, ax = plt.subplots(figsize=(6, 4))\n'
           'ax.plot([1, 2, 3])\n'
           'fig.savefig("o.png")\n'
           'plt.subplots_adjust(left=0.30, right=0.90, top=0.90, bottom=0.20)\n')
params_move = {
    'version': 3, 'script': 'target.py', 'figsize_px': None, 'figsize_in': None,
    'axes': [{'index': 0, 'pos': [0.05, 0.05, 0.5, 0.5], 'aspect_locked': False,
              'title_fontsize': None, 'label_fontsize': None, 'tick_fontsize': None,
              'is_colorbar': False, 'clim': None, 'cmap': None,
              'xscale': 'linear', 'yscale': 'linear', 'grid': None,
              'spines': {}, 'lines': []}],
}
d8, script8, pp8 = setup()
with open(script8, 'w', encoding='utf-8') as f:
    f.write(SCRIPT8)
res8 = writeback.writeback(script8, params_move, pp8, verify=True, semantic=True)
check('语义验证识别"布局被覆盖"→ 换到脚本尾',
      res8.get('anchor') == 'tail' and res8.get('verified') is True
      and res8.get('semantic') is True,
      'anchor=%s verified=%s semantic=%s'
      % (res8.get('anchor'), res8.get('verified'), res8.get('semantic')))
shutil.rmtree(d8, ignore_errors=True)

d8b, script8b, pp8b = setup()
with open(script8b, 'w', encoding='utf-8') as f:
    f.write(SCRIPT8)
res8b = writeback.writeback(script8b, params_move, pp8b, verify=True, semantic=False)
check('（对照）关掉语义验证 → 静默放过',
      res8b.get('anchor') == 'before' and res8b.get('verified') is True,
      'anchor=%s' % res8b.get('anchor'))
shutil.rmtree(d8b, ignore_errors=True)

# ---- 9. B2：循环出图 + fig_index → 加 if 守卫只改目标那张 ----
d9, script9, pp9 = setup()
with open(script9, 'w', encoding='utf-8') as f:
    f.write('import matplotlib\nmatplotlib.use("Agg")\n'
            'import matplotlib.pyplot as plt\n'
            'for i in range(4):\n'
            '    plt.figure(figsize=(5, 3))\n'
            '    plt.plot([1, 2, 3])\n'
            '    plt.savefig("f%d.png" % i)\n')
params_loop = {
    'version': 3, 'script': 'target.py', 'figsize_px': None, 'figsize_in': None,
    'fig_index': 2, 'n_figs': 4,
    'axes': [{'index': 0, 'pos': [0.15, 0.15, 0.7, 0.7], 'aspect_locked': False,
              'title_fontsize': None, 'label_fontsize': None, 'tick_fontsize': None,
              'is_colorbar': False, 'clim': None, 'cmap': None,
              'xscale': 'linear', 'yscale': 'linear', 'grid': None,
              'spines': {}, 'lines': []}],
}
res9 = writeback.writeback(script9, params_loop, pp9, verify=True, semantic=True)
blk9 = res9['block'] or ''
check('B2 循环处加 if 守卫（i == 2）', 'if i == 2:' in blk9,
      blk9.splitlines()[1] if len(blk9.splitlines()) > 1 else '')
check('B2 守卫块写回后验证通过',
      res9.get('verified') is True and res9.get('semantic') is True,
      'v=%s semantic=%s' % (res9.get('verified'), res9.get('semantic')))
_src9 = read(script9)
check('B2 守卫块仍在循环内、savefig 之前',
      _src9.index(writeback.BLOCK_START) < _src9.index('plt.savefig'))
shutil.rmtree(d9, ignore_errors=True)

# ---- 10. B3：多 mappable 轴按接收者 / colorbar 精确配对 ----
d10, script10, pp10 = setup()
with open(script10, 'w', encoding='utf-8') as f:
    f.write('import matplotlib\nmatplotlib.use("Agg")\n'
            'import matplotlib.pyplot as plt\n'
            'import numpy as np\n'
            'fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(8, 3))\n'
            'a = ax1.contourf(np.arange(12).reshape(3, 4))\n'
            'b = ax2.contourf(np.arange(12).reshape(3, 4) * 10)\n'
            'fig.colorbar(a, ax=ax1)\n'
            'fig.colorbar(b, ax=ax2)\n'
            'fig.savefig("o.png")\n')


def _mm(i, clim, cmap):
    return {'index': i, 'pos': None, 'aspect_locked': False,
            'title_fontsize': None, 'label_fontsize': None, 'tick_fontsize': None,
            'is_colorbar': False, 'clim': clim, 'cmap': cmap,
            'xscale': 'linear', 'yscale': 'linear', 'grid': None,
            'spines': {}, 'lines': []}


params_mm = {'version': 3, 'script': 'target.py', 'figsize_px': None,
             'figsize_in': None,
             'axes': [_mm(0, [0.0, 10.0], 'viridis'), _mm(1, [0.0, 100.0], 'plasma')]}
res10 = writeback.writeback(script10, params_mm, pp10, verify=True, semantic=True)
blk10 = res10['block'] or ''
check('B3 ax0 配到 a、ax1 配到 b（不再都取排序第一个）',
      'a.set_clim(0, 10)' in blk10 and 'b.set_clim(0, 100)' in blk10, '')
check('B3 不再报"多个 mappable"警告',
      not any('多个 mappable' in w for w in res10.get('warnings', [])),
      '; '.join(res10.get('warnings', []))[:60])
check('B3 写回后语义验证通过（clim 真的落在各自轴上）',
      res10.get('verified') is True and res10.get('semantic') is True,
      'v=%s semantic=%s' % (res10.get('verified'), res10.get('semantic')))
shutil.rmtree(d10, ignore_errors=True)

# ---- 11. 多图同时改：每个改过的图各一份参数 → apply 逐张落实 ----
d11, script11, pp11 = setup()
with open(script11, 'w', encoding='utf-8') as f:
    f.write('import matplotlib\nmatplotlib.use("Agg")\n'
            'import matplotlib.pyplot as plt\n'
            'fig1, ax1 = plt.subplots(figsize=(5, 3))\n'
            'ax1.plot([1, 2, 3])\n'
            'fig1.savefig("a.png")\n'
            'fig2, ax2 = plt.subplots(figsize=(5, 3))\n'
            'ax2.plot([3, 2, 1])\n'
            'fig2.savefig("b.png")\n')


def _mkpar(idx, pos):
    return {'version': 3, 'script': 'target.py', 'figsize_px': None,
            'figsize_in': None, 'fig_index': idx, 'n_figs': 2,
            'axes': [{'index': 0, 'pos': pos, 'aspect_locked': False,
                      'title_fontsize': None, 'label_fontsize': None,
                      'tick_fontsize': None, 'is_colorbar': False,
                      'clim': None, 'cmap': None, 'xscale': 'linear',
                      'yscale': 'linear', 'grid': None,
                      'spines': {}, 'lines': []}]}


# 模拟 launch 的产物：每张改过的图一份（文件名带图号），最后改的另存主文件
_pd = os.path.join(d11, '.tweak_params')
os.makedirs(_pd, exist_ok=True)
for _name, _idx, _pos in (('target.fig0.json', 0, [0.10, 0.10, 0.5, 0.5]),
                          ('target.json', 1, [0.40, 0.40, 0.5, 0.5])):
    with open(os.path.join(_pd, _name), 'w', encoding='utf-8') as f:
        json.dump(_mkpar(_idx, _pos), f, ensure_ascii=False)

_rc = apply.main([script11, '--write', '--style', 'block'])
_src11 = read(script11)
check('多图：apply 返回 0', _rc == 0, 'rc=%s' % _rc)
check('多图：两份参数都落实了（脚本里 2 个调整块）',
      _src11.count(writeback.BLOCK_START) == 2,
      'blocks=%d' % _src11.count(writeback.BLOCK_START))
check('多图：第 1 张的块在 a.png 之前',
      _src11.index(writeback.BLOCK_START) < _src11.index('fig1.savefig("a.png")'))
check('多图：第 2 张的块在 b.png 之前',
      _src11.rindex(writeback.BLOCK_START) < _src11.index('fig2.savefig("b.png")'))
shutil.rmtree(d11, ignore_errors=True)

# ---- 12. 多图参数文件生命周期：改动过的保留、未改动的清掉（含 .lock）----
#   坑：<stem>.fig<k>.json 既是过程中写的那份、也是最终正式参数（同名），
#   收尾若无脑全删 → "一次改多张"会被清空。
d12, script12, pp12 = setup()
_pd12 = os.path.join(d12, '.tweak_params')
os.makedirs(_pd12, exist_ok=True)
_tmps = {}
for _k in range(3):
    _p = os.path.join(_pd12, 'target.fig%d.json' % _k)
    with open(_p, 'w', encoding='utf-8') as f:
        json.dump(_mkpar(_k, [0.1 + 0.1 * _k, 0.1, 0.5, 0.5]), f)
    with open(_p + '.lock', 'w', encoding='utf-8') as f:
        f.write('123')
    _tmps[_k] = _p
launch._prune_fig_params(_tmps, [1])          # 只保留第 2 张（它改动过）
check('多图：改动过的参数文件被保留', os.path.exists(_tmps[1]))
check('多图：未改动的参数文件被清掉',
      not os.path.exists(_tmps[0]) and not os.path.exists(_tmps[2]))
check('多图：所有 .lock 都清掉',
      not any(os.path.exists(_tmps[k] + '.lock') for k in _tmps))
shutil.rmtree(d12, ignore_errors=True)

# ---- 13. 原位写回：直接改原代码数字，不插调整块（用户主推方式）----
d13, script13, pp13 = setup()
with open(script13, 'w', encoding='utf-8') as f:
    f.write('import matplotlib\nmatplotlib.use("Agg")\n'
            'import matplotlib.pyplot as plt\n'
            'fig = plt.figure(figsize=(6, 4))\n'
            'ax = fig.add_axes([0.10, 0.10, 0.70, 0.70])\n'
            'ax.plot([1, 2, 3])\n'
            'ax.set_title("t", fontsize=14)\n'
            'ax.set_xlabel("x", fontsize=11)\n'
            'ax.tick_params(labelsize=9)\n'
            'ax.grid(True)\n'
            'fig.savefig("o.png")\n')
params_ip = {'version': 3, 'script': 'target.py', 'figsize_px': None,
             'figsize_in': None,
             'axes': [{'index': 0, 'pos': [0.12, 0.15, 0.60, 0.65],
                       'aspect_locked': False, 'title_fontsize': 16,
                       'label_fontsize': 12, 'tick_fontsize': 8,
                       'is_colorbar': False, 'clim': None, 'cmap': None,
                       'xscale': 'linear', 'yscale': 'linear', 'grid': False,
                       'spines': {}, 'lines': [],
                       'legend': {'loc': 'upper right', 'anchor': None,
                                  'fontsize': 11}}]}
res13 = writeback.writeback(script13, params_ip, pp13, verify=True, semantic=True,
                            style='inplace')
_s13 = read(script13)
check('原位：add_axes 的 4 个数字被替换',
      'add_axes([0.12, 0.15, 0.6, 0.65])' in _s13
      or 'add_axes([0.12, 0.15, 0.60, 0.65])' in _s13, _s13.splitlines()[3])
check('原位：title 字号被替换', 'set_title("t", fontsize=16)' in _s13)
check('原位：grid 被替换', 'ax.grid(False)' in _s13)
check('原位：不再插入调整块', writeback.BLOCK_START not in _s13)
check('原位：Agg + 语义验证通过',
      res13.get('verified') is True and res13.get('semantic') is True,
      'v=%s s=%s' % (res13.get('verified'), res13.get('semantic')))
check('原位：legend 改不了 → 有跳过警告',
      any('legend' in w for w in res13.get('warnings', [])), '')
check('原位：备份在 .tweak_params 里',
      os.path.exists(os.path.join(os.path.dirname(pp13), 'target.tweak.bak')),
      str(res13.get('backup')))
_before13 = read(script13)
writeback.writeback(script13, params_ip, pp13, verify=False, style='inplace')
check('原位：幂等（第二次写回不再改动文件）', read(script13) == _before13)
shutil.rmtree(d13, ignore_errors=True)

# ---- 14. 原位写回 + figsize：多图按 fig_index 定位、替换后坐标不错位 ----
#   坑：edit_figsize 改了文件长度后原 tree 坐标失效 → 后续替换全错位（实测
#   ax_d.bar→ax_d.12r / tick_params→tick_param14labelsize）；且 figsize 必须
#   改**对应图**的调用，不能总改第一个。
d14, script14, pp14 = setup()
with open(script14, 'w', encoding='utf-8') as f:
    f.write('import matplotlib\nmatplotlib.use("Agg")\n'
            'import matplotlib.pyplot as plt\n'
            'fig1 = plt.figure(figsize=(11, 8))\n'
            'ax_a = fig1.add_axes([0.06, 0.56, 0.46, 0.36])\n'
            'ax_a.set_title("a", fontsize=14)\n'
            'ax_a.plot([1, 2, 3])\n'
            'fig1.savefig("a.png")\n'
            'fig2 = plt.figure(figsize=(9, 4))\n'
            'ax_b = fig2.add_axes([0.10, 0.10, 0.70, 0.70])\n'
            'ax_b.set_title("b", fontsize=12)\n'
            'ax_b.plot([1, 2, 3])\n'
            'fig2.savefig("b.png")\n')
params14 = {'version': 3, 'script': 'target.py', 'fig_index': 1, 'n_figs': 2,
            'figsize_px': None, 'figsize_in': [4.7, 3.91],
            'axes': [{'index': 0, 'pos': [0.12, 0.15, 0.60, 0.65],
                      'aspect_locked': False, 'title_fontsize': 16,
                      'label_fontsize': None, 'tick_fontsize': None,
                      'is_colorbar': False, 'clim': None, 'cmap': None,
                      'xscale': 'linear', 'yscale': 'linear', 'grid': None,
                      'spines': {}, 'lines': [], 'legend': None}]}
res14 = writeback.writeback(script14, params14, pp14, verify=True, semantic=True,
                            style='inplace')
_s14 = read(script14)
check('原位+figsize：fig1 的 figsize 不被误改',
      'fig1 = plt.figure(figsize=(11, 8))' in _s14)
check('原位+figsize：fig2 的 figsize 被替换',
      'fig2 = plt.figure(figsize=(4.7, 3.91))' in _s14)
check('原位+figsize：fig2 的 add_axes 数字替换不错位',
      'ax_b = fig2.add_axes([0.12, 0.15, 0.6, 0.65])' in _s14
      or 'ax_b = fig2.add_axes([0.12, 0.15, 0.60, 0.65])' in _s14, '')
check('原位+figsize：fig2 的 title 字号替换', 'ax_b.set_title("b", fontsize=16)' in _s14)
check('原位+figsize：验证通过',
      res14.get('verified') is True and res14.get('semantic') is True,
      'v=%s s=%s' % (res14.get('verified'), res14.get('semantic')))
shutil.rmtree(d14, ignore_errors=True)

# ---- 18. colorbar 显式 cax=fig.add_axes() 夹在中间：轴-变量配对不错位 ----
#   坑：cax 也是 add_axes 创建的，若不剔除会占掉一个"非 colorbar 轴"配对位，
#   导致 ax1 的位置被改到 cax 的源码上（且 cax 不在语义验证范围 → 可静默错写）。
d18, script18, pp18 = setup()
with open(script18, 'w', encoding='utf-8') as f:
    f.write('import matplotlib\nmatplotlib.use("Agg")\n'
            'import matplotlib.pyplot as plt\n'
            'import numpy as np\n'
            'fig = plt.figure(figsize=(6, 4))\n'
            'ax0 = fig.add_axes([0.10, 0.60, 0.30, 0.30])\n'
            'cax = fig.add_axes([0.85, 0.60, 0.03, 0.30])\n'
            'ax1 = fig.add_axes([0.10, 0.10, 0.30, 0.30])\n'
            'im = ax0.imshow(np.random.rand(8, 8))\n'
            'fig.colorbar(im, cax=cax)\n'
            'ax1.plot([1, 2, 3])\n'
            'fig.savefig("o.png")\n')
def _ax18(i, pos, is_cb=False):
    return {'index': i, 'pos': pos, 'aspect_locked': bool(is_cb),
            'title_fontsize': None, 'label_fontsize': None,
            'tick_fontsize': None, 'is_colorbar': is_cb,
            'clim': [0.0, 1.0] if is_cb else None,
            'cmap': 'viridis' if is_cb else None,
            'xscale': 'linear', 'yscale': 'linear', 'grid': None,
            'spines': {}, 'lines': [], 'legend': None}
params18 = {'version': 3, 'script': 'target.py', 'fig_index': 0, 'n_figs': 1,
            'figsize_px': None, 'figsize_in': None,
            'axes': [_ax18(0, [0.10, 0.60, 0.30, 0.30]),
                     _ax18(1, [0.85, 0.60, 0.03, 0.30], True),
                     _ax18(2, [0.12, 0.15, 0.34, 0.26])]}
res18 = writeback.writeback(script18, params18, pp18, verify=True, semantic=True,
                            style='inplace')
_s18 = read(script18)
check('cax：ax1(idx2) 的位置数字被改',
      'ax1 = fig.add_axes([0.12, 0.15, 0.34, 0.26])' in _s18, '')
check('cax：colorbar 槽(cax) 不被误改',
      'cax = fig.add_axes([0.85, 0.6, 0.03, 0.3])' in _s18
      or 'cax = fig.add_axes([0.85, 0.60, 0.03, 0.30])' in _s18, '')
check('cax：验证通过',
      res18.get('verified') is True and res18.get('semantic') is True,
      'v=%s s=%s' % (res18.get('verified'), res18.get('semantic')))
shutil.rmtree(d18, ignore_errors=True)

# ---- 19. 中文脚本：ast col_offset 是 UTF-8 字节偏移，含中文的 set_title/set_ylabel
#   字号替换不错位（实测修前 '(a) 三角函数' 的 fontsize 替换偏 8 字符 → ax_d.bar
#   被写成 ax_d.12r）。 ----
d19, script19, pp19 = setup()
with open(script19, 'w', encoding='utf-8') as f:
    f.write('import matplotlib\nmatplotlib.use("Agg")\n'
            'import matplotlib.pyplot as plt\n'
            'fig = plt.figure(figsize=(6, 4))\n'
            'ax = fig.add_axes([0.10, 0.10, 0.70, 0.70])\n'
            'ax.plot([1, 2, 3])\n'
            "ax.set_title('(a) 三角函数', fontsize=17)\n"
            "ax.set_ylabel('数值', fontsize=16)\n"
            'fig.savefig("o.png")\n')
params19 = {'version': 3, 'script': 'target.py', 'fig_index': 0, 'n_figs': 1,
            'figsize_px': None, 'figsize_in': None,
            'axes': [{'index': 0, 'pos': [0.10, 0.10, 0.70, 0.70],
                      'aspect_locked': False, 'title_fontsize': 13,
                      'label_fontsize': 10, 'tick_fontsize': None,
                      'is_colorbar': False, 'clim': None, 'cmap': None,
                      'xscale': 'linear', 'yscale': 'linear', 'grid': None,
                      'spines': {}, 'lines': [], 'legend': None}]}
res19 = writeback.writeback(script19, params19, pp19, verify=True, semantic=True,
                            style='inplace')
_s19 = read(script19)
check('中文：set_title 字号替换不错位',
      "ax.set_title('(a) 三角函数', fontsize=13)" in _s19, '')
check('中文：set_ylabel 字号替换不错位',
      "ax.set_ylabel('数值', fontsize=10)" in _s19, '')
check('中文：add_axes 不被破坏',
      'ax = fig.add_axes([0.1, 0.1, 0.7, 0.7])' in _s19
      or 'ax = fig.add_axes([0.10, 0.10, 0.70, 0.70])' in _s19, '')
check('中文：验证通过',
      res19.get('verified') is True and res19.get('semantic') is True,
      'v=%s s=%s' % (res19.get('verified'), res19.get('semantic')))
shutil.rmtree(d19, ignore_errors=True)

# ---- 20. colorbar 宿主轴：fig.colorbar(..., ax=axX) 会在创建时重定位宿主轴，
#   add_axes 数字重跑时被 fraction 覆盖 → pos 原位改无效，必须 skipped + 警告，
#   且不参与语义验证（否则误报位置不一致而回滚）。 ----
d20, script20, pp20 = setup()
with open(script20, 'w', encoding='utf-8') as f:
    f.write('import matplotlib\nmatplotlib.use("Agg")\n'
            'import matplotlib.pyplot as plt\n'
            'import numpy as np\n'
            'fig = plt.figure(figsize=(6, 4))\n'
            'ax0 = fig.add_axes([0.10, 0.10, 0.60, 0.70])\n'
            'im = ax0.imshow(np.random.rand(8, 8))\n'
            'fig.colorbar(im, ax=ax0, fraction=0.15, pad=0.05)\n'
            'ax0.set_title("t", fontsize=12)\n'
            'fig.savefig("o.png")\n')
def _ax20(i, pos, is_cb=False, title=None):
    return {'index': i, 'pos': pos, 'aspect_locked': True,
            'title_fontsize': title, 'label_fontsize': None,
            'tick_fontsize': None, 'is_colorbar': is_cb,
            'clim': [0.0, 1.0] if is_cb else None,
            'cmap': 'plasma' if is_cb else None,
            'xscale': 'linear', 'yscale': 'linear', 'grid': None,
            'spines': {}, 'lines': [], 'legend': None}
params20 = {'version': 3, 'script': 'target.py', 'fig_index': 0, 'n_figs': 1,
            'figsize_px': None, 'figsize_in': None,
            'axes': [_ax20(0, [0.12, 0.15, 0.55, 0.62], title=15),
                     _ax20(1, [0.75, 0.10, 0.05, 0.70], True)]}
res20 = writeback.writeback(script20, params20, pp20, verify=True, semantic=True,
                            style='inplace')
_s20 = read(script20)
check('宿主轴：add_axes 数字不被改（原位无效）',
      'ax0 = fig.add_axes([0.1, 0.1, 0.6, 0.7])' in _s20
      or 'ax0 = fig.add_axes([0.10, 0.10, 0.60, 0.70])' in _s20, '')
check('宿主轴：字号照常原位改', 'ax0.set_title("t", fontsize=15)' in _s20, '')
check('宿主轴：警告说明 colorbar 决定位置',
      any('colorbar 的宿主' in w and '保持原样' in w
          for w in res20.get('warnings', [])), '')
check('宿主轴：验证通过',
      res20.get('verified') is True and res20.get('semantic') is True,
      'v=%s s=%s' % (res20.get('verified'), res20.get('semantic')))
shutil.rmtree(d20, ignore_errors=True)

# ---- 21. colorbar fraction 写回：拖 colorbar 轴宽度 → 反推 fraction →
#   原位改 fig.colorbar(..., fraction=N) 数字（fraction 是相对宿主轴 add_axes
#   原始宽的比例）。宿主轴 pos 仍 skipped（colorbar 会重定位它）。 ----
d21, script21, pp21 = setup()
with open(script21, 'w', encoding='utf-8') as f:
    f.write('import matplotlib\nmatplotlib.use("Agg")\n'
            'import matplotlib.pyplot as plt\n'
            'import numpy as np\n'
            'fig = plt.figure(figsize=(6, 4))\n'
            'ax_c = fig.add_axes([0.10, 0.10, 0.33, 0.40])\n'
            'cs = ax_c.contourf(np.random.rand(8, 8), levels=4)\n'
            'cb = fig.colorbar(cs, ax=ax_c, fraction=0.28, pad=0.03)\n'
            'ax_c.set_title("c", fontsize=12)\n'
            'fig.savefig("o.png")\n')
def _ax21(i, pos, is_cb=False):
    return {'index': i, 'pos': pos, 'aspect_locked': False,
            'title_fontsize': 14 if not is_cb else None,
            'label_fontsize': None, 'tick_fontsize': None,
            'is_colorbar': is_cb, 'clim': [0.0, 1.0] if is_cb else None,
            'cmap': 'viridis' if is_cb else None,
            'xscale': 'linear', 'yscale': 'linear', 'grid': None,
            'spines': {}, 'lines': [], 'legend': None}
params21 = {'version': 3, 'script': 'target.py', 'fig_index': 0, 'n_figs': 1,
            'figsize_px': None, 'figsize_in': None,
            'axes': [_ax21(0, [0.12, 0.15, 0.30, 0.35]),
                     _ax21(1, [0.45, 0.10, 0.0269, 0.40], True)]}
res21 = writeback.writeback(script21, params21, pp21, verify=True, semantic=True,
                            style='inplace')
_s21 = read(script21)
check('fraction：colorbar 调用数字被改（0.0269/0.33≈0.0815，_num %.6g 给 0.0815152）',
      'fraction=0.0815152' in _s21, '')
check('fraction：宿主轴 add_axes 不被改（原位无效）',
      'ax_c = fig.add_axes([0.1, 0.1, 0.33, 0.4])' in _s21
      or 'ax_c = fig.add_axes([0.10, 0.10, 0.33, 0.40])' in _s21, '')
check('fraction：宿主轴字号照常原位改', 'ax_c.set_title("c", fontsize=14)' in _s21, '')
check('fraction：验证通过',
      res21.get('verified') is True and res21.get('semantic') is True,
      'v=%s s=%s' % (res21.get('verified'), res21.get('semantic')))
shutil.rmtree(d21, ignore_errors=True)

# ---- 22. 验证超时三态：慢脚本（>timeout）→ 超时不被当"跑不通"回滚 ----
#   修前：verify_run 超时返回 False → 误判失败回滚（M7）；修后返回 None →
#   警告"未能自动验证"，写回保留。
d22, script22, pp22 = setup()
with open(script22, 'w', encoding='utf-8') as f:
    f.write('import time\nimport matplotlib\nmatplotlib.use("Agg")\n'
            'import matplotlib.pyplot as plt\n'
            'time.sleep(3)\n'                        # 慢脚本：超时模拟
            'fig = plt.figure(figsize=(6, 4))\n'
            'ax = fig.add_axes([0.10, 0.10, 0.70, 0.70])\n'
            'ax.set_title("t", fontsize=12)\n'
            'fig.savefig("o.png")\n')
params22 = {'version': 3, 'script': 'target.py', 'fig_index': 0, 'n_figs': 1,
            'figsize_px': None, 'figsize_in': None,
            'axes': [{'index': 0, 'pos': [0.10, 0.10, 0.70, 0.70],
                      'aspect_locked': False, 'title_fontsize': 14,
                      'label_fontsize': None, 'tick_fontsize': None,
                      'is_colorbar': False, 'clim': None, 'cmap': None,
                      'xscale': 'linear', 'yscale': 'linear', 'grid': None,
                      'spines': {}, 'lines': [], 'legend': None}]}
res22 = writeback.writeback(script22, params22, pp22, verify=True, semantic=True,
                            timeout=2, style='inplace')
check('超时：不失败回滚', res22.get('reason') in ('ok', 'best_effort'),
      str(res22.get('reason')))
check('超时：verified=None 且有超时警告',
      res22.get('verified') is None
      and any('超时' in w for w in res22.get('warnings', [])), '')
shutil.rmtree(d22, ignore_errors=True)

shutil.rmtree(d, ignore_errors=True)
shutil.rmtree(d3, ignore_errors=True)
shutil.rmtree(d4, ignore_errors=True)
shutil.rmtree(_TMPROOT, ignore_errors=True)

print('FAILED: %d' % fail if fail else 'ALL PASS')
sys.exit(1 if fail else 0)
