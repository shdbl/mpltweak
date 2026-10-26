# `--json` 契约（0.1.17 冻结：`apply` + `--check-update`）

这是 `--json` 输出载荷的**冻结契约**：键名、类型、值域、政策一次写清，
之后只准照它来。给两类读者用：**给 agent 做机器分支**（别去读中文文案），**给维护者改代码前
先看这条线在哪**。

一句话政策：**只加不改** —— 加新键可以；改既有键的语义、删键、改值域，必须走**大版本**。

> 本页冻结**两条命令**的载荷：**`apply --json`**（主体，下含顶层键、`files[]` 与五张值域表）
> 与 **`mpltweak --check-update --json`**（只有五个键，见文末自己的一节）。
> `describe` / `check` / `revert` / `launch` 各有自己的载荷形状
> （各自的 `--help` 与 `docs/agent.md` 描述），但它们与本页**共用同一套词汇**：`stage`、
> `error_code`、`attribution` 的取值都出自同一处，所以按值分支的代码可以跨命令复用。

## 顶层键

| 键 | 类型 | 何时出现 | 值域 / 口径 |
|---|---|---|---|
| `script` | string | 恒在 | 脚本的 **basename**（不含目录）；出处 `apply.py` 的 `_emit_json` |
| `ok` | bool | 恒在 | 整批是否算成功；**判"写没写"别看它**（看 `files[].applied`） |
| `files` | array | 恒在（早退时是**空数组** `[]`） | 每份参数文件一条，见下一节 |
| `preflight` | object | 只在**这次真会重跑脚本**时出现 | `{ok: bool, risks: [{kind, attribution, detail, evidence, next_step, hint, packages}]}`；出处 `preflight.py` 的 `analyze` 返回值 |
| `error` | string | 只在**早退失败**载荷上出现 | 一句人话（英文），给人读；机器分支用 `error_code` |
| `error_code` | string | 只在**失败 / 零改动**载荷上出现 | 见下面的 `error_code` 值域表；成功载荷**没有**这个键 |
| `stage` | string | 失败载荷恒有；成功载荷**只有一种例外**：`stage='finalize'`（写回已落盘、收尾降级） | 见下面的 `stage` 值域表（六值） |
| `next_step` | string | 与 `stage` **同进同出** | 一句**可直接复制**的命令（或动作）；是建议不是保证 |

几条容易踩的口径：

* `preflight` 的存在条件 = "这次真的要重跑你的脚本"。`--no-verify` / `--verify-fast` /
  `--dry-run` / `--diff` 四档都**不重跑**，所以这四档的载荷**没有** `preflight` 键
  （出处：`apply.py` 主流程里给 `_pf_res` 赋值的那个条件）。
* `error_code` 取的是**第一个失败文件**的码（多份参数时"有一份没成"就是没成）；
  逐文件的细分原因照旧在 `files[].reason` / `files[].attribution` / `files[].err` 里。
* 顶层 `next_step` 与 `files[].next_step` **不是同一件事**：逐文件那个只覆盖"验证失败归因"
  这一档；顶层这个在没有逐文件值时另给一条（出处：`apply.py` 的 `_failure_next_step`）。

## `files[]` 条目的键

每条都是一个参数文件的结果。**下面这些键全部恒在** —— `_emit_json` 是**显式键集**，缺失值一律
归一成 `null` / `0` / `[]` / `false`（读侧不用防空）。这是"只加不改"能在 agent 侧落地的前提。

| 键 | 类型 | 值域 / 口径 |
|---|---|---|
| `params` | string | 参数文件 basename |
| `reason` | string | 落位结果码：`ok` / `best_effort` / `refused` / `no_change` / `preview` / `bad_params` / `stale` / `no_axes` / `no_fig` / `fail` / `external_modified` / `layout_conflict` / `unknown`（兜底） |
| `style` | string | `auto` / `inplace` / `block`；**永不为 null** —— 没走过写回时是"请求的通道"，走过写回时是"实际通道" |
| `applied` | bool | 磁盘**是否真的改了**；`false` 时不许把文案读成"已写回" |
| `changes` | array | 真正落地的字段令牌，如 `["ax0.pos", "ax1.title_fontsize"]` |
| `block_lines` | int | **块通道**写进去的**整块总行数**（含两行标记、生成时间与说明行）—— **不是"改了几行代码"**，也**没有固定值**（单字段实测 4 / 5 / 6 都有）；**原位通道恒 `0`**（所以同一夹具 `auto`/`inplace` = `0`、`block` > 0，这是**通道差异**，不是结论不同）。想知道改了什么看 `changes` |
| `auto_fallback` | bool | 这次是否由 `auto` 自动退到了块模式 |
| `inplace_reason` | string or null | **原位那一趟**的落位结果（口径同 `reason`） |
| `inplace_changes` | array | 原位那一趟真正改到的字段 |
| `needs_block` | bool | **这次 apply 之后**是否仍有字段没落地（退过块且都落地了就是 `false`） |
| `inplace_needs_block` | bool | 原位那一趟是否因为写不了而退块 |
| `pending` | array | **本次最终**还没落地的字段令牌清单（唯一口径）。`auto` 退块时它是**退块之后**剩下的账；判"这次写完没"看它，**别把 `inplace_pending` 加进来** |
| `inplace_pending` | array | **退块之前、原位那一趟**写不了的字段清单（**留档**，不代表最终结果）：`auto` 退块时用它看"为什么退块"；`--style inplace` 单跑时它通常是 `[]`，账在 `pending` 里。**两者禁止相加** |
| `refused_hint` | string or null | 拒写时的**照着改**指导（0.1.17 起）。**0.1.18 起触发面变了**：只对"引擎能点名"的拒写生成（建轴点与参数条数对不上、载体名不是恰好一次模块层调用…）；`fig.axes[i]` / `plt.gca()` / 函数里建图**已不再必然拒写**（判据表见 [verify.md](verify.md)） |
| `anchor` | string or null | 块的插入位置标记（如 `before`） |
| `attempts` | array | 块落点尝试记录，每项 `{anchor, line, ok, semantic}` |
| `layers` | object | 三层验证的真实状态：`{syntax: bool, rerun: bool or null, semantic: bool or null}`；**null = 这一层没跑**（别再用 `verify_mode` 反推） |
| `err` | string or null | 失败原因文本（给人读；机器分支用 `reason` / `attribution`） |
| `external_modified` | bool | 验证期间脚本被**外部**改过（已回滚、未写回）；正常路径恒 `false` |
| `backup` | string or null | 备份文件路径；`--diff` 预览时**恒 `null`**（预览不复制备份，不给指向不存在文件的路径） |
| `verified` | bool or null | 重跑验证结论。**默认档（`auto` / `inplace` / `block`，全验证）真正写过时不是 `null`** —— 退块那一次也继承原位那趟的结论；`null` 只出现在：`--no-verify` / `--verify-fast` / `--no-semantic`、`no_change` 与 `preview` 这类**没写盘就早退**的路径、以及 `refused`。要判"这次写没写"别用它，看 `applied` |
| `verify_mode` | string or null | `fast`（快速档）或 `null`（其余） |
| `verify_passes` | int | 写回后为验证跑了几遍：`1` = 默认单遍，`2` = 单遍取不到目标图状态已回退两遍，`0` = 这次没为验证跑脚本 |
| `semantic_basis` | string | 语义判定的依据：`live+savefig` / `live` / `''`（空串 = 这次没有语义判定依据） |
| `semantic` | bool or null | 语义验证结论；`null` = 没跑 |
| `warnings` | array | 保留原样 / 降级的说明（含 `mtime_sync_failed` 这种收尾降级） |
| `layout_conflicts` | array | 布局引擎冲突的证据行（"哪一行开了 `constrained_layout` / `figure.autolayout`"） |
| `layout_why` | string or null | 冲突下"这次到底会不会改轴位置"的**判据依据**，五值见下 |
| `attribution` | string or null | 失败归因（脚本侧 / 工具侧），四值见下 |
| `attribution_detail` | string or null | 归因细分，九值见下 |
| `diff` | array | `--diff` 的行级差异，每项 `{line, type, old, new}`；没开 `--diff` 时恒为 `[]` |
| `preview_sha256` | string or null | "将要写出的文件"的**严格**字节指纹（与真写盘后的文件逐字节一致，唯一例外是块说明行里的"生成于 <时间>"） |
| `preview_sha256_norm` | string or null | 同上，但把"生成于 <时间>"抹成 `<ts>` 之后再算 —— **跨分钟也稳定**，"预览 == 真写盘"的确定性对拍用它 |
| `preview_bytes` | int or null | 将要写出的字节数 |
| `next_step` | string or null | 逐文件的下一步（只覆盖"验证失败归因"这一档） |
| `dry_run` | bool | **不在上面的显式键集里**：它由 `--dry-run` 通过"额外平铺"塞进**每条** `files[]` |
| `delta_dropped` | array（**恒在**） | 因为"**源码本来就是那个值**"而没有写进去的字段令牌（如 `["ax0.lines", "ax1.lines"]`）。`[]` 有两种含义——这一档没算基线，或算了但一个都没摘；**要区分请看 `delta`** |
| `delta` | object or null | `{"dropped": N}` = 算了基线、摘掉 N 个字段（与 `delta_dropped` 长度一致）；`{"skipped": "baseline_unavailable"}` = 想算但基线取不到（脚本跑不通 / 超时，退回老口径）；**`null` = 这一档压根不算基线** |

## 值域表

### `reason` 的三档 + 两种"没写"（0.1.18）

"没写成"在同一份载荷里其实是**三件不同的事**，机器分支只认 `reason` 配 `error_code`：

| `reason` | `error_code` | 含义 | 源码动了没 |
|---|---|---|---|
| `best_effort` | ——（无） | **能写、没全写**：能落地的都落了，其余在 `pending` / `inplace_pending` | 动了 |
| `refused` | `nothing_applied` | **拒写**：静态层面就证不出目标 | **一个字节都没动** |
| `fail` | `writeback_failed` | **写了又回滚**：重跑回比不达标 | 还原成原样 |

**`best_effort` 不等于被拒**：它只说"能写的都写了、剩下的进清单"。判"写没写"看 `applied` / `changes`
（块路径落地时 `changes` 可能是 `[]`，那种情况以 `applied: true` + 文件 sha256 变了为准）；
判"成没成"看 `ok` / `rc` / `error_code`。

**两种"没写"对用户是两件事**：`nothing_applied` = 根本没定位到目标（改脚本结构，或按 `--snippet` 手改）；
`writeback_failed` = 改过又退回（"以为改了、其实没改"，读 `err` / `warnings`，**别当成已写回**）。

### `reason` 四档与两条"没写"（0.1.19）

机器分支只认 `reason` 配 `error_code`：

| `reason` | `error_code` | 含义 | 源码动了没 |
|---|---|---|---|
| `ok` | ——（无） | 该写的都写了、验证通过 | 动了 |
| `best_effort` | ——（无） | **能写、没全写**：能落地的都落了，其余在 `pending` | 动了（`applied: true`）；`applied: false` 时是"一个字节都没写" |
| `refused` | `nothing_applied` | **拒写**：静态层面就证不出目标 | **一个字节都没动** |
| `fail` | `writeback_failed` | **写了又回滚**：重跑回比不达标 | 还原成原样 |

**`best_effort` 不等于被拒**：它只说"能写的都写了、剩下的进清单"。判"写没写"看 `applied` / `changes`
（块路径落地时 `changes` 可能是 `[]`，那种情况以 `applied: true` + 文件 sha256 变了为准）；
判"成没成"看 `ok` / `rc` / `error_code`。

**两条"没写"是两件事**：`nothing_applied` = 根本没定位到目标（改脚本结构，或按 `--snippet` 手改）；
`writeback_failed` = 改过又退回（"以为改了、其实没改"，读 `err` / `warnings`，**别当成已写回**）。

### 三条通道的结论：**在"都能写得进去"的夹具上**一致（0.1.19 起）

**夹具 A**（`add_axes` 两条 + 各一条 `plot`；只改一根轴的**位置**与**标题字号**，**线属性未改**），
`--style auto` / `inplace` / `block` 给出**相同的** `{rc, reason, applied, pending}` —— 本机实测：

| `--style` | rc | `reason` | `applied` | `verified` | `pending` | `warnings` |
|---|---|---|---|---|---|---|
| `auto` | 0 | `ok` | true | true | `[]` | 0 |
| `inplace` | 0 | `ok` | true | true | `[]` | 0 |
| `block` | 0 | `ok` | true | true | `[]` | 0 |

**"一致"指结论一致**（不是载荷逐字节相同），而且**只在"每个要改的字段都写得进去"时成立**。下面三处是**通道差异**，与"一致"并不冲突，单独读：

| 键 | 谁会看到它 |
|---|---|
| `block_lines` | 只有**块通道**有值（同一夹具实测：`auto` / `inplace` = `0`，`block` = `4`） |
| `inplace_pending` | 只有**退块那次**才留档；`inplace` 单跑时通常为 `[]`（账在 `pending`） |
| `delta` / `delta_dropped` | 只有**会跑脚本的档位**才有（见下节） |

**边界（必须分清）**：一旦有「源码里确实没有对应写法」的字段被**真的改了**，三档就不再相同 —— 那是**通道能力差异**，不是口径不一致。
实测**夹具 B**（同上夹具，但把 `ax0` 的线宽改成 3.0：源码里 `ax0.plot()` 没有 `linewidth=`，原位改不了）：

| `--style` | rc | `reason` | `applied` | `pending` | 落地方式 |
|---|---|---|---|---|---|
| `auto` | 0 | `ok` | true | `[]` | 退块把线宽写进块（`block_lines=5`） |
| `inplace` | **1** | **`best_effort`** | **true** | **`["ax0.lines"]`** | 位置写了、**线宽如实留在 `pending`** |
| `block` | 0 | `ok` | true | `[]` | 块里写（`block_lines=6`） |

⇒ `inplace` 这档 `rc=1` 是**正确上报**（"写进去一部分、还有一项没写"），**不是**它和三档不一致。
判"到底写了没有"看 `applied`（这里是 `true`），不要用 `rc` / `reason` 反推 —— 这也是
[agent.md](agent.md) 那句「**不要只看 `ok`**：它只说整批算不算成功，不回答'改了没有'」的具体例子。

**降级档不是"不一致"，是"没有基线"**：`--verify-fast` / `--no-verify` / `--no-semantic` **不跑你的脚本**
⇒ 同一夹具在这三档下仍会 `pending: ["ax0.lines", "ax1.lines"]`（源码里没有对应写法，确实写不了）；
`--style inplace` 在这档会 `rc=1` + `reason=best_effort`（原位写不了那两项），`auto` 则退块落地
（`rc=0`、`block_lines=7`）。**这是设计，不是缺口径。**

> 已知**文案**问题（0.1.20 修）：`--verify-fast` 的人读输出会同时打印「正在验证（…）」与「本次没有重跑脚本」。
> 后半句是事实（fast 档确实不重跑），前半句是文案错。`--json` 侧口径不受影响：`layers.rerun = null`、`verified = null`。

### `delta` / `delta_dropped`：哪些字段"本来就没变"所以没写

- **什么时候算基线**：默认档（`apply --write`）与 `--diff` —— 这两档本来就要跑你的脚本；
  `--verify-fast` / `--no-verify` / `--no-semantic` **不算**（它们的承诺就是不跑脚本）⇒ 这档 `delta` 是 `null`、
  `delta_dropped` 是 `[]`。
- **`no_change` 轮也会带非零 `dropped`**（本机实测 2 项：`ax0.lines` / `ax1.lines`）：那时它只说明
  "这些字段源码本来就是那个值"，本次**什么都没改**（`applied: false`、`rc: 0`）⇒ **可忽略**。
- **无基线档下"等值"也会写块**：同一份参数原样回写，默认档是 `no_change` / `applied: false`（0 字节）；
  `--verify-fast` 下会 `applied: true` 并写一个块（本机实测 7 行）—— 那是"不跑脚本就证明不了现值"的代价。
- 判"省了什么"看 `delta_dropped`；判"这次写没写"看 `applied`。

### `stage`：六个值

唯一出处：`messages.py` 的 `STAGES` 元组（新增取值必须同时改那个元组与本表；
`tests/test_agent_api.py` §25⑩ 会断言载荷里出现过的 `stage` 落在这个集合内）。

| 值 | 含义（失败卡在哪一步） | 谁会给这个值 |
|---|---|---|
| `params` | 入口参数 / 参数文件本身的问题（还没动任何文件） | `file_not_found` / `no_params` / `mutually_exclusive_flags` / `bad_params` |
| `preflight` | 动手前的检查（布局引擎冲突、参数过期、找不到可落地目标、后端探测） | `layout_conflict` / `stale_params` / `no_axes` / `no_fig` |
| `backup` | 备份 / 留档（找备份、写备份、拿脚本锁） | `revert` 的 `no_backup` / `no_first_backup` |
| `writeback` | 真正动文件的那一步（AST 写回；`launch` 下 = 跑脚本抓图） | `writeback_failed` / `nothing_applied` |
| `verify` | 写回后的验证（重跑 / 语义 / 外部改动守卫） | `external_modified`、以及 `reason=fail` 且判定为重跑失败 |
| `finalize` | 结果收尾。**唯一会出现在成功载荷里的 `stage`**：写回已落盘、只有"刷新参数文件 mtime"这一步失败（`ok` 仍 `true`） | `apply.py` 的 `_finalize_stage` |

### `error_code`：全集

出处：`apply.py` §6 的常量区 —— `_FAIL_ERROR_CODE`（reason → 码）与 `_failure_code`（取第一个
失败文件）。早退出口另外直接给码；`internal_error` 来自 `messages.py` 的 `guard_json_main`。

| 码 | 什么时候出现 |
|---|---|
| `file_not_found` | 脚本不存在（`stage=params`） |
| `no_params` | 没有参数文件（`stage=params`） |
| `mutually_exclusive_flags` | `--no-verify` 与 `--verify-fast` 同时给（`stage=params`） |
| `bad_params` | 参数文件结构 / 取值硬错误（缺 `version`、顶层非对象、字段越界、JSON 本身坏）（`stage=params`） |
| `stale_params` | 脚本比参数文件新（套用旧参数会覆盖手改），要 `--force`（`stage=preflight`） |
| `layout_conflict` | 布局引擎冲突 + 这次真要写轴位置 ⇒ 默认拒绝；`--strict` 下是整批中止（`stage=preflight`） |
| `no_axes` | 参数里没有可落地的轴 / 字段（`stage=preflight`） |
| `no_fig` | 找不到 figure/subplots 的赋值目标（纯 pyplot 流）（`stage=preflight`） |
| `writeback_failed` | 写回 / 验证失败（含"脚本跑不通"的 `fail`）（`stage=writeback` 或 `verify`） |
| `external_modified` | 验证期间脚本被外部改过（已回滚、未写回）（`stage=verify`） |
| `nothing_applied` | 整批**一个字节都没写**而这次确实想写（`ok=false`、`rc=1`；逐文件 `reason` 可能仍是 `best_effort`）（`stage=writeback`） |
| `internal_error` | 工具自身抛了未预期异常（`attribution=tool_bug`、脚本零改动）；**不属于**逐文件 `reason` 值域 |
| `apply_failed` | 兜底：出现了 `ok` 清单之外的 `reason` —— 这是"新增 reason 忘了登记"的信号 |

### `attribution` 与 `attribution_detail`

`attribution` 四值（出处：`verify.py` 的 `VERIFY_FAIL_*` 常量）：

| 值 | 意思 | 你该做什么 |
|---|---|---|
| `script_cannot_run` | 脚本**自己**就跑不起来（缺数据 / 依赖 / 语法等） | 先修脚本；`apply` 的验证证明不了写回 |
| `script_needs_args` | 脚本要命令行参数才能跑 | 按 `next_step` 补 `-- <你的参数>` |
| `script_needs_gui_or_headless_impossible` | 脚本要交互后端 / 无头跑不了 | 换机器或改成 `Agg` 可跑 |
| `tool_bug` | 是工具的 bug（写回写坏了 / 内部异常） | 报 issue，带上 `err` 与 `files[]` |

`attribution_detail` 是细分（值域见 `verify.py` 里 `classify_verify_failure` 的 docstring）：
`unknown` / `argparse` / `missing_dependency` / `missing_data` / `display` / `static_args` /
`static_gui` / `configured_args`，外加两条新值：`generated_code_failed`（写回生成的
代码跑不通）与 `mtime_sync_failed`（收尾刷 mtime 失败）。`classify_verify_failure` **不会**从
stderr 反推出 `tool_bug`（不许猜"工具坏了"）。

### `layout_why`：五个值

唯一出处：`writeback.py` 的 `position_write_needed`（返回 `(True|False|None, why)`），判据是
"这组参数到底会不会改到轴位置"。

| 值 | 意思 | 冲突下会不会被拒 |
|---|---|---|
| `no_pos` | 参数里根本没有位置类字段（`GEOMETRY_FIELDS` 只有 `pos`） | 不会（只改字号 / 线宽 / cmap 的写回不受引擎影响） |
| `proved_equal` | 静态证出源码现值**已等于**目标位置，写了也是空改 | 不会 |
| `live_equal` | 重跑实测：参数里的位置与现状一致，不用写 | 不会 |
| `live_differs` | 重跑实测确实不同 —— **这次真要写位置** | 会（默认拒绝，给两条出路） |
| `unjudged` | 判不了（没开验证 / 脚本重跑不了 / 解析失败）⇒ 保守按"可能要写"处理 | 会（fail-closed，并在人读里说清"为什么没放行"） |

### `diff[].type`：三个值

唯一出处：`writeback.py` 的 `preview_diff`（返回 `[{line, type, old, new}]`）。`line` 是
**新文件**里的行号（1 基）；`old` 是原行（插入时为 `null`），`new` 是改后行（删除时为 `null`）。

| 值 | 意思 |
|---|---|
| `inplace` | 一对一换行：原位改数字 / 补 `fontsize=` 参数 |
| `insert_block` | 新增行落在自动生成的调整块里（同一次写回可能多行） |
| `replace` | 其余：已有块被整体替换、行数变了、删除行 |

## `mpltweak --check-update --json`

第二条冻结载荷（0.1.17）。**五个键固定**，snake_case 稳定英文，**不随 `--lang` 变**（人读文案会变，键不会）。

```json
{"current": "0.1.16", "latest": "0.1.17", "update_available": true, "error": null, "error_detail": null}
```

| 键 | 类型 | 值域 / 口径 |
|---|---|---|
| `current` | string | 本地版本号（恒在） |
| `latest` | string or null | PyPI 上的版本号；**查不到时 `null`** |
| `update_available` | bool or null | **三态**：`true` = PyPI 比本地新；`false` = 同版本或本地更新；**`null` = 查不到，或比不出**（版本号带 `rc` / `dev` / `post` 后缀时宁可说"比不出"，不替用户猜 —— 把"不知道"说成 `false` 就是"已是最新"的假话） |
| `error` | string or null | `null` = 查到了；否则是**四个稳定原因码**之一：`timeout` / `network` / `http` / `payload` |
| `error_detail` | string or null | 一行英文说明（`timed out after 5.0s` / `HTTP 404` / `not JSON: …`）；查到时 `null` |

**退出码与"查没查到"是两件事**（这里最容易读错）：**查到、查不到都是 `rc=0`**；`rc=2` 只给
**用法错误**（多给位置参数 / 未知开关 / `--lang` 值不认识或缺值 —— 走 stderr，**不产 JSON**）。
要知道这次到底查没查到，**看 `error`（`null` = 查到了），别拿 `rc` 判**。这么定的理由：它是
"顺带问一句"的辅助动作，不该让一次 DNS 抖动把 `mpltweak --check-update || echo 网络不行`
或者 CI 的健康检查弄红。

三条附带口径：① `--json` 下 **stdout 只有这五个键的 JSON**（人读的版本号那一行不印）；
② **只查不升**：不跑 `pip install`、不改脚本、不写任何文件（连缓存都不留）；
③ **只认顶层开关位**：`mpltweak fig.py --check-update` 里那个是**你脚本自己的参数**（照旧透传），
不会去查 PyPI —— 与 `--version` 只认第一个位置参数是同一条纪律（脚本的参数我们不许抢）。

## 政策：只加不改

1. **加字段随时可以**：新增键必须在全部 `files[]` 条目里**恒在**（缺值归一成
   `null` / `0` / `[]` / `false`），并且写进本页 + 被"契约测试"覆盖到。
   `--check-update` 那份载荷同理：**五个键恒在**，以后要加第六个键也照这条走。
2. **改语义 / 删键 / 改值域 = 大版本**：既有键的含义、"何时出现"、值域一律不动；
   确实要改就等大版本，并在 `CHANGELOG` 里单列。
3. **新增枚举值也算改值域**：`stage` 加值要同时改 `messages.STAGES` 与本页；
   `error_code` 加码要同时改 `apply.py` §6 的常量区与本页；`layout_why` / `diff.type` 各只有
   一处判据函数，加值得改那个函数。
4. **退出码与载荷分开看**：`0` = 成功；`1` = 结果需要处理（有文件没落地 / 验证失败 /
   参数过期 / `nothing_applied`）；`2` = 输入或用法错误（含参数文件坏、`--strict` 整批中止），
   工具自身异常也落这一档。**"有没有写进文件"只看 `files[].applied`**。
5. **中文文案不参与机器分支**：`error` / `err` / `warnings` 里的文字随时可能改口径与语种，
   分支一律用 `error_code` / `reason` / `stage` / `attribution` / `layout_why` 这些稳定枚举。

## 怎么验证契约没被破坏

一条现有测试就够：`tests/test_cli_output.py::test_json_payload_stays_on_stdout`。它逐字断言
"成功载荷的键集"（注释原话：**少一个就是破坏契约**）与"成功载荷不许带 `error_code`"；
配套的两条是 `test_json_failure_keeps_payload_and_stderr_clean`（失败载荷仍是合法 JSON、
`error_code` / `reason` 都在、人读行只在 stderr）与 `test_json_script_missing_emits_only_json`
（早退出口只给 JSON）。

值域那一半由 `tests/test_agent_api.py` 盯：§23 断言"每一条失败出口都带 `error_code`"（含
`file_not_found` / `no_params` / `mutually_exclusive_flags` / `bad_params` 与"成功载荷不加"），
§25 断言 `stage` 只落在 `messages.STAGES` 里、`files[]` 条目不新增 `stage`。

`--check-update` 那一份由 `tests/test_check_update.py` 盯：它的 `[4] --json 载荷` 一段断言
**五个键固定**、`update_available` **三态**（含"查不到 ⇒ `null` 不是 `false`"）、四个原因码
**各有人读文案**；同套件的 `[5] 默认不联网` 另用两条**对照型**棘轮守着"只有它联网"（静态扫全树：
`update.py` 以外不许 import 网络模块、不许出现 `urlopen(`；运行期换掉唯一出口后证明
`--version` / `--help` / `-V` / 空参数**零调用**、而 `--check-update` **恰好一次**）。

```bash
python -m pytest tests/test_cli_output.py -q -k "json"
python tests/test_agent_api.py          # §23 / §25 在其中的 ALL PASS 报告里
python tests/test_check_update.py       # [4] / [5] 在其中；零联网
```

改完 `apply.py` / `messages.py` / `writeback.py` / `verify.py` 里任何与本页相关的常量或落键处，
**上面两条必须仍绿**；红了先看是"实现漂了"还是"契约该升级到大版本"。
