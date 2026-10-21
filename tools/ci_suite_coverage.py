# -*- coding: utf-8 -*-
"""MAINTENANCE §25.9：CI「每套件至少落在一条腿的名单里」的覆盖检查。

**为什么需要**：`.github/workflows/tests.yml` 的覆盖是**按分配**的 —— 老七套在 8 条腿
（ubuntu/windows × 3.9/3.10/3.11/3.12）的 shell 循环里都跑，其余套件只靠 3.12 腿上的
`pytest -q`（`tests/test_suites.py` 的 `SUITES`）捎带。于是「新加一个套件、却忘了分配」
可以一路绿到发布：`tests/test_launch.py` 就曾在 3.9/3.10/3.11 上从没跑过。本工具把
「每个 `tests/test_*.py` 至少被一条腿的名单点到」变成可机检：**没分配到就 rc=1**。

**零依赖**：只用标准库。CI 的 3.9 腿只装 matplotlib/numpy，装不了 PyYAML；也不许为了
这条检查去装东西 —— 那等于给 CI 加时长。

**为什么自己写 YAML 子集解析**：判据必须读**结构**，不许 grep 文本 —— 注释里的套件名、
缩进变化、`run:` 里的 heredoc 正文都会把文本匹配骗过去（"名字只出现在注释里"会假绿、
"名字被 `\\` 拆行"会假红）。本解析器覆盖工作流用到的子集（块映射 / 块序列 / 流序列 /
块标量 / 注释 / 引号），**不认识的构造一律报错**（rc=2），不猜。

**覆盖模型**（"腿" = `strategy.matrix` 的一个单元；每个 step 按 `if:` 落到腿上）。
一条腿的名单来自该 step 的 `run:` 脚本里的四类声明：

  ① bash 循环名单：`for t in a b c; do ... python "tests/$t.py" ...`；
  ② 字面路径：`tests/<名字>.py`；
  ③ `pytest` 调用：`-k` 表达式点到的套件 / 显式路径 / **裸 pytest**（无选择器 -> 收起
     `pyproject.toml` 的 `python_files` 点名的每个包装文件，以及包装文件里 `SUITES`
     登记的全部套件 —— 那就是这条腿实际会跑的集合）；
  ④ `python tools/<x>.py`：该工具的 AST 里若**自己起 pytest**（如
     `tools/ci_pytest_summary.py` 跑 `pytest -q`），按 ③ 的裸 pytest 规则算。
     **认调用、不认注释**（文本里提一句 pytest 不算）。

`if:` 只认 `matrix.<维度> == '[取值]'` / `!=` 及其 `&&` / `||` 组合；别的写法报错。

**豁免口径**（两个来源，都必须带理由；豁免每次运行都打印出来，不许静默）：

  * `SUITES_EXCLUDED`（`tests/test_suites.py`）—— 套件自己声明"不进 pytest 路径"并
    附一句理由，那同时就是"不进 CI"的登记点；
  * `EXTRA_EXEMPT`（本文件）—— 既不在 `SUITES`、也不被 pytest 收集、又没有腿显式点名的
    套件。登记一条要写清"为什么现在不能在 CI 上跑"和"本地怎么自证"。**当前为空**：
    `test_cli_output` 已被 t10 接进 3.12 腿的 pytest step（显式路径），所以那条豁免
    删掉了 —— 现在它由本工具自己盯（谁把那一行接回去，这里就红）。

**rc 语义**：0 = 全覆盖；1 = 有套件没落在任何一条腿的名单里；2 = 用法错 / 工作流或
pyproject 读不成 / 遇到本工具不认识的构造（此时**不给结论**，也不给假绿）。

用法::

    python tools/ci_suite_coverage.py              # 查真仓库
    python tools/ci_suite_coverage.py -v           # 附带打印每条腿的套件名
    python tools/ci_suite_coverage.py --ci -       # 从 stdin 读工作流（反向探测用，不落盘）
    python tools/ci_suite_coverage.py --extra-suite test_zz   # 假装 tests/ 多了这个套件

**接进 CI 的方式：进 `tests/test_hygiene.py`，不新增 workflow step。** 理由：本检查纯静态、
毫秒级，搭 3.12 腿已有的 `pytest -q` 班车即可，CI 时长零增长（3.9 腿装 pytest 只是为了那条
秒级冒烟，不值得为这条检查再装一次）；而且它同时成了本地 `python tests/test_hygiene.py`
就能跑的一道闸（贡献者不用等 CI 才知道自己漏了分配）。
"""
import argparse
import ast
import io
import itertools
import os
import re
import shlex
import sys

# 未进 CI 的登记表（`SUITES_EXCLUDED` 之外的那一类）。**只增不减**，每条都要写清
# "为什么现在不能在 CI 上跑" + "本地怎么自证"，否则就是在给覆盖检查开后门。
#
# 现状：**空**。`test_cli_output` 曾经在这里（真缺口：pytest 函数式、又不在
# `python_files` 里 ⇒ 谁都收不到 ⇒ 从未在 CI 跑过）；t10 已把它接进 3.12 腿的
# pytest step（显式路径），所以这条豁免被删掉了 —— 覆盖检查现在**自己盯住它**，
# 有人把这行接回去就会红。
EXTRA_EXEMPT = {}


class YamlError(Exception):
    """工作流文件不是本解析器能读的 YAML 子集。"""


class Unsupported(Exception):
    """本工具不认识的构造 —— 宁可报错、不给结论，也不猜着判绿。"""


# ---------------------------------------------------------------- YAML 子集
def _strip_comment(line):
    """去掉行尾注释，但**不碰引号里**的 ``#``（`name: a#b`、`- 'x # y'`）。"""
    out = []
    quote = None
    i = 0
    n = len(line)
    while i < n:
        ch = line[i]
        if quote == "'":
            if ch == "'":
                if i + 1 < n and line[i + 1] == "'":
                    out.append("''")
                    i += 2
                    continue
                quote = None
            out.append(ch)
        elif quote == '"':
            if ch == '\\' and i + 1 < n:
                out.append(line[i:i + 2])
                i += 2
                continue
            if ch == '"':
                quote = None
            out.append(ch)
        else:
            if ch in '\'"':
                quote = ch
                out.append(ch)
            elif ch == '#' and (i == 0 or line[i - 1] in ' \t'):
                break
            else:
                out.append(ch)
        i += 1
    return ''.join(out)


def _scalar(tok):
    """单个标量：单引号（``''`` 转义）/ 双引号（JSON 风格转义）/ 裸值原样。"""
    tok = tok.strip()
    if len(tok) >= 2 and tok[0] == "'" and tok[-1] == "'":
        return tok[1:-1].replace("''", "'")
    if len(tok) >= 2 and tok[0] == '"' and tok[-1] == '"':
        try:
            return ast.literal_eval(tok)
        except (ValueError, SyntaxError):
            raise YamlError('双引号标量解不开：%r' % tok)
    return tok


def _split_key(content):
    """``键: 值`` 的切分点 = 第一个「引号外、后跟空格或行尾」的冒号。"""
    quote = None
    i = 0
    n = len(content)
    while i < n:
        ch = content[i]
        if quote:
            if ch == quote:
                if quote == "'" and content[i + 1:i + 2] == "'":
                    i += 2
                    continue
                quote = None
            elif quote == '"' and ch == '\\':
                i += 2
                continue
        elif ch in '\'"':
            quote = ch
        elif ch == ':' and (i + 1 == n or content[i + 1] in ' \t'):
            key = content[:i].strip()
            if key == '':
                return None
            return _scalar(key), content[i + 1:]
        i += 1
    return None


def _split_flow(inner):
    """按顶层逗号切 flow 序列 / flow 映射的元素（引号与嵌套括号里的逗号不算）。"""
    items = []
    buf = []
    depth = 0
    quote = None
    for ch in inner:
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in '\'"':
            quote = ch
            buf.append(ch)
            continue
        if ch in '[{':
            depth += 1
        elif ch in ']}':
            depth -= 1
        if ch == ',' and depth == 0:
            items.append(''.join(buf))
            buf = []
            continue
        buf.append(ch)
    items.append(''.join(buf))
    return [x.strip() for x in items if x.strip() != '']


class _Doc(object):
    """按缩进的递归下降解析器（只覆盖工作流用到的 YAML 子集）。"""

    def __init__(self, text):
        self.lines = text.split('\n')
        self.i = 0

    # --- 行游标 ---------------------------------------------------------
    def _peek(self):
        """→ ``(缩进, 内容)``；空行与整行注释就地跳过（它们不携带结构）。"""
        while self.i < len(self.lines):
            body = _strip_comment(self.lines[self.i])
            if body.strip() == '':
                self.i += 1
                continue
            lead = body[:len(body) - len(body.lstrip(' '))]
            if '\t' in lead:
                raise YamlError('第 %d 行用 tab 缩进（本解析器只认空格）'
                                % (self.i + 1))
            return len(lead), body.strip()
        return None

    def _fail_indent(self, indent, want):
        raise YamlError('第 %d 行缩进是 %d，这里期望 %d'
                        % (self.i + 1, indent, want))

    # --- 入口 -----------------------------------------------------------
    def parse(self):
        first = self._peek()
        if first is None:
            raise YamlError('工作流文件是空的')
        if first[0] != 0:
            raise YamlError('顶层缩进不是 0（第 %d 行）' % (self.i + 1))
        value = self._block(0)
        if self._peek() is not None:
            raise YamlError('第 %d 行开始有本解析器没读懂的结构'
                            % (self.i + 1))
        return value

    # --- 块 -------------------------------------------------------------
    def _block(self, indent):
        head = self._peek()
        if head is None:
            return None
        ind, content = head
        if ind < indent:
            return None
        if ind > indent:
            self._fail_indent(ind, indent)
        if content == '-' or content.startswith('- '):
            return self._seq(ind)
        return self._map(ind)

    def _seq(self, indent):
        out = []
        while True:
            head = self._peek()
            if head is None:
                break
            ind, content = head
            if ind < indent:
                break
            if ind > indent:
                self._fail_indent(ind, indent)
            if not (content == '-' or content.startswith('- ')):
                break
            self.i += 1
            rest = content[1:].strip()
            if rest == '':
                out.append(self._block(indent + 2))
                continue
            if _split_key(rest) is not None:
                # `- key: value`：把这一行改写成 indent+2 处的映射首行再走映射分支
                self.i -= 1
                self.lines[self.i] = ' ' * (indent + 2) + rest
                out.append(self._map(indent + 2))
                continue
            out.append(_scalar(rest))
        return out

    def _map(self, indent):
        out = {}
        while True:
            head = self._peek()
            if head is None:
                break
            ind, content = head
            if ind < indent:
                break
            if ind > indent:
                self._fail_indent(ind, indent)
            if content == '-' or content.startswith('- '):
                break
            kv = _split_key(content)
            if kv is None:
                raise YamlError('第 %d 行不是「键: 值」：%r'
                                % (self.i + 1, content))
            key, rest = kv
            if key in out:
                raise YamlError('第 %d 行的键 %r 重复' % (self.i + 1, key))
            self.i += 1
            rest = rest.strip()
            if rest == '':
                nxt = self._peek()
                if nxt is None:
                    out[key] = None
                elif nxt[0] > indent:
                    out[key] = self._block(nxt[0])
                elif nxt[0] == indent and (nxt[1] == '-' or nxt[1].startswith('- ')):
                    # 序列与父键同缩进（`on:` 下面那种写法）
                    out[key] = self._block(indent)
                else:
                    out[key] = None
            elif rest[0] in '|>':
                out[key] = self._block_scalar(indent, rest)
            elif rest[0] == '[':
                out[key] = self._flow_seq(rest)
            elif rest[0] == '{':
                out[key] = self._flow_map(rest)
            else:
                out[key] = _scalar(rest)
        return out

    # --- 标量 -----------------------------------------------------------
    def _flow_seq(self, text):
        text = text.strip()
        if not (text.startswith('[') and text.endswith(']')):
            raise YamlError('flow 序列没有闭合：%r' % text)
        inner = text[1:-1].strip()
        if inner == '':
            return []
        return [_scalar(x) for x in _split_flow(inner)]

    def _flow_map(self, text):
        text = text.strip()
        if not (text.startswith('{') and text.endswith('}')):
            raise YamlError('flow 映射没有闭合：%r' % text)
        inner = text[1:-1].strip()
        out = {}
        if inner == '':
            return out
        for item in _split_flow(inner):
            kv = _split_key(item)
            if kv is None:
                raise YamlError('flow 映射里的元素不是「键: 值」：%r' % item)
            out[kv[0]] = _scalar(kv[1].strip())
        return out

    def _block_scalar(self, parent_indent, header):
        """``run: |`` / ``run: >`` —— 正文逐行原样保留（脚本注释留到扫描时才处理）。"""
        if re.match(r'^[|>][+-]?\d*\s*$', header) is None:
            raise YamlError('块标量头看不懂：%r' % header)
        style = header[0]
        body = []
        block_indent = None
        while self.i < len(self.lines):
            line = self.lines[self.i]
            if line.strip() == '':
                body.append('')
                self.i += 1
                continue
            ind = len(line) - len(line.lstrip(' '))
            if ind <= parent_indent:
                break
            if block_indent is None:
                block_indent = ind
            if ind < block_indent:
                raise YamlError('第 %d 行的块标量缩进比首行浅'
                                % (self.i + 1))
            body.append(line[block_indent:])
            self.i += 1
        while body and body[-1] == '':
            body.pop()
        if style == '|':
            return '\n'.join(body) + ('\n' if body else '')
        # 折叠标量：空行分段，段内相邻行用一个空格连起来
        folded = []
        for line in body:
            if line == '':
                folded.append('\n')
            elif folded and folded[-1] not in ('\n',):
                folded.append(' ' + line)
            else:
                folded.append(line)
        return ''.join(folded) + ('\n' if folded else '')


def parse_yaml(text):
    return _Doc(text).parse()


# ---------------------------------------------------------------- 仓库事实
def _literal_assigns(path):
    """文件里的模块级 ``NAME = <字面量>`` → ``{名字: 值}``（取不到就跳过）。"""
    with io.open(path, encoding='utf-8-sig') as fh:
        tree = ast.parse(fh.read())
    out = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
            continue
        try:
            out[node.targets[0].id] = ast.literal_eval(node.value)
        except (ValueError, SyntaxError):
            continue
    return out


def read_python_files(pyproject_path):
    """只取 ``[tool.pytest.ini_options]`` 的 ``python_files``（单行数组）。

    为什么不用 tomllib：它要 Python 3.11+，而本工具要能在 3.9/3.10 上跑（谁都能本地
    自查）。这里只认"键在行首、值是单行数组"这一种写法，并用 ``ast.literal_eval`` 校验；
    形状一变就报错（rc=2），不猜。
    """
    section = None
    with io.open(pyproject_path, encoding='utf-8-sig') as fh:
        for raw in fh:
            line = raw.split('#', 1)[0].strip()
            if line == '':
                continue
            if line.startswith('[') and line.endswith(']'):
                section = line[1:-1].strip()
                continue
            if section != 'tool.pytest.ini_options':
                continue
            m = re.match(r'^python_files\s*=\s*\[(.*)\]$', line)
            if m:
                try:
                    value = ast.literal_eval('[' + m.group(1) + ']')
                except (ValueError, SyntaxError):
                    raise Unsupported('pyproject 的 python_files 解不开：%r' % line)
                return [str(x) for x in value]
    raise Unsupported('pyproject 里没找到 [tool.pytest.ini_options] 的 '
                      'python_files = [...]（覆盖模型依赖它：裸 pytest 收集哪些包装文件）')


def _suite_names(items):
    """SUITES 的条目是 ``(套件名, 额外环境变量)``；也容忍纯字符串。"""
    out = []
    for item in items or []:
        if isinstance(item, (tuple, list)) and item:
            out.append(str(item[0]))
        else:
            out.append(str(item))
    return out


class Facts(object):
    """判据要用到的仓库事实：pytest 收集面、登记豁免。"""

    def __init__(self, root, tests_dir=None):
        self.root = root
        self.tests_dir = tests_dir or os.path.join(root, 'tests')
        self.warnings = []
        pyproject = os.path.join(root, 'pyproject.toml')
        if not os.path.exists(pyproject):
            raise Unsupported('找不到 %s（覆盖模型要读 python_files）' % pyproject)
        self.pytest_files = read_python_files(pyproject)
        # 裸 pytest（无选择器）会跑到的套件集合：每个被收集的包装文件自己 + 它登记的套件
        self.pytest_universe = set()
        self.exempt = {}
        for name in self.pytest_files:
            path = os.path.join(self.tests_dir, name)
            stem = name[:-3] if name.endswith('.py') else name
            self.pytest_universe.add(stem)
            if not os.path.exists(path):
                self.warnings.append(
                    'pyproject 的 python_files 点名了 %s，但 %s 下没有它（跳过它的 SUITES）'
                    % (name, self.tests_dir))
                continue
            lit = _literal_assigns(path)
            for suite in _suite_names(lit.get('SUITES')):
                self.pytest_universe.add(suite)
            for suite, reason in (lit.get('SUITES_EXCLUDED') or {}).items():
                self.exempt[str(suite)] = str(reason)

    def on_disk(self, extra=()):
        """``tests/test_*.py`` 的套件名（``.``/``_`` 开头的探针文件天然不在 glob 里）。"""
        names = set()
        if os.path.isdir(self.tests_dir):
            for fname in os.listdir(self.tests_dir):
                if fname.startswith('test_') and fname.endswith('.py'):
                    names.add(fname[:-3])
        names.update(extra)
        return names

    def resolve_keyword(self, word):
        """``pytest -k`` 里的一个词 → 它点到的套件（对不上任何套件就报错）。"""
        word = word.strip().strip('\'"')
        if word == '':
            return set()
        cands = [word] if word.startswith('test_') else ['test_' + word, word]
        out = set(c + '' for c in cands
                  if os.path.exists(os.path.join(self.tests_dir, c + '.py')))
        if not out:
            raise Unsupported('pytest -k 里的关键字 %r 对不上 tests/ 下任何套件'
                              '（覆盖检查会因此低估覆盖，先修工具）' % word)
        return out


# ---------------------------------------------------------------- 矩阵 / if
_COMPARE = re.compile(
    r"^matrix\.([A-Za-z0-9_.-]+)\s*(==|!=)\s*"
    r"(?:'([^']*)'|\"([^\"]*)\"|([A-Za-z0-9_.-]+))$")


def _split_top(text, op):
    """按顶层（引号与括号外）的连接符切分。"""
    parts = []
    buf = []
    depth = 0
    quote = None
    i = 0
    while i < len(text):
        ch = text[i]
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in '\'"':
            quote = ch
            buf.append(ch)
        elif ch == '(':
            depth += 1
            buf.append(ch)
        elif ch == ')':
            depth -= 1
            buf.append(ch)
        elif depth == 0 and text.startswith(op, i):
            parts.append(''.join(buf))
            buf = []
            i += len(op)
            continue
        else:
            buf.append(ch)
        i += 1
    parts.append(''.join(buf))
    return [p.strip() for p in parts]


def eval_condition(expr, leg):
    """只认 ``matrix.<维度> == '[取值]'`` / ``!=`` 及其 ``&&`` / ``||`` 组合。"""
    text = str(expr).strip()
    while text.startswith('(') and text.endswith(')'):
        text = text[1:-1].strip()
    for op in ('||', '&&'):
        parts = [p for p in _split_top(text, op) if p != '']
        if len(parts) > 1:
            vals = [eval_condition(p, leg) for p in parts]
            return any(vals) if op == '||' else all(vals)
    if text in ('always()', 'true'):
        return True
    if text == 'false':
        return False
    m = _COMPARE.match(text)
    if not m:
        raise Unsupported('工作流的 if 表达式看不懂：%r（先更新 tools/ci_suite_coverage.py）'
                          % expr)
    dim, op, single, double, bare = m.groups()
    rhs = single if single is not None else (double if double is not None else bare)
    if dim not in leg:
        raise Unsupported('if 里引用了矩阵维度 %r，但它不在腿 %r 里' % (dim, leg))
    same = str(leg[dim]) == str(rhs)
    return same if op == '==' else not same


def expand_legs(matrix):
    """``strategy.matrix`` → ``[{维度: 取值}]``（笛卡尔积 + include / exclude）。"""
    if matrix is None:
        return [{}]
    if not isinstance(matrix, dict):
        raise Unsupported('strategy.matrix 不是映射')
    dims = []
    include = []
    exclude = []
    for key in matrix:
        value = matrix[key]
        if key == 'include':
            if not isinstance(value, list) or any(not isinstance(x, dict) for x in value):
                raise Unsupported('matrix.include 的形状不认识')
            include = value
            continue
        if key == 'exclude':
            if not isinstance(value, list) or any(not isinstance(x, dict) for x in value):
                raise Unsupported('matrix.exclude 的形状不认识')
            exclude = value
            continue
        if not isinstance(value, list) or not value:
            raise Unsupported('matrix.%s 不是非空列表（本工具只展开显式列表）' % key)
        for item in value:
            if isinstance(item, (dict, list)):
                raise Unsupported('matrix.%s 里有非标量取值' % key)
        dims.append((key, value))
    keys = [k for k, _ in dims]
    legs = [dict(zip(keys, combo))
            for combo in itertools.product(*[v for _, v in dims])]
    for extra in include:
        legs.append(dict(extra))
    if exclude:
        kept = []
        for leg in legs:
            drop = False
            for ex in exclude:
                if all(str(leg.get(k)) == str(v) for k, v in ex.items()):
                    drop = True
                    break
            if not drop:
                kept.append(leg)
        legs = kept
    return legs


# ---------------------------------------------------------------- run 脚本
_FOR_RE = re.compile(r'\bfor\s+([A-Za-z_][A-Za-z0-9_]*)\s+in\s+([^;\n]*?)\s*(?:;|\n)\s*do\b')
_LITERAL_PATH = re.compile(r'tests/([A-Za-z_][A-Za-z0-9_]*)\.py')
_TOOL_PATH = re.compile(r'^(?:.*/)?tools/([A-Za-z_][A-Za-z0-9_]*)\.py$')
_PYNAME = re.compile(r'^python[\d.]*$')
_BOOL_WORDS = ('not', 'and', 'or')


def _join_continuations(script):
    """把 ``\\`` 续行拼回逻辑行（名单被拆行时文本匹配会漏，这里先拼）。"""
    out = []
    buf = ''
    for raw in script.split('\n'):
        line = raw.rstrip()
        if line.endswith('\\'):
            buf += line[:-1] + ' '
            continue
        out.append(buf + line)
        buf = ''
    if buf:
        out.append(buf)
    return out


def _drop_heredocs(lines):
    """丢掉 heredoc 正文（``<<'PY'`` 到 ``PY``）：里面的 Python 不是 shell 命令。"""
    out = []
    i = 0
    while i < len(lines):
        line = lines[i]
        out.append(line)
        m = re.search(r"<<-?\s*'?([A-Za-z_][A-Za-z0-9_]*)'?", line)
        i += 1
        if not m:
            continue
        tag = m.group(1)
        while i < len(lines):
            body = lines[i]
            out.append('')
            i += 1
            if body.strip() == tag:
                break
    return out


def _script_lines(script):
    """有效脚本行：拼续行 → 丢 heredoc 正文 → 丢整行注释。"""
    kept = []
    for line in _drop_heredocs(_join_continuations(script)):
        if line.strip().startswith('#'):
            continue                      # 脚本注释里提到套件名不算分配
        kept.append(line)
    return kept


def _split_commands(lines):
    """按 ``;`` ``&&`` ``||`` ``|`` 切成一条条命令（引号里的不算）。"""
    cmds = []
    for line in lines:
        buf = []
        quote = None
        i = 0
        while i < len(line):
            ch = line[i]
            if quote:
                buf.append(ch)
                if ch == quote:
                    quote = None
                i += 1
                continue
            if ch in '\'"':
                quote = ch
                buf.append(ch)
                i += 1
                continue
            if line.startswith('&&', i) or line.startswith('||', i):
                cmds.append(''.join(buf))
                buf = []
                i += 2
                continue
            if ch in ';|':
                cmds.append(''.join(buf))
                buf = []
                i += 1
                continue
            buf.append(ch)
            i += 1
        cmds.append(''.join(buf))
    return [c.strip() for c in cmds if c.strip() != '']


def _tokens(cmd):
    try:
        return shlex.split(cmd, posix=True)
    except ValueError:
        return cmd.split()


def _pytest_args(toks):
    """→ pytest 的实参列表；不是 pytest 调用则 ``None``。"""
    for i, tok in enumerate(toks):
        name = os.path.basename(tok).replace('.exe', '').lower()
        if name == 'pytest':
            return toks[i + 1:]
        if name == 'python' or _PYNAME.match(name):
            rest = toks[i + 1:]
            if rest[:1] == ['-m'] and rest[1:2] == ['pytest']:
                return rest[2:]
    return None


def _call_consts(node):
    """一个 Call 里出现过的全部字符串字面量（含列表/元组里的）。"""
    out = []
    for arg in list(node.args) + [kw.value for kw in node.keywords]:
        for sub in ast.walk(arg):
            if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                out.append(sub.value)
    return out


_TOOL_RUNNERS = ('run', 'Popen', 'call', 'check_output', 'check_call', 'main')


def _suites_from_tool(path, facts):
    """``python tools/x.py`` 若自己起 pytest（AST 认调用、不认注释），它让这条腿跑到哪些套件。"""
    if not os.path.exists(path):
        raise Unsupported('工作流调用了 %s，但文件不存在' % path)
    with io.open(path, encoding='utf-8-sig') as fh:
        tree = ast.parse(fh.read())
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        callee = getattr(node.func, 'attr', None) or getattr(node.func, 'id', None)
        if callee not in _TOOL_RUNNERS:
            continue
        consts = _call_consts(node)
        if 'pytest' not in consts:
            continue
        if '-k' not in consts:
            return set(facts.pytest_universe)      # 裸 pytest：收起全部包装文件 + SUITES
        got = set()
        for word in _k_words(consts[consts.index('-k') + 1:]):
            got |= facts.resolve_keyword(word)
        return got
    return set()


def _k_words(expr):
    """``-k`` 表达式 → 它点到的词（布尔连接词与 ``not`` 后面那个跳过）。"""
    text = ' '.join(expr).replace('(', ' ').replace(')', ' ')
    out = []
    skip_next = False
    for word in re.split(r'[\s]+', text):
        word = word.strip().strip('\'"')
        if word == '' or word in ('or', 'and', '|', '&'):
            continue
        if word == 'not':
            skip_next = True
            continue
        if skip_next:
            skip_next = False
            continue
        out.append(word)
    return out


def scan_run(script, facts):
    """一个 step 的 ``run:`` 在它适用的腿上跑到哪些套件。"""
    lines = _script_lines(script)
    joined = '\n'.join(lines)
    found = set()
    # ① bash 循环名单（只在循环体真的用它跑 tests/<var>.py 时才算名单）
    for var, body in _FOR_RE.findall(joined):
        if ('tests/$%s.py' % var) in joined or ('tests/${%s}.py' % var) in joined:
            for word in body.split():
                if word.startswith('test_') and re.match(r'^[A-Za-z0-9_]+$', word):
                    found.add(word)
    # ② 字面 tests/<名字>.py（循环变量 `tests/$t.py` 天然匹配不上）
    for name in _LITERAL_PATH.findall(joined):
        found.add(name)
    # ③ pytest 调用 + ④ 调到的工具自己起 pytest
    for cmd in _split_commands(lines):
        toks = _tokens(cmd)
        args = _pytest_args(toks)
        if args is not None:
            paths = [a for a in args if a.endswith('.py') and not a.startswith('-')]
            if '-k' in args:
                given = args[args.index('-k') + 1:] if args.index('-k') + 1 < len(args) else []
                for word in _k_words(given):
                    found |= facts.resolve_keyword(word)
            for path in paths:
                stem = os.path.basename(path)[:-3]
                if stem.startswith('test_'):
                    found.add(stem)
            if '-k' not in args and not paths:
                found |= set(facts.pytest_universe)   # 裸 pytest
        for tok in toks:
            m = _TOOL_PATH.match(tok.replace('\\', '/'))
            if m:
                found |= _suites_from_tool(
                    os.path.join(facts.root, 'tools', m.group(1) + '.py'), facts)
    return found


# ---------------------------------------------------------------- 主流程
def leg_label(leg):
    return '/'.join('%s=%s' % (k, leg[k]) for k in sorted(leg))


def analyze(wf, facts):
    """→ (腿列表, {腿: 套件集合})。"""
    jobs = wf.get('jobs')
    if not isinstance(jobs, dict):
        raise Unsupported('工作流里没有 jobs（或形状不认识）')
    legs = []
    per_leg = {}
    for job_name in jobs:
        job = jobs[job_name]
        if not isinstance(job, dict):
            raise Unsupported('job %r 不是映射' % job_name)
        steps = job.get('steps')
        if not isinstance(steps, list):
            continue                        # 复用型 job（uses:）不跑套件
        matrix = None
        strategy = job.get('strategy')
        if isinstance(strategy, dict):
            matrix = strategy.get('matrix')
        job_legs = expand_legs(matrix)
        for leg in job_legs:
            label = '%s %s' % (job_name, leg_label(leg))
            legs.append((label, leg))
            per_leg[label] = set()
        for step in steps:
            if not isinstance(step, dict):
                raise Unsupported('job %r 里有不是映射的 step' % job_name)
            run = step.get('run')
            if not isinstance(run, str):
                continue                    # uses: / with: 之类不跑套件
            hit = scan_run(run, facts)
            if not hit:
                continue
            for leg in job_legs:
                cond = step.get('if')
                if cond is None or eval_condition(cond, leg):
                    per_leg['%s %s' % (job_name, leg_label(leg))] |= hit
    return legs, per_leg


def run(root, ci_path, tests_dir=None, extra_suites=(), verbose=False, out=None):
    out = out or sys.stdout
    facts = Facts(root, tests_dir)
    if ci_path == '-':
        text = sys.stdin.read()
        shown = '<stdin>'
    else:
        shown = ci_path
        if not os.path.exists(ci_path):
            raise Unsupported('找不到工作流文件：%s' % ci_path)
        with io.open(ci_path, encoding='utf-8-sig') as fh:
            text = fh.read()
    wf = parse_yaml(text)
    legs, per_leg = analyze(wf, facts)
    assigned = set()
    for label in per_leg:
        assigned |= per_leg[label]
    universe = facts.on_disk(extra_suites)
    unassigned = sorted(universe - assigned)
    covered_exempt = [s for s in unassigned
                      if s in facts.exempt or s in EXTRA_EXEMPT]
    missing = [s for s in unassigned if s not in covered_exempt]

    out.write('== CI 套件覆盖检查（MAINTENANCE 25.9）==\n')
    out.write('工作流: %s\n' % shown)
    out.write('腿（矩阵单元）: %d 条\n' % len(legs))
    for label, _leg in legs:
        if verbose:
            names = sorted(per_leg.get(label, ()))
            out.write('  %-32s %2d 个: %s\n'
                      % (label, len(names), ' '.join(names) or '-'))
        else:
            out.write('  %-32s %2d 个套件\n' % (label, len(per_leg.get(label, ()))))
    for warning in facts.warnings:
        out.write('注意: %s\n' % warning)
    out.write('tests/ 下的套件: %d\n' % len(universe))
    out.write('已分配（至少落一条腿）: %d\n' % len(universe & assigned))
    # "至少一条腿"是本项的判据；下面的分布**只报不判** —— 只在 1 条腿上的套件正是
    # §25.9 说的"某些版本上根本不跑"，关掉它要改发布链路（每条腿都跑 = CI 时间乘上去），
    # 那不是这条检查该拦的事，但值得每次都看见。
    counts = {}
    for name in sorted(universe & assigned):
        legs_hit = sum(1 for label in per_leg if name in per_leg[label])
        counts.setdefault(legs_hit, []).append(name)
    if counts:
        out.write('覆盖腿数分布: %s\n'
                  % ' / '.join('%d 条腿 %d 个' % (n, len(counts[n]))
                               for n in sorted(counts, reverse=True)))
        if verbose and 1 in counts:
            out.write('  只在 1 条腿上（版本缺口，见 CONTRIBUTING 的已知缺口）: %s\n'
                      % ' '.join(sorted(counts[1])))
    exempt_names = [s for s in sorted(universe)
                    if s not in assigned
                    and (s in facts.exempt or s in EXTRA_EXEMPT)]
    if exempt_names:
        out.write('登记豁免: %d（每条都带理由，见下）\n' % len(exempt_names))
        for name in exempt_names:
            reason = facts.exempt.get(name) or EXTRA_EXEMPT[name]
            where = 'SUITES_EXCLUDED' if name in facts.exempt else 'EXTRA_EXEMPT'
            out.write('  - %s [%s]: %s\n' % (name, where, reason))
    if not missing:
        out.write('OK: 每个套件至少落在一条腿的名单里\n')
        return 0
    out.write('FAIL: %d 个套件没有被任何一条腿分配到：\n' % len(missing))
    for name in missing:
        out.write('  - %s（tests/%s.py 在，但工作流里没有任何一条腿的名单点到它）\n'
                  % (name, name))
    out.write('怎么办：加进 tests/test_suites.py 的 SUITES（3.12 腿的 pytest 会跑），'
              '或加进工作流某条腿的套件列表；\n'
              '        确认确实不该在 CI 上跑，就在 SUITES_EXCLUDED（套件侧）或 '
              'tools/ci_suite_coverage.py 的 EXTRA_EXEMPT（CI 侧）里登记理由。\n')
    return 1


def main(argv=None):
    ap = argparse.ArgumentParser(
        description='CI 套件覆盖检查（MAINTENANCE 25.9）：每个 tests/test_*.py '
                    '至少落在一条腿（工作流矩阵单元）的名单里。')
    ap.add_argument('--root', default=None,
                    help='仓库根（默认 tools/ 的上一级）')
    ap.add_argument('--ci', default=None,
                    help='工作流文件（默认 <root>/.github/workflows/tests.yml；'
                         '给 - 表示从 stdin 读，反向探测用）')
    ap.add_argument('--tests-dir', default=None,
                    help='套件目录（默认 <root>/tests）')
    ap.add_argument('--extra-suite', action='append', default=[], metavar='NAME',
                    help='把 NAME 当作 tests/ 下新增的套件（自检用；只增不减，'
                         '加了它只会更红，永远不会更绿）')
    ap.add_argument('-v', '--verbose', action='store_true',
                    help='打印每条腿的套件名')
    args = ap.parse_args(argv)              # 用法错时 argparse 自己返回 2
    root = (os.path.abspath(args.root) if args.root
            else os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    ci_path = args.ci if args.ci is not None else os.path.join(
        root, '.github', 'workflows', 'tests.yml')
    tests_dir = os.path.abspath(args.tests_dir) if args.tests_dir else None
    try:
        return run(root, ci_path, tests_dir, args.extra_suite, args.verbose)
    except (YamlError, Unsupported) as exc:
        sys.stdout.write('NG(rc=2) 没法给出结论: %s\n' % exc)
        return 2


if __name__ == '__main__':
    sys.exit(main())
