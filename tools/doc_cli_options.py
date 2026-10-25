# -*- coding: utf-8 -*-
"""文档 ↔ CLI 选项**双向**比对（t76）。

为什么做成**发布期工具**而不是常驻测试：docs 里合法地会出现**别的工具**的选项
（`pip install --no-deps`、`tools/corpus_filter_t7.py --workroot`、
`tools/check_doc_refs.py --with-maintenance` …）。放进 CI 只会不断假红；做成发布期
闸门 + 显式白名单，既拦得住真漂移（今晚的 `check --timeout` 就是"文档列了但 CLI 不认"），
又不会把无关工具的长相当成违规。

两个方向：
  ① **文档列了但 CLI 不认**（真漂移，退出码 1）
  ② **CLI 有但文档完全没提**（提示，不判失败 —— 有些选项是给脚本/内部用的）

用法：`python tools/doc_cli_options.py [--json]`
"""
import argparse
import io
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
CMDS = [(), ('apply',), ('describe',), ('check',), ('revert',), ('schema',),
        ('doctor',), ('launch',), ('mcp',)]
DOCS = ['skills/mpltweak/SKILL.md', 'README.md', 'README.en.md']
# 非本工具自己的选项（别的命令/工具的长相 / 有意写错的反例 / `--` 之后的用户脚本参数）。
# **每条必须带一行理由** —— t80（2026-10-14）把 9 个候选逐个分类后的结论写在这里；
# 加白名单**只能**靠"它确实不是本工具的选项"，不许为了让闸门变绿而放宽判据。
# 方向① 已分类白名单（t76：逐条看文档原句后判定为**非漂移**）

ALLOW = {
    # 安装/环境类（pip、python 自己的长相）
    '--no-deps': 'pip install 的选项（安装说明里出现）',
    '--upgrade': 'pip install 的选项',
    '--no-cache-dir': 'pip 的选项',
    '--user': 'pip 的选项',
    '--prefix': 'pip 的选项',
    '--target': 'pip 的选项',
    '--index-url': 'pip 的选项',
    '--force-reinstall': 'pip 的选项',
    '--editable': 'pip 的选项',
    '--version': 'pip / python 的选项',
    '--python': '`python tests/xxx.py` 里的解释器名（不是 apply 的 --python 选项）',
    # 仓库内别的工具的选项
    '--workroot': 'tools/corpus_filter_t7.py 的选项',
    '--with-maintenance': 'tools/check_doc_refs.py 的选项',
    # 0.1.18 新增：树身闸门 `tools/guard_tree_stable.py` 的选项。
    # 为什么要在**判断工具**里登记**工具自己的旗标**：这把尺子查的是
    # "文档里出现的 CLI 选项，产品是否接受"，而工具旗标本来就不是产品选项；
    # 不登记就会把 docs/dev.md 里"用 --record/--verify 包起来跑两臂"这类**正确写法**
    # 报成 suspicious（0.1.18 的 V1 基线就是这么红的：documented_not_accepted: ["--record"]）。
    '--record': 'tools/guard_tree_stable.py 的选项（记录树身指纹）',
    '--state': 'tools/guard_tree_stable.py 的选项（显式指定状态文件路径）',
    '--include-untracked': 'tools/guard_tree_stable.py 的选项（把未跟踪文件算进指纹）',
    '--wrap': 'tools/guard_tree_stable.py 的选项（包住一条命令，树变了就 rc=97）',
    '--early-ratio': 'tools/mutate_gate.py 的选项（提前退出判定的耗时阈值）',
    '--allow-broken-tree': 'tools/mutate_gate.py 的选项（基线语法坏时仍强行跑变异）',
    '--jobs': 'tools/mutate_gate.py 的选项（并行度）',
    '--only': 'tools/mutate_gate.py 的选项（只跑指定判据）',
    '--kind': 'tools/mutate_gate.py 的选项（只跑指定变异种类）',
    '--control': 'tests/test_corpus_identity.py 的选项（正对照：用指定树复跑）',
    '--limit': 'tests/test_corpus_identity.py 的选项（抽样上限）',
    '--backend': 'tests/test_real_window.py 的选项（真窗口测试脚本；它出现在含 mpltweak URL 的那一行）',
    '--dry-list': 'tests/test_real_window.py 的选项（免弹窗列场景/断言）',
    '--selftest': 'tests/test_real_window.py 的选项（断言机器自检）',
    '--scenario': 'tests/test_real_window.py 的选项（场景名）',
    '--resume': 'tests/test_corpus_invariants.py 的选项（语料跑批断点续跑）',
    # 用户脚本自己的参数（写在 `--` 之后，工具原样透传）
    '--data': '用户脚本自己的参数：`mpltweak apply x.py --write -- --data x.npz`',
    '--level': '用户脚本自己的参数：`… -- --data x.npz --level 3`（与 --data 同族）',
    # 有意示例 / 反面例子
    '--bogus': '文档里**故意写错的例子**（演示"顶层未知参数 ⇒ rc=2"）',
    '--tweak': '文档里作为**反面例子**：工具明令禁止用户脚本出现 `--tweak` 分支',
    # argparse 公共
    '--help': 'argparse 公共选项',
    '-h': 'argparse 公共选项',
    '--json': '工具自己的选项（多个子命令都有；不参与①的逐命令比对）',
}
OPT_RE = re.compile(r'(?<![\w-])--[a-z][a-z0-9-]{1,30}')


def cli_options():
    out = {}
    for c in CMDS:
        r = subprocess.run([PY, '-m', 'mpltweak'] + list(c) + ['--help'],
                           cwd=ROOT, capture_output=True, timeout=300,
                           env=dict(os.environ, MPLBACKEND='Agg',
                                    PYTHONIOENCODING='utf-8'))
        txt = (r.stdout + r.stderr).decode('utf-8', 'replace')
        got = set(OPT_RE.findall(txt))
        if got:
            out[' '.join(c) or 'mpltweak'] = got
    return out


def doc_options():
    got = {}
    for rel in DOCS + [os.path.join('docs', f) for f in
                       (os.listdir(os.path.join(ROOT, 'docs'))
                        if os.path.isdir(os.path.join(ROOT, 'docs')) else [])
                       if f.endswith('.md')]:
        p = os.path.join(ROOT, rel)
        if not os.path.isfile(p):
            continue
        txt = io.open(p, encoding='utf-8', errors='replace').read()
        # 只认**与 mpltweak 出现在同一行**的选项：docs 里合法地会写别的工具/用户脚本
        # 自己的选项（`pip install --no-deps`、`mpltweak apply x.py -- --data y`、
        # `--bogus` 这种"故意写错的例子"），那些不该算"本工具的文档选项"。
        txt = '\n'.join(l for l in txt.split('\n') if 'mpltweak' in l)
        for o in OPT_RE.findall(txt):
            got.setdefault(o, set()).add(rel)
    return got


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--json', action='store_true')
    args = ap.parse_args(argv)
    cli = cli_options()
    doc = doc_options()
    accepted = set()
    for s in cli.values():
        accepted |= s
    doc_only = sorted(o for o in doc if o not in accepted and o not in ALLOW)
    cli_only = sorted(o for o in accepted
                      if o not in doc and o not in ALLOW)
    if args.json:
        print(json.dumps({'documented_not_accepted': doc_only,
                          'accepted_not_documented': cli_only},
                         ensure_ascii=False, indent=2))
    else:
        print('① 文档列了但 CLI 不认（真漂移）：%d 处' % len(doc_only))
        for o in doc_only:
            print('   %-22s 见 %s' % (o, ', '.join(sorted(doc[o]))[:70]))
        print('② CLI 有但文档完全没提（提示）：%d 处' % len(cli_only))
        print('   %s' % (', '.join(cli_only) or '（空）'))
        print('结论：%s' % ('OK' if not doc_only else 'FAIL（①非空）'))
    return 1 if doc_only else 0


if __name__ == '__main__':
    sys.exit(main())
