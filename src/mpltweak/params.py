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
      "label_fontsize": 10,          # int|None x/y 轴标签字号
      "tick_fontsize": 9,            # int|None 刻度字号
      "is_colorbar": false,          # bool 是否 colorbar 轴（落实前须先解除 locator/box_aspect）
      "clim": [0.0, 1.0] | null,     # [vmin, vmax] 仅含 mappable 的轴有
      "cell": [1, 2, 0, 1] | null,   # [行数, 列数, 起始行, 起始列] 网格身份
                                     #   （add_axes 手工轴为 null）
      "xlim": [-0.01, 1.01] | null,  # [xmin, xmax] 仅在脚本**显式固定**过时才记
      "ylim": [0.0, 10.0] | null,    #   见 verify._state 里的说明（autoscale 不记）
      "cmap": "viridis",             # str  mappable 的 colormap 名（有 mappable 时）
      "xscale": "linear",            # 'linear'|'log'
      "yscale": "linear",            # 'linear'|'log'
      "grid": true,                  # bool
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

import json
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
    'tick_fontsize': None,
    'is_colorbar': False,
    'clim': None,
    'cell': None,
    'xlim': None,
    'ylim': None,
    'xscale': 'linear',
    'yscale': 'linear',
    'grid': False,
    'spines': {},
    'lines': [],
}

# pos 四元的语义（索引 0-3）
POS_LEFT, POS_BOTTOM, POS_WIDTH, POS_HEIGHT = 0, 1, 2, 3


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
    的工具定义。注意 additionalProperties=True —— 与「未知键一律忽略」的兼容
    约定一致（新版本加字段不会让旧校验失败）。
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
            'tick_fontsize': _num_or_null,
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
            'xscale': {'type': 'string', 'enum': ['linear', 'log']},
            'yscale': {'type': 'string', 'enum': ['linear', 'log']},
            'grid': {'type': 'boolean'},
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
    return {
        '$schema': 'http://json-schema.org/draft-07/schema#',
        'title': 'mpltweak params',
        'description': 'mpltweak 公开参数格式（.tweak_params/*.json）',
        'type': 'object',
        'required': ['version'],
        'properties': {
            'version': {'type': 'integer', 'const': SCHEMA_VERSION},
            'script': {'type': 'string'},
            'figsize_px': {'type': ['array', 'null'], 'items': {'type': 'number'},
                           'minItems': 2, 'maxItems': 2},
            'figsize_in': {'type': ['array', 'null'], 'items': {'type': 'number'},
                           'minItems': 2, 'maxItems': 2},
            'fig_index': {'type': ['integer', 'null']},
            'n_figs': {'type': ['integer', 'null']},
            'axes': {'type': 'array', 'items': axis},
        },
        'additionalProperties': True,
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
    """
    with open(path, 'rb') as f:
        raw = f.read()
    for enc in ('utf-8-sig', 'utf-8', 'gbk', 'gb18030', 'big5'):
        try:
            text = raw.decode(enc)
        except UnicodeDecodeError:
            continue
        # 返回的编码会被调用方用于**写回**。这里必须把 utf-8-sig 规范成 utf-8：
        # Python 用 'utf-8-sig' 写入时会**自动加上 BOM**，那会把用户脚本改脏
        # （compile() 立刻报 invalid non-printable character U+FEFF）。
        # 代价是原本带 BOM 的脚本写回后不再有 BOM —— 无害（Python 3 默认 UTF-8，
        # 而读的时候 utf-8-sig 照样认）。
        return text, ('utf-8' if enc == 'utf-8-sig' else enc)
    raise UnicodeDecodeError('mpltweak', raw, 0, min(1, len(raw)),
                             '无法识别的文本编码（已试 utf-8-sig / utf-8 / gbk / '
                             'gb18030 / big5）')


def load(path: str) -> Dict[str, Any]:
    """读参数文件并 normalize。文件不存在/非法抛 OSError / ValueError / JSONDecodeError。

    用 read_text 兜编码：GBK 写的参数文件也要能读（t6-H2 的四路畸形输入之一）。
    """
    text, _enc = read_text(path)
    return normalize(json.loads(text))


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


def validate(data: Dict[str, Any]) -> List[str]:
    """结构校验：返回问题列表（空 = 合规）。只报结构问题，不判数值好坏。"""
    problems: List[str] = []
    if not isinstance(data, dict):
        return ['顶层不是对象']
    ver = data.get('version')
    if not isinstance(ver, int):
        problems.append('version 缺失或非整数')
    elif ver > SCHEMA_VERSION:
        problems.append('version=%d 高于当前 %d，按尽力读处理' % (ver, SCHEMA_VERSION))
    for a in data.get('axes', []):
        if not isinstance(a, dict):
            problems.append('axes 含非对象项: %r' % (a,))
            continue
        i = a.get('index')
        if not isinstance(i, int):
            problems.append('axis 缺 index: %r' % (a,))
        pos = a.get('pos')
        if pos is not None and (not isinstance(pos, (list, tuple)) or len(pos) != 4):
            problems.append('ax%s 的 pos 不是 4 元组' % (i,))
    return problems


def defaults() -> Dict[str, Any]:
    """空骨架（构造新参数文件用）。"""
    return dict(TOP_DEFAULTS)
