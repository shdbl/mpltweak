# -*- coding: utf-8 -*-
"""§119（0.1.17 / S8）`mpltweak demo` 的离屏验收：示例随包、复用不覆盖、零侵入、
`--write` 落盘 + 两张对比图、`first_demo` 第一次 true 后 false、无后端人话 + rc=4。

**全程不开窗**（`--no-window --params`）：开窗那条路只有真人能测，测试只钉非交互部分。
为什么必须有这套：`demo` 是新用户第一条命令，它错了（复制不到示例 / 覆盖了用户的
试验场 / 不 --write 却写了盘）第一印象就毁 —— 而这些都能离屏测出来。
"""
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, 'src')
fail = 0

# 控制台兜底（GBK 控制台下 print 中文会 UnicodeEncodeError 把整套用例打挂；
# tests/test_hygiene.py 的 `test_test_suites_cannot_crash_gbk_console` 认这个标记）
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(errors='replace')
    except (AttributeError, ValueError, OSError):
        pass


def check(name, cond, detail=''):
    global fail
    if not cond:
        fail += 1
        print('FAIL | %s%s' % (name, (' | %s' % (detail,)) if detail else ''))
    else:
        print('PASS | %s%s' % (name, (' | %s' % (detail,)) if detail else ''))


def _pin_child_env(base, **overrides):
    """起子进程前**硬钉** MPLBACKEND=Agg（口径同 tests/test_hygiene.py 的级联闸门）。

    为什么硬钉而不是 setdefault：宿主若把 MPLBACKEND 设成 Qt5Agg / TkAgg，
    setdefault 会原样保留 -> 子进程落到交互后端 -> 真开窗、抢焦点与鼠标。
    """
    env = dict(base)
    env.update({k: str(v) for k, v in overrides.items()})
    env['MPLBACKEND'] = 'Agg'
    return env


def run(cwd, args, env):
    r = subprocess.run([sys.executable, '-m', 'mpltweak'] + args, cwd=cwd,
                       capture_output=True, env=_pin_child_env(env), timeout=1800)
    return (r.returncode, r.stdout.decode('utf-8', 'replace'),
            r.stderr.decode('utf-8', 'replace'))


def sha(p):
    return hashlib.sha256(open(p, 'rb').read()).hexdigest()


def main():
    home = tempfile.mkdtemp(prefix='mpltweak_demo_home_')
    d = tempfile.mkdtemp(prefix='mpltweak_demo_case_')
    # 级联闸门：起子进程一律经 `_pin_child_env` 硬钉 MPLBACKEND=Agg
    env = _pin_child_env(dict(os.environ), PYTHONPATH=SRC,
                         MPLTWEAK_LANG='zh', PYTHONUTF8='1',
                         PYTHONIOENCODING='utf-8', HOME=home,
                         USERPROFILE=home)
    sp = os.path.join(d, 'mpltweak-demo', 'demo_panels.py')
    try:
        rc, out, err = run(d, ['demo', '--no-window', '--json'], env)
        j = json.loads(out) if out.strip().startswith('{') else {}
        check('§119① 首次跑：rc=0 + 示例脚本随包铺到 ./mpltweak-demo/',
              rc == 0 and os.path.isfile(sp) and j.get('first_demo') is True,
              'rc=%s file=%s first=%r' % (rc, os.path.isfile(sp),
                                          j.get('first_demo')))
        check('§119① 没改任何东西 ⇒ 明说"没有改动任何东西"，且不写盘（wrote=false）',
              '没有改动任何东西' in (err + out) and j.get('wrote') is False,
              (err + out)[-160:])
        sha0 = sha(sp)
        rc2, out2, _e2 = run(d, ['demo', '--no-window', '--json'], env)
        check('§119② 目录已存在 ⇒ **复用不覆盖**（脚本字节不变、first_demo 变 false）',
              sha(sp) == sha0 and '复用' in out2 + _e2
              and (json.loads(out2) if out2.strip().startswith('{') else {}
                   ).get('first_demo') is False, 'sha 同=%s' % (sha(sp) == sha0))
        # 造一份"改了字号"的参数（describe 全量 → 只改一个字段）
        rc3, _o3, _e3 = run(d, ['describe', sp, '-o', 'p.json'], env)
        p = os.path.join(d, 'p.json')
        pdata = json.load(io.open(p, encoding='utf-8'))
        pdata['axes'][0]['label_fontsize'] = 17
        json.dump(pdata, io.open(p, 'w', encoding='utf-8'))
        rc4, out4, err4 = run(d, ['demo', '--no-window', '--params', 'p.json',
                                  '--json'], env)
        j4 = json.loads(out4) if out4.strip().startswith('{') else {}
        check('§119③ 关窗报告：changes 是**人话字符串数组** + diff + 下一步',
              rc4 == 0 and isinstance(j4.get('changes'), list)
              and j4['changes'] and all(isinstance(x, str) for x in j4['changes'])
              and (j4.get('diff') or []) != []
              and '下一步' in (err4 + out4),
              'changes=%r' % (j4.get('changes'),))
        check('§119③ 不传 --write ⇒ 示例脚本**一个字节都没动**（零侵入）',
              sha(sp) == sha0 and j4.get('wrote') is False, sha(sp) == sha0)
        rc5, out5, err5 = run(d, ['demo', '--no-window', '--params', 'p.json',
                                  '--write', '--json'], env)
        j5 = json.loads(out5) if out5.strip().startswith('{') else {}
        ci = j5.get('compare_images') or {}
        check('§119④ --write：落盘 + before.png / after.png 都在 + 写回后脚本能跑',
              rc5 == 0 and j5.get('wrote') is True
              and os.path.isfile(ci.get('before') or '')
              and os.path.isfile(ci.get('after') or '')
              and sha(sp) != sha0
              and subprocess.run([sys.executable, 'demo_panels.py'],
                                 cwd=os.path.dirname(sp), capture_output=True,
                                 env=_pin_child_env(env), timeout=1800).returncode == 0,
              'rc=%s wrote=%r' % (rc5, j5.get('wrote')))
        check('§119④ 输出里给了两张图的路径、且**没有**免责声明段',
              'before.png' in (err5 + out5) and 'after.png' in (err5 + out5)
              and '仅示例' not in (err5 + out5), (err5 + out5)[-200:])
        check('§119⑦ 人读输出提示"按 ? 看快捷键速查表"（窗口里那件事得让人知道）',
              '快捷键速查表' in (out4 + err4) and '?' in (out4 + err4),
              repr((out4 + err4)[-140:]))
        check('§119⑤ first_demo=true 时带一句**给用户的话**（不含命令/参数名）',
              '给用户看 demo 效果后' in (out + err)
              and '给用户看 demo 效果后' not in (out2 + _e2),
              repr((out + err)[-120:]))
        # §119⑥ 无图形后端：走 launch 同一句人话 + 同一个退出码 4（**不许 Traceback**）。
        # 用 monkeypatch 把 launch.main 换成"返回 4"（真去拔环境太脆），只验 demo 的
        # 转发口径：rc=4（既有口径，不新造退出码）+ 人读不出现 traceback。
        sys.path.insert(0, SRC)
        from mpltweak import launch as _launch
        from mpltweak import demo as _demo
        _orig_launch = _launch.main
        _launch.main = lambda argv: 4
        try:
            _rc6 = _demo.main(['--dir', os.path.join(d, 'nobk'), '--json'])
        finally:
            _launch.main = _orig_launch
        check('§119⑥ 无图形后端：rc=4（既有口径）+ 不抛 Traceback',
              _rc6 == 4, 'rc=%r' % (_rc6,))
    finally:
        shutil.rmtree(d, ignore_errors=True)
        shutil.rmtree(home, ignore_errors=True)
    print('FAILED: %d' % fail if fail else 'ALL PASS')
    return 1 if fail else 0


if __name__ == '__main__':
    sys.exit(main())
