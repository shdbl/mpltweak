# -*- coding: utf-8 -*-
"""t39 性能探针：同一批真实脚本上"旧两遍验证"vs"新单遍验证"的墙钟耗时对照。

只读语料（复制到临时工程后跑），零窗口（MPLBACKEND=Agg），不写任何仓库产物。

对照口径
--------
* **旧两遍**（0.1.10 的 happy path）：``writeback.verify_run``（原样跑一遍看退出码）
  + ``verify.verify_semantic``（套壳再跑一遍取状态比对）。
* **新一遍**（t39）：``writeback._verify_script`` —— 一次子进程同时回答"还跑得通吗"
  与"改动真生效了吗"；取不到状态才回退两遍（回退的份会单独计数，不计入节省）。

参数取自真实 ``describe``（= 与当前状态一致的干净往返），所以两臂的语义层都应判"一致"，
差异只来自"跑了几遍脚本"。

用法：
    python tools/probe_onepass_perf.py --sample 24 --out <dir\\perf.jsonl>
"""

import argparse
import json
import os
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, 'src')
if SRC not in sys.path:
    sys.path.insert(0, SRC)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import cmp_plain_vs_instrumented as C        # noqa: E402  复用抽样与门禁

from mpltweak import verify as V             # noqa: E402
from mpltweak import writeback as W          # noqa: E402


def _seed(dst, src_abs, corpus_root):
    """把脚本与**同级**文件复制到隔离目录（与阶段 1 探针同口径）。"""
    rel = os.path.relpath(src_abs, corpus_root)
    src_dir = os.path.dirname(src_abs)
    os.makedirs(dst, exist_ok=True)
    name = os.path.basename(src_abs)
    budget = 12 << 20
    used = 0
    for n in sorted(os.listdir(src_dir)):
        p = os.path.join(src_dir, n)
        if not os.path.isfile(p):
            continue
        sz = os.path.getsize(p)
        if n != name and used + sz > budget:
            continue
        try:
            shutil.copy2(p, os.path.join(dst, n))
            used += sz
        except OSError:
            pass
    return os.path.join(dst, name)


def _describe(py, script, d):
    pj = os.path.join(d, 'p.json')
    r = C._run([py, '-m', 'mpltweak', 'describe', os.path.basename(script),
                '--fig', '0', '-o', 'p.json'],
               d, C._child_env(), 60)
    if r['rc'] != 0 or not os.path.exists(pj):
        return None
    try:
        with open(pj, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def one_case(rel, corpus_root, py, work, timeout):
    src_abs = os.path.join(corpus_root, rel)
    case = os.path.join(work, C.hashlib.sha256(rel.encode('utf-8')).hexdigest()[:16])
    shutil.rmtree(case, ignore_errors=True)
    script = _seed(case, src_abs, corpus_root)
    data = _describe(py, script, case)
    if not data or not (data.get('axes') or []):
        return {'path': rel, 'skip': 'no_params'}
    rec = {'path': rel, 'n_figs': data.get('n_figs'),
           'fig_index': data.get('fig_index')}
    # ---- 旧两遍 ----
    d2 = case + '_2'
    shutil.rmtree(d2, ignore_errors=True)
    shutil.copytree(case, d2)
    s2 = os.path.join(d2, os.path.basename(script))
    t0 = time.time()
    vok, verr = W.verify_run(s2, py, timeout)
    t_run = time.time() - t0
    if vok is not True:
        rec['skip'] = 'plain_rc!=0' if vok is False else 'timeout/spawn'
        rec['t_two'] = None
        return rec
    t0 = time.time()
    sok, sdetail = V.verify_semantic(s2, data, py, timeout)
    t_sem = time.time() - t0
    rec['t_two'] = round(t_run + t_sem, 3)
    rec['t_verify_run'] = round(t_run, 3)
    rec['t_verify_semantic'] = round(t_sem, 3)
    rec['old_sok'] = sok
    # ---- 新一遍 ----
    d1 = case + '_1'
    shutil.rmtree(d1, ignore_errors=True)
    shutil.copytree(case, d1)
    s1 = os.path.join(d1, os.path.basename(script))
    warns = []
    t0 = time.time()
    vp = W._verify_script(s1, data, py, timeout, semantic=True,
                          fields_by_axis=None, figsize_edited=False,
                          script_args=None, warnings=warns)
    rec['t_one'] = round(time.time() - t0, 3)
    rec['passes'] = vp['passes']
    rec['basis'] = vp.get('basis')
    rec['new_sok'] = None if vp['sok'] is W._UNSET else vp['sok']
    rec['vok'] = vp['vok']
    rec['fallback'] = (warns or [''])[0][:160]
    shutil.rmtree(case, ignore_errors=True)
    shutil.rmtree(d2, ignore_errors=True)
    shutil.rmtree(d1, ignore_errors=True)
    return rec


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--corpus', default=C.DEFAULT_CORPUS)
    ap.add_argument('--pool', default=C.DEFAULT_POOL)
    ap.add_argument('--python', default=sys.executable)
    ap.add_argument('--sample', type=int, default=24,
                    help='样本目标（与阶段 1 同 seed 抽样，取前 N 份跑得通的）')
    ap.add_argument('--per-stratum', type=int, default=40)
    ap.add_argument('--timeout', type=float, default=60.0)
    ap.add_argument('--work', default=os.path.join(
        os.path.dirname(ROOT), '.tmp_t39', 'perf_work'))
    ap.add_argument('--out', default=os.path.join(
        os.path.dirname(ROOT), '_review', 'cmp_t39_fixed', 'perf.jsonl'))
    args = ap.parse_args(argv)

    wr, prov, werr = C.load_window_risk()
    if wr is None:
        sys.stderr.write('[门禁] A 档判据装载失败：%s\n' % werr)
        return 2
    rows = C.load_pool(args.pool)
    sample, stats = C.build_sample(rows, args.corpus, max(args.sample, 120),
                                   args.per_stratum, wr)
    os.makedirs(args.work, exist_ok=True)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    recs = []
    for s in sample:
        if len([r for r in recs if not r.get('skip')]) >= args.sample:
            break
        try:
            rec = one_case(s['path'], args.corpus, args.python, args.work,
                           args.timeout)
        except Exception as e:                          # noqa: BLE001
            rec = {'path': s['path'], 'skip': '%s: %s' % (type(e).__name__, e)}
        recs.append(rec)
        print('[%2d/%d] %-58s %s'
              % (len(recs), args.sample, rec['path'][-58:],
                 ('skip=%s' % rec['skip']) if rec.get('skip')
                 else 'two=%.2fs one=%.2fs passes=%s basis=%s'
                      % (rec['t_two'], rec['t_one'], rec['passes'],
                         rec['basis'])), flush=True)
    with open(args.out, 'w', encoding='utf-8') as f:
        for r in recs:
            f.write(json.dumps(r, ensure_ascii=False) + '\n')
    good = [r for r in recs if not r.get('skip')]
    one = [r for r in good if r['passes'] == 1]
    print('\n# t39 性能对照（%d 份跑得通的可比样本；其中单遍命中 %d 份）'
          % (len(good), len(one)))
    if one:
        t2 = sum(r['t_two'] for r in one)
        t1 = sum(r['t_one'] for r in one)
        print('- 旧两遍合计 %.1fs；新一遍合计 %.1fs；省 %.1fs（%.1f%%），'
              '单份均值 %.2fs → %.2fs'
              % (t2, t1, t2 - t1, 100.0 * (t2 - t1) / max(t2, 1e-9),
                 t2 / len(one), t1 / len(one)))
        print('- basis 分布: %s' % _cnt(one, 'basis'))
        print('- 两臂判定一致份数: %d/%d'
              % (sum(1 for r in one if r['old_sok'] == r['new_sok']), len(one)))
    fb = [r for r in good if r['passes'] == 2]
    print('- 回退两遍的份数: %d（%s）' % (len(fb), _cnt(fb, 'fallback')))
    print('- 跳过: %s' % _cnt([r for r in recs if r.get('skip')], 'skip'))
    print('\n[perf] 明细=%s' % args.out)
    return 0


def _cnt(rows, key):
    from collections import Counter
    return dict(Counter((r.get(key) or '')[:60] for r in rows))


if __name__ == '__main__':
    sys.exit(main())
