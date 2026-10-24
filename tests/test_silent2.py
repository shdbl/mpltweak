# -*- coding: utf-8 -*-
"""S2（0.1.16）：0.1.15 静默 except 审计留下的**两条债**。

① `writeback._pending_vs_live` 删旧 `_verify_state.json` 失败仍静默（审计 §4）。
   这是整棵树里**唯一"静默但会影响结论"**的一处：残留状态文件可能被
   `verify_semantic` 回读，"参数 vs 现场"的判定于是沿用上一次 —— 用户只看到
   "待验/已验"莫名其妙。修法：给该函数加 `warnings` 形参并穿到
   `_writeback_inplace`，失败写进 `files[].warnings`（**判定不变**，只留痕）。

② `apply.py` 的 per-file `os.utime` 没有保护（审计 §5）：失败会被顶层
   `guard_json_main` 兜成 `error_code=internal_error` + `attribution=tool_bug`
   +「这是工具的 bug」（rc=2）—— **一次文件系统失败被归因成工具缺陷**（误归因）。
   修法：`except OSError` → `files[].warnings`（既有 `mtime_sync_fail` 键）+
   `attribution=mtime_sync_failed` + 顶层 `stage=finalize`（那个占位值第一次真用上）。
   `ok`/`rc` 不动：写回确实落了盘。

**两条都各有一个"场景可触发"的实测**（注入故障，不需要真的去占文件句柄）：
  * ① 注入：让删 `_verify_state.json` 抛 `OSError`（其余删除照旧）；
  * ② 注入：让刷**那份参数文件**的 `os.utime` 抛 `OSError`。
反向验证（把保护改回 0.1.15 的原样 ⇒ 本套件必红）见
``_team014/silent2/reverse2.py``（在仓库外的临时副本上跑，不碰共享工作树）。

跑法（脚本式，与 CI 一致）：`python tests/test_silent2.py`
"""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile

PY = sys.executable
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, 'src')
sys.path.insert(0, SRC)

from mpltweak import writeback as _wb            # noqa: E402

# 控制台兜底（GBK 控制台下 print 中文会 UnicodeEncodeError 把整套用例打挂；
# tests/test_hygiene.py 的 `test_test_suites_cannot_crash_gbk_console` 认这个标记）
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(errors='replace')
    except (AttributeError, ValueError, OSError):
        pass          # 被重定向成不支持 reconfigure 的对象时忽略即可

ENV = dict(os.environ, MPLBACKEND='Agg', MPLTWEAK_LANG='zh', PYTHONUTF8='1',
           PYTHONIOENCODING='utf-8', PYTHONPATH=SRC)

FAILED = []
PASSED = []

# 夹具：`plt.subplots()` 的 pos 在源码里**没有字面量** ⇒ 原位写不了 ⇒ 一定进
# `pending`，`_pending_vs_live` 才会被调用（这是①的唯一入口条件）。
DEMO = ('import matplotlib\n'
        'matplotlib.use("Agg")\n'
        'import matplotlib.pyplot as plt\n'
        'fig, ax = plt.subplots()\n'
        'ax.plot([1, 2], [2, 1])\n'
        'ax.set_title("A")\n'
        'fig.savefig("out0.png")\n')


def check(name, cond, detail=''):
    if cond:
        PASSED.append(name)
        print('  OK   %s%s' % (name, (' | %s' % detail) if detail else ''))
    else:
        FAILED.append(name)
        print('  FAIL %s%s' % (name, (' | %s' % detail) if detail else ''))


def _run(args, cwd):
    r = subprocess.run([PY, '-m', 'mpltweak.cli', *args], cwd=cwd,
                       capture_output=True, env=ENV)
    return (r.returncode,
            (r.stdout or b'').decode('utf-8', 'replace'),
            (r.stderr or b'').decode('utf-8', 'replace'))


def _fixture(d, pos):
    """写 demo 脚本 + 用真 describe 导一份参数，再把 pos 改成与现场**不同**的目标值。"""
    script = os.path.join(d, 'fig.py')
    with io.open(script, 'w', encoding='utf-8') as f:
        f.write(DEMO)
    params = os.path.join(d, 'p.json')
    rc, out, err = _run(['describe', 'fig.py', '-o', 'p.json'], d)
    assert rc == 0, '夹具 describe 失败：%s %s' % (rc, err[-400:])
    with io.open(params, encoding='utf-8') as f:
        data = json.load(f)
    for ax in data.get('axes') or []:
        ax['pos'] = pos
    with io.open(params, 'w', encoding='utf-8') as f:
        f.write(json.dumps(data, ensure_ascii=False, indent=1))
    return script, params, data


def _patch_remove():
    """把 `os.remove` 换成"只对 `_verify_state.json` 抛 OSError"的版本。"""
    real = os.remove
    seen = []

    def _boom(p, *a, **kw):
        if os.path.basename(str(p)) == '_verify_state.json':
            seen.append(str(p))
            raise OSError(13, 'injected: cannot remove _verify_state.json')
        return real(p, *a, **kw)

    os.remove = _boom
    return (lambda: setattr(os, 'remove', real)), seen


# --------------------------------------------------------------------- ① 静默点
def case_probe_state(base):
    """① `_pending_vs_live` 删不掉旧 `_verify_state.json` ⇒ 必须留痕、判定不变。"""
    d = os.path.join(base, 'probe')
    os.makedirs(d)
    script, params, data = _fixture(d, [0.15, 0.12, 0.74, 0.75])
    with io.open(os.path.join(d, '_verify_state.json'), 'w',
                 encoding='utf-8') as f:
        f.write('{"stale": true}')          # 上一次留下的状态
    pending = {0: ['pos']}

    restore, seen = _patch_remove()
    warns = []
    try:
        verdict = _wb._pending_vs_live(script, data, pending, PY, 120, params,
                                       warnings=warns)
    finally:
        restore()
    check('①a 注入生效：确实尝试删过旧状态文件', bool(seen), seen)
    check('①b 删不掉 → warnings 里有「旧验证状态文件删不掉」',
          any('旧验证状态文件删不掉' in w for w in warns), warns)
    check('①c 警告不改判定（仍是 True/False，没有退化成"判不了"）',
          verdict in (True, False), verdict)
    # 旧调用口径（不传 warnings）仍合法：只加不改
    restore, _seen = _patch_remove()
    try:
        verdict2 = _wb._pending_vs_live(script, data, pending, PY, 120, params)
    finally:
        restore()
    check('①d 不传 warnings 的旧调用不抛异常（默认参数保兼容）',
          verdict2 in (True, False), verdict2)
    # 无故障时不多嘴（不改成功路径）—— 尤其：**压根没有旧状态文件**这一路
    # （`os.remove` 抛 `FileNotFoundError`）不许出声。这条是 tests/test_writeback.py
    # 的 T11 抓到的真回归：宽 `except OSError` 会把"本来就不存在"也说成"删不掉"，
    # 于是每次干净验证都多一条假警告。
    state = os.path.join(d, '_verify_state.json')
    if os.path.exists(state):
        os.remove(state)
    warns_ok = []
    _wb._pending_vs_live(script, data, pending, PY, 120, params,
                         warnings=warns_ok)
    check('①e 没有旧状态文件 / 删得掉时 warnings 为空（成功路径一字不变）',
          warns_ok == [], warns_ok)

    # 端到端：穿到 `--json` 的 files[].warnings（真写回 + 真验证重跑）
    runner = os.path.join(d, 'arun.py')
    with io.open(runner, 'w', encoding='utf-8') as f:
        f.write(_A_RUNNER % {'src': SRC, 'params': os.path.abspath(params),
                             'argv': [script, '--params', params, '--write',
                                      '--json']})
    r = subprocess.run([PY, runner], cwd=d, capture_output=True, env=ENV)
    out = (r.stdout or b'').decode('utf-8', 'replace')
    try:
        payload = json.loads(out.strip())
    except ValueError:                                        # noqa: BLE001
        payload = {}
    warns_e2e = [w for f in (payload.get('files') or [])
                 for w in (f.get('warnings') or [])]
    check('①f 端到端：--json 的 files[].warnings 里有这条',
          any('旧验证状态文件删不掉' in w for w in warns_e2e), warns_e2e)


# 端到端用的子进程垫片：只对 `_verify_state.json` 的删除注入故障，再跑真 apply。
_A_RUNNER = r'''
import os, sys
sys.path.insert(0, %(src)r)
from mpltweak import apply as _apply
_real = os.remove

def _boom(p, *a, **kw):
    if os.path.basename(str(p)) == '_verify_state.json':
        raise OSError(13, 'injected: cannot remove _verify_state.json')
    return _real(p, *a, **kw)

os.remove = _boom
try:
    rc = _apply.main(%(argv)r)
finally:
    os.remove = _real
sys.exit(rc)
'''


# ------------------------------------------------------------------ ② 误归因
_B_RUNNER = r'''
import os, sys
sys.path.insert(0, %(src)r)
from mpltweak import apply as _apply
_real = os.utime
_target = os.path.normcase(%(params)r)

def _boom(path, *a, **kw):
    if os.path.normcase(os.path.abspath(str(path))) == _target:
        raise OSError(13, 'injected: cannot utime')
    return _real(path, *a, **kw)

os.utime = _boom
try:
    rc = _apply.main(%(argv)r)
finally:
    os.utime = _real
sys.exit(rc)
'''


def case_mtime(base):
    """② per-file `os.utime` 失败 ⇒ warnings + attribution + stage=finalize，rc/ok 不变。"""
    d = os.path.join(base, 'mtime')
    os.makedirs(d)
    script, params, _data = _fixture(d, [0.15, 0.12, 0.74, 0.75])
    runner = os.path.join(d, 'brun.py')
    argv = [script, '--params', params, '--write', '--json', '--verify-fast']
    with io.open(runner, 'w', encoding='utf-8') as f:
        f.write(_B_RUNNER % {'src': SRC, 'params': os.path.abspath(params),
                             'argv': argv})
    with io.open(script, encoding='utf-8') as f:
        before = f.read()
    r = subprocess.run([PY, runner], cwd=d, capture_output=True, env=ENV)
    with io.open(script, encoding='utf-8') as f:
        after = f.read()
    stdout = (r.stdout or b'').decode('utf-8', 'replace')
    stderr = (r.stderr or b'').decode('utf-8', 'replace')
    try:
        payload = json.loads(stdout.strip())
    except ValueError:                                        # noqa: BLE001
        payload = {}
    f0 = (payload.get('files') or [{}])[0]
    warns = [w for f in (payload.get('files') or [])
             for w in (f.get('warnings') or [])]
    check('②a 不再是 error_code=internal_error（误归因那条路没了）',
          payload.get('error_code') is None, payload.get('error_code'))
    check('②b ok=true 且 rc=0（写回确实落了盘）',
          payload.get('ok') is True and r.returncode == 0,
          '%r/%s' % (payload.get('ok'), r.returncode))
    check('②c 脚本真的被改了（rc=0 不是空头承诺）',
          after != before and f0.get('applied') is True, f0.get('applied'))
    check('②d files[].warnings 里有 mtime_sync_fail 那条',
          any('mtime' in w for w in warns), warns)
    check('②e 顶层 stage=finalize（messages.STAGES 的占位值第一次真用上）',
          payload.get('stage') == 'finalize', payload.get('stage'))
    check('②f attribution 说清是这一步（不再说 tool_bug）',
          f0.get('attribution') == 'mtime_sync_failed', f0.get('attribution'))
    check('②g next_step 是带 --force 的可复制命令',
          '--force' in (payload.get('next_step') or ''),
          payload.get('next_step'))
    check('②h 人读归因改成"外部环境问题，不是工具的 bug"（旧话术消失）',
          '这是工具的 bug' not in stderr and '不是工具的 bug' in stderr,
          stderr[-200:])
    check('②i stage 值仍在文档化的值域内（messages.STAGES）',
          payload.get('stage') in ('params', 'preflight', 'backup',
                                   'writeback', 'verify', 'finalize'),
          payload.get('stage'))


def main():
    base = tempfile.mkdtemp(prefix='mpltweak_s2_')
    try:
        case_probe_state(base)
        case_mtime(base)
    finally:
        shutil.rmtree(base, ignore_errors=True)
    print('\n%s：%d 项通过，%d 项失败' %
          ('FAILED' if FAILED else 'ALL PASS', len(PASSED), len(FAILED)))
    if FAILED:
        for name in FAILED:
            print('  - ' + name)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
