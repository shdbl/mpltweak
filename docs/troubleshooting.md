# 卡住了先看这里：三个最常见的问题

新手实际会撞的三件事。每条都是 **现象 → 一句话原因 → 怎么做**，而且三条都能在**不动脚本**的前提下先确认一遍。

> 主 README 只留一行指引；这里逐条展开：拖了没反应 / 拖了弹回去 / 报错怎么办。
> 相关细节：[params.md](params.md)（逐字段判据）、[verify.md](verify.md)（三层验证 / 写回被拒绝 / 跨轮混用）、
> [interaction.md](interaction.md)（开窗相关的「如果没反应」）、[exec-boundaries.md](exec-boundaries.md)（退出码与四类归因）。

## ① 拖了没反应，或者写回说"未写回"

**现象**：窗口里拖了、松开后又回到原样；或者 `apply --write` 跑完脚本没变化，输出里是"未写回 + 原因"（`--json` 里 `applied=false`、`pending` 非空）。
**一句话原因**：这次要改的东西在源码里**找不到可写的目标** —— 典型是位置来自 `plt.subplots()` / `GridSpec`（源码里本来就没有那几个数字），或者目标写成 `fig.axes[i]` / `plt.gca()` 这种"运行时才知道指向谁"的写法。判不出来的字段**宁可拒绝，也不猜着写**。
**怎么做**：

1. 先跑 `mpltweak apply <脚本>`（只读预览：不执行脚本、不写文件），看逐轴清单 —— 能原位改的会列出数字，要插调整块的会说明，写不了的连同原因一起列出来。
2. 被拒绝时代码**一个字节都不动**，并打印一段**可粘贴片段**（也可以显式 `mpltweak apply <脚本> --snippet` 拿这份片段）；把片段里的 `<ax>` / `<fig>` 换成你自己的变量名贴进脚本即可。
3. 想让下次能自动落地：给它一个**名字** —— `fig, ax = plt.subplots(...)` 比 `plt.subplot(...)` 裸调更好定位（片段末尾也会这么提示）。
4. **网格图（`plt.subplots()`）另有一个坑**：`axes[0, 0]` 这类**字面量元素索引**能落地；但 `for ax in axes.ravel()` 或 `ax0, ax1 = axes.ravel()` 这种**解包 / 循环**用法会被 fail-closed 拒绝（宁可不写也不猜）—— 改成对容器直接下标（`axes[0, 0]`）的写法，或手动贴 `--snippet` 给的片段。实测：这两种用法都是 `applied=false`、不写盘（见主 README 的「原位改数字，还是插入调整块」）。

> 完全没弹窗、或开窗报错，是另一回事：先跑 `mpltweak doctor`，逐条见 [interaction.md](interaction.md) 的「如果没反应」。
> 细节：逐字段判据见 [params.md](params.md)，"写回被整体拒绝"的硬约束见 [verify.md](verify.md)。

## ② 拖了又弹回去（位置被布局引擎覆盖）

**现象**：窗口里拖到位了，重开窗 / 重跑脚本又回到原来的位置；`apply --write` 也可能直接报布局引擎冲突。
**一句话原因**：脚本里开着 `constrained_layout=True` 或 `rcParams['figure.autolayout']` —— 这类**布局引擎会在每次绘制时重算轴位置**，你手动排的位置会被它覆盖（所以表现常常是"验证不一致"，而不是位置没改）。
**怎么做**：

1. 调图前先关掉引擎：删掉 `constrained_layout=True`（或改成 `layout=None`，也可以 `fig.set_layout_engine('none')`）；`figure.autolayout` 同理。
2. 或者把要精调的轴改成 `fig.add_axes([...])` 定位 —— **引擎不管这类轴**。
3. 先确认有没有踩到：`mpltweak check <脚本> --static` 把布局引擎冲突**单列一栏**（**带行号**，且**不执行**脚本）；`mpltweak check <脚本> --strict` 可把它从提示升级为问题（`rc=1`），`apply --write --strict` 遇到冲突直接**拒绝写回**（要放行加 `--allow-layout-conflict`）。

> 细节见 [interaction.md](interaction.md) 的「如果没反应」末尾一行、[verify.md](verify.md) 的「接进 CI：`--strict`」。

## ③ 报错 / 失败怎么办：先分清是谁的问题

**现象**：`apply --write` 退出码不是 0、打印回滚，或者报"脚本跑不通 / 语义验证失败"。
**一句话原因**：重跑那一层会把失败**归因**并给一句可照抄的下一步 —— 0.1.10 起，"你的脚本跑不起来"与"工具把代码改坏了"**彻底分开报**，不再共用一句「验证失败，已回滚」。

| 输出里的「归因」 | 是谁的错 | 它给的下一步（可照抄） |
|---|---|---|
| 脚本跑不起来（缺依赖 / 缺数据） | 你的脚本 | `mpltweak check <脚本> --static`（不执行脚本，只做静态检查）——源码没被动过（已回滚） |
| 脚本需要命令行参数 | 你的调用方式 | `mpltweak apply <脚本> --write -- <你的参数...>`（`--` 后的参数原样透传给重跑验证） |
| 无头环境跑不通（脚本要开窗口 / 交互） | 环境限制 | `mpltweak check <脚本> --static`；确要写回用 `--verify-fast` 或 `--keep-unverified` |
| 工具自身异常（internal_error） | **工具的 bug** | 源码一个字节都未修改（已回滚）——请把那段输出报告给我们 |

`--json` 同时给机器可读的 `attribution` / `attribution_detail` / `next_step`（agent 据此自动分支，不必读中文猜）。**退出码数值语义没变**（0.1.9 已文档化，CI 依赖）；判不出原因时保守归"脚本跑不起来"并注明"无法确定原因"，不会甩锅给工具。

**怎么做**：

| 你的情况 | 加什么 | 代价（它不做什么） |
|---|---|---|
| 先把脚本修到能在无头环境跑通（最省事） | —— | 跑不通时**默认不保留**这次写回 —— 这正是那道安全网 |
| 明知无头跑不通，但你确实要保留这次写回 | `--keep-unverified`（自担风险） | `verified: null` + 警告，**要自己跑一遍确认** |
| 慢脚本，想先快速落实、马上自己看图 | `--verify-fast` | 只做语法检查：**不重跑脚本**、不证明布局落到了目标图上（`--json` 里如实标 `verify_mode: "fast"`） |
| 不想执行脚本，先做静态体检 | `mpltweak check <脚本> --static` | 只看代码：量不了真实渲染尺寸，**`--static` 通过不等于图没问题** |

真改坏了也不用慌：**默认已经回滚到批前状态**，备份留在 `.tweak_params/<脚本>.tweak.bak`，`mpltweak revert <脚本> --write` 退回写回前（预览 + 可逆，见主 README 的「后悔了」一节与 [verify.md](verify.md)）。`--no-verify` 跳过的是**重跑与语义验证**（写盘后的语法检查仍会做）：跑不通、布局没落到位都不会被发现，也不会自动回滚 —— 那等于关掉唯一的安全网，不建议。

> 细节：三层验证各查什么、`--json` 的 `layers` 怎么读，见 [verify.md](verify.md)；哪些命令会跑你的脚本、退出码怎么算、四类归因的完整表，见 [exec-boundaries.md](exec-boundaries.md)。
