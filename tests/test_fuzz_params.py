# -*- coding: utf-8 -*-
"""mpltweak params.validate_errors property-based fuzz（hypothesis）。

验证铁律（params.py:535-667 的语义 + apply.py:519-527 / 766-779 的落地行为）：
    **validate_errors 非空 ⟺ apply 会拒写（rc=2 + 源码字节不变）**。

五个层次：
  A. 任意 JSON 结构（含非 dict、NaN/±Inf、深层嵌套）调用 validate_errors /
     validate 永不抛异常（fail-closed 的基本安全网）。
  B. validate 与 validate_errors 的关系：validate = validate_errors（硬错误）的
     前缀 + 只追加已知**软问题** —— 软/硬严格区分，软问题绝不混进硬错误
     （params.validate 只追加两类软问题：version 高于当前、fontsize==0）。
  C. 合法参数生成器：随机生成"完全合法"的参数 dict → validate_errors 必为空
     （合法不误伤；含 fontsize==0 这类只提示不拒写的软问题）。
  D. 定向畸形突变：每个字段族（version / figsize / pos / index / fontsize /
     clim / xlim / spines / lines / legend / grid / xscale / yscale / axes
     类型）各给一个明确非法的取值 → validate_errors 必非空且报出该字段
     （畸形必拒）。
  E. 端到端抽查：畸形参数 → ``apply --write --json --no-verify`` → rc=2 且源码
     字节不变；合法参数 → 不因参数层误伤（rc≠2）。

软问题 / 硬错误的口径（任务明示 + params.py 读码）：
  * validate_errors = **硬错误**（error 级）：语义无法安全落地 → 必须拒写（rc=2）。
  * validate = 硬错误 + **软问题**（只提示不拒写）：version 高于当前、fontsize==0。
    注意现实：version 高于当前会**同时**命中硬错误（_version_problem 只要 != 当前
    就拒）与软问题（"高于当前，按尽力读处理"）—— 断言用"前缀 + 已知软问题"覆盖。
  * fontsize==0 不是硬错误：matplotlib 允许（只是文字看不见），只提示、不拒写。
  * pos 的 left/bottom 为负但**仍有可见部分**不是错误（常见做法）；只有整块
    完全在画布外（left>=1 / bottom>=1 / left+w<=0 / bottom+h<=0）才拒。
  * 顶层非 dict 的 JSON（数组/字符串/数字/null）在 apply 的 load() 里归
    bad_params 硬错误（0.1.10 修：不再崩成 internal_error）→ rc=2 + 字节不变
    + reason=bad_params，与"缺 version / 结构完全不对"同一档。
"""
import json
import os
import shutil
import subprocess
import sys

try:
    # 可选依赖：装了 hypothesis 就是**真货**；没装则由 `_hypothesis_shim` 提供只服务
    # "装饰函数/拼配置"的最小替身 —— 于是本套件在"CI step 6 不装 hypothesis"时也能
    # import 成功，**mutation 与 e2e 两部分照常全跑**，只有 A/B/C 三条 property 被
    # `_HAS_HYPOTHESIS` 挡掉并打印明确原因（不静默、不算失败）。
    from _hypothesis_shim import (HAS_HYPOTHESIS as _HAS_HYPOTHESIS, st,
                                  given, settings, HealthCheck)
except ImportError:                                          # noqa: BLE001
    _HAS_HYPOTHESIS = False

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 界面语言钉死（与其它套件一致）：断言的是中文文案，别让宿主 locale 决定结论。
os.environ['MPLTWEAK_LANG'] = 'zh'
sys.path.insert(0, os.path.join(ROOT, 'src'))
from mpltweak import params as _params              # noqa: E402

# GBK 控制台兜底（同 test_check.py）：CI 是 UTF-8，本地 cp936 上一次
# UnicodeEncodeError 就把整套从 ALL PASS 打成 rc=1 的假红。
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(errors='replace')
    except (AttributeError, ValueError, OSError):
        pass


def _gbk_safe(s):
    s = str(s)
    enc = getattr(sys.stdout, 'encoding', None) or 'utf-8'
    try:
        return s.encode(enc, 'replace').decode(enc, 'replace')
    except (LookupError, UnicodeDecodeError):
        return s.encode('ascii', 'backslashreplace').decode('ascii')


def _decode_out(raw):
    """子进程输出解码：先 utf-8，再系统 ANSI（GBK），最后 replace。"""
    raw = raw or b''
    for enc in ('utf-8', 'gbk', 'gb18030'):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode('utf-8', 'replace')


# 沙箱：测试只能写 workspace 内 → 临时区放测试文件旁（带 pid，避免并行互删）。
_TMPROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        '.tmp_fuzz_%d' % os.getpid())
os.makedirs(_TMPROOT, exist_ok=True)
# %TEMP% 写入被沙箱拦（环境提示）：子进程跑 apply 前把 TEMP/TMP 指到可写目录。
os.environ['TEMP'] = _TMPROOT
os.environ['TMP'] = _TMPROOT
os.environ['TMPDIR'] = _TMPROOT

_fails = []


def check(name, cond, detail=''):
    print('%s %s%s' % ('  OK  ' if cond else ' FAIL ',
                       _gbk_safe(name),
                       '' if cond else '   ' + _gbk_safe(detail)))
    if not cond:
        _fails.append(name)


# ---------------------------------------------------------------------------
# hypothesis 设置：纯函数毫秒级 → deadline=None；不落 ~/.hypothesis 数据库
# （套件应自包含、不依赖仓库外状态）；结构不大 → 只需压掉数据过大的健康检查。
# ---------------------------------------------------------------------------
_SET = dict(
    max_examples=200,
    deadline=None,
    database=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)


# ---------------------------------------------------------------------------
# 任意 JSON 结构策略（A/B 层的输入）：标量里故意带 NaN/±Inf（_is_num 必须挡），
# 递归生成字典/数组深层嵌套；keys 用短文本。
# ---------------------------------------------------------------------------
_any_scalar = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(min_value=-10 ** 9, max_value=10 ** 9),
    # hypothesis 不允许 allow_nan=True 与 min/max 同时出现 → 拆成两个：
    # 有界有限浮点 + 无界（可 NaN/±Inf，_is_num 必须把它们挡下来）。
    st.floats(min_value=-1e6, max_value=1e6),
    st.floats(allow_nan=True, allow_infinity=True),
    st.text(max_size=16),
)
_json_value = st.recursive(
    _any_scalar,
    lambda c: st.lists(c, max_size=5)
    | st.dictionaries(st.text(max_size=8), c, max_size=5),
    max_leaves=40,
)


# ---------------------------------------------------------------------------
# A. 任意输入永不抛异常（fail-closed 安全网）
# ---------------------------------------------------------------------------
@given(_json_value)
@settings(**_SET)
def test_any_json_never_crashes(d):
    _params.validate_errors(d)
    _params.validate(d)


# ---------------------------------------------------------------------------
# B. validate = validate_errors 前缀 + 只追加已知软问题（软/硬严格区分）
# ---------------------------------------------------------------------------
def _is_soft_problem(p):
    """已知的软问题（params.validate 在硬错误之外追加的全部类型）：
    1) version 高于当前 → "按尽力读处理"；
    2) 轴字号 == 0 → "这处文字会看不见"；
    3) 图例字号 == 0 → "图例文字会看不见"。
    硬错误文案不含这三段（读码核对），命中即"软问题"，不得拿来拒写。"""
    return ('高于当前' in p or '这处文字会看不见' in p
            or '图例文字会看不见' in p)


@given(_json_value)
@settings(**_SET)
def test_validate_is_errors_prefix_plus_soft(d):
    hard = _params.validate_errors(d)
    allp = _params.validate(d)
    # validate() 内部第一行就是 validate_errors(data)，软问题**追加**在其后 →
    # allp 必须是 hard 的前缀扩展（先断言前缀，再断言多余的全是已知软问题）。
    assert allp[:len(hard)] == hard, (repr(d), hard, allp)
    for p in allp[len(hard):]:
        assert _is_soft_problem(p), (repr(d), p)


# ---------------------------------------------------------------------------
# C. 合法参数生成器 → validate_errors 必为空（合法不误伤）
# ---------------------------------------------------------------------------
@st.composite
def legal_params(draw):
    n_axes = draw(st.integers(min_value=0, max_value=4))
    axes = []
    for _ in range(n_axes):
        axis = {'index': draw(st.integers(min_value=0, max_value=10))}
        # pos：w/h > 0、left<1、bottom<1、left+w>0、bottom+h>0（负但可见允许）。
        # 注意边界：`_pos_problem` 对 left+w **<= 0**（右缘正好压在 x=0）也拒
        # （fuzz 实测 [-0.25, 0.0, 0.25, 0.5] 命中）→ 生成器必须保证严格 > 0。
        # 取 left/bottom ∈ [-0.3, 0.9] 且 w/h ∈ [0.4, 0.95] ⇒ 和 ≥ 0.1 > 0；
        # left 最大 0.9 < 1 —— 全在"可见"合法域内。
        if draw(st.booleans()):
            axis['pos'] = [
                draw(st.floats(min_value=-0.3, max_value=0.9)),
                draw(st.floats(min_value=-0.3, max_value=0.9)),
                draw(st.floats(min_value=0.4, max_value=0.95)),
                draw(st.floats(min_value=0.4, max_value=0.95)),
            ]
        # 字号：>= 0（含 0 —— 0 是软问题、不是硬错误，正是要验证的点）。
        for f in ('title_fontsize', 'label_fontsize', 'label_fontsize_y',
                  'tick_fontsize', 'tick_fontsize_y'):
            if draw(st.booleans()):
                axis[f] = draw(st.one_of(
                    st.integers(min_value=0, max_value=40),
                    st.floats(min_value=0.0, max_value=40.0,
                              allow_nan=False, allow_infinity=False)))
        if draw(st.booleans()):
            axis['clim'] = draw(st.lists(
                st.floats(min_value=-1e4, max_value=1e4, allow_nan=False,
                          allow_infinity=False), min_size=2, max_size=2))
        if draw(st.booleans()):
            axis['xlim'] = draw(st.lists(
                st.floats(min_value=-1e4, max_value=1e4, allow_nan=False,
                          allow_infinity=False), min_size=2, max_size=2))
        if draw(st.booleans()):
            axis['ylim'] = draw(st.lists(
                st.floats(min_value=-1e4, max_value=1e4, allow_nan=False,
                          allow_infinity=False), min_size=2, max_size=2))
        if draw(st.booleans()):
            axis['grid'] = draw(st.booleans())
        if draw(st.booleans()):
            axis['xscale'] = draw(st.sampled_from(
                [x for x in _params.SCALE_ENUM if x is not None]))
        if draw(st.booleans()):
            axis['yscale'] = draw(st.sampled_from(
                [x for x in _params.SCALE_ENUM if x is not None]))
        if draw(st.booleans()):
            axis['spines'] = draw(st.dictionaries(
                st.text(max_size=8), st.booleans(), max_size=4))
        if draw(st.booleans()):
            axis['lines'] = [
                {'index': draw(st.integers(min_value=0, max_value=5)),
                 'linewidth': draw(st.one_of(
                     st.none(),
                     st.floats(min_value=0.0, max_value=20.0,
                               allow_nan=False, allow_infinity=False))),
                 'color': draw(st.one_of(st.none(), st.text(max_size=12)))}
                for _ in range(draw(st.integers(min_value=0, max_value=3)))
            ]
        if draw(st.booleans()):
            axis['legend'] = draw(st.one_of(
                st.none(),
                st.fixed_dictionaries({
                    'loc': st.one_of(st.none(), st.text(max_size=16)),
                    'anchor': st.one_of(
                        st.none(),
                        st.lists(st.floats(min_value=-1e4, max_value=1e4,
                                           allow_nan=False,
                                           allow_infinity=False),
                                 min_size=2, max_size=2)),
                    'fontsize': st.one_of(
                        st.none(),
                        st.floats(min_value=0.0, max_value=40.0,
                                  allow_nan=False, allow_infinity=False)),
                }),
            ))
        axes.append(axis)
    d = {'version': 4, 'script': 'target.py', 'axes': axes}
    if draw(st.booleans()):
        d['figsize_in'] = draw(st.lists(
            st.floats(min_value=0.1, max_value=60.0, allow_nan=False,
                      allow_infinity=False), min_size=2, max_size=2))
    if draw(st.booleans()):
        d['figsize_px'] = draw(st.lists(
            st.floats(min_value=1.0, max_value=4000.0, allow_nan=False,
                      allow_infinity=False), min_size=2, max_size=2))
    if draw(st.booleans()):
        d['fig_index'] = draw(st.integers(min_value=0, max_value=10))
    if draw(st.booleans()):
        d['n_figs'] = draw(st.integers(min_value=0, max_value=10))
    return d


@given(legal_params())
@settings(**_SET)
def test_legal_params_never_hard_rejected(d):
    # 合法参数：硬错误必空；normalize（apply 的 load 路径）后同样必空。
    assert _params.validate_errors(d) == [], repr(d)
    assert _params.validate_errors(_params.normalize(d)) == [], repr(d)
    # validate 顶多带软问题（fontsize==0 / version 高于当前），绝无硬错误混入。
    assert all(_is_soft_problem(p) for p in _params.validate(d)), repr(d)


# ---------------------------------------------------------------------------
# D. 定向畸形突变 → validate_errors 必非空且报出该字段（畸形必拒）
#    在"完全合法"的基线上逐字段埋错，命中任务点名的每个字段族。
# ---------------------------------------------------------------------------
def _base():
    return {'version': 4, 'script': 'target.py',
            'figsize_in': [12.0, 4.8], 'figsize_px': [1200, 480],
            'axes': [{'index': 0, 'pos': [0.05, 0.1, 0.42, 0.78],
                      'title_fontsize': 12, 'label_fontsize': 10,
                      'label_fontsize_y': 10, 'tick_fontsize': 9,
                      'tick_fontsize_y': 9, 'grid': True,
                      'clim': [0.0, 1.0], 'xlim': [0.0, 1.0],
                      'ylim': [0.0, 1.0],
                      'spines': {'top': True, 'right': True},
                      'lines': [{'index': 0, 'linewidth': 2.0,
                                 'color': '#d62728'}],
                      'legend': {'loc': 'upper right',
                                 'anchor': [0.5, 0.5], 'fontsize': 10},
                      'xscale': 'linear', 'yscale': 'linear'}]}


def _mutate(d, path, value):
    """按 'a.b.c' 路径写坏值（列表用 'axes.0.pos' 这类下标）。"""
    parts = path.split('.')
    cur = d
    for i, p in enumerate(parts[:-1]):
        if p.isdigit():
            cur = cur[int(p)]
        else:
            cur = cur[p]
    last = parts[-1]
    if last.isdigit():
        cur[int(last)] = value
    else:
        cur[last] = value
    return d


# (label, 路径, 坏值, 期望子串)
_MUTATIONS = [
    ('缺 version', 'version', None, 'version'),
    ('version 非整数', 'version', '4', 'version'),
    ('version 布尔', 'version', True, 'version'),
    ('figsize_in 长度 3', 'figsize_in', [1.0, 2.0, 3.0], 'figsize_in 必须是'),
    ('figsize_in 零', 'figsize_in.1', 0.0, '必须 > 0'),
    ('figsize_px 负', 'figsize_px.0', -100, '必须 > 0'),
    ('figsize_in 含 NaN', 'figsize_in.0', float('nan'), '非数值'),
    ('pos 长度 3', 'axes.0.pos', [0.05, 0.1, 0.42], 'pos 必须是 4'),
    ('pos width 零', 'axes.0.pos.2', 0.0, 'width'),
    ('pos width 负', 'axes.0.pos.2', -0.2, 'width'),
    ('pos height 负', 'axes.0.pos.3', -0.1, 'height'),
    ('pos 完全出画布(left>=1)', 'axes.0.pos.0', 1.05, '画布外'),
    # left+w<=0 需要两个数一起埋错（单改 left 只会让它"负但可见"——合法）：
    # left=-1.0 + 基线的 w=0.42 ⇒ left+w=-0.58 <= 0 ⇒ 整块出画布。
    ('pos left+w<=0', 'axes.0.pos.0', -1.0, '画布外'),
    ('pos bottom>=1', 'axes.0.pos.1', 1.05, '画布外'),
    ('pos 含非数值', 'axes.0.pos.0', 'x', '非数值'),
    ('index 负', 'axes.0.index', -1, '为负'),
    ('index 非整数', 'axes.0.index', '0', '必须是整数'),
    ('index 布尔', 'axes.0.index', True, '必须是整数'),
    ('index null', 'axes.0.index', None, '必须是整数'),
    ('title_fontsize 负', 'axes.0.title_fontsize', -3, '不能为负'),
    ('label_fontsize_y 负', 'axes.0.label_fontsize_y', -1, '不能为负'),
    ('tick_fontsize 非数字', 'axes.0.tick_fontsize', 'big', '必须是数字或 null'),
    ('axes 非数组', 'axes', {'index': 0}, 'axes 必须是数组'),
    ('axes 元素非对象', 'axes.0', 'nope', '不是对象'),
    ('clim 长度 1', 'axes.0.clim', [0.0], 'clim 必须是 2 个数字'),
    ('clim 长度 3', 'axes.0.clim', [0.0, 1.0, 2.0], 'clim 必须是 2 个数字'),
    ('clim 含字符串', 'axes.0.clim.0', 'a', 'clim 含非数值'),
    ('xlim 长度 3', 'axes.0.xlim', [0.0, 1.0, 2.0], 'xlim 必须是 2 个数字'),
    ('ylim 非数组', 'axes.0.ylim', 5, 'ylim 必须是 2 个数字'),
    ('spines 非对象', 'axes.0.spines', [True, False], 'spines 必须是对象'),
    ('lines 非数组', 'axes.0.lines', {'a': 1}, 'lines 必须是数组'),
    ('lines 元素非对象', 'axes.0.lines.0', 'nope', 'lines[0] 不是对象'),
    ('linewidth 非数字', 'axes.0.lines.0.linewidth', 'thick',
     'linewidth 必须是数字或 null'),
    ('legend 非对象', 'axes.0.legend', 'top', 'legend 必须是对象或 null'),
    ('legend.fontsize 负', 'axes.0.legend.fontsize', -1, 'legend.fontsize'),
    ('legend.fontsize 非数字', 'axes.0.legend.fontsize', 'x',
     'legend.fontsize 必须是数字或 null'),
    ('legend.loc 非字符串', 'axes.0.legend.loc', 5, 'legend.loc'),
    ('grid 非布尔', 'axes.0.grid', 1, 'grid 必须是布尔'),
    ('xscale 非法', 'axes.0.xscale', 'banana', '不是支持的轴尺度'),
    ('yscale 非字符串', 'axes.0.yscale', 3, '不是支持的轴尺度'),
]


def run_mutations():
    print('D. 定向畸形突变：每个字段族埋错 → validate_errors 必非空且点名该字段')
    for label, path, value, want in _MUTATIONS:
        d = _mutate(_base(), path, value)
        errs = _params.validate_errors(d)
        got = ' '.join(errs)
        check('%s 被硬错误拦下' % label,
              bool(errs) and want in got, got[:200])
    # load() 语义：version 缺失时 normalize 补成当前值，但 load 打 _version_missing
    # 标记 → validate_errors 仍报"缺少 version"（apply 的 load 路径，与裸 dict 不同）。
    d = _params.normalize({'script': 'target.py', 'axes': []})
    d['_version_missing'] = True
    errs = _params.validate_errors(d)
    check('load() 路径缺 version 被硬错误拦下',
          any('缺少 version' in e for e in errs), ' '.join(errs)[:200])
    # 非 dict 顶层：validate_errors 直接报"顶层不是对象"；load() 路径（apply 读入）
    # 对这类输入打 _top_not_object 标记 → 同样报硬错误（0.1.10 修，不再崩
    # internal_error）。这里两条路径都验。
    check('非 dict 顶层被硬错误拦下',
          _params.validate_errors([1, 2, 3]) and
          _params.validate_errors('hello') and
          '顶层不是对象' in _params.validate_errors([1, 2, 3])[0], '')


# ---------------------------------------------------------------------------
# F. 返回类型契约（t59）：validate / validate_errors 的**每一条返回路径**都是 List[str]
#
# 为什么单独立一节：t56 实测到"顶层不是对象"（load 路径的 `_top_not_object` 标记）
# 那条分支 `return` 的是**一个字符串**（签名写的是 List[str]），旧 apply 于是
#   * `for prob in params.validate(data)` **逐字符**打印（实测 541 行，每行一个字符）；
#   * `'; '.join(_errs)` 把字符用 "; " 串起来 ⇒ --json 的 files[].err 变乱码。
# t56 在调用侧归一避险；t59 把**根因**修在 params.py，这里钉住类型契约 ——
# 下一个调用方不必再自己防一遍。文案与顺序都不许变（zh/en 由 pv_* 键承载）。
# ---------------------------------------------------------------------------
def _write_tmp_params(text, name='types.json'):
    path = os.path.join(_TMPROOT, name)
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        f.write(text)
    return path


def run_return_types():
    print('F. 返回类型契约：validate / validate_errors 恒返回 List[str]')
    cases = [
        ('裸非 dict(list)', [1, 2, 3]),
        ('裸非 dict(str)', 'hello'),
        ('裸非 dict(int)', 7),
        ('load 路径·顶层 list', _params.load(_write_tmp_params('[1, 2, 3]'))),
        ('load 路径·顶层 str', _params.load(_write_tmp_params('"hi"'))),
        ('load 路径·缺 version', _params.load(
            _write_tmp_params('{"script": "target.py", "axes": []}'))),
        ('正常参数', _params.normalize(_base())),
    ]
    for label, d in cases:
        for fname, fn in (('validate_errors', _params.validate_errors),
                          ('validate', _params.validate)):
            try:
                res = fn(d)
            except Exception as e:                              # noqa: BLE001
                check('%s(%s) 不抛异常' % (fname, label), False,
                      _gbk_safe(str(e))[:200])
                continue
            check('%s(%s) 返回 list[str]' % (fname, label),
                  isinstance(res, list) and all(isinstance(x, str) for x in res),
                  '%s: %r' % (type(res).__name__, _gbk_safe(str(res))[:120]))
    # 内容与顺序：顶层非对象那条必须**恰好一句**（而不是被逐字符拆开）
    _d_top = _params.load(_write_tmp_params('[1, 2, 3]'))
    _e_top = _params.validate_errors(_d_top)
    check('顶层非对象的硬错误恰好 1 条、是一句话、点名"顶层不是对象"',
          len(_e_top) == 1 and '顶层不是对象' in _e_top[0]
          and len(_e_top[0]) > 40, repr(_e_top)[:160])
    # validate 对同一条路径也是 list，且以硬错误开头（软问题只追加）
    _v_top = _params.validate(_d_top)
    check('validate(顶层非对象) 同样是 list 且以硬错误开头',
          isinstance(_v_top, list) and _v_top[:1] == _e_top, repr(_v_top)[:160])


# ---------------------------------------------------------------------------
# E. 端到端抽查：apply --write --json --no-verify 的行为与 validate_errors 一致
# ---------------------------------------------------------------------------
_FIX = os.path.join(ROOT, 'tests', 'fixtures', 'synth_script.py')
_E2E_SCRIPT = os.path.join(_TMPROOT, 'target.py')


def _e2e_apply(params_path):
    env = dict(os.environ, PYTHONPATH=os.path.join(ROOT, 'src'),
               MPLBACKEND='Agg', MPLTWEAK_LANG='zh',
               TEMP=_TMPROOT, TMP=_TMPROOT)
    r = subprocess.run([sys.executable, '-m', 'mpltweak.cli', 'apply',
                        _E2E_SCRIPT, '--params', params_path,
                        '--write', '--json', '--no-verify'],
                       capture_output=True, env=env)
    return r.returncode, _decode_out(r.stdout), _decode_out(r.stderr)


def _e2e_one(tag, payload):
    """写一份参数文件跑一次 apply；返回 (rc, out, 脚本是否原样)。"""
    shutil.copyfile(_FIX, _E2E_SCRIPT)      # 每例重新铺纯净脚本
    params_path = os.path.join(_TMPROOT, 'e2e_%s.json' % tag)
    with open(params_path, 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False)
    before = open(_E2E_SCRIPT, 'rb').read()
    rc, out, _err = _e2e_apply(params_path)
    after = open(_E2E_SCRIPT, 'rb').read()
    return rc, out, (before == after)


_E2E_MALFORMED = [
    ('no_version', {'script': 'target.py', 'axes': []}),
    ('version_old', {'version': 1, 'script': 'target.py', 'axes': []}),
    ('figsize_neg', {'version': 4, 'figsize_px': [1500, -10], 'axes': []}),
    ('pos_w_zero', {'version': 4,
                    'axes': [{'index': 0, 'pos': [0.05, 0.1, 0.0, 0.7]}]}),
    ('pos_off_canvas', {'version': 4,
                        'axes': [{'index': 0, 'pos': [1.05, 0.1, 0.2, 0.7]}]}),
    ('index_neg', {'version': 4,
                   'axes': [{'index': -1, 'pos': [0.05, 0.1, 0.4, 0.7]}]}),
    ('fontsize_neg', {'version': 4,
                      'axes': [{'index': 0, 'title_fontsize': -3}]}),
    ('axes_not_list', {'version': 4, 'axes': {'index': 0}}),
    ('clim_len1', {'version': 4, 'axes': [{'index': 0, 'clim': [0.0]}]}),
    ('spines_bad', {'version': 4, 'axes': [{'index': 0, 'spines': [True]}]}),
    ('legend_bad', {'version': 4, 'axes': [{'index': 0, 'legend': 'top'}]}),
    ('xscale_bad', {'version': 4, 'axes': [{'index': 0, 'xscale': 'nope'}]}),
]

_LEGAL_E2E = {'version': 4, 'script': 'target.py',
              'figsize_in': [12.0, 4.8],
              'axes': [{'index': 0, 'pos': [0.05, 0.1, 0.42, 0.78]}]}


def run_e2e():
    print('E. 端到端抽查：apply --write --json 与 validate_errors 一致')
    for tag, payload in _E2E_MALFORMED:
        # 先确认纯函数侧判它有硬错误（CLI 拒写的"依据"）。
        with open(os.path.join(_TMPROOT, 'precheck.json'), 'w',
                  encoding='utf-8') as f:
            json.dump(payload, f)
        hard = _params.validate_errors(
            _params.load(os.path.join(_TMPROOT, 'precheck.json')))
        rc, out, same = _e2e_one(tag, payload)
        check('e2e %s：validate_errors 判硬错误' % tag, bool(hard),
              ' '.join(hard)[:120])
        check('e2e %s：apply 拒写 rc=2' % tag, rc == 2,
              'rc=%s' % rc)
        check('e2e %s：源码字节不变' % tag, same, '')
        check('e2e %s：--json 报 bad_params' % tag,
              '"reason": "bad_params"' in out, out[:160])
    # 顶层非对象 JSON（数组/字符串/数字/null）：0.1.10 修 —— 归 bad_params 硬错误
    # （load 不再崩成 internal_error），rc=2 + 字节不变 + reason=bad_params。
    for tag, payload in (('top_array', [1, 2, 3]),
                         ('top_str', 'hello'),
                         ('top_num', 42),
                         ('top_null', None)):
        with open(os.path.join(_TMPROOT, 'precheck.json'), 'w',
                  encoding='utf-8') as f:
            json.dump(payload, f)
        hard = _params.validate_errors(
            _params.load(os.path.join(_TMPROOT, 'precheck.json')))
        rc, out, same = _e2e_one(tag, payload)
        check('e2e %s：validate_errors 判硬错误' % tag, bool(hard),
              ' '.join(hard)[:120])
        check('e2e %s：apply 拒写 rc=2' % tag, rc == 2, 'rc=%s' % rc)
        check('e2e %s：源码字节不变' % tag, same, '')
        check('e2e %s：--json 报 bad_params' % tag,
              '"reason": "bad_params"' in out, out[:160])
    # 合法参数：不因参数层误伤（rc≠2）；validate_errors 判它合法。
    with open(os.path.join(_TMPROOT, 'precheck.json'), 'w',
              encoding='utf-8') as f:
        json.dump(_LEGAL_E2E, f)
    hard = _params.validate_errors(
        _params.load(os.path.join(_TMPROOT, 'precheck.json')))
    rc, out, _same = _e2e_one('legal', _LEGAL_E2E)
    check('e2e legal：validate_errors 判合法', hard == [], ' '.join(hard)[:120])
    check('e2e legal：不因参数层误伤（rc≠2）', rc != 2, 'rc=%s' % rc)


# ---------------------------------------------------------------------------
def main():
    # A/B/C 三条 property 需要 hypothesis（可选依赖）。装了就跑；没装则**明确打印
    # 跳过原因**，而不是让缺依赖变成"测试失败"（CI step 6 按设计只装 pytest，
    # 见 tests/_hypothesis_shim.py）。下面 D（mutation）与 E（e2e）不依赖它，照跑。
    _hyp_on = _HAS_HYPOTHESIS
    if not _hyp_on:
        print('SKIP | hypothesis 未安装 → A/B/C 三条 property 不执行；'
              'mutation 与 e2e 两部分照常执行（装上 hypothesis 即跑全部）')

    print('A. 任意 JSON 结构 fuzz：validate_errors/validate 永不抛异常')
    if _hyp_on:
        try:
            test_any_json_never_crashes()
            check('任意输入不崩', True)
        except Exception as e:                       # noqa: BLE001
            check('任意输入不崩', False, _gbk_safe(str(e))[:500])

    print('B. validate = 硬错误前缀 + 只追加已知软问题（软/硬严格区分）')
    if _hyp_on:
        try:
            test_validate_is_errors_prefix_plus_soft()
            check('validate 是 errors 前缀 + 已知软问题', True)
        except Exception as e:                       # noqa: BLE001
            check('validate 是 errors 前缀 + 已知软问题', False,
                  _gbk_safe(str(e))[:500])

    print('C. 合法参数生成器 fuzz：validate_errors 必为空（合法不误伤）')
    if _hyp_on:
        try:
            test_legal_params_never_hard_rejected()
            check('合法参数无硬错误', True)
        except Exception as e:                       # noqa: BLE001
            check('合法参数无硬错误', False, _gbk_safe(str(e))[:500])

    run_return_types()

    run_mutations()

    run_e2e()

    shutil.rmtree(_TMPROOT, ignore_errors=True)
    print()
    if _fails:
        print('FAILED: %d 项 -> %s' % (len(_fails), _fails))
        return 1
    print('ALL PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
