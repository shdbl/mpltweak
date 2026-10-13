# 参数文件

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
      "tick_fontsize_y": 8.0,            // y 刻度字号（**没写这个键 ⇒ y 一个字节都不动**，T73 零兼容）
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

## `--style` 与 JSON 字段：这次到底落没落地

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

| 命令 | rc | 结果 |
|---|---|---|
| `apply --write --verify-fast --json`（`auto`） | 0 | `style=block / applied=true / auto_fallback=true / inplace_pending=["ax0.pos"]`；回读 `pos=[0.2, 0.2, 0.6, 0.6]`（**真的落地了**） |
| 同上再加 `--style inplace` | 1 | `applied=false / needs_block=true / pending=["ax0.pos"]`，外加上面那句人话失败原因 |
| 同上再加 `--style block` | 0 | 一律插块（即使能原位改） |

实测（同一份 `plt.subplots(2, 2)` 脚本、目标 `pos=[0.2, 0.2, 0.6, 0.6]`，源码里没有位置字面量；
`audit_018/promo/t16_verify.log` —— **本地记录，未随仓库发布**，可重跑入口 `python tests/test_writeback.py`）：

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

位置来自 `plt.subplots` / `plt.subplot(1,2,n)` / `GridSpec` 时，源码里没有数字可改。这种图改成**插入一个自包含的调整块**：

```python
# ===== auto layout (generated) - do not edit inside =====
# 本块由 mpltweak 自动生成于 2026-10-13 16:10（可手动微调，勿删上下两行标记）
# 轴身份（调图时）：ax0=网格[2, 2, 0, 0]，ax1=网格[2, 2, 0, 1]，ax2=网格[2, 2, 1, 0]，ax3=网格[2, 2, 1, 1]
axes[0, 0].set_position([0.0728, 0.5363, 0.4418, 0.4015])
axes[0, 1].set_position([0.5394, 0.5363, 0.4418, 0.4015])
axes[1, 0].set_position([0.0728, 0.0645, 0.4418, 0.4015])
axes[1, 1].set_position([0.5394, 0.0645, 0.4418, 0.4015])
axes[0, 0].title.set_fontsize(12)
axes[0, 1].title.set_fontsize(11)
axes[1, 0].title.set_fontsize(11)
axes[1, 1].title.set_fontsize(11)
# ===== end auto layout =====
```

> 上面这段是**实测真实输出**，不是示意：脚本是 `fig, axes = plt.subplots(2, 2, figsize=(8, 6))`
> （四块面板用**字面量下标**逐条画，如 `axes[0, 0].plot(...)`），命令是
> `mpltweak apply fig1.py --params .tweak_params/fig1.json --write --style block`
> （Agg、无头，零窗口）。生成文本的是 `src/mpltweak/writeback.py` 的 `render_block`
> （`writeback.py:3955`），决定"哪条轴能静态定位"的是 `_block_targets`（`writeback.py:3885`，
> T70 那张**允许/拒绝**的目标表达式表）与 `_axes_binding_entries`（`writeback.py:3614`）；
> "轴身份"注释里恒为中文。
> 你实际拿到的数字与轴数会跟着你的布局变。

**块里的定位必须是"静态可证"的**：`axes[0, 0]` 这种**字面量下标**能证明它指向哪条轴；而
`fig.axes[i]` / `plt.gca()` / `plt.gcf().axes[i]` / `zip(fig.axes, [...])` 属于**运行期才知道指向谁**
的写法（运行期 `fig.axes` 还混着色条轴、嵌入轴与双轴），**已被禁用**：

```python
# 反面例子（**不要这样写**，也不要照它理解块形态 —— 这是 0.1.9 之前的老写法）
for _ax, _p in zip(fig.axes, [...]):
    _ax.set_position(_p)
fig.axes[0].title.set_fontsize(12)
```

证不出"作用在哪张图 / 哪条轴"时工具**拒绝写盘**（`applied=false`、脚本字节不变、`rc=1`，人读输出
"未写回 + 原因"）。实测（本机 2026-10-13）：把上面那张 2×2 图改成用
`for r in range(2): for c in range(2): axes[r, c]...` **循环建轴**之后，块路径直接放弃写盘，
warning 原文是：`块内只能按静态可证的变量名定位目标对象；这些字段证不出目标（……）⇒ 按 fail-closed
处理，本次不写：ax0.pos …`。所以块要落地，容器元素得能被静态认出来（字面量下标 / 明确的变量名）。

上下两行标记是**真写进你脚本的**，`grep auto layout` 就能定位。块是**自包含**的：不加 `import`、不依赖 matplotlib 之外的东西、不改你原有的逻辑；重复写回会**整体替换**它。想只走一条路：`--style inplace`（一处都不插块，写不了的字段 `rc=1` 明确失败）或 `--style block`（一律插块）。

`apply --write` 默认 `--style auto`：**每张图各自决定**，规则如下。

| 这张图的情况 | 结果 |
|---|---|
| 代码里有可改的字面量（`add_axes([...])`、`set_title/set_xlabel(fontsize=)`、`tick_params(labelsize=)`、`grid(...)`） | **原位改那几个数字**，脚本风格不变 |
| 一个字段都改不到（典型：位置来自 `plt.subplots` / `GridSpec` 的网格轴） | 整张图**退到插入调整块** |
| 有的字段能原位改、有的不能（`clim` / `cmap` / `legend` / `spines`、网格轴位置、colorbar 宿主轴位置） | **逐字段三态**：①已原位落地 ②与源码现值相等 ③有目标值、与现值不同、原位写不了。存在第 ③ 类字段 → 整张图**退到插入块**并把它**落地**（改不到的字段同时进"未原位应用"清单逐条列出，不静默丢）；只出现 ①② → `no_change`，一个字节都不动（实测：`pos` 等值 + `title_fontsize` 等值 → 不改源码） |

想强制统一：`--style inplace`（一处都不插块；遇到写不了的字段**明确失败** `rc=1`，不假装改好）或
`--style block`（一律插块）。判据与字段语义见下一节。

**块的插入点有硬约束**：脚本里有 `savefig` / `show` 时，调整块**只允许插在最后一个 `savefig` / `show`
之前** —— 插在它后面等于"你调的布局根本没进图"，而这正是最难发现的一类事故。行内复合语句
（如 `if flag: fig.savefig("o.png")`）会把插入点**提升到那条语句之前**；找不到合规插入点就**拒绝写回**
（`reason=fail`、脚本一个字节都不动），不会再出现"报成功、你的 PNG 却没变"。实测
（`audit_018/promo/t32_probe.log` —— **本地记录，未随仓库发布**，可重跑入口 `python tests/test_writeback.py`）：`if flag: fig.savefig("o.png")` 在第 76 行时，块落在第 9–75 行、
`anchor="before"`、`attempts=[{"anchor": "before", "line": 9, "ok": true, "semantic": true}]`。

**写回还可能被整体拒绝**：写盘后工具会**无条件**做一次 `ast.parse`（原位与块两条路径都做），语法只要
被写坏就**当场回滚**；重跑失败时会进一步判别"我们写坏了"还是"脚本本来就跑不通"（缺数据 / 依赖 /
需要 GUI）——**后者默认也回滚 + 明确失败**。详见「三层验证」。

**跨轮可能混用**：先原位改过数字，后来某轮因为"一个字段都没落地"退成了块，文件里就会同时
存在两种形态。两者都有效，但读代码的人容易困惑。要统一，先从备份恢复（
`.tweak_params/<脚本>.tweak.bak` 是写回前的原文），再用 `--style block` 写一次——
块是整体替换的，单靠再写一次并不会把之前原位改掉的数字改回去。

## 必须遵守的约定

**1. `axes[i]` 的序号 = `fig.axes` 的顺序，不是"代码里 `add_axes` 出现的顺序"。**

- `plt.subplots(2, 2)` / `add_subplot(...)` 建出来的轴**也算数**，按它们在 `fig.axes` 里的先后编号
  （`cell` 字段告诉你它是网格的哪一格 `[行数, 列数, 起始行, 起始列]`）；
- colorbar 的槽轴（`fig.colorbar(...)` 自动建的 `cax`）**剔除**，不占序号；它的位置按设计
  **不支持**原位改写（该项 `is_colorbar: true`）；
- 按"代码里 `add_axes` 的顺序"编号会错位：实测在混用脚本（如 gallery 的 `artists.py`）上写回会对不上
  轴、语义验证失败并回滚（日志 `audit_018/promo/33_index_order.log` —— **本地记录，未随仓库发布**，可重跑入口 `python tests/test_writeback.py`）；
- 最稳的用法是**先让工具自己写一份，再只改你要动的字段**：
  `mpltweak describe fig1.py -o .tweak_params/fig1.json`。注意 `-o` 的**目录必须先存在**，否则报
  `FileNotFoundError`（默认路径 `.tweak_params/` 工具已经帮你建好了）。

**2. `figsize` 可以单独改。**

只给画布尺寸、不给任何轴项的参数也能原位改写：

```jsonc
{ "version": 4, "script": "fig1.py", "figsize_in": [10.0, 6.0] }
```

实测：`rc=0`、`style=inplace`、`changes=["figsize"]`，脚本里的 `plt.subplots(figsize=(6, 4))`
被改成 `(10, 6)`（`audit_018/promo/t14_verify.log` —— **本地记录，未随仓库发布**，可重跑入口 `python tests/test_writeback.py`）。而 `figsize=(w,h)` 恰好是论文型绘图脚本里
**最常见**的可静态构造目标（只算确实绘图：89.5%）。

**3. `label_fontsize_y`：y 轴标签字号可以单独给。**

参数里带 `label_fontsize_y` 时只改 y 轴标签字号（x 由 `label_fontsize` 决定）；**没写 `label_fontsize_y` 时 y 一个字节都不动**（T73 零兼容，见 CHANGELOG「修复 · y 侧字号」），`changes` 只报真正变了的那个。字段已进 `mpltweak schema`。
（`describe` 导出时若 y 与 x 同值就不单列这个键 —— 想单独改 y，自己加上即可。）

**4. `describe --all-figs` 的产物是“一套图”，不能直接喂回 `apply --params`。**

`describe --all-figs` 导出的是**嵌套**结构：单图状态在 `figures[i]` 里，顶层没有 `axes`
（顶层键是 `version` / `script` / `n_figs` / `figures`）。直接
`mpltweak apply <脚本> --params all-figs.json --write` 会以 **`rc=1 / reason=no_axes`** 明确失败
——**不静默、脚本一个字节都不改**（`writeback.py` 里有对应的 NO_AXES 早退）。原因是 `apply` 的
`--all-figs` 语义是“循环出图时**统一应用**”，它读的输入仍是**单图**状态。
实测（`audit_018/promo/t24_allfigs.log` —— **本地记录，未随仓库发布**，可重跑入口 `python tests/test_agent_api.py`）：喂嵌套文件 → `rc=1 / ok=false / reason=no_axes /
applied=false`，脚本 sha256 不变；把同一份单图状态（`describe <脚本> --fig 0 -o one.json`）加
`--all-figs` 喂回则 `rc=0`。要落实多图就**逐图导出、逐图 apply**（`--fig N`）。

**5. `tick_fontsize_y`：y 轴刻度字号也可以单独给。**

与 `label_fontsize_y` 同款：`tick_fontsize` 管 x 刻度、`tick_fontsize_y` 管 y 刻度。源码里本来就有
`tick_params(axis='y', labelsize=…)` 时**各改各的**（原位）；只有一个 `labelsize=`（一次管两边）时两个
字段一起**退到调整块**（块里分两行写 `axis='x'` / `axis='y'`）。**没写 `tick_fontsize_y` 时 y 一个字节都不动**（T73 零兼容）。字段已进 `mpltweak schema`。
实测（`audit_018/promo/t32_probe.log` —— **本地记录，未随仓库发布**，可重跑入口 `python tests/test_agent_api.py`）：`describe` 采到 `tick_fontsize=10.0 / tick_fontsize_y=11.0`；
把 `tick_fontsize_y` 改成 14 → `inplace_changes=["ax0.tick_fontsize_y"]`，源码
`ax.tick_params(axis="y", labelsize=11)` 原位变成 `labelsize=14`（x 那行不动）。

---

## 附：「零侵入」边界的实测证据

**"零侵入"要说清楚边界**，否则第一次写回会意外：

- **平时的零侵入**：不加 `import`、脚本里不留任何工具痕迹、不生成副文件；开窗只写参数。
- **写回是你主动要求的动作**（`apply --write`），它本来就要改代码 —— 问题在**改多少**：
  开窗导出的是**当时各轴的实测状态**（每个轴的 pos / 字号 / 网格 / spines / 线宽 / 图例 /
  xlim），直通写回会把这一整份**钉进源码**。实测：一个 8 行的 `plt.subplots(2, 2)` 脚本被插进
  30+ 行的块，连"当时恰好是默认值"的那些字段也一起写死（以后改脚本里的默认值就不生效了）。
- **只给"代码里确有写法"的等值参数 = 不改源码**（实测）：只传 `index` + `pos`（可选
  `figsize_in`）且值与原图一致时，`apply --write --verify-fast` 报 `reason=no_change`、
  `changes=[]`、字节不变（实测）。
- **代码里没有写法的字段，等值时也不再插块**（退块判据是**逐字段三态**，见「`--style` 与 JSON
  字段」一节）：只有"有目标值 ∧ 与源码现值不同 ∧ 原位写不了"的字段才会退块。实测 `pos` 等值 +
  `title_fontsize: 12.0`（源码里没写 `fontsize=`），以及 `label_fontsize` + `tick_fontsize` /
  `spines` / `grid` / `xscale` / `figsize` 等值 → `reason=no_change`、不插块、`sha256` 不变
  （复现：实测）。反向对照（**值确实不同**）仍退块并把值落到图上：
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
