# 0.1.12 证据包

本目录是 0.1.12 发布时对 CHANGELOG 中每个数字的**可指证落点**：每条命令的原始输出
（stdout/stderr 原样）与退出码，由 `_release_012_collect.py` 逐条生成，机器可读清单位于
[MANIFEST.json](MANIFEST.json)。

## 这些文件各自证明什么

| 文件 | 命令 | 证明 |
|---|---|---|
| [writeback.txt](writeback.txt) | `python tests/test_writeback.py` | 写回套件 573 条断言全过（含函数封装锚点 §108、原位补参数 §109） |
| [fuzz_events.txt](fuzz_events.txt) | `python tests/test_fuzz_events.py` | 交互离屏事件 67 条全过（含拖动自愈 §t101 与两条反向验证） |
| [figure_matrix.txt](figure_matrix.txt) | `python tests/test_figure_matrix.py` | 图形矩阵 406 通过 / 0 失败 / 47 条显式不适用 / NOTE 0（含双 Y 轴位置共享硬断言） |
| [tweak.txt](tweak.txt) | `python tests/test_tweak.py` | 交互主套件 162 条全过 |
| [check.txt](check.txt) | `python tests/test_check.py` | 静态检查套件全过 |
| [agent_api.txt](agent_api.txt) | `python tests/test_agent_api.py` | agent 契约全过（含 A3 的 9 条反向断言） |
| [cli_output.txt](cli_output.txt) | `pytest -q tests/test_cli_output.py` | 人读输出契约 14 条（stderr/stdout 分工 + `--json` 不变） |
| [hygiene.txt](hygiene.txt) | `pytest -q tests/test_hygiene.py` | 工程卫生棘轮（含"套件在 GBK 控制台不崩"）59 passed |
| [docs.txt](docs.txt) | `python tests/test_docs.py` | 文档闸门（断链 / 锚点 / 歧义标题基线 / 测试行证据链接） |
| [doc_refs.txt](doc_refs.txt) | `python tools/check_doc_refs.py` | 文档里「文件:行号」引用 41 条、0 可疑 |
| [doc_cli.txt](doc_cli.txt) | `python tools/doc_cli_options.py` | 文档列的 CLI 选项与真实注册一致（①0 ②0） |

## 口径

- 全部在 Windows + 本仓库工作树跑，`MPLBACKEND=Agg`（无头）、`PYTHONIOENCODING=utf-8`；
- 单套件一律用 `python tests/<套件>.py`（这类套件是模块级脚本，直接 pytest 会 INTERNALERROR）；
- `MANIFEST.json` 里的 `rc` 与 `secs` 由各文件首部的 `[rc=N] X.Xs` 行解析，未做人工改动；
- 语料类数字（原位补参数覆盖 1,541 / 9,336 = 16.5%）出处是 `gapprobe/`（本机，不进仓库），
  CHANGELOG 里以"静态语料口径"字样标注范围。
