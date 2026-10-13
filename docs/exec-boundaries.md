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
| `4` | **开窗但找不到可用图形后端**（顶层 `launch`） | 无 Qt / 无 tkinter 的环境开窗 → `[launch] 找不到可用的交互后端…`，并提示装 `mpltweak[qt]` 或 `python3-tk`；纯 CLI 命令（describe / check / apply）不受影响 |

两点要记住：**脚本自己的 `sys.exit(N)` 不会成为命令的退出码**（执行在子进程里，工具照常给结论）；
**"脚本找不到"在顶层是 3、在子命令是 2**（已记进 CHANGELOG 的 CH-6，未统一）。
这张表只覆盖实测过的情形，没列到的请以 `--help` 为准。

## 写回之前的可重跑预检（0.1.10）

`apply --write` 默认要**重跑你的脚本**（重跑层 + 语义层，一次写回 2~5 遍）。于是「跑不通 / 会卡住」
的脚本总是等到写回时才暴露 —— 现在改成**动手之前先说**：默认会重跑时，`apply --write` 与开窗入口
都会**静态**看一眼源码，命中下面五类就打一段提示（人读 + `--json` 的 `preflight.risks[]`）。

| 风险（`preflight.risks[].kind`） | 静态判据 | 建议的下一步 |
|---|---|---|
| `missing_dependency` | 模块级 `import` 的顶层包在本机 `find_spec` 不到 | 先装依赖；或 `--verify-fast`（只查语法）/ `--keep-unverified`（保留写回，先用版本控制） |
| `needs_args` | 读 `sys.argv` / 用 argparse（`required=True` 算强证据） | `mpltweak apply <脚本> --write -- <你的参数...>`（`--` 后原样透传） |
| `network` | `import requests` / `urllib` / `socket` / `http` … | 同 `--verify-fast` / `--keep-unverified` |
| `stdin` | `input()` / 读 `sys.stdin` / `getpass` | 同上（重跑时没人输入，会一直等到超时） |
| `gui` | `import tkinter` / PyQt / `filedialog` / `plt.show()` / `matplotlib.use('TkAgg')` | 同上（无头跑不通；在有桌面的机器上还可能真的弹窗） |

三条纪律（都要守住）：

* **预检不执行你的脚本**：只有 `ast` 读源码 + `importlib.util.find_spec` 查"包在不在"
  （find_spec 只查规格、**不执行模块体**）。
* **没风险就一个字都不说**：干净脚本零噪音；`--no-verify` / `--verify-fast` / `--dry-run` 本来就不重跑，
  也不提示。`--` 后面已经给了参数时不再喊"需要参数"。
* **预检自己出岔子一律放行**：读不了 / 语法错误 / 目录 / 找不到一律当"无风险"，绝不让预检成为新的失败点。

`--json` 顶层字段（与 `files[]` 平级）：`preflight.ok`（bool）与 `preflight.risks[]`，每条含
`kind` / `attribution`（就是下面那张表同一套标签）/ `detail` / `evidence`（命中的 import / 调用点）/
`next_step`（可直接执行的命令）/ `hint`（英文短句）。开窗入口只往 **stderr** 打一次，**不阻塞开窗**；
`MPLTWEAK_NO_HINT=1` 可关掉这类提示。

## 验证窗口里你改过脚本 → 工具不动你的文件（0.1.10）

写回验证要重跑脚本，可能等几十秒到几分钟。这期间你要是回编辑器保存过一次，工具**不会**覆盖你的改动：

* 读源码时记一份 **sha256 内容指纹**（按内容算，不看修改时间——编辑器常常只改一个字符、或"保存了其实没变"，时间/大小这类粗判据会被骗过），每个落盘点之前重算一遍；
* **三处落盘点**都守着：**原位写盘**、**插入调整块**、**验证失败后的回滚（从备份恢复）**；
* 指纹不一致 → **不写、不回滚、不动你的文件**，输出里说明「检测到脚本在验证期间被外部修改；
  本次写回已放弃，你的改动未被覆盖。若仍要写回，请先保存并重跑本命令」；`--json` 给
  `files[].external_modified: true` + `reason: "external_modified"` + `next_step`（可直接执行的命令）。

边界（如实说明，不声称覆盖）：

* 判据是"查完再写"，与真正落盘之间有**微秒级**的 TOCTOU 缝隙 —— 本机制针对的是**分钟量级**的
  验证窗口，不是这个缝隙。
* **`revert --write` 不在此守卫范围**（它是独立命令、没有验证等待、窗口是秒级）—— 待办候选。
* 读不了文件（不存在 / 坏字节）时守卫**放行**：写盘异常自己会兜底，守卫不该把正常写回拦下。

## 跑不起来时，是谁的错、下一步做什么（0.1.10）

`apply --write` 的验证重跑失败时，输出会把失败**归因**，并给一句**可照抄的下一步**：

| 归因（`--json` 的 `attribution`） | 什么时候 | 打印的下一步 |
|---|---|---|
| `script_cannot_run` | 脚本自己跑不起来：缺依赖（`ModuleNotFoundError`）、缺数据文件（`FileNotFoundError`）等 | 工具没有改动你的源码（已回滚）。下一步：`mpltweak check <脚本> --static`（不执行脚本，只做静态检查） |
| `script_needs_args` | 脚本依赖命令行参数，而这次没有透传（`sys.argv[1]` / argparse `required=True`） | 下一步：`mpltweak apply <脚本> --write -- <你的参数...>`（`--` 后的参数会**原样透传**给重跑验证） |
| `script_needs_gui_or_headless_impossible` | 无头环境跑不通：脚本要开窗口 / 要交互（tkinter、Qt、`plt.show`、`input()`） | 下一步：改用 `mpltweak check <脚本> --static`；确要写回可用 `--verify-fast`（只查语法，弱保证）或 `--keep-unverified`（保留写回，请先用版本控制） |
| `tool_bug` | `internal_error`：**本工具自己**抛了未预期的异常，与你的脚本无关 | 你的源码一个字节都未修改（已回滚）。这是工具的 bug，请把这段输出报告给我们 |

读法：

* **脚本侧**（前三类）与**工具侧**（`tool_bug`）是两件完全不同的事 —— 前者要你去修脚本或换个
  用法，后者要我们修工具。过去两者共用一句「验证失败，已回滚」，用户既不知道是谁的错、也不知道
  下一步做什么（独立评审点名，0.1.10 修）。
* `--json` 同时给三个机器可读字段：`attribution`（上表第一列）、`attribution_detail`（稳定英文
  短原因，如 `missing_dependency` / `missing_data` / `argparse` / `static_args` / `display` /
  `unknown`）、`next_step`（可直接执行的命令模板）。agent 客户端据此自动分支，不必读中文文案猜。
* **判不出原因时归 `script_cannot_run` 并在文案里注明"无法确定原因"**，绝不猜成 `tool_bug`
  （会把人吓到）或 `script_needs_args`（会把人带偏去传参数）。
* **退出码数值语义一个字都没改**（`0` / `1` / `2` / `3` / `4` 的含义与上表一致，0.1.9 已对外
  文档化，用户 CI 依赖）：只加了文案与 JSON 字段。同为退出码 `2`，现在能一眼看出是脚本侧还是
  工具侧。

失败归因只挂在 **apply 写回验证**这条链上；`check` 的失败路径（跑不起来 / 语法错 / 文件不存在）
仍是原来的 `error_code`，不带归因字段。

开窗后端的选择（Qt / Tk 对照、探测顺序、`doctor` 输出）与交互排障见
[interaction.md](interaction.md)。

## 测试用沙箱的边界（0.1.10）

项目自带的真跑测试（语料级不变量扫描）在 `MPLTWEAK_SANDBOX_ROOT` 指定的"游乐场"里执行陌生脚本，
它的保护边界如实写明：沙箱判定"这是不是在跑用户脚本"依据 **`argv[0]` 是否落在游乐场内**，而真跑链路是
`apply` → `python -m mpltweak.verify …` → 脚本，那个进程的 `argv[0]` 是 mpltweak 模块路径 ⇒ 它落在
**harness 侧，只有"文件层"生效**（越界读写删实测被拦、磁盘无残留）；**socket / subprocess 层在这条链路里
不生效**，本版靠**静态预筛**兜住（含联网或起子进程调用的脚本一律不真跑）。精细的判定规则（例如"argv 里
出现游乐场内的脚本路径也算脚本进程"，需同时避免 CLI 自身被误判而拦死 verify 的 spawn）列入
**0.1.11 候选**。