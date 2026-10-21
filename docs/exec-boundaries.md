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
| `mpltweak apply <脚本> --write --no-verify` | **不会** | 跳过重跑与语义验证（**写盘后的语法检查仍会做**）：跑不通、布局没落到位都不会被发现，也不会自动回滚 —— 等于关掉唯一的安全网 |
| `mpltweak apply <脚本> --write --dry-run` | **会**（**默认档** + 参数里有"原位写不了"的字段时） | 只把参数写出去、**不落盘**；但"到底要不要退块"只能**实测**参数与源码现值是否不同 ⇒ 会**先跑一遍**脚本（`writeback.py:3869 # _pending_vs_live` 在 `:3884 # dry_run` 的 dry-run 早退**之前**）。`--verify-fast` / `--no-verify` / `--no-semantic` 下**不跑**；参数里没有这类字段时也不跑 |
| `mpltweak revert <脚本> [--write]` | **不会** | 纯文件操作 |
| `mpltweak schema` / `mpltweak doctor` | **不会** | 不碰你的脚本 |
| `mpltweak mcp`（MCP 服务端） | **会**，每次 `tools/call` 一次 | 每次调用都在**独立子进程**里跑：脚本 `sys.exit` 带不走 server（实测：跑完一个会 `sys.exit(3)` 的脚本后 server 仍能应答 `tools/list`），上一次调用改的全局状态也不会串味 |

> 上表由探针脚本逐条实测（探针第一行写一个标记文件，跑一遍看文件出没出现）：
> 复测记录 `audit_018/promo/t14_verify.log` 与 `t14_verify2.log` 是**本地记录，未随仓库发布**；
> 可重跑的等价入口在仓库里：`python tests/test_agent_api.py`（`describe` / `apply --json` 路径）与 `python tests/test_check.py`。

**唯一"完全不执行脚本"的静态通道**：`mpltweak check <脚本> --static` —— 只做 AST 扫描 + 布局引擎
冲突检测，不导入、不运行（实测：探针脚本的标记文件没有出现，而 `check --no-text` 会写出来）。
适合"脚本跑起来有副作用"或"CI 里只想先查一眼"的场景。代价是它**只看代码、不量真实渲染尺寸**，
`--static` 通过不等于图没问题（输出里也会自己说明"没检查什么"）。

**工具自己的副作用**（脚本自己造成的副作用由脚本负责）：

| 类型 | 具体是什么 |
|---|---|
| 写文件 | `.tweak_params/<脚本>.json`（参数）、`.tweak_params/<stem>.fig<k>.json`（多图会话里每张图一份正式参数；同名 `.lock` 是会话接管标记，launch.py:470-487 # _prune_fig_params、toolbox.py:1403 # export_path）、`.tweak_params/<stem>.writeback.json`（写回元数据，writeback.py:5412 # writeback.json、apply.py:449 # writeback.json）、`<脚本>.tweak.bak`（**固定名**备份：**首份优先**，只在不存在时创建、之后的写回**不覆盖**它；默认 `revert` 用它回到最初原件）、`<脚本>.tweak.<pid>.bak`（**每次写回各留一份**的专属副本，保留"最近 5 份 + **最早 1 份**"；`revert --first` 用它）、`<脚本>.revert.bak`（revert 前的留档）、`_verify_state.json`、`<脚本>.apply.lock`（并发写回锁，writeback.py:3200-3213 # script_lock） |
| 写在哪儿 | **不全是脚本旁**：分**三处** —— 脚本旁 `<脚本目录>/.tweak_params/`、系统临时区 `mpltweak_state_*.json`、用户家目录 `~/.mpltweak/`；逐项见下面「落盘位置（三处）」那张表 |
| 联网 | **工具本体不联网**（不查版本、不回传，源码里没有任何网络调用）。但你的脚本可能联网 —— 每次执行都是一次真的执行 |
| 缓存 | Python 会写 `__pycache__/`；matplotlib 可能写字体缓存 |
| 工作目录 | 脚本执行期间 `cwd` 被切到**脚本所在目录**，结束后恢复，所以脚本里的相对路径照常能找到数据 |
| 导入路径 | 执行期间脚本所在目录会被插进 `sys.path` —— 插的是**子进程**的，你的进程不受影响 |
| 全局 rcParams | `describe` / `check` / `apply --write` 的验证都跑在**子进程**里，脚本改过的全局 `rcParams` 不会留到你的进程（库级实测：`collect_isolated` 前后父进程 `font.size` 10.0 → 10.0，`sys.path` / `sys.modules` 也无残留）。只有**开窗**仍是本进程 |
| 退出码（**子进程**命令：`describe` / `check` / `apply` 验证重跑 / `mcp`） | 脚本自己调 `sys.exit` / `os._exit` **不会**带走命令：执行在子进程里（`collect_isolated` 起子进程 verify.py:1104 # subprocess.run、重跑层 writeback.py:5550 # cmd），工具照样输出结果 |
| 退出码（**开窗**） | **本进程**跑脚本（`runpy.run_path`，launch.py:815 # runpy.run_path），只 `except SystemExit`（launch.py:816 # SystemExit）⇒ 脚本里的 `sys.exit()` 被吞掉，但 `os._exit()` 会**直接杀掉窗口进程** |

**落盘位置（三处，不是"全部落在脚本旁"）**：

| 落在哪儿 | 具体是什么 | 备注 |
|---|---|---|
| **脚本旁** `<脚本目录>/.tweak_params/` | 参数 `.tweak_params/<脚本>.json`、多图参数 `.tweak_params/<stem>.fig<k>.json` 与会话接管标记 `<stem>.fig<k>.json.lock`（launch.py:470-487 # _prune_fig_params、toolbox.py:1403 # export_path）、写回元数据 `.tweak_params/<stem>.writeback.json`（writeback.py:5412 # writeback.json、apply.py:449 # writeback.json）、备份 `<脚本>.tweak.bak` / `<脚本>.tweak.<pid>.bak` / `<脚本>.revert.bak`、验证状态中转 `_verify_state.json`、并发锁 `<脚本>.apply.lock`（writeback.py:3200-3213 # script_lock） | 备份的锚点是**脚本**、不是参数文件 —— `--params other/x.json` 只影响从哪儿读参数，备份**不会**跟着跑到 `other/`，`revert` 也**不需要**再传 `--params`（0.1.8 放在参数文件旁边的旧位置只作为兼容兜底读一次） |
| **系统临时区**（`tempfile` 的默认目录：`/tmp`、`%TEMP%`） | `mpltweak_state_*.json` —— `describe` / `check` 的采集子进程把图状态 dump 到这里，父进程读完即删（`tempfile.mkstemp(prefix='mpltweak_state_')`，verify.py:1091 # tempfile.mkstemp；清理 verify.py:1107-1119 # _unlink） | 与脚本目录无关 ⇒ 采集不会在你的项目里留下中转文件；超时 / 起不来时也走同一条清理 |
| **用户家目录** `~/.mpltweak/` | `tk_hint_shown` —— "Tk 后端提示只打一次"的戳文件（launch.py:204-211 # _tk_hint_stamp_path） | `MPLTWEAK_HOME` 可改这个家目录（测试隔离 / 多环境共用一台机器）；每个用户 / 每次安装一个，与具体脚本无关 |

**同一个脚本会被重复执行**：开窗 1 次、每次 `check` / `describe` 1 次、每次 `apply --write` 1 次。
所以**别把它指向"跑一次会花钱 / 会改数据 / 会发消息"的脚本** ——「不适用场景」讲的就是这类。

## 退出码（实测）

只列**实测过**的组合（合并树 + `PYTHONPATH=<src>`；原始记录 `audit_018/promo/t28_exit_matrix.txt` 是**本地记录，未随仓库发布** ——
可重跑的等价入口：`python tests/test_agent_api.py`、`python tests/test_check.py`、`python tests/test_sandbox.py`）：

| 码 | 什么时候 | 实测例子 |
|---|---|---|
| `0` | 成功（含"没发现问题""未检测到改动"） | `mpltweak --help`、`doctor`、`schema`、`schema --help`、`describe <脚本>`、`check <干净脚本>`、`check --static`、`launch <脚本> --dry-run` |
| `1` | **跑通了，但结果需要处理** | `check <有问题的脚本>`（有 problem）、`apply --params <describe --all-figs 的嵌套产物> --write`（`no_axes`：原位改不了、需要退块） |
| `2` | **跑不起来**（**所有入口同一口径**，0.1.11 方案 A） | 脚本找不到：顶层 `mpltweak nosuch.py`、`launch` / `describe` / `check` / `apply` / `revert <不存在的脚本>` 全部 `[xxx] 找不到脚本: …`；顶层未知参数：`mpltweak --bogus` → `[launch] 需要脚本路径…`；脚本自己报错 / 采集失败；`apply` 没有参数文件或参数文件不合法（`no params: …`）；工具自身异常（`internal_error`）。原表此行的尾部例子：`check --tol inf`、`schema --bogus`、`revert` 还没有可回退的备份 |
| ~~`3`~~ | **已退役**（0.1.11 方案 A）：常量 `messages.EXIT_SCRIPT` 保留（外部可能 import），但**任何路径都不再返回 3** | 0.1.9 时它背着三件事（顶层找不到脚本 / 顶层脚本报错 / 顶层未知参数），现已全部并入 `2`；子命令早就是 `2` |
| `4` | **开窗但找不到可用图形后端**（顶层 `launch`） | 无 Qt / 无 tkinter 的环境开窗 → `[launch] 找不到可用的交互后端…`，并提示装 `mpltweak[qt]` 或 `python3-tk`；纯 CLI 命令（describe / check / apply）不受影响 |

两点要记住：**脚本自己的 `sys.exit(N)` 不会成为命令的退出码**（执行在子进程里，工具照常给结论）；
**"脚本找不到"、"脚本自己报错"、"顶层未知参数"在全部入口都是 `2`**（0.1.11 方案 A 统一；
0.1.9 的 `3` 已退役）。口径收敛成一句话：**0 = 没问题/成功；1 = 发现问题（check）；2 = 跑不起来；4 = 没有可用图形后端**。
这张表只覆盖实测过的情形，没列到的请以 `--help` 为准。

## 输出归属：stdout 与 stderr（0.1.12）

`apply` 的人读输出分两条流。这条契约只为一件事：**管道/重定向能干净地区分"数据 / 错误"** ——
`mpltweak apply foo.py > out.txt` 里，`out.txt` 只该有报告，错误该走终端（`2>`）而不是混进文件
（0.1.11 及以前人读错误行也走 stdout，2026-10-15 拍板改成现在这样）。

| 流 | 装什么 | 典型行 |
|---|---|---|
| **stdout** | 数据 / 报告 | 成功摘要（`[OK] 已原位写回 N 处…`）、`-v` 的逐处清单、只读预览的「会改：…」与可粘贴骨架、`--write` 的落地行、`no_change`、进度行（`正在验证…`）、参数文件抬头（`参数文件:` / `脚本:` / `画布:`）、`--snippet`、`--json` 的**载荷** |
| **stderr** | **人读错误** | 用法/输入错误（脚本找不到、没有参数文件、`--no-verify` 与 `--verify-fast` 互斥、`--strict` 撞布局引擎）、参数文件坏（读不出来 / 缺 `version` / 越界）、拒写与「未写回」、外部改动被跳过（脚本比参数新）、写回失败与回滚、验证重跑失败 + 失败归因（脚本侧 / 工具侧）、`internal_error` |

为什么要划这条线：判据是"**这次请求有没有兑现**"，不是"消息长得凶不凶"。

* **模糊的那几类留在 stdout**（算"过程披露"，不算错误）：`--verify-fast` / `--no-verify` 的
  降级提醒、"这个字段本机没动"、布局引擎提示、`[i]` 类的分层说明。它们既不宣告成功也不宣告
  失败 —— 成功与否由**退出码 + stderr 是否为空**共同决定；把它们塞进 stderr 会让
  "stderr 非空 ⇒ 有错"这条管道规则失效。
* **`--json` 一字不动**：载荷仍只走 stdout（`json.loads(stdout)` 必须成功），错误码仍在载荷里
  （`error_code` / `files[].reason` / `attribution` / `next_step`），退出码口径不变；人读行在
  `--json` 下照旧全部让到 stderr，**stdout 永远不会出现半句人话**。有一条出口本来就是"只给
  JSON"：`--json` + 脚本不存在时**不打**人读行（错因在载荷里），所以那种情况的 stderr 是空的。
* **退出码与流无关**：`0 / 1 / 2 / 4` 的归属见上一节，本次改动一个数值都没动。

自证入口：`python -m pytest -q tests/test_cli_output.py`（成功/预览 stderr 必须为空、五类失败
出口的错误行只在 stderr、`--json` 载荷仍可解析且键集不变）；`python tests/test_agent_api.py`
的 §24 是同一契约在 agent 视角下的版本。

## 写回之前的可重跑预检（0.1.10）

`apply --write` 默认要**重跑你的脚本**（重跑层 + 语义层：原位路径**正常只跑 1 遍**（参数里有"原位写不了"的字段时会
**先额外跑 1 遍**判要不要退块，`writeback.py:3869 # _pending_vs_live`）、取不到目标图状态时回退 2 遍、需要归因时最多 3 遍；
要插调整块的路径会**逐个候选插入点重试**，每试一个再跑一遍 —— 逐条口径见
[verify.md 的「写回会跑几遍脚本」](verify.md#写回会跑几遍脚本)）。于是「跑不通 / 会卡住」
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
* **没风险就一个字都不说**：干净脚本零噪音；`--no-verify` / `--verify-fast` 本来就不重跑，也不提示；
  `--dry-run` 也不提示（预检只在真写回时才做，`apply.py:744-746`）—— 但它**仍可能**跑一遍脚本（见上表）。
  `--` 后面已经给了参数时不再喊"需要参数"。
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
* **这条守卫没有"强制放行"开关**：`--force` 管的是**另一档** —— "**参数比脚本旧**"（存完参数之后脚本被手改过，默认跳过并给 `reason: "stale"`）；`apply --write --force` 才会照样套用参数（`apply.py:751 # force` 定义、`:875 # _stale` 判定）。验证窗口内检测到的外部修改一律放弃写回。
* 读不了文件（不存在 / 坏字节）时守卫**放行**：写盘异常自己会兜底，守卫不该把正常写回拦下。

## 跑不起来时，是谁的错、下一步做什么（0.1.10）

`apply --write` 的验证重跑失败时，输出会把失败**归因**，并给一句**可照抄的下一步**：

| 归因（`--json` 的 `attribution`） | 什么时候 | 打印的下一步 |
|---|---|---|
| `script_cannot_run` | 脚本自己跑不起来：缺依赖（`ModuleNotFoundError`）、缺数据文件（`FileNotFoundError`）等 | 工具没有改动你的源码（已回滚）。下一步：`mpltweak check <脚本> --static`（不执行脚本，只做静态检查） |
| `script_needs_args` | 脚本依赖命令行参数，而这次没有透传（`sys.argv[1]` / argparse `required=True`） | 下一步：`mpltweak apply <脚本> --write -- <你的参数...>`（`--` 后的参数会**原样透传**给重跑验证） |
| `script_needs_gui_or_headless_impossible` | 无头环境跑不通：脚本要开窗口 / 要交互（tkinter、Qt、`plt.show`、`input()`） | 下一步：改用 `mpltweak check <脚本> --static`；确要写回可用 `--verify-fast`（只查语法，弱保证）或 `--keep-unverified`（保留写回，请先用版本控制） |
| `tool_bug` | `internal_error`：**本工具自己**抛了未预期的异常，与你的脚本无关 | 你的源码一个字节都未修改（已回滚）。这是工具的 bug，请把这段输出报告给作者 |

读法：

* **脚本侧**（前三类）与**工具侧**（`tool_bug`）是两件完全不同的事 —— 前者要你去修脚本或换个
  用法，后者要工具作者修工具。过去两者共用一句「验证失败，已回滚」，用户既不知道是谁的错、也不知道
  下一步做什么（独立评审点名，0.1.10 修）。
* `--json` 同时给三个机器可读字段：`attribution`（上表第一列）、`attribution_detail`（稳定英文
  短原因，如 `missing_dependency` / `missing_data` / `argparse` / `static_args` / `display` /
  `unknown`）、`next_step`（可直接执行的命令模板）。agent 客户端据此自动分支，不必读中文文案猜。
* **判不出原因时归 `script_cannot_run` 并在文案里注明"无法确定原因"**，绝不猜成 `tool_bug`
  （会把人吓到）或 `script_needs_args`（会把人带偏去传参数）。
* **退出码口径（0.1.11 方案 A）**：对外只有 `0` / `1` / `2` / `4`；**`3` 已退役**（常量保留、
  任何路径都不再产生）。变化只有一处：原先顶层路径返回 `3` 的三类（找不到脚本 / 脚本报错 /
  未知参数）现在返回 `2` —— **不是软化**（仍是非零失败，不会把错误变成成功），而是把"同一个
  脚本找不到"在顶层与子命令之间的分歧抹平。同为退出码 `2`，仍能一眼看出是脚本侧还是工具侧。

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

### `doctor` 的选项口径

- `mpltweak doctor` **不接受其它选项，也不接受位置参数**（多余参数 ⇒ **rc=2**，usage 走 stderr）—— 与其它子命令一致，不再静默忽略。
- **`doctor` 没有 `--json`**：它的输出是给人看的自检结果，不产出机器可读载荷；给了 `--json` ⇒ **rc=2** 且明确说明（**明拒，不静默忽略**）。
- `-h` / `--help` ⇒ rc=0，打印 doctor 用法。
