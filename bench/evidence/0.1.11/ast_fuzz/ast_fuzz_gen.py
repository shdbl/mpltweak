# -*- coding: utf-8 -*-
"""语法化 AST 模糊测试生成器（t77）—— 自造 CFG，seed 可复现，**不依赖 hypothesis**。

为什么自己写
------------
CI 的 step 6 按设计不装 hypothesis（只有 ``tests/_hypothesis_shim.py`` 的最小替身），
而现有的 fuzz 套件随机的是**参数**（test_fuzz_params）与**手势**（test_fuzz_events），
``test_property_writeback`` 也自述"脚本不随机生成"。今晚的真 bug
（``a,b,c,d = axs.ravel()`` 解包名、``fig.subplots(...)`` 误判、``axis_var_index``
变量解析）**全在 AST 长尾** ⇒ 这里补的正是"**随机拼代码**"这一层。

产出
----
``gen(seed)`` → dict：

    {
      'seed':      int,
      'form':      str,    # 见 FORMS
      'src':       str,    # 语法完全合法、可直接 describe/run 的脚本
      'figsize_line': int, # 1-based：figsize 那一行（没写 figsize 则 None）
      'target_line':  int, # 1-based：目标轴的 add_axes/subplots 行
      'figsize_literal': bool,  # figsize 是不是纯数字字面量
      'pos_literal':     bool,  # 目标轴的 pos 是不是纯数字字面量
      'n_axes':    int,
    }

设计纪律
--------
* **必须可 seed 复现**：只用 ``random.Random(seed)``，不碰全局 random。
* **语法必须合法**：拼出来先自己 ``ast.parse`` 一次，不合法就 raise（生成器自身 bug
  不该伪装成"引擎拒写"）。
* **必须能真跑**：只用 Agg、只 savefig 到临时名、不开窗。
* **形态按 seed 轮转**（``seed % len(FORMS)``）而不是纯随机抽 —— 这样 N 例一定
  **均匀覆盖**每个对抗形态，"某个形态一次没抽到"不会伪装成"这个形态没问题"。
"""

import ast
import builtins
import random

# 对抗形态（前 5 个对应用户点名的五类；其余是同类长尾与混合）
FORMS = (
    'plain',          # 对照：全字面量
    'nested_unpack',  # ① 嵌套解构 (ax1,(ax2,ax3)) = subplots(...)
    'tuple_unpack',   # ① 经典 fig,(ax1,ax2) = subplots(1,2)
    'listcomp',       # ② 列表推导里建轴
    'walrus',         # ③ 海象运算符 left := 0.1
    'scope',          # ④ 类方法 / 嵌套函数 / 装饰器 / style.context
    'exprs',          # ⑤ 字面量表达式变换 10+2 / BASE*1.2 / 1e-1 / 5/10
    'dynamic',        # 动态：变量 / 环境变量驱动的 figsize 或 pos
    'loop_axes',      # for 循环里建轴
)

HEAD = ("import matplotlib\n"
        "matplotlib.use('Agg')\n"
        "import matplotlib.pyplot as plt\n")

# figsize 的写法域（gen 按 seed 轮转，保证覆盖；见 gen 的 docstring）
FS_KINDS = ('literal', 'var', 'expr', 'dynamic')
# 全覆盖所需的最小样本数 = 形态数 × figsize 写法数
COVERAGE_N = len(FORMS) * len(FS_KINDS)

# 目标值：只改轴位置，**绝不碰 figsize**（这是"只动目标"不变量的前提）
TARGET_POS = [0.25, 0.25, 0.35, 0.35]


class _Lines(object):
    """按行拼脚本，同时记住关键字段的行号（1-based）。"""

    def __init__(self):
        self.lines = []
        self.marks = {}

    def add(self, text, mark=None):
        for ln in text.split('\n'):
            self.lines.append(ln)
            if mark is not None and mark not in self.marks:
                self.marks[mark] = len(self.lines)
        return self

    def text(self):
        return '\n'.join(self.lines) + '\n'


def _figsize(rnd, L, mark='figsize', kind=None):
    """→ (表达式文本, 是否纯字面量)。覆盖字面量 / 变量 / 常量表达式 / 动态。

    ``kind`` 由 ``gen()`` **按 seed 轮转**指定（保证覆盖），None 时才随机。
    """
    kind = kind or rnd.choice(('literal', 'literal', 'var', 'expr', 'dynamic'))
    if kind == 'literal':
        return '(%d, %d)' % (rnd.randint(4, 8), rnd.randint(3, 6)), True
    if kind == 'var':
        L.add('W = %d.0\nH = %d.0' % (rnd.randint(5, 8), rnd.randint(3, 5)))
        return '(W, H)', False
    if kind == 'expr':
        L.add('BASE = %d' % rnd.randint(8, 12))
        return '(BASE - %d, 4)' % rnd.randint(2, 5), False
    L.add('import os')
    L.add("W = float(os.environ.get('MPLTWEAK_ASTFUZZ_W', '%d'))"
          % rnd.randint(5, 8))
    return '(W, 4)', False


def _pos(rnd, i):
    """→ (位置表达式文本, 是否纯字面量)。i = 轴序号（用于错开）。"""
    kind = rnd.choice(('literal', 'literal', 'expr', 'walrus', 'dynamic'))
    x = 0.1 + 0.05 * i
    if kind == 'literal':
        return '[%.2f, 0.15, 0.5, 0.5]' % x, True
    if kind == 'expr':
        return '[1e-1, 0.15, 5 / 10, 0.5]', False
    if kind == 'walrus':
        return '[left%d := %.2f, 0.15, 0.5, 0.5]' % (i, x), False
    return '[left, 0.15, 0.5, 0.5]', False


def _savefig(L):
    L.add("fig.savefig('o.png')")


def _build(seed, form, fs_kind=None):
    rnd = random.Random(seed)
    L = _Lines()
    L.add(HEAD)
    meta = {'figsize_literal': True, 'pos_literal': True, 'n_axes': 1,
            'figsize_line': None, 'target_line': None}
    fs, fs_lit = _figsize(rnd, L, kind=fs_kind)
    meta['figsize_literal'] = fs_lit
    L.add('')                                   # 空行，贴近真实脚本
    if form in ('plain', 'exprs', 'dynamic'):
        pos, pl = _pos(rnd, 0)
        if form == 'exprs':
            pos, pl = '[1e-1, 0.15, 5 / 10, 0.5]', False
        if form == 'dynamic':
            pos, pl = '[left, 0.15, 0.5, 0.5]', False
        if '[left,' in pos:
            # 变量驱动的 pos 必须在脚本里**定义**，否则脚本跑不起来 —— 那是**生成器
            # bug**，会被误当成"引擎拒写"（seed 54/81/126/135/162/225 实测踩到过；
            # scope 形态的 seed 14 同因）。下面 _check_names 是这类 bug 的兜底。
            L.add('left = 0.15')
        L.add('fig = plt.figure(figsize=%s)' % fs, mark='figsize')
        L.add('ax = fig.add_axes(%s)' % pos, mark='target')
        meta.update(pos_literal=pl, n_axes=1)
        L.add("ax.set_title('t', fontsize=12)")
        _savefig(L)
    elif form == 'walrus':
        L.add('fig = plt.figure(figsize=%s)' % fs, mark='figsize')
        pos, pl = _pos(rnd, 0)
        if ':=' not in pos:
            pos = '[left := 0.15, bottom := 0.15, w := 0.5, h := 0.5]'
        L.add('ax = fig.add_axes(%s)' % pos, mark='target')
        meta.update(pos_literal=False, n_axes=1)
        _savefig(L)
    elif form == 'listcomp':
        L.add('fig = plt.figure(figsize=%s)' % fs, mark='figsize')
        L.add('axes = [fig.add_axes([0.1 * i, 0.15, 0.3, 0.3]) for i in range(3)]',
              mark='target')
        meta.update(pos_literal=False, n_axes=3)
        L.add("axes[0].set_title('t', fontsize=12)")
        _savefig(L)
    elif form == 'loop_axes':
        L.add('fig = plt.figure(figsize=%s)' % fs, mark='figsize')
        L.add('axes = []')
        L.add('for i in range(2):')
        L.add('    axes.append(fig.add_axes([0.1 * (i + 1), 0.15, 0.3, 0.3]))',
              mark='target')
        meta.update(pos_literal=False, n_axes=2)
        _savefig(L)
    elif form in ('nested_unpack', 'tuple_unpack'):
        if form == 'nested_unpack':
            L.add('fig, ((ax1, ax2), (ax3, ax4)) = plt.subplots(2, 2, figsize=%s)'
                  % fs, mark='target')
            meta['n_axes'] = 4
        else:
            L.add('fig, (ax1, ax2) = plt.subplots(1, 2, figsize=%s)' % fs,
                  mark='target')
            meta['n_axes'] = 2
        meta['figsize_line'] = L.marks.get('target')      # figsize 在 subplots 行上
        L.add("ax1.set_title('t', fontsize=12)")
        _savefig(L)
    elif form == 'scope':
        pos, pl = _pos(rnd, 0)                  # 先算，才能决定要不要定义 left
        if '[left,' in pos:
            L.add('left = 0.15')                # 模块级：闭包/类方法里可见
            L.add('')
            L.add('')
        L.add('def deco(fn):')
        L.add('    def wrap(*a, **k):')
        L.add('        return fn(*a, **k)')
        L.add('    return wrap')
        L.add('')
        L.add('')
        L.add('class Plotter:')
        L.add('    def __init__(self):')
        L.add('        self.fig = plt.figure(figsize=%s)' % fs, mark='figsize')
        L.add('')
        L.add('    @deco')
        L.add('    def draw(self):')
        L.add('        fig = self.fig')
        L.add('')
        L.add('        def inner():')
        L.add('            return fig.add_axes(%s)' % pos, mark='target')
        L.add('        ax = inner()')
        L.add("        ax.set_title('t', fontsize=12)")
        L.add('        return fig')
        L.add('')
        L.add('')
        L.add("with plt.style.context('default'):")
        L.add('    _p = Plotter()')
        L.add("    _p.draw().savefig('o.png')")
        meta.update(pos_literal=pl, n_axes=1)
    else:
        raise AssertionError('未知形态 %r' % form)

    if meta['figsize_line'] is None:
        meta['figsize_line'] = L.marks.get('figsize')
    meta['target_line'] = L.marks.get('target')
    meta['form'] = form
    meta['seed'] = seed
    meta['src'] = L.text()
    ast.parse(meta['src'])          # 生成器自身 bug 必须当场炸，不许伪装成"引擎拒写"
    _check_names(meta['src'])       # 语法合法 ≠ 跑得起来（NameError 也要当场炸）
    return meta


def _check_names(src):
    """静态查**未定义的名字** —— 生成器自检，防"生成器 bug 伪装成引擎拒写"。

    为什么必须有：``ast.parse`` 只查语法，查不出 ``NameError``。实测被这条咬过两次
    （``scope`` 的 seed 14、``plain`` 的 seed 54/81/126/135/162/225：pos 写成
    ``[left, …]`` 却没定义 ``left``）—— 那种脚本 describe 直接 rc=2，看起来像
    "引擎拒写"，其实是**我的生成器**的错。批量跑的时候这种假信号最危险。
    """
    tree = ast.parse(src)
    defined = set(dir(builtins))
    loaded = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            if isinstance(node.ctx, ast.Store):
                defined.add(node.id)
            else:
                loaded.append(node.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            defined.add(node.name)
        elif isinstance(node, ast.arg):
            defined.add(node.arg)
        elif isinstance(node, ast.alias):
            defined.add((node.asname or node.name).split('.')[0])
        elif isinstance(node, ast.ExceptHandler) and node.name:
            defined.add(node.name)
    missing = sorted(set(n for n in loaded if n not in defined))
    if missing:
        raise AssertionError(
            '生成器产出**未定义的变量** %s —— 这是生成器 bug，不是引擎的问题' % missing)


def gen(seed):
    """按 seed 生成一例。

    **双重轮转**（这是"形态枚举"交付物的骨架）：
    * ``form      = FORMS[seed % len(FORMS)]``                —— 9 种对抗形态轮转
    * ``fs_kind   = FS_KINDS[(seed // len(FORMS)) % 4]``      —— figsize 写法轮转
    ⇒ N = 36（= 9×4）时**每一对 (形态, figsize 写法) 恰好出现一次**，
    包括**对照格**（plain + 字面量 figsize ⇒ 已知缺陷不适用 ⇒ 必须能原位写回）。
    纯随机抽的话"某个形态/写法一次没抽到"会伪装成"这种写法没问题"。
    """
    form = FORMS[seed % len(FORMS)]
    fs_kind = FS_KINDS[(seed // len(FORMS)) % len(FS_KINDS)]
    return _build(seed, form, fs_kind)


def gen_many(n, base_seed=0):
    return [gen(base_seed + i) for i in range(n)]


if __name__ == '__main__':
    import sys
    n = int(sys.argv[1]) if len(sys.argv) > 1 else len(FORMS)
    for m in gen_many(n):
        print('==== seed=%d form=%s figsize_literal=%s pos_literal=%s '
              'figsize_line=%s target_line=%s ===='
              % (m['seed'], m['form'], m['figsize_literal'], m['pos_literal'],
                 m['figsize_line'], m['target_line']))
        print(m['src'])
