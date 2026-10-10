# -*- coding: utf-8 -*-
"""
mpltweak —— 交互式 matplotlib 调图（脚本零侵入）

产品形态：**库内核 + CLI 外挂入口**。
  * 用户/agent 只接触 CLI：``mpltweak <script.py>`` 开窗调图，``mpltweak apply [--write]`` 写回；
  * 本包是内核（toolbox / params / writeback），可被其它工具 import 嵌入；
  * ``mpltweak.toolbox.gaitu(fig, ...)`` 是嵌入 API（Jupyter / 宿主进程内直接用），
    文档明确标注：走它 = 在脚本里加一行 import，会破坏"脚本零侵入"，仅嵌入场景用。

用户脚本里不出现本工具的任何痕迹；写回产出是纯 matplotlib 代码。
"""

from .params import (
    SCHEMA_VERSION,
    defaults,
    dump,
    load,
    normalize,
    params_path,
    validate,
)

__version__ = '0.1.8'          # 必须与 pyproject.toml 的 version 保持一致
__all__ = [
    'SCHEMA_VERSION',
    'defaults',
    'dump',
    'load',
    'normalize',
    'params_path',
    'validate',
]
