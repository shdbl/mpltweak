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
# 脚本入口：`python tests/test_cli_output.py` 等价于跑一遍本文件（pytest 驱动）
# ----------------------------------------------------------------------
if __name__ == '__main__':
    sys.exit(pytest.main([os.path.abspath(__file__), '-q', '--no-header']))
