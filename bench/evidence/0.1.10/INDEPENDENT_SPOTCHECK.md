# 独立复核与已知漏网（2026-10-13 发布后追加）

## 1. 这份文件为什么存在

发布后做了一次**独立抽查**：由一位独立复核者（与记录作者不是同一会话、用**独立驱动**）随机抽 50 份语料重跑，
回答的问题是"**已发布的判定，另一个人能不能复现**"。结果 **49/50 一致**、**1 条不一致**，并由此发现一处
**工具侧静态预筛的已知漏网**。按本项目"已知边界必须如实写明、不藏"的纪律，追加本文件。

## 2. 抽查方法（可重放）

- 样本：`random.Random(57).sample(sorted(both_ok paths), 50)`；池 = `both_ok=true` 的 1,837 份
- 池指纹：`corpus/filter.jsonl` 的 sha256 与 `corpus/RUN_META.json` 的 `pool_sha256` **逐字相同**
- 机制：**独立驱动 + 复用仓库自带 worker 入口**（`tests/test_corpus_invariants.py --worker`，保证判据同口径）；
  每条独立 workdir；整棵临时树放在沙箱游乐场内
- 纪律：显式 `MPLBACKEND=Agg`、零窗口、未改 `src/`、`tests/`

## 3. 结果

| 项 | 结果 |
|---|---|
| 判定一致 | **49 / 50** |
| 混淆矩阵（记录 → 复核） | `pass→pass` 24、`na_stdin→na_stdin` 13、`na_gui→na_gui` 11、`na_skip→na_skip` 1、`na_stdin→pass` **1** |
| 语料是否被改动 | **0 份**（跑前/跑后逐份 sha256 对账；与记录中的 sha256 **50/50 相符**） |
| 违约 | 复核者本轮 `violation=0`、`na_error=0` ⇒ **独立印证了已发布的"违约 0"** |
| 记录查找 | 50 份全部能在 `corpus/results.jsonl` 中找到（无缺记录） |

## 4. 那 1 条不一致：环境依赖，不是记录造假

- 脚本：`big/github/statsmodels/examples/python/statespace_sarimax_internet.py`（两边语料 sha256 完全相同）
- 记录：`verdict=na_stdin`、`reason1=fail`、`attribution=script_cannot_run`、字节未变
- 复核：**`pass`**，四律全过，**连跑 5 次 5/5** ⇒ 不是偶发
- 根因：该脚本**模块级联网下载数据**（约第 29-30 行 `pd.read_table("https://raw.githubusercontent.com/…")`）。
  双向对照：正常网络 `rc=0`；强制断网 `rc=1` 抛 `urllib.error.URLError`。
  ⇒ **它的 verdict 取决于当时网络是否可达**：联网 pass、断网 fail。记录那轮大概率无网络。
- 两个判定都是**"当时的真实行为"**，不是编造。

## 5. 已知漏网：静态联网预筛不认"URL 当调用实参"

- runner 的静态预筛 `_has_net_risk(text)` 只认：网络模块 import、`urlopen/urlretrieve`、`os.system|popen`、`subprocess.*`
- **不认** `pd.read_table("https://…")` 这类"URL 字符串出现在调用实参位置"的写法
  ⇒ 该脚本**未被 `na_skip(net_risk)` 挡住，被真跑了**（verdict 于是随环境翻转）
- 影响面（全池 1,837 份静态扫描 + 逐份离线对照实测）：

| 口径 | 命中 |
|---|---:|
| 公开口径 `_has_net_risk` | 30（其中 29 判 `na_skip`） |
| 补充口径（URL 作调用实参） | 12 |
| 漏网面（补充口径命中、公开口径未命中） | 10（**含误报**：`len("http…")` / `print("http…")` / `register_namespace("http…")` 等并非网络访问） |
| **逐份离线对照后：真依赖网络** | **4 / 1,837 ≈ 0.2%** |

4 份为：`statespace_sarimax_internet.py`、`statespace_local_linear_trend.py`、`stl_decomposition.py`、
`plot_wikipedia_principal_eigenvector.py`。

- **对已发布数字的影响：不是虚高，而是偏保守。** 这 4 份在已发布记录里的 verdict 是 `na_stdin`×2、`na_gui`×2
  —— **没有一份是 `pass`**。⇒ `corpus/SUMMARY.md` 的「**928 通过**」没有被夸大；
  方向相反：若记录那轮网络可达，这些"未判定"只可能变成 `pass`，**928 只会更大、不会更小**。
- 附带事实：`plot_wikipedia_principal_eigenvector.py` 联网时要下载大文件，实测把探针跑到 **240s 超时**；
  断网则 `rc=1 URLError` ⇒ 在网络快/慢两种环境下，这类脚本甚至可能落进不同桶。

## 6. 口径前提（必须与结论一起读）

记录那轮的源码状态是 `HEAD=02998e6` **+ 37 个未提交改动**；与本次复核使用的 `a67f87e` 相比，
9 个关键 src 文件中**只有 `params.py` / `cli.py` 的 md5 相同**，其余 7 个
（`apply` / `writeback` / `verify` / `messages` / `preflight` / `revert` / `launch`）**都不同**（含写回验证单遍化）。

⇒ 本次复核是「**用今天的代码，比对记录时的判定**」，**不是位对位复现**；那 37 个未提交改动已无法重建。
功能语义不受影响（49/50 一致即为证据），但读者不应把它当作"同代码复现"。

## 7. 原始产物在哪

本次复核的驱动脚本、逐条对照表、抽样与池指纹、语料前后哈希对账、离线对照记录等，位于仓库外的本机工作目录
`_review/spotcheck_t57/`（**未随本证据树发布**）：`COMPARISON.md`（50 行逐条表）、`comparison.json`、
`netrisk_scan.json`、`netrisk_offline_probe.json`、`sample_50.json`、`corpus_hashes.json`、
`provenance_diff.json`、`run50.log`，以及可重跑脚本 `spotcheck_t57.py` / `netrisk_scan.py` / `netrisk_offline_probe.py`。

## 8. 待办（下一版）

- 预筛补上"URL 字符串作调用实参"的识别（并加回归用例），把这类脚本统一判 `na_skip(net_risk)`；
  或对含网络迹象的脚本统一标"环境依赖"标记，避免 verdict 随环境翻转。
- 本项与本树 `sandbox/SANDBOX.md` 记录的「socket / subprocess 层在真跑链路不生效、靠静态预筛兜住」
  **属同一族问题**：兜底手段有效，但**预筛本身有已知漏网**，故在此如实并列。

---

（本文件由船长在发布后追加；内容依据独立复核者交付的报告与产物，**未改动任何已发布的判定数据**。）
