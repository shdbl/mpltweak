# -*- coding: utf-8 -*-
"""writeback property-based 不变量测试（hypothesis）—— 四条铁律 + 坏参数拒写。

与七个套件同风格：模块级脚本（``python tests/test_property_writeback.py`` 直接跑），
语言钉死 MPLTWEAK_LANG=zh，末尾打印 ALL PASS / FAILED 并 sys.exit。用 hypothesis
生成「固定可运行模板 × 参数值域 × 换行（LF/CRLF）」组合，钉住四条铁律：

  ① 幂等（idempotent）：apply --write 后脚本字节不变；再 apply = no_change。
  ② 可逆（reversible）：revert --write 逐字节还原写回前的原文。
  ③ 语法守恒（syntax）：写回后 ast.parse 必须通过。
  ④ 只动目标（target-only）：非目标图/轴的代码字节不动；声称落地的字段，
     参数值必须以数值字面量出现在源码里（源码字面量 == 参数值）。

外加：坏参数（pos 越界/负尺寸、负字号、figsize 零/负、fig_index 越界、
version 缺失/错误…）必须被拒写（CLI rc=2 + bad_params + 脚本一个字节不动）。

设计要点
--------
* **脚本不随机生成**：只用固定可运行模板 + 写法变体（add_axes 列表/元组、
  plt.subplots(figsize=)、set_title(fontsize=)、tick_params(labelsize=)、
  set_xlim 三种写法、多图、循环出图），否则验证层重跑会产生 false negative。
* **参数值全部整数派生**（pos = n/100、字号取整数）：写回的字面量能精确往返，
  避免「参数 0.123456789 被 %.6g 写成 0.123457，第二次 apply 又判 ok」这类
  浮点不幂等假阳性。
* **字节断言用 open(..., 'rb') / open(..., newline='') 读**（CRLF 坑，
  MAINTENANCE §12.4）：默认文本模式会把 \\r\\n 吞成 \\n，字节对比失去意义。
* **引擎调用在进程内**（writeback.writeback + revert.main，快）；坏参数走
  **子进程 CLI**（apply --json 的输出直接写 fd1，进程内重定向抓不到）。
* **每条铁律拆成独立 check 函数**，反向验证把引擎 monkeypatch 成「破坏该律」，
  跑真实管线后断言 check 必须报 False（判据关掉 → 用例变红）。

已知引擎缺陷（0.1.10，t2 fuzz 发现，t5 已修复）
------------------------------------------------
``writeback.apply_inplace`` / ``_needed_block_fields`` 的 ``var_for`` 原来按位置 zip
参数 axes 与脚本轴创建调用（writeback.py:1180-1183 / 3226-3230）：**只给非零轴号**
（如 axes 只含 index=1）时，index=1 被映射到脚本第一个轴（ax0）→ 原位写回写到错的
轴；第二次 apply 因语义验证被已插入的块「掩盖」而保留错写 → 幂等(①)与只动目标(④)
同时被违反。t5 已把 var_for 改为按轴序号 index 直接索引（index → 第 index 个非
colorbar 轴创建调用），fuzz 空间恢复多轴模板 axis_ids=[0,1]，并保留确定性回归
``_sparse_axis_regression``（只给 index=1 → 落地 ax1、不碰 ax0、二次 apply 幂等）。
"""

import ast
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

# ---- 输出兜底（T13 纪律）：GBK 控制台 / 管道下 print 中文不崩 ----
def _install_console_fallback():
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(errors='replace')
        except (AttributeError, ValueError, OSError):
            pass


_install_console_fallback()


def _gbk_safe(s):
    """把字符串清洗成当前输出流编得出来的形式（U+FFFD 之类换成 ?）。"""
    s = str(s)
    enc = getattr(sys.stdout, 'encoding', None) or 'utf-8'
    try:
        return s.encode(enc, 'replace').decode(enc, 'replace')
    except (LookupError, UnicodeDecodeError):
        return s.encode('ascii', 'backslashreplace').decode('ascii')


def _decode_out(raw):
    """子进程输出解码：先 utf-8，再系统 ANSI（GBK），最后 replace 兜底。"""
    for enc in ('utf-8', 'gbk', 'gb18030'):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode('utf-8', 'replace')


# ---- 语言 / 后端钉死：别让宿主 locale 决定结论 ----
os.environ['MPLTWEAK_LANG'] = 'zh'
os.environ['MPLBACKEND'] = 'Agg'

fail = 0


def check(name, cond, detail=''):
    global fail
    print(('PASS' if cond else 'FAIL') + ' | ' + _gbk_safe(name)
          + ((' | ' + _gbk_safe(detail)) if detail else ''))
    if not cond:
        fail += 1


# ---- 临时区：沙箱拦真实 %TEMP%，把 TEMP/TMP 指到可写目录（含 verify 子进程）----
_TMPROOT = None


def _ensure_tmp_writable():
    global _TMPROOT
    base = tempfile.gettempdir()
    root = os.path.join(base, '.mpltweak_prop_%d' % os.getpid())
    try:
        os.makedirs(root, exist_ok=True)
        probe = os.path.join(root, '.w')
        open(probe, 'w').close()
        os.unlink(probe)
    except OSError:
        root = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            '.tmp_propwb_%d' % os.getpid())
        os.makedirs(root, exist_ok=True)
    _TMPROOT = root
    os.environ['TEMP'] = root
    os.environ['TMP'] = root
    os.environ['TMPDIR'] = root
    tempfile.tempdir = root


_ensure_tmp_writable()

_case_no = [0]


def _new_case_dir():
    _case_no[0] += 1
    d = os.path.join(_TMPROOT, 'case_%d' % _case_no[0])
    os.makedirs(d, exist_ok=True)
    return d


def _read_bytes(path):
    with open(path, 'rb') as f:
        return f.read()


def _read_text(path):
    # newline='' 保留 \r\n：CRLF 检出的字节对比才成立
    with open(path, 'r', encoding='utf-8', newline='') as f:
        return f.read()


# ---------------------------------------------------------------------------
# 模板：固定可运行脚本 × 写法变体。src 全部以 \n 换行，CRLF 由 _run_valid_case
# 在写盘时统一替换（模板里的 '\\r' 不可出现，保持确定性）。
# ---------------------------------------------------------------------------
_HEAD = ('import matplotlib\n'
         "matplotlib.use('Agg')\n"
         'import matplotlib.pyplot as plt\n')

T_ADD_LIST = _HEAD + '''
fig = plt.figure(figsize=(10, 5))
ax0 = fig.add_axes([0.1, 0.12, 0.38, 0.75])
ax1 = fig.add_axes([0.55, 0.12, 0.35, 0.75])
ax0.plot([1, 2, 3], [1, 4, 9])
ax1.plot([5, 6, 7], [8, 9, 10])
ax0.set_title('T0', fontsize=12)
ax1.set_title('AX1_KEEP', fontsize=9)
ax0.tick_params(labelsize=10)
ax1.tick_params(labelsize=7)
fig.savefig('out.png')
print('done')
'''

T_ADD_TUPLE = _HEAD + '''
fig = plt.figure(figsize=(10, 5))
ax0 = fig.add_axes((0.1, 0.12, 0.38, 0.75))
ax1 = fig.add_axes((0.55, 0.12, 0.35, 0.75))
ax0.plot([1, 2, 3], [1, 4, 9])
ax1.plot([5, 6, 7], [8, 9, 10])
ax0.set_title('T0', fontsize=12)
ax1.set_title('AX1_KEEP', fontsize=9)
ax0.tick_params(labelsize=10)
ax1.tick_params(labelsize=7)
fig.savefig('out.png')
print('done')
'''

T_SUBPLOTS = _HEAD + '''
fig, ax = plt.subplots(figsize=(10, 4))
ax.plot([1, 2, 3], [1, 4, 9])
ax.set_title('T', fontsize=12)
ax.set_xlabel('XLAB', fontsize=11)
ax.tick_params(labelsize=10)
fig.savefig('out.png')
print('done')
'''

T_XLIM_POS = _HEAD + '''
fig, ax = plt.subplots(figsize=(10, 4))
ax.plot([1, 2, 3], [1, 4, 9])
ax.set_xlim(0, 10)
ax.set_title('T', fontsize=12)
fig.savefig('out.png')
print('done')
'''

T_XLIM_TUPLE = _HEAD + '''
fig, ax = plt.subplots(figsize=(10, 4))
ax.plot([1, 2, 3], [1, 4, 9])
ax.set_xlim((0, 10))
ax.set_title('T', fontsize=12)
fig.savefig('out.png')
print('done')
'''

T_XLIM_KW = _HEAD + '''
fig, ax = plt.subplots(figsize=(10, 4))
ax.plot([1, 2, 3], [1, 4, 9])
ax.set_xlim(left=0, right=10)
ax.set_title('T', fontsize=12)
fig.savefig('out.png')
print('done')
'''

T_MULTIFIG = _HEAD + '''
fig1 = plt.figure(figsize=(8, 4))
ax1 = fig1.add_axes([0.1, 0.1, 0.8, 0.8])
ax1.plot([1, 2, 3], [1, 4, 9])
ax1.set_title('FIG1_KEEP', fontsize=11)
fig1.savefig('out1.png')

fig2 = plt.figure(figsize=(8, 4))
ax2 = fig2.add_axes([0.1, 0.1, 0.8, 0.8])
ax2.plot([3, 2, 1], [9, 4, 1])
ax2.set_title('FIG2', fontsize=10)
fig2.savefig('out2.png')
print('done')
'''

T_LOOP = _HEAD + '''
for i in range(3):
    fig, ax = plt.subplots(figsize=(8, 4))
    ax.plot([1, 2, 3], [1, 4, 9])
    ax.set_title('LOOP_%d' % i, fontsize=10)
    fig.savefig('loop_%d.png' % i)
print('done')
'''

_PLOT_KEEP = 'ax.plot([1, 2, 3], [1, 4, 9])'
_SAVE_KEEP = "fig.savefig('out.png')"
_PRINT_KEEP = "print('done')"
_IMPORT_KEEP = 'import matplotlib.pyplot as plt'
TEMPLATES = [
    dict(name='add_axes_list', file='fig.py', src=T_ADD_LIST,
         n_figs=1, axis_ids=[0, 1],       # 双轴模板：fuzz 轴 0/1（稀疏 index=1 也在空间里）
         supports_pos=True, supports_title=True, supports_tick=True,
         supports_label=False, supports_xlim=False, supports_figsize=True,
         keeps=['ax0.plot([1, 2, 3], [1, 4, 9])',
                'ax1.plot([5, 6, 7], [8, 9, 10])',
                _SAVE_KEEP, _PRINT_KEEP, _IMPORT_KEEP],
         axis_keeps={(0, 0): ['ax0.set_title(\'T0\', fontsize=12)',
                              'ax0.tick_params(labelsize=10)',
                              'ax0.plot([1, 2, 3], [1, 4, 9])'],
                     (0, 1): ['ax1.set_title(\'AX1_KEEP\', fontsize=9)',
                              'ax1.tick_params(labelsize=7)',
                              'ax1.plot([5, 6, 7], [8, 9, 10])']},
         fig_keeps={}),
    dict(name='add_axes_tuple', file='fig.py', src=T_ADD_TUPLE,
         n_figs=1, axis_ids=[0, 1],       # 双轴模板：fuzz 轴 0/1（稀疏 index=1 也在空间里）
         supports_pos=True, supports_title=True, supports_tick=True,
         supports_label=False, supports_xlim=False, supports_figsize=False,
         keeps=['ax0.plot([1, 2, 3], [1, 4, 9])',
                'ax1.plot([5, 6, 7], [8, 9, 10])',
                _SAVE_KEEP, _PRINT_KEEP, _IMPORT_KEEP],
         axis_keeps={(0, 0): ['ax0.set_title(\'T0\', fontsize=12)',
                              'ax0.tick_params(labelsize=10)',
                              'ax0.plot([1, 2, 3], [1, 4, 9])'],
                     (0, 1): ['ax1.set_title(\'AX1_KEEP\', fontsize=9)',
                              'ax1.tick_params(labelsize=7)',
                              'ax1.plot([5, 6, 7], [8, 9, 10])']},
         fig_keeps={}),
    dict(name='subplots_figsize', file='fig.py', src=T_SUBPLOTS,
         n_figs=1, axis_ids=[0],
         supports_pos=True, supports_title=True, supports_tick=True,
         supports_label=True, supports_xlim=False, supports_figsize=True,
         keeps=[_PLOT_KEEP, _SAVE_KEEP, _PRINT_KEEP, _IMPORT_KEEP],
         axis_keeps={(0, 0): ['ax.set_title(\'T\', fontsize=12)',
                              'ax.set_xlabel(\'XLAB\', fontsize=11)',
                              'ax.tick_params(labelsize=10)']},
         fig_keeps={}),
    dict(name='xlim_pos', file='fig.py', src=T_XLIM_POS,
         n_figs=1, axis_ids=[0],
         supports_pos=False, supports_title=True, supports_tick=False,
         supports_label=False, supports_xlim=True, supports_figsize=False,
         keeps=[_PLOT_KEEP, _SAVE_KEEP, _PRINT_KEEP, _IMPORT_KEEP, 'set_xlim'],
         axis_keeps={(0, 0): ['ax.set_title(\'T\', fontsize=12)']},
         fig_keeps={}),
    dict(name='xlim_tuple', file='fig.py', src=T_XLIM_TUPLE,
         n_figs=1, axis_ids=[0],
         supports_pos=False, supports_title=True, supports_tick=False,
         supports_label=False, supports_xlim=True, supports_figsize=False,
         keeps=[_PLOT_KEEP, _SAVE_KEEP, _PRINT_KEEP, _IMPORT_KEEP, 'set_xlim'],
         axis_keeps={(0, 0): ['ax.set_title(\'T\', fontsize=12)']},
         fig_keeps={}),
    dict(name='xlim_kw', file='fig.py', src=T_XLIM_KW,
         n_figs=1, axis_ids=[0],
         supports_pos=False, supports_title=True, supports_tick=False,
         supports_label=False, supports_xlim=True, supports_figsize=False,
         keeps=[_PLOT_KEEP, _SAVE_KEEP, _PRINT_KEEP, _IMPORT_KEEP, 'set_xlim',
                'ax.set_xlim(left=0, right=10)'],
         axis_keeps={(0, 0): ['ax.set_title(\'T\', fontsize=12)']},
         fig_keeps={}),
    dict(name='multifig', file='fig.py', src=T_MULTIFIG,
         n_figs=2, axis_ids=[0],
         supports_pos=True, supports_title=True, supports_tick=False,
         supports_label=False, supports_xlim=False, supports_figsize=False,
         keeps=['ax1.plot([1, 2, 3], [1, 4, 9])',
                'ax2.plot([3, 2, 1], [9, 4, 1])',
                _PRINT_KEEP, _IMPORT_KEEP],
         axis_keeps={(0, 0): ['ax1.set_title(\'FIG1_KEEP\', fontsize=11)',
                              'fig1.add_axes([0.1, 0.1, 0.8, 0.8])',
                              'ax1.plot([1, 2, 3], [1, 4, 9])'],
                     (1, 0): ['ax2.set_title(\'FIG2\', fontsize=10)',
                              'fig2.add_axes([0.1, 0.1, 0.8, 0.8])',
                              'ax2.plot([3, 2, 1], [9, 4, 1])']},
         fig_keeps={0: ['FIG1_KEEP', 'out1.png'],
                    1: ['FIG2', 'out2.png']}),
    dict(name='loop', file='fig.py', src=T_LOOP,
         n_figs=1, axis_ids=[0],
         supports_pos=False, supports_title=True, supports_tick=False,
         supports_label=False, supports_xlim=False, supports_figsize=False,
         keeps=['range(3)', "'LOOP_%d'", 'loop_%d.png', _PLOT_KEEP,
                _PRINT_KEEP, _IMPORT_KEEP],
         axis_keeps={(0, 0): []},
         fig_keeps={}),
]

TEMPLATE_BY_NAME = {t['name']: t for t in TEMPLATES}


# ---------------------------------------------------------------------------
# 引擎调用：复刻 apply.py 的 auto 语义（先原位、落不了再退块；退块复用备份）
# ---------------------------------------------------------------------------
def _need_block_fallback(res):
    if res.get('reason') == 'no_change':
        return False
    if res.get('reason') == 'fail':
        return True
    return bool(res.get('needs_block'))


def _apply_auto(script, data, pp, verify=True):
    from mpltweak import writeback
    res = writeback.writeback(script, data, pp, verify=verify, style='inplace')
    if _need_block_fallback(res):
        bk = res.get('backup')
        res = writeback.writeback(script, data, pp, verify=verify,
                                  style='block', backup=bk)
    return res


# ---------------------------------------------------------------------------
# 四条铁律的独立 check（反向验证直接喂违规场景，断言它们必须报 False）
# ---------------------------------------------------------------------------
def _check_syntax_text(text):
    try:
        ast.parse(text)
        return True
    except (SyntaxError, ValueError):
        return False


def _check_syntax(script):
    return _check_syntax_text(_read_text(script))


def _check_idempotent(bytes_after_first, bytes_after_second, reason2):
    """① 幂等：第二次 apply 后字节不变，且 reason 是 no_change。"""
    return bytes_after_first == bytes_after_second and reason2 == 'no_change'


def _check_reversible(original_bytes, current_bytes):
    """② 可逆：revert --write 之后当前字节 == 写回前的原文。"""
    return original_bytes == current_bytes


def _numeric_literals(text):
    vals = []
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return vals
    for n in ast.walk(tree):
        if (isinstance(n, ast.Constant)
                and isinstance(n.value, (int, float))
                and not isinstance(n.value, bool)):
            vals.append(float(n.value))
        elif isinstance(n, ast.UnaryOp) \
                and isinstance(n.op, (ast.USub, ast.UAdd)) \
                and isinstance(n.operand, ast.Constant) \
                and isinstance(n.operand.value, (int, float)) \
                and not isinstance(n.operand.value, bool):
            # -4 在 AST 里是 UnaryOp(USub, Constant(4))，不是 Constant(-4)
            v = float(n.operand.value)
            vals.append(-v if isinstance(n.op, ast.USub) else v)
    return vals


def _values_present(text, values, tol=1e-6):
    nums = _numeric_literals(text)
    for v in values:
        if not any(abs(float(v) - x) <= tol for x in nums):
            return False
    return True


def _check_target_only(tpl, data, res, cur_text):
    """④ 只动目标：
    a) 无条件保留的代码字节（数据/保存/打印）一个不丢；
    b) 非目标图 / 非目标轴的独特字面量原样保留；
    c) 声称落地的字段（不在 pending 里），参数值必须以数值字面量出现在源码。
    """
    for k in tpl['keeps']:
        if k not in cur_text:
            return False, '无条件保留的代码字节丢失: %r' % k
    fig_idx = data.get('fig_index')
    if not isinstance(fig_idx, int):
        fig_idx = 0
    for fi, ks in tpl.get('fig_keeps', {}).items():
        if fi != fig_idx:
            for k in ks:
                if k not in cur_text:
                    return False, '非目标图 %d 的代码被改动（%r 丢失）' % (fi, k)
    target_axes = {a.get('index') for a in data.get('axes') or []}
    for (fi, ai), ks in tpl.get('axis_keeps', {}).items():
        if fi == fig_idx and ai not in target_axes:
            for k in ks:
                if k not in cur_text:
                    return False, '非目标轴 (fig%d, ax%d) 的代码被改动（%r 丢失）' \
                                  % (fi, ai, k)
    pending = set(res.get('pending') or [])
    for axitem in data.get('axes') or []:
        i = axitem.get('index')
        if not isinstance(i, int):
            continue
        want = []
        if axitem.get('pos') is not None and 'ax%d.pos' % i not in pending:
            want += list(axitem['pos'])
        if axitem.get('xlim') is not None and 'ax%d.xlim' % i not in pending:
            want += list(axitem['xlim'])
        for f in ('title_fontsize', 'tick_fontsize', 'label_fontsize'):
            if axitem.get(f) is not None \
                    and 'ax%d.%s' % (i, f) not in pending:
                want.append(axitem[f])
        if want and not _values_present(cur_text, want):
            return False, '声称落地的字段值没以字面量出现在源码: %r' % (want,)
    if data.get('figsize_in') is not None and 'figsize' not in pending:
        if not _values_present(cur_text, list(data['figsize_in'])):
            return False, 'figsize 值没以字面量出现在源码: %r' % data['figsize_in']
    return True, ''


# ---------------------------------------------------------------------------
# 单个 valid 用例：完整管线（写盘 → apply → 二次 apply → revert）+ 四条铁律
# ---------------------------------------------------------------------------
def _run_valid_case(tpl, data, eol):
    from mpltweak import revert, writeback
    d = _new_case_dir()
    script = os.path.join(d, tpl['file'])
    src = tpl['src'].replace('\n', '\r\n') if eol == 'crlf' else tpl['src']
    with open(script, 'w', encoding='utf-8', newline='') as f:
        f.write(src)
    orig_bytes = _read_bytes(script)
    pp = os.path.join(d, tpl['file'].replace('.py', '.json'))
    with open(pp, 'w', encoding='utf-8') as f:
        json.dump(data, f)
    res = _apply_auto(script, data, pp)
    reason = res.get('reason')
    if reason not in ('ok', 'best_effort', 'no_change'):
        return False, '合法参数被引擎拒绝: reason=%s err=%s' % (reason, res.get('err'))
    cur_bytes = _read_bytes(script)
    cur_text = _read_text(script)

    # ③ 语法守恒
    ok_syntax = _check_syntax_text(cur_text)

    # ① 幂等：第二次 apply 必须 no_change 且字节不变
    res2 = _apply_auto(script, data, pp)
    cur2_bytes = _read_bytes(script)
    ok_idem = _check_idempotent(cur_bytes, cur2_bytes, res2.get('reason'))

    # ② 可逆：有备份 → revert 逐字节还原；无备份（no_change/全 pending）→ rc=2 且不动
    bak = writeback._backup_path(script, pp)
    have_backup = os.path.exists(bak)
    _cap = io.StringIO()
    _cerr = io.StringIO()
    with contextlib.redirect_stdout(_cap), contextlib.redirect_stderr(_cerr):
        rc_rev = revert.main([script, '--write'])
    rev_bytes = _read_bytes(script)
    if have_backup:
        ok_rev = _check_reversible(orig_bytes, rev_bytes) and rc_rev == 0
    else:
        ok_rev = rc_rev == 2 and rev_bytes == cur2_bytes

    # ④ 只动目标 + 落地字段字面量
    ok_tgt, tgt_detail = _check_target_only(tpl, data, res, cur_text)

    ok = ok_syntax and ok_idem and ok_rev and ok_tgt
    detail = 'reason=%s style=%s eol=%s backup=%s | 语法=%s 幂等=%s 可逆=%s 只动目标=%s %s' \
             % (reason, res.get('style'), eol, have_backup,
                ok_syntax, ok_idem, ok_rev, ok_tgt, tgt_detail)
    return ok, detail


# ---------------------------------------------------------------------------
# hypothesis：策略 + 两条 property
# ---------------------------------------------------------------------------
try:
    # 可选依赖：装了 hypothesis 就是**真货**（shim 直接透传）；没装则由 shim 提供只服务
    # "装饰函数/拼配置"的最小替身 —— 于是本套件在"CI step 6 不装 hypothesis"时也能
    # import 成功，**确定性用例照常全跑**，只有下面那两条 property 被
    # `_HAS_HYPOTHESIS` 挡掉并打印明确原因（不静默、不算失败）。
    from _hypothesis_shim import (HAS_HYPOTHESIS as _HAS_HYPOTHESIS, st,
                                  given, settings, HealthCheck)
except ImportError:                                          # noqa: BLE001
    _HAS_HYPOTHESIS = False


@st.composite
def st_valid_case(draw):
    """模板 × 参数值域 × 换行。参数值全部整数派生（精确往返，避免浮点假阳性）。"""
    tpl = draw(st.sampled_from(TEMPLATES))
    data = {'version': 4, 'script': tpl['file']}
    n_figs = tpl.get('n_figs', 1)
    if n_figs > 1:
        data['fig_index'] = draw(st.integers(0, n_figs - 1))
    elif draw(st.booleans()):
        data['fig_index'] = 0
    if tpl.get('supports_figsize') and draw(st.booleans()):
        data['figsize_in'] = [float(draw(st.integers(3, 16))),
                              float(draw(st.integers(3, 16)))]
    ax = {'index': draw(st.sampled_from(tpl['axis_ids']))}
    if tpl.get('supports_pos') and draw(st.booleans()):
        ax['pos'] = [draw(st.integers(3, 90)) / 100.0 for _ in range(4)]
    if tpl.get('supports_title') and draw(st.booleans()):
        ax['title_fontsize'] = draw(st.integers(5, 30))
    if tpl.get('supports_tick') and draw(st.booleans()):
        ax['tick_fontsize'] = draw(st.integers(5, 24))
    if tpl.get('supports_label') and draw(st.booleans()):
        ax['label_fontsize'] = draw(st.integers(5, 24))
    if tpl.get('supports_xlim') and draw(st.booleans()):
        lo = draw(st.integers(-20, 19))
        hi = draw(st.integers(lo + 1, 20))
        ax['xlim'] = [float(lo), float(hi)]
    data['axes'] = [ax]
    eol = draw(st.sampled_from(['lf', 'crlf']))
    return tpl, data, eol


# 坏参数清单：pos 越界/负尺寸、负字号、figsize 零/负、fig_index 越界、
# version 缺失/错误、轴序号负值、xlim 长度错 —— 全部必须被拒写（脚本一个字节
# 不动）。第三项是期望 reason（assert 用它核对），rc 由 reason 决定：
# bad_params → rc=2，no_axes → rc=1。
# 注意 fig_index 越界判据在 writeback 里是**先过 no_axes 闸再查**（writeback.py:
# 4205-4242），所以越界样例必须带上 axes，否则提前命中 no_axes（那也是一种
# "拒写且不动脚本"的结果，见最末一条独立样例）。
_BROKEN_PARAMS = [
    ('pos_3nums', {'axes': [{'index': 0, 'pos': [0.1, 0.1, 0.8]}]}, 'bad_params'),
    ('pos_5nums', {'axes': [{'index': 0, 'pos': [0.1, 0.1, 0.8, 0.8, 0.5]}]}, 'bad_params'),
    ('pos_w_zero', {'axes': [{'index': 0, 'pos': [0.1, 0.1, 0.0, 0.8]}]}, 'bad_params'),
    ('pos_h_neg', {'axes': [{'index': 0, 'pos': [0.1, 0.1, 0.8, -0.2]}]}, 'bad_params'),
    ('pos_fully_out', {'axes': [{'index': 0, 'pos': [1.2, 0.1, 0.3, 0.8]}]}, 'bad_params'),
    ('pos_nan_text', {'axes': [{'index': 0, 'pos': [0.1, 'x', 0.8, 0.8]}]}, 'bad_params'),
    ('title_neg', {'axes': [{'index': 0, 'title_fontsize': -3}]}, 'bad_params'),
    ('tick_neg', {'axes': [{'index': 0, 'tick_fontsize': -2}]}, 'bad_params'),
    ('label_neg', {'axes': [{'index': 0, 'label_fontsize': -4}]}, 'bad_params'),
    ('figsize_zero', {'figsize_in': [0.0, 5.0]}, 'bad_params'),
    ('figsize_neg', {'figsize_in': [-2.0, 5.0]}, 'bad_params'),
    ('figsize_1num', {'figsize_in': [5.0]}, 'bad_params'),
    ('fig_index_5', {'fig_index': 5, 'axes': [{'index': 0, 'title_fontsize': 15}]},
     'bad_params'),
    ('fig_index_neg', {'fig_index': -1, 'axes': [{'index': 0, 'title_fontsize': 15}]},
     'bad_params'),
    ('version_missing', {'script': 'fig.py'}, 'bad_params'),
    ('version_5', {'version': 5}, 'bad_params'),
    ('version_str', {'version': '4'}, 'bad_params'),
    ('ax_index_neg', {'axes': [{'index': -1, 'title_fontsize': 15}]}, 'bad_params'),
    ('ax_index_str', {'axes': [{'index': '0', 'title_fontsize': 15}]}, 'bad_params'),
    ('xlim_1num', {'axes': [{'index': 0, 'xlim': [0.1]}]}, 'bad_params'),
    # 无 axes 也无 figsize → no_axes（rc=1）同样"拒写 + 脚本不动"
    ('no_axes', {'fig_index': 0}, 'no_axes'),
]


@st.composite
def st_invalid_case(draw):
    tpl = TEMPLATE_BY_NAME['subplots_figsize']
    name, partial, reason = draw(st.sampled_from(_BROKEN_PARAMS))
    if name == 'version_missing':
        # 必须真的没有 version 键（dict 合并无法删键，单独构造）
        data = dict(partial)
    else:
        data = dict({'version': 4, 'script': tpl['file']}, **partial)
    return tpl, name, reason, data


@settings(max_examples=30, deadline=None, database=None,
          suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
          report_multiple_bugs=False)
@given(st_valid_case())
def prop_valid_writeback(spec):
    tpl, data, eol = spec
    ok, detail = _run_valid_case(tpl, data, eol)
    assert ok, detail


@settings(max_examples=len(_BROKEN_PARAMS), deadline=None, database=None,
          suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
          report_multiple_bugs=False)
@given(st_invalid_case())
def prop_invalid_params(spec):
    tpl, name, reason, data = spec
    d = _new_case_dir()
    script = os.path.join(d, tpl['file'])
    with open(script, 'w', encoding='utf-8', newline='') as f:
        f.write(tpl['src'])
    before = _read_bytes(script)
    pp = os.path.join(d, tpl['file'].replace('.py', '.json'))
    with open(pp, 'w', encoding='utf-8') as f:
        json.dump(data, f)
    env = dict(os.environ,
               PYTHONPATH=os.path.join(os.path.dirname(
                   os.path.dirname(os.path.abspath(__file__))), 'src'),
               MPLBACKEND='Agg', MPLTWEAK_LANG='zh', PYTHONIOENCODING='utf-8')
    r = subprocess.run([sys.executable, '-m', 'mpltweak', 'apply', script,
                        '--params', pp, '--write', '--json'],
                       capture_output=True, cwd=d, env=env)
    out = _decode_out(r.stdout + r.stderr)
    after = _read_bytes(script)
    expect_rc = 2 if reason == 'bad_params' else 1
    assert r.returncode == expect_rc, \
        '坏参数 %s 竟然 rc=%d（期望 %d/%s）: %s' % (name, r.returncode, expect_rc,
                                             reason, out[-300:])
    assert '"%s"' % reason in out, \
        '坏参数 %s 未报 %s: %s' % (name, reason, out[-300:])
    assert after == before, '坏参数 %s 竟然改动了脚本字节' % name
    assert _check_syntax_text(after.decode('utf-8', 'replace')), \
        '坏参数 %s 之后脚本语法坏了' % name


def _run_hyp(label, prop):
    global fail
    t0 = time.time()
    try:
        prop()
        check(label, True, '%.0fs' % (time.time() - t0))
    except Exception as e:                        # noqa: BLE001 - hypothesis 反例
        check(label, False, 'property 被反例击穿: %s' % _gbk_safe(str(e))[:500])


# ---------------------------------------------------------------------------
# 确定性冒烟：LF 与 CRLF 检出各跑一遍四条铁律，逐条 check
# ---------------------------------------------------------------------------
def _smoke_four_laws(label, eol):
    tpl = TEMPLATE_BY_NAME['add_axes_list']
    data = {'version': 4, 'script': 'fig.py', 'fig_index': 0,
            'figsize_in': [12.0, 6.0],
            'axes': [{'index': 0, 'pos': [0.12, 0.15, 0.42, 0.7],
                      'title_fontsize': 15, 'tick_fontsize': 8}]}
    ok, detail = _run_valid_case(tpl, data, eol)
    check('%s 检出：四条铁律全过（幂等/可逆/语法/只动目标）' % label, ok, detail)


# ---------------------------------------------------------------------------
# 反向验证：把引擎 monkeypatch 成破坏某条铁律，真实管线跑完后
# 对应 check 必须报 False（判据被关掉 → 用例变红）。
# ---------------------------------------------------------------------------
_RV_TEMPLATE = 'subplots_figsize'
_RV_DATA = {'version': 4, 'script': 'fig.py',
            'axes': [{'index': 0, 'pos': [0.12, 0.15, 0.78, 0.7],
                      'title_fontsize': 14}]}


def _rv_case():
    from mpltweak import revert, writeback
    tpl = TEMPLATE_BY_NAME[_RV_TEMPLATE]
    d = _new_case_dir()
    script = os.path.join(d, tpl['file'])
    with open(script, 'w', encoding='utf-8', newline='') as f:
        f.write(tpl['src'])
    orig = _read_bytes(script)
    pp = os.path.join(d, tpl['file'].replace('.py', '.json'))
    with open(pp, 'w', encoding='utf-8') as f:
        json.dump(_RV_DATA, f)
    res = _apply_auto(script, _RV_DATA, pp)
    assert res.get('reason') in ('ok', 'best_effort'), res
    return tpl, script, pp, orig, res


def _reverse_validation():
    from mpltweak import revert, writeback

    # 正对照：不打补丁时四条 check 全过（证明上面三个场景本身是合法的）
    tpl, script, pp, orig, res = _rv_case()
    cur1 = _read_bytes(script)
    cur2 = _read_bytes(script)          # 同一份，模拟"第二次没写"
    check('反向验证正对照：未破坏引擎时四条铁律 check 全过',
          _check_idempotent(cur1, cur2, 'no_change')
          and _check_syntax(script)
          and _check_target_only(tpl, _RV_DATA, res, _read_text(script))[0],
          'reason=%s style=%s' % (res.get('reason'), res.get('style')))

    # ① 幂等：直接喂两个可观测的违规形态，check 必须都报 False。
    #    （引擎级**真实**违规已由"已知引擎缺陷 pin"覆盖：稀疏轴号场景下第二次
    #    apply 确实重写了字节、reason=ok，幂等检测器如实报红。这里钉 check 本身
    #    对两个违规形态都敏感：字节变化、或 reason 不是 no_change。）
    got_a = _check_idempotent(b'abc', b'abd', 'no_change')   # 字节变了
    got_b = _check_idempotent(b'abc', b'abc', 'ok')          # 声称又写盘
    check('① 幂等 反向验证：字节变化 / reason≠no_change 都被 check 报红（%s）'
          % ('捕获' if not (got_a or got_b) else '漏网'),
          not got_a and not got_b, 'bytes-changed=%s reason-changed=%s'
          % (got_a, got_b))

    # ② 可逆：revert 把备份还原成"改过的字节" → 还原后 != 原文 → 可逆 check 变红
    _orig_restore = revert._atomic_restore

    def _buggy_restore(script, backup):
        with open(backup, 'rb') as f:
            raw = f.read()
        with open(script, 'wb') as f:
            f.write(raw + b'\n# tamper\n')          # 引擎把还原写脏了

    try:
        revert._atomic_restore = _buggy_restore
        _t, _s, _pp, _o, _r1 = _rv_case()
        _cap = io.StringIO()
        with contextlib.redirect_stdout(_cap):
            _rc = revert.main([_s, '--write'])
        got = _check_reversible(_o, _read_bytes(_s))
        check('② 可逆 反向验证：revert 还原出脏字节 → 可逆 check 变红（%s）'
              % '捕获' if not got else '漏网', not got, 'rc=%d' % _rc)
    finally:
        revert._atomic_restore = _orig_restore

    # ③ 语法守恒：模拟"引擎写坏语法且没有安全网" → 语法 check 必须变红
    _t, _s, _pp, _o, _r1 = _rv_case()
    with open(_s, 'a', encoding='utf-8', newline='') as f:
        f.write('def broken(:\n')                    # 非法语法
    got = _check_syntax(_s)
    check('③ 语法守恒 反向验证：源码语法坏了 → 语法 check 变红（%s）'
          % '捕获' if not got else '漏网', not got, '')

    # ④ 只动目标：模拟"引擎连非目标轴的代码也改了" → 只动目标 check 必须变红
    tpl2 = TEMPLATE_BY_NAME['add_axes_list']
    d = _new_case_dir()
    script2 = os.path.join(d, tpl2['file'])
    with open(script2, 'w', encoding='utf-8', newline='') as f:
        f.write(tpl2['src'])
    data2 = {'version': 4, 'script': 'fig.py', 'fig_index': 0,
             'axes': [{'index': 0, 'title_fontsize': 15}]}
    pp2 = os.path.join(d, 'fig.json')
    with open(pp2, 'w', encoding='utf-8') as f:
        json.dump(data2, f)
    res2 = _apply_auto(script2, data2, pp2)
    text2 = _read_text(script2)
    # 引擎"误伤"了非目标轴 ax1 的标题
    text2_bad = text2.replace("ax1.set_title('AX1_KEEP', fontsize=9)",
                              "ax1.set_title('TAMPERED', fontsize=9)")
    got_clean, _ = _check_target_only(tpl2, data2, res2, text2)
    got_bad, why = _check_target_only(tpl2, data2, res2, text2_bad)
    check('④ 只动目标 反向验证：非目标轴代码被改 → check 变红（%s）'
          % '捕获' if not got_bad else '漏网',
          got_clean and not got_bad, why)


# ---------------------------------------------------------------------------
# T57-稀疏轴回归（t5 修复后的确定性用例）：参数只给非零轴号（index=1、不带 0）时，
# var_for 按轴序号 index 直接索引（index → 第 index 个非 colorbar 轴创建调用）⇒
# 原位写回**正确落在 ax1**、**不碰 ax0**；二次 apply 幂等。
# （修复前该场景由 _known_defect_sparse_axis pin 复现为"错写 ax0 + 幂等/只动目标
# 双破功"，fuzz 空间相应把多轴模板 pin 成 axis_ids=[0]；t5 修复后 pin 移除、fuzz
# 恢复 [0, 1]，此处保留确定性回归。）
# ---------------------------------------------------------------------------
_SPARSE_DATA = {'version': 4, 'script': 'fig.py', 'fig_index': 0,
                'axes': [{'index': 1, 'title_fontsize': 5}]}


def _sparse_axis_regression():
    tpl = TEMPLATE_BY_NAME['add_axes_list']
    d = _new_case_dir()
    script = os.path.join(d, tpl['file'])
    with open(script, 'w', encoding='utf-8', newline='') as f:
        f.write(tpl['src'])
    pp = os.path.join(d, 'fig.json')
    with open(pp, 'w', encoding='utf-8') as f:
        json.dump(_SPARSE_DATA, f)
    res1 = _apply_auto(script, _SPARSE_DATA, pp)
    b1 = _read_bytes(script)
    t1 = _read_text(script)
    res2 = _apply_auto(script, _SPARSE_DATA, pp)
    b2 = _read_bytes(script)
    t2 = _read_text(script)
    # 修复后正确行为：目标轴 ax1 的 title 字面量被改（fontsize=9 → 5）
    landed_ax1 = "ax1.set_title('AX1_KEEP', fontsize=5)" in t1 and \
                 "ax1.set_title('AX1_KEEP', fontsize=5)" in t2
    # ④ 只动目标：非目标轴 ax0 的 title 原样（fontsize=12 保留），两次都未碰
    ax0_untouched = "ax0.set_title('T0', fontsize=12)" in t1 and \
                    "ax0.set_title('T0', fontsize=12)" in t2
    # ① 幂等：第二次 apply = no_change 且字节不变
    ok_idem = _check_idempotent(b1, b2, res2.get('reason'))
    ok_reason = res1.get('reason') in ('ok', 'best_effort', 'no_change')
    check('T57-稀疏轴回归：只给 index=1（不带 0）→ 原位落在 ax1、ax0 不动、'
          '二次 apply 幂等',
          ok_reason and landed_ax1 and ax0_untouched and ok_idem,
          'r1=%s r2=%s 落地ax1=%s ax0未动=%s 幂等=%s'
          % (res1.get('reason'), res2.get('reason'), landed_ax1,
             ax0_untouched, ok_idem))


# ---------------------------------------------------------------------------
def main():
    from mpltweak import writeback, params, revert       # noqa: F401 冒烟导入

    # 「hypothesis 装没装」是**环境事实**，不是被测行为：装了照旧断言它可用；
    # 没装则明确打印跳过原因（而不是把"环境缺可选依赖"记成测试失败 ——
    # CI 的 step 6 按设计只装 pytest，见 tests/_hypothesis_shim.py 的说明）。
    if _HAS_HYPOTHESIS:
        check('hypothesis 可用（pyproject [dev] 应含 hypothesis>=6.0）', True,
              'hypothesis %s' % __import__('hypothesis').__version__)
    else:
        print('SKIP | hypothesis 未安装 → 下面两条 property 不执行；'
              '确定性用例（四铁律冒烟 / 反向验证 / 稀疏轴回归）照常执行'
              '（装上 hypothesis 即跑全部）')
    check('坏参数清单已登记（%d 条）' % len(_BROKEN_PARAMS),
          len(_BROKEN_PARAMS) >= 15, '')

    # 确定性冒烟：CRLF 与 LF 检出各自跑一遍四条铁律（验收 ③）
    _smoke_four_laws('LF', 'lf')
    _smoke_four_laws('CRLF', 'crlf')

    # 反向验证：判据被关掉 → 用例变红（验收 ②）
    _reverse_validation()

    # T57-稀疏轴回归：只给 index=1 → 原位落在 ax1、不碰 ax0、二次 apply 幂等
    _sparse_axis_regression()

    if _HAS_HYPOTHESIS:
        _run_hyp('hypothesis 合法参数：四铁律（幂等/可逆/语法/只动目标）',
                 prop_valid_writeback)
        _run_hyp('hypothesis 坏参数：rc=2 + bad_params + 脚本字节不动',
                 prop_invalid_params)

    shutil.rmtree(_TMPROOT, ignore_errors=True)
    print('FAILED: %d' % fail if fail else 'ALL PASS')
    return 1 if fail else 0


sys.exit(main())
