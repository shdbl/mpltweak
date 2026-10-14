# -*- coding: utf-8 -*-
"""发布前脱敏：把本机证据材料复制进 bench 证据目录，并抹掉本机绝对路径。

用法：
    python bench/tools/sanitize_evidence.py \
        --src "D:\\Agent\\deepseek\\_evidence_freeze\\0.1.10" \
        --dst "bench/evidence/0.1.10"

    # 新版本的**增量包**（目录结构与 0.1.10 不同）：用 --walk 原样遍历，不必维护清单
    python bench/tools/sanitize_evidence.py --walk \
        --src "D:\\Agent\\deepseek\\_evidence_freeze\\0.1.11" \
        --dst "bench/evidence/0.1.11"

设计原则（为什么这样做，而不是"重写文件"）：
  1. **只在字符串内部做定点替换**：不解析重排、不改结构，保持原文件可逐行审计；
  2. **规则按"最具体优先"排序**，逐条计数，跑完打印审计表（哪个规则、改了几处、样例）；
  3. **脱敏后必须过红线守卫** `check_evidence_publishable.py`；未过不得提交；
  4. **改写 JSONL 后逐行 json.loads 复验**，防止替换把 JSON 弄坏（静默弄坏比不脱敏更糟）。

植入口径：本机工作根 = `<work>`；语料根 = `<corpus_root>`；沙箱根 = `<sandbox_root>`；
        证据目录 = `<evidence>`；临时区 = `<tmp>`；解释器 = `<python>`；家目录 = `<home>`。
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys

SEP = r'[\\/]{1,2}'

# 规则：名字 -> (正则, 替换串)。**顺序即优先级**：具体在前，兜底在后。
RULES = [
    ('python_exe', re.compile(r'[A-Za-z]:' + SEP + r'Miniconda' + SEP + r'envs' + SEP
                              + r'longxia' + SEP + r'python\.exe'), '<python>'),
    ('sandbox_root', re.compile(r'[A-Za-z]:' + SEP + r'Agent' + SEP + r'deepseek' + SEP
                                + r'\.tmp_t\d+' + SEP + r'\.mpltweak_corpus_\d+'
                                + SEP + r'sandbox'), '<sandbox_root>'),
    ('pool_file', re.compile(r'[A-Za-z]:' + SEP + r'Agent' + SEP + r'deepseek' + SEP
                             + r'_review' + SEP + r'corpus_filter_t7' + SEP
                             + r'filter\.jsonl'), '<evidence>/corpus_filter_t7/filter.jsonl'),
    ('corpus_root', re.compile(r'[A-Za-z]:' + SEP + r'Agent' + SEP + r'deepseek' + SEP
                               + r'corpus_union'), '<corpus_root>'),
    ('window_evidence', re.compile(r'[A-Za-z]:' + SEP + r'Agent' + SEP + r'deepseek' + SEP
                                   + r'_tmp_t16_real'), '<evidence>/window_logs'),
    ('tmp_tests', re.compile(r'[A-Za-z]:' + SEP + r'Agent' + SEP + r'deepseek' + SEP
                             + r'_tmp_tests'), '<tmp>'),
    ('review_root', re.compile(r'[A-Za-z]:' + SEP + r'Agent' + SEP + r'deepseek' + SEP
                               + r'_review'), '<evidence>'),
    ('agent_root', re.compile(r'[A-Za-z]:' + SEP + r'Agent' + SEP + r'deepseek'), '<work>'),
    ('home', re.compile(r'[A-Za-z]:' + SEP + r'Users' + SEP + r'[A-Za-z0-9_.\-]+'), '<home>'),
    ('conda_env', re.compile(r'[A-Za-z]:' + SEP + r'Miniconda' + SEP + r'envs' + SEP
                             + r'[A-Za-z0-9_.\-]+'), '<conda_env>'),
    # 兜底：任何残留的盘符路径（排除引号/空白/常见分隔符，避免吃掉整行）
    ('any_abs', re.compile(r'[A-Za-z]:' + SEP + r'[^"\s\'`\)\]\},;]{3,}'), '<abs>'),
]

# 大小写不敏感：JSON 里出现的路径常是小写（d:\\agent\\...），而具体规则写的是正常大小写。
# 不加这个，具体规则会漏配、退到兜底的 <abs>，证据可读性变差（语义丢失）。
RULES = [(name, re.compile(pat.pattern, pat.flags | re.IGNORECASE), repl)
         for name, pat, repl in RULES]

# 要发布的文件：源（相对 --src） -> 目标（相对 --dst）
FILES = [
    ('corpus_postfix/results.jsonl', 'corpus/results.jsonl'),
    ('corpus_postfix/SUMMARY.md', 'corpus/SUMMARY.md'),
    ('corpus_postfix/RUN_META.json', 'corpus/RUN_META.json'),
    ('corpus_postfix/RUN_OBSERVATIONS.json', 'corpus/RUN_OBSERVATIONS.json'),
    ('corpus_filter/filter.jsonl', 'corpus/filter.jsonl'),
    ('corpus_filter/FILTER.md', 'corpus/FILTER.md'),
    ('window_numbers/NUMBERS.md', 'window/NUMBERS.md'),
    ('window_numbers/tk_huge_transcript.log', 'window/tk_huge_transcript.log'),
    ('window_logs/run_qt9.log', 'window/run_qt9.log'),
    ('window_logs/run_tk7.log', 'window/run_tk7.log'),
    ('window_logs/selftest_t22.log', 'window/selftest_t22.log'),
    ('window_logs/ledger_drylist.txt', 'window/ledger_drylist.txt'),
    ('sandbox/SANDBOX.md', 'sandbox/SANDBOX.md'),
    # 单遍化（写回验证 2 遍 → 1 遍）的等价性证据：before / after / 性能 / 归因
    ('onepass_cmp/STAGE1_FINDINGS.md', 'onepass/before/STAGE1_FINDINGS.md'),
    ('onepass_cmp/SUMMARY.md', 'onepass/before/SUMMARY.md'),
    ('onepass_cmp/results.jsonl', 'onepass/before/results.jsonl'),
    ('onepass_cmp/RUN_META.json', 'onepass/before/RUN_META.json'),
    ('onepass_cmp/CLOSE_PROBE_cfd75bbb.json', 'onepass/before/CLOSE_PROBE_cfd75bbb.json'),
    ('onepass_cmp/NONDET.jsonl', 'onepass/before/NONDET.jsonl'),
    ('onepass_small/close_probe_key_values.txt', 'onepass/before/close_probe_key_values.txt'),
    ('onepass_after/results_FINAL.jsonl', 'onepass/after/results_FINAL.jsonl'),
    ('onepass_after/SUMMARY_FINAL.md', 'onepass/after/SUMMARY_FINAL.md'),
    ('onepass_after/NONDET.jsonl', 'onepass/after/NONDET.jsonl'),
    ('onepass_after/perf_final.jsonl', 'onepass/perf_final.jsonl'),
    ('onepass_small/pong_rc_stability.log', 'onepass/pong_rc_stability.log'),
    # 离屏矩阵：0.1.10 发布时这轮日志曾丢失（写在被 gitignore 的临时目录），此处是重跑生成的证据
    ('matrix/matrix_utf8.log', 'matrix/matrix_utf8.log'),
    ('matrix/matrix_gbk.log', 'matrix/matrix_gbk.log'),
    # t54：离屏矩阵数字表（体例同 window/NUMBERS.md，每行带「日志文件:行」）
    ('matrix/NUMBERS_matrix.md', 'matrix/NUMBERS_matrix.md'),
]


def read_text(path):
    """优先 UTF-8，失败回退 GBK（窗口日志可能是 GBK 控制台产物）。"""
    with open(path, 'rb') as fh:
        raw = fh.read()
    for enc in ('utf-8', 'gbk'):
        try:
            return raw.decode(enc), enc
        except UnicodeDecodeError:
            continue
    return raw.decode('utf-8', errors='replace'), 'utf-8/replace'


def sanitize(text, audit):
    for name, pat, repl in RULES:
        text, n = pat.subn(repl, text)
        if n:
            audit.setdefault(name, {'n': 0, 'samples': []})
            audit[name]['n'] += n
    return text


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--src', required=True)
    ap.add_argument('--dst', required=True)
    ap.add_argument('--walk', action='store_true',
                    help='把 --src 下的**所有**文件按相同相对路径脱敏进 --dst。'
                         '默认走上面的 FILES（0.1.10 的固定清单）；'
                         '新版本的**增量包**用这个开关，不必再维护一份清单。'
                         '脱敏规则、JSON 复验、红线守卫完全一致。')
    args = ap.parse_args()

    if args.walk:
        pairs = []
        for dirpath, dirnames, filenames in os.walk(args.src):
            dirnames[:] = [d for d in dirnames if d not in ('__pycache__',)]
            for fn in sorted(filenames):
                p = os.path.join(dirpath, fn)
                rel = os.path.relpath(p, args.src).replace('\\', '/')
                pairs.append((rel, rel))
        pairs.sort()
    else:
        pairs = FILES

    total = 0
    failures = []
    print('=== 脱敏：%s -> %s ===' % (args.src, args.dst))
    print('=== 清单来源：%s（%d 项）===' % ('--walk 遍历' if args.walk else 'FILES',
                                        len(pairs)))
    for rel_src, rel_dst in pairs:
        src = os.path.join(args.src, rel_src)
        dst = os.path.join(args.dst, rel_dst)
        if not os.path.exists(src):
            print('  [缺] %s' % rel_src)
            failures.append(rel_src)
            continue
        text, enc = read_text(src)
        before = text
        audit = {}
        text = sanitize(text, audit)
        # 审计样例：抓替换前的原文（再跑一次抓样本）
        for name, pat, repl in RULES:
            for m in list(pat.finditer(before))[:2]:
                audit.setdefault(name, {'n': 0, 'samples': []})
                if len(audit[name]['samples']) < 2:
                    audit[name]['samples'].append(m.group(0)[:120])
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with io.open(dst, 'w', encoding='utf-8', newline='\n') as fh:
            fh.write(text)
        changed = sum(v['n'] for v in audit.values())
        total += changed
        print('  %-42s enc=%-6s 替换 %d 处' % (rel_src, enc, changed))
        for name in sorted(audit, key=lambda k: -audit[k]['n']):
            v = audit[name]
            if v['n']:
                print('       - %-14s x%-3d 例: %s' % (name, v['n'], v['samples'][0] if v['samples'] else ''))

        # JSONL 完整性复验（替换不得弄坏 JSON）
        if rel_dst.endswith('.jsonl'):
            bad = 0
            with io.open(dst, 'r', encoding='utf-8') as fh:
                for i, line in enumerate(fh, 1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        json.loads(line)
                    except Exception as exc:  # noqa: BLE001
                        bad += 1
                        if bad <= 3:
                            print('       !! JSON 破坏 行%d: %s' % (i, exc))
            print('       JSONL 复验: %s' % ('全部可解析' if bad == 0 else ('%d 行坏了' % bad)))
            if bad:
                failures.append(rel_dst + ' (json)')

    print()
    print('=== 合计替换 %d 处；缺失/损坏 %d 项 ===' % (total, len(failures)))
    for f in failures:
        print('  !! %s' % f)
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
