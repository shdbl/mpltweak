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
check('撤销可视化：操作名提到移动', '移动' in _nm21, repr(_nm21))
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
twS._push_undo(); twS._clim_ax = cbS.ax; twS._clim_side = 'vmin'; twS._clim0 = _oldS
_twbb = cbS.ax.get_position().transformed(figS.transFigure)
twS._clim_box_px = _twbb.bounds[:2] + (_twbb.x1, _twbb.y1)
twS._clim_orientation = 'horizontal'; twS._drag_cb_clim = True
twS._update_cb_clim_drag(_exS - 50, _eyS); twS._finish_cb_clim_drag()
check('clim 拖拽已移除（Alt+拖端点功能删）', _clim_of(cbS.ax) == _oldS,
      str(_clim_of(cbS.ax)))
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

print('----')
print('FAILED: %d' % fail if fail else 'ALL PASS')
sys.exit(1 if fail else 0)
