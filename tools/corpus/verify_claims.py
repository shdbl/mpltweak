# -*- coding: utf-8 -*-
"""语料差分跑的**机检清单**（仓内）：把 CLAIMS.json 里能机检的断言从磁盘重算一遍。

## 这个文件为什么入仓

16k 语料差分跑（v0.1.12 vs 工作树，两臂 × inplace/block）的全部产物**体积太大**，
只能留在仓外（一次跑约 78 MB / 717 个文件，见 docs/dev.md「同类语料验证」）。
但**清单与校验脚本**体积只有几十 KB，而且它们才是"这些数字凭什么可信"的那部分：

* `CLAIMS.json`  —— 12 条断言的原文 + 每条的证据文件 + 复跑命令 + 期望值；
* `MANIFEST.json` —— 产物清单（282 个文件的路径 / 字节数 / sha256），
  即"当时到底有哪些证据、有没有被人改过"；
* 本文件 —— 把 `CLAIMS.json` 里**能机检**的那些断言从分片 JSONL 重新算一遍
  （不信任任何人手抄进 README 的数字）。

0.1.15 只把入口 `tools/corpus_diff.py` 收进仓，这三份**证据骨架**仍在仓外 ⇒
仓库里能"跑差分"，却拿不出"上次跑的结果凭什么算数"。0.1.16 把它们补进来。

## 用法

    python tools/corpus/verify_claims.py                    # 默认：跑机检，全 PASS 才 rc=0
    python tools/corpus/verify_claims.py --list             # 只打印 12 条断言（不需要语料）
    python tools/corpus/verify_claims.py --manifest         # 清单自检：仓内 CLAIMS.json 的
                                                           #   sha256 是否与 MANIFEST 记录一致
    python tools/corpus/verify_claims.py --work <仓外证据目录>
    python tools/corpus/verify_claims.py --summary-only     # 只打聚合数字，不逐条判（rc=0）

退出码（与仓库其它工具同口径）：0 = 全 PASS / 没发现问题；1 = 有 FAIL 或**仓外证据缺失**；
2 = 用法错。**缺语料时只打印人话 + 列出缺哪几样，绝不抛 Traceback。**

## 仓外路径怎么给（参数化）

证据目录（含 `meta/` 与 `out/`）默认取仓库**上一级**的 `_corpus013/`，可改：

* `--work DIR`
* 或环境变量 `MPLTWEAK_CORPUS_WORK`（与 `tools/corpus_diff.py` 同一个变量，口径一致）

本文件**不含任何绝对路径**：`tests/test_hygiene.py` 的牙齿会核对这一点
（写死盘符路径就等于把别人的机器名搬进仓库）。
"""

import argparse
import hashlib
import io
import json
import os
import sys

#: 本文件所在目录（CLAIMS.json / MANIFEST.json 与它同级）
HERE = os.path.dirname(os.path.abspath(__file__))
#: 仓库根（tools/corpus/ 往上两级）
ROOT = os.path.dirname(os.path.dirname(HERE))
#: 仓外证据目录的默认值：环境变量优先，其次「仓库的上一级目录」里的 _corpus013
DEFAULT_WORK = os.environ.get('MPLTWEAK_CORPUS_WORK',
                              os.path.join(os.path.dirname(ROOT), '_corpus013'))
CLAIMS_PATH = os.path.join(HERE, 'CLAIMS.json')
MANIFEST_PATH = os.path.join(HERE, 'MANIFEST.json')
#: 机检需要读的仓外证据（缺一样就给人话，不去猜）
STYLES = ('inplace', 'block')
ARMS = ('A', 'B')
REQUIRED_FILES = ('meta/corpus_manifest.jsonl', 'meta/arms.json',
                  'out/summary_inplace.json', 'out/summary_block.json')
REQUIRED_DIRS = tuple('out/%s/%s' % (arm, style)
                      for style in STYLES for arm in ARMS)


def _say(msg=''):
    print(msg)


def _err(msg):
    sys.stderr.write('verify_claims: ' + msg + '\n')


def _rel(path):
    try:
        return os.path.relpath(path, ROOT).replace(os.sep, '/')
    except ValueError:
        return path.replace(os.sep, '/')


def _sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(65536), b''):
            h.update(chunk)
    return h.hexdigest()


def _load_json(path):
    with io.open(path, encoding='utf-8') as f:
        return json.load(f)


# ---------------------------------------------------------------- 三种模式
def list_claims(claims_path=CLAIMS_PATH):
    """打印 CLAIMS.json 里的断言清单（只读 json，不需要任何语料）。"""
    if not os.path.isfile(claims_path):
        _err('清单不在：%s（它应该和本脚本同级入仓）' % claims_path)
        return 1
    data = _load_json(claims_path)
    claims = data.get('claims') or []
    _say('清单：%s' % _rel(claims_path))
    _say('任务：%s' % data.get('task', '(没写)'))
    _say('断言：%d 条' % len(claims))
    _say('')
    for i, c in enumerate(claims, 1):
        _say('%2d. %-22s %s' % (i, c.get('id', '?'), c.get('claim', '')))
        _say('    证据：%s' % ', '.join(c.get('evidence') or []))
        _say('    期望：%s' % (c.get('expected'),))
    return 0


def check_manifest(claims_path=CLAIMS_PATH, manifest_path=MANIFEST_PATH):
    """清单自检：仓内 CLAIMS.json 的 sha256 必须等于 MANIFEST.json 里记录的那个。

    这是"入仓的证据骨架没被人手改过"的机检点：MANIFEST 是跑完那次差分时生成的，
    里面第 1 条就是 `CLAIMS.json` 的 sha256。改了仓内这份而没重生成清单 ⇒ 红。
    """
    for p in (claims_path, manifest_path):
        if not os.path.isfile(p):
            _err('缺文件：%s（CLAIMS.json 与 MANIFEST.json 应同时入仓）' % p)
            return 1
    man = _load_json(manifest_path)
    files = man.get('files') or []
    recorded = None
    for row in files:
        if row.get('path') == 'CLAIMS.json':
            recorded = row
            break
    _say('清单：%s（%d 个文件，记录于 %s）'
         % (_rel(manifest_path), len(files), man.get('task', '(没写任务)')))
    top = man.get('by_top_level') or {}
    if top:
        _say('     顶层分布：%s'
             % ', '.join('%s=%d' % (k, v) for k, v in sorted(top.items())))
    if recorded is None:
        _err('MANIFEST.json 里没有 CLAIMS.json 这一条 —— 清单与证据对不上')
        return 1
    got = _sha256(claims_path)
    want = recorded.get('sha256')
    ok = (got == want) and (os.path.getsize(claims_path) == recorded.get('bytes'))
    _say('  %-34s got=%s  want=%s  %s'
         % ('CLAIMS.json sha256', got[:16] + '...', str(want)[:16] + '...',
            'PASS' if ok else 'FAIL'))
    if not ok:
        _say('')
        _say('结论：**清单与仓内证据对不上** —— 仓内的 CLAIMS.json 与那次差分跑记录的')
        _say('      不是同一份（字节数 %d / 记录 %s）。要么是有人改了断言，要么是复制错了。')
        _say('      正确做法：不要手改 CLAIMS.json；新一次差分跑应当重生成清单。')
        return 1
    _say('')
    _say('结论：清单自洽（仓内 CLAIMS.json 与 MANIFEST 记录的 sha256 一致）。')
    return 0


def check_evidence(work):
    """→ 缺失项列表（人话字符串）。全齐返回 []。"""
    missing = []
    for rel in REQUIRED_FILES:
        p = os.path.join(work, rel)
        if not os.path.isfile(p):
            missing.append('%s（缺文件）' % rel)
    for rel in REQUIRED_DIRS:
        d = os.path.join(work, rel)
        if not os.path.isdir(d):
            missing.append('%s（缺目录）' % rel)
        elif not [f for f in os.listdir(d) if f.endswith('.jsonl')]:
            missing.append('%s（目录里没有 shard_*.jsonl 分片）' % rel)
    return missing


# ---------------------------------------------------------------- 机检本体
def _count_rows(work, arm, style):
    """数一个 (arm, style) 下的记录数与分片数。"""
    d = os.path.join(work, 'out', arm, style)
    n = 0
    shards = 0
    for fn in sorted(os.listdir(d)):
        if not fn.endswith('.jsonl'):
            continue
        shards += 1
        with io.open(os.path.join(d, fn), encoding='utf-8') as f:
            for line in f:
                if '"__meta__"' in line[:60]:
                    continue
                n += 1
    return n, shards


def _count_exc(work, arm):
    """数某臂 inplace 波里带 `"exc"` 且是 AttributeError 的行。"""
    exc = 0
    d = os.path.join(work, 'out', arm, 'inplace')
    for fn in sorted(os.listdir(d)):
        if not fn.endswith('.jsonl'):
            continue
        for line in io.open(os.path.join(d, fn), encoding='utf-8'):
            if '"exc"' in line and 'AttributeError' in line:
                exc += 1
    return exc


def _trans(s, frm, to):
    for t in s.get('transitions') or []:
        if t.get('from') == frm and t.get('to') == to:
            return t.get('n')
    return 0


def run_checks(work):
    """把能机检的断言从磁盘重算一遍；→ 失败条数。"""
    fails = []

    def check(name, got, want):
        ok = got == want
        _say('  %-58s got=%-8s want=%-8s %s'
             % (name, got, want, 'PASS' if ok else 'FAIL'))
        if not ok:
            fails.append(name)

    _say('C1 分母')
    man_path = os.path.join(work, 'meta', 'corpus_manifest.jsonl')
    man = [l for l in io.open(man_path, encoding='utf-8') if l.strip()]
    check('manifest 行数', len(man), 16126)
    buckets = {}
    for line in man:
        top = json.loads(line)['rel'].split('/')[0]
        buckets[top] = buckets.get(top, 0) + 1
    check('018/big/harness',
          (buckets.get('018'), buckets.get('big'), buckets.get('harness')),
          (1676, 8482, 5968))

    _say('C4 两臂跑完 + 分片数')
    for style in STYLES:
        for arm in ARMS:
            n, shards = _count_rows(work, arm, style)
            check('%s/%s 记录数' % (style, arm), n, 16126)
            check('%s/%s 分片数' % (style, arm), shards, 32)

    _say('C5/C6/C10 聚合数字')
    for style in STYLES:
        s = _load_json(os.path.join(work, 'out', 'summary_%s.json' % style))
        check('%s denominator' % style, s['denominator'], 16126)
        check('%s bad_params A' % style, s['reasons_A'].get('bad_params'), 1214)
        check('%s bad_params B' % style, s['reasons_B'].get('bad_params'), 170)
        check('%s changes_mismatch(同 reason)' % style,
              s['changes_mismatch_same_reason'], 0)
        _say('  %s 迁移：%s'
             % (style, json.dumps(s['transitions'], ensure_ascii=False)))
    s = _load_json(os.path.join(work, 'out', 'summary_inplace.json'))
    check('inplace byte_mismatch(同 reason)', s['byte_mismatch_same_reason'], 0)
    check('inplace changed/same', (s['changed'], s['same_reason']), (1082, 15044))
    check('inplace n_changes>0 A/B',
          (s['n_changes_gt0_A'], s['n_changes_gt0_B']), (3901, 4750))
    check('inplace 迁移 bad->best_effort', _trans(s, 'bad_params', 'best_effort'),
          1054)
    check('inplace 迁移 best_effort->bad', _trans(s, 'best_effort', 'bad_params'),
          19)
    check('inplace 迁移 bad->ok', _trans(s, 'bad_params', 'ok'), 5)
    check('inplace 迁移 bad->EXC', _trans(s, 'bad_params', 'EXC:AttributeError'), 4)
    s = _load_json(os.path.join(work, 'out', 'summary_block.json'))
    check('block byte_mismatch(同 reason)', s['byte_mismatch_same_reason'], 945)
    check('block n_changes>0 A/B',
          (s['n_changes_gt0_A'], s['n_changes_gt0_B']), (2015, 2561))

    _say('C11 存量 AttributeError 份数')
    check('A EXC:AttributeError', _count_exc(work, 'A'), 34)
    check('B EXC:AttributeError', _count_exc(work, 'B'), 38)

    _say('C2/C3 两臂身份')
    arms = _load_json(os.path.join(work, 'meta', 'arms.json'))
    check('armA tag', arms['tag'], 'v0.1.12')
    check('armA tag_commit', arms['tag_commit'],
          '6538ae9a1b1fff4ba68a1276828565c80c8c6fff')
    check('armA tag_tree', arms['tag_tree'],
          'e8d3e877ba3c65d43157ef7dedd30e147e2a1c62')
    check('armB 真实改动文件数', len(arms['changed_real']), 3)
    check('armB 仅 EOL 差异文件数', len(arms['changed_eol_only']), 2)
    return fails


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog='verify_claims.py',
        description='语料差分跑的机检清单：把 CLAIMS.json 里能机检的断言从磁盘重算一遍。',
        epilog='仓外证据目录用 --work 或环境变量 MPLTWEAK_CORPUS_WORK 给；'
               '本脚本不含绝对路径。')
    ap.add_argument('--work', default=DEFAULT_WORK,
                    help='仓外证据目录（含 meta/ 与 out/）；默认取环境变量 '
                         'MPLTWEAK_CORPUS_WORK，再退回仓库上一级的 _corpus013/')
    ap.add_argument('--claims', default=CLAIMS_PATH, help='CLAIMS.json 路径（默认与脚本同级）')
    ap.add_argument('--manifest', default=MANIFEST_PATH,
                    help='MANIFEST.json 路径（默认与脚本同级）')
    ap.add_argument('--list', action='store_true', help='只打印断言清单（不需要语料）')
    ap.add_argument('--manifest-check', action='store_true',
                    help='清单自检（仓内 CLAIMS.json 与 MANIFEST 记录的 sha256）')
    ap.add_argument('--summary-only', action='store_true',
                    help='只打聚合数字，不逐条判（rc 恒 0；人读用）')
    args = ap.parse_args(argv)

    if args.list:
        return list_claims(args.claims)
    if args.manifest_check:
        return check_manifest(args.claims, args.manifest)

    work = os.path.abspath(args.work)
    _say('证据目录：%s' % work)
    if not os.path.isdir(work):
        _err('看不到这个目录：%s\n'
             '     这份清单校验的是**仓外**那一次差分跑的产物（语料本体 / 两臂 worktree / '
             '128 个分片 JSONL），\n'
             '     它们刻意不入仓（约 78 MB）。\n'
             '     要指到别处：--work DIR，或设环境变量 MPLTWEAK_CORPUS_WORK。\n'
             '     只想看断言清单：--list；只想自检清单：--manifest-check。'
             % work)
        return 1
    missing = check_evidence(work)
    if missing:
        _err('证据不全 —— 缺 %d 样：' % len(missing))
        for m in missing:
            _err('  * ' + m)
        _err('     先跑一次差分（python tools/corpus_diff.py --plan 里有完整命令），'
             '或把 --work 指到跑过的那份工作区。')
        return 1

    fails = run_checks(work)
    _say('')
    if args.summary_only:
        _say('（--summary-only：只打数字，不判 rc；FAIL 条数 = %d）' % len(fails))
        return 0
    _say('FAIL 数：%d %s' % (len(fails), fails))
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main())
