# -*- coding: utf-8 -*-
"""``mpltweak --check-update``：**显式**联网查 PyPI 上有没有新版本。

── 产品原则（守住它，别顺手加东西）────────────────────────────────────────
**默认任何命令都不联网。** 整棵树里只有本模块发起网络请求，而且只有用户 / agent
**明确**敲了 ``--check-update`` 才会走到这里（``tests/test_check_update.py`` 有一条
静态棘轮盯着：除本模块外 ``src/mpltweak`` 不许 import ``urllib`` / ``socket`` /
``http.client`` / ``requests``）。

── 四条硬约束（0.1.17）────────────────────────────────────────────────────
1. **只查不升**：不 ``pip install``、不改脚本、不写任何文件（连缓存都不留）。
2. **查不到不算命令失败**：断网 / 被墙 / 超时 / PyPI 改格式，一律 rc=0 —— 这是
   "顺带问一句"的辅助动作，不该让 CI 或 ``&&`` 链条在你的网络抖动时红掉。
   用法错误（多给了不认的参数）才是 rc=2。
3. **默认 5 秒超时**（``DEFAULT_TIMEOUT``）：堵在 DNS / TLS 握手上也不许让用户干等。
4. **不抛 Traceback**：连不上的每一种姿势都要落成一句人话 + 一句"不影响你用"。

── 机器可读（``--json``）──────────────────────────────────────────────────
``{"current": "0.1.16", "latest": "0.1.17", "update_available": true,
   "error": null, "error_detail": null}``

* ``latest``：查不到时为 ``null``；
* ``update_available``：``true`` 只有"PyPI 比本地新"这一种；"同版本 / 本地更新"
  为 ``false``；查不到或版本号比不出（``rc1`` / ``dev0``）为 ``null``
  —— 不许把"不知道"说成 ``false``（那会让 agent 报"已是最新"，是假话）；
* ``error``：``null`` 或稳定英文原因码 ``timeout`` / ``network`` / ``http`` /
  ``payload``（**不随界面语言漂移**，agent 按它分支）；人话看 ``error_detail``。

── 测试钩子（只给测试用，不在 ``--help`` 里宣传）──────────────────────────
``MPLTWEAK_PYPI_URL`` 换请求地址（``file://`` 也能读，测试因此**零联网**）；
``MPLTWEAK_UPDATE_TIMEOUT`` 换超时秒数（注入超时用，比如 ``0.01``）。
"""

from __future__ import annotations

import json
import os
import re
import socket
import sys
import urllib.error
import urllib.request

from . import __version__
from . import messages as _msg

# ==== §1 常量：地址 / 超时 / 原因码 ====

#: PyPI 的 JSON API（``info.version`` 就是"最新发布的版本号"）。
PYPI_URL = 'https://pypi.org/pypi/mpltweak/json'

#: 给人看的项目页（失败时那句"手动看这里"用它，别让人去猜 URL）。
PROJECT_URL = 'https://pypi.org/project/mpltweak/'

#: 默认超时（秒）。5 秒是"顺带问一句"的耐心上限：再长用户会以为命令卡死。
DEFAULT_TIMEOUT = 5.0

#: 升级命令（**只打印，绝不替你执行**）。
UPGRADE_CMD = 'pip install -U mpltweak'

#: 环境变量：地址 / 超时（测试钩子，见模块 docstring）。
ENV_URL = 'MPLTWEAK_PYPI_URL'
ENV_TIMEOUT = 'MPLTWEAK_UPDATE_TIMEOUT'

#: 失败原因码（**英文、稳定**；值与 ``--json`` 的 ``error`` 字段一一对应）。
REASON_TIMEOUT = 'timeout'
REASON_NETWORK = 'network'
REASON_HTTP = 'http'
REASON_PAYLOAD = 'payload'

#: 本模式认的开关（其它一律 rc=2，免得像 0.1.8 的 doctor 那样"静默忽略"）。
FLAGS_BOOL = ('--check-update', '--version', '-V', '--json', '-h', '--help')
FLAGS_VALUE = ('--lang',)



class PayloadError(ValueError):
    """PyPI 返回的内容解析不了（格式变了 / 截断 / 不是 JSON）。

    继承 ``ValueError`` 是为了让"解析失败"与"网络失败"在 ``query`` 里分得开：
    前者是我们读不懂它的响应，后者是根本没连上。
    """


# ==== §2 网络层：唯一一处真正发请求的地方（测试 monkeypatch 这里）====

def _open_url(url, timeout):
    """打开 URL（**唯一**的网络出口；测试用 monkeypatch 换掉它，不必联网）。

    单独抽成一层不是为了"架构好看"：``tests/test_check_update.py`` 的三条失败路径
    （超时 / 拒连 / HTTP 错误）全靠替换本函数注入，测试因此一次都不联网。
    """
    return urllib.request.urlopen(url, timeout=timeout)


def _url():
    """本次要请求的地址（``MPLTWEAK_PYPI_URL`` 可换，测试用 ``file://``）。"""
    return os.environ.get(ENV_URL) or PYPI_URL


def _timeout():
    """本次超时秒数（``MPLTWEAK_UPDATE_TIMEOUT`` 可换；垃圾值退回默认）。"""
    raw = os.environ.get(ENV_TIMEOUT)
    if not raw:
        return DEFAULT_TIMEOUT
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return DEFAULT_TIMEOUT
    # 0 / 负数不是"立刻超时"而是"交给 socket 当阻塞"——统一退回默认，别制造惊喜。
    return value if value > 0 else DEFAULT_TIMEOUT


def _detail(exc):
    """异常 → 一行短说明（**不是** Traceback：用户要的是"怎么了"，不是栈）。"""
    text = str(exc).strip()
    return text or type(exc).__name__


# ==== §3 解析与比较（纯函数：不碰网络、不碰文件）====

def parse_latest(raw):
    """PyPI JSON（``bytes`` 或 ``str``）→ ``info.version`` 字符串。

    只认 PyPI 那个结构：``{"info": {"version": "0.1.17"}, ...}``。少一层、类型不对、
    版本号空白，一律 ``PayloadError`` —— 宁可说"读不懂"，也不许把 ``None`` 当版本号
    流到比较里去（那会出现"已是最新：None"这种假话）。
    """
    if isinstance(raw, bytes):
        try:
            text = raw.decode('utf-8')
        except UnicodeDecodeError as e:
            raise PayloadError('not UTF-8: %s' % _detail(e))
    else:
        text = raw
    try:
        doc = json.loads(text)
    except ValueError as e:
        raise PayloadError('not JSON: %s' % _detail(e))
    if not isinstance(doc, dict):
        raise PayloadError('JSON root is %s, not an object' % type(doc).__name__)
    info = doc.get('info')
    if not isinstance(info, dict):
        raise PayloadError('no "info" object in the response')
    version = info.get('version')
    if not isinstance(version, str) or not version.strip():
        raise PayloadError('info.version is missing or blank')
    return version.strip()


def version_key(text):
    """``'0.1.16'`` → ``(0, 1, 16)``；不是纯数字点版本（``0.1.17rc1``）→ ``None``。

    口径：**只认纯数字点**。带后缀（``rc1`` / ``dev0`` / ``post1``）就返回 ``None``，
    上层报"比不出"，而不是擅自替用户决定"rc1 比正式版老"——猜错了会让人以为
    自己已经是最新版（或反过来白升级一次）。
    """
    if not isinstance(text, str):
        return None
    parts = text.strip().split('.')
    if not parts or not all(re.fullmatch(r'\d+', p) for p in parts):
        return None
    return tuple(int(p) for p in parts)


def compare(current, latest):
    """比较两个版本号 → ``'latest'`` / ``'newer'`` / ``'older'`` / ``'unknown'``。

    * ``latest``：同版本（``0.1.16`` 与 ``0.1.16.0`` 也算同版本）；
    * ``newer``：PyPI 比本地新（**唯一**会建议升级的情形）；
    * ``older``：本地比 PyPI 新（开发版 / 预发布，常见于 ``pip install -e .``）；
    * ``unknown``：任一版本号带后缀或不是字符串 ⇒ 老实说"比不出"。
    """
    left = version_key(current)
    right = version_key(latest)
    if left is None or right is None:
        return 'unknown'
    width = max(len(left), len(right))
    left = left + (0,) * (width - len(left))
    right = right + (0,) * (width - len(right))
    if left == right:
        return 'latest'
    return 'newer' if right > left else 'older'


def query(url=None, timeout=None):
    """查一次 PyPI → ``(latest, reason, detail)``：成功 ``(版本号, None, None)``。

    四种失败各自归到稳定原因码，**不抛异常**（调用方只有一个分支要写，用户也永远
    看不到 Traceback）：

    ``timeout`` 5 秒内没连上 / ``network`` 连不上（DNS、拒连、TLS）
    / ``http`` PyPI 给了错误码 / ``payload`` 返回内容读不懂。
    """
    target = url or _url()
    limit = timeout if timeout is not None else _timeout()
    try:
        with _open_url(target, limit) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as e:
        # 顺序要紧：HTTPError 是 URLError 的子类，先接住它才能拿到状态码。
        return None, REASON_HTTP, 'HTTP %s' % (e.code,)
    except urllib.error.URLError as e:
        inner = getattr(e, 'reason', None)
        if isinstance(inner, (socket.timeout, TimeoutError)):
            return None, REASON_TIMEOUT, 'timed out after %ss' % limit
        return None, REASON_NETWORK, _detail(inner if inner is not None else e)
    except (socket.timeout, TimeoutError):
        return None, REASON_TIMEOUT, 'timed out after %ss' % limit
    except OSError as e:
        # 连接被重置 / TLS 证书 / 读一半断了 —— 都算"连不上"，不是我们读不懂。
        return None, REASON_NETWORK, _detail(e)
    except ValueError as e:
        # urlopen 对畸形地址（比如 file:// 路径不存在）抛 ValueError。
        return None, REASON_NETWORK, _detail(e)
    try:
        return parse_latest(raw), None, None
    except PayloadError as e:
        return None, REASON_PAYLOAD, _detail(e)


# ==== §4 输出：人话 / --json ====

def _reason_text(reason):
    """原因码 → 一句人话（四个分支各写死一个字面量键）。

    为什么不用 ``t('update_reason_' + reason)`` 或查表：``tools/msg_key_audit.py``
    只认**字面量**键（``t('字面量')``），拼接/查表会被算成"未解析调用点"，而那条
    棘轮是"只减不增"的 —— 为了少写四行去顶破它，等于把键名写错的风险留给用户
    （``t()`` 缺键会把键名原样回显）。四条分支平铺就好。
    """
    if reason == REASON_TIMEOUT:
        return _msg.t('update_reason_timeout')
    if reason == REASON_NETWORK:
        return _msg.t('update_reason_network')
    if reason == REASON_HTTP:
        return _msg.t('update_reason_http')
    return _msg.t('update_reason_payload')


def payload(current, latest, reason, detail):
    """``--json`` 的载荷（键顺序固定；少一个键 agent 就得靠猜）。"""
    if reason:
        available = None                      # "不知道" ≠ False，见模块 docstring
    else:
        verdict = compare(current, latest)
        available = True if verdict == 'newer' else (
            False if verdict in ('latest', 'older') else None)
    return {
        'current': current,
        'latest': latest,
        'update_available': available,
        'error': reason,
        'error_detail': detail,
    }


def _print(out, text):
    """写一行（GBK 控制台也不会炸：模板全部 GBK 可编码，见 test_hygiene）。"""
    out.write(text + '\n')


def _human(out, current, latest, reason, detail):
    """人话四路：已是最新 / 有新版本 / 本地更新 / 查不到（+ 一句"不影响你用"）。"""
    if reason:
        _print(out, _msg.t('update_failed', reason=reason,
                           gloss=_reason_text(reason), detail=detail or '-'))
        _print(out, _msg.t('update_failed_hint', url=PROJECT_URL))
        return
    verdict = compare(current, latest)
    if verdict == 'latest':
        _print(out, _msg.t('update_is_latest', current=current, latest=latest))
    elif verdict == 'newer':
        _print(out, _msg.t('update_newer', current=current, latest=latest,
                           cmd=UPGRADE_CMD))
    elif verdict == 'older':
        _print(out, _msg.t('update_older', current=current, latest=latest))
    else:
        _print(out, _msg.t('update_unknown', current=current, latest=latest))


# ==== §5 入口：``mpltweak --check-update`` ====

def _parse(flags, rest):
    """校验本模式的参数 → ``(opts, error)``；``error`` 非 ``None`` 时调用方 rc=2。

    只认 ``FLAGS_BOOL`` / ``FLAGS_VALUE`` 里的开关，且**不接受位置参数**：
    ``mpltweak --check-update fig.py`` 是敲错了（想看版本却带了个脚本），
    静默忽略会让人以为脚本问题被查过了。
    """
    opts = {'version': False, 'json': False, 'help': False, 'lang': 'auto'}
    unknown = []
    for name, value in flags:
        if name not in FLAGS_BOOL and name not in FLAGS_VALUE:
            unknown.append(name)
            continue
        if name in ('--version', '-V'):
            opts['version'] = True
        elif name == '--json':
            opts['json'] = True
        elif name in ('-h', '--help'):
            opts['help'] = True
        elif name == '--lang':
            if value is None:
                unknown.append('--lang（缺值：--lang zh|en）')
            elif value not in ('auto', 'zh', 'en'):
                unknown.append('--lang %s（只认 auto / zh / en）' % value)
            else:
                opts['lang'] = value
    unknown.extend(rest)
    return opts, unknown


def main(flags=(), rest=(), out=None, err=None):
    """``mpltweak --check-update [--version] [--json] [--lang zh|en]``。

    退出码（0.1.17 起，写死在 ``--help`` 与 ``docs/agent.md``）：

    * ``0`` —— 查成功（有新版本 / 已是最新 / 本地更新），**以及查不到**（网络失败）；
    * ``2`` —— **用法错误**：多给了参数、``--lang`` 值不认识、混了位置参数。

    "查不到"给 0 是刻意的：这是辅助动作，不是闸门。脚本里 ``mpltweak --check-update
    || echo 网络不行`` 不该因为一次 DNS 抖动就红。要知道查没查到，看 ``--json``
    的 ``error`` 字段（``null`` = 查到了）。
    """
    out = sys.stdout if out is None else out
    err = sys.stderr if err is None else err
    opts, unknown = _parse(list(flags), list(rest))

    if opts['lang'] != 'auto':
        _msg.set_lang(opts['lang'])

    if unknown:
        err.write(_msg.t('update_usage') + '\n')
        err.write(_msg.t('schema_unknown_args', args=' '.join(unknown)) + '\n')
        return _msg.EXIT_USAGE
    if opts['help']:
        out.write(_msg.t('update_usage') + '\n')
        return _msg.EXIT_OK

    # 与 --version 同用时先报版本号（`mpltweak --version --check-update` 两件事都要
    # 能看到）；--json 下不印这行，否则 stdout 就不是纯 JSON 了。
    if opts['version'] and not opts['json']:
        _print(out, 'mpltweak %s' % __version__)

    latest, reason, detail = query()

    if opts['json']:
        _msg.emit_json(payload(__version__, latest, reason, detail))
    else:
        _human(out, __version__, latest, reason, detail)
    return _msg.EXIT_OK
