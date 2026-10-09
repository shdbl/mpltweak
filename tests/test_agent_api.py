# -*- coding: utf-8 -*-
"""mpltweak agent 接口测试（describe / schema / apply --json / 读改写闭环）。

§1  describe 输出干净 JSON 且结构符合 params 规范
§2  describe --all-figs 多图包装
§3  describe --compact 更短（给 agent 省 token）
§4  schema 合法，且能校验 describe 的产物
§5  apply --json：stdout 只有 JSON，过程信息走 stderr
§6  完整闭环：describe → agent 改数字 → apply --write → 源码真的变了
"""
import json
import os
import subprocess
import sys
import tempfile
import textwrap

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
FIX = os.path.join(ROOT, 'tests', 'fixtures', 'synth_script.py')

_fails = []


def check(cond, label, extra=''):
    print('%s %s%s' % ('  OK  ' if cond else ' FAIL ', label,
                       '' if cond else '   ' + str(extra)))
    if not cond:
        _fails.append(label)


def run(*args, cwd=None):
    r = subprocess.run([PY, '-m', 'mpltweak.cli', *args], cwd=cwd or ROOT,
                       capture_output=True, text=True, encoding='utf-8')
    return r.returncode, (r.stdout or ''), (r.stderr or '')


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


def main():
    print('§1 describe 输出干净 JSON')
    rc, out, err = run('describe', FIX)
    d = None
    try:
        d = json.loads(out)
        check(rc == 0, 'exit 0', rc)
        check(d['version'] == 3, 'version=3', d.get('version'))
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
    check(len(comp) < len(full) and json.loads(comp)['version'] == 3,
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
        rc, out, err = run('apply', script, '--write', '--json')
        res = json.loads(out)
        check(res['ok'], 'apply --write 成功', res['files'][0].get('reason'))
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

    print()
    if _fails:
        print('FAILED: %d 项 -> %s' % (len(_fails), _fails))
        return 1
    print('ALL PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
