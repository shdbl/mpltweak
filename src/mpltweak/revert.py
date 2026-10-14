# -*- coding: utf-8 -*-
"""mpltweak revert —— 从备份回退一次写回（"完整本地回路"缺的那一环）。

设计要点
--------
* **备份在哪**：每次 `apply --write` 都会先 `_make_backup()`，在
  ``.tweak_params/<脚本名>.tweak.bak`` 留一份**写回前**的原文（并发时另有带 pid 的
  副本）。revert 就是把它拿回来 —— 不需要额外机制，这个备份一直存在。
* **可逆的范围要说清楚（RC-7）**：默认 revert 用的是**固定名**备份
  ``.tweak_params/<脚本名>.tweak.bak``，而它是**首份优先**的（T71：已存在就不再覆盖）
  → 它保存的是这份备份**建立那一刻**的原文，一般就是你**第一次对它做
  ``apply --write`` 之前**的原件 —— 不是"最近一次写回之前"的中间态。要拿"跨很多次
  写回 / 跨进程"的**最早**那一份，用 ``--first``（读 pid 副本里最早的一份；
  ``_prune_backups`` 永久保留最早那份、只裁中间的）。pid 副本写不出来时（典型是
  Windows 长路径），``--first`` **明确失败 rc=2**，不会拿固定名备份冒充"最早"（T41）。
* **默认只预览**：与 `apply` 一致，`revert` 不加 `--write` 只打印"会改回什么"，
  一个字节都不动。要真回退必须显式 `--write`。
* **revert 自身也可逆**：覆盖前先把当前文件另存为
  ``.tweak_params/<脚本名>.revert.bak``。所以"手滑 revert 了"还能再回去 ——
  这是它敢不做交互确认的前提（CLI 不阻塞，agent 也能用）。
* **原样字节恢复**：直接复制备份的**字节**（不重新编码、不规范化换行），
  所以 BOM、CRLF、GBK 脚本都原封不动地回来。预览里的 diff 才需要解码，
  解码失败只影响预览文字，不影响恢复结果。
* **不删侧边元数据**：`<脚本名>.writeback.json` 里是调整块的行号区间。回退后这些
  区间可能失效，但 `insert_or_replace` 用它之前会校验首尾哨兵、并在不匹配时回退到
  哨兵扫描 —— 删掉反而会让"脚本里原本就有的旧块"下次被重复插入。
"""

from __future__ import annotations

import argparse
import difflib
import os
import re
import shutil
import sys

from . import messages as _msg
from . import params, writeback

MAX_DIFF_LINES = 20          # 预览里最多列这么多行差异（够看出发生了什么）


def backup_path(script: str, params_path: str = None) -> str:
    """回退用的备份路径（与 writeback 共用同一套命名，别各写一份）。

    锚点是**脚本**而不是参数文件：`--params` 可以指向任何地方，备份却始终属于这个
    脚本（RC-10）。"""
    pp = params_path or params.params_path(script)
    return writeback._backup_path(script, pp)


def legacy_backup_path(script: str, params_path: str) -> str:
    """0.1.8 的旧定位（备份在 `--params` 旁边）—— 只在主位置找不到时才试。"""
    return writeback._backup_path_legacy(script, params_path)


def oldest_backup(script: str, params_path: str = None):
    """**最早**一份 pid 备份的路径（没有则 None）—— ``--first`` 用。

    默认回退用的固定名备份是**首份优先**的（只在不存在时创建）⇒ 它自己通常就代表
    "第一次改这张图之前"的原件，普通 `revert --write` 就够了。``--first`` 是给"固定名
    备份被删过 / 被旧版覆盖过"或"想明确取最早那份快照"的场景准备的：
    ``_prune_backups`` 从 0.1.9 起永久保留最早那一份，这里把它找出来（RC-7）。
    一份 pid 副本都没有时，``--first`` 会**明确失败 rc=2**，不拿固定名备份冒充"最早"。
    """
    pp = params_path or params.params_path(script)
    d = writeback._backup_dir(script, pp)
    stem = os.path.splitext(os.path.basename(script))[0]
    pat = re.compile(r'^%s\.tweak\.(\d+)\.bak$' % re.escape(stem))
    try:
        files = [os.path.join(d, n) for n in os.listdir(d) if pat.match(n)]
    except OSError:
        return None
    if not files:
        return None
    files.sort(key=lambda p: (os.path.getmtime(p), p))
    return files[0]


def _read_text(path: str) -> str:
    """尽力解码（只服务预览）：utf-8-sig 优先，退回 gbk，再退回 replace。"""
    with open(path, 'rb') as f:
        raw = f.read()
    for enc in ('utf-8-sig', 'gbk'):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode('utf-8', 'replace')


def _diff_summary(cur_text: str, bak_text: str):
    """返回 (新增行数, 删除行数, 差异行列表)。"""
    diff = list(difflib.unified_diff(cur_text.splitlines(),
                                     bak_text.splitlines(), lineterm=''))
    added = [ln for ln in diff if ln.startswith('+') and not ln.startswith('+++')]
    removed = [ln for ln in diff if ln.startswith('-') and not ln.startswith('---')]
    # 只留"会被改回去"的正文行（含 +/- 标记，方便用户一眼看出方向）
    show = (removed + added) if len(removed) + len(added) <= 400 else []
    return len(added), len(removed), show[:MAX_DIFF_LINES]


def _atomic_restore(script: str, backup: str) -> None:
    """把备份的**字节**原子地写回脚本（同 writeback 的临时文件 + os.replace）。"""
    with open(backup, 'rb') as f:
        raw = f.read()
    tmp = script + '.revert.tmp'
    with open(tmp, 'wb') as f:
        f.write(raw)
    os.replace(tmp, script)


def _snapshot_current(script: str, params_path: str) -> str:
    """回退前给当前文件留一份（revert 自身可逆），返回路径。

    与 backup_path 同一套定位（脚本旁的 `.tweak_params`），否则 `--params` 一换
    就找不到自己刚写的那份留档。"""
    d = writeback._backup_dir(script, params_path)
    stem = os.path.splitext(os.path.basename(script))[0]
    dst = os.path.join(d, stem + '.revert.bak')
    shutil.copy2(script, dst)
    return dst


def _rel_time(path: str) -> str:
    try:
        import time
        return time.strftime('%Y-%m-%d %H:%M', time.localtime(os.path.getmtime(path)))
    except OSError:
        return '?'


    # 【不许 i18n 的显式例外 · t85】预览/写回里回显的那行
    # `-# 本块由 mpltweak 自动生成于 …（…勿删上下两行标记）` 是 **writeback 的
    # 块标记哨兵**（写进用户源码、且是幂等替换锚点，t63 已定案必须保持 zh），
    # 因此 en 下允许它仍是中文；i18n 棘轮请把它登记为豁免。
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog='mpltweak revert',
        description=_msg.t_ui('把脚本回退到备份里的状态（默认用固定名 '
                    '.tweak_params/<脚本名>.tweak.bak；该备份首份优先 → 通常是'
                    '你第一次改这张图之前的原件）'))
    ap.add_argument('script')
    ap.add_argument('--params', default=None, help='参数文件（默认 .tweak_params/<脚本>.json）')
    ap.add_argument('--write', action='store_true',
                    help='真的回退（不加这个只预览，不动代码）')
    ap.add_argument('--first', action='store_true',
                    help='回到**最早**那次写回之前（读最早一份 pid 备份）；'
                         '默认回退用固定名备份（首份优先 → 通常是第一次改之前）')
    ap.add_argument('--json', action='store_true', help='机器可读输出')
    args = ap.parse_args(argv if argv is not None else sys.argv[1:])

    script = os.path.abspath(args.script)
    if not os.path.exists(script):
        if args.json:
            _msg.fail_json('file_not_found', 'script not found: %s' % script)
        sys.stderr.write(_msg.t_ui('[revert] 找不到脚本: %s\n') % script)
        return 2
    params_path = params.params_path(script, args.params)
    backup = backup_path(script, params_path)
    if args.first:
        _first = oldest_backup(script, params_path)
        if not _first:
            # T41（high）：**不许**静默回落到固定名备份 —— 那份只代表"最近一次
            # apply --write 之前"，回落到它会报 `first: true` 却给出中间态
            # （长路径下 pid 副本写不出来时就是这个现场，语料复核 A/B 实测：
            #  深目录 pid 路径 261 > MAX_PATH → 副本 0 个 → --first 回不到原件）。
            _d = writeback._backup_dir(script, params_path)
            _m = (_msg.t_ui('没有可用的 pid 备份（%s 下没有 `<脚本名>.tweak.<pid>.bak`）：'
                  '无法回到**最早**状态。\n'
                  '  · 常见原因：备份目录路径过长（Windows MAX_PATH 260），'
                  'pid 副本没写出来 —— 本次写回会给出相应 warning。\n'
                  '  · 固定名备份回到的是它建立那一刻的原文（通常是你第一次改'
                  '这张图之前），不要拿它当 --first 用；只想要这个默认回退，'
                  '请去掉 --first。')
                  % _d)
            if args.json:
                _msg.fail_json('no_first_backup', _m)
            sys.stderr.write(_msg.t_ui('[revert] %s\n') % _m)
            return 2
        backup = _first
    if not os.path.exists(backup):
        # RC-10 兼容：0.1.8 用 `--params X` 写回时备份落在 X 旁边（新版落在脚本旁）。
        # 主位置找不到就再试一次旧位置，别让用户以为"没法回退"。
        _legacy = legacy_backup_path(script, params_path)
        if _legacy != backup and os.path.exists(_legacy):
            backup = _legacy

    def _out(payload, human_lines, rc=0):
        if args.json:
            _msg.emit_json(payload)
        else:
            for ln in human_lines:
                print(ln)
        return rc

    if not os.path.exists(backup):
        msg = (_msg.t_ui('还没有可回退的备份：%s\n'
               '  revert 只能回退"做过 mpltweak apply --write 的脚本"——'
               '备份是写回时留下的写回前原文。') % backup)
        if args.json:
            _msg.fail_json('no_backup', msg)
        sys.stderr.write(_msg.t_ui('[revert] %s\n') % msg)
        return 2

    cur_text = _read_text(script)
    bak_text = _read_text(backup)
    if cur_text == bak_text:
        # 键保持稳定：agent 不该因为"有没有差异"而拿到不同形状的 payload
        return _out({'script': os.path.basename(script),
                     'backup': backup, 'ok': True, 'changed': False,
                     'diff': {'added': 0, 'removed': 0}, 'lines': [],
                     'written': False},
                    [_msg.t_ui('已一致：%s 与备份内容相同（没有可回退的写回）。')
                     % os.path.basename(script)])

    added, removed, show = _diff_summary(cur_text, bak_text)
    payload = {
        'script': os.path.basename(script),
        'backup': backup,
        'backup_time': _rel_time(backup),
        'ok': True,
        'changed': True,
        # 键保持稳定（agent 不该因为"有没有差异"拿到不同形状的 payload）；
        # first：这次用的是不是"最早那份"备份（RC-7 的可逆范围要说清楚）。
        'first': bool(args.first),
        'diff': {'added': added, 'removed': removed},
        'lines': show,
        'written': False,
    }
    human = [_msg.t_ui('回退预览：%s  ←  %s（备份于 %s）')
             % (os.path.basename(script), backup, _rel_time(backup)),
             _msg.t_ui('  可逆范围：本次用的是%s —— 默认回退用**固定名**备份，它**首份优先**'
             '（第一次写回时建立、之后不再覆盖），所以通常回到你**第一次改这张图'
             '之前**的原件，而不是上一步的中间态；要拿"最早那次写回之前"的快照用 '
             '--first')
             % (_msg.t_ui('最早一份专属副本（最早那次写回之前）')
                if args.first else _msg.t_ui('固定名备份（它建立那一刻的原文）')),
             # added = unified_diff(current → backup) 的 '+' 行 = **会被恢复**的行；
             # removed = '-' 行 = 会被删掉的行。这两个参数曾经写反：块写回后
             # 当前 40 行 / 备份 9 行时打印成"将改回 31 行、去掉 0 行"（独立审阅 F3）。
             _msg.t_ui('  将恢复 %d 行、删除 %d 行：') % (added, removed)]
    human += ['    ' + ln for ln in show]
    if len(show) >= MAX_DIFF_LINES:
        human.append('    …（只显示前 %d 行差异）' % MAX_DIFF_LINES)

    if not args.write:
        human += ['', _msg.t_ui('  这是预览，你的代码没有被改动。确认回退：'
                      'mpltweak revert %s --write') % os.path.basename(script)]
        return _out(payload, human)

    lock = None
    try:
        lock = writeback.script_lock(script)
        lock.__enter__()
    except RuntimeError as e:
        payload.update({'ok': False, 'error': str(e)})
        if args.json:
            _msg.emit_json(payload)
        sys.stderr.write(_msg.t_ui('[revert] 拿不到写回锁：%s\n') % e)
        return 1
    except Exception as e:                                  # noqa: BLE001
        payload.update({'ok': False, 'error': str(e)})
        if args.json:
            _msg.emit_json(payload)
        sys.stderr.write(_msg.t_ui('[revert] 拿不到写回锁：%s\n') % e)
        return 1
    try:
        keep = _snapshot_current(script, params_path)
        try:
            _atomic_restore(script, backup)
        except OSError as e:
            payload.update({'ok': False, 'error': '写文件失败: %s' % e})
            if args.json:
                _msg.emit_json(payload)
            sys.stderr.write(_msg.t_ui('[revert] 写文件失败: %s（原文件未改动）\n') % e)
            return 1
        # 回退后脚本比参数新 → 下次开窗会拒绝套用旧参数（防止静默覆盖）。
        # 想接着上次调，加 --force-resume。这里只把这个事实告诉用户。
        human += ['', _msg.t_ui('√ 已回退：%s') % script,
                  _msg.t_ui('  回退前的那份留档：%s（再回退一次就回到这里）') % keep,
                  _msg.t_ui('  提示：脚本比参数新了，下次开窗不会自动套用旧参数；'
                  '确要接着调加 --force-resume')]
        payload.update({'written': True, 'revert_bak': keep})
        return _out(payload, human)
    finally:
        if lock is not None:
            try:
                lock.__exit__(None, None, None)
            except Exception:                               # noqa: BLE001
                pass


if __name__ == '__main__':
    sys.exit(main())
