#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""t39 追因实验：对**单份脚本**做 A / A′ / B / B′ 四方对照，把"产物差异"的根因钉死。

四方（全部在**临时区**的副本目录里跑；语料只读；显式 ``MPLBACKEND=Agg``）：

* **A**  原样跑：``python <脚本>``
* **A′** 原样再跑一遍（换一个副本目录）—— 用来判"脚本自身是否就不确定"
* **B**  套壳跑：``python -m mpltweak.verify <脚本> <state.json> 0``（真实包装路径）
* **B′** 套壳 **但只禁 ``plt.close``**：拦截集与包装**完全相同**（``plt.show`` /
  ``matplotlib.use`` / ``plt.switch_backend`` 置空 + stdout 改道 stderr + cwd 切到
  脚本目录），**唯一差别是不把 ``plt.close`` 变成空操作**。
  ⇒ 于是 ``B′ vs B`` 就单变量地隔离出"拦 ``plt.close``"这一项干预的效果。

判读（照船长给的判读表）：

| A vs A′ | B vs B′ | 结论 |
|---|---|---|
| 不同 | — | 脚本自身不确定 ⇒ 与包装无关 |
| 相同 | 相同 | 包装无副作用 ⇒ 支持"套壳≈原样" |
| 相同 | 不同 | 包装改了产物 ⇒ 定位到"拦 ``plt.close``"，该干预必须去掉 |

用法::

    python tools/probe_close_vs_wrapper.py --script 018/zenodo/18910861__generate_all_figures.py
    python tools/probe_close_vs_wrapper.py --arm bprime <脚本>     # B′ 臂（内部使用）

门禁：跑之前对该脚本走 **A 档防弹窗静态筛**（复用 ``tests/test_corpus_invariants.py``
的 ``_window_risk()``）；命中即 ``rc=2`` 拒跑，**绝不裸跑真实语料**。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import time

try:
    sys.stdout.reconfigure(errors='replace')
    sys.stderr.reconfigure(errors='replace')
except Exception:                                # noqa: BLE001
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import cmp_plain_vs_instrumented as P                  # noqa: E402

OUT_DIR = os.path.join(os.path.dirname(REPO), '_review', 'cmp_plain_vs_instrumented')
DEFAULT_WORK = os.path.join(os.path.dirname(REPO), '.tmp_t39', 'close_probe')


# ---------------------------------------------------------------------------
# B′ 臂：套壳的拦截集，**去掉**"把 plt.close 置空"这一项
# ---------------------------------------------------------------------------
def run_bprime(script: str) -> int:
    """B′：与包装同一拦截集，但**不动** ``plt.close``。"""
    import contextlib
    import runpy

    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.switch_backend('Agg')

    real = (plt.show, matplotlib.use, plt.switch_backend)
    plt.show = lambda *a, **k: None
    matplotlib.use = lambda *a, **k: None
    plt.switch_backend = lambda *a, **k: None
    # 注意：**故意不碰 plt.close** —— 这正是本臂要隔离的那一项。
    _real_stdout = sys.stdout
    saved_fd = None
    try:
        sys.stdout.flush()
        saved_fd = os.dup(1)
        os.dup2(2, 1)
    except OSError:
        saved_fd = None
    sys.stdout = sys.stderr
    d = os.path.dirname(os.path.abspath(script))
    old = os.getcwd()
    os.chdir(d)
    rc = 0
    try:
        if d not in sys.path:
            sys.path.insert(0, d)
        runpy.run_path(script, run_name='__main__')
    except SystemExit as e:
        rc = int(e.code) if isinstance(e.code, int) else (0 if e.code is None else 1)
    except BaseException as e:                   # noqa: BLE001 与 collect() 同口径：报错不带走进程
        sys.stderr.write('%s: %s\n' % (type(e).__name__, e))
        rc = 1
    finally:
        plt.show, matplotlib.use, plt.switch_backend = real
        with contextlib.suppress(Exception):
            plt.close('all')
        try:
            if saved_fd is not None:
                sys.stdout.flush()
                os.dup2(saved_fd, 1)
                os.close(saved_fd)
        except OSError:
            pass
        sys.stdout = _real_stdout
        os.chdir(old)
    return rc


# ---------------------------------------------------------------------------
# 一个臂：在独立副本目录里跑，返回 (rc, wall, 产出文件表)
# ---------------------------------------------------------------------------
def run_arm(arm: str, src_abs: str, case_dir: str, python: str, timeout: float,
            env: dict):
    d = os.path.join(case_dir, arm)
    if os.path.isdir(d):
        shutil.rmtree(d, ignore_errors=True)
    P._seed_dir(d, src_abs)
    script_abs = os.path.join(d, os.path.basename(src_abs))
    if arm == 'B':
        state = os.path.join(case_dir, 'state_%s.json' % arm)
        if os.path.exists(state):
            os.remove(state)
        cmd = [python, '-m', 'mpltweak.verify', script_abs, state, '0']
    elif arm == 'bprime':
        cmd = [python, os.path.abspath(__file__), '--arm', 'bprime',
               '--script', script_abs]
    elif arm == 'closeonly':
        cmd = [python, os.path.abspath(__file__), '--arm', 'closeonly',
               '--script', script_abs]
    elif arm == 'trace':
        cmd = [python, os.path.abspath(__file__), '--arm', 'trace',
               '--script', script_abs, '--trace-log',
               os.path.join(case_dir, 'trace_A.json')]
    elif arm == 'tracec':
        cmd = [python, os.path.abspath(__file__), '--arm', 'tracec',
               '--script', script_abs, '--trace-log',
               os.path.join(case_dir, 'trace_B2.json')]
    else:
        cmd = [python, script_abs]
    r = P._run(cmd, d, env, timeout)
    snap = P._snapshot(d)
    seeded = set()
    for dp, dn, fn in os.walk(d):
        dn[:] = [x for x in dn if x not in ('__pycache__',)]
        for f in fn:
            seeded.add(os.path.relpath(os.path.join(dp, f), d).replace('\\', '/'))
    return {'rc': r['rc'], 'wall': round(r['wall'], 3), 'timeout': r['timeout'],
            'spawn_err': r['spawn_err'],
            'stdout_lines': len(P.normalize(P._dec(r['out']), [])),
            'stderr_tail': '\n'.join(P.normalize(P._dec(r['err']), [])[-6:])[:600],
            'files': {k: list(v) for k, v in snap.items()
                      if k not in ('__pycache__',)},
            'seed_dir': d}


def run_closeonly(script: str) -> int:
    """B″：**原样跑 + 只把 ``plt.close`` 变成空操作** —— 其余一概不干预。

    与 B′ 的区别（这是两个**不同**的实验，别混）：

    * **B′** = 套壳的**全部**干预**减去**"拦 close"（``plt.show``/``matplotlib.use``/
      ``switch_backend`` 仍置空、stdout 仍改道 stderr）⇒ 回答"套壳的差异是不是就
      来自拦 close 这一项"。
    * **B″** = **原样** + **只加**"拦 close"（不拦别的、不改 stdout）⇒ 回答"单独拦
      close 这一项本身能不能造出同样的产物差异"。

    两个都做才不会有歧义。代价：打补丁前必须先 import pyplot（A 里是脚本自己首次
    import），这是一项无法消除的小干预，如实记在报告里。
    """
    import runpy

    import matplotlib.pyplot as plt           # noqa: F401 必须能拿到 pyplot 才打得了补丁
    plt.close = lambda *a, **k: None
    d = os.path.dirname(os.path.abspath(script))
    old = os.getcwd()
    os.chdir(d)
    rc = 0
    try:
        if d not in sys.path:
            sys.path.insert(0, d)
        runpy.run_path(script, run_name='__main__')
    except SystemExit as e:
        rc = int(e.code) if isinstance(e.code, int) else (0 if e.code is None else 1)
    except BaseException as e:                   # noqa: BLE001
        sys.stderr.write('%s: %s\n' % (type(e).__name__, e))
        rc = 1
    finally:
        os.chdir(old)
    return rc


def run_traced(script: str, close_blocked: bool, log_path: str) -> int:
    """A 或 B″，外加**记录每次 ``savefig`` 时被保存的那张图长什么样**。

    只**观测**不改变行为：包一层 ``Figure.savefig`` 记录 ``fig.number`` / 轴数 /
    线条数 / 散点数 / 标题，然后把调用原样转给真的 savefig。
    close_blocked=True 时额外把 ``plt.close`` 变成空操作（= B″）。
    """
    import json as _json
    import runpy

    from matplotlib.figure import Figure
    import matplotlib.pyplot as plt

    real = Figure.savefig
    log = []

    def patched(self, fname, *a, **k):
        try:
            log.append({
                'file': os.path.basename(str(fname)),
                'fig_number': getattr(self, 'number', None),
                'n_axes': len(self.axes),
                'n_lines': sum(len(ax.lines) for ax in self.axes),
                'n_collections': sum(len(ax.collections) for ax in self.axes),
                'n_patches': sum(len(ax.patches) for ax in self.axes),
                'titles': [ax.get_title() for ax in self.axes],
            })
        except Exception:                        # noqa: BLE001 记录失败不影响脚本
            pass
        return real(self, fname, *a, **k)

    Figure.savefig = patched
    if close_blocked:
        plt.close = lambda *a, **k: None
    d = os.path.dirname(os.path.abspath(script))
    old = os.getcwd()
    os.chdir(d)
    rc = 0
    try:
        if d not in sys.path:
            sys.path.insert(0, d)
        runpy.run_path(script, run_name='__main__')
    except SystemExit as e:
        rc = int(e.code) if isinstance(e.code, int) else (0 if e.code is None else 1)
    except BaseException as e:                   # noqa: BLE001
        sys.stderr.write('%s: %s\n' % (type(e).__name__, e))
        rc = 1
    finally:
        Figure.savefig = real
        os.chdir(old)
        with open(log_path, 'w', encoding='utf-8') as f:
            _json.dump(log, f, ensure_ascii=False, indent=1)
    return rc


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='t39 追因：A / A′ / B / B′ 四方对照')
    ap.add_argument('--script', default='')
    ap.add_argument('--arm', default='', help='内部：bprime / closeonly / trace / tracec')
    ap.add_argument('--trace-log', default='', help='内部：--arm trace 的日志落点')
    ap.add_argument('--corpus', default=P.DEFAULT_CORPUS)
    ap.add_argument('--python', default=sys.executable)
    ap.add_argument('--timeout', type=float, default=180.0)
    ap.add_argument('--work', default=DEFAULT_WORK)
    ap.add_argument('--target-glob', default='',
                    help='要重点看的产出文件子串（默认自动挑"差异最大的那个"）')
    ap.add_argument('--keep', action='store_true')
    args = ap.parse_args(argv)

    if args.arm == 'bprime':
        return run_bprime(args.script)
    if args.arm == 'closeonly':
        return run_closeonly(args.script)
    if args.arm in ('trace', 'tracec'):
        return run_traced(args.script, args.arm == 'tracec', args.trace_log)

    if not args.script:
        sys.stderr.write('需要 --script\n')
        return 2
    src_abs = os.path.join(args.corpus, args.script)
    if not os.path.isfile(src_abs):
        sys.stderr.write('语料里没有: %s\n' % src_abs)
        return 2

    # ---- A 档防弹窗门禁（跑前自证；命中即拒跑）-----------------------------
    wr, prov, err = P.load_window_risk()
    if wr is None:
        sys.stderr.write('[门禁] **拒绝开跑**：A 档判据复用失败（%s）\n' % err)
        return 2
    P.WINDOW_RISK = wr
    ok, reason, ev = P.screen_script(P.read_text(src_abs), wr)
    sys.stderr.write('[门禁] %s → A 档命中 = %d，reason=%s\n'
                     % (args.script, 0 if ok else 1, reason))
    if not ok:
        sys.stderr.write('[门禁] **拒绝开跑**：%s %s\n' % (reason, ev))
        return 2

    env = P._child_env()
    env['MPLBACKEND'] = 'Agg'
    os.makedirs(args.work, exist_ok=True)
    case_dir = os.path.join(args.work, hashlib.sha256(
        args.script.encode('utf-8')).hexdigest()[:16])
    os.makedirs(case_dir, exist_ok=True)

    arms = [('A', 'A 原样跑'), ('A2', 'A′ 原样再跑一遍'),
            ('B', 'B 套壳跑'),
            ('bprime', 'B′ 套壳去掉"拦 close"这一项（其余干预照旧）'),
            ('closeonly', 'B″ 原样 + 只加"拦 close"这一项（其余不干预）'),
            ('trace', 'A + savefig 追踪（只观测）'),
            ('tracec', 'B″ + savefig 追踪（只观测）')]
    res = {}
    for arm, label in arms:
        sys.stderr.write('>>> %s\n' % label)
        res[arm] = run_arm(arm, src_abs, case_dir, args.python, args.timeout, env)
        res[arm]['label'] = label

    # ---- 产出文件对比 ------------------------------------------------------
    def arts(arm):
        out = {}
        for k, v in res[arm]['files'].items():
            if k.endswith('.py'):
                continue
            out[k] = v
        return out

    keys = set()
    for arm, _ in arms:
        keys |= set(arts(arm))
    target = args.target_glob
    if not target:
        # 自动挑：在所有**确实跑出文件**的臂里、sha 不全相同的那个文件
        live = [a for a, _ in arms if arts(a)]
        for k in sorted(keys):
            shas = {arts(a).get(k, [0, ''])[1] for a in live}
            if len(shas) > 1:
                target = k
                break
    tgt = [k for k in sorted(keys) if target and target in k]

    def rel(arm):
        return {k: v[0] for k, v in arts(arm).items()}

    payload = {
        'script': args.script,
        'script_sha256': P._sha256_file(src_abs),
        'gate': {'criterion_source': prov, 'window_risk_hits': 0},
        'arms': {a: {k: v for k, v in res[a].items() if k != 'files'}
                 for a, _ in arms},
        'files': {a: rel(a) for a, _ in arms},
        'target_files': tgt,
        'target_sizes': {a: {k: arts(a).get(k, [None, None])[0] for k in tgt}
                         for a, _ in arms},
        'target_sha': {a: {k: arts(a).get(k, [None, None])[1] for k in tgt}
                       for a, _ in arms},
    }

    # ---- 目标 PNG 的逐像素差异（有 PIL 就算，没有就跳过）------------------
    pix = {}
    if tgt:
        try:
            from PIL import Image
            import numpy as np
        except Exception:                        # noqa: BLE001
            Image = None
        if Image is not None:
            t0 = tgt[0]
            base = None
            for arm, _ in arms:
                fp = os.path.join(res[arm]['seed_dir'], t0)
                if not os.path.isfile(fp):
                    pix[arm] = None
                    continue
                try:
                    arr = np.asarray(Image.open(fp).convert('RGB'), dtype=np.int16)
                except Exception:                # noqa: BLE001
                    pix[arm] = None
                    continue
                if base is None:
                    base = arr
                    pix[arm] = {'shape': list(arr.shape), 'diff_px_ratio': 0.0,
                                'max_abs': 0, 'mean_abs': 0.0}
                else:
                    if arr.shape != base.shape:
                        pix[arm] = {'shape': list(arr.shape),
                                    'diff_px_ratio': 1.0,
                                    'max_abs': None, 'mean_abs': None,
                                    'note': '尺寸不同'}
                        continue
                    d = np.abs(arr - base)
                    px = (d.max(axis=2) > 0)
                    pix[arm] = {'shape': list(arr.shape),
                                'diff_px_ratio': round(float(px.mean()), 6),
                                'max_abs': int(d.max()),
                                'mean_abs': round(float(d.mean()), 6)}
    payload['pixel_vs_A'] = pix

    # ---- savefig 追踪（A 与 B″ 各一份）：把"叠加了哪张图"从推断变成实测 -----
    traced = {}
    for arm in ('trace', 'tracec'):
        fp = os.path.join(case_dir, 'trace_%s.json'
                          % ('A' if arm == 'trace' else 'B2'))
        if os.path.isfile(fp):
            try:
                with open(fp, 'r', encoding='utf-8') as f:
                    traced[arm] = json.load(f)
            except (OSError, ValueError):
                traced[arm] = None
    if traced:
        payload['savefig_trace'] = traced
        ta, tb = traced.get('trace') or [], traced.get('tracec') or []
        bya = {r['file']: r for r in ta}
        byb = {r['file']: r for r in tb}
        delta = {}
        for k in sorted(set(bya) | set(byb)):
            ra, rb = bya.get(k), byb.get(k)
            if ra != rb:
                delta[k] = {'A': ra, 'B2': rb}
        payload['savefig_trace_delta'] = delta

    os.makedirs(OUT_DIR, exist_ok=True)
    jp = os.path.join(OUT_DIR, 'CLOSE_PROBE_%s.json'
                      % hashlib.sha256(args.script.encode()).hexdigest()[:8])
    with open(jp, 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)

    print(json.dumps(payload, ensure_ascii=False, indent=1))
    print('\n[close-probe] 明细=%s' % jp)
    if not args.keep:
        shutil.rmtree(case_dir, ignore_errors=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
