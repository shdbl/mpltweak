# -*- coding: utf-8 -*-
# writeback 引擎 golden 测试（自包含：临时目录现场造脚本 + 参数 JSON）：
#   1. 零 LLM 写回：fig 变量解析 / savefig 前插入 / figsize 原位替换
#   2. colorbar 轴用 _colorbar.mappable 结构导航（不依赖首次 draw）
#   3. 幂等：重复写回 = 整体替换旧块，不叠加
#   4. 安全网：写回后 Agg 重跑失败 → 自动回滚备份
import os
import re
import shutil
import sys
import json
import tempfile
import time

from mpltweak import apply, launch, revert, verify, writeback

fail = 0

# 沙箱：测试只能写 workspace 内；用测试文件旁目录做临时区
# 临时根目录带 pid：本套件与 CI/手工并行跑时，两边的 rmtree 不会互删对方正在用的
# case 目录（独立审阅实测踩到过 FileNotFoundError: case_19/target.py）。
_TMPROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        '.tmp_writeback_%d' % os.getpid())


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
check('reason=OK', res['reason'] == 'ok',
      '%s | err=%s' % (res['reason'], res.get('err')))
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
    'figsize_in': [6.0, 4.0], 'fig_index': 0,
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
# §6 用独立参数：多图脚本必须带 fig_index（writeback 现在会拒绝"多图却无图号"），
# 这里明确指向第 2 张，锚点应当落在它的 savefig（第 9 行 plt.savefig("b.png")）。
params_gcf6 = dict(params_gcf, fig_index=1)
res6 = writeback.writeback(script6, params_gcf6, pp6, verify=True)
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
    return {'version': 4, 'script': 'target.py', 'figsize_px': None,
            'figsize_in': None, 'fig_index': idx, 'n_figs': 2,
            'axes': [{'index': 0, 'pos': pos, 'aspect_locked': False,
                      'title_fontsize': None, 'label_fontsize': None,
                      'tick_fontsize': None, 'is_colorbar': False,
                      'clim': None, 'cmap': None, 'xlim': None, 'ylim': None,
                      'xscale': 'linear',
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

# ---- 23. S1 原子写：写文件失败或进程被打断时，原脚本绝不能变成 0 字节/半截 ----
#   修前：`open(script, 'w')` 会**先截断为目标 0 字节**再写入；进程若在该窗口内
#   消失（断电/OOM/taskkill），用户脚本就永久变空文件。修后：写同目录临时文件后
#   `os.replace` 原子替换；替换失败则原文件原样保留。
d23, script23, pp23 = setup()
ORIG23 = ('import matplotlib\n'
          'matplotlib.use("Agg")\n'
          'import matplotlib.pyplot as plt\n'
          'fig = plt.figure(figsize=(6, 4))\n'
          'ax = fig.add_axes([0.10, 0.10, 0.70, 0.70])\n'
          'ax.set_title("t", fontsize=12)\n'
          'fig.savefig("o.png")\n')
with open(script23, 'w', encoding='utf-8', newline='') as f:
    f.write(ORIG23)

# 23a 正常路径：内容写对，且不留临时文件
writeback._atomic_write(script23, 'AAA\nBBB\n')
check('原子写：内容正确', read(script23) == 'AAA\nBBB\n', repr(read(script23))[:50])
check('原子写：不留 .mpltweak-*.tmp 临时文件',
      not [n for n in os.listdir(os.path.dirname(script23))
           if n.startswith('.mpltweak-')], '')

# 23b 换行符保持 LF（newline='' 的作用；修前 open(...,'w') 会把 \n 翻译成 os.linesep）
writeback._atomic_write(script23, ORIG23)
check('原子写：换行符仍是 LF，未被悄悄改成 CRLF',
      '\r\n' not in read(script23), '')

# 23c 替换失败 → 原文件保持旧内容（关键：不能出现 0 字节或半截）
_real_replace = os.replace


def _boom_replace(a, b):
    raise OSError('simulated replace failure')


os.replace = _boom_replace
try:
    try:
        writeback._atomic_write(script23, 'SHOULD-NOT-LAND')
        _raised = False
    except OSError:
        _raised = True
finally:
    os.replace = _real_replace
check('原子写：替换失败会抛错（不静默吞掉）', _raised, '')
check('原子写：替换失败后原文件内容完好（不是 0 字节）',
      read(script23) == ORIG23, 'len=%d' % len(read(script23)))

# 23d 端到端：写回流程中替换失败 → 脚本仍是原文，且结论如实报告
params23 = {'version': 3, 'script': 'target.py', 'fig_index': 0, 'n_figs': 1,
            'figsize_px': None, 'figsize_in': None,
            'axes': [{'index': 0, 'pos': [0.10, 0.10, 0.70, 0.70],
                      'aspect_locked': False, 'title_fontsize': 14,
                      'label_fontsize': None, 'tick_fontsize': None,
                      'is_colorbar': False, 'clim': None, 'cmap': None,
                      'xscale': 'linear', 'yscale': 'linear', 'grid': None,
                      'spines': {}, 'lines': [], 'legend': None}]}
os.replace = _boom_replace
try:
    res23 = writeback.writeback(script23, params23, pp23, verify=False,
                                style='inplace')
finally:
    os.replace = _real_replace
check('原子写端到端：替换失败 → 脚本未被破坏',
      read(script23) == ORIG23, 'len=%d' % len(read(script23)))
check('原子写端到端：如实报错而不是假装成功',
      res23.get('reason') == writeback.FAIL, str(res23.get('reason')))
shutil.rmtree(d23, ignore_errors=True)

# ---- 24. S2 回滚诚实：回滚失败时结论必须说"回滚失败"，不能谎称"已回滚" ----
#   修前：`except OSError: pass` 吞掉回滚异常，而提示语照旧写"（已回滚）"——
#   用户以为安全了，磁盘上却仍是改坏的脚本。
d24, script24, _ = setup()
writeback._atomic_write(script24, 'X = 1\n')
_bak24 = os.path.join(d24, 'good.bak')
writeback._atomic_write(_bak24, 'ORIG\n')
check('回滚说明：成功时返回「已回滚」且内容确实被拷回',
      writeback._rollback_note(_bak24, script24) == '已回滚'
      and read(script24) == 'ORIG\n', repr(read(script24)))
writeback._atomic_write(script24, 'BROKEN\n')
_missing24 = os.path.join(d24, 'nonexistent.bak')
_note24 = writeback._rollback_note(_missing24, script24)
check('回滚说明：失败时如实报告（含「回滚也失败」与备份路径）',
      '回滚也失败' in _note24 and _missing24 in _note24, _note24[:90])
check('回滚说明：失败时绝不谎称「已回滚」',
      '已回滚' not in _note24, _note24[:90])
check('回滚说明：失败后坏内容留在盘上（这正是必须如实告知的原因）',
      read(script24) == 'BROKEN\n', repr(read(script24)))
shutil.rmtree(d24, ignore_errors=True)

# ---- 25. auto 模式（默认）：每张图各自决定写回方式 ----
#   默认 --style auto = 先试"原位改代码里的数字"；若这张图什么都没落地
#   （典型：位置来自 plt.subplots / plt.subplot(1,2,n) / GridSpec，源码里根本
#   没有可改的数字），再改用插入调整块。这样同一脚本里能干净改的图和只能插块的
#   图各得其所，而不是因为少数几张图就把整份脚本都变成插块。
#   同时锁死：显式 --style 时绝不能被 auto 逻辑覆盖（曾经踩过这个坑）。
d25, script25, _pp25 = setup()


def _write25(src):
    with open(script25, 'w', encoding='utf-8', newline='') as f:
        f.write(src)


def _params25(pos):
    # CLI 在 <脚本目录>/.tweak_params/<stem>.json 找参数；setup() 返回的 pp 是
    # 侧边元数据用的 <d>/target.json，两者不是一回事，这里必须建在前者。
    _pd = os.path.join(d25, '.tweak_params')
    os.makedirs(_pd, exist_ok=True)
    with open(os.path.join(_pd, 'target.json'), 'w', encoding='utf-8') as f:
        json.dump(_mkpar(0, pos), f, ensure_ascii=False)


_GRID_SRC = ('import matplotlib\n'
             'matplotlib.use("Agg")\n'
             'import matplotlib.pyplot as plt\n'
             'fig, axs = plt.subplots(2, 2, figsize=(8, 6))\n'
             'fig.savefig("o.png")\n')
_ADD_SRC = ('import matplotlib\n'
            'matplotlib.use("Agg")\n'
            'import matplotlib.pyplot as plt\n'
            'fig = plt.figure(figsize=(8, 6))\n'
            'ax = fig.add_axes([0.10, 0.10, 0.70, 0.70])\n'
            'fig.savefig("o.png")\n')

# 25a 网格轴（subplots）→ auto 应自动退到插块
_write25(_GRID_SRC)
_params25([0.08, 0.55, 0.38, 0.38])
_rc25 = apply.main([script25, '--write'])
_s25 = read(script25)
check('auto：网格轴（subplots）→ 自动改用插入调整块',
      _rc25 == 0 and writeback.BLOCK_START in _s25,
      'rc=%s blocks=%d' % (_rc25, _s25.count(writeback.BLOCK_START)))
check('auto：退到块模式后确实改到了位置',
      'set_position' in _s25, '')

# 25b add_axes 字面量 → auto 应原位改、不插块
_write25(_ADD_SRC)
_params25([0.20, 0.20, 0.50, 0.50])
_rc25b = apply.main([script25, '--write'])
_s25b = read(script25)
check('auto：add_axes 字面量 → 原位改，不插块',
      writeback.BLOCK_START not in _s25b and '[0.2, 0.2, 0.5, 0.5]' in _s25b,
      repr([ln for ln in _s25b.splitlines() if 'add_axes' in ln]))

# 25c 显式 --style inplace → 不得被 auto 逻辑覆盖（保持原样、不插块）
_write25(_GRID_SRC)
_params25([0.08, 0.55, 0.38, 0.38])
_rc25c = apply.main([script25, '--write', '--style', 'inplace'])
_s25c = read(script25)
check('--style inplace：显式指定时不得退到块模式',
      writeback.BLOCK_START not in _s25c and 'set_position' not in _s25c,
      'rc=%s' % _rc25c)

# 25d 显式 --style block → 即使能原位改也必须插块
_write25(_ADD_SRC)
_params25([0.20, 0.20, 0.50, 0.50])
_rc25d = apply.main([script25, '--write', '--style', 'block'])
_s25d = read(script25)
check('--style block：显式指定时即使能原位改也插块',
      writeback.BLOCK_START in _s25d, 'rc=%s' % _rc25d)
shutil.rmtree(d25, ignore_errors=True)

# ---- 26. 写回互斥锁 + 备份不共享（P0：并发 apply 会互相覆盖）----
#   修前（t5-S4 / t6-B2 实测 7/7）：两个 apply --write 同时跑，两边都打印
#   "✓ 已原位写回"，其中一份改动凭空消失，而且共用同一个 .tweak.bak ——
#   出事后唯一的恢复点已经不可信。
d26, script26, _p26 = setup()
_lkdir = os.path.join(d26, '.tweak_params')
os.makedirs(_lkdir, exist_ok=True)
_lk = os.path.join(_lkdir, 'target.apply.lock')

# 26a 已持锁时，第二个写回必须被挡住（等待后超时报错），而不是并行跑
with writeback.script_lock(script26, timeout=5):
    _t0 = time.time()
    _blocked = False
    try:
        with writeback.script_lock(script26, timeout=0.6):
            pass
    except RuntimeError:
        _blocked = True
    _waited = time.time() - _t0
check('互斥锁：第二个写回被挡住（超时报错）', _blocked,
      'blocked=%s waited=%.2f' % (_blocked, _waited))
check('互斥锁：是等待后超时，不是立刻失败', _waited >= 0.5, '%.2f' % _waited)

# 26b 释放后锁文件要清掉（否则下次会被自己的陈旧锁挡住）
check('互斥锁：释放后锁文件已删除', not os.path.exists(_lk), _lk)

# 26c 陈旧锁（持有进程已死）应被清理，不能误挡
with open(_lk, 'w', encoding='utf-8') as f:
    f.write('999999')                     # 几乎不可能存在的 pid
_ok26c = True
try:
    with writeback.script_lock(script26, timeout=2):
        pass
except RuntimeError:
    _ok26c = False
check('互斥锁：陈旧锁（持有者已死）被清理，不误挡', _ok26c, '')

# 26d 备份：固定名 + 带 pid 的专属副本（后者谁也覆盖不了）
#   直接测 _make_backup —— 走完整 writeback 时若参数与原码一致会提前返回
#   （no_change），压根不写备份，反而测不到这条。
_pp26 = os.path.join(_lkdir, 'target.json')
_fixed26 = writeback._make_backup(script26, _pp26)
check('备份：固定名 target.tweak.bak 仍生成（兼容 README/测试/用户习惯）',
      _fixed26 == os.path.join(_lkdir, 'target.tweak.bak')
      and os.path.exists(_fixed26), _fixed26)
check('备份：同时生成带 pid 的专属副本（并发不互相覆盖）',
      os.path.exists(os.path.join(_lkdir,
                                  'target.tweak.%d.bak' % os.getpid())), '')
shutil.rmtree(d26, ignore_errors=True)

# ---- 27. M16：被重叠检测跳过的字段不得计进 changes（不许"声称改了但没改"）----
#   同名轴变量被 add_axes 赋值两次时，第二处替换会被重叠检测拒绝（L 的修复）。
#   修前 covered/changes 照记 → 输出"原位修改 2 处"而磁盘上只落了 1 处（t6-M16）。
d27, script27, _p27x = setup()
with open(script27, 'w', encoding='utf-8', newline='') as f:
    f.write('import matplotlib\n'
            'matplotlib.use("Agg")\n'
            'import matplotlib.pyplot as plt\n'
            'fig = plt.figure(figsize=(6, 4))\n'
            'ax = fig.add_axes([0.10, 0.10, 0.40, 0.40])\n'
            'ax = fig.add_axes([0.10, 0.10, 0.40, 0.40])\n'
            'fig.savefig("o.png")\n')
_pd27 = os.path.join(d27, '.tweak_params')
os.makedirs(_pd27, exist_ok=True)
_ax27 = {'index': 0, 'pos': [0.2, 0.2, 0.5, 0.5], 'aspect_locked': False,
         'title_fontsize': None, 'label_fontsize': None, 'tick_fontsize': None,
         'is_colorbar': False, 'clim': None, 'cmap': None,
         'xscale': 'linear', 'yscale': 'linear', 'grid': None,
         'spines': {}, 'lines': []}
params27 = {'version': 3, 'script': 'target.py', 'fig_index': 0, 'n_figs': 1,
            'figsize_px': None, 'figsize_in': None,
            # 两个轴项都指向同一个变量 ax → 第二处替换必然与第一处重叠
            'axes': [dict(_ax27), dict(_ax27, index=1)]}
_pp27 = os.path.join(_pd27, 'target.json')
with open(_pp27, 'w', encoding='utf-8') as f:
    json.dump(params27, f, ensure_ascii=False)
res27 = writeback.writeback(script27, params27, _pp27, verify=False,
                            style='inplace')
_s27 = read(script27)
_ch27 = res27.get('changes') or []
check('M16：有替换被重叠跳过时给出警告',
      any('重叠' in w for w in res27.get('warnings', [])),
      str(res27.get('warnings')))
check('M16：changes 只算真正落盘的（不多报）',
      _ch27 == ['ax0.pos'], 'changes=%s' % _ch27)
check('M16：源码确实只改了第一处、第二处原样',
      _s27.count('[0.2, 0.2, 0.5, 0.5]') == 1
      and _s27.count('[0.10, 0.10, 0.40, 0.40]') == 1,
      str([_l.strip() for _l in _s27.splitlines() if 'add_axes' in _l]))
shutil.rmtree(d27, ignore_errors=True)

# ---- 28. 写回不得给脚本加 BOM（C2 编码兜底的回归）----
#   坑：params.read_text 探测到 'utf-8-sig' 后把这个编码直接交给写回，而 Python
#   用 'utf-8-sig' **写入时会自动加 BOM** → 用户的脚本被改脏，compile() 立刻报
#   invalid non-printable character U+FEFF。七套件全都查不出来（它们不查 BOM），
#   是被 t1 的验收脚本"落盘语法非法=True"照出来的。
d28, _s28, _p28 = setup()
with open(_s28, 'w', encoding='utf-8', newline='') as f:
    f.write('import matplotlib\n'
            'matplotlib.use("Agg")\n'
            'import matplotlib.pyplot as plt\n'
            'fig = plt.figure(figsize=(6, 4))\n'
            'ax = fig.add_axes([0.10, 0.10, 0.40, 0.40])\n'
            'fig.savefig("o.png")\n')
_pd28 = os.path.join(d28, '.tweak_params')
os.makedirs(_pd28, exist_ok=True)
_pp28 = os.path.join(_pd28, 'target.json')
with open(_pp28, 'w', encoding='utf-8') as f:
    json.dump(_mkpar(0, [0.2, 0.2, 0.5, 0.5]), f, ensure_ascii=False)
writeback.writeback(_s28, _mkpar(0, [0.2, 0.2, 0.5, 0.5]), _pp28,
                    verify=False, style='inplace')
_raw28 = open(_s28, 'rb').read()
check('写回不给脚本加 BOM（否则 compile 报 U+FEFF）',
      not _raw28.startswith(b'\xef\xbb\xbf'), repr(_raw28[:8]))
check('写回后确实改了内容（不是空跑）',
      b'[0.2, 0.2, 0.5, 0.5]' in _raw28, repr(_raw28[-120:]))
shutil.rmtree(d28, ignore_errors=True)

# 带 BOM 的脚本也要能写回（写回后不再有 BOM，但内容正确）
d29, _s29, _p29 = setup()
with open(_s29, 'wb') as f:
    f.write(b'\xef\xbb\xbf' + b'import matplotlib\n'
            b'matplotlib.use("Agg")\n'
            b'import matplotlib.pyplot as plt\n'
            b'fig = plt.figure(figsize=(6, 4))\n'
            b'ax = fig.add_axes([0.10, 0.10, 0.40, 0.40])\n'
            b'fig.savefig("o.png")\n')
_pd29 = os.path.join(d29, '.tweak_params')
os.makedirs(_pd29, exist_ok=True)
_pp29 = os.path.join(_pd29, 'target.json')
with open(_pp29, 'w', encoding='utf-8') as f:
    json.dump(_mkpar(0, [0.2, 0.2, 0.5, 0.5]), f, ensure_ascii=False)
_res29 = writeback.writeback(_s29, _mkpar(0, [0.2, 0.2, 0.5, 0.5]), _pp29,
                             verify=False, style='inplace')
_raw29 = open(_s29, 'rb').read()
check('带 BOM 的脚本也能写回（原先直接抛 UnicodeDecodeError）',
      _res29.get('reason') != 'fail' and b'[0.2, 0.2, 0.5, 0.5]' in _raw29,
      '%s / %r' % (_res29.get('reason'), _raw29[:8]))
check('写回后 BOM 不再叠加', not _raw29.startswith(b'\xef\xbb\xbf'),
      repr(_raw29[:8]))
shutil.rmtree(d29, ignore_errors=True)

# ---- 30. 元组写法 add_axes((...)) 也要进原位写回 ----
# 外部审阅第 2 条：原位路径原先只认 ast.List，元组写法会被**静默**丢进 skipped
# （用户看到的是"拖了没反应"，而代码里明明有可改的数字）。
# 反向验证：把 writeback.py 的 `isinstance(..., (ast.List, ast.Tuple))` 改回只认
# ast.List，本条立刻失败 → 证明它真的在测东西。
d30, _s30, _pp30 = setup()
with open(_s30, 'w', encoding='utf-8') as f:
    f.write('import matplotlib\n'
            'matplotlib.use("Agg")\n'
            'import matplotlib.pyplot as plt\n'
            'fig = plt.figure(figsize=(6, 4))\n'
            'ax = fig.add_axes((0.10, 0.10, 0.40, 0.40))\n'
            'fig.savefig("o.png")\n')
_par30 = _mkpar(0, [0.25, 0.30, 0.35, 0.45])
with open(_pp30, 'w', encoding='utf-8') as f:
    json.dump(_par30, f, ensure_ascii=False)
_res30 = writeback.writeback(_s30, _par30, _pp30, verify=False, style='inplace')
_src30 = read(_s30)
_m30 = re.search(r'add_axes\(\(([^)]*)\)\)', _src30)
_vals30 = [float(x) for x in _m30.group(1).split(',')] if _m30 else []
check('元组 add_axes((...)) 原位写回（原先静默进 skipped）',
      len(_vals30) == 4 and all(abs(a - b) < 1e-6
                                for a, b in zip(_vals30, [0.25, 0.30, 0.35, 0.45])),
      'reason=%s pos=%r' % (_res30.get('reason'), _vals30))
check('元组写回不改容器类型（仍然是一对圆括号，不是方括号）',
      _m30 is not None and 'add_axes([' not in _src30,
      str([ln for ln in _src30.splitlines() if 'add_axes' in ln]))
check('元组写回被记进 covered（不是"改了 0 处"）',
      bool(_res30.get('changes')), str(_res30.get('changes')))

shutil.rmtree(d30, ignore_errors=True)

# ---- 31. 轴范围 xlim/ylim 的位置参数写法（含带符号字面量）----
# 外部审阅第 2 条点名的 `set_xlim(-0.01, 1.01)`：负号在 AST 里是 UnaryOp 而不是
# Constant，只认 Constant 会把它静默丢进 skipped（用户看到"改了没反应"）。
# 反向验证：把 _is_num_literal 换回 isinstance(..., ast.Constant)，本条立刻失败。
d31, _s31, _p31 = setup()
with open(_s31, 'w', encoding='utf-8') as f:
    f.write('import matplotlib\n'
            'matplotlib.use("Agg")\n'
            'import matplotlib.pyplot as plt\n'
            'fig = plt.figure(figsize=(6, 4))\n'
            'ax = fig.add_axes([-0.02, 0.10, 0.80, 0.80])\n'
            'ax.plot([1, 2, 3])\n'
            'ax.set_xlim(-0.01, 1.01)\n'
            'ax.set_ylim(0.0, 10.0)\n'
            'fig.savefig("o.png")\n')
_par31 = _mkpar(0, [0.0, 0.10, 0.80, 0.80])
_par31['axes'][0]['xlim'] = [0.0, 2.0]
_par31['axes'][0]['ylim'] = [0.5, 8.5]
with open(_p31, 'w', encoding='utf-8') as f:
    json.dump(_par31, f, ensure_ascii=False)
_res31 = writeback.writeback(_s31, _par31, _p31, verify=False, style='inplace')
_src31 = read(_s31)
check('负数 pos 原位改写（-0.02 → 0，原先整项进 skipped）',
      'add_axes([0, 0.10, 0.80, 0.80])' in _src31,
      str([ln for ln in _src31.splitlines() if 'add_axes' in ln]))
check('只改真正变了的数字（未变的 0.10 / 0.80 原样保留）',
      '0.10' in _src31 and '0.80' in _src31,
      str([ln for ln in _src31.splitlines() if 'add_axes' in ln]))
check('set_xlim(a, b) 原位改写（负数范围要认）',
      'set_xlim(0, 2)' in _src31,
      str([ln for ln in _src31.splitlines() if 'set_xlim' in ln]))
check('set_ylim(a, b) 原位改写',
      'set_ylim(0.5, 8.5)' in _src31,
      str([ln for ln in _src31.splitlines() if 'set_ylim' in ln]))
check('三处都被记进 covered（不是"改了 0 处"）',
      {'ax0.pos', 'ax0.xlim', 'ax0.ylim'} <= set(_res31.get('changes') or []),
      str(_res31.get('changes')))
shutil.rmtree(d31, ignore_errors=True)

# ---- 32. 轴范围的容器 / 关键字写法 ----
d32, _s32, _p32 = setup()
with open(_s32, 'w', encoding='utf-8') as f:
    f.write('import matplotlib\n'
            'matplotlib.use("Agg")\n'
            'import matplotlib.pyplot as plt\n'
            'fig = plt.figure(figsize=(6, 4))\n'
            'ax = fig.add_axes([0.10, 0.10, 0.80, 0.80])\n'
            'ax.plot([1, 2, 3])\n'
            'ax.set_xlim((-0.01, 1.01))\n'          # 元组容器
            'ax.set_ylim(ymin=0.0, ymax=10.0)\n'    # 关键字
            'fig.savefig("o.png")\n')
_par32 = _mkpar(0, [0.10, 0.10, 0.80, 0.80])
_par32['axes'][0]['xlim'] = [0.0, 2.0]
_par32['axes'][0]['ylim'] = [0.5, 8.5]
with open(_p32, 'w', encoding='utf-8') as f:
    json.dump(_par32, f, ensure_ascii=False)
writeback.writeback(_s32, _par32, _p32, verify=False, style='inplace')
_src32 = read(_s32)
check('set_xlim((a, b)) 容器写法原位改写', 'set_xlim((0, 2))' in _src32,
      str([ln for ln in _src32.splitlines() if 'set_xlim' in ln]))
check('set_ylim(ymin=, ymax=) 关键字写法原位改写',
      'ymin=0.5' in _src32 and 'ymax=8.5' in _src32,
      str([ln for ln in _src32.splitlines() if 'set_ylim' in ln]))
shutil.rmtree(d32, ignore_errors=True)

# ---- 33. 源码里没有对应写法时：范围要进"未原位应用"，且块模式能承载 ----
d33, _s33, _p33 = setup()
_par33 = _mkpar(0, [0.05, 0.10, 0.42, 0.78])
_par33['axes'][0]['xlim'] = [0.0, 4.0]
with open(_p33, 'w', encoding='utf-8') as f:
    json.dump(_par33, f, ensure_ascii=False)
_res33 = writeback.writeback(_s33, _par33, _p33, verify=False, style='inplace')
check('没有 set_xlim 可改 → 明确进"未原位应用"（不静默）',
      any('xlim' in w for w in (_res33.get('warnings') or [])),
      str(_res33.get('warnings')))
_blk33, _warn33 = writeback.render_block(_par33, 'fig', [], [])
check('块模式会带上 set_xlim（--style block 不丢范围）',
      'set_xlim(0, 4)' in _blk33,
      str([ln for ln in _blk33.splitlines() if 'set_xlim' in ln]))
shutil.rmtree(d33, ignore_errors=True)

# ---- 34. revert：从备份回退，而且自身可逆 ----
# "完整本地回路"缺的那一环：写回错了要能一键回去（用户提过"没有 undo"）。
d34, _s34, _ = setup()
# 用单轴脚本（共用的 SCRIPT 有两轴 + colorbar，pos 只给一个轴会走不到干净的原位路径）
_SRC34 = ('import matplotlib\n'
          'matplotlib.use("Agg")\n'
          'import matplotlib.pyplot as plt\n'
          'fig = plt.figure(figsize=(6, 4))\n'
          'ax = fig.add_axes([0.10, 0.10, 0.40, 0.40])\n'
          'fig.savefig("o.png")\n')
with open(_s34, 'w', encoding='utf-8') as f:
    f.write(_SRC34)
_pd34 = os.path.join(d34, '.tweak_params')
os.makedirs(_pd34, exist_ok=True)
_pp34 = os.path.join(_pd34, 'target.json')       # 规范位置（revert 按它算备份路径）
_par34 = _mkpar(0, [0.2, 0.2, 0.5, 0.5])
with open(_pp34, 'w', encoding='utf-8') as f:
    json.dump(_par34, f, ensure_ascii=False)
writeback.writeback(_s34, _par34, _pp34, verify=False, style='inplace')
check('写回后源码确实变了（前提）',
      'add_axes([0.2, 0.2, 0.5, 0.5])' in read(_s34),
      str([ln for ln in read(_s34).splitlines() if 'add_axes' in ln]))
_bak34 = writeback._backup_path(_s34, _pp34)
check('写回留了备份（revert 的依据）', os.path.exists(_bak34), _bak34)

_before34 = read(_s34)
check('revert 默认只预览：rc=0 且代码一个字节没动',
      revert.main([_s34]) == 0 and read(_s34) == _before34)
check('预览阶段不会生成 revert.bak',
      not os.path.exists(os.path.join(_pd34, 'target.revert.bak')))

check('revert --write rc=0', revert.main([_s34, '--write']) == 0)
check('回到写回前的原文', read(_s34) == _SRC34, read(_s34)[:140])
_rev34 = os.path.join(_pd34, 'target.revert.bak')
check('revert 自身可逆：留了 revert.bak，内容是回退前那份',
      os.path.exists(_rev34) and '[0.2, 0.2, 0.5, 0.5]' in read(_rev34),
      _rev34)
_d34b, _s34b, _ = setup()
check('没有备份时 rc=2（并给出去哪找备份的说明）', revert.main([_s34b]) == 2)
check('脚本不存在时 rc=2',
      revert.main([os.path.join(d34, 'nope.py')]) == 2)
shutil.rmtree(d34, ignore_errors=True)
shutil.rmtree(_d34b, ignore_errors=True)

# ---- 35. 快速验证档：只查语法；写出坏语法要当场回滚 ----
_SRC35 = ('import matplotlib\n'
          'matplotlib.use("Agg")\n'
          'import matplotlib.pyplot as plt\n'
          'fig = plt.figure(figsize=(6, 4))\n'
          'ax = fig.add_axes([0.10, 0.10, 0.40, 0.40])\n'
          'fig.savefig("o.png")\n')
d35, _s35, _pp35 = setup()
_par35 = _mkpar(0, [0.2, 0.2, 0.5, 0.5])
with open(_s35, 'w', encoding='utf-8') as f:
    f.write(_SRC35)
with open(_pp35, 'w', encoding='utf-8') as f:
    json.dump(_par35, f, ensure_ascii=False)
_res35 = writeback.writeback(_s35, _par35, _pp35, verify=False,
                             fast_check=True, style='inplace')
check('快速档标记 verify_mode=fast、且不声称验证通过',
      _res35.get('verify_mode') == 'fast' and _res35.get('verified') is None,
      str((_res35.get('verify_mode'), _res35.get('verified'))))
check('快速档仍然把改动写进去了',
      '[0.2, 0.2, 0.5, 0.5]' in read(_s35),
      str([ln for ln in read(_s35).splitlines() if 'add_axes' in ln]))
shutil.rmtree(d35, ignore_errors=True)

# 安全网：注入一个"写出的源码语法坏了"的场景（替换后续被污染），
# 快速档必须当场识破并回滚 —— 这是它唯一的强保证，必须真的成立。
d35b, _s35b, _pp35b = setup()
with open(_s35b, 'w', encoding='utf-8') as f:
    f.write(_SRC35)
with open(_pp35b, 'w', encoding='utf-8') as f:
    json.dump(_par35, f, ensure_ascii=False)
_orig_apply = writeback.apply_inplace


def _bad_apply(*a, **k):
    _new, _cov, _skip = _orig_apply(*a, **k)
    return _new + '\ndef broken(:\n', _cov, _skip


writeback.apply_inplace = _bad_apply
try:
    _res35b = writeback.writeback(_s35b, _par35, _pp35b, verify=False,
                                  fast_check=True, style='inplace')
finally:
    writeback.apply_inplace = _orig_apply
check('语法坏了 → reason=fail，且错误里点名"快速验证"',
      _res35b.get('reason') == 'fail' and '快速验证失败' in (_res35b.get('err') or ''),
      _res35b.get('err'))
check('语法坏了 → 已回滚，磁盘上没留下坏脚本',
      read(_s35b) == _SRC35 and 'broken' not in read(_s35b),
      read(_s35b)[-80:])
shutil.rmtree(d35b, ignore_errors=True)

# ---- 36. 网格身份（cell）：轴序变了要**指名道姓**地失败，而不是默默改错面板 ----
# 做法与取舍：块生成仍按 fig.axes 下标寻址（重写寻址会动整块格式、且在脚本变结构时
# 有把用户脚本跑崩的风险），但把"调图时这个下标对应哪个网格格位"记进参数，
# 写回后由语义验证回比 → 不匹配就回滚并说清原因。这条用例测的正是这个回比。
_par36 = _mkpar(0, [0.1, 0.1, 0.4, 0.4])
_par36['axes'][0]['cell'] = [1, 3, 0, 0]
_par36['axes'][0]['title_fontsize'] = 12.0
_act36 = [{'index': 0, 'cell': [1, 4, 0, 0], 'title_fontsize': 12.0}]
_bad36 = verify.compare(_par36['axes'], _act36)
check('cell 身份不一致 → 比对失败且信息里点名"轴序变了"',
      any('网格身份' in b and '轴序变了' in b for b in _bad36),
      str(_bad36))
_act36b = [{'index': 0, 'cell': [1, 3, 0, 0], 'title_fontsize': 12.0}]
check('cell 一致 → 不报（否则会误伤正常写回）',
      not [b for b in verify.compare(_par36['axes'], _act36b) if '网格身份' in b],
      str(verify.compare(_par36['axes'], _act36b)))
_blk36, _w36 = writeback.render_block(_par36, 'fig', [], [])
check('块注释里带出轴身份（出问题人一眼能对上）',
      '轴身份' in _blk36 and '[1, 3, 0, 0]' in _blk36,
      str([ln for ln in _blk36.splitlines() if '轴身份' in ln]))

# ---- 37. 值没变的字段不算"改到"（否则 auto 模式会被骗住）----
# 这条是实打实踩出来的：xlim/ylim 进参数后，**值没变**的字段也被登记成一处改动，
# 于是 changes 非空 → apply 的 auto 模式以为"有改动落地"、不再退到块模式 →
# 网格轴图里用户拖的位置就落不了地（只剩一句"未原位应用"警告）。
# 顺带它也让"重复 apply 同一份参数"从谎报"原位修改 N 处"变成正确的 no_change。
d37, _s37, _pp37 = setup()
_SRC37 = ('import matplotlib\n'
          'matplotlib.use("Agg")\n'
          'import matplotlib.pyplot as plt\n'
          'fig = plt.figure(figsize=(6, 4))\n'
          'ax = fig.add_axes([0.10, 0.10, 0.40, 0.40])\n'
          'ax.set_xlim(-0.01, 1.01)\n'
          'fig.savefig("o.png")\n')
with open(_s37, 'w', encoding='utf-8') as f:
    f.write(_SRC37)
_par37 = _mkpar(0, [0.10, 0.10, 0.40, 0.40])          # 与源码**数值完全相同**
_par37['axes'][0]['xlim'] = [-0.01, 1.01]
with open(_pp37, 'w', encoding='utf-8') as f:
    json.dump(_par37, f, ensure_ascii=False)
_res37 = writeback.writeback(_s37, _par37, _pp37, verify=False, style='inplace')
check('值与源码一致 → reason=no_change（不再谎报"改了 N 处"）',
      _res37.get('reason') == 'no_change',
      '%s / changes=%s' % (_res37.get('reason'), _res37.get('changes')))
check('no_change 时源码一个字节都没变', read(_s37) == _SRC37, read(_s37)[-60:])
shutil.rmtree(d37, ignore_errors=True)
# ---- 39. 多图同名轴变量：字段查找必须夹在本图区间内 ----
# 独立审阅 C2 的构造：两张图都用 `ax`，只有第二张写了 set_xlim。改第一张图的
# xlim 时，_find_call 若只有下界就会命中第二张图那一行 —— 静默改错图。
d39, _s39, _pp39 = setup()
_SRC39 = ('import matplotlib\n'
          'matplotlib.use("Agg")\n'
          'import matplotlib.pyplot as plt\n'
          'fig = plt.figure(figsize=(6, 4))\n'
          'ax = fig.add_axes([0.10, 0.10, 0.40, 0.40])\n'
          'ax.plot([1, 2, 3])\n'
          'fig.savefig("a.png")\n'
          'fig2 = plt.figure(figsize=(6, 4))\n'
          'ax = fig2.add_axes([0.20, 0.20, 0.50, 0.50])\n'
          'ax.plot([3, 2, 1])\n'
          'ax.set_xlim(0.0, 10.0)\n'          # 只在第二张图里
          'fig2.savefig("b.png")\n')
with open(_s39, 'w', encoding='utf-8') as f:
    f.write(_SRC39)
_par39 = _mkpar(0, [0.10, 0.10, 0.40, 0.40])     # 目标 = 第 0 张图
_par39['axes'][0]['xlim'] = [0.0, 5.0]
with open(_pp39, 'w', encoding='utf-8') as f:
    json.dump(_par39, f, ensure_ascii=False)
_res39 = writeback.writeback(_s39, _par39, _pp39, verify=False, style='inplace')
_src39 = read(_s39)
check('第一张图的图内没有 set_xlim → 不能去改第二张图那一行',
      'ax.set_xlim(0.0, 10.0)' in _src39,
      str([ln for ln in _src39.splitlines() if 'set_xlim' in ln]))
check('该字段如实进"未原位应用"（不静默）',
      any('xlim' in w for w in (_res39.get('warnings') or [])),
      str(_res39.get('warnings')))
shutil.rmtree(d39, ignore_errors=True)

# ---- 38. 网格身份：期望是网格轴、实测不是 → 必须判失败（不能默默放行）----
_c38 = [{'index': 0, 'cell': [1, 2, 0, 0], 'title_fontsize': 12.0}]
check('实测该轴不再是网格轴（cell=None）→ 判失败',
      any('网格身份对不上' in b for b in verify.compare(
          _c38, [{'index': 0, 'cell': None, 'title_fontsize': 12.0}])),
      str(verify.compare(_c38, [{'index': 0, 'cell': None,
                                 'title_fontsize': 12.0}])))
check('实测侧没这个字段（旧参数/采集失败）→ 不判，保持向后兼容',
      not [b for b in verify.compare(
          _c38, [{'index': 0, 'title_fontsize': 12.0}]) if '网格身份' in b],
      str(verify.compare(_c38, [{'index': 0, 'title_fontsize': 12.0}])))
check('cell 不同 → 判失败（此前已有）',
      any('网格身份' in b for b in verify.compare(
          _c38, [{'index': 0, 'cell': [1, 2, 0, 1], 'title_fontsize': 12.0}])))

shutil.rmtree(d, ignore_errors=True)
shutil.rmtree(d3, ignore_errors=True)
shutil.rmtree(d4, ignore_errors=True)
shutil.rmtree(_TMPROOT, ignore_errors=True)

print('FAILED: %d' % fail if fail else 'ALL PASS')
sys.exit(1 if fail else 0)
