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
import importlib.util
import io
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

# GBK 控制台兜底（T13/T15）：CI 是 UTF-8 永远绿，本地 cp936 上一次 UnicodeEncodeError
# 就把整个套件从 ALL PASS 变成 rc=1 的假红（T13 实测：detail 里混进 U+FFFD）。
# 两层：① 输出流 errors='replace' 兜底；② 进 print 前先清洗（_gbk_safe）。
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(errors='replace')
    except (AttributeError, ValueError, OSError):
        pass          # 被重定向成不支持 reconfigure 的对象时忽略即可


def _gbk_safe(s):
    """把字符串清洗成"当前输出流编得出来"的形式（U+FFFD 之类换成 ?）。"""
    s = str(s)
    enc = getattr(sys.stdout, 'encoding', None) or 'utf-8'
    try:
        return s.encode(enc, 'replace').decode(enc, 'replace')
    except (LookupError, UnicodeDecodeError):
        return s.encode('ascii', 'backslashreplace').decode('ascii')


# 版本号一律引用 SCHEMA_VERSION，别写字面量 —— schema 升到 4 时这里曾经硬编码 3 而挂掉
sys.path.insert(0, os.path.join(ROOT, 'src'))
from mpltweak import params as _params                            # noqa: E402

_fails = []


def check(cond, label, extra=''):
    # 输出前清洗：extra 里可能混进子进程乱码（U+FFFD）→ GBK 控制台下 print 会崩（T13/T15）
    print('%s %s%s' % ('  OK  ' if cond else ' FAIL ', _gbk_safe(label),
                       '' if cond else '   ' + _gbk_safe(extra)))
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
        # CH-5 修法（0.1.9）：顶层 additionalProperties 由 True 收紧成 False，
        # 并把 `describe --all-figs` 的 {n_figs, figures} 写进 schema ——
        # 否则 schema 校验不了自己的产物（嵌套 axes 静默假通过）。
        check(s.get('additionalProperties') is False,
              'additionalProperties=False（顶层收紧，schema 校验自己的产物）')
        check('figures' in s['properties'],
              '--all-figs 的 figures 在 schema 里', sorted(s['properties'])[:8])
        check(s['properties']['axes']['items'].get('additionalProperties') is True,
              'axis 层仍宽松（未知轴字段继续被忽略）')
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
        # 0.1.10 外部改动守卫：files[] 上必须有**逐字布尔键**（agent 靠它判断
        # "这次没成是不是因为用户在验证窗口里自己保存过"）；正常路径恒为 false。
        check(all(f.get('external_modified') is False for f in r['files']),
              '每条 files[] 都带 external_modified 布尔键（正常路径 false）',
              [f.get('external_modified') for f in r['files']])
        # t45 / t39：这两个键**恒存在**（agent 不必再从 warnings 文本里猜
        # "这次为什么回退到两遍"）。预览没有写回、也没有重跑 → 0 / 空串，
        # 而且类型固定（int / str），不是缺键、也不是 None。
        check(all('verify_passes' in f and 'semantic_basis' in f
                  for f in r['files']),
              '每条 files[] 恒有 verify_passes + semantic_basis（t45）',
              [sorted(k for k in f if 'verif' in k or 'semantic' in k)
               for f in r['files']])
        check(all(isinstance(f.get('verify_passes'), int)
                  and not isinstance(f.get('verify_passes'), bool)
                  for f in r['files']),
              'verify_passes 类型是 int（不是 bool/None）',
              [f.get('verify_passes') for f in r['files']])
        check(all(f.get('verify_passes') == 0 for f in r['files']),
              '预览（没写回、没重跑）时 verify_passes=0',
              [f.get('verify_passes') for f in r['files']])
        check(all(f.get('semantic_basis') == '' for f in r['files']),
              '语义依据未知时 semantic_basis 是空串（不是缺键）',
              [f.get('semantic_basis') for f in r['files']])
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
        # t45 / t39：正常原位写回**只跑 1 遍**（这一遍同时回答"还跑得通吗"与
        # "改动真落到目标图上了吗"），并给出语义判定依据。
        check(res['files'][0].get('verify_passes') == 1,
              '正常原位写回只重跑 1 遍（verify_passes=1，t39 起的单遍口径）',
              res['files'][0].get('verify_passes'))
        check(res['files'][0].get('semantic_basis') in ('live', 'live+savefig'),
              '语义依据如实回报 live / live+savefig',
              res['files'][0].get('semantic_basis'))
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
        # 冲突解法：退出码取**合并后实测**值（集成树 rc=2；基线 0.1.8 是 1）——
        # T8 统一退出码矩阵后 internal_error 归 2，引擎副本缺该改动才写 1。
        # 解码器取 incoming 的 _dec()（T13 的候选编码解码，避免 U+FFFD 进 print）。
        _out = _dec(_r.stdout)
        _errs = _dec(_r.stderr)
        check(_r.returncode == 2,
              '退出码 2 = "跑不起来"（不是被 NameError 带走）', _r.returncode)
        try:
            _d = json.loads(_out)
            check(_d.get('ok') is False
                  and _d.get('error_code') == 'internal_error',
                  '异常路径仍给出结构化 JSON', _d)
            # 0.1.10 失败归因：internal_error = **工具自身**的错（tool_bug），
            # 必须与"脚本跑不起来"分开报 —— agent 凭 attribution 自动分支。
            check(_d.get('attribution') == 'tool_bug',
                  'internal_error 带 attribution=tool_bug（工具侧，不是脚本侧）', _d)
            # next_step 走机器可读通道 → 一律英文（t_en），不跟随界面语言。
            check(bool(_d.get('next_step'))
                  and all(ord(c) < 128 for c in _d['next_step']),
                  'next_step 是可直接执行的英文指引（机器可读通道不跟随语言）',
                  repr(_d.get('next_step'))[:120])
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
        # 0.1.10#3-①：describe 产物必须带 layout_engine（agent 读排版第一步
        # 就知道 constrained_layout 会覆盖位置，不用等 apply 才发现）。
        _le11 = d11.get('layout_engine') or []
        check(len(_le11) >= 1 and any(
            'constrained_layout' in (f.get('how') or '') for f in _le11),
            'describe 产物带 layout_engine（constrained_layout 冲突）', _le11)
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

        # 幂等回归（0.1.9 E1/E5 的修法）：同一份参数再跑一次，写出来的块与磁盘上
        # 的完全一致（只差说明行里的生成时间）→ 必须 no_change，人读输出不得再说
        # "已写回"（旧行为：又写一遍 + 再报一次 ok）。
        rc, out, err = run('apply', s11, '--write', '--json')
        _r11b = json.loads(out)['files'][0]
        check(_r11b.get('reason') == 'no_change',
              '重复 apply = no_change（幂等，不再重复落盘）', _r11b.get('reason'))
        check(_r11b.get('applied') is False,
              '重复 apply 不落盘（applied=False）', _r11b.get('applied'))

        # 人读输出的冲突提示：先把脚本复原（否则第二次是 no_change，按设计不报冲突 ——
        # "没落地就没冲突可言"），再跑一次真实写回。
        with open(s11, 'w', encoding='utf-8') as f:
            f.write(CLAYOUT)
        os.utime(os.path.join(pdir11, 'fig.json'), None)
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
        # t45：快速档**没有为验证跑脚本** → verify_passes=0 且语义无依据（空串）。
        # 这两个键仍然恒存在（消费方不必分支处理"键在不在"）。
        check(r13.get('verify_passes') == 0,
              '--verify-fast 没重跑 → verify_passes=0（不是缺键、不是 None）',
              r13.get('verify_passes'))
        check(r13.get('semantic_basis') == '',
              '--verify-fast 没有语义判定依据 → semantic_basis 空串',
              repr(r13.get('semantic_basis')))

        # ---- §13c 回退场景的机器可读取值（走 --json 用的同一段白名单）----
        # 为什么不用 CLI 测：`apply` 先要能**定位到图**才能走到验证层，而"取不到
        # 目标图状态"的脚本（OO 风格 Figure / 压根没画图）在更早的采集阶段就
        # no_fig 了 —— 拿不到那条路径（t39 已把它记成 0.1.11 的残留项）。
        # 所以这里从模块级入口（writeback + apply._emit_json）走同一段白名单代码：
        # 判据一字不放宽 —— 断言的是"回退被如实回报"，不是"回退也算通过"。
        print('§13c 取不到目标图状态 → 回退两遍：verify_passes=2 / 语义无依据 / 原因可见')
        tmp13c = None
        try:
            from mpltweak import apply as _apply13c
            from mpltweak import messages as _msg13c
            from mpltweak import writeback as _wb13c
            tmp13c = tempfile.mkdtemp(prefix='mpltweak_twopass_')
            s13c = os.path.join(tmp13c, 'oo.py')
            with open(s13c, 'w', encoding='utf-8') as f:
                f.write(textwrap.dedent('''
                    import matplotlib
                    matplotlib.use('Agg')
                    from matplotlib.figure import Figure
                    from matplotlib.backends.backend_agg import FigureCanvasAgg

                    fig = Figure(figsize=(8, 4))
                    ax = fig.add_axes([0.08, 0.55, 0.40, 0.35])
                    ax.plot([1, 2, 3], [4, 5, 6])
                    ax.set_title('t', fontsize=12)
                    FigureCanvasAgg(fig)
                    fig.savefig('oo.png', dpi=80)
                '''))
            _p13c = os.path.join(tmp13c, '.tweak_params')
            os.makedirs(_p13c, exist_ok=True)
            _pp13c = os.path.join(_p13c, 'oo.json')
            _d13c = {'fig_index': 0, 'axes': [{'index': 0,
                                               'title_fontsize': 21.0}]}
            with open(_pp13c, 'w', encoding='utf-8') as f:
                json.dump(_d13c, f, ensure_ascii=False)
            _res13c = _wb13c.writeback(s13c, _d13c, _pp13c, python=PY,
                                       timeout=180, style='inplace')
            check(_res13c.get('verify_passes') == 2,
                  '取不到状态 → writeback 如实回报 verify_passes=2（回退两遍）',
                  _res13c.get('verify_passes'))
            check(_res13c.get('verified') is None
                  and _res13c.get('semantic') is None,
                  '没验到就不许说验过：verified/semantic 都是 None',
                  (_res13c.get('verified'), _res13c.get('semantic')))
            check(any('回退' in str(w) for w in (_res13c.get('warnings') or [])),
                  '回退原因在 warnings 里可见（用户与 agent 都读得到）',
                  _res13c.get('warnings'))
            check('fontsize=21' in open(s13c, encoding='utf-8').read(),
                  '回退不代表放弃写回（字号仍然落盘）',
                  [ln.strip() for ln in open(s13c, encoding='utf-8')
                   if 'set_title' in ln])
            # 同一段白名单：emit_json 自己直写 fd1（UTF-8，绕过 redirect_stdout），
            # 所以这里替换 messages.emit_json 抓 payload —— 构造逻辑仍是产品代码。
            _cap13c = {}
            _orig13c = _msg13c.emit_json
            _msg13c.emit_json = lambda payload: _cap13c.update(payload)
            try:
                _apply13c._emit_json(s13c, [(_pp13c, _res13c)], sys.stdout,
                                     ok=True)
            finally:
                _msg13c.emit_json = _orig13c
            _f13c = _cap13c['files'][0]
            check(_f13c.get('verify_passes') == 2
                  and isinstance(_f13c.get('verify_passes'), int),
                  '--json 白名单里回退场景仍是 int 2（键恒存在、类型固定）',
                  _f13c.get('verify_passes'))
            check(_f13c.get('semantic_basis') == '',
                  '回退时 semantic_basis 是空串（从 None 归一，不是缺键）',
                  repr(_f13c.get('semantic_basis')))
            check(any('回退' in str(w) for w in (_f13c.get('warnings') or [])),
                  '回退原因也进了 --json 的 warnings（机器可读通道能追）',
                  _f13c.get('warnings'))
        except Exception as _e13c:                                # noqa: BLE001
            check(False, '§13c 回退场景执行', _e13c)
        finally:
            if tmp13c:
                shutil.rmtree(tmp13c, ignore_errors=True)

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

        rc, out, err = run('apply', s13, '--write', '--no-verify', '--verify-fast',
                           env={'MPLTWEAK_LANG': 'zh'})
        check(rc == 2, '--no-verify 与 --verify-fast 互斥 → rc=2', rc)
        # A1（t4）：互斥报错也必须把**后果**说全 —— 与 apply.py 用法行（:13）和
        # `apply --help`（:530-540）同款口径：只说"二选一"用户不知道这两档分别
        # 丢了哪层安全网。断言钉在 zh 文案上（界面上方那句是硬编码中文，不受
        # MPLTWEAK_LANG 影响；这里仍显式钉 zh，免得将来本地化后判据漂移）。
        check('不会自动回滚' in err and '唯一的' in err and '不重跑脚本' in err,
              '互斥报错补齐后果口径（不会自动回滚 / 唯一的自动安全网 / 不重跑脚本）',
              err[:240])
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

    print('§15 执行边界（A1/A2）：sys.exit() 的脚本不能把 describe --all-figs 打空')
    # 0.1.8：collect_all 缺 except SystemExit → `describe --all-figs` 对 sys.exit(0)
    # 的脚本 rc=0 且 **stdout 零字节**；sys.exit(3) → rc=3、SystemExit(7) → rc=7
    # （都不在文档退出码矩阵里）。MCP 的 describe_layout(all_figs=True) 更会把
    # server 进程直接带走（见 test_mcp 的同源用例）。
    tmp15 = None
    try:
        tmp15 = tempfile.mkdtemp(prefix='mpltweak_exit_')
        _head = ("import matplotlib\nmatplotlib.use('Agg')\n"
                 "import matplotlib.pyplot as plt\n"
                 "fig = plt.figure(figsize=(4, 3))\n"
                 "ax = fig.add_axes([0.15, 0.15, 0.7, 0.7])\n"
                 "ax.plot([1, 2], [1, 2])\n"
                 "ax.set_title('t')\n")
        for _name, _tail in (('exit0.py', 'import sys\nsys.exit(0)\n'),
                             ('exit3.py', 'import sys\nsys.exit(3)\n'),
                             ('sexit7.py', 'raise SystemExit(7)\n')):
            _p = os.path.join(tmp15, _name)
            with open(_p, 'w', encoding='utf-8') as f:
                f.write(_head + _tail)
            rc, out, err = run('describe', _p, '--all-figs')
            check(rc == 0, '%s → rc=0（退出码不被脚本顶掉）' % _name, rc)
            try:
                _d = json.loads(out)
                _ok = (_d.get('n_figs') == 1 and len(_d.get('figures') or []) == 1)
            except Exception as _e:                              # noqa: BLE001
                _d, _ok = None, False
                check(False, '%s → stdout 可解析 JSON' % _name, _e)
            else:
                check(_ok, '%s → 输出完整的 figures 包装' % _name, _d)
            rc1, out1, _ = run('describe', _p)
            check(rc1 == 0 and json.loads(out1).get('version'),
                  '%s → 单图路径同样正常（行为一致）' % _name, rc1)
    except Exception as _e:                                      # noqa: BLE001
        check(False, '§15 执行', _e)
    finally:
        if tmp15:
            shutil.rmtree(tmp15, ignore_errors=True)

    print('§16 执行边界（A1/A3）：同一进程连查多个脚本不再互相污染')
    # 0.1.8：check/describe/MCP 都在**工具自己的进程**里 runpy 跑用户脚本 →
    # sys.path 不回收（同一个脚本的判定随调用历史翻转）、sys.modules 陈旧
    # （改了 helper 仍读旧值）、rcParams 串味（12.0 → 26.4，污染值会被 apply 写进源码）。
    # 0.1.9 把执行挪进子进程；这里从库层面钉住"三路都不串味"。
    tmp16 = None
    try:
        tmp16 = tempfile.mkdtemp(prefix='mpltweak_iso_')
        _a = os.path.join(tmp16, 'A')
        _b = os.path.join(tmp16, 'B')
        os.makedirs(_a)
        os.makedirs(_b)
        with open(os.path.join(_a, 'only_a.py'), 'w', encoding='utf-8') as f:
            f.write('VALUE = "from-A"\n')
        _head = ("import matplotlib\nmatplotlib.use('Agg')\n"
                 "import matplotlib.pyplot as plt\n"
                 "fig = plt.figure(figsize=(4, 3))\n"
                 "ax = fig.add_axes([0.15, 0.15, 0.7, 0.7])\n"
                 "ax.plot([1, 2], [1, 2])\n")
        _bscript = os.path.join(_b, 'leak_b.py')
        with open(_bscript, 'w', encoding='utf-8') as f:
            f.write(_head + "import only_a\nax.set_title(only_a.VALUE)\n")
        _polluter = os.path.join(tmp16, 'polluter.py')
        with open(_polluter, 'w', encoding='utf-8') as f:
            f.write(_head + 'matplotlib.rcParams["font.size"] = 22\n')
        _clean = os.path.join(tmp16, 'clean.py')
        with open(_clean, 'w', encoding='utf-8') as f:
            f.write(_head + 'ax.set_title("t")\n')
        _driver = '''
import os, sys
sys.path.insert(0, "@SRC@")
os.environ["MPLBACKEND"] = "Agg"
from mpltweak import verify
path_before = list(sys.path)
mods_before = set(sys.modules)
B, A, POLL, CLEAN = r"@B@", r"@A@", r"@POLL@", r"@CLEAN@"
err_b = verify.collect_isolated(B)[1]
verify.collect_isolated(A)
err_b2 = verify.collect_isolated(B)[1]
s1 = verify.collect_isolated(CLEAN)[0]
verify.collect_isolated(POLL)
s3 = verify.collect_isolated(CLEAN)[0]
def title(s):
    return (s or {}).get("axes", [{}])[0].get("title_fontsize")
leftover = sorted(k for k in sys.modules if k not in mods_before
                  and not k.startswith("_") and k != "string")
print("RESULT " + "|".join([
    str("ModuleNotFoundError" in (err_b or "")),
    str("ModuleNotFoundError" in (err_b2 or "")),
    str(sys.path == path_before),
    repr(leftover),
    str(title(s1) == title(s3))]))
'''.replace('@SRC@', os.path.join(ROOT, 'src')).replace('@B@', _bscript) \
   .replace('@A@', _a).replace('@POLL@', _polluter).replace('@CLEAN@', _clean)
        _r = subprocess.run([PY, '-c', _driver], capture_output=True,
                            env=dict(os.environ, MPLBACKEND='Agg'))
        _out = (_r.stdout or b'').decode('utf-8', 'replace')
        _line = [l for l in _out.splitlines() if l.startswith('RESULT ')]
        if not _line:
            check(False, '隔离驱动跑通', _out[-300:] + (_r.stderr or b'')[-300:].decode('utf-8', 'replace'))
        else:
            _f = _line[0][len('RESULT '):].split('|')
            check(_f[0] == 'True', 'B 第一次：ModuleNotFoundError（B 目录没有 only_a）', _f)
            check(_f[1] == 'True',
                  '跑过 A 之后同一个 B **仍然** ModuleNotFoundError（判定不翻转）',
                  _f)
            check(_f[2] == 'True', 'sys.path 采集后原样还原', _f)
            check(_f[3] == '[]', 'sys.modules 没有新增用户脚本的模块', _f)
            check(_f[4] == 'True',
                  'rcParams 不串味：污染脚本前后 clean 的字号一致（12.0）', _f)
    except Exception as _e:                                      # noqa: BLE001
        check(False, '§16 执行', _e)
    finally:
        if tmp16:
            shutil.rmtree(tmp16, ignore_errors=True)

    print('§17 退出码矩阵（CH-6）：describe 的失败码与 check/apply 一致')
    _missing = os.path.join(tempfile.gettempdir(), 'mpltweak_definitely_missing.py')
    rc, out, err = run('describe', _missing)
    check(rc == 2, 'describe 找不到脚本 → rc=2（0.1.8 是 1）', rc)
    try:
        check(json.loads(out).get('error_code') == 'file_not_found',
              '结构化 JSON 仍给 error_code', out[:120])
    except Exception as _e:                                      # noqa: BLE001
        check(False, 'describe 失败仍给 JSON', _e)
    rc, out, err = run('check', _missing)
    check(rc == 2, 'check 找不到脚本 → rc=2（两边一致）', rc)

    print('§18 CH-9：schema 不再忽略参数（--help / 未知参数）')
    rc, out, err = run('schema')
    check(rc == 0 and json.loads(out).get('$schema'), 'schema 本身照常输出', rc)
    rc, out, err = run('schema', '--help')
    # 语言可能中英（CI 是 en、本地是 zh）——这里只断言"给的是用法、不是 JSON"
    check(rc == 0 and 'mpltweak schema' in (out + err)
          and not out.strip().startswith('{'),
          '--help → 用法，而不是原样吐 JSON', (rc, out[:60]))
    rc, out, err = run('schema', '--bogus', 'extra.py')
    check(rc == 2, '未知参数 → rc=2（0.1.8：rc=0 且照常吐 JSON）', rc)
    check(out.strip() == '', 'stdout 干净（错误走 stderr）', repr(out[:60]))
    rc, out, err = run('schema', '--compact')
    check(rc == 0 and json.loads(out).get('title'),
          '--compact 仍然可用', rc)

    print('§19 T26：describe 的图元字段（cmap / legend / lines）')
    # 0.1.8：白名单里 `lines` 硬编码 `[]`（图里明明有线却报"没有线"），cmap/legend
    # 压根没有键 —— 而 schema 里它们都是正式属性。这里钉住三件事：
    #   ① 采集侧真给值时，describe 要原样搬运（stub state 单测）；
    #   ② 采集侧没给（旧 state）时，三个键**不能出现**（不是空数组假字段）；
    #   ③ 端到端：带 colorbar / 带自定义 legend / 普通折线三种脚本都过 schema。
    from mpltweak.describe import _to_params
    _stub_base = {'index': 0, 'pos': [0.1, 0.1, 0.8, 0.8], 'grid': True,
                  'spines': {}, 'xscale': 'linear', 'yscale': 'linear'}
    _old = _to_params({'axes': [dict(_stub_base)]}, 'x.py')['axes'][0]
    check(not any(k in _old for k in ('cmap', 'legend', 'lines')),
          '旧 state（采集侧没给）→ 三个键都不出现（不是 lines: [] 假字段）',
          sorted(k for k in _old if k in ('cmap', 'legend', 'lines')))
    _stub = dict(_stub_base, _cmap='viridis',
                 _legend={'labels': ['a'], 'n_handles': 1, 'fontsize': 8.0},
                 _lines=[{'index': 0, 'linewidth': 2.5, 'color': '#d62728'}])
    _new = _to_params({'axes': [_stub]}, 'x.py')['axes'][0]
    check(_new.get('cmap') == 'viridis'
          and _new.get('legend') == {'labels': ['a'], 'n_handles': 1,
                                     'fontsize': 8.0}
          and _new.get('lines') == [{'index': 0, 'linewidth': 2.5,
                                     'color': '#d62728'}],
          '新 state → 三个键被原样搬运', {k: _new.get(k) for k in
                                         ('cmap', 'legend', 'lines')})

    try:
        import jsonschema                                              # noqa: F401
        _have_js = True
    except ImportError:                    # CI 只装 matplotlib/numpy/mcp
        _have_js = False
    rc, out, err = run('schema')
    _schema = json.loads(out)
    _HEAD = ("import matplotlib\nmatplotlib.use('Agg')\n"
             "import matplotlib.pyplot as plt\n")
    _CASES = {
        'colorbar': _HEAD + (
            'fig, ax = plt.subplots(figsize=(6, 4))\n'
            'im = ax.imshow([[1, 2], [3, 4]], cmap="viridis")\n'
            'fig.colorbar(im, ax=ax)\n'
            'ax.plot([1, 2, 3], linewidth=2.5, color="#d62728")\n'),
        'legend': _HEAD + (
            'fig, ax = plt.subplots(figsize=(6, 4))\n'
            'ax.plot([1, 2, 3], label="alpha")\n'
            'ax.plot([3, 2, 1], label="beta")\n'
            'ax.plot([2, 2, 2], linestyle="--")\n'
            'ax.legend(loc="upper left", fontsize=9)\n'),
        'plain': _HEAD + (
            'fig, ax = plt.subplots(figsize=(6, 4))\n'
            'ax.plot([1, 2, 3], linewidth=1.0)\n'),
    }
    _tmp19 = tempfile.mkdtemp(prefix='mpltweak_t26_')
    try:
        _got = {}
        for _name, _body in _CASES.items():
            _p = os.path.join(_tmp19, _name + '.py')
            with open(_p, 'w', encoding='utf-8') as f:
                f.write(_body + 'fig.savefig("o.png")\n')
            rc, out, err = run('describe', _p)
            _d = json.loads(out)
            _got[_name] = _d
            check(rc == 0 and _d['axes'], '%s：describe 正常' % _name, rc)
            if _have_js:
                try:
                    jsonschema.validate(_d, _schema)
                    check(True, '%s：产物通过 mpltweak schema' % _name)
                except Exception as _e:                                # noqa: BLE001
                    check(False, '%s：产物通过 mpltweak schema' % _name, _e)
            else:
                check(True, '%s：（未装 jsonschema，跳过真校验）' % _name)
        _cb = _got['colorbar']['axes'][0]
        check(_cb.get('cmap') == 'viridis',
              'colorbar 图：ax0 采到 cmap=viridis', _cb.get('cmap'))
        check(_cb.get('lines') and _cb['lines'][0].get('index') == 0
              and _cb['lines'][0].get('linewidth') == 2.5
              and _cb['lines'][0].get('color') == '#d62728',
              'colorbar 图：lines 是真实线（index/linewidth/color 齐全，不再恒 []）',
              _cb.get('lines'))
        _lg = _got['legend']['axes'][0].get('legend') or {}
        # 关键：用**图例自身内容**。ax.get_legend_handles_labels() 会把没进图例的
        # 第三条（无 label 的虚线）也算进来（GUI 侧 #2c 同款坑）。
        check(_lg.get('labels') == ['alpha', 'beta'] and _lg.get('n_handles') == 2,
              'legend 图：条目文本/句柄数来自图例自身（无 label 的线不进图例）', _lg)
        check(_lg.get('fontsize') == 9.0 and _lg.get('loc') == 'upper left',
              'legend 图：字号与标准位名也采到', _lg)
        _pl = _got['plain']['axes'][0]
        check(_pl.get('lines') and len(_pl['lines']) == 1
              and _pl['lines'][0].get('color') == '#1f77b4',
              '普通折线图：lines 有 1 项、颜色归一成 #rrggbb', _pl.get('lines'))
        check('cmap' not in _pl and 'legend' not in _pl,
              '没有图像/图例的轴：这两键不出现（不是 null 占位）', sorted(_pl))
        rc, out, err = run('describe', os.path.join(_tmp19, 'legend.py'),
                           '--all-figs')
        if _have_js:
            try:
                jsonschema.validate(json.loads(out), _schema)
                check(True, '--all-figs 产物同样通过 schema')
            except Exception as _e:                                    # noqa: BLE001
                check(False, '--all-figs 产物同样通过 schema', _e)
    except Exception as _e:                                            # noqa: BLE001
        check(False, '§19 执行', _e)
    finally:
        shutil.rmtree(_tmp19, ignore_errors=True)

    print('§20 T26/T25：字号 x/y 分列（tick_fontsize_y）与 compare 的独立口径')
    # 背景：GUI 导出侧从 0.1.9 起分列输出 tick_fontsize(x) / tick_fontsize_y(y)，
    # 写回侧由引擎线分列落地。语义比对**必须**逐字段回比 —— 只比 x 的话，
    # "只改了 y 刻度字号"这件事在验证里完全隐形（假通过）。
    from mpltweak import verify as _verify
    _tmp20 = tempfile.mkdtemp(prefix='mpltweak_t26y_')
    try:
        _p20 = os.path.join(_tmp20, 'y.py')
        with open(_p20, 'w', encoding='utf-8') as f:
            f.write("import matplotlib\nmatplotlib.use('Agg')\n"
                    "import matplotlib.pyplot as plt\n"
                    "fig, ax = plt.subplots(figsize=(6, 4))\n"
                    "ax.plot([1, 2, 3])\n"
                    "ax.tick_params(axis='x', labelsize=9)\n"
                    "ax.tick_params(axis='y', labelsize=13)\n"
                    "ax.set_xlabel('X', fontsize=8)\n"
                    "ax.set_ylabel('Y', fontsize=15)\n"
                    "fig.savefig('o.png')\n")
        rc, out, err = run('describe', _p20)
        _a20 = json.loads(out)['axes'][0]
        check(_a20.get('tick_fontsize') == 9.0
              and _a20.get('tick_fontsize_y') == 13.0,
              'describe 采集 x/y 刻度字号（9 / 13，不再只有 x）',
              (_a20.get('tick_fontsize'), _a20.get('tick_fontsize_y')))
        check(_a20.get('label_fontsize') == 8.0
              and _a20.get('label_fontsize_y') == 15.0,
              'describe 采集 x/y 轴标签字号（8 / 15）',
              (_a20.get('label_fontsize'), _a20.get('label_fontsize_y')))

        def _mk(idx=0, **kw):
            base = {'index': idx, 'pos': [0.1, 0.1, 0.8, 0.8],
                    'xscale': 'linear', 'yscale': 'linear', 'grid': True,
                    'spines': {}, 'cell': None}
            base.update(kw)
            return base
        check(_verify.compare([_mk(tick_fontsize=9, tick_fontsize_y=12)],
                              [_mk(tick_fontsize=9, tick_fontsize_y=13)]),
              '只改 y 刻度字号（x 相同）→ compare 必须报出 tick_fontsize_y',
              _verify.compare([_mk(tick_fontsize=9, tick_fontsize_y=12)],
                              [_mk(tick_fontsize=9, tick_fontsize_y=13)]))
        check(not _verify.compare([_mk(tick_fontsize=9, tick_fontsize_y=13)],
                                  [_mk(tick_fontsize=9, tick_fontsize_y=13)]),
              'x/y 都对 → 不报')
        # T73（零兼容）：参数里**没带** label_fontsize_y → **只按 x 比** ——
        # 不再拿实测的 y 当合法值（旧口径会让"只改了 y"在语义验证里完全隐形）。
        check(_verify.compare([_mk(label_fontsize=15)],
                              [_mk(label_fontsize=8, label_fontsize_y=15)]),
              'T73（零兼容）：参数没带 label_fontsize_y → 只按 x 比（x 不同必须报）')
        check(_verify.compare([_mk(label_fontsize=8, label_fontsize_y=20)],
                              [_mk(label_fontsize=8, label_fontsize_y=15)]),
              '新参数带 label_fontsize_y：y 独立比（不同 → 报）')
        check(_verify.compare([_mk(label_fontsize=20, label_fontsize_y=15)],
                              [_mk(label_fontsize=8, label_fontsize_y=15)]),
              '新参数带 label_fontsize_y：x 独立比（不同 → 报）')

        # 端到端（真起子进程重跑脚本 + 逐字段回比）：参数说 y=12、脚本实际 13
        _data20 = {'version': _params.SCHEMA_VERSION,
                   'script': os.path.basename(_p20), 'figsize_in': [6.0, 4.0],
                   'axes': [_mk(tick_fontsize=9, tick_fontsize_y=12,
                                label_fontsize=8, label_fontsize_y=15,
                                pos=[0.125, 0.11, 0.775, 0.77], grid=False)]}
        _sok, _detail = _verify.verify_semantic(_p20, _data20)
        check(_sok is False and 'tick_fontsize_y' in (_detail or ''),
              '语义验证端到端覆盖 tick_fontsize_y（不再假通过）',
              (_sok, _detail))
    except Exception as _e:                                            # noqa: BLE001
        check(False, '§20 执行', _e)
    finally:
        shutil.rmtree(_tmp20, ignore_errors=True)

    print('§21 失败归因：脚本跑不起来 vs 工具改坏了，四类各给一句下一步')
    # 背景（独立评审 + 用户 2026-10-12 拍板）：写回验证失败时用户只看到
    # 「验证失败，已回滚」—— 不知道**是谁的错**、也不知道下一步做什么。这里把
    # 「你的脚本跑不起来」和「工具把代码改坏了」彻底分开报，并各给一句可照抄的
    # 下一步；--json 用 attribution 给 agent 自动分支。
    # 退出码数值语义**不变**（0.1.9 已对外文档化，用户 CI 依赖）——只改文案与字段。
    _tmp21 = tempfile.mkdtemp(prefix='mpltweak_attr_')
    _made21 = []
    _env21 = dict(os.environ)
    _env21['PYTHONPATH'] = os.path.join(ROOT, 'src')

    def _has21(name):
        """这个名字在本环境此刻能不能被导入（只用来判断夹具是否成立）。"""
        try:
            return importlib.util.find_spec(name) is not None
        except (ImportError, ValueError):
            return False

    def _mk21(name, body, params=None):
        """造脚本；params 给了就同时写 `.tweak_params/<stem>.json`。"""
        p = os.path.join(_tmp21, name)
        with open(p, 'w', encoding='utf-8', newline='') as f:
            f.write(body)
        _made21.append(p)
        if params is not None:
            d = os.path.join(_tmp21, '.tweak_params')
            os.makedirs(d, exist_ok=True)
            # 用 `w` 重写 → mtime 变新：否则上一次失败写回留下的"脚本被外部
            # 修改过"判定会让第二次调用直接 rc=1 reason=stale（不是归因路径）。
            with open(os.path.join(d, name[:-3] + '.json'), 'w',
                      encoding='utf-8') as f:
                f.write(params)
        return p

    def _apply21(script, extra=()):
        """真跑一次 `apply --write --style inplace --json`。

        每次调用前重写参数文件（见 _mk21 的注释），所以同一个脚本可以跑多次。
        """
        _e = dict(_env21)
        if extra:
            _e.update(extra)
        _stem = os.path.basename(script)[:-3]
        _pd = os.path.join(_tmp21, '.tweak_params')
        os.makedirs(_pd, exist_ok=True)
        _pj = os.path.join(_pd, _stem + '.json')
        _cur = ''
        try:
            with open(_pj, 'r', encoding='utf-8') as f:
                _cur = f.read()
        except OSError:
            pass
        if _cur:
            with open(_pj, 'w', encoding='utf-8') as f:
                f.write(_cur)
        r = subprocess.run([PY, '-m', 'mpltweak.cli', 'apply', script,
                            '--write', '--style', 'inplace', '--json'],
                           cwd=_tmp21, capture_output=True, env=_e)
        _o, _er = _dec(r.stdout), _dec(r.stderr)
        try:
            _j = json.loads(_o)
        except Exception:                                         # noqa: BLE001
            _j = {}
        _f0 = (_j.get('files') or [{}])[0]
        return r.returncode, _f0, _er

    _move21 = ('{"version": %d, "fig_index": 0, "figsize_in": [6.0, 4.0], '
               '"axes": [{"index": 0, "pos": [0.2, 0.2, 0.5, 0.5], '
               '"xscale": "linear", "yscale": "linear", "grid": false, '
               '"spines": {}, "cell": null}]}' % _params.SCHEMA_VERSION)
    _head21 = ("import matplotlib\nmatplotlib.use('Agg')\n"
               "import matplotlib.pyplot as plt\n"
               "fig = plt.figure(figsize=(6, 4))\n"
               "ax = fig.add_axes([0.10, 0.10, 0.40, 0.40])\n"
               "ax.plot([1, 2, 3])\n")

    def _attr_lines(text):
        """只取归因那两行（第 2 段）—— 上面通用的 write_failed 文案里本来就写着
        "缺数据/依赖"（writeback 的 Case A 笼统文案），拿整段 stderr 断言"不含缺数据"
        会误伤，所以判据只看分类本身。"""
        _i = text.find('归因：')
        return text[_i:] if _i >= 0 else ''

    try:
        # ---- ① script_cannot_run：脚本自己缺依赖 ----
        # 事后校验"这个名字此刻真的导不进来"—— 版本太新的环境里刚好装了同名包时
        # 用例自降为跳过，而不是给出一个假的绿色。
        if _has21('t11_gone_pkg'):
            print('  --  跳过：t11_gone_pkg 在本环境可被导入，缺依赖夹具造不出来')
        else:
            _s1 = _mk21('attr_missing_dep.py',
                        _head21 + 'import t11_gone_pkg\n'
                        "fig.savefig('o.png')\n", _move21)
            _rc1, f1, e1 = _apply21(_s1)
            _a1 = _attr_lines(e1)
            check(f1.get('attribution') == 'script_cannot_run',
                  '①缺依赖 → attribution=script_cannot_run', f1.get('attribution'))
            check(f1.get('attribution_detail') in ('missing_dependency',
                                                   'missing_data', 'unknown'),
                  '①缺依赖 → detail 是稳定英文短原因', f1.get('attribution_detail'))
            check('脚本跑不起来' in _a1,
                  '①人读输出印出分类「脚本跑不起来」', _a1[-300:])
            check('已回滚' in _a1 and 'mpltweak check' in _a1 and '--static' in _a1,
                  '①下一步：明说没改源码 + `check <脚本> --static`', _a1[-300:])
            check((f1.get('next_step') or '').endswith('--static'),
                  '①--json 的 next_step 可直接执行', f1.get('next_step'))
            check('需要参数' not in _a1,
                  '①缺依赖**不**被伪装成"需要参数"（不许乱猜）', _a1[:200])

        # ---- ② script_needs_args：脚本依赖 sys.argv，本次没透传 ----
        _s2 = _mk21('attr_needs_args.py',
                    _head21 + "import sys\ndata = sys.argv[1]\n"
                    "fig.savefig('o.png')\n", _move21)
        _rc2, f2, e2 = _apply21(_s2)
        _a2 = _attr_lines(e2)
        check(f2.get('attribution') == 'script_needs_args',
              '②脚本要 argv → attribution=script_needs_args', f2.get('attribution'))
        # ★ 评审点名的伪装问题：脚本需要参数时**不能说成"缺数据"**
        check('缺数据' not in _a2 and '缺依赖' not in _a2,
              '②★「需要参数」不得伪装成「缺数据/缺依赖」', _a2[-300:])
        check('脚本需要命令行参数' in _a2, '②人读输出印出分类「脚本需要命令行参数」',
              _a2[-300:])
        check('你的脚本需要参数' in _a2 and '-- <' in _a2,
              '②下一步给出可照抄的 `apply <脚本> --write -- <参数...>`（原文含 `<`）',
              _a2[-300:])
        check('<your args...>' in (f2.get('next_step') or ''),
              '②--json 的 next_step 带 `-- <your args...>` 写法',
              f2.get('next_step'))

        # ---- ③ script_needs_gui_or_headless_impossible：要开窗口，无头跑不通 ----
        # 夹具如实模拟"本机没有可用窗口"：真 import tkinter（静态证据来源），而
        # 打不开窗口时的报错文本与 Linux/CI 无头机上一模一样。
        #
        # ★ 团队硬规则（船长 2026-10-12，用户报"有人抢鼠标"后定）：测试夹具
        #   **禁止创建任何真实 GUI 对象** —— tk.Tk() / Toplevel() / QApplication() /
        #   QWidget() / plt.show() / use('TkAgg') 后建图 一律不许。tkinter 直接建
        #   窗口，MPLBACKEND=Agg **挡不住**，会真抢焦点与鼠标。需要"脚本里有 GUI
        #   调用"这类证据时，只用**静态文本/AST 层面的构造**（写进临时文件的源码
        #   字符串），不要真执行。
        #
        # 在真有窗口的机器上，MPLTWEAK_GUI_OK=1 就跳过这段 → 反向对照证明红/绿
        # 来自分类而不是夹具恒坏。
        _s3 = _mk21(
            'attr_needs_gui.py',
            _head21 + (
                "import os\n"
                "import tkinter as tk        # noqa: F401  真需要窗口的脚本\n"
                "if not os.environ.get('MPLTWEAK_GUI_OK'):\n"
                "    raise SystemExit('TclError: no display name and no '\n"
                "                     '$DISPLAY environment variable')\n"
                "fig.savefig('o.png')\n"), _move21)
        _rc3, f3, e3 = _apply21(_s3)
        _a3 = _attr_lines(e3)
        check(f3.get('attribution') == 'script_needs_gui_or_headless_impossible',
              '③无头跑不通 → attribution=script_needs_gui_or_headless_impossible',
              f3.get('attribution'))
        check('无头环境跑不通' in _a3, '③人读输出印出分类「无头环境跑不通」', _a3[-300:])
        check('mpltweak check' in _a3 and '--static' in _a3
              and '--verify-fast' in _a3 and '--keep-unverified' in _a3,
              '③下一步：--static + 确要写回时的两个弱保证开关', _a3[-400:])
        # 反向对照：同一条路径在"有窗口"时应当**正常写回**。
        _rc3b, _f3b, _e3b = _apply21(_s3, extra={'MPLTWEAK_GUI_OK': '1'})
        check(_f3b.get('reason') != 'fail',
              '③反向对照：给了窗口后同一脚本正常写回（不是夹具恒失败）',
              (_rc3b, _f3b.get('reason'), _e3b[-160:]))

        # ---- ④ tool_bug：internal_error（工具自身异常），与脚本失败彻底分开 ----
        _code21 = (
            'import sys\n'
            'sys.path.insert(0, %r)\n'
            'from mpltweak import messages as _msg\n'
            '@_msg.guard_json_main\n'
            'def boom(argv=None):\n'
            '    raise RuntimeError("boom-t11")\n'
            'sys.exit(boom(["--json"]))\n'
        ) % os.path.join(ROOT, 'src')
        _r4 = subprocess.run([PY, '-c', _code21], capture_output=True)
        _o4, _e4 = _dec(_r4.stdout), _dec(_r4.stderr)
        check(_r4.returncode == 2, '④工具自身异常 → 退出码仍是 2（方案 A 统一后不变）',
              _r4.returncode)
        try:
            _j4 = json.loads(_o4)
        except Exception as _e:                                   # noqa: BLE001
            _j4 = {}
        check(_j4.get('error_code') == 'internal_error'
              and _j4.get('attribution') == 'tool_bug',
              '④--json：error_code=internal_error + attribution=tool_bug', _j4)
        check('一个字节都未修改' in _e4 and '这是工具的 bug' in _e4,
              '④人读输出明说「源码一个字节都未修改」+「这是工具的 bug」', _e4[-200:])

        # ---- ⑤ cli 帮助：合并表述消失（脚本侧 / 工具侧分开写）----
        _u = subprocess.run([PY, '-m', 'mpltweak.cli', '--help'],
                            capture_output=True, env=dict(os.environ))
        _ut = _dec(_u.stdout) + _dec(_u.stderr)
        check('脚本报错或工具自身异常' not in _ut,
              '⑤cli 帮助不再出现"脚本报错或工具自身异常"的合并表述')
        check('internal_error' in _ut and '与你的脚本无关' in _ut,
              '⑤cli 帮助把工具侧 internal_error 单列并点明与用户脚本无关',
              _ut[_ut.find('工具侧'):][:120] if '工具侧' in _ut else _ut[:120])
        check('tool_bug' in _ut and 'script_cannot_run' in _ut,
              '⑤cli 帮助列出 attribution 的取值（agent 可据此分支）')
        # t58（方案 A）：cli 帮助改成 0 / 1 / 2 / 4 且写明 3 已退役 —— 判据从
        # "0.1.9 的旧表述还在"改为"新契约三件都写全"（口径统一，不是软化：
        # 1 = 发现问题、2 = 跑不起来、非零仍表示失败）。
        # F2（t4）：版本标注 = **实际随哪个版本发布** —— 这批退出码改动是在 0.1.10
        # 发布之后做的（仍在工作区未提交），所以帮助里必须写 0.1.11（先前误写 0.1.10）。
        check('0.1.11' in _ut and '0 / 1 / 2 / 4' in _ut,
              '⑤cli 帮助声明统一后的退出码集合 0 / 1 / 2 / 4（版本标注=0.1.11）',
              _ut[_ut.find('退出码'):][:140] if '退出码' in _ut else _ut[:140])
        check('已退役' in _ut and '3' in _ut,
              '⑤cli 帮助注明 0.1.9 的退出码 3 已退役、不再由任何路径产生')
        check('1 = 发现问题' in _ut and '2 = 跑不起来' in _ut,
              '⑤cli 帮助保留 1 / 2 的语义（CI 依赖：非零仍是失败）')

        # ---- ⑥ 英文分支（文案走 messages.py，中英各一份）----
        _s6 = _mk21('attr_needs_args_en.py',
                    _head21 + "import sys\ndata = sys.argv[1]\n"
                    "fig.savefig('o.png')\n", _move21)
        _rc6, f6, e6 = _apply21(_s6, extra={'MPLTWEAK_LANG': 'en'})
        check('script needs command-line arguments' in e6,
              '⑥英文界面：归因分类也出英文', e6[-300:])
        check(all(ord(c) < 128 for c in (f6.get('next_step') or 'x')),
              '⑥英文下 next_step 仍是 ASCII 可执行命令', f6.get('next_step'))
        check(f6.get('attribution') == 'script_needs_args',
              '⑥英文分支的 attribution 取值与中文一致（机器可读值不翻译）',
              f6.get('attribution'))

        # ---- ⑦ 纯函数判据（不跑子进程）：保守性 + 不误报 tool_bug ----
        from mpltweak import verify as _verify21
        _noisy = os.path.join(_tmp21, 'static_probe.py')
        with open(_noisy, 'w', encoding='utf-8') as _f:
            _f.write('import sys\n_x = sys.argv[1]\n')
        check(_verify21.classify_verify_failure(
            "usage: p.py [-h]\nerror: the following arguments are required: --data",
            script_path=_noisy, args_passed=False)
            == (_verify21.VERIFY_FAIL_NEEDS_ARGS, 'argparse'),
            '⑦stderr 命中 argparse 强证据 → script_needs_args/argparse')
        # 已经传过参数、重跑仍报用法错误 → 仍归 needs_args，但 detail 换成
        # configured_args（apply 换一句"参数已透传"的文案，不再叫你再传一遍）。
        check(_verify21.classify_verify_failure(
            'error: the following arguments are required: --data',
            script_path=_noisy, args_passed=True)
            == (_verify21.VERIFY_FAIL_NEEDS_ARGS, 'configured_args'),
            '⑦参数已透传时不再叫他"去传参数"（detail=configured_args）')
        # 保守性：无 stderr 且参数已给 → **不**归 needs_args（不强猜）
        check(_verify21.classify_verify_failure(
            '', script_path=_noisy, args_passed=True)
            == (_verify21.VERIFY_FAIL_CANNOT_RUN, 'unknown'),
            '⑦无 stderr + 参数已给 → 保守归 script_cannot_run/unknown')
        check(_verify21.classify_verify_failure('', script_path=_noisy,
                                               args_passed=False)
              == (_verify21.VERIFY_FAIL_NEEDS_ARGS, 'static_args'),
              '⑦无 stderr + sys.argv[1] 强证据 + 未传参 → static_args')
        # 判不出时**绝不**误报 tool_bug（会把用户吓到）
        _kinds21 = set()
        for _t in ('', 'Traceback (most recent call last):\nValueError: nope',
                   'TclError: no display name and no $DISPLAY environment '
                   'variable', 'ModuleNotFoundError: No module named "pandas"',
                   'FileNotFoundError: [Errno 2] data.csv'):
            _kinds21.add(_verify21.classify_verify_failure(
                _t, script_path=_noisy, args_passed=False)[0])
        check(_verify21.VERIFY_FAIL_TOOL_BUG not in _kinds21,
              '⑦分类器永不从脚本 stderr 判出 tool_bug（只有 internal_error 才是）',
              sorted(_kinds21))
        check(_kinds21 <= {_verify21.VERIFY_FAIL_CANNOT_RUN,
                           _verify21.VERIFY_FAIL_NEEDS_ARGS,
                           _verify21.VERIFY_FAIL_NEEDS_GUI},
              '⑦分类器只产出三类（工具侧那类由 guard 产出）', sorted(_kinds21))
    except Exception as _e:                                       # noqa: BLE001
        check(False, '§21 执行', _e)
    finally:
        shutil.rmtree(_tmp21, ignore_errors=True)

    print('§22 可重跑预检：写回/开窗前静态预警五类重跑风险（绝不执行脚本）')
    # 背景（用户视角评审 2026-10-12 拍板进 0.1.10）：本工具的安全网要求**在你的环境里
    # 重跑脚本**（0.1.10 起写回默认只重跑 1 遍，取不到目标图状态时才回退 2 遍），于是
    # 「要参数 / 要联网 / 等人输入 /
    # 要弹窗 / 缺依赖」的脚本会无声撞墙，用户事后归因成"这工具不行"。
    # t11 管的是**失败之后**怎么说（四类归因），本节钉住**失败之前**的预警：
    #   ① 五类各一条：人读提示（含可照抄的下一步）+ --json 的 preflight.risks；
    #   ② **干净脚本必须一个字都不说**（不能变成新噪音）；
    #   ③ 预检自身异常一律当无风险放行（绝不成为新的失败点）；
    #   ④ 预检**绝不执行脚本**（金丝雀：脚本里的模块级写盘不得发生）。
    from mpltweak import preflight as _pf22
    from mpltweak import verify as _v22
    from mpltweak import messages as _messages22
    _tmp22 = tempfile.mkdtemp(prefix='mpltweak_preflight_')
    _p22 = {'version': _params.SCHEMA_VERSION, 'fig_index': 0,
            'figsize_in': [6.0, 4.0],
            'axes': [{'index': 0, 'pos': [0.2, 0.2, 0.5, 0.5],
                      'xscale': 'linear', 'yscale': 'linear', 'grid': False,
                      'spines': {}, 'cell': None}]}
    _head22 = ("import matplotlib\nmatplotlib.use('Agg')\n"
               "import matplotlib.pyplot as plt\n"
               "fig = plt.figure(figsize=(6, 4))\n"
               "ax = fig.add_axes([0.10, 0.10, 0.40, 0.40])\n"
               "ax.plot([1, 2, 3])\n")
    # 下面两串**故意**运行时拼接：本文件里不出现"真实创建 GUI 对象"的字面量
    # （t18 的 hygiene 棘轮会扫 tests/ 下的真实窗口创建；连字符串也别留）。
    _CREATE22 = 'tkinter.' + 'Tk' + '()'
    _SHOW22 = 'plt.' + 'show()'

    def _mk22(name, body, params=True):
        p = os.path.join(_tmp22, name)
        with open(p, 'w', encoding='utf-8', newline='') as f:
            f.write(body)
        if params:
            d = os.path.join(_tmp22, '.tweak_params')
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, name[:-3] + '.json'), 'w',
                      encoding='utf-8') as f:
                json.dump(_p22, f)
        return p

    _fx22 = {}
    _env22 = {'MPLBACKEND': 'Agg', 'MPLTWEAK_LANG': 'zh'}
    try:
        # ① 缺依赖：这个名字先确认"此刻真的导不进来"，否则该条自降为跳过
        if importlib.util.find_spec('t12_gone_pkg_zzz') is None:
            _fx22['missing_dependency'] = _mk22(
                'pf_dep.py', _head22 + 'import t12_gone_pkg_zzz\n')
        # ② 需要参数：sys.argv[1]（强证据）+ argparse required=True
        _fx22['needs_args'] = _mk22(
            'pf_args.py',
            "import sys\nimport argparse\n" + _head22
            + "if len(sys.argv) > 1:\n    _data = sys.argv[1]\n"
            + "_ap = argparse.ArgumentParser()\n"
            + "_ap.add_argument('--x', required=True)\n")
        # ③ 联网：只 import 不发请求（预检只看静态足迹）
        _fx22['network'] = _mk22('pf_net.py', _head22 + 'import urllib.request\n')
        # ④ 人工输入：input() 藏在环境变量守卫后面 —— 静态看得见、**真重跑不会卡**
        _fx22['stdin'] = _mk22(
            'pf_stdin.py',
            "import os\n" + _head22
            + "if os.environ.get('T12_PF_ASK'):\n    _n = input('name? ')\n")
        # ⑤ GUI/弹窗：import tkinter + 受守卫保护的创建调用 + plt.show()
        _fx22['gui'] = _mk22(
            'pf_gui.py',
            "import os\nimport tkinter\n" + _head22
            + "if os.environ.get('T12_PF_GUI'):\n    " + _CREATE22 + "\n"
            + _SHOW22 + "\n")
        _clean22 = _mk22('pf_clean.py', _head22 + "fig.savefig('o.png')\n")
        _canary22 = os.path.join(_tmp22, 'T12_PF_CANARY.txt')
        _canary_script = _mk22(
            'pf_canary.py',
            _head22 + "open(%r, 'w').write('boom')\n" % _canary22, params=False)
        _bad22 = _mk22('pf_bad.py', 'def broken(:\n', params=False)

        # ---- 五类各一条（人读 + JSON 字段） ----
        _want22 = {
            'missing_dependency': ('缺依赖', '--verify-fast'),
            'needs_args': ('需要参数', '--write -- <你的参数...>'),
            'network': ('联网', '--verify-fast'),
            'stdin': ('等输入', '--verify-fast'),
            'gui': ('弹窗', '--verify-fast'),
        }
        for _kind, (_word, _step) in _want22.items():
            if _kind not in _fx22:
                print('  --  跳过：%s 的夹具在本环境造不出来' % _kind)
                continue
            _res = _pf22.analyze(_fx22[_kind])
            _ks = [x['kind'] for x in _res['risks']]
            _txt = '\n'.join(_pf22.message_lines(_res, _fx22[_kind]))
            check(_kind in _ks, '§22-%s 静态判出该风险' % _kind, _ks)
            check(_txt.startswith('[预检]') and _word in _txt
                  and _step in _txt and '--keep-unverified' in _txt,
                  '§22-%s 人读提示含风险名 + 可照抄的下一步' % _kind,
                  _txt[:300])
            # 用 next(..., {}) 而不是 [0]：万一某类检测被关掉，这里要**干净地判红**，
            # 而不是抛 IndexError 把整节变成"§22 执行"（反转验证实测踩到）。
            _r0 = next((x for x in _res['risks'] if x['kind'] == _kind), {})
            check(bool(_r0.get('next_step')) and bool(_r0.get('attribution'))
                  and bool(_r0.get('hint')) and bool(_r0.get('evidence')),
                  '§22-%s JSON 字段齐（next_step/attribution/hint/evidence）'
                  % _kind, _r0)
        check(_v22.VERIFY_FAIL_NEEDS_ARGS == _pf22.ATTRIBUTION['needs_args']
              and _v22.VERIFY_FAIL_CANNOT_RUN == _pf22.ATTRIBUTION['missing_dependency']
              and _v22.VERIFY_FAIL_NEEDS_GUI == _pf22.ATTRIBUTION['gui'],
              '§22 预检标签与 t11 的四类归因同值（agent 可用同一套标签分支）')
        for _k, _mk in _pf22.MSG_KEY.items():
            _item = _messages22.MESSAGES.get(_mk) or {}
            check(bool(_item.get('zh')) and bool(_item.get('en')),
                  '§22 文案双语句柄存在: %s' % _mk, sorted(_item))

        # ---- 不误报 / 静默 ----
        _rc22 = _pf22.analyze(_clean22)
        check(_rc22['risks'] == []
              and _pf22.message_lines(_rc22, _clean22) == [],
              '§22 干净脚本静默通过（risks=[] 且一条提示都不打）', _rc22)
        _local22 = _mk22('pf_local.py', 'import pf_helper_local\n', params=False)
        with open(os.path.join(_tmp22, 'pf_helper_local.py'), 'w',
                  encoding='utf-8') as f:
            f.write('x = 1\n')
        check(_pf22.analyze(_local22)['risks'] == [],
              '§22 同目录本地模块（import 旁边那个 .py）不算缺依赖 —— 不误报')
        check(_pf22.analyze(_fx22['needs_args'], args_passed=True)['risks'] == [],
              '§22 参数已透传 → 不再叫用户去传参数（不重复、不误导）')

        # ---- 绝不执行脚本（金丝雀）+ 失败即放行 ----
        _pf22.analyze(_canary_script)
        check(not os.path.exists(_canary22),
              '§22 预检**绝不执行脚本**（金丝雀的模块级写盘没有发生）')
        check(_pf22.analyze(os.path.join(_tmp22, 'nope.py'))
              == {'ok': True, 'risks': []}
              and _pf22.analyze(_tmp22) == {'ok': True, 'risks': []}
              and _pf22.analyze(_bad22)['risks'] == [],
              '§22 失败即放行：文件不存在 / 传目录 / 语法错误 → 一律当无风险，绝不抛')

        # ---- 入口 A：apply（写回前） ----
        if 'missing_dependency' in _fx22:
            _rc, _out, _err = run('apply', _fx22['missing_dependency'],
                                  '--write', '--style', 'inplace',
                                  cwd=_tmp22, env=_env22)
            check('[预检]' in _out and '缺依赖' in _out and '--verify-fast' in _out,
                  '§22-apply 写回前打印人读预检（含下一步）', _out[-400:])
            _rc, _out, _err = run('apply', _fx22['missing_dependency'],
                                  '--write', '--style', 'inplace', '--json',
                                  cwd=_tmp22, env=_env22)
            try:
                _j22 = json.loads(_out)
            except Exception:                                     # noqa: BLE001
                _j22 = {}
            _pfj = (_j22.get('preflight') or {})
            _rk = [x.get('kind') for x in (_pfj.get('risks') or [])]
            check('missing_dependency' in _rk and _pfj.get('ok') is False,
                  '§22-apply --json 带顶层 preflight.risks（机器可读）',
                  _out[:300])
            check('[预检]' in _err,
                  '§22-apply --json 时人读预检走 stderr（stdout 只有 JSON）',
                  _err[-200:])
            _r0 = (_pfj.get('risks') or [{}])[0]
            check(bool(_r0.get('next_step')) and bool(_r0.get('attribution')),
                  '§22-apply --json 每条风险带 next_step/attribution', _r0)
            # 不重跑的档位不该提示（说了只是噪音）
            _rc, _out, _err = run('apply', _fx22['missing_dependency'], '--write',
                                  '--verify-fast', '--json', cwd=_tmp22,
                                  env=_env22)
            try:
                _jf = json.loads(_out)
            except Exception:                                     # noqa: BLE001
                _jf = {}
            check('[预检]' not in _out and _jf.get('preflight') is None,
                  '§22 --verify-fast（这次不重跑）→ 预检静默、JSON 不带 preflight')
        _rc, _out, _err = run('apply', _clean22, '--write', '--style', 'inplace',
                              '--json', cwd=_tmp22, env=_env22)
        try:
            _jc = json.loads(_out)
        except Exception:                                         # noqa: BLE001
            _jc = {}
        check('[预检]' not in _out
              and (_jc.get('preflight') or {}).get('risks') == [],
              '§22-apply 干净脚本静默（人读无提示、JSON risks 为空）', _out[:200])

        # ---- 入口 B：launch 开窗前（直接调内部函数，**不真开窗**） ----
        from mpltweak import launch as _launch22
        _buf = io.StringIO()
        _emitted = _launch22._emit_preflight_hint(_fx22['stdin'], stderr=_buf)
        check(_emitted is True and '[预检]' in _buf.getvalue()
              and '等输入' in _buf.getvalue(),
              '§22-launch 开窗前向 stderr 给一次提示（不阻塞）',
              _buf.getvalue()[:200])
        _buf2 = io.StringIO()
        check(_launch22._emit_preflight_hint(_clean22, stderr=_buf2) is False
              and _buf2.getvalue() == '',
              '§22-launch 干净脚本静默（stderr 一个字不打）')
        _buf3 = io.StringIO()
        check(_launch22._emit_preflight_hint(
            _fx22['stdin'], stderr=_buf3,
            environ={'MPLTWEAK_NO_HINT': '1'}) is False
            and _buf3.getvalue() == '',
            '§22-launch MPLTWEAK_NO_HINT=1 静默（批量脚本开关）')
        _buf4 = io.StringIO()
        check(_launch22._emit_preflight_hint(_tmp22, stderr=_buf4) is False
              and _buf4.getvalue() == '',
              '§22-launch 预检无风险/出岔子 → 返回 False，绝不影响开窗')
    except Exception as _e:                                       # noqa: BLE001
        check(False, '§22 执行', _e)
    finally:
        shutil.rmtree(_tmp22, ignore_errors=True)

    print()
    if _fails:
        print('FAILED: %d 项 -> %s' % (len(_fails), _fails))
        return 1
    print('ALL PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
