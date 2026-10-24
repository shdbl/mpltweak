# -*- coding: utf-8 -*-
"""`mpltweak demo`：**装上就能体验**（0.1.17 头号功能）。

一条命令做四件事：

1. 把随包的示例脚本复制到 ``./mpltweak-demo/demo_panels.py``（目录已存在就**复用不覆盖**
   —— 你可能已经把它改成自己的试验场）；
2. 开窗让你像在 PPT 里那样拖一拖（复用 launch 的后端探测 / Tk 回退 / rc=4 人话）；
3. 关窗后**只报告**：改了哪几处（人话）+ 逐行差异（复用 `apply --diff` 的机制）+ 下一行命令；
4. 加 ``--write`` 才落盘，并生成**改前 / 改后**两张对比图（`before.png` / `after.png`）。

设计纪律（每一条都是本版吃过的亏，别省）：

* **默认零侵入**：不带 ``--write`` 时示例脚本一个字节都不动（只读 + 报告）；
* **没改任何东西 ≠ 失败，也 ≠ 完成**：输出"你没有改动任何东西，所以什么都没写"；
* **无图形后端** ⇒ 走 launch 同一句人话 + 同一个退出码 4，绝不 Traceback；
* **离屏可测**：``--no-window``（配 ``--params``）跳过开窗，给测试/CI 用；
* 示例脚本用 ``fig.add_subplot(221)`` 逐个建轴 —— 不用元组解包、不用循环建轴，
  第一印象不能碰壁（那两类写法在 0.1.15 之前会生成跑不通的代码）。
"""
import io
import json
import os
import shutil
import subprocess
import sys

from .. import messages as _msg

EX_NAME = 'demo_panels.py'
DIR_NAME = 'mpltweak-demo'


def example_text():
    """随包示例脚本的**文本**（`importlib.resources`：装成 wheel 也取得到）。"""
    try:
        from importlib.resources import files
    except ImportError:                                 # pragma: no cover - <3.9
        from importlib_resources import files           # type: ignore
    return files(__name__).joinpath(EX_NAME).read_text(encoding='utf-8')


def stamp_path(environ=None):
    """``~/.mpltweak/first_demo_shown``（机制仿 launch 的 first_open_shown）。"""
    from ..launch import _mpltweak_home
    return os.path.join(_mpltweak_home(environ), 'first_demo_shown')


def first_demo(environ=None):
    """这次是不是**第一次**跑 demo（给 agent 判断要不要多提醒一句）。"""
    return not os.path.exists(stamp_path(environ))


def _touch_stamp(environ=None):
    p = stamp_path(environ)
    try:
        d = os.path.dirname(p)
        if d and not os.path.isdir(d):
            os.makedirs(d, exist_ok=True)
        with io.open(p, 'w', encoding='utf-8') as f:
            f.write('1\n')
    except OSError:
        pass                        # 戳记失败绝不影响 demo 本身（它只是提示用的）

def prepare(demo_dir):
    """准备 ``demo_dir/demo_panels.py``：已存在就**复用不覆盖**。

    返回 ``(script_path, created)``：created=True 表示这次是新写出来的。
    覆盖是绝对不做的（用户可能已经把它改成自己的试验场），只在一次都没写过时落盘。
    """
    script = os.path.join(demo_dir, EX_NAME)
    if os.path.isfile(script):
        return script, False
    os.makedirs(demo_dir, exist_ok=True)
    with io.open(script, 'w', encoding='utf-8', newline=chr(10)) as f:
        f.write(example_text())
    return script, True


def _apply_json(args, cwd):
    """在**示例目录里**跑一次 `mpltweak apply`，把它的 `--json` 载荷拿回来。

    为什么不自己算 diff：`--diff` 的产物（行号/类型/新旧行、以及"同源"保证）是这一版
    反复验证过的那套；在 demo 里另算一遍等于再养一个会分叉的实现。

    为什么走**子进程**而不是进程内 import：第一次实测进程内调用会污染输出 ——
    `apply` 的 `--json` 把载荷打到真 stdout（它内部有自己的 stdout 引用），于是
    demo 的 stdout 里成了"两个 JSON 粘一起"，agent 侧直接解析失败。子进程还顺带让
    "示例目录"成为干净的工作目录（`apply` 按 cwd 解析相对路径）。
    """
    cmd = [sys.executable, '-m', 'mpltweak', 'apply'] + [str(a) for a in args]
    try:
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, timeout=1800)
    except (OSError, subprocess.SubprocessError) as e:
        return 1, {'ok': False, 'error': str(e), 'files': []}
    txt = r.stdout.decode('utf-8', 'replace')
    try:
        payload = json.loads(txt[txt.index('{'):txt.rindex('}') + 1])
    except ValueError:
        payload = {'ok': False, 'files': [],
                   'error': (r.stderr.decode('utf-8', 'replace') or txt)[-800:]}
    return r.returncode, payload

def change_lines(files):
    """`--json` 的 `changes`：**人话字符串数组**（agent 直接转述，不让它自己拼结构）。

    每条形如 ``ax0.pos 改成 [0.1, 0.1, 0.5, 0.5]`` / ``ax1.label_fontsize 改成 18``。
    只报"这次确实要改的字段"（来自 apply 的 `files[].changes`，那是写回真正落地的清单），
    再带上参数里的目标值；没有目标值的字段只报字段名（不编造数值）。
    """
    out = []
    for f in files or []:
        pdata = f.get('_params') or {}
        for key in f.get('changes') or []:
            ax, _dot, field = key.partition('.')
            want = None
            try:
                idx = int(ax[2:])
                for a in pdata.get('axes') or []:
                    if a.get('index') == idx:
                        want = a.get(field)
                        break
            except (ValueError, TypeError):
                pass
            out.append('%(field)s 改成 %(val)s' % {
                'field': key, 'val': ('%r' % (want,)) if want is not None
                else '(参数里没给值)'})
    return out


def run_script(script):
    """在示例脚本自己的目录里跑一遍（产出图），返回 rc。**不碰 cwd**。"""
    env = dict(os.environ, MPLBACKEND='Agg')
    try:
        r = subprocess.run([sys.executable, os.path.basename(script)],
                           cwd=os.path.dirname(script) or '.',
                           capture_output=True, env=env, timeout=600)
    except (OSError, subprocess.SubprocessError) as e:
        return 1, str(e)
    tail = (r.stdout + r.stderr).decode('utf-8', 'replace')[-400:]
    return r.returncode, tail

def _newest_png(d):
    """示例脚本目录里**最新**的 PNG（示例脚本自己存的那张）。没有 → None。"""
    try:
        names = [os.path.join(d, n) for n in os.listdir(d) if n.endswith('.png')]
    except OSError:
        return None
    names = [p for p in names if os.path.isfile(p)]
    return max(names, key=os.path.getmtime) if names else None


def main(argv=None):
    """`mpltweak demo [--write] [--json] [--params P] [--no-window]` 的入口。"""
    import argparse
    ap = argparse.ArgumentParser(prog='mpltweak demo', add_help=True)
    ap.add_argument('--write', action='store_true',
                    help=_msg.t_ui('关窗后把改动落盘，并生成 before.png / after.png '
                                   '对比图（默认只报告、一个字节都不写）'))
    ap.add_argument('--json', action='store_true',
                    help=_msg.t_ui('往 stdout 打机器可读 JSON（人读信息走 stderr）'))
    ap.add_argument('--dir', default=None,
                    help=_msg.t_ui('示例目录（默认当前目录下的 mpltweak-demo/）'))
    ap.add_argument('--params', default=None,
                    help=_msg.t_ui('既有参数文件：给了就不需要关窗后重新落参数'
                                   '（测试/CI 用，跳过开窗的一部分）'))
    ap.add_argument('--no-window', action='store_true',
                    help=_msg.t_ui('不开窗（配 --params 用；测试/CI 的非交互路径）'))
    args = ap.parse_args(argv)
    return _run(args)

def _emit(human, payload, as_json):
    """人读走 stderr、`--json` 走 stdout（仓库既有口径：机器通道只留数据）。"""
    if as_json:
        _msg.emit_json(payload)
        if human:
            sys.stderr.write(human)
    elif human:
        sys.stdout.write(human)


def _run(args):
    demo_dir = os.path.abspath(args.dir or os.path.join(os.getcwd(), DIR_NAME))
    first = first_demo()
    script, created = prepare(demo_dir)
    head = _msg.t('demo_header', dir=demo_dir,
                  note=_msg.t('demo_created' if created else 'demo_reused'))
    # ---- 1. 参数：给了 --params 就直接用（测试/CI），否则开窗让用户调 ----
    params = os.path.abspath(args.params) if args.params else None
    rc_window = 0
    if params is None:
        stamp_dir = os.path.join(demo_dir, '.tweak_params')
        os.makedirs(stamp_dir, exist_ok=True)
        params = os.path.join(stamp_dir, 'demo_panels.json')
        if not args.no_window:
            # 窗口开着的时候用户未必知道里面有速查表 —— 开窗前先打一句（走 stderr，
            # 不污染 --json 的 stdout）。不开窗时这句并进关窗报告，否则没人看得见。
            sys.stderr.write(_msg.t('demo_keys_hint') + chr(10))
            from ..launch import main as launch_main
            rc_window = launch_main([script, '--params', params])
            if rc_window == 4:                  # 无图形后端：launch 已打印人话
                _emit(head, {'ok': False, 'error_code': 'no_backend',
                             'stage': 'window', 'demo_dir': demo_dir,
                             'script': script, 'first_demo': first,
                             'compare_images': None, 'changes': [], 'diff': [],
                             'wrote': False}, args.json)
                return 4
            if rc_window != 0:
                return rc_window
    return _report(args, demo_dir, script, params, head, first,
                    keys_hint=(_msg.t('demo_keys_hint')
                               if params is not None or args.no_window
                               else None))

def _report(args, demo_dir, script, params, head, first,
            keys_hint=None):
    """关窗之后：报告改了哪几处 / 逐行差异 / 下一步；`--write` 才落盘 + 对比图。"""
    try:
        pdata = json.load(io.open(params, encoding='utf-8'))
    except (OSError, ValueError):
        pdata = None
    if not pdata:
        human = head + _msg.t('demo_nothing_changed') + (
            _msg.t('demo_first_hint') if first else '')
        _emit(human, {'ok': True, 'demo_dir': demo_dir, 'script': script,
                      'changes': [], 'diff': [], 'wrote': False,
                      'first_demo': first, 'compare_images': None}, args.json)
        _touch_stamp()
        return 0
    # 传**绝对路径**：describe 落下的 params 里 script 是绝对路径，传相对名
    # 会被 apply 判成「不是同一份脚本」（第一次实测：files 空 -> IndexError）。
    rc, payload = _apply_json([script, '--params', params, '--diff', '--json'],
                               demo_dir)
    files = payload.get('files') or [{}]     # 缺 files 也不许 IndexError
    for f in files:
        f['_params'] = pdata
    changes = change_lines(files)
    rows = files[0].get('diff') or []
    next_cmd = 'mpltweak demo --write'
    if not changes:
        # 没改任何东西：**不是失败、也不是完成** —— 如实说"什么都没写"
        human = head + _msg.t('demo_nothing_changed') + (
            _msg.t('demo_first_hint') + chr(10) if first else ''
        ) + _msg.t('demo_repeat_hint')
        _emit(human, {'ok': True, 'demo_dir': demo_dir, 'script': script,
                      'changes': [], 'diff': [], 'wrote': False,
                      'first_demo': first, 'compare_images': None}, args.json)
        _touch_stamp()
        return 0
    return _finish(args, demo_dir, script, params, pdata, head, first, changes,
                   rows, next_cmd, keys_hint=keys_hint)

def _finish(args, demo_dir, script, params, pdata, head, first, changes, rows,
            next_cmd, keys_hint=None):
    """最后一段：报告（+ 可选落盘与对比图）。"""
    out = [head] + ([keys_hint] if keys_hint else []) + [
        _msg.t('demo_changes_head', n=len(changes))]
    out += ['  - ' + c for c in changes]
    out.append(_msg.t('demo_diff_head', n=len(rows)))
    for r in rows[:8]:
        out.append('    L%d %s %s => %s'
                   % (r.get('line'), r.get('type'), r.get('old'), r.get('new')))
    wrote, cmp_imgs = False, None
    if args.write:
        before = _newest_png(demo_dir)
        _rc1, _tail1 = run_script(script)          # 改前那张图
        if before and os.path.isfile(before):
            _dst = os.path.join(demo_dir, 'before.png')
            shutil.copyfile(before, _dst)
        rc_w, pw = _apply_json([script, '--params', params, '--write',
                                  '--json'], demo_dir)
        wrote = rc_w == 0 and bool((pw.get('files') or [{}])[0].get('applied'))
        if wrote:
            _rc2, _tail2 = run_script(script)
            after = _newest_png(demo_dir)
            _a = os.path.join(demo_dir, 'after.png')
            if after and os.path.isfile(after):
                shutil.copyfile(after, _a)
            cmp_imgs = {'before': os.path.join(demo_dir, 'before.png'),
                        'after': _a}
            out.append(_msg.t('demo_wrote', script=script))
            out.append(_msg.t('demo_compare_paths', before=cmp_imgs['before'],
                              after=cmp_imgs['after']))
        else:
            out.append(_msg.t('demo_write_failed',
                              err=(pw.get('error') or pw.get('error_code') or '?')))
    else:
        out.append(_msg.t('demo_next_step', cmd=next_cmd))
    if first:
        out.append(_msg.t('demo_first_hint'))
    _emit(chr(10).join(out) + chr(10),
          {'ok': True, 'demo_dir': demo_dir, 'script': script,
           'changes': changes, 'diff': rows, 'wrote': wrote,
           'first_demo': first, 'compare_images': cmp_imgs}, args.json)
    _touch_stamp()
    return 0
