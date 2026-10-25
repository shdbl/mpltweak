# -*- coding: utf-8 -*-
"""文档里的 `文件.py:行号` 引用检查器（**只报告，不修改**）。

## 这个工具为什么存在

对外文档 / 手册里写死的 `文件:行号` 会随代码改动漂移。2026-10-13 那一夜，同一批
`writeback.py` 引用**漂移了两次**（t30 刷过一遍，之后 t36 / t51 / t52 / t54 又让它变长）。
所以它**故意不做成 CI 测试** —— 代码一改行号就漂，做成断言只会天天红、最后被
"放宽"掉。定位是**发布期跑一次、人工过目**：输出每条引用**当前那一行到底是什么**，
让人一眼看出"文档说的和代码说的还是不是一回事"。

## 为什么 0.1.13 要"加严"（§25.4 的三项）

加严前它只能发现三类硬伤：解析不到目标文件 / 行号越界 / 指向空行。
**实测**：抽样 5 条里 4 条已经指错地方，而闸门全绿 ⇒ `rc=0` 只是**弱证据**。
`t72` 又实测到「`t80` 修过的 4 个锚点这次**又全漂 +5~+13**」—— 判据只看"存在性"，
**指错函数它看不见**。三项加严：

* **(a) 裸 `:NNN`**：文档里常见 `` `writeback.py:4513` 的 `verify_run`：`:4527` 设这个环境变量 ``
  这种写法（`docs/verify.md:10`），后半截**没有文件名**。旧判据直接看不见 ⇒ 那两处
  "永远不查"。**继承规则只有一条、可指证：只看同一行、且在它之前出现过的 `.py` 名字**
  （带不带行号都算 —— `` `tests/test_fuzz_events.py` 的（`:1154`） `` 这种写法也接得上）。
  本行里没有 `.py` 名字 ⇒ **不猜**，记 `skipped_bare`（只提示、不算可疑，且在汇总行计数）。
  **为什么收窄到"同一行"**（t14 实测出来的假绿）：旧实现按"同段上一处文件名"继承，
  而 `MAINTENANCE.md:832` 的「同文件 `:12`/`:43` … ⇒ `:44` 与之冲突」里的 `:NNN` 指的是
  **文档 `docs/exec-boundaries.md` 自己的行号**，它只跟上一行的 `launch.py:760` 同段 ——
  于是被接成 `launch.py:44` 并误报空行。跨行时"上一行那个文件名跟这个裸行号是不是同一件事"
  **没有机械可证的办法**（同一段、同一句、甚至同一口气都可能指别的文件），所以判据只认
  一行之内这个**可指证的证据**：宁可少判（记提示），也不误报（记可疑）。
* **(b) 符号锚**：文档可以写 `` `writeback.py:2255 # _prune_backups` ``。
  工具在目标行 **±`--anchor-window`（默认 0 = 只认被引的那一行）** 里搜这个符号，搜不到就报
  `symbol_mismatch`，并把**最近一次出现的行号**打出来（人一眼能看出漂了多少）。
  窗口默认 **0**（§28-A1，2026-10-24）而不是 3：±3 那个默认让 **1~3 行的小位移看不见**
  —— 本批 `−1` 瞬时树就是实证（8 条恰好落空行才报红，其余 ~90 条静默差 1 行）；
  窗口越大越迟钝，`+1` 这种漂移也必须被咬到。需要放宽就显式给 `--anchor-window N`
  （**别改默认值**；`--self-test` 方向 10 会咬住默认值）。符号按**子串**匹配
  （`_prune_backups` / `Fig.savefig` 这种带点的名字都按原样搜），不做正则解释。

  **纯注释行不承重**（§28-A2，2026-10-24）：匹配与"最近出现"两个环节都跳过真注释行 ——
  一行横幅注释里满是符号名，锚指向它等于"引用了一句注释"，代码挪走后注释一动不动，
  闸门绿着而引用早失效。判"真注释"用 `tokenize` 的 `COMMENT` token（不是 `#` 前缀）：
  **docstring / 多行字符串里以 `#` 开头的行不是注释**，照样承重（现役就有一条：
  `docs/verify.md:75` 的 `writeback.py:3108 # first` 落在 docstring 正文里）。行尾注释
  （`x = 1  # sym`）所在的那一行仍算代码行、仍承重。
* **(c) 豁免口径（三张表，逐条带理由，键都不含文档行号）**：

  | 表 | 语义 | 命中时 | 豁免范围 |
  |---|---|---|---|
  | `HISTORICAL_ALLOW` | **历史留痕**：`MAINTENANCE.md` 是日志型手册，记的是当时现场 | 打印「放行」、不计可疑 | 任何 `why`（它说的就是"当时对、现在不对"） |
  | `KNOWN_DRIFT` | **已知漂移·待文档侧修**（带 owner + 为什么本批不能改） | 打印「待修」、不计可疑；条目失效会见「空转键」一节 | 任何 `why`（同上） |
  | `UNANCHORED_ALLOW` | **未锚基线**：加锚之前的存量引用，冻结（只准缩小） | 打印「未锚（冻结）」、不计可疑；条目失效会提示删掉 | **只有 `anchor_missing`** |

  **最后一列是判据的一部分，不是注释**（t14 修）：冻结表登记的是"这条引用可以不**锚**"，
  不是"这条引用可以指错地方"。旧实现"先查表、后看 why" ⇒ 冻结条目即使已经指到空行 / 越界 /
  符号不符也一律绿 ⇒ `可疑=0` 是**假绿**（实测 4 条：`docs/exec-boundaries.md:19`/`:106`、
  `docs/verify.md:166` 两处**已经指到空行**，闸门却显示 0 可疑）。现在
  `blank_line` / `line_out_of_range` / `symbol_mismatch` / `target_not_found` /
  `ambiguous_basename` **一律照报**。

  三张表的键都是 `(文档相对路径, 引用串)` —— **刻意不含"文档第几行"**（文档行号会漂，
  与 `tests/test_docs.py` 的 `TEST_EVIDENCE_BASELINE` / `TABLE_COLS_BASELINE` 同纪律）。
  棘轮语义同 `AMBIG_BASELINE`：**新增即红**，**消失请删条目**（会打印"请删掉"）。

  **空转键（§28-A3，2026-10-24）**：三张表都会算"这条账现在还顶不顶用"，结果进
  `scan()['stale_keys']`（`--json` 同名），人类输出里单列一节。两种原因 ——
  `missing`：这条引用在文档里已经不存在（真该删）；`idle`：引用还在、但**这条豁免此刻没被用到**
  （可能是问题修好了账该销，也可能只是暂时没再漂）。判定只对**本面扫到的文档**做（默认面不扫
  `MAINTENANCE.md`，而历史留痕的键全在它上面 —— 不区分就会把 9 条健康账报成"请删"）。
  **只提示、不影响 rc**：账没销不该挡发布，否则判据会被人骂着放宽。
  「哪些引用可以不锚」还有**第一层**、按**整份文档**给（同样不带行号、逐份带理由）：

  | 文档 | 理由 |
  |---|---|
  | `MAINTENANCE.md` | 日志型手册（未入库）：记的是**当时现场**的行号，补锚只会诱使人篡改历史 |

  **新增引用必须带符号锚**；带不了就该登记未锚基线并写理由，而不是放宽整条判据。

## 已知覆盖面缺口（**判据边界，不是实现缺陷**）

裸 `:NNN` 只在**同一行**继承文件名（§25.4a；2026-10-13 由 t14 收窄）。因此"续行接着上文那个
`.py` 的行号"这种写法**不再解析**，只计入汇总行的 `裸引用未继承(只提示)=N`
（0.1.13 落稿时：维护面 `N=28`、默认面 `N=0`）—— 既**不报错也不检查**，只能靠人眼。

**为什么不能便宜地收回来**：`MAINTENANCE.md:832-836` 是反例本尊 —— 那段只提到一个 `.py`
（`launch.py`），而 `:12`/`:43`/`:44` 指的是 `docs/exec-boundaries.md`（**`.md`**）的行号。
「按段落最后一个 `.py`」「按列表项唯一的 `.py`」「按同一句」这三条规则的**任意组合**都会把它
重新接成 `launch.py:12/43/44`，也就是把 t14 修掉的假绿原样装回去 —— **跨行时没有任何机械证据
能区分"续行接着上文"与"这行在说文档自己的行号"**（同段、同句、同一口气都可能指别的文件）。

**干净解**：把那些续行改写成显式 `文件名:NNN`（属文档批改动，不是工具侧改动）；本批已记为
0.1.14 债（`mpltweak_0.1.13_施工记录.md` §7）。

## 改表纪律（三条；下一批谁碰这些表都照这个来）

1. **表可以改，判据不许动** —— 三张表与 `ANCHOR_FREE_DOCS` 是**数据**（文档侧的记账）；
   判定顺序、窗口语义、`--self-test` 的方向集合是**判据**。改完必须 `--self-test` 全过，
   且 `teeth37/38`（逐条带理由、三表不重叠）仍绿。
2. **冻结表只准缩小** —— `UNANCHORED_ALLOW` 只允许删条目，新增引用要带锚。输出里**没有**
   "请从 `UNANCHORED_ALLOW` 删掉"（即 `stale=0`）是继续读这份报告的前提。
3. **「改号」与「补锚」必须同一步落** —— 冻结表按**引用串（含行号）**冻结：改行号那一刻
   新串不在表里、旧串失效 ⇒ 若不同时补锚（或不同时登记），默认面立刻 `anchor_missing` 变红。
   这一条是本批实测踩出来的（t13 的 42 条全是"改号 + 加锚"一步到位）。

## 自带自测（§25.5）：`--self-test`

`t103` 修 F1' 时用 `--root` 手工造过 4 个方向的夹具，**没有沉淀**。现在固化进工具：

1. **src 副本优先**：`src/mpltweak/writeback.py` 与 `build/lib/mpltweak/writeback.py` 同时在场
   ⇒ 必须解析到 **src**、`rc=0`（旧实现会取 `build/` 的陈旧副本）；
2. **裸引用越界**：`` `writeback.py:10` 与 `:99` `` ⇒ `:99` 必须报 `line_out_of_range`（同时
   证明裸引用**真的在被查**，不是被忽略）；
3. **同名多命中**：`x/foo.py` 与 `y/foo.py` ⇒ `ambiguous_basename`（旧实现静默取 `hits[0]`）；
4. **只有 `build/` 副本** ⇒ `target_not_found`（= F1' 的回归测试）。

另加 5 条**新判据自测**（否则 (a)(b)(c) 自己没牙）：符号锚写对 / 写错、窗口是承重的、
未锚登记通道真的生效、**裸引用只在本行内继承**（t14：该继承的继承、跨行的不许误继承）、
**豁免只豁免"未锚"这一项**（t14：冻结条目指到空行必须红）。全部夹具都用
`tempfile.mkdtemp()` 造在系统临时目录，**不往仓库里写任何文件**，`--root` 不再需要人工指定。

## 反向验证（把判据改坏 ⇒ 必须变红）：`--self-test --break MODE`

`MODE ∈ {bare, anchor, window, allow, herit, resolve, all}`：把对应判据**故意弄坏**再跑同一套
自测 ⇒ **必须**有方向 FAIL、`rc=1`。这是"自测不是恒绿"的证据，也是每条新判据的反向验证命令。
`herit` = 复原"跨行继承"的旧坏行为（t14 的假绿源头），用来证明"同一行继承"这条收紧是承重的。
**正常发布闸门绝不许带 `--break`**（没配 `--self-test` 时直接 `rc=2` 拒跑）。

## 用法

    python tools/check_doc_refs.py                    # 默认扫 docs/** + README*.md + skills/**
    python tools/check_doc_refs.py --with-maintenance # 追加 MAINTENANCE.md（未入库，默认不扫）
    python tools/check_doc_refs.py --self-test        # 自带自测（10 个方向）
    python tools/check_doc_refs.py --self-test --break bare   # 反向验证
    python tools/check_doc_refs.py --anchor-window 2  # 放宽符号锚窗口（默认 0 = 只认被引那一行）
    python tools/check_doc_refs.py --json             # 机器可读
    python tools/check_doc_refs.py --root DIR         # 扫别的目录（人工夹具 / 复现）

退出码：**有可疑引用时 rc=1**，便于当发布闸门；干净时 rc=0；用法错（`--break` 没配
`--self-test`）rc=2。

## 范围与判据的边界（写清以免误用）

* 只认 `*.py:行号`；别的语言的行号（如 `foo.rs:12`）不在范围内；
* 目标文件解析顺序：相对仓库根 → **裸 basename 优先 `<root>/src/mpltweak/<basename>`** →
  仓库内**唯一**同名命中 → matplotlib 自带的 `backend_qt.py`（文档里确实引过它的行号）；
  都没有就报"解析不到"；**同名多命中一律报 `ambiguous_basename`（不许静默取第一个）**。
* **不索引构建产物 / 缓存**（`build/`、`dist/`、`*.egg-info`、`__pycache__`、`.git` 等，t103）：
  发布流程是**先 build wheel 再跑闸门**，而 `build/lib/mpltweak/*` 是 `.gitignore` 忽略的
  **陈旧副本** —— 索引它会让闸门"校验一个旧树"（t73 的 F1' 就是这么发生的：41 条引用里 38 条
  解析到了 `build\lib\mpltweak\*`）。
* **只报告，不改任何文件**；`--self-test` 的夹具也在系统临时目录里，跑完即删。
"""
import argparse
import glob as _glob
import io
import json
import os
import re
import shutil
import sys
import tempfile
import tokenize

ROOT_DEFAULT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_INCLUDES = ('docs/**/*.md', 'README*.md', 'skills/**/*.md')

# 完整引用：`writeback.py:4513` / `writeback.py:2126-2165`（区间只查起点行）。
CITE_RE = re.compile(r'([A-Za-z0-9_./\\-]+\.py):(\d+)(?:-(\d+))?')
# 裸引用（§25.4a）：`:4527` / `:829-836`。前置字符不许是 标识符字符 / 点 / 冒号 / 斜杠 ——
# 这样 `12:30`（时间）、`127.0.0.1:7890`（端口）、`http://`（协议）、以及
# `writeback.py:4513` 的后半截都不会被误认成裸引用。
BARE_RE = re.compile(r'(?<![\w:.\\/-]):(\d+)(?:-(\d+))?')
# 一行里出现的 `.py` 名字（**带不带行号都算**）：裸 `:NNN` 只按"本行、且在它之前"的这个
# 证据去继承（§25.4a 的收紧，t14）。写成"名字 + 可选 :行号"，所以
# `writeback.py:4513`（完整引用）与 `tests/test_fuzz_events.py`（只提名字）都能被认出来 ——
# 文档里"`foo.py` 的某函数（`:123`）"这种写法很常见，只认完整引用会漏掉它。
PY_TOKEN_RE = re.compile(r'[A-Za-z0-9_./\\-]+\.py')
# 符号锚（§25.4b）：紧跟引用之后（只允许空白 / 反引号 / 引号）的 `# 符号`。
# 符号必须是 ASCII 标识符形态（可带点）—— 这样正文里 `# 注释` 这类中文不会被当成锚。
ANCHOR_RE = re.compile(r'\s*[`\'"]?\s*#\s*[`]?(?P<sym>[A-Za-z_][A-Za-z0-9_.]*)')

# 符号锚窗口（§25.4b）：`±N` 行内搜不到就算指错。**默认 0 = 只认被引的那一行**（§28-A1）：
#   * 0.1.13 的默认是 3，代价是**1~3 行的小位移看不见** —— 0.1.14 施工中那棵 `−1` 瞬时树
#     实测：只有恰好落空行的 8 条报红，其余 ~90 条静默差 1 行（`+5~+13` 那种大漂移才看得见）；
#   * 归零后"写在哪行就必须在那行"是**可机械判定**的，`+1` 也咬得到；
#   * 需要更宽时用 `--anchor-window N`，**别改默认值**（`--self-test` 方向 10 咬默认值）。
ANCHOR_WINDOW = 0

# 不索引的目录（构建产物 / 缓存）与"裸 basename 的首选解析位置"（t103）。
# `build/` 是 `.gitignore:5` 忽略的**陈旧构建副本**；发布流程先 build wheel 再跑闸门 ⇒
# 索引它 = 闸门在校验旧树（t73 的 F1'：41 条引用里 38 条解析到 `build\lib\mpltweak\*`）。
SKIP_DIRS = {'.git', 'build', 'dist', '__pycache__', '.pytest_cache', '.mypy_cache',
             '.tox', '.eggs', 'node_modules', '.venv', 'venv', '.ruff_cache'}
SRC_PKG = os.path.join('src', 'mpltweak')

# 历史留痕放行（t72）：`(文档相对路径, 引用串)` → **一行理由**。命中的引用单独打印为"放行"、
# 不计入可疑（判据与 rc 语义不变）。**只加"确实是当时现场"的条目，不许为了让闸门变绿而放宽**：
# 这些行号一旦被"改对"就等于篡改历史（MAINTENANCE 是日志型手册）；现行行号见各节正文与 ★ 段。
# **只准缩小**（t103 缩小记录）：`('MAINTENANCE.md', 'writeback.py:4432')` 曾在本表里 —— 那是
# 闸门解析到旧 `build/` 副本时的产物（该副本那行是空行）；修解析后它命中 `src/mpltweak/writeback.py`
# 的非空行 ⇒ 不再可疑，条目已删。
# **本表（以及另外两张豁免表）有几条、本面扫到几条、本面真生效几条，一律由闸门现算**：
# 人类输出摘要行 + `--json` 的 `counts.tables` 字段；**这里的注释刻意不写任何数字**（§28-A4）——
# 旧注释曾写死一个手写计数，但随着条目增删早就少算了（每多一条"一处键、两行引用"就再漂一次），
# 而且没有任何东西会因为数字错而变红。teeth51 现在咬住这条纪律：表头注释里出现手写计数即红。
HISTORICAL_ALLOW = {
    # ---- §20.1 交接清单（该节自注："下面每条引用的行号是当时的现场值……本节作为历史记录
    # 保留，不改写原数字"）。这条现在落在空行上，按历史留痕放行；现役值见 §14.2。
    #
    # **0.1.18（T4 债①）按"空转键"清掉三条** —— `--with-maintenance --json` 的 `stale_keys`
    # 逐条报出原因，三条都不是"历史值还在被用"，而是账已经用不上：
    #   * `verify.py:1436` —— `idle`：那条裸引用还在文档里（§14.2② 的
    #     `` `verify.py:1061` / `:1328` / `:1436` `` 同一行继承 `verify.py`），但目标行
    #     **现在是普通代码行**（某函数签名的形参那行）⇒ 空行 / 越界 / 符号锚一条都不触发，
    #     这条豁免用不上。历史值本身留在文档正文里（不删文档、不改旧行号）。
    #   * `writeback.py:452-483` —— `idle`：区间只查起点行，而第 452 行现在是 docstring 正文行
    #     （非空）⇒ 同样用不上。原文自己写着"已随 0.1.13 的重写失效，别再照着找"，
    #     这句自注本身就是最好的防误用说明，不必再挂一条豁免。
    #   * `writeback.py:5412` —— `missing`：`MAINTENANCE.md` 里**已经不存在**这条引用
    #     （§20.1 第 5 条被改写掉了）⇒ 键指向空气。
    # **为什么留着反而危险**：这三条账一旦挂着，以后同一处引用真的漂到空行 / 越界时，
    # 会被旧账**静默吞掉**（`可疑=0` 变假绿）—— 这正是 §28-A3 加 `stale_keys` 的动机。
    # 删前/删后的引用条数与可疑数逐项不变（撤销记录与两次运行证据见 `_team014/debts18.md`）。
    # **0.1.18（t5 落仓）再清掉最后一条 —— 但这次不是"账用不上"，是把引用改对了**：
    #   * `verify.py:829` —— `idle`：§20.1 第②条那句"`_collect_session` 真初始化 / 置空"
    #     已按**符号真身**重指到 `verify.py:875 # matplotlib.use` / `:877 # switch_backend`
    #     （真初始化）与 `:880-882`（置空）—— 历史叙述一字未改，只是把"指向哪两行代码"
    #     改到那两行现在的位置。引用改对之后豁免自然用不上 ⇒ 删。**注意方向**：这不是
    #     "为了让闸门变绿而放宽账"，正相反 —— **放宽的反面就是把引用改对**。
    # 于是本表空了。空表不是"留白"：它是"当前没有任何一条引用处在'该保留旧行号'的状态"这个
    # 事实；真要再挂账，理由必须写在这一行下面（`--json` 的 `stale_keys` 会当场咬住空转键）。
}

# 「哪些引用可以不锚」（§25.4c）的第一层：**整份文档**层面（逐份带理由）。
# 键是文档相对路径、值是一行理由 —— 同样**不含任何文档行号**。
# `MAINTENANCE.md` 是**日志型手册**（未入库）：它记的是"当时现场"的行号，给历史行号补符号锚
# 没有意义，反而会诱使人把历史改成当前值（`HISTORICAL_ALLOW` 存在的全部理由就是不許这么干）。
# 所以整份**不要求锚**；但**解析 / 越界 / 空行 / 已写锚的符号**照查。这是"谁必须带锚"的口径，
# **不是**"这些引用可以不查"。
ANCHOR_FREE_DOCS = {
    'MAINTENANCE.md': '日志型手册（未入库）：记的是当时现场，给历史行号补锚没有意义（§25.4c）',
}

# 未锚基线（§25.4c）：`(文档相对路径, 引用串)` → 一行理由。命中 = 该引用**允许不带符号锚**
# （打印"未锚（冻结）"、不计可疑）。**语义是棘轮，不是永久豁免**：
#   * **新增引用不许再加进来** —— 新引用要么带 `# 符号`，要么这条判据就成了摆设；
#   * 存量条目**只准缩小**：哪天把锚补上，就把对应条目删掉（工具会打印"请删掉"）；
#   * 键不含"文档第几行"（文档行号会漂，与 `TEST_EVIDENCE_BASELINE` 同纪律）。
#
# **t13（0.1.13 文档批）缩小记录 39 → 6**：默认面 48 条引用里原本 39 条登记在这里；按最终代码
# 重算行号时**行号与符号锚一次性落地**（改号那一刻新引用串不在本表里，必须先补锚，否则默认面
# 立刻 `anchor_missing` 变红 —— t14 提醒的顺序坑），于是 33 条退役。剩下 6 条不是"懒得锚"，
# 而是**锚不出可区分符号**：argparse 注册行只有通用的 `add_argument`（每行都有 ⇒ 锚了也验不出
# 位移，纯装饰），`tests/sandbox/*` 是测试夹具、没有产品符号。
_FIXTURE = '指向 tests/sandbox/* 测试夹具（非产品源码），没有稳定的产品符号可锚'
_NO_SYM = '该行是 argparse 注册行 / 没有可区分符号（唯一能锚的是通用 `add_argument`，会退化成装饰性锚）⇒ 改由人工过目'
UNANCHORED_ALLOW = {
    # 只剩测试夹具两条。**四条 argparse 注册行引用已于 0.1.14（B3）退役** ——
    # 处理方式不是"给 add_argument 补锚"（`add_argument` 每行都有 ⇒ 锚会退化成装饰），
    # 而是把 docs 里那 4 处改成「文件 + 选项名」的描述式引用（不带行号）：
    #   `--dry-run` 注册点、以及 `--lang` 的三处注册。
    # 为什么更可靠（有实证）：退役前三处的行号**全是旧号且无人察觉**
    #   （`apply.py:754` 实为 839、`check.py:570` 实为 601、`apply.py:744-746` 实为 827），
    #   因为挂在"未锚（冻结）"上 ⇒ 闸门从不检查它们；描述式配一条 `grep -n "'--lang'"`
    #   反而**可当场复核**，行号只会静静烂掉。
    ('docs/reliability.md', 'tests/sandbox/sandbox_env.py:52'): _FIXTURE,
    ('docs/reliability.md', 'tests/sandbox/sitecustomize.py:42'): _FIXTURE,
}

# 已知漂移·待文档侧修：`(文档相对路径, 引用串)` → `owner + 为什么本批不能修`。
# 与 `HISTORICAL_ALLOW` 的区别：**那些是"当时现场"（本来就该保留旧行号）；这些是"现在的锚点
# 真的错了、只是修它不在本任务的改动范围内"**。命中打印"待修"、不计可疑，但**条目本身是债**：
# 目标文件定稿后由文档侧重算并**删掉条目**（工具会打印"请删掉"）。
#
# **0.1.13 文档批（t13）已清空本表**：三条 `writeback.py:4531`（`docs/boundaries.md:21` /
# `docs/exec-boundaries.md:44` / `docs/verify.md:10`）已按最终代码重算成 `writeback.py:5538 # cmd`
# 并加了符号锚 ⇒ 债还完、条目撤掉。以后再有"知道指错、本批修不了"的引用，仍可往这里加
# （同样要带 owner + 一行理由）。
KNOWN_DRIFT = {}


def _flag(rec, why, susp, items, hist=None, drift=None, unanch=None, ignore_allow=False,
          exempt_any_why=False):
    """记一条可疑。豁免表命中则改记对应状态、不计入可疑（见模块 docstring 的表）。

    **豁免的范围是有边界的（t14 修，实测出来的假绿）**：
      * `UNANCHORED_ALLOW` 登记的是"**这条引用可以不锚**"，所以它**只**豁免 `anchor_missing`
        这一项；`blank_line` / `line_out_of_range` / `symbol_mismatch` / `target_not_found` /
        `ambiguous_basename` **一律照报**。
        依据：t14 现场 —— 冻结条目里有 4 条（`docs/exec-boundaries.md:19`/`:106`、
        `docs/verify.md:166` 两处）**已经指到空行**，旧实现因为"先查表后看 why"把它们
        吞成绿的，`可疑=0` 就是假绿。
      * `HISTORICAL_ALLOW`（当时现场）与 `KNOWN_DRIFT`（已知漂移·待修）**不是**"允许未锚"，
        它们说的正是"这条引用现在指的位置不对/是历史值"，所以对任何 `why` 都成立 —— 保持原样。

    `ignore_allow=True` 只给反向验证用（`--break allow`）：三张表一律当空，用来看
    "登记通道一旦失效，闸门是不是立刻红"。
    """
    rec['why'] = why
    key = (rec['doc'], rec['ref'])
    rec['exempt'] = ''
    rec['exempt_why'] = ''
    if not ignore_allow:
        tables = [('allowed', HISTORICAL_ALLOW if hist is None else hist),
                  ('drift', KNOWN_DRIFT if drift is None else drift)]
        if why == 'anchor_missing' or exempt_any_why:   # ← 只有"未锚"可被冻结表豁免（t14）
            tables.append(('unanchored', UNANCHORED_ALLOW if unanch is None else unanch))
        for kind, table in tables:
            if key in table:
                rec['exempt'] = kind
                rec['exempt_why'] = table[key]
                break
    if rec['exempt']:
        items.append(rec)
        return
    susp.append(rec)
    items.append(rec)


def _setup_console():
    """控制台兜底：GBK 控制台下打印中文 / 任意字节不许崩（与仓库既有 tools/ 同款）。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors='replace')
        except (AttributeError, ValueError):
            pass


def _iter_docs(root, patterns):
    """展开 glob（** 递归），返回去重后的文件列表。"""
    out = []
    for pat in patterns:
        for p in _glob.glob(os.path.join(root, pat), recursive=True):
            if os.path.isfile(p) and p not in out:
                out.append(p)
    return sorted(out)


def _index_py(root, strict=True):
    """裸 basename → 候选路径列表。`strict=False` 只给反向验证用（把 t103 的修法关掉）。"""
    idx = {}
    skip = SKIP_DIRS if strict else {'.git', '__pycache__', '.pytest_cache'}
    for d, subs, fs in os.walk(root):
        subs[:] = [s for s in subs if s not in skip and not s.endswith('.egg-info')]
        if any(part in skip for part in d.split(os.sep)):
            continue
        for f in fs:
            if f.endswith('.py'):
                idx.setdefault(f, []).append(os.path.join(d, f))
    try:                                  # 文档里引过 matplotlib 自己的 backend_qt.py
        import matplotlib.backends.backend_qt as _bq
        idx.setdefault('backend_qt.py', []).append(_bq.__file__)
    except Exception:                     # noqa: BLE001 - 没装 matplotlib 就少一类可解析目标
        pass
    return idx


class _Cache(object):
    def __init__(self):
        self._d = {}

    def lines(self, path):
        if path not in self._d:
            try:
                with io.open(path, encoding='utf-8', errors='replace') as f:
                    self._d[path] = f.read().split('\n')
            except OSError:
                self._d[path] = None
        return self._d[path]


def _resolve(root, idx, path, strict=True):
    """解析引用 → `(target, ambiguous)`。

    `ambiguous` 非空 = **同名多命中且首选位置没有**：返回候选相对路径列表（调用方报
    `ambiguous_basename`），**绝不静默取 `hits[0]`**（t103：那正是"闸门读错树"的机制）。
    `strict=False` 复原 t103 之前的坏行为（无 src 优先 + 静默取首个），只给反向验证用。
    """
    if os.path.isabs(path):
        return (path if os.path.exists(path) else None), []
    cand = os.path.join(root, path.replace('/', os.sep))
    if os.path.exists(cand):
        return cand, []
    base = os.path.basename(path)
    if strict and os.sep not in path.replace('/', os.sep):
        # 裸 basename：本仓库的产品源码位置优先（`src/mpltweak/`）
        preferred = os.path.join(root, SRC_PKG, base)
        if os.path.isfile(preferred):
            return preferred, []
    hits = [h for h in idx.get(base, []) if os.path.isfile(h)]
    if not hits:
        return None, []
    if len(hits) == 1:
        return hits[0], []
    if not strict:
        return hits[0], []                # ← 反向验证专用：t103 之前"静默取第一个"的坏行为
    return hits[0], [os.path.relpath(h, root) for h in sorted(hits)]


def _anchor_after(line, pos):
    """引用之后紧跟的 `# 符号`（§25.4b）→ 符号串；没有就 None。"""
    m = ANCHOR_RE.match(line, pos)
    return m.group('sym') if m else None


def _last_py_token(line, pos):
    """`line[:pos]` 里**最后一个** `.py` 名字（§25.4a 的继承证据）；没有就 None。

    只看"同一行、且在裸引用之前"这一段 —— 这就是这条判据唯一可指证的依据：
    文档自己写了哪个 `.py`，裸行号就接哪个；没写就**不猜**（记 `skipped_bare` 提示）。
    """
    name = None
    for m in PY_TOKEN_RE.finditer(line, 0, pos):
        name = m.group(0)
    return name


def _iter_refs(line, allow_bare=True):
    """一行里的全部引用（完整 + 裸），按出现位置排序。

    裸引用（`:4527`）与完整引用（`writeback.py:4513`）**位置重叠时算完整引用**，
    不许同一处被数两遍。
    """
    out, spans = [], []
    for m in CITE_RE.finditer(line):
        spans.append(m.span())
        out.append({'file': m.group(1), 'start': int(m.group(2)), 'end': m.group(3),
                    'span': m.span(), 'inherited': False,
                    'anchor': _anchor_after(line, m.end())})
    if allow_bare:
        for m in BARE_RE.finditer(line):
            a, b = m.span()
            if any(not (b <= s0 or a >= s1) for s0, s1 in spans):
                continue
            out.append({'file': None, 'start': int(m.group(1)), 'end': m.group(2),
                        'span': (a, b), 'inherited': True,
                        'anchor': _anchor_after(line, m.end())})
    out.sort(key=lambda r: r['span'][0])
    return out


def _comment_lines(lines):
    """哪些行是**纯注释行**（1-based 行号集合）：该行首个非空字符落在 `tokenize` 的 COMMENT token 上。

    §28-A2（2026-10-24）：**纯注释行不承重**。理由不是洁癖 —— 一行横幅注释
    （`# ---- prune_backups 相关 ----`）里满是符号名，锚指向它等于"引用了注释里的一句话"，
    代码真挪走时它一动不动 ⇒ 闸门绿着而引用早失效。行尾注释（`x = 1  # sym`）**不算**：
    那一行本身是代码，符号出现在哪儿不影响"这行是那句代码"。

    为什么用 `tokenize` 而不是 `l.strip().startswith('#')`：docstring / 多行字符串里可能有以
    `#` 开头的行（reST 的 `# ` 小标题、`>>> # 输出`），按前缀一刀切会连**字符串里的承重锚**
    一起排掉。tokenize 只认真正的 COMMENT token，字符串内的 `#` 一律不算 —— docstring 因此
    **不受影响**（§28-A2 的"docstring 例外"就是这个意思：不是特批，而是它本来就不是注释）。

    解析不了（语法怪 / 半截文件 / 编码异常）⇒ 退回前缀法，**宁严勿松**：宁可把注释当符号，
    也不放过"引用了一行注释"。
    """
    text = '\n'.join(lines) + '\n'
    out = set()
    try:
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type != tokenize.COMMENT:
                continue
            row, col = tok.start
            src = lines[row - 1] if 0 <= row - 1 < len(lines) else ''
            if not src[:col].strip():        # `#` 前面只有空白 ⇒ 整行是注释
                out.add(row)
    except Exception:
        out = set(i for i, l in enumerate(lines, 1) if (l or '').lstrip().startswith('#'))
    return out


def _symbol_where(lines, sym, line_no, window):
    """符号锚判定 → `('', 命中行)` / `('out_of_window', 最近出现行)` / `('not_found', None)`。

    **两个环节都跳过纯注释行**（§28-A2）：① 窗口内匹配；② 窗口外"最近出现"的定位。只排 ①
    的话，提示会变成"最近出现于第 5 行"，而第 5 行是一行横幅注释 —— 人照着去锚就锚到注释上。

    窗口外时报的是**离被引行最近**的那一处（`min(..., key=|h - line_no|)`），不是"文件里第一处"。
    为什么（t16 现场）：`docs/verify.md:76` 的锚 `oldest_backup` 在 `revert.py` 出现于
    `[58, 163]`（58 = `def oldest_backup`，163 = `_first = oldest_backup(...)`）。旧实现取
    `hits[0]` ⇒ 报"最近出现于第 58 行"，而实际只差 1 行（163）—— 文案说"最近"却给了最远的那个，
    人一眼看过去会以为差了 104 行，把"补锚还是改号"的判断带偏。**只影响提示文字，不影响判定**：
    走不走 `symbol_mismatch` 只看「窗口内有没有」，这段代码在"窗口内没有"之后才执行。
    （那一处锚在 §28-A1 已换成同偏移的 `# args.first`；上面记的 `oldest_backup` 是 t16 的
    历史现场，别拿它当"现在文档里写着什么"。）

    **docstring 不排**（§28-A2 的"例外"不是特批，是它本来就不是注释）：现役就有一条承重锚
    落在 docstring 里 —— `docs/verify.md:75` 的 `writeback.py:3108 # first`，命中的那一行是
    docstring 正文（"……却仍报 `first: true`。"），一刀切按 `#` 前缀/按字符串排都会把它判红。
    """
    if not lines:
        return 'not_found', None
    skip = _comment_lines(lines)
    lo, hi = max(1, line_no - window), min(len(lines), line_no + window)
    for i in range(lo, hi + 1):
        if i in skip:
            continue
        if sym in (lines[i - 1] or ''):
            return '', i
    hits = [i for i, l in enumerate(lines, 1)
            if i not in skip and sym in (l or '')]
    if hits:
        return 'out_of_window', min(hits, key=lambda h: abs(h - line_no))
    return 'not_found', None


def scan(root, patterns, allow_bare=True, anchor_window=ANCHOR_WINDOW,
         verify_anchor=True, require_anchor=True, strict_resolve=True,
         line_scope=True, hist=None, drift=None, unanch=None, ignore_allow=False,
         exempt_any_why=False):
    """扫一遍，返回 dict：items / suspicious / allowed / drift / unanchored / stale / skipped_bare。

    每条 item：doc / doc_line / ref / target / line / code / why / anchor / anchor_at /
    inherited / exempt / exempt_why（+ ambiguous 时的 candidates）。

    `line_scope=False` **只给反向验证用**（`--break herit`）：复原"跨行继承"的旧坏行为，
    用来证明"同一行继承"这条收紧是承重的（见模块 docstring §25.4a）。
    """
    cache = _Cache()
    idx = _index_py(root, strict=strict_resolve)
    hist_t = HISTORICAL_ALLOW if hist is None else hist
    drift_t = KNOWN_DRIFT if drift is None else drift
    unanch_t = UNANCHORED_ALLOW if unanch is None else unanch
    items, susp, skipped = [], [], []
    for doc in _iter_docs(root, patterns):
        # 统一用 `/`（POSIX）表示：三张豁免表的键、`--json`、跨平台（CI 是 Linux）
        # 都按这个写法对齐；否则 Windows 上键会变成 `docs\\x.md`，表永远命中不了。
        rel_doc = os.path.relpath(doc, root).replace(os.sep, '/')
        # 整份文档级的"不要求锚"（§25.4c 第一层）：日志型手册不要求锚，但仍然查解析/越界/空行。
        need_anchor = require_anchor and rel_doc not in ANCHOR_FREE_DOCS
        para_file = None                  # 只给 `--break herit` 用的"跨行继承"状态
        for i, line in enumerate(cache.lines(doc) or [], 1):
            if not line.strip():
                para_file = None
                continue
            for one in _iter_refs(line, allow_bare=allow_bare):
                path = one['file']
                if path is None:
                    # §25.4a：裸 `:NNN` 只继承**同一行内、出现在它之前**的 `.py` 名字。
                    # 为什么收窄到一行（t14 实测）：MAINTENANCE.md:832 的 "同文件 `:12`/`:43`…
                    # ⇒ `:44` 与文档 `docs/exec-boundaries.md` 的**行号**自指，上一行的
                    # `launch.py:760` 跟它无关；按段落继承就会接成 `launch.py:44` 并误报空行。
                    # 一行之内是**可指证**的证据（"本行出现过哪个 .py"），跨行只能靠猜。
                    holder = _last_py_token(line, one['span'][0])
                    if holder is None and not line_scope:
                        holder = para_file
                    if holder is None:
                        skipped.append({'doc': rel_doc, 'doc_line': i,
                                        'ref': ':%d' % one['start'],
                                        'why': 'bare_without_file'})
                        continue
                    path = holder
                else:
                    para_file = path
                ref = '%s:%d%s' % (path, one['start'],
                                   ('-' + one['end']) if one['end'] else '')
                rec = {'doc': rel_doc, 'doc_line': i, 'ref': ref, 'target': None,
                       'line': one['start'], 'code': '', 'why': '',
                       'anchor': one['anchor'] or '', 'anchor_at': None,
                       'inherited': one['inherited'],
                       'exempt': '', 'exempt_why': ''}      # 每条都带，`--json` 消费方不用防空
                tgt, ambig = _resolve(root, idx, path, strict=strict_resolve)
                if ambig:
                    rec['target'] = os.path.relpath(tgt, root)
                    rec['candidates'] = ambig
                    _flag(rec, 'ambiguous_basename', susp, items,
                          hist_t, drift_t, unanch_t, ignore_allow, exempt_any_why)
                    continue
                if tgt is None:
                    _flag(rec, 'target_not_found', susp, items,
                          hist_t, drift_t, unanch_t, ignore_allow, exempt_any_why)
                    continue
                rec['target'] = os.path.relpath(tgt, root)
                lines = cache.lines(tgt)
                if lines is None or one['start'] > len(lines):
                    _flag(rec, 'line_out_of_range', susp, items,
                          hist_t, drift_t, unanch_t, ignore_allow, exempt_any_why)
                    continue
                code = lines[one['start'] - 1].strip()
                rec['code'] = code
                if not code:
                    _flag(rec, 'blank_line', susp, items,
                          hist_t, drift_t, unanch_t, ignore_allow, exempt_any_why)
                    continue
                # —— 符号锚（§25.4b）：写了就**必须**对得上；没写见下面"必须带锚"那条 ——
                if one['anchor'] and verify_anchor:
                    verdict, where = _symbol_where(lines, one['anchor'],
                                                   one['start'], anchor_window)
                    rec['anchor_at'] = where
                    if verdict:
                        rec['anchor_why'] = verdict
                        _flag(rec, 'symbol_mismatch', susp, items,
                              hist_t, drift_t, unanch_t, ignore_allow, exempt_any_why)
                        continue
                elif not one['anchor'] and need_anchor:
                    _flag(rec, 'anchor_missing', susp, items,
                          hist_t, drift_t, unanch_t, ignore_allow, exempt_any_why)
                    continue
                items.append(rec)
    used = {(r['doc'], r['ref']) for r in items}
    _docs = sorted({r['doc'] for r in items} | {s['doc'] for s in skipped})
    # 哪张表**真的用上了**（§28-A3）：只统计"被豁免"的条目 —— 引用还在、但这条引用当前
    # 根本不需要豁免，账挂着就是空转（问题修好了账还挂着 ⇒ 以后同类新问题会被旧账吞掉）。
    _hit = {}
    for r in items:
        if r['exempt']:
            _hit.setdefault(r['exempt'], set()).add((r['doc'], r['ref']))

    def _stale(table, label, kind):
        """空转键：① `missing` = 引用在仓库里已不存在；② `idle` = 引用还在但没被这条豁免用到。

        **本次没扫到的文档不算空转**（`unscanned`）：例如默认面不扫 `MAINTENANCE.md`，而历史留痕
        表的键全在它上面 —— 报成"引用已不存在（请删条目）"就是纯误导。这类只能交给人：换个面再跑。
        """
        out, skipped_tables = [], []
        for (doc, ref) in sorted(table):
            if doc not in _docs:
                skipped_tables.append({'table': label, 'doc': doc, 'ref': ref})
                continue
            if (doc, ref) not in used:
                out.append({'table': label, 'doc': doc, 'ref': ref, 'reason': 'missing'})
            elif (doc, ref) not in _hit.get(kind, set()):
                out.append({'table': label, 'doc': doc, 'ref': ref, 'reason': 'idle'})
        return out, skipped_tables

    stale = sorted(set(unanch_t) - used)          # 向后兼容：仍只指 UNANCHORED_ALLOW 的"引用消失"
    stale_keys, stale_unscanned = [], []
    for _t, _l, _k in ((hist_t, 'HISTORICAL_ALLOW', 'allowed'),
                       (drift_t, 'KNOWN_DRIFT', 'drift'),
                       (unanch_t, 'UNANCHORED_ALLOW', 'unanchored')):
        _o, _s = _stale(_t, _l, _k)
        stale_keys += _o
        stale_unscanned += _s

    def _tbl(table, kind):
        """一张表的三个人数（§28-A4：**所有计数都由这里算**，任何人都不许再手写数字）。"""
        return {'keys': len(table),                                     # 表里一共几条
                'in_scan': len(set(table) & used),                      # 本面扫到的几条
                'exercised': len(_hit.get(kind, ()))}                   # 本面真被豁免用到的几条

    counts = {
        'refs': len(items),
        'suspicious': len(susp),
        'anchored': len([r for r in items if r['anchor'] and not r['why']]),
        'skipped_bare': len(skipped),
        'tables': {'HISTORICAL_ALLOW': _tbl(hist_t, 'allowed'),
                   'KNOWN_DRIFT': _tbl(drift_t, 'drift'),
                   'UNANCHORED_ALLOW': _tbl(unanch_t, 'unanchored')},
        'stale_keys': len(stale_keys),
        'stale_unscanned': len(stale_unscanned),
    }
    return {
        'items': items,
        'suspicious': susp,
        'allowed': [(k, v) for k, v in hist_t.items() if k in used],
        'drift': [(k, v) for k, v in drift_t.items() if k in used],
        'unanchored': sorted(k for k in unanch_t if k in used),
        'stale': stale,
        'stale_keys': stale_keys,
        'stale_unscanned': stale_unscanned,
        'exercised': dict((k, sorted(v)) for k, v in _hit.items()),
        'counts': counts,
        'skipped_bare': skipped,
        'docs': _docs,
    }


# --------------------------------------------------------------------------- 渲染
def _mark(rec):
    if not rec['why']:
        return '   '
    return {'allowed': ' ~ ', 'drift': ' D ', 'unanchored': ' u '}.get(rec['exempt'], ' ! ')


def _detail(r):
    if r['why'] == 'target_not_found':
        detail = '<解析不到该文件>'
    elif r['why'] == 'ambiguous_basename':
        detail = '<同名多命中: %s>' % ' / '.join(r.get('candidates') or [])
    elif r['why'] == 'line_out_of_range':
        detail = '<越界>'
    elif r['why'] == 'blank_line':
        detail = '<空行>'
    elif r['why'] == 'symbol_mismatch':
        if r.get('anchor_why') == 'not_found':
            detail = '<符号 `%s` 在整个文件里都找不到> 该行= %s' % (
                r['anchor'], (r['code'] or '')[:60])
        else:
            detail = '<符号 `%s` 最近出现于第 %s 行（窗口之外）> 该行= %s' % (
                r['anchor'], r.get('anchor_at'), (r['code'] or '')[:60])
    elif r['why'] == 'anchor_missing':
        detail = '<未带符号锚> 该行= %s' % (r['code'] or '')[:70]
    else:
        detail = (r['code'] or '')[:100]
    if r['anchor'] and not r['why']:
        detail = 'sym=%s@%s | %s' % (r['anchor'], r.get('anchor_at'), detail)
    if r['inherited']:
        detail = '[裸引用·继承文件名] ' + detail
    return detail


def render(res, root, patterns, out=None):
    out = out or sys.stdout
    print('文档引用检查（只报告）：root=%s' % root, file=out)
    print('扫描面：%s' % '、'.join(patterns), file=out)
    print('', file=out)
    for r in res['items']:
        print('%s%s:%d | %s | %s' % (_mark(r), r['doc'], r['doc_line'], r['ref'],
                                     _detail(r)), file=out)
    print('', file=out)
    print('引用条数=%d 可疑=%d 带符号锚=%d 裸引用未继承(只提示)=%d'
          % (len(res['items']), len(res['suspicious']),
             res['counts']['anchored'], len(res['skipped_bare'])), file=out)
    # §28-A4：豁免表的人数**一律由 scan() 现算**，不手写、也不单列一个会被误读的数字。
    # 「表内 9 / 本面生效 0」把"账挂着"和"真在放行"分得清清楚楚（旧写法「放行(历史留痕)=9」
    # 在 `exempt` 生效数为 0 时仍显示 9，属"尺子报假绿灯"，2026-10-24 船长裁示改掉）。
    _t = res['counts']['tables']
    print('豁免表人数（表内 / 本面扫到 / 本面真生效）：历史留痕 %d/%d/%d ｜ 已知漂移 %d/%d/%d ｜ '
          '未锚冻结 %d/%d/%d'
          % (_t['HISTORICAL_ALLOW']['keys'], _t['HISTORICAL_ALLOW']['in_scan'],
             _t['HISTORICAL_ALLOW']['exercised'],
             _t['KNOWN_DRIFT']['keys'], _t['KNOWN_DRIFT']['in_scan'],
             _t['KNOWN_DRIFT']['exercised'],
             _t['UNANCHORED_ALLOW']['keys'], _t['UNANCHORED_ALLOW']['in_scan'],
             _t['UNANCHORED_ALLOW']['exercised']), file=out)
    _idle = [k for k in res.get('stale_keys') or []
             if k['table'] != 'UNANCHORED_ALLOW']
    _unsc = res.get('stale_unscanned') or []
    if _idle:
        print('', file=out)
        print('空转键（历史留痕 / 已知漂移两张表里还挂着、但已经用不上；'
              '**只提示、不影响 rc**）：', file=out)
        for k in _idle:
            print('  [S] %s | %s | %s —— %s'
                  % (k['table'], k['doc'], k['ref'],
                     '引用已不存在（文档里已没有这条引用 → 建议删条目）'
                     if k['reason'] == 'missing'
                     else '引用还在、但这条豁免此刻没被用到'
                          '（账可能该销；也可能只是暂时没再漂）'), file=out)
    else:
        print('空转键：无（历史留痕 / 已知漂移两张表里、本面扫到的条目都还在被命中）', file=out)
    if _unsc:
        print('      另有 %d 条豁免条目的文档**本面没扫**（例如 MAINTENANCE.md 只在 '
              '--with-maintenance 面出现），不计空转：%s'
              % (len(_unsc), '、'.join(sorted({k['doc'] for k in _unsc}))), file=out)
    _free = [(d, w) for d, w in ANCHOR_FREE_DOCS.items() if d in res['docs']]
    if _free:
        print('', file=out)
        print('本份文档**不要求**符号锚（日志型手册，仍查解析 / 越界 / 空行 / 已写锚的符号）：',
              file=out)
        for d, w in _free:
            print('  [a] %s —— %s' % (d, w), file=out)
    if res['drift']:
        print('', file=out)
        print('已知漂移·待修（文档侧按最终代码重算后请删条目）：', file=out)
        for (doc, ref), why in res['drift']:
            print('  [D] %s | %s —— %s' % (doc, ref, why), file=out)
    if res['allowed']:
        print('', file=out)
        print('放行（历史留痕 · 当时现场；逐条带理由，不计入可疑）：', file=out)
        _used_hist = set(tuple(k) for k in (res.get('exercised') or {}).get('allowed', []))
        for (doc, ref), why in res['allowed']:
            # §28-A3：这条账**本面有没有真的用上**。没用上还挂在表里 = 空转键（见下面那一节）。
            print('  [~] %s | %s —— %s%s'
                  % (doc, ref, why,
                     '' if (doc, ref) in _used_hist else '（**本面没被用到**）'), file=out)
    if res['stale']:
        print('', file=out)
        print('提示：下列未锚基线条目在仓库里已不存在（该引用已消失或已补上锚）'
              '→ 请从 UNANCHORED_ALLOW 删掉：', file=out)
        for doc, ref in res['stale']:
            print('       - %s | %s' % (doc, ref), file=out)
    if res['skipped_bare']:
        print('', file=out)
        print('裸引用但本段没有可继承的文件名（只提示、不计可疑）：', file=out)
        for r in res['skipped_bare']:
            print('  [?] %s:%d | %s' % (r['doc'], r['doc_line'], r['ref']), file=out)
    if res['suspicious']:
        print('', file=out)
        print('可疑引用（需要人工确认）：', file=out)
        for r in res['suspicious']:
            print('  [X] %s:%d | %s | %s' % (r['doc'], r['doc_line'], r['ref'], r['why']),
                  file=out)
        print('', file=out)
        print('提示：这些引用可能只是指向了已删除 / 移位的代码 —— 请人工判断'
              '"文档想指的那段代码现在在哪"，本工具不改任何文件。', file=out)
    else:
        print('', file=out)
        # §28-A4：这里也不许再出现"历史留痕放行 N 条"这种会被误读的数字 —— 数字可能为 0。
        _cnt = res['counts']['tables']
        _extra = []
        if res['drift']:
            _extra.append('%d 条已知漂移待文档侧修（本面真生效 %d）'
                          % (_cnt['KNOWN_DRIFT']['in_scan'],
                             _cnt['KNOWN_DRIFT']['exercised']))
        if res['allowed']:
            _extra.append('历史留痕表内 %d 条、本面真生效 %d'
                          % (_cnt['HISTORICAL_ALLOW']['keys'],
                             _cnt['HISTORICAL_ALLOW']['exercised']))
        print('[OK] 没有可疑引用%s。注意：判据仍只覆盖"解析不到 / 越界 / 空行 / 符号锚不符"，'
              '%d 条未锚引用（冻结基线）与人工过目仍是必需的。'
              % (('（另：%s）' % '、'.join(_extra)) if _extra else '',
                 _cnt['UNANCHORED_ALLOW']['keys']), file=out)
    return 1 if res['suspicious'] else 0


# --------------------------------------------------------------------------- 自带自测
def _mk(path, text):
    d = os.path.dirname(path)
    if d and not os.path.isdir(d):
        os.makedirs(d)
    with io.open(path, 'w', encoding='utf-8', newline='\n') as f:
        f.write(text)


# 自测夹具：第 10 行 = `def _prune_backups(...)`，第 20 行 = `def verify_run(...)`，
# 第 21~24 行 = `tail*`（给"符号在窗口之外"那条用）。
_WB_FIXTURE = (''.join('line%d\n' % i for i in range(1, 10))
               + 'def _prune_backups(fig, keep=3):\n'
               + ''.join('body%d\n' % i for i in range(1, 11))
               + 'def verify_run(script, python=None):\n'
               + ''.join('tail%d\n' % i for i in range(1, 5)))


def _self_test(break_mode='', out=None):
    """12 个方向的自带自测（§25.5 + t14 + §28-A1 窗口 / §28-A2 注释 / §28-A3 空转键）。

    返回 rc：0 = 全过；1 = 有方向 FAIL。
    """
    out = out or sys.stdout
    broken = set()
    if break_mode:
        broken = ({'bare', 'anchor', 'window', 'allow', 'herit', 'exempt', 'resolve',
                   'comment'}
                  if break_mode == 'all' else {break_mode})
    base = tempfile.mkdtemp(prefix='mpltweak_docrefs_selftest_')
    try:
        # ---- 方向 1：src 副本优先（t103）----
        d1 = os.path.join(base, 'd1')
        _mk(os.path.join(d1, 'docs', 'd.md'),
            '引用：`writeback.py:10 # _prune_backups`\n')
        _mk(os.path.join(d1, 'src', 'mpltweak', 'writeback.py'), _WB_FIXTURE)
        _mk(os.path.join(d1, 'build', 'lib', 'mpltweak', 'writeback.py'),
            _WB_FIXTURE.replace('_prune_backups', 'BUILD_ONLY'))
        r1 = scan(d1, ['docs/**/*.md'], strict_resolve=('resolve' not in broken))
        ok1 = (not r1['suspicious'] and len(r1['items']) == 1
               and r1['items'][0]['target'].replace('\\', '/') ==
               'src/mpltweak/writeback.py')
        # ---- 方向 2：裸引用越界（`:99` 继承 writeback.py）----
        d2 = os.path.join(base, 'd2')
        _mk(os.path.join(d2, 'docs', 'd.md'),
            '引用：`writeback.py:10 # _prune_backups` 与 `:99`\n')
        _mk(os.path.join(d2, 'src', 'mpltweak', 'writeback.py'), _WB_FIXTURE)
        r2 = scan(d2, ['docs/**/*.md'], allow_bare=('bare' not in broken),
                  strict_resolve=('resolve' not in broken))
        ok2 = any(r['why'] == 'line_out_of_range' and r['ref'] == 'writeback.py:99'
                  and r['inherited'] for r in r2['suspicious'])
        # ---- 方向 3：同名多命中 ⇒ ambiguous_basename（t103）----
        d3 = os.path.join(base, 'd3')
        _mk(os.path.join(d3, 'docs', 'd.md'), '引用：`foo.py:1`\n')
        _mk(os.path.join(d3, 'x', 'foo.py'), 'X1\n')
        _mk(os.path.join(d3, 'y', 'foo.py'), 'Y1\n')
        r3 = scan(d3, ['docs/**/*.md'], require_anchor=False,
                  strict_resolve=('resolve' not in broken))
        ok3 = any(r['why'] == 'ambiguous_basename'
                  and len(r.get('candidates') or []) == 2 for r in r3['suspicious'])
        # ---- 方向 4：只有 build/ 副本 ⇒ target_not_found（= F1' 回归）----
        d4 = os.path.join(base, 'd4')
        _mk(os.path.join(d4, 'docs', 'd.md'), '引用：`writeback.py:10`\n')
        _mk(os.path.join(d4, 'build', 'lib', 'mpltweak', 'writeback.py'), _WB_FIXTURE)
        r4 = scan(d4, ['docs/**/*.md'], require_anchor=False,
                  strict_resolve=('resolve' not in broken))
        ok4 = any(r['why'] == 'target_not_found' for r in r4['suspicious'])
        # ---- 方向 5：符号锚（对 / 窗口外 / 整文件没有）----
        d5 = os.path.join(base, 'd5')
        _mk(os.path.join(d5, 'docs', 'd.md'),
            '对：`writeback.py:10 # _prune_backups`\n\n'
            '错（符号在第 20 行、窗口之外）：`writeback.py:10 # verify_run`\n\n'
            '错（整文件没有）：`writeback.py:10 # _no_such_symbol`\n')
        _mk(os.path.join(d5, 'src', 'mpltweak', 'writeback.py'), _WB_FIXTURE)
        r5 = scan(d5, ['docs/**/*.md'], verify_anchor=('anchor' not in broken),
                  strict_resolve=('resolve' not in broken))
        why5 = sorted(r['why'] for r in r5['suspicious'])
        ok5 = (why5 == ['symbol_mismatch', 'symbol_mismatch']
               and len(r5['items']) == 3
               and [r['why'] for r in r5['items']][0] == '')
        # ---- 方向 6：窗口是承重的（0 偏移必过；偏 3 行在默认窗口下必红；显式放宽 ±3 又过）----
        # 0.1.14 §28-A1：默认窗口 3 → 0 ⇒ "偏 3 行"从"能过"变成"必须红"；
        # `--break window` 的弄坏方式也随之反过来：把窗口**撑大**（判据变迟钝）⇒ 本方向必须 FAIL。
        d6 = os.path.join(base, 'd6')
        _mk(os.path.join(d6, 'docs', 'd.md'),
            '同偏移（符号就在第 10 行）：`writeback.py:10 # _prune_backups`\n\n'
            '偏 3 行（符号在第 10 行）：`writeback.py:13 # _prune_backups`\n')
        _mk(os.path.join(d6, 'src', 'mpltweak', 'writeback.py'), _WB_FIXTURE)
        w = 99 if 'window' in broken else ANCHOR_WINDOW
        r6 = scan(d6, ['docs/**/*.md'], anchor_window=w,
                  strict_resolve=('resolve' not in broken))
        r6_wide = scan(d6, ['docs/**/*.md'], anchor_window=3)
        _s6 = [(r['ref'], r['why']) for r in r6['suspicious']]
        ok6 = (_s6 == [('writeback.py:13', 'symbol_mismatch')]
               and not r6_wide['suspicious'])
        # ---- 方向 7：未锚必须红 / 登记后不红 / 失效条目要提示删（§25.4c）----
        d7 = os.path.join(base, 'd7')
        _mk(os.path.join(d7, 'docs', 'd.md'), '引用：`writeback.py:10`\n')
        _mk(os.path.join(d7, 'src', 'mpltweak', 'writeback.py'), _WB_FIXTURE)
        allow7 = {('docs/d.md', 'writeback.py:10'): '自测用：登记理由'}
        allow7_bad = {('docs/d.md', 'writeback.py:99'): '自测用：已失效的条目'}
        r7_raw = scan(d7, ['docs/**/*.md'], unanch={}, ignore_allow=('allow' in broken))
        r7 = scan(d7, ['docs/**/*.md'], unanch=allow7, ignore_allow=('allow' in broken))
        r7_stale = scan(d7, ['docs/**/*.md'], unanch=allow7_bad)
        ok7 = (any(r['why'] == 'anchor_missing' for r in r7_raw['suspicious'])
               and not r7['suspicious']
               and [k for k in r7_stale['stale']] == [('docs/d.md', 'writeback.py:99')])
        # ---- 方向 8：裸引用只在**本行**内继承（t14 的假绿源头）----
        # 正例：同一行的 "`a.py:10` 与 `:12`" 必须继承；
        # 反例：另一行/另一段的 `:44` **不许**接到上一行的 `launch.py:760` 上（现场复刻
        #       MAINTENANCE.md:832 那种"指文档自己的行号"的写法）⇒ 必须记 skipped、不许变红。
        d8 = os.path.join(base, 'd8')
        _mk(os.path.join(d8, 'docs', 'd.md'),
            '本行内继承：`writeback.py:10 # _prune_backups` 与 `:12`。\n'
            '上一行提到了 `launch.py:760`（开窗是同进程），而下一行的裸行号指**文档自己**：\n'
            '    同文件 `:12`/`:43` 已注明开窗是本进程 ⇒ `:44` 与之冲突。\n')
        _mk(os.path.join(d8, 'src', 'mpltweak', 'writeback.py'), _WB_FIXTURE)
        _mk(os.path.join(d8, 'src', 'mpltweak', 'launch.py'),
            ''.join('l%d\n' % i for i in range(1, 801)))
        r8 = scan(d8, ['docs/**/*.md'], require_anchor=False,
                  allow_bare=('bare' not in broken), line_scope=('herit' not in broken))
        _refs8 = [(r['ref'], r['inherited']) for r in r8['items']]
        ok8 = (_refs8 == [('writeback.py:10', False), ('writeback.py:12', True),
                          ('launch.py:760', False)]
               and len(r8['skipped_bare']) == 3
               and not r8['suspicious'])
        # ---- 方向 9：豁免只豁免"未锚"，其它缺陷照报（t14 的假绿）----
        d9 = os.path.join(base, 'd9')
        _mk(os.path.join(d9, 'docs', 'd.md'),
            '指空行：`writeback.py:13`；指越界：`writeback.py:99`。\n')
        _mk(os.path.join(d9, 'src', 'mpltweak', 'writeback.py'),
            ''.join(('b%d\n' % i) if i != 13 else '\n' for i in range(1, 21)))
        _k9a = ('docs/d.md', 'writeback.py:13')
        _k9b = ('docs/d.md', 'writeback.py:99')
        r9 = scan(d9, ['docs/**/*.md'], unanch={_k9a: '自测：只豁免未锚',
                                                _k9b: '自测：只豁免未锚'},
                  ignore_allow=('allow' in broken), exempt_any_why=('exempt' in broken))
        _why9 = sorted(r['why'] for r in r9['suspicious'])
        ok9 = _why9 == ['blank_line', 'line_out_of_range']
        # ---- 方向 10：窗口默认值必须是 0（§28-A1，2026-10-24；防手滑改回 3）----
        # 三条路一起钉：模块常量 / argparse 的 default= / 真解析出来的 args —— 只看常量会漏掉
        # "常量没动但命令行默认值被写成 3"那种改法（`__main__` 那条路才是闸门真正用的值）。
        _ap10 = _build_parser()
        ok10 = (ANCHOR_WINDOW == 0 and _ap10.get_default('anchor_window') == 0
                and _ap10.parse_args([]).anchor_window == 0)
        # ---- 方向 11：空转键能被报出来（§28-A3，2026-10-24；只提示、不影响 rc）----
        # 三种情形一起钉：`missing`（引用已不存在）/ `idle`（引用还在但这条豁免用不上）/
        # **真被用到的那条不许报**（否则提示就没意义了）。
        d11 = os.path.join(base, 'd11')
        _mk(os.path.join(d11, 'docs', 'd.md'),
            '锚对且绿：`writeback.py:10 # _prune_backups`；符号错（交给"已知漂移"放行）：'
            '`writeback.py:11 # nope`\n')
        _mk(os.path.join(d11, 'src', 'mpltweak', 'writeback.py'), _WB_FIXTURE)
        _hist11 = {('docs/d.md', 'writeback.py:19'): '自测：引用已不存在（missing）',
                   ('docs/d.md', 'writeback.py:10'): '自测：这条引用根本不需要豁免（idle）'}
        _drift11 = {('docs/d.md', 'writeback.py:11'): '自测：真在用（不许报）'}
        r11 = scan(d11, ['docs/**/*.md'], hist=_hist11, drift=_drift11, unanch={},
                   strict_resolve=('resolve' not in broken))
        _st11 = [(k['table'], k['ref'], k['reason']) for k in r11['stale_keys']]
        ok11 = (_st11 == [('HISTORICAL_ALLOW', 'writeback.py:10', 'idle'),
                          ('HISTORICAL_ALLOW', 'writeback.py:19', 'missing')]
                and not r11['stale']                       # 兼容字段：未锚表白名单没空转
                and len(r11['suspicious']) == 0)           # 提示不改 rc 语义
        # ---- 方向 12：纯注释行不承重（§28-A2）；`--break comment` 关掉排除 ⇒ 本方向必须 FAIL ----
        d12 = os.path.join(base, 'd12')
        _mk(os.path.join(d12, 'docs', 'd.md'),
            '横幅注释行当锚（第 1 行是注释）：`writeback.py:1 # _prune_backups`\n\n'
            '真代码行当锚（第 11 行 = def）：`writeback.py:11 # _prune_backups`\n')
        _mk(os.path.join(d12, 'src', 'mpltweak', 'writeback.py'),
            '# ---- _prune_backups：横幅注释，不承重 ----\n' + _WB_FIXTURE)
        _orig_cl = globals()['_comment_lines']
        if 'comment' in broken:
            globals()['_comment_lines'] = lambda lines: set()      # 等价 RV-1：关掉注释排除
        try:
            r12 = scan(d12, ['docs/**/*.md'], strict_resolve=('resolve' not in broken))
        finally:
            globals()['_comment_lines'] = _orig_cl
        _s12 = [(r['ref'], r['why']) for r in r12['suspicious']]
        ok12 = _s12 == [('writeback.py:1', 'symbol_mismatch')]
        # ---- 方向 13：计数由工具算，不许手写（§28-A4，2026-10-24）----
        # 复用手臂 11 的夹具与表：`counts` 必须与"真表长 / 真扫到 / 真生效"逐项相等。
        # 为什么钉这个：表头旧注释写死"9 键 / 11 条"，实测 12 条（三处键各被两行引用），
        # 从 t72 起少算 1 条 —— 手写计数**必然**漂，而且没有任何东西会因此变红。
        _c13 = r11['counts']
        ok13 = (_c13['tables']['HISTORICAL_ALLOW'] == {'keys': len(_hist11), 'in_scan': 1,
                                                       'exercised': 0}
                and _c13['tables']['KNOWN_DRIFT'] == {'keys': len(_drift11), 'in_scan': 1,
                                                      'exercised': 1}
                and _c13['tables']['UNANCHORED_ALLOW'] == {'keys': 0, 'in_scan': 0,
                                                           'exercised': 0}
                and _c13['refs'] == len(r11['items'])
                and _c13['stale_keys'] == len(r11['stale_keys']) == 2
                and _c13['stale_unscanned'] == 0)
    finally:
        shutil.rmtree(base, ignore_errors=True)

    checks = [
        ('1) src 副本优先（build/ 的陈旧副本不许被采用）', ok1),
        ('2) 裸引用 `:99` 被继承并被查（line_out_of_range）', ok2),
        ('3) 同名多命中 ⇒ ambiguous_basename', ok3),
        ('4) 只有 build/ 副本 ⇒ target_not_found（F1\' 回归）', ok4),
        ('5) 符号锚：对的过、错的报 symbol_mismatch', ok5),
        ('6) 窗口是承重的（默认 0 偏移必过 / 偏 3 行必红 / 放宽 ±3 又过）', ok6),
        ('7) 未锚必须红 / 登记后豁免 / 失效条目提示删', ok7),
        ('8) 裸引用只在本行内继承（跨行的 `:44` 不许误继承、不许误报）', ok8),
        ('9) 豁免只豁免"未锚"：冻结条目指到空行 / 越界必须照报', ok9),
        ('10) 窗口默认值是 0（只认被引那一行；防手滑改回 3）', ok10),
        ('11) 空转键能报出来（missing / idle；真在用的那条不许报，且不改 rc）', ok11),
        ('12) 纯注释行不承重（横幅注释当锚必红；`--break comment` 会把它关掉）', ok12),
        ('13) 计数由工具算（表内 / 本面扫到 / 本面真生效，与真表长逐项相等）', ok13),
    ]
    print('SELF-TEST（自带自测：t103 四方向 + §25.4 三项判据 + t14 两条收紧'
          ' + §28 的窗口/注释/空转键）', file=out)
    if broken:
        print('!! 反向验证模式 --break %s：判据被**故意**弄坏，下面必须出现 FAIL / rc=1'
              % break_mode, file=out)
    bad = 0
    for name, ok in checks:
        print('  %s  %s' % ('PASS' if ok else 'FAIL', name), file=out)
        if not ok:
            bad += 1
    print('SELF-TEST %s：%d / %d 通过'
          % ('FAILED' if bad else 'PASS', len(checks) - bad, len(checks)), file=out)
    return 1 if bad else 0


# --------------------------------------------------------------------------- CLI
def _build_parser():
    """命令行参数表（**单独成函数**：`--self-test` 的方向 10 要读它的默认值）。

    为什么抽出来：窗口默认值有两个出处 —— 模块常量 `ANCHOR_WINDOW` 与这里的 `default=`。
    只钉常量会漏掉"常量没动、命令行默认值却写成别的"那种改法，而闸门真正跑的是后者的值。
    """
    ap = argparse.ArgumentParser(description='检查文档里的 文件.py:行号 引用（只报告，不修改）')
    ap.add_argument('--root', default=ROOT_DEFAULT, help='仓库根（默认：本脚本的上一级）')
    ap.add_argument('--include', action='append', default=None,
                    help='额外 / 覆盖的扫描 glob（可重复）；给了就只用给的那些')
    ap.add_argument('--with-maintenance', action='store_true',
                    help='追加 MAINTENANCE.md（未入库文件，默认不扫）')
    ap.add_argument('--json', action='store_true', help='输出 JSON')
    ap.add_argument('--anchor-window', type=int, default=ANCHOR_WINDOW,
                    help='符号锚的搜索窗口 ±N（默认 %d = 只认被引那一行；越大越迟钝）'
                         % ANCHOR_WINDOW)
    ap.add_argument('--self-test', action='store_true',
                    help='跑自带自测（t103 四方向 + §25.4 三项判据），不看仓库状态')
    ap.add_argument('--break', dest='break_mode', default='',
                    choices=('', 'bare', 'anchor', 'window', 'allow', 'herit', 'exempt',
                             'resolve', 'comment', 'all'),
                    help='只配 --self-test：故意弄坏某条判据，用来看自测会不会红（反向验证）')
    return ap


def main(argv=None):
    _setup_console()
    ap = _build_parser()
    args = ap.parse_args(argv)

    if args.break_mode and not args.self_test:
        sys.stderr.write('用法错：--break 只能与 --self-test 一起用'
                         '（它会把判据故意弄坏，不能用来跑发布闸门）\n')
        return 2
    if args.self_test:
        return _self_test(args.break_mode)

    root = os.path.abspath(args.root)
    patterns = list(args.include) if args.include else list(DEFAULT_INCLUDES)
    if args.with_maintenance:
        patterns.append('MAINTENANCE.md')

    res = scan(root, patterns, anchor_window=args.anchor_window)
    if args.json:
        sys.stdout.write(json.dumps({'root': root, 'patterns': patterns,
                                     'anchor_window': args.anchor_window,
                                     'items': res['items'],
                                     'suspicious': res['suspicious'],
                                     'allowed': [{'doc': d, 'ref': r, 'why': w}
                                                 for (d, r), w in res['allowed']],
                                     'drift': [{'doc': d, 'ref': r, 'why': w}
                                               for (d, r), w in res['drift']],
                                     'unanchored': [{'doc': d, 'ref': r}
                                                    for d, r in res['unanchored']],
                                     'stale_unanchored': [{'doc': d, 'ref': r}
                                                          for d, r in res['stale']],
                                     'stale_keys': res['stale_keys'],
                                     'stale_unscanned': res['stale_unscanned'],
                                     'counts': res['counts'],
                                     'skipped_bare': res['skipped_bare']},
                                    ensure_ascii=False, indent=2) + '\n')
        return 1 if res['suspicious'] else 0
    return render(res, root, patterns)


if __name__ == '__main__':
    sys.exit(main())
