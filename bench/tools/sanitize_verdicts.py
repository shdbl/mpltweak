# -*- coding: utf-8 -*-
"""把静态扫描的逐脚本判定**脱敏**（白名单投影）：绝不写入语料脚本正文。

用法：
    python sanitize_verdicts.py <src_scan_full.jsonl> [<src_scan_rest.jsonl>] -o <outdir>

白名单：
  * 顶层：path / error / fig / n_figs / n_literal_sites / n_reach_sites / unreach / families
  * families[族]：判定字段（见 verdicts/README.md 的表）
  * 剔除 `after_text`（整份源码）与 `block.diff_head`（diff 头里逐行源码）；`block` 只留 start_line/end_line

红线：本脚本只按白名单取值，任何未知键都不会被写出 —— 想加字段请显式加进白名单并重跑
`check_no_corpus_text.py`。
"""
from __future__ import annotations

import argparse
import io
import json
import os

TOP_KEEP = ["path", "error", "fig", "n_figs", "n_literal_sites", "n_reach_sites", "unreach"]
FAM_KEEP = ["family", "n_sites", "targets", "multi_site", "rc", "ok", "bytes_changed",
            "decl_reason", "decl_style", "decl_applied", "decl_needs_block", "decl_pending",
            "decl_auto_fallback", "decl_inplace_reason", "decl_inplace_pending",
            "decl_verify_mode", "landed", "verify", "verify_reasons", "reloc_fail",
            "block_decl", "inplace_ref", "s3", "after_syntax_error"]
BLOCK_KEEP = ["start_line", "end_line"]


def sanitize(d):
    out = {k: d[k] for k in TOP_KEEP if k in d}
    keep = {}
    for name, info in (d.get("families") or {}).items():
        if not isinstance(info, dict):
            keep[name] = info
            continue
        entry = {k: v for k, v in info.items() if k in FAM_KEEP}
        blk = info.get("block")
        if isinstance(blk, dict):
            entry["block"] = {k: blk.get(k) for k in BLOCK_KEEP}
        keep[name] = entry
    out["families"] = keep
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("srcs", nargs="+")
    ap.add_argument("-o", "--outdir", required=True)
    a = ap.parse_args()
    os.makedirs(a.outdir, exist_ok=True)
    for src in a.srcs:
        dst = os.path.join(a.outdir, os.path.basename(src))
        n = 0
        with io.open(src, encoding="utf-8", errors="replace") as f, \
                io.open(dst, "w", encoding="utf-8", newline="\n") as g:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                g.write(json.dumps(sanitize(json.loads(line)), ensure_ascii=False) + "\n")
                n += 1
        print("  %s -> %s（%d 行，%d bytes）" % (src, dst, n, os.path.getsize(dst)))


if __name__ == "__main__":
    main()
