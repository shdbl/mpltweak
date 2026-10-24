# -*- coding: utf-8 -*-
"""tests/test_docs.py —— 文档锚点 / 链接回归网（0.1.10）

为什么需要这一层（真实教训，不是假想）
====================================
* 0.1.9 的文档大重写**静默删掉**了「命令在哪敲」那三行（`3263a0a` 加入 → `6863f02` 删除），
  **没有任何测试会报警** —— README 少一段，没有一个用例会变红。
* t32（给一级章节加 `---` + 编号 1~15）与 t33（找回「命令在哪敲」等 A 级 6 条）两轮改动
  **都靠一次性脚本兜锚点**，没沉淀成回归网。
* 章节**编号 / 改名 / 移动都会改变锚点**（`## 它解决什么问题` → `## 1. 它解决什么问题`
  ⇒ 锚点 `#它解决什么问题` → `#1-它解决什么问题`）。仓库此前对这一层**零守卫**。

本套件检查七类（纯文本、**零网络**、秒级；**代码围栏内的示例文本不参与校验**，那里是样例）
================================================================================
① **文件内锚点**：`](#slug)` 必须命中**同文件**某个标题的 slug；
② **相对链接**：`docs/x.md` / `../README.md` / `x.md#slug` 的**目标文件必须存在**；带 `#slug`
   的还要在**目标文件**里存在该 slug（跨文档锚点也管）；
③ **相对图片**：`![](相对路径)` 必须存在；`http(s)://` 一律跳过，**不发任何网络请求**；
④ **外部链接**：`http(s)://` 只做**格式**检查（scheme + host 形如域名），同样不发请求；
⑤ **自仓 URL**（0.1.11 扩展，见下）：`https://github.com/<owner>/<repo>/blob|tree/<默认分支>/<path>`
   **解析回仓库内相对路径**，再按 ② 同款口径查"文件/目录存在 + 跨文档锚点存在"。
⑥ **表格列数自洽（转义感知）**（0.1.11 扩展，见下）：每个表格块里**每个行**（表头、分隔行、
   数据行）的单元格数必须等于**表头**格数；`\|` 是字面竖线、**不**参与切分。
⑦ **`文件.py:行号` 引用闸门（判据级）**（0.1.13 扩展，见下 + `tools/check_doc_refs.py`）：
   裸 `:NNN` 继承同段文件名 / 符号锚 `# 符号` 必须在**被引的那一行**搜得到（窗口默认 0，
   `--anchor-window N` 才放宽；0.1.14 §28-A1）/ 三张"带理由豁免表"
   的纪律，各配 teeth 与反向验证。**刻意不含"真仓库必须 0 可疑"的断言**（原因见 ⑦ 段）。

⑤ 为什么必须有（**真实口径漏洞，不是假想**）
==========================================
两份 README 引 `docs/*.md` 用的**不是相对链接**，而是**绝对自仓 URL**（如
`https://github.com/shdbl/mpltweak/blob/main/docs/interaction.md`）—— 全文各 30 余条。
旧口径下这些链接落到 ④，**只查格式**（`github.com` 有域名、格式永远合法）⇒
**`docs/` 里任何一份被改名/删除，没有任何用例会变红**，而 README 是用户第一入口。
⑤ 把这类 URL 映射回仓库路径后按 ② 查，缺口才真正补上。口径上"**宁可少判也不要误报**"：
* 只认 `owner/repo` 与 `pyproject.toml` 的 `Homepage` **完全一致**的自仓 URL；外部仓库 → 交给 ④；
* 只认**默认分支 `main`**；README 里引的证据分支（`blob/evidence-0.1.10/…`）本地不存在该分支，
  硬查必然**误报** ⇒ 一律跳过；
* `tree/<dir>` 查目录、`blob/<file>` 查文件（有 `isdir` 探针时）；锚点只在目标是**已扫描的 `.md`**
  时查（与 ② 同款：目标不在扫描集里就不硬判）。

⑥ 为什么必须有（**同一类问题今晚出现 3 次，每次都是人工临时探针抓的**）
====================================================================
1. `MAINTENANCE.md` 某行一个**未转义**的 `|` ⇒ 该行被多切一格（表格错位）；
2. `docs/reliability.md` 的「来源」行只有 **2 格**而表头 **3 格**（**真缺陷**；提交版 HEAD 里是第
   204 行 `| 来源 | … |`，doc-owner 补 `| — |` 修掉）；
3. 同一张表里 `\\|Δ\\|` / `\\|G2−G0\\|∞` 的**假阳性** —— 朴素 `split('|')` 把它们数成 5 / 7 格。
⇒ 这条判据必须**转义感知**，且**宁可少判也不误报**：只认规范写法（行首 `|` + 分隔行），

⑦ 为什么必须有（**"0 处问题"曾经是弱证据**）
============================================
`tools/check_doc_refs.py`（发布期人工过目的引用闸门）加严前只能发现"解析不到 / 越界 / 空行"
三类硬伤，**实测抽样 5 条里 4 条已经指错地方而闸门全绿**；`t72` 又实测到「`t80` 修过的 4 个
锚点这次**又全漂 +5~+13**」—— 判据只看"存在性"，**指错函数它看不见**。0.1.13 给它加了三条：
裸 `:NNN`（继承同句/同段上一处文件名，否则 `docs/verify.md:10` 后半那两处永远不查）、
符号锚 `file.py:2255 # _prune_backups`（被引的那一行必须搜得到该符号 —— 窗口默认 0，
`--anchor-window N` 放宽；0.1.14 §28-A1；搜不到报 `symbol_mismatch`，
并打印**最近一次出现的行号**）、以及"哪些引用可以不锚"的**带理由豁免口径**（三张表，键都不含
文档行号，棘轮只准缩小）。本套件只搬**判据级**的东西进 CI：

* `t103` 的四方向（src 副本优先 / 裸引用越界 / 同名多命中 / 只有 `build/` 副本）——
  **直接跑工具自带的 `--self-test`**（同一套夹具跑两遍没有意义，不重复造第二套）；
* 三条新判据各一条合成夹具 teeth（裸引用继承 / 误认守卫 / 不跨段落、符号锚对错与窗口、
  未锚必红 + 登记豁免 + 表优先级 + 失效条目提示）；
* 每条新判据各一条**反向验证**（把判据弄坏 ⇒ 必须变红），含对**真仓库**的两条：
  清空豁免表 ⇒ 可疑数必须变多、关掉裸引用支持 ⇒ 引用条数必须变少。

**为什么不把"真仓库 rc=0"写成断言**：那等于把测试与被守卫的代码行号耦死 —— 任何改动
`writeback.py` 的任务都会把它打红（0.1.13 的 P0-1 改一行就让 3 条引用落空行，正是实例）；
这个仓库对"会天天红、最后被放宽"的断言有明确结论：**它是发布期人工闸门，不是 CI 断言**。
CI 只管"判据本身有没有牙"。
`\\|` 计为字面竖线、不切分。

slug 算法必须**自证**，不许靠约定
================================
t32 就在标题里的 `·`（U+00B7）上卡过一次：它在 GitHub slug 里**是否被剔除因渲染器而异**，
最后靠改标题消除歧义。所以：
* `slug()`：小写 → 删除非「字母 / 数字 / 空格 / `-` / `_` / CJK 等字母类」的字符 → 空格换 `-`；
* **用仓库现有链接反验**：每条 `](#slug)` 与 `x.md#slug` 都必须命中对应文件的标题 slug
  （本套件的口径就是**命中率必须 100%**，失败清单逐条打印）；
* 表驱动固定用例（正是 t32 的实例）：`## 4. 安装 + 快速开始` → `#4-安装--快速开始` 等。

歧义字符棘轮（两道；**R1 优先于 R2**）
====================================
* **R1（零容忍，不受基线豁免）**：**凡是被链接引用的标题**（文件内锚点或跨文档引用）一律
  不许含歧义字符（`·` U+00B7 / `—` U+2014 / `–` U+2013 / `…` U+2026 / NBSP / ZWSP / emoji）
  ⇒ 报红并**指出该标题**。理由：只有被引用的标题才真正承担锚点语义，而这类字符的 slug 行为
  **因渲染器而异** ⇒ 正是"锚点靠猜"的风险点。
* **R2（冻结基线，只拦新增）**：未被引用的标题里的歧义字符，按 **(文件, 标题文本)** 冻结在下面的
  `AMBIG_BASELINE` 里（**不用行号** —— 行号会漂）。**新增即红**；基线里已不存在的条目 ⇒ 打印
  "请删掉该条目"（与 `tests/test_hygiene.py` 的 `BASELINE` 同款棘轮语义）。每次运行**逐条打印**。
* 冻结的是仓库里**当前已有**的 33 处（CHANGELOG 18 / README.md 5 / README.en.md 5 /
  MAINTENANCE.md 3 / docs/agent.md 1 / skills/mpltweak/SKILL.md 1）。它们**都不被任何链接引用**，
  所以不影响锚点正确性；要把它们清零，改的是那几份文档（**不在本套件范围**）——清完请**从基线
  里删掉**对应条目（届时本套件会打印"请删掉"提示）。

teeth（必须能咬人）
==================
对**合成 markdown 字符串**注入已知错误 ⇒ 检查器**必须报出**：断锚点 / 不存在的相对链接 /
不存在的图片 / 跨文档断锚点 / 坏外链 / 被引用的歧义标题（R1）/ 新增歧义标题（R2）/
"R1 与 R2 同时命中时 R1 先报"。⑤ 另有两条**误报守卫**（宁可不判，也不许误报）：
别的分支的自仓 URL、别人的仓库 URL 都**必须不报**。再加两条**反向验证**（把判据弄坏就必须变红）：
把 `slug()` 换成坏实现 ⇒ 真仓库的断锚点数 > 0；把 `AMBIG_BASELINE` 清空 ⇒ R2 必须报出全部 34 处。
⑤ 的反向验证在 `run_real` 的"README 覆盖钉"（README 若被移出扫描面或链接不再被解析 ⇒ 直接红）。

口径（仍需人工判断的边界，写清不藏）
==================================
1. 只校验 `*.md`；**代码围栏内**（``` / ~~~）的链接不校验（示例文本）；
2. 锚点只认**标题**（不含 HTML 锚 `<a name=...>`；仓库当前没有用到）；
3. 跨文档锚点按**目标文件 basename** 定位标题（同名文件在不同目录时可能混，仓库当前无此情况）；
4. 外部链接**只查格式**，不查可达性（零网络是硬约束）；
5. 歧义字符只针对**标题**；正文里的 `·`/`—`/emoji 不拦（它们不参与 slug）；
6. 图片只查**相对路径存在**，不校验内容/尺寸；
7. R2 是"冻结 + 只拦新增"，不是"不许存在" —— 基线条目**清完即删**，本套件不会替你去改文档。
"""
import io
import os
import re
import sys
import unicodedata
import urllib.parse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# GBK 控制台兜底（T13/T15 同款）：CI 是 UTF-8 永远绿，本地 cp936 下哪怕一个 emoji（基线里就有
# U+2705）或一个 U+FFFD 就能把整套从 ALL PASS 打成 rc=1 的假红。两层：① 输出流 errors='replace'；
# ② 进 print 前先清洗（_gbk_safe）。test_hygiene 的 GBK 棘轮按这两行的存在判定"装了兜底"。
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(errors='replace')
    except (AttributeError, ValueError, OSError):
        pass          # 被重定向成不支持 reconfigure 的对象时忽略即可


def _gbk_safe(s):
    """把字符串清洗成"当前输出流编得出来"的形式。"""
    s = str(s)
    enc = getattr(sys.stdout, 'encoding', None) or 'utf-8'
    try:
        return s.encode(enc, 'replace').decode(enc, 'replace')
    except (LookupError, UnicodeDecodeError):
        return s.encode('ascii', 'backslashreplace').decode('ascii')


def _p(line=''):
    print(_gbk_safe(line))


_fails = []


def check(name, cond, detail=''):
    mark = 'OK' if cond else 'FAIL'
    _p('%s | %s%s' % (mark, name, (' | ' + str(detail)) if detail else ''))
    if not cond:
        _fails.append(name)


# ---------------------------------------------------------------- 扫描范围
SKIP_DIRS = {'.git', '__pycache__', '.pytest_cache', 'node_modules', '.eggs',
             'build', 'dist', '.tweak_params', '.idea', '.vscode'}


def _skip_dir(name):
    return name in SKIP_DIRS or name.startswith('.tmp') or name.startswith('tmp_')


NOT_SHIPPED_MD = {'MAINTENANCE.md'}


def md_files(root=ROOT):
    """仓库里全部 *.md（相对路径，POSIX 风格）；跳过 .git / 缓存 / 临时目录。

    另外跳过**不入库的本地手册**（``NOT_SHIPPED_MD``，约定：``MAINTENANCE.md`` 不进仓库）。
    必须排除，否则同一套检查在两种环境里结果不同：
      · 开发机：文件在 → 它的标题被计入 R2 基线；
      · CI / 干净检出：文件不在 → 同一批基线条目"已不复存在" → **R2 误报红**。
    （教训 2026-10-13：冻结基线一旦引用了未入库文件，CI 上必炸。）
    """
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not _skip_dir(d)]
        for f in sorted(filenames):
            if f.endswith('.md') and f not in NOT_SHIPPED_MD:
                out.append(os.path.relpath(os.path.join(dirpath, f), root).replace('\\', '/'))
    return sorted(out)


def load_files(root=ROOT):
    """→ {relpath: text}（只含 .md；显式 UTF-8）。"""
    return {rel: io.open(os.path.join(root, rel), encoding='utf-8', newline='').read()
            for rel in md_files(root)}


# ---------------------------------------------------------------- slug + 解析
def slug(title):
    """GitHub 风格锚点：小写 → 删非「字母/数字/空格/-/_」→ 空格换 `-`（保留 CJK）。"""
    s = title.strip().lower()
    keep = []
    for ch in s:
        if ch.isspace() or ch in '-_' or ch.isalnum() or unicodedata.category(ch).startswith('L'):
            keep.append(ch)
    return ''.join(keep).replace(' ', '-')


_SLUG_CASES = [
    # t32 的真实实例（当时逐条推导、并与 GitHub 现有链接反验过）
    ('4. 安装 + 快速开始', '4-安装--快速开始'),
    ('11. 执行边界：哪些命令会跑你的脚本', '11-执行边界哪些命令会跑你的脚本'),
    ('1. 它解决什么问题', '1-它解决什么问题'),
    ('12. 你这个脚本能帮上吗？两个问题', '12-你这个脚本能帮上吗两个问题'),
    ('原位改数字，还是插入调整块', '原位改数字还是插入调整块'),
    ('不适用场景（先说清楚）', '不适用场景先说清楚'),
    ('License', 'license'),
    ('5-minute quick start', '5-minute-quick-start'),
    ('Execution boundaries: which commands run your script',
     'execution-boundaries-which-commands-run-your-script'),
    ('When it does not apply (up front)', 'when-it-does-not-apply-up-front'),
    ('Can it help with your script? Two questions', 'can-it-help-with-your-script-two-questions'),
]

HEAD_RE = re.compile(r'^#{1,6}\s+(.*?)\s*$')
LINK_RE = re.compile(r'(!?)\[([^\]]*)\]\(\s*<?([^)\s>]+)>?(?:\s+"[^"]*")?\s*\)')
FENCE_RE = re.compile(r'^(```+|~~~+)')
HTTP_RE = re.compile(r'^https?://')
HOST_OK_RE = re.compile(r'^https?://[^\s/]+\.[^\s/]+')
SCHEME_RE = re.compile(r'^[a-zA-Z][a-zA-Z0-9+.\-]*:')

# ---- ⑤ 自仓 URL：README 引 docs/ 用的就是这种写法（旧口径只查格式 ⇒ 等于没查）----
DEFAULT_BRANCH = 'main'
SELF_REPO_RE = re.compile(
    r'^https?://github\.com/(?P<owner>[^/\s]+)/(?P<repo>[^/\s]+)/'
    r'(?P<kind>blob|tree)/(?P<branch>[^/\s]+)/(?P<path>[^#?\s]+)')
README_DOCS = ('README.md', 'README.en.md')


def _self_repo_slug(root=ROOT):
    """从 `pyproject.toml` 的 `[project.urls] Homepage` 读 (owner, repo)；读不到 → None。

    刻意**不硬编码** owner/repo：仓库改名 / fork 后自仓 URL 的判据要跟着走，而不是悄悄失效。
    零网络、零 git 调用（只读一个本地文件）。
    """
    try:
        text = io.open(os.path.join(root, 'pyproject.toml'), encoding='utf-8').read()
    except (OSError, IOError):
        return None
    m = re.search(r'^Homepage\s*=\s*"https://github\.com/([^/"]+)/([^/"]+)"', text, re.M)
    return (m.group(1), m.group(2)) if m else None


SELF_REPO = _self_repo_slug()


def _self_repo_target(tgt, self_repo=None):
    """自仓 GitHub URL → `(仓库相对路径, 是否目录链接, 锚点)`；**非自仓 / 非默认分支 → None**。

    返回 None 的那些会落到 ④（只查格式）——这是刻意的"宁可少判也不要误报"：
    外部仓库、以及 `blob/evidence-0.1.10/…` 这类**别的分支**（本地根本没有那个分支）都不硬查。
    """
    slug = SELF_REPO if self_repo is None else self_repo
    if not slug:
        return None
    m = SELF_REPO_RE.match(tgt)
    if not m:
        return None
    if (m.group('owner').lower(), m.group('repo').lower()) != (slug[0].lower(), slug[1].lower()):
        return None
    if m.group('branch') != DEFAULT_BRANCH:
        return None
    path = urllib.parse.unquote(m.group('path')).rstrip('/')
    if not path:
        return None
    frag = ''
    q = tgt.find('#')
    if q >= 0:
        frag = urllib.parse.unquote(tgt[q + 1:].split('?')[0])
    return path, m.group('kind') == 'tree', frag


# 歧义字符：在 slug 里"是否被剔除"因渲染器而异的那些（t32 的 `·` 就是实例）
AMBIG_CHARS = {
    '\u00b7': 'MIDDLE DOT(U+00B7)',
    '\u2014': 'EM DASH(U+2014)',
    '\u2013': 'EN DASH(U+2013)',
    '\u2026': 'ELLIPSIS(U+2026)',
    '\u00a0': 'NBSP(U+00A0)',
    '\u200b': 'ZWSP(U+200B)',
}


def _is_emoji(ch):
    o = ord(ch)
    return ((0x1F300 <= o <= 0x1FAFF) or (0x2600 <= o <= 0x27BF) or
            (0x1F000 <= o <= 0x1F2FF) or (0xFE00 <= o <= 0xFE0F) or
            o in (0x2B50, 0x2705, 0x274C, 0x2728))


def ambig_chars(title):
    """标题里命中的歧义字符（→ [(字符, 说明)]）。"""
    out = []
    for ch in title:
        if ch in AMBIG_CHARS:
            out.append((ch, AMBIG_CHARS[ch]))
        elif _is_emoji(ch):
            out.append((ch, 'EMOJI(U+%04X)' % ord(ch)))
    return out


def _lines(text, skip_fences=True):
    """→ [(行号, 行文本)]；skip_fences=True 时丢掉代码围栏内的行。"""
    out, fence = [], None
    for i, line in enumerate(text.splitlines(), 1):
        if skip_fences:
            m = FENCE_RE.match(line.lstrip())
            if m:
                tok = m.group(1)[0] * 3
                if fence is None:
                    fence = tok
                elif line.lstrip().startswith(fence):
                    fence = None
                continue
            if fence is not None:
                continue
        out.append((i, line))
    return out


def headings(text, skip_fences=True):
    """→ [(行号, 标题文本)]（不含行首 `#`）。"""
    out = []
    for ln, line in _lines(text, skip_fences):
        m = HEAD_RE.match(line)
        if m:
            out.append((ln, m.group(1)))
    return out


# ---------------------------------------------------------------- ⑥ 表格列数自洽
# t48：同一类问题今晚出现 3 次 —— ①MAINTENANCE.md 某行未转义的 `|` 多切一格；
# ②docs/reliability.md「来源」行只有 2 格而表头 3 格（**真缺陷**；提交版 HEAD 第 204 行，
# doc-owner 补 `| — |` 修掉）；③同表 `\|Δ\|` / `\|G2−G0\|∞` 的**假阳性**（朴素 split 数成 5 / 7 格）。
# ⇒ 判据必须**转义感知**，且**宁可少判也不误报**。
#
# 判据（写清不藏）：
#   * 只认**规范写法**：行首（去空白后）是 `|`，且**表头行的下一行是分隔行**（`|---|:-:|` 之类）。
#     不满足就不算表格 —— 避免把正文里的 `|` 当表。
#   * 切分：只有**未转义**的 `|` 是定界符。判据 = 数 `|` 前连续反斜杠的个数，**奇数 = 被转义 =
#     字面竖线（不切）**。刻意**不用** `(?<!\\)\|`：那个正则漏掉 `\\|`（两个反斜杠 + 竖线 =
#     字面反斜杠 + 定界符，Markdown 里**该切**）。
#   * 围栏内不校验（与 ①~⑤ 同一个开关，复用 `_lines`）。
#   * 比对基准是**表头**格数；表头、分隔行、每个数据行都算，任一不等即报。
#
# 不纳入的东西（写清）：
#   * `MAINTENANCE.md` 不进仓库（`NOT_SHIPPED_MD`），**不进** `analyze()` 的扫描面 ——
#     它的标题若进 R1/R2 会跟"CI 上文件不存在"打架（见 `md_files` 的注释）。
#     但它是表格最多的一份、也正是本问题的发生地 ⇒ 用 `TABLE_EXTRA_FILES` 在**本机存在时**
#     补扫；CI 上文件不存在就跳过（不会假红）。代价是覆盖差：**本机更严、CI 不扫** ——
#     方向安全（本机拦得住、CI 不会因它假红），且与 `tools/check_doc_refs.py` 的
#     `--with-maintenance` 是同一个模式（船长 2026-10-14 裁定保留）。
#   * 非规范表格（无行首 `|`、无分隔行）不判 —— 宁可少判。
TABLE_EXTRA_FILES = ('MAINTENANCE.md',)

# 显式白名单（**当前为空** = 全仓 0 处真违规）：确有"故意列数不同"的表格时登记在这里。
# 键 = (仓库相对路径, 该行文本去空白)；值 = 理由字符串。**刻意不含行号**（行号会漂，
# 与 AMBIG_BASELINE 同款纪律）。棘轮纪律：宁可登记例外，**不许**为此放宽整条判据。
TABLE_COLS_BASELINE = {}


def split_row_cells(line):
    """转义感知切分一行表格 → 单元格列表（处理首 / 尾定界符）。"""
    s = line.strip()
    if s.startswith('|'):
        s = s[1:]
    cells, buf, bs = [], [], 0
    for ch in s:
        if ch == '\\':
            bs += 1
            buf.append(ch)
            continue
        if ch == '|' and bs % 2 == 0:
            cells.append(''.join(buf))
            buf = []
            bs = 0
            continue
        bs = 0
        buf.append(ch)
    cells.append(''.join(buf))
    if len(cells) > 1 and not cells[-1].strip():
        cells.pop()                       # 行尾定界符带出的空尾格
    return cells


def naive_row_cells(line):
    """**故意**的朴素切分：只用于反向验证，证明"转义感知"是承重的、不是装饰。"""
    return line.strip().strip('|').split('|')


def _is_table_row(line):
    return line.strip().startswith('|')


def _is_table_sep(line):
    s = line.strip()
    if not s.startswith('|'):
        return False
    body = s.strip('|')
    return bool(body) and '-' in body and all(c in '|-: ' for c in body)


def table_issues(text, rel, splitter=None, baseline=None):
    """→ ['文件:行号  表格列数不符（表头第 N 行 = X 格 / 本行 = Y 格）…']（⑥ 的核心）。"""
    sp = splitter or split_row_cells
    base = TABLE_COLS_BASELINE if baseline is None else baseline
    out, lines, i = [], _lines(text), 0
    while i < len(lines):
        hln, hline = lines[i]
        if _is_table_row(hline) and i + 1 < len(lines) and _is_table_sep(lines[i + 1][1]):
            want = len(sp(hline))
            j = i + 1
            while j < len(lines) and _is_table_row(lines[j][1]):
                rln, rline = lines[j]
                got = len(sp(rline))
                if got != want and (rel, ' '.join(rline.split())) not in base:
                    out.append('%s:%d  表格列数不符（表头第 %d 行 = %d 格 / 本行 = %d 格）%s%s'
                               % (rel, rln, hln, want, got,
                                  '【分隔行】' if _is_table_sep(rline) else '',
                                  rline.strip()[:70]))
                j += 1
            i = j
        else:
            i += 1
    return out


def table_count(text):
    """→ 识别到的表格块数（覆盖钉用：防"一个表都没识别到"导致的恒绿）。"""
    lines, n, i = _lines(text), 0, 0
    while i < len(lines):
        if _is_table_row(lines[i][1]) and i + 1 < len(lines) and _is_table_sep(lines[i + 1][1]):
            n += 1
            i += 1
            while i < len(lines) and _is_table_row(lines[i][1]):
                i += 1
        else:
            i += 1
    return n


def collect_table_issues(files, root=ROOT, extra=TABLE_EXTRA_FILES, splitter=None,
                         baseline=None):
    """扫描面 `files` + `extra`（在则扫、不在则跳过）上的 ⑥ 全部问题。"""
    out = []
    for rel in sorted(files):
        out += table_issues(files[rel], rel, splitter=splitter, baseline=baseline)
    for rel in extra:
        p = os.path.join(root, rel)
        if os.path.exists(p):
            out += table_issues(io.open(p, encoding='utf-8', newline='').read(), rel,
                                splitter=splitter, baseline=baseline)
    return out


def collect_table_count(files, root=ROOT, extra=TABLE_EXTRA_FILES):
    """→ (表格块数, 被扫到的文件名列表)（覆盖钉：证明 ⑥ 真的看到了表）。"""
    n, seen = 0, []
    for rel in sorted(files):
        n += table_count(files[rel])
        seen.append(rel)
    for rel in extra:
        p = os.path.join(root, rel)
        if os.path.exists(p):
            n += table_count(io.open(p, encoding='utf-8', newline='').read())
            seen.append(rel)
    return n, seen


# ---------------------------------------------------------------- ⑦ 测试表必须带证据链接
# 25.10（对外材料铁律：只展示"能证明什么"）：一张"跑过什么 → 得出什么"的表，行内不挂
# 指向原始证据的链接，读者就只能**选择相信**；挂了链接，那些数字才是**可复算的**。
# ⇒ 判据：**测试表的每一行都必须含一处 `evidence-*` 链接**。
#
# "测试行"怎么认（写清，不藏）：
#   * 行必须是**规范表格行**（与 ⑥ 同一判据：行首 `|`、上一行是分隔行；复用 `_lines` 跳围栏）；
#   * 且该行带**测试标记** `**测的是**`（中文表）或 `**What it tests**`（英文表）——
#     这就是仓库真实在用的列名（docs/reliability.md:8-13、README.md:123-127、README.en.md:125-129）。
#     靠列名认、而不是靠"第几张表"认：出处表 / 分母表 / 移交表（表头本身就是"证据 / 来源"）
#     是**另一类表**，不该被这条判据套上"每行都必须有链接"。
#   * 链接判据：同一行里出现 `evidence-...` 的字样（`evidence-0.1.10/...`、`evidence-0.1.11/...`
#     这类证据分支 / 目录）。**只查字样、不查可达性**（零网络，与 ④ 外链格式同款）。
#   * 围栏内不校验（与 ①~⑥ 同一个开关）。
#
# 不纳入的东西（写清）：
#   * 目录索引式表格（表头是"文件 / 说明"）不判 —— 它们不是测试结果行，没有"证据"可言。
#   * MAINTENANCE.md 用 `TABLE_EXTRA_FILES` 在**本机存在时**补扫（与 ⑥ 同款；CI 上不存在就跳过）。
TEST_EVIDENCE_MARKERS = ('**测的是**', '**What it tests**')
TEST_EVIDENCE_RE = re.compile(r'evidence-[0-9A-Za-z._-]+')

# 显式白名单（**当前为空** = 全仓测试行 0 处缺链接）：确有"该行本就不该带证据链接"时登记。
# 键 = (仓库相对路径, 该行文本去空白)；值 = 理由字符串。**刻意不含行号**（与
# TABLE_COLS_BASELINE 同款纪律：行号会漂）。棘轮纪律：宁可登记例外，**不许**放宽整条判据。
TEST_EVIDENCE_BASELINE = {}


def test_table_evidence_issues(text, rel, baseline=None):
    """⑦ 的核心：测试行里没有 `evidence-*` 链接 → ['文件:行号  测试行缺证据链接…']。"""
    base = TEST_EVIDENCE_BASELINE if baseline is None else baseline
    out, lines, i = [], _lines(text), 0
    while i < len(lines):
        hln, hline = lines[i]
        if _is_table_row(hline) and i + 1 < len(lines) and _is_table_sep(lines[i + 1][1]):
            j = i + 2                                    # i+1 是分隔行，从第一个数据行起
            while j < len(lines) and _is_table_row(lines[j][1]):
                rln, rline = lines[j]
                if (any(m in rline for m in TEST_EVIDENCE_MARKERS)
                        and not TEST_EVIDENCE_RE.search(rline)
                        and (rel, ' '.join(rline.split())) not in base):
                    out.append('%s:%d  测试行缺 `evidence-*` 证据链接（表头第 %d 行）%s'
                               % (rel, rln, hln, rline.strip()[:70]))
                j += 1
            i = j
        else:
            i += 1
    return out


def collect_test_evidence_issues(files, root=ROOT, extra=TABLE_EXTRA_FILES, baseline=None):
    """扫描面 `files` + `extra`（在则扫、不在则跳过）上的 ⑦ 全部问题。"""
    out = []
    for rel in sorted(files):
        out += test_table_evidence_issues(files[rel], rel, baseline=baseline)
    for rel in extra:
        p = os.path.join(root, rel)
        if os.path.exists(p):
            out += test_table_evidence_issues(
                io.open(p, encoding='utf-8', newline='').read(), rel, baseline=baseline)
    return out


def count_test_rows(files, root=ROOT, extra=TABLE_EXTRA_FILES):
    """→ 识别到的测试行总数（覆盖钉：防"一行都没认出来"导致的恒绿）。"""
    n, texts = 0, dict(files)
    for rel in extra:
        p = os.path.join(root, rel)
        if os.path.exists(p):
            texts[rel] = io.open(p, encoding='utf-8', newline='').read()
    for rel in sorted(texts):
        lines, i = _lines(texts[rel]), 0
        while i < len(lines):
            if _is_table_row(lines[i][1]) and i + 1 < len(lines) and _is_table_sep(lines[i + 1][1]):
                j = i + 2
                while j < len(lines) and _is_table_row(lines[j][1]):
                    if any(m in lines[j][1] for m in TEST_EVIDENCE_MARKERS):
                        n += 1
                    j += 1
                i = j
            else:
                i += 1
    return n


# ---------------------------------------------------------------- 核心分析器
def analyze(files, exists, slug_fn=slug, skip_fences=True, baseline=None,
            self_repo=None, isdir=None):
    """纯函数核心（teeth 直接在合成内容上调用它）。

    files    : {relpath: markdown 文本}（只含 .md）
    exists   : callable(relpath) -> bool（相对仓库根的路径是否存在；图片/其它文件都走它）
    baseline : R2 冻结基线 {(relpath, 标题文本)}；None → 用模块级 AMBIG_BASELINE
    self_repo: (owner, repo)；None → 用模块级 SELF_REPO（从 pyproject.toml 读出）
    isdir    : callable(relpath) -> bool；只给 ⑤ 的 `tree/` 链接判"是不是真目录"，
               为 None 时退化成 exists()（宁可少判：不硬判类型）
    返回 dict(kind -> [issue 描述])，kind ∈ anchor / rel / img / ext / selflink /
                                        ambig_linked(R1) / ambig_new(R2) / baseline_stale
    另有 res['stats']['checkable'] = {relpath: 该文件里**真正被解析成仓库内路径**的链接数}
    （② + ⑤ 之和）—— run_real 用它钉"README 的链接确实在被校验"，防止扫描面悄悄缩水。
    """
    base = AMBIG_BASELINE if baseline is None else baseline
    res = {k: [] for k in ('anchor', 'rel', 'img', 'ext', 'selflink', 'ambig_linked',
                           'ambig_new', 'baseline_stale')}
    res['stats'] = {'checkable': {}}
    heads = {rel: [(ln, t, slug_fn(t)) for ln, t in headings(text, skip_fences)]
             for rel, text in files.items()}
    slug_sets = {rel: {s for _ln, _t, s in hs} for rel, hs in heads.items()}

    # —— 链接四类 ——
    linked = set()          # {(目标文件 basename 或本文 basename, slug)} → 供 R1 判定
    for rel, text in files.items():
        base_name = os.path.basename(rel)
        for ln, line in _lines(text, skip_fences):
            for m in LINK_RE.finditer(line):
                is_img, label, tgt = m.group(1) == '!', m.group(2)[:34], m.group(3)
                where = '%s:%d' % (rel, ln)
                if tgt.startswith('#'):
                    frag = urllib.parse.unquote(tgt[1:])
                    linked.add((base_name, frag))
                    if frag not in slug_sets[rel]:
                        res['anchor'].append('%s  文件内锚点 `%s` 在本文件找不到对应标题（%s）'
                                             % (where, tgt, label))
                elif HTTP_RE.match(tgt):
                    st = _self_repo_target(tgt, self_repo) if not is_img else None
                    if st is not None:
                        # ⑤ 自仓 URL → 按 ② 的口径查（README 引 docs/ 走的就是这条）
                        path, is_tree, frag = st
                        res['stats']['checkable'][rel] = \
                            res['stats']['checkable'].get(rel, 0) + 1
                        if is_tree:
                            if not (isdir(path) if isdir else exists(path)):
                                res['selflink'].append(
                                    '%s  自仓目录链接目标不存在 `%s`（%s）' % (where, tgt, label))
                        elif not exists(path):
                            res['selflink'].append(
                                '%s  自仓文件链接目标不存在 `%s`（%s）' % (where, tgt, label))
                        elif isdir and isdir(path):
                            res['selflink'].append(
                                '%s  自仓链接用的是 `blob/` 但目标是目录（GitHub 上 404）`%s`（%s）'
                                % (where, tgt, label))
                        elif frag and path.endswith('.md') and path in files:
                            linked.add((os.path.basename(path), frag))
                            if frag not in slug_sets[path]:
                                res['anchor'].append(
                                    '%s  自仓 URL 锚点 `%s` 在 `%s` 里找不到对应标题（%s）'
                                    % (where, tgt, path, label))
                    elif not HOST_OK_RE.match(tgt) or ' ' in tgt:
                        res['ext'].append('%s  外部链接格式可疑 `%s`（%s）' % (where, tgt, label))
                elif SCHEME_RE.match(tgt):
                    continue                     # mailto: 等其它协议，跳过
                else:
                    path, _, frag = tgt.partition('#')
                    path = urllib.parse.unquote(path)
                    target = os.path.normpath(os.path.join(os.path.dirname(rel), path)) \
                        if path else rel
                    target = target.replace('\\', '/')
                    kind_long = '相对图片' if is_img else '相对链接'
                    if not is_img:
                        res['stats']['checkable'][rel] = \
                            res['stats']['checkable'].get(rel, 0) + 1
                    if not exists(target):
                        res['img' if is_img else 'rel'].append(
                            '%s  %s目标不存在 `%s`（%s）' % (where, kind_long, tgt, label))
                    elif frag and target.endswith('.md') and target in files:
                        frag_u = urllib.parse.unquote(frag)
                        linked.add((os.path.basename(target), frag_u))
                        if frag_u not in slug_sets[target]:
                            res['anchor'].append(
                                '%s  跨文档锚点 `%s` 在 `%s` 里找不到对应标题（%s）'
                                % (where, tgt, target, label))

    # —— R1 / R2（R1 优先：命中 R1 的不再算 R2）——
    baseline_hits = set()
    for rel, hs in heads.items():
        for ln, title, s in hs:
            chs = ambig_chars(title)
            if not chs:
                continue
            detail = '、'.join(n for _c, n in chs)
            if (os.path.basename(rel), s) in linked:
                res['ambig_linked'].append(
                    '%s:%d  【R1】被链接引用的标题含歧义字符：`%s`（%s）'
                    % (rel, ln, title, detail))
            elif (rel, title) in base:
                baseline_hits.add((rel, title))
            else:
                res['ambig_new'].append(
                    '%s:%d  【R2】新增歧义标题（不在冻结基线里）：`%s`（%s）'
                    % (rel, ln, title, detail))
    res['baseline_stale'] = sorted(set(base) - baseline_hits)
    return res


# ---------------------------------------------------------------- R2 冻结基线
# 说明（**必须保留**）：这是 **R2 的冻结基线** —— 只表示"这些歧义标题在写这一版时**已经存在**且
# **不被任何链接引用**"，不是"它们是正确的"。语义是**只拦新增**：
#   * 出现基线之外的歧义标题（含把某个已被冻结的标题**改名**后仍带歧义字符）⇒ **报红**；
#   * 基线里已不存在的条目 ⇒ 打印"请删掉该条目"（与 tests/test_hygiene.py 的 BASELINE 同款）；
#   * **清完请删条目**：哪天把这些标题里的 `·`/`—`/emoji 改掉了（改的是那些文档，不在本套件范围），
#     就把对应行从这里删掉 —— 本套件**不会**替你去改文档。
# 键 = (仓库相对路径, 标题文本；不含行首 `#`)。**刻意不用行号**：行号会漂。
AMBIG_BASELINE = {
    ('CHANGELOG.md', '0.1.9 · 详细修复台账（技术细节）'),
    ('CHANGELOG.md', '[0.1.0] — 2026-10-09'),
    ('CHANGELOG.md', '[0.1.1] — 2026-10-09'),
    ('CHANGELOG.md', '[0.1.2] — 2026-10-09'),
    ('CHANGELOG.md', '[0.1.3] — 2026-10-09'),
    ('CHANGELOG.md', '[0.1.4] — 2026-10-09（**已发布到 PyPI**）'),
    ('CHANGELOG.md', '[0.1.5] — 2026-10-09'),
    ('CHANGELOG.md', '[0.1.6] — 2026-10-09'),
    ('CHANGELOG.md', '[0.1.7] — 2026-10-10'),
    ('CHANGELOG.md', '[0.1.8] — 2026-10-10'),
    ('CHANGELOG.md', '修复 · CLI / API / MCP'),
    ('CHANGELOG.md', '修复 · GUI'),
    ('CHANGELOG.md', '修复 · 写回引擎'),
    ('CHANGELOG.md', '修复 · 第三波：退块判据改逐字段三态'),
    ('CHANGELOG.md', '修复 · 第二波（L0 复测暴露）'),
    ('CHANGELOG.md', '修复 · 第五波'),
    ('CHANGELOG.md', '修复 · 第四波：多图 stale + 验证链诚实 + 块锚点硬约束 + 刻度字号分列 + 新开关 --keep-unverified'),
    ('CHANGELOG.md', '文档 · 推广'),
    # 注意：**不要**把不入库的 `MAINTENANCE.md` 的标题写进基线 —— 它在 CI / 干净检出里
    # 不存在，会让这些条目"已不复存在"并使 R2 误报红（2026-10-13 实际踩到，见 md_files 注释）。
    # 2026-10-13（README 方案 A 落地）：两份 README 各 5 条 `### N · …` 步骤标题随分层压缩一起
    # 消失（那 5 步现在在正文的表与段落里，不再是小标题）⇒ 按本文件的语义**删掉这 10 条**；
    # 新结构里没有任何"被引用且含歧义字符"的标题，故**不新增**（判据本身、R1 与阈值一字未改）。
    ('docs/agent.md', '也可以接进 AI 客户端（MCP）——**可选适配层**'),
    ('skills/mpltweak/SKILL.md', 'mpltweak —— 交互式调图闭环'),
}          # ← 机器生成（见上面那段说明）；清完请删条目


# ---------------------------------------------------------------- 真仓库检查
def _print_issues(items, cap=40):
    for it in items[:cap]:
        _p('     · %s' % it)
    if len(items) > cap:
        _p('     · …（还有 %d 条，省略）' % (len(items) - cap))


def slug_selfproof():
    """① slug 规则的表驱动自证；② 用**仓库现有链接**反验（后者在 run_real 里）。"""
    _p('---- slug 自证（固定用例，含 t32 的真实实例）----')
    for title, want in _SLUG_CASES:
        got = slug(title)
        check('slug(`%s`) == #%s' % (title[:38], want), got == want, '实得 #%s' % got)


def run_real():
    files = load_files()
    exists = lambda p: os.path.exists(os.path.join(ROOT, p))          # noqa: E731
    isdir = lambda p: os.path.isdir(os.path.join(ROOT, p))            # noqa: E731
    res = analyze(files, exists, isdir=isdir)
    n_heads = sum(len(headings(t)) for t in files.values())
    _p('---- 真仓库：%d 个 *.md / %d 个标题 ----' % (len(files), n_heads))
    _p('（② 的反验即"下列锚点必须 0 处解析失败"；失败清单会逐条打印）')
    _p('（⑤ 自仓 URL 判据：owner/repo = %s，默认分支 = %s；'
       '读不到 pyproject.toml 时该项自动跳过）'
       % ('%s/%s' % SELF_REPO if SELF_REPO else '（未读到）', DEFAULT_BRANCH))
    checks = [
        ('anchor', '① 文件内锚点：全部命中同文件标题'),
        ('rel', '② 相对链接：目标文件全部存在'),
        ('img', '③ 相对图片：全部存在（http(s) 一律跳过、零网络）'),
        ('ext', '④ 外部链接：格式全部合法（不查可达性）'),
        ('selflink', '⑤ 自仓 URL：blob/tree 目标全部存在（只认自仓 + main 分支）'),
        ('ambig_linked', 'R1 被链接引用的标题：无歧义字符（零容忍）'),
        ('ambig_new', 'R2 新增歧义标题：无（基线之外的都算新增）'),
    ]
    for kind, label in checks:
        items = res[kind]
        check('%s —— 实得 %d 处' % (label, len(items)), not items,
              (items[0][:120] if items else ''))
        _print_issues(items)

    # —— ⑥ 表格列数自洽（转义感知）——
    _t_extra = [x for x in TABLE_EXTRA_FILES if os.path.exists(os.path.join(ROOT, x))]
    t_items = collect_table_issues(files)
    check('⑥ 表格列数自洽（转义感知 `\\|`；扫描 %d 份 *.md%s）—— 实得 %d 处'
          % (len(files), (' + ' + '、'.join(_t_extra)) if _t_extra else '',
             len(t_items)),
          not t_items, (t_items[0][:140] if t_items else ''))
    _print_issues(t_items)
    # 覆盖钉：⑥ 必须**真的识别到表格**，否则判据可能因"一个表都没认出来"而恒绿
    # （与 ⑤ 的 README 覆盖钉同款纪律）。
    _n_tab, _tab_files = collect_table_count(files)
    check('⑥ 覆盖钉：确实识别到表格（%d 个表块 / %d 份文件，扫描面完整）'
          % (_n_tab, len(_tab_files)),
          _n_tab > 0 and len(_tab_files) == len(files) + len(_t_extra),
          '表块 %d / 文件 %d（期望 %d）' % (_n_tab, len(_tab_files),
                                        len(files) + len(_t_extra)))

    # —— ⑦ 测试表每行必须带 `evidence-*` 证据链接 ——
    e_items = collect_test_evidence_issues(files)
    n_trows = count_test_rows(files)
    check('⑦ 测试表每行都必须含 `evidence-*` 链接（识别到的测试行 %d 行；扫描 %d 份 *.md%s）'
          '—— 实得 %d 处'
          % (n_trows, len(files), (' + ' + '、'.join(_t_extra)) if _t_extra else '', len(e_items)),
          not e_items, (e_items[0][:160] if e_items else ''))
    _print_issues(e_items)
    # 覆盖钉：⑦ 必须**真的认出测试行**，否则判据会因"一行都没认出来"而恒绿（与 ⑥ 同款纪律）。
    check('⑦ 覆盖钉：确实识别到测试行（%d 行 > 0）' % n_trows, n_trows > 0,
          '测试行 %d（期望 > 0）' % n_trows)

    # —— README 覆盖钉：两份 README 必须在扫描面内，且它们的链接**真的**被解析成仓库路径 ——
    # 不钉的话，将来把 README 移出扫描面（或链接风格一换）本套件会**静默**失去对第一入口的覆盖。
    # 判据用"② + ⑤ 合计 > 0"而不是"自仓 URL > 0"：README 若改回相对链接，这条不该跟着红。
    readme_n = {f: res['stats']['checkable'].get(f, 0) for f in README_DOCS}
    check('README 覆盖钉：两份 README 都在扫描面内、且其链接确实被校验（%s）'
          % '、'.join('%s=%d 条' % (f, n) for f, n in sorted(readme_n.items())),
          all(f in files for f in README_DOCS) and sum(readme_n.values()) > 0,
          '缺失文件 %s' % [f for f in README_DOCS if f not in files])

    _p('---- R2 冻结基线：%d 条（只拦新增；某条已不复存在时请从 AMBIG_BASELINE 删掉）----'
       % len(AMBIG_BASELINE))
    for rel, title in sorted(AMBIG_BASELINE):
        _p('     %s  %s' % (rel, title))
    stale = res['baseline_stale']
    if stale:
        _p('     提示：下列基线条目在仓库里已不存在（歧义字符已被改掉）→ 请从 AMBIG_BASELINE 删掉：')
        for rel, title in stale:
            _p('       - %s  %s' % (rel, title))
    check('R2 基线已装载（%d 条；失效条目只提示、不判红 —— 与 hygiene BASELINE 同语义）'
          % len(AMBIG_BASELINE), bool(AMBIG_BASELINE),
          '失效 %d 条' % len(stale))
    return files, exists


# ---------------------------------------------------------------- teeth
def _an(files, exists=None, slug_fn=slug, skip_fences=True, baseline=None,
        self_repo=None, isdir=None):
    return analyze(files, exists or (lambda p: p in files), slug_fn=slug_fn,
                   skip_fences=skip_fences, baseline=baseline,
                   self_repo=self_repo, isdir=isdir)


def teeth(real_files, real_exists):
    """合成 markdown 上注入已知错误 ⇒ 检查器必须报出；再把判据弄坏 ⇒ 必须变红。"""
    _p('---- teeth：合成样例注入已知错误 ----')
    clean = {'a.md': '# Title\n\n- [x](#title)\n- [y](b.md)\n- [z](b.md#second)\n'
                      '![img](pics/x.png)\n',
             'b.md': '# Second\n'}
    clean_exists = lambda p: p in ('a.md', 'b.md', 'pics/x.png')       # noqa: E731
    r = _an(clean, clean_exists)
    check('teeth⓪ 干净样例必须 0 问题（防"恒红"与"恒绿"两种假象）',
          not any(r[k] for k in ('anchor', 'rel', 'img', 'ext', 'ambig_linked', 'ambig_new')),
          '实得 %s' % {k: len(r[k]) for k in ('anchor', 'rel', 'img', 'ext')})

    r = _an({'a.md': '# Title\n\n[x](#nope)\n'})
    check('teeth① 断锚点必须被报出', len(r['anchor']) == 1 and '#nope' in r['anchor'][0],
          '实得 %s' % r['anchor'])

    r = _an({'a.md': '# T\n\n[x](docs/nope.md)\n'})
    check('teeth② 不存在的相对链接必须被报出', len(r['rel']) == 1 and 'nope.md' in r['rel'][0],
          '实得 %s' % r['rel'])

    r = _an({'a.md': '# T\n\n![i](pics/nope.png)\n'})
    check('teeth③ 不存在的相对图片必须被报出', len(r['img']) == 1, '实得 %s' % r['img'])

    r = _an({'a.md': '# T\n\n[x](b.md#nope)\n', 'b.md': '# Other\n'},
            exists=lambda p: p in ('a.md', 'b.md'))
    check('teeth④ 跨文档断锚点必须被报出（文件存在、slug 不存在）',
          len(r['anchor']) == 1 and 'b.md#nope' in r['anchor'][0] and not r['rel'],
          '实得 anchor=%s rel=%s' % (r['anchor'], r['rel']))

    r = _an({'a.md': '# T\n\n[x](http://nodot)\n'})
    check('teeth⑤ 坏外链格式必须被报出（只查格式、零网络）',
          len(r['ext']) == 1 and not r['rel'], '实得 %s' % r['ext'])

    amb = '# \u4fee\u590d \u00b7 GUI\n'
    r = _an({'a.md': amb + '\n[x](#\u4fee\u590d--gui)\n'})
    check('teeth⑥ R1：被链接引用的歧义标题必须报红（并在条目里标【R1】）',
          len(r['ambig_linked']) == 1 and '【R1】' in r['ambig_linked'][0]
          and not r['ambig_new'] and not r['anchor'],
          '实得 %s' % r['ambig_linked'])

    r = _an({'a.md': amb})
    check('teeth⑦ R2：新增歧义标题（未被引用、不在基线）必须报红（标【R2】）',
          len(r['ambig_new']) == 1 and '【R2】' in r['ambig_new'][0] and not r['ambig_linked'],
          '实得 %s' % r['ambig_new'])

    r = _an({'a.md': amb + '\n[x](#\u4fee\u590d--gui)\n'},
            baseline={('a.md', '\u4fee\u590d \u00b7 GUI')})
    check('teeth⑧ R1 优先于 R2：同时命中时只报 R1（基线豁免不了被引用的标题）',
          len(r['ambig_linked']) == 1 and not r['ambig_new'],
          'ambig_linked=%d ambig_new=%d' % (len(r['ambig_linked']), len(r['ambig_new'])))

    r = _an({'a.md': '# T\n'}, baseline={('a.md', '\u4e0d\u5b58\u5728 \u00b7 \u6807\u9898')})
    check('teeth⑨ 失效基线条目必须被报出（提示删除，不判红）',
          len(r['baseline_stale']) == 1 and not any(r[k] for k in ('anchor', 'rel', 'img', 'ext')),
          '实得 %s' % r['baseline_stale'])

    fenced = {'a.md': '# T\n\n```\n[x](#nope)\n[y](nope.md)\n```\n'}
    r_skip = _an(fenced)
    r_noskip = _an(fenced, skip_fences=False)
    check('teeth⑩ 围栏内不校验是**刻意的开关**：跳过=0 问题、不跳过=2 处问题',
          not r_skip['anchor'] and not r_skip['rel']
          and len(r_noskip['anchor']) == 1 and len(r_noskip['rel']) == 1,
          'skip=%s noskip=%s' % ((len(r_skip['anchor']), len(r_skip['rel'])),
                                 (len(r_noskip['anchor']), len(r_noskip['rel']))))

    # ---- ⑤ 自仓 URL（README 引 docs/ 的真实写法）----
    # 合成 URL 从**运行时解析出的** SELF_REPO 拼，避免把用例与 pyproject 里那串字面量耦死。
    _slug = SELF_REPO or ('shdbl', 'mpltweak')
    _base = 'https://github.com/%s/%s/blob/%s/' % (_slug[0], _slug[1], DEFAULT_BRANCH)

    r = _an({'README.md': '# T\n\n[x](%sdocs/nope.md)\n' % _base})
    check('teeth⑪ ⑤ 自仓 URL 指向不存在的文件必须报出（且不再只是"格式合法"）',
          len(r['selflink']) == 1 and 'nope.md' in r['selflink'][0] and not r['ext'],
          '实得 selflink=%s ext=%s' % (r['selflink'], r['ext']))

    r = _an({'README.md': '# T\n\n[x](%sdocs/ok.md)\n' % _base, 'docs/ok.md': '# OK\n'})
    check('teeth⑫ ⑤ 自仓 URL 目标存在时必须**不报**（防"恒红"）',
          not r['selflink'] and not r['ext'],
          '实得 selflink=%s ext=%s' % (r['selflink'], r['ext']))

    other_branch = 'https://github.com/%s/%s/blob/evidence-0.1.10/bench/nope.jsonl' % _slug
    r = _an({'README.md': '# T\n\n[x](%s)\n' % other_branch})
    check('teeth⑬ ⑤ 误报守卫：**别的分支**的自仓 URL 必须不报（本地没那个分支）',
          not r['selflink'] and not r['ext'],
          '实得 selflink=%s ext=%s' % (r['selflink'], r['ext']))

    foreign = 'https://github.com/someone-else/other-repo/blob/main/docs/nope.md'
    r = _an({'README.md': '# T\n\n[x](%s)\n' % foreign})
    check('teeth⑭ ⑤ 误报守卫：**别人的仓库** URL 必须不报（只查格式，交给 ④）',
          not r['selflink'] and not r['ext'],
          '实得 selflink=%s ext=%s' % (r['selflink'], r['ext']))

    r = _an({'README.md': '# T\n\n[x](%sdocs/b.md#nope)\n' % _base, 'docs/b.md': '# Second\n'})
    check('teeth⑮ ⑤ 自仓 URL 的 `#锚点` 必须按目标文件标题校验（断锚点报出、文件存在不报 rel）',
          len(r['anchor']) == 1 and 'b.md#nope' in r['anchor'][0]
          and not r['selflink'] and not r['rel'],
          '实得 anchor=%s selflink=%s' % (r['anchor'], r['selflink']))

    tree_ok = 'https://github.com/%s/%s/tree/%s/docs' % (_slug[0], _slug[1], DEFAULT_BRANCH)
    r = _an({'README.md': '# T\n\n[x](%s)\n' % tree_ok},
            exists=lambda p: p in ('README.md', 'docs'),
            isdir=lambda p: p == 'docs')
    r_bad_tree = _an({'README.md': '# T\n\n[x](%s)\n' % (tree_ok + '/nope')},
                     exists=lambda p: p in ('README.md', 'docs'),
                     isdir=lambda p: p == 'docs')
    check('teeth⑯ ⑤ `tree/<dir>` 查目录：存在的目录不报、不存在的目录报出',
          not r['selflink'] and len(r_bad_tree['selflink']) == 1,
          'ok=%s bad=%s' % (r['selflink'], r_bad_tree['selflink']))

    # ---- ⑥ 表格列数自洽（转义感知）----
    # 编号刻意用 17..23 而**不**续 ⑰：⑪~⑳ 在 GBK（cp936）里**编不出来**（本机实测），
    # 本文件虽有 errors='replace' 兜底不至于崩，但新加的标签不该再多带一批不可编码字符
    # （t15/C7 记号纪律）。也**不用** `teeth⑥a` 这种写法：那是"第 ⑥ 号 teeth"的子号，
    # 与既有 `teeth⑥`（R1 歧义标题）会读混 —— 这里是**新类别** ⑥。
    _t_esc = ('# T\n\n| A | B | C |\n|---|---|---|\n'
              '| d\\|Δ\\| | x | y |\n'
              '| e\\|G2−G0\\|∞ ≤ 0.002 且 \\|G3−G1\\|∞ ≤ 0.002 | x | y |\n')
    _r_esc = table_issues(_t_esc, 'a.md')
    check('teeth17 ⑥ 假阳性守卫：`\\|Δ\\|` / `\\|G2−G0\\|∞` 里的 `\\|` 是字面竖线，'
          '必须算**同一格内**（转义感知 = 3 格 ⇒ 0 问题）',
          not _r_esc, '实得 %s' % _r_esc)

    _r_naive = table_issues(_t_esc, 'a.md', splitter=naive_row_cells)
    check('teeth18 ⑥ 反向：同一段文本用**朴素 split** 必须误报（证明转义感知是承重的）',
          len(_r_naive) >= 2, '朴素实得 %d 处：%s' % (len(_r_naive), _r_naive[:1]))

    _t_bad = '# T\n\n| A | B | C |\n|---|---|---|\n| 只有两格 | x |\n'
    _r_bad = table_issues(_t_bad, 'a.md')
    check('teeth19 ⑥ 真缺陷必须报出，且点名 文件:行号 + 表头格数 + 本行格数',
          len(_r_bad) == 1 and 'a.md:5' in _r_bad[0] and '= 3 格' in _r_bad[0]
          and '本行 = 2 格' in _r_bad[0], '实得 %s' % _r_bad)

    _t_sep = '# T\n\n| A | B | C |\n|---|---|\n| x | y | z |\n'
    _r_sep = table_issues(_t_sep, 'a.md')
    check('teeth20 ⑥ 分隔行格数不符也要报（带【分隔行】标注）',
          len(_r_sep) == 1 and '【分隔行】' in _r_sep[0], '实得 %s' % _r_sep)

    _t_fence = '# T\n\n```\n| A | B | C |\n|---|---|---|\n| 只有两格 | x |\n```\n'
    check('teeth21 ⑥ 围栏内的"表样"文本不校验（与 ①~⑤ 同一个开关）',
          not table_issues(_t_fence, 'a.md'), '实得 %s' % table_issues(_t_fence, 'a.md'))

    # 奇偶反斜杠：`\|` = 字面竖线（不切）；`\\|` = 字面反斜杠 + 真定界符（**要切**）。
    # 这一条就是"为什么不用 `(?<!\\)\|` 正则"的证据（那个正则会漏掉后者）。
    _t_bs = ('# T\n\n| A | B |\n|---|---|\n'
             '| x\\|y | z |\n'
             '| p\\\\|q | r |\n')
    _r_bs = table_issues(_t_bs, 'a.md')
    check('teeth22 ⑥ 奇数反斜杠 = 字面竖线（不切），偶数反斜杠 = 真定界符（要切）'
          '—— 故不用 `(?<!\\\\)\\|`',
          len(_r_bs) == 1 and 'a.md:6' in _r_bs[0], '实得 %s' % _r_bs)

    check('teeth23 ⑥ table_count 能数出表块（防"一个表都没识别到"的恒绿）',
          table_count(_t_bad) == 1 and table_count('# T\n\n没有表格\n') == 0,
          '有表=%d / 无表=%d' % (table_count(_t_bad), table_count('# T\n\n没有表格\n')))

    # ---- ⑦ 测试表每行必须带 evidence-* 证据链接 ----
    # 编号用 24..28 续 ⑥ 的 17..23（同一条纪律：不引入 ⑪~⑳ 这批 GBK 编不出来的字符）。
    _t_noev = ('# T\n\n| 层 | 说明 |\n|---|---|\n'
               '| ① | **测的是**：能不能原位落进源码；427 / 427 |\n')
    _r_noev = test_table_evidence_issues(_t_noev, 'a.md')
    check('teeth24 ⑦ 测试行没有 `evidence-*` 链接必须报出，且点名 文件:行号',
          len(_r_noev) == 1 and 'a.md:5' in _r_noev[0] and 'evidence-' in _r_noev[0],
          '实得 %s' % _r_noev)

    _t_ev = ('# T\n\n| 层 | 说明 |\n|---|---|\n'
             '| ① | **测的是**：能不能原位落进源码；427 / 427 '
             '[`scan_full.jsonl`](https://github.com/o/r/blob/evidence-0.1.10/bench/v.jsonl) |\n')
    _r_ev = test_table_evidence_issues(_t_ev, 'a.md')
    check('teeth25 ⑦ 误报守卫：带 `evidence-*` 链接的同一行必须**不报**（防"恒红"）',
          not _r_ev, '实得 %s' % _r_ev)

    _t_other = '# T\n\n| 数字 | 来源 |\n|---|---|\n| 427 | corpus/SUMMARY.md |\n'
    check('teeth26 ⑦ 误报守卫：**非测试行**（无 `**测的是**` 标记，如出处表 / 分母表）'
          '即使没有链接也不报',
          not test_table_evidence_issues(_t_other, 'a.md'),
          '实得 %s' % test_table_evidence_issues(_t_other, 'a.md'))

    _t_ev_fence = ('# T\n\n```\n| 层 | 说明 |\n|---|---|\n| ① | **测的是**：x |\n```\n')
    check('teeth27 ⑦ 围栏内的"表样"文本不校验（与 ①~⑥ 同一个开关）',
          not test_table_evidence_issues(_t_ev_fence, 'a.md'),
          '实得 %s' % test_table_evidence_issues(_t_ev_fence, 'a.md'))

    _t_ev_base = ('# T\n\n| 层 | 说明 |\n|---|---|\n'
                  '| ① | **测的是**：纯索引行、没有证据可取 |\n')
    _k = ('a.md', '| ① | **测的是**：纯索引行、没有证据可取 |')
    check('teeth28 ⑦ 白名单通道真的生效：登记进基线的行被豁免（判据本身不放宽）',
          len(test_table_evidence_issues(_t_ev_base, 'a.md')) == 1
          and not test_table_evidence_issues(_t_ev_base, 'a.md', baseline={_k}),
          '无基线 %d 处 / 带基线 %d 处'
          % (len(test_table_evidence_issues(_t_ev_base, 'a.md')),
             len(test_table_evidence_issues(_t_ev_base, 'a.md', baseline={_k}))))

    # ---- 反向验证：把判据弄坏 ⇒ 必须变红（不许"恰好全绿"）----
    _p('---- 反向验证（把判据弄坏就必须变红）----')
    bad_slug = lambda t: t.strip().lower()                                        # noqa: E731
    r_bad = _an(real_files, real_exists, slug_fn=bad_slug)
    r_ok = _an(real_files, real_exists, slug_fn=slug)
    check('反向① 把 slug() 换成"只小写"的坏实现 ⇒ 真仓库断锚点数必须 > 0（当前 %d 处）'
          % len(r_bad['anchor']), len(r_bad['anchor']) > 0 and not r_ok['anchor'],
          '坏实现 %d 处 / 正确实现 %d 处' % (len(r_bad['anchor']), len(r_ok['anchor'])))

    r_nobase = _an(real_files, real_exists, baseline=set())
    check('反向② 把 R2 基线清空 ⇒ 必须多报出全部 %d 条冻结基线（严格多于带基线时的报出数）'
          % len(AMBIG_BASELINE),
          len(r_nobase['ambig_new']) >= len(AMBIG_BASELINE)
          and len(r_nobase['ambig_new']) > len(r_ok['ambig_new']) and bool(AMBIG_BASELINE),
          '清空基线 %d 处 / 恢复基线 %d 处 / 基线条数 %d'
          % (len(r_nobase['ambig_new']), len(r_ok['ambig_new']), len(AMBIG_BASELINE)))

    r_tab_ok = collect_table_issues(real_files)
    r_tab_naive = collect_table_issues(real_files, splitter=naive_row_cells)
    check('反向③ ⑥ 对真仓库：转义感知 = 0 处；换成朴素 split 必须误报 > 0 处'
          '（证明"转义感知"确实在挡假阳性，且这条判据不是恒绿）',
          not r_tab_ok and len(r_tab_naive) > 0,
          '转义感知 %d 处 / 朴素 %d 处' % (len(r_tab_ok), len(r_tab_naive)))

    # 刻意**不**写成 `== len(AMBIG_BASELINE)`：那会把自检与"仓库此刻恰好没有基线外的歧义标题"
    # 耦死 —— 一旦有人新增一个（正是棘轮要拦的），自检会跟着红、掩盖真信号。用
    # `>= 基线数` + `严格多于带基线时` 既证明"基线确实在豁免"，又不与仓库状态耦合。

    # 反向④：⑦ 不是恒绿 —— 把判据换成"整张表每个数据行都要链接"（即无视测试标记）⇒
    # 真仓库里的出处表 / 分母表会立刻被误报。证明"只认测试行标记"这条收窄是承重的。
    _r_real_ok = collect_test_evidence_issues(real_files)
    _wide_hits = 0
    for _rel in sorted(real_files):
        _ls = _lines(real_files[_rel])
        for _i in range(len(_ls) - 1):
            if _is_table_row(_ls[_i][1]) and _is_table_sep(_ls[_i + 1][1]):
                _j = _i + 2
                while _j < len(_ls) and _is_table_row(_ls[_j][1]):
                    if not TEST_EVIDENCE_RE.search(_ls[_j][1]):
                        _wide_hits += 1
                    _j += 1
    check('反向④ ⑦ 对真仓库：按标记认测试行 = 0 处；放宽成"所有数据行都要链接"必须'
          '误报 > 0 处（证明"只认测试行"确实在挡假阳性、且这条判据不是恒绿）',
          not _r_real_ok and _wide_hits > 0,
          '按标记 %d 处 / 一刀切 %d 处' % (len(_r_real_ok), _wide_hits))


# ---------------------------------------------------------------- ⑦ 引用闸门（0.1.13）
# 被测对象：`tools/check_doc_refs.py`（发布期人工过目的引用闸门，**本身不进 CI**）。
# 这里只搬"判据级"的牙齿：t103 四方向（直接跑工具自带 `--self-test`，不重复造夹具）+ 三条
# 新判据的合成夹具 teeth + 每条判据一条反向验证。**不**断言"真仓库 0 可疑"（理由见模块 docstring ⑦）。
_GATE_PATH = os.path.join(ROOT, 'tools', 'check_doc_refs.py')
_GATE_INCLUDES = ('docs/**/*.md', 'README*.md', 'skills/**/*.md')

# 夹具（**刻意与工具自测里的 `_WB_FIXTURE` 分开写**：teeth 用独立夹具，
# 免得工具里那个常量被改坏后"自测和牙齿一起瞎"）。
# 行号对照：1~9 = row*；**10 = `def prune_backups`**；11~20 = mid*；**21 = `def gate_symbol`**；22~25 = end*。
_GT_WB = (''.join('row%d\n' % i for i in range(1, 10))
          + 'def prune_backups(fig):\n'
          + ''.join('mid%d\n' % i for i in range(1, 11))
          + 'def gate_symbol(x):\n'
          + ''.join('end%d\n' % i for i in range(1, 5)))

# A2 夹具（§28-A2 纯注释行不承重）：**行号是断言的一部分**，别随手插行。
# 1 = 横幅注释（含 `check_sym`）；2 = 纯注释；3 空；4 = `def real_thing`；
# 5~8 = docstring（**6 = 以 `#` 开头的正文行**，7 = 正文，含 `gate_inner`）；
# 9 = **行尾注释**所在的代码行（含 `tail_only`）；10 空；11 = `def check_sym`；12 = return。
_GT_A2 = ('# ---- check_sym: 横幅注释，锚在这里等于引用一句注释 ----\n'
          '# 第二行纯注释\n'
          '\n'
          'def real_thing(x):\n'
          '    """说明。\n'
          '    # doc_inner 在 docstring 里、以 # 开头 —— 这**不是**注释\n'
          '    gate_inner 出现在 docstring 正文里\n'
          '    """\n'
          '    return x  # tail_only 在行尾注释里\n'
          '\n'
          'def check_sym(y):\n'
          '    return y\n')


def _load_gate():
    """按路径加载 `tools/check_doc_refs.py`（不污染 sys.path、不与别的模块重名）。"""
    import importlib.util
    spec = importlib.util.spec_from_file_location('_mpltweak_check_doc_refs', _GATE_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _TmpTree(object):
    """系统临时目录里的一棵小树（**一个字节都不往仓库里写**；用完删干净）。"""

    def __init__(self):
        import tempfile
        self.root = tempfile.mkdtemp(prefix='mpltweak_docrefs_')

    def write(self, rel, text):
        path = os.path.join(self.root, rel.replace('/', os.sep))
        d = os.path.dirname(path)
        if d and not os.path.isdir(d):
            os.makedirs(d)
        with io.open(path, 'w', encoding='utf-8', newline='\n') as f:
            f.write(text)
        return path

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        import shutil
        shutil.rmtree(self.root, ignore_errors=True)
        return False


def _gate_refs(res):
    return [r['ref'] for r in res['items']]


def _pin_child_env(base=None, **overrides):
    """起子进程前的**最后一道闸**：硬钉 ``MPLBACKEND=Agg``（覆盖宿主投毒）。

    与 `tests/test_hygiene.py` / `tests/test_sandbox.py` 的同名闸门是**同一个口径的不同副本**：
    宿主把 `MPLBACKEND` 设成 `Qt5Agg` / `TkAgg` 时，`setdefault` 会原样保留 ⇒ 子进程落到交互
    后端 ⇒ 脚本里任何建图/`plt.show()` 都真开窗、抢焦点与鼠标（2026-10-12 用户当天报过）。
    `tests/test_hygiene.py::test_every_child_process_call_site_is_pinned_or_ledgered`
    按 AST 逐个子进程调用点检查"有没有经过闸门" —— 本文件只有下面一处（跑闸门自测），
    必须走这里，否则那条棘轮会红（t9 实测抓到过一次）。

    **只钉后端**，不替调用方决定输出编码（自测那边要 `PYTHONIOENCODING=utf-8` 才能稳定
    解码中文输出，由调用方显式给）。
    """
    out = dict(os.environ if base is None else base)
    out['MPLBACKEND'] = 'Agg'
    out.update(overrides)
    return out


def refs_gate():
    _p('---- ⑦ 引用闸门：裸 :NNN / 符号锚 ±N / 带理由豁免（判据级 teeth + 反向验证）----')
    if not os.path.isfile(_GATE_PATH):
        check('⑦ 引用闸门工具在场（tools/check_doc_refs.py）', False, _GATE_PATH)
        return
    g = _load_gate()
    check('⑦ 引用闸门工具可被测试加载', callable(g.scan) and callable(g._self_test),
          os.path.basename(_GATE_PATH))

    with _TmpTree() as t:
        # ---- teeth29/30/31：裸引用（§25.4a）----
        t.write('src/mpltweak/writeback.py', _GT_WB)
        t.write('docs/bare.md', '版本 `writeback.py:10`，另见 `:12` 与 `:20`。\n')
        r = g.scan(t.root, ['docs/bare.md'], require_anchor=False)
        check('teeth29 裸引用 `:NNN` 继承**同一行**上一处 `.py` 名字（`:12` / `:20` → writeback.py:12 / :20）',
              _gate_refs(r) == ['writeback.py:10', 'writeback.py:12', 'writeback.py:20']
              and [x['inherited'] for x in r['items']] == [False, True, True],
              '实得 %s' % _gate_refs(r))

        t.write('docs/trap.md', '时间 12:30；端口 127.0.0.1:7890；链接 https://x 与 a//b:1。\n')
        r2 = g.scan(t.root, ['docs/trap.md'], require_anchor=False)
        check('teeth30 裸引用误认守卫：`12:30` / `127.0.0.1:7890` / `https://` 不许被当成引用',
              not r2['items'] and not r2['skipped_bare'],
              '实得 items=%d skipped=%d' % (len(r2['items']), len(r2['skipped_bare'])))

        t.write('docs/para.md',
                '第一段 `writeback.py:10` 到此为止。\n\n第二段裸引用 `:12` 没有文件名。\n')
        r3 = g.scan(t.root, ['docs/para.md'], require_anchor=False)
        check('teeth31 裸引用**不跨行**继承（空行之后 / 只有换行都不算 —— 一律记 skipped_bare，'
              '只提示不算可疑）',
              len(r3['items']) == 1 and len(r3['skipped_bare']) == 1 and not r3['suspicious'],
              'items=%d skipped=%d susp=%d'
              % (len(r3['items']), len(r3['skipped_bare']), len(r3['suspicious'])))

        # ---- teeth32/33：符号锚与窗口（§25.4b；窗口默认值 0 见 §28-A1）----
        t.write('docs/sym.md',
                '对：`writeback.py:10 # prune_backups`；错：`writeback.py:10 # gate_symbol`。\n')
        r4 = g.scan(t.root, ['docs/sym.md'])
        _s4 = r4['suspicious']
        check('teeth32 符号锚：写对 ⇒ 过；写错 ⇒ `symbol_mismatch` 且报出**最近出现的行号**（第 21 行）',
              len(r4['items']) == 2 and len(_s4) == 1
              and _s4[0]['why'] == 'symbol_mismatch' and _s4[0]['anchor_at'] == 21,
              '实得 %s' % [(x['ref'], x['why'], x.get('anchor_at')) for x in _s4])

        # 窗口默认值 0（§28-A1，2026-10-24）：**偏 0 行**必过、**偏 3 行**必红；
        # 显式给 `anchor_window=3` 才放行 ⇒ 证明"承重"的是窗口这个**参数**，而不是"恰好恒红"。
        t.write('docs/win.md', '同偏移：`writeback.py:10 # prune_backups`；偏 3 行：`writeback.py:13 # prune_backups`。\n')
        w0 = g.scan(t.root, ['docs/win.md'], anchor_window=0)
        w3 = g.scan(t.root, ['docs/win.md'], anchor_window=3)
        _s0 = [(x['ref'], x['why']) for x in w0['suspicious']]
        check('teeth33 符号锚窗口是**承重的**：偏 3 行在默认窗口（0 = 只认被引那一行）下必红，'
              '显式放宽到 ±3 才通过；且默认值就是 0',
              _s0 == [('writeback.py:13', 'symbol_mismatch')]
              and not w3['suspicious'] and g.ANCHOR_WINDOW == 0,
              '±0=%s / ±3=%d 处 / 默认窗口=%d'
              % (_s0, len(w3['suspicious']), g.ANCHOR_WINDOW))

        # ---- teeth48/49：纯注释行不承重，docstring **不是**注释（§28-A2，2026-10-24）----
        # 为什么要有这条：一行横幅注释（`# ---- foo: ...`）里满是符号名，锚指向它等于"引用了
        # 一句注释"—— 代码挪走后注释一动不动，闸门绿着而引用已失效。docstring 则相反：它是
        # 产品文档的一部分，`docs/verify.md:75` 的 `writeback.py:3108 # first` 就落在 docstring 里。
        t.write('src/mpltweak/anchors.py', _GT_A2)
        t.write('docs/anchors.md',
                '横幅注释：`anchors.py:1 # check_sym`；'
                '行尾注释那行（是代码）：`anchors.py:9 # tail_only`；'
                'docstring 里以 `#` 开头的行：`anchors.py:6 # doc_inner`；'
                'docstring 正文：`anchors.py:7 # gate_inner`。\n')
        r5 = g.scan(t.root, ['docs/anchors.md'])
        _s5 = [(x['ref'], x['why'], x['anchor_at']) for x in r5['suspicious']]
        _ok5 = [x['ref'] for x in r5['items'] if not x['why']]
        check('teeth48 纯注释行**不承重**（匹配与"最近出现"两个环节都跳过）：横幅注释当锚 ⇒ '
              '`symbol_mismatch`，且"最近出现"跳过纯注释行、报第 11 行的 `def check_sym`；'
              '而**行尾注释**所在的代码行（第 9 行）照样承重',
              _s5 == [('anchors.py:1', 'symbol_mismatch', 11)]
              and _ok5[0] == 'anchors.py:9',
              'susp=%s / 通过=%s' % (_s5, _ok5))
        # teeth49 只钉 docstring 那两条（不与 teeth48 的横幅断言耦合）：这样"关掉排除"只会让
        # teeth48 红、"按 # 前缀一刀切"只会让 teeth49 红 —— 两条牙齿各自咬各自的错法。
        _susp5 = set(x['ref'] for x in r5['suspicious'])
        check('teeth49 docstring **不是**注释、不参与排除：docstring 里以 `#` 开头的正文行（第 6 行）'
              '与普通正文行（第 7 行）当锚都必须命中（按 `#` 前缀一刀切就会把第 6 行误排）',
              set(['anchors.py:6', 'anchors.py:7']) <= set(_ok5)
              and not (_susp5 & set(['anchors.py:6', 'anchors.py:7'])),
              '通过=%s / 可疑=%s' % (_ok5, _s5))

        # ---- teeth50：空转键（§28-A3）—— 表里挂着但已经用不上的条目必须被报出来 ----
        # 为什么要：`HISTORICAL_ALLOW`（历史留痕）是"当时对、现在不对"的账。问题修好了账还挂着，
        # 以后同类新问题就会被旧账吞掉（同一处引用又被放行）。所以两类空转都要报：
        #   missing = 引用在仓库里已经不存在；idle = 引用还在、但这条豁免用不上。
        # 只提示，**不改 rc**（否则"账没销"会挡住发布，判据会被人骂着放宽）。
        import contextlib
        import json
        _saved_hist, _saved_unanch = g.HISTORICAL_ALLOW, g.UNANCHORED_ALLOW
        try:
            g.HISTORICAL_ALLOW = {
                ('docs/idle.md', 'writeback.py:10'): '自测：引用还在，但这条豁免用不上（idle）',
                ('docs/idle.md', 'writeback.py:99'): '自测：引用已不存在（missing）'}
            g.UNANCHORED_ALLOW = {}
            t.write('docs/idle.md', '带锚且对：`writeback.py:10 # prune_backups`\n')
            _out_js = io.StringIO()
            with contextlib.redirect_stdout(_out_js):
                _rc_js = g.main(['--root', t.root, '--include', 'docs/idle.md', '--json'])
            _js = json.loads(_out_js.getvalue())
            _keys50 = [(k['table'], k['ref'], k['reason']) for k in _js['stale_keys']]
            _out_txt = io.StringIO()
            with contextlib.redirect_stdout(_out_txt):
                _rc_txt = g.main(['--root', t.root, '--include', 'docs/idle.md'])
            _txt50 = _out_txt.getvalue()
            g.HISTORICAL_ALLOW = {}
            _out_none = io.StringIO()
            with contextlib.redirect_stdout(_out_none):
                g.main(['--root', t.root, '--include', 'docs/idle.md'])
            _txt_none = _out_none.getvalue()
        finally:
            g.HISTORICAL_ALLOW, g.UNANCHORED_ALLOW = _saved_hist, _saved_unanch
        check('teeth50 空转键：表里挂着但用不上的条目必须被报出来（missing = 引用已不存在 / '
              'idle = 引用还在但这条豁免用不上），且**只提示、不改 rc**',
              _keys50 == [('HISTORICAL_ALLOW', 'writeback.py:10', 'idle'),
                          ('HISTORICAL_ALLOW', 'writeback.py:99', 'missing')]
              and _js['stale_unanchored'] == [] and _rc_js == 0 and _rc_txt == 0
              and '空转键' in _txt50 and 'writeback.py:99' in _txt50
              and '空转键：无' in _txt_none,
              'keys=%s / rc=%s,%s / 有提示=%s / 无键提示=%s'
              % (_keys50, _rc_js, _rc_txt, '空转键' in _txt50,
                 '空转键：无' in _txt_none))

        # ---- teeth51：表头计数不许手写（§28-A4）----
        # 病根：`HISTORICAL_ALLOW` 的表头注释写死过"9 键 / 11 条"，实测 12 条（三处键各被两行
        # 引用），从 t72 起就少算 1 条 —— 而**没有任何东西会因此变红**。两条一起钉：
        #   (a) 源码里三张表的"表头注释块"不许出现 `N 键` / `N 条` / `N 处` 这种手写计数；
        #   (b) `--json` 的 counts 必须与**真表长 / 真扫到 / 真生效**逐项相等（数字只能算出来）。
        import io as _io
        with _io.open(_GATE_PATH, encoding='utf-8') as _f:
            _src = _f.read().split('\n')
        _bad_hdr = []
        for _tname in ('HISTORICAL_ALLOW', 'KNOWN_DRIFT', 'UNANCHORED_ALLOW'):
            _idx = None
            for _i, _ln in enumerate(_src):
                if _ln.startswith(_tname + ' = {'):
                    _idx = _i
                    break
            if _idx is None:
                _bad_hdr.append('%s：找不到定义' % _tname)
                continue
            _hdr, _j = [], _idx - 1
            while _j >= 0 and _src[_j].lstrip().startswith('#'):
                _hdr.insert(0, _src[_j])
                _j -= 1
            for _ln in _hdr:
                if re.search(r'\d+\s*(键|条|处|个键|个条目)', _ln):
                    _bad_hdr.append('%s: 表头注释里有手写计数 → %s'
                                    % (_tname, _ln.strip()[:60]))
        _real51 = g.scan(ROOT, list(_GATE_INCLUDES))
        _c51 = _real51['counts']
        _cnt_bad = []
        for _tname, _kind in (('HISTORICAL_ALLOW', 'allowed'), ('KNOWN_DRIFT', 'drift'),
                              ('UNANCHORED_ALLOW', 'unanchored')):
            _tbl = getattr(g, _tname)
            _one = _c51['tables'][_tname]
            _ex = len([r for r in _real51['items'] if r['exempt'] == _kind])
            if _one['keys'] != len(_tbl):
                _cnt_bad.append('%s.keys=%d≠表长%d' % (_tname, _one['keys'], len(_tbl)))
            if _one['exercised'] != _ex:
                _cnt_bad.append('%s.exercised=%d≠实测%d' % (_tname, _one['exercised'], _ex))
            if _one['in_scan'] > _one['keys']:
                _cnt_bad.append('%s.in_scan=%d 大于表长' % (_tname, _one['in_scan']))
        check('teeth51 表头计数不许手写（§28-A4）：三张表的表头注释里没有 `N 键 / N 条 / N 处`，'
              '且 `counts` 的表内 / 本面扫到 / 本面真生效三项与真表长、真豁免数逐项相等',
              not _bad_hdr and not _cnt_bad,
              '手写计数：%s / 计数不符：%s / 实测 历史留痕%s 已知漂移%s 未锚%s'
              % (_bad_hdr or '无', _cnt_bad or '无',
                 _c51['tables']['HISTORICAL_ALLOW'], _c51['tables']['KNOWN_DRIFT'],
                 _c51['tables']['UNANCHORED_ALLOW']))

        # ---- teeth34/35/36：带理由豁免口径（§25.4c）----
        t.write('docs/un.md', '引用 `writeback.py:10`。\n')
        _k34 = ('docs/un.md', 'writeback.py:10')
        raw = g.scan(t.root, ['docs/un.md'])
        ok_un = g.scan(t.root, ['docs/un.md'], unanch={_k34: '理由：存量'})
        ok_dr = g.scan(t.root, ['docs/un.md'], drift={_k34: '理由：待修'})
        check('teeth34 未锚必须红（`anchor_missing`）；登记进豁免表后才放行 —— 判据本身没被放宽',
              [x['why'] for x in raw['suspicious']] == ['anchor_missing']
              and not ok_un['suspicious'] and not ok_dr['suspicious'],
              '未登记 %d 处 / 登记未锚 %d 处 / 登记待修 %d 处'
              % (len(raw['suspicious']), len(ok_un['suspicious']), len(ok_dr['suspicious'])))

        prio = g.scan(t.root, ['docs/un.md'], drift={_k34: '理由：待修'},
                      unanch={_k34: '理由：存量'})
        check('teeth35 豁免表有优先级、不吃错表：同键同时在 drift / unanchored ⇒ 记 `drift`（待修优先）',
              [x['exempt'] for x in prio['items']] == ['drift'],
              '实得 %s' % [x['exempt'] for x in prio['items']])

        stale = g.scan(t.root, ['docs/un.md'],
                       unanch={_k34: '理由', ('docs/un.md', 'writeback.py:99'): '理由'})
        check('teeth36 失效条目必须提示删（登记了但引用不存在 ⇒ 进 stale，且不影响"干净"判定）',
              stale['stale'] == [('docs/un.md', 'writeback.py:99')] and not stale['suspicious'],
              'stale=%s / susp=%d' % (stale['stale'], len(stale['suspicious'])))

        # ---- teeth41：整份文档级的"不要求锚"（日志型手册）----
        t.write('MAINTENANCE.md', '历史现场：`writeback.py:10`（当时行号）。\n')
        t.write('docs/live.md', '现行说明：`writeback.py:10`。\n')
        r41 = g.scan(t.root, ['MAINTENANCE.md', 'docs/live.md'])
        _w41 = [(x['doc'], x['ref'], x['why']) for x in r41['items']]
        check('teeth41 整份文档级豁免：日志型手册（MAINTENANCE.md）的引用不要求符号锚，'
              '但**别的**文档照样要求（同一处引用一个不红、一个必红）',
              _w41 == [('MAINTENANCE.md', 'writeback.py:10', ''),
                       ('docs/live.md', 'writeback.py:10', 'anchor_missing')]
              and g.ANCHOR_FREE_DOCS.get('MAINTENANCE.md'),
              '实得 %s' % _w41)

        # ---- teeth42/43：t14 漏洞 1 —— 裸 `:NNN` 的继承**只认同一行** ----
        # teeth42 是现场复刻（MAINTENANCE.md:830-832 的形状）：上一行写 `launch.py:760`，
        # 这一行的 `:12`/`:43`/`:44` 其实指**文档自己**的行号 ⇒ 不许接成 launch.py:
        # （旧实现会接上去，然后 `launch.py:44` 落在空行/错行上误报）。
        t.write('src/mpltweak/launch.py', ''.join('l%d\n' % i for i in range(1, 801)))
        t.write('docs/selfref.md',
                '7. `docs/exec-boundaries.md:44` 那条不成立：\n'
                '   开窗是**同进程**（`launch.py:760`），`sys.exit` 被 `:761-762` 接住，但\n'
                '   同文件 `:12`/`:43` 已注明开窗是本进程 ⇒ `:44` 与之冲突。\n')
        r42 = g.scan(t.root, ['docs/selfref.md'], require_anchor=False)
        _refs42 = [x['ref'] for x in r42['items']]
        check('teeth42 漏洞 1（t14 现场复刻）：跨行的 `:12`/`:43`/`:44` 不许继承上一行的 '
              '`launch.py:760` ⇒ 记 skipped_bare，且**不得误报**',
              _refs42 == ['launch.py:760', 'launch.py:761-762']
              and len(r42['skipped_bare']) == 3 and not r42['suspicious'],
              'items=%s skipped=%d susp=%s'
              % (_refs42, len(r42['skipped_bare']),
                 [(x['ref'], x['why']) for x in r42['suspicious']]))

        # teeth43：该继承的必须继承 —— 同一行里**只提名字、不带行号**也要接得上
        # （`tests/test_fuzz_events.py` 的（`:1154`）这种写法；旧实现只认完整引用 ⇒ 接不上，
        #  还会接到上一行那个文件名上，正是 MAINTENANCE.md:1048 误报 journey_wheel 的机制）。
        t.write('src/mpltweak/gate.py', ''.join('g%d\n' % i for i in range(1, 31)))
        t.write('docs/bname.md',
                '见 `tests/test_fuzz_events.py` 的 §t82③（`:1154` 与 `:1160`）。\n')
        t.write('tests/test_fuzz_events.py', ''.join('t%d\n' % i for i in range(1, 1201)))
        r43 = g.scan(t.root, ['docs/bname.md'], require_anchor=False)
        check('teeth43 只提名字（不带行号）也要能继承：同一行的 '
              '`tests/test_fuzz_events.py` + `:1154` ⇒ test_fuzz_events.py:1154',
              [x['ref'] for x in r43['items']] == ['tests/test_fuzz_events.py:1154',
                                                   'tests/test_fuzz_events.py:1160'],
              '实得 %s' % [x['ref'] for x in r43['items']])

        # ---- teeth44/45：t14 漏洞 2 —— 冻结表**只**豁免"未锚" ----
        # 夹具：blankw.py 第 5 行是空行、第 11 行非空、总共 10 行（`:99` 越界）。
        t.write('src/mpltweak/blankw.py',
                ''.join(('w%d\n' % i) if i != 5 else '\n' for i in range(1, 11)))
        t.write('docs/frozen.md', '空行：`blankw.py:5`；非空：`blankw.py:7`；越界：`blankw.py:99`。\n')
        r44 = g.scan(t.root, ['docs/frozen.md'], require_anchor=False,
                     unanch={('docs/frozen.md', 'blankw.py:5'): '自测：登记为未锚'})
        _w44 = [(x['ref'], x['why'], x['exempt']) for x in r44['items']]
        check('teeth44 漏洞 2（t14）：冻结条目指到**空行**必须照样红并点名它；'
              '越界同理（豁免只豁免 anchor_missing 这一项）',
              ('blankw.py:5', 'blank_line', '') in _w44
              and ('blankw.py:99', 'line_out_of_range', '') in _w44
              and len(r44['suspicious']) == 2,
              '实得 %s' % _w44)

        _r45 = g.scan(t.root, ['docs/frozen.md'],
                      unanch={('docs/frozen.md', 'blankw.py:5'): '自测：登记为未锚',
                              ('docs/frozen.md', 'blankw.py:7'): '自测：登记为未锚',
                              ('docs/frozen.md', 'blankw.py:99'): '自测：登记为未锚'})
        check('teeth45 漏洞 2 的反面：同一批条目里只"未锚"的那条（`:7` 非空行）确实被豁免成绿，'
              '而空行 / 越界两条即使登记了也仍然红 —— 豁免范围是**按 why 收窄**的，不是整条放宽',
              [x['ref'] for x in _r45['suspicious']] == ['blankw.py:5', 'blankw.py:99']
              and [(x['ref'], x['exempt']) for x in _r45['items']
                   if x['ref'] == 'blankw.py:7'] == [('blankw.py:7', 'unanchored')],
              'susp=%s items=%s' % ([x['ref'] for x in _r45['suspicious']],
                                    [(x['ref'], x['exempt']) for x in _r45['items']]))

    # ---- teeth46：符号锚的"窗口外"提示要报**离被引行最近**的那一处（t16）----
    # 0.1.14 §28-A1 之后"窗口 3"不再是被放宽的默认值，所以这里用**刚够到**的最小窗口（±2）
    # 演示"放宽就能命中"，别再把 ±3 当成口径。
    _l46 = ['line%d' % i for i in range(1, 21)]
    _l46[4] = 'def alpha():'          # 第 5 行
    _l46[19] = 'alpha()'              # 第 20 行
    _v46 = g._symbol_where(_l46, 'alpha', 18, 0)
    check('teeth46 窗口外提示报"**离被引行最近**"那一处（不是文件里第一处）：符号在 5 / 20、'
          '引用行 18、窗口 0 ⇒ 必须报 20（旧实现报 5，会让人以为差 13 行）',
          _v46 == ('out_of_window', 20)
          and g._symbol_where(_l46, 'alpha', 18, 2) == ('', 20),
          '窗口 0 得 %s / 窗口 2 得 %s' % (_v46, g._symbol_where(_l46, 'alpha', 18, 2)))

    # ---- teeth47：窗口**默认值**必须是 0（§28-A1，2026-10-24；防手滑改回 3）----
    # 只钉模块常量不够：闸门真正运行时用的是 argparse 的默认值，两个出处都要钉。
    check('teeth47 符号锚窗口的**默认值**是 0（只认被引那一行；常量与命令行默认值两处一致）',
          g.ANCHOR_WINDOW == 0
          and g._build_parser().get_default('anchor_window') == 0
          and g._build_parser().parse_args([]).anchor_window == 0,
          'ANCHOR_WINDOW=%d / CLI 默认=%s'
          % (g.ANCHOR_WINDOW, g._build_parser().get_default('anchor_window')))

    # ---- teeth37/38：三张真表的**静态纪律**（与环境状态无关，永不因代码改动漂移）----
    def _table_bad(table):
        bad = []
        for k, why in table.items():
            ok_key = (isinstance(k, tuple) and len(k) == 2
                      and all(isinstance(x, str) for x in k)
                      and re.search(r'\.py:\d+', k[1]) and not k[0].startswith(('/', '\\')))
            if not ok_key or not str(why).strip():
                bad.append(k)
        return bad

    _tables = (('HISTORICAL_ALLOW', g.HISTORICAL_ALLOW),
               ('KNOWN_DRIFT', g.KNOWN_DRIFT),
               ('UNANCHORED_ALLOW', g.UNANCHORED_ALLOW))
    _bad = {n: _table_bad(tb) for n, tb in _tables}
    _bad = dict((n, v) for n, v in _bad.items() if v)
    check('teeth37 三张豁免表都"逐条带理由"，键形如 (文档相对路径, `文件.py:行号`)'
          '（键**不含文档行号** —— 与 TEST_EVIDENCE_BASELINE / AMBIG_BASELINE 同纪律）',
          not _bad, '不合格条目：%s' % (_bad or '无'))

    _seen, _dup = {}, []
    for _n, _tb in _tables:
        for _k in _tb:
            if _k in _seen:
                _dup.append('%s 同时在 %s 与 %s' % (_k, _seen[_k], _n))
            _seen[_k] = _n
    check('teeth38 三张豁免表不许重叠（同一个键只许出现在一张表里，否则"为什么放行"说不清）',
          not _dup, '重叠：%s' % (_dup or '无'))

    # ---- teeth39：§25.5 工具自带自测（t103 四方向 + 三项新判据 + t14 两条收紧）必须全过 ----
    import subprocess
    _proc = subprocess.run([sys.executable, _GATE_PATH, '--self-test'],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=300,
                           env=_pin_child_env(PYTHONIOENCODING='utf-8'))
    _out = (_proc.stdout or b'').decode('utf-8', 'replace')
    _tail = [l for l in _out.splitlines() if l.strip()][-1:] or ['']
    check('teeth39 工具自带自测 `--self-test` 全过（§25.5：t103 四方向 + 符号锚/窗口/豁免 '
          '+ t14 的两条收紧 + §28 的默认窗口 0 / 注释行不承重 / 空转键 / 计数由工具算）'
          '—— CI 级，不靠人工 --root',
          _proc.returncode == 0 and '13 / 13' in _out,
          'rc=%d | %s' % (_proc.returncode, _tail[0]))

    # ---- teeth40：反向 —— 把每条判据**故意弄坏**，自测必须变红（证明自测不是恒绿）----
    _not_red = []
    for _mode in ('bare', 'anchor', 'window', 'allow', 'herit', 'exempt', 'resolve',
                  'comment'):
        _buf = io.StringIO()
        if g._self_test(_mode, out=_buf) != 1:
            _not_red.append(_mode)
    check('teeth40 反向验证：`--break bare/anchor/window/allow/herit/exempt/resolve/comment` '
          '逐条弄坏判据 ⇒ 自测**必须**变红（rc=1）', not _not_red,
          '没变红的：%s' % (_not_red or '无'))

    # ---- 反向⑤/⑥：对**真仓库**证明这两条新判据确实在干活（不是恒绿）----
    real = g.scan(ROOT, list(_GATE_INCLUDES))
    real_nofreeze = g.scan(ROOT, list(_GATE_INCLUDES), unanch={}, drift={})
    real_nobare = g.scan(ROOT, list(_GATE_INCLUDES), allow_bare=False)
    check('反向⑤ 对真仓库：把未锚基线 + 待修表清空 ⇒ 可疑引用必须变多'
          '（%d → %d 处；证明这两张表确实在挡，而不是判据恒绿）'
          % (len(real['suspicious']), len(real_nofreeze['suspicious'])),
          len(real_nofreeze['suspicious']) > len(real['suspicious']),
          '带表 %d 处 / 清空 %d 处 / 冻结 %d 条'
          % (len(real['suspicious']), len(real_nofreeze['suspicious']), len(g.UNANCHORED_ALLOW)))
    # ---- 反向⑥：§25.4a 的裸引用继承是承重的（**自造文档**，不依赖真仓库里还剩几处裸引用）----
    # 为什么改成自造：B1（0.1.14）已把 docs 里的续行裸引用全部改成显式 `文件名:NNN`
    # ⇒ "对真仓库关掉裸引用支持"退化成恒等（48 → 48），判据变恒绿。
    with _TmpTree() as _t6:
        _t6.write('src/mpltweak/demo.py',
                  'def alpha():\n    pass\n\n\ndef beta():\n    pass\n')
        _t6.write('docs/bare.md', '`demo.py:1 # alpha` 与 `:5 # beta`。\n')
        _on = g.scan(_t6.root, ['docs/**/*.md'], allow_bare=True)
        _off = g.scan(_t6.root, ['docs/**/*.md'], allow_bare=False)
        check('反向⑥ 裸引用继承是承重的（自造文档：同一行 `demo.py:1` 之后的裸 `:5` 必须继承，'
              '且必须正好 2 条 → 关掉后 1 条）',
              len(_on['items']) == 2 and not _on['suspicious']
              and len(_off['items']) == 1,
              '带裸引用 items=%d 可疑=%d / 关掉 items=%d 可疑=%d'
              % (len(_on['items']), len(_on['suspicious']),
                 len(_off['items']), len(_off['suspicious'])))


def main():
    _p('文档锚点 / 相对链接回归网（0.1.10）')
    _p('范围：仓库全部 *.md（跳过 .git / 缓存 / 临时目录）；纯文本、零网络、围栏内不校验')
    _p('')
    slug_selfproof()
    files, exists = run_real()
    teeth(files, exists)
    refs_gate()          # ⑦ 引用闸门（0.1.13）：判据级 teeth + 反向验证
    _p('')
    if _fails:
        _p('FAILED: %d 项 -> %s' % (len(_fails), _fails))
        return 1
    _p('ALL PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
