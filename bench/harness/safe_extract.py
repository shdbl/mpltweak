# -*- coding: utf-8 -*-
"""带**单文件硬超时**的静态抽取器（fields_harness 的安全外壳）。

背景（要修的病）
----------------
`fields_harness.Ctx` / `extract_sites` 在个别文件上**永远不返回**：
实测 `big/zenodo/7942283__P4_v01.py`（616 KB）> 590 s 未返回；而产品侧
不卡（`ast.parse` 0.12 s、`describe` 3.6 s、`check` 2.3 s）⇒ 病理分支在
harness 自己的提取器里，不在 mpltweak。同一个 corpus 里其余文件解析中位数
只有 ~0.13 s，所以这是**极少数文件的病态**，不是普遍慢。

修法（本文件 = before/after 里的 after）
----------------------------------------
进程池 + **硬超时 + 递归二分定位**：
  1. 把 manifest 切成 chunk（默认 400 个），用一个进程池跑；
  2. chunk 给一个总预算 budget；到点还没回来的任务即为嫌疑；
  3. 嫌疑集合二分（halve）后**递归重跑**，预算同比例缩小但有下限；
  4. 缩到单个文件仍不返回 → 判定 `extract_timeout`，**单列记录，绝不静默丢**；
  5. 每次超时都 `terminate()` 掉残留 worker，避免僵尸进程占住池。

对外保证
--------
- 每个 manifest 行必然出现在三者之一：`results` / `parse_fail` /
  `extract_timeout`（三者之和 == n_manifest，可断言）。
- 只做**静态** AST 抽取：不 import mpltweak、不跑 apply、不执行任何脚本。
"""
import argparse
import ast
import collections
import concurrent.futures as futures
import hashlib
import io
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import fields_harness as FH                                     # noqa: E402

CORPUS = None
FH.CORPUS = CORPUS


def _kill(ex):
    """terminate 掉进程池残留子进程，避免下一次调用被僵尸占满。"""
    try:
        procs = getattr(ex, '_processes', None)
        if procs:
            for pr in list(procs.values()):
                try:
                    pr.terminate()
                except Exception:                               # noqa: BLE001
                    pass
        ex.shutdown(wait=False, cancel_futures=True)
    except Exception:                                           # noqa: BLE001
        pass


def extract_one(corpus, rel):
    """在子进程里跑：返回 (rel, info, err)。

    `corpus` 必须**作为参数**传入：Windows 用 spawn 起子进程，子进程会重新
    import 本模块，父进程设的模块全局变量不会带过去（否则 CORPUS=None，
    表现为 `TypeError: expected str, bytes or os.PathLike object`）。
    """
    p = os.path.join(corpus, rel.replace('/', os.sep))
    if not os.path.exists(p):
        return rel, None, 'missing-file'
    with open(p, 'rb') as f:
        raw = f.read()
    text = None
    for enc in ('utf-8-sig', 'utf-8', 'gbk', 'gb18030', 'big5', 'latin-1'):
        try:
            text = raw.decode(enc)
            break
        except (UnicodeDecodeError, UnicodeEncodeError):
            continue
    if text is None:
        return rel, None, 'undecodable'
    try:
        tree = ast.parse(text)
    except SyntaxError as e:
        return rel, None, 'syntax: %s' % str(e)[:120]
    except Exception as e:                                      # noqa: BLE001
        return rel, None, 'parse-error: %r' % (e,)
    ctx = FH.Ctx(text, tree)
    sites = FH.extract_sites(ctx)
    ni = FH.extract_non_inplace_sites(ctx)
    return rel, {
        'n_figs': ctx.n_figs,
        'sites': {f: [(s['kind'], bool(s['reach']), s.get('reason'))
                      for s in ss] for f, ss in sites.items() if ss},
        'nonin': {f: len(v) for f, v in ni.items() if v},
    }, None


def _run_chunk(corpus, rels, jobs, budget):
    """跑一个 chunk，返回 (done: {rel: (info, err)}, remaining: [rel])。"""
    ex = futures.ProcessPoolExecutor(max_workers=jobs)
    done = {}
    futs = {}
    try:
        for r in rels:
            futs[ex.submit(extract_one, corpus, r)] = r
        try:
            it = futures.as_completed(list(futs), timeout=budget)
            for fu in it:
                rel = futs[fu]
                try:
                    rr, info, err = fu.result(timeout=1)
                except Exception as e:                          # noqa: BLE001
                    rr, info, err = rel, None, 'worker: %r' % (e,)
                done[rel] = (info, err)
        except futures.TimeoutError:
            pass
    finally:
        remaining = [r for r in rels if r not in done]
        if remaining:
            _kill(ex)
        else:
            try:
                ex.shutdown(wait=False)
            except Exception:                                   # noqa: BLE001
                pass
    return done, remaining


def scan(corpus, manifest, jobs=10, chunk=400, per_task=2.0,
         min_budget=20.0, log=None):
    global CORPUS
    CORPUS = corpus
    FH.CORPUS = corpus
    rows = [json.loads(l) for l in io.open(manifest, encoding='utf-8')
            if l.strip()]
    rels = [r['path'] for r in rows]
    results = {}
    parse_fail = []
    timeouts = []
    t0 = time.time()

    for ci in range(0, len(rels), chunk):
        block = rels[ci:ci + chunk]
        queue = [(block, max(min_budget, len(block) / float(jobs) * per_task))]
        while queue:
            group, budget = queue.pop(0)
            done, remaining = _run_chunk(corpus, group, jobs, budget)
            for rel, (info, err) in done.items():
                if info is None:
                    parse_fail.append(rel + '\t' + (err or '?'))
                else:
                    results[rel] = info
            if remaining:
                if len(remaining) == 1:
                    timeouts.append(remaining[0])
                    if log:
                        log('  extract_timeout: %s (budget %.0fs exceeded)'
                            % (remaining[0], budget))
                else:
                    half = len(remaining) // 2
                    sub = max(min_budget, budget / 2.0)
                    queue.insert(0, (remaining[half:], sub))
                    queue.insert(0, (remaining[:half], sub))
        if log:
            log('  chunk %d/%d  done=%d fail=%d timeout=%d  %.0fs'
                % (min(ci + chunk, len(rels)), len(rels), len(results),
                   len(parse_fail), len(timeouts), time.time() - t0))
    total = len(results) + len(parse_fail) + len(timeouts)
    assert total == len(rows), 'accounting broken: %d != %d' % (total,
                                                                len(rows))
    return {'corpus': corpus, 'manifest': manifest,
            'n_manifest': len(rows),
            'n_parse_ok': len(results),
            'parse_fail': parse_fail,
            'extract_timeout': timeouts,
            'results': results,
            'seconds': round(time.time() - t0, 1)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--corpus', required=True)
    ap.add_argument('--manifest', default=None)
    ap.add_argument('--out', required=True)
    ap.add_argument('--jobs', type=int, default=10)
    ap.add_argument('--chunk', type=int, default=400)
    ap.add_argument('--per-task', type=float, default=2.0)
    args = ap.parse_args()
    mf = args.manifest or os.path.join(args.corpus, 'manifest.jsonl')

    def log(m):
        print(m, flush=True)

    out = scan(args.corpus, mf, jobs=args.jobs, chunk=args.chunk,
               per_task=args.per_task, log=log)
    tmp = args.out + '.tmp'
    io.open(tmp, 'w', encoding='utf-8').write(
        json.dumps(out, ensure_ascii=False))
    os.replace(tmp, args.out)
    log('WROTE %s  n=%d ok=%d fail=%d timeout=%d  %.0fs'
        % (args.out, out['n_manifest'], out['n_parse_ok'],
           len(out['parse_fail']), len(out['extract_timeout']),
           out['seconds']))
    for p in out['extract_timeout']:
        log('  TIMEOUT %s' % p)


if __name__ == '__main__':
    main()
