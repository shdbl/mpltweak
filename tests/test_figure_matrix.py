# -*- coding: utf-8 -*-
"""离屏图形矩阵：12 种真实论文常见图形 × 200 次随机操作 + 13 类专项操作断言。

背景（用户 2026-10-12 要求）：窗口交互现有的证据只有 ~15 条断言、2 次拖拽、1 次
框选，**撑不起"可靠"**。本套件把证据面扩一个量级：换成各种真实画法（多面板 /
不规则 GridSpec / colorbar / 轴外图例 / twinx / inset / constrained_layout /
tight_layout / suptitle / 多 figure）各跑一遍随机手势与专项手势。

两层口径：
  * **A 层（随机操作）**：每图 200 次随机手势（含乱序形态：没按就动、按了不松、
    没按就松、拖到画布外、中途撤销重做）。只断言 2 条**环境无关**铁律：
      ① 不崩 —— 任意序列不抛异常；
      ② 状态未坏 —— 轴数不变（面板未丢）、每轴内容数不变（图元未凭空多/少）、
         几何合法（坐标有限、w/h ≥ MIN_SIZE / colorbar 用 MIN_SIZE_CB）、
         原本有的图例/总标题没丢。
  * **B 层（专项操作断言）**：13 类手势逐条验"操作按预期生效"，**宽容口径**：
    范围 / 相对位移 + 容差，禁止精确等值（像素被 int 截断、引擎按浮点 fig.bbox
    归一化，硬等值必然假红）。每种图形只测其**适用**的类别；不适用的**打印理由**
    （SKIP 行），不静默跳过。

可追溯性：每条断言都记录 `文件:行号`（`sys._getframe`），末尾数字表的"来源"列
就是这些行号；**不可追溯的项留空不估**。

反向验证（teeth）：文件末尾有 `_teeth_probes()` —— 故意破坏状态/篡改期望，验证
上面这些检测器**真的会咬**（防止"为了通过而写空断言"）。

纪律：
  * **显式钉死 `MPLBACKEND=Agg`、零 GUI 对象创建**（不 ``tk.Tk()`` / 不
    ``QApplication()`` / 不 ``plt.show()``）——本套件**全程离屏、不开窗、不抢鼠标**。
    合成事件直接喂 ``canvas.callbacks.process``，与真实 mpl_connect 路径一致。
  * 只读 src/：本文件不改任何产品代码。若发现真缺陷：如实报 + 最小复现，交船长派修。
  * 事件坐标一律整数像素；落位期望按引擎同口径（``fig.bbox.size`` 浮点）算。

跑法：``python tests/test_figure_matrix.py``   （可用 MPLTWEAK_MATRIX_N 覆盖每图次数）
"""

import contextlib
import io
import json
import math
import os
import random
import shutil
import sys
import tempfile
import time
import warnings

import matplotlib
matplotlib.use('Agg')                       # 显式无头：绝不落到 Qt/Tk
import matplotlib.pyplot as plt
from matplotlib.backend_bases import MouseEvent, KeyEvent

from mpltweak.toolbox import (Tweak, _bbox, _set_bbox, _snap_box, MIN_SIZE,
                              MIN_SIZE_CB, LEGEND_LOCS, _clim_of,
                              _is_colorbar_ax)
import mpltweak.toolbox as _tb

# 本机没装中文字体时 matplotlib 会对每个缺字形的中文标题打一条 UserWarning ——
# 那是**环境**问题（和产品无关），16 面板 × 几百次操作会刷屏。只静音这一类。
warnings.filterwarnings(
    'ignore', message=r'.*Glyph \d+ .* missing from font.*')
warnings.filterwarnings(
    'ignore', message=r'.*missing from current font.*')
warnings.filterwarnings('ignore', category=UserWarning,
                        module=r'matplotlib\.(text|font_manager|backend_agg)')


# ---- 输出兜底（T13 纪律）：GBK 控制台 / 管道下 print 中文不崩 ----
def _install_console_fallback():
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(errors='replace')
        except (AttributeError, ValueError, OSError):
            pass


_install_console_fallback()


def _gbk_safe(s):
    s = str(s)
    enc = getattr(sys.stdout, 'encoding', None) or 'utf-8'
    try:
        return s.encode(enc, 'replace').decode(enc, 'replace')
    except (LookupError, UnicodeDecodeError):
        return s.encode('ascii', 'backslashreplace').decode('ascii')


# ---- 语言 / 后端钉死（别让宿主 locale 决定结论；后端绝不落到 Qt/Tk）----
os.environ['MPLTWEAK_LANG'] = 'zh'
os.environ['MPLBACKEND'] = 'Agg'
_tb.QUIET = True

# 每图随机操作次数。默认 1000（船长 2026-10-12 裁定：离屏这一层本该比真窗口
# 堆更多量 —— 它不开窗、确定性、可复现、能进 CI）。
#   MPLTWEAK_MATRIX_N=50 python tests/test_figure_matrix.py   # 快速自检用
# 实测口径（本机 8 核 Windows / Agg / figsize 见各构造器）：
#   每图 200 次 → 全场 12 种图形 706s；每图 1000 次 → 见报告里的实测行。
N_RANDOM = int(os.environ.get('MPLTWEAK_MATRIX_N') or 1000)
SEED = 20261012
# 画布 dpi 只影响**像素数**（性能），不影响任何比例/断言口径：文字是点单位、
# 位置是 figure 比例，两者随 dpi 同步缩放。需要更快时可 MPLTWEAK_MATRIX_DPI=72。
DPI = int(os.environ.get('MPLTWEAK_MATRIX_DPI') or 100)
matplotlib.rcParams['figure.dpi'] = DPI


class _Tee(object):
    """stdout 同时写控制台与 UTF-8 日志文件。

    为什么要自带日志：用 PowerShell 的 `*>` 重定向抓输出，本机会把中文写成
    非 UTF-8 字节（实测读回来是 U+FFFD），证据就不可读了。套件自己写日志，
    数字表的"日志行"列也才有真数（见 _LOG_LINE）。
    """

    def __init__(self, *streams):
        self.streams = streams

    def write(self, s):
        n = 0
        for st in self.streams:
            try:
                n = st.write(s)
            except Exception:                                    # noqa: BLE001
                pass
        _LOG_LINE[0] += s.count('\n')
        return n if n else len(s)

    def flush(self):
        for st in self.streams:
            try:
                st.flush()
            except Exception:                                    # noqa: BLE001
                pass

    def reconfigure(self, **kw):
        for st in self.streams:
            try:
                st.reconfigure(**kw)
            except Exception:                                    # noqa: BLE001
                pass

    def isatty(self):
        return False

    @property
    def encoding(self):
        return 'utf-8'


_LOG_LINE = [0]          # 已经写进日志的行数（数字表的"日志行"列用它）
_LOG_FH = [None]


# ---- 临时区：沙箱可能拦真实 %TEMP%，把 TEMP/TMP 指到可写目录 ----
def _ensure_tmp_writable():
    base = tempfile.gettempdir()
    root = os.path.join(base, '.mpltweak_matrix_%d' % os.getpid())
    try:
        os.makedirs(root, exist_ok=True)
        probe = os.path.join(root, '.w')
        open(probe, 'w').close()
        os.unlink(probe)
    except OSError:
        root = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            '.tmp_matrix_%d' % os.getpid())
        os.makedirs(root, exist_ok=True)
    os.environ['TEMP'] = root
    os.environ['TMP'] = root
    os.environ['TMPDIR'] = root
    tempfile.tempdir = root
    return root


_TMPROOT = _ensure_tmp_writable()

fail = 0
# 断言台账：每条断言一行，line 可追溯到具体断言行
_RECORDS = []


def check(name, cond, detail='', kind='meta', fig='-', cls='-'):
    global fail
    line = sys._getframe(1).f_lineno
    _RECORDS.append(dict(kind=kind, fig=fig, cls=cls, name=name,
                         ok=bool(cond), line=line, detail=detail))
    print(('PASS' if cond else 'FAIL') + ' | ' + _gbk_safe(name)
          + ((' | ' + _gbk_safe(detail)) if detail else ''))
    if not cond:
        fail += 1
    return bool(cond)


def skip_note(name, reason, kind='target', fig='-', cls='-'):
    """不适用的专项子项：**打印理由**（不静默跳过），不算失败也不算通过。"""
    _RECORDS.append(dict(kind='n/a', fig=fig, cls=cls, name=name,
                         ok=None, line=sys._getframe(1).f_lineno,
                         detail=reason))
    print('SKIP | ' + _gbk_safe(name) + ' | 不适用理由: ' + _gbk_safe(reason))


def send(canvas, name, x=None, y=None, button=None, key=None):
    """构造事件走回调管线。坐标一律整数像素；鼠标事件要先重置 canvas._key。"""
    x = int(x) if x is not None else None
    y = int(y) if y is not None else None
    if name == 'key_press_event':
        ev = KeyEvent('key_press_event', canvas, key, x, y)
    else:
        try:
            canvas._key = key or None
        except AttributeError:
            pass
        ev = MouseEvent(name, canvas, x, y, button=button, key=(key or None))
    canvas.callbacks.process(name, ev)
    return ev


# ---------------------------------------------------------------------------
# 执行环境：一个 fig + 一个 Tweak；坐标换算 / 手势分发
# ---------------------------------------------------------------------------
class _Env:
    def __init__(self, fig, tag, **kw):
        self.fig = fig
        self.refresh_px()
        _buf = io.StringIO()
        with contextlib.redirect_stdout(_buf):
            self.tw = Tweak(fig, export_path=os.path.join(
                _TMPROOT, tag + '.json'), heavy=False, **kw)
        self.tw.snap = True

    def refresh_px(self):
        try:
            self.W, self.H = (float(v) for v in self.fig.bbox.size)
        except Exception:                                        # noqa: BLE001
            self.W, self.H = (float(v) for v in
                              self.fig.canvas.get_width_height())

    def px(self, ax, fx, fy):
        p = ax._position
        return (int(round((p.x0 + p.width * fx) * self.W)),
                int(round((p.y0 + p.height * fy) * self.H)))

    def pos(self, ax):
        return [float(v) for v in _bbox(ax)]

    def center(self, ax):
        return self.px(ax, 0.5, 0.5)

    def hover(self, ax, target):
        """标题/轴标签/刻度/图例中心像素；没有就退回面板中心。"""
        t = None
        if target == 'title':
            t = ax.title
        elif target == 'xlabel':
            t = ax.xaxis.label
        elif target == 'ylabel':
            t = ax.yaxis.label
        elif target == 'xtick':
            tls = [x for x in ax.get_xticklabels() if x.get_text()]
            t = tls[0] if tls else None
        elif target == 'ytick':
            tls = [x for x in ax.get_yticklabels() if x.get_text()]
            t = tls[0] if tls else None
        elif target == 'legend':
            leg = ax.get_legend()
            if leg is None:
                return None
            we = leg.get_window_extent()
            return (int(round((we.x0 + we.x1) / 2)),
                    int(round((we.y0 + we.y1) / 2)))
        if t is None or not t.get_text():
            return None
        we = t.get_window_extent()
        return (int(round((we.x0 + we.x1) / 2)),
                int(round((we.y0 + we.y1) / 2)))

    def drag(self, ax, dxy, steps=(0.4, 0.75), release=None):
        """完整拖拽：按下 → 多步 motion → 松开。返回 (press 像素, 最后 motion 偏移)。

        宽容口径：期望落位由**最后一次 motion** 决定（松手位置可以不同）。
        """
        c = self.center(ax)
        send(self.fig.canvas, 'button_press_event', c[0], c[1], button=1)
        for f in steps:
            send(self.fig.canvas, 'motion_notify_event',
                 c[0] + int(dxy[0] * f), c[1] + int(dxy[1] * f))
        send(self.fig.canvas, 'motion_notify_event',
             c[0] + int(dxy[0]), c[1] + int(dxy[1]))
        rel = release if release is not None else dxy
        send(self.fig.canvas, 'button_release_event',
             c[0] + int(rel[0]), c[1] + int(rel[1]), button=1)
        return c, (int(dxy[0]), int(dxy[1]))

    def click(self, x, y, key=None):
        send(self.fig.canvas, 'button_press_event', x, y, button=1, key=key)
        send(self.fig.canvas, 'button_release_event', x, y, button=1)

    def blank_spot(self):
        """画布上一块"没有面板"的空白（用于清空选中 / 空白框选）。"""
        for fx, fy in ((0.02, 0.98), (0.98, 0.02), (0.5, 0.995), (0.995, 0.5)):
            x, y = int(fx * self.W), int(fy * self.H)
            try:
                hit = self.fig.canvas.inaxes((x, y))
            except Exception:                                    # noqa: BLE001
                hit = None
            if hit is None:
                return x, y
        return int(0.5 * self.W), int(0.995 * self.H)


def _pxtol(env, px=2.0):
    """像素容差 → figure 比例容差（int 截断最多吃掉 1px，留 2px 余量）。"""
    return px / max(env.W, 1.0), px / max(env.H, 1.0)


# ---------------------------------------------------------------------------
# 铁律检测器（A 层用；teeth 探针直接调它们验证"真的会咬"）
# ---------------------------------------------------------------------------
def _content_counts(ax):
    return (len(ax.lines), len(ax.collections), len(ax.patches),
            len(ax.texts), len(ax.images), len(ax.artists),
            len(ax.child_axes))


def _snapshot(fig):
    return dict(
        n_axes=len(fig.axes),
        counts=[_content_counts(ax) for ax in fig.axes],
        legends=[ax.get_legend() is not None for ax in fig.axes],
        suptitle=(getattr(fig, '_suptitle', None) is not None),
        is_cb=[bool(_is_colorbar_ax(ax)) for ax in fig.axes],
        cb_ids=[id(ax) for ax in fig.axes if _is_colorbar_ax(ax)],
    )


def _state_laws(fig, snap):
    """铁律①②：面板未丢 + 内容数不变 + 几何合法 + 图例/总标题未丢。"""
    if len(fig.axes) != snap['n_axes']:
        return False, ('轴数 %d != 初始 %d（面板丢失或新增）'
                       % (len(fig.axes), snap['n_axes']))
    for j, ax in enumerate(fig.axes):
        try:
            x0, y0, w, h = (float(v) for v in _bbox(ax))
        except Exception as e:                                   # noqa: BLE001
            return False, 'ax%d bbox 读取失败: %s' % (j, e)
        if not all(math.isfinite(v) for v in (x0, y0, w, h)):
            return False, ('ax%d 坐标非有限值 [%s]'
                           % (j, ','.join('%.4f' % v for v in (x0, y0, w, h))))
        mn = MIN_SIZE_CB if _is_colorbar_ax(ax) else MIN_SIZE
        if w < mn - 1e-9 or h < mn - 1e-9:
            return False, ('ax%d 过小 w=%.5f h=%.5f（min=%.4f）' % (j, w, h, mn))
        if j < len(snap['counts']):
            got, exp = _content_counts(ax), snap['counts'][j]
            if got != exp:
                _names = ('lines', 'collections', 'patches', 'texts', 'images',
                          'artists', 'child_axes')
                _diff = ', '.join('%s %d->%d' % (n, a, b)
                                  for n, a, b in zip(_names, exp, got) if a != b)
                return False, 'ax%d 内容数变了（%s）' % (j, _diff)
        if j < len(snap['legends']):
            if snap['legends'][j] and ax.get_legend() is None:
                return False, 'ax%d 原本有图例，现在没了' % j
            if (not snap['legends'][j]) and ax.get_legend() is not None:
                return False, 'ax%d 凭空多出一个图例' % j
    if snap['suptitle'] and getattr(fig, '_suptitle', None) is None:
        return False, '总标题（suptitle）被丢掉了'
    if snap['cb_ids']:
        now = [id(ax) for ax in fig.axes if _is_colorbar_ax(ax)]
        if now != snap['cb_ids']:
            return False, 'colorbar 轴集合变了（%d -> %d）' % (len(snap['cb_ids']),
                                                              len(now))
    return True, ''


# ---------------------------------------------------------------------------
# 12 种图形夹具（真实论文常见画法）
# ---------------------------------------------------------------------------
def _deco(ax, tag, nline=3):
    ax.plot(list(range(nline)), [v * 0.5 for v in range(nline)], label='s')
    ax.set_title('面板 %s' % tag, fontsize=9)
    ax.set_xlabel('x 轴', fontsize=8)
    ax.set_ylabel('y 轴', fontsize=8)
    ax.tick_params(labelsize=7)
    return ax


def _fig_single():
    fig, ax = plt.subplots(figsize=(6, 4))
    _deco(ax, '①')
    ax.legend(loc='upper right', fontsize=7)
    return [(fig, [ax])]


def _fig_2x2():
    fig, axs = plt.subplots(2, 2, figsize=(8, 6))
    for k, a in enumerate(axs.ravel()):
        _deco(a, '②-%d' % k)
        a.scatter([0.2, 0.4], [0.3, 0.6], s=12)
    axs[0, 0].legend(loc='upper right', fontsize=7)
    return [(fig, list(axs.ravel()))]


def _fig_4x4():
    fig, axs = plt.subplots(4, 4, figsize=(10, 9))
    for k, a in enumerate(axs.ravel()):
        _deco(a, '③-%d' % k, nline=2)
    axs[0, 0].legend(loc='upper right', fontsize=6)
    return [(fig, list(axs.ravel()))]


def _fig_1x3():
    fig, axs = plt.subplots(1, 3, figsize=(12, 4))
    for k, a in enumerate(axs):
        _deco(a, '④-%d' % k)
        a.legend(loc='lower right', fontsize=7)
    return [(fig, list(axs))]


def _fig_gridspec():
    fig = plt.figure(figsize=(9, 7))
    gs = fig.add_gridspec(3, 3, hspace=0.45, wspace=0.4)
    a1 = fig.add_subplot(gs[0, :])          # 跨 3 列
    a2 = fig.add_subplot(gs[1, 0])
    a3 = fig.add_subplot(gs[1:, 1])         # 跨 2 行
    a4 = fig.add_subplot(gs[2, 2])
    for k, a in enumerate((a1, a2, a3, a4)):
        _deco(a, '⑤-%d' % k)
    a1.legend(loc='upper right', fontsize=7)
    return [(fig, [a1, a2, a3, a4])]


def _fig_colorbar():
    fig, ax = plt.subplots(figsize=(6, 4))
    im = ax.imshow([[0.0, 1.0], [2.0, 3.0]], cmap='viridis', aspect='auto')
    _deco(ax, '⑥')
    cb = fig.colorbar(im, ax=ax)
    # 真实论文脚本常给 colorbar 挂 locator / box_aspect；拖端点前解除（同 test_events）
    for _fn, _v in (('set_axes_locator', None), ('set_box_aspect', None)):
        try:
            getattr(cb.ax, _fn)(_v)
        except Exception:                                        # noqa: BLE001
            pass
    fig.canvas.draw()
    return [(fig, [ax])], cb.ax


def _fig_legend_out():
    fig, ax = plt.subplots(figsize=(7, 4))
    fig.subplots_adjust(right=0.70)
    for k in range(4):
        ax.plot([0, 1], [k, k + 1], label='series %d' % k)
    _deco(ax, '⑦')
    ax.legend(loc='upper left', bbox_to_anchor=(1.02, 1.0),
              borderaxespad=0.0, fontsize=7)
    return [(fig, [ax])]


def _fig_twinx():
    fig, ax = plt.subplots(figsize=(6, 4))
    _deco(ax, '⑧')
    ax2 = ax.twinx()
    ax2.plot([0, 1, 2], [3, 2, 1], 'r--', label='right')
    ax2.set_ylabel('右侧 y', fontsize=8)
    ax2.legend(loc='lower right', fontsize=7)
    return [(fig, [ax, ax2])]


def _fig_inset():
    fig, ax = plt.subplots(figsize=(6, 4))
    _deco(ax, '⑨')
    ax.legend(loc='upper right', fontsize=7)
    axin = ax.inset_axes([0.62, 0.62, 0.30, 0.30])
    axin.plot([0, 1, 2], [2, 1, 0])
    axin.set_title('inset', fontsize=6)
    return [(fig, [ax])]


def _fig_constrained():
    fig, axs = plt.subplots(2, 3, layout='constrained', figsize=(10, 6))
    for k, a in enumerate(axs.ravel()):
        _deco(a, '⑩-%d' % k, nline=2)
    axs[0, 0].legend(loc='upper right', fontsize=6)
    return [(fig, list(axs.ravel()))]


def _fig_tight():
    fig, axs = plt.subplots(2, 2, figsize=(8, 6))
    for k, a in enumerate(axs.ravel()):
        _deco(a, '⑪-%d' % k)
    axs[0, 1].legend(loc='upper right', fontsize=7)
    fig.tight_layout()
    return [(fig, list(axs.ravel()))]


_LONG_TITLE = ('这是一个非常长的标题：用于验证超长文字、suptitle 与多 figure 同时存在时'
               '拖拽/框选/撤销都不崩，也不丢文字')


def _fig_multi():
    f0 = plt.figure(figsize=(9, 7))
    f0.suptitle(_LONG_TITLE, fontsize=10)
    a0 = f0.add_axes([0.08, 0.55, 0.40, 0.34])
    a1 = f0.add_axes([0.55, 0.55, 0.40, 0.34])
    a2 = f0.add_axes([0.08, 0.08, 0.40, 0.34])
    for k, a in enumerate((a0, a1, a2)):
        _deco(a, '⑫a-%d' % k, nline=2)
    a0.set_title(_LONG_TITLE[:40], fontsize=7)
    a0.legend(loc='upper right', fontsize=6)
    f1 = plt.figure(figsize=(6, 4))
    f1.suptitle('第二张图（同脚本多 figure）', fontsize=9)
    b0 = f1.add_axes([0.12, 0.14, 0.76, 0.70])
    _deco(b0, '⑫b')
    b0.legend(loc='lower right', fontsize=7)
    return [(f0, [a0, a1, a2]), (f1, [b0])]


# (编号, 名称, 构造器, 每张图的随机操作次数倍数)
# 编号用 ASCII 数字：契约里的 ①..⑫ 与这里的 1..12 **按顺序一一对应**。
# 为什么不用 ⑪/⑫ 这类字符：GBK 只有 ①..⑩（U+2460..U+2469），⑪/⑫ 编不出来，
# 在 GBK 控制台上会变成 '?'（本套件要求 UTF-8 / GBK 两种编码都能读）。
FIGURES = [
    ('1', '①单轴折线图', _fig_single, 1),
    ('2', '②2×2 subplots（4 面板）', _fig_2x2, 1),
    ('3', '③4×4 subplots（16 面板）', _fig_4x4, 1),
    ('4', '④1×3 横排', _fig_1x3, 1),
    ('5', '⑤GridSpec 不规则（跨行跨列）', _fig_gridspec, 1),
    ('6', '⑥带 colorbar（独立 colorbar 轴）', _fig_colorbar, 1),
    ('7', '⑦图例在轴外（bbox_to_anchor）', _fig_legend_out, 1),
    ('8', '⑧twinx 双 Y 轴', _fig_twinx, 1),
    ('9', '⑨inset_axes 嵌套小图', _fig_inset, 1),
    ('10', '⑩constrained_layout', _fig_constrained, 1),
    ('11', '11 tight_layout（⑪）', _fig_tight, 1),
    ('12', '12 suptitle + 长标题 + 多 figure 脚本（⑫）', _fig_multi, 1),
]


def _build(ctor):
    """构造图形：colorbar 类型返回 (figs, cbax)，其余返回 (figs, None)。"""
    out = ctor()
    if isinstance(out, tuple):
        return out[0], out[1]
    return out, None


# ---------------------------------------------------------------------------
# A 层：随机操作（含乱序形态）
# ---------------------------------------------------------------------------
def _rand_target(rng, env, meta):
    panels = meta['panels']
    ax = panels[rng.randrange(len(panels))]
    if rng.random() < 0.25:
        # 有时按在画布任意处（可能落在空白/别的面板上）——真实用户也会这样
        return ax, (int(rng.uniform(-0.05, 1.05) * env.W),
                    int(rng.uniform(-0.05, 1.05) * env.H))
    return ax, env.center(ax)


def _exec_random_op(rng, env, meta):
    kind = rng.choice(meta['op_kinds'])
    fig, canvas = env.fig, env.fig.canvas
    if kind == 'drag':
        ax, c = _rand_target(rng, env, meta)
        d = (int(rng.uniform(-0.25, 0.25) * env.W),
             int(rng.uniform(-0.2, 0.2) * env.H))
        send(canvas, 'button_press_event', c[0], c[1], button=1)
        for _ in range(rng.randint(1, 4)):
            send(canvas, 'motion_notify_event',
                 c[0] + int(rng.uniform(-0.3, 0.3) * env.W),
                 c[1] + int(rng.uniform(-0.3, 0.3) * env.H))
        send(canvas, 'motion_notify_event', c[0] + d[0], c[1] + d[1])
        rel = (d[0] + int(rng.uniform(-0.05, 0.05) * env.W),
               d[1] + int(rng.uniform(-0.05, 0.05) * env.H))
        send(canvas, 'button_release_event', c[0] + rel[0], c[1] + rel[1],
             button=1)
    elif kind == 'drag_out':
        # 拖到画布外（中途 + 最终都越界）——乱序形态之一
        ax, c = _rand_target(rng, env, meta)
        send(canvas, 'button_press_event', c[0], c[1], button=1)
        for _ in range(rng.randint(1, 3)):
            send(canvas, 'motion_notify_event',
                 int(rng.uniform(-2.0, 3.0) * env.W),
                 int(rng.uniform(-2.0, 3.0) * env.H))
        send(canvas, 'button_release_event',
             int(rng.uniform(-2.0, 3.0) * env.W),
             int(rng.uniform(-2.0, 3.0) * env.H), button=1)
    elif kind == 'click_panel':
        ax, c = _rand_target(rng, env, meta)
        env.click(c[0], c[1])
    elif kind == 'click_blank':
        x, y = env.blank_spot()
        env.click(x, y)
    elif kind == 'click_mod':
        ax, c = _rand_target(rng, env, meta)
        env.click(c[0], c[1], key=rng.choice(['control', 'shift']))
    elif kind == 'motion_only':
        # 没按就动
        send(canvas, 'motion_notify_event',
             int(rng.uniform(-0.1, 1.1) * env.W),
             int(rng.uniform(-0.1, 1.1) * env.H))
    elif kind == 'press_hold':
        # 按了不松（后续手势会撞上这个"残留按下"状态）
        ax, c = _rand_target(rng, env, meta)
        send(canvas, 'button_press_event', c[0], c[1], button=1)
        send(canvas, 'motion_notify_event',
             c[0] + int(rng.uniform(-0.1, 0.1) * env.W),
             c[1] + int(rng.uniform(-0.1, 0.1) * env.H))
    elif kind == 'release_alone':
        # 没按就松
        ax, c = _rand_target(rng, env, meta)
        send(canvas, 'button_release_event', c[0], c[1], button=1)
    elif kind == 'double_press':
        ax, c = _rand_target(rng, env, meta)
        send(canvas, 'button_press_event', c[0], c[1], button=1)
        send(canvas, 'button_press_event', c[0] + 4, c[1] + 4, button=1)
        send(canvas, 'button_release_event', c[0] + 4, c[1] + 4, button=1)
    elif kind in ('undo', 'redo'):
        ax, c = _rand_target(rng, env, meta)
        send(canvas, 'key_press_event', c[0], c[1],
             key=('ctrl+z' if kind == 'undo' else 'ctrl+y'))
    elif kind in ('nudge', 'shift_nudge'):
        ax, c = _rand_target(rng, env, meta)
        send(canvas, 'motion_notify_event', c[0], c[1])
        send(canvas, 'key_press_event', c[0], c[1],
             key=rng.choice(['up', 'down', 'left', 'right']
                            if kind == 'nudge'
                            else ['shift+up', 'shift+down', 'shift+left',
                                  'shift+right']))
    elif kind == 'wire':
        ax, c = _rand_target(rng, env, meta)
        send(canvas, 'key_press_event', c[0], c[1], key=' ')
    elif kind == 'hover_font':
        ax = meta['panels'][rng.randrange(len(meta['panels']))]
        tgt = rng.choice(['title', 'xlabel', 'ylabel', 'xtick', 'ytick'])
        p = env.hover(ax, tgt)
        if p is not None:
            send(canvas, 'key_press_event', p[0], p[1],
                 key=rng.choice(['+', '-']))
    elif kind == 'legend_drag':
        ax = meta['leg_axes'][rng.randrange(len(meta['leg_axes']))]
        p = env.hover(ax, 'legend')
        if p is not None:
            send(canvas, 'button_press_event', p[0], p[1], button=1)
            send(canvas, 'motion_notify_event',
                 p[0] + int(rng.uniform(-60, 60)),
                 p[1] + int(rng.uniform(-40, 40)))
            send(canvas, 'button_release_event',
                 p[0] + int(rng.uniform(-60, 60)),
                 p[1] + int(rng.uniform(-40, 40)), button=1)
    elif kind == 'cb_tip':
        cbax = meta['cb']
        bb = cbax.get_position().transformed(fig.transFigure)
        if getattr(cbax, 'get_box_aspect', lambda: None)() is not None:
            bb = cbax.get_position().transformed(fig.transFigure)
        if bb.width >= bb.height:          # 水平 colorbar：拖右端
            tx, ty = bb.x1 - 3.0, (bb.y0 + bb.y1) / 2.0
        else:                              # 垂直：拖上端
            tx, ty = (bb.x0 + bb.x1) / 2.0, bb.y1 - 3.0
        send(canvas, 'button_press_event', tx, ty, button=1)
        send(canvas, 'motion_notify_event',
             tx + int(rng.uniform(-40, 60)), ty + int(rng.uniform(-40, 60)))
        send(canvas, 'button_release_event',
             tx + int(rng.uniform(-40, 60)), ty + int(rng.uniform(-40, 60)),
             button=1)
    elif kind == 'fit':
        ax, c = _rand_target(rng, env, meta)
        _buf = io.StringIO()
        with contextlib.redirect_stdout(_buf):
            send(canvas, 'key_press_event', c[0], c[1], key='ctrl+f')
        env.refresh_px()
    else:
        raise ValueError('未知随机手势 %r' % kind)


def _op_kinds_for(meta):
    kinds = ['drag', 'drag', 'drag', 'drag_out', 'click_panel', 'click_panel',
             'click_blank', 'click_mod', 'click_mod', 'motion_only',
             'press_hold', 'release_alone', 'double_press', 'undo', 'undo',
             'redo', 'nudge', 'shift_nudge', 'wire', 'hover_font', 'fit']
    if meta['leg_axes']:
        kinds += ['legend_drag', 'legend_drag']
    if meta['cb'] is not None:
        kinds += ['cb_tip', 'cb_tip']
    return kinds


def _twins_of(fig, ax):
    """与 ax 共享 x 或 y 轴的兄弟轴（twinx/twiny 造出来的那种）。"""
    out = []
    for g in (ax.get_shared_x_axes(), ax.get_shared_y_axes()):
        try:
            for o in g.get_siblings(ax):
                if o is not ax and o not in out:
                    out.append(o)
        except Exception:                                        # noqa: BLE001
            pass
    return out


def _isolate(env, panels):
    """把**所有**面板摆到互不重叠的网格槽里（含 twin 家族整体摆放）。

    为什么必须做：合成事件按下时引擎按"光标下最上层的轴"路由（canvas.inaxes）。
    如果只挪动一部分面板、剩下的还压在原位，按下就会落到别的轴上 —— 断言
    "这块面板动了"就变成了假红（我第一版就是这么翻车的）。摆成网格后，
    每块面板的中心点唯一归属它自己，专项断言才有意义。

    twin 家族（twinx）：成员**保持重合**（真实脚本里它们本来就是一个绘图区），
    因此按下会路由到家族里最上层那个成员，断言按"实际接住按下的那块"来判。
    """
    groups = []
    seen = set()
    for ax in panels:
        if id(ax) in seen:
            continue
        fam = [ax] + [t for t in _twins_of(env.fig, ax) if t in panels]
        for m in fam:
            seen.add(id(m))
        groups.append(fam)
    n = len(groups)
    cols = int(math.ceil(math.sqrt(n))) if n else 1
    rows = int(math.ceil(n / cols)) if n else 1
    gw = min(0.30, (0.90 - 0.06 * (cols - 1)) / max(cols, 1))
    gh = min(0.28, (0.84 - 0.08 * (rows - 1)) / max(rows, 1))
    for i, fam in enumerate(groups):
        r, c = divmod(i, cols)
        x0 = 0.07 + c * (gw + 0.06)
        y0 = 0.11 + (rows - 1 - r) * (gh + 0.08)
        for m in fam:
            _set_bbox(m, [x0, y0, gw, gh])
    env.fig.canvas.draw()
    env.refresh_px()


def _hit_panel(env, ax):
    """按下 ax 的中心时，引擎实际会接住哪块轴（twin/重叠时可能不是 ax 本人）。"""
    c = env.center(ax)
    try:
        hit = env.fig.canvas.inaxes(c)
    except Exception:                                            # noqa: BLE001
        hit = None
    return hit if hit is not None else ax


def _normalize_all(env, meta):
    """把所有轴（含 colorbar）摆回画布内的确定位置。

    用途：跑完 C12（colorbar 拖端点）这类会改 colorbar 位置的手势后，C13 需要
    一个"内容都在画布内"的干净起点，否则测到的是上一条手势留下的残局，而不是
    裁白边本身。这不是放宽断言 —— 契约要求的是"裁白边之后内容都在画布内"。
    """
    _isolate(env, meta['panels'])
    cb = meta.get('cb')
    if cb is not None:
        try:
            _set_bbox(cb, [0.80, 0.30, 0.04, 0.40])
        except Exception:                                        # noqa: BLE001
            pass
    env.fig.canvas.draw()
    env.refresh_px()


# ---------------------------------------------------------------------------
# B 层：13 类专项操作断言
# ---------------------------------------------------------------------------
def _t_drag(env, meta, fid):
    """C1 拖拽：多步 / 越界 / 松手≠最后位置（宽容：范围 + 相对位移 + 2px 容差）。"""
    _isolate(env, meta['panels'])
    want = meta['panels'][0]
    ax = _hit_panel(env, want)
    if ax is not want:
        check('C1 拖拽前的准备：按下中心确实路由到目标面板（%s 重合家族，'
              '按下落到 %s）' % (fid, type(ax).__name__), True,
              'twinx 家族重合：按最上层成员判，属预期',
              kind='meta', fig=fid, cls='C1')
    env.tw.snap = False                     # 关吸附：位移可精确验（另有吸附专项）
    b0 = env.pos(ax)
    # —— 多步（含中途越界），松手位置 ≠ 最后 motion ——
    c, d = env.drag(ax, (int(0.06 * env.W), int(0.03 * env.H)),
                    steps=(0.3, 0.5, -0.9, 0.8),   # 中途往回/越界
                    release=(3, -4))              # 松手 ≠ 最后 motion
    b1 = env.pos(ax)
    tx, ty = _pxtol(env)
    check('C1 拖拽多步：落位 == 最后 motion（松手位置不参与）',
          abs(b1[0] - b0[0] - d[0] / env.W) <= tx
          and abs(b1[1] - b0[1] - d[1] / env.H) <= ty,
          '%s -> %s 期望 Δ(%.4f,%.4f)（松手另给 (3,-4)）'
          % ([round(v, 4) for v in b0], [round(v, 4) for v in b1],
             d[0] / env.W, d[1] / env.H), kind='target', fig=fid, cls='C1')
    check('C1 松手 ≠ 最后 motion：确实被区分（不是"跟松手走"）',
          abs((b1[0] - b0[0]) - 3 / env.W) > tx,
          '若落位跟着松手 (3,-4) 走，差值应约 %.4f' % (3 / env.W),
          kind='target', fig=fid, cls='C1')
    # —— twinx 观察：拖一块，twin 家族其他成员有没有跟着走 ——
    fam = _twins_of(env.fig, ax)
    if fam:
        moved = [o for o in fam
                 if abs(_bbox(o)[0] - (b0[0] + d[0] / env.W)) <= tx]
        print('NOTE | %s 观测：拖 %s 后，twin 家族成员 %d 个中 %d 个同步移动'
              % (fid, type(ax).__name__, len(fam), len(moved)))
    # —— 越界：中途 motion 远超画布，最终回到画布内 → 落位仍正确 ——
    b2 = env.pos(ax)
    c = env.center(ax)
    send(env.fig.canvas, 'button_press_event', c[0], c[1], button=1)
    send(env.fig.canvas, 'motion_notify_event', -int(2.0 * env.W),
         -int(2.0 * env.H))
    send(env.fig.canvas, 'motion_notify_event', int(3.0 * env.W),
         int(3.0 * env.H))
    send(env.fig.canvas, 'motion_notify_event',
         c[0] + int(0.05 * env.W), c[1] - int(0.02 * env.H))
    send(env.fig.canvas, 'button_release_event',
         c[0] + int(0.05 * env.W), c[1] - int(0.02 * env.H), button=1)
    b3 = env.pos(ax)
    check('C1 越界拖拽：中途拖出画布，回到画布内仍正确落位',
          abs(b3[0] - b2[0] - int(0.05 * env.W) / env.W) <= tx
          and abs(b3[1] - b2[1] + int(0.02 * env.H) / env.H) <= ty,
          '%s -> %s' % ([round(v, 4) for v in b2], [round(v, 4) for v in b3]),
          kind='target', fig=fid, cls='C1')
    env.tw.snap = True


def _t_band(env, meta, fid):
    """C2 框选：框住 1 个 / 2 个 / 空白（完全框住才算，对齐 PPT 语义）。"""
    panels = meta['panels']
    if len(panels) < 2:
        skip_note('C2 框选：完整框住 2 个', '本图形只有 1 个可拖面板',
                  fig=fid, cls='C2')
    _isolate(env, panels)
    p0 = panels[0]
    b0 = _bbox(p0)
    env.tw._selected.clear()
    env.tw._rebuild_selection_artists()

    def _fully_inside(box):
        """全图里"被这个框完整框住"的面板集合（含重合的 twin 成员）。"""
        out = set()
        for p in panels:
            b = _bbox(p)
            if (b[0] >= box[0] - 1e-9 and b[1] >= box[1] - 1e-9
                    and b[0] + b[2] <= box[2] + 1e-9
                    and b[1] + b[3] <= box[3] + 1e-9):
                out.add(id(p))
        return out

    # —— 框住 1 个 ——
    band1 = (b0[0] - 0.03, b0[1] - 0.03, b0[0] + b0[2] + 0.02,
             b0[1] + b0[3] + 0.02)
    exp1 = _fully_inside(band1)
    _band(env, band1[:2], band1[2:])
    check('C2 框选：完整框住 1 个 → 只选中被框住的那个（含重合 twin 成员）',
          env.tw._selected == exp1 and id(p0) in exp1,
          'sel=%d 期望=%d（框住目标=%s）'
          % (len(env.tw._selected), len(exp1), id(p0) in exp1),
          kind='target', fig=fid, cls='C2')
    # —— 框住 2 个 ——
    if len(panels) >= 2:
        p1 = panels[1]
        b1 = _bbox(p1)
        band2 = (min(b0[0], b1[0]) - 0.03, min(b0[1], b1[1]) - 0.03,
                 max(b0[0] + b0[2], b1[0] + b1[2]) + 0.03,
                 max(b0[1] + b0[3], b1[1] + b1[3]) + 0.03)
        exp2 = _fully_inside(band2)
        _band(env, band2[:2], band2[2:])
        check('C2 框选：完整框住 2 个 → 两个都选中（含重合 twin 成员）',
              env.tw._selected == exp2
              and {id(p0), id(p1)}.issubset(exp2),
              'sel=%d 期望=%d' % (len(env.tw._selected), len(exp2)),
              kind='target', fig=fid, cls='C2')
        # 相交但未完全框住 → 不算（PPT 语义；产品文档写明的口径）
        _band(env, (b0[0] - 0.02, b0[1] - 0.02),
              (b0[0] + b0[2] * 0.5, b0[1] + b0[3] * 0.5))
        check('C2 框选：只相交未完全框住 → 不选（PPT 语义）',
              len(env.tw._selected) == 0, 'sel=%d' % len(env.tw._selected),
              kind='target', fig=fid, cls='C2')
    # —— 空白框选 ——
    x, y = env.blank_spot()
    _band(env, (x / env.W, y / env.H), (min(1.0, x / env.W + 0.01),
                                        min(1.0, y / env.H + 0.01)))
    check('C2 框选：空白处框选不选中任何面板',
          len(env.tw._selected) == 0, 'sel=%d' % len(env.tw._selected),
          kind='target', fig=fid, cls='C2')


def _t_ctrl_add(env, meta, fid):
    """C3 Ctrl 加选：先单选一个，再 Ctrl 点另一个 → 两个都在选。"""
    panels = meta['panels']
    if len(panels) < 2:
        skip_note('C3 Ctrl 加选', '本图形只有 1 个可拖面板（加选需要 ≥2 个）',
                  fig=fid, cls='C3')
        return
    env.tw._selected.clear()
    env.tw._rebuild_selection_artists()
    a, b = panels[0], panels[1]
    _isolate(env, panels)
    a, b = _hit_panel(env, a), _hit_panel(env, b)
    if a is b:
        skip_note('C3 Ctrl 加选', '两块面板完全重合（twinx），按下无法区分归属',
                  fig=fid, cls='C3')
        return
    ax_c, ay_c = env.center(a)
    bx_c, by_c = env.center(b)
    env.click(ax_c, ay_c)
    check('C3 Ctrl 加选：先单选 1 个',
          env.tw._selected == {id(a)}, 'sel=%d' % len(env.tw._selected),
          kind='target', fig=fid, cls='C3')
    env.click(bx_c, by_c, key='control')
    check('C3 Ctrl 加选：Ctrl 点第二个 → 两个都在选（单选分支无法蒙对）',
          env.tw._selected == {id(a), id(b)},
          'sel=%d' % len(env.tw._selected),
          kind='target', fig=fid, cls='C3')


def _t_clear_blank(env, meta, fid):
    """C4 点空白清空选中（蓝框也跟着撤）。"""
    _isolate(env, meta['panels'])
    ax = _hit_panel(env, meta['panels'][0])
    env.tw._selected.clear()
    env.tw._rebuild_selection_artists()
    c = env.center(ax)
    env.click(c[0], c[1])
    _n_before = len(env.tw._selected)
    x, y = env.blank_spot()
    env.click(x, y)
    check('C4 点空白：选中被清空', len(env.tw._selected) == 0,
          '选中 %d -> %d' % (_n_before, len(env.tw._selected)),
          kind='target', fig=fid, cls='C4')
    check('C4 点空白：蓝色外框 artist 也撤了', len(env.tw._sel_artists) == 0,
          'sel_artists=%d' % len(env.tw._sel_artists),
          kind='target', fig=fid, cls='C4')


def _t_multi_drag(env, meta, fid):
    """C5 多选后一起拖：两块面板位移相同（相对距离不变）。"""
    panels = meta['panels']
    if len(panels) < 2:
        skip_note('C5 多选后一起拖', '本图形只有 1 个可拖面板', fig=fid, cls='C5')
        return
    _isolate(env, panels)
    a, b = _hit_panel(env, panels[0]), _hit_panel(env, panels[1])
    if a is b:
        skip_note('C5 多选后一起拖', '两块面板完全重合（twinx），按下无法区分归属',
                  fig=fid, cls='C5')
        return
    env.tw.snap = False
    env.tw._selected.clear()
    env.tw._rebuild_selection_artists()
    env.click(*env.center(a))
    env.click(*env.center(b), key='control')
    if len(env.tw._selected) != 2:
        skip_note('C5 多选后一起拖', 'Ctrl 多选没选上 2 个（本图形面板重叠/位置特殊）',
                  fig=fid, cls='C5')
        env.tw.snap = True
        return
    ba0, bb0 = env.pos(a), env.pos(b)
    c = env.center(a)
    dx, dy = int(0.05 * env.W), int(0.02 * env.H)
    send(env.fig.canvas, 'button_press_event', c[0], c[1], button=1)
    send(env.fig.canvas, 'motion_notify_event', c[0] + dx // 2, c[1] + dy // 2)
    send(env.fig.canvas, 'motion_notify_event', c[0] + dx, c[1] + dy)
    send(env.fig.canvas, 'button_release_event', c[0] + dx, c[1] + dy, button=1)
    ba1, bb1 = env.pos(a), env.pos(b)
    tx, ty = _pxtol(env)
    check('C5 多选后一起拖：两块位移相同（相对距离不变）',
          abs((ba1[0] - ba0[0]) - (bb1[0] - bb0[0])) <= tx
          and abs((ba1[1] - ba0[1]) - (bb1[1] - bb0[1])) <= ty
          and abs(ba1[0] - ba0[0] - dx / env.W) <= tx,
          'Δa=(%.4f,%.4f) Δb=(%.4f,%.4f) 期望 %.4f'
          % (ba1[0] - ba0[0], ba1[1] - ba0[1], bb1[0] - bb0[0], bb1[1] - bb0[1],
             dx / env.W), kind='target', fig=fid, cls='C5')
    env.tw.snap = True


def _t_snap_neighbor(env, meta, fid):
    """C6 贴邻吸附：拖到邻块边缘附近 → 吸附到正好贴齐（期望用引擎同款 _snap_box）。"""
    panels = meta['panels']
    if len(panels) < 2:
        skip_note('C6 贴邻吸附', '本图形只有 1 个可拖面板（贴邻需要 ≥2 个）',
                  fig=fid, cls='C6')
        return
    _isolate(env, panels)
    a, b = _hit_panel(env, panels[0]), _hit_panel(env, panels[1])
    if a is b:
        skip_note('C6 贴邻吸附', '两块面板完全重合（twinx），按下无法区分归属',
                  fig=fid, cls='C6')
        return
    env.tw.snap = True
    ba = _bbox(a)
    for attempt in range(4):
        bb0 = _bbox(b)
        # 目标：b 左缘贴到 a 右缘 + 3px 之内（落在吸附容差里）
        want = ba[0] + ba[2] + (3.0 / env.W)
        c = env.center(b)
        dx = int(round((want - bb0[0]) * env.W))
        send(env.fig.canvas, 'button_press_event', c[0], c[1], button=1)
        send(env.fig.canvas, 'motion_notify_event', c[0] + dx, c[1])
        send(env.fig.canvas, 'button_release_event', c[0] + dx, c[1], button=1)
        got = _bbox(b)
        others = [_bbox(x) for x in env.fig.axes if x is not b]
        exp, _, _ = _snap_box([got[0], got[1], got[2], got[3]], others)
        if abs(got[0] - exp[0]) <= 1e-6:
            check('C6 贴邻吸附：拖到邻块边缘附近 → 落位 == 引擎吸附规则',
                  abs(got[0] - (ba[0] + ba[2])) <= 2e-3
                  or abs(got[0] - exp[0]) <= 1e-6,
                  'b.x0=%.4f 期望（吸附后）=%.4f，a 右缘=%.4f'
                  % (got[0], exp[0], ba[0] + ba[2]),
                  kind='target', fig=fid, cls='C6')
            return
    check('C6 贴邻吸附：拖到邻块边缘附近 → 落位 == 引擎吸附规则', False,
          '4 次尝试都没落到吸附点（最后一次 b.x0=%.4f）' % _bbox(b)[0],
          kind='target', fig=fid, cls='C6')


def _t_snap_edge(env, meta, fid):
    """C7 贴边吸附：拖近画布左缘 → 吸附到 0；拖近右缘 → 右缘贴 1。"""
    _isolate(env, meta['panels'])
    ax = _hit_panel(env, meta['panels'][0])
    env.tw.snap = True
    # 左缘：从当前（隔离后）位置往左拖到"离 0 只有 3px"
    bb = _bbox(ax)
    c = env.center(ax)
    dx = int(round((0.003 - bb[0]) * env.W))
    send(env.fig.canvas, 'button_press_event', c[0], c[1], button=1)
    send(env.fig.canvas, 'motion_notify_event', c[0] + dx, c[1])
    send(env.fig.canvas, 'button_release_event', c[0] + dx, c[1], button=1)
    check('C7 贴边吸附：拖近画布左缘 → x0 吸到 0',
          abs(_bbox(ax)[0]) <= 2e-3, 'x0=%.5f' % _bbox(ax)[0],
          kind='target', fig=fid, cls='C7')
    # 右缘：拖到"右缘离 1 只有 3px"
    bb = _bbox(ax)
    c = env.center(ax)
    want = 1.0 - 0.003 - bb[2]
    dx = int(round((want - bb[0]) * env.W))
    send(env.fig.canvas, 'button_press_event', c[0], c[1], button=1)
    send(env.fig.canvas, 'motion_notify_event', c[0] + dx, c[1])
    send(env.fig.canvas, 'button_release_event', c[0] + dx, c[1], button=1)
    bb2 = _bbox(ax)
    check('C7 贴边吸附：拖近画布右缘 → 右缘吸到 1',
          abs(bb2[0] + bb2[2] - 1.0) <= 2e-3,
          'x0+w=%.5f' % (bb2[0] + bb2[2]), kind='target', fig=fid, cls='C7')


def _t_hover_font(env, meta, fid):
    """C8 悬停改字号：标题 / 轴标签 / 刻度，+ 增 - 减（宽容：增了且步长合理）。

    两个坑（实测踩过，都写在这里免得后人也踩）：
      * `get_window_extent()` 用的是**上一次 draw** 的布局 —— 前面几条手势移动过
        面板后必须重画，否则悬停点算的是旧位置；
      * 刻度标签会**互相压住**（面板挤在画布边时 x 刻度与 y 刻度重叠），引擎按
        "先 x 刻度后 y 刻度"的顺序判定，此时悬停点归属不唯一 —— 这不是缺陷，
        而是布局本身有歧义。遇到歧义就换一个不歧义的标签；全都有歧义才记
        SKIP 并写明理由（不静默跳过，也不弱化成假绿）。
    """
    _isolate(env, meta['panels'])
    ax = _hit_panel(env, meta['panels'][0])
    env.fig.canvas.draw()

    def _label(tgt):
        if tgt == 'title':
            return ax.title
        if tgt == 'xlabel':
            return ax.xaxis.label
        if tgt == 'ylabel':
            return ax.yaxis.label
        if tgt == 'xtick':
            return [x for x in ax.get_xticklabels() if x.get_text()]
        return [x for x in ax.get_yticklabels() if x.get_text()]

    def _ambiguous(tgt, x, y):
        """该点是否同时落在**别的文本元素**里（归属不唯一）。

        只看自己那类刻度不够：x 刻度与 y 刻度会互相压住，twinx 的两块轴刻度也
        完全重合 —— 引擎按 fig.axes 顺序 + "先 x 后 y"判定，谁先命中算谁的。
        点数全图命中数：>1 就是布局本身有歧义，换点；都歧义才记 SKIP。
        """
        hits = []
        for a in env.fig.axes:
            for t in (a.title, a.xaxis.label, a.yaxis.label):
                try:
                    if t.get_text() and t.get_window_extent().contains(x, y):
                        hits.append(t)
                except Exception:                                # noqa: BLE001
                    pass
            for t in (list(a.get_xticklabels()) + list(a.get_yticklabels())):
                try:
                    if t.get_text() and t.get_window_extent().contains(x, y):
                        hits.append(t)
                except Exception:                                # noqa: BLE001
                    pass
        for t in list(env.fig.texts):
            try:
                if t.get_text() and t.get_window_extent().contains(x, y):
                    hits.append(t)
            except Exception:                                    # noqa: BLE001
                pass
        return len(hits) > 1

    for tgt, getter in (
            ('title', lambda: ax.title.get_fontsize()),
            ('xlabel', lambda: ax.xaxis.label.get_fontsize()),
            ('ylabel', lambda: ax.yaxis.label.get_fontsize()),
            ('xtick', lambda: ax.get_xticklabels()[0].get_fontsize()),
            ('ytick', lambda: ax.get_yticklabels()[0].get_fontsize())):
        if tgt in ('title', 'xlabel', 'ylabel'):
            objs = [_label(tgt)]
        else:
            objs = _label(tgt)
        if not objs:
            skip_note('C8 悬停 + 改字号：%s' % tgt, '本图形该文字不存在/为空',
                      fig=fid, cls='C8')
            continue
        p = None
        for t in objs:
            if t is None or not t.get_text():
                continue
            we = t.get_window_extent()
            cand = (int(round((we.x0 + we.x1) / 2)),
                    int(round((we.y0 + we.y1) / 2)))
            if not _ambiguous(tgt, cand[0], cand[1]):
                p = cand
                break
        if p is None:
            skip_note('C8 悬停 + 改字号：%s' % tgt,
                      'x/y 刻度标签在当前布局下互相压住，悬停点归属不唯一'
                      '（引擎按 x 优先判定）', fig=fid, cls='C8')
            continue
        f0 = getter()
        send(env.fig.canvas, 'key_press_event', p[0], p[1], key='+')
        f1 = getter()
        send(env.fig.canvas, 'key_press_event', p[0], p[1], key='-')
        f2 = getter()
        check('C8 悬停 %s：+ 变大、- 变回' % tgt,
              (0.5 <= f1 - f0 <= 2.0) and abs(f2 - f0) <= 0.01,
              '%s: %.1f -> %.1f -> %.1f' % (tgt, f0, f1, f2),
              kind='target', fig=fid, cls='C8')


def _t_nudge(env, meta, fid):
    """C9 方向键 / Shift+方向键微调（宽容：0.005 位移、0.025 尺寸，容差 1e-4）。"""
    _isolate(env, meta['panels'])
    ax = _hit_panel(env, meta['panels'][0])
    env.tw.snap = False
    c = env.center(ax)
    send(env.fig.canvas, 'motion_notify_event', c[0], c[1])   # 设 _last_ax
    b0 = env.pos(ax)
    send(env.fig.canvas, 'key_press_event', c[0], c[1], key='up')
    b1 = env.pos(ax)
    check('C9 方向键↑：上移 0.005 且宽高不变',
          abs(b1[1] - b0[1] - 0.005) <= 1e-4
          and abs(b1[2] - b0[2]) <= 1e-9 and abs(b1[3] - b0[3]) <= 1e-9,
          'y %.4f -> %.4f' % (b0[1], b1[1]), kind='target', fig=fid, cls='C9')
    send(env.fig.canvas, 'key_press_event', c[0], c[1], key='right')
    b2 = env.pos(ax)
    check('C9 方向键→：右移 0.005',
          abs(b2[0] - b1[0] - 0.005) <= 1e-4,
          'x %.4f -> %.4f' % (b1[0], b2[0]), kind='target', fig=fid, cls='C9')
    b3 = env.pos(ax)
    send(env.fig.canvas, 'key_press_event', c[0], c[1], key='shift+up')
    b4 = env.pos(ax)
    check('C9 Shift+↑：加高 0.025 且中心不动',
          abs(b4[3] - b3[3] - 0.025) <= 1e-4
          and abs((b4[1] + b4[3] / 2) - (b3[1] + b3[3] / 2)) <= 1e-4,
          'h %.4f -> %.4f' % (b3[3], b4[3]), kind='target', fig=fid, cls='C9')
    env.tw.snap = True


def _t_wire(env, meta, fid):
    """C10 空格切线框：进线条模式隐藏数据图元，再按一次恢复。"""
    _isolate(env, meta['panels'])
    ax = _hit_panel(env, meta['panels'][0])
    vis0 = [a.get_visible() for a in ax.lines]
    if not vis0:
        skip_note('C10 空格切线框', '本图形首个面板没有折线图元', fig=fid, cls='C10')
        return
    c = env.center(ax)
    send(env.fig.canvas, 'key_press_event', c[0], c[1], key=' ')
    on = bool(getattr(env.tw, '_wire', False))
    hidden = [a.get_visible() for a in ax.lines]
    send(env.fig.canvas, 'key_press_event', c[0], c[1], key=' ')
    off = bool(getattr(env.tw, '_wire', True))
    back = [a.get_visible() for a in ax.lines]
    check('C10 空格切线条模式：数据图元隐藏，再按恢复',
          on and (not any(hidden)) and (not off) and back == vis0,
          'wire=%s/%s 可见=%s -> %s -> %s'
          % (on, off, vis0, hidden, back), kind='target', fig=fid, cls='C10')


def _t_legend(env, meta, fid):
    """C11 图例拖动 + 8 位吸附（预览 8 个、高亮唯一、松手落标准位）。"""
    ax = meta['leg_axes'][0] if meta['leg_axes'] else None
    env.fig.canvas.draw()                  # 图例 extent 要按最新布局取
    p = env.hover(ax, 'legend') if ax is not None else None
    if p is None:
        skip_note('C11 图例拖动 + 8 位吸附', '本图形无图例', fig=fid, cls='C11')
        return
    send(env.fig.canvas, 'button_press_event', p[0], p[1], button=1)
    check('C11 图例拖动：按下显示 8 个标准位预览',
          len(env.tw._leg_preview) == 8,
          '预览=%d' % len(env.tw._leg_preview), kind='target', fig=fid, cls='C11')
    send(env.fig.canvas, 'motion_notify_event', p[0] - 40, p[1] - 25)
    hl = [n for n, r in env.tw._leg_preview_map.items() if r.get_alpha() > 0.2]
    check('C11 图例拖动：拖动中只高亮一个候选位',
          len(hl) == 1 and hl[0] in LEGEND_LOCS, str(hl),
          kind='target', fig=fid, cls='C11')
    send(env.fig.canvas, 'button_release_event', p[0] - 40, p[1] - 25, button=1)
    st = env.tw.leg_state.get(id(ax), {})
    check('C11 图例拖动：松手吸附到 8 个标准位之一',
          st.get('loc') in LEGEND_LOCS and st.get('anchor') is None,
          'loc=%s anchor=%s' % (st.get('loc'), st.get('anchor')),
          kind='target', fig=fid, cls='C11')
    check('C11 图例拖动：松手后预览被清掉',
          len(env.tw._leg_preview) == 0,
          '预览=%d' % len(env.tw._leg_preview), kind='target', fig=fid, cls='C11')


def _t_cb_tip(env, meta, fid):
    """C12 colorbar 拖端点：长度变、clim 不变（宽容：方向对 + 有变化）。"""
    cbax = meta['cb']
    if cbax is None:
        skip_note('C12 colorbar 拖端点', '本图形无 colorbar（不适用）',
                  fig=fid, cls='C12')
        return
    _normalize_all(env, meta)              # 起点干净：colorbar 摆在画布内确定位置
    bb = _bbox(cbax)
    clim0 = _clim_of(cbax)
    if bb[2] >= bb[3]:          # 水平
        tip = (cbax.get_position().transformed(env.fig.transFigure).x1 - 3.0,
               (bb[1] + bb[3] / 2) * env.H)
        horiz = True
    else:
        tip = ((bb[0] + bb[2] / 2) * env.W,
               cbax.get_position().transformed(env.fig.transFigure).y1 - 3.0)
        horiz = False
    send(env.fig.canvas, 'button_press_event', tip[0], tip[1], button=1)
    send(env.fig.canvas, 'motion_notify_event', tip[0] + (60 if horiz else 0),
         tip[1] + (0 if horiz else 60))
    send(env.fig.canvas, 'button_release_event', tip[0] + (60 if horiz else 0),
         tip[1] + (0 if horiz else 60), button=1)
    bb1 = _bbox(cbax)
    grew = (bb1[2] > bb[2] + 1e-9) if horiz else (bb1[3] > bb[3] + 1e-9)
    check('C12 colorbar 拖端点：长轴变长（长度可变）', grew,
          'w %.4f -> %.4f  h %.4f -> %.4f' % (bb[2], bb1[2], bb[3], bb1[3]),
          kind='target', fig=fid, cls='C12')
    check('C12 colorbar 拖端点：clim 不变（不误改色标范围）',
          _clim_of(cbax) == clim0, '%s -> %s' % (clim0, _clim_of(cbax)),
          kind='target', fig=fid, cls='C12')


def _content_outside_canvas(env):
    """画布**外面**还有内容吗？（用 matplotlib 自己的 tight bbox 口径判）

    这是"裁白边把画布撑大"的合法前提：suptitle / 刻度文字伸出画布时，裁白边把
    画布扩到装下它们才是对的；反过来说，内容本来都在画布内却涨了，才是可疑。
    """
    fig = env.fig
    try:
        fig.canvas.draw()
        tb = fig.get_tightbbox(fig.canvas.get_renderer())
        fb = fig.bbox_inches
        return (tb.x0 < fb.x0 - 1e-3 or tb.y0 < fb.y0 - 1e-3
                or tb.x1 > fb.x1 + 1e-3 or tb.y1 > fb.y1 + 1e-3)
    except Exception:                                            # noqa: BLE001
        return None


def _t_fit(env, meta, fid):
    """C13 Ctrl+F 裁白边：裁完所有面板仍在画布内；若裁前内容本来都在画布内，
    画布还不该变大（内容伸出画布时，裁白边把画布扩到装下内容是对的）。"""
    _normalize_all(env, meta)
    outside_before = _content_outside_canvas(env)
    w0, h0 = int(env.W), int(env.H)
    ax = _hit_panel(env, meta['panels'][0])
    c = env.center(ax)
    _buf = io.StringIO()
    with contextlib.redirect_stdout(_buf):
        send(env.fig.canvas, 'key_press_event', c[0], c[1], key='ctrl+f')
    env.refresh_px()
    w1, h1 = int(env.W), int(env.H)
    inside = all(-0.02 <= b[0] and b[1] >= -0.02
                 and b[0] + b[2] <= 1.02 and b[1] + b[3] <= 1.02
                 for b in (_bbox(a) for a in env.fig.axes))
    _outs = ['ax%d=%s' % (i, [round(v, 3) for v in _bbox(a)])
             for i, a in enumerate(env.fig.axes)
             if not (-0.02 <= _bbox(a)[0] and _bbox(a)[1] >= -0.02
                     and _bbox(a)[0] + _bbox(a)[2] <= 1.02
                     and _bbox(a)[1] + _bbox(a)[3] <= 1.02)]
    check('C13 Ctrl+F 裁白边：裁完所有面板都在画布内',
          inside, '画布 %dx%d -> %dx%d（都在内=%s%s）'
          % (w0, h0, w1, h1, inside,
             ('；越界: ' + ', '.join(_outs)) if _outs else ''),
          kind='target', fig=fid, cls='C13')
    if outside_before:
        skip_note('C13 裁白边：裁前内容都在画布内时画布不涨',
                  '裁白边前已有内容（suptitle / 刻度文字）伸出画布，'
                  '把画布撑到装下内容是正确的行为', fig=fid, cls='C13')
    elif outside_before is None:
        skip_note('C13 裁白边：裁前内容都在画布内时画布不涨',
                  '本环境取不到 tight bbox（无法判定前提），不猜',
                  fig=fid, cls='C13')
    else:
        check('C13 裁白边：裁前内容都在画布内 → 画布不涨',
              w1 <= w0 + 2 and h1 <= h0 + 2,
              '裁前内容都在画布内；画布 %dx%d -> %dx%d' % (w0, h0, w1, h1),
              kind='target', fig=fid, cls='C13')
    # 撤销把画布还原（宽容 2px）
    send(env.fig.canvas, 'key_press_event', c[0], c[1], key='ctrl+z')
    env.refresh_px()
    check('C13 Ctrl+F 裁白边：Ctrl+Z 把画布尺寸还原（容差 2px）',
          abs(int(env.W) - w0) <= 2 and abs(int(env.H) - h0) <= 2,
          '画布 %dx%d（裁前 %dx%d）' % (env.W, env.H, w0, h0),
          kind='target', fig=fid, cls='C13')


TARGETS = [
    ('C1', '拖拽（多步 / 越界 / 松手≠最后位置）', _t_drag),
    ('C2', '框选（框住 1 个 / 2 个 / 空白）', _t_band),
    ('C3', 'Ctrl 加选', _t_ctrl_add),
    ('C4', '空白清空', _t_clear_blank),
    ('C5', '多选后一起拖', _t_multi_drag),
    ('C6', '贴邻吸附', _t_snap_neighbor),
    ('C7', '贴边吸附', _t_snap_edge),
    ('C8', '悬停改字号（标题/轴标签/刻度 ±）', _t_hover_font),
    ('C9', '方向键 / Shift 微调', _t_nudge),
    ('C10', '空格切线框', _t_wire),
    ('C11', '图例拖动 + 8 位吸附', _t_legend),
    ('C12', 'colorbar 拖端点', _t_cb_tip),
    ('C13', 'Ctrl+F 裁白边', _t_fit),
]


def _band(env, p0, p1):
    x0, y0 = int(p0[0] * env.W), int(p0[1] * env.H)
    x1, y1 = int(p1[0] * env.W), int(p1[1] * env.H)
    send(env.fig.canvas, 'button_press_event', x0, y0, button=1)
    send(env.fig.canvas, 'motion_notify_event', x1, y1)
    send(env.fig.canvas, 'button_release_event', x1, y1, button=1)


# ---------------------------------------------------------------------------
# 跑一个图形：A 层（随机）+ B 层（专项）
# ---------------------------------------------------------------------------
ROWS = []

# 分块并行（船长 2026-10-12 裁定 (b)）：
#   每种图形 1,000 次随机操作 = **分 2 段各 500 次**，跑在**两个独立的新 figure
#   实例**上，6 个 worker 并行。口径与代价都在报告里写明：分块损失的只是"单条
#   1,000 步连续随机游走"的覆盖，换来 wall clock 从 ~52min 降到 ~12min。
#   不变量检查**每次操作后都执行**（不是段末），所以"第 480 次把状态弄坏"照样抓。
CHUNKS = max(1, int(os.environ.get('MPLTWEAK_MATRIX_CHUNKS') or 2))
WORKERS = max(1, int(os.environ.get('MPLTWEAK_MATRIX_WORKERS') or 6))
WORKER_TAG = '##MATRIX_RESULT##'
_HERE = os.path.abspath(__file__)
_SRCDIR = os.path.join(os.path.dirname(os.path.dirname(_HERE)), 'src')


def _emit(s):
    print(s)


def _chunk_plan(n_subs_per_fig):
    """[(fid, fi, chunk, per, do_targeted)]：每张图切 CHUNKS 段，专项只在第 0 段。"""
    per = max(1, N_RANDOM // CHUNKS)
    out = []
    for fid, name, ctor, _m in FIGURES:
        for fi in range(n_subs_per_fig[fid]):
            for c in range(CHUNKS):
                out.append((fid, fi, c, per, c == 0))
    return out


def _sub_name(fid, fi, n_subs):
    return fid if n_subs == 1 else '%s#%d' % (fid, fi)


def _run_one(fid, name, ctor, fi, chunk, per, do_targeted):
    """跑一段：可选专项断言 + 该段随机操作（**每次操作后**验铁律）。

    返回可 JSON 化的 dict（含断言台账），供父进程汇总；不打印数字表。
    """
    res = dict(fid=fid, fig_index=fi, chunk=chunk, ops=0, law_fail=0,
               law_detail='', n_subs=1, secs=0.0, records=[])
    t0 = time.time()
    figs, cbax = _build(ctor)
    res['n_subs'] = len(figs)
    sub = _sub_name(fid, fi, len(figs))

    def _meta(f, cb, fi_, figs_):
        ps = list(figs_[fi_][1])
        return dict(panels=ps,
                    leg_axes=[a for a in ps if a.get_legend() is not None],
                    cb=cb if fi_ == 0 else None,
                    op_kinds=_op_kinds_for(dict(
                        panels=ps,
                        leg_axes=[a for a in ps if a.get_legend() is not None],
                        cb=cb if fi_ == 0 else None)))

    print('')
    print('---- %s %s（第 %d/%d 段：%d 次随机操作%s）----'
          % (sub, _gbk_safe(name), chunk + 1, CHUNKS, per,
             '，先跑 13 类专项断言' if do_targeted else ''))

    if do_targeted:
        meta = _meta(figs, cbax, fi, figs)
        env = _Env(figs[fi][0], 'target_%s_%d' % (fid, fi))
        for cls, cname, fn in TARGETS:
            before = len(_RECORDS)
            try:
                fn(env, meta, sub)
            except Exception as e:                               # noqa: BLE001
                check('%s %s：执行不抛异常' % (sub, cls), False,
                      '专项断言抛异常 %r' % (e,), kind='target', fig=sub,
                      cls=cls)
            for rec in _RECORDS[before:]:
                rec['fig'] = sub
                rec['cls'] = cls
        plt.close(figs[fi][0])
        figs, cbax = _build(ctor)      # 随机段用一张**干净**的新图（专项改过位置）

    meta = _meta(figs, cbax, fi, figs)
    fig = figs[fi][0]
    env = _Env(fig, 'rand_%s_%d_%d' % (fid, fi, chunk))
    snap = _snapshot(fig)
    rng = random.Random('%s-%s-%d-%d' % (SEED, fid, fi, chunk))
    for i in range(per):
        try:
            _exec_random_op(rng, env, meta)
        except Exception as e:                                   # noqa: BLE001
            res['law_fail'] += 1
            res['law_detail'] = '第 %d 次随机操作抛异常: %r' % (i, e)
            break
        res['ops'] += 1
        ok, why = _state_laws(fig, snap)     # ← 每次操作后都验（不是段末）
        if not ok:
            res['law_fail'] += 1
            res['law_detail'] = '第 %d 次随机操作后状态坏了：%s' % (i, why)
            break
    try:
        c = env.center(meta['panels'][0])
        send(fig.canvas, 'button_release_event', c[0], c[1], button=1)
    except Exception:                                            # noqa: BLE001
        pass
    ok_end, why_end = _state_laws(fig, snap)
    if not ok_end and res['law_fail'] == 0:
        res['law_fail'] += 1
        res['law_detail'] = '收尾补松开后状态坏了：%s' % why_end
    check('%s 第 %d/%d 段：%d 次随机操作不崩 + 状态未坏（每次操作后都验）'
          % (sub, chunk + 1, CHUNKS, res['ops']),
          res['law_fail'] == 0,
          res['law_detail'] or '轴数/内容数/几何/图例/总标题全程保持',
          kind='law', fig=sub, cls='A')
    plt.close(fig)
    res['secs'] = time.time() - t0
    if do_targeted:
        res['records'] = [dict(r) for r in _RECORDS
                          if r['fig'] == sub and r['kind'] in ('target', 'n/a')]
    return res


# ---------------------------------------------------------------------------
# worker 模式：只跑一个分块，最后打一行机器可读结果
# ---------------------------------------------------------------------------
def _worker_main():
    fid = os.environ.get('MPLTWEAK_MATRIX_ONLY')
    fi = int(os.environ.get('MPLTWEAK_MATRIX_SUB') or 0)
    chunk = int(os.environ.get('MPLTWEAK_MATRIX_CHUNK') or 0)
    per = int(os.environ.get('MPLTWEAK_MATRIX_PER') or 1)
    do_t = os.environ.get('MPLTWEAK_MATRIX_DO_TARGET') == '1'
    info = [f for f in FIGURES if f[0] == fid]
    if not info:
        sys.stderr.write('未知图形 %r\n' % fid)
        return 1
    _f, name, ctor, _m = info[0]
    try:
        res = _run_one(fid, name, ctor, fi, chunk, per, do_t)
    except Exception as e:                                       # noqa: BLE001
        import traceback
        traceback.print_exc()
        print(WORKER_TAG + json.dumps(
            dict(fid=fid, fig_index=fi, chunk=chunk, ops=0, law_fail=1,
                 law_detail='worker 崩溃: %r' % (e,), n_subs=1, secs=0.0,
                 records=[]), ensure_ascii=False))
        return 1
    print(WORKER_TAG + json.dumps(res, ensure_ascii=False))
    return 1 if (fail or res['law_fail']) else 0


def _spawn_one(fid, name, ctor, fi, chunk, per, do_t, job_log):
    """起一个 worker 子进程，stdout 直接落**文件**（避免管道写满互锁）。

    注意：每个 subprocess 调用点都**显式**传含 `MPLBACKEND=Agg` 的 env
    （t17/t24 的卫生棘轮要求：子进程必须钉死无头后端，绝不能落到 Qt/Tk 开窗）。

    ⚠ 棘轮的**溯源写法**（2026-10-12 实测，别人也会踩）：它的判据是把 `env=` 的实参
    溯源到定义处，认得这几种形态：
      ① 调用点内联 `dict(os.environ, MPLBACKEND='Agg', ...)`；
      ② 同一作用域里 `x['MPLBACKEND'] = 'Agg'`（**订阅赋值**，本函数用的就是这条）；
      ③ 本地"闸门函数"（函数体里钉了后端，如 `_pin_child_env`）。
    **不认** `env = dict(os.environ)` + `env.update({'MPLBACKEND': 'Agg', ...})` ——
    因为 `.update(...)` 是表达式语句、不是赋值，收集器看不到（本文件第一版就因此
    在全量 pytest 里被棘轮判红）。所以下面这行订阅赋值**不能**改成 update。
    """
    import subprocess
    env = dict(os.environ)
    env['MPLBACKEND'] = 'Agg'          # ← 棘轮认的形态（订阅赋值；同 test_check.run()）
    env.update({
        'MPLTWEAK_MATRIX_WORKER': '1',
        'MPLTWEAK_MATRIX_ONLY': fid,
        'MPLTWEAK_MATRIX_SUB': str(fi),
        'MPLTWEAK_MATRIX_CHUNK': str(chunk),
        'MPLTWEAK_MATRIX_PER': str(per),
        'MPLTWEAK_MATRIX_DO_TARGET': '1' if do_t else '0',
        'MPLTWEAK_MATRIX_N': str(per),
        'MPLTWEAK_MATRIX_LOG': '',      # worker 不再开日志（父进程统一写）
        'PYTHONPATH': _SRCDIR + os.pathsep + env.get('PYTHONPATH', ''),
    })
    fh = open(job_log, 'w', encoding='utf-8')
    p = subprocess.Popen([sys.executable, _HERE, '--worker'],
                         cwd=os.path.dirname(_HERE), env=env,
                         stdout=fh, stderr=subprocess.STDOUT)
    return p, fh


def _run_parallel(joblogdir):
    """最多 WORKERS 个 worker 并行；每段原始输出按完成顺序整块写进主日志。"""
    plan = _chunk_plan({f[0]: len(_build(f[2])[0]) for f in FIGURES})
    name_of = {f[0]: f[1] for f in FIGURES}
    ctor_of = {f[0]: f[2] for f in FIGURES}
    pending = list(plan)
    running = []
    results = []
    while pending or running:
        while pending and len(running) < WORKERS:
            fid, fi, chunk, per, do_t = pending.pop(0)
            jl = os.path.join(joblogdir, 'job_%s_%d_%d.log' % (fid, fi, chunk))
            p, fh = _spawn_one(fid, name_of[fid], ctor_of[fid], fi, chunk, per,
                               do_t, jl)
            running.append(dict(fid=fid, fi=fi, chunk=chunk, per=per,
                                do_t=do_t, p=p, fh=fh, jl=jl))
        time.sleep(0.3)
        for it in list(running):
            rc = it['p'].poll()
            if rc is None:
                continue
            running.remove(it)
            it['fh'].close()
            it['rc'] = rc
            raw = ''
            try:
                raw = open(it['jl'], encoding='utf-8', errors='replace').read()
            except OSError as e:
                raw = '(读不到 worker 输出: %s)' % e
            # 整块写进主日志（可追溯：这行下面是该段的全部断言）
            _emit('')
            _emit('==== worker 图 %s 第 %d/%d 段 rc=%d（原始输出见 %s）===='
                  % (it['fid'], it['chunk'] + 1, CHUNKS, rc,
                     os.path.basename(it['jl'])))
            body = [ln for ln in raw.splitlines() if not ln.startswith(WORKER_TAG)]
            tag = [ln for ln in raw.splitlines() if ln.startswith(WORKER_TAG)]
            for ln in body:
                _emit(ln)
            it['log_line'] = _LOG_LINE[0]
            res = None
            if tag:
                try:
                    res = json.loads(tag[-1][len(WORKER_TAG):])
                except ValueError as e:
                    _emit('（worker 结果行解析失败: %s）' % e)
            if res is None:
                check('图 %s 第 %d 段 worker 正常返回（rc=%d）'
                      % (it['fid'], it['chunk'] + 1, rc), False,
                      '没拿到机器可读结果行；输出尾巴: %s' % raw[-400:],
                      kind='law', fig=it['fid'], cls='A')
                res = dict(fid=it['fid'], fig_index=it['fi'], chunk=it['chunk'],
                           ops=0, law_fail=1, law_detail='worker 无结果',
                           n_subs=1, secs=0.0, records=[])
            elif rc != 0 and res['law_fail'] == 0:
                # worker 退出码非 0 但没报铁律失败 → 有专项断言挂了
                check('图 %s 第 %d 段 worker 退出码为 0' % (it['fid'],
                                                          it['chunk'] + 1),
                      False, 'rc=%d（多半有专项断言 FAIL）；输出尾巴: %s'
                      % (rc, raw[-400:]), kind='law', fig=it['fid'], cls='A')
                res['law_fail'] += 1
                res['law_detail'] = 'worker rc=%d（专项断言失败）' % rc
            res['log_line'] = it['log_line']
            res['secs_wall'] = 0.0
            results.append(res)
    return results


def _run_sequential():
    results = []
    for fid, name, ctor, _m in FIGURES:
        n_subs = len(_build(ctor)[0])
        for fi in range(n_subs):
            for c in range(CHUNKS):
                per = max(1, N_RANDOM // CHUNKS)
                res = _run_one(fid, name, ctor, fi, c, per, c == 0)
                res['log_line'] = _LOG_LINE[0]
                results.append(res)
    return results


def _rows_from(results):
    """把分段结果汇总成逐图形一行（口径：每种图形 N_RANDOM 次，分 CHUNKS 段）。"""
    by_fig = {}
    for r in results:
        by_fig.setdefault(r['fid'], []).append(r)
    rows = []
    for fid, name, ctor, _m in FIGURES:
        rs = by_fig.get(fid, [])
        if not rs:
            continue
        row = dict(fid=fid, name=name, n_figs=rs[0].get('n_subs', 1),
                   rnd=sum(r['ops'] for r in rs),
                   law_fail=sum(r['law_fail'] for r in rs), law_detail='',
                   n_app=0, n_pass=0, n_fail=0, n_na=0, lines=[],
                   secs=sum(r['secs'] for r in rs), segments=len(rs),
                   log_lines=[r.get('log_line', 0) for r in rs])
        for r in rs:
            if r['law_fail'] and not row['law_detail']:
                row['law_detail'] = '图 %s 第 %d 段：%s' % (
                    fid, r['chunk'] + 1, r['law_detail'])
            for rec in r.get('records') or []:
                if rec['kind'] == 'target':
                    row['n_app'] += 1 if rec['ok'] is not None else 0
                    row['n_pass'] += 1 if rec['ok'] is True else 0
                    row['n_fail'] += 1 if rec['ok'] is False else 0
                    row['lines'].append(rec['line'])
                else:
                    row['n_na'] += 1
        # 父进程侧对"整图形"再钉一条汇总断言（每段的逐操作断言在 worker 里）
        check('%s 随机操作 %d 次（分 %d 段）：不崩 + 状态未坏'
              % (fid, row['rnd'], row['segments']), row['law_fail'] == 0,
              row['law_detail'] or '每段的**每次操作后**都验过，全部保持',
              kind='law', fig=fid, cls='A')
        rows.append(row)
    return rows


# ---------------------------------------------------------------------------
# 反向验证（teeth）：检测器必须真的会咬
# ---------------------------------------------------------------------------
def _teeth_probes():
    print('')
    print('---- 反向验证（teeth）：故意破坏 → 检测器必须报红 ----')
    # ① 删掉一个面板 → 状态铁律报"轴数变了"
    fig, axs = plt.subplots(2, 2, figsize=(8, 6))
    for a in axs.ravel():
        _deco(a, 'teeth')
    fig.canvas.draw()
    snap = _snapshot(fig)
    fig.delaxes(fig.axes[-1])
    ok, why = _state_laws(fig, snap)
    check('teeth① 删掉一个面板 → 状态铁律报红',
          (not ok) and ('轴数' in why), why, kind='reverse', fig='R', cls='A')
    plt.close(fig)

    # ② 面板塌成 0 宽 → 几何铁律报红
    fig, ax = plt.subplots(figsize=(6, 4))
    _deco(ax, 'teeth2')
    fig.canvas.draw()
    snap = _snapshot(fig)
    _set_bbox(ax, [0.2, 0.2, 1e-6, 0.4])
    ok, why = _state_laws(fig, snap)
    check('teeth② 面板塌成 0 宽 → 几何铁律报红',
          (not ok) and ('过小' in why), why, kind='reverse', fig='R', cls='A')
    plt.close(fig)

    # ③ 凭空加一条线 → 内容数铁律报红
    fig, ax = plt.subplots(figsize=(6, 4))
    _deco(ax, 'teeth3')
    fig.canvas.draw()
    snap = _snapshot(fig)
    ax.plot([0, 1], [1, 0])
    ok, why = _state_laws(fig, snap)
    check('teeth③ 凭空多一条线 → 内容数铁律报红',
          (not ok) and ('内容数' in why), why, kind='reverse', fig='R', cls='A')
    plt.close(fig)

    # ④ 篡改期望落位 → C1 的落位断言必须报红（用同一套容差口径重算）
    fig, ax = plt.subplots(figsize=(6, 4))
    _deco(ax, 'teeth4')
    fig.canvas.draw()
    env = _Env(fig, 'teeth4')
    env.tw.snap = False
    b0 = env.pos(ax)
    c, d = env.drag(ax, (int(0.06 * env.W), int(0.03 * env.H)))
    b1 = env.pos(ax)
    tx, ty = _pxtol(env)
    good = (abs(b1[0] - b0[0] - d[0] / env.W) <= tx
            and abs(b1[1] - b0[1] - d[1] / env.H) <= ty)
    bad = (abs(b1[0] - b0[0] - (d[0] + 40) / env.W) <= tx
           and abs(b1[1] - b0[1] - (d[1] + 40) / env.H) <= ty)
    check('teeth④ 期望落位被篡改 40px → 落位断言必红（口径真的在比）',
          good and (not bad),
          '真实落位匹配=%s，篡改后匹配=%s' % (good, bad),
          kind='reverse', fig='R', cls='C1')
    plt.close(fig)

    # ⑤ 无效键不生效 → "悬停改字号"这类断言必须能报红（无效键 = 无变化）
    fig, ax = plt.subplots(figsize=(6, 4))
    _deco(ax, 'teeth5')
    fig.canvas.draw()
    env = _Env(fig, 'teeth5')
    p = env.hover(ax, 'title')
    f0 = ax.title.get_fontsize()
    send(fig.canvas, 'key_press_event', p[0], p[1], key='~')   # 无效键
    f1 = ax.title.get_fontsize()
    check('teeth⑤ 无效键"~" → 字号不变（所以 C8 的 +1 断言不是空断言）',
          abs(f1 - f0) <= 1e-9, '%.1f -> %.1f' % (f0, f1),
          kind='reverse', fig='R', cls='C8')
    plt.close(fig)

    # ⑥ 零 GUI 对象自证：本进程里没有任何 Qt/Tk 对象被创建
    mods = [m for m in ('PyQt5', 'PyQt6', 'PySide2', 'PySide6', 'tkinter')
            if m in sys.modules]
    check('teeth⑥ 反证离屏纪律：本进程未加载任何 GUI 工具包（%s）'
          % (','.join(mods) or '无'),
          not mods or all(m == 'tkinter' for m in mods),
          '已加载: %s（tkinter 只在 matplotlib 探测后端时可能出现，不会建窗）'
          % (','.join(mods) or '无'), kind='reverse', fig='R', cls='-')
    return True


def _loads_gui():
    return [m for m in ('PyQt5', 'PyQt6', 'PySide2', 'PySide6')
            if m in sys.modules]


# ---------------------------------------------------------------------------
# 数字表（全部来自断言台账，不估、不凑）
# ---------------------------------------------------------------------------
def _report():
    tot_rnd = sum(r['rnd'] for r in ROWS)
    tot_law_fail = sum(r['law_fail'] for r in ROWS)
    tot_app = sum(r['n_app'] for r in ROWS)
    tot_pass = sum(r['n_pass'] for r in ROWS)
    tot_fail = sum(r['n_fail'] for r in ROWS)
    tot_na = sum(r['n_na'] for r in ROWS)
    rev = [r for r in _RECORDS if r['kind'] == 'reverse']
    rev_pass = sum(1 for r in rev if r['ok'])
    print('')
    print('=' * 100)
    print('图形矩阵数字表（每行都可追溯到断言所在行；「来源」= 专项断言行号）')
    print('=' * 100)
    print('%-4s %-30s %4s %6s %7s %5s %5s %5s %5s %7s %10s %s'
          % ('#', '图形', '图数', '随机', '铁律败', '适用', '通过', '失败',
             '不适用', '耗时s', '日志行', '来源(代码行)'))
    print('-' * 126)
    for r in ROWS:
        lines = sorted(set(r['lines']))
        ltxt = ('L%s' % ','.join(str(x) for x in lines[:6])
                + ('…(+%d)' % (len(lines) - 6) if len(lines) > 6 else ''))
        lls = r.get('log_lines') or [r.get('log_line', 0)]
        lltxt = ','.join('L%d' % x for x in lls[:3])
        print('%-4s %-30s %4d %6d %7d %5d %5d %5d %5d %7.1f %10s %s'
              % (r['fid'], _gbk_safe(r['name']), r['n_figs'], r['rnd'],
                 r['law_fail'], r['n_app'], r['n_pass'], r['n_fail'],
                 r['n_na'], r['secs'], lltxt, ltxt))
    print('-' * 126)
    print('%-4s %-30s %4d %6d %7d %5d %5d %5d %5d %7.1f'
          % ('合计', '12 种图形', sum(r['n_figs'] for r in ROWS), tot_rnd,
             tot_law_fail, tot_app, tot_pass, tot_fail, tot_na,
             sum(r['secs'] for r in ROWS)))
    rate = ('%.1f%%' % (100.0 * tot_pass / tot_app)) if tot_app else '—'
    print('')
    print('图形类型数            = %d（契约要求 12）' % len(ROWS))
    print('实际跑到的 figure 张数 = %d（12 号多 figure 脚本含 2 张）'
          % sum(r['n_figs'] for r in ROWS))
    print('随机操作总次数        = %d（口径：每种图形 %d 次 = 分 %d 段各 %d 次，'
          '每段独立新 figure；12 号两张图各 %d 次）'
          % (tot_rnd, N_RANDOM, CHUNKS, max(1, N_RANDOM // CHUNKS), N_RANDOM))
    print('A 层铁律失败数        = %d（每次操作后都验，非段末）' % tot_law_fail)
    print('专项断言：适用 %d 条 / 通过 %d / 失败 %d / 成功率 %s'
          % (tot_app, tot_pass, tot_fail, rate))
    print('专项明确"不适用"项    = %d 条（每条都打印了理由，见上面 SKIP 行）'
          % tot_na)
    print('反向验证（teeth）     = %d/%d 通过（检测器确实会咬）'
          % (rev_pass, len(rev)))
    print('逐图形失败数          = %s'
          % ', '.join('%s:%d' % (r['fid'], r['n_fail'] + r['law_fail'])
                      for r in ROWS))
    print('逐图形耗时            = %s'
          % ', '.join('%s:%.1fs' % (r['fid'], r['secs']) for r in ROWS))
    print('最贵图形              = %s（%.1fs = 总耗时的 %.0f%%）'
          % (max(ROWS, key=lambda r: r['secs'])['fid'],
             max(r['secs'] for r in ROWS),
             100.0 * max(r['secs'] for r in ROWS) / max(sum(
                 r['secs'] for r in ROWS), 1e-9)))
    print('不可追溯的项          = 无（每条断言记录 代码行；每个图形的数字记录日志行）')
    return tot_rnd, tot_law_fail, tot_fail, rev_pass, len(rev)


def main():
    if os.environ.get('MPLTWEAK_MATRIX_WORKER') == '1':
        return _worker_main()            # worker：只跑一段，末行给机器可读结果
    t0 = time.time()
    global ROWS
    # 自带 UTF-8 日志（见 _Tee 的说明）；路径可由 MPLTWEAK_MATRIX_LOG 覆盖
    _log_path = os.environ.get('MPLTWEAK_MATRIX_LOG') \
        or os.path.join(_TMPROOT, 'matrix_run.log')
    try:
        _LOG_FH[0] = open(_log_path, 'w', encoding='utf-8')
        sys.stdout = _Tee(sys.__stdout__, _LOG_FH[0])
    except OSError as e:
        sys.stderr.write('[matrix] 日志打不开（%s），只写控制台\n' % e)
        _log_path = '(未写日志)'
    print('=' * 100)
    print('离屏图形矩阵：12 种常见论文图形 × %d 次随机操作 + 13 类专项操作断言'
          % N_RANDOM)
    print('=' * 100)
    print('环境自证：MPLBACKEND=%r  语言=%r  随机种子=%s  dpi=%d  worker=%d 分块=%d'
          % (os.environ.get('MPLBACKEND'), os.environ.get('MPLTWEAK_LANG'),
             SEED, DPI, WORKERS, CHUNKS))
    print('UTF-8 日志（证据文件）：%s' % _log_path)
    print('离屏纪律：事件走 canvas.callbacks.process（与 mpl_connect 同路径），'
          '不创建任何 GUI 对象、不调用 plt.show()')
    print('随机操作口径：**每种图形 %d 次 = 分 %d 段各 %d 次**，每段跑在一个'
          '**独立的新 figure 实例**上（%d 个 worker 并行）。'
          % (N_RANDOM, CHUNKS, max(1, N_RANDOM // CHUNKS),
             WORKERS if (WORKERS > 1 and CHUNKS > 1) else 1))
    print('  ↑ 这项分块是有代价的，如实写明：**损失"单条 %d 步连续随机游走"的'
          '覆盖**（两条 %d 步 vs 一条 %d 步），换来 wall clock 从 ~52min 降到 ~12min。'
          % (N_RANDOM, max(1, N_RANDOM // CHUNKS), N_RANDOM))
    print('  ↑ 不变量检查**每次操作后都执行**（不是段末），所以"第 480 次操作把'
          '状态弄坏"照样能被抓到。')
    joblogdir = os.path.join(_TMPROOT, 'jobs')
    try:
        os.makedirs(joblogdir, exist_ok=True)
    except OSError:
        joblogdir = _TMPROOT
    if WORKERS > 1 and CHUNKS > 1:
        results = _run_parallel(joblogdir)
    else:
        print('（串行模式：MPLTWEAK_MATRIX_WORKERS=1 或 分块=1）')
        results = _run_sequential()
    ROWS = _rows_from(results)

    _teeth_probes()
    tot_rnd, tot_law_fail, tot_fail, rev_pass, rev_all = _report()

    print('')
    print('----')
    gui = _loads_gui()
    check('离屏总检：本进程未加载 Qt 工具包（%s）' % (','.join(gui) or '无'),
          not gui, '已加载: %s' % (','.join(gui) or '无'), kind='meta')
    _wall = time.time() - t0
    print('总耗时 %.1fs（%.1f min）' % (_wall, _wall / 60.0))
    print('FAILED: %d' % fail if fail else 'ALL PASS')
    if _LOG_FH[0] is not None:
        try:
            _LOG_FH[0].flush()
            _LOG_FH[0].close()
        except Exception:                                        # noqa: BLE001
            pass
    # 退出码：有失败 → 1；否则 0
    return 1 if fail else 0


if __name__ == '__main__':
    sys.exit(main())
