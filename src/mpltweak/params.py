# -*- coding: utf-8 -*-
"""
mpltweak.params —— .tweak_params 参数 JSON 的公开规范（唯一权威定义）
=====================================================================

mpltweak 的「人肉调图 ↔ 程序写回」共用这一份文件格式：

  * 交互窗口关窗时写入（mpltweak.toolbox.Tweak.export()）
  * ``mpltweak apply`` 读取 → 打印改动清单 / 生成纯 matplotlib 片段
  * ``mpltweak apply --write`` 读取 → 把数值确定性写回脚本（AST，见 mpltweak.writeback）
  * 任何第三方工具 / agent 也可以读写这份 JSON —— 这是 agent 化的公开接口

文件位置：``<脚本目录>/.tweak_params/<脚本名>.json``（``--params`` 可覆盖）
编码：UTF-8（无 BOM）。
数值口径：位置 4 位小数；颜色统一 ``#rrggbb`` 十六进制（可 json 序列化、可直接
``set_color('#rrggbb')``）；figsize 用英寸（100dpi 逻辑口径，无头 savefig dpi=100
一致，不用 fig.dpi —— 高 DPI 显示器上 Qt 会把 fig.dpi 放大到 200）。

Schema（version = 4）
--------------------

顶层::

    {
      "version": 4,                  # int  格式版本，读方据此做兼容
      "script": "xxx.py",            # str  脚本 basename（写回定位用）
      "figsize_px": [1500, 700],     # [w, h]|null 画布逻辑像素；未 resize 过为 null
      "figsize_in": [15.0, 7.0],     # [w, h]|null 写回 figsize 的英寸数
      "fig_index": 0,                # int|null 本次调的是第几张图（0-based，顺序同
                                     #   plt.get_fignums()）——多图脚本写回按它定位
      "n_figs": 3,                   # int|null 脚本共几张图
      "axes": [ <AxisItem>, ... ]    # 每轴一项，顺序 = fig.axes 顺序
    }

AxisItem::

    {
      "index": 0,                    # int  轴在 fig.axes 中的序号（写回用 fig.axes[i] 寻址）
      "pos": [x0, y0, w, h],         # [left, bottom, width, height] figure 归一化坐标 0~1
      "aspect_locked": false,        # bool cartopy 锁长宽比（等比缩放）
      "title_fontsize": 12,          # int|None 标题字号
      "label_fontsize": 10,          # int|None x 轴标签字号
      "label_fontsize_y": 10,        # int|None y 轴标签字号；**没写就不动 y 侧**
                                     #   （T73 零兼容：不再"缺省＝与 label_fontsize 同值"）
      "tick_fontsize": 9,            # int|None 刻度字号
      "is_colorbar": false,          # bool 是否 colorbar 轴（落实前须先解除 locator/box_aspect）
      "clim": [0.0, 1.0] | null,     # [vmin, vmax] 仅含 mappable 的轴有
      "cell": [1, 2, 0, 1] | null,   # [行数, 列数, 起始行, 起始列] 网格身份
                                     #   （add_axes 手工轴为 null）
      "xlim": [-0.01, 1.01] | null,  # [xmin, xmax] 仅在脚本**显式固定**过时才记
      "ylim": [0.0, 10.0] | null,    #   见 verify._state 里的说明（autoscale 不记）
      "cmap": "viridis",             # str  mappable 的 colormap 名（有 mappable 时）
      "xscale": "linear",            # 'linear'|'log'|'symlog'|'logit'|...
      "yscale": "linear",            # 'linear'|'log'|'symlog'|'logit'|...
      "grid": true,                  # bool|缺省＝未设置（写回不得改动网格）
      "spines": {"top": true, "right": true, "bottom": true, "left": true},
      "lines": [                     # 每根线一项（index = ax.lines 序号）
        {"index": 0, "linewidth": 2.0, "color": "#d62728"}
      ],
      "legend": {"loc": "upper right",   # str|None 8 位标准位名（拖拽吸附后落此）
                 "anchor": [0.5, 0.5],   # [x, y]|None 自由锚点（未吸附时）
                 "fontsize": 10} | 缺省   # int|None
    }

版本历史
--------
v1/v2：早期内部形态（未公开）。
v3：当前公开版本——引入 ``is_colorbar``：落实 colorbar 位置/clim 前必须先
    ``set_axes_locator(None)`` + ``set_box_aspect(None)``，否则 matplotlib 每次重绘
    会用 ``_ColorbarAxesLocator`` + ``_box_aspect=20`` 把位置/宽度重置回自动值。

兼容约定（读方必须遵守）
------------------------
1. 未知键一律忽略（向前兼容，新版本加字段不影响旧读方）；
2. 缺失键用本模块的字段默认值补齐（向后兼容）；
3. ``version > SCHEMA_VERSION`` 时按"尽力读 + 警告"处理，不硬失败。
"""

from __future__ import annotations

import codecs
import json
import math
import os
from typing import Any, Dict, List, Optional, Tuple

# 当前公开格式版本
SCHEMA_VERSION = 4
# v3 → v4：AxisItem 增加可选的 ``xlim`` / ``ylim``（各 2 个数或 null）。
# 读方兼容：v3 文件照读（normalize 补 None）；v4 文件被旧工具读到时 validate 会
# 给出"version 高于当前"的警告，并按"尽力读"处理（旧工具不会写回这两个字段）。

# 顶层字段默认值（读方补齐缺失键用）
TOP_DEFAULTS: Dict[str, Any] = {
    'version': SCHEMA_VERSION,
    'script': '',
    'figsize_px': None,
    'figsize_in': None,
    'axes': [],
}

# AxisItem 字段默认值
AXIS_DEFAULTS: Dict[str, Any] = {
    'index': None,
    'pos': None,
    'aspect_locked': False,
    'title_fontsize': None,
    'label_fontsize': None,
    # x/y 轴标签字号**分开记**（T11）：GUI 能分别调 x 与 y 标签（gui-fixer 的 F3）。
    # T73（用户裁定，零兼容）：**删掉"缺失 label_fontsize_y 时按与 label_fontsize
    # 同值处理"这条回退** —— 参数里没写 y，写回就一个字都不动 y（见
    # writeback._label_fontsizes()）。当前零用户，不为旧 JSON 背兼容。
    'label_fontsize_y': None,
    'tick_fontsize': None,
    # x/y 轴**刻度**字号也分开记（T23，与 label_fontsize_y 同构）：GUI 悬停 y 刻度
    # 加号能单独调 y（T25 实测导出 `tick_fontsize=10 / tick_fontsize_y=11`），而写回
    # 过去只认单一 tick_fontsize → `tick_params(labelsize=…)` 一次把 x/y 都改掉，
    # y 的改动被吃掉。T73（零兼容）：**删掉"缺失 tick_fontsize_y 时按与 tick_fontsize
    # 同值处理"这条回退** —— 没写 y 就不写 y 侧那一行（见 writeback._tick_fontsizes()）。
    'tick_fontsize_y': None,
    'is_colorbar': False,
    'clim': None,
    'cell': None,
    'xlim': None,
    'ylim': None,
    # xscale/yscale 的默认值必须是 ``None``（＝未设置），**不能**是 'linear' ——
    # 与下面 grid 完全同一个陷阱：'linear' 与"调用方显式要求线性轴"同形，于是
    # normalize 给**每个轴**补上的 'linear' 会被写回当成目标。实测危害（L0 最终复测
    # E5，T35）：`23192393__figure_panels.py` 参数里**只有 pos**，而源码明写
    # `ax.set_xscale("log")` / `set_yscale("log")` → 判成"有目标值且不同" → 退块 →
    # 块里塞进 `set_xscale('linear')` ⇒ **对数轴被改成线性（语义被改）**；
    # `5809269__O3_FYSP_paper.py`（31 处 `plt.yscale('log')`）同款。
    # 下游一律用 `is not None` 判"有目标值"（writeback 的 ⑥b、块渲染器的 needed
    # 集合、compare），所以这里只改默认值就能让"未设置就不动"贯通原位/块/compare。
    'xscale': None,
    'yscale': None,
    # grid 的默认值必须是 ``None``（＝未设置），**不能**是 False。
    # 这是值型默认的经典陷阱：``False`` 与"用户显式写了 grid:false"完全同形，
    # 于是"参数文件里根本没有 grid 键"会被写回当成"用户要求关掉网格"——
    # 实测 `ax.grid(True)` 被改成 `ax.grid(False)`、样式表打开的网格被
    # `grid(False)` 关掉（审计 E2 / RC-3）。下游一律用 `is not None` 判"有目标值"，
    # 所以这里只改默认值就能让"未设置就不动"贯通原位 / 块 / compare 三处。
    'grid': None,
    'spines': {},
    'lines': [],
}

# pos 四元的语义（索引 0-3）
POS_LEFT, POS_BOTTOM, POS_WIDTH, POS_HEIGHT = 0, 1, 2, 3

# 轴尺度白名单（与 matplotlib 的 ``Axes.set_xscale`` 取值域一致）。
# 旧 schema 只写了 ``['linear', 'log']``，于是 ``symlog`` / ``logit`` 的
# describe 产物**校验不过**（审计 CH-5：schema 校验不了自己的产物）。
# 末尾的 ``None`` 是"未设置"，draft-07 的 enum 必须显式列出 null，
# 否则 ``xscale: null`` 会被这份 schema 自己判非法。
SCALE_ENUM = ('linear', 'log', 'symlog', 'logit', 'asinh',
              'function', 'functionlog', None)


def params_path(script: str, override: Optional[str] = None) -> str:
    """参数文件的规范路径：<脚本目录>/.tweak_params/<脚本名>.json。"""
    if override:
        return os.path.abspath(override)
    d = os.path.dirname(os.path.abspath(script))
    stem = os.path.splitext(os.path.basename(script))[0]
    return os.path.join(d, '.tweak_params', stem + '.json')


def _fill_axis(item: Dict[str, Any]) -> Dict[str, Any]:
    # 约定：``_`` 开头的键是**内部字段**（如 verify 采到的 ``_texts`` 文字盒），
    # 只在本进程里用，绝不进参数文件、不进 describe 的 agent 输出。
    # 放在 normalize 这一层是因为它是"进参数/出参数"的唯一收口。
    #
    # 值为 None 的键**不覆盖默认值**：这让"缺键"与"显式 null"都落成
    # AXIS_DEFAULTS 里的 None，也就是统一的"未设置"语义（grid 的默认值必须是
    # None 而不是 False，见 AXIS_DEFAULTS 处的说明）。
    out = dict(AXIS_DEFAULTS)
    out.update({k: v for k, v in item.items()
                if v is not None and not k.startswith('_')})
    return out


def normalize(data: Dict[str, Any]) -> Dict[str, Any]:
    """补齐缺失字段（读方入口；不抛异常，坏值保留原样交 validate 报告）。"""
    out = dict(TOP_DEFAULTS)
    out.update({k: v for k, v in data.items()
                if v is not None and not k.startswith('_')})
    out['axes'] = [_fill_axis(a) if isinstance(a, dict) else a
                   for a in (data.get('axes') or [])]
    return out


def schema() -> Dict[str, Any]:
    """生成参数文件的 JSON Schema（draft-07）。

    给 agent / 第三方工具校验自己生成的参数用，也能直接当作 function-calling
    的工具定义。

    **顶层 additionalProperties=False**（axis 层仍为 True）：这份 schema 必须能校验
    ``describe`` 自己的两种产物 —— 单图形态与 ``--all-figs`` 的
    ``{version, script, n_figs, figures:[...]}``。顶层放开时后者不在规范里，
    任何结构都能"校验通过"（审计 CH-5）。axis 层保持宽松是**故意的**：写回侧
    ``normalize`` 只挑已知键，未知轴字段继续被忽略（向后兼容约定不变）。
    """
    _num_or_null = {'type': ['number', 'null']}
    axis = {
        'type': 'object',
        'required': ['index'],
        'properties': {
            'index': {'type': ['integer', 'null']},
            'pos': {'type': ['array', 'null'], 'items': {'type': 'number'},
                    'minItems': 4, 'maxItems': 4},
            'aspect_locked': {'type': 'boolean'},
            'title_fontsize': _num_or_null,
            'label_fontsize': _num_or_null,
            'label_fontsize_y': _num_or_null,
            'tick_fontsize': _num_or_null,
            'tick_fontsize_y': _num_or_null,
            'is_colorbar': {'type': 'boolean'},
            'clim': {'type': ['array', 'null'], 'items': {'type': 'number'},
                     'minItems': 2, 'maxItems': 2},
            # 轴范围：源码里能原位改的写法有 set_xlim(a, b) / set_xlim((a, b)) /
            # set_xlim(xmin=, xmax=)；写不了就进"未原位应用"清单（--style block 可兜底）。
            'xlim': {'type': ['array', 'null'], 'items': {'type': 'number'},
                     'minItems': 2, 'maxItems': 2},
            'ylim': {'type': ['array', 'null'], 'items': {'type': 'number'},
                     'minItems': 2, 'maxItems': 2},
            # 网格身份 [行数, 列数, 起始行, 起始列]：轴按下标寻址时，
            # 它就是"这个下标在调图时代表哪个面板"的可回比凭据。
            'cell': {'type': ['array', 'null'],
                     'items': {'type': 'integer'},
                     'minItems': 4, 'maxItems': 4},
            'cmap': {'type': ['string', 'null']},
            'xscale': {'type': ['string', 'null'], 'enum': list(SCALE_ENUM)},
            'yscale': {'type': ['string', 'null'], 'enum': list(SCALE_ENUM)},
            # 允许 null：缺键 ⇒ normalize 补 None ⇒ 写回视为"未设置、别动"
            # （审计 E2/RC-3；旧 schema 只写 'boolean'，与新的默认值语义对不上）。
            'grid': {'type': ['boolean', 'null']},
            'spines': {'type': 'object',
                       'additionalProperties': {'type': 'boolean'}},
            'lines': {'type': 'array', 'items': {
                'type': 'object',
                'properties': {
                    'index': {'type': 'integer'},
                    'linewidth': _num_or_null,
                    'color': {'type': ['string', 'null']},
                }}},
            'legend': {'type': ['object', 'null'], 'properties': {
                'loc': {'type': ['string', 'null']},
                'anchor': {'type': ['array', 'null'], 'items': {'type': 'number'}},
                'fontsize': _num_or_null,
            }},
        },
        'additionalProperties': True,
    }
    _top_props = {
        'version': {'type': 'integer', 'const': SCHEMA_VERSION},
        'script': {'type': 'string'},
        'figsize_px': {'type': ['array', 'null'], 'items': {'type': 'number'},
                       'minItems': 2, 'maxItems': 2},
        'figsize_in': {'type': ['array', 'null'], 'items': {'type': 'number'},
                       'minItems': 2, 'maxItems': 2},
        'fig_index': {'type': ['integer', 'null']},
        'n_figs': {'type': ['integer', 'null']},
        'axes': {'type': 'array', 'items': axis},
    }
    # `describe --all-figs` 的产物是 {version, script, n_figs, figures:[<单图对象>]}。
    # 顶层不枚举 figures、又放开 additionalProperties 时，"结构完全不对"的文件也能
    # 校验通过（审计 CH-5）。这里把两种形态都写进 schema 并收紧顶层。
    _one_fig = {
        'type': 'object',
        'properties': dict(_top_props),
        'required': ['version'],
        'additionalProperties': False,
    }
    return {
        '$schema': 'http://json-schema.org/draft-07/schema#',
        'title': 'mpltweak params',
        'description': 'mpltweak 公开参数格式（.tweak_params/*.json）',
        'type': 'object',
        'required': ['version'],
        'properties': dict(_top_props,
                           figures={'type': 'array', 'items': _one_fig}),
        # 顶层收紧（axis 层仍是 True：未知轴字段继续忽略，兼容"未知键一律忽略"）。
        # 写回侧不看 schema，normalize 照样只挑已知键，所以收紧只影响"第三方校验"的
        # 严格度 —— 正是 CH-5 要的：schema 必须能校验自己的 describe 产物。
        'additionalProperties': False,
    }


def schema_main(argv=None) -> int:
    """``mpltweak schema``：把 JSON Schema 打到 stdout（供 agent / 工具消费）。"""
    compact = bool(argv) and '--compact' in argv
    from . import messages as _msg          # 局部导入：避免顶层循环依赖
    # 走 fd 级 UTF-8：schema 的 description 是中文，cp936 管道下会编成 GBK，
    # 按 UTF-8 解码的 agent 直接崩（README 承诺"机器可读"就该是 UTF-8）
    _msg.emit_json(schema(), indent=None if compact else 2)
    return 0


def read_text(path: str) -> Tuple[str, str]:
    """读文本文件，容忍 UTF-8-BOM 与 GBK 等常见编码。返回 ``(text, encoding)``。

    中国 Windows 上大量老脚本是 GBK（带 ``# -*- coding: gbk -*-``），还有从记事本
    存下来的 UTF-8-BOM。直接按 utf-8 打开会抛 ``UnicodeDecodeError``——
    在 CLI 层表现为裸 traceback 且 ``--json`` 下 stdout 全空（t1-J / t4-F4 / t6-H2）。

    返回探测到的编码，调用方写回时应沿用它，以免把用户的 GBK 脚本悄悄变成 UTF-8。
    需要"原文件本来有没有 BOM"时用 :func:`read_text_ex`。
    """
    text, enc, _had_bom = read_text_ex(path)
    return text, enc


def read_text_ex(path: str) -> Tuple[str, str, bool]:
    """同 :func:`read_text`，额外返回 ``had_bom``（原文件是否带 UTF-8 BOM）。

    **为什么必须把这个事实交出去**：``utf-8-sig`` 解码会吃掉 BOM，过去为了
    "别凭空加 BOM"把编码规范成 ``utf-8``，于是"原本有 BOM"这件事丢了 ——
    写回后 BOM 被**静默删除**（审计 E10：源码字节变了，超出用户请求的修改范围）。
    写回侧据此选 ``utf-8-sig`` 就能原样保留。
    """
    with open(path, 'rb') as f:
        raw = f.read()
    had_bom = raw.startswith(codecs.BOM_UTF8)
    for enc in ('utf-8-sig', 'utf-8', 'gbk', 'gb18030', 'big5'):
        try:
            text = raw.decode(enc)
        except UnicodeDecodeError:
            continue
        # 返回的编码会被调用方用于**写回**。这里必须把 utf-8-sig 规范成 utf-8：
        # Python 用 'utf-8-sig' 写入时会**自动加上 BOM**，那会把用户脚本改脏
        # （compile() 立刻报 invalid non-printable character U+FEFF）。
        # 代价是原本带 BOM 的脚本写回后不再有 BOM —— 调用方若在意，用 had_bom
        # 显式把 'utf-8-sig' 传回给 _atomic_write（writeback 就是这么做的）。
        return text, ('utf-8' if enc == 'utf-8-sig' else enc), had_bom
    raise UnicodeDecodeError('mpltweak', raw, 0, min(1, len(raw)),
                             '无法识别的文本编码（已试 utf-8-sig / utf-8 / gbk / '
                             'gb18030 / big5）')


def load(path: str) -> Dict[str, Any]:
    """读参数文件并 normalize。文件不存在/非法抛 OSError / ValueError / JSONDecodeError。

    用 read_text 兜编码：GBK 写的参数文件也要能读（t6-H2 的四路畸形输入之一）。

    T61：**记下"文件里本来有没有 version 字段"** —— ``normalize`` 会把缺失的
    version 补成当前 ``SCHEMA_VERSION``（老文件兼容所需），补完之后
    ``validate_errors`` 再也看不出"这份文件根本没写 version"。于是
    ``{"script":…, "fig_index":0, "axes":[…]}.py`` 这种残文件会一路走到
    ``no_change`` + rc=0（用户以为成功）。这里在 normalize **之后**打一个内部标记
    （``_`` 前缀：normalize 会把输入里的 ``_`` 键丢掉，所以外部伪造不了），
    由 ``validate_errors`` 译成一条硬错误。
    """
    text, _enc = read_text(path)
    raw = json.loads(text)
    out = normalize(raw)
    if not isinstance(raw, dict) or raw.get('version') is None:
        out['_version_missing'] = True
    return out


def dump(data: Dict[str, Any], path: str) -> None:
    """写参数文件（UTF-8、缩进 2、保留中文）。

    NaN / Infinity 一律转成 null —— 它们是 Python json 的私有扩展，严格的
    JSON 解析器（多数语言 / jsonschema 校验器）会直接拒收（t5-S6 的 low 项）。
    """
    def _clean(v):
        if isinstance(v, float):
            return None if (v != v or v in (float('inf'), float('-inf'))) else v
        if isinstance(v, dict):
            return {k: _clean(x) for k, x in v.items()}
        if isinstance(v, list):
            return [_clean(x) for x in v]
        return v

    with open(path, 'w', encoding='utf-8') as f:
        json.dump(_clean(normalize(data)), f, ensure_ascii=False, indent=2)


def _is_num(v: Any) -> bool:
    """是否是**可用**的数值：bool 不算（``True`` 会被 ``float()`` 收下但不是本意），
    NaN / ±Inf 也不算（下沉到 __int__/文本化时必炸或写出非法 JSON）。"""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return False
    try:
        return math.isfinite(float(v))
    except (TypeError, ValueError, OverflowError):
        return False


def _num_list_problem(v: Any, n: int, what: str) -> Optional[str]:
    """``v`` 必须是 n 个可用数值的数组；否则返回一句指名道姓的问题。"""
    if v is None:
        return None
    if not isinstance(v, (list, tuple)):
        return '%s 必须是 %d 个数字的数组，实际是 %r' % (what, n, v)
    if len(v) != n:
        return '%s 必须是 %d 个数字（实际 %d 个）: %r' % (what, n, len(v), v)
    for x in v:
        if not _is_num(x):
            return '%s 含非数值/非法数值元素: %r' % (what, x)
    return None


# ---------------------------------------------------------------------------
# T61：坏参数 fail-closed —— 逐条判据都拆成**独立小函数**，便于单测与反向验证
# （反向 = 把整个函数体换成 ``return None``，只有一处锚点，不会退化成死代码）。
# ---------------------------------------------------------------------------
def _version_problem(data: Dict[str, Any]) -> Optional[str]:
    """version 必须存在且**等于**当前规范（T61）。

    过去只查"是不是整数"：``version: 1`` 一路放行，而 ``version`` 缺失时
    ``normalize`` 会静默补成当前值 —— 两种残文件最后都落到 ``no_change`` + rc=0，
    用户以为"写回成功了"。
    """
    if data.get('_version_missing'):
        return ('参数文件缺少 version 字段（本规范要求 version=%d）—— 这份文件'
                '可能不是本工具导出的，或被人手改掉了版本号；拒写。'
                '请用 `mpltweak describe <脚本>` 重新导出。' % SCHEMA_VERSION)
    ver = data.get('version')
    if isinstance(ver, bool) or not isinstance(ver, int):
        return 'version 缺失或非整数: %r' % (ver,)
    if ver != SCHEMA_VERSION:
        return ('version=%d 与当前规范 %d 不符（旧版/未知版参数一律拒写，'
                '以免按错误语义改代码；请用 `mpltweak describe <脚本>` 重新导出）'
                % (ver, SCHEMA_VERSION))
    return None


def _size_problem(v: Any, what: str) -> Optional[str]:
    """尺寸类字段（pos 的 w/h、figsize 的每一维）必须 > 0（T61）。

    零/负尺寸写进源码后：轴不可见或画布为负 —— matplotlib 不一定报错
    （``figsize=(0,0)`` 甚至能跑完），但出来的图是废的，而 CLI 报 ok。
    NaN/inf 由 :func:`_is_num` 挡（更早一层）。
    """
    if not isinstance(v, (int, float)) or isinstance(v, bool):
        return None
    if v <= 0:
        return '%s 必须 > 0，实际是 %r' % (what, v)
    return None


def _pos_problem(pos: Any, what: str) -> Optional[str]:
    """``pos`` 的落地合法性（T61）。

    拒绝：w <= 0 / h <= 0（零或负尺寸面板）；**整块完全落在画布外**
    （``left+w <= 0`` / ``bottom+h <= 0`` / ``left >= 1`` / ``bottom >= 1``）——
    那种面板用户在图上一个像素都看不到，写进源码等于把面板改没了。

    **不拒**（口径写死）：left/bottom 为负但仍有可见部分 —— matplotlib 允许轴
    部分位于画布外（这是常见做法）；只因为"出现负号"就拒绝是误伤。
    """
    if pos is None:
        return None
    # 结构/数值性问题由 _num_list_problem 报（这里是**数值语义**判据，
    # 长度不对或元素非数值时直接放过，免得替它报第二遍或自己炸 IndexError）
    if not isinstance(pos, (list, tuple)) or len(pos) != 4:
        return None
    for x in pos:
        if not _is_num(x):
            return None
    p = _size_problem(pos[2], '%s[2]（width）' % what)
    if p:
        return p
    p = _size_problem(pos[3], '%s[3]（height）' % what)
    if p:
        return p
    left, bottom, w, h = (float(pos[0]), float(pos[1]), float(pos[2]),
                          float(pos[3]))
    if left + w <= 0 or bottom + h <= 0 or left >= 1 or bottom >= 1:
        return ('%s 整块都在画布外（left=%g bottom=%g w=%g h=%g）：写进源码后'
                '这块面板在图上看不到（left/bottom 为负但**有可见部分**是允许的，'
                '这里拒的是完全跑到画布外）'
                % (what, left, bottom, w, h))
    return None


def _figsize_problem(data: Dict[str, Any]) -> Optional[str]:
    """``figsize_in`` / ``figsize_px`` 每一维都必须 > 0（T61）。"""
    for key in ('figsize_in', 'figsize_px'):
        v = data.get(key)
        if not isinstance(v, (list, tuple)) or len(v) != 2:
            continue                      # 结构问题由 _num_list_problem 报
        for k, dim in enumerate(v):
            p = _size_problem(dim, '%s[%d]' % (key, k))
            if p:
                return p
    return None


def _fontsize_problem(v: Any, what: str) -> Optional[str]:
    """字号必须 >= 0（T61）。``0`` 合法（matplotlib 允许，只是看不见字），
    只在 :func:`validate` 里给一条提示；负字号直接拒（渲染异常/无意义）。"""
    if v is None or isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    if v < 0:
        return '%s 不能为负（实际 %r）：matplotlib 渲染异常' % (what, v)
    return None


def _axis_index_problem(idx: Any, what: str) -> Optional[str]:
    """``axes[].index`` 必须是非负整数（T61）。

    **负数必须拒**：schema 是 0 基，而 ``-1`` 在 Python 里是"最后一张" ——
    参数里的 -1 会被静默写到**另一张轴**上（对象错，用户看不出来）。
    上限（``index >= n_axes``）需要脚本才能判定，放在 writeback 的落地入口，
    用"图内可寻址的轴槽数 / 参数自报的轴数"两者取大做保守下界。
    """
    if idx is None or isinstance(idx, bool) or not isinstance(idx, int):
        return None                        # 类型问题由上面那条报
    if idx < 0:
        return '%s 为负（%d）：轴序号是 0 基，负数会被 Python 当成"最后一张"，'\
               '静默写到别的轴上' % (what, idx)
    return None


def validate_errors(data: Dict[str, Any]) -> List[str]:
    """**硬错误**（error 级）：语义无法安全落地的输入 —— 调用方必须拒写（rc=2）。

    与 :func:`validate` 的分工：那边是"全部问题（含提醒）"，这边只挑"必须拦住"的。
    审计 E6：这些输入过去一路走到下游才崩成 ``internal_error``
    （``float('abc')`` / ``%d`` 格式化字符串 / ``pos[3]`` 越界 / ``int(nan)``），
    而 ``params.validate`` 明明已经看出问题了，却只 print 一句就继续跑。
    """
    errs: List[str] = []
    if not isinstance(data, dict):
        return ['顶层不是对象: %r' % (data,)]
    # T61：version 必须存在且等于当前规范
    p = _version_problem(data)
    if p:
        errs.append(p)
    for key, n in (('figsize_in', 2), ('figsize_px', 2)):
        p = _num_list_problem(data.get(key), n, key)
        if p:
            errs.append(p)
    # T61：画布尺寸每一维必须 > 0（零/负尺寸 = 废图，但 matplotlib 不报错）
    p = _figsize_problem(data)
    if p:
        errs.append(p)
    axes = data.get('axes')
    if axes is None:
        axes = []
    if not isinstance(axes, list):
        errs.append('axes 必须是数组，实际是 %s' % type(axes).__name__)
        return errs
    for i, a in enumerate(axes):
        if not isinstance(a, dict):
            errs.append('axes[%d] 不是对象: %r' % (i, a))
            continue
        idx = a.get('index')
        if isinstance(idx, bool) or not isinstance(idx, int):
            errs.append('axes[%d].index 必须是整数，实际是 %r' % (i, idx))
        # T61：负数轴序号（-1 = Python 的最后一张）会被静默写到别的轴上
        p = _axis_index_problem(idx, 'axes[%d].index' % i)
        if p:
            errs.append(p)
        for f in ('pos', 'xlim', 'ylim'):
            p = _num_list_problem(a.get(f), 4 if f == 'pos' else 2,
                                  'axes[%d].%s' % (i, f))
            if p:
                errs.append(p)
        # T61：pos 的尺寸必须 > 0，且不得整块跑到画布外（left/bottom 为负不拒）
        p = _pos_problem(a.get('pos'), 'axes[%d].pos' % i)
        if p:
            errs.append(p)
        p = _num_list_problem(a.get('clim'), 2, 'axes[%d].clim' % i)
        if p:
            errs.append(p)
        for f in ('title_fontsize', 'label_fontsize', 'label_fontsize_y',
                  'tick_fontsize', 'tick_fontsize_y'):
            v = a.get(f)
            if v is not None and not _is_num(v):
                errs.append('axes[%d].%s 必须是数字或 null，实际是 %r' % (i, f, v))
                continue
            # T61：负字号拒写（0 合法，只在 validate 里提示）
            p = _fontsize_problem(v, 'axes[%d].%s' % (i, f))
            if p:
                errs.append(p)
        g = a.get('grid')
        if g is not None and not isinstance(g, bool):
            errs.append('axes[%d].grid 必须是布尔或 null，实际是 %r' % (i, g))
        for f in ('xscale', 'yscale'):
            v = a.get(f)
            if v is not None and (not isinstance(v, str) or v not in SCALE_ENUM):
                errs.append('axes[%d].%s 不是支持的轴尺度: %r（允许 %s）'
                            % (i, f, v,
                               '、'.join(str(x) for x in SCALE_ENUM if x)))
        lines = a.get('lines')
        if lines is not None and not isinstance(lines, list):
            errs.append('axes[%d].lines 必须是数组，实际是 %s'
                        % (i, type(lines).__name__))
        else:
            for j, ln in enumerate(lines or []):
                if not isinstance(ln, dict):
                    errs.append('axes[%d].lines[%d] 不是对象: %r' % (i, j, ln))
                    continue
                lw = ln.get('linewidth')
                if lw is not None and not _is_num(lw):
                    errs.append('axes[%d].lines[%d].linewidth 必须是数字或 null，'
                                '实际是 %r' % (i, j, lw))
        lg = a.get('legend')
        if lg is not None:
            if not isinstance(lg, dict):
                errs.append('axes[%d].legend 必须是对象或 null: %r' % (i, lg))
            else:
                fs = lg.get('fontsize')
                if fs is not None and not _is_num(fs):
                    errs.append('axes[%d].legend.fontsize 必须是数字或 null，'
                                '实际是 %r' % (i, fs))
                else:
                    p = _fontsize_problem(fs, 'axes[%d].legend.fontsize' % i)
                    if p:
                        errs.append(p)
                loc = lg.get('loc')
                if loc is not None and not isinstance(loc, str):
                    errs.append('axes[%d].legend.loc 必须是字符串或 null，'
                                '实际是 %r' % (i, loc))
        sp = a.get('spines')
        if sp is not None and not isinstance(sp, dict):
            errs.append('axes[%d].spines 必须是对象: %r' % (i, sp))
    return errs


def validate(data: Dict[str, Any]) -> List[str]:
    """结构校验：返回问题列表（空 = 合规）。含无法落地的硬错误与值得提醒的软问题。

    只报结构问题，不判数值好坏。要"必须拒写"的那一档用 :func:`validate_errors`。
    """
    problems: List[str] = validate_errors(data)
    if not isinstance(data, dict):
        return problems
    ver = data.get('version')
    if isinstance(ver, int) and not isinstance(ver, bool) and ver > SCHEMA_VERSION:
        problems.append('version=%d 高于当前 %d，按尽力读处理' % (ver, SCHEMA_VERSION))
    # T61：fontsize == 0 合法（matplotlib 允许，只是那处文字看不见）→ 只提示，不拒写
    for i, a in enumerate(data.get('axes') or []):
        if not isinstance(a, dict):
            continue
        for f in ('title_fontsize', 'label_fontsize', 'label_fontsize_y',
                  'tick_fontsize', 'tick_fontsize_y'):
            if a.get(f) == 0 and not isinstance(a.get(f), bool):
                problems.append('axes[%d].%s = 0：这处文字会看不见'
                                '（matplotlib 允许，故不拒写）' % (i, f))
        lg = a.get('legend')
        if isinstance(lg, dict) and lg.get('fontsize') == 0 \
                and not isinstance(lg.get('fontsize'), bool):
            problems.append('axes[%d].legend.fontsize = 0：图例文字会看不见'
                            '（matplotlib 允许，故不拒写）' % i)
    return problems


def defaults() -> Dict[str, Any]:
    """空骨架（构造新参数文件用）。"""
    return dict(TOP_DEFAULTS)
