# -*- coding: utf-8 -*-
"""树在变就不许开跑：给一段**必须跑在固定树上**的活儿套一层树身指纹闸门。

## 这个工具为什么存在

"两个结果互相矛盾，其实是两份代码跑出来的"这件事今晚发生了两次：

* **V1 两臂**（0.1.17 发版前）—— A 臂 / B 臂本意是同一棵树上的两次独立跑，结果一条绿
  一条红。真正的原因是两臂之间**有人改了 ``src/``**：A 臂读的是改前的引擎，B 臂读的是
  改后的引擎。两边各自都"跑对了"，可它们的结论根本不构成对照。
* **真窗口** —— 手工跑 ``tests/test_real_window.py`` 时撞上引擎改动，那一轮的通过/失败
  既不能归因给窗口，也不能归因给改动，等于白跑一轮（真窗口一轮二十多分钟，还抢焦点）。

共同点：**树动了，结果就无效**，而这个事实在跑完那一刻是看不出来的 —— 除非跑前跑后各
记一次树身指纹。``tools/preflight_matrix.py`` 内部已经有一份"每臂前后各记一次"的私有
实现（只管 ``src/tests/tools`` 下的 ``*.py``，只服务于矩阵自己）；本工具把它抽成**通用闸门**，
扫描面是**整棵树的受版本管理的文件**，可以包住任何命令。

## 用法

    # ① 记一次"干净口径"的指纹（跑之前）
    python tools/guard_tree_stable.py --record

    # ② 与记录比对（跑之后）；不一致 → rc=1 + 人话
    python tools/guard_tree_stable.py --verify

    # ③ 一条命令包住：跑前 record、跑后 verify，命令 rc 原样透传
    python tools/guard_tree_stable.py --wrap -- python -m pytest -q
    python tools/guard_tree_stable.py --wrap -- python tests/test_real_window.py

**两臂 / 真窗口 / 多版本矩阵必须用它包起来**（见 ``docs/dev.md``「树在变就不许开跑」一节）：
这三类活儿的共同成本是"一轮很久、结论很贵"，而它们被污染的机制完全相同。

## 退出码

| rc | 含义 |
|---:|---|
| 0 | 树没变（``--wrap`` 时**命令自己也 rc=0**） |
| 命令的 rc | ``--wrap`` 下树没变 → **原样透传**命令退出码 |
| 1 | ``--verify`` 比对不一致 → 树在跑动期间改过，本次结果无效 |
| **97** | ``--wrap`` 下树变了 → **专用退出码**（结果无效，与命令自己的 rc 无关） |
| 2 | 用法错误 / 没记过指纹就 --verify（只打印人话，不抛栈） |

为什么 97 要**压过**命令自己的 rc：树变了的结果**无论如何都不能用**，如果只在命令 rc=0
时才标红，那最危险的情况（命令"绿"但跑在两版树上）恰好漏网。命令自己的 rc 仍会打印出来
供参考，只是不再作为本进程的退出码。

## 口径（重要）

* 扫描面默认 = **受版本管理的文件**（``git ls-files``）。不扫 ``__pycache__`` / ``.pytest_cache``
  / ``.tmp_*/`` / ``*.json.lock`` 这类跑动期产物 —— 它们每一轮都在变，纳进来会把**每一轮**
  都误判成"树变了"（0.1.16 矩阵首跑就是这么假红一片的）。
* ``--include-untracked`` 把"未跟踪但没被忽略"的文件也纳入（想在跑动期间抓"新冒出来的
  模块"时用；默认不开，因为探针目录天然在变）。
* 指纹四项：**文件数 / 总字节 / 总行数（LF 个数）/ 逐文件 sha256**。前三项给人一眼看懂，
  第四项负责定位到具体文件、也负责抓"字节数恰好相同"的改法。
* 指纹里**不写盘符绝对路径**（状态文件可能被复制到另一台机器上比对）；只记仓库目录名。
* 状态文件默认落在**系统临时目录**（``%TEMP%\\mpltweak_tree_<仓库哈希>.json``）—— **不进仓库**：
  ``--record`` 是最常敲的一条命令，默认值若落在仓库里，谁跑一次就在工作区多一个文件。
  仓库里只放**``--state`` 显式指定**的路径（那种情形工具会把它从指纹里摘掉，见
  ``rel_if_inside``）。``--wrap`` 下默认用一次性临时文件，跑完即删 —— 两臂并行不会互相覆盖。

不做的事（写清，免得被当成"它保证干净"）：**本工具不判断树"干净不干净"，只判断树
"跑动期间有没有变"**。在脏树上跑是完全合法的用法，只要跑前跑后是同一份脏。
"""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time

#: 树变了时 ``--wrap`` 的专用退出码（见模块文档的退出码表）
TREE_CHANGED_RC = 97

#: 仓库根：本文件在 <repo>/tools/ 下
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: 仓库根的指纹文件名（**只在** `--state` 显式指进仓库时才会用到；已进 .gitignore）
DEFAULT_STATE = '.tree_fingerprint.json'

#: 状态文件格式版本（将来改结构时用来给人话，而不是静默按新口径读旧文件）
STATE_VERSION = 1

#: 没有 git（或目录不是仓库）时，退化成走目录：这些目录整棵跳过
WALK_SKIP_DIRS = ('.git', '__pycache__', '.pytest_cache', '.hypothesis',
                  '.tweak_params', 'build', 'dist', '.eggs', 'node_modules',
                  '.mypy_cache', '.tox')
#: 走目录时跳过的**文件名前缀**：测试自己造的临时目录（`tests/.tmp_t83/…`）
WALK_SKIP_PREFIXES = ('.tmp_', 'tmp_')
#: 走目录时跳过的**后缀**：跑动期产物与编辑器的原子写残片
WALK_SKIP_SUFFIXES = ('.pyc', '.pyo', '.lock', '.tweak.bak', '.orig.py')


def _say(msg=''):
    print(msg)


def _err(msg):
    sys.stderr.write('guard_tree_stable: ' + msg + '\n')


# ------------------------------------------------------------------ git 探测
def _run_git(repo, args):
    """在 ``repo`` 里跑一条 git 命令 → ``(rc, stdout_bytes)``；git 不可用 → ``(None, b'')``。

    只看 rc 与 stdout：这里的用途全是"列文件"，stderr 的每个字要么没意义、要么
    已经由 rc 表达了。**不抛栈**：没有 git 的环境（有的用户只是下载了 zip）必须
    平滑退化成走目录，而不是把闸门变成一个新的失败点。
    """
    try:
        p = subprocess.Popen(['git'] + list(args), cwd=repo,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        out, _ = p.communicate(timeout=180)
    except (OSError, subprocess.SubprocessError):
        return None, b''
    return p.returncode, out


def _split_z(raw):
    """``git -z`` 的输出 → 路径列表（``\\0`` 分隔，路径不经 C 引号，原样是文件系统字节）。"""
    return [os.fsdecode(chunk) for chunk in raw.split(b'\0') if chunk]


def rel_if_inside(repo, path):
    """→ ``path`` 相对 ``repo`` 的 posix 形式；不在 repo 里 → ``None``。

    **用途只有一个**：把状态文件从"树身"里摘出去。``--state`` 落在仓库里是最自然的
    用法（``--record`` 之后就摆在手边），而它自己每跑一次就变 —— 不摘掉的话
    ``--verify`` 会稳定地报"树变了"，闸门当场变成噪音源。

    状态文件在**另一个盘**上（``--wrap`` 默认就把它放进 ``%TEMP%``）时 ``relpath``
    会抛 ``ValueError``：那当然"不在 repo 里"，返回 ``None`` 即可。
    """
    try:
        rel = os.path.relpath(os.path.abspath(path), os.path.abspath(repo))
    except ValueError:  # Windows: 状态文件在别的驱动器上
        return None
    rel = rel.replace('\\', '/')
    if rel == '..' or rel.startswith('../') or os.path.isabs(rel):
        return None
    return rel


def select_files(repo, include_untracked=False, exclude=()):
    """挑出"算进树身"的文件 → ``(scope, [相对路径…])``，相对路径一律用 ``/`` 分隔。

    优先 ``git ls-files``（受版本管理的文件）：这是"树"最不含糊的定义，且天然把
    ``__pycache__`` / ``.tmp_*`` / ``*.json.lock`` 这些跑动期产物挡在外面。
    git 不可用或不是仓库时退化成走目录 + 黑名单，口径写在 ``WALK_SKIP_*``。

    ``exclude``：要摘掉的相对路径（放进来的只有状态文件，见 ``rel_if_inside``）。
    """
    skip = {e for e in exclude if e}
    if include_untracked:
        rc, raw = _run_git(repo, ['ls-files', '-z', '--cached', '--others',
                                  '--exclude-standard'])
        scope = 'git-tracked+untracked'
    else:
        rc, raw = _run_git(repo, ['ls-files', '-z'])
        scope = 'git-tracked'
    if rc == 0:
        rels = sorted({r.replace('\\', '/') for r in _split_z(raw) if r} - skip)
        return scope, rels

    scope = 'walk'
    rels = []
    for dirpath, dirnames, filenames in os.walk(repo):
        dirnames[:] = [d for d in dirnames
                       if d not in WALK_SKIP_DIRS
                       and not d.startswith(WALK_SKIP_PREFIXES)]
        for fn in filenames:
            if fn.startswith(WALK_SKIP_PREFIXES) or fn.endswith(WALK_SKIP_SUFFIXES):
                continue
            if fn == DEFAULT_STATE or fn.startswith('.tree_fingerprint') \
                    or fn.startswith('.guard_tree_'):
                continue
            p = os.path.join(dirpath, fn)
            rels.append(os.path.relpath(p, repo).replace('\\', '/'))
    return scope, sorted(set(rels) - skip)


# --------------------------------------------------------------- 树身指纹
def fingerprint(repo, include_untracked=False, exclude=()):
    """记一次树身指纹 → ``{'scope', 'files', 'bytes', 'newlines', 'rows'}``。

    * ``files`` / ``bytes`` / ``newlines``：三项给人一眼看懂"变了没有"（行数按 LF 计；
      CRLF 文件里一行仍是一个 LF，所以它稳定）；
    * ``rows``：``{相对路径: sha256}``，负责定位到具体文件，也负责抓"字节数恰好相同"
      的改动（只比前四项的总量是抓不到那种的）；
    * 读不到的文件（受版本管理但已被删 / 权限不足）记成 ``"MISSING"`` —— 它本身
      就是一次树变动，不能被静默跳过。
    """
    scope, rels = select_files(repo, include_untracked=include_untracked,
                               exclude=exclude)
    rows = {}
    total_bytes = 0
    total_newlines = 0
    missing = 0
    for rel in rels:
        p = os.path.join(repo, rel.replace('/', os.sep))
        try:
            with open(p, 'rb') as f:
                raw = f.read()
        except OSError:
            rows[rel] = 'MISSING'
            missing += 1
            continue
        rows[rel] = hashlib.sha256(raw).hexdigest()
        total_bytes += len(raw)
        total_newlines += raw.count(b'\n')
    return {'scope': scope, 'files': len(rows), 'bytes': total_bytes,
            'newlines': total_newlines, 'rows': rows, 'missing': missing}


def fp_triple(fp):
    """→ ``files / bytes / newlines`` 三元组（打印与比对都只认这三个数）。"""
    return (fp['files'], fp['bytes'], fp['newlines'])


def fp_triple_text(fp):
    return 'files=%d bytes=%d newlines=%d' % fp_triple(fp)


def drift(before, after):
    """→ ``(消失, 新增, 内容变化)`` 三个已排序列表。"""
    b, a = before.get('rows') or {}, after.get('rows') or {}
    gone = sorted(set(b) - set(a))
    new = sorted(set(a) - set(b))
    changed = sorted(k for k in set(a) & set(b) if a[k] != b[k])
    return gone, new, changed


def has_drift(before, after):
    gone, new, changed = drift(before, after)
    return bool(gone or new or changed)


def drift_lines(before, after, limit=10):
    """把漂移写成**人话**（每条一行，超量折叠）—— 这是给人看的部分，不许只有 rc=1。"""
    gone, new, changed = drift(before, after)
    out = []
    for title, items in (('消失', gone), ('新增', new), ('内容变化', changed)):
        if not items:
            continue
        shown = items[:limit]
        tail = '' if len(items) <= limit else '（还有 %d 个没列）' % (len(items) - limit)
        out.append('  %s %d 个%s：' % (title, len(items), tail))
        for rel in shown:
            out.append('    - %s' % rel)
    return out


# ------------------------------------------------------------------ 状态文件
def default_state_path(repo):
    """默认状态文件 → **系统临时目录**里的固定名（按仓库区分，不进仓库）。

    为什么不放仓库里：``--record`` 是最常敲的一条命令，默认值落在仓库里就意味着
    "谁跑一次就在工作区里多一个文件"（哪怕已经 gitignore，``git status`` 干净不代表
    目录干净）。仓库里只许放**``--state`` 显式指定**的路径。

    "固定名 + 按仓库区分"是为了 ``--record`` 与 ``--verify`` 跨进程、跨终端能对上同一
    份记录；名字用仓库绝对路径的 sha1 前 8 位，既稳定又不把 `D:\\...` 这类绝对路径
    泄进文件名（也不会有两个同名仓库互相踩）。
    """
    tag = hashlib.sha1(os.path.abspath(repo).encode('utf-8', 'replace')).hexdigest()[:8]
    return os.path.join(tempfile.gettempdir(), 'mpltweak_tree_%s.json' % tag)


def state_payload(fp, repo, action):
    """组状态文件内容。

    **只记仓库目录名，不记绝对路径**：状态文件会被复制到别的机器/别的 worktree 上比对
    （"这份结果是不是同一棵树跑出来的"），写死 `D:\\...` 就立刻失效。
    """
    return {
        'tool': 'guard_tree_stable',
        'version': STATE_VERSION,
        'repo_basename': os.path.basename(os.path.abspath(repo)),
        'action': action,
        'recorded_at': time.strftime('%Y-%m-%dT%H:%M:%S'),
        'fingerprint': fp,
    }


def write_state(path, payload):
    """原子写状态文件（先写同目录临时件再 ``os.replace``）—— 半截文件比没有文件更危险。"""
    parent = os.path.dirname(os.path.abspath(path)) or '.'
    fd, tmp = tempfile.mkstemp(prefix='.guard_tree_', suffix='.json', dir=parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as f:
            json.dump(payload, f, ensure_ascii=False, indent=1, sort_keys=True)
            f.write('\n')
        os.replace(tmp, path)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_state(path):
    """读状态文件 → ``payload``；读不到/结构不对 → ``None``（调用方给人话，不抛栈）。"""
    try:
        with open(path, encoding='utf-8') as f:
            payload = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    fp = payload.get('fingerprint')
    if not isinstance(fp, dict) or 'rows' not in fp:
        return None
    return payload


def _dirty_count(repo):
    """工作树里未提交改动的条数（**只作信息**：闸门只查"跑动期间有没有变"）。"""
    rc, raw = _run_git(repo, ['status', '--porcelain'])
    if rc != 0:
        return None
    return len([ln for ln in raw.decode('utf-8', 'replace').splitlines() if ln.strip()])


# ------------------------------------------------------------------ 三个动作
def do_record(repo, state_path, include_untracked=False):
    """``--record``：记一次指纹（默认落在仓库根的 ``.tree_fingerprint.json``）。"""
    fp = fingerprint(repo, include_untracked=include_untracked,
                     exclude=[rel_if_inside(repo, state_path)])
    write_state(state_path, state_payload(fp, repo, 'record'))
    _say('[guard] 已记指纹（scope=%s）：%s' % (fp['scope'], fp_triple_text(fp)))
    dirty = _dirty_count(repo)
    if dirty:
        _say('[guard] 注意：工作树有 %d 处未提交改动 —— 不影响判定（闸门只查跑动期间'
             '有没有变），但如果你期望的是"干净树口径"，先去 commit / stash。' % dirty)
    _say('[guard] 状态文件：%s' % state_path)
    return 0


def do_verify(repo, state_path):
    """``--verify``：与记录比对；不一致 → rc=1 + 人话「树在跑动期间改过，本次结果无效」。"""
    payload = read_state(state_path)
    if payload is None:
        _err('比对失败：读不到指纹 %s（或格式不对）。先用 --record 记一次：\n'
             '    python tools/guard_tree_stable.py --record' % state_path)
        return 2
    if payload.get('version') != STATE_VERSION:
        _err('指纹文件版本是 %r，本工具只认 %d —— 请用当前版本重新 --record'
             % (payload.get('version'), STATE_VERSION))
        return 2
    before = payload['fingerprint']
    after = fingerprint(repo,
                        include_untracked=before.get('scope') == 'git-tracked+untracked',
                        exclude=[rel_if_inside(repo, state_path)])
    _say('[guard] 跑前 %s' % fp_triple_text(before))
    _say('[guard] 跑后 %s' % fp_triple_text(after))
    if not has_drift(before, after):
        _say('[guard] OK：树在跑动期间没变（记录的 %s 仍然成立）'
             % payload.get('recorded_at'))
        return 0
    _err('树在跑动期间改过，本次结果无效（记于 %s）' % payload.get('recorded_at'))
    for line in drift_lines(before, after):
        _err(line)
    _err('同一份结论必须跑在同一棵树上：把改动收起来（或 commit）之后重跑，'
         '别拿这一轮的结果去下判断。')
    return 1


def do_wrap(repo, state_path, cmd, include_untracked=False):
    """``--wrap -- <命令>``：跑前 record、跑后 verify，命令 rc 原样透传。

    专用退出码：树变了 → ``TREE_CHANGED_RC``(97)，**压过命令自己的 rc**（见模块文档）。
    ``state_path`` 为 ``None`` 时用系统临时目录里的一次性文件（两臂并行不互相覆盖）。
    """
    if not cmd:
        _err('--wrap 后面没有命令：用法 `--wrap -- <命令…>`（`--` 是分隔符，必须写）')
        return 2
    tmp_state = None
    if state_path is None:
        fd, tmp_state = tempfile.mkstemp(prefix='guard_tree_', suffix='.json')
        os.close(fd)
        state_path = tmp_state
    try:
        exclude = [rel_if_inside(repo, state_path)]
        fp = fingerprint(repo, include_untracked=include_untracked, exclude=exclude)
        write_state(state_path, state_payload(fp, repo, 'wrap-pre'))
        _say('[guard] 跑前 %s（%s）→ %s' % (fp_triple_text(fp), fp['scope'],
                                          ' '.join(cmd)))
        try:
            rc = subprocess.call(list(cmd), cwd=repo)
        except OSError as exc:
            _err('命令起不来（%s）：%s' % (exc, ' '.join(cmd)))
            return 2
        after = fingerprint(repo, include_untracked=include_untracked,
                            exclude=exclude)
        _say('[guard] 跑后 %s；命令 rc=%d' % (fp_triple_text(after), rc))
        if has_drift(fp, after):
            _err('树在跑动期间改过，本次结果无效（命令 rc=%d 仅供参照）' % rc)
            for line in drift_lines(fp, after):
                _err(line)
            _err('rc=%d 是本闸门的专用退出码：这一轮不能拿来下判断，收好改动重跑。'
                 % TREE_CHANGED_RC)
            return TREE_CHANGED_RC
        _say('[guard] OK：树在跑动期间没变，命令 rc=%d 原样透传' % rc)
        return rc
    finally:
        if tmp_state:
            try:
                os.unlink(tmp_state)
            except OSError:
                pass


# ------------------------------------------------------------------ CLI
def build_parser():
    p = argparse.ArgumentParser(
        prog='guard_tree_stable.py',
        description='树在变就不许开跑：跑前/跑后记一次树身指纹，变了就标红。',
        epilog='退出码：0 = 树没变（--wrap 下命令也 rc=0）；'
               '命令 rc 原样透传；1 = --verify 不一致；'
               '%d = --wrap 下树变了（专用）；2 = 用法错误。'
               % TREE_CHANGED_RC,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument('--record', action='store_true',
                   help='记一次树身指纹（files/bytes/newlines + 逐文件 sha256）')
    g.add_argument('--verify', action='store_true',
                   help='与记录比对；不一致 → rc=1 + 人话「树在跑动期间改过」')
    g.add_argument('--wrap', action='store_true',
                   help='跑前 record、跑后 verify，命令 rc 原样透传（命令写在 `--` 之后）')
    p.add_argument('--state', default=None,
                   help='状态文件路径（默认放系统临时目录里的 mpltweak_tree_<仓库哈希>.json，'
                        '不进仓库；--wrap 下默认用一次性临时文件）')
    p.add_argument('--repo', default=REPO_ROOT,
                   help='要盯的树（默认本工具所在仓库的根目录）')
    p.add_argument('--include-untracked', action='store_true',
                   help='把"未跟踪但没被忽略"的文件也算进树身（默认不算）')
    return p


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    # `--` 之后的全部原样当命令：不用 argparse.REMAINDER，因为它会把 `--`
    # 自己（或被 `--` 吞掉的选项）留在列表里，踩过坑的写法不值得再踩一次。
    if '--' in argv:
        cut = argv.index('--')
        head, cmd = argv[:cut], argv[cut + 1:]
    else:
        head, cmd = argv, []
    parser = build_parser()
    try:
        args = parser.parse_args(head)
    except SystemExit as exc:                  # argparse 自己已经打好用法人话
        return 0 if exc.code in (0, None) else 2
    repo = os.path.abspath(args.repo)
    if not os.path.isdir(repo):
        _err('要盯的树不存在：%s' % repo)
        return 2
    if args.wrap:
        return do_wrap(repo, args.state, cmd, include_untracked=args.include_untracked)
    state = args.state or default_state_path(repo)
    if cmd:
        _err('只有 --wrap 才接受 `--` 之后的命令；--record / --verify 不认识多余参数：%r'
             % (cmd,))
        return 2
    if args.record:
        return do_record(repo, state, include_untracked=args.include_untracked)
    return do_verify(repo, state)


if __name__ == '__main__':
    sys.exit(main())
