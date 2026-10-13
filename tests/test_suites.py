# -*- coding: utf-8 -*-
"""pytest 入口：把纯脚本套件包一层，换来 CI 报告 / 单测挑选 / IDE 集成。

**为什么保留"纯脚本"形态**：贡献者没装 pytest 也能直接
``python tests/test_check.py`` 跑（CONTRIBUTING 里就是这么写的），
而且脚本里 ``print('ALL PASS')`` 的输出对人最好读。这里只做包装，
不重写任何测试逻辑 —— 一个套件一个用例，失败时带上它的原始输出尾巴。

**为什么 pyproject 里要固定 python_files**：这些套件是**模块级脚本**
（多数没有 ``if __name__ == '__main__'`` 守卫）。若按 pytest 默认规则收集
``tests/test_*.py``，它们会在**收集期**被 import 而整个跑掉，失败还表现为
ImportError（看不出是哪条断言挂了），套件输出也会和 pytest 的报告混在一起。

**t24 补登记（2026-10-12）**：过去这里只有 7 个老套件 ⇒ 8 个新测试文件
（fuzz / property / sandbox / launch / matrix …）**根本没被执行**
——"pytest 全绿 ≠ 全测过"。现在把安全的都挂上；两个刻意**不挂**：

* ``test_real_window`` —— 它会**真开窗**，挂进 pytest 就把"无人值守跑测试"
  变成"弹窗抢焦点"（2026-10-12 事故的同类风险）。只能人工跑。
* ``test_corpus_invariants`` —— 需要仓库外的真实语料（corpus_union，只读），
  且默认档要**真跑 240 份陌生脚本**（分钟级重活，不是快速回归网）。
  替代覆盖：``test_figure_matrix``（12 图矩阵）+ ``test_property_writeback``
  （写回四铁律）在离屏侧覆盖同一批不变量；语料级证据由 t23 在交付窗口用
  ``--limit N`` 冒烟 + 全量单独跑，不进 pytest 路径。

**档位约定（t24 船长裁定）**：pytest 路径 = **降档回归档**；交付证据 = 默认档单独跑。
``test_figure_matrix`` 支持 ``MPLTWEAK_MATRIX_N``（默认 1000 = 12,000 次交付证据档，
≈426s）——这里传 **200**，让 pytest 总时长保持在可接受范围，同时该套件确实被执行。

用法：
    pytest -q                    # 全部
    pytest -q -k test_writeback  # 只跑写回引擎那套（最敏感的那套）
"""

import locale
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# (套件名, 该套件额外的环境变量)。env 一律在下面显式拼进子进程（含 MPLBACKEND=Agg），
# 不依赖外层环境 —— test_hygiene 的"级联闸门"棘轮会逐个调用点核对这一点。
SUITES = [
    # --- 老七套（0.1.9 起就在网里）---
    ('test_tweak', {}),
    ('test_events', {}),
    ('test_redo', {}),
    ('test_writeback', {}),
    ('test_agent_api', {}),
    ('test_check', {}),
    ('test_mcp', {}),
    # --- t24 挂入的新套件（都不开窗、零 GUI 对象）---
    ('test_fuzz_events', {}),                 # ② 随机手势 fuzz
    ('test_fuzz_params', {}),                 # ⑤ params fuzz
    ('test_property_writeback', {}),          # ④ 写回 property 不变量
    ('test_sandbox', {}),                     # 文件系统沙箱（自建 env、子进程串行）
    ('test_launch', {}),                      # 开窗前置链路（不建窗）
    # --- t39 挂入：写回验证「默认只跑一遍」的回归网（单遍 + savefig 快照 +
    #     fail-closed 回退；零窗口、秒级；见 tests/test_verify_onepass.py 文件头）---
    ('test_verify_onepass', {}),
    # --- t35 挂入：文档锚点/相对链接回归网（纯文本、零网络、秒级；见 tests/test_docs.py 文件头）---
    ('test_docs', {}),                        # 锚点/相对链接/相对图片/外链格式 + slug 自证 + 歧义标题棘轮
    # 12 图 × 100 次 = 1,200 次离屏矩阵：**降档回归档**（船长 t24 裁定：从 200 降到 100，
    # 让 pytest 总时长回到目标区间；交付证据档 1000 = 12,000 次见文件头，单独跑、不进 pytest）
    ('test_figure_matrix', {'MPLTWEAK_MATRIX_N': '100'}),
    # --- t15 挂入：记号纪律检查器（纯 AST + 合成样例自证，零 IO 重活、秒级；
    #     只报告不修；真仓库要求产品面零违规。见 tools/notation_discipline.py 文件头）---
    ('test_notation_discipline', {}),
]

# 刻意不挂进 pytest 的两个（理由见文件头 docstring）：
SUITES_EXCLUDED = {
    'test_real_window': '真开窗（会抢焦点/鼠标），只能由人工/船长放行时跑',
    'test_corpus_invariants': '需要仓库外语料 + 默认档真跑 240 份脚本（分钟级）',
}


@pytest.mark.parametrize('name,extra', SUITES, ids=[n for n, _ in SUITES])
def test_suite(name, extra):
    """跑一个套件：要求退出码 0 且确实打印了 ALL PASS。"""
    # 语言钉死 + 输出按候选编码解码：CI 上套件 stdout 是 UTF-8，
    # 本地 Windows 控制台是 GBK，硬解 UTF-8 会把失败信息变成乱码。
    env = dict(os.environ, MPLBACKEND='Agg', MPLTWEAK_LANG='zh', **extra)
    r = subprocess.run([sys.executable, os.path.join('tests', name + '.py')],
                       cwd=ROOT, capture_output=True, env=env)
    raw = r.stdout + r.stderr
    out = None
    for _enc in ('utf-8', locale.getpreferredencoding(False) or 'utf-8'):
        try:
            out = raw.decode(_enc)
            break
        except UnicodeDecodeError:
            continue
    if out is None:
        out = raw.decode('utf-8', 'replace')
    assert r.returncode == 0, (
        '%s 退出码 %s（期望 0）\n---- 输出尾巴 ----\n%s'
        % (name, r.returncode, out[-4000:]))
    assert 'ALL PASS' in out, (
        '%s 没有打印 ALL PASS（可能中途崩了）\n---- 输出尾巴 ----\n%s'
        % (name, out[-4000:]))
