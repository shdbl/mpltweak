<div align="center">

<img src="https://cdn.jsdelivr.net/gh/shdbl/mpltweak@e9b34f0/docs/banner.png" width="720" alt="mpltweak — matplotlib layout, the PPT way">

# mpltweak

**像在 PPT 里调多图排版一样调 matplotlib，然后把参数写回你的代码。**

[![PyPI](https://img.shields.io/pypi/v/mpltweak?style=flat-square&color=2F6FEB)](https://pypi.org/project/mpltweak/)
[![Python](https://img.shields.io/pypi/pyversions/mpltweak?style=flat-square)](https://pypi.org/project/mpltweak/)
[![License](https://img.shields.io/badge/license-MIT-2F6FEB?style=flat-square)](LICENSE)
[![Tests](https://github.com/shdbl/mpltweak/actions/workflows/tests.yml/badge.svg)](https://github.com/shdbl/mpltweak/actions/workflows/tests.yml)

**中文** | [English](https://github.com/shdbl/mpltweak/blob/main/README.en.md)

</div>

## 它解决什么问题

调一张多面板图（一张画布里并排放好几张小图）时，麻烦通常出在排版而不是数据：挪一下子图，旁边的轴标签就被挡住；字号放大一点，图例又压到曲线上 —— 而每改一次，都要重跑脚本、再看一遍图。

**mpltweak 的做法**：像在 PPT 里摆放图片那样直接拖，拖完把那几个数字写回源码。它**只管样式和位置，不发明内容** —— 文字、数据、图形仍完全由你的代码决定。

**零侵入**：不改你的代码、不加 `import`、不生成副文件 —— 拖出来的参数单独存在 `.tweak_params/fig1.json`；**只有你明确要求写回，`.py` 才会变**。

| 方式 | 流程 |
|---|---|
| **传统方式** | 改一个数字 → 重跑脚本 → 等几十秒 → 看 PNG → 还是不对 → 再改 |
| **mpltweak** | 打开窗口 → 拖到位 → **让它把那几个数字写回代码** |

演示：

<table>
<tr>
<td width="50%"><img src="https://cdn.jsdelivr.net/gh/shdbl/mpltweak@e9b34f0/docs/gifs/01_drag_layout.gif" alt="拖动面板 + 吸附参考线"><br>
<b>拖动 + 边缘吸附</b><br><sub>拖动时给出 ghost 预览与对齐参考线，靠近对齐位置自动贴合</sub></td>
<td width="50%"><img src="https://cdn.jsdelivr.net/gh/shdbl/mpltweak@e9b34f0/docs/gifs/02_multi_align.gif" alt="多选对齐 + 均分"><br>
<b>多选对齐 / 均分</b><br><sub>Ctrl 加选三个面板 → 左对齐 + 垂直均分</sub></td>
</tr>
<tr>
<td><img src="https://cdn.jsdelivr.net/gh/shdbl/mpltweak@e9b34f0/docs/gifs/03_fontsize.gif" alt="悬停改字号"><br>
<b>悬停改字号</b><br><sub>鼠标悬停标题、轴标签、刻度或图例，按 <code>+</code> / <code>-</code> 直接调</sub></td>
<td><img src="https://cdn.jsdelivr.net/gh/shdbl/mpltweak@e9b34f0/docs/gifs/04_wireframe.gif" alt="cartopy 线条模式"><br>
<b>线框模式提速（空格）</b><br><sub>cartopy 全球图全量重绘 <b>494ms → 169ms</b>，拖动不再卡顿</sub></td>
</tr>
</table>


## 两条路，挑一条

mpltweak 自带一份**给 AI 助手（Agent）看的手册（Skill）**：把这份手册交给能读写你电脑文件的 Agent，它会替你把命令全部敲完。所以有两条路可选——**用的是同一个工具、同一份 Skill**，区别只在"谁敲命令"：

- **路线 A：交给 Agent**（不熟悉命令行就选这条）：你只负责看图、说话。← **下面先讲这条**
- **路线 B：自己敲命令**：几条命令，十分钟能跑通。→ [跳到文末](#路线-b自己敲命令)

## 路线 A：交给 Agent

**你不需要懂命令行 —— 把这件事交给 Agent 就行。**
（**要能直接读写你电脑上文件的那种 Agent**；只在网页聊天窗口里聊天的不行。）

**把下面这句话直接复制给你正在用的 Agent**（Claude Code / Codex / Cursor / DeepSeek Harness 等，只要它能读写你电脑上的文件）：

> 请帮我装一下 https://github.com/shdbl/mpltweak/blob/main/skills/mpltweak/SKILL.md 这个 skill，然后按里面的说明把 mpltweak 装好；装好之后先跑一下示例给我看效果。

### 先花两分钟看看效果（让 Agent 跑示例）

装好之后，让它先跑一次**示例**给你看：它生成一份**示例脚本**（四面板合成图，纯 matplotlib 写法）并打开调图窗口，你随便拖、随便改字号；关掉窗口它会用人话告诉你**你改了哪几处**、并把**代码会变成什么样**逐行给你看。

觉得不错，就让它把改动写回那份**示例脚本**、重跑一遍，并给你看**改前 / 改后**两张图。

> 全程只在当前目录下新建一个 `mpltweak-demo/` 文件夹，**不会碰你自己写的任何脚本**。

### 日常使用中跟 Agent 协作的流程

1. **让 Agent 帮你画图。**（**如果你已经有画图脚本、只想改排版，可以直接跳到第 3 步，并告诉它脚本名，比如 `fig1.py`。**）
   *你会看到*：Agent 把画好的图贴给你看。
2. **先确认图的内容对不对**（这一步别急着调排版）。坐标轴、标签、数据是不是你要的？不对就现在说，让 Agent 改。
   *你会看到*：内容定稿的版本 —— 后面要调的只是"排版"，不是"画什么"。
3. **跟 Agent 说一句"我要交互式修改"。** 它会自己把调图窗口弹出来（你不用装东西、也不用打任何命令）。
   *你会看到*：一个窗口弹出来，里面就是你的图，可以用鼠标拖、缩放。
4. **你在窗口里改。** 拖面板、拖图例、调字号、对齐、均分（**在窗口里按 `?`，随时调出全部键位说明**）。
   *你会看到*：拖动时是半透明预览，松手才落位。
5. **Agent 用人话告诉你它改了什么，并等你确认。** 比如"子图 (a) 往右挪了一点、标题字号从 12 调到 14"。
   这一步**代码还没动** —— 你不同意就继续说，它接着改。
   *你会看到*：一份"改了什么"的人话清单 + 一句"要不要写进代码？"
6. **你说"可以"，Agent 才把改动写回代码，然后重跑给你看。** 重跑之后对着第 2 步的内容再看一眼，确认没被改坏。
   *你会看到*：重跑出来的新图 + 它改了哪几处；不满意可以接着调，或者让它退回上一次。

> **全程你不需要打开命令行、不需要记任何命令** —— 你只需要看图 + 说话。

```mermaid
flowchart LR
    A["① 你对 Agent 说：我要什么图"]:::script --> B["② Agent 写脚本 + 跑出图"]:::win
    B --> C{"③ 你看图：内容对不对？"}:::check
    C -->|不对| B
    C -->|对| D["④ 你说：要交互式修改<br/>Agent 弹出调图窗口"]:::win
    D --> E["你在窗口里拖着改<br/>（按 ? 看键位）"]:::win
    E --> F{"⑤ Agent 用人话说明改了哪些地方"}:::check
    F -->|不满意| E
    F -->|满意| G["⑥ Agent 写回代码 + 重跑"]:::win
    G --> Z(["你看新图，完事"]):::ok

    classDef script fill:#F7F8FA,stroke:#C9D1DB,stroke-width:1.4px,color:#16181D
    classDef win fill:#2F6FEB,stroke:#2F6FEB,stroke-width:1.6px,color:#FFFFFF
    classDef check fill:#F5F8FF,stroke:#2F6FEB,stroke-width:1.6px,color:#16181D
    classDef ok fill:#EAF7EF,stroke:#2E9E5B,stroke-width:1.4px,color:#16181D
```

**窗口里的键位（按 `?` 随时调出全部）**：拖动 / 多选、对齐 / 均分、吸附、`Ctrl+F` 裁白边、空格线框提速、悬停文字后按 `+` / `-` 调字号、图例 8 个标准位、colorbar 调长 / 调厚、**叠在一起时 `Alt+点击` 穿透**、**`Tab` 循环同级元素**。完整版见 [docs/interaction.md](https://github.com/shdbl/mpltweak/blob/main/docs/interaction.md)。

## 它会怎么改你的代码？三种结果

它对每份脚本的处理结果，事先就能说清，只有三种：

| 情况 | 它会怎么做 | 结果 |
|---|---|---|
| **源码里明写着这个数字**<br>（`fig.add_axes([0.08, 0.66, 0.30, 0.21])`） | **原位改那一个数**，不插任何东西 | 代码里那一行变了，别处一个字不动 |
| **源码里没有能改的数字**<br>（`plt.subplots()` / `GridSpec` 画的图） | **在文件末尾追加一小段自包含代码**，不加 `import`、不动你原有逻辑 | 代码末尾多出一个带 `auto layout` 标记的块 |
| **它证明不了该改哪一个**<br>（**号牌不稳时它不猜**：循环里建轴、建轴点与参数条数对不上、载体名不是恰好一次模块层调用；**函数里建图本身已经能写**） | **不写**。它不会猜一个"大概是这张"的对象 | **代码一个字节都不变**；并给出一条**能让 Agent 照着动手**的定位说明（改哪个符号、改成什么）。完整判据表（作用域 × 建轴方式 → 能不能写）见 [docs/verify.md](https://github.com/shdbl/mpltweak/blob/main/docs/verify.md) |

### 1. 能原位改的：直接改那一行

改前：

```python
fig = plt.figure(figsize=(10, 8))
ax1 = fig.add_axes([0.08, 0.66, 0.30, 0.21])
ax1.set_title('(a)', fontsize=12)
```

改后：

```python
fig = plt.figure(figsize=(10, 8))
ax1 = fig.add_axes([0.06, 0.68, 0.32, 0.23])   # ← 就改这几个数字
ax1.set_title('(a)', fontsize=15)              # ← 字号补在同一行末尾
```

**没有新增任何行，别处一个字不动。**

### 2. 不能原位改的：文件末尾追加一小段

改前（`subplots` 画的图，源码里没有能直接改的数字）：

```python
fig, axes = plt.subplots(1, 2, figsize=(10, 4))
axes[0].plot(np.arange(5), np.arange(5))
axes[0].set_title('(a)')
axes[1].plot(np.arange(5), np.arange(5) * 2)
axes[1].set_title('(b)')
fig.savefig('fig1.png', dpi=150)
```

改后（下面整段是真跑输出的原文，只有时间戳随生成时刻改变）：

```python
fig, axes = plt.subplots(1, 2, figsize=(10, 4))
axes[0].plot(np.arange(5), np.arange(5))
axes[0].set_title('(a)')
axes[1].plot(np.arange(5), np.arange(5) * 2)
axes[1].set_title('(b)')
# ===== auto layout (generated) - do not edit inside =====
# 本块由 mpltweak 自动生成于 <时间>（可手动微调，勿删上下两行标记）
# 轴身份（调图时）：ax0=网格[1, 2, 0, 0]，ax1=网格[1, 2, 0, 1]
axes[0].set_position([0.0800, 0.1500, 0.3800, 0.7500])
axes[1].set_position([0.5477, 0.1100, 0.3523, 0.7700])
axes[0].lines[0].set_linewidth(1.5)
axes[0].lines[0].set_color('#1f77b4')
axes[1].lines[0].set_linewidth(1.5)
axes[1].lines[0].set_color('#1f77b4')
# ===== end auto layout =====
fig.savefig('fig1.png', dpi=150)
```

**你原来写的代码一行都没被改写**；不满意就整段删掉，或一条命令撤回（见下节）。

### 3. 证明不了的：不写，告诉你怎么办

改前（轴是循环建的，运行时才知道 `fig.axes[i]` 指的是哪一根）：

```python
fig = plt.figure(figsize=(8, 3))
for i, name in enumerate(['a', 'b', 'c']):
    fig.add_subplot(1, 3, i + 1).plot(np.arange(5), np.arange(5) * (i + 1))
    fig.axes[i].set_title(name)
fig.savefig('fig2.png', dpi=150)
```

它给出的是（真跑输出的原文，节选最后一行）：

```text
· 保持原样: 块内只能按**静态可证的变量名**定位目标对象；这些字段证不出目标
  （没有可证的变量名，而运行期下标 fig.axes[i] / plt.gcf() 会写到别的轴或别的图上）
  → 按 fail-closed 处理，本次不写：ax0.lines、ax0.pos、ax1.lines、ax1.pos、ax2.lines、ax2.pos
```

**代码一个字节都没变。** 这不是死路：那段话直接告诉你该改哪一处。**Agent 看到它会自己把脚本改好，再回来接着调图** —— 你不用复制，也不用转达。

## 它可靠吗？

**四道保险，从松到紧：**

1. **只要你不要求写回，你的 `.py` 就保持原样。** 开窗、拖动、关窗，全程只往 `.tweak_params/` 里写参数，代码不动。
2. **写回前可以先看。** 让它先把"要动哪几行、从什么改成什么"逐行给你看——只看不改。
3. **真写回会自动备份**，一条命令就能退回写回前。
4. **证明不了的写法一律不写**，并说明原因 —— 宁可这次不生效，也不给你的代码里塞一个指错对象的东西。

每次真写回还会自动过**三道检查**：**语法**（改完的代码还能不能正常解析）→ **重跑**（脚本能不能真的再跑一遍、图还能不能出）→ **语义**（图上的位置、字号是不是跟你拖的一致）。任何一道不过，它会**自动退回写回前**，你拿到的仍是原来那份脚本。

拿 **10,158 份**真实科研绘图脚本、**1,780 份**真跑、**13,000 次**随机操作试过：**"说了改好、其实没改" 这类事 0 次。**

（静态差分那一批语料扩到 **16,126 份** `.py` = **10,158 份第三方公开绘图脚本** + **5,968 份本项目自身脚手架**；谈"真实用户脚本"时分母只用前者，两者不混着说。）

完整的样本分母、判据与逐族结果见 [docs/reliability.md](https://github.com/shdbl/mpltweak/blob/main/docs/reliability.md)；不适用场景与不声称项见 [docs/boundaries.md](https://github.com/shdbl/mpltweak/blob/main/docs/boundaries.md)。

## 路线 B：自己敲命令

### 安装

```bash
pip install mpltweak            # 核心：describe / check / apply
pip install "mpltweak[qt]"      # 要开窗拖图再加这个（装 PyQt5 图形后端）
```

```bash
mpltweak --version      # 版本
mpltweak --check-update # 查 PyPI 有没有新版本（显式联网；默认任何命令都不联网）
```

**退出码**（脚本里判断用）：`0` 成功 / `1` 检查发现问题 **或本次不写**（含 fail-closed 拒写）/ `2` 跑不起来（脚本找不到、脚本自身报错、参数非法）/ `4` 没有可用图形后端。四个入口口径一致。

`[qt]` 提供 PyQt5 后端；只跑 `describe` / `check` / `apply` 不需要。没有 Qt 时回退 Tk（交互能力受限，开窗会提示）；一个后端都没有时开窗失败并说明原因。

> 后端对照表、各平台装法、`mpltweak doctor` 探测顺序与开窗失败逐条处理 —— [docs/interaction.md](https://github.com/shdbl/mpltweak/blob/main/docs/interaction.md)。
> 哪些命令会**执行**你的脚本、退出码怎么算 —— [docs/exec-boundaries.md](https://github.com/shdbl/mpltweak/blob/main/docs/exec-boundaries.md)。

### 示例

```bash
mpltweak demo            # 生成 ./mpltweak-demo/ 并开窗；不写盘
mpltweak demo --write    # 写回示例脚本 + 重跑 + 生成 before.png / after.png
```

关窗后输出改动清单与逐行 diff；不带 `--write` 时示例脚本一个字节不变。目录已存在则复用，不覆盖。

### 六步

```
cd 你的脚本目录
```

`fig1.py`（零侵入：不需要 `import mpltweak`，不改动任何现有代码）：

```python
# fig1.py —— 就是平时写的 matplotlib
import matplotlib.pyplot as plt
import numpy as np

fig = plt.figure(figsize=(10, 8))
x = np.linspace(0, 1, 200)

ax1 = fig.add_axes([0.08, 0.66, 0.30, 0.21])      # 位置就是这几个数字
ax1.plot(x, np.sin(6 * x))
ax1.set_title('(a)')

ax2 = fig.add_axes([0.11, 0.38, 0.30, 0.21])      # 手写的数字，通常都不齐
ax2.scatter(np.random.rand(60), np.random.rand(60), s=8)

ax3 = fig.add_axes([0.05, 0.08, 0.30, 0.21])
ax3.hist(np.random.randn(300), bins=20)

fig.savefig('fig1.png', dpi=150)
```

| 步 | 命令 | 你会看到 |
|---|---|---|
| **1 · 装** | `pip install mpltweak`<br>`pip install "mpltweak[qt]"` | 核心包 / 加 PyQt5 后端（后者才需要开窗） |
| **2 · 开窗拖** | `mpltweak fig1.py` | 调图窗口：**拖**面板改位置、拖**边 / 角**缩放、鼠标**悬停文字**按 `+` `-` 调字号、**空格**切线框提速、**Ctrl+F** 裁白边；按 **`?`** 出全部键位 |
| **3 · 关窗** | —— | 脚本**一个字符都没变**；参数落在 `.tweak_params/fig1.json`（没调好就再开窗接着调）。真拖过东西的话，关窗时终端会给你下一条可复制的命令 |
| **4 · 预览** | `mpltweak apply fig1.py`<br>`mpltweak apply fig1.py --diff` | 前者只读列出每轴参数与「原位改 / 插块 / 拒写」判定；后者给**逐行 diff**（`{line,type,old,new}`），`type ∈ inplace / insert_block / replace`。两者都**不执行脚本、不写文件**；`--diff` 的 `preview_sha256` 与真实写盘文件 sha256 一致（跨分钟时看 `preview_sha256_norm`）。要机器可读就加 `--json` |
| **5 · 写回** | `mpltweak apply fig1.py --write` | **原子写**（临时文件 + rename）→ 备份到 `.tweak_params/fig1.tweak.bak` → 子进程重跑验证 → 语义比对。成功只报一行；**幂等**（同参数重复写回不产生新差异）；撤回：`mpltweak revert fig1.py --write`（先不带 `--write` 是预览） |
| **6 · 重跑看图** | `python fig1.py` | 图就是你拖完的样子 |

**写回是确定性的，不靠猜。** 它不是文本层正则替换（那样别处的 `0.08` 会被误伤），而是把脚本解析成 **AST（语法树）**，定位到"哪一句在建图、哪一个 `Constant` 是位置"，再顺着变量名绑定到目标对象；写回是 **AST 确定性重写 + 原文保真**（未命中的行逐字节不变）。整个过程**没有 LLM 参与**：同一输入、同一参数，输出恒定。

写回分两条通道：**源码里是字面量 ⇒ 原位改常量**（不插任何节点）；**否则在 `savefig`/`show` 前插入一段自包含的 `auto layout` 块**。两条通道都要过三道验证（**语法可解析 → 子进程重跑 → 语义比对**），任一不过 **默认回滚**；写回**原子落地**（临时文件 + rename），备份在 `.tweak_params/fig1.tweak.bak`，**幂等**（同一参数重复写回不产生新差异）。`--keep-unverified` 可保留未验证结果。

> 想看机器可读结果：`--json` 给 `files[].diff`（逐行 `{line,type,old,new}`）、`preview_sha256`（与真实写盘文件 sha256 一致）、`stage`（卡在哪一步）、`next_step`、`attribution`。契约见 [docs/agent.md](https://github.com/shdbl/mpltweak/blob/main/docs/agent.md)。

> 卡住了见 [docs/troubleshooting.md](https://github.com/shdbl/mpltweak/blob/main/docs/troubleshooting.md)（拖了没反应 / 拖了弹回去 / 报错怎么办，每条都是「现象 → 一句话原因 → 怎么做」）。
> 完整输出样张、逐处清单 `-v`、`--style inplace` / `--style block`、参数字段表 —— [docs/verify.md](https://github.com/shdbl/mpltweak/blob/main/docs/verify.md) 与 [docs/params.md](https://github.com/shdbl/mpltweak/blob/main/docs/params.md)。

## License

MIT

---

**要看完整版：** 键位与手势 [docs/interaction.md](https://github.com/shdbl/mpltweak/blob/main/docs/interaction.md) · 参数文件字段表 [docs/params.md](https://github.com/shdbl/mpltweak/blob/main/docs/params.md) · `--json` 冻结核对契约 [docs/json-contract.md](https://github.com/shdbl/mpltweak/blob/main/docs/json-contract.md) · 三道验证与备份 / `revert` [docs/verify.md](https://github.com/shdbl/mpltweak/blob/main/docs/verify.md) · 卡住了 [docs/troubleshooting.md](https://github.com/shdbl/mpltweak/blob/main/docs/troubleshooting.md) · 实测数字 [docs/reliability.md](https://github.com/shdbl/mpltweak/blob/main/docs/reliability.md) · 边界 [docs/boundaries.md](https://github.com/shdbl/mpltweak/blob/main/docs/boundaries.md) · 执行边界与退出码 [docs/exec-boundaries.md](https://github.com/shdbl/mpltweak/blob/main/docs/exec-boundaries.md) · 给 Agent 的手册 [docs/agent.md](https://github.com/shdbl/mpltweak/blob/main/docs/agent.md) 与 [skills/mpltweak/SKILL.md](https://github.com/shdbl/mpltweak/blob/main/skills/mpltweak/SKILL.md) · 开发与跑测试 [docs/dev.md](https://github.com/shdbl/mpltweak/blob/main/docs/dev.md)
