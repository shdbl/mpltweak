# -*- coding: utf-8 -*-
"""t39 的 teeth（变异测试）：证明 `tests/test_verify_onepass.py` 的每一条都**真会红**。

做法：把当前 ``src/mpltweak`` 复制成一棵临时树，做**单点变异**，再让回归网用
``PYTHONPATH=<变异树>`` 跑 —— 每条变异都必须让**指定的那条 check** 变红。
只读取/复制源树，**不改仓库里的任何文件**；全部在临时目录里进行、跑完删掉。

变异清单（与 tests/test_verify_onepass.py 文件头列的一致）：
  M1 还原 close 拦截（``plt.close = lambda *a, **k: None``，即阶段 1 定位的机制①）
     → B「产物对账」必须红
  M2 ``verify.verify_once`` 永远返回 status='ok' / verified=True
     → C①「脚本跑不通」必须红
  M3 ``verify._compare_state`` 永远返回空 bad（= 不比对还说通过）
     → C②「改动没生效」必须红

用法：
    python tools/teeth_onepass.py            # 跑全部三条变异
    python tools/teeth_onepass.py --only M1  # 只跑一条
退出码 0 = 每条变异都按预期变红（teeth 有效）；非 0 = 有变异没能让用例变红（用例是纸老虎）。
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, 'src')
TEST = os.path.join(ROOT, 'tests', 'test_verify_onepass.py')

# ---------------------------------------------------------------------------
# 变异体：每项 = (id, 说明, 目标文件, 期待变红的 check 关键字, 补丁函数)
# ---------------------------------------------------------------------------


def _m1_close_interception(text):
    """M1：恢复"拦 plt.close"（阶段 1 双向证明过的机制①）。"""
    old = ('    real = (plt.show, matplotlib.use, plt.switch_backend)\n'
           '    plt.show = lambda *a, **k: None\n')
    new = ('    real = (plt.show, plt.close, matplotlib.use, plt.switch_backend)\n'
           '    plt.show = lambda *a, **k: None\n'
           '    plt.close = lambda *a, **k: None\n')
    old2 = '        plt.show, matplotlib.use, plt.switch_backend = real\n'
    new2 = '        plt.show, plt.close, matplotlib.use, plt.switch_backend = real\n'
    if old not in text or old2 not in text:
        raise RuntimeError('M1 找不到锚点（verify.py 结构变了）')
    return text.replace(old, new, 1).replace(old2, new2, 1)


def _m2_onepass_always_ok(text):
    """M2：让单遍验证永远"通过"（= 把退出码/成败检查弄坏）。

    做法：在 verify.py 末尾重绑模块级 ``verify_once`` —— ``writeback._verify_script``
    是**属性查表**调用（``_verify.verify_once``），所以尾巴上的重绑一定生效。
    """
    if 'def verify_once(' not in text:
        raise RuntimeError('M2 找不到 verify_once')
    return text + (
        '\n\n# ---- M2 变异（teeth 用；不改仓库源码）----\n'
        'def _teeth_verify_once(*a, **k):\n'
        '    return {"status": "ok", "verified": True, "detail": "",\n'
        '            "stderr": "", "rc": 0, "basis": "savefig",\n'
        '            "fallback_reason": ""}\n\n\n'
        'verify_once = _teeth_verify_once\n')


def _m3_compare_never_bad(text):
    """M3：让状态比对永远返回"无不一致"（= 报告得比实际宽松）。

    重绑模块级 ``_compare_state``：``verify_semantic`` 与 ``verify_once`` 都按名字查表
    调用它，所以单遍/两遍两条路一起被"弄松"。
    """
    if 'def _compare_state(' not in text:
        raise RuntimeError('M3 找不到 _compare_state')
    return text + (
        '\n\n# ---- M3 变异（teeth 用；不改仓库源码）----\n'
        'def _teeth_compare_state(*a, **k):\n'
        '    return [], ""\n\n\n'
        '_compare_state = _teeth_compare_state\n')


MUTATIONS = [
    ('M1', '还原 close 拦截（plt.close 空操作化）', 'verify.py',
     'B 用户目录里的每张图都与原样跑逐字节一致', _m1_close_interception),
    ('M2', 'verify_once 永远返回 ok/verified=True', 'verify.py',
     'C① 判失败且未保留写回', _m2_onepass_always_ok),
    ('M3', '_compare_state 永远返回空 bad', 'verify.py',
     'C② 判失败', _m3_compare_never_bad),
]


def _decode(b):
    for enc in ('utf-8', os.device_encoding(1) or 'utf-8'):
        try:
            return b.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return b.decode('utf-8', 'replace')


def run_mutation(mid, desc, fname, expect_red, patch):
    tree = tempfile.mkdtemp(prefix='t39_teeth_%s_' % mid)
    try:
        dst = os.path.join(tree, 'mpltweak')
        shutil.copytree(os.path.join(SRC, 'mpltweak'), dst)
        target = os.path.join(dst, fname)
        with open(target, 'r', encoding='utf-8') as f:
            text = f.read()
        with open(target, 'w', encoding='utf-8') as f:
            f.write(patch(text))
        # 变异体自身必须能编译/导入 —— 否则"用例变红"只是变异写坏了，不是 teeth 有效
        cc = subprocess.run([sys.executable, '-m', 'py_compile', target],
                            capture_output=True)
        if cc.returncode != 0:
            print('FAIL %s | %s\n     变异体编译失败（teeth 自身出错，不算结果）:\n%s'
                  % (mid, desc, _decode(cc.stderr)[-500:]))
            return False
        env = dict(os.environ, MPLBACKEND='Agg', MPLTWEAK_LANG='zh',
                   PYTHONPATH=tree, PYTHONIOENCODING='utf-8')
        r = subprocess.run([sys.executable, TEST], cwd=ROOT, env=env,
                           capture_output=True, timeout=1800)
        out = _decode(r.stdout + r.stderr)
        red = [ln for ln in out.splitlines()
               if ln.strip().startswith('FAIL') and expect_red in ln]
        ok = r.returncode != 0 and bool(red)
        print('%s %s | %s' % ('PASS' if ok else 'FAIL', mid, desc))
        print('     期待变红的 check: %s' % expect_red)
        print('     实际 exit=%s；命中红色行 %d 条' % (r.returncode, len(red)))
        for ln in red[:2]:
            print('       %s' % ln.strip()[:220])
        if not ok:
            tail = out.strip().splitlines()[-12:]
            print('     ---- 输出尾巴 ----')
            for ln in tail:
                print('       %s' % ln[:220])
        return ok
    finally:
        shutil.rmtree(tree, ignore_errors=True)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--only', default='', help='只跑某个变异（M1/M2/M3）')
    args = ap.parse_args(argv)
    n = ok = 0
    for mid, desc, fname, expect_red, patch in MUTATIONS:
        if args.only and mid != args.only:
            continue
        n += 1
        ok += 1 if run_mutation(mid, desc, fname, expect_red, patch) else 0
    print('\n[teeth] %d/%d 条变异按预期让用例变红' % (ok, n))
    print('TEETH OK' if ok == n and n else 'TEETH FAILED')
    return 0 if (ok == n and n) else 1


if __name__ == '__main__':
    sys.exit(main())
