# 开发

```bash
git clone https://github.com/shdbl/mpltweak && cd mpltweak
pip install -e .
python tests/test_tweak.py       # 交互内核
python tests/test_events.py      # 事件路径
python tests/test_redo.py        # 撤销 / 重做
python tests/test_writeback.py   # AST 写回 + 三层验证（最敏感）
python tests/test_agent_api.py   # describe / schema / apply --json
python tests/test_check.py       # 排版体检
python tests/test_mcp.py         # MCP 服务（未装 mcp 依赖时自行 skip）
python tools/crosscheck.py       # 环境 / API 兼容性自检
```

这些 `tests/test_*.py` 是**模块级脚本**（逐个直接跑，和 CI 一致）；`pytest -q` 是包装入口，
套件清单以 [`tests/test_suites.py`](../tests/test_suites.py) 的 `SUITES` 为准（含卫生棘轮）。细节见 CONTRIBUTING。

## GUI 离屏冒烟怎么手动跑（0.1.14 起）

`toolbox.py` 的开窗路径有一套**离屏**冒烟（`tests/test_gui_smoke.py`）：开一次真 QMainWindow
→ 拖一次 → 按一次键 → 关窗 → 断言导出的参数文件与图一致。**不需要显示器、不抢焦点鼠标**，
因为它跑在 Qt 的 offscreen 平台上。

```bash
pip install PyQt5                              # 只有这套需要 Qt（CI 上只有 3.12 腿装它）
python -m pytest -q tests/test_gui_smoke.py    # pytest 模式（CI 3.12 腿跑这条）
python tests/test_gui_smoke.py                 # 脚本模式（与 SUITES 的调用方式一致）
```

* **没装 PyQt5 时**：套件**显式 skip 并打印原因**（脚本模式下会打一整块 `SKIP |`），
  不会假装通过。要看它真跑，先 `pip install PyQt5`。
* **平台插件起不来时**（无头 Linux 缺 xkb/EGL 之类）：同样 skip，原因里是 Qt 的报错原文；
  CI 侧另有 `python -c "import PyQt5"` 断言 ⇒「装了却悄悄跳」与「压根没装」分得清。
* 它和 `tests/test_real_window.py` **不是一回事**：那套是**真窗口**（会抢焦点，按约定只在用户
  不在电脑前时手工跑，见 `SUITES_EXCLUDED`），照的是"窗口被钳制 / 200% 缩放"这类离屏天生
  照不到的问题；这套照的是"窗口建没建成、Qt 事件投递到没到、关窗导没导出"。

反向验证（证明它不是恒绿）：把 `src/mpltweak/toolbox.py` 的 `_on_close` 里 `self.export()`
注释掉 ⇒ 冒烟红（导出文件不存在）；把 `_on_release` 的落位那步改掉 ⇒ "拖动真的改了轴位置"那条红。


```text
src/mpltweak/
├── cli.py          # 命令分派：mpltweak <脚本> = launch；8 个子命令 launch / apply / revert / describe / check / schema / mcp / doctor
├── launch.py       # 跑脚本 → 挂窗口 → 关窗存参数
├── toolbox.py      # 交互内核（Tweak 控制器，纯 matplotlib 事件）
├── params.py       # 参数 JSON 规范（SCHEMA_VERSION=4 + validate）
├── apply.py        # 改动清单 / 写回入口
├── writeback.py    # AST 定位 + 原位 / 块写回
├── verify.py       # 语义验证（重跑后逐项比对）
├── describe.py     # 导出当前排版（agent 的"读"）
├── check.py        # 排版体检（agent 的"查"）
├── revert.py       # 回退到上次写回之前
├── layoutwarn.py   # 纯 AST 布局引擎扫描（check --static 用）
├── mcp_server.py   # MCP 适配层（mpltweak mcp）
├── messages.py     # 中英文案 + fd 级 UTF-8
├── __init__.py     # 版本号
└── __main__.py     # python -m mpltweak
```

`toolbox.py` 也可作**嵌入 API**（`from mpltweak.toolbox import gaitu`），
但主推 CLI —— 用户脚本里不该出现本工具的任何痕迹。

### `doctor` 的选项口径

- `mpltweak doctor` **不接受其它选项，也不接受位置参数**（多余参数 ⇒ **rc=2**，usage 走 stderr）—— 与其它子命令一致，不再静默忽略。
- **`doctor` 没有 `--json`**：它的输出是给人看的自检结果，不产出机器可读载荷；给了 `--json` ⇒ **rc=2** 且明确说明（**明拒，不静默忽略**）。
- `-h` / `--help` ⇒ rc=0，打印 doctor 用法。

## 同类语料验证（16k 语料差分）怎么做

改动碰到「写回判据」这类敏感面时，跑一遍**全池语料差分**：同一份语料、同一个解释器、
同一份参数，把**两臂**（A = 上一个发布 tag，B = 当前工作树快照）各跑一遍 inplace 与
block 两条写回路径，然后比 `reason` / `changes` / 写后字节。

仓内入口是 `tools/corpus_diff.py`（语料本体与产物**刻意留在仓外**，见下）：

```bash
python tools/corpus_diff.py --plan      # 打印可复制的命令（默认动作）
python tools/corpus_diff.py --check     # 就绪自检：语料 / harness / 解释器在不在（rc=0/1）
python tools/corpus_diff.py --baseline  # 重算 I2 指标并与基线比对（rc=0 未漂 / 1 漂了）
```

路径参数化：`--corpus` / `--work`，或环境变量 `MPLTWEAK_CORPUS` / `MPLTWEAK_CORPUS_WORK`
（默认取仓库上一级的 `corpus_union/` 与 `_corpus013/`）。**缺语料时只给人话、不抛栈。**

一次完整的差分跑（`--plan` 输出的就是这些，逐条可复制）：

```powershell
$py = 'D:\Miniconda\envs\longxia\python.exe'
cd <工作区>\harness
& $py freeze_arms.py                     # A = git worktree v0.1.12；B = 当前 src 快照（两次 sha256 一致才认）
& $py build_manifest.py                  # 生成 meta/corpus_manifest.jsonl（分母来源）

$env:NSHARD='32'; $env:JOBS='4'; $env:STYLE='inplace'; & $py launch.py   # 32 分片 × 2 臂 = 64 作业
$env:START='inplace'; & $py aggregate.py                                  # -> out/SUMMARY_inplace.md
$env:STYLE='inplace'; & $py probe_xtab.py > ..\out\REVERSE_inplace.md     # 反方向取证
# block 同样一遍
& $py verify_claims.py                   # 把 CLAIMS.json 里能机检的断言从磁盘重算（全 PASS 才 rc=0）
& $py make_manifest.py                   # 产物清单（路径 / 字节 / sha256）
```

**分母口径**（写数字时必须带上用的是哪个）：`corpus_union` **递归** `*.py` = **16,126**
份 = `018` 1,676 + `big` 8,482 + `harness` 5,968。其中 `harness` 那 5,968 份是**本项目自己的
脚手架脚本**，不是第三方语料 —— 谈「真实用户脚本」时分母用 `big + 018 = 10,158`。

**产物落在哪**：工作区 `out/` 下 —— `out/{A,B}/{inplace,block}/shard_00..31.jsonl`
（每片首行 `__meta__` 记 exe / python / offset / limit / pool_total）、`out/SUMMARY_*.md`、
`out/summary_*.json`、`out/REVERSE_*.md`。**哪些证据刻意留在仓外**：语料本体、两臂
worktree（`armA` / `armB`）、128 个分片 JSONL（约 53 MB）、GIF / PNG 抓图、`home/` 与
`tmp/`（假家目录）—— 一次跑的产物总共约 **78 MB**（717 个文件），进仓会毁掉克隆体积。
仓内只留：入口 + 做法（本文）+ 基线数字（下一节）+ 机检清单（`CLAIMS.json` 与
`verify_claims.py` 属于**证据**，需随语料一起留在工作区；仓内以本文与入口为准）。

### I2 回归基线（发版前对照）

`python tools/corpus_diff.py --baseline` 会从分片重算下面三行，和内置基线逐项比：

| 指标 | 定义 | 基线（0.1.12 → 0.1.13，style=inplace） | block |
|---|---|---:|---:|
| 拒写面 | B 臂 `reason=bad_params` 的**份数** | **1,214 → 170** | 1,214 → 170 |
| 放行面 | B 臂带「dpi 静态判不出」warning 的**份数** | **166（1.03%）** | 154（0.955%） |
| 其中真放行 | A 拒（`bad_params`）而 B 放行（`ok`/`best_effort`）的**份数** | **44（0.27%）** | 44（0.27%） |
| 同 reason 字节 | 两臂 `reason` 相同但写后 `sha256` 不同 | **0** | 945（口径待定，见下） |

**阈值与解释**（`--baseline` 就按这个判）：

- **分母变了**（语料增删）⇒ 先**重定基线**，不要当回归；工具会明确提示这一点。
- `bad_params` **上升** ⇒ 回归，先查拒写判据（这是"该拒的拒不住"）。
- 「dpi 判不出 ⇒ 主动放行」份数**上升超过基线 +10%** ⇒ 回归（放行面变宽，且没在 warning 里说清）；
  **下降**是改善，可以把基线数字往下调（改 `tools/corpus_diff.py` 的 `BASELINE`）。
- `block` 的「同 reason 字节不一致 945」是**已知口径差异**（块插入位置/缩进在两种写回路径下
  不同），**不作为回归判据**；`inplace` 的 0 才是硬指标。

**这份基线的出处**：工作区 `_corpus013` 的 `README.md`（§5 结论与 §「A→B 变化」表）、
`out/SUMMARY_inplace.md` §2 的 reason 分布，以及 `--baseline` 从 32×2 个分片重算的原始计数
（命令即 `python tools/corpus_diff.py --baseline`，结果 16,126 / 1,214→170 / 166 / 44 与上表一致）。
**注意「份」与「条」**：166 是**份数**（inplace 下每份恰好 1 条，所以两者相等）；
block 是 154 份 / 154 条。引用时别把「条」写成「份」。
