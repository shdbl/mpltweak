# -*- coding: utf-8 -*-
"""脱敏自检：确认公开材料里**没有语料脚本正文**。

用法：
    python check_no_corpus_text.py .            # 扫描当前目录下所有文件

判据（两档）：
  1. JSONL / JSON 文件里**出现任何**语料正文特征串 ⇒ 失败
     （`after_text` / `before_text` / `pristine` / `source_text` / `diff_head`）；
  2. 任何**文本文件**里出现「脚本正文片段」的行 ⇒ 计数并报告（`.py` 脚手架自己的代码不算正文，
     所以对 `.py` 只报计数、不判失败；JSONL 里出现即为失败）。

退出码：0 = 干净；1 = 有命中。
"""
from __future__ import annotations

import io
import os
import sys

TEXT_KEYS = ["after_text", "before_text", "pristine", "source_text", "diff_head"]
# 只用**不可能被文件名满足**的源码特征串（`np.` / `def ` 会被 `powernp.py` 这类文件名命中）
CODE_HINTS = ["import matplotlib", "import numpy", "plt.subplots(", "plt.figure(", "ax.set_",
              "def plot", "def main", "fig.add_axes(", "savefig("]


def walk(root):
    for dirpath, _dirnames, filenames in os.walk(root):
        for fn in filenames:
            yield os.path.join(dirpath, fn)


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else "."
    bad = 0
    rows = []
    for p in sorted(walk(root)):
        rel = os.path.relpath(p, root)
        try:
            with io.open(p, encoding="utf-8", errors="replace") as f:
                txt = f.read()
        except OSError:
            continue
        ext = os.path.splitext(p)[1].lower()
        is_json = ext in (".jsonl", ".json")
        hits_key = {k: txt.count(k) for k in TEXT_KEYS if k in txt}
        hits_code = sum(txt.count(h) for h in CODE_HINTS) if ext in (".jsonl", ".json") else 0
        code_all = sum(txt.count(h) for h in CODE_HINTS)
        rows.append((rel, ext, hits_key, hits_code, code_all, os.path.getsize(p)))
        if is_json and (hits_key or hits_code):
            bad += 1
    print("%-34s %-7s %8s  %-34s %s" % ("file", "ext", "bytes", "正文特征键命中", "正文片段行命中"))
    print("-" * 110)
    for rel, ext, hk, hc, ca, size in rows:
        print("%-34s %-7s %8d  %-34s %s" % (rel[:34], ext, size,
                                            str(hk) if hk else "-",
                                            ("%d(仅 .py 源码，允许)" % ca) if ext == ".py" else hc))
    print()
    print("JSONL/JSON 里带语料正文特征的文件数 = %d（应为 0）" % bad)
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
