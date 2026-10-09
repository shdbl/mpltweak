# -*- coding: utf-8 -*-
# ======================================================================
# tweak_toolbox.py —— gaitu v3（改图工具箱）
# v2 基础上新增：
#   1. 图例：按 l 循环 6 个标准位置；鼠标直接拖图例框自由移动
#   2. 字号：[] 标题 / -= 轴标签 / ,. 刻度 / ;' 图例文字（悬停哪个轴改哪个）
#   3. 自动对齐：拖动子图时边缘自动吸附到其它子图边缘（容差内），
#      并画出对齐参考线；n 键开关
#   4. 关窗口自动导出 JSON（无感：拖完直接关，坐标自动落盘）
# 保留 v2：重图 ghost+blit 不卡；锁长宽比地图等比缩放；e 手动导出；
#          右键打印坐标；模式固定为重图（无切换键）。
# 用法：
#   from tweak_toolbox import gaitu
#   gaitu(fig, export_path='fig1.tweak.json')
#   plt.show()
# 快捷键（鼠标交互为主，子图位置/大小不用键盘）：
#   拖面板=移动；拖边框/角=PPT式缩放（对边固定，锁比轴等比）
#   悬停文本元素按 +/-：标题/x轴标签/y轴标签/刻度/图例/colorbar 各调各的字号
#   多选（PPT 式）：Ctrl+点击（或 Shift+点击）加选/减选，或拖空白框选
#                    （框选需「完全框住」面板才选中，相交不算）；
#                    点击单个面板会显示蓝色选中框；拖任一个选中的面板=整体移动
#   方向键微调尺寸（中心不动，先选中/悬停目标）：↑加高 ↓减矮 →加宽 ←减窄
#   Ctrl+Z 撤销 / Ctrl+Y、Ctrl+Shift+Z 重做（位置+字号+图例全量快照）
#   拖拽时窗口标题栏实时显示尺寸（W×H px）
#   图例：拖动图例自由跟手，松手自动吸附到最近的 8 位之一（不支持键位循环）
#   其它：e 导出；n 吸附开关（含画布边界 0/1）；关窗自动导出；
#        拖窗口大小=改画布尺寸；**模式固定为重图**（ghost+blit，无轻/重切换）
# 口径与取舍：STEP=0.01、MIN_SIZE=0.02（面板最小）/MIN_SIZE_CB=0.008（colorbar 可更细）、
#            HEAVY_MS=45、COMMIT_MS=280、SNAP_TOL=0.006（吸附容差）、
#            LEGEND_LOCS 八个标准位、EDGE_TOL=6px / CORNER_TOL=8px（边框/角抓取区）。
# ======================================================================

import os
import sys
import json
import time
import math

import matplotlib
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
from matplotlib.lines import Line2D
from matplotlib.backend_tools import Cursors
from matplotlib.backend_bases import MouseEvent

# ---- 口径常量（置顶，带依据） ----
STEP = 0.01          # 每按键一格的 figure 比例（沿用原版 0.01）
NUDGE_STEP = 0.005   # 方向键微调尺寸的每次增量（中心不动）
NUDGE_STEP_COARSE = 0.025   # Shift+方向键：5 倍步长的粗调
MIN_SIZE = 0.02      # 坐标框最小宽/高（figure 比例），防等比缩小时塌成 0
MIN_SIZE_CB = 0.008  # colorbar 的最小宽度（可更细，约 8px@1000px）
FIT_MARGIN_IN = 0.15 # 「适配画布到内容」(f键) 保留的白边，单位英寸（100dpi 逻辑口径）
HEAVY_MS = 45        # 仅作参考：早期用来自动判"重图"；现在模式固定重图，该值只用于
                     # 诊断打印（全量重绘耗时）时的对比说明，不再决定行为
COMMIT_MS = 280      # ghost 模式落位防抖毫秒：停键这么久才真正 set_position
SNAP_TOL = 0.006     # 子图边缘吸附容差（figure 比例）
EDGE_TOL = 6         # 边框拖拽手柄的抓取区（像素，PPT 手感）
CORNER_TOL = 8       # 角点拖拽手柄的抓取区（像素）
SEL_EDGE = '#1f77b4'   # 选中面板外框颜色（PPT 蓝）
BAND_EDGE = '#1f77b4'  # 橡皮筋框选颜色

QUIET = False          # 安静模式：交互期不打印任何 [mpltweak] 日志（供外部启动器用）
CLIM_TIP_TOL = 10      # colorbar 长轴端点的 clim 抓取区（像素）
CLIM_MIN_SPAN = 1e-9   # clim 端点不允许完全重合
LINE_LW_STEP = 0.5
LINE_LW_MIN = 0.5
LINE_LW_MAX = 12.0
_LINE_COLORS = ('black', 'tab:blue', 'tab:orange', 'tab:green', 'tab:red',
                'tab:purple', 'tab:brown', 'tab:pink', 'tab:gray', 'tab:olive',
                'darkgreen', 'darkblue')
_CMAPS = ('viridis', 'plasma', 'inferno', 'magma', 'cividis', 'coolwarm',
          'RdBu_r', 'BrBG', 'PuOr_r', 'turbo', 'gray')


def _log(*args, **kwargs):
    '''[mpltweak] 日志：安静模式下静默（关窗只留参数文件，不刷屏）。'''
    if not QUIET:
        print(*args, **kwargs)


def _info(*args, **kwargs):
    '''关键运行状态（heavy 判定 / blit 降级）：安静模式下**也要**打印——
    这两条直接决定"卡不卡"，必须让用户/agent 看得见。'''
    print(*args, **kwargs)
LEGEND_LOCS = ['upper right', 'upper center', 'upper left', 'center left',
               'lower left', 'lower center', 'lower right', 'center right']  # 8 个标准位   # 图例六个标准位
_LOC_CODES = {0: 'best', 1: 'upper right', 2: 'upper left', 3: 'lower left',
              4: 'lower right', 5: 'right', 6: 'center left', 7: 'center right',
              8: 'lower center', 9: 'upper center', 10: 'center'}   # loc 整数码->名

# ============ ? 键位表独立窗口（快捷键手册风格，中英双语） ============
# 结构化数据：每组 = (组名, [(键帽, 说明), ...])。键帽会渲染成圆角胶囊。
_ZH = [
    ('选择', [
        ('拖面板', '移动'),
        ('拖边/拖角', '缩放'),
        ('Shift+拖角', '等比缩放'),
        ('Alt+拖角', '中心对称'),
    ]),
    ('多选', [
        ('Ctrl+点击', '加选'),
        ('Shift+点击', '加选'),
        ('拖空白', '框选'),
    ]),
    ('移动', [
        ('方向键', '移动选中'),
        ('Shift+方向键', '微调尺寸'),
    ]),
    ('对齐', [
        ('Ctrl+Shift+L', '左对齐'),
        ('Ctrl+Shift+R', '右对齐'),
        ('Ctrl+Shift+T', '顶对齐'),
        ('Ctrl+Shift+B', '底对齐'),
        ('Ctrl+Shift+C', '水平居中'),
        ('Ctrl+Shift+M', '垂直居中'),
    ]),
    ('均分', [
        ('Ctrl+Shift+H', '水平等间距'),
        ('Ctrl+Shift+V', '垂直等间距'),
    ]),
    ('字号（悬停文字，按 + / -）', [
        ('标题', '调字号'),
        ('轴标', '调字号'),
        ('刻度', '调字号'),
        ('图例', '调字号'),
    ]),
    ('图例', [
        ('拖图例', '移动（松手吸附 8 位）'),
    ]),
    ('样式', [
        ('[ / ]', '线宽'),
        ('c', '线条颜色'),
        ('C', 'colormap'),
        ('g', '网格'),
        ('s', '边框'),
        ('x / y', 'log 轴'),
    ]),
    ('画布', [
        ('Ctrl+F', '裁白边'),
        ('n', '吸附开关'),
        ('空格', '线条模式'),
        ('Ctrl+Q', '关全部窗口'),
    ]),
    ('历史', [
        ('Ctrl+Z', '撤销'),
        ('Ctrl+Y', '重做'),
        ('Ctrl+Shift+Z', '重做'),
    ]),
]
_EN = [
    ('Select', [
        ('Drag panel', 'move'),
        ('Drag edge/corner', 'resize'),
        ('Shift+drag corner', 'keep ratio'),
        ('Alt+drag corner', 'center anchor'),
    ]),
    ('Multi-select', [
        ('Ctrl+click', 'add to selection'),
        ('Shift+click', 'add to selection'),
        ('Drag empty', 'marquee select'),
    ]),
    ('Move', [
        ('Arrow keys', 'move selection'),
        ('Shift+arrow', 'fine resize'),
    ]),
    ('Align', [
        ('Ctrl+Shift+L', 'align left'),
        ('Ctrl+Shift+R', 'align right'),
        ('Ctrl+Shift+T', 'align top'),
        ('Ctrl+Shift+B', 'align bottom'),
        ('Ctrl+Shift+C', 'center horizontally'),
        ('Ctrl+Shift+M', 'center vertically'),
    ]),
    ('Distribute', [
        ('Ctrl+Shift+H', 'even spacing'),
        ('Ctrl+Shift+V', 'even spacing'),
    ]),
    ('Font size (hover text, +/-)', [
        ('Title', 'resize'),
        ('Axis label', 'resize'),
        ('Tick label', 'resize'),
        ('Legend', 'resize'),
    ]),
    ('Legend', [
        ('Drag legend', 'move (snaps to 8 spots)'),
    ]),
    ('Style', [
        ('[ / ]', 'line width'),
        ('c', 'line color'),
        ('C', 'colormap'),
        ('g', 'grid'),
        ('s', 'spines'),
        ('x / y', 'log scale'),
    ]),
    ('Canvas', [
        ('Ctrl+F', 'trim to content'),
        ('n', 'snap toggle'),
        ('Space', 'wireframe mode'),
        ('Ctrl+Q', 'close all windows'),
    ]),
    ('History', [
        ('Ctrl+Z', 'undo'),
        ('Ctrl+Y', 'redo'),
        ('Ctrl+Shift+Z', 'redo'),
    ]),
]
_KEYMAP = {'zh': _ZH, 'en': _EN}
_HELP_LANG = 'zh'          # 当前语言（? 窗口内点按钮 / 按 L 切换）
_HELP_LANG_BTNS = []       # 语言按钮命中矩形（像素坐标）
_HELP_FIG = None


def _pick_ui_font():
    '''跨平台可用字体探测：Windows 雅黑 → macOS PingFang → Linux Noto，
    都没有就回退 Segoe/Helvetica/Arial；全没有返回 None（matplotlib 默认）。'''
    try:
        from matplotlib import font_manager as _fm
        _avail = {f.name for f in _fm.fontManager.ttflist}
        for _cand in ('Microsoft YaHei', 'PingFang SC', 'Hiragino Sans GB',
                      'Noto Sans CJK SC', 'Source Han Sans SC',
                      'WenQuanYi Micro Hei', 'SimHei', 'DengXian',
                      'Segoe UI', 'Helvetica Neue', 'Arial'):
            if _cand in _avail:
                return _cand
    except Exception:                        # noqa: BLE001
        pass
    return None


def _text_w(r, s, fs, font, dpi=110.0):
    '''估算文本像素宽度：TextPath 按字号量出宽度（单位"点"）→ 乘 dpi/72 换算像素。
    （直接用 get_window_extent：无 transform 时返回"点"当像素用会偏小 ~35%，
    导致键帽胶囊不够宽、文字与说明重叠。）'''
    try:
        from matplotlib.textpath import TextPath
        from matplotlib.font_manager import FontProperties
        _tp = TextPath((0, 0), s, size=fs, prop=FontProperties(family=font))
        return max(float(_tp.get_extents().width) * dpi / 72.0, 6.0)
    except Exception:                        # noqa: BLE001
        _m = 0.62 if all(ord(c) < 256 for c in s) else 1.0
        return max(len(s) * fs * _m * dpi / 72.0, 10.0)


def _render_help(fig, lang, font):
    '''在帮助窗口画布上渲染"快捷键手册"：顶部强调条 + 两列圆角卡片
    （左侧彩色条 + 组标题 + 键帽胶囊 + 说明）+ 底部语言切换。
    坐标一律用 fig.dpi_scale_trans（单位 = 英寸 = 像素/dpi），圆角各向同性。'''
    global _HELP_LANG_BTNS
    from matplotlib.patches import FancyBboxPatch
    fig.clear()
    try:
        fig.canvas.draw()
    except Exception:                        # noqa: BLE001
        pass
    try:
        _r = fig.canvas.get_renderer()
    except Exception:                        # noqa: BLE001
        _r = None
    # 布局一律按"设计基准 dpi"的逻辑像素换算英寸——与 fig 实际 dpi（是否 2x 高清）
    # 解耦：否则 2x 渲染时间距物理尺寸减半、字号(pt)不变 → 行距重叠。
    _BASE_DPI = 110.0
    _W, _H = (float(x) for x in fig.get_size_inches() * _BASE_DPI)
    _grps = _KEYMAP.get(lang, _ZH)
    _accent = '#2F6FEB'                       # 品牌蓝
    _dark, _grey = '#16181D', '#4B5563'
    try:
        fig.patch.set_facecolor('#F6F7F9')    # 浅灰背景，白色卡片更有层次
    except Exception:                        # noqa: BLE001
        pass

    def _x(px):
        return px / _BASE_DPI                # 逻辑像素 → 英寸

    def _y(py):
        return py / _BASE_DPI

    # 顶部强调条
    fig.add_artist(Rectangle((_x(0), _y(_H - 5)), _x(_W), _y(5),
                             transform=fig.dpi_scale_trans,
                             facecolor=_accent, zorder=3))
    # 标题
    fig.text(_x(_W / 2), _y(_H - 38),
             'mpltweak · 快捷键' if lang == 'zh'
             else 'mpltweak · Shortcuts',
             ha='center', va='center', fontsize=16, fontweight='bold',
             color=_dark, fontfamily=font, transform=fig.dpi_scale_trans)
    # 两列：平衡分配（更矮的列接收下一组）
    _margin, _gap = 26, 26
    _col_w = int((_W - 2 * _margin - _gap) / 2)
    _col_x = [_margin, _margin + _col_w + _gap]
    _col_y = [_H - 62, _H - 62]
    _pad, _title_h, _row_h = 14, 26, 26
    _half = (len(_grps) + 1) // 2          # 前一半左列、后一半右列（保持阅读顺序且高度均衡）
    for _gi, (_gn, _rows) in enumerate(_grps):
        _hgt = _pad + _title_h + len(_rows) * _row_h + 8
        _ci = 0 if _gi < _half else 1
        _cx, _cy_top = _col_x[_ci], _col_y[_ci]
        _cy_bot = _cy_top - _hgt
        # 卡片阴影（错位浅灰）
        fig.add_artist(FancyBboxPatch((_x(_cx + 2), _y(_cy_bot - 2)),
                                      _x(_col_w), _y(_hgt),
                                      boxstyle='round,pad=0,rounding_size=%f' %
                                      (10 / _BASE_DPI),
                                      transform=fig.dpi_scale_trans,
                                      facecolor='#E4E6EA', edgecolor='none',
                                      lw=0, zorder=1))
        # 卡片
        fig.add_artist(FancyBboxPatch((_x(_cx), _y(_cy_bot)),
                                      _x(_col_w), _y(_hgt),
                                      boxstyle='round,pad=0,rounding_size=%f' %
                                      (10 / _BASE_DPI),
                                      transform=fig.dpi_scale_trans,
                                      facecolor='white', edgecolor='#E3E6EA',
                                      lw=1.0, zorder=2))
        # 左侧彩色条
        fig.add_artist(Rectangle((_x(_cx + 1), _y(_cy_bot + 6)), _x(3),
                                 _y(_hgt - 12),
                                 transform=fig.dpi_scale_trans,
                                 facecolor=_accent, zorder=3))
        # 组标题
        fig.text(_x(_cx + _pad + 8), _y(_cy_top - _pad - 4), _gn, ha='left',
                 va='center', fontsize=11.5, fontweight='bold', color='#1B1E24',
                 fontfamily=font, transform=fig.dpi_scale_trans)
        # 键帽胶囊 + 说明
        _yy = _cy_top - _pad - _title_h - _row_h / 2 + 4
        for _k, _d in _rows:
            _pw = _text_w(_r, _k, 9, font, _BASE_DPI) + 18
            fig.add_artist(FancyBboxPatch((_x(_cx + _pad + 8), _y(_yy - 9.5)),
                                          _x(_pw), _y(19),
                                          boxstyle='round,pad=0,rounding_size=%f' %
                                          (6 / _BASE_DPI),
                                          transform=fig.dpi_scale_trans,
                                          facecolor='#F2F4F7',
                                          edgecolor='#CFD5DD', lw=0.8, zorder=3))
            fig.text(_x(_cx + _pad + 8 + _pw / 2), _y(_yy), _k, ha='center',
                     va='center', fontsize=9, color='#2A2E35',
                     fontfamily=font, transform=fig.dpi_scale_trans)
            fig.text(_x(_cx + _pad + 8 + _pw + 12), _y(_yy), _d, ha='left',
                     va='center', fontsize=9.5, color=_grey, fontfamily=font,
                     transform=fig.dpi_scale_trans)
            _yy -= _row_h
        _col_y[_ci] = _cy_bot - 17
    # 底部：语言按钮（右下）+ 极简提示（左下）
    _HELP_LANG_BTNS = []
    _bx = _W - _margin
    for _lg, _lb in (('zh', '中'), ('en', 'EN')):
        _bw = _text_w(_r, _lb, 10, font, _BASE_DPI) + 18
        _bx -= _bw
        _on = _lg == lang
        fig.add_artist(FancyBboxPatch((_x(_bx), _y(14)), _x(_bw), _y(21),
                                      boxstyle='round,pad=0,rounding_size=%f' %
                                      (8 / _BASE_DPI),
                                      transform=fig.dpi_scale_trans,
                                      facecolor=_accent if _on else '#EDEFF2',
                                      edgecolor=_accent if _on else '#D5DAE0',
                                      lw=0.8, zorder=3))
        fig.text(_x(_bx + _bw / 2), _y(24.5), _lb, ha='center', va='center',
                 fontsize=10, color='white' if _on else '#3A3F47',
                 fontfamily=font, transform=fig.dpi_scale_trans)
        _HELP_LANG_BTNS.append((_lg, (_bx, 14, _bx + _bw, 35)))
        _bx -= 10
    _hint = ('按 ? 置顶 · Esc 关闭 · L language' if lang == 'zh'
             else '? bring to front · Esc close · L language')
    fig.text(_x(_margin), _y(24.5), _hint, ha='left', va='center', fontsize=8.5,
             color='#8A919C', fontfamily=font, transform=fig.dpi_scale_trans)
    try:
        fig.canvas.draw_idle()
    except Exception:                        # noqa: BLE001
        pass


def _load_help_png(lang):
    '''加载预渲染键位表高清 PNG（包资源）。失败返回 None → 降级内联渲染。'''
    _name = 'help_zh.png' if lang == 'zh' else 'help_en.png'
    try:
        import importlib.resources as _res
        _data = _res.files('mpltweak').joinpath('resources', _name).read_bytes()
    except Exception:                        # noqa: BLE001
        return None
    try:
        from io import BytesIO
        from matplotlib.image import imread
        return imread(BytesIO(_data))
    except Exception:                        # noqa: BLE001
        return None


def _fill_help_fig(fig, lang):
    '''把帮助内容填进 fig：优先预渲染高清 PNG（像素级，用户环境零字体依赖），
    资源缺失（如未打包的开发环境）时降级为 matplotlib 内联渲染。'''
    _img = _load_help_png(lang)
    if _img is not None:
        try:
            fig.clear()
            _ax = fig.add_axes([0, 0, 1, 1])
            _ax.imshow(_img, aspect='auto')
            _ax.axis('off')
            try:
                fig.canvas.draw_idle()
            except Exception:                # noqa: BLE001
                pass
            return
        except Exception:                    # noqa: BLE001
            fig.clear()
    _render_help(fig, lang, _pick_ui_font())


def _on_help_key(event):
    _k = str(getattr(event, 'key', '') or '')
    if _k == 'escape':
        _close_help_fig()
    elif _k in ('l', 'L'):
        _toggle_help_lang()


def _on_help_click(event):
    _x, _y = getattr(event, 'x', -1), getattr(event, 'y', -1)
    if _x < 0 or _y < 0:
        return
    for _lg, (_x0, _y0, _x1, _y1) in _HELP_LANG_BTNS:
        if _x0 <= _x <= _x1 and _y0 <= _y <= _y1:
            _toggle_help_lang()
            return


def _toggle_help_lang():
    global _HELP_LANG
    _HELP_LANG = 'en' if _HELP_LANG == 'zh' else 'zh'
    if _HELP_FIG is not None:
        try:
            _HELP_FIG.canvas.manager.set_window_title(
                'mpltweak · 快捷键' if _HELP_LANG == 'zh'
                else 'mpltweak · Shortcuts')
        except Exception:                    # noqa: BLE001
            pass
        _fill_help_fig(_HELP_FIG, _HELP_LANG)


def _on_help_closed(_event=None):
    '''帮助窗口被关闭（点 × / Ctrl+Q / launch 清理）→ 置空单例。'''
    global _HELP_FIG
    _HELP_FIG = None


def _show_help_fig():
    global _HELP_FIG
    if _HELP_FIG is not None:
        # 已开着 → 重新置顶（不重复创建）
        try:
            _w = _HELP_FIG.canvas.manager.window
            _w.raise_()
            _w.activateWindow()
        except Exception:                    # noqa: BLE001
            try:
                _HELP_FIG.canvas.manager.show()
            except Exception:
                pass
        return
    _hf = plt.figure(figsize=(7.2, 8.8), dpi=110)
    _HELP_FIG = _hf
    try:
        _hf.patch.set_facecolor('white')
    except Exception:                        # noqa: BLE001
        pass
    _fill_help_fig(_hf, _HELP_LANG)
    try:
        _hf.canvas.manager.set_window_title(
            'mpltweak · 快捷键' if _HELP_LANG == 'zh'
            else 'mpltweak · Shortcuts')
    except Exception:                        # noqa: BLE001
        pass
    _hf.canvas.mpl_connect('close_event', _on_help_closed)
    _hf.canvas.mpl_connect('key_press_event', _on_help_key)
    _hf.canvas.mpl_connect('button_press_event', _on_help_click)
    try:
        _tb = getattr(_hf.canvas.manager, 'toolbar', None)
        if _tb is not None:
            _tb.hide()
    except Exception:                        # noqa: BLE001
        pass
    try:
        _hf.canvas.manager.show()
    except Exception:                        # noqa: BLE001
        pass


def _close_help_fig():
    global _HELP_FIG
    if _HELP_FIG is None:
        return
    try:
        plt.close(_HELP_FIG)
    except Exception:                        # noqa: BLE001
        pass
    _HELP_FIG = None


def _loc_name(loc):
    '''legend._loc 可能是字符串也可能是整数码，统一成字符串。'''
    if isinstance(loc, int):
        return _LOC_CODES.get(loc, 'best')
    return loc


def _aspect_locked(ax):
    '''判断坐标轴是否锁长宽比（cartopy GeoAxes 返回 'equal'，数值 aspect 也是锁）。'''
    try:
        a = ax.get_aspect()
    except Exception:
        return False
    if isinstance(a, str):
        return a == 'equal'
    return isinstance(a, (int, float))


def _legend_props(leg):
    '''读出重建图例时必须保留的属性。

    为什么需要：拖图例时我们会用 `ax.legend(handles, labels, loc=...)` **重建** Legend，
    不带上原属性就会把用户脚本里的设置丢掉 —— 最典型的是 **多列图例被悄悄变成单列**
    （`ncols` 丢了）。这里把列数/间距/边框/标题等都捞出来，重建时原样带回去。
    版本差异用 getattr 多名字兜（ncols/ncol、_xxx/xxxpublic）。
    '''
    props = {}
    if leg is None:
        return props

    def _g(*names):
        for n in names:
            v = getattr(leg, n, None)
            if v is not None and not callable(v):
                return v
        return None

    ncols = _g('_ncols', 'ncols', '_ncol', 'ncol')
    if isinstance(ncols, int) and ncols > 1:
        props['ncols'] = ncols
    for k, names in (('markerfirst', ('_markerfirst', 'markerfirst')),
                     ('handlelength', ('_handlelength', 'handlelength')),
                     ('handleheight', ('_handleheight', 'handleheight')),
                     ('handletextpad', ('_handletextpad', 'handletextpad')),
                     ('columnspacing', ('_columnspacing', 'columnspacing')),
                     ('labelspacing', ('_labelspacing', 'labelspacing')),
                     ('borderpad', ('_borderpad', 'borderpad')),
                     ('borderaxespad', ('_borderaxespad', 'borderaxespad')),
                     ('markerscale', ('_markerscale', 'markerscale')),
                     ('numpoints', ('_numpoints', 'numpoints')),
                     ('scatterpoints', ('_scatterpoints', 'scatterpoints')),
                     ('framealpha', ('_framealpha', 'framealpha'))):
        v = _g(*names)
        if v is not None:
            props[k] = v
    try:
        ttl = leg.get_title().get_text() if leg.get_title() is not None else ''
        if ttl:
            props['title'] = ttl
    except Exception:
        pass
    return props


def _title_fs(ax):
    '''取该轴"有文字"的标题字号（中间/左/右三个标题位里最大的那个）。

    matplotlib 的 `set_title(loc='left')` 设的是 `_left_title`，不是 `ax.title`；
    真实论文脚本几乎都用 loc='left'，只读 ax.title 会得到默认字号（错的）。
    '''
    fs = None
    for t in (getattr(ax, '_left_title', None), ax.title,
              getattr(ax, '_right_title', None)):
        try:
            if t is not None and t.get_text():
                v = t.get_fontsize()
                fs = v if fs is None else max(fs, v)
        except Exception:
            pass
    if fs is None:
        try:
            fs = ax.title.get_fontsize()
        except Exception:
            fs = None
    return fs


def _is_colorbar_ax(ax):
    '''判断是否 colorbar 轴。**三种信号任一命中即算**：

    真实论文脚本里 colorbar 轴的 label 常常是空串（不是 '<colorbar>'），只看 label
    会漏判，后果有两个：①最小尺寸误用 MIN_SIZE(0.02) 而不是 MIN_SIZE_CB(0.008)，
    细条减不到更薄；②不去除 _ColorbarAxesLocator，则每次重绘它都会按父轴重算
    x0/宽度，用户拖好的位置被悄悄改回（表现为"宽度自己缩了"）。
    '''
    try:
        if ax.get_label() == '<colorbar>':
            return True
    except Exception:
        pass
    try:
        if getattr(ax, '_colorbar', None) is not None:
            return True
    except Exception:
        pass
    try:
        loc = ax.get_axes_locator()
        if loc is not None and type(loc).__name__ == '_ColorbarAxesLocator':
            return True
    except Exception:
        pass
    return False


def _cb_mappable(ax):
    """返回 colorbar 轴对应的 ``(Colorbar, mappable)``。"""
    cb = getattr(ax, '_colorbar', None)
    if cb is None:
        return None, None
    mappable = getattr(cb, 'mappable', None)
    return cb, mappable


def _cb_orientation(ax):
    cb, _ = _cb_mappable(ax)
    if cb is not None:
        return getattr(cb, 'orientation', None) or ('horizontal'
                if ax.get_position().width >= ax.get_position().height
                else 'vertical')
    return 'horizontal'


def _clim_of(ax):
    """读取 colorbar mappable 的 clim，失败返回 None。"""
    _, mappable = _cb_mappable(ax)
    if mappable is None or not hasattr(mappable, 'get_clim'):
        return None
    try:
        lo, hi = mappable.get_clim()
        if lo is None or hi is None:
            return None
        return float(lo), float(hi)
    except Exception:
        return None


def _set_cb_clim(ax, vmin, vmax):
    """设置 colorbar mappable 的 clim，并同步 Colorbar。"""
    _, mappable = _cb_mappable(ax)
    if mappable is None or not hasattr(mappable, 'set_clim'):
        return False
    try:
        mappable.set_clim(vmin, vmax)
        cb = getattr(ax, '_colorbar', None)
        if cb is not None:
            cb.update_normal(mappable)
        return True
    except Exception:
        return False


def _hit_cb_endpoint(fig, x, y):
    """命中 colorbar 长轴端点，返回 ``(ax, 'vmin'|'vmax')``。"""
    for ax in fig.axes:
        if not _is_colorbar_ax(ax):
            continue
        try:
            bb = ax.get_position().transformed(fig.transFigure)
            x0, y0, x1, y1 = bb.x0, bb.y0, bb.x1, bb.y1
            if _cb_orientation(ax) == 'vertical':
                if x0 - EDGE_TOL <= x <= x1 + EDGE_TOL:
                    if abs(y - y0) <= CLIM_TIP_TOL:
                        return ax, 'vmin'
                    if abs(y - y1) <= CLIM_TIP_TOL:
                        return ax, 'vmax'
            else:
                if y0 - EDGE_TOL <= y <= y1 + EDGE_TOL:
                    if abs(x - x0) <= CLIM_TIP_TOL:
                        return ax, 'vmin'
                    if abs(x - x1) <= CLIM_TIP_TOL:
                        return ax, 'vmax'
        except Exception:
            pass
    return None, None


def _is_line_artist(artist):
    '''白名单：可以安全调「线宽 / 单色」的线条类对象。

    为什么必须白名单：Quiver（箭头）、scatter 的 PathCollection、填色等值线
    都带 ``set_linewidth``/``set_color``，但对它们调这两个属性要么毫无效果、
    要么直接把图弄坏 —— 实测在真实论文图上把 Collection 的 ``N×4`` 颜色数组
    喂给 ``to_hex`` 会抛：
        ``RGBA sequence should have length 3 or 4``
    只放行三类：plot 线（Line2D）、LineCollection、**未填充**的等值线（ContourSet）。
    '''
    if isinstance(artist, Line2D):
        return True
    try:
        if isinstance(artist, matplotlib.collections.LineCollection):
            return True
    except Exception:
        pass
    if hasattr(artist, 'levels'):          # ContourSet（contour / contourf）
        return not getattr(artist, 'filled', False)
    return False


def _single_color(v):
    '''把任意颜色表示（字符串 / 元组 / 嵌套序列 / numpy 数组）剥成单个颜色。

    Collection 的颜色常常是 ``(N,3)``/``(N,4)`` 或 ``[color, color, ...]``；
    逐层剥到第一个"像颜色"的标量层，再交给 ``to_rgba`` 校验，失败返回 None。
    '''
    if v is None:
        return None
    try:
        depth = 0
        while (hasattr(v, '__len__') and not isinstance(v, str)
               and len(v) and hasattr(v[0], '__len__') and depth < 3):
            v = v[0]
            depth += 1
        return matplotlib.colors.to_rgba(v)
    except Exception:
        return None


def _line_at(fig, x, y):
    """命中可调线条，返回 ``(artist, ax)``；优先 Line2D。"""
    try:
        event = MouseEvent('motion_notify_event', fig.canvas, x, y)
    except Exception:
        return None, None
    # 先查 plot 线，再查 LineCollection / 未填充等值线（都走白名单）。
    for ax in reversed(fig.axes):
        for artist in reversed(list(getattr(ax, 'lines', []))):
            try:
                if artist.contains(event)[0]:
                    return artist, ax
            except Exception:
                pass
        for artist in reversed(list(getattr(ax, 'collections', []))):
            if not _is_line_artist(artist):
                continue
            if not (hasattr(artist, 'set_linewidth') and
                    hasattr(artist, 'get_linewidth')):
                continue
            try:
                if artist.contains(event)[0]:
                    return artist, ax
            except Exception:
                pass
    return None, None


def _bbox(ax):
    '''取坐标框 [x0, y0, w, h]（figure 比例坐标）。'''
    p = ax._position
    return [p.x0, p.y0, p.width, p.height]


def _set_bbox(ax, box):
    ax.set_position((box[0], box[1], box[2], box[3]))


def _nearest(v, cands, tol):
    '''在容差内找离 v 最近的候选值，没有返回 None。'''
    best = None
    bd = tol
    for c in cands:
        d = abs(c - v)
        if d <= bd:
            bd = d
            best = c
    return best


def _best_snap(mine, cands, tol):
    '''在候选线里找最近的一对（我的边/中心 → 候选线）。

    返回 (mine, cand) 或 None。**中心也参与吸附**：面板的中心线对齐到
    另一个面板的中心线/边线，是排版里最常用的一种对齐（axisblueprint 的做法）。
    '''
    best = None
    for m in mine:
        for c in cands:
            d = abs(m - c)
            if d <= tol and (best is None or d < best[2]):
                best = (m, c, d)
    return None if best is None else (best[0], best[1])


def _resize_anchored(box0, anchor, w, h, min_size=MIN_SIZE):
    '''**锚点化缩放**：锚点 (ax, ay) ∈ [0,1]² 那个点在缩放前后**不动**。

        x_ll = x_ll0 + ax * (w0 - w)
        y_ll = y_ll0 + ay * (h0 - h)

    锚点含义：(0,0)=左下角固定、(1,1)=右上角固定、(0.5,0.5)=中心固定、
    (0.5,0)=下边中点固定…… 拖拽手柄、方向键、锁长宽比等比缩放全都归到这一条公式，
    不再各写一套（这一块此前因为写法分散出过两个 bug）。
    '''
    x0, y0, w0, h0 = box0
    w = max(min_size, w)
    h = max(min_size, h)
    return [x0 + anchor[0] * (w0 - w), y0 + anchor[1] * (h0 - h), w, h]


def _snap_box(box, others, tol=SNAP_TOL):
    '''移动吸附：把 box 的 **左/中/右** 与 **下/中/上** 吸附到其它轴/画布的
    边线与中心线（容差内），整体平移（尺寸不变）。
    返回 (新box, 吸附到的x线列表, 吸附到的y线列表)（供画对齐参考线）。'''
    x0, y0, w, h = box
    cand_x, cand_y = [0.0, 0.5, 1.0], [0.0, 0.5, 1.0]   # 画布边界 + 画布中心
    for ob in others:
        ox0, oy0, ow, oh = ob
        cand_x += [ox0, ox0 + ow / 2.0, ox0 + ow]       # 左 / 中 / 右
        cand_y += [oy0, oy0 + oh / 2.0, oy0 + oh]       # 下 / 中 / 上
    bx = _best_snap([x0, x0 + w / 2.0, x0 + w], cand_x, tol)
    by = _best_snap([y0, y0 + h / 2.0, y0 + h], cand_y, tol)
    dx = 0.0 if bx is None else bx[1] - bx[0]
    dy = 0.0 if by is None else by[1] - by[0]
    gx = [] if bx is None else [bx[1]]
    gy = [] if by is None else [by[1]]
    return [x0 + dx, y0 + dy, w, h], gx, gy


def _hit_handle(fig, x, y):
    '''PPT 式命中检测：返回 (ax, handle) 或 (None, None)。
    handle ∈ left/right/top/bottom/tl/tr/bl/br（角优先于边）。'''
    axc = namec = None
    dc = CORNER_TOL
    axe = namee = None
    de = EDGE_TOL
    for ax in fig.axes:
        bb = ax.get_position().transformed(fig.transFigure)
        x0, y0, x1, y1 = bb.x0, bb.y0, bb.x1, bb.y1
        for name, cx, cy in (('tl', x0, y1), ('tr', x1, y1),
                             ('bl', x0, y0), ('br', x1, y0)):
            d = math.hypot(x - cx, y - cy)
            if d < dc:
                axc, namec, dc = ax, name, d
        for name, e, a0, a1 in (('left', x0, y0, y1), ('right', x1, y0, y1),
                                ('bottom', y0, x0, x1), ('top', y1, x0, x1)):
            if name in ('left', 'right'):
                d = abs(x - e)
                if d < de and a0 - EDGE_TOL <= y <= a1 + EDGE_TOL:
                    axe, namee, de = ax, name, d
            else:
                d = abs(y - e)
                if d < de and a0 - EDGE_TOL <= x <= a1 + EDGE_TOL:
                    axe, namee, de = ax, name, d
    if axc is not None:
        return axc, namec
    if axe is not None:
        return axe, namee
    # 细条（colorbar 等）兜底：**按维度分别判断**——
    #   只有"真的窄"的那一维，两条边才贴得近、才需要把抓取区按比例放大；
    #   细长水平条的左右两端其实离得很远，绝不该扩左右。
    # （旧实现用 `w < 5*EDGE_TOL or h < 5*EDGE_TOL` 一起判断，导致 14px 高的水平
    #   colorbar 的左右抓取区被扩到 1/3 宽 → 想调高度总是改到宽度。）
    for ax in fig.axes:
        bb = ax.get_position().transformed(fig.transFigure)
        x0, y0, x1, y1 = bb.x0, bb.y0, bb.x1, bb.y1
        w, h = x1 - x0, y1 - y0
        narrow_w = w < 5 * EDGE_TOL
        narrow_h = h < 5 * EDGE_TOL
        if not narrow_w and not narrow_h:
            continue
        # 抓取区：至少 EDGE_TOL，最多到该维的 35%（给"移动"留出中间带）
        wt = (min(max(EDGE_TOL, w / 3.0), w * 0.35) if narrow_w else EDGE_TOL)
        ht = (min(max(EDGE_TOL, h / 3.0), h * 0.35) if narrow_h else EDGE_TOL)
        if abs(x - x0) <= wt and y0 - EDGE_TOL <= y <= y1 + EDGE_TOL:
            return ax, 'left'
        if abs(x - x1) <= wt and y0 - EDGE_TOL <= y <= y1 + EDGE_TOL:
            return ax, 'right'
        if abs(y - y0) <= ht and x0 - EDGE_TOL <= x <= x1 + EDGE_TOL:
            return ax, 'bottom'
        if abs(y - y1) <= ht and x0 - EDGE_TOL <= x <= x1 + EDGE_TOL:
            return ax, 'top'
    return None, None


def _resize_frac(b1, handle, mx, my, locked, min_size=MIN_SIZE, center=False):
    '''PPT 式拖拽缩放：b1=[x0,y0,x1,y1]（figure 比例），mx,my=鼠标（figure 比例）。
    未锁比：直接移动被拖的边/角，对边固定，受 min_size 约束；
    锁比（cartopy）：绕锚点等比缩放（角=对角顶点，边=对边中点）。
    center=True（Alt）：以框中心为锚的对称缩放（宽高独立，不锁比）。'''
    x0, y0, x1, y1 = b1
    if locked:
        anchors = {
            'right': (x0, (y0 + y1) / 2), 'left': (x1, (y0 + y1) / 2),
            'top': ((x0 + x1) / 2, y0), 'bottom': ((x0 + x1) / 2, y1),
            'tr': (x0, y0), 'tl': (x1, y0), 'br': (x0, y1), 'bl': (x1, y1)}
        ax0, ay0 = anchors[handle]
        sx = (mx - ax0) / (x1 - x0) if (x1 - x0) else 1.0
        sy = (my - ay0) / (y1 - y0) if (y1 - y0) else 1.0
        s = max(abs(sx), abs(sy))
        s = max(s, min_size / max(x1 - x0, 1e-9),
                min_size / max(y1 - y0, 1e-9))
        return [ax0 + s * (x0 - ax0), ay0 + s * (y0 - ay0),
                ax0 + s * (x1 - ax0), ay0 + s * (y1 - ay0)]
    if center:
        cx = (x0 + x1) / 2
        cy = (y0 + y1) / 2
        nx0, ny0, nx1, ny1 = x0, y0, x1, y1
        if handle in ('left', 'tl', 'bl'):
            nx0 = mx
        elif handle in ('right', 'tr', 'br'):
            nx1 = mx
        if handle in ('top', 'tl', 'tr'):
            ny1 = my
        elif handle in ('bottom', 'bl', 'br'):
            ny0 = my
        if handle in ('left', 'tl', 'bl'):
            nx1 = 2 * cx - nx0
        elif handle in ('right', 'tr', 'br'):
            nx0 = 2 * cx - nx1
        if handle in ('top', 'tl', 'tr'):
            ny0 = 2 * cy - ny1
        elif handle in ('bottom', 'bl', 'br'):
            ny1 = 2 * cy - ny0
        if nx1 - nx0 < min_size:
            d = (min_size - (nx1 - nx0)) / 2
            nx0 -= d
            nx1 += d
        if ny1 - ny0 < min_size:
            d = (min_size - (ny1 - ny0)) / 2
            ny0 -= d
            ny1 += d
        return [nx0, ny0, nx1, ny1]
    if handle == 'right':
        x1 = mx
    elif handle == 'left':
        x0 = mx
    elif handle == 'top':
        y1 = my
    elif handle == 'bottom':
        y0 = my
    elif handle == 'tr':
        x1, y1 = mx, my
    elif handle == 'tl':
        x0, y1 = mx, my
    elif handle == 'br':
        x1, y0 = mx, my
    elif handle == 'bl':
        x0, y0 = mx, my
    if x1 - x0 < min_size:
        if handle in ('left', 'tl', 'bl'):
            x0 = x1 - min_size
        else:
            x1 = x0 + min_size
    if y1 - y0 < min_size:
        if handle in ('bottom', 'bl', 'br'):
            y0 = y1 - min_size
        else:
            y1 = y0 + min_size
    return [x0, y0, x1, y1]


def _snap_resize(b1, handle, others, tol=SNAP_TOL):
    '''缩放时只吸附被拖动的边/角（对边固定），返回 (新b1, gx, gy) 供画参考线。'''
    x0, y0, x1, y1 = b1
    # 显式列出每种手柄真正在动的边。**不能用子串判断**：
    # 'left' 含 't'、'right' 含 't'、'bottom' 含 't' → 会把不该动的 y1 也拿去吸附，
    # 结果"拖一条边，另一维也变了"。
    moving = set()
    if handle in ('left', 'tl', 'bl'):
        moving.add('x0')
    if handle in ('right', 'tr', 'br'):
        moving.add('x1')
    if handle in ('bottom', 'bl', 'br'):
        moving.add('y0')
    if handle in ('top', 'tl', 'tr'):
        moving.add('y1')
    gx, gy = [], []
    for ob in others:
        ox0, oy0, ox1, oy1 = ob
        # 候选线含**中线**：被拖的边可以对齐到别的面板的中心线
        cx_ = [ox0, (ox0 + ox1) / 2.0, ox1]
        cy_ = [oy0, (oy0 + oy1) / 2.0, oy1]
        if 'x0' in moving:
            v = _nearest(x0, cx_, tol)
            if v is not None:
                x0 = v
                gx.append(v)
        if 'x1' in moving:
            v = _nearest(x1, cx_, tol)
            if v is not None:
                x1 = v
                gx.append(v)
        if 'y0' in moving:
            v = _nearest(y0, cy_, tol)
            if v is not None:
                y0 = v
                gy.append(v)
        if 'y1' in moving:
            v = _nearest(y1, cy_, tol)
            if v is not None:
                y1 = v
                gy.append(v)
    # 画布边界（0/1）吸附：只作用于被拖动的边
    for cand in (0.0, 1.0):
        if 'x0' in moving and abs(x0 - cand) <= tol:
            x0 = cand
            gx.append(cand)
        if 'x1' in moving and abs(x1 - cand) <= tol:
            x1 = cand
            gx.append(cand)
        if 'y0' in moving and abs(y0 - cand) <= tol:
            y0 = cand
            gy.append(cand)
        if 'y1' in moving and abs(y1 - cand) <= tol:
            y1 = cand
            gy.append(cand)
    return [x0, y0, x1, y1], sorted(set(gx)), sorted(set(gy))


# 手柄 -> 缩放光标（仅边；角走 Qt 原生对角线光标，见 _on_motion hover 分支）
_CURSORS = {
    'left': Cursors.RESIZE_HORIZONTAL, 'right': Cursors.RESIZE_HORIZONTAL,
    'top': Cursors.RESIZE_VERTICAL, 'bottom': Cursors.RESIZE_VERTICAL,
}


def _resize_box(box, key, locked):
    '''按键 -> 新坐标框（v2 同款：锁比=对角锚定等比缩放，未锁=原版逐边语义）。'''
    x0, y0, w, h = box
    x1, y1 = x0 + w, y0 + h
    if locked:
        if key == 'a':
            nw = max(MIN_SIZE, w + STEP); s = nw / w
            return [x1 - nw, y0, nw, h * s]
        elif key == 'd':
            nw = max(MIN_SIZE, w - STEP); s = nw / w
            return [x1 - nw, y0, nw, h * s]
        elif key == 'D':
            nw = max(MIN_SIZE, w + STEP); s = nw / w
            return [x0, y0, nw, h * s]
        elif key == 'A':
            nw = max(MIN_SIZE, w - STEP); s = nw / w
            return [x0, y0, nw, h * s]
        elif key == 's':
            nh = max(MIN_SIZE, h + STEP); s = nh / h
            return [x0, y1 - nh, w * s, nh]
        elif key == 'w':
            nh = max(MIN_SIZE, h - STEP); s = nh / h
            return [x0, y1 - nh, w * s, nh]
        elif key == 'W':
            nh = max(MIN_SIZE, h + STEP); s = nh / h
            return [x0, y0, w * s, nh]
        elif key == 'S':
            nh = max(MIN_SIZE, h - STEP); s = nh / h
            return [x0, y0, w * s, nh]
        elif key == 'left':
            return [x0 - STEP, y0, w, h]
        elif key == 'right':
            return [x0 + STEP, y0, w, h]
        elif key == 'up':
            return [x0, y0 + STEP, w, h]
        elif key == 'down':
            return [x0, y0 - STEP, w, h]
    else:
        if key == 'a':
            return [x0 - STEP, y0, w + STEP, h]
        elif key == 'd':
            return [x0 + STEP, y0, w - STEP, h]
        elif key == 'A':
            return [x0, y0, w - STEP, h]
        elif key == 'D':
            return [x0, y0, w + STEP, h]
        elif key == 'w':
            return [x0, y0 + STEP, w, h - STEP]
        elif key == 's':
            return [x0, y0 - STEP, w, h + STEP]
        elif key == 'W':
            return [x0, y0, w, h + STEP]
        elif key == 'S':
            return [x0, y0, w, h - STEP]
        elif key == 'left':
            return [x0 - STEP, y0, w, h]
        elif key == 'right':
            return [x0 + STEP, y0, w, h]
        elif key == 'up':
            return [x0, y0 + STEP, w, h]
        elif key == 'down':
            return [x0, y0 - STEP, w, h]
    return box


class Tweak:
    '''交互改图控制器：一个 fig 挂一个。'''

    def __init__(self, fig, export_path=None, heavy=None,
                 fig_index=None, n_figs=None, edit_clock=None):
        self.fig = fig
        if export_path is None:
            base = os.path.splitext(os.path.basename(sys.argv[0]))[0] if sys.argv else 'figure'
            export_path = os.path.join(os.getcwd(), base + '.tweak.json')
        self.export_path = export_path
        # 调的是第几张图（0-based，顺序同 plt.get_fignums()）/ 脚本共几张图。
        # 多图脚本写回时必须靠它定位，否则只能猜"最后一张"。
        self.fig_index = fig_index
        self.n_figs = n_figs
        # 改动计数：多图会话里所有图共享同一个 clock（launch 传入），
        # 用来判断"哪张被改过 / 最后改的是哪张"→ 不用预先 --fig 指定。
        self._edit_clock = edit_clock if edit_clock is not None else [0]
        self.edit_seq = 0
        # 会话接管锁（见 export() 防覆盖逻辑）
        self._lock_path = None
        try:
            lock = export_path + '.lock'
            with open(lock, 'w', encoding='utf-8') as f:
                f.write(str(os.getpid()))
            self._lock_path = lock
        except OSError:
            pass
        self.locked_map = {id(ax): _aspect_locked(ax) for ax in fig.axes}
        # colorbar 轴解除 box_aspect：make_axes 会给它设 _box_aspect=20，
        # 导致每次重绘 apply_aspect 都强制宽度回自动值，set_position 不生效。
        # 清掉后拖拽/缩放/烘焙的颜色条位置才能真正钉住。
        # colorbar 轴两处接管（缺一不可）：
        #   ① set_box_aspect(None)：make_axes 设的 _box_aspect=20 会让 apply_aspect
        #      每次重绘把宽度拉回自动值；
        #   ② set_axes_locator(None)：_ColorbarAxesLocator 每次重绘按父轴重算
        #      x0/宽度 —— 这是"改高度正常、宽度却自己缩"的真正元凶。
        for ax in fig.axes:
            try:
                if _is_colorbar_ax(ax):
                    ax.set_box_aspect(None)
                    ax.set_axes_locator(None)
                    # 竖直 colorbar 的 x 轴（底部 0-1 内部刻度）与水平 colorbar 的
                    # y 轴（左侧）本应隐藏；解除 locator 后 matplotlib 会重新显示
                    # 这些归一化刻度（用户实测"colorbar 下方冒出 0 和 1"）。
                    # 接管时强制关掉非数据轴刻度。
                    try:
                        if _cb_orientation(ax) == 'vertical':
                            ax.xaxis.set_ticks([])
                            ax.tick_params(axis='x', bottom=False,
                                           labelbottom=False, top=False, labeltop=False)
                        else:
                            ax.yaxis.set_ticks([])
                            ax.tick_params(axis='y', left=False,
                                           labelleft=False, right=False, labelright=False)
                    except Exception:
                        pass
            except Exception:
                pass
        # 清掉与工具箱键位冲突的 matplotlib 默认快捷键：s(保存框)/c(后退)/
        # f/ctrl+f(全屏)/g(网格)/left/right(前后页) 会让用户按工具箱键时弹出默认
        # 行为（实测：中文输入法下字母键常被吞，英文输入法下 s 会弹保存框、
        # f 会切全屏）。只清不新增，工具箱自己的 _on_key 处理仍生效。
        try:
            for _kp in ('fullscreen', 'save', 'back', 'forward', 'grid',
                        'grid_minor', 'xscale', 'yscale'):
                _lst = matplotlib.rcParams.get('keymap.' + _kp)
                if isinstance(_lst, list):
                    _lst[:] = [k for k in _lst
                               if k not in ('f', 's', 'c', 'g', 'left',
                                            'right', 'v', 'k', 'L', 'l',
                                            'ctrl+f', 'ctrl+s')]
        except Exception:
            pass
        # 图例状态：{id(ax): {'loc': str|None, 'anchor': [x,y]|None, 'handles':.., 'labels':..}}
        self.leg_state = {}
        self._init_legends()
        # 只有一种模式：固定重图（ghost+blit 预览）。全量重绘耗时仅作诊断，
        # 显示在窗口标题栏（让用户知道"这张图一次重绘要多久"）。
        self._heavy_ms = None
        self._is_heavy()
        self.heavy = True if heavy is None else bool(heavy)
        self.snap = True                     # 吸附开关
        self._blit_ok = True                 # blit 是否可用；失败自动降级实时模式
        self._last_draw = 0.0                # 节流：上次 draw_idle 时刻
        # ? 键位浮层：独立键位表窗口（模块级单例 _show_help_fig，多图共享）
        self._help_fig = None
        # 状态栏 + 操作 toast（fig.text 纯 matplotlib，Qt/Tk 都可用）
        self._status = None
        self._toast_art = None
        self._toast_timer = None
        try:
            self._status = fig.text(0.012, 0.004, '', fontsize=8, color='0.35',
                                    ha='left', va='bottom',
                                    transform=fig.transFigure, zorder=6000)
            self._toast_art = fig.text(0.5, 0.012, '', fontsize=9, color='0.18',
                                       ha='center', va='bottom',
                                       transform=fig.transFigure, zorder=6000)
            self._toast_art.set_visible(False)
        except Exception:                    # noqa: BLE001 - 无头/特殊后端兜底
            self._status = self._toast_art = None
        # 状态
        self._ax = None
        self._target = None
        self._drag = False
        self._drag_cb_clim = False                # 兼容旧属性引用（clim 拖拽已移除）
        self._clim_ax = None
        self._clim_side = None
        self._clim0 = None
        self._clim_box_px = None
        self._clim_orientation = None
        self._cb_hint_on = False               # 是否正显示 clim 端点悬停提示
        self._drag_leg = False
        self._leg = None
        self._press_xy = (0, 0)
        self._origin = None
        self._ghost = None
        self._guides = []
        self._bg = None
        self._timer = None
        self._bg_timer = None                # 延迟补抓背景用的单次定时器
        self._wire = False                   # 线条模式开关
        self._hits = []                      # HitMap：重绘后缓存的文本命中矩形表
        self._nudge_seal = None              # 方向键微调的 undo 合并标记
        self._nudge_seal_t = 0.0
        self._wire_saved = []                # 线条模式下被隐藏图元的原可见性
        self._atp_saved = []                 # 线条模式下被关掉的 _autotitlepos 原值
        self._gl_saved = []                  # 线条模式下被摘掉的 cartopy Gridliner
        # PPT 式边框/角点缩放状态
        self._drag_resize = False
        self._rs_ax = None
        self._rs_handle = None
        self._rs_origin = None
        self._rs_target = None
        self._cur = None
        self._fig_px = None               # 当前画布像素尺寸（窗口拖多大记多大）
        # 多选 + 橡皮筋框选（PPT 式）
        self._selected = set()            # 选中的轴 id 集合
        self._sel_artists = []            # 选中蓝色外框 artists
        self._band = None                 # 橡皮筋 Rectangle（fig 比例）
        self._band_origin = None          # 框选起点（px）
        self._band_active = False
        self._multi_drag = False          # 多选拖拽中
        self._multi_origin = {}           # 多选拖拽时各轴起始 bbox
        self._drag_ax = None              # 多选拖拽中被抓住的面板
        self._drag_ax_origin = None       # 被抓住面板的起始 bbox
        # Undo/Redo（快照：位置+字号+图例）
        self._undo = []
        self._redo = []
        # 尺寸 HUD：走窗口标题栏（不占画布、不碰 blit）
        self._title_base = None
        # 图例拖拽时的 8 位候选预览
        self._leg_preview = []
        self._leg_preview_map = {}
        # 禁用全部 matplotlib 内置键位（键位归本工具所有）
        for k in list(plt.rcParams):
            if k.startswith('keymap.'):
                plt.rcParams[k] = ''
        self._connect()
        # 窗口标题 + 置顶（用户不需要强抢焦点，只保证弹在最前）
        # 模式标签进窗口标题：全量重绘耗时 + blit 是否降级，一眼可见
        # 标题不再带"重图[Nms]"模式标签（用户要求；重绘耗时见控制台 [mpltweak] 输出）
        self._title_tmpl = 'mpltweak — 拖动调布局 · 按 ? 看全部键位'
        base = self._title_tmpl
        # 多图会话：标题带上图号，一眼能看出总共几个窗口、还剩哪个没关
        if (isinstance(self.fig_index, int) and isinstance(self.n_figs, int)
                and self.n_figs > 1):
            base = '[图 %d/%d] %s' % (self.fig_index + 1, self.n_figs, base)
        self._title_base = base
        try:
            m = fig.canvas.manager
            if m is not None:
                m.set_window_title(base)
                w = getattr(m, 'window', None)
                if w is not None:
                    try:
                        w.show()
                        w.raise_()
                    except Exception:
                        pass
        except Exception:
            pass
        # 背景预抓（关键：解决"第一下拖拽卡半秒"）——窗口已显示，先复用
        # _is_heavy() 那次 draw 直接抓一次（不额外绘制），再延迟补抓一次，
        # 防止 w.show() 引发的 resize 把刚抓的背景作废。
        self._capture_bg(False)
        self._arm_bg_capture()
        _info('[mpltweak] 模式=重图（ghost+blit）全量重绘=%s 吸附=%s 导出=%s' % (
            ('%.0fms' % self._heavy_ms) if self._heavy_ms else '?',
            self.snap, self.export_path), flush=True)
        _info('[mpltweak] 拖拽/框选/多选都走 blit 预览（松手或停顿才真正落位）；'
              '若下面出现 blit 降级提示，说明该后端不支持 blit，会明显卡', flush=True)
        _log('[mpltweak] 键位: 拖面板=移动 拖边/角=缩放 | '
              '悬停标题/x轴/y轴/刻度/图例/colorbar 按 +/- 调字号 | '
              'Ctrl+Z撤销 | 悬停文字±字号 | l图例不再支持键位切换（拖图例=8位吸附） | '
              '方向键=移动面板 | Shift+方向键=微调尺寸(中心不动) | '
              '关窗自动导出 | 拖窗口=画布尺寸 | 模式固定=重图',
              flush=True)

    # ---------- 初始化 ----------
    def _init_legends(self):
        '''记录每个有图例的轴的 handles/labels 与当前 loc，供循环/拖拽/导出。'''
        for ax in self.fig.axes:
            leg = ax.get_legend()
            if leg is None:
                continue
            handles, labels = ax.get_legend_handles_labels()
            self.leg_state[id(ax)] = {
                'handles': handles, 'labels': labels,
                'loc': _loc_name(getattr(leg, '_loc', 'best')),
                'anchor': None,
                'props': _legend_props(leg),      # 重建时要保留的原属性（列数等）
            }

    def _is_heavy(self):
        try:
            t0 = time.perf_counter()
            self.fig.canvas.draw()
            self._heavy_ms = (time.perf_counter() - t0) * 1000
            return self._heavy_ms > HEAVY_MS
        except Exception:
            self._heavy_ms = None
            return False

    def _capture_bg(self, draw=True):
        '''抓一张「干净背景」供 blit 用。

        为什么必须在开窗前/开窗后主动抓：第一次拖拽若发现 `_bg is None`，就要先做
        一次全量重绘（这张真实图 810ms）才画得出 ghost —— 用户感受就是
        "每次第一下拖拽都卡半秒"。拖拽/缩放/框选期间不抓（否则会把覆盖层抓进背景）。
        '''
        if (self._drag or self._drag_leg or self._drag_resize
                or self._multi_drag or self._band_active):
            return
        try:
            if draw:
                self.fig.canvas.draw()
            self._bg = self.fig.canvas.copy_from_bbox(self.fig.bbox)
            self._build_hits()      # 刚重绘完，文字几何最新 → 顺手重建命中表
        except Exception:
            self._bg = None

    def _arm_bg_capture(self, delay=300):
        '''延迟补抓背景：窗口刚显示 / 刚被拖过尺寸时布局还没稳定，
        等一小会儿再抓一次，这样用户第一下拖拽不必再付全量重绘的代价。'''
        try:
            if self._bg_timer is None:
                self._bg_timer = self.fig.canvas.new_timer(interval=delay)
                self._bg_timer.add_callback(lambda: self._capture_bg(True))
                try:
                    self._bg_timer.single_shot = True
                except Exception:
                    pass
            self._bg_timer.stop()
            self._bg_timer.start()
        except Exception:
            self._capture_bg(True)           # 后端无定时器：立即抓

    # ---------- 事件接线 ----------
    def _connect(self):
        c = self.fig.canvas
        c.mpl_connect('button_press_event', self._on_press)
        c.mpl_connect('button_release_event', self._on_release)
        c.mpl_connect('motion_notify_event', self._on_motion)
        c.mpl_connect('key_press_event', self._on_key)
        c.mpl_connect('close_event', self._on_close)
        c.mpl_connect('resize_event', self._on_resize)

    def _on_resize(self, event):
        '''记录画布像素尺寸（窗口被拖多大就记多大，导出时写回 figsize）。'''
        try:
            W, H = self.fig.canvas.get_width_height()
            self._fig_px = (int(W), int(H))
        except Exception:
            pass
        self._bg = None            # 画布尺寸变了，缓存背景必须失效重抓
        self._arm_bg_capture()     # 但别等下次拖拽才抓（那会卡一次全量重绘）

    def _on_close(self, event):
        '''关窗口自动导出 JSON（无感协作的关键一步）。'''
        try:
            self.export()
        except Exception as e:
            _log('[mpltweak] 关窗自动导出失败:', e)

    # ---------- ghost + blit ----------
    def _ensure_bg(self):
        '''抓「干净背景」：覆盖层（ghost/参考线/橡皮筋/蓝框/图例预览）先隐藏再抓，
        否则移动它们会在 blit 帧里留下残影。'''
        if self._bg is not None:
            return
        overlays = []
        cand = []
        if self._ghost is not None:
            cand.append(self._ghost)
        cand += list(self._guides)
        if self._band is not None:
            cand.append(self._band)
        cand += list(self._sel_artists)
        cand += list(self._leg_preview)
        for art in cand:
            try:
                overlays.append((art, art.get_visible()))
                art.set_visible(False)
            except Exception:
                pass
        try:
            self.fig.canvas.draw()
            self._bg = self.fig.canvas.copy_from_bbox(self.fig.bbox)
        finally:
            for art, vis in overlays:
                try:
                    art.set_visible(vis)
                except Exception:
                    pass

    def _overlay_artists(self):
        '''当前需要进 blit 帧的所有覆盖层。'''
        out = []
        if self._ghost is not None:
            out.append(self._ghost)
        out += list(self._guides)
        if self._band is not None:
            out.append(self._band)
        out += list(self._sel_artists)
        out += list(self._leg_preview)
        return out

    def _blit_frame(self):
        '''统一 blit 帧：恢复干净背景 → 画全部覆盖层 → blit。

        重图下这一步只要几十毫秒（对比全量重绘 ~800ms），所以多选拖拽 /
        框选 / 点选蓝框全部走它，避免"每次操作卡一秒"。
        '''
        try:
            if self._bg is None:
                self._ensure_bg()
            self.fig.canvas.restore_region(self._bg)
            for art in self._overlay_artists():
                try:
                    if art.get_visible():
                        self.fig.draw_artist(art)
                except Exception:
                    pass
            self.fig.canvas.blit(self.fig.bbox)
            return True
        except Exception as e:
            self._blit_ok = False
            self._remove_ghost()
            self.fig.canvas.draw_idle()
            if not getattr(self, '_warned_blit', False):
                _info('[mpltweak] blit 不可用，已降级节流实时重绘（重图会明显卡）:',
                      e, flush=True)
                self._warned_blit = True
                # 标题栏标红提示：降级后拖拽会卡
                try:
                    self._toast('blit 不可用：已降级为节流重绘（重图下会卡）')
                except Exception:
                    pass
                self._set_title_hint(None)
            return False

    def _make_ghost(self, box):
        # 半透明填充 + 粗红虚线，重图拖拽时反馈醒目
        self._ghost = Rectangle((box[0], box[1]), box[2], box[3],
                                fill=True, facecolor='#d62728', alpha=0.12,
                                edgecolor='#d62728', linestyle='--',
                                linewidth=2.5, transform=self.fig.transFigure)
        self.fig.add_artist(self._ghost)

    def _draw_guides(self, gx, gy):
        '''画对齐参考线（figure 比例坐标的全幅竖/横线）。'''
        for x in gx:
            ln = Line2D([x, x], [0, 1], transform=self.fig.transFigure,
                        color='#1f77b4', alpha=0.7, lw=0.8, ls=':')
            self.fig.add_artist(ln)
            self._guides.append(ln)
        for y in gy:
            ln = Line2D([0, 1], [y, y], transform=self.fig.transFigure,
                        color='#1f77b4', alpha=0.7, lw=0.8, ls=':')
            self.fig.add_artist(ln)
            self._guides.append(ln)

    def _clear_guides(self):
        for ln in self._guides:
            ln.remove()
        self._guides = []

    def _blit_ghost(self, box, gx=(), gy=()):
        '''设置 ghost 位置 → 参考线 → 统一 blit 帧（重图不卡）。
        任何 blit 环节失败则回退「节流实时重绘」，保证操作一定有效果。'''
        if self._ghost is None:
            self._make_ghost(box)
        else:
            self._ghost.set_xy((box[0], box[1]))
            self._ghost.set_width(box[2])
            self._ghost.set_height(box[3])
        try:
            self._ghost.set_visible(True)
        except Exception:
            pass
        self._clear_guides()
        self._draw_guides(gx, gy)
        self._blit_frame()

    def _throttled_draw(self):
        '''节流重绘（blit 降级后拖拽用，~30fps 上限）。'''
        now = time.perf_counter()
        if now - self._last_draw > 0.033:
            self._last_draw = now
            self.fig.canvas.draw_idle()

    def _remove_ghost(self):
        for ln in self._guides:
            ln.remove()
        self._guides = []
        if self._ghost is not None:
            if self._bg is not None:
                self.fig.canvas.restore_region(self._bg)
            self._ghost.remove()
            self._ghost = None
        self._bg = None

    def _commit(self):
        '''防抖后落位：坐标框应用 + ghost/参考线清理 + 全量重绘。'''
        if self._drag:
            return
        if self._ax is not None and self._target is not None:
            _set_bbox(self._ax, self._target)
            self._remove_ghost()
        self._ax = None
        self._target = None
        self._bg = None            # 全量重绘后缓存背景失效，下次 blit 重新抓
        self._hits = []            # 文字几何也可能变了，命中表一并失效
        self.fig.canvas.draw_idle()

    def _schedule_commit(self):
        '''停键 COMMIT_MS 后落位（连续操作期间只 blit，不触发全量重绘）。'''
        if not self._timer_ok():
            self._commit()
            return
        self._timer.stop()
        self._timer.start()

    def _schedule_draw(self):
        '''只重绘（字号/方向键等非拖拽改动），同样防抖。'''
        if not self._timer_ok():
            self.fig.canvas.draw_idle()
            return
        self._timer.stop()
        self._timer.start()

    def _timer_ok(self):
        '''后端是否支持定时器（Agg 等无 GUI 后端不支持，需退回直接重绘）。'''
        if getattr(self, '_timer_bad', False):
            return False
        try:
            if self._timer is None:
                self._timer = self.fig.canvas.new_timer(interval=COMMIT_MS)
                self._timer.add_callback(self._commit)
            return True
        except Exception:
            self._timer_bad = True
            return False

    # ---------- 图例 ----------
    def _legend_at(self, x, y):
        '''返回 (ax, leg) 若鼠标点在某个图例框内。'''
        for ax in self.fig.axes:
            leg = ax.get_legend()
            if leg is not None:
                try:
                    if leg.get_window_extent().contains(x, y):
                        return ax, leg
                except Exception:
                    pass
        return None, None

    def _mk_legend(self, st, ax, **kw):
        '''重建图例：**带上原图例属性**（列数/间距/边框/标题…），并按版本逐项兜底。

        不带上原属性就会出现"拖完图例，多列变单列"这种静默破坏。
        版本差异（如 ncols 在旧版叫 ncol）用 TypeError 信息逐项剔除重试。
        '''
        full = dict(st.get('props') or {})
        full.update(kw)
        for _ in range(12):
            try:
                return ax.legend(st['handles'], st['labels'], **full)
            except TypeError as e:
                drop = None
                for k in list(full):
                    if k in str(e):
                        drop = k
                        break
                if drop is None:
                    raise
                full.pop(drop)
        return None

    def _cycle_legend(self, ax):
        '''图例循环 6 个标准位置（重建 legend，保留字号与框线）。'''
        st = self.leg_state.get(id(ax))
        if st is None or not st['handles'] and not st['labels']:
            _log('[mpltweak] 该轴没有可循环的图例')
            return
        leg = ax.get_legend()
        fs = leg.get_texts()[0].get_fontsize() if leg is not None and leg.get_texts() else None
        frameon = leg.get_frame_on() if leg is not None else True
        cur = st['loc'] if st['loc'] is not None else 'anchored'
        try:
            idx = LEGEND_LOCS.index(cur) if cur in LEGEND_LOCS else -1
        except ValueError:
            idx = -1
        nloc = LEGEND_LOCS[(idx + 1) % len(LEGEND_LOCS)]
        kw = dict(loc=nloc, frameon=frameon)
        if fs:
            kw['fontsize'] = fs
        self._mk_legend(st, ax, **kw)
        st['loc'] = nloc
        st['anchor'] = None
        _log('[mpltweak] %s 图例 -> %s' % (ax, nloc))
        self._draw_now()

    def _draw_now(self):
        if self.heavy:
            self._schedule_draw()
        else:
            self.fig.canvas.draw_idle()

    def _refresh(self, moved=False):
        '''刷新画布。moved=True 表示面板位置变了 → 背景失效，必须全量重绘；
        否则（只动蓝框/选区）重图下走 blit 帧，避免每次点选卡 ~800ms。'''
        self._update_status()
        if moved:
            self._remove_ghost()          # 内含 _bg = None（背景失效）
            self._hits = []               # 面板动了 → 文字也动了，命中表失效
            self.fig.canvas.draw_idle()
            return
        if self.heavy and self._blit_ok:
            if self._blit_frame():
                return
        self._draw_now()

    # ---------- 字号 ----------
    def _adj_font(self, ax, kind, delta):
        '''调字号：title/label/tick/legend，返回新字号。'''
        if kind == 'title':
            fs = ax.title.get_fontsize() + delta
            ax.title.set_fontsize(fs)
            return fs
        if kind == 'label':
            fs = ax.xaxis.label.get_fontsize() + delta
            ax.xaxis.label.set_fontsize(fs)
            ax.yaxis.label.set_fontsize(fs)
            return fs
        if kind == 'tick':
            fs = (ax.get_xticklabels()[0].get_fontsize()
                  if ax.get_xticklabels() else plt.rcParams['font.size']) + delta
            ax.tick_params(labelsize=fs)
            return fs
        if kind == 'legend':
            leg = ax.get_legend()
            if leg is None or not leg.get_texts():
                return None
            fs = leg.get_texts()[0].get_fontsize() + delta
            for t in leg.get_texts():
                t.set_fontsize(fs)
            return fs
        return None

    def _ax_under_mouse(self):
        '''取鼠标当前悬停的轴（键盘事件 inaxes 依赖鼠标位置，做容错）。'''
        try:
            mp = self.fig.canvas.mouseposition
        except Exception:
            return None
        if mp is None:
            return None
        for a in self.fig.axes:
            try:
                if a.contains_point(mp):
                    return a
            except Exception:
                pass
        return None

    # ---------- 事件处理 ----------
    # ---------- 快照（Undo/Redo） ----------
    def _snapshot(self):
        '''全状态快照：画布尺寸 + 每个轴的位置 + 字号 + 图例。'''
        _fs = self._fig_px
        if _fs is None:                      # 无头/未 resize 过的兜底
            try:
                _fs = self.fig.canvas.get_width_height()
            except Exception:
                _fs = None
        snap = {'figsize': tuple(_fs) if _fs else None,
                'axes': []}
        for ax in self.fig.axes:
            x0, y0, w, h = _bbox(ax)
            tls = ax.get_xticklabels()
            item = {
                'id': id(ax),
                'pos': [x0, y0, w, h],
                'title': ax.title.get_fontsize(),
                'xlabel': ax.xaxis.label.get_fontsize(),
                'ylabel': ax.yaxis.label.get_fontsize(),
                'xtick': tls[0].get_fontsize() if tls else None,
                'ytick': (ax.get_yticklabels()[0].get_fontsize()
                          if ax.get_yticklabels() else None),
                'is_colorbar': bool(_is_colorbar_ax(ax)),
                'clim': list(_clim_of(ax)) if _clim_of(ax) is not None else None,
                'xscale': ax.get_xscale(),
                'yscale': ax.get_yscale(),
                'grid': any(line.get_visible() for line in
                            list(ax.get_xgridlines()) + list(ax.get_ygridlines())),
                'spines': {k: bool(v.get_visible()) for k, v in ax.spines.items()},
                'lines': [{'index': j, 'linewidth': self._line_lw(line),
                           'color': self._line_color(line)}
                          for j, line in enumerate(getattr(ax, 'lines', []))],
            }
            mappable = None
            if _is_colorbar_ax(ax):
                _, mappable = _cb_mappable(ax)
            else:
                for artist in list(getattr(ax, 'images', [])) + list(getattr(ax, 'collections', [])):
                    if hasattr(artist, 'get_cmap'):
                        mappable = artist
                        break
            if mappable is not None:
                try:
                    item['cmap'] = mappable.get_cmap().name
                except Exception:
                    pass
            leg = ax.get_legend()
            if leg is not None:
                st = self.leg_state.get(id(ax), {})
                item['legend'] = {
                    'loc': st.get('loc'),
                    'anchor': st.get('anchor'),
                    'fontsize': leg.get_texts()[0].get_fontsize()
                    if leg.get_texts() else None,
                }
            snap['axes'].append(item)
        return snap

    def _find_ax(self, aid):
        for ax in self.fig.axes:
            if id(ax) == aid:
                return ax
        return None

    def _restore_snap(self, snap):
        # 先恢复画布尺寸（若有记录），再恢复各轴位置——否则轴位置是按旧画布算的
        fs = snap.get('figsize') if isinstance(snap, dict) else None
        if fs is not None:
            try:
                self.fig.set_size_inches(fs[0] / 100.0, fs[1] / 100.0)
                self._fig_px = (int(fs[0]), int(fs[1]))
            except Exception:
                pass
        items = snap.get('axes', []) if isinstance(snap, dict) else snap
        for item in items:
            ax = self._find_ax(item['id'])
            if ax is None:
                continue
            _set_bbox(ax, item['pos'])
            ax.title.set_fontsize(item['title'])
            ax.xaxis.label.set_fontsize(item['xlabel'])
            ax.yaxis.label.set_fontsize(item['ylabel'])
            if item.get('xtick'):
                for t in ax.get_xticklabels():
                    t.set_fontsize(item['xtick'])
            if item.get('ytick'):
                for t in ax.get_yticklabels():
                    t.set_fontsize(item['ytick'])
            if item.get('clim'):
                if _is_colorbar_ax(ax):
                    _set_cb_clim(ax, *item['clim'])
                else:
                    # 数据轴上的 mappable（contourf/imshow 在数据轴上）clim 也要回滚
                    _mp = None
                    for _c in getattr(ax, 'collections', []):
                        if hasattr(_c, 'get_clim') and _c.get_array() is not None:
                            _mp = _c
                            break
                    if _mp is None:
                        for _im in getattr(ax, 'images', []):
                            if hasattr(_im, 'get_clim'):
                                _mp = _im
                                break
                    if _mp is not None and hasattr(_mp, 'set_clim'):
                        try:
                            _mp.set_clim(*item['clim'])
                        except Exception:            # noqa: BLE001
                            pass
            if item.get('xscale') and not _is_colorbar_ax(ax):
                try:
                    ax.set_xscale(item['xscale'])
                    ax.set_yscale(item.get('yscale', ax.get_yscale()))
                except Exception:
                    pass
            if item.get('grid') is not None:
                ax.grid(bool(item['grid']))
            for k, visible in item.get('spines', {}).items():
                if k in ax.spines:
                    ax.spines[k].set_visible(bool(visible))
            for line_item in item.get('lines', []):
                j = line_item.get('index')
                lines = getattr(ax, 'lines', [])
                if j is not None and j < len(lines):
                    line = lines[j]
                    if line_item.get('linewidth') is not None:
                        line.set_linewidth(line_item['linewidth'])
                    if line_item.get('color') is not None:
                        line.set_color(line_item['color'])
            if item.get('cmap'):
                mappable = None
                if _is_colorbar_ax(ax):
                    _, mappable = _cb_mappable(ax)
                else:
                    for artist in list(getattr(ax, 'images', [])) + list(getattr(ax, 'collections', [])):
                        if hasattr(artist, 'set_cmap'):
                            mappable = artist
                            break
                if mappable is not None:
                    try:
                        mappable.set_cmap(item['cmap'])
                        if _is_colorbar_ax(ax):
                            ax._colorbar.update_normal(mappable)
                    except Exception:
                        pass
            if item.get('legend') and ax.get_legend() is not None:
                st = self.leg_state.get(id(ax))
                if st is not None:
                    lf = item['legend']
                    st['loc'] = lf['loc']
                    st['anchor'] = lf['anchor']
                    if lf['loc'] is not None:
                        self._mk_legend(st, ax, loc=lf['loc'],
                                        fontsize=lf['fontsize'],
                                        frameon=ax.get_legend().get_frame_on())
                    else:
                        self._mk_legend(st, ax, loc='lower left',
                                        bbox_to_anchor=tuple(lf['anchor']),
                                        bbox_transform=ax.transAxes,
                                        fontsize=lf['fontsize'],
                                        frameon=ax.get_legend().get_frame_on())
        self._rebuild_selection_artists()
        self._refresh(moved=True)

    def _sel_axes(self):
        return [a for a in self.fig.axes if id(a) in self._selected]

    def _align_selection(self, mode):
        '''多选后一键对齐。参照 = 选中面板的**外接框**（Figma/Illustrator 做法）。

        mode ∈ left / right / top / bottom / cx（水平居中）/ cy（垂直居中）
        '''
        axs = self._sel_axes()
        if len(axs) < 2:
            _log('[mpltweak] 对齐需要先选中 ≥2 个面板（Ctrl+点击 或 拖框选）')
            return
        bs = {id(a): _bbox(a) for a in axs}
        x0 = min(b[0] for b in bs.values())
        x1 = max(b[0] + b[2] for b in bs.values())
        y0 = min(b[1] for b in bs.values())
        y1 = max(b[1] + b[3] for b in bs.values())
        self._push_undo()
        for a in axs:
            bx, by, bw, bh = bs[id(a)]
            if mode == 'left':
                bx = x0
            elif mode == 'right':
                bx = x1 - bw
            elif mode == 'cx':
                bx = (x0 + x1) / 2.0 - bw / 2.0
            elif mode == 'bottom':
                by = y0
            elif mode == 'top':
                by = y1 - bh
            elif mode == 'cy':
                by = (y0 + y1) / 2.0 - bh / 2.0
            _set_bbox(a, [bx, by, bw, bh])
        self._rebuild_selection_artists()
        self._set_title_hint('对齐 %s（%d 个面板）' % (mode, len(axs)))
        self._refresh(moved=True)

    def _distribute_selection(self, axis):
        '''均分：两端面板不动，中间等间距 —— gap = (span - Σsize) / (n-1)。'''
        axs = self._sel_axes()
        if len(axs) < 3:
            _log('[mpltweak] 均分需要先选中 ≥3 个面板')
            return
        axs.sort(key=(lambda a: _bbox(a)[0]) if axis == 'h'
                 else (lambda a: _bbox(a)[1]))
        bs = [_bbox(a) for a in axs]
        if axis == 'h':
            lo = bs[0][0]
            hi = max(b[0] + b[2] for b in bs)
            total = sum(b[2] for b in bs)
        else:
            lo = bs[0][1]
            hi = max(b[1] + b[3] for b in bs)
            total = sum(b[3] for b in bs)
        gap = (hi - lo - total) / (len(bs) - 1)
        self._push_undo()
        cur = lo
        for a, b in zip(axs, bs):
            if axis == 'h':
                _set_bbox(a, [cur, b[1], b[2], b[3]])
                cur += b[2] + gap
            else:
                _set_bbox(a, [b[0], cur, b[2], b[3]])
                cur += b[3] + gap
        self._rebuild_selection_artists()
        self._set_title_hint('均分 %s（%d 个面板）'
                             % ('水平' if axis == 'h' else '垂直', len(axs)))
        self._refresh(moved=True)

    def _fit_to_content(self):
        '''一键「适配画布到内容」（快捷键）：把画布缩到刚好包住所有内容——
        面板 + 图例 + 标题/轴标签/刻度文字，四周白边全裁掉，子图**像素大小
        不变**、整体平移到边距处。

        坐标系：全程用 **figure 归一化坐标**（0-1 比例）。tightbbox 返回的
        物理像素要除以 fig.bbox 才归一化（高 DPI 下 fig.dpi=200、bbox 是逻辑
        像素的 2 倍，直接混用会把内容范围算大 2 倍 → 永远"已贴边"）。
        '''
        _info('[mpltweak] fit: 收到快捷键', flush=True)
        try:
            renderer = self.fig.canvas.get_renderer()
            W, H = self.fig.canvas.get_width_height()   # 逻辑像素
        except Exception:
            import traceback as _tb
            _tb.print_exc()
            _info('[mpltweak] fit: 拿不到 renderer，无法适配', flush=True)
            return
        if not self.fig.axes or W <= 0 or H <= 0:
            _info('[mpltweak] fit: 无轴或画布无效 %dx%d' % (W, H), flush=True)
            return
        BW, BH = self.fig.bbox.width, self.fig.bbox.height   # 物理像素
        if BW <= 0 or BH <= 0:
            BW, BH = W, H
        # 1) 归一化内容范围：轴框（兜底）∪ tightbbox（含文字）∪ 图例。
        #    面板定位用轴框归一化（ax_px 存归一化，最后转逻辑像素）。
        boxes = []          # 归一化 [x0, y0, x1, y1]
        ax_px = []          # [ax, nx0, ny0, nw, nh] —— 轴框归一化
        for ax in self.fig.axes:
            x0, y0, w, h = _bbox(ax)
            ax_px.append([ax, x0, y0, w, h])
            boxes.append([x0, y0, x0 + w, y0 + h])
            try:
                tb2 = ax.get_tightbbox(renderer)
                if tb2 is not None:
                    boxes.append([tb2.x0 / BW, tb2.y0 / BH,
                                  tb2.x1 / BW, tb2.y1 / BH])
            except Exception:
                pass
            leg = ax.get_legend()
            if leg is not None and leg.get_visible():
                try:
                    lb = leg.get_window_extent(renderer=renderer)
                    boxes.append([lb.x0 / BW, lb.y0 / BH,
                                  lb.x1 / BW, lb.y1 / BH])
                except Exception:
                    pass
        cx0 = min(b[0] for b in boxes)
        cy0 = min(b[1] for b in boxes)
        cx1 = max(b[2] for b in boxes)
        cy1 = max(b[3] for b in boxes)
        cw = max(cx1 - cx0, 1e-4)
        ch = max(cy1 - cy0, 1e-4)
        # 2) 新画布（逻辑像素）= 归一化内容范围 × 当前逻辑画布 + 边距
        m = int(round(FIT_MARGIN_IN * 100.0))
        NW = max(2 * m + 1, int(round(cw * W + 2 * m)))
        NH = max(2 * m + 1, int(round(ch * H + 2 * m)))
        if NW >= W and NH >= H:
            _info('[mpltweak] fit: 内容已贴边，无需适配（%dx%d）' % (W, H), flush=True)
            return
        self._push_undo()
        # 3) 改画布
        try:
            self.fig.set_size_inches(NW / 100.0, NH / 100.0)
            self._fig_px = (NW, NH)
        except Exception:
            import traceback as _tb
            _tb.print_exc()
            _info('[mpltweak] fit: set_size_inches 失败', flush=True)
            return
        # 4) 面板像素大小不变，内容左上角贴边距 → 新归一化坐标
        #    原面板逻辑像素 = 归一化 × 旧逻辑画布；新归一化 = 像素 / 新逻辑画布
        for ax, nx0, ny0, nw, nh in ax_px:
            px0, py0 = nx0 * W, ny0 * H        # 逻辑像素（大小与位置）
            _set_bbox(ax, [(px0 - cx0 * W + m) / NW,
                           (py0 - cy0 * H + m) / NH,
                           nw * W / NW, nh * H / NH])
        self._rebuild_selection_artists()
        self._set_title_hint('适配画布到内容 → %dx%d px' % (NW, NH))
        self._refresh(moved=True)
        _info('[mpltweak] fit: 完成 → %dx%d px（原 %dx%d）'
              % (NW, NH, W, H), flush=True)

    def _snap_on(self, event):
        '''当前是否吸附：开关开着 且 **没按住 Alt**（Alt = 临时关吸附）。'''
        if not self.snap:
            return False
        return 'alt' not in str(getattr(event, 'key', '') or '').lower()

    def _axis_name(self, axid):
        for _j, _a in enumerate(self.fig.axes):
            if id(_a) == axid:
                return 'ax%d' % _j
        return 'ax?'

    # ---------- ? 键位浮层（发现层：功能一眼可查） ----------
    def _toggle_help(self):
        '''? 键位浮层：弹出独立键位表窗口（非模态，可边看边调，不挡主图）。
        再按 ? = 重新置顶；Esc / 点 × = 关闭。'''
        _show_help_fig()

    def _diff_name(self, prev, cur):
        '''从两个快照反推操作名（撤销可视化显示用，不必精确到词级）。'''
        if not isinstance(prev, dict) or not isinstance(cur, dict):
            return None
        paxes = {i.get('id'): i for i in prev.get('axes', [])}
        out = []
        for it in cur.get('axes', []):
            p = paxes.get(it.get('id'))
            if p is None:
                continue
            an = self._axis_name(it['id'])
            if p.get('pos') != it.get('pos'):
                out.append('移动 ' + an)
            if p.get('title') != it.get('title'):
                out.append('调字号 ' + an)
            if p.get('xlabel') != it.get('xlabel') or p.get('ylabel') != it.get('ylabel'):
                out.append('调轴标 ' + an)
            if p.get('clim') != it.get('clim'):
                out.append('调色标 ' + an)
            if p.get('grid') != it.get('grid'):
                out.append('网格 ' + an)
            if p.get('spines') != it.get('spines'):
                out.append('边框 ' + an)
            if p.get('xscale') != it.get('xscale') or p.get('yscale') != it.get('yscale'):
                out.append('比例尺 ' + an)
            if p.get('lines') != it.get('lines'):
                out.append('线条 ' + an)
        if prev.get('figsize') != cur.get('figsize'):
            out.append('画布尺寸')
        seen, uniq = set(), []
        for s in out:
            if s not in seen:
                seen.add(s)
                uniq.append(s)
        return '、'.join(uniq) if uniq else None

    def _update_status(self):
        '''刷新画布底部状态栏：选中数 / 吸附 / 可撤可重做步数。'''
        if self._status is None:
            return
        try:
            self._status.set_text(
                '选中 %d | 吸附 %s | 可撤 %d / 可重做 %d'
                % (len(self._selected), '开' if self.snap else '关',
                   len(self._undo), len(self._redo)))
        except Exception:                    # noqa: BLE001
            pass

    def _toast(self, msg):
        '''画布底部 1.5s 瞬态提示（操作反馈，不依赖 QUIET）。'''
        if self._toast_art is None:
            return
        try:
            self._toast_art.set_text(msg)
            self._toast_art.set_visible(True)
            self.fig.canvas.draw_idle()
            if self._toast_timer is not None:
                try:
                    self._toast_timer.stop()
                except Exception:
                    pass
            try:
                _t = self.fig.canvas.new_timer(interval=1500)
                _t.single_shot = True
                _t.add_callback(self._hide_toast)
                _t.start()
                self._toast_timer = _t
            except Exception:                # noqa: BLE001
                pass
        except Exception:                    # noqa: BLE001
            pass

    def _hide_toast(self):
        if self._toast_art is None:
            return
        try:
            self._toast_art.set_visible(False)
            self.fig.canvas.draw_idle()
        except Exception:                    # noqa: BLE001
            pass

    def _push_undo(self):
        # 每次真实改动前压快照 —— 这里同时记录"本图被改过"与全局改动顺序。
        # 快照附带操作名（diff 上一快照），撤销/重做时能告诉用户"撤的是什么"。
        self._edit_clock[0] += 1
        self.edit_seq = self._edit_clock[0]
        _new = self._snapshot()
        _prev = self._undo[-1] if self._undo else None
        _new['_name'] = self._diff_name(_prev, _new) or '调整'
        self._undo.append(_new)
        if len(self._undo) > 60:
            self._undo.pop(0)
        self._redo.clear()
        # L4：任何非 nudge 操作入 undo 都打断"方向键微调合并"（seal），否则 0.6s 内
        # "拖一下→按方向键"会被错误合并进前一次 nudge 的 undo 条目。nudge 流程里
        # 本方法调用后立刻重建 seal（_nudge_seal_t=now），不受此重置影响。
        self._nudge_seal = None
        self._update_status()

    def _push_undo_sealed(self, key):
        '''同类连续微调（字号/线宽等离散点击）0.6s 内合并为一步 undo——
        否则连续调字号会一条条灌满 60 步上限。先 _push_undo（内部重置 seal），
        再记新 seal，与 _nudge_size 的合并语义一致。'''
        now = time.perf_counter()
        if self._nudge_seal == key and now - self._nudge_seal_t < 0.6:
            return
        self._push_undo()
        self._nudge_seal = key
        self._nudge_seal_t = now

    def _undo_once(self):
        if not self._undo:
            _log('[mpltweak] 没有可撤销的操作', flush=True)
            self._toast('没有可撤销的操作')
            return
        _s = self._undo[-1]
        _cur = self._snapshot()                # 操作后的当前状态
        self._redo.append(_cur)
        self._restore_snap(self._undo.pop())
        # 操作名现算：diff(弹出快照, 当前) = 这次撤销回退的变化
        _n = self._diff_name(_s, _cur) or (_s.get('_name') or '调整')
        _log('[mpltweak] 撤销：%s（剩 %d 步）' % (_n, len(self._undo)), flush=True)
        self._toast('已撤销：%s' % _n)
        self._update_status()

    def _redo_once(self):
        if not self._redo:
            _log('[mpltweak] 没有可重做的操作', flush=True)
            self._toast('没有可重做的操作')
            return
        _s = self._redo[-1]
        _cur = self._snapshot()                # 撤销后的当前状态
        self._undo.append(_cur)
        self._restore_snap(self._redo.pop())
        _n = self._diff_name(_cur, _s) or (_s.get('_name') or '调整')
        _log('[mpltweak] 重做：%s' % _n, flush=True)
        self._toast('已重做：%s' % _n)
        self._update_status()

    # ---------- 多选（PPT 式） ----------
    def _rebuild_selection_artists(self):
        '''按当前 _selected 重建蓝色选中外框（不重绘，由调用方 draw）。'''
        for a in self._sel_artists:
            try:
                a.remove()
            except Exception:
                pass
        self._sel_artists = []
        self._sel_map = {}
        for ax in self.fig.axes:
            if id(ax) not in self._selected:
                continue
            x0, y0, w, h = _bbox(ax)
            r = Rectangle((x0, y0), w, h, fill=False, edgecolor=SEL_EDGE,
                          linewidth=1.6, transform=self.fig.transFigure,
                          zorder=5000)
            self.fig.add_artist(r)
            self._sel_artists.append(r)
            self._sel_map[id(ax)] = r

    def _sync_sel(self, ax, box):
        '''拖拽期间把某个面板的蓝框挪到 box（fig 比例），实现蓝框跟手。'''
        r = getattr(self, '_sel_map', {}).get(id(ax))
        if r is not None:
            r.set_bounds(box[0], box[1], box[2], box[3])

    def _clear_selection(self):
        if not self._selected:
            return
        self._selected.clear()
        self._rebuild_selection_artists()
        self._refresh()

    def _update_band(self, x, y):
        '''橡皮筋框从起点画到当前点（fig 比例）。'''
        X, Y = self.fig.bbox.size
        x0 = min(self._band_origin[0], x) / X
        x1 = max(self._band_origin[0], x) / X
        y0 = min(self._band_origin[1], y) / Y
        y1 = max(self._band_origin[1], y) / Y
        if self._band is None:
            self._band = Rectangle((x0, y0), x1 - x0, y1 - y0,
                                   facecolor=BAND_EDGE, alpha=0.15,
                                   edgecolor=BAND_EDGE, lw=1.2,
                                   transform=self.fig.transFigure, zorder=5001)
            self.fig.add_artist(self._band)
        else:
            self._band.set_bounds(x0, y0, x1 - x0, y1 - y0)
        if self.heavy:
            if self._blit_ok:
                self._blit_frame()          # 重图：橡皮筋走 blit（全量重绘要 ~800ms）
            else:
                self._throttled_draw()
        else:
            self.fig.canvas.draw_idle()

    def _finish_band(self):
        '''框选结束：与选框相交的面板全选中；框太小视为点击=清空选中。'''
        band, self._band = self._band, None
        self._band_active = False
        self._band_origin = None
        if band is None:
            self._clear_selection()
            return
        try:
            band.remove()
        except Exception:
            pass
        x0, y0, w, h = band.get_bbox().bounds
        if w < 0.01 or h < 0.01:
            self._clear_selection()
            return
        bx0, bx1, by0, by1 = x0, x0 + w, y0, y0 + h
        picked = set()
        for ax in self.fig.axes:
            px0, py0, pw, ph = _bbox(ax)
            # 「完全框住」才选中（相交不算）
            if (px0 >= bx0 - 1e-9 and px0 + pw <= bx1 + 1e-9
                    and py0 >= by0 - 1e-9 and py0 + ph <= by1 + 1e-9):
                picked.add(id(ax))
        self._selected = picked
        self._rebuild_selection_artists()
        self._refresh()
        _log('[mpltweak] 框选 %d 个面板' % len(self._selected), flush=True)

    def _legend_candidates(self, ax, leg):
        '''8 个标准位的图例框（轴坐标）：{name: (x, y, w, h)}。

        含 borderaxespad 折算，预览框与真正吸附后的位置一致。
        '''
        try:
            we = leg.get_window_extent()
            inv = ax.transAxes.inverted()
            x0, y0 = inv.transform((we.x0, we.y0))
            x1, y1 = inv.transform((we.x1, we.y1))
        except Exception:
            return None
        w, h = x1 - x0, y1 - y0
        # Legend 没有 get_fontsize()，用图例文字的字号（legend.fontsize 可能是字符串）
        try:
            fs = leg.get_texts()[0].get_fontsize() if leg.get_texts() else None
        except Exception:
            fs = None
        if not isinstance(fs, (int, float)):
            try:
                fs = float(matplotlib.rcParams['legend.fontsize'])
            except Exception:
                fs = 10.0
        pad = leg.borderaxespad * fs / 72.0 * self.fig.dpi
        px = pad / max(ax.bbox.width, 1.0)
        py = pad / max(ax.bbox.height, 1.0)
        return {
            'upper right': (1 - w - px, 1 - h - py, w, h),
            'upper center': (0.5 - w / 2, 1 - h - py, w, h),
            'upper left': (px, 1 - h - py, w, h),
            'center left': (px, 0.5 - h / 2, w, h),
            'lower left': (px, py, w, h),
            'lower center': (0.5 - w / 2, py, w, h),
            'lower right': (1 - w - px, py, w, h),
            'center right': (1 - w - px, 0.5 - h / 2, w, h),
        }

    def _legend_nearest(self, ax, leg, cands=None):
        '''当前图例离哪个标准位最近 -> name。'''
        leg = leg if leg is not None else ax.get_legend()
        if leg is None:
            return None
        cands = cands or self._legend_candidates(ax, leg)
        if not cands:
            return None
        try:
            we = leg.get_window_extent()
            inv = ax.transAxes.inverted()
            x0, y0 = inv.transform((we.x0, we.y0))
            x1, y1 = inv.transform((we.x1, we.y1))
        except Exception:
            return None
        ccx, ccy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
        return min(cands, key=lambda k: (cands[k][0] + cands[k][2] / 2 - ccx) ** 2
                   + (cands[k][1] + cands[k][3] / 2 - ccy) ** 2)

    def _legend_preview_show(self, ax, leg):
        '''拖图例时显示 8 个候选位的半透明预览框。'''
        self._legend_preview_hide()
        cands = self._legend_candidates(ax, leg)
        if not cands:
            return
        self._leg_preview_map = {}
        for name, (bx, by, w, h) in cands.items():
            r = Rectangle((bx, by), w, h, transform=ax.transAxes,
                          facecolor=BAND_EDGE, alpha=0.10,
                          edgecolor=BAND_EDGE, lw=0.8, ls='--', zorder=5002)
            self.fig.add_artist(r)
            self._leg_preview.append(r)
            self._leg_preview_map[name] = r
        self._legend_preview_highlight(ax, leg, cands)

    def _legend_preview_highlight(self, ax, leg, cands=None):
        '''高亮"将要吸附到"的那个候选位，其余淡显。'''
        if not self._leg_preview_map:
            return
        best = self._legend_nearest(ax, leg, cands)
        for name, r in self._leg_preview_map.items():
            if name == best:
                r.set_facecolor(BAND_EDGE)
                r.set_alpha(0.30)
                r.set_linestyle('-')
                r.set_linewidth(1.6)
            else:
                r.set_facecolor(BAND_EDGE)
                r.set_alpha(0.10)
                r.set_linestyle('--')
                r.set_linewidth(0.8)

    def _legend_preview_hide(self):
        for r in self._leg_preview:
            try:
                r.remove()
            except Exception:
                pass
        self._leg_preview = []
        self._leg_preview_map = {}

    def _snap_legend(self, ax):
        '''拖拽结束：把图例吸附到 8 个标准位里最近的一个（不做自由定位）。'''
        leg = ax.get_legend()
        st = self.leg_state.get(id(ax))
        if leg is None or st is None:
            return
        best = self._legend_nearest(ax, leg)
        if best is None:
            return
        fs = leg.get_texts()[0].get_fontsize() if leg.get_texts() else None
        kw = dict(loc=best, frameon=leg.get_frame_on())
        if fs:
            kw['fontsize'] = fs
        self._mk_legend(st, ax, **kw)
        st['loc'] = best
        st['anchor'] = None
        _log('[mpltweak] 图例吸附到 %s' % best, flush=True)

    # ---------- 尺寸 HUD（窗口标题栏） ----------
    def _set_title_hint(self, text):
        try:
            m = self.fig.canvas.manager
            if m is None:
                return
            if text is None:
                m.set_window_title(self._title_base or 'mpltweak 改图')
            else:
                m.set_window_title('%s — %s' % (self._title_base or 'mpltweak 改图', text))
        except Exception:
            pass

    def _hint_box(self, idx, box):
        X, Y = self.fig.bbox.size
        return 'ax%d [%.3f,%.3f,%.3f,%.3f] %dx%dpx' % (
            idx, box[0], box[1], box[2], box[3],
            round(box[2] * X), round(box[3] * Y))

    @staticmethod
    def _fmt_clim(clim):
        if clim is None:
            return 'clim [?]'
        return 'clim [%.4g, %.4g]' % tuple(clim)

    def _update_cb_clim_drag(self, x, y):        # noqa: D401 - 已随 Alt+clim 移除
        return

    def _finish_cb_clim_drag(self):             # noqa: D401 - 已随 Alt+clim 移除
        return

    def _on_press(self, event):
        X, Y = self.fig.bbox.size
        # 右键：暂不响应（曾打印坐标框调试、会污染 stdout，已移除；将来可放右键菜单）
        if event.button != 1:
            return
        # 左键
        # 1) 图例拖拽
        ax_leg, leg = self._legend_at(event.x, event.y)
        if leg is not None:
            st = self.leg_state.get(id(ax_leg))
            if st is None:
                return
            self._push_undo()
            fs = leg.get_texts()[0].get_fontsize() if leg.get_texts() else None
            frameon = leg.get_frame_on()
            # 按图例当前窗口位置反算轴坐标锚点，重建为「lower-left 锚定」，
            # 保证按下瞬间不跳位；之后 motion 只更新锚点。
            anchor0 = self._leg_anchor(ax_leg, leg)
            # 注意：matplotlib 3.10 的 Axes.legend() 不接受 transform，要用 bbox_transform
            self._mk_legend(st, ax_leg, loc='lower left',
                            bbox_to_anchor=(anchor0[0], anchor0[1]),
                            bbox_transform=ax_leg.transAxes,
                            fontsize=fs, frameon=frameon)
            self._leg = ax_leg.get_legend()
            self._leg_ax = ax_leg
            self._leg_anchor0 = anchor0
            st['loc'] = None                     # 自由拖拽 = 锚定模式
            st['anchor'] = list(anchor0)
            self._press_xy = (event.x, event.y)
            self._drag_leg = True
            if self.heavy and self._blit_ok:
                # 重图：先把图例藏掉刷新干净背景（无拖痕），再画回起始位置
                self._leg.set_visible(False)
                try:
                    self._ensure_bg()
                except Exception:
                    self._blit_ok = False
                self._leg.set_visible(True)
                if self._blit_ok:
                    self.fig.canvas.restore_region(self._bg)
                    self.fig.draw_artist(self._leg)
                    self.fig.canvas.blit(self.fig.bbox)
            # 8 位候选预览（半透明），最近的那个高亮
            self._legend_preview_show(ax_leg, self._leg)
            if not self.heavy:
                self.fig.canvas.draw_idle()
            return
        # 2) colorbar 端点：拖 = 调长度（Alt+拖改 clim 已按用户要求移除）
        # 3) 边框/角缩放
        ax_h, hd = _hit_handle(self.fig, event.x, event.y)
        if ax_h is not None:
            self._push_undo()
            self._rs_ax = ax_h
            self._rs_handle = hd
            x0, y0, w, h = _bbox(ax_h)
            self._rs_origin = [x0, y0, x0 + w, y0 + h]
            self._rs_target = list(self._rs_origin)
            self._press_xy = (event.x, event.y)
            self._rs_mods = set((event.key or '').split('+')) if event.key else set()
            self._drag_resize = True
            _log('[mpltweak] 缩放开始 ax%d %s' % (
                self.fig.axes.index(ax_h), hd), flush=True)
            if self.heavy:
                self._blit_ghost([x0, y0, w, h])
            return
        # 3) 空白：橡皮筋框选起点（拖出选区；纯点击=清空选中）
        if event.inaxes is None:
            self._band_origin = (event.x, event.y)
            self._band_active = True
            return
        # 4) Ctrl/Shift+点击：切换该面板的选中态（PPT 里是 Ctrl+点击）。
        #    Ctrl+Shift 同时按住时 event.key='ctrl+shift'，不能只匹配单个键名。
        if set((event.key or '').split('+')) & {'ctrl', 'shift'}:
            aid = id(event.inaxes)
            if aid in self._selected:
                self._selected.discard(aid)
            else:
                self._selected.add(aid)
            self._rebuild_selection_artists()
            self._refresh()
            _log('[mpltweak] 选中 %d 个面板' % len(self._selected), flush=True)
            return
        # 5) 点击已选中面板且处于多选 → 整体拖拽（拖一个全部跟着动）
        if len(self._selected) > 1 and id(event.inaxes) in self._selected:
            self._push_undo()
            self._multi_drag = True
            self._drag_ax = event.inaxes
            self._drag_ax_origin = list(_bbox(event.inaxes))
            self._multi_origin = {id(a): list(_bbox(a))
                                  for a in self.fig.axes
                                  if id(a) in self._selected}
            self._press_xy = (event.x, event.y)
            _log('[mpltweak] 多选拖拽 %d 个面板' % len(self._selected), flush=True)
            return
        # 6) 点击面板：单选（显示蓝框）+ 拖拽
        self._selected = {id(event.inaxes)}
        self._rebuild_selection_artists()
        self._push_undo()
        self._ax = event.inaxes
        self._origin = _bbox(event.inaxes)
        self._target = list(self._origin)
        self._press_xy = (event.x, event.y)
        self._drag = True
        _log('[mpltweak] 拖轴开始 ax%d' % self.fig.axes.index(event.inaxes), flush=True)
        if self.heavy:
            self._blit_ghost(self._target)
        else:
            self._refresh()

    def _leg_anchor(self, ax, leg):
        '''图例当前左下角在轴坐标比例下的位置（窗口范围反算，按下不跳位）。'''
        try:
            we = leg.get_window_extent()
            inv = ax.transAxes.inverted()
            ll = inv.transform((we.x0, we.y0))
            return [ll[0], ll[1]]
        except Exception:
            return [0.5, 0.5]

    def _on_motion(self, event):
        if event.inaxes is not None:
            self._last_ax = event.inaxes
        X, Y = self.fig.bbox.size
        if X == 0 or Y == 0:
            return
        if self._drag_leg and self._leg is not None:
            # 拖图例：锚点随鼠标移动（轴坐标比例增量）
            ax = self._leg_ax
            aw = ax.bbox.width
            ah = ax.bbox.height
            if aw == 0 or ah == 0:
                return
            dx = (event.x - self._press_xy[0]) / aw
            dy = (event.y - self._press_xy[1]) / ah
            anchor = [self._leg_anchor0[0] + dx, self._leg_anchor0[1] + dy]
            self._leg.set_bbox_to_anchor((anchor[0], anchor[1]),
                                         transform=ax.transAxes)
            self.leg_state.get(id(ax), {})['anchor'] = anchor
            self._legend_preview_highlight(ax, self._leg)
            if self.heavy:
                if self._blit_ok:
                    if self._bg is None:
                        try:
                            self._ensure_bg()
                        except Exception:
                            self._blit_ok = False
                    if self._blit_ok:
                        self.fig.canvas.restore_region(self._bg)
                        for r in self._leg_preview:
                            self.fig.draw_artist(r)
                        self.fig.draw_artist(self._leg)
                        self.fig.canvas.blit(self.fig.bbox)
                else:
                    self.fig.canvas.draw_idle()
            else:
                self.fig.canvas.draw_idle()
            return
        if self._band_active:
            # 橡皮筋框选：空白按下后拖动画选区
            self._update_band(event.x, event.y)
            return
        if self._multi_drag and self._drag_ax is not None:
            # 多选拖拽：拖被抓住的面板，所有选中面板按同一位移跟随
            dx = (event.x - self._press_xy[0]) / X
            dy = (event.y - self._press_xy[1]) / Y
            target = [self._drag_ax_origin[0] + dx,
                      self._drag_ax_origin[1] + dy,
                      self._drag_ax_origin[2], self._drag_ax_origin[3]]
            gx = gy = ()
            if self._snap_on(event):
                others = [_bbox(a) for a in self.fig.axes
                          if a is not self._drag_ax]
                target, gx, gy = _snap_box(target, others)
            fx = target[0] - self._drag_ax_origin[0]
            fy = target[1] - self._drag_ax_origin[1]
            for a in self.fig.axes:
                if id(a) not in self._selected:
                    continue
                o = self._multi_origin.get(id(a))
                if o is None:
                    continue
                nb = [o[0] + fx, o[1] + fy, o[2], o[3]]
                _set_bbox(a, nb)
                self._sync_sel(a, nb)       # 每个选中面板的蓝框都跟手
            if self.heavy:
                if self._blit_ok:
                    # 重图：被抓住的面板给 ghost 预览 + 全部蓝框同步 → 一次 blit
                    self._blit_ghost(target)
                else:
                    self._throttled_draw()
            else:
                self.fig.canvas.draw_idle()
            self._set_title_hint('移动 %d 个面板' % len(self._selected))
            return
        if self._drag_resize and self._rs_ax is not None:
            # PPT 式缩放：被拖边/角随鼠标，对边固定，可吸附
            mx, my = event.x / X, event.y / Y
            # Shift = 强制等比（对普通轴也生效）；Alt = 以框中心为锚对称缩放。
            # 拖拽中 event.key 带当前修饰键，取不到时退回按下时记录的。
            _mods = set((event.key or '').split('+')) if event.key else set(
                getattr(self, '_rs_mods', set()))
            locked = self.locked_map.get(id(self._rs_ax), False)
            if 'shift' in _mods:
                locked = True
            center = 'alt' in _mods
            ms = (MIN_SIZE_CB if self._as_colorbar(self._rs_ax) is not None
                  else MIN_SIZE)
            # 细条（colorbar 等）按**拖动方向**判定意图，手感宽容得多：
            #   竖直拖 = 调厚度（上/下边）；水平拖 = 调长度（左/右端）。
            #   否则在 ~10px 高的条子上，"抓上边"与"抓左端"只差几像素，几乎瞄不准。
            handle = self._rs_handle
            b0 = self._rs_origin
            px_w = (b0[2] - b0[0]) * X
            px_h = (b0[3] - b0[1]) * Y
            thin = min(px_w, px_h) < 3 * EDGE_TOL
            if thin:
                # 细条一律按边调整（不做等比缩放）：否则一旦碰到最小尺寸，
                # locked 分支会把 `s` 抬到 min_size/h，导致**宽高一起缩**
                # （表现为"减到最小高度以下就整体缩了"）。
                locked = False
                dxp = event.x - self._press_xy[0]
                dyp = event.y - self._press_xy[1]
                if abs(dxp) > abs(dyp) * 1.5:
                    handle = ('left' if (self._press_xy[0] - b0[0] * X)
                              < (b0[2] * X - self._press_xy[0]) else 'right')
                else:
                    handle = ('bottom' if (self._press_xy[1] - b0[1] * Y)
                              < (b0[3] * Y - self._press_xy[1]) else 'top')
            b1 = _resize_frac(self._rs_origin, handle, mx, my, locked, ms,
                              center=center)
            b1_raw = list(b1)
            gx = gy = ()
            if self._snap_on(event):
                others = []
                for a in self.fig.axes:
                    if a is self._rs_ax:
                        continue
                    x0, y0, w, h = _bbox(a)
                    others.append([x0, y0, x0 + w, y0 + h])
                b1, gx, gy = _snap_resize(b1, handle, others)
            if not QUIET:
                print('[mpltweak] DBG ax%d 按下=%s 实际=%s locked=%s origin=%s '
                      'resize=%s snap=%s (gx=%s gy=%s)'
                      % (self.fig.axes.index(self._rs_ax), self._rs_handle,
                         handle, locked,
                         ['%.4f' % v for v in self._rs_origin],
                         ['%.4f' % v for v in b1_raw],
                         ['%.4f' % v for v in b1], gx, gy), flush=True)
            self._rs_target = b1
            box = [b1[0], b1[1], b1[2] - b1[0], b1[3] - b1[1]]
            self._sync_sel(self._rs_ax, box)      # 蓝框跟着缩放
            self._set_title_hint(self._hint_box(
                self.fig.axes.index(self._rs_ax), box))
            if self.heavy:
                if self._blit_ok:
                    self._blit_ghost(box, gx, gy)
                else:
                    _set_bbox(self._rs_ax, box)      # 降级：面板直接跟手
                    self._throttled_draw()
            else:
                _set_bbox(self._rs_ax, box)
                self.fig.canvas.draw_idle()
            return
        if self._drag and self._ax is not None:
            # 拖轴：平移 + 自动吸附
            dx = (event.x - self._press_xy[0]) / X
            dy = (event.y - self._press_xy[1]) / Y
            box = self._origin
            target = [box[0] + dx, box[1] + dy, box[2], box[3]]
            gx = gy = ()
            if self._snap_on(event):
                others = [_bbox(a) for a in self.fig.axes if a is not self._ax]
                target, gx, gy = _snap_box(target, others)
            self._target = target
            self._sync_sel(self._ax, target)      # 蓝框跟着面板（ghost 模式也跟着）
            self._set_title_hint(self._hint_box(
                self.fig.axes.index(self._ax), target))
            if self.heavy:
                if self._blit_ok:
                    self._blit_ghost(target, gx, gy)
                else:
                    _set_bbox(self._ax, target)      # 降级：面板直接跟手（节流）
                    self._throttled_draw()
            else:
                _set_bbox(self._ax, target)
                self.fig.canvas.draw_idle()
            return
        # hover：边框/角 → 变换光标提示（PPT 手感）
        ax_h, hd = _hit_handle(self.fig, event.x, event.y)
        # colorbar 长轴端点：普通拖=调长度、Alt+拖=改 clim。
        # 改 clim 要按 Alt 是"隐藏功能"，不给提示没人猜得到 → 悬停即说清。
        ax_cb, _cb_side = _hit_cb_endpoint(self.fig, event.x, event.y)
        if ax_cb is not None:
            horiz = _cb_orientation(ax_cb) == 'horizontal'
            ax_h, hd = ax_cb, ('left' if horiz else 'top')
            self._set_title_hint(
                'Alt+拖%s端 = 改 clim %s ｜ 普通拖 = 调长度（%s）'
                % ('左/右' if horiz else '下/上', self._fmt_clim(_clim_of(ax_cb)),
                   self._hint_box(self.fig.axes.index(ax_cb),
                                  _bbox(ax_cb))))
            self._cb_hint_on = True
        elif self._cb_hint_on:
            self._set_title_hint(None)
            self._cb_hint_on = False
        w = getattr(getattr(self.fig.canvas, 'manager', None), 'window', None)
        qt = None
        if w is not None:
            try:
                from matplotlib.backends.qt_compat import QtCore
                qt = QtCore
            except Exception:
                qt = None
        if hd is not None:
            done = False
            if qt is not None:
                # Qt 原生光标（含对角线）。Qt6 是 scoped enum（Qt.CursorShape.XXX），
                # Qt5 是 Qt.XXX；两代都要兼容，取不到就静默退回 matplotlib 光标，
                # 绝不抛异常（曾因 AttributeError 每次鼠标移动刷屏 stderr）。
                try:
                    names = {'left': 'SizeHorCursor', 'right': 'SizeHorCursor',
                             'top': 'SizeVerCursor', 'bottom': 'SizeVerCursor',
                             'tl': 'SizeFDiagCursor', 'br': 'SizeFDiagCursor',
                             'tr': 'SizeBDiagCursor', 'bl': 'SizeBDiagCursor'}
                    shape_ns = getattr(qt.Qt, 'CursorShape', qt.Qt)
                    curs = getattr(shape_ns, names[hd])
                    if curs is not self._cur:
                        self._cur = curs
                        self.fig.canvas.setCursor(curs)
                    done = True
                except Exception:
                    done = False
            if not done:
                c = _CURSORS.get(hd, Cursors.POINTER)
                if c is not self._cur:
                    self._cur = c
                    try:
                        self.fig.canvas.set_cursor(c)
                    except Exception:
                        pass
        elif self._cur is not None:
            self._cur = None
            try:
                if qt is not None:
                    self.fig.canvas.unsetCursor()
                else:
                    self.fig.canvas.set_cursor(Cursors.POINTER)
            except Exception:
                pass

    def _on_release(self, event):
        if self._drag_leg:
            self._drag_leg = False
            self._legend_preview_hide()
            ax = self._leg_ax
            if ax is not None:
                self._snap_legend(ax)      # 松手吸附到最近的 8 个标准位之一
            if self.heavy:
                self._remove_ghost()
            self.fig.canvas.draw_idle()
            self._leg = None
            self._leg_ax = None
            return
        if self._multi_drag:
            self._multi_drag = False
            self._drag_ax = None
            self._drag_ax_origin = None
            self._multi_origin = {}
            self._rebuild_selection_artists()
            self._set_title_hint(None)
            self._refresh(moved=True)         # 面板真落位：全量重绘
            return
        if self._band_active:
            self._finish_band()
            self._set_title_hint(None)
            return
        if self._drag_resize:
            self._drag_resize = False
            ax = self._rs_ax
            if ax is not None and self._rs_target is not None:
                if self.heavy:
                    _set_bbox(ax, [self._rs_target[0], self._rs_target[1],
                                   self._rs_target[2] - self._rs_target[0],
                                   self._rs_target[3] - self._rs_target[1]])
                    self._remove_ghost()
                    self.fig.canvas.draw_idle()
                _log('[mpltweak] 缩放结束 ax%d %s -> [%.3f,%.3f,%.3f,%.3f]' % (
                    self.fig.axes.index(ax), self._rs_handle, *_bbox(ax)),
                    flush=True)
            self._rs_ax = None
            self._rs_handle = None
            self._rs_target = None
            self._rebuild_selection_artists()     # 蓝框落到最终位置
            self._set_title_hint(None)
            self._refresh(moved=True)
            return
        if not self._drag:
            return
        self._drag = False
        if self.heavy:
            _set_bbox(self._ax, self._target)
            self._remove_ghost()
            self.fig.canvas.draw_idle()
        _log('[mpltweak] 拖轴结束 ax%d -> [%.3f,%.3f,%.3f,%.3f]' % (
            self.fig.axes.index(self._ax), *_bbox(self._ax)), flush=True)
        self._ax = None
        self._target = None
        self._rebuild_selection_artists()         # 蓝框落到最终位置
        self._set_title_hint(None)
        self._refresh(moved=True)

    def _as_colorbar(self, ax):
        '''判断轴是不是 colorbar 轴，是则返回自身（多信号判定，见 _is_colorbar_ax）。'''
        return ax if _is_colorbar_ax(ax) else None

    def _build_hits(self):
        '''重绘后把**所有可命中文本/图例**的像素矩形缓存成一张表（HitMap）。

        为什么要这张表：`Text.get_window_extent()` 会触发**文字重新排版**（字体度量、
        字形 advance、必要时重排行）。鼠标每动一次都实时问一遍十几个刻度标签是纯浪费
        —— figtune 实测 hover 12.766ms → 0.0099ms。建表只在重绘之后做一次。
        '''
        hits = []
        try:
            for ax in self.fig.axes:
                iscb = _is_colorbar_ax(ax)
                for t in (getattr(ax, '_left_title', None), ax.title,
                          getattr(ax, '_right_title', None)):
                    if t is None or not t.get_text():
                        continue
                    hits.append(tuple(t.get_window_extent().bounds) + ('text', t))
                for lab, kind in ((ax.xaxis.label,
                                   'cb_label' if iscb else 'xlabel'),
                                  (ax.yaxis.label,
                                   'cb_label' if iscb else 'ylabel')):
                    if not lab.get_text():
                        continue
                    hits.append(tuple(lab.get_window_extent().bounds) + (kind, ax))
                for t in list(ax.texts):
                    if t.get_text():
                        hits.append(tuple(t.get_window_extent().bounds) + ('text', t))
                for t in list(ax.get_xticklabels()):
                    if t.get_text():
                        hits.append(tuple(t.get_window_extent().bounds)
                                    + ('cb_ticks' if iscb else 'xtick', ax))
                for t in list(ax.get_yticklabels()):
                    if t.get_text():
                        hits.append(tuple(t.get_window_extent().bounds)
                                    + ('cb_ticks' if iscb else 'ytick', ax))
                leg = ax.get_legend()
                if leg is not None:
                    hits.append(tuple(leg.get_window_extent().bounds)
                                + ('legend', ax))
            for t in list(self.fig.texts):
                if t.get_text():
                    hits.append(tuple(t.get_window_extent().bounds) + ('text', t))
        except Exception:
            pass
        self._hits = hits

    def _text_at(self, x, y):
        '''命中：鼠标下的文本元素 -> (kind, owner)。

        先查 `_build_hits()` 预先算好的矩形表（微秒级）；表里没有才走慢路径
        （colorbar 条外刻度带、轴线附近的刻度兜底带这些"非矩形"判定）。
        '''
        if not getattr(self, '_hits', None):
            self._build_hits()
        for x0, y0, x1, y1, kind, owner in self._hits:
            if x0 - 1 <= x <= x1 + 1 and y0 - 1 <= y <= y1 + 1:
                return (kind, owner)
        return self._text_at_slow(x, y)

    def _text_at_slow(self, x, y):
        '''命中检测：鼠标下的文本元素 -> (kind, owner)。kind ∈
        title/xlabel/ylabel/xtick/ytick/legend/cb_ticks/cb_label/None'''
        # 1) 图例（任意轴，最优先）
        for ax in self.fig.axes:
            leg = ax.get_legend()
            if leg is not None:
                try:
                    if leg.get_window_extent().contains(x, y):
                        return ('legend', ax)
                except Exception:
                    pass
        # 2) 标题 / 轴标签（跳过空文本——colorbar 轴的空标题/空标签不应拦截命中）
        #    ★ matplotlib 有三个标题位：ax.title（中间）、ax._left_title、ax._right_title。
        #      `set_title(..., loc='left')` 设的是 _left_title —— 只认 ax.title 会漏掉
        #      真实论文图里绝大多数"面板标题"（用户反馈"标题字号改不了"）。
        for ax in self.fig.axes:
            try:
                for t in (getattr(ax, '_left_title', None), ax.title,
                          getattr(ax, '_right_title', None)):
                    if (t is not None and t.get_text()
                            and t.get_window_extent().contains(x, y)):
                        return ('text', t)
                if ax.xaxis.label.get_text() and ax.xaxis.label.get_window_extent().contains(x, y):
                    return ('xlabel', ax)
                if ax.yaxis.label.get_text() and ax.yaxis.label.get_window_extent().contains(x, y):
                    return ('ylabel', ax)
            except Exception:
                pass
        # 2b) 任意文本：面板标注 (a)(b)、图内注记、fig.text 总标题等。
        #     真实论文图常常用 ax.text(...) 写面板号而不用 set_title，
        #     只认 ax.title 会漏掉它们（用户反馈"标题改不了字号"）。
        try:
            cands = list(self.fig.texts)
            for ax in self.fig.axes:
                cands += list(ax.texts)
            for tx in cands:
                if tx.get_text() and tx.get_window_extent().contains(x, y):
                    return ('text', tx)
        except Exception:
            pass
        # 3) colorbar：刻度标签 / colorbar 标签 / 条本身。
        #    **水平 colorbar 的刻度标签在条的下方（完全在条外）**，只判条内 bbox
        #    会漏掉（用户反馈"colorbar 刻度改不了字号"）。
        for ax in self.fig.axes:
            if self._as_colorbar(ax) is None:
                continue
            try:
                for t in (list(ax.get_xticklabels()) + list(ax.get_yticklabels())):
                    if t.get_text() and t.get_window_extent().contains(x, y):
                        return ('cb_ticks', ax)
                for lab in (ax.xaxis.label, ax.yaxis.label):
                    if lab.get_text() and lab.get_window_extent().contains(x, y):
                        return ('cb_label', ax)
            except Exception:
                pass
            bb = ax.get_position().transformed(self.fig.transFigure)
            if bb.contains(x, y):
                return ('cb_ticks', ax)
            if (bb.x0 - 14 <= x <= bb.x1 + 14
                    and bb.y0 - 14 <= y <= bb.y1 + 14):
                return ('cb_ticks', ax)
        # 4) 刻度：先逐标签命中，再按轴线附近兜底
        for ax in self.fig.axes:
            if self._as_colorbar(ax) is not None:
                continue
            bb = ax.get_position().transformed(self.fig.transFigure)
            try:
                for t in ax.get_xticklabels():
                    if t.get_window_extent().contains(x, y):
                        return ('xtick', ax)
                for t in ax.get_yticklabels():
                    if t.get_window_extent().contains(x, y):
                        return ('ytick', ax)
            except Exception:
                pass
            if bb.y0 - 14 <= y <= bb.y0 + 6 and bb.x0 <= x <= bb.x1:
                return ('xtick', ax)
            if bb.x0 - 14 <= x <= bb.x0 + 6 and bb.y0 <= y <= bb.y1:
                return ('ytick', ax)
        return (None, None)

    def _adj_font_hover(self, x, y, delta):
        '''鼠标悬停的文本元素字号 ±1（标题/x轴/y轴/刻度/图例/colorbar）。'''
        kind, owner = self._text_at(x, y)
        if owner is None:
            return
        self._push_undo_sealed(('fs', kind, id(owner)))
        try:
            if kind == 'title':
                owner.title.set_fontsize(owner.title.get_fontsize() + delta)
                _log('[mpltweak] 标题字号 -> %s' % owner.title.get_fontsize(), flush=True)
            elif kind == 'xlabel':
                owner.xaxis.label.set_fontsize(owner.xaxis.label.get_fontsize() + delta)
                _log('[mpltweak] x轴标签字号 -> %s' % owner.xaxis.label.get_fontsize(), flush=True)
            elif kind == 'ylabel':
                owner.yaxis.label.set_fontsize(owner.yaxis.label.get_fontsize() + delta)
                _log('[mpltweak] y轴标签字号 -> %s' % owner.yaxis.label.get_fontsize(), flush=True)
            elif kind == 'xtick':
                for t in owner.get_xticklabels():
                    t.set_fontsize(t.get_fontsize() + delta)
                _log('[mpltweak] x刻度字号 -> %s' % owner.get_xticklabels()[0].get_fontsize(), flush=True)
            elif kind == 'ytick':
                for t in owner.get_yticklabels():
                    t.set_fontsize(t.get_fontsize() + delta)
                _log('[mpltweak] y刻度字号 -> %s' % owner.get_yticklabels()[0].get_fontsize(), flush=True)
            elif kind == 'legend':
                _log('[mpltweak] 图例字号 -> %s' % self._adj_font(owner, 'legend', delta), flush=True)
            elif kind == 'cb_ticks':
                tl = list(owner.get_yticklabels()) + list(owner.get_xticklabels())
                for t in tl:
                    t.set_fontsize(t.get_fontsize() + delta)
                _log('[mpltweak] colorbar 刻度字号 -> %s' % (tl[0].get_fontsize() if tl else '?'),
                      flush=True)
            elif kind == 'cb_label':
                owner.yaxis.label.set_fontsize(owner.yaxis.label.get_fontsize() + delta)
                _log('[mpltweak] colorbar 标签字号 -> %s' % owner.yaxis.label.get_fontsize(), flush=True)
            elif kind == 'text':
                # 任意文本对象（面板标注/图内注记/fig.text）
                owner.set_fontsize(owner.get_fontsize() + delta)
                _log('[mpltweak] 文本字号 -> %s' % owner.get_fontsize(), flush=True)
            self._draw_now()
        except Exception as e:
            _log('[mpltweak] 字号调节失败:', e, flush=True)

    def _toggle_wireframe(self):
        '''线条模式（类似矢量软件的"轮廓/线框"视图）：只留**边框 + 坐标轴刻度 +
        文字 + 图例**，隐藏所有数据图元（海岸线、等值线、栅格、折线、散点…）。

        用途是排版阶段提速：cartopy 重图的一次全量重绘 ~810ms，几乎全花在地图要素
        上；隐藏后掉到几十毫秒，移动/缩放/框选/选中都跟着变快。再按空格恢复。
        '''
        if not self._wire:
            self._wire_saved = []
            self._atp_saved = []
            self._gl_saved = []
            try:
                from cartopy.mpl.gridliner import Gridliner
            except Exception:
                Gridliner = None
            keep = (matplotlib.spines.Spine, matplotlib.axis.Axis,
                    matplotlib.text.Text, matplotlib.legend.Legend)
            for ax in self.fig.axes:
                # cartopy 关键项：_update_title_position() 为了让标题避让经纬标注，
                # 每次重绘都会**完整绘制一遍经纬网**（实测这张图 545ms / 次）。
                # 线条模式下不需要那个避让，关掉它。
                if hasattr(ax, '_autotitlepos'):
                    self._atp_saved.append((ax, ax._autotitlepos))
                    ax._autotitlepos = False
                # Gridliner 光设 visible=False 拦不住（cartopy 会绕过去重画），
                # 必须从 artists / _children 里摘掉——它每次重绘都要重新投影
                # 整张经纬网（shapely + pyproj，实测占线条模式剩余耗时的大头）。
                if Gridliner is not None:
                    gls = [a for a in getattr(ax, 'artists', [])
                           if isinstance(a, Gridliner)]
                    if gls:
                        self._gl_saved.append((ax, gls))
                        # 只动 _children（ax.artists 是只读属性，由 _children 派生）
                        ax._children = [c for c in ax._children
                                        if not isinstance(c, Gridliner)]
                for art in list(ax.get_children()):
                    if isinstance(art, keep):
                        continue
                    try:
                        self._wire_saved.append((art, art.get_visible()))
                        art.set_visible(False)
                    except Exception:
                        pass
            self._wire = True
        else:
            for art, vis in self._wire_saved:
                try:
                    art.set_visible(vis)
                except Exception:
                    pass
            self._wire_saved = []
            for ax, gls in self._gl_saved:
                for gl in gls:
                    try:
                        ax.add_artist(gl)
                    except Exception:
                        pass
            self._gl_saved = []
            for ax, v in self._atp_saved:
                try:
                    ax._autotitlepos = v
                except Exception:
                    pass
            self._atp_saved = []
            self._wire = False
        self._remove_ghost()              # 背景失效，重抓（线条模式下很便宜）
        self._capture_bg(True)
        self._set_title_hint('线条模式 ON（空格恢复）' if self._wire else None)
        self.fig.canvas.draw_idle()

    def _nudge_size(self, key, event):
        '''方向键 = 移动面板（←→↑↓ 平移，PPT 肌肉记忆）；Shift+方向键 = 微调
        尺寸（中心不动）：Shift+↑加高 / Shift+↓减矮 / Shift+→加宽 / Shift+←减窄。

        对细长 colorbar 尤其好用——鼠标抓边很难瞄，键盘一下一下加厚最稳。
        防抖重绘：连按只在停顿 280ms 后重绘一次（重图下每次全量重绘要 ~0.8s）。
        '''
        ax = event.inaxes or getattr(self, '_last_ax', None)
        if ax is None and len(self._selected) == 1:
            ax = self._find_ax(next(iter(self._selected)))
        if ax is None:
            _log('[mpltweak] 方向键：先把鼠标移到要调的子图上')
            return
        x0, y0, w, h = _bbox(ax)
        ms = (MIN_SIZE_CB if self._as_colorbar(ax) is not None else MIN_SIZE)
        step = (NUDGE_STEP_COARSE if str(key).startswith('shift+')
                else NUDGE_STEP)
        k = str(key).split('+')[-1]
        if not str(key).startswith('shift+'):
            # 方向键 = 移动面板（←→ 平移 x、↑↓ 平移 y）
            dx = step if k == 'right' else (-step if k == 'left' else 0.0)
            dy = step if k == 'up' else (-step if k == 'down' else 0.0)
            box = [x0 + dx, y0 + dy, w, h]
        elif self.locked_map.get(id(ax), False):
            # 锁长宽比的轴（cartopy 地图 aspect='equal'）：只改一维会被重绘时的
            # apply_aspect 按比例修正回去 → 一律等比缩放
            grow = k in ('up', 'right')
            s = ((h + step) / h if grow else (h - step) / h) if h else 1.0
            s = max(s, ms / h if h else 1e-3)
            box = _resize_anchored([x0, y0, w, h], (0.5, 0.5), w * s, h * s, ms)
        elif k in ('up', 'down'):
            nh = h + (step if k == 'up' else -step)
            box = _resize_anchored([x0, y0, w, h], (0.5, 0.5), w, nh, ms)
        else:
            nw = w + (step if k == 'right' else -step)
            box = _resize_anchored([x0, y0, w, h], (0.5, 0.5), nw, h, ms)
        # undo 按"形状"合并：0.6s 内对同一轴的同一方向连续微调 = 一步（seal）
        now = time.perf_counter()
        kind_v = ('m' + k if not str(key).startswith('shift+')
                  else ('v' if k in ('up', 'down') else 'h'))
        same = (self._nudge_seal == (id(ax), kind_v)
                and now - self._nudge_seal_t < 0.6)
        if not same:
            self._push_undo()
        self._nudge_seal = (id(ax), kind_v)
        self._nudge_seal_t = now
        _set_bbox(ax, box)
        self._selected = {id(ax)}                       # 调哪个就选中哪个（蓝框反馈）
        self._rebuild_selection_artists()
        self._sync_sel(ax, box)
        W, H = self.fig.bbox.size
        self._set_title_hint('ax%d [%.3f,%.3f,%.3f,%.3f]  %.0fx%.0fpx' % (
            self.fig.axes.index(ax), box[0], box[1], box[2], box[3],
            box[2] * W, box[3] * H))
        self._schedule_draw()

    def _key_ax(self, event):
        ax = event.inaxes or getattr(self, '_last_ax', None)
        if ax is None and len(self._selected) == 1:
            ax = self._find_ax(next(iter(self._selected)))
        return ax

    @staticmethod
    def _line_lw(artist):
        try:
            v = artist.get_linewidth()
            if hasattr(v, '__len__') and not isinstance(v, str):
                v = v[0] if len(v) else 1.0
            return float(v)
        except Exception:
            return None

    @staticmethod
    def _line_color(artist):
        '''取线条当前颜色，归一成 ``'#rrggbb'``（取不到 None）。

        统一成十六进制字符串有三个好处：①可直接与调色板字符串比较；
        ②可写进 JSON 给落实环节读（元组/数组会让 json.dump 直接失败）；
        ③``set_color('#rrggbb')`` 一定合法。
        '''
        for getter in ('get_color', 'get_edgecolor'):
            try:
                v = getattr(artist, getter)()
            except Exception:
                continue
            rgba = _single_color(v)
            if rgba is None:
                continue
            try:
                return matplotlib.colors.to_hex(rgba, keep_alpha=False)
            except Exception:
                continue
        return None

    def _line_target(self, event, ax):
        '''鼠标下的线条；没有则退回当前轴的第一条可调线条。'''
        artist, owner = _line_at(self.fig, event.x, event.y)
        if artist is not None:
            return artist, owner
        if ax is not None:
            candidates = (list(getattr(ax, 'lines', []))
                          + list(getattr(ax, 'collections', [])))
            for artist in candidates:
                if not _is_line_artist(artist):
                    continue
                if self._line_lw(artist) is not None:
                    return artist, ax
        return None, ax

    def _mappable_target(self, event, ax):
        if event.inaxes is not None and self._as_colorbar(event.inaxes) is not None:
            _, mappable = _cb_mappable(event.inaxes)
            return mappable, event.inaxes
        if ax is None:
            return None, None
        candidates = list(getattr(ax, 'images', [])) + list(getattr(ax, 'collections', []))
        for artist in candidates:
            if hasattr(artist, 'get_cmap') and hasattr(artist, 'set_cmap'):
                return artist, ax
        return None, ax

    def _cycle_linewidth(self, event, delta):
        ax = self._key_ax(event)
        artist, owner = self._line_target(event, ax)
        if artist is None:
            _log('[mpltweak] 线宽：鼠标悬停在线条或先选中含线条的轴', flush=True)
            return
        old = self._line_lw(artist)
        if old is None:
            return
        self._push_undo()
        new = min(LINE_LW_MAX, max(LINE_LW_MIN, old + delta))
        try:
            artist.set_linewidth(new)
            self._set_title_hint('线宽 %.1f' % new)
            self._draw_now()
        except Exception as e:
            _log('[mpltweak] 线宽调节失败:', e, flush=True)

    def _cycle_linecolor(self, event):
        '''循环切换悬停线条的颜色（白名单内的线条类对象才生效）。'''
        ax = self._key_ax(event)
        artist, owner = self._line_target(event, ax)
        if artist is None:
            _log('[mpltweak] 颜色：鼠标悬停到线条上（plot 线 / 等值线），'
                 '或先选中含线条的轴', flush=True)
            return
        palette = []
        for c in _LINE_COLORS:
            rgba = _single_color(c)
            palette.append(matplotlib.colors.to_hex(rgba, keep_alpha=False)
                           if rgba is not None else None)
        old = self._line_color(artist)
        idx = palette.index(old) if old in palette else -1
        new = palette[(idx + 1) % len(palette)]
        self._push_undo()
        try:
            if hasattr(artist, 'set_color'):
                artist.set_color(new)
            else:
                artist.set_edgecolor(new)
            self._set_title_hint('颜色 %s' % new)
            _log('[mpltweak] 线条颜色 -> %s' % new, flush=True)
            self._bg = None
            self._hits = []
            self._draw_now()
        except Exception as e:
            self._log_color_fail(artist, e)

    @staticmethod
    def _log_color_fail(artist, exc):
        _log('[mpltweak] 颜色调节失败（%s）: %s'
             % (type(artist).__name__, exc), flush=True)

    def _cycle_cmap(self, event):
        ax = self._key_ax(event)
        artist, owner = self._mappable_target(event, ax)
        if artist is None:
            _log('[mpltweak] colormap：鼠标移到 colorbar 或含 mappable 的轴上', flush=True)
            return
        self._push_undo()
        try:
            old = artist.get_cmap().name
            try:
                idx = [_CMAP for _CMAP in _CMAPS].index(old)
            except ValueError:
                idx = -1
            new = _CMAPS[(idx + 1) % len(_CMAPS)]
            artist.set_cmap(new)
            if owner is not None and self._as_colorbar(owner) is not None:
                cb = getattr(owner, '_colorbar', None)
                if cb is not None:
                    cb.update_normal(artist)
            self._set_title_hint('colormap %s' % new)
            self._bg = None
            self._hits = []
            self._draw_now()
        except Exception as e:
            _log('[mpltweak] colormap 调节失败:', e, flush=True)

    def _toggle_grid(self, event):
        ax = self._key_ax(event)
        if ax is None:
            return
        lines = list(ax.get_xgridlines()) + list(ax.get_ygridlines())
        current = any(line.get_visible() for line in lines)
        self._push_undo()
        ax.grid(not current)
        self._set_title_hint('grid %s' % ('ON' if not current else 'OFF'))
        self._draw_now()

    def _cycle_spines(self, event):
        ax = self._key_ax(event)
        if ax is None:
            return
        states = tuple(bool(ax.spines.get(k) and ax.spines[k].get_visible())
                       for k in ('top', 'right', 'bottom', 'left'))
        if states == (True, True, True, True):
            mode = 'leftbottom'
            visible = {'left': True, 'bottom': True, 'top': False, 'right': False}
        elif states == (False, False, True, True):
            mode = 'none'
            visible = {k: False for k in ('top', 'right', 'bottom', 'left')}
        else:
            mode = 'all'
            visible = {k: True for k in ('top', 'right', 'bottom', 'left')}
        self._push_undo()
        for k, v in visible.items():
            if k in ax.spines:
                ax.spines[k].set_visible(v)
        self._set_title_hint('spines %s' % mode)
        self._draw_now()

    def _toggle_scale(self, event, axis):
        ax = self._key_ax(event)
        if ax is None:
            return
        getter = ax.get_xscale if axis == 'x' else ax.get_yscale
        setter = ax.set_xscale if axis == 'x' else ax.set_yscale
        old = getter()
        new = 'log' if old == 'linear' else 'linear'
        self._push_undo()
        try:
            setter(new)
            self._set_title_hint('%sscale %s' % (axis, new))
            self._draw_now()
        except Exception as e:
            self._undo.pop()
            _log('[mpltweak] %sscale 调节失败: %s' % (axis, e), flush=True)

    def _on_key(self, event):
        '''键位：端点拖 clim；[/]线宽、c颜色、C色标、g网格、s边框、x/y比例尺。'''
        key = event.key
        # ? = 键位浮层；Esc = 关闭浮层
        if key == '?':
            self._toggle_help()
            return
        if key == 'escape':
            _close_help_fig()
            return
        if key == '[':
            self._cycle_linewidth(event, -LINE_LW_STEP)
            return
        if key == ']':
            self._cycle_linewidth(event, LINE_LW_STEP)
            return
        if key == 'c':
            self._cycle_linecolor(event)
            return
        if key == 'C':
            self._cycle_cmap(event)
            return
        if key == 'g':
            self._toggle_grid(event)
            return
        if key == 's':
            self._cycle_spines(event)
            return
        if key in ('x', 'y'):
            self._toggle_scale(event, key)
            return
        # 方向键 = 移动面板（PPT 习惯）；Shift+方向键 = 微调尺寸（中心不动）
        if str(key).split('+')[-1] in ('up', 'down', 'left', 'right'):
            self._nudge_size(key, event)
            return
        # 空格：线条模式（只留边框/坐标轴/文字，排版时重绘快得多）
        if key in (' ', 'space'):
            self._toggle_wireframe()
            return
        if key == 'e':
            self.export()
            return
        if key in ('f', 'ctrl+f', 'ctrl+shift+f', 'ctrl+shift+F'):
            self._fit_to_content()
            return
        if key == 'n':
            self.snap = not self.snap
            _log('[mpltweak] 吸附=%s' % self.snap, flush=True)
            self._toast('吸附 %s' % ('开' if self.snap else '关'))
            self._update_status()
            self.fig.canvas.draw_idle()
            return
        # 多选对齐 / 均分（Ctrl+Shift+字母）
        _align_keys = {'ctrl+shift+l': 'left', 'ctrl+shift+r': 'right',
                       'ctrl+shift+t': 'top', 'ctrl+shift+b': 'bottom',
                       'ctrl+shift+c': 'cx', 'ctrl+shift+m': 'cy'}
        if key in _align_keys:
            self._align_selection(_align_keys[key])
            return
        if key in ('ctrl+shift+h', 'ctrl+shift+v'):
            self._distribute_selection('h' if key.endswith('h') else 'v')
            return
        # 撤销 / 重做（PPT 惯例）：Ctrl+Z 撤销；Ctrl+Y 或 Ctrl+Shift+Z 重做
        if key in ('ctrl+z', 'ctrl+Z'):
            self._undo_once()
            return
        if key in ('ctrl+y', 'ctrl+Y', 'ctrl+shift+z', 'ctrl+shift+Z'):
            self._redo_once()
            return
        # 字号：鼠标悬停哪个文本元素，+/- 就调哪个
        if key in ('+', '=', 'plus'):
            self._adj_font_hover(event.x, event.y, 1)
            return
        if key in ('-', '_', 'minus'):
            self._adj_font_hover(event.x, event.y, -1)
            return

    # ---------- 导出 ----------
    def export(self, path=None):
        '''全部子图位置 + 图例 + 字号 -> JSON（供 DSH agent 写回 .py）。'''
        # 会话接管锁：导出路径被更新的会话接管后，本会话不再导出，
        # 防止旧窗口关窗时用过期状态覆盖新会话的结果。
        if self._lock_path is not None:
            try:
                with open(self._lock_path, 'r', encoding='utf-8') as f:
                    if f.read().strip() != str(os.getpid()):
                        _log('[mpltweak] 跳过导出：会话已被新窗口接管', flush=True)
                        return
            except OSError:
                pass
        p = path or self.export_path
        axes = []
        for i, ax in enumerate(self.fig.axes):
            x0, y0, w, h = _bbox(ax)
            if self._as_colorbar(ax) is not None:
                # colorbar 刻度走 y 轴（竖条）
                tls = list(ax.get_yticklabels()) or list(ax.get_xticklabels())
                tick_fs = tls[0].get_fontsize() if tls else None
            else:
                tick_fs = (ax.get_xticklabels()[0].get_fontsize()
                           if ax.get_xticklabels() else None)
            item = {
                'index': i,
                'pos': [round(x0, 4), round(y0, 4), round(w, 4), round(h, 4)],
                'aspect_locked': self.locked_map.get(id(ax), False),
                # 三个标题位都有文字的取最大那个（真实论文图常用 loc='left'）
                'title_fontsize': _title_fs(ax),
                'label_fontsize': ax.xaxis.label.get_fontsize(),
                'tick_fontsize': tick_fs,
                # 落实时要靠这个标记决定"要不要先解除 locator/box_aspect"
                'is_colorbar': bool(_is_colorbar_ax(ax)),
                'clim': list(_clim_of(ax)) if _clim_of(ax) is not None else None,
                'xscale': ax.get_xscale(),
                'yscale': ax.get_yscale(),
                'grid': any(line.get_visible() for line in
                            list(ax.get_xgridlines()) + list(ax.get_ygridlines())),
                'spines': {k: bool(v.get_visible()) for k, v in ax.spines.items()},
                'lines': [{'index': j, 'linewidth': self._line_lw(line),
                           'color': self._line_color(line)}
                          for j, line in enumerate(getattr(ax, 'lines', []))],
            }
            mappable = None
            if _is_colorbar_ax(ax):
                _, mappable = _cb_mappable(ax)
            else:
                for artist in list(getattr(ax, 'images', [])) + list(getattr(ax, 'collections', [])):
                    if hasattr(artist, 'get_cmap'):
                        mappable = artist
                        break
            if mappable is not None:
                try:
                    item['cmap'] = mappable.get_cmap().name
                except Exception:
                    pass
            leg = ax.get_legend()
            if leg is not None:
                st = self.leg_state.get(id(ax), {})
                fs = leg.get_texts()[0].get_fontsize() if leg.get_texts() else None
                item['legend'] = {
                    'loc': st.get('loc'),
                    'anchor': st.get('anchor'),
                    'fontsize': fs,
                }
            axes.append(item)
        data = {
            'version': 3,
            'script': os.path.basename(sys.argv[0]) if sys.argv else '',
            # 画布逻辑像素尺寸（Qt get_width_height 返回逻辑 px，与显示器缩放无关）
            'figsize_px': list(self._fig_px) if self._fig_px else None,
            # 写回 figsize 的英寸数：按 100dpi 逻辑口径换算（无头 savefig dpi=100 一致）。
            # 不用 fig.dpi——高 DPI 显示器上 Qt 会把 fig.dpi 放大到 200，px/dpi 会算小一半。
            'figsize_in': ([round(self._fig_px[0] / 100.0, 2),
                            round(self._fig_px[1] / 100.0, 2)]
                           if self._fig_px else None),
            'axes': axes,
            # 多图脚本写回定位用：调的是第几张图 / 脚本共几张图（None = 未知/旧文件）
            'fig_index': self.fig_index,
            'n_figs': self.n_figs,
        }
        with open(p, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        _log('[mpltweak] 已导出 %d 个子图 -> %s' % (len(axes), p))


def gaitu(fig, export_path=None, heavy=None, quiet=False,
          fig_index=None, n_figs=None, edit_clock=None):
    '''挂到 fig 上即可交互改图（兼容旧版调用方式）。

    quiet=True：交互期不打印 [mpltweak] 日志（外部启动器 launch.py 用，
    关窗只留参数文件，界面上"什么都没发生"）。
    fig_index/n_figs：多图脚本里"调的是第几张 / 共几张"，写回按图号定位。
    edit_clock：多图会话共享的改动计数器（判断最后改的是哪张）。
    '''
    global QUIET
    QUIET = bool(quiet)
    return Tweak(fig, export_path=export_path, heavy=heavy,
                 fig_index=fig_index, n_figs=n_figs, edit_clock=edit_clock)
