# -*- coding: utf-8 -*-
"""mpltweak 写回演示用脚本：初始布局是"随手写的 add_axes"——
左列三个面板左边缘参差（0.080 / 0.115 / 0.048）、间距不均，
第四个面板与它们也不齐。调图后由 mpltweak 原位写回整齐的坐标。
"""
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

fig = plt.figure(figsize=(10, 8))
_x = np.linspace(0, 1, 120)

_ax1 = fig.add_axes([0.048, 0.655, 0.3, 0.215])
_ax1.plot(_x, np.sin(6 * _x), color='#0F4D92', lw=1.2)
_ax1.set_title('(a)', fontsize=9)
_ax1.set_xlabel('t', fontsize=8)

_ax2 = fig.add_axes([0.048, 0.37, 0.3, 0.215])
_ax2.hist(np.random.default_rng(1).normal(size=300), bins=20,
          color='#699ECA', edgecolor='white', linewidth=0.4)
_ax2.set_title('(b)', fontsize=9)

_ax3 = fig.add_axes([0.048, 0.085, 0.3, 0.215])
_ax3.scatter(np.random.default_rng(2).normal(size=80),
             np.random.default_rng(3).normal(size=80), s=8, color='#0C9B82')
_ax3.set_title('(c)', fontsize=9)

_ax4 = fig.add_axes([0.455, 0.56, 0.43, 0.31])
for _i in range(4):
    _ax4.plot(_x, np.sin((_i + 2) * _x) * (1 - 0.15 * _i))
_ax4.set_title('(d)', fontsize=9)
_ax4.legend(['run 1', 'run 2', 'run 3', 'run 4'], fontsize=7, frameon=False)

fig.savefig('messy_layout.png', dpi=100)
