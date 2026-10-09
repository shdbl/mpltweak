# -*- coding: utf-8 -*-
"""跨平台 / 跨后端兼容性自检（发布前跑一遍）。

覆盖：
  1. Python 3.9 语法兼容（match 语句 / PEP 604 联合类型）
  2. 文件 I/O 编码（非二进制 open 必须显式 encoding）
  3. 后端矩阵：Agg / TkAgg / QtAgg 下能否建 Tweak 并完成拖拽 + undo/redo + toast
  4. 字体缺失：无中文字体时帮助窗口（预渲染 PNG）是否仍正常
  5. API 下限：matplotlib 3.5 起可用的 API（set_box_aspect / dpi_scale_trans /
     boxstyle rounding_size / canvas.new_timer）+ importlib.resources.files(3.9+)

用法：python tools/crosscheck.py
"""
import ast
import glob
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.abspath(os.path.join(HERE, '..', 'src'))
sys.path.insert(0, SRC)

_OK, _FAIL = [0], [0]


def chk(name, cond, extra=''):
    if cond:
        _OK[0] += 1
        print('PASS | %s %s' % (name, extra))
    else:
        _FAIL[0] += 1
        print('FAIL | %s %s' % (name, extra))


# ---------- 1) Python 3.9 语法兼容 + 2) 文本 open() 编码（AST 级，排除注释/docstring） ----------
_bad = []
_noenc = []
for _p in sorted(glob.glob(os.path.join(SRC, 'mpltweak', '*.py'))):
    _fn = os.path.basename(_p)
    _tree = ast.parse(open(_p, encoding='utf-8').read())
    for _n in ast.walk(_tree):
        if isinstance(_n, ast.Match):
            _bad.append('%s:%d match(3.10+)' % (_fn, _n.lineno))
        _annis = []
        if isinstance(_n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for _a in (list(_n.args.args) + list(_n.args.kwonlyargs)
                       + list(_n.args.posonlyargs)):
                if _a.annotation is not None:
                    _annis.append((_a.annotation, _n.lineno))
            if _n.returns is not None:
                _annis.append((_n.returns, _n.lineno))
        if isinstance(_n, ast.AnnAssign) and _n.annotation is not None:
            _annis.append((_n.annotation, _n.lineno))
        for _an, _ln in _annis:
            for _x in ast.walk(_an):
                if isinstance(_x, ast.BinOp) and isinstance(_x.op, ast.BitOr):
                    _bad.append('%s:%d PEP604 X|Y(3.10+)' % (_fn, _ln))
        # open() 调用：二进制放行，文本必须 encoding=
        if isinstance(_n, ast.Call) and isinstance(_n.func, ast.Name) \
                and _n.func.id == 'open':
            _kws = {k.arg for k in _n.keywords}
            _mode = None
            if len(_n.args) > 1 and isinstance(_n.args[1], ast.Constant):
                _mode = _n.args[1].value
            for _k in _n.keywords:
                if _k.arg == 'mode' and isinstance(_k.value, ast.Constant):
                    _mode = _k.value.value
            if _mode is not None and 'b' in str(_mode):
                continue
            if 'encoding' not in _kws:
                _noenc.append('%s:%d' % (_fn, _n.lineno))
chk('Python 3.9 语法兼容（无 match / X|Y 注解）', not _bad, '; '.join(_bad[:4]))
chk('文本 open() 均显式 encoding（AST 级）', not _noenc, '; '.join(_noenc[:4]))

# ---------- 3) 后端矩阵（子进程，避免后端切换污染） ----------
_BE_CODE = r'''
import matplotlib, sys
matplotlib.use("__BE__", force=True)
import matplotlib.pyplot as plt
sys.path.insert(0, r"__SRC__")
from mpltweak.toolbox import gaitu
fig, axes = plt.subplots(1, 2, figsize=(6, 3))
axes[0].plot([1, 2, 3], [1, 4, 9]); axes[1].plot([1, 2, 3], [3, 2, 1])
fig.canvas.draw()
tw = gaitu(fig, export_path=None, quiet=True, heavy=False)
E = type("E", (), {})
x0, y0 = axes[0].get_position().transformed(fig.transFigure).p0
px, py = x0 * fig.bbox.width, y0 * fig.bbox.height
e1 = E(); e1.inaxes = axes[0]; e1.x = px + 20; e1.y = py + 20
e1.button = 1; e1.key = None; e1.xdata = 1; e1.ydata = 1
e2 = E(); e2.inaxes = axes[0]; e2.x = px + 50; e2.y = py + 45
e2.button = 1; e2.key = None; e2.xdata = 1.5; e2.ydata = 1.5
e3 = E(); e3.inaxes = axes[0]; e3.x = px + 50; e3.y = py + 45
e3.button = 1; e3.key = None
tw._on_press(e1); tw._on_motion(e2); tw._on_release(e3)
_moved = tuple(round(v, 6) for v in tw._snapshot()["axes"][0]["pos"])
tw._undo_once(); tw._redo_once()
tw._toast("x"); tw._update_status(); fig.canvas.draw()
tw._toggle_help()                       # 帮助窗口（预渲染 PNG）
print("BACKEND_OK|%s|moved=%s|timer=%s" % (
    matplotlib.get_backend(), _moved, hasattr(fig.canvas, "new_timer")))
import os, sys                      # noqa: E402
sys.stdout.flush(); sys.stderr.flush()
os._exit(0)                         # 强制退出（先 flush，否则管道缓冲会吞掉输出）
'''


def _be_test(be):
    _code = _BE_CODE.replace('__BE__', be).replace('__SRC__', SRC)
    try:
        _r = subprocess.run([sys.executable, '-c', _code], cwd=SRC,
                            capture_output=True, text=True, timeout=120,
                            encoding='utf-8', errors='replace')
    except Exception as _e:                  # noqa: BLE001
        chk('后端 %s' % be, False, 'subprocess: %s' % _e)
        return
    _out = (_r.stdout or '') + (_r.stderr or '')
    _line = [l for l in _out.splitlines() if l.startswith('BACKEND_OK')]
    chk('后端 %s：建 Tweak + 拖拽 + undo/redo + toast + 帮助窗口' % be,
        bool(_line),
        (_line[0] if _line
         else ' | '.join(l for l in _out.strip().splitlines()
                         if 'Warning' not in l)[-300:]))


for _be in ('Agg', 'TkAgg', 'QtAgg'):
    _be_test(_be)

# ---------- 4) 字体缺失：帮助窗口仍正常（预渲染 PNG 免疫） ----------
import matplotlib                       # noqa: E402
matplotlib.use('Agg', force=True)
import matplotlib.pyplot as plt          # noqa: E402
import numpy as np                       # noqa: E402
from mpltweak import toolbox as tb          # noqa: E402

_orig_font = tb._pick_ui_font
tb._pick_ui_font = lambda: 'NoSuchFontXYZ'          # 模拟无中文字体的环境
_img = tb._load_help_png('zh')
chk('帮助窗口资源加载（与系统字体无关）', _img is not None,
    'shape=%s' % ((None if _img is None else _img.shape),))
tb._HELP_LANG = 'zh'
tb._show_help_fig()
_f = tb._HELP_FIG
_f.canvas.draw()
_buf, _size = _f.canvas.print_to_buffer()
_a = np.frombuffer(_buf, dtype='uint8').reshape(_size[1], _size[0], 4)
_non = int(np.sum(_a[:, :, :3].min(axis=2) < 250))
chk('无中文字体下帮助窗口仍有完整内容', _non > 100000, 'non-white=%d' % _non)
tb._close_help_fig()
tb._pick_ui_font = _orig_font

# ---------- 5) API 下限 ----------
from matplotlib.axes import Axes                     # noqa: E402
from matplotlib.figure import Figure                 # noqa: E402
from matplotlib.patches import FancyBboxPatch        # noqa: E402
import importlib.resources as _res                   # noqa: E402

chk('Axes.set_box_aspect（matplotlib 3.3+）', hasattr(Axes, 'set_box_aspect'))
chk('Figure.dpi_scale_trans（实例属性）', hasattr(plt.figure(), 'dpi_scale_trans'))
try:
    FancyBboxPatch((0, 0), 1, 1, boxstyle='round,pad=0,rounding_size=0.1')
    _rb = True
except Exception as _e:                              # noqa: BLE001
    _rb = False
chk('boxstyle rounding_size（3.4+）', _rb)
chk('importlib.resources.files（Python 3.9+）', hasattr(_res, 'files'))
chk('canvas.new_timer（toast 定时器）', hasattr(plt.figure().canvas, 'new_timer'))

print('\n==== %d PASS / %d FAIL ====' % (_OK[0], _FAIL[0]))
sys.exit(1 if _FAIL[0] else 0)
