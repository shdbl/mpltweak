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

Schema（version = 3）
--------------------

顶层::

    {
      "version": 3,                  # int  格式版本，读方据此做兼容
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
from typing import Any, Dict, List, Optional

# 当前公开格式版本
SCHEMA_VERSION = 3

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
    out = dict(AXIS_DEFAULTS)
    out.update({k: v for k, v in item.items() if v is not None})
    return out


def normalize(data: Dict[str, Any]) -> Dict[str, Any]:
    """补齐缺失字段（读方入口；不抛异常，坏值保留原样交 validate 报告）。"""
    out = dict(TOP_DEFAULTS)
    out.update({k: v for k, v in data.items() if v is not None})
    out['axes'] = [_fill_axis(a) if isinstance(a, dict) else a
                   for a in (data.get('axes') or [])]
    return out


def load(path: str) -> Dict[str, Any]:
    """读参数文件并 normalize。文件不存在/非法抛 OSError / ValueError。"""
    with open(path, 'r', encoding='utf-8-sig') as f:
        raw = json.load(f)
    return normalize(raw)


def dump(data: Dict[str, Any], path: str) -> None:
    """写参数文件（UTF-8、缩进 2、保留中文）。"""
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(normalize(data), f, ensure_ascii=False, indent=2)


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
