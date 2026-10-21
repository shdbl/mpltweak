# -*- coding: utf-8 -*-
"""随机手势 fuzz（猴子乱改图）—— 交互层事件序列不变量测试。

评审批评：产品卖点是「像 PPT 一样拖拽调图」，但交互层（拖拽/吸附/撤销）没有自动化
测试兜底，导致本版 #1（Ctrl+F 裁白边）、#2（拖拽错位）两个交互 bug 漏到实机。
本套件补这块：**让程序随机乱按鼠标键盘**，喂给 toolbox 的合成事件管线，验证四条
不变量在任意手势序列下都成立。

基础设施（照抄 tests/test_events.py，全程 Agg 无头、不弹窗、不抢鼠标）：
  send(canvas, name, x, y, ...) 构造 MouseEvent/KeyEvent 走 canvas.callbacks.process，
  与真实 mpl_connect 路径一致。

不变量（本套件钉死的四条铁律）：
  ① 不崩（no-crash）：任意序列跑完不抛异常（捕获即 FAIL，并打印触发序列）。
  ② 撤销/重做往返一致（undo round-trip）：记录初始布局 → 随机序列 → 反复 Ctrl+Z 到
     栈底 → 布局 == 初始（±1e-9）；再反复 Ctrl+Y → 布局 == 撤销前（画布尺寸也还原）。
  ③ 松手落位（drag landing）：完整拖拽（按下→多次移动→松开）结束后，面板位置 ==
     最后 motion 对应的期望位置（吸附命中时按 _snap_box 的吸附规则算）。
  ④ 几何合法（geometry）：所有面板 w/h > MIN_SIZE（colorbar 用 MIN_SIZE_CB）、
     坐标不含 NaN/Inf、轴数量与初始一致（没有面板丢失）。

覆盖的随机手势（确定性命名用例 + hypothesis 生成）：
  拖拽（按下→1~8 次 motion（含越界：负坐标/超画布）→松开，松开位置可能≠最后 motion）、
  单击（按下即抬起）、Ctrl/Shift 多选 + 多选拖拽、贴边/贴邻吸附、悬停标题/轴标签/
  刻度 + +/- 调字号、方向键微调、Shift+方向键微调尺寸、空格切线框模式、Ctrl+Z/Ctrl+Y
  撤销重做、图例拖拽与 8 位吸附、colorbar 拖端点调长度、无意义乱序（裸 motion、
  两键同按、连按不松开、无按下就松开）。

关键口径：
* **事件坐标一律整数像素**：matplotlib 合成 MouseEvent 会把小数像素坐标截断成 int
  （实测 493.5 -> 493），松手落位期望值必须用整数像素差反推，否则假红。
* 松手落位期望：origin + (最后 motion 像素 - 按下像素)/画布；吸附开启时再套
  _snap_box（与引擎同一条纯函数路径，验证"引擎确实按吸附规则落位"）。
* 撤销往返的 `_restore_snap` 顺带还原画布尺寸（Ctrl+F 改过的画布也能全撤回去）。
* hypothesis 只生成"固定结构 + 参数随机"的手势（模板保证能跑，不随机拼代码）。

关键口径 · 铁律②第 2 条腿的基线：**历史末端**（t26 实测，2026-10-12）：
* 第 2 条腿（Ctrl+Y 到栈顶）的期望 = **历史末端状态** `forward_end`（每执行完一个 op，
  若 `tw._redo` 为空就记一次实测布局），**不是**"跑完 ops 时游标处的状态"（`post`）。
* 两者在"ops 末尾没有 undo/redo"时**逐字节相等**（所以平时全绿）；**末尾含显式 undo**
  时不同：游标停在半路，抽干 `_redo` 走到的是末端（Ctrl+Y 本来就该往前走）——
  拿游标态当期望必然假红。这是**测试口径**问题，不是引擎缺陷（别再去查 src）。
* 实测（脚本与原始日志见 t26 交付物；下面是 `ax0` 的 y0；hypothesis 反例
  `[('two_keys', 0, 'up', '+'), ('undo',)]`，seed 52571158591668349824887539026665539149）：

  | 变体 | ①全撤销==初始 | ②全重做==游标态(旧) | ③全重做==末端(新) |
  |---|---|---|---|
  | 反例原样 | PASS | FAIL | PASS |
  | 按键间插一次 draw | PASS | FAIL | PASS |
  | 按键间插 0.7s（打破 0.6s seal 合并窗口） | PASS | FAIL | PASS |
  | 只按 up + undo | PASS | FAIL | PASS |
  | 只按 '+' + undo | PASS | PASS | PASS |
  | 两次按键不 undo | PASS | PASS | PASS |

  ⇒ 与**负载/时序无关**（插 draw、插 0.7s 结果逐位相同）；触发条件是"**末尾含一次
  显式 undo**"这一**结构**（与按什么键无关）。③ 在全部 6 个变体 PASS，且末尾无 undo
  的两例里 `post == forward_end` ⇒ **新基线是等价替换，不是放宽**（`teeth ②b` 另有
  反向验证：真把重做弄坏，它照样红）。
* 附带：hypothesis 命中反例后会**收缩**，撞 5 分钟上限时同一次失败从 ~68s 变成 ~362s
  （"并发下慢 8 倍"的真成因）。本套件默认关掉 shrink 阶段（`MPLTWEAK_FZ_SHRINK=1` 开回）
  ⇒ 命中反例**秒级报错**，让"pytest 全绿"不再依赖机器闲不闲。

若 fuzz 发现真实交互层缺陷：**不弱化/删除断言**，用显式 pin（写明缺陷 + 最小复现，
标注已知缺陷）或在 output 报告船长派修复任务。本文件不改 src/ 下任何产品代码。
"""

import ast
import contextlib
import io
import math
import os
import shutil
import sys
import tempfile
import time

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.backend_bases import MouseEvent, KeyEvent, LocationEvent

from mpltweak.toolbox import (Tweak, _bbox, _set_bbox, _snap_box,
                              MIN_SIZE, MIN_SIZE_CB, LEGEND_LOCS,
                              _clim_of, _is_colorbar_ax)
import mpltweak.toolbox as _tb


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


def _decode_out(raw):
    for enc in ('utf-8', 'gbk', 'gb18030'):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode('utf-8', 'replace')


# ---- 语言 / 后端钉死 + 安静模式（别让宿主 locale 决定结论）----
os.environ['MPLTWEAK_LANG'] = 'zh'
os.environ['MPLBACKEND'] = 'Agg'
_tb.QUIET = True            # 静默拖拽日志，只剩 _info（模式判定）与测试自身输出

fail = 0


def check(name, cond, detail=''):
    global fail
    print(('PASS' if cond else 'FAIL') + ' | ' + _gbk_safe(name)
          + ((' | ' + _gbk_safe(detail)) if detail else ''))
    if not cond:
        fail += 1


# ---- 临时区：沙箱拦真实 %TEMP%，把 TEMP/TMP 指到可写目录 ----
_TMPROOT = None


def _ensure_tmp_writable():
    global _TMPROOT
    base = tempfile.gettempdir()
    root = os.path.join(base, '.mpltweak_fz_%d' % os.getpid())
    try:
        os.makedirs(root, exist_ok=True)
        probe = os.path.join(root, '.w')
        open(probe, 'w').close()
        os.unlink(probe)
    except OSError:
        root = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            '.tmp_fuzz_%d' % os.getpid())
        os.makedirs(root, exist_ok=True)
    _TMPROOT = root
    os.environ['TEMP'] = root
    os.environ['TMP'] = root
    os.environ['TMPDIR'] = root
    tempfile.tempdir = root


_ensure_tmp_writable()

_case_no = [0]


def send(canvas, name, x=None, y=None, button=None, key=None, buttons=None):
    '''构造事件对象并走回调管线。KeyEvent 必须显式传 x/y。坐标一律整数像素。

    关键坑（实测 mpl 3.10）：canvas.callbacks.process 会把 ``canvas._key``（最近
    按下的键）**注入**鼠标事件 —— 合成事件必须先重置 ``canvas._key``，否则上次按
    的 ctrl+f / ctrl+z / shift+up 会把下一次按下误判成 Ctrl/Shift+点击（选择切换
    而不是拖拽），真实 GUI 里修饰键状态本来就该这样，但合成事件必须显式表达。

    ``buttons``（t101）：matplotlib 3.10 里**只有** ``motion_notify_event`` 认这个
    字段（``MouseEvent.buttons``：``None`` 或 ``frozenset``；press/release 传非空会
    直接抛 ValueError）。不传 = 历史套件的"老形态"（离屏事件不表达按键状态）；
    传 ``frozenset({1})`` = 真机拖拽形态（"左键还按着"）。``figure_leave_event`` /
    ``figure_enter_event`` 走 ``LocationEvent``（真机就是它，没有 buttons 字段）。
    '''
    x = int(x) if x is not None else None
    y = int(y) if y is not None else None
    if name == 'key_press_event':
        ev = KeyEvent('key_press_event', canvas, key, x, y)
    elif name in ('figure_leave_event', 'figure_enter_event'):
        ev = LocationEvent(name, canvas, x, y)
    else:
        try:
            canvas._key = key or None
        except AttributeError:
            pass
        ev = MouseEvent(name, canvas, x, y, button=button, key=(key or None))
        if buttons is not None:
            # mpl 3.9.x 的 MouseEvent.__init__ 没有 buttons 形参（3.10 才有）；
            # 属性本身两版都有，构造后补赋值，语义与 3.10 传参一致。
            ev.buttons = buttons
    canvas.callbacks.process(name, ev)
    return ev


# ---------------------------------------------------------------------------
# 夹具：固定可运行面板图（2x2 add_axes + scatter + 标题 + 轴标签 + 一个图例），
# 以及 imshow + 水平 colorbar 的独立图（拖端点调长度用）。
# ---------------------------------------------------------------------------
def _make_fig():
    fig = plt.figure(figsize=(9, 7))
    pos = [[0.07, 0.55, 0.40, 0.38], [0.54, 0.55, 0.40, 0.38],
           [0.07, 0.07, 0.40, 0.38], [0.54, 0.07, 0.40, 0.38]]
    axes = [fig.add_axes(p) for p in pos]
    for k, ax in enumerate(axes):
        ax.scatter([0.1, 0.2, 0.3], [0.1, 0.4, 0.9], label='s%d' % k)
        ax.set_title('panel %d' % k, fontsize=10)
        ax.set_xlabel('XLAB_%d' % k, fontsize=10)
        ax.set_ylabel('YLAB_%d' % k, fontsize=10)
    axes[0].legend(loc='upper right', fontsize=8)
    fig.canvas.draw()
    return fig, axes


def _make_cb_fig():
    '''主数据轴 + 手工 cax 水平 colorbar（与 test_events §16 同款，左右端与父轴
    不重合，拖端点不会被吸附到父轴边线而看不出变化）。'''
    fig = plt.figure(figsize=(8, 5))
    ax = fig.add_axes([0.10, 0.45, 0.80, 0.45])
    im = ax.imshow([[0.0, 1.0], [2.0, 3.0]], cmap='viridis')
    cbax = fig.add_axes([0.20, 0.20, 0.50, 0.04])
    cb = fig.colorbar(im, cax=cbax, orientation='horizontal')
    fig.canvas.draw()
    cb.ax.set_axes_locator(None)
    cb.ax.set_box_aspect(None)
    fig.canvas.draw()
    return fig, fig.axes


# ---------------------------------------------------------------------------
# 执行环境：一个 fig + 一个 Tweak，坐标换算 / 手势分发都在这里。
# ---------------------------------------------------------------------------
class _Env:
    def __init__(self, fig, axes, tag):
        self.fig = fig
        self.axes = axes
        self.refresh_px()
        # Tweak 构造会打 [mpltweak] 模式判定日志（_info 不随 QUIET 静默），
        # 每用例两条会刷屏 —— 构造期间把 stdout 暂时接管掉。
        _buf = io.StringIO()
        with contextlib.redirect_stdout(_buf):
            self.tw = Tweak(fig,
                            export_path=os.path.join(_TMPROOT, tag + '.json'),
                            heavy=False)
        self.tw.snap = True

    def refresh_px(self):
        # 引擎在 _on_press/_on_motion 里用 fig.bbox.size 归一化（**浮点**像素），
        # 而 canvas.get_width_height() 会 int 截断 —— set_size_inches 之后两者
        # 相差 ~0.3px（实测 864.2778 vs 864），期望落位必须跟引擎同口径。
        try:
            self.W, self.H = (float(v) for v in self.fig.bbox.size)
        except Exception:                                    # noqa: BLE001
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

    def _hover_target(self, target, ax):
        '''悬停目标中心像素：标题/轴标签/刻度/图例；没有就退回面板中心。'''
        if target == 'title':
            t = ax.title
        elif target == 'xlabel':
            t = ax.xaxis.label
        elif target == 'ylabel':
            t = ax.yaxis.label
        elif target == 'xtick':
            tls = ax.get_xticklabels()
            t = tls[0] if tls else None
        elif target == 'ytick':
            tls = ax.get_yticklabels()
            t = tls[0] if tls else None
        elif target == 'legend':
            leg = ax.get_legend()
            if leg is not None:
                we = leg.get_window_extent()
                return (int(round((we.x0 + we.x1) / 2)),
                        int(round((we.y0 + we.y1) / 2)))
            return self.center(ax)
        else:
            return self.center(ax)
        if t is None or not t.get_text():
            return self.center(ax)
        we = t.get_window_extent()
        return (int(round((we.x0 + we.x1) / 2)),
                int(round((we.y0 + we.y1) / 2)))

    def exec_op(self, op):
        '''执行一个手势，返回待验的 (ax_i, 期望落位) 列表（只有完整拖拽会产生）。'''
        landings = []
        f, canvas = self.fig, self.fig.canvas
        kind = op[0]
        if kind == 'drag':
            # ('drag', ax_i, press_frac, [绝对像素偏移...], release偏移, snap_ov)
            _, ax_i, press_f, offs, roff, snap_ov = op
            ax = self.axes[ax_i]
            if snap_ov is not None:
                self.tw.snap = bool(snap_ov)
            ref = [float(v) for v in _bbox(ax)]      # Bbox 不可迭代，走 _bbox
            X, Y = float(self.W), float(self.H)
            px0, py0 = self.px(ax, press_f[0], press_f[1])
            send(canvas, 'button_press_event', px0, py0, button=1)
            origin = self.pos(ax)                  # 按下不改位置
            # 确认这次按下真的拖到了目标轴（面板重叠/画布外按下会被引擎路由成
            # 空白框选或别的轴 —— 合成事件在画布外拿不到 inaxes，真实 GUI 也点
            # 不到画布外）。
            dragged = (bool(getattr(self.tw, '_drag', False))
                       and self.tw._ax is ax)
            # 限制最终 motion：面板中心留在画布内（否则下一次按下面板中心会在
            # 画布外，拖拽退化成框选；也避免面板重叠把 press 路由到别的轴）。
            # 用「面板中心像素」算最终偏移，且只在真正越界时才 clamp——重推导会
            # 引入浮点误差（-8px → -7.9999…，int() 截断成 -7 落位差 1px）。
            last = [int(round(float(v))) for v in offs[-1]]
            cpx = (origin[0] + origin[2] / 2) * X
            cpy = (origin[1] + origin[3] / 2) * Y
            npx = max(0.06 * X, min(0.94 * X, cpx + last[0]))
            npy = max(0.06 * Y, min(0.94 * Y, cpy + last[1]))
            lx = int(round(npx - cpx))
            ly = int(round(npy - cpy))
            for (odx, ody) in offs[:-1]:            # 中间步可越界（负/超画布）
                send(canvas, 'motion_notify_event', px0 + int(odx),
                     py0 + int(ody))
            send(canvas, 'motion_notify_event', px0 + lx, py0 + ly)
            # 松开位置可与最后 motion 不同（含越界）——落位只由最后 motion 决定
            send(canvas, 'button_release_event',
                 px0 + int(roff[0]), py0 + int(roff[1]), button=1)
            target = [origin[0] + lx / X, origin[1] + ly / Y,
                      origin[2], origin[3]]
            if self.tw.snap:
                others = [_bbox(a) for a in f.axes if a is not ax]
                target, _, _ = _snap_box(target, others)
            landings.append((ax_i, [float(v) for v in target], dragged))
            if snap_ov is not None:
                self.tw.snap = True
        elif kind == 'click':
            _, ax_i, frac, mods = op
            ax = self.axes[ax_i]
            x, y = self.px(ax, frac[0], frac[1])
            send(canvas, 'button_press_event', x, y, button=1,
                 key=(mods or None))
            send(canvas, 'button_release_event', x, y, button=1)
        elif kind == 'key':
            _, key, target, ax_i = op
            ax = self.axes[ax_i]
            x, y = self._hover_target(target, ax)
            send(canvas, 'key_press_event', x, y, key=key)
        elif kind == 'nudge':
            _, ax_i, key = op
            ax = self.axes[ax_i]
            x, y = self.center(ax)
            send(canvas, 'motion_notify_event', x, y)     # 设 _last_ax
            send(canvas, 'key_press_event', x, y, key=key)
        elif kind == 'two_keys':
            _, ax_i, k1, k2 = op
            ax = self.axes[ax_i]
            x, y = self.center(ax)
            send(canvas, 'key_press_event', x, y, key=k1)
            send(canvas, 'key_press_event', x, y, key=k2)
        elif kind in ('undo', 'redo', 'space', 'snap_toggle', 'fit'):
            ax = self.axes[0]
            x, y = self.center(ax)
            keymap = {'undo': 'ctrl+z', 'redo': 'ctrl+y', 'space': ' ',
                      'snap_toggle': 'n', 'fit': 'ctrl+f'}
            if kind == 'fit':
                # _fit_to_content 的 _info 不随 QUIET 静默，接管 stdout 压掉
                _buf = io.StringIO()
                with contextlib.redirect_stdout(_buf):
                    send(canvas, 'key_press_event', x, y, key=keymap[kind])
                self.refresh_px()
            else:
                send(canvas, 'key_press_event', x, y, key=keymap[kind])
        elif kind == 'legend_drag':
            _, ax_i, dx, dy = op
            ax = self.axes[ax_i]
            leg = ax.get_legend()
            if leg is None:
                return landings
            we = leg.get_window_extent()
            lx = int(round((we.x0 + we.x1) / 2))
            ly = int(round((we.y0 + we.y1) / 2))
            send(canvas, 'button_press_event', lx, ly, button=1)
            send(canvas, 'motion_notify_event', lx + int(dx), ly + int(dy))
            send(canvas, 'button_release_event', lx + int(dx), ly + int(dy),
                 button=1)
        elif kind == 'motion':
            _, frac = op
            ax = self.axes[0]
            x, y = self.px(ax, frac[0], frac[1])
            send(canvas, 'motion_notify_event', x, y)
        elif kind == 'double_press':
            _, ax_i, frac = op
            ax = self.axes[ax_i]
            x, y = self.px(ax, frac[0], frac[1])
            send(canvas, 'button_press_event', x, y, button=1)
            send(canvas, 'button_press_event', x + 5, y + 5, button=1)
            send(canvas, 'button_release_event', x + 5, y + 5, button=1)
        elif kind == 'release_alone':
            _, frac = op
            ax = self.axes[0]
            x, y = self.px(ax, frac[0], frac[1])
            send(canvas, 'button_release_event', x, y, button=1)
        else:
            raise ValueError('未知手势 %r' % (op,))
        return landings


# ---------------------------------------------------------------------------
# 四条铁律的检测器（独立函数，teeth 探针直接调用它们验证"确实在测"）。
# ---------------------------------------------------------------------------
def _snapshot_pos(fig):
    return [[float(v) for v in _bbox(ax)] for ax in fig.axes]


def _close(a, b, tol=1e-9):
    return len(a) == len(b) and all(abs(x - y) <= tol for x, y in zip(a, b))


def _close_all(pa, pb, tol=1e-9):
    return len(pa) == len(pb) and all(_close(a, b, tol) for a, b in zip(pa, pb))


def _fmt_box(b):
    return '[%.4f,%.4f,%.4f,%.4f]' % tuple(float(v) for v in b)


def _check_geometry(fig, n0):
    '''铁律④：轴数不变 + 每轴 w/h 不塌 + 坐标有限。'''
    if len(fig.axes) != n0:
        return False, '轴数 %d != 初始 %d（面板丢失或新增）' % (len(fig.axes), n0)
    for j, ax in enumerate(fig.axes):
        try:
            x0, y0, w, h = (float(v) for v in _bbox(ax))
        except Exception as e:                                   # noqa: BLE001
            return False, 'ax%d bbox 读取失败: %s' % (j, e)
        if not all(math.isfinite(v) for v in (x0, y0, w, h)):
            return False, 'ax%d 坐标非有限值 %s' % (j, _fmt_box([x0, y0, w, h]))
        mn = MIN_SIZE_CB if _is_colorbar_ax(ax) else MIN_SIZE
        if w < mn - 1e-9 or h < mn - 1e-9:
            return False, 'ax%d 过小 w=%.5f h=%.5f（min=%.4f）' % (j, w, h, mn)
    return True, ''


def _check_undo_roundtrip(env, fig, init, forward_end, init_px, post=None):
    '''铁律②：Ctrl+Z 到栈底 == 初始布局 + 初始画布；Ctrl+Y 到栈顶 == **历史末端**。

    ⚠ 第 2 条腿的基线是"**历史末端状态**"（`forward_end`），**不是**"跑完 ops 时游标处
    的状态"（`post`）。这对旧判据是**逐字节等价**的：ops 末尾没有 undo/redo 时游标就在
    末端，`forward_end == post`；只有**末尾含显式 undo** 时两者才不同 —— 那时抽干
    `_redo` 走到的是历史末端（Ctrl+Y 本来就该往前走），拿游标态当期望必然假红。
    t26 实测反例 `[('two_keys', 0, 'up', '+'), ('undo',)]`（见文件头"六变体判读表"）。
    `post` 只用于失败信息里做对照，不参与判定。
    '''
    tw = env.tw
    n = 0
    while tw._undo and n < 500:
        tw._undo_once()
        n += 1
    undo = _snapshot_pos(fig)
    if not _close_all(undo, init):
        return False, '全撤销后布局 != 初始（撤 %d 步）' % n
    W, H = (int(v) for v in fig.canvas.get_width_height())
    if abs(W - init_px[0]) > 2 or abs(H - init_px[1]) > 2:
        return False, '全撤销后画布 %dx%d != 初始 %dx%d' % (W, H, init_px[0], init_px[1])
    m = 0
    while tw._redo and m < 500:
        tw._redo_once()
        m += 1
    redo = _snapshot_pos(fig)
    if not _close_all(redo, forward_end):
        return False, ('全重做后布局 != 历史末端（旧判据拿"游标态"当期望：'
                       '游标态=%s 末端=%s）'
                       % (_fmt_box(post[0]) if post else '?',
                          _fmt_box(forward_end[0])))
    return True, 'undo=%d redo=%d' % (n, m)


# ---------------------------------------------------------------------------
# 通用用例执行器：跑一串手势 -> 逐条验铁律。
# ---------------------------------------------------------------------------
def _exec_ops(env, ops, check_landing, fwd=None):
    '''跑一串手势。

    `fwd`（可选，单元素 list）用来记**历史末端状态**：每跑完一个 op，若 `tw._redo` 为空
    （= 游标在历史末端）就把当前实测布局记进去。为什么第 2 条腿必须用它当基线，见
    `_check_undo_roundtrip` 的说明（末尾含显式 undo 时"游标态"并不是末端）。
    '''
    crash = None
    land_fail = None
    for idx, op in enumerate(ops):
        try:
            lds = env.exec_op(op)
        except Exception as e:                                   # noqa: BLE001
            return (idx, op, e), land_fail
        if fwd is not None and not env.tw._redo:
            fwd[0] = _snapshot_pos(env.fig)
        if check_landing:
            for ax_i, exp, dragged in lds:
                if not dragged:
                    continue        # 未真正拖到目标轴（画布外/重叠路由）→ 不算拖拽
                got = env.pos(env.axes[ax_i])
                if not _close(got, exp, 1e-9):
                    land_fail = (idx, ax_i, got, exp)
    return crash, land_fail


def _case(label, ops, make=None, check_landing=True, extra=None, pre=None,
          quiet=False):
    '''跑一个用例并逐条断言铁律；返回结果 dict 供 teeth 探针复用。'''
    make = make or _make_fig
    _case_no[0] += 1
    fig, axes = make()
    env = _Env(fig, axes, 'case_%d' % _case_no[0])
    pre_state = pre(env) if pre is not None else None
    init = _snapshot_pos(fig)
    init_px = (int(env.W), int(env.H))
    fwd = [None]
    crash, land_fail = _exec_ops(env, ops, check_landing, fwd)
    post = _snapshot_pos(fig)
    # 历史末端状态（铁律②第 2 条腿的基线）：ops 末尾不含 undo/redo 时它 == post
    # （与旧判据逐字节等价）；末尾含显式 undo 时它才是"抽干 _redo 后该落到的样子"。
    forward_end = fwd[0] if fwd[0] is not None else init
    why1 = why2 = why3 = why4 = ex_why = ''
    ok1 = crash is None
    if crash is not None:
        idx, op, e = crash
        why1 = '第 %d 个手势 %r 抛异常: %s' % (idx, op, _gbk_safe(e))
    ok3 = True
    if ok1 and check_landing and land_fail is not None:
        idx, ax_i, got, exp = land_fail
        ok3 = False
        why3 = '第%d手势后 ax%d 落位 %s != 期望 %s' % (
            idx, ax_i, _fmt_box(got), _fmt_box(exp))
    ok4 = True
    if ok1:
        ok4, why4 = _check_geometry(fig, len(axes))
    ex_ok = True
    if ok1 and extra is not None:
        try:
            ex_ok, ex_why = extra(env, fig, axes, init, post, pre_state)
        except Exception as e:                                   # noqa: BLE001
            ex_ok, ex_why = False, 'extra 抛异常 %r' % (e,)
    ok2 = True
    if ok1:
        ok2, why2 = _check_undo_roundtrip(env, fig, init, forward_end, init_px,
                                          post)
    allok = ok1 and ok2 and ok3 and ok4 and ex_ok
    detail = ' | '.join(x for x in (why1, why3, why4, ex_why, why2) if x)
    if not quiet:
        check(label, allok, detail)
    plt.close(fig)          # 每个用例独立图，用完即关（防图对象堆积吃内存）
    return dict(ok1=ok1, ok2=ok2, ok3=ok3, ok4=ok4, ex_ok=ex_ok,
                detail=detail, env=env, crash=crash)


# ---------------------------------------------------------------------------
# hypothesis：随机手势序列。只用固定结构 + 参数随机（模板保证能跑）。
# ---------------------------------------------------------------------------
try:
    # 可选依赖：装了 hypothesis 就是**真货**；没装则由 `_hypothesis_shim` 提供只服务
    # "装饰函数/拼配置"的最小替身 —— 于是本套件在"CI step 6 不装 hypothesis"时也能
    # import 成功，**命名用例与 teeth 探针照常全跑**，只有下面那两条 property 被
    # `_HAS_HYPOTHESIS` 挡掉并打印明确原因（不静默、不算失败）。
    from _hypothesis_shim import (HAS_HYPOTHESIS as _HAS_HYPOTHESIS, st, given,
                                  settings, HealthCheck, Phase)
except ImportError:                                          # noqa: BLE001
    _HAS_HYPOTHESIS = False


@st.composite
def st_drag(draw):
    '''完整拖拽：按下（面板内）→ 1~8 次 motion（像素偏移，大步常越界）→ 松开
    （位置可能 == 或 != 最后 motion）。'''
    ax_i = draw(st.integers(0, 3))
    pfx = draw(st.floats(0.25, 0.75))
    pfy = draw(st.floats(0.25, 0.75))
    n = draw(st.integers(1, 8))
    ox = oy = 0
    offs = []
    for _ in range(n):
        rng = 1000 if draw(st.booleans()) else 90      # 大步 ≈ 越界
        ox += draw(st.integers(-rng, rng))
        oy += draw(st.integers(-rng, rng))
        offs.append((ox, oy))
    rdx = draw(st.integers(-60, 60))
    rdy = draw(st.integers(-60, 60))
    snap_ov = draw(st.sampled_from([None, True, False]))
    return ('drag', ax_i, (pfx, pfy), offs, (offs[-1][0] + rdx,
                                             offs[-1][1] + rdy), snap_ov)


@st.composite
def st_mixed_op(draw):
    '''任意手势：拖拽/单击/悬停字号/方向键/撤销重做/空格/吸附开关/裸 motion/
    双按下/无按下松开/图例拖拽/两键同按。'''
    kind = draw(st.integers(0, 13))
    ax_i = draw(st.integers(0, 3))
    if kind == 0:
        return draw(st_drag())
    if kind == 1:
        frac = (draw(st.floats(0.2, 0.8)), draw(st.floats(0.2, 0.8)))
        mods = draw(st.sampled_from(['', 'ctrl', 'shift']))
        return ('click', ax_i, frac, mods)
    if kind == 2:
        tgt = draw(st.sampled_from(
            ['title', 'xlabel', 'ylabel', 'xtick', 'ytick', None]))
        k = draw(st.sampled_from(['+', '-']))
        return ('key', k, tgt, ax_i)
    if kind == 3:
        k = draw(st.sampled_from(
            ['up', 'down', 'left', 'right', 'shift+up', 'shift+right']))
        return ('nudge', ax_i, k)
    if kind == 4:
        return ('undo',)
    if kind == 5:
        return ('redo',)
    if kind == 6:
        return ('space',)
    if kind == 7:
        return ('snap_toggle',)
    if kind == 8:
        return ('motion', (draw(st.floats(-0.3, 1.3)),
                           draw(st.floats(-0.3, 1.3))))
    if kind == 9:
        return ('double_press', ax_i,
                (draw(st.floats(0.2, 0.8)), draw(st.floats(0.2, 0.8))))
    if kind == 10:
        return ('release_alone', (draw(st.floats(0.1, 0.9)),
                                  draw(st.floats(0.1, 0.9))))
    if kind == 11:
        return ('legend_drag', ax_i, draw(st.integers(-120, 120)),
                draw(st.integers(-120, 120)))
    if kind == 12:
        return ('two_keys', ax_i,
                draw(st.sampled_from(['up', 'down', 'left', 'right'])),
                draw(st.sampled_from(['+', '-'])))
    return ('fit',)


# 默认**不做收缩（shrink）**：命中反例时秒级报错，而不是卡在 hypothesis 的 5 分钟
# 收缩上限上。t26 实测同一个反例：带收缩 362s、不收缩 68s —— "并发下慢 8 倍"的真成因
# 就是收缩本身，与机器负载无关（详情见文件头"六变体判读表"）。
# 需要收缩来把反例最小化时：设 MPLTWEAK_FZ_SHRINK=1。
_FZ_PHASES = ([Phase.explicit, Phase.generate, Phase.shrink]
              if os.environ.get('MPLTWEAK_FZ_SHRINK') == '1'
              else [Phase.explicit, Phase.generate])
_FZ_CFG = dict(deadline=None, database=None,
               suppress_health_check=[HealthCheck.too_slow,
                                      HealthCheck.data_too_large],
               report_multiple_bugs=False,
               phases=_FZ_PHASES)


@settings(max_examples=6, **_FZ_CFG)
@given(st.lists(st_mixed_op(), min_size=1, max_size=25))
def prop_fuzz_mixed(ops):
    res = _case('fuzz-mixed', ops, check_landing=False, quiet=True)
    assert res['ok1'] and res['ok2'] and res['ok4'] and res['ex_ok'], \
        res['detail'] + (' | 序列=%r' % (ops,))


@settings(max_examples=6, **_FZ_CFG)
@given(st.lists(st_drag(), min_size=1, max_size=5))
def prop_fuzz_drag(ops):
    res = _case('fuzz-drag', ops, check_landing=True, quiet=True)
    assert res['ok1'] and res['ok2'] and res['ok3'] and res['ok4'], \
        res['detail'] + (' | 序列=%r' % (ops,))


def _run_hyp(label, prop):
    global fail
    t0 = time.time()
    try:
        prop()
        check(label, True, '%.0fs' % (time.time() - t0))
    except Exception as e:                                   # noqa: BLE001
        check(label, False, 'property 被反例击穿: %s' % _gbk_safe(e)[:500])


# ---------------------------------------------------------------------------
# 确定性命名用例：覆盖验收点列出的每一种交互。
# ---------------------------------------------------------------------------
def _run_named():
    # ① 拖拽语义：多步 motion + 越界坐标（中间步）+ 松手位置≠最后 motion
    _case('拖拽：3 步 motion（含越界）+ 松手≠最后motion → 落位=最后motion',
          [('drag', 1, (0.5, 0.5), [(50, -20), (1050, -40), (120, -20)],
            (950, -60), False),
           ('drag', 2, (0.5, 0.5), [(-200, 30)], (-200, 30), False)],
          check_landing=True)

    # ② 贴边吸附（画布左缘 0 / 右缘 1）
    _case('贴边吸附：ax3 左缘拖到画布左缘（x0→0.0）',
          [('drag', 3, (0.5, 0.5), [(-485, 0)], (-485, 0), True)],
          check_landing=True)
    _case('贴边吸附：ax1 右缘拖到画布右缘（x1→1.0）',
          [('drag', 1, (0.5, 0.5), [(54, 0)], (54, 0), True)],
          check_landing=True)

    # ③ 贴邻吸附：ax2 左缘拖近 ax1 左缘（0.54）
    _case('贴邻吸附：ax2 左缘 → ax1 左缘（0.54）',
          [('drag', 2, (0.5, 0.5), [(428, 0)], (428, 0), True)],
          check_landing=True)

    # ④ 单击：位置不变 + 不压幽灵撤销步（F1 行为）
    def _ex_click(env, fig, axes, init, post, pre):
        return (len(env.tw._undo) == 0, 'undo=%d' % len(env.tw._undo))

    _case('单击（按下即抬起）：位置不变 + 无幽灵撤销步',
          [('click', 0, (0.5, 0.5), '')], extra=_ex_click, check_landing=False)

    # ⑤ Ctrl 多选 + 多选拖拽（两面板同位移、尺寸不变）
    def _ex_multi(env, fig, axes, init, post, pre):
        dx = 36.0 / env.W
        dy = 18.0 / env.H
        ok0 = _close([post[0][0] - init[0][0], post[0][1] - init[0][1]],
                     [dx, dy], 1e-9)
        ok1 = _close([post[1][0] - init[1][0], post[1][1] - init[1][1]],
                     [dx, dy], 1e-9)
        sz0 = _close([post[0][2], post[0][3]], [init[0][2], init[0][3]], 1e-9)
        sz1 = _close([post[1][2], post[1][3]], [init[1][2], init[1][3]], 1e-9)
        return (ok0 and ok1 and sz0 and sz1,
                'ax0 移=%.4f,%.4f ax1 移=%.4f,%.4f' % (
                    post[0][0] - init[0][0], post[0][1] - init[0][1],
                    post[1][0] - init[1][0], post[1][1] - init[1][1]))

    _case('Ctrl 多选 + 多选拖拽：两面板同位移（尺寸不变）',
          [('click', 0, (0.5, 0.5), 'ctrl'),
           ('click', 1, (0.5, 0.5), 'ctrl'),
           ('drag', 0, (0.5, 0.5), [(36, 18)], (36, 18), False)],
          extra=_ex_multi, check_landing=True)

    # ⑥ 悬停标题/轴标签/刻度 + +/- 调字号
    def _pre_font(env):
        a2 = env.axes[2]
        return dict(t0=env.axes[0].title.get_fontsize(),
                    x0=env.axes[1].xaxis.label.get_fontsize(),
                    y0=(a2.get_yticklabels()[0].get_fontsize()
                        if a2.get_yticklabels() else None))

    def _ex_font(env, fig, axes, init, post, pre):
        t1 = axes[0].title.get_fontsize()
        x1 = axes[1].xaxis.label.get_fontsize()
        y1 = axes[2].get_yticklabels()[0].get_fontsize()
        ok = (t1 == pre['t0'] + 1 and x1 == pre['x0'] + 1
              and pre['y0'] is not None and y1 == pre['y0'] + 1)
        return (ok, 'title %s→%s xlabel %s→%s ytick %s→%s'
                % (pre['t0'], t1, pre['x0'], x1, pre['y0'], y1))

    _case('悬停标题/x轴标签/y刻度 + +/-：字号各 ±1',
          [('key', '+', 'title', 0), ('key', '+', 'title', 0),
           ('key', '-', 'title', 0), ('key', '+', 'xlabel', 1),
           ('key', '+', 'ytick', 2)],
          extra=_ex_font, pre=_pre_font, check_landing=False)

    # ⑦ 方向键微调：↑/→ 移动 0.005，Shift+↑ 加高 0.025（中心不动）
    def _pre_nudge(env):
        return dict(b0=list(_bbox(env.axes[0])))

    def _ex_nudge(env, fig, axes, init, post, pre):
        b = [float(v) for v in _bbox(axes[0])]
        b0 = pre['b0']
        exp = [b0[0] + 0.005, b0[1] + 0.005 - 0.0125, b0[2], b0[3] + 0.025]
        return (_close(b, exp, 1e-9),
                '%s -> %s（期望 %s）' % (_fmt_box(b0), _fmt_box(b), _fmt_box(exp)))

    _case('方向键：↑/→ 移动 0.005，Shift+↑ 加高 0.025（中心不动）',
          [('nudge', 0, 'up'), ('nudge', 0, 'right'),
           ('nudge', 0, 'shift+up')],
          extra=_ex_nudge, pre=_pre_nudge, check_landing=False)

    # ⑧ 空格切线框模式：开→关 后布局不变、退出线框态
    def _ex_wire(env, fig, axes, init, post, pre):
        return (env.tw._wire is False, 'wire=%s' % env.tw._wire)

    _case('空格 切线框模式：开→关 后布局不变、状态复原',
          [('space',), ('space',)], extra=_ex_wire, check_landing=False)

    # ⑨ Ctrl+Z / Ctrl+Y 撤销重做：深序列 + 中途撤销重做后仍往返一致
    _case('Ctrl+Z/Ctrl+Y：深序列 + 中途撤销重做 → 全撤/全重做仍往返一致',
          [('drag', 0, (0.5, 0.5), [(40, 10)], (40, 10), False),
           ('drag', 1, (0.5, 0.5), [(-30, -15)], (-30, -15), False),
           ('key', '+', 'title', 2),
           ('undo',), ('undo',), ('redo',),
           ('drag', 3, (0.5, 0.5), [(25, 25)], (25, 25), False)],
          check_landing=True)

    # ⑩ 图例拖拽 + 8 位吸附
    def _ex_leg(env, fig, axes, init, post, pre):
        st = env.tw.leg_state.get(id(axes[0]), {})
        ok = (st.get('loc') in LEGEND_LOCS and st.get('anchor') is None
              and axes[0].get_legend() is not None)
        return (ok, 'loc=%s anchor=%s' % (st.get('loc'), st.get('anchor')))

    _case('图例拖拽：松手吸附 8 标准位之一（loc 有效 + anchor 清空）',
          [('legend_drag', 0, -40, -20)], extra=_ex_leg, check_landing=False)

    # ⑪ 无意义乱序：不崩 + 往返 + 几何
    _case('无意义乱序：裸 motion / 双按下 / 无按下就松开 / 两键同按 / 越界拖拽',
          [('motion', (0.9, 0.9)), ('motion', (-0.2, 1.2)),
           ('release_alone', (0.5, 0.5)),
           ('double_press', 1, (0.5, 0.5)),
           ('two_keys', 0, 'up', '+'),
           ('drag', 2, (0.5, 0.5), [(-300, 0), (300, 200)], (0, 0), False),
           ('click', 3, (0.5, 0.5), 'shift')],
          check_landing=True)

    # ⑫ Ctrl+F 裁白边：画布变化 → 全撤销后画布与布局都还原
    def _pre_fit(env):
        return dict(W0=int(env.W), H0=int(env.H))

    def _ex_fit(env, fig, axes, init, post, pre):
        W, H = (int(v) for v in fig.canvas.get_width_height())
        changed = (W != pre['W0'] or H != pre['H0'])
        return (True, '画布 %dx%d → %dx%d（changed=%s）'
                % (pre['W0'], pre['H0'], W, H, changed))

    _case('Ctrl+F 裁白边：全撤销后画布/布局还原',
          [('fit',), ('drag', 0, (0.5, 0.5), [(30, 15)], (30, 15), False)],
          extra=_ex_fit, pre=_pre_fit, check_landing=True)


def _case_mixed_legend_panel():
    '''图例按下未松开就改按面板（状态混合）：不崩 + 往返一致 + 几何合法。'''
    fig, axes = _make_fig()
    env = _Env(fig, axes, 'mixleg')
    leg = axes[0].get_legend()
    we = leg.get_window_extent()
    lx = int(round((we.x0 + we.x1) / 2))
    ly = int(round((we.y0 + we.y1) / 2))
    x, y = env.px(axes[2], 0.5, 0.5)
    init = _snapshot_pos(fig)
    init_px = (int(env.W), int(env.H))
    try:
        send(fig.canvas, 'button_press_event', lx, ly, button=1)
        send(fig.canvas, 'motion_notify_event', lx - 40, ly - 30)
        send(fig.canvas, 'button_press_event', x, y, button=1)   # 未松开图例按面板
        send(fig.canvas, 'motion_notify_event', x + 30, y + 20)
        send(fig.canvas, 'button_release_event', x + 30, y + 20, button=1)
        send(fig.canvas, 'button_release_event', lx - 40, ly - 30, button=1)
    except Exception as e:                                       # noqa: BLE001
        check('图例未松开改按面板：不崩', False, '抛异常 %s' % _gbk_safe(e))
        return
    post = _snapshot_pos(fig)
    # 这条用例的手势里没有 undo/redo op ⇒ 游标一直在历史末端，末端态 == post
    # （与旧判据等价；传 post 即正确基线）
    ok2, why2 = _check_undo_roundtrip(env, fig, init, post, init_px, post)
    ok4, why4 = _check_geometry(fig, len(axes))
    check('图例未松开改按面板：往返一致 + 几何合法',
          ok2 and ok4, (why2 + ' ' + why4).strip())
    plt.close(fig)


def _case_colorbar():
    '''colorbar 拖端点：长度变长 + clim 不变（#8 手势方向锁死）。'''
    fig, axes = _make_cb_fig()
    env = _Env(fig, axes, 'cb')
    cb_ax = axes[-1]
    w0 = float(_bbox(cb_ax)[2])
    clim0 = _clim_of(cb_ax)
    init = _snapshot_pos(fig)
    init_px = (int(env.W), int(env.H))

    def _tip():
        bb = cb_ax.get_position().transformed(fig.transFigure)
        return (int(round(bb.x1 - 3.0)), int(round((bb.y0 + bb.y1) / 2)))

    crash = None
    try:
        x, y = _tip()
        send(fig.canvas, 'button_press_event', x, y, button=1)
        send(fig.canvas, 'motion_notify_event', x + 70, y)
        send(fig.canvas, 'button_release_event', x + 70, y, button=1)
    except Exception as e:                                       # noqa: BLE001
        crash = e
    ok1 = crash is None
    w1 = float(_bbox(cb_ax)[2])
    clim1 = _clim_of(cb_ax)
    ok_sp = ok1 and w1 > w0 + 1e-6 and clim1 == clim0
    post = _snapshot_pos(fig)
    # 同 _case_mixed_legend_panel：本条也没有 undo/redo op ⇒ 末端态 == post
    ok2, why2 = _check_undo_roundtrip(env, fig, init, post, init_px, post) \
        if ok1 else (True, '')
    ok4, why4 = _check_geometry(fig, len(axes)) if ok1 else (True, '')
    detail = ('长度 %.4f→%.4f clim %s→%s' % (w0, w1, clim0, clim1))
    if crash is not None:
        detail += ' 抛异常 %s' % _gbk_safe(crash)
    check('colorbar 拖端点：长度变长 + clim 不变 + 往返一致 + 几何合法',
          ok1 and ok_sp and ok2 and ok4, detail)
    plt.close(fig)


# ---------------------------------------------------------------------------
# teeth 探针：故意破坏一条不变量 -> 对应检测器必须变红（证明确实在测）。
# ---------------------------------------------------------------------------
def _teeth_probes():
    _orig_restore_snap = Tweak._restore_snap
    _orig_on_motion = Tweak._on_motion
    _orig_set_bbox = _tb._set_bbox
    drag_op = [('drag', 1, (0.5, 0.5), [(40, 20)], (40, 20), False)]

    # ② 撤销往返：_restore_snap 失效 → 检测器红
    r_clean = _case('teeth-②-正对照', drag_op, quiet=True)
    Tweak._restore_snap = lambda self, snap: None
    try:
        r_bad = _case('teeth-②-破坏（预期FAIL）：_restore_snap 失效', drag_op,
                      quiet=True)
    finally:
        Tweak._restore_snap = _orig_restore_snap
    check('teeth ② 撤销往返检测器活着（_restore_snap 失效 → ok2 变红）',
          r_clean['ok2'] and not r_bad['ok2'], 'clean=%s bad=%s'
          % (r_clean['ok2'], r_bad['ok2']))

    # ②b **只打第 2 条腿**（重做 → 历史末端）：让 `_redo_once` 失效 → 新基线必须红。
    #     ①ops 必须**末尾含一次显式 undo**，否则重做栈为空、这条腿无事可做 —— 那也正是
    #       旧判据（拿游标态当期望）看不出差别的原因；
    #     ②这条探针证明"新基线与旧判据等价"不是"放宽"：重做一旦真的失效，它照样红。
    undo_op = [('nudge', 0, 'up'), ('undo',)]
    r_clean = _case('teeth-②b-正对照', undo_op, quiet=True)
    _orig_redo_once = Tweak._redo_once
    Tweak._redo_once = lambda self: None
    try:
        r_bad = _case('teeth-②b-破坏（预期FAIL）：_redo_once 失效', undo_op,
                      quiet=True)
    finally:
        Tweak._redo_once = _orig_redo_once
    check('teeth ②b 第 2 条腿（重做→历史末端）检测器活着'
          '（_redo_once 失效 → ok2 变红）',
          r_clean['ok2'] and not r_bad['ok2'], 'clean=%s bad=%s'
          % (r_clean['ok2'], r_bad['ok2']))

    # ③ 松手落位：_set_bbox 篡改 x（+0.01）→ 检测器红
    r_clean = _case('teeth-③-正对照', drag_op, quiet=True)

    def _bad_sb(ax, box):
        _orig_set_bbox(ax, [box[0] + 0.01, box[1], box[2], box[3]])

    _tb._set_bbox = _bad_sb
    try:
        r_bad = _case('teeth-③-破坏（预期FAIL）：落位被篡改', drag_op, quiet=True)
    finally:
        _tb._set_bbox = _orig_set_bbox
    check('teeth ③ 松手落位检测器活着（落位被篡改 → ok3 变红）',
          r_clean['ok3'] and not r_bad['ok3'], 'clean=%s bad=%s'
          % (r_clean['ok3'], r_bad['ok3']))

    # ④ 几何合法：_set_bbox 写"宽 < MIN_SIZE"（有限值，不把管线打崩）→ 检测器红
    def _tiny_sb(ax, box):
        _orig_set_bbox(ax, [box[0], box[1], MIN_SIZE / 2.0, box[3]])

    _tb._set_bbox = _tiny_sb
    try:
        r_bad = _case('teeth-④-破坏（预期FAIL）：面板宽被压到 MIN_SIZE 以下',
                      drag_op, quiet=True)
    finally:
        _tb._set_bbox = _orig_set_bbox
    check('teeth ④ 几何合法检测器活着（面板过小 → ok4 变红）',
          not r_bad['ok4'], 'ok4=%s' % r_bad['ok4'])

    # ① 不崩：_on_motion 抛异常 → 检测器红
    def _boom_motion(self, ev):
        raise RuntimeError('teeth boom')

    Tweak._on_motion = _boom_motion
    try:
        r_bad = _case('teeth-①-破坏（预期FAIL）：_on_motion 抛异常', drag_op,
                      quiet=True)
    finally:
        Tweak._on_motion = _orig_on_motion
    check('teeth ① 不崩检测器活着（事件处理器抛异常 → ok1 变红）',
          not r_bad['ok1'], 'ok1=%s' % r_bad['ok1'])


# ---------------------------------------------------------------------------
# =====================================================================
# §t82 t82：**交互不变量套件**（把"手感/用户的期望"翻译成可断言的不变量）
#
# 六条：① 影响面（逐字段 diff：一次手势只许动它瞄准的对象）② 撤销完备性（参数**字节级**
# 回到起点，且不许动没碰过的对象）③ 命中自洽（点哪中哪，**独立 patch 几何**判据，不复用
# 被测代码的判定）④ 可达性（每个可见对象都有记录在案的选中路径）⑤ 无死锁（逃生路径存在）
# ⑥ 延迟预算（代表性图形上的单次拖拽 p50/p95）。
#
# **离屏可证 / 待真窗口**：
#   · 离屏可证：①-⑤ 的全部判据、⑥ 的**离屏 wall time**（本机基线，可复现）。
#   · **待真窗口**：⑥ 里 blit/DPR/Qt 事件循环带来的**真机延迟**（离屏 Agg 没有 blit 路径、
#     没有屏幕刷新）；以及"用户看得见的反馈"（蓝框/提示条）本身 —— 本套件只断言状态与
#     时序，不断言像素。
#
# **已知缺口机制**：③④（P0-2 命中/可达）在**当前实现**上就是红的（这正是 t82 的交付：
# 证明这套测试能独立抓住这个坑）。默认模式把它们记成"已知缺口"（只登记+打印，套件仍绿，
# 不影响全量）；设 `MPLTWEAK_INVARIANTS_STRICT=1` 时它们变**硬失败** ⇒ 供 t71 做
# 红 → 绿验收（红/绿原始输出并排）。
# =====================================================================
_STRICT_INV = os.environ.get('MPLTWEAK_INVARIANTS_STRICT') == '1'
_INV_GAPS = []


def inv(name, cond, detail=''):
    '''不变量断言：默认模式下"已知缺口"只登记；严格模式（env）下算失败。'''
    if cond:
        check(name, True)
    else:
        _INV_GAPS.append(name)
        check(name, not _STRICT_INV, '【已知缺口】%s' % detail)


def _params(env):
    '''导出参数（字节 + dict）——影响面/撤销完备性都以**参数**为口径。'''
    import json as _json
    env.tw.export()
    with open(env.tw.export_path, 'rb') as f:
        raw = f.read()
    return raw, _json.loads(raw.decode('utf-8'))


def _field_map(d):
    '''把参数摊平成 {(对象, 字段): 值}；'canvas' 也是一个对象（figsize_* 等）。'''
    out = {}
    for k in ('figsize_px', 'figsize_in', 'n_figs', 'fig_index'):
        out[('canvas', k)] = d.get(k)
    for it in d.get('axes') or []:
        i = it.get('index')
        for k, v in it.items():
            if k != 'index':
                out[('ax%s' % i, k)] = v
    return out


def _diff_fields(a, b):
    ka, kb = _field_map(a), _field_map(b)
    return sorted(k for k in (set(ka) | set(kb)) if ka.get(k) != kb.get(k))


def _click(env, x, y, key=None):
    send(env.fig.canvas, 'button_press_event', int(x), int(y), button=1, key=key)
    send(env.fig.canvas, 'button_release_event', int(x), int(y), button=1, key=key)


def _press_esc(env):
    send(env.fig.canvas, 'key_press_event', x=0, y=0, key='escape')


def _undo_all(env, cap=80):
    n = 0
    while env.tw._undo and n < cap:
        send(env.fig.canvas, 'key_press_event', x=0, y=0, key='ctrl+z')
        n += 1
    return n


def _drag(env, x0, y0, dx, dy, steps=3):
    send(env.fig.canvas, 'button_press_event', int(x0), int(y0), button=1)
    for i in range(1, steps + 1):
        send(env.fig.canvas, 'motion_notify_event',
             int(x0 + dx * i / steps), int(y0 + dy * i / steps), button=1)
    send(env.fig.canvas, 'button_release_event',
         int(x0 + dx), int(y0 + dy), button=1)


def _sel_idx(env):
    return sorted(i for i, a in enumerate(env.axes) if id(a) in env.tw._selected)


def _real_contains(ax, x, y):
    '''**独立**命中判据：matplotlib 自己的几何（不调用被测代码的判定）。

    两层：① `ax.patch` 的点包含（常规轴）；② 退到 `ax.get_window_extent()`（colorbar
    这类 cax 的 patch 可能不可用）—— 两者都是 matplotlib 的几何，不是工具箱的命中逻辑。
    '''
    try:
        px, py = ax.transAxes.inverted().transform((float(x), float(y)))
        if bool(ax.patch.contains_point((px, py))):
            return True
    except Exception:                                    # noqa: BLE001
        pass
    try:
        bb = ax.get_window_extent()
        return bool(bb.x0 <= float(x) <= bb.x1 and bb.y0 <= float(y) <= bb.y1)
    except Exception:                                    # noqa: BLE001
        return False


def _make_cb_inside_polar():
    '''报告 P0-2 的**现场形状**：colorbar 的 rect 落在极地图矩形**内部**（相交）。

    极地图 rect `[0.039, 0.4651, 0.9224, 0.4349]`（t75 台账里的实测值）+ colorbar
    rect `[0.33, 0.455, 0.34, 0.016]` ⇒ y 方向真重叠 `[0.4651, 0.471]`。
    '''
    import math
    fig = plt.figure(figsize=(15, 9.5))
    ax = fig.add_axes([0.039, 0.4651, 0.9224, 0.4349], projection='polar')
    th = [i * 6.2832 / 60 for i in range(60)]
    rr = [math.cos(t) * 3 for t in th]
    sc = ax.scatter(th, rr, c=rr)
    cax = fig.add_axes([0.33, 0.455, 0.34, 0.016])
    fig.colorbar(sc, cax=cax, orientation='horizontal')
    return fig, fig.axes


def _make_heavy_fig():
    '''代表性图形：**极地图 + colorbar + 多艺术家**（延迟预算用）。'''
    import math
    fig = plt.figure(figsize=(9, 7))
    ax = fig.add_subplot(111, projection='polar')
    th = [i * 6.2832 / 60 for i in range(60)]
    rr = [math.cos(t) * 3 for t in th]
    sc = ax.scatter(th, rr, c=rr)
    fig.colorbar(sc, ax=ax)
    for k in range(3):
        ax.plot(th, [math.cos(t) * (k + 1) for t in th], lw=1.0 + k)
    return fig, fig.axes


# ---- ① 影响面（逐字段 diff）-------------------------------------------------
def _inv1_case(label, ops, make=None, target='ax0', allow_canvas=False,
               allow_pos=False, expect_any=False, quiet=False):
    holder = {}

    def pre(env):
        holder['raw'], holder['d'] = _params(env)
        return None

    def extra(env, fig, axes, init, post, pre_state):
        _raw1, d1 = _params(env)
        changed = _diff_fields(holder['d'], d1)
        bad = [c for c in changed
               if not (c[0] == target
                       or (allow_canvas and c[0] == 'canvas')
                       or (allow_pos and c[1] == 'pos'))]
        if expect_any and not changed:
            return False, '期望有改动但**一个字段都没变**'
        return (not bad, '越界改动 = %s（允许：%s%s%s）'
                % (bad, target, ' + canvas' if allow_canvas else '',
                   ' + 所有轴的 pos' if allow_pos else ''))

    return _case(label, ops, make=make, extra=extra, pre=pre, quiet=quiet)


# ---- ② 撤销完备性（参数字节级）---------------------------------------------
def _inv2_undo(ops, make=None, target='ax0', allow_canvas=False):
    fig, axes = make() if make else _make_fig()
    env = _Env(fig, axes, 'inv2_%d' % _case_no[0])
    _case_no[0] += 1
    raw0, d0 = _params(env)
    crash, _ = _exec_ops(env, ops, False)
    if crash is not None:
        plt.close(fig)
        return False, '手势抛异常 %r' % (crash[2],)
    n = _undo_all(env)
    raw1, d1 = _params(env)
    changed = _diff_fields(d0, d1)
    plt.close(fig)
    return (raw1 == raw0 and not changed,
            '撤销 %d 次后：参数字节相同=%s 残留差异=%s' % (n, raw1 == raw0, changed))


# ---- ③ 命中自洽 + ④ 可达性 -------------------------------------------------
def _inv34_hit_reach(make, tag):
    fig, axes = make()
    env = _Env(fig, axes, 'inv34_' + tag)
    bad_hit, unreach = [], []
    for i, ax in enumerate(axes):
        x, y = env.center(ax)
        _press_esc(env)
        _click(env, x, y)
        got = _sel_idx(env)
        inside = _real_contains(ax, x, y)
        if inside and got != [i]:
            bad_hit.append('ax%d 中心(%d,%d) 在真实几何内，但选中 %s'
                           % (i, x, y, got))
        if got != [i]:
            _press_esc(env)
            _click(env, x, y, key='alt')
            if _sel_idx(env) != [i]:
                unreach.append('ax%d（中心点击 + Alt+点击都选不中）' % i)
    plt.close(fig)
    return bad_hit, unreach


# ---- ⑤ 无死锁 ---------------------------------------------------------------
def _inv5_deadlock():
    fig, axes = _make_cb_fig()
    env = _Env(fig, axes, 'inv5')
    before = set(os.listdir(_TMPROOT)) if os.path.isdir(_TMPROOT) else set()
    _drag(env, *env.center(axes[0]), 40, 25)
    _press_esc(env)
    _click(env, *env.center(axes[0]))
    live_ok = bool(env.tw._selected)
    after = set(os.listdir(_TMPROOT)) if os.path.isdir(_TMPROOT) else set()
    nopy = not [f for f in (after - before) if f.endswith('.py')]
    n = _undo_all(env)
    plt.close(fig)
    return (live_ok and nopy and n >= 0,
            'Esc 后仍能选中=%s；手势没写任何 .py=%s；可撤销步数=%d'
            % (live_ok, nopy, n))


# ---- ⑥ 延迟预算（离屏 wall time；真机延迟待真窗口）--------------------------
_BUDGET_P95_MS = 700.0      # 本机基线（Agg/离屏、15×9.5 极地图+colorbar+多艺术家、
#                             单次完整拖拽）：**空载**实测 p50≈212ms、p95≈276ms；
#                             **全量 pytest 满载**时实测 p50≈378ms、p95≈393ms
#                             ⇒ 预算取 700ms（≈1.8× 满载基线，抗机器抖动且能抓住 2× 级
#                             回归；**只覆盖离屏 wall time**，不含 blit/DPR/屏幕刷新
#                             —— 真机延迟待真窗口）。


def _inv6_latency(reps=30):
    import time
    fig, axes = _make_heavy_fig()
    env = _Env(fig, axes, 'inv6')
    ts = []
    for _ in range(reps):
        _press_esc(env)
        x, y = env.center(axes[0])
        t0 = time.perf_counter()
        _drag(env, x, y, 20, 12, steps=4)
        ts.append((time.perf_counter() - t0) * 1000.0)
        _undo_all(env)
    ts.sort()
    p50 = ts[len(ts) // 2]
    p95 = ts[min(len(ts) - 1, int(len(ts) * 0.95))]
    plt.close(fig)
    return (p95 <= _BUDGET_P95_MS,
            'p50=%.1f ms p95=%.1f ms（预算 %.0f ms，%d 次）'
            % (p50, p95, _BUDGET_P95_MS, reps))


def _run_invariants():
    # ---- ① 影响面（含"没什么变化"与"只变了该变的"两种判定）----
    # 手势元组格式与 harness 一致：('drag', ax_i, (pfx,pfy), [offs], (rdx,rdy), snap_ov)
    # / ('click', ax_i, frac, mods) / ('key', k, tgt, ax_i) / ('fit',)
    _inv1_case('§t82① 影响面：拖 ax0 只改 ax0 的字段（画布不许动）',
               [('drag', 0, (0.5, 0.5), [(60, 40)], (60, 40), False)],
               target='ax0')
    _inv1_case('§t82①b 影响面：拖 ax1 只改 ax1 的字段',
               [('drag', 1, (0.5, 0.5), [(-50, 30)], (-50, 30), False)],
               target='ax1')
    _inv1_case('§t82①c 影响面：**裸点击**（无拖拽）一个字段都不许变',
               [('click', 0, (0.5, 0.5), '')], target=None)
    _inv1_case('§t82①d 影响面：Ctrl+F 裁白边只许动画布 + 轴的 pos（位置跟着画布走）',
               [('fit',)], target='canvas', allow_canvas=True, allow_pos=True,
               expect_any=True)

    # ---- ② 撤销完备性 ----
    ok, detail = _inv2_undo([('drag', 0, (0.5, 0.5), [(60, 40)], (60, 40), False)])
    inv('§t82② 撤销完备性：拖拽序列撤销到底 ⇒ 参数**字节级**回到起点', ok, detail)
    ok, detail = _inv2_undo([('drag', 0, (0.5, 0.5), [(60, 40)], (60, 40), False),
                             ('drag', 1, (0.5, 0.5), [(-40, 20)], (-40, 20), False),
                             ('fit',)], target='canvas', allow_canvas=True)
    ok2, detail2 = ok, detail
    inv('§t82②b 撤销完备性：拖拽 + 裁白边混合序列 ⇒ 同样字节级回到起点', ok2, detail2)

    # ---- ③ 命中自洽 / ④ 可达性（当前实现上的**已知缺口** = t82 要证明的那件事）----
    # 两种形状：① 不重叠（常规布局，应当绿）② **colorbar 落在极地图矩形内**（报告 P0-2 的
    # 现场形状：相交处的点被路由给极地图 ⇒ colorbar 既"点不中"也"永远选不中"）。
    for tag, mk in (('polar+cb', _make_heavy_fig), ('imshow+cb', _make_cb_fig),
                    ('cb_inside_polar', _make_cb_inside_polar)):
        bad_hit, unreach = _inv34_hit_reach(mk, tag)
        inv('§t82③ 命中自洽（点哪中哪；独立 patch 几何判据）[%s]' % tag,
            not bad_hit, '；'.join(bad_hit))
        inv('§t82④ 可达性（每个可见对象都有记录在案的路径）[%s]' % tag,
            not unreach, '；'.join(unreach))

    # ---- ⑤ 无死锁 ----
    ok, detail = _inv5_deadlock()
    inv('§t82⑤ 无死锁：手势后仍有逃生路径（Esc 后仍可用 / 不写脚本 / 可撤销）', ok,
        detail)

    # ---- ⑥ 延迟预算 ----
    ok, detail = _inv6_latency()
    inv('§t82⑥ 延迟预算（离屏 wall time，预算 %.0f ms p95；真机 blit/DPR 待真窗口）'
        % _BUDGET_P95_MS, ok, detail)

    # ---- teeth：单点变异 ⇒ 指定不变量必红 ----
    _inv_teeth()
    if _INV_GAPS:
        print('KNOWN | §t82 已知缺口 %d 条（默认模式登记；设 '
              'MPLTWEAK_INVARIANTS_STRICT=1 变硬失败）：%s'
              % (len(_INV_GAPS), '；'.join(_INV_GAPS)))
    return _INV_GAPS


def _inv_teeth():
    '''teeth：把实现改回错误形态 ⇒ 对应不变量必红（每条都实跑一次）。'''
    # ① 影响面：让每次拖拽顺手把**另一个轴**挪一下 ⇒ 逐字段 diff 必报越界
    _orig_motion = Tweak._on_motion

    def _motion_pollute(self, event):
        _orig_motion(self, event)
        try:
            if getattr(self, '_press_pushed', False) and len(self.fig.axes) > 1:
                a = self.fig.axes[-1]
                b = [float(v) for v in _bbox(a)]
                b[0] += 0.001
                _set_bbox(a, b)
        except Exception:                                # noqa: BLE001
            pass

    Tweak._on_motion = _motion_pollute
    try:
        res = _inv1_case('§t82①-teeth（静默：预期这条会红）',
                         [('drag', 0, (0.5, 0.5), [(60, 40)], (60, 40), False)],
                         target='ax0', quiet=True)
    finally:
        Tweak._on_motion = _orig_motion
    check('§t82①【有牙】拖拽时顺手改别的轴 ⇒ 影响面断言必红',
          res['ex_ok'] is False, res['detail'][:120])

    # ② 撤销完备性：让撤销**不回放画布**（= t66 之前的"画布尺寸不进撤销栈"那种钝口径）
    _orig_restore = Tweak._restore_snap

    def _restore_nocanvas(self, snap):
        if isinstance(snap, dict) and snap.get('figsize_in') is not None:
            snap = dict(snap, figsize_in=None)
        return _orig_restore(self, snap)

    Tweak._restore_snap = _restore_nocanvas
    try:
        ok_bad, det_bad = _inv2_undo([('key', 'ctrl+f')], target='canvas',
                                     allow_canvas=True)
    finally:
        Tweak._restore_snap = _orig_restore
    check('§t82②【有牙】撤销不回放画布 ⇒ 撤销完备性（字节级）必红',
          ok_bad is False, det_bad[:120])

    # ⑤ 无死锁：让工具**僵住**（按下事件一律早退 ⇒ 之后任何点击都选不中）⇒ 必红
    _orig_press = Tweak._on_press

    def _press_stuck(self, event):
        return

    Tweak._on_press = _press_stuck
    try:
        ok_bad, det_bad = _inv5_deadlock()
    finally:
        Tweak._on_press = _orig_press
    check('§t82⑤【有牙】手势后工具"僵住"（后续点击不再选中）⇒ 无死锁断言必红',
          ok_bad is False, det_bad[:120])

    # ⑥ 延迟预算牙齿（**相对化**，t116）——
    # 病根（实测）：原来固定 `sleep(0.25)` 去逼"p95 > 预算 700ms"，这**依赖机器速度**：
    #   需要 **基线 p95 > 700 − 250 = 450ms** 才成立。
    #   · CI（ubuntu / py3.12）实测注入后 `p95=664.0ms` ⇒ **仍 ≤ 700 ⇒ 牙齿不咬**（假红，
    #     不是产品回归）；· 本机（负载中）注入后 `p95=808.5ms` ⇒ 咬得住。
    # ⇒ 修法：**先量本机基线 p95**，再注入「预算 − 基线 p95 + 余量150ms」。
    #   注入后 p95 ≈ 基线 + 注入 = 基线 + (预算 − 基线 + 150) = 预算 + 150ms > 预算 ✓
    #   **与机器速度无关**（无论基线是 200ms 还是 500ms 都成立，只要拖拽至少触发 1 次刷新）。
    # ⚠️ **没有放宽产品侧的预算判据**：`_BUDGET_P95_MS` 仍是 700.0，一个字没动。
    _orig_refresh = Tweak._refresh
    import time as _t
    _ok_base, _det_base = _inv6_latency(reps=6)
    _base_p95 = float(_det_base.split('p95=')[1].split(' ms')[0])
    _inject_ms = max(150.0, _BUDGET_P95_MS - _base_p95 + 150.0)

    def _refresh_slow(self, *a, **k):
        _t.sleep(_inject_ms / 1000.0)
        return _orig_refresh(self, *a, **k)

    Tweak._refresh = _refresh_slow
    try:
        ok_bad, det_bad = _inv6_latency(reps=6)
    finally:
        Tweak._refresh = _orig_refresh
    check('§t82⑥【有牙】按**实测基线**注入 %.0fms（= 预算 − 基线 + 150）⇒ 延迟预算（p95）必红'
          % _inject_ms,
          ok_bad is False,
          '基线 %s ｜ 注入后 %s' % (_det_base, det_bad[:70]))


# =====================================================================
# §t71 t71：P0-2 修复的**纯函数 + 逃生通道**验收（拾取优先级 / 真实形状 / Alt 穿透 / Tab 循环）
# 现场形状同 §t82：colorbar 的 rect 落在极地图矩形内（相交带 y∈[0.4651,0.471]）。
# =====================================================================
def _run_t71_units():
    from mpltweak import toolbox as _tb71
    fig, axes = _make_cb_inside_polar()
    env = _Env(fig, axes, 't71')
    pol, cb = axes[0], axes[1]
    x, y = env.center(cb)
    # ① 几何口径（两层）：极地图的**真实形状是圆** ⇒ 外接矩形角上的点不在圆里；
    #    `_body_contains` 第一层用 patch（圆），第二层才退到 window_extent（cax 兜底）。
    _corner = (int(round((pol._position.x0 + 0.002) * env.W)),
               int(round((pol._position.y0 + 0.002) * env.H)))
    try:
        _in_circle = bool(pol.patch.contains_point(
            pol.transAxes.inverted().transform((float(_corner[0]),
                                                float(_corner[1])))))
    except Exception:                                    # noqa: BLE001
        _in_circle = None
    check('§t71① 真实形状口径：极地图**圆**不包含外接矩形角点（patch 层），'
          '`_body_contains` 第二层才退到 window_extent 兜底',
          _in_circle is False and _tb71._body_contains(pol, *_corner) is True,
          'patch 内含=%s / _body_contains=%s'
          % (_in_circle, _tb71._body_contains(pol, *_corner)))
    # ② 拾取优先级：后加入者 + 面积小者优先 ⇒ colorbar 中心的第一候选是 colorbar
    _cands = _tb71._pick_candidates(fig, x, y)
    check('§t71② 拾取优先级（后加入者/面积小者优先）⇒ colorbar 中心第一候选 = colorbar',
          bool(_cands) and _cands[0] is cb and cb in _cands,
          [fig.axes.index(c) for c in _cands])
    # ③ 端到端：点 colorbar 中心 ⇒ 真的选中 colorbar（不再被"缩放 ax0 bottom"抢走）
    _press_esc(env)
    _click(env, x, y)
    check('§t71③ 点 colorbar 中心 ⇒ 选中 colorbar（边缘把手让位给真实形状命中）',
          _sel_idx(env) == [1], _sel_idx(env))
    # ④ 逃生通道①：Alt+点击**穿透**到下一层候选 —— 用"colorbar **完全落在极地图矩形内**"
    #    的形状（重叠带厚、且不贴 colorbar 自己的边），否则那条带正好压着 colorbar 上缘
    #    把手，Alt+按下要留给"Alt+拖端点 = 调长度"的老手势（test_events 在守这条）。
    _fig4 = plt.figure(figsize=(15, 9.5))
    _pol4 = _fig4.add_axes([0.039, 0.4651, 0.9224, 0.4349], projection='polar')
    _sc4 = _pol4.scatter([i * 6.2832 / 60 for i in range(60)],
                         [math.cos(i * 6.2832 / 60) * 3 for i in range(60)],
                         c=[math.cos(i * 6.2832 / 60) for i in range(60)])
    _cax4 = _fig4.add_axes([0.45, 0.60, 0.20, 0.02])
    _fig4.colorbar(_sc4, cax=_cax4, orientation='horizontal')
    _env4 = _Env(_fig4, _fig4.axes, 't71_alt')
    _x4 = int(round((_cax4._position.x0 + _cax4._position.width * 0.5) * _env4.W))
    _y4 = int(round((_cax4._position.y0 + _cax4._position.height * 0.5) * _env4.H))
    _both4 = _tb71._pick_candidates(_fig4, _x4, _y4)
    _click(_env4, _x4, _y4)
    _sel0 = _sel_idx(_env4)
    _click(_env4, _x4, _y4, key='alt')
    _sel1 = _sel_idx(_env4)
    check('§t71④ Alt+点击穿透到下一层候选（colorbar 完全落在极地图内：colorbar → 极地图）',
          len(_both4) >= 2 and _sel0 == [1] and _sel1 == [0],
          '候选=%s 点击后=%s Alt 后=%s'
          % ([_fig4.axes.index(c) for c in _both4], _sel0, _sel1))
    plt.close(_fig4)
    # ⑤ 逃生通道②：Tab 循环逐个走到每个轴（含被压住的）
    _seen = set()
    for _ in range(len(axes) + 1):
        send(fig.canvas, 'key_press_event', x=0, y=0, key='tab')
        _seen |= set(_sel_idx(env))
    check('§t71⑤ Tab 循环覆盖所有可见对象（含被压住的 colorbar）',
          _seen == set(range(len(axes))), sorted(_seen))
    plt.close(fig)

    # teeth：把"真实形状拾取 + 逃生通道"整组关掉（= 只认外接矩形/边缘把手的老口径）
    # ⇒ §t82③ 命中自洽 + §t82④ 可达性**双双必红**
    _orig_pick = _tb71._pick_at
    _orig_cands = _tb71._pick_candidates
    _tb71._pick_at = lambda fig2, x2, y2, skip=0: None
    _tb71._pick_candidates = lambda fig2, x2, y2: []
    try:
        bad_hit, unreach = _inv34_hit_reach(_make_cb_inside_polar, 't71_teeth')
    finally:
        _tb71._pick_at = _orig_pick
        _tb71._pick_candidates = _orig_cands
    check('§t71⑥【有牙】关掉"真实形状拾取"（改回只认外接矩形/边缘把手）⇒ '
          '§t82③ 命中自洽 + §t82④ 可达性**双双必红**',
          bool(bad_hit) and bool(unreach),
          '命中自洽红=%s 可达性红=%s' % ('; '.join(bad_hit)[:80],
                                         '; '.join(unreach)[:80]))


# =====================================================================
# §t92 t92：撤销完备性**逐字段**（显式点名 `canvas.figsize_px` 与 `figsize_in`）
#
# 来历：`t82` 严格模式发现「裁白边（Ctrl+F）后撤销只还原了 `figsize_in`，跟踪的
# `figsize_px` 没还原」⇒ 参数内部不一致（与 P0-1 同源）。`t71` 已修：
#   · `_snapshot(with_canvas=True)` 记 `figsize_tracked = (_fig_px is not None)`；
#   · `_restore_snap` 只在"本来就跟踪过"时写回 `_fig_px`，否则**复位 None**。
# 本节的加严点：**不看字节，看逐字段 diff**（`_diff_fields` 覆盖 canvas 的 px/in 与每轴
# 的每个字段），并显式把 canvas 的 px/in 打进 detail 便于定位。
# =====================================================================
_S92_SEQS = {
    '拖拽': [('drag', 0, (0.5, 0.5), [(60, 40)], (60, 40), False)],
    '拖拽+裁白边': [('drag', 1, (0.5, 0.5), [(-40, 20)], (-40, 20), False),
                    ('fit',)],
    '多操作混合': [('drag', 0, (0.5, 0.5), [(30, 15)], (30, 15), False),
                   ('key', '+', 'title', 1),
                   ('click', 2, (0.5, 0.5), 'ctrl'),
                   ('fit',)],
}


def _s92_fields(ops):
    '''跑 ops → 撤销到底 → **逐字段** diff（含 canvas 的 px/in）。返回 (ok, detail)。'''
    fig, axes = _make_fig()
    env = _Env(fig, axes, 't92_%d' % _case_no[0])
    _case_no[0] += 1
    _raw0, d0 = _params(env)
    crash, _lf = _exec_ops(env, ops, False)
    if crash is not None:
        plt.close(fig)
        return False, '手势抛异常 %r' % (crash[2],)
    n = _undo_all(env)
    _raw1, d1 = _params(env)
    changed = _diff_fields(d0, d1)
    _cv = [(k, d0.get(('canvas', k)), d1.get(('canvas', k)))
           for k in ('figsize_px', 'figsize_in')]
    plt.close(fig)
    return (not changed), ('撤销 %d 次后逐字段差异=%s；canvas(px,in)=%s；字节相同=%s'
                           % (n, changed, _cv, _raw1 == _raw0))


def _run_t92_units():
    for _nm, _ops in _S92_SEQS.items():
        _ok, _det = _s92_fields(_ops)
        check('§t92① 撤销到底后**逐字段**与起点一致（含 canvas.figsize_px/in）[%s]'
              % _nm, _ok, _det)
    # teeth：把 `figsize_px` 的还原**整段去掉**（只还原英寸）⇒ 逐字段断言必红
    _orig_rs92 = Tweak._restore_snap

    def _rs_inch_only(self, snap):
        if isinstance(snap, dict):
            _fi = snap.get('figsize_in')
            if _fi is not None:
                try:
                    self.fig.set_size_inches(float(_fi[0]), float(_fi[1]))
                except (AttributeError, TypeError, ValueError, IndexError):
                    pass
            return _orig_rs92(self, {'axes': snap.get('axes', [])})
        return _orig_rs92(self, snap)

    Tweak._restore_snap = _rs_inch_only
    try:
        _okb, _detb = _s92_fields(_S92_SEQS['拖拽+裁白边'])
    finally:
        Tweak._restore_snap = _orig_rs92
    check('§t92②【有牙】把 `figsize_px` 的还原去掉（只还原英寸）⇒ '
          '§t92① 逐字段断言必红', _okb is False, _detb[:150])


# =====================================================================
# §t101 t101（0.1.12 · MAINTENANCE §25.3）：**sticky drag 自愈**
#
# 真机现场（用户 2026-10-14）：按住子图拖动 → 按 QQ 截图快捷键 → 框选截完 → 松开键，
# 蓝框**仍跟着鼠标走**。根因是外部程序**接管了鼠标**，`button_release` 没送到画布
# （同类触发：窗口失焦 / 模态对话框 / 全局热键）⇒ 工具一直以为"还在拖"。
#
# 修法（照 §25.3 判据，不自创）：motion 报「没有任何键按着」**且**「本次手势确实见过
# 按键」⇒ **当次 motion 内**收尾，收尾走既有 `_on_release`（不复制逻辑）；外加
# `figure_leave_event` 兜底（离开画布且**确证**没键按着才收尾）。
#
# 产品侧还有一道**能力闸门** `_btn_capable`：只有后端真的报过"有键按着"（真机 Qt/Tk
# 每个 motion 都带 `buttons`）才允许用这条判据 —— 否则**离屏合成事件**（不传 `buttons`
# ⇒ 恒为空 frozenset）与历史套件里那些"按下 → motion → 松开"的序列会被误判成"拖完了"，
# 全仓落位断言整片假红。本节 ② 就是钉这条（它守的是**整个仓库的历史形态**）。
#
# 离屏可证 / 待真机：
#   · 离屏可证：判据本身（合成事件能精确摆出"有键 / 无键 / 从没报过键"三种形态）、
#     收尾动作（面板落位 + 蓝框不再跟随）、两类负例、leave 兜底三态、zh/en 两臂。
#   · **待真机**（本套件造不出）：事件真被别的程序吃掉这件事本身，以及 Qt
#     `mouseButtons()` 的实机读数（`_mouse_buttons_down` 在离屏这里恒返回 None）。
# =====================================================================
_LBTN = frozenset({1})      # 真机 motion 形态：左键按着（MouseEvent.buttons 是 frozenset）


def _t101_env(tag):
    fig, axes = _make_fig()
    return fig, axes, _Env(fig, axes, tag)


def _t101_press(env, x, y):
    send(env.fig.canvas, 'button_press_event', int(x), int(y), button=1)


def _t101_motion(env, x, y, buttons=None):
    '''合成 motion。不传 `buttons` = 历史套件的"老形态"（离屏事件不表达按键状态）。'''
    send(env.fig.canvas, 'motion_notify_event', int(x), int(y), buttons=buttons)


def _t101_frame(env, ax):
    '''蓝框（选中外框 Rectangle）的 bounds；没挂框返回 None。

    读的是 Rectangle 自己的几何（`get_x/get_y/get_width/get_height`），那是 fig 比例
    坐标下的**真值** —— 蓝框有没有跟着鼠标跑，就看它。
    '''
    r = getattr(env.tw, '_sel_map', {}).get(id(ax))
    if r is None:
        return None
    return [float(r.get_x()), float(r.get_y()),
            float(r.get_width()), float(r.get_height())]


def _run_t101_units():
    # ---- ① 正例：真机形态（motion 报"左键按着"）→ 键被抢走后 motion 报空 ----
    fig, axes, env = _t101_env('t101_heal')
    tw, ax0 = env.tw, axes[0]
    x0, y0 = env.center(ax0)
    p0 = env.pos(ax0)
    _t101_press(env, x0, y0)
    check('§t101①a 按下 ⇒ 进入拖动（蓝框挂上，位置未变）',
          bool(tw._drag) and tw._ax is ax0 and tw._drag_active()
          and _t101_frame(env, ax0) == p0,
          'drag=%s frame=%s pos=%s' % (tw._drag, _t101_frame(env, ax0), p0))
    _t101_motion(env, x0 + 40, y0 + 20, buttons=_LBTN)      # 真机：键还按着
    p1 = env.pos(ax0)
    check('§t101①b 带按键的 motion 照常跟手（判据不许提前收尾）',
          bool(tw._drag) and p1 != p0 and _t101_frame(env, ax0) == p1,
          'drag=%s 位移 ok=%s 蓝框=%s' % (tw._drag, p1 != p0, _t101_frame(env, ax0)))
    # 外部程序（QQ 截图）抢走鼠标：键在**画布外**松开 ⇒ 下一个 motion 的 buttons 为空
    _t101_motion(env, x0 + 80, y0 + 40)                     # 就是这一下必须收尾
    p2 = env.pos(ax0)
    check('§t101①c **当次 motion 内**拖动已结束（不等 button_release）',
          not tw._drag and tw._ax is None and tw._target is None
          and not tw._drag_active(), 'drag=%s ax=%s' % (tw._drag, tw._ax))
    check('§t101①d 面板落在"键丢失那一下"之前的位置（无按键的 motion 不再跟手）',
          p2 == p1 and _t101_frame(env, ax0) == p1,
          'p1=%s p2=%s 蓝框=%s' % (p1, p2, _t101_frame(env, ax0)))
    _t101_motion(env, x0 + 160, y0 + 100, buttons=_LBTN)
    _t101_motion(env, x0 + 240, y0 + 140)
    check('§t101①e 收尾后再怎么动，蓝框/面板一个像素都不动',
          env.pos(ax0) == p2 and _t101_frame(env, ax0) == p2,
          'now=%s 蓝框=%s' % (env.pos(ax0), _t101_frame(env, ax0)))
    plt.close(fig)

    # ---- ② 负例 A：**老形态**合成拖拽（按下 + motion 不带 buttons）不许自愈 ----
    # 这条守的是"整个仓库的历史形态"：test_events / test_fuzz_events 的所有落位断言
    # 都是"按下 → 若干 motion → 松开"，一旦误触发自愈，它们会整片假红。
    fig, axes, env = _t101_env('t101_legacy')
    tw, ax0 = env.tw, axes[0]
    x0, y0 = env.center(ax0)
    p0 = env.pos(ax0)
    _t101_press(env, x0, y0)
    _t101_motion(env, x0 + 40, y0 + 20)                     # 不带 buttons（老形态）
    check('§t101② 老形态 motion（不表达按键）⇒ 拖动照旧，不许自愈',
          bool(tw._drag) and env.pos(ax0) != p0 and not tw._btn_capable,
          'drag=%s capable=%s' % (tw._drag, tw._btn_capable))
    send(fig.canvas, 'button_release_event', x0 + 40, y0 + 20, button=1)
    check('§t101②b 真松手照旧收尾（自愈没抢掉正常路径）',
          not tw._drag and not tw._btn_seen, 'drag=%s' % tw._drag)
    plt.close(fig)

    # ---- ③ 负例 B：没有按键史的纯 hover 不许自愈 ----
    fig, axes, env = _t101_env('t101_hover')
    tw, ax0 = env.tw, axes[0]
    x0, y0 = env.center(ax0)
    p0 = env.pos(ax0)
    for _d in (12, 24, 36):
        _t101_motion(env, x0 + _d, y0 + _d)                 # 从没按下过
    check('§t101③ 无按键史的 hover 序列不误触发（状态与布局都不动）',
          not tw._drag and not tw._btn_seen and not tw._btn_capable
          and env.pos(ax0) == p0, 'pos=%s' % (p0,))
    plt.close(fig)

    # ---- ④ leave 兜底三态（figure_leave_event 本身**不带**按键状态）----
    _orig_mbd = _tb._mouse_buttons_down
    try:
        for _tag, _val, _want_heal in (('确证没键按着', False, True),
                                       ('确证还按着', True, False),
                                       ('问不出来（Tk/Agg/无 Qt）', None, False)):
            fig, axes, env = _t101_env('t101_leave_%s' % _tag[:4])
            tw, ax0 = env.tw, axes[0]
            x0, y0 = env.center(ax0)
            _t101_press(env, x0, y0)
            _t101_motion(env, x0 + 30, y0 + 15, buttons=_LBTN)
            _tb._mouse_buttons_down = (lambda canvas, _v=_val: _v)
            send(fig.canvas, 'figure_leave_event', x0 + 30, y0 + 15)
            check('§t101④ 离画布兜底（%s）⇒ %s' % (_tag, '收尾' if _want_heal else '不打断'),
                  (not tw._drag) if _want_heal else bool(tw._drag),
                  'drag=%s' % tw._drag)
            plt.close(fig)
    finally:
        _tb._mouse_buttons_down = _orig_mbd
    # 离画布时**没有拖动** ⇒ 什么都不做（别在空状态下乱跑收尾逻辑）
    fig, axes, env = _t101_env('t101_leave_idle')
    send(fig.canvas, 'figure_leave_event', *env.center(axes[0]))
    check('§t101④d 没在拖动时离开画布 ⇒ 无事发生（不抛异常、不改布局）',
          not env.tw._drag_active() and env.pos(axes[0]) is not None)
    plt.close(fig)

    # ---- ⑤ 能力探针：Agg 画布问不出全局按键状态 ⇒ 必须是 None（不瞎猜）----
    fig, axes, env = _t101_env('t101_probe')
    check('§t101⑤ 离屏 Agg 画布 `_mouse_buttons_down` 返回 None（判不了就不动）',
          _tb._mouse_buttons_down(fig.canvas) is None,
          repr(_tb._mouse_buttons_down(fig.canvas)))
    plt.close(fig)

    # ---- ⑥ zh / en 两臂跑自愈路径：**零新增文案** ----
    _t101_lang_arms()

    # ---- ⑦ teeth：把自愈分支关掉 ⇒ ①c 必红；把"确证没键"的判据改坏 ⇒ ④ 必红 ----
    _t101_teeth()


def _t101_lang_arms():
    '''⑥ zh/en 两臂：**自愈路径本身不产生任何新文案**。

    为什么口径是"零新增文案"而不是"文案对不对"：这一项没有（也不该有）新的用户可见
    文本 —— 自愈是**静默**的后台收尾，走的是既有 `_on_release`。所以判据取"跑一遍
    自愈，stdout / stderr 一个字都不多"：两臂都如此 ⇒ 不存在"zh 有、en 漏翻译"或
    "en 混进中文（非纯 ASCII）"的可能。真正会加文案的那类改动由
    `test_msg_key_audit.py` / `test_launch.py §9` 盯（本项与它们互不影响）。
    '''
    from mpltweak import messages as _msg
    bad = []
    for _lang in ('zh', 'en'):
        _prev = _msg.set_lang(_lang)
        try:
            fig, axes, env = _t101_env('t101_lang_' + _lang)
            x0, y0 = env.center(axes[0])
            _out, _err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(_out), contextlib.redirect_stderr(_err):
                _t101_press(env, x0, y0)
                _t101_motion(env, x0 + 40, y0 + 20, buttons=_LBTN)
                _t101_motion(env, x0 + 80, y0 + 40)          # 自愈在这里发生
            healed = not env.tw._drag_active()
            if not healed or _out.getvalue() or _err.getvalue():
                bad.append((_lang, healed, _out.getvalue()[:60],
                            _err.getvalue()[:60]))
            plt.close(fig)
        finally:
            _msg.set_lang(_prev)
    check('§t101⑥ zh / en 两臂都自愈成功，且 stdout/stderr 零输出（无新增文案）',
          not bad, bad[:2])


def _t101_teeth():
    '''⑦ teeth：两条断言各自"拆掉实现就必红"。'''
    _orig_heal = Tweak._self_heal_drag

    def _no_heal(self, event):
        return False

    Tweak._self_heal_drag = _no_heal
    try:
        fig, axes, env = _t101_env('t101_teeth_heal')
        tw, ax0 = env.tw, axes[0]
        x0, y0 = env.center(ax0)
        _t101_press(env, x0, y0)
        _t101_motion(env, x0 + 40, y0 + 20, buttons=_LBTN)
        _t101_motion(env, x0 + 80, y0 + 40)                 # 该收尾的那一下
        _still_dragging = bool(tw._drag)
        plt.close(fig)
    finally:
        Tweak._self_heal_drag = _orig_heal
    check('§t101⑦【有牙】把自愈分支关掉 ⇒ "当次 motion 内结束"必红（这里仍挂着拖动）',
          _still_dragging is True, '_drag=%s' % _still_dragging)

    _orig_mbd = _tb._mouse_buttons_down
    try:
        _tb._mouse_buttons_down = (lambda canvas: True)     # 判据被改坏：永远"还按着"
        fig, axes, env = _t101_env('t101_teeth_leave')
        tw, ax0 = env.tw, axes[0]
        x0, y0 = env.center(ax0)
        _t101_press(env, x0, y0)
        _t101_motion(env, x0 + 30, y0 + 15, buttons=_LBTN)
        send(fig.canvas, 'figure_leave_event', x0 + 30, y0 + 15)
        _no_heal_on_leave = bool(tw._drag)
        plt.close(fig)
    finally:
        _tb._mouse_buttons_down = _orig_mbd
    check('§t101⑦b【有牙】把"确证没键按着"改坏（永远 True）⇒ ④ 的收尾断言必红',
          _no_heal_on_leave is True, '_drag=%s' % _no_heal_on_leave)


def main():
    # 「hypothesis 装没装」是**环境事实**，不是被测行为：装了照旧断言它可用；
    # 没装则**明确打印跳过原因**（而不是把一个"环境缺可选依赖"记成测试失败 ——
    # CI 的 step 6 按设计只装 pytest，见 tests/_hypothesis_shim.py 的说明）。
    if _HAS_HYPOTHESIS:
        check('hypothesis 可用（pyproject [dev] 应含 hypothesis>=6.0）', True,
              'hypothesis %s' % __import__('hypothesis').__version__)
    else:
        print('SKIP | hypothesis 未安装 → 下面两条 property 不执行；'
              '命名用例与 teeth 探针照常执行（装上 hypothesis 即跑全部）')

    _run_named()
    _case_mixed_legend_panel()
    _case_colorbar()
    _teeth_probes()
    _run_invariants()
    _run_t71_units()
    _run_t92_units()
    _run_t101_units()

    if _HAS_HYPOTHESIS:
        _run_hyp('hypothesis 任意乱序手势：不崩/往返/几何（不验落位）',
                 prop_fuzz_mixed)
        _run_hyp('hypothesis 完整拖拽序列：不崩/往返/落位/几何（全验）',
                 prop_fuzz_drag)

    shutil.rmtree(_TMPROOT, ignore_errors=True)
    print('FAILED: %d' % fail if fail else 'ALL PASS')
    return 1 if fail else 0


def test_fuzz_events_suite():
    """pytest 入口：本文件是脚本式套件（§t101 / §32 全在 ``main()`` 里）。

    为什么需要这一段（0.1.12 / t10）：收尾原先写成**模块级**的
    ``sys.exit(main())`` —— ``python -m pytest -q tests/test_fuzz_events.py`` 在 import
    期就被 SystemExit 带走（INTERNALERROR，rc=3），于是
    ``tools/ci_pytest_summary.py`` 那条「全量 pytest」CI 腿只要收集到本文件就整体崩。
    现在把 ``sys.exit`` 收进 ``if __name__ == '__main__':``，并补这个入口断言结论。

    两种调用方式结论一致：
      * ``python tests/test_fuzz_events.py`` → 逐条 PASS + ``ALL PASS`` + rc=0；
      * ``python -m pytest -q tests/test_fuzz_events.py`` → 1 passed。
    """
    assert main() == 0


if __name__ == '__main__':
    sys.exit(main())
