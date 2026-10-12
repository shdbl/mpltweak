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

这 7 个 `tests/test_*.py` 是**模块级脚本**（逐个直接跑，和 CI 一致）；另有 `pytest -q` 包装入口
（七套件各一条 + 卫生棘轮）。细节见 CONTRIBUTING。

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
