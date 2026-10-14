# -*- coding: utf-8 -*-
"""首次体验**旅程测试**（t74）：从 **wheel** 装进干净 venv，再按**文档里写的命令**逐条真跑。

为什么要从 wheel 装（而不是 PYTHONPATH 指仓库源码）
----------------------------------------------------
本机与 CI 的 python 里 mpltweak 常是 **editable 安装**（`__editable__.mpltweak-*.pth`
把 `<仓库>/src` 塞进 `sys.path`）。直接 import 拿到的是**工作区源码**，于是
"改坏源码 → 测试变红"这条路会在别的机器上悄悄消失 —— 今晚两次假绿都源于 editable
遮蔽了被测副本。所以本套件先在干净 venv 里装 wheel，再**断言
`mpltweak.__file__` 落在 venv 的 site-packages 下**（teeth③ 反过来证明这条判据有区分力）。

它抓的是单模块测试抓不到的**接缝**
----------------------------------
① wheel 里到底有没有该有的文件（打包缺件类问题只能等发版才发现）；
② 文档命令表里写的选项，对相应子命令是否真的可解析（报告附录 B #7）；
③ `describe → apply` 的零改动不变量（报告附录 B #5）；
④ `apply --write` 只改**预期字段**（逐行 diff，不拿"有变化"当判据）+ `revert` 逐字节回原样；
⑤ P2-8：`pip install` 后命令不在 PATH 时，文档给的兜底指引是否真的存在、可复制、可跑。

零网络 / 不开窗
---------------
wheel 用 `--no-build-isolation` 本地构建；安装用 `--no-index`（**绝不联网**）；
全程 `MPLBACKEND=Agg`，只跑 CLI 子命令，不建任何窗口。

**已知缺口台账（KNOWN_NON_ASCII）**
----------------------------------
`revert` 在 `MPLTWEAK_LANG=en` 下仍打中文（`src/mpltweak/revert.py`，**不在本套件
inScope**）。照本项目"宁可登记例外、不软化判据"的规矩：台账**只准缩小** ——
出现台账外的非 ASCII ⇒ 判红；台账里的条目已修好 ⇒ 只提示、不判红（同 AMBIG_BASELINE）。

**红了怎么判（船长 2026-10-14 要求写进说明）**
--------------------------------------------
本套件跑的是**工作区**，所以开发期红**可能来自别人的在制品**（t74 实测：`cli.py` 被改到
一半 ⇒ `--help` NameError；`messages.py:1752` 的 en 半边把 `\n` 写成 `\\n` ⇒ en 下 help
打成一行 —— 两次都是**本套件抓到的真回归**，但根因都在 `src/`，属别的车道）。判据：

1. **先看开头的树指纹**（`HEAD` / `M` / `??` / `diff --stat`）—— 树在动就别急着下结论；
2. **跨语言两臂（zh/en）共同的红才认领**；只在单臂出现、或重跑不复现的，按**别人的在制品 /
   偶发**处理（t74 那次全量：zh 臂红 `test_agent_api`、en 臂红 `test_launch`，**不跨臂** ⇒ 不认领；
   `test_journey_wheel` 与 `test_mcp` **两臂都红** ⇒ 认领并查根因，结果两条都是真 bug）；
3. 认领之后再看是不是本套件自己的问题。

⇒ **权威用法 = 发布期在冻结树上跑**（t43/V4 收官门那种静止状态）。

跑法：``python tests/test_journey_wheel.py``（约 27 秒；已登记进 test_suites.py 的 SUITES）
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 工作产物放**仓库外**（MAINTENANCE §7 的约定：素材与工作脚本在 D:\Agent\deepseek\，
# 不在项目里）。这里还有一个实测原因：本机干净 venv 里的 python **写不进 %TEMP%**
# （见下面 _venv_env 的注释），所以工作目录必须落在仓库这一侧。
WORKSPACE = os.path.dirname(ROOT)

# GBK 控制台兜底（T13/T15 同款）：CI 是 UTF-8 永远绿，本地 cp936 下哪怕一个字符就能
# 把整套从 ALL PASS 打成 rc=1 的假红。test_hygiene 的 GBK 棘轮按这两行的存在判定。
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(errors='replace')
    except (AttributeError, ValueError, OSError):
        pass          # 被重定向成不支持 reconfigure 的对象时忽略即可


def _gbk_safe(s):
    s = str(s)
    enc = getattr(sys.stdout, 'encoding', None) or 'utf-8'
    try:
        return s.encode(enc, 'replace').decode(enc, 'replace')
    except (LookupError, UnicodeDecodeError):
        return s.encode('ascii', 'backslashreplace').decode('ascii')


def _p(line=''):
    print(_gbk_safe(line))


_fails = []


def check(name, cond, detail=''):
    mark = 'OK' if cond else 'FAIL'
    _p('%s | %s%s' % (mark, name, (' | ' + str(detail)) if detail else ''))
    if not cond:
        _fails.append(name)


# ---------------------------------------------------------------- 常量
DOC_FILES = ('skills/mpltweak/SKILL.md', 'README.md')
CMD_RE = re.compile(r'`mpltweak\s+([a-z][a-z0-9-]*)([^`]*)`')
OPT_RE = re.compile(r'--[a-z][a-z0-9-]*')
# 反例线索：SKILL.md:113 用这些词**故意**举了一条会失败的命令（`check … --timeout`）。
# 只认"表格行 / 围栏代码块"之后它本来就落选（那句是散文），这里再留一道闸：
# 万一哪天反例被搬进表格，也不会被当成"文档承诺这个选项可用"。
NEG_CUES = ('不认', '不支持', 'unrecognized')

# 台账：`命令/流` → 为什么 en 下仍有非 ASCII。**只准缩小**（见文件头）。
# 键必须精确到"哪条命令的哪个流"：否则"已修好"的提示会乱报。
# 2026-10-14：`revert` 成功路径已由 code-owner 的 t85 英文化 ⇒ 删掉 `revert-write/stdout`
# （en 下只剩 writeback 的**块标记哨兵**那一行 —— 它写进用户源码、必须保持 zh，由
# `revert.py` 的显式"不许 i18n"例外注释钉住，属合法例外，不进本台账）。
KNOWN_NON_ASCII = {}

SCRIPT_SRC = (
    'import matplotlib\n'
    'matplotlib.use("Agg")\n'
    'import matplotlib.pyplot as plt\n'
    '\n'
    'fig = plt.figure(figsize=(6, 4))\n'
    'ax = fig.add_axes([0.15, 0.15, 0.5, 0.5])\n'
    'ax.set_title("t", fontsize=12)\n'
    'fig.savefig("out.png")\n'
)


# ---------------------------------------------------------------- 基础设施
def _venv_env(wd, lang='en'):
    """venv 子进程的环境。

    **为什么显式指定 TEMP/TMP/TMPDIR**：本机实测（t74 取证）—— 干净 venv 里的
    python **用不了环境里的 %TEMP%**（`tempfile.gettempdir()` 会退到工作目录），
    而 base python 用得了；后果是 venv 的 pip 直接报
    `No usable temporary directory found`。把三者显式指到**仓库这一侧**的工作目录
    既绕开这个环境限制，又让产物落在我能清理的地方。CI 上无害。
    """
    tmp = os.path.join(wd, 'tmp')
    if not os.path.isdir(tmp):
        os.makedirs(tmp, exist_ok=True)
    return dict(os.environ, TEMP=tmp, TMP=tmp, TMPDIR=tmp, MPLBACKEND='Agg',
                PYTHONIOENCODING='utf-8', MPLTWEAK_LANG=lang)


def _pin(env):
    """把 ``MPLBACKEND`` **硬钉**成 ``Agg``（不是 setdefault —— 宿主投毒必须被覆盖）。

    本文件**唯一的子进程 env 出口**就是 `_run()`，它一律经这里过一道。这样写不只是
    为了正确，也是为了让 `tests/test_hygiene.py` 的"级联闸门"棘轮能**静态认出**这是
    一个闸门函数（`_local_func_verdict` 看函数体里有没有 ``env['MPLBACKEND'] = 'Agg'``）
    —— 否则它会 fail-closed 把我这些调用点算成 unresolved/unpinned，逼我去登记例外。
    """
    env = dict(env)
    env['MPLBACKEND'] = 'Agg'
    return env


def _run(cmd, cwd, env, timeout=600):
    return subprocess.run(cmd, cwd=cwd, env=_pin(env), capture_output=True,
                          text=True, encoding='utf-8', errors='replace',
                          timeout=timeout)


def _venv_python(venv):
    for rel in (os.path.join('Scripts', 'python.exe'), os.path.join('bin', 'python')):
        p = os.path.join(venv, rel)
        if os.path.exists(p):
            return p
    return os.path.join(venv, 'Scripts', 'python.exe')


def _venv_exe(venv):
    for rel in (os.path.join('Scripts', 'mpltweak.exe'), os.path.join('bin', 'mpltweak')):
        p = os.path.join(venv, rel)
        if os.path.exists(p):
            return p
    return None


def _sha(p):
    with open(p, 'rb') as f:
        return hashlib.sha256(f.read()).hexdigest()


def tree_fingerprint():
    """打印**工作树指纹** —— 本套件跑的是工作区，不是 HEAD。

    为什么必须有：本套件从**工作区**建 wheel（**刻意不从 HEAD 建** —— 那会漏掉未提交
    的修复），所以开发期它可能被"别人正在改的半成品"打红。t74 实测就撞上一次：
    `src/mpltweak/cli.py` 被改到一半 ⇒ `mpltweak --help` NameError ⇒ 本套件 ④ 变红。
    **判别手段是指纹，不是换基线**：红了先看这里的 HEAD / M / ?? 与 `diff --stat`，
    确认树是否静止、改动是否属于自己。

    ⇒ 正确使用场景：**发布期在冻结树上跑**（t43/V4 收官门那种静止状态）。
    """
    def git(*a):
        try:
            r = subprocess.run(['git'] + list(a), cwd=ROOT, env=_pin(os.environ),
                               capture_output=True, text=True, encoding='utf-8',
                               errors='replace')
            return r.stdout or ''
        except Exception:                                          # noqa: BLE001
            return ''

    head = git('rev-parse', '--short', 'HEAD').strip() or '(取不到)'
    status = git('status', '--short')
    n_mod = sum(1 for ln in status.splitlines() if ln[:2].strip() == 'M')
    n_new = sum(1 for ln in status.splitlines() if ln.startswith('??'))
    stat = [ln for ln in git('diff', '--stat').strip().splitlines() if ln.strip()]
    return ('树指纹：HEAD=%s  已改(M)=%d  未跟踪(??)=%d  diff --stat: %s'
            % (head, n_mod, n_new, stat[-1].strip() if stat else '(无改动)'))


def _ascii_bad(s):
    return sorted({c for c in s if ord(c) > 127})


# 全套件的 en 非 ASCII 采集（按 `命令/流` 精确登记；最后统一判一次）
_ASCII_BAD = {}


def _audit_ascii(name, r):
    for stream, txt in (('stdout', r.stdout), ('stderr', r.stderr)):
        ch = _ascii_bad(txt)
        if ch:
            _ASCII_BAD['%s/%s' % (name, stream)] = ch


def audit_ascii_final():
    """台账**只准缩小**：台账外的非 ASCII ⇒ 判红；台账里的已修好 ⇒ 只提示、不判红。"""
    unexpected = {k: v for k, v in _ASCII_BAD.items() if k not in KNOWN_NON_ASCII}
    check('⑤ en 下逐字节 ASCII（台账之外不得有新缺口）', not unexpected,
          '新缺口=%s；台账内命中=%s'
          % (unexpected, sorted(set(_ASCII_BAD) & set(KNOWN_NON_ASCII))))
    stale = [k for k in KNOWN_NON_ASCII if k not in _ASCII_BAD]
    if stale:
        _p('     提示：台账里这些已修好（en 下已全 ASCII）→ 请从 KNOWN_NON_ASCII 删掉：%s'
           % stale)


# ---------------------------------------------------------------- ① wheel
def build_wheel(wd):
    dist = os.path.join(wd, 'dist')
    os.makedirs(dist, exist_ok=True)
    base = [sys.executable, '-m', 'pip', 'wheel', '.', '--no-deps', '-w', dist]
    # 先试**不建隔离环境**（本机可离线跑，实测 3.3s）；失败再让 pip 自己建
    # （CI 有网，且那里未必装了 setuptools>=77）。
    tried = []
    for extra in (['--no-build-isolation'], []):
        r = _run(base + extra, ROOT, dict(os.environ, PYTHONIOENCODING='utf-8'))
        tried.append((extra or ['(默认:建隔离)'], r.returncode))
        if r.returncode == 0:
            break
    whls = [os.path.join(dist, f) for f in os.listdir(dist) if f.endswith('.whl')]
    check('① wheel 能构建（%s）' % '；'.join('%s→rc=%s' % (a, b) for a, b in tried),
          bool(whls), 'dist=%s' % [os.path.basename(w) for w in whls])
    if not whls:
        return None
    whl = sorted(whls)[-1]
    # 打包缺件类问题（CI 从不构建 wheel/sdist ⇒ 只能等发版才发现）
    with zipfile.ZipFile(whl) as z:
        names = z.namelist()
    need = ['mpltweak/__init__.py', 'mpltweak/cli.py', 'mpltweak/apply.py',
            'mpltweak/revert.py']
    missing = [n for n in need if n not in names]
    pngs = [n for n in names if n.startswith('mpltweak/resources/') and n.endswith('.png')]
    check('① wheel 里有该有的文件（package-data 的 resources/*.png 也在）',
          not missing and pngs, '缺=%s / resources png=%d / 共 %d 项'
          % (missing, len(pngs), len(names)))
    return whl


# ---------------------------------------------------------------- ② venv + 装
def make_venv(wd, whl):
    venv = os.path.join(wd, 'venv')
    env = _venv_env(wd)
    r = _run([sys.executable, '-m', 'venv', '--system-site-packages', venv],
             WORKSPACE, env)
    check('② 建干净 venv（--system-site-packages）rc=0', r.returncode == 0,
          (r.stderr or r.stdout)[-200:])
    vpy = _venv_python(venv)
    if not os.path.exists(vpy):
        return venv, None, None
    # --no-index：**绝不联网**（本机 wheel 是本地文件）
    r = _run([vpy, '-m', 'pip', 'install', '--no-deps', '--no-index',
              '--no-cache-dir', whl], WORKSPACE, env)
    ok = r.returncode == 0 and 'Successfully installed' in (r.stdout + r.stderr)
    check('② 从 wheel 装进 venv（--no-index，零网络）rc=0', ok,
          (r.stdout + r.stderr).strip().splitlines()[-1][:160])
    # ★ 这一条是整个套件的立足点：装到的是 wheel，不是仓库源码
    r = _run([vpy, '-c', 'import mpltweak; print(mpltweak.__file__)'], WORKSPACE, env)
    f = (r.stdout or '').strip().splitlines()[-1] if r.stdout.strip() else ''
    in_venv = f.lower().startswith(venv.lower())
    check('★ ③ 装到的是 wheel：venv 里 import 到的 mpltweak 在 venv 的 site-packages 下',
          in_venv and 'mpltweak\\src' not in f.lower() and '/src/mpltweak' not in f.lower(),
          '__file__=%s' % f)
    return venv, vpy, _venv_exe(venv)


# ---------------------------------------------------------------- ④ 文档↔CLI
def doc_cli_consistency(venv, vpy):
    """附录 B #7：文档命令表里列出的选项，对相应子命令必须可解析。"""
    env = _venv_env(os.path.dirname(venv), 'en')
    top = _run([vpy, '-m', 'mpltweak', '--help'], WORKSPACE, env)
    help_text = top.stdout + top.stderr
    # 先钉一条**最容易被忽略的**接缝：顶层 help 必须是**多行**（真换行），
    # 不能把 `\n` 当字面量打出来。t74 实测踩到：`MPLTWEAK_LANG=en` 下英文 help
    # 整块打成**一行**、中间是可见的 `\n`（0 个真换行 / 33 个字面 `\n`），
    # 而 zh 正常（31 个真换行）。没有这一条，症状只会表现成"子命令清单抓不到"。
    check('④ 顶层 --help 是多行输出（真换行），不把 `\\n` 字面量打出来',
          help_text.count('\n') >= 3 and '\\n' not in help_text,
          '真换行=%d 字面\\n=%d rc=%s'
          % (help_text.count('\n'), help_text.count('\\n'), top.returncode))
    subs = set(re.findall(r'^\s+mpltweak\s+([a-z][a-z0-9-]*)\b', help_text, re.M))
    check('④ 能从 CLI 自己的"用法："块取到子命令清单', bool(subs),
          'rc=%s subs=%s 输出=%r' % (top.returncode, sorted(subs), help_text[:400]))
    if not subs:
        return
    found, skipped = {}, []
    for rel in DOC_FILES:
        text = open(os.path.join(ROOT, rel), encoding='utf-8').read()
        fence = False
        for i, line in enumerate(text.splitlines(), 1):
            s = line.strip()
            if s.startswith('```'):
                fence = not fence
                continue
            if not (fence or s.startswith('|')):
                continue                    # 只认"命令参考"位置：表格 / 围栏代码块
            if any(c in line for c in NEG_CUES):
                if 'mpltweak' in line:
                    skipped.append('%s:%d' % (rel, i))
                continue
            for m in CMD_RE.finditer(line):
                sub, rest = m.group(1), m.group(2)
                if sub not in subs:
                    continue                # 脚本名（fig1.py）不是子命令
                for o in set(OPT_RE.findall(rest)):
                    found.setdefault(sub, {}).setdefault(o, set()).add('%s:%d' % (rel, i))
    pairs = [(s, o) for s in sorted(found) for o in sorted(found[s])]
    check('④ 文档命令表里抓到了 (子命令, 选项) 对', len(pairs) >= 4,
          '%d 对：%s' % (len(pairs), pairs))
    bad = []
    for sub, opt in pairs:
        h = _run([vpy, '-m', 'mpltweak', sub, '--help'], WORKSPACE, env)
        if opt not in (h.stdout + h.stderr):
            bad.append('%s %s (%s)' % (sub, opt, ','.join(sorted(found[sub][opt]))))
    check('④ 文档提到的每个选项都能被对应子命令解析（附录 B #7）', not bad,
          '不认的 %d 个：%s' % (len(bad), bad))
    _p('     （按反例线索跳过的行：%s）' % (skipped or '无'))


# ---------------------------------------------------------------- ⑤ 五条命令
def journey_commands(wd, vpy, exe, lang='en'):
    """按文档逐条真跑，断言 rc + 关键片段 + stdout/stderr 分流（en 下 ASCII）。"""
    env = _venv_env(wd, lang)
    script = os.path.join(wd, 'fig1.py')
    with open(script, 'w', encoding='utf-8', newline='') as f:
        f.write(SCRIPT_SRC)
    pdir = os.path.join(wd, '.tweak_params')

    def cli(*args):
        return _run([exe] + list(args), wd, env)

    results = {}

    r = cli('doctor')
    results['doctor'] = r
    check('⑤ doctor：rc=0 且 stdout 报出 matplotlib / backend',
          r.returncode == 0 and 'matplotlib' in r.stdout and 'backend' in r.stdout
          and not r.stderr.strip(),
          'rc=%s stdout[%d] stderr[%d]' % (r.returncode, len(r.stdout), len(r.stderr)))

    r = cli('check', script)
    results['check'] = r
    check('⑤ check：rc=0 且 stdout 给出体检结论（nothing found / 问题）',
          r.returncode == 0 and 'checked' in r.stdout and not r.stderr.strip(),
          'rc=%s stdout[:60]=%r' % (r.returncode, r.stdout[:60]))

    r = cli('describe', script)
    results['describe'] = r
    try:
        d = json.loads(r.stdout)
    except Exception:                                              # noqa: BLE001
        d = None
    check('⑤ describe：rc=0、stdout 是 JSON（version/n_figs/axes 齐）、stderr 干净',
          r.returncode == 0 and isinstance(d, dict) and 'version' in d
          and 'n_figs' in d and 'axes' in d and not r.stderr.strip(),
          'rc=%s keys=%s' % (r.returncode, sorted(d) if d else None))

    # describe -o：确认写盘通道（stdout 空、stderr 有确认行）
    os.makedirs(pdir, exist_ok=True)     # 实测：describe -o 不建父目录（见「未决」）
    pf = os.path.join(pdir, 'fig1.json')
    r = cli('describe', script, '-o', pf)
    results['describe-o'] = r
    check('⑤ describe -o：rc=0、stdout 空、stderr 有「written to」、文件真的落盘',
          r.returncode == 0 and not r.stdout.strip()
          and 'written to' in r.stderr and os.path.exists(pf),
          'rc=%s stderr=%r exists=%s' % (r.returncode, r.stderr[:60], os.path.exists(pf)))

    sha_before = _sha(script)
    r = cli('apply', script)                 # 不带 --json：走**人读**通道
    results['apply'] = r
    human = r.stdout + r.stderr
    check('⑤ apply（不写）：rc=0、列出 will change（文档承诺"列出会改哪些数字"）、'
          '且源码一个字节不动',
          r.returncode == 0 and 'will change:' in human and _sha(script) == sha_before,
          'rc=%s will_change=%s（stdout=%s / stderr=%s）'
          % (r.returncode, 'will change:' in human,
             'will change:' in r.stdout, 'will change:' in r.stderr))
    # 机器可读通道另测：`--json` 时结果走 stdout、过程信息走 stderr（SKILL.md:96）
    r = cli('apply', script, '--json')
    results['apply-json'] = r
    try:
        aj = json.loads(r.stdout)
    except Exception:                                              # noqa: BLE001
        aj = None
    check('⑤ apply --json：rc=0、reason=preview、changes 为空（stdout 里不混过程信息）',
          r.returncode == 0 and isinstance(aj, dict)
          and aj['files'][0]['reason'] == 'preview'
          and aj['files'][0]['changes'] == [],
          'rc=%s reason=%s' % (r.returncode,
                               (aj or {}).get('files', [{}])[0].get('reason')))

    r = cli('revert', script)
    results['revert'] = r
    check('⑤ revert（没写过、预览）：rc=2 且错误走 stderr、stdout 空',
          r.returncode == 2 and not r.stdout.strip() and '[revert]' in r.stderr,
          'rc=%s stderr=%r' % (r.returncode, r.stderr[:70]))

    # en 下逐字节 ASCII：采集起来，最后由 audit_ascii_final() 统一判（台账只准缩小）
    for name, r in results.items():
        _audit_ascii(name, r)

    return script, pf, d


# ---------------------------------------------------------------- ⑥ 不变量
def invariants(wd, vpy, exe, script, pf, d):
    env = _venv_env(wd, 'en')

    def cli(*args):
        return _run([exe] + list(args), wd, env)

    # ---- 零改动不变量（附录 B #5）----
    before = _sha(script)
    r1 = cli('apply', script, '--json')
    r2 = cli('apply', script, '--json')
    j1 = json.loads(r1.stdout)
    check('⑥ 零改动①：describe → apply（不写）⇒ reason=preview / changes=[] / 字节不动',
          r1.returncode == 0 and j1['files'][0]['reason'] == 'preview'
          and j1['files'][0]['changes'] == [] and _sha(script) == before,
          'reason=%s 字节变了吗=%s' % (j1['files'][0]['reason'], _sha(script) != before))
    check('⑥ 零改动②：同样的 apply 两次 ⇒ JSON 逐字段相同（幂等）', j1 == json.loads(r2.stdout),
          '')

    # ---- 写回不变量：只允许预期字段变化 ----
    copy = os.path.join(wd, 'fig2.py')
    shutil.copyfile(script, copy)
    shutil.copyfile(pf, os.path.join(wd, '.tweak_params', 'fig2.json'))
    src_before = open(copy, 'rb').read()

    # 先证明"参数与源码一致时 --write 也不动字节"（= no_change 的真实语义）
    r = cli('apply', copy, '--write', '--json')
    jw = json.loads(r.stdout)
    check('⑥ 写回①：参数与源码一致时 apply --write ⇒ reason=no_change 且一个字节不动',
          r.returncode == 0 and jw['files'][0]['reason'] == 'no_change'
          and jw['files'][0]['applied'] is False
          and open(copy, 'rb').read() == src_before,
          'reason=%s applied=%s' % (jw['files'][0]['reason'], jw['files'][0]['applied']))

    # 再把参数改成"真的不同"，看它改的是不是**只有预期字段**
    d2 = json.loads(json.dumps(d))
    d2['axes'][0]['pos'] = [0.25, 0.25, 0.5, 0.5]
    d2['figsize_in'] = [7.5, 5.0]
    mod = os.path.join(wd, '.tweak_params', 'fig2_mod.json')
    with open(mod, 'w', encoding='utf-8') as f:
        json.dump(d2, f, ensure_ascii=False)
    r = cli('apply', copy, '--params', mod, '--write', '--json')
    jw = json.loads(r.stdout)
    fl = jw['files'][0]
    src_after = open(copy, 'rb').read()
    bl = src_before.decode('utf-8').splitlines()
    al = src_after.decode('utf-8').splitlines()
    changed_lines = [(i + 1, bl[i] if i < len(bl) else '<无>',
                      al[i] if i < len(al) else '<无>')
                     for i in range(max(len(bl), len(al)))
                     if (bl[i] if i < len(bl) else None) != (al[i] if i < len(al) else None)]
    check('⑥ 写回②：reason=ok、changes 恰好是预期字段、备份与验证都记了',
          r.returncode == 0 and fl['reason'] == 'ok' and fl['applied'] is True
          and sorted(fl['changes']) == ['ax0.pos', 'figsize']
          and fl['backup'] and fl['verified'] is True,
          'reason=%s changes=%s backup=%s verified=%s'
          % (fl['reason'], fl['changes'], bool(fl['backup']), fl['verified']))
    # **不拿"有变化"当判据**：逐行比对，只许那两行变，且新值就是喂进去的值
    ok_lines = (len(changed_lines) == 2
                and all(ln == 5 or ln == 6 for ln, _a, _b in changed_lines)
                and 'figsize=(7.5, 5)' in al[4] and 'add_axes([0.25, 0.25, 0.5, 0.5])' in al[5])
    check('⑥ 写回③：源码**只有**预期那两行变了（figsize 行 + add_axes 行），其余逐字不动',
          ok_lines, '变化行=%s' % [(n, a.strip(), b.strip()) for n, a, b in changed_lines])

    # ---- revert 逐字节回原样 ----
    r = cli('revert', copy, '--write')
    _audit_ascii('revert-write', r)
    back = open(copy, 'rb').read() == src_before
    check('⑥ 回退：revert --write ⇒ 与写回前**逐字节**相同', r.returncode == 0 and back,
          'rc=%s 逐字节相同=%s' % (r.returncode, back))

    # ---- describe 幂等（JSON 逐字段）----
    a = json.loads(cli('describe', script).stdout)
    b = json.loads(cli('describe', script).stdout)
    check('⑥ describe 两次 ⇒ JSON 逐字段相同（幂等）', a == b, '')


# ---------------------------------------------------------------- ⑦ P2-8
def path_guidance(venv, vpy, exe):
    """P2-8：命令不在 PATH 时，文档给的指引必须真的存在、可复制、可跑。"""
    text = open(os.path.join(ROOT, 'skills', 'mpltweak', 'SKILL.md'),
                encoding='utf-8').read()
    need = ['which is not on PATH', 'Scripts\\mpltweak.exe', 'python -m mpltweak doctor']
    miss = [n for n in need if n not in text]
    check('⑦ P2-8 指引存在（全路径调用 / 加 PATH / python -m 三条兜底都在）', not miss,
          '缺=%s' % miss)
    env = _venv_env(os.path.dirname(venv), 'en')
    r1 = _run([exe, 'doctor'], WORKSPACE, env)
    r2 = _run([vpy, '-m', 'mpltweak', 'doctor'], WORKSPACE, env)
    check('⑦ 文档那两条兜底**真的能跑**：全路径 exe 与 python -m mpltweak 都 rc=0',
          r1.returncode == 0 and r2.returncode == 0,
          'exe rc=%s / -m rc=%s' % (r1.returncode, r2.returncode))


# ---------------------------------------------------------------- teeth
def teeth(wd, vpy, exe):
    """反向自证：这三条判据都得**会咬人**，不能是恒绿。"""
    env = _venv_env(wd, 'en')

    # teeth① 文档提取器真的读了文档：把一条真选项改成不存在的 ⇒ 必须被抓到
    text = open(os.path.join(ROOT, 'skills', 'mpltweak', 'SKILL.md'),
                encoding='utf-8').read()
    mutated = text.replace('`mpltweak apply <脚本.py> --json`',
                           '`mpltweak apply <脚本.py> --not-a-real-option`', 1)
    check('teeth① 文档提取器不是恒绿：改坏一条选项能被抓出来',
          mutated != text
          and '--not-a-real-option' in mutated
          and '--not-a-real-option' not in
          (_run([vpy, '-m', 'mpltweak', 'apply', '--help'], WORKSPACE, env).stdout),
          '原文档命中=%s' % ('--json' in text))

    # teeth② 零改动判据不是恒绿：参数真的不同时 changes 必须非空
    d = json.loads(_run([vpy, '-m', 'mpltweak', 'describe',
                         os.path.join(wd, 'fig1.py')], wd, env).stdout)
    d['axes'][0]['pos'] = [0.3, 0.3, 0.4, 0.4]
    m = os.path.join(wd, '.tweak_params', 'fig1_teeth.json')
    with open(m, 'w', encoding='utf-8') as f:
        json.dump(d, f, ensure_ascii=False)
    c = os.path.join(wd, 'fig_teeth.py')
    shutil.copyfile(os.path.join(wd, 'fig1.py'), c)
    shutil.copyfile(m, os.path.join(wd, '.tweak_params', 'fig_teeth.json'))
    r = _run([exe, 'apply', c, '--write', '--json'], wd, env)
    ch = json.loads(r.stdout)['files'][0]['changes']
    check('teeth② 「零改动」判据不是恒绿：参数真不同时 changes 非空', bool(ch),
          'changes=%s' % ch)

    # teeth③ provenance 判据有区分力：editable 安装处 import 到的**确实**是仓库源码
    r = _run([sys.executable, '-c', 'import mpltweak; print(mpltweak.__file__)'],
             WORKSPACE, dict(os.environ, PYTHONIOENCODING='utf-8'))
    src_path = (r.stdout or '').strip()
    check('teeth③ provenance 判据有区分力：仓库侧 import 到的确实指向仓库 src'
          '（所以"必须在 venv 下"这条不是白写的）',
          ('mpltweak\\src' in src_path.lower() or '/src/mpltweak' in src_path.lower()),
          '__file__=%s' % src_path)


# ---------------------------------------------------------------- main
def main():
    _p('首次体验旅程测试（t74）：wheel → 干净 venv → 按文档逐条真跑')
    _p('零网络（--no-index）· 不开窗（MPLBACKEND=Agg）· 只跑 CLI')
    _p(tree_fingerprint())
    _p('  ⚠ 本套件跑的是**工作区**（刻意不从 HEAD 建 wheel，免得漏掉未提交的修复）：'
       '开发期红了先用上面的指纹判别"是别人的半成品还是我的问题"；'
       '权威用法是**发布期在冻结树上跑**。')
    _p('')
    wd = os.path.join(WORKSPACE, '.mpltweak_journey_%d' % os.getpid())
    if os.path.isdir(wd):
        shutil.rmtree(wd, ignore_errors=True)
    os.makedirs(wd)
    try:
        _p('---- ① wheel ----')
        whl = build_wheel(wd)
        if whl is None:
            return 1
        _p('     轮子 = %s（%.1f KB）'
           % (os.path.basename(whl), os.path.getsize(whl) / 1024.0))
        _p('')
        _p('---- ② 干净 venv + 装 wheel ----')
        venv, vpy, exe = make_venv(wd, whl)
        if not vpy or not exe:
            _p('FAIL | ② venv/入口没建起来，后面的旅程无法进行')
            _fails.append('venv')
            return 1
        _p('')
        _p('---- ③ 文档 ↔ CLI ----')
        doc_cli_consistency(venv, vpy)
        _p('')
        _p('---- ④ 五条命令真跑（en）----')
        script, pf, d = journey_commands(wd, vpy, exe)
        _p('')
        _p('---- ⑤ 不变量 ----')
        invariants(wd, vpy, exe, script, pf, d)
        _p('')
        _p('---- ⑥ P2-8 PATH 指引 ----')
        path_guidance(venv, vpy, exe)
        _p('')
        _p('---- teeth（反向自证）----')
        teeth(wd, vpy, exe)
        _p('')
        _p('---- en 逐字节 ASCII（汇总）----')
        audit_ascii_final()
    finally:
        shutil.rmtree(wd, ignore_errors=True)
        _p('')
        _p('临时工作区已清理：%s（仍存在=%s）' % (wd, os.path.exists(wd)))
    _p('')
    if _fails:
        _p('FAILED: %d 项 -> %s' % (len(_fails), _fails))
        return 1
    _p('ALL PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
