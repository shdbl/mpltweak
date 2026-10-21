# -*- coding: utf-8 -*-
"""mpltweak GUI 续调 launch._apply_saved 顶层非对象 JSON 守卫回归（0.1.10）。

背景（fuzz-params 在 params fuzz 里发现，船长授权修复）：
  GUI 续调路径读上次保存的参数文件套回图。顶层非对象 JSON（数组/字符串/
  数字/null）过去会让 `data.get('axes', [])`（_apply_saved）崩成
  AttributeError —— 参数文件坏了就崩掉整个开窗流程，违背"续调是尽力而为"
  （文件坏了就跳过，绝不让 GUI 开窗崩掉）的语义。

修法（launch.py，与 params.load 的 0.1.10 修复同款语义）：
  * _apply_saved()：json.load 后先 isinstance(data, dict) 守卫，非对象 →
    stderr 一句人话提示 + return 0（不续调、不崩、不打扰主流程）。
  * main() 续调循环：_d2.get('fig_index') 是续调流程里更早的一道闸，非对象
    会在那里先崩（AttributeError，根本到不了 _apply_saved）—— 同样加
    isinstance 守卫，跳过该文件（本轮一并修，属同一崩溃类）。

本套件断言（纯函数级，不真开窗口）：
  * 顶层数组/字符串/数字/null 各一：_apply_saved(stub_fig, path) 返回 0、
    不抛异常、stderr 有"顶层不是对象"人话提示；
  * 合法 dict 参数：不被误跳过（无提示、不抛异常、返回 int）；
  * 文件不存在：返回 0（既有语义不回归）。
  stub fig 只需带空 axes 列表（_apply_saved 的非对象路径根本不触碰 fig；
  正常路径也只调 fig.axes[i].* / fig.set_size_inches）。
"""
import contextlib
import io
import json
import re
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 界面语言钉死（与其它套件一致）：断言的是中文文案，别让宿主 locale 决定结论。
os.environ['MPLTWEAK_LANG'] = 'zh'
sys.path.insert(0, os.path.join(ROOT, 'src'))
from mpltweak.launch import (_apply_saved,            # noqa: E402
                             _next_step_cmd, _rel_for_shell)
from mpltweak import messages as _msgs                # noqa: E402

# GBK 控制台兜底（同 test_check.py / test_fuzz_params.py）：CI 是 UTF-8，
# 本地 cp936 上一次 UnicodeEncodeError 就把整套从 ALL PASS 打成 rc=1 的假红。
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


_TMPROOT = os.path.join(ROOT, 'tests', '.tmp_launch_%d' % os.getpid())

# t31：子进程 CLI 的**假家目录**。launch 的两个"每个用户只打一次"戳
# （``first_open_shown`` / ``tk_hint_shown``）都落在
# ``_mpltweak_home() = MPLTWEAK_HOME or ~/.mpltweak``（launch.py:86-104 / 165-172）。
# 不隔离的话，任何走到"真要建窗口那一刻"的子进程用例都会把**用户真实**的一次性戳
# 写掉 —— 2026-10-13 实测事故：跑一次 pytest 就写出了
# ``C:\Users\xiang\.mpltweak\first_open_shown``，用户以后真开窗反而看不到那句提示
# （被测试留下的戳骗过去了）。所以这里钉死一个测试临时区内的家目录。
_SANDBOX_HOME = os.path.join(_TMPROOT, 'home')


class _StubFig:
    """_apply_saved 的桩 fig：非对象参数路径不触碰它；空 axes + set_size_inches
    兜底，保证万一走到正常路径也不炸（不需要真窗口）。"""

    def __init__(self):
        self.axes = []

    def set_size_inches(self, w, h):
        pass


_fails = []


def check(name, cond, detail=''):
    mark = 'OK' if cond else 'FAIL'
    print('%s | %s%s' % (mark, _gbk_safe(name),
                         (' | ' + _gbk_safe(detail)) if detail else ''))
    if not cond:
        _fails.append(name)


# 顶层非对象负载：数组/字符串/数字/null 各一（json.dump 写文件，模拟用户手改坏）
_NON_OBJECTS = (
    ('array', [1, 2, 3]),
    ('string', 'hello'),
    ('number', 42),
    ('null', None),
)

# 合法 dict：续调要正常走（不被误跳过）；figsize_in 顺带验证 stub 的
# set_size_inches 兜底路径。
_LEGAL_PARAMS = {'version': 4, 'fig_index': 0, 'axes': [],
                 'figsize_in': [8.0, 6.0]}


def run_guards():
    fig = _StubFig()
    for tag, payload in _NON_OBJECTS:
        pth = os.path.join(_TMPROOT, '%s.json' % tag)
        with open(pth, 'w', encoding='utf-8') as f:
            json.dump(payload, f)
        err = io.StringIO()
        try:
            with contextlib.redirect_stderr(err):
                n = _apply_saved(fig, pth)
            raised = None
        except Exception as e:                       # noqa: BLE001
            n, raised = None, e
        check('_apply_saved %s：返回 0 不抛异常' % tag,
              raised is None and n == 0,
              ('raised=%s' % _gbk_safe(repr(raised))[:200]) if raised
              else 'n=%s' % n)
        check('_apply_saved %s：stderr 有人话提示' % tag,
              '顶层不是对象' in err.getvalue(),
              err.getvalue().strip()[:120])

    # 合法 dict 参数：不被误跳过（无提示、不抛异常、返回 int）
    pth = os.path.join(_TMPROOT, 'legal.json')
    with open(pth, 'w', encoding='utf-8') as f:
        json.dump(_LEGAL_PARAMS, f)
    err = io.StringIO()
    try:
        with contextlib.redirect_stderr(err):
            n = _apply_saved(fig, pth)
        raised = None
    except Exception as e:                           # noqa: BLE001
        n, raised = None, e
    check('_apply_saved 合法 dict：不误跳过（无提示、不抛异常、返回 int）',
          raised is None and isinstance(n, int) and err.getvalue() == '',
          ('raised=%s' % _gbk_safe(repr(raised))[:200]) if raised
          else 'n=%r stderr=%r' % (n, err.getvalue()[:120]))

    # 文件不存在：既有语义 return 0
    try:
        n = _apply_saved(fig, os.path.join(_TMPROOT, 'nope.json'))
        raised = None
    except Exception as e:                           # noqa: BLE001
        n, raised = None, e
    check('_apply_saved 文件不存在：return 0 不抛异常',
          raised is None and n == 0,
          ('raised=%s' % _gbk_safe(repr(raised))[:200]) if raised else 'n=%s' % n)


def run_next_step():
    """t44 无感化①：关窗后打印的那条命令怎么拼。

    契约（审阅者会按这些查）：
      * **相对路径**（与用户敲命令时所在的目录一致）→ 可直接复制；
      * 不在那个目录之下（要写一串 `..` / 跨盘）→ 保留绝对路径（同样能复制）；
      * 含空格的路径加双引号（cmd / PowerShell / bash 都能粘贴）；
      * `--params` 只在用户**自定义过**参数路径时才带（默认路径不必写）；
      * launch 收到的脚本参数要**原样透传**（`-- <参数...>`）—— 写回重跑要用同一份 argv；
      * 文案跟随 `--lang`、GBK 可编码、命令行**独立成行且不带前缀**（整行可复制）。
    """
    base = os.path.join(_TMPROOT, 'proj')
    script = os.path.join(base, 'fig1.py')
    params = os.path.join(base, '.tweak_params', 'fig1.json')

    cmd = _next_step_cmd(script, params, base=base)
    check('next_step_cmd 同目录：相对路径 + --write',
          cmd == 'mpltweak apply fig1.py --write', cmd)

    # 脚本在 base 的上一级 → 相对路径（不写绝对路径，更好认）
    up = _next_step_cmd(os.path.join(os.path.dirname(base), 'fig2.py'),
                        params, base=base)
    check('next_step_cmd 上一级目录：写成 ..%s 形式（仍是可复制的相对路径）'
          % os.sep,
          up == 'mpltweak apply ..%sfig2.py --write' % os.sep, up)

    # 更深一层（常见：脚本在子目录里）
    sub = _next_step_cmd(os.path.join(base, 'sub', 'fig5.py'), params, base=base)
    check('next_step_cmd 子目录：写成 sub%sfig5.py 形式' % os.sep,
          sub == 'mpltweak apply sub%sfig5.py --write' % os.sep, sub)

    # 跨盘符（Windows 才有）：没有相对路径可言 → 退回绝对路径
    drv = os.path.splitdrive(base)[0]
    if os.name == 'nt':
        other = ('Z:' if drv.upper() != 'Z:' else 'Y:') + os.sep
        far = os.path.join(other, 'other', 'fig3.py')
        far_cmd = _next_step_cmd(far, params, base=base)
        check('next_step_cmd 跨盘符：退回绝对路径（没有 .. 可言）',
              far in far_cmd, far_cmd)
        check('_rel_for_shell 跨盘符原样返回',
              _rel_for_shell(far, base) == far, _rel_for_shell(far, base))
    else:
        check('next_step_cmd POSIX：不存在跨盘，远亲路径仍是相对路径（可直接复制）',
              _rel_for_shell('/other/fig3.py', base).startswith('..'))

    # 空格 → 双引号
    sp = os.path.join(base, 'my figs', 'fig4.py')
    sp_cmd = _next_step_cmd(sp, params, base=base)
    check('next_step_cmd 含空格的路径加双引号（cmd / PowerShell / bash 都能粘贴）',
          '"my figs%sfig4.py"' % os.sep in sp_cmd, sp_cmd)

    # --params 只在用户自定义过路径时才带
    check('next_step_cmd 默认参数路径：不带 --params',
          '--params' not in cmd, cmd)
    p2 = os.path.join(base, 'custom.json')
    check('next_step_cmd 自定义参数路径：带 --params',
          _next_step_cmd(script, p2, params_given=True, base=base)
          == 'mpltweak apply fig1.py --write --params custom.json',
          _next_step_cmd(script, p2, params_given=True, base=base))

    # 脚本参数透传（bug #6 的口径：写回重跑必须拿到同一份 argv）
    check('next_step_cmd 透传脚本参数：`-- <参数...>`',
          _next_step_cmd(script, params, script_args=('--n', '7'),
                         base=base)
          == 'mpltweak apply fig1.py --write -- --n 7',
          _next_step_cmd(script, params, script_args=('--n', '7'), base=base))

    # 文案：跟随 --lang、GBK 可编码、命令行独立成行且不带前缀
    for lang, needle in (('zh', '要写回源码'), ('en', 'to write')):
        line = _msgs_next_step(lang)
        check('next_step_apply 跟随 --lang（%s）' % lang, needle in line,
              _gbk_safe(line.replace('\n', '\\n')))
        tail = line.split('\n')[-1]
        check('next_step_apply（%s）命令行独立成行、不带前缀 ⇒ 可整行复制'
              % lang,
              tail.strip() == 'mpltweak apply fig1.py --write'
              and not tail.lstrip().startswith('[launch]'), repr(tail))
        try:
            line.encode('gbk')
            ok_gbk = True
        except UnicodeEncodeError:
            ok_gbk = False
        check('next_step_apply（%s）GBK 可编码（cp936 控制台不许崩）' % lang,
              ok_gbk, _gbk_safe(line.replace('\n', '\\n')))
    _msgs.set_lang('zh')                       # 复位，别影响后面的用例


def _msgs_next_step(lang):
    _msgs.set_lang(lang)
    return _msgs.t('next_step_apply', params='.tweak_params/fig1.json',
                   cmd='mpltweak apply fig1.py --write')


# ---------------------------------------------------------------------------
# t58（方案 A，0.1.11）：**退出码矩阵** —— 0 / 1 / 2 / 4；**3 已退役**
#
# 口径（用户 2026-10-13 拍板）：
#   0 = 没问题 / 成功；1 = 发现问题（check）；2 = 跑不起来；4 = 没有可用图形后端。
#   2 覆盖：脚本找不到 / 脚本自己报错 / 顶层未知参数 / 参数非法 / 工具自身异常。
#
# 这里**真的起子进程跑 CLI**（`python -m mpltweak ...`）——不用 mock：口径是"用户 CI
# 会看到的那个数字"，只有真调用才算证据。矩阵覆盖 6 个入口 × 各场景。
#
# 例外（**不在本用例的豁免之外，如实登记**）：`src/mpltweak/verify.py` 内部子进程
# 契约仍返回 3（它由 writeback 以子进程方式消费、不是用户可见 CLI 入口），改它不在
# 本任务 inScope；本用例只断言**用户可见入口**（cli/launch/apply/describe/check/
# revert/mcp_server）里没有任何 `return 3`、也没有任何地方 return `EXIT_SCRIPT`。
# ---------------------------------------------------------------------------

def _cli_env():
    env = dict(os.environ)
    src = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'src')
    # t114：**追加**而不是覆盖 —— 否则“强制无后端”的手艺（把 tkinter/Qt 的
    # stub 目录放到 PYTHONPATH 最前）传不进子进程，那条分支就永远测不到。
    _pp = os.environ.get('PYTHONPATH')
    env['PYTHONPATH'] = (_pp + os.pathsep + src) if _pp else src
    env['MPLBACKEND'] = 'Agg'
    env['MPLTWEAK_LANG'] = 'zh'
    env['PYTHONIOENCODING'] = 'utf-8'
    # t31：**家目录必须隔离**（理由见 _SANDBOX_HOME 处那段注释：不隔离会让子进程
    # 用例把用户真实的 first_open_shown 戳写掉，且写掉一次就再也补不回来）。
    env['MPLTWEAK_HOME'] = _SANDBOX_HOME
    return env


# t117：**不要再猜环境** —— t114（“能 import tkinter”口径）与 t115（doctor 口径）
# 两次都跟 `launch` 分叉过：Windows runner 有 tkinter、无 PyQt ⇒ doctor 说“不可用”
# 而 launch 优雅退回 tkinter 可用 ⇒ 期望被推导成 4、产品却给 2 ⇒ 红。
# 现在改为**由产品在同一条路径上的实际表现推导期望**：
#   输出里出现无头提示 ⇒ 期望 rc=4（并要求给出可操作建议）；
#   没出现           ⇒ 期望 rc=2。
# 两个分支**各自断言**，不是“2 或 4 都行”。
_HEADLESS_MARKS = ('找不到可用的图形后端', 'no usable')


def _is_headless_out(text):
    return any(_m in (text or '') for _m in _HEADLESS_MARKS)




def _write_fixture(name, text):
    p = os.path.join(_TMPROOT, name)
    with open(p, 'w', encoding='utf-8', newline='\n') as f:
        f.write(text)
    return p


def _run_cli(argv):
    """真跑一次 CLI，返回 (rc, 第一条非空 stderr/stdout 行, 全文)。"""
    import subprocess
    r = subprocess.run([sys.executable, '-m', 'mpltweak'] + list(argv),
                       capture_output=True, text=True, encoding='utf-8',
                       errors='replace', env=_cli_env(),
                       cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       timeout=300)
    full = (r.stderr or '') + '\n' + (r.stdout or '')
    first = ''
    for line in full.split('\n'):
        if line.strip():
            first = line.strip()
            break
    return r.returncode, first, full


def run_exit_matrix():
    ok_script = _write_fixture('exit_ok.py',
                               'import matplotlib\nmatplotlib.use("Agg")\n'
                               'import matplotlib.pyplot as plt\n'
                               'fig = plt.figure(figsize=(4, 3))\n'
                               'ax = fig.add_axes([0.1, 0.1, 0.5, 0.5])\n'
                               'ax.plot([0, 1], [0, 1])\n')
    raise_script = _write_fixture('exit_raise.py', 'raise RuntimeError("boom")\n')
    nofig_script = _write_fixture('exit_nofig.py', 'x = 1\n')
    overlap_script = _write_fixture('exit_overlap.py',
                                    'import matplotlib\nmatplotlib.use("Agg")\n'
                                    'import matplotlib.pyplot as plt\n'
                                    'fig = plt.figure(figsize=(4, 3))\n'
                                    'a = fig.add_axes([0.2, 0.2, 0.4, 0.4])\n'
                                    'a.plot([0, 1], [0, 1])\n'
                                    'b = fig.add_axes([0.2, 0.2, 0.4, 0.4])\n'
                                    'b.plot([0, 1], [1, 0])\n')
    missing = os.path.join(_TMPROOT, 'exit_nope.py')
    if os.path.exists(missing):
        os.remove(missing)
    # `apply <存在但没参数>` 必须用一个**没被别的用例碰过**的脚本（launch --dry-run
    # 会顺手落参数文件，跑过就变成"有参数"了）
    bare_script = _write_fixture('exit_bare.py',
                                 'import matplotlib\nmatplotlib.use("Agg")\n'
                                 'import matplotlib.pyplot as plt\n'
                                 'fig = plt.figure(figsize=(4, 3))\n'
                                 'ax = fig.add_axes([0.1, 0.1, 0.5, 0.5])\n')
    for f in (bare_script,):
        b = os.path.join(os.path.dirname(f), '.tweak_params',
                         os.path.splitext(os.path.basename(f))[0] + '.json')
        if os.path.exists(b):
            os.remove(b)

    cases = [
        ('顶层 · 脚本找不到', [missing], 2, '找不到脚本', None),
        ('顶层 · 脚本自己报错', [raise_script], 2, None, None),
        ('顶层 · 脚本没图', [nofig_script], 2, None, None),
        ('顶层 · 未知参数', ['--bogus'], 2, None, None),
        ('launch · 脚本找不到', ['launch', missing], 2, '找不到脚本', None),
        ('launch · 脚本自己报错', ['launch', raise_script], 2, None, None),
        ('describe · 脚本找不到', ['describe', missing], 2, '找不到脚本', None),
        ('describe · 脚本自己报错', ['describe', raise_script], 2, None, None),
        ('describe · 正常脚本', ['describe', ok_script], 0, None, None),
        ('check · 脚本找不到', ['check', missing], 2, '找不到脚本', None),
        ('check · 脚本自己报错', ['check', raise_script], 2, None, None),
        ('check · 检出排版问题', ['check', overlap_script], 1, None, None),
        ('check · 正常脚本', ['check', ok_script], 0, None, None),
        ('apply · 脚本找不到', ['apply', missing], 2, '找不到脚本', 'no params'),
        ('apply · 存在但没参数文件', ['apply', bare_script], 2, 'no params', None),
        ('apply · 脚本自己报错（无参数文件）', ['apply', raise_script], 2, None, None),
        ('revert · 脚本找不到', ['revert', missing], 2, '找不到脚本', None),
        ('revert · 存在但没备份', ['revert', bare_script], 2, None, None),
        ('doctor', ['doctor'], 0, None, None),
    ]
    for label, argv, want, must, must_not in cases:
        rc, first, full = _run_cli(argv)
        _headless = _is_headless_out(full)
        if _headless:
            # t117：产品**自己说**没有可用后端 ⇒ 它必须如实返回 4（不是 2，也不是 0）。
            want = 4
        detail = 'rc=%s（期望 %s）| 首行: %s' % (rc, want, _gbk_safe(first)[:90])
        ok = (rc == want)
        if ok and must is not None:
            ok = must in first
            if not ok:
                detail += '（首行必须含 %r）' % must
        if ok and must_not is not None:
            ok = must_not not in first
            if not ok:
                detail += '（首行不许含 %r）' % must_not
        if ok and _headless:
            # 无后端分支的**额外**要求：得说清为什么（可操作建议），不能只给个 4。
            ok = ('pip install "mpltweak[qt]"' in full) or ('python3-tk' in full)
            if not ok:
                detail += '（无后端分支必须给出可操作建议）'
        check('退出码矩阵：%s' % label, ok, detail)

    # 静态面：用户可见入口里不许再出现 `return 3`；也不许再 return EXIT_SCRIPT
    src_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'src', 'mpltweak')
    entries = ['cli.py', 'launch.py', 'apply.py', 'describe.py', 'check.py', 'revert.py',
               'mcp_server.py', 'schema.py', 'params.py']
    bad = []
    for name in entries:
        p = os.path.join(src_dir, name)
        if not os.path.exists(p):
            continue
        text = io.open(p, encoding='utf-8').read()
        for i, line in enumerate(text.split('\n'), 1):
            if re.match(r'\s*return\s+3\b', line) or re.match(r'\s*return\s+\.?EXIT_SCRIPT\b', line) \
                    or re.match(r'\s*return\s+_?m(sg)?\.EXIT_SCRIPT\b', line):
                bad.append('%s:%d' % (name, i))
    check('退出码矩阵：用户可见入口无 `return 3`（3 已退役）', not bad, ','.join(bad) or '干净')


# ---------------------------------------------------------------------------
# t31：**真实家目录护栏** —— 本套件绝不许往用户真实 ~/.mpltweak 写任何东西
#
# 三道：
#   G1 隔离真生效（确定性）：用 _cli_env() 起子进程问 launch._mpltweak_home() 解析到
#      哪儿，必须落在本套件的临时区内。把 _cli_env() 里那行隔离删掉 → 必须变红。
#   G2 真实家目录零新增：整个 CLI 矩阵跑完，~/.mpltweak 不许出现任何新文件，且
#      first_open_shown 必须不存在（这是用户**可感知**的那一条）。
#   G3 护栏有牙（安全版）：把 MPLTWEAK_HOME 摘掉、HOME/USERPROFILE 指向一个**假家
#      目录**，真调一次 _emit_first_open_hint() —— 戳必须落在那个假家目录里。这证明
#      "不隔离 ⇒ 写进用户家目录"这条因果是真的，而验证过程**不碰**真实家目录
#      （所以它能常驻套件，不必只做一次性反向验证）。
# ---------------------------------------------------------------------------

def _real_home_dir():
    """用户**真实**家目录下的 .mpltweak（expanduser，不受 MPLTWEAK_HOME 影响）。"""
    return os.path.join(os.path.expanduser('~'), '.mpltweak')


def _real_home_listing():
    try:
        return sorted(os.listdir(_real_home_dir()))
    except OSError:
        return []


_HOME_PROBE = (
    'import sys\n'
    'sys.path.insert(0, sys.argv[1])\n'
    'from mpltweak.launch import _mpltweak_home\n'
    'print(_mpltweak_home())\n'
)

_TEETH_PROBE = (
    'import io, sys\n'
    'sys.path.insert(0, sys.argv[1])\n'
    'from mpltweak.launch import _emit_first_open_hint, _first_open_stamp_path\n'
    'buf = io.StringIO()\n'
    'shown = _emit_first_open_hint(stderr=buf)\n'
    'print(_first_open_stamp_path())\n'
    'print("SHOWN=%s" % shown)\n'
)


def run_real_home_guards(home_before):
    """跑完 CLI 矩阵后调用；``home_before`` = 开跑前真实家目录的清单快照。"""
    import subprocess
    real = _real_home_dir()
    src = os.path.join(ROOT, 'src')
    print('t31 真实家目录护栏：%s' % real)

    # G1 —— 隔离真生效：子进程里问 launch 自己怎么解析家目录
    r = subprocess.run([sys.executable, '-c', _HOME_PROBE, src],
                       capture_output=True, text=True, encoding='utf-8',
                       errors='replace', env=_cli_env(), cwd=ROOT, timeout=300)
    got = [l.strip() for l in (r.stdout or '').splitlines() if l.strip()]
    got = got[0] if got else ''
    try:
        inside = (os.path.commonpath([os.path.abspath(got), os.path.abspath(_TMPROOT)])
                  == os.path.abspath(_TMPROOT))
    except ValueError:                       # 不同盘符 -> 必然不在里面
        inside = False
    check('G1 子进程 CLI 的家目录被隔离到测试临时区', bool(got) and inside,
          'rc=%s 解析到 %r（临时区 %r）' % (r.returncode, got, _TMPROOT))
    check('G1b 解析到的家目录不是用户真实家目录',
          bool(got) and os.path.abspath(got) != os.path.abspath(real),
          'real=%r' % real)

    # G2 —— 真实家目录零新增（"用户可感知"的那一条）
    after = _real_home_listing()
    new = sorted(set(after) - set(home_before))
    check('G2 跑完整套 CLI 矩阵后，真实 ~/.mpltweak 无任何新增文件', not new,
          '新增=%s（跑前=%s / 跑后=%s）' % (new, home_before, after))
    check('G2b 真实 ~/.mpltweak/first_open_shown 不存在',
          not os.path.exists(os.path.join(real, 'first_open_shown')),
          '跑后目录内容=%s' % after)

    # G3 —— 护栏有牙（安全版：假家目录，绝不碰真实家目录）
    fake = os.path.join(_TMPROOT, 'fake_home')
    env = _cli_env()
    env.pop('MPLTWEAK_HOME', None)            # 摘掉隔离
    env.pop('MPLTWEAK_NO_HINT', None)         # 别被宿主静默掉
    env['HOME'] = fake                        # POSIX 的 ~
    env['USERPROFILE'] = fake                 # Windows 的 ~
    os.makedirs(fake, exist_ok=True)
    r3 = subprocess.run([sys.executable, '-c', _TEETH_PROBE, src],
                        capture_output=True, text=True, encoding='utf-8',
                        errors='replace', env=env, cwd=ROOT, timeout=300)
    lines = [l.strip() for l in (r3.stdout or '').splitlines() if l.strip()]
    stamp = lines[0] if lines else ''
    shown = lines[1] if len(lines) > 1 else ''
    fake_stamp = os.path.join(fake, '.mpltweak', 'first_open_shown')
    check('G3 【有牙】摘掉 MPLTWEAK_HOME 后，戳真的落进（假）用户家目录',
          os.path.exists(fake_stamp),
          'rc=%s %s 戳=%r（期望 %r）' % (r3.returncode, shown, stamp, fake_stamp))
    check('G3b 该探针没有把戳写进**真实**家目录',
          not os.path.exists(os.path.join(real, 'first_open_shown')),
          '真实目录内容=%s' % _real_home_listing())


def _ui_literals(rel, extra=()):
    """AST：列出文件里 **print / sys.stderr.write 实参**中的中文模板（去重，排除 docstring）。

    ``extra``：额外当作"用户可见出口"的函数名（t79 起用 `_log/_info/_toast/
    _set_title_hint` 覆盖 toolbox 的交互/调试行）。
    **已经包进 `_msg.t_ui(...)` 的字面量不再计入**（否则包完还报"漏登记"）。
    """
    import ast
    path = os.path.join(ROOT, rel)
    tree = ast.parse(open(path, encoding='utf-8').read())
    docs = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            body = getattr(node, 'body', None)
            if body and isinstance(body[0], ast.Expr) \
                    and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                docs.add(id(body[0].value))
    wrapped = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, 'attr', '') == 't_ui':
            for a in list(node.args) + [k.value for k in node.keywords]:
                for sub in ast.walk(a):
                    if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                        wrapped.add(id(sub))
    out = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        nm = getattr(node.func, 'attr', getattr(node.func, 'id', ''))
        if nm not in ('print', 'write') and nm not in extra:
            continue
        for a in list(node.args) + [k.value for k in node.keywords]:
            for sub in ast.walk(a):
                if isinstance(sub, ast.Constant) \
                        and isinstance(sub.value, str) and id(sub) not in docs \
                        and id(sub) not in wrapped \
                        and any('\u4e00' <= c <= '\u9fff' for c in sub.value):
                    out.add(sub.value)
    return out


# ---------------------------------------------------------------------------
# §9 t64：用户可见文案的 i18n 契约（doctor / launch / describe + toolbox 横幅 2 行）
#
# 判据（与 t63 §98 同族）：
#   ① 覆盖：launch.py / describe.py 里**会打到 stdout/stderr** 的中文模板必须全部登记
#      在 messages.UI_ZH_TO_EN（漏一条就红 —— 这就是"系统扫一遍"的网）；
#   ② en 每条纯 ASCII，且 `%` 占位符个数与 zh 一致；
#   ③ zh 下 t_ui 原样返回（逐字不变）；
#   ④ 【有牙】把某条 en 改回中文 ⇒ ② 必红（teeth 脚本 t64_probe/teeth64.py）。
# 已知豁免（船长 2026-10-14 裁定"只登记、不改"）：cli.py:22 的 --help epilog、
# revert.py 3 条、mcp_server.py 1 条、toolbox.py 其余 49 条交互状态/调试行 ——
# 它们不在 t64 的验收路径（doctor/launch/describe/check 的运行时输出）上。
# （t78/t79 已撤掉前三条与"toolbox 交互/调试行"那条豁免；见下面 ①/①b 的覆盖式判据。）
#
# ---------------------------------------------------------------------------
# 【留痕 · t89（2026-10-14）：toolbox 的**帮助覆盖层 keymap 表**为什么**不该**包进
#  `UI_ZH_TO_EN`】—— 写给下一个看到这张表的人，**别再"顺手包装修一下"**：
#   1) `toolbox._KEYMAP` 是**模块级双语数据表**（`{'zh': _ZH, 'en': _EN}`）：键是**语言名**，
#      标签是**值** —— 与 `UI_ZH_TO_EN`（**模板 → 译文** 的查表）**不是一回事**。
#      渲染处已经按语言取表（`_fill_help_fig` → `_grps = _KEYMAP.get(lang, _ZH)`），
#      窗口内还有语言切换（`L` 键 / 按钮）；把标签再包一层 = **把"数据"当"模板"用**。
#   2) 三类分流结论（t79 判据）：纯人读 **35 条标签 + 10 条组标题**；协议/分帧载荷 **0**；
#      **被 `==`/`in` 比较或当字典键 = 0**（`_KEYMAP` 的键是 `'zh'`/`'en'`，标签是值）。
#   3) 因此这一组的棘轮取「**结构同步 + en 纯 ASCII + zh 非空**」（见 ①c 三条断言），
#      **不是** `_ui_literals(extra=…)` —— 后者是**调用点**口径，而这里是模块级数据表。
# ---------------------------------------------------------------------------
_TOOLBOX_BANNERS = (
    '[mpltweak] 模式=重图（ghost+blit）全量重绘=%s 吸附=%s 导出=%s',
    '[mpltweak] 拖拽/框选/多选都走 blit 预览（松手或停顿才真正落位）；'
    '若下面出现 blit 降级提示，说明该后端不支持 blit，会明显卡',
)


def run_ui_i18n():
    # t78：en 下 `mpltweak --help` 必须纯 ASCII，且仍声明退出码集合
    import subprocess as _sp78
    _e78 = dict(os.environ, MPLBACKEND='Agg',
                PYTHONIOENCODING='utf-8', MPLTWEAK_LANG='en')
    _r78 = _sp78.run([sys.executable, '-m', 'mpltweak',
                      '--help'], cwd=ROOT, capture_output=True,
                     env=_e78, timeout=300)
    _o78 = (_r78.stdout + _r78.stderr).decode('utf-8',
                                              'replace')
    check('§9 en 下 cli --help 纯 ASCII 且仍声明 0 / 1 / 2 / 4',
          _r78.returncode == 0 and _o78.isascii()
          and '0 / 1 / 2 / 4' in _o78 and 'is retired' in _o78
          and len(_o78.splitlines()) > 5 and '\\n' not in _o78,
          (_r78.returncode, _o78[:60]))
    import re
    from mpltweak import messages as _msg
    tbl = _msg.UI_ZH_TO_EN
    old = _msg.current_lang()
    try:
        # ① 覆盖：launch.py / describe.py 的 print/write 中文模板一条不漏
        for rel in ('src/mpltweak/launch.py',
                    'src/mpltweak/describe.py',
                    'src/mpltweak/cli.py'):
            lits = _ui_literals(rel)
            missing = sorted(t for t in lits if t not in tbl)
            check('%s 的用户可见中文模板全部登记（%d 条）' % (rel.split('/')[-1],
                                                       len(lits)),
                  not missing, '漏登记：%r' % (missing[:3],))
        # ①b toolbox：**交互/调试行**（拖拽提示、状态行、调试输出）也必须全登记
        #     （t79 撤掉了 t64 那条"toolbox 其余 49 条只登记、不改"的豁免）
        tb_lits = _ui_literals('src/mpltweak/toolbox.py',
                               extra=('_log', '_info', '_toast', '_set_title_hint'))
        missing_tb = sorted(t for t in tb_lits if t not in tbl)
        check('toolbox.py 交互/调试行的中文模板全部登记（%d 条，t79 撤豁免）'
              % len(tb_lits), not missing_tb, '漏登记：%r' % (missing_tb[:3],))
        tb = open(os.path.join(ROOT, 'src', 'mpltweak', 'toolbox.py'),
                  encoding='utf-8').read()
        check('toolbox 那 2 行横幅仍在表里、且已包 t_ui（不在"未包"清单里）',
              all(t in tbl for t in _TOOLBOX_BANNERS)
              and not any(t in tb_lits for t in _TOOLBOX_BANNERS),
              [t[:24] for t in _TOOLBOX_BANNERS])
        # ①c toolbox **帮助覆盖层**（keymap 表 `_ZH`/`_EN`，t89）：
        #     它**不走** `UI_ZH_TO_EN` —— 表本身是双语的（`_KEYMAP={'zh':_ZH,'en':_EN}`），
        #     窗口内还有语言切换（`L` 键 / 按钮）。所以棘轮在这里的形态是**结构同步**：
        #     zh/en 的**组数 + 每组的行数**必须一一对应（加一行 zh 却不加 en ⇒ 必红），
        #     且 en 的标签/键列/组标题必须纯 ASCII。
        #     （不能用 `_ui_literals(extra=…)`：那是**调用点**口径，而这里是模块级数据表。）
        from mpltweak import toolbox as _tb89
        _zh_shape = [len(rows) for _g, rows in _tb89._ZH]
        _en_shape = [len(rows) for _g, rows in _tb89._EN]
        check('§9 toolbox 帮助表 zh/en 结构一一对应（组数 + 每组行数；%d 组 / %d 行）'
              % (len(_zh_shape), sum(_zh_shape)),
              _zh_shape == _en_shape, (_zh_shape, _en_shape))
        _en_txt = ([g for g, _r in _tb89._EN]
                   + [k for _g, r in _tb89._EN for k, _d in r]
                   + [d for _g, r in _tb89._EN for _k, d in r])
        _bad_en = [x for x in _en_txt if not x.isascii()]
        check('§9 toolbox 帮助表 en（组标题+键列+标签，%d 条）全纯 ASCII'
              % len(_en_txt), not _bad_en, _bad_en[:3])
        _zh_txt = ([g for g, _r in _tb89._ZH]
                   + [d for _g, r in _tb89._ZH for _k, d in r])
        check('§9 toolbox 帮助表 zh 侧确实带中文（%d 条里 %d 条含中文 ⇒ 不是空表）'
              % (len(_zh_txt), sum(1 for x in _zh_txt
                                   if any('\u4e00' <= c <= '\u9fff' for c in x))),
              sum(1 for x in _zh_txt
                  if any('\u4e00' <= c <= '\u9fff' for c in x)) >= 30)
        # ①d t94（t84 取证后的收口）：只读预览的 colorbar 提示必须是**可直接粘贴的
        #     两行片段**（① `set_box_aspect(None)` + ② `set_axes_locator(None)`），
        #     并注明 **extend 情形两行都要加** —— 实测只 ① 时带 `extend='both'` 的
        #     colorbar 仍会回弹；en 必须纯 ASCII。
        import mpltweak.messages as _msg94
        _hint94 = _msg94.MESSAGES['apply_preview_hint']
        for _lg94 in ('zh', 'en'):
            _t94 = _hint94[_lg94]
            check('§9 t94 预览提示（%s）含**两行**片段 ① set_box_aspect + '
                  '② set_axes_locator' % _lg94,
                  'cb.ax.set_box_aspect(None)' in _t94
                  and 'cb.ax.set_axes_locator(None)' in _t94, _t94[:70])
            check('§9 t94 预览提示（%s）注明 extend 情形必须两行都加' % _lg94,
                  'extend' in _t94, _t94[:70])
        check('§9 t94 预览提示 en 纯 ASCII', _hint94['en'].isascii(),
              [c for c in _hint94['en'] if not c.isascii()][:3])
        _orig94 = dict(_hint94)
        try:
            _msg94.MESSAGES['apply_preview_hint'] = {
                'zh': _orig94['zh'].replace('cb.ax.set_axes_locator(None)', ''),
                'en': _orig94['en'].replace('cb.ax.set_axes_locator(None)', '')}
            _h94 = _msg94.MESSAGES['apply_preview_hint']
            _bad94 = ('cb.ax.set_box_aspect(None)' in _h94['zh']
                      and 'cb.ax.set_axes_locator(None)' in _h94['zh'])
        finally:
            _msg94.MESSAGES['apply_preview_hint'] = _orig94
        check('§9 t94【有牙】把提示里的 ② 去掉（只留 ①）⇒ 上面那条断言必红',
              _bad94 is False, '去掉 ② 后仍判"两行齐全"=%s' % (_bad94,))
        # ② en 纯 ASCII + 占位符个数一致
        bad_ascii, bad_ph = [], []
        for zh, en in tbl.items():
            if not en.isascii():
                bad_ascii.append(zh[:34])
            if len(re.findall(r'%[-#0-9.]*[sdgfrxXeE]', zh)) \
                    != len(re.findall(r'%[-#0-9.]*[sdgfrxXeE]', en)):
                bad_ph.append(zh[:34])
        check('UI_ZH_TO_EN 全部 en 纯 ASCII（%d 条）' % len(tbl),
              not bad_ascii, '非 ASCII：%r' % (bad_ascii[:3],))
        check('UI_ZH_TO_EN 占位符个数 zh/en 一致', not bad_ph,
              '不一致：%r' % (bad_ph[:3],))
        # ③ zh 原样返回（逐字不变）
        _msg.set_lang('zh')
        drift = [z for z in tbl if _msg.t_ui(z) != z]
        check('zh 下 t_ui 原样返回（逐字不变）', not drift, drift[:3])
        # ④ en 下 t_ui 真的取到表里的值（不是原样返回）
        _msg.set_lang('en')
        notrans = [z for z in tbl if _msg.t_ui(z) == z and tbl[z] != z]
        check('en 下 t_ui 走表（不是原样返回）', not notrans, notrans[:3])
    finally:
        _msg.set_lang(old)



# ---------------------------------------------------------------------------
# §11 t69（首次体验雷区 A4）：doctor 编码（P2-9）/ 脚本 stderr 汇总（P2-10）/
#      apply 预览明说"会改哪些字段"（P2-11）。
#
# 反向验证（teeth，t69_probe/teeth69.py）：① 去掉 doctor 的 `_force_utf8_streams()`
# ⇒ P2-9 那条红；② 去掉末尾汇总 ⇒ P2-10 那条红；③ 去掉预览的 `会改：` 行 ⇒ P2-11 红。
# ---------------------------------------------------------------------------
def run_t69():
    import json as _json
    import subprocess as _sp

    def _cli(argv, env=None):
        """真跑一次 CLI：返回 (rc, stdout, stderr)（要全量输出 + 可覆盖 env）。"""
        _e = _cli_env()
        if env:
            _e.update(env)
        _r = _sp.run([sys.executable, '-m', 'mpltweak'] + list(argv), cwd=ROOT,
                     capture_output=True, text=True, encoding='utf-8',
                     errors='replace', env=_e, timeout=300)
        return _r.returncode, (_r.stdout or ''), (_r.stderr or '')

    _fig = os.path.join(_TMPROOT, 't69_fig.py')
    with open(_fig, 'w', encoding='utf-8', newline='\n') as f:
        f.write("import os\n"
                "import sys\n"
                "import matplotlib\n"
                "matplotlib.use('Agg')\n"
                "import matplotlib.pyplot as plt\n"
                "print('script-noise-1', file=sys.stderr)\n"
                "print('script-noise-2', file=sys.stderr)\n"
                "fig = plt.figure(figsize=(15.0, 9.5))\n"
                "ax = fig.add_axes([0.10, 0.10, 0.40, 0.40])\n"
                "ax.plot([0, 1], [1, 0])\n"
                "fig.savefig(os.path.join(os.path.dirname("
                "os.path.abspath(__file__)), 'o.png'), dpi=100)\n")
    _p = os.path.join(_TMPROOT, 't69_params.json')
    with open(_p, 'w', encoding='utf-8', newline='\n') as f:
        _json.dump({'version': 4, 'script': 't69_fig.py', 'fig_index': 0,
                    'n_figs': 1, 'figsize_in': [7.5, 4.75],
                    'axes': [{'index': 0, 'pos': [0.2, 0.2, 0.5, 0.5]}]}, f)

    # ---- P2-11：预览明说会改哪些字段（zh / en）----
    _rc1, _o1, _e1 = _cli(['apply', _fig, '--params', _p])
    check('§11[P2-11] zh 预览列出"会改"的字段（figsize 旧→新）',
          _rc1 == 0 and '会改：' in _o1 and 'figsize (15, 9.5) → (7.5, 4.75)' in _o1,
          _o1[-400:])
    _rc2, _o2, _e2 = _cli(['apply', _fig, '--params', _p],
                          env={'MPLTWEAK_LANG': 'en'})
    check('§11[P2-11] en 预览同样列出（ASCII 箭头）',
          _rc2 == 0 and 'will change:' in _o2
          and 'figsize (15, 9.5) -> (7.5, 4.75)' in _o2, _o2[-400:])
    check('§11[P2-11] en 下整份预览输出仍是纯 ASCII', _o2.isascii(), _o2[:160])
    check('§11[P2-11] 明说"未列出的字段本次不动"（与没动的区分开）',
          '未列出的字段本次不动' in _o1 or 'fields not listed' in _o2)

    # ---- P2-9：doctor 的输出显式 UTF-8 ----
    # **单元级判据**（船长 2026-10-14 裁定）：直接观察 doctor() 对两条流的
    # reconfigure 调用 —— 端到端那条在本机**天然无鉴别力**（变异后子进程仍产出
    # 可被 UTF-8 解码的字节，怀疑编码继承链），所以牙必须长在这里。
    class _FakeStream:
        def __init__(self):
            self.encoding = 'gbk'
            self.calls = []
            self.text = ''

        def reconfigure(self, **kw):
            self.calls.append(kw)
            if kw.get('encoding'):
                self.encoding = kw['encoding']

        def write(self, s):
            self.text += s
            return len(s)

        def flush(self):
            pass

    from mpltweak import launch as _l69
    _fo, _fe = _FakeStream(), _FakeStream()
    _old_streams = (sys.stdout, sys.stderr)
    sys.stdout, sys.stderr = _fo, _fe
    try:
        _l69.doctor()
    finally:
        sys.stdout, sys.stderr = _old_streams
    check('§11[P2-9] doctor() 把两条流显式切到 utf-8（单元级：删掉那次调用必红）',
          _fo.encoding == 'utf-8' and _fe.encoding == 'utf-8'
          and bool(_fo.calls) and bool(_fe.calls),
          (_fo.encoding, _fe.encoding, _fo.calls, _fe.calls))
    check('§11[P2-9] doctor 的人读输出确实走这两条流（夹具有效性自证）',
          ('开窗后端' in _fo.text) or ('window backend' in _fo.text),
          _fo.text[:90])
    # 补充（端到端；**本机无鉴别力**，价值在真机 PowerShell 管道）
    _env = _cli_env()
    _env.pop('PYTHONIOENCODING', None)          # 复现用户现场：只有平台默认编码
    _r9 = _sp.run([sys.executable, '-m', 'mpltweak.cli', 'doctor'], cwd=ROOT,
                  capture_output=True, env=_env, timeout=300)
    try:
        _t9 = _r9.stdout.decode('utf-8')
        _ok9 = True
    except UnicodeDecodeError:
        _t9, _ok9 = '', False
    check('§11[P2-9]（补充·本机无鉴别力）管道下 doctor 输出可按 UTF-8 解码',
          _ok9 and (('开窗后端' in _t9) or ('window backend' in _t9)),
          (_ok9, _r9.stdout[:60]))

    # ---- P2-10：脚本自己刷 stderr 时末尾给汇总，且**不吞**脚本输出 ----
    _rc10, _o10, _e10 = _cli(['launch', _fig, '--dry-run'])
    _want10 = ('[launch] 提示：上面的 stderr 里有 2 行来自**你的脚本自身**'
               '（工具自己的提示都带 [launch]/[mpltweak] 前缀）')
    _hint10 = _is_headless_out(_e10)
    if not _hint10:
        check('§11[P2-10] 脚本自己的 stderr 原样保留（不吞）',
              _rc10 == 0 and 'script-noise-1' in _e10 and 'script-noise-2' in _e10,
              _e10[-300:])
        check('§11[P2-10] 汇总行的**精确文本 + 计数**（删掉汇总必红）',
              _want10 in _e10, _e10[-300:])
    else:
        # t114：无后端分支 —— 产品**如实**返回 4 并给出无头提示（不许静默 rc=0）。
        check('§11[P2-10]（无后端）launch --dry-run 如实返回 4（不是静默 rc=0）',
              _rc10 == 4, (_rc10, _e10[-200:]))
        # t117：非空话 —— 无后端时必须给出**可操作**建议（不是只贴一句“不可用”）。
        check('§11[P2-10]（无后端）给出可操作建议（说清为什么开不了窗）',
              ('pip install "mpltweak[qt]"' in _e10) or ('python3-tk' in _e10),
              _e10[-200:])


# ---------------------------------------------------------------------------
# §12 t91：doctor 严格校验（未知选项/多余位置参数 ⇒ rc=2；--json 明拒；-h ⇒ rc=0）
# 背景：doctor 过去**静默接受**未知选项/多余参数（rc=0），与 --help 里"未知选项 = 2"
# 的口径打架；最危险的是 `doctor --json` —— 用户/agent 以为拿到机器可读结果，实际被
# 静默忽略（"静默撒谎"）。
# 反向验证（teeth）：把 `cli.py` doctor 分支的四路校验改回去 ⇒ 下面断言必红。
# ---------------------------------------------------------------------------
def run_doctor_strict():
    import subprocess as _sp12

    def _d(lang, *argv):
        _e = _cli_env()
        _e['MPLTWEAK_LANG'] = lang
        _r = _sp12.run([sys.executable, '-m', 'mpltweak'] + list(argv),
                       cwd=ROOT, capture_output=True, env=_e, timeout=300)
        return _r.returncode, (_r.stdout + _r.stderr).decode('utf-8',
                                                             'replace')

    for _lang in ('zh', 'en'):
        _rc0, _o0 = _d(_lang, 'doctor')
        check('§12[%s] doctor（无参）仍 rc=0 且照常自检' % _lang,
              _rc0 == 0 and 'python' in _o0, (_rc0, _o0[:60]))
        _rch, _oh = _d(_lang, 'doctor', '-h')
        check('§12[%s] doctor -h ⇒ rc=0（用法）' % _lang,
              _rch == 0 and 'doctor' in _oh, (_rch, _oh[:60]))
        _rcj, _oj = _d(_lang, 'doctor', '--json')
        check('§12[%s] doctor --json ⇒ rc=2 且**明拒**（不静默忽略）' % _lang,
              _rcj == 2 and ('--json' in _oj), (_rcj, _oj[:80]))
        _rcb, _ob = _d(_lang, 'doctor', '--bogus')
        check('§12[%s] doctor --bogus ⇒ rc=2' % _lang, _rcb == 2, _rcb)
        _rce, _oe = _d(_lang, 'doctor', 'extra.py')
        check('§12[%s] doctor extra.py ⇒ rc=2（多余位置参数不再被吞）' % _lang,
              _rce == 2, _rce)
        if _lang == 'en':
            _all = _o0 + _oh + _oj + _ob + _oe
            check('§12 en 下 doctor 各路径输出纯 ASCII', _all.isascii(),
                  _all[:80])

# ---------------------------------------------------------------------------
# §13 t90：`check --fig K`（K 越界）提示走 messages（en 纯 ASCII）+ 单图措辞
# 反向验证（teeth）：把 `UI_ZH_TO_EN` 里这两条 en 值改回中文 ⇒ §9 的
# `UI_ZH_TO_EN 全部 en 纯 ASCII` 必红。
# ---------------------------------------------------------------------------
def run_fig_msg():
    import subprocess as _sp13
    import os as _os13
    _d = _os13.path.join(_os13.path.dirname(_os13.path.abspath(__file__)),
                         '.tmp_t90')
    _os13.makedirs(_d, exist_ok=True)
    for _name, _n in (('one.py', 1), ('two.py', 2)):
        _src = ["import os", "import matplotlib",
                "matplotlib.use('Agg')",
                "import matplotlib.pyplot as plt"]
        for _i in range(_n):
            _src += ['fig%d, ax%d = plt.subplots(figsize=(6.0, 4.0))'
                     % (_i, _i), 'ax%d.plot([0, 1], [1, 0])' % _i]
        _fp = _os13.path.join(_d, _name)
        with open(_fp, 'w', encoding='utf-8', newline='\n') as _f:
            _f.write('\n'.join(_src) + '\n')
    for _lang in ('zh', 'en'):
        for _name in ('one.py', 'two.py'):
            _e = _cli_env()
            _e['MPLTWEAK_LANG'] = _lang
            _r = _sp13.run([sys.executable, '-m', 'mpltweak', 'check',
                            _os13.path.join(_d, _name), '--fig', '9'],
                           cwd=_d, capture_output=True, env=_e,
                           timeout=300)
            _o = (_r.stdout + _r.stderr).decode('utf-8', 'replace')
            check('§13[%s] %s --fig 9 ⇒ rc=2' % (_lang, _name),
                  _r.returncode == 2, _r.returncode)
            check('§13[%s] %s 提示里“第/张图不存在”措辞正确'
                  % (_lang, _name),
                  ('张图不存在' in _o) if _lang == 'zh'
                  else ('does not exist' in _o), _o[:80])
            if _lang == 'en':
                check('§13 en 下提示纯 ASCII', _o.isascii(), _o[:80])
            if _name == 'one.py':
                check('§13[%s] 单图时不说“0..0 之间的序号”' % _lang,
                      ('0..0' not in _o), _o[:90])

# ---------------------------------------------------------------------------
# §14 t83：`describe -o` 自动建父目录（规范位置 `.tweak_params/<脚本>.json`）+
# 写失败给可操作文案（不再谎称“这是工具的 bug / 已回滚”）
# 反向验证（teeth）：把 `describe.py` 里建父目录那两行删掉 ⇒ 本节指定断言必红。
# ---------------------------------------------------------------------------
def run_describe_o():
    import os as _os14
    import subprocess as _sp14
    _d = _os14.path.join(_os14.path.dirname(_os14.path.abspath(__file__)),
                         '.tmp_t83')
    _os14.makedirs(_d, exist_ok=True)
    _fp = _os14.path.join(_d, 'fig1.py')
    _src = (
        "import os\n"
        "import matplotlib\n"
        "matplotlib.use('Agg')\n"
        "import matplotlib.pyplot as plt\n"
        "fig, ax = plt.subplots()\n"
        "ax.plot([0, 1], [1, 0])\n"
        "fig.savefig(os.path.join(os.path.dirname(os.path.abspath(__file__)),"
        " 'o.png'), dpi=80)\n"
    )
    with open(_fp, 'w', encoding='utf-8', newline='\n') as _f:
        _f.write(_src)
    _out = _os14.path.join('.tweak_params', 'fig1.json')
    for _lang in ('zh', 'en'):
        _e = _cli_env()
        _e['MPLTWEAK_LANG'] = _lang
        _r = _sp14.run([sys.executable, '-m', 'mpltweak', 'describe',
                        'fig1.py', '-o', _out],
                       cwd=_d, capture_output=True, env=_e, timeout=300)
        _o = (_r.stdout + _r.stderr).decode('utf-8', 'replace')
        _made = _os14.path.exists(_os14.path.join(_d, _out))
        check('§14[%s] describe -o .tweak_params/x.json ⇒ rc=0' % _lang,
              _r.returncode == 0, (_r.returncode, _o[:80]))
        check('§14[%s] 父目录被自动创建、文件已写入' % _lang, _made, _out)
        check('§14[%s] 输出里没有“工具 bug / 已回滚”这类假归因' % _lang,
              ('bug' not in _o.lower()) and ('已回滚' not in _o), _o[:90])
        if _lang == 'en':
            check('§14 en 下输出纯 ASCII', _o.isascii(), _o[:90])
    _r2 = _sp14.run([sys.executable, '-m', 'mpltweak', 'describe', 'fig1.py',
                     '-o', '.tweak_params'],
                    cwd=_d, capture_output=True, env=_e, timeout=300)
    _o2 = (_r2.stdout + _r2.stderr).decode('utf-8', 'replace')
    check('§14 -o 给目录 ⇒ rc=2 且明说“是一个目录/is a directory”',
          _r2.returncode == 2 and ('是一个目录' in _o2
                                   or 'is a directory' in _o2), _o2[:90])


# ---------------------------------------------------------------------------
# §15 t93：`doctor` 拒绝未知选项时**点名**被拒参数（与 argparse 家族同形）
# 反向验证（teeth）：把 cli.py 里那行 `schema_unknown_args` 去掉 ⇒ 本节必红。
# ---------------------------------------------------------------------------
def run_doctor_names():
    import subprocess as _sp15
    _cases = (
        (('doctor', 'x.py', '--nope'), '--nope'),
        (('doctor', '--nope'), '--nope'),
        (('doctor', '--json'), '--json'),
    )
    for _lang in ('zh', 'en'):
        _e = _cli_env()
        _e['MPLTWEAK_LANG'] = _lang
        for _argv, _want in _cases:
            _r = _sp15.run([sys.executable, '-m', 'mpltweak'] + list(_argv),
                           cwd=ROOT, capture_output=True, env=_e, timeout=300)
            _o = (_r.stdout + _r.stderr).decode('utf-8', 'replace')
            check('§15[%s] %s ⇒ rc=2' % (_lang, ' '.join(_argv)),
                  _r.returncode == 2, _r.returncode)
            check('§15[%s] %s ⇒ 输出**点名**被拒参数 %s'
                  % (_lang, ' '.join(_argv), _want), _want in _o, _o[:90])
            check('§15[%s] %s ⇒ 不出现未登记的键名'
                  % (_lang, ' '.join(_argv)),
                  'schema_unknown_args' not in _o, _o[:90])
            if _lang == 'en':
                check('§15 en 下输出纯 ASCII', _o.isascii(), _o[:90])

# =====================================================================
# §10 / t5（MAINTENANCE §25.11）：帮助覆盖层**离屏可测**
#
# 缺口：帮助窗口（`?` 覆盖层）的真实内容从没被自动验证过。渲染那一步
# （`_fill_help_fig`）只需要一个 figure，但**窗口调用**原先散在 `_show_help_fig` /
# `_toggle_help_lang` 里，且每条都包着 `except Exception: pass` ⇒ 离屏（Agg /
# 无窗口）时它们**静默失败**，于是"开窗时到底设没设标题、有没有真的 show()"
# 没人证明得了（§25.11 的原话：实际渲染从没被自动验证过）。
#
# 本节的解法（配合 toolbox 新抽的薄层 `_HelpWindowOp` / `_HELP_WIN`）：
# ① 把 `_HELP_WIN` 换成**记录用的假窗口层** ⇒ `_show_help_fig()` /
#    `_toggle_help_lang()` 在离屏 Agg 下能整条跑完，并能断言窗口调用**真的发生**
#    （含"取不到 window ⇒ 退回 show()"这条回退链）；
# ② 把渲染结果钉成基线：分组数、每组行数、元素个数、两列布局边界（左列最低点
#    不越界、同列不重叠、不超出画布）—— 数字就是 0.1.12 的实测值，改布局会红；
# ③ **有牙**：把某一组行数改错（少一行）⇒ 行数基线与元素数基线**双双必红**，
#    还原即绿。
#
# 边界（别误读）：本节证明的是「**离屏可测 + 内容与几何符合基线 + 窗口调用真的
# 发生**」；**真机观感**（字体、清晰度、2x 缩放、按钮手感）仍需用户开窗看。
# **不声称"真机已看"**。
# =====================================================================
_T5_GROUP_ROWS = [4, 3, 2, 2, 6, 2, 4, 1, 6, 4, 3]      # 0.1.12 实测（zh/en 同构）
_T5_ROWS_TOTAL = 37
_T5_TEXTS = 89        # 1 标题 + 11 组标题 + 37×2（键帽+说明）+ 2 语言按钮 + 1 底部提示
_T5_CARDS = 11        # 每组一张卡片
_T5_BARS = 12         # 顶部强调条 1 + 每组左侧彩条 11
_T5_W, _T5_H = 792.0, 968.0        # figsize=(7.2, 8.8) × 基准 dpi 110（逻辑像素）
_T5_MARGIN = 26.0
_T5_LEFT_BOTTOM = 39.0            # 左列最低卡片 y0（实测）
_T5_RIGHT_BOTTOM = 130.0          # 右列最低卡片 y0（实测）


class _FakeWinLayer(object):
    '''假窗口层：记录调用，并**故意**让 `window()` 抛错 —— 好覆盖"置顶取不到窗口
    就退回 show()"那条回退链（离屏真跑时走的也正是这条）。'''

    def __init__(self):
        self.calls = []

    def set_title(self, manager, title):
        self.calls.append(('set_title', title))

    def show(self, manager):
        self.calls.append(('show',))

    def window(self, manager):
        self.calls.append(('window',))
        raise AttributeError('no window (offscreen)')

    def raise_window(self, window):
        self.calls.append(('raise',))

    def hide_toolbar(self, manager):
        self.calls.append(('hide_toolbar',))


def _t5_inline_metrics(tb, plt, lang):
    '''强制**内联渲染**路径（不看预渲染 PNG），把元素数与两列布局边界量出来。'''
    from matplotlib.patches import FancyBboxPatch, Rectangle
    fig = plt.figure(figsize=(7.2, 8.8), dpi=110)
    try:
        tb._render_help(fig, lang, tb._pick_ui_font())
        r = fig.canvas.get_renderer()
        cards = [a for a in fig.artists
                 if isinstance(a, FancyBboxPatch)
                 and a.get_facecolor()[:3] == (1.0, 1.0, 1.0)]
        bars = [a for a in fig.artists if isinstance(a, Rectangle)]
        boxes = []
        for c in cards:
            bb = c.get_window_extent(r)
            boxes.append((bb.x0, bb.y0, bb.x1, bb.y1))
        mid = _T5_W / 2.0
        left = sorted([b for b in boxes if b[2] <= mid], key=lambda b: -b[3])
        right = sorted([b for b in boxes if b[0] >= mid], key=lambda b: -b[3])
        overlap = 0
        for col in (left, right):
            for i in range(len(col) - 1):
                if col[i][1] < col[i + 1][3]:      # 上一张的底 < 下一张的顶 ⇒ 重叠
                    overlap += 1
        return dict(texts=len(fig.texts), cards=len(cards), bars=len(bars),
                    left=len(left), right=len(right),
                    left_bottom=(min(b[1] for b in left) if left else -1.0),
                    right_bottom=(min(b[1] for b in right) if right else -1.0),
                    x_min=(min(b[0] for b in boxes) if boxes else -1.0),
                    x_max=(max(b[2] for b in boxes) if boxes else -1.0),
                    overlap=overlap, lang_btns=len(tb._HELP_LANG_BTNS))
    finally:
        plt.close(fig)


def run_t5_help_overlay():
    import matplotlib
    try:
        matplotlib.use('Agg', force=True)      # 离屏：任何后端都拉到 Agg
    except TypeError:                          # matplotlib < 3.6 没有 force
        matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from mpltweak import toolbox as tb

    orig_win = tb._HELP_WIN
    orig_keymap = tb._KEYMAP
    orig_lang, orig_fig = tb._HELP_LANG, tb._HELP_FIG
    fake = _FakeWinLayer()
    try:
        # ---- ① 键位表结构基线（zh/en 同构）----
        zh_rows = [len(rows) for _, rows in tb._KEYMAP['zh']]
        en_rows = [len(rows) for _, rows in tb._KEYMAP['en']]
        check('§10① 键位表分组基线：%d 组 / 每组行数 %s / 共 %d 行'
              % (len(_T5_GROUP_ROWS), _T5_GROUP_ROWS, _T5_ROWS_TOTAL),
              zh_rows == _T5_GROUP_ROWS and sum(zh_rows) == _T5_ROWS_TOTAL,
              zh_rows)
        check('§10①b zh/en 分组同构（组数、每组行数一一对应）',
              en_rows == zh_rows
              and len(tb._KEYMAP['zh']) == len(tb._KEYMAP['en']), en_rows)

        # ---- ② 内联渲染（降级路径）的元素数 + 布局边界 ----
        for _lg in ('zh', 'en'):
            m = _t5_inline_metrics(tb, plt, _lg)
            check('§10② 内联渲染元素基线[%s]：texts=%d cards=%d bars=%d lang_btns=2'
                  % (_lg, _T5_TEXTS, _T5_CARDS, _T5_BARS),
                  m['texts'] == _T5_TEXTS and m['cards'] == _T5_CARDS
                  and m['bars'] == _T5_BARS and m['lang_btns'] == 2, m)
            check('§10②b 布局边界[%s]：左列 6 张最低 y0≈%.0f / 右列 5 张最低 y0≈%.0f'
                  '（均 > 页边 %.0f）、同列零重叠、x∈[%.0f,%.0f] 不出画布'
                  % (_lg, _T5_LEFT_BOTTOM, _T5_RIGHT_BOTTOM, _T5_MARGIN,
                     _T5_MARGIN, _T5_W - _T5_MARGIN),
                  m['left'] == 6 and m['right'] == 5
                  and abs(m['left_bottom'] - _T5_LEFT_BOTTOM) < 0.6
                  and abs(m['right_bottom'] - _T5_RIGHT_BOTTOM) < 0.6
                  and m['left_bottom'] > _T5_MARGIN
                  and m['right_bottom'] > _T5_MARGIN
                  and m['overlap'] == 0
                  and m['x_min'] >= _T5_MARGIN - 0.6
                  and m['x_max'] <= _T5_W - _T5_MARGIN + 0.6, m)

        # ---- ③ 生产路径：预渲染 PNG（用户环境零字体依赖那一条）----
        png = tb._load_help_png('zh')
        fig = plt.figure(figsize=(7.2, 8.8), dpi=110)
        try:
            tb._fill_help_fig(fig, 'zh')
            n_axes, n_texts = len(fig.axes), len(fig.texts)
        finally:
            plt.close(fig)
        if png is not None:
            check('§10③ 生产路径（预渲染 PNG %s）离屏可填充：1 个铺满 axes + 0 文字'
                  % (png.shape,), n_axes == 1 and n_texts == 0,
                  'axes=%d texts=%d' % (n_axes, n_texts))
        else:
            check('§10③ 包内没有预渲染 PNG ⇒ 降级内联渲染必须画出内容（不空图）',
                  n_texts > 0, 'axes=%d texts=%d' % (n_axes, n_texts))

        # ---- ④ 假窗口层：`_show_help_fig()` 整条跑通 ----
        tb._HELP_WIN = fake
        tb._HELP_FIG = None
        tb._HELP_LANG = 'zh'
        fake.calls[:] = []
        tb._show_help_fig()
        titles = [c[1] for c in fake.calls if c[0] == 'set_title']
        fig_now = tb._HELP_FIG
        check('§10④ `_show_help_fig()` 离屏整条跑通（假窗口层）：建图 + 设中文标题'
              ' + 隐藏工具栏 + show()',
              fig_now is not None and titles == ['mpltweak · 快捷键']
              and ('hide_toolbar',) in fake.calls and ('show',) in fake.calls
              and (len(fig_now.axes) >= 1 or len(fig_now.texts) >= 1),
              fake.calls)

        # 已开着再按一次 `?`：置顶分支取不到 window（离屏）⇒ 退回 show()，不重建
        fake.calls[:] = []
        tb._show_help_fig()
        check('§10④b 已开着再按 `?`：取不到 window ⇒ 退回 show()，且不重复建图/改标题',
              ('window',) in fake.calls and ('show',) in fake.calls
              and tb._HELP_FIG is fig_now
              and not [c for c in fake.calls if c[0] == 'set_title'],
              fake.calls)

        # ---- ⑤ 按 `L` 切语言：标题跟着换 + 重绘 ----
        fake.calls[:] = []
        tb._toggle_help_lang()
        en_titles = [c[1] for c in fake.calls if c[0] == 'set_title']
        check('§10⑤ 按 `L` 切语言：标题切英文 + 重绘后内容仍在（离屏可验证）',
              tb._HELP_LANG == 'en' and en_titles == ['mpltweak · Shortcuts']
              and tb._HELP_FIG is not None
              and (len(tb._HELP_FIG.axes) >= 1 or len(tb._HELP_FIG.texts) >= 1),
              en_titles)
        tb._toggle_help_lang()                 # 切回中文，保持全局状态干净

        # ---- ⑥ 有牙：把「样式」组（6 行）少一行 ⇒ 两条基线都得红 ----
        bad = list(tb._KEYMAP['zh'])
        gi = _T5_GROUP_ROWS.index(6) + 1       # 第二个 6 行组 = '样式'
        bad[gi] = (bad[gi][0], bad[gi][1][:-1])
        tb._KEYMAP = {'zh': bad, 'en': tb._KEYMAP['en']}
        rows_bad = [len(rows) for _, rows in tb._KEYMAP['zh']]
        m_bad = _t5_inline_metrics(tb, plt, 'zh')
        tb._KEYMAP = orig_keymap
        m_ok = _t5_inline_metrics(tb, plt, 'zh')
        red = (rows_bad != _T5_GROUP_ROWS) and (m_bad['texts'] != _T5_TEXTS)
        check('§10⑥【有牙】把「样式」组少一行 ⇒ 行数基线 %s 与元素数基线 %d 双双必红'
              '（少一行 = 少 2 个文字），还原即绿'
              % (_T5_GROUP_ROWS, _T5_TEXTS),
              red and rows_bad == _T5_GROUP_ROWS[:gi] + [_T5_GROUP_ROWS[gi] - 1]
              + _T5_GROUP_ROWS[gi + 1:]
              and m_bad['texts'] == _T5_TEXTS - 2 and m_bad['cards'] == _T5_CARDS
              and m_ok['texts'] == _T5_TEXTS
              and [len(rows) for _, rows in tb._KEYMAP['zh']] == _T5_GROUP_ROWS,
              'bad_texts=%d bad_rows=%s / ok_texts=%d'
              % (m_bad['texts'], rows_bad, m_ok['texts']))
    finally:
        tb._HELP_WIN = orig_win
        tb._KEYMAP = orig_keymap
        try:
            tb._close_help_fig()
        except Exception:                      # noqa: BLE001
            pass
        tb._HELP_LANG = orig_lang
        tb._HELP_FIG = orig_fig


def main():
    os.makedirs(_TMPROOT, exist_ok=True)
    # t31：先给**真实**家目录拍快照（任何子进程起来之前），跑完再对比零新增。
    _home_before = _real_home_listing()
    print('launch._apply_saved 顶层非对象 JSON 守卫回归（0.1.10）')
    run_guards()
    print()
    print('t44 无感化①：关窗后那条"可直接复制"的写回命令（纯函数级，不建窗）')
    run_next_step()
    print()
    print('t58 退出码矩阵（方案 A：0 / 1 / 2 / 4；3 已退役）—— 真起子进程跑 CLI')
    run_exit_matrix()
    print()
    print('t31 真实家目录护栏（防"测试写用户家目录"）')
    run_real_home_guards(_home_before)
    print()
    print('t64 用户可见文案 i18n 契约（doctor/launch/describe + toolbox 横幅 2 行）')
    run_ui_i18n()
    print()
    print('t69 首次体验雷区 A4：doctor 编码 / 脚本 stderr 汇总 / 预览列会改字段')
    run_t69()
    print()
    print('t91 doctor 严格校验（未知选项/多余参数/--json）')
    run_doctor_strict()
    print()
    print('t90 check --fig 越界提示（i18n + 单图措辞）')
    run_fig_msg()
    print()
    print('t83 describe -o 建父目录 + 可操作文案')
    run_describe_o()
    print()
    print('t93 doctor 拒绝未知参数时点名')
    run_doctor_names()
    print()
    print('t5 帮助覆盖层离屏可测（薄层注入 + 元素/分组/布局基线，§25.11）')
    run_t5_help_overlay()
    shutil.rmtree(_TMPROOT, ignore_errors=True)
    print()
    if _fails:
        print('FAILED: %d 项 -> %s' % (len(_fails), _fails))
        return 1
    print('ALL PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
