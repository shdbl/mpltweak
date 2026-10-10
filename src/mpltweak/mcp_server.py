# -*- coding: utf-8 -*-
"""mpltweak MCP server —— 把排版能力直接暴露给 AI 客户端。

安装与启动::

    pip install mpltweak[mcp]
    mpltweak mcp                      # stdio server

客户端配置（Claude Desktop / Cursor / Claude Code 等）::

    {"mcpServers": {"mpltweak": {"command": "mpltweak", "args": ["mcp"]}}}

暴露四个工具，对应"读 → 查 → 写"闭环：

* ``describe_layout`` 读：不开窗导出脚本当前的排版（参数 JSON）
* ``check_layout``    查：排版体检（对齐/等大/间距/字号/越界/重叠）
* ``params_schema``   格式：参数文件的 JSON Schema（校验自己生成的内容）
* ``apply_layout``    写：把参数落实回源码（默认只读预览，write=True 才改代码）

分工原则：**"好不好看"归人，"精确落实"归 agent。**
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys


def build_server():
    """构造 FastMCP server（延迟 import，没装 mcp 时给人话提示）。"""
    # mcp 2.x 把 FastMCP 改名成 MCPServer（装饰器与 run() 用法一致）—— 两个大版本都兼容
    try:
        from mcp.server.mcpserver import MCPServer as _Server      # mcp >= 2
    except ImportError:                                            # mcp 1.x
        from mcp.server.fastmcp import FastMCP as _Server

    mcp = _Server('mpltweak')

    @mcp.tool()
    def describe_layout(script: str, fig: int = -1, all_figs: bool = False) -> str:
        """读：不开窗导出脚本当前的排版，返回符合 mpltweak 公开规范（version = params.SCHEMA_VERSION，当前 4）的参数 JSON。

        Args:
            script: 绘图脚本路径。
            fig: 第几张图（0-based；默认 -1 = 最后一张）。
            all_figs: True 时一次导出全部图（多图脚本）。
        """
        from . import params, verify
        from .describe import _to_params

        script = os.path.abspath(script)
        if not os.path.exists(script):
            return json.dumps({'error': '找不到脚本: %s' % script}, ensure_ascii=False)
        if all_figs:
            states, err = verify.collect_all(script)
            if states is None:
                return json.dumps({'error': err}, ensure_ascii=False)
            payload = {
                'version': params.SCHEMA_VERSION,
                'script': os.path.basename(script),
                'n_figs': len(states),
                'figures': [_to_params(s, script) for s in states],
            }
        else:
            state, err = verify.collect(script, fig if fig >= 0 else None)
            if state is None:
                return json.dumps({'error': err}, ensure_ascii=False)
            payload = _to_params(state, script)
        return json.dumps(payload, ensure_ascii=False, indent=2)

    @mcp.tool()
    def check_layout(script: str, fig: int = -1, tol: float = 0.01) -> str:
        """查：排版体检 —— 对齐 / 等大 / 间距 / 字号 / 越界 / 重叠。

        纯几何规则，不需要"看到"图。返回 {ok, problems[], warnings[]}；
        problems 里的 error 级问题值得修，warnings 可能是有意为之。

        Args:
            script: 绘图脚本路径。
            fig: 第几张图（0-based；默认 -1 = 最后一张）。
            tol: 几何容差（默认 0.01，即画布宽度的 1%）。
        """
        from . import verify
        from .check import analyze_state

        script = os.path.abspath(script)
        if not os.path.exists(script):
            return json.dumps({'error': '找不到脚本: %s' % script}, ensure_ascii=False)
        # with_text=True：文字尺寸检查（刻度标签/标题/标注）需要一次真实绘制 ——
        # 与 CLI 的 `mpltweak check` 走**同一个入口**，免得两边规则漂移。
        state, err = verify.collect(script, fig if fig >= 0 else None,
                                    with_text=True)
        if state is None:
            return json.dumps({'error': err}, ensure_ascii=False)
        problems, warns = analyze_state(state, tol, with_text=True)
        return json.dumps({'ok': not problems, 'problems': problems,
                           'warnings': warns,
                           'text_checked': not state.get('_text_error')},
                          ensure_ascii=False, indent=2)

    @mcp.tool()
    def params_schema() -> str:
        """参数文件的 JSON Schema（draft-07）—— 生成参数前先读它，可校验自己的输出。"""
        from . import params
        return json.dumps(params.schema(), ensure_ascii=False, indent=2)

    @mcp.tool()
    def apply_layout(script: str, write: bool = False) -> str:
        """写：把 `.tweak_params/<脚本名>.json` 里的参数落实回源码。

        默认 write=False 只做只读预览（列出会改什么，不动代码）。
        write=True 才真正写回：AST 精确定位 + 三层验证（能跑通 / 改对了 / 换锚点重试），
        全部失败会自动回滚，不会留下改坏的脚本。

        Args:
            script: 绘图脚本路径。
            write: 是否真的写入源码（默认 False = 预览）。
        """
        from .apply import main as apply_main

        script = os.path.abspath(script)
        if not os.path.exists(script):
            return json.dumps({'error': '找不到脚本: %s' % script}, ensure_ascii=False)
        buf = io.StringIO()
        argv = [script] + (['--write'] if write else [])
        try:
            with contextlib.redirect_stdout(buf):
                rc = apply_main(argv)
        except SystemExit as e:                   # argparse 之类
            rc = int(e.code or 0)
        return json.dumps({'exit_code': rc, 'wrote': bool(write),
                           'output': buf.getvalue()}, ensure_ascii=False, indent=2)

    return mcp


def main(argv=None) -> int:
    try:
        mcp = build_server()
    except ImportError as e:                      # noqa: BLE001
        sys.stderr.write(
            '[mcp] 需要 mcp 包：pip install "mpltweak[mcp]"\n       (%s)\n' % e)
        return 2
    sys.stderr.write('[mcp] mpltweak MCP server 已启动（stdio）\n')
    mcp.run()
    return 0


if __name__ == '__main__':
    sys.exit(main())
