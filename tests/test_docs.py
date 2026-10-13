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

本套件检查四类（纯文本、**零网络**、秒级；**代码围栏内的示例文本不参与校验**，那里是样例）
================================================================================
① **文件内锚点**：`](#slug)` 必须命中**同文件**某个标题的 slug；
② **相对链接**：`docs/x.md` / `../README.md` / `x.md#slug` 的**目标文件必须存在**；带 `#slug`
   的还要在**目标文件**里存在该 slug（跨文档锚点也管）；
③ **相对图片**：`![](相对路径)` 必须存在；`http(s)://` 一律跳过，**不发任何网络请求**；
④ **外部链接**：`http(s)://` 只做**格式**检查（scheme + host 形如域名），同样不发请求。

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
"R1 与 R2 同时命中时 R1 先报"。再加两条**反向验证**（把判据弄坏就必须变红）：
把 `slug()` 换成坏实现 ⇒ 真仓库的断锚点数 > 0；把 `AMBIG_BASELINE` 清空 ⇒ R2 必须报出全部 34 处。

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


# ---------------------------------------------------------------- 核心分析器
def analyze(files, exists, slug_fn=slug, skip_fences=True, baseline=None):
    """纯函数核心（teeth 直接在合成内容上调用它）。

    files   : {relpath: markdown 文本}（只含 .md）
    exists  : callable(relpath) -> bool（相对仓库根的路径是否存在；图片/其它文件都走它）
    baseline: R2 冻结基线 {(relpath, 标题文本)}；None → 用模块级 AMBIG_BASELINE
    返回 dict(kind -> [issue 描述])，kind ∈ anchor / rel / img / ext /
                                        ambig_linked(R1) / ambig_new(R2) / baseline_stale
    """
    base = AMBIG_BASELINE if baseline is None else baseline
    res = {k: [] for k in ('anchor', 'rel', 'img', 'ext', 'ambig_linked', 'ambig_new',
                           'baseline_stale')}
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
                    if not HOST_OK_RE.match(tgt) or ' ' in tgt:
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
    ('CHANGELOG.md', '修复 · 第三波（T15：退块判据改逐字段三态）'),
    ('CHANGELOG.md', '修复 · 第二波（L0 复测暴露）'),
    ('CHANGELOG.md', '修复 · 第五波（T35 / T36）'),
    ('CHANGELOG.md', '修复 · 第四波（T23：多图 stale + 验证链诚实 + 块锚点硬约束 + 刻度字号分列 + 新开关 --keep-unverified）'),
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
    res = analyze(files, exists)
    n_heads = sum(len(headings(t)) for t in files.values())
    _p('---- 真仓库：%d 个 *.md / %d 个标题 ----' % (len(files), n_heads))
    _p('（② 的反验即"下列锚点必须 0 处解析失败"；失败清单会逐条打印）')
    checks = [
        ('anchor', '① 文件内锚点：全部命中同文件标题'),
        ('rel', '② 相对链接：目标文件全部存在'),
        ('img', '③ 相对图片：全部存在（http(s) 一律跳过、零网络）'),
        ('ext', '④ 外部链接：格式全部合法（不查可达性）'),
        ('ambig_linked', 'R1 被链接引用的标题：无歧义字符（零容忍）'),
        ('ambig_new', 'R2 新增歧义标题：无（基线之外的都算新增）'),
    ]
    for kind, label in checks:
        items = res[kind]
        check('%s —— 实得 %d 处' % (label, len(items)), not items,
              (items[0][:120] if items else ''))
        _print_issues(items)
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
def _an(files, exists=None, slug_fn=slug, skip_fences=True, baseline=None):
    return analyze(files, exists or (lambda p: p in files), slug_fn=slug_fn,
                   skip_fences=skip_fences, baseline=baseline)


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
    # 刻意**不**写成 `== len(AMBIG_BASELINE)`：那会把自检与"仓库此刻恰好没有基线外的歧义标题"
    # 耦死 —— 一旦有人新增一个（正是棘轮要拦的），自检会跟着红、掩盖真信号。用
    # `>= 基线数` + `严格多于带基线时` 既证明"基线确实在豁免"，又不与仓库状态耦合。


def main():
    _p('文档锚点 / 相对链接回归网（0.1.10）')
    _p('范围：仓库全部 *.md（跳过 .git / 缓存 / 临时目录）；纯文本、零网络、围栏内不校验')
    _p('')
    slug_selfproof()
    files, exists = run_real()
    teeth(files, exists)
    _p('')
    if _fails:
        _p('FAILED: %d 项 -> %s' % (len(_fails), _fails))
        return 1
    _p('ALL PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
