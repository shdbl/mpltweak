# 给 AI / agent 的排版接口

大模型能写出正确的绘图代码，但**排版数字基本靠猜** —— 它看不到那张图。

mpltweak 把这部分从"猜"变成"写文件"：

**参数文件是公开 JSON 格式（带 JSON Schema）**，AI 可以直接生成或修改它，不需要"看懂"图。`mpltweak apply --write` 负责确定性落实：AST 定位 + 三层验证 + 失败回滚，**写错了也不会把你的脚本改坏**。

**分工：让 AI 管内容，让 mpltweak 管排版。**

**入口定位：命令行是主路径，`mpltweak mcp` 是可选适配层。**

- **命令行是主路径**：只要调用方**能跑命令**（人或 agent —— Claude Code / Codex / Cursor / DSH 这类都行），
  用下面三条就够，不需要任何额外配置：

```bash
  mpltweak describe fig1.py -o layout.json   # 读：拿到当前排版
  mpltweak apply fig1.py --write --json      # 写：落回源码（--json 时过程信息全走 stderr）
  mpltweak check fig1.py --json              # 查：排版体检，退出码即 CI 门禁
  ```

- **`mpltweak mcp` 只服务"拿不到 shell"的客户端**（Claude Desktop 这类图形客户端）：它把 CLI 的
  **核心子集**包成 stdio 工具，按 **best-effort** 维护 —— 定位、功能与验收口径都以 CLI 为准。
  **能跑命令就不必配 MCP**；确需接客户端时看本节的「也可以接进 AI 客户端（MCP）」。

```jsonc
// agent 产出这个 → mpltweak apply fig1.py --write → 落进你的源码
{ "axes": [
  { "index": 0, "pos": [0.080, 0.560, 0.395, 0.330],
    "title_fontsize": 9.5, "legend": { "loc": "upper right" } },
  { "index": 1, "pos": [0.525, 0.560, 0.395, 0.330],
    "title_fontsize": 9.5, "legend": { "loc": "upper right" } }
] }
```

**读 → 改 → 写 → 查回路：**

| 命令 | 作用 |
|---|---|
| `mpltweak describe <脚本>` | 不开窗，把**当前排版**导出成参数 JSON（agent 的"读"）；`--all-figs` 时是嵌套的 `{"figures": [...]}`，**不能**直接喂回 `apply --params`（`rc=1 / reason=no_axes`，见「参数文件」） |
| `mpltweak schema` | 输出参数文件的 JSON Schema，供 agent 校验自己生成的内容 |
| `mpltweak apply <脚本> --write --json` | 落实回源码，并输出机器可读结果（`--json` 时过程信息全部走 stderr） |
| `mpltweak revert <脚本> [--write]` | 回退到上次写回之前（默认只预览；回退前也会留存当前版本） |
| `mpltweak check <脚本> --json` | 不看图也能检查：对齐 / 等大 / 间距 / 字号 / 越界 / 重叠，**文字按真实渲染尺寸**查（超出画布 = 问题，互相压住 = 提示），另有轴范围不统一提示；布局引擎冲突单独一栏（`--strict` 可升级为问题） |

```bash
mpltweak describe fig1.py -o layout.json    # 读：拿到现在的排版
# ...agent 改 layout.json 里的几个数字...
mpltweak apply fig1.py --write --json       # 写：落回源码并自检
mpltweak check fig1.py --json               # 查：排版体检，输出 JSON
```

**为什么需要"读"**：`add_axes([...])` 这种明写的位置，读源码就够了；但 `plt.subplots` 的网格、
没写 `fontsize=` 时实际生效的默认值、`tight_layout` 之后的位置、colorbar 与 cartopy 锁定后的
真实尺寸 —— 这些**源码里没有数字**，得跑一遍才知道。

**agent 路径建议不要加 `--verify-fast`**：它只检查语法、**不重跑脚本**，因此证明不了"某个字段
与源码现值相等"。当源码里没有可比写法时（典型：`plt.subplots` 的网格轴位置由 subplotspec + 边距
算出），工具只能**保守退块**。实测：拿 12 个真实语料脚本走 `describe` 原样导出 → `apply --write
--verify-fast`，能跑起来的 **7/7 都插了调整块**（`audit_018/engine-hunter/fix018/t15_agent_path.txt`）；
同一条路径**去掉 `--verify-fast`** 就是 `reason=no_change`、一个字节不动（本地 A/B：同一份
`plt.subplots(2, 2)` 脚本，加它 → `style=block` 且块 31 行，不加 → `reason=no_change / applied=false`，
`audit_018/promo/t16_probe2.log`）。`--verify-fast` 的定位是"慢脚本的快速语法档"（见「慢脚本」一节），
不是 agent 的默认档。

## 也可以接进 AI 客户端（MCP）——**可选适配层**

**先看这句**：只要你的客户端 / agent **能跑 shell**，直接用上面的命令行就够了，**不必配置 MCP**。
MCP 面向的是**拿不到 shell 的客户端**（Claude Desktop 这类，AI 只能在对话里调工具）。

它是**可选依赖**（要求 Python ≥ 3.10），装上之后能把 mpltweak 挂进 Claude Desktop / Cursor / Claude Code：

```bash
pip install "mpltweak[mcp]"
mpltweak mcp                    # 启动 stdio server
```

客户端配置加一段：

```json
{"mcpServers": {"mpltweak": {"command": "mpltweak", "args": ["mcp"]}}}
```

**它暴露的是 CLI 的「核心子集」**（下面这张表按 `mcp_server.py` 里的实际函数签名写）：

| MCP 工具 | 参数（实际签名） | 对应 CLI |
|---|---|---|
| `describe_layout` | `script`, `fig=-1`, `all_figs=False` | `describe [--fig N] [--all-figs]` |
| `check_layout` | `script`, `fig=-1`, `tol=0.01` | `check [--fig N] [--tol T]` |
| `params_schema` | （无参数） | `schema` |
| `apply_layout` | `script`, `write=False` | `apply --write`（读默认位置的 `.tweak_params/<脚本>.json`，**不接受** `--params` 路径） |

**不在这个子集里的**（只在 CLI 上有）：`revert`、`--style`、`--verify-fast`、`--keep-unverified`、
`--strict`、`--no-text`、`--static`、`apply --all-figs`、`--lang`。MCP 按 **best-effort** 维护；
四个工具里的用户脚本同样跑在**独立子进程**（写文件 / 联网等副作用照旧真实发生）。

## 让 AI 直接读这份手册（skill）

仓库里的 `skills/mpltweak/SKILL.md` 是一份**写给 AI 的操作手册**（Agent Skills 格式）：
命令、使用铁律、以及几种人机协作方式。想让自己的 AI 助手学会用 mpltweak，
把这份文件喂给它就行 —— 不用你每次解释一遍怎么用。

## 人 + agent 怎么分工

| 分工 | 怎么做 |
|---|---|
| **人拖 → agent 落盘** | 你开窗拖完，说一句"落盘"，AI 跑 `apply --write` |
| **agent 起草 → 人微调** | 让 AI 按规则先排一版 → 你开窗接着调（参数会续上）→ 确认落盘 |
| **agent 自检** | AI 画完图跑 `check`：不看图也能发现不齐 / 不等大 / 间距不均 / 字号不一 / 越界 / 重叠 |
| **一张图当模板** | `describe` 排好的那张 → 改 `script` 字段 → `apply` 到其它脚本，统一整篇论文的排版 |
| **人拖完，agent 总结** | AI 读参数 JSON，用大白话说明改了什么（可当 commit message 或图注） |

原则很简单：**"好不好看"归你，"精确落实"归它。**
