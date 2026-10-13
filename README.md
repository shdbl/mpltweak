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

## 目录

- [1. 它解决什么问题](#1-它解决什么问题)
- [2. 演示](#2-演示)
- [3. 5 分钟上手](#3-5-分钟上手)
- [4. 安装 + 快速开始](#4-安装--快速开始)
- [5. 给 Agent 用](#5-给-agent-用)
- [6. 工作流](#6-工作流)
- [7. 它可靠吗](#7-它可靠吗)
- [8. 键位速查](#8-键位速查)
- [9. 原位改数字，还是插入调整块](#9-原位改数字还是插入调整块)
- [10. 三层验证](#10-三层验证)
- [11. 执行边界：哪些命令会跑你的脚本](#11-执行边界哪些命令会跑你的脚本)
- [12. 你这个脚本能帮上吗？两个问题](#12-你这个脚本能帮上吗两个问题)
- [13. 参数文件](#13-参数文件)
- [14. 开发](#14-开发)
- [15. License](#15-license)

---

## 1. 它解决什么问题

调一张多面板图（multi-panel figure）时，麻烦通常出在排版而不是数据：挪一下子图，旁边的轴标签就被挡住；字号放大一点，图例又压到曲线上。而每改一次，都要重跑脚本、再看一遍图。

**mpltweak 的做法**：像在 PPT 里摆放图片那样直接拖，拖完把那几个数字写回源码。

| 方式 | 流程 |
|---|---|
| **传统方式** | 改一个数字 → 重跑脚本 → 等几十秒 → 看 PNG → 还是不对 → 再改 |
| **mpltweak** | 打开窗口 → 拖到位 → `mpltweak apply --write` → 那几个数字回到代码里 |

**写回是确定性的。** AST 精确定位；写回分两种方式：**代码里写的是字面量时，直接改那几个数字**（最干净，不插入任何东西）；**否则插入一个自包含的调整块**（不加 import、不改动你原有的逻辑）。两种方式都过三层验证（语法 / 重跑 / 语义），任一层不过**默认**回滚到批前状态（`--keep-unverified` 可保留，需自己验证），备份留在 `.tweak_params/<脚本名>.tweak.bak`。

**要动源码，就必须做到确定性** —— 这是 mpltweak 的设计前提。

**它只管样式和位置，不发明内容** —— 文字、数据、图形仍完全由你的代码决定。

> 如果你是用 Agent 写图的（让 agent 来读排版 / 出计划 / 写回），直接看 [给 Agent 用](#5-给-agent-用)。

---

## 2. 演示

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

**操作手势（与 PPT 类似）：** 拖动面板、Ctrl 多选、对齐 / 均分（Ctrl+Shift+L/R/T/B/C/M、H/V）、边缘吸附（带参考线）、Ctrl+F 适配画布裁白边、空格切线框模式、悬停改字号、图例吸附 8 个标准位、colorbar 调长度 / 厚度。

---

## 3. 5 分钟上手

下面所有命令都在**终端**里执行 —— Windows 用 cmd / PowerShell，macOS / Linux 用 Terminal，
在 PyCharm 里则是底部的 **Terminal** 面板（**不是** Python Console / 交互式解释器）。
调图前先 `cd` 到脚本所在目录。

```bash
cd 你的文件夹          # ← 换成你放 fig1.py 的那个目录（整行可复制）
```

**它会做什么（先说清楚）**：接下来它会**把你的脚本跑一遍**，然后把图显示在一个可以拖动的窗口里。跑的时候用的是你自己的环境、你自己的数据；**mpltweak 自己不联网，也不会改你的文件** —— **关掉窗口之前，你的 `.py` 一个字符都不会变**。拖出来的参数先落在 `.tweak_params/fig1.json`；要不要写回、什么时候写回，由后面那两条命令决定。（装哪个库、以及开窗相关的名词与排查，见下面「安装 + 快速开始」的「窗口后端」一节与 [docs/interaction.md](https://github.com/shdbl/mpltweak/blob/main/docs/interaction.md)。）

第一次用**不用读完这份 README**，照着下面走一遍就能看到效果。示例脚本 `fig1.py` 就是后面「安装 + 快速开始」第 1 步那段代码 —— **先把它存成 `fig1.py`**，然后：

| 步 | 命令 | 你会看到 |
|---|---|---|
| **1 · 装** | `pip install mpltweak`<br>`pip install "mpltweak[qt]"` | 核心包 + 让图能在窗口里打开的那个图形库（装哪个、什么区别见下面「窗口后端」）；只跑命令行（`describe` / `check` / `apply`）不用装第二条 |
| **2 · 开窗拖** | `mpltweak fig1.py` | 调图窗口：**拖**面板移动、拖**边 / 角**缩放、鼠标**悬停文字**按 `+` `-` 调字号、**空格**切线框提速、**Ctrl+F** 裁白边；按 **`?`** 出全部键位 |
| **3 · 关窗** | —— | 脚本**一个字符都没变**；参数落在 `.tweak_params/fig1.json`（没调好就再开窗接着调）。**真拖过东西**的话，关窗时终端会直接给你下一条可复制的命令（`mpltweak apply fig1.py --write`），不用记 |
| **4 · 看它打算改什么** | `mpltweak apply fig1.py` | 逐轴列出位置与字号，并说明**改哪个数字 / 要不要插调整块 / 会不会拒绝**；只读，不执行脚本、不写文件 |
| **5 · 写回** | `mpltweak apply fig1.py --write` | **三段式：预览（只读，不执行脚本）→ 你确认 → 写回**；写回**自动备份**（`.tweak_params/fig1.tweak.bak`）、成功**只报一行**（`已原位写回 N 处；图已更新`，要看逐处清单加 `-v`）、**不满意一条命令就撤回**（`mpltweak revert fig1.py --write`）。**只改数字**那一类的真实样张见「安装 + 快速开始」第 4 步；其余细节见下面两段 |
| **6 · 重跑看图** | `python fig1.py` | 图就是你拖完的样子；不满意：`mpltweak revert fig1.py --write` 退回写回前（先不带 `--write` 是预览） |

**一次真实写回**的输出样张在下面「安装 + 快速开始」的第 4 步 —— 那是"只改数字"那一种：你实际看到几个轴、几处改动取决于你的脚本；如果脚本里还有源码没写到的字段（比如线宽 / 颜色），它会在文件末尾再追加一段自包含调整块，输出里会说明，并给出备份路径。

**写回是三段式**：`mpltweak apply fig1.py` 是**预览**（只看，不执行脚本、不写文件）→ 你看完自己决定 → 才跑 `--write` 那一条。写回时它**自动备份**（`.tweak_params/fig1.tweak.bak`）、成功后**只报一行**（写完才知道几处，所以那行里有"已原位写回 N 处；图已更新"；逐处清单 / 备份路径 / 分层结论用 `-v` 展开）、**不满意一条命令就撤回**：`mpltweak revert fig1.py --write`（不带 `--write` 是预览）。

**图是用 `plt.subplots()` / `GridSpec` 画的？** 源码里往往没有能直接改的数字 —— 那它会在文件末尾**追加一段带标记的代码**（自包含调整块），**不满意 `mpltweak revert fig1.py --write` 一键删掉**；你原来写的那些代码一行都没被改写。

**没看到窗口？** 按顺序自查三件事：

1. 脚本末尾是不是有 `input()` / 要等你输入？开窗或重跑都会卡在那儿 —— 先临时注释掉那一行。
2. 脚本要参数吗（平时敲的是 `python fig1.py --data x.npz` 那种）？命令后面用 `--` 把参数原样带上：`mpltweak fig1.py -- --data x.npz`。
3. 还不行：把终端里的输出**整段**贴给你的 Agent，问一句"这是什么问题"。

三条都不中，见 [docs/troubleshooting.md](https://github.com/shdbl/mpltweak/blob/main/docs/troubleshooting.md)（拖了没反应 / 拖了弹回去 / 报错怎么办，每条都是「现象 → 一句话原因 → 怎么做」）。

**还没动手就想知道你这脚本能不能用？** 先看「[12. 你这个脚本能帮上吗？两个问题](#12-你这个脚本能帮上吗两个问题)」—— 回答两个问题就有结论。

---

## 4. 安装 + 快速开始

下面所有命令都在**终端**里执行 —— Windows 用 cmd / PowerShell，macOS / Linux 用 Terminal，
在 PyCharm 里则是底部的 **Terminal** 面板（**不是** Python Console / 交互式解释器）。
装之前先 `cd` 到脚本所在目录：

```bash
cd 你的文件夹          # ← 换成你放 fig1.py 的那个目录（整行可复制）
```

```bash
pip install mpltweak            # 核心：describe / check / apply（mcp 是可选适配层，见「给 agent 的通道」）
pip install "mpltweak[qt]"      # 要**开窗拖图**再加这个（装 PyQt5 图形后端）
```

开窗需要一个图形后端（推荐 PyQt5，约 +150MB），所以分成两步 —— 服务器 / headless / 纯 agent 场景只用 `describe` / `check` / `apply`，不该为此装一整套 Qt（有些环境本来也已经装了 PySide6 / PyQt6）。Windows / macOS 的官方 Python 安装包自带 `tkinter`，所以只装 `mpltweak` 一般就能直接开窗，此时用的是 **Tk 后端（交互弱化版）**，不是安装失败、也不是没装好；一个图形后端都没有时开窗 `rc=4` 并给出装哪个的提示。后端对照、探测顺序与常见问题见 [docs/interaction.md](https://github.com/shdbl/mpltweak/blob/main/docs/interaction.md)，命令执行边界见 [docs/exec-boundaries.md](https://github.com/shdbl/mpltweak/blob/main/docs/exec-boundaries.md)。

### 窗口后端：Qt（推荐）还是 Tk（默认可用）

装 `mpltweak[qt]` 走 Qt（交互最全）；只装核心包时用 Tk（弱化版，能开窗但手势不全）；两个后端都没有时开窗直接 `rc=4`，并提示该装哪一个。
后端对照表、各平台装法与探测顺序见 [docs/interaction.md](https://github.com/shdbl/mpltweak/blob/main/docs/interaction.md)。

### 1 · 你原来的脚本，一行都不用改

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

不需要 `import mpltweak`，不需要任何钩子。

### 2 · 打开调图窗口

```bash
mpltweak fig1.py
```

> 子命令：`launch` / `apply` / `revert` / `describe` / `check` / `schema` / `mcp` / `doctor`。
> `mpltweak fig1.py` 与 `mpltweak launch fig1.py` 等价；除此之外，第一个参数一律按**脚本路径**解释。

窗口里：**拖**面板移动、拖**边 / 角**缩放、**Ctrl 加选**多个面板 → `Ctrl+Shift+L` 左对齐 / `Ctrl+Shift+V` 垂直均分、鼠标**悬停文字**按 `+` `-` 调字号、**空格**切线框（大图提速）、**Ctrl+F** 裁掉白边。按 **`?`** 随时打开键位帮助页；键位表与工作流图见 [docs/interaction.md](https://github.com/shdbl/mpltweak/blob/main/docs/interaction.md)。

**调完直接关窗。** 此时脚本一个字符都没变，参数落在 `.tweak_params/fig1.json`。

### 3 · 先看它打算改什么（只读）

```bash
mpltweak apply fig1.py
```

打印每个轴的位置、字号清单，不写任何文件。

### 4 · 写回

**写回是三段式：预览（只读，不执行脚本）→ 你确认 → 写回。** 写回前**自动备份**（`.tweak_params/fig1.tweak.bak`）；成功后**只报一行**（几处 + 图已更新）；**不满意一条命令就撤回**：`mpltweak revert fig1.py --write`（不带 `--write` 是预览）。

```bash
mpltweak apply fig1.py --write
```

```text
  正在验证（子进程重跑脚本，最长 300s）
[OK] 已原位写回 3 处（fig1.py）；图已更新（重跑 + 语义验证通过）
```

上面是**真实一次写回**的输出（路径省了目录前缀；你看到的会是绝对路径）；其中"重跑 / 语义验证"是在**你的环境**里重跑脚本得到的（跑不起来或语义不符都会回滚，见「三层验证」）。

要看**逐处清单 / 备份路径 / 验证分层结论**，加 `-v`（成功路径的细节都在这里；失败与降级信息不加 `-v` 也会打）：

```text
[OK] 已原位写回: fig1.py
  原位修改 3 处: ax0.pos, ax1.pos, ax2.pos
  备份: .tweak_params/fig1.tweak.bak
  [OK] Agg 重跑验证通过
  [OK] 语义验证通过（目标图状态 == 参数）
```

**脚本自己带参数时**（依赖 `sys.argv`，如 `python fig1.py --data x.npz`）：把参数放在 `--` 后面，写回的重跑验证会原样透传给脚本，保证与基线一致、不误回滚：

```bash
mpltweak apply fig1.py --write -- --data x.npz --level 3
```

图是 `plt.subplots()` / `GridSpec`（或开了 `constrained_layout`）画的？源码里通常没有能直接改的数字 —— 那它会在文件末尾**追加一段带标记的代码**（自包含调整块），**不满意 `mpltweak revert fig1.py --write` 一键删掉**，你原有的代码一行都没被改写。

代码里写的是字面量时，它只改 `add_axes([...]` 里那几个数字；否则插入一个自包含的调整块。任何一步验证不过就自动回滚。

### 5 · 重跑看图

```bash
python fig1.py
```

### 如果没反应

没弹窗、写回后没变化、参数从哪来：先看 [docs/interaction.md](https://github.com/shdbl/mpltweak/blob/main/docs/interaction.md) 的「如果没反应」（含 `doctor`、`--timeout 600`、`--force-resume`、布局引擎冲突等逐条对照表）。

卡住了看 [docs/troubleshooting.md](https://github.com/shdbl/mpltweak/blob/main/docs/troubleshooting.md)（三个最常见的问题：拖了没反应 / 拖了弹回去 / 报错怎么办，每条都是「现象 → 一句话原因 → 怎么做」）。

---

## 5. 给 Agent 用

**你不需要懂命令行 —— 把这件事交给 Agent 就行。**（自己敲命令也行：拖一下就到位，**不用手写那些位置数字**。）
（**要能直接读写你电脑上文件的那种 Agent**；只在网页聊天窗口里聊天的，做不到。）

把下面这条链接发给你正在用的 Agent（Claude / ChatGPT / Cursor / DeepSeek 等，只要它能读写你电脑上的文件），
它自己就会照着这份手册把环境配好、帮你调图：

- **给 Agent 的操作手册**：https://github.com/shdbl/mpltweak/blob/main/skills/mpltweak/SKILL.md
  （Agent 不能联网时：让它打开仓库里的 `skills/mpltweak/SKILL.md`，或者把那份文件的内容直接贴给它。）

### 首推流程：六步（照着跟 Agent 说就行）

1. **让 Agent 帮你画图。** 你只用说你要什么（"把这两组数据画成对比柱状图"），脚本和图都由 Agent 来写、来跑。
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
   *你会看到*：重跑出来的新图 + 它改了哪几处；不满意可以接着调，或者让它退回上一次（`revert`）。

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

**技术指针（都在仓库的 `docs/` 里）**：MCP（可选适配层）与 JSON 契约、**读 → 改 → 写 → 查的三行命令**、四个子命令的作用表与退出码口径、MCP 四个工具的参数签名、**人 + agent 的几种分工**，以及**排版前要先看的布局引擎冲突**，都在 [docs/agent.md](https://github.com/shdbl/mpltweak/blob/main/docs/agent.md)。

---

## 6. 工作流

```mermaid
flowchart TD
    A["fig1.py<br/>纯 matplotlib，一行都不用改"]:::script
    B(["交互窗口<br/>拖 · 对齐 · 吸附 · 调字号"]):::win
    C[".tweak_params/fig1.json<br/>参数静默落盘，代码没动"]:::script
    P["只读预览：<br/>原位改哪几个数字 / 要不要插块 / 会不会拒绝"]:::check
    D{"逐字段判定"}:::check
    D0(["【等值】no_change<br/>一个字节都不动"]):::ok
    D1["【原位】只替换那几个数字<br/>其余逐字节不变"]:::script
    D2{"插入点合规吗？<br/>（在最后一个 savefig / show 之前）"}:::check
    D2b{"块里的目标对象<br/>能静态证明吗？<br/>（用变量名，不用 fig.axes[i]）"}:::check
    D3["【追加】插入自包含调整块"]:::script
    D4["【拒绝】一个字节都不动<br/>说明原因 + 打印可粘贴片段"]:::bad
    PASTE(["你手动粘贴那几行<br/>（把占位符换成自己的变量名）"]):::win
    E{"验证<br/>默认：语法 + 重跑 + 语义<br/>--verify-fast：只 ast.parse"}:::check
    F(["提交改动 + 留备份"]):::ok
    G(["自动回滚到批前状态"]):::bad
    R(["revert：回到写回前的原件"]):::ok

    A -->|mpltweak fig1.py| B
    B -->|关窗| C
    C -->|apply（不带 --write）| P
    P -->|你确认后 apply --write| D
    D -->|目标值 == 源码现值| D0
    D -->|有字面量| D1
    D -->|没有| D2
    D2 -->|不合规| D4
    D2 -->|合规| D2b
    D2b -->|能证明| D3
    D2b -->|证不出| D4
    D1 --> E
    D3 --> E
    E -->|通过| F
    E -->|失败| G
    D4 --> PASTE
    PASTE -.->|回头再跑一次只读预览复核| P
    F -.->|后悔了| R

    classDef script fill:#F7F8FA,stroke:#C9D1DB,stroke-width:1.4px,color:#16181D
    classDef win fill:#2F6FEB,stroke:#2F6FEB,stroke-width:1.6px,color:#FFFFFF
    classDef check fill:#F5F8FF,stroke:#2F6FEB,stroke-width:1.6px,color:#16181D
    classDef ok fill:#EAF7EF,stroke:#2E9E5B,stroke-width:1.4px,color:#16181D
    classDef bad fill:#FEF3E2,stroke:#D97706,stroke-width:1.4px,color:#16181D
```

从打开窗口到 `mpltweak apply --write`，中间任何一步（包括关窗之后）脚本一个字符都不会变；
写回是你在第三步显式点头后的动作。

---

## 7. 它可靠吗

**只列实测数字，每个数字带分母**；数字按日志行号回填，**不用估算值填空位**。完整版（口径、逐族表、块通道、窗口交互）在 [docs/reliability.md](https://github.com/shdbl/mpltweak/blob/main/docs/reliability.md)。

| 层 | 分母（口径） | 实测 |
|---|---|---|
| ① 静态层 · 真实绘图代码语料 | 10,158 份公开绘图脚本（静态判定，不执行） | **97.8% 原位落进源码（3,531 / 3,610）**；承诺违约 **0** |
| ② 运行时层 · 写回后重跑验证 | 池 **1,837** → 真跑候选 **1,780** | **928 份写回并验证通过**（四条铁律逐律 928/928）；承诺违约 **0** |
| ③ 窗口交互 · 离屏矩阵 | **12 种图形 × 1,000 次随机操作**（**声明口径：12 种图形 × 1,000 次 = 12,000 次**；⑫"多 figure 脚本"含两张子图、各 1,000 次 ⇒ **实跑 13,000 次**） | **两编码均 ALL PASS**（UTF-8 414.3s / GBK 466.8s）；专项 **356 适用 · 356 通过 · 0 失败**；每次操作后校验不变量，**失败 0** |
| ③ 窗口交互 · 真窗口 | 6 种图形 × 1,000 次 = **6,000** 次手势 + **27 条硬断言** | **27/27 全部通过**（Qt 轮 25/25、Tk 轮 2/2） |

- 静态层是**真的把公开绘图脚本拿来做定位与改写判定**：合计 4,101 个站点里 3,531 个真原位写回、7 个故意拒绝、37 个参数非法被拒、3 个改走追加块 —— **没有一次"声称改了却没改"**。
- 交互层的判据不是"看起来对"：随机操作下核对**不崩 / 往返一致 / 几何合法 / 落位与参数一致**，用离屏矩阵（实跑 13,000 次，每次操作后都验）与真窗口（6,000 次、**27 条硬断言全部通过**）两层跑；47 条判据不适用的情况逐条带理由，断言机器本身也做过 6/6 灵敏度自检。

---

## 8. 键位速查

| 操作 | 作用 |
|---|---|
| **`?`** | **打开 / 关闭键位帮助页**（画布内浮层：Esc 关闭，L 切换中英文） |
| 拖面板 / 拖边框、角 | 移动 / PPT 式缩放（对边固定；锁长宽比的轴自动等比） |
| **Ctrl+点击**（或 Shift+点击） | 多选加选 / 减选；拖空白处橡皮筋框选 |
| **Ctrl+Shift+L/R/T/B/C/M** | 对齐：左 / 右 / 上 / 下 / 水平居中 / 垂直居中 |
| **Ctrl+Shift+H / V** | 均分：水平 / 垂直（两端不动，中间等间距） |
| **Ctrl+Z** / **Ctrl+Y**（或 Ctrl+Shift+Z） | 撤销 / 重做 |
| 悬停文字后 `+` / `-` | 标题、轴标签、刻度、图例、colorbar 的字号 |
| **空格** | 线条模式（只留边框与文字，隐藏数据图元） |
| **Ctrl+F** | 适配画布到内容（裁掉四周白边，子图像素不变） |
| **方向键** / Shift+方向键 | 移动面板（PPT 习惯）/ 微调尺寸（中心不动） |
| 拖 colorbar 长轴端点 / 细条边 / 中点 | 调长度 / 调厚度 / 整条平移 |
| 拖图例 | 8 个标准位预览 + 松手吸附 |
| `[` `]` · `c` · `C` · `g` · `s` · `x` `y` | 线宽 / 线色 / colormap / 网格 / 边框 / 线性对数轴 |
| `n` · `e` | 吸附开关 / 手动导出 |
| 拖窗口边缘 | 改画布尺寸（写回 `figsize`） |

---

## 9. 原位改数字，还是插入调整块

写回有**两条路**：源码里写的是**字面量** → **直接改那几个数字**；位置来自 `plt.subplots()` / `plt.subplot(1,2,n)` / `GridSpec`（源码里本来就没有数字）→ **插入一个自包含的调整块**。**能不能插块，取决于目标能不能静态证出**：证不出就 `applied=false`、**不写盘**，只给你一份可粘贴的手改片段。

以 `fig1.py` 为例。**动手前**（手写的数字，通常都不齐）：

```python
ax1 = fig.add_axes([0.08, 0.66, 0.30, 0.21])
ax2 = fig.add_axes([0.11, 0.38, 0.30, 0.21])
ax3 = fig.add_axes([0.05, 0.08, 0.30, 0.21])
```

**你拖完之后**（`mpltweak apply --write` 的结果）：

```diff
-ax1 = fig.add_axes([0.08, 0.66, 0.30, 0.21])
+ax1 = fig.add_axes([0.08, 0.655, 0.300, 0.215])
-ax2 = fig.add_axes([0.11, 0.38, 0.30, 0.21])
+ax2 = fig.add_axes([0.08, 0.380, 0.300, 0.215])
-ax3 = fig.add_axes([0.05, 0.08, 0.30, 0.21])
+ax3 = fig.add_axes([0.08, 0.085, 0.300, 0.215])
```

代码里写的就是字面量，所以**只动了这几个数字**：没有新增一行、没有加 `import`、没有引入任何依赖。字号、图例位置、网格、colorbar 同理，改的都是你代码里原本就有的参数。

位置来自 `subplots` / `GridSpec` 的那类图，插的是这样一段（2×2 网格的一次真实写回；头部两行与结尾标记行逐字照抄，中间重复的同类行用 `# …` 省略）：

```python
# ===== auto layout (generated) - do not edit inside =====
# 本块由 mpltweak 自动生成于 2026-10-12 19:42（可手动微调，勿删上下两行标记）
axes[0, 0].set_position([0.1450, 0.5500, 0.3723, 0.3700])
axes[0, 0].title.set_fontsize(14)
axes[0, 0].lines[0].set_linewidth(1.5)
# … 其余三条轴各自同样几行（位置 / title / lines）
# ===== end auto layout =====
```

上下两行标记是**真写进你脚本的**，`grep auto layout` 就能定位；块是**自包含**的（不加 `import`、不改你原有的逻辑），重复写回会**整体替换**它。想只走一条路：`--style inplace`（一处都不插块，写不了的字段 `rc=1` 明确失败）或 `--style block`（一律插块）。**块内为什么这么写、"整份状态被钉进源码"这类取舍，见 [docs/verify.md](https://github.com/shdbl/mpltweak/blob/main/docs/verify.md)。**


---

## 10. 三层验证

1. **语法层**（`layers.syntax`）：写盘后无条件 `ast.parse`，原位与块两条路径都做；语法错误当场回滚到批前状态。
2. **重跑层**（`layers.rerun`）：用 Agg 无头重跑；退出码非 0 时区分"脚本本身跑不通"与"写回把它改坏了"。
3. **语义层**（`layers.semantic`）：重跑后 dump 目标图状态，与参数里的位置 / 字号 / `grid` / `spines` / `clim` 等逐字段比对；判定不了时 `verified=null`，`detail` 里列出未判定字段。

任一层不过即回滚到批前状态；写回前的原文备份在 `.tweak_params/<脚本>.tweak.bak`。逐层细节与 `--verify-fast` / `--keep-unverified` 的关系见 [docs/verify.md](https://github.com/shdbl/mpltweak/blob/main/docs/verify.md)。

### 加速与门禁：`--verify-fast` 与 `--strict`

**`--verify-fast`（加速）**：只做写盘后的 `ast.parse`，所以快；它不证明脚本能跑通、也不证明布局真的落到目标图上（JSON 里 `verify_mode: "fast"`）。
**`--strict`（门禁）**：`check --strict` 把布局引擎冲突（`constrained_layout` / `autolayout`）从提示升级为问题（`rc=1`）；`apply --write --strict` 遇到冲突拒绝写回（要放行加 `--allow-layout-conflict`）。`check --json` 的 `ok` 字段 + 退出码可直接当 CI 用例；`check` 一次只接受一个脚本，批量要自己循环。
其余细节（`--no-verify` 的边界、`bbox_inches='tight'` 的降级、`--tol` 只收有限正数、`--static` 静态门禁、批量脚本示例）见 [docs/verify.md](https://github.com/shdbl/mpltweak/blob/main/docs/verify.md)。

### 后悔了：`mpltweak revert`

写回错了、或者拖完发现还是原来那版好看？不用去翻备份：

```bash
mpltweak revert fig1.py            # 只预览：会改回哪几行（默认，不动代码）
mpltweak revert fig1.py --write    # 真的回退到"上次写回之前"
```

它用的就是**参数文件旁边**那份备份（默认 `.tweak_params/<脚本>.tweak.bak`，每次
`apply --write` 之前自动留的）。写回时若用了 `--params`，回退也要带上同一个 `--params`，
否则会报 `[revert] 还没有可回退的备份`（`rc=2`）。
回退的是**最近一次**写回 —— 备份每次写回都被覆盖，反复写回后再 revert 回不到最早的原文。
**revert 自身也可逆**：覆盖前会把当前版本另存为 `<脚本>.revert.bak`（也在参数文件旁边），
所以手滑了还能再回去。
恢复是**字节级原样复制**（BOM / 换行 / 编码都不动）。

---

## 11. 执行边界：哪些命令会跑你的脚本

`describe` / `check` / 开窗会**执行**你的脚本（都在**子进程**里、Agg 无头；退出码与全局状态都留在子进程内）；`apply` 只做 AST 改写，除非你要求**重跑验证**（`--write` 后默认重跑一次，`--verify-fast` 只做 `ast.parse`）。

退出码语义、脚本自己 `sys.exit()` 的影响、`--strict` 的 CI 用法，见 [docs/exec-boundaries.md](https://github.com/shdbl/mpltweak/blob/main/docs/exec-boundaries.md)。

**写回前先做一次「可重跑预检」（0.1.10）**：默认要重跑验证时，`apply --write` 与开窗入口会**静态**看一眼
脚本（**不执行**），命中「缺依赖 / 需要参数 / 联网 / 等人输入 / 要弹窗」时给一段提示 + 一句可照抄的下一步
（`--json` 里是顶层 `preflight.risks[]`）。干净脚本一个字都不说；预检自己出岔子就放行，绝不影响写回与开窗。
五类判据与 JSON 字段见 [docs/exec-boundaries.md](https://github.com/shdbl/mpltweak/blob/main/docs/exec-boundaries.md)。

---

## 12. 你这个脚本能帮上吗？两个问题

**先回答两个问题**：

1. **你的脚本能不能"没人管"地自己跑完？** 不用你点、不用你输、不靠屏幕上的窗口 —— 写回时的验证会在**无头环境**里**真的跑你的脚本**，旁边没有人在帮它。
2. **要联网、要输密码、要读很大的文件吗？** 有就别用写回。

两个都过得去，它多半帮得上。**真正的门槛只有一条：能不能在无头环境里无人值守跑通。**

| 脚本特征 | 会怎样 |
|---|---|
| 要读大数据 / 依赖重环境 / 要联网或凭据 | 无头重跑会卡住或失败 → 写回被回滚 |
| 中途需要人工输入（`input()`、交互式选择） | 无头重跑直接堵死 → 写回被回滚 |
| 需要在特定交互后端才有意义的图 | 验证固定用 Agg 无头后端 |

这类脚本走只读路线（`mpltweak describe` / `mpltweak check`，都不跑写回），或者手动改代码。
它的目标用户是**本地跑得动的小中型科研绘图脚本**。

> 验证的机制细节见 [docs/verify.md](https://github.com/shdbl/mpltweak/blob/main/docs/verify.md)。
> 门槛与口径的集中版（不适用场景 / 执行边界 / 退出码 / 验证的不声称项 / 语料口径）在 [docs/boundaries.md](https://github.com/shdbl/mpltweak/blob/main/docs/boundaries.md)。

---

## 13. 参数文件

参数文件是**公开格式**，可以手工改，也可以让 Agent 直接生成，`--write` 一样能落实。顶层 `version` 标记格式版本（当前为 4）：

```jsonc
{ "version": 4, "script": "fig1.py",
  "axes": [ { "index": 0, "pos": [0.048, 0.655, 0.30, 0.215], "title_fontsize": 9.0 } ] }
```

**只写显式给出的字段**：参数里没写的字段不会写回。字段表（`figsize_px` / `figsize_in` / `aspect_locked` / `cell` / `clim` / `cmap` / `lines` / `legend` / `tick_fontsize_y` …）与两条约定（`axes[i]` 顺序、colorbar 槽轴不占序号）见 [docs/params.md](https://github.com/shdbl/mpltweak/blob/main/docs/params.md)。

---

## 14. 开发

仓库结构：`src/mpltweak/`（`cli.py` / `launch.py` / `toolbox.py` / `params.py` / `apply.py` / `writeback.py` / `verify.py`）。跑测试：`python tests/test_writeback.py`（AST 写回 + 三层验证）。其余（安装、全部测试命令、模块职责、嵌入 API `from mpltweak.toolbox import gaitu`）见 [docs/dev.md](https://github.com/shdbl/mpltweak/blob/main/docs/dev.md)。

---

## 15. License

MIT
