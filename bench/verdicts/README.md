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
| `families` | 逐字段族的判定结果（见下） |

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
