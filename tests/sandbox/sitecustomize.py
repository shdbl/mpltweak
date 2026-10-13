# -*- coding: utf-8 -*-
# tests/sandbox/sitecustomize.py —— mpltweak 测试文件系统沙箱（审计钩子，第 2 层防护）。
#
# 注入方式：把本文件所在目录加进子进程的 PYTHONPATH —— Python 启动时 site 模块会
# **自动 import** 名为 sitecustomize 的模块（无论它在哪个目录），本文件随即安装守卫。
#
# 守卫（sys.addaudithook）策略（进程分级）：
#   * 被防护进程 = "在跑用户脚本的进程"（sys.argv[0] 是 MPLTWEAK_SANDBOX_ROOT 内的
#     文件，即 mpltweak 的 verify 子进程 `python <游乐场脚本>`）→ **完整守卫**：
#       ① 文件写/删/改名/建目录/改权限/chdir 目标不在游乐场内 → PermissionError
#       ② 起子进程（subprocess / os.system / os.exec / os.spawn / os.popen）→ PermissionError
#       ③ 网络（socket.connect / connect_ex / sendmsg / sendto）→ PermissionError
#   * 其余进程（harness：apply / verify 本体 / collect 等，argv[0] 不在游乐场）→
#     **文件守卫**（① 同左；②③ 放行 —— harness 要自己 spawn verify 子进程）。
#     这样 verify_semantic 的 collect（runpy 执行脚本）里脚本的文件操作也受保护，
#     只有"子进程/网络"这两个口子留给 harness（已知边界，写进 SANDBOX.md）。
#   * 读取一律放行（库导入需要读 site-packages / 数据文件）。
#   * 未设 MPLTWEAK_SANDBOX_ROOT → 完全惰性（只打印未武装，测试对照用）。
#
# 禁 GUI（补充层，弹窗不是文件越界、audit hook 管不了，单独兜底）—— 四道，从早到晚：
#   ① meta_path 拦截 tkinter/PyQt/PySide/wx/gi 导入（库都进不来）；
#   ② matplotlib.use 锁 Agg 族后端 + 硬钉 os.environ['MPLBACKEND']='Agg'
#      （env 会被子进程链继承：宿主投毒 Qt5Agg/TkAgg 也覆盖掉）；
#   ③ **事后补丁** `_poison_tk_windows()`：tkinter/_tkinter 若已经进了 sys.modules
#      （meta_path 只管"还没导入的模块"），把 Tk/Toplevel 的构造入口换成抛错对象。
#   ④ 静态关键词预检留在 test_corpus_invariants.py（省时间）；运行时这四道兜底。
#   **为什么只能在 import 层拦**：CPython 的审计事件表里**没有**"创建窗口对象"
#   这样的事件（open/subprocess/socket 那些有，窗口没有），所以 sys.addaudithook
#   对弹窗无能为力（详见 SANDBOX.md §3）。
#   MPLTWEAK_SANDBOX_NOGUI=1 可关闭禁 GUI（仅关禁 GUI，文件/子进程/网络守卫不关）。
#
# 环境变量：
#   MPLTWEAK_SANDBOX_ROOT   游乐场根目录（绝对路径）。
#   MPLTWEAK_SANDBOX_LOG    拦截日志文件路径（可选；守卫用预打开 fd 追加写，不递归）。
#   MPLTWEAK_SANDBOX_NOGUI  设 1 → 关闭禁 GUI。
#
# 已知边界（SANDBOX.md 详述）：audit hook 不是完美沙箱 —— ctypes/内存修改可理论绕过；
# 威胁模型是"防无意乱写乱删/防弹窗/防越权子进程"，非对抗恶意攻击。
import os
import sys

_ROOT = os.environ.get('MPLTWEAK_SANDBOX_ROOT')
_LOG_PATH = os.environ.get('MPLTWEAK_SANDBOX_LOG')
_NOGUI = os.environ.get('MPLTWEAK_SANDBOX_NOGUI') == '1'

_ROOT_REAL = None
if _ROOT:
    try:
        _ROOT_REAL = os.path.normcase(os.path.realpath(os.path.abspath(_ROOT)))
    except Exception:
        _ROOT_REAL = os.path.normcase(os.path.abspath(_ROOT))

# ---- 预打开的日志 fd（写日志不触发 open 审计事件，避免递归）----
_LOG_FD = None
if _LOG_PATH:
    try:
        # 在安装守卫**之前**打开日志文件 —— 之后 os.write(fd) 不再触发文件类审计。
        _LOG_FD = os.open(_LOG_PATH, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    except OSError:
        _LOG_FD = None


def _log(msg):
    if _LOG_FD is not None:
        try:
            os.write(_LOG_FD, (msg + '\n').encode('utf-8', 'replace'))
        except OSError:
            pass
    try:
        sys.stderr.write('SANDBOX| %s\n' % msg)
    except Exception:
        pass


def _norm(p):
    try:
        return os.path.normcase(os.path.realpath(os.path.abspath(p)))
    except Exception:
        try:
            return os.path.normcase(os.path.abspath(p))
        except Exception:
            return os.path.normcase(str(p))


def _in_playground(p):
    if _ROOT_REAL is None:
        return True
    try:
        rp = _norm(p)
    except Exception:
        return False
    return rp == _ROOT_REAL or rp.startswith(_ROOT_REAL + os.sep)


def _is_script_process():
    """argv[0] 是游乐场内的真实文件 → 正在跑用户脚本（完整守卫）。"""
    if _ROOT_REAL is None:
        return False
    try:
        a0 = sys.argv[0]
    except Exception:
        return False
    if not a0 or not os.path.isabs(a0):
        try:
            a0 = os.path.abspath(a0)
        except Exception:
            return False
    if not os.path.isfile(a0):
        return False
    return _in_playground(a0)


_IS_SCRIPT = _is_script_process()

# ---- 写类事件的文件路径提取 + 校验 ----------------------------------------
# 审计事件参数形态（CPython 3.10 官方审计事件表）：
#   open(path, mode, flags)                        os.open(path, flags, mode)
#   os.remove(path, dir_fd)  os.unlink  os.rmdir  os.mkdir(path,mode,dir_fd)
#   os.makedirs(path, mode)  os.chmod  os.chown  os.truncate(path,length)
#   os.rename(src,dst,...)   os.replace(src,dst)  os.link(src,dst)  os.symlink(src,dst)
#   os.system(cmd)  os.exec(path,args)  os.spawn(mode,path,args)  os.posix_spawn(path,argv,env)
#   os.popen(cmd,mode,bufsize)  os.chdir(path)  subprocess.Popen(exe,args,cwd)
#   socket.connect(addr) socket.connect_ex(addr) socket.sendmsg(addr) socket.sendto(addr)
_WRITE_FLAGS = (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
                | os.O_EXCL | os.O_TEMPORARY)


def _mode_is_write(mode):
    try:
        return any(c in mode for c in 'wax+')
    except TypeError:
        return False


def _flags_is_write(flags):
    try:
        return bool(flags & _WRITE_FLAGS)
    except TypeError:
        return False


def _block(event, path):
    _log('BLOCK %s: %s' % (event, path))
    raise PermissionError(
        '沙箱：%s 被禁止（目标不在游乐场 MPLTWEAK_SANDBOX_ROOT 内）：%s'
        % (event, path))


def _block_always(event, what):
    _log('BLOCK %s: %s' % (event, what))
    raise PermissionError('沙箱：%s 被禁止（%s）' % (event, what))


def _guard(event, args):
    # ---- ① 文件写/删/改名/建目录等（harness 与脚本进程都生效）----
    if _ROOT_REAL is None:
        return
    if event == 'open':
        path = args[0]
        # fd 形态（os.fdopen / open(fd,...)）：路径参数是**整数文件描述符**，不是
        # 路径 —— 底层那次 os.open 已经过审计；这里放行（否则会把 '4' 当路径拦掉）。
        if isinstance(path, int):
            return
        mode = args[1] if len(args) > 1 else ''
        flags = args[2] if len(args) > 2 else 0
        if _mode_is_write(mode) or _flags_is_write(flags):
            if not _in_playground(path):
                _block('open(write)', path)
        return
    if event == 'os.open':
        path = args[0]
        if isinstance(path, int):
            return
        flags = args[1] if len(args) > 1 else 0
        if _flags_is_write(flags) and not _in_playground(path):
            _block('os.open(write)', path)
        return
    if event in ('os.remove', 'os.unlink', 'os.rmdir', 'os.mkdir', 'os.makedirs',
                 'os.chmod', 'os.chown', 'os.truncate'):
        path = args[0]
        if not _in_playground(path):
            _block(event, path)
        return
    if event in ('os.rename', 'os.replace', 'os.link', 'os.symlink'):
        src, dst = args[0], args[1]
        if not _in_playground(src):
            _block(event + '(src)', src)
        if not _in_playground(dst):
            _block(event + '(dst)', dst)
        return
    if event == 'os.chdir':
        # 防相对路径逃逸：cwd 改出游乐场后相对写会落在外边 —— 一并拦。
        if not _in_playground(args[0]):
            _block('os.chdir', args[0])
        return
    # ---- ② 起子进程 / ③ 网络：只拦"跑用户脚本"的进程 ----
    if not _IS_SCRIPT:
        return
    if event in ('subprocess.Popen', 'subprocess.call', 'subprocess.run'):
        _block_always(event, args[0] if args else '?')
    if event in ('os.system', 'os.popen', 'os.exec', 'os.spawn',
                 'os.posix_spawn'):
        _block_always(event, args[0] if args else '?')
    if event in ('socket.connect', 'socket.connect_ex', 'socket.sendmsg',
                 'socket.sendto'):
        _block_always(event, args[0] if args else '?')


sys.addaudithook(_guard)
_log('SANDBOX armed: root=%s script_process=%s nogui=%s'
     % (_ROOT_REAL, _IS_SCRIPT, _NOGUI))

# ---- ⑤ 禁 GUI（meta_path 拦截 + matplotlib.use 锁 + tkinter 事后补丁 + env 硬钉）----
def _gui_ban_factory(what):
    """造一个"一调用就抛"的替代品（给建窗入口用）。"""
    def _banned(*a, **k):
        raise RuntimeError(
            '沙箱：禁止创建窗口对象（%s）—— mpltweak 测试沙箱运行时禁 GUI' % what)
    return _banned


def _poison_tk_windows():
    """纵深防御第 3 道：把**已经进了 sys.modules** 的 tkinter/_tkinter 建窗入口换掉。

    为什么需要：meta_path 拦截器只管"还没导入的模块"。CPython **没有**"创建窗口
    对象"的审计事件，audit hook 这一层帮不上忙（见文件头）；而 tkinter 一旦被别的
    代码导入过（同一个 harness 进程里先跑过真窗口测试；或 verify 的 collect 用
    runpy **在进程内**执行脚本），后面 `import tkinter` 拿到的就是 sys.modules 里
    那份**没被拦过**的模块 —— 此时 `tkinter.Tk()` 能真开窗。

    本函数**不主动 import tkinter**：主动导入会让 test_sandbox 的 D1
    "import tkinter 被拦"失效（库都进得来就不叫拦了）。只在它已经存在时补丁，
    并且同时补**类本身**的 `__init__`（防"补丁之前就把 Tk 绑到别的名字上"的引用）。
    """
    hits = []
    tk = sys.modules.get('tkinter')
    if tk is not None:
        for attr in ('Tk', 'Toplevel'):
            cls = getattr(tk, attr, None)
            if cls is None:
                continue
            try:
                cls.__init__ = _gui_ban_factory('tkinter.%s.__init__' % attr)
                hits.append('tkinter.%s.__init__' % attr)
            except Exception:                                    # noqa: BLE001
                pass
            try:
                setattr(tk, attr, _gui_ban_factory('tkinter.%s' % attr))
                hits.append('tkinter.%s' % attr)
            except Exception:                                    # noqa: BLE001
                pass
    tk_c = sys.modules.get('_tkinter')
    if tk_c is not None and hasattr(tk_c, 'create'):
        try:
            # 最底层入口：tkinter 的 Tk/Toplevel 都最终走它
            tk_c.create = _gui_ban_factory('_tkinter.create')
            hits.append('_tkinter.create')
        except Exception:                                        # noqa: BLE001
            pass
    return hits


if _ROOT_REAL is not None and not _NOGUI:
    _GUI_TOP = ('tkinter', '_tkinter', 'Tkinter', 'PyQt5', 'PyQt6', 'PyQt4',
                'PySide', 'PySide2', 'PySide6', 'wx', 'gi', 'Gdk', 'gtk',
                'Gtk', 'Qt', 'PyQt')

    class _GuiBlocker:
        """导入 GUI 库时直接抛 ImportError —— 弹窗不可能发生（库都进不来）。"""

        def find_spec(self, name, path=None, target=None):
            top = name.split('.')[0]
            if top in _GUI_TOP:
                raise ImportError(
                    '沙箱：禁止 GUI 库导入（%s）—— mpltweak 测试沙箱运行时禁 GUI' % name)
            return None

    sys.meta_path.insert(0, _GuiBlocker())

    # env 硬钉（不是 setdefault）：宿主若把 MPLBACKEND 设成 Qt5Agg/TkAgg，
    # setdefault 会原样保留 → 本进程及**它的子进程链**都会落到交互后端
    # （2026-10-12 抢鼠标事故的同类隐患）。sandbox_env.py / test_sandbox.py 的
    # _pin_child_env 是同一个闸门的不同副本，这里补上"进程内"那一份。
    os.environ['MPLBACKEND'] = 'Agg'

    try:
        import matplotlib  # noqa: E402 —— 字体缓存写 MPLCONFIGDIR（已指游乐场）
        _orig_use = matplotlib.use

        def _sandbox_use(backend=None, force=None):
            if backend is not None:
                b = str(backend).lower()
                if b not in ('agg', 'pdf', 'ps', 'svg', 'template', 'cairo',
                             'module://matplotlib_inline.backend_inline'):
                    raise RuntimeError(
                        '沙箱：只允许无窗口后端（Agg 等），拒绝 %r' % backend)
            return _orig_use(backend=backend, force=force)

        matplotlib.use = _sandbox_use
        try:
            matplotlib.use('Agg', force=True)
        except Exception:
            pass
        _log('SANDBOX: matplotlib.use locked to Agg-family; MPLBACKEND=Agg pinned')
    except Exception as e:  # noqa: BLE001 —— 拿不到 matplotlib 就只靠 meta_path 拦
        _log('SANDBOX: matplotlib.use lock skipped: %r' % (e,))

    # 第 3 道：已经导入过 tkinter/_tkinter 的情况（meta_path 拦不住）
    _poisoned = _poison_tk_windows()
    if _poisoned:
        _log('SANDBOX: tkinter window entry points poisoned: %s'
             % ', '.join(_poisoned))
    else:
        _log('SANDBOX: tkinter not in sys.modules -> meta_path blocker only')

# ---- 无人值守：禁止 input()/stdin 等待（t10 需要：等人输入的脚本会卡到超时）----
# MPLTWEAK_SANDBOX_NO_STDIN=1 → 把 builtins.input 换成"读即抛 EOFError"，
# 脚本一碰 input() 就立刻失败（可归 na_stdin），不会占满超时配额。
if _ROOT_REAL is not None and os.environ.get('MPLTWEAK_SANDBOX_NO_STDIN') == '1':
    import builtins

    def _no_stdin_input(*a, **k):
        raise EOFError('沙箱：禁止等待 stdin（无人值守真跑，input() 直接 EOF）')

    builtins.input = _no_stdin_input
    _log('SANDBOX: builtins.input disabled (NO_STDIN)')

