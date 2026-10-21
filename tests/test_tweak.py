# -*- coding: utf-8 -*-
# tweak_toolbox v3 无头自测：
#   1. _snap_box 吸附数学（近则吸、远不吸、参考线坐标）
#   2. _resize_box 锁比等比 / 未锁原版语义
#   3. _zoom_box 绕中心等比
#   4. 图例 6 位循环、字号调节（title/label/legend）
#   5. export v3 协议（pos/legend/字号字段）
#   6. PPT 式边框/角点缩放（_resize_frac 锁比/未锁/MIN_SIZE、_snap_resize、_hit_handle）
# 全部用 Agg 后端，不开窗口。
import os
import sys
import json
import tempfile

import numpy as np

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

from mpltweak import params as _params
from mpltweak.toolbox import (Tweak, _resize_box, _snap_box,
                           _aspect_locked, LEGEND_LOCS, MIN_SIZE, MIN_SIZE_CB,
                           _resize_frac, _snap_resize, _hit_handle,
                           _is_colorbar_ax, _bbox, _title_fs, _resize_anchored,
                           _hit_cb_endpoint, _clim_of)

# 测试断言的是中文人读文案：把界面语言**钉死**，别让宿主 locale 决定结论。
os.environ['MPLTWEAK_LANG'] = 'zh'
fail = 0

def check(name, cond, detail=''):
    global fail
    print(('PASS' if cond else 'FAIL') + ' | ' + name + ((' | ' + detail) if detail else ''))
    if not cond:
        fail += 1

def approx(a, b, tol=1e-9):
    return abs(a - b) < tol

# ---- 1. 吸附 ----
box = [0.105, 0.1, 0.2, 0.1]
others = [[0.1, 0.1, 0.2, 0.1], [0.1, 0.3, 0.2, 0.1]]
nb, gx, gy = _snap_box(box, others, tol=0.006)
check('snap 近边吸附', approx(nb[0], 0.1), str(nb))
check('snap 吸附后宽不变', approx(nb[2], 0.2) and approx(nb[3], 0.1), str(nb))
# 新版语义：整体平移吸附，每条轴最多一条参考线（左/中/右 三种候选里取最近）
check('snap 参考线为一条候选线',
      len(gx) == 1 and min(abs(gx[0] - v) for v in (0.1, 0.2, 0.3)) < 1e-9,
      str(gx))

box = [0.15, 0.1, 0.2, 0.1]
nb, gx, gy = _snap_box(box, others, tol=0.006)
check('snap 远边不吸', approx(nb[0], 0.15) and gx == [], str(nb))

# 上边缘对齐（y1 吸附）
box = [0.1, 0.205, 0.2, 0.1]          # y1=0.305, 其它有 y1=0.3
others2 = [[0.1, 0.2, 0.2, 0.1]]
nb, gx, gy = _snap_box(box, others2, tol=0.006)
check('snap 上边对齐', approx(nb[1] + nb[3], 0.3), str(nb))

# ---- 2. 锁比缩放 ----
box = [0.10, 0.10, 0.40, 0.20]
ratio = box[2] / box[3]
for key in ['a', 'd', 'A', 'D', 'w', 's', 'W', 'S']:
    nb = _resize_box(box, key, locked=True)
    check('locked resize %s 等比' % key, abs(nb[2] / nb[3] - ratio) < 1e-9)
nb = _resize_box(box, 'right', locked=True)
check('locked 平移', nb[2] == box[2] and nb[3] == box[3] and nb[0] == box[0] + 0.01)

# ---- 3. 滚轮缩放已按用户要求移除，不再有对应测试 ----

# ---- 4. 图例循环 + 字号 ----
fig = plt.figure(figsize=(8, 6))
ax = fig.add_axes([0.1, 0.1, 0.6, 0.6])
ax.plot([0, 1], [0, 1], label='a')
ax.plot([0, 1], [1, 0], label='b')
ax.legend(loc='upper right', fontsize=8)
ax.set_title('T', fontsize=10)
ax.set_xlabel('x', fontsize=9)
tw = Tweak(fig, export_path=os.path.join(
    tempfile.gettempdir(), 'test_v3.json'), heavy=False)
tw._cycle_legend(ax)
locs_seen = [tw.leg_state[id(ax)]['loc']]
tw._cycle_legend(ax)
locs_seen.append(tw.leg_state[id(ax)]['loc'])
check('legend 6位循环', locs_seen == LEGEND_LOCS[1:3], str(locs_seen))
check('legend 字号保留', ax.get_legend().get_texts()[0].get_fontsize() == 8)

check('字号 title +1', tw._adj_font(ax, 'title', 1) == 11)
check('字号 label -1', tw._adj_font(ax, 'label', -1) == 8)
check('字号 legend +1', tw._adj_font(ax, 'legend', 1) == 9)

# ---- 5. export v3 协议 ----
tw.export()
with open(os.path.join(tempfile.gettempdir(), 'test_v3.json'),
          'r', encoding='utf-8') as f:
    data = json.load(f)
check('export version = SCHEMA_VERSION',
      data.get('version') == _params.SCHEMA_VERSION)
a0 = data['axes'][0]
check('export pos', a0['pos'] == [0.1, 0.1, 0.6, 0.6], str(a0['pos']))
check('export title_fontsize', a0['title_fontsize'] == 11)
check('export label_fontsize', a0['label_fontsize'] == 8)
check('export legend loc', a0['legend']['loc'] == LEGEND_LOCS[2], str(a0['legend']))
check('export legend fontsize', a0['legend']['fontsize'] == 9)
check('export aspect_locked', a0['aspect_locked'] is False)

# ---- 5b. export 的新字段：网格身份 + 显式固定过的轴范围 ----
# 交互导出（开窗调图这条路径）产出的参数也必须带 `cell`，否则 apply 的
# "轴序漂移"回比（两边都有 cell 才比）在这条最常走的路径上形同不存在。
check('export cell（手工 add_axes → None）', a0.get('cell') is None,
      str(a0.get('cell')))
check('export xlim（没显式固定 → None，autoscale 不记）', a0.get('xlim') is None,
      str(a0.get('xlim')))
ax.set_xlim(-0.01, 1.01)          # 显式固定 → 之后应当被采集
ax.set_ylim(0.0, 10.0)
tw.export()
with open(os.path.join(tempfile.gettempdir(), 'test_v3.json'),
          'r', encoding='utf-8') as f:
    data2 = json.load(f)
check('export 显式固定的 xlim 被采集', data2['axes'][0].get('xlim') == [-0.01, 1.01],
      str(data2['axes'][0].get('xlim')))
check('export 显式固定的 ylim 被采集', data2['axes'][0].get('ylim') == [0.0, 10.0],
      str(data2['axes'][0].get('ylim')))
_fig5b, _axes5b = plt.subplots(1, 2, figsize=(6, 3))
tw5b = Tweak(_fig5b, export_path=os.path.join(tempfile.gettempdir(),
                                              'test_v3_grid.json'), heavy=False)
tw5b.export()
with open(os.path.join(tempfile.gettempdir(), 'test_v3_grid.json'),
          'r', encoding='utf-8') as f:
    data3 = json.load(f)
check('export 网格轴的 cell = [1, 2, 0, col]',
      [a.get('cell') for a in data3['axes']] == [[1, 2, 0, 0], [1, 2, 0, 1]],
      str([a.get('cell') for a in data3['axes']]))

# ---- 6. PPT 式边框/角点缩放 ----
# 未锁：右边缘拖动（x1 变，x0 固定，高不变）
nb = _resize_frac([0.1, 0.1, 0.6, 0.3], 'right', 0.7, 0.2, False)
check('resize right 边', abs(nb[0] - 0.1) < 1e-12 and abs(nb[2] - 0.7) < 1e-12
      and abs(nb[3] - nb[1] - 0.2) < 1e-12, str(nb))
# 未锁：左边缘（x0 变，x1 固定）
nb = _resize_frac([0.1, 0.1, 0.6, 0.3], 'left', 0.2, 0.2, False)
check('resize left 边', abs(nb[0] - 0.2) < 1e-12 and abs(nb[2] - 0.6) < 1e-12, str(nb))
# 未锁：br 角（x1,y0 变，tl 固定）
nb = _resize_frac([0.1, 0.1, 0.6, 0.3], 'br', 0.5, 0.05, False)
check('resize br 角', abs(nb[0] - 0.1) < 1e-12 and abs(nb[3] - 0.3) < 1e-12
      and abs(nb[2] - 0.5) < 1e-12, str(nb))
# 未锁：MIN_SIZE 约束（右边缘拖到左边去）
nb = _resize_frac([0.1, 0.1, 0.6, 0.3], 'right', 0.105, 0.2, False)
check('resize 最小尺寸', abs((nb[2] - nb[0]) - MIN_SIZE) < 1e-12, str(nb))
# colorbar 细下限（MIN_SIZE_CB） vs 面板默认下限（MIN_SIZE）
nb = _resize_frac([0.4, 0.1, 0.8, 0.3], 'right', 0.405, 0.2, False, MIN_SIZE_CB)
check('colorbar 细下限 0.008', abs((nb[2] - nb[0]) - MIN_SIZE_CB) < 1e-12, str(nb))
nb = _resize_frac([0.4, 0.1, 0.8, 0.3], 'right', 0.405, 0.2, False)
check('面板默认下限 0.02', abs((nb[2] - nb[0]) - MIN_SIZE) < 1e-12, str(nb))
# 锁比：br 角等比（宽高比 4:1 保持）
nb = _resize_frac([0.1, 0.1, 0.5, 0.2], 'br', 0.9, 0.25, True)
check('resize 锁比等比', abs((nb[2] - nb[0]) / (nb[3] - nb[1]) - 4.0) < 1e-9, str(nb))
# 锁比：右边拖拽绕对边中点等比
nb = _resize_frac([0.1, 0.1, 0.5, 0.2], 'right', 0.9, 0.15, True)
check('resize 锁比右边缘', abs(nb[0] - 0.1) < 1e-12
      and abs((nb[2] - nb[0]) / (nb[3] - nb[1]) - 4.0) < 1e-9, str(nb))
# snap_resize：只吸被拖动的边，对边固定（0.604 距 0.6 仅 0.004 < SNAP_TOL → 吸）
nb, gx, gy = _snap_resize([0.604, 0.1, 0.9, 0.3], 'left', [[0.5, 0.1, 0.6, 0.3]])
check('snap_resize 只吸动边', abs(nb[0] - 0.6) < 1e-12 and abs(nb[2] - 0.9) < 1e-12
      and gx == [0.6], str((nb, gx)))
# 超容差不吸（0.62 距 0.6 为 0.02 > SNAP_TOL）
nb, gx, gy = _snap_resize([0.62, 0.1, 0.9, 0.3], 'left', [[0.5, 0.1, 0.6, 0.3]])
check('snap_resize 超容差不吸', abs(nb[0] - 0.62) < 1e-12 and gx == [], str((nb, gx)))
# _hit_handle：命中边/角/内部
fig2 = plt.figure(figsize=(8, 6))
axh = fig2.add_axes([0.1, 0.1, 0.4, 0.4])
pt = fig2.transFigure.transform
_, hd = _hit_handle(fig2, *pt((0.5, 0.3)))
check('hit 右边', hd == 'right', str(hd))
_, hd = _hit_handle(fig2, *pt((0.5, 0.1)))
check('hit br 角', hd == 'br', str(hd))
_, hd = _hit_handle(fig2, *pt((0.3, 0.3)))
check('hit 内部无手柄', hd is None, str(hd))

# ---- 7. 悬停文本字号 + colorbar 识别 + 画布尺寸导出 ----
fig3 = plt.figure(figsize=(8, 6))
ax3 = fig3.add_axes([0.1, 0.1, 0.6, 0.6])
ax3.set_title('T', fontsize=10)
ax3.set_xlabel('x', fontsize=9)
ax3.set_ylabel('y', fontsize=9)
im3 = ax3.imshow(np.zeros((4, 4)))
cb3 = fig3.colorbar(im3, ax=ax3, fraction=0.05, pad=0.03)
cb3.set_label('cb', fontsize=8)
fig3.canvas.draw()
tw3 = Tweak(fig3, export_path=os.path.join(
    tempfile.gettempdir(), 'test_text.json'), heavy=False)
check('colorbar 识别', tw3._as_colorbar(cb3.ax) is not None)
check('普通轴非 colorbar', tw3._as_colorbar(ax3) is None)
tt = ax3.title.get_window_extent()
tx, ty = (tt.x0 + tt.x1) / 2, (tt.y0 + tt.y1) / 2
kind, owner = tw3._text_at(tx, ty)
check('text_at 标题',
      kind == 'text' and owner is ax3.title, str((kind, owner)))
tw3._adj_font_hover(tx, ty, 1)
check('hover 标题 +1', ax3.title.get_fontsize() == 11,
      'fs=%s' % ax3.title.get_fontsize())
xt = ax3.get_xticklabels()[0].get_window_extent()
kind, owner = tw3._text_at(xt.x0 + 1, (xt.y0 + xt.y1) / 2)
check('text_at x刻度', kind == 'xtick', str((kind, owner)))
cbb = cb3.ax.get_position().transformed(fig3.transFigure)
kind, owner = tw3._text_at(cbb.x0 + 2, (cbb.y0 + cbb.y1) / 2)
check('text_at colorbar', kind == 'cb_ticks', str((kind, owner)))
# 画布尺寸：模拟 resize 后导出
tw3._fig_px = (800, 600)
tw3.export()
with open(os.path.join(tempfile.gettempdir(), 'test_text.json'),
          'r', encoding='utf-8') as f:
    d3 = json.load(f)
check('导出 figsize_in', d3.get('figsize_in') == [8.0, 6.0],
      str(d3.get('figsize_in')))
check('导出含 colorbar 轴', len(d3['axes']) == 2, str(len(d3['axes'])))
# box_aspect 解除后：colorbar 宽度跨重绘钉住（make_axes 默认 _box_aspect=20 会强制宽度）
cb3.ax.set_box_aspect(None)
cb3.ax.set_position([0.44, 0.06, 0.03, 0.38])
fig3.canvas.draw()
fig3.canvas.draw()
check('colorbar 宽度钉住', abs(cb3.ax.get_position().width - 0.03) < 1e-9,
      str(cb3.ax.get_position().bounds))

# ---- 8. 窄轴（colorbar 细条）抓取：左/右 1/3 扩区，正中留给移动 ----
fig4 = plt.figure(figsize=(8, 6))
axn = fig4.add_axes([0.475, 0.06, 0.025, 0.38])   # 窄条：宽 20px @ 800px
fig4.canvas.draw()
pt4 = fig4.transFigure.transform
_, hd = _hit_handle(fig4, *pt4((0.478, 0.2)))     # 左 1/3
check('窄轴 左抓取', hd == 'left', str(hd))
_, hd = _hit_handle(fig4, *pt4((0.497, 0.2)))     # 右 1/3
check('窄轴 右抓取', hd == 'right', str(hd))
_, hd = _hit_handle(fig4, *pt4((0.4875, 0.2)))    # 正中 -> 移动
check('窄轴 中点留移动', hd is None, str(hd))

# ---- 8b. 细长**水平** colorbar：上/下边必须能抓（否则"想调高度却改了宽度"）----
# 真实场景：h=0.020 @ 700px ≈ 14px 高的水平 colorbar
fig5 = plt.figure(figsize=(15, 7))
axh = fig5.add_axes([0.19, 0.14, 0.28, 0.020])
fig5.canvas.draw()
pt5 = fig5.transFigure.transform
y_mid = 0.14 + 0.020 / 2
_, hd = _hit_handle(fig5, *pt5((0.33, y_mid + 0.008)))    # 靠上边
check('水平条 上边抓取', hd == 'top', str(hd))
_, hd = _hit_handle(fig5, *pt5((0.33, y_mid - 0.008)))    # 靠下边
check('水平条 下边抓取', hd == 'bottom', str(hd))
_, hd = _hit_handle(fig5, *pt5((0.33, y_mid)))            # 正中 -> 移动
check('水平条 中点留移动', hd is None, str(hd))
_, hd = _hit_handle(fig5, *pt5((0.191, y_mid)))           # 左端 -> 宽（14px 高必然命中角）
check('水平条 左端可抓宽', hd in ('left', 'tl', 'bl'), str(hd))

# ---- 8c. _snap_resize 只吸附被拖动的那条边 ----
# 旧实现用子串判断（'left' 含 't'、'right' 含 't'、'bottom' 含 't'）→ 拖左右边会
# 连带把上边 y1 也吸走，表现为"拖高度/宽度时另一维也变了"。
_others = [[0.10, 0.10, 0.30, 0.40]]
_b = [0.20, 0.20, 0.50, 0.4005]        # y1=0.4005 距参照 y1=0.40 仅 0.0005（在容差内）
nb, _, _ = _snap_resize(_b, 'left', _others)
check('snap left 不动 y', abs(nb[3] - _b[3]) < 1e-12 and abs(nb[1] - _b[1]) < 1e-12,
      str(nb))
nb, _, _ = _snap_resize(_b, 'right', _others)
check('snap right 不动 y', abs(nb[3] - _b[3]) < 1e-12, str(nb))
nb, _, _ = _snap_resize(_b, 'bottom', _others)
check('snap bottom 不动 y1', abs(nb[3] - _b[3]) < 1e-12, str(nb))
nb, gx, _ = _snap_resize([0.20, 0.20, 0.50, 0.402], 'top', [[0.10, 0.10, 0.30, 0.40]])
check('snap top 只动 y1', abs(nb[3] - 0.40) < 1e-12 and abs(nb[0] - 0.20) < 1e-12
      and abs(nb[2] - 0.50) < 1e-12, str(nb))

# ---- 9. colorbar 多信号识别 + 解除 locator ----
# 真实论文脚本里 colorbar 的 label 是空串，只看 label 会漏判 →
# ①最小尺寸误用 0.02（细条减不薄）②_ColorbarAxesLocator 每次重绘按父轴重算
# x0/宽度 → "改高度时宽度自己缩"。
fig6 = plt.figure(figsize=(8, 6))
axm6 = fig6.add_axes([0.10, 0.40, 0.50, 0.40])
im6 = axm6.imshow([[0.0, 1.0], [1.0, 0.0]])
cax6 = fig6.add_axes([0.10, 0.20, 0.50, 0.03])
cb6 = fig6.colorbar(im6, cax=cax6, orientation='horizontal')
fig6.canvas.draw()
check('colorbar 被识别', _is_colorbar_ax(cax6), repr(cax6.get_label()))
check('普通轴不被误判', not _is_colorbar_ax(axm6), repr(axm6.get_label()))

tw6 = Tweak(fig6)
check('colorbar locator 已解除', cax6.get_axes_locator() is None,
      type(cax6.get_axes_locator()).__name__)
cax6.set_position([0.10, 0.20, 0.25, 0.06])
fig6.canvas.draw()
b6 = cax6.get_position().bounds
check('colorbar 位置钉住（重绘不改回）',
      abs(b6[2] - 0.25) < 1e-9 and abs(b6[3] - 0.06) < 1e-9,
      '[%.4f, %.4f, %.4f, %.4f]' % b6)

# ---- 10. 开窗即预抓背景（否则"第一下拖拽"要等一次全量重绘 = 卡半秒）----
check('开窗即已预抓背景', tw6._bg is not None,
      'None 表示首拖要等一次全量重绘')

# ---- 11. 线条模式（空格）：隐藏数据图元、保留边框/文字，可完整恢复 ----
fig7 = plt.figure(figsize=(8, 6))
ax7 = fig7.add_axes([0.10, 0.10, 0.60, 0.60])
ln7, = ax7.plot([0, 1], [0, 1], label='a')
ax7.set_title('t7')
fig7.canvas.draw()
tw7 = Tweak(fig7)
tw7._toggle_wireframe()
check('线条模式：数据图元已隐藏', not ln7.get_visible(), str(ln7.get_visible()))
check('线条模式：标题文字仍可见', ax7.title.get_visible(), str(ax7.title.get_visible()))
check('线条模式：进过模式标记', tw7._wire is True, str(tw7._wire))
tw7._toggle_wireframe()
check('线条模式：退出后恢复原状', ln7.get_visible() and tw7._wire is False,
      'line=%s wire=%s' % (ln7.get_visible(), tw7._wire))

# ---- 12. 锁长宽比轴的 ↑/→ 等比缩放（只改一维会被 apply_aspect 修正回去）----
fig8 = plt.figure(figsize=(8, 6))
ax8 = fig8.add_axes([0.2, 0.2, 0.3, 0.3])
ax8.set_aspect('equal')
fig8.canvas.draw()
tw8 = Tweak(fig8)
tw8.locked_map[id(ax8)] = True
_c8 = fig8.transFigure.transform((0.35, 0.35))
_b8 = list(_bbox(ax8))
tw8._nudge_size('shift+up',
                type('E', (), {'inaxes': ax8, 'key': 'shift+up'})())
_b9 = list(_bbox(ax8))
check('锁比轴 ↑ 同比放大', abs(_b9[2] / _b9[3] - _b8[2] / _b8[3]) < 1e-9
      and _b9[3] > _b8[3], '%s -> %s' % (_b8, _b9))
check('锁比轴 ↑ 中心不动',
      abs((_b9[0] + _b9[2] / 2) - (_b8[0] + _b8[2] / 2)) < 1e-9
      and abs((_b9[1] + _b9[3] / 2) - (_b8[1] + _b8[3] / 2)) < 1e-9, str(_b9))

# ---- 13. 字号命中：colorbar 条外刻度标签 + ax.text 面板标注 ----
fig9 = plt.figure(figsize=(8, 6))
ax9 = fig9.add_axes([0.1, 0.5, 0.5, 0.35])
im9 = ax9.imshow([[0.0, 1.0], [1.0, 0.0]])
cax9 = fig9.add_axes([0.1, 0.20, 0.5, 0.03])
fig9.colorbar(im9, cax=cax9, orientation='horizontal')
tx9 = ax9.text(0.5, 0.5, '(a)', transform=ax9.transAxes)
fig9.canvas.draw()
tw9 = Tweak(fig9)
_tbb = cax9.get_xticklabels()[0].get_window_extent()
_hk = tw9._text_at((_tbb.x0 + _tbb.x1) / 2, (_tbb.y0 + _tbb.y1) / 2)[0]
check('colorbar 条外刻度标签可命中', _hk == 'cb_ticks', str(_hk))
_xbb = tx9.get_window_extent()
_hk2 = tw9._text_at((_xbb.x0 + _xbb.x1) / 2, (_xbb.y0 + _xbb.y1) / 2)[0]
check('ax.text 面板标注可命中', _hk2 == 'text', str(_hk2))

# ---- 14. loc='left' 的面板标题：文字在 ax._left_title 上（不是 ax.title）----
fig10 = plt.figure(figsize=(8, 6))
ax10 = fig10.add_axes([0.15, 0.2, 0.7, 0.6])
ax10.set_title('panel-a', loc='left', fontsize=16)
fig10.canvas.draw()
tw10 = Tweak(fig10)
_lb = ax10._left_title.get_window_extent()
_k10, _o10 = tw10._text_at((_lb.x0 + _lb.x1) / 2, (_lb.y0 + _lb.y1) / 2)
check('loc=left 面板标题可命中',
      _k10 == 'text' and _o10 is ax10._left_title,
      'kind=%s owner_ok=%s' % (_k10, _o10 is ax10._left_title))
check('标题字号取 loc=left 那个', _title_fs(ax10) == 16, str(_title_fs(ax10)))

# ---- 15. HitMap：建表 + 查表命中 ----
figH = plt.figure(figsize=(8, 6))
axH = figH.add_axes([0.15, 0.2, 0.7, 0.6])
axH.set_title('tH', loc='left')
figH.canvas.draw()
twH = Tweak(figH)
twH._build_hits()
check('HitMap 已建表', len(twH._hits) > 0, str(len(twH._hits)))
_tb = axH._left_title.get_window_extent()
check('HitMap 查表命中 loc=left 标题',
      twH._text_at((_tb.x0 + _tb.x1) / 2, (_tb.y0 + _tb.y1) / 2)[0] == 'text',
      str(len(twH._hits)))

# ---- 16. 锚点化缩放：锚点那个点不动 ----
_n = _resize_anchored([0.2, 0.2, 0.4, 0.4], (0.5, 0.5), 0.2, 0.4)
check('锚点(0.5,0.5) 中心不动',
      approx(_n[0] + _n[2] / 2, 0.4) and approx(_n[1] + _n[3] / 2, 0.4), str(_n))
_n = _resize_anchored([0.2, 0.2, 0.4, 0.4], (0, 0), 0.2, 0.4)
check('锚点(0,0) 左下角不动',
      approx(_n[0], 0.2) and approx(_n[1], 0.2), str(_n))

# ---- 17. 移动吸附含「中线」候选 ----
# 参照面板较宽（0.2~0.6，中心 0.4）；我的面板中心 0.41、左右边都离候选线很远，
# 只有「中心对齐中心」这一对在容差内 → 必须吸到 0.4
_nb, _gx, _gy = _snap_box([0.31, 0.5, 0.2, 0.1], [[0.2, 0.5, 0.4, 0.1]],
                          tol=0.02)
check('吸附可用中线对齐', approx(_nb[0] + _nb[2] / 2, 0.4), str(_nb))
check('中线吸附给出参考线', len(_gx) == 1, str(_gx))

# ---- 18. 多选对齐 / 均分 ----
figA = plt.figure(figsize=(8, 6))
axsA = [figA.add_axes([0.1 + i * 0.25, 0.1 + i * 0.12, 0.2, 0.2])
        for i in range(3)]
figA.canvas.draw()
twA = Tweak(figA)
twA._selected = {id(a) for a in axsA}
twA._align_selection('left')
check('对齐左边缘', all(approx(_bbox(a)[0], 0.1) for a in axsA),
      str([round(_bbox(a)[0], 4) for a in axsA]))
twA._distribute_selection('v')
_bs = sorted([_bbox(a) for a in axsA], key=lambda b: b[1])
_gaps = [_bs[i + 1][1] - (_bs[i][1] + _bs[i][3]) for i in range(2)]
check('垂直均分等间距', abs(_gaps[0] - _gaps[1]) < 1e-9, str([round(g, 4) for g in _gaps]))

# ---- 19. 方向键连续微调：undo 合并为一步（seal）----
_Ev = type('E', (), {'inaxes': axsA[0], 'key': 'up'})
_n0 = len(twA._undo)
for _ in range(3):
    twA._nudge_size('up', _Ev())
check('连续微调合并为一步 undo', len(twA._undo) - _n0 == 1,
      'undo +%d' % (len(twA._undo) - _n0))

# ---- 21. 撤销可视化：diff 生成操作名 + undo/redo 带 toast 不崩 ----
_E21 = type('E', (), {'inaxes': axsA[0], 'key': 'left'})
_bx21 = list(_bbox(axsA[0]))
twA._push_undo()
twA._nudge_size('left', _E21())                # x0 左移 0.005
_bx22 = list(_bbox(axsA[0]))
check('撤销可视化：方向键移动生效', abs(_bx22[0] - (_bx21[0] - 0.005)) < 1e-9,
      str(_bx22))
_snap0 = twA._snapshot()
twA._push_undo()
twA._nudge_size('left', _E21())                # 再左移一次
_snap1 = twA._snapshot()
_nm21 = twA._diff_name(_snap0, _snap1) or ''
# F5 之后操作名按"有无 CJK 字体"双语（无 CJK 时回退英文），断言也要两可。
check('撤销可视化：操作名提到移动', ('移动' in _nm21) or ('move' in _nm21),
      repr(_nm21))
twA._undo_once()                               # 撤销（内部 _toast 兜底，不崩）
twA._redo_once()
check('撤销可视化：undo/redo 不崩', True, '')


# ---- 20. 编辑顺序无关（figtune 四铁律之一）----
def _order_state(seq):
    f = plt.figure(figsize=(6, 4))
    a = f.add_axes([0.1, 0.1, 0.6, 0.6])
    a.plot([0, 1], label='x')
    a.set_title('T', fontsize=10)
    a.legend(fontsize=8)
    f.canvas.draw()
    for fn in seq:
        fn(f, a)
    f.canvas.draw()
    leg = a.get_legend()
    return (tuple(round(v, 9) for v in _bbox(a)), a.title.get_fontsize(),
            leg.get_texts()[0].get_fontsize(), str(leg._loc))


_ed_a = [lambda f, a: a.set_position([0.2, 0.2, 0.5, 0.5]),
         lambda f, a: a.title.set_fontsize(14),
         lambda f, a: a.legend(loc='lower left', fontsize=9)]
_ed_b = [lambda f, a: a.legend(loc='lower left', fontsize=9),
         lambda f, a: a.set_position([0.2, 0.2, 0.5, 0.5]),
         lambda f, a: a.title.set_fontsize(14)]
check('编辑顺序无关（位置/字号/图例）',
      _order_state(_ed_a) == _order_state(_ed_b),
      '%s vs %s' % (_order_state(_ed_a), _order_state(_ed_b)))

# ---- 21. #8 第一步：clim 端点/属性按键/导出字段 ----
figS = plt.figure(figsize=(8, 5))
axS = figS.add_axes([0.12, 0.35, 0.58, 0.5])
imS = axS.imshow(np.arange(100).reshape(10, 10), vmin=0, vmax=100)
lineS, = axS.plot([0, 9], [0, 9], linewidth=1, color='black')
cbS = figS.colorbar(imS, ax=axS, orientation='horizontal')
figS.canvas.draw()
twS = Tweak(figS, export_path=os.path.join(tempfile.gettempdir(), 'test_step8.json'), heavy=False)
_bbS = cbS.ax.get_position().transformed(figS.transFigure)
_exS, _eyS = _bbS.x0, (_bbS.y0 + _bbS.y1) / 2
check('clim 端点命中', _hit_cb_endpoint(figS, _exS, _eyS) == (cbS.ax, 'vmin'))
_oldS = _clim_of(cbS.ax)
# F2：Alt+拖端点改 clim 已按用户要求移除（handler 与死代码全删）。
# 这里钉死两点：①clim 保持原值（没有任何拖拽 handler 会改它）；
# ②空函数/死属性确已不在（防"功能删了、死代码又悄悄长回来"）。
check('clim 拖拽已移除（Alt+拖端点功能删）', _clim_of(cbS.ax) == _oldS,
      str(_clim_of(cbS.ax)))
check('F2：clim 拖拽空函数已删除',
      not hasattr(twS, '_update_cb_clim_drag')
      and not hasattr(twS, '_finish_cb_clim_drag'), '')
check('F2：_fmt_clim 已删除', not hasattr(twS, '_fmt_clim'), '')
check('F2：clim 拖拽死属性已删除',
      not hasattr(twS, '_clim_ax') and not hasattr(twS, '_drag_cb_clim'), '')
_EvS = type('E', (), {'inaxes': axS, 'x': axS.transData.transform((4, 4))[0], 'y': axS.transData.transform((4, 4))[1]})()
for _k in (']', 'c', 'C', 'g', 's', 'x', 'y'):
    twS._on_key(type('K', (), {'key': _k, 'inaxes': axS, 'x': _EvS.x, 'y': _EvS.y})())
check('[/] 键位线宽', lineS.get_linewidth() == 1.5, str(lineS.get_linewidth()))
check('c 键位颜色', lineS.get_color() != 'black')
check('C 键位 colormap', imS.get_cmap().name == 'plasma')
check('g 键位 grid', any(g.get_visible() for g in axS.get_xgridlines()))
check('s 键位 spines', not axS.spines['top'].get_visible())
check('x/y 键位 scale', axS.get_xscale() == 'log' and axS.get_yscale() == 'log')
twS.export()
with open(os.path.join(tempfile.gettempdir(), 'test_step8.json'), encoding='utf-8') as _fS:
    _dS = json.load(_fS)
check('导出含 #8 字段', all(k in _dS['axes'][0] for k in ('cmap', 'grid', 'spines', 'xscale', 'yscale', 'lines')))
check('导出含 colorbar clim', _dS['axes'][1].get('clim') is not None)

# ---- 22. c 键健壮性：Quiver/scatter/填色等值线不进单色循环（真机 bug 回归）----
# 真实论文图上按 c 曾抛：RGBA sequence should have length 3 or 4
# 根因 = Quiver/scatter 这类 Collection 也带 set_linewidth/set_color，
# 但颜色是 N×4 数组，喂给 to_hex 就炸。修法是白名单 + 颜色归一。
from mpltweak.toolbox import _is_line_artist, _single_color, _line_at   # noqa: E402

figQ = plt.figure(figsize=(6, 5))
axQ = figQ.add_axes([0.12, 0.12, 0.78, 0.78])
_X, _Y = np.meshgrid(np.arange(0, 3, 0.5), np.arange(0, 3, 0.5))
_quiver = axQ.quiver(_X, _Y, _X, _Y)                     # 箭头（PolyCollection）
_contf = axQ.contourf(_X, _Y, _X, colors=['#ffffff', '#cccccc'])   # 显式离散色（NoNorm）
_cs = axQ.contour(_X, _Y, _Y)                            # 等值线（可单色）
_sc = axQ.scatter([1.5], [1.5], c=[[1.0, 0.0, 0.0, 1.0]])          # N×4 颜色
_lq, = axQ.plot([0, 3], [0, 3], linewidth=1, color='black')
figQ.canvas.draw()

check('白名单：plot 线放行', _is_line_artist(_lq))
check('白名单：未填充等值线放行', _is_line_artist(_cs))
check('白名单：Quiver 拦下', not _is_line_artist(_quiver))
check('白名单：填色等值线拦下', not _is_line_artist(_contf))
check('白名单：scatter 拦下', not _is_line_artist(_sc))
check('颜色归一：N×4 数组不炸', _single_color(_sc.get_facecolors()) is not None)
check('颜色归一：坏值返回 None', _single_color('not-a-color') is None)

twQ = Tweak(figQ, export_path=os.path.join(
    tempfile.gettempdir(), 'test_step8_q.json'), heavy=False)
# 鼠标正压在箭头上（曾经就是这里炸的）
_qx, _qy = axQ.transData.transform((_X[2][3], _Y[2][3]))
_EvQ = type('E', (), {'key': 'c', 'inaxes': axQ, 'x': float(_qx), 'y': float(_qy)})()
_fc_before = _sc.get_facecolors().copy()
try:
    twQ._on_key(_EvQ)                                    # 不抛异常 = 通过
    _ok_c = True
    _err_c = ''
except Exception as _e:
    _ok_c = False
    _err_c = repr(_e)
check('c 键命中 Quiver 不抛异常', _ok_c, _err_c)
check('c 键不动 scatter 颜色', np.array_equal(_fc_before, _sc.get_facecolors()))
# 悬停真正的线条时，c 仍然要生效（别把功能一起关掉）
_lx, _ly = axQ.transData.transform((1.5, 1.5))
_c_before = _lq.get_color()
twQ._on_key(type('E', (), {'key': 'c', 'inaxes': axQ,
                           'x': float(_lx), 'y': float(_ly)})())
check('c 键悬停线条仍生效', _lq.get_color() != _c_before,
      '%s -> %s' % (_c_before, _lq.get_color()))

# ---- 23. F4：续调 _apply_saved 恢复 loc=left/right 标题字号 + 字号类计入 n ----
from mpltweak.launch import _apply_saved        # noqa: E402

figF4 = plt.figure(figsize=(8, 6))
axF4 = figF4.add_axes([0.1, 0.1, 0.7, 0.7])
axF4.set_title('panel', loc='left', fontsize=12)
axF4.set_title('mid', loc='center', fontsize=12)
axF4.set_title('right', loc='right', fontsize=12)
axF4.set_xlabel('x', fontsize=9)
axF4.set_ylabel('y', fontsize=9)
figF4.canvas.draw()
_pF4 = os.path.join(tempfile.gettempdir(), 'test_f4_resume.json')
with open(_pF4, 'w', encoding='utf-8') as _f:
    json.dump({'version': 4, 'axes': [
        {'index': 0, 'title_fontsize': 20,
         'label_fontsize': 11, 'label_fontsize_y': 13}]}, _f)
_nF4 = _apply_saved(figF4, _pF4)
check('F4：续调恢复 loc=left 标题字号', axF4._left_title.get_fontsize() == 20,
      'left=%s' % axF4._left_title.get_fontsize())
check('F4：续调恢复 loc=right 标题字号', axF4._right_title.get_fontsize() == 20,
      'right=%s' % axF4._right_title.get_fontsize())
check('F4：续调恢复中间标题字号', axF4.title.get_fontsize() == 20,
      'mid=%s' % axF4.title.get_fontsize())
check('F4：label 按 x/y 各自套用', axF4.xaxis.label.get_fontsize() == 11
      and axF4.yaxis.label.get_fontsize() == 13,
      'x=%s y=%s' % (axF4.xaxis.label.get_fontsize(), axF4.yaxis.label.get_fontsize()))
check('F4：字号类续调计入 n（不再误判未套用）', _nF4 >= 1, 'n=%d' % _nF4)

# T73（零兼容）：参数只有单一 label_fontsize（无 label_fontsize_y）→ **只套 x，不动 y**
# （上一步续调已把 y 设成 13；这一条要确认 y 不被 x 的值覆盖）
with open(_pF4, 'w', encoding='utf-8') as _f:
    json.dump({'version': 4, 'axes': [
        {'index': 0, 'label_fontsize': 15}]}, _f)
_nF4b = _apply_saved(figF4, _pF4)
check('T73（零兼容）：只给 label_fontsize → 只套 x，y 保持 13 不被改成 15',
      axF4.xaxis.label.get_fontsize() == 15 and axF4.yaxis.label.get_fontsize() == 13,
      'x=%s y=%s' % (axF4.xaxis.label.get_fontsize(), axF4.yaxis.label.get_fontsize()))

# ---- 24. T25-#1：figsize 口径（dpi=200 / dpi=100 导出必须等于源码 figsize）----
for _dpi in (200, 100):
    _figF = plt.figure(figsize=(6, 4), dpi=_dpi)
    _axF = _figF.add_axes([0.1, 0.1, 0.8, 0.8])
    _figF.canvas.draw()
    _twF = Tweak(_figF, export_path=os.path.join(
        tempfile.gettempdir(), 'test_figsize_dpi%d.json' % _dpi), heavy=False)
    _twF._fig_px = _figF.canvas.get_width_height()   # 模拟 GUI resize 事件
    _pF = os.path.join(tempfile.gettempdir(), 'test_figsize_dpi%d.json' % _dpi)
    _twF.export(path=_pF)
    with open(_pF, encoding='utf-8') as _f:
        _dF = json.load(_f)
    check('T25-#1：dpi=%d 导出 figsize_in == [6.0, 4.0]' % _dpi,
          _dF.get('figsize_in') == [6.0, 4.0],
          'figsize_in=%s（dpi=%d, px=%s）'
          % (_dF.get('figsize_in'), _dpi, _dF.get('figsize_px')))
    plt.close(_figF)

# ---- 25. T25-#2a：双开丢改动必须大声提示（stderr），不得静默 return ----
import io as _io
import contextlib as _cl

_figD = plt.figure(figsize=(6, 4))
_axD = _figD.add_axes([0.1, 0.1, 0.8, 0.8])
_figD.canvas.draw()
_pD = os.path.join(tempfile.gettempdir(), 'test_double_open.json')
_twD = Tweak(_figD, export_path=_pD, heavy=False)
# 模拟后开进程覆盖 pid 锁
with open(_pD + '.lock', 'w', encoding='utf-8') as _f:
    _f.write(str(os.getpid() + 999999))
if os.path.exists(_pD):
    os.remove(_pD)
_bufD = _io.StringIO()
with _cl.redirect_stderr(_bufD):
    _twD.export(path=_pD)
_errD = _bufD.getvalue()
check('T25-#2a：锁被接管时 stderr 有大响提示',
      ('接管' in _errD) or ('taken over' in _errD) or ('未保存' in _errD),
      repr(_errD))
check('T25-#2a：锁被接管时不落盘（不覆盖新会话）', not os.path.exists(_pD),
      'file exists=%s' % os.path.exists(_pD))

# ---- 26. T25-#2c：图例重建用图例自身 handles（ax.legend(handles=[l1,l3]) 保持 2 条）----
_figL = plt.figure(figsize=(8, 6))
_axL = _figL.add_axes([0.1, 0.1, 0.8, 0.8])
_l1, = _axL.plot([0, 1], [0, 1], label='a')
_l2, = _axL.plot([0, 1], [1, 0], label='b')
_l3, = _axL.plot([0, 1], [0.5, 0.5], label='c')
_legL = _axL.legend(handles=[_l1, _l3], labels=['a', 'c'])
_figL.canvas.draw()
_twL = Tweak(_figL, export_path=os.path.join(
    tempfile.gettempdir(), 'test_legend_handles.json'), heavy=False)
_stL = _twL.leg_state.get(id(_axL))
check('T25-#2c：_init_legends 用图例自身 handles（2 条不是 3 条）',
      _stL is not None and len(_stL['handles']) == 2,
      'handles=%s labels=%s'
      % (len(_stL['handles']) if _stL else None, _stL['labels'] if _stL else None))
check('T25-#2c：labels 是图例自身文本', _stL is not None and _stL['labels'] == ['a', 'c'],
      str(_stL['labels'] if _stL else None))
_legL2 = _twL._mk_legend(_stL, _axL, loc='upper right')
check('T25-#2c：重建后仍 2 条（l2 不被加回）',
      len(list(getattr(_legL2, 'legend_handles', []))) == 2,
      str(len(list(getattr(_legL2, 'legend_handles', [])))))
# fig.legend() → 明确提示不接管
_figL2 = plt.figure(figsize=(8, 6))
_axL2 = _figL2.add_axes([0.1, 0.1, 0.8, 0.8])
_ll, = _axL2.plot([0, 1], [0, 1], label='x')
_figL2.legend()
_figL2.canvas.draw()
_bufL2 = _io.StringIO()
with _cl.redirect_stderr(_bufL2):
    Tweak(_figL2, export_path=os.path.join(
        tempfile.gettempdir(), 'test_figlegend.json'), heavy=False)
check('T25-#2c：fig.legend() 明确提示不接管',
      ('fig.legend' in _bufL2.getvalue()) or ('图级图例' in _bufL2.getvalue()),
      repr(_bufL2.getvalue()))

# ---- 27. 0.1.10#1：fit 裁白边三缺陷回归 ----
# ① suptitle / fig.text 必须进内容盒（旧实现只并轴框 ∪ ax.tightbbox ∪ 图例，
#    主标题被裁掉一块）；② 新 figsize 按英寸直算、与 dpi/dpr 完全解耦
#    （旧实现「canvas 逻辑 px ÷ fig.dpi」在 Windows 200% + HiDPI 下缩小一半）。
def _mk_fit_fig(dpi=100.0, suptitle=None):
    _f = plt.figure(figsize=(10, 8))
    _f.set_dpi(dpi)
    _a = _f.add_axes([0.1, 0.1, 0.8, 0.8])
    _a.plot([0, 1], [0, 1])
    if suptitle:
        _f.suptitle(suptitle, fontsize=16)
    _f.canvas.draw()
    return _f

# ① suptitle 进内容盒：有 suptitle 的图 fit 后高度 > 无 suptitle 的图
_fitA = _mk_fit_fig(suptitle=None)
_twA = Tweak(_fitA, export_path=os.path.join(
    tempfile.gettempdir(), 'test_fit_a.json'), heavy=False)
_twA._fit_to_content()
_h_no_title = _fitA.get_size_inches()[1]
plt.close(_fitA)
_fitB = _mk_fit_fig(suptitle='A TITLE THAT MUST SURVIVE THE FIT')
_twB = Tweak(_fitB, export_path=os.path.join(
    tempfile.gettempdir(), 'test_fit_b.json'), heavy=False)
_twB._fit_to_content()
_h_with_title = _fitB.get_size_inches()[1]
check('0.1.10#1-① fit 内容盒含 suptitle（有标题的高度更大，不被裁）',
      _h_with_title > _h_no_title + 0.05,
      'no-title=%.3f with-title=%.3f' % (_h_no_title, _h_with_title))
plt.close(_fitB)

# ② dpi 解耦：模拟 Qt HiDPI（get_width_height 返回逻辑 px，fig.dpi=物理 200）
#    —— 旧实现会用逻辑 px÷200 把图缩成一半；新实现按英寸直算不缩水。
_fitC = _mk_fit_fig(dpi=200.0, suptitle='HI DPI FIT')
# 模拟 Qt 行为：canvas.get_width_height 给逻辑 px（物理的一半）
#
# ⚠ matplotlib 3.11 起签名变成 `get_width_height(*, physical=False)`：**关键字参数**，
#   而且 `FigureCanvasAgg.get_renderer()` 会以 `physical=True` 调进来（3.10 不会）。
#   所以这个 mock **必须接受 physical**，否则 py3.11/3.12 + mpl 3.11 的 CI 直接
#   `TypeError: <lambda>() got an unexpected keyword argument 'physical'`（本任务根因）。
#   语义照真画布来，保持本用例原本的模拟意图：
#     physical=False（逻辑 px）→ 物理的一半 ← 本用例要模拟的 HiDPI 现象
#     physical=True （物理 px）→ 真实像素值（= 逻辑 × 2），与后端缓冲区尺寸一致
#   额外容忍未知关键字：将来 matplotlib 再给这个方法加参数，也不至于又把 CI 弄红。
_real_gwh = _fitC.canvas.get_width_height
_real_physical = _real_gwh()


def _hidpi_gwh(physical=False, **_kw):
    if physical:
        return _real_physical
    return (_real_physical[0] // 2, _real_physical[1] // 2)


_fitC.canvas.get_width_height = _hidpi_gwh
_twC = Tweak(_fitC, export_path=os.path.join(
    tempfile.gettempdir(), 'test_fit_c.json'), heavy=False)
_twC._fit_to_content()
_wi, _hi = _fitC.get_size_inches()
check('0.1.10#1-② fit 与 dpi 解耦（HiDPI 下不再缩一半：宽≈8.3 而非 ≈4.15）',
      7.5 < _wi < 9.5, 'fiw=%.3f' % _wi)
_fitC.canvas.get_width_height = _real_gwh
plt.close(_fitC)

# ---- 28. 0.1.10#2-②：拖拽用归一化锚点（fig.bbox 变化后增量仍正确）----
# 旧实现：motion 用 `(event.x - _press_xy[0]) / X`，X 是**当前** fig.bbox 宽度；
# 若按下后被钳制/缩窗（fig.bbox 变了），旧像素锚点错位 → ghost 蓝框对不上。
# 新实现：按下记归一化锚点 _press_norm，motion 只算归一化增量。
_figN = plt.figure(figsize=(10, 8))
_axN = _figN.add_axes([0.1, 0.1, 0.4, 0.4])
_axN.plot([0, 1], [0, 1])
_figN.canvas.draw()
_twN = Tweak(_figN, export_path=os.path.join(
    tempfile.gettempdir(), 'test_drag_norm.json'), heavy=False)
_X0, _Y0 = _figN.bbox.width, _figN.bbox.height
_b0 = _bbox(_axN)
# 按下（面板中心）
_px0, _py0 = (_b0[0] + _b0[2] / 2) * _X0, (_b0[1] + _b0[3] / 2) * _Y0
from matplotlib.backend_bases import MouseEvent as _ME
_ev = _ME('button_press_event', _figN.canvas, _px0, _py0, button=1)
_ev.inaxes = _axN
_twN._on_press(_ev)
check('0.1.10#2-② 按下记录归一化锚点',
      abs(_twN._press_norm[0] - (_b0[0] + _b0[2] / 2)) < 1e-9
      and abs(_twN._press_norm[1] - (_b0[1] + _b0[3] / 2)) < 1e-9,
      str(_twN._press_norm))
# 模拟窗口被钳制：fig.bbox 中途变化（motion 前缩窗）
_figN.set_size_inches(6, 5)
_X1, _Y1 = _figN.bbox.width, _figN.bbox.height
# motion：目标 = 归一化锚点 + 0.05 增量（不管 fig.bbox 多大，增量都该是 0.05）
_ev = _ME('motion_notify_event', _figN.canvas,
          (_twN._press_norm[0] + 0.05) * _X1,
          _twN._press_norm[1] * _Y1)
_ev.inaxes = _axN
_twN._on_motion(_ev)
# 松手落位（重图模式 bbox 在 release 才真正 set_position）
_ev = _ME('button_release_event', _figN.canvas,
          (_twN._press_norm[0] + 0.05) * _X1,
          _twN._press_norm[1] * _Y1, button=1)
_twN._on_release(_ev)
_b1 = _bbox(_axN)
check('0.1.10#2-② fig.bbox 变化后拖拽增量仍正确（x 移 0.05）',
      abs(_b1[0] - _b0[0] - 0.05) < 1e-6,
      'b0=%s b1=%s' % ([round(v, 4) for v in _b0], [round(v, 4) for v in _b1]))
plt.close(_figN)

# ---- 29. t19：超屏请求不许被窗口钳制改形（长宽比守恒）----
# 根因（t16 真窗口实测）：Tweak.__init__ 里 w.show() 过去跑在 _fit_to_screen()
# 之前 → Windows 钳制窗口时 backend_qt.py:395-399 的 resizeEvent 按"钳制后的画布
# 像素"逐轴 set_size_inches，figsize 被改成**非等比**（请求 30×22in 长宽比 1.364 →
# 画布 1601×496px 即 3.234）；随后那次等比缩放把这个**已变形**的尺寸整体缩到 57%
# （→ 912×282）—— 变形被固化，_figsize_open 记的也是变形值。
# 本用例在 Agg 后端（**绝不开窗**）里用假 window 复现同一条路径：
#   ① 开窗**前**就必须已经等比缩到屏内，且 _figsize_open 记的是等比尺寸；
#   ② 即使窗口管理器**仍然**把画布钳制成非等比，resizeEvent 守卫也必须恢复等比。
_AVAIL = (960, 516)                      # 模拟 t16 那台机器的屏幕可用区
_REQ_W, _REQ_H = 30.0, 22.0              # 请求 30×22in
_RATIO_REQ = _REQ_W / _REQ_H             # 1.3636…
_CLAMP_PX = (1601, 496)                  # t16 实测的"钳制后画布像素"（3.234）


class _FakeWin:
    '''假窗口：模拟 w.show()/raise_() 与 backend_qt resizeEvent 的改形行为。'''

    def __init__(self, fig, clamp_px=None):
        self.fig = fig
        self.clamp_px = clamp_px
        self.shown = None            # 开窗瞬间的 figsize（证明"开窗前已等比缩放"）

    def show(self):
        self.shown = tuple(self.fig.get_size_inches())
        if self.clamp_px is not None:
            _dpi = float(self.fig.dpi)
            # backend_qt.py:395-399 的等价物：按"钳制后的画布像素"逐轴换算
            self.fig.set_size_inches(self.clamp_px[0] / _dpi,
                                     self.clamp_px[1] / _dpi)
            # 与 backend_qt 一样：改完尺寸触发 resize_event（钳制守卫在那里）
            self.fig.canvas.callbacks.process('resize_event', None)

    def raise_(self):
        pass


def _oversized_fig(dpi=100.0):
    _f = plt.figure(figsize=(_REQ_W, _REQ_H))
    _f.set_dpi(dpi)
    _f.add_axes([0.1, 0.1, 0.8, 0.8]).plot([0, 1], [0, 1])
    _f.canvas.draw()
    return _f


_avail_real = Tweak._screen_available
Tweak._screen_available = lambda self: _AVAIL      # 无头下钉死"屏幕可用区"
try:
    # ① 开窗前就已等比缩到屏内（长宽比守恒）—— 旧实现此处 shown=30×22（未缩）
    _f19a = _oversized_fig()
    _w19a = _FakeWin(_f19a)
    _f19a.canvas.manager.window = _w19a
    _tw19a = Tweak(_f19a, export_path=os.path.join(
        tempfile.gettempdir(), 'test_oversize_a.json'), heavy=False)
    _shown = _w19a.shown or (0.0, 1.0)
    _r_open = _shown[0] / _shown[1]
    check('t19① 开窗瞬间的 figsize 长宽比守恒（未被窗口钳制改形，偏离 <2%）',
          _w19a.shown is not None
          and abs(_r_open - _RATIO_REQ) / _RATIO_REQ < 0.02,
          'shown=%s ratio=%.4f req=%.4f'
          % ([round(v, 3) for v in _shown], _r_open, _RATIO_REQ))
    check('t19①b 缩到屏内（画布 ≤ 可用区 95%）',
          _shown[0] * _f19a.dpi <= _AVAIL[0] * 0.95 + 1e-6
          and _shown[1] * _f19a.dpi <= _AVAIL[1] * 0.95 + 1e-6,
          'px=%s avail=%s' % ([round(_shown[0] * _f19a.dpi),
                               round(_shown[1] * _f19a.dpi)], list(_AVAIL)))
    check('t19①c _figsize_open 记的是等比尺寸（不再是钳制后的变形值）',
          bool(_tw19a._figsize_open)
          and abs(_tw19a._figsize_open[0] / _tw19a._figsize_open[1]
                  - _RATIO_REQ) / _RATIO_REQ < 0.02,
          str([round(v, 3) for v in (_tw19a._figsize_open or [])]))
    plt.close(_f19a)

    # ② 窗口管理器**仍然**钳制（画布 1601×496）→ 守卫必须恢复等比，且画布回屏内
    _f19b = _oversized_fig()
    _w19b = _FakeWin(_f19b, clamp_px=_CLAMP_PX)
    _f19b.canvas.manager.window = _w19b
    _tw19b = Tweak(_f19b, export_path=os.path.join(
        tempfile.gettempdir(), 'test_oversize_b.json'), heavy=False)
    _r_cl = _f19b.get_size_inches()
    check('t19② 窗口仍把画布钳制成 1601x496（3.234）时，守卫恢复等比（偏离 <2%）',
          abs(_r_cl[0] / _r_cl[1] - _RATIO_REQ) / _RATIO_REQ < 0.02,
          'after-clamp=%s ratio=%.4f'
          % ([round(v, 3) for v in _r_cl], _r_cl[0] / _r_cl[1]))
    check('t19②b 钳制守卫确实触发过（提示只打一次）+ 恢复后画布回到屏内',
          _tw19b._warned_offscreen is True
          and _r_cl[0] * _f19b.dpi <= _AVAIL[0] * 0.95 + 1e-6
          and _r_cl[1] * _f19b.dpi <= _AVAIL[1] * 0.95 + 1e-6,
          'warned=%s px=%s' % (_tw19b._warned_offscreen,
                               [round(_r_cl[0] * _f19b.dpi),
                                round(_r_cl[1] * _f19b.dpi)]))
    plt.close(_f19b)
finally:
    Tweak._screen_available = _avail_real

# ---- 30. t20：Tk 回退路径的单位/时序缺陷（屏幕物理 px vs 画布逻辑 px + dpr 两态）----
# t16 实测（--backend tk --scenario huge，30×22in@dpi100）：
#   日志「画布 3000x2200 超出屏幕可用区 3840x2160，已自动缩放到屏内 (93%)」
#   → 映射后客户区 5596×2119 物理 px（> 屏 3840×2160）→ WM 钳制 →
#   最终 figsize 27.98×10.275in（长宽比 2.723 vs 请求 1.3636，偏 +99.7%）且**永久固化**
#   （全程无 _CLAMP_HINT，说明钳制守卫根本没触发）。
# 根因：① Tk 的 winfo_screen* 是**物理** px（逻辑屏其实 1920×1080），而引擎其余部分
#      （fig.canvas.get_width_height() / Tk 的 <Configure> 客户区）用**逻辑** px；
#      ② device_pixel_ratio 未映射时是 1.0（matplotlib 在 <Map> 才更新它）；
#      ③ 钳制判据把逻辑画布比物理屏幕 -> 条件恒假 -> 永不恢复。
# 本用例仍用**假 window**（Agg，绝不开窗）复现这条时序，不依赖真开窗。
_TK_PHYS = (3840, 2160)           # Tk 报的物理屏（= 本机 200% 缩放下的真分辨率）
_TK_DPR = 2.0                     # 映射后 matplotlib 读出的 device_pixel_ratio
_TK_LOGICAL = (1920, 1080)        # 折算后的逻辑屏（= 引擎与画布的口径）
_TK_CLAMP_PHYS = (5596, 2055)     # t16 实测的"钳制后客户区"（**物理** px，见下）


def _set_dpr(fig, ratio):
    '''设置画布 dpr：matplotlib 只提供 `_set_device_pixel_ratio()`
    （`device_pixel_ratio` 是只读 property；Tk 后端 :272 / Qt :249 都走它）。
    注意：它同时会把 `fig.dpi` 重设为 ``ratio × _original_dpi``
    （backend_bases.py:1941-1943）—— 这正是"物理 dpi"，测试里必须按它换算。
    '''
    fig.canvas._set_device_pixel_ratio(ratio)


class _FakeTkWin:
    '''假 Tk 窗口：winfo_screen* 报**物理** px；show() 模拟 <Map>（dpr 变真实值）。

    说明：`_screen_available()` 会先试 Qt 分支 —— 本类**没有** `screen()`，
    而 Agg 进程里没有 QApplication，所以自然落到 Tk 分支（= 真实 Tk 场景）。
    '''

    def __init__(self, fig, dpr_before=1.0, dpr_after=_TK_DPR, clamp_phys=None):
        self.fig = fig
        self.dpr_after = dpr_after
        self.clamp_phys = clamp_phys       # 模拟 WM 钳制后的客户区（**物理** px）
        self.before_show = None            # 映射前的 figsize（开窗瞬间）
        self.clamped_to = None             # 被钳制后的 figsize（修正前的中间态）
        _set_dpr(fig, dpr_before)
        fig.canvas.manager.window = self

    def winfo_screenwidth(self):
        return _TK_PHYS[0]

    def winfo_screenheight(self):
        return _TK_PHYS[1]

    def show(self):
        self.before_show = tuple(self.fig.get_size_inches())
        _set_dpr(self.fig, self.dpr_after)                       # <Map>
        if self.clamp_phys is not None:
            # backend_tk.py:279-286 的等价物：resize() 拿 <Configure> 的客户区像素
            # （Tk 这边是**物理** px，widget 按 physical 尺寸建）除以 fig.dpi。
            _dpi = float(self.fig.dpi)
            self.fig.set_size_inches(self.clamp_phys[0] / _dpi,
                                     self.clamp_phys[1] / _dpi)
            self.clamped_to = tuple(self.fig.get_size_inches())
        self.fig.canvas.callbacks.process('resize_event', None)   # <Configure>

    def raise_(self):
        pass


def _huge_tk_fig(dpi=100.0):
    _f = plt.figure(figsize=(_REQ_W, _REQ_H))        # 30×22in（长宽比 1.3636）
    _f.set_dpi(dpi)
    _f.add_axes([0.1, 0.1, 0.8, 0.8]).plot([0, 1], [0, 1])
    _f.canvas.draw()
    return _f


def _ratio_dev(fig):
    _w, _h = fig.get_size_inches()
    return abs((_w / _h) - _RATIO_REQ) / _RATIO_REQ


def _dev_of(ratio):
    return abs(ratio - _RATIO_REQ) / _RATIO_REQ


# ① 映射前：只能按 dpr=1.0 折算（= 把物理屏当逻辑屏）→ 目标偏大（t16 实测 93%）
_f20a = _huge_tk_fig()
_w20a = _FakeTkWin(_f20a)                            # dpr 1.0 → 2.0，不钳制
_tw20a = Tweak(_f20a, export_path=os.path.join(
    tempfile.gettempdir(), 'test_tk_huge_a.json'), heavy=False)
check('t20① 映射前按 dpr=1.0 折算（目标=27.98×20.52，与 t16 trace 一致；长宽比守恒）',
      _w20a.before_show is not None
      and abs(_w20a.before_show[0] - 27.9818) < 0.01
      and abs(_w20a.before_show[1] - 20.52) < 0.01
      and _dev_of(_w20a.before_show[0] / _w20a.before_show[1]) < 0.02,
      'before_show=%s' % ([round(v, 3) for v in (_w20a.before_show or ())],))

# ② 映射后（dpr=2.0）：dpr 自检必须**用正确口径重算等比目标** → 真的缩进屏内
_fin20a = _f20a.get_size_inches()
# 口径：引擎用的是 canvas.get_width_height()（**逻辑** px = 英寸×dpi÷dpr，
# backend_bases.py:1967），所以这里直接用同一个 API，别自己乘 dpi。
_px20a = _f20a.canvas.get_width_height()
check('t20② 映射后自检修正：最终 figsize≈13.99×10.26、长宽比偏差 ≤2%',
      abs(_fin20a[0] - 13.9916) < 0.05 and abs(_fin20a[1] - 10.2605) < 0.05
      and _ratio_dev(_f20a) < 0.02,
      'final=%s ratio=%.4f dev=%.4f'
      % ([round(v, 3) for v in _fin20a], _fin20a[0] / _fin20a[1], _ratio_dev(_f20a)))
check('t20②b 画布（逻辑 px）真的落在逻辑屏内（≤ 逻辑屏 95%）+ _figsize_open 同步',
      _px20a[0] <= _TK_LOGICAL[0] * 0.95 + 1e-6
      and _px20a[1] <= _TK_LOGICAL[1] * 0.95 + 1e-6
      and abs(_tw20a._figsize_open[0] - _fin20a[0]) < 1e-6
      and abs(_tw20a._fit_dpr - _TK_DPR) < 1e-9,
      'px=%s logical=%s open=%s dpr=%s'
      % ([round(v) for v in _px20a], list(_TK_LOGICAL),
         [round(v, 3) for v in _tw20a._figsize_open], _tw20a._fit_dpr))
plt.close(_f20a)

# ③ 钳制守卫在 Tk 上必须能触发（旧代码因"逻辑画布 vs 物理屏幕"混单位而恒假）
_f20b = _huge_tk_fig()
_w20b = _FakeTkWin(_f20b, dpr_before=_TK_DPR, clamp_phys=_TK_CLAMP_PHYS)
_tw20b = Tweak(_f20b, export_path=os.path.join(
    tempfile.gettempdir(), 'test_tk_huge_b.json'), heavy=False)
check('t20③ Tk 上钳制守卫会触发（_warned_offscreen=True）且恢复等比（偏差 ≤2%）',
      _tw20b._warned_offscreen is True
      and _w20b.clamped_to is not None
      and _dev_of(_w20b.clamped_to[0] / _w20b.clamped_to[1]) > 0.5   # 先真的被改形
      and _ratio_dev(_f20b) < 0.02,
      'clamped=%s final=%s warned=%s'
      % ([round(v, 3) for v in (_w20b.clamped_to or ())],
         [round(v, 3) for v in _f20b.get_size_inches()], _tw20b._warned_offscreen))
plt.close(_f20b)

# ④ 映射瞬间被钳制 + dpr 同时变化：自检要能一次纠回（复现 t16 的 2.723 中间态）
_f20c = _huge_tk_fig()
_w20c = _FakeTkWin(_f20c, dpr_before=1.0, clamp_phys=_TK_CLAMP_PHYS)
_tw20c = Tweak(_f20c, export_path=os.path.join(
    tempfile.gettempdir(), 'test_tk_huge_c.json'), heavy=False)
_cl = _w20c.clamped_to or (0.0, 1.0)
check('t20④ 映射即被钳制：中间态复现 t16 的 27.98×10.27（比值 2.723）→ 修正到 ≤2%',
      abs(_cl[0] - 27.98) < 0.05 and abs(_cl[1] - 10.275) < 0.02
      and _dev_of(_cl[0] / _cl[1]) > 0.9
      and _ratio_dev(_f20c) < 0.02,
      'intermediate=%s ratio=%.4f | final=%s dev=%.4f'
      % ([round(v, 3) for v in _cl], _cl[0] / _cl[1],
         [round(v, 3) for v in _f20c.get_size_inches()], _ratio_dev(_f20c)))
plt.close(_f20c)


# ⑤ 不得回归 Qt：availableGeometry() 已是逻辑 px → **不折算**
class _FakeQtScr:
    def availableGeometry(self):
        class _AG:
            def width(self):
                return _TK_LOGICAL[0]

            def height(self):
                return _TK_LOGICAL[1]
        return _AG()


class _FakeQtWin:
    def screen(self):
        return _FakeQtScr()


_f20d = _huge_tk_fig()
_set_dpr(_f20d, _TK_DPR)                               # Qt 上 dpr 也可能是 2.0
_f20d.canvas.manager.window = _FakeQtWin()
_tw20d = Tweak(_f20d, export_path=os.path.join(
    tempfile.gettempdir(), 'test_tk_huge_d.json'), heavy=False)
_av20d = _tw20d._screen_available()
check('t20⑤ Qt 分支不折算（要么 (1920,1080)，要么本机无 Qt → None；绝不能是 960×540）',
      _av20d in (None, _TK_LOGICAL), 'avail=%s' % (_av20d,))
try:
    from matplotlib.backends import qt_compat as _qc      # noqa: F401
    _has_qt = True
except Exception:                                          # noqa: BLE001
    _has_qt = False
if _has_qt:
    check('t20⑤b 本机有 Qt binding → 走 availableGeometry 分支且原样返回（不除 dpr）',
          _av20d == _TK_LOGICAL, str(_av20d))
plt.close(_f20d)

# ---- 31. t24：带约束的吸附 —— 吸附不得把已夹到 MIN_SIZE 的边拉过下限（面板塌成一条线）----
# 真缺陷（t21 的 12,000 次离屏矩阵抓到，确定性种子；图 11 = tight_layout 2×2）：
#   press(673,191) → motion(-1541,842) → release(1071,550)，高度 0.45094 → 0.01411（223.7→7.0px）。
# 最小复现（2 面板，3 事件）：A 的 y1=0.70，B 的中线 y=0.6841；按 A 的下边沿往下拖出画布外松手。
# 机理：`_resize_frac` 先把边夹到 h=MIN_SIZE(0.02)，随后 `_snap_resize` 又把它吸到 B 的中线
#   → h = 0.70 - 0.6841 = 0.0159 < MIN_SIZE -> A 只剩 ~9.5px（本机 800px 画布）。
# 修法：`_snap_resize(..., min_size=...)` **过滤掉会造成越界的候选线**（宁可不吸）+ 出口保险。
_fig31 = plt.figure(figsize=(8, 6))
_A31 = _fig31.add_axes([0.10, 0.30, 0.40, 0.40])          # y1 = 0.70
_B31 = _fig31.add_axes([0.60, 0.6041, 0.30, 0.16])        # B 中线 y = 0.6841
_fig31.canvas.draw()
_tw31 = Tweak(_fig31, export_path=os.path.join(
    tempfile.gettempdir(), 'test_snap_min_size.json'), heavy=False)
_X31, _Y31 = _fig31.bbox.width, _fig31.bbox.height
_b31 = _bbox(_A31)
from matplotlib.backend_bases import MouseEvent as _ME31     # noqa: E402
_cx31 = (_b31[0] + _b31[2] / 2) * _X31
_ev31 = _ME31('button_press_event', _fig31.canvas, _cx31, (_b31[1] + 0.006) * _Y31,
              button=1)
_ev31.inaxes = _A31
_tw31._on_press(_ev31)
_ev31 = _ME31('motion_notify_event', _fig31.canvas, _cx31, 1.7 * _Y31)
_ev31.inaxes = _A31
_tw31._on_motion(_ev31)
_ev31 = _ME31('button_release_event', _fig31.canvas, _cx31, 1.7 * _Y31, button=1)
_tw31._on_release(_ev31)
_h31 = _bbox(_A31)[3]
check('t24① 往下拖出画布外松手：A 高度 ≥ MIN_SIZE（吸附不得拉过下限）',
      _h31 >= MIN_SIZE - 1e-9,
      'h=%.5f (%.1fpx) MIN_SIZE=%.3f' % (_h31, _h31 * _Y31, MIN_SIZE))
# ② 单测：会造成越界的候选线必须被**跳过**（0.6800 距 B 中线 0.6841 仅 0.0041 < tol，
#    但吸过去会让 h=0.0159 < MIN_SIZE -> 宁可不吸）
_b31b, _gx31b, _gy31b = _snap_resize(
    [0.10, 0.6800, 0.50, 0.70], 'bottom', [[0.60, 0.6841, 0.90, 0.8441]],
    min_size=MIN_SIZE)
check('t24② 越界候选线被跳过（宁可不吸）：y0 保持 0.6800、h=0.02 ≥ MIN_SIZE',
      abs(_b31b[1] - 0.6800) < 1e-9 and (_b31b[3] - _b31b[1]) >= MIN_SIZE - 1e-9,
      'b=%s h=%.5f' % (_b31b, _b31b[3] - _b31b[1]))
# ③ 不回归吸附本身：合法候选线（吸过去仍 ≥ MIN_SIZE）照样吸得上
_b31c, _gx31c, _gy31c = _snap_resize(
    [0.10, 0.6000, 0.50, 0.70], 'bottom', [[0.60, 0.6041, 0.90, 0.7441]],
    min_size=MIN_SIZE)
check('t24③ 合法候选线仍吸附（0.6000 → 0.6041，tol=0.006 内）',
      abs(_b31c[1] - 0.6041) < 1e-9 and _gy31c == [0.6041],
      'b=%s gy=%s' % (_b31c, _gy31c))
# ④ 向后兼容：不传 min_size 时行为与历史版本一致（既有调用方/用例不受影响）
_b31d, _gx31d, _gy31d = _snap_resize(
    [0.10, 0.6800, 0.50, 0.70], 'bottom', [[0.60, 0.6841, 0.90, 0.8441]])
check('t24④ min_size 缺省=None 时行为不变（仍会吸到 0.6841，供旧调用方）',
      abs(_b31d[1] - 0.6841) < 1e-9, str(_b31d))
# ⑤ 画布边界候选线同样受约束（0 这条线也能把面板压到下限以下）
_b31e, _gx31e, _gy31e = _snap_resize([0.10, 0.9850, 0.50, 1.0], 'top',
                                    [], min_size=MIN_SIZE)
check('t24⑤ 边界线上边吸附仍受 min_size 约束（吸到 1.0 后 h ≥ MIN_SIZE）',
      (_b31e[3] - _b31e[1]) >= MIN_SIZE - 1e-9, 'b=%s h=%.5f' % (_b31e, _b31e[3] - _b31e[1]))
plt.close(_fig31)

# ---- 32. ★25.3 / t101：sticky drag 自愈（外部程序抢走鼠标 → button_release 丢了）----
# 真机（用户 2026-10-14）：按住子图拖动 → 按 QQ 截图 → 框选 → 松开键，蓝框仍跟着鼠标。
# 根因是 QQ 截图**接管鼠标**，release 事件根本没送到画布 -> 工具一直以为"还在拖"。
# 修法：motion 报「没有任何键按着」**且**「本次手势见过按键」-> 当次 motion 内收尾（走
# 既有 `_on_release`）；另加 `figure_leave_event` 兜底（离开画布 + 确证没键按着才收尾）。
# 判据带**能力闸门**：后端没报过"有键按着"时一律不启用 —— 否则**离屏合成事件**
# （不传 buttons -> 恒为空）会把"按下 → motion → 松开"整片误判成"拖完了"（见 ③）。
import mpltweak.toolbox as _tb32
from matplotlib.backend_bases import LocationEvent as _LE32


def _t101_mk(tag32):
    '小夹具：单面板图 + Tweak（重图关掉，落位当场生效，和 §28 同口径）。'
    fig = plt.figure(figsize=(9, 6))
    ax = fig.add_axes([0.20, 0.25, 0.40, 0.45])
    ax.plot([0, 1], [0, 1])
    fig.canvas.draw()
    tw = Tweak(fig, export_path=os.path.join(
        tempfile.gettempdir(), tag32 + '.json'), heavy=False)
    X, Y = fig.bbox.width, fig.bbox.height
    b = _bbox(ax)
    return fig, ax, tw, (int(round((b[0] + b[2] / 2) * X)),
                         int(round((b[1] + b[3] / 2) * Y)))


def _t101_fire(tw32, name, x, y, button=None, buttons=None, ax=None):
    '发一个事件进对应 handler；motion/press 显式给 inaxes（离屏拿不到自动命中）。'
    canvas = tw32.fig.canvas
    if name in ('figure_leave_event', 'figure_enter_event'):
        ev = _LE32(name, canvas, int(x), int(y))
    elif name == 'key_press_event':
        raise ValueError('本段不发键盘事件')
    else:
        # mpl 3.9.x 的 MouseEvent.__init__ 没有 buttons 形参（3.10 才有），
        # 先按老签名构造、再补属性 —— 两版语义一致（3.10 里它也是普通属性）。
        ev = _ME(name, canvas, int(x), int(y), button=button)
        if buttons is not None:
            ev.buttons = buttons
        if ax is not None:
            ev.inaxes = ax
    if name in ('button_press_event', 'button_release_event'):
        tw32._on_press(ev) if name == 'button_press_event' else tw32._on_release(ev)
    elif name == 'motion_notify_event':
        tw32._on_motion(ev)
    else:
        tw32._on_figure_leave(ev)
    return ev


_FIG32, _AX32, _TW32, (_PX32, _PY32) = _t101_mk('test_t101_heal')
_B32_0 = _bbox(_AX32)
_t101_fire(_TW32, 'button_press_event', _PX32, _PY32, button=1, ax=_AX32)
check('t101① 按下 -> 进入拖动，且记下"见过按键"',
      bool(_TW32._drag) and _TW32._btn_seen and not _TW32._btn_capable,
      'drag=%s seen=%s capable=%s' % (_TW32._drag, _TW32._btn_seen,
                                      _TW32._btn_capable))
# 真机形态：拖拽中的 motion 报 buttons = frozenset({左键})
_t101_fire(_TW32, 'motion_notify_event', _PX32 + 40, _PY32 + 20,
           buttons=frozenset({1}), ax=_AX32)
_B32_1 = _bbox(_AX32)
check('t101② 带按键的 motion 照常跟手（判据不许提前收尾；能力闸门打开）',
      bool(_TW32._drag) and _bbox(_AX32) != _B32_0 and _TW32._btn_capable,
      'b0=%s b1=%s capable=%s' % ([round(v, 4) for v in _B32_0],
                                  [round(v, 4) for v in _B32_1],
                                  _TW32._btn_capable))
# 外部程序抢走鼠标：下一个 motion 的 buttons 为空 -> 当次 motion 内收尾
_t101_fire(_TW32, 'motion_notify_event', _PX32 + 80, _PY32 + 40, ax=_AX32)
check('t101③ motion 报"没有键按着" -> 当次 motion 内拖动已结束（不等 release）',
      not _TW32._drag and _TW32._ax is None and not _TW32._drag_active(),
      'drag=%s ax=%s' % (_TW32._drag, _TW32._ax))
check('t101④ 面板落在"键丢失那一下"之前的位置（蓝框不再跟随）',
      _bbox(_AX32) == _B32_1,
      'now=%s' % ([round(v, 4) for v in _bbox(_AX32)],))
_t101_fire(_TW32, 'motion_notify_event', _PX32 + 200, _PY32 + 120, ax=_AX32)
check('t101⑤ 收尾后继续移动，面板一个像素都不动',
      _bbox(_AX32) == _B32_1, 'now=%s' % ([round(v, 4) for v in _bbox(_AX32)],))
plt.close(_FIG32)

# ③ 负例（守整个仓库的历史形态）：按下 + motion **不表达按键** -> 不许自愈
_FIG32b, _AX32b, _TW32b, (_PX32b, _PY32b) = _t101_mk('test_t101_legacy')
_B32b_0 = _bbox(_AX32b)
_t101_fire(_TW32b, 'button_press_event', _PX32b, _PY32b, button=1, ax=_AX32b)
_t101_fire(_TW32b, 'motion_notify_event', _PX32b + 40, _PY32b + 20, ax=_AX32b)
check('t101⑥ 老形态 motion（不带 buttons）-> 拖动照旧（离屏合成事件不许误触发）',
      bool(_TW32b._drag) and _bbox(_AX32b) != _B32b_0 and not _TW32b._btn_capable,
      'drag=%s capable=%s' % (_TW32b._drag, _TW32b._btn_capable))
_t101_fire(_TW32b, 'button_release_event', _PX32b + 40, _PY32b + 20, button=1)
check('t101⑦ 真松手照旧收尾（自愈没抢掉正常路径）',
      not _TW32b._drag and not _TW32b._btn_seen, 'drag=%s' % _TW32b._drag)
plt.close(_FIG32b)

# ④ 能力探针：离屏 Agg 问不出全局按键状态 -> None（判不了就不许动）
_FIG32c, _AX32c, _TW32c, _ = _t101_mk('test_t101_probe')
check('t101⑧ 离屏 Agg 画布 `_mouse_buttons_down` 返回 None（不瞎猜）',
      _tb32._mouse_buttons_down(_FIG32c.canvas) is None,
      repr(_tb32._mouse_buttons_down(_FIG32c.canvas)))
plt.close(_FIG32c)

# ⑤ leave 兜底三态：确证没键按着才收尾；还按着 / 问不出来都**不许打断**
_orig_mbd32 = _tb32._mouse_buttons_down
try:
    for _val32, _want32 in ((False, False), (True, True), (None, True)):
        _figE, _axE, _twE, (_pxE, _pyE) = _t101_mk('test_t101_leave')
        _t101_fire(_twE, 'button_press_event', _pxE, _pyE, button=1, ax=_axE)
        _t101_fire(_twE, 'motion_notify_event', _pxE + 30, _pyE + 15,
                   buttons=frozenset({1}), ax=_axE)
        _tb32._mouse_buttons_down = (lambda canvas, _v=_val32: _v)
        _t101_fire(_twE, 'figure_leave_event', _pxE + 30, _pyE + 15)
        check('t101⑨ 离画布兜底（查询=%r）-> %s' % (_val32, '仍挂着拖动（不打断）'
                                                    if _want32 else '收尾'),
              bool(_twE._drag) is _want32, 'drag=%s' % _twE._drag)
        plt.close(_figE)
finally:
    _tb32._mouse_buttons_down = _orig_mbd32

# ⑥ teeth：把自愈分支关掉 -> 上面 ③ 的断言必红（这里直接验"它确实没在自愈"）
_orig_heal32 = Tweak._self_heal_drag
Tweak._self_heal_drag = lambda self, event: False
try:
    _figT, _axT, _twT, (_pxT, _pyT) = _t101_mk('test_t101_teeth')
    _t101_fire(_twT, 'button_press_event', _pxT, _pyT, button=1, ax=_axT)
    _t101_fire(_twT, 'motion_notify_event', _pxT + 40, _pyT + 20,
               buttons=frozenset({1}), ax=_axT)
    _t101_fire(_twT, 'motion_notify_event', _pxT + 80, _pyT + 40, ax=_axT)
    _still32 = bool(_twT._drag)
    plt.close(_figT)
finally:
    Tweak._self_heal_drag = _orig_heal32
check('t101⑩【有牙】关掉自愈分支 -> "当次 motion 内结束"必红（这里仍挂着拖动）',
      _still32 is True, 'drag=%s' % _still32)

print('----')
print('FAILED: %d' % fail if fail else 'ALL PASS')


def test_tweak_suite():
    """pytest 入口：本文件是**线性脚本式套件**（全部断言在模块级执行）。

    为什么需要这一段（0.1.12 / t10）：``python -m pytest -q tests/test_tweak.py``
    原先**必然 INTERNALERROR**（rc=3）—— 模块级 ``sys.exit(0)`` 在 import 期把 pytest
    进程带走，于是 ``tools/ci_pytest_summary.py`` 那条「全量 pytest」CI 腿只要收集到
    本文件就整体崩。现在把收尾的 ``sys.exit`` 收进 ``if __name__ == '__main__':``，
    并补这个入口断言结论（模块级已经跑完全部检查，这里只复述 ``fail`` 的最终值）。

    两种调用方式结论一致：
      * ``python tests/test_tweak.py`` → 打印 ``ALL PASS`` / rc=0（逐条 PASS 行不变）；
      * ``python -m pytest -q tests/test_tweak.py`` → 1 passed（收集期跑正文，测试期断言）。
    """
    assert fail == 0, 'test_tweak 有 %d 条 FAIL（见收集期的详细输出）' % fail


if __name__ == '__main__':
    sys.exit(1 if fail else 0)
