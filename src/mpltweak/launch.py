# -*- coding: utf-8 -*-
# ======================================================================
# tweak_launch.py —— 无侵入交互调图「外部启动器」
#
# 目标：用户脚本里**完全不出现本工具的任何痕迹**（不 import、不加写回区、
#       不加 --mpltweak 分支、不动一行代码）。做法是外部包一层：
#
#   1. 用 QtAgg 后端执行用户脚本（拦截脚本自己的 plt.show，避免阻塞）
#   2. 抓到脚本留下的 Figure，挂上交互改图窗口
#   3. 关窗后**只**写参数 JSON（默认 <脚本目录>/.tweak_params/<脚本名>.json），
#      不碰代码、不产生其它副作用
#   4. 等用户确认后，由 AI 把 JSON 里的数值变成**纯 matplotlib 代码**写回脚本
#
# 用法：
#   mpltweak <脚本.py> [脚本自己的参数 ...]   （包入口：mpltweak.cli）
#   可选：
#     --fig N         调第 N 张图（默认最后一张）
#     --params PATH   自定义参数输出路径
#     --dry-run       不开窗：只验证「能抓到图 + 能存参数」后立即退出
#     --timeout SEC   配合 --dry-run 无意义；留作将来扩展
#
# 退出码：0 正常；2 脚本没留下可调的图；3 脚本本身报错
# ======================================================================

import argparse
import json
import os
import runpy
import sys
import traceback


def _params_path(script, override=None):
    if override:
        return os.path.abspath(override)
    d = os.path.dirname(script)
    stem = os.path.splitext(os.path.basename(script))[0]
    return os.path.join(d, '.tweak_params', stem + '.json')


def _summarize(fig):
    X, Y = fig.canvas.get_width_height()
    return '图 %dx%dpx，%d 个轴' % (X, Y, len(fig.axes))


def _prune_fig_params(tmps, keep_keys, protect=()):
    """收尾清理：删掉**本轮新产生且未改动**的参数文件 + 全部 ``.lock``。

    多图会话里 ``<stem>.fig<k>.json`` 既是过程中写的那份、也是最终留下的正式参数
    （一个图一份），所以**改动过的必须保留**，否则"一次改多张"会被清掉。

    ``protect``：开窗前就已经存在的图号 —— 那是**上一轮**留下的正式参数，删掉会让
    用户白调一轮（t2-A4/A5、t6-H5）。清理只应针对本轮新产生的临时产物。
    """
    keep = set(keep_keys) | set(protect)
    for k in list(tmps):
        if k not in keep:
            try:
                os.remove(tmps[k])
            except OSError:
                pass
        try:
            os.remove(tmps[k] + '.lock')
        except OSError:
            pass


def _diff(before, data):
    '''对比交互前后的变化，给 AI/用户一行紧凑摘要。'''
    items = []
    axes = data.get('axes', [])
    for a in axes:
        i = a.get('index')
        p = a.get('pos')
        b = before.get(i)
        if b is not None and p is not None and any(
                abs(p[k] - b[k]) > 1e-4 for k in range(4)):
            items.append('ax%d [%s] -> [%s]' % (
                i, ','.join('%.3f' % v for v in b),
                ','.join('%.3f' % v for v in p)))
    fonts = data.get('axes', [])
    for a in fonts:
        if a.get('title_fontsize') is not None:
            pass
    if data.get('legend_changed'):
        pass
    return items


def _apply_saved(fig, params):
    '''续调：把上次的（还没落实的）参数套回图上，这样再开窗口是接着上次调，
    而不是回到脚本原始布局。返回套用的项数。'''
    if not os.path.exists(params):
        return 0
    with open(params, 'r', encoding='utf-8-sig') as f:
        data = json.load(f)
    from .toolbox import _set_cb_clim, _is_colorbar_ax, _cb_mappable, _close_help_fig
    n = 0
    for a in data.get('axes', []):
        i = a.get('index')
        if i is None or i >= len(fig.axes):
            continue
        ax = fig.axes[i]
        p = a.get('pos')
        if p:
            ax.set_position(p)
            n += 1
        if a.get('title_fontsize'):
            ax.title.set_fontsize(a['title_fontsize'])
        if a.get('label_fontsize'):
            ax.xaxis.label.set_fontsize(a['label_fontsize'])
            ax.yaxis.label.set_fontsize(a['label_fontsize'])
        if a.get('tick_fontsize'):
            ax.tick_params(labelsize=a['tick_fontsize'])
        if a.get('clim') and _is_colorbar_ax(ax):
            _set_cb_clim(ax, *a['clim'])
            n += 1
        if a.get('cmap'):
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
                    mappable.set_cmap(a['cmap'])
                    if _is_colorbar_ax(ax):
                        ax._colorbar.update_normal(mappable)
                    n += 1
                except Exception:
                    pass
        if a.get('xscale') and not _is_colorbar_ax(ax):
            try:
                ax.set_xscale(a['xscale'])
                ax.set_yscale(a.get('yscale', ax.get_yscale()))
                n += 1
            except Exception:
                pass
        if a.get('grid') is not None:
            ax.grid(bool(a['grid']))
            n += 1
        for k, visible in a.get('spines', {}).items():
            if k in ax.spines:
                ax.spines[k].set_visible(bool(visible))
        for line_item in a.get('lines', []):
            j = line_item.get('index')
            lines = getattr(ax, 'lines', [])
            if j is not None and j < len(lines):
                line = lines[j]
                if line_item.get('linewidth') is not None:
                    line.set_linewidth(line_item['linewidth'])
                if line_item.get('color') is not None:
                    line.set_color(line_item['color'])
        lg = a.get('legend')
        if lg and ax.get_legend() is not None:
            kw = {'loc': lg.get('loc') or 'best'}
            if lg.get('fontsize'):
                kw['fontsize'] = lg['fontsize']
            ax.legend(**kw)
    fs = data.get('figsize_in')
    if fs:
        try:
            fig.set_size_inches(fs[0], fs[1])
        except Exception:
            pass
    return n


def doctor():
    '''环境自检：报告后端/Qt 绑定/版本/光标枚举支持，供排查"别人的机器"差异。'''
    try:
        import matplotlib
    except ImportError as e:
        # 连 matplotlib 都没有时，原先会抛裸 ModuleNotFoundError（t4-F9 / t6-M19）。
        # 这正是 `pip install -e . --no-deps` 之后的首次体验，必须给人话。
        print('python       :', sys.version.split()[0])
        print('matplotlib   : 未安装 ->', e)
        print('             -> 先安装：pip install matplotlib（或直接 pip install '
              'mpltweak 会一并装上）')
        return 0
    print('python       :', sys.version.split()[0])
    print('matplotlib   :', matplotlib.__version__)
    tried = []
    ok = None
    for backend in ('QtAgg', 'Qt5Agg', 'Qt6Agg', 'TkAgg'):
        try:
            matplotlib.use(backend, force=True)
            import matplotlib.pyplot as plt
            _f = plt.figure()
            plt.close(_f)
            tried.append('%s=OK' % backend)
            ok = backend
            break
        except Exception as e:                     # noqa: BLE001
            tried.append('%s=%s' % (backend, type(e).__name__))
    print('backend      :', matplotlib.get_backend(), '(可用: %s)' % (ok or '无'))
    print('backend 探测 :', ', '.join(tried))
    try:
        from matplotlib.backends import qt_compat
        print('Qt 绑定      :', qt_compat.QtCore.__name__)
        ns = getattr(qt_compat.QtCore.Qt, 'CursorShape', qt_compat.QtCore.Qt)
        shapes = {n: getattr(ns, n, None)
                  for n in ('SizeHorCursor', 'SizeVerCursor',
                            'SizeFDiagCursor', 'SizeBDiagCursor')}
        print('光标枚举     :', shapes)
        if any(v is None for v in shapes.values()):
            print('             -> 有缺失：会静默退回 matplotlib 光标（功能不受影响，'
                  '只是边框/角的形状提示弱一点）')
    except Exception as e:                         # noqa: BLE001
        print('Qt 绑定      : 不可用 ->', e)
    try:
        from matplotlib.axes import Axes
        print('set_box_aspect:', hasattr(Axes, 'set_box_aspect'),
              '(缺则 colorbar 宽度无法钉住，其余功能正常)')
    except Exception:
        pass
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument('script', nargs='?',
                    help='要调图的用户脚本（里面不需要任何本工具的代码）')
    ap.add_argument('--fig', type=int, default=-1, help='调第 N 张图（默认最后一张）')
    ap.add_argument('--params', default=None, help='参数输出路径')
    ap.add_argument('--force-resume', action='store_true',
                    help='脚本在参数保存后被改过也强行套用旧参数'
                         '（默认不套用，避免静默覆盖你的手动改动）')
    ap.add_argument('--doctor', action='store_true',
                    help='只做环境自检（后端/绑定/版本/光标），不跑脚本')
    ap.add_argument('--dry-run', action='store_true',
                    help='不开窗，只验证能抓图并存参数')
    ap.add_argument('--verbose', action='store_true',
                    help='打印全部交互日志（缩放开始 axN <手柄> 等），排查用')
    args, passthrough = ap.parse_known_args(argv)

    if args.doctor:
        return doctor()
    if not args.script:
        sys.stderr.write('[launch] 需要脚本路径，或用 --doctor 做环境自检\n')
        return 3

    script = os.path.abspath(args.script)
    if not os.path.exists(script):
        sys.stderr.write('[launch] 找不到脚本: %s\n' % script)
        return 3

    # 布局引擎冲突（constrained_layout / figure.autolayout）必须**在开窗之前**提示：
    # 引擎会在每次绘制时重算轴位置，用户拖完看到的是"位置弹回去/验证不一致"，
    # 而病因在脚本第 N 行（外部审阅第 4 条）。开窗后再提醒已经白拖了。
    from . import layoutwarn
    for _line in layoutwarn.messages_for(layoutwarn.scan(script)):
        sys.stderr.write(_line + '\n')

    # Qt 后端必须在用户脚本之前设置。Qt 绑定/版本差异（PyQt5/PyQt6/PySide2/PySide6、
    # 老版本没有 QtAgg 名字）全在这里兜住：逐个候选后端真建一次 canvas 验证，
    # 都不行就给一句人话（而不是甩 traceback）。
    import matplotlib
    import matplotlib.pyplot as plt
    backend_err = None
    for backend in ('QtAgg', 'Qt5Agg', 'Qt6Agg', 'TkAgg'):
        try:
            matplotlib.use(backend, force=True)
            _probe = plt.figure()          # 真建一次，确认能拿到交互 canvas
            plt.close(_probe)
            backend_err = None
            break
        except Exception as e:             # noqa: BLE001 - 故意兜所有
            backend_err = e
    if backend_err is not None:
        # 注意：此时**还没有**改过 cwd（chdir 在下面才做），所以无需恢复。
        # 这里曾经写的是 os.chdir(old_cwd)，而 old_cwd 在下面才赋值 →
        # 抛 UnboundLocalError，把一段友好提示变成看不懂的 traceback。
        sys.stderr.write(
            '[launch] 找不到可用的交互后端（试过 QtAgg/Qt5Agg/Qt6Agg/TkAgg）：%s\n'
            % backend_err)
        sys.stderr.write(
            '[launch] 开窗调图需要一个图形后端，任选其一：\n'
            '           pip install "mpltweak[qt]"     # 推荐（装 PyQt5）\n'
            '           # Linux 也可以用系统包管理器装 python3-tk\n'
            '[launch] 若你只需要命令行用法（describe / check / apply / mcp），\n'
            '         则不需要图形后端，那些命令照常可用。\n')
        return 4

    # 以脚本所在目录为工作目录（脚本里的相对路径/存图位置才和平时一致），
    # 并把脚本目录加进 sys.path —— runpy.run_path 不像 `python x.py` 那样自动加，
    # 脚本里 `from toolbox import ...` 之类的同目录导入会失败。
    old_cwd = os.getcwd()
    script_dir = os.path.dirname(script)
    os.chdir(script_dir)
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)
    sys.argv = [script] + list(passthrough)

    # 1) 执行用户脚本（拦掉它自己的 plt.show，避免卡住）
    #    同时把 matplotlib.use / plt.switch_backend 临时变成空操作：
    #    科研脚本常硬写 matplotlib.use('Agg')（批量出图用），那会把我们的交互后端顶掉。
    #    Agg/Qt 只影响"怎么显示"，不影响 savefig 结果，所以忽略这行是安全的，
    #    而且用户脚本一个字都不用改。
    real_show = plt.show
    real_use = matplotlib.use
    real_switch = plt.switch_backend
    real_close = plt.close
    plt.show = lambda *a, **k: None
    matplotlib.use = lambda *a, **k: None
    plt.switch_backend = lambda *a, **k: None
    # 科研脚本八件套常在存图后 plt.close(fig)：那会把图关掉，启动器就挂不上窗了。
    # 交互调图期间把 close 临时变空操作——图保留给启动器挂窗，脚本本身零改动。
    plt.close = lambda *a, **k: None
    try:
        runpy.run_path(script, run_name='__main__')
    except SystemExit:
        pass
    except Exception:
        plt.show = real_show
        matplotlib.use = real_use
        plt.switch_backend = real_switch
        os.chdir(old_cwd)
        traceback.print_exc()
        sys.stderr.write('[launch] 用户脚本执行出错，已中止\n')
        return 3
    finally:
        plt.show = real_show
        matplotlib.use = real_use
        plt.switch_backend = real_switch
        plt.close = real_close

    # 2) 抓脚本留下的图
    nums = plt.get_fignums()
    if not nums:
        os.chdir(old_cwd)
        sys.stderr.write('[launch] 脚本没有留下可交互的图（可能已 close 或只存盘）\n')
        return 2
    # 兜底：脚本若通过别的途径（如 `from matplotlib import use` 的引用、
    # 或某个库在 import 时切后端）把后端弄成非交互，就把已有图**重新挂回 Qt**——
    # switch_backend 会保留 figure 对象、只换 canvas，图内容不丢。
    be = str(matplotlib.get_backend()).lower()
    if not (be.startswith('qt') or be.startswith('tk')):
        sys.stderr.write('[launch] 脚本把后端设成了 %s，正在把已有图重新挂回 QtAgg…\n' % be)
        for _b in ('QtAgg', 'Qt5Agg', 'Qt6Agg', 'TkAgg'):
            try:
                plt.switch_backend(_b)
                break
            except Exception:
                continue
        be = str(matplotlib.get_backend()).lower()
    if not (be.startswith('qt') or be.startswith('tk')):
        os.chdir(old_cwd)
        sys.stderr.write('[launch] 后端仍是非交互（%s），窗口无法显示。\n' % be)
        sys.stderr.write('[launch] 请临时注释脚本里的 matplotlib.use(...) 那行再试。\n')
        return 2
    if args.fig >= 0:
        if args.fig >= len(nums):
            sys.stderr.write('[launch] 只有 %d 张图，取不到第 %d 张\n'
                             % (len(nums), args.fig))
            return 2
        targets = [args.fig]
    else:
        targets = list(range(len(nums)))       # 默认：每张图都可交互
    idx = args.fig if args.fig >= 0 else len(nums) - 1

    from .toolbox import gaitu
    params = _params_path(script, args.params)
    os.makedirs(os.path.dirname(params), exist_ok=True)
    stem = os.path.splitext(params)[0]

    print('[launch] 脚本: %s' % os.path.basename(script), flush=True)
    if len(targets) > 1:
        print('[launch] 共 %d 张图，全部可交互：%s'
              % (len(nums),
                 ' | '.join('%d:%s' % (k + 1, _summarize(plt.figure(nums[k])))
                            for k in targets)), flush=True)
    else:
        print('[launch] 正在调第 %d 张（共 %d 张）：%s'
              % (idx + 1, len(nums), _summarize(plt.figure(nums[idx]))), flush=True)
    print('[launch] 关窗后只写参数，不改代码: %s' % params, flush=True)
    print('[launch] 提示：请用**英文输入法**（中文输入法下 c/s/g/x/y 等字母快捷键会直接上屏）',
          flush=True)
    if len(targets) > 1:
        print('[launch] 提示：%d 个窗口，**全部关闭**后本进程才会退出（窗口标题带 [图 k/%d]）。'
              '一次关完按 Ctrl+Q；每关一个就立刻存一份参数（没改过的不存），随时停也不会白改'
              % (len(targets), len(nums)), flush=True)

    # 多图：每张图各挂一个控制器、各写自己的参数文件（``<stem>.fig<k>.json``，
    # 文件名带图号，就是它最终的正式参数）；关窗时按"哪张真被改过"决定留不留，
    # 并把最后改动的那张另存成主文件 —— 所以不必预先 --fig 指定。
    clock = [0]
    tws, tmps = {}, {}
    _preexisting = set()     # 开窗前已存在的参数文件（上一轮的成果，清理时必须保）
    _closed_cnt = [0]        # 已关闭的主图窗口数（用于主图全关后清理 ? 键位表窗口）
    for k in targets:
        _f = plt.figure(nums[k])
        tmps[k] = '%s.fig%d.json' % (stem, k)
        # 记下"开窗前就已存在"的：收尾清理只能删本轮新产生的临时产物，绝不能删
        # 上一轮留下的正式参数（t2-A4/A5、t6-H5 实测：多图分轮调图时第 2 轮会把
        # 第 1 轮改好的记录删掉，用户白调一轮）。
        if os.path.exists(tmps[k]):
            _preexisting.add(k)
        tws[k] = gaitu(_f, export_path=tmps[k],
                       quiet=not args.verbose, fig_index=k, n_figs=len(nums),
                       edit_clock=clock)

        # 关这一个窗口就把它的参数立刻落成正式文件（<stem>.fig<k>.json + 主文件），
        # 不等"所有窗口关完"那一步汇总——这样只关一部分 / Ctrl+C 都不会白改。
        # 没改动的不落（与"没动过不产生文件"一致）。
        # 顺序：gaitu 的关窗回调先注册、先执行（写临时文件），本回调在其后读它。
        def _on_close(_event, _k=k):
            _closed_cnt[0] += 1
            try:
                _cur = _read_json(tmps[_k])
                if _cur is None or bases.get(_k) is None or _cur == bases[_k]:
                    return                         # 这张没动过 → 不留参数文件
                # 这张图自己的参数文件（tmps[_k]，也就是它最终的正式参数）已由
                # gaitu 的关窗回调写好；这里只补一份主文件 = 最后关闭且改动过的那张。
                with open(tmps[_k], 'rb') as _fsrc:
                    _blob = _fsrc.read()
                with open(params, 'wb') as _fdst:
                    _fdst.write(_blob)
            except Exception as _e:                # noqa: BLE001
                print('[launch] 警告：关窗落参数失败（第 %d 张图，可能未保存）：%s'
                      % (_k + 1, _e), file=sys.stderr, flush=True)
            finally:
                # 主图全关 → 顺带关掉 ? 键位表窗口，否则 plt.show() 会等它，
                # 用户忘了关键位表窗口会导致进程不退出。
                if _closed_cnt[0] >= len(targets):
                    try:
                        # 注意：_close_help_fig 只在本模块的**函数内部** import 过，
                        # launch 的全局命名空间里并没有它 —— 直接调用会 NameError，
                        # 而原先的 `except Exception: pass` 会把错误吞掉，于是帮助窗
                        # 关不掉、plt.show() 永远等它 → 进程挂死（t2-A1 / t6-H6，
                        # 真 Qt E2E 实测 12s 不退出 vs 注入后 3.2s 正常退出）。
                        from . import toolbox as _tb
                        _tb._close_help_fig()
                    except Exception as _e:        # noqa: BLE001
                        print('[launch] 警告：关闭帮助窗口失败: %s' % _e,
                              file=sys.stderr, flush=True)

        _f.canvas.mpl_connect('close_event', _on_close)

        # Ctrl+Q = 关掉全部窗口（多图时不必逐个去找那个压在后面的小窗口）。
        # 此处 plt.close 已被恢复成真的 close（见上文 real_close），所以能真关。
        def _on_quit_key(_event):
            if str(getattr(_event, 'key', '') or '').lower() == 'ctrl+q':
                try:
                    plt.close('all')
                except Exception:                  # noqa: BLE001
                    pass

        _f.canvas.mpl_connect('key_press_event', _on_quit_key)

    # 续调：把所有参数文件各自套回它记录的图（多图会话会有主文件 + <stem>.fig<k>.json）
    def _resume_files():
        _stem = os.path.splitext(params)[0]
        _d, _base = os.path.dirname(_stem), os.path.basename(_stem)
        _out = {}
        if os.path.isdir(_d):
            for _fn in sorted(os.listdir(_d)):
                if (_fn.startswith(_base + '.fig') and _fn.endswith('.json')
                        and not _fn.endswith('.writeback.json')):
                    _out[_fn] = os.path.join(_d, _fn)
        if os.path.exists(params):
            _out.setdefault(_base + '.json', params)
        picked, seen = [], set()                  # 带图号的文件优先，按图号去重
        for _fn, _pth in sorted(_out.items()):
            try:
                with open(_pth, 'r', encoding='utf-8-sig') as _f:
                    _k = json.load(_f).get('fig_index')
            except Exception:
                _k = None
            if isinstance(_k, int):
                if _k in seen:
                    continue
                seen.add(_k)
            picked.append(_pth)
        return picked

    resumed = []
    for pth in _resume_files():
        try:
            with open(pth, 'r', encoding='utf-8-sig') as f:
                _d2 = json.load(f)
        except Exception:
            continue
        # 脚本比参数文件新 → 代码在保存参数之后被手动改过。此时套用旧参数会
        # 静默覆盖那些改动（你改的看不到效果，关窗写回还会把旧值盖回去），
        # 所以默认跳过；确要接着上次调，加 --force-resume。
        try:
            _stale = os.path.getmtime(script) > os.path.getmtime(pth) + 1.0
        except OSError:
            _stale = False
        if _stale and not args.force_resume:
            print('[launch] 注意：脚本在参数保存之后被修改过 → 本次不套用该参数'
                  '（避免覆盖你的手动改动）；确要续调加 --force-resume', flush=True)
            continue
        kk = _d2.get('fig_index')
        if kk is None:
            kk = idx if idx in tws else None        # 主文件没图号 → 默认图
        elif not isinstance(kk, int) or kk not in tws:
            continue                                 # 陈旧图号（脚本图数减少/换 --fig）→ 跳过，
                                                     # 不回退到 idx 套错图
        if kk is None:
            continue
        n_saved = _apply_saved(plt.figure(nums[kk]), pth)
        if n_saved:
            resumed.append((kk, n_saved))
    if resumed:
        print('[launch] 已套用上次参数：%s（接着上次调；删掉这些文件可从头开始）'
              % '; '.join('第 %d 张 %d 项' % (k + 1, n)
                          for k, n in sorted(resumed)), flush=True)
    apply_to = resumed[-1][0] if resumed else idx

    def _read_json(p):
        try:
            with open(p, 'r', encoding='utf-8-sig') as f:
                return json.load(f)
        except Exception:
            return None

    # 基线：挂载 + 续调都做完之后再落，避免把这两步误报成"本次改动"
    for k in tws:
        tws[k].export(path=tmps[k])
    bases = {k: _read_json(tmps[k]) for k in tws}

    def _copy_to_params(src_k):
        with open(tmps[src_k], 'rb') as fsrc:
            blob = fsrc.read()
        with open(params, 'wb') as fdst:
            fdst.write(blob)

    def _clean_temps(keep_keys=()):
        _prune_fig_params(tmps, keep_keys, protect=_preexisting)

    if args.dry_run:
        src_k = apply_to if apply_to in tws else sorted(tws)[-1]
        _copy_to_params(src_k)
        # --dry-run 绝不做破坏性清理：原先这里是 _clean_temps()（keep=()），会把
        # **之前所有**已存参数删光（t2 / t6-H5 实测：跑完只剩主文件）。
        # 但本轮自己产生的 .lock 是标记文件、不是用户数据，要收掉（否则残留，
        # t5-S13）。
        for _k in tws:
            try:
                os.remove(tmps[_k] + '.lock')
            except OSError:
                pass
        print('[launch] --dry-run：参数已写出（未开窗）-> %s' % params, flush=True)
        os.chdir(old_cwd)
        return 0

    real_show()                # 开窗交互；关窗时各图把自己的最终状态写进临时文件

    changed = []
    for k in sorted(tws):
        cur = _read_json(tmps[k])
        if cur is not None and bases.get(k) is not None and cur != bases[k]:
            changed.append((tws[k].edit_seq, k))

    chosen = None
    if changed:
        changed.sort()
        chosen = changed[-1][1]                # 最后被改动的那张

    if chosen is None:
        print('[launch] 未检测到改动，未写参数文件', flush=True)
    else:
        # 每张改过的图在它的关窗回调里已经写好了自己的 <stem>.fig<k>.json（= 正式参数），
        # 这里只需把"最后改动的那张"另存一份主文件（兼容旧读者）；未改动的随后清掉。
        _copy_to_params(chosen)
        if len(changed) == 1:
            print('[launch] 已记录第 %d 张图（共 %d 张）的参数: %s'
                  % (chosen + 1, len(nums), params), flush=True)
        else:
            print('[launch] 已记录 %d 张图的参数（%s）：'
                  % (len(changed), os.path.dirname(params)), flush=True)
            for _, k in sorted(changed):
                print('[launch]   第 %d 张 -> %s%s'
                      % (k + 1, os.path.basename(tmps[k]),
                         '（并另存主文件）' if k == chosen else ''), flush=True)
    _clean_temps([k for _, k in changed])

    # 3) 变化摘要（只读，不写代码）
    if chosen is None:
        # 没有任何改动时主文件根本不存在 —— 原先会去 open 它，然后打印
        # "参数摘要读取失败: [Errno 2]"，一切正常却像出了事（t6-M17）。
        print('[launch] 参数摘要: 无改动', flush=True)
    else:
        try:
            with open(params, 'r', encoding='utf-8-sig') as f:
                data = json.load(f)
            before = {a['index']: a['pos'] for a in bases[chosen].get('axes', [])}
            ch = _diff(before, data)
            print('[launch] 位置变化: %s' % ('; '.join(ch) if ch else '无'),
                  flush=True)
        except Exception as e:
            print('[launch] 参数摘要读取失败: %s' % e, flush=True)

    os.chdir(old_cwd)
    return 0


if __name__ == '__main__':
    sys.exit(main())
