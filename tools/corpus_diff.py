# -*- coding: utf-8 -*-
"""语料差分跑的**仓内入口**：16k 语料 × 两条写回路径（inplace / block）的可复制命令。

## 这个工具为什么存在

0.1.13 之前做过一次「全池语料差分」（v0.1.12 vs 工作树，两臂 × 两 style），那套 harness
当时只落在**会话临时目录** `_corpus013/` 里，仓库里没有任何入口 —— 下次做同类验证只能
重新拼命令，**口径（分母 / 解释器 / 参数 / 环境变量）也就跟着重新猜一遍**。
本工具把那次的口径固化成三件事：

1. `--plan`  ：把「两臂怎么跑」打印成一串可直接复制的命令（含 env 口径）；
2. `--check` ：就绪自检（语料在不在 / harness 在不在 / 解释器是哪个），**缺了就给人话**；
3. `--baseline`：从 `out/` 的分片 JSONL 重算 I2 的回归指标，与内置基线比对。

语料本体 / GIF / 图片**刻意不入仓**（体积），所以这些路径一律参数化：
`--corpus` / `--work` 两个开关，或环境变量 `MPLTWEAK_CORPUS` / `MPLTWEAK_CORPUS_WORK`。

## 用法

    python tools/corpus_diff.py --plan                 # 打印可复制的命令（默认动作）
    python tools/corpus_diff.py --check                # 就绪自检（rc=0 全齐 / 1 有缺 / 2 用法错）
    python tools/corpus_diff.py --baseline             # 重算 I2 指标并与基线比对
    python tools/corpus_diff.py --run --style inplace  # 真跑一波（launch → 聚合 → 反证 → 逐条验收）
    python tools/corpus_diff.py --run --style block --dry-run   # 只打印将要执行的命令

退出码（与仓库其它工具同口径）：0 = 没问题；1 = 有缺项 / 基线漂移；2 = 用法错（参数、
路径不存在、语料目录看不到）。**任何路径问题都只打印人话，不抛栈。**

## 分母口径（最容易搞错的一条）

`corpus_union` **递归** `*.py` = 16,126 份 = `018` 1,676 + `big` 8,482 + `harness` 5,968。
其中 `harness` 那 5,968 份是**本项目自己的脚手架脚本**，不是第三方语料 —— 谈「真实用户
脚本」时分母用 `big + 018 = 10,158`。写数字时必须带上用的是哪个分母（见 docs/dev.md）。
"""

import argparse
import glob
import io
import json
import os
import subprocess
import sys

#: 仓库根（tools/ 的上一级）
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
#: 语料根与 harness 工作区的默认值：环境变量优先，其次「仓库的上一级目录」
DEFAULT_CORPUS = os.environ.get('MPLTWEAK_CORPUS',
                                os.path.join(os.path.dirname(ROOT), 'corpus_union'))
DEFAULT_WORK = os.environ.get('MPLTWEAK_CORPUS_WORK',
                              os.path.join(os.path.dirname(ROOT), '_corpus013'))
#: harness 里本工具会用到的脚本（都在工作区 harness/ 下）
HARNESS_SCRIPTS = ('freeze_arms.py', 'build_manifest.py', 'launch.py',
                   'aggregate.py', 'probe_xtab.py', 'verify_claims.py',
                   'make_manifest.py', 'runner.py')
STYLES = ('inplace', 'block')

#: I2 回归基线（0.1.12 → 0.1.13 那次全池差分跑，见 _corpus013/README.md 与
#: SUMMARY_inplace.md 的 §2；分片可用 --baseline 重算，两边必须对得上）
BASELINE = {
    'denominator': 16126,          # corpus_union 递归 *.py
    'bad_params_A': 1214,          # A 臂（v0.1.12）拒写份数
    'bad_params_B': 170,           # B 臂（本工作树）拒写份数
    'dpi_warning_files': 166,      # B 臂带 dpi「静态判不出」warning 的**份数**（inplace，1.03%）
    'a_rejected_b_passed': 44,     # 其中「A 拒 bad_params → B 放行」的份数（0.27%）
}
#: 阈值：拒写数**不许上升**；dpi 放行份数允许下降（改善），上升超过这个比例才报漂移
DPI_GROWTH_LIMIT = 0.10


def _rel(path):
    try:
        return os.path.relpath(path, ROOT).replace(os.sep, '/')
    except ValueError:
        return path.replace(os.sep, '/')


def _say(msg=''):
    print(msg)


def _err(msg):
    sys.stderr.write('corpus_diff: ' + msg + '\n')


def plan(corpus, work, python):
    """打印「两臂怎么跑」的固定命令（可直接复制；不执行任何东西）。"""
    _say('语料根    : %s' % corpus)
    _say('harness   : %s' % os.path.join(work, 'harness'))
    _say('解释器    : %s' % python)
    _say('')
    _say('口径（两臂必须一致，改一项就等于换了实验）：')
    _say('  * 语料清单 : harness/build_manifest.py 产出的 meta/corpus_manifest.jsonl（按 rel 字典序）')
    _say('  * 分母     : corpus_union 递归 *.py = 16,126（018 1,676 + big 8,482 + harness 5,968）')
    _say('  * 参数     : version=4, fig_index=0, figsize_px=[600,400], figsize_in=[6.0,4.0]'
         ' => 隐含 dpi=100；axes[0] title 13 / label 12 / tick 11 / grid True；verify=False')
    _say('  * 环境     : MPLBACKEND=Agg；HOME/USERPROFILE/TMP/TEMP/MPLCONFIGDIR 全指向工作区的')
    _say('               home/ 与 tmp/（不写用户真实家目录 —— 这一步由 harness/launch.py 负责）')
    _say('')
    _say('# 0) 一次性准备（已跑过；重跑会重建两臂与清单）')
    _say('cd %s' % os.path.join(work, 'harness'))
    _say('%s freeze_arms.py        # A=v0.1.12 worktree, B=本工作树 src 快照' % python)
    _say('%s build_manifest.py     # 生成 meta/corpus_manifest.jsonl（分母来源）' % python)
    _say('')
    for style in STYLES:
        _say('# 1) style=%s：两臂交替派发（32 分片 × 2 臂 = 64 个作业，可中断续跑）' % style)
        _say('$env:NSHARD=%d; $env:JOBS=4; $env:STYLE=%s; %s launch.py' % (32, style, python))
        _say('# 2) 聚合 -> out/SUMMARY_%s.md + out/summary_%s.json' % (style, style))
        _say('$env:START=%s; %s aggregate.py' % (style, python))
        _say('# 3) 反方向取证 -> out/REVERSE_%s.md' % style)
        _say('$env:STYLE=%s; %s probe_xtab.py > ..\\out\\REVERSE_%s.md' % (style, python, style))
    _say('')
    _say('# 4) 逐条验收：把 CLAIMS.json 里能机检的断言从磁盘重算（全 PASS 才 rc=0）')
    _say('%s verify_claims.py' % python)
    _say('# 5) 产物清单（路径 / 字节数 / sha256）')
    _say('%s make_manifest.py' % python)
    _say('# 6) I2 回归基线（本工具的仓内入口，重算并比对）')
    _say('%s %s --baseline --corpus "%s" --work "%s"'
         % (python, _rel(os.path.abspath(__file__)), corpus, work))
    return 0


def check(corpus, work, python):
    """就绪自检：缺什么就直说（不抛栈），rc=0 全齐 / 1 有缺。"""
    missing = []
    _say('== 语料根 ==')
    if os.path.isdir(corpus):
        n = sum(1 for _ in glob.iglob(os.path.join(corpus, '**', '*.py'), recursive=True))
        _say('  %s （递归 *.py = %d 份）' % (corpus, n))
        if n != BASELINE['denominator']:
            _say('  注意：份数与基线分母 %d 不同 —— 语料变了的话，基线数字要重定，'
                 '别直接比。' % BASELINE['denominator'])
    else:
        _say('  %s  —— 看不到这个目录' % corpus)
        missing.append('语料根不存在（用 --corpus 指过去，或设 MPLTWEAK_CORPUS）')
    _say('== harness 工作区 ==')
    if os.path.isdir(os.path.join(work, 'harness')):
        lacks = [s for s in HARNESS_SCRIPTS
                 if not os.path.isfile(os.path.join(work, 'harness', s))]
        _say('  %s' % os.path.join(work, 'harness'))
        if lacks:
            _say('  缺脚本：%s' % ', '.join(lacks))
            missing.append('harness 脚本不全（缺 %s）' % ', '.join(lacks))
    else:
        _say('  %s  —— 看不到 harness/ 子目录' % work)
        missing.append('harness 工作区不存在（用 --work 指过去，或设 MPLTWEAK_CORPUS_WORK）')
    _say('== 解释器 ==')
    if os.path.isfile(python):
        _say('  %s' % python)
    else:
        _say('  %s  —— 这个路径上没有解释器' % python)
        missing.append('解释器路径不存在（用 --python 指定）')
    _say('')
    if missing:
        _say('结论：还不能跑 —— 缺 %d 项：' % len(missing))
        for m in missing:
            _say('  * ' + m)
        return 1
    _say('结论：齐了，可以跑 `--plan` 看命令，或 `--run --style inplace` 直接开跑。')
    return 0


def _load_shards(work, style, arm):
    """读某个 (arm, style) 的全部分片 -> {rel: row}；分片不全返回 None。"""
    rows = {}
    paths = sorted(glob.glob(os.path.join(work, 'out', arm, style, 'shard_*.jsonl')))
    if not paths:
        return None
    for p in paths:
        with io.open(p, encoding='utf-8') as f:
            for ln in f:
                ln = ln.strip()
                if not ln:
                    continue
                d = json.loads(ln)
                if d.get('__meta__'):
                    continue
                rows[d.get('rel')] = d
    return rows


def _dpi_warns(row):
    return [w for w in (row.get('warnings') or []) if 'dpi' in w]


def baseline(work, style):
    """重算 I2 指标并与 BASELINE 比对；rc=0 未漂移 / 1 漂移 / 2 读不到产物。"""
    A = _load_shards(work, style, 'A')
    B = _load_shards(work, style, 'B')
    if not A or not B:
        _err('读不到 %s 的分片产物（%s/out/{A,B}/%s/shard_*.jsonl）—— 先跑 `--run --style %s`，'
             '或用 --work 指到跑过的工作区。' % (style, work, style, style))
        return 2
    common = set(A) & set(B)
    denom = len(common)
    bad_a = sum(1 for r in common if A[r].get('reason') == 'bad_params')
    bad_b = sum(1 for r in common if B[r].get('reason') == 'bad_params')
    dpi_files = [r for r in common if _dpi_warns(B[r])]
    dpi_rows = sum(len(_dpi_warns(B[r])) for r in common)
    passed = [r for r in dpi_files
              if A[r].get('reason') == 'bad_params'
              and B[r].get('reason') in ('ok', 'best_effort')]
    pct = (100.0 * len(dpi_files) / denom) if denom else 0.0

    _say('== I2 回归指标（style=%s）==' % style)
    _say('  分母（两臂共同份数）      : %d' % denom)
    _say('  bad_params 份数             : A %d -> B %d（基线 %d -> %d）'
         % (bad_a, bad_b, BASELINE['bad_params_A'], BASELINE['bad_params_B']))
    _say('  dpi「判不出」放行 warning   : %d 份 / %d 条（%.3f%%；inplace 基线 %d 份 = 1.03%%）'
         % (len(dpi_files), dpi_rows, pct, BASELINE['dpi_warning_files']))
    _say('  其中「A 拒 -> B 放行」      : %d 份（%.3f%%；基线 %d 份 = 0.27%%）'
         % (len(passed), (100.0 * len(passed) / denom) if denom else 0.0,
            BASELINE['a_rejected_b_passed']))

    drift = []
    if denom != BASELINE['denominator']:
        drift.append('分母 %d != 基线 %d（语料变了：基线要重定，不是回归）'
                     % (denom, BASELINE['denominator']))
    if style == 'inplace' and bad_b != BASELINE['bad_params_B']:
        drift.append('bad_params %d != 基线 %d' % (bad_b, BASELINE['bad_params_B']))
    if bad_b > BASELINE['bad_params_B']:
        drift.append('bad_params 上升（%d > %d）—— 这是回归，先查拒写判据'
                     % (bad_b, BASELINE['bad_params_B']))
    limit = BASELINE['dpi_warning_files'] * (1 + DPI_GROWTH_LIMIT)
    if style == 'inplace' and len(dpi_files) > limit:
        drift.append('dpi 放行份数 %d 超过基线 %d 的 +%.0f%%'
                     % (len(dpi_files), BASELINE['dpi_warning_files'],
                        DPI_GROWTH_LIMIT * 100))
    _say('')
    if drift:
        _say('结论：**漂移**（%d 条）——' % len(drift))
        for d in drift:
            _say('  * ' + d)
        _say('  解释口径见 docs/dev.md「同类语料验证」一节：分母变了就先重定基线，别当回归。')
        return 1
    _say('结论：与基线一致（未漂移）。')
    return 0


def run(corpus, work, python, style, jobs, shards, dry_run):
    """真跑一波：launch -> aggregate -> probe_xtab -> verify_claims（缺件就停）。"""
    harness = os.path.join(work, 'harness')
    if not os.path.isdir(harness):
        _err('harness 工作区不存在：%s（用 --work 指过去，或设 MPLTWEAK_CORPUS_WORK）'
             % harness)
        return 2
    if not os.path.isdir(corpus):
        _err('语料根不存在：%s（用 --corpus 指过去，或设 MPLTWEAK_CORPUS）' % corpus)
        return 2
    env = dict(os.environ)
    env.update({'PYTHONUTF8': '1', 'PYTHONIOENCODING': 'utf-8',
                'MPLBACKEND': 'Agg', 'NSHARD': str(shards), 'JOBS': str(jobs),
                'STYLE': style, 'START': style})
    steps = [(['launch.py'], '两臂派发（%d 分片）' % shards),
             (['aggregate.py'], '聚合 SUMMARY_%s.md' % style),
             (['probe_xtab.py'], '反方向取证 REVERSE_%s.md' % style)]
    rc_all = 0
    for argv, what in steps:
        cmd = [python] + argv
        _say('[%s] %s' % (what, ' '.join(cmd)))
        if dry_run:
            _say('   （--dry-run：未执行）')
            continue
        r = subprocess.run(cmd, cwd=harness, env=env)
        if r.returncode != 0:
            _err('步骤失败（rc=%d）：%s —— 停在这里，后面的步骤不再执行'
                 % (r.returncode, ' '.join(argv)))
            rc_all = r.returncode
            break
    if rc_all == 0 and not dry_run:
        _say('')
        _say('下一步（逐条验收，全 PASS 才 rc=0）：')
        _say('  %s %s' % (python, os.path.join(harness, 'verify_claims.py')))
    return rc_all


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog='corpus_diff.py',
        description='语料差分跑的仓内入口：打印可复制命令 / 就绪自检 / 重算 I2 基线 / 真跑一波。',
        epilog='语料本体与 GIF/图片刻意不入仓（体积），路径用 --corpus/--work 或环境变量给。')
    ap.add_argument('--corpus', default=DEFAULT_CORPUS,
                    help='语料根（递归 *.py）；默认取环境变量 MPLTWEAK_CORPUS，'
                         '再退回仓库上一级的 corpus_union/')
    ap.add_argument('--work', default=DEFAULT_WORK,
                    help='harness 工作区（含 harness/ 与 out/）；默认取环境变量 '
                         'MPLTWEAK_CORPUS_WORK，再退回仓库上一级的 _corpus013/')
    ap.add_argument('--python', default=sys.executable, help='跑 harness 用的解释器（默认当前解释器）')
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument('--plan', action='store_true', help='打印可复制的固定命令（默认动作）')
    mode.add_argument('--check', action='store_true', help='就绪自检（rc=0 全齐 / 1 有缺 / 2 用法错）')
    mode.add_argument('--baseline', action='store_true', help='重算 I2 指标并与基线比对')
    mode.add_argument('--run', action='store_true', help='真跑一波（launch → 聚合 → 反证）')
    ap.add_argument('--style', choices=STYLES, default='inplace', help='写回路径（默认 inplace）')
    ap.add_argument('--jobs', type=int, default=4, help='并发作业数（默认 4）')
    ap.add_argument('--shards', type=int, default=32, help='分片数（默认 32）')
    ap.add_argument('--dry-run', action='store_true', help='只打印将要执行的命令')
    args = ap.parse_args(argv)

    if args.run:
        return run(args.corpus, args.work, args.python, args.style,
                   args.jobs, args.shards, args.dry_run)
    if args.check:
        return check(args.corpus, args.work, args.python)
    if args.baseline:
        return baseline(args.work, args.style)
    return plan(args.corpus, args.work, args.python)


if __name__ == '__main__':
    sys.exit(main())
