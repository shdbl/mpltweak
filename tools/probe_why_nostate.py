#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""t39 追因小工具：为什么"套壳跑"会报"脚本没有留下任何图"？

对每份脚本：在隔离副本目录里按**套壳的拦截口径**（``plt.close`` 变空操作）跑一遍，
打印 ①脚本自己的退出方式 ②跑完后 ``plt.get_fignums()`` ③matplotlib 的 figure
manager 数量。用来判"没有留下任何图"到底是"脚本真的没图"还是"图被谁弄没了"。

只读语料（复制后跑）；跑前过 A 档防弹窗静态筛。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

try:
    sys.stdout.reconfigure(errors='replace')
except Exception:                                # noqa: BLE001
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import cmp_plain_vs_instrumented as P                  # noqa: E402

CHILD = r'''
import os, runpy, sys
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
plt.switch_backend('Agg')
plt.show = lambda *a, **k: None
plt.close = lambda *a, **k: None          # 套壳口径：拦 close
os.chdir(sys.argv[1])
sys.path.insert(0, sys.argv[1])
try:
    runpy.run_path(sys.argv[2], run_name='__main__')
    print('EXIT_MODE normal')
except SystemExit as e:
    print('EXIT_MODE SystemExit code=%r' % (e.code,))
except BaseException as e:
    print('EXIT_MODE %s: %s' % (type(e).__name__, e))
try:
    print('FIGNUMS', plt.get_fignums())
except Exception as e:
    print('FIGNUMS_ERROR', e)
try:
    import matplotlib.pyplot as _p
    print('MANAGERS', len(_p._pylab_helpers.Gcf.get_all_fig_managers()))
except Exception as e:
    print('MANAGERS_ERROR', e)
'''


def main(argv=None) -> int:
    rels = list(argv if argv is not None else sys.argv[1:])
    if not rels:
        sys.stderr.write('用法: probe_why_nostate.py <relpath> [relpath...]\n')
        return 2
    wr, _prov, err = P.load_window_risk()
    if wr is None:
        sys.stderr.write('[门禁] 判据装载失败：%s\n' % err)
        return 2
    tmp = os.path.join(os.path.dirname(REPO), '.tmp_t39', 'why_nostate')
    for rel in rels:
        src = os.path.join(P.DEFAULT_CORPUS, rel)
        ok, reason, ev = P.screen_script(P.read_text(src), wr)
        print('=' * 78)
        print('== %s   [A 档筛 ok=%s %s]' % (rel, ok, reason if not ok else ''))
        if not ok:
            print('   拒绝开跑：%s %s' % (reason, ev))
            continue
        d = os.path.join(tmp, rel.replace('/', '__'))
        shutil.rmtree(d, ignore_errors=True)
        P._seed_dir(d, src)
        s = os.path.join(d, os.path.basename(rel))
        env = P._child_env()
        env['MPLBACKEND'] = 'Agg'
        try:
            r = subprocess.run([sys.executable, '-c', CHILD, d, s],
                               capture_output=True, env=env, timeout=180)
            out = (r.stdout + r.stderr).decode('utf-8', 'replace')
        except subprocess.TimeoutExpired:
            print('   超时')
            continue
        print('   rc=%s' % r.returncode)
        for ln in out.strip().splitlines()[-16:]:
            print('   | %s' % ln[:200])
        shutil.rmtree(d, ignore_errors=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
