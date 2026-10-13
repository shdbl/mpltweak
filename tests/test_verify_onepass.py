# -*- coding: utf-8 -*-
"""t39：写回验证「默认只跑一遍」的回归网（单遍 + savefig 快照 + fail-closed 回退）。

为什么需要这套
--------------
到 0.1.10 为止，`apply --write` 的 happy path 要跑**两遍**脚本：

  1. ``writeback.verify_run``  —— 原样 ``python 脚本.py``，只看退出码；
  2. ``verify.verify_semantic`` —— 套壳 ``python -m mpltweak.verify ...``，为了在脚本
     跑完之后还能拿到 figure 对象做逐字段比对（第 2 遍同时是**最后写盘的人**）。

第 2 遍为了取数**必须改脚本行为**（``_collect_session`` 把 ``plt.close`` 空操作化）——
阶段 1 用双向实验（B′==A、B″==B）证明根因唯一就是那一行：脚本写成
``fig.savefig()`` → ``plt.close(fig)`` → 再裸 ``plot()``（不新建 figure）时，图没被销毁、
仍是 current ⇒ 后面的绘制**画到上一张图上**，第 2 次 savefig 把被污染的画布存出去。
真实链路里套壳那遍最后写盘 ⇒ 用户目录里的 PNG 被**静默改坏**（样本 1/144）。

修复后的形态（本套件钉的就是它）：

  * 取数改成两件**只读**的东西：``Figure.savefig`` 快照钩子（写盘那一刻的状态）
    + 创建顺序 figure 注册表（钩 pyplot 建图的唯一漏斗 ``plt.figure``、持强引用，
    脚本自己 close 过也读得到）；
  * ``plt.close`` 不再被拦 ⇒ 脚本行为与 ``python 脚本.py`` 一致；
  * 一次跑同时给出"还跑得通吗"与"改动真生效了吗"；取不到状态 ⇒ **fail-closed**
    回退两遍并把原因写进 warnings，**绝不**静默判"通过"；
  * 判据**只严不松**：主判据仍是"脚本跑完时图对象的状态"（旧 ``verify_semantic`` 口径，
    一个字没改），savefig 快照是**追加**的严格检查 —— 这次改动必须也出现在磁盘上
    那张图里（旧口径会放过"改动只落在最后一次 savefig 之后"的情况，那时图一个像素没变）。

四条被钉住的不变量（一条都不许放宽）
----------------------------------
  A 单遍      ：happy path 只跑 1 遍脚本（``res['verify_passes'] == 1``），
                主口径 = 图对象当前状态 + （有 savefig 时）磁盘上那张图的快照
                （``res['semantic_basis'] == 'live+savefig'``）。
  B 产物对账  ：``apply --write`` 成功后，用户目录里的图片与"把写回后的脚本原样跑一遍"
                的产出**逐字节一致**（机制①的直接回归网）。
  C 覆盖不降  ：① 脚本跑不通 → 必须失败 + 回滚；② 改动没生效（图状态与参数不符，
                含"磁盘上那张图没变"的追加判据）→ 必须失败 + 回滚。
  D fail-closed：取不到状态 → 回退两遍（``verify_passes == 2``）+ warnings 里有原因，
                语义层如实记 None。

teeth（怎么证明这套用例会红 —— 见 `tools/teeth_onepass.py`）
----------------------------------------------------------
把当前 ``src/mpltweak`` 复制到临时树、做**单点变异**后再用 ``PYTHONPATH=<变异树>``
跑本套件，要求对应用例**必红**：

  * M1 还原 close 拦截（``plt.close = lambda *a, **k: None``）→ **B 必须红**
  * M2 让 ``verify.verify_once`` 永远返回 ``status='ok'/verified=True`` → **C① 必须红**
  * M3 让 ``verify._compare_state`` 永远返回空 bad               → **C② 必须红**

用法：
    python tests/test_verify_onepass.py          # 直接跑（零窗口，MPLBACKEND=Agg）
    pytest -q -k test_verify_onepass           # 走 tests/test_suites.py 的包装
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile

# ---- 输出兜底（T13 纪律）：GBK 控制台 / 管道下 print 中文不崩 ----
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(errors='replace')
    except (AttributeError, ValueError, OSError):
        pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, 'src')


def _has_mpltweak(parts):
    """这些路径里有没有可 import 的 mpltweak。"""
    return any(os.path.isdir(os.path.join(p, 'mpltweak')) for p in parts if p)


# 只有在**没有**别处提供 mpltweak 时才把仓库 src 顶上去 —— 这样
# `tools/teeth_onepass.py` 能用 PYTHONPATH=<变异树> 把本套件跑在变异代码上
# （否则本套件会强行把自己钉回仓库 src，teeth 就永远测不到东西）。
if not _has_mpltweak(sys.path):
    sys.path.insert(0, SRC)

# 再把 src 放进**本进程的 os.environ['PYTHONPATH']**（不只是 sys.path）—— t40 复核 A2：
# 本套件有 3 处是**在进程内直接调产品函数**（`V.verify_once(...)`，见 [A]/[A2]/[D]），
# 而产品那几个入口用 `dict(os.environ)` 起子进程、**不补 PYTHONPATH**。只塞 sys.path
# 的话，子进程 `python -m mpltweak.verify` 会 ModuleNotFoundError ⇒ 单遍取不到状态
# ⇒ fail-closed 回退两遍 ⇒ 本套件出 5 条**假红**（实测：无 PYTHONPATH 时 FAILED:5；
# 把本块临时停用则 FAILED:12）。开发检出（未做 editable 安装、又没给 PYTHONPATH）
# 正是这种情况；CI 有 `pip install -e .` 所以看不到。有此一行即两种条件全绿。
_pp = [p for p in (os.environ.get('PYTHONPATH') or '').split(os.pathsep) if p]
if SRC not in _pp:
    _pp.insert(0, SRC)
os.environ['PYTHONPATH'] = os.pathsep.join(_pp)

os.environ.setdefault('MPLBACKEND', 'Agg')
os.environ.setdefault('MPLTWEAK_LANG', 'zh')

fail = 0


def check(name, cond, detail=''):
    global fail
    print(('  OK   ' if cond else '  FAIL ') + name
          + ((' | ' + str(detail)[:400]) if detail else ''))
    if not cond:
        fail += 1


# ---------------------------------------------------------------------------
# 用例脚本（固定写法，不随机生成 —— 验证层重跑要有确定结论）
# ---------------------------------------------------------------------------
# 机制①：savefig → plt.close(fig) → 裸 plot（原样跑会新建第 2 张图）
CASE_CLOSE_THEN_PLOT = '''# -*- coding: utf-8 -*-
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

fig = plt.figure(figsize=(6, 4))
ax = fig.add_subplot(111)
ax.plot([1, 2, 3], [4, 5, 6])
ax.set_title("first", fontsize=12)
fig.savefig("out_a.png", dpi=100)
plt.close(fig)

ax2 = plt.gca()          # 原样跑：这里新建第 2 张图（套壳拦 close 时会画回旧图）
ax2.plot([3, 2, 1], [1, 2, 3])
plt.savefig("out_b.png", dpi=100)
'''

# C①：脚本自己跑不通（写回后也无头跑不通 → 必须回滚，不许静默留下）
CASE_SCRIPT_FAILS = '''# -*- coding: utf-8 -*-
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

fig, ax = plt.subplots(figsize=(6, 4))
ax.plot([1, 2, 3], [4, 5, 6])
ax.set_title("t", fontsize=12)
fig.savefig("t3.png", dpi=100)
raise RuntimeError("boom: 这份脚本本来就跑不通")
'''

# C②：改动没生效 —— 同一属性被后面一句覆盖（原位只会改最早的那条调用）
CASE_NO_EFFECT = '''# -*- coding: utf-8 -*-
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

fig, ax = plt.subplots(figsize=(6, 4))
ax.plot([1, 2, 3], [4, 5, 6])
ax.set_title("t", fontsize=12)
ax.set_title("t", fontsize=99)      # 覆盖掉上一句 → 参数里的 21 永远不生效
fig.savefig("t4.png", dpi=100)
'''

# D：没有留下任何图（既没 savefig、也没有活着的图）→ 取不到状态
CASE_NO_FIGURE = '''# -*- coding: utf-8 -*-
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

print("no figure at all")
'''

# A/快照口径：savefig 之后才改的字段 —— 快照取"写盘那一刻"，结束状态是另一个值
CASE_SAVE_THEN_EDIT = '''# -*- coding: utf-8 -*-
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

fig, ax = plt.subplots(figsize=(6, 4))
ax.plot([1, 2, 3], [4, 5, 6])
ax.set_title("t", fontsize=12)
fig.savefig("t6.png", dpi=100)
ax.set_title("t", fontsize=99)      # 磁盘上那张图里没有这一句的效果
'''

_TMPROOT = tempfile.mkdtemp(prefix='mpltweak_t39_onepass_')


def _mkcase(name, src):
    d = os.path.join(_TMPROOT, name)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, 'g.py'), 'w', encoding='utf-8') as f:
        f.write(src)
    return d


def _env():
    """子进程环境闸门：硬钉无头后端，并保证 src 在 PYTHONPATH 上。

    形状是 hygiene 棘轮认得的"本地闸门函数"：函数体里显式写
    ``env['MPLBACKEND'] = 'Agg'``（**硬钉**，不用 setdefault —— 宿主把后端投毒成
    Qt5Agg/TkAgg 时 setdefault 会原样保留 → 子进程真开窗）。

    若调用方的 PYTHONPATH 里**已经有** mpltweak（teeth 的变异树就是这种情形），
    就原样沿用它、不再插仓库 src —— 否则变异永远到不了子进程。
    """
    parts = [p for p in (os.environ.get('PYTHONPATH') or '').split(os.pathsep)
             if p]
    if not _has_mpltweak(parts):
        parts.insert(0, SRC)
    env = dict(os.environ)
    env['MPLBACKEND'] = 'Agg'
    env['MPLTWEAK_LANG'] = 'zh'
    env['PYTHONIOENCODING'] = 'utf-8'
    env['PYTHONPATH'] = os.pathsep.join(parts)
    return env


def _run(cmd, cwd):
    return subprocess.run(cmd, cwd=cwd, env=_env(), capture_output=True,
                          timeout=300)


def _py():
    return sys.executable


def _pngs(d):
    import hashlib
    out = {}
    for root, _dirs, names in os.walk(d):
        for n in sorted(names):
            if n.endswith('.png'):
                p = os.path.join(root, n)
                with open(p, 'rb') as f:
                    out[os.path.relpath(p, d).replace('\\', '/')] = \
                        hashlib.sha256(f.read()).hexdigest()
    return out


def _plain_run(src_dir, tag):
    """把 src_dir/g.py 复制到干净目录原样跑一遍，返回 {png: sha256}。"""
    d = os.path.join(_TMPROOT, tag)
    shutil.rmtree(d, ignore_errors=True)
    os.makedirs(d)
    shutil.copy2(os.path.join(src_dir, 'g.py'), os.path.join(d, 'g.py'))
    r = _run([_py(), 'g.py'], d)
    check('原样跑《%s》能跑通' % tag, r.returncode == 0,
          r.stderr.decode('utf-8', 'replace')[-300:])
    return _pngs(d)


def _apply_json(d, extra=()):
    r = _run([_py(), '-m', 'mpltweak', 'apply', 'g.py', '--params', 'p.json',
              '--write', '--json'] + list(extra), d)
    out = r.stdout.decode('utf-8', 'replace')
    try:
        res = json.loads(out[out.index('{'):out.rindex('}') + 1])
    except Exception:                                    # noqa: BLE001
        return r, {}, out[-600:] + r.stderr.decode('utf-8', 'replace')[-600:]
    return r, ((res.get('files') or [{}])[0]), ''


def _write_params(d, fig_index=0, title=21.0, figsize=(6.0, 4.0)):
    """只请求"标题字号"这一项（干净请求：不留 pending，判据才看得清）。"""
    data = {'version': 4, 'script': 'g.py', 'fig_index': fig_index,
            'n_figs': 1, 'figsize_in': list(figsize),
            'axes': [{'index': 0, 'title_fontsize': title}]}
    with open(os.path.join(d, 'p.json'), 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False)
    return data


# ---------------------------------------------------------------------------
# B + A：happy path 单遍，且用户目录的产物 == "写回后的脚本原样跑"
# ---------------------------------------------------------------------------
def case_single_pass_parity():
    print('\n[B] apply --write 后用户目录图片 == 写回后脚本原样跑的产出（机制①）')
    d = _mkcase('b_parity', CASE_CLOSE_THEN_PLOT)
    _plain_run(d, 'b_parity_plain_before')            # 先确认脚本本身能跑
    _write_params(d)
    r, res, raw = _apply_json(d, extra=['--style', 'inplace'])
    check('B apply 成功落地（applied=True）', res.get('applied') is True,
          'rc=%s reason=%s err=%s %s' % (r.returncode, res.get('reason'),
                                         res.get('err'), raw))
    check('B 语义层判过（semantic=True）', res.get('semantic') is True,
          res.get('err') or raw)
    after = _pngs(d)
    plain = _plain_run(d, 'b_parity_plain_after')     # 写回后的脚本，原样跑
    diff = {k: (plain.get(k) != v) for k, v in after.items()}
    check('B 用户目录里的每张图都与原样跑逐字节一致',
          after and not any(diff.values()),
          'user=%s plain=%s diff=%s' % (after, plain, diff))


def case_verify_passes_is_one():
    print('\n[A] happy path 只跑 1 遍脚本；状态取自 savefig 快照')
    from mpltweak import writeback as W
    d = _mkcase('a_onepass', CASE_CLOSE_THEN_PLOT)
    data = _write_params(d)
    script = os.path.join(d, 'g.py')
    res = W.writeback(script, data, os.path.join(d, 'p.json'), verify=True,
                      python=_py(), timeout=120, style='inplace',
                      semantic=True, script_args=())
    check('A 写回成功', res.get('applied') is True,
          'reason=%s err=%s' % (res.get('reason'), res.get('err')))
    check('A verify_passes == 1（默认只跑一遍）',
          res.get('verify_passes') == 1, res.get('verify_passes'))
    check('A semantic_basis == live+savefig（主口径 = 图对象状态；快照在则追加比对）',
          res.get('semantic_basis') == 'live+savefig',
          res.get('semantic_basis'))
    check('A 三层验证都过（verified/semantic True）',
          res.get('verified') is True and res.get('semantic') is True,
          'verified=%s semantic=%s' % (res.get('verified'),
                                       res.get('semantic')))


def case_artifact_check():
    print('\n[A2] 追加判据：这次改动也必须出现在**磁盘上那张图**里（只严不松）')
    from mpltweak import verify as V
    d = _mkcase('a2_snapshot', CASE_SAVE_THEN_EDIT)
    script = os.path.join(d, 'g.py')
    # (a) 参数取"savefig 时刻"的值（12）—— 脚本跑完时是 99：旧口径本来就判失败
    op = V.verify_once(script, {'fig_index': 0,
                                'axes': [{'index': 0, 'title_fontsize': 12.0}]},
                       python=_py(), timeout=120)
    check('A2 主判据 = 脚本跑完时的图对象状态（12≠99 → 判失败）',
          op['status'] == 'ok' and op['verified'] is False
          and '12.0≠99.0' in (op.get('detail') or ''),
          'status=%s verified=%s detail=%s' % (op['status'], op['verified'],
                                               op.get('detail')))
    # (b) 参数取"结束状态"的值（99，正是 describe 会给的）—— 主判据过，但**磁盘上
    #     那张图**（最后一次 savefig 时刻）仍是 12 ⇒ 必须判失败。
    #     这正是旧口径的盲区：改动只落在最后一次 savefig 之后，图一个像素没变，
    #     旧实现照样报 verified=True。
    op2 = V.verify_once(script, {'fig_index': 0,
                                 'axes': [{'index': 0, 'title_fontsize': 99.0}]},
                        python=_py(), timeout=120)
    check('A2 图没变就必须判失败（旧口径会放过：主判据对得上）',
          op2['status'] == 'ok' and op2['verified'] is False
          and '磁盘上那张图' in (op2.get('detail') or ''),
          'status=%s verified=%s detail=%s' % (op2['status'], op2['verified'],
                                               op2.get('detail')))
    st, _err = V.collect(script, 0)
    check('A2 结束状态确实是 99（两条口径不同，证明追加判据真的在比另一份状态）',
          ((st or {}).get('axes') or [{}])[0].get('title_fontsize') == 99.0,
          (st or {}).get('axes'))


# ---------------------------------------------------------------------------
# C：覆盖不下降 —— ① 跑不通 / ② 改动没生效，都必须红
# ---------------------------------------------------------------------------
def case_catches_script_failure():
    print('\n[C①] 脚本跑不通 → 必须失败 + 回滚（不许静默留下写回）')
    d = _mkcase('c1_fail', CASE_SCRIPT_FAILS)
    before = open(os.path.join(d, 'g.py'), 'rb').read()
    _write_params(d)
    r, res, raw = _apply_json(d, extra=['--style', 'inplace'])
    check('C① 判失败且未保留写回（applied=False）', res.get('applied') is False,
          'rc=%s reason=%s %s' % (r.returncode, res.get('reason'), raw))
    check('C① verified=False（不许报"验证过"）', res.get('verified') is False,
          'verified=%s' % res.get('verified'))
    check('C① 脚本字节未变（回滚干净）',
          open(os.path.join(d, 'g.py'), 'rb').read() == before)


def case_catches_no_effect():
    print('\n[C②] 改动没生效（图状态与参数不符）→ 必须失败 + 回滚')
    d = _mkcase('c2_noeffect', CASE_NO_EFFECT)
    before = open(os.path.join(d, 'g.py'), 'rb').read()
    _write_params(d)
    r, res, raw = _apply_json(d, extra=['--style', 'inplace'])
    check('C② 判失败（applied=False）', res.get('applied') is False,
          'rc=%s reason=%s %s' % (r.returncode, res.get('reason'), raw))
    check('C② semantic=False 且文案指名不一致',
          res.get('semantic') is False and '21.0' in str(res.get('err')),
          'semantic=%s err=%s' % (res.get('semantic'), res.get('err')))
    check('C② 脚本字节未变（回滚干净）',
          open(os.path.join(d, 'g.py'), 'rb').read() == before)


# ---------------------------------------------------------------------------
# D：取不到状态 → fail-closed 回退两遍 + 原因可见（绝不静默判通过）
# ---------------------------------------------------------------------------
def case_fail_closed_fallback():
    print('\n[D] 取不到目标图状态 → 回退两遍 + 打印原因 + 语义层记 None')
    from mpltweak import verify as V
    from mpltweak import writeback as W
    d = _mkcase('d_nostate', CASE_NO_FIGURE)
    script = os.path.join(d, 'g.py')
    data = {'fig_index': 0, 'figsize_in': [6.0, 4.0],
            'axes': [{'index': 0, 'title_fontsize': 21.0}]}
    op = V.verify_once(script, data, python=_py(), timeout=120)
    check('D 单遍如实报 no_state（不是 ok）', op['status'] == 'no_state',
          'status=%s' % op['status'])
    check('D no_state 带给出原因（fail-closed 要有话可说）',
          bool(op.get('fallback_reason')), op.get('fallback_reason'))
    warns = []
    vp = W._verify_script(script, data, _py(), 120, semantic=True,
                          fields_by_axis={0: {'title_fontsize'}},
                          figsize_edited=False, script_args=None,
                          warnings=warns)
    check('D 回退到两遍（verify_passes == 2）', vp['passes'] == 2, vp['passes'])
    check('D 脚本成败仍由**原样重跑**判定（vok=True）', vp['vok'] is True,
          vp['vok'])
    check('D 语义层如实记 None（不是 True —— 没验到就不许说验过）',
          vp['sok'] is None, vp['sok'])
    check('D 回退原因打印进 warnings（用户可见）',
          bool(warns) and '回退' in warns[0] and '未取到目标图状态' in warns[0],
          warns)


CASES = [case_single_pass_parity, case_verify_passes_is_one,
         case_artifact_check, case_catches_script_failure,
         case_catches_no_effect, case_fail_closed_fallback]


def main():
    try:
        for fn in CASES:
            fn()
    finally:
        shutil.rmtree(_TMPROOT, ignore_errors=True)
    print('FAILED: %d' % fail if fail else 'ALL PASS')
    return 1 if fail else 0


if __name__ == '__main__':
    sys.exit(main())
