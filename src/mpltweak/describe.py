# -*- coding: utf-8 -*-
"""mpltweak describe —— 不开窗，把脚本当前的排版状态导出成参数 JSON。

这是给 **AI / agent** 的"读"侧接口：agent 不必看懂图，只要读这份 JSON，
改几个数字，再用 ``mpltweak apply --write`` 落实回源码 —— 读 → 改 → 写 闭环。

    mpltweak describe fig1.py                 # 最后一张图 → 参数 JSON（stdout）
    mpltweak describe fig1.py --fig 0         # 指定第 0 张
    mpltweak describe fig1.py --all-figs      # 多图脚本：一次导出全部
    mpltweak describe fig1.py -o layout.json  # 写文件（默认 stdout）

产物直接符合 .tweak_params 公开规范（version 3），可以原样喂给 apply。
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from . import messages as _msg
from . import params, verify


def _to_params(state, script):
    """把 verify 采集到的图状态整理成参数文件格式（version 3）。"""
    out = {
        'version': params.SCHEMA_VERSION,
        'script': os.path.basename(script),
        'figsize_px': state.get('figsize_px'),
        'figsize_in': state.get('figsize_in'),
    }
    if out['figsize_in'] is None:
        # 兜底：采集侧没给英寸数时按 100dpi 逻辑口径换算（高 DPI 下可能偏）
        px = state.get('figsize_px')
        if px:
            out['figsize_in'] = [round(px[0] / 100.0, 4), round(px[1] / 100.0, 4)]
    if isinstance(state.get('fig_index'), int):
        out['fig_index'] = state['fig_index']
        out['n_figs'] = state.get('n_figs')
    axes = []
    for it in state.get('axes') or []:
        ax = {
            'index': it.get('index'),
            'pos': it.get('pos'),
            'aspect_locked': it.get('aspect') not in (None, 'auto'),
            'title_fontsize': it.get('title_fontsize'),
            'label_fontsize': it.get('label_fontsize'),
            'tick_fontsize': it.get('tick_fontsize'),
            'is_colorbar': bool(it.get('is_colorbar')),
            'clim': it.get('clim'),
            'xscale': it.get('xscale', 'linear'),
            'yscale': it.get('yscale', 'linear'),
            'grid': bool(it.get('grid')),
            'spines': it.get('spines') or {},
            'lines': [],
        }
        axes.append(ax)
    out['axes'] = axes
    return out


@_msg.guard_json_main
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog='mpltweak describe',
        description='不开窗导出脚本当前的排版（参数 JSON，符合 mpltweak 公开规范）')
    ap.add_argument('script', help='要导出的绘图脚本')
    ap.add_argument('--fig', type=int, default=-1,
                    help='第几张图（0-based；默认最后一张）')
    ap.add_argument('--all-figs', action='store_true',
                    help='多图脚本：导出全部图（结果为 {"figures": [...]}）')
    ap.add_argument('-o', '--output', default=None,
                    help='输出文件（默认打印到 stdout）')
    ap.add_argument('--compact', action='store_true',
                    help='紧凑 JSON（不缩进；给程序读时省 token）')
    args = ap.parse_args(argv if argv is not None else sys.argv[1:])

    # 脚本自己的 print 不能混进 stdout —— 采集期间把 stdout 让给 stderr，
    # 这样 `mpltweak describe ... > layout.json` 拿到的就是干净的 JSON。
    _real_stdout = sys.stdout
    sys.stdout = sys.stderr

    script = os.path.abspath(args.script)
    if not os.path.exists(script):
        sys.stdout = _real_stdout           # 早退也要把 stdout 还回去
        sys.stderr.write('[describe] 找不到脚本: %s\n' % script)
        _msg.fail_json('file_not_found', 'script not found: %s' % script)
        return 1

    if args.all_figs:
        states, err = verify.collect_all(script)
        if states is None:
            sys.stderr.write('[describe] 采集失败: %s\n' % err)
            _msg.fail_json('collect_failed', err)
            return 1
        payload = {
            'version': params.SCHEMA_VERSION,
            'script': os.path.basename(script),
            'n_figs': len(states),
            'figures': [_to_params(s, script) for s in states],
        }
    else:
        state, err = verify.collect(script, args.fig if args.fig >= 0 else None)
        if state is None:
            sys.stderr.write('[describe] 采集失败: %s\n' % err)
            _msg.fail_json('collect_failed', err)
            return 1
        payload = _to_params(state, script)

    sys.stdout = _real_stdout
    text = json.dumps(payload, ensure_ascii=False,
                      indent=None if args.compact else 2)
    if args.output:
        with open(args.output, 'w', encoding='utf-8') as f:
            f.write(text + '\n')
        sys.stderr.write('[describe] 已写入 %s\n' % args.output)
    else:
        sys.stdout.write(text + '\n')
    return 0


if __name__ == '__main__':
    sys.exit(main())
