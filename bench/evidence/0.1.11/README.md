# `bench/evidence/0.1.11` —— 本轮**新增**两个数字的可复核材料

## 0. 这份材料为什么存在

2026-10-14 用户问了一句：

> README 里「每个数字怎么查：原始产物…都在 bench 分支的证据包 tag `evidence-0.1.10`」——
> **这里，这一版相关的证据传上去了吗？**

核查后结论是 **没有** ✗：

* 本地与远端**只有** `evidence-0.1.10`，**没有** `evidence-0.1.11`；
* 本轮新加的两行 —— **随机语法 1,000 例** 与 **命令行组合 5,880 种** —— 在 README 里
  **连证据链接都没有**，数字当时**只活在团队任务记录**里。

⇒ 这两行的数字**只能信、不能查**。本目录即整改结果。

> **这是增量包**：只装这两行。另外三行仍指向 `evidence-0.1.10`（那个包未改、仍可点）。
> **一个包不覆盖全部** —— 详见 `COVERAGE.md`。

## 1. 目录

```
bench/evidence/0.1.11/
├── README.md              本文件（人读索引）
├── COVERAGE.md            覆盖范围：本包管哪两行、另外三行去哪查
├── CLAIMS.json            机器可读：每个数字 → 产物 + 命令 + 可复现等级 + 源码溯源
├── MANIFEST.json          每个文件的 sha256（取自**发布树**，防发布后被改动）
├── ast_fuzz/              随机语法（1,000 例 = 4 片 × 250 并行）
│   ├── shard_0_seeds000-249.log   片0 原始输出（seed   0..249）
│   ├── shard_1_seeds250-499.log   片1 原始输出（seed 250..499）
│   ├── shard_2_seeds500-749.log   片2 原始输出（seed 500..749）
│   ├── shard_3_seeds750-999.log   片3 原始输出（seed 750..999）
│   ├── ASSERTIONS.md              **断言表**：每个 `0` 对应哪条判据、防的是哪种失败
│   ├── ast_fuzz_gen.py            生成器（自造 CFG，只用 random.Random(seed)）
│   ├── test_ast_fuzz.py           驱动器 + 断言（--offset 分片 / --corpus / 环境变量改 N）
│   └── corpus/                    13 条**实测钉住**的可复现样本（seed 即最小化样本）
└── cli_fuzz/              命令行组合（分母 5,880 = 7×10×6×14）
    ├── run_N240.log               `N=240` 那次运行的原始输出
    ├── DENOMINATOR.md             分母怎么来的 + 分层抽样口径 + 五条断言
    └── test_cli_fuzz.py           驱动器（枚举分母 / 抽样 / 断言 / teeth）
```

## 2. 每个数字去哪查

| 数字 | 产物 | 复现命令 | 可复现等级 |
|---|---|---|---|
| 随机语法 **1,000 例**（4×250）全通过 | `ast_fuzz/shard_*.log` | `& $py tests/test_ast_fuzz.py 250 --offset {0,250,500,750}` | `rerun-from-repo` |
| 三条不变量 **零违约**（承诺 0 / 第四种 0 / 不幂等 0…） | `ast_fuzz/ASSERTIONS.md` + 分片日志 | 同上 | `rerun-from-repo` |
| 形态覆盖 **9/9**、格子 **18/18**、corpus **13/13** | `ast_fuzz/shard_*.log`、`ast_fuzz/corpus/` | `& $py tests/test_ast_fuzz.py 36` | `rerun-from-repo` |
| 命令行组合分母 **5,880** | `cli_fuzz/DENOMINATOR.md` | `& $py tests/test_cli_fuzz.py` | `rerun-from-repo` |
| 命令行组合 **N=240 实测全通过** | `cli_fuzz/run_N240.log` | `& $py tests/test_cli_fuzz.py 240` | `rerun-from-repo` |
| 耗时（**不是**可复现量） | 各日志的 `耗时` 行 | — | `machine-dependent` |

> 耗时的两种实测值：本包 **1.46~1.48 s/例**（片 366.1 / 369.9 / 366.7 / 371.2 s）、
> 命令行 **0.18 s/组合**；同一批命令在机器负载更重时实测过 **3.45~3.52 s/例**。
> **两个数都是实测**，差别来自负载 ⇒ 只发布"实测值 + 测量环境"，不声称是常量。

## 3. 怎么自己复算

```powershell
# 随机语法：先跑最小全覆盖（36 例 ≈ 1 分钟），再决定要不要上 1,000
& $py tests/test_ast_fuzz.py 36
& $py tests/test_ast_fuzz.py --corpus            # 只跑 13 条钉住的样本
& $py tests/test_ast_fuzz.py 250 --offset 0      # 发布期分片

# 命令行组合：CI smoke（秒级）→ 发布期规模
& $py tests/test_cli_fuzz.py
& $py tests/test_cli_fuzz.py 240
```

两个套件的**输入样本都在临时目录里确定性重建**（正常/坏语法/目录/只读/超长路径；
坏 JSON / 缺 version / 顶层非对象…），**不依赖任何外部语料** ⇒ 第三方开箱可重跑。

## 4. 本包**不**包含什么（如实交代）

1. **1,000 例的逐例 JSON**（每例约 500 B）—— 分片日志已含每例判定明细与汇总，
   逐例文件只是同一信息的分片，发布它们只会造成噪声；
2. **本地语料与任何用户脚本正文**（沿用 0.1.10 的脱敏口径：只公开清单与判定）；
3. **把耗时当常量**（见上：`machine-dependent`）；
4. 命令行组合的**分母已全量枚举**，但**实跑是抽样**（本包给的是 `N=240` 那一次）。

## 5. 树指纹（本包产出时的树）

见 `CLAIMS.json` 的 `_tree_fingerprint` 与 `_meta/tree_fingerprint.txt`：
`HEAD=a00604f`、`M=3 / ??=0`。
⇒ 这两个套件跑的是**工作区**：**开发期的红可能来自别人的半成品**，
判据是**先看指纹**（不是换基线）；**权威用法 = 发布期在冻结树上跑**。
