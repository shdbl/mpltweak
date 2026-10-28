# -*- coding: utf-8 -*-
# ==== §1 文件头：外部启动器的目标、用法与退出码 ====

# ======================================================================
# tweak_launch.py —— 无侵入交互调图「外部启动器」
#
# 目标：用户脚本里**完全不出现本工具的任何痕迹**（不 import、不加写回区、
#       不加 --mpltweak 分支、不动一行代码）。做法是外部包一层：
#
#   1. 用 QtAgg 后端执行用户脚本（拦截脚本自己的 plt.show，避免阻塞）
#   2. 抓到脚本留下的 Figure，挂上交互改图窗口
#   3. 关窗后**只**写参数 JSON（默认 <脚本目录>/.tweak_params/<脚本名>.json），
#      不碰代码、不产生其它副作用
#   4. 等用户确认后，由 AI 把 JSON 里的数值变成**纯 matplotlib 代码**写回脚本
#
# 用法：
#   mpltweak <脚本.py> [脚本自己的参数 ...]   （包入口：mpltweak.cli）
#   可选：
#     --fig N         调第 N 张图（默认最后一张）
#     --params PATH   自定义参数输出路径
#     --dry-run       不开窗：只验证「能抓到图 + 能存参数」后立即退出
#     --timeout SEC   配合 --dry-run 无意义；留作将来扩展
#
# 退出码（方案 A，0.1.11 统一）：0 = 成功；2 = 跑不起来（脚本找不到 / 脚本自己报错 /
#           参数非法 —— 与各子命令同一口径）；4 = 找不到可用的图形后端。
#           （版本标注 = 实际随哪个版本发布：这批统一是 0.1.10 发布**之后**做的，
#           所以写 0.1.11；messages.py:31-43 的同一批改动也是这么标的。）
#           **3 已退役**：常量（messages.EXIT_SCRIPT）保留，但任何路径都不再返回它。
# ======================================================================

# ---- 快速导航（顶层 §1-§7 的分区横幅被文件尾索引收录；main() 体内的小节用
#      `# ==== §7.N … ====`，工具不索引，跳转请 `grep -n '§7.5'`）----
#
#   §1 文件头：外部启动器的目标、用法与退出码（就是本段）
#   §2 基础设施：UTF-8 流（只给 doctor）、stderr 行数计数、后端候选探测
#   §3 一次性提示与戳记：mpltweak 家目录 / Tk 回退提示 / 首次开窗 / 可重跑预检 /
#      「需要参数」的开窗专属人话（都用 ~/.mpltweak 下的戳文件保证"每个用户只打一次"）
#   §4 参数文件与「关窗后怎么写回」：参数路径、摘要、shell 相对化、可复制命令
#   §5 收尾清理、摘要与续调：删掉本轮新产生且没改的参数文件 + 全部 .lock、比对摘要、
#      把上次的(还没落实的)参数套回图上
#   §6 doctor 结论与 doctor 子命令：后端/Qt 绑定/版本/光标枚举，一句人能看懂的判断
#   §7 主流程 main（开窗调图）：内分 6 小节 ——
#      §7.1 开窗前的提示（布局引擎冲突、可重跑预检）/ §7.2 后端与工作目录 /
#      §7.3 执行用户脚本（拦 show / matplotlib.use / close）/ §7.4 抓图与后端兜底 /
#      §7.5 挂控制器（每图一个 Tweak，各写自己的参数文件）/
#      §7.6 续调与关窗之后（套回参数、落盘、摘要、写回命令）
#
# ---- 这一层与另外三层的分工（跳读时先看这条）----
#   launch = 开窗（人手动调）；toolbox = 窗口内的交互（拖/缩放/键位）；
#   describe/check = 不开窗导出与体检；apply + writeback = 把参数落实成代码。
#   参数文件是四者之间**唯一的接口**（.tweak_params/<stem>.json + <stem>.fig<k>.json）。

import argparse
import json
import os
import runpy
import sys
import traceback

# t64：launch 的**用户可见文案**统一走 messages（zh 逐字不变 / en 查 UI_ZH_TO_EN）。
# 本文件既有的 `from . import messages`（局部导入）继续保留：那是取退出码常量用的，
# 与这里的 `_msg.t_ui(...)` 不冲突；模块级导入 messages 无循环（messages 只 import 标准库）。
from . import messages as _msg                      # noqa: E402


# ==== §2 基础设施：UTF-8 流、stderr 计数与后端探测 ====

def _force_utf8_streams():
    """P2-9（t69）：把 stdout/stderr 显式切到 UTF-8（**只给 doctor 用**）。

    中文 Windows + PowerShell 管道下 doctor 的中文会乱码（用户实测：即便设了
    ``[Console]::OutputEncoding=UTF8`` 与 ``PYTHONIOENCODING=utf-8``）。doctor 是
    "降低门槛"的自检入口，输出必须能读：
      * 真控制台上 Python 走 Windows 控制台 API（WriteConsoleW），切编码不影响它；
      * 管道/重定向（``mpltweak doctor > x.txt``）下则得到**合法 UTF-8**，用
        ``Get-Content -Encoding UTF8`` 或编辑器都能读。
    失败一律静默退回（被包装过的流可能没有 reconfigure）：绝不因为"改编码"把自检
    本身搞崩。只动 doctor —— 机器可读的 --json 路径（fd 级 UTF-8）不受影响。
    """
    for _s in (sys.stdout, sys.stderr):
        try:
            if (getattr(_s, 'encoding', '') or '').lower().replace('-', '') \
                    != 'utf8':
                _s.reconfigure(encoding='utf-8')
        except (AttributeError, OSError, ValueError):
            pass


class _CountingStderr:
    """P2-10（t69）：数一下**被调脚本自己**往 stderr 写了多少行（不吞、不改内容）。

    用户实测：脚本打 20+ 条 ``RuntimeWarning: Mean of empty slice``，与工具提示混在
    一起，新用户分不清哪条是工具的。这里只**计数**、原样透传；脚本跑完后由 main 打
    一句汇总（工具自身的提示都带 [launch]/[mpltweak] 前缀）。
    """

    def __init__(self, inner):
        self._inner = inner
        self.lines = 0

    def write(self, s):
        self.lines += str(s).count('\n')
        return self._inner.write(s)

    def flush(self):
        return self._inner.flush()

    def __getattr__(self, name):
        if name == '_inner':                    # __init__ 期间的防递归
            raise AttributeError(name)
        return getattr(self._inner, name)

# P0-1（方案 B，用户已拍板）：无 PyQt5 → Tk 开窗时给一句人话提示装 mpltweak[qt]。
# 文案优先走 messages.t('backend_tk_hint')（cli-hunter 补中英表）；键缺失时 t()
# 容错地原样返回 key，这里退回内置兜底，绝不崩、也不污染 --json / MCP 的 stdout。
_BACKEND_TK_HINT_ZH = ('[launch] 当前是 Tk 后端（交互体验不如 Qt）；'
                       '想要 Qt 界面：pip install "mpltweak[qt]"')
_BACKEND_TK_HINT_EN = ('[launch] using the Tk backend (less polished than Qt); '
                       'for the Qt UI run: pip install "mpltweak[qt]"')

# B 组（首次使用体验，0.1.11）：**第一次**开窗给一句安慰/预期说明 —— 窗口是现建的，
# 头几秒终端里没有新输出，用户很容易以为卡死了；这句话说清"会慢、之后就顺手了"，
# **不改任何行为**（写回/验证判定一个字节都不动）。
# 与 Tk 提示同一套"每个用户只打一次"范式（戳文件 + MPLTWEAK_HOME 可改 + dry-run
# 不落戳 + MPLTWEAK_NO_HINT=1 静默），但**各用各的戳**（first_open_shown）——
# 绝不动 tk_hint_shown 的语义。文案优先走 messages.t('first_open_hint')，键缺失时
# 退回内置兜底（en 必须纯 ASCII；两者都必须 GBK 可编码，见 messages.py 顶部）。
_FIRST_OPEN_HINT_ZH = ('[launch] 第一次开窗会等几秒（窗口要现建），不是卡住了；'
                       '之后拖拽/缩放跟在 PPT 里拖图一样跟手。'
                       '放心拖：关窗之前不会写你的脚本。')
_FIRST_OPEN_HINT_EN = ('[launch] the first window takes a few seconds to build; '
                       'it is not frozen. After that, dragging and resizing feel '
                       'like moving shapes in a slide editor. Go ahead - nothing '
                       'touches your script until you close the window.')

# 两段式的**早段**（0.1.11 收尾批）：跑用户脚本**之前**说一句，因为"以为卡死"真正发生
# 在更早那段静默（后端 import + 跑用户脚本）。早段**只说不落戳** —— 那时还不知道窗开不
# 开得成（脚本报错 / 不留图 / 找不到脚本都会走到这里），落戳就会把用户真正的那次首次
# 开窗提示吃掉（2026-10-13 t8 实测的原坑）。戳统一由建窗前那一段落（stage='window'）。
_FIRST_OPEN_EARLY_HINT_ZH = ('[launch] 正在加载绘图环境并执行你的脚本，第一次要等几秒'
                             '到几十秒（脚本越大越久）——这段时间终端没有新输出是'
                             '正常的，不是卡住了。')
_FIRST_OPEN_EARLY_HINT_EN = ('[launch] loading the plotting environment and running '
                             'your script; the first time this can take from a few '
                             'seconds to a minute (longer for big scripts) - no new '
                             'output here is normal, not a freeze.')

# rc=4（找不到可用图形后端）那句人话（0.1.11 收尾批）：**发生了什么 + 后果 +
# 你现在该做什么**。与 messages 表 'launch_backend_missing' 逐字一致（键缺失时退回
# 这里的内置兜底：宁可少些措辞，也不能把一段人话换成一句看不懂的报错）。
_BACKEND_MISSING_ZH = (
    '[launch] 开不了窗：这台机器找不到可用的图形后端'
    '（试过 QtAgg/Qt5Agg/Qt6Agg/TkAgg，最后一个的报错：%(err)s）。\n'
    '         你的脚本没有执行，磁盘上也没有任何改动 —— 这是环境缺图形后端，'
    '不是脚本的问题。\n'
    '         现在两条路任选：\n'
    '           ① 要开窗调图：pip install "mpltweak[qt]"（推荐，装 PyQt5）；'
    'Linux 也可以装系统包 python3-tk\n'
    '              （没有桌面的服务器还要 DISPLAY / X 转发，否则装了也起不来）\n'
    '           ② 只用命令行：describe / check / apply / mcp 不需要图形后端，'
    '照常可用。')
_BACKEND_MISSING_EN = (
    '[launch] cannot open a window: this machine has no usable GUI backend '
    '(tried QtAgg/Qt5Agg/Qt6Agg/TkAgg; the last error: %(err)s).\n'
    '         Your script was NOT run and nothing on disk changed - the '
    'environment is missing a GUI backend, not a problem in the script.\n'
    '         Two ways out:\n'
    '           1) to open windows: pip install "mpltweak[qt]" (recommended, '
    'installs PyQt5); on Linux you can also install the system package python3-tk\n'
    '              (a machine with no desktop also needs DISPLAY / X forwarding, '
    'or it will still fail)\n'
    '           2) CLI only: describe / check / apply / mcp need no GUI backend '
    'and keep working.')


def _detect_backend():
    '''逐个候选后端真建一次 canvas 验证，返回 ``(backend_name, last_err)``。
    命中 Qt 优先（QtAgg/Qt5Agg/Qt6Agg），都不行才退回 TkAgg —— 方案 B 的判据：
    PyQt5 可用 → Qt 现行为；不可用 → Tk 开窗（main 里再补 stderr 人话提示）。'''
    import matplotlib
    import matplotlib.pyplot as plt
    err = None
    for backend in ('QtAgg', 'Qt5Agg', 'Qt6Agg', 'TkAgg'):
        try:
            matplotlib.use(backend, force=True)
            _probe = plt.figure()          # 真建一次，确认能拿到交互 canvas
            plt.close(_probe)
            return backend, None
        except Exception as e:             # noqa: BLE001 - 故意兜所有后端差异
            err = e
    return None, err


def _backend_tk_hint():
    '''方案 B 的 stderr 人话提示（PyQt5 不在时用）。'''
    from . import messages
    hint = messages.t('backend_tk_hint')
    if hint == 'backend_tk_hint':
        hint = (_BACKEND_TK_HINT_ZH if messages.current_lang() == 'zh'
                else _BACKEND_TK_HINT_EN)
    return hint


def _backend_missing_msg(err):
    '''rc=4 时那句人话：**发生了什么 + 后果 + 你现在该做什么**（0.1.11 收尾批）。

    优先取 messages 表（``launch_backend_missing``，中英齐备）；键缺失时退回内置兜底
    （两个模板与表里逐字一致）—— 失败路径上的文案比"少几行"重要得多。
    '''
    from . import messages
    msg = messages.t('launch_backend_missing', err=err)
    if msg == 'launch_backend_missing':
        tmpl = (_BACKEND_MISSING_ZH if messages.current_lang() == 'zh'
                else _BACKEND_MISSING_EN)
        msg = tmpl % {'err': err}
    return msg


# ==== §3 一次性提示与戳记：Tk 回退 / 首次开窗 / 可重跑预检 / 需要参数 ====

def _mpltweak_home(environ=None):
    '''mpltweak 的家目录（戳文件放这里）：``MPLTWEAK_HOME`` 可改，默认 ``~/.mpltweak``。

    测试隔离 / 多环境共用一台机器时用。各条"只打一次"的提示**共用这个家目录、
    但各用各的戳文件**（见 ``_tk_hint_stamp_path`` / ``_first_open_stamp_path``）。
    '''
    environ = os.environ if environ is None else environ
    return environ.get('MPLTWEAK_HOME') or os.path.join(
        os.path.expanduser('~'), '.mpltweak')


def _tk_hint_stamp_path(environ=None):
    '''"Tk 提示已经打过一次"的戳文件路径。

    默认 ``~/.mpltweak/tk_hint_shown``；``MPLTWEAK_HOME`` 可改这个家目录
    （测试隔离 / 多环境共用一台机器时用）。命名与位置是**对外行为**，见
    ``docs/interaction.md``「窗口后端」一节。
    '''
    return os.path.join(_mpltweak_home(environ), 'tk_hint_shown')


def _emit_backend_hint(backend_name, dry_run=False, environ=None, stderr=None):
    '''无 PyQt5 → Tk 开窗时向 stderr 打一句人话；返回是否打出。

    **每个用户 / 每次安装只打一次**（无感化③，2026-10-13 用户认可）：每次开窗都念
    一遍只会变成噪音，而"怎么装 Qt"并没有被藏起来 —— ``mpltweak doctor`` 的
    「开窗后端」那行**永远**照打（见 ``doctor()``），``docs/interaction.md`` 也照写。
    打过的戳文件见 ``_tk_hint_stamp_path()``（删掉它即可让提示再出现一次）。

    dry-run 不开窗不提示（也**不落戳**）；``MPLTWEAK_NO_HINT=1`` 静默（批量脚本用，
    同样不落戳 —— 那是"这次别说话"，不是"已经说过了"）。
    '''
    environ = os.environ if environ is None else environ
    if dry_run or environ.get('MPLTWEAK_NO_HINT') == '1':
        return False
    if not str(backend_name or '').lower().startswith('tk'):
        return False
    hint = _backend_tk_hint()
    if not hint:
        return False
    # 只在真的第一次提示时说话；判断失败（权限/IO）= 当作没打过（宁可多说一次，
    # 也不能因为戳文件读不了就把提示彻底吞掉）。
    stamp = _tk_hint_stamp_path(environ)
    try:
        if os.path.exists(stamp):
            return False
    except OSError:                                # noqa: BLE001
        pass
    _out = sys.stderr if stderr is None else stderr
    try:
        _out.write(hint + '\n')
    except (OSError, ValueError):                  # noqa: BLE001 - 输出失败也不许影响开窗
        return False
    # best-effort 落戳：写不了也照常开窗（代价只是下次再说一遍）。
    try:
        _dir = os.path.dirname(stamp)
        if _dir:
            os.makedirs(_dir, exist_ok=True)
        with open(stamp, 'w', encoding='utf-8') as _f:
            _f.write('1\n')
    except OSError:                                # noqa: BLE001
        pass
    return True


def _first_open_hint(stage='window'):
    '''首次开窗的安慰/预期说明（B 组，0.1.11）。

    两段式（0.1.11 收尾批）：``stage='early'`` 取**早段**那句（跑用户脚本之前，
    "环境在加载、脚本在跑，别以为卡死"）、``stage='window'``（默认）取**建窗前**那句
    （"窗口要现建，之后拖拽跟手"）。默认档与 0.1.11 首版逐字一致 —— 既有调用点与
    测试（``tests/test_hygiene.py::test_emit_first_open_hint_cases``）都按默认档立契。

    与 ``_backend_tk_hint()`` 同一写法：优先取 messages 表，键缺失时退回内置兜底 ——
    绝不因为消息表里还没有这个键就少一句话，也绝不崩。
    '''
    from . import messages
    _early = (stage == 'early')
    key = 'first_open_early_hint' if _early else 'first_open_hint'
    hint = messages.t(key)
    if hint == key:
        if _early:
            hint = (_FIRST_OPEN_EARLY_HINT_ZH if messages.current_lang() == 'zh'
                    else _FIRST_OPEN_EARLY_HINT_EN)
        else:
            hint = (_FIRST_OPEN_HINT_ZH if messages.current_lang() == 'zh'
                    else _FIRST_OPEN_HINT_EN)
    return hint


def _first_open_stamp_path(environ=None):
    '''"首次开窗提示已经打过一次"的戳文件路径：``~/.mpltweak/first_open_shown``。

    与 ``_tk_hint_stamp_path()`` 共用家目录规则（``MPLTWEAK_HOME`` 可改），但**是
    另一个戳**：Tk 提示打过了不代表开窗安慰打过了，两条提示互不影响；删掉这个文件
    即可让安慰再来一次。
    '''
    return os.path.join(_mpltweak_home(environ), 'first_open_shown')


def _emit_first_open_hint(dry_run=False, environ=None, stderr=None, stage='window'):
    '''**第一次**开窗时给一句安慰/预期；返回是否打出。

    两段式（0.1.11 收尾批，用户 2026-10-13 拍板）：

      * ``stage='early'``（早段，main 在**跑用户脚本之前**调）：戳不存在就说明"环境在
        加载 + 脚本在跑，等一会儿是正常的"，**但不落戳** —— 那一刻还不知道窗开不开得成
        （脚本报错 / 不留图 / 找不到脚本都会走到这里），落戳就会把用户真正的第一次
        开窗提示吃掉（2026-10-13 实测的原坑）。
      * ``stage='window'``（默认，main 在**真要建窗口的那一刻**调）：说"窗口要现建、
        之后拖拽跟手、关窗之前不写你的脚本"，并**落戳**（每个用户只打一次）。

    为什么要有早段：用户真正会以为卡死的是**更早那段静默** —— 后端 import + 跑用户
    脚本（几十秒都可能）；首版只覆盖了"建窗那几秒"。两段都只说事实、**不改任何行为**
    （写回/验证的判定一个字都不动）。

    dry-run 不开窗不提示（也**不落戳**）；``MPLTWEAK_NO_HINT=1`` 静默（批量脚本用，
    同样不落戳 —— 那是"这次别说话"，不是"已经说过了"）。

    输出只走 stderr（与后端提示/预检同款）：stdout 留给 `--json` / MCP 的机器可读
    内容，不能被这句安慰污染。
    '''
    environ = os.environ if environ is None else environ
    if dry_run or environ.get('MPLTWEAK_NO_HINT') == '1':
        return False
    hint = _first_open_hint(stage)
    if not hint:
        return False
    # 判断失败（权限/IO）= 当作没打过（宁可多说一次，也不能因为戳文件读不了就把话吞掉）。
    stamp = _first_open_stamp_path(environ)
    try:
        if os.path.exists(stamp):
            return False
    except OSError:                                # noqa: BLE001
        pass
    _out = sys.stderr if stderr is None else stderr
    try:
        _out.write(hint + '\n')
    except (OSError, ValueError):                  # noqa: BLE001 - 输出失败也不许影响开窗
        return False
    if stage == 'early':
        # 早段到此为止：**刻意不落戳**（那时还不知道窗开不开得成，见 docstring）。
        return True
    # best-effort 落戳：写不了也照常开窗（代价只是下次再说一遍）。
    try:
        _dir = os.path.dirname(stamp)
        if _dir:
            os.makedirs(_dir, exist_ok=True)
        with open(stamp, 'w', encoding='utf-8') as _f:
            _f.write('1\n')
    except OSError:                                # noqa: BLE001
        pass
    return True


def _emit_preflight_hint(script, args_passed=False, environ=None, stderr=None,
                         out=None):
    '''开窗**之前**给一次可重跑预检提示（0.1.10）；返回是否打出。

    为什么开窗入口也要提示：launch 会**执行用户脚本**取图 —— 「等人输入」的脚本会把
    启动卡到超时、「要参数」的脚本直接报错、「要弹窗」的脚本还会再开一个窗口，而这些
    全都能在跑之前静态看出来（见 mpltweak.preflight，绝不执行脚本）。

    **绝不阻塞开窗**：预检自身出任何岔子都静默返回（失败即放行）。
    ``MPLTWEAK_NO_HINT=1`` 静默（与后端提示同一开关，批量脚本用）。
    注意 dry-run 也提示：dry-run **照样执行脚本**（只是不开窗），风险一模一样。

    ``out``（0.1.11 收尾批）：可选**出参**（dict）—— 把同一份预检结论填进去，供调用方
    在预检之后补"开窗专属"的下一步（launch 用它判 needs_args，见
    ``_emit_needs_args_action``）。只在真的分析了脚本时填（NO_HINT 静默时连分析都不做）。
    **返回值仍是"是否打出"这个 bool** —— 既有调用点与用例按它立契
    （``tests/test_agent_api.py`` §22 断言 ``is True`` / ``is False``）。
    '''
    environ = os.environ if environ is None else environ
    if environ.get('MPLTWEAK_NO_HINT') == '1':
        return False
    try:
        from . import preflight
        res = preflight.analyze(script, args_passed=args_passed)
        if out is not None:
            out.update(res)
        lines = preflight.message_lines(res, script)
    except Exception:                  # noqa: BLE001 - 预检绝不让开窗失败
        return False
    if not lines:
        return False
    _out = sys.stderr if stderr is None else stderr
    for _line in lines:
        try:
            _out.write(_line + '\n')
        except Exception:              # noqa: BLE001 - 输出失败也不许影响开窗
            return False
    return True


def _emit_needs_args_action(script, pf_res=None, environ=None, stderr=None):
    '''launch 专属：脚本要命令行参数时，补一句"**接下来会发生什么 + 现在怎么带参数重来**"。

    为什么要有（0.1.11 收尾批）：预检那条 ``preflight_needs_args`` 是 apply / launch
    **两个入口共用**的文案，落点在"重跑验证时要把参数原样传给 apply"；而 launch 是
    **马上就把脚本跑起来取图** —— 缺参数它会立刻报错、窗口根本开不出来。用户在原位
    需要的是这句：现在会发生什么（为什么开不成窗）+ 立刻该敲什么。

    与预检同款纪律：``MPLTWEAK_NO_HINT=1`` 静默；只写 stderr；任何岔子都静默返回
    （提示绝不让开窗失败）。dry-run 也照说 —— dry-run 照样执行脚本，同样会报这个错。
    '''
    environ = os.environ if environ is None else environ
    if environ.get('MPLTWEAK_NO_HINT') == '1':
        return False
    try:
        from . import preflight
        kinds = [r.get('kind') for r in (pf_res or {}).get('risks') or []]
        if preflight.RISK_NEEDS_ARGS not in kinds:
            return False
        from . import messages
        line = messages.t('launch_needs_args_action',
                          script=_rel_for_shell(script, os.getcwd()))
    except (OSError, ImportError, AttributeError, TypeError, ValueError):
        # 只收这几类（棘轮：launch.py 的宽兜底数已被 BASELINE 钉住，不许再涨）——
        # preflight.analyze / messages.t 内部各自兜住了自己那点岔子。
        return False
    _out = sys.stderr if stderr is None else stderr
    try:
        _out.write(line + '\n')
    except (OSError, ValueError):      # noqa: BLE001 - 输出失败也不许影响开窗
        return False
    return True


# ==== §4 参数文件与「关窗后怎么写回」的命令 ====

def _params_path(script, override=None):
    if override:
        return os.path.abspath(override)
    d = os.path.dirname(script)
    stem = os.path.splitext(os.path.basename(script))[0]
    return os.path.join(d, '.tweak_params', stem + '.json')


def _summarize(fig):
    X, Y = fig.canvas.get_width_height()
    return _msg.t_ui('图 %dx%dpx，%d 个轴') % (X, Y, len(fig.axes))


def _rel_for_shell(path, base):
    '''把绝对路径表达成"相对 base（用户敲命令时所在的目录）"的样子，便于复制粘贴。

    只要**同盘**就给相对路径（含 ``..`` 形式，例如用户从 ``proj/`` 里开的
    ``../fig2.py``）—— 用户就在那个终端里粘贴，相对形式最短也最好认；
    跨盘符没有相对路径（``ValueError``）时才退回绝对路径。
    '''
    try:
        return os.path.relpath(path, base)
    except ValueError:                  # Windows 跨盘符：没有相对路径
        return path


def _quote_arg(s):
    '''含空格的参数加双引号（cmd / PowerShell / bash 都能直接粘贴）。'''
    return '"%s"' % s if any(c in s for c in ' \t') else s


def _next_step_cmd(script, params, script_args=(), params_given=False, base=None):
    '''关窗后那条**可直接复制**的写回命令（无感化①，省掉"记住怎么写回"）。

    只是把命令**打出来**：动作仍然要用户自己确认，**绝不自动写回用户源码**（红线）。
    ``script_args`` 是 launch 收到的透传参数（``--`` 之后的用户脚本参数）—— 写回时
    重跑验证要用同一份 argv，所以一并带上（bug #6 的口径）。
    '''
    base = os.getcwd() if base is None else base
    parts = ['mpltweak', 'apply', _quote_arg(_rel_for_shell(script, base)),
             '--write']
    if params_given:
        parts += ['--params', _quote_arg(_rel_for_shell(params, base))]
    if script_args:
        parts += ['--'] + [_quote_arg(str(a)) for a in script_args]
    return ' '.join(parts)


# ==== §5 收尾清理、摘要与续调：把上次的参数套回图上 ====

def _prune_fig_params(tmps, keep_keys, protect=()):
    """收尾清理：删掉**本轮新产生且未改动**的参数文件 + 全部 ``.lock``。

    多图会话里 ``<stem>.fig<k>.json`` 既是过程中写的那份、也是最终留下的正式参数
    （一个图一份），所以**改动过的必须保留**，否则"一次改多张"会被清掉。

    ``protect``：开窗前就已经存在的图号 —— 那是**上一轮**留下的正式参数，删掉会让
    用户白调一轮（t2-A4/A5、t6-H5）。清理只应针对本轮新产生的临时产物。
    """
    keep = set(keep_keys) | set(protect)
    for k in list(tmps):
        if k not in keep:
            try:
                os.remove(tmps[k])
            except OSError:
                pass
        try:
            os.remove(tmps[k] + '.lock')
        except OSError:
            pass


def _diff(before, data):
    '''对比交互前后的变化，给 AI/用户一行紧凑摘要。'''
    items = []
    axes = data.get('axes', [])
    for a in axes:
        i = a.get('index')
        p = a.get('pos')
        b = before.get(i)
        if b is not None and p is not None and any(
                abs(p[k] - b[k]) > 1e-4 for k in range(4)):
            items.append('ax%d [%s] -> [%s]' % (
                i, ','.join('%.3f' % v for v in b),
                ','.join('%.3f' % v for v in p)))
    fonts = data.get('axes', [])
    for a in fonts:
        if a.get('title_fontsize') is not None:
            pass
    if data.get('legend_changed'):
        pass
    return items


def _apply_saved(fig, params):
    '''续调：把上次的（还没落实的）参数套回图上，这样再开窗口是接着上次调，
    而不是回到脚本原始布局。返回套用的项数。'''
    if not os.path.exists(params):
        return 0
    with open(params, 'r', encoding='utf-8-sig') as f:
        data = json.load(f)
    if not isinstance(data, dict):
        # 顶层非对象（数组/字符串/数字/null）：不续调、不崩、不打扰主流程
        # （调用方是 GUI 开窗，绝不能因参数文件坏了崩掉）。与 params.load 的
        # 0.1.10 修复（params.py）同款语义：文件坏就跳过。
        print(_msg.t_ui('[mpltweak] 参数文件顶层不是对象，跳过续调：%s') % params,
              file=sys.stderr, flush=True)
        return 0
    from .toolbox import _set_cb_clim, _is_colorbar_ax, _cb_mappable, _close_help_fig
    n = 0
    for a in data.get('axes', []):
        i = a.get('index')
        if i is None or i >= len(fig.axes):
            continue
        ax = fig.axes[i]
        p = a.get('pos')
        if p:
            ax.set_position(p)
            n += 1
        # F4：续调要恢复三个标题位（loc='left'/'right' 落在 _left_title/_right_title），
        # 只调 ax.title 会丢真实论文图最常用的 loc='left' 标题字号。
        if a.get('title_fontsize'):
            for _t in (getattr(ax, '_left_title', None), ax.title,
                       getattr(ax, '_right_title', None)):
                if _t is not None:
                    _t.set_fontsize(a['title_fontsize'])
            n += 1
        # T73（零兼容）：label 字号 x/y 各按自己那一项应用 —— 参数里没写 y 就**不动** y。
        if a.get('label_fontsize') is not None or a.get('label_fontsize_y') is not None:
            if a.get('label_fontsize') is not None:
                ax.xaxis.label.set_fontsize(a['label_fontsize'])
            if a.get('label_fontsize_y') is not None:
                ax.yaxis.label.set_fontsize(a['label_fontsize_y'])
            n += 1
        # T25-#3a：tick 也按 x/y 各自套用；T73 起不再互相回退。
        if a.get('tick_fontsize') is not None or a.get('tick_fontsize_y') is not None:
            if a.get('tick_fontsize') is not None:
                ax.tick_params(axis='x', labelsize=a['tick_fontsize'])
            if a.get('tick_fontsize_y') is not None:
                ax.tick_params(axis='y', labelsize=a['tick_fontsize_y'])
            n += 1
        if a.get('clim') and _is_colorbar_ax(ax):
            _set_cb_clim(ax, *a['clim'])
            n += 1
        if a.get('cmap'):
            mappable = None
            if _is_colorbar_ax(ax):
                _, mappable = _cb_mappable(ax)
            else:
                for artist in list(getattr(ax, 'images', [])) + list(getattr(ax, 'collections', [])):
                    if hasattr(artist, 'set_cmap'):
                        mappable = artist
                        break
            if mappable is not None:
                try:
                    mappable.set_cmap(a['cmap'])
                    if _is_colorbar_ax(ax):
                        ax._colorbar.update_normal(mappable)
                    n += 1
                except Exception:
                    pass
        if a.get('xscale') and not _is_colorbar_ax(ax):
            try:
                ax.set_xscale(a['xscale'])
                ax.set_yscale(a.get('yscale', ax.get_yscale()))
                n += 1
            except Exception:
                pass
        if a.get('grid') is not None:
            ax.grid(bool(a['grid']))
            n += 1
        # F4：spines / lines / legend 每条成功恢复也计入 n（否则"只调字号"被误判未套用）。
        for k, visible in a.get('spines', {}).items():
            if k in ax.spines:
                ax.spines[k].set_visible(bool(visible))
                n += 1
        for line_item in a.get('lines', []):
            j = line_item.get('index')
            lines = getattr(ax, 'lines', [])
            if j is not None and j < len(lines):
                line = lines[j]
                if line_item.get('linewidth') is not None:
                    line.set_linewidth(line_item['linewidth'])
                    n += 1
                if line_item.get('color') is not None:
                    line.set_color(line_item['color'])
                    n += 1
        lg = a.get('legend')
        if lg and ax.get_legend() is not None:
            kw = {'loc': lg.get('loc') or 'best'}
            if lg.get('fontsize'):
                kw['fontsize'] = lg['fontsize']
            ax.legend(**kw)
            n += 1
    fs = data.get('figsize_in')
    if fs:
        try:
            fig.set_size_inches(fs[0], fs[1])
        except Exception:
            pass
    return n


# ==== §6 doctor 结论与 doctor 子命令 ====

def _doctor_verdict(matplotlib_ok, backend_ok, tkinter_ok=False):
    '''doctor 结尾那一句"一眼可懂的判断"（0.1.11 收尾批）。

    上面那些行是**给排查用**的明细（后端名 / 绑定 / 光标枚举）；这一句回答用户真正
    想知道的那个问题："这套环境现在到底能不能用、缺什么、缺了怎么办"。
    四个分支各说一件事：可用（Qt）/ 基本可用（只有 Tk）/ 不可用（有 tkinter 但仍起不来
    = 多半没显示环境）/ 不可用（既没 Qt 也没 tkinter）；matplotlib 都没装时另说。

    只读、不建窗（``tkinter`` 只 import 探测，不建任何窗口）。文案走 messages 表。
    '''
    from . import messages
    if not matplotlib_ok:
        return messages.t('doctor_verdict_no_matplotlib')
    if backend_ok and str(backend_ok).lower().startswith('qt'):
        return messages.t('doctor_verdict_ok_qt', backend=backend_ok)
    if backend_ok:
        return messages.t('doctor_verdict_ok_tk', backend=backend_ok)
    return messages.t('doctor_verdict_headless' if tkinter_ok
                      else 'doctor_verdict_no_backend')


def doctor():
    '''环境自检：报告后端/Qt 绑定/版本/光标枚举支持，供排查"别人的机器"差异。'''
    _force_utf8_streams()          # P2-9：中文 Windows 管道下别乱码
    try:
        import matplotlib
    except ImportError as e:
        # 连 matplotlib 都没有时，原先会抛裸 ModuleNotFoundError（t4-F9 / t6-M19）。
        # 这正是 `pip install -e . --no-deps` 之后的首次体验，必须给人话。
        print('python       :', sys.version.split()[0])
        print(_msg.t_ui('matplotlib   : 未安装 ->'), e)
        print(_msg.t_ui('             -> 先安装：pip install matplotlib（或直接 pip install '
              'mpltweak 会一并装上）'))
        print(_msg.t_ui('结论       :'), _doctor_verdict(False, None))
        return 0
    print('python       :', sys.version.split()[0])
    print('matplotlib   :', matplotlib.__version__)
    tried = []
    ok = None
    for backend in ('QtAgg', 'Qt5Agg', 'Qt6Agg', 'TkAgg'):
        try:
            matplotlib.use(backend, force=True)
            import matplotlib.pyplot as plt
            _f = plt.figure()
            plt.close(_f)
            tried.append('%s=OK' % backend)
            ok = backend
            break
        except Exception as e:                     # noqa: BLE001
            tried.append('%s=%s' % (backend, type(e).__name__))
    print('backend      :', matplotlib.get_backend(), _msg.t_ui('(可用: %s)') % (ok or _msg.t_ui('无')))
    print(_msg.t_ui('backend 探测 :'), ', '.join(tried))
    # P0-1：doctor 也报"开窗会走哪个后端"——这是无 Qt 环境里唯一披露 Tk 的入口，
    # 让用户/agent 一眼知道第一次开窗的交互质量。
    if ok and str(ok).lower().startswith('qt'):
        _win_be = _msg.t_ui('Qt（PyQt5 可用）')
    elif ok:
        _win_be = _msg.t_ui('Tk（未装 Qt；想要 Qt 界面：pip install "mpltweak[qt]"）')
    else:
        _win_be = _msg.t_ui('无（无法开窗调图，仅 CLI 可用）')
    print(_msg.t_ui('开窗后端   :'), _win_be)
    try:
        from matplotlib.backends import qt_compat
        print(_msg.t_ui('Qt 绑定      :'), qt_compat.QtCore.__name__)
        ns = getattr(qt_compat.QtCore.Qt, 'CursorShape', qt_compat.QtCore.Qt)
        shapes = {n: getattr(ns, n, None)
                  for n in ('SizeHorCursor', 'SizeVerCursor',
                            'SizeFDiagCursor', 'SizeBDiagCursor')}
        print(_msg.t_ui('光标枚举     :'), shapes)
        if any(v is None for v in shapes.values()):
            print(_msg.t_ui('             -> 有缺失：会静默退回 matplotlib 光标（功能不受影响，'
                  '只是边框/角的形状提示弱一点）'))
    except Exception as e:                         # noqa: BLE001
        print(_msg.t_ui('Qt 绑定      : 不可用 ->'), e)
    try:
        from matplotlib.axes import Axes
        print('set_box_aspect:', hasattr(Axes, 'set_box_aspect'),
              _msg.t_ui('(缺则 colorbar 宽度无法钉住，其余功能正常)'))
    except Exception:
        pass
    # 0.1.11 收尾批：结尾补**一行结论**（"能不能用 / 缺什么 / 怎么办"）。只新增这一行，
    # 上面所有既有行原样保留（明细是排查用的，不重排、不删除）。
    # tkinter 只 import 探测（不建窗），用来区分"缺 Qt 也缺 tkinter"与"有 tkinter
    # 但没有显示环境"——两种不可用的下一步不一样。
    try:
        import tkinter                        # noqa: F401 - 只探测能否 import
        _tk = True
    except ImportError:
        _tk = False
    print(_msg.t_ui('结论       :'), _doctor_verdict(True, ok, _tk))
    return 0


# ==== §7 主流程：main（开窗调图） ====

def main(argv=None):
    from . import messages   # 退出码常量（方案 A：只用 EXIT_USAGE / EXIT_OK）
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument('script', nargs='?',
                    help=_msg.t_ui('要调图的用户脚本（里面不需要任何本工具的代码）'))
    ap.add_argument('--fig', type=int, default=-1, help=_msg.t_ui('调第 N 张图（默认最后一张）'))
    ap.add_argument('--params', default=None, help=_msg.t_ui('参数输出路径'))
    ap.add_argument('--force-resume', action='store_true',
                    help=_msg.t_ui('脚本在参数保存后被改过也强行套用旧参数'
                         '（默认不套用，避免静默覆盖你的手动改动）'))
    ap.add_argument('--doctor', action='store_true',
                    help=_msg.t_ui('只做环境自检（后端/绑定/版本/光标），不跑脚本'))
    ap.add_argument('--dry-run', action='store_true',
                    help=_msg.t_ui('不开窗，只验证能抓图并存参数'))
    ap.add_argument('--verbose', action='store_true',
                    help=_msg.t_ui('打印全部交互日志（缩放开始 axN <手柄> 等），排查用'))
    # 0.1.15（N3）：launch 过去**完全没有** --json —— 开窗这条路的失败（后端不可用 /
    # 脚本自己报错 / 没留下图）只能从 stderr 的中文行里猜。这里只补**失败**出口：
    # 成功路径照旧开窗、不产 JSON（开窗是长时间交互，机器格式在那条路上没有意义）。
    # 注意：这会吃掉原本透传给你脚本的 `--json`；要透传请写 `mpltweak launch x.py -- --json`。
    ap.add_argument('--json', action='store_true',
                    help=_msg.t_ui('失败时往 stdout 打一条机器可读 JSON'
                         '（成功路径不开窗以外的输出不受影响；要透传给脚本用 --）'))
    # L36（0.1.20）：`--lang` 必须在这里注册。此前它「只有 apply/describe/check 认」，
    # 于是 `launch --lang en s.py` 会把 `en` 当成**脚本路径**（报"找不到脚本: …\en"），
    # `s.py --lang en` 则静默把旗标塞进脚本的 sys.argv。
    # 口径与 `--json` / `--params` 一致：**认出来的归工具，认不出的才透传**；要给脚本
    # 传 `--lang` 请用 `--`（`mpltweak s.py -- --lang en`），这条从 0.1.15 起就是既有规则。
    ap.add_argument('--lang', default=None, choices=('auto', 'zh', 'en'),
                    help=_msg.t_ui('界面语言：auto=跟随系统（默认），或强制 zh / en'))
    args, passthrough = ap.parse_known_args(argv)
    _msg.set_lang(args.lang)          # None / 'auto' ⇒ 回到 MPLTWEAK_LANG → locale

    def _json_fail(stage, error_code, error, next_step, rc):
        """失败出口（0.1.15 / N3）：`--json` 时把结论打成**一条**可解析 JSON，再返回同一 rc。

        三条不变：① 退出码口径一个字不改（JSON 只是把已写在 stderr 的那句话说成机器可读的）；
        ② `error` 恒英文纯 ASCII（这是机器通道，与 apply 的 t_en 口径一致）；
        ③ `next_step` 是**建议不是保证** —— 按"这条失败最常见的成因"给，工具不保证它一定
        跑得通（docs/agent.md 的 stage 对照表里写清了这层语义）。
        """
        if args.json:
            _msg.emit_json({'ok': False, 'error_code': error_code,
                            'stage': stage, 'error': error,
                            'next_step': next_step})
        return rc

    if args.doctor:
        return doctor()
    if not args.script:
        sys.stderr.write(_msg.t_ui('[launch] 需要脚本路径，或用 --doctor 做环境自检\n'))
        return _json_fail('params', 'no_script',
                          'launch needs a script path (or --doctor)',
                          'mpltweak launch --doctor', messages.EXIT_USAGE)

    script = os.path.abspath(args.script)
    if not os.path.exists(script):
        sys.stderr.write(_msg.t_ui('[launch] 找不到脚本: %s\n') % script)
        return _json_fail('params', 'file_not_found',
                          'script not found: %s' % script,
                          'point it at an existing .py file, then: '
                          'mpltweak launch %s' % _msg.shell_path(script),
                          messages.EXIT_USAGE)

    # ==== §7.1 开窗前的提示：布局引擎冲突与可重跑预检 ====
    # 布局引擎冲突（constrained_layout / figure.autolayout）必须**在开窗之前**提示：
    # 引擎会在每次绘制时重算轴位置，用户拖完看到的是"位置弹回去/验证不一致"，
    # 而病因在脚本第 N 行（外部审阅第 4 条）。开窗后再提醒已经白拖了。
    from . import layoutwarn
    for _line in layoutwarn.messages_for(layoutwarn.scan(script)):
        sys.stderr.write(_line + '\n')

    # 0.1.10 可重跑预检：脚本**马上要被执行**（开窗取图），先把静态能看出来的风险
    # 提示一次（等人输入会卡到超时 / 要参数会直接报错 / 要弹窗会再开一个窗口）。
    # 纯静态、只写 stderr、**绝不阻塞开窗**（见 _emit_preflight_hint）。
    # 0.1.11 收尾批：预检结论顺手带出来（out=），"要参数"这种**马上就会失败**的情形
    # 再补一句开窗专属的人话（预检那条是 apply / launch 共用文案，落点在写回）。
    _pf_res = {}
    _emit_preflight_hint(script, args_passed=bool(passthrough), out=_pf_res)
    _emit_needs_args_action(script, _pf_res)

    # B 组两段式（0.1.11 收尾批）**早段**：跑用户脚本之前先打个招呼。
    # 用户以为卡死的是这段静默（后端 import + 跑脚本，几十秒都可能）；只说不落戳 ——
    # 此刻还不知道窗开不开得成（脚本报错 / 不留图都会走到这里），落戳会把用户真正的
    # 第一次开窗提示吃掉（落戳统一在"真要建窗口"那一刻，见下面那处调用）。
    _emit_first_open_hint(dry_run=args.dry_run, stage='early')

    # ==== §7.2 后端与工作目录：Qt 候选探测、Tk 回退、切 cwd/sys.path ====
    # Qt 后端必须在用户脚本之前设置。Qt 绑定/版本差异（PyQt5/PyQt6/PySide2/PySide6、
    # 老版本没有 QtAgg 名字）全在这里兜住：逐个候选后端真建一次 canvas 验证，
    # 都不行就给一句人话（而不是甩 traceback）。
    import matplotlib
    import matplotlib.pyplot as plt
    backend_name, backend_err = _detect_backend()
    if backend_err is not None:
        # 注意：此时**还没有**改过 cwd（chdir 在下面才做），所以无需恢复。
        # 这里曾经写的是 os.chdir(old_cwd)，而 old_cwd 在下面才赋值 →
        # 抛 UnboundLocalError，把一段友好提示变成看不懂的 traceback。
        # 0.1.11 收尾批：这段从"三行零散提示"改成**一整句人话**（文案走 messages 表
        # 'launch_backend_missing'，中英齐备）—— 说全后果：脚本没跑、盘上没动；
        # 再给两条可照抄的下一步（装后端 / 只走 CLI）。
        sys.stderr.write(_backend_missing_msg(backend_err) + '\n')
        return _json_fail('preflight', 'no_backend',
                          'no interactive backend: Qt and Tk both failed to '
                          'create a canvas',
                          'pip install pyqt5, then: mpltweak launch %s'
                          % _msg.shell_path(script), 4)
    # P0-1 方案 B：PyQt5 不在 → Tk 开窗。这里在真正开窗前打一句人话（stderr），
    # 绝不污染 stdout（--json / MCP 的机器可读输出仍干净）。
    _emit_backend_hint(backend_name, dry_run=args.dry_run)

    # 以脚本所在目录为工作目录（脚本里的相对路径/存图位置才和平时一致），
    # 并把脚本目录加进 sys.path —— runpy.run_path 不像 `python x.py` 那样自动加，
    # 脚本里 `from toolbox import ...` 之类的同目录导入会失败。
    old_cwd = os.getcwd()
    script_dir = os.path.dirname(script)
    os.chdir(script_dir)
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)
    sys.argv = [script] + list(passthrough)

    # ==== §7.3 执行用户脚本：拦 plt.show / matplotlib.use / plt.close ====
    # 1) 执行用户脚本（拦掉它自己的 plt.show，避免卡住）
    #    同时把 matplotlib.use / plt.switch_backend 临时变成空操作：
    #    科研脚本常硬写 matplotlib.use('Agg')（批量出图用），那会把我们的交互后端顶掉。
    #    Agg/Qt 只影响"怎么显示"，不影响 savefig 结果，所以忽略这行是安全的，
    #    而且用户脚本一个字都不用改。
    real_show = plt.show
    real_use = matplotlib.use
    real_switch = plt.switch_backend
    real_close = plt.close
    plt.show = lambda *a, **k: None
    matplotlib.use = lambda *a, **k: None
    plt.switch_backend = lambda *a, **k: None
    # 科研脚本八件套常在存图后 plt.close(fig)：那会把图关掉，启动器就挂不上窗了。
    # 交互调图期间把 close 临时变空操作——图保留给启动器挂窗，脚本本身零改动。
    plt.close = lambda *a, **k: None
    _real_stderr = sys.stderr
    _err_probe = _CountingStderr(_real_stderr)
    sys.stderr = _err_probe
    try:
        runpy.run_path(script, run_name='__main__')
    except SystemExit:
        pass
    except Exception as e:
        plt.show = real_show
        matplotlib.use = real_use
        plt.switch_backend = real_switch
        os.chdir(old_cwd)
        traceback.print_exc()
        sys.stderr.write(_msg.t_ui('[launch] 用户脚本执行出错，已中止\n'))
        # stage='writeback'：launch 的"写回"就是**执行你的脚本**这一步（apply 那边
        # 对应 AST 写回）。跑不起来 ⇒ 下一步是脱离工具直接跑一遍，拿完整报错。
        return _json_fail('writeback', 'script_failed',
                          'the script raised %s: %s'
                          % (type(e).__name__, e),
                          'run it directly to see the full traceback: python %s'
                          % _msg.shell_path(script), messages.EXIT_USAGE)
    finally:
        plt.show = real_show
        matplotlib.use = real_use
        plt.switch_backend = real_switch
        plt.close = real_close
        sys.stderr = _real_stderr            # P2-10：计数器只罩住脚本那一段

    # P2-10：脚本自己刷了多少行 stderr？给一句汇总（不折叠、不吞它的输出）
    if _err_probe.lines:
        print(_msg.t_ui('[launch] 提示：上面的 stderr 里有 %d 行来自**你的脚本自身**'
                        '（工具自己的提示都带 [launch]/[mpltweak] 前缀）')
              % _err_probe.lines, file=sys.stderr, flush=True)

    # ==== §7.4 抓图与后端兜底：把已有图重新挂回 Qt ====
    # 2) 抓脚本留下的图
    nums = plt.get_fignums()
    if not nums:
        os.chdir(old_cwd)
        sys.stderr.write(_msg.t_ui('[launch] 脚本没有留下可交互的图（可能已 close 或只存盘）\n'))
        return _json_fail('writeback', 'no_figures',
                          'the script left no interactive figure (closed or '
                          'save-only)',
                          'make sure the script creates a figure and leaves it '
                          'open, then: mpltweak launch %s'
                          % _msg.shell_path(script), 2)
    # 兜底：脚本若通过别的途径（如 `from matplotlib import use` 的引用、
    # 或某个库在 import 时切后端）把后端弄成非交互，就把已有图**重新挂回 Qt**——
    # switch_backend 会保留 figure 对象、只换 canvas，图内容不丢。
    be = str(matplotlib.get_backend()).lower()
    if not (be.startswith('qt') or be.startswith('tk')):
        sys.stderr.write(_msg.t_ui('[launch] 脚本把后端设成了 %s，正在把已有图重新挂回 QtAgg…\n') % be)
        for _b in ('QtAgg', 'Qt5Agg', 'Qt6Agg', 'TkAgg'):
            try:
                plt.switch_backend(_b)
                break
            except Exception:
                continue
        be = str(matplotlib.get_backend()).lower()
    if not (be.startswith('qt') or be.startswith('tk')):
        os.chdir(old_cwd)
        sys.stderr.write(_msg.t_ui('[launch] 后端仍是非交互（%s），窗口无法显示。\n') % be)
        sys.stderr.write(_msg.t_ui('[launch] 请临时注释脚本里的 matplotlib.use(...) 那行再试。\n'))
        # stage='finalize'：脚本跑通了、图也有了，**收尾**时后端仍不满足交互。
        return _json_fail('finalize', 'backend_not_interactive',
                          'the backend is non-interactive after the script ran: '
                          '%s' % be,
                          'comment out matplotlib.use(...) / '
                          'plt.switch_backend(...) in the script, then: '
                          'mpltweak launch %s' % _msg.shell_path(script), 2)
    if args.fig >= 0:
        if args.fig >= len(nums):
            sys.stderr.write(_msg.t_ui('[launch] 只有 %d 张图，取不到第 %d 张\n')
                             % (len(nums), args.fig))
            # stage='params'：这是**你给的** --fig 越界（脚本与图都没问题）。
            return _json_fail('params', 'fig_out_of_range',
                              'only %d figure(s); --fig %d is out of range'
                              % (len(nums), args.fig),
                              'drop --fig (defaults to the last figure): '
                              'mpltweak launch %s' % _msg.shell_path(script), 2)
        targets = [args.fig]
    else:
        targets = list(range(len(nums)))       # 默认：每张图都可交互
    idx = args.fig if args.fig >= 0 else len(nums) - 1

    from .toolbox import gaitu
    params = _params_path(script, args.params)
    os.makedirs(os.path.dirname(params), exist_ok=True)
    stem = os.path.splitext(params)[0]

    print(_msg.t_ui('[launch] 脚本: %s') % os.path.basename(script), flush=True)
    if len(targets) > 1:
        print(_msg.t_ui('[launch] 共 %d 张图，全部可交互：%s')
              % (len(nums),
                 ' | '.join('%d:%s' % (k + 1, _summarize(plt.figure(nums[k])))
                            for k in targets)), flush=True)
    else:
        print(_msg.t_ui('[launch] 正在调第 %d 张（共 %d 张）：%s')
              % (idx + 1, len(nums), _summarize(plt.figure(nums[idx]))), flush=True)
    print(_msg.t_ui('[launch] 关窗后只写参数，不改代码: %s') % params, flush=True)
    print(_msg.t_ui('[launch] 提示：请用**英文输入法**（中文输入法下 c/s/g/x/y 等字母快捷键会直接上屏）'),
          flush=True)
    if len(targets) > 1:
        print(_msg.t_ui('[launch] 提示：%d 个窗口，**全部关闭**后本进程才会退出（窗口标题带 [图 k/%d]）。'
              '一次关完按 Ctrl+Q；每关一个就立刻存一份参数（没改过的不存），随时停也不会白改')
              % (len(targets), len(nums)), flush=True)

    # B 组（首次使用体验，0.1.11）：**第一次**开窗给一句安慰/预期（stderr）。两段式的
    # **第二段**（默认 stage='window'）：位置是"**真要建窗口的那一刻**"（下面 gaitu 才
    # 开始建窗）—— 早段（跑脚本之前）已经说过一句（那时**不落戳**），这里才是落戳点。
    # 为什么早段不落戳：更早的位置连"有没有图、开不开得成窗"都还不知道，脚本报错 /
    # 没留图 / 找不到脚本这些**根本没开成窗**的路径会把一次性戳吃掉，用户真正的第一次
    # 开窗反而看不到这句话（2026-10-13 实测：test_launch.py 的顶层 CLI 子进程用例会
    # 走到那个位置，跑一次 pytest 就把 ~/.mpltweak/first_open_shown 写了出来）。
    # 与后端提示同款：dry-run 不开窗不打（也不落戳 —— dry-run 只建窗不 show，但它
    # 不是"开窗调图"，同样不该消耗这个戳）、MPLTWEAK_NO_HINT=1 静默、每个用户只打
    # 一次（新戳 first_open_shown，与 tk_hint_shown 互不影响）。
    _emit_first_open_hint(dry_run=args.dry_run)

    # ==== §7.5 挂控制器：每图一个 Tweak，各写自己的参数文件 ====
    # 多图：每张图各挂一个控制器、各写自己的参数文件（``<stem>.fig<k>.json``，
    # 文件名带图号，就是它最终的正式参数）；关窗时按"哪张真被改过"决定留不留，
    # 并把最后改动的那张另存成主文件 —— 所以不必预先 --fig 指定。
    clock = [0]
    tws, tmps = {}, {}
    _preexisting = set()     # 开窗前已存在的参数文件（上一轮的成果，清理时必须保）
    _closed_cnt = [0]        # 已关闭的主图窗口数（用于主图全关后清理 ? 键位表窗口）
    for k in targets:
        _f = plt.figure(nums[k])
        tmps[k] = '%s.fig%d.json' % (stem, k)
        # 记下"开窗前就已存在"的：收尾清理只能删本轮新产生的临时产物，绝不能删
        # 上一轮留下的正式参数（t2-A4/A5、t6-H5 实测：多图分轮调图时第 2 轮会把
        # 第 1 轮改好的记录删掉，用户白调一轮）。
        if os.path.exists(tmps[k]):
            _preexisting.add(k)
        tws[k] = gaitu(_f, export_path=tmps[k],
                       quiet=not args.verbose, fig_index=k, n_figs=len(nums),
                       edit_clock=clock)

        # 关这一个窗口就把它的参数立刻落成正式文件（<stem>.fig<k>.json + 主文件），
        # 不等"所有窗口关完"那一步汇总——这样只关一部分 / Ctrl+C 都不会白改。
        # 没改动的不落（与"没动过不产生文件"一致）。
        # 顺序：gaitu 的关窗回调先注册、先执行（写临时文件），本回调在其后读它。
        def _on_close(_event, _k=k):
            _closed_cnt[0] += 1
            try:
                _cur = _read_json(tmps[_k])
                if _cur is None or bases.get(_k) is None or _cur == bases[_k]:
                    return                         # 这张没动过 → 不留参数文件
                # 这张图自己的参数文件（tmps[_k]，也就是它最终的正式参数）已由
                # gaitu 的关窗回调写好；这里只补一份主文件 = 最后关闭且改动过的那张。
                with open(tmps[_k], 'rb') as _fsrc:
                    _blob = _fsrc.read()
                with open(params, 'wb') as _fdst:
                    _fdst.write(_blob)
            except Exception as _e:                # noqa: BLE001
                print(_msg.t_ui('[launch] 警告：关窗落参数失败（第 %d 张图，可能未保存）：%s')
                      % (_k + 1, _e), file=sys.stderr, flush=True)
            finally:
                # 主图全关 → 顺带关掉 ? 键位表窗口，否则 plt.show() 会等它，
                # 用户忘了关键位表窗口会导致进程不退出。
                if _closed_cnt[0] >= len(targets):
                    try:
                        # 注意：_close_help_fig 只在本模块的**函数内部** import 过，
                        # launch 的全局命名空间里并没有它 —— 直接调用会 NameError，
                        # 而原先的 `except Exception: pass` 会把错误吞掉，于是帮助窗
                        # 关不掉、plt.show() 永远等它 → 进程挂死（t2-A1 / t6-H6，
                        # 真 Qt E2E 实测 12s 不退出 vs 注入后 3.2s 正常退出）。
                        from . import toolbox as _tb
                        _tb._close_help_fig()
                    except Exception as _e:        # noqa: BLE001
                        print(_msg.t_ui('[launch] 警告：关闭帮助窗口失败: %s') % _e,
                              file=sys.stderr, flush=True)

        _f.canvas.mpl_connect('close_event', _on_close)

        # Ctrl+Q = 关掉全部窗口（多图时不必逐个去找那个压在后面的小窗口）。
        # 此处 plt.close 已被恢复成真的 close（见上文 real_close），所以能真关。
        def _on_quit_key(_event):
            if str(getattr(_event, 'key', '') or '').lower() == 'ctrl+q':
                try:
                    plt.close('all')
                except Exception:                  # noqa: BLE001
                    pass

        _f.canvas.mpl_connect('key_press_event', _on_quit_key)

    # ==== §7.6 续调与关窗之后：套回上次参数、落参数、摘要与写回命令 ====
    # 续调：把所有参数文件各自套回它记录的图（多图会话会有主文件 + <stem>.fig<k>.json）
    def _resume_files():
        _stem = os.path.splitext(params)[0]
        _d, _base = os.path.dirname(_stem), os.path.basename(_stem)
        _out = {}
        if os.path.isdir(_d):
            for _fn in sorted(os.listdir(_d)):
                if (_fn.startswith(_base + '.fig') and _fn.endswith('.json')
                        and not _fn.endswith('.writeback.json')):
                    _out[_fn] = os.path.join(_d, _fn)
        if os.path.exists(params):
            _out.setdefault(_base + '.json', params)
        picked, seen = [], set()                  # 带图号的文件优先，按图号去重
        for _fn, _pth in sorted(_out.items()):
            try:
                with open(_pth, 'r', encoding='utf-8-sig') as _f:
                    _k = json.load(_f).get('fig_index')
            except Exception:
                _k = None
            if isinstance(_k, int):
                if _k in seen:
                    continue
                seen.add(_k)
            picked.append(_pth)
        return picked

    resumed = []
    for pth in _resume_files():
        try:
            with open(pth, 'r', encoding='utf-8-sig') as f:
                _d2 = json.load(f)
        except Exception:
            continue
        if not isinstance(_d2, dict):
            # 顶层非对象（数组/字符串/数字/null）：文件坏/非本工具产物 → 跳过
            # 续调（"续调是尽力而为"，绝不让 GUI 开窗崩掉）。这是续调流程里
            # 最早的一道守卫：_d2.get('fig_index') 要求 dict，非对象会在这里
            # 崩（AttributeError），到不了 _apply_saved。与 params.load 的
            # 0.1.10 修复同款语义 —— 这类输入归参数层坏文件，但不打断主流程。
            print(_msg.t_ui('[mpltweak] 参数文件顶层不是对象，跳过续调：%s') % pth,
                  file=sys.stderr, flush=True)
            continue
        # 脚本比参数文件新 → 代码在保存参数之后被手动改过。此时套用旧参数会
        # 静默覆盖那些改动（你改的看不到效果，关窗写回还会把旧值盖回去），
        # 所以默认跳过；确要接着上次调，加 --force-resume。
        try:
            _stale = os.path.getmtime(script) > os.path.getmtime(pth) + 1.0
        except OSError:
            _stale = False
        if _stale and not args.force_resume:
            print(_msg.t_ui('[launch] 注意：脚本在参数保存之后被修改过 → 本次不套用该参数'
                  '（避免覆盖你的手动改动）；确要续调加 --force-resume'), flush=True)
            continue
        kk = _d2.get('fig_index')
        if kk is None:
            kk = idx if idx in tws else None        # 主文件没图号 → 默认图
        elif not isinstance(kk, int) or kk not in tws:
            continue                                 # 陈旧图号（脚本图数减少/换 --fig）→ 跳过，
                                                     # 不回退到 idx 套错图
        if kk is None:
            continue
        n_saved = _apply_saved(plt.figure(nums[kk]), pth)
        if n_saved:
            resumed.append((kk, n_saved))
    if resumed:
        print(_msg.t_ui('[launch] 已套用上次参数：%s（接着上次调；删掉这些文件可从头开始）')
              % '; '.join(_msg.t_ui('第 %d 张 %d 项') % (k + 1, n)
                          for k, n in sorted(resumed)), flush=True)
    apply_to = resumed[-1][0] if resumed else idx

    def _read_json(p):
        try:
            with open(p, 'r', encoding='utf-8-sig') as f:
                return json.load(f)
        except Exception:
            return None

    # 基线：挂载 + 续调都做完之后再落，避免把这两步误报成"本次改动"
    for k in tws:
        tws[k].export(path=tmps[k])
    bases = {k: _read_json(tmps[k]) for k in tws}

    def _copy_to_params(src_k):
        with open(tmps[src_k], 'rb') as fsrc:
            blob = fsrc.read()
        with open(params, 'wb') as fdst:
            fdst.write(blob)

    def _clean_temps(keep_keys=()):
        _prune_fig_params(tmps, keep_keys, protect=_preexisting)

    if args.dry_run:
        src_k = apply_to if apply_to in tws else sorted(tws)[-1]
        _copy_to_params(src_k)
        # --dry-run 绝不做破坏性清理：原先这里是 _clean_temps()（keep=()），会把
        # **之前所有**已存参数删光（t2 / t6-H5 实测：跑完只剩主文件）。
        # 但本轮自己产生的 .lock 是标记文件、不是用户数据，要收掉（否则残留，
        # t5-S13）。
        for _k in tws:
            try:
                os.remove(tmps[_k] + '.lock')
            except OSError:
                pass
        print(_msg.t_ui('[launch] --dry-run：参数已写出（未开窗）-> %s') % params, flush=True)
        os.chdir(old_cwd)
        return 0

    real_show()                # 开窗交互；关窗时各图把自己的最终状态写进临时文件

    changed = []
    for k in sorted(tws):
        cur = _read_json(tmps[k])
        if cur is not None and bases.get(k) is not None and cur != bases[k]:
            changed.append((tws[k].edit_seq, k))

    chosen = None
    if changed:
        changed.sort()
        chosen = changed[-1][1]                # 最后被改动的那张

    if chosen is None:
        print(_msg.t_ui('[launch] 未检测到改动，未写参数文件'), flush=True)
    else:
        # 每张改过的图在它的关窗回调里已经写好了自己的 <stem>.fig<k>.json（= 正式参数），
        # 这里只需把"最后改动的那张"另存一份主文件（兼容旧读者）；未改动的随后清掉。
        _copy_to_params(chosen)
        if len(changed) == 1:
            print(_msg.t_ui('[launch] 已记录第 %d 张图（共 %d 张）的参数: %s')
                  % (chosen + 1, len(nums), params), flush=True)
        else:
            print(_msg.t_ui('[launch] 已记录 %d 张图的参数（%s）：')
                  % (len(changed), os.path.dirname(params)), flush=True)
            for _, k in sorted(changed):
                print(_msg.t_ui('[launch]   第 %d 张 -> %s%s')
                      % (k + 1, os.path.basename(tmps[k]),
                         _msg.t_ui('（并另存主文件）') if k == chosen else ''), flush=True)
        # 无感化①（0.1.10，用户 2026-10-13 认可）：关窗后给一条**可直接复制**的
        # 写回命令，省掉"记住怎么写回"这一步。只在**这次真的存过参数**时打
        # （没拖过时上面那支 `chosen is None` 直接走开，零噪音）。
        # 注意：这里只是把命令**打出来** —— 写回仍然要用户自己确认，
        # **绝不自动改用户源码**（红线）。
        from . import messages as _msgs
        print(_msgs.t('next_step_apply',
                      params=_rel_for_shell(params, old_cwd),
                      cmd=_next_step_cmd(script, params,
                                         script_args=passthrough,
                                         params_given=bool(args.params),
                                         base=old_cwd)), flush=True)
    _clean_temps([k for _, k in changed])

    # 3) 变化摘要（只读，不写代码）
    if chosen is None:
        # 没有任何改动时主文件根本不存在 —— 原先会去 open 它，然后打印
        # "参数摘要读取失败: [Errno 2]"，一切正常却像出了事（t6-M17）。
        print(_msg.t_ui('[launch] 参数摘要: 无改动'), flush=True)
    else:
        try:
            with open(params, 'r', encoding='utf-8-sig') as f:
                data = json.load(f)
            before = {a['index']: a['pos'] for a in bases[chosen].get('axes', [])}
            ch = _diff(before, data)
            print(_msg.t_ui('[launch] 位置变化: %s') % ('; '.join(ch) if ch else _msg.t_ui('无')),
                  flush=True)
        except Exception as e:
            print(_msg.t_ui('[launch] 参数摘要读取失败: %s') % e, flush=True)

    os.chdir(old_cwd)
    return 0


if __name__ == '__main__':
    sys.exit(main())

# --- BEGIN AUTO NAV ---
# 自动生成，不要手改：`python tools/gen_nav.py --write` 重算、`--check` 校验
# （tests/test_hygiene.py 有一条同判据的用例）。行号 = 该 def / 小节的**定义行**（1-based）。
# 索引块固定放在文件末尾：它自己不占用任何函数之前的行，重算不会挤动行号。
# 缩进一级的 §N.M 是**类内/函数内小节**（挂在包含它的顶层定义或它的大号分区下）。
# §1 文件头：外部启动器的目标、用法与退出码（行 2-65，0 个顶层函数）
#     （本分区没有顶层函数）
# §2 基础设施：UTF-8 流、stderr 计数与后端探测（行 66-218，5 个顶层函数）
#     _force_utf8_streams                             68
#     _CountingStderr                                 89
#     _detect_backend                                176
#     _backend_tk_hint                               194
#     _backend_missing_msg                           204
# §3 一次性提示与戳记：Tk 回退 / 首次开窗 / 可重跑预检 / 需要参数（行 219-449，8 个顶层函数）
#     _mpltweak_home                                 221
#     _tk_hint_stamp_path                            232
#     _emit_backend_hint                             242
#     _first_open_hint                               286
#     _first_open_stamp_path                         311
#     _emit_first_open_hint                          321
#     _emit_preflight_hint                           376
#     _emit_needs_args_action                        416
# §4 参数文件与「关窗后怎么写回」的命令（行 450-499，5 个顶层函数）
#     _params_path                                   452
#     _summarize                                     460
#     _rel_for_shell                                 465
#     _quote_arg                                     478
#     _next_step_cmd                                 483
# §5 收尾清理、摘要与续调：把上次的参数套回图上（行 500-654，3 个顶层函数）
#     _prune_fig_params                              502
#     _diff                                          524
#     _apply_saved                                   546
# §6 doctor 结论与 doctor 子命令（行 655-749，2 个顶层函数）
#     _doctor_verdict                                657
#     doctor                                         678
# §7 主流程：main（开窗调图）（行 750-1248，1 个顶层函数）
#     main                                           752
#         §7.1 开窗前的提示：布局引擎冲突与可重跑预检                   816
#         §7.2 后端与工作目录：Qt 候选探测、Tk 回退、切 cwd/sys.path   839
#         §7.3 执行用户脚本：拦 plt.show / matplotlib.use / plt.close   873
#         §7.4 抓图与后端兜底：把已有图重新挂回 Qt                   923
#         §7.5 挂控制器：每图一个 Tweak，各写自己的参数文件            1008
#         §7.6 续调与关窗之后：套回上次参数、落参数、摘要与写回命令           1076
# --- END AUTO NAV ---
