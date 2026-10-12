---
name: mpltweak
description: 交互式 matplotlib 排版（脚本零侵入）。AI 写完绘图脚本后，用户说"开交互调图 / 我要拖一下图 / 调图 / 微调布局 / 图的位置不对 / 帮我排版多子图 / 统一这几张图的排版"时用它。开窗拖拽（PPT 式手势：多选、对齐、均分、吸附、裁白边、线条模式）→ 参数静默存到 .tweak_params/*.json（此时脚本一个字没动）→ 用户确认后用 `mpltweak apply --write` 原位写回源码（AST 确定性 + 三层验证 + 失败自动回滚）。也支持不开窗的 agent 闭环：describe 读排版 / check 体检 / schema 校验 / mcp 接入 AI 客户端。用户脚本里不出现本工具任何痕迹。
---

# mpltweak —— 交互式调图闭环

**一句话**：脚本零侵入 → 用户拖图 → 参数落盘 → 用户点头 → 数值精确写回纯 matplotlib 代码。

**定位**：只管**位置 / 字号 / 图例 / 样式**，不发明内容。文字、数据、图元仍由用户代码决定。

面向人的完整文档在项目根的 `README.md`（含演示 GIF、键位表、JSON 规范）。

## 铁律（违反即失败）

1. **脚本零侵入**：绝不在用户脚本里写 `import mpltweak...`、`gaitu()`、`_POS`/`_FONTS` 写回区、`--tweak` 分支、`if '--tweak' in sys.argv`。工具**全部在脚本外部**（CLI + 参数文件）。
2. **只在用户明确要求调图时才开窗**；没说要调图就不要弹窗。
3. **关窗只存参数**：只允许写 `<脚本目录>/.tweak_params/<脚本名>.json`。**不碰代码**、不自动落实、不删参数。
4. **等明确确认才落实**：只有用户说"确定 / 应用 / 落实 / 就这样"之后才 `apply --write`；落实前先把变化清单汇报给用户过目。
5. **落实必须是纯 matplotlib**：`set_position` / `legend(loc=, fontsize=)` / `set_title(fontsize=)` / `tick_params(labelsize=)` / `figsize=`；不得引入本工具的任何符号。
6. `.tweak_params/` 是临时产物；落实完成后问用户是否清理。

## 命令速查

| 命令 | 作用 |
|---|---|
| `mpltweak <脚本.py>` | **开窗调图**（主用法）。`--fig N` 只挂第 N 张；`--params X` 改参数位置 |
| `mpltweak apply <脚本.py>` | 只读预览：列出会改哪些数字，**不动代码** |
| `mpltweak apply <脚本.py> --write` | **原位写回**（备份 + 三层验证 + 失败自动回滚） |
| `mpltweak apply <脚本.py>`（默认 `--style auto`） | 每张图各自决定：代码里有数字就原位改，改不了（如 `plt.subplots` 网格位置）自动改用插入调整块 |
| `mpltweak apply <脚本.py> --style inplace` | 强制只用原位改数字；**写不了的字段会让命令明确失败**（`rc=1`，JSON 的 `pending` 列出字段），不会静默“保持原样” |
| `mpltweak apply <脚本.py> --snippet` | 输出可粘贴片段（你要手动按用户风格落实时用） |
| `mpltweak apply <脚本.py> --json` | 机器可读结果（过程信息走 stderr） |
| `mpltweak describe <脚本.py>` | **不开窗**导出当前排版（参数 JSON）。`--all-figs` 导全部（**嵌套**的 `{"figures": [...]}`，不能直接喂回 `apply --params`，见「口径与默认值」）；`-o 文件` 写文件；`--compact` 紧凑 |
| `mpltweak check <脚本.py>` | **排版体检**：对齐 / 等大 / 间距 / 字号 / 越界 / 重叠。`--json` 机器可读，退出码 1 = 有问题 |
| `mpltweak check <脚本.py> --static` | 只做 AST 解析 + 布局引擎冲突扫描，**不执行脚本**（脚本有副作用 / CI 里先查一眼时用；代价是不量真实渲染尺寸，`--static` 通过 ≠ 图没问题） |
| `mpltweak schema` | 参数文件的 JSON Schema（draft-07），校验你自己生成的参数 |
| `mpltweak mcp` | 启动 MCP server（stdio），把上面能力暴露给 AI 客户端 |
| `mpltweak doctor` | 环境自检（后端 / Qt 绑定 / matplotlib 版本） |

其他选项：`--all-figs`（循环出图时统一应用，默认只改参数记录的那张）、`--timeout 秒`、`--python 解释器`、`--lang zh|en`（界面语言，默认跟随系统）、`--force`（脚本比参数新时仍强行套用；默认跳过以免覆盖手改）。

`--no-verify` / `--no-semantic` 会跳过验证 —— **那是写坏代码时唯一的自动安全网，一般不要用**；脚本确实无法无头重跑时才考虑，且用前先确认脚本已在版本控制里。

**`--verify-fast` 不是“跳过验证”，而是“只查语法”**：它**不重跑脚本**，因此证明不了“参数里某个字段与源码现值相等”。源码里没有可比写法时（典型 `plt.subplots()` 的网格轴位置，由 subplotspec + 边距算出），它会**保守退块插块**。实测同一条 agent 路径（`describe` 原样导出 → `apply --write`）：加它会在一份 12 行 `plt.subplots(2, 2)` 脚本上插 31 行块，不加则是 `reason=no_change`、一个字节不动 —— **默认不要加 `--verify-fast`**。

## 三种用法，先判断该走哪个

### A. 用户自己拖（最常见）

1. **用后台 job 开窗**（窗口是交互的，job 到关窗才结束）：
   ```bash
   mpltweak <脚本路径>
   ```
   启动器会打印：脚本名 / 共几张图 / 正在调哪张 / 参数写到哪 / 续调了几项。
2. **开窗前提醒用户**：窗口里请**切英文输入法** —— 中文 IME 打开时 `c/s/g/x/y` 等字母快捷键会被吞掉直接上屏。
3. 用户关窗后，读启动器输出，**向用户复述改了哪些**（位置 / 字号 / 图例），并说明"**还没写进代码，等你确认**"。
4. 用户确认 → `apply --write` → **重跑脚本出 PNG，用 show_image 给用户看** → 问是否清理 `.tweak_params/`。

### B. 你直接排（用户不在场 / 要批量统一）

```bash
mpltweak describe <脚本.py> -o layout.json    # 读：现在的排版
# 改 layout.json 里的几个数字（如 axes[0].pos / title_fontsize）
# 存成 <脚本目录>/.tweak_params/<脚本名>.json
mpltweak apply <脚本.py> --write              # 写：落回源码并自检
```

适合：把**一张排好的图当模板**套到其他脚本（改 `script` 字段即可）、批量统一整篇论文的排版。

### C. 接力（推荐给"想要好看"的场景）

你按规则先排一版 → 用户开窗接着调（**参数会自动续上**，因为文件已存在）→ 确认落实。
这样你的"精确"补上用户的"审美"。

## 人 + agent 怎么分工

**"好不好看"归用户，"精确落实"归你。**

- 不要替用户决定审美（配色、字号大小这类没有标准答案的）；
- 但你可以保证"说改哪就精确改哪"，并且**用 `check` 自查几何问题**（不齐 / 不等大 / 间距不均 / 越界 / 重叠）——
  这些不需要"看到"图，数字就能判断。

## 键位速查（转述给用户；`?` 键能在窗口里打开带图的帮助页）

| 组 | 键 | 作用 |
|---|---|---|
| 选择 | 拖面板 / 拖边、拖角 | 移动 / 缩放 |
| 选择 | **Shift**+拖角 · **Alt**+拖角 | 等比缩放 · 中心对称缩放 |
| 多选 | **Ctrl+点击** · **Shift+点击** · 拖空白 | 加选 / 加选 / 橡皮筋框选（完全框住才选中） |
| 移动 | **方向键** | **移动选中面板**（PPT 肌肉记忆） |
| 移动 | **Shift+方向键** | **微调尺寸**（中心不动） |
| 对齐 | **Ctrl+Shift+L/R/T/B/C/M** | 左 / 右 / 顶 / 底 / 水平居中 / 垂直居中 |
| 均分 | **Ctrl+Shift+H / V** | 水平 / 垂直等间距（两端不动） |
| 字号 | 悬停文字后 `+` / `-` | 标题 / 轴标 / 刻度 / 图例 / colorbar 标签（`ax.text('(a)')` 面板标注也能调） |
| 图例 | 拖图例 | 半透明预览 + 松手吸附到 8 个标准位 |
| 样式 | `[` `]` · `c` · `C` · `g` · `s` · `x` `y` | 线宽 · 线色 · colormap · 网格 · 边框 · log 轴 |
| 画布 | **Ctrl+F** · `n` · **空格** · **Ctrl+Q** | 裁白边 · 吸附开关 · 线条模式 · 关全部窗口 |
| 历史 | **Ctrl+Z** · **Ctrl+Y** 或 **Ctrl+Shift+Z** | 撤销 · 重做（Office 惯例双绑） |
| 其他 | **Alt+拖拽** | 临时关闭吸附（这一下不对齐） |

**空格（线条模式）**：只留边框 + 刻度文字 + 图例，隐藏数据图元。cartopy 全球图实测全量重绘
494ms → 169ms，排版挪位置时用它，再按一次恢复。

## 口径与默认值

- **参数文件**：`<脚本目录>/.tweak_params/<脚本名>.json`（隐藏目录集中存放；`--params` 可改）。
  格式是**公开规范**：顶层 `version` 字段当前是 **4**（即代码里的 `SCHEMA_VERSION`；旧版文件照读），README 有完整字段表。
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
  **单图**状态。所以 `describe --all-figs` 的产物**不能**直接喂回 `apply --params` ——
  会 `rc=1 / reason=no_axes`（**明确失败**、脚本一个字节都不改，不是静默忽略）。
  要落实多图：逐图 `describe --fig N` 导出单图状态，再逐图 `apply`。
- **模式固定为"重图"**（ghost + blit 预览）：所有拖拽 / 框选 / 多选都先预览、松手才落位。
- **后端**：脚本里写 `matplotlib.use('Agg')` 或 `'TkAgg'` **都不用改**（启动器会拦掉切换）；
  后端按 **QtAgg → Qt5Agg → Qt6Agg → TkAgg 真建 canvas 逐个探测**。`pip install mpltweak` **不含 Qt**（Qt 是可选 extra `[qt]`）：Windows / macOS 官方 Python 自带 `tkinter`，因此只装核心包也能开窗，用的是 **Tk 后端（交互弱化版）**，并在 **stderr** 打一句人话提示（`MPLTWEAK_NO_HINT=1` 静默、`--dry-run` 不提示）；想要 Qt 体验让用户 `pip install "mpltweak[qt]"`。两个后端都拿不到（典型 Linux 服务器没装 `python3-tk`）→ 开窗 `rc=4`，提示装 `mpltweak[qt]` 或系统包的 `python3-tk`；**纯 CLI（describe / check / apply / mcp）不需要任何图形后端**。当前用的是哪个：`mpltweak doctor` 会打印一行“开窗后端”。

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

## 写回失败 / 半成功时怎么读结果

`apply --json` 的 `files[]` 里有几个字段决定"这次到底算不算成功"——**别只看 `ok`**：

- **`layers = {syntax, rerun, semantic}`**：三层各自"跑没跑"。`true` = 跑了，`null` / 缺键 = **没跑**。
  **只有 `semantic: true` 才算"布局真的落到目标图上"**；`verified: null` 表示**语义层没跑**（取不到图
  状态，或你加了 `--verify-fast`）——**不要当成通过**；`verified: false` 才是明确的失败。
- **`reason`** 常见取值（都在集成树上实测过）：

  | 值 | 含义 | 你该做什么 |
  |---|---|---|
  | `ok` | 写回成功且验证通过 | 完事 |
  | `no_change` | 参数与源码一致 → **一个字节都没改**（`applied: false` 属正常） | 不用做 |
  | `best_effort` | 写回成功，但有字段**没能原位落地**（已退块落地）；`--keep-unverified` 保留时也是它 | 看 `inplace_pending` / `warnings` |
  | `fail` | **已回滚 / 拒绝写回**，原因在 `err`（语法被写坏、重跑不通、没有合规块锚点…） | 读 `err`，修脚本或重调 |
  | `stale` | 参数比脚本旧（存参数之后脚本被手改过）→ **跳过** | 重新 `describe` / 开窗调；确要覆盖手改才用 `--force` |
  | `no_axes` | 参数文件里没有 `axes`（最典型：`describe --all-figs` 的**嵌套**产物直接喂回 `apply`） | 逐图导出（`--fig N`）再逐图 apply |
  | `bad_params` | 参数结构硬错误（`rc=2`），`err` 指出是哪个字段 | 修参数文件 |

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
  仍会被拦下（`reason=stale`）。

## 本 skill 是薄壳

产品逻辑全在 pip 包 **mpltweak** 里（`pip install mpltweak` 就够跑 `describe` / `check` / `apply` / `mcp`；**开窗需要一个图形后端，而 Qt 是可选 extra、不在默认依赖里** —— 没装 Qt 时自动退回 Tk 开窗并在 stderr 提示，想要完整体验加 `pip install "mpltweak[qt]"`）。
本文件只负责"什么时候用、怎么用、别踩什么坑"—— 内核升级不需要动它。
