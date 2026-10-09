# -*- coding: utf-8 -*-
"""
mpltweak —— CLI 入口（库内核 + CLI 外挂）

主推用法（脚本零侵入，脚本里不出现本工具任何痕迹）：

  mpltweak <script.py> [--fig N] [--params X]    # 跑脚本 + 弹交互调图窗，关窗只存参数
  mpltweak apply <script.py>                     # 改动清单（只读）
  mpltweak apply <script.py> --write             # AST 确定性写回（零 LLM）
  mpltweak apply <script.py> --snippet           # 输出可粘贴片段
  mpltweak doctor                                # 环境自检（后端/绑定/版本）
"""

from __future__ import annotations

import sys


def _usage(out):
    out.write(
        'mpltweak —— 像在 PPT 里调多图排版一样调 matplotlib（脚本零侵入）\n'
        '\n'
        '用法：\n'
        '  mpltweak <script.py> [--fig N] [--params X] [--dry-run]  开窗调图\n'
        '  mpltweak apply <script.py> [--write] [--snippet] [--params X]\n'
        '  mpltweak doctor                                          环境自检\n'
        '\n'
        '交互：拖面板移动 / PPT式缩放 / Ctrl+点击多选 / 对齐均分 / 吸附 /\n'
        '      Ctrl+F 裁白边 / 悬停±字号 / 方向键微调 / 空格线条模式 /\n'
        '      Ctrl+Z 撤销，Ctrl+Y 或 Ctrl+Shift+Z 重做\n'
    )


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
    if cmd == 'schema':
        from .params import schema_main
        return schema_main(argv[1:])
    if cmd == 'check':
        from .check import main as check_main
        return check_main(argv[1:])
    if cmd == 'mcp':
        from .mcp_server import main as mcp_main
        return mcp_main(argv[1:])
    if cmd == 'doctor':
        from .launch import doctor
        return doctor()
    # 默认：launch（透传剩余参数；脚本自己的参数由 launch 的 parse_known_args 接住）
    from .launch import main as launch_main
    return launch_main(argv)


if __name__ == '__main__':
    sys.exit(main())
