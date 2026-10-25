# -*- coding: utf-8 -*-
"""T18 回滚字节级验证：`revert --write` 必须把文件**逐字节**复原。

为什么单独一个套件
==================
CC 第三轮复核点出来的发版风险：**「回滚后是否逐字节恢复」我们从没测过** ——
既有测试只断言**字符串相等**，而字符串相等**不代表字节相等**：

* 同一个脚本，LF 与 CRLF 两种写法的 ``text.splitlines()`` 完全一样，``sha256`` 却不同；
* 带 UTF-8 BOM 的脚本，用 ``utf-8-sig`` 解码后字符串也完全一样，少了前 3 个字节；
* 落到用户身上就是**git diff 显示"整个文件都改了"**（几十行的图脚本变成全文件 diff），
  而所有断言都是绿的。

所以本套件的判据只有一条：**``hashlib.sha256(path.read_bytes())`` 与写回前相同**，
不看字符串（字符串相等只当附加提示打印，不算判据）。

覆盖（三形态 × 三场景 + 预览 + 反向有牙）
=========================================
形态：``lf``（纯 LF）/ ``crlf``（全文 CRLF）/ ``bom``（UTF-8 BOM + LF）；
场景：

1. **写回成功后 revert**：`apply --write` → `revert --write` ⇒ sha256 必须等于**原始**；
2. **写回失败自动回滚**：夹具里守卫写在 `fig.savefig(...)` **之后**，写回后的重跑必抛异常
   ⇒ 引擎自动回滚 ⇒ 原文件 sha256 **必须未变**（这才是"一个字节都没动"的真凭据）；
3. **``.tweak.bak`` 首份优先**：连写两次（两次参数不同）再 `revert --write`
   ⇒ 回到**最早那份**（第一次写回之前），不是第一次写回之后的中间态；
   同时核对固定名备份本身的 sha256 == 原始；
4. **预览不动字节**：`revert` 不加 `--write` ⇒ sha256 未变（预览是只读的）。

反向有牙（``--only mutation``，默认跑）
=====================================
把 `src/mpltweak/revert.py` 的 ``_atomic_restore`` **变异**成"只恢复文本、不恢复换行/BOM"
（``raw.decode('utf-8-sig').replace('\\r\\n', '\\n').encode('utf-8')``），拷到临时目录的
副本树上跑同一组断言 ⇒ **必须判红**。变异只作用在 ``%TEMP%`` 的副本里，仓库不动一个字节。
这一步同时说明判据不是空转：如果哪天有人把 revert 改回"解成字符串再写回"，
`crlf` / `bom` 两种形态会立刻红。

用法
====
    python tests/test_revert_bytes.py                    # 全跑（含反向有牙）
    python tests/test_revert_bytes.py --only roundtrip    # 只跑某一组
    python tests/test_revert_bytes.py --forms crlf,bom    # 只跑某几形态
    python tests/test_revert_bytes.py --tree <另一棵树>    # 换被测树（变异档内部用）
    python tests/test_revert_bytes.py --keep             # 保留工作目录（排查用）

口径与边界
==========
* 一律在 ``%TEMP%`` 的一次性目录里造夹具；``MPLTWEAK_HOME`` 也指到那里 —— **不碰用户家目录**；
* 后端钉 ``Agg``、语言钉 ``zh``（子进程 ``env=`` 显式给，宿主环境漏不进来）；
* 子进程一律 ``sys.executable -m mpltweak`` + ``PYTHONPATH=<树>/src``；
* 只碰 ``tests/test_revert_bytes.py``（不改 ``src/``、不 commit）。
"""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import traceback

# ---- 输出兜底（GBK 控制台 / 管道下 print 中文不崩）----
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


# ---- 语言 / 后端钉死：别让宿主 locale 决定结论 ----
os.environ.setdefault('MPLTWEAK_LANG', 'zh')
os.environ.setdefault('MPLBACKEND', 'Agg')

fail = 0


def check(name, cond, detail=''):
    global fail
    print(('PASS' if cond else 'FAIL') + ' | ' + _gbk_safe(name)
          + ((' | ' + _gbk_safe(detail)) if detail else ''))
    if not cond:
        fail += 1


HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)                       # <树> = .../mpltweak
WORKSPACE = os.path.dirname(ROOT)

#: 默认三种原始形态（验收要求的 LF / CRLF / BOM）。
#: ``bom_crlf``（BOM + CRLF 叠在一起）是更刁的第四种，默认**不跑**（省 20~30 s），
#: 想跑加 ``--forms lf,crlf,bom,bom_crlf``。
FORMS = ('lf', 'crlf', 'bom')

#: 单次 CLI 的墙钟上限（apply 内部还会重跑脚本，给足）
TIMEOUT = 420

# ---- 夹具：普通脚本（写回一定成功）----
#: 用 `fig.add_axes([...])` 建轴 + `ax.set_xlim(0, 10)` 字面量：xlim 请求走**原位通道**
#: （实测 LF/CRLF/BOM 三种都能 rc=0 落地），所以 revert 面对的是"只改了几个数字"的写回。
BASE_SRC = '''import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

fig = plt.figure(figsize=(4.0, 3.0))
ax = fig.add_axes([0.15, 0.15, 0.70, 0.70])
ax.set_xlim(0, 10)
ax.plot([1, 2, 3], [3, 1, 2])
ax.set_title("demo")
fig.savefig("demo.png", dpi=60)
'''

# ---- 夹具：写回后必跑不起来的脚本（触发引擎自动回滚）----
#: 关键：守卫放在 `fig.savefig(...)` **之后**。块通道的插入点是 `savefig` 之前、
#: 原位通道直接改字面量 —— 两边都会被 `savefig` 之后这两条守卫抓到（第一版把守卫写在
#: savefig 之前，块写回落在守卫后面 ⇒ 守卫看不见改动 ⇒ 白白 rc=0，等于没测）。
GUARD_SRC = '''import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

fig = plt.figure(figsize=(4.0, 3.0))
ax = fig.add_axes([0.15, 0.15, 0.70, 0.70])
ax.set_xlim(0, 10)
ax.plot([1, 2, 3], [3, 1, 2])
ax.set_title("demo")
fig.savefig("demo.png", dpi=60)
if ax.get_xlim() != (0.0, 10.0):
    raise RuntimeError("xlim guard: 写回把 xlim 改了，这个脚本跑不起来")
if ax.get_position().x0 != 0.15:
    raise RuntimeError("pos guard: 写回把位置改了，这个脚本跑不起来")
'''

#: 写回请求的**字段值**（参数文件一律由 ``describe -o`` 生成再改这些字段 —— 见 `_params_of`；
#: 手搓最小 payload 会被 0.1.18 的参数校验判 `bad_params`：`axes[k].pos` 必须是真实值）
OVER_XLIM = {'xlim': [2.0, 8.0]}
OVER_XLIM2 = {'xlim': [1.0, 9.0]}
OVER_POS = {'pos': [0.35, 0.15, 0.70, 0.70]}


# ---------------------------------------------------------------------------
# §1 临时根与子进程封装
# ---------------------------------------------------------------------------
_TMPROOT = None


def _ensure_tmp_writable():
    """临时根：优先系统 temp，不可写就退回仓库内（子进程继承 TEMP/TMP/TMPDIR）。"""
    global _TMPROOT
    base = tempfile.gettempdir()
    root = os.path.join(base, '.mpltweak_revert_bytes_%d' % os.getpid())
    try:
        os.makedirs(root, exist_ok=True)
        probe = os.path.join(root, '.w')
        open(probe, 'w').close()
        os.unlink(probe)
    except OSError:
        root = os.path.join(HERE, '.tmp_revert_bytes_%d' % os.getpid())
        os.makedirs(root, exist_ok=True)
    _TMPROOT = root
    os.environ['TEMP'] = root
    os.environ['TMP'] = root
    os.environ['TMPDIR'] = root
    tempfile.tempdir = root
    return root


def _pin_env(tree, home):
    """CLI 子进程环境。``env=`` 必须**显式钉住**后端（hygiene 棘轮按 AST 溯源到这里，
    函数体里出现 ``MPLBACKEND='Agg'`` 即判 pinned）。

    ``PYTHONPATH`` 指到**被测树**的 ``src``：``--tree`` 换树（变异档）就是靠这条生效。
    ``MPLTWEAK_HOME`` 指到本次一次性目录 —— 不碰用户家目录。
    """
    env = dict(os.environ, MPLBACKEND='Agg', MPLTWEAK_LANG='zh',
               PYTHONIOENCODING='utf-8', PYTHONUTF8='1')
    env['PYTHONPATH'] = os.path.join(tree, 'src')
    env['MPLTWEAK_HOME'] = home
    if _TMPROOT:
        env['MPLCONFIGDIR'] = os.path.join(_TMPROOT, '.mplconfig')
        env['TMP'] = env['TEMP'] = env['TMPDIR'] = _TMPROOT
    env.pop('MPLTWEAK_SANDBOX_ROOT', None)
    return env


def _decode_out(raw):
    for enc in ('utf-8', 'gbk', 'gb18030'):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode('utf-8', 'replace')


def _run_cli(tree, argv, cwd, home, timeout=TIMEOUT):
    """跑一次 ``python -m mpltweak <argv>`` → (rc, stdout, stderr)。超时 rc=-9。"""
    cmd = [sys.executable, '-m', 'mpltweak'] + list(argv)
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout,
                           env=_pin_env(tree, home), cwd=cwd)
    except subprocess.TimeoutExpired:
        return (-9, '', 'CLI 超时（%ss）' % timeout)
    return (r.returncode, _decode_out(r.stdout), _decode_out(r.stderr))


def _run_py(tree, script, cwd, home, timeout=TIMEOUT):
    """直接跑一个脚本（用来先证明夹具基线是能跑的）。"""
    try:
        r = subprocess.run([sys.executable, script], capture_output=True,
                           timeout=timeout, env=_pin_env(tree, home), cwd=cwd)
    except subprocess.TimeoutExpired:
        return (-9, '', '脚本超时（%ss）' % timeout)
    return (r.returncode, _decode_out(r.stdout), _decode_out(r.stderr))


def _extract_json(stdout):
    """从 ``--json`` 的 stdout 里抽对象（前导杂讯容错）。"""
    s = (stdout or '').strip()
    i = s.find('{')
    if i < 0:
        return None
    try:
        return json.loads(s[i:])
    except ValueError:
        for line in reversed(s.splitlines()):
            line = line.strip()
            if line.startswith('{'):
                try:
                    return json.loads(line)
                except ValueError:
                    continue
    return None


# ---------------------------------------------------------------------------
# §2 字节级工具（判据全部建在这上面：**不读文本**）
# ---------------------------------------------------------------------------
def _sha256(path):
    """文件字节指纹。判据只用它 —— 字符串相等不算证据。"""
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(65536), b''):
            h.update(chunk)
    return h.hexdigest()


def _shape(path):
    """字节形态（给人看的证据，不进判据）：CRLF 个数 / 是否有 BOM / 大小。"""
    with open(path, 'rb') as f:
        raw = f.read()
    return {'bytes': len(raw),
            'crlf': raw.count(b'\r\n'),
            'lf_only': raw.count(b'\n') - raw.count(b'\r\n'),
            'bom': raw[:3] == b'\xef\xbb\xbf'}


def _write_form(path, text, form):
    """按形态落盘：lf / crlf / bom（BOM+LF）/ bom_crlf（BOM+CRLF）。"""
    body = text.replace('\r\n', '\n')
    if form == 'crlf' or form == 'bom_crlf':
        body = body.replace('\n', '\r\n')
    data = body.encode('utf-8')
    if form in ('bom', 'bom_crlf'):
        data = b'\xef\xbb\xbf' + data
    with open(path, 'wb') as f:
        f.write(data)


def _write_params(dirpath, name, payload):
    p = os.path.join(dirpath, name)
    with open(p, 'w', encoding='utf-8') as f:
        json.dump(payload, f)
    return p


def _preflight(tree, home):
    """被测树能不能用？——先问一句再开跑。

    为什么必要（实测踩过）：队友正在改 `src/` 时，`python -m mpltweak` 可能**瞬间**失败
    （ImportError / IndentationError，0.4 s 就返回 rc=2、没有 JSON）。这时
    "写回没发生 → sha 未变 → 回滚场景全绿"会**假绿一整组**。所以先确认 CLI 活着：
    跑得动才继续，跑不动就当场红、并说清"树正在被编辑，重跑"。

    **不能只问 `--version`**（实测踩过）：`cli.py` 是懒加载的，`--version` 根本不 import
    `revert`，所以把 `revert.py` 写坏了它照样 rc=0（我第一版前置闸就这么漏的）。
    这里改成跑 ``revert <不存在的脚本> --json``：它会真的 import 整条链路
    （revert → writeback → params → messages），健康的树必然回 `rc=2` +
    `error_code=file_not_found`。
    """
    ghost = os.path.join(home, 'no_such_script_%d.py' % os.getpid())
    rc, out, err = _run_cli(tree, ['revert', ghost, '--json'], home, home, timeout=180)
    payload = _extract_json(out) or {}
    code = payload.get('error_code')
    tail = ((err or '').strip().splitlines() or [''])[-1]
    return (rc == 2 and code == 'file_not_found',
            'rc=%s code=%s | %s' % (rc, code, str(tail)[:160]))


def _case_dir(name):
    d = os.path.join(_TMPROOT, '%s_%s_%d' % (name, time.strftime('%H%M%S'),
                                            int(time.time() * 1000) % 1000))
    os.makedirs(d, exist_ok=True)
    os.makedirs(os.path.join(d, 'home'), exist_ok=True)
    return d


#: 参数文件缓存（key = 夹具种类）：**一律用 ``describe -o`` 生成本尊参数文件**，再要动的字段。
#: 不手搓最小 payload 的原因（实测踩过）：0.1.18 起 `axes[k].pos` 必须是真实值，
#: 手搓成 `{"index":0,"xlim":[...]}` 会被判 `bad_params`（"axes[0].pos 是 null"），
#: 于是"写回成功"根本不会发生、后面的字节断言全部变成空转。
_PARAMS_CACHE = {}

#: 自验开关（**只在自验/变异档用，CI 与日常跑一律不设**）：
#:   ``once``   —— 第一次 describe 直接失败（模拟 verify 实测过的 1/5 偶发失败），验重试路径
#:   ``always`` —— 两次都失败，验"两次都失败 ⇒ 照旧 FAIL 且记录两次 rc/stderr"
#: 只作用于 ``kind == 'base'``；读到就算用掉（避免影响后续形态）。
_FLAKY_ENV = 'MPLTWEAK_REVERT_BYTES_FLAKY_DESCRIBE'
_FLAKY_STATE = {'used': False}


def _flaky_arm(kind):
    """自验注入：返回 1（只坏第一次）/ 2（次次坏）/ 0（不注入）。"""
    mode = (os.environ.get(_FLAKY_ENV) or '').strip().lower()
    if not mode or kind != 'base' or _FLAKY_STATE['used']:
        return 0
    if mode in ('once', '1'):
        _FLAKY_STATE['used'] = True
        return 1
    if mode in ('always', '2'):
        return 2
    return 0


def _params_of(tree, kind, script, dirpath, home, timeout=TIMEOUT):
    """``describe -o`` 取本尊参数（按夹具种类缓存）→ ``(payload|None, diag)``。

    **describe 会偶发失败**（verify 在两臂 zh 臂上实测 1/5 次：同一棵树 en 臂绿、直跑两次也绿
    ⇒ 间歇性，不是树的确定性缺陷）。它在 CI 3.12 腿上会跑，所以这里**重试一次**——
    但**不许静默重试**：重试成功要打一行 ``RETRY | ...``（带第一次的 rc 与 stderr 尾），
    两次都失败时 ``diag`` 里留两轮记录（rc / stderr 尾 / 解释器路径）供报告与 FAIL 文案用。
    """
    if kind in _PARAMS_CACHE:
        return (_PARAMS_CACHE[kind], {'cached': True, 'attempts': []})
    out_params = os.path.join(dirpath, 'from_describe_%s.json' % kind)
    flaky = _flaky_arm(kind)
    attempts = []
    payload = None
    for n in (1, 2):
        # 上一轮可能留下半份文件：先删掉，免得"残留被当成本次成功"
        if os.path.isfile(out_params):
            try:
                os.unlink(out_params)
            except OSError:
                pass
        if flaky and (flaky == 2 or n == 1):
            rc, err = 2, '（自验注入：%s=1 模拟 describe 第一次失败）' % _FLAKY_ENV
            tail = err[-300:]
        else:
            rc, out, err = _run_cli(tree, ['describe', script, '-o', out_params],
                                    dirpath, home, timeout)
            tail = (err or '').strip()[-300:]
        ok = rc == 0 and os.path.isfile(out_params)
        if ok:
            try:
                with open(out_params, 'r', encoding='utf-8') as f:
                    payload = json.load(f)
            except (OSError, ValueError) as e:
                ok, payload = False, None
                tail = ('参数文件读不出来：%s；' % e.__class__.__name__) + tail[-200:]
        attempts.append({'n': n, 'rc': rc, 'ok': ok, 'stderr_tail': tail})
        if ok:
            break
        if n == 1:
            print('RETRY | describe 第 1 次失败（rc=%s），重试一次 | stderr_tail=%r'
                  % (rc, tail[:200]))
    diag = {'kind': kind, 'script': os.path.basename(script),
            'python': sys.executable, 'attempts': attempts,
            'retried': len(attempts) > 1, 'flaky': bool(flaky)}
    if payload is not None:
        if diag['retried']:
            print('RETRY | describe 重试后**成功**（第 2 次 rc=%s）| 首次 rc=%s stderr_tail=%r'
                  % (attempts[-1]['rc'], attempts[0]['rc'],
                     (attempts[0]['stderr_tail'] or '')[:200]))
        _PARAMS_CACHE[kind] = payload
    return (payload, diag)


def _describe_why(rec):
    """把 ``describe_failed`` 的诊断拼成一句话（进 FAIL 文案，别只留在 json 里）。"""
    dg = rec.get('describe') or {}
    parts = []
    for a in dg.get('attempts') or []:
        parts.append('第%s次 rc=%s stderr_tail=%r'
                     % (a.get('n'), a.get('rc'), (a.get('stderr_tail') or '')[:200]))
    py = dg.get('python') or '?'
    return 'describe_failed（py=%s；%s）' % (
        py, '；'.join(parts) if parts else '无记录')


def _payload(kind_params, axes_over=None):
    """在 describe 的本尊参数上改**第 0 根轴**的若干字段（其余字段保持与代码一致）。"""
    payload = json.loads(json.dumps(kind_params))          # deepcopy
    axes = payload.get('axes') or []
    if not axes:
        return None
    axes[0].update(axes_over or {})
    return payload


def _apply(tree, script, dirpath, home, payload, tag='p'):
    pj = _write_params(dirpath, '%s.json' % tag, payload)
    rc, out, err = _run_cli(tree, ['apply', script, '--params', pj,
                                   '--write', '--json'], dirpath, home)
    j = _extract_json(out) or {}
    f0 = (j.get('files') or [{}])[0] or {}
    return {'rc': rc, 'json': j, 'file': f0, 'stderr': err,
            'sha': _sha256(script), 'shape': _shape(script)}



def _revert(tree, script, dirpath, home, write=True, first=False):
    argv = ['revert', script, '--json'] + (['--first'] if first else []) \
        + (['--write'] if write else [])
    rc, out, err = _run_cli(tree, argv, dirpath, home)
    j = _extract_json(out) or {}
    return {'rc': rc, 'json': j, 'stderr': err,
            'sha': _sha256(script), 'shape': _shape(script)}


# ---------------------------------------------------------------------------
# §3 三个场景（每个都返回"可指证"的读数）
# ---------------------------------------------------------------------------
def case_roundtrip(tree, form, timeout=TIMEOUT):
    """写回成功 → revert --write ⇒ sha256 逐字节复原。"""
    d = _case_dir('rt_' + form)
    home = os.path.join(d, 'home')
    sp = os.path.join(d, 'demo.py')
    _write_form(sp, BASE_SRC, form)
    rec = {'case': 'roundtrip', 'form': form, 'sha_in': _sha256(sp),
           'shape_in': _shape(sp)}
    base, dg = _params_of(tree, 'base', sp, d, home, timeout)
    if base is None:
        rec.update({'error': 'describe_failed', 'describe': dg,
                    'restored': False, 'wrote': False})
        return rec
    ap = _apply(tree, sp, d, home, _payload(base, OVER_XLIM), tag='xlim')
    rec.update({'apply_rc': ap['rc'], 'apply_reason': ap['file'].get('reason'),
                'apply_applied': ap['file'].get('applied'),
                'apply_style': ap['file'].get('style'),
                'apply_err': (ap['file'].get('err') or '')[:160],
                'sha_written': ap['sha'], 'shape_written': ap['shape']})
    rec['wrote'] = (ap['sha'] != rec['sha_in'])
    rv = _revert(tree, sp, d, home, write=True)
    rec.update({'revert_rc': rv['rc'], 'revert_changed': rv['json'].get('changed'),
                'revert_written': rv['json'].get('written'),
                'sha_out': rv['sha'], 'shape_out': rv['shape'],
                'revert_stderr': (rv['stderr'] or '').strip()[-300:]})
    rec['restored'] = (rv['sha'] == rec['sha_in'])
    return rec


def case_rollback(tree, form, timeout=TIMEOUT):
    """写回失败（写后跑不起来）→ 引擎自动回滚 ⇒ 原文件 sha256 未变。"""
    d = _case_dir('rb_' + form)
    home = os.path.join(d, 'home')
    sp = os.path.join(d, 'guard.py')
    _write_form(sp, GUARD_SRC, form)
    rec = {'case': 'rollback', 'form': form, 'sha_in': _sha256(sp),
           'shape_in': _shape(sp)}
    # 先证明夹具基线能跑（不然"写回后跑不起来"分不清是谁的问题）
    brc, bout, berr = _run_py(tree, sp, d, home)
    rec.update({'baseline_rc': brc, 'baseline_stderr': (berr or '').strip()[-200:]})
    base, dg = _params_of(tree, 'guard', sp, d, home, timeout)
    if base is None:
        rec.update({'error': 'describe_failed', 'describe': dg,
                    'unchanged': False, 'refused_all': False})
        return rec
    tried = []
    # 每条请求都要真的"写进去再跑起来"（一次失败写回 = 2~3 次脚本重跑，约 10 s），
    # 所以只在 lf 上把两条通道（块 / 原位）都试一遍，其余形态只试 xlim 那一条 ——
    # **形态**才是本场景的变量，通道覆盖不必乘上形态数。
    variants = [('xlim', OVER_XLIM)]
    if form == 'lf':
        variants.append(('pos', OVER_POS))
    for tag, over in variants:
        # 每次都用干净副本重来（上一次可能已经写过 / 回滚过）
        _write_form(sp, GUARD_SRC, form)
        ap = _apply(tree, sp, d, home, _payload(base, over), tag=tag)
        tried.append({'tag': tag, 'rc': ap['rc'],
                      'reason': ap['file'].get('reason'),
                      'applied': ap['file'].get('applied'),
                      'error_code': ap['json'].get('error_code'),
                      'err': (ap['file'].get('err') or '')[:160],
                      'sha': ap['sha'], 'shape': ap['shape'],
                      'stderr_tail': (ap['stderr'] or '').strip()[-400:]})
    rec['tried'] = tried
    rec['sha_out'] = _sha256(sp)
    rec['shape_out'] = _shape(sp)
    rec['unchanged'] = all(t['sha'] == rec['sha_in'] for t in tried) \
        and rec['sha_out'] == rec['sha_in']
    rec['refused_all'] = all(t['rc'] != 0 and not t['applied'] for t in tried)
    return rec


def case_first_backup(tree, form, timeout=TIMEOUT):
    """连写两次再 revert ⇒ 回到**最早那份**（首份优先），并核对固定名备份本身。"""
    d = _case_dir('fb_' + form)
    home = os.path.join(d, 'home')
    sp = os.path.join(d, 'demo.py')
    _write_form(sp, BASE_SRC, form)
    rec = {'case': 'first_backup', 'form': form, 'sha_in': _sha256(sp)}
    base, dg = _params_of(tree, 'base', sp, d, home, timeout)
    if base is None:
        rec.update({'error': 'describe_failed', 'describe': dg,
                    'back_to_original': False,
                    'fixed_bak_is_original': False, 'back_to_first_write': False})
        return rec
    first = _apply(tree, sp, d, home, _payload(base, OVER_XLIM), tag='p1')
    rec.update({'apply1_rc': first['rc'],
                'apply1_reason': first['file'].get('reason'),
                'apply1_err': (first['file'].get('err') or '')[:160],
                'sha_after1': first['sha']})
    second = _apply(tree, sp, d, home, _payload(base, OVER_XLIM2), tag='p2')
    rec.update({'apply2_rc': second['rc'],
                'apply2_reason': second['file'].get('reason'),
                'apply2_err': (second['file'].get('err') or '')[:160],
                'sha_after2': second['sha']})
    bakdir = os.path.join(d, '.tweak_params')
    fixed = os.path.join(bakdir, 'demo.tweak.bak')
    rec['bak_files'] = sorted(os.listdir(bakdir)) if os.path.isdir(bakdir) else []
    rec['fixed_bak'] = os.path.basename(fixed) if os.path.isfile(fixed) else None
    rec['fixed_bak_sha'] = _sha256(fixed) if os.path.isfile(fixed) else None
    rec['fixed_bak_is_original'] = (rec['fixed_bak_sha'] == rec['sha_in'])
    rv = _revert(tree, sp, d, home, write=True)
    rec.update({'revert_rc': rv['rc'], 'revert_first': rv['json'].get('first'),
                'revert_fallback': rv['json'].get('fallback'),
                'revert_backup': os.path.basename(str(rv['json'].get('backup'))),
                'sha_out': rv['sha'], 'shape_out': rv['shape'],
                'revert_stderr': (rv['stderr'] or '').strip()[-300:]})
    rec['back_to_original'] = (rv['sha'] == rec['sha_in'])
    rec['back_to_first_write'] = (rv['sha'] == rec['sha_after1'])
    return rec


def _pid_backups(bakdir, stem='demo'):
    """目录里的 `<stem>.tweak.<pid>.bak` 列表（不排 mtime，只列名字）。"""
    try:
        names = sorted(os.listdir(bakdir))
    except OSError:
        return []
    return [n for n in names if n.startswith(stem + '.tweak.') and n.endswith('.bak')
            and n[len(stem) + 7:-4].isdigit()]


def case_first_flag(tree, form, timeout=TIMEOUT):
    """``revert --first``（最早 pid 副本）与 H2 兜底（固定名备份被删 ⇒ 自动退回最早副本）。

    这两条是**同一条代码路径**（"哪一份才算最早"）的两个入口，且都落在"逐字节复原"上，
    所以判据仍然是 ``sha256(read_bytes())``。

    **区分力从哪来**：只留一份 pid 副本时，"取最早"和"取唯一"分不开。所以这里在备份目录里
    **种一份 mtime 最新的 pid 副本**（内容 = 当前写回后的版本）：

    * ``--first`` 若错取"最新"，拿回来的就是**当前内容** ⇒ 文件不变、sha != 原件 ⇒ 红；
    * 默认回退（H2）若错取"最新"，同样红。

    种副本只是往 ``.tweak_params`` 里放一个名字/内容都合规的文件（revert 只按
    ``<stem>.tweak.<digits>.bak`` 匹配再按 mtime 排序），不改被测逻辑。
    """
    import re as _re

    d = _case_dir('ff_' + form)
    home = os.path.join(d, 'home')
    sp = os.path.join(d, 'demo.py')
    _write_form(sp, BASE_SRC, form)
    rec = {'case': 'first_flag', 'form': form, 'sha_in': _sha256(sp)}
    base, dg = _params_of(tree, 'base', sp, d, home, timeout)
    if base is None:
        rec.update({'error': 'describe_failed', 'describe': dg,
                    'h2_back_to_original': False,
                    'first_back_to_original': False})
        return rec

    # ① 写一次 → 固定名备份 = 原件，同时生出第一份 pid 副本（= 原件）
    a1 = _apply(tree, sp, d, home, _payload(base, OVER_XLIM), tag='p1')
    rec.update({'apply1_rc': a1['rc'], 'sha_after1': a1['sha'],
                'apply1_reason': a1['file'].get('reason')})
    bakdir = os.path.join(d, '.tweak_params')
    fixed = os.path.join(bakdir, 'demo.tweak.bak')
    rec['fixed_exists'] = os.path.isfile(fixed)
    rec['bak_files_after_apply1'] = sorted(os.listdir(bakdir)) if os.path.isdir(bakdir) else []

    # ② 种一份"最新"的 pid 副本（内容 = 写回后的版本），再删掉固定名备份
    plant = os.path.join(bakdir, 'demo.tweak.999999999.bak')
    shutil.copy2(sp, plant)
    rec['planted_sha'] = _sha256(plant)
    pids = _pid_backups(bakdir)
    oldest = None
    if pids:
        oldest = min([os.path.join(bakdir, n) for n in pids], key=os.path.getmtime)
        rec['oldest_pid'] = os.path.basename(oldest)
        rec['oldest_pid_sha'] = _sha256(oldest)
    if os.path.isfile(fixed):
        os.remove(fixed)
    rec['fixed_deleted'] = not os.path.isfile(fixed)

    # ③ 分支 A：**不带 --first**，固定名备份已删 ⇒ H2 兜底退回最早 pid 副本
    rvA = _revert(tree, sp, d, home, write=True)
    rec.update({'h2_rc': rvA['rc'], 'h2_fallback': rvA['json'].get('fallback'),
                'h2_first': rvA['json'].get('first'),
                'h2_changed': rvA['json'].get('changed'),
                'h2_sha': rvA['sha'],
                'h2_back_to_original': rvA['sha'] == rec['sha_in'],
                'h2_stderr': (rvA['stderr'] or '').strip()[-200:]})

    # ④ 再写一次（让当前文件 != 原件），继续测 **--first**
    a2 = _apply(tree, sp, d, home, _payload(base, OVER_XLIM2), tag='p2')
    rec.update({'apply2_rc': a2['rc'], 'sha_after2': a2['sha']})
    if os.path.isfile(fixed):
        os.remove(fixed)
    shutil.copy2(sp, plant)                       # "最新"副本跟着更新
    rec['planted_sha2'] = _sha256(plant)
    rvB = _revert(tree, sp, d, home, write=True, first=True)
    rec.update({'first_rc': rvB['rc'], 'first_payload': rvB['json'].get('first'),
                'first_changed': rvB['json'].get('changed'),
                'first_sha': rvB['sha'],
                'first_back_to_original': rvB['sha'] == rec['sha_in'],
                'first_backup': os.path.basename(str(rvB['json'].get('backup'))),
                'first_stderr': (rvB['stderr'] or '').strip()[-200:]})
    return rec


def case_preview(tree, form, timeout=TIMEOUT):
    """预览（不加 --write）不许动字节；顺带记一条**已知口径**（不进判据）。

    已知口径：``revert`` 判断"要不要回退"用的是**解码后的文本**（``cur_text == bak_text``
    ⇒ 直接报 `changed=false`、什么都不做），而真正恢复走的是**字节**。所以"文本一样、
    只有换行/BOM 不同"这种状态它认为无事可做（实测：把当前文件换成"备份的文本但用 LF 写"，
    `revert --write` 报 `changed=false`，字节不会回到备份那样）。
    这条**不当判据**（当前实现如此，"文本没变"也确实可以叫没有可回退的改动），
    只作为观察值记进 results.json，报告里如实写。
    """
    d = _case_dir('pv_' + form)
    home = os.path.join(d, 'home')
    sp = os.path.join(d, 'demo.py')
    _write_form(sp, BASE_SRC, form)
    rec = {'case': 'preview', 'form': form}
    base, dg = _params_of(tree, 'base', sp, d, home, timeout)
    if base is None:
        rec.update({'error': 'describe_failed', 'describe': dg,
                    'preview_is_readonly': False})
        return rec
    ap = _apply(tree, sp, d, home, _payload(base, OVER_XLIM), tag='xlim')
    rec['sha_written'] = ap['sha']
    rv = _revert(tree, sp, d, home, write=False)
    rec.update({'revert_rc': rv['rc'], 'revert_written': rv['json'].get('written'),
                'sha_after_preview': rv['sha'],
                'preview_is_readonly': rv['sha'] == rec['sha_written']})
    # ---- 观察（不进判据）：文本 == 备份、字节 != 备份 时 revert 怎么办 ----
    bak = os.path.join(d, '.tweak_params', 'demo.tweak.bak')
    if os.path.isfile(bak):
        with open(bak, 'rb') as f:
            text_bak = f.read().decode('utf-8-sig', 'replace')
        with open(sp, 'wb') as f:      # 文本与备份相同，但统一按 LF 写（并去掉 BOM）
            f.write(text_bak.replace('\r\n', '\n').encode('utf-8'))
        rec['textsame_sha_in'] = _sha256(sp)
        rv2 = _revert(tree, sp, d, home, write=True)
        rec['textsame_revert_changed'] = rv2['json'].get('changed')
        rec['textsame_sha_out'] = rv2['sha']
        rec['textsame_back_to_backup'] = (rv2['sha'] == _sha256(bak))
    return rec


# ---------------------------------------------------------------------------
# §4 反向有牙：把 revert 变异成"只恢复文本"，断言必须红
# ---------------------------------------------------------------------------
#: 变异锚点（在 `_atomic_restore` 里，读到备份字节之后那一句）
_MUT_ANCHOR = "    with open(backup, 'rb') as f:\n        raw = f.read()\n"
#: 变异体：解成字符串（utf-8-sig 顺手吞掉 BOM）再按 LF 编码 —— 正是"字符串相等就以为对了"的写法
_MUT_LINE = ("    raw = raw.decode('utf-8-sig').replace('\\r\\n', '\\n')"
             ".encode('utf-8')  # 变异：只恢复文本、不恢复换行/BOM\n")


def _mutate_tree(tree, seconds=None):
    """把 ``<tree>/src`` 拷到临时目录并变异 ``revert._atomic_restore`` → (mut_tree, why)。"""
    src = os.path.join(tree, 'src')
    if not os.path.isdir(src):
        return (None, 'no_src:%s' % src)
    mut = os.path.join(_TMPROOT, 'mut_%d' % int(time.time() * 1000))
    shutil.copytree(src, os.path.join(mut, 'src'))
    rp = os.path.join(mut, 'src', 'mpltweak', 'revert.py')
    try:
        with open(rp, 'r', encoding='utf-8') as f:
            text = f.read()
    except OSError as e:
        return (None, 'read_failed:%s' % e.__class__.__name__)
    i = text.find('def _atomic_restore(')
    if i < 0:
        return (None, 'anchor_missing:_atomic_restore')
    j = text.find('os.replace(tmp, script)', i)
    if j < 0:
        return (None, 'anchor_missing:os.replace')
    body = text[i:j]
    if _MUT_ANCHOR not in body:
        return (None, 'anchor_missing:read_backup_bytes')
    body = body.replace(_MUT_ANCHOR, _MUT_ANCHOR + _MUT_LINE, 1)
    with open(rp, 'w', encoding='utf-8') as f:
        f.write(text[:i] + body + text[j:])
    return (mut, '')


def _run_child(tree, forms, timeout=1200):
    """以子进程跑"本文件的 roundtrip 组"，返回 (rc, stdout)。变异档用。"""
    cmd = [sys.executable, os.path.abspath(__file__), '--tree', tree,
           '--only', 'roundtrip', '--forms', ','.join(forms), '--quiet']
    env = _pin_env(tree, os.path.join(_TMPROOT, 'child_home'))
    try:
        os.makedirs(env['MPLTWEAK_HOME'], exist_ok=True)
    except OSError:
        pass
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout, env=env,
                           cwd=os.path.dirname(os.path.abspath(__file__)))
    except subprocess.TimeoutExpired:
        return (-9, '')
    return (r.returncode, _decode_out(r.stdout))


def case_mutation(tree, forms=('crlf', 'bom'), timeout=1200):
    """变异树必须判红（判据有牙）。返回读数 + 变异树路径。"""
    rec = {'case': 'mutation', 'forms': list(forms)}
    mut, why = _mutate_tree(tree)
    rec['mut_tree'] = mut
    rec['mutate_why'] = why
    if mut is None:
        return rec
    rec['child_rc'], child_out = _run_child(mut, forms, timeout=timeout)
    rec['child_tail'] = (child_out or '').strip()[-600:]
    rec['child_failed_lines'] = [ln.strip() for ln in (child_out or '').splitlines()
                                 if ln.strip().startswith('FAIL')]
    rec['caught_sha'] = any('逐字节复原' in ln for ln in rec['child_failed_lines'])
    # "有红 ≠ 被抓住"：写成别的断言红（比如写回没成功）不算判据有牙 —— 必须**那一条**红
    rec['caught'] = (rec['child_rc'] != 0
                     and rec['caught_sha'])
    shutil.rmtree(mut, ignore_errors=True)
    return rec


# ---------------------------------------------------------------------------
# §5 主流程
# ---------------------------------------------------------------------------
def _sha_row(name, rec):
    fmt = {'lf': 'LF  ', 'crlf': 'CRLF', 'bom': 'BOM ', 'bom_crlf': 'BOM+CRLF'}[name]
    if rec['case'] == 'roundtrip':
        return ('| 写回后 revert | %s | `%s` | `%s` | `%s` | %s |'
                % (fmt, rec['sha_in'][:12], rec['sha_written'][:12],
                   rec['sha_out'][:12], 'OK' if rec['restored'] else '**MISMATCH**'))
    if rec['case'] == 'rollback':
        sha_out = rec.get('sha_out', '')[:12]
        return ('| 失败自动回滚 | %s | `%s` | —（未落盘）— | `%s` | %s |'
                % (fmt, rec['sha_in'][:12], sha_out,
                   'OK' if rec['unchanged'] else '**CHANGED**'))
    if rec['case'] == 'first_backup':
        return ('| 连写两次后 revert（首份优先） | %s | `%s` | `%s` | `%s` | %s |'
                % (fmt, rec['sha_in'][:12], rec['sha_after2'][:12],
                   rec.get('sha_out', '')[:12],
                   'OK' if rec['back_to_original'] else '**NOT-ORIGINAL**'))
    if rec['case'] == 'preview':
        return ('| 预览（不加 --write） | %s | — | `%s` | `%s` | %s |'
                % (fmt, rec['sha_written'][:12], rec['sha_after_preview'][:12],
                   'OK' if rec['preview_is_readonly'] else '**CHANGED**'))
    return ''


def _one_group(group, tree, forms, timeout):
    """跑一组场景，返回 [(form, rec)]（每个 rec 带 ``seconds``，耗时进报告）。"""
    fn = {'roundtrip': case_roundtrip, 'rollback': case_rollback,
          'first': case_first_backup, 'first_flag': case_first_flag,
          'preview': case_preview}[group]
    out = []
    for form in forms:
        t0 = time.time()
        try:
            rec = fn(tree, form, timeout=timeout)
        except BaseException as e:                             # noqa: BLE001 - 兜底
            rec = {'case': group, 'form': form,
                   'error': '%s:%s' % (type(e).__name__, str(e)[:200])}
            if os.environ.get('MPLTWEAK_DEBUG'):
                traceback.print_exc()
        rec.setdefault('seconds', round(time.time() - t0, 1))
        out.append((form, rec))
    return out


def _assert_group(group, rows):
    """把读数变成断言（每组 1~3 条），并打印证据行。"""
    if group == 'roundtrip':
        ok_apply = [f for f, r in rows
                    if r.get('apply_rc') == 0 and r.get('wrote') is True]
        check('写回成功（每个形态都真的改了字节）',
              len(ok_apply) == len(rows),
              '落地形态=%s / 全部=%s%s'
              % (ok_apply, [f for f, _ in rows],
                 '' if len(ok_apply) == len(rows) else
                 '（失败原因：%s）' % '；'.join(
                     '%s:%s' % (f, _describe_why(r)
                                if (r.get('error') == 'describe_failed')
                                else (r.get('apply_reason') or r.get('error') or '?'))
                     for f, r in rows if f not in ok_apply)))
        bad = [(f, r) for f, r in rows if not r.get('restored')]
        check('**revert --write ⇒ sha256 逐字节复原**（不是字符串相等）',
              not bad,
              ('全部 %d 形态一致' % len(rows)) if not bad else
              '；'.join('%s: %s→%s（期望 %s）'
                       % (f, r.get('sha_written', '')[:8], r.get('sha_out', '')[:8],
                          r.get('sha_in', '')[:8]) for f, r in bad))
        # 形态本身没被写回"悄悄规范化"（BOM 还在、CRLF 还在）—— 这是给人看的旁证
        shape_bad = []
        for f, r in rows:
            sin, sout = r.get('shape_out') or {}, r.get('shape_in') or {}
            if not r.get('restored'):
                continue
            if sin.get('bom') != sout.get('bom') or sin.get('crlf') != sout.get('crlf'):
                shape_bad.append('%s: %s→%s' % (f, sin, sout))
        check('复原后字节**形态**也一致（BOM / CRLF 计数）', not shape_bad,
              '；'.join(shape_bad) if shape_bad else 'BOM 与 CRLF 计数逐形态相同')

    elif group == 'rollback':
        bad = [(f, r) for f, r in rows if not r.get('unchanged')]
        check('**写回失败自动回滚 ⇒ 原文件 sha256 未变**',
              not bad,
              ('全部 %d 形态未变' % len(rows)) if not bad else
              '；'.join('%s: %s→%s' % (f, (r.get('sha_in') or '')[:8],
                                     (r.get('sha_out') or '')[:8]) for f, r in bad))
        notfailed = [(f, r) for f, r in rows if not r.get('refused_all')]
        check('回滚场景确实"写回失败"（rc!=0 且 applied=false）', not notfailed,
              '；'.join('%s: rc=%s applied=%s'
                       % (f, [t.get('rc') for t in (r.get('tried') or [])],
                          [t.get('applied') for t in (r.get('tried') or [])])
                       for f, r in notfailed)
              or '请求都被拒且一个字节未落盘（lf 上两条通道各试一次）')
        # 上面那条只说明"失败了"，还得说明失败是**写完之后的脚本**造成的（不是 CLI 本身炸了）
        notours = [(f, r) for f, r in rows
                   if not any(('guard' in (t.get('stderr_tail') or ''))
                              or ('guard' in (t.get('err') or ''))
                              for t in (r.get('tried') or []))]
        check('失败原因确实是"写回后的脚本跑不起来"（夹具守卫抛的错，不是 CLI 自己炸）',
              not notours,
              '；'.join('%s: %s' % (f, (r.get('tried') or [{}])[0].get('error_code'))
                       for f, r in notours)
              or '每条失败的 stderr 里都有夹具抛的 guard 文案')
        base_bad = [(f, r) for f, r in rows if r.get('baseline_rc') != 0]
        check('夹具基线本身能跑（证明"跑不起来"是写回造成的）', not base_bad,
              '；'.join('%s: rc=%s' % (f, r.get('baseline_rc')) for f, r in base_bad)
              or '%d 形态基线 rc=0' % len(rows))

    elif group == 'first':
        notwrote = [(f, r) for f, r in rows
                    if r.get('sha_after2') == r.get('sha_in')
                    or r.get('apply1_rc') != 0 or r.get('apply2_rc') != 0]
        check('两次写回都真的落地（否则"回到原件"是空转）', not notwrote,
              '；'.join('%s: rc1=%s rc2=%s sha=%s'
                       % (f, r.get('apply1_rc'), r.get('apply2_rc'),
                          (r.get('sha_after2') or '')[:8]) for f, r in notwrote)
              or '%d 形态两次写回都改了字节' % len(rows))
        bad = [(f, r) for f, r in rows if not r.get('back_to_original')]
        check('**连写两次后 revert ⇒ 回到最早那份**（首份优先）', not bad,
              ('全部 %d 形态回到原件' % len(rows)) if not bad else
              '；'.join('%s: out=%s 原始=%s 第一次写回=%s'
                       % (f, (r.get('sha_out') or '')[:8], (r.get('sha_in') or '')[:8],
                          (r.get('sha_after1') or '')[:8]) for f, r in bad))
        bak_bad = [(f, r) for f, r in rows if not r.get('fixed_bak_is_original')]
        check('固定名 `.tweak.bak` 本身 = 原件（首份优先、不被二次写回覆盖）',
              not bak_bad,
              '；'.join('%s: bak=%s 原始=%s' % (f, (r.get('fixed_bak_sha') or '')[:8],
                                              (r.get('sha_in') or '')[:8])
                       for f, r in bak_bad) or '%d 形态 bak sha == 原件 sha' % len(rows))
        mid_bad = [(f, r) for f, r in rows if r.get('back_to_first_write')]
        check('回退结果**不是**第一次写回后的中间态（分清"最早"与"上一次"）',
              not mid_bad, '；'.join(f for f, _ in mid_bad)
              or '%d 形态都没退回中间态' % len(rows))

    elif group == 'first_flag':
        prem = [(f, r) for f, r in rows
                if not (r.get('apply1_rc') == 0 and r.get('apply2_rc') == 0
                        and r.get('sha_after2') != r.get('sha_in')
                        and r.get('fixed_exists') and r.get('fixed_deleted'))]
        check('前提：两次写回都真的落地 + 固定名备份曾存在且已被删', not prem,
              '；'.join('%s: rc1=%s rc2=%s fixed=%s/%s'
                       % (f, r.get('apply1_rc'), r.get('apply2_rc'),
                          r.get('fixed_exists'), r.get('fixed_deleted'))
                       for f, r in prem)
              or '%d 形态都满足（两次写回改了字节、固定名备份删得掉）' % len(rows))
        # 区分力旁证：种的那份"最新"副本必须与最早那份内容不同，否则这条测不出"取最早"
        nodisc = [(f, r) for f, r in rows
                  if r.get('oldest_pid_sha') == r.get('planted_sha')]
        check('前提：最早 pid 副本与"最新"副本内容不同（否则"取最早"没区分力）', not nodisc,
              '；'.join(f for f, _ in nodisc) or '两者 sha 不同')
        bad = [(f, r) for f, r in rows if not r.get('h2_back_to_original')]
        check('**H2 兜底：固定名备份被删 ⇒ 默认 revert 自动退回最早 pid 副本，逐字节复原**',
              not bad,
              ('全部 %d 形态：fallback=%s'
               % (len(rows), sorted(set(str(r.get('h2_fallback')) for _, r in rows))))
              if not bad else
              '；'.join('%s: fallback=%s sha=%s（期望 %s）'
                       % (f, r.get('h2_fallback'), (r.get('h2_sha') or '')[:8],
                          (r.get('sha_in') or '')[:8]) for f, r in bad))
        bad2 = [(f, r) for f, r in rows
                if not (r.get('first_back_to_original') and r.get('first_payload') is True)]
        check('**`revert --first`：逐字节回到最早那份**（payload first=true）', not bad2,
              ('全部 %d 形态：backup=%s'
               % (len(rows), sorted(set(str(r.get('first_backup')) for _, r in rows))))
              if not bad2 else
              '；'.join('%s: first=%s backup=%s sha=%s（期望 %s）'
                       % (f, r.get('first_payload'), r.get('first_backup'),
                          (r.get('first_sha') or '')[:8], (r.get('sha_in') or '')[:8])
                       for f, r in bad2))

    elif group == 'preview':
        notwrote = [(f, r) for f, r in rows
                    if r.get('sha_written') == r.get('sha_in')]
        check('预览场景的前提成立（写回真的改了字节）', not notwrote,
              '；'.join(f for f, _ in notwrote) or '%d 形态都改过字节' % len(rows))
        bad = [(f, r) for f, r in rows if not r.get('preview_is_readonly')]
        check('预览（revert 不加 --write）不改一个字节', not bad,
              ('全部 %d 形态未动' % len(rows)) if not bad else
              '；'.join('%s: %s→%s' % (f, (r.get('sha_written') or '')[:8],
                                     (r.get('sha_after_preview') or '')[:8])
                       for f, r in bad))
        # 已知口径（**钉现状、不修**：船长 2026-10-25 裁决"只钉现状不修，改动需建单评估"）。
        # 口径本身：`revert` 判断"要不要回退"看**解码文本**（utf-8-sig 会吞掉 BOM），
        # 执行恢复才看**字节** ⇒ 只有 BOM 差异这一种"文本相同、字节不同"会被当成"已一致"。
        # 钉成断言的理由：以后谁"顺手修好"或"顺手改坏"这条，这里会红，口径变化必须被看见。
        bom_rows = [(f, r) for f, r in rows
                    if f == 'bom' and 'textsame_revert_changed' in r]
        if bom_rows:
            check('已知口径（钉现状·改动需建单）：只有 BOM 差异时 revert 报 changed=false 且不补回 BOM',
                  all(r.get('textsame_revert_changed') is False
                      and r.get('textsame_back_to_backup') is False
                      for _, r in bom_rows),
                  '；'.join('%s: changed=%s back_to_backup=%s'
                           % (f, r.get('textsame_revert_changed'),
                              r.get('textsame_back_to_backup')) for f, r in bom_rows))
        # 反向钉子：换行（CRLF）差异**必须**被识别 —— 谁把"要不要回退"的判据改成
        # 规范化换行后再比文本，这条会红。
        crlf_rows = [(f, r) for f, r in rows
                     if f == 'crlf' and 'textsame_revert_changed' in r]
        if crlf_rows:
            check('已知口径（反向钉）：CRLF 差异必须被 revert 识别（changed=true 且逐字节复原）',
                  all(r.get('textsame_revert_changed') is True
                      and r.get('textsame_back_to_backup') is True
                      for _, r in crlf_rows),
                  '；'.join('%s: changed=%s back_to_backup=%s'
                           % (f, r.get('textsame_revert_changed'),
                              r.get('textsame_back_to_backup')) for f, r in crlf_rows))
        for f, r in rows:
            if 'textsame_revert_changed' in r and f not in ('bom', 'crlf'):
                print('INFO | 观察（%s）：文本与备份相同但字节不同时 revert 报 changed=%s，'
                      '字节回到备份=%s' % (f, r.get('textsame_revert_changed'),
                                         r.get('textsame_back_to_backup')))



def _main(argv):
    ap = argparse.ArgumentParser(prog='test_revert_bytes.py')
    ap.add_argument('--tree', default=None, help='被测树（默认 = 本文件所在仓库）')
    ap.add_argument('--only', default='all',
                    choices=('all', 'roundtrip', 'rollback', 'first', 'first_flag', 'preview',
                             'mutation'),
                    help='只跑某一组（变异档内部用 roundtrip）')
    ap.add_argument('--forms', default=None,
                    help='逗号分隔的形态（lf,crlf,bom,bom_crlf）；默认全跑')
    ap.add_argument('--timeout', type=int, default=TIMEOUT)
    ap.add_argument('--keep', action='store_true', help='保留临时工作目录')
    ap.add_argument('--quiet', action='store_true',
                    help='只打断言（变异档的子进程用）')
    args = ap.parse_args(argv)

    tree = os.path.abspath(args.tree or ROOT)
    forms = tuple(f.strip() for f in args.forms.split(',')) if args.forms else FORMS
    _ensure_tmp_writable()

    if not args.quiet:
        print('=' * 72)
        print('T18 回滚字节级验证：revert 必须 sha256 逐字节复原')
        print('  被测树 : %s' % tree)
        print('  形态   : %s' % ', '.join(forms))
        print('  临时根 : %s' % _TMPROOT)
        print('=' * 72)

    groups = ('roundtrip', 'rollback', 'first', 'first_flag',
              'preview') if args.only == 'all' \
        else (() if args.only == 'mutation' else (args.only,))
    # ---- 前置闸：被测树得能用（否则下面每一组都可能假绿：什么都没发生 ⇒ sha 未变）----
    home0 = os.path.join(_TMPROOT, 'preflight_home')
    os.makedirs(home0, exist_ok=True)
    alive, why_alive = _preflight(tree, home0)
    check('被测树可用（`revert` 链路能 import：不存在的脚本必回 rc=2/file_not_found）',
          alive,
          '%s（不可用多半是队友正在改 src/，稍后重跑；本次不拿"什么都没发生"当通过）'
          % why_alive)
    if not alive:
        print('=' * 72)
        print('ABORT：被测树当前不可用，后面的字节比对没有意义。')
        if not args.keep:
            shutil.rmtree(_TMPROOT, ignore_errors=True)
        return 1
    t0 = time.time()
    results = []
    for group in groups:
        rows = _one_group(group, tree, forms, args.timeout)
        results += [r for _, r in rows]
        _assert_group(group, rows)
        if not args.quiet:
            print('-' * 72)

    if args.only in ('all', 'mutation'):
        # ---- 反向有牙：变异树必须红 ----
        mut_forms = ('crlf', 'bom') if 'bom' in forms else ('crlf',)
        rec = case_mutation(tree, forms=mut_forms, timeout=max(args.timeout * 4, 1200))
        results.append(rec)
        if rec.get('mut_tree') is None:
            check('变异档：能造出变异树（锚点找得到）', False, rec.get('mutate_why'))
        else:
            check('**反向有牙：把 revert 改成"只恢复文本"⇒ 断言必红**',
                  rec.get('caught') is True,
                  '子进程 rc=%s；红了 %d 条：%s'
                  % (rec.get('child_rc'),
                     len(rec.get('child_failed_lines') or []),
                     '；'.join(l[:90] for l in
                              (rec.get('child_failed_lines') or [])[:3])))
        if not args.quiet:
            print('=' * 72)

    # ---- 三形态 × 三场景 sha256 对照表（报告直接用这张）----
    if not args.quiet:
        print('sha256 对照表（前 12 位；完整值见 --json 落盘）')
        print('| 场景 | 形态 | 写回前 | 写回后 | 复原后 | 结论 |')
        print('|------|------|--------|--------|--------|------|')
        order = {'roundtrip': 0, 'rollback': 1, 'first_backup': 2, 'preview': 3}
        for r in sorted([x for x in results if x.get('case') in order],
                        key=lambda x: (order[x['case']],
                                       FORMS.index(x['form'])
                                       if x.get('form') in FORMS else 9)):
            row = _sha_row(r['form'], r)
            if row:
                print(row)
        print('-' * 72)
        print('总耗时 %.1fs（临时根：%s）' % (time.time() - t0, _TMPROOT))

    outdir = os.environ.get('MPLTWEAK_REVERT_BYTES_OUT',
                            os.path.join(WORKSPACE, '_review', 'revert_bytes'))
    summary = {'tree': tree, 'forms': list(forms), 'only': args.only,
               'seconds': round(time.time() - t0, 1),
               'checks': {'failed': fail}, 'results': results,
               'tmp_root': _TMPROOT}
    try:
        os.makedirs(outdir, exist_ok=True)
        with open(os.path.join(outdir, 'results.json'), 'w', encoding='utf-8') as f:
            json.dump(summary, f, ensure_ascii=False, indent=1)
        if not args.quiet:
            print('产出：%s' % os.path.join(outdir, 'results.json'))
    except OSError:
        pass

    if not args.keep:
        shutil.rmtree(_TMPROOT, ignore_errors=True)
    if not args.quiet:
        print('=' * 72)
    if fail:
        if not args.quiet:
            print('FAILED（%d 条）' % fail)
        return 1
    if not args.quiet:
        print('ALL PASS')
    return 0


def _dispatch(argv):
    try:
        return _main(argv[1:])
    except SystemExit:
        raise
    except BaseException:                                      # noqa: BLE001
        traceback.print_exc()
        return 1


if __name__ == '__main__':
    sys.exit(_dispatch(sys.argv))
