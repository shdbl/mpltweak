# `bench/evidence/0.1.10` —— 运行时层与交互层的可复核材料

## 0. 这份材料为什么存在

2026-10-13 有人对项目提出一条批评，核查后**成立**：

> README 主表里最显眼的两行 ——「**1,780 份真跑 / 928 通过**」与「**13,000 次离屏 + 6,000 次真窗口手势 / 27 条断言**」——
> 只给了日志行号（`NUMBERS.md:28`、`run_qt9.log:79`），而这些日志**没有随仓库发布**。
> 也就是说：这两行**只能信、不能查**。

核查结论：证据当时确实全在仓库外（`_review\` 与 `_tmp_t16_real\` 等目录），而离屏矩阵那轮日志原先是写在被 `.gitignore` 忽略的临时目录里、**已被清理**。
对照之下，**静态层**（语料研究）一直有正确做法：材料在 `bench` 分支，且早有一条提交把不可达的日志出处改成「本地构建日志，未随仓库发布」。**两层政策不一致**，批评正好戳在这条缝上。

本目录即整改结果：把运行时层与交互层的**原始产出、复现命令、源码溯源、完整性哈希**一起入库，并配上两道工具：

| 工具 | 作用 |
|---|---|
| `bench/tools/sanitize_evidence.py` | 发布前脱敏（定点替换本机绝对路径 / 用户名；改完逐行复验 JSON 未损坏） |
| `bench/tools/check_evidence_publishable.py` | 发布红线守卫（绝对路径 / 用户名 / 第三方脚本正文 / MANIFEST 哈希） |

## 1. 目录

```
bench/evidence/0.1.10/
├── README.md            本文件（人读索引）
├── CLAIMS.json          机器可读：每个数字 → 产物 + 命令 + 可复现等级 + 源码溯源
├── MANIFEST.json        每个文件的 sha256（防发布后被改动）
├── corpus/              语料级真跑（1,837 份）
│   ├── results.jsonl      逐脚本判定（1,837 行；含四律结果、原因、sha256）
│   ├── SUMMARY.md         分层分母表 + 四律统计 + src 溯源（§9）+ 边界说明（§0b）
│   ├── RUN_META.json      池指纹（pool_sha256）与语料规模
│   ├── RUN_OBSERVATIONS.json
│   ├── filter.jsonl       候选池定义（4,433 行：依赖齐 + 有可定位目标）
│   └── FILTER.md          静态筛选方法与计数
├── window/              真窗口交互（6 种图形 × 1,000 次）
│   ├── NUMBERS.md         数字表（每行带日志行号）
│   ├── run_qt9.log        Qt 轮（含第 6 行源码状态）
│   ├── run_tk7.log        Tk 轮
│   ├── selftest_t22.log   断言机器自检（35/35 变异被抓）
│   ├── ledger_drylist.txt 场景 / 断言 / 台账
│   └── tk_huge_transcript.log
├── matrix/              离屏图形矩阵（12 图 × 1,000 次）
│   ├── matrix_utf8.log / matrix_gbk.log   套件自写日志（UTF-8）
│   └── NUMBERS_matrix.md                  数字表（每行带日志行号）
├── onepass/             写回验证「2 遍 → 1 遍」的等价性证明
└── sandbox/             四层沙箱及其边界（SANDBOX.md）
```

> 说明：语料跑还会生成 **1,837 个逐脚本小文件**（每份约 500 B）。它们**不在此发布**——聚合的 `corpus/results.jsonl` 已包含每份的判定与 sha256，单文件只是同一信息的分片，发布它们只会造成噪声。

## 2. 每个数字去哪查

| 数字 | 去哪查 | 复现命令 | 第三方能复现吗 |
|---|---|---|---|
| 静态层 10,158 / 4,427 / 3,531（97.8%） | `bench/manifest.jsonl`、`bench/verdicts/scan_full.jsonl`（本目录的**上一级**） | 见 `bench/README.md` 第 3 节 | 需语料（清单含 url + sha256） |
| 池 **1,837** | `corpus/RUN_META.json`（含 `pool_sha256`）、`corpus/filter.jsonl` | `python tools/corpus_filter_t7.py --jobs 3` | 需语料 |
| 候选 **1,780** | `corpus/SUMMARY.md` | `python tests/test_corpus_invariants.py --pilot 48` | 需语料 |
| **928 通过 / 四律 928×928 / 违约 0** | `corpus/results.jsonl`（逐脚本）、`corpus/SUMMARY.md` | `python tests/test_corpus_invariants.py --resume` | 需语料 |
| 未判定 **852**（开窗 317 / 等人输入 464 / 超时 71） | 同上 | 同上 | 需语料 |
| 离屏 **12,000 / 实跑 13,000**、断言 356/356 | `matrix/matrix_utf8.log`、`matrix/NUMBERS_matrix.md` | `set MPLTWEAK_MATRIX_N=1000` + `set MPLBACKEND=Agg` + `python tests/test_figure_matrix.py` | **可以**（测试已在仓库） |
| 真窗口 **6,000 次手势 / 27 条硬断言** | `window/NUMBERS.md`、`window/run_qt9.log`、`window/run_tk7.log` | `python tests/test_real_window.py --backend qt --log …`（需图形界面） | **可以**（需要有窗口的机器） |
| 断言不是空断言（35/35） | `window/selftest_t22.log` | `python tests/test_real_window.py --selftest` | **可以**（免弹窗） |
| 单遍化 **143/144 → 144/144**、**38.1 s → 19.6 s** | `onepass/` | `python tools/cmp_plain_vs_instrumented.py` | 需语料（样本 144 份） |

**每条数字的源码溯源写在各日志自己打印的「源码状态」行里**（例如 `window/run_qt9.log` 第 6 行：`toolbox.py=58d6125152 writeback.py=88297ccf71 apply.py=ea6bae7111 verify.py=5b54f60f62 ｜ git HEAD=02998e6`；语料轮见 `corpus/SUMMARY.md` §9，含 9 个文件的完整 md5）。`CLAIMS.json` 把这些溯源做成了机器可读字段。

## 3. 第三方怎么自己复现

分三级，**我们说清楚哪一级是哪一级**：

1. **`rerun-from-repo`（有仓库就行）**：离屏矩阵、真窗口交互、沙箱、单遍化探针。测试代码都在仓库里，命令见上表。
   - 注意：**墙钟耗时随机器变化**，我们只发布实测值与测量环境，不声称它是常量。
2. **`needs-corpus`（需要那份本地语料）**：语料级真跑与静态研究。第三方脚本**不随仓库分发**，但：
   - `corpus/filter.jsonl` 逐份给出 `source`（来源层）与路径；
   - `bench/manifest.jsonl`（上一级）给出 `url` 与 `sha256`，可按来源自行抓回、用 sha256 校验是否同一份。
3. **与本机相关（不可复现为常量）**：耗时、并发表现、真窗口轮的屏幕/缩放环境。判据用的是**相对量**（比值、偏差、极差），不是绝对像素——这也写在 `CLAIMS.json._not_independently_reproducible` 里。

**独立抽查建议**：从 `corpus/filter.jsonl` 随机取 50 份（`both_ok=true`），按 `corpus/SUMMARY.md` 的命令跑，再与 `corpus/results.jsonl` 同路径的记录逐条对照。若结果不一致，那就是我们该修的——请开 issue。

## 4. 脱敏（红线）

发布前对本目录**逐文件**做过定点替换（规则与计数全在 `bench/tools/sanitize_evidence.py` 里，可自行复核）：

- 抹掉内容：本机盘符路径（工作根 / 语料根 / 沙箱根 / 临时区 / 解释器路径 / 家目录与用户名）。
- 替换为语义占位符：`<work>`、`<corpus_root>`、`<sandbox_root>`、`<evidence>`、`<tmp>`、`<python>`、`<home>`。
- **不发布**：任何第三方脚本正文。本目录只含**判定结果**（路径 + sha256 + 结论 + 原因），不含脚本内容。
- 改写 `results.jsonl` / `filter.jsonl` 后**逐行 `json.loads` 复验**，确保替换没有弄坏 JSON。

自查（任何改动后都应重跑；退出码 0 才算过）：

```bash
python bench/tools/check_evidence_publishable.py --root bench/evidence/0.1.10
```

## 5. 我们**不能**独立复现的部分（如实声明）

- **墙钟耗时**（离屏 6.9–7.8 min、真窗口 15–24 min、语料几十 min）：随机器负载变化，只作参考量。
- **语料快照**：上游页面可能变动或消失；我们用 url + sha256 固定"当时那一份"，但无法保证你今天抓到的还是同一份。
- **真窗口的屏幕环境**：本机 960×540 可用区、dpr=4、dpi=200、`QT_SCALE_FACTOR=2`；别的机器读数会有差异。
- **沙箱的边界**（见 `sandbox/SANDBOX.md`）：真跑链路里 `argv[0]` 落在 harness 侧 ⇒ 只有**文件层**生效；socket / subprocess 层靠**静态预筛**兜住（含联网或起子进程调用的脚本一律不真跑）。更精细的判定规则列入 0.1.11。
