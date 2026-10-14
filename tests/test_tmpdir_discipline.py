# -*- coding: utf-8 -*-
"""tests/test_tmpdir_discipline.py —— 临时目录取法的跨平台纪律（0.1.11，t22）

**为什么单独一个套件**：这族坑已经咬了两次 ——
* ``tests/test_sandbox.py:57-63`` 记过 CI 假红：``os.environ.get('TEMP', '.')``
  在 POSIX 上回落成相对路径 ``./sandbox_t9_tests`` ⇒ 产物写进**当前工作目录**，
  可复现 18 项失败；修法就是 ``tempfile.gettempdir()``；
* ``tools/corpus_filter_t7.py``（t7 全量筛选）的 ``--workroot`` 默认值**同一个写法**，
  一直没修（不在 t13 的口径内），t22 才收口成
  ``default_tmp_root()``：``TMPDIR`` → ``TEMP`` → ``TMP`` → ``tempfile.gettempdir()``，
  每个环境变量候选都要求 ``os.path.isabs``。

本套件钉住的就是那个契约：**解析出来的临时根永远是绝对路径，绝不回落成
``'.'``/``'..'``/任何相对路径**（宁可响亮失败）。之所以用子进程：``os.environ`` 必须在
``tempfile.gettempdir()`` **首次调用前**设好（它有进程级缓存），同进程内改环境没意义。

t39 追加（同一族的另一半）：该工具的 ``DEFAULT_CORPUS`` / ``DEFAULT_OUT`` 曾**写死
``D:\...`` 盘符**（Linux / CI 上同样不可用），已改成从树位置推
（``WORKSPACE = os.path.dirname(ROOT)``，与 ``tools/cmp_plain_vs_instrumented.py:77-80``
同一写法）；第 ⑧ 组（09~09d）钉住"定义行里不许再出现盘符字面量"。
"""
import io
import json
import os
import re
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOL = os.path.join(ROOT, 'tools', 'corpus_filter_t7.py')
PY = sys.executable

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(errors='replace')
    except (AttributeError, ValueError, OSError):
        pass

_fails = []


def check(name, cond, detail=''):
    print('%s | %s%s' % ('OK' if cond else 'FAIL', name,
                         (' | ' + str(detail)) if detail else ''))
    if not cond:
        _fails.append(name)


def pinned_default_lines(path):
    """t39：扫出"默认语料/产出目录"的**定义行**里写死盘符字面量的地方。

    为什么必须看**源码字面量**而不是只看解析结果：本机上
    ``dirname(<树>) == D:\\Agent\\deepseek``，所以写死的
    ``r'D:\\Agent\\deepseek\\corpus_union'`` 与"从树位置推"**解析结果逐字相同**
    —— 只比值的断言在这台机器上**永远不会红**。真正有牙的是"定义行里不许出现
    ``[盘符]:[\\/]``"这条静态判据（同族工具 `cmp_plain_vs_instrumented.py:77-80`
    对同两个路径就是从树位置推的）。
    """
    out = []
    with io.open(path, encoding='utf-8') as f:
        for i, line in enumerate(f, 1):
            if re.match(r'\s*(DEFAULT_CORPUS|DEFAULT_OUT|WORKSPACE)\s*=', line) \
                    and re.search(r'[A-Za-z]:[\\/]', line):
                out.append('%d:%s' % (i, line.strip()))
    return out


# 子进程里：按给定环境算出 default_tmp_root()（失败则报 RuntimeError）
CHILD = (
    "import importlib.util, json, os, sys\n"
    "old_root = os.environ.get('TEMP', '.')\n"
    "try:\n"
    "    spec = importlib.util.spec_from_file_location('cf', sys.argv[1])\n"
    "    m = importlib.util.module_from_spec(spec)\n"
    "    spec.loader.exec_module(m)\n"
    "    root = m.default_tmp_root()\n"
    "    print(json.dumps({'root': root,\n"
    "                      'workroot': os.path.join(root, 'corpus_filter_work'),\n"
    "                      'isabs': os.path.isabs(os.path.join(root, 'corpus_filter_work')),\n"
    "                      'old_root': old_root,\n"
    "                      'old_workroot': os.path.join(old_root, 'corpus_filter_work'),\n"
    "                      'old_isabs': os.path.isabs(os.path.join(old_root, 'corpus_filter_work'))}))\n"
    "except Exception as e:\n"
    "    print(json.dumps({'error': type(e).__name__, 'msg': str(e)[:200],\n"
    "                      'old_root': old_root}))\n"
)


def _resolve(overrides, strip=True, cwd=None):
    """在指定环境（默认剥掉 TMPDIR/TEMP/TMP）下解析默认 workroot → dict。

    ``strip=False`` = **原样**继承本机环境（"行为不变"那条要用它）。
    """
    if strip:
        env = {k: v for k, v in os.environ.items()
               if k not in ('TMPDIR', 'TEMP', 'TMP')}
    else:
        env = dict(os.environ)
    env['PYTHONIOENCODING'] = 'utf-8'
    # 棘轮要求（test_hygiene.py:1081 调用点级闸门）：子进程**绝不**继承宿主后端，
    # 否则可能落到 Qt/Tk 抢屏。本套件不起图，但把 Agg 钉死是零成本的硬纪律。
    env['MPLBACKEND'] = 'Agg'
    for k, v in overrides.items():
        if v is not None:
            env[k] = v
    r = subprocess.run([PY, '-c', CHILD, TOOL], capture_output=True, env=env,
                       cwd=cwd or ROOT)
    out = r.stdout.decode('utf-8', 'replace').strip()
    try:
        return json.loads(out)
    except ValueError:
        return {'error': 'Unparseable', 'msg': out[-200:] + ' / ' +
                r.stderr.decode('utf-8', 'replace')[-200:]}


def main():
    print('临时目录取法纪律（tools/corpus_filter_t7.py:default_tmp_root 的契约）')
    print('')

    sentinel = tempfile.mkdtemp(prefix='t22_sentinel_')
    try:
        # ⓪ 本机真实环境（**不改**任何环境变量）：这是"行为不变"的那一条
        d0 = _resolve({}, strip=False)
        check('00 本机真实环境（环境变量原样）-> workroot 绝对',
              d0.get('isabs') is True, d0)
        if sys.platform == 'win32':
            native = os.path.join(os.environ.get('TEMP', ''), 'corpus_filter_work')
            check('00b 【Windows 行为不变】仍等于 %TEMP%\\corpus_filter_work（新旧写法逐字一致）',
                  d0.get('workroot') == native == d0.get('old_workroot'), d0)

        # ① 剥掉三个环境变量后：仍必须绝对
        d = _resolve({})
        check('01 剥掉 TMPDIR/TEMP/TMP 后仍给出绝对路径', d.get('isabs') is True, d)

        # ② 优先级：TMPDIR 胜出，且原样采用（绝对候选）
        d = _resolve({'TMPDIR': sentinel, 'TEMP': tempfile.gettempdir(),
                      'TMP': tempfile.gettempdir()})
        check('02 TMPDIR 优先于 TEMP/TMP，且原样采用绝对候选',
              d.get('root') == sentinel and d.get('isabs') is True, d)

        # ③ 三个环境变量全没有 -> 回落到 tempfile.gettempdir()，仍是绝对
        d3 = _resolve({'TMPDIR': None, 'TEMP': None, 'TMP': None})
        check('03 环境变量全缺时回落 gettempdir() 仍绝对',
              d3.get('isabs') is True, d3)

        # ④ 相对候选必须被**跳过**（不能悄悄锚到 cwd）
        d = _resolve({'TMPDIR': sentinel, 'TEMP': 'reltmp_xyz', 'TMP': None})
        check('04 相对 TEMP 被跳过，改用绝对 TMPDIR（不锚 cwd）',
              d.get('root') == sentinel, d)

        # ⑤ 只有相对候选可用时：绝不返回相对路径 —— 要么响亮失败，要么绝对
        d = _resolve({'TMPDIR': None, 'TEMP': '.', 'TMP': None})
        if 'error' in d:
            ok = d.get('error') == 'RuntimeError'
            check('05 TEMP="." 时响亮失败（RuntimeError），绝不写 cwd', ok, d)
        else:
            check('05 TEMP="." 时若未失败，也必须给出绝对路径',
                  d.get('isabs') is True and d.get('root') != '.', d)

        # ⑥ 旧的脆弱写法在同一场景下**确实**会退化成相对路径 —— 这条才是
        #    "会失败于修复前"的反向证据，所以必须在**子进程里**量（父进程里
        #    TEMP 是设着的，量不到退化）。
        d9 = _resolve({'TMPDIR': None, 'TEMP': None, 'TMP': None})
        check('06 【反向证据】全无环境变量时，旧写法 os.environ.get("TEMP",".") 退化成 "."'
              '（workroot 变成相对路径，产物写进 cwd）',
              d9.get('old_root') == '.' and d9.get('old_isabs') is False, d9)
        check('07 【反向证据】同一场景下新写法给出绝对路径（修复前的实现在这里会红）',
              d9.get('isabs') is True, d9)

        # ⑦ 工具可加载 + --help 可用（改动没把 CLI 弄坏）
        env_help = dict(os.environ)
        env_help['PYTHONIOENCODING'] = 'utf-8'
        env_help['MPLBACKEND'] = 'Agg'          # 同上：不继承宿主后端
        r = subprocess.run([PY, TOOL, '--help'], capture_output=True,
                           env=env_help, cwd=ROOT)
        check('07b corpus_filter_t7.py --help rc=0 且列出 --workroot',
              r.returncode == 0 and b'--workroot' in r.stdout,
              'rc=%s' % r.returncode)

        # ⑧ t39：默认**语料 / 产出**目录不许写死盘符 —— 同一族平台假设（Linux/CI 上
        #    直接不可用），要像同族工具那样从树位置推。见 pinned_default_lines 的注释：
        #    09/09b 只比解析值，本机上写死与推导**恰好相等**，所以只有 09c 有牙。
        import importlib.util
        _spec = importlib.util.spec_from_file_location('cf_defaults_t39', TOOL)
        _m = importlib.util.module_from_spec(_spec)
        _spec.loader.exec_module(_m)
        _ws = os.path.dirname(_m.ROOT)
        check('09 默认语料目录 = <ws>/corpus_union（由树位置推）',
              _m.DEFAULT_CORPUS == os.path.join(_ws, 'corpus_union'),
              _m.DEFAULT_CORPUS)
        check('09b 默认产出目录 = <ws>/_review/corpus_filter_t7（由树位置推）',
              _m.DEFAULT_OUT == os.path.join(_ws, '_review', 'corpus_filter_t7'),
              _m.DEFAULT_OUT)
        check('09c 【棘轮】这两个默认值的定义行里没有写死的盘符字面量',
              not pinned_default_lines(TOOL), pinned_default_lines(TOOL) or '干净')

        # 09d 反向证据：把旧写法（写死 D:\）放进一份临时副本 -> 判据必须报出来
        _mut = os.path.join(sentinel, 'mutant_defaults.py')
        with io.open(TOOL, encoding='utf-8') as f:
            _txt = f.read()
        _txt = _txt.replace(
            "DEFAULT_CORPUS = os.path.join(WORKSPACE, 'corpus_union')",
            "DEFAULT_CORPUS = r'D:\\Agent\\deepseek\\corpus_union'")
        with io.open(_mut, 'w', encoding='utf-8', newline='\n') as f:
            f.write(_txt)
        check('09d 【反向证据】写死盘符的旧写法会被 09c 抓住（且改回推导后不报）',
              bool(pinned_default_lines(_mut)) and not pinned_default_lines(TOOL),
              pinned_default_lines(_mut))
    finally:
        import shutil
        shutil.rmtree(sentinel, ignore_errors=True)
        check('08 探针哨兵目录已清理', not os.path.exists(sentinel), sentinel)

    print('')
    if _fails:
        print('FAILED: %d 项 -> %s' % (len(_fails), _fails))
        return 1
    print('ALL PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
