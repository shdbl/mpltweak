# -*- coding: utf-8 -*-
"""布局引擎冲突检测 —— ``constrained_layout`` / ``autolayout`` 会覆盖写回的位置。

**为什么需要这个模块**（外部审阅的第 4 条，实测确认：全仓库原先零处理）：

- 调整块（``--style block``）落到代码里的是 ``_ax.set_position(...)``；
- ``constrained_layout`` / ``figure.autolayout`` 这类**布局引擎会在每次绘制时**重新
  计算轴位置 → 手动位置被覆盖。语义验证能抓到不一致并回滚/换锚点，但用户看到的只是
  一句"验证不一致"，而不是"你把 constrained_layout 关掉就好了"——提示没有落到**病因**上；
- ``tight_layout()`` 是**一次性**的（调用即生效，不在 draw 时重算），mpltweak 正是拿它
  当插入锚点（块插在它之后、位置反过来覆盖它），所以**不算冲突**，只在 README 里说明。

**为什么用静态 AST 扫描而不是运行时探测**：

1. ``launch`` / ``apply`` / ``check`` 三处都要用，静态扫描不跑脚本、不依赖图形后端，成本最低；
2. 报出来的信息必须能指到**行号**，用户才知道去哪儿改；
3. 已知边界：动态构造的布局参数（``**kwargs``、变量、拼出来的 rcParams 键）扫不到 ——
   这条边界写进 README 的"已知限制"，不假装全能。
"""

from __future__ import annotations

import ast
from typing import Any, Dict, List

# 布局引擎在每次绘制时重算位置的两个来源：
#   1) rcParams['figure.constrained_layout.use'] / fig.set_constrained_layout(...)
#   2) rcParams['figure.autolayout']（等价于每次绘制都跑一次 tight_layout）
CONSTRAINED_KEYS = ('figure.constrained_layout.use',)
AUTOLAYOUT_KEYS = ('figure.autolayout',)
# layout= 参数里这两个都交给"约束布局"引擎（compressed 是它的紧凑变体）
CONSTRAINED_VALUES = ('constrained', 'compressed')

KIND_CONSTRAINED = 'constrained'
KIND_AUTOLAYOUT = 'autolayout'


def _const_str(node: Any) -> Any:
    """取字符串字面量的值（不是字面量则返回 None）。"""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _is_true(node: Any) -> bool:
    """字面量真值判断：True / 1 / 非空字符串都算开（变量一律不算，避免瞎猜）。"""
    if isinstance(node, ast.Constant):
        return bool(node.value) and node.value is not False
    return False


def _sub_key(node: Any) -> Any:
    """取下标赋值的键：``x.rcParams['figure.autolayout']`` → 那个字符串键。"""
    if not isinstance(node, ast.Subscript):
        return None
    sl = getattr(node, 'slice', None)
    # Python 3.8 用 ast.Index 包一层；3.9+ 直接是表达式。按类名判断，
    # 避免在 3.12（ast.Index 已移除）上被 import 期属性访问坑到。
    if sl is not None and sl.__class__.__name__ == 'Index':
        sl = sl.value
    return _const_str(sl)


def _dict_items(node: Any):
    """从字面量 dict 里产出 (键, 值节点) 对（用于 rcParams.update({...}) / rc_context）。"""
    if not isinstance(node, ast.Dict):
        return
    for k, v in zip(node.keys, node.values):
        key = _const_str(k)
        if key is not None:
            yield key, v


def scan_source(src: str) -> List[Dict[str, Any]]:
    """扫源码字符串，返回冲突列表 ``[{kind, line, how}, ...]``（按行号排序、已去重）。

    ``kind``：``constrained``（约束布局）/ ``autolayout``（rcParams 自动布局）。
    ``how``：触发写法的简短片段，直接拼进给用户看的提示里。
    ``line``：行号（从 1 开始），用户据此定位。
    """
    try:
        tree = ast.parse(src)
    except (SyntaxError, ValueError):
        # 语法错不该由这里报（写回/采集路径有更准确的报错），静默不抢戏
        return []

    found = {}

    def add(kind: str, line: int, how: str):
        found.setdefault((kind, int(line or 0), how), None)

    def rcparam_hit(key: str, value: Any, line: int):
        if key in CONSTRAINED_KEYS and _is_true(value):
            add(KIND_CONSTRAINED, line, "rcParams['%s']=True" % key)
        elif key in AUTOLAYOUT_KEYS and _is_true(value):
            add(KIND_AUTOLAYOUT, line, "rcParams['%s']=True" % key)

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            # plt.subplots(2, 2, layout='constrained') / fig, ax = plt.subplots(
            #     constrained_layout=True) / plt.rc('figure', autolayout=True)
            #
            # 关键词规则**只认 `xxx.yyy(...)` 形式**（callee 是 Attribute）：
            # `constrained_layout=True` 是个很普通的关键词名，用户自己的
            # `save_plot(constrained_layout=True)` 那种 helper 不该被误报成
            # "你的布局引擎会覆盖位置"（独立审阅 C5）。
            # 代价：`mylib.save_plot(constrained_layout=True)` 仍会被报 ——
            # 那是"保守启发式"的已知边界，写在 docstring 里。
            _attr_callee = isinstance(node.func, ast.Attribute)
            for kw in node.keywords:
                if not _attr_callee:
                    continue
                if kw.arg == 'constrained_layout' and _is_true(kw.value):
                    add(KIND_CONSTRAINED, kw.value.lineno,
                        'constrained_layout=True')
                elif kw.arg == 'autolayout' and _is_true(kw.value):
                    add(KIND_AUTOLAYOUT, kw.value.lineno, 'autolayout=True')
                elif kw.arg == 'layout':
                    v = _const_str(kw.value)
                    if v in CONSTRAINED_VALUES:
                        add(KIND_CONSTRAINED, kw.value.lineno,
                            "layout='%s'" % v)
            fn = node.func
            if isinstance(fn, ast.Attribute):
                if (fn.attr == 'set_constrained_layout' and node.args
                        and _is_true(node.args[0])):
                    add(KIND_CONSTRAINED, node.lineno,
                        '.set_constrained_layout(True)')
                if (fn.attr == 'set_layout_engine' and node.args):
                    v = _const_str(node.args[0])
                    if v in CONSTRAINED_VALUES:
                        add(KIND_CONSTRAINED, node.lineno,
                            ".set_layout_engine('%s')" % v)
            # plt.rcParams.update({...}) / matplotlib.rc_context({...})
            for arg in list(node.args) + [kw.value for kw in node.keywords]:
                for key, val in _dict_items(arg):
                    rcparam_hit(key, val, getattr(val, 'lineno', 0))
        elif isinstance(node, ast.Assign):
            for tgt in node.targets:
                key = _sub_key(tgt)
                if key is not None:
                    rcparam_hit(key, node.value, node.lineno)

    return [{'kind': k[0], 'line': k[1], 'how': k[2]}
            for k in sorted(found)]


def scan(script: str) -> List[Dict[str, Any]]:
    """扫脚本文件的布局引擎冲突；**任何读失败都返回空表**。

    检测本身绝不能让命令挂掉：读不出来（编码怪、权限、文件刚被删）就当作"没发现"，
    真正的失败报错留给原有路径。
    """
    try:
        from . import params
        src, _enc = params.read_text(script)
    except Exception:                             # noqa: BLE001 - 兜底见 docstring
        try:
            with open(script, 'r', encoding='utf-8', errors='replace') as f:
                src = f.read()
        except OSError:
            return []
    return scan_source(src)


def messages_for(findings: List[Dict[str, Any]]) -> List[str]:
    """把冲突点转成可以直接打印的人话（每条冲突一行 + 末尾一条修复建议）。"""
    from . import messages as _msg
    if not findings:
        return []
    out = []
    seen = set()
    for f in findings:
        key = (f.get('kind'), f.get('line'), f.get('how'))
        if key in seen:
            continue
        seen.add(key)
        out.append(_msg.t('layout_engine_conflict', line=f.get('line'),
                          how=f.get('how')))
    out.append(_msg.t('layout_engine_hint'))
    return out
