# -*- coding: utf-8 -*-
"""pytest 入口：把七个纯脚本套件包一层，换来 CI 报告 / 单测挑选 / IDE 集成。

**为什么保留"纯脚本"形态**：贡献者没装 pytest 也能直接
``python tests/test_check.py`` 跑（CONTRIBUTING 里就是这么写的），
而且脚本里 ``print('ALL PASS')`` 的输出对人最好读。这里只做包装，
不重写任何测试逻辑 —— 一个套件一个用例，失败时带上它的原始输出尾巴。

**为什么 pyproject 里要固定 python_files**：这七个套件是**模块级脚本**
（没有 ``if __name__ == '__main__'`` 守卫）。若按 pytest 默认规则收集
``tests/test_*.py``，它们会在**收集期**被 import 而整个跑掉，失败还表现为
ImportError（看不出是哪条断言挂了），套件输出也会和 pytest 的报告混在一起。

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
SUITES = ['test_tweak', 'test_events', 'test_redo', 'test_writeback',
          'test_agent_api', 'test_check', 'test_mcp']


@pytest.mark.parametrize('name', SUITES)
def test_suite(name):
    """跑一个套件：要求退出码 0 且确实打印了 ALL PASS。"""
    # 语言钉死 + 输出按候选编码解码：CI 上套件 stdout 是 UTF-8，
    # 本地 Windows 控制台是 GBK，硬解 UTF-8 会把失败信息变成乱码。
    env = dict(os.environ, MPLBACKEND='Agg', MPLTWEAK_LANG='zh')
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
