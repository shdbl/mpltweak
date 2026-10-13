# 0.1.10 · 写回验证「两遍合并为一遍」的等价性证据

> 归档目录：`bench/evidence/0.1.10/onepass/`（本文 + 原始记录）
> 对应声明：`CLAIMS.json` 的 **`onepass-equivalence`**（layer=runtime，reproducibility=needs-corpus）
> 关联变更：写回验证的 happy path 由「原样重跑 1 遍（只看退出码）+ 套壳重跑 1 遍（取图状态做逐字段回比）」
> 合并为**默认只跑 1 遍**：这一次同时回答"脚本还跑得通吗"与"改动真的落到目标图上了吗"，
> 并且**磁盘上那张导出的图片也必须体现这次改动**。

## 0 这份材料能证明什么 / 不能证明什么

**能证明（实测）**

1. **等价性**：在同一批 144 份真实脚本上，修复前的"套壳跑"与"原样跑"在**产出文件**上的一致率是
   **143/144**；修复后（去掉套壳对 `plt.close` 的拦截）升到 **144/144**。也就是说：
   套壳跑与"原样跑"的**唯一**系统性产出差异来自那一处拦截，去掉后两者在产出上等价。
2. **根因可定位且可复现**（5 臂追因实验，双向证明）：把"拦截 `plt.close`"这一处
   （a）从套壳里**去掉** → 与"原样跑"的产物**逐字节、逐像素相同**；
   （b）**只**加到"原样跑"上 → 复现出套壳那套错误产物。
   两边的目标图大小 / sha256 / 像素差都记录在案（见 §2 第 6 行）。
3. **不夸大的一致性代价**：修复后退出码语义一致率 136/144 → 135/144，其中**唯一**的变化份
   是脚本自身的非确定性（§2 第 3 行 + `pong_rc_stability.log`：同一份脚本原样跑 24 次，
   rc 两种取值都出现）；其余指标没有下降。
4. **性能收益**（同一批 24 份可比脚本）：两遍合计 38.1 s → 一遍合计 19.6 s，**省 48.7%**；
   24/24 份都命中了单遍路径（0 份需要回退两遍）。

**不能证明 / 不在本材料范围**

- 不能外推到**全量语料**：本材料是 150 份抽样（三类来源：matplotlib gallery / zenodo / github），
  不是全量 4,433 份候选池或 1,837 份真跑池。样本口径见 `before/SUMMARY.md` §0。
- 不证明**性能数字与机器无关**：38.1 s / 19.6 s 是本机同一批脚本的墙钟对照（§2 第 7 行），
  换机器/换负载会变；可复核的是**方法与倍数关系**（同一批脚本、同一时段、同一解释器）。
- 不证明**判据严格性**：本材料回答"等价不 equivalence"，不回答"三层验证判得对不对"。
  后者由仓库内的回归网覆盖（`tests/test_writeback.py`、`tests/test_verify_onepass.py`），不在本归档内。
- 不证明**已发布语料数字**（池 1,837 / pass 928 / 违约 0）：那是另一套运行器（判源码字节四律）
  的结论，本材料只涉及"验证重跑写出的图片"这一条通道。

## 1 建议的归档布局

```
bench/evidence/0.1.10/onepass/
├── README.md                     ← 本文（本轮唯一新撰写的文件）
├── before/                       ← 修复前（阶段 1）原始记录，11 个文件里选 6 个
│   ├── STAGE1_FINDINGS.md        结论与逐条归因（含 5 臂追因的文字版）
│   ├── SUMMARY.md                机器生成的汇总（四项一致率 / 判定分布 / 逐条分类）
│   ├── results.jsonl            150 份逐份原始记录（核心可复核材料）
│   ├── RUN_META.json            跑前门禁留档（A 档防弹窗筛命中 0 + 样本清单）
│   ├── CLOSE_PROBE_cfd75bbb.json 5 臂追因的原始数据
│   ├── NONDET.jsonl             噪声地板（A vs A′）原始记录
│   └── close_probe_key_values.txt ← 本轮新增：上面 JSON 的 35 行关键值摘录（便于人工核对）
├── after/                        ← 修复后（阶段 2）原始记录
│   ├── results_FINAL.jsonl      150 份逐份原始记录（144/144 的直接来源）
│   ├── SUMMARY_FINAL.md         同一判定实现重算出的汇总（四项一致率）
│   └── NONDET.jsonl             噪声地板同轮重跑（与修复前逐位相同）
├── perf_final.jsonl             性能对照逐份数据（38.1 s → 19.6 s）
└── pong_rc_stability.log        "退出码 -1"的归因实测（空载 12 次 vs 施压 12 次）
```

## 2 数字表（每个数字都能落到文件与行）

| # | 指标 | 修复前 | 修复后 | 出处（行号均为该文件内行号） |
|---|---|---|---|---|
| 1 | 记录数 / 统计分母 | 150 / **144**（6 份不适用：3 超时 + 3 需 stdin） | 同 | `before/SUMMARY.md` L72、`after/SUMMARY_FINAL.md` L72 |
| 2 | 产出文件一致（存在/大小/sha256） | **143** = 99.3% | **144** = 100.0% | `before/SUMMARY.md` L107 · `after/SUMMARY_FINAL.md` L107 |
| 3 | 退出码语义一致（两侧同为跑通/跑不通） | 136 = 94.4% | 135 = 93.8% | `before/SUMMARY.md` L104 · `after/SUMMARY_FINAL.md` L104 |
| 4 | stdout+stderr 一致（抹掉已知套壳噪声） | 123 = 85.4% | 124 = 86.1% | `before/SUMMARY.md` L106 · `after/SUMMARY_FINAL.md` L106 |
| 5 | 判定分布 | `equivalent` 63 / `equivalent_fail` 51 / `diff_wrapper_nostate` 8 / **`diff_artifacts` 1** / `diff_unknown` 21 | 63 / 53 / 8 / **0** / 20 | `before/SUMMARY.md` §2 表 · `after/SUMMARY_FINAL.md` §2 表 |
| 6 | 追因 5 臂（目标图大小 / sha256 前 8 / 像素差） | A 原样 185,701 B `6a7acb15` 0.0% · B 套壳 140,667 B `252597fc` **15.2121%** · B′ 套壳去掉拦 close = **与 A 完全相同** · 「原样 + 只加拦 close」= **与 B 完全相同** | — | `before/CLOSE_PROBE_cfd75bbb.json`（键 `target_sizes` / `target_sha` / `pixel_vs_A` / `arms`）＋ `before/close_probe_key_values.txt` 全文 |
| 7 | 性能：两遍 vs 一遍（24 份可比脚本） | 38.1 s（单份均值 1.59 s） | **19.6 s（0.82 s）= −48.7%**，24/24 命中单遍，0 份回退 | `perf_final.jsonl`（27 行，`passes=1` 的 24 行求和） |
| 8 | 噪声地板 A vs A′ | **31/40 = 77.5%** | 31/40 = 77.5%（逐位相同） | `before/SUMMARY.md` L416 · `before/NONDET.jsonl` · `after/NONDET.jsonl` |
| 9 | "退出码 -1"的归因 | — | 同一脚本原样跑 24 次，rc 取值集合 `{0, 1}`（空载 12 次含 2 个 1；施压 12 次含 1 个 1） | `pong_rc_stability.log` |
| 10 | 语料只读自证 | 逐份 sha256 前后对账 **0 份不一致** | 同（脚本 sha256 与 `results.jsonl` 里记录的 `corpus_sha256` 一致） | `before/SUMMARY.md` §6 · `pong_rc_stability.log` 头部对账行 |

第 5 行的 `diff_artifacts` 那 1 份就是第 6 行的追因对象；修复后它变成
`equivalent`（可在 `after/results_FINAL.jsonl` 里按该路径检索到 `artifacts_same: true`）。

## 3 建议发布的原始文件清单

「正文」列 = 是否含**第三方脚本源码正文**（本套材料全部为 0，见 §6）；
「本机路径」列 = 含盘符路径 / 用户名的**出现次数**（脱敏前实测；发布前必须替换成占位符）。

| 文件 | 为什么要它 | 正文 | 本机路径 | 备注 |
|---|---|---|---|---|
| `before/STAGE1_FINDINGS.md` | 修复前的结论与逐条归因（含"为什么两遍"与风险描述） | 无 | **0** | 已干净，可直接放 |
| `before/SUMMARY.md` | 机器生成的四项一致率 + 判定分布 + 逐条分类（口径 §0 齐全） | 无 | 17 | 脱敏：§0 的候选池/语料根/解释器三条路径 |
| `before/results.jsonl` | **核心**：150 份逐份原始记录（两臂 rc/耗时/输出片段 + `cmp` 逐项） | 无 | 135（含 6 处用户名） | 脱敏：两臂 traceback 片段里的工作目录与解释器路径 |
| `before/RUN_META.json` | 证明"跑前做了 A 档防弹窗静态筛、样本内命中 0"，并登记池 sha256 与样本清单 | 无 | 3 | 脱敏：候选池 / 语料根 / 解释器 |
| `before/CLOSE_PROBE_cfd75bbb.json` | 追因 5 臂原始数据（第 6 行的全部数值） | 无 | 7 | 脱敏：各臂工作目录 |
| `before/NONDET.jsonl` | 噪声地板原始记录（第 8 行，解释"不一致不能全记在套壳头上"） | 无 | 22 | 脱敏：工作目录 |
| `before/close_probe_key_values.txt` | 本轮新增：把第 6 行的关键值摘成 35 行，便于人工核对（无需读 62 KB JSON） | 无 | **0** | 已干净 |
| `after/results_FINAL.jsonl` | **核心**：修复后 150 份逐份原始记录（144/144 的直接来源） | 无 | 167（含 12 处用户名） | 脱敏同上；provenance 见 §4 注 |
| `after/SUMMARY_FINAL.md` | 修复后汇总（同一判定实现重算） | 无 | 16 | 脱敏：§0 三条路径 |
| `after/NONDET.jsonl` | 修复后地板重跑（与修复前逐位相同 = 地板没被改动影响） | 无 | 22 | 脱敏：工作目录 |
| `perf_final.jsonl` | 第 7 行的唯一来源（逐份两遍/一遍耗时） | 无 | **0** | 已干净，可直接放 |
| `pong_rc_stability.log` | 第 9 行的唯一来源（rc 抖动实测，含语料 sha 对账） | 无 | **0** | 已干净 |

**脱敏口径**（与已入档的 `corpus/` 证据一致：那边 6 个文件实测 0 处盘符路径 / 0 处用户名）：
把**以盘符开头的绝对路径**统一替换成占位符（例如 `<work>` / `<corpus>` / `<python>`），
把**家目录路径里的用户名段**替换成 `<user>`。**替换后请重跑 §6 的自查命令**。

## 4 不建议发布的文件

| 文件 | 理由 |
|---|---|
| `results_ROUND2_partial_superseded.jsonl`（+ 同名 `.README.txt`） | 一轮**被取代**的部分结果（文件名与正式结果同形，最容易被误引）；本机路径 139 处、用户名 32 处，脱敏收益低 |
| `results_UNSCREENED_ROUND1_VOID.jsonl`（+ 同名 `.README.txt`） | **未过**跑前静态门的无效轮次（样本口径不同，混进来会污染"144 分母"的可追溯性） |
| `NONDET_round2_smoke.jsonl` | 冒烟小样，与 `NONDET.jsonl` 口径不同，同目录并存会让人误用 |
| 性能对照的失败趟（本轮 `perf.jsonl`） | 那一趟因缺 `PYTHONPATH` 导致两臂都走了回退路径（24/24 `passes=2`），数字无意义；只发布 `perf_final.jsonl` |
| 阶段 1 结果的副本（`results_stage1_prev.jsonl`） | 与 `before/results.jsonl` 内容重复，徒增歧义 |
| 内部阶段报告（本轮的 `t39_STAGE2_REPORT.md`） | 内部过程记录（含"第一版判据偏松"等自我修正历史与实现细节），不属于对外证据；对外只需 §0–§3 的口径说明 |

> **`after/results_FINAL.jsonl` 的 provenance（如实说明）**：它是从一个**文件被追加写坏**的
> 结果文件里**尾部 150 行**截出来的（当时先起了一趟对照、中途终止，又在同一路径起了一趟完整对照）。
> 校验方式：尾部 150 条路径互不重复、头部 123 条路径**全部**落在尾部集合内（脚本可复核）。
> 若希望归档里只放"单趟一文件"的原始记录，可用 §5 的命令重跑一趟替换它 —— 数字预期一致
> （同 seed、同池、同判定实现）。

## 5 第三方如何自己重跑

前置条件（缺一不可，全部在 main 仓库里随 0.1.10 发布）：

1. `tools/cmp_plain_vs_instrumented.py`（本对照的生成器）、`tools/probe_close_vs_wrapper.py`
   （5 臂追因）、`tools/probe_why_nostate.py`（"取不到状态"追因）。
2. 候选池 `filter.jsonl`（t7 的静态筛选结果，含 `path`/`source`/`plot`/`both_ok` 等字段）
   与**语料根**（真实脚本按 `path` 相对语料根存放）。两者都在仓库外，需自备：
   - 池文件的 sha256 见 `before/SUMMARY.md` §0（用来确认你用的是同一份池）；
   - 语料是公开来源（matplotlib gallery / zenodo / github）的脚本集合。
3. 一个装了 matplotlib 的 CPython 3.10 环境（本材料用 3.10 跑的）；
   **不需要**网络、**不需要** GUI：工具全程 `MPLBACKEND=Agg`、零窗口。

命令（占位符按你自己的路径替换）：

```bash
# 修复前（对照的基准）：在 0.1.10 之前的代码上跑
python tools/cmp_plain_vs_instrumented.py \
    --corpus <corpus_root> --pool <pool.jsonl> --python <python.exe> \
    --sample 120 --per-stratum 40 --jobs 2 --timeout 60 \
    --out before/results.jsonl --summary before/SUMMARY.md

# 修复后（0.1.10）：同一条命令，换输出目录
python tools/cmp_plain_vs_instrumented.py \
    --corpus <corpus_root> --pool <pool.jsonl> --python <python.exe> \
    --sample 120 --per-stratum 40 --jobs 2 --timeout 60 \
    --out after/results.jsonl --summary after/SUMMARY.md

# 噪声地板（同一路原样跑两次）：对任一 results.jsonl 跑 A vs A′
python tools/cmp_plain_vs_instrumented.py \
    --corpus <corpus_root> --nondet before/results.jsonl --nondet-control 10 \
    --out before/results.jsonl

# 只重算判定 / 只出汇总（不重跑脚本）
python tools/cmp_plain_vs_instrumented.py --reclassify after/results.jsonl \
    --summary after/SUMMARY.md
```

抽样是**确定序**的（固定 seed、按来源分层），所以"同一份池 + 同一份语料"重跑会得到同一批 150 份；
跑前有 A 档防弹窗静态筛（命中即拒绝开跑，样本内命中数记录在 `before/RUN_META.json`）。
语料只读：脚本一律复制到临时工作区再跑，逐份 sha256 前后对账。

**性能数字怎么复核**：性能不是这条命令产出的，而是"同一批脚本分别跑两遍验证与一遍验证"的
墙钟对照，逐份数据在 `perf_final.jsonl`（字段：`t_two` = 旧两遍合计、`t_one` = 新单遍、
`passes` = 实际跑了几遍、`basis` = 状态取自图对象/写盘快照）。第三方可用同样的方式自测，
但**不要**期望与本机秒数完全一致（见 §0 第 2 条）。

## 6 口径边界：哪些是实测、哪些是推导、哪些与本机相关

| 类别 | 条目 |
|---|---|
| **本次实测** | §2 全部 10 行（分母 144 口径、143→144、136→135 并归因、噪声地板 77.5%、5 臂追因的目标图大小/sha256/像素差、性能 38.1→19.6 s 与 24/24 单遍、rc 抖动 24 次观测、语料 sha 对账） |
| **由机制推导（未在本材料里逐份实测）** | ① "去掉拦 close 之后，套壳跑与原地跑在**任意**脚本上等价"——本材料只在 144 份样本上实测了一致性，不能外推为全量保证；② "回退两遍的份数在更大语料上仍是少数"——同属外推（阶段 1 的估计是 ≈9/144，见 `before/STAGE1_FINDINGS.md` §7.3，那是**预期**不是实测） |
| **与本机相关（换环境会变）** | 墙钟秒数（38.1 / 19.6 s）、单份耗时、`pong` 那份的 rc 抖动频率、`.svg/.pdf/.eps` 时间戳豁免的具体份数、`frame_number` 警告的行号漂移 |

另外两条边界，避免误读：

- **"一致率"的分母是 144，不是 150**：6 份不适用（3 份超时、3 份需要 stdin）已从分母剔除，
  报告里没有任何指标偷用 150（`before/SUMMARY.md` L86 的分母声明）。
- **噪声地板 77.5% 意味着"同一路原样跑两次就有 22.5% 不一致"**：所以"修复前后一致率的小幅变化"
  （退出码 136→135、抹噪声 123→124）**不能**单独解读为工具行为变化，必须对照地板
  （机制上：地板属于"原样跑"那一路，本变更不触碰它 ⇒ 重跑实测逐位相同，见 §2 第 8 行）。

## 7 自查结果与已知缺口（如实报告）

**自查已通过**（本文撰写后实测）：

```
python bench/tools/check_evidence_publishable.py --root bench/evidence/0.1.10
→ 扫描文件 16 个；代码串提示 2 处（不计失败）
→ OK：未发现本机绝对路径 / 用户名 / 脚本正文；必备文件齐全。   (exit 0)
```

- 本文自身实测 0 处盘符路径 / 0 处用户名（188 行、16 KB）。
- 根目录的 `README.md` 与 `CLAIMS.json` 已存在，且已把本目录登记为
  `onepass/`（根 README 第 45、63 行；`CLAIMS.json` 的 `onepass-equivalence` 声明）。
  两者引用的数字（143/144 → 144/144、38.1 s → 19.6 s）与本 README §2 逐项一致（已交叉核对）。
- **尚未做完的一步**：§3 清单里的原始记录还没复制进 `onepass/`（按分工由船长统一复制 + 脱敏）。
  所以上面那次自查扫到的是**已有的** `corpus/`、`sandbox/`、`window/` 与本文，
  **不含** §3 的 12 个文件；复制完成后需要**重跑**同一条自查命令。
- §3 里标了"本机路径 > 0"的文件（`before/results.jsonl` 135 处、`after/results_FINAL.jsonl` 167 处等）
  在脱敏前**一定**过不了这条红线 —— 这是预期内的，脱敏口径见 §3 末尾。
- 若要"第三方完全不依赖仓库外材料"地重跑，仍需自备语料池与语料本身（公开来源，
  但**不进归档**：语料正文与本机路径都是红线）。
