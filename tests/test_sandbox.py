# -*- coding: utf-8 -*-
# tests/test_sandbox.py —— 文件系统沙箱（t9）验收测试。
#
# 用法：python tests/test_sandbox.py   → ALL PASS / exit 0
# 本文件是模块级脚本（各套件同款风格），**不被 pytest 收集**（pyproject
# python_files 只收集 test_suites.py + test_hygiene.py；本文件是独立套件，由
# test_suites.py 的子进程驱动或手工直跑）。hygiene 的 GBK-console 棘轮会检查本文件
# （装了 reconfigure 兜底，见文件头）。
#
# 内容（验收四类 + 补充）：
#   A. 拦得住：删除/写/子进程/网络 四种捣蛋脚本在沙箱下全被拦 + 证据日志
#   B. 不误拦：正常绘图（savefig）+ matplotlib 字体缓存 + numpy 导入 必须成功
#   C. 对照：同一捣蛋脚本 不开守卫→成功（文件真被删） / 开守卫→被拦（文件还在）
#   D. 禁 GUI：import tkinter 被拦；matplotlib.use('TkAgg') 被拦/回退 Agg；正常 Agg 不误拦
#   E. 链路：apply --write（沙箱 PYTHONPATH 合并）→ verify 子进程 → 脚本 守卫全程生效
#   F. 无人值守：input() 读即 EOFError（防卡超时，t10 na_stdin 用）
#   G. 工具单测：sandbox_env 的 PYTHONPATH 合并（沙箱目录 + <树>/src 并存）；
#      子进程输出编码钉死 utf-8（t15 根因哨兵）
#
# t15 —— 编码健壮性（本轮根因修复，勿回退）：
#   在**不设** PYTHONIOENCODING 的 GBK 控制台上，CPython 会把子进程 stdout/stderr
#   按 locale 编码（cp936）写进管道；旧版本测试侧硬解 UTF-8，守卫抛出的中文
#   PermissionError 文案整段变成 U+FFFD，只剩 traceback 里的 ASCII 片段
#   （如 `, line 136, in _block`）→ A1/A2/A3/A4/C2/D1/D2 七项"守卫明明生效却判失败"。
#   两条纪律：
#     ① 子进程输出编码由 sandbox_env/plain_env **显式钉死** utf-8（见 G1/G2）；
#     ② 断言**行为信号优先** —— 退出码、文件是否存在、守卫日志（纯 ASCII 事件名）、
#        traceback 里的守卫函数名；中文文案只在①钉死之后作为补充证据匹配。
#
# 抢鼠标防护（2026-10-12 事故后加严）：本文件起的**每一个**子进程 env 都硬钉
# MPLBACKEND=Agg（见 _pin_child_env / G3），**不用 setdefault** —— 宿主环境若把
# MPLBACKEND 设成 Qt5Agg/TkAgg，子进程就会落到交互后端，plt.show()/建图即真开窗、
# 抢焦点与鼠标。测试侧的子进程跑的正是陌生脚本，这一层最不能漏。
# （真·开窗还有一条与后端无关的路：脚本直接 `tkinter.Tk()` —— 那条由
#   sitecustomize 的禁 GUI 层按"拦 import"兜住，边界见 _review/sandbox_t9/SANDBOX.md。）
#
# 本任务只跑**自己写的**捣蛋/正常脚本 —— **绝不执行任何真实语料脚本**。
import os
import shutil
import subprocess
import sys
import tempfile

try:
    sys.stdout.reconfigure(errors='replace')
    sys.stderr.reconfigure(errors='replace')
except (AttributeError, ValueError, OSError):
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # mpltweak 树
SRC = os.path.join(ROOT, 'src')
sys.path.insert(0, os.path.join(ROOT, 'tests', 'sandbox'))
from sandbox_env import (sandbox_env, plain_env, SANDBOX_DIR,   # noqa: E402
                         CHILD_IO_ENCODING)

_PY = sys.executable
# 临时根必须**跨平台**取：Linux 上只有 TMPDIR（没有 TEMP）⇒ 原先写
# `os.environ.get('TEMP', '.')` 会回落成相对路径 `./sandbox_t9_tests`，
# 于是 PG 也变相对，而 `_run_raw()` 是「相对脚本路径 + cwd=PG」起子进程 ⇒
# 子进程在 PG 里再拼一遍相对路径 ⇒ `python: can't open file
# '…/sandbox_t9_tests/playground/sandbox_t9_tests/playground/<name>.py'`、rc=2、
# **脚本一行没跑** ⇒ CI 的 py3.12 Ubuntu 那一格全红（本机删掉 TEMP/TMP/TMPDIR
# 可复现 18 项失败）。`tempfile.gettempdir()` 三平台都认（TMPDIR/TEMP/TMP）。
WORKROOT = os.path.join(tempfile.gettempdir(), 'sandbox_t9_tests')
PG = os.path.join(WORKROOT, 'playground')      # 游乐场（每次全新）
OUT = os.path.join(WORKROOT, 'outside')        # 游乐场外（放"受害者"文件）
LOG = os.path.join(WORKROOT, 'sandbox.log')    # 守卫拦截日志

#: 子进程输出解码口径 —— 与 sandbox_env.CHILD_IO_ENCODING 同一个来源（钉死）。
_IO_ENC = CHILD_IO_ENCODING

#: 守卫 traceback 里的**函数名**（纯 ASCII，任何控制台编码下都原样幸存）。
_GUARD_FNS = ('_block', '_block_always')                       # 文件/子进程/网络
_GUI_FNS = ('find_spec', '_sandbox_use', 'sitecustomize.py')   # 禁 GUI

_FAIL = []


def _check(name, cond, detail=''):
    if cond:
        print('PASS | %s' % name)
    else:
        print('FAIL | %s | %s' % (name, detail))
        _FAIL.append(name)


def _fresh_dirs():
    for d in (WORKROOT, PG, OUT):
        if os.path.isdir(d):
            shutil.rmtree(d)
    os.makedirs(PG)
    os.makedirs(os.path.join(PG, 'tmp'))
    os.makedirs(OUT)
    if os.path.exists(LOG):
        os.remove(LOG)


def _write(path, text):
    with open(path, 'w', encoding='utf-8') as f:
        f.write(text)


# ---------------------------------------------------------------------------
# 子进程执行 + 解码（t15：编码钉死后按 utf-8 读，非法字节只降级不炸）
# ---------------------------------------------------------------------------
def _decode(raw):
    """子进程输出解码：口径固定 _IO_ENC（由 sandbox_env 钉死），非法字节降级替换。"""
    return (raw or b'').decode(_IO_ENC, 'replace')


def _read_text(path):
    """读文件：utf-8 + errors='replace'（路径/内容都可能是守卫写的）。"""
    try:
        with open(path, encoding=_IO_ENC, errors='replace') as f:
            return f.read()
    except OSError:
        return ''


def _pin_child_env(env):
    """起子进程前的**最后一道闸**：硬钉 MPLBACKEND=Agg（+ 输出编码口径）。

    必须硬钉而不是 setdefault：宿主环境若把 MPLBACKEND 设成 Qt5Agg/TkAgg，
    setdefault 会原样保留 → 子进程落到交互后端 → plt.show()/建图真开窗、抢焦点
    与鼠标（2026-10-12 事故的同类隐患）。这里对任何传进来的 env 都生效，
    即使调用方拿的是手搓 dict。
    """
    env['MPLBACKEND'] = 'Agg'
    env['PYTHONIOENCODING'] = _IO_ENC
    return env


def _run_raw(payload, env, cwd, name=None, timeout=90, stdin_devnull=True):
    """跑脚本，返回**原始字节**（G2 要验证字节层编码，不能先解码）。

    脚本路径一律取**绝对**：子进程的 cwd 就是 `cwd`，若脚本路径是相对的，子进程会
    相对 `cwd` 再解析一次（= 路径被拼两层）⇒ `can't open file`、rc=2、脚本没跑。
    这正是 Linux 上（`TEMP` 缺失 ⇒ WORKROOT 变相对）那一批假红的一半原因；
    取绝对后，即便将来路径语义再被改坏，也不会退化成"找不到脚本"。
    """
    script = os.path.join(os.path.abspath(cwd), (name or 'evil_script') + '.py')
    _write(script, payload)
    r = subprocess.run([_PY, script], capture_output=True, timeout=timeout,
                       env=_pin_child_env(dict(env)), cwd=cwd,
                       stdin=subprocess.DEVNULL if stdin_devnull else None)
    return r.returncode, (r.stdout or b''), (r.stderr or b'')


def _run_script(payload, env, cwd, name=None, timeout=90, stdin_devnull=True):
    """把 payload 写成一个脚本文件跑（argv[0]=文件 → 完整守卫路径）。"""
    rc, raw_out, raw_err = _run_raw(payload, env, cwd, name, timeout, stdin_devnull)
    return rc, _decode(raw_out), _decode(raw_err)


# ---------------------------------------------------------------------------
# 行为信号取证：守卫日志（纯 ASCII 事件名，跨编码稳定）
# ---------------------------------------------------------------------------
def _log_lines():
    """守卫拦截日志当前全部行（守卫用 utf-8 字节写，见 sitecustomize._log）。"""
    return _read_text(LOG).splitlines()


def _blocked(new_lines, event, needle=None):
    """新增日志里是否有 `BLOCK <event>...`（event 可给前缀，如 'socket.'）。

    needle 非空时还要求该行含这个路径/对象 —— 证明拦的是"这一个"目标。
    """
    for ln in new_lines:
        if ln.startswith('BLOCK %s' % event) and (needle is None or needle in ln):
            return True
    return False


def _guard_frame(err, fns):
    """traceback 是否落在守卫函数里 —— 断言"是守卫拦的"而不是"脚本自己崩的"。"""
    return any(fn in err for fn in fns)


# ---------------------------------------------------------------------------
# 辅助脚本
# ---------------------------------------------------------------------------
def _evil_delete(victim):
    return ('import os\n'
            "os.remove(%r)\n"
            "print('DELETED')\n" % victim)


def _evil_write(target):
    return ('open(%r, "w").write("x")\n'
            "print('WROTE')\n" % target)


def _evil_subprocess():
    return ('import subprocess\n'
            "subprocess.run([%r, '-c', 'pass'])\n"
            "print('SPAWNED')\n" % _PY)


def _evil_socket():
    return ('import socket\n'
            "s = socket.socket()\n"
            "s.connect(('127.0.0.1', 9))\n"
            "print('CONNECTED')\n")


def _normal_plot():
    return ('import matplotlib\n'
            "import matplotlib.pyplot as plt\n"
            "fig, ax = plt.subplots(figsize=(6, 4))\n"
            "ax.plot([1, 2])\n"
            "fig.savefig('out.png')\n"
            "print('PLOT_OK', matplotlib.get_backend())\n")


# ===========================================================================
# A. 拦得住
# ===========================================================================
def _test_a():
    # A1 删除游乐场外的真实文件 → 被拦 + 文件仍在 + 日志有 BLOCK os.remove
    #    行为信号：rc≠0、脚本没走到 print('DELETED')、受害者文件仍在；
    #    归因：守卫日志 + traceback 里的 _block。
    victim = os.path.join(OUT, 'victim_delete.txt')
    _write(victim, 'keep me')
    env = sandbox_env(PG, log_path=LOG)
    mark = len(_log_lines())
    rc, out, err = _run_script(_evil_delete(victim), env, PG, name='a1')
    new = _log_lines()[mark:]
    logged = _blocked(new, 'os.remove', victim)
    guard = _guard_frame(err, _GUARD_FNS)
    _check('A1 删除外部文件被拦',
           rc != 0 and os.path.exists(victim) and 'DELETED' not in out
           and logged and guard,
           'rc=%d exists=%s deleted_printed=%s logged=%s guard_fn=%s err=%.200s'
           % (rc, os.path.exists(victim), 'DELETED' in out, logged, guard,
              err[-300:]))
    _check('A1 守卫日志记录 BLOCK', logged)

    # A2 往游乐场外写文件 → 被拦 + 目标不存在
    target = os.path.join(OUT, 'evil_write.txt')
    env = sandbox_env(PG, log_path=LOG)
    mark = len(_log_lines())
    rc, out, err = _run_script(_evil_write(target), env, PG, name='a2')
    new = _log_lines()[mark:]
    logged = _blocked(new, 'open(write)', target)
    guard = _guard_frame(err, _GUARD_FNS)
    _check('A2 外部写被拦',
           rc != 0 and not os.path.exists(target) and 'WROTE' not in out
           and logged and guard,
           'rc=%d exists=%s wrote_printed=%s logged=%s guard_fn=%s err=%.200s'
           % (rc, os.path.exists(target), 'WROTE' in out, logged, guard,
              err[-300:]))

    # A3 起子进程 → 被拦
    env = sandbox_env(PG, log_path=LOG)
    mark = len(_log_lines())
    rc, out, err = _run_script(_evil_subprocess(), env, PG, name='a3')
    new = _log_lines()[mark:]
    logged = _blocked(new, 'subprocess.')
    guard = _guard_frame(err, _GUARD_FNS)
    _check('A3 子进程被拦',
           rc != 0 and 'SPAWNED' not in out and logged and guard,
           'rc=%d spawned_printed=%s logged=%s guard_fn=%s err=%.220s'
           % (rc, 'SPAWNED' in out, logged, guard, err[-320:]))

    # A4 连 socket → 被拦
    env = sandbox_env(PG, log_path=LOG)
    mark = len(_log_lines())
    rc, out, err = _run_script(_evil_socket(), env, PG, name='a4')
    new = _log_lines()[mark:]
    logged = _blocked(new, 'socket.')
    guard = _guard_frame(err, _GUARD_FNS)
    _check('A4 网络被拦',
           rc != 0 and 'CONNECTED' not in out and logged and guard,
           'rc=%d connected_printed=%s logged=%s guard_fn=%s err=%.220s'
           % (rc, 'CONNECTED' in out, logged, guard, err[-320:]))


# ===========================================================================
# B. 不误拦
# ===========================================================================
def _test_b():
    # B1 正常绘图 + savefig（到游乐场 cwd）→ 成功 + png 存在 + 后端 Agg
    env = sandbox_env(PG, log_path=LOG)
    rc, out, err = _run_script(_normal_plot(), env, PG, name='b1')
    _check('B1 正常绘图成功',
           rc == 0 and 'PLOT_OK' in out and 'agg' in out.lower()
           and os.path.exists(os.path.join(PG, 'out.png')),
           'rc=%d out=%.80s png=%s err=%.120s'
           % (rc, out.strip()[:100],
              os.path.exists(os.path.join(PG, 'out.png')), err[-160:]))

    # B2 matplotlib 字体缓存（MPLCONFIGDIR 在游乐场内）+ numpy 导入 → 成功
    env = sandbox_env(PG, log_path=LOG)
    rc, out, err = _run_script(
        'import matplotlib\nimport numpy as np\n'
        "import os\nprint('CACHE_OK', os.path.exists(os.environ['MPLCONFIGDIR']))\n",
        env, PG, name='b2')
    _check('B2 字体缓存/numpy 不炸',
           rc == 0 and 'CACHE_OK' in out and 'True' in out,
           'rc=%d out=%.120s err=%.120s' % (rc, out.strip()[:140], err[-160:]))

    # B3 沙箱下不产生"游乐场外"的 __pycache__（PYTHONDONTWRITEBYTECODE）
    pyc_outside = [os.path.join(dp, f) for dp, _dn, fn in os.walk(OUT)
                   for f in fn if f.endswith('.pyc')]
    _check('B3 游乐场外无 __pycache__', len(pyc_outside) == 0,
           'found=%d' % len(pyc_outside))


# ===========================================================================
# C. 对照：守卫 开/关 同一捣蛋脚本
# ===========================================================================
def _test_c():
    victim = os.path.join(OUT, 'victim_control.txt')

    # C1 不开守卫 → 删除**真的成功**（文件真被删 + 脚本真的跑到 DELETED）——
    #    这是"证明守卫确实是那道墙"的关键对照，**不得弱化**。
    _write(victim, 'delete me')
    env_off = plain_env()
    mark = len(_log_lines())
    rc_off, out_off, err_off = _run_script(_evil_delete(victim), env_off, PG,
                                           name='c_off')
    new_off = _log_lines()[mark:]
    _check('C1 对照-无守卫删除成功',
           rc_off == 0 and not os.path.exists(victim) and 'DELETED' in out_off
           and not _blocked(new_off, 'os.remove', victim),
           'rc=%d exists=%s out=%.80s blocked=%s err=%.120s'
           % (rc_off, os.path.exists(victim), out_off.strip()[:80],
              _blocked(new_off, 'os.remove', victim), err_off[-160:]))

    # C2 开守卫 → 同一脚本、同一目标：被拦（文件还在 + 脚本没跑到 DELETED +
    #    守卫日志记得这一笔 + traceback 落在 _block 上）
    _write(victim, 'keep me')
    env_on = sandbox_env(PG, log_path=LOG)
    mark = len(_log_lines())
    rc_on, out_on, err_on = _run_script(_evil_delete(victim), env_on, PG,
                                        name='c_on')
    new_on = _log_lines()[mark:]
    logged = _blocked(new_on, 'os.remove', victim)
    guard = _guard_frame(err_on, _GUARD_FNS)
    _check('C2 对照-有守卫删除被拦',
           rc_on != 0 and os.path.exists(victim) and 'DELETED' not in out_on
           and logged and guard,
           'rc=%d exists=%s deleted_printed=%s logged=%s guard_fn=%s err=%.200s'
           % (rc_on, os.path.exists(victim), 'DELETED' in out_on, logged, guard,
              err_on[-300:]))


# ===========================================================================
# D. 禁 GUI
# ===========================================================================
def _test_d():
    # D1 import tkinter → 被拦（不是弹窗）。归因：traceback 落在守卫的
    #    find_spec；中文文案『禁止 GUI』是 t10 的 na_gui 分类口径，编码钉死后才
    #    允许匹配（它本身就是契约的一部分）。
    env = sandbox_env(PG, log_path=LOG)
    rc, out, err = _run_script('import tkinter\nprint("TK_OK")\n', env, PG,
                               name='d1')
    guard = _guard_frame(err, _GUI_FNS)
    _check('D1 import tkinter 被拦',
           rc != 0 and 'TK_OK' not in out and guard and '禁止 GUI' in err,
           'rc=%d tk_printed=%s guard_fn=%s err=%.220s'
           % (rc, 'TK_OK' in out, guard, err[-320:]))

    # D2 matplotlib.use('TkAgg') → 被拦或回退 Agg（不得弹窗；TkAgg 要 import tkinter）
    env = sandbox_env(PG, log_path=LOG)
    rc, out, err = _run_script(
        'import matplotlib\n'
        "matplotlib.use('TkAgg')\n"
        "print('BACKEND', matplotlib.get_backend())\n", env, PG, name='d2')
    got_backend = ''
    for ln in out.splitlines():
        if ln.startswith('BACKEND'):
            got_backend = ln.split()[-1]
    blocked_ok = (rc != 0 and _guard_frame(err, _GUI_FNS)
                  and ('只允许无窗口后端' in err or '禁止 GUI' in err))
    fellback_ok = (rc == 0 and 'BACKEND' in out
                   and 'agg' in got_backend.lower())
    _check('D2 TkAgg 被拦/回退 Agg（不弹窗）', blocked_ok or fellback_ok,
           'rc=%d backend=%s guard_fn=%s err=%.220s'
           % (rc, got_backend, _guard_frame(err, _GUI_FNS), err[-320:]))

    # D2b matplotlib.use('Qt5Agg') → 被拦（Qt5Agg 要 import PyQt5）
    env = sandbox_env(PG)
    rc, out, err = _run_script(
        'import matplotlib\nmatplotlib.use("Qt5Agg")\nprint("QT_OK")\n',
        env, PG, name='d2b')
    _check('D2b Qt5Agg 被拦',
           rc != 0 and 'QT_OK' not in out and _guard_frame(err, _GUI_FNS),
           'rc=%d qt_printed=%s guard_fn=%s err=%.220s'
           % (rc, 'QT_OK' in out, _guard_frame(err, _GUI_FNS), err[-320:]))

    # D3 正常 Agg 绘图不被误拦（显式 use('Agg', force=True)）
    env = sandbox_env(PG)
    d3_png = os.path.join(PG, 'd3.png')
    if os.path.exists(d3_png):
        os.remove(d3_png)
    rc, out, err = _run_script(
        'import matplotlib\nmatplotlib.use("Agg", force=True)\n'
        "import matplotlib.pyplot as plt\n"
        "fig, ax = plt.subplots()\nax.plot([1])\n"
        "fig.savefig('d3.png')\nprint('AGG_OK')\n", env, PG, name='d3')
    _check('D3 Agg 不误拦',
           rc == 0 and 'AGG_OK' in out and os.path.exists(d3_png),
           'rc=%d png=%s err=%.200s' % (rc, os.path.exists(d3_png), err[-300:]))


# ===========================================================================
# E. 链路：apply --write → verify 子进程 → 脚本（守卫全程生效 + PYTHONPATH 合并）
# ===========================================================================
_NORMAL_FIG = ('import matplotlib.pyplot as plt\n'
               'fig, ax = plt.subplots(figsize=(6, 4))\n'
               'ax.plot([1, 2])\n'
               "fig.savefig('out.png')\n"
               "print('DONE')\n")


def _test_e():
    # E3 工具单测：sandbox_env 必须把 沙箱目录 与 <树>/src 都并进 PYTHONPATH
    env = sandbox_env(PG, extra_pythonpath=[SRC])
    pp = env['PYTHONPATH'].split(os.pathsep)
    _check('E3 PYTHONPATH 合并（sandbox+src 并存）',
           SANDBOX_DIR in pp and SRC in pp,
           'pp=%s' % env['PYTHONPATH'])

    # E1 正常链路：apply --write（沙箱 env）→ verify 子进程跑脚本 → 写回成功
    chain = os.path.join(PG, 'chain')
    os.makedirs(chain, exist_ok=True)
    os.makedirs(os.path.join(chain, 'tmp'), exist_ok=True)   # TMP/TEMP 重定向目标
    fig_path = os.path.join(chain, 'fig.py')
    _write(fig_path, _NORMAL_FIG)
    params = os.path.join(chain, 'params.json')
    _write(params,
           '{"version": 4, "script": "fig.py", "fig_index": 0, '
           '"figsize_in": [8.5, 5.5]}')
    env = sandbox_env(chain, extra_pythonpath=[SRC], log_path=LOG)
    before = _read_text(fig_path)
    r = subprocess.run([_PY, '-m', 'mpltweak', 'apply', fig_path,
                        '--params', params, '--write', '--timeout', '60'],
                       capture_output=True, timeout=180, cwd=chain,
                       env=_pin_child_env(dict(env)),
                       stdin=subprocess.DEVNULL)
    o = _decode(r.stdout)
    e = _decode(r.stderr)
    after = _read_text(fig_path)
    _check('E1 apply 链正常写回（verify 子进程沙箱内成功）',
           r.returncode == 0 and 'figsize=(8.5, 5.5)' in after
           and os.path.exists(os.path.join(chain, 'out.png')),
           'rc=%d wrote=%s png=%s out=%.150s err=%.150s' % (
               r.returncode, 'figsize=(8.5, 5.5)' in after,
               os.path.exists(os.path.join(chain, 'out.png')), o[-180:],
               e[-180:]))

    # E2 恶意链路：脚本带外部写副作用 → verify 子进程（完整守卫）拦截 → apply 失败回滚
    evil_fig = os.path.join(chain, 'fig_evil.py')
    evil_out = os.path.join(OUT, 'evil2.txt')
    _write(evil_fig,
           _NORMAL_FIG.replace("import matplotlib.pyplot as plt\n",
                               "import matplotlib.pyplot as plt\n"
                               "open(%r, 'w').write('x')\n" % evil_out))
    params2 = os.path.join(chain, 'params2.json')
    _write(params2,
           '{"version": 4, "script": "fig_evil.py", "fig_index": 0, '
           '"figsize_in": [8.5, 5.5]}')
    eb_before = _read_text(evil_fig)
    mark = len(_log_lines())
    r2 = subprocess.run([_PY, '-m', 'mpltweak', 'apply', evil_fig,
                         '--params', params2, '--write', '--timeout', '60'],
                        capture_output=True, timeout=180, cwd=chain,
                        env=_pin_child_env(dict(env)),
                        stdin=subprocess.DEVNULL)
    e2 = _decode(r2.stderr)
    eb_after = _read_text(evil_fig)
    new2 = _log_lines()[mark:]
    logged = _blocked(new2, 'open(write)', evil_out)
    _check('E2 恶意链被 verify 子进程守卫拦下 + 回滚',
           r2.returncode != 0 and eb_before == eb_after
           and not os.path.exists(evil_out) and logged,
           'rc=%d unchanged=%s evil2_exists=%s logged=%s err=%.200s' % (
               r2.returncode, eb_before == eb_after,
               os.path.exists(evil_out), logged, e2[-300:]))


# ===========================================================================
# F. 无人值守：input() 读即 EOFError（t10 防卡超时 / na_stdin）
# ===========================================================================
def _test_f():
    env = sandbox_env(PG, log_path=LOG, no_stdin=True)
    rc, out, err = _run_script('x = input("prompt")\nprint("GOT", x)\n',
                               env, PG, name='f1')
    _check('F1 input() 读即 EOFError',
           rc != 0 and 'EOFError' in err and 'GOT' not in out,
           'rc=%d got_printed=%s err=%.160s' % (rc, 'GOT' in out, err[-220:]))

    # F1b 未开 no_stdin 且 stdin=DEVNULL → input() 读 EOF → EOFError（**不卡**，
    #    不会被拖到超时 —— t10 的 DEVNULL 兜底路径）
    env = sandbox_env(PG)
    rc, out, err = _run_script('x = input("prompt")\nprint("GOT", repr(x))\n',
                               env, PG, name='f1b')
    _check('F1b stdin=DEVNULL 不卡（自然 EOF 失败，非超时）',
           rc != 0 and 'EOFError' in err and 'GOT' not in out,
           'rc=%d out=%.120s err=%.120s' % (rc, out.strip()[:140],
                                            err.strip()[-140:]))


# ===========================================================================
# G. 子进程输出编码钉死（t15 根因哨兵 —— 回退了这里，A/C/D 会再次假红）
# ===========================================================================
def _test_g():
    # G1 两个 env 构造器都必须显式钉死 PYTHONIOENCODING（不靠宿主环境）
    env_on = sandbox_env(PG)
    env_off = plain_env()
    _check('G1 子进程 IO 编码钉死 utf-8',
           env_on.get('PYTHONIOENCODING') == _IO_ENC
           and env_off.get('PYTHONIOENCODING') == _IO_ENC,
           'sandbox=%r plain=%r expect=%r'
           % (env_on.get('PYTHONIOENCODING'),
              env_off.get('PYTHONIOENCODING'), _IO_ENC))

    # G2 子进程写出的中文必须**UTF-8 严格可解**（旧版本是 cp936 字节 → 测试侧按
    #    utf-8 解出 U+FFFD，正是七项假红的根因）。这里在**字节层**验证，不经解码器。
    marker = '沙箱中文回环 chk'
    env = sandbox_env(PG, log_path=LOG)
    payload = ('import sys\n'
               "sys.stdout.write('OUT ' + %r + '\\n')\n"
               "sys.stderr.write('ERR ' + %r + '\\n')\n" % (marker, marker))
    rc, raw_out, raw_err = _run_raw(payload, env, PG, name='g2')
    try:
        out = raw_out.decode('utf-8')      # 严格解 —— 有非法字节直接抛
        err = raw_err.decode('utf-8')
        strict = True
    except UnicodeDecodeError:
        out = err = ''
        strict = False
    _check('G2 子进程中文输出 UTF-8 无损（跨编码哨兵）',
           rc == 0 and strict and marker in out and marker in err,
           'rc=%d strict_utf8=%s raw_out=%.90r raw_err=%.90r'
           % (rc, strict, raw_out[:110], raw_err[:110]))

    # G3 **宿主后端投毒**也必须被硬钉回 Agg（不是 setdefault）—— 本机装了 PyQt5/
    #    tkinter，宿主 MPLBACKEND 一旦是 Qt5Agg/TkAgg，子进程就会落到交互后端，
    #    plt.show()/建图即真开窗、抢焦点与鼠标（2026-10-12 事故同类隐患）。
    #    同时验证 NOGUI 开关只能由**参数**决定：宿主残留 NOGUI=1 不许把禁 GUI 层关掉。
    poison = {'MPLBACKEND': 'Qt5Agg', 'MPLTWEAK_SANDBOX_NOGUI': '1'}
    env_p = sandbox_env(PG, base_env=poison)
    env_p_off = plain_env(base_env=poison)
    _check('G3 宿主 MPLBACKEND 投毒 → 子进程仍硬钉 Agg',
           env_p.get('MPLBACKEND') == 'Agg'
           and env_p_off.get('MPLBACKEND') == 'Agg'
           and 'MPLTWEAK_SANDBOX_NOGUI' not in env_p,
           'sandbox=%r plain=%r nogui=%r'
           % (env_p.get('MPLBACKEND'), env_p_off.get('MPLBACKEND'),
              env_p.get('MPLTWEAK_SANDBOX_NOGUI')))


# ===========================================================================
# 主流程
# ===========================================================================
def main():
    _fresh_dirs()
    print('沙箱测试: 游乐场=%s 游乐场外=%s 输出编码=%s' % (PG, OUT, _IO_ENC))
    _test_a()
    _test_b()
    _test_c()
    _test_d()
    _test_e()
    _test_f()
    _test_g()
    if _FAIL:
        print('FAILED | %d 项失败: %s' % (len(_FAIL), '; '.join(_FAIL)))
        sys.exit(1)
    print('ALL PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
