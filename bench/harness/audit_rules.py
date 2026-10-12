# -*- coding: utf-8 -*-
"""铁律自查：① 语料原件与 manifest 的 sha256 是否全部一致（未被改动）
② corpus018/gallery、corpus018/zenodo 下有没有本次运行新写入的文件
③ mpltweak 仓库（被测工具）是否被改动（git status）"""
import hashlib
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import l0_harness as H

out = []
now = time.time()
rows = H.load_manifest()
bad = []
missing = []
for r in rows:
    p = r.get('path') or ''
    if not p.endswith('.py'):
        continue
    fp = os.path.join(H.CORPUS, p.replace('/', os.sep))
    if not os.path.exists(fp):
        missing.append(p)
        continue
    if H.sha256_file(fp) != r.get('sha256'):
        bad.append(p)
out.append('① manifest 脚本总数 %d；原件 sha256 与 manifest 不一致：%d；缺失：%d'
           % (sum(1 for r in rows if (r.get('path') or '').endswith('.py')), len(bad), len(missing)))
for p in bad[:20]:
    out.append('   MISMATCH %s' % p)
for p in missing[:20]:
    out.append('   MISSING %s' % p)

out.append('')
out.append('② 语料目录里的"本次运行写入"痕迹（gallery/zenodo 下 24h 内新建的文件/目录）：')
recent = []
for sub in ('gallery', 'zenodo'):
    base = os.path.join(H.CORPUS, sub)
    for root, dirs, files in os.walk(base):
        for nm in list(dirs) + files:
            fp = os.path.join(root, nm)
            try:
                if now - os.path.getmtime(fp) < 86400:
                    recent.append(os.path.relpath(fp, H.CORPUS).replace('\\', '/'))
            except OSError:
                pass
out.append('   近 24h 内 mtime 变动的条目数：%d' % len(recent))
for p in recent[:40]:
    out.append('   %s' % p)

out.append('')
out.append('③ 被测工具目录是否被改动（只读要求）：')
env = dict(os.environ)
env['GIT_CONFIG_GLOBAL'] = r'D:\Agent\deepseek\.gitconfig-dsh'
try:
    p = subprocess.run(['git', 'status', '--porcelain'], cwd=H.MPLTWEAK_ROOT,
                       capture_output=True, env=env, timeout=60)
    txt = p.stdout.decode('utf-8', 'replace').strip()
    out.append('   git status --porcelain 输出：%s' % (txt if txt else '（空 = 工作区干净，未被改动）'))
    out.append('   rc=%s' % p.returncode)
except Exception as e:                                        # noqa: BLE001
    out.append('   git 不可用：%r' % e)

out.append('')
out.append('④ harness 目录产物清单：')
for nm in sorted(os.listdir(H.HERE)):
    fp = os.path.join(H.HERE, nm)
    if os.path.isdir(fp):
        n = sum(len(f) for _r, _d, f in os.walk(fp))
        out.append('   %-28s <DIR>  文件数 %d' % (nm, n))
    else:
        out.append('   %-28s %8d B' % (nm, os.path.getsize(fp)))

with open(os.path.join(H.HERE, '_audit.txt'), 'w', encoding='utf-8') as f:
    f.write('\n'.join(out) + '\n')
print('ok')
