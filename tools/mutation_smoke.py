# -*- coding: utf-8 -*-
"""变异冒烟（t76）：发布期跑，**不进 CI**（每次要起多个套件子进程，分钟级）。

纪律（今晚两次假绿的教训）：
  * 变异只在**副本**里做（src + tests 一起拷），仓库文件一个字节都不动；
  * **自证①**：跑前断言 `import mpltweak` 落在副本之下 —— editable 安装会遮蔽副本，
    不钉 PYTHONPATH 时同一变异会跑出 ALL PASS 假绿（verifier t43 实测）；
  * **自证②**：锚点替换数必须 == 1 —— 0 次说明锚点漂了，继续跑就是假绿；
  * 每条被指定的断言都必须**至少被一条变异弄红**，否则进"无鉴别力"清单（退出码 1）。

用法：`python tools/mutation_smoke.py [--json]`
"""
import argparse
import ast
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable

# (名字, 相对文件, 锚点, 替换, 期望变红的断言关键字, 跑哪个套件)
MUTATIONS = [
    ('M1 doctor 的 UTF-8 切换', 'mpltweak/launch.py',
     '    _force_utf8_streams()          # P2-9：中文 Windows 管道下别乱码\n',
     '    pass\n', '§11[P2-9]', 'test_launch'),
    ('M2 脚本 stderr 汇总', 'mpltweak/launch.py',
     "    if _err_probe.lines:\n", "    if False:  # teeth\n",
     '§11[P2-10]', 'test_launch'),
    ('M3 预览的"会改"清单', 'mpltweak/apply.py',
     '    _refs = _snippet_refs(data, None)\n', '    _refs = []  # teeth\n',
     '§11[P2-11]', 'test_launch'),
    ('M4 check 的 colorbar 规则', 'mpltweak/check.py',
     "min(tol, CB_OVERLAP_TOL)",
     "min(tol, 9e9)  # teeth", '§16', 'test_check'),
]


def _run_one(name, rel, old, new, want, suite):
    tmp = tempfile.mkdtemp(prefix='mut_smoke_')
    dst = os.path.join(tmp, 'src')
    shutil.copytree(os.path.join(ROOT, 'src'), dst)
    shutil.copytree(os.path.join(ROOT, 'tests'), os.path.join(tmp, 'tests'))
    f = os.path.join(dst, rel)
    s = io.open(f, encoding='utf-8', newline='').read()
    n = s.count(old)
    if n != 1:
        shutil.rmtree(tmp, ignore_errors=True)
        raise SystemExit('[%s] 自证②失败：锚点命中 %d 次（必须 1）—— 拒绝跑出假绿'
                         % (name, n))
    io.open(f, 'w', encoding='utf-8', newline='').write(s.replace(old, new))
    ast.parse(io.open(f, encoding='utf-8').read())

    env = dict(os.environ, MPLBACKEND='Agg', PYTHONIOENCODING='utf-8',
               MPLTWEAK_LANG='zh', PYTHONPATH=dst)
    pr = subprocess.run([PY, '-c', 'import mpltweak; print(mpltweak.__file__)'],
                        cwd=tmp, capture_output=True, env=env, timeout=300)
    got = pr.stdout.decode('utf-8', 'replace').strip()
    if not got.startswith(dst):
        shutil.rmtree(tmp, ignore_errors=True)
        raise SystemExit('[%s] 自证①失败：import 解析到副本之外（%s）—— 拒绝跑出假绿'
                         % (name, got))
    r = subprocess.run([PY, os.path.join(tmp, 'tests', suite + '.py')],
                       cwd=tmp, capture_output=True, env=env, timeout=2400)
    txt = r.stdout.decode('utf-8', 'replace')
    fails = [l for l in txt.splitlines() if l.startswith('FAIL')]
    hit = any(want in l for l in fails)
    shutil.rmtree(tmp, ignore_errors=True)
    return {'mutation': name, 'suite': suite, 'expect': want,
            'rc': r.returncode, 'n_fail': len(fails),
            'bites': bool(r.returncode != 0 and hit),
            'sample': next((l[:150] for l in fails if want in l), '')}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--json', action='store_true')
    args = ap.parse_args(argv)
    rows = [_run_one(*m) for m in MUTATIONS]
    toothless = [r['mutation'] for r in rows if not r['bites']]
    if args.json:
        print(json.dumps({'mutations': rows, 'toothless': toothless},
                         ensure_ascii=False, indent=2))
    else:
        for r in rows:
            print('%-24s suite=%-12s rc=%s FAIL=%d  咬=%s'
                  % (r['mutation'], r['suite'], r['rc'], r['n_fail'],
                     'YES' if r['bites'] else 'NO'))
            if r['sample']:
                print('    %s' % r['sample'])
        print()
        print('无鉴别力清单（没有任何变异能弄红它）= %s'
              % (toothless if toothless else '空'))
        print('结论：%s' % ('OK' if not toothless else 'FAIL'))
    return 1 if toothless else 0


if __name__ == '__main__':
    sys.exit(main())
