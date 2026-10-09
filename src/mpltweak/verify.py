# -*- coding: utf-8 -*-
"""
mpltweak.verify —— 语义验证：写回后重跑脚本，dump **目标图的真实状态**并与参数逐项比对
=======================================================================================

"能跑通"（exit 0）只证明写回没把脚本改坏，**不能证明布局真的落在了你要调的那张图上**。
本模块补上这一环：在一个干净子进程里重跑写回后的脚本 → 抓取目标图（按
``fig_index``，与 launch 选图口径一致）→ 把位置/字号/grid/spines/clim 与参数 JSON
逐项比对。

判定：
  * 全部一致            → ``(True, '')``
  * 有不一致项          → ``(False, '明细')`` —— 调用方据此换锚点重试
  * 取不到图状态        → ``(None, '原因')`` —— **不否决**（脚本可能存完图就 close，
                          或需要数据/依赖跑不起来；此时退回"仅按能跑通判定"）

取数口径与 ``toolbox.export()`` 完全一致（都用 toolbox 的 ``_bbox``/``_title_fs``/
``_clim_of``），保证比对的是同一套定义。

子进程入口：``python -m mpltweak.verify <script> <out.json> [fig_index]``
"""

from __future__ import annotations

import json
import os
import runpy
import subprocess
import sys
from typing import Any, Dict, List, Optional, Tuple

POS_TOL = 2e-3        # 位置容差（figure 比例，参数存 4 位小数）
FS_TOL = 0.51         # 字号容差（磅）
CLIM_REL_TOL = 0.01   # clim 容差（相对量程）


def _state(fig) -> Dict[str, Any]:
    """抽取图形的可比对状态（口径同 toolbox.export）。"""
    from .toolbox import _bbox, _clim_of, _title_fs
    axes = []
    for i, ax in enumerate(fig.axes):
        x0, y0, w, h = _bbox(ax)
        item = {
            'index': i,
            'pos': [round(x0, 4), round(y0, 4), round(w, 4), round(h, 4)],
            'title_fontsize': _title_fs(ax),
            'label_fontsize': ax.xaxis.label.get_fontsize(),
            'label_fontsize_y': ax.yaxis.label.get_fontsize(),
            'grid': any(line.get_visible() for line in
                        list(ax.get_xgridlines()) + list(ax.get_ygridlines())),
            'spines': {k: bool(v.get_visible()) for k, v in ax.spines.items()},
        }
        tls = ax.get_xticklabels()
        if tls:
            item['tick_fontsize'] = tls[0].get_fontsize()
        try:
            item['aspect'] = ax.get_aspect()      # 'auto' / 'equal' / 数值
        except Exception:                         # noqa: BLE001
            item['aspect'] = None
        clim = _clim_of(ax)
        if clim is not None:
            item['clim'] = [round(float(clim[0]), 6), round(float(clim[1]), 6)]
        axes.append(item)
    try:
        px = list(fig.canvas.get_width_height())
    except Exception:                            # noqa: BLE001
        px = None
    return {'figsize_px': px, 'axes': axes}


def collect(script: str, fig_index: Optional[int] = None) -> Tuple[Optional[Dict], str]:
    """在当前进程跑脚本并抽取目标图状态。返回 (state|None, err)。

    与 launch 一样拦掉 plt.show / plt.close / matplotlib.use / switch_backend，
    并以脚本所在目录为 cwd（脚本里的相对路径/存图位置才和平时一致）。
    """
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.switch_backend('Agg')       # 真初始化后端，之后再拦（否则 pyplot 内部取不到）

    real = (plt.show, plt.close, matplotlib.use, plt.switch_backend)
    plt.show = lambda *a, **k: None
    plt.close = lambda *a, **k: None
    matplotlib.use = lambda *a, **k: None
    plt.switch_backend = lambda *a, **k: None
    d = os.path.dirname(os.path.abspath(script))
    old = os.getcwd()
    os.chdir(d)
    if d not in sys.path:
        sys.path.insert(0, d)
    try:
        runpy.run_path(script, run_name='__main__')
        nums = plt.get_fignums()
        if not nums:
            return None, '脚本没有留下任何图（可能存完图就 close 了）'
        if isinstance(fig_index, int) and 0 <= fig_index < len(nums):
            num = nums[fig_index]
        else:
            num = nums[-1]
        return _state(plt.figure(num)), ''
    except Exception as e:                       # noqa: BLE001
        return None, '%s: %s' % (type(e).__name__, e)
    finally:
        plt.show, plt.close, matplotlib.use, plt.switch_backend = real
        try:
            plt.close('all')
        except Exception:                        # noqa: BLE001
            pass
        os.chdir(old)


def _r(v):
    try:
        return [round(float(x), 3) for x in v]
    except Exception:                            # noqa: BLE001
        return v


def compare(expected: List[Dict[str, Any]], actual: List[Dict[str, Any]],
            pos_tol: float = POS_TOL,
            fields_by_axis: Optional[Dict[int, set]] = None) -> List[str]:
    """参数（期望）与目标图实测状态比对，返回不一致明细（空 = 一致）。

    fields_by_axis：{轴序号: {字段名, ...}} 时，每轴只比对列出的字段（原位写回
    模式下"没被原位改写的项"不比对，避免因刻意跳过而误判失败）。
    """
    bad: List[str] = []
    for e in expected or []:
        i = e.get('index')
        if not isinstance(i, int) or i >= len(actual or []):
            bad.append('ax%s 在目标图里不存在' % (i,))
            continue
        a = actual[i]
        fields = (fields_by_axis or {}).get(i)
        ep, ap = e.get('pos'), a.get('pos')
        # aspect 锁定的轴（imshow / cartopy 地图）位置由长宽比约束决定：draw 时
        # matplotlib 的 apply_aspect 会重调位置，比"设进去的位置"必然对不上，
        # 所以这类轴只比字号/grid/spines/clim，不比位置。
        locked = (bool(e.get('aspect_locked'))
                  or a.get('aspect') not in (None, 'auto'))
        if fields is None or 'pos' in fields:
            if not locked and ep and ap and any(abs(x - y) > pos_tol for x, y in zip(ep, ap)):
                bad.append('ax%d 位置 %s≠%s' % (i, _r(ep), _r(ap)))
        for key in ('title_fontsize', 'tick_fontsize'):
            if fields is not None and key not in fields:
                continue
            ev, av = e.get(key), a.get(key)
            if ev is None or av is None:
                continue
            if abs(float(ev) - float(av)) > FS_TOL:
                bad.append('ax%d %s %s≠%s' % (i, key, ev, av))
        # label_fontsize：x/y 任一等于参数即通过（原位写回可能只找到 set_xlabel 或
        # 只找到 set_ylabel；脚本 x/y 字号可能本来就不同——只比 x 会漏检 y 异值）
        if fields is None or 'label_fontsize' in fields:
            ev = e.get('label_fontsize')
            if ev is not None:
                avx, avy = a.get('label_fontsize'), a.get('label_fontsize_y')
                if (avx is not None and abs(float(ev) - float(avx)) <= FS_TOL) \
                        or (avy is not None and abs(float(ev) - float(avy)) <= FS_TOL):
                    pass
                elif avx is not None or avy is not None:
                    bad.append('ax%d label_fontsize %s≠%s/%s'
                               % (i, ev, avx, avy))
        if (fields is None or 'grid' in fields) \
                and e.get('grid') is not None \
                and bool(e['grid']) != bool(a.get('grid')):
            bad.append('ax%d grid %s≠%s' % (i, e['grid'], a.get('grid')))
        if fields is None or 'spines' in fields:
            for k, v in (e.get('spines') or {}).items():
                if k in (a.get('spines') or {}) and bool(v) != bool(a['spines'][k]):
                    bad.append('ax%d spines[%s] %s≠%s' % (i, k, v, a['spines'][k]))
        if fields is None or 'clim' in fields:
            ec, ac = e.get('clim'), a.get('clim')
            if ec and ac:
                span = abs(float(ec[1]) - float(ec[0])) or 1.0
                if (abs(float(ec[0]) - float(ac[0])) > CLIM_REL_TOL * span
                        or abs(float(ec[1]) - float(ac[1])) > CLIM_REL_TOL * span):
                    bad.append('ax%d clim %s≠%s' % (i, _r(ec), _r(ac)))
    return bad


def verify_semantic(script: str, data: Dict[str, Any], python: Optional[str] = None,
                    timeout: float = 300,
                    out_path: Optional[str] = None,
                    fields_by_axis: Optional[Dict[int, set]] = None
                    ) -> Tuple[Optional[bool], str]:
    """子进程重跑脚本 → dump 目标图状态 → 与参数比对。

    返回 ``(ok, detail)``：True 一致 / False 不一致（detail 为明细）/ None 无法判定。
    """
    python = python or sys.executable
    if out_path is None:
        d = os.path.dirname(os.path.abspath(script))
        out_path = os.path.join(d, '.tweak_params', '_verify_state.json')
    try:
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
    except OSError:
        pass
    try:
        os.remove(out_path)
    except OSError:
        pass
    fig_index = data.get('fig_index')
    idx_arg = str(fig_index) if isinstance(fig_index, int) else '-1'
    env = dict(os.environ, MPLBACKEND='Agg')
    try:
        r = subprocess.run([python, '-m', 'mpltweak.verify', script, out_path, idx_arg],
                           capture_output=True, timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        return None, '语义验证超时'
    except Exception as e:                       # noqa: BLE001
        return None, str(e)
    if not os.path.exists(out_path):
        tail = (r.stderr or b'').decode('utf-8', 'replace').strip()[-200:]
        return None, '未能取到图状态（%s）' % (tail or 'exit %d' % r.returncode)
    try:
        with open(out_path, 'r', encoding='utf-8') as f:
            actual = json.load(f)
    except Exception as e:                       # noqa: BLE001
        return None, '读取状态失败: %s' % e
    bad = compare(data.get('axes') or [], actual.get('axes') or [],
                  fields_by_axis=fields_by_axis)
    return (not bad), ('; '.join(bad[:4]) if bad else '')


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) < 2:
        sys.stderr.write('用法: python -m mpltweak.verify <script> <out.json> [fig_index]\n')
        return 2
    script, out = argv[0], argv[1]
    try:
        idx: Optional[int] = int(argv[2])
    except (IndexError, ValueError):
        idx = None
    state, err = collect(script, idx)
    if state is None:
        sys.stderr.write('[verify] %s\n' % err)
        return 3
    with open(out, 'w', encoding='utf-8') as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    return 0


if __name__ == '__main__':
    sys.exit(main())
