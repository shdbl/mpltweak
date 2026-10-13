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

# 界面语言钉死（与其它七个套件一致）：断言的是中文文案，别让宿主 locale 决定结论。
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
    env['PYTHONPATH'] = src
    env['MPLBACKEND'] = 'Agg'
    env['MPLTWEAK_LANG'] = 'zh'
    env['PYTHONIOENCODING'] = 'utf-8'
    return env


def _write_fixture(name, text):
    p = os.path.join(_TMPROOT, name)
    with open(p, 'w', encoding='utf-8', newline='\n') as f:
        f.write(text)
    return p


def _run_cli(argv):
    """真跑一次 CLI，返回 (rc, 第一条非空 stderr/stdout 行)。"""
    import subprocess
    r = subprocess.run([sys.executable, '-m', 'mpltweak'] + list(argv),
                       capture_output=True, text=True, encoding='utf-8',
                       errors='replace', env=_cli_env(),
                       cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       timeout=300)
    first = ''
    for line in ((r.stderr or '') + '\n' + (r.stdout or '')).split('\n'):
        if line.strip():
            first = line.strip()
            break
    return r.returncode, first


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
        rc, first = _run_cli(argv)
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


def main():
    os.makedirs(_TMPROOT, exist_ok=True)
    print('launch._apply_saved 顶层非对象 JSON 守卫回归（0.1.10）')
    run_guards()
    print()
    print('t44 无感化①：关窗后那条"可直接复制"的写回命令（纯函数级，不建窗）')
    run_next_step()
    print()
    print('t58 退出码矩阵（方案 A：0 / 1 / 2 / 4；3 已退役）—— 真起子进程跑 CLI')
    run_exit_matrix()
    shutil.rmtree(_TMPROOT, ignore_errors=True)
    print()
    if _fails:
        print('FAILED: %d 项 -> %s' % (len(_fails), _fails))
        return 1
    print('ALL PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
