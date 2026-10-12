# 执行边界：哪些命令会跑你的脚本

> 本文件是 [README](../README.md) 的展开版，内容自 README 搬出；命令、参数、退出码与实测数值与原文一致。

先说结论：**mpltweak 会执行你的脚本，而且要跑不止一次 —— 它不是一个沙箱。**
脚本在你的机器上、用你的解释器、你的依赖、你的全部权限跑（能读写文件、能联网、能删东西，
取决于脚本自己写了什么）。用之前请确认两件事：这份脚本在无头环境跑一遍是安全的；你手上有它的
版本控制记录。

| 命令 | 会执行脚本吗 | 说明 |
|---|---|---|
| `mpltweak <脚本>`（开窗）／`mpltweak launch <脚本>` | **会**（本进程） | 先跑一次拿到初始布局再开窗；脚本改过的全局 `rcParams` 留在窗口进程里，关窗即结束 |
| `mpltweak describe <脚本>` | **会**（**子进程**） | 采集当前排版；脚本在子进程里以 Agg 无头后端跑，退出码 / `sys.path` / 全局 `rcParams` 都留在子进程 |
| `mpltweak check <脚本>` | **会**（**子进程**） | 文字越界要按**真实渲染尺寸**量，必须真跑一次（`--no-text` 也照样跑） |
| `mpltweak apply <脚本>`（只读预览） | **不会** | 纯 AST 对比 + 参数校验，不碰你的脚本 |
| `mpltweak apply <脚本> --write` | **会**（子进程重跑） | 写回后重跑做"能跑通 + 语义一致"验证，不过就回滚 |
| `mpltweak apply <脚本> --write --verify-fast` | **不会** | 只做语法检查（实测：脚本没被执行、也没重出图） |
| `mpltweak apply <脚本> --write --no-verify` | **不会** | 连语法都不查 —— 等于关掉唯一的安全网 |
| `mpltweak apply <脚本> --write --dry-run` | **不会** | 只把参数写出去 |
| `mpltweak revert <脚本> [--write]` | **不会** | 纯文件操作 |
| `mpltweak schema` / `mpltweak doctor` | **不会** | 不碰你的脚本 |
| `mpltweak mcp`（MCP 服务端） | **会**，每次 `tools/call` 一次 | 每次调用都在**独立子进程**里跑：脚本 `sys.exit` 带不走 server（实测：跑完一个会 `sys.exit(3)` 的脚本后 server 仍能应答 `tools/list`），上一次调用改的全局状态也不会串味 |

> 上表由探针脚本逐条实测（探针第一行写一个标记文件，跑一遍看文件出没出现）：
> 复测见 `audit_018/promo/t14_verify.log` 与 `t14_verify2.log`。

**唯一"完全不执行脚本"的静态通道**：`mpltweak check <脚本> --static` —— 只做 AST 扫描 + 布局引擎
冲突检测，不导入、不运行（实测：探针脚本的标记文件没有出现，而 `check --no-text` 会写出来）。
适合"脚本跑起来有副作用"或"CI 里只想先查一眼"的场景。代价是它**只看代码、不量真实渲染尺寸**，
`--static` 通过不等于图没问题（输出里也会自己说明"没检查什么"）。

**工具自己的副作用**（脚本自己造成的副作用由脚本负责）：

| 类型 | 具体是什么 |
|---|---|
| 写文件 | `.tweak_params/<脚本>.json`（参数）、`<脚本>.tweak.bak`（**写回前**的原文，每次写回覆盖）、`<脚本>.tweak.<pid>.bak`（并发时的专属副本，只留最近 5 份）、`<脚本>.revert.bak`（revert 前的留档）、`_verify_state.json`、`<脚本>.apply.lock` |
| 写在哪儿 | 备份 / 留档放在**参数文件所在目录**：默认 `<脚本目录>/.tweak_params/`；用 `--params other/x.json` 时就落在 `other/`。`_verify_state.json` 与 `.apply.lock` 固定放 `<脚本目录>/.tweak_params/` |
| 联网 | **工具本体不联网**（不查版本、不回传，源码里没有任何网络调用）。但你的脚本可能联网 —— 每次执行都是一次真的执行 |
| 缓存 | Python 会写 `__pycache__/`；matplotlib 可能写字体缓存 |
| 工作目录 | 脚本执行期间 `cwd` 被切到**脚本所在目录**，结束后恢复，所以脚本里的相对路径照常能找到数据 |
| 导入路径 | 执行期间脚本所在目录会被插进 `sys.path` —— 插的是**子进程**的，你的进程不受影响 |
| 全局 rcParams | `describe` / `check` / `apply --write` 的验证都跑在**子进程**里，脚本改过的全局 `rcParams` 不会留到你的进程（库级实测：`collect_isolated` 前后父进程 `font.size` 10.0 → 10.0，`sys.path` / `sys.modules` 也无残留）。只有**开窗**仍是本进程 |
| 退出码 | 脚本自己调 `sys.exit` / `os._exit` **不会**带走命令：执行在子进程里，工具照样输出结果 |

**同一个脚本会被重复执行**：开窗 1 次、每次 `check` / `describe` 1 次、每次 `apply --write` 1 次。
所以**别把它指向"跑一次会花钱 / 会改数据 / 会发消息"的脚本** ——「不适用场景」讲的就是这类。

## 退出码（实测）

只列**实测过**的组合（合并树 + `PYTHONPATH=<src>`；原始记录 `audit_018/promo/t28_exit_matrix.txt`）：

| 码 | 什么时候 | 实测例子 |
|---|---|---|
| `0` | 成功（含"没发现问题""未检测到改动"） | `mpltweak --help`、`doctor`、`schema`、`schema --help`、`describe <脚本>`、`check <干净脚本>`、`check --static`、`launch <脚本> --dry-run` |
| `1` | **跑通了，但结果需要处理** | `check <有问题的脚本>`（有 problem）、`apply --params <describe --all-figs 的嵌套产物> --write`（`no_axes`：原位改不了、需要退块） |
| `2` | **输入 / 用法错误**（子命令层） | `describe` / `check` / `revert <不存在的脚本>`（`[xxx] 找不到脚本: …`）、`apply` 没有参数文件或参数文件不合法（`no params: …`）、脚本抛异常或 `sys.exit` 导致**采集失败**、`check --tol inf`、`schema --bogus`、`revert` 还没有可回退的备份 |
| `3` | **顶层路径**（`mpltweak <脚本>`：不开窗直接跑脚本这条入口） | `mpltweak nosuch.py` → `[launch] 找不到脚本: …`；`mpltweak --bogus` → `[launch] 需要脚本路径，或用 --doctor 做环境自检` |

两点要记住：**脚本自己的 `sys.exit(N)` 不会成为命令的退出码**（执行在子进程里，工具照常给结论）；
**"脚本找不到"在顶层是 3、在子命令是 2**（已记进 CHANGELOG 的 CH-6，未统一）。
这张表只覆盖实测过的情形，没列到的请以 `--help` 为准。

**为什么分成两步**：开窗需要一个图形后端（推荐 PyQt5，约 +150MB），而
服务器 / headless / 纯 agent 场景只用 `describe` / `check` / `apply`（需要 MCP 见后文），
不该为此装一整套 Qt；有些环境本来也已经装了 PySide6 / PyQt6。

**没装 Qt 不等于开不了窗**：Windows / macOS 的官方 Python 安装包自带 `tkinter`，所以只装
`mpltweak` 一般就能直接开窗 —— 此时用的是 **Tk 后端（交互弱化版）**，不是安装失败、也不是没装好。
回退到 Tk 时工具会在 **stderr** 打一句人话提示（`MPLTWEAK_NO_HINT=1` 可静默），并按顺序
QtAgg → Qt5Agg → Qt6Agg → TkAgg 逐个真建 canvas 探测；`doctor` 会多报一行当前开窗后端
（实测：`开窗后端   : Tk（未装 Qt；想要 Qt 界面：pip install "mpltweak[qt]"）`）。

## 常见情况

| 现象 | 处理 |
|---|---|
| 没弹窗 | 运行 `mpltweak doctor` 看诊断 |
| 敲了 `mpltweak launch fig1.py` | 与 `mpltweak fig1.py` 等价（`launch` 是显式子命令） |
| 用 `--params` 写回过，`revert` 却说"还没有可回退的备份" | 备份放在**参数文件旁边**，而 `revert` 默认只找 `.tweak_params/`；把同一个 `--params` 也传给 `revert` |
| 想从头调 | 删掉 `.tweak_params/fig1.json`（不删就是接着上次续调） |
| 多图脚本 | 每张图都会弹窗，**改哪张记哪张**；`mpltweak apply` 逐张写回 |
| 脚本要读数据 / 跑很久 | 先试 `--timeout 600` **放宽**等待。`--no-verify` 会**完全跳过重跑验证**——那是写坏代码时唯一的自动安全网，只在脚本确实无法在无头环境重跑时才用，且用前请确认脚本已在版本控制里 |
| **拖动特别卡** | 按 **空格** 切线框模式：只画边框、坐标轴和文字，**不渲染数据图元**（cartopy 大图实测 494ms → 169ms），挪完再按一次空格恢复 |
| 手动改过代码后再开窗 | 会自动**不套用**上次参数（避免静默覆盖你的改动）并给出提示；确要接着上次调，加 `--force-resume` |
| 脚本里开着 `constrained_layout` / `autolayout` | 开窗与写回时会提示（**带行号**）：布局引擎在每次绘制时重算轴位置，拖拽/写回的位置会被它覆盖。调图前先关掉（或把要精调的轴改成 `add_axes([...])`，引擎不管这类轴） |
> 上表由探针脚本逐条实测（探针第一行写一个标记文件，跑一遍看文件出没出现）。
只列**实测过**的组合（合并树 + `PYTHONPATH=<src>`；本次发行前实测）：
