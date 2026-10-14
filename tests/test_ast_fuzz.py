# -*- coding: utf-8 -*-
"""t77 语法化 AST 模糊测试 —— 三条不变量 + 形态矩阵 + 只准缩小的台账。

跑法
----
* **CI smoke（秒级）**：``python tests/test_ast_fuzz.py``（默认 N = 18 = 2×9 形态）
* **发布期大样本**：``set MPLTWEAK_ASTFUZZ_N=1000`` 再跑（耗时自己打印）
* pytest：已登记进 ``tests/test_suites.py`` 的 SUITES（子进程跑，要求 ALL PASS + rc=0）

为什么要有这一套（用户 2026-10-14 批准）
----------------------------------------
现有 fuzz 套件随机的是**参数**（test_fuzz_params）与**手势**（test_fuzz_events），
``test_property_writeback`` 也自述"脚本不随机生成" —— **随机拼代码**这一层是空的。
而今晚的真 bug（``a,b,c,d = axs.ravel()`` 解包名、``fig.subplots(...)`` 误判、
``axis_var_index`` 变量解析）**全在 AST 长尾**。生成器见 ``tools/ast_fuzz_gen.py``
（自造 CFG，seed 可复现，**不依赖 hypothesis** —— CI 的 step 6 按设计不装它）。

三条不变量（本套件的判据）
--------------------------
① **承诺违约 = 0**：改写后 ``ast.parse`` 必须通过；**只允许目标字段变化**（按**行号**
   逐行比，不拿"有变化"当判据）；目标值必须**真的**出现在源码里。
② **未匹配项安全降级**：每例必须落在 {原位写回 ｜ block 通道 ｜ 优雅拒写(0 字节)}
   三者之一。**落在第四种（写坏 / 写错位置 / 写错值）即 FAIL**。
   实测：嵌套解构 / 推导式 / 海象 / 类方法闭包 ⇒ 全部 ``best_effort`` + 0 字节 ⇒ 已通过。
③ **幂等**：第二次 apply 必须 **0 字节改动**。
   ⚠️ **不要**拿 ``reason2 == 'no_change'`` 当通用判据 —— 它只在**首次是成功原位写回**
   （``reason == 'ok'``）时才成立；降级/拒写路径第二次仍是 ``best_effort``，那不是缺陷。

踩过的坑（写给后人）
--------------------
* **``verify=True`` 才是 CLI 的真实默认**。用 ``verify=False`` 跑，**所有**用例都会变成
  ``best_effort`` ⇒ 会被误判成"引擎坏了"。本套件一律 ``verify=True``。
* 幂等判据要按**字节**，不是按 reason（见 ③）。
* 引擎在**进程内**驱动（``writeback.writeback``）；只有 ``describe`` 走子进程（它会执行
  用户脚本）。子进程一律 ``env=_pin(...)``（hygiene 的级联闸门棘轮要求）。
"""

import ast
import io
import json
import os
import shutil
import subprocess
import sys
import contextlib
import tempfile
import time

# ---- 输出兜底（T13 纪律）----
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(errors='replace')
    except (AttributeError, ValueError, OSError):
        pass

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(ROOT, 'tools'))

import ast_fuzz_gen as G                                            # noqa: E402

_fails = []


def _p(s):
    print(s)


def check(name, cond, detail=''):
    if cond:
        _p('OK | %s%s' % (name, (' | %s' % detail) if detail else ''))
    else:
        _fails.append(name)
        _p('FAIL | %s | %s' % (name, detail))


def _pin(env):
    """子进程 env 闸门（hygiene 的级联闸门棘轮按"函数体里有 MPLBACKEND=Agg"识别）。"""
    env = dict(env)
    env['MPLBACKEND'] = 'Agg'
    env['PYTHONIOENCODING'] = 'utf-8'
    env['MPLTWEAK_LANG'] = 'zh'
    env['MPLTWEAK_HOME'] = os.path.join(tempfile.gettempdir(), 'mpltweak_astfuzz_home')
    return env


# ---------------------------------------------------------------------------
# 形态矩阵（**交付物之一**，船长 2026-10-14 要求）：每种对抗形态 → 期望结果
# ---------------------------------------------------------------------------
# 纪律：这张表是"下一轮别再漏一种形态"的清单。'expect' 是**实测记录**，
# 'hard'=True 的才是硬断言（只有对照形态是硬断言：普通脚本必须能原位写回）；
# 其余形态若"变得更好"（本来降级、现在能写）只是**矩阵漂移**，要报告、不算 FAIL。
FORM_MATRIX = {
    'plain':         dict(expect='inplace', hard=True,
                          why='全字面量 ⇒ 必须能原位写回（对照）'),
    'nested_unpack': dict(expect='degrade', hard=False,
                          why='subplots 嵌套解包 ⇒ 轴名静态证不出 ⇒ 应拒写'),
    'tuple_unpack':  dict(expect='degrade', hard=False,
                          why='经典 fig,(ax1,ax2) 解包 ⇒ 同上'),
    'listcomp':      dict(expect='degrade', hard=False,
                          why='列表推导里建轴 ⇒ 无静态轴名'),
    'walrus':        dict(expect='degrade', hard=False,
                          why='海象表达式 ⇒ 目标不是纯字面量'),
    'scope':         dict(expect='degrade', hard=False,
                          why='类方法 / 嵌套函数 / 装饰器 / style.context'),
    'exprs':         dict(expect='degrade', hard=False,
                          why='常量表达式 1e-1 / 5/10 ⇒ 目标非字面量'),
    'dynamic':       dict(expect='degrade', hard=False,
                          why='变量 / 环境变量驱动 ⇒ 无法静态证明'),
    'loop_axes':     dict(expect='degrade', hard=False,
                          why='for 循环里建轴 ⇒ 无静态轴名'),
}

# ---------------------------------------------------------------------------
# 已知缺陷台账（**只准缩小**；与 test_journey_wheel 的 KNOWN_NON_ASCII 同款纪律）
# ---------------------------------------------------------------------------
# 精确条件（船长要求写清）：**源码里的 figsize 不是纯数字字面量**（是变量 / 常量表达式 /
# 环境变量驱动）**且用户只改了轴位置** ⇒ 引擎仍会把 figsize 那一行改写成字面量
# （`figsize=(W, H)` → `figsize=(6, 4)`），并在 `changes` 里**谎报**用户改过 figsize。
# 危害：有损（变量/表达式/动态配置被抹掉，D 例直接改运行期行为）。
# 已立 t88（src/ 的活，core-owner）并**挂进冻结门** ⇒ 台账只是"临时允许存在"，不是延期。
# 修好之后本条目会变成 stale（提示删除）；**台账外任何越界改写一律判红**。
# **2026-10-14 已清空**：唯一那条 `figsize_expr_to_literal` 已由 core-owner 的 **t88** 修好
# （`writeback.edit_figsize()` 在 `_have = _seq_lit_nums(v)` 之后加了守卫：源码 figsize
# **不是两个字面量数字** ⇒ **直接返回未编辑**，绝不替换成字面量；`changes` 也不再谎报）。
# ⇒ 按"只准缩小"删掉。从这一刻起，下面的 figsize 断言**没有台账兜底**，是**真·硬断言**。
# 两次缩小记录：① 本条目（t88 修）；② 从未进台账的 `t_ui` 类问题不属本套件。
KNOWN_OVERWRITE = {}


# ---------------------------------------------------------------------------
# 分类：把"改了什么"精确到行
# ---------------------------------------------------------------------------
def attribute_changes(before, after, meta):
    """把"改了什么"**按内容**归因（不按行号身份）—— → (outcome, info)。

    为什么要按内容：``fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(W, 4))`` 这行上
    **figsize 和轴创建在同一行**。若按"这行变了就算 pos 写了"，会把 figsize 被改写成
    字面量**误判成**"pos 写成功"（t77 第一轮实测踩到：seed=10/11）。
    判据改成：这一行的新内容里**真的出现了用户要的目标值** ⇒ 才算 pos 写。

    outcome ∈ refuse（0 字节）| block（纯追加）| inplace（写了目标值）|
              figsize_only（只改了 figsize）| other（第四种 ⇒ 调用方判红）
    """
    bl, al = before.splitlines(), after.splitlines()
    empty = {'changed_lines': [], 'pos_lines': [], 'figsize_lines': [], 'other_lines': []}
    if bl == al:
        return 'refuse', dict(empty)
    if len(al) > len(bl) and al[:len(bl)] == bl:
        info = dict(empty)
        info['appended'] = len(al) - len(bl)
        return 'block', info
    if len(al) != len(bl):
        info = dict(empty)
        info.update(before_n=len(bl), after_n=len(al))
        return 'other', info

    diffs = [i + 1 for i in range(len(bl)) if bl[i] != al[i]]
    info = dict(empty)
    info['changed_lines'] = diffs
    if not diffs:
        return 'refuse', info
    for n in diffs:
        line = al[n - 1]
        if all(('%.2f' % v) in line or str(v) in line for v in G.TARGET_POS):
            info['pos_lines'].append(n)          # 目标值真的出现在这一行
        elif 'figsize=' in line:
            info['figsize_lines'].append(n)
        else:
            info['other_lines'].append(n)
    if info['pos_lines']:
        outcome = 'inplace'
    elif info['figsize_lines'] and not info['other_lines']:
        outcome = 'figsize_only'
    else:
        outcome = 'other'
    return outcome, info


def _line_of(text, n):
    ls = text.splitlines()
    return ls[n - 1] if n and 1 <= n <= len(ls) else ''


# ---------------------------------------------------------------------------
# 一例的执行
# ---------------------------------------------------------------------------
def run_case(meta, workdir, semantic=False):
    """→ dict：结果 + 三条不变量的判定明细。"""
    from mpltweak import writeback
    d = os.path.join(workdir, 'seed%04d_%s' % (meta['seed'], meta['form']))
    os.makedirs(d, exist_ok=True)
    sp = os.path.join(d, 'fig.py')
    with open(sp, 'w', encoding='utf-8', newline='') as f:
        f.write(meta['src'])
    pp = os.path.join(d, 'fig.json')
    e = _pin(os.environ)
    r = subprocess.run([sys.executable, '-m', 'mpltweak', 'describe', sp, '-o', pp],
                       cwd=d, capture_output=True, env=e)
    if r.returncode != 0 or not os.path.exists(pp):
        return {'skip': 'describe rc=%s %s' % (r.returncode,
                                               r.stderr.decode('utf-8', 'replace')[:80])}
    data = json.load(open(pp, encoding='utf-8'))
    if not data.get('axes'):
        return {'skip': 'describe 没给出 axes'}
    # 用户只改轴位置（**一个 figsize 数字都不动**）
    data['axes'][0]['pos'] = list(G.TARGET_POS)
    json.dump(data, open(pp, 'w', encoding='utf-8'), ensure_ascii=False)

    cap = io.StringIO()
    with contextlib.redirect_stdout(cap), contextlib.redirect_stderr(cap):
        res = writeback.writeback(sp, data, pp, verify=True, style='inplace')
    if res.get('reason') not in ('ok', 'best_effort', 'no_change'):
        with contextlib.redirect_stdout(cap), contextlib.redirect_stderr(cap):
            res = writeback.writeback(sp, data, pp, verify=True, style='block',
                                      backup=res.get('backup'))
    after1 = open(sp, encoding='utf-8', newline='').read()
    outcome, info = attribute_changes(meta['src'], after1, meta)

    # ① 语法守恒
    try:
        ast.parse(after1)
        syn = True
    except (SyntaxError, ValueError):
        syn = False

    # ② 只动目标 / 安全降级（按内容归因，见 attribute_changes 的注释）
    pos_written = bool(info['pos_lines'])
    fs_written = bool(info['figsize_lines'])
    other_written = list(info['other_lines'])
    tgt_line = meta.get('target_line')
    tgt_line_after = _line_of(after1, tgt_line)
    tgt_value_ok = all(('%.2f' % v) in tgt_line_after or str(v) in tgt_line_after
                       for v in G.TARGET_POS)

    # ③ 幂等：第二次必须 0 字节改动
    with contextlib.redirect_stdout(cap), contextlib.redirect_stderr(cap):
        res2 = writeback.writeback(sp, data, pp, verify=True, style='inplace')
    after2 = open(sp, encoding='utf-8', newline='').read()
    idem = (after2 == after1)

    # 语义抽检：写回后真跑脚本，看目标值是否真落到图上
    sem_ok, sem_detail = None, ''
    if semantic and pos_written:
        sem_ok, sem_detail = _semantic_probe(sp, d)

    return {
        'seed': meta['seed'], 'form': meta['form'], 'outcome': outcome,
        'reason': res.get('reason'), 'style': res.get('style'),
        'syntax': syn, 'figsize_written': fs_written, 'pos_written': pos_written,
        'claims_pos': 'ax0.pos' in (res.get('changes') or []),
        'other_written': other_written, 'target_value_ok': tgt_value_ok,
        'idempotent': idem, 'reason2': res2.get('reason'),
        'figsize_literal': meta['figsize_literal'], 'pos_literal': meta['pos_literal'],
        'sem_ok': sem_ok, 'sem_detail': sem_detail,
        'after': after1, 'before': meta['src'], 'dir': d,
    }


def _semantic_probe(script, cwd):
    """运行期真值验证：真跑脚本，看目标轴的实际位置是否等于用户要的值。"""
    probe = (
        "import runpy, sys, json\n"
        "import matplotlib\nmatplotlib.use('Agg')\n"
        "import matplotlib.pyplot as plt\n"
        "runpy.run_path(sys.argv[1], run_name='__main__')\n"
        "out = []\n"
        "for num in plt.get_fignums():\n"
        "    fig = plt.figure(num)\n"
        "    out.append([list(a.get_position().bounds) for a in fig.axes])\n"
        "print('POSJSON=' + json.dumps(out))\n")
    pf = os.path.join(cwd, '_sem_probe.py')
    with open(pf, 'w', encoding='utf-8', newline='') as f:
        f.write(probe)
    e = _pin(os.environ)
    e['MPLTWEAK_ASTFUZZ_W'] = '6'
    r = subprocess.run([sys.executable, pf, script], cwd=cwd,
                       capture_output=True, env=e)
    out = r.stdout.decode('utf-8', 'replace')
    for ln in out.splitlines():
        if ln.startswith('POSJSON='):
            try:
                figs = json.loads(ln[len('POSJSON='):])
            except ValueError:
                return False, 'POSJSON 解不出'
            want = [round(v, 6) for v in G.TARGET_POS]
            for fig in figs:
                for bounds in fig:
                    if [round(v, 6) for v in bounds] == want:
                        return True, '目标值真的落到图上'
            return False, '跑完没有哪个轴的位置 == 用户要的值 %s' % (want,)
    return False, '探针没打印 POSJSON（rc=%s）' % r.returncode


def _load_corpus():
    """读固定 corpus（seed + 实测期望）。脚本不存盘：seed 就是最小化样本，
    ``tools/ast_fuzz_gen.py`` 能从 seed **确定性重建**同一份脚本。"""
    p = os.path.join(_HERE, 'astfuzz_corpus', 'corpus.json')
    if not os.path.exists(p):
        return []
    try:
        with open(p, encoding='utf-8') as f:
            return json.load(f).get('entries', [])
    except (ValueError, OSError):
        return []


# ---------------------------------------------------------------------------
# teeth：把"安全降级"换成"勉强替换" ⇒ 不变性断言**必须红**
# ---------------------------------------------------------------------------
def teeth(workdir):
    """反向验证两件事：

    A. **判据本身有牙**（合成输入，确定性）：把一例"安全降级"分别改成
       ① 勉强替换（写了**错的值**）② 写坏语法 ③ 顺手改第三个字段 ⇒
       对应的判据必须各自报 False。
    B. **端到端**：真的把引擎 monkeypatch 成"勉强替换"，跑一例真实用例 ⇒
       "承诺=事实"与"安全降级"必须报 False。
    """
    from mpltweak import writeback
    out = []

    meta = G.gen(0)
    before = meta['src']
    ls = before.splitlines()
    ti = (meta['target_line'] or 1) - 1

    # ① 勉强替换：写了错的值（不是用户要的）
    bad1 = list(ls)
    bad1[ti] = 'ax = fig.add_axes([0.99, 0.99, 0.99, 0.99])'
    oc1, inf1 = attribute_changes(before, '\n'.join(bad1) + '\n', meta)
    out.append(('勉强替换（写错值）⇒ 判成 other/越界', oc1 == 'other'
                and not inf1['pos_lines']))

    # ② 写坏语法
    bad2 = '\n'.join(ls) + '\n' + 'def broken(:\n'
    try:
        ast.parse(bad2)
        syn2 = True
    except (SyntaxError, ValueError):
        syn2 = False
    out.append(('写坏语法 ⇒ ast.parse 必须失败', not syn2))

    # ③ 顺手改第三个字段
    bad3 = list(ls)
    for i, ln in enumerate(bad3):
        if ln.startswith('ax.set_title'):
            bad3[i] = "ax.set_title('MUTATED', fontsize=99)"
            break
    oc3, inf3 = attribute_changes(before, '\n'.join(bad3) + '\n', meta)
    out.append(('顺手改第三个字段 ⇒ other_written 必须非空',
                bool(inf3['other_lines'])))

    # ④ 端到端：monkeypatch 成"勉强替换"
    real = writeback.writeback
    meta2 = G.gen(0)

    def fake_forced(script, data, pp, **kw):
        with open(script, encoding='utf-8', newline='') as f:
            t = f.read()
        lns = t.splitlines()
        for i, ln in enumerate(lns):
            if 'add_axes' in ln:
                lns[i] = 'ax = fig.add_axes([0.99, 0.99, 0.99, 0.99])'
                break
        with open(script, 'w', encoding='utf-8', newline='') as f:
            f.write('\n'.join(lns) + '\n')
        return {'reason': 'ok', 'style': 'inplace', 'applied': True,
                'changes': ['ax0.pos'], 'verified': True}

    writeback.writeback = fake_forced
    try:
        r = run_case(meta2, workdir, semantic=False)
    finally:
        writeback.writeback = real
    out.append(('端到端：勉强替换 ⇒ 承诺=事实 必须报 False',
                r.get('claims_pos') is True and r.get('pos_written') is False))
    out.append(('端到端：勉强替换 ⇒ 安全降级判据必须报 False',
                r.get('outcome') == 'other'))
    return out


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def main():
    t0 = time.time()
    n = int(os.environ.get('MPLTWEAK_ASTFUZZ_N', '9'))
    _nums = [a for a in sys.argv[1:] if a.isdigit()]
    if _nums:
        n = int(_nums[0])
    corpus_only = '--corpus' in sys.argv
    # --offset K：从 seed=K 开始（分片跑大样本用 —— 4 片 × 250 = 1000，
    # 并行 4 个进程把发布期大样本从 ~60 分钟压到 ~15 分钟）
    offset = 0
    if '--offset' in sys.argv:
        offset = int(sys.argv[sys.argv.index('--offset') + 1])
    sem_every = int(os.environ.get('MPLTWEAK_ASTFUZZ_SEM', '10'))
    _p('---- t77 语法化 AST 模糊测试：N=%d（每 %d 例抽 1 例做运行期真值验证）----'
       % (n, sem_every))
    workdir = tempfile.mkdtemp(prefix='mpltweak_astfuzz_')
    rows = []
    try:
        metas = ([G.gen(e['seed']) for e in _load_corpus()] if corpus_only
                 else G.gen_many(n, base_seed=offset))
        if offset:
            _p('分片：seed %d .. %d' % (offset, offset + len(metas) - 1))
        for i, m in enumerate(metas):
            sem = (i % sem_every == 0)
            r = run_case(m, workdir, semantic=sem)
            if r.get('skip'):
                check('seed=%d form=%s 能跑起来' % (m['seed'], m['form']), False,
                      r['skip'])
                continue
            rows.append(r)

        _p('')
        _p('---- 三条不变量（逐例）----')
        bad_syn = [r for r in rows if not r['syntax']]
        check('① 承诺违约=0：改写后 ast.parse 全部通过（%d 例）' % len(rows),
              not bad_syn, '坏语法 %d 例：%s' % (len(bad_syn),
                                             [(r['seed'], r['form']) for r in bad_syn][:3]))
        bad_other = [r for r in rows if r['other_written']]
        check('② 只动目标：没有**第三个**字段被顺手改掉',
              not bad_other, '越界 %d 例：%s' % (len(bad_other),
                                            [(r['seed'], r['form'], r['other_written'])
                                             for r in bad_other][:3]))
        # 承诺与事实必须一致：changes 里说改过 pos，源码里就必须真有目标值（反之亦然）。
        # 这比"目标值在不在"更硬 —— 它抓的是**谎报**（figsize 被改写时 changes 会谎报
        # figsize，同理若谎报 pos 也必须被抓）。
        bad_promise = [r for r in rows if r['claims_pos'] != r['pos_written']]
        check('② 承诺=事实：changes 声称改过的字段必须真改了，反之亦然',
              not bad_promise, '不符 %d 例：%s' % (len(bad_promise),
                                               [(r['seed'], r['form'], r['claims_pos'],
                                                 r['pos_written']) for r in bad_promise][:3]))
        # 对照形态：**已知缺陷不适用**（figsize 是字面量）**且目标可写**（pos 是字面量）
        # 时，普通脚本必须能原位写回。
        # ⚠️ `pos_literal` 这个条件是必须的 —— 第一版漏了它，在 N=250 规模下 4 片各报
        # "对照 7 例、失败 4/4/3/3"；实测这 3~4 例**正是 pos 非字面量**的那些（7-4=3、
        # 7-3=4 逐片吻合）⇒ 它们**本来就写不了**（属"安全降级"，另一条断言已经覆盖），
        # 把它们算进对照是**断言写宽了**，不是引擎的问题。
        plain_ctrl = [r for r in rows
                      if r['form'] == 'plain' and r['figsize_literal']
                      and r['pos_literal']]
        plain_bad = [r for r in plain_ctrl if not r['pos_written']]
        check('对照形态（plain + figsize 与 pos 都是字面量 ⇒ 缺陷不适用且目标可写）'
              '必须能原位写回',
              bool(plain_ctrl) and not plain_bad,
              '对照 %d 例、失败 %d' % (len(plain_ctrl), len(plain_bad)))
        bad_out = [r for r in rows if r['outcome'] == 'other']
        check('② 安全降级：每例都落在 {原位写回 ｜ block ｜ 拒写} 三者之一',
              not bad_out, '第四种 %d 例：%s' % (len(bad_out),
                                             [(r['seed'], r['form']) for r in bad_out][:3]))
        bad_idem = [r for r in rows if not r['idempotent']]
        check('③ 幂等：第二次 apply 必须 0 字节改动（%d 例）' % len(rows),
              not bad_idem, '不幂等 %d 例：%s' % (len(bad_idem),
                                              [(r['seed'], r['form']) for r in bad_idem][:3]))

        # ---- figsize 越界改写（t88 修好后：**零例外**，无台账兜底）----
        # `figsize_written` 只看"**同一行内**出现 figsize= 的改写"（见 attribute_changes）：
        # **block 通道是纯追加**（`info['appended']`、`figsize_lines` 为空）⇒ **天然排除**，
        # 不会被误算成越界改写 —— 这点 core-owner 特意问过，代码层面就是这样。
        fs_over = [r for r in rows if r['figsize_written']]
        check('② figsize **一律不得被越界改写**（t88 修好后零例外；block 追加不算）',
              not fs_over,
              '命中 %d 例：%s' % (len(fs_over),
                                [(r['seed'], r['form'], r['outcome'])
                                 for r in fs_over][:3]))
        _p('  （台账 KNOWN_OVERWRITE 已随 t88 **清空** ⇒ 上面这条是真·硬断言；'
           '本回合 figsize 越界改写 %d 例）' % len(fs_over))

        # ---- 形态矩阵：覆盖 + 漂移 ----
        _p('')
        _p('---- 形态矩阵（每种对抗形态 → 实际结果；**这是交付物之一**）----')
        seen = {}
        for r in rows:
            seen.setdefault(r['form'], []).append(r)
        missing = [f for f in G.FORMS if f not in seen]
        check('形态覆盖：每个对抗形态都至少跑到 1 例（%d 种）' % len(G.FORMS),
              not missing, '没跑到：%s' % missing)
        # (形态 × figsize 写法) 的**格子覆盖**：N ≥ 36 时必须每格都跑到 —— 这是
        # "形态枚举"交付物的骨架，防"某种写法一次没抽到 ⇒ 伪装成没问题"。
        pairs = set((r['form'], r['figsize_literal']) for r in rows)
        _p('  格子覆盖：%d/%d 个 (形态 × figsize写法) 格子跑到'
           % (len(pairs), len(G.FORMS) * 2))
        if n >= G.COVERAGE_N:
            want = set((f, lit) for f in G.FORMS for lit in (True, False))
            check('格子覆盖完整（N=%d ≥ %d ⇒ 每个 (形态 × figsize写法) 都必须跑到）'
                  % (n, G.COVERAGE_N), want <= pairs,
                  '缺 %s' % sorted(want - pairs)[:5])
        _p('  %-15s %-9s %-9s %-8s %-8s %-8s %s'
           % ('形态', '期望', '实际', 'pos写?', 'figsize写?', '幂等', '备注'))
        drift = []
        for f in G.FORMS:
            rs = seen.get(f, [])
            if not rs:
                continue
            acts = sorted(set(r['outcome'] for r in rs))
            act = acts[0] if len(acts) == 1 else '/'.join(acts)
            exp = FORM_MATRIX[f]['expect']
            posw = any(r['pos_written'] for r in rs)
            fsw = any(r['figsize_written'] for r in rs)
            idem = all(r['idempotent'] for r in rs)
            note = ''
            if exp != act and not (exp == 'inplace' and 'inplace' in acts):
                note = '← 矩阵漂移'
                drift.append(f)
            _p('  %-15s %-9s %-9s %-8s %-8s %-8s %s'
               % (f, exp, act, posw, fsw, idem, note))
        # 漂移**只报告、不判红**：一个"本来降级、现在能写"的变化是**变好**，不是 bug。
        # 但必须显式看见 —— 这就是"形态枚举"作为交付物的意义（下一轮别又只验一种形态）。
        _p('  矩阵漂移（只报告，不判红）：%s' % (drift or '无'))
        if drift:
            _p('    ↳ 漂移不是缺陷：说明该形态的结果**变了**。若变得更好（能写了），'
               '请把 FORM_MATRIX 的 expect 更新成实测值；若变差（写坏/写错），'
               '上面的硬断言已经会红。')

        # ---- 语义抽检 ----
        _p('')
        sem_rows = [r for r in rows if r['sem_ok'] is not None]
        bad_sem = [r for r in sem_rows if not r['sem_ok']]
        check('语义抽检（运行期真值）：抽到的 %d 例，写回后真跑脚本、目标值确实落到图上'
              % len(sem_rows), not bad_sem,
              '失败 %d：%s' % (len(bad_sem), [(r['seed'], r['sem_detail'])
                                           for r in bad_sem][:2]))
        _p('  抽检比例：每 %d 例抽 1 例（≈%.0f%%）—— 理由：运行期验证要真跑脚本'
           '（每例 ~0.4s），全量跑会让发布期大样本从分钟级变成小时级；'
           '静态断言覆盖全部 %d 例。' % (sem_every, 100.0 / sem_every, len(rows)))

        # ---- 固定 corpus：钉住的 seed + 实测期望（可复现）----
        corpus = _load_corpus()
        if corpus:
            _p('')
            _p('---- 固定 corpus（tests/astfuzz_corpus/corpus.json）：%d 条 ----'
               % len(corpus))
            by_seed = dict((r['seed'], r) for r in rows)
            hit = agree = 0
            for e in corpus:
                r = by_seed.get(e['seed'])
                if r is None:
                    _p('  · %-28s seed=%-4d 未在本轮样本内（N≥%d 或 --corpus 可复现）'
                       % (e['name'], e['seed'], e['seed'] + 1))
                    continue
                hit += 1
                if e['expect'] == 'inplace_nofs':
                    # **t88 之后的收紧型期望**：pos 落地 **且** figsize **一字未动**
                    # （旧行为是"pos 落地 + figsize 被改写成字面量"）。台账删掉后就该有这条。
                    same = (r['outcome'] == 'inplace' and not r['figsize_written'])
                else:
                    same = (r['outcome'] == e['expect'])
                agree += 1 if same else 0
                if e.get('hard'):
                    check('corpus %s（硬断言）必须 %s' % (e['name'], e['expect']),
                          same, '实际 %s' % r['outcome'])
                else:
                    _p('  · %-28s seed=%-4d 期望=%-12s 实际=%-12s %s'
                       % (e['name'], e['seed'], e['expect'], r['outcome'],
                          '' if same else '← 漂移（只报告，不算 FAIL）'))
            _p('  本轮命中 %d 条 / 与实测一致 %d 条' % (hit, agree))

        # ---- teeth：把"安全降级"换成"勉强替换" ⇒ 判据必须红 ----
        _p('')
        _p('---- teeth：把「安全降级」换成「勉强替换」⇒ 判据必须红 ----')
        for name, ok in teeth(workdir):
            check('teeth %s' % name, ok)

        # ---- 台账全文（船长要求：必须打印，不许藏在代码里）----
        _p('')
        _p('---- 已知缺陷台账 KNOWN_OVERWRITE（只准缩小；本回合命中 %d 例）----'
           % len(fs_over))
        for k, v in sorted(KNOWN_OVERWRITE.items()):
            _p('  · %s：%s' % (k, v))
        if not KNOWN_OVERWRITE:
            _p('  （空 —— 台账已清空）')
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
        _p('临时工作区已清理：%s（仍存在=%s）' % (workdir, os.path.exists(workdir)))

    _p('')
    _p('耗时 %.1fs（N=%d）' % (time.time() - t0, n))
    if _fails:
        _p('FAILED: %d 项 -> %s' % (len(_fails), _fails))
        return 1
    _p('ALL PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
