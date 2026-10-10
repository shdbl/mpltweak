# 更新日志（CHANGELOG）

本项目从 0.1.0 起记录。格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)；
版本号遵循语义化版本。

> **发布方式提醒**：PyPI 的发布由 GitHub **Release 的 published 事件**触发
> （`.github/workflows/publish.yml` 是 `on: release: published`）。**只推 tag 不会发版**，
> 必须在 GitHub 上真正发布 Release。也不要对同一个版本号发两次。

## [0.1.8] — 2026-10-10

> 这一版把"布局调完能不能确定落地"这条链路补齐了：新增 `revert` 回退、
> `--verify-fast` 快速档、`--strict` 严格档、轴范围 `xlim/ylim`（参数 schema v4）、
> `check` 按**真实渲染尺寸**查文字，并修掉了 `--json` 输出编码与元组/带符号写法
> 原位写回等 6 个真 bug。测试：七套件 + `pytest -q` 25 项全绿（GBK 与 UTF-8 环境各跑一遍），
> 12 项反向验证全部命中；两个模型家族的独立只读审阅共 10 条发现全部修复。


### 修复
- **`--json` 成功路径的 stdout 不是 UTF-8**：`check` / `describe` / `apply --json` / `schema`
  原先用 `print()`，在管道被捕获时按**本地编码**（中国 Windows 上是 cp936/GBK）输出；JSON 里
  只要有一个中文（`check` 的 `msg`、`schema` 的 `description`），按 UTF-8 解码的 agent / MCP
  客户端 / CI 就会直接崩。现在统一走 fd 级 UTF-8 写出（与失败路径 `fail_json` 一致）。
- **顶层兜底 `guard_json_main` 在异常路径上失效**：`messages.py` 用了 `sys` 却没 import，
  写出结构化 JSON 之后就在 `sys.stderr.write` 上抛 `NameError`，把本该打印的人话提示、
  `MPLTWEAK_DEBUG` 的 traceback 和 `return 1` 全部吃掉（用户看到的是与真实故障无关的 traceback）。
- **元组/带符号写法的原位写回**：原先只认列表字面量与 `ast.Constant`，于是
  `add_axes((-0.01, ...))`（元组）和 `set_xlim(-0.01, 1.01)`（负号在 AST 里是 `UnaryOp`）
  会被**静默**丢进"未原位应用"——用户看到的是"拖了没反应"。
- **`--json` 的 `style` 字段在写回时永远是 `null`**：写回路径没设 `style`，导致 `apply` 永远走
  "已插入调整块"那条消息分支，**"原位修改 N 处"永远打不出来**，agent 也判断不了这次是哪种写回。
  （只读预览 `--json` 里 `style` 现在填**请求的模式**：`auto` 表示"逐图待定"——预览阶段还没决定
  这张图会不会退到块。）

### 新增
- **布局引擎冲突检测**（`layoutwarn.py`）：静态扫描脚本，命中
  `constrained_layout=True` / `layout='constrained'|'compressed'` / `set_constrained_layout` /
  `set_layout_engine` / `rcParams['figure.constrained_layout.use']` / `rcParams['figure.autolayout']`
  时给出**带行号**的提示与修复建议（`launch` 开窗前、`apply` 写回时、`check` 的 `layout_engine` 字段）。
  原因：布局引擎在**每次绘制**时重算轴位置，写回/拖拽的位置会被它覆盖，而旧行为只表现为一句
  "验证不一致"，指不到病因。
- **`mpltweak revert <脚本> [--write]`**：回退到上次写回之前（默认只预览）。用现成的
  `.tweak_params/<脚本>.tweak.bak`，**自身也可逆**（覆盖前把当前版本留成 `<脚本>.revert.bak`），
  恢复是字节级原样复制（BOM / 换行 / 编码不动）。
- **`check` 按真实渲染尺寸查文字**：绘制一次后用 `Text.get_window_extent()` 量外框 ——
  **超出画布 = 问题**（客观可判定），**文字互相压住 = 提示**；同轴超长刻度标签按
  "沿刻度方向的重叠比例"判"太密"。纯几何框查不出这类问题。`--no-text` 可退回纯几何口径。
- **轴范围 `xlim` / `ylim`（参数 schema v4）**：`describe` 采集（**只在脚本显式固定过时**才记，
  autoscale 不记），`apply` 原位改写 `set_xlim(a, b)` / `set_xlim((a, b))` / `set_xlim(xmin=, xmax=)`
  三种写法，`--style block` 的调整块也会带上，写回后由语义验证比对范围是否真的等于参数。
- **`check` 轴范围不统一提示**：多面板里 ≥60% 面板共享同一范围、另有少数不同 → 提示
  （各自为政的多面板图不报）。
- **`--verify-fast` 快速验证档**：只做语法静态检查（写坏当场回滚），**不重跑脚本**；
  `--json` 标 `verify_mode=fast` + `verified=null`，人读输出原话说清"不保证能跑通、也不保证布局正确"。
- **`--strict`**：`check --strict` 把布局引擎冲突升级为问题（rc=1，接 CI）；
  `apply --write --strict` 在冲突时拒绝写回（`--allow-layout-conflict` 可放行）。
- **轴网格身份 `cell`**：参数里记录每个轴调图时的网格格位 `[行数, 列数, 起始行, 起始列]`，
  写回后回比 —— 脚本改动导致轴序漂移时，语义验证会**指名道姓**地失败并回滚
  （而不是默默把位置套到别的面板上）；调整块注释里也带出这份身份。
- `tests/test_hygiene.py`（宽兜底棘轮 + 消息表 GBK 可编码 + 新模块登记）、
  `tests/test_suites.py`（pytest 包装）、`tools/audit_exceptions.py`（宽兜底清单与可窄化提示）。
- `CHANGELOG.md`（本文件）。

### 文档
- `CONTRIBUTING.md`：改正 Qt 依赖描述（`[qt]` extra，不是默认依赖）；补 pytest 路线、
  "加一套测试要改哪四处"、宽兜底棘轮与可窄化清单的用法。
- README（中/英）：新增 `revert` / `--verify-fast` / `--strict` 三节；「不适用场景」；
  「原位改数字 vs 插入调整块」（`--style auto` 的逐图判定、跨轮混用与彻底统一的正确做法）；
  参数文件示例补 `xlim` / `ylim` / `cell` 并升到 v4；`check` 一行写清新增检查项。

## [0.1.7] — 2026-10-10
### 修复
- 发布前审计的 **27 项修复**：写回引擎、并发写回、输出契约、交互细节。
### 文档
- README 演示图改用 **commit sha** 引用（避免 `@main` 缓存导致新 GIF 不生效）。
- CI 工作流、贡献者文档、issue / PR 模板、social preview 素材。

## [0.1.6] — 2026-10-09
### 修复
- 演示 GIF 播放异常慢（重新压缩时逐帧 `duration` 丢失，整段节奏被拍平）。
### 文档
- README 图片全部改为**绝对 URL**（PyPI 不渲染相对路径图片）。
- 英文项目简介。

## [0.1.5] — 2026-10-09
### 新增
- `check`（排版体检：对齐 / 等大 / 间距 / 字号 / 越界 / 重叠，`--json` 可机读）。
- `mcp` 服务端（把排版能力暴露给 AI 客户端），可选依赖 `mpltweak[mcp]`。
### 文档
- README 重构（目录 / MCP 与 skill 章节）；重写 AI 用的 skill 说明。

## [0.1.4] — 2026-10-09（**未发布到 PyPI**）
### 新增
- agent 接口第一版：`describe`（不开窗导出当前排版）/ `schema`（输出 JSON Schema）/
  `apply --json`（机器可读结果），打通"读 → 改 → 写"闭环。

## [0.1.3] — 2026-10-09
### 文档
- README 定版：更朴素的说法、带表头的对比表、说明命令在终端而不是 Python Console 里跑。

## [0.1.2] — 2026-10-09
### 新增
- **参数过期保护**：脚本在参数保存之后被手动改过时，拒绝套用旧参数（mtime 比较），
  避免静默覆盖用户的手动改动；确要续调用 `--force-resume`。

## [0.1.1] — 2026-10-09
### 变更
- PyQt5 进默认依赖（0.1.7 起改为 `[qt]` extra，开窗才需要）。

## [0.1.0] — 2026-10-09
### 新增
- 首个版本：交互式调 matplotlib 多图排版（拖拽 / 对齐 / 吸附 / 字号 / colorbar），
  参数静默落盘，AST 确定性写回 + 三层验证 + 失败自动回滚。
