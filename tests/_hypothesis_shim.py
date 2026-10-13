# -*- coding: utf-8 -*-
"""可选依赖 hypothesis 的兼容层（t31）。

**为什么需要它**：CI 的 step 6（`pytest report + hygiene ratchet`）只在 py3.12 跑，
命令是 `python -m pip install "pytest>=7.0"` + `python -m pytest -q` —— **它不装
hypothesis**（Install dependencies 那步只装 matplotlib / numpy / mcp，而且 `pip
install -e . --no-deps` 也不会带 dev 依赖）。而 `tests/test_suites.py` 的 SUITES 里
有 3 个 hypothesis 套件（test_fuzz_events / test_fuzz_params /
test_property_writeback）⇒ 它们**在 import 阶段**就炸：

    @st.composite
    ^^
    NameError: name 'st' is not defined

⇒ 3 个 parametrize 用例失败 ⇒ 整个 pytest 步骤 rc=1（本地实测
`3 failed, 54 passed, 5 skipped`，与 CI 症状一致；注意这**不是 py3.12 特有**，
在 3.11 上同样复现 —— 差别只在"step 6 只在 3.12 跑"）。

**这个 shim 做什么**：
* 装了 hypothesis → **原样透传真货**，行为一个字都不变；
* 没装 → 提供只服务于"**装饰函数 / 拼配置字典**"的最小替身，让套件照常 import，
  于是**所有不需要 hypothesis 的确定性断言照跑**（test_fuzz_events 的命名用例与
  teeth 探针、test_property_writeback 的确定性用例、test_fuzz_params 的
  mutation 与 e2e），而 hypothesis 那几条 property 由各套件自己的
  `_HAS_HYPOTHESIS` 分支跳过并**打印明确原因**（不静默、不计失败）。

替身的语义边界（故意做窄，避免"假装测过了"）：`_Strategy` 永远不会生成数据 ——
它只在 import 期被调用/相或/当字典值用；真正的 property 执行一律要求
`_HAS_HYPOTHESIS` 为真。所以"没装 hypothesis ⇒ 那几条 property 没跑"这件事，
在套件输出里是**明写**的，不存在假绿。
"""
try:                                        # 装了 → 真货，行为与以前完全一致
    import hypothesis
    from hypothesis import (HealthCheck, Phase, given, settings,   # noqa: F401
                            strategies as st)

    HAS_HYPOTHESIS = True
    VERSION = hypothesis.__version__
except ImportError:                         # 没装 → 最小替身（只服务装饰/配置）
    HAS_HYPOTHESIS = False
    VERSION = None

    class _Strategy(object):
        """占位策略：可调用（`st.lists(...)`、`st_mixed_op()`）、可相或、可当字典值。"""

        def __call__(self, *a, **k):
            return _Strategy()

        def __or__(self, other):
            return self

        def __ror__(self, other):
            return self

        def __getattr__(self, name):
            return _Strategy()

    class _Strategies(object):
        """`st` 的替身：任何属性都返回一个占位策略（可继续调用）。"""

        def __getattr__(self, name):
            return _Strategy()

    st = _Strategies()

    def given(*a, **k):                     # 装饰器：原样返回被装饰函数
        return lambda fn: fn

    def settings(*a, **k):                  # 同上（配置字典被忽略）
        return lambda fn: fn

    class HealthCheck(object):
        too_slow = 'too_slow'
        data_too_large = 'data_too_large'

    class Phase(object):
        explicit = 'explicit'
        generate = 'generate'
        shrink = 'shrink'


def skip_reason():
    """给各套件打印"为什么这几条没跑"的同一句话（口径一致，便于 CI 日志检索）。"""
    if HAS_HYPOTHESIS:
        return None
    return ('hypothesis 未安装 → 本套件的 hypothesis property 未执行；'
            '其余确定性断言照常执行（装上 hypothesis 即跑全部）')
