# -*- coding: utf-8 -*-
"""t86 CLI 参数 × 脚本 组合模糊测试 —— **永不当机、永不撒谎**。

用户 2026-10-14 拍板：再补一条**离屏、便宜、高产出**的模糊测试，抓的正是他踩过的
那一类 —— **崩 + 误导性报错**（`describe -o` 崩、`--timeout` 文档不符）。

做法：把 **命令 × 输入 × 参数文件 × 选项** 的笛卡尔积**确定性枚举**，按 seed 抽样跑，
对每次调用断言五条。**不依赖 hypothesis**（CI 的 step 6 按设计不装它），只用 `random`
做抽样、**seed 可复现**。

五条断言（每条都配"单点变异 ⇒ 必红"的 teeth）
------------------------------------------------
1. **永不出现未处理的 traceback**：stderr 不得含 `Traceback (most recent call last)`。
2. **rc 必属文档化集合 {0,1,2,4}**（`messages.py:1757` 的 `--help` 文案钉死；0.1.9 的 3 已退役），
   且**与文档口径一致**：脚本找不到 = 2、未知选项 = 2。
3. **报错说人话**：rc≠0 时 **stdout 或 stderr 至少一路**要有可执行信息（路径/选项名/下一步）；
   对**用户输入类**错误（脚本不存在 / 未知选项）必须**点名**路径或选项名，不许只说 "internal error"。
   ⚠️ **为什么不是"必须 stderr"**：`apply` **不带 `--json` 时人读输出走 stdout**（实测：
   `no params: <路径>` / `!! params file error (write refused): …` 都在 stdout、rc=2、stderr 空；
   带 `--json` 时 JSON 走 stdout、人读走 stderr —— 见 t74 地面真值）。按字面要求"必须 stderr"
   会把**产品既定行为**判成缺陷（= 检查器误报，正是本任务最怕的废掉方式）。所以判据落在
   "**有没有说到点子上**"，而不是"说到哪个流"。
4. **`--json` 恒可解析**：给了 `--json` 且 stdout 非空 ⇒ 必须是**合法 JSON**；
   `apply --json` 失败时（rc=2）必须带 **`error_code`**（B9 契约，值域见 `apply.py:440-476`）。
5. **en 下无中文**：全程 `MPLTWEAK_LANG=en` ⇒ stdout/stderr 逐字节 ASCII
   （台账 `KNOWN_NON_ASCII` 只准缩小，见文件末尾；与 `test_journey_wheel.py` 同款纪律）。

规模
----
* **CI smoke**：`MPLTWEAK_CLIFUZZ_N`（默认 12）—— 秒级量级。
* **发布期大样本**：位置参数或环境变量给 N（如 600）。
* **组合分母**：`len(COMMANDS) × len(INPUTS) × len(PARAMS) × len(OPTIONS)`，脚本会打印。
* **抽样**：分层（每个命令都保证被抽到）+ 确定性 stride，seed 固定即可复现。

踩过的坑（写给后人）
--------------------
* **`--json` 不是所有子命令都认**：`describe` 默认就输出 JSON、`revert` 不列 `--json`
  ⇒ 给了不认它的命令会走"未知选项"（rc=2）。所以断言 4 只在 **stdout 非空**时要求可解析，
  且 `error_code` 只对 **apply** 强求（那是 B9 明写的契约）。
* **子进程 env 一律走 `_pin()`**（hygiene 的级联闸门棘轮要求），并钉 `MPLTWEAK_HOME`
  到临时区 —— 否则 CLI 会去碰用户真实家目录（t31 的教训）。
"""

import io
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time

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


def _pin(env):
    """子进程 env 闸门（hygiene 的级联闸门棘轮按"函数体里有 MPLBACKEND=Agg"识别）。"""
    env = dict(env)
    env['MPLBACKEND'] = 'Agg'
    env['PYTHONIOENCODING'] = 'utf-8'
    env['MPLTWEAK_LANG'] = 'en'          # 断言 5：全程英文宿主
    env['MPLTWEAK_HOME'] = os.path.join(tempfile.gettempdir(), 'mpltweak_clifuzz_home')
    return env


# 认脚本的子命令（doctor 是**环境自检**，根本不看脚本位置参数 ⇒ 不能对它钉"脚本找不到=2"）。
SCRIPT_CMDS = ('describe', 'check', 'apply', 'revert')
# 文档里**认** `--json` 的子命令（各自的 usage 里列了它）。describe/doctor 不认 ⇒
# 给它们 `--json` 只会走"未知选项"，不能要求 stdout 是 JSON。
JSON_CMDS = ('apply', 'check', 'revert')

# 台账：已知缺陷（**只准缩小**）。键 = 缺陷名；值 = **精确条件** + 为什么还没修。
# 命中**不算 FAIL**，但末尾必须**全文打印**（不许掩盖）；条目不再命中时会提示删除。
# 两条附加纪律（船长 2026-10-14 对 t77 台账的裁定，这里照搬）：条件写精确 + 末尾打印全文。
#
# **两次缩小记录（都是"修好就删 + 把修好的行为收紧成断言"）**：
#   ① `check_fig_missing_zh_in_en` —— check 的"第 N 张图不存在"在 en 下是中文；
#      code-owner **t90** 修好（en 纯 ASCII、单图措辞改成"请用 0"）⇒ 删条目。
#   ② `doctor_reject_does_not_name_option` —— doctor 拒未知选项但**不点名**；
#      code-owner **t93** 修好（根因很值得记：`cli.py` 调的 `schema_unknown_args` 键
#      **从未登记**，而 `t()` 对缺键**直接回显键名** ⇒ 用户只看到字面量键名）⇒ 删条目。
# **台账现为空**。结构保留，供以后登记。
KNOWN = {}

# **doctor 的严格校验（t91 裁定）**：doctor 是环境自检，曾经**静默接受**未知/无关选项与
# 多余位置参数并 rc=0（与 `--help` 里"未知选项 = 2"的口径不一致）。船长裁定后
# code-owner 的 **t91** 把它改成**严格校验 ⇒ rc=2**。
# 因此这里把它从"只打印的观察"升级成**真断言**（台账缩小了就补一条回归守卫）：
# 一旦又出现"doctor + 选项 且 rc=0"，就是退回静默接受 ⇒ 判红。
# 注意：它**不属**五条断言的任何一条（doctor 不认 `--json`、也不看脚本位置参数），
# 所以单独成一条 —— 避免又把 ②/④ 写宽（第一版就是这么误报的）。


def ledger_key(r):
    """把一条违规映射到台账键（只认**当前台账里**的键；认不出 ⇒ None ⇒ 真 FAIL）。"""
    for b in r['bad']:
        if b.startswith('en 下非 ASCII') and r['cmd'].startswith('check'):
            return 'check_fig_missing_zh_in_en'
        if '没点名选项' in b and r['cmd'] == 'doctor':
            return 'doctor_reject_does_not_name_option'
    return None


COMMANDS = ('doctor', 'describe', 'check', 'apply', 'apply --write',
            'revert', 'revert --write')
INPUTS = ('ok', 'syntax', 'raises', 'needs_args', 'empty', 'binary', 'dir',
          'missing', 'readonly', 'longpath')
PARAMS = (None, 'missing', 'badjson', 'noversion', 'notobject', 'good')
OPTIONS = ((), ('--json',), ('--fig', '0'), ('--fig', '1'), ('--fig', '99'),
           ('--fig', '-1'), ('--timeout', '60'), ('--timeout', 'abc'),
           ('--timeout', '0'), ('--force',), ('--no-tight',), ('--nope',),
           ('--json', '--json'), ('--', 'extra'))
DENOM = len(COMMANDS) * len(INPUTS) * len(PARAMS) * len(OPTIONS)

_OK_SRC = ("import matplotlib\nmatplotlib.use('Agg')\nimport matplotlib.pyplot as plt\n"
           "\nfig = plt.figure(figsize=(6, 4))\n"
           "ax = fig.add_axes([0.15, 0.15, 0.5, 0.5])\n"
           "ax.set_title('t', fontsize=12)\nfig.savefig('o.png')\n")


def make_inputs(root):
    """造全部输入样本 → {名字: 路径}。**全部在临时目录里**，不碰仓库。"""
    d = os.path.join(root, 'inputs')
    os.makedirs(d, exist_ok=True)
    paths = {}
    with open(os.path.join(d, 'ok.py'), 'w', encoding='utf-8', newline='') as f:
        f.write(_OK_SRC)
    paths['ok'] = os.path.join(d, 'ok.py')
    with open(os.path.join(d, 'syntax.py'), 'w', encoding='utf-8', newline='') as f:
        f.write('def broken(:\n')
    paths['syntax'] = os.path.join(d, 'syntax.py')
    with open(os.path.join(d, 'raises.py'), 'w', encoding='utf-8', newline='') as f:
        f.write('raise RuntimeError("boom from user script")\n')
    paths['raises'] = os.path.join(d, 'raises.py')
    with open(os.path.join(d, 'needs_args.py'), 'w', encoding='utf-8',
              newline='') as f:
        f.write('import sys\nopen(sys.argv[1]).read()\n')
    paths['needs_args'] = os.path.join(d, 'needs_args.py')
    with open(os.path.join(d, 'empty.py'), 'w', encoding='utf-8', newline='') as f:
        f.write('')
    paths['empty'] = os.path.join(d, 'empty.py')
    with open(os.path.join(d, 'binary.py'), 'wb') as f:
        f.write(b'\x00\x01\x02\xff\xfe\x00matplotlib')
    paths['binary'] = os.path.join(d, 'binary.py')
    dd = os.path.join(d, 'dir_as_script.py')
    os.makedirs(dd, exist_ok=True)
    paths['dir'] = dd
    paths['missing'] = os.path.join(d, 'not_here.py')
    ro = os.path.join(d, 'readonly.py')
    with open(ro, 'w', encoding='utf-8', newline='') as f:
        f.write(_OK_SRC)
    os.chmod(ro, 0o444)
    paths['readonly'] = ro
    paths['longpath'] = os.path.join(d, 'x' * 260 + '.py')
    return paths


def make_params(root, ok_path):
    """造全部参数文件 → {名字: 路径}；`good` 用 describe 的真实产物。"""
    d = os.path.join(root, 'params')
    os.makedirs(d, exist_ok=True)
    out = {'missing': os.path.join(d, 'not_here.json')}
    with open(os.path.join(d, 'badjson.json'), 'w', encoding='utf-8') as f:
        f.write('{"version": 4,,}')
    out['badjson'] = os.path.join(d, 'badjson.json')
    with open(os.path.join(d, 'noversion.json'), 'w', encoding='utf-8') as f:
        json.dump({'axes': []}, f)
    out['noversion'] = os.path.join(d, 'noversion.json')
    with open(os.path.join(d, 'notobject.json'), 'w', encoding='utf-8') as f:
        json.dump([1, 2, 3], f)
    out['notobject'] = os.path.join(d, 'notobject.json')
    gp = os.path.join(d, 'good.json')
    r = subprocess.run([sys.executable, '-m', 'mpltweak', 'describe', ok_path,
                        '-o', gp], cwd=root, capture_output=True, env=_pin(os.environ))
    out['good'] = gp if (r.returncode == 0 and os.path.exists(gp)) else None
    return out


# 固定 corpus：**可复现的失败/可疑样本**（seed + 精确组合 + 期望归类）。
# 不存脚本文件：输入样本由 `make_inputs()` **确定性重建**，抽样由 seed 确定性复现
# ⇒ "精确组合"本身就是最小化样本。
# `expect`：'known' = 命中台账（已知缺陷）/ 'clean' = 必须完全干净（防"修过头"把好行为改坏）。
CORPUS = (
    ('check', 'ok', None, ('--fig', '1'), 'clean',
     't90 已修：en 纯 ASCII（`figure 1 does not exist (the script has only 1); use 0, …`）、'
     '单图措辞改成「请用 0」⇒ 不再命中台账，必须干净'),
    ('doctor', 'missing', None, ('--fig', '99'), 'clean',
     't91 已裁定：doctor 严格校验未知选项 ⇒ rc=2（过去静默接受 rc=0）⇒ 不再是观察项'),
    ('doctor', 'missing', None, ('--nope',), 'names',
     't93 已修 + **收紧**：rc=2 **且输出必须含 `--nope`**'
     '（过去只回显未登记的键名 `schema_unknown_args`）'),
    ('doctor', 'missing', None, ('--json',), 'names',
     't93 已修 + **收紧**：doctor 没有 --json ⇒ rc=2 且输出必须含 `--json`'),
    ('apply', 'ok', 'badjson', (), 'clean',
     '参数文件坏 JSON ⇒ rc=2，人话在 **stdout**（apply 不带 --json 的既定行为）'),
    ('describe', 'missing', None, (), 'clean',
     '脚本不存在 ⇒ rc=2 且报错点名路径'),
    ('revert', 'ok', None, ('--json',), 'clean',
     'revert 认 --json ⇒ stdout 必须是合法 JSON'),
)


def enumerate_combos():
    out = []
    for cmd in COMMANDS:
        for inp in INPUTS:
            for par in PARAMS:
                for opt in OPTIONS:
                    out.append((cmd, inp, par, opt))
    return out


def sample(n, seed=0):
    """分层抽样：每个命令都保证抽到，其余按确定性 stride 铺开（seed 可复现）。"""
    allc = enumerate_combos()
    rnd = random.Random(seed)
    by_cmd = {}
    for c in allc:
        by_cmd.setdefault(c[0], []).append(c)
    picked = []
    for cmd in COMMANDS:                      # 先保证每个命令至少一条
        picked.append(rnd.choice(by_cmd[cmd]))
    rest = [c for c in allc if c not in picked]
    rnd.shuffle(rest)
    picked += rest[:max(0, n - len(picked))]
    rnd.shuffle(picked)
    return picked


# ---------------------------------------------------------------------------
# 单次调用 + 五条断言
# ---------------------------------------------------------------------------
def run_one(cmd, inp, par, opt, ctx):
    argv = list(cmd.split())
    argv.append(ctx['inputs'][inp])
    if par is not None and ctx['params'].get(par):
        argv += ['--params', ctx['params'][par]]
    argv += list(opt)
    r = subprocess.run([sys.executable, '-m', 'mpltweak'] + argv,
                       cwd=ctx['root'], capture_output=True, env=_pin(os.environ))
    rc = r.returncode
    out = r.stdout.decode('utf-8', 'replace')
    err = r.stderr.decode('utf-8', 'replace')
    bad = []
    # ① 未处理 traceback
    if 'Traceback (most recent call last)' in err:
        bad.append('traceback')
    # ② rc 属文档化集合
    if rc not in (0, 1, 2, 4):
        bad.append('rc=%s 不在 {0,1,2,4}' % rc)
    if inp == 'missing' and rc != 2 and cmd in SCRIPT_CMDS:
        bad.append('脚本找不到应 rc=2，实得 %s' % rc)
    if '--nope' in opt and rc != 2 and cmd != 'doctor':
        bad.append('未知选项应 rc=2，实得 %s' % rc)
    # ③ 报错说人话（stdout 或 stderr 至少一路；apply 的人读错误走 stdout）
    both = out + err
    if rc != 0 and not both.strip():
        bad.append('rc≠0 但 stdout/stderr 都空（用户不知道发生了什么）')
    # 只有**没有其它非法/无关选项**时，"报错必须点名脚本路径"才成立 —— 否则先炸的是
    # argparse 的"未知选项"（那种报错当然只点名选项）。第一版只排了 `--nope`/`--force`，
    # 结果 `revert <missing> --fig 0` 这类（revert 不认 --fig）又误报 3 条 ⇒ 改成
    # **除了 `--json` 之外的任何 `--*` 都算**（`--` 也算，它是分隔符不是错误来源）；
    # **重复的 `--json` 也算** —— argparse 会把重复的不可追加选项判成错误，
    # 那种报错同样只点名选项（N=240 实测 `check <missing> --json --json` 又误报 1 条）。
    _other_bad_opt = any(o.startswith('--') and o != '--json' for o in opt) \
        or list(opt).count('--json') > 1
    if rc == 2 and inp == 'missing' and not _other_bad_opt and 'not_here' not in both:
        bad.append('脚本不存在的报错里没点名路径')
    if rc == 2 and '--nope' in opt and '--nope' not in both:
        bad.append('未知选项的报错里没点名选项')
    # ④ --json 恒可解析（只对**文档里认 --json** 的子命令强求）
    if '--json' in opt and out.strip() and rc in (0, 1, 2) and cmd.split()[0] in JSON_CMDS:
        try:
            payload = json.loads(out)
        except ValueError:
            bad.append('--json 的 stdout 不是合法 JSON')
            payload = None
        if payload is not None and rc == 2 and cmd.startswith('apply'):
            if 'error_code' not in payload:
                bad.append('apply --json 失败载荷缺 error_code（B9 契约）')
    # ⑤ en 下无中文
    nonascii = sorted(set(ch for ch in (out + err) if ord(ch) > 127))
    if nonascii:
        bad.append('en 下非 ASCII：%s' % ''.join(nonascii[:12]))
    # ⚠ **不要在这里截断**：断言要靠**全文**判 —— "必须点名被拒参数"那一行可能落在 400 字
    # 之后（超长路径样本 `longpath` 有 260 字符的文件名，实测因此被误判成"不点名"）。
    # 截断只发生在**展示**时（`_show()` 里做）。
    return {'cmd': cmd, 'inp': inp, 'par': par, 'opt': opt, 'rc': rc,
            'bad': bad, 'out': out, 'err': err}


def _combo(r):
    return '%s|%s|%s|%s' % (r['cmd'], r['inp'], r['par'], '+'.join(r['opt']) or '-')


def _name_hit(rc, text, opts):
    """**收紧型**判据：rc 必须是 2，且输出必须**点名**每个被拒的选项。

    抽成函数是为了让 corpus 期望与 teeth **共用同一份判据** —— 否则 teeth 咬的
    可能是另一套逻辑，看着绿实际不设防。
    """
    opts = [o for o in opts if o.startswith('--') and o != '--']
    return rc == 2 and bool(opts) and all(o in text for o in opts)


def _show(rows, n=4):
    return [(r['cmd'], r['inp'], r['par'], '+'.join(r['opt']) or '-', r['rc'],
             r['bad'], (r['out'] + r['err']).strip().replace('\n', ' ')[:110])
            for r in rows[:n]]


# ---------------------------------------------------------------------------
# teeth：五条断言各自"单点变异 ⇒ 必红"
# ---------------------------------------------------------------------------
def teeth():
    out = []
    base = {'rc': 0, 'out': '', 'err': '', 'bad': []}

    def judge(rec):
        bad = []
        if 'Traceback (most recent call last)' in rec['err']:
            bad.append('traceback')
        if rec['rc'] not in (0, 1, 2, 4):
            bad.append('rc')
        if rec['rc'] != 0 and not (rec['out'] + rec['err']).strip():
            bad.append('silent')
        if rec.get('json_expected') and rec['out'].strip():
            try:
                json.loads(rec['out'])
            except ValueError:
                bad.append('json')
        if sorted(set(ch for ch in (rec['out'] + rec['err']) if ord(ch) > 127)):
            bad.append('nonascii')
        return bad

    out.append(('① traceback 判据能咬人',
                'traceback' in judge(dict(base, err='Traceback (most recent call last):'))))
    out.append(('② rc 越界能咬人', 'rc' in judge(dict(base, rc=99))))
    out.append(('③ "rc≠0 但 stderr 空"能咬人', 'silent' in judge(dict(base, rc=2))))
    out.append(('④ --json 坏 JSON 能咬人',
                'json' in judge(dict(base, json_expected=True, out='{not json,'))))
    out.append(('⑤ en 下中文能咬人',
                'nonascii' in judge(dict(base, out='回退预览'))))
    # ⑥ 收紧型判据（t93 后新增的"必须点名"）—— 抹掉选项名必须判红，带上必须判绿。
    out.append(('⑥ "必须点名被拒参数"能咬人（抹掉选项名 ⇒ 判红）',
                (not _name_hit(2, 'usage: mpltweak doctor … takes no other options',
                               ('--nope',)))
                and _name_hit(2, 'unrecognized argument(s): --nope', ('--nope',))
                and not _name_hit(0, '--nope', ('--nope',))))
    return out


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def main():
    t0 = time.time()
    n = int(os.environ.get('MPLTWEAK_CLIFUZZ_N', '12'))
    nums = [a for a in sys.argv[1:] if a.isdigit()]
    if nums:
        n = int(nums[0])
    seed = int(os.environ.get('MPLTWEAK_CLIFUZZ_SEED', '0'))
    _p('---- t86 CLI 组合模糊：组合分母 = %d×%d×%d×%d = **%d**；本轮抽 %d（seed=%d）----'
       % (len(COMMANDS), len(INPUTS), len(PARAMS), len(OPTIONS), DENOM, n, seed))
    root = tempfile.mkdtemp(prefix='mpltweak_clifuzz_')
    try:
        ctx = {'root': root, 'inputs': make_inputs(root)}
        ctx['params'] = make_params(root, ctx['inputs']['ok'])
        combos = sample(n, seed)
        rows, skipped = [], 0
        for c in combos:
            if c[2] is not None and not ctx['params'].get(c[2]):
                skipped += 1
                continue          # good.json 造不出来（describe 失败）⇒ 跳过该参数维度
            rows.append(run_one(c[0], c[1], c[2], c[3], ctx))
        _p('')
        _p('---- 五条断言（逐组合）----')
        def viol(tag):
            return [r for r in rows if any(tag in b for b in r['bad'])]
        # 台账分流：命中台账的**不算 FAIL**（末尾全文打印，不掩盖）；其余才是真违规。
        real = [r for r in rows if r['bad'] and ledger_key(r) is None]
        known_hits = [r for r in rows if r['bad'] and ledger_key(r) is not None]
        tb = [r for r in real if 'traceback' in r['bad']]
        check('① 永不出现未处理的 traceback（%d 组合）' % len(rows), not tb,
              '命中 %d：%s' % (len(tb), _show(tb)))
        rc_bad = [r for r in real if any(b.startswith('rc') or '应 rc=2' in b
                                         for b in r['bad'])]
        check('② rc 属文档化集合 {0,1,2,4} 且与文档口径一致', not rc_bad,
              '命中 %d：%s' % (len(rc_bad), _show(rc_bad)))
        talk = [r for r in real if any(('都空' in b) or ('没点名' in b)
                                       for b in r['bad'])]
        check('③ 报错说人话（rc≠0 必有 stdout 或 stderr；用户输入类错误必须点名路径/选项）',
              not talk, '命中 %d：%s' % (len(talk), _show(talk)))
        js = [r for r in real if any(('JSON' in b) or ('error_code' in b)
                                     for b in r['bad'])]
        check('④ --json 恒可解析（且 apply 失败载荷带 error_code）', not js,
              '命中 %d：%s' % (len(js), _show(js)))
        asc = [r for r in real if any(b.startswith('en 下非 ASCII') for b in r['bad'])]
        check('⑤ en 下无中文（stdout/stderr 逐字节 ASCII）', not asc,
              '命中 %d：%s' % (len(asc), _show(asc, 3)))
        _p('')
        _p('---- 覆盖与分母 ----')
        _p('  组合分母（命令×输入×参数×选项）= **%d**；本轮实跑 **%d** 条（跳过 %d 条：'
           '参数维度不可用）' % (DENOM, len(rows), skipped))
        cmds_seen = sorted(set(r['cmd'] for r in rows))
        ins_seen = sorted(set(r['inp'] for r in rows))
        _p('  命令覆盖 %d/%d：%s' % (len(cmds_seen), len(COMMANDS), cmds_seen))
        _p('  输入覆盖 %d/%d：%s' % (len(ins_seen), len(INPUTS), ins_seen))
        check('每个命令都至少跑到 1 条', len(cmds_seen) == len(COMMANDS),
              '缺 %s' % [c for c in COMMANDS if c not in cmds_seen])
        # rc 分布（可见性：确认不是"全 rc=0"的假绿）
        dist = {}
        for r in rows:
            dist[r['rc']] = dist.get(r['rc'], 0) + 1
        _p('  rc 分布：%s' % sorted(dist.items()))
        check('不是"全 rc=0"的假绿（必须出现过非 0 的 rc）',
              any(r['rc'] != 0 for r in rows), 'rc 分布 %s' % sorted(dist.items()))
        _doc_bad = [r for r in rows if r['cmd'] == 'doctor'
                    and any(o.startswith('--') and o != '--' for o in r['opt'])]
        _doc_silent = [r for r in _doc_bad if r['rc'] == 0]
        _doc_unnamed = [r for r in _doc_bad
                        if r['rc'] != 0 and not any(
                            o in (r['out'] + r['err'])
                            for o in r['opt'] if o.startswith('--') and o != '--')]
        check('doctor 严格校验未知/无关选项（t91）**且点名被拒参数**（t93，与 argparse 家族同形）',
              not _doc_silent and not _doc_unnamed,
              '静默接受 %d / 不点名 %d：%s'
              % (len(_doc_silent), len(_doc_unnamed),
                 _show(_doc_silent + _doc_unnamed, 3)))

        _p('')
        _p('---- 固定 corpus：%d 条可复现样本（seed=%d 下按精确组合复跑）----'
           % (len(CORPUS), seed))
        for cmd, inp, par, opt, expect, why in CORPUS:
            r = run_one(cmd, inp, par, opt, ctx)
            if ledger_key(r):
                got = 'known'
            elif expect == 'names':
                # **收紧**型期望：rc 必须是 2，且输出必须**点名**被拒的选项
                # （t93 修好后实得如此；这是"台账删掉 → 补一条更强的守卫"）。
                got = ('names' if _name_hit(r['rc'], r['out'] + r['err'], opt)
                       else 'VIOLATION')
            elif not r['bad']:
                got = 'clean'
            else:
                got = 'VIOLATION'
            check('corpus %s|%s|%s|%s ⇒ 期望 %s'
                  % (cmd, inp, par or '-', '+'.join(opt) or '-', expect),
                  got == expect, '实得 %s（%s）；bad=%s' % (got, why, r['bad']))

        _p('')
        _p('---- 已知缺陷台账 KNOWN（只准缩小；本轮命中 %d 条）----' % len(known_hits))
        _p('  ⚠ 台账**不是豁免**：它只把"已知且已派给 src/ 的缺陷"从 FAIL 降为可见记录；'
           '条目一旦不再命中，请删掉（说明修好了）。')
        if known_hits:
            for r in known_hits[:12]:
                _p('  · [%s] %s  rc=%s  %s'
                   % (ledger_key(r), _combo(r), r['rc'],
                      '；'.join(r['bad'])[:110]))
            _p('  · …（共 %d 条）' % len(known_hits))
        for k, why in sorted(KNOWN.items()):
            hit = any(ledger_key(r) == k for r in known_hits)
            _p('  [%s] %s' % ('命中' if hit else '本轮未命中', k))
            _p('      %s' % why)
        _p('')

        _p('---- teeth：五条断言各自单点变异 ⇒ 必须红 ----')
        for name, ok in teeth():
            check('teeth %s' % name, ok)
    finally:
        # 只读样本（chmod 444）会让 rmtree 在 Windows 上删不掉 ⇒ 先恢复可写
        # （第一次跑实测 `仍存在=True`）。
        for _r, _dirs, _files in os.walk(root):
            for _f in _files:
                try:
                    os.chmod(os.path.join(_r, _f), 0o666)
                except OSError:
                    pass
        shutil.rmtree(root, ignore_errors=True)
        _p('临时工作区已清理：%s（仍存在=%s）' % (root, os.path.exists(root)))
    _p('')
    _p('耗时 %.1fs（N=%d，%.2fs/组合）'
       % (time.time() - t0, n, (time.time() - t0) / max(1, n)))
    if _fails:
        _p('FAILED: %d 项 -> %s' % (len(_fails), _fails))
        return 1
    _p('ALL PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
