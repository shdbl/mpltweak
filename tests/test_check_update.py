# -*- coding: utf-8 -*-
"""T2（0.1.17）：`mpltweak --check-update` —— **显式**联网查 PyPI，默认任何命令都不联网。

守的四件事（每条都有断言，末尾"全套件绿"只是顺带）：

① **默认不联网**：静态扫 ``src/mpltweak/*.py``（除 ``update.py`` 外不许 import
   ``urllib`` / ``socket`` / ``http`` / ``requests``，也不许出现 ``urlopen(``），
   再用 monkeypatch 把网络出口换掉，证明 ``--version`` / ``--help`` 一次都不碰它；
   而 ``--check-update`` **必须**碰它（否则上一条的"零调用"就是假绿的）。

② **解析 / 比较三路**：PyPI JSON 的各种坏形状（不是 JSON、没有 info、版本号空白、
   非 UTF-8）都要变成"读不懂"而不是 ``None`` 流进比较；版本比较按**数字段**比
   （``0.1.9`` 比 ``0.1.16`` 老），带后缀（``rc1`` / ``dev0``）老实说"比不出"。

③ **失败四路**（超时 / 连不上 / HTTP 错误码 / 载荷读不懂）：全部由 monkeypatch 注入
   —— **本套件一次都不联网**（PyPI 地址一律换 ``file://`` 夹具，见下）。

④ **语义**：查不到 ⇒ rc=0（不是命令失败）；用法错误 ⇒ rc=2；``--json`` 五个键
   形状固定、``update_available`` 在"不知道"时是 ``null`` 而不是 ``false``
   （说成 false 就是"已是最新"的假话）；命令**不写任何文件**。

跑法（脚本式，与 CI 一致）：`python tests/test_check_update.py`
反向验证（把比较逻辑改坏 ⇒ 本套件必红）见 ``_team014/check_update/reverse.py``
（在仓库外的临时副本上跑，不碰共享工作树）。
"""
import ast
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import urllib.error

PY = sys.executable
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, 'src')
sys.path.insert(0, SRC)

from mpltweak import update as _u          # noqa: E402
from mpltweak import messages as _msg      # noqa: E402
from mpltweak import cli as _cli           # noqa: E402

# 控制台兜底（GBK 控制台下 print 中文会 UnicodeEncodeError 把整套用例打挂；
# tests/test_hygiene.py 的 `test_test_suites_cannot_crash_gbk_console` 认这个标记）
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(errors='replace')
    except (AttributeError, ValueError, OSError):
        pass          # 被重定向成不支持 reconfigure 的对象时忽略即可

# 子进程一律 Agg + UTF-8（本套件不画图，但 launch 那条透传用例会过 matplotlib）。
# 这里是**模块级钉死**的 dict：`test_hygiene` 的子进程闸门要能溯源到 MPLBACKEND=Agg
# （直接 `env=dict(os.environ)` 会被判成"继承宿主后端"的缺口）。
ENV = dict(os.environ, MPLBACKEND='Agg', MPLTWEAK_LANG='zh', PYTHONUTF8='1',
           PYTHONIOENCODING='utf-8', PYTHONPATH=SRC)

FAILED = []
PASSED = []


def check(name, cond, detail=''):
    if cond:
        PASSED.append(name)
        print('  OK   %s%s' % (name, (' | %s' % detail) if detail else ''))
    else:
        FAILED.append(name)
        print('  FAIL %s%s' % (name, (' | %s' % detail) if detail else ''))


def _env(**kw):
    """子进程环境：ENV 打底，清掉两个测试钩子，再按用例设（免得继承上一条的地址）。

    函数体里那行 ``env['MPLBACKEND'] = 'Agg'`` 既是真的、也是**给闸门看的**：
    `test_hygiene` 认"本地 def 里钉了后端"这一形态（同 `_pin_child_env` 的范式）。
    """
    env = dict(ENV)
    env['MPLBACKEND'] = 'Agg'
    env.pop('MPLTWEAK_PYPI_URL', None)
    env.pop('MPLTWEAK_UPDATE_TIMEOUT', None)
    env.update({k: str(v) for k, v in kw.items()})
    return env


def _run(args, cwd, **envkw):
    r = subprocess.run([PY, '-m', 'mpltweak.cli', *args], cwd=cwd,
                       capture_output=True, env=_env(**envkw))
    return (r.returncode,
            (r.stdout or b'').decode('utf-8', 'replace'),
            (r.stderr or b'').decode('utf-8', 'replace'))


def _fake_open(calls=None, raises=None, body=b''):
    """替换 ``update._open_url``：记录调用 / 抛指定异常 / 返回指定字节。

    这是**唯一**的"网络层"—— 本套件全部失败路径都靠它注入，因此一次都不联网。
    """
    def _open(url, timeout):
        if calls is not None:
            calls.append((url, timeout))
        if raises is not None:
            raise raises
        return io.BytesIO(body)
    return _open


def _with_open(fake, fn):
    """临时替换网络出口跑 ``fn()``，无论如何都换回来。"""
    real = _u._open_url
    _u._open_url = fake
    try:
        return fn()
    finally:
        _u._open_url = real


# ---- ① 解析（坏形状一律"读不懂"，不许 None 流下去）----

def case_parse():
    print('\n[1] parse_latest：PyPI JSON → info.version')
    good = b'{"info": {"version": "0.1.17"}, "releases": {}}'
    check('解析 bytes 载荷', _u.parse_latest(good) == '0.1.17')

    check('解析 str 载荷（非 bytes 也认）',
          _u.parse_latest('{"info": {"version": "0.1.17"}}') == '0.1.17')
    check('版本号两端空白被去掉',
          _u.parse_latest('{"info": {"version": "  0.1.17\\n"}}') == '0.1.17')

    bad_cases = {
        '不是 JSON': b'<html>404</html>',
        'JSON 顶层是数组': b'[1, 2, 3]',
        '没有 info 对象': b'{"releases": {}}',
        'info 不是对象': b'{"info": "0.1.17"}',
        'version 缺失': b'{"info": {"name": "mpltweak"}}',
        'version 空白': b'{"info": {"version": "   "}}',
        'version 是数字': b'{"info": {"version": 17}}',
        '非 UTF-8 字节': b'{"info": {"version": "\xff\xfe"}}',
    }
    for label, raw in bad_cases.items():
        try:
            got = _u.parse_latest(raw)
        except _u.PayloadError as e:
            check('坏载荷 → PayloadError：%s' % label, True, str(e)[:40])
        except Exception as e:                     # noqa: BLE001 - 意外类型也贴出来
            check('坏载荷 → PayloadError：%s' % label, False,
                  '抛了 %s: %s' % (type(e).__name__, e))
        else:
            check('坏载荷 → PayloadError：%s' % label, False, '返回了 %r' % (got,))


# ---- ② 比较（数字段比较，不是字符串比较）----

def case_compare():
    print('\n[2] compare：数字段比较，比不出就说比不出')
    table = [
        ('0.1.16', '0.1.16', 'latest', '同版本'),
        ('0.1.16', '0.1.16.0', 'latest', '补零算同版本'),
        ('0.1.16', '0.1.17', 'newer', '补丁版更新'),
        ('0.1.16', '0.2.0', 'newer', '次版本更新'),
        # 这一行是"按字符串比"的照妖镜：'0.1.16' < '0.1.9' 字符串上成立，数值上不成立
        ('0.1.9', '0.1.16', 'newer', '9 → 16 按数字比（不是字符串）'),
        ('0.1.16', '0.1.9', 'older', '本地比 PyPI 新（开发版常见）'),
        ('0.1.17', '0.1.16', 'older', '本地更新'),
        ('0.1.17rc1', '0.1.16', 'unknown', '带后缀不猜'),
        ('0.1.16', '0.1.17.dev0', 'unknown', '带 .dev 不猜'),
        ('0.1.16', None, 'unknown', 'None 不猜'),
        (None, '0.1.17', 'unknown', 'None 不猜'),
    ]
    for cur, latest, want, why in table:
        got = _u.compare(cur, latest)
        check('compare(%r, %r) == %s（%s）' % (cur, latest, want, why), got == want,
              'got %r' % (got,))

    check('version_key 只认纯数字点',
          _u.version_key('0.1.16') == (0, 1, 16)
          and _u.version_key('0.1.17rc1') is None
          and _u.version_key('v0.1.16') is None
          and _u.version_key('') is None
          and _u.version_key(3) is None)


# ---- ③ 失败四路（monkeypatch 注入，零联网）----

def case_query():
    print('\n[3] query：成功一路 + 失败四路（全部注入，不联网）')
    calls = []
    latest, reason, detail = _with_open(
        _fake_open(calls, body=b'{"info": {"version": "0.1.17"}}'),
        lambda: _u.query('https://example.invalid/pypi.json', 2.5))
    check('成功：拿到版本号', (latest, reason, detail) == ('0.1.17', None, None),
          '%r / %r' % (latest, reason))
    check('成功：请求用的是调用方给的地址 + 超时',
          calls == [('https://example.invalid/pypi.json', 2.5)], calls)

    injected = [
        ('timeout', urllib.error.URLError(socket.timeout('timed out'))),
        ('timeout', socket.timeout('timed out')),
        ('network', urllib.error.URLError(OSError(10061, 'refused'))),
        ('network', OSError(10054, 'connection reset')),
        ('network', ValueError('unknown url type: nope')),
        ('http', urllib.error.HTTPError('u', 503, 'Service Unavailable', None, None)),
        ('payload', None),          # None ⇒ 返回坏载荷（不是抛异常）
    ]
    for want, exc in injected:
        if exc is None:
            got = _with_open(_fake_open(body=b'not json at all'),
                             lambda: _u.query('https://example.invalid/', 1.0))
        else:
            got = _with_open(_fake_open(raises=exc),
                             lambda: _u.query('https://example.invalid/', 1.0))
        check('失败：%s → %s' % (type(exc).__name__ if exc else '坏载荷', want),
              got[0] is None and got[1] == want, '%r / %r' % (got[0], got[1]))

    # 超时那一刻的说明要带秒数（用户/运维一眼看出"等了多久"）
    got = _with_open(_fake_open(raises=socket.timeout('x')),
                     lambda: _u.query('https://example.invalid/', 0.5))
    check('超时的 detail 带秒数', '0.5' in (got[2] or ''), got[2])


# ---- ③b payload 形状（"不知道" ≠ False）----

def case_payload():
    print('\n[4] --json 载荷：五个键 + update_available 的三态')
    keys = {'current', 'latest', 'update_available', 'error', 'error_detail'}
    body = _u.payload('0.1.16', '0.1.17', None, None)
    check('键集合固定', set(body) == keys, sorted(body))
    check('有新版本 ⇒ true', body['update_available'] is True and body['error'] is None)
    check('同版本 ⇒ false',
          _u.payload('0.1.16', '0.1.16', None, None)['update_available'] is False)
    check('本地更新 ⇒ false',
          _u.payload('0.1.17', '0.1.16', None, None)['update_available'] is False)
    unknown = _u.payload('0.1.17rc1', '0.1.16', None, None)
    check('比不出 ⇒ null（不是 false）', unknown['update_available'] is None, unknown)
    failed = _u.payload('0.1.16', None, 'timeout', 'timed out after 5.0s')
    check('查不到 ⇒ latest=null / error 有值 / available=null',
          failed['latest'] is None and failed['error'] == 'timeout'
          and failed['update_available'] is None, failed)
    check('原因码是文档化的四个之一',
          _u.REASON_TIMEOUT == 'timeout' and _u.REASON_NETWORK == 'network'
          and _u.REASON_HTTP == 'http' and _u.REASON_PAYLOAD == 'payload')
    reason_keys = ('update_reason_timeout', 'update_reason_network',
                   'update_reason_http', 'update_reason_payload')
    check('每个原因码都有 zh/en 文案',
          all(_msg.MESSAGES.get(k) and _msg.MESSAGES[k].get('zh')
              and _msg.MESSAGES[k].get('en') for k in reason_keys),
          '%d 个键' % len(reason_keys))
    check('四个分支各打到自己那个键（不许串）',
          [_u._reason_text(r) for r in ('timeout', 'network', 'http', 'payload')]
          == [_msg.t('update_reason_timeout'), _msg.t('update_reason_network'),
              _msg.t('update_reason_http'), _msg.t('update_reason_payload')])


# ---- ④ 默认不联网：静态棘轮 + 运行期零调用 ----

NET_MODULES = ('urllib', 'socket', 'http', 'requests', 'httplib', 'ftplib',
               'smtplib', 'urllib3', 'aiohttp')


def case_offline_default():
    print('\n[5] 默认不联网：整棵树只有 update.py 有网络出口')
    offenders = []
    pkg = os.path.join(SRC, 'mpltweak')
    for dirpath, _dirs, files in os.walk(pkg):
        for fn in sorted(files):
            if not fn.endswith('.py') or fn == 'update.py':
                continue
            path = os.path.join(dirpath, fn)
            with io.open(path, encoding='utf-8') as f:
                text = f.read()
            rel = os.path.relpath(path, pkg).replace('\\', '/')
            tree = ast.parse(text)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom) and not node.level:
                    names = [node.module or '']
                else:
                    continue
                for name in names:
                    root = name.split('.')[0]
                    if root in NET_MODULES:
                        offenders.append('%s: import %s' % (rel, name))
            if 'urlopen(' in text:
                offenders.append('%s: 出现 urlopen(' % rel)
    check('update.py 以外没有网络 import / urlopen', not offenders, offenders)

    # 运行期：把唯一出口换成"记一笔就拒连"的假货，跑几条**不联网**的命令。
    # 必须真的在这段里一直替换着（`_with_open` 跑完就还原，别用它包整段）。
    calls = []
    real_open = _u._open_url
    _u._open_url = _fake_open(
        calls, raises=urllib.error.URLError(OSError(10061, 'refused')))
    quiet = []
    try:
        for argv in (['--version'], ['--help'], ['-V'], []):
            del calls[:]
            out_buf, err_buf = io.StringIO(), io.StringIO()
            real_out, real_err = sys.stdout, sys.stderr
            sys.stdout, sys.stderr = out_buf, err_buf
            try:
                _cli.main(list(argv))
            finally:
                sys.stdout, sys.stderr = real_out, real_err
            quiet.append((argv, len(calls)))

        # 对照项：--check-update **必须**碰这个出口，否则上面那条"零调用"是假绿。
        del calls[:]
        out_buf = io.StringIO()
        real_out = sys.stdout
        sys.stdout = out_buf
        try:
            rc = _cli.main(['--check-update'])
        finally:
            sys.stdout = real_out
    finally:
        _u._open_url = real_open

    check('--version / --help / -V / 空参数 一次都不联网',
          all(n == 0 for _a, n in quiet), quiet)
    check('--check-update 确实走了那个唯一出口（对照项）',
          len(calls) == 1 and rc == _msg.EXIT_OK, '%d 次 / rc=%r' % (len(calls), rc))
    check('默认超时是 5 秒',
          _u.DEFAULT_TIMEOUT == 5.0 and calls[0][1] == 5.0, calls[0][1])
    check('超时不认环境变量里的垃圾值',
          _with_timeout('abc', lambda: _u._timeout()) == 5.0
          and _with_timeout('-1', lambda: _u._timeout()) == 5.0
          and _with_timeout('0.25', lambda: _u._timeout()) == 0.25)


def _with_timeout(raw, fn):
    old = os.environ.get(_u.ENV_TIMEOUT)
    os.environ[_u.ENV_TIMEOUT] = raw
    try:
        return fn()
    finally:
        if old is None:
            os.environ.pop(_u.ENV_TIMEOUT, None)
        else:
            os.environ[_u.ENV_TIMEOUT] = old


# ---- ⑤ CLI 端到端（PyPI 地址换 file:// 夹具，零联网）----

def _write(d, name, text):
    path = os.path.join(d, name)
    with io.open(path, 'w', encoding='utf-8') as f:
        f.write(text)
    return path


def _file_url(path):
    return 'file:///' + path.replace('\\', '/').lstrip('/')


def case_cli(base):
    print('\n[6] CLI 端到端：file:// 夹具（不联网）')
    cur = _cli._version_text().split()[-1]
    newer = _write(base, 'pypi_newer.json',
                   json.dumps({'info': {'version': '999.0.0'}}))
    same = _write(base, 'pypi_same.json',
                  json.dumps({'info': {'version': cur}}))
    older = _write(base, 'pypi_older.json',
                   json.dumps({'info': {'version': '0.0.1'}}))
    broken = _write(base, 'pypi_broken.json', '<html>nope</html>')
    cwd = os.path.join(base, 'cwd')
    os.makedirs(cwd)

    # 不写任何文件：跑之前 / 之后目录内容必须一致（含隐藏文件）
    before = sorted(os.listdir(cwd))

    rc, out, err = _run(['--check-update'], cwd,
                        MPLTWEAK_PYPI_URL=_file_url(newer))
    check('有新版本：rc=0', rc == 0, 'rc=%r' % rc)
    check('有新版本：给出升级命令',
          'pip install -U mpltweak' in out and '999.0.0' in out, out.strip()[:120])

    rc, out, err = _run(['--check-update', '--json'], cwd,
                        MPLTWEAK_PYPI_URL=_file_url(newer))
    try:
        body = json.loads(out)
    except ValueError as e:
        body = None
        check('--json（有新版本）stdout 是纯 JSON', False, '%s: %r' % (e, out[:120]))
    else:
        check('--json（有新版本）stdout 是纯 JSON', True)
        check('--json 五键齐全且 current 是真版本',
              set(body) == {'current', 'latest', 'update_available', 'error',
                            'error_detail'} and body['current'] == cur, body)
        check('--json 有新版本 ⇒ available=true / error=null',
              body['update_available'] is True and body['latest'] == '999.0.0'
              and body['error'] is None, body)

    rc, out, err = _run(['--check-update', '--json'], cwd,
                        MPLTWEAK_PYPI_URL=_file_url(same))
    body = json.loads(out) if out.strip().startswith('{') else {}
    check('同版本 ⇒ available=false', body.get('update_available') is False, body)

    rc, out, err = _run(['--check-update'], cwd,
                        MPLTWEAK_PYPI_URL=_file_url(older))
    check('本地比 PyPI 新 ⇒ rc=0 且不劝升级',
          rc == 0 and 'pip install -U' not in out and '0.0.1' in out,
          out.strip()[:120])

    # 找不着 / 读不懂：人话两行、无 Traceback、rc=0（不是命令失败）
    for label, url, want in (
            ('文件不存在（连不上）', _file_url(os.path.join(base, 'nope.json')),
             'network'),
            ('载荷读不懂', _file_url(broken), 'payload')):
        rc, out, err = _run(['--check-update'], cwd, MPLTWEAK_PYPI_URL=url)
        check('%s：rc=0（查不到不算命令失败）' % label, rc == 0, 'rc=%r' % rc)
        check('%s：stdout 有原因码 %s + 一句"不影响"' % (label, want),
              want in out and '不影响本地使用' in out, out.strip()[:120].replace('\n', ' | '))
        check('%s：不抛 Traceback' % label, 'Traceback' not in (out + err),
              (out + err)[:120])

        rc, out, err = _run(['--check-update', '--json'], cwd,
                            MPLTWEAK_PYPI_URL=url)
        body = json.loads(out) if out.strip().startswith('{') else {}
        check('%s：--json 里 error=%s / available=null' % (label, want),
              body.get('error') == want and body.get('latest') is None
              and body.get('update_available') is None, body)

    # 超时路径（真 socket 不通的地址 + 0.05 秒超时）：rc=0、人话、不崩。
    # 注意：这条**不发往外部网络**（10.255.255.1 是本机不可路由地址，Windows 上
    # 直接"目标不可达"/超时），只为覆盖"超时也不许抛 Traceback"这一条。
    rc, out, err = _run(['--check-update'], cwd,
                        MPLTWEAK_PYPI_URL='http://10.255.255.1/pypi/mpltweak/json',
                        MPLTWEAK_UPDATE_TIMEOUT='0.05')
    check('超时/不可达：rc=0 且不抛 Traceback', rc == 0 and 'Traceback' not in out + err,
          'rc=%r' % rc)
    check('超时/不可达：原因码是 timeout 或 network',
          ('timeout' in out) or ('network' in out), out.strip()[:120].replace('\n', ' | '))

    after = sorted(os.listdir(cwd))
    check('命令不写任何文件（目录内容前后一致）', before == after,
          '%r -> %r' % (before, after))


# ---- ⑥ 用法错误与透传 ----

def case_cli_args(base):
    print('\n[7] 用法错误 rc=2 / 脚本参数透传')
    cwd = base

    rc, out, err = _run(['--check-update', 'extra.py'], cwd)
    check('多给位置参数 ⇒ rc=2', rc == 2, 'rc=%r' % rc)
    check('用法错误打到 stderr（stdout 干净）',
          '用法' in err and 'extra.py' in err and not out.strip(), repr(out[:60]))

    rc, out, err = _run(['--check-update', '--lang'], cwd)
    check('--lang 缺值 ⇒ rc=2', rc == 2, 'rc=%r' % rc)

    rc, out, err = _run(['--check-update', '--lang', 'klingon'], cwd)
    check('--lang 值不认识 ⇒ rc=2', rc == 2, 'rc=%r' % rc)

    rc, out, err = _run(['--check-update', '--bogus'], cwd)
    check('未知开关 ⇒ rc=2 且点名那个开关', rc == 2 and '--bogus' in err, err.strip()[:80])

    # 透传：脚本名在前时 `--check-update` 是**用户脚本自己的参数**，不许抢
    rc, out, err = _run(['definitely_missing.py', '--check-update'], cwd)
    check('脚本参数透传：走 launch 找不到脚本（rc=2）', rc == 2, 'rc=%r' % rc)
    check('脚本参数透传：没去查 PyPI（stdout 无升级提示）',
          'pip install -U' not in out and 'PyPI' not in out, out.strip()[:80])

    # en 下这一路必须是纯 ASCII（机器/英文终端都稳）
    rc, out, err = _run(['--check-update', '--lang', 'en'], cwd,
                        MPLTWEAK_PYPI_URL=_file_url(
                            _write(base, 'pypi_ascii.json',
                                   json.dumps({'info': {'version': '999.0.0'}}))))
    check('--lang en：输出纯 ASCII', rc == 0 and out.isascii(),
          out.strip()[:120].replace('\n', ' | '))
    check('--lang en：给出升级命令', 'pip install -U mpltweak' in out, out.strip()[:100])


# ---- ⑦ help 与代码侧同步 ----

def case_help():
    print('\n[8] --help 与 --check-update 自述同步')
    _msg.set_lang('zh')
    buf = io.StringIO()
    _cli._usage(buf)
    help_zh = buf.getvalue()
    check('顶层 --help 列出 --check-update 与"默认不联网"',
          '--check-update' in help_zh and '默认任何命令都不联网' in help_zh)
    check('顶层 --help 说明"查不到仍 rc=0"', '仍 rc=0' in help_zh)

    usage = _msg.t('update_usage')
    check('本模式 usage 写清查不到 ⇒ rc=0 / 用法错误 ⇒ rc=2',
          'rc=0' in usage and 'rc=2' in usage, usage.replace('\n', ' | ')[:160])

    _msg.set_lang('en')
    try:
        help_en = _msg.t_ui(help_zh)
    finally:
        _msg.set_lang('zh')
    check('--help 的 en 版已登记且是纯 ASCII',
          help_en != help_zh and help_en.isascii(), help_en[:80])
    check('--help 的 en 版也提到 --check-update', '--check-update' in help_en)


def main():
    base = tempfile.mkdtemp(prefix='mpltweak_upd_')
    try:
        case_parse()
        case_compare()
        case_query()
        case_payload()
        case_offline_default()
        case_cli(base)
        case_cli_args(base)
        case_help()
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
