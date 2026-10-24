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
    # t78：cli.py 的 messages 导入都在各函数内部（如 _schema_command），
    # 本函数也要显式导入一次，供 t_ui 取语言表。
    from . import messages as _msg
    out.write(_msg.t_ui(
        'mpltweak —— 像在 PPT 里调多图排版一样调 matplotlib（脚本零侵入）\n'
        '\n'
        '用法：\n'
        '  mpltweak <script.py> [--fig N] [--params X]     开窗调图\n'
        '  mpltweak launch <script.py> [--fig N]           同上（显式子命令）\n'
        '  mpltweak --version                              打印版本号后退出（rc=0）\n'
        '  mpltweak --check-update                         查 PyPI 最新版（显式联网；查不到不算失败）\n'
        '  mpltweak apply <script.py> [--write] [--json]   落实（默认只读预览）\n'
        '  mpltweak revert <script.py> [--write]           回退到上次写回之前\n'
        '  mpltweak demo [--write]                         示例体验（复制示例 + 开窗 + 对比图）\n'
        '  mpltweak describe <script.py> [--lang zh|en]    不开窗导出当前排版\n'
        '  mpltweak check <script.py> [--json]             排版体检（会执行脚本）\n'
        '  mpltweak check <script.py> --static             只做 AST + 布局引擎扫描\n'
        '  mpltweak schema [--compact]                     参数 JSON Schema\n'
        '  mpltweak mcp                                    启动 MCP server\n'
        '  mpltweak doctor                                 环境自检\n'
        '\n'
        '退出码（0.1.11 起统一为 0 / 1 / 2 / 4）：0 = 没问题 / 成功；1 = 发现问题（check）；\n'
        '  2 = 跑不起来（脚本找不到 / 脚本自己报错 / 参数非法 / 工具自身异常）；\n'
        '  4 = 没有可用图形后端。\n'
        '  （0.1.9 的退出码 3 已退役：常量保留，但不再由任何路径产生。）\n'
        '同为退出码 2，**"脚本侧失败"与"工具自身 bug"分开报**（0.1.10 起，只改文案）：\n'
        '  · 脚本侧：文件不存在 / 参数文件不合法（bad_params）/ 你的脚本自己报错\n'
        '    （缺依赖 / 缺数据 / 需要命令行参数 / 无头环境要开窗口）；\n'
        '  · 工具侧：internal_error —— 本工具自己抛了未预期的异常，与你的脚本无关。\n'
        '两类都印一行「归因」+ 一句可照抄的**下一步**；`--json` 下用 `attribution` 字段\n'
        '（script_cannot_run / script_needs_args / script_needs_gui_or_headless_impossible\n'
        '/ tool_bug / mtime_sync_failed）与 `next_step` 区分，agent 可据此自动分支。\n'
        '注意：describe / check / apply 会**执行你的脚本**（在独立子进程里，Agg 无头后端），\n'
        '      脚本的副作用（写文件 / 联网 / 缓存）都会真实发生；--static 是唯一不执行的选择。\n'
        '注意：**默认任何命令都不联网**；唯一会联网的是 --check-update（你敲了才查）。\n'
        '      它查不到（断网 / 超时 / 被墙）时仍 rc=0 —— 那不是命令失败；也不自动升级、\n'
        '      不写任何文件。\n'
        '\n'
        '交互：拖面板移动 / PPT式缩放（Shift 等比、Alt 中心）/ Ctrl+点击多选 /\n'
        '      对齐均分 / 吸附 / Ctrl+F 裁白边 / 悬停±字号 / 方向键移动面板 /\n'
        '      空格线条模式 / Ctrl+Z 撤销，Ctrl+Y 或 Ctrl+Shift+Z 重做 / ? 看键位\n'
    ))

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


def _version_text():
    """``mpltweak --version`` 的一行输出（只报版本号，不给别的废话）。

    为什么单独一条：过去 ``mpltweak --version`` 落到"默认 = launch"分支，被当成
    **脚本路径** ⇒ "找不到脚本: ...\--version" + rc=2（agent 打包/自检时最先踩的坑）。
    """
    from . import __version__
    return 'mpltweak %s' % __version__


def _leading_top_flags(argv):
    """扫 ``mpltweak`` **开头那一段**顶层开关 → ``([(名字, 值或 None)], 停在第几个)``。

    为什么需要它（0.1.17）：``--check-update`` 是**顶层**开关，但
    ``mpltweak fig.py --check-update`` 里的那个属于**用户脚本**——脚本参数我们不许抢
    （与 ``--version`` 只认第一个位置参数同一条纪律）。所以只看"第一个不是开关的
    token"之前的那一段。

    ``--lang`` **带值**，值必须一起吃掉：否则 ``mpltweak --check-update --lang en``
    里的 ``en`` 会被当成脚本名（于是变成"查版本 + 一个叫 en 的脚本"）。
    ``--`` 之后一律是位置参数（脚本参数分隔符），扫描就地停。
    """
    flags = []
    i = 0
    while i < len(argv):
        tok = argv[i]
        if tok == '--' or not tok.startswith('-'):
            break
        if tok == '--lang':
            value = argv[i + 1] if i + 1 < len(argv) else None
            flags.append((tok, value))
            i += 2 if value is not None else 1
            continue
        flags.append((tok, None))
        i += 1
    return flags, i


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        _usage(sys.stderr)
        return 2
    cmd = argv[0]
    if cmd in ('-h', '--help', 'help'):
        _usage(sys.stdout)
        return 0
    # 0.1.17：`mpltweak --check-update`（**显式**联网查 PyPI 最新版）。
    # 放在 --version 之前，`--version --check-update` 两件事都能看到；只看开头那段
    # 顶层开关，所以 `mpltweak fig.py --check-update` 里的那个仍透传给用户脚本。
    _tflags, _stop = _leading_top_flags(argv)
    if any(name == '--check-update' for name, _v in _tflags):
        from .update import main as update_main
        return update_main(_tflags, argv[_stop:])
    if cmd in ('-V', '--version'):
        # 只认**第一个**位置参数：`mpltweak fig.py --version` 是用户脚本自己的参数，
        # 由默认分支透传给 launch（脚本的参数我们不许抢）。
        # 注意：`--version --check-update` 已经在上面的 --check-update 分支里被接住了
        # （那个分支自己会先印版本号），所以这里的"只认第一个"不影响两者同用。
        sys.stdout.write(_version_text() + '\n')
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
    if cmd == 'demo':
        # 0.1.17 头号功能：装上一条命令就能体验（复制示例 → 开窗 → 报告 / 对比图）。
        from .demo import main as demo_main
        return demo_main(argv[1:])
    if cmd == 'schema':
        return _schema_command(argv[1:])
    if cmd == 'check':
        from .check import main as check_main
        return check_main(argv[1:])
    if cmd == 'mcp':
        from .mcp_server import main as mcp_main
        return mcp_main(argv[1:])
    if cmd == 'doctor':
        # t91（船长裁定收窄）：doctor 过去**静默接受**未知选项/多余位置参数（rc=0），
        # 与 --help 里"未知选项 = 2"的口径打架；最危险的是 `doctor --json` ——
        # 用户/agent 以为拿到机器可读结果，实际被静默忽略（"静默撒谎"）。
        # 现按其它子命令同一口径严格校验；--json 明拒（doctor 没有这个选项）。
        # 注意：cli.py 的 messages 都是**函数内局部导入**（见 _schema_command），
        # 本分支同样要显式导入一次，否则 NameError（t78 同型坑）。
        from . import messages as _msg
        _dargs = argv[1:]
        if any(a in ('-h', '--help') for a in _dargs):
            sys.stdout.write(_msg.t('doctor_usage') + '\n')
            return _msg.EXIT_OK
        if any(a == '--json' for a in _dargs):
            sys.stderr.write(_msg.t('doctor_no_json') + '\n')
            sys.stderr.write(_msg.t('doctor_usage') + '\n')
            return _msg.EXIT_USAGE
        _dunknown = [a for a in _dargs if a != '--lang']
        if _dunknown:
            sys.stderr.write(_msg.t('doctor_usage') + '\n')
            sys.stderr.write(_msg.t('schema_unknown_args',
                                    args=' '.join(_dunknown)) + '\n')
            return _msg.EXIT_USAGE
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
