# 给 AI / agent 的排版接口

> **`--json` 的键名、类型、值域已冻结**（0.1.17 起）：逐条口径见 [json-contract.md](json-contract.md)。
> 政策是**只加不改** —— 按机器分支请认那一页里的稳定枚举，别读中文文案。

大模型能写出正确的绘图代码，但**排版数字基本靠猜** —— 它看不到那张图。

mpltweak 把这部分从"猜"变成"写文件"：

**参数文件是公开 JSON 格式（带 JSON Schema）**，AI 可以直接生成或修改它，不需要"看懂"图。`mpltweak apply --write` 负责确定性落实：AST 定位 + 三层验证 + 失败回滚，**写错了也不会把你的脚本改坏**。

**分工：让 AI 管内容，让 mpltweak 管排版。**

**入口定位：命令行是主路径，`mpltweak mcp` 是可选适配层。**

- **命令行是主路径**：只要调用方**能跑命令**（人或 agent —— Claude Code / Codex / Cursor / DSH 这类都行），
  用下面三条就够，不需要任何额外配置：

```bash
  mpltweak describe fig1.py -o layout.json   # 读：拿到当前排版
  mpltweak apply fig1.py --write --json      # 写：落回源码（--json 时过程信息全走 stderr）
  mpltweak check fig1.py --json              # 查：排版体检，退出码即 CI 门禁
  ```

- **`mpltweak mcp` 只服务"拿不到 shell"的客户端**（Claude Desktop 这类图形客户端）：它把 CLI 的
  **核心子集**包成 stdio 工具，按 **best-effort** 维护 —— 定位、功能与验收口径都以 CLI 为准。
  **能跑命令就不必配 MCP**；确需接客户端时看本节的「也可以接进 AI 客户端（MCP）」。

```jsonc
// agent 产出这个 → mpltweak apply fig1.py --write → 落进你的源码
// 两个字段不是可选的：version 必须写 4；每条轴必须带 "index"（= fig.axes 的顺序）。
// 缺 version ⇒ bad_params（报文会说要 version=4）；缺 index ⇒ bad_params。
{ "version": 4, "axes": [
  { "index": 0, "pos": [0.080, 0.560, 0.395, 0.330],
    "title_fontsize": 9.5, "legend": { "loc": "upper right" } },
  { "index": 1, "pos": [0.525, 0.560, 0.395, 0.330],
    "title_fontsize": 9.5, "legend": { "loc": "upper right" } }
] }
```

**读 → 改 → 写 → 查回路：**

| 命令 | 作用 |
|---|---|
| `mpltweak describe <脚本>` | 不开窗，把**当前排版**导出成参数 JSON（agent 的"读"）；`-o/--output 文件` 直接写进文件（最常用）；`--all-figs` 时是嵌套的 `{"figures": [...]}`，**顶层没有 `axes`**（直接喂回 `apply --params` 是 `rc=1 / reason=no_axes`，见「参数文件」）——但**0.1.14 起 `--all-figs` 会顺手按每图写一份 `<stem>.fig<k>.json`**（apply 认的命名），所以"导出全部再逐图落实"不必再逐图 `--fig N` 导一遍 |
| `mpltweak schema` | 输出参数文件的 JSON Schema，供 agent 校验自己生成的内容 |
| `mpltweak apply <脚本> --write --json` | 落实回源码，并输出机器可读结果（`--json` 时过程信息全部走 stderr）。**脚本自己带参数**（依赖 `sys.argv`，如 `python fig1.py --data x.npz`）时在 `--` 后透传：`apply <脚本> --write -- --data x.npz`（bug #6，否则重跑验证与基线不一致而误回滚） |
| `mpltweak revert <脚本> [--write]` | 回退到上次写回之前（默认只预览；回退前也会留存当前版本） |
| `mpltweak apply <脚本> --diff --json` | **写回前先看差异**（0.1.16，别名 `--preview-diff`，同一个开关）：引擎**照真写回的路跑**，只是把目标换成同目录的**影子副本**（跑完即删），所以内容与真写盘**同源**（含 `--style auto` 退块那次"重读源码"的语义）。`files[].diff` 逐行给出 `{line, type, old, new}`（`type` = `inplace` 原位补参数 / `insert_block` 插入调整块 / `replace` 替换）。这一档 `reason='preview'` / `applied=false` / `rc=0`（与只读预览同档，**不是**"已写回"），**脚本一个字节都不动、不造备份**。**它不重跑脚本**，所以边界是：真写盘时**验证失败**触发的回滚/换锚点重试不会体现在预览里 —— 那一档以真写回为准 |
| `mpltweak check <脚本> --json` | 不看图也能检查：对齐 / 等大 / 间距 / 字号 / 越界 / 重叠，**文字按真实渲染尺寸**查（超出画布 = 问题，互相压住 = 提示），另有轴范围不统一提示；布局引擎冲突**默认就是 error 级问题**（`rule='layout_engine_conflict'`，0.1.14 起；`--strict` 只是兼容开关） |
| `mpltweak --check-update [--json] [--lang zh\|en]` | 显式联网查 PyPI 最新版（0.1.17）。**默认任何命令都不联网，只有它联网**；只查不升、不写任何文件、默认 5 秒超时（`MPLTWEAK_UPDATE_TIMEOUT` 可调）。**查不到（断网 / 超时 / 被墙 / 载荷坏）也是 `rc=0`** —— 原因看 `--json` 的 `error`（`timeout` / `network` / `http` / `payload`），**别把它当失败去重试**；用法错误才是 `rc=2`（stderr，不产 JSON）。机器可读载荷的五个键见 [json-contract.md](json-contract.md) |

**三个选项各自只属于一个子命令**（`--help` 里都列着，这里再点名一次：它们**不是**全局开关，给错了子命令是 `rc=2`）

| 选项 | 谁认 | 作用 | 给别的子命令会怎样 |
|---|---|---|---|
| **`apply --force`** | **只有 `apply`** | 参数比脚本旧（存完参数之后你手改过脚本）时也照样套用；默认**跳过**并给 `reason: stale` | `describe` / `check` / `revert` ⇒ `rc=2`（`unrecognized arguments`） |
| **`--lang auto\|zh\|en`** | **只有 `apply` / `describe` / `check`** | 界面语言：`auto` = 跟随 `MPLTWEAK_LANG` → locale（默认） | `revert` / `schema` / `doctor` ⇒ `rc=2`；**顶层 `mpltweak <脚本>` 与 `launch` 则是静默忽略**（它们用 `parse_known_args`，多出来的参数**原样进你脚本的 `sys.argv`**）⇒ 开窗那条路要切语言请设 `MPLTWEAK_LANG=en`，别在命令行上加 `--lang` |
| **`check --no-tight`** | **只有 `check`** | 忽略脚本里的 `bbox_inches='tight'`，文字越界仍按 `error` 判（0.1.8 的旧口径） | `apply` / `describe` / `revert` ⇒ `rc=2` |

> 实测口径（可重跑）：`mpltweak fig1.py --dry-run --lang en` **打印仍是中文**、`rc=0`；同一个脚本改用 `MPLTWEAK_LANG=en` 再跑则是英文。`mpltweak revert fig1.py --lang en` / `--force`、`mpltweak apply fig1.py --no-tight` 都是 `rc=2`（`unrecognized arguments`），三段都能用 `python -m mpltweak …` 原样复现。

```bash
mpltweak describe fig1.py --output layout.json    # 读：拿到现在的排版（-o / --output 同一个选项）
# ...agent 改 layout.json 里的几个数字...
mpltweak apply fig1.py --write --json       # 写：落回源码并自检
mpltweak check fig1.py --json               # 查：排版体检，输出 JSON
```

**`describe -o <文件>` 就是"只要文件"：它不往 stdout 打 JSON（0.1.15 定口径）**。给了 `-o` 之后
**stdout 一个字节都没有**，屏幕上只有一句 `[describe] 已写入 <文件>`（这句走 stderr）。
⇒ **别在 `describe -o` 之后再去找 stdout 里的 JSON —— 它不会有**；要有内容就别给 `-o`（用 stdout），
或者直接读那份文件。**为什么这么定**：避免两种输出通道混在一起 —— 一旦 stdout 既可能被重定向成
JSON、又可能被重定向成提示语，调用方就得靠"猜这次有没有 `-o`"来解析，容易把提示语当 JSON。

**为什么需要"读"**：`add_axes([...])` 这种明写的位置，读源码就够了；但 `plt.subplots` 的网格、
没写 `fontsize=` 时实际生效的默认值、`tight_layout` 之后的位置、colorbar 与 cartopy 锁定后的
真实尺寸 —— 这些**源码里没有数字**，得跑一遍才知道。

**agent 路径建议不要加 `--verify-fast`**：它只检查语法、**不重跑脚本**，因此证明不了"某个字段
与源码现值相等"。当源码里没有可比写法时（典型：`plt.subplots` 的网格轴位置由 subplotspec + 边距
算出），工具只能**保守退块**。实测：拿 12 个真实语料脚本走 `describe` 原样导出 → `apply --write
--verify-fast`，能跑起来的 **7/7 都插了调整块**（`audit_018/engine-hunter/fix018/t15_agent_path.txt` —— **本地记录，未随仓库发布**，可重跑入口 `python tests/test_agent_api.py`）；
同一条路径**去掉 `--verify-fast`** 就是 `reason=no_change`、一个字节不动（本地 A/B：同一份
`plt.subplots(2, 2)` 脚本，加它 → `style=block`（**这一档没有差异过滤**：0.1.18 起只在会跑脚本的档位（默认 `--write` / `--diff`）做基线比较，`--verify-fast` 仍可能把"恰好等于默认值"的字段一起写进块），不加 → `reason=no_change / applied=false`，
`audit_018/promo/t16_probe2.log` —— **本地记录，未随仓库发布**，可重跑入口 `python tests/test_agent_api.py`）。`--verify-fast` 的定位是"慢脚本的快速语法档"（见「慢脚本」一节），
不是 agent 的默认档。

**`--json` 给机器读**：`files[]` 里的 `style` / `applied` / `changes` / `needs_block` / `pending` 判读这次落没落地；`schema` 子命令输出参数文件的 JSON Schema，供 agent 校验自己生成的内容。下面几个键的口径容易读错，逐个写清（0.1.14 起 `style` 也在此列）：

* `files[].style`（**恒存在，永不为 `null`**）：这次**请求的通道**（`auto` / `inplace` / `block`）。走过写回时它被**实际采用的通道**覆盖 —— `auto` 退块就是 `block`（同时 `auto_fallback=true`、`inplace_reason` 说为什么没原位落地）；只读预览（`reason=preview`）与早退条目（`bad_params` / `stale` / `layout_conflict` 等）填的是**请求值**，`auto` = 逐图待定。**别把 `style` 单独当"落没落地"**：那件事只看 `reason` 与 `applied`（早退条目也带请求的 `style`，但它一个字节都没写）。
* `files[].verify_passes`（**恒存在**，int）：**1** = 默认单遍；**2** = 单遍取不到目标图状态、已回退两遍；**0** = 这次**没有**为验证重跑脚本（`--no-verify` / `--verify-fast` / 预览 / 没有可写字段）—— **`0` 不是失败**，它只表示"这一档没为验证重跑脚本"。它**只数主验证的遍数**：参数里有"原位写不了"的字段时先额外跑的那一遍**不计入**（所以 `verify_passes=1` ≠ 整条命令只跑了 1 遍脚本）。要判断"这次为什么回退"，看 `verify_passes=2` 再读同一条目的 `warnings`。
* `files[].semantic_basis`（**恒存在**，str）：`live+savefig` = 图对象状态 + 磁盘那张图的追加判据；`live` = 只有图对象状态；**空串** = 这次没有语义判定依据（没跑 / 回退后如实记"没验到"）。
* `files[].layout_conflicts`（**恒存在**，数组）：脚本里**开着的布局引擎冲突点**（`constrained_layout` / `figure.autolayout`），每项一句人话并带脚本行号。**空数组 = 这份脚本没开引擎**，位置的写回不会被覆盖。非空要看同一条目的 `reason` 分流：`reason='layout_conflict'` ⇒ 这次要写轴位置、被默认拦下（`applied=false`、**这一份**没写盘、`rc=1`）；`reason` 是 `ok` / `best_effort` / `refused` / `fail` ⇒ 冲突**没挡这次**（只动了非几何字段、或你给了 `--force`、或走的是拒写 / 回滚那条路），冲突仍在。**它只说明"脚本开着引擎"，不说明"这次写不写位置"**。
* `files[].diff`（**恒存在**，数组；只有 `--diff` 时非空）：写回前预览的**行级差异**，每项 `{line, type, old, new}`。`line` 是**新文件**里的行号（1 基）；`old` 是原行（插入时 `null`）、`new` 是改后行（删除时 `null`）。`type` 三档：`inplace` = 一对一换行（原位补参数 / 改数字）、`insert_block` = 新增行落在自动生成的调整块里、`replace` = 其余（已有块被整体替换 / 行数变了）。**用它就能在落盘前复现"改完是什么样"**：把每项按 `line` 重放到原文即可（`old` 必须与原文同位置的整行一致，对不上就是口径错）。同组的三个键：`preview_sha256`（**影子副本最终内容**的严格字节指纹 —— 它就是真写盘后文件的 sha256，唯一例外是调整块说明行里的"生成于 <时间>"：预览与真写回是两次运行，跨了分钟就只差这几个字符）、`preview_sha256_norm`（把"生成于 <时间>"抹成 `<ts>` 之后再算，**跨分钟也稳定**，"预览 == 真写盘"的确定性对拍用它）、`preview_bytes`（将要写出的字节数）。**边界说清**：`--diff` **不重跑你的脚本**，所以"验证失败 → 自动回滚 / 换锚点重试"这类**由验证结果驱动**的分支不在预览里；那种情况下以真写回为准。
* `files[].layout_why`（**判定依据**，稳定英文枚举，不进文案）：这条参数到底会不会改到轴位置 —— `no_pos` = 参数里没有位置类字段，不可能写位置；`proved_equal` = 静态证得出源码现值已等于目标位置，写了也是空改；`live_equal` = 重跑实测参数里的位置与现状一致，不用写；`live_differs` = 重跑实测确实不同，**这次真要写位置**（冲突下会被拒）；`unjudged` = 判不了（没开验证、脚本重跑不了、解析失败）⇒ 保守按"可能要写"处理。判据只有一处：`writeback.position_write_needed`（返回 `(True|False|None, why)`）。
  **注意**：`layout_why` 自 0.1.14 起就**在 `--json` 的每条 `files[]` 里**（值域与唯一出处见 [json-contract.md](json-contract.md)），不用再从"这次关没关验证"反推。人读输出只对 `unjudged` 那一档多打一句「补充：本次没有跑（或跑不出）脚本重跑验证 → 无法确认参数里的位置是否已经与现状一致，按"可能要写"处理（保守拒绝）」。想让判定更准，就别关验证。
* `files[].refused_hint`（**恒存在**，string 或 `null`）：拒写时给你的**"照着改"指导**（0.1.17 起）。**0.1.18 起只对"引擎能点名"的拒写生成**（建轴点与参数条数对不上、载体名不是恰好一次模块层调用…），其余拒写是 `null` —— 注意 `fig.axes[i]` / `plt.gca()` / 函数里建图**已不再是必然拒写**（判据表见 [verify.md](verify.md)） —— 它在人读输出里就是那段"把 `fig.savefig(...)` 挪出函数"之类的建议，机器侧**不要**拿它做分支判据（分支用 `reason` / `pending`）。
* 顶层 `error_code`（**只在失败载荷上出现**；成功载荷没有这个键）：`file_not_found` / `no_params` / `mutually_exclusive_flags` / `layout_conflict` / **`nothing_applied`** / `bad_params` / `stale_params` / `no_axes` / `no_fig` / `writeback_failed` / `external_modified` / `internal_error`，兜底 `apply_failed`（出现 ok 清单之外的 `reason` = "新增 reason 忘登记"的信号）。**`nothing_applied`（0.1.14）** = 整批**一个字节都没写**而这次确实想写（典型：循环里建轴 / 建轴点与参数条数对不上 / 载体名不是恰好一次模块层调用 / 类里 `self.ax` —— 引擎证不出目标，按 fail-closed 保守拒写；**0.1.18 起这一档的 `reason` 是 `refused`，不再借 `best_effort`**）：这一档 `ok=false`、`rc=1`、`files[].applied=false`。**另一种"没写成"是 `writeback_failed`（`reason=fail`）：写进去了、重跑回比不达标、又回滚** —— 那是"以为改了、其实没改"，与"根本没改"必须分开看。反面对照：`reason=no_change`（参数与源码本来就一致）仍是 `ok=true` / `rc=0` / 无 `error_code`，那不是失败。所以判"写没写"只看 `applied` / `changes` / `reason`，判"成没成"才看 `ok` / `rc` / `error_code`。其中 `internal_error` 是**工具自身**抛了未预期的异常（`attribution: tool_bug`，与你的脚本无关，源码已回滚零改动）—— 它不属于 `apply` 的逐文件 `reason` 值域，来自顶层异常兜底（`apply` / `check` / `describe` 三处都有，附带 `error` + 英文 `next_step`），所以 `--json` 下同样是 `ok: false`；顶层码取**第一个失败文件**，逐文件的细分原因照旧在 `files[].reason` / `files[].attribution` / `files[].err` 里 —— 有码就按码分支，别去读文案。

（这三个键的口径全文在 `docs/verify.md` 的「机器可读通道」一节。）

**失败时还告诉你「哪一步没成」和「下一步做什么」（0.1.15 起，`apply` / `revert` / `launch` 同口径）**：
失败载荷顶层多两个键。`ok` / `error_code` / `files[]` / 退出码的口径**一个字都没改**，这两个键
只在**失败/降级**时出现（成功载荷**没有**它们，`files[]` 条目里也**没有** `stage` —— 逐文件细分原因
照旧看 `files[].reason`）。

**唯一例外：收尾降级（0.1.16 起）。** 写回**已经落盘**、只是收尾那一步没干净完成时，`ok` 仍是
`true`、`rc=0`，载荷顶层会带 `stage='finalize'` + `next_step`（典型：写回后刷新参数文件 mtime 失败）。
这一档**不是**写回失败，也**不是**"工具的 bug" —— 判"写没写"照旧只看 `applied` / `changes` / `reason`，
逐文件原因看同一条目的 `files[].warnings` 与 `files[].attribution`。

* `stage`：失败卡在**哪一步**（稳定英文枚举，值域只有下面 6 个，唯一出处在 `messages.STAGES`，
  机器据此决定"原命令重试 / 改输入 / 改脚本 / 报 bug"）。**其中 `finalize` 会出现在成功载荷上**
  （见下表的说明）。
* `next_step`：一句**可直接复制**的下一步（命令或动作），路径一律带双引号。**它是建议不是保证** ——
  按"这条失败最常见的成因"给，工具不保证照抄就一定跑得通（例：脚本本身有语法错时，`--force` 也救不回来）。
  0.1.10 起 `files[].next_step` 只覆盖"验证失败归因"那一档；顶层这个把它抬到顶层，并且在没有逐文件
  归因时（找不到脚本、无备份、无后端…）也给出。

| `stage` | 含义（失败卡在哪一步） | 会给这个值的命令 |
|---|---|---|
| `params` | 入口参数 / 参数文件本身（脚本路径不存在、`--fig` 越界、缺参数文件、互斥开关） | `apply` `revert` `launch` |
| `preflight` | 动手前的检查（脚本比参数新、布局引擎冲突、没有可落地的轴、无交互后端） | `apply` `launch` |
| `backup` | 备份 / 留档（拿脚本锁、找备份、写备份） | `revert` |
| `writeback` | 真正动文件那一步（AST 写回；`launch` 下 = 执行你的脚本并抓图） | `apply` `launch` |
| `verify` | 写回后的验证（重跑、语义比对、外部改动守卫） | `apply` |
| `finalize` | 结果收尾（0.1.16 起**真会用到**，且这一档**会出现在成功载荷**：`apply` 写回**已落盘**、之后刷新参数文件 mtime 失败 ⇒ `ok` 仍 `true` / `rc=0`，只有收尾降级；`launch` 侧同一档 = 脚本跑通、图也有了，收尾时后端已不可交互） | `apply` `launch` |

**`files[].warnings` 里 0.1.16 新增的两类留痕（都不影响 `ok` / `rc`）**：`probe_state_stale` = 旧的验证
状态文件删不掉（这次仍按重跑回读的结果判，但"待验 / 已验"可能沿用了上一次的结论）；`mtime_sync_fail` =
写回成功后刷新参数文件 mtime 失败（下次 `apply` 可能把这份参数判成 `stale`，必要时重新 `describe`）。
收尾降级那一档的逐文件归因是 `attribution='mtime_sync_failed'`（归因取值表见
[exec-boundaries.md](exec-boundaries.md)「跑不起来时，是谁的错、下一步做什么」）。

**`files[].attribution_detail`：`tool_bug` 有两种来源（0.1.16 起）。** `attribution` 的**值域一个都没变**
（仍是脚本侧三类 + `tool_bug` + `mtime_sync_failed`），细分加在 `attribution_detail` 上：`generated_code_failed`
= **写回之后的代码跑不起来，而你的原脚本本身是能跑的** ⇒ 有问题的是**工具生成的那几行**，不是你的脚本
（`attribution` 仍归 `tool_bug`，因为该修的是工具）。它是**升级、不是新分类**：底层分类器仍然只产出三类
（`script_cannot_run` / `script_needs_args` / `script_needs_gui_or_headless_impossible`），"判成 `tool_bug`"
这件事**只发生在 `apply` 这一层**，且只在"重跑失败的证据表明卡在写回那一步"（`verify_fail_kind` =
`after_write_broken` / `anchors_all_failed`）时才做 —— **绝不从 stderr 文案反推**（0.1.13 的教训：按文案反推
在英文下会静默失效）。**为什么值得单列**：这一档以前落到「脚本跑不起来（无法确定原因）」，用户读到的
是"你的脚本有问题"，于是根本没来报。

**`launch` 也有 `--json` 了（只覆盖失败路径）**：成功路径照旧开窗、不产出 JSON（开窗是长时间交互，
机器格式在那条路上没有意义）。失败时 stdout 只有**一条** JSON：`ok=false` + `error_code` + `stage` +
`error`（恒英文纯 ASCII）+ `next_step`；`error_code` 值域是 `no_script` / `file_not_found` / `no_backend` /
`script_failed` / `no_figures` / `backend_not_interactive` / `fig_out_of_range`。**退出码口径不变**
（脚本/用法问题 `2`、无交互后端 `4`）。副作用：这个开关会吃掉原本透传给你脚本的 `--json`，
要透传请写 `mpltweak launch x.py -- --json`（`--` 之后一律原样进脚本的 `sys.argv`）。
`--doctor` 不在这个通道里：它是**人读诊断报告**（`rc=0`，从不以失败退出），本来就不需要机器码。

**解析契约：错误只出现在 stderr（0.1.12 起）**。不带 `--json` 时，`apply` 的人读输出分两条流 ——
**stdout 只有数据/报告**（成功摘要、逐处清单、「会改：…」预览、可粘贴骨架），**人读错误全在
stderr**（失败与回滚、拒写与「未写回」、参数文件坏、外部改动被跳过、验证失败 + 归因、用法/输入
错误）。所以：

* `mpltweak apply foo.py > out.txt` 时，`out.txt` **不会**混进错误行；`2>` 里才是"为什么没成"。
* 管道消费者可以只看一件事：**stderr 空 = 这次没有任何错误**（成功/预览都保证 stderr 一字不发）。
  判"改没改"仍以**退出码**为准（`0/1/2/4`，见 [exec-boundaries.md 的退出码表](exec-boundaries.md#退出码实测)），
  别用"stderr 有字"当失败判据的替代品 —— `--verify-fast` 那类**降级提醒**仍留在 stdout。
* `--json` 不变：载荷仍只走 stdout（`json.loads(stdout)` 必须成功），错因在载荷的 `error_code` /
  `files[].reason` / `attribution` / `next_step` 里；人读行照旧全部让到 stderr。唯一例外是
  `--json` + 脚本不存在：那条出口**只给 JSON**，不打人读行（此时 stderr 为空，别据此认为"成功了"）。

逐类归属表 + 为什么这么划：见 [exec-boundaries.md 的「输出归属」](exec-boundaries.md#输出归属stdout-与-stderr0112)。
自证入口：`python -m pytest -q tests/test_cli_output.py`（本仓库的同类断言也在
`python tests/test_agent_api.py` 的 §24）。

**mpltweak 本身不含任何 AI 模型**：它只提供命令和判断，`ok` / `reason` / `changes` / `pending` 就是给 AI 读的结构化结果 —— 判断"到底改对了没有"靠三层验证 + 出错回滚 + `revert`，**不靠 AI 自己说"我改好了"**。

> **读排版前先看布局引擎冲突**：`describe` 的产物带 `layout_engine` 字段（脚本里 `constrained_layout` / `figure.autolayout` 的冲突点，带行号），MCP 的 `describe_layout` / `check_layout` 同样带。**非空就先 `mpltweak check <脚本> --static` 看一眼再动位置** —— 这些引擎会在每次绘制时重算轴位置，你排好的布局会被它覆盖。这是 0.1.9 实机调图会话踩出来的：人类开窗有终端提示，agent 通道却没有，白调一轮。
>
> 0.1.14 起这条约束变成了硬拦：**`check` 把冲突当 error 级问题**（`rc=1`、`problems[].rule='layout_engine_conflict'`）；**`apply --write` 在"冲突 + 这次真要写轴位置"时默认拒绝**（不写盘、`files[].reason='layout_conflict'`、顶层 `error_code='layout_conflict'`、`rc=1`，**脚本字节不变**）。只写字号 / 线宽 / cmap / 标题这类非几何字段照旧放行。agent 的正解是**先关引擎再写位置**；确实要写（例如脚本不能改）就加 `--force`（老开关 `--allow-layout-conflict` 同效，保留兼容），输出里会有 `[i] 已按 --force 跳过布局引擎冲突检查`；这一档**不会**静默放行 —— 那行声明一定会打出来。
> **逐份拒绝、`--strict` 与退出码**：上面这条拦截是**按参数文件逐份**判的 —— 一次 `apply` 会把主文件与 `<stem>.fig<k>.json` 一起收上来**逐份落实**，每份单独判，所以同一批里完全可能「图 1 写了、图 2 被拒」，**不是整批中止**。识别只看 `files[]`：`applied=true` 的那几条落地了，`reason='layout_conflict'` 的那条没落地；顶层 `error_code` 取**第一个失败文件**，默认档 `rc=1`。人读摘要讲的只是**这一份**没写盘（不是说整批没动）。**判断整体结果一律以 `files[].applied` 为准，别用人读摘要。**
> `apply --strict` 是另一档：冲突 + 这次要写位置 ⇒ **整批中止、`rc=2`**（`error_code='layout_conflict'`、`files=[]`、脚本字节一个都不变），不给逐份机会。**同一种情况两种退出码，按你要的语义选**：想**整批中止**（要么全写、要么一个都不写）就用 `--strict`（`rc=2`）；想**逐份评估**（能写的先写、不能写的逐条报）就用默认档（`rc=1`）。`check --strict` 反过来是**兼容开关**：`check` / `check --static` 与加不加 `--strict` **载荷逐字节相同、退出码相同**（冲突已经默认按 error 级报），加它只为旧脚本/CI 不改命令。


## 也可以接进 AI 客户端（MCP）——**可选适配层**

**先看这句**：只要你的客户端 / agent **能跑 shell**，直接用上面的命令行就够了，**不必配置 MCP**。
MCP 面向的是**拿不到 shell 的客户端**（Claude Desktop 这类，AI 只能在对话里调工具）。

它是**可选依赖**（要求 Python ≥ 3.10），装上之后能把 mpltweak 挂进 Claude Desktop / Cursor / Claude Code：

```bash
pip install "mpltweak[mcp]"
mpltweak mcp                    # 启动 stdio server
```

客户端配置加一段：

```json
{"mcpServers": {"mpltweak": {"command": "mpltweak", "args": ["mcp"]}}}
```

**它暴露的是 CLI 的「核心子集」**（下面这张表按 `mcp_server.py` 里的实际函数签名写）：

| MCP 工具 | 参数（实际签名） | 对应 CLI |
|---|---|---|
| `describe_layout` | `script`, `fig=-1`, `all_figs=False` | `describe [--fig N] [--all-figs]` |
| `check_layout` | `script`, `fig=-1`, `tol=0.01` | `check [--fig N] [--tol T]` |
| `params_schema` | （无参数） | `schema` |
| `apply_layout` | `script`, `write=False` | `apply --write`（读默认位置的 `.tweak_params/<脚本>.json`，**不接受** `--params` 路径） |

**不在这个子集里的**（只在 CLI 上有）：`revert`、`--style`、`--verify-fast`、`--keep-unverified`、
`--strict`、`--no-text`、`--static`、`apply --all-figs`、`--lang`。MCP 按 **best-effort** 维护；
四个工具里的用户脚本同样跑在**独立子进程**（写文件 / 联网等副作用照旧真实发生）。

## 让 AI 直接读这份手册（skill）

仓库里的 `skills/mpltweak/SKILL.md` 是一份**写给 AI 的操作手册**（Agent Skills 格式）：
命令、使用铁律、以及几种人机协作方式。想让自己的 AI 助手学会用 mpltweak，
把这份文件喂给它就行 —— 不用你每次解释一遍怎么用。放进客户端的 skills 目录（如 `~/.dsh/skills/`）也能用。

## 人 + agent 怎么分工

| 分工 | 怎么做 |
|---|---|
| **人拖 → agent 落盘** | 你开窗拖完，说一句"落盘"，AI 跑 `apply --write` |
| **agent 起草 → 人微调** | 让 AI 按规则先排一版 → 你开窗接着调（参数会续上）→ 确认落盘 |
| **agent 自检** | AI 画完图跑 `check`：不看图也能发现不齐 / 不等大 / 间距不均 / 字号不一 / 越界 / 重叠 |
| **一张图当模板** | `describe` 排好的那张 → 改 `script` 字段 → `apply` 到其它脚本，统一整篇论文的排版 |
| **人拖完，agent 总结** | AI 读参数 JSON，用大白话说明改了什么（可当 commit message 或图注） |

原则很简单：**"好不好看"归你，"精确落实"归它。**

另一条口径：**"改哪个数字"可以交给 agent，"改完对不对"由产品判定**（`check` 与三层验证给出结论，失败回滚）。
