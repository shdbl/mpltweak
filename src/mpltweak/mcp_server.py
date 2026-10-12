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

执行边界（0.1.9）：
  * 工具里的用户脚本一律跑在**独立子进程**（``verify.collect_isolated``）——
    server 是长驻进程，同进程 runpy 会让 sys.path / sys.modules / matplotlib
    rcParams 跨调用串味，脚本里的 ``sys.exit()`` 更会把整个 server 带走（CH-1/CH-2）；
  * 工具的失败一律以 ``{"ok": false, "error_code": ..., "error": ...}`` 返回
    （机器可读字段固定英文），不抛异常 —— 客户端只看 ``ok``，不必去猜 isError。
"""

from __future__ import annotations

import contextlib
import hashlib
import inspect
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

    from . import __version__ as _pkg_version
    from . import messages as _msg

    # mcp 2.x 的 MCPServer 收 `version=`；1.x 的 FastMCP 没这个参数（会塞进 settings
    # 校验）。用**签名探测**而不是 try/except：不新增宽兜底，也不赌 pydantic 的报错类型。
    # 0.1.8 没传，客户端日志里 serverInfo.version 是空串（CH-10）。
    _kw = {}
    try:
        if 'version' in inspect.signature(_Server.__init__).parameters:
            _kw['version'] = _pkg_version
    except (TypeError, ValueError):
        pass
    mcp = _Server('mpltweak', **_kw)

    def _fail(code, **kw):
        """统一的失败返回：稳定错误码 + 固定英文（错误语义只有这一种形状）。"""
        return json.dumps({'ok': False, 'error_code': code,
                           'error': _msg.t_en(code, **kw)}, ensure_ascii=False)

    def _sha(path):
        try:
            with open(path, 'rb') as f:
                return hashlib.sha256(f.read()).hexdigest()
        except OSError:
            return None

    @mcp.tool()
    def describe_layout(script: str, fig: int = -1, all_figs: bool = False) -> str:
        """读：不开窗导出脚本当前的排版，返回符合 mpltweak 公开规范（version = params.SCHEMA_VERSION，当前 4）的参数 JSON。

        执行边界：脚本在**独立子进程**里跑一次（Agg 无头后端）；
        脚本自身的副作用（写文件 / 联网 / 缓存）会真实发生。

        Args:
            script: 绘图脚本路径。
            fig: 第几张图（0-based；默认 -1 = 最后一张）。
            all_figs: True 时一次导出全部图（多图脚本）。
        """
        from . import params, verify
        from .describe import _to_params

        script = os.path.abspath(script)
        if not os.path.exists(script):
            return _fail('file_not_found', path=script)
        if all_figs:
            states, err = verify.collect_isolated(script, all_figs=True)
            if states is None:
                return _fail('collect_failed', err=err)
            payload = {
                'version': params.SCHEMA_VERSION,
                'script': os.path.basename(script),
                'n_figs': len(states),
                'figures': [_to_params(s, script) for s in states],
            }
        else:
            state, err = verify.collect_isolated(
                script, fig if fig >= 0 else None)
            if state is None:
                return _fail('collect_failed', err=err)
            payload = _to_params(state, script)
        return json.dumps(payload, ensure_ascii=False, indent=2)

    @mcp.tool()
    def check_layout(script: str, fig: int = -1, tol: float = 0.01) -> str:
        """查：排版体检 —— 对齐 / 等大 / 间距 / 字号 / 越界 / 重叠。

        纯几何规则，不需要"看到"图。返回 {ok, problems[], warnings[]}；
        problems 里的 error 级问题值得修，warnings 可能是有意为之。

        执行边界：脚本在**独立子进程**里跑一次（含一次真实绘制，用于量文字尺寸）；
        脚本自身的副作用会真实发生。

        Args:
            script: 绘图脚本路径。
            fig: 第几张图（0-based；默认 -1 = 最后一张）。
            tol: 几何容差（默认 0.01，即画布宽度的 1%；必须是有限正数）。
        """
        import math

        from . import check as _check
        from . import verify

        script = os.path.abspath(script)
        if not os.path.exists(script):
            return _fail('file_not_found', path=script)
        if not (isinstance(tol, (int, float)) and math.isfinite(tol) and tol > 0):
            # 0.1.8：tol=0 时 ValueError 逃出工具，SDK 只回一句
            # "Error executing tool check_layout"，真原因只在 server 的 stderr 里
            return _fail('bad_tol', tol=tol)
        # with_text=True：文字尺寸检查（刻度标签/标题/标注）需要一次真实绘制 ——
        # 与 CLI 的 `mpltweak check` 走**同一个入口**，免得两边规则漂移。
        state, err = verify.collect_isolated(script, fig if fig >= 0 else None,
                                             with_text=True)
        if state is None:
            return _fail('collect_failed', err=err)
        # CH-4：tight 导出的脚本画布会按内容扩张，文字越界降级为提示。
        tight = _check.has_tight_bbox(script)
        problems, warns = _check.analyze_state(state, tol, with_text=True,
                                              tight=tight)
        return json.dumps({'ok': not problems, 'problems': problems,
                           'warnings': warns,
                           'tight_bbox': tight,
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

        返回里的 ``wrote`` = **源码是否真的被改动**（文件哈希前后对比），不是"你请求了
        写回" —— 0.1.8 直接回显入参，于是"没有参数文件、什么都没写"也报 wrote=true（CH-7）。

        Args:
            script: 绘图脚本路径。
            write: 是否真的写入源码（默认 False = 预览）。
        """
        from .apply import main as apply_main

        script = os.path.abspath(script)
        if not os.path.exists(script):
            return _fail('file_not_found', path=script)
        before = _sha(script)
        buf = io.StringIO()
        argv = [script] + (['--write'] if write else [])
        try:
            with contextlib.redirect_stdout(buf):
                rc = apply_main(argv)
        except SystemExit as e:                   # argparse 之类
            rc = int(e.code or 0)
        after = _sha(script)
        wrote = bool(before is not None and after is not None and before != after)
        return json.dumps({'ok': rc == 0, 'exit_code': rc, 'wrote': wrote,
                           'requested_write': bool(write),
                           'output': buf.getvalue()}, ensure_ascii=False, indent=2)

    return mcp


def main(argv=None) -> int:
    try:
        mcp = build_server()
    except ImportError as e:                      # noqa: BLE001
        from . import messages as _msg
        sys.stderr.write('%s\n       (%s)\n' % (_msg.t('mcp_no_mcp_pkg'), e))
        return 2
    sys.stderr.write('[mcp] mpltweak MCP server 已启动（stdio）\n')
    mcp.run()
    return 0


if __name__ == '__main__':
    sys.exit(main())
