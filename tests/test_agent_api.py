# -*- coding: utf-8 -*-
"""mpltweak agent 接口测试（describe / schema / apply --json / 读改写闭环）。

§1  describe 输出干净 JSON 且结构符合 params 规范
§2  describe --all-figs 多图包装
§3  describe --compact 更短（给 agent 省 token）
§4  schema 合法，且能校验 describe 的产物
§5  apply --json：stdout 只有 JSON，过程信息走 stderr
§6  完整闭环：describe → agent 改数字 → apply --write → 源码真的变了
§7  figsize_in 不受 dpi 影响
§8  多图闭环：fig_index 贯穿 describe → apply
§9  --json 成功路径的 stdout 必须是 UTF-8（cp936 管道下原先编成 GBK）
§10 顶层兜底 guard_json_main 在异常路径上真的可用（原先 NameError 吃掉一切）
§11 布局引擎冲突跟着 apply 的落地结果一起报（块模式的 changes 是空的，别拿它当条件）
§12 轴范围 xlim：describe 采集 → 改数字 → apply 原位写回 → 语义验证通过
"""
import json
import locale
import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 测试断言的是中文人读文案：把界面语言**钉死**，别让宿主 locale 决定结论。
# messages 的顺序是 --lang > MPLTWEAK_LANG > locale > LANG > en：CI(Linux,
# LANG=C) 走英文、本地(zh-CN)走中文 —— 不钉住就会"本地全绿、CI 全红"（0.1.8 真栽过）。
# 需要英文分支的用例，自行 run(..., env={'MPLTWEAK_LANG': 'en'}) 覆盖即可。
os.environ['MPLTWEAK_LANG'] = 'zh'
PY = sys.executable
FIX = os.path.join(ROOT, 'tests', 'fixtures', 'synth_script.py')

# 版本号一律引用 SCHEMA_VERSION，别写字面量 —— schema 升到 4 时这里曾经硬编码 3 而挂掉
sys.path.insert(0, os.path.join(ROOT, 'src'))
from mpltweak import params as _params                            # noqa: E402

_fails = []


def check(cond, label, extra=''):
    print('%s %s%s' % ('  OK  ' if cond else ' FAIL ', label,
                       '' if cond else '   ' + str(extra)))
    if not cond:
        _fails.append(label)


def _dec(b):
    """按字节解码子进程输出：不假设控制台编码（Windows 上 CLI 的人读输出是 GBK）。

    测试套件跑在谁的机器上都该得出同样的结论 —— 用 encoding='utf-8' 硬解时，
    在 GBK 控制台下会抛 UnicodeDecodeError，把人读输出的断言变成假失败。
    """
    raw = b or b''
    for enc in ('utf-8', locale.getpreferredencoding(False) or 'utf-8'):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode('utf-8', 'replace')


def run(*args, cwd=None, env=None):
    """跑一次 CLI。env 里的键**并入**当前环境。

    用途：把**界面语言**和**输出编码**由测试钉死，而不是听凭宿主 locale ——
    messages 的探测顺序是 ``--lang > MPLTWEAK_LANG > locale > LANG > en``，
    于是同一份断言在 CI（Linux，LANG=C）走英文、在本地（Windows zh-CN）走中文。
    0.1.8 发布时就栽在这里：只断言中文的用例在 CI 全红（本地全绿）。
    """
    _env = None
    if env:
        _env = dict(os.environ)
        _env.update(env)
    r = subprocess.run([PY, '-m', 'mpltweak.cli', *args], cwd=cwd or ROOT,
                       capture_output=True, env=_env)
    return r.returncode, _dec(r.stdout), _dec(r.stderr)


DEMO = textwrap.dedent('''
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    fig = plt.figure(figsize=(8, 4))
    ax1 = fig.add_axes([0.08, 0.55, 0.40, 0.35])
    ax1.plot([1, 2, 3], [1, 4, 9])
    ax1.set_title('A', fontsize=10)
    ax2 = fig.add_axes([0.55, 0.55, 0.40, 0.35])
    ax2.plot([1, 2, 3], [3, 2, 1])
    fig.savefig('out.png', dpi=80)
''')

# §11 用的夹具：网格轴（位置改不了 → apply 会退到块模式）+ 开了 constrained_layout。
# 这两者叠加正是最该提示的组合：块的 set_position 与布局引擎的目标冲突。
CLAYOUT = textwrap.dedent('''
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(1, 2, figsize=(10, 4), constrained_layout=True)
    axes[0].plot([1, 2, 3])
    axes[1].plot([3, 2, 1])
    axes[0].set_title('left', fontsize=10.0)
    axes[1].set_title('right', fontsize=10.0)
    fig.savefig('out.png', dpi=60)
''')

# §12 用的夹具：显式固定了 xlim（autoscale 关掉）→ describe 才会采到范围
XLIM_DEMO = textwrap.dedent('''
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    fig = plt.figure(figsize=(6, 4))
    ax = fig.add_axes([0.12, 0.12, 0.80, 0.78])
    ax.plot([1, 2, 3])
    ax.set_xlim(-0.01, 1.01)
    fig.savefig('out.png', dpi=60)
''')


def main():
    print('§1 describe 输出干净 JSON')
    rc, out, err = run('describe', FIX)
    d = None
    try:
        d = json.loads(out)
        check(rc == 0, 'exit 0', rc)
        check(d['version'] == _params.SCHEMA_VERSION,
              'version = SCHEMA_VERSION(%d)' % _params.SCHEMA_VERSION,
              d.get('version'))
        check(d['script'] == 'synth_script.py', 'script 名', d.get('script'))
        check(isinstance(d['figsize_in'], list) and len(d['figsize_in']) == 2,
              'figsize_in 两项', d.get('figsize_in'))
        a0 = d['axes'][0]
        check(len(a0['pos']) == 4, 'ax0.pos 四项', a0.get('pos'))
        check(isinstance(a0['aspect_locked'], bool), 'aspect_locked 是 bool')
        check('title_fontsize' in a0 and 'grid' in a0, '含字号/grid 字段')
    except Exception as e:                                        # noqa: BLE001
        check(False, 'describe 输出可解析', '%s / %r' % (e, out[:120]))

    print('§2 describe --all-figs')
    rc, out, err = run('describe', FIX, '--all-figs')
    try:
        da = json.loads(out)
        check(da['n_figs'] == len(da['figures']), 'n_figs 与 figures 数一致',
              da.get('n_figs'))
        check(all('axes' in f for f in da['figures']), '每张图都带 axes')
    except Exception as e:                                        # noqa: BLE001
        check(False, '--all-figs 可解析', e)

    print('§3 describe --compact')
    _, full, _ = run('describe', FIX)
    _, comp, _ = run('describe', FIX, '--compact')
    check(len(comp) < len(full)
          and json.loads(comp)['version'] == _params.SCHEMA_VERSION,
          '紧凑版更短且合法', '%d vs %d' % (len(comp), len(full)))

    print('§4 schema')
    rc, out, err = run('schema')
    try:
        s = json.loads(out)
        check(s['$schema'].startswith('http'), '$schema 声明', s.get('$schema'))
        check('version' in s['required'], 'required 含 version')
        props = s['properties']['axes']['items']['properties']
        check('pos' in props and 'grid' in props and 'legend' in props,
              'axis 属性齐全', sorted(props)[:5])
        check(s.get('additionalProperties') is True,
              'additionalProperties=True（未知键忽略的兼容约定）')
        try:
            import jsonschema
            jsonschema.validate(json.loads(full), s)
            check(True, 'jsonschema 校验 describe 产物')
        except ImportError:
            print('  --  未装 jsonschema，跳过实际校验')
        except Exception as e:                                    # noqa: BLE001
            check(False, 'jsonschema 校验 describe 产物', e)
    except Exception as e:                                        # noqa: BLE001
        check(False, 'schema 可解析', e)

    print('§5 apply --json')
    # 夹具的参数文件放在 .tweak_params/ 里，而那个目录是「用户脚本目录的临时产物」，
    # 被 .gitignore 排除了 —— 所以它不会进仓库，CI 上 checkout 下来是空的，
    # 导致 apply 没有参数可预览（files 为空）。这里现场用 describe 生成一份，
    # 让本地与 CI 行为一致。§6/§7 用的是自己新建的临时脚本，不受影响。
    fix_pdir = os.path.join(os.path.dirname(FIX), '.tweak_params')
    fix_stem = os.path.splitext(os.path.basename(FIX))[0]
    fix_ppath = os.path.join(fix_pdir, fix_stem + '.json')
    if not os.path.exists(fix_ppath):
        os.makedirs(fix_pdir, exist_ok=True)
        _rc, _out, _err = run('describe', FIX)
        with open(fix_ppath, 'w', encoding='utf-8') as _f:
            _f.write(_out)
    rc, out, err = run('apply', FIX, '--json')
    try:
        r = json.loads(out)                     # stdout 必须只有 JSON
        check('ok' in r and 'files' in r, '含 ok/files', sorted(r))
        check(len(r['files']) >= 1, '预览也算一条结果', r['files'])
    except Exception as e:                                        # noqa: BLE001
        check(False, 'apply --json 输出可解析', '%s / %r' % (e, out[:120]))

    print('§6 闭环：describe → 改数字 → apply --write')
    tmp = tempfile.mkdtemp(prefix='mpltweak_agent_')
    try:
        script = os.path.join(tmp, 'fig.py')
        with open(script, 'w', encoding='utf-8') as f:
            f.write(DEMO)
        rc, out, err = run('describe', script)
        data = json.loads(out)
        check(data['axes'][0]['pos'] == [0.08, 0.55, 0.4, 0.35],
              'describe 读到原始位置', data['axes'][0]['pos'])
        # agent 改数字
        data['axes'][0]['pos'] = [0.10, 0.60, 0.35, 0.30]
        data['axes'][0]['title_fontsize'] = 14.0
        pdir = os.path.join(tmp, '.tweak_params')
        os.makedirs(pdir, exist_ok=True)
        with open(os.path.join(pdir, 'fig.json'), 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        # 本条测的是"原位写回 + 脚本零痕迹"，所以要显式指定 --style inplace：
        # 默认已是 auto（原位改不了会自动退到插入调整块），那会往脚本里留块。
        rc, out, err = run('apply', script, '--write', '--json',
                           '--style', 'inplace')
        res = json.loads(out)
        check(res['ok'], 'apply --write 成功', res['files'][0].get('reason'))
        check(res['files'][0].get('style') == 'inplace',
              '--json 如实回报 style（写回路径原先漏设 → 人读消息永远说"插入调整块"）',
              res['files'][0].get('style'))
        src = open(script, encoding='utf-8').read()
        check('[0.1, 0.6, 0.35, 0.3]' in src,
              '源码里的位置被原位改写', [l for l in src.splitlines()
                                        if 'add_axes' in l][:2])
        check('fontsize=14' in src, '字号被原位改写',
              [l for l in src.splitlines() if 'set_title' in l])
        check('verify' not in src and 'mpltweak' not in src,
              '脚本里没留下工具痕迹')
    except Exception as e:                                        # noqa: BLE001
        check(False, '闭环跑通', e)
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)

    print('§7 figsize_in 不受 dpi 影响')
    dpi_script = os.path.join(ROOT, 'tests', '.tmp_dpi.py')
    try:
        with open(dpi_script, 'w', encoding='utf-8') as f:
            f.write(textwrap.dedent('''
                import matplotlib
                matplotlib.use('Agg')
                import matplotlib as mpl
                mpl.rcParams['figure.dpi'] = 200     # 非默认 dpi
                import matplotlib.pyplot as plt
                fig = plt.figure(figsize=(8, 4))
                _ax = fig.add_axes([0.1, 0.1, 0.8, 0.8])
                _ax.plot([1, 2, 3])
                fig.savefig('dpi.png')
            '''))
        rc, out, err = run('describe', dpi_script)
        d = json.loads(out)
        check(d['figsize_px'] == [1600, 800], 'figsize_px 是 200dpi 的真实像素',
              d.get('figsize_px'))
        check(d['figsize_in'] == [8, 4],
              'figsize_in 仍是 8x4 英寸（没被 dpi 带偏）', d.get('figsize_in'))
    except Exception as e:                                        # noqa: BLE001
        check(False, '§7 执行', e)
    finally:
        for _p in (dpi_script, os.path.join(ROOT, 'tests', '.tweak_params',
                                            '.tmp_dpi.json'),
                   os.path.join(ROOT, 'tests', 'dpi.png')):
            try:
                os.remove(_p)
            except OSError:
                pass

    # ---- §8 多图：describe 必须自报 fig_index，apply 必须写到对的那张图 ----
    #   背景（t6 的 B1 / P0）：describe 产物曾**没有 fig_index**，而 apply 缺省按
    #   第 0 张处理 → 走 README 主推的 agent 闭环时，参数会静默落到**另一张图**上，
    #   还 rc=0、"✓ 语义验证通过"、零警告。这条用例就是把它钉死。
    print('§8 多图闭环：fig_index 必须贯穿 describe → apply')
    t8 = os.path.join(ROOT, 'tests', 'twofig_tmp.py')
    pdir8 = os.path.join(ROOT, 'tests', '.tweak_params')
    os.makedirs(pdir8, exist_ok=True)
    par8 = os.path.join(pdir8, 'twofig_tmp.json')
    try:
        with open(t8, 'w', encoding='utf-8', newline='') as f:
            f.write(textwrap.dedent('''
                import matplotlib
                matplotlib.use('Agg')
                import matplotlib.pyplot as plt

                fig = plt.figure(figsize=(6, 4))
                ax = fig.add_axes([0.10, 0.10, 0.40, 0.40])
                ax.set_title('first', fontsize=12)
                fig.savefig('a.png')

                fig2 = plt.figure(figsize=(5, 5))
                ax2 = fig2.add_axes([0.20, 0.20, 0.50, 0.50])
                ax2.set_title('second', fontsize=12)
                fig2.savefig('b.png')
            ''').lstrip())
        rc, out, err = run('describe', t8)
        d8 = json.loads(out)
        # describe 默认取最后一张 → 产物必须能自报家门，否则 apply 只能猜
        check(d8.get('fig_index') == 1,
              'describe 产物带 fig_index（默认最后一张 = 1）', d8.get('fig_index'))
        check(d8.get('n_figs') == 2, 'describe 产物带 n_figs=2', d8.get('n_figs'))

        # 改一个数字再喂回：这一改必须落到**第 2 张**图上
        d8['axes'][0]['pos'] = [0.25, 0.25, 0.5, 0.5]
        with open(par8, 'w', encoding='utf-8') as f:
            json.dump(d8, f, ensure_ascii=False)
        rc, out, err = run('apply', t8, '--write', '--style', 'inplace')
        src8 = open(t8, encoding='utf-8').read()
        # 按**数值**比对：no-op 优化后，值没变的数字不会被改写
        # （0.50 与 0.5 数值相同 → 保留原样），绑死文本会误判。
        _m8 = re.search(r'ax2 = fig2\.add_axes\(\[([^\]]*)\]\)', src8)
        _v8 = [float(x) for x in _m8.group(1).split(',')] if _m8 else []
        check(len(_v8) == 4 and all(abs(a - b) < 1e-9
                                   for a, b in zip(_v8, [0.25, 0.25, 0.5, 0.5])),
              '参数落到第 2 张图（目标图）',
              str([ln for ln in src8.splitlines() if 'add_axes' in ln]))
        check('ax = fig.add_axes([0.1, 0.1, 0.4, 0.4])' in src8
              or 'ax = fig.add_axes([0.10, 0.10, 0.40, 0.40])' in src8,
              '第 1 张图未被误改（这是 P0 的核心断言）',
              [ln for ln in src8.splitlines() if 'add_axes' in ln])
    except Exception as e:                                        # noqa: BLE001
        check(False, '§8 执行', e)
    finally:
        for _p in (t8, par8, os.path.join(ROOT, 'tests', 'a.png'),
                   os.path.join(ROOT, 'tests', 'b.png')):
            try:
                os.remove(_p)
            except OSError:
                pass

    print('§9 --json 成功路径的 stdout 必须是 UTF-8')
    # 这条断言测的就是**编码本身**，所以必须按字节捕获、自己解码：
    # 旧实现下 `sys.stdout.encoding` 是 cp936（管道被捕获时），JSON 里的中文
    # （schema 的 description / check 的 msg）会被编成 GBK —— 失败路径 fail_json
    # 走 fd 级 UTF-8 反而是好的，于是"同一条命令成功时解不开、失败时正常"。
    try:
        def run_raw(*args):
            # 显式把子进程 stdout 固定成 **cp936**（中国 Windows 的默认管道编码）：
            # 1) 这条断言测的契约就是"JSON 永远是 UTF-8，与终端代码页无关"，
            #    所以不能让宿主的 PYTHONIOENCODING/PYTHONUTF8 把它变成恒真；
            # 2) 在 UTF-8 locale 的 Linux CI 上，不固定编码就永远测不出旧 bug。
            _env = dict(os.environ, PYTHONIOENCODING='gbk', MPLBACKEND='Agg')
            _r = subprocess.run([PY, '-m', 'mpltweak.cli', *args], cwd=ROOT,
                                capture_output=True, env=_env)
            return _r.returncode, (_r.stdout or b''), (_r.stderr or b'')

        _rc, _raw, _err = run_raw('schema')
        try:
            _s = json.loads(_raw.decode('utf-8'))
            check(True, 'schema 的 stdout 能按 UTF-8 解码')
        except Exception as _e:                                  # noqa: BLE001
            _s = None
            check(False, 'schema 的 stdout 能按 UTF-8 解码', _e)
        check(_s is not None and '参数格式' in json.dumps(_s, ensure_ascii=False),
              'payload 里确实有中文（否则这条断言测不到编码）',
              (_s or {}).get('description'))

        t9 = os.path.join(ROOT, 'tests', '_jsonenc9.py')
        with open(t9, 'w', encoding='utf-8') as _f:
            _f.write(textwrap.dedent('''
                import matplotlib
                matplotlib.use('Agg')
                import matplotlib.pyplot as plt
                fig = plt.figure(figsize=(8, 6))
                _a = fig.add_axes([0.08, 0.55, 0.40, 0.38])
                _b = fig.add_axes([0.30, 0.60, 0.40, 0.30])
                fig.savefig('_jsonenc9.png', dpi=60)
            '''))
        try:
            _rc, _raw, _err = run_raw('check', t9, '--json')
            # 中文 msg（"重叠"）让它非 ASCII：旧实现下这行 decode 直接抛
            _d = json.loads(_raw.decode('utf-8'))
            check(any('重叠' in p.get('msg', '') for p in _d.get('problems', [])),
                  'check --json 的中文可按 UTF-8 解码', _d.get('problems'))
        except Exception as _e:                                  # noqa: BLE001
            check(False, 'check --json 的中文可按 UTF-8 解码', _e)
        finally:
            for _p in (t9, os.path.join(ROOT, 'tests', '_jsonenc9.png')):
                try:
                    os.remove(_p)
                except OSError:
                    pass
    except Exception as _e:                                      # noqa: BLE001
        check(False, '§9 执行', _e)

    print('§10 顶层兜底（guard_json_main）异常路径可用')
    # 原先 messages.py 用了 sys 却没 import：guard 写出了 JSON，随后在
    # `sys.stderr.write` 上抛 NameError → 人话提示、MPLTWEAK_DEBUG 的 traceback、
    # 以及本该 return 1 的收尾全部执行不到（异常直接穿出去）。
    try:
        _code = (
            'import sys\n'
            'sys.path.insert(0, %r)\n'
            'from mpltweak import messages as _msg\n'
            '@_msg.guard_json_main\n'
            'def boom(argv=None):\n'
            '    raise RuntimeError("boom")\n'
            'sys.exit(boom(["--json"]))\n'
        ) % os.path.join(ROOT, 'src')
        _r = subprocess.run([PY, '-c', _code], capture_output=True)
        _out = (_r.stdout or b'').decode('utf-8', 'replace')
        _errs = (_r.stderr or b'').decode('utf-8', 'replace')
        check(_r.returncode == 1, '退出码 1（不是被 NameError 带走）',
              _r.returncode)
        try:
            _d = json.loads(_out)
            check(_d.get('ok') is False
                  and _d.get('error_code') == 'internal_error',
                  '异常路径仍给出结构化 JSON', _d)
        except Exception as _e:                                  # noqa: BLE001
            check(False, '异常路径仍给出结构化 JSON', _e)
        # 只看 ASCII 片段：stderr 是 locale 编码（GBK），中文解码不可靠
        check('NameError' not in _errs and '[mpltweak]' in _errs
              and 'RuntimeError' in _errs,
              'stderr 打出了人话而不是 NameError', repr(_errs[-160:]))
    except Exception as _e:                                      # noqa: BLE001
        check(False, '§10 执行', _e)

    print('§11 布局引擎冲突要跟着 apply 的落地结果一起报出来')
    # 回归（2026-10-10 端到端冒烟发现）：原先用 `res['changes']` 当条件，而**块模式**
    # 的 changes 是空的 → "块 + constrained_layout"这个最该提示的组合反而一声不响。
    tmp11 = None
    try:
        tmp11 = tempfile.mkdtemp(prefix='mpltweak_layout_')
        s11 = os.path.join(tmp11, 'fig.py')
        with open(s11, 'w', encoding='utf-8') as f:
            f.write(CLAYOUT)
        rc, out, err = run('describe', s11)
        d11 = json.loads(out)
        for _a in d11['axes']:
            _a['title_fontsize'] = 14.0     # 只改字号：网格轴的位置本来就改不了
        pdir11 = os.path.join(tmp11, '.tweak_params')
        os.makedirs(pdir11, exist_ok=True)
        with open(os.path.join(pdir11, 'fig.json'), 'w', encoding='utf-8') as f:
            json.dump(d11, f, ensure_ascii=False, indent=2)

        rc, out, err = run('apply', s11, '--write', '--json')
        res11 = json.loads(out)['files'][0]
        check(res11.get('style') == 'block',
              '网格轴退到块模式（本用例的前提）', res11.get('style'))
        conf = res11.get('layout_conflicts') or []
        check(len(conf) >= 1 and 'constrained_layout' in conf[0],
              'JSON 的 layout_conflicts 带出了冲突', conf)

        rc, out, err = run('apply', s11, '--write')
        check('[!]' in out and 'constrained_layout' in out,
              '人读输出也提示了（带行号）', out[-500:])
    except Exception as _e:                                      # noqa: BLE001
        check(False, '§11 执行', _e)
    finally:
        if tmp11:
            shutil.rmtree(tmp11, ignore_errors=True)

    print('§12 轴范围 xlim：describe → 改数字 → apply --write → 语义验证')
    tmp12 = None
    try:
        tmp12 = tempfile.mkdtemp(prefix='mpltweak_xlim_')
        s12 = os.path.join(tmp12, 'fig.py')
        with open(s12, 'w', encoding='utf-8') as f:
            f.write(XLIM_DEMO)
        rc, out, err = run('describe', s12)
        d12 = json.loads(out)
        check(d12.get('version') == 4,
              'schema 版本 = 4（xlim/ylim 是 v4 新增字段）', d12.get('version'))
        check(d12['axes'][0].get('xlim') == [-0.01, 1.01],
              '显式固定过的 xlim 被采到（autoscale 的不采）',
              d12['axes'][0].get('xlim'))
        d12['axes'][0]['xlim'] = [0.0, 2.0]
        pdir12 = os.path.join(tmp12, '.tweak_params')
        os.makedirs(pdir12, exist_ok=True)
        with open(os.path.join(pdir12, 'fig.json'), 'w', encoding='utf-8') as f:
            json.dump(d12, f, ensure_ascii=False, indent=2)
        rc, out, err = run('apply', s12, '--write', '--json', '--style', 'inplace')
        res12 = json.loads(out)['files'][0]
        src12 = open(s12, encoding='utf-8').read()
        # 按**数值**比对，别绑字面量拼写（0.50 与 0.5 数值相同、写法不同）
        _m12 = re.search(r'set_xlim\(([^)]*)\)', src12)
        _v12 = ([float(x) for x in _m12.group(1).split(',')]
                if _m12 and ',' in _m12.group(1) else [])
        check(len(_v12) == 2 and all(abs(a - b) < 1e-9
                                    for a, b in zip(_v12, [0.0, 2.0])),
              '范围被原位改写',
              str([ln for ln in src12.splitlines() if 'set_xlim' in ln]))
        check(res12.get('semantic') is True,
              '语义验证通过（重跑后范围真的等于参数）', res12.get('semantic'))
        check(res12.get('verified') is True, '能跑通验证通过', res12.get('verified'))
    except Exception as _e:                                      # noqa: BLE001
        check(False, '§12 执行', _e)
    finally:
        if tmp12:
            shutil.rmtree(tmp12, ignore_errors=True)

    print('§13 快速验证档 --verify-fast 的契约')
    tmp13 = None
    try:
        tmp13 = tempfile.mkdtemp(prefix='mpltweak_fast_')
        s13 = os.path.join(tmp13, 'fig.py')

        def _setup13():
            with open(s13, 'w', encoding='utf-8') as f:
                f.write(XLIM_DEMO)
            _rc, _out, _err = run('describe', s13)
            _d = json.loads(_out)
            _d['axes'][0]['pos'] = [0.20, 0.20, 0.50, 0.50]
            _p = os.path.join(tmp13, '.tweak_params')
            os.makedirs(_p, exist_ok=True)
            with open(os.path.join(_p, 'fig.json'), 'w', encoding='utf-8') as f:
                json.dump(_d, f, ensure_ascii=False)

        _setup13()
        rc, out, err = run('apply', s13, '--write', '--verify-fast', '--json')
        r13 = json.loads(out)['files'][0]
        check(r13.get('verify_mode') == 'fast' and r13.get('verified') is None,
              '--json 如实回报 verify_mode=fast / verified=None（不谎称验证过）',
              (r13.get('verify_mode'), r13.get('verified')))

        _setup13()
        # 语言由测试自己钉住（见 run() 的说明）：两种语言都断言，两个分支都真被覆盖。
        # 之前只断言中文 -> CI 走英文时整条用例失败；而下面那条"不含中文"的断言在
        # 英文环境下是**恒真**的（等于没测），钉住 zh 之后它才真的有鉴别力。
        rc, out, err = run('apply', s13, '--write', '--verify-fast',
                           env={'MPLTWEAK_LANG': 'zh'})
        check('没有重跑脚本' in out and '不保证能跑通' in out,
              '人读输出（zh）用原话说清"没有重跑脚本"与不保证什么', out[-300:])
        check('Agg 重跑验证通过' not in out,
              '快速档不再打印"Agg 重跑验证通过"（zh）', out[-200:])
        _src13 = open(s13, encoding='utf-8').read()
        _m13 = re.search(r'add_axes\(\[([^\]]*)\]\)', _src13)
        _v13 = [float(x) for x in _m13.group(1).split(',')] if _m13 else []
        check(len(_v13) == 4 and all(abs(a - b) < 1e-9
                                    for a, b in zip(_v13, [0.2, 0.2, 0.5, 0.5])),
              '快速档仍然把改动写出去了', str(_v13))

        _setup13()          # 复位（否则第二次是 no_change、根本不会打印快速档说明）
        rc, out, err = run('apply', s13, '--write', '--verify-fast',
                           env={'MPLTWEAK_LANG': 'en'})
        check('NOT re-run' in out and 'does not prove it runs' in out,
              '人读输出（en）同样说清 "NOT re-run" 与不保证什么', out[-300:])
        check('Agg re-run check passed' not in out,
              '快速档不再打印英文的"Agg re-run check passed"', out[-200:])

        rc, out, err = run('apply', s13, '--write', '--no-verify', '--verify-fast')
        check(rc == 2, '--no-verify 与 --verify-fast 互斥 → rc=2', rc)
        # --json 下互斥错误也必须是可解析 JSON（独立审阅 F4：原先 stdout 是空的）
        rc, out, err = run('apply', s13, '--write', '--no-verify',
                           '--verify-fast', '--json')
        try:
            _d13 = json.loads(out)
            check(rc == 2 and _d13.get('ok') is False and _d13.get('error'),
                  '互斥错误在 --json 下给出可解析 JSON（ok=false + error）', out[:200])
        except Exception as _e:                              # noqa: BLE001
            check(False, '互斥错误在 --json 下给出可解析 JSON',
                  '%s / out=%r' % (_e, out[:160]))
    except Exception as _e:                                      # noqa: BLE001
        check(False, '§13 执行', _e)
    finally:
        if tmp13:
            shutil.rmtree(tmp13, ignore_errors=True)

    print('§14 apply --strict：有布局引擎冲突时拒绝写回（要放行得显式说）')
    tmp14 = None
    try:
        tmp14 = tempfile.mkdtemp(prefix='mpltweak_strict_')
        s14 = os.path.join(tmp14, 'fig.py')
        with open(s14, 'w', encoding='utf-8') as f:
            f.write(CLAYOUT)
        rc, out, err = run('describe', s14)
        d14 = json.loads(out)
        for _a in d14['axes']:
            _a['title_fontsize'] = 14.0
        p14 = os.path.join(tmp14, '.tweak_params')
        os.makedirs(p14, exist_ok=True)
        with open(os.path.join(p14, 'fig.json'), 'w', encoding='utf-8') as f:
            json.dump(d14, f, ensure_ascii=False)
        _before14 = open(s14, encoding='utf-8').read()
        rc, out, err = run('apply', s14, '--write', '--strict')
        check(rc == 2, '--strict 拒绝写回 → rc=2', rc)
        check(open(s14, encoding='utf-8').read() == _before14, '代码一个字节没动')
        check('--allow-layout-conflict' in err and 'constrained_layout' in err,
              'stderr 说清原因与放行办法', err[-220:])
        rc, out, err = run('apply', s14, '--write', '--strict',
                           '--allow-layout-conflict', '--style', 'block', '--json')
        check(rc == 0 and json.loads(out)['ok'],
              '显式 --allow-layout-conflict 后可以写回', rc)
    except Exception as _e:                                      # noqa: BLE001
        check(False, '§14 执行', _e)
    finally:
        if tmp14:
            shutil.rmtree(tmp14, ignore_errors=True)

    print()
    if _fails:
        print('FAILED: %d 项 -> %s' % (len(_fails), _fails))
        return 1
    print('ALL PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
