# -*- coding: utf-8 -*-
# tests/sandbox/sandbox_env.py —— 沙箱子进程环境构造（测试侧工具，t9）。
#
# 关键：把沙箱目录（本文件所在目录，含 sitecustomize.py）**并进** PYTHONPATH，
# **绝不覆盖**已有项（如 mpltweak 的 <树>/src）—— Python 启动时会自动 import
# sitecustomize，守卫随即生效。
#
# t15：子进程 stdout/stderr 编码必须**钉死**成 UTF-8（CHILD_IO_ENCODING）。
# 根因：CPython 在**不设** PYTHONIOENCODING 的 GBK 控制台上，会把子进程 stdout/
# stderr 按 locale 编码（cp936）写进管道；而测试侧一律按 UTF-8 解码 → 守卫抛出的
# 中文 PermissionError 文案整段变成 U+FFFD，只有 traceback 里的 ASCII 片段
# （如 `, line 136, in _block`）幸存 → 7 项"守卫明明生效却判失败"的假红。
# 钉死编码后两端口径一致：断言既可依赖行为信号，也可安全匹配中文文案。
import os

SANDBOX_DIR = os.path.dirname(os.path.abspath(__file__))

#: 子进程输出编码（测试侧统一按此解码，见 test_sandbox._decode）。
CHILD_IO_ENCODING = 'utf-8'


def sandbox_env(playground_root, extra_pythonpath=(), *, disable_gui_ban=False,
                log_path=None, no_stdin=False, base_env=None):
    """构造"守卫已武装"的子进程环境。

    - playground_root: 游乐场根（MPLTWEAK_SANDBOX_ROOT）；守卫只允许在这里面写。
    - extra_pythonpath: 追加的 PYTHONPATH 项（如 <树>/src）。
    - disable_gui_ban: True → 设 MPLTWEAK_SANDBOX_NOGUI=1（只关禁 GUI，文件守卫不关）。
    - log_path: 守卫拦截日志（MPLTWEAK_SANDBOX_LOG）。
    - no_stdin: True → 设 MPLTWEAK_SANDBOX_NO_STDIN=1（input() 读即抛 EOFError，
      t10 无人值守真跑用，防"等人输入"卡到超时）。

    无论调用方是否指定，子进程输出编码一律钉死为 CHILD_IO_ENCODING（t15 根因修复）：
    不这么做的话 GBK 控制台下守卫的中文报错在管道里是 cp936 字节，测试侧按 UTF-8
    解码会得到乱码，导致"守卫已生效"被误判为失败。

    同理，**MPLBACKEND 一律硬钉 Agg**（不是 setdefault）：本机装了 PyQt5/tkinter，
    一旦宿主环境把 MPLBACKEND 设成 Qt5Agg/TkAgg，子进程就会落到交互后端，任何
    plt.show()/建图都可能真开窗抢焦点与鼠标（0.1.9 教训 + 2026-10-12 抢鼠标事故）。
    测试侧是最不能漏的一环——这里的子进程跑的正是**陌生脚本**。
    """
    env = dict(os.environ if base_env is None else base_env)
    env['PYTHONIOENCODING'] = CHILD_IO_ENCODING
    parts = [SANDBOX_DIR]
    cur = env.get('PYTHONPATH')
    if cur:
        parts.append(cur)              # 保留现有 PYTHONPATH —— 不能覆盖
    for x in extra_pythonpath:
        if x and x not in parts:
            parts.append(x)
    env['PYTHONPATH'] = os.pathsep.join(parts)
    env['MPLTWEAK_SANDBOX_ROOT'] = os.path.abspath(playground_root)
    env['MPLCONFIGDIR'] = os.path.join(os.path.abspath(playground_root),
                                       '.mplconfig')
    # 临时文件也必须落进游乐场（tempfile 类库侧写入）—— 否则 harness/脚本的
    # tempfile.mkstemp 会打在游乐场外被守卫拦下。
    _ptmp = os.path.join(os.path.abspath(playground_root), 'tmp')
    env['TMP'] = _ptmp
    env['TEMP'] = _ptmp
    env['TMPDIR'] = _ptmp
    env.setdefault('PYTHONDONTWRITEBYTECODE', '1')   # numpy 等 __pycache__ 不写盘
    env['MPLBACKEND'] = 'Agg'                        # 硬钉（覆盖宿主投毒，见 docstring）
    env.setdefault('MPLTWEAK_LANG', 'zh')
    # 开关类环境变量同理只能由**本函数的参数**决定：宿主环境里若残留
    # NOGUI=1（= 关掉禁 GUI 层）或 NO_STDIN=1，子进程的安全属性就被悄悄改掉了。
    if disable_gui_ban:
        env['MPLTWEAK_SANDBOX_NOGUI'] = '1'
    else:
        env.pop('MPLTWEAK_SANDBOX_NOGUI', None)
    if log_path:
        env['MPLTWEAK_SANDBOX_LOG'] = os.path.abspath(log_path)
    if no_stdin:
        env['MPLTWEAK_SANDBOX_NO_STDIN'] = '1'
    else:
        env.pop('MPLTWEAK_SANDBOX_NO_STDIN', None)
    return env


def plain_env(base_env=None):
    """不带守卫的对照环境（同一基座，只是不注入 sandbox 目录）。

    同样钉死输出编码 —— 对照用例（C1）与守卫用例必须同口径，否则对照里"删成功"
    的中文输出也会在同一种 GBK 控制台下被弄花。

    也一样**硬钉 MPLBACKEND=Agg**：对照脚本同样是陌生脚本，不能因为"没上守卫"
    就任它落到 Qt/Tk 后端开窗。
    """
    env = dict(os.environ if base_env is None else base_env)
    env['PYTHONIOENCODING'] = CHILD_IO_ENCODING
    env.setdefault('PYTHONDONTWRITEBYTECODE', '1')
    env['MPLBACKEND'] = 'Agg'                        # 硬钉（覆盖宿主投毒）
    env.setdefault('MPLTWEAK_LANG', 'zh')
    return env
