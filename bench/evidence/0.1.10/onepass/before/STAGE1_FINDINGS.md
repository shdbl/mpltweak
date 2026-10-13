# t39 阶段 1 结论（原样跑 vs 套壳跑 等价性）

> 数据：`results.jsonl`（150 份，判定实现/归一化 `norm_version=3`）、`SUMMARY.md`、`NONDET.jsonl`（A vs A′ 噪声地板）、`RUN_META.json`（门禁留档）、`CLOSE_PROBE_cfd75bbb.json`（追因 5 臂）。
> 工具：`mpltweak/tools/cmp_plain_vs_instrumented.py`、`mpltweak/tools/probe_close_vs_wrapper.py`、`mpltweak/tools/probe_why_nostate.py`。
> 纪律：语料只读（复制后跑；逐份 sha256 前后对账 = **0 份不一致**）；跑前 A 档防弹窗静态筛，**样本内命中 = 0**（非 0 则 rc=2 拒跑）；全程**零窗口**；未 commit。

## 1 样本与分母（可追溯）

- 池 `_review/corpus_filter_t7/filter.jsonl` 4,433 行；**过 A 档静态筛 4,065 份**
  （排除 `window_risk_A` 123 / `dynamic_code_unknown` 146 / `syntax_unparseable` 99）。
- 抽样三步（全是保底/上限）：类别钉选 6 类×≤6 = **36 次 pick → 去重 30 份**；
  分层新增 **40×3 = 120 份**；补足 **0 份**。⇒ **150 = 30 + 120**。
  分层：gallery 40+19=**59**、zenodo 40+11=**51**、github 40+0=**40**。
- **分母 144**（= 150 − 6 不适用：3 `na_timeout` + 3 `na_skip(stdin)`）。**所有一致率分母都是 144。**

## 2 一致率（分母 144）

| 项 | 一致 | 占比 |
|---|---:|---:|
| 退出码语义（同为跑通 / 同为跑不通） | 136 | **94.4%** |
| stdout+stderr（严格，合流后多重集） | 16 | 11.1% |
| stdout+stderr（抹掉已知套壳噪声后） | 123 | **85.4%** |
| 产出文件（存在/大小/sha256，含非确定性格式豁免） | 143 | **99.3%** |

判定分布：`equivalent` 63 / `equivalent_fail` 51 / `diff_wrapper_nostate` 8 / `diff_artifacts` 1 / `diff_unknown` 21 / 不适用 6。

**已知套壳噪声（不计入不一致）**：`frame_number_drift` 73（同一警告仅"源码位置行号"漂移——套壳多包一层 runpy）、`wrapper_err_channel` 50（两侧都跑不通；A 直吐完整 traceback，B 收成一行 `[verify] ...`）。产出文件非确定性格式豁免：`.svg` 6 / `.pdf` 4 / `.eps` 1（matplotlib 往这些格式写时间戳与随机 id；PNG 不带时间戳，仍逐字节严格判等）。

## 3 噪声地板（关键对照）

**A vs A′（同一路"原样跑"跑两次）一致率 = 77.5%（31/40）。**

⇒ **"A vs B 不一致"不能全记在套壳头上**：21 份 `diff_unknown` 里 **9 份在 A vs A′ 同样不一致**
（环境/非确定性：未固定随机数、墙上时钟、joblib 资源追踪竞态等），**12 份**才是"只有套壳那次不同"。

## 4 12 份残余差异的逐条根因（全部已解释）

| 类 | 份数 | 路径 | 根因 | 归谁 |
|---|---:|---|---|---|
| **R1 argparse / `sys.argv` 泄漏** | 3 | `018/zenodo/18820771__CreateEmbeddingVisuals7.py`、`big/zenodo/21387252__ppmi_entropy_analysis.py`、`big/zenodo/22089837__cascade_shelf_analysis_CANONICAL_scaffold.py` | 套壳自己的两个位置参数（`<out.json>`、`0`）**留在用户脚本的 `sys.argv[1:]` 里**：A → `error: the following arguments are required: csv_file`；B → `error: unrecognized arguments: <WORK>\state.json 0` | **套壳造成（真缺陷）** |
| **R2 joblib `resource_tracker` 警告只在 B 出现** | 3 | `018/gallery/scikitlearn/model_selection/plot_learning_curve.py`、`.../plot_multi_metric_evaluation.py`、`big/github/statsmodels/archive/examples/ex_multivar_kde.py` | 进程收尾/atexit 顺序 + 套壳改道 fd1→fd2 后 joblib 的 shutdown 消息变得可见 | **套壳造成（不影响图）** |
| **R3 语料目录遮蔽标准库** | 1 | `018/gallery/shapes_and_collections/hatch_demo.py` | 该目录里真有一个 `collections.py`（gallery 示例），脚本目录进 `sys.path` 后遮蔽标准库；套壳用 runpy/importlib，对 `collections` 依赖更深 ⇒ 报错更早（`Could not import runpy module`） | **环境造成 + 套壳放大**（两侧都跑不通） |
| **R4 错误诊断通道（A 有 traceback / B 收成一行）** | 5 | `018/zenodo/23142271__make_figure_7.py`、`big/zenodo/10278919__gradient_consensus_demo.py`、`statsmodels/archive/examples/example_crossval.py`、`l1_demo/sklearn_compare.py`、`ex_multivar_kde.py`（与 R2 重叠） | 两侧都跑不通；A 直接打脚本自己的失败诊断（含非 `XxxError:` 形式的自打印与异常消息续行），B 吞成一行 `[verify] …` | **套壳造成（通道设计）** |
| 环境/非确定性 | 1 | `big/github/statsmodels/examples/python/tsa_arma_0.py` | statsmodels 未固定种子的估计输出 + 警告里的数值 | 证据强度中等（A vs A′ 该例恰好一致，单次采样不能证伪） |

（R2/R4 有 1 份重叠，故合计 12。）

## 5 追因实验：`plt.close` 被空操作化 = 静默改坏用户图片（**已双向定位**）

脚本 `018/zenodo/18910861__generate_all_figures.py`（固定种子：`np.random.seed(42)` @L90 / `(123)` @L111 / `(7)` @L345）。
目标图 `figures_supplementary/ige_dynamics/2WQR_rg.png`（1920×1440）：

| 臂 | 做法 | 大小 | sha256 前 12 | 与 A 不同像素 | 与 A 不同文件 |
|---|---|---:|---|---:|---:|
| A | 原样跑 | 185701 | `6a7acb15d52f` | 0 | 0/41 |
| A′ | 原样第二遍 | 185701 | `6a7acb15d52f` | 0 | **0/41** |
| B | 套壳跑 | 140667 | `252597fc6170` | **15.2121%** | **28/41** |
| B′ | 套壳**去掉**"拦 close" | 185701 | `6a7acb15d52f` | **0** | **0/41** |
| B″ | **原样 + 只加**"拦 close" | 140667 | `252597fc6170` | **15.2121%** | **28/41** |

`B′ == A`（去掉它差异全消失）与 `B″ == B`（只加它差异完整复现）构成**双向证明**：根因**唯一** =
`src/mpltweak/verify.py:719`（`_collect_session` 内；`:804`/`:844` 同口径）把 `plt.close = lambda *a, **k: None` 空操作化。

**savefig 追踪（只观测）实测**：A 的 41 次 savefig **每次 `fig_number=1`**；B″ 的 `fig_number` 一路涨到 **13**，**40/41 次记录不同**——保存的那张图上还留着上一张图的 artist（`pgl_sasa.png` 1→**14** 条线；`S14_pca_plot.png` 1 轴 1 散点 → **4** 轴 6 散点）。源码可见成因：`L429 plt.figure()` → `L436 savefig` → `L437 plt.close()`（被拦后图仍活着且仍是 current）→ `L442 plt.plot(...)` **没有** `plt.figure()`。

**用户可见风险**：真实链路是 `verify_run`（原样，写出正确图）→ `verify_semantic`（套壳，close 被拦，写出错误图），两次 `cwd` 都在脚本目录 ⇒ **后者覆盖前者** ⇒ `apply --write` 成功后用户目录里的图可能被**静默改坏**。本样本命中 1/144（≈0.7%，需"多图 + savefig 后 close + 再裸 plot"）。

## 6 已发布数字是否受影响（**核实结论：不受影响**，附代码依据）

`tests/test_corpus_invariants.py`（t4 / t23 用的同一个运行器）：
- `:796-801` 注释与实现："拷贝到隔离 workdir（语料目录本身只读，绝不写入）" —— 脚本字节写到 `os.path.join(workdir, basename)`；
- `:292-298` apply CLI 以 `cwd=cwd`（= 该隔离 workdir）调用；
- `:1222` `wd = os.path.join(workroot, 's_%03d' % seq)` —— **每份脚本一个独立 workdir**；`:1334` 结束即 `rmtree`。

⇒ 两遍重跑的 `cwd` 都是 `<workroot>/s_NNN/`，**被改坏的图片落在临时目录里并随 run 删除**，既没碰到语料、也不涉及用户目录；且四律判的是**源码字节**。**池 1,837 / pass 928 / 违约 0 不受图片覆盖影响。**

## 7 对"写回只跑一遍"的判定口径

### 7.1 单遍成立的条件（可判定）
1. 脚本能跑完（含 `SystemExit(0)`）；**`sys.argv` 必须被摆成恰好 `[script] + script_args`**（当前只在 `script_args` 非空时才摆 ⇒ 见 R1）；
2. 目标图**能取到状态**，取法二选一：
   - (a) 目标图在 `savefig` 时刻被快照（钩 `Figure.savefig`，**写盘那一刻**）；或
   - (b) 目标图在脚本结束时仍存活（注册表持有强引用，**即使脚本自己 close 过也能读**）。
3. 目标图必须能**定位**：`fig_index` 用"figure 创建顺序注册表"，与 `describe`（close 被拦 ⇒ Gcf 顺序 == 创建顺序）**语义一致**。

### 7.2 取不到状态时的行为（fail-closed）
回退到**现有两遍路径**（`verify_run` 原样跑 + `verify_semantic` 套壳跑），并**打印原因**；
**绝不允许**静默判"通过"。且**图片永远以原样跑的产出为准**（套壳那一遍不得覆盖；实现上要么不拦 close、要么把套壳跑挪到与用户产物隔离的目录）。

### 7.3 预期比例（映射自阶段 1 的 144 分母）

| 桶 | 份数 | 单遍路径 |
|---|---:|---|
| `equivalent` 63 | 63 | **可单遍**（跑通 + 状态可比对 + 产出一致） |
| `equivalent_fail` 51 | 51 | **可单遍**（两侧都跑不通 ⇒ 退出码已判定；注意其中大部分是"缺依赖/缺子目录数据"⇒ 本样本里**信息量低**） |
| `diff_wrapper_nostate` 8 | 8 | **必须回退**（现状取不到状态）——**其中相当一部分正是 savefig 钩子/注册表能救回的**（见 §7.1-2b）；救不回的那些（脚本模块级根本不建图，如 `22796824__Folds.py`）**永久回退** |
| `diff_artifacts` 1 | 1 | **现状必须回退**；去掉"拦 close"后预期转为**可单遍**（见 §5） |
| `diff_unknown` 21 | 9 环境/非确定性 + 12 已解释 | 判"跑通/跑不通"不受影响（136/144 一致）；其中 **R1 的 3 份必须修 `sys.argv`** 才可单遍 |
| 不适用 6 | — | 与单遍无关 |

**换算成"必须回退两遍"的保守估计：≈9/144（6.3%）= 8 `diff_wrapper_nostate` + 1 `diff_artifacts`**，
另加 R1 的 3 份在修好 `sys.argv` 前也不可单遍。**其余 ≈94% 可单遍**——但这是**设计预期**，
必须在阶段 2 用 5 臂/144 份实测确认后才是**实测结论**。

### 7.4 可写进文档的表述
> **已实测（阶段 1）**：在 144 份真实脚本上，"原样跑"与"套壳跑"的**退出码语义 94.4% 一致、产出文件 99.3% 一致**；
> 其中不一致的部分已逐条定位，**没有一条是"改动没生效"或"图状态对不上"**。
> **设计预期（待阶段 2 实测）**：修完后**默认只跑 1 遍**；仅当"目标图既没被 `savefig`、也无法在结束时读到"
> （本样本 ≈6%）才**回退到 2 遍**并**打印原因**；失败归因时最多 3 遍（与现状同）。
> 用户会看到"跑了不止一遍"的场合：① 目标图取不到状态（回退，有提示）；② 验证失败做基线归因（原样重跑）。

### 7.5 剩余风险（单遍路径下仍可能改动用户产物的通道）
| 通道 | 有无 | 依据 |
|---|---|---|
| `plt.close` 被拦 | **有（现状，必须去掉）** | §5 实测：`B′==A`、`B″==B` 双向证明 |
| `savefig` 钩子本身 | **无**（设计上只读） | 包一层记录状态后**原样调用真 `savefig`**；须用 `functools.wraps` 保留签名，并保证异常时不影响返回值 |
| `plt.show()` 被拦 | 有（**保留**） | Agg 下本是 no-op；保留是为"零窗口"纪律。实测噪声 = `W_SHOW_SILENCED`（A 多一行 Agg 警告），**已归入已知套壳噪声** |
| `frame_number` / 行号文本 | 有（**已知噪声**） | `frame_number_drift` 73 份：只是警告的源码位置行号漂移，不影响判据 |
| `rcParams` 改道 / `sys.path` / `sys.modules` | 有（**必须有**） | 长驻进程（MCP）不回收会跨界污染；`_snapshot_process_state` 已在 `finally` 还原 |
| 子目录数据未复制（**本探针的限制，非产品问题**） | — | `_seed_dir` 只复制同级文件、不复制子目录 ⇒ 需要 `sub/data.csv` 的脚本在 A/B 两侧**同样**失败（`equivalent_fail` 51 份里占多数，**信息量低**）。产品路径跑在脚本自己的目录里，无此问题 |

## 8 阶段 2 的最小落地清单（尚未开工）

1. **删三处 close 拦截**：`src/mpltweak/verify.py:719`、`:804`、`:844`。
2. **钩 `Figure.savefig`** 在写盘那一刻抓快照 + **figure 创建顺序注册表**（`plt.figure` 包一层，注册表持强引用 ⇒ 脚本 close 过也能读）。
3. **`sys.argv` 无条件摆正**：`sys.argv = [abspath(script)] + list(script_args or [])`（修 R1）。
4. **`writeback.py` 两条路径**（inplace `:2982-3056`、block `:4743-4804`）改为"先走单遍"，取不到状态时**打印原因并回退两遍**；`layers` 三态语义与 `--verify-fast` / `--no-verify` / `--keep-unverified` 保持不变。
5. **回归测试**（`tests/`，船长已授权）：构造"多 figure + `savefig` → `plt.close()` → 不再 `plt.figure()` 就 `plot`"的脚本，断言 `apply --write` 后**用户目录里的图片与原样运行逐字节一致**。
6. **重跑 144 份对照表**：`diff_artifacts` 预期 1 → 0，`diff_wrapper_nostate` 应下降。
