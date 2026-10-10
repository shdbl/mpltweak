# -*- coding: utf-8 -*-
"""mpltweak revert —— 从备份回退一次写回（"完整本地回路"缺的那一环）。

设计要点
--------
* **备份在哪**：每次 `apply --write` 都会先 `_make_backup()`，在
  ``.tweak_params/<脚本名>.tweak.bak`` 留一份**写回前**的原文（并发时另有带 pid 的
  副本）。revert 就是把它拿回来 —— 不需要额外机制，这个备份一直存在。
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
import shutil
import sys

from . import messages as _msg
from . import params, writeback

MAX_DIFF_LINES = 20          # 预览里最多列这么多行差异（够看出发生了什么）


def backup_path(script: str, params_path: str = None) -> str:
    """回退用的备份路径（与 writeback 共用同一套命名，别各写一份）。"""
    pp = params_path or params.params_path(script)
    return writeback._backup_path(script, pp)


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
    """回退前给当前文件留一份（revert 自身可逆），返回路径。"""
    d = os.path.dirname(os.path.abspath(params_path))
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


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog='mpltweak revert',
        description='把脚本回退到上一次 apply --write 之前的状态（用 '
                    '.tweak_params/<脚本名>.tweak.bak）')
    ap.add_argument('script')
    ap.add_argument('--params', default=None, help='参数文件（默认 .tweak_params/<脚本>.json）')
    ap.add_argument('--write', action='store_true',
                    help='真的回退（不加这个只预览，不动代码）')
    ap.add_argument('--json', action='store_true', help='机器可读输出')
    args = ap.parse_args(argv if argv is not None else sys.argv[1:])

    script = os.path.abspath(args.script)
    if not os.path.exists(script):
        if args.json:
            _msg.fail_json('file_not_found', 'script not found: %s' % script)
        sys.stderr.write('[revert] 找不到脚本: %s\n' % script)
        return 2
    params_path = params.params_path(script, args.params)
    backup = backup_path(script, params_path)

    def _out(payload, human_lines, rc=0):
        if args.json:
            _msg.emit_json(payload)
        else:
            for ln in human_lines:
                print(ln)
        return rc

    if not os.path.exists(backup):
        msg = ('还没有可回退的备份：%s\n'
               '  revert 只能回退"做过 mpltweak apply --write 的脚本"——'
               '备份是写回时留下的写回前原文。' % backup)
        if args.json:
            _msg.fail_json('no_backup', msg)
        sys.stderr.write('[revert] %s\n' % msg)
        return 2

    cur_text = _read_text(script)
    bak_text = _read_text(backup)
    if cur_text == bak_text:
        # 键保持稳定：agent 不该因为"有没有差异"而拿到不同形状的 payload
        return _out({'script': os.path.basename(script),
                     'backup': backup, 'ok': True, 'changed': False,
                     'diff': {'added': 0, 'removed': 0}, 'lines': [],
                     'written': False},
                    ['已一致：%s 与备份内容相同（没有可回退的写回）。'
                     % os.path.basename(script)])

    added, removed, show = _diff_summary(cur_text, bak_text)
    payload = {
        'script': os.path.basename(script),
        'backup': backup,
        'backup_time': _rel_time(backup),
        'ok': True,
        'changed': True,
        'diff': {'added': added, 'removed': removed},
        'lines': show,
        'written': False,
    }
    human = ['回退预览：%s  ←  %s（备份于 %s）'
             % (os.path.basename(script), backup, _rel_time(backup)),
             # added = unified_diff(current → backup) 的 '+' 行 = **会被恢复**的行；
             # removed = '-' 行 = 会被删掉的行。这两个参数曾经写反：块写回后
             # 当前 40 行 / 备份 9 行时打印成"将改回 31 行、去掉 0 行"（独立审阅 F3）。
             '  将恢复 %d 行、删除 %d 行：' % (added, removed)]
    human += ['    ' + ln for ln in show]
    if len(show) >= MAX_DIFF_LINES:
        human.append('    …（只显示前 %d 行差异）' % MAX_DIFF_LINES)

    if not args.write:
        human += ['', '  这是预览，你的代码没有被改动。确认回退：'
                      'mpltweak revert %s --write' % os.path.basename(script)]
        return _out(payload, human)

    lock = None
    try:
        lock = writeback.script_lock(script)
        lock.__enter__()
    except RuntimeError as e:
        payload.update({'ok': False, 'error': str(e)})
        if args.json:
            _msg.emit_json(payload)
        sys.stderr.write('[revert] 拿不到写回锁：%s\n' % e)
        return 1
    except Exception as e:                                  # noqa: BLE001
        payload.update({'ok': False, 'error': str(e)})
        if args.json:
            _msg.emit_json(payload)
        sys.stderr.write('[revert] 拿不到写回锁：%s\n' % e)
        return 1
    try:
        keep = _snapshot_current(script, params_path)
        try:
            _atomic_restore(script, backup)
        except OSError as e:
            payload.update({'ok': False, 'error': '写文件失败: %s' % e})
            if args.json:
                _msg.emit_json(payload)
            sys.stderr.write('[revert] 写文件失败: %s（原文件未改动）\n' % e)
            return 1
        # 回退后脚本比参数新 → 下次开窗会拒绝套用旧参数（防止静默覆盖）。
        # 想接着上次调，加 --force-resume。这里只把这个事实告诉用户。
        human += ['', '√ 已回退：%s' % script,
                  '  回退前的那份留档：%s（再回退一次就回到这里）' % keep,
                  '  提示：脚本比参数新了，下次开窗不会自动套用旧参数；'
                  '确要接着调加 --force-resume']
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
