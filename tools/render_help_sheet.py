# -*- coding: utf-8 -*-
"""渲染 ? 键位表高清 PNG 资源（zh/en）到 src/mpltweak/resources/。
构建期工具：本机有微软雅黑等字体，渲染成图后运行时零字体依赖（figimage 显示像素）。

用法：python tools/render_help_sheet.py
"""
import os
import sys

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, '..', 'src'))
from mpltweak import toolbox as tb            # noqa: E402

_SCALE = 2                                  # 2x 高清（窗口 1x 显示时清晰）
_OUT = os.path.join(_HERE, '..', 'src', 'mpltweak', 'resources')


def main():
    os.makedirs(_OUT, exist_ok=True)
    font = tb._pick_ui_font()
    print('UI font:', font)
    for lang, name in (('zh', 'help_zh.png'), ('en', 'help_en.png')):
        _f = plt.figure(figsize=(7.2, 8.8), dpi=110 * _SCALE)
        _f.patch.set_facecolor('#F6F7F9')
        tb._render_help(_f, lang, font)
        _p = os.path.join(_OUT, name)
        _f.savefig(_p, dpi=110 * _SCALE, facecolor='#F6F7F9')
        plt.close(_f)
        print('saved %s (%dx%d)' % (_p, 792 * _SCALE, 968 * _SCALE))


if __name__ == '__main__':
    main()
