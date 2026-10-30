---
name: mpltweak
description: 交互式 matplotlib 排版（脚本零侵入）：像 PPT 一样拖拽调整多子图的位置/字号/图例/样式，确认后把精确数值写回脚本源码，适合论文配图排版。AI 写完绘图脚本后，用户说"开交互调图 / 我要拖一下图 / 调图 / 微调布局 / 图的位置不对 / 帮我排版多子图 / 统一这几张图的排版"时用它。开窗拖拽（PPT 式手势：多选、对齐、均分、吸附、裁白边、线条模式）→ 参数静默存到 .tweak_params/*.json（此时脚本一个字没动）→ 用户确认后用 `mpltweak apply --write` 原位写回源码（AST 确定性 + 三层验证 + 失败自动回滚）。也支持不开窗的 agent 闭环：describe 读排版 / check 体检 / schema 校验 / mcp 接入 AI 客户端。用户脚本里不出现本工具任何痕迹。
---

# mpltweak —— 交互式调图闭环

**一句话**：脚本零侵入 → 用户拖图 → 参数落盘 → 用户点头 → 数值精确写回纯 matplotlib 代码。

**定位**：只管**位置 / 字号 / 图例 / 样式**，不发明内容。文字、数据、图元仍由用户代码决定。

本手册对应 **mpltweak ≥ 0.1.21**（`mpltweak --version` 低于它就先按「版本与更新」处理）。
需要手册没写的细节时**按需读**（不要整篇读）：键位与手势 https://github.com/shdbl/mpltweak/blob/main/docs/interaction.md ｜ 参数文件字段 `mpltweak schema` ｜ `--json` 字段 https://github.com/shdbl/mpltweak/blob/main/docs/json-contract.md ｜ 排障 https://github.com/shdbl/mpltweak/blob/main/docs/troubleshooting.md ｜ 版本变化 https://github.com/shdbl/mpltweak/blob/main/CHANGELOG.md

## 铁律（违反即失败）

1. **脚本零侵入**：绝不在用户脚本里写 `import mpltweak...`、`gaitu()`、`_POS`/`_FONTS` 写回区、`--tweak` 分支、`if '--tweak' in sys.argv`。工具**全部在脚本外部**（CLI + 参数文件）。
2. **只在用户明确要求调图时才开窗**；没说要调图就不要弹窗。
3. **关窗只存参数**：只允许写 `<脚本目录>/.tweak_params/<脚本名>.json`。**不碰代码**、不自动落实、不删参数。
4. **等明确确认才落实**：只有用户说"确定 / 应用 / 落实 / 就这样"之后才 `apply --write`；落实前先把变化清单汇报给用户过目。
5. **落实必须是纯 matplotlib**：`set_position` / `legend(loc=, fontsize=)` / `set_title(fontsize=)` / `tick_params(labelsize=)` / `figsize=`；不得引入本工具的任何符号。
6. `.tweak_params/` 是临时产物；落实完成后问用户是否清理。
7. **不静默升级用户的包**：`pip install -U mpltweak` 会改他的环境 ⇒ 先说明"会升到哪个版本、可能有什么变化"，**征得同意再执行**。
8. **不在没确认版本的情况下断言"这是已知问题 / 已经修了"**：先 `mpltweak --version`，再看最新版与 `CHANGELOG.md`（见下一节）。
9. **引用工具输出必须逐字**：不许把输出"整理成通顺句子"再标成"实时输出"；某一行含乱码（`◆` / `�` / `锟斤拷`）就整行只说"该行乱码"，不得翻译、补全、润色。判排版结论用 `check --json` 的 `clean` / `verdict`，别去解析人读中文。

## 开工第一步：确认环境（缺了就装，但先问用户）

**先自检**（输出里有一行「开窗后端」）：
```bash
mpltweak doctor
```
> 自检一律用 `mpltweak doctor`。**查版本用 `mpltweak --version`（`-V` 同义，0.1.15 起）**：打印一行 `mpltweak <版本号>`、退出码 0。它只认命令行**第一个**位置参数 —— `mpltweak x.py --version` 仍是老规矩（`--version` 原样透传给你脚本的 `sys.argv`），别指望那样查版本。
> **输出编码**（0.1.21 起）：**管道 / 重定向**下所有人读中文都是 **UTF-8**（`doctor` / `check` / `apply` / `describe` / `launch` …），`--json` 与 `doctor` 更是恒 UTF-8 ⇒ 重定向到文件直接 `Get-Content -Encoding UTF8` 读即可；**真终端**里跟随平台编码（cp936 控制台看中文正常）。若下游只吃平台编码：`MPLTWEAK_NO_UTF8=1` 回到旧行为。若**你直接跑 python 脚本**也乱，那与本工具无关（看「常见坑」最后一条）。

**⚠️ `pip install` 之后命令还是"不存在"？** Windows + conda 很常见：包装好了，但 `Scripts` 目录不在 PATH
（安装时会打一行 `WARNING: The script mpltweak.exe is installed in '…\Scripts' which is not on PATH`）。
**这不是"没装"，别重装一遍。** 三条等价路子，任选一条：
1. **全路径调用**：`<环境根>\Scripts\mpltweak.exe`（目录用 `python -c "import sysconfig;print(sysconfig.get_path('scripts'))"` 取）；
2. 把 `<环境>\Scripts` **加进 PATH**（改完要**重开终端**才生效）；
3. **最稳**：`python -m mpltweak doctor` —— 只要 `python` 是本环境的，任何 PATH 配置都对；本文所有 `mpltweak <子命令>` 都能这么换。

**命令不存在（`command not found` / `不是内部或外部命令`）⇒ 先问，再装**：
1. 先用一句人话告诉用户："这台机器上还没装 mpltweak，需要装一下才能用。"
2. **只做排版 / 体检 / 写回**（`describe` / `check` / `apply` / `mcp`，不需要图形界面）：
   ```bash
   pip install mpltweak
   ```
   **装完若命令仍报"不存在"，多半只是不在 PATH —— 见上面 ⚠️ 那段，别重装。**
3. **要开窗拖图**（用户说"开交互调图 / 我要拖一下"）：再装 Qt 后端，**多约 150MB**：
   ```bash
   pip install "mpltweak[qt]"
   ```
   不装也能开窗：Windows / macOS 自带 `tkinter`，会**自动退回 Tk 弱化版**（功能可用、交互弱一些），stderr 会打一句提示。
4. **装之前必须得到用户同意**（要下载、占磁盘），不要不吭声就装。

**开窗失败或退化时，用人话转述（不要把诊断码原样丢给用户）**：
- **开窗 `rc=4`** = 两个图形后端都拿不到（典型：Linux 服务器没装 `python3-tk`）。转述成："这台机器现在没有能画窗口的组件。**只做排版 / 体检 / 写回完全不受影响**；要开窗的话，我可以装一个图形组件（约 150MB），或者让系统装 `python3-tk` —— 你想怎么办？"（包名只出现在**你自己执行**的命令里）
- **看到 Tk 弱化版提示**（stderr 的人话）= 没装 Qt，自动用了备选方案。转述成："窗口能开，但用的是简化版交互。想要完整体验，我可以装 `mpltweak[qt]`（约 150MB），要装吗？"
- **禁止**把 `rc=4`、「图形后端」、「Qt 绑定」这类词直接抛给非技术用户 —— 一句话说清"缺什么 / 要不要装 / 不装会怎样"。

## 先说一句"要等多久"，别静默等待

`describe` / `check`（不带 `--static`）/ `apply --write` **都会在独立子进程里重跑用户的脚本**（无头 Agg 后端）；**只读预览**（`apply` 不带 `--write`）不执行脚本；
其中 **`apply --write` 默认 1 遍起**：源码里有可直接改的数字时就是 1 遍（这一遍同时看「能不能跑通」与
「改的字段有没有真落到目标图上」）；参数里有**「原位写不了」的字段**（`grid` / `spines` / `clim` / `pos` /
`xscale` 这类，**很常见**）时会**先额外跑 1 遍**判「要不要退块」
（口径见 `docs/verify.md` 的「写回会跑几遍脚本」表第 5 行）⇒ **真有改动要落地是 2 遍起**，
**需要归因"是不是脚本本来就跑不通"时 3 遍**（多跑一遍原脚本做基线对照；取不到目标图状态还会**回退到两遍口径**）——
这几遍**都在用户拿回控制权之前**发生，而且**会真的执行他的脚本（`savefig` 照常产出他的图）**；
**默认超时 300 秒**（**只有 `apply --write` 能调**：`--timeout 秒`；`describe` / `check` / 开窗**都没有**这个开关 —— 见「其他选项」的分列）。

**先把口径说对（别把因果讲反）**：重跑**不是额外负担** —— 用户改完之后**本来就要重跑确认图对不对**，
而且我们这次重跑**会顺便把他的图生成出来**（直接拿它给用户看，多数情况下不用再单独跑一次）。
**真正的门槛只有一条：脚本能不能在无头环境里跑通。**
慢脚本只是"等待约等于单次运行的时长（有「原位写不了」的字段时 2 倍起，走到回退或归因时 2~3 倍）"：
**绝对不要对用户说"你这脚本太慢 / 不合适 / 不能用"**（那是错误归因）。

所以任何一步动手前，先用一句人话给预期：

> "接下来我会跑一遍你的脚本做验证（顺利的话顺手把你的图也出好），通常几十秒，慢的脚本可能几分钟。"

用户看到的是"卡住了"，不是"在干活"。脚本明显很慢（读大数据、画地图）时，**走 `apply --write` 时**提前把 `--timeout` 调大并告诉用户"这一步会久一点"（开窗那一步没有超时开关，等多久就是多久）。

## 命令速查

| 命令 | 作用 |
|---|---|
| `mpltweak <脚本.py>` | **开窗调图**（主用法）。`--fig N` 只挂第 N 张；`--params X` 改参数位置 |
| `mpltweak launch <脚本.py>` | 同上，**显式子命令**写法（脚本名容易和子命令撞、或批处理里想写明白时用） |
| `mpltweak apply <脚本.py>` | 只读预览：列出会改哪些数字，**不动代码**。想看**逐行差异**（会改哪几行、从什么改成什么）就加 `--diff`（别名 `--preview-diff`，同一个开关；同样**不落盘、不备份**） |
| `mpltweak apply <脚本.py> --write` | **原位写回**（备份 + 三层验证 + 失败自动回滚） |
| `mpltweak apply <脚本.py>`（默认 `--style auto`） | 每张图各自决定：代码里有数字就原位改，改不了（如 `plt.subplots` 网格位置）自动改用插入调整块 |
| `mpltweak apply <脚本.py> --style inplace` | 强制只用原位改数字；**写不了的字段会让命令明确失败**（`rc=1`，JSON 的 `pending` 列出字段），不会静默“保持原样” |
| `mpltweak apply <脚本.py> --snippet` | 输出可粘贴片段（你要手动按用户风格落实时用） |
| `mpltweak apply <脚本.py> --json` | 机器可读结果（过程信息走 stderr） |
| `mpltweak revert <脚本.py>` | **预览回退**：打印会改回什么，**一个字节都不动** |
| `mpltweak revert <脚本.py> --write` | **真回退**：把脚本恢复到**备份里的字节**（默认用固定名 `.tweak.bak`；它**首份优先** ⇒ 回到它建立那一刻、通常就是你第一次改这张图之前的原件；**固定名缺失但还有 pid 副本时自动退回最早那份**，`rc=0`、逐字节回到最初，`--json` 用 `fallback: "no_fixed_backup_used_oldest_pid"` 标明；两处都没有才 `rc=2` / `no_backup`）；`--first` 回到**最早**那次写回前（读最早一份 pid 副本） |
| `mpltweak describe <脚本.py>` | **不开窗**导出当前排版（参数 JSON）。`--all-figs` 导全部（**嵌套**的 `{"figures": [...]}`，包装本身不能直接喂回 `apply --params`；但 **0.1.14 起会顺手按每图写好 `<stem>.fig<k>.json`**，那个能喂，见「口径与默认值」）；`-o 文件` **只落盘、不打印 JSON**（stdout 为空，屏幕上只有一句「已写入 …」走 stderr）；`--compact` 紧凑 |
| `mpltweak check <脚本.py>` | **排版体检**：对齐 / 等大 / 间距 / 字号 / 越界 / 重叠。`--json` 机器可读，退出码 1 = 有 **error 级**问题。字段：`ok` / `clean` / `verdict` / `problems[]` / `warnings[]` / `n_axes` / `n_colorbar` / `tol` / `text_checked` / `n_texts` / `tight_bbox` / `layout_engine[]`。**`ok` 只等于"没有 problems"**；文字压字 / 刻度拥挤 / 色标压轴 / tight 降级的越界文字都在 **`warnings`** 里 ⇒ **要判"能不能对用户说没问题"读 `clean` / `verdict`**（`partial` = 这次没量文字，不许说干净） |
| `mpltweak check <脚本.py> --static` | 只做 AST 解析 + 布局引擎冲突扫描，**不执行脚本**（脚本有副作用 / CI 里先查一眼时用；代价是不量真实渲染尺寸，`--static` 通过 ≠ 图没问题） |
| `mpltweak schema` | 参数文件的 JSON Schema（draft-07），校验你自己生成的参数 |
| `mpltweak mcp` | 启动 MCP server（stdio），把上面能力暴露给 AI 客户端 |
| `mpltweak doctor` | 环境自检（后端 / Qt 绑定 / matplotlib 版本） |
| `mpltweak --version` | 打印一行版本号（`-V` 同义，`rc=0`）；只认第一个位置参数，`mpltweak x.py --version` 会透传 |
| `mpltweak --check-update [--json]` | 问 PyPI 有没有新版本（**唯一联网的命令**，其余命令一律不联网）。只查不升、不写文件。**查不到也是 `rc=0`**（断网 / 超时 / 被墙 / 载荷坏 ⇒ `--json` 的 `error` 给原因码），别当失败重试；`update_available` 是**三态**，`null` = 查不到或比不出（只有 `true` 才表示"PyPI 更新"）。用法错误才 `rc=2` |

其他选项（**按子命令分列 —— 选项不是全局通用的**）：
- **`apply`**：`--timeout 秒`（**默认 300**，就是它在子进程里重跑脚本做验证的等待上限；脚本很慢就调大）、`--python 解释器`、`--style auto|inplace|block`、`--force`、`--snippet`、`--json`、`--diff`（别名 `--preview-diff`：写回前逐行 diff，不落盘）、`--dry-run`、`--all-figs`、`--keep-unverified`、`--no-verify` / `--no-semantic` / `--verify-fast`、`--strict` / `--allow-layout-conflict`、`-v`。
- **`launch`**（含 `mpltweak <脚本>` 这种写法）：`--fig N`、`--params X`、`--force-resume`、`--dry-run`、`--verbose`、`--doctor`、**`--json`（0.1.15 起，只覆盖失败出口）** —— 成功路径照旧开窗、不产出 JSON；失败时 stdout 只有一条 JSON（`ok=false` + `error_code` + `stage` + `error` + `next_step`），退出码口径不变。**副作用**：这个开关会吃掉原本要透传给你脚本的 `--json`，要透传请写 `mpltweak launch x.py -- --json`。
- **`describe`**：`--fig N`、`--all-figs`、`-o 文件` / `--output 文件`（**把排版直接写进文件，最常用**）、`--compact`。
- **`check`**：`--fig N`、`--tol 容差`、`--json`、`--no-text`、`--no-tight`、`--static`、`--strict`。
- **`--lang zh|en|auto`（界面语言，默认跟随系统）**：**是工具选项，两种位置都认** —— 前置位（`mpltweak --lang en <脚本>`，由 `cli._consume_leading_lang` 先摘掉再分派）与子命令位（`apply` / `describe` / `check` / `launch` / `--check-update`）。认出来的 `--lang` **不会**进你脚本的 `sys.argv`；要传给脚本请用 `--`（`mpltweak x.py -- --lang en`）。`revert` / `schema` / `demo` / `mcp` 不认（`rc=2`）；`doctor --lang en` 也 `rc=2`，但会**点名**并提示写成 `mpltweak --lang en doctor`。`auto` 会**清掉**继承的 `MPLTWEAK_LANG`。**例外**：`--version` / `-V` 不受 `--lang` 影响 —— 版本号是纯 ASCII，写错语言值时照样出版本号（`mpltweak --lang xx --version` ⇒ rc=0）。等价写法一律是 `MPLTWEAK_LANG=en`。
- **`--force` 只有 `apply` 认**（参数比脚本旧时照样套用；`describe` / `check` / `revert` 给了是 `rc=2`）；**`--no-tight` 只有 `check` 认**（按 0.1.8 旧口径严判 `bbox_inches='tight'`；`apply` / `describe` / `revert` 给了是 `rc=2`）。

**`--timeout` 只有 `apply` 认** —— `check` / `describe` / `launch` **都不认**（实测：`mpltweak check <脚本> --timeout 900` ⇒ `unrecognized arguments`、`rc=2`）。**`check` 也不需要它**：实测一份要读 NSIDC + 128 个 MME 文件的重脚本，`check` 只用 **50 秒**左右（远低于 300 秒）⇒ "默认 300 秒不够"这个担心对 `check` 不成立。选项清单以 `mpltweak <子命令> --help` 为准。

**备份与回退（成对记）**：`apply --write` 之后，**脚本旁**会同时存在两份可回退的备份 ——
① `<脚本目录>/.tweak_params/<脚本名>.tweak.bak`（**固定名、首份优先**：只在不存在时创建，之后的写回
**不覆盖**它 ⇒ 默认 `revert` 回到的是它**建立那一刻**、通常就是你第一次改这张图之前的原件，而**不是**
上一步的中间态）；② `<脚本目录>/.tweak_params/<脚本名>.tweak.<pid>.bak`（**该次写回之前**的专属副本；
保留"最近 5 份 + **最早 1 份**" ⇒ `--first` 回到**最早**那次写回之前；副本缺失时 `--first` **明确失败
`rc=2`**，不会拿固定名备份冒充"最早"）。
备份锚在**脚本**上：写回时用了 `--params`，`revert` **不用**再传一遍。
`revert --write` 覆盖前会把当前文件另存为 `<脚本目录>/.tweak_params/<脚本名>.revert.bak`（**所以 revert 自身也可逆** —— 手滑了还能再回去）。
恢复是**逐字节**的（BOM / CRLF / GBK 脚本原样回来）。写回前的变化清单先用不带 `--write` 的 `revert` 预览给用户过目。

`--no-verify` / `--no-semantic` 会跳过验证 —— **那是写坏代码时唯一的自动安全网，一般不要用**；脚本确实无法无头重跑时才考虑，且用前先确认脚本已在版本控制里。

**`--verify-fast` 不是“跳过验证”，而是“只查语法”**：它**不重跑脚本**，因此证明不了“参数里某个字段与源码现值相等”。源码里没有可比写法时（典型 `plt.subplots()` 的网格轴位置，由 subplotspec + 边距算出），它会**保守退块插块**。实测同一条 agent 路径（`describe` 原样导出 → `apply --write`）：加它会在一份 12 行 `plt.subplots(2, 2)` 脚本上插 31 行块，不加则是 `reason=no_change`、一个字节不动 —— **默认不要加 `--verify-fast`**。

## 什么时候该提醒用户检查更新

满足任一条，**每次会话最多查一次**版本（`mpltweak --version` + `pip index versions mpltweak`）：

1. **首次使用**：本次会话第一次用 mpltweak 前，先确认版本；如果落后最新版，用一句人话告诉用户，**不阻塞**继续干活。
2. **用户抱怨不好用**（"不好用 / 没效果 / 改了没用 / 报错"）：先查版本。若用户装的是旧版，说明"这可能是某版已修的问题"，而不是直接下结论。
3. **文档提到但 CLI 不认**：先对照本手册「其他选项」一节看**这个选项是否属于这条子命令**（`--timeout` 只有 `apply` 认、`--force` 只有 `apply`、`--no-tight` 只有 `check`）——放错子命令同样报 `unrecognized arguments`。确认该选项本应属于它却仍报，才是版本落后，再建议升级。

**红线**：

- **不要静默升级用户的包**（会改环境，必须先说明升到哪个版本、征得同意）；
- **不要在没确认版本时断言"这是已知问题"**；
- 给用户的话里**不要出现命令行 / 参数名**（命令你自己执行，用户听到的是"能升级到新版吗"这种人话）。

### 怎么查（都是只读，不改环境）

1. **他现在装的是哪一版**：`mpltweak --version`（`-V` 同义）。判据看**输出**：匹配 `^mpltweak \d+\.\d+\.\d+` 才算新版；报错、或输出不是这一行 ⇒ 按旧版处理。
2. **最新版是哪一版**：`pip index versions mpltweak`（只读）；没有这条命令的 pip 就看 `https://pypi.org/pypi/mpltweak/json` 的 `info.version`。
3. **他装的是旧版** ⇒ 先说清"你可能遇到的是**某版已经修掉**的问题"，指向 `https://github.com/shdbl/mpltweak/blob/main/CHANGELOG.md` 里那个版本的条目（那里只写用户能感知到什么）；**征得同意后**再升级：`pip install -U mpltweak`（要开窗拖图的用 `pip install -U "mpltweak[qt]"`）。升级后用 `mpltweak --version` 复核一次，再让他重试原来的操作，并明确问一句"现在还复现吗"。
4. **他已经是最新版** ⇒ 别急着说"那就是 bug"。先回头查已知边界：`apply --diff` 只是**计划预览**（真写回时验证失败仍会回滚）、开了 `constrained_layout` / `tight_layout` 的图**写回轴位置会被默认拦下**、脚本在无头环境跑不通时会回滚、含"原位写不了"的字段时会多跑一遍脚本（看起来像"变慢了"）—— 这些都在 `docs/troubleshooting.md` 与 `docs/boundaries.md` 里。确实与文档不符，再让用户带着**版本号 + `--json` 输出**去 https://github.com/shdbl/mpltweak/issues 提交。

## 三种用法，先判断该走哪个

### A. 首推流程：六步（最常见）

1. **先让用户把"画什么"定下来。** 他可能已经让你画过图；没有就先按他的要求把图画出来、跑出来给他看。
2. **再让他确认图的内容没问题**（坐标轴 / 标签 / 数据对不对）—— **内容没定之前不要动排版**：
   排版是内容定稿之后才做的事，顺序反了会白调一轮。
3. **他说"要交互式修改"，这时才开窗**（用后台 job，窗口是交互的，job 到关窗才结束）：
   ```bash
   mpltweak <脚本路径>
   ```
   启动器会打印：脚本名 / 共几张图 / 正在调哪张 / 参数写到哪 / 续调了几项。
   开窗前提醒他：窗口里请**切英文输入法** —— 中文 IME 打开时 `c/s/g/x/y` 等字母快捷键会被吞掉直接上屏。
4. **他在窗口里改**（拖面板 / 拖图例 / 悬停改字号 / 对齐均分；键位表见下）。
5. **他关窗后：用一句人话总结"改了哪些地方"，并明确请他确认**（位置 / 字号 / 图例分别是多少 → 多少），
   同时说明"**还没写进代码，等你点头**"。**这一步不能省** —— 不要让用户自己去翻参数文件。
6. **他认可之后，才 `apply --write`，然后重跑把新图给他看**（这次重跑顺便出图，不用再单独跑一遍）：
   写回前先给一句"接下来我会跑一遍你的脚本做验证（顺手把图也出好），几十秒到几分钟"；
   写回后问是否清理 `.tweak_params/`。
   他改主意了：`mpltweak revert <脚本.py> --write` 会回到**固定名备份 `.tweak.bak` 里的原件**（首份优先 ⇒ 通常是最初那份，**不是"只撤这一步"**；先不带 `--write` 看预览，再问他确认）。

### B. 你直接排（用户不在场 / 要批量统一）

```bash
mpltweak describe <脚本.py> -o layout.json    # 读：现在的排版
# 改 layout.json 里的几个数字（如 axes[0].pos / title_fontsize）
# 存成 <脚本目录>/.tweak_params/<脚本名>.json
mpltweak apply <脚本.py> --write              # 写：落回源码并自检
```

`apply --write` 这步会**重跑脚本验证写回结果**（默认超时 300 秒）—— 动手前先给用户一句"要跑一遍脚本，几十秒到几分钟"。
要撤销写回：`mpltweak revert <脚本.py> --write`（回到固定名备份里的原件，**不是**只撤最后一步；先不带 `--write` 预览）。

适合：把**一张排好的图当模板**套到其他脚本（改 `script` 字段即可）、批量统一整篇论文的排版。

### C. 接力（推荐给"想要好看"的场景）

你按规则先排一版 → 用户开窗接着调（**参数会自动续上**，因为文件已存在）→ 确认落实。
这样你的"精确"补上用户的"审美"。

## 人 + agent 怎么分工

**"好不好看"归用户，"精确落实"归你。**

- 不要替用户决定审美（配色、字号大小这类没有标准答案的）；
- 但你可以保证"说改哪就精确改哪"，并且**用 `check` 自查几何问题**（不齐 / 不等大 / 间距不均 / 越界 / 重叠）——
  这些不需要"看到"图，数字就能判断。

## 键位速查（只用于回答用户；完整表在 docs/interaction.md，窗口里按 `?` 也能看带图帮助）

用户问"怎么拖/怎么缩放/怎么对齐"时**直接转述**这几条就够：拖面板移动、拖边/角缩放、**Shift**+拖角等比、**Alt**+拖角中心对称、**Ctrl+点击**多选、方向键移动面板、**Ctrl+Shift+L/R/T/B/C/M** 对齐、**Ctrl+Shift+H/V** 均分、悬停文字按 `+`/`-` 改字号、拖图例吸附到 8 个标准位、**Ctrl+F** 裁白边、**Ctrl+Z** 撤销。
**开窗前提醒用户切英文输入法**：中文 IME 打开时 `c/s/g/x/y` 等字母快捷键会被吞掉直接上屏。

**点不到想点的对象**（用户报"选不中"时）：光标下有多个对象时**后加入者优先、再按面积小者优先**（压在极地图上的 colorbar 会先被选中，极地图按真实圆形判命中而不是外接矩形）；仍点不到就用 `Alt+点击` **穿透**到下一层，或 `Tab` **逐个循环**所有可见对象 —— 不存在"永远选不中"的对象。

**空格**＝线条模式：只留边框、刻度文字与图例，隐藏数据图元，重绘明显更快；排版挪位置时按一下，再按一次恢复。

## 口径与默认值

- **参数文件**：`<脚本目录>/.tweak_params/<脚本名>.json`（隐藏目录集中存放；`--params` 可改）。
  格式是**公开规范**：顶层 `version` 字段当前是 **4**（旧版文件照读）；完整字段表用 `mpltweak schema`。
- **续调语义**：参数文件 = "待落实状态"。再次开窗会自动套回图上（打印"已套用上次参数 N 项"）；
  落实进代码后它与代码等价，再开窗套用即为无操作。用户想从头调就删掉它。
- **多图脚本**：默认**每张图都弹窗且都可交互**，用户改哪张记哪张；一次改了多张就各留一份参数
  （主文件 = 最后改动的那张 + `<stem>.fig<k>.json`），`apply` 会**逐张落实**。全程没动则不写文件。
- **写回定位**：按参数里的 `fig_index` 定位到对应图的 `savefig` 之前；**循环出图**自动加守卫
  只改目标那张（要统一应用加 `--all-figs`）；多 mappable 轴按接收者变量名 /
  `fig.colorbar(cs, ax=ax1)` 的显式父轴精确配对。
- **`--all-figs` 在两条命令上含义不同**：`describe --all-figs` 导出的是**嵌套**的
  `{"version": 4, "script": ..., "n_figs": N, "figures": [...]}`（单图状态在 `figures[i]` 里，
  顶层**没有** `axes`）；而 `apply --all-figs` 指的是"循环出图时**统一应用**"，它的输入仍是
  **单图**状态。所以那个**包装**不能直接喂回 `apply --params` —— 会 `rc=1 / reason=no_axes`
  （**明确失败**、脚本一个字节都不改，不是静默忽略）。
  要落实多图：**0.1.14 起 `describe --all-figs -o all.json` 会顺手按每图各写一份
  `all.fig<k>.json`**（apply 认的命名）⇒ 直接 `apply <脚本> --params all.fig1.json --write`；
  写进 `.tweak_params/` 时连 `--params` 都不用给。也可以老办法逐图 `describe --fig N`。
- **模式固定为"重图"**（ghost + blit 预览）：所有拖拽 / 框选 / 多选都先预览、松手才落位。
- **后端**：脚本里写 `matplotlib.use('Agg')` 或 `'TkAgg'` **都不用改**（启动器会拦掉切换）；
  后端按 **QtAgg → Qt5Agg → Qt6Agg → TkAgg 真建 canvas 逐个探测**。`pip install mpltweak` **不含 Qt**（Qt 是可选 extra `[qt]`）：Windows / macOS 官方 Python 自带 `tkinter`，因此只装核心包也能开窗，用的是 **Tk 后端（交互弱化版）**，并在 **stderr** 打一句人话提示（`MPLTWEAK_NO_HINT=1` 静默、`--dry-run` 不提示）；想要完整体验：**征得同意后由你**执行 `pip install "mpltweak[qt]"`。两个后端都拿不到（典型 Linux 服务器没装 `python3-tk`）→ 开窗 `rc=4`，提示装 `mpltweak[qt]` 或系统包的 `python3-tk`；**纯 CLI（describe / check / apply / mcp）不需要任何图形后端**。当前用的是哪个：`mpltweak doctor` 会打印一行“开窗后端”。

## 常见坑

- **位置接管要在 `tight_layout()` 之后**；接管后不要再调 `tight_layout()` / `subplots_adjust()`，
  否则位置会被重算掉。
- **colorbar 位置**必须同时 `set_axes_locator(None)` + `set_box_aspect(None)` 再 `set_position`，
  否则每次重绘都会被 `_ColorbarAxesLocator` / `_box_aspect` 拉回自动位置。
- **改 `clim` 对显式离散色的 `contourf(..., colors=[...])` 不会重新着色**（norm 是 `NoNorm`）：
  参数会变、图面不变，先跟用户说清楚。
- **锁长宽比的轴**（cartopy 地图、`imshow`）位置由宽高比在重绘时决定：`--style block` 时
  位置比对会跳过；`check` 也不把这些轴算进几何比对。
- **字号是"悬停"改的**，不是选中改的 —— 鼠标要停在那个文字上再按 `+`/`-`。
- **原地改不了的情况很正常**：`plt.subplots()` 的网格位置、代码里没写的字号，原位那一趟就是改不了。
  默认的 `--style auto` 会自动改用插入调整块把它们**落地**（栏里同时列出原位为什么没成），通常不用
  手动加参数；只有显式 `--style inplace` 才会因为"写不了"而 `rc=1` 失败（这是故意的，见命令表）。

- **中文变成 `◆` / `�` 时，先分清是谁的输出**：**你自己** `python 脚本.py` 也乱 ⇒ 与工具无关（Windows 上 Python 被管道接走按 **cp936** 编码，而面板按 UTF-8 解；给子进程设 `PYTHONIOENCODING=utf-8` 或 `PYTHONUTF8=1` 即可）。**工具**的输出在管道下**已经是 UTF-8**（0.1.21 起），还乱就用 `--json`（fd 级 UTF-8，任何情况都不该乱）。**不许**把乱码行"翻译"成通顺中文 —— 见铁律第 9 条。

## 写回失败 / 半成功时怎么读结果

`apply --json` 的 `files[]` 里有几个字段决定"这次到底算不算成功"——**别只看 `ok`**：

- **`stage` / `next_step`**（0.1.15 起，**顶层**，只在失败/降级时出现）：`stage` 说失败卡在哪一步
  （`params` 参数本身 / `preflight` 动手前检查 / `backup` 备份与留档 / `writeback` 真正动文件 /
  `verify` 写回后验证 / `finalize` 收尾）；`next_step` 给一句可直接照着做的下一步。**它们是建议不是
  保证**（脚本自己有语法错时 `--force` 也救不回来）；**判定结果仍只看 `ok` / `error_code` / 退出码**。
  成功载荷**没有**这两个键，`files[]` 条目里也没有 `stage`（逐文件原因照旧看 `files[].reason`）。
  **唯一例外（0.1.16 起）**：写回**已落盘**、只有收尾那一步降级时，`ok` 仍是 `true`、`rc=0`，
  顶层会带 `stage: "finalize"` + `next_step`（典型：刷参数文件 mtime 失败，逐文件归因
  `attribution: "mtime_sync_failed"`）。这一档**不是**写回失败 —— 别因为它去回滚或改脚本。

- **`attribution` / `attribution_detail`**（逐文件；0.1.16 起有细分）：`attribution` 的值域 = 脚本侧三类
  （`script_cannot_run` / `script_needs_args` / `script_needs_gui_or_headless_impossible`）+ `tool_bug`
  + `mtime_sync_failed`。0.1.16 新增 **`attribution_detail: "generated_code_failed"`**（`attribution` 仍是
  `tool_bug`）= **写回之后的代码跑不起来、而用户的原脚本本身能跑** ⇒ 是 mpltweak 生成的那几行有问题，
  **不是用户脚本的问题**，别让人去改自己的脚本。它是**升级不是新分类**（分类器仍只产出脚本侧三类，判
  `tool_bug` 只发生在 `apply` 层、只认 `verify_fail_kind` 为 `after_write_broken` / `anchors_all_failed`
  的证据，**不从文案反推**）。

- **`layers = {syntax, rerun, semantic}`**：三层各自"跑没跑"。`true` = 跑了，`null` / 缺键 = **没跑**。
  **只有 `semantic: true` 才算"布局真的落到目标图上"**；`verified: null` 表示**语义层没跑**（取不到图
  状态，或你加了 `--verify-fast`）——**不要当成通过**；`verified: false` 才是明确的失败。
- **`verify_passes` / `semantic_basis`**：两个键**恒有**（别再从 `warnings` 的文本里猜）——
  `verify_passes` = 这次为验证在**写回后的脚本**上跑了几遍：**1** = 默认单遍 / **2** = 单遍取不到目标图
  状态、已回退到两遍 / **0** = 这次没有为验证重跑脚本（`--no-verify` / `--verify-fast` / 预览 /
  没有可写字段）。**`0` 不是失败**，它只表示"这一档没有为验证重跑脚本"（与提示语「没有重跑脚本 ——
  不保证能跑通、也不保证布局真的落到目标图上」是同一口径）。它**只数主验证的遍数**：参数里有"原位
  写不了"的字段时会**先额外跑一遍**判要不要退块（见上面「先说一句要等多久」那节），**这一遍不计入**
  ⇒ `verify_passes=1` **不等于**"整条命令只跑了 1 遍脚本"。
  `semantic_basis` = 语义判定的依据：**`live+savefig`**（图对象状态 + 磁盘那张图的追加判据）/
  **`live`**（只有图对象状态）/ **空串**（这次没有语义判定依据）。想知道"这次**为什么**回退到两遍"：
  先看 `verify_passes=2`，再读同一条目的 `warnings`。
- **`style`**（**恒有值，永不为 `null`**）：这次**请求的通道**（`auto` / `inplace` / `block`）。真走过写回
  时它被**实际采用的通道**覆盖（`auto` 退块 ⇒ `block` + `auto_fallback: true`）；预览与早退条目
  （`bad_params` / `stale` / `layout_conflict`）填的是**请求值**。**别拿 `style` 当"落没落地"** ——
  那件事只看 `reason` 与 `applied`。
- **`layout_conflicts` / `layout_why`**：`layout_conflicts` **恒有**（数组）—— 脚本里开着的布局引擎冲突点
  （`constrained_layout` / `figure.autolayout`，带行号）。**空 = 没开引擎**；非空且 `reason='layout_conflict'`
  ⇒ 这次要写轴位置被默认拦下（`applied: false`，**只这一份**没写盘，见下表）；非空而 `reason` 是
  `ok` / `best_effort` / `refused` / `fail` ⇒ 冲突**没挡这次**（只改了非几何字段 / 加了 `--force` / 走的是拒写或回滚那条路）。`layout_why` 是"这次会不会真改
  位置"的判定依据（五值：`no_pos` 参数里没有位置字段 / `proved_equal` 源码现值已等于目标 /
  `live_equal` 实测一致 / `live_differs` 实测不同、真要写 / `unjudged` 判不了 ⇒ 保守拒绝）；
  **0.1.14 的 `--json` 还没带出这个键**，此时按"这次有没有 `--no-verify` / `--verify-fast`"分流：
  关了验证还拒 ⇒ `unjudged`，正常验证后仍拒 ⇒ `live_differs`。
- **`diff` / `preview_sha256` / `preview_sha256_norm` / `preview_bytes`**（**只有 `--diff` 时**才非空）：
  `diff` 逐行给 `{line, type, old, new}`（`line` 是**新文件**的行号；`type` = `inplace` 原位补参数 /
  `insert_block` 插入调整块 / `replace` 其余），按 `line` 重放回原文就能在落盘前看到"改完什么样"；
  `preview_sha256` = **真写盘后文件的 sha256**（唯一例外是调整块说明行的"生成于 <时间>"，预览与真写回
  跨了分钟就只差这几个字符 ⇒ 要确定性对拍用 `preview_sha256_norm`）；`preview_bytes` = 将要写出的字节数。
  **预览不重跑脚本** ⇒ "验证失败 → 自动回滚"这类由验证结果驱动的分支不在预览里。
- **`reason`** 常见取值（都在集成树上实测过）：

  | 值 | 含义 | 你该做什么 |
  |---|---|---|
  | `ok` | 写回成功且验证通过 | 完事 |
  | `preview` | 这次**只预览**（不带 `--write` 的 `apply`，或 `--diff`）：`applied: false`、**一个字节都没写**，属正常 | 确认差异后再跑 `--write`（`--diff` 下就是去掉 `--diff`） |
  | `no_change` | 参数与源码一致 → **一个字节都没改**（`applied: false` 属正常） | 不用做 |
  | `best_effort` | **能写、没全写**：能落地的都落了，其余在 `pending` / `inplace_pending`。**它不等于被拒**（拒写自成 `refused` 一档，0.1.18 起） | 看 `pending` / `inplace_pending`；判"写没写"看 `applied` / `changes` |
| `refused` | **拒写**：静态层面就证不出目标（循环里建轴 / 建轴点与参数条数对不上 / 载体名不是恰好一次模块层调用…）⇒ **源码一个字节都没动**，`error_code="nothing_applied"`、`rc=1` | 按 `--snippet` 给的手改片段改，或改脚本结构 |
  | `fail` | **写了又回滚**（重跑回比不达标 / 语法被写坏 / 重跑不通 / 没有合规块锚点…），`error_code="writeback_failed"`，原因在 `err`。**"以为改了、其实没改"就是这一档** | 读 `err`，修脚本或重调 |
  | `stale` | 参数比脚本旧（存参数之后脚本被手改过）→ **跳过** | 重新 `describe` / 开窗调；确要覆盖手改才用 `--force` |
  | `no_axes` | 参数文件里没有 `axes`（最典型：`describe --all-figs` 的**嵌套包装**直接喂回 `apply`） | 喂它顺手写出的 `<stem>.fig<k>.json`（0.1.14 起），或逐图 `--fig N` 导出 |
  | `bad_params` | 参数结构硬错误（`rc=2`），`err` 指出是哪个字段 | 修参数文件 |
  | `layout_conflict` | 脚本开着布局引擎、这次真要写**轴位置** ⇒ **默认拒绝**（这一份没写盘，默认档 `rc=1`；`--strict` 下则整批中止 `rc=2`） | 先关引擎（删 `constrained_layout=True` / 改 `layout=None`）或把要精调的轴改成 `add_axes([...])`；确实要写就 `--force` |

- **默认不保留"没验证过"的写回**：脚本在无头环境跑不通（缺数据 / 缺依赖 / 需要 GUI）时，默认
  **回滚 + `reason=fail`**，`err` 会写明"要保留请显式 `--keep-unverified`"。**只有你明确要留住半成品**
  （例如脚本要读一个只有你机器上有的数据文件）才加 `--keep-unverified` —— 自担风险：此时
  `verified: null` + warning，脚本改没改对**没有任何自动保证**。
- **块路径看 `anchor` / `attempts` / `err`**：`anchor` = 块插在哪（`before` / `after` / `tail`；
  `inplace` 表示这次没走块），`attempts` = 每次锚点尝试落在第几行、成没成（`ok` / `semantic`），
  拒绝写回时 `err` 说明为什么 —— 这是"块插到哪里、为什么没成"的唯一线索。
- **多图逐张写回不会把后续参数误判为过期**：一次 `apply` 发现多份参数文件（主文件 +
  `<stem>.fig<k>.json`）时逐张落实，基准是**本批次开始时刻**（不是"刚被我们改新的脚本 mtime"），
  成功后还会刷新这批参数的 mtime。真正过期的参数（脚本 mtime 比参数新 >1s，即存参数之后脚本被手改过）
  仍会被拦下（`reason=stale`）。**布局冲突的判定也是逐份的**：同一批里可能「这图写了、那图被拒」（拒的那份 `reason='layout_conflict'`、`applied: false`），顶层 `error_code` 取第一个失败文件、默认档 `rc=1` —— 判整体落地情况只看各条 `applied`，不要用那句「脚本一个字节没动」当整批结论。

## 本 skill 是薄壳

产品逻辑全在 pip 包 **mpltweak** 里（`pip install mpltweak` 就够跑 `describe` / `check` / `apply` / `mcp`；**开窗需要一个图形后端，而 Qt 是可选 extra、不在默认依赖里** —— 没装 Qt 时自动退回 Tk 开窗并在 stderr 提示，想要完整体验加 `pip install "mpltweak[qt]"`）。
本文件只负责"什么时候用、怎么用、别踩什么坑"—— 内核升级不需要动它。

### `doctor` 的选项口径

- `mpltweak doctor` **不接受其它选项，也不接受位置参数**（多余参数 ⇒ **rc=2**，usage 走 stderr）—— 与其它子命令一致，不再静默忽略。
- **`doctor` 没有 `--json`**：它的输出是给人看的自检结果，不产出机器可读载荷；给了 `--json` ⇒ **rc=2** 且明确说明（**明拒，不静默忽略**）。
- `-h` / `--help` ⇒ rc=0，打印 doctor 用法。
