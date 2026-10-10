# -*- coding: utf-8 -*-
"""``python -m mpltweak`` 的入口，等价于 ``mpltweak`` 命令。

README 里说明"命令不在 PATH 时怎么办"时用的就是这条替代路径（t4-F8：
原先没有这个文件，`python -m mpltweak` 会报 No module named mpltweak.__main__）。
"""
import sys

from .cli import main

if __name__ == '__main__':
    sys.exit(main())
