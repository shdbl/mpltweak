# 参数文件（规范）

> 本文件是 [README](../README.md) 的展开版，内容自 README 搬出；命令、参数、退出码与实测数值与原文一致。

## 参数文件

参数文件是**公开格式** —— 可以手工改，也可以让 AI / agent 直接生成，`--write` 一样能落实。
顶层 `version` 字段标记格式版本（当前为 4；旧版文件照读），工具据此判断跨版本兼容性：

```jsonc
{
  "version": 4,
  "script": "fig1.py",
  "figsize_px": [1500, 700],             // 画布逻辑像素（未 resize 为 null）
  "figsize_in": [15.0, 7.0],             // 写回 figsize 的英寸数
  "axes": [
    {
      "index": 0,                        // = fig.axes 顺序
      "pos": [0.048, 0.655, 0.30, 0.215],  // [x0, y0, w, h]，figure 归一化坐标
      "aspect_locked": false,
      "title_fontsize": 9.0,
      "label_fontsize": 8.0,
      "tick_fontsize": 8.0,
      "tick_fontsize_y": 8.0,            // y 刻度字号（不带这个键则 y 沿用 tick_fontsize）
      "grid": false,
      "spines": { "top": false, "right": false },
      "xscale": "linear",
      "yscale": "linear",
      "is_colorbar": false,
      "clim": [-2.0, 2.0],
      "xlim": [-0.01, 1.01],             // 轴范围：只在脚本**显式固定**过时才记（autoscale 不记）
      "ylim": [0.0, 10.0],
      "cell": [1, 2, 0, 1],              // 网格身份 [行数, 列数, 起始行, 起始列]（手工 add_axes 为 null）
      "cmap": "RdBu_r",
      "lines": [ { "index": 0, "linewidth": 1.2, "color": "#0F4D92" } ],
      "legend": { "loc": "upper right", "fontsize": 8.0 }
    }
  ]
}
```

### 必须遵守的约定

**1. `axes[i]` 的序号 = `fig.axes` 的顺序，不是"代码里 `add_axes` 出现的顺序"。**

- `plt.subplots(2, 2)` / `add_subplot(...)` 建出来的轴**也算数**，按它们在 `fig.axes` 里的先后编号
  （`cell` 字段告诉你它是网格的哪一格 `[行数, 列数, 起始行, 起始列]`）；
- colorbar 的槽轴（`fig.colorbar(...)` 自动建的 `cax`）**剔除**，不占序号；它的位置按设计
  **不支持**原位改写（该项 `is_colorbar: true`）；
- 按"代码里 `add_axes` 的顺序"编号会错位：实测在混用脚本（如 gallery 的 `artists.py`）上写回会对不上
  轴、语义验证失败并回滚（日志 `audit_018/promo/33_index_order.log`）；
- 最稳的用法是**先让工具自己写一份，再只改你要动的字段**：
  `mpltweak describe fig1.py -o .tweak_params/fig1.json`。注意 `-o` 的**目录必须先存在**，否则报
  `FileNotFoundError`（默认路径 `.tweak_params/` 工具已经帮你建好了）。

**2. `figsize` 可以单独改。**

只给画布尺寸、不给任何轴项的参数也能原位改写：

```jsonc
{ "version": 4, "script": "fig1.py", "figsize_in": [10.0, 6.0] }
```

实测：`rc=0`、`style=inplace`、`changes=["figsize"]`，脚本里的 `plt.subplots(figsize=(6, 4))`
被改成 `(10, 6)`（`audit_018/promo/t14_verify.log`）。而 `figsize=(w,h)` 恰好是论文型绘图脚本里
**最常见**的可静态构造目标（只算确实绘图：89.5%）。

**3. `label_fontsize_y`：y 轴标签字号可以单独给。**

参数里带 `label_fontsize_y` 时只改 y 轴标签字号，x 轴沿用 `label_fontsize`；不带则两轴同值
（老参数文件行为不变），`changes` 只报真正变了的那个。字段已进 `mpltweak schema`。
（`describe` 导出时若 y 与 x 同值就不单列这个键 —— 想单独改 y，自己加上即可。）

**4. `describe --all-figs` 的产物是“一套图”，不能直接喂回 `apply --params`。**

`describe --all-figs` 导出的是**嵌套**结构：单图状态在 `figures[i]` 里，顶层没有 `axes`
（顶层键是 `version` / `script` / `n_figs` / `figures`）。直接
`mpltweak apply <脚本> --params all-figs.json --write` 会以 **`rc=1 / reason=no_axes`** 明确失败
——**不静默、脚本一个字节都不改**（`writeback.py` 里有对应的 NO_AXES 早退）。原因是 `apply` 的
`--all-figs` 语义是“循环出图时**统一应用**”，它读的输入仍是**单图**状态。
实测（`audit_018/promo/t24_allfigs.log`）：喂嵌套文件 → `rc=1 / ok=false / reason=no_axes /
applied=false`，脚本 sha256 不变；把同一份单图状态（`describe <脚本> --fig 0 -o one.json`）加
`--all-figs` 喂回则 `rc=0`。要落实多图就**逐图导出、逐图 apply**（`--fig N`）。

**5. `tick_fontsize_y`：y 轴刻度字号也可以单独给。**

与 `label_fontsize_y` 同款：`tick_fontsize` 管 x 刻度、`tick_fontsize_y` 管 y 刻度。源码里本来就有
`tick_params(axis='y', labelsize=…)` 时**各改各的**（原位）；只有一个 `labelsize=`（一次管两边）时两个
字段一起**退到调整块**（块里分两行写 `axis='x'` / `axis='y'`）。不带 `tick_fontsize_y` 时 y 沿用 x
（老参数文件行为不变）。字段已进 `mpltweak schema`。
实测（`audit_018/promo/t32_probe.log`）：`describe` 采到 `tick_fontsize=10.0 / tick_fontsize_y=11.0`；
把 `tick_fontsize_y` 改成 14 → `inplace_changes=["ax0.tick_fontsize_y"]`，源码
`ax.tick_params(axis="y", labelsize=11)` 原位变成 `labelsize=14`（x 那行不动）。

- `plt.subplots(2, 2)` / `add_subplot(...)` 建出来的轴**也算数**，按它们在 `fig.axes` 里的先后编号
  （`cell` 字段告诉你它是网格的哪一格 `[行数, 列数, 起始行, 起始列]`）；
- colorbar 的槽轴（`fig.colorbar(...)` 自动建的 `cax`）**剔除**，不占序号；它的位置按设计
  **不支持**原位改写（该项 `is_colorbar: true`）；
- 按"代码里 `add_axes` 的顺序"编号会错位：实测在混用脚本（如 gallery 的 `artists.py`）上写回会对不上
  轴、语义验证失败并回滚（本次发行前实测）；
- 最稳的用法是**先让工具自己写一份，再只改你要动的字段**：
  `mpltweak describe fig1.py -o .tweak_params/fig1.json`。注意 `-o` 的**目录必须先存在**，否则报
  `FileNotFoundError`（默认路径 `.tweak_params/` 工具已经帮你建好了）。

实测：`rc=0`、`style=inplace`、`changes=["figsize"]`，脚本里的 `plt.subplots(figsize=(6, 4))`
被改成 `(10, 6)`（本次发行前实测）。而 `figsize=(w,h)` 恰好是论文型绘图脚本里
**最常见**的可静态构造目标（只算确实绘图：89.5%）。

`describe --all-figs` 导出的是**嵌套**结构：单图状态在 `figures[i]` 里，顶层没有 `axes`
（顶层键是 `version` / `script` / `n_figs` / `figures`）。直接
`mpltweak apply <脚本> --params all-figs.json --write` 会以 **`rc=1 / reason=no_axes`** 明确失败
——**不静默、脚本一个字节都不改**（`writeback.py` 里有对应的 NO_AXES 早退）。原因是 `apply` 的
`--all-figs` 语义是“循环出图时**统一应用**”，它读的输入仍是**单图**状态。
实测（本次发行前实测）：喂嵌套文件 → `rc=1 / ok=false / reason=no_axes /
applied=false`，脚本 sha256 不变；把同一份单图状态（`describe <脚本> --fig 0 -o one.json`）加
`--all-figs` 喂回则 `rc=0`。要落实多图就**逐图导出、逐图 apply**（`--fig N`）。

与 `label_fontsize_y` 同款：`tick_fontsize` 管 x 刻度、`tick_fontsize_y` 管 y 刻度。源码里本来就有
`tick_params(axis='y', labelsize=…)` 时**各改各的**（原位）；只有一个 `labelsize=`（一次管两边）时两个
字段一起**退到调整块**（块里分两行写 `axis='x'` / `axis='y'`）。不带 `tick_fontsize_y` 时 y 沿用 x
（老参数文件行为不变）。字段已进 `mpltweak schema`。
实测（本次发行前实测）：`describe` 采到 `tick_fontsize=10.0 / tick_fontsize_y=11.0`；
把 `tick_fontsize_y` 改成 14 → `inplace_changes=["ax0.tick_fontsize_y"]`，源码
`ax.tick_params(axis="y", labelsize=11)` 原位变成 `labelsize=14`（x 那行不动）。

---
参数文件的字段含义与两条约定（`axes[i]` 顺序、colorbar 槽轴不占序号）见 [docs/params.md](docs/params.md)。
## `--style` 与 JSON 字段（原文）

> 以下内容原样取自 `README_v2.md`（T80 瘦身时从主 README 搬入，未改一字）。

### `--style` 与 JSON 字段：这次到底落没落地

三种风格现在共用同一个**逐字段三态**判据（①已原位落地 ②与源码现值相等 ③有目标值、与现值不同、
原位写不了）：

| 风格 | 遇到第 ③ 类字段时 |
|---|---|
| `auto`（默认） | 退到插入调整块，把该字段**落地**（不静默丢），并在 JSON 里留档说明原位那一趟为什么退块 |
| `inplace`（显式） | **明确失败**：`rc=1`、`applied=false`，并打印 `[!] 这些字段代码里没有可改的写法、本次**没能落地**：ax0.pos`（不会 `rc=0` 假装都改好） |
| `block` | 一律插块 |

`apply --json` 的 `files[]` 里与此相关的字段（agent 可直接判读）：

| 字段 | 含义 |
|---|---|
| `needs_block` | **这次 apply 之后**是否仍有字段没落地（哪怕这次退过块，只要都落地了就是 `false`） |
| `pending` | 这次仍然没落地的字段清单（如 `["ax0.pos"]`） |
| `inplace_pending` / `inplace_needs_block` | **原位那一趟**为什么退块：原位阶段写不了哪些字段、是否因此退块 |
| `auto_fallback` | 这次是否由 `auto` 自动退到块模式 |
| `inplace_changes` / `inplace_reason` | 原位那一趟真正改到的字段 / 落位结果（`best_effort` = 有字段没能原位落地） |

实测（同一份 `plt.subplots(2, 2)` 脚本、目标 `pos=[0.2, 0.2, 0.6, 0.6]`，源码里没有位置字面量）：

| 命令 | rc | 结果 |
|---|---|---|
| `apply --write --verify-fast --json`（`auto`） | 0 | `style=block / applied=true / auto_fallback=true / inplace_pending=["ax0.pos"]`；回读 `pos=[0.2, 0.2, 0.6, 0.6]`（**真的落地了**） |
| 同上再加 `--style inplace` | 1 | `applied=false / needs_block=true / pending=["ax0.pos"]`，外加上面那句人话失败原因 |
| 同上再加 `--style block` | 0 | 一律插块（即使能原位改） |

### 附：「零侵入」边界的实测证据（从主 README 搬入）

**"零侵入"要说清楚边界**，否则第一次写回会意外：

- **平时的零侵入**：不加 `import`、脚本里不留任何工具痕迹、不生成副文件；开窗只写参数。
- **写回是你主动要求的动作**（`apply --write`），它本来就要改代码 —— 问题在**改多少**：
  开窗导出的是**当时各轴的实测状态**（每个轴的 pos / 字号 / 网格 / spines / 线宽 / 图例 /
  xlim），直通写回会把这一整份**钉进源码**。实测：一个 8 行的 `plt.subplots(2, 2)` 脚本被插进
  30+ 行的块，连"当时恰好是默认值"的那些字段也一起写死（以后改脚本里的默认值就不生效了）。
- **只给"代码里确有写法"的等值参数 = 不改源码**（实测）：只传 `index` + `pos`（可选
  `figsize_in`）且值与原图一致时，`apply --write --verify-fast` 报 `reason=no_change`、
  `changes=[]`、字节不变（本次发行前实测）。
- **代码里没有写法的字段，等值时也不再插块**（退块判据是**逐字段三态**，见「`--style` 与 JSON
  字段」一节）：只有"有目标值 ∧ 与源码现值不同 ∧ 原位写不了"的字段才会退块。实测 `pos` 等值 +
  `title_fontsize: 12.0`（源码里没写 `fontsize=`），以及 `label_fontsize` + `tick_fontsize` /
  `spines` / `grid` / `xscale` / `figsize` 等值 → `reason=no_change`、不插块、`sha256` 不变
  （复现：本次发行前实测）。反向对照（**值确实不同**）仍退块并把值落到图上：
  `title_fontsize: 20.0`、`tick_fontsize: 7.0`、`spines={'top': False}`（同日志）。
- **`describe → apply` 直通依然可能"改代码"**：只要参数里带了**确实不同**、而源码里又写不了的字段，
  它就会插入调整块（那正是"落地"的方式）。所以别把直通写回当成"无需改动"，写回后看一眼 `git diff`。
- **`--verify-fast` 下这条更保守**：它不重跑脚本，证明不了"某字段与源码现值相等"，源码里没有可比写法
  时会退块（详见「给 AI / agent 的排版接口」一节）。
- **只想改你要的字段**：参数写成最小形态
  `{"version": 4, "script": "fig1.py", "axes": [{"index": 0, "pos": [0.07, 0.54, 0.44, 0.40]}]}`。
  只有参数里**显式**给出的字段才会写回（缺 `grid` 键不再被当成 `false` 补上）。
- 写回前先 `mpltweak apply fig1.py`（只读预览，**不会执行**脚本）或 `git diff` 看一眼。

这就是确定性写回的价值：mpltweak 用 AST 精确定位 + 三层验证，**让排版结果真正落进你的代码里，而不是只存在于工具中**。

---

## 参数文件（原文：字段示例）

> 由 T81 从 `README_v2.md` 搬入；搬入时只清掉了营销腔，命令 / 参数 / 字段 / 数字未改。

## 给 AI / agent 的排版接口

大模型能写出正确的绘图代码，但**排版数字基本靠猜**。mpltweak 把这部分从"猜"变成"写文件"：**参数文件是公开 JSON 格式（带 JSON Schema）**，agent 直接生成或修改它，`mpltweak apply --write` 负责落实。

```bash
mpltweak describe fig1.py -o layout.json   # 读：拿到当前排版
mpltweak apply fig1.py --write --json      # 写：落回源码（--json 时过程信息全走 stderr）
mpltweak check fig1.py --json              # 查：排版体检，退出码即 CI 门禁
```

`--json` 给机器读：`files[]` 里的 `style` / `applied` / `changes` / `needs_block` / `pending` 判读这次落没落地；`schema` 子命令输出参数文件的 JSON Schema，供 agent 校验自己生成的内容。

四个子命令的作用表、退出码口径、读 → 改 → 写 → 查回路、以及 MCP 四个工具的参数签名见 [docs/agent.md](docs/agent.md)。

### 也可以接进 AI 客户端（MCP）——**可选适配层**

**能跑 shell 的客户端不需要 MCP**；它面向拿不到 shell 的客户端（如 Claude Desktop）。可选依赖（Python ≥ 3.10）：`pip install "mpltweak[mcp]"`，然后 `mpltweak mcp` 起 stdio server；客户端配置加 `{"mcpServers": {"mpltweak": {"command": "mpltweak", "args": ["mcp"]}}}`。
它暴露 CLI 的子集：`describe_layout` / `check_layout` / `params_schema` / `apply_layout`（`write=False`）；`revert`、`--style`、`--verify-fast`、`--strict` 等只在 CLI 上。四个工具的实际参数签名、`--params` 路径不支持的说明与 best-effort 维护策略见 [docs/agent.md](docs/agent.md)。

### 让 AI 直接读这份手册（skill）

仓库里的 `skills/mpltweak/SKILL.md` 是写给 AI 的操作手册（Agent Skills 格式），含命令、使用铁律与人机协作方式；把它喂给 AI 助手即可，不必每次解释用法。

---

## 原位改数字 / 插入调整块：逐字段三态与零侵入边界（原文）

## 原位改数字，还是插入调整块

以 `fig1.py` 为例。**动手前**（手写的数字，通常都不齐）：

```python
ax1 = fig.add_axes([0.08, 0.66, 0.30, 0.21])
ax2 = fig.add_axes([0.11, 0.38, 0.30, 0.21])
ax3 = fig.add_axes([0.05, 0.08, 0.30, 0.21])
```

**你拖完之后**（`mpltweak apply --write` 的结果）：

```diff
-ax1 = fig.add_axes([0.08, 0.66, 0.30, 0.21])
+ax1 = fig.add_axes([0.08, 0.655, 0.300, 0.215])
-ax2 = fig.add_axes([0.11, 0.38, 0.30, 0.21])
+ax2 = fig.add_axes([0.08, 0.380, 0.300, 0.215])
-ax3 = fig.add_axes([0.05, 0.08, 0.30, 0.21])
+ax3 = fig.add_axes([0.08, 0.085, 0.300, 0.215])
```

**这是第一种情况：代码里写的就是字面量，所以只动了这些数字** —— 没有新增一行、没有加 `import`、没有引入任何依赖。字号、图例位置、网格、colorbar 同理 —— 改的都是你代码里**原本就有的**参数。

如果位置是 `plt.subplots()` / `plt.subplot(1,2,n)` / `GridSpec` 算出来的，**源码里根本没有数字可改** —— 这在真实脚本里很常见。这种图会改成**插入一个自包含的调整块**：

```python
# ===== auto layout (generated) - do not edit inside =====
# 本块由 mpltweak 自动生成于 2026-10-10 16:45（可手动微调，勿删上下两行标记）
# 轴身份（调图时）：ax0=网格[2, 2, 0, 0]，ax1=网格[2, 2, 0, 1]，ax2=网格[2, 2, 1, 0]，ax3=网格[2, 2, 1, 1]
for _ax, _p in zip(fig.axes, [
    [0.0728, 0.5363, 0.4418, 0.4015],
    [0.5394, 0.5363, 0.4418, 0.4015],
    [0.0728, 0.0645, 0.4418, 0.4015],
    [0.5394, 0.0645, 0.4418, 0.4015],
]):
    _ax.set_position(_p)
fig.axes[0].title.set_fontsize(12)
fig.axes[0].grid(False)
# ===== end auto layout =====
```

> 上面那两行 `# ===== auto layout (generated) - do not edit inside =====` … `# ===== end auto layout =====` 是**真的写进你脚本的**，不是示意图；想在自己脚本里找这个块，`grep auto layout` 就能定位。

块是**自包含**的：不加 `import`、不依赖 matplotlib 之外的任何东西、不改动你原有的逻辑。重复写回会**整体替换**它，不会越积越多。两种方式都有三层验证 + 失败自动回滚兜底。

`apply --write` 默认 `--style auto`，**每张图各自决定**：代码里有可改的字面量（`add_axes([...])`、`set_title/set_xlabel(fontsize=)`、`tick_params(labelsize=)`、`grid(...)`）就**原位改那几个数字**；一个字段都改不到（典型：位置来自 `plt.subplots()` / `GridSpec` 的网格轴）就整张图**退到插入调整块**；有的字段能原位改、有的不能（`clim` / `cmap` / `legend` / `spines`、网格轴位置、colorbar 宿主轴位置）走**逐字段三态**判据（①已原位落地 ②与源码现值相等 ③有目标值、与现值不同、原位写不了），存在第 ③ 类字段就退块并把它**落地**、同时进"未原位应用"清单逐条列出，不静默丢。想强制统一：`--style inplace`（一处都不插块；遇到写不了的字段**明确失败** `rc=1`，不假装改好）或 `--style block`（一律插块）。

**"零侵入"的边界**（第一次写回容易踩的点）：

- **平时**：不加 `import`、不在脚本里留工具痕迹、不生成副文件；开窗只写参数文件。
- **写回才会改代码**（`apply --write` 是你主动要求的动作）。`describe` 导出的是**当时各轴的实测状态**，直通写回会把整份状态钉进源码：实测一个 8 行的 `plt.subplots(2, 2)` 脚本被插进 30+ 行块，连"当时恰好等于默认值"的字段也一并写死。
- **想少改就少给**：参数里**没有显式给出**的字段不会写回（缺 `grid` 不再被当成 `false` 补上）；`describe → apply` 直通前先看一眼 `git diff`，或先跑只读预览 `mpltweak apply fig1.py`（**不执行**脚本）。
- **等值也可能插块**：退块判据是逐字段三态（①已原位落地 ②与源码现值相等 ③有目标值但原位写不了），只有第 ③ 类才退块并把值落地，同时进"未原位应用"清单，不静默丢。
- 逐字段细节与这一批的实测日志（等值不插块 / 反向对照 / `--verify-fast` 更保守 / `sys.exit()` 影响）见 [docs/params.md](docs/params.md)。

写回 = AST 精确定位 + 三层验证；改动落在你的源码里。
