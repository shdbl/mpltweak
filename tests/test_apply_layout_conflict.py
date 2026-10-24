# -*- coding: utf-8 -*-
"""D1（0.1.14，用户拍板 B）：布局引擎冲突**默认拒绝写回** + `--force` 绕过。

四条行为（按写回路径区分，不一刀切）：
  ① 冲突 + 本次要写**几何类**字段（轴 position / add_axes 矩形 …）⇒ **默认拒绝**：
     不写盘、脚本字节不变、rc=1（"写回没落地"档），并给两条出路；
  ② 冲突 + 本次只写**非几何**字段（字号 / 线宽 / cmap / 标题 …）⇒ **不拒绝**（保持旧行为）；
  ③ `--force` ⇒ 跳过这条检查、照写，并在输出里**明说**"已按 --force 跳过"；
  ④ `check` 子命令：该情形从 warn 升为 **error 级**问题（rc=1）。

为什么这一条必须按路径分：引擎（constrained_layout / tight_layout）**每次绘制**都会重算轴
位置，写进源码的位置一定被覆盖 —— 用户"拖完白拖"。但只改字号的那次写回完全不受影响，
一刀切会把本来好用的路堵死。判据在 `writeback.position_write_needed`（只在"这次真会改
位置"时回 True），本套件就是钉它。

跑法（脚本式，与 CI 一致）：`python tests/test_apply_layout_conflict.py`
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile

PY = sys.executable
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, 'src')

# 控制台兜底（GBK 控制台下 print 中文会 UnicodeEncodeError 把整套用例打挂；
# tests/test_hygiene.py 的 `test_test_suites_cannot_crash_gbk_console` 认这个标记）
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(errors='replace')
    except (AttributeError, ValueError, OSError):
        pass          # 被重定向成不支持 reconfigure 的对象时忽略即可

FAILED = []
PASSED = []


def check(name, cond, detail=''):
    if cond:
        PASSED.append(name)
        print('  OK   %s%s' % (name, (' | %s' % detail) if detail else ''))
    else:
        FAILED.append(name)
        print('  FAIL %s%s' % (name, (' | %s' % detail) if detail else ''))


def _run(args, cwd):
    env = dict(os.environ, MPLBACKEND='Agg', PYTHONUTF8='1',
               PYTHONIOENCODING='utf-8', PYTHONPATH=SRC)
    r = subprocess.run([PY, '-m', 'mpltweak.cli', *args], cwd=cwd,
                       capture_output=True, env=env)
    return (r.returncode,
            (r.stdout or b'').decode('utf-8', 'replace'),
            (r.stderr or b'').decode('utf-8', 'replace'))


# --- 夹具：add_axes 让 pos 在代码里是**字面量**（原位可写），标题字号也是字面量 ---
TEXT_CONFLICT = '''import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

fig = plt.figure(figsize=(6.4, 4.2), constrained_layout=True)
ax1 = fig.add_axes([0.10, 0.10, 0.38, 0.38])
ax1.set_title("panel 1", fontsize=12)
ax1.plot([0, 1, 2], [0, 1, 0])
ax2 = fig.add_axes([0.55, 0.10, 0.38, 0.38])
ax2.set_title("panel 2", fontsize=12)
ax2.plot([0, 1, 2], [0, 1, 0])
fig.savefig("out.png")
'''

TEXT_CLEAN = TEXT_CONFLICT.replace(', constrained_layout=True', '')

GEO = [0.12, 0.12, 0.36, 0.62]


def _sha(path):
    with open(path, 'rb') as f:
        return hashlib.sha256(f.read()).hexdigest()


def _fixture(base, name, text):
    d = os.path.join(base, name)
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, 'fig.py')
    with open(p, 'w', encoding='utf-8', newline='\n') as f:
        f.write(text)
    rc, out, err = _run(['describe', 'fig.py', '-o', 'p.json'], d)
    assert rc == 0, (name, rc, out, err)
    with open(os.path.join(d, 'p.json'), encoding='utf-8') as f:
        data = json.load(f)
    return d, p, data


def _save(d, data):
    with open(os.path.join(d, 'p.json'), 'w', encoding='utf-8',
              newline='\n') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def case1_conflict_geometry(base):
    """① 冲突 + 几何 ⇒ 拒绝：rc=1、字节不变、两条出路都在。"""
    print('\n[1] 冲突 + 几何（默认拒绝）')
    d, p, data = _fixture(base, 'geo', TEXT_CONFLICT)
    before = _sha(p)
    data['axes'][0]['pos'] = GEO
    _save(d, data)
    rc, out, err = _run(['apply', 'fig.py', '--params', 'p.json', '--write'], d)
    blob = out + err
    check('① rc=1（写回没落地档）', rc == 1, 'rc=%d' % rc)
    check('① 脚本字节未变', _sha(p) == before,
          '%s -> %s' % (before[:12], _sha(p)[:12]))
    check('① 说出原因（布局引擎会覆盖位置）',
          'constrained_layout' in blob and '轴位置' in blob, blob[-300:])
    check('① 给出路一（去掉引擎后重试）', '出路一' in blob and '重试' in blob)
    check('① 给出路二（--force）', '出路二' in blob and '--force' in blob)
    check('① 没有静默写（不出现"已写回"）', '已写回' not in blob)


def case2_conflict_nongeometry(base):
    """② 冲突 + 只改非几何 ⇒ 不拒绝、正常写回。"""
    print('\n[2] 冲突 + 非几何（字号）')
    d, p, data = _fixture(base, 'nongeo', TEXT_CONFLICT)
    before = _sha(p)
    data['axes'][0]['title_fontsize'] = 17
    _save(d, data)
    rc, out, err = _run(['apply', 'fig.py', '--params', 'p.json', '--write'], d)
    blob = out + err
    check('② 不拒绝（rc!=1 或明确 best_effort/ok）', '默认拒绝写回' not in blob,
          blob[-300:])
    check('② 没被布局引擎那条拦住（无"出路二"提示）', '出路二' not in blob)
    check('② 字节已变（真写了）', _sha(p) != before,
          '%s -> %s' % (before[:12], _sha(p)[:12]))
    with open(p, encoding='utf-8') as f:
        body = f.read()
    check('② 写进去的是字号 17', '17' in body.split('panel 1')[1][:60])


def case3_force(base):
    """③ --force ⇒ 照写，并明说已跳过检查。"""
    print('\n[3] 冲突 + 几何 + --force')
    d, p, data = _fixture(base, 'force', TEXT_CONFLICT)
    before = _sha(p)
    data['axes'][0]['pos'] = GEO
    _save(d, data)
    rc, out, err = _run(['apply', 'fig.py', '--params', 'p.json', '--write',
                         '--force'], d)
    blob = out + err
    check('③ 不拒绝（不出现"默认拒绝写回"）', '默认拒绝写回' not in blob, blob[-300:])
    check('③ 明说"已按 --force 跳过布局引擎冲突检查"',
          '已按 --force 跳过' in blob, blob[:300])
    check('③ 字节已变（照写）', _sha(p) != before,
          '%s -> %s' % (before[:12], _sha(p)[:12]))


def case4_no_conflict(base):
    """④ 无冲突 + 几何 ⇒ 行为不变（正常写回、无跳过声明）。"""
    print('\n[4] 无冲突 + 几何（对照）')
    d, p, data = _fixture(base, 'clean', TEXT_CLEAN)
    before = _sha(p)
    data['axes'][0]['pos'] = GEO
    _save(d, data)
    rc, out, err = _run(['apply', 'fig.py', '--params', 'p.json', '--write'], d)
    blob = out + err
    check('④ 无冲突时不拦（无"默认拒绝写回"）', '默认拒绝写回' not in blob, blob[-300:])
    check('④ 无冲突时不打跳过声明', '已按 --force 跳过' not in blob)
    check('④ 字节已变（正常写回）', _sha(p) != before,
          '%s -> %s' % (before[:12], _sha(p)[:12]))


def case5_check_error_level(base):
    """⑤ check 子命令：冲突从 warn 升为 error 级（rc=1）。"""
    print('\n[5] check --static：冲突是 error 级')
    d, _p, _data = _fixture(base, 'chk', TEXT_CONFLICT)
    rc, out, err = _run(['check', 'fig.py', '--static'], d)
    blob = out + err
    check('⑤ rc=1（error 级问题）', rc == 1, 'rc=%d' % rc)
    check('⑤ 点名了冲突行与后果',
          'constrained_layout' in blob and '写了不生效' in blob, blob[-300:])


def case6_no_state_leftovers(base):
    """⑥ t36（REV1-2）：探测不许在用户的 `.tweak_params/` 里留下副文件。

    修前实测：一次**被拒**的 `apply --write`（什么都没有写回）之后，目录里多出
    `_layout_probe_state.json`（D1 的 `position_write_needed` 起的子进程写的），
    以及回退两遍验证留下的 `_verify_state.json` —— 两者都违反 README 的
    "零侵入、不生成副文件"。判据：跑完之后目录里不许出现 `_` 开头的文件。
    """
    print('\n[6] 探测副文件不许残留（拒绝路径 / 成功路径）')
    for tag, mutate in (('leak_refused', 'pos'), ('leak_written', 'font')):
        d, p, data = _fixture(base, tag, TEXT_CONFLICT)
        data['axes'][0]['pos'] = GEO if mutate == 'pos' else data['axes'][0]['pos']
        if mutate == 'font':
            data['axes'][0]['title_fontsize'] = 17
        _save(d, data)
        rc, out, err = _run(['apply', 'fig.py', '--params', 'p.json', '--write'], d)
        # 两处都查：`_layout_probe_state.json` 落在**参数文件所在目录**（可能是脚本旁，
        # 也可能是别的目录）；`_verify_state.json` 落在**脚本旁** `.tweak_params/`。
        names = []
        for sub in ('', '.tweak_params'):
            bdir = os.path.join(d, sub) if sub else d
            if os.path.isdir(bdir):
                names += os.listdir(bdir)
        left = [n for n in names if n.startswith('_')]
        check('⑥ %s：探测副文件零残留（目录=%r）' % (tag, sorted(names)),
              not left, '残留=%r rc=%d | %s' % (left, rc, (out + err)[-160:]))
        check('⑥ %s：既没有 _layout_probe_state 也没有 _verify_state' % tag,
              '_layout_probe_state.json' not in names
              and '_verify_state.json' not in names, sorted(names))


def case7_layout_why_in_json(base):
    """⑦ REV1-1b：`--json` 的 `files[].layout_why` 必须给出**判据依据**。

    为什么单开一条：载荷过去只抄了 `layout_conflicts`（"哪一行开了引擎"），agent 拿不到
    "实测有差异 live_differs" vs "判不了 unjudged" 的分支依据。允许值域 = 拒写判据的
    返回值：no_pos / proved_equal / live_equal / live_differs / unjudged。
    """
    print('\n[7] --json 的 layout_why（判据依据）')
    allowed = {'no_pos', 'proved_equal', 'live_equal', 'live_differs', 'unjudged'}
    # ⑦a 默认档：要写位置且与实测不同 ⇒ live_differs
    d, p, data = _fixture(base, 'why_diff', TEXT_CONFLICT)
    data['axes'][0]['pos'] = GEO
    _save(d, data)
    rc, out, err = _run(['apply', 'fig.py', '--params', 'p.json', '--write',
                         '--json'], d)
    why = (json.loads(out)['files'][0].get('layout_why')
           if out.strip().startswith('{') else '<无 JSON>')
    check('⑦a 默认档拒写：layout_why 在值域内且为 live_differs',
          rc == 1 and why == 'live_differs', 'rc=%s why=%r' % (rc, why))
    check('⑦a layout_why 的值域收窄在拒写判据里', why in allowed, '%r' % (why,))
    # ⑦b --no-verify：不重跑 ⇒ 判不了 ⇒ unjudged（fail-closed 仍拒）
    d, p, data = _fixture(base, 'why_unj', TEXT_CONFLICT)
    data['axes'][0]['pos'] = GEO
    _save(d, data)
    rc, out, err = _run(['apply', 'fig.py', '--params', 'p.json', '--write',
                         '--no-verify', '--json'], d)
    why = (json.loads(out)['files'][0].get('layout_why')
           if out.strip().startswith('{') else '<无 JSON>')
    check('⑦b --no-verify（判不了）⇒ layout_why=unjudged 且仍 rc=1',
          rc == 1 and why == 'unjudged', 'rc=%s why=%r' % (rc, why))
    # ⑦c --force：放行 ⇒ 这条拒写判据没参与，layout_why 必须是 None（不是空串/缺席以外的东西）
    d, p, data = _fixture(base, 'why_force', TEXT_CONFLICT)
    data['axes'][0]['pos'] = GEO
    _save(d, data)
    rc, out, err = _run(['apply', 'fig.py', '--params', 'p.json', '--write',
                         '--force', '--json'], d)
    entry = json.loads(out)['files'][0] if out.strip().startswith('{') else {}
    check('⑦c --force 放行：layout_why 为 None（该键恒存在、不参与时不撒谎）',
          rc == 0 and 'layout_why' in entry and entry.get('layout_why') is None,
          'rc=%s why=%r' % (rc, entry.get('layout_why')))
    # ⑦d 非几何那次：同样不参与 ⇒ None
    d, p, data = _fixture(base, 'why_nongeo', TEXT_CONFLICT)
    data['axes'][0]['title_fontsize'] = 17
    _save(d, data)
    rc, out, err = _run(['apply', 'fig.py', '--params', 'p.json', '--write',
                         '--json'], d)
    entry = json.loads(out)['files'][0] if out.strip().startswith('{') else {}
    check('⑦d 非几何写回：layout_why 为 None（没有退化成 no_pos 噪音）',
          rc == 0 and entry.get('layout_why') is None,
          'rc=%s why=%r' % (rc, entry.get('layout_why')))


def main():
    base = tempfile.mkdtemp(prefix='mpltweak_d1_')
    try:
        case1_conflict_geometry(base)
        case2_conflict_nongeometry(base)
        case3_force(base)
        case4_no_conflict(base)
        case5_check_error_level(base)
        case6_no_state_leftovers(base)
        case7_layout_why_in_json(base)
    finally:
        shutil.rmtree(base, ignore_errors=True)
    print('\n%s：%d 项通过，%d 项失败' %
          ('FAILED' if FAILED else 'ALL PASS', len(PASSED), len(FAILED)))
    if FAILED:
        for name in FAILED:
            print('  - ' + name)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
