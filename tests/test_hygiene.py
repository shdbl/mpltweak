# -*- coding: utf-8 -*-
"""工程卫生棘轮：把两条"只写在文档里、没有任何测试钉住"的约束变成可执行的。

1. **宽兜底 ``except Exception`` 不许不登记地增长。**
   这类兜底在 GUI 回调与运行时探测里是**对的**（一个坏回调不该弄死整个调图会话），
   但堆到一百多处之后，读代码的人分不清"预期内降级"和"在掩盖 bug"。一次性收窄的
   回归风险太大，所以这里只做棘轮：每个文件的处数**不得超过基线**（降下来是好事，
   不拦），新模块一律从 0 起算。要新增一处，先在 ``BASELINE`` 里登记理由。
   完整清单与"可窄化"提示：``python tools/audit_exceptions.py --markdown``。

2. **消息表必须 GBK 可编码。**
   ``messages.py`` 顶部写明：中国 Windows 上 stdout 被捕获时编码是 cp936 且
   ``errors='strict'``，一个 U+2713（✓）就能把"写回成功"变成 rc=1 +
   UnicodeEncodeError——而且**崩在写回落盘之后**（用户看到失败，磁盘上其实已经改了）。
   这条约束原先只存在于注释里，加进来之后新消息写错符号会立刻被拦下。
"""

import ast
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, 'src', 'mpltweak')
if os.path.join(ROOT, 'src') not in sys.path:
    sys.path.insert(0, os.path.join(ROOT, 'src'))

# 基线：以 `python tools/audit_exceptions.py` 的输出为准（2026-10 实测，与下表合计一致：154 处）。
# 只允许降。要涨，必须在本表里改数字，并在 PR 里说明为什么无法窄化到具体异常。
# 已登记的例外：
#   * verify.py 15 → 19（2026-10-10）：新增 `_collect_texts` / `_ax_identity` 的
#     "尽力采集"兜底 —— 它们采的是**附加**信息（文字外框 / 轴身份），
#     采不到就该退化成空，绝不能因为某个 artist 取不到外框就让整个 check 失败。
#     每个点都写在 docstring / 行内注释里说明了这一点。
BASELINE = {
    'toolbox.py': 105,
    'launch.py': 16,
    'verify.py': 19,
    'writeback.py': 5,
    'messages.py': 5,
    'apply.py': 1,
    'layoutwarn.py': 1,
    # revert.py：2 处（拿写回锁失败、释放锁失败）—— 都是"锁不可用也不能崩"的兜底，
    # 与 apply.py 里同类处理一致；锁本身是尽力串行化，不是正确性依赖。
    'revert.py': 2,
    'params.py': 0,
    'check.py': 0,
    'describe.py': 0,
    'mcp_server.py': 0,
    'cli.py': 0,
    '__init__.py': 0,
    '__main__.py': 0,
}


def count_broad_excepts(path):
    """数"宽兜底"：``except:`` / ``except Exception`` / ``except BaseException``。"""
    with open(path, 'r', encoding='utf-8') as f:
        tree = ast.parse(f.read())
    n = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        if node.type is None:
            n += 1
        elif (isinstance(node.type, ast.Name)
              and node.type.id in ('Exception', 'BaseException')):
            n += 1
    return n


def test_all_modules_registered():
    """新增模块必须登记：新文件默认 0 处，出现兜底就得说明理由。"""
    files = sorted(f for f in os.listdir(SRC) if f.endswith('.py'))
    missing = [f for f in files if f not in BASELINE]
    assert not missing, (
        'src/mpltweak 下有未登记的新模块：%s —— 请在 tests/test_hygiene.py 的 '
        'BASELINE 里登记（通常应为 0：新模块不该一上来就用宽兜底）' % missing)


@pytest.mark.parametrize('name', sorted(BASELINE))
def test_except_exception_ratchet(name):
    path = os.path.join(SRC, name)
    if not os.path.exists(path):
        pytest.skip('%s 不存在了（已删除/改名 → 记得同步 BASELINE）' % name)
    n = count_broad_excepts(path)
    assert n <= BASELINE[name], (
        '%s 的宽兜底从 %d 涨到 %d。要么收窄到具体异常（ValueError / OSError / '
        'matplotlib 具体类型），要么在 BASELINE 里登记并说明理由。'
        '清单：python tools/audit_exceptions.py --markdown'
        % (name, BASELINE[name], n))
    if n < BASELINE[name]:
        # 降下来不拦，但提醒把基线一起降掉，免得棘轮越跑越松
        print('%s: 宽兜底 %d < 基线 %d —— 请顺手把 BASELINE 改成 %d'
              % (name, n, BASELINE[name], n))


def test_messages_are_gbk_encodable():
    """面向终端的所有消息模板都要能编成 GBK（t4-F2 那类崩在落盘之后的坑）。"""
    from mpltweak import messages as _msg
    bad = []
    for key, item in _msg.MESSAGES.items():
        for lang, tpl in item.items():
            try:
                tpl.encode('gbk')
            except UnicodeEncodeError as e:
                bad.append('%s[%s]: %s' % (key, lang, e))
    assert not bad, (
        '这些消息模板不能编成 GBK，会在 cp936 终端上把命令变成 rc=1 + '
        'UnicodeEncodeError：\n  ' + '\n  '.join(bad))


def test_message_lookup_never_raises():
    """``t()`` 的容错契约：key 缺失 / 参数缺失都返回文本，绝不抛异常。"""
    from mpltweak import messages as _msg
    old = _msg.current_lang()
    try:
        _msg.set_lang('zh')
        assert _msg.t('written_inplace', script='a.py').endswith('a.py')
        assert _msg.t('no_such_key') == 'no_such_key'
        # 少给参数时**原样返回模板**（不抛异常、也不丢成空串）—— 这条能失败：
        # 返回 None / 抛 KeyError / 返回空串都会被抓住。
        _tpl = _msg.MESSAGES['written_inplace'][_msg.current_lang()[:2]] \
            if _msg.current_lang()[:2] in _msg.MESSAGES['written_inplace'] \
            else _msg.MESSAGES['written_inplace']['zh']
        assert _msg.t('written_inplace') == _tpl
    finally:
        _msg.set_lang(old)
