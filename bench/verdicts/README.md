# `verdicts/` —— 逐脚本判定（已脱敏）

每个文件一行一条记录（JSONL），一行 = 一个脚本。

## 顶层字段

| 字段 | 含义 |
|---|---|
| `path` | 语料内的相对路径（`corpus_union/manifest.jsonl` 的 `path` 可对回 sha256 / url / license） |
| `error` | 静态扫描状态：`None` = 可分析；`no_figure` = 没有图；`syntax…*` = 语法错 |
| `n_figs` / `fig` | 脚本里的图数量 / 本次判定的图序号 |
| `n_literal_sites` | 每个字段族在源码里的**字面量站点数**（`{族名: 个数}`） |
| `n_reach_sites` | 其中**静态寻址模型判可达**的站点数 |
| `unreach` | 不可达的站点（`{族名: [...]}`） |
| `families` | 逐字段族的判定结果（见下）。**只包含有判定**的组合 —— 可达组合数须另行从 `n_reach_sites` 汇总（见下） |

> **`families` 与"可达组合"是两个不同的数，别混着用**：`families` 只装**有判定**的组合（实测 3,578 条）；
> **可达组合数须另行从 `n_reach_sites` 汇总**，口径是**每个脚本里 `n_reach_sites[族] > 0` 的 (脚本, 族) 对数**
> （实测 3,610 = `families` 的 3,578 + 缺条目的 32）—— 要报"判定了多少"看 `families`，要报"可达多少"按这个口径数。
> **注意 `n_reach_sites` 的值是站点数、不是组合数**：直接按族把站点数相加得到的是 8,071（另一个量）。
> 差的这 32 条组合形式一致：「`n_reach_sites[族] > 0` 但连 `families[族]` 键都没有」（32/32，涉及 25 脚本 / 8 族），
> 属**上游静态扫描的记录缺口**，不是"跳过 / 拒绝"这类产品行为。

## `families[族名]` 的判定字段

| 字段 | 含义 |
|---|---|
| `family` / `n_sites` / `targets` / `multi_site` | 族名 / 站点数 / 目标值 / 是否多站点 |
| `rc` / `ok` | 这次判定的退出码 / 是否成功 |
| `bytes_changed` | **源码字节是否真的变了**（"承诺违约"就是 `decl_style=inplace` 而这里是 `False`） |
| `decl_style` | 工具申报的落地方式：`inplace` / `block` / … |
| `decl_applied` | 工具申报"落地了没有" |
| `decl_reason` | 工具给的结论码（`ok` / `best_effort` / `no_change` / `fail` / `bad_params` …） |
| `decl_needs_block` / `decl_auto_fallback` | 是否需要退块 / 是否由 `auto` 自动退块 |
| `decl_pending` / `decl_inplace_pending` | 没落地的字段清单（最终 / 原位那一趟） |
| `decl_inplace_reason` / `decl_verify_mode` | 原位那一趟的结论码 / 验证档位 |
| `landed` | 目标值最终是否等于源码值 |
| `verify` / `verify_reasons` | 逐项校验（`parse_ok` / `mask_ok` / `value_ok` / `form_ok` / `site_count_ok`）与失败原因 |
| `reloc_fail` | 重定位失败的原因 |
| `block` | 仅保留 `start_line` / `end_line`（块的起止行）；**含源码行的 `diff_head` 已剔除** |
| `block_decl` / `inplace_ref` / `s3` / `after_syntax_error` | 块的申报、原位参考、站点元信息、语法错消息（都不含源码正文） |

## 脱敏白名单

只保留上面这些键；**剔除**：

- `after_text` —— 整份写回后源码正文（实测 3,578 条）；
- `block.diff_head` —— diff 头里逐行打印了源码（`-ax2 = fig.add_axes((0.25, …))` / `+ax2 = …`），
  实测 427 条；只保留 `start_line` / `end_line`。

白名单实现在 `../tools/sanitize_verdicts.py`，自查脚本 `../tools/check_no_corpus_text.py`。
