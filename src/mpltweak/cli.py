# -*- coding: utf-8 -*-
"""
mpltweak —— CLI 入口（库内核 + CLI 外挂）

主推用法（脚本零侵入，脚本里不出现本工具任何痕迹）：

  mpltweak <script.py> [--fig N] [--params X]    # 跑脚本 + 弹交互调图窗，关窗只存参数
  mpltweak apply <script.py>                     # 改动清单（只读）
  mpltweak apply <script.py> --write             # AST 确定性写回（零 LLM）
  mpltweak apply <script.py> --snippet           # 输出可粘贴片段
  mpltweak revert <script.py> [--write]          # 回退到上次写回之前（默认只预览）
  mpltweak doctor                                # 环境自检（后端/绑定/版本）
"""

from __future__ import annotations

import sys


def _usage(out):
    out.write(
        'mpltweak —— 像在 PPT 里调多图排版一样调 matplotlib（脚本零侵入）\n'
        '\n'
        '用法：\n'
        '  mpltweak <script.py> [--fig N] [--params X]     开窗调图\n'
        '  mpltweak launch <script.py> [--fig N]           同上（显式子命令）\n'
        '  mpltweak apply <script.py> [--write] [--json]   落实（默认只读预览）\n'
        '  mpltweak revert <script.py> [--write]           回退到上次写回之前\n'
        '  mpltweak describe <script.py> [--lang zh|en]    不开窗导出当前排版\n'
        '  mpltweak check <script.py> [--json]             排版体检（会执行脚本）\n'
        '  mpltweak check <script.py> --static             只做 AST + 布局引擎扫描\n'
        '  mpltweak schema [--compact]                     参数 JSON Schema\n'
        '  mpltweak mcp                                    启动 MCP server\n'
        '  mpltweak doctor                                 环境自检\n'
        '\n'
        '退出码（0.1.9 起统一，**数值语义不变**）：0 = 没问题 / 成功；1 = 发现问题（check）；\n'
        '  2 = 跑不起来（含下面几种互不相同的来源）；\n'
        '  3 = 交互命令找不到要打开的脚本文件。\n'
        '同为退出码 2，**"脚本侧失败"与"工具自身 bug"分开报**（0.1.10 起，只改文案）：\n'
        '  · 脚本侧：文件不存在 / 参数文件不合法（bad_params）/ 你的脚本自己报错\n'
        '    （缺依赖 / 缺数据 / 需要命令行参数 / 无头环境要开窗口）；\n'
        '  · 工具侧：internal_error —— 本工具自己抛了未预期的异常，与你的脚本无关。\n'
        '两类都印一行「归因」+ 一句可照抄的**下一步**；`--json` 下用 `attribution` 字段\n'
        '（script_cannot_run / script_needs_args / script_needs_gui_or_headless_impossible\n'
        '/ tool_bug）与 `next_step` 区分，agent 可据此自动分支。退出码数值一个都没改\n'
        '（0.1.9 已对外文档化，用户 CI 依赖）。\n'
        '注意：describe / check / apply 会**执行你的脚本**（在独立子进程里，Agg 无头后端），\n'
        '      脚本的副作用（写文件 / 联网 / 缓存）都会真实发生；--static 是唯一不执行的选择。\n'
        '\n'
        '交互：拖面板移动 / PPT式缩放（Shift 等比、Alt 中心）/ Ctrl+点击多选 /\n'
        '      对齐均分 / 吸附 / Ctrl+F 裁白边 / 悬停±字号 / 方向键移动面板 /\n'
        '      空格线条模式 / Ctrl+Z 撤销，Ctrl+Y 或 Ctrl+Shift+Z 重做 / ? 看键位\n'
    )


def _schema_command(argv):
    """``mpltweak schema``：CH-9 —— 不再忽略参数。

    0.1.8 的 ``schema_main`` 只探测 ``--compact``，于是 ``schema --help``、
    ``schema --bogus extra.py`` 都原样吐 JSON 且 rc=0：用户/脚本敲错参数时
    "看起来成功了"。这里在分派层做参数校验（params.py 归引擎线，不改它）。
    """
    from . import messages as _msg
    argv = list(argv)
    if any(a in ('-h', '--help') for a in argv):
        sys.stdout.write(_msg.t('schema_usage') + '\n')
        return _msg.EXIT_OK
    unknown = [a for a in argv if a != '--compact']
    if unknown:
        sys.stderr.write(_msg.t('schema_usage') + '\n')
        sys.stderr.write('unrecognized argument(s): %s\n' % ' '.join(unknown))
        return _msg.EXIT_USAGE
    from .params import schema_main
    return schema_main(argv)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        _usage(sys.stderr)
        return 2
    cmd = argv[0]
    if cmd in ('-h', '--help', 'help'):
        _usage(sys.stdout)
        return 0
    if cmd == 'apply':
        from .apply import main as apply_main
        return apply_main(argv[1:])
    if cmd == 'describe':
        from .describe import main as describe_main
        return describe_main(argv[1:])
    if cmd == 'revert':
        from .revert import main as revert_main
        return revert_main(argv[1:])
    if cmd == 'schema':
        return _schema_command(argv[1:])
    if cmd == 'check':
        from .check import main as check_main
        return check_main(argv[1:])
    if cmd == 'mcp':
        from .mcp_server import main as mcp_main
        return mcp_main(argv[1:])
    if cmd == 'doctor':
        from .launch import doctor
        return doctor()
    if cmd == 'launch':
        # 显式 `mpltweak launch x.py`：0.1.8 没有这个分支，`launch` 会被当成脚本名
        # 丢给 launch 的位置参数 → "找不到脚本: ...\launch" + rc=3（P0-4）。
        from .launch import main as launch_main
        return launch_main(argv[1:])
    # 默认：launch（透传剩余参数；脚本自己的参数由 launch 的 parse_known_args 接住）
    from .launch import main as launch_main
    return launch_main(argv)


if __name__ == '__main__':
    sys.exit(main())
