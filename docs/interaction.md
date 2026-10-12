# 交互速查

## 键位速查

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

## 工作流

```mermaid
flowchart TD
    A["fig1.py<br/>纯 matplotlib，一行都不用改"]:::script
    B(["交互窗口<br/>拖 · 对齐 · 吸附 · 调字号"]):::win
    C[".tweak_params/fig1.json<br/>参数静默落盘，代码没动"]:::script
    D["AST 定位 → 原位改写画图参数"]:::script
    E{"三层验证"}:::check
    F(["提交改动 + 留备份"]):::ok
    G(["自动回滚，不会留下改坏的脚本"]):::bad

A -->|mpltweak fig1.py| B
    B -->|关窗| C
    C -->|mpltweak apply --write| D
    D -->|自动比对| E
    E -->|通过| F
    E -->|失败| G

classDef script fill:#F7F8FA,stroke:#C9D1DB,stroke-width:1.4px,color:#16181D
    classDef win fill:#2F6FEB,stroke:#2F6FEB,stroke-width:1.6px,color:#FFFFFF
    classDef check fill:#F5F8FF,stroke:#2F6FEB,stroke-width:1.6px,color:#16181D
    classDef ok fill:#EAF7EF,stroke:#2E9E5B,stroke-width:1.4px,color:#16181D
    classDef bad fill:#FEF3E2,stroke:#D97706,stroke-width:1.4px,color:#16181D
```

从打开窗口到 `mpltweak apply --write`，中间任何一步（包括关窗之后）脚本一个字符都不会变；
写回是你在第三步显式点头后的动作。

## 窗口后端：Qt（推荐）还是 Tk（默认可用）

开窗必须有一个图形后端。mpltweak 依次尝试 **QtAgg（需要 PyQt5）→ Qt5Agg → Qt6Agg（PyQt6 / PySide6）→ TkAgg（需要 `tkinter`）**，用第一个 import 得动的：

| 你的环境 | 实际用的后端 | 体验 |
|---|---|---|
| `pip install "mpltweak[qt]"` | Qt（QtAgg + PyQt5） | **推荐**：交互最全；**真实开窗实测**走的就是 Qt 路径（自动化套件用离屏 / Agg 合成事件，不覆盖真窗口） |
| 只 `pip install mpltweak`（系统有 `tkinter`） | **Tk（TkAgg）** | 能开窗、能拖，功能基本都在，但重绘与悬停提示是弱化版 |
| 一个都没有 | —— | 开窗 `rc=4` 并给出装哪个的提示（见下方注） |

> 一个后端都没有时（典型：Linux 服务器没装 `python3-tk`）：开窗 `rc=4`，并提示
> `pip install "mpltweak[qt]"`（推荐），或"Linux 也可以用系统包管理器装 `python3-tk`"。
> **纯 CLI 用法（`describe` / `check` / `apply`）一个图形后端都不需要**；`mpltweak mcp` 走 stdio，同样不需要。

不确定现在用的是哪个：

```bash
mpltweak doctor        # 打印 backend 探测（QtAgg / TkAgg 哪个 OK）与 Qt 绑定
```

想要开窗就补一句：

```bash
pip install "mpltweak[qt]"
```

> 命令不在 PATH 时（例如用 `pip --user` 装的），把本文的 `mpltweak` 换成
> `python -m mpltweak` 即可，用法完全一样。

---

回退到 Tk 时工具会在 **stderr** 打一句人话提示（`MPLTWEAK_NO_HINT=1` 可静默），并按顺序
QtAgg → Qt5Agg → Qt6Agg → TkAgg 逐个真建 canvas 探测；`doctor` 会多报一行当前开窗后端
（实测：`开窗后端   : Tk（未装 Qt；想要 Qt 界面：pip install "mpltweak[qt]"）`）。

> 命令不在 PATH 时（例如用 `pip --user` 装的），把本文的 `mpltweak` 换成 `python -m mpltweak` 即可，用法完全一样。

## 如果没反应

一般用不到这一节；遇到异常再对照下表。

| 现象 | 处理 |
|---|---|
| 没弹窗 | 运行 `mpltweak doctor` 看诊断 |
| 敲了 `mpltweak launch fig1.py` | 现在这两种写法等价（`launch` 是显式子命令），参数与 `mpltweak fig1.py` 完全相同 |
| 用 `--params` 写回过，`revert` 却说"还没有可回退的备份" | 备份放在**参数文件旁边**，而 `revert` 默认只找 `.tweak_params/`；把同一个 `--params` 也传给 `revert` |
| 想从头调 | 删掉 `.tweak_params/fig1.json`（不删就是接着上次续调） |
| 多图脚本 | 每张图都会弹窗，**改哪张记哪张**；`mpltweak apply` 逐张写回 |
| 脚本要读数据 / 跑很久 | 先试 `--timeout 600` **放宽**等待。`--no-verify` 会**完全跳过重跑验证**——那是写坏代码时唯一的自动安全网，只在脚本确实无法在无头环境重跑时才用，且用前请确认脚本已在版本控制里 |
| **拖动特别卡** | 按 **空格** 切线框模式：只画边框、坐标轴和文字，**不渲染数据图元**（cartopy 大图实测 494ms → 169ms），挪完再按一次空格恢复 |
| 手动改过代码后再开窗 | 会自动**不套用**上次参数（避免静默覆盖你的改动）并给出提示；确要接着上次调，加 `--force-resume` |
| 脚本里开着 `constrained_layout` / `autolayout` | 开窗与写回时会提示（**带行号**）：布局引擎在每次绘制时重算轴位置，拖拽/写回的位置会被它覆盖。调图前先关掉（或把要精调的轴改成 `add_axes([...])`，引擎不管这类轴） |
