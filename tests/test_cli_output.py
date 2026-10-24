# -*- coding: utf-8 -*-
"""A3 / MAINTENANCE §25.15：`apply` 的**输出归属**契约（人读错误走 stderr）。

为什么要这份用例
================
`mpltweak apply foo.py > out.txt` 曾经把**人读错误**也写进 out.txt（错误行原先和正常
报告一样走 stdout），于是管道/重定向无法干净区分"数据 / 错误"。2026-10-15 用户的结论
是"这事进 0.1.12"，契约就一条：

  * **stdout** = 数据/报告：成功摘要、逐处清单、预览的「会改：…」与骨架、`--json` 载荷；
  * **stderr** = 人读错误：失败、回滚、拒写（bad_params / 未写回 / 需要退块却没落地）、
    参数文件坏、外部改动被跳过、验证失败及其归因、用法/输入错误。

判据是"**这次请求有没有兑现**"，不是"消息长得凶不凶"：模糊的那几类（`--verify-fast`
降级提醒、某字段没动、布局引擎提示）算**过程披露**，仍在 stdout —— 它们既不宣告成功也不
宣告失败，成功与否由退出码 + stderr 是否为空共同决定；把它们塞进 stderr 会让
"stderr 非空 ⇒ 有错"这条管道规则失效。完整边界表见 `docs/exec-boundaries.md` 的
「输出归属：stdout 与 stderr」。

反向验证（teeth，手工一步，约 20 秒）
------------------------------------
把 `src/mpltweak/apply.py` 里 `_emit_err` 的落点改回 stdout（函数体那一行
`sys.stderr.write('%s\n' % (_l,))` → `print(_l)`），再跑本文件：**8 条必红**
（5 条参数化失败出口 + layout 拒写 + 外部改动 + 重跑失败），因为错误行又回到了
stdout、stderr 变空。改回来即 14 条全绿。
（只把**某一处** `_emit_err(...)` 改回 `print(...)` 也能咬 —— 但要点名那条出口
被本文件的哪条用例覆盖，否则可能改到没人走的分支。）

调用方式
--------
`python -m pytest -q tests/test_cli_output.py`（本文件是 pytest 函数式；`__main__`
下也能当脚本跑，输出与退出码同 pytest 结论）。
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap

import pytest

# GBK 控制台兜底（与 test_agent_api.py / test_writeback.py 同款）：
# 本文件会把子进程输出按 `utf-8` + `errors='replace'` 解码（非法字节 → U+FFFD），
# 而本地 GBK 控制台一 print 到 U+FFFD 就 UnicodeEncodeError，整套用例从 ALL PASS
# 变成 rc=1 的**假红**（T13 的老现场：CI 永远绿、本地必崩）。这里给两条标准流加
# `errors='replace'`，让"打不出来"退化成"打成 ?"而不是崩。
# 守护它的是 tests/test_hygiene.py::test_test_suites_cannot_crash_gbk_console
# （按文件名逐个检查：用了 replace 解码就必须带这段 reconfigure）。
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(errors='replace')
    except (AttributeError, ValueError, OSError):
        pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, 'src')
PY = sys.executable

# 断言的是中文人读文案 → 把界面语言钉死（与 test_agent_api.py 同一理由：
# messages 的探测顺序是 --lang > MPLTWEAK_LANG > locale > LANG > en）。
_LANG = 'zh'

_SCRIPT = textwrap.dedent('''\
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    fig = plt.figure(figsize=(8, 4))
    ax = fig.add_axes([0.10, 0.10, 0.70, 0.70])
    ax.plot([1, 2, 3], [1, 4, 9])
    ax.set_title('A', fontsize=10)
    fig.savefig('o.png')
''')

# 布局引擎冲突脚本：`--strict --write` 是"拒写"出口之一（rc=2）。
_LAYOUT = textwrap.dedent('''\
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import numpy as np

    fig, ax = plt.subplots(figsize=(5, 4), constrained_layout=True)
    ax.plot(np.arange(10))
    fig.savefig('o.png')
''')

# D1（0.1.14）：**只改非几何字段**的布局冲突脚本 —— 带一行可原位改的字号，
# 用来证明"冲突 + 只改字号"照样写回（别把本来好用的路堵死）。
_LAYOUT_FONT = textwrap.dedent('''\
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import numpy as np

    fig, ax = plt.subplots(figsize=(5, 4), constrained_layout=True)
    ax.plot(np.arange(10))
    ax.set_title('A', fontsize=10)
    fig.savefig('o.png')
''')

# D1：冲突 + `add_axes` 的**字面量位置**（与参数里的目标值一致）—— 静态可证
# "写了也是空改"，不该被拒绝。
_LAYOUT_ADD = textwrap.dedent('''\
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import numpy as np

    fig = plt.figure(figsize=(6, 4), constrained_layout=True)
    ax = fig.add_axes([0.10, 0.10, 0.70, 0.70])
    ax.plot(np.arange(10))
    fig.savefig('o.png')
''')

# J2（0.1.14）：**双图**脚本 —— `describe --all-figs -o` 的产物要能被 `apply` 吃回去。
_TWO = textwrap.dedent('''\
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import numpy as np

    f0 = plt.figure(figsize=(4, 3))
    a0 = f0.add_axes([0.10, 0.10, 0.80, 0.80])
    a0.plot(np.arange(5))
    f1 = plt.figure(figsize=(4, 3))
    a1 = f1.add_axes([0.10, 0.10, 0.80, 0.80])
    a1.plot(np.arange(5))
    f0.savefig('o0.png')
    f1.savefig('o1.png')
''')

# J1（0.1.14）：零改动夹具。0.1.15 起夹具必须换形态：**单图纯 `plt.*` 已经可写回**
# （N1：全文件没有一行显式建图 ⇒ `plt.gcf()` 可证），拿它当"保守拒写"夹具已经失效。
# 这里改用**仍然**证不出目标的形态：图在**函数体**里建 ⇒ 建轴/绘图调用不在模块层直
# 线执行一次，隐式建图那档 fail-closed（实测 `nothing_applied` / rc=1 / 零改动）。
_IMPLICIT = textwrap.dedent('''\
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    def draw():
        plt.plot([0, 1], [0, 1])
        plt.title('implicit in a function')

    draw()
    plt.savefig('o.png')
''')

# 0.1.15 / N1：老夹具原样留着当**新能力**的正面用例（单图 + 满篇 `plt.*`）。
_IMPLICIT_PURE = textwrap.dedent('''\
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    plt.plot([0, 1], [0, 1])
    plt.title('implicit')
    plt.xlim(0, 1)
    plt.ylim(0, 1)
    plt.grid(True)
    plt.savefig('o.png')
''')


def _env(**over):
    """子进程环境：**硬钉 Agg**（宿主 MPLBACKEND 可能是 Qt5Agg/TkAgg → 抢鼠标）。

    与 tests/test_hygiene.py 的子进程闸门同一口径（`env=` 里必须看得见
    `MPLBACKEND=Agg`）；另钉语言与编码，别让宿主 locale 决定结论。
    """
    out = dict(os.environ)
    out['MPLBACKEND'] = 'Agg'
    out['MPLTWEAK_LANG'] = _LANG
    out['PYTHONIOENCODING'] = 'utf-8'
    out['PYTHONPATH'] = SRC + (os.pathsep + out['PYTHONPATH']
                               if out.get('PYTHONPATH') else '')
    out.update(over)
    return out


def _run(cwd, *args, **over):
    """跑一次真 CLI，返回 (rc, stdout, stderr)（都按 utf-8 解）。"""
    r = subprocess.run([PY, '-m', 'mpltweak.cli', *args], cwd=cwd,
                       capture_output=True, env=_env(**over))
    return (r.returncode,
            (r.stdout or b'').decode('utf-8', 'replace'),
            (r.stderr or b'').decode('utf-8', 'replace'))


def _write(path, text):
    """按 utf-8 + LF 写文件（夹具统一写法，免得每处重复 newline='\\n'）。"""
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        f.write(text)


_SCRATCH_SEQ = [0]


def _scratch_dir():
    """在临时根下建一个**进程内唯一、且可写**的目录。

    ⚠ 不用 ``tempfile.mkdtemp``：它建出来的目录带自己的 DACL，在 DSH 的 Windows
    沙箱（workspace-write）里**建得成、里面写不进** —— 实测 ``open(d/fig.py,'w')``
    → ``PermissionError: [Errno 13]``，整个文件 14 条全 ERROR（t3 的覆盖检查把本
    文件照出来之后才发现的）。改用 ``os.makedirs``：新目录继承父目录的 DACL，
    沙箱里与 CI 上都可写。唯一性靠 pid + 序号 + ``exist_ok=False`` 撞了就重试。
    """
    base = tempfile.gettempdir()
    while True:
        _SCRATCH_SEQ[0] += 1
        d = os.path.join(base, 'mpltweak_cliout_%d_%d'
                         % (os.getpid(), _SCRATCH_SEQ[0]))
        try:
            os.makedirs(d)
            return d
        except FileExistsError:
            continue


@pytest.fixture()
def work():
    """一份可写回的脚本 + 一份正常参数 + 两份坏参数 + 一份布局冲突脚本。

    ⚠ 故意**不用** pytest 的 ``tmp_path``：它走 ``tmp_path_factory``（basetemp +
    每例子目录），在受限令牌 / Windows 沙箱这类环境里连 ``os.scandir(basetemp)``
    都会被拒（实测 WinError 5），整个文件会变成 14 条 ERROR —— 而本文件的被测对象
    是 CLI 的输出流，不是临时目录取法。取目录走 ``_scratch_dir()``（见它的注释：
    也不用 ``mkdtemp``，理由写在里面），``finally`` 里清干净。
    """
    d = _scratch_dir()
    try:
        _write(os.path.join(d, 'fig.py'), _SCRIPT)
        _write(os.path.join(d, 'p.json'), json.dumps(
            {'version': 4, 'script': 'fig.py', 'fig_index': 0, 'n_figs': 1,
             'axes': [{'index': 0, 'pos': [0.2, 0.2, 0.6, 0.6]}]},
            ensure_ascii=False))
        _write(os.path.join(d, 'p_bad.json'), json.dumps(
            {'script': 'fig.py', 'fig_index': 0, 'n_figs': 1, 'axes': []},
            ensure_ascii=False))
        _write(os.path.join(d, 'p_broken.json'),
               '{"version": 4, "script": "fig.py", "axes": [,')
        # 显式 --style inplace 时"有字段落不了地"（spines 原位写不了）→ rc=1
        _write(os.path.join(d, 'p_unlanded.json'), json.dumps(
            {'version': 4, 'script': 'fig.py', 'fig_index': 0, 'n_figs': 1,
             'axes': [{'index': 0, 'pos': [0.2, 0.2, 0.6, 0.6],
                       'spines': {'top': False}}]}, ensure_ascii=False))
        _write(os.path.join(d, 'layout.py'), _LAYOUT)
        yield d
    finally:
        shutil.rmtree(d, ignore_errors=True)


def _shared_lines(out, err):
    """stderr 的行里，有几行**同时**出现在 stdout（= 错误行漏进了 stdout）。

    比"不含某个关键词"更结实：文案/语言改了这条断言也不会失效。
    """
    so = {l.strip() for l in (out or '').splitlines() if l.strip()}
    se = {l.strip() for l in (err or '').splitlines() if l.strip()}
    return sorted(se & so)


# ----------------------------------------------------------------------
# ① 成功 / 预览：stderr 必须**为空**
# ----------------------------------------------------------------------
def test_preview_keeps_stderr_empty(work):
    rc, out, err = _run(work, 'apply', 'fig.py', '--params', 'p.json')
    assert rc == 0, (rc, out[-300:], err[-300:])
    assert err == '', '预览成功时 stderr 必须一个字都不打：%r' % err[:300]
    assert out.strip() != '', '报告（会改：…）必须在 stdout'


def test_write_success_keeps_stderr_empty(work):
    rc, out, err = _run(work, 'apply', 'fig.py', '--params', 'p.json',
                        '--write', '--verify-fast')
    assert rc == 0, (rc, out[-300:], err[-300:])
    assert err == '', '写回成功时 stderr 必须一个字都不打：%r' % err[:300]
    assert out.strip() != '', '成功摘要必须在 stdout'
    assert not any(l.startswith('!!') for l in out.splitlines()), out[-300:]


def test_no_change_keeps_stderr_empty(work):
    """连跑两次：第二次 no_change，stderr 仍为空（成功路径一视同仁）。"""
    _run(work, 'apply', 'fig.py', '--params', 'p.json', '--write',
         '--verify-fast')
    rc, out, err = _run(work, 'apply', 'fig.py', '--params', 'p.json',
                        '--write', '--verify-fast')
    assert rc == 0 and err == '', (rc, repr(err[:200]))
    assert 'no_change' in out or out.strip() != ''


# ----------------------------------------------------------------------
# ② 失败：错误行只在 stderr，stdout 不含（也不许两边都有）
# ----------------------------------------------------------------------
@pytest.mark.parametrize('args,label', [
    (('apply', 'nope.py', '--params', 'p.json'), '脚本不存在 rc=2'),
    (('apply', 'fig.py', '--params', 'p_bad.json', '--write'), '参数缺 version rc=2'),
    (('apply', 'fig.py', '--params', 'p_broken.json', '--write'), '参数 JSON 坏 rc=2'),
    (('apply', 'fig.py', '--params', 'p.json', '--write', '--no-verify',
      '--verify-fast'), '互斥 flag rc=2'),
    (('apply', 'fig.py', '--params', 'p_unlanded.json', '--write', '--style',
      'inplace'), '显式 inplace 有字段没落地 rc=1'),
])
def test_failure_lines_are_on_stderr_only(work, args, label):
    rc, out, err = _run(work, *args)
    assert rc in (1, 2), (label, rc, out[-300:], err[-300:])
    assert err.strip() != '', '%s：人读错误必须在 stderr（现在 stderr 是空的）' % label
    dup = _shared_lines(out, err)
    assert not dup, '%s：这些错误行两边都有（漏进了 stdout）：%r' % (label, dup)


def test_layout_conflict_refusal_is_on_stderr(work):
    """`--strict --write` 撞布局引擎 → 拒写（rc=2），拒写理由只在 stderr。"""
    with open(os.path.join(work, 'l.json'), 'w', encoding='utf-8',
              newline='\n') as f:
        json.dump({'version': 4, 'script': 'layout.py', 'fig_index': 0,
                   'n_figs': 1,
                   'axes': [{'index': 0, 'pos': [0.2, 0.2, 0.6, 0.6]}]},
                  f, ensure_ascii=False)
    rc, out, err = _run(work, 'apply', 'layout.py', '--params', 'l.json',
                        '--write', '--strict')
    assert rc == 2, (rc, out[-300:], err[-300:])
    assert err.strip() != ''
    assert not _shared_lines(out, err), _shared_lines(out, err)


# ----------------------------------------------------------------------
# ①b D1（0.1.14，用户拍板 B）：布局引擎冲突按**写回路径**分流
#     冲突 + 要写轴位置   ⇒ 默认拒绝（rc=1、不写盘、脚本字节不变、两条出路）
#     冲突 + 只写非几何   ⇒ 照旧写回（不拒绝）
#     --force            ⇒ 跳过这条检查，并在输出里说清（不静默）
#     无冲突 + 写位置     ⇒ 行为完全不变
#     冲突 + 位置恰好相等 ⇒ 不拒绝（静态或实测证得出"写了也是空改"）
# ----------------------------------------------------------------------
def _read_bytes(path):
    with open(path, 'rb') as f:
        return f.read()


def _dump_params(work, name, payload):
    with open(os.path.join(work, name), 'w', encoding='utf-8',
              newline='\n') as f:
        json.dump(payload, f, ensure_ascii=False)


def test_layout_conflict_position_write_refused_by_default(work):
    """冲突 + 这次要写轴位置 → **默认拒绝**：rc=1、脚本一个字节不动。"""
    _dump_params(work, 'lpos.json',
                 {'version': 4, 'script': 'layout.py', 'fig_index': 0,
                  'n_figs': 1,
                  'axes': [{'index': 0, 'pos': [0.125, 0.11, 0.352, 0.77]}]})
    p = os.path.join(work, 'layout.py')
    before = _read_bytes(p)
    rc, out, err = _run(work, 'apply', 'layout.py', '--params', 'lpos.json',
                        '--write')
    assert rc == 1, '拒写走"结果需要处理"档 = rc=1：%s' % ((rc, err[-400:]),)
    assert _read_bytes(p) == before, '拒写必须**一个字节都不动**脚本'
    assert not os.path.exists(os.path.join(work, 'layout.tweak.bak')), \
        '没写盘就不该留备份'
    # 人话：为什么拒 + 两条出路（去掉引擎 / --force）都得在 stderr 上说清
    for want in ('constrained_layout', '轴位置', '--force', '出路'):
        assert want in err, 'stderr 缺 %r：%r' % (want, err[-500:])
    assert '去掉 constrained_layout' in err or '去掉 constrained_layout=True' in err
    assert not _shared_lines(out, err), _shared_lines(out, err)
    assert '已写回' not in out, '没落地就不许说"已写回"：%r' % out[-300:]


def test_layout_conflict_non_geometry_write_still_lands(work):
    """冲突 + 只写非几何字段（标题字号）→ **不拒绝**，照旧写回（rc=0）。"""
    _write(os.path.join(work, 'layout_font.py'), _LAYOUT_FONT)
    _dump_params(work, 'lfont.json',
                 {'version': 4, 'script': 'layout_font.py', 'fig_index': 0,
                  'n_figs': 1,
                  'axes': [{'index': 0, 'title_fontsize': 14.0}]})
    p = os.path.join(work, 'layout_font.py')
    before = _read_bytes(p)
    rc, out, err = _run(work, 'apply', 'layout_font.py', '--params',
                        'lfont.json', '--write')
    assert rc == 0, (rc, out[-400:], err[-400:])
    assert err == '', '成功路径 stderr 必须为空：%r' % err[:300]
    after = _read_bytes(p)
    assert after != before, '非几何字段必须真的写进去了'
    assert b'fontsize=14' in after, after[-500:]
    assert '跳过布局引擎冲突检查' not in out, '没加 --force 就不许说跳过'


def test_layout_conflict_force_skips_the_check(work):
    """`--force`：跳过这条检查、强制写回，并在输出里**明确声明**（不静默）。"""
    _dump_params(work, 'lpos.json',
                 {'version': 4, 'script': 'layout.py', 'fig_index': 0,
                  'n_figs': 1,
                  'axes': [{'index': 0, 'pos': [0.125, 0.11, 0.352, 0.77]}]})
    p = os.path.join(work, 'layout.py')
    before = _read_bytes(p)
    rc, out, err = _run(work, 'apply', 'layout.py', '--params', 'lpos.json',
                        '--write', '--force')
    assert rc == 0, (rc, out[-400:], err[-400:])
    after = _read_bytes(p)
    assert after != before, '--force 必须真的写回'
    assert b'set_position([0.1250, 0.1100, 0.3520, 0.7700])' in after, \
        after[-500:]
    assert '跳过布局引擎冲突检查' in out and '--force' in out, \
        '--force 放行必须大声说（不许静默）：%r' % out[:400]


def test_no_layout_conflict_position_write_unchanged(work):
    """无冲突的脚本写位置：行为**完全不变**（rc=0、无跳过声明）。"""
    p = os.path.join(work, 'fig.py')
    rc, out, err = _run(work, 'apply', 'fig.py', '--params', 'p.json',
                        '--write', '--verify-fast')
    assert rc == 0, (rc, out[-300:], err[-300:])
    assert err == '', err[:300]
    with open(p, encoding='utf-8') as f:
        assert 'add_axes([0.2, 0.2, 0.6, 0.6])' in f.read()
    assert '跳过布局引擎冲突检查' not in out
    assert '轴位置' not in out, '没有冲突就不许打布局引擎的话'


def test_layout_conflict_position_already_equal_is_not_refused(work):
    """冲突 + 参数里的位置**恰好等于源码现值** → 不拒绝（写了也是空改）。

    这条守的是"别把本来好用的路堵死"：`add_axes` 的字面量静态可证，不必重跑
    脚本、也不该拦下。
    """
    _write(os.path.join(work, 'layout_add.py'), _LAYOUT_ADD)
    _dump_params(work, 'ladd.json',
                 {'version': 4, 'script': 'layout_add.py', 'fig_index': 0,
                  'n_figs': 1,
                  'axes': [{'index': 0, 'pos': [0.10, 0.10, 0.70, 0.70]}]})
    rc, out, err = _run(work, 'apply', 'layout_add.py', '--params', 'ladd.json',
                        '--write', '--verify-fast')
    assert rc == 0, (rc, out[-400:], err[-400:])
    assert '轴位置' not in err, '位置已经一致时不该拒绝：%r' % err[-400:]



def test_external_modification_notice_is_on_stderr(work):


    """脚本比参数新（外部改动）→ 跳过写回（rc=1），提示只在 stderr。"""
    import time
    future = time.time() + 5
    os.utime(os.path.join(work, 'fig.py'), (future, future))
    rc, out, err = _run(work, 'apply', 'fig.py', '--params', 'p.json',
                        '--write', '--verify-fast')
    assert rc == 1, (rc, out[-300:], err[-300:])
    assert err.strip() != ''
    assert not _shared_lines(out, err), _shared_lines(out, err)


def test_rollback_failure_is_on_stderr(work):
    """验证重跑失败（脚本缺参数跑不通）→ 失败与归因都在 stderr，stdout 不掺。

    这条走的是"写回后重跑 → 跑不通 → 回滚/归因"那条路：脚本读 `sys.argv[1]`，
    没给参数 ⇒ 重跑层失败 ⇒ `reason=fail` + 归因行。
    """
    broken = textwrap.dedent('''\
        import sys
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt

        fig = plt.figure(figsize=(8, 4))
        ax = fig.add_axes([0.10, 0.10, 0.70, 0.70])
        ax.plot([1, 2, 3], [1, 4, 9])
        fig.savefig('o.png')
        print('data=%s' % sys.argv[1])
    ''')
    with open(os.path.join(work, 'needs_args.py'), 'w', encoding='utf-8',
              newline='\n') as f:
        f.write(broken)
    with open(os.path.join(work, 'n.json'), 'w', encoding='utf-8',
              newline='\n') as f:
        json.dump({'version': 4, 'script': 'needs_args.py', 'fig_index': 0,
                   'n_figs': 1,
                   'axes': [{'index': 0, 'pos': [0.2, 0.2, 0.6, 0.6]}]},
                  f, ensure_ascii=False)
    rc, out, err = _run(work, 'apply', 'needs_args.py', '--params', 'n.json',
                        '--write', '--timeout', '30')
    assert rc == 1, (rc, out[-400:], err[-400:])
    assert err.strip() != '', '失败与归因必须在 stderr：%r' % out[-300:]
    assert not _shared_lines(out, err), _shared_lines(out, err)


# ----------------------------------------------------------------------
# ③ `--json`：载荷一字不动地留在 stdout；人读行不许污染它
# ----------------------------------------------------------------------
def test_json_payload_stays_on_stdout(work):
    rc, out, err = _run(work, 'apply', 'fig.py', '--params', 'p.json',
                        '--write', '--verify-fast', '--json')
    assert rc == 0, (rc, out[-300:], err[-300:])
    payload = json.loads(out)          # stdout 必须**只有** JSON
    assert payload.get('ok') is True
    # 键集不变：这些是 agent 侧已依赖的键（少一个就是破坏契约）
    for key in ('script', 'ok', 'files'):
        assert key in payload, sorted(payload)
    assert 'error_code' not in payload, '成功载荷不许带 error_code：%r' % sorted(payload)
    entry = payload['files'][0]
    for key in ('params', 'reason', 'style', 'changes', 'applied',
                'block_lines', 'needs_block', 'pending', 'anchor', 'layers',
                'err', 'verified', 'verify_mode', 'verify_passes',
                'semantic_basis', 'semantic', 'warnings', 'attribution',
                'next_step'):
        assert key in entry, sorted(entry)
    # `--json` 时人读**报告**按既有约定走 stderr（`sys.stdout = sys.stderr`）——
    # 这里的契约是"stdout 只有 JSON、载荷一字不动"，不是"stderr 为空"。
    assert out.lstrip().startswith('{'), 'stdout 必须以 JSON 开头：%r' % out[:80]
    assert not _shared_lines(out, err), _shared_lines(out, err)


def test_json_failure_keeps_payload_and_stderr_clean(work):
    rc, out, err = _run(work, 'apply', 'fig.py', '--params', 'p_bad.json',
                        '--write', '--json')
    assert rc == 2, (rc, out[-300:], err[-300:])
    payload = json.loads(out)          # 人读错误不许拼进 stdout
    assert payload.get('error_code') == 'bad_params'
    assert payload['files'][0].get('reason') == 'bad_params'
    assert err.strip() != '', '人读错误要打，但只在 stderr'
    assert not _shared_lines(out, err), _shared_lines(out, err)


def test_json_script_missing_emits_only_json(work):
    rc, out, err = _run(work, 'apply', 'nope.py', '--params', 'p.json',
                        '--json')
    assert rc == 2
    payload = json.loads(out)
    assert payload.get('error_code') == 'file_not_found'
    assert err.strip() == '', '这条出口只给 JSON（人读行一个字都不打）'


# ----------------------------------------------------------------------
# ⑨ F1：`--json` 的 `style` **永不为 null**（0.1.14）
#
# 口径（契约全文见 `docs/agent.md` 的 `--json` 一节 + `src/mpltweak/apply.py`
# 的 `_emit_json` docstring）：`style` = 这次**请求的通道**；真的走过写回时它被
# 实际采用的通道覆盖（auto 退块 ⇒ `block` + `auto_fallback=true`）。**每个
# `files[]` 条目都有值**，调用方不必逐条特判 null（旧缺陷：预览与两条早退路径
# 都曾给 null，agent 无法区分「没传」与「auto」）。
# ----------------------------------------------------------------------
def test_json_style_preview_is_requested_mode(work):
    rc, out, err = _run(work, 'apply', 'fig.py', '--params', 'p.json', '--json')
    assert rc == 0, (rc, out[-300:], err[-300:])
    entry = json.loads(out)['files'][0]
    assert entry['reason'] == 'preview', entry
    assert entry['style'] == 'auto', (
        '预览且没传 --style ⇒ 必须是默认的 auto（不是 null）：%r' % entry['style'])
    # 显式 --style block 时，预览如实报请求的 block（不是退化成 auto / null）
    rc2, out2, _ = _run(work, 'apply', 'fig.py', '--params', 'p.json',
                        '--style', 'block', '--json')
    entry2 = json.loads(out2)['files'][0]
    assert rc2 == 0 and entry2['reason'] == 'preview', (rc2, entry2)
    assert entry2['style'] == 'block', (
        '预览要报**请求的模式**：传了 --style block 就得是 block：%r'
        % entry2['style'])


def test_json_style_write_is_used_channel(work):
    rc, out, err = _run(work, 'apply', 'fig.py', '--params', 'p.json',
                        '--write', '--json')
    assert rc == 0, (rc, out[-300:], err[-300:])
    entry = json.loads(out)['files'][0]
    assert entry['reason'] in ('ok', 'best_effort', 'no_change'), entry
    assert entry['style'] == 'inplace', (
        '这份夹具（add_axes 明写位置）走原位改数字 ⇒ 写回后 style 必须是实际通道 '
        'inplace：%r' % entry['style'])
    assert entry['applied'] is True, entry


def test_json_style_never_null_on_early_exit(work):
    # 参数文件非法（bad_params）是**早退**路径：也要带"请求的模式"，否则调用方
    # 得逐条特判 null，而这类条目恰恰是 agent 最需要看的时候。
    rc, out, err = _run(work, 'apply', 'fig.py', '--params', 'p_bad.json',
                        '--style', 'inplace', '--json')
    assert rc == 2, (rc, out[-300:], err[-300:])
    entry = json.loads(out)['files'][0]
    assert entry['reason'] == 'bad_params', entry
    assert entry['style'] == 'inplace', (
        '早退路径要报请求的模式（传了什么就是什么），不许是 null：%r'
        % entry['style'])
    # 全量兜底：载荷里**任何** files[] 条目都不许出现 style=null
    for e in json.loads(out)['files']:
        assert e.get('style') is not None, e


# ----------------------------------------------------------------------
# J2（0.1.14，P1 报的 F2）：`describe --all-figs -o` 的产物必须能被 `apply` 吃回去
# ----------------------------------------------------------------------
def test_describe_all_figs_output_is_apply_compatible(work):
    """`describe --all-figs -o X.json` 顺手按每图落 `X.fig<k>.json` ⇒ 回路打通。

    修前：只写包装 `{"figures": [...]}`，`apply --params X.json` 一律 `no_axes`
    （rc=1、一字节不改）；
    修后：包装照旧写（agent 读它），**另加**每图一份（apply 认的命名）。
    """
    _write(os.path.join(work, 'two.py'), _TWO)
    rc, out, err = _run(work, 'describe', 'two.py', '--all-figs', '-o', 'all.json')
    assert rc == 0, (rc, out[-300:], err[-300:])
    fig0 = os.path.join(work, 'all.fig0.json')
    fig1 = os.path.join(work, 'all.fig1.json')
    assert os.path.exists(fig0) and os.path.exists(fig1), sorted(os.listdir(work))
    with open(fig1, encoding='utf-8') as f:
        p1 = json.load(f)
    assert p1.get('fig_index') == 1 and p1.get('n_figs') == 2, sorted(p1)
    assert 'axes' in p1, '每图一份必须是**单图**结构（顶层有 axes）：%r' % sorted(p1)
    # 真的喂回去：改第 2 张图的轴位置 → 写进源码
    p1['axes'][0]['pos'] = [0.2, 0.2, 0.6, 0.6]
    with open(fig1, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(p1, f, ensure_ascii=False)
    rc, out, err = _run(work, 'apply', 'two.py', '--params', 'all.fig1.json',
                        '--write', '--verify-fast')
    assert rc == 0, (rc, out[-400:], err[-400:])
    with open(os.path.join(work, 'two.py'), encoding='utf-8') as f:
        assert 'add_axes([0.2, 0.2, 0.6, 0.6])' in f.read()


# ----------------------------------------------------------------------
# J1（0.1.14，P1 报的 F1）：`ok=true` 但**一字节没改** —— 契约不许撒谎
# ----------------------------------------------------------------------
def test_zero_change_is_not_reported_as_success(work):
    """整批零改动（保守拒写）⇒ rc=1 / ok=false / error_code='nothing_applied'。

    修前实测：`reason='best_effort'`、`applied=false`、`changes=[]`、盘上字节不变，
    但 `ok=true`、`rc=0`、`error_code=null` —— 只看 ok/rc 的 agent 会当成"已写回"。
    """
    _write(os.path.join(work, 'imp.py'), _IMPLICIT)
    rc, out, err = _run(work, 'describe', 'imp.py', '-o', 'imp.json')
    assert rc == 0, (rc, out[-300:], err[-300:])
    with open(os.path.join(work, 'imp.json'), encoding='utf-8') as f:
        d = json.load(f)
    d['axes'][0]['title_fontsize'] = 13.0
    d['axes'][0]['xlim'] = [-0.5, 1.5]
    with open(os.path.join(work, 'imp.json'), 'w', encoding='utf-8',
              newline='\n') as f:
        json.dump(d, f, ensure_ascii=False)
    p = os.path.join(work, 'imp.py')
    before = _read_bytes(p)
    rc, out, err = _run(work, 'apply', 'imp.py', '--params', 'imp.json',
                        '--write', '--json')
    payload = json.loads(out)
    entry = payload['files'][0]
    assert _read_bytes(p) == before, '这个形态必须**一个字节都不改**（拒写）'
    assert entry.get('applied') is False, entry
    assert rc == 1, '零改动要落到"结果需要处理"档：%s' % ((rc, err[-300:]),)
    assert payload.get('ok') is False, '不许再报 ok=true：%r' % payload.get('ok')
    assert payload.get('error_code') == 'nothing_applied', payload.get('error_code')
    assert '未被改动' in err or 'NOT modified' in err, err[-300:]


def test_implicit_single_figure_is_writable(work):
    """N1（0.1.15）：只建一张图、满篇 `plt.*`（**没有一行显式建图**）⇒ 现在可证写回。

    修前：`_block_targets` 证不出目标（`fig_call is None` ⇒ `plt.gcf()` 不可证）⇒ 全拒、
    `nothing_applied`（这就是 J1 老夹具的形态）。修后：全文件恰好一张图、没有 close、
    没有额外建轴、那些调用都在模块层直线执行 ⇒ `plt.gcf()` 就是它 ⇒ 块里落
    `plt.gcf().axes[0].set_position(`，而且块注释如实写"没有显式建图调用"。
    """
    _write(os.path.join(work, 'imp1.py'), _IMPLICIT_PURE)
    rc, out, err = _run(work, 'describe', 'imp1.py', '-o', 'imp1.json')
    assert rc == 0, (rc, out[-300:], err[-300:])
    with open(os.path.join(work, 'imp1.json'), encoding='utf-8') as f:
        d = json.load(f)
    d['axes'][0]['pos'] = [0.2, 0.2, 0.6, 0.6]
    with open(os.path.join(work, 'imp1.json'), 'w', encoding='utf-8',
              newline='\n') as f:
        json.dump(d, f, ensure_ascii=False)
    rc, out, err = _run(work, 'apply', 'imp1.py', '--params', 'imp1.json',
                        '--write', '--json')
    payload = json.loads(out)
    entry = payload['files'][0]
    with open(os.path.join(work, 'imp1.py'), encoding='utf-8') as f:
        src = f.read()
    assert rc == 0 and payload.get('ok') is True, (rc, out[-400:], err[-400:])
    assert entry.get('applied') is True, entry
    assert 'plt.gcf().axes[0].set_position(' in src, src[-500:]
    assert '# 目标定位（SFA' in src, '块注释必须说"隐式建图"，不许照抄"只有一次建图"'


def test_no_change_still_counts_as_success(work):
    """反面对照：**参数已经一致**（no_change）仍是成功 —— 不许被 J1 误伤。

    这里刻意**手写最小参数**（只给 `pos`，且等于源码里 `add_axes` 的字面量），不再拿
    `describe` 的整份产物当输入：整份产物里含 `lines[].linewidth/color` 这类"源码没写、
    取的是 matplotlib 默认值"的字段，它们的可证伪判据由 writeback 侧决定（会随别人的
    改动在"no_change"和"插块"之间摆动），与本用例要钉的东西（零改动的档位）无关。
    """
    _write(os.path.join(work, 'one.py'), _SCRIPT)
    with open(os.path.join(work, 'one.json'), 'w', encoding='utf-8',
              newline='\n') as f:
        json.dump({'version': 4, 'script': 'one.py', 'fig_index': 0,
                   'n_figs': 1,
                   'axes': [{'index': 0, 'pos': [0.10, 0.10, 0.70, 0.70]}]},
                  f, ensure_ascii=False)
    before = _read_bytes(os.path.join(work, 'one.py'))
    rc, out, err = _run(work, 'apply', 'one.py', '--params', 'one.json',
                        '--write', '--json')
    payload = json.loads(out)
    assert rc == 0, (rc, err[-300:])
    assert _read_bytes(os.path.join(work, 'one.py')) == before
    assert payload.get('ok') is True, payload.get('ok')
    assert 'error_code' not in payload, payload.get('error_code')
    assert payload['files'][0]['reason'] == 'no_change', payload['files'][0]


# ----------------------------------------------------------------------
# 脚本入口：`python tests/test_cli_output.py` 等价于跑一遍本文件（pytest 驱动）
# ----------------------------------------------------------------------
if __name__ == '__main__':
    sys.exit(pytest.main([os.path.abspath(__file__), '-q', '--no-header']))
