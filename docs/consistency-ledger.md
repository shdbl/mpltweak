# 跨读者一致性：口径台账（0.1.11）

> 用途：**接缝**类缺陷（两条路径各自都对、合起来错）的常驻检查依据。
> 配套测试：`tests/test_check.py` §17（跨读者几何/画布一致性矩阵）。
> 读者代号：**R1** `toolbox._bbox(ax)`（窗口/交互侧）· **R2** `verify._state(fig)`（describe/verify 采集侧）
> · **R3** `check._pos(item)`（check 侧）· **R4** `toolbox._canvas_geometry(fig, fig_px)`（窗口侧画布）
> · **R5** `writeback.count_figures(tree)`（静态 AST 侧）。

## 1. 台账

| 量 | 权威（以谁为准） | 读者 | 容差 | 依据 |
|---|---|---|---|---|
| 轴 rect（普通轴） | **源码声明的 rect** = `ax._position`（`get_position(original=True)` 同值） | R1 / R2 / R3 | **5e-5** | R1 全精度；R2/R3 把 rect **四舍五入到 4 位小数**（量子 1e-4 ⇒ 半量子 5e-5）。这是**刻意**的既有口径，不是 bug |
| **colorbar rect** | **源码声明值** `ax.get_position(original=True)` | R1 / R2 | **5e-5** | colorbar 的 `_ColorbarAxesLocator` **每次重绘**改写 active position（实测宽度 `0.1162 → 0.0289`）⇒ 任何读者若读 active，就会与源码差一个数量级，`describe → apply` 会凭空改用户没动过的 colorbar。**R1 对 colorbar 走 original 口径，R2 复用 R1** |
| 画布英寸 `figsize_in` | **`fig.get_size_inches()`**（与 dpi / DPR 无关） | R4 / R2 | **5e-3** | 窗口侧导出保留 **2 位小数**（量子 1e-2 ⇒ 半量子 5e-3）；R2 保留 4 位。刻意 |
| 画布像素 `figsize_px` | 窗口侧 = **自己跟踪的逻辑 px**（从未 resize 过 ⇒ `null`，参数规范允许）；采集侧 = **live canvas px** | R4 / R2 | **1 px** | 两者**不是同一个量**：R4 为 `null` 时 R2 的 px 必须等于 `英寸 × fig.dpi`（兜底口径）。实测：`figsize=(8.3333,4)` ⇒ R2 px `[833,400]`、R4 英寸 `[8.33,4.0]` |
| 图数量 | **运行时真实张数** | R5（静态 AST） / R2（运行时） | 0（必须相等） | 静态口径必须 `figure_only=True`：`fig.subplots(...)` **不算**新图（按图数量口径收口）。反例：不带该参数 ⇒ `fig.subplots` 形状报 2 张、运行时 1 张 |
| polar × colorbar 重叠（接缝②） | **读者数据里的 rect** | R1 / R2 | 重叠量 > 0 | 报告现场：极地图 rect `[0.039, 0.4651, 0.9224, 0.4349]` + colorbar rect `[0.33, 0.455, 0.34, 0.016]` ⇒ **y 重叠 0.0059、x 重叠 0.2754**，两个读者的数据里都看得见。`check` 看不见的原因是它把 **aspect_locked 轴排除在几何比对之外**（§16 的刻意设计），**不是数据缺失** |

## 2. 读者清单（含"抽不动 / 必须开窗"的如实列出）

| 读者 | 离屏可调用？ | 说明 |
|---|---|---|
| R1 `toolbox._bbox(ax)` | ✅ | 纯函数，只读 `ax` |
| R2 `verify._state(fig)` | ✅ | 纯函数；**其 pos 用的就是 R1 的函数**（同源 ⇒ 结构上不可能"各算一遍"） |
| R3 `check._pos(item)` | ✅ | **只消费 R2 的 pos**，check 侧**没有独立几何读者**（所以它只能与 R2 一样对） |
| R4 `toolbox._canvas_geometry(fig, fig_px)` | ✅（0.1.11 从 `Tweak.export` 里**不改行为地抽出**） | 画布英寸/像素的唯一权威口径 |
| R5 `writeback.count_figures(tree)` | ✅ | 纯 AST |
| 窗口跟踪的**逻辑 px**（`Tweak._fig_px`） | ❌ **必须开窗** | 它是交互期状态（用户拖过窗口才有值；离屏恒为 `None`）⇒ 矩阵只检查"为 None 时的兜底口径" |
| 真实 **DPR**（`Tweak._canvas_dpr()`） | ❌ **必须开窗** | 读的是 Qt/Tk canvas 的 device pixel ratio。离屏只能用**注入**模拟（见 §3），并在测试里写明"模拟 ≠ 真机" |
| `Tweak.export(...)` 的其余部分（选择态、拖拽预览…） | ❌ 必须开窗 | 不是几何/画布读者；几何部分已抽成 R4 |

## 3. 已知的"模拟 ≠ 真机"边界

- **Qt HiDPI 把 `fig.dpi` 乘 DPR**：离屏没有真 Qt canvas，DPR 只能**注入**。注入依据是仓库自己的注释
  `toolbox.py:1923-1934 # _fit_size_for`（物理请求尺寸 = 英寸 × `fig.dpi`；逻辑画布 px = 物理 ÷ DPR；HiDPI 下 matplotlib 把
  `fig.dpi` 乘过 DPR ⇒ `fig.dpi = rcParams['figure.dpi'] × DPR`，而逻辑 px = 英寸 × `rcParams['figure.dpi']`）。
  ⇒ 旧的 `px / fig.dpi` 少算一个 DPR 因子：**DPR=2 时画布正好缩到 1/2**（用户现场 7.5×4.75）。
  矩阵（`tests/test_writeback.py` §103）证的是"**换算公式在 DPR≠1 时必然错**"，**不是**"Qt 真的这么做"；
  真机那一步**待真窗口实测**（授权后补：开窗后断言 `fig.dpi == rcParams.dpi × DPR` 且逻辑 px = 英寸 × rcParams.dpi）。
- **colorbar 的 locator 时机**：矩阵在**重绘之后**读（最坏时刻）⇒ 任何"读 active"的读者都会当场露馅。

## 4. teeth（单点变异 ⇒ 矩阵必红）

| 变异 | 期望红 | 实测 |
|---|---|---|
| R1 改回 `ax._position`（active） | §17②b（colorbar rect） | colorbar rect 与源码声明值差 **0.0874** > 5e-5 ⇒ 红 |
| R4 的英寸改回 `px/100`（历史写法） | §17③（画布） | 英寸 (6,4) × dpi=200 ⇒ px (1200,800) ⇒ 历史写法算出 12 英寸，与 R2 差 **6.000** > 5e-3 ⇒ 红 |
| R5 改回「`fig.subplots` 也算新图」的旧口径 | §17④（图数量） | `fig.subplots` 形状报 **2** 张、运行时 **1** 张 ⇒ 红 |
| 把 aspect_locked 轴从读者数据里去掉 | §17⑤（接缝②） | 重叠在两个读者的数据里消失 ⇒ 红（check 的 §16 排除是**判定层**，不是数据层） |

## 5. 边界声明（"对外只讲能证明什么"）

- 本台账与 §17 矩阵证明的是：**同一物理量在各读者之间一致**（含 colorbar rect 与源码声明值一致、画布英寸/像素同源、图数量静态与运行时一致、接缝②的重叠在数据层可见）。
- **不**证明：Qt 真机的 DPR 行为（待真窗口）；`check` 的**判定规则**是否正确（那是 `check` 规则自己的验收，见 §16）；窗口交互态（选择/拖拽）的正确性。
