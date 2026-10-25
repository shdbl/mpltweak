# -*- coding: utf-8 -*-
"""发版前**本地多版本矩阵**：3.9 / 3.10 / 3.11 / 3.12 × 中文 / 英文 各跑一遍 `pytest -q`。

## 这个工具为什么存在

CI 的 8 条腿（4 个解释器 × 2 个系统）只有在**推上去之后**才跑；本地过去要证明
"3.9 上也能跑" 只能靠手搓 venv + 手记命令，于是同一件事每次的做法都不一样：

* 解释器从哪来 —— 有 `py -3.9` 的、有 conda 的、有 uv 的，混着用；
* 装什么 —— `-e .` / `-e .[dev]` / `-e .[dev,qt]` 三种口径，跳过的东西不同
  （少了 `[dev]` 就没有 hypothesis，3 个 property 套件会**静默降级**成 SKIP）；
* 环境变量 —— `MPLTWEAK_LANG` 忘了钉，同一份断言在中文 Windows 上绿、在 CI 上红
  （0.1.8 就是这么红的）；`QT_QPA_PLATFORM` 忘了钉 offscreen，GUI 冒烟会去抢焦点；
* 临时目录 —— `%TEMP%\\sandbox_t9_tests` 是 `test_sandbox` 的固定工作区，
  **两臂并行就会互踩**（0.1.15 发版时实测），必须每臂前清一次。

本工具把这些口径固化成一条命令：**uv 拉解释器 → 仓外建 venv → 装 `-e .[extras]`
→ 每个语言臂清临时目录后跑 `pytest -q` → 汇总 passed/skipped/failed 与耗时**。

## 用法

    python tools/preflight_matrix.py --list                 # 只打印将要跑的臂（默认动作）
    python tools/preflight_matrix.py --pythons 3.12         # 只跑 3.12（zh + en 两臂，约 25 分钟）
    python tools/preflight_matrix.py --pythons 3.12 --langs zh
    python tools/preflight_matrix.py --dry-run              # 只打印将要执行的命令，不落盘
    python tools/preflight_matrix.py --clean                # 跑完删掉根目录下的 venv 与日志

退出码（与仓库其它工具同口径）：0 = 每臂都绿；1 = 有臂红 / 装不上 / 找不到 uv；
2 = 用法错（参数不认识、仓库不像仓库、根目录落在仓库里）。**任何路径问题只打印人话，不抛栈。**

## 目录口径（重要）

* **一律在仓外**：默认根目录 `%TEMP%\\mpltweak_matrix`（可用 `--root` 或环境变量
  `MPLTWEAK_MATRIX_ROOT` 改）。落到仓库里会被工具**拒绝**（rc=2）—— 一次跑差不多
  1~2 GB 的 venv，进仓就是灾难。
* 每个解释器一个 venv：`<root>/venv-3.12/`；每臂一份日志：`<root>/logs/3.12-zh.log`。
* `--clean` 删的只有 `<root>` 这一个目录（删前会打印绝对路径，并核对它确实在仓外、
  名字确实是 `mpltweak_matrix`）。

## 它证明了什么、没证明什么

* **证明**：这棵工作树在某个解释器 + 某套已安装依赖下，`pytest -q` 全量绿，
  且 `[dev,qt]` 都装得上（= 新增的 `pyproject` extra 没写坏）。
* **没证明**：没跑 `test_real_window`（真开窗，会抢焦点）与 `test_corpus_invariants`
  （仓外语料）—— 两者本来就不在 `SUITES` 里，这里也不引入。真窗口的排期见
  `CONTRIBUTING.md`「发版前必须人工跑一次的那套」。
* 每个语言臂跑前/跑后各记一次「树身证」（`src` + `tests` + `tools` 下 `*.py` 的
  文件数 / 总字节 / 总行数 / 逐文件 sha256）。**臂内树身动了 = 有人并发改树**，
  这时 passed/failed 不能归因给解释器，工具会把该臂标成 `drifted` 并在结论里说出来。

## 「已知环境副作用」与「真失败」必须分开（0.1.18 加）

矩阵 venv 用 `uv venv --python 3.X` 建 -> **base 解释器是 uv 自带的标准 CPython**，
这台 base 上没有 matplotlib（matplotlib 只装在矩阵 venv 里）。而 `test_journey_wheel`
把 wheel 装进 `python -m venv --system-site-packages` 的**子 venv** 里再真跑 CLI ——
`--system-site-packages` 继承的是 **base** 的 site-packages，不是矩阵 venv 的
-> 子 venv 里 `import matplotlib` 必然 `ModuleNotFoundError` -> `check` / `describe`
`rc=2`（`collect_failed`）-> 该套件在这个矩阵里**结构上不可能绿**，而同一棵树在
conda 里直跑 ALL PASS。

这不是产品失败（真实用户 `pip install mpltweak` 会带进 matplotlib），也不是"可以
静默跳过的噪音" —— 它是一条**可测的环境事实**，所以这里做成三件事：

1. **预检**（`probe_child_venv_deps`）：每台解释器建一个一次性的
   `--system-site-packages` 子 venv，问它"能不能 import matplotlib" ——
   这是**可测的事实**，不是靠失败文本猜；
2. **分类**（`classify_arm`）：只当「失败行**只有** journey_wheel」**且**「预检确认
   子 venv 里没有 matplotlib」两条同时成立时，才把该臂记成 `known_env`；
   别的套件红、或预检说 matplotlib 在，一律照旧算**红**；
3. **显式呈现**：汇总表多一列「分类」（`ok` / `known_env` / `red`），结论里单列
   「已知环境副作用（不计红）」一节并说出**是哪台 base 解释器、缺什么**；
   `summary.json` 也有 `known_env` 字段。套件照跑、失败行照打印，一个字都不藏。

`--strict-known-env` 可把这类副作用重新当红（给"我要在能用 matplotlib 的 base 上
较真跑一遍"的场合用；默认关闭，否则每次矩阵都注定红一条，结论没人看）。
"""

import argparse
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

#: 仓库根（tools/ 的上一级）
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
#: 默认矩阵根目录：**仓外**的系统临时目录（用完可删）
DEFAULT_ROOT = os.environ.get('MPLTWEAK_MATRIX_ROOT',
                             os.path.join(tempfile.gettempdir(), 'mpltweak_matrix'))
#: 默认解释器与语言臂
DEFAULT_PYTHONS = '3.9,3.10,3.11,3.12'
DEFAULT_LANGS = 'zh,en'
#: 默认 extras：与 CI 的 3.12 腿对齐（dev = pytest + hypothesis；qt = PyQt5）
DEFAULT_EXTRAS = 'dev,qt'
#: uv 的常见落点（PATH 之外先看这些；都没有就给人话）
UV_CANDIDATES = (
    r'C:\Users\xiang\.cherrystudio\bin\uv.exe',
    os.path.join(os.path.expanduser('~'), '.local', 'bin', 'uv.exe'),
    os.path.join(os.path.expanduser('~'), 'AppData', 'Roaming', 'uv', 'uv.exe'),
)
#: `test_sandbox` 的固定工作区（每臂前必须清：两臂共用会互踩）
SANDBOX_TMP_NAME = 'sandbox_t9_tests'
#: 「树身证」的扫描面
FP_DIRS = ('src', 'tests', 'tools')
FP_SKIP_DIRS = ('__pycache__', '.pytest_cache', '.hypothesis', '.tweak_params')
#: 测试自己会造临时目录（`tests/.tmp_t83/…`、`tests/.tmp_writeback_12345/…`），有的跑完不删。
#: 它们属于**跑的过程产物**，不是"有人动过源码" —— 不排掉就会把每一臂都误判成 drifted
#: （0.1.16 首跑实测：干净树也会因为 tests/.tmp_* 残留而 drifted=True）。
FP_SKIP_PREFIXES = ('.tmp_', 'tmp_')
#: 矩阵根目录的固定名字（`--clean` 只敢删这个名字）
ROOT_BASENAME = 'mpltweak_matrix'


def _say(msg=''):
    print(msg)


def _err(msg):
    sys.stderr.write('preflight_matrix: ' + msg + '\n')


def _is_inside(path, parent):
    p = os.path.normcase(os.path.abspath(path))
    q = os.path.normcase(os.path.abspath(parent))
    return p == q or p.startswith(q + os.sep)


# ---------------------------------------------------------------- uv / venv
def find_uv(explicit=None):
    """→ uv 可执行文件路径；找不到返回 None（调用方给人话并 rc=1）。"""
    for cand in ((explicit,) if explicit else ()):
        return cand if os.path.isfile(cand) else None
    env = os.environ.get('MPLTWEAK_UV')
    if env:
        return env if os.path.isfile(env) else None
    found = shutil.which('uv')
    if found:
        return found
    for cand in UV_CANDIDATES:
        if os.path.isfile(cand):
            return cand
    return None


def venv_python(venv):
    """venv 里的解释器路径（Windows: Scripts/python.exe；POSIX: bin/python）。"""
    if os.name == 'nt':
        return os.path.join(venv, 'Scripts', 'python.exe')
    return os.path.join(venv, 'bin', 'python')


def clean_sandbox_tmp():
    """清 `<TEMP>/sandbox_t9_tests`（**只**删这一个名字，删前核对父目录是系统临时目录）。"""
    tmp = tempfile.gettempdir()
    target = os.path.join(tmp, SANDBOX_TMP_NAME)
    base = os.path.basename(target)
    if base != SANDBOX_TMP_NAME or os.path.normcase(os.path.dirname(target)) != \
            os.path.normcase(os.path.abspath(tmp)):
        _err('临时目录路径异常，拒绝删除：%s' % target)
        return False
    if not os.path.isdir(target):
        _say('  [tmp] 无残留目录（干净）：%s' % target)
        return True
    try:
        shutil.rmtree(target, ignore_errors=True)
    except OSError as e:                      # 极端情况下删不掉：不抛栈，如实说
        _err('删不掉 %s（%s）—— 该臂结果可能受上一臂残留影响' % (target, e))
        return False
    ok = not os.path.isdir(target)
    _say('  [tmp] 已删除残留目录：%s（删后还在吗：%s）' % (target, '在' if not ok else '不在'))
    return ok


# ---------------------------------------------------------------- 树身证
def fingerprint(repo):
    """「树身证」：FP_DIRS 下 *.py 的文件数 / 总字节 / 总行数 / 逐文件 sha256。"""
    rows = {}
    total_bytes = 0
    total_lines = 0
    for top in FP_DIRS:
        root = os.path.join(repo, top)
        if not os.path.isdir(root):
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames
                           if d not in FP_SKIP_DIRS
                           and not d.startswith(FP_SKIP_PREFIXES)]
            for fn in sorted(filenames):
                if not fn.endswith('.py'):
                    continue
                p = os.path.join(dirpath, fn)
                rel = os.path.relpath(p, repo).replace('\\', '/')
                try:
                    with open(p, 'rb') as f:
                        raw = f.read()
                except OSError:
                    continue
                rows[rel] = hashlib.sha256(raw).hexdigest()
                total_bytes += len(raw)
                total_lines += raw.count(b'\n')
    return {'files': len(rows), 'bytes': total_bytes, 'newlines': total_lines,
            'rows': rows}


def fp_triple(fp):
    return (fp['files'], fp['bytes'], fp['newlines'])


def fp_drift(before, after):
    """→ (消失, 新增, 内容变化) 三个已排序列表。"""
    b, a = before['rows'], after['rows']
    return (sorted(set(b) - set(a)), sorted(set(a) - set(b)),
            sorted(k for k in set(a) & set(b) if a[k] != b[k]))


# ---------------------------------------------------------------- 执行
def run_streaming(cmd, cwd, env, log_path):
    """跑一条命令，stdout+stderr 直接进日志文件（**流式**，长任务也能 tail）。

    → (rc, 秒数)。命令本身不存在时不抛栈：返回 (127, 0.0)。
    """
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    t0 = time.time()
    try:
        with io.open(log_path, 'w', encoding='utf-8', errors='replace') as fh:
            fh.write('$ ' + ' '.join(cmd) + '\n')
            fh.write('# cwd=%s\n' % cwd)
            fh.write('#' * 70 + '\n')
            fh.flush()
            proc = subprocess.Popen(cmd, cwd=cwd, env=env, stdout=fh,
                                    stderr=subprocess.STDOUT)
            rc = proc.wait()
    except OSError as e:
        _err('起不来：%s（%s）' % (cmd[0], e))
        return 127, time.time() - t0
    return rc, time.time() - t0


_COUNT_KEYS = ('passed', 'skipped', 'failed', 'error', 'errors', 'xfailed',
               'xpassed', 'deselected', 'warnings')


def parse_counts(text):
    """从 pytest 末行抽计数（`89 passed, 5 skipped, 17 warnings in 719.32s`）。"""
    lines = [ln for ln in (text or '').strip().splitlines() if ln.strip()]
    last = lines[-1] if lines else ''
    counts = {}
    for key in _COUNT_KEYS:
        m = re.search(r'(\d+) %s' % key, last)
        if m:
            counts[key] = int(m.group(1))
    m = re.search(r'in ([\d.]+)s', last)
    if m:
        counts['pytest_seconds'] = float(m.group(1))
    return last, counts


def parse_failures(text):
    """抽 `FAILED ...` 行（红的时候这就是可读证据）。"""
    return [ln.strip() for ln in (text or '').splitlines()
            if ln.startswith('FAILED') or ln.startswith('ERROR')]


def _read_text(path):
    try:
        return io.open(path, encoding='utf-8', errors='replace').read()
    except OSError:
        return ''


def setup_venv(uv, py, root, repo, extras, env):
    """建 venv + 装 `-e .[extras]` → (venv 解释器路径, rc, 日志路径)。"""
    venv = os.path.join(root, 'venv-%s' % py)
    log = os.path.join(root, 'logs', '%s-install.log' % py)
    exe = venv_python(venv)
    if os.path.isfile(exe):
        _say('  [venv] 复用已有的 %s' % venv)
    else:
        _say('  [venv] 建 %s（uv venv --python %s --seed）' % (venv, py))
        rc, secs = run_streaming([uv, 'venv', '--python', py, '--seed', venv],
                                 repo, env, log)
        _say('  [venv] rc=%d  %.1fs' % (rc, secs))
        if rc != 0 or not os.path.isfile(exe):
            _err('venv 没建成 —— 日志尾巴：\n%s' % _read_text(log)[-1500:])
            return exe, (rc or 1), log
    spec = '-e'
    target = '.[%s]' % extras if extras else '.'
    _say('  [pip ] %s pip install %s %s' % (uv, spec, target))
    rc, secs = run_streaming([uv, 'pip', 'install', '--python', exe,
                              spec, target], repo, env, log)
    _say('  [pip ] rc=%d  %.1fs' % (rc, secs))
    if rc != 0:
        _err('装不上 —— 日志尾巴：\n%s' % _read_text(log)[-1500:])
    return exe, rc, log


def probe_versions(py_exe, repo, env):
    """问一下解释器与 matplotlib 的实际版本（写进汇总表，方便事后对照）。"""
    code = ("import sys, matplotlib;"
            "print('%d.%d.%d' % sys.version_info[:3]);"
            "print(matplotlib.__version__)")
    try:
        r = subprocess.run([py_exe, '-c', code], cwd=repo, env=env,
                           capture_output=True, text=True)
    except OSError:
        return None
    if r.returncode != 0:
        return None
    parts = [ln.strip() for ln in r.stdout.strip().splitlines() if ln.strip()]
    if len(parts) < 2:
        return None
    return {'python': parts[0], 'matplotlib': parts[1]}


# ---------------------------------------------------------------- 已知环境副作用
#: 需要"子 venv 里能 import matplotlib"的那一个套件（见模块 docstring 的对应一节）。
JOURNEY_SUITE = 'test_journey_wheel'
#: 预检用的子 venv 名字（在矩阵根目录下，`--clean` 一并删）
CHILD_PROBE_DIR = 'probe-child-venv'
#: 预检里"子 venv 有没有 matplotlib"的探针代码：只打印事实，不判红
_CHILD_PROBE_CODE = (
    "import sys\n"
    "print('base_prefix=' + sys.base_prefix)\n"
    "try:\n"
    "    import matplotlib\n"
    "    print('matplotlib=%s' % matplotlib.__version__)\n"
    "except Exception as e:\n"
    "    print('matplotlib=MISSING:%s: %s' % (type(e).__name__, e))\n"
)
#: 失败行里用来判"是不是这个套件"的键
_JOURNEY_FAIL_KEY = 'test_suite[%s]' % JOURNEY_SUITE


def probe_child_venv_deps(py_exe, repo, root, env):
    """预检：从**这台 venv 的 python** 建 `--system-site-packages` 子 venv，能不能 import matplotlib。

    为什么必须预检（而不是事后从失败文本猜）：`test_journey_wheel` 的失败签名
    （`collect_failed` / `ModuleNotFoundError: No module named 'matplotlib'`）在下面两种
    情形里**一模一样** ——
      (a) 环境口径问题：矩阵 venv 的 base 是 uv 自带 python，那里没有 matplotlib；
      (b) 真问题：产品 / 打包坏了，哪怕 matplotlib 在也跑不通。
    能把两者分开的只有一条**可测的事实**：这台 python 的子 venv 到底有没有 matplotlib。
    量它，不要猜它。

    产物落在 `<root>/probe-child-venv/`（**仓外**，`--clean` 会一起删）。建一次即可
    （每台解释器一份），所以成本约 3~6 秒/解释器。
    """
    probe_root = os.path.join(root, CHILD_PROBE_DIR)
    exe = venv_python(probe_root)
    log = os.path.join(root, 'logs', 'probe-child-venv.log')
    if not os.path.isfile(exe):
        rc, secs = run_streaming([py_exe, '-m', 'venv', '--system-site-packages', probe_root],
                                 repo, env, log)
        if rc != 0 or not os.path.isfile(exe):
            # 探不出来就不猜：返回 None，调用方保持"照旧算红"的行为（人去看日志）
            _say('  [env ] 子 venv 建不起来（rc=%d，%.1fs）-> 不做环境分类，红就是红' % (rc, secs))
            return None
    try:
        r = subprocess.run([exe, '-c', _CHILD_PROBE_CODE], cwd=repo, env=env,
                           capture_output=True, text=True, encoding='utf-8',
                           errors='replace')
    except OSError as e:
        _say('  [env ] 子 venv 探针起不来（%s）-> 不做环境分类，红就是红' % e)
        return None
    out = (r.stdout or '') + (r.stderr or '')
    base = ''
    mpl = ''
    for ln in out.splitlines():
        if ln.startswith('base_prefix='):
            base = ln.split('=', 1)[1].strip()
        elif ln.startswith('matplotlib='):
            mpl = ln.split('=', 1)[1].strip()
    ok = r.returncode == 0 and mpl and not mpl.startswith('MISSING')
    detail = ('子 venv（--system-site-packages）能 import matplotlib %s' % mpl if ok
              else '子 venv（--system-site-packages）里 %s（base=%s）'
                   % (mpl.replace('MISSING:', '') or '（探针没输出）', base or '?'))
    _say('  [env ] %s' % detail)
    return {'ok': ok, 'detail': detail, 'base': base, 'matplotlib': mpl}


def classify_arm(arm, probe, strict=False):
    """把「已知环境副作用」与「真失败」分开 → (kind, 一句话理由)。

    `kind ∈ {'ok', 'red', 'known_env'}`。**两条判据同时成立**才允许记 `known_env`：

      1. 该臂的失败行里**只有** journey_wheel 这一条套件（别的套件红 -> 真红，
         哪怕环境确实缺 matplotlib：那说明还有别的问题要人看）；
      2. 预检证据说明**这台解释器的子 venv 里没有 matplotlib** -> 该套件结构上不可能绿。

    预检拿不到（`probe is None`，例如子 venv 建不起来）-> **不猜**，保持红。
    `strict=True`（`--strict-known-env`）-> 一律保持红，把选择权交回人。
    """
    if arm.get('rc') is None:
        return 'red', '未跑（venv / 安装就失败了）'
    if arm.get('rc') == 0:
        return 'ok', ''
    if arm.get('drifted'):
        return 'red', '臂内树身变了 —— 结果不能归因给解释器'
    fails = list(arm.get('failures') or [])
    only_journey = bool(fails) and all(_JOURNEY_FAIL_KEY in f for f in fails)
    if strict:
        return 'red', '--strict-known-env：不承认环境副作用'
    if not only_journey:
        return 'red', '失败行里不止 %s（或没有失败行）' % JOURNEY_SUITE
    if probe is None:
        return 'red', '预检拿不到（这台解释器的子 venv 建不起来）-> 不猜，保持红'
    if probe.get('ok'):
        return 'red', ('预检说子 venv 里能 import matplotlib %s -> 这条红不能归给环境'
                       % probe.get('matplotlib'))
    return 'known_env', ('%s 要求「子 venv 里能 import matplotlib」，而本矩阵 venv 的 base '
                         '解释器（%s）没有它 -> 该套件结构上不可能绿'
                         % (JOURNEY_SUITE, probe.get('base') or '?'))



def run_arm(py_exe, py, lang, repo, root, fast_only=False):
    """一个语言臂：清临时目录 → 树身证 → pytest -q → 树身证 → 计数。"""
    arm = {'python_req': py, 'lang': lang, 'log': None, 'rc': None,
           'seconds': None, 'counts': {}, 'last': '', 'failures': [],
           'drifted': False, 'only_before': [], 'only_after': [], 'changed': [],
           'fp_before': None, 'fp_after': None,
           # 分类由 main() 里的 classify_arm 填（ok / red / known_env + 一句话理由）
           'kind': None, 'kind_why': ''}
    env = dict(os.environ)
    env.update({'MPLBACKEND': 'Agg', 'MPLTWEAK_LANG': lang,
                'PYTHONIOENCODING': 'utf-8', 'PYTHONUTF8': '1',
                'QT_QPA_PLATFORM': 'offscreen'})
    # 臂内不许有别的语言臂的残留：每臂前清一次
    clean_sandbox_tmp()
    before = fingerprint(repo)
    arm['fp_before'] = fp_triple(before)
    _say('  [fp] 跑前 files=%d bytes=%d newlines=%d' % fp_triple(before))
    log = os.path.join(root, 'logs', '%s-%s.log' % (py, lang))
    cmd = [py_exe, '-m', 'pytest', '-q']
    _say('  [run] MPLTWEAK_LANG=%s  %s' % (lang, ' '.join(cmd)))
    rc, secs = run_streaming(cmd, repo, env, log)
    text = _read_text(log)
    last, counts = parse_counts(text)
    arm.update({'rc': rc, 'seconds': round(secs, 1), 'counts': counts,
                'last': last, 'failures': parse_failures(text)[-20:], 'log': log})
    clean_sandbox_tmp()
    after = fingerprint(repo)
    arm['fp_after'] = fp_triple(after)
    only_b, only_a, changed = fp_drift(before, after)
    arm.update({'only_before': only_b, 'only_after': only_a, 'changed': changed,
                'drifted': bool(only_b or only_a or changed)})
    _say('  [fp] 跑后 files=%d bytes=%d newlines=%d' % fp_triple(after))
    if arm['drifted']:
        _say('  [fp] 臂内树身变了（并发改树）：消失 %s / 新增 %s / 内容变化 %s'
             % (only_b or '无', only_a or '无', changed or '无'))
    _say('  [res] rc=%d  %.1fs  %s' % (rc, secs, last or '(末行读不到)'))
    return arm


# ---------------------------------------------------------------- 主流程
def plan_lines(pythons, langs, root, extra_cmd=''):
    out = []
    out.append('矩阵根目录（仓外）：%s' % root)
    out.append('解释器：%s   语言臂：%s' % (' / '.join(pythons), ' / '.join(langs)))
    out.append('每个解释器：uv venv -> uv pip install -e .[extras]')
    for py in pythons:
        out.append('  * %s：venv-%s/，然后 %d 臂'
                   % (py, py, len(langs)))
        for lang in langs:
            out.append('    - MPLTWEAK_LANG=%-2s pytest -q   -> logs/%s-%s.log'
                       % (lang, py, lang))
    out.append('每臂前清：%s' % os.path.join(tempfile.gettempdir(), SANDBOX_TMP_NAME))
    out.append('每台解释器先预检子 venv 依赖：%s/（缺 matplotlib -> %s 记 known_env，'
               '不计红）' % (os.path.join(root, CHILD_PROBE_DIR), JOURNEY_SUITE))
    if extra_cmd:
        out.append(extra_cmd)
    return out


def render_summary(arms):
    """人读汇总表（passed/skipped/failed + 耗时 + **分类**）。

    「分类」列是与「rc」并列的证据，不是替代：rc 永远是 pytest 的真实退出码，
    `known_env` 只说明"这条红是环境口径决定的、与本棵树的产品行为无关"。
    """
    head = '| 解释器 | 语言 | rc | passed | skipped | failed | 耗时(s) | 树身 | 分类 |'
    sep = '|---|---|---:|---:|---:|---:|---:|---|---|'
    lines = [head, sep]
    for a in arms:
        c = a['counts']
        state = 'drifted' if a['drifted'] else 'stable'
        if a['rc'] is None:
            lines.append('| %s | %s | - | - | - | - | - | 未跑 | red |'
                         % (a['python_req'], a['lang']))
            continue
        lines.append('| %s | %s | %d | %s | %s | %s | %.1f | %s | %s |'
                     % (a['python_req'], a['lang'], a['rc'],
                        c.get('passed', '-'), c.get('skipped', '-'),
                        c.get('failed', '-'),
                        a['seconds'] or 0.0, state,
                        a.get('kind') or ('ok' if a['rc'] == 0 else 'red')))
    return lines


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog='preflight_matrix.py',
        description='发版前本地多版本矩阵：uv 建解释器 -> 仓外 venv -> '
                    '每个语言臂跑一次 pytest -q 并汇总。',
        epilog='目录一律在仓外（默认 %%TEMP%%\\mpltweak_matrix），用完 --clean 可删。')
    ap.add_argument('--pythons', default=DEFAULT_PYTHONS,
                    help='逗号分隔的解释器版本（默认 %s）' % DEFAULT_PYTHONS)
    ap.add_argument('--langs', default=DEFAULT_LANGS,
                    help='逗号分隔的 MPLTWEAK_LANG 臂（默认 %s）' % DEFAULT_LANGS)
    ap.add_argument('--extras', default=DEFAULT_EXTRAS,
                    help='安装用的 extras（默认 %s；给空串就是裸 -e .）' % DEFAULT_EXTRAS)
    ap.add_argument('--root', default=DEFAULT_ROOT,
                    help='矩阵根目录（仓外；默认 %%TEMP%%\\mpltweak_matrix 或 '
                         'MPLTWEAK_MATRIX_ROOT）')
    ap.add_argument('--repo', default=ROOT, help='被测仓库（默认本仓库）')
    ap.add_argument('--uv', default=None, help='uv 可执行文件路径（默认自动找）')
    ap.add_argument('--strict-known-env', action='store_true',
                    dest='strict_known_env',
                    help='不承认「已知环境副作用」（如矩阵 venv 的 base 里没有 '
                         'matplotlib -> journey_wheel 必红）：这类臂照旧算红')
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument('--list', action='store_true', help='只打印将要跑的臂（默认动作）')
    mode.add_argument('--dry-run', action='store_true', help='只打印将要执行的命令')
    mode.add_argument('--clean', action='store_true', help='删掉矩阵根目录（venv 与日志）')
    args = ap.parse_args(argv)

    repo = os.path.abspath(args.repo)
    root = os.path.abspath(args.root)
    pythons = [p.strip() for p in args.pythons.split(',') if p.strip()]
    langs = [l.strip() for l in args.langs.split(',') if l.strip()]

    if not pythons or not langs:
        _err('--pythons / --langs 不能是空的')
        return 2
    if not os.path.isfile(os.path.join(repo, 'pyproject.toml')):
        _err('这个目录不像 mpltweak 仓库（缺 pyproject.toml）：%s' % repo)
        return 2
    if _is_inside(root, repo):
        _err('矩阵根目录落在仓库里（会污染工作树，也会毁掉克隆体积）：%s\n'
             '     换一个仓外路径：--root D:\\somewhere\\mpltweak_matrix'
             % root)
        return 2

    if args.clean:
        if os.path.basename(root.rstrip('\\/')) != ROOT_BASENAME:
            _err('拒绝删除：%s 的名字不是 %r —— --clean 只敢删矩阵根目录本身'
                 % (root, ROOT_BASENAME))
            return 2
        if not os.path.isdir(root):
            _say('没有这个目录（已经干净了）：%s' % root)
            return 0
        _say('将删除：%s' % root)
        shutil.rmtree(root, ignore_errors=True)
        _say('删完了：存在吗 = %s' % os.path.isdir(root))
        return 0

    uv = find_uv(args.uv)
    _say('仓库      : %s' % repo)
    if uv is None:
        _say('uv        : (找不到)')
        _err('找不到 uv。这个工具靠 uv 拉独立解释器并建仓外 venv，没有它就做不了矩阵。\n'
             '     装一个：  winget install --id=astral-sh.uv -e   （或 pip install uv）\n'
             '     已经装了但不在 PATH：用 --uv 指过去，或设 MPLTWEAK_UV\n'
             '     本机已知落点：C:\\Users\\xiang\\.cherrystudio\\bin\\uv.exe')
        return 1
    _say('uv        : %s' % uv)
    _say('')
    for ln in plan_lines(pythons, langs, root,
                         'extras=%s' % (args.extras or '(无)')):
        _say(ln)
    _say('')
    if args.list:
        _say('（--list：只打印，不动手。真跑就去掉 --list。）')
        return 0

    if args.dry_run:
        env = dict(os.environ)
        for py in pythons:
            venv = os.path.join(root, 'venv-%s' % py)
            _say('[dry] %s venv --python %s --seed %s' % (uv, py, venv))
            _say('[dry] %s pip install --python %s -e .[%s]'
                 % (uv, venv_python(venv), args.extras))
            for lang in langs:
                _say('[dry] cd %s && MPLTWEAK_LANG=%s QT_QPA_PLATFORM=offscreen '
                     '%s -m pytest -q > %s'
                     % (repo, lang, venv_python(venv),
                        os.path.join(root, 'logs', '%s-%s.log' % (py, lang))))
        del env
        _say('（--dry-run：没有落盘、没有执行。）')
        return 0

    if not os.path.isdir(root):
        os.makedirs(root)

    env = dict(os.environ, MPLBACKEND='Agg', PYTHONIOENCODING='utf-8',
               PYTHONUTF8='1', QT_QPA_PLATFORM='offscreen')
    arms = []
    bad = []
    known_env = []
    t_all = time.time()
    for py in pythons:
        _say('=' * 66)
        _say('解释器 %s' % py)
        _say('=' * 66)
        exe, rc, _log = setup_venv(uv, py, root, repo, args.extras, env)
        if rc != 0 or not os.path.isfile(exe):
            bad.append('%s：venv / 安装失败（rc=%d）' % (py, rc))
            for lang in langs:
                arms.append({'python_req': py, 'lang': lang, 'rc': None,
                             'counts': {}, 'seconds': None, 'drifted': False,
                             'kind': 'red', 'kind_why': 'venv / 安装失败'})
            continue
        ver = probe_versions(exe, repo, env)
        if ver:
            _say('  [ver] python %s / matplotlib %s'
                 % (ver['python'], ver['matplotlib']))
        # 每台解释器探一次子 venv 的依赖（见模块 docstring 的「已知环境副作用」一节）
        probe = probe_child_venv_deps(exe, repo, root, env)
        for lang in langs:
            arm = run_arm(exe, py, lang, repo, root)
            arm['ver'] = ver
            arm['probe'] = probe
            kind, why = classify_arm(arm, probe, strict=args.strict_known_env)
            arm['kind'] = kind
            arm['kind_why'] = why
            arms.append(arm)
            if arm['drifted']:
                bad.append('%s / %s：臂内树身变了，结果不能归因给解释器'
                           % (py, lang))
            elif kind == 'known_env':
                known_env.append('%s / %s：%s' % (py, lang, why))
                _say('  [cls] 已知环境副作用（不计红）：%s' % why)
            elif kind != 'ok':
                bad.append('%s / %s：pytest rc=%d（%s）—— %s'
                           % (py, lang, arm['rc'], arm['last'] or '末行读不到', why))
        _say('')

    _say('=' * 66)
    _say('汇总')
    _say('=' * 66)
    table = render_summary(arms)
    for ln in table:
        _say(ln)
    _say('')
    _say('总墙钟：%.1f s（约 %.1f 分钟）' % (time.time() - t_all,
                                            (time.time() - t_all) / 60.0))
    summary = {'repo': repo, 'root': root, 'uv': uv, 'extras': args.extras,
               'pythons': pythons, 'langs': langs,
               'started_at': time.strftime('%Y-%m-%d %H:%M:%S'),
               'arms': arms, 'bad': bad, 'known_env': known_env}
    out_json = os.path.join(root, 'summary.json')
    with io.open(out_json, 'w', encoding='utf-8') as f:
        f.write(json.dumps(summary, ensure_ascii=False, indent=1))
    out_md = os.path.join(root, 'summary.md')
    with io.open(out_md, 'w', encoding='utf-8') as f:
        f.write('\n'.join(table) + '\n')
    _say('产物：%s' % out_json)
    _say('      %s' % out_md)
    _say('      %s' % os.path.join(root, 'logs') + '（每臂一份原始日志）')
    _say('')
    if known_env:
        _say('已知环境副作用（**不计红**；套件照跑、失败行在各自日志里，'
             '不是静默跳过）：')
        for k in known_env:
            _say('  - ' + k)
        _say('  重新当红跑一遍：加 --strict-known-env；'
             '换一台 base 里带 matplotlib 的解释器做矩阵就不会出现这条。')
        _say('')
    if bad:
        _say('结论：**红 / 不可归因**（%d 条）——' % len(bad))
        for b in bad:
            _say('  * ' + b)
        _say('  先看对应日志尾巴；红在某个套件上的话，单独跑它：'
             'python -m pytest -q -k <套件名>')
        return 1
    if known_env:
        _say('结论：每臂**除已知环境副作用外**都绿（分类见上表「分类」列；'
             'skipped 通常是没装 mcp / 平台不支持）。')
        return 0
    _say('结论：每臂都绿（passed/skipped 见上表；skipped 通常是没装 mcp / 平台不支持）。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
