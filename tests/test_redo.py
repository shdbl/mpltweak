# -*- coding: utf-8 -*-
# 新增 Redo 功能验证：Ctrl+Y / Ctrl+Shift+Z 键位 + undo/redo 栈行为。
# 用合成 KeyEvent 走真实回调管线（同 test_events.py 方式），Agg 后端不开窗。
import os
import sys
import tempfile

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.backend_bases import KeyEvent

from mpltweak.toolbox import Tweak, _bbox, _set_bbox

fail = 0


def check(name, cond, detail=''):
    global fail
    print(('PASS' if cond else 'FAIL') + ' | ' + name + ((' | ' + detail) if detail else ''))
    if not cond:
        fail += 1


def send_key(canvas, key):
    W, H = canvas.get_width_height()
    ev = KeyEvent('key_press_event', canvas, key, W / 2, H / 2)
    canvas.callbacks.process('key_press_event', ev)
    return ev


# 单轴图
fig = plt.figure(figsize=(6, 4))
ax = fig.add_axes([0.1, 0.1, 0.8, 0.8])
fig.canvas.draw()
tw = Tweak(fig, export_path=os.path.join(tempfile.gettempdir(), 'test_redo.json'),
           heavy=False)

box0 = list(_bbox(ax))
new_box = [box0[0] + 0.05, box0[1], box0[2], box0[3]]

# 模拟一次"拖动落位"：push_undo 快照当前 → 移动 → undo → redo
tw._push_undo()
_set_bbox(ax, new_box)
check('移动生效', abs(_bbox(ax)[0] - new_box[0]) < 1e-6, str(_bbox(ax)))

# Ctrl+Z 撤销
send_key(fig.canvas, 'ctrl+z')
check('Ctrl+Z 撤销回原位', abs(_bbox(ax)[0] - box0[0]) < 1e-6, str(_bbox(ax)))

# Ctrl+Y 重做
send_key(fig.canvas, 'ctrl+y')
check('Ctrl+Y 重做回移动位', abs(_bbox(ax)[0] - new_box[0]) < 1e-6, str(_bbox(ax)))

# 再撤销，用 Ctrl+Shift+Z 重做（第二键位）
send_key(fig.canvas, 'ctrl+z')
send_key(fig.canvas, 'ctrl+shift+z')
check('Ctrl+Shift+Z 重做', abs(_bbox(ax)[0] - new_box[0]) < 1e-6, str(_bbox(ax)))

# 空 redo 栈时不崩、不改变状态
tw._redo.clear()
send_key(fig.canvas, 'ctrl+y')
check('空 redo 栈安全', abs(_bbox(ax)[0] - new_box[0]) < 1e-6, str(_bbox(ax)))

# 新操作清空 redo 栈（标准语义）：undo 后做新改动，redo 不可用
send_key(fig.canvas, 'ctrl+z')                # 回原位
tw._push_undo()                               # 新操作
_set_bbox(ax, [box0[0] - 0.02] + box0[1:])    # 另一个位置
send_key(fig.canvas, 'ctrl+y')                # 应无操作（redo 已被清）
check('新操作后 redo 被清空', abs(_bbox(ax)[0] - (box0[0] - 0.02)) < 1e-6,
      str(_bbox(ax)))

print('FAILED: %d' % fail if fail else 'ALL PASS')
sys.exit(1 if fail else 0)
