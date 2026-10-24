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
    # --- t22 挂入：临时目录取法纪律（tools/corpus_filter_t7.py 的 --workroot 默认值
    #     跨平台契约：永远绝对、绝不回落成 '.'；含"修复前会红"的反向证据。
    #     子进程量环境、秒级。见 tests/test_tmpdir_discipline.py 文件头）---
    ('test_tmpdir_discipline', {}),
    # --- t74 挂入：**首次体验旅程测试**（从 wheel 装进干净 venv → 按文档逐条真跑 →
    #     零改动/幂等/写回不变量）。约 27~90 秒：本地建 wheel（离线 --no-build-isolation，
    #     失败自动回退到 pip 自建隔离环境）+ 建 venv + 装 wheel + 跑 CLI。
    #     零网络（安装用 --no-index）、不开窗（MPLBACKEND=Agg）。
    #     它抓的是单模块测试抓不到的接缝，尤其"装到的是 wheel 而不是仓库源码"
    #     （editable 安装曾两次造成假绿）。见 tests/test_journey_wheel.py 文件头 ---
    ('test_journey_wheel', {}),
    # --- t77 挂入：**语法化 AST 模糊测试**（自造 CFG 随机拼代码 —— 补的是"随机拼代码"
    #     这一层：现有 fuzz 只随参数/手势，property 也自述"脚本不随机生成"）。
    #     三条不变量：承诺违约=0 / 未匹配项安全降级 / 幂等。
    #     默认 N=9（每个对抗形态各 1 例，约 25 秒）；发布期大样本用
    #     `MPLTWEAK_ASTFUZZ_N=1000`（约 60 分钟，单独跑、不进 pytest）。
    #     零网络、不开窗。见 tests/test_ast_fuzz.py 文件头 + tools/ast_fuzz_gen.py ---
    ('test_ast_fuzz', {}),
    # --- t86 挂入：**CLI 参数 × 脚本 组合模糊**（命令×输入×参数文件×选项 的笛卡尔积，
    #     分母 7×10×6×14 = 5880，按 seed 分层抽样；可复现）。
    #     五条断言：无未处理 traceback / rc 属 {0,1,2,4} 且与文档口径一致 / 报错说人话
    #     （stdout 或 stderr 一路）/ --json 恒可解析 / en 无中文。
    #     默认 N=12（约 4 秒）；大样本用 `MPLTWEAK_CLIFUZZ_N`（N=240 约 62 秒；
    #     5880 全跑约 25 分钟）。零网络、不开窗。见 tests/test_cli_fuzz.py 文件头 ---
    ('test_cli_fuzz', {}),
    # --- t95 挂入：**消息键登记护栏**（`t('键')` 引用的键必须已登记 —— 否则
    #     `messages.t()` 的容错契约"缺键原样返回键"会把**键名回显给用户**；
    #     这是 t93 的真实根因）。三层：静态扫描（tools/msg_key_audit.py）+
    #     症状级（真跑 doctor/schema，断言输出里不出现任何已登记键名）+ 4 条 teeth。
    #     刻意把 `t()`（回显键名 = 违规）与 `t_ui()`（缺登记只是"en 下漏翻"、
    #     返回中文原文）分开报，否则一上来 60+ 条误报。秒级、零网络、不开窗。
    #     见 tests/test_msg_key_audit.py 文件头 ---
    ('test_msg_key_audit', {}),
    # --- D1 挂入（0.1.14，用户拍板 B）：布局引擎冲突**默认拒绝写回** + `--force` 绕过。
    #     四条路径各一组断言（冲突+几何 ⇒ 拒绝 / 字节不变 / rc=1 / 两条出路；
    #     冲突+非几何 ⇒ 照写；`--force` ⇒ 照写且明说跳过；无冲突 ⇒ 行为不变）+
    #     `check` 里该情形是 error 级。纯子进程 CLI、零窗口、秒级。
    #     见 tests/test_apply_layout_conflict.py 文件头 ---
    ('test_apply_layout_conflict', {}),
    # --- F3 挂入（0.1.14，§27.10 L2）：**GUI 离屏冒烟** —— `toolbox.py` 的窗口路径
    #     首次有自动化覆盖（开窗 → 拖一次 → 按一次键 → 关窗 → 查导出的参数文件与图一致）。
    #     用 `QT_QPA_PLATFORM=offscreen`（不碰屏幕、不抢焦点）；**缺 PyQt5 时显式 skip
    #     并打印原因**（这里刻意不带 `MPLTWEAK_REQUIRE_QT`：没有 Qt 的开发机也得能跑完
    #     这一套）。CI 侧只在 3.12 腿装 PyQt5 并**先断言 `import PyQt5` 成功**，
    #     所以"CI 里悄悄跳过"这条路是堵死的（见 `.github/workflows/tests.yml` 那段注释）。
    #     实测（longxia + PyQt5 5.15.11 + offscreen）：2 passed / 3.4s ---
    ('test_gui_smoke', {}),
    # --- S2 挂入（0.1.16，§31 A）：**两处静默点与误归因** —— 0.1.15 静默 except 审计
    #     留下的两条债，各给一条有牙的断言（注入故障，不占文件句柄）：
    #     ① `writeback._pending_vs_live` 删不掉旧 `_verify_state.json` ⇒ 必须进
    #        `files[].warnings`（唯一"静默但会影响结论"的一处；判定本身不变）；
    #     ② `apply` per-file `os.utime` 失败 ⇒ `warnings` + `attribution=mtime_sync_failed`
    #        + 顶层 `stage=finalize`，rc/ok 不变（过去被兜成 internal_error + tool_bug）。
    #     反向验证（把保护改回 0.1.15 原样 ⇒ 只有对应那几条红）见仓外
    #     `_team014/silent2/reverse2.py`。纯子进程 + 一次真写回验证重跑，约 7 秒。
    #     见 tests/test_silent2.py 文件头 ---
    ('test_silent2', {}),
    # --- S4 挂入（0.1.16，§31 A）：**形状矩阵 + "生成错代码"判据** —— 元组解包写回
    #     bug（`fig, (ax1, ax2) = plt.subplots(1, 2)`）藏了三个版本，根因是测试只问
    #     "成没成"、没问"为什么没成"。本套件把 10 种常见建轴写法（11 格，二维数组两种
    #     下标）各跑一遍「describe -> 改一个字段 -> apply --write -> 子进程重跑」，并把
    #     规则写死：**原脚本能跑 + 写回后跑不通 => 一律 FAIL（工具生成错代码）**，不许
    #     skip/降级；写回被拒只允许白名单四条"结构上明确表达不了"，且必须带明确
    #     reason/error_code，不在白名单里的拒绝同样判 FAIL。
    #     反向验证（仓外副本里把元组解包寻址改回 0.1.15 行为 => 本套件必红）见仓外
    #     `_team014/shapes_reverse.py`。11 格 × 4 个子进程，约 45 秒。
    #     见 tests/test_shapes.py 文件头 ---
    ('test_shapes', {}),
    # --- S8 挂入（0.1.17）：`mpltweak demo` 的**离屏**验收 —— 示例随包 / 复用不覆盖 /
    #     默认零侵入 / --write 落盘 + before.png+after.png / first_demo 真值翻转。
    #     反向验证（把"复用不覆盖"改成覆盖 ⇒ §119② 必红）见仓外 _team014/demo_impl/。
    #     纯子进程 + 一次真写回验证重跑，约 25 秒。见 tests/test_demo.py 文件头 ---
    ('test_demo', {}),
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
    # t109：CI 上这条断言的消息**会被截断**（`tools/ci_pytest_summary.py` 只把最后 8000
    # 字符发成注解，而这里原本只嵌 `out[-4000:]`）⇒ 失败时逐条 `FAIL |` 行常被截掉，
    # 排查只能靠猜（t109 实测：CI 只留下 5 条断言的**名字**，值全丢）。这里把失败行
    # 单独抽出来放消息里（短、必落在注解窗口内）⇒ 下一次 CI 红**自带**可读证据。
    _fails = [l.strip() for l in out.splitlines()
              if l.startswith('FAIL') or l.startswith('FAILED')]
    _ftxt = '\n'.join(_fails[-40:]) or '（输出里没有 FAIL 行 —— 可能是中途崩了）'
    assert r.returncode == 0, (
        '%s 退出码 %s（期望 0）\n---- 失败行 ----\n%s\n---- 输出尾巴 ----\n%s'
        % (name, r.returncode, _ftxt, out[-3000:]))
    assert 'ALL PASS' in out, (
        '%s 没有打印 ALL PASS（可能中途崩了）\n---- 失败行 ----\n%s\n'
        '---- 输出尾巴 ----\n%s' % (name, _ftxt, out[-3000:]))
