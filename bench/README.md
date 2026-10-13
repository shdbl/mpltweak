# `bench` —— 「真实绘图代码语料验证」的可复核材料

这个分支只放**方法、清单与判定结果**，用来复核 README 里那张表（10,158 份脚本 / 97.8% / 承诺违约 0）。
**语料本体（脚本正文）不在这里、也不随仓库分发** —— 见「5. 脱敏」。

> 本文件覆盖的是**静态层**（不执行脚本）。
> **运行时层**（语料级真跑）与**交互层**（离屏矩阵 / 真窗口）的原始产出、复现命令与源码溯源在
> [`evidence/0.1.10/`](evidence/0.1.10/README.md) —— 见本文件第 6 节。

## 1. 语料怎么来的

| 来源 | 份数（`manifest.jsonl` 实测） |
|---|---:|
| Zenodo 数据集记录（论文补充材料里的 `.py`） | **6,904** |
| GitHub 科研绘图仓库（61 个仓库名，如 `github-sunpy` / `github-statsmodels` / `github-metpy` …） | **2,045** |
| 官方 / 项目自带 gallery（matplotlib-gallery、scikit-image、scikit-learn、pyart …） | **1,209** |
| **合计** | **10,158** |

筛选与去重口径：

- **只要 `.py`**，并按 **sha256 唯一化**：同一份脚本在多处出现只留一份。合并阶段原始记录 **10,168** 份，
  sha256 唯一化后 **10,158** 份（丢 10 份完全相同的重复）。本地构建日志（`corpus_union/_logs/build_summary.json`，未随仓库发布）：
  `copied=10168`、`unique_sha=10158`、`dup_groups` 10 组 × 2、`sha_mismatch=0 / size_mismatch=0 /
  undecodable=0`。
- 更早一次跨阶段合并（`corpus_big` 阶段的本地日志 `corpus_big/_logs/merge_summary.json`，同样未随仓库发布）另有
  `dup_cross_stage = 1469` 条命中同一 sha 的记录被跳过（同一脚本在 GitHub / Zenodo 两个阶段都抓到）。
- **许可与 URL 逐份记录**在 `manifest.jsonl`；**语料本体保存在本地只读，不再分发**。
- 本次是**静态研究**：不执行语料脚本，只读源码做定位 / 改写判定。

## 2. 目录

```
manifest.jsonl            10,158 行：path / source / url / license / sha256 / size（+ corpus / plot 标志）
verdicts/scan_full.jsonl   4,427 行：逐脚本判定（**已脱敏**；覆盖 README 主表的全部 4,427 个输入脚本）
verdicts/README.md        字段字典（每个判定字段的含义）+ 脱敏白名单
tools/_cu_static_table.py 主表（原位写回 10 族 + 通过率 + 承诺违约）生成脚本
tools/_cu_why_not100.py   逐族「为什么没到 100%」分类脚本（A–E 五类）
tools/sanitize_verdicts.py 判定文件脱敏脚本（白名单，可重跑）
tools/check_no_corpus_text.py 脱敏自检脚本（扫语料正文特征，应为 0）
harness/                  脚手架脚本（提取 / 静态扫描 / 字段族实验 / 分母口径 / 排障）
```

## 3. 怎么复算两张表

```bash
# 主表：每族 = 有站点 / 可达 / 真原位 / 退块 / 拒绝 / 承诺违约 / 通过率
python tools/_cu_static_table.py

# 逐族「为什么没到 100%」：A 真原位 · B 改块落地 · C 证不出而拒绝 · D 参数非法被拒 · E 其它未落地
python tools/_cu_why_not100.py verdicts/scan_full.jsonl

# 脱敏自检（应全部为 0）
python tools/check_no_corpus_text.py .
```

两个工具的默认输入已指向本分支的 `verdicts/`。

补扫已并入主文件：`scan_rest.jsonl` 不再单独发布（同一批最终判定的补扫部分已合并进
`scan_full.jsonl`）；两个复算工具读不到 `scan_rest.jsonl` 时会自动跳过，输出即为全量。

## 4. 判定的可信度边界

- **判定数据与源码版本**：`verdicts/scan_full.jsonl` 的逐脚本判定来自 **0.1.9 数字口径冻结提交
  `e28a608`** 之后的最终静态扫描（4,427 份输入全覆盖），真原位 **3,531 / 3,610 = 97.8%**、承诺违约 **0**，
  与 README 主表同一批数据；更早的 t69 扫描（宽松口径 3,534 / tick 88 = 96.7%）已被
  「T73：没写 y 侧键就不动 y」修复后的重新扫描取代，不再对外发布。
- **只做静态判定**：不执行脚本，所以「能跑通」这类运行期结论不在本次范围内。
- **分母分三层**：`有站点`（源码里确实有这一项的字面量）⊃ `可达`（静态寻址模型判可达）⊃ `真原位`
  （工具申报原位 **且** 字节确实变了）。通过率 = 真原位 ÷ 可达。
- **承诺违约 = 0** 的含义是：没有任何一行出现「工具声称原位写回、字节却没变」。这是本次研究要证伪的
  那种失败模式（`tools/_cu_static_table.py` 里 `viol` 计数器）。
- 没走成原位的分三类：参数非法被拒 / 证不出目标对象而拒绝落地 / 改为追加块完成（都不是静默丢改动），
  逐脚本清单可由 `tools/_cu_why_not100.py` 复算。

## 5. 脱敏（红线）

`verdicts/*.jsonl` 是**白名单投影**：只保留判定字段，**整份源码正文（`after_text`）与含源码行的 diff
（`block.diff_head`）一律剔除**，`block` 只留 `start_line` / `end_line`。白名单写在
`tools/sanitize_verdicts.py`，可用 `tools/check_no_corpus_text.py` 自查（本分支提交前实测
JSONL/JSON 命中数 = 0，可在本地复跑验证）。

许可证信息逐份保留在 `manifest.jsonl`，但**脚本正文不再分发**；想复核「某个脚本被怎么判的」，用
`manifest.jsonl` 里的 `sha256` / `url` / `license` 自行获取原文即可。

## 6. 运行时层与交互层的证据（`evidence/0.1.10/`）

静态层之外的证据单独放在 [`evidence/0.1.10/`](evidence/0.1.10/README.md)：

| 目录 | 内容 |
|---|---|
| `corpus/` | 语料级真跑：逐脚本判定（`results.jsonl`，1,837 行）、分层分母表（`SUMMARY.md`）、池指纹（`RUN_META.json`）、候选池定义（`filter.jsonl`） |
| `window/` | 真窗口：6 种图形 × 1,000 次手势的日志与数字表（`NUMBERS.md`） |
| `matrix/` | 离屏矩阵：12 张图 × 1,000 次随机操作的日志（两编码）与数字表 |
| `onepass/` | 写回验证「2 遍 → 1 遍」的等价性证明（before / after 逐份原始记录） |
| `sandbox/` | 四层沙箱及其边界（`SANDBOX.md`） |

三个入口文件：

- `evidence/0.1.10/README.md` —— 人读索引：每个数字去哪查、三级可复现性、脱敏口径、我们做不到的部分；
- `evidence/0.1.10/CLAIMS.json` —— 机器可读：每个数字 → 产物 + 命令 + 可复现等级 + 源码溯源（git HEAD / 文件 md5）；
- `evidence/0.1.10/MANIFEST.json` —— 每个文件的 sha256（防发布后被改动）。

发布红线守卫（任命中即失败：本机绝对路径 / 用户名 / 第三方脚本正文 / 清单哈希不符）：

```bash
python bench/tools/check_evidence_publishable.py --root bench/evidence/0.1.10
```

为什么会有这一节：2026-10-13 有人指出 README 主表最显眼的两行**只给了日志行号、而那些日志没随仓库发布**
（"只能信、不能查"）。核查后该批评成立 —— 静态层一直有本文件的正确做法，而运行时/交互层当时做不到。
`evidence/0.1.10/README.md` 第 0 节把这件事如实写下来了。
