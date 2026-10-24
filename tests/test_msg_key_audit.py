# -*- coding: utf-8 -*-
"""t95：消息键登记护栏 —— 任何未登记的 `t()` 键都会把**键名回显给用户**。

背景（t93 的真实根因，见 tools/msg_key_audit.py 文件头）：`cli.py` 的 doctor 分支调了
**从未登记**的键 `schema_unknown_args`，而 `messages.t()` 的容错契约是"**缺键原样返回键**"
⇒ 用户在终端看到字面量键名。那次补了登记，但**这一类风险仍敞着**。

三层护栏
--------
**A. 静态扫描**（`tools/msg_key_audit.py`）：`src/mpltweak/**` 里 `t('键')` 引用的键必须
   已登记；漏登记 ⇒ 红，并**点名 `文件:行号` 与键名**。
**B. 症状级**：真跑 CLI（`doctor x.py --nope` / `doctor --json` / `schema`，zh+en），断言
   **用户可见输出里不出现任何已登记键名** —— 键名出现就意味着它被 `t()` 原样回显了；
   并单独钉 t93 的哨兵 `schema_unknown_args`。
**C. teeth**：副本里改掉一条登记 ⇒ 静态扫描**必红并点名调用点**；哨兵注入 ⇒ 症状判据必红。

**`t()` 与 `t_ui()` 的后果不同，刻意分开**（否则一上来 60+ 条误报、检查器直接废掉）
-------------------------------------------------------------------------------
* `t(key)`：键是**内部标识符**；缺 ⇒ 回显键名 ⇒ **违规**。
* `t_ui(zh_template)`：键**就是面向用户的中文模板**；缺 ⇒ `UI_ZH_TO_EN.get(zh, zh)`
  返回**中文原文**（`messages.py:1812-1817` 明写的契约）⇒ 后果是"en 下漏翻"，
  **不是键名回显** ⇒ 归 `ui_untranslated`，**只统计不判红**（en-ASCII 棘轮已在盯）。

未解析的实参（`t(key)` 透传、`t(_DICT[k])`、`'前缀' + 变量`）**显式列出、不假装覆盖**，
并把"未解析调用点数"钉成**只减不增**。
"""

import ast
import os
import shutil
import subprocess
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(_HERE)
sys.path.insert(0, os.path.join(ROOT, 'tools'))
import msg_key_audit as M                                            # noqa: E402

# ---- 输出兜底（T13 纪律）：GBK 控制台 / 管道下 print 中文与记号不崩 ----
# hygiene 的 GBK 棘轮按这两行判定"装了兜底"；notation 也按 `reconfigure(errors=` 判豁免。
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(errors='replace')
    except (AttributeError, ValueError, OSError):
        pass

_fails = []


def _p(s):
    print(s)


def check(name, cond, detail=''):
    if cond:
        _p('OK | %s%s' % (name, (' | %s' % detail) if detail else ''))
    else:
        _fails.append(name)
        _p('FAIL | %s | %s' % (name, detail))


# 台账（**只准缩小**）：已确认、且**已派给 src/** 的漏登记点。键 = (相对路径, 键名)。
#
# **两次缩小记录**（都是"修好就删 + 把修好的行为留成真断言"）：
#   ① 本任务开工时静态扫描发现的唯一真缺陷 `launch.py:640 / doctor_verdict_headless`
#      （与 t93 同一类：键从未登记 ⇒ 用户看到字面量键名）—— code-owner 的 **t96** 修好
#      （`messages.py` 补了该键，MESSAGES 215 → **216**）⇒ 删条目。
#   ② `t_ui()` 漏翻 61 处**从来没进过本台账**：它们不是键名回显（缺登记返回中文原文），
#      归 `ui_untranslated` 只统计不判红。
# ⇒ **台账现为空**。从这一刻起，下面 A 的静态断言**没有台账兜底**，是**真·硬断言**：
#   任何新的漏登记都会**直接判红**（这正是"台账只准缩小"要的终局）。
KNOWN_UNREGISTERED = {}

# 未解析调用点：**只钉总数**（只减不增），条目本身在扫描输出里全文打印。
KNOWN_UNRESOLVED_N = 13


def _key_echoes(txt, keys):
    """症状判据：输出里出现了哪些**已登记键名**（出现 = 该键被回显）。"""
    return sorted(k for k in keys if k in txt)


def _dict_literal_dups(name, path=None):
    """`messages.py` 里 `name = {...}` 的**字典字面量**有哪些重复键 → {键: [行号...]}。

    为什么必须扫**字面量**而不是 `literal_eval` 之后的 dict：后者天生去重（后写的赢），
    重复键在结果里完全看不见 —— 只有 ast 的 `keys` 节点能看见"同一张表里写了两遍"。
    """
    src_path = path or os.path.join(ROOT, 'src', 'mpltweak', 'messages.py')
    with open(src_path, encoding='utf-8') as f:
        tree = ast.parse(f.read())
    node = None
    for n in tree.body:
        if isinstance(n, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == name for t in n.targets):
            node = n.value
    if not isinstance(node, ast.Dict):
        return {'<%s 不是字典字面量>' % name: []}
    seen, dups = {}, {}
    for k in node.keys:
        if not isinstance(k, ast.Constant):
            continue
        if k.value in seen:
            dups.setdefault(k.value, [seen[k.value]]).append(k.lineno)
        else:
            seen[k.value] = k.lineno
    return dups


def _pin(env):
    env = dict(env)
    env['MPLBACKEND'] = 'Agg'
    env['PYTHONIOENCODING'] = 'utf-8'
    env['MPLTWEAK_HOME'] = os.path.join(tempfile.gettempdir(), 'mpltweak_msgkey_home')
    return env


def run_cli(args, lang, cwd):
    env = _pin(os.environ)
    env['MPLTWEAK_LANG'] = lang
    r = subprocess.run([sys.executable, '-m', 'mpltweak'] + list(args), cwd=cwd,
                       capture_output=True, env=env)
    return r.returncode, (r.stdout + r.stderr).decode('utf-8', 'replace')


def teeth(keys, cwd):
    """C：三条单点变异。"""
    tmp = tempfile.mkdtemp(prefix='mpltweak_msgkey_')
    copy = os.path.join(tmp, 'repo')
    try:
        # 副本要能**独立跑**：src/ + tools/ + 这个套件文件本身 —— 因为 captain t96 要求
        # 证明"**测试侧的牙**"也咬了（不能只证明 scanner 咬了）。
        shutil.copytree(os.path.join(ROOT, 'src'), os.path.join(copy, 'src'))
        shutil.copytree(os.path.join(ROOT, 'tools'), os.path.join(copy, 'tools'))
        os.makedirs(os.path.join(copy, 'tests'))
        shutil.copy2(os.path.abspath(__file__), os.path.join(copy, 'tests'))
        mp = os.path.join(copy, 'src', 'mpltweak', 'messages.py')
        with open(mp, encoding='utf-8') as f:
            txt = f.read()
        before = M.scan(copy)
        _p('  副本基线：漏登记 %d 处（**应为 0** —— 台账已空）' % len(before['bad']))
        check('teeth⓪ 副本基线本身干净（防"基线就红、变异看不出效果"）',
              not before['bad'], '基线命中 %s' % (before['bad'][:2],))
        # 变异①：把一条**已登记**的键改名 ⇒ 引用它的调用点立刻变成漏登记
        assert "'schema_usage':" in txt, '副本里找不到 schema_usage 的登记'
        with open(mp, 'w', encoding='utf-8', newline='') as f:
            f.write(txt.replace("'schema_usage':", "'schema_usage_RENAMED_BY_T95':", 1))
        res = M.scan(copy)
        hit = [b for b in res['bad'] if b[3] == 'schema_usage']
        check('teeth① 改掉一条登记 ⇒ 静态扫描**必红**并**点名调用点**',
              bool(hit) and 'cli.py' in hit[0][0],
              '实得 %s' % (hit[:2] or res['bad'][:2]))
        check('teeth② 夹具自证：变异确实落在**副本**里（不是真仓库）',
              bool(hit) and os.path.abspath(mp).startswith(os.path.abspath(copy))
              and not os.path.abspath(mp).startswith(os.path.abspath(ROOT) + os.sep),
              '副本 messages.py = %s' % mp)

        # ⚠ **防无限递归**：teeth⑤ 要跑"副本里的套件"，而副本里的套件又会跑它自己的
        # teeth⑤ ⇒ 套娃（实测：>600s 不结束）。用环境变量让**嵌套那一层**直接返回 ——
        # 嵌套层只需要"rc≠0"这个事实，它的其它断言无关紧要。
        if os.environ.get('MPLTWEAK_MSGKEY_NESTED'):
            _p('  （嵌套层：跳过 teeth⑤/⑥，防无限递归）')
            return
        # 变异②（captain t96 要求）：删掉 `doctor_verdict_headless` 的登记
        # （= t96 修好**之前**的状态）⇒ **副本里的套件自己**必须 rc≠0 且点名
        # launch.py 里那条引用（行号现算，见下）。
        with open(mp, 'w', encoding='utf-8', newline='') as f:
            f.write(txt.replace("'doctor_verdict_headless':",
                                "'doctor_verdict_headless_RENAMED_BY_T95':", 1))
        env = _pin(os.environ)
        env['PYTHONPATH'] = os.path.join(copy, 'src')
        env['MPLTWEAK_LANG'] = 'en'
        env['MPLTWEAK_MSGKEY_NESTED'] = '1'          # 防无限递归（见上）
        pr = subprocess.run([sys.executable,
                             os.path.join(copy, 'tests', 'test_msg_key_audit.py')],
                            cwd=copy, capture_output=True, env=env)
        out = (pr.stdout + pr.stderr).decode('utf-8', 'replace')
        # 注意：套件打印的是**元组 repr**（`('src/mpltweak/launch.py', 640, 't', …)`），
        # 不是 `launch.py:640` 这种拼接串 —— 第一版就按后者找、白找（假红）。
        # ⚠ 行号**现算，不写死**（0.1.14 D1 收工时修的）：原来写死 `'640' in out`，
        # 任何人往 launch.py 前面插行都会把它变成红 —— 本批 C 系加导航注释就把它推到了
        # 674 行，"夹具脆"被读成"产品缺陷"。现按 launch.py 里的实际引用行号拼元组前缀，
        # **强度不降**（仍然要求点名 文件 + 行号 + 键名，且是同一行的元组）。
        with open(os.path.join(ROOT, 'src', 'mpltweak', 'launch.py'),
                  encoding='utf-8') as _f:
            _lns = _f.read().splitlines()
        _want = next((i for i, ln in enumerate(_lns, 1)
                      if 'doctor_verdict_headless' in ln), None)
        assert _want is not None, 'launch.py 里找不到 doctor_verdict_headless 的引用'
        _hit = "'src/mpltweak/launch.py', %d," % _want
        check('teeth⑤ **套件自己**在副本里必红（删掉该键登记 ⇒ 台账已空 ⇒ 真断言咬）',
              pr.returncode != 0 and _hit in out
              and 'doctor_verdict_headless' in out,
              'rc=%s；含 %r=%s / 键名=%s'
              % (pr.returncode, _hit, _hit in out,
                 'doctor_verdict_headless' in out))
        _p('  teeth⑤ 失败原文（片段）：%s'
           % ' ⏐ '.join(ln.strip() for ln in out.splitlines()
                        if 'launch.py' in ln or 'FAILED' in ln)[:300])
        # 夹具自证：**刻意不依赖 `PYTHONPATH` 的优先级** —— 它会随调用方式变
        # （wrapper 注入的 PYTHONPATH、`-P`/`-I` 忽略 PYTHONPATH、editable 的 `.pth`
        # 在 site 阶段追加……）。实测 **en 臂全量里就因此红过一次**：
        # `mpltweak.__file__` 解析到**真实仓库**而不是副本 ⇒ 夹具自证变成时红时绿。
        # 现在用 `sys.path.insert(0, <copy>/src)` 把副本钉成**第一个**候选 ⇒ 确定性；
        # 并把诊断信息一起打出来，万一日后仍失败，一眼分清"夹具环境问题"与"真问题"。
        probe = (
            'import sys, os\n'
            'sys.path.insert(0, %r)\n'
            'import mpltweak\n'
            'sys.stdout.write("FILE=" + mpltweak.__file__ + "\\n")\n'
            'sys.stdout.write("PYTHONPATH=" + str(os.environ.get("PYTHONPATH")) + "\\n")\n'
            'sys.stdout.write("SYSPATH0=" + str(sys.path[:3]) + "\\n")'
            % os.path.join(copy, 'src'))
        pf = subprocess.run([sys.executable, '-c', probe], cwd=copy,
                            capture_output=True, env=env)
        ptxt = pf.stdout.decode('utf-8', 'replace')
        _lines = [ln[5:] for ln in ptxt.splitlines() if ln.startswith('FILE=')]
        pfp = _lines[0].strip() if _lines else ''
        check('teeth⑥ 夹具自证：`mpltweak.__file__` 在**副本**下'
              '（sys.path 显式钉住，不依赖 PYTHONPATH 优先级；'
              '**失败 = 夹具/环境问题，不是产品缺陷**）',
              bool(pfp) and os.path.abspath(pfp).startswith(os.path.abspath(copy)),
              'mpltweak.__file__ = %s ｜ 诊断（PYTHONPATH / sys.path[0:3]）%s'
              % (pfp or '(空)', ptxt.replace('\n', ' ⏐ ')[:200]))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    check('teeth③ 症状判据能咬人（输出里塞一个键名 ⇒ 必红；干净文本 ⇒ 不误报）',
          _key_echoes('xx file_not_found yy', keys) == ['file_not_found']
          and _key_echoes('nothing to see here', keys) == [],
          '实得 %s / %s' % (_key_echoes('xx file_not_found yy', keys),
                            _key_echoes('nothing to see here', keys)))
    # 端到端：真跑一条会走到「未知参数」的路径，确认**没有**键名回显
    d = os.path.join(tempfile.mkdtemp(prefix='mpltweak_msgkey_in_'), 'x.py')
    with open(d, 'w', encoding='utf-8', newline='') as f:
        f.write("import matplotlib\nmatplotlib.use('Agg')\n")
    try:
        rc, txt = run_cli(['doctor', d, '--nope'], 'en', cwd)
        check('teeth④ 端到端：doctor 未知参数路径 rc=2 且输出含 `--nope`、无键名回显',
              rc == 2 and '--nope' in txt and not _key_echoes(txt, keys),
              'rc=%s 回显=%s' % (rc, _key_echoes(txt, keys)))
    finally:
        shutil.rmtree(os.path.dirname(d), ignore_errors=True)


def main():
    _p('---- t95 消息键登记护栏 ----')
    res = M.scan(ROOT)
    st = res['stats']
    _p('  静态扫描：调用点 %d（可解析键 %d / 未解析 %d）'
       % (st['calls'], st['resolved_keys'], st['unresolved']))
    _p('  注册表：MESSAGES %d 键 / UI_ZH_TO_EN %d 键'
       % (st['messages_keys'], st['ui_keys']))

    # ---- A. 静态扫描 ----
    real_bad = [b for b in res['bad'] if (b[0], b[3]) not in KNOWN_UNREGISTERED]
    known_bad = [b for b in res['bad'] if (b[0], b[3]) in KNOWN_UNREGISTERED]
    check('A 静态扫描：`t()` 引用的键**全部已登记**（零漏登记；台账已空 ⇒ **无兜底**）',
          not real_bad, '命中 %d：%s' % (len(real_bad), real_bad[:3]))
    check('A 族前缀检查：`\'前缀\' + 变量` 的前缀在注册表里确有键（防整族漏登记）',
          not res['prefix_bad'], '命中 %d：%s' % (len(res['prefix_bad']),
                                                res['prefix_bad'][:3]))
    check('A 未解析调用点数**只减不增**（当前 %d；新增 = 新的覆盖盲点）'
          % KNOWN_UNRESOLVED_N, st['unresolved'] <= KNOWN_UNRESOLVED_N,
          '实得 %d' % st['unresolved'])

    # ---- D. 三张表的字典字面量**不许出现重复键**（H1 / 0.1.14）----
    # 为什么：`{'k': 'a', 'k': 'a'}` 是合法 Python，**后写的赢** —— 表面看两处都在，
    # 实际只有一处生效；改错一处就静默失效（H1 实测 `UI_ZH_TO_EN` 曾有 3 个键各 3 次、
    # 3 个键各 2 次，只因值恰好相同才没出事）。这条牙扫**字面量节点**，重复即红并点名行号。
    for _name in ('MESSAGES', 'WB_ZH_TO_EN', 'UI_ZH_TO_EN'):
        _dups = _dict_literal_dups(_name)
        check('D 字典字面量 `%s` 里**没有重复键**（重复 = 后写的赢，改一处会静默失效）'
              % _name, not _dups, '重复 %s' % (_dups,))

    # ---- B. 症状级 ----
    keys, _ui = M.load_registry(ROOT)
    d = tempfile.mkdtemp(prefix='mpltweak_msgkey_sym_')
    x = os.path.join(d, 'x.py')
    with open(x, 'w', encoding='utf-8', newline='') as f:
        f.write("import matplotlib\nmatplotlib.use('Agg')\n")
    try:
        for lang in ('zh', 'en'):
            for args in (['doctor', x, '--nope'], ['doctor', '--json'], ['schema']):
                rc, txt = run_cli(args, lang, ROOT)
                echoes = _key_echoes(txt, keys)
                check('B 症状（%s）`%s`：输出里**不出现任何已登记键名**（出现=被回显）'
                      % (lang, ' '.join(args)), not echoes,
                      'rc=%s 回显 %s' % (rc, echoes))
                check('B 症状（%s）`%s`：不含 t93 哨兵 `schema_unknown_args`'
                      % (lang, ' '.join(args)), 'schema_unknown_args' not in txt)
    finally:
        shutil.rmtree(d, ignore_errors=True)

    # ---- C. teeth ----
    _p('')
    _p('---- teeth ----')
    teeth(keys, ROOT)

    # ---- 台账全文打印（不许掩盖）----
    _p('')
    _p('---- 已知漏登记台账 KNOWN_UNREGISTERED（只准缩小；本轮命中 %d 处）----'
       % len(known_bad))
    _p('  ⚠ 台账**不是豁免**：条目不再命中时请删掉（说明修好了）。')
    for (rel, k), why in sorted(KNOWN_UNREGISTERED.items()):
        hit = any(b[0] == rel and b[3] == k for b in known_bad)
        _p('  [%s] %s  %s' % ('命中' if hit else '本轮未命中', rel, k))
        _p('      %s' % why)
    _p('  `t_ui()` 漏翻 %d 处（**只统计不判红**：返回中文原文，不是键名回显）'
       % st['ui_untranslated'])
    _p('  未解析（不假装覆盖）%d 处：' % st['unresolved'])
    for rel, ln, which, expr in res['unresolved']:
        _p('    · %s:%d  %s(%s)' % (rel, ln, which, expr))

    _p('')
    if _fails:
        _p('FAILED: %d 项 -> %s' % (len(_fails), _fails))
        return 1
    _p('ALL PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
