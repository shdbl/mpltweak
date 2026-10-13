# 文件系统沙箱报告（t9）—— sitecustomize + sys.addaudithook

> 让后续"全量真跑"能在不碰用户电脑任何文件的前提下执行陌生语料脚本。
> 本任务**未执行任何真实语料脚本**（只用自己写的捣蛋/正常脚本验证）。

## 0 四层防护（设计）
| 层 | 机制 | 状态 |
|---|---|---|
| 1 游乐场隔离 | 每份脚本跑在全新临时目录（cwd=该目录），相对读写都落这里 | ✅（t4/t7 runner 已有；t10 沿用） |
| **2 审计钩子沙箱（核心）** | `tests/sandbox/sitecustomize.py`：`sys.addaudithook` 拦截文件写/删/改/建目录、子进程、网络 | ✅ 本任务实现 |
| 3 超时 | worker 超时（60~90s） | ✅（沿用） |
| 4 防弹窗预检 | 静态关键词筛查（TkAgg/tkinter/filedialog/PyQt/plt.show() → 判据不适用） | ✅（test_corpus_invariants.py 已有）+ 本任务补**运行时禁 GUI**（第 5 层实质：meta_path + use 锁 + env 硬钉 Agg + tkinter 事后补丁） |

## 1 注入方式（实测有效）
- 把 `tests/sandbox/`（含 `sitecustomize.py`）加进子进程 `PYTHONPATH` —— Python 启动时
  site 模块**自动 import** sitecustomize，守卫随即安装。
- **PYTHONPATH 合并修复**（t4/t7 runner 的坑）：`test_corpus_invariants.py::_cli_env()`
  现在在 `MPLTWEAK_SANDBOX_ROOT` 已设时把 `tests/sandbox` **并进** PYTHONPATH
  （`tests/sandbox;<树>/src`），**不覆盖** src；同时指好游乐场内 `MPLCONFIGDIR`、
  `TMP/TEMP/TMPDIR`、`PYTHONDONTWRITEBYTECODE`。worker 子进程 env 同规则。
- 整条链实测：apply（harness 级）→ verify 子进程 `python <脚本>`（**完整守卫**）
  → 脚本内文件操作。见 §4 E 组证据。

## 2 守卫策略（进程分级）
- **完整守卫**（`sys.argv[0]` 是游乐场内的真实文件 = 正在跑用户脚本的进程）：
  ① 文件写/删/改名/建目录/chmod/chdir 目标不在 `MPLTWEAK_SANDBOX_ROOT` 内 → PermissionError；
  ② 起子进程（subprocess / os.system / os.exec / os.spawn / os.popen）→ PermissionError；
  ③ 网络（socket.connect / connect_ex / sendmsg / sendto）→ PermissionError。
- **harness 级守卫**（argv[0] 不在游乐场 = apply/verify/collect 本体）：
  只拦 ①（文件越界）；②③ 放行（harness 要自己 spawn verify 子进程）。
  这样 verify_semantic 的 collect（runpy 执行脚本）里脚本的文件操作仍受保护。
- **读取一律放行**（库导入需要读 site-packages / 数据文件）。
- 事件覆盖（CPython 3.10 官方审计事件表）：`open` / `os.open`（写 flags）、
  `os.remove`/`os.unlink`/`os.rmdir`/`os.mkdir`/`os.makedirs`/`os.chmod`/`os.chown`/
  `os.truncate`、`os.rename`/`os.replace`/`os.link`/`os.symlink`（src+dst）、`os.chdir`、
  `os.system`/`os.popen`/`os.exec`/`os.spawn`/`os.posix_spawn`/`subprocess.Popen`、
  `socket.connect`/`connect_ex`/`sendmsg`/`sendto`。
- **fd 形态放行**：`os.fdopen(fd,…)` 的 `open` 审计 path 是**整数 fd**（实测会拦到
  `'4'`）——已放行；底层那次 `os.open` 仍会被审计（writeback._atomic_write 实测通过）。

## 3 禁 GUI（运行时，弹窗兜底）
弹窗不是文件越界、audit hook 管不了 → 同一 sitecustomize 里加**四道**（从早到晚）：
1. **meta_path 查找器**：`tkinter/_tkinter/PyQt5/PyQt6/PySide/PySide2/PySide6/wx/gi/Gdk/gtk/Gtk/Qt/PyQt`
   导入即 `raise ImportError('沙箱：禁止 GUI 库导入（…）')` —— 库都进不来，窗口不可能开。
2. **matplotlib.use 锁**：monkeypatch 拒绝非 Agg 族后端（agg/pdf/ps/svg/template/cairo/
   inline），导入后强制 `use('Agg', force=True)`；**并硬钉 `os.environ['MPLBACKEND']='Agg'`**
   （不是 setdefault —— 宿主投毒 Qt5Agg/TkAgg 会被原样继承，本进程的子进程链同样中招；
   2026-10-12 抢鼠标事故的同类隐患）。
3. **事后补丁 `_poison_tk_windows()`**（2026-10-12 补）：tkinter/_tkinter 若**已经**在
   `sys.modules` 里（meta_path 只管"还没导入的模块"），把 `Tk`/`Toplevel` 的 `__init__`
   与模块属性、以及 `_tkinter.create` 换成"一调用就 RuntimeError"的替代品。
   **本函数不主动 import tkinter** —— 主动导入会让 D1「import tkinter 被拦」失效。
4. 静态关键词预检（第 4 层）**保留**；另有 `tests/test_hygiene.py` 两道 AST 调用点级棘轮
   兜测试侧（子进程调用点必须过 `_pin_child_env` 闸门 + 不许创建真实 GUI 对象）。
5. 开关：`MPLTWEAK_SANDBOX_NOGUI=1` 关闭禁 GUI（文件/子进程/网络守卫不关）。
   t10 分类口径：被禁 GUI 拦下 = stderr 含 `禁止 GUI` 或 `只允许无窗口后端` → 记 **na_gui**，
   并记录命中原因（哪个模块导入被拦 / 是否 `matplotlib.use('TkAgg')` / 是否被事后补丁拦）。

> **为什么禁 GUI 只能在 import 层拦（关键事实）**：CPython 的审计事件表里**没有**
> "创建窗口对象"这一类事件（`open` / `subprocess.Popen` / `socket.connect` 那些有，
> 窗口没有）。所以 `sys.addaudithook` 对弹窗**无能为力** —— 拦 GUI 只能靠
> ①导入层（meta_path）②后端层（use 锁 + env 钉 Agg）③事后补丁 这三层，
> 这也是"MPLBACKEND=Agg 挡不住 `tkinter.Tk()`"的原因（两者是不同闸门）。

## 4 验证证据（tests/test_sandbox.py，19 项 ALL PASS）
### A. 拦得住（捣蛋脚本，全部被拦 + 守卫日志）
| 用例 | 行为 | 结果 |
|---|---|---|
| A1 删除游乐场外真实文件 | `os.remove(<外部文件>)` | 被拦（`沙箱：os.remove 被禁止`），文件仍在；日志 `BLOCK os.remove` |
| A2 往游乐场外写文件 | `open(<外部>, 'w')` | 被拦（`沙箱：open(write) 被禁止`），目标不存在 |
| A3 起子进程 | `subprocess.run([...])` | 被拦（`沙箱：subprocess.Popen 被禁止`） |
| A4 连 socket | `socket.connect(('127.0.0.1', 9))` | 被拦（`沙箱：socket.connect 被禁止`） |

拦截日志样例（MPLTWEAK_SANDBOX_LOG，A1 实跑）：
```
SANDBOX armed: root=... script_process=True nogui=False
SANDBOX: matplotlib.use locked to Agg-family
BLOCK os.remove: <abs>
```

### B. 不误拦（同等重要）
| 用例 | 行为 | 结果 |
|---|---|---|
| B1 正常绘图 | matplotlib 建图 + savefig('out.png')（到游乐场 cwd） | ✅ rc=0，png 生成，后端 Agg |
| B2 字体缓存/numpy | MPLCONFIGDIR 指游乐场内 + import numpy | ✅ rc=0（字体缓存落在游乐场） |
| B3 __pycache__ | PYTHONDONTWRITEBYTECODE=1 | ✅ 游乐场外零 .pyc |

### C. 对照（守卫真的在起作用）
| 用例 | 同一捣蛋删除脚本 | 结果 |
|---|---|---|
| C1 不开守卫（plain env，无 sitecustomize） | `os.remove(受害者)` | ✅ **成功**：文件真被删（rc=0） |
| C2 开守卫（sandbox env） | 同上 | ✅ **被拦**：文件还在（rc≠0） |

### D. 禁 GUI
| 用例 | 行为 | 结果 |
|---|---|---|
| D1 | `import tkinter` | ✅ 被拦（ImportError：禁止 GUI 库导入），无窗口 |
| D2 | `matplotlib.use('TkAgg')` | ✅ 被拦（use 锁：只允许无窗口后端）或回退 Agg，不弹窗 |
| D2b | `matplotlib.use('Qt5Agg')` | ✅ 被拦 |
| D3 | `matplotlib.use('Agg', force=True)` + 绘图 | ✅ 不误拦 |

### E. 链路（apply → verify → 脚本，守卫全程生效）
| 用例 | 行为 | 结果 |
|---|---|---|
| E3 | `sandbox_env()` 的 PYTHONPATH 合并 | ✅ 沙箱目录 与 `<树>/src` 并存 |
| E1 | `apply fig.py --params … --write`（沙箱 env，cwd=游乐场） | ✅ rc=0，figsize 原位写回 (8.5,5.5)，verify 子进程在守卫内 savefig 成功 |
| E2 | 同上但脚本带 `open(<游乐场外>,'w')` 副作用 | ✅ verify 子进程（完整守卫）拦截 → apply 失败 + **回滚**，脚本字节不变，外部文件不存在 |

### F. 无人值守（t10 防卡超时）
| 用例 | 行为 | 结果 |
|---|---|---|
| F1 | `MPLTWEAK_SANDBOX_NO_STDIN=1` → `input()` | ✅ 读即 EOFError（可归 na_stdin，不占超时配额） |
| F1b | stdin=DEVNULL（无 monkeypatch） | ✅ 自然 EOF → EOFError，**不卡** |

## 5 环境变量速查
| 变量 | 作用 |
|---|---|
| `MPLTWEAK_SANDBOX_ROOT` | 游乐场根（绝对路径）。不设 → sitecustomize 完全惰性 |
| `MPLTWEAK_SANDBOX_LOG` | 守卫拦截日志（预打开 fd 追加，不递归） |
| `MPLTWEAK_SANDBOX_NOGUI` | `=1` 关闭禁 GUI（文件/子进程/网络守卫不关） |
| `MPLTWEAK_SANDBOX_NO_STDIN` | `=1` 把 `builtins.input` 换成读即抛 EOFError（无人值守真跑） |
| `MPLCONFIGDIR` / `TMP`/`TEMP`/`TMPDIR` | 由 `sandbox_env()`/`_cli_env()` 指进游乐场 |
| `PYTHONDONTWRITEBYTECODE` | 由注入代码设为 1（__pycache__ 不落盘） |

## 6 已知边界（诚实声明）
- **audit hook 不是完美沙箱**：`ctypes` / 直接内存修改 / 加载 C 扩展等手法理论上可绕。
  威胁模型 = **防"无意乱写乱删"**（陌生脚本的绝对路径写、相对路径逃逸、子进程、联网、
  弹窗），**不是对抗恶意攻击**。恶意攻击者写进你 home 的代码本来就已拿到你的权限。
- harness 级守卫对 collect（runpy 执行脚本）只拦文件操作，**子进程/网络两个口子留给
  harness**（verify 本身要 spawn）——已记录，属可接受缺口。
- `os.chdir` 到游乐场外被拦：个别要 chdir 到外部数据目录的脚本会失败（读取仍可用
  绝对路径）；这是"相对写不逃逸"的代价，写进口径。
- 性能：每份沙箱脚本启动多一次 matplotlib import（use 锁），~1s；相对全量真跑总时长可接受。
- t4 的 240 份（verify_full）是**加守卫之前**跑的，没有 na_gui/na_stdin 类 —— t10 报告
  不得把两批数字直接相减比较（口径差异需注明）。

### 6.1 禁 GUI 的五条边界（2026-10-12 补，逐条都可复现）
1. **CPython 没有"创建窗口对象"的审计事件** → `sys.addaudithook` 对弹窗无能为力；
   禁 GUI 只能在**导入层 + 后端层 + 事后补丁**拦（见 §3）。这是本层"补丁式"而非
   "沙箱式"的根本原因。
2. **`MPLBACKEND=Agg` 与"禁 GUI"是两道不同的闸门**：前者只决定 matplotlib 用哪个后端，
   **拦不住** `tkinter.Tk()` / `QApplication()` / `Toplevel()` 这类直接建窗；反之 meta_path
   拦住了库，却管不了"用别的名字已经拿到的类"。两层必须并存（2026-10-12 用户报
   "有人抢鼠标"时，测试夹具里那句 `tk.Tk()` 正是 env 层拦不住的那一类）。
3. **只对"经 PYTHONPATH 注入过 sitecustomize 的进程"生效**：没走注入的子进程、
   或压根没 import sitecustomize 的进程（例如 pytest 本体、命令行手工起的 python）
   **不受任何禁 GUI 保护**；`MPLTWEAK_SANDBOX_NOGUI=1` 时四道闸门全关（真窗口测试
   test_real_window.py 必须用它 —— 那时屏幕上**真的会有窗口**）。
4. **事后补丁只覆盖"已经在 sys.modules 里的 tkinter/_tkinter"**，且**在补丁之前就已经
   绑定到别的名字上的类**（`from tkinter import Tk` 早于补丁）理论上仍能建窗；
   本函数**不主动 import tkinter**（主动导入会让 D1「import tkinter 被拦」失效），
   所以"补丁后新导入的 tkinter"靠的是 meta_path 那道闸门 —— 两者互补、不重叠。
5. **补丁会替换 `tkinter.Tk`/`Toplevel` 本身**：沙箱进程里 `isinstance(x, tkinter.Tk)`
   这类自省判断会失真（沙箱里本就不该构造窗口，代价可接受）；另外 `ctypes` 直接调
   libtk/libtcl/libQt 建窗**拦不住** —— 与第 1 条同属"不是完美沙箱"的威胁模型。

> 测试侧配套（2026-10-12）：`tests/test_hygiene.py` 加两道 **AST 调用点级**棘轮 ——
> ① 每个 `subprocess.*` 调用点必须过 `_pin_child_env`（硬钉 MPLBACKEND=Agg），
> 未过的必须在 `UNPINNED_CALLSITES` 里登记理由；② 不许创建真实 GUI 对象
> （白名单只有 `test_real_window.py`）。两条棘轮各有反向验证用例（去掉 pin / 加一行
> `tk.Tk()` → 必须报红）。

## 7 文件清单
- `tests/sandbox/sitecustomize.py` —— 守卫（audit hook + 禁 GUI 四道 + NO_STDIN）
- `tests/sandbox/sandbox_env.py` —— 子进程环境构造（PYTHONPATH 合并等）
- `tests/test_sandbox.py` —— 验收（A/B/C/D/E/F，19 项 ALL PASS；模块级脚本，不被 pytest 收集）
- `tests/test_corpus_invariants.py` —— `_cli_env()` 沙箱注入（MPLTWEAK_SANDBOX_ROOT 已设时）
- `tests/test_hygiene.py` —— 测试侧卫生棘轮（2026-10-12 加两道 AST 调用点级：子进程
  调用点必须过 `_pin_child_env` 闸门 / 不许建真实 GUI 对象；含反向验证用例）
