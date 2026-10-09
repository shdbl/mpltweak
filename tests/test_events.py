# -*- coding: utf-8 -*-
# 合成事件测试：手动构造 MouseEvent/KeyEvent 喂给 canvas.callbacks，
# 与 mpl_connect 真实事件路径一致（不依赖 GUI 后端）。
import os
import sys
import json
import tempfile

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.backend_bases import MouseEvent, KeyEvent

from mpltweak.toolbox import (Tweak, _bbox, _snap_box, LEGEND_LOCS, _set_bbox,
                           MIN_SIZE, _clim_of)

fail = 0

def check(name, cond, detail=''):
    global fail
    print(('PASS' if cond else 'FAIL') + ' | ' + name + ((' | ' + detail) if detail else ''))
    if not cond:
        fail += 1

def send(canvas, name, x=None, y=None, button=None, key=None):
    '''构造事件对象并走回调管线。KeyEvent 必须显式传 x/y（默认 0,0 会算到左下角）。'''
    if name == 'key_press_event':
        ev = KeyEvent('key_press_event', canvas, key, x, y)
    else:
        ev = MouseEvent(name, canvas, x, y, button=button, key=key)
    canvas.callbacks.process(name, ev)
    return ev

# ---- 建 2x2 面板（同 demo） ----
fig = plt.figure(figsize=(10, 8))
pos = [[0.08, 0.55, 0.40, 0.38], [0.55, 0.55, 0.40, 0.38],
       [0.08, 0.08, 0.40, 0.38], [0.55, 0.08, 0.40, 0.38]]
axes = [fig.add_axes(p) for p in pos]
for k, ax in enumerate(axes):
    ax.scatter([0.1, 0.2], [0.1, 0.2], label='s%d' % k)
    ax.set_title('panel %d' % k, fontsize=10)
axes[0].legend(loc='upper right', fontsize=8)
fig.canvas.draw()

tw = Tweak(fig, export_path=os.path.join(
    tempfile.gettempdir(), 'test_events.json'), heavy=False)

W, H = fig.canvas.get_width_height()

def pxf(ax, fx, fy):
    '''轴内 figure 比例坐标 -> 画布像素（matplotlib 显示坐标，原点左下）。'''
    x = ax._position.x0 + ax._position.width * fx
    y = ax._position.y0 + ax._position.height * fy
    return x * W, y * H

# ---- 1. 键盘：悬停标题按 + 调字号（l 键图例循环已按用户要求移除） ----
tt = axes[0].title.get_window_extent()
tx, ty = (tt.x0 + tt.x1) / 2, (tt.y0 + tt.y1) / 2
send(fig.canvas, 'key_press_event', tx, ty, key='+')
check('key + 标题字号 +1', axes[0].title.get_fontsize() == 11,
      'fs=%s' % axes[0].title.get_fontsize())

# 'l' 已移除：按下不应再改图例位置
cx, cy = pxf(axes[0], 0.5, 0.5)
_loc_before = tw.leg_state[id(axes[0])]['loc']
send(fig.canvas, 'key_press_event', cx, cy, key='l')
check('key l 已移除（图例位置不变）',
      tw.leg_state[id(axes[0])]['loc'] == _loc_before,
      str(tw.leg_state[id(axes[0])]['loc']))

# ---- 2. 拖拽平移 ax1（heavy=False 实时模式） ----
b0 = list(_bbox(axes[1]))
x0, y0 = pxf(axes[1], 0.5, 0.5)
dx_px, dy_px = 0.05 * W, 0.03 * H
send(fig.canvas, 'button_press_event', x0, y0, button=1)
send(fig.canvas, 'motion_notify_event', x0 + dx_px, y0 + dy_px)
send(fig.canvas, 'button_release_event', x0 + dx_px, y0 + dy_px, button=1)
b1 = _bbox(axes[1])
check('拖拽平移 ax1', abs(b1[0] - b0[0] - 0.05) < 1e-6 and abs(b1[1] - b0[1] - 0.03) < 1e-6,
      '%s -> %s' % ([round(v, 4) for v in b0], [round(v, 4) for v in b1]))

# ---- 3. 自动吸附：ax2 左缘拖近 0.55（ax1 左缘），差 0.005<容差 -> 吸附 ----
b2_0 = list(_bbox(axes[2]))
tx0, ty0 = pxf(axes[2], 0.5, 0.5)
delta = (0.545 - b2_0[0]) * W
send(fig.canvas, 'button_press_event', tx0, ty0, button=1)
send(fig.canvas, 'motion_notify_event', tx0 + delta, ty0)
send(fig.canvas, 'button_release_event', tx0 + delta, ty0, button=1)
b2_1 = _bbox(axes[2])
check('拖拽吸附 ax2 左缘到 0.55', abs(b2_1[0] - 0.55) < 1e-6,
      'x0=%.4f' % b2_1[0])

# ---- 4. heavy 模式拖拽（ghost+blit 路径，验证 fallback 不炸） ----
tw.heavy = True
b0 = list(_bbox(axes[3]))
x0, y0 = pxf(axes[3], 0.5, 0.5)
send(fig.canvas, 'button_press_event', x0, y0, button=1)
send(fig.canvas, 'motion_notify_event', x0 + 0.06 * W, y0 - 0.02 * H)
send(fig.canvas, 'button_release_event', x0 + 0.06 * W, y0 - 0.02 * H, button=1)
b1 = _bbox(axes[3])
check('heavy 拖拽生效', abs(b1[0] - b0[0] - 0.06) < 1e-6 and abs(b1[1] - b0[1] + 0.02) < 1e-6,
      '%s -> %s' % ([round(v, 4) for v in b0], [round(v, 4) for v in b1]))

# ---- 5. 导出 ----
tw.export()
out = os.path.join(tempfile.gettempdir(), 'test_events.json')
check('导出成功', os.path.exists(out))

# ---- 6. 画布边缘吸附（0/1）----
b = _snap_box([0.004, 0.1, 0.3, 0.2], [])
check('吸附画布左缘', abs(b[0][0]) < 1e-12, str(b[0]))
b = _snap_box([0.6, 0.1, 0.396, 0.2], [])
check('吸附画布右缘', abs(b[0][0] + b[0][2] - 1.0) < 1e-12, str(b[0]))

# ---- 7. Shift/Ctrl 点选 + 多选拖拽 ----
tw._selected.clear()                 # 前面单击测试会留下单选，先清空
tw._rebuild_selection_artists()
cx0, cy0 = pxf(axes[0], 0.5, 0.5)
cx1, cy1 = pxf(axes[1], 0.5, 0.5)
send(fig.canvas, 'button_press_event', cx0, cy0, button=1, key='shift')
send(fig.canvas, 'button_release_event', cx0, cy0, button=1)
check('shift 选中 ax0', tw._selected == {id(axes[0])}, str(len(tw._selected)))
send(fig.canvas, 'button_press_event', cx1, cy1, button=1, key='shift')
send(fig.canvas, 'button_release_event', cx1, cy1, button=1)
check('shift 加选 ax1', tw._selected == {id(axes[0]), id(axes[1])},
      str(len(tw._selected)))
b0_0 = list(_bbox(axes[0]))
b1_0 = list(_bbox(axes[1]))
send(fig.canvas, 'button_press_event', cx0, cy0, button=1)
send(fig.canvas, 'motion_notify_event', cx0 + 0.04 * W, cy0 + 0.02 * H)
send(fig.canvas, 'button_release_event', cx0 + 0.04 * W, cy0 + 0.02 * H, button=1)
check('多选整体移动', abs(_bbox(axes[0])[0] - b0_0[0] - 0.04) < 1e-6
      and abs(_bbox(axes[1])[0] - b1_0[0] - 0.04) < 1e-6,
      '%s | %s' % ([round(v, 4) for v in _bbox(axes[0])],
                   [round(v, 4) for v in _bbox(axes[1])]))

# ---- 8. 橡皮筋框选 ----
send(fig.canvas, 'button_press_event', 0.02 * W, 0.02 * H, button=1)
send(fig.canvas, 'button_release_event', 0.02 * W, 0.02 * H, button=1)
check('点击空白清空选中', len(tw._selected) == 0, str(len(tw._selected)))
# 摆成确定位置再框选（前面若干拖拽已改变面板位置）
_set_bbox(axes[2], [0.06, 0.06, 0.30, 0.30])
_set_bbox(axes[3], [0.55, 0.06, 0.30, 0.30])
fig.canvas.draw()
send(fig.canvas, 'button_press_event', 0.03 * W, 0.03 * H, button=1)
send(fig.canvas, 'motion_notify_event', 0.97 * W, 0.45 * H)
send(fig.canvas, 'button_release_event', 0.97 * W, 0.45 * H, button=1)
check('框选下半面板', tw._selected == {id(axes[2]), id(axes[3])},
      'sel=%d' % len(tw._selected))
# 相交但未完全框住 -> 不选中
send(fig.canvas, 'button_press_event', 0.03 * W, 0.03 * H, button=1)
send(fig.canvas, 'motion_notify_event', 0.60 * W, 0.30 * H)
send(fig.canvas, 'button_release_event', 0.60 * W, 0.30 * H, button=1)
check('框选需完全框住（相交不算）', len(tw._selected) == 0,
      'sel=%d' % len(tw._selected))

# ---- 9. Undo / Redo ----
tw._undo_once()
check('undo 恢复移动前', abs(_bbox(axes[0])[0] - b0_0[0]) < 1e-6,
      str([round(v, 4) for v in _bbox(axes[0])]))
tw._redo_once()
check('redo 重做移动', abs(_bbox(axes[0])[0] - b0_0[0] - 0.04) < 1e-6,
      str([round(v, 4) for v in _bbox(axes[0])]))

# ---- 10. Ctrl+点击多选 / 单击单选显示蓝框 ----
tw._selected.clear()
tw._rebuild_selection_artists()
cx0, cy0 = pxf(axes[0], 0.5, 0.5)
send(fig.canvas, 'button_press_event', cx0, cy0, button=1, key='control')
send(fig.canvas, 'button_release_event', cx0, cy0, button=1)
check('Ctrl 加选 ax0', tw._selected == {id(axes[0])}, str(len(tw._selected)))
cx3, cy3 = pxf(axes[3], 0.5, 0.5)
send(fig.canvas, 'button_press_event', cx3, cy3, button=1)
check('单击单选显蓝框', tw._selected == {id(axes[3])}, str(tw._selected))
check('蓝框 artist 已生成', len(tw._sel_artists) == 1, str(len(tw._sel_artists)))
send(fig.canvas, 'button_release_event', cx3, cy3, button=1)

# ---- 11. 图例拖拽（matplotlib 3.10 必须用 bbox_transform；预览 + 松手吸附 8 位）----
leg = axes[0].get_legend()
if leg is not None:
    we = leg.get_window_extent()
    lx, ly = (we.x0 + we.x1) / 2, (we.y0 + we.y1) / 2
    send(fig.canvas, 'button_press_event', lx, ly, button=1)
    check('图例拖拽显示 8 位预览', len(tw._leg_preview) == 8,
          str(len(tw._leg_preview)))
    send(fig.canvas, 'motion_notify_event', lx - 30, ly - 20)
    hl = [n for n, r in tw._leg_preview_map.items() if r.get_alpha() > 0.2]
    check('图例预览高亮一位', len(hl) == 1 and hl[0] in LEGEND_LOCS, str(hl))
    send(fig.canvas, 'button_release_event', lx - 30, ly - 20, button=1)
    st = tw.leg_state.get(id(axes[0]), {})
    check('图例拖拽吸附标准位',
          st.get('loc') in LEGEND_LOCS and st.get('anchor') is None,
          'loc=%s anchor=%s' % (st.get('loc'), st.get('anchor')))
    check('图例预览已清除', len(tw._leg_preview) == 0, str(len(tw._leg_preview)))
else:
    check('图例拖拽吸附标准位', False, 'no legend')

# ---- 13. 蓝框跟随拖拽 ----
tw._selected = {id(axes[0])}
tw._rebuild_selection_artists()
cx0, cy0 = pxf(axes[0], 0.5, 0.5)
send(fig.canvas, 'button_press_event', cx0, cy0, button=1)
send(fig.canvas, 'motion_notify_event', cx0 + 25, cy0 + 15)
send(fig.canvas, 'button_release_event', cx0 + 25, cy0 + 15, button=1)
bx, by, bw, bh = _bbox(axes[0])
rx, ry, rw, rh = tw._sel_map[id(axes[0])].get_bbox().bounds
check('蓝框跟随面板', abs(rx - bx) < 1e-6 and abs(ry - by) < 1e-6
      and abs(rw - bw) < 1e-6 and abs(rh - bh) < 1e-6,
      'panel=%s sel=%s' % ([round(v, 3) for v in (bx, by, bw, bh)],
                           [round(v, 3) for v in (rx, ry, rw, rh)]))

# ---- 12. 图例 8 个标准位 ----
check('图例 8 位', len(LEGEND_LOCS) == 8, '%d: %s' % (len(LEGEND_LOCS), LEGEND_LOCS))

# ---- 14. 方向键 = 移动面板（PPT 习惯）；Shift+方向键 = 微调尺寸（中心不动）----
tw._selected.clear()
tw._rebuild_selection_artists()
cx0, cy0 = pxf(axes[0], 0.5, 0.5)
send(fig.canvas, 'motion_notify_event', cx0, cy0)       # 让 _last_ax 指向 ax0
b0 = list(_bbox(axes[0]))
send(fig.canvas, 'key_press_event', cx0, cy0, key='up')
b1 = list(_bbox(axes[0]))
check('方向键↑ 上移 0.005', abs(b1[1] - b0[1] - 0.005) < 1e-9,
      '%.4f -> %.4f' % (b0[1], b1[1]))
check('方向键↑ 宽高不变', abs(b1[2] - b0[2]) < 1e-12
      and abs(b1[3] - b0[3]) < 1e-12, str(b1))
send(fig.canvas, 'key_press_event', cx0, cy0, key='right')
b2 = list(_bbox(axes[0]))
check('方向键→ 右移 0.005', abs(b2[0] - b1[0] - 0.005) < 1e-9,
      '%.4f -> %.4f' % (b1[0], b2[0]))
check('方向键→ 宽高不变', abs(b2[2] - b1[2]) < 1e-12
      and abs(b2[3] - b1[3]) < 1e-12, str(b2))
# Shift+方向键 = 微调尺寸（中心不动）
_bh0 = list(_bbox(axes[0]))
send(fig.canvas, 'key_press_event', cx0, cy0, key='shift+up')
_bh1 = list(_bbox(axes[0]))
check('Shift+↑ 加高（中心不动）', abs(_bh1[3] - _bh0[3] - 0.025) < 1e-9
      and abs((_bh1[1] + _bh1[3] / 2) - (_bh0[1] + _bh0[3] / 2)) < 1e-9, str(_bh1))

# ---- 15. 多列图例拖拽后必须仍是多列（曾因重建 Legend 丢了 ncols）----
def _ncols(leg):
    for n in ('_ncols', 'ncols', '_ncol', 'ncol'):
        v = getattr(leg, n, None)
        if isinstance(v, int):
            return v
    return 1


figL = plt.figure(figsize=(8, 6))
axL = figL.add_axes([0.10, 0.10, 0.80, 0.80])
for k in range(6):
    axL.plot([0, 1], [k, k], label='s%d' % k)
legL = axL.legend(ncols=3, loc='upper right')
figL.canvas.draw()
check('初始图例 3 列', _ncols(legL) == 3, str(_ncols(legL)))

twL = Tweak(figL)
bb = legL.get_window_extent()
cxL, cyL = (bb.x0 + bb.x1) / 2, (bb.y0 + bb.y1) / 2
send(figL.canvas, 'button_press_event', cxL, cyL, button=1)      # 按下图例（此处会重建）
send(figL.canvas, 'motion_notify_event', cxL - 80, cyL - 60)     # 拖动
send(figL.canvas, 'button_release_event', cxL - 80, cyL - 60, button=1)
check('拖后仍 3 列', _ncols(axL.get_legend()) == 3, str(_ncols(axL.get_legend())))
check('拖后图例仍在', axL.get_legend() is not None, '')

# ---- 16. #8 手势方向：普通拖 colorbar 端点=调长度；Alt+拖=改 clim ----
#     用户 2026-09-20 明确要的方向，反过来就是 bug，所以在这里锁死两条路径。
figG = plt.figure(figsize=(8, 5))
axG = figG.add_axes([0.10, 0.45, 0.80, 0.45])
imG = axG.imshow([[0.0, 1.0], [2.0, 3.0]], cmap='viridis')
# 手工 colorbar 轴（和真实论文脚本一样）：它的左右端与父轴不重合，
# 这样"拖长端点"不会被吸附到父轴边线而看不出变化。
cbaxG = figG.add_axes([0.20, 0.20, 0.50, 0.04])
cbG = figG.colorbar(imG, cax=cbaxG, orientation='horizontal')
figG.canvas.draw()
cbG.ax.set_axes_locator(None)          # 真实脚本里 colorbar 挂 locator，要先解除
cbG.ax.set_box_aspect(None)
figG.canvas.draw()
twG = Tweak(figG, export_path=os.path.join(
    tempfile.gettempdir(), 'test_events_cb.json'), heavy=False)

# 水平 colorbar 的右端点（长轴）像素坐标：transFigure 之后已经是像素
def cbtip():
    bb = cbG.ax.get_position().transformed(figG.transFigure)
    return bb.x1 - 3.0, (bb.y0 + bb.y1) / 2.0

_clim_a = _clim_of(cbG.ax)
_w_a = _bbox(cbG.ax)[2]
exG, eyG = cbtip()
send(figG.canvas, 'button_press_event', exG, eyG, button=1)          # 无 Alt
send(figG.canvas, 'motion_notify_event', exG + 70, eyG)
send(figG.canvas, 'button_release_event', exG + 70, eyG, button=1)
check('普通拖端点：clim 不变', _clim_of(cbG.ax) == _clim_a, str(_clim_of(cbG.ax)))
check('普通拖端点：长度变长', _bbox(cbG.ax)[2] > _w_a + 1e-6,
      'w %.4f -> %.4f' % (_w_a, _bbox(cbG.ax)[2]))

_w_b = _bbox(cbG.ax)[2]
exG, eyG = cbtip()                                                   # 位置已变，重取端点
send(figG.canvas, 'button_press_event', exG, eyG, button=1, key='alt')   # Alt+拖（clim 功能已删）
send(figG.canvas, 'motion_notify_event', exG + 40, eyG, key='alt')
send(figG.canvas, 'button_release_event', exG + 40, eyG, button=1, key='alt')
check('Alt+拖端点（clim 已移除）：按调长度处理', _bbox(cbG.ax)[2] > _w_b + 1e-6,
      'w %.4f -> %.4f' % (_w_b, _bbox(cbG.ax)[2]))

# ---- 多图会话：改动检测 + "最后改的那张"（launch 靠它挑图，不用预先 --fig）----
_td = tempfile.gettempdir()
pM1 = os.path.join(_td, 'tweak_multi_f0.json')
pM2 = os.path.join(_td, 'tweak_multi_f1.json')
fM1 = plt.figure(figsize=(5, 3))
fM1.add_subplot(111).plot([1, 2])
fM2 = plt.figure(figsize=(5, 3))
fM2.add_subplot(111).plot([2, 1])
clock = [0]                                     # 多图会话共享的改动计数器
tM1 = Tweak(fM1, export_path=pM1, heavy=False, fig_index=0, n_figs=2,
            edit_clock=clock)
tM2 = Tweak(fM2, export_path=pM2, heavy=False, fig_index=1, n_figs=2,
            edit_clock=clock)
tM1.export()
tM2.export()                                    # 各自落一份基线


def _rj(p):
    with open(p, encoding='utf-8-sig') as f:
        return json.load(f)


b1, b2 = _rj(pM1), _rj(pM2)
check('多图：基线里带 fig_index / n_figs',
      b1.get('fig_index') == 0 and b1.get('n_figs') == 2,
      'fig_index=%s n_figs=%s' % (b1.get('fig_index'), b1.get('n_figs')))

tM1._push_undo()                                # 只改第 0 张
_set_bbox(fM1.axes[0], [0.2, 0.2, 0.5, 0.5])
tM1.export()
check('多图：改过的那张被检测到', _rj(pM1) != b1)
check('多图：没动的那张完全不变', _rj(pM2) == b2)
check('多图：edit_seq 只涨改过的那张',
      tM1.edit_seq > 0 and tM2.edit_seq == 0,
      'seq=%d/%d' % (tM1.edit_seq, tM2.edit_seq))

tM2._push_undo()                                # 再改第 1 张 → 它才是"最后改的"
_set_bbox(fM2.axes[0], [0.1, 0.1, 0.6, 0.6])
tM2.export()
check('多图：后改的序号更大（挑"最后改的那张"）',
      tM2.edit_seq > tM1.edit_seq, 'seq=%d vs %d' % (tM2.edit_seq, tM1.edit_seq))
check('多图：两张都被判定为改过',
      _rj(pM1) != b1 and _rj(pM2) != b2)

print('----')
print('FAILED: %d' % fail if fail else 'ALL PASS')
sys.exit(1 if fail else 0)
