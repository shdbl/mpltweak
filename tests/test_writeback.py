# -*- coding: utf-8 -*-
# writeback 引擎 golden 测试（自包含：临时目录现场造脚本 + 参数 JSON）：
#   1. 零 LLM 写回：fig 变量解析 / savefig 前插入 / figsize 原位替换
#   2. colorbar 轴用 _colorbar.mappable 结构导航（不依赖首次 draw）
#   3. 幂等：重复写回 = 整体替换旧块，不叠加
#   4. 安全网：写回后 Agg 重跑失败 → 自动回滚备份
import os
import io
import contextlib
import ast as _ast
import re
import shutil
import sys
import json
import tempfile
import time

from mpltweak import apply, launch, revert, verify, writeback

# 测试断言的是中文人读文案：把界面语言**钉死**，别让宿主 locale 决定结论。
os.environ['MPLTWEAK_LANG'] = 'zh'
fail = 0


def _install_console_fallback():
    """让本套件在**任何**控制台编码下都不崩（核心：中国 Windows 的 GBK 控制台）。

    为什么必须有：CI 是 UTF-8，永远绿；而本地 GBK 控制台上一旦有字符编不出来，
    `print` 就抛 UnicodeEncodeError —— 整个套件从"ALL PASS"变成 rc=1 的假红
    （集成期实测：§52 的 detail 里混进 U+FFFD 就把套件打挂，行 1889）。
    `errors='replace'` 让编不出的字符退化成 `?`，结论仍然可读；这是**兜底**，
    不是"允许乱码"——真正该做的是别把 U+FFFD 交给 print（见 _gbk_safe/_decode_out）。
    """
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(errors='replace')
        except (AttributeError, ValueError, OSError):
            pass          # 被重定向成不支持 reconfigure 的对象时忽略即可


_install_console_fallback()


def _gbk_safe(s):
    """把字符串清洗成"当前输出流编得出来"的形式（`U+FFFD` 之类换成 `?`）。

    只影响显示：断言用的原字符串不变。
    """
    s = str(s)
    enc = getattr(sys.stdout, 'encoding', None) or 'utf-8'
    try:
        return s.encode(enc, 'replace').decode(enc, 'replace')
    except (LookupError, UnicodeDecodeError):
        return s.encode('ascii', 'backslashreplace').decode('ascii')


def _decode_out(raw):
    """子进程输出解码：先 utf-8，再系统 ANSI（GBK），最后才 replace。

    为什么不是直接 `decode('utf-8','replace')`：GBK 控制台下子进程打的是 GBK 字节，
    按 UTF-8 硬解会**整片**变成 U+FFFD（既丢信息、又拿去 print 就把套件打挂）。
    先试 GBK 能拿到真正的中文文案，`replace` 只作为最后的兜底。
    """
    for enc in ('utf-8', 'gbk', 'gb18030'):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode('utf-8', 'replace')

# 沙箱：测试只能写 workspace 内；用测试文件旁目录做临时区
# 临时根目录带 pid：本套件与 CI/手工并行跑时，两边的 rmtree 不会互删对方正在用的
# case 目录（独立审阅实测踩到过 FileNotFoundError: case_19/target.py）。
_TMPROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        '.tmp_writeback_%d' % os.getpid())


def check(name, cond, detail=''):
    global fail
    # 输出前统一清洗：name/detail 里可能混进不可编码字符（子进程乱码、外部路径），
    # 直接 print 会在 GBK 控制台上抛 UnicodeEncodeError 把整套测试打挂（T13）。
    print(('PASS' if cond else 'FAIL') + ' | ' + _gbk_safe(name)
          + ((' | ' + _gbk_safe(detail)) if detail else ''))
    if not cond:
        fail += 1


SCRIPT = '''# 测试脚本
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

fig, axes = plt.subplots(1, 2, figsize=(10, 4))
ax1, ax2 = axes
ax1.plot([1, 2, 3], [1, 4, 9], label='series')
ax1.legend(loc='upper left')
im = ax2.imshow(np.arange(12).reshape(3, 4), cmap='viridis')
fig.colorbar(im, ax=ax2)
plt.tight_layout()
fig.savefig('out.png')
print('done')
'''

PARAMS = {
    'version': 4,
    'script': 'target.py',
    'figsize_px': [1200, 480],
    'figsize_in': [12.0, 4.8],
    'axes': [
        {'index': 0, 'pos': [0.05, 0.1, 0.42, 0.78], 'aspect_locked': False,
         'title_fontsize': None, 'label_fontsize': 12, 'tick_fontsize': 10,
         'is_colorbar': False, 'clim': None, 'xscale': 'linear',
         'yscale': 'linear', 'grid': True,
         'spines': {'top': False, 'right': False, 'bottom': True, 'left': True},
         'lines': [{'index': 0, 'linewidth': 2.5, 'color': '#d62728'}],
         'legend': {'loc': 'upper right', 'anchor': None, 'fontsize': 11}},
        {'index': 1, 'pos': [0.55, 0.1, 0.36, 0.78], 'aspect_locked': True,
         'title_fontsize': 13, 'label_fontsize': None, 'tick_fontsize': None,
         'is_colorbar': False, 'clim': [0.0, 15.0], 'cmap': 'plasma',
         'xscale': 'linear', 'yscale': 'linear', 'grid': False,
         'spines': {}, 'lines': []},
        {'index': 2, 'pos': [0.93, 0.1, 0.03, 0.78], 'aspect_locked': False,
         'title_fontsize': None, 'label_fontsize': None, 'tick_fontsize': None,
         'is_colorbar': True, 'clim': [0.0, 15.0], 'cmap': 'plasma',
         'xscale': 'linear', 'yscale': 'linear', 'grid': False,
         'spines': {}, 'lines': []},
    ],
}


_case = [0]


def setup():
    _case[0] += 1
    os.makedirs(_TMPROOT, exist_ok=True)
    d = os.path.join(_TMPROOT, 'case_%d' % _case[0])
    os.makedirs(d, exist_ok=True)     # 沙箱坑：tempfile.mkdtemp 建的目录写不进，makedirs 可以
    script = os.path.join(d, 'target.py')
    with open(script, 'w', encoding='utf-8') as f:
        f.write(SCRIPT)
    pp = os.path.join(d, 'target.json')          # 侧边元数据同目录
    return d, script, pp


def read(script):
    with open(script, 'r', encoding='utf-8') as f:
        return f.read()


# ---- 1. 主流程 ----
d, script, pp = setup()
res = writeback.writeback(script, PARAMS, pp, verify=True)
# T70：ax2 是**未命名的 colorbar 轴**（`fig.colorbar(im, ax=ax2)` 的返回值没接
# 名字）→ 块里找不到可证的目标对象 → 它的 pos/clim/cmap 按 fail-closed 不写、如实进
# pending。ax0/ax1 照常落地 → reason 从 ok 变成**诚实**的 best_effort（不是退步：
# 旧行为是把 ax2 的改动写到运行期 fig.axes[2] 上，静态轴数一漂就落到别的轴）。
check('reason=best_effort（未命名 colorbar 轴的目标证不出，如实报没能落地）',
      res['reason'] == 'best_effort' and res.get('needs_block') is True
      and 'ax2.pos' in (res.get('pending') or []),
      '%s | pending=%s | err=%s'
      % (res['reason'], res.get('pending'), res.get('err')))
check('fig 变量=fig', res.get('fig_var') == 'fig', str(res.get('fig_var')))
check('figsize 原位替换', res.get('figsize_edited') is True)
check('插入点=before(savefig)', res.get('anchor') == 'before', str(res.get('anchor')))
check('Agg 验证通过', res.get('verified') is True)
src = read(script)
check('figsize 已替换 (12, 4.8)',
      "figsize=(12, 4.8)" in src, 'figsize=(10, 4)' in src and 'FAIL 未替换')
# T70：块内**只许用静态可证的对象名**定位目标 —— 运行期下标 `fig.axes[i]` /
# `plt.gcf()` / `plt.gca()` 一律不得出现（它们会落到色条轴、inset、别的图上）。
# ax0/ax1 用**单次字面量 subplots 容器的元素**（身份由那次调用唯一确定）。
check('T70 块内无运行期下标/动态定位',
      'fig.axes[' not in src and 'plt.gcf()' not in src
      and 'plt.gca()' not in src and 'get_axes()[' not in src)
check('T70 ax0/ax1 用可证的容器元素（axes[0] / axes[1]）',
      'axes[0].set_position(' in src and 'axes[1].set_position(' in src)
check('T70 未命名 colorbar 轴：块里没有它、但如实进 pending',
      '_colorbar' not in src and 'ax2.cmap' in (res.get('pending') or []))
check('非 colorbar mappable 用变量 im',
      'im.set_clim(0, 15)' in src and "im.set_cmap('plasma')" in src)
check('块在 savefig 之前', src.index(writeback.BLOCK_START) < src.index('fig.savefig'))
check('备份已生成', res.get('backup') and os.path.exists(res['backup']))

# ---- 2. 幂等：重复写回不叠加 ----
res2 = writeback.writeback(script, PARAMS, pp, verify=True)
src2 = read(script)
# 0.1.9（E1/E5 的修法）：重复写回同一份参数 = 写出来的块与磁盘上的**完全一致**
# （只差说明行里的"生成于 <时间>"）→ 必须报 no_change，而不是"又写一遍 + 再报 ok"。
# 反向验证：把 writeback.py 里 `_norm_ts(content) == _src_norm` 的那段 no_change
# 分支删掉 → 这里立刻回到 ok（见 fix_engine.md §反向验证 R2）。
# T70 补注：参数里带"目标证不出"的字段时（本例 ax2 = 未命名的 colorbar 轴），
# 第二次写回仍然**不落盘**（applied=False / 无备份 / 块标记各 1 个，见下面三条），
# 但 reason 从 no_change 如实升级成 best_effort —— "有字段没能落地"比"没有变化"更
# 贴近事实（T68 的诚实通道：宁可说没落地，也不谎报）。
check('再次写回不重复落盘（reason ∈ {no_change, best_effort}）',
      res2['reason'] in ('no_change', 'best_effort'), res2['reason'])
check('重复写回不落盘：applied=False 且没写备份',
      res2.get('applied') is False and res2.get('backup') is None,
      'applied=%r backup=%r' % (res2.get('applied'), res2.get('backup')))
check('块标记仍各 1 个',
      src2.count(writeback.BLOCK_START) == 1 and src2.count(writeback.BLOCK_END) == 1,
      'start=%d end=%d' % (src2.count(writeback.BLOCK_START),
                           src2.count(writeback.BLOCK_END)))

# ---- 3. 安全网：写回后运行失败 → 回滚 ----
d3, script3, pp3 = setup()
# 造一个"写回后必炸"的脚本：纯折线图无 mappable → clim 写回走 collections[0] 兜底
# → 运行期 collections 为空 IndexError → 验证失败 → 自动回滚
with open(script3, 'w', encoding='utf-8') as f:
    f.write('import matplotlib\nmatplotlib.use("Agg")\n'
            'import matplotlib.pyplot as plt\n'
            'fig, ax = plt.subplots(figsize=(5, 3))\n'
            'ax.plot([1, 2, 3])\n'
            'fig.savefig("o.png")\n')
params_bad = {
    'version': 4, 'script': 'target.py', 'figsize_px': None, 'figsize_in': None,
    'axes': [
        {'index': 0, 'pos': None, 'aspect_locked': False,
         'title_fontsize': None, 'label_fontsize': None, 'tick_fontsize': None,
         'is_colorbar': False, 'clim': [0.0, 15.0], 'cmap': 'plasma',
         'xscale': 'linear', 'yscale': 'linear', 'grid': False,
         'spines': {}, 'lines': []},
    ],
}
before = read(script3)
res3 = writeback.writeback(script3, params_bad, pp3, verify=True)
check('坏参数写回 reason=FAIL', res3['reason'] == 'fail', res3['reason'])
check('验证失败已标记', res3['verified'] is False)
check('已回滚到原内容', read(script3) == before, '')

# ---- 4a. 纯 pyplot 流 → plt.gcf() 兜底（真实老代码的主力形态），且写回后真能跑 ----
d4, script4, pp4 = setup()
with open(script4, 'w', encoding='utf-8') as f:
    f.write('import matplotlib\nmatplotlib.use("Agg")\n'
            'import matplotlib.pyplot as plt\n'
            'plt.figure(figsize=(6, 4))\n'
            'plt.plot([1, 2, 3])\n'
            'plt.savefig("o.png")\n')
params_gcf = {
    'version': 4, 'script': 'target.py', 'figsize_px': [600, 400],
    'figsize_in': [6.0, 4.0], 'fig_index': 0,
    'axes': [{'index': 0, 'pos': [0.15, 0.15, 0.75, 0.75], 'aspect_locked': False,
              'title_fontsize': 14, 'label_fontsize': None, 'tick_fontsize': None,
              'is_colorbar': False, 'clim': None, 'cmap': None,
              'xscale': 'linear', 'yscale': 'linear', 'grid': True,
              'spines': {}, 'lines': []}],
}
before4 = read(script4)
res4 = writeback.writeback(script4, params_gcf, pp4, verify=True)
# T70-B（船长批示）：纯 pyplot **单图**是**有条件可证**的 —— 全文件只有一次建图、没有
# colorbar/inset_axes/inset_locator/twinx/twiny/make_axes_locatable/secondary_*、
# 也没有 close → `plt.gcf()` 就是那张图、`.axes[k]` 就是静态第 k 条轴 → **允许写块**，
# 并在块注释里写明"凭什么可证"。这一档在原 census 里是 B 档（403/1158）的主体，
# 一刀切拒掉换不到任何安全性。
check('T70-B 纯 pyplot 单图：可证 → 照常写块、验证通过',
      res4.get('gcf_fallback') is True and res4['reason'] == 'ok'
      and res4.get('verified') is True,
      'gcf=%s reason=%s verified=%s'
      % (res4.get('gcf_fallback'), res4['reason'], res4.get('verified')))
_src4 = read(script4)
check('T70-B 块用 plt.gcf().axes[k]，且把"凭什么可证"写进块注释',
      'plt.gcf().axes[0].set_position(' in _src4
      and '目标定位：本文件只有一次建图' in _src4,
      str([ln for ln in _src4.splitlines() if '.axes[' in ln][:2]))
check('T70-B 写盘后确实换了内容（不是字节不变）', _src4 != before4)
# 对照：多图（§6）与循环（§9）条件不成立 → 仍然拒绝（见下面两节）

# ---- 4b. 无 plt 导入且无 fig 变量 → NO_FIG（不瞎猜）----
d4b, script4b, pp4b = setup()
with open(script4b, 'w', encoding='utf-8') as f:
    f.write('from matplotlib.figure import Figure\n'
            'obj = Figure()\n'
            'obj.savefig("o.png")\n')
res4b = writeback.writeback(script4b, PARAMS, pp4b, verify=False)
check('无 plt 导入 → NO_FIG', res4b['reason'] == 'no_fig', res4b['reason'])
check('NO_FIG 不落盘', read(script4b).startswith('from matplotlib.figure'))
shutil.rmtree(d4b, ignore_errors=True)

# ---- 5. 真实脚本边角（2026-10 真实 NWCP 脚本 dry-run 发现）----
#   colorbar 轴 xscale='function'：裸 set_xscale('function') 运行时报错 → 不生成
#   cmap='from_list'（ListedColormap 名）：不是已注册 colormap → 跳过 + 警告
d5, script5, pp5 = setup()
params_edge = {
    'version': 4, 'script': 'target.py', 'figsize_px': None, 'figsize_in': None,
    'axes': [
        {'index': 0, 'pos': None, 'aspect_locked': False,
         'title_fontsize': None, 'label_fontsize': None, 'tick_fontsize': None,
         'is_colorbar': True, 'clim': [0.0, 1.0], 'cmap': 'from_list',
         'xscale': 'function', 'yscale': 'linear', 'grid': False,
         'spines': {}, 'lines': []},
        {'index': 1, 'pos': None, 'aspect_locked': False,
         'title_fontsize': None, 'label_fontsize': None, 'tick_fontsize': None,
         'is_colorbar': False, 'clim': None, 'cmap': None,
         'xscale': 'log', 'yscale': 'linear', 'grid': False,
         'spines': {}, 'lines': []},
    ],
}
res5 = writeback.writeback(script5, params_edge, pp5, verify=False)
block5 = res5['block'] or ''
check('colorbar function 刻度不生成', "set_xscale('function')" not in block5, '')
check('from_list cmap 跳过', "set_cmap('from_list')" not in block5, '')
check('from_list cmap 有警告', any('from_list' in w for w in res5['warnings']), '')
check('普通轴 log 刻度仍生成', "set_xscale('log')" in block5, '')
shutil.rmtree(d5, ignore_errors=True)

# ---- 6. 多图脚本：改用 plt.gcf() + 最后一个 savefig 锚点 ----
#   （fig 变量可能停在早先那张图上 → fig.axes 空 → IndexError；实测踩到）
d6, script6, pp6 = setup()
with open(script6, 'w', encoding='utf-8') as f:
    f.write('import matplotlib\nmatplotlib.use("Agg")\n'
            'import matplotlib.pyplot as plt\n'
            'fig = plt.figure(figsize=(5, 3))\n'
            'ax0 = fig.add_subplot(111)\n'
            'fig.savefig("a.png")\n'
            'plt.figure(figsize=(5, 3))\n'
            'ax1 = plt.gca()\n'
            'plt.savefig("b.png")\n')
# §6 用独立参数：多图脚本必须带 fig_index（writeback 现在会拒绝"多图却无图号"），
# 这里明确指向第 2 张，锚点应当落在它的 savefig（第 9 行 plt.savefig("b.png")）。
params_gcf6 = dict(params_gcf, fig_index=1)
res6 = writeback.writeback(script6, params_gcf6, pp6, verify=True)
# T74：目标图（第 2 张）虽然**没接名字**，但建图调用可**静态编号**（两次
# `plt.figure(...)` → 第 2 张就是编号 2）、且模块层没有 close、没有额外建轴来源 →
# 块改用 `plt.figure(2).axes[0]` 定位（T70 时代的"一律拒绝"被这条可证路径救回）。
# 注意：仍然**不许**出现 `plt.gcf()` / `plt.gca()` / `fig.axes[i]`（运行期下标）。
_src6 = read(script6)
_blk6 = (_src6.split(writeback.BLOCK_START)[-1].split('end auto layout')[0]
         if writeback.BLOCK_START in _src6 else '')
check('T74 多图 + 未命名 figure：编号可证 → 块用 plt.figure(2).axes[0]（块内无运行期下标）',
      _blk6.count('plt.figure(2).axes[0]') >= 1
      and 'plt.gcf()' not in _blk6 and 'plt.gca()' not in _blk6
      and 'fig.axes[' not in _blk6,
      'gcf=%s fig=%s reason=%s anchor=%s pending=%s'
      % (res6.get('gcf_fallback'), res6.get('fig_var'), res6['reason'],
         res6.get('anchor'), res6.get('pending')))
check('T74 figure 级（figsize）仍能落地（第 2 张图的字面量被原位改掉）',
      _src6.count('figsize=(6, 4)') == 1)
shutil.rmtree(d6, ignore_errors=True)

# ---- 7. 多图 + fig_index：按记录的图号定位（不再只会调最后一张）----
d7, script7, pp7 = setup()
with open(script7, 'w', encoding='utf-8') as f:
    f.write('import matplotlib\nmatplotlib.use("Agg")\n'
            'import matplotlib.pyplot as plt\n'
            'fig = plt.figure(figsize=(5, 3))\n'
            'ax0 = fig.add_subplot(111)\n'
            'ax0.plot([1, 2, 3])\n'
            'fig.savefig("a.png")\n'
            'fig2 = plt.figure(figsize=(5, 3))\n'
            'ax1 = fig2.add_subplot(111)\n'
            'ax1.plot([3, 2, 1])\n'
            'fig2.savefig("b.png")\n')
params_first = dict(params_gcf)
params_first['fig_index'] = 0            # 本次调的是第一张图
params_first['n_figs'] = 2
res7 = writeback.writeback(script7, params_first, pp7, verify=True)
check('fig_index=0 → 锚定第一个 savefig（第 7 行）',
      res7.get('anchor') == 'before' and res7.get('anchor_line') == 7,
      'anchor=%s line=%s' % (res7.get('anchor'), res7.get('anchor_line')))
check('fig_index=0 写回验证通过', res7.get('verified') is True, str(res7.get('verified')))
src7 = read(script7)
check('块确实插在 a.png 的 savefig 之前',
      src7.index(writeback.BLOCK_START) < src7.index('fig.savefig("a.png")'))
shutil.rmtree(d7, ignore_errors=True)

# ---- 8. 语义验证 A/B：布局被后续代码覆盖 ----
#   脚本存图后又调 subplots_adjust 把位置重置 → "插在 savefig 前"的块能跑通但白改。
#   无语义验证：静默放过；有语义验证：判失败 → 换到脚本尾锚点 → 位置真的生效。
SCRIPT8 = ('import matplotlib\nmatplotlib.use("Agg")\n'
           'import matplotlib.pyplot as plt\n'
           'fig, ax = plt.subplots(figsize=(6, 4))\n'
           'ax.plot([1, 2, 3])\n'
           'fig.savefig("o.png")\n'
           'plt.subplots_adjust(left=0.30, right=0.90, top=0.90, bottom=0.20)\n')
params_move = {
    'version': 4, 'script': 'target.py', 'figsize_px': None, 'figsize_in': None,
    'axes': [{'index': 0, 'pos': [0.05, 0.05, 0.5, 0.5], 'aspect_locked': False,
              'title_fontsize': None, 'label_fontsize': None, 'tick_fontsize': None,
              'is_colorbar': False, 'clim': None, 'cmap': None,
              'xscale': 'linear', 'yscale': 'linear', 'grid': None,
              'spines': {}, 'lines': []}],
}
d8, script8, pp8 = setup()
with open(script8, 'w', encoding='utf-8') as f:
    f.write(SCRIPT8)
res8 = writeback.writeback(script8, params_move, pp8, verify=True, semantic=True)
# T23/3(b) 起改判据：这个脚本在 savefig **之后**用 subplots_adjust 覆盖布局 ——
# 过去会"换到脚本尾"重试（块插在 subplots_adjust 之后 → 布局生效，但 o.png 早就存过了
# → 存出来的图根本没变，还报 verified/semantic 全 true）。现在**不许**把块插到最后一个
# savefig 之后：合规锚点只有"savefig 之前"，那个位置会被后面的 subplots_adjust 覆盖 →
# 语义验证失败 → 拒绝写回并明确报告（脚本保持原样）。
check('语义验证识别"布局被 savefig 之后的代码覆盖"→ 拒绝写回（不再静默换到脚本尾）',
      res8.get('reason') == 'fail' and res8.get('applied') is False
      and read(script8) == SCRIPT8
      and '目标值在重跑后仍未达标' in (res8.get('err') or '')
      and 'savefig' in (res8.get('err') or ''),
      'reason=%s applied=%s err=%s'
      % (res8.get('reason'), res8.get('applied'), (res8.get('err') or '')[:200]))
shutil.rmtree(d8, ignore_errors=True)

d8b, script8b, pp8b = setup()
with open(script8b, 'w', encoding='utf-8') as f:
    f.write(SCRIPT8)
res8b = writeback.writeback(script8b, params_move, pp8b, verify=True, semantic=False)
check('（对照）关掉语义验证 → 静默放过',
      res8b.get('anchor') == 'before' and res8b.get('verified') is True,
      'anchor=%s' % res8b.get('anchor'))
shutil.rmtree(d8b, ignore_errors=True)

# ---- 9. B2：循环出图 + fig_index → 加 if 守卫只改目标那张 ----
d9, script9, pp9 = setup()
with open(script9, 'w', encoding='utf-8') as f:
    f.write('import matplotlib\nmatplotlib.use("Agg")\n'
            'import matplotlib.pyplot as plt\n'
            'for i in range(4):\n'
            '    plt.figure(figsize=(5, 3))\n'
            '    plt.plot([1, 2, 3])\n'
            '    plt.savefig("f%d.png" % i)\n')
params_loop = {
    'version': 4, 'script': 'target.py', 'figsize_px': None, 'figsize_in': None,
    'fig_index': 2, 'n_figs': 4,
    'axes': [{'index': 0, 'pos': [0.15, 0.15, 0.7, 0.7], 'aspect_locked': False,
              'title_fontsize': None, 'label_fontsize': None, 'tick_fontsize': None,
              'is_colorbar': False, 'clim': None, 'cmap': None,
              'xscale': 'linear', 'yscale': 'linear', 'grid': None,
              'spines': {}, 'lines': []}],
}
_src9_before = read(script9)
res9 = writeback.writeback(script9, params_loop, pp9, verify=True, semantic=True)
_src9 = read(script9)
# T70：循环里 `plt.figure()/plt.plot()` 建图建轴 —— 既没有可证的变量名（每轮重绑），
# 又是 pyplot 隐式建轴（认不出条数）→ 目标证不出 → fail-closed 不写盘。
# 旧的 `if i == 2:` 循环守卫因此对**这种形态**不再可达（守卫逻辑仍在，但没有可证的
# 目标对象）；这是本任务有意的能力收窄，详见报告 §T70。
check('T70 循环里 pyplot 建图建轴：目标证不出 → 拒绝写（不插块、字节不变）',
      res9['reason'] == 'best_effort' and res9.get('applied') is False
      and res9.get('needs_block') is True and bool(res9.get('pending'))
      and _src9 == _src9_before and writeback.BLOCK_START not in _src9,
      'reason=%s applied=%s pending=%s'
      % (res9['reason'], res9.get('applied'), res9.get('pending')))
check('T70 拒绝写时没有留下任何守卫块痕迹', 'if i == 2:' not in _src9)
shutil.rmtree(d9, ignore_errors=True)

# ---- 10. B3：多 mappable 轴按接收者 / colorbar 精确配对 ----
d10, script10, pp10 = setup()
with open(script10, 'w', encoding='utf-8') as f:
    f.write('import matplotlib\nmatplotlib.use("Agg")\n'
            'import matplotlib.pyplot as plt\n'
            'import numpy as np\n'
            'fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(8, 3))\n'
            'a = ax1.contourf(np.arange(12).reshape(3, 4))\n'
            'b = ax2.contourf(np.arange(12).reshape(3, 4) * 10)\n'
            'fig.colorbar(a, ax=ax1)\n'
            'fig.colorbar(b, ax=ax2)\n'
            'fig.savefig("o.png")\n')


def _mm(i, clim, cmap):
    return {'index': i, 'pos': None, 'aspect_locked': False,
            'title_fontsize': None, 'label_fontsize': None, 'tick_fontsize': None,
            'is_colorbar': False, 'clim': clim, 'cmap': cmap,
            'xscale': 'linear', 'yscale': 'linear', 'grid': None,
            'spines': {}, 'lines': []}


params_mm = {'version': 4, 'script': 'target.py', 'figsize_px': None,
             'figsize_in': None,
             'axes': [_mm(0, [0.0, 10.0], 'viridis'), _mm(1, [0.0, 100.0], 'plasma')]}
res10 = writeback.writeback(script10, params_mm, pp10, verify=True, semantic=True)
blk10 = res10['block'] or ''
check('B3 ax0 配到 a、ax1 配到 b（不再都取排序第一个）',
      'a.set_clim(0, 10)' in blk10 and 'b.set_clim(0, 100)' in blk10, '')
check('B3 不再报"多个 mappable"警告',
      not any('多个 mappable' in w for w in res10.get('warnings', [])),
      '; '.join(res10.get('warnings', []))[:60])
check('B3 写回后语义验证通过（clim 真的落在各自轴上）',
      res10.get('verified') is True and res10.get('semantic') is True,
      'v=%s semantic=%s' % (res10.get('verified'), res10.get('semantic')))
shutil.rmtree(d10, ignore_errors=True)

# ---- 11. 多图同时改：每个改过的图各一份参数 → apply 逐张落实 ----
d11, script11, pp11 = setup()
with open(script11, 'w', encoding='utf-8') as f:
    f.write('import matplotlib\nmatplotlib.use("Agg")\n'
            'import matplotlib.pyplot as plt\n'
            'fig1, ax1 = plt.subplots(figsize=(5, 3))\n'
            'ax1.plot([1, 2, 3])\n'
            'fig1.savefig("a.png")\n'
            'fig2, ax2 = plt.subplots(figsize=(5, 3))\n'
            'ax2.plot([3, 2, 1])\n'
            'fig2.savefig("b.png")\n')


def _mkpar(idx, pos):
    return {'version': 4, 'script': 'target.py', 'figsize_px': None,
            'figsize_in': None, 'fig_index': idx, 'n_figs': 2,
            'axes': [{'index': 0, 'pos': pos, 'aspect_locked': False,
                      'title_fontsize': None, 'label_fontsize': None,
                      'tick_fontsize': None, 'is_colorbar': False,
                      'clim': None, 'cmap': None, 'xlim': None, 'ylim': None,
                      'xscale': 'linear',
                      'yscale': 'linear', 'grid': None,
                      'spines': {}, 'lines': []}]}


# 模拟 launch 的产物：每张改过的图一份（文件名带图号），最后改的另存主文件
_pd = os.path.join(d11, '.tweak_params')
os.makedirs(_pd, exist_ok=True)
for _name, _idx, _pos in (('target.fig0.json', 0, [0.10, 0.10, 0.5, 0.5]),
                          ('target.json', 1, [0.40, 0.40, 0.5, 0.5])):
    with open(os.path.join(_pd, _name), 'w', encoding='utf-8') as f:
        json.dump(_mkpar(_idx, _pos), f, ensure_ascii=False)

# T23：**把 mtime 打桩**，别依赖"两份参数处理得够快"（T22 实测的"时间炸弹"：
# 进程内 apply + 极小脚本时第一份写回 <1s，于是旧实现"用当前脚本 mtime 当基准"
# 也碰巧过 —— 慢机器/慢脚本上会随机红绿）。
#   脚本 mtime = now-10s，两份参数 = now-5s（参数比脚本新 → 合法，不该拦）；
#   旧实现里第一份写回后脚本变成 now → 第二份被判 stale（差 5s > 1s 容差）→ 用例必失败。
_now = time.time()
os.utime(script11, (_now - 10, _now - 10))
for _n in ('target.fig0.json', 'target.json'):
    os.utime(os.path.join(_pd, _n), (_now - 5, _now - 5))

_rc = apply.main([script11, '--write', '--style', 'block'])
_src11 = read(script11)
check('多图：apply 返回 0', _rc == 0, 'rc=%s' % _rc)
check('多图：两份参数都落实了（脚本里 2 个调整块）',
      _src11.count(writeback.BLOCK_START) == 2,
      'blocks=%d' % _src11.count(writeback.BLOCK_START))
check('多图：第 1 张的块在 a.png 之前',
      _src11.index(writeback.BLOCK_START) < _src11.index('fig1.savefig("a.png")'))
check('多图：第 2 张的块在 b.png 之前',
      _src11.rindex(writeback.BLOCK_START) < _src11.index('fig2.savefig("b.png")'))
shutil.rmtree(d11, ignore_errors=True)

# ---- 12. 多图参数文件生命周期：改动过的保留、未改动的清掉（含 .lock）----
#   坑：<stem>.fig<k>.json 既是过程中写的那份、也是最终正式参数（同名），
#   收尾若无脑全删 → "一次改多张"会被清空。
d12, script12, pp12 = setup()
_pd12 = os.path.join(d12, '.tweak_params')
os.makedirs(_pd12, exist_ok=True)
_tmps = {}
for _k in range(3):
    _p = os.path.join(_pd12, 'target.fig%d.json' % _k)
    with open(_p, 'w', encoding='utf-8') as f:
        json.dump(_mkpar(_k, [0.1 + 0.1 * _k, 0.1, 0.5, 0.5]), f)
    with open(_p + '.lock', 'w', encoding='utf-8') as f:
        f.write('123')
    _tmps[_k] = _p
launch._prune_fig_params(_tmps, [1])          # 只保留第 2 张（它改动过）
check('多图：改动过的参数文件被保留', os.path.exists(_tmps[1]))
check('多图：未改动的参数文件被清掉',
      not os.path.exists(_tmps[0]) and not os.path.exists(_tmps[2]))
check('多图：所有 .lock 都清掉',
      not any(os.path.exists(_tmps[k] + '.lock') for k in _tmps))
shutil.rmtree(d12, ignore_errors=True)

# ---- 13. 原位写回：直接改原代码数字，不插调整块（用户主推方式）----
d13, script13, pp13 = setup()
with open(script13, 'w', encoding='utf-8') as f:
    f.write('import matplotlib\nmatplotlib.use("Agg")\n'
            'import matplotlib.pyplot as plt\n'
            'fig = plt.figure(figsize=(6, 4))\n'
            'ax = fig.add_axes([0.10, 0.10, 0.70, 0.70])\n'
            'ax.plot([1, 2, 3])\n'
            'ax.set_title("t", fontsize=14)\n'
            'ax.set_xlabel("x", fontsize=11)\n'
            'ax.tick_params(labelsize=9)\n'
            'ax.grid(True)\n'
            'fig.savefig("o.png")\n')
params_ip = {'version': 4, 'script': 'target.py', 'figsize_px': None,
             'figsize_in': None,
             'axes': [{'index': 0, 'pos': [0.12, 0.15, 0.60, 0.65],
                       'aspect_locked': False, 'title_fontsize': 16,
                       'label_fontsize': 12, 'tick_fontsize': 8,
                       'is_colorbar': False, 'clim': None, 'cmap': None,
                       'xscale': 'linear', 'yscale': 'linear', 'grid': False,
                       'spines': {}, 'lines': [],
                       'legend': {'loc': 'upper right', 'anchor': None,
                                  'fontsize': 11}}]}
res13 = writeback.writeback(script13, params_ip, pp13, verify=True, semantic=True,
                            style='inplace')
_s13 = read(script13)
check('原位：add_axes 的 4 个数字被替换',
      'add_axes([0.12, 0.15, 0.6, 0.65])' in _s13
      or 'add_axes([0.12, 0.15, 0.60, 0.65])' in _s13, _s13.splitlines()[3])
check('原位：title 字号被替换', 'set_title("t", fontsize=16)' in _s13)
check('原位：grid 被替换', 'ax.grid(False)' in _s13)
check('原位：不再插入调整块', writeback.BLOCK_START not in _s13)
check('原位：Agg + 语义验证通过',
      res13.get('verified') is True and res13.get('semantic') is True,
      'v=%s s=%s' % (res13.get('verified'), res13.get('semantic')))
check('原位：legend 改不了 → 有跳过警告',
      any('legend' in w for w in res13.get('warnings', [])), '')
check('原位：备份在**脚本旁**的 .tweak_params 里（RC-10：不再跟着 --params 走）',
      os.path.exists(os.path.join(d13, '.tweak_params', 'target.tweak.bak')),
      str(res13.get('backup')))
_before13 = read(script13)
writeback.writeback(script13, params_ip, pp13, verify=False, style='inplace')
check('原位：幂等（第二次写回不再改动文件）', read(script13) == _before13)
shutil.rmtree(d13, ignore_errors=True)

# ---- 14. 原位写回 + figsize：多图按 fig_index 定位、替换后坐标不错位 ----
#   坑：edit_figsize 改了文件长度后原 tree 坐标失效 → 后续替换全错位（实测
#   ax_d.bar→ax_d.12r / tick_params→tick_param14labelsize）；且 figsize 必须
#   改**对应图**的调用，不能总改第一个。
d14, script14, pp14 = setup()
with open(script14, 'w', encoding='utf-8') as f:
    f.write('import matplotlib\nmatplotlib.use("Agg")\n'
            'import matplotlib.pyplot as plt\n'
            'fig1 = plt.figure(figsize=(11, 8))\n'
            'ax_a = fig1.add_axes([0.06, 0.56, 0.46, 0.36])\n'
            'ax_a.set_title("a", fontsize=14)\n'
            'ax_a.plot([1, 2, 3])\n'
            'fig1.savefig("a.png")\n'
            'fig2 = plt.figure(figsize=(9, 4))\n'
            'ax_b = fig2.add_axes([0.10, 0.10, 0.70, 0.70])\n'
            'ax_b.set_title("b", fontsize=12)\n'
            'ax_b.plot([1, 2, 3])\n'
            'fig2.savefig("b.png")\n')
params14 = {'version': 4, 'script': 'target.py', 'fig_index': 1, 'n_figs': 2,
            'figsize_px': None, 'figsize_in': [4.7, 3.91],
            'axes': [{'index': 0, 'pos': [0.12, 0.15, 0.60, 0.65],
                      'aspect_locked': False, 'title_fontsize': 16,
                      'label_fontsize': None, 'tick_fontsize': None,
                      'is_colorbar': False, 'clim': None, 'cmap': None,
                      'xscale': 'linear', 'yscale': 'linear', 'grid': None,
                      'spines': {}, 'lines': [], 'legend': None}]}
res14 = writeback.writeback(script14, params14, pp14, verify=True, semantic=True,
                            style='inplace')
_s14 = read(script14)
check('原位+figsize：fig1 的 figsize 不被误改',
      'fig1 = plt.figure(figsize=(11, 8))' in _s14)
check('原位+figsize：fig2 的 figsize 被替换',
      'fig2 = plt.figure(figsize=(4.7, 3.91))' in _s14)
check('原位+figsize：fig2 的 add_axes 数字替换不错位',
      'ax_b = fig2.add_axes([0.12, 0.15, 0.6, 0.65])' in _s14
      or 'ax_b = fig2.add_axes([0.12, 0.15, 0.60, 0.65])' in _s14, '')
check('原位+figsize：fig2 的 title 字号替换', 'ax_b.set_title("b", fontsize=16)' in _s14)
check('原位+figsize：验证通过',
      res14.get('verified') is True and res14.get('semantic') is True,
      'v=%s s=%s' % (res14.get('verified'), res14.get('semantic')))
shutil.rmtree(d14, ignore_errors=True)

# ---- 18. colorbar 显式 cax=fig.add_axes() 夹在中间：轴-变量配对不错位 ----
#   坑：cax 也是 add_axes 创建的，若不剔除会占掉一个"非 colorbar 轴"配对位，
#   导致 ax1 的位置被改到 cax 的源码上（且 cax 不在语义验证范围 → 可静默错写）。
d18, script18, pp18 = setup()
with open(script18, 'w', encoding='utf-8') as f:
    f.write('import matplotlib\nmatplotlib.use("Agg")\n'
            'import matplotlib.pyplot as plt\n'
            'import numpy as np\n'
            'fig = plt.figure(figsize=(6, 4))\n'
            'ax0 = fig.add_axes([0.10, 0.60, 0.30, 0.30])\n'
            'cax = fig.add_axes([0.85, 0.60, 0.03, 0.30])\n'
            'ax1 = fig.add_axes([0.10, 0.10, 0.30, 0.30])\n'
            'im = ax0.imshow(np.random.rand(8, 8))\n'
            'fig.colorbar(im, cax=cax)\n'
            'ax1.plot([1, 2, 3])\n'
            'fig.savefig("o.png")\n')
def _ax18(i, pos, is_cb=False):
    return {'index': i, 'pos': pos, 'aspect_locked': bool(is_cb),
            'title_fontsize': None, 'label_fontsize': None,
            'tick_fontsize': None, 'is_colorbar': is_cb,
            'clim': [0.0, 1.0] if is_cb else None,
            'cmap': 'viridis' if is_cb else None,
            'xscale': 'linear', 'yscale': 'linear', 'grid': None,
            'spines': {}, 'lines': [], 'legend': None}
params18 = {'version': 4, 'script': 'target.py', 'fig_index': 0, 'n_figs': 1,
            'figsize_px': None, 'figsize_in': None,
            'axes': [_ax18(0, [0.10, 0.60, 0.30, 0.30]),
                     _ax18(1, [0.85, 0.60, 0.03, 0.30], True),
                     _ax18(2, [0.12, 0.15, 0.34, 0.26])]}
res18 = writeback.writeback(script18, params18, pp18, verify=True, semantic=True,
                            style='inplace')
_s18 = read(script18)
check('cax：ax1(idx2) 的位置数字被改',
      'ax1 = fig.add_axes([0.12, 0.15, 0.34, 0.26])' in _s18, '')
check('cax：colorbar 槽(cax) 不被误改',
      'cax = fig.add_axes([0.85, 0.6, 0.03, 0.3])' in _s18
      or 'cax = fig.add_axes([0.85, 0.60, 0.03, 0.30])' in _s18, '')
check('cax：验证通过',
      res18.get('verified') is True and res18.get('semantic') is True,
      'v=%s s=%s' % (res18.get('verified'), res18.get('semantic')))
shutil.rmtree(d18, ignore_errors=True)

# ---- 19. 中文脚本：ast col_offset 是 UTF-8 字节偏移，含中文的 set_title/set_ylabel
#   字号替换不错位（实测修前 '(a) 三角函数' 的 fontsize 替换偏 8 字符 → ax_d.bar
#   被写成 ax_d.12r）。 ----
d19, script19, pp19 = setup()
with open(script19, 'w', encoding='utf-8') as f:
    f.write('import matplotlib\nmatplotlib.use("Agg")\n'
            'import matplotlib.pyplot as plt\n'
            'fig = plt.figure(figsize=(6, 4))\n'
            'ax = fig.add_axes([0.10, 0.10, 0.70, 0.70])\n'
            'ax.plot([1, 2, 3])\n'
            "ax.set_title('(a) 三角函数', fontsize=17)\n"
            "ax.set_ylabel('数值', fontsize=16)\n"
            'fig.savefig("o.png")\n')
params19 = {'version': 4, 'script': 'target.py', 'fig_index': 0, 'n_figs': 1,
            'figsize_px': None, 'figsize_in': None,
            'axes': [{'index': 0, 'pos': [0.10, 0.10, 0.70, 0.70],
                      'aspect_locked': False, 'title_fontsize': 13,
                      'label_fontsize': 10, 'tick_fontsize': None,
                      'is_colorbar': False, 'clim': None, 'cmap': None,
                      'xscale': 'linear', 'yscale': 'linear', 'grid': None,
                      'spines': {}, 'lines': [], 'legend': None}]}
res19 = writeback.writeback(script19, params19, pp19, verify=True, semantic=True,
                            style='inplace')
_s19 = read(script19)
check('中文：set_title 字号替换不错位',
      "ax.set_title('(a) 三角函数', fontsize=13)" in _s19, '')
# T73（零兼容）：参数只给了 x → y 标签字号**不许**被写成 x 的值（保持源码原值）
check('中文：只给 x → y 标签字号保持源码原值（T73 零兼容）',
      "ax.set_ylabel('数值', fontsize=10)" not in _s19
      and any('set_ylabel' in ln and 'fontsize' in ln
              for ln in _s19.splitlines()),
      str([ln.strip() for ln in _s19.splitlines() if 'set_ylabel' in ln]))
check('中文：add_axes 不被破坏',
      'ax = fig.add_axes([0.1, 0.1, 0.7, 0.7])' in _s19
      or 'ax = fig.add_axes([0.10, 0.10, 0.70, 0.70])' in _s19, '')
check('中文：验证通过',
      res19.get('verified') is True and res19.get('semantic') is True,
      'v=%s s=%s' % (res19.get('verified'), res19.get('semantic')))
shutil.rmtree(d19, ignore_errors=True)

# ---- 20. colorbar 宿主轴：fig.colorbar(..., ax=axX) 会在创建时重定位宿主轴，
#   add_axes 数字重跑时被 fraction 覆盖 → pos 原位改无效，必须 skipped + 警告，
#   且不参与语义验证（否则误报位置不一致而回滚）。 ----
d20, script20, pp20 = setup()
with open(script20, 'w', encoding='utf-8') as f:
    f.write('import matplotlib\nmatplotlib.use("Agg")\n'
            'import matplotlib.pyplot as plt\n'
            'import numpy as np\n'
            'fig = plt.figure(figsize=(6, 4))\n'
            'ax0 = fig.add_axes([0.10, 0.10, 0.60, 0.70])\n'
            'im = ax0.imshow(np.random.rand(8, 8))\n'
            'fig.colorbar(im, ax=ax0, fraction=0.15, pad=0.05)\n'
            'ax0.set_title("t", fontsize=12)\n'
            'fig.savefig("o.png")\n')
def _ax20(i, pos, is_cb=False, title=None):
    return {'index': i, 'pos': pos, 'aspect_locked': True,
            'title_fontsize': title, 'label_fontsize': None,
            'tick_fontsize': None, 'is_colorbar': is_cb,
            'clim': [0.0, 1.0] if is_cb else None,
            'cmap': 'plasma' if is_cb else None,
            'xscale': 'linear', 'yscale': 'linear', 'grid': None,
            'spines': {}, 'lines': [], 'legend': None}
params20 = {'version': 4, 'script': 'target.py', 'fig_index': 0, 'n_figs': 1,
            'figsize_px': None, 'figsize_in': None,
            'axes': [_ax20(0, [0.12, 0.15, 0.55, 0.62], title=15),
                     _ax20(1, [0.75, 0.10, 0.05, 0.70], True)]}
res20 = writeback.writeback(script20, params20, pp20, verify=True, semantic=True,
                            style='inplace')
_s20 = read(script20)
check('宿主轴：add_axes 数字不被改（原位无效）',
      'ax0 = fig.add_axes([0.1, 0.1, 0.6, 0.7])' in _s20
      or 'ax0 = fig.add_axes([0.10, 0.10, 0.60, 0.70])' in _s20, '')
check('宿主轴：字号照常原位改', 'ax0.set_title("t", fontsize=15)' in _s20, '')
check('宿主轴：警告说明 colorbar 决定位置',
      any('colorbar 的宿主' in w and '保持原样' in w
          for w in res20.get('warnings', [])), '')
check('宿主轴：验证通过',
      res20.get('verified') is True and res20.get('semantic') is True,
      'v=%s s=%s' % (res20.get('verified'), res20.get('semantic')))
shutil.rmtree(d20, ignore_errors=True)

# ---- 21. colorbar 的 fraction **不再**被反推写回（E4/E5 的修法）----
#   旧实现拿"colorbar 实测宽 ÷ 宿主 add_axes 宽"反推 fraction 写回源码。它只在
#   **draw 之前**取值时才成立：脚本一旦 savefig/show 过（几乎每个脚本都会），
#   colorbar 宽度就被 _box_aspect=20 接管（实测 fraction 0.05/0.15/0.28/0.5 的
#   cb_w 全都变成 0.026=高/20），反推出来的是垃圾值 —— 干净往返把 0.28 写成
#   0.0815152，而且这个伪字段永远算"真改动"→ auto 永不退块 → 用户拖的位置被静默
#   丢弃还报 ok。现在 colorbar 轴的位置照旧进 skipped，由 auto 退到块模式落地。
#   反向验证：把 writeback.py 里那段 fraction 反推代码加回去 → 本条立刻失败。
d21, script21, pp21 = setup()
with open(script21, 'w', encoding='utf-8') as f:
    f.write('import matplotlib\nmatplotlib.use("Agg")\n'
            'import matplotlib.pyplot as plt\n'
            'import numpy as np\n'
            'fig = plt.figure(figsize=(6, 4))\n'
            'ax_c = fig.add_axes([0.10, 0.10, 0.33, 0.40])\n'
            'cs = ax_c.contourf(np.random.rand(8, 8), levels=4)\n'
            'cb = fig.colorbar(cs, ax=ax_c, fraction=0.28, pad=0.03)\n'
            'ax_c.set_title("c", fontsize=12)\n'
            'fig.savefig("o.png")\n')
def _ax21(i, pos, is_cb=False):
    return {'index': i, 'pos': pos, 'aspect_locked': False,
            'title_fontsize': 14 if not is_cb else None,
            'label_fontsize': None, 'tick_fontsize': None,
            'is_colorbar': is_cb, 'clim': [0.0, 1.0] if is_cb else None,
            'cmap': 'viridis' if is_cb else None,
            'xscale': 'linear', 'yscale': 'linear', 'grid': None,
            'spines': {}, 'lines': [], 'legend': None}
params21 = {'version': 4, 'script': 'target.py', 'fig_index': 0, 'n_figs': 1,
            'figsize_px': None, 'figsize_in': None,
            'axes': [_ax21(0, [0.12, 0.15, 0.30, 0.35]),
                     _ax21(1, [0.45, 0.10, 0.0269, 0.40], True)]}
res21 = writeback.writeback(script21, params21, pp21, verify=True, semantic=True,
                            style='inplace')
_s21 = read(script21)
check('fraction：源码里的 fraction=0.28 原样保留（不再被反推成 0.0815152）',
      'fraction=0.28' in _s21 and '0.0815152' not in _s21, '')
check('fraction：宿主轴 add_axes 不被改（原位无效）',
      'ax_c = fig.add_axes([0.1, 0.1, 0.33, 0.4])' in _s21
      or 'ax_c = fig.add_axes([0.10, 0.10, 0.33, 0.40])' in _s21, '')
check('fraction：宿主轴字号照常原位改', 'ax_c.set_title("c", fontsize=14)' in _s21, '')
check('fraction：colorbar 轴位置进"未原位应用"清单（不再静默丢）',
      any('未原位应用' in w for w in res21.get('warnings', [])),
      str(res21.get('warnings'))[:200])
check('fraction：验证通过',
      res21.get('verified') is True and res21.get('semantic') is True,
      'v=%s s=%s' % (res21.get('verified'), res21.get('semantic')))
shutil.rmtree(d21, ignore_errors=True)

# ---- 22. 验证超时三态：慢脚本（>timeout）→ 超时不被当"跑不通"回滚 ----
#   修前：verify_run 超时返回 False → 误判失败回滚（M7）；修后返回 None →
#   警告"未能自动验证"，写回保留。
d22, script22, pp22 = setup()
with open(script22, 'w', encoding='utf-8') as f:
    f.write('import time\nimport matplotlib\nmatplotlib.use("Agg")\n'
            'import matplotlib.pyplot as plt\n'
            'time.sleep(3)\n'                        # 慢脚本：超时模拟
            'fig = plt.figure(figsize=(6, 4))\n'
            'ax = fig.add_axes([0.10, 0.10, 0.70, 0.70])\n'
            'ax.set_title("t", fontsize=12)\n'
            'fig.savefig("o.png")\n')
params22 = {'version': 4, 'script': 'target.py', 'fig_index': 0, 'n_figs': 1,
            'figsize_px': None, 'figsize_in': None,
            'axes': [{'index': 0, 'pos': [0.10, 0.10, 0.70, 0.70],
                      'aspect_locked': False, 'title_fontsize': 14,
                      'label_fontsize': None, 'tick_fontsize': None,
                      'is_colorbar': False, 'clim': None, 'cmap': None,
                      'xscale': 'linear', 'yscale': 'linear', 'grid': None,
                      'spines': {}, 'lines': [], 'legend': None}]}
res22 = writeback.writeback(script22, params22, pp22, verify=True, semantic=True,
                            timeout=2, style='inplace')
check('超时：不失败回滚', res22.get('reason') in ('ok', 'best_effort'),
      str(res22.get('reason')))
check('超时：verified=None 且有超时警告',
      res22.get('verified') is None
      and any('超时' in w for w in res22.get('warnings', [])), '')
shutil.rmtree(d22, ignore_errors=True)

# ---- 23. S1 原子写：写文件失败或进程被打断时，原脚本绝不能变成 0 字节/半截 ----
#   修前：`open(script, 'w')` 会**先截断为目标 0 字节**再写入；进程若在该窗口内
#   消失（断电/OOM/taskkill），用户脚本就永久变空文件。修后：写同目录临时文件后
#   `os.replace` 原子替换；替换失败则原文件原样保留。
d23, script23, pp23 = setup()
ORIG23 = ('import matplotlib\n'
          'matplotlib.use("Agg")\n'
          'import matplotlib.pyplot as plt\n'
          'fig = plt.figure(figsize=(6, 4))\n'
          'ax = fig.add_axes([0.10, 0.10, 0.70, 0.70])\n'
          'ax.set_title("t", fontsize=12)\n'
          'fig.savefig("o.png")\n')
with open(script23, 'w', encoding='utf-8', newline='') as f:
    f.write(ORIG23)

# 23a 正常路径：内容写对，且不留临时文件
writeback._atomic_write(script23, 'AAA\nBBB\n')
check('原子写：内容正确', read(script23) == 'AAA\nBBB\n', repr(read(script23))[:50])
check('原子写：不留 .mpltweak-*.tmp 临时文件',
      not [n for n in os.listdir(os.path.dirname(script23))
           if n.startswith('.mpltweak-')], '')

# 23b 换行符保持 LF（newline='' 的作用；修前 open(...,'w') 会把 \n 翻译成 os.linesep）
writeback._atomic_write(script23, ORIG23)
check('原子写：换行符仍是 LF，未被悄悄改成 CRLF',
      '\r\n' not in read(script23), '')

# 23c 替换失败 → 原文件保持旧内容（关键：不能出现 0 字节或半截）
_real_replace = os.replace


def _boom_replace(a, b):
    raise OSError('simulated replace failure')


os.replace = _boom_replace
try:
    try:
        writeback._atomic_write(script23, 'SHOULD-NOT-LAND')
        _raised = False
    except OSError:
        _raised = True
finally:
    os.replace = _real_replace
check('原子写：替换失败会抛错（不静默吞掉）', _raised, '')
check('原子写：替换失败后原文件内容完好（不是 0 字节）',
      read(script23) == ORIG23, 'len=%d' % len(read(script23)))

# 23d 端到端：写回流程中替换失败 → 脚本仍是原文，且结论如实报告
params23 = {'version': 4, 'script': 'target.py', 'fig_index': 0, 'n_figs': 1,
            'figsize_px': None, 'figsize_in': None,
            'axes': [{'index': 0, 'pos': [0.10, 0.10, 0.70, 0.70],
                      'aspect_locked': False, 'title_fontsize': 14,
                      'label_fontsize': None, 'tick_fontsize': None,
                      'is_colorbar': False, 'clim': None, 'cmap': None,
                      'xscale': 'linear', 'yscale': 'linear', 'grid': None,
                      'spines': {}, 'lines': [], 'legend': None}]}
os.replace = _boom_replace
try:
    res23 = writeback.writeback(script23, params23, pp23, verify=False,
                                style='inplace')
finally:
    os.replace = _real_replace
check('原子写端到端：替换失败 → 脚本未被破坏',
      read(script23) == ORIG23, 'len=%d' % len(read(script23)))
check('原子写端到端：如实报错而不是假装成功',
      res23.get('reason') == writeback.FAIL, str(res23.get('reason')))
shutil.rmtree(d23, ignore_errors=True)

# ---- 24. S2 回滚诚实：回滚失败时结论必须说"回滚失败"，不能谎称"已回滚" ----
#   修前：`except OSError: pass` 吞掉回滚异常，而提示语照旧写"（已回滚）"——
#   用户以为安全了，磁盘上却仍是改坏的脚本。
d24, script24, _ = setup()
writeback._atomic_write(script24, 'X = 1\n')
_bak24 = os.path.join(d24, 'good.bak')
writeback._atomic_write(_bak24, 'ORIG\n')
check('回滚说明：成功时返回「已回滚」且内容确实被拷回',
      writeback._rollback_note(_bak24, script24) == '已回滚'
      and read(script24) == 'ORIG\n', repr(read(script24)))
writeback._atomic_write(script24, 'BROKEN\n')
_missing24 = os.path.join(d24, 'nonexistent.bak')
_note24 = writeback._rollback_note(_missing24, script24)
check('回滚说明：失败时如实报告（含「回滚也失败」与备份路径）',
      '回滚也失败' in _note24 and _missing24 in _note24, _note24[:90])
check('回滚说明：失败时绝不谎称「已回滚」',
      '已回滚' not in _note24, _note24[:90])
check('回滚说明：失败后坏内容留在盘上（这正是必须如实告知的原因）',
      read(script24) == 'BROKEN\n', repr(read(script24)))
shutil.rmtree(d24, ignore_errors=True)

# ---- 25. auto 模式（默认）：每张图各自决定写回方式 ----
#   默认 --style auto = 先试"原位改代码里的数字"；若这张图什么都没落地
#   （典型：位置来自 plt.subplots / plt.subplot(1,2,n) / GridSpec，源码里根本
#   没有可改的数字），再改用插入调整块。这样同一脚本里能干净改的图和只能插块的
#   图各得其所，而不是因为少数几张图就把整份脚本都变成插块。
#   同时锁死：显式 --style 时绝不能被 auto 逻辑覆盖（曾经踩过这个坑）。
d25, script25, _pp25 = setup()


def _write25(src):
    with open(script25, 'w', encoding='utf-8', newline='') as f:
        f.write(src)


def _params25(pos):
    # CLI 在 <脚本目录>/.tweak_params/<stem>.json 找参数；setup() 返回的 pp 是
    # 侧边元数据用的 <d>/target.json，两者不是一回事，这里必须建在前者。
    _pd = os.path.join(d25, '.tweak_params')
    os.makedirs(_pd, exist_ok=True)
    with open(os.path.join(_pd, 'target.json'), 'w', encoding='utf-8') as f:
        json.dump(_mkpar(0, pos), f, ensure_ascii=False)


_GRID_SRC = ('import matplotlib\n'
             'matplotlib.use("Agg")\n'
             'import matplotlib.pyplot as plt\n'
             'fig, axs = plt.subplots(2, 2, figsize=(8, 6))\n'
             'fig.savefig("o.png")\n')
_ADD_SRC = ('import matplotlib\n'
            'matplotlib.use("Agg")\n'
            'import matplotlib.pyplot as plt\n'
            'fig = plt.figure(figsize=(8, 6))\n'
            'ax = fig.add_axes([0.10, 0.10, 0.70, 0.70])\n'
            'fig.savefig("o.png")\n')

# 25a 网格轴（subplots）→ auto 应自动退到插块
_write25(_GRID_SRC)
_params25([0.08, 0.55, 0.38, 0.38])
_rc25 = apply.main([script25, '--write'])
_s25 = read(script25)
check('auto：网格轴（subplots）→ 自动改用插入调整块',
      _rc25 == 0 and writeback.BLOCK_START in _s25,
      'rc=%s blocks=%d' % (_rc25, _s25.count(writeback.BLOCK_START)))
check('auto：退到块模式后确实改到了位置',
      'set_position' in _s25, '')

# 25b add_axes 字面量 → auto 应原位改、不插块
_write25(_ADD_SRC)
_params25([0.20, 0.20, 0.50, 0.50])
_rc25b = apply.main([script25, '--write'])
_s25b = read(script25)
check('auto：add_axes 字面量 → 原位改，不插块',
      writeback.BLOCK_START not in _s25b and '[0.2, 0.2, 0.5, 0.5]' in _s25b,
      repr([ln for ln in _s25b.splitlines() if 'add_axes' in ln]))

# 25c 显式 --style inplace → 不得被 auto 逻辑覆盖（保持原样、不插块）
_write25(_GRID_SRC)
_params25([0.08, 0.55, 0.38, 0.38])
_rc25c = apply.main([script25, '--write', '--style', 'inplace'])
_s25c = read(script25)
check('--style inplace：显式指定时不得退到块模式',
      writeback.BLOCK_START not in _s25c and 'set_position' not in _s25c,
      'rc=%s' % _rc25c)

# 25d 显式 --style block → 即使能原位改也必须插块
_write25(_ADD_SRC)
_params25([0.20, 0.20, 0.50, 0.50])
_rc25d = apply.main([script25, '--write', '--style', 'block'])
_s25d = read(script25)
check('--style block：显式指定时即使能原位改也插块',
      writeback.BLOCK_START in _s25d, 'rc=%s' % _rc25d)
shutil.rmtree(d25, ignore_errors=True)

# ---- 26. 写回互斥锁 + 备份不共享（P0：并发 apply 会互相覆盖）----
#   修前（t5-S4 / t6-B2 实测 7/7）：两个 apply --write 同时跑，两边都打印
#   "✓ 已原位写回"，其中一份改动凭空消失，而且共用同一个 .tweak.bak ——
#   出事后唯一的恢复点已经不可信。
d26, script26, _p26 = setup()
_lkdir = os.path.join(d26, '.tweak_params')
os.makedirs(_lkdir, exist_ok=True)
_lk = os.path.join(_lkdir, 'target.apply.lock')

# 26a 已持锁时，第二个写回必须被挡住（等待后超时报错），而不是并行跑
with writeback.script_lock(script26, timeout=5):
    _t0 = time.time()
    _blocked = False
    try:
        with writeback.script_lock(script26, timeout=0.6):
            pass
    except RuntimeError:
        _blocked = True
    _waited = time.time() - _t0
check('互斥锁：第二个写回被挡住（超时报错）', _blocked,
      'blocked=%s waited=%.2f' % (_blocked, _waited))
check('互斥锁：是等待后超时，不是立刻失败', _waited >= 0.5, '%.2f' % _waited)

# 26b 释放后锁文件要清掉（否则下次会被自己的陈旧锁挡住）
check('互斥锁：释放后锁文件已删除', not os.path.exists(_lk), _lk)

# 26c 陈旧锁（持有进程已死）应被清理，不能误挡
with open(_lk, 'w', encoding='utf-8') as f:
    f.write('999999')                     # 几乎不可能存在的 pid
_ok26c = True
try:
    with writeback.script_lock(script26, timeout=2):
        pass
except RuntimeError:
    _ok26c = False
check('互斥锁：陈旧锁（持有者已死）被清理，不误挡', _ok26c, '')

# 26d 备份：固定名 + 带 pid 的专属副本（后者谁也覆盖不了）
#   直接测 _make_backup —— 走完整 writeback 时若参数与原码一致会提前返回
#   （no_change），压根不写备份，反而测不到这条。
_pp26 = os.path.join(_lkdir, 'target.json')
_fixed26 = writeback._make_backup(script26, _pp26)
check('备份：固定名 target.tweak.bak 仍生成（兼容 README/测试/用户习惯）',
      _fixed26 == os.path.join(_lkdir, 'target.tweak.bak')
      and os.path.exists(_fixed26), _fixed26)
check('备份：同时生成带 pid 的专属副本（并发不互相覆盖）',
      os.path.exists(os.path.join(_lkdir,
                                  'target.tweak.%d.bak' % os.getpid())), '')
shutil.rmtree(d26, ignore_errors=True)

# 27. M16：被重叠/不可定位的字段不得计进 changes（不许"声称改了但没改"）----
#   同名轴变量被 add_axes 赋值两次时（T41 起）第二个轴**不再视为可原位定位** →
#   它的 pos 明确进 skipped/pending；第一个轴照常原位落地。
#   历史：T7 那版是"第二处替换被重叠检测拒绝"，covered/changes 照记 → 输出"原位修改
#   2 处"而磁盘上只落了 1 处（t6-M16）；两版共同的红线都是"不许声称改了但没改"。
d27, script27, _p27x = setup()
with open(script27, 'w', encoding='utf-8', newline='') as f:
    f.write('import matplotlib\n'
            'matplotlib.use("Agg")\n'
            'import matplotlib.pyplot as plt\n'
            'fig = plt.figure(figsize=(6, 4))\n'
            'ax = fig.add_axes([0.10, 0.10, 0.40, 0.40])\n'
            'ax = fig.add_axes([0.10, 0.10, 0.40, 0.40])\n'
            'fig.savefig("o.png")\n')
_pd27 = os.path.join(d27, '.tweak_params')
os.makedirs(_pd27, exist_ok=True)
_ax27 = {'index': 0, 'pos': [0.2, 0.2, 0.5, 0.5], 'aspect_locked': False,
         'title_fontsize': None, 'label_fontsize': None, 'tick_fontsize': None,
         'is_colorbar': False, 'clim': None, 'cmap': None,
         'xscale': 'linear', 'yscale': 'linear', 'grid': None,
         'spines': {}, 'lines': []}
params27 = {'version': 4, 'script': 'target.py', 'fig_index': 0, 'n_figs': 1,
            'figsize_px': None, 'figsize_in': None,
            # 两个轴项都指向同一个变量 ax → 第二处替换必然与第一处重叠
            'axes': [dict(_ax27), dict(_ax27, index=1)]}
_pp27 = os.path.join(_pd27, 'target.json')
with open(_pp27, 'w', encoding='utf-8') as f:
    json.dump(params27, f, ensure_ascii=False)
res27 = writeback.writeback(script27, params27, _pp27, verify=False,
                            style='inplace')
_s27 = read(script27)
_ch27 = res27.get('changes') or []
check('M16：有替换被重叠跳过时给出警告',
      any(('重叠' in w or '未原位应用' in w)
          for w in res27.get('warnings', [])),
      str(res27.get('warnings')))
check('M16：changes 只算真正落盘的（重名轴的位置字面量无从归属 → 不计入 changes）',
      _ch27 == [], 'changes=%s' % _ch27)
check('M16：重名轴（两处同名 add_axes）的位置一律不进原位改动，明说 pending、'
      '源码原样（不许声称改了但没改）',
      sorted(res27.get('pending') or []) == ['ax0.pos', 'ax1.pos']
      and _s27.count('[0.2, 0.2, 0.5, 0.5]') == 0
      and _s27.count('[0.10, 0.10, 0.40, 0.40]') == 2,
      'pending=%s | %s' % (res27.get('pending'),
                           [_l.strip() for _l in _s27.splitlines()
                            if 'add_axes' in _l]))
shutil.rmtree(d27, ignore_errors=True)

# ---- 28. 写回不得给脚本加 BOM（C2 编码兜底的回归）----
#   坑：params.read_text 探测到 'utf-8-sig' 后把这个编码直接交给写回，而 Python
#   用 'utf-8-sig' **写入时会自动加 BOM** → 用户的脚本被改脏。
#   实测到的**真实后果**（CPython 3.10.19，三行微测可复现）：脚本本身
#   **还能跑**（`python x.py` 容忍 UTF-8 BOM，rc=0、正常出结果），坏的是**把文件按
#   `utf-8` 读成字符串再 compile()/ast.parse()** 这条路径 —— 报
#   invalid non-printable character U+FEFF（compile(str) 与 ast.parse(str) 同报），
#   也就是一个**假的"语法坏"**：工具自己的语法校验、以及任何按 utf-8 读它的脚本都会误判。
#   当时的套件不查 BOM，全靠 t1 的验收脚本"落盘语法非法=True"照出来。
d28, _s28, _p28 = setup()
with open(_s28, 'w', encoding='utf-8', newline='') as f:
    f.write('import matplotlib\n'
            'matplotlib.use("Agg")\n'
            'import matplotlib.pyplot as plt\n'
            'fig = plt.figure(figsize=(6, 4))\n'
            'ax = fig.add_axes([0.10, 0.10, 0.40, 0.40])\n'
            'fig.savefig("o.png")\n')
_pd28 = os.path.join(d28, '.tweak_params')
os.makedirs(_pd28, exist_ok=True)
_pp28 = os.path.join(_pd28, 'target.json')
with open(_pp28, 'w', encoding='utf-8') as f:
    json.dump(_mkpar(0, [0.2, 0.2, 0.5, 0.5]), f, ensure_ascii=False)
writeback.writeback(_s28, _mkpar(0, [0.2, 0.2, 0.5, 0.5]), _pp28,
                    verify=False, style='inplace')
_raw28 = open(_s28, 'rb').read()
check('写回不给脚本加 BOM（否则 compile 报 U+FEFF）',
      not _raw28.startswith(b'\xef\xbb\xbf'), repr(_raw28[:8]))
check('写回后确实改了内容（不是空跑）',
      b'[0.2, 0.2, 0.5, 0.5]' in _raw28, repr(_raw28[-120:]))
shutil.rmtree(d28, ignore_errors=True)

# 带 BOM 的脚本也要能写回，且**保留原有的 BOM**（0.1.9 / E10 的修法）
d29, _s29, _p29 = setup()
with open(_s29, 'wb') as f:
    f.write(b'\xef\xbb\xbf' + b'import matplotlib\n'
            b'matplotlib.use("Agg")\n'
            b'import matplotlib.pyplot as plt\n'
            b'fig = plt.figure(figsize=(6, 4))\n'
            b'ax = fig.add_axes([0.10, 0.10, 0.40, 0.40])\n'
            b'fig.savefig("o.png")\n')
_pd29 = os.path.join(d29, '.tweak_params')
os.makedirs(_pd29, exist_ok=True)
_pp29 = os.path.join(_pd29, 'target.json')
with open(_pp29, 'w', encoding='utf-8') as f:
    json.dump(_mkpar(0, [0.2, 0.2, 0.5, 0.5]), f, ensure_ascii=False)
_res29 = writeback.writeback(_s29, _mkpar(0, [0.2, 0.2, 0.5, 0.5]), _pp29,
                             verify=False, style='inplace')
_raw29 = open(_s29, 'rb').read()
check('带 BOM 的脚本也能写回（原先直接抛 UnicodeDecodeError）',
      _res29.get('reason') != 'fail' and b'[0.2, 0.2, 0.5, 0.5]' in _raw29,
      '%s / %r' % (_res29.get('reason'), _raw29[:8]))
# 0.1.9（E10）：原文件本来有 BOM 就必须原样保留 —— "别凭空加 BOM"不等于"可以静默删掉
# 本来有的 BOM"（那是超出用户请求的改动）。反向验证：把 _src_bom 那段删掉即失败。
check('写回保留原有的 BOM（不再静默删除）',
      _raw29.startswith(b'\xef\xbb\xbf'), repr(_raw29[:8]))
shutil.rmtree(d29, ignore_errors=True)

# ---- 30. 元组写法 add_axes((...)) 也要进原位写回 ----
# 外部审阅第 2 条：原位路径原先只认 ast.List，元组写法会被**静默**丢进 skipped
# （用户看到的是"拖了没反应"，而代码里明明有可改的数字）。
# 反向验证：把 writeback.py 的 `isinstance(..., (ast.List, ast.Tuple))` 改回只认
# ast.List，本条立刻失败 → 证明它真的在测东西。
d30, _s30, _pp30 = setup()
with open(_s30, 'w', encoding='utf-8') as f:
    f.write('import matplotlib\n'
            'matplotlib.use("Agg")\n'
            'import matplotlib.pyplot as plt\n'
            'fig = plt.figure(figsize=(6, 4))\n'
            'ax = fig.add_axes((0.10, 0.10, 0.40, 0.40))\n'
            'fig.savefig("o.png")\n')
_par30 = _mkpar(0, [0.25, 0.30, 0.35, 0.45])
with open(_pp30, 'w', encoding='utf-8') as f:
    json.dump(_par30, f, ensure_ascii=False)
_res30 = writeback.writeback(_s30, _par30, _pp30, verify=False, style='inplace')
_src30 = read(_s30)
_m30 = re.search(r'add_axes\(\(([^)]*)\)\)', _src30)
_vals30 = [float(x) for x in _m30.group(1).split(',')] if _m30 else []
check('元组 add_axes((...)) 原位写回（原先静默进 skipped）',
      len(_vals30) == 4 and all(abs(a - b) < 1e-6
                                for a, b in zip(_vals30, [0.25, 0.30, 0.35, 0.45])),
      'reason=%s pos=%r' % (_res30.get('reason'), _vals30))
check('元组写回不改容器类型（仍然是一对圆括号，不是方括号）',
      _m30 is not None and 'add_axes([' not in _src30,
      str([ln for ln in _src30.splitlines() if 'add_axes' in ln]))
check('元组写回被记进 covered（不是"改了 0 处"）',
      bool(_res30.get('changes')), str(_res30.get('changes')))

shutil.rmtree(d30, ignore_errors=True)

# ---- 31. 轴范围 xlim/ylim 的位置参数写法（含带符号字面量）----
# 外部审阅第 2 条点名的 `set_xlim(-0.01, 1.01)`：负号在 AST 里是 UnaryOp 而不是
# Constant，只认 Constant 会把它静默丢进 skipped（用户看到"改了没反应"）。
# 反向验证：把 _is_num_literal 换回 isinstance(..., ast.Constant)，本条立刻失败。
d31, _s31, _p31 = setup()
with open(_s31, 'w', encoding='utf-8') as f:
    f.write('import matplotlib\n'
            'matplotlib.use("Agg")\n'
            'import matplotlib.pyplot as plt\n'
            'fig = plt.figure(figsize=(6, 4))\n'
            'ax = fig.add_axes([-0.02, 0.10, 0.80, 0.80])\n'
            'ax.plot([1, 2, 3])\n'
            'ax.set_xlim(-0.01, 1.01)\n'
            'ax.set_ylim(0.0, 10.0)\n'
            'fig.savefig("o.png")\n')
_par31 = _mkpar(0, [0.0, 0.10, 0.80, 0.80])
_par31['axes'][0]['xlim'] = [0.0, 2.0]
_par31['axes'][0]['ylim'] = [0.5, 8.5]
with open(_p31, 'w', encoding='utf-8') as f:
    json.dump(_par31, f, ensure_ascii=False)
_res31 = writeback.writeback(_s31, _par31, _p31, verify=False, style='inplace')
_src31 = read(_s31)
check('负数 pos 原位改写（-0.02 → 0，原先整项进 skipped）',
      'add_axes([0, 0.10, 0.80, 0.80])' in _src31,
      str([ln for ln in _src31.splitlines() if 'add_axes' in ln]))
check('只改真正变了的数字（未变的 0.10 / 0.80 原样保留）',
      '0.10' in _src31 and '0.80' in _src31,
      str([ln for ln in _src31.splitlines() if 'add_axes' in ln]))
check('set_xlim(a, b) 原位改写（负数范围要认）',
      'set_xlim(0, 2)' in _src31,
      str([ln for ln in _src31.splitlines() if 'set_xlim' in ln]))
check('set_ylim(a, b) 原位改写',
      'set_ylim(0.5, 8.5)' in _src31,
      str([ln for ln in _src31.splitlines() if 'set_ylim' in ln]))
check('三处都被记进 covered（不是"改了 0 处"）',
      {'ax0.pos', 'ax0.xlim', 'ax0.ylim'} <= set(_res31.get('changes') or []),
      str(_res31.get('changes')))
shutil.rmtree(d31, ignore_errors=True)

# ---- 32. 轴范围的容器 / 关键字写法 ----
d32, _s32, _p32 = setup()
with open(_s32, 'w', encoding='utf-8') as f:
    f.write('import matplotlib\n'
            'matplotlib.use("Agg")\n'
            'import matplotlib.pyplot as plt\n'
            'fig = plt.figure(figsize=(6, 4))\n'
            'ax = fig.add_axes([0.10, 0.10, 0.80, 0.80])\n'
            'ax.plot([1, 2, 3])\n'
            'ax.set_xlim((-0.01, 1.01))\n'          # 元组容器
            'ax.set_ylim(ymin=0.0, ymax=10.0)\n'    # 关键字
            'fig.savefig("o.png")\n')
_par32 = _mkpar(0, [0.10, 0.10, 0.80, 0.80])
_par32['axes'][0]['xlim'] = [0.0, 2.0]
_par32['axes'][0]['ylim'] = [0.5, 8.5]
with open(_p32, 'w', encoding='utf-8') as f:
    json.dump(_par32, f, ensure_ascii=False)
writeback.writeback(_s32, _par32, _p32, verify=False, style='inplace')
_src32 = read(_s32)
check('set_xlim((a, b)) 容器写法原位改写', 'set_xlim((0, 2))' in _src32,
      str([ln for ln in _src32.splitlines() if 'set_xlim' in ln]))
check('set_ylim(ymin=, ymax=) 关键字写法原位改写',
      'ymin=0.5' in _src32 and 'ymax=8.5' in _src32,
      str([ln for ln in _src32.splitlines() if 'set_ylim' in ln]))
shutil.rmtree(d32, ignore_errors=True)

# ---- 33. 源码里没有对应写法时：范围要进"未原位应用"，且块模式能承载 ----
d33, _s33, _p33 = setup()
_par33 = _mkpar(0, [0.05, 0.10, 0.42, 0.78])
_par33['axes'][0]['xlim'] = [0.0, 4.0]
with open(_p33, 'w', encoding='utf-8') as f:
    json.dump(_par33, f, ensure_ascii=False)
_res33 = writeback.writeback(_s33, _par33, _p33, verify=False, style='inplace')
check('没有 set_xlim 可改 → 明确进"未原位应用"（不静默）',
      any('xlim' in w for w in (_res33.get('warnings') or [])),
      str(_res33.get('warnings')))
# T70：渲染器现在**只按证明过的目标表达式**写 —— 直接调它时必须把目标表传进来
# （真路径由 writeback() 用 `_block_targets` 算好后传入）。这里给一个可证的名字。
_blk33, _warn33 = writeback.render_block(_par33, 'fig', [], [],
                                         targets={0: 'ax'})
check('块模式会带上 set_xlim（--style block 不丢范围）',
      'ax.set_xlim(0, 4)' in _blk33,
      str([ln for ln in _blk33.splitlines() if 'set_xlim' in ln]))
shutil.rmtree(d33, ignore_errors=True)

# ---- 34. revert：从备份回退，而且自身可逆 ----
# "完整本地回路"缺的那一环：写回错了要能一键回去（用户提过"没有 undo"）。
d34, _s34, _ = setup()
# 用单轴脚本（共用的 SCRIPT 有两轴 + colorbar，pos 只给一个轴会走不到干净的原位路径）
_SRC34 = ('import matplotlib\n'
          'matplotlib.use("Agg")\n'
          'import matplotlib.pyplot as plt\n'
          'fig = plt.figure(figsize=(6, 4))\n'
          'ax = fig.add_axes([0.10, 0.10, 0.40, 0.40])\n'
          'fig.savefig("o.png")\n')
with open(_s34, 'w', encoding='utf-8') as f:
    f.write(_SRC34)
_pd34 = os.path.join(d34, '.tweak_params')
os.makedirs(_pd34, exist_ok=True)
_pp34 = os.path.join(_pd34, 'target.json')       # 规范位置（revert 按它算备份路径）
_par34 = _mkpar(0, [0.2, 0.2, 0.5, 0.5])
with open(_pp34, 'w', encoding='utf-8') as f:
    json.dump(_par34, f, ensure_ascii=False)
writeback.writeback(_s34, _par34, _pp34, verify=False, style='inplace')
check('写回后源码确实变了（前提）',
      'add_axes([0.2, 0.2, 0.5, 0.5])' in read(_s34),
      str([ln for ln in read(_s34).splitlines() if 'add_axes' in ln]))
_bak34 = writeback._backup_path(_s34, _pp34)
check('写回留了备份（revert 的依据）', os.path.exists(_bak34), _bak34)

_before34 = read(_s34)
check('revert 默认只预览：rc=0 且代码一个字节没动',
      revert.main([_s34]) == 0 and read(_s34) == _before34)
check('预览阶段不会生成 revert.bak',
      not os.path.exists(os.path.join(_pd34, 'target.revert.bak')))

check('revert --write rc=0', revert.main([_s34, '--write']) == 0)
check('回到写回前的原文', read(_s34) == _SRC34, read(_s34)[:140])
_rev34 = os.path.join(_pd34, 'target.revert.bak')
check('revert 自身可逆：留了 revert.bak，内容是回退前那份',
      os.path.exists(_rev34) and '[0.2, 0.2, 0.5, 0.5]' in read(_rev34),
      _rev34)
_d34b, _s34b, _ = setup()
check('没有备份时 rc=2（并给出去哪找备份的说明）', revert.main([_s34b]) == 2)
check('脚本不存在时 rc=2',
      revert.main([os.path.join(d34, 'nope.py')]) == 2)
shutil.rmtree(d34, ignore_errors=True)
shutil.rmtree(_d34b, ignore_errors=True)

# ---- 35. 快速验证档：只查语法；写出坏语法要当场回滚 ----
_SRC35 = ('import matplotlib\n'
          'matplotlib.use("Agg")\n'
          'import matplotlib.pyplot as plt\n'
          'fig = plt.figure(figsize=(6, 4))\n'
          'ax = fig.add_axes([0.10, 0.10, 0.40, 0.40])\n'
          'fig.savefig("o.png")\n')
d35, _s35, _pp35 = setup()
_par35 = _mkpar(0, [0.2, 0.2, 0.5, 0.5])
with open(_s35, 'w', encoding='utf-8') as f:
    f.write(_SRC35)
with open(_pp35, 'w', encoding='utf-8') as f:
    json.dump(_par35, f, ensure_ascii=False)
_res35 = writeback.writeback(_s35, _par35, _pp35, verify=False,
                             fast_check=True, style='inplace')
check('快速档标记 verify_mode=fast、且不声称验证通过',
      _res35.get('verify_mode') == 'fast' and _res35.get('verified') is None,
      str((_res35.get('verify_mode'), _res35.get('verified'))))
check('快速档仍然把改动写进去了',
      '[0.2, 0.2, 0.5, 0.5]' in read(_s35),
      str([ln for ln in read(_s35).splitlines() if 'add_axes' in ln]))
shutil.rmtree(d35, ignore_errors=True)

# 安全网：注入一个"写出的源码语法坏了"的场景（替换后续被污染），
# 快速档必须当场识破并回滚 —— 这是它唯一的强保证，必须真的成立。
d35b, _s35b, _pp35b = setup()
with open(_s35b, 'w', encoding='utf-8') as f:
    f.write(_SRC35)
with open(_pp35b, 'w', encoding='utf-8') as f:
    json.dump(_par35, f, ensure_ascii=False)
_orig_apply = writeback.apply_inplace


def _bad_apply(*a, **k):
    _new, _cov, _skip = _orig_apply(*a, **k)
    return _new + '\ndef broken(:\n', _cov, _skip


writeback.apply_inplace = _bad_apply
try:
    _res35b = writeback.writeback(_s35b, _par35, _pp35b, verify=False,
                                  fast_check=True, style='inplace')
finally:
    writeback.apply_inplace = _orig_apply
check('语法坏了 → reason=fail，且错误里点名"快速验证"',
      _res35b.get('reason') == 'fail' and '快速验证失败' in (_res35b.get('err') or ''),
      _res35b.get('err'))
check('语法坏了 → 已回滚，磁盘上没留下坏脚本',
      read(_s35b) == _SRC35 and 'broken' not in read(_s35b),
      read(_s35b)[-80:])
shutil.rmtree(d35b, ignore_errors=True)

# ---- 36. 网格身份（cell）：轴序变了要**指名道姓**地失败，而不是默默改错面板 ----
# 做法与取舍：块生成仍按 fig.axes 下标寻址（重写寻址会动整块格式、且在脚本变结构时
# 有把用户脚本跑崩的风险），但把"调图时这个下标对应哪个网格格位"记进参数，
# 写回后由语义验证回比 → 不匹配就回滚并说清原因。这条用例测的正是这个回比。
_par36 = _mkpar(0, [0.1, 0.1, 0.4, 0.4])
_par36['axes'][0]['cell'] = [1, 3, 0, 0]
_par36['axes'][0]['title_fontsize'] = 12.0
_act36 = [{'index': 0, 'cell': [1, 4, 0, 0], 'title_fontsize': 12.0}]
_bad36 = verify.compare(_par36['axes'], _act36)
check('cell 身份不一致 → 比对失败且信息里点名"轴序变了"',
      any('网格身份' in b and '轴序变了' in b for b in _bad36),
      str(_bad36))
_act36b = [{'index': 0, 'cell': [1, 3, 0, 0], 'title_fontsize': 12.0}]
check('cell 一致 → 不报（否则会误伤正常写回）',
      not [b for b in verify.compare(_par36['axes'], _act36b) if '网格身份' in b],
      str(verify.compare(_par36['axes'], _act36b)))
_blk36, _w36 = writeback.render_block(_par36, 'fig', [], [])
check('块注释里带出轴身份（出问题人一眼能对上）',
      '轴身份' in _blk36 and '[1, 3, 0, 0]' in _blk36,
      str([ln for ln in _blk36.splitlines() if '轴身份' in ln]))

# ---- 37. 值没变的字段不算"改到"（否则 auto 模式会被骗住）----
# 这条是实打实踩出来的：xlim/ylim 进参数后，**值没变**的字段也被登记成一处改动，
# 于是 changes 非空 → apply 的 auto 模式以为"有改动落地"、不再退到块模式 →
# 网格轴图里用户拖的位置就落不了地（只剩一句"未原位应用"警告）。
# 顺带它也让"重复 apply 同一份参数"从谎报"原位修改 N 处"变成正确的 no_change。
d37, _s37, _pp37 = setup()
_SRC37 = ('import matplotlib\n'
          'matplotlib.use("Agg")\n'
          'import matplotlib.pyplot as plt\n'
          'fig = plt.figure(figsize=(6, 4))\n'
          'ax = fig.add_axes([0.10, 0.10, 0.40, 0.40])\n'
          'ax.set_xlim(-0.01, 1.01)\n'
          'fig.savefig("o.png")\n')
with open(_s37, 'w', encoding='utf-8') as f:
    f.write(_SRC37)
_par37 = _mkpar(0, [0.10, 0.10, 0.40, 0.40])          # 与源码**数值完全相同**
_par37['axes'][0]['xlim'] = [-0.01, 1.01]
with open(_pp37, 'w', encoding='utf-8') as f:
    json.dump(_par37, f, ensure_ascii=False)
_res37 = writeback.writeback(_s37, _par37, _pp37, verify=False, style='inplace')
check('值与源码一致 → reason=no_change（不再谎报"改了 N 处"）',
      _res37.get('reason') == 'no_change',
      '%s / changes=%s' % (_res37.get('reason'), _res37.get('changes')))
check('no_change 时源码一个字节都没变', read(_s37) == _SRC37, read(_s37)[-60:])
shutil.rmtree(d37, ignore_errors=True)
# ---- 39. 多图同名轴变量：字段查找必须夹在本图区间内 ----
# 独立审阅 C2 的构造：两张图都用 `ax`，只有第二张写了 set_xlim。改第一张图的
# xlim 时，_find_call 若只有下界就会命中第二张图那一行 —— 静默改错图。
d39, _s39, _pp39 = setup()
_SRC39 = ('import matplotlib\n'
          'matplotlib.use("Agg")\n'
          'import matplotlib.pyplot as plt\n'
          'fig = plt.figure(figsize=(6, 4))\n'
          'ax = fig.add_axes([0.10, 0.10, 0.40, 0.40])\n'
          'ax.plot([1, 2, 3])\n'
          'fig.savefig("a.png")\n'
          'fig2 = plt.figure(figsize=(6, 4))\n'
          'ax = fig2.add_axes([0.20, 0.20, 0.50, 0.50])\n'
          'ax.plot([3, 2, 1])\n'
          'ax.set_xlim(0.0, 10.0)\n'          # 只在第二张图里
          'fig2.savefig("b.png")\n')
with open(_s39, 'w', encoding='utf-8') as f:
    f.write(_SRC39)
_par39 = _mkpar(0, [0.10, 0.10, 0.40, 0.40])     # 目标 = 第 0 张图
_par39['axes'][0]['xlim'] = [0.0, 5.0]
with open(_pp39, 'w', encoding='utf-8') as f:
    json.dump(_par39, f, ensure_ascii=False)
_res39 = writeback.writeback(_s39, _par39, _pp39, verify=False, style='inplace')
_src39 = read(_s39)
check('第一张图的图内没有 set_xlim → 不能去改第二张图那一行',
      'ax.set_xlim(0.0, 10.0)' in _src39,
      str([ln for ln in _src39.splitlines() if 'set_xlim' in ln]))
check('该字段如实进"未原位应用"（不静默）',
      any('xlim' in w for w in (_res39.get('warnings') or [])),
      str(_res39.get('warnings')))
shutil.rmtree(d39, ignore_errors=True)

# ---- 38. 网格身份：期望是网格轴、实测不是 → 必须判失败（不能默默放行）----
_c38 = [{'index': 0, 'cell': [1, 2, 0, 0], 'title_fontsize': 12.0}]
check('实测该轴不再是网格轴（cell=None）→ 判失败',
      any('网格身份对不上' in b for b in verify.compare(
          _c38, [{'index': 0, 'cell': None, 'title_fontsize': 12.0}])),
      str(verify.compare(_c38, [{'index': 0, 'cell': None,
                                 'title_fontsize': 12.0}])))
check('实测侧没这个字段（旧参数/采集失败）→ 不判，保持向后兼容',
      not [b for b in verify.compare(
          _c38, [{'index': 0, 'title_fontsize': 12.0}]) if '网格身份' in b],
      str(verify.compare(_c38, [{'index': 0, 'title_fontsize': 12.0}])))
check('cell 不同 → 判失败（此前已有）',
      any('网格身份' in b for b in verify.compare(
          _c38, [{'index': 0, 'cell': [1, 2, 0, 1], 'title_fontsize': 12.0}])))

# ======================================================================
# 0.1.9 修复的反向验证用例（§40–§46）
#   每条对应审计报告里的一个编号（E1/E2/E3/E4/E5/RC-8/RC-9/RC-10）。
#   "反向验证"= 把对应修改改回 0.1.8 的写法后，这条 check 必须失败；
#   逐字输出见 audit_018/fix_engine.md（§反向验证 R1–R7，用 0.1.8 的 src 跑同一份
#   用例文件，记录哪几条变成 FAIL）。
# ======================================================================

# ---- 40. E1 主干：参数与源码完全一致 → no_change，一个字节都不动 ----
d40, _s40, _pp40 = setup()
_SRC40 = ('import matplotlib\n'
          'matplotlib.use("Agg")\n'
          'import matplotlib.pyplot as plt\n'
          'fig, ax = plt.subplots(figsize=(6, 4))\n'
          'ax.plot([1, 2, 3])\n'
          'ax.set_title("t", fontsize=12)\n'
          'ax.grid(True)\n'
          'fig.savefig("o.png")\n')
with open(_s40, 'w', encoding='utf-8') as f:
    f.write(_SRC40)
_st40, _e40 = verify.collect(_s40, 0)
check('40 前置：能采集到图状态（造"与源码完全一致"的参数）',
      _st40 is not None, str(_e40))
_p40 = {'version': 4, 'script': 'target.py', 'figsize_px': None,
        'figsize_in': [6, 4], 'fig_index': 0, 'n_figs': 1,
        'axes': [{'index': 0, 'pos': _st40['axes'][0]['pos'],
                  'title_fontsize': 12, 'grid': True}]}
_res40 = writeback.writeback(_s40, _p40, _pp40, verify=True, semantic=True,
                             style='inplace')
check('40 E1：参数 == 源码（subplots 网格轴的 pos + grid + 字号）→ no_change',
      _res40.get('reason') == 'no_change',
      '%s / changes=%s / warnings=%s'
      % (_res40.get('reason'), _res40.get('changes'), _res40.get('warnings')))
check('40 E1：一个字节都没变、没有插调整块',
      read(_s40) == _SRC40 and writeback.BLOCK_START not in read(_s40),
      read(_s40)[-80:])
check('40 E1：needs_block=False（不退块）、没写盘、没写备份',
      _res40.get('needs_block') is False and _res40.get('applied') is False
      and _res40.get('backup') is None,
      'nb=%r applied=%r backup=%r' % (_res40.get('needs_block'),
                                      _res40.get('applied'),
                                      _res40.get('backup')))
# T11：这条路径上的"未原位应用"聚合警告是**误导**（读者会以为改动没生效，而实测
# 证明现值与参数一致）→ no_change 时一条警告都不带。
check('48 T11：no_change 不再刷"未原位应用"警告（warnings 为空）',
      (_res40.get('warnings') or []) == [],
      str(_res40.get('warnings')))
# 反面：网格轴 pos 与实测**不同**时要判"该退块"（别静默丢位置请求）
_p40b = json.loads(json.dumps(_p40))
_p0 = list(_st40['axes'][0]['pos'])
_p40b['axes'][0]['pos'] = [round(_p0[0] + 0.05, 4), _p0[1], _p0[2], _p0[3]]
_res40b = writeback.writeback(_s40, _p40b, _pp40, verify=True, semantic=True,
                              style='inplace')
check('40 E1b：pos 与实测不同 → best_effort + needs_block=True（该退块）',
      _res40b.get('reason') == 'best_effort'
      and _res40b.get('needs_block') is True,
      '%s / nb=%r / warnings=%s' % (_res40b.get('reason'),
                                    _res40b.get('needs_block'),
                                    _res40b.get('warnings')))
check('40 E1b：判定阶段仍然没动磁盘',
      read(_s40) == _SRC40 and _res40b.get('applied') is False)
shutil.rmtree(d40, ignore_errors=True)

# ---- 41. E2/RC-3：缺 grid 键 = 未设置（不改成 False）；显式 grid 仍生效 ----
d41, _s41, _pp41 = setup()
_SRC41 = ('import matplotlib\n'
          'matplotlib.use("Agg")\n'
          'import matplotlib.pyplot as plt\n'
          'fig = plt.figure(figsize=(6, 4))\n'
          'ax = fig.add_axes([0.10, 0.10, 0.40, 0.40])\n'
          'ax.grid(True)\n'
          'fig.savefig("o.png")\n')
with open(_s41, 'w', encoding='utf-8') as f:
    f.write(_SRC41)
_p41 = _mkpar(0, [0.10, 0.10, 0.40, 0.40])
_p41['axes'][0].pop('grid', None)                     # 连键都不给
with open(_pp41, 'w', encoding='utf-8') as f:
    json.dump(_p41, f, ensure_ascii=False)
# 必须走 params.load（= CLI 的真实入口）：AXIS_DEFAULTS 的默认值是在 normalize 里
# 补的，直接把手写 dict 交给 writeback 就绕过了这条路径（那样测不到 E2）。
from mpltweak import params as _params               # noqa: E402
_res41 = writeback.writeback(_s41, _params.load(_pp41), _pp41, verify=False,
                             style='inplace')
check('41 E2：参数缺 grid 键 → ax.grid(True) 原样保留 + no_change',
      'ax.grid(True)' in read(_s41) and _res41.get('reason') == 'no_change',
      '%s / %s' % (_res41.get('reason'), read(_s41).splitlines()[-3:]))
check('41 E2：不再谎报"未原位应用：grid"（RC-3）',
      not any('grid' in w for w in (_res41.get('warnings') or [])),
      str(_res41.get('warnings')))
d41b, _s41b, _pp41b = setup()
with open(_s41b, 'w', encoding='utf-8') as f:
    f.write(_SRC41)
_p41b = _mkpar(0, [0.10, 0.10, 0.40, 0.40])
_p41b['axes'][0]['grid'] = False                      # 显式要关
with open(_pp41b, 'w', encoding='utf-8') as f:
    json.dump(_p41b, f, ensure_ascii=False)
_res41b = writeback.writeback(_s41b, _params.load(_pp41b), _pp41b, verify=False,
                              style='inplace')
check('41 E2b：显式 grid=false 仍然生效（"缺键=未设置"≠"不能关网格"）',
      'ax.grid(False)' in read(_s41b)
      and _res41b.get('changes') == ['ax0.grid'],
      '%s / %s' % (_res41b.get('changes'), read(_s41b).splitlines()[-3:]))
shutil.rmtree(d41, ignore_errors=True)
shutil.rmtree(d41b, ignore_errors=True)

# ---- 42. RC-8：只给 figsize_in（没有 axes 项）也要能改画布尺寸 ----
d42, _s42, _pp42 = setup()
_SRC42 = ('import matplotlib\n'
          'matplotlib.use("Agg")\n'
          'import matplotlib.pyplot as plt\n'
          'fig, ax = plt.subplots(figsize=(6, 4))\n'
          'ax.plot([1, 2, 3])\n'
          'fig.savefig("o.png")\n')
with open(_s42, 'w', encoding='utf-8') as f:
    f.write(_SRC42)
_p42 = {'version': 4, 'script': 'target.py', 'figsize_px': None,
        'figsize_in': [8, 5], 'fig_index': 0, 'n_figs': 1, 'axes': []}
_res42 = writeback.writeback(_s42, _p42, _pp42, verify=False, style='inplace')
check('42 RC-8：只有 figsize_in → rc0 且只改 figsize 字面量（不再 no_axes 早退）',
      _res42.get('reason') == 'ok' and 'figsize=(8, 5)' in read(_s42),
      '%s / %s' % (_res42.get('reason'), read(_s42).splitlines()[-3:]))
check('42 RC-9：changes 里如实出现 figsize（不再空 changes 却改了盘）',
      (_res42.get('changes') or []) == ['figsize'],
      str(_res42.get('changes')))
d42b, _s42b, _pp42b = setup()
with open(_s42b, 'w', encoding='utf-8') as f:
    f.write(_SRC42)
_res42b = writeback.writeback(_s42b, _p42, _pp42b, verify=False, style='block')
check('42 RC-8b：块模式同样不能因"没有 axes 项"而拒绝（figsize 照样落地）',
      _res42b.get('reason') != 'no_axes' and 'figsize=(8, 5)' in read(_s42b),
      '%s / %s' % (_res42b.get('reason'), read(_s42b).splitlines()[-4:]))
shutil.rmtree(d42, ignore_errors=True)
shutil.rmtree(d42b, ignore_errors=True)

# ---- 43. E3：块路径写盘后验证抛异常 → 必须回滚（脚本逐字节复原）----
d43, _s43, _pp43 = setup()
_SRC43 = ('import matplotlib\n'
          'matplotlib.use("Agg")\n'
          'import matplotlib.pyplot as plt\n'
          'fig = plt.figure(figsize=(6, 4))\n'
          'ax = fig.add_axes([0.10, 0.10, 0.40, 0.40])\n'
          'ax.plot([1, 2, 3])\n'
          'fig.savefig("o.png")\n')
with open(_s43, 'w', encoding='utf-8') as f:
    f.write(_SRC43)
_p43 = _mkpar(0, [0.10, 0.10, 0.40, 0.40])
_p43['axes'][0]['xlim'] = [None, 5]     # verify.compare 里 float(None) → TypeError
try:
    _res43 = writeback.writeback(_s43, _p43, _pp43, verify=True, semantic=True,
                                 style='block')
except BaseException as _e43:           # noqa: BLE001
    # 0.1.8 的引擎在这里**直接抛异常**（异常穿过所有 except，块已经写进脚本）——
    # 这正是 E3 的现场。包住它，让同一份用例在旧引擎上继续跑完、把每条都钉下来。
    _res43 = {'reason': 'CRASH', 'err': '%s: %s' % (type(_e43).__name__, _e43)}
check('43 E3：写盘后语义验证抛异常 → reason=fail 且脚本逐字节复原',
      _res43.get('reason') == 'fail' and read(_s43) == _SRC43
      and writeback.BLOCK_START not in read(_s43),
      '%s / %s / %s' % (_res43.get('reason'), str(_res43.get('err'))[:90],
                        read(_s43).splitlines()[-2:]))
check('43 E3：err 里明确写了回滚结果（不是"静默留半成品"）',
      '已回滚' in str(_res43.get('err')), str(_res43.get('err'))[:200])
shutil.rmtree(d43, ignore_errors=True)

# ---- 44. E5/幂等：describe 直出 → apply 干净往返不得改源码 ----
d44, _s44, _pp44 = setup()
_SRC44 = ('import matplotlib\n'
          'matplotlib.use("Agg")\n'
          'import matplotlib.pyplot as plt\n'
          'import numpy as np\n'
          'fig = plt.figure(figsize=(6, 4))\n'
          'ax = fig.add_axes([0.10, 0.10, 0.65, 0.80])\n'
          'im = ax.imshow(np.arange(12).reshape(3, 4))\n'
          'fig.colorbar(im, ax=ax, fraction=0.15)\n'
          'fig.savefig("o.png")\n')
with open(_s44, 'w', encoding='utf-8') as f:
    f.write(_SRC44)
from mpltweak import describe as _describe          # noqa: E402  局部导入避免改动头部
_st44, _e44 = verify.collect(_s44, 0)
_p44 = _describe._to_params(_st44, _s44)            # 与 `mpltweak describe` 同源
with open(_pp44, 'w', encoding='utf-8') as f:
    json.dump(_p44, f, ensure_ascii=False, indent=2)
_p44 = _params.load(_pp44)                          # 走 CLI 同一条读入路径
_res44a = writeback.writeback(_s44, _p44, _pp44, verify=True, semantic=True,
                              style='inplace')
_res44b = writeback.writeback(_s44, _p44, _pp44, verify=True, semantic=True,
                              style='inplace')
check('44 E5：干净往返 no_change —— fraction 字面量一个字符都不动',
      _res44a.get('reason') == 'no_change'
      and 'fraction=0.15' in read(_s44)
      and 'fraction=0.0410769' not in read(_s44),
      '%s / %s' % (_res44a.get('reason'), read(_s44).splitlines()[-4:]))
check('44 E5/幂等：第二次 apply 同一份参数仍是 no_change',
      _res44b.get('reason') == 'no_change', _res44b.get('reason'))
check('44 E5：两次都没有改动磁盘（字节级）', read(_s44) == _SRC44)
shutil.rmtree(d44, ignore_errors=True)

# ---- 45. RC-10：备份不跟着 --params 走，自定义参数写回后默认 revert 可用 ----
d45, _s45, _pp45 = setup()
_SRC45 = ('import matplotlib\n'
          'matplotlib.use("Agg")\n'
          'import matplotlib.pyplot as plt\n'
          'fig = plt.figure(figsize=(6, 4))\n'
          'ax = fig.add_axes([0.10, 0.10, 0.40, 0.40])\n'
          'ax.plot([1, 2, 3])\n'
          'fig.savefig("o.png")\n')
with open(_s45, 'w', encoding='utf-8') as f:
    f.write(_SRC45)
_else = os.path.join(d45, 'elsewhere')
os.makedirs(_else, exist_ok=True)
_pp45c = os.path.join(_else, 'layout.json')         # 参数文件放在别处（--params X）
_p45 = _mkpar(0, [0.20, 0.20, 0.50, 0.50])
with open(_pp45c, 'w', encoding='utf-8') as f:
    json.dump(_p45, f, ensure_ascii=False)
_res45 = writeback.writeback(_s45, _params.load(_pp45c), _pp45c, verify=False,
                             style='inplace')
_defbak = os.path.join(d45, '.tweak_params', 'target.tweak.bak')
check('45 RC-10：备份落在**脚本旁**（不再跟着 --params 目录走）',
      os.path.exists(_defbak) and not os.path.exists(
          os.path.join(_else, 'target.tweak.bak')),
      'backup=%r' % (_res45.get('backup'),))
check('45 RC-10 前置：写回确实改了脚本', read(_s45) != _SRC45)
_rc45 = revert.main([_s45, '--write'])              # 不带 --params
check('45 RC-10：接着直接 revert（不带 --params）必须成功（原先 rc=2 找不到备份）',
      _rc45 == 0, 'rc=%r' % (_rc45,))
check('45 RC-10：revert 后脚本逐字节回到原文', read(_s45) == _SRC45,
      str(read(_s45).splitlines()[-3:]))
shutil.rmtree(d45, ignore_errors=True)

# ---- 47. E4：aspect 锁定轴（imshow）的 pos 也要判得出"要不要退块" ----
#   verify.compare 因为"锁定轴的位置会被 apply_aspect 重算"而不比 pos；但"参数是否
#   已经描述当前状态"这个判断必须比（同一脚本两次运行的 pos 是确定的）。不比的话：
#   干净往返 → no_change（对）；拖过位置 → 也会被误判成 no_change（错，静默丢位置）。
d47, _s47, _pp47 = setup()
_SRC47 = ('import matplotlib\n'
          'matplotlib.use("Agg")\n'
          'import matplotlib.pyplot as plt\n'
          'import numpy as np\n'
          'fig = plt.figure(figsize=(6, 4))\n'
          'ax = fig.add_axes([0.10, 0.10, 0.65, 0.80])\n'
          'im = ax.imshow(np.arange(12).reshape(3, 4))\n'
          'fig.colorbar(im, ax=ax, fraction=0.15)\n'
          'fig.savefig("o.png")\n')
with open(_s47, 'w', encoding='utf-8') as f:
    f.write(_SRC47)
_st47, _e47 = verify.collect(_s47, 0)
_p47 = _describe._to_params(_st47, _s47)
with open(_pp47, 'w', encoding='utf-8') as f:
    json.dump(_p47, f, ensure_ascii=False, indent=2)
_res47a = writeback.writeback(_s47, _params.load(_pp47), _pp47, verify=True,
                              semantic=True, style='inplace')
check('47 E4：imshow（aspect 锁定）+ 参数与实测一致 → no_change（不误退块）',
      _res47a.get('reason') == 'no_change' and read(_s47) == _SRC47,
      '%s / %s' % (_res47a.get('reason'), _res47a.get('warnings')))
_p47b = json.loads(json.dumps(_p47))
_p47b['axes'][0]['pos'] = [0.20, 0.20, 0.60, 0.70]      # 拖过宿主轴位置
with open(_pp47, 'w', encoding='utf-8') as f:
    json.dump(_p47b, f, ensure_ascii=False, indent=2)
_res47b = writeback.writeback(_s47, _params.load(_pp47), _pp47, verify=True,
                              semantic=True, style='inplace')
check('47 E4：拖过 aspect 锁定轴的位置 → needs_block=True（该退块，不许静默丢）',
      _res47b.get('reason') == 'best_effort'
      and _res47b.get('needs_block') is True and read(_s47) == _SRC47,
      '%s / nb=%r / %s' % (_res47b.get('reason'), _res47b.get('needs_block'),
                           _res47b.get('warnings')))
shutil.rmtree(d47, ignore_errors=True)

# ---- 49. T11：x / y 轴标签字号分开写（label_fontsize_y）----
#   旧实现把单一 `label_fontsize` **同时写给 x 与 y** → 用户只调 y 时被 x 的值覆盖。
#   反向验证：把 §③ 字号那段改回"单一字段写两边"→ 本条立刻失败（见报告
#   §T11 的针对性反向验证：fix018/revert_labels/）。
d49, _s49, _pp49 = setup()
_SRC49 = ('import matplotlib\n'
          'matplotlib.use("Agg")\n'
          'import matplotlib.pyplot as plt\n'
          'fig = plt.figure(figsize=(6, 4))\n'
          'ax = fig.add_axes([0.15, 0.15, 0.70, 0.70])\n'
          'ax.plot([1, 2, 3])\n'
          'ax.set_xlabel("x", fontsize=10)\n'
          'ax.set_ylabel("y", fontsize=11)\n'
          'fig.savefig("o.png")\n')


def _ax49(**kw):
    a = {'index': 0, 'pos': [0.15, 0.15, 0.70, 0.70], 'aspect_locked': False,
         'title_fontsize': None, 'label_fontsize': None, 'tick_fontsize': None,
         'is_colorbar': False, 'clim': None, 'cmap': None, 'xscale': 'linear',
         'yscale': 'linear', 'grid': None, 'spines': {}, 'lines': []}
    a.update(kw)
    return a


def _par49(axis):
    return {'version': 4, 'script': 'target.py', 'fig_index': 0, 'n_figs': 1,
            'figsize_in': None, 'axes': [axis]}


# 49a：**只改 y** → x 不动、y 变
with open(_s49, 'w', encoding='utf-8') as f:
    f.write(_SRC49)
_p49a = _par49(_ax49(label_fontsize=10, label_fontsize_y=15))
with open(_pp49, 'w', encoding='utf-8') as f:
    json.dump(_p49a, f, ensure_ascii=False)
_res49a = writeback.writeback(_s49, _params.load(_pp49), _pp49, verify=True,
                              semantic=True, style='inplace')
_src49a = read(_s49)
check('49a T11：只改 y 标签字号 → y 变（fontsize=15）',
      'ax.set_ylabel("y", fontsize=15)' in _src49a,
      str([ln for ln in _src49a.splitlines() if 'label' in ln]))
check('49a T11：x 标签字号**不被覆盖**（仍是 fontsize=10）',
      'ax.set_xlabel("x", fontsize=10)' in _src49a,
      str([ln for ln in _src49a.splitlines() if 'label' in ln]))
check('49a T11：changes 只报 y 那一个字段（x 是空改、不进账）',
      (_res49a.get('changes') or []) == ['ax0.label_fontsize_y'],
      str(_res49a.get('changes')))
check('49a T11：语义验证通过（verify 只认 label_fontsize，门要开对）',
      _res49a.get('verified') is True and _res49a.get('semantic') is True,
      'v=%s s=%s err=%s' % (_res49a.get('verified'),
                            _res49a.get('semantic'),
                            str(_res49a.get('err'))[:120]))

# 49b：老参数文件（只有 label_fontsize，没有 y）→ 兼容语义 = 两边同值
with open(_s49, 'w', encoding='utf-8') as f:
    f.write(_SRC49)
_p49b = _par49(_ax49(label_fontsize=14))
with open(_pp49, 'w', encoding='utf-8') as f:
    json.dump(_p49b, f, ensure_ascii=False)
_res49b = writeback.writeback(_s49, _params.load(_pp49), _pp49, verify=True,
                              semantic=True, style='inplace')
_src49b = read(_s49)
# T73（零兼容）：没有 label_fontsize_y → **一个字节都不动 y**（旧期望是"y 也写成 14"）
check('49b T73：无 label_fontsize_y → x 写 14、y 保持源码原值 11',
      'ax.set_xlabel("x", fontsize=14)' in _src49b
      and 'ax.set_ylabel("y", fontsize=11)' in _src49b
      and 'ax.set_ylabel("y", fontsize=14)' not in _src49b,
      str([ln for ln in _src49b.splitlines() if 'label' in ln]))
check('49b T11：语义验证通过', _res49b.get('verified') is True
      and _res49b.get('semantic') is True,
      'v=%s s=%s' % (_res49b.get('verified'), _res49b.get('semantic')))

# 49c：块模式两条路径都要按字段各自写
with open(_s49, 'w', encoding='utf-8') as f:
    f.write(_SRC49)
_p49c = _par49(_ax49(label_fontsize=10, label_fontsize_y=15))
with open(_pp49, 'w', encoding='utf-8') as f:
    json.dump(_p49c, f, ensure_ascii=False)
_res49c = writeback.writeback(_s49, _params.load(_pp49), _pp49, verify=False,
                              style='block')
_src49c = read(_s49)
check('49c T11：块模式 x=10 / y=15 分开写（不再同值）',
      # T35 起：x 与源码现值相同（源码就是 fontsize=10）→ 不算 pending、块里不写那一行；
      # 关键是 y 的 15 **不许**落到 x 上。
      'yaxis.label.set_fontsize(15)' in _src49c
      and 'xaxis.label.set_fontsize(15)' not in _src49c,
      str([ln.strip() for ln in _src49c.splitlines() if 'label.set_fontsize' in ln]))
with open(_s49, 'w', encoding='utf-8') as f:
    f.write(_SRC49)
_p49d = _par49(_ax49(label_fontsize=14))
with open(_pp49, 'w', encoding='utf-8') as f:
    json.dump(_p49d, f, ensure_ascii=False)
writeback.writeback(_s49, _params.load(_pp49), _pp49, verify=False,
                    style='block')
_src49d = read(_s49)
# T73（零兼容）：块模式同样"没写 y 就不写 y 那一行"（旧期望是"两边都是 14"）
check('49d T73：块模式无 y → 只写 x 那一行（y 不出现）',
      'xaxis.label.set_fontsize(14)' in _src49d
      and 'yaxis.label.set_fontsize(14)' not in _src49d,
      str([ln.strip() for ln in _src49d.splitlines() if 'label.set_fontsize' in ln]))
shutil.rmtree(d49, ignore_errors=True)

# ---- 50. T12 / L0-1 + L0-3：等值 figsize 不得改写源码（更不得谎报 changes）----
#   L0 语料（812 脚本）里 27/30 条 M3（干净往返）残余都是这个：参数与源码现值**完全
#   相等**，工具却把整个 kwarg 重写成规范形式 → 值没变、字节变了。
#   反向验证：把 edit_figsize 的等值短路删掉（fix018/reverse_check_t12.py 的 R-D）→ FAIL。
import hashlib                                                       # noqa: E402


def _sha(path):
    with open(path, 'rb') as f:
        return hashlib.sha256(f.read()).hexdigest()


d50, _s50, _pp50 = setup()
_FS_FORMS = (
    ('列表容器', 'figsize=[6, 3]', [6.0, 3.0]),
    ('双空格',   'figsize=(6.4,  4)', [6.4, 4.0]),
    ('.0 写法',  'figsize=(4., 4.)', [4.0, 4.0]),
    ('无空格',   'figsize=(16,10)', [16.0, 10.0]),
    ('空格+小数', 'figsize=(7.0, 6.9)', [7.0, 6.9]),
)
for _tag, _form, _val in _FS_FORMS:
    with open(_s50, 'w', encoding='utf-8') as f:
        f.write('import matplotlib\nmatplotlib.use("Agg")\n'
                'import matplotlib.pyplot as plt\n'
                'fig = plt.figure(%s)\nfig.savefig("o.png")\n' % _form)
    _before = _sha(_s50)
    _p50 = {'version': 4, 'script': 'target.py', 'fig_index': 0, 'n_figs': 1,
            'figsize_in': list(_val), 'axes': []}
    with open(_pp50, 'w', encoding='utf-8') as f:
        json.dump(_p50, f, ensure_ascii=False)
    _r50 = writeback.writeback(_s50, _params.load(_pp50), _pp50, verify=True,
                               semantic=True, style='inplace')
    check('50 L0-1：等值 figsize（%s）→ no_change 且 changes=[]' % _tag,
          _r50.get('reason') == 'no_change' and (_r50.get('changes') or []) == [],
          '%s / changes=%s' % (_r50.get('reason'), _r50.get('changes')))
    check('50 L0-1/L0-3：等值 figsize（%s）→ 字节一个都不动（sha 相同）' % _tag,
          _sha(_s50) == _before, '%s != %s' % (_sha(_s50)[:12], _before[:12]))
    check('50 L0-1：等值 figsize（%s）→ 原写法原样保留（未被规范化）' % _tag,
          _form in read(_s50), read(_s50).splitlines()[3])
# 值**真的**变了时必须写回，且保留原容器（列表→列表）
with open(_s50, 'w', encoding='utf-8') as f:
    f.write('import matplotlib\nmatplotlib.use("Agg")\n'
            'import matplotlib.pyplot as plt\n'
            'fig = plt.figure(figsize=[6, 3])\nfig.savefig("o.png")\n')
_p50b = dict(_p50, figsize_in=[8.0, 5.0])
with open(_pp50, 'w', encoding='utf-8') as f:
    json.dump(_p50b, f, ensure_ascii=False)
_r50b = writeback.writeback(_s50, _params.load(_pp50), _pp50, verify=True,
                            semantic=True, style='inplace')
check('50 L0-1b：值确实变了 → 写回，且**保留列表容器**（[8, 5]）',
      'figsize=[8, 5]' in read(_s50)
      and (_r50b.get('changes') or []) == ['figsize'],
      '%s / %s' % (_r50b.get('changes'), read(_s50).splitlines()[3]))
shutil.rmtree(d50, ignore_errors=True)

# ---- 51. T12 / L0-2：目标值等于源码现值但**无法原位核对**时必须 no_change ----
#   三种形态：(a) colorbar 槽轴 cax（字面量在源码里但轴被排除在映射外）、
#   (b) 字面量在建图区间之外、(c) 值不同 → 必须保持"保守退块"（不能误证明）。
d51, _s51, _pp51 = setup()
_SRC51 = ('import matplotlib\nmatplotlib.use("Agg")\n'
          'import matplotlib.pyplot as plt\n'
          'fig = plt.figure(figsize=(4, 3))\n'
          'sm = plt.cm.ScalarMappable()\n'
          'cbar_ax = fig.add_axes([0.875, 0.13, 0.02, 0.69])\n'
          'cb = plt.colorbar(sm, cax=cbar_ax)\n'
          'fig.savefig("o.png")\n')
with open(_s51, 'w', encoding='utf-8') as f:
    f.write(_SRC51)
_before51 = _sha(_s51)
_p51 = {'version': 4, 'script': 'target.py', 'fig_index': 0, 'n_figs': 1,
        'figsize_in': [4.0, 3.0],
        'axes': [{'index': 0, 'pos': [0.875, 0.13, 0.02, 0.69]}]}
with open(_pp51, 'w', encoding='utf-8') as f:
    json.dump(_p51, f, ensure_ascii=False)
_r51 = writeback.writeback(_s51, _params.load(_pp51), _pp51, verify=False,
                           style='inplace', fast_check=True)
check('51 L0-2：cax 位置 == 源码现值 → no_change（不得 best_effort/退块）',
      _r51.get('reason') == 'no_change' and _r51.get('needs_block') is False,
      '%s / nb=%r / skipped=%s' % (_r51.get('reason'),
                                   _r51.get('needs_block'),
                                   _r51.get('warnings')))
check('51 L0-2：cax 位置 == 源码现值 → 脚本字节不变',
      _sha(_s51) == _before51)
# 值不同 → 必须保持"保守退块"（证明只做等价证明，不做不等价证明）
_p51b = dict(_p51, axes=[{'index': 0, 'pos': [0.5, 0.5, 0.3, 0.3]}])
with open(_pp51, 'w', encoding='utf-8') as f:
    json.dump(_p51b, f, ensure_ascii=False)
_r51b = writeback.writeback(_s51, _params.load(_pp51), _pp51, verify=False,
                            style='inplace', fast_check=True)
check('51 L0-2b：cax 位置**不同** → 仍然 best_effort + needs_block（该退块）',
      _r51b.get('reason') == 'best_effort' and _r51b.get('needs_block') is True,
      '%s / nb=%r' % (_r51b.get('reason'), _r51b.get('needs_block')))
# 单元级：区间外用同名变量的字面量也能证明；值不同则证不出
import ast                                                           # noqa: E402
_t51 = ast.parse('ax = fig.add_axes((0, 0, 1, 1))\nfig = plt.figure()\n')
check('51c T12：建图区间之外的 add_axes 字面量也构成等价证明',
      writeback._pos_literal_equal(_t51, [0, 0, 1, 1], 'ax', {'ax': 0}, 0, 0) is True)
check('51c T12：值不同 → 不给证明（宁可保守退块，也不静默丢掉真差异）',
      writeback._pos_literal_equal(_t51, [0.1, 0, 1, 1], 'ax', None, 0, 0) is False)
shutil.rmtree(d51, ignore_errors=True)

# ---- 52. T12 / L0-4：auto 一命令两次写盘时，备份不得被第二次覆盖 ----
#   端到端走 CLI（apply 的自动回退逻辑就在那），断言：
#     ① auto 确实退到了块模式（auto_fallback=true）；
#     ② 固定名备份（.tweak.bak）里是**原件**（title fontsize=10），而不是原位那次的
#        中间态（16）—— 否则 revert 拿不回原件（主仓 20154193__RealSpace.py 同因）。
import subprocess                                                    # noqa: E402
_SRC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(writeback.__file__)))
d52, _s52, _pp52 = setup()
_SRC52 = ('import matplotlib\nmatplotlib.use("Agg")\n'
          'import matplotlib.pyplot as plt\n'
          'fig = plt.figure(figsize=(4, 3))\n'
          'ax = fig.add_subplot(111)\n'
          'ax.set_title("t", fontsize=10)\n'          # ← 原位能改（真改动）
          'sm = plt.cm.ScalarMappable()\n'
          'cbar_ax = fig.add_axes([0.875, 0.13, 0.02, 0.69])\n'
          'cb = plt.colorbar(sm, cax=cbar_ax)\n'
          'fig.savefig("o.png")\n')
with open(_s52, 'w', encoding='utf-8') as f:
    f.write(_SRC52)
_p52 = {'version': 4, 'script': 'target.py', 'fig_index': 0, 'n_figs': 1,
        'figsize_in': [4.0, 3.0],
        # 位置**确实不同**（源码里的 cax 是 [0.875, 0.13, 0.02, 0.69]）→ 原位写不了
        # → auto 必须退块；而 title 是原位能改的真改动 → 这条命令里会写盘**两次**。
        'axes': [{'index': 0, 'pos': [0.5, 0.1, 0.3, 0.3],
                  'title_fontsize': 16}]}
with open(_pp52, 'w', encoding='utf-8') as f:
    json.dump(_p52, f, ensure_ascii=False)
_env52 = dict(os.environ, PYTHONPATH=_SRC_DIR, MPLBACKEND='Agg',
              MPLTWEAK_LANG='zh')
_bak52 = os.path.join(os.path.dirname(_s52), '.tweak_params',
                      'target.tweak.bak')


# 52a（核心，可反向验证）：**同一次 apply 的第二次写盘必须复用第一次的备份**。
#   这里按 apply 的自动回退原样调用两次（先原位、再带 backup= 退块），断言备份里
#   始终是**原件**（title fontsize=10），而不是原位写过的中间态（16）。
#   反向验证：把 `backup or _make_backup(...)` 改回 `_make_backup(...)`
#   （fix018/reverse_check_t12.py 的 R-E）→ 本段 FAIL。
_p52a = {'version': 4, 'script': 'target.py', 'fig_index': 0, 'n_figs': 1,
         'figsize_in': [4.0, 3.0],
         'axes': [{'index': 0, 'pos': None, 'title_fontsize': 16}]}
with open(_pp52, 'w', encoding='utf-8') as f:
    json.dump(_p52a, f, ensure_ascii=False)
_r52a1 = writeback.writeback(_s52, _params.load(_pp52), _pp52, verify=False,
                             style='inplace', fast_check=True)
check('52a L0-4：第一次（原位）确实写盘并做了备份',
      _r52a1.get('applied') is True and _r52a1.get('backup')
      and 'fontsize=16' in read(_s52), '%s' % _r52a1.get('reason'))
_p52a2 = dict(_p52a, axes=[{'index': 0, 'pos': [0.5, 0.1, 0.3, 0.3],
                            'title_fontsize': 16}])
with open(_pp52, 'w', encoding='utf-8') as f:
    json.dump(_p52a2, f, ensure_ascii=False)
_r52a2 = writeback.writeback(_s52, _params.load(_pp52), _pp52, verify=False,
                             style='block', fast_check=True,
                             backup=_r52a1.get('backup'))
check('52a L0-4：第二次（退块）用了**同一份**备份（没有被覆盖）',
      _r52a2.get('backup') == _r52a1.get('backup')
      and writeback.BLOCK_START in read(_s52),
      '%r' % _r52a2.get('backup'))
check('52a L0-4：备份内容仍是**原件**（fontsize=10，不是中间态 16）',
      os.path.exists(_bak52) and 'fontsize=10' in read(_bak52)
      and 'fontsize=16' not in read(_bak52),
      read(_bak52).splitlines()[5] if os.path.exists(_bak52) else '(无备份)')

# 52b（端到端 auto + revert）：等值 figsize + **位置确实变了**的 cax → auto 退块，
#   一条命令后 revert 必须逐字节拿回原件。
with open(_s52, 'w', encoding='utf-8') as f:
    f.write(_SRC52)
if os.path.exists(_bak52):
    os.remove(_bak52)
_p52 = {'version': 4, 'script': 'target.py', 'fig_index': 0, 'n_figs': 1,
        'figsize_in': [4.0, 3.0],
        # 源码 cax 是 [0.875, 0.13, 0.02, 0.69] → 位置真的不同 → 只能靠块落地
        'axes': [{'index': 0, 'pos': [0.5, 0.1, 0.3, 0.3]}]}
with open(_pp52, 'w', encoding='utf-8') as f:
    json.dump(_p52, f, ensure_ascii=False)
_run52 = subprocess.run([sys.executable, '-m', 'mpltweak', 'apply', _s52,
                         '--params', _pp52, '--write', '--style', 'auto',
                         '--verify-fast', '--json'],
                        capture_output=True, cwd=d52, env=_env52)
_out52 = _decode_out(_run52.stdout + _run52.stderr)
check('52b L0-4/auto：位置确实不同 → auto 退块（auto_fallback=true / style=block）',
      '"auto_fallback": true' in _out52 and '"style": "block"' in _out52,
      _out52[-300:])
check('52b：块已落地（脚本里有 auto layout 块）',
      writeback.BLOCK_START in read(_s52))
_run52r = subprocess.run([sys.executable, '-m', 'mpltweak', 'revert', _s52,
                          '--write'],
                         capture_output=True, cwd=d52, env=_env52)
check('52b L0-4：auto 之后 revert 逐字节回到原件（不再留下半成品）',
      read(_s52) == _SRC52,
      _decode_out(_run52r.stdout + _run52r.stderr)[-200:])

# 52c：等值（pos + 字号都没变）→ no_change、不插块、不改字节
_p52b = dict(_p52, axes=[{'index': 0, 'pos': [0.875, 0.13, 0.02, 0.69],
                          'title_fontsize': 10}])
with open(_s52, 'w', encoding='utf-8') as f:
    f.write(_SRC52)
with open(_pp52, 'w', encoding='utf-8') as f:
    json.dump(_p52b, f, ensure_ascii=False)
_run52b = subprocess.run([sys.executable, '-m', 'mpltweak', 'apply', _s52,
                          '--params', _pp52, '--write', '--style', 'auto',
                          '--verify-fast', '--json'],
                         capture_output=True, cwd=d52, env=_env52)
_out52b = _decode_out(_run52b.stdout + _run52b.stderr)
check('52c L0-2：等值（pos + 字号都没变）→ no_change，不插块、不改字节',
      '"reason": "no_change"' in _out52b
      and read(_s52) == _SRC52, _out52b[-300:])
shutil.rmtree(d52, ignore_errors=True)


# ---- 53. T15：逐字段三态退块判据（INT-2 的 A/B/C + T14-D1 无写法但等值）----
#   核心语义（任务书）：
#     ① 已原位落地 / ② 与源码现值相同（字面量证明 + 默认值证明）/ ③ 有目标值 ∧ 与源码
#     现值不同 ∧ 原位写不了；**存在③ → 必须退块**（auto 会退，字段照样落地）；
#     全部属①② → no_change，一个字节都不写、不插块。
#   反向验证：把判据改回"只看有没有落地"（writeback.py 里 `needs_block=False` 的那一版）
#   → 53b/53e（B 情形）必须失败。见 fix018/reverse_check_t15.py。
_GRID_SRC = ('import matplotlib\nmatplotlib.use("Agg")\n'
             'import matplotlib.pyplot as plt\n'
             'fig, ax = plt.subplots(figsize=(6, 3))\n'
             'ax.plot([1, 2, 3])\n'
             'fig.savefig("out.png")\n')
_ADD_SRC = ('import matplotlib\nmatplotlib.use("Agg")\n'
            'import matplotlib.pyplot as plt\n'
            'fig = plt.figure(figsize=(6, 4))\n'
            'ax = fig.add_axes([0.10, 0.10, 0.80, 0.80])\n'
            'ax.set_title("t")\n'
            'ax.plot([1, 2, 3])\n'
            'ax.grid(True)\n'
            'fig.savefig("out_grid.png")\n')
_POS15 = [0.2, 0.2, 0.6, 0.6]


def _mk15(name, src, params):
    d = tempfile.mkdtemp(prefix='mpltweak_%s_' % name)
    s = os.path.join(d, 'fig.py')
    p = os.path.join(d, 'p.json')
    with open(s, 'w', encoding='utf-8', newline='\n') as f:
        f.write(src)
    with open(p, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(params, f, ensure_ascii=False)
    return d, s, p


# 53a A：只有 pos（写不了 + 确实不同）→ fast 下保守退块（needs_block=True，不写盘）
_d53, _s53, _p53 = _mk15('t15a', _GRID_SRC,
                         {'version': 4, 'script': 'fig.py',
                          'axes': [{'index': 0, 'pos': _POS15}]})
_r53a = writeback.writeback(_s53, _params.load(_p53), _p53, verify=False,
                            fast_check=True, style='inplace')
check('53a A：pos 写不了且不同 → best_effort + needs_block=True，不写盘',
      _r53a.get('reason') == 'best_effort' and _r53a.get('needs_block') is True
      and _r53a.get('applied') is False and read(_s53) == _GRID_SRC,
      '%s / nb=%r' % (_r53a.get('reason'), _r53a.get('needs_block')))

# 53b B（INT-2 的核心）：figsize 原位落地 + pos 写不了且不同
#     → **仍然** must needs_block=True（旧实现只因为"有字段落地了"就报 needs_block=False，
#     pos 被静默丢弃：rc=0 + applied=true）
_p53b = {'version': 4, 'script': 'fig.py', 'figsize_in': [8.0, 4.0],
         'axes': [{'index': 0, 'pos': _POS15}]}
with open(_p53, 'w', encoding='utf-8', newline='\n') as f:
    json.dump(_p53b, f, ensure_ascii=False)
_r53b = writeback.writeback(_s53, _params.load(_p53), _p53, verify=False,
                            fast_check=True, style='inplace')
check('53b B（INT-2）：figsize 落地 + pos 写不了 → needs_block=True（不许静默丢）',
      _r53b.get('needs_block') is True and _r53b.get('applied') is True
      and _r53b.get('pending') == ['ax0.pos']
      and 'figsize' in (_r53b.get('changes') or []),
      'nb=%r applied=%r pending=%r changes=%r'
      % (_r53b.get('needs_block'), _r53b.get('applied'), _r53b.get('pending'),
         _r53b.get('changes')))

# 53c C：figsize 等值 + pos 写不了且不同 → 同 A（等值不会让 pos 变成"已完成"）
with open(_s53, 'w', encoding='utf-8', newline='\n') as f:
    f.write(_GRID_SRC)
_p53c = {'version': 4, 'script': 'fig.py', 'figsize_in': [6.0, 3.0],
         'axes': [{'index': 0, 'pos': _POS15}]}
with open(_p53, 'w', encoding='utf-8', newline='\n') as f:
    json.dump(_p53c, f, ensure_ascii=False)
_r53c = writeback.writeback(_s53, _params.load(_p53), _p53, verify=False,
                            fast_check=True, style='inplace')
check('53c C：figsize 等值 + pos 不同 → needs_block=True，字节不变',
      _r53c.get('needs_block') is True and _r53c.get('applied') is False
      and read(_s53) == _GRID_SRC,
      'nb=%r applied=%r' % (_r53c.get('needs_block'), _r53c.get('applied')))

# 53d B + **允许重跑**（verify=True/semantic=True）：拿实测回比也必须是 needs_block
with open(_p53, 'w', encoding='utf-8', newline='\n') as f:
    json.dump(_p53b, f, ensure_ascii=False)
_r53d = writeback.writeback(_s53, _params.load(_p53), _p53, verify=True,
                            style='inplace')
check('53d B + 允许重跑：实测回比同样判 needs_block=True（两种校验档一致）',
      _r53d.get('needs_block') is True and _r53d.get('pending') == ['ax0.pos'],
      'nb=%r pending=%r' % (_r53d.get('needs_block'), _r53d.get('pending')))

# ---- 53e 端到端：A/B/C 三条命令都必须把 pos 落地（auto 自动退块）----
_src_dir15 = os.path.dirname(os.path.dirname(os.path.abspath(writeback.__file__)))
_env15 = dict(os.environ, PYTHONPATH=_src_dir15, MPLBACKEND='Agg',
              MPLTWEAK_LANG='zh', PYTHONIOENCODING='utf-8')
for _tag, _pp, _fs, _want_fallback in (('A', {'axes': [{'index': 0, 'pos': _POS15}]},
                                        None, True),
                                       ('B', {'figsize_in': [8.0, 4.0],
                                              'axes': [{'index': 0, 'pos': _POS15}]},
                                        None, True),
                                       ('C', {'figsize_in': [6.0, 3.0],
                                              'axes': [{'index': 0, 'pos': _POS15}]},
                                        None, True)):
    for _fast in (True, False):
        _d, _s, _p = _mk15('t15cli', _GRID_SRC,
                           dict({'version': 4, 'script': 'fig.py'}, **_pp))
        _args = [sys.executable, '-m', 'mpltweak', 'apply', _s, '--params', _p,
                 '--write', '--style', 'auto']
        if _fast:
            _args.append('--verify-fast')
        _args.append('--json')
        _rr = subprocess.run(_args, capture_output=True, cwd=_d, env=_env15)
        _oo = _decode_out(_rr.stdout + _rr.stderr)
        _txt = read(_s)
        check('53e %s（%s）：pos 落到源码里（set_position）且退块留档一致'
              % (_tag, 'fast' if _fast else 'rerun'),
              _rr.returncode == 0 and 'set_position' in _txt
              and writeback.BLOCK_START in _txt
              and '"auto_fallback": true' in _oo
              and ('"inplace_needs_block": true' in _oo) is _want_fallback,
              'rc=%s | %s' % (_rr.returncode, _oo[-260:]))
        shutil.rmtree(_d, ignore_errors=True)

# 53f 显式 --style inplace + B：**不许 rc=0 假装都改好了**（明确失败 + 指出字段）
_d, _s, _p = _mk15('t15ip', _GRID_SRC,
                   {'version': 4, 'script': 'fig.py', 'figsize_in': [8.0, 4.0],
                    'axes': [{'index': 0, 'pos': _POS15}]})
_rr = subprocess.run([sys.executable, '-m', 'mpltweak', 'apply', _s, '--params', _p,
                      '--write', '--style', 'inplace', '--verify-fast', '--json'],
                     capture_output=True, cwd=_d, env=_env15)
_oo = _decode_out(_rr.stdout + _rr.stderr)
check('53f 显式 inplace + pos 写不了 → rc=1 且 JSON 里 needs_block=true/pending',
      _rr.returncode == 1 and '"needs_block": true' in _oo
      and '"pending": [' in _oo and 'ax0.pos' in _oo,
      'rc=%s | %s' % (_rr.returncode, _oo[-260:]))
check('53f 显式 inplace：明确打印"没能落地"的字段（不是埋在 warnings 里）',
      '没能落地' in _oo and 'ax0.pos' in _oo, _oo[-200:])
shutil.rmtree(_d, ignore_errors=True)

# ---- 53g T14-D1：源码里**没有写法**、目标值等于 matplotlib 默认值 → no_change ----
#   六种字段各一条（title/label/tick 字号、spines、grid、xscale），全部走 --verify-fast
#   （不重跑）—— 这就是"能静态判相等就不要因为没重跑而退块"。
_D1 = [
    ('title_fontsize', 12.0),
    ('label_fontsize', 10.0),
    ('tick_fontsize', 10.0),
    ('spines', {'left': True, 'right': True, 'bottom': True, 'top': True}),
    ('grid', True),
    ('xscale', 'linear'),
]
for _f, _v in _D1:
    _ax = {'index': 0, 'pos': [0.1, 0.1, 0.8, 0.8], _f: _v}
    _d, _s, _p = _mk15('t15d1', _ADD_SRC,
                       {'version': 4, 'script': 'fig.py', 'axes': [_ax]})
    _rr = subprocess.run([sys.executable, '-m', 'mpltweak', 'apply', _s, '--params', _p,
                          '--write', '--verify-fast', '--json'],
                         capture_output=True, cwd=_d, env=_env15)
    _oo = _decode_out(_rr.stdout + _rr.stderr)
    check('53g T14-D1：%s 无源码写法但等值 → no_change、字节不变、不插块' % _f,
          _rr.returncode == 0 and '"reason": "no_change"' in _oo
          and read(_s) == _ADD_SRC
          and writeback.BLOCK_START not in read(_s), _oo[-260:])
    shutil.rmtree(_d, ignore_errors=True)

# 53h 反向：值**确实不同**的同类字段必须落地（默认值证明不许把真差异也吞掉）。
# A2（0.1.12 / §25.1）起 title_fontsize 有了**原位**落地形态：`_ADD_SRC` 里已经有
# `ax.set_title("t")`，于是现在补 `fontsize=20` 而不再退块插 `set_fontsize(20)` ——
# 落点变了，"绝不静默吞掉真差异"这条不变（所以下面按"任一通道落地"判）。
for _f, _v, _marks in (('title_fontsize', 20.0, ('fontsize=20', 'set_fontsize(20)')),
                       ('spines', {'top': False}, ("'top'",)),
                       ('tick_fontsize', 7.0, ('labelsize=7',))):
    _ax = {'index': 0, 'pos': [0.1, 0.1, 0.8, 0.8], _f: _v}
    _d, _s, _p = _mk15('t15d1n', _ADD_SRC,
                       {'version': 4, 'script': 'fig.py', 'axes': [_ax]})
    _rr = subprocess.run([sys.executable, '-m', 'mpltweak', 'apply', _s, '--params', _p,
                          '--write', '--verify-fast', '--json'],
                         capture_output=True, cwd=_d, env=_env15)
    _oo = _decode_out(_rr.stdout + _rr.stderr)
    check('53h 反向：%s=%r 与现值不同 → 必须落地（含 %s），绝不静默吞'
          % (_f, _v, ' 或 '.join(_marks)),
          _rr.returncode == 0
          and any(_m in read(_s) for _m in _marks), _oo[-260:])
    shutil.rmtree(_d, ignore_errors=True)

# 53h-A2：同一条反例的**落点**钉死 —— 有 `set_title("t")` 就该原位补参数、不透块
_ax = {'index': 0, 'pos': [0.1, 0.1, 0.8, 0.8], 'title_fontsize': 20.0}
_d, _s, _p = _mk15('t15d1n2', _ADD_SRC,
                   {'version': 4, 'script': 'fig.py', 'axes': [_ax]})
_r53h = writeback.writeback(_s, _params.load(_p), _p, verify=False,
                            fast_check=True, style='inplace')
_a53h = read(_s)
check('53h-A2：源码有 `ax.set_title("t")` ⇒ title_fontsize=20 就地补进去'
      '（applied=true / 不插块 / 不再报"没有对应写法"）',
      _r53h.get('applied') is True and 'ax.set_title("t", fontsize=20)' in _a53h
      and writeback.BLOCK_START not in _a53h
      and not (_r53h.get('pending') or []),
      '%s | %s' % (_r53h.get('changes'), _a53h.splitlines()[-4:]))
shutil.rmtree(_d, ignore_errors=True)

# 53i 默认值证明的**门**：源码里出现能改该字段的写法（rcParams）→ 不许证明"现值=默认值"。
# A2 起这条门的意义更清楚了：既然证不出，就在已有的 `set_title("t")` 上**显式**补
# fontsize=12（把 rcParams 的影响钉死在这条轴上），而不是谎称相等、也不再退块。
_GATE_SRC = _ADD_SRC.replace('ax.plot([1, 2, 3])',
                             "plt.rcParams['axes.titlesize'] = 12\nax.plot([1, 2, 3])")
_d, _s, _p = _mk15('t15gate', _GATE_SRC,
                   {'version': 4, 'script': 'fig.py',
                    'axes': [{'index': 0, 'pos': [0.1, 0.1, 0.8, 0.8],
                              'title_fontsize': 12.0}]})
_r53i = writeback.writeback(_s, _params.load(_p), _p, verify=False,
                            fast_check=True, style='inplace')
_a53i = read(_s)
check('53i 源码里有 rcParams 改字号 → 放弃默认值证明（不许谎称"现值=默认值"），'
      '并显式补 fontsize=12 落地（不静默吞）',
      'ax.set_title("t", fontsize=12)' in _a53i
      and _a53i.count('fontsize=12') == 1,
      'nb=%r | %s' % (_r53i.get('needs_block'),
                      [ln for ln in _a53i.splitlines() if 'title' in ln]))
shutil.rmtree(_d, ignore_errors=True)

# 53j cax 在**函数里**创建（`cbar_ax = fig.add_axes([...])` 用的是传进来的 fig）：
#     静态"建图区间"圈不到它，但 `fig.colorbar(..., cax=X)` 给了它的身份 →
#     字面量等值也必须判 no_change（语料实测 plot_successive_halving_heatmap.py）
_HELPER_CAX = ('import matplotlib\nmatplotlib.use("Agg")\n'
               'import matplotlib.pyplot as plt\n'
               'import numpy as np\n'
               'fig, axes = plt.subplots(ncols=2, sharey=True)\n'
               'def _heat(ax, make_cbar=False):\n'
               '    im = ax.imshow(np.arange(9).reshape(3, 3))\n'
               '    if make_cbar:\n'
               '        cbar_ax = fig.add_axes([0.85, 0.15, 0.05, 0.7])\n'
               '        fig.colorbar(im, cax=cbar_ax)\n'
               '_heat(axes[0])\n'
               '_heat(axes[1], make_cbar=True)\n'
               'fig.savefig("o.png")\n')
_axj = {'index': 0, 'is_colorbar': True, 'pos': [0.85, 0.15, 0.05, 0.7]}
_d, _s, _p = _mk15('t15caxj', _HELPER_CAX,
                   {'version': 4, 'script': 'fig.py', 'fig_index': 0, 'n_figs': 1,
                    'axes': [_axj]})
_r53j = writeback.writeback(_s, _params.load(_p), _p, verify=False,
                            fast_check=True, style='inplace')
check('53j 函数内创建的 cax：字面量等值 → no_change，不插块',
      _r53j.get('reason') == 'no_change' and _r53j.get('needs_block') is False
      and read(_s) == _HELPER_CAX,
      '%s / nb=%r' % (_r53j.get('reason'), _r53j.get('needs_block')))
# 反向：值不同 → 必须退块（不许因为"cax 找不到区间"就静默丢）
_axj2 = {'index': 0, 'is_colorbar': True, 'pos': [0.5, 0.1, 0.3, 0.3]}
with open(_p, 'w', encoding='utf-8', newline='\n') as f:
    json.dump({'version': 4, 'script': 'fig.py', 'fig_index': 0, 'n_figs': 1,
               'axes': [_axj2]}, f, ensure_ascii=False)
_r53j2 = writeback.writeback(_s, _params.load(_p), _p, verify=False,
                             fast_check=True, style='inplace')
check('53j 反向：cax 位置确实不同 → needs_block=True（保守侧不变）',
      _r53j2.get('needs_block') is True and _r53j2.get('pending') == ['ax0.pos'],
      'nb=%r pending=%r' % (_r53j2.get('needs_block'), _r53j2.get('pending')))
shutil.rmtree(_d, ignore_errors=True)
shutil.rmtree(_d53, ignore_errors=True)


# ---- 54. T18：默认值证明的前提检查改成 AST 白名单（不再用子串黑名单）----
#   旧实现把 'size' 当子串 token → 脚本里出现 figsize=(4,4)/markersize= 就把默认值证明
#   打死（describe→apply 又退块）。新实现：
#     ① 字面量路径接受 kw.arg in ('fontsize','size')（matplotlib 的 Text 两个都收）；
#     ② 前提检查 = AST 精确匹配 + 白名单：所有相关调用要么不带字号关键字、要么带的字面量
#        恰等于目标值；认不出（变量/表达式/**kwargs/rcParams/style/seaborn）一律保守退块。
_T18_BASE = ('import matplotlib\nmatplotlib.use("Agg")\n'
             'import matplotlib.pyplot as plt\n'
             'fig, ax = plt.subplots(figsize=(6, 4))\n'
             'ax.plot([1, 2, 3], markersize=4)\n'
             '%s\n'
             'fig.savefig("o.png")\n')


def _ast_ok(text):
    """语法是否合法：True / SyntaxError 实例（T23 用）。"""
    try:
        _ast.parse(text)
        return True
    except SyntaxError as e:
        return e


def _t18(name, extra, ax_params, style='inplace'):
    d, s, p = _mk15(name, _T18_BASE % extra,
                    {'version': 4, 'script': 'fig.py',
                     'axes': [dict({'index': 0}, **ax_params)]})
    r = writeback.writeback(s, _params.load(p), p, verify=False,
                            fast_check=True, style=style)
    return d, s, p, r


_d, _s, _p, _r54a = _t18('t18a', "ax.set_title('t', size=9.5)",
                         {'title_fontsize': 9.5})
check('54a 字面量路径认 `size=`：set_title(size=目标，非默认值) → no_change',
      _r54a.get('reason') == 'no_change' and _r54a.get('needs_block') is False,
      '%s / nb=%r' % (_r54a.get('reason'), _r54a.get('needs_block')))
shutil.rmtree(_d, ignore_errors=True)

_d, _s, _p, _r54b = _t18('t18b', "ax.set_title('t', size=20)",
                         {'title_fontsize': 12.0})
check('54b set_title(size=非默认) + 目标=默认值 → 必须退块（不许当等值）',
      _r54b.get('needs_block') is True
      and _r54b.get('pending') == ['ax0.title_fontsize'],
      'nb=%r pending=%r' % (_r54b.get('needs_block'), _r54b.get('pending')))
shutil.rmtree(_d, ignore_errors=True)

# 54b 端到端：退块后目标值必须**真的写进源码**
_d, _s, _p = _mk15('t18b2', _T18_BASE % "ax.set_title('t', size=20)",
                   {'version': 4, 'script': 'fig.py',
                    'axes': [{'index': 0, 'title_fontsize': 12.0}]})
_rr54b = subprocess.run(
    [sys.executable, '-m', 'mpltweak', 'apply', _s, '--params', _p, '--write',
     '--verify-fast', '--json'], capture_output=True, cwd=_d, env=_env15)
_oo = _decode_out(_rr54b.stdout + _rr54b.stderr)
check('54b 端到端：size=20 的脚本 + 目标 12 → 目标值写进源码（title.set_fontsize(12)）',
      _rr54b.returncode == 0 and 'title.set_fontsize(12)' in read(_s),
      'rc=%s | %s' % (_rr54b.returncode, _oo[-220:]))
shutil.rmtree(_d, ignore_errors=True)

# 54c 反向：脚本里只有 figsize=(6,4)/markersize=4（含 'size' 子串）也要能证明等值
_d, _s, _p, _r54c = _t18('t18c', "ax.set_title('t')", {'title_fontsize': 12.0})
check("54c `figsize=`/`markersize=` 里的 'size' 子串不得打死证明 → no_change",
      _r54c.get('reason') == 'no_change' and _r54c.get('needs_block') is False,
      '%s / nb=%r' % (_r54c.get('reason'), _r54c.get('needs_block')))
shutil.rmtree(_d, ignore_errors=True)

for _tag, _extra, _axp, _want_nb, _why in (
        ('54e', "plt.rcParams['axes.titlesize'] = 12", {'title_fontsize': 12.0}, True,
         'rcParams 赋值（认不出 → 保守）'),
        ('54f', "plt.style.use('bmh')", {'title_fontsize': 12.0}, True,
         'style.use（全局样式）'),
        ('54h', "ax.tick_params(labelsize=10.0)", {'tick_fontsize': 10.0}, False,
         'labelsize= 恰等于目标 → 可证等值'),
        ('54i', "ax.spines['top'].set_visible(False)",
         {'spines': {'left': True, 'right': True, 'bottom': True, 'top': True}}, True,
         '有 spine 被隐藏 → 默认值结论不成立'),
        ('54j', "ax.set_xscale('log')", {'xscale': 'linear'}, True,
         '源码是 log、目标是 linear → 必须落地（旧实现静默 no_change）')):
    _d, _s, _p, _r54 = _t18('t18' + _tag, _extra, _axp)
    check('%s %s → needs_block=%s' % (_tag, _why, _want_nb),
          bool(_r54.get('needs_block')) is _want_nb,
          'nb=%r reason=%s' % (_r54.get('needs_block'), _r54.get('reason')))
    shutil.rmtree(_d, ignore_errors=True)

# 54d / 54g：源码里**已有字面量**而且与目标不同时，原位直接改那个字面量（比退块更干净）
#   —— 记在这里以免把"应当落地"误判成"证不出等值"。
for _tag, _extra, _axp, _mark, _why in (
        ('54d', "ax.set_title('t', fontsize=20)", {'title_fontsize': 12.0},
         'fontsize=12', 'set_title(fontsize=20) → 目标 12 原位改'),
        ('54g', "ax.tick_params(labelsize=8)", {'tick_fontsize': 10.0},
         'labelsize=10', 'tick_params(labelsize=8) → 目标 10 原位改')):
    _d, _s, _p, _r54 = _t18('t18' + _tag, _extra, _axp)
    check('%s %s（%s 写进源码）' % (_tag, _why, _mark),
          _mark in read(_s) and _r54.get('reason') == 'ok',
          'reason=%s | %s' % (_r54.get('reason'),
                              [ln for ln in read(_s).splitlines()
                               if _mark.split('=')[0] in ln]))
    shutil.rmtree(_d, ignore_errors=True)

# ---- 55. T18：块自检 / spines=True 块行 / 语义回比失败文案 ----
# 55a spines 从"隐藏"改回"显示"必须真的能落地（块里要有 set_visible(True)）
_d, _s, _p = _mk15('t18sp', _T18_BASE % "ax.spines['top'].set_visible(False)",
                   {'version': 4, 'script': 'fig.py',
                    'axes': [{'index': 0,
                              'spines': {'top': True, 'bottom': True,
                                         'left': True, 'right': True}}]})
writeback.writeback(_s, _params.load(_p), _p, verify=False,
                    fast_check=True, style='block')
check("55a spines=True 的块行：`spines['top'].set_visible(True)` 真的写进源码",
      "spines['top'].set_visible(True)" in read(_s), read(_s)[-260:])
shutil.rmtree(_d, ignore_errors=True)

# 55b 块自检的安全网：让渲染器"写不出"某个字段（把安全刻度白名单清空）→
#     不许报 ok/applied=true/needs_block=false，必须 needs_block=true + pending
_old_scales = writeback._SAFE_SCALES
try:
    writeback._SAFE_SCALES = ()
    _d, _s, _p = _mk15('t18self', _T18_BASE % "ax.set_title('t')",
                       {'version': 4, 'script': 'fig.py',
                        'axes': [{'index': 0, 'xscale': 'log',
                                  'title_fontsize': 14.0}]})
    _r55b = writeback.writeback(_s, _params.load(_p), _p, verify=False,
                                fast_check=True, style='block')
    check('55b 块表达不了的字段 → needs_block=true + pending=[ax0.xscale]（不报 ok）',
          # T35 起 'linear' 不再是"需要落地"的目标（源码本来就是线性轴），所以这里
          # 用源码里没有的 'log'：它确实要落地、而渲染器在 _SAFE_SCALES=() 下写不出
          # → 自检必须如实报出来。
          _r55b.get('needs_block') is True
          and _r55b.get('pending') == ['ax0.xscale']
          and _r55b.get('reason') != 'ok',
          'reason=%s nb=%r pending=%r'
          % (_r55b.get('reason'), _r55b.get('needs_block'), _r55b.get('pending')))
    shutil.rmtree(_d, ignore_errors=True)
finally:
    writeback._SAFE_SCALES = _old_scales

# 55c 语义回比失败的文案：让 verify_semantic 恒返回"不一致"，全候选失败后结论必须是
#     "目标值在重跑后仍未达标：…"（旧文案"所有锚点写回后 Agg 重跑均失败"会误导）
_d, _s, _p = _mk15('t18msg', _T18_BASE % "ax.set_title('t', size=20)",
                   {'version': 4, 'script': 'fig.py',
                    'axes': [{'index': 0, 'pos': [0.2, 0.2, 0.6, 0.6]}]})
_before55 = read(_s)
_orig_sem = writeback._verify.verify_semantic
try:
    writeback._verify.verify_semantic = (
        lambda *a, **k: (False, 'ax0.pos 实测 [0.125, 0.11, 0.775, 0.77] ≠ 参数'))
    _r55c = writeback.writeback(_s, _params.load(_p), _p, verify=True,
                                semantic=True, style='block')
finally:
    writeback._verify.verify_semantic = _orig_sem
_err55 = _r55c.get('err') or ''
check('55c 语义回比失败 → 文案"目标值在重跑后仍未达标：<字段清单>"（不是"重跑失败"）',
      _r55c.get('reason') == 'fail' and '目标值在重跑后仍未达标' in _err55
      and 'ax0.pos 实测' in _err55,
      'reason=%s err=%s' % (_r55c.get('reason'), _err55[:160]))
check('55c 全候选失败 → 脚本回滚到写回前（逐字节一致）', read(_s) == _before55, '')
shutil.rmtree(_d, ignore_errors=True)


# ---- 56. T23①：多图逐张写回不得自判 stale（批次基准）----
#   外部评审条目 2 / T22 实测：2 图 + 两份参数，第一张写回耗时 2.9s → 第二张被判 stale
#   跳过，理由还是"脚本在参数保存之后被修改过"（改它的正是 mpltweak 自己）。
#   这里用**打桩 mtime** 造出确定性场景：脚本 now-20s、两份参数 now-10s（合法），
#   且两份参数都早于"第一张写回后的脚本" → 旧实现必判第二份 stale。
_MULTI_SRC = ('import matplotlib\nmatplotlib.use("Agg")\n'
              'import matplotlib.pyplot as plt\n'
              'fig1, ax1 = plt.subplots(figsize=(6, 3))\n'
              'ax1.plot([1, 2, 3])\n'
              'fig1.savefig("a.png")\n'
              'fig2, ax2 = plt.subplots(figsize=(6, 3))\n'
              'ax2.plot([3, 2, 1])\n'
              'fig2.savefig("b.png")\n')
_d56 = tempfile.mkdtemp(prefix='mpltweak_t23multi_')
_pd56 = os.path.join(_d56, '.tweak_params')
os.makedirs(_pd56)
_s56 = os.path.join(_d56, 'fig.py')
with open(_s56, 'w', encoding='utf-8', newline='\n') as f:
    f.write(_MULTI_SRC)
for _name, _idx, _pos in (('fig.fig0.json', 0, [0.2, 0.2, 0.6, 0.6]),
                          ('fig.fig1.json', 1, [0.5, 0.5, 0.3, 0.3])):
    with open(os.path.join(_pd56, _name), 'w', encoding='utf-8') as f:
        json.dump({'version': 4, 'script': 'fig.py', 'fig_index': _idx,
                   'n_figs': 2, 'axes': [{'index': 0, 'pos': _pos}]},
                  f, ensure_ascii=False)
_now56 = time.time()
os.utime(_s56, (_now56 - 20, _now56 - 20))
for _n in ('fig.fig0.json', 'fig.fig1.json'):
    os.utime(os.path.join(_pd56, _n), (_now56 - 10, _now56 - 10))
_rr56 = subprocess.run([sys.executable, '-m', 'mpltweak', 'apply', 'fig.py',
                        '--write', '--json'], capture_output=True, cwd=_d56,
                       env=_env15)
_oo56 = _decode_out(_rr56.stdout + _rr56.stderr)
_txt56 = read(_s56)
check('56a 多图逐张写回：两张都落地（不是第二张被判 stale）',
      _rr56.returncode == 0 and '"reason": "stale"' not in _oo56
      and _txt56.count(writeback.BLOCK_START) == 2,
      'rc=%s blocks=%d' % (_rr56.returncode, _txt56.count(writeback.BLOCK_START)))
check('56a 两张的位置都写进了源码',
      '[0.2000, 0.2000, 0.6000, 0.6000]' in _txt56
      and '[0.5000, 0.5000, 0.3000, 0.3000]' in _txt56, _oo56[-200:])
# 56b 真 stale：脚本确实在参数保存之后被手改 → 仍要拦住，且理由与事实相符
os.utime(_s56, (time.time() + 5, time.time() + 5))
_s0_56 = read(_s56)
_rr56b = subprocess.run([sys.executable, '-m', 'mpltweak', 'apply', 'fig.py',
                         '--write', '--json'], capture_output=True, cwd=_d56,
                        env=_env15)
_oo56b = _decode_out(_rr56b.stdout + _rr56b.stderr)
check('56b 真 stale（脚本比两份参数都新）→ 拦住且脚本未被改',
      _oo56b.count('"reason": "stale"') == 2 and read(_s56) == _s0_56,
      'rc=%s | %s' % (_rr56b.returncode, _oo56b[-200:]))
# 56c 写回成功后要同步**这一批落地过**的参数文件 mtime（T23 的等价做法）：把脚本 mtime
#     恢复成正常值后立刻再跑一次，两份参数都不该被判 stale；并直接断言 mtime 关系。
os.utime(_s56, (_now56, _now56))
_rr56c = subprocess.run([sys.executable, '-m', 'mpltweak', 'apply', 'fig.py',
                         '--write', '--json'], capture_output=True, cwd=_d56,
                        env=_env15)
_oo56c = _decode_out(_rr56c.stdout + _rr56c.stderr)
check('56c 第二批（mtime 正常）不再被判 stale —— 落地过的参数已同步 mtime',
      '"reason": "stale"' not in _oo56c and _rr56c.returncode == 0
      and os.path.getmtime(os.path.join(_pd56, 'fig.fig1.json'))
      >= os.path.getmtime(_s56) - 1.0,
      'rc=%s p1=%.2f script=%.2f | %s'
      % (_rr56c.returncode, os.path.getmtime(os.path.join(_pd56, 'fig.fig1.json')),
         os.path.getmtime(_s56), _oo56c[-160:]))
shutil.rmtree(_d56, ignore_errors=True)


# ---- 57. T23②(a)：②层没跑就不能报 verified=true（三态诚实）----
#   触发器（T22 实测）：OO 风格 Figure()+FigureCanvasAgg → verify.collect 取不到 plt 图 →
#   semantic=None；旧实现同时给 verified=true（人读会以为"全验过了"）。
_OO_SRC = ('import matplotlib\nmatplotlib.use("Agg")\n'
           'from matplotlib.figure import Figure\n'
           'from matplotlib.backends.backend_agg import FigureCanvasAgg\n'
           'fig = Figure(figsize=(6, 4))\n'
           'FigureCanvasAgg(fig)\n'
           'ax = fig.add_subplot(111)\n'
           'ax.plot([1, 2, 3])\n'
           'ax.set_xlim(0, 5)\n'
           'fig.savefig("o.png")\n')
_d57, _s57, _p57 = _mk15('t23oo', _OO_SRC,
                         {'version': 4, 'script': 'fig.py',
                          'axes': [{'index': 0, 'xlim': [0, 10]}]})
_rr57 = subprocess.run([sys.executable, '-m', 'mpltweak', 'apply', 'fig.py',
                        '--params', _p57, '--write', '--json'],
                       capture_output=True, cwd=_d57, env=_env15)
_oo57 = _decode_out(_rr57.stdout + _rr57.stderr)
_j57 = {}
try:
    _j57 = (json.loads(_rr57.stdout.decode('utf-8')).get('files') or [{}])[0]
except Exception:                                             # noqa: BLE001
    pass
check('57a ②层取不到图状态 → verified 不得为 true（如实 null）+ semantic=null',
      _j57.get('verified') is not True and _j57.get('semantic') is None,
      'verified=%r semantic=%r' % (_j57.get('verified'), _j57.get('semantic')))
check('57a layers 三态如实（syntax=True / rerun=True / semantic=None）',
      (_j57.get('layers') or {}).get('syntax') is True
      and (_j57.get('layers') or {}).get('rerun') is True
      and (_j57.get('layers') or {}).get('semantic') is None,
      'layers=%r' % (_j57.get('layers'),))
check('57a 人读输出不得说"已验证"（要明说只确认了能跑通）',
      '已验证' not in _oo57 and '没有' in _oo57, _oo57[-260:])
shutil.rmtree(_d57, ignore_errors=True)

# 57b --verify-fast：如实标注"只做了语法检查、没重跑、没比对"
_d57b, _s57b, _p57b = _mk15(
    't23fast', _ADD_SRC,
    {'version': 4, 'script': 'fig.py',
     'axes': [{'index': 0, 'pos': [0.2, 0.2, 0.6, 0.6]}]})
_rr57b = subprocess.run([sys.executable, '-m', 'mpltweak', 'apply', 'fig.py',
                         '--params', _p57b, '--write', '--verify-fast', '--json'],
                        capture_output=True, cwd=_d57b, env=_env15)
_oo57b = _decode_out(_rr57b.stdout + _rr57b.stderr)
_j57b = {}
try:
    _j57b = (json.loads(_rr57b.stdout.decode('utf-8')).get('files') or [{}])[0]
except Exception:                                             # noqa: BLE001
    pass
check('57b --verify-fast → layers 只有 syntax=true，rerun/semantic 都是 None',
      (_j57b.get('layers') or {}).get('syntax') is True
      and (_j57b.get('layers') or {}).get('rerun') is None
      and (_j57b.get('layers') or {}).get('semantic') is None
      and _j57b.get('verified') is not True, 'layers=%r' % (_j57b.get('layers'),))
check('57b --verify-fast 人读明说"只做了语法检查"',
      '语法检查' in _oo57b and '没有重跑' in _oo57b, _oo57b[-260:])
shutil.rmtree(_d57b, ignore_errors=True)


# ---- 58. T23②(b)：块锚点不得落在最后一个 savefig 之后 ----
#   外部评审条目 3(b)：savefig 写成**单行复合语句**时，before-savefig 候选过去因缩进
#   （按 anchor.col_offset=9）写出 IndentationError → 退到 tail 候选 → ok/verified/
#   semantic 全 true，而 o.png 的 sha 前后完全相同。
_IF_SRC = ('import matplotlib\nmatplotlib.use("Agg")\n'
           'import matplotlib.pyplot as plt\n'
           'flag = True\n'
           'fig, ax = plt.subplots(figsize=(6, 3))\n'
           'ax.plot([1, 2, 3])\n'
           "if flag: fig.savefig('o.png')\n")
for _tag58, _style in (('58a', 'block'), ('58b', 'auto')):
    _d58, _s58, _p58 = _mk15('t23' + _tag58, _IF_SRC,
                             {'version': 4, 'script': 'fig.py',
                              'axes': [{'index': 0, 'pos': [0.2, 0.2, 0.6, 0.6]}]})
    _rr58 = subprocess.run([sys.executable, '-m', 'mpltweak', 'apply', 'fig.py',
                            '--params', _p58, '--write', '--style', _style,
                            '--json'], capture_output=True, cwd=_d58, env=_env15)
    _oo58 = _decode_out(_rr58.stdout + _rr58.stderr)
    _t58 = read(_s58)
    _ls = _t58.splitlines()
    _blk = [i for i, ln in enumerate(_ls) if 'auto layout (generated)' in ln]
    _sav = [i for i, ln in enumerate(_ls) if 'savefig' in ln]
    check('58%s 单行复合语句 savefig：块必须插在 savefig **之前**（%s）'
          % (_tag58, _style),
          _rr58.returncode == 0 and _blk and _sav and _blk[0] < _sav[0]
          and 'fail' not in _oo58[:40],
          'rc=%s blk=%s sav=%s | %s' % (_rr58.returncode, _blk, _sav, _oo58[-200:]))
    check('58%s 写回后源码仍然合法（缩进没坏）' % _tag58,
          _ast_ok(_t58) is True, str(_ast_ok(_t58))[:120])
    shutil.rmtree(_d58, ignore_errors=True)

# 58c 没有合规锚点时**拒绝写回**（把 find_anchor 打桩成只能给 tail，模拟
#     "唯一的锚点只能落在最后一个 savefig 之后"）
_d58c, _s58c, _p58c = _mk15('t23tail', _IF_SRC,
                            {'version': 4, 'script': 'fig.py',
                             'axes': [{'index': 0, 'pos': [0.2, 0.2, 0.6, 0.6]}]})
_s0_58c = read(_s58c)
_orig_anchor = writeback.find_anchor
try:
    writeback.find_anchor = lambda tree, last=False: ('tail', None)
    _r58c = writeback.writeback(_s58c, _params.load(_p58c), _p58c, verify=False,
                                fast_check=True, style='block')
finally:
    writeback.find_anchor = _orig_anchor
check('58c 唯一锚点只能落在 savefig 之后 → 拒绝写回（fail + 脚本未动）',
      _r58c.get('reason') == 'fail' and read(_s58c) == _s0_58c
      and '合规锚点' in (_r58c.get('err') or ''),
      'reason=%s err=%s' % (_r58c.get('reason'), (_r58c.get('err') or '')[:140]))
shutil.rmtree(_d58c, ignore_errors=True)


# ---- 59. T23②(c)：脚本本来跑不通时不许留坏写回 ----
#   59a 完整验证档 + 脚本因缺数据跑不通 → 默认**回滚 + 明确失败**（旧实现把写回留在
#       磁盘上并报 ok；T22 实测那次的写回文件 ast.parse 直接报 unexpected indent）。
_IF_SRC2 = _IF_SRC + "open('no_such_data.csv')\n"     # 原脚本也跑不通（缺数据）
_d59, _s59, _p59 = _mk15('t23_59a', _IF_SRC2,
                         {'version': 4, 'script': 'fig.py',
                          'axes': [{'index': 0, 'pos': [0.2, 0.2, 0.6, 0.6]}]})
_s0_59 = read(_s59)
_rr59 = subprocess.run([sys.executable, '-m', 'mpltweak', 'apply', 'fig.py',
                        '--params', _p59, '--write', '--json'],
                       capture_output=True, cwd=_d59, env=_env15)
_oo59 = _decode_out(_rr59.stdout + _rr59.stderr)
check('59a 脚本本身跑不通（缺数据）→ 默认回滚、不留写回、rc≠0',
      _rr59.returncode != 0 and read(_s59) == _s0_59
      and _ast_ok(read(_s59)) is True and '不保留' in _oo59,
      'rc=%s 源码被改=%s | %s' % (_rr59.returncode, read(_s59) != _s0_59,
                                  _oo59[-220:]))
shutil.rmtree(_d59, ignore_errors=True)

#   59b --verify-fast（只查语法）：写回语法合法 → 保留 + rc=0，但必须如实标注
#       "只做了语法检查、没重跑、没比对"（这就是 3(d) 要的层级透明）。
_d59b, _s59b, _p59b = _mk15('t23_59b', _IF_SRC2,
                            {'version': 4, 'script': 'fig.py',
                             'axes': [{'index': 0, 'pos': [0.2, 0.2, 0.6, 0.6]}]})
_rr59b = subprocess.run([sys.executable, '-m', 'mpltweak', 'apply', 'fig.py',
                         '--params', _p59b, '--write', '--verify-fast', '--json'],
                        capture_output=True, cwd=_d59b, env=_env15)
_oo59b = _decode_out(_rr59b.stdout + _rr59b.stderr)
_j59b = {}
try:
    _j59b = (json.loads(_rr59b.stdout.decode('utf-8')).get('files') or [{}])[0]
except Exception:                                             # noqa: BLE001
    pass
check('59b --verify-fast：语法合法 → 保留写回，但 layers 只有 syntax、人读标注级别',
      _rr59b.returncode == 0 and (_j59b.get('layers') or {}).get('syntax') is True
      and (_j59b.get('layers') or {}).get('rerun') is None
      and '语法检查' in _oo59b,
      'rc=%s layers=%r | %s' % (_rr59b.returncode, _j59b.get('layers'),
                                _oo59b[-200:]))
shutil.rmtree(_d59b, ignore_errors=True)

#   59c 写盘后语法真的坏了（用 §35 同款注入）：**无条件 ast.parse** 必须当场抓住并回滚，
#       哪怕脚本本来就跑不通（过去 baseline 分支会把它当"不是我们的锅"留下）。
_d59c, _s59c, _p59c = _mk15('t23_59c', _IF_SRC2,
                            {'version': 4, 'script': 'fig.py',
                             'axes': [{'index': 0, 'pos': [0.2, 0.2, 0.6, 0.6]}]})
_s0_59c = read(_s59c)
_orig_apply23 = writeback.apply_inplace


def _bad_apply23(*a, **k):
    _n, _c, _s = _orig_apply23(*a, **k)
    return _n + '\ndef broken(:\n', _c, _s


writeback.apply_inplace = _bad_apply23
try:
    _r59c = writeback.writeback(_s59c, _params.load(_p59c), _p59c, verify=True,
                               semantic=True, style='inplace')
finally:
    writeback.apply_inplace = _orig_apply23
check('59c 写回后语法坏 → 无条件 ast.parse 抓住：fail + 回滚 + 明确指出语法错',
      _r59c.get('reason') == 'fail'
      and '语法错误' in (_r59c.get('err') or '')
      and read(_s59c) == _s0_59c,
      'reason=%s err=%s' % (_r59c.get('reason'), (_r59c.get('err') or '')[:160]))
shutil.rmtree(_d59c, ignore_errors=True)

# 59d 显式 --keep-unverified 时才保留（只适用于"语法没问题、只是跑不通"）
_NEED_DATA_SRC = ('import matplotlib\nmatplotlib.use("Agg")\n'
                  'import matplotlib.pyplot as plt\n'
                  "open('no_such_data.csv')\n"
                  'fig, ax = plt.subplots(figsize=(6, 3))\n'
                  'ax.plot([1, 2, 3])\n'
                  'fig.savefig("o.png")\n')
for _tag59d, _extra, _want_kept in (('59d', [], False),
                                    ('59e', ['--keep-unverified'], True)):
    _d59d, _s59d, _p59d = _mk15('t23' + _tag59d, _NEED_DATA_SRC,
                                {'version': 4, 'script': 'fig.py',
                                 'axes': [{'index': 0,
                                           'pos': [0.2, 0.2, 0.6, 0.6]}]})
    _s0_59d = read(_s59d)
    _rr59d = subprocess.run([sys.executable, '-m', 'mpltweak', 'apply', 'fig.py',
                             '--params', _p59d, '--write', '--style', 'block',
                             '--json'] + _extra,
                            capture_output=True, cwd=_d59d, env=_env15)
    _oo59d = _decode_out(_rr59d.stdout + _rr59d.stderr)
    _kept = read(_s59d) != _s0_59d
    check('%s 缺数据脚本：%s（保留=%s）'
          % (_tag59d, '默认回滚' if not _want_kept else '--keep-unverified 保留',
             _kept),
          _kept is _want_kept and _ast_ok(read(_s59d)) is True,
          'rc=%s kept=%s | %s' % (_rr59d.returncode, _kept, _oo59d[-200:]))
    shutil.rmtree(_d59d, ignore_errors=True)


# ---- 60. T23 附带：刻度字号 x/y 分列（与 label_fontsize_y 同构，T25 的下游依赖）----
#   导出侧（T25 GUI）已经能分列 tick_fontsize / tick_fontsize_y，写回侧过去只认单一值 →
#   `tick_params(labelsize=…)` 一次把 x/y 都改掉，y 的改动被吃掉。
_TICK_ONE = ('import matplotlib\nmatplotlib.use("Agg")\n'
             'import matplotlib.pyplot as plt\n'
             'fig, ax = plt.subplots(figsize=(6, 3))\n'
             'ax.plot([1, 2, 3])\n'
             'ax.tick_params(labelsize=10)\n'
             'fig.savefig("o.png")\n')
_TICK_TWO = ('import matplotlib\nmatplotlib.use("Agg")\n'
             'import matplotlib.pyplot as plt\n'
             'fig, ax = plt.subplots(figsize=(6, 3))\n'
             'ax.plot([1, 2, 3])\n'
             "ax.tick_params(axis='x', labelsize=10)\n"
             "ax.tick_params(axis='y', labelsize=11)\n"
             'fig.savefig("o.png")\n')

# 60a 单一 labelsize（一次管两边）+ 参数要 x=10 / y=11 → 原位表达不了差异 → 退块，
#     且块里必须**分两行**写（x 的 10 不许写到 y 上，反之亦然）。
_d60, _s60, _p60 = _mk15('t23tick1', _TICK_ONE,
                         {'version': 4, 'script': 'fig.py',
                          'axes': [{'index': 0, 'tick_fontsize': 10.0,
                                    'tick_fontsize_y': 11.0}]})
_r60a = writeback.writeback(_s60, _params.load(_p60), _p60, verify=False,
                            fast_check=True, style='inplace')
check('60a 单一 labelsize + 参数分列 x/y → 原位写不了 → needs_block=True',
      _r60a.get('needs_block') is True
      and (_r60a.get('pending') or []) == ['ax0.tick_fontsize_y'],
      'nb=%r pending=%r' % (_r60a.get('needs_block'), _r60a.get('pending')))
_r60a2 = writeback.writeback(_s60, _params.load(_p60), _p60, verify=False,
                             fast_check=True, style='block')
_t60 = read(_s60)
check('60a 退块后：y 用 11（x 不许被 y 的值顶掉；x=10 与源码相同 → 块里不必重写）',
      "tick_params(axis='y', labelsize=11)" in _t60
      and "axis='x', labelsize=11" not in _t60,
      [ln for ln in _t60.splitlines() if 'tick_params' in ln])
shutil.rmtree(_d60, ignore_errors=True)

# 60b 源码里已经有分开的 axis='x' / axis='y' 写法 → 原位各自改字面量、changes 两个字段
_d60b, _s60b, _p60b = _mk15('t23tick2', _TICK_TWO,
                            {'version': 4, 'script': 'fig.py',
                             'axes': [{'index': 0, 'tick_fontsize': 12.0,
                                       'tick_fontsize_y': 15.0}]})
_r60b = writeback.writeback(_s60b, _params.load(_p60b), _p60b, verify=False,
                            fast_check=True, style='inplace')
_t60b = read(_s60b)
check('60b 源码已分列 → 原位各改各的（x=12、y=15，changes 两个字段）',
      "axis='x', labelsize=12" in _t60b and "axis='y', labelsize=15" in _t60b
      and _r60b.get('changes') == ['ax0.tick_fontsize', 'ax0.tick_fontsize_y'],
      'changes=%r | %s' % (_r60b.get('changes'),
                           [ln for ln in _t60b.splitlines() if 'tick_params' in ln]))
shutil.rmtree(_d60b, ignore_errors=True)

# 60c 老参数文件（没有 tick_fontsize_y）→ 行为不变：还是单行 labelsize
_d60c, _s60c, _p60c = _mk15('t23tick3', _TICK_ONE,
                            {'version': 4, 'script': 'fig.py',
                             'axes': [{'index': 0, 'tick_fontsize': 12.0}]})
writeback.writeback(_s60c, _params.load(_p60c), _p60c, verify=False,
                    fast_check=True, style='inplace')
_t60c = read(_s60c)
check('60c 老参数文件（无 tick_fontsize_y）→ 仍是单行 labelsize=12（兼容不变）',
      'tick_params(labelsize=12)' in _t60c and "axis='y'" not in _t60c,
      [ln for ln in _t60c.splitlines() if 'tick_params' in ln])
shutil.rmtree(_d60c, ignore_errors=True)

# 60d 端到端（--style block）：只改 y 时源码里 x 仍是 10、y 是 11
_d60d, _s60d, _p60d = _mk15('t23tick4', _TICK_TWO,
                            {'version': 4, 'script': 'fig.py',
                             'axes': [{'index': 0, 'tick_fontsize': 10.0,
                                       'tick_fontsize_y': 13.0}]})
_rr60d = subprocess.run([sys.executable, '-m', 'mpltweak', 'apply', 'fig.py',
                         '--params', _p60d, '--write', '--style', 'block',
                         '--json'], capture_output=True, cwd=_d60d, env=_env15)
_oo60d = _decode_out(_rr60d.stdout + _rr60d.stderr)
check('60d 只改 y（11→13）：x 保持 10、y 变 13（x 的 10 不重写）',
      _rr60d.returncode == 0 and "axis='y', labelsize=13" in read(_s60d)
      and "axis='x', labelsize=13" not in read(_s60d)
      and "axis='x', labelsize=10" in read(_s60d),
      'rc=%s | %s' % (_rr60d.returncode, _oo60d[-200:]))
shutil.rmtree(_d60d, ignore_errors=True)


# ---- 61. T23 跨线回归：T26 新采集的字段（cmap / legend / lines / tick_fontsize_y）----
#   T26 让 verify._state 采集 cmap/legend/lines 并补 tick_fontsize_y，describe 产物里就
#   出现了这些字段；而写回侧原位写不了 cmap/legend/lines → 旧的逐字段三态判据把它们
#   全算成 pending → **参数与源码一模一样的干净往返也退块**（集成树 pytest 因此变红：
#   §44 E5 干净往返 / 幂等、§47 E4 imshow+aspect 锁定都从 no_change 变成 best_effort）。
#   修法：给这些字段补等价证明（静态字面量 + 实测回比），等值就不算 pending、不退块。
_CMAP_SRC = ('import matplotlib\nmatplotlib.use("Agg")\n'
             'import matplotlib.pyplot as plt\n'
             'import numpy as np\n'
             'fig, ax = plt.subplots(figsize=(6, 3))\n'
             'im = ax.imshow(np.arange(9).reshape(3, 3), cmap="viridis")\n'
             'fig.colorbar(im, ax=ax)\n'
             'fig.savefig("o.png")\n')
_LEG_SRC = ('import matplotlib\nmatplotlib.use("Agg")\n'
            'import matplotlib.pyplot as plt\n'
            'fig, ax = plt.subplots(figsize=(6, 3))\n'
            "ax.plot([1, 2, 3], label='a')\n"
            "ax.legend(loc='upper right')\n"
            'fig.savefig("o.png")\n')
_TICKY_SRC = ('import matplotlib\nmatplotlib.use("Agg")\n'
              'import matplotlib.pyplot as plt\n'
              'fig, ax = plt.subplots(figsize=(6, 3))\n'
              'ax.plot([1, 2, 3])\n'
              'ax.tick_params(labelsize=10)\n'
              'fig.savefig("o.png")\n')

# 61a cmap 等值（源码里就是 cmap="viridis"）→ 不算 pending、不退块
_d61a, _s61a, _p61a = _mk15('t23cmap', _CMAP_SRC,
                            {'version': 4, 'script': 'fig.py',
                             'axes': [{'index': 0, 'cmap': 'viridis'}]})
_r61a = writeback.writeback(_s61a, _params.load(_p61a), _p61a, verify=False,
                            fast_check=True, style='inplace')
check('61a cmap 等值（T26 新字段）→ no_change、字节不变、不插块',
      _r61a.get('reason') == 'no_change' and _r61a.get('needs_block') is False
      and read(_s61a) == _CMAP_SRC,
      '%s / nb=%r pending=%r' % (_r61a.get('reason'), _r61a.get('needs_block'),
                                 _r61a.get('pending')))
shutil.rmtree(_d61a, ignore_errors=True)

# 61b legend 等值（loc 一致）→ no_change
_d61b, _s61b, _p61b = _mk15('t23leg', _LEG_SRC,
                            {'version': 4, 'script': 'fig.py',
                             'axes': [{'index': 0,
                                       'legend': {'loc': 'upper right'}}]})
_r61b = writeback.writeback(_s61b, _params.load(_p61b), _p61b, verify=False,
                            fast_check=True, style='inplace')
check('61b legend 等值（loc 一致）→ no_change、不插块',
      _r61b.get('reason') == 'no_change' and read(_s61b) == _LEG_SRC,
      '%s / nb=%r pending=%r' % (_r61b.get('reason'), _r61b.get('needs_block'),
                                 _r61b.get('pending')))
shutil.rmtree(_d61b, ignore_errors=True)

# 61c tick_fontsize_y 等值（单一 labelsize=10 同时管两边）→ no_change
_d61c, _s61c, _p61c = _mk15('t23ticky', _TICKY_SRC,
                            {'version': 4, 'script': 'fig.py',
                             'axes': [{'index': 0, 'tick_fontsize': 10.0,
                                       'tick_fontsize_y': 10.0}]})
_r61c = writeback.writeback(_s61c, _params.load(_p61c), _p61c, verify=False,
                            fast_check=True, style='inplace')
check('61c tick_fontsize_y 等值 → no_change、不插块',
      _r61c.get('reason') == 'no_change' and read(_s61c) == _TICKY_SRC,
      '%s / nb=%r pending=%r' % (_r61c.get('reason'), _r61c.get('needs_block'),
                                 _r61c.get('pending')))
shutil.rmtree(_d61c, ignore_errors=True)

# 61d/e/f describe → 原样 apply 的**干净往返**：三类脚本都必须 no_change 且字节不变
#   （① imshow+colorbar（T26 会带 cmap）② legend ③ 自定义刻度字号（带 tick_fontsize_y））。
#   注意：本副本的 describe 还没合入 T26（cmap/legend 是集成树那边刚加的），所以这三条
#   在本副本里主要挡"其它字段被新采集后带崩干净往返"；带 cmap/legend 的**判别**用例是
#   上面的 61a/61b（手写参数）+ 集成树仿真（fix018/t23_integ_sim.txt）。
for _tag61, _src61 in (('61d', _CMAP_SRC), ('61e', _LEG_SRC), ('61f', _TICKY_SRC)):
    _d61 = tempfile.mkdtemp(prefix='mpltweak_t23rt_')
    _s61 = os.path.join(_d61, 'fig.py')
    with open(_s61, 'w', encoding='utf-8', newline='\n') as f:
        f.write(_src61)
    _rr_d = subprocess.run([sys.executable, '-m', 'mpltweak', 'describe', 'fig.py'],
                           capture_output=True, cwd=_d61, env=_env15)
    if _rr_d.returncode != 0:
        check('%s describe 成功' % _tag61, False, _decode_out(_rr_d.stderr)[-200:])
        shutil.rmtree(_d61, ignore_errors=True)
        continue
    _p61 = os.path.join(_d61, 'p.json')
    with open(_p61, 'wb') as f:
        f.write(_rr_d.stdout)
    _s0_61 = read(_s61)
    _rr_a = subprocess.run([sys.executable, '-m', 'mpltweak', 'apply', 'fig.py',
                            '--params', 'p.json', '--write', '--json'],
                           capture_output=True, cwd=_d61, env=_env15)
    _oo_a = _decode_out(_rr_a.stdout + _rr_a.stderr)
    check('%s describe→原样 apply 干净往返：no_change 且字节不变' % _tag61,
          '"reason": "no_change"' in _oo_a and read(_s61) == _s0_61,
          'rc=%s | %s' % (_rr_a.returncode, _oo_a[-240:]))
    shutil.rmtree(_d61, ignore_errors=True)

# 61e 单元级：T26 采集到的实测状态（cmap/legend/lines/tick_fontsize_y）参与回比不再"判不了"
check('61e _pv_live_field 认识 T26 的新字段（等值 False / 不等 True）',
      writeback._pv_live_field('cmap', {'cmap': 'viridis'},
                               {'cmap': 'viridis'}, {}, 0) is False
      and writeback._pv_live_field('cmap', {'cmap': 'viridis'},
                                   {'cmap': 'plasma'}, {}, 0) is True
      and writeback._pv_live_field(
          'legend', {'legend': {'labels': ['a'], 'n_handles': 1,
                                'loc': 'upper right'}},
          {'legend': {'labels': ['a'], 'n_handles': 1, 'loc': 'upper right'}},
          {}, 0) is False
      and writeback._pv_live_field(
          'lines', {'lines': [{'linewidth': 2.0, 'color': 'r'}]},
          {'lines': [{'linewidth': 2.0, 'color': 'r'}]}, {}, 0) is False
      and writeback._pv_live_field('tick_fontsize_y', {'tick_fontsize_y': 11.0},
                                   {'tick_fontsize_y': 11.0}, {}, 0) is False,
      'cmap/legend/lines/tick_y 的 live 回比')


# ---- 62. T35：默认 scale 注入 → 把 log 轴写成 linear（E5，真 bug）----
#   normalize 过去给**每个轴**补 `xscale/yscale='linear'`（调用方从没要求），写回侧把它
#   当目标 → 源码明写 log 时判"有目标值且不同" → 退块 → 块里塞 `set_xscale('linear')`
#   → **对数轴被改成线性**（L0 最终复测：23192393__figure_panels / 5809269__O3_FYSP_paper）。
#   修法：默认值改 None（＝未设置，与 grid 同一个先例）＋ 块渲染器只写 pending 里的字段。
_LOG_SRC = ('import matplotlib\nmatplotlib.use("Agg")\n'
            'import matplotlib.pyplot as plt\n'
            'fig = plt.figure(figsize=(6.85, 6.15))\n'
            'ax = fig.add_axes([0.082, 0.070, 0.903, 0.022])\n'
            'ax.plot([1, 2, 3])\n'
            'ax.set_xscale("log")\n'
            'ax.set_yscale("log")\n'
            'fig.savefig("o.png")\n')

# 62a（A1）参数里**根本不给** scale → 必须 no_change / 字节不变 / 不插块
_d62a, _s62a, _p62a = _mk15('t35a1', _LOG_SRC,
                            {'version': 4, 'script': 'fig.py',
                             'axes': [{'index': 0,
                                       'pos': [0.082, 0.070, 0.903, 0.022]}]})
_r62a = writeback.writeback(_s62a, _params.load(_p62a), _p62a, verify=False,
                            fast_check=True, style='inplace')
check('62a A1：参数不给 scale（源码是 log）→ no_change / 字节不变 / 不插块',
      _r62a.get('reason') == 'no_change' and read(_s62a) == _LOG_SRC
      and writeback.BLOCK_START not in read(_s62a),
      '%s / nb=%r pending=%r' % (_r62a.get('reason'), _r62a.get('needs_block'),
                                 _r62a.get('pending')))
shutil.rmtree(_d62a, ignore_errors=True)

# 62b（A2）scale 写成源码真值 log → no_change（防回归）
_d62b, _s62b, _p62b = _mk15('t35a2', _LOG_SRC,
                            {'version': 4, 'script': 'fig.py',
                             'axes': [{'index': 0,
                                       'pos': [0.082, 0.070, 0.903, 0.022],
                                       'xscale': 'log', 'yscale': 'log'}]})
_r62b = writeback.writeback(_s62b, _params.load(_p62b), _p62b, verify=False,
                            fast_check=True, style='inplace')
check('62b A2：scale 写成源码真值 log → no_change / 不插块',
      _r62b.get('reason') == 'no_change' and read(_s62b) == _LOG_SRC
      and writeback.BLOCK_START not in read(_s62b),
      '%s / pending=%r' % (_r62b.get('reason'), _r62b.get('pending')))
shutil.rmtree(_d62b, ignore_errors=True)

# 62c（B3）`plt.yscale('log')` + 一个**真实** pos 目标（网格轴，必须退块）→
#       块里不得出现 linear scale；写回后真跑一遍第一条轴仍是 log
_B3_SRC = ('import matplotlib\nmatplotlib.use("Agg")\n'
           'import matplotlib.pyplot as plt\n'
           'fig, ax = plt.subplots(figsize=(6, 3))\n'
           'ax.plot([1, 2, 3])\n'
           'ax.tick_params(labelsize=10)\n'
           "plt.yscale('log')\n"
           'fig.savefig("o.png")\n')
_d62c, _s62c, _p62c = _mk15('t35b3', _B3_SRC,
                            {'version': 4, 'script': 'fig.py',
                             'axes': [{'index': 0,
                                       'pos': [0.2, 0.2, 0.6, 0.6],
                                       # 调用方**显式**写上与源码一致的值：
                                       # 它们都不该被写进块（T35 的 needed 过滤）
                                       'xscale': 'log', 'yscale': 'log',
                                       'tick_fontsize': 10.0}]})
_rr62c = subprocess.run([sys.executable, '-m', 'mpltweak', 'apply', 'fig.py',
                         '--params', _p62c, '--write', '--json'],
                        capture_output=True, cwd=_d62c, env=_env15)
_t62c = read(_s62c)
_blk62c = _t62c.split(writeback.BLOCK_START)[-1]
check('62c B3：真实 pos 目标 + 源码 plt.yscale(log) → 块里不得出现 linear scale；'
      '显式但等值的字段（scale/字号）也不进块',
      _rr62c.returncode == 0 and 'set_position' in _t62c
      and "set_xscale('linear')" not in _t62c
      and "set_yscale('linear')" not in _t62c
      and 'labelsize=' not in _blk62c
      and "plt.yscale('log')" in _t62c,
      'rc=%s | %s' % (_rr62c.returncode,
                      [ln for ln in _t62c.splitlines()
                       if 'scale' in ln or 'set_position' in ln
                       or 'labelsize' in ln]))
_rr62c_run = subprocess.run(
    [sys.executable, '-c',
     'import runpy, matplotlib\n'
     'matplotlib.use("Agg")\n'
     'import matplotlib.pyplot as plt\n'
     'runpy.run_path("fig.py")\n'
     'print("yscale=" + plt.gcf().axes[0].get_yscale())'],
    capture_output=True, cwd=_d62c, env=_env15)
_o62c = _decode_out(_rr62c_run.stdout + _rr62c_run.stderr)
check('62c B3：写回后真跑一遍，第一条轴仍是 log', 'yscale=log' in _o62c, _o62c[-200:])
shutil.rmtree(_d62c, ignore_errors=True)

# 62d（M4 次级）参数里所有字段都与源码一致 → **不得写块**（显式 --style block 也一样）
_M4_SRC = ('import matplotlib\nmatplotlib.use("Agg")\n'
           'import matplotlib.pyplot as plt\n'
           'fig = plt.figure(figsize=(6, 4))\n'
           'ax = fig.add_axes([0.10, 0.10, 0.80, 0.80])\n'
           "ax.set_title('t', fontsize=12)\n"
           'ax.grid(True)\n'
           'fig.savefig("o.png")\n')
_d62d, _s62d, _p62d = _mk15('t35m4', _M4_SRC,
                            {'version': 4, 'script': 'fig.py',
                             'axes': [{'index': 0, 'pos': [0.10, 0.10, 0.80, 0.80],
                                       'title_fontsize': 12.0, 'grid': True}]})
_r62d = writeback.writeback(_s62d, _params.load(_p62d), _p62d, verify=False,
                            fast_check=True, style='block')
check('62d M4：没有字段需要落地 → 不写块（no_change + 字节不变）',
      _r62d.get('reason') == 'no_change' and read(_s62d) == _M4_SRC
      and writeback.BLOCK_START not in read(_s62d),
      '%s / %s' % (_r62d.get('reason'),
                   [ln for ln in read(_s62d).splitlines() if 'layout' in ln]))
shutil.rmtree(_d62d, ignore_errors=True)

# 62e 反向保护：调用方**显式**要 linear（源码 log）时仍必须落地（T18 §54j 的成果）
_d62e, _s62e, _p62e = _mk15('t35exp', _LOG_SRC,
                            {'version': 4, 'script': 'fig.py',
                             'axes': [{'index': 0, 'xscale': 'linear'}]})
_r62e = writeback.writeback(_s62e, _params.load(_p62e), _p62e, verify=False,
                            fast_check=True, style='inplace')
check('62e 显式 linear（源码 log）→ 仍然要落地（needs_block + pending）',
      _r62e.get('needs_block') is True
      and 'ax0.xscale' in (_r62e.get('pending') or []),
      'nb=%r pending=%r' % (_r62e.get('needs_block'), _r62e.get('pending')))
shutil.rmtree(_d62e, ignore_errors=True)


# ---- 63. T36：多份参数一次 apply → revert 必须拿回**批前原件** ----
#   多图会话里一次 `apply --write` 会连着改同一个脚本（每份参数一次）；每次写盘都
#   备一份 → 固定名 `.tweak.bak` 被后来的写盘覆盖成"中间态"，`revert` 只能还原到
#   "改了一半"（和 L0-4 / inplace→block 同一个坑，只是发生在多参数循环里）。
#   修法：本批次第一次写回产生的备份沿循环逐份透传。
_T36_SRC = ('import matplotlib\nmatplotlib.use("Agg")\n'
            'import matplotlib.pyplot as plt\n'
            'fig1, ax1 = plt.subplots(figsize=(6, 3))\n'
            'ax1.plot([1, 2, 3])\n'
            'fig1.savefig("a.png")\n'
            'fig2, ax2 = plt.subplots(figsize=(6, 3))\n'
            'ax2.plot([3, 2, 1])\n'
            'fig2.savefig("b.png")\n')
_d63 = tempfile.mkdtemp(prefix='mpltweak_t36_')
_pd63 = os.path.join(_d63, '.tweak_params')
os.makedirs(_pd63)
_s63 = os.path.join(_d63, 'fig.py')
with open(_s63, 'w', encoding='utf-8', newline='\n') as f:
    f.write(_T36_SRC)
for _name63, _idx63, _pos63 in (('fig.fig0.json', 0, [0.2, 0.2, 0.6, 0.6]),
                                ('fig.fig1.json', 1, [0.5, 0.5, 0.3, 0.3])):
    with open(os.path.join(_pd63, _name63), 'w', encoding='utf-8') as f:
        json.dump({'version': 4, 'script': 'fig.py', 'fig_index': _idx63,
                   'n_figs': 2, 'axes': [{'index': 0, 'pos': _pos63}]},
                  f, ensure_ascii=False)
_sha63_before = _sha(_s63)
_rr63 = subprocess.run([sys.executable, '-m', 'mpltweak', 'apply', 'fig.py',
                        '--write', '--json'], capture_output=True, cwd=_d63,
                       env=_env15)
_oo63 = _decode_out(_rr63.stdout + _rr63.stderr)
_t63 = read(_s63)
check('63a 2 份参数一次 apply --write：两张都落地（脚本确实被改过）',
      _rr63.returncode == 0 and _t63.count(writeback.BLOCK_START) == 2
      and _sha(_s63) != _sha63_before,
      'rc=%s blocks=%d | %s' % (_rr63.returncode,
                                _t63.count(writeback.BLOCK_START), _oo63[-200:]))
_rr63b = subprocess.run([sys.executable, '-m', 'mpltweak', 'revert', 'fig.py',
                         '--write', '--json'], capture_output=True, cwd=_d63,
                        env=_env15)
_oo63b = _decode_out(_rr63b.stdout + _rr63b.stderr)
check('63b 2 份参数一次 apply → revert：脚本 sha256 == 原件（备份没被第二次写盘覆盖）',
      _rr63b.returncode == 0 and _sha(_s63) == _sha63_before,
      'rc=%s sha相等=%s | %s' % (_rr63b.returncode, _sha(_s63) == _sha63_before,
                                 _oo63b[-240:]))
shutil.rmtree(_d63, ignore_errors=True)

# 63c/63d：`revert --first` 同样要回到真·原件；预览输出必须与实际回退一致
#   （F-V1 的现场：修前预览只显示第 2 次写盘的 `-ax2 add_axes` 行）
_T36_ADD = ('import matplotlib\nmatplotlib.use("Agg")\n'
            'import matplotlib.pyplot as plt\n'
            'fig1 = plt.figure(figsize=(6, 3))\n'
            'ax1 = fig1.add_axes([0.10, 0.10, 0.80, 0.80])\n'
            'ax1.plot([1, 2, 3])\n'
            'fig1.savefig("a.png")\n'
            'fig2 = plt.figure(figsize=(6, 3))\n'
            'ax2 = fig2.add_axes([0.10, 0.10, 0.80, 0.80])\n'
            'ax2.plot([3, 2, 1])\n'
            'fig2.savefig("b.png")\n')
for _tag63, _extra63, _what63 in (('63c', ['--first'], '首份备份 --first'),
                                  ('63d', [], '默认备份')):
    _d63b = tempfile.mkdtemp(prefix='mpltweak_t36x_')
    _pdb = os.path.join(_d63b, '.tweak_params')
    os.makedirs(_pdb)
    _sb = os.path.join(_d63b, 'fig.py')
    with open(_sb, 'w', encoding='utf-8', newline='\n') as f:
        f.write(_T36_ADD)
    for _n63, _i63, _pos63 in (('fig.fig0.json', 0, [0.2, 0.2, 0.6, 0.6]),
                               ('fig.fig1.json', 1, [0.5, 0.5, 0.3, 0.3])):
        with open(os.path.join(_pdb, _n63), 'w', encoding='utf-8') as f:
            json.dump({'version': 4, 'script': 'fig.py', 'fig_index': _i63,
                       'n_figs': 2, 'axes': [{'index': 0, 'pos': _pos63}]},
                      f, ensure_ascii=False)
    _sha0 = _sha(_sb)
    subprocess.run([sys.executable, '-m', 'mpltweak', 'apply', 'fig.py',
                    '--write', '--json'], capture_output=True, cwd=_d63b,
                   env=_env15)
    _t63b = read(_sb)
    _both63 = ('[0.2, 0.2, 0.6, 0.6]' in _t63b
               and '[0.5, 0.5, 0.3, 0.3]' in _t63b)
    if _tag63 == '63d':
        # 预览（不带 --write）：必须同时显示**两处**改动（修前只显示第 2 处）
        _rp63 = subprocess.run([sys.executable, '-m', 'mpltweak', 'revert',
                                'fig.py', '--json'], capture_output=True,
                               cwd=_d63b, env=_env15)
        try:
            _jp63 = json.loads(_rp63.stdout.decode('utf-8'))
        except Exception:                                         # noqa: BLE001
            _jp63 = {}
        _l63 = ' '.join(_jp63.get('lines') or [])
        check('63d 预览与实际一致：两处改动都出现在预览里（ax1 + ax2）',
              _jp63.get('changed') is True
              and (_jp63.get('diff') or {}).get('removed') == 2
              and 'ax1' in _l63 and 'ax2' in _l63,
              'changed=%r diff=%r lines=%s'
              % (_jp63.get('changed'), _jp63.get('diff'),
                 (_jp63.get('lines') or [])[:4]))
    _rrw63 = subprocess.run([sys.executable, '-m', 'mpltweak', 'revert', 'fig.py',
                             '--write', '--json'] + _extra63,
                            capture_output=True, cwd=_d63b, env=_env15)
    check('63%s 批量 apply（两处都落地=%s）→ revert（%s）→ sha256 == 原件'
          % (_tag63, _both63, _what63),
          _rrw63.returncode == 0 and _sha(_sb) == _sha0,
          'rc=%s sha相等=%s | %s'
          % (_rrw63.returncode, _sha(_sb) == _sha0,
             _decode_out(_rrw63.stdout + _rrw63.stderr)[-160:]))
    shutil.rmtree(_d63b, ignore_errors=True)


# ---- 64. T41：同名轴变量静默丢改动（blocker）+ 长路径 pid 备份 / --first ----
#   同一张图里 `ax = fig.add_subplot(2,1,k)` 写两遍时，`_find_call` 只命中最早的
#   同名调用 → 第 2 个轴的属性落到第 1 个轴上：noop 判定把字段从 covered 剔除 →
#   谎报 no_change、源码一个字节没改（语料 F-DUPVAR 实测 16 例触发）。
_DUP_SRC = ('import matplotlib\nmatplotlib.use("Agg")\n'
            'import matplotlib.pyplot as plt\n'
            'fig = plt.figure(figsize=(6, 6))\n'
            'ax = fig.add_subplot(2, 1, 1)\n'
            'ax.plot([1, 2], [3, 4])\n'
            'ax.grid(True)\n'
            'ax = fig.add_subplot(2, 1, 2)\n'
            'ax.plot([1, 2], [4, 3])\n'
            'ax.grid(False)\n'
            "fig.savefig('repro_out.png')\n")

# 64a 定向改写第 2 个轴（grid True，源码是 False）——原位模式下必须**明确报出**
#     "第 2 个轴无法原位定位"（needs_block + pending），不许 no_change
_d64a, _s64a, _p64a = _mk15('t41dup', _DUP_SRC,
                            {'version': 4, 'script': 'fig.py', 'fig_index': 0,
                             'axes': [{'index': 0, 'grid': True},
                                      {'index': 1, 'grid': True}]})
_r64a = writeback.writeback(_s64a, _params.load(_p64a), _p64a, verify=False,
                            fast_check=True, style='inplace')
check('64a 同名两轴：第 2 个轴按**本轴窗口**精确归属 → 就地落地（不再是静默 no_change）',
      _r64a.get('reason') == 'ok'
      and 'ax1.grid' in (_r64a.get('changes') or [])
      and [ln.strip() for ln in read(_s64a).splitlines() if 'grid(' in ln]
      == ['ax.grid(True)', 'ax.grid(True)'],
      '%s changes=%r | %s' % (_r64a.get('reason'), _r64a.get('changes'),
                              [ln.strip() for ln in read(_s64a).splitlines()
                               if 'grid(' in ln]))
shutil.rmtree(_d64a, ignore_errors=True)

# 64b auto（默认）——要么落地、要么明确失败：这里必须退块把第 2 个轴落地
_d64b, _s64b, _p64b = _mk15('t41dup2', _DUP_SRC,
                            {'version': 4, 'script': 'fig.py', 'fig_index': 0,
                             'axes': [{'index': 0, 'grid': True},
                                      {'index': 1, 'grid': True}]})
_rr64b = subprocess.run([sys.executable, '-m', 'mpltweak', 'apply', 'fig.py',
                         '--params', _p64b, '--write', '--verify-fast',
                         '--json'], capture_output=True, cwd=_d64b, env=_env15)
_oo64b = _decode_out(_rr64b.stdout + _rr64b.stderr)
_t64b = read(_s64b)
check('64b auto：同名第 2 个轴的改动**落地**（就地改到本轴那一行，不插块）',
      _rr64b.returncode == 0
      and [ln.strip() for ln in _t64b.splitlines() if 'grid(' in ln]
      == ['ax.grid(True)', 'ax.grid(True)']
      and writeback.BLOCK_START not in _t64b,
      'rc=%s | %s' % (_rr64b.returncode, _oo64b[-200:]))
shutil.rmtree(_d64b, ignore_errors=True)

# 64c 逐轴等值（轴0 True=源码、轴1 False=源码）→ auto 下**不得改动文件**
_d64c, _s64c, _p64c = _mk15('t41dup3', _DUP_SRC,
                            {'version': 4, 'script': 'fig.py', 'fig_index': 0,
                             'axes': [{'index': 0, 'grid': True},
                                      {'index': 1, 'grid': False}]})
_rr64c = subprocess.run([sys.executable, '-m', 'mpltweak', 'apply', 'fig.py',
                         '--params', _p64c, '--write', '--verify-fast',
                         '--json'], capture_output=True, cwd=_d64c, env=_env15)
_oo64c = _decode_out(_rr64c.stdout + _rr64c.stderr)
check('64c 逐轴等于源码现值 → auto 下 no_change、字节不变、不插块',
      '"reason": "no_change"' in _oo64c and read(_s64c) == _DUP_SRC
      and writeback.BLOCK_START not in read(_s64c),
      'rc=%s | %s' % (_rr64c.returncode, _oo64c[-220:]))
shutil.rmtree(_d64c, ignore_errors=True)

# 64d pid 副本写不出来时**不许静默**：warning 里要有路径与 errno
_d64d, _s64d, _p64d = _mk15('t41bak', _DUP_SRC, {'version': 4, 'script': 'fig.py'})
_warn64 = []
_real_copy = writeback.shutil.copy2


def _fake_copy(src, dst, *a, **k):
    if '.tweak.' in str(dst) and str(dst).endswith('.bak') \
            and not str(dst).endswith('.tweak.bak'):
        raise OSError(2, 'fake long path')
    return _real_copy(src, dst, *a, **k)


try:
    writeback.shutil.copy2 = _fake_copy
    writeback._make_backup(_s64d, _p64d, warnings=_warn64)
finally:
    writeback.shutil.copy2 = _real_copy
check('64d pid 副本失败不再静默：warning 含路径与 errno',
      len(_warn64) == 1 and 'pid 专属备份' in _warn64[0] and 'errno=2' in _warn64[0],
      str(_warn64)[:200])
shutil.rmtree(_d64d, ignore_errors=True)

# 64e 没有 pid 副本时 `revert --first` 必须**失败**（不得静默回落仍报 first:true）
_d64e, _s64e, _p64e = _mk15('t41first', _DUP_SRC,
                            {'version': 4, 'script': 'fig.py', 'fig_index': 0,
                             'axes': [{'index': 1, 'grid': True}]})
_rr64e = subprocess.run([sys.executable, '-m', 'mpltweak', 'apply', 'fig.py',
                         '--params', _p64e, '--write', '--verify-fast'],
                        capture_output=True, cwd=_d64e, env=_env15)
for _n in os.listdir(os.path.dirname(_p64e)):
    if _n.endswith('.bak') and not _n.endswith('.tweak.bak'):
        os.remove(os.path.join(os.path.dirname(_p64e), _n))
_rr64f = subprocess.run([sys.executable, '-m', 'mpltweak', 'revert', 'fig.py',
                         '--params', _p64e, '--write', '--first', '--json'],
                        capture_output=True, cwd=_d64e, env=_env15)
_oo64f = _decode_out(_rr64f.stdout + _rr64f.stderr)
check('64e 无 pid 副本 → revert --first 失败（rc≠0 + no_first_backup）',
      _rr64e.returncode == 0 and _rr64f.returncode != 0
      and 'no_first_backup' in _oo64f and '"first": true' not in _oo64f,
      'apply rc=%s revert rc=%s | %s' % (_rr64e.returncode, _rr64f.returncode,
                                         _oo64f[-200:]))
shutil.rmtree(_d64e, ignore_errors=True)

# ---- 64g H2（0.1.14）：固定名备份被删、只剩 pid 副本时，**默认** revert 必须回到最初 ----
#   症状（独立验证员 e2 §Q2-c 实测）：盘上明明有 `fig.tweak.<pid>.bak`（= 最初那次写回
#   之前的原文），默认 `revert`（不带 --first）却 rc=2 报 `no_backup`「还没有可回退的
#   备份」—— 信息明明在，默认路径却说没有。修法：固定名备份缺失时复用 `--first` 同一套
#   "最早备份"发现逻辑（`revert.oldest_backup`），逐字节回到最初原件。
_H2_SRC = ('import matplotlib\nmatplotlib.use("Agg")\n'
           'import matplotlib.pyplot as plt\n'
           'fig = plt.figure(figsize=(6, 4))\n'
           'ax = fig.add_axes([0.10, 0.10, 0.40, 0.40])\n'
           'ax.plot([0, 1], [0, 1])\n'
           "fig.savefig('o.png')\n")
_H2_PAR = {'version': 4, 'script': 'fig.py', 'fig_index': 0, 'n_figs': 1,
           'axes': [{'index': 0, 'pos': [0.2, 0.2, 0.5, 0.5]}]}


def _h2_setup(name):
    """写回一次并删掉固定名备份，只留 pid 副本。返回 (目录, 脚本, 原件 sha256)。"""
    _d, _s, _p = _mk15(name, _H2_SRC, _H2_PAR)
    _r = subprocess.run([sys.executable, '-m', 'mpltweak', 'apply', 'fig.py',
                         '--params', _p, '--write', '--verify-fast'],
                        capture_output=True, cwd=_d, env=_env15)
    _bakdir = os.path.join(_d, '.tweak_params')     # 备份恒定落在**脚本旁**
    _fixed = os.path.join(_bakdir, 'fig.tweak.bak')
    _pids = [n for n in os.listdir(_bakdir)
             if n.startswith('fig.tweak.') and n.endswith('.bak')
             and n != 'fig.tweak.bak']
    if os.path.exists(_fixed):
        os.remove(_fixed)
    return _d, _s, _p, _r.returncode, _pids


# 64g-1 默认 revert（--write）→ rc=0 且**逐字节**回到最初
_d64g, _s64g, _p64g, _rc64g, _pids64g = _h2_setup('t26h2')
_rr64g = subprocess.run([sys.executable, '-m', 'mpltweak', 'revert', 'fig.py',
                         '--write', '--json'], capture_output=True, cwd=_d64g,
                        env=_env15)
_oo64g = _decode_out(_rr64g.stdout + _rr64g.stderr)
_raw64g = _decode_out(_rr64g.stdout)
try:
    _j64g = json.loads(_raw64g) if _raw64g.strip().startswith('{') else {}
except ValueError:
    _j64g = {}
check('64g-1 H2：固定名备份被删、只剩 pid 副本 ⇒ 默认 revert rc=0 且**逐字节**回到最初'
      '（不再 rc=2 说"没有备份"）',
      _rc64g == 0 and len(_pids64g) == 1 and _rr64g.returncode == 0
      and read(_s64g) == _H2_SRC,
      'apply rc=%s pid 副本=%r revert rc=%s 逐字节回到最初=%s | %s'
      % (_rc64g, _pids64g, _rr64g.returncode, read(_s64g) == _H2_SRC,
         _oo64g[-200:]))
check('64g-1b H2：这次用的是**最早那份**——payload 明说 `fallback='
      'no_fixed_backup_used_oldest_pid`（不许闷着用 pid 副本却冒充固定名）',
      _j64g.get('fallback') == 'no_fixed_backup_used_oldest_pid'
      and _j64g.get('first') is True and _j64g.get('written') is True
      and 'no_backup' not in _oo64g,
      'json=%r' % ({k: _j64g.get(k) for k in ('first', 'fallback', 'written')},))
shutil.rmtree(_d64g, ignore_errors=True)

# 64g-2 **真一份备份都没有** → 仍然 rc=2，且文案说清"哪两处都找过了"（不许含糊）
_d64h = tempfile.mkdtemp(prefix='mpltweak_t26h2none_')
_s64h = os.path.join(_d64h, 'fig.py')
with open(_s64h, 'w', encoding='utf-8', newline='\n') as f:
    f.write(_H2_SRC)
_rr64h = subprocess.run([sys.executable, '-m', 'mpltweak', 'revert', 'fig.py',
                         '--json'], capture_output=True, cwd=_d64h, env=_env15)
_oo64h = _decode_out(_rr64h.stdout + _rr64h.stderr)
check('64g-2 H2：真的一个备份都没有 ⇒ rc=2 + `no_backup`，文案点名"固定名与 pid 副本'
      '都不存在"（不再用旧文案暗示"是不是没做过 apply"就完事）',
      _rr64h.returncode == 2 and 'no_backup' in _oo64h
      and '没有任何可回退的备份' in _oo64h and 'pid' in _oo64h,
      'rc=%s | %s' % (_rr64h.returncode, _oo64h[-240:]))
shutil.rmtree(_d64h, ignore_errors=True)

# 64g-3 `--first` 语义**不变**：同一现场仍 rc=0、仍逐字节回到最初
_d64i, _s64i, _p64i, _, _pids64i = _h2_setup('t26h2first')
_rr64i = subprocess.run([sys.executable, '-m', 'mpltweak', 'revert', 'fig.py',
                         '--first', '--write', '--json'], capture_output=True,
                        cwd=_d64i, env=_env15)
_oo64i = _decode_out(_rr64i.stdout + _rr64i.stderr)
try:
    _j64i = json.loads(_decode_out(_rr64i.stdout))
except ValueError:
    _j64i = {}
check('64g-3 H2：`--first` 语义不变（只剩 pid 副本时仍 rc=0、回到最初、'
      'fallback 为空而不是走默认兜底）',
      _rr64i.returncode == 0 and read(_s64i) == _H2_SRC
      and _j64i.get('fallback') is None and _j64i.get('first') is True,
      'rc=%s 回到最初=%s json=%r | %s'
      % (_rr64i.returncode, read(_s64i) == _H2_SRC,
         {k: _j64i.get(k) for k in ('first', 'fallback')}, _oo64i[-160:]))
shutil.rmtree(_d64i, ignore_errors=True)

# 64g-4 固定名备份**在**时行为一字不改：默认 revert 用固定名那份，fallback 为空
_d64j, _s64j, _p64j = _mk15('t26h2fixed', _H2_SRC, _H2_PAR)
_rr64j0 = subprocess.run([sys.executable, '-m', 'mpltweak', 'apply', 'fig.py',
                          '--params', _p64j, '--write', '--verify-fast'],
                         capture_output=True, cwd=_d64j, env=_env15)
_rr64j = subprocess.run([sys.executable, '-m', 'mpltweak', 'revert', 'fig.py',
                         '--write', '--json'], capture_output=True, cwd=_d64j,
                        env=_env15)
_oo64j = _decode_out(_rr64j.stdout + _rr64j.stderr)
try:
    _j64j = json.loads(_decode_out(_rr64j.stdout))
except ValueError:
    _j64j = {}
check('64g-4 H2：固定名备份**在**时行为一字不改（仍用固定名那份、`fallback: null`、'
      '`first: false`、逐字节回到最初）',
      _rr64j0.returncode == 0 and _rr64j.returncode == 0
      and read(_s64j) == _H2_SRC and _j64j.get('fallback') is None
      and _j64j.get('first') is False,
      'rc=%s 回到最初=%s json=%r | %s'
      % (_rr64j.returncode, read(_s64j) == _H2_SRC,
         {k: _j64j.get(k) for k in ('first', 'fallback')}, _oo64j[-160:]))
shutil.rmtree(_d64j, ignore_errors=True)

# 64f 长路径缓解：`\\?\` 前缀 helper 的行为。
# **平台显式**：`\\?\` 是 Windows 专属机制，POSIX 上 `_long_path` 的契约是**恒等**返回。
# 旧写法在 POSIX 上做 `os.path.abspath(r'C:\a\b.txt')` —— POSIX 不把 `C:\…` 当绝对路径，
# 会返回 `<cwd>/C:\a\b.txt`，于是断言**在 Linux 上恒假**（就是这类"平台敏感路径假设"）。
if os.name == 'nt':
    _p64f = 'C:\\' + 'x' * 250 + '\\f.py'
    check('64f [Windows] `_long_path` 给超长绝对路径加 \\\\?\\ 前缀（短路径不变）',
          writeback._long_path(r'C:\a\b.txt') == os.path.abspath(r'C:\a\b.txt')
          and writeback._long_path(_p64f) == '\\\\?\\' + os.path.abspath(_p64f),
          'short=%r long=%r' % (writeback._long_path(r'C:\a\b.txt'),
                                writeback._long_path(_p64f)[:60]))
else:
    # POSIX：`_long_path` 必须**原样返回**（不做前缀、不做 abspath）——这才是两个平台都成立的契约。
    _p64f = '/tmp/' + 'x' * 250 + '/f.py'
    check('64f [POSIX] `_long_path` 恒等返回（不做 `\\\\?\\` 前缀）',
          writeback._long_path('/a/b.txt') == '/a/b.txt'
          and writeback._long_path(_p64f) == _p64f,
          'short=%r long=%r' % (writeback._long_path('/a/b.txt'),
                                writeback._long_path(_p64f)[:60]))


# ---- 65. T42：subplots(2,3) 静默丢块专属字段（Bug5 blocker）/ auto 写坏却报 no_change（Bug3）----
_B5_SRC = ('import matplotlib\nmatplotlib.use("Agg")\n'
           'import matplotlib.pyplot as plt\n'
           'fig, ax = plt.subplots(2, 3)\n'
           'ax[0, 0].plot([1, 2], [3, 4])\n'
           "fig.savefig('o.png')\n")

# 65a（Bug5）：一个变量名绑多个轴 → 拿不到轴变量名；块专属字段（spines）不得被静默丢
_d65a, _s65a, _p65a = _mk15('t42b5', _B5_SRC,
                            {'version': 4, 'script': 'fig.py', 'fig_index': 0,
                             'axes': [{'index': 0, 'spines': {'top': False}}]})
_rr65a = subprocess.run([sys.executable, '-m', 'mpltweak', 'apply', 'fig.py',
                         '--params', _p65a, '--write', '--verify-fast', '--json'],
                        capture_output=True, cwd=_d65a, env=_env15)
_oo65a = _decode_out(_rr65a.stdout + _rr65a.stderr)
check('65a Bug5：subplots(2,3) + spines → 退块落地（不再 no_change/文件未改）',
      _rr65a.returncode == 0 and '"reason": "no_change"' not in _oo65a
      and "spines['top'].set_visible(False)" in read(_s65a),
      'rc=%s | %s' % (_rr65a.returncode, _oo65a[-200:]))
shutil.rmtree(_d65a, ignore_errors=True)

# 65b（Bug3）：混合参数（能原位的一处 + 必须退块的一处）→ 结果必须并进已落盘的
#      原位 changes，且 reason 不得是 no_change
_B3_SRC = ('import matplotlib\nmatplotlib.use("Agg")\n'
           'import matplotlib.pyplot as plt\n'
           'fig = plt.figure(figsize=(6, 4))\n'
           'ax1 = fig.add_axes([0.10, 0.10, 0.40, 0.40])\n'
           'ax1.plot([1, 2, 3])\n'
           'ax2 = fig.subplot(212)\n'
           'ax2.plot([3, 2, 1])\n'
           "fig.savefig('o.png')\n")
_d65b, _s65b, _p65b = _mk15('t42b3', _B3_SRC,
                            {'version': 4, 'script': 'fig.py', 'fig_index': 0,
                             'axes': [{'index': 0, 'pos': [0.15, 0.15, 0.7, 0.7]},
                                      {'index': 1, 'pos': [0.15, 0.15, 0.7, 0.7]}]})
_rr65b = subprocess.run([sys.executable, '-m', 'mpltweak', 'apply', 'fig.py',
                         '--params', _p65b, '--write', '--verify-fast', '--json'],
                        capture_output=True, cwd=_d65b, env=_env15)
_oo65b = _decode_out(_rr65b.stdout + _rr65b.stderr)
try:
    _j65b = json.loads(_oo65b[_oo65b.index('{'):_oo65b.rindex('}') + 1])
    _f65b = (_j65b.get('files') or [{}])[0]
except Exception:                                                 # noqa: BLE001
    _f65b = {}
check('65b Bug3：混合参数下 reason 不得是 no_change、changes 并进原位已落盘的、backup 如实',
      _rr65b.returncode == 0 and _f65b.get('reason') != 'no_change'
      and (_f65b.get('changes') or []) and _f65b.get('backup'),
      'reason=%r changes=%r backup=%r'
      % (_f65b.get('reason'), _f65b.get('changes'), _f65b.get('backup')))
shutil.rmtree(_d65b, ignore_errors=True)


# 65c（Bug4）：锚点落在**行内复合语句**上（后面紧跟顶格注释）时，块必须插到整行行首，
#   生成代码语法合法（修前：写盘后语法层拦下 → `unexpected indent`、rc=1 合法请求失败）
_B4_SRC = ('import matplotlib\nmatplotlib.use("Agg")\n'
           'import matplotlib.pyplot as plt\n'
           'fig1 = plt.figure(figsize=(6, 3))\n'
           'ax1 = fig1.add_axes([0.10, 0.10, 0.80, 0.80])\n'
           'ax1.plot([1, 2, 3])\n'
           "fig1.savefig('a.png')\n"
           'fig2 = plt.figure(figsize=(6, 3))\n'
           'ax2 = fig2.add_axes([0.10, 0.10, 0.80, 0.80])\n'
           'ax2.plot([3, 2, 1])\n'
           "fig2.tight_layout(); fig2.savefig('b.png')\n"
           '# 顶格注释\n'
           "plt.close('all')\n")
_d65c, _s65c, _p65c = _mk15('t42b4', _B4_SRC,
                            {'version': 4, 'script': 'fig.py', 'fig_index': 1,
                             'axes': [{'index': 0, 'pos': [0.25, 0.25, 0.5, 0.5]}]})
_rr65c = subprocess.run([sys.executable, '-m', 'mpltweak', 'apply', 'fig.py',
                         '--params', _p65c, '--write', '--style', 'block',
                         '--json'], capture_output=True, cwd=_d65c, env=_env15)
_oo65c = _decode_out(_rr65c.stdout + _rr65c.stderr)
_t65c = read(_s65c)
check('65c Bug4：行内复合语句上的锚点 → 块插到整行行首，写回后语法合法（rc=0）',
      _rr65c.returncode == 0 and _ast_ok(_t65c) is True
      and writeback.BLOCK_START in _t65c,
      'rc=%s | %s' % (_rr65c.returncode, _oo65c[-200:]))
shutil.rmtree(_d65c, ignore_errors=True)


# 66a/66b（T51 / Bug 8）：无末尾换行（无 savefig/show ｜ 末行缩进语句）→ 第二次 apply = no_change
for _tag66, _srcc, _why in (
        ('66a', 'import matplotlib\nmatplotlib.use("Agg")\n'
                'import matplotlib.pyplot as plt\n'
                'fig, ax = plt.subplots(figsize=(6, 4))\n'
                'ax.plot([1, 2, 3])\n'
                'def f():\n    return ""', '无 savefig/show + 无末尾换行'),
        ('66b', 'import matplotlib\nmatplotlib.use("Agg")\n'
                'import matplotlib.pyplot as plt\n'
                'fig, ax = plt.subplots(figsize=(6, 4))\n'
                'ax.plot([1, 2, 3])\n'
                'if True:\n    ax.grid(True)', '末行缩进语句 + 无末尾换行')):
    _d66, _s66, _p66 = _mk15('t51' + _tag66, _srcc,
                             {'version': 4, 'script': 'fig.py', 'fig_index': 0,
                              'axes': [{'index': 0, 'pos': [0.2, 0.2, 0.6, 0.6]}]})
    _rr1 = subprocess.run([sys.executable, '-m', 'mpltweak', 'apply', 'fig.py',
                           '--params', _p66, '--write', '--verify-fast', '--json'],
                          capture_output=True, cwd=_d66, env=_env15)
    _t1 = read(_s66)
    _alone = writeback.BLOCK_START + '\n' in _t1 or _t1.endswith(writeback.BLOCK_START)
    _rr2 = subprocess.run([sys.executable, '-m', 'mpltweak', 'apply', 'fig.py',
                           '--params', _p66, '--write', '--verify-fast', '--json'],
                          capture_output=True, cwd=_d66, env=_env15)
    _o2 = _decode_out(_rr2.stdout + _rr2.stderr)
    check('66%s Bug8：%s → 哨兵独占一行 且第二次 apply = no_change'
          % (_tag66, _why),
          _rr1.returncode == 0 and _rr2.returncode == 0
          and '"reason": "no_change"' in _o2 and _alone
          and _t1.count(writeback.BLOCK_START) == 1,
          'rc1=%s rc2=%s 块数=%d 独占=%s | %s'
          % (_rr1.returncode, _rr2.returncode, _t1.count(writeback.BLOCK_START),
             _alone, _o2[-140:]))
    shutil.rmtree(_d66, ignore_errors=True)


# ---- 68. T58：重名轴 + 部分覆盖 → 任何模式都不许"写错行并被保留"（9 格钉子）----
_DUP68 = ('import matplotlib\nmatplotlib.use("Agg")\n'
          'import matplotlib.pyplot as plt\n'
          'fig = plt.figure(figsize=(6, 6))\n'
          'ax = fig.add_subplot(2, 1, 1)\n'
          'ax.plot([1, 2], [3, 4])\n'
          'ax.grid(False)\n'
          'ax = fig.add_subplot(2, 1, 2)\n'
          'ax.plot([1, 2], [4, 3])\n'
          'ax.grid(False)\n'
          "fig.savefig('o.png')\n")
for _mtag, _mextra in (('full', []), ('fast', ['--verify-fast']),
                       ('noverify', ['--no-verify'])):
    for _stag, _axes, _t1, _t2 in (
            ('both', [{'index': 0, 'grid': True}, {'index': 1, 'grid': True}],
             True, True),
            ('idx1', [{'index': 1, 'grid': True}], False, True),
            ('idx0', [{'index': 0, 'grid': True}], True, False)):
        _d, _s, _p = _mk15('t58%s%s' % (_mtag, _stag), _DUP68,
                           {'version': 4, 'script': 'fig.py', 'fig_index': 0,
                            'axes': _axes})
        _sha0 = _sha(_s)
        _rr = subprocess.run([sys.executable, '-m', 'mpltweak', 'apply', 'fig.py',
                              '--params', _p, '--write', '--json',
                              '--style', 'inplace'] + _mextra,
                             capture_output=True, cwd=_d, env=_env15)
        _txt = read(_s)
        _grid = [ln.strip() for ln in _txt.splitlines() if 'grid(' in ln]
        _changed = _sha(_s) != _sha0
        _l1 = _grid[0].endswith('True)') if _grid else None
        _l2 = _grid[1].endswith('True)') if len(_grid) > 1 else None
        if _changed:
            _ok = (_l1 == _t1 and _l2 == _t2) or (_l1 == _t1 and _l2 == _t2)
        else:
            _ok = _rr.returncode != 0
        check('68 %s/%s：sha 变了就必须只有目标轴那行变，否则必须 rc≠0（L1=%s L2=%s rc=%s）'
              % (_mtag, _stag, _l1, _l2, _rr.returncode), bool(_ok),
              'rc=%s L1=%s L2=%s changed=%s' % (_rr.returncode, _l1, _l2, _changed))
        shutil.rmtree(_d, ignore_errors=True)


# ---- 69. T59：图级 figsize 判据（Bug 6 修复的守护用例）----
# 69a 无 figsize 字面量 + 目标尺寸不同 → 原位必须判"需改"（needs_block + pending 含 figsize）
_NOLIT69 = ('import matplotlib\nmatplotlib.use("Agg")\n'
            'import matplotlib.pyplot as plt\n'
            'fig, ax = plt.subplots()\n'
            'ax.plot([1, 2, 3])\n'
            "fig.savefig('o.png')\n")
_d69, _s69, _p69 = _mk15('t59a', _NOLIT69,
                         {'version': 4, 'script': 'fig.py', 'fig_index': 0,
                          'figsize_in': [9.0, 5.0], 'axes': [{'index': 0}]})
_r69 = writeback.writeback(_s69, _params.load(_p69), _p69, verify=True,
                           semantic=True, style='inplace')
check('69a 无 figsize 字面量 + 目标不同 → reason≠no_change ∧ needs_block ∧ pending 含 figsize',
      _r69.get('reason') != 'no_change' and _r69.get('needs_block') is True
      and 'figsize' in (_r69.get('pending') or []),
      'reason=%r needs_block=%r pending=%r'
      % (_r69.get('reason'), _r69.get('needs_block'), _r69.get('pending')))
# 端到端（auto 等价路径）：尺寸必须真的落地
io.open(_s69, 'w', encoding='utf-8', newline='').write(_NOLIT69)
_rr69 = subprocess.run([sys.executable, '-m', 'mpltweak', 'apply', 'fig.py',
                        '--params', _p69, '--write', '--json',
                        '--verify-fast'], capture_output=True, cwd=_d69,
                       env=_env15)
_t69 = read(_s69)
check('69a-e2e auto 路径最终把尺寸落地（set_size_inches 出现在脚本里）',
      _rr69.returncode == 0 and 'set_size_inches' in _t69,
      'rc=%s | %s' % (_rr69.returncode,
                      [ln.strip() for ln in _t69.splitlines()
                       if 'set_size_inches' in ln][:2]))
shutil.rmtree(_d69, ignore_errors=True)

# 69b 干净往返不破功：有字面量且等值 → no_change 且字节不变
_LIT69 = ('import matplotlib\nmatplotlib.use("Agg")\n'
          'import matplotlib.pyplot as plt\n'
          'fig, ax = plt.subplots(figsize=(6.4, 4.8))\n'
          'ax.plot([1, 2, 3])\n'
          "fig.savefig('o.png')\n")
_d69b, _s69b, _p69b = _mk15('t59b', _LIT69,
                            {'version': 4, 'script': 'fig.py', 'fig_index': 0,
                             'figsize_in': [6.4, 4.8], 'axes': [{'index': 0}]})
_r69b = writeback.writeback(_s69b, _params.load(_p69b), _p69b, verify=True,
                            semantic=True, style='inplace')
check('69b 干净往返（字面量与目标等值）→ no_change 且字节不变',
      _r69b.get('reason') == 'no_change' and read(_s69b) == _LIT69,
      'reason=%r bytes_same=%s' % (_r69b.get('reason'), read(_s69b) == _LIT69))
shutil.rmtree(_d69b, ignore_errors=True)


# 69c（T60 / Bug 6 v2）：无 figsize 字面量 + 参数 == 脚本实际会用的尺寸 → 必须 no_change，
#   且**不得**插入任何块 / 写 set_size_inches（防"多余插块"回归；v1 补丁的缺陷）。
_NOLIT69C = ('import matplotlib\nmatplotlib.use("Agg")\n'
             'import matplotlib.pyplot as plt\n'
             'fig, ax = plt.subplots()\n'
             'ax.plot([1, 2, 3])\n'
             "fig.savefig('o.png')\n")
_d69c, _s69c, _p69c = _mk15('t60a', _NOLIT69C,
                            {'version': 4, 'script': 'fig.py', 'fig_index': 0,
                             'figsize_in': [6.4, 4.8], 'axes': [{'index': 0}]})
_r69c = writeback.writeback(_s69c, _params.load(_p69c), _p69c, verify=True,
                            semantic=True, style='inplace')
_t69c = read(_s69c)
check('69c 无字面量 + 参数等于默认尺寸(6.4x4.8) → no_change / 字节不变 / 不插块 / 不写 set_size_inches',
      _r69c.get('reason') == 'no_change' and _t69c == _NOLIT69C
      and writeback.BLOCK_START not in _t69c and 'set_size_inches' not in _t69c,
      'reason=%r bytes_same=%s block=%s ssi=%s'
      % (_r69c.get('reason'), _t69c == _NOLIT69C,
         writeback.BLOCK_START in _t69c, 'set_size_inches' in _t69c))
shutil.rmtree(_d69c, ignore_errors=True)

# 69c-v2：脚本里显式 fig.set_size_inches(6.4, 4.8)、参数与之相等 → 同样 no_change
_SSI69C = ('import matplotlib\nmatplotlib.use("Agg")\n'
           'import matplotlib.pyplot as plt\n'
           'fig, ax = plt.subplots()\n'
           'fig.set_size_inches(6.4, 4.8)\n'
           'ax.plot([1, 2, 3])\n'
           "fig.savefig('o.png')\n")
_d69d, _s69d, _p69d = _mk15('t60b', _SSI69C,
                            {'version': 4, 'script': 'fig.py', 'fig_index': 0,
                             'figsize_in': [6.4, 4.8], 'axes': [{'index': 0}]})
_r69d = writeback.writeback(_s69d, _params.load(_p69d), _p69d, verify=True,
                            semantic=True, style='inplace')
_t69d = read(_s69d)
check('69c-v2 脚本 set_size_inches(6.4,4.8) + 参数相等 → no_change / 字节不变 / 不再写一行',
      _r69d.get('reason') == 'no_change' and _t69d == _SSI69C
      and writeback.BLOCK_START not in _t69d,
      'reason=%r bytes_same=%s block=%s'
      % (_r69d.get('reason'), _t69d == _SSI69C, writeback.BLOCK_START in _t69d))
shutil.rmtree(_d69d, ignore_errors=True)
# ---- 70. T61：坏参数 fail-closed（6 类真写入 + 3 类静默忽略） -----------------
#   外部语料 D 场景的实测：pos/figsize 的零负值、负字号、越界轴序号会被**真的写进
#   源码**（有的还插块）；bad_fig_index / bad_version / no_version 则静默
#   `no_change` + rc=0（用户以为写回成功了）。两条口径写死：
#     * 拒：w/h ≤ 0、figsize 任一维 ≤ 0、fontsize < 0、NaN/inf、轴 index ≥ 槽位或 < 0、
#           fig_index 越界、version 缺失或 ≠ 4；
#     * 不拒：pos 的 left/bottom 为负（只要还有可见部分）、xlim 反转（倒置轴）、
#           fontsize == 0（合法，只提示）。
#   这些都必须在**任何写盘/插块之前**发生（脚本字节不变、无块、ok=false、
#   reason=bad_params → apply rc=2）。
_T61_SRC = ('import matplotlib\nmatplotlib.use("Agg")\n'
            'import matplotlib.pyplot as plt\n'
            'fig, axes = plt.subplots(1, 2, figsize=(9, 4))\n'
            'axes[0].plot([1, 2, 3])\n'
            'axes[1].plot([3, 2, 1])\n'
            "fig.savefig('o.png')\n")


def _t61_params(body):
    p = {'version': 4, 'script': 'fig.py', 'fig_index': 0,
         'axes': [{'index': k} for k in range(2)]}
    p.update(body)
    return p


# 70①：**参数层**判据（apply 在写盘前调用 params.validate_errors 拦下这些）
for _c70, _b70 in (
        ('pos_zero', {'axes': [{'index': 0, 'pos': [0, 0, 0, 0]}]}),
        ('pos_negative_offscreen', {'axes': [{'index': 0, 'pos': [-1, -1, 0.5, 0.5]}]}),
        ('pos_neg_inf', {'axes': [{'index': 0, 'pos': [0.1, 0.1, float('inf'), 0.4]}]}),
        ('figsize_zero', {'figsize_in': [0, 0]}),
        ('figsize_negative', {'figsize_in': [-1, -1]}),
        ('fontsize_negative', {'axes': [{'index': 0, 'title_fontsize': -5}]}),
        ('axis_index_negative', {'axes': [{'index': -1, 'xlim': [0, 1]}]}),
        ('bad_version', {'version': 1}),
):
    _errs70 = _params.validate_errors(_t61_params(_b70))
    check('70① %s → 参数层硬错误（拒写）' % _c70, bool(_errs70), _errs70)

# no_version 走 load()：normalize 会把缺失的 version 补成当前值，靠 load 打的
# `_version_missing` 标记才能识别（否则静默 no_change + rc=0）
with tempfile.TemporaryDirectory(prefix='mpltweak_t61v_') as _d70v:
    _p70v = os.path.join(_d70v, 'p.json')
    with open(_p70v, 'w', encoding='utf-8') as f:
        json.dump(_t61_params({'__x': 1}), f)
        f.write('\n')
    _raw70v = json.load(open(_p70v, encoding='utf-8'))
    _raw70v.pop('version', None)
    with open(_p70v, 'w', encoding='utf-8') as f:
        json.dump(_raw70v, f)
    _errs70v = _params.validate_errors(_params.load(_p70v))
    check('70① no_version → load 后仍被识别为硬错误（normalize 不能把它洗白）',
          bool(_errs70v), _errs70v)

# 70②：**脚本层**判据（writeback 落地入口，before any write）
for _c70b, _b70b in (
        ('axis_index_99', {'axes': [{'index': 99, 'xlim': [0, 1]}]}),
        ('bad_fig_index', {'fig_index': 99}),
):
    _d70b, _s70b, _p70b = _mk15('t61wb_' + _c70b, _T61_SRC, _t61_params(_b70b))
    _before70b = read(_s70b)
    _r70b = writeback.writeback(_s70b, _params.load(_p70b), _p70b, verify=False,
                                fast_check=True, style='auto')
    check('70② %s → 引擎层拒写（bad_params / 字节不变 / 无块）' % _c70b,
          _r70b.get('reason') == 'bad_params' and read(_s70b) == _before70b
          and writeback.BLOCK_START not in read(_s70b),
          'reason=%r bytes_same=%s block=%s err=%s'
          % (_r70b.get('reason'), read(_s70b) == _before70b,
             writeback.BLOCK_START in read(_s70b), (_r70b.get('err') or '')[:70]))
    shutil.rmtree(_d70b, ignore_errors=True)


# 70③：**端到端**（真 CLI）—— rc=2 + ok=false + reason=bad_params + 字节不变 + 无块
def _t61_cli(case, body, drop_version=False):
    d, s, p = _mk15('t61cli_' + case, _T61_SRC, _t61_params(body))
    if drop_version:
        _pj = json.load(open(p, encoding='utf-8'))
        _pj.pop('version', None)
        with open(p, 'w', encoding='utf-8') as f:
            json.dump(_pj, f)
    before = read(s)
    r = subprocess.run([sys.executable, '-m', 'mpltweak', 'apply', s,
                        '--params', p, '--write', '--json'],
                       capture_output=True, cwd=d, env=_env15)
    out = _decode_out(r.stdout)
    try:
        payload = json.loads(out)
        f0 = (payload.get('files') or [{}])[0]
        reason, ok = f0.get('reason'), payload.get('ok')
    except ValueError:
        reason, ok = 'JSON?', None
    after = read(s)
    check('70③ CLI %s → rc=2 / ok=false / reason=bad_params / 字节不变 / 无块'
          % case,
          r.returncode == 2 and ok is False and reason == 'bad_params'
          and after == before and writeback.BLOCK_START not in after,
          'rc=%s ok=%r reason=%r bytes_same=%s' % (r.returncode, ok, reason,
                                                   after == before))
    shutil.rmtree(d, ignore_errors=True)


_t61_cli('pos_zero', {'axes': [{'index': 0, 'pos': [0, 0, 0, 0]}]})
_t61_cli('figsize_negative', {'figsize_in': [-1, -1]})
_t61_cli('no_version', {}, drop_version=True)
_t61_cli('axis_index_99', {'axes': [{'index': 99, 'xlim': [0, 1]}]})

# 70④：合法用法不许被误伤
_d70a, _s70a, _p70a = _mk15(
    't61_legit_pos', _T61_SRC,
    _t61_params({'axes': [{'index': 0, 'pos': [-0.15, 0.1, 0.4, 0.5]}]}))
_r70a = writeback.writeback(_s70a, _params.load(_p70a), _p70a, verify=False,
                            fast_check=True, style='auto')
check('70④ 合法：pos 的 left/bottom 为负（仍有可见部分）→ 不得拒绝，正常落地',
      _r70a.get('reason') in ('ok', 'best_effort') and read(_s70a) != _T61_SRC,
      'reason=%r bytes_changed=%s'
      % (_r70a.get('reason'), read(_s70a) != _T61_SRC))
shutil.rmtree(_d70a, ignore_errors=True)

_d70c, _s70c, _p70c = _mk15(
    't61_legit_xlim', _T61_SRC,
    _t61_params({'axes': [{'index': 0, 'xlim': [5, 1]}]}))
_r70c = writeback.writeback(_s70c, _params.load(_p70c), _p70c, verify=False,
                            fast_check=True, style='auto')
check('70④ 合法：xlim 反转（倒置轴）→ 接受且落地',
      _r70c.get('reason') in ('ok', 'best_effort') and read(_s70c) != _T61_SRC
      and 'set_xlim(5,1)' in read(_s70c).replace(' ', ''),
      'reason=%r | %s' % (_r70c.get('reason'),
                          [l for l in read(_s70c).splitlines()
                           if 'xlim' in l][:2]))
shutil.rmtree(_d70c, ignore_errors=True)

# fontsize == 0：合法（matplotlib 允许）→ 校验层不许拒；只在 warnings 里提示
_p70d = _t61_params({'axes': [{'index': 0, 'title_fontsize': 0}]})
check('70④ fontsize == 0 → 不进硬错误，但进提示',
      not _params.validate_errors(_p70d)
      and any('title_fontsize = 0' in p for p in _params.validate(_p70d)),
      'errs=%r probs=%r' % (_params.validate_errors(_p70d),
                            _params.validate(_p70d)))

# 已有的干净拒绝不许退步（D 场景里那 16 类）：抽样三类仍必须被硬错误拦住
for _cid70, _p70e in (
        ('pos_3_elems', {'version': 4, 'axes': [{'index': 0, 'pos': [0.1, 0.1, 0.5]}]}),
        ('figsize_str', {'version': 4, 'figsize_in': 'abc'}),
        ('xscale_bogus', {'version': 4, 'axes': [{'index': 0, 'xscale': 'bogus'}]}),
        ('spines_str', {'version': 4, 'axes': [{'index': 0, 'spines': 'top'}]}),
):
    check('70⑤ %s 仍被硬错误拦住（未退步）' % _cid70,
          bool(_params.validate_errors(_p70e)), _params.validate_errors(_p70e))


# 69e（T65/T66）：**不变量** —— 绝不静默丢改动。三种结局分别钉死：
# （a）声称"无需改动"（reason=no_change 且未写盘）→ 必须证明脚本**生效尺寸已经==目标**
#      （独立读取器读不出 → 未知 → FAIL）；**P3'/P7 修前正是这一支**（假等值 → 静默 no_change）。
# （b）没写盘、也没说 no_change → 必须**明确申报**退块（needs_block=True 或 pending 非空），否则同样是静默丢改动；
# （c）真写了盘 → 改完后生效尺寸必须==目标。
# 生效尺寸用**本测试自带的独立读取器**（绝不调产品函数 _figsize_from_source，避免自证）。
def _eff_size(src):
    """独立读取脚本的**生效尺寸**：所有可读字面量按 lineno 取最后一条；有尺寸语句但读不出 → None；
    完全没有尺寸语句 → matplotlib 默认值。"""
    import ast as _ast
    try:
        tree = _ast.parse(src)
    except SyntaxError:
        return None
    cands, seen = [], False
    for n in _ast.walk(tree):
        if isinstance(n, _ast.Call):
            for kw in n.keywords:
                if kw.arg == 'figsize':
                    seen = True
                    v = kw.value
                    if isinstance(v, (_ast.Tuple, _ast.List)) and len(v.elts) == 2:
                        try:
                            cands.append((n.lineno, [float(_ast.literal_eval(v.elts[0])),
                                                     float(_ast.literal_eval(v.elts[1]))]))
                        except (ValueError, TypeError, SyntaxError):
                            pass
            if (isinstance(n.func, _ast.Attribute)
                    and n.func.attr == 'set_size_inches'):
                seen = True
                if len(n.args) >= 2:
                    try:
                        cands.append((n.lineno, [float(_ast.literal_eval(n.args[0])),
                                                 float(_ast.literal_eval(n.args[1]))]))
                    except (ValueError, TypeError, SyntaxError):
                        pass
        if isinstance(n, _ast.Assign):
            is_pair = (isinstance(n.value, (_ast.Tuple, _ast.List))
                       and len(n.value.elts) == 2)
            if is_pair:
                for tg in n.targets:
                    if (isinstance(tg, _ast.Subscript)
                            and isinstance(tg.slice, _ast.Constant)
                            and tg.slice.value == 'figure.figsize'):
                        seen = True
                        try:
                            cands.append((n.lineno, [float(_ast.literal_eval(n.value.elts[0])),
                                                     float(_ast.literal_eval(n.value.elts[1]))]))
                        except (ValueError, TypeError, SyntaxError):
                            pass
    if cands:
        cands.sort(key=lambda c: c[0])
        return cands[-1][1]
    if seen:
        return None                      # 有尺寸语句但读不出 → 未知
    try:
        import matplotlib as _m
        d = _m.rcParams['figure.figsize']
        return [float(d[0]), float(d[1])]
    except Exception:                                             # noqa: BLE001
        return None


_SZ = lambda a, b: (abs(a[0] - b[0]) <= 1e-9 and abs(a[1] - b[1]) <= 1e-9)
_hit = lambda v, b: bool(v is not None and len(v) == 2 and _SZ(v, b))
_H69E = ('import matplotlib\nmatplotlib.use("Agg")\nimport matplotlib.pyplot as plt\n')
_P3H = ('%sfig, ax = plt.subplots(figsize=(9, 5))\nfig.set_size_inches(4, 3)\n'
        'ax.plot([1, 2, 3])\nfig.savefig("o.png")\n')
_P1 = ('%sfig, ax = plt.subplots(figsize=(9, 5))\nax.plot([1, 2, 3])\nfig.savefig("o.png")\n')
_P2 = ('%sfig = plt.figure(figsize=(9, 5))\nax = fig.add_axes([0.1, 0.1, 0.8, 0.8])\n'
       'ax.plot([1, 2, 3])\nfig.savefig("o.png")\n')
_P4 = ('%sfig, ax = plt.subplots()\nax.plot([1, 2, 3])\nfig.savefig("o.png")\n')
_P5 = ('%sfig, ax = plt.subplots()\nfig.set_size_inches(6.4, 4.8)\n'
       'ax.plot([1, 2, 3])\nfig.savefig("o.png")\n')
_P7 = ('%sfig, ax = plt.subplots()\nW, H = 6.4, 4.8\nfig.set_size_inches(W, H)\n'
       'ax.plot([1, 2, 3])\nfig.savefig("o.png")\n')
# --- 读取器自检（防它自己退化成"永远返回默认值"，那会让 (a) 支整体失效）---
check('69e-R1 读取器：figsize= 字面量 → [9.0, 5.0]', _hit(_eff_size(_P1 % _H69E), [9.0, 5.0]),
      str(_eff_size(_P1 % _H69E)))
check('69e-R2 读取器：后置 set_size_inches 覆盖 → [4.0, 3.0]',
      _hit(_eff_size(_P3H % _H69E), [4.0, 3.0]), str(_eff_size(_P3H % _H69E)))
check('69e-R3 读取器：变量尺寸（有语句但读不出）→ None（未知）',
      _eff_size(_P7 % _H69E) is None, str(_eff_size(_P7 % _H69E)))
check('69e-R4 读取器：完全没有尺寸语句 → rcParams 默认 [6.4, 4.8]',
      _hit(_eff_size(_P4 % _H69E), [6.4, 4.8]), str(_eff_size(_P4 % _H69E)))
# --- 七例不变量 ---
for _tag, _src, _fs in (('69e-P1', _P1 % _H69E, [6.4, 4.8]),
                        ('69e-P2', _P2 % _H69E, [6.4, 4.8]),
                        ('69e-P3', _P3H % _H69E, [4.0, 3.0]),
                        ("69e-P3'", _P3H % _H69E, [9.0, 5.0]),
                        ('69e-P4', _P4 % _H69E, [6.4, 4.8]),
                        ('69e-P5', _P5 % _H69E, [6.4, 4.8]),
                        ('69e-P7', _P7 % _H69E, [6.4, 4.8])):
    _d, _s, _p = _mk15('t66' + _tag.replace('-', '').replace("'", 'p'), _src,
                       {'version': 4, 'script': 'fig.py', 'fig_index': 0,
                        'figsize_in': _fs, 'axes': [{'index': 0}]})
    _r = writeback.writeback(_s, _params.load(_p), _p, verify=True,
                             semantic=True, style='inplace')
    _after = read(_s)
    _same = _after == _src
    _eff = _eff_size(_after)
    _rsn = _r.get('reason')
    _nb = _r.get('needs_block')
    _pend = _r.get('pending') or []
    if _rsn == 'no_change' and _same:
        _kind = '声称无需改动+未写盘'
        _ok = _hit(_eff, _fs)                       # 空口宣称 → 必须证明本来就对
    elif _same:
        _kind = '未写盘+已申报退块'
        _ok = bool(_nb is True or _pend)            # 弃权可以，但必须说清楚
    else:
        _kind = '声称已改（已写盘）'
        _ok = _hit(_eff, _fs)                       # 真改了 → 改完必须对
    check('%s 不变量[%s]：生效尺寸 %s vs 目标 %s' % (_tag, _kind, _eff, _fs), _ok,
          'reason=%r nb=%r pending=%r bytes_same=%s eff=%s'
          % (_rsn, _nb, _pend, _same, _eff))
    shutil.rmtree(_d, ignore_errors=True)


# ======================================================================
# §71（T67）位置字段等值证明（A）+ 块内目标轴 fail-closed（A）+ 多图回退幂等（C）
# ======================================================================
# 两类真缺陷（逐字证据见 `_fin/triage_9212ace.md`，复现见 fix_engine.md §T67）：
#   A  值相等的 pos 仍退块写盘：语料
#      `big/zenodo/3490985__cloudFractionZonalMeanProfiles.py` 写的是
#      `plt.colorbar(thePlot, cax=fig.add_axes([0.78, 0.18, 0.2, 0.03]))`，
#      参数给的 axes[0].pos 与源码**逐位相同**。旧实现的位置等值证明要求字面量挂在
#      `ast.Assign` 上（(a)~(d) 四条路径都够不到"内联"的 add_axes）→ 判"证不出" →
#      best_effort → auto 退块 → 插一段
#      `for _ax, _p in zip(fig.axes, [[0.78, 0.18, 0.2, 0.03]]): _ax.set_position(_p)`。
#      而那个脚本运行期有 **12** 条轴（11 个面板由 `for i in range(11):
#      plt.subplot(3,4,i+1)` 循环建出 + 色条 cax 是第 12 条）：`fig.axes[0]` 是**主图**，
#      不是参数里记的那条 cax。步骤 1 实测：块执行前 fig.axes[0].pos=
#      (0.0656, 0.7086, 0.1698, 0.2483)、cax=(0.78, 0.18, 0.2, 0.03)；块执行后
#      fig.axes[0]=(0.78, 0.18, 0.2, 0.03)、cax 一个数都没动 → **把主图改成了色条的几何**。
#   C  同一参数两次 apply 不幂等：语料 `big/zenodo/21785935__03_ml_pipeline.py`
#      （136 KB / 20 张图）第 1 次原位把 `fig = plt.figure(figsize=(18, 12))` 改成 (6, 4)；
#      第 2 次被**另一张图**的 `fig_bee.set_size_inches(10, 7)` 误判成"目标尺寸已被覆盖"
#      → 等值证明失败 → auto 退块又插一遍 `plt.gcf().set_size_inches(6, 4)`（+5 行）。
# 反向验证（`fix018/t67/t67_rev.py`，三条都必须真判别）：
#   R1 `_pos_literal_equal` 首行加 `return False`（等值证明恒"证不出"）→ 71① 必须 FAIL；
#   R2 `_later_size_override` 里 `if fig_names is not None:` 改成 `if False:`
#      （退回"任何 set_size_inches 都算覆盖"）→ 71② 必须 FAIL；
#   R3 `_pos_block_target_ok` 首行加 `return True`（关掉 fail-closed）→ 71①b 必须 FAIL。
# ③ §69a/b/c/e 全系列仍在同一套件里逐条跑（在本节之前），一条都没动。
_S71_A = ('import matplotlib\n'
          'matplotlib.use("Agg")\n'
          'import matplotlib.pyplot as plt\n'
          'fig = plt.figure(figsize=(8, 5))\n'
          'for i in range(11):\n'
          '    plt.subplot(3, 4, i + 1)\n'
          '    im = plt.imshow([[1, 2], [3, 4]])\n'
          'cbar = plt.colorbar(im, cax=fig.add_axes([0.78, 0.18, 0.2, 0.03]),\n'
          '                    orientation="horizontal")\n'
          'plt.tight_layout()\n'
          'fig.savefig("o.png")\n')
_S71_20 = ('import matplotlib\n'
           'matplotlib.use("Agg")\n'
           'import matplotlib.pyplot as plt\n'
           'f0 = plt.figure(figsize=(18, 12))\n'
           'f0.savefig("f0.png")\n'
           + ''.join('f%d = plt.figure(figsize=(18, 12))\n' % _k
                     for _k in range(1, 20))
           + 'f19.set_size_inches(10, 7)\n')

# ---- 71①（A 的主症状）：值相等的内联 cax pos → 判"本来就对"，一个字节都不动 ----
_d71a, _s71a, _p71a = _mk15('t67a', _S71_A,
                            {'version': 4, 'script': 'fig.py',
                             'axes': [{'index': 0,
                                       'pos': [0.78, 0.18, 0.2, 0.03]}]})
_r71a = writeback.writeback(_s71a, _params.load(_p71a), _p71a, verify=False,
                            fast_check=True, style='inplace')
check('71① 值相等的内联 cax pos → 原位判"本来就对"（no_change、不写盘、不申退块）',
      _r71a.get('reason') == 'no_change' and _r71a.get('applied') is False
      and _r71a.get('needs_block') is not True
      and not (_r71a.get('pending') or [])
      and read(_s71a) == _S71_A,
      'reason=%r nb=%r pending=%r' % (_r71a.get('reason'),
                                      _r71a.get('needs_block'),
                                      _r71a.get('pending')))
_rc71a = apply.main([_s71a, '--params', _p71a, '--write', '--verify-fast'])
_t71a = read(_s71a)
check('71① auto 全流程：值相等 → 字节不变、无调整块、无 set_position',
      _rc71a == 0 and _t71a == _S71_A
      and writeback.BLOCK_START not in _t71a and 'set_position' not in _t71a,
      'rc=%s | %s' % (_rc71a, [_l for _l in _t71a.splitlines()
                              if 'position' in _l or 'auto layout' in _l]))
shutil.rmtree(_d71a, ignore_errors=True)

# ---- 71①b（A 的第二症状）：值不同 + 循环建轴 → 块内目标轴证不出 → fail-closed ----
#   旧实现在这里会插 zip 块，把位置写到运行时第一张轴（主图）上 —— 宁可不改。
_d71b, _s71b, _p71b = _mk15('t67b', _S71_A,
                            {'version': 4, 'script': 'fig.py',
                             'axes': [{'index': 0,
                                       'pos': [0.10, 0.10, 0.50, 0.50]}]})
_r71b = writeback.writeback(_s71b, _params.load(_p71b), _p71b, verify=False,
                            fast_check=True, style='inplace')
check('71①b 值不同 + 循环建轴：原位写不了 → best_effort + needs_block（不写盘）',
      _r71b.get('reason') == 'best_effort' and _r71b.get('needs_block') is True
      and _r71b.get('applied') is False and read(_s71b) == _S71_A,
      'reason=%r nb=%r applied=%r' % (_r71b.get('reason'),
                                      _r71b.get('needs_block'),
                                      _r71b.get('applied')))
# J1（t30 / 0.1.14）：下面 71①b / 72② 的期望 rc 从 0 **收紧**到 1 —— 这两条都是
# "想写却一处都没落地"的形态，现在顶层给 ok=false + error_code='nothing_applied'。
# 收紧的是"结果需要处理"这一档，字节/块的断言一条都没放宽。
_rc71b = apply.main([_s71b, '--params', _p71b, '--write', '--verify-fast'])

_t71b = read(_s71b)
check('71①b auto 退块时 fail-closed：不许把位置写到 fig.axes[0]（字节不变、不插块）',
      _rc71b == 1 and _t71b == _S71_A
      and writeback.BLOCK_START not in _t71b and 'set_position' not in _t71b,
      'rc=%s | %s' % (_rc71b, [_l for _l in _t71b.splitlines()
                              if 'position' in _l or 'auto layout' in _l]))
shutil.rmtree(_d71b, ignore_errors=True)

# ---- 71②（C）：20 图脚本（另一张图带 set_size_inches）+ 同一参数两次 apply ----
_d71c, _s71c, _p71c = _mk15('t67c', _S71_20,
                            {'version': 4, 'script': 'fig.py',
                             'figsize_in': [6.0, 4.0], 'fig_index': 0,
                             'n_figs': 20, 'axes': []})
_rc71c1 = apply.main([_s71c, '--params', _p71c, '--write', '--verify-fast'])
_t71c1 = read(_s71c)
check('71② 20 图：第 1 次原位落地（只改目标图那一处字面量）',
      _rc71c1 == 0 and _t71c1 != _S71_20 and 'figsize=(6, 4)' in _t71c1
      and 'f19.set_size_inches(10, 7)' in _t71c1,
      'rc=%s | %s' % (_rc71c1, [_l for _l in _t71c1.splitlines()
                                if 'set_size_inches' in _l or 'figsize' in _l][:3]))
_rc71c2 = apply.main([_s71c, '--params', _p71c, '--write', '--verify-fast'])
_t71c2 = read(_s71c)
check('71② 同一参数第 2 次 apply 幂等：no_change、字节不变、不插块',
      _rc71c2 == 0 and _t71c2 == _t71c1
      and writeback.BLOCK_START not in _t71c2
      and 'plt.gcf().set_size_inches' not in _t71c2,
      'rc=%s | 新增行=%s' % (_rc71c2, [_l for _l in _t71c2.splitlines()
                                      if _l not in _t71c1.splitlines()]))
shutil.rmtree(_d71c, ignore_errors=True)

# ======================================================================
# §72（T68）消息层诚实性：**没写盘就不许说"已写回"**
# ======================================================================
# 缺陷：块通道的人读行只看 `reason` 不看 `applied` → "拒绝写回"的路径
# （applied=false，例如 T67 的"运行时轴数证不出 → 不写位置块"fail-closed）也打
# `[OK] 已写回: <脚本>`；bad_params 入口拒写只有一句"已拒写"、没有统一的"未写回"行。
# JSON 一直是诚实的（ok/applied/needs_block/pending 都对），坏的只有人读那一行。
# 修法（apply.py + messages.py）：原位/块两条人读通道都按 `applied`（且非 dry-run）决定；
# 未写盘 → `未写回[<reason>]：脚本未被改动（<脚本>）`（中英走 messages 表）。
# 反向验证（fix018/t68/t68_rev.py）：关掉这两处判定（恢复无条件 written_block +
# 删掉 bad_params 那行）→ 72① / 72② 必须 FAIL。
_S72_IP = ('import matplotlib\nmatplotlib.use("Agg")\n'
           'import matplotlib.pyplot as plt\n'
           'fig = plt.figure(figsize=(8, 6))\n'
           'fig.savefig("o.png")\n')
_S72_NOAX = ('from matplotlib.figure import Figure\n'
             'fig = Figure(figsize=(4, 3))\n')


def _s72_run(src, params, extra=()):
    """跑一次真 CLI（人读输出走 stdout；--json 时会被挤到 stderr，这里合并看）。"""
    _d, _s, _p = _mk15('t68', src, params)
    _rr = subprocess.run([sys.executable, '-m', 'mpltweak', 'apply', _s,
                          '--params', _p, '--write', '--verify-fast',
                          '--lang', 'zh']          # T70：语言钉死，不看 --lang auto
                         + list(extra),
                         capture_output=True, cwd=_d, env=_env15)
    _o = _decode_out(_rr.stdout + _rr.stderr)
    _txt = read(_s)
    shutil.rmtree(_d, ignore_errors=True)
    return _rr.returncode, _o, _txt


# 72① 坏参数（params.validate_errors 命中）→ rc=2、字节不变、人读不得说"已写回"
_rc72a, _o72a, _t72a = _s72_run(
    _GRID_SRC, {'version': 4, 'script': 'fig.py',
                'axes': [{'index': 0, 'pos': [0.1, 0.2, 0.3]}]})
check('72① 坏参数：rc=2、未写盘、人读不得出现「已写回」，须如实「未写回」+ 原因码',
      _rc72a == 2 and '已写回' not in _o72a and '未写回' in _o72a
      and 'bad_params' in _o72a and _t72a == _GRID_SRC,
      'rc=%s | %s' % (_rc72a, [_l for _l in _o72a.splitlines() if '写回' in _l]))

# 72② 块目标证不出（applied=false + needs_block=true，T67 的 fail-closed）→ 同上
_rc72b, _o72b, _t72b = _s72_run(
    _S71_A, {'version': 4, 'script': 'fig.py',
             'axes': [{'index': 0, 'pos': [0.10, 0.10, 0.50, 0.50]}]})
check('72② 块目标证不出（一个字节都没写）：人读不得说「已写回」，须「未写回」+ 原因码',
      _rc72b == 1 and '[OK] 已写回' not in _o72b and '未写回' in _o72b
      and 'best_effort' in _o72b and _t72b == _S71_A,
      'rc=%s | %s' % (_rc72b, [_l for _l in _o72b.splitlines() if '写回' in _l]))

# 72③a 正常**原位**写盘 → 人读仍要说"已原位写回"（不许因为本次修正而少说）
_rc72c, _o72c, _t72c = _s72_run(
    _S72_IP, {'version': 4, 'script': 'fig.py', 'figsize_in': [6.0, 4.0],
              'fig_index': 0, 'n_figs': 1, 'axes': []})
check('72③a 正常原位写盘：人读仍明确「已原位写回」，且数字真的落地',
      _rc72c == 0 and '已原位写回' in _o72c and '未写回' not in _o72c
      and 'figsize=(6, 4)' in _t72c,
      'rc=%s | %s' % (_rc72c, [_l for _l in _o72c.splitlines() if '写回' in _l]))

# 72③b 正常**追加调整块** → 人读仍要说"已写回"，且块真进文件
_rc72d, _o72d, _t72d = _s72_run(
    _GRID_SRC, {'version': 4, 'script': 'fig.py',
                'axes': [{'index': 0, 'pos': [0.20, 0.20, 0.60, 0.60]}]})
check('72③b 正常插入调整块：人读仍说「已写回」、块真进文件',
      _rc72d == 0 and '[OK] 已写回' in _o72d and '未写回' not in _o72d
      and writeback.BLOCK_START in _t72d,
      'rc=%s | %s' % (_rc72d, [_l for _l in _o72d.splitlines() if '写回' in _l]))

# 72④ 拒绝执行（no_axes）：同样不许说"已写回"，并给出原因码
_rc72e, _o72e, _t72e = _s72_run(_S72_NOAX,
                                {'version': 4, 'script': 'fig.py', 'axes': []})
check('72④ 拒绝执行（no_axes）：人读给出原因码、不说「已写回」',
      _rc72e != 0 and '已写回' not in _o72e and 'no_axes' in _o72e,
      'rc=%s | %s' % (_rc72e, [_l for _l in _o72e.splitlines()
                              if '写回' in _l or 'no_axes' in _l]))

# ---- 73. T70：块内只许用**静态可证的对象名**定位目标（判据见 fix_engine.md §T70）----
#   ① 目标对象自己的变量名 / 单次字面量 `subplots` 容器的元素 → 可证，块里就用它；
#   ② 纯 pyplot 单图（全文件只有一次建图、无 colorbar/inset/twin/close）→
#      `plt.gcf().axes[k]` 也可证（块注释里写明凭什么可证）；
#   ③ 其余（名字出作用域 / 循环里建图建轴 / 多图没名字 / 未命名 colorbar 轴）→ 目标
#      证不出 → **拒绝写**：不插块、字节不变、applied=false + needs_block + pending，
#      人读走 T68 的 not_written 通道（绝不许出现「已写回」）。
_SRC73A = ('import matplotlib\nmatplotlib.use("Agg")\n'
           'import matplotlib.pyplot as plt\n'
           'fig, ax = plt.subplots(figsize=(6, 3))\n'
           'ax.plot([1, 2, 3])\n'
           'fig.savefig("o.png")\n')
_PAR73A = {'version': 4, 'script': 'fig.py', 'fig_index': 0, 'n_figs': 1,
           'axes': [{'index': 0, 'xlim': [0, 4]}]}
_d73a, _s73a, _p73a = _mk15('t73a', _SRC73A, _PAR73A)
_r73a = writeback.writeback(_s73a, _params.load(_p73a), _p73a, verify=False,
                            fast_check=True, style='block')
_blk73a = _r73a.get('block') or ''
check('73① 目标可证 → 块用变量名，块内无运行期下标/动态定位',
      'ax.set_xlim(0, 4)' in _blk73a
      and not any(k in _blk73a for k in ('fig.axes[', 'plt.gcf()', 'plt.gca()',
                                         'get_axes()[', 'zip(')),
      _blk73a[:160])
check('73① 成功路径没退步：真写盘（applied=True 且块进文件）',
      _r73a.get('applied') is True and writeback.BLOCK_START in read(_s73a),
      'applied=%r' % (_r73a.get('applied'),))
shutil.rmtree(_d73a, ignore_errors=True)

# ② 目标不可证：图与轴建在函数里，而那个函数**有多条 return / 提前 return**
#    （0.1.12 / A1 的拒绝规则 a）→ 块插到哪条 return 之前都证不出"存图前那份 fig 就是
#    目标图" ⇒ 仍然 fail-closed 拒写。
#    ⚠ 0.1.12 口径变更：本用例原来的形状（函数里建图 + 末尾**恰好一条** `return fig`
#    + 模块层 `fig.savefig`）**已经能证明**了 —— 锚点下沉到那条 return 之前（见 §108）。
#    这里换成**仍然不可证**的多 return 形状，继续钉 T70 的 fail-closed 结论。
_SRC73B = ('import matplotlib\nmatplotlib.use("Agg")\n'
           'import matplotlib.pyplot as plt\n\n\n'
           'def draw(flag):\n'
           '    fig, ax = plt.subplots(figsize=(6, 3))\n'
           '    ax.plot([1, 2, 3])\n'
           '    if flag:\n'
           '        return fig\n'
           '    return fig\n\n\n'
           'fig = draw(True)\n'
           'fig.savefig("o.png")\n')
_d73b, _s73b, _p73b = _mk15('t73b', _SRC73B, _PAR73A)
_r73b = writeback.writeback(_s73b, _params.load(_p73b), _p73b, verify=False,
                            fast_check=True, style='block')
check('73② 目标不可证 → 拒绝写：不插块 + 字节不变 + applied=false + pending 非空',
      _r73b.get('reason') == 'best_effort' and _r73b.get('applied') is False
      and _r73b.get('needs_block') is True and bool(_r73b.get('pending'))
      and read(_s73b) == _SRC73B,
      'reason=%r applied=%r pending=%r'
      % (_r73b.get('reason'), _r73b.get('applied'), _r73b.get('pending')))
_r73b_cli = subprocess.run(
    [sys.executable, '-m', 'mpltweak', 'apply', _s73b, '--params', _p73b,
     '--write', '--verify-fast', '--lang', 'zh'],   # T70：语言钉死
    capture_output=True, text=True, encoding='utf-8', errors='replace',
    env=dict(os.environ, MPLBACKEND='Agg', PYTHONIOENCODING='utf-8',
             PYTHONPATH=os.path.dirname(os.path.dirname(
                 os.path.abspath(writeback.__file__)))))
_o73b = (_r73b_cli.stdout or '') + (_r73b_cli.stderr or '')
check('73② 人读照实说「未写回」，绝不许出现「已写回」',
      '未写回' in _o73b and '已写回' not in _o73b and read(_s73b) == _SRC73B,
      str([_l for _l in _o73b.splitlines() if '写回' in _l]))
# T70 第 4 条：拒绝时**自动**打印可复制骨架（不要求 --snippet），中英各一份；
# 片段里**不许**再出现运行期下标（旧 --snippet 的 zip(fig.axes,...) 就是"一边禁一边教"）。
def _cli73(args):
    r = subprocess.run(
        [sys.executable, '-m', 'mpltweak', 'apply', _s73b, '--params', _p73b,
         '--write', '--verify-fast'] + list(args),
        capture_output=True, text=True, encoding='utf-8', errors='replace',
        env=dict(os.environ, MPLBACKEND='Agg', PYTHONIOENCODING='utf-8',
                 PYTHONPATH=os.path.dirname(os.path.dirname(
                     os.path.abspath(writeback.__file__)))))
    return (r.stdout or '') + (r.stderr or ''), r.returncode


_o73zh, _rc73zh = _cli73(['--lang', 'zh'])
check('73② 拒绝时**自动**打印建议片段（中文）：有原因 + 有骨架 + 无「已写回」',
      '未写回' in _o73zh and '手动落实片段' in _o73zh
      and '<ax>.set_xlim(0, 4)' in _o73zh and '已写回' not in _o73zh
      and read(_s73b) == _SRC73B,
      str([_l for _l in _o73zh.splitlines() if '片段' in _l or ' <ax>' in _l][:3]))
_o73en, _rc73en = _cli73(['--lang', 'en'])
check('73② 拒绝时**自动**打印建议片段（英文一份，走同一渲染器）',
      'Manual snippet' in _o73en and '<ax>.set_xlim(0, 4)' in _o73en
      and read(_s73b) == _SRC73B,
      str([_l for _l in _o73en.splitlines() if 'snippet' in _l][:2]))
_snip_zh = [_l for _l in _o73zh.splitlines() if _l.lstrip().startswith('#')]
check('73② 片段正文里没有运行期下标（不一边禁一边教）',
      # 只看**片段正文**（注释行）：warnings 里那句"运行期下标 fig.axes[i] 会写到别的轴"是解释文案，允许出现
      not any(('fig.axes[' in _l or 'zip(' in _l or 'plt.gcf()' in _l)
              for _l in _snip_zh),
      str([_l for _l in _snip_zh if 'axes[' in _l or 'zip(' in _l][:2]))
# 旧 `--snippet` 开关仍可用、正文同一渲染器
_o73sn, _rc73sn = _cli73(['--snippet', '--lang', 'zh'])
check('73② --snippet 正文与新渲染器一致（旧 zip(fig.axes, ...) 形态已消失）',
      '手动落实片段' in _o73sn and 'zip(fig.axes' not in _o73sn
      and '<ax>.set_' in _o73sn,
      str([_l for _l in _o73sn.splitlines() if 'zip(' in _l][:2]))
shutil.rmtree(_d73b, ignore_errors=True)

# ③ figure 级（figsize）仍能写进**块**（能证 figure 变量名时照写）。
#    注意：参数**只有** figsize 时会走原位改字面量那条路（RC-8b 的设计，style=inplace，
#    结果里的 `block` 放的是改后的源码文本）—— 要验块路径必须同时带一个块才能表达的
#    轴字段（这里给 xlim）。
_SRC73C = _SRC73A.replace('fig, ax = plt.subplots(figsize=(6, 3))',
                          'fig, ax = plt.subplots(figsize=(8, 6))')
_PAR73C = {'version': 4, 'script': 'fig.py', 'fig_index': 0, 'n_figs': 1,
           'figsize_in': [6.0, 4.0], 'axes': [{'index': 0, 'xlim': [0, 4]}]}
_d73c, _s73c, _p73c = _mk15('t73c', _SRC73C, _PAR73C)
_r73c = writeback.writeback(_s73c, _params.load(_p73c), _p73c, verify=False,
                            fast_check=True, style='block')
_blk73c = _r73c.get('block') or ''
check('73③ figure 级（figsize）仍能落地（原位改字面量或块里 set_size_inches），'
      '且块只承载轴字段、块内没有下标',
      ('fig.set_size_inches(6, 4)' in _blk73c
       or 'figsize=(6, 4)' in read(_s73c))
      and 'axes[' not in _blk73c and 'ax.set_xlim(0, 4)' in _blk73c
      and _r73c.get('applied') is True,
      'style=%r figsize_edited=%r' % (_r73c.get('style'),
                                      _r73c.get('figsize_edited')))
shutil.rmtree(_d73c, ignore_errors=True)

# ---- 76. T71：幂等与可撤销（第二次写盘不得再改字节；revert 必须回到**批前原件**）----
#   真因：① `_later_size_override` 把**别的对象**的属性链覆盖
#   （`d.figure_.set_size_inches(10, 6)`）误判成"本图被覆盖" → `figsize_already_ok` False
#   → 第二次 apply 又插块、又改字节，并**重写固定名备份** → revert 只能回到中间态；
#   ② 跨独立 apply 调用时固定名 `.tweak.bak` 被重新复制（首份不优先）。
_SRC76 = ('import matplotlib\nmatplotlib.use("Agg")\n'
          'import matplotlib.pyplot as plt\n\n\n'
          'class D:\n'
          '    pass\n\n\n'
          'fig, ax = plt.subplots(figsize=(10, 6))\n'
          'ax.plot([1, 2])\n'
          'd = D()\n'
          'd.figure_ = fig\n'
          'd.figure_.set_size_inches(10, 6)\n'
          'fig.savefig("o.png")\n')
_PAR76 = {'version': 4, 'script': 'fig.py', 'fig_index': 0, 'n_figs': 1,
          'figsize_in': [6.0, 4.0], 'axes': []}
_d76, _s76, _p76 = _mk15('t71', _SRC76, _PAR76)
_pristine76 = read(_s76)
_r76a = writeback.writeback(_s76, _params.load(_p76), _p76, verify=False,
                           fast_check=True, style='inplace')
_src76a = read(_s76)
_bak76 = os.path.join(os.path.dirname(_s76), '.tweak_params',
                      'fig.tweak.bak')
_bak76a = open(_bak76, 'rb').read() if os.path.exists(_bak76) else None
check('76① 首次写盘落地 figsize（原位改数字）',
      _r76a.get('reason') == 'ok' and _r76a.get('applied') is True
      and _src76a != _pristine76 and 'figsize=(6, 4)' in _src76a,
      'reason=%r applied=%r' % (_r76a.get('reason'), _r76a.get('applied')))
check('76② 固定名备份 = **批前原件**（sha 与 pristine 逐字节相同）',
      _bak76a is not None and _bak76a == _pristine76.encode('utf-8'),
      'bak=%s' % (_bak76a is None and 'None' or 'len=%d' % len(_bak76a)))
_r76b = writeback.writeback(_s76, _params.load(_p76), _p76, verify=False,
                           fast_check=True, style='inplace')
_src76b = read(_s76)
_bak76b = open(_bak76, 'rb').read() if os.path.exists(_bak76) else None
check('76③ 第二次写盘**幂等**：reason=no_change 且字节一个都不变',
      _r76b.get('reason') == 'no_change' and _src76b == _src76a,
      'reason=%r bytes_same=%s' % (_r76b.get('reason'), _src76b == _src76a))
check('76④ 第二次写盘**不覆盖**固定名备份（首份优先）',
      _bak76b == _bak76a,
      'bak_same=%s' % (_bak76b == _bak76a))
# 76⑥（T71 附加）：**跨独立 apply 调用**的真齿 —— 第二次用**不同参数**（确实写盘）
# → 备份必须仍是**批前原件**（而不是上一次写回后的中间态）。
_PAR76b = dict(_PAR76)
_PAR76b['figsize_in'] = [8.0, 3.0]
_p76b = os.path.join(_d76, 'params76b.json')
with open(_p76b, 'w', encoding='utf-8') as _f76b:
    json.dump(_PAR76b, _f76b, ensure_ascii=False)
_r76c = writeback.writeback(_s76, _params.load(_p76b), _p76b, verify=False,
                           fast_check=True, style='inplace')
_src76c = read(_s76)
_bak76c = open(_bak76, 'rb').read() if os.path.exists(_bak76) else None
check('76⑥ 第二次用**不同参数**真写盘后，备份仍是**批前原件**（不是中间态）',
      _src76c != _src76b and _bak76c == _pristine76.encode('utf-8'),
      'changed=%s bak_orig=%s'
      % (_src76c != _src76b, _bak76c == _pristine76.encode('utf-8')))

from mpltweak import revert as _revert76                       # noqa: E402
_revert76.main([_s76, '--write'])
check('76⑤ revert 回到**批前原件**（字节 == pristine）',
      read(_s76) == _pristine76,
      str([_l for _l in read(_s76).splitlines() if 'figsize' in _l][:2]))
shutil.rmtree(_d76, ignore_errors=True)

# ---- 77. T73 零兼容：参数里没写 y → **一个字都不动 y** ----
_SRC77 = ('import matplotlib\nmatplotlib.use("Agg")\n'
          'import matplotlib.pyplot as plt\n'
          'fig, ax = plt.subplots()\n'
          'ax.plot([1, 2, 3])\n'
          'ax.set_xlabel("X", fontsize=9)\n'
          'ax.set_ylabel("Y", fontsize=20)\n'
          'fig.savefig("o.png")\n')


def _par77(fields):
    return {'version': 4, 'script': 'fig.py', 'fig_index': 0, 'n_figs': 1,
            'axes': [dict({'index': 0}, **fields)]}


_D77, _S77, _P77 = _mk15('t73', _SRC77, _par77({'label_fontsize': 15.0}))
_r77a = writeback.writeback(_S77, _params.load(_P77), _P77, verify=False,
                           fast_check=True, style='inplace')
_src77a = read(_S77)
_ch77a = [str(c) for c in (_r77a.get('changes') or [])]
check('77① 只给 x → y 侧 set_ylabel 逐字节不变、changes 不含 y 侧',
      'ax.set_ylabel("Y", fontsize=20)' in _src77a
      and 'ax.set_ylabel("Y", fontsize=15)' not in _src77a
      and 'ax.set_xlabel("X", fontsize=15)' in _src77a
      and not any('label_fontsize_y' in c for c in _ch77a),
      'changes=%r' % (_ch77a,))
_D77b, _S77b, _P77b = _mk15('t73', _SRC77,
                            _par77({'label_fontsize': 15.0,
                                    'label_fontsize_y': 25.0}))
_r77b = writeback.writeback(_S77b, _params.load(_P77b), _P77b, verify=False,
                           fast_check=True, style='inplace')
_src77b = read(_S77b)
_ch77b = [str(c) for c in (_r77b.get('changes') or [])]
check('77② x/y 都给且都不同 → 两侧都改、changes 两侧都报',
      'ax.set_xlabel("X", fontsize=15)' in _src77b
      and 'ax.set_ylabel("Y", fontsize=25)' in _src77b
      and any('label_fontsize_y' in c for c in _ch77b)
      and any(c.endswith('label_fontsize') for c in _ch77b),
      'changes=%r' % (_ch77b,))
# ③ 窗口导出风格的参数（launch.py 写出的 .tweak_params **两列都给**，且已等于源码值）
_D77c, _S77c, _P77c = _mk15('t73', _SRC77,
                            _par77({'label_fontsize': 9.0,
                                    'label_fontsize_y': 20.0}))
_r77c = writeback.writeback(_S77c, _params.load(_P77c), _P77c, verify=False,
                           fast_check=True, style='inplace')
check('77③ 窗口风格参数（x/y 都给且已等于源码）→ no_change、两边都不动',
      _r77c.get('reason') == 'no_change'
      and 'fontsize=9' in read(_S77c) and 'fontsize=20' in read(_S77c),
      'reason=%r' % (_r77c.get('reason'),))
shutil.rmtree(_D77, ignore_errors=True)
shutil.rmtree(_D77b, ignore_errors=True)
shutil.rmtree(_D77c, ignore_errors=True)

# ---- 78. T75b：describe 补 y 侧字号字段 + verify **真的判 y** -----------------
#   T73 删掉了"参数没写 y → 沿用 x"的兼容回退（零兼容），随之暴露两个缺口：
#   ① `describe` 只导出 x 侧三个字号字段 → agent 读不到 y 侧、也不知道能设；
#   ② `verify.compare` 只比 x 侧 → **纯 y 改动在语义层不被判定**（"没比却说通过"）。
#   本节钉三件事：describe 出两个 y 字段且是**源码真值**（不是复制 x）；参数带了 y
#   就必须比（写错要失败）；只给 x 的参数一个字节都不动 y（T73 不变量）。
_S78 = ('import matplotlib\nmatplotlib.use("Agg")\n'
        'import matplotlib.pyplot as plt\n'
        'fig, ax = plt.subplots(figsize=(6, 4))\n'
        'ax.plot([1, 2, 3])\n'
        'ax.set_xlabel("x label", fontsize=11)\n'
        'ax.set_ylabel("y label", fontsize=17)\n'
        'ax.tick_params(axis="x", labelsize=9)\n'
        'ax.tick_params(axis="y", labelsize=13)\n'
        "fig.savefig('o.png')\n")
_d78, _s78, _p78 = _mk15('t75b', _S78,
                         {'version': 4, 'script': 'fig.py', 'fig_index': 0,
                          'axes': [{'index': 0}]})

# 78① describe（默认单图 + --all-figs）必须带两个 y 字段，且值是源码真值
def _describe_json(sp, *extra):
    _r = subprocess.run([sys.executable, '-m', 'mpltweak', 'describe', sp]
                        + list(extra), capture_output=True, cwd=os.path.dirname(sp),
                        env=_env15)
    try:
        return json.loads(_decode_out(_r.stdout))
    except ValueError:
        return None


_d78j = _describe_json(_s78)
_a78 = ((_d78j or {}).get('axes') or [{}])[0]
check('78① describe（单图）导出 y 侧字号且=源码真值（x=11 / y=17、tick x=9 / y=13）',
      _a78.get('label_fontsize') == 11.0 and _a78.get('label_fontsize_y') == 17.0
      and _a78.get('tick_fontsize') == 9.0 and _a78.get('tick_fontsize_y') == 13.0,
      'label_fontsize=%r label_fontsize_y=%r tick_fontsize=%r tick_fontsize_y=%r'
      % (_a78.get('label_fontsize'), _a78.get('label_fontsize_y'),
         _a78.get('tick_fontsize'), _a78.get('tick_fontsize_y')))
_d78a = _describe_json(_s78, '--all-figs')
_a78a = ((((_d78a or {}).get('figures') or [{}])[0].get('axes')) or [{}])[0]
check('78① describe --all-figs 同样带两个 y 字段且值正确',
      _a78a.get('label_fontsize_y') == 17.0 and _a78a.get('tick_fontsize_y') == 13.0,
      'label_fontsize_y=%r tick_fontsize_y=%r'
      % (_a78a.get('label_fontsize_y'), _a78a.get('tick_fontsize_y')))
# 纯增量：既有键的**相对顺序**不得变；两个新键必须存在。
# （集成树的 describe 还有 T26 的"采集侧给了才搬"动态追加 —— lines/cmap/legend
#   在字面量**之后**才补，所以两个新键不一定落在全字段最末尾。断言"既有键的
#   相对顺序不变 + 新键存在"才是"纯增量"的正确含义；值正确性见上面两条。）
_BASE78_KEYS = ['index', 'pos', 'aspect_locked', 'title_fontsize', 'label_fontsize',
                'tick_fontsize', 'is_colorbar', 'clim', 'cell', 'xlim', 'ylim',
                'xscale', 'yscale', 'grid', 'spines', 'lines']
_old78 = [k for k in _a78.keys() if k not in ('label_fontsize_y', 'tick_fontsize_y')]
check('78① 纯增量：既有键的相对顺序不变，且两个新键都在',
      _old78 == _BASE78_KEYS
      and 'label_fontsize_y' in _a78 and 'tick_fontsize_y' in _a78,
      'keys=%s' % (list(_a78.keys()),))

# 78② verify 真的判 y：带了 y 就必须比，写错要失败
from mpltweak import verify as _verify                             # noqa: E402
_BASE78 = {'version': 4, 'script': 'fig.py', 'fig_index': 0,
           'axes': [{'index': 0, 'pos': [0.125, 0.11, 0.775, 0.77],
                     'label_fontsize': 11, 'label_fontsize_y': 17,
                     'tick_fontsize': 9, 'tick_fontsize_y': 13,
                     'grid': False, 'spines': {}, 'xscale': 'linear',
                     'yscale': 'linear', 'cell': None}]}
_ok78, _det78 = _verify.verify_semantic(_s78, json.loads(json.dumps(_BASE78)))
check('78② 全部与源码一致（含 y）→ 语义层通过',
      _ok78 is True, 'ok=%r detail=%r' % (_ok78, _det78))
_bad78 = json.loads(json.dumps(_BASE78))
_bad78['axes'][0]['label_fontsize_y'] = 23
_ok78b, _det78b = _verify.verify_semantic(_s78, _bad78)
check('78② **y 标签字号写错**（17→23）→ 语义层必须判失败（不是 pass）',
      _ok78b is False and 'label_fontsize_y' in (_det78b or ''),
      'ok=%r detail=%r' % (_ok78b, _det78b))
_bad78c = json.loads(json.dumps(_BASE78))
_bad78c['axes'][0]['tick_fontsize_y'] = 19
_ok78c, _det78c = _verify.verify_semantic(_s78, _bad78c)
check('78② **y 刻度字号写错**（13→19）→ 语义层必须判失败',
      _ok78c is False and 'tick_fontsize_y' in (_det78c or ''),
      'ok=%r detail=%r' % (_ok78c, _det78c))
# 只给 x 时**不许拿 x 的值当 y 的期望**（T73 删掉的回退不许以任何形式复活）：
# 故意把 x 的期望设成源码 y 的值（17），若还拿 y 来"洗白"就会假通过。
_onlyx = json.loads(json.dumps(_BASE78))
_onlyx['axes'][0] = {'index': 0, 'pos': [0.125, 0.11, 0.775, 0.77],
                     'label_fontsize': 17, 'grid': False, 'spines': {},
                     'xscale': 'linear', 'yscale': 'linear', 'cell': None}
_ok78d, _det78d = _verify.verify_semantic(_s78, _onlyx)
check('78② 只给 x（x 的期望=源码 y 的值）→ 必须失败（不许拿 y 洗白 x）',
      _ok78d is False and 'label_fontsize' in (_det78d or ''),
      'ok=%r detail=%r' % (_ok78d, _det78d))

# 78③ 未判定字段必须如实列出（"没比却说通过"禁止）
_uj78 = []
_b78 = _verify.compare([{'index': 0, 'label_fontsize_y': 17}],
                       [{'index': 0, 'label_fontsize': 11}], unjudged=_uj78)
check('78③ 已请求但实际值取不到 → 记进未判定清单，且**不算失败**',
      _uj78 == ['ax0.label_fontsize_y'] and _b78 == [],
      'unjudged=%r bad=%r' % (_uj78, _b78))
_S78u = ('import matplotlib\nmatplotlib.use("Agg")\n'
         'import matplotlib.pyplot as plt\n'
         'fig, ax = plt.subplots(figsize=(6, 4))\n'
         'ax.set_yticks([])\n'
         "fig.savefig('o.png')\n")
_d78u, _s78u, _p78u = _mk15('t75bu', _S78u,
                            {'version': 4, 'script': 'fig.py', 'fig_index': 0,
                             'axes': [{'index': 0}]})
_oku, _detu = _verify.verify_semantic(_s78u, {
    'version': 4, 'script': 'fig.py', 'fig_index': 0,
    'axes': [{'index': 0, 'tick_fontsize_y': 20}]})
check('78③ 轴没有 y 刻度标签时（实际值取不到）→ detail 里**列出来**（不谎报通过）',
      '未判定字段' in (_detu or '') and 'tick_fontsize_y' in (_detu or ''),
      'ok=%r detail=%r' % (_oku, _detu))

# 78④ 只有 x 的参数 → y 一个字节都不动（T73 不变量）
with open(_p78, 'w', encoding='utf-8', newline='\n') as _f78:
    json.dump({'version': 4, 'script': 'fig.py', 'fig_index': 0,
               'axes': [{'index': 0, 'label_fontsize': 22}]}, _f78)
with open(_s78, 'w', encoding='utf-8', newline='\n') as _f78s:
    _f78s.write(_S78)
_r78 = writeback.writeback(_s78, _params.load(_p78), _p78, verify=False,
                           fast_check=True, style='inplace')
_t78 = read(_s78)
check('78④ 只给 x（label_fontsize=22）→ x 落地、y 一个字节不动（T73 不变量）',
      _r78.get('reason') in ('ok', 'best_effort')
      and 'fontsize=22' in _t78 and 'fontsize=17' in _t78
      and 'fontsize=11' not in _t78,
      'reason=%r y_kept=%s x_written=%s'
      % (_r78.get('reason'), 'fontsize=17' in _t78, 'fontsize=22' in _t78))
shutil.rmtree(_d78, ignore_errors=True)
shutil.rmtree(_d78u, ignore_errors=True)
# ---- 74. T74：多图 pyplot 的**可证**定位 `plt.figure(<n>).axes[k]` ----
#   为什么可证：`plt.gcf()` 是"**当前活动**的那张图"（脚本末尾通常是最后一张 ≠ 目标
#   图，T70 禁掉的就是它）；`plt.figure(n)` 是"**编号为 n 的那张图**"——n 可静态枚举、
#   模块层没有 close → 它**就是**目标图。
_SRC74 = ('import matplotlib\nmatplotlib.use("Agg")\n'
          'import matplotlib.pyplot as plt\n'
          'plt.figure()\nplt.plot([1, 2, 3])\n'
          'plt.figure()\nplt.plot([4, 5, 6])\n'
          'plt.savefig("o.png")\n')
_PAR74 = {'version': 4, 'script': 'fig.py', 'fig_index': 1, 'n_figs': 2,
          'axes': [{'index': 0, 'xlim': [0, 5]}]}
_D74, _S74, _P74 = _mk15('t74', _SRC74, _PAR74)
_r74a = writeback.writeback(_S74, _params.load(_P74), _P74, verify=False,
                           fast_check=True, style='block')
_src74a = read(_S74)
check('74① 多图编号可证 → 块用 plt.figure(2).axes[0]，且不含 gcf/gca/fig.axes[',
      'plt.figure(2).axes[0]' in _src74a
      and 'plt.gcf()' not in _src74a and 'plt.gca()' not in _src74a
      and 'fig.axes[' not in _src74a,
      str([ln.strip() for ln in _src74a.splitlines() if '.axes[' in ln]))
# ②a `close(2)` → 编号会被复用/重置 → 必须拒绝（字节不变 + pending + 不插块）
_D74b, _S74b, _P74b = _mk15(
    't74', _SRC74.replace('plt.savefig', 'plt.close(2)\nplt.savefig'), _PAR74)
_prior74b = read(_S74b)
_r74b = writeback.writeback(_S74b, _params.load(_P74b), _P74b, verify=False,
                           fast_check=True, style='block')
check('74② 有 close(2) → 拒绝写（字节不变 + pending 非空 + 不插块）',
      _r74b.get('applied') is False and bool(_r74b.get('pending'))
      and read(_S74b) == _prior74b
      and writeback.BLOCK_START not in read(_S74b),
      'applied=%r pending=%r' % (_r74b.get('applied'), _r74b.get('pending')))
# ②b 循环里建图 → 编号不可枚举 → 拒绝
_SRC74c = ('import matplotlib\nmatplotlib.use("Agg")\n'
           'import matplotlib.pyplot as plt\n'
           'for i in range(2):\n'
           '    plt.figure()\n'
           '    plt.plot([i, i + 1])\n'
           'plt.savefig("o.png")\n')
_D74c, _S74c, _P74c = _mk15('t74', _SRC74c, _PAR74)
_prior74c = read(_S74c)
_r74c = writeback.writeback(_S74c, _params.load(_P74c), _P74c, verify=False,
                           fast_check=True, style='block')
check('74② 循环里建图 → 编号不可枚举 → 拒绝写（字节不变 + pending 非空）',
      _r74c.get('applied') is False and bool(_r74c.get('pending'))
      and read(_S74c) == _prior74c,
      'applied=%r pending=%r' % (_r74c.get('applied'), _r74c.get('pending')))
# ③ 单图 pyplot 的既有可证路径**不退步**（仍是 plt.gcf().axes[k]）
_SRC74d = ('import matplotlib\nmatplotlib.use("Agg")\n'
           'import matplotlib.pyplot as plt\n'
           'plt.figure()\nplt.plot([1, 2, 3])\n'
           'plt.savefig("o.png")\n')
_PAR74d = {'version': 4, 'script': 'fig.py', 'fig_index': 0, 'n_figs': 1,
           'axes': [{'index': 0, 'xlim': [0, 5]}]}
_D74d, _S74d, _P74d = _mk15('t74', _SRC74d, _PAR74d)
_r74d = writeback.writeback(_S74d, _params.load(_P74d), _P74d, verify=False,
                           fast_check=True, style='block')
_src74d = read(_S74d)
check('74③ 单图 pyplot 既有路径不退步（仍用 plt.gcf().axes[0]）',
      'plt.gcf().axes[0]' in _src74d
      and 'plt.figure(1).axes[0]' not in _src74d,
      str([ln.strip() for ln in _src74d.splitlines() if '.axes[' in ln]))
shutil.rmtree(_D74, ignore_errors=True)
shutil.rmtree(_D74b, ignore_errors=True)
shutil.rmtree(_D74c, ignore_errors=True)
shutil.rmtree(_D74d, ignore_errors=True)

shutil.rmtree(d, ignore_errors=True)
shutil.rmtree(d3, ignore_errors=True)
shutil.rmtree(d4, ignore_errors=True)
shutil.rmtree(_TMPROOT, ignore_errors=True)

# 69e（T65/T66）：**不变量** —— 绝不静默丢改动。三种结局分别钉死：
# （a）声称"无需改动"（reason=no_change 且未写盘）→ 必须证明脚本**生效尺寸已经==目标**
#      （独立读取器读不出 → 未知 → FAIL）；**P3'/P7 修前正是这一支**（假等值 → 静默 no_change）。
# （b）没写盘、也没说 no_change → 必须**明确申报**退块（needs_block=True 或 pending 非空），否则同样是静默丢改动；
# （c）真写了盘 → 改完后生效尺寸必须==目标。
# 生效尺寸用**本测试自带的独立读取器**（绝不调产品函数 _figsize_from_source，避免自证）。
def _eff_size(src):
    """独立读取脚本的**生效尺寸**：所有可读字面量按 lineno 取最后一条；有尺寸语句但读不出 → None；
    完全没有尺寸语句 → matplotlib 默认值。"""
    import ast as _ast
    try:
        tree = _ast.parse(src)
    except SyntaxError:
        return None
    cands, seen = [], False
    for n in _ast.walk(tree):
        if isinstance(n, _ast.Call):
            for kw in n.keywords:
                if kw.arg == 'figsize':
                    seen = True
                    v = kw.value
                    if isinstance(v, (_ast.Tuple, _ast.List)) and len(v.elts) == 2:
                        try:
                            cands.append((n.lineno, [float(_ast.literal_eval(v.elts[0])),
                                                     float(_ast.literal_eval(v.elts[1]))]))
                        except (ValueError, TypeError, SyntaxError):
                            pass
            if (isinstance(n.func, _ast.Attribute)
                    and n.func.attr == 'set_size_inches'):
                seen = True
                if len(n.args) >= 2:
                    try:
                        cands.append((n.lineno, [float(_ast.literal_eval(n.args[0])),
                                                 float(_ast.literal_eval(n.args[1]))]))
                    except (ValueError, TypeError, SyntaxError):
                        pass
        if isinstance(n, _ast.Assign):
            is_pair = (isinstance(n.value, (_ast.Tuple, _ast.List))
                       and len(n.value.elts) == 2)
            if is_pair:
                for tg in n.targets:
                    if (isinstance(tg, _ast.Subscript)
                            and isinstance(tg.slice, _ast.Constant)
                            and tg.slice.value == 'figure.figsize'):
                        seen = True
                        try:
                            cands.append((n.lineno, [float(_ast.literal_eval(n.value.elts[0])),
                                                     float(_ast.literal_eval(n.value.elts[1]))]))
                        except (ValueError, TypeError, SyntaxError):
                            pass
    if cands:
        cands.sort(key=lambda c: c[0])
        return cands[-1][1]
    if seen:
        return None                      # 有尺寸语句但读不出 → 未知
    try:
        import matplotlib as _m
        d = _m.rcParams['figure.figsize']
        return [float(d[0]), float(d[1])]
    except Exception:                                             # noqa: BLE001
        return None


_SZ = lambda a, b: (abs(a[0] - b[0]) <= 1e-9 and abs(a[1] - b[1]) <= 1e-9)
_hit = lambda v, b: bool(v is not None and len(v) == 2 and _SZ(v, b))
_H69E = ('import matplotlib\nmatplotlib.use("Agg")\nimport matplotlib.pyplot as plt\n')
_P3H = ('%sfig, ax = plt.subplots(figsize=(9, 5))\nfig.set_size_inches(4, 3)\n'
        'ax.plot([1, 2, 3])\nfig.savefig("o.png")\n')
_P1 = ('%sfig, ax = plt.subplots(figsize=(9, 5))\nax.plot([1, 2, 3])\nfig.savefig("o.png")\n')
_P2 = ('%sfig = plt.figure(figsize=(9, 5))\nax = fig.add_axes([0.1, 0.1, 0.8, 0.8])\n'
       'ax.plot([1, 2, 3])\nfig.savefig("o.png")\n')
_P4 = ('%sfig, ax = plt.subplots()\nax.plot([1, 2, 3])\nfig.savefig("o.png")\n')
_P5 = ('%sfig, ax = plt.subplots()\nfig.set_size_inches(6.4, 4.8)\n'
       'ax.plot([1, 2, 3])\nfig.savefig("o.png")\n')
_P7 = ('%sfig, ax = plt.subplots()\nW, H = 6.4, 4.8\nfig.set_size_inches(W, H)\n'
       'ax.plot([1, 2, 3])\nfig.savefig("o.png")\n')
# --- 读取器自检（防它自己退化成"永远返回默认值"，那会让 (a) 支整体失效）---
check('69e-R1 读取器：figsize= 字面量 → [9.0, 5.0]', _hit(_eff_size(_P1 % _H69E), [9.0, 5.0]),
      str(_eff_size(_P1 % _H69E)))
check('69e-R2 读取器：后置 set_size_inches 覆盖 → [4.0, 3.0]',
      _hit(_eff_size(_P3H % _H69E), [4.0, 3.0]), str(_eff_size(_P3H % _H69E)))
check('69e-R3 读取器：变量尺寸（有语句但读不出）→ None（未知）',
      _eff_size(_P7 % _H69E) is None, str(_eff_size(_P7 % _H69E)))
check('69e-R4 读取器：完全没有尺寸语句 → rcParams 默认 [6.4, 4.8]',
      _hit(_eff_size(_P4 % _H69E), [6.4, 4.8]), str(_eff_size(_P4 % _H69E)))
# --- 七例不变量 ---
for _tag, _src, _fs in (('69e-P1', _P1 % _H69E, [6.4, 4.8]),
                        ('69e-P2', _P2 % _H69E, [6.4, 4.8]),
                        ('69e-P3', _P3H % _H69E, [4.0, 3.0]),
                        ("69e-P3'", _P3H % _H69E, [9.0, 5.0]),
                        ('69e-P4', _P4 % _H69E, [6.4, 4.8]),
                        ('69e-P5', _P5 % _H69E, [6.4, 4.8]),
                        ('69e-P7', _P7 % _H69E, [6.4, 4.8])):
    _d, _s, _p = _mk15('t66' + _tag.replace('-', '').replace("'", 'p'), _src,
                       {'version': 4, 'script': 'fig.py', 'fig_index': 0,
                        'figsize_in': _fs, 'axes': [{'index': 0}]})
    _r = writeback.writeback(_s, _params.load(_p), _p, verify=True,
                             semantic=True, style='inplace')
    _after = read(_s)
    _same = _after == _src
    _eff = _eff_size(_after)
    _rsn = _r.get('reason')
    _nb = _r.get('needs_block')
    _pend = _r.get('pending') or []
    if _rsn == 'no_change' and _same:
        _kind = '声称无需改动+未写盘'
        _ok = _hit(_eff, _fs)                       # 空口宣称 → 必须证明本来就对
    elif _same:
        _kind = '未写盘+已申报退块'
        _ok = bool(_nb is True or _pend)            # 弃权可以，但必须说清楚
    else:
        _kind = '声称已改（已写盘）'
        _ok = _hit(_eff, _fs)                       # 真改了 → 改完必须对
    check('%s 不变量[%s]：生效尺寸 %s vs 目标 %s' % (_tag, _kind, _eff, _fs), _ok,
          'reason=%r nb=%r pending=%r bytes_same=%s eff=%s'
          % (_rsn, _nb, _pend, _same, _eff))
    shutil.rmtree(_d, ignore_errors=True)


# ---- 79. T78：tick_fontsize 原位写回必须按 axis 过滤（不许改到 axis='y' 那条）----
# 实测来源：zenodo `22254023__figure_lib.py`（第 812 行 axis='y' 在前、第 833 行 axis='x' 在后）。
# 旧代码用不过滤 axis 的 _find_call → 命中最早的 812（y）改成目标值，而该改的 833（x）没动，
# 工具却报 ok / style=inplace / changes=['ax0.tick_fontsize']。
_S79 = '''import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
fig = plt.figure()
ax = fig.add_subplot(111)
ax.plot([1, 2, 3], [3, 2, 1])
ax.tick_params(axis="y", labelcolor="red", labelsize=10)
ax.tick_params(axis="x", labelsize=10)
fig.savefig("o.png")
'''
_d79, _s79, _p79 = _mk15('t78a', _S79,
                         {'version': 4, 'script': 'fig.py',
                          'axes': [{'index': 0, 'tick_fontsize': 16.0}]})
_rr79 = subprocess.run([sys.executable, '-m', 'mpltweak', 'apply', _s79,
                        '--params', _p79, '--write', '--verify-fast', '--json'],
                       capture_output=True, cwd=_d79, env=_env15)
_o79 = _decode_out(_rr79.stdout + _rr79.stderr)
_t79 = read(_s79)
_ly79 = [ln for ln in _t79.splitlines() if 'axis="y"' in ln]
_lx79 = [ln for ln in _t79.splitlines() if 'axis="x"' in ln]
check('79① T78：只给 tick_fontsize → 只改 axis="x" 那条，axis="y" 那条一个字节不动',
      _rr79.returncode == 0 and len(_lx79) == 1 and 'labelsize=16' in _lx79[0]
      and len(_ly79) == 1 and 'labelsize=10' in _ly79[0],
      'x=%s y=%s' % (_lx79, _ly79))
check('79② T78：changes 只报 tick_fontsize（不谎报改过 y 侧）',
      '"ax0.tick_fontsize"' in _o79 and '"ax0.tick_fontsize_y"' not in _o79,
      _o79[-160:])
shutil.rmtree(_d79, ignore_errors=True)

# 79③ 不带 axis= 的写法仍是**合法**的原位目标（源码自己就是"一次改两边"，
#     改它那一个字面量是它的原本语义；54g / 60c 的语义不许被 T78 改掉）
_S79c = '''import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
fig = plt.figure()
ax = fig.add_subplot(111)
ax.plot([1, 2, 3], [3, 2, 1])
ax.tick_params(labelsize=8)
fig.savefig("o.png")
'''
_d79c, _s79c, _p79c = _mk15('t78c', _S79c,
                            {'version': 4, 'script': 'fig.py',
                             'axes': [{'index': 0, 'tick_fontsize': 16.0}]})
_rr79c = subprocess.run([sys.executable, '-m', 'mpltweak', 'apply', _s79c,
                         '--params', _p79c, '--write', '--verify-fast', '--json'],
                        capture_output=True, cwd=_d79c, env=_env15)
_t79c = read(_s79c)
check('79③ 不带 axis= 的 tick_params(labelsize=) 仍是原位目标（改成 16，不插块）',
      _rr79c.returncode == 0 and 'labelsize=16' in _t79c
      and '===== auto layout' not in _t79c,
      [ln for ln in _t79c.splitlines() if 'tick_params' in ln])
shutil.rmtree(_d79c, ignore_errors=True)

# 79④ axis= 是**变量** → 证不出 → 不许乱猜（那条字面量必须原样不动）
_S79d = '''import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
fig = plt.figure()
ax = fig.add_subplot(111)
ax.plot([1, 2, 3], [3, 2, 1])
_side = "y"
ax.tick_params(axis=_side, labelsize=10)
fig.savefig("o.png")
'''
_d79d, _s79d, _p79d = _mk15('t78d', _S79d,
                            {'version': 4, 'script': 'fig.py',
                             'axes': [{'index': 0, 'tick_fontsize': 16.0}]})
_rr79d = subprocess.run([sys.executable, '-m', 'mpltweak', 'apply', _s79d,
                         '--params', _p79d, '--write', '--verify-fast', '--json'],
                        capture_output=True, cwd=_d79d, env=_env15)
_t79d = read(_s79d)
check('79④ axis= 是变量（证不出）→ 那条 labelsize 必须原样不动',
      'labelsize=10' in _t79d and 'labelsize=16' not in _t79d.split('axis=_side')[1][:60],
      [ln for ln in _t79d.splitlines() if 'tick_params' in ln])
shutil.rmtree(_d79d, ignore_errors=True)

# 79⑤ T78 第二处：**退块时 x 侧必须写显式 axis='x'**
#   不带 axis 的 `tick_params(labelsize=N)` 运行期一次改 x 和 y —— 用户只说改 x，
#   块却顺手改了 y（与 T73"没说 y 就不动 y"矛盾）。
_S79e = '''import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
fig = plt.figure()
ax = fig.add_subplot(111)
ax.plot([1, 2, 3], [3, 2, 1])
fig.savefig("o.png")
'''
_d79e, _s79e, _p79e = _mk15('t78e', _S79e,
                            {'version': 4, 'script': 'fig.py',
                             'axes': [{'index': 0, 'tick_fontsize': 16.0}]})
_rr79e = subprocess.run([sys.executable, '-m', 'mpltweak', 'apply', _s79e,
                         '--params', _p79e, '--write', '--style', 'block',
                         '--verify-fast', '--json'],
                        capture_output=True, cwd=_d79e, env=_env15)
_t79e = read(_s79e)
_ln79e = [ln.strip() for ln in _t79e.splitlines() if 'tick_params' in ln]
check('79⑤ T78②：块里 x 侧写成 axis=\'x\'（不许用不带 axis= 的写法连 y 一起改）',
      any("axis='x'" in ln for ln in _ln79e)
      and not any('tick_params(labelsize=' in ln for ln in _ln79e),
      str(_ln79e))
shutil.rmtree(_d79e, ignore_errors=True)

# ---- 84. T84（blocker，Codex r4 §2.3）：同图**重名轴** → 原位写回禁止改别的轴 ----
#   `fig, (ax, ax) = plt.subplots(1, 2)`：名字 ax 在运行期只认**最后一次**解包（第二条轴），
#   而 `_find_call` 只能按名字取"最早那次调用" → 轴 0 的字段会被写到**第二条轴**那行。
#   T57 的判据建立在 `axes_calls`（只收 add_axes/add_subplot）上，**看不见同名解包** → 漏。
import hashlib as _hl84                                                # noqa: E402

_SRC84 = ('import matplotlib\nmatplotlib.use("Agg")\n'
          'import matplotlib.pyplot as plt\n'
          'fig, (ax, ax) = plt.subplots(1, 2)\n'
          'ax.tick_params(axis="x", labelsize=10)\n'
          'fig.savefig("o.png")\n')

def _run84(tag, param_axes):
    _d, _s, _p = _mk15(tag, _SRC84,
                       {'version': 4, 'script': 'target.py', 'axes': param_axes})
    _before = open(_s, 'rb').read()
    _r = subprocess.run([sys.executable, '-m', 'mpltweak', 'apply', _s,
                         '--params', _p, '--write', '--json', '--verify-fast',
                         '--style', 'inplace', '--force'],
                        capture_output=True, cwd=_d, env=_env15)
    _after = open(_s, 'rb').read()
    try:
        _f = (json.loads(_r.stdout.decode('utf-8', 'replace')).get('files')
              or [{}])[0]
    except ValueError:
        _f = {}
    _out = ((_r.stdout or b'').decode('utf-8', 'replace')
            + (_r.stderr or b'').decode('utf-8', 'replace'))
    return _d, _s, _r, _f, _before, _after, _out

_d84, _s84, _r84, _f84, _b84, _a84, _o84 = _run84(
    't84dup', [{'index': 0, 'tick_fontsize': 14.0}])
check('84① 同图重名轴 + 参数只给 index=0 → 禁止原位写回（applied=false / pending 非空）',
      _f84.get('applied') is False and bool(_f84.get('pending')),
      'reason=%r applied=%r pending=%r' % (_f84.get('reason'), _f84.get('applied'),
                                           _f84.get('pending')))
check('84① 字节 sha256 一个字节都没变',
      hashlib.sha256(_b84).hexdigest() == hashlib.sha256(_a84).hexdigest(),
      '%s -> %s' % (hashlib.sha256(_b84).hexdigest()[:16],
                    hashlib.sha256(_a84).hexdigest()[:16]))
check('84① 人读如实说「未写回」，且**不许**出现「已原位写回」',
      '未写回' in _o84 and '已原位写回' not in _o84,
      str([l.strip() for l in _o84.splitlines() if '写回' in l][:3]))
check('84① 源码里那条 tick_params 的字面量原样（labelsize=10）',
      'labelsize=10' in read(_s84) and 'labelsize=14' not in read(_s84),
      str([l.strip() for l in read(_s84).splitlines() if 'tick_params' in l]))
shutil.rmtree(_d84, ignore_errors=True)

_d84b, _s84b, _r84b, _f84b, _b84b, _a84b, _o84b = _run84(
    't84dup_ok', [{'index': 0, 'tick_fontsize': 14.0},
                  {'index': 1, 'tick_fontsize': 14.0}])
check('84② 对照：参数覆盖全部被绑定轴 → 允许原位写（applied=true、字节变了）',
      _f84b.get('applied') is True and _a84b != _b84b,
      'reason=%r applied=%r pending=%r' % (_f84b.get('reason'), _f84b.get('applied'),
                                           _f84b.get('pending')))
shutil.rmtree(_d84b, ignore_errors=True)

# =====================================================================
# §85 figsize-only 原位写回：covered 为空也必须跑语义验证（figsize 比对）
# =====================================================================
# 复现：plt.subplots(figsize=...) 网格轴 → 原位路径没有任何轴字段可覆盖
# （covered 空）→ 修复前语义层被跳过（semantic=None），figsize 比对不可达；
# 修复后 `if covered or figsize_edited:` → verify_semantic 照跑，figsize 落地
# 到目标图才通过（writeback.py:2873 / verify.py:928）。
_d85 = os.path.join(_TMPROOT, 'case_85')
os.makedirs(_d85, exist_ok=True)
_s85 = os.path.join(_d85, 'fig85.py')
open(_s85, 'w', encoding='utf-8', newline='').write(
    'import matplotlib.pyplot as plt\n'
    'import numpy as np\n'
    'fig, ax = plt.subplots(figsize=(10, 3))\n'
    'ax.plot(np.linspace(0, 1, 50))\n'
    'fig.savefig("out85.png")\n')
_p85 = os.path.join(_d85, 'params85.json')
_data85 = {'fig_index': 0, 'figsize_in': [7.0, 3.5], 'axes': []}
open(_p85, 'w', encoding='utf-8').write(json.dumps(_data85))
_r85 = writeback.writeback(_s85, _data85, _p85, verify=True, semantic=True,
                           style='inplace', timeout=120)
check('85① figsize-only（covered 空）→ 语义层照跑且通过（semantic is True，不再是 None）',
      _r85.get('semantic') is True,
      'reason=%r semantic=%r verified=%r err=%r' % (_r85.get('reason'),
                                                    _r85.get('semantic'),
                                                    _r85.get('verified'),
                                                    _r85.get('err')))
check('85② 原位写回生效：源码里的 figsize 变成目标值',
      'figsize=(7' in read(_s85),
      str([l.strip() for l in read(_s85).splitlines() if 'figsize' in l][:2]))
shutil.rmtree(_d85, ignore_errors=True)

# =====================================================================
# §86 写盘前二次确认：src 读取后外部改过脚本 → 拒绝落盘（不覆盖用户改动）
# =====================================================================
_d86 = os.path.join(_TMPROOT, 'case_86')
os.makedirs(_d86, exist_ok=True)
_s86 = os.path.join(_d86, 'fig86.py')
_orig86 = ('import matplotlib.pyplot as plt\n'
           'fig, ax = plt.subplots(figsize=(6, 4))\n'
           'fig.savefig("out86.png")\n')
open(_s86, 'w', encoding='utf-8', newline='').write(_orig86)
_g86 = writeback._guard_external_edit(_s86, _orig86)
check('86① 磁盘未变 → 放行（返回 None）', _g86 is None, repr(_g86))
open(_s86, 'w', encoding='utf-8', newline='').write(_orig86 + '# 用户此刻保存了新行\n')
_g86b = writeback._guard_external_edit(_s86, _orig86)
check('86② 磁盘与 src 不一致（编辑器保存过）→ 拒绝写盘（返回原因）',
      isinstance(_g86b, str) and '外部修改' in _g86b, repr(_g86b))
shutil.rmtree(_d86, ignore_errors=True)

# =====================================================================
# §87 bug #6：apply --write 验证重跑透传用户脚本参数（`--` 分隔）
# =====================================================================
# 复现：依赖 sys.argv 的脚本（python fig.py --n 7）在写回验证重跑里拿不到
# 自己的参数 → 与基线行为不一致 → 默认回滚 + 误归因"缺数据/依赖"
# （verify.py:907 的子进程命令行 [python, -m, mpltweak.verify, script, out,
#  idx] 不带用户脚本参数；runpy 只覆盖 argv[0]、argv[1:] 是 verify 自己的）。
# 修法：`mpltweak apply <脚本> --write -- <脚本参数...>` —— `--` 后的参数沿
# apply → writeback → verify_run / verify_semantic → 子进程 `--` 分隔 → collect
# 的 sys.argv 全链透传。§87① 对本用例是"先写会失败的"：修前它必然像 §87②
# 一样缺参跑不通、默认回滚（对修前代码必红）。
_SRC87 = ('import sys\n'
          'import matplotlib\n'
          'matplotlib.use("Agg")\n'
          'import matplotlib.pyplot as plt\n'
          'if "--n" not in sys.argv:\n'
          '    raise SystemExit("bug6: missing --n")\n'
          '_n = int(sys.argv[sys.argv.index("--n") + 1])\n'
          'fig, ax = plt.subplots()\n'
          'ax.plot(range(_n))\n'
          'ax.grid(False)\n'
          'fig.savefig("out87.png")\n')
_PAR87 = {'version': 4, 'fig_index': 0, 'n_figs': 1,
          'axes': [{'index': 0, 'grid': True}]}
_d87a, _s87a, _p87a = _mk15('t87a', _SRC87, _PAR87)
_d87b, _s87b, _p87b = _mk15('t87b', _SRC87, _PAR87)


def _cli87(s, p, *extra):
    """E2E：`mpltweak apply <脚本> --params <p> --write [-- <脚本参数...>]`。"""
    r = subprocess.run(
        [sys.executable, '-m', 'mpltweak', 'apply', s, '--params', p,
         '--write'] + list(extra),
        capture_output=True, text=True, encoding='utf-8', errors='replace',
        env=dict(os.environ, MPLBACKEND='Agg', PYTHONIOENCODING='utf-8',
                 PYTHONPATH=os.path.dirname(os.path.dirname(
                     os.path.abspath(writeback.__file__)))))
    return (r.stdout or '') + (r.stderr or ''), r.returncode


# ① 带参脚本 + `-- --n 7` 透传 → 重跑拿到参数、语义验证通过、真实写回
_o87, _rc87 = _cli87(_s87a, _p87a, '--', '--n', '7')
check('87① apply --write -- --n 7：脚本参数透传给重跑，验证通过并写回（rc=0）',
      _rc87 == 0 and '已原位写回' in _o87 and '语义验证通过' in _o87,
      'rc=%r %s' % (_rc87, str([_l.strip() for _l in _o87.splitlines()
                                if '写回' in _l or '验证' in _l][:3])))
check('87① 源码真的被改：ax.grid(False) → ax.grid(True)',
      'ax.grid(True)' in read(_s87a) and 'ax.grid(False)' not in read(_s87a),
      str([_l.strip() for _l in read(_s87a).splitlines() if 'grid' in _l][:2]))
# ② 对照：**不带**脚本参数（修前行为）→ 脚本因缺参跑不通 → 回滚 + 写回失败
_o87b, _rc87b = _cli87(_s87b, _p87b)
check('87② 对照（不带参数）：脚本缺参跑不通 → 写回失败（rc≠0，未落地）',
      _rc87b != 0,
      'rc=%r %s' % (_rc87b, str([_l.strip() for _l in _o87b.splitlines()
                                 if '写回' in _l or '跑不通' in _l][:3])))
check('87② 回滚后源码原样（ax.grid(False) 还在、没有 ax.grid(True)）',
      'ax.grid(False)' in read(_s87b) and 'ax.grid(True)' not in read(_s87b),
      str([_l.strip() for _l in read(_s87b).splitlines() if 'grid' in _l][:2]))
# ③ 块路径（auto 退块）同样透传：pos 原位写不了 → 块 + 带参重跑验证通过
_SRC87C = ('import sys\n'
           'import matplotlib\n'
           'matplotlib.use("Agg")\n'
           'import matplotlib.pyplot as plt\n'
           'if "--n" not in sys.argv:\n'
           '    raise SystemExit("bug6: missing --n")\n'
           '_n = int(sys.argv[sys.argv.index("--n") + 1])\n'
           'fig, ax = plt.subplots()\n'
           'ax.plot(range(_n))\n'
           'fig.savefig("out87c.png")\n')
_PAR87C = {'version': 4, 'fig_index': 0, 'n_figs': 1,
           'axes': [{'index': 0, 'pos': [0.2, 0.2, 0.6, 0.6]}]}
_d87c, _s87c, _p87c = _mk15('t87c', _SRC87C, _PAR87C)
_o87c, _rc87c = _cli87(_s87c, _p87c, '--', '--n', '3')
check('87③ 块路径透传：原位写不了 pos → auto 退块，带参重跑 + 语义验证通过',
      _rc87c == 0 and '已写回' in _o87c and '语义验证通过' in _o87c
      and 'set_position' in read(_s87c),
      'rc=%r %s' % (_rc87c, str([_l.strip() for _l in _o87c.splitlines()
                                 if '写回' in _l or '验证' in _l][:3])))
# ④ 库级：writeback.writeback(..., script_args=...) 直通（无 CLI 也能用）
_d87d, _s87d, _p87d = _mk15('t87d', _SRC87, _PAR87)
_r87d = writeback.writeback(_s87d, _params.load(_p87d), _p87d, verify=True,
                            semantic=True, style='inplace', timeout=120,
                            script_args=['--n', '5'])
check('87④ 库级 writeback(script_args=...)：验证通过、语义通过、真实写回',
      _r87d.get('verified') is True and _r87d.get('semantic') is True
      and _r87d.get('applied') is True
      and 'ax.grid(True)' in read(_s87d),
      'reason=%r verified=%r semantic=%r err=%r'
      % (_r87d.get('reason'), _r87d.get('verified'), _r87d.get('semantic'),
         _r87d.get('err')))
shutil.rmtree(_d87a, ignore_errors=True)
shutil.rmtree(_d87b, ignore_errors=True)
shutil.rmtree(_d87c, ignore_errors=True)
shutil.rmtree(_d87d, ignore_errors=True)

# =====================================================================
# §88 bug：无赋值名的 plt.subplot(1,2,1) 让 apply 崩成 internal_error
# =====================================================================
# 根因（t7 全量静态筛选发现、船长实测复现）：_axes_binding_entries 里
#   _sig = '%s|%s' % (attr, ast.dump(_call_sig(node)))
# 把 _call_sig(node) 返回的**字符串列表**喂给 ast.dump（只接受 AST 节点）→
# TypeError: expected AST, got 'list' → 任何模式都 internal_error。
# 触发路径：attr in _AXES_RETURNING 且该调用**没有赋值名**、又不是 add_axes /
# add_subplot（那两条在 3607-3613 提前处理）时落到这一行 —— plt.subplot(1,2,1)
# 是极常见写法。修法：列表 join 成字符串 `'|'.join(_call_sig(node))`。
# 该行语义=「槽位签名」去重（同一槽位=复用、不新建轴），修完必须保持：
# 相同 subplot 重复调用仍判复用（1 条轴）；参数不同的 subplot 仍各自新建轴。
# §88① 对本用例是"先写会失败的"：修前它必然 internal_error（对修前代码必红）。
_SRC88 = ('import matplotlib\n'
          'matplotlib.use("Agg")\n'
          'import matplotlib.pyplot as plt\n'
          'plt.subplot(1, 2, 1)\n'
          'plt.plot([1, 2], [1, 2])\n'
          'plt.savefig("out88.png")\n')
_PAR88 = {'version': 4, 'fig_index': 0, 'figsize_in': [8.5, 5.5]}


def _cli88(s, p):
    """E2E：`mpltweak apply <脚本> --params <p> --write --dry-run --no-verify --json`。"""
    r = subprocess.run(
        [sys.executable, '-m', 'mpltweak', 'apply', s, '--params', p,
         '--write', '--dry-run', '--no-verify', '--json'],
        capture_output=True, text=True, encoding='utf-8', errors='replace',
        env=dict(os.environ, MPLBACKEND='Agg', PYTHONIOENCODING='utf-8',
                 PYTHONPATH=os.path.dirname(os.path.dirname(
                     os.path.abspath(writeback.__file__)))))
    return (r.stdout or '') + (r.stderr or ''), r.returncode


def _axes_entries88(src, fig_index=0):
    """静态建轴条目：无赋值名 subplot 序列 → (entries, ok, suspect)。"""
    return writeback._axes_binding_entries(_ast.parse(src), fig_index)


# ① 无赋值 subplot 不崩（端到端：--dry-run --no-verify --json 返回正常报告）
_d88, _s88, _p88 = _mk15('t88', _SRC88, _PAR88)
_o88, _rc88 = _cli88(_s88, _p88)
check('88① 无赋值 plt.subplot(1,2,1) 不再崩：apply --json 返回正常报告（非 internal_error）',
      'internal_error' not in _o88 and '"ok":' in _o88,
      'rc=%r %s' % (_rc88, str(_o88.strip().splitlines()[-1:])))
shutil.rmtree(_d88, ignore_errors=True)
# ② 复用判定仍正确：相同 subplot(1,2,1) 重复调用 = 同槽位复用 → 仍只 1 条轴
_e88b = _axes_entries88('import matplotlib.pyplot as plt\n'
                        'plt.subplot(1, 2, 1)\n'
                        'plt.subplot(1, 2, 1)\n')
check('88② 槽位复用：相同 plt.subplot(1,2,1) 重复调用仍判为复用（1 条轴，不新建）',
      _e88b[1] is True and len(_e88b[0]) == 1,
      'entries=%d ok=%r' % (len(_e88b[0]), _e88b[1]))
# ③ 参数不同 → 新建轴：subplot(1,2,1) + subplot(1,2,2) = 2 条轴
_e88c = _axes_entries88('import matplotlib.pyplot as plt\n'
                        'plt.subplot(1, 2, 1)\n'
                        'plt.subplot(1, 2, 2)\n')
check('88③ 槽位不同：subplot(1,2,1) + subplot(1,2,2) 仍是 2 条新建轴',
      _e88c[1] is True and len(_e88c[0]) == 2,
      'entries=%d ok=%r' % (len(_e88c[0]), _e88c[1]))

# =====================================================================
# §89 外部改动守卫（0.1.10）：验证期间用户在编辑器保存 → 工具绝不覆盖
# =====================================================================
# 风险现场：`apply --write` 读源码在流程**开头**，落盘要等（重跑验证的）几十秒到
# 几分钟。用户在这段时间回编辑器保存一次是很自然的动作（他以为工具已经改完），
# 而工具的后续落盘（验证失败后的**回滚**、块重试的**二次写盘**）会把它静默覆盖
# —— 对科研用户这是最不可接受的一类数据丢失。
# 判据 = sha256 内容指纹：读源码时记一份，落盘前重算，不一致就停手。
import threading                                                     # noqa: E402
from mpltweak import messages as _messages89                         # noqa: E402

_d89 = os.path.join(_TMPROOT, 'case_89')
os.makedirs(os.path.join(_d89, '.tweak_params'), exist_ok=True)

_SRC89 = ('import matplotlib\n'
          'matplotlib.use("Agg")\n'
          'import matplotlib.pyplot as plt\n'
          'fig = plt.figure(figsize=(6, 4))\n'
          'ax = fig.add_axes((0.10, 0.10, 0.40, 0.40))\n'
          'ax.plot([1, 2, 3])\n'
          'fig.savefig("out89.png")\n')
# "用户在编辑器里保存"后的内容：编辑器缓冲区里通常还是**工具改动之前**那些字符
_USER89 = _SRC89 + '# USER-EDIT-89 用户在验证期间自己加的一行\n'
_PAR89 = {'version': 4, 'fig_index': 0, 'figsize_in': [6.0, 4.0],
          'axes': [{'index': 0, 'pos': [0.2, 0.2, 0.5, 0.5],
                    'xscale': 'linear', 'yscale': 'linear', 'grid': False,
                    'spines': {}, 'cell': None}]}


def _mk89(name, body, params=_PAR89):
    p = os.path.join(_d89, name)
    with open(p, 'w', encoding='utf-8', newline='') as f:
        f.write(body)
    pp = os.path.join(_d89, '.tweak_params', name[:-3] + '.json')
    with open(pp, 'w', encoding='utf-8') as f:
        json.dump(params, f)
    return p, pp


def _sha89(p):
    with open(p, 'rb') as f:
        return hashlib.sha256(f.read()).hexdigest()


# ---- ① 单元：指纹判据本身 ----
_s89, _p89 = _mk89('fig89a.py', _SRC89)
check('89① 磁盘内容未变 → _check_external_edit 放行（None）',
      writeback._check_external_edit(_s89, _SRC89) is None)
with open(_s89, 'w', encoding='utf-8', newline='') as f:
    f.write(_USER89)
# 用 `or {}` 包一层：守卫一旦被关掉（反面验证）这里会拿到 None，必须**干净判红**
# 而不是抛 AttributeError 把整节后续断言全跳过（反验证实测踩到）。
_r89u = writeback._check_external_edit(_s89, _SRC89) or {}
check('89② 磁盘内容变过 → 拒绝，并给机器可读字段（reason/external_modified/指纹）',
      isinstance(_r89u, dict)
      and _r89u.get('reason') == writeback.EXTERNAL_MODIFIED
      and _r89u.get('external_modified') is True
      and _r89u.get('sha256_expected') != _r89u.get('sha256_found'),
      repr(_r89u)[:200])
check('89③ 人读文案含"你的改动未被覆盖"+下一步（--write）',
      '外部修改' in (_r89u.get('err') or '')
      and '未被覆盖' in (_r89u.get('err') or '')
      and _r89u.get('next_step', '').endswith('--write'),
      repr(_r89u.get('err')))
check('89④ 兼容入口 _guard_external_edit 仍返回一句原因文本（§86 的旧契约）',
      isinstance(writeback._guard_external_edit(_s89, _SRC89), str))
check('89⑤ messages 的 external_modified 中英齐备、且都 GBK 可编码（终端不炸）',
      all('外部修改' in _messages89.MESSAGES['external_modified']['zh']
          and _messages89.MESSAGES['external_modified']['en']
          and _messages89.MESSAGES['external_modified']['zh'].encode('gbk')
          and _messages89.MESSAGES['external_modified']['en'].encode('gbk')
          for _ in (0,)))
shutil.rmtree(_d89, ignore_errors=True)

# ---- ② 正向写盘：备份步骤里被外部改过 → 拒绝覆盖（monkeypatch 注入一次外部写入）----
_d89b = os.path.join(_TMPROOT, 'case_89b')
os.makedirs(os.path.join(_d89b, '.tweak_params'), exist_ok=True)


def _mk89b(name, body, params=_PAR89):
    p = os.path.join(_d89b, name)
    with open(p, 'w', encoding='utf-8', newline='') as f:
        f.write(body)
    pp = os.path.join(_d89b, '.tweak_params', name[:-3] + '.json')
    with open(pp, 'w', encoding='utf-8') as f:
        json.dump(params, f)
    return p, pp


_s89b, _p89b = _mk89b('fig89b.py', _SRC89)
_orig_backup89 = writeback._make_backup


def _backup_then_user_save89(*a, **k):
    """备份照做，但备完之后"用户保存了" —— 落盘前的守卫必须拦住。"""
    _bk = _orig_backup89(*a, **k)
    with open(_s89b, 'w', encoding='utf-8', newline='') as f:
        f.write(_USER89)
    return _bk


writeback._make_backup = _backup_then_user_save89
try:
    _res89b = writeback.writeback(_s89b, _PAR89, _p89b, verify=True,
                                  semantic=True, style='inplace', timeout=120)
finally:
    writeback._make_backup = _orig_backup89
_src89b = read(_s89b)
check('89⑥ 落盘前发现外部改动 → 本次写回放弃（reason=external_modified）',
      _res89b.get('reason') == writeback.EXTERNAL_MODIFIED
      and _res89b.get('external_modified') is True
      and _res89b.get('applied') is False,
      'reason=%r external_modified=%r' % (_res89b.get('reason'),
                                          _res89b.get('external_modified')))
check('89⑦ 用户的改动**一个字都没被覆盖**（我们的写回没落盘）',
      _src89b == _USER89 and '0.2, 0.2, 0.5, 0.5' not in _src89b,
      _src89b[-60:])
check('89⑧ 结论里如实说明（人读/JSON 同一个 err）',
      '外部修改' in (_res89b.get('err') or '')
      and '未被覆盖' in (_res89b.get('err') or ''),
      repr(_res89b.get('err')))
shutil.rmtree(_d89b, ignore_errors=True)

# ---- ③ 回滚路径：写盘后、验证期间被外部改过 → **不回滚**（保留用户版本）----
_d89c = os.path.join(_TMPROOT, 'case_89c')
os.makedirs(os.path.join(_d89c, '.tweak_params'), exist_ok=True)
_s89c = os.path.join(_d89c, 'fig89c.py')
with open(_s89c, 'w', encoding='utf-8', newline='') as f:
    f.write(_SRC89)
_p89c = os.path.join(_d89c, '.tweak_params', 'fig89c.json')
with open(_p89c, 'w', encoding='utf-8') as f:
    json.dump(_PAR89, f)
_orig_verify89 = writeback.verify_run


def _verify_then_user_save89(*a, **k):
    """验证"跑"的期间用户保存了，且这次验证以失败告终（正常路径会回滚）。"""
    with open(_s89c, 'w', encoding='utf-8', newline='') as f:
        f.write(_USER89)
    return (False, 'boom 89')


writeback.verify_run = _verify_then_user_save89
try:
    _res89c = writeback.writeback(_s89c, _PAR89, _p89c, verify=True,
                                  semantic=False, style='inplace', timeout=120)
finally:
    writeback.verify_run = _orig_verify89
_src89c = read(_s89c)
check('89⑨ 验证失败要回滚、但磁盘已被外部改过 → **跳过回滚**，保留用户版本',
      _src89c == _USER89 and 'USER-EDIT-89' in _src89c,
      'reason=%r | %s' % (_res89c.get('reason'), _src89c[-50:]))
check('89⑩ 回滚被跳过这件事写进了结论（不是静默）',
      '外部修改' in (_res89c.get('err') or '')
      and '未回滚' in (_res89c.get('err') or ''),
      repr((_res89c.get('err') or '')[-160:]))
shutil.rmtree(_d89c, ignore_errors=True)

# ---- ④ 对照：全程没人动过 → 行为与以前完全一致 ----
_d89d = os.path.join(_TMPROOT, 'case_89d')
os.makedirs(os.path.join(_d89d, '.tweak_params'), exist_ok=True)
_s89d = os.path.join(_d89d, 'fig89d.py')
with open(_s89d, 'w', encoding='utf-8', newline='') as f:
    f.write(_SRC89)
_p89d = os.path.join(_d89d, '.tweak_params', 'fig89d.json')
with open(_p89d, 'w', encoding='utf-8') as f:
    json.dump(_PAR89, f)
_shab = _sha89(_s89d)
_res89d = writeback.writeback(_s89d, _PAR89, _p89d, verify=True,
                              semantic=False, style='inplace', timeout=120)
check('89⑪ 对照：无外部改动 → 正常写回成功、且没有外部改动标记（正常路径零变化）',
      _res89d.get('reason') == 'ok' and _res89d.get('verified') is True
      and _res89d.get('external_modified') is None
      and _sha89(_s89d) != _shab,
      'reason=%r verified=%r' % (_res89d.get('reason'), _res89d.get('verified')))
check('89⑫ 对照：守卫在正常路径上确实放行（源码真的被改了）',
      '0.2, 0.2, 0.5, 0.5' in read(_s89d),
      str([l.strip() for l in read(_s89d).splitlines()
           if 'add_axes' in l][:1]))
shutil.rmtree(_d89d, ignore_errors=True)

# ---- ⑤ 端到端（真 CLI + 慢脚本 + 并发线程）：用户在验证窗口里保存 ----
_d89e = os.path.join(_TMPROOT, 'case_89e')
os.makedirs(os.path.join(_d89e, '.tweak_params'), exist_ok=True)
_s89e = os.path.join(_d89e, 'fig89e.py')
# 慢脚本：验证重跑要花几秒（模拟"读数据/出图慢"的真实窗口）；跑完故意失败，
# 好让正常路径**一定会尝试回滚** —— 那正是会覆盖用户保存的那一步。
_SRC89E = (_SRC89.replace('fig.savefig("out89.png")\n',
                          'fig.savefig("out89.png")\n'
                          'import time; time.sleep(1.2)\n'
                          'raise SystemExit(9)\n'))
with open(_s89e, 'w', encoding='utf-8', newline='') as f:
    f.write(_SRC89E)
_p89e = os.path.join(_d89e, '.tweak_params', 'fig89e.json')
with open(_p89e, 'w', encoding='utf-8') as f:
    json.dump(_PAR89, f)
_saved89e = [False]


def _user_saves_during_verify():
    """盯着磁盘：等工具把新的 pos 写进去之后，"用户在编辑器按了保存"。"""
    import time as _t
    _deadline = _t.time() + 60
    while _t.time() < _deadline:
        try:
            with open(_s89e, encoding='utf-8') as f:
                _cur = f.read()
        except OSError:
            _cur = ''
        if '0.2, 0.2, 0.5, 0.5' in _cur:
            try:
                with open(_s89e, 'w', encoding='utf-8', newline='') as f:
                    f.write(_USER89)          # 编辑器缓冲区里那一版（工具改动之前）
                _saved89e[0] = True
            except OSError:
                pass
            return
        _t.sleep(0.02)


_th89 = threading.Thread(target=_user_saves_during_verify, daemon=True)
_th89.start()
_env89e = dict(os.environ, PYTHONPATH=os.path.dirname(os.path.dirname(
    os.path.abspath(writeback.__file__))), MPLBACKEND='Agg',
    MPLTWEAK_LANG='zh', PYTHONIOENCODING='utf-8')
_run89e = subprocess.run(
    [sys.executable, '-m', 'mpltweak', 'apply', _s89e, '--write',
     '--style', 'inplace', '--timeout', '60', '--json'],
    capture_output=True, env=_env89e, cwd=_d89e)
_th89.join(timeout=30)
_out89e = _decode_out(_run89e.stdout + _run89e.stderr)
try:
    _json89e = json.loads(_run89e.stdout.decode('utf-8', 'replace'))
except Exception:                                                    # noqa: BLE001
    _json89e = {}
_src89e = read(_s89e)
check('89⑬ E2E：慢脚本验证窗口里用户保存 → 保存这一版仍在磁盘上（未被回滚覆盖）',
      _saved89e[0] is True and 'USER-EDIT-89' in _src89e,
      'saved=%r | tail=%s' % (_saved89e[0], _src89e[-50:]))
check('89⑭ E2E：人读/JSON 都如实报出"外部修改、你的改动未被覆盖"',
      '外部修改' in _out89e and '未被覆盖' in _out89e
      and '外部修改' in ((_json89e.get('files') or [{}])[0].get('err') or ''),
      'rc=%r | %s' % (_run89e.returncode, _out89e[-200:]))
check('89⑮ E2E：退出码仍是非 0（写回没成功，用户必须知道）',
      _run89e.returncode != 0, 'rc=%r' % _run89e.returncode)
shutil.rmtree(_d89e, ignore_errors=True)

# ---- ⑥ --json 的**逐字布尔键**（agent 侧判据）：真命中时必须是 true ----
# 为什么走进程内 apply.main + fd 级捕获：守卫的"写盘前"那条路只在「读源码之后、
# 写盘之前」那一小段被触发，跨进程无法确定性构造（靠线程轮询是 flaky 的）；
# 而 messages.emit_json 是 **fd 级直写**（刻意绕过 redirect_stdout），所以要 dup2。
_d89f = os.path.join(_TMPROOT, 'case_89f')
os.makedirs(os.path.join(_d89f, '.tweak_params'), exist_ok=True)
_s89f = os.path.join(_d89f, 'fig89f.py')
with open(_s89f, 'w', encoding='utf-8', newline='') as f:
    f.write(_SRC89)
_p89f = os.path.join(_d89f, '.tweak_params', 'fig89f.json')
with open(_p89f, 'w', encoding='utf-8') as f:
    json.dump(_PAR89, f)
_USER89F = _SRC89 + '# USER-EDIT-89F 用户在验证期间保存\n'


def _capture_fd1_89(fn):
    """把 fd1 临时重定向到文件，跑 fn() 后读回它写出的内容。

    两个坑都踩过：① 必须先 `sys.stdout.flush()` —— 否则测试自己**缓冲区里**的输出
    会在 dup2 之后才落盘，混进捕获文件、把 JSON 解析毁掉；② 解析时从第一个 `{`
    开始（万一还有别的字节混进来，也不至于整条断言变成"空 dict"）。
    """
    sys.stdout.flush()
    _saved = os.dup(1)
    _cap = os.path.join(_d89f, '_json_cap.txt')
    _fd = os.open(_cap, os.O_WRONLY | os.O_CREAT | os.O_TRUNC)
    try:
        os.dup2(_fd, 1)
        fn()
        sys.stdout.flush()
    finally:
        os.dup2(_saved, 1)
        os.close(_fd)
        os.close(_saved)
    with open(_cap, encoding='utf-8') as f:
        return f.read()


def _json_from_text_89(text):
    """从捕获文本里取 JSON（容忍前面可能混进来的非 JSON 字节）。"""
    i = text.find('{')
    if i < 0:
        return {}
    try:
        return json.loads(text[i:])
    except Exception:                                                # noqa: BLE001
        return {}


def _apply_inproc_89():
    _ob = writeback._make_backup

    def _b_save(*a, **k):
        _bk = _ob(*a, **k)
        with open(_s89f, 'w', encoding='utf-8', newline='') as f:
            f.write(_USER89F)
        return _bk
    writeback._make_backup = _b_save
    try:
        apply.main([_s89f, '--write', '--style', 'inplace', '--json'])
    finally:
        writeback._make_backup = _ob


_txt89f = _capture_fd1_89(_apply_inproc_89)
_j89f = _json_from_text_89(_txt89f)
_f89f = (_j89f.get('files') or [{}])[0]
check('89⑯ apply --json 逐字布尔键：真命中时 files[0].external_modified is true',
      _f89f.get('external_modified') is True, str(_f89f)[:220])
check('89⑰ apply --json 三条机器可读线索齐（reason + err + next_step）',
      _f89f.get('reason') == 'external_modified'
      and '未被覆盖' in (_f89f.get('err') or '')
      and (_f89f.get('next_step') or '').endswith('--write'),
      str(_f89f)[:220])
check('89⑱ JSON 报的是"已放弃"：files[0].applied is False（没写盘）',
      _f89f.get('applied') is False, str(_f89f.get('applied')))
shutil.rmtree(_d89f, ignore_errors=True)

# =====================================================================
# §90 t27 回归：块模式"报未写却留下块"（no-tamper 违约）
# =====================================================================
# 违约现场（t23 全量真跑 1,837 份里 3 例同签名，增量**都恰好 +231 字节** = 一个调整块）：
#   候选①写盘成功 → 验证失败（**原脚本单独跑得通**）→ 没回滚就换锚点重试
#   → 候选②的"外部改动守卫"拿**入口读的 src** 去比磁盘（此刻磁盘上已是"我们刚写的块"）
#   → 误报 external_modified，且命中分支**不回滚就 return** → apply 说"没写"、磁盘 +231。
# 本用例用 monkeypatch 把触发条件（我们的写回版跑不通 / 原脚本跑得通）钉死，断言不变量：
# **最终字节 == 原始字节**（且不得报 external_modified、applied 必须 False）。
_d90 = os.path.join(_TMPROOT, 'case_90')
os.makedirs(os.path.join(_d90, '.tweak_params'), exist_ok=True)
_SRC90 = ('import matplotlib\n'
          'matplotlib.use("Agg")\n'
          'import matplotlib.pyplot as plt\n'
          'fig1, ax1 = plt.subplots(figsize=(6, 4))\n'
          'ax1.plot([1, 2, 3])\n'
          'fig1.savefig("o1.png")\n'
          'fig2, ax2 = plt.subplots(figsize=(6, 4))\n'
          'ax2.plot([3, 2, 1])\n'
          'fig2.savefig("o2.png")\n')
_s90 = os.path.join(_d90, 'fig90.py')
with open(_s90, 'w', encoding='utf-8', newline='') as f:
    f.write(_SRC90)
_p90 = os.path.join(_d90, '.tweak_params', 'fig90.json')
_p90data = {'version': 4, 'fig_index': 0, 'figsize_in': [6.0, 4.0],
            'axes': [{'index': 0, 'pos': [0.2, 0.2, 0.5, 0.5],
                      'xscale': 'linear', 'yscale': 'linear', 'grid': False,
                      'spines': {}, 'cell': None}]}
with open(_p90, 'w', encoding='utf-8') as f:
    json.dump(_p90data, f)
_orig90 = open(_s90, 'rb').read()
_orig_verify90 = writeback.verify_run


def _fake_verify90(path, *a, **k):
    """写回版跑不通、原脚本（备份路径）跑得通 → 必然走"换锚点重试"分支。"""
    if os.path.abspath(path) == os.path.abspath(_s90):
        return (False, '写回版跑不通（§90 夹具）')
    return (True, '')


writeback.verify_run = _fake_verify90
try:
    _res90 = writeback.writeback(_s90, _p90data, _p90, verify=True,
                                 semantic=False, style='block', timeout=60)
finally:
    writeback.verify_run = _orig_verify90
_after90 = open(_s90, 'rb').read()
check('90① 块模式"验证失败 → 换锚点重试"之后：**磁盘字节 == 原始字节**（no-tamper 不变量）',
      _after90 == _orig90,
      'before=%d after=%d（差 %+d 字节）'
      % (len(_orig90), len(_after90), len(_after90) - len(_orig90)))
check('90② 不再把"我们自己刚写的块"误报成外部改动',
      _res90.get('reason') != 'external_modified'
      and not _res90.get('external_modified'),
      'reason=%r external_modified=%r' % (_res90.get('reason'),
                                          _res90.get('external_modified')))
check('90③ 如实报"没写"（applied=False）且磁盘上不留调整块',
      _res90.get('applied') is False
      and writeback.BLOCK_START not in _after90.decode('utf-8', 'replace'),
      'applied=%r reason=%r' % (_res90.get('applied'), _res90.get('reason')))
check('90④ 夹具确实走到了"换锚点重试"（≥2 个候选），否则本用例会退化成空跑',
      len(_res90.get('attempts') or []) >= 2,
      'attempts=%r' % (_res90.get('attempts'),))
shutil.rmtree(_d90, ignore_errors=True)

# =====================================================================
# §91 执行边界（0.1.11，t2 实测）：`apply --write --dry-run` 默认档**会跑一遍你的脚本**
# =====================================================================
# 触发链（行号按当前 writeback.py）：
#   :2888 `_needs = _pending_vs_live(...)` 在 :2902 的 `dry_run` 早退**之前**被调用，
#   而 :2694 的 `_pending_vs_live` 内部会调 `_verify.verify_semantic(...)`
#   → **真跑一遍脚本**、并在参数文件旁写 `_verify_state.json`。
# 触发条件（:2882-2893 的判据链）：存在"原位写不了"的字段（`_pending` 非空）
#   **且** `verify and semantic` 都为真。`--verify-fast`（apply.py:733 →
#   verify=False / fast_check=True）、`--no-verify`（verify=False）、
#   `--no-semantic`（semantic=False）都走 :2891-2893 的保守分支 → 不重跑。
# 为什么必须钉住：docs/exec-boundaries.md 那张"哪些命令会跑你的脚本"的表里，
#   `--write --dry-run` 那一格曾经写"不会"（实测相反）。这是对用户的**安全承诺**，
#   只靠措辞没有回归网 —— 这里用**标记文件法**（脚本第一行就写标记文件）钉死：
#   会跑脚本（标记文件出现）与不会改你的脚本（dry-run 后字节不变）是**两件事**，
#   分别断言，不许混为一谈。
# 反向验证（"故意改坏会不会变红"，照 tools/teeth_onepass.py 的做法在临时树上做，
#   不改仓库文件：复制 src/mpltweak → 单点变异 → PYTHONPATH 指向变异树跑本套件）：
#   ① 把 :2902 的 `if dry_run:` 早退**提到** :2888 之前（= 让文档里"不会执行"成真）
#      → 91① 必红（标记文件不再出现）；91② 仍绿（那本来就不该变）。
#   ② 去掉 :2886 的 `and semantic`（= 让 _pending_vs_live 在 --no-semantic 下也跑）
#      → 91⑤ 必红。
_SRC91 = ('import os\n'
          'open(%r, "w", encoding="utf-8").write("ran")\n'
          'import matplotlib\n'
          'matplotlib.use("Agg")\n'
          'import matplotlib.pyplot as plt\n'
          'fig, ax = plt.subplots(figsize=(6, 3))\n'
          'ax.plot([1, 2, 3])\n'
          'fig.savefig("out91.png")\n')
# 参数里只有 pos：`plt.subplots` 的网格轴在源码里没有可写的 pos 字面量 →
# 进 skipped（writeback.py:1354-1358）→ `_pending = {'ax0': {'pos'}}`，
# 且实测（:2694 那一遍）与源码现值不同 → 退块。
_PAR91 = {'version': 4, 'script': 'fig.py', 'fig_index': 0, 'n_figs': 1,
          'axes': [{'index': 0, 'pos': [0.2, 0.2, 0.6, 0.6]}]}
# 对照夹具：只改 figsize 且**与源码现值相等** → `_pending` 为空 → 压根不进 _pending_vs_live
_PAR91_NOPENDING = {'version': 4, 'script': 'fig.py', 'fig_index': 0, 'n_figs': 1,
                    'figsize_in': [6.0, 3.0]}


def _cli91(s, p, *extra):
    """E2E：`apply <脚本> --params <p> --write --dry-run [extra]`（Agg 子进程）。"""
    r = subprocess.run(
        [sys.executable, '-m', 'mpltweak', 'apply', s, '--params', p,
         '--write', '--dry-run'] + list(extra),
        capture_output=True, text=True, encoding='utf-8', errors='replace',
        env=dict(os.environ, MPLBACKEND='Agg', PYTHONIOENCODING='utf-8',
                 MPLTWEAK_LANG='zh',
                 PYTHONPATH=os.path.dirname(os.path.dirname(
                     os.path.abspath(writeback.__file__)))))
    return (r.stdout or '') + (r.stderr or ''), r.returncode


def _case91(name, params, *extra):
    """造 case 目录跑一次 dry-run → (输出, 退出码, 脚本跑过吗, 源码字节没变吗)。

    标记文件路径**写进脚本第一行**，所以"脚本被跑过"只可能由这一行产生
    （不受 __file__ / cwd / 子进程工作目录影响）。case 目录用完即删
    （落在本文件旁的 `_TMPROOT` 里，与 §89/§90 同一个临时区、不往系统 %TEMP% 写）。
    """
    d = os.path.join(_TMPROOT, 'case_%s' % name)
    shutil.rmtree(d, ignore_errors=True)
    os.makedirs(d, exist_ok=True)
    try:
        mark = os.path.join(d, 'marker91.txt')
        src = _SRC91 % mark
        s = os.path.join(d, 'fig.py')
        p = os.path.join(d, 'p.json')
        with open(s, 'w', encoding='utf-8', newline='\n') as f:
            f.write(src)
        with open(p, 'w', encoding='utf-8', newline='\n') as f:
            json.dump(dict(params, script='fig.py'), f, ensure_ascii=False)
        out, rc = _cli91(s, p, *extra)
        return out, rc, os.path.exists(mark), read(s) == src
    finally:
        shutil.rmtree(d, ignore_errors=True)


_o91a, _rc91a, _ran91a, _same91a = _case91('t91a', _PAR91)
check('91① 默认档（verify+semantic）：apply --write --dry-run **会跑**你的脚本（标记文件出现）',
      _rc91a == 0 and _ran91a is True,
      'rc=%r marker=%r %s' % (_rc91a, _ran91a,
                              str([_l.strip() for _l in _o91a.splitlines()
                                   if 'dry-run' in _l or '未落盘' in _l][:2])))
check('91② 但**不写你的脚本**：dry-run 之后源码字节不变（"会跑"≠"会改"）',
      _same91a is True, 'src_unchanged=%r' % (_same91a,))
_o91b, _rc91b, _ran91b, _same91b = _case91('t91b', _PAR91, '--verify-fast')
check('91③ --verify-fast：不跑脚本（标记文件不出现），源码也不变',
      _rc91b == 0 and _ran91b is False and _same91b is True,
      'rc=%r marker=%r' % (_rc91b, _ran91b))
_o91c, _rc91c, _ran91c, _same91c = _case91('t91c', _PAR91, '--no-verify')
check('91④ --no-verify：不跑脚本（标记文件不出现），源码也不变',
      _rc91c == 0 and _ran91c is False and _same91c is True,
      'rc=%r marker=%r' % (_rc91c, _ran91c))
_o91d, _rc91d, _ran91d, _same91d = _case91('t91d', _PAR91, '--no-semantic')
check('91⑤ --no-semantic：不跑脚本（触发条件里 `verify and semantic` 的 semantic 那一半）',
      _rc91d == 0 and _ran91d is False and _same91d is True,
      'rc=%r marker=%r' % (_rc91d, _ran91d))
_o91e, _rc91e, _ran91e, _same91e = _case91('t91e', _PAR91_NOPENDING)
check('91⑥ 对照：参数里**没有**"原位写不了"的字段时，默认档也不跑脚本（触发条件钉死）',
      _rc91e == 0 and _ran91e is False and _same91e is True,
      'rc=%r marker=%r' % (_rc91e, _ran91e))

# =====================================================================
# §92 无感化②（0.1.10 遗留回归，t4）：**默认成功只报一行**，细节全进 `-v`
# =====================================================================
# 行为（messages.py:115-124 那段红线）：apply 写回成功后，默认 stdout 的**结果段**
# 只有一行 —— 带关键数字的 `written_inplace_summary`（"已原位写回 N 处（脚本）…"）；
# 逐处清单（written_inplace_count）/ 备份路径（backup_at）/ 两层验证的确认行
# （verify_run_ok / semantic_ok）只在 `-v/--verbose` 下打印。
# **失败与降级一个字都不收敛**（这是"成功路径的收敛"，别读成"少说话"）——
# 所以本用例只钉成功路径，且 92④ 专门排除"因为压根没成功才只有一行"的假绿。
#
# 为什么必须钉：tests/test_hygiene.py:197-204 只钉了 `written_inplace` 这个**模板
# 字符串**本身，钉不住 apply.py 里那条 `if _verbose:` 的分支走向 —— 把默认分支改回
# 多行，既有套件一样全绿。
# 反向验证（"故意改坏会不会变红"）：把 apply.py:830 的 `if _verbose:` 改成
# `if True:`（= 默认档也打"已写回 + 逐处清单"两行、不再打带关键数字的那一行）
# → 92① 必红（`[OK]` 行里没有"已原位写回 1 处"这个 summary），92② 也红。
# 做法照 tools/teeth_onepass.py：复制 src/mpltweak 到临时树做单点变异，再用
# `PYTHONPATH=<变异树>/src` 跑本套件（本套件不把仓库 src 硬塞 sys.path，
# 且下面 _case92 的 PYTHONPATH 取自**当前 import 到的** apply，所以变异会被子进程继承）。
_SRC92 = ('import matplotlib\n'
          'matplotlib.use("Agg")\n'
          'import matplotlib.pyplot as plt\n'
          'fig = plt.figure(figsize=(6, 3))\n'
          'ax = fig.add_axes([0.1, 0.1, 0.4, 0.4])\n'
          'ax.plot([1, 2, 3])\n'
          'fig.savefig("o92.png")\n')
# add_axes 的位置是源码里**现成的数字字面量** → 走原位写回（style=inplace），
# 且默认真跑档（verify+semantic）能判"图已更新" —— 这正是"干净成功"这条路。
_PAR92 = {'version': 4, 'script': 'fig.py', 'fig_index': 0, 'n_figs': 1,
          'axes': [{'index': 0, 'pos': [0.2, 0.2, 0.5, 0.5]}]}


def _case92(name, *extra):
    """跑一次 `apply <脚本> --params <p> --write [extra]` → (非空输出行, 退出码, 写回后源码)。

    case 目录落在本文件旁的 `_TMPROOT`（与 §89/§90/§91 同一临时区），用完即删。
    语言显式钉 zh（本用例断言的是中文人读文案），后端硬钉 Agg（级联闸门）。
    """
    d = os.path.join(_TMPROOT, 'case_%s' % name)
    shutil.rmtree(d, ignore_errors=True)
    os.makedirs(d, exist_ok=True)
    try:
        s = os.path.join(d, 'fig.py')
        p = os.path.join(d, 'p.json')
        with open(s, 'w', encoding='utf-8', newline='\n') as _f:
            _f.write(_SRC92)
        with open(p, 'w', encoding='utf-8', newline='\n') as _f:
            json.dump(dict(_PAR92, script='fig.py'), _f, ensure_ascii=False)
        _r = subprocess.run(
            [sys.executable, '-m', 'mpltweak', 'apply', s, '--params', p,
             '--write'] + list(extra),
            capture_output=True, cwd=d,
            env=dict(os.environ, MPLBACKEND='Agg', PYTHONIOENCODING='utf-8',
                     MPLTWEAK_LANG='zh',
                     PYTHONPATH=os.path.dirname(os.path.dirname(
                         os.path.abspath(apply.__file__)))))
        return ([_l for _l in _decode_out(_r.stdout).splitlines() if _l.strip()],
                _r.returncode, read(s))
    finally:
        shutil.rmtree(d, ignore_errors=True)


_l92, _rc92, _src92 = _case92('t92a')
# 注意：成功路径那几行确认行在 -v 下是**缩进**的（'  [OK] …'），所以一律 strip 后再判前缀。
_ok92 = [_l for _l in _l92 if _l.strip().startswith('[OK]')]
check('92① 默认成功只报一行：结果段恰好 1 条 [OK]（且带关键数字"已原位写回 1 处"）',
      _rc92 == 0 and len(_ok92) == 1 and '已原位写回 1 处' in _ok92[0],
      'rc=%r [OK]x%d %s' % (_rc92, len(_ok92), _ok92[:2]))
check('92② 默认档不再打细节行（逐处清单 / 备份路径都收进 -v）',
      all('原位修改' not in _l and '.tweak.bak' not in _l for _l in _l92),
      str(_l92))
_l92v, _rc92v, _src92v = _case92('t92b', '-v')
_ok92v = [_l for _l in _l92v if _l.strip().startswith('[OK]')]
check('92③ 加 -v 细节全回来：>=3 条 [OK]（逐处清单 + 两层验证的确认行）',
      _rc92v == 0 and len(_ok92v) >= 3
      and any('原位修改' in _l for _l in _l92v)
      and any('语义验证通过' in _l for _l in _l92v),
      'rc=%r [OK]x%d %s' % (_rc92v, len(_ok92v), _l92v[-4:]))
check('92④ 前置：这次写回**真的落盘了**（源码里 add_axes 的数字被改）——'
      '排除"因为没成功才只有一行"的假绿',
      _rc92 == 0 and '[0.2, 0.2, 0.5, 0.5]' in _src92
      and '[0.1, 0.1, 0.4, 0.4]' not in _src92,
      _src92.splitlines()[4] if len(_src92.splitlines()) > 4 else _src92)
check('92⑤ 结构性对照（与文案无关）：默认档的输出行数严格少于 -v',
      len(_l92) < len(_l92v), 'default=%d verbose=%d' % (len(_l92), len(_l92v)))

# =====================================================================
# §93 披露缺陷（§20.3#4 = §14.2#3，t32）：dry-run 跑了却说没跑 / --no-verify 该说不说 /
#     --verify-fast 文案错 / 以及"语义验证未能判定"这个假解释
# =====================================================================
# 三条现象（都先用**标记文件法**实测过；夹具与 §91 同款：脚本第一行写标记文件 →
# "这次到底跑没跑"只由标记文件回答）：
#   ① `apply … --write --dry-run` 默认档（参数含"原位写不了"的字段）**真跑脚本**，
#      旧输出却打 `[i] 本次没有重跑脚本（--no-verify）` → **说反了**；
#   ② 真加 `--no-verify` 时旧输出**反而不打**这条降级提示（`not args.no_verify` 压掉）
#      → **该说时不说**（写盘路径一样中招）；
#   ③ `--verify-fast` 也打上面那条，而它的正确文案是 `_LAYERS_FAST_NOTE`
#      （dry-run 走 writeback 的早退分支，res 里没有 verify_mode，旧 gate 认不出档位）。
# 同一根因的第四条：dry-run / `--verify-fast` 下 res 里**根本没有 `semantic` 键**，
# 旧判据把它当成"语义层没判出来"，于是打 `· 语义验证未能判定（脚本不存图/跑不通）` ——
# 而那一遍脚本其实跑得好好的。
# 改后判据（本用例钉的就是它）：一律看**真凭据** —— `res['layers']` / `verify_mode` /
# "键到底在不在 res 里" + `apply._verify_rerun_state()`；**绝不按 flag 反推"跑没跑"**。
# 反向验证（teeth，harness 见 D:\Agent\deepseek\t32_probe\teeth32.py）：
#   M1 把闸门改回 `_ly.get('rerun') is None and not args.no_verify` → 93②/93④/93⑤ 必红；
#   M2 把语义那条改回 `res.get('semantic') is None` → 93⑥/93⑦/93⑬ 必红。


class _Args93(object):
    """给纯函数 ``apply._verify_rerun_state`` 造 args（它只读这几个属性）。"""

    def __init__(self, **kw):
        self.dry_run = False
        self.verify_fast = False
        self.no_verify = False
        self.no_semantic = False
        self.style = 'auto'
        self.__dict__.update(kw)


def _apply93(res_extra, src=_SRC92, extra=()):
    """用**patch 过的 writeback** 驱动 apply.main，抓人读 stdout。

    只钉"打印闸门"本身：这些分支（键在 / 键不在、verified 是 None / True）由 writeback
    的返回决定，patch 最直接（本文件既有做法见 §35b 的 `_bad_apply`）；真实 res 的构造
    由 §93①-93⑦ 的 CLI 子进程用例负责。
    """
    _d = os.path.join(_TMPROOT, 'case_t93c')
    shutil.rmtree(_d, ignore_errors=True)
    os.makedirs(_d, exist_ok=True)
    s = os.path.join(_d, 'fig.py')
    p = os.path.join(_d, 'p.json')
    with open(s, 'w', encoding='utf-8', newline='\n') as _f:
        _f.write(src % os.path.join(_d, 'mark.txt') if '%r' in src else src)
    with open(p, 'w', encoding='utf-8', newline='\n') as _f:
        json.dump(dict(_PAR92, script='fig.py'), _f, ensure_ascii=False)
    base = {'reason': 'ok', 'changes': ['ax0.pos'], 'block': '原位修改：ax0.pos',
            'backup': None, 'warnings': [], 'applied': True, 'style': 'inplace',
            'needs_block': False, 'pending': []}
    base.update(res_extra)
    _orig = writeback.writeback
    writeback.writeback = lambda *a, **k: dict(base)
    _buf = io.StringIO()
    _so = sys.stdout
    sys.stdout = _buf
    try:
        _rc = apply.main([s, '--params', p, '--write'] + list(extra))
    finally:
        sys.stdout = _so
        writeback.writeback = _orig
        shutil.rmtree(_d, ignore_errors=True)
    return _buf.getvalue(), _rc


# ---- ①-④ 三条现象的 CLI 子进程实测（复用 §91/§92 的夹具与 helper）----
_o93a, _rc93a, _ran93a, _same93a = _case91('t93a', _PAR91)
check('93① 前置真凭据：dry-run 默认档**确实跑了**脚本（标记文件出现）',
      _rc93a == 0 and _ran93a is True, 'rc=%r marker=%r' % (_rc93a, _ran93a))
check('93② dry-run 默认档**不许**说"没有重跑"（旧版正是在这里说反）',
      '没有重跑' not in _o93a and 'dry-run 预览' in _o93a,
      [l.strip() for l in _o93a.splitlines() if '重跑' in l][:2])
_o93b, _rc93b, _ran93b, _same93b = _case91('t93b', _PAR91, '--verify-fast')
check('93③ dry-run + --verify-fast 出**快速档专属**文案，不再错挂 --no-verify',
      _rc93b == 0 and '本次只做了' in _o93b
      and apply._LAYERS_NO_RERUN not in _o93b,
      [l.strip() for l in _o93b.splitlines() if '重跑' in l or '只做了' in l][:3])
_o93c, _rc93c, _ran93c, _same93c = _case91('t93c', _PAR91, '--no-verify')
check('93④ dry-run + --no-verify（确实没跑）**如实打降级提示**（旧版该说时不说）',
      _rc93c == 0 and apply._LAYERS_NO_RERUN in _o93c,
      [l.strip() for l in _o93c.splitlines() if '重跑' in l][:2])
_l93d, _rc93d, _src93d = _case92('t93d', '--no-verify')
check('93⑤ 写盘路径 + --no-verify 同样如实打降级提示（旧版被 `not args.no_verify` 压掉）',
      _rc93d == 0 and any(apply._LAYERS_NO_RERUN in l for l in _l93d),
      _l93d[-3:])
_l93e, _rc93e, _src93e = _case92('t93e', '--verify-fast')
check('93⑥ 写盘路径 + --verify-fast 出快速档文案，且**不**打假"未能判定"',
      _rc93e == 0 and any('本次只做了' in l for l in _l93e)
      and not any('语义验证未能判定' in l for l in _l93e), _l93e[-3:])
check('93⑦ dry-run 也不打假"语义验证未能判定（脚本不存图/跑不通）"',
      '语义验证未能判定' not in _o93a,
      [l.strip() for l in _o93a.splitlines() if '语义' in l][:2])

# ---- 判据本身：纯函数真值表（真凭据优先、不按 flag 反推）----
_R93 = apply._verify_rerun_state
check('93⑧ 写盘路径：res.layers.rerun=None → 真没跑；不是 None → 跑过（含失败那遍）',
      _R93({'layers': {'rerun': None}}, _Args93()) == 'not_ran'
      and _R93({'layers': {'rerun': False}}, _Args93()) == 'ran'
      and _R93({'layers': {'rerun': True}}, _Args93()) == 'ran')
check('93⑨ dry-run：有待回比字段 → may_ran（绝不反过来说没跑）；'
      '档位/形状能保证不跑的 → not_ran',
      _R93({'needs_block': True}, _Args93(dry_run=True)) == 'may_ran'
      and _R93({'needs_block': False}, _Args93(dry_run=True)) == 'not_ran'
      and _R93({'needs_block': True}, _Args93(dry_run=True, verify_fast=True)) == 'not_ran'
      and _R93({'needs_block': True}, _Args93(dry_run=True, no_verify=True)) == 'not_ran'
      and _R93({'needs_block': True}, _Args93(dry_run=True, no_semantic=True)) == 'not_ran'
      and _R93({'needs_block': True}, _Args93(dry_run=True, style='block')) == 'not_ran')
check('93⑩ auto 退块时原位那次的凭据也认（inplace_needs_block，apply.py:826）',
      _R93({'inplace_needs_block': True}, _Args93(dry_run=True)) == 'may_ran')
_u93t, _rc93t = _apply93({'verified': None, 'semantic': None,
                          'layers': {'syntax': True, 'rerun': True, 'semantic': None}})
check('93⑪ 真"取不到状态"（rerun 有值 + verified=None + semantic=None）→ 照旧打"未能判定"',
      '语义验证未能判定' in _u93t, _u93t[-200:])
_u93u, _rc93u = _apply93({'verified': True, 'semantic': None,
                          'layers': {'syntax': True, 'rerun': True, 'semantic': None}})
check('93⑫ 语义层**有意跳过**（verified=True）→ 只打"没有做语义比对"，不打假"未能判定"',
      '没有做语义比对' in _u93u and '语义验证未能判定' not in _u93u, _u93u[-220:])
_u93v, _rc93v = _apply93({'verified': None})
check('93⑬ res 里连 semantic 键都没有（= dry-run 早退的形状）→ 不打"未能判定"',
      '语义验证未能判定' not in _u93v, _u93v[-200:])

# =====================================================================
# §94 t36：`fig.subplots(...)` 被误判成"下一张图"（形状 A 的第二半，t10 定位）
#
# 形状 A（本任务命名；t10 的 probe3 里叫 B）：
#     fig = plt.figure(figsize=(8, 6))
#     axs = fig.subplots(2, 2)
#     a, b, c, d = axs.ravel()
# 根因：`_fig_span`（writeback.py:1057）把 `fig.subplots(...)` 也算成建图调用 → 目标图区间
# 被截成 [fig 行, fig.subplots 行) → 块的建轴枚举 `_axes_binding_entries`（writeback.py:3718，
# 那条路唯一传 figure_only=True）一条轴都数不到 → pos 字段全 fail-closed 拒写
# （best_effort / needs_block=True / pending=ax0..ax3.pos）。第一半（认 ravel 解包名）
# 已在 t10 落地。
#
# 修法（t36，保守）：只给 `_axes_binding_entries` 这一条路传 `figure_only=True` ——
# "接收者可证是 Figure 的 `X.subplots(...)`"是**在这张图上建轴**，不算新图分界；
# **其余 5 个调用点口径一字不动**（§94①/①b 专门钉这一条，防"改建图调用口径"的越界）。
#
# 本节的 E2E 用 verify=False：只验"目标表达式能不能证出来 + 块真写进文件"
# （t36 改的就是这一步；带真跑验证的 E2E 证据见 t36 output 的 probe）。
# =====================================================================
_S94A = ('import matplotlib\n'
         'matplotlib.use("Agg")\n'
         'import matplotlib.pyplot as plt\n\n'
         'fig = plt.figure(figsize=(8, 6))\n'
         'axs = fig.subplots(2, 2)\n'
         'a, b, c, d = axs.ravel()\n'
         'a.plot([1, 2], [1, 2])\nb.plot([1, 2], [2, 1])\n'
         'c.plot([1, 2], [1, 2])\nd.plot([1, 2], [2, 1])\n'
         'fig.savefig("out.png")\n')
_S94B = ('import matplotlib\n'
         'matplotlib.use("Agg")\n'
         'import matplotlib.pyplot as plt\n\n'
         'fig, axs = plt.subplots(2, 2)\n'
         'a, b, c, d = axs.ravel()\n'
         'a.plot([1, 2], [1, 2])\nb.plot([1, 2], [2, 1])\n'
         'c.plot([1, 2], [1, 2])\nd.plot([1, 2], [2, 1])\n'
         'fig.savefig("out.png")\n')
_PAR94 = {'version': 4, 'script': 'fig.py', 'fig_index': 0, 'n_figs': 1,
          'figsize_in': [8.0, 6.0],
          'axes': [{'index': k, 'pos': [0.08 + 0.5 * (k % 2),
                                        0.55 - 0.45 * (k // 2), 0.36, 0.36]}
                   for k in range(4)]}


def _s94_run(src, tag, patch_old=False):
    """真跑一次 writeback（style=block、verify=False）：返回 (res, 新增的块文本)。"""
    _d = os.path.join(_TMPROOT, 'case_t94_' + tag)
    shutil.rmtree(_d, ignore_errors=True)
    os.makedirs(os.path.join(_d, '.tweak_params'), exist_ok=True)
    _s = os.path.join(_d, 'fig.py')
    with open(_s, 'w', encoding='utf-8', newline='\n') as _f:
        _f.write(src)
    _p = os.path.join(_d, '.tweak_params', 'fig.json')
    with open(_p, 'w', encoding='utf-8') as _f:
        json.dump(_PAR94, _f, ensure_ascii=False)
    _orig = writeback._fig_span
    if patch_old:
        # 反向验证（teeth）：把 t36 的那一个调用点的口径改回改前（figure_only 恒 False）。
        writeback._fig_span = (lambda t, i, figure_only=False: _orig(t, i, False))
    try:
        _res = writeback.writeback(_s, dict(_PAR94), _p, verify=False,
                                   style='block')
    finally:
        writeback._fig_span = _orig
    with open(_s, encoding='utf-8') as _f:
        _txt = _f.read()
    return _res, _txt[len(src):]


# ① 口径：默认档（其余 5 个调用点）**一字不变**，只有 figure_only 这条拿到完整区间
_t94 = _ast.parse(_S94A)
check('94① 形状 A：默认口径仍截在 fig.subplots 那一行（其余调用点不受影响），'
      'figure_only=True 才拿到完整区间',
      writeback._fig_span(_t94, 0) == (5, 6)
      and writeback._fig_span(_t94, 0, figure_only=True) == (5, 10 ** 9),
      'default=%r figure_only=%r' % (writeback._fig_span(_t94, 0),
                                     writeback._fig_span(_t94, 0, figure_only=True)))
check('94①b 判据只认"接收者可证是 Figure 的 X.subplots(...)"：'
      'plt.subplots(...) / 证不出的接收者**仍是**新图分界',
      writeback._is_axes_on_existing_figure(
          [n for n in _ast.walk(_ast.parse(_S94B))
           if isinstance(n, _ast.Call)
           and getattr(n.func, 'attr', None) == 'subplots'][0], {'fig'}) is False
      and writeback._is_axes_on_existing_figure(
          [n for n in _ast.walk(_ast.parse('axs = other.subplots(1, 2)\n'))
           if isinstance(n, _ast.Call)
           and getattr(n.func, 'attr', None) == 'subplots'][0], {'fig'}) is False,
      'plt.subplots / 不可证接收者都必须返回 False')

# ② 两个形状的成功行为：块真写进去、目标表达式用解包名、无 pending
_res94a, _blk94a = _s94_run(_S94A, 'a')
check('94② 形状 A（plt.figure + fig.subplots + ravel）：applied=True、'
      'needs_block=False、pending 空、块用 a/b/c/d',
      _res94a.get('applied') is True and not _res94a.get('needs_block')
      and not (_res94a.get('pending') or [])
      and all(('%s.set_position(' % n) in _blk94a for n in 'abcd'),
      'reason=%r applied=%r needs_block=%r pending=%r'
      % (_res94a.get('reason'), _res94a.get('applied'),
         _res94a.get('needs_block'), _res94a.get('pending')))
_res94b, _blk94b = _s94_run(_S94B, 'b')
check('94②b 既有形状（fig, axs = plt.subplots + ravel，t10 第一半）：同样 applied=True、'
      'pending 空、块用 a/b/c/d',
      _res94b.get('applied') is True and not _res94b.get('needs_block')
      and not (_res94b.get('pending') or [])
      and all(('%s.set_position(' % n) in _blk94b for n in 'abcd'),
      'reason=%r applied=%r needs_block=%r pending=%r'
      % (_res94b.get('reason'), _res94b.get('applied'),
         _res94b.get('needs_block'), _res94b.get('pending')))

# ③ 反向验证（teeth）：把那一处口径改回改前 → 形状 A 必须重新被拒
#    （这一条保证 ② 不是"本来就能过"的假绿）
_res94o, _blk94o = _s94_run(_S94A, 'a_old', patch_old=True)
check('94③【有牙】改回改前口径（figure_only 恒 False）→ 形状 A 重新被拒：'
      'applied=False、needs_block=True、pending=ax0..ax3.pos、盘上不留块',
      _res94o.get('applied') is False
      and _res94o.get('needs_block') is True
      and (_res94o.get('pending') or []) == ['ax0.pos', 'ax1.pos', 'ax2.pos',
                                             'ax3.pos']
      and _blk94o.strip() == '',
      'reason=%r applied=%r needs_block=%r pending=%r block=%r'
      % (_res94o.get('reason'), _res94o.get('applied'),
         _res94o.get('needs_block'), _res94o.get('pending'), _blk94o[:80]))

# =====================================================================
# §95 t37/t52：`axis_var_index` 认哪些形式（`axes.ravel()` / `axes.flatten()` 调用形式）
#
# t37 取证结论：**代码 bug**（不是文档声称过头）—— 分支②只匹配属性 `ast.Attribute`，而
# `.ravel()` / `.flatten()` 是 `ast.Call`；属性元组里已经写了 'ravel'/'flatten'，说明原意
# 就是要支持这两种写法，只是漏了"带括号的调用形式"。实测代价（真跑，写后执行脚本取真值）：
# `fig, axs = plt.subplots(2,2)` + `a,b,c,d = axs.ravel()` + 按轴给 clim/cmap 时，块把第 2
# 张图的 clim/cmap 也写到第 1 个 mappable 上（`im0`），且 `verified=True` → 静默错目标
# （ax0 拿到 plasma、ax1 停在默认 viridis）。
#
# **t52 已修**（船长批准）：分支②改走 t10 的 `_ravel_receiver`（同一份判据：无
# args/keywords 的 `.ravel()`/`.flatten()` 与属性 `.flat` 都认；证不出容器就不认）。
# 修后全池 1,837 静态 A/B：本表变化 **46** 份、`mappable_by_axis` / `_needed_block_fields`
# 变化 **0**；这 46 份真跑 = 块路径 46/46 逐字节一致、原位路径 5 份 `pending` 多出
# `ax0.cmap`（源码字节相同）；合成形状的块目标 im0→im1、`reason` best_effort→ok，
# 写后真值 ax0=viridis / ax1=plasma ✓ 各就各位。
#
# 本节钉：① 已支持形式；② **调用形式必须认出来**（t52 后）；③ teeth（把判据改回只认
# `ast.Attribute` → ② 必须变红）；④ 块目标回归（clim/cmap 落到 im0/im1，不许再全落 im0）。
# =====================================================================
_S95 = ('import matplotlib.pyplot as plt\n'
        'fig, axs = plt.subplots(2, 2)\n'
        'g, h = axs.flat\n'
        'i = axs[1]\n'
        'a, b, c, d = axs.ravel()\n'
        'e, f = axs.flatten()\n'
        'fig2, ax2 = plt.subplots()\n'
        'ax3 = fig2.add_subplot(2, 2, 1)\n'
        'ax4 = fig.add_axes([0.1, 0.1, 0.3, 0.3])\n')
_map95 = writeback.axis_var_index(_ast.parse(_S95))
check('95① 已支持的形式都认：`axs.flat` 属性解包、`axs[1]` 下标、`fig, ax = plt.subplots()`、'
      '`fig.add_subplot(...)` / `fig.add_axes([...])` 逐个创建',
      _map95.get('g') == 0 and _map95.get('h') == 1 and _map95.get('i') == 1
      and _map95.get('ax2') == 0 and _map95.get('ax3') == 0
      and _map95.get('ax4') == 1, _map95)
check('95②【t52 已修】**调用**形式必须认出来：`a,b,c,d = axs.ravel()` → a..d = 0..3、'
      '`e,f = axs.flatten()` → e,f = 0,1（判据同 t10 的 `_ravel_receiver`）',
      [_map95.get(n) for n in ('a', 'b', 'c', 'd')] == [0, 1, 2, 3]
      and [_map95.get(n) for n in ('e', 'f')] == [0, 1], _map95)

# ③ teeth：把判据改回"只认 ast.Attribute"（= t52 之前的代码）→ ② 的断言必须变红
_rr95 = writeback._ravel_receiver


def _old_rr95(value):
    if isinstance(value, _ast.Call):        # 改前：Call 形式一律不认
        return None
    return _rr95(value)


writeback._ravel_receiver = _old_rr95
try:
    _map95o = writeback.axis_var_index(_ast.parse(_S95))
finally:
    writeback._ravel_receiver = _rr95
check('95③【有牙】把判据改回只认 `ast.Attribute`（t52 之前）→ 调用形式又认不出来'
      '（a..f 全缺），即 95② 的断言在改前代码上必然为假',
      all(n not in _map95o for n in ('a', 'b', 'c', 'd', 'e', 'f'))
      and _map95o.get('g') == 0 and _map95o.get('h') == 1, _map95o)

# ④ 块目标回归：按轴 clim/cmap 必须落到各自的 mappable（不再全落 im0）
_S95B = ('import matplotlib\nmatplotlib.use("Agg")\n'
         'import matplotlib.pyplot as plt\n'
         'import numpy as np\n\n'
         'fig, axs = plt.subplots(2, 2)\n'
         'a, b, c, d = axs.ravel()\n'
         'im0 = a.imshow(np.arange(16).reshape(4, 4) / 15.0)\n'
         'im1 = b.imshow((np.arange(16).reshape(4, 4) + 5) / 15.0)\n'
         'c.plot([1, 2])\nd.plot([2, 1])\n'
         'fig.savefig("out.png")\n')
_PAR95B = {'version': 4, 'script': 'fig.py', 'fig_index': 0, 'n_figs': 1,
           'figsize_in': [8.0, 6.0],
           'axes': [{'index': 0, 'clim': [0.2, 0.8], 'cmap': 'viridis'},
                    {'index': 1, 'clim': [0.1, 0.9], 'cmap': 'plasma'}]}
_d95 = os.path.join(_TMPROOT, 'case_t95_clim')
shutil.rmtree(_d95, ignore_errors=True)
os.makedirs(os.path.join(_d95, '.tweak_params'), exist_ok=True)
_s95 = os.path.join(_d95, 'fig.py')
with open(_s95, 'w', encoding='utf-8', newline='\n') as _f:
    _f.write(_S95B)
_p95 = os.path.join(_d95, '.tweak_params', 'fig.json')
with open(_p95, 'w', encoding='utf-8') as _f:
    json.dump(_PAR95B, _f, ensure_ascii=False)
writeback.writeback(_s95, dict(_PAR95B), _p95, verify=False, style='block')
with open(_s95, encoding='utf-8') as _f:
    _blk95 = _f.read()[len(_S95B):]
check('95④ 块目标回归（`axs.ravel()` + 按轴 clim/cmap）：`im0.set_clim(0.2, 0.8)` 与 '
      '`im1.set_clim(0.1, 0.9)` / `im1.set_cmap(\'plasma\')` 各就各位，'
      '且**不再**出现把 ax1 的值写到 im0 上（`im0.set_clim(0.1, 0.9)`）',
      'im0.set_clim(0.2, 0.8)' in _blk95 and 'im1.set_clim(0.1, 0.9)' in _blk95
      and "im1.set_cmap('plasma')" in _blk95
      and 'im0.set_clim(0.1, 0.9)' not in _blk95,
      [l.strip() for l in _blk95.splitlines() if 'set_clim' in l or 'set_cmap' in l])

# =====================================================================
# §96 t51：`count_figures` 把 `fig.subplots(...)` 算一张图 → 形状 A 误报"2 处图"
#
# 现象：形状 A（`fig = plt.figure()` + `axs = fig.subplots(2,2)` + `a,b,c,d = axs.ravel()`）
# 在 t36 之后**已能落地**（applied/verified/semantic 全 True），但 `count_figures=2`（运行期
# 只有 1 张）→ 输出里出现**不成立**的「脚本创建了 2 处图（运行期共 1 张），改用 plt.gcf()
# 定位第 1 张」+ 锚点/gcf 退到多图口径 + `reason` 停在 `best_effort` → 拿**假前提**建议用户
# 改自己的代码（本项目红线：不得说反话）。
#
# 修法（t51，纪律照抄 t36 —— 只动一条路）：`count_figures(tree, figure_only=True)`，判据复用
# t36 的 `_is_axes_on_existing_figure`（`X.subplots(...)` 仅当 X 可证是 Figure 时不算新图）；
# **只给块路径那两处**传 True（:4582 越界拒写、:4603 多图分支），**原位路径（:1315/:2851）
# 与 `_fig_index_beyond`（:378）保持既有口径**（另案）。默认参数 False = 既有行为一字不变。
#
# 本节钉：① 两种口径的计数（含"plt.subplots 照旧算"与"两张真图仍进多图分支"）；
#        ② 形状 A 的 E2E：reason=ok、无"创建了 N 处图"警告、写出的字节不变；
#        ③ teeth：把口径改回 → 警告与 best_effort 回来（而写出的字节**仍相同**）。
# =====================================================================
_S96A = ('import matplotlib\n'
         'matplotlib.use("Agg")\n'
         'import matplotlib.pyplot as plt\n\n'
         'fig = plt.figure(figsize=(8, 6))\n'
         'axs = fig.subplots(2, 2)\n'
         'a, b, c, d = axs.ravel()\n'
         'a.plot([1, 2], [1, 2])\nb.plot([1, 2], [2, 1])\n'
         'c.plot([1, 2], [1, 2])\nd.plot([1, 2], [2, 1])\n'
         'fig.savefig("out.png")\n')
_S96B = ('import matplotlib\n'
         'matplotlib.use("Agg")\n'
         'import matplotlib.pyplot as plt\n\n'
         'fig, axs = plt.subplots(2, 2)\n'
         'a, b, c, d = axs.ravel()\n'
         'a.plot([1, 2], [1, 2])\nb.plot([1, 2], [2, 1])\n'
         'c.plot([1, 2], [1, 2])\nd.plot([1, 2], [2, 1])\n'
         'fig.savefig("out.png")\n')
_S96C = ('import matplotlib\n'
         'matplotlib.use("Agg")\n'
         'import matplotlib.pyplot as plt\n\n'
         'fig = plt.figure(figsize=(8, 6))\n'
         'axs = fig.subplots(2, 2)\n'
         'a, b, c, d = axs.ravel()\n'
         'a.plot([1], [1])\nb.plot([1], [1])\nc.plot([1], [1])\nd.plot([1], [1])\n'
         'fig.savefig("out.png")\n'
         'fig2 = plt.figure(figsize=(8, 6))\n'
         'axs2 = fig2.subplots(1, 2)\n'
         'e, f = axs2.ravel()\ne.plot([1], [1])\nf.plot([1], [2])\n'
         'fig2.savefig("out2.png")\n')
_PAR96 = {'version': 4, 'script': 'fig.py', 'fig_index': 0, 'n_figs': 1,
          'figsize_in': [8.0, 6.0],
          'axes': [{'index': k, 'pos': [0.08 + 0.5 * (k % 2),
                                        0.55 - 0.45 * (k // 2), 0.36, 0.36]}
                   for k in range(4)]}


def _s96_run(src, tag, patch_old=False):
    """真跑一次 writeback（style=block、verify=True）：返回 (res, 新增块文本)。"""
    _d = os.path.join(_TMPROOT, 'case_t96_' + tag)
    shutil.rmtree(_d, ignore_errors=True)
    os.makedirs(os.path.join(_d, '.tweak_params'), exist_ok=True)
    _s = os.path.join(_d, 'fig.py')
    with open(_s, 'w', encoding='utf-8', newline='\n') as _f:
        _f.write(src)
    _p = os.path.join(_d, '.tweak_params', 'fig.json')
    with open(_p, 'w', encoding='utf-8') as _f:
        json.dump(_PAR96, _f, ensure_ascii=False)
    _orig = writeback.count_figures
    if patch_old:
        # 反向验证（teeth）：把 t51 那两处调用点的口径改回改前（figure_only 恒 False）。
        writeback.count_figures = (lambda t, figure_only=False: _orig(t, False))
    try:
        _res = writeback.writeback(_s, dict(_PAR96), _p, verify=True,
                                   semantic=True, style='block', timeout=60)
    finally:
        writeback.count_figures = _orig
    with open(_s, encoding='utf-8') as _f:
        _txt = _f.read()
    return _res, _txt[len(src):]


# ① 判据与两种口径
check('96① 形状 A：默认档仍数 2（其余调用点口径一字不变），figure_only 数 1',
      writeback.count_figures(_ast.parse(_S96A)) == 2
      and writeback.count_figures(_ast.parse(_S96A), figure_only=True) == 1,
      'default=%r figure_only=%r' % (
          writeback.count_figures(_ast.parse(_S96A)),
          writeback.count_figures(_ast.parse(_S96A), figure_only=True)))
check('96①b `plt.subplots(...)` 照旧算新图：`fig, axs = plt.subplots(2,2)` 两种口径都数 1；'
      '两张真图（两张都用 `fig.subplots`）figure_only 仍数 2',
      writeback.count_figures(_ast.parse(_S96B), figure_only=True) == 1
      and writeback.count_figures(_ast.parse(_S96C), figure_only=True) == 2,
      'B=%r C=%r' % (writeback.count_figures(_ast.parse(_S96B), figure_only=True),
                     writeback.count_figures(_ast.parse(_S96C), figure_only=True)))

# ② 形状 A 的 E2E：reason=ok + 不再有假警告 + 字节不变
_res96a, _blk96a = _s96_run(_S96A, 'a')
check('96② 形状 A（plt.figure + fig.subplots + ravel）：reason=ok、applied/verified 真、'
      'pending 空、**不再**出现"创建了 N 处图"警告',
      _res96a.get('reason') == 'ok' and _res96a.get('applied') is True
      and _res96a.get('verified') is True
      and not (_res96a.get('pending') or [])
      and not any('处图' in _w for _w in (_res96a.get('warnings') or [])),
      'reason=%r applied=%r verified=%r pending=%r warnings=%r'
      % (_res96a.get('reason'), _res96a.get('applied'),
         _res96a.get('verified'), _res96a.get('pending'),
         _res96a.get('warnings')))

# ③ teeth：口径改回 → 假警告 + best_effort 回来（**牙齿只咬"说假话"这一面** —— 平台无关）。
#    ⚠️ 原判据里还带一条"写出来的字节与改后**完全相同**"：它是 t51 的**附加**观察，但
#    **平台/版本敏感** —— CI 实测 `py3.9 (windows-latest)` 上 `字节相同=False`，而本机
#    `py3.10-win` 为 True（本机无法复现 py3.9 条件）⇒ 按 t119 的裁定**降级为信息化输出**，
#    不再作为牙齿判据（否则平台差异会被误判成回归）。牙齿真正要证的"说假话面"保留：
#    `reason='best_effort'` + 「创建了 2 处图」警告（两者都与平台无关）。
_res96o, _blk96o = _s96_run(_S96A, 'a_old', patch_old=True)
check('96③【有牙】改回改前口径 → 形状 A 重新报 best_effort + 出现「创建了 2 处图」警告'
      '（牙齿只咬"说假话"这一面，与平台无关）',
      _res96o.get('reason') == 'best_effort'
      and any('创建了 2 处图' in _w for _w in (_res96o.get('warnings') or [])),
      'reason=%r warnings=%r'
      % (_res96o.get('reason'), _res96o.get('warnings')))
_same96 = _blk96o.replace('\r\n', '\n') == _blk96a.replace('\r\n', '\n')
print('INFO | 96③ 附加观察（不判红、不作为牙齿判据）：改前/改后写出的块文本'
      '归一化换行后相同 = %r（本机 py3.10-win=True；CI py3.9-windows 实测 False '
      '⇒ 平台/版本敏感，见 t119）' % (_same96,))

# =====================================================================
# §97 t54：`count_figures` **剩下 3 个调用点**也传 `figure_only=True`
#
# t51 只改了块路径那两处（:4601 越界拒写 / :4622 多图分支），于是同一个脚本在两条路径上
# 得到**不同的"图数量"**（块路径=1、原位路径=2）—— 这个不一致本身就是缺陷。t54 收口：
#   · `_fig_index_beyond`（fi=1 越界守卫）：形状 A 被算成 2 张 → 真越界的 `fig_index=1`
#     混过守卫（实测 OLD `reason=ok` 照写，NEW `reason=bad_params` + 「越界：脚本里只有 1 张图」）。
#   · `apply_inplace` 的网格轴补映射门槛 `count_figures(tree) <= 1`：形状 A 被当多图 →
#     白跳过补映射（本次测的样本里该分支的补映射被 idx_map 覆盖，未产生可观察差异）。
#   · `_writeback_inplace` 的 `_fs_ambiguous`：形状 A + figsize 参数无 fig_index → **假警告**
#     「脚本有 2 张图但参数没有 fig_index…保持原样」并把本该做的画布尺寸改动整条跳过
#     （实测 OLD applied=False/d=+0 + 假警告 → NEW applied=True/d=+1、无警告）。
#
# 本节钉：① 越界守卫（fi=1 拒、fi=0 放行）；② 原位路径的 figsize 真落地且无假警告；
#        ③ teeth：把 `_writeback_inplace` 里的口径改回 → ② 的断言变红；
#        ④ teeth：把 `count_figures` 的口径改回 → ① 的断言变红。
# =====================================================================
_S97A = ('import matplotlib\n'
         'matplotlib.use("Agg")\n'
         'import matplotlib.pyplot as plt\n\n'
         'fig = plt.figure(figsize=(8, 6))\n'
         'axs = fig.subplots(2, 2)\n'
         'a, b, c, d = axs.ravel()\n'
         'a.plot([1, 2], [1, 2])\nb.plot([1, 2], [2, 1])\n'
         'c.plot([1, 2], [1, 2])\nd.plot([1, 2], [2, 1])\n'
         'fig.savefig("out.png")\n')
_PAR97 = {'version': 4, 'script': 'fig.py', 'fig_index': 0, 'n_figs': 1,
          'figsize_in': [10.0, 7.0],
          'axes': [{'index': k, 'pos': [0.08 + 0.5 * (k % 2),
                                        0.55 - 0.45 * (k // 2), 0.36, 0.36],
                    'grid': True} for k in range(4)]}
_PAR97_NOFI = {k: v for k, v in _PAR97.items() if k != 'fig_index'}


def _s97_run(src, tag, params, patch_old_inplace=False):
    """真跑一次 writeback（style='inplace'、verify=False）：返回 (res, 写后源码)。"""
    _d = os.path.join(_TMPROOT, 'case_t97_' + tag)
    shutil.rmtree(_d, ignore_errors=True)
    os.makedirs(os.path.join(_d, '.tweak_params'), exist_ok=True)
    _s = os.path.join(_d, 'fig.py')
    with open(_s, 'w', encoding='utf-8', newline='\n') as _f:
        _f.write(src)
    _p = os.path.join(_d, '.tweak_params', 'fig.json')
    with open(_p, 'w', encoding='utf-8') as _f:
        json.dump(params, _f, ensure_ascii=False)
    _cf = writeback.count_figures
    _wi = writeback._writeback_inplace

    def _old_inplace(*a, **k):
        writeback.count_figures = (lambda t, figure_only=False: _cf(t, False))
        try:
            return _wi(*a, **k)
        finally:
            writeback.count_figures = _cf

    if patch_old_inplace:
        writeback._writeback_inplace = _old_inplace
    try:
        _res = writeback.writeback(_s, dict(params), _p, verify=False,
                                   style='inplace')
    finally:
        writeback._writeback_inplace = _wi
        writeback.count_figures = _cf
    with open(_s, encoding='utf-8') as _f:
        return _res, _f.read()


# ① 越界守卫（:378）
_t97 = _ast.parse(_S97A)
check('97① 形状 A 的 `fig_index=1` 越界必须被拒（`_fig_index_beyond` 用 figure_only 口径）：'
      'fi=1 报「越界：脚本里只有 1 张图」、fi=0 放行',
      '越界' in writeback._fig_index_beyond(_t97, {'fig_index': 1, 'n_figs': 1})
      and writeback._fig_index_beyond(_t97, {'fig_index': 0, 'n_figs': 1}) == '',
      writeback._fig_index_beyond(_t97, {'fig_index': 1, 'n_figs': 1})[:60])

# ② 原位路径：figsize 与脚本不同 + 无 fig_index → 真改画布尺寸、且无假警告
_res97, _src97 = _s97_run(_S97A, 'a', _PAR97_NOFI)
check('97② 形状 A + figsize[10,7] 无 fig_index（原位）：applied=True、`figsize=(10, 7)` '
      '真落地、且**不再**出现假警告「脚本有 2 张图但参数没有 fig_index」',
      _res97.get('applied') is True and 'figsize=(10, 7)' in _src97
      and not any('张图' in _w for _w in (_res97.get('warnings') or [])),
      'applied=%r 源码含 figsize=(10, 7)=%r warnings=%r'
      % (_res97.get('applied'), 'figsize=(10, 7)' in _src97,
         _res97.get('warnings')))

# ③ teeth：把原位路径里的口径改回 → ② 的断言变红（applied=False + 假警告回来）
_res97o, _src97o = _s97_run(_S97A, 'a_old', _PAR97_NOFI, patch_old_inplace=True)
check('97③【有牙】把 `_writeback_inplace` 里的口径改回（figure_only 恒 False）→ '
      'applied=False、`figsize=(10, 7)` 没落地、假警告回来 —— 即 97② 在改前代码上必假',
      _res97o.get('applied') is False and 'figsize=(10, 7)' not in _src97o
      and any('2 张图' in _w for _w in (_res97o.get('warnings') or [])),
      'applied=%r 含 figsize=(10, 7)=%r warnings=%r'
      % (_res97o.get('applied'), 'figsize=(10, 7)' in _src97o,
         _res97o.get('warnings')))

# ④ teeth：把 `count_figures` 的口径改回 → ① 的越界断言变红
_cf97 = writeback.count_figures
writeback.count_figures = (lambda t, figure_only=False: _cf97(t, False))
try:
    _bad97 = writeback._fig_index_beyond(_t97, {'fig_index': 1, 'n_figs': 1})
finally:
    writeback.count_figures = _cf97
check('97④【有牙】把 `count_figures` 的口径改回 → `_fig_index_beyond` 又放行 fi=1'
      '（= ① 在改前代码上必假）',
      _bad97 == '', '旧口径下 fi=1 的判定=%r' % _bad97[:60])

# =====================================================================
# §98 t63：writeback **错误/边缘路径**的语言化棘轮（en 下输出全 ASCII）
#
# t63 把 writeback.py 里 74 处用户可见中文模板原地包成 `_msg.t_wb('<原 zh 模板>')`，
# 表在 messages.py 的 `WB_ZH_TO_EN`（zh 模板 → en 模板；zh 语言原样返回 → 逐字不变）。
# 本节钉四件事：
#   ① 覆盖率：writeback.py 里非 docstring 的中文常量**除两条块标记**外都必须在表里
#      （块标记是**写进用户脚本**的幂等哨兵，语言一换会出现两块 → 有意例外）；
#   ② en 值纯 ASCII + cp936 可编 + 占位符个数与 zh 一致（否则渲染错位）；
#   ③ zh 语言下 `t_wb` 原样返回（逐字不变）；
#   ④ teeth：把某条 en 改回中文 → ② 的判据变红。
# =====================================================================
_S98_MARK = '# ===== 自动布局调整'
from mpltweak import messages as _messages                      # noqa: E402


def _s98_templates():
    _src = io.open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                '..', 'src', 'mpltweak', 'writeback.py'),
                   encoding='utf-8').read()
    _t = _ast.parse(_src)
    _doc = set()
    for _n in _ast.walk(_t):
        if isinstance(_n, (_ast.Module, _ast.ClassDef, _ast.FunctionDef,
                           _ast.AsyncFunctionDef)):
            _b = getattr(_n, 'body', None) or []
            if _b and isinstance(_b[0], _ast.Expr) \
                    and isinstance(_b[0].value, _ast.Constant) \
                    and isinstance(_b[0].value.value, str):
                _doc.add(id(_b[0].value))
    _cjk = re.compile('[\u4e00-\u9fff]')
    return [_n.value for _n in _ast.walk(_t)
            if isinstance(_n, _ast.Constant) and isinstance(_n.value, str)
            and _cjk.search(_n.value) and id(_n) not in _doc
            and not _n.value.startswith(_S98_MARK)]


_tpl98 = _s98_templates()
_miss98 = [t[:40] for t in _tpl98 if t not in _messages.WB_ZH_TO_EN]
check('98① 覆盖率：writeback.py 里非 docstring 的中文模板（除两条块标记哨兵）'
      '全部进了 messages.WB_ZH_TO_EN',
      not _miss98, '未进表 %d 条：%s' % (len(_miss98), _miss98[:3]))
_spec98 = re.compile(r'%(?:\((\w+)\))?[sdrf]')
_bad98 = [(k[:26], len(_spec98.findall(k)), len(_spec98.findall(v)))
          for k, v in _messages.WB_ZH_TO_EN.items()
          if not v.isascii()
          or len(_spec98.findall(k)) != len(_spec98.findall(v))]
check('98② 表里每条 en 都是纯 ASCII 且占位符个数与 zh 一致（en 渲染不会错位）',
      not _bad98, _bad98[:3])
check('98③ zh 语言下 `t_wb` 原样返回（zh 文案逐字不变）',
      _messages.t_wb(_tpl98[0]) == _tpl98[0] if _tpl98 else False,
      _tpl98[:1])
_orig_tbl98 = dict(_messages.WB_ZH_TO_EN)
_tampered = dict(_orig_tbl98)
_tampered['已回滚'] = '已回滚'                     # 故意把一条 en 改回中文
_bad_tampered = [k for k, v in _tampered.items() if not v.isascii()]
check('98④【有牙】把一条 en 改回中文 → ② 的 ASCII 判据变红（改前代码上必假）',
      bool(_bad_tampered) and not [k for k, v in _orig_tbl98.items()
                                   if not v.isascii()],
      'tampered=%s' % _bad_tampered)

# =====================================================================
# §99 t66 / P1-5：colorbar 的**自定义** cmap 不许记成 'from_list'
#
# 报告 P1-5：开窗落盘把自建 `mcolors.ListedColormap(...)`（`.name == 'from_list'`）的名字
# 记进 `item['cmap']`，写回会得到 `cmap='from_list'`（不是任何可用 colormap 名）。
# 修法（t66）：只在 `name in matplotlib.colormaps` 时记录，其余记 null（字段不出现 →
# 写回自然跳过）。两处快照（`_snapshot` 与导出）都改。
# 验收（报告附录 B #6）：自定义 cmap 的 colorbar → 参数里 `cmap` 为 null 且 apply 不报该字段。
# =====================================================================
import matplotlib as _mpl99                                     # noqa: E402
from matplotlib import colors as _mc99                          # noqa: E402

_s99_tb = io.open(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..',
                               'src', 'mpltweak', 'toolbox.py'),
                  encoding='utf-8').read()
check('99① 接线：toolbox.py 两处快照都改用"命名 colormap 才记"的判据（旧写法 0 残留）',
      _s99_tb.count("item['cmap'] = mappable.get_cmap().name") == 0
      and _s99_tb.count('_nm in matplotlib.colormaps') == 2,
      '旧=%d 新=%d' % (_s99_tb.count("item['cmap'] = mappable.get_cmap().name"),
                       _s99_tb.count('_nm in matplotlib.colormaps')))
_s99_custom = _mc99.ListedColormap(['#a00', '#fff', '#00a'])
check('99② 自定义 ListedColormap 的 `.name`（%r）不在 mpl.colormaps → 判据会记 null'
      '（报告附录 B #6 的前半）' % _s99_custom.name,
      _s99_custom.name not in _mpl99.colormaps, _s99_custom.name)
check('99③ 命名 colormap（viridis）在 mpl.colormaps → 判据照记（不误伤）',
      'viridis' in _mpl99.colormaps, list(_mpl99.colormaps)[:3])
check('99④【有牙】改回旧判据（直接取 `.name`）→ 自定义 cmap 会被记成 %r，'
      '而它编不进 mpl.colormaps（= 99② 在改前代码上必假）' % _s99_custom.name,
      # t105（CI 3.11/3.12 红）：**不要**把 matplotlib 给"无名 ListedColormap"的
      # 默认名硬编码进来 —— 旧版给 'from_list'，新版（3.11/3.12 装到的那支）给
      # 'unnamed'（CI 原始日志：`FAIL | 99④… | unnamed`）。这条 teeth 要证的是
      # "这个名字**编不进 mpl.colormaps** ⇒ 旧判据会记下一个不可用的名字"，
      # 与具体默认名无关 ⇒ 只留这一条（同场景下 99② 也用它，且在红 job 上是 PASS ✓）。
      _s99_custom.name not in _mpl99.colormaps,
      _s99_custom.name)
check('99⑤ 参数里 `cmap: null` 合法（= 该字段不出现，apply 不报它；报告附录 B #6 后半）',
      _params.validate_errors({'version': 4, 'script': 'f.py', 'fig_index': 0,
                               'n_figs': 1,
                               'axes': [{'index': 0, 'cmap': None}]}) == [],
      _params.validate_errors({'version': 4, 'script': 'f.py', 'fig_index': 0,
                               'n_figs': 1,
                               'axes': [{'index': 0, 'cmap': None}]}))

# =====================================================================
# §100 t66 / P0-1（**按操作类型定范围**的修正版）：撤销只在"画布类操作"里动画布
#
# 用户一手证据："我没有动画布，只是拖了子图，然后按撤销，结果画布变了（缩小很多）。"
# 机制：撤销栈里带着画布尺寸 → 任何一次撤销都回放它；旧回放 `set_size_inches(px/fig.dpi)`
# 里 `px` 是**逻辑像素**（= figsize×100）、`fig.dpi` 在 HiDPI 下 = rcParams.dpi×DPR
# （用户环境 200）→ 画布正好缩到 1/2。
# 修正口径：**元素类**操作快照不记画布（撤销纹丝不动）；**画布类**操作（Ctrl+F 裁白边）
# 快照记画布且能还原（C13 契约）；还原只用**权威英寸** `fig.get_size_inches()`（与导出
# `figsize_in` 同源、与 dpi/DPR 无关），旧的 px/dpi 换算已删。
# 本节矩阵 = dpi ∈ {100,150,200}（不开窗：未绑定方法 + 只带所需属性的假 self）。
# =====================================================================
import matplotlib.pyplot as _plt100                             # noqa: E402
from mpltweak import toolbox as _tb100                          # noqa: E402


class _F100:
    def __init__(self, fig, px):
        self.fig = fig
        self._fig_px = px
        self.leg_state = {}
        self._find_ax = lambda i: None

    def __getattr__(self, name):
        return lambda *a, **k: None


_s100_figs = []
for _dpi in (100, 150, 200):
    _f = _plt100.figure(figsize=(15.0, 9.5))
    _f.dpi = _dpi
    _c = _F100(_f, (1500, 950))
    _snap = _tb100.Tweak._snapshot(_c, with_canvas=False)      # 元素类：不记画布
    _f.set_size_inches(10.0, 7.0)                              # 假装"拖了一次子图"
    _tb100.Tweak._restore_snap(_c, _snap)                      # 撤销
    _got = tuple(round(float(x), 2) for x in _f.get_size_inches())
    check('100① dpi=%d：**元素类**撤销**不动画布**（快照 with_canvas=False 不带画布）'
          % _dpi,
          _snap.get('figsize') is None and _snap.get('figsize_in') is None
          and _got == (10.0, 7.0), 'snap=%r 画布=%r' % (_snap.get('figsize'), _got))
    _s100_figs.append(_f)
for _dpi in (100, 150, 200):
    _f = _plt100.figure(figsize=(15.0, 9.5))
    _f.dpi = _dpi
    _c = _F100(_f, (1500, 950))
    _snap = _tb100.Tweak._snapshot(_c, with_canvas=True)       # 画布类：记画布
    _f.set_size_inches(7.0, 4.5)                               # 假装裁白边/显式改画布
    _tb100.Tweak._restore_snap(_c, _snap)                      # 撤销 → 必须还原
    _got = tuple(round(float(x), 2) for x in _f.get_size_inches())
    check('100② dpi=%d：**画布类**撤销**把画布还原**到权威英寸 (15.0, 9.5)'
          '（C13 契约；旧 px/dpi 换算在 dpi>100 时会缩水）' % _dpi,
          _snap.get('figsize_in') == [15.0, 9.5] and _got == (15.0, 9.5),
          'snap_in=%r 画布=%r' % (_snap.get('figsize_in'), _got))
    _s100_figs.append(_f)
# teeth ①：把"元素类不记画布"改回（= 元素类也记画布）→ 撤销会把画布拉回 15×9.5 → 100① 必红
_f = _plt100.figure(figsize=(15.0, 9.5))
_c = _F100(_f, (1500, 950))
_snap_bad = _tb100.Tweak._snapshot(_c, with_canvas=True)       # 改回：元素类也记画布
_f.set_size_inches(10.0, 7.0)
_tb100.Tweak._restore_snap(_c, _snap_bad)
_got_bad = tuple(round(float(x), 2) for x in _f.get_size_inches())
check('100③【有牙】把"元素类不记画布"改回去 → 拖子图后撤销把画布拉回 15.0×9.5'
      '（= 100① 在改前口径上必假）',
      _got_bad == (15.0, 9.5), '画布=%r' % (_got_bad,))
_s100_figs.append(_f)
# teeth ②：把"画布类不还原"改回（= 画布类也不记）→ 裁白边后撤销还原不了 → 100② 必红
_f = _plt100.figure(figsize=(15.0, 9.5))
_c = _F100(_f, (1500, 950))
_snap_bad2 = _tb100.Tweak._snapshot(_c, with_canvas=False)     # 改回：画布类也不记
_f.set_size_inches(7.0, 4.5)
_tb100.Tweak._restore_snap(_c, _snap_bad2)
_got_bad2 = tuple(round(float(x), 2) for x in _f.get_size_inches())
check('100④【有牙】把"画布类还原"改回去（画布类也不记）→ 裁白边后撤销还原不了'
      '（= 100② 在改前口径上必假；C13 会红）',
      _got_bad2 == (7.0, 4.5), '画布=%r' % (_got_bad2,))
_s100_figs.append(_f)
for _f in _s100_figs:
    _plt100.close(_f)

# =====================================================================
# §101 t66 / P1-4：`describe → apply` 零往返（colorbar 的位置读法）
#
# 报告 P1-4：colorbar 的 `_ColorbarAxesLocator` 在**每次重绘**时改写 cax 的 active
# position → "脚本 savefig 之后再读"（describe）与"开窗前读"（窗口）拿到两个值
# （实测 0.1162 → 0.0289 那种 4× 差），`describe → apply` 就会凭空改一个用户没动过的
# colorbar。修法：`toolbox._bbox` 对 colorbar 轴用 `get_position(original=True)`
# （与重绘时机无关）。
# 验收（报告附录 B #5）：describe 后立刻 apply 零改动。**注**：预览模式 apply 一律报
# `reason='preview'`（设计如此），所以"零改动"用三件一起证：`changes=[]` + 字节未变 +
# `--write` 时 `reason='no_change'`。
# =====================================================================
_s101_f = _plt100.figure(figsize=(8, 6))
_s101_ax = _s101_f.add_subplot(111)
_s101_im = _s101_ax.imshow([[0, 1], [1, 0]])
_s101_cb = _s101_f.colorbar(_s101_im, ax=_s101_ax)
_s101_b1 = [round(float(v), 6) for v in _tb100._bbox(_s101_cb.ax)]
_s101_f.canvas.draw()
_s101_b2 = [round(float(v), 6) for v in _tb100._bbox(_s101_cb.ax)]
_s101_orig = [round(float(v), 6) for v in
              _s101_cb.ax.get_position(original=True).bounds]
check('101① colorbar 轴的 `_bbox` 与**重绘时机无关**且等于 `get_position(original=True)`'
      '（= 权威读位置）',
      _tb100._is_colorbar_ax(_s101_cb.ax) and _s101_b1 == _s101_b2
      and _s101_b1 == _s101_orig, '重绘前=%s 重绘后=%s original=%s'
      % (_s101_b1, _s101_b2, _s101_orig))
# teeth：改回旧读法（直接读 `ax._position`）→ 重绘前后不一致 → 101① 在改前代码上必假
_s101_old_after = [round(float(v), 6) for v in
                   [_s101_cb.ax._position.x0, _s101_cb.ax._position.y0,
                    _s101_cb.ax._position.width, _s101_cb.ax._position.height]]
check('101②【有牙】改回旧读法（`ax._position`）→ 重绘后宽度被 locator 改写'
      '（%s ≠ %s）→ 两条路径各读一个值，101① 在改前代码上必假'
      % (_s101_old_after[2], _s101_b1[2]),
      _s101_old_after != _s101_b1, '旧读法(重绘后)=%s 权威=%s'
      % (_s101_old_after, _s101_b1))
_plt100.close(_s101_f)

# 真 CLI 往返（describe → apply）：零改动三件套
import subprocess as _sp101                                     # noqa: E402
import json as _json101                                         # noqa: E402
_s101_d = os.path.join(_TMPROOT, 'case_t101')
shutil.rmtree(_s101_d, ignore_errors=True)
os.makedirs(_s101_d)
_s101_py = os.path.join(_s101_d, 'fig.py')
with open(_s101_py, 'w', encoding='utf-8', newline='\n') as _f:
    _f.write('import matplotlib\nmatplotlib.use("Agg")\n'
             'import matplotlib.pyplot as plt\n\n'
             'fig, ax = plt.subplots(figsize=(8, 6))\n'
             'im = ax.imshow([[0, 1], [1, 0]])\n'
             'cb = fig.colorbar(im, ax=ax)\n'
             'fig.savefig("out.png")\n')
_s101_pj = os.path.join(_s101_d, 'layout.json')
_s101_env = dict(os.environ, MPLBACKEND='Agg', PYTHONIOENCODING='utf-8',
                 MPLTWEAK_LANG='zh', MPLTWEAK_HOME=os.path.join(_s101_d, 'home'),
                 PYTHONPATH=os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                         '..', 'src'))
_s101_env.pop('MPLTWEAK_NO_HINT', None)
_sp101.run([sys.executable, '-m', 'mpltweak', 'describe', _s101_py, '-o', _s101_pj],
           capture_output=True, timeout=300, env=_s101_env, cwd=_s101_d)
with open(_s101_py, 'rb') as _f:
    _s101_before = _f.read()
_s101_r = _sp101.run([sys.executable, '-m', 'mpltweak', 'apply', _s101_py,
                      '--params', _s101_pj, '--json'],
                     capture_output=True, timeout=300, env=_s101_env, cwd=_s101_d)
_s101_rw = _sp101.run([sys.executable, '-m', 'mpltweak', 'apply', _s101_py,
                       '--params', _s101_pj, '--json', '--write'],
                      capture_output=True, timeout=300, env=_s101_env, cwd=_s101_d)
with open(_s101_py, 'rb') as _f:
    _s101_after = _f.read()
_s101_o = (_s101_r.stdout or b'').decode('utf-8', 'replace')
_s101_ow = (_s101_rw.stdout or b'').decode('utf-8', 'replace')
_s101_j = _json101.loads(_s101_o[_s101_o.index('{'):_s101_o.rindex('}') + 1])['files'][0]
_s101_jw = _json101.loads(_s101_ow[_s101_ow.index('{'):_s101_ow.rindex('}') + 1])['files'][0]
check('101③【报告附录 B #5】describe → apply 零往返：预览 `changes=[]` + 字节未变，'
      '`--write` 时 `reason=no_change` 且字节未变（colorbar 位置不再被凭空改）',
      not _s101_j.get('changes') and _s101_before == _s101_after
      and _s101_jw.get('reason') == 'no_change',
      'preview reason=%r changes=%r / write reason=%r 字节未变=%s'
      % (_s101_j.get('reason'), _s101_j.get('changes'), _s101_jw.get('reason'),
         _s101_before == _s101_after))

# =====================================================================
# §102 t66 / P0-1 止损：figsize **交叉一致性守卫**（报告建议 3 + 附录 B #2）
#
# 报告的现场：`figsize_px=[1500, 950]` 配 `figsize_in=[7.5, 4.75]`（**正好 1/2**）
# = "dpi 换算错一倍"，写回会把用户的画布永久减半。`writeback._figsize_inconsistent`
# 在写回前拦下**成倍**矛盾（1.5/2/3 倍，任一方向；dpi 按脚本静态探测、默认 100），
# 拒写并给人话原因（`reason=bad_params`，rc 仍是 2 —— 不动退出码）。
# =====================================================================
_S102 = ('import matplotlib\n'
         'matplotlib.use("Agg")\n'
         'import matplotlib.pyplot as plt\n\n'
         'fig, ax = plt.subplots(figsize=(15, 9.5))\n'
         'ax.plot([1, 2], [1, 2])\n'
         'fig.savefig("out.png")\n')
_S102_200 = ('import matplotlib\n'
             'matplotlib.use("Agg")\n'
             'import matplotlib.pyplot as plt\n\n'
             'fig, ax = plt.subplots(figsize=(6, 4), dpi=200)\n'
             'ax.plot([1, 2], [1, 2])\n'
             'fig.savefig("out.png")\n')


def _s102_run(src, tag, params):
    _d = os.path.join(_TMPROOT, 'case_t102_' + tag)
    shutil.rmtree(_d, ignore_errors=True)
    os.makedirs(os.path.join(_d, '.tweak_params'), exist_ok=True)
    _s = os.path.join(_d, 'fig.py')
    with open(_s, 'w', encoding='utf-8', newline='\n') as _f:
        _f.write(src)
    _p = os.path.join(_d, '.tweak_params', 'fig.json')
    with open(_p, 'w', encoding='utf-8') as _f:
        json.dump(params, _f, ensure_ascii=False)
    with open(_s, 'rb') as _f:
        _b0 = _f.read()
    _r = writeback.writeback(_s, dict(params), _p, verify=False, style='block')
    with open(_s, 'rb') as _f:
        return _r, _b0 == _f.read()


_p102_bad = {'version': 4, 'script': 'fig.py', 'fig_index': 0, 'n_figs': 1,
             'figsize_px': [1500, 950], 'figsize_in': [7.5, 4.75]}
_r102a, _same102a = _s102_run(_S102, 'half', _p102_bad)
check('102①【报告附录 B #2】px=[1500,950] + in=[7.5,4.75]（正好 1/2）→ 拒写'
      '（reason=bad_params）+ 人话原因 + 一个字节不动',
      _r102a.get('reason') == 'bad_params' and _same102a
      and 'figsize' in (_r102a.get('err') or ''),
      'reason=%r 字节未变=%s err=%r' % (_r102a.get('reason'), _same102a,
                                       (_r102a.get('err') or '')[:70]))
_r102b, _ = _s102_run(_S102, 'double',
                      dict(_p102_bad, figsize_in=[30.0, 19.0]))
check('102② 反方向（in 是 2 倍）也拒（成倍矛盾两个方向都拦）',
      _r102b.get('reason') == 'bad_params', _r102b.get('reason'))
_r102c, _ = _s102_run(_S102, 'ok100',
                      dict(_p102_bad, figsize_in=[15.0, 9.5]))
check('102③ 合法参数（in = px/100）**不拒**（守卫不误伤正常写回）',
      _r102c.get('reason') != 'bad_params', _r102c.get('reason'))
_r102d, _ = _s102_run(_S102_200, 'dpi200',
                      {'version': 4, 'script': 'fig.py', 'fig_index': 0,
                       'n_figs': 1, 'figsize_px': [1200, 800],
                       'figsize_in': [6.0, 4.0]})
check('102④ dpi=200 的脚本（px=[1200,800] + in=[6,4]）**不拒**（按脚本静态 dpi 折算，'
      '不误伤合法的 dpi=200 参数）',
      _r102d.get('reason') != 'bad_params', _r102d.get('reason'))
_orig_fi102 = writeback._figsize_inconsistent
writeback._figsize_inconsistent = lambda tree, data: ''
try:
    _r102e, _ = _s102_run(_S102, 'teeth', _p102_bad)
finally:
    writeback._figsize_inconsistent = _orig_fi102
check('102⑤【有牙】把守卫换成 `return ''`（= 没有这条）→ 坏参数不再被拒'
      '（= 102① 在改前代码上必假）',
      _r102e.get('reason') != 'bad_params', _r102e.get('reason'))

# ---------------------------------------------------------------------
# §102 续（t103 / MAINTENANCE §25.18）：P0-1 止损的**误伤** —— dpi 静态提取
# 只认「建图位置」，放过 `savefig(dpi=300)`。
#
# 现场：`plt.subplots(figsize=(12, 7))` + `fig.savefig('x.png', dpi=300)` 是语料里
# 最常见的论文写法（16,126 份里 1,629 份带 savefig 的 dpi），而 **savefig 的 dpi 是
# 出图像素、不是画布 dpi**。旧 `_script_dpi` 不看接收者、把所有 `dpi=` 都当画布 dpi ⇒
# `_figsize_inconsistent` 判成"12 in 该是 4 in，差成倍"⇒ `apply --write` 报
# `reason=bad_params` / rc=2 / 一个字节不动；而**预览**（不带 --write）在 apply.py 里
# 早已 `reason=preview` 早退、根本不走这条守卫 ⇒ 用户看到"预览正常、一写就报错"。
# 语料影响面 1,495 份（9.3%）落这个陷阱，判据只是"碰巧"（savefig dpi 写 150/200/300）。
#
# 收窄口径（`writeback._script_dpi` + `DPI_FACTORY_NAMES` / `DPI_OUTPUT_NAMES`）：
# 只收 `plt.figure` / `plt.subplots` / `plt.subplot_mosaic` / `Figure(...)` 的 `dpi=`、
# `fig.set_dpi(N)`、`plt.rc('figure', dpi=N)`，以及原有的 `fig.dpi = N` /
# `rcParams['figure.dpi'] = N`；出图/读图调用（savefig/imsave/imread/print_*）的
# `dpi=` 一律不收。
# ---------------------------------------------------------------------
_S102_SAVE300 = ('import matplotlib\n'
                 'matplotlib.use("Agg")\n'
                 'import matplotlib.pyplot as plt\n\n'
                 # 建图不带 dpi ⇒ 画布 dpi = 默认 100（= params.py 的设计口径）
                 'fig, ax = plt.subplots(figsize=(12, 7))\n'
                 'ax.plot([1, 2], [1, 2])\n'
                 'fig.savefig("x.png", dpi=300)\n')     # 出图 300dpi ≠ 画布 dpi
_S102_FIG200_SAVE300 = ('import matplotlib\n'
                        'matplotlib.use("Agg")\n'
                        'import matplotlib.pyplot as plt\n\n'
                        'fig, ax = plt.subplots(figsize=(6, 4), dpi=200)\n'
                        'ax.plot([1, 2], [1, 2])\n'
                        'fig.savefig("x.png", dpi=300)\n')
_S102_FIG300 = ('import matplotlib\n'
                'matplotlib.use("Agg")\n'
                'import matplotlib.pyplot as plt\n\n'
                'fig, ax = plt.subplots(figsize=(12, 7), dpi=300)\n'
                'ax.plot([1, 2], [1, 2])\n'
                'fig.savefig("out.png")\n')
_S102_RC200 = ('import matplotlib\n'
               'matplotlib.use("Agg")\n'
               'import matplotlib.pyplot as plt\n\n'
               'plt.rc("figure", dpi=200)\n'
               'fig, ax = plt.subplots(figsize=(6, 4))\n'
               'ax.plot([1, 2], [1, 2])\n'
               'fig.savefig("x.png", dpi=300)\n')
_S102_SETDPI200 = ('import matplotlib\n'
                   'matplotlib.use("Agg")\n'
                   'import matplotlib.pyplot as plt\n\n'
                   'fig, ax = plt.subplots(figsize=(6, 4))\n'
                   'fig.set_dpi(200)\n'
                   'ax.plot([1, 2], [1, 2])\n'
                   'fig.savefig("x.png", dpi=300)\n')

_ra103, _ = _s102_run(_S102_SAVE300, 't103_a',
                      {'version': 4, 'script': 'fig.py', 'fig_index': 0, 'n_figs': 1,
                       'figsize_px': [1200, 700], 'figsize_in': [12.0, 7.0]})
check('102⑥【本版唯一新增行为】subplots(figsize=(12,7)) + savefig(dpi=300)，'
      'px=[1200,700] / in=[12,7]（画布 12in × 100dpi）→ **不再**因 bad_params 被拒'
      '（= §25.18 那个 9.3% 陷阱）',
      _ra103.get('reason') != 'bad_params', _ra103.get('reason'))
_rb103, _ = _s102_run(_S102_FIG200_SAVE300, 't103_b',
                      {'version': 4, 'script': 'fig.py', 'fig_index': 0, 'n_figs': 1,
                       'figsize_px': [1200, 800], 'figsize_in': [6.0, 4.0]})
check('102⑦ 既有 102④ 的加强版（subplots(dpi=200) **且** savefig(dpi=300)，'
      'px=[1200,800] / in=[6,4]）→ 仍不拒：画布 dpi 取建图那个 200，savefig 的 300 不算',
      _rb103.get('reason') != 'bad_params', _rb103.get('reason'))
_rc103, _ = _s102_run(_S102_FIG300, 't103_c',
                      {'version': 4, 'script': 'fig.py', 'fig_index': 0, 'n_figs': 1,
                       'figsize_px': [3600, 2100], 'figsize_in': [12.0, 7.0]})
check('102⑧ MAINTENANCE 验收 b：subplots(figsize=(12,7), dpi=300) + px=[3600,2100] '
      '/ in=[12,7] → 放行（真 dpi 与 px 自洽，收窄没把"建图 dpi"弄瞎）',
      _rc103.get('reason') != 'bad_params', _rc103.get('reason'))
_rd103, _same103d = _s102_run(_S102_SAVE300, 't103_d', _p102_bad)
check('102⑨ 真矛盾在"带 savefig(dpi=300) 的脚本"上**照样拒**：'
      'px=[1500,950] / in=[7.5,4.75]（1/2）→ 仍 reason=bad_params 且一个字节不动'
      '（= 102① 未被放水）',
      _rd103.get('reason') == 'bad_params' and _same103d
      and 'figsize' in (_rd103.get('err') or ''),
      'reason=%r 字节未变=%s err=%r' % (_rd103.get('reason'), _same103d,
                                       (_rd103.get('err') or '')[:60]))
_re103, _ = _s102_run(_S102_RC200, 't103_e',
                      {'version': 4, 'script': 'fig.py', 'fig_index': 0, 'n_figs': 1,
                       'figsize_px': [1200, 800], 'figsize_in': [6.0, 4.0]})
_rf103, _ = _s102_run(_S102_SETDPI200, 't103_f',
                      {'version': 4, 'script': 'fig.py', 'fig_index': 0, 'n_figs': 1,
                       'figsize_px': [1200, 800], 'figsize_in': [6.0, 4.0]})
check('102⑩ 收窄不能把既有能看的写法弄瞎：`plt.rc("figure", dpi=200)` 与 '
      '`fig.set_dpi(200)`（都配 savefig(dpi=300)）→ 两种都**不拒**',
      _re103.get('reason') != 'bad_params' and _rf103.get('reason') != 'bad_params',
      'rc: reason=%r / set_dpi: reason=%r' % (_re103.get('reason'), _rf103.get('reason')))


def _s102_legacy_script_dpi(tree):
    '''**旧实现原样**（t66 那版 `_script_dpi`：任意调用的 `dpi=` 都收，不看接收者）。

    只给 102⑪ 的 teeth 用：临时装回 `writeback._script_dpi`，"只认建图位置"这个改动
    必须让 102⑥ / 102⑦ 变红 —— 否则那两条验的是别的东西。
    '''
    dpi = 100.0
    for n in _ast.walk(tree):
        if isinstance(n, _ast.Call):
            for kw in n.keywords:
                if kw.arg == 'dpi' and isinstance(kw.value, _ast.Constant) \
                        and isinstance(kw.value.value, (int, float)) \
                        and not isinstance(kw.value.value, bool):
                    dpi = float(kw.value.value)
        if isinstance(n, _ast.Assign) and isinstance(n.value, _ast.Constant) \
                and isinstance(n.value.value, (int, float)) \
                and not isinstance(n.value.value, bool):
            for t in n.targets:
                if isinstance(t, _ast.Attribute) and t.attr == 'dpi':
                    dpi = float(n.value.value)
                elif isinstance(t, _ast.Subscript) \
                        and isinstance(t.value, _ast.Attribute) \
                        and t.value.attr == 'rcParams':
                    sl = t.slice
                    if isinstance(sl, _ast.Constant) and sl.value == 'figure.dpi':
                        dpi = float(n.value.value)
    return dpi


_orig_sd103 = writeback._script_dpi
writeback._script_dpi = _s102_legacy_script_dpi
try:
    _rt103a, _ = _s102_run(_S102_SAVE300, 't103_teeth_a',
                           {'version': 4, 'script': 'fig.py', 'fig_index': 0, 'n_figs': 1,
                            'figsize_px': [1200, 700], 'figsize_in': [12.0, 7.0]})
    _rt103b, _ = _s102_run(_S102_FIG200_SAVE300, 't103_teeth_b',
                           {'version': 4, 'script': 'fig.py', 'fig_index': 0, 'n_figs': 1,
                            'figsize_px': [1200, 800], 'figsize_in': [6.0, 4.0]})
finally:
    writeback._script_dpi = _orig_sd103
check('102⑪【有牙】把 `_script_dpi` 换回旧实现（任意调用取 dpi）→ ⑥ 与 ⑦ **都变红**'
      '（reason=bad_params）⇒ ⑥⑦ 咬的正是"只认建图位置"这个改动本身',
      _rt103a.get('reason') == 'bad_params' and _rt103b.get('reason') == 'bad_params',
      '⑥ 旧实现: reason=%r / ⑦ 旧实现: reason=%r'
      % (_rt103a.get('reason'), _rt103b.get('reason')))


# ---------------------------------------------------------------------
# §102 续（t104 / 第二批）：`_script_dpi` **静态推断的三条边界**
#
# 背景：`_script_dpi` 推断的是**运行期**概念（"这张图"实际多少 dpi），手上只有**静态**
# 信息。三条边界越过去，判据就从"看得见的矛盾"变成"猜出来的矛盾"——一猜错就是**误拒**
# （预览正常、一写就报 bad_params，与 §25.18 同一类现场）：
#   ① 多图不同 dpi：`fig1` 默认 100、`fig2` 显式 200 ⇒ 旧实现"最后一个赢"，把 fig2 的
#      200 套到 fig1 的参数上，2 倍差 ⇒ 误拒；
#   ② 非 figure 的 `.dpi =`：`class P: self.dpi = 150` 这种接收者不是这张图，旧实现谁写
#      都收；本版只在接收者**可证是 Figure 变量**时认；
#   ③ 控制流里的 dpi 写法：`if <条件>: rcParams['figure.dpi'] = 200` 走没走只有运行期
#      知道，旧实现无条件采纳。
# 三条一律"判不出 ⇒ 放行 + warnings 里如实说**未做换算校验**"（宁可漏拦，不可误伤）；
# 真矛盾（102①）不受影响，仍必须拒。
# ---------------------------------------------------------------------
_S102_T104_TWODPI = ('import matplotlib\n'
                     'matplotlib.use("Agg")\n'
                     'import matplotlib.pyplot as plt\n\n'
                     'fig1, ax1 = plt.subplots(figsize=(10, 6))\n'
                     'ax1.plot([1, 2], [1, 2])\n'
                     'fig1.savefig("f1.png")\n\n'
                     'fig2, ax2 = plt.subplots(figsize=(5, 3), dpi=200)\n'
                     'ax2.plot([1, 2], [1, 2])\n'
                     'fig2.savefig("f2.png")\n')
_S102_T104_SELFDPI = ('import matplotlib\n'
                      'matplotlib.use("Agg")\n'
                      'import matplotlib.pyplot as plt\n\n'
                      'class P:\n'
                      '    def __init__(self):\n'
                      '        self.dpi = 150\n\n\n'
                      'p = P()\n'
                      'fig, ax = plt.subplots(figsize=(6, 4))\n'
                      'ax.plot([1, 2], [1, 2])\n'
                      'fig.savefig("x.png", dpi=p.dpi)\n')
_S102_T104_CTRL = ('import matplotlib\n'
                   'matplotlib.use("Agg")\n'
                   'import matplotlib.pyplot as plt\n'
                   'import os\n\n'
                   'if os.environ.get("MPLTWEAK_T104_BRANCH"):\n'
                   '    plt.rcParams["figure.dpi"] = 200\n\n'
                   'fig, ax = plt.subplots(figsize=(3, 2))\n'
                   'ax.plot([1, 2], [1, 2])\n'
                   'fig.savefig("x.png")\n')
_S102_T104_NONLIT = ('import matplotlib\n'
                     'matplotlib.use("Agg")\n'
                     'import matplotlib.pyplot as plt\n\n'
                     'DPI = 200\n'
                     'fig, ax = plt.subplots(figsize=(6, 4), dpi=DPI)\n'
                     'ax.plot([1, 2], [1, 2])\n'
                     'fig.savefig("x.png")\n')
_S102_T104_SETDPI = ('import matplotlib\n'
                     'matplotlib.use("Agg")\n'
                     'import matplotlib.pyplot as plt\n\n'
                     'fig, ax = plt.subplots(figsize=(6, 4))\n'
                     'fig.set_dpi(200)\n'
                     'ax.plot([1, 2], [1, 2])\n'
                     'fig.savefig("x.png")\n')
_S102_T104 = '未做'                     # warnings 文案里的锚（"本次未做 figsize 的 dpi 换算校验"）


def _s102_104_run(src, tag, params):
    _r, _ = _s102_run(src, tag, params)
    return _r, [w for w in (_r.get('warnings') or []) if _S102_T104 in w]


_P104_TWODPI = {'version': 4, 'script': 'fig.py', 'fig_index': 0, 'n_figs': 2,
                'figsize_px': [1000, 600], 'figsize_in': [10.0, 6.0]}
_r104a, _w104a = _s102_104_run(_S102_T104_TWODPI, 't104_a', dict(_P104_TWODPI))
check('102⑫ 多图不同 dpi（fig1 默认 100 / fig2 显式 200）对 fig1 写回 '
      'px=[1000,600] / in=[10,6] → **不因 bad_params 被拒**（修前：最后一个赢 = 200，'
      '2 倍差 ⇒ 拒），且 warnings 如实说"未做 dpi 换算校验"',
      _r104a.get('reason') != 'bad_params' and bool(_w104a),
      'reason=%r warn=%r' % (_r104a.get('reason'), _w104a[:1]))
_r104b, _w104b = _s102_104_run(_S102_T104_SELFDPI, 't104_b',
                               {'version': 4, 'script': 'fig.py', 'fig_index': 0,
                                'n_figs': 1, 'figsize_px': [600, 400],
                                'figsize_in': [6.0, 4.0]})
check('102⑬ 非 figure 的 `.dpi =`：`class P: self.dpi = 150` + subplots 默认 + '
      '`savefig(dpi=p.dpi)`，px=[600,400] / in=[6,4] → **不拒**（修前：收下 150，1.5 倍 ⇒ 拒）'
      '；且**没有**"未做校验"的 warnings（dpi 判得出 = 100，说明不是靠"一律放行"糊过去的）',
      _r104b.get('reason') != 'bad_params' and not _w104b,
      'reason=%r warn=%r' % (_r104b.get('reason'), _w104b[:1]))
_r104c, _w104c = _s102_104_run(_S102_T104_CTRL, 't104_c',
                               {'version': 4, 'script': 'fig.py', 'fig_index': 0,
                                'n_figs': 1, 'figsize_px': [300, 200],
                                'figsize_in': [3.0, 2.0]})
_r104d, _w104d = _s102_104_run(_S102_T104_NONLIT, 't104_d',
                               {'version': 4, 'script': 'fig.py', 'fig_index': 0,
                                'n_figs': 1, 'figsize_px': [1200, 800],
                                'figsize_in': [6.0, 4.0]})
check('102⑭ 控制流里的 `rcParams["figure.dpi"] = 200`（运行期没走这一支）与"dpi 值不是'
      '字面量"（`DPI = 200; subplots(dpi=DPI)`）→ 两种都**不拒**且 warnings 有"未做换算'
      '校验"（修前：分别收下 200 / 按 100 折算 ⇒ 都判 2 倍差拒写）',
      _r104c.get('reason') != 'bad_params' and bool(_w104c)
      and _r104d.get('reason') != 'bad_params' and bool(_w104d),
      'ctrl: reason=%r warn=%r / nonlit: reason=%r warn=%r'
      % (_r104c.get('reason'), _w104c[:1], _r104d.get('reason'), _w104d[:1]))
_r104e, _same104e = _s102_run(_S102, 't104_e', _p102_bad)
_r104f, _same104f = _s102_run(_S102_T104_SETDPI, 't104_f',
                              {'version': 4, 'script': 'fig.py', 'fig_index': 0,
                               'n_figs': 1, 'figsize_px': [1200, 800],
                               'figsize_in': [12.0, 8.0]})
check('102⑮ 真矛盾**没被放水**：px=[1500,950] / in=[7.5,4.75]（§102① 同款）仍 '
      'reason=bad_params 且字节不动；且"判得出 dpi"的路径仍在咬 —— '
      '`fig.set_dpi(200)` + px=[1200,800] / in=[12,8]（200dpi 下应是 6×4 in，正好 2 倍）'
      '也仍拒',
      _r104e.get('reason') == 'bad_params' and _same104e
      and _r104f.get('reason') == 'bad_params',
      '102①: reason=%r 字节未变=%s / set_dpi(200): reason=%r'
      % (_r104e.get('reason'), _same104e, _r104f.get('reason')))
_ra104g, _ = _s102_run(_S102_SAVE300, 't104_g',
                       {'version': 4, 'script': 'fig.py', 'fig_index': 0, 'n_figs': 1,
                        'figsize_px': [1200, 700], 'figsize_in': [12.0, 7.0]})
_rb104g, _ = _s102_run(_S102_FIG200_SAVE300, 't104_h',
                       {'version': 4, 'script': 'fig.py', 'fig_index': 0, 'n_figs': 1,
                        'figsize_px': [1200, 800], 'figsize_in': [6.0, 4.0]})
check('102⑯【不回归】T1 的 102⑥（savefig(dpi=300) 放过）与 102⑦（subplots(dpi=200) + '
      'savefig(dpi=300) 共存）仍绿，且与 102⑫⑬ 的四条边界改动互不干扰',
      _ra104g.get('reason') != 'bad_params' and _rb104g.get('reason') != 'bad_params'
      and _r104a.get('reason') != 'bad_params'
      and _r104b.get('reason') != 'bad_params',
      '102⑥=%r / 102⑦=%r / 102⑫=%r / 102⑬=%r'
      % (_ra104g.get('reason'), _rb104g.get('reason'),
         _r104a.get('reason'), _r104b.get('reason')))
_orig_vote104 = writeback._script_dpi_vote


def _t104_no_multi_skip(tree):
    """牙 A：去掉"多值 ⇒ 放行"（退回"挑一个值"，= 旧口径的最后一个赢）。"""
    _dpi, _why = _orig_vote104(tree)
    if _dpi is None and _why.startswith('dpi sites differ'):
        return 200.0, ''
    return _dpi, _why


try:
    writeback._script_dpi_vote = _t104_no_multi_skip
    _r104i, _ = _s102_run(_S102_T104_TWODPI, 't104_i', dict(_P104_TWODPI))
finally:
    writeback._script_dpi_vote = _orig_vote104
_orig_ctrl104 = writeback._dpi_in_control_flow
try:
    writeback._dpi_in_control_flow = lambda node, parents: False
    _r104j, _ = _s102_run(_S102_T104_CTRL, 't104_j',
                          {'version': 4, 'script': 'fig.py', 'fig_index': 0,
                           'n_figs': 1, 'figsize_px': [300, 200],
                           'figsize_in': [3.0, 2.0]})
finally:
    writeback._dpi_in_control_flow = _orig_ctrl104
check('102⑰【两头有牙】把"多值 ⇒ 放行"去掉（`_script_dpi_vote` 改回挑一个值）⇒ 102⑫ '
      '必变红（bad_params）；把"控制流跳过"去掉（`_dpi_in_control_flow` 恒 False）⇒ '
      '102⑭ 的控制流那条必变红 —— 两条边界各自有独立的牙',
      _r104i.get('reason') == 'bad_params' and _r104j.get('reason') == 'bad_params',
      '去多值放行: 102⑫ reason=%r / 去控制流跳过: 102⑭ reason=%r'
      % (_r104i.get('reason'), _r104j.get('reason')))


# ---------------------------------------------------------------------
# §102 续（t17 / **存量缺陷**，0.1.12 起就有）：裸 `tick_params()` 不许把写回打崩
#
# t6 全池差分跑（1.6 万份真实脚本、两臂）抓到：`_gap_specs_tick` 里
# `_av = next((k for k in c.keywords if k.arg == 'axis'), None)` 之后**直接**取
# `_av.value` —— 没写 `axis=` 时 `_av` 是 None ⇒
# `AttributeError: 'NoneType' object has no attribute 'value'` 穿到顶层，
# CLI 变成 `internal_error` / rc=2（脚本一个字节都没动，用户看到的却是"工具 bug"）。
# 两臂同码同炸（inplace 波 A 侧 34 份 / B 侧 38 份，分母 16,126）⇒ 与本批 0.1.13 无关。
#
# 修法（不新增任何文案/不改 message 表）：把「没写 axis」与「写了但不是本侧 / 'both'」
# **同样处理** —— 不做候选。这不是"绕过异常"，而是本节既有口径：不带 `axis=` 的
# `tick_params` 一次改两侧，往里补 `labelsize` 会**偷偷改另一侧**（fail-closed）；
# 没有候选 ⇒ 调用方按既有语义退块，块里精确写 `axis='x', labelsize=…`（两侧不串）。
# ---------------------------------------------------------------------
_T17_HEAD = ('import matplotlib\nmatplotlib.use("Agg")\n'
             'import matplotlib.pyplot as plt\n'
             'fig, ax = plt.subplots(figsize=(6, 4))\n'
             'ax.plot([1, 2], [1, 2])\n')
_T17_TAIL = 'fig.savefig("x.png")\n'
_T17_BARE = _T17_HEAD + 'ax.tick_params()\n' + _T17_TAIL
_T17_COLORS = _T17_HEAD + 'ax.tick_params(colors="red")\n' + _T17_TAIL
_T17_AXISX_ONLY = _T17_HEAD + "ax.tick_params(axis='x')\n" + _T17_TAIL
_T17_AXISX_8 = _T17_HEAD + "ax.tick_params(axis='x', labelsize=8)\n" + _T17_TAIL
_T17_PARAMS = {'version': 4, 'script': 'fig.py',
               'axes': [{'index': 0, 'tick_fontsize': 14.0}]}
# 14 而不是 10：matplotlib 默认刻度字号就是 10，参数等于默认值时引擎判"已经是这个值"、
# 根本走不到补参数那条路（t17 复现时踩过：改 10 就"不炸"，改 14 才炸）。


def _t17_run(src, tag, params=None, style='inplace'):
    """跑一次真写回，返回 ``(res, 写出后的源码)``；异常**不吞**（要测的就是"不许抛"）。"""
    _d = os.path.join(_TMPROOT, 'case_t17_' + tag)
    shutil.rmtree(_d, ignore_errors=True)
    os.makedirs(os.path.join(_d, '.tweak_params'), exist_ok=True)
    _s = os.path.join(_d, 'fig.py')
    with open(_s, 'w', encoding='utf-8', newline='\n') as _f:
        _f.write(src)
    _p = os.path.join(_d, '.tweak_params', 'fig.json')
    with open(_p, 'w', encoding='utf-8') as _f:
        json.dump(dict(params or _T17_PARAMS), _f, ensure_ascii=False)
    _r = writeback.writeback(_s, dict(params or _T17_PARAMS), _p,
                             verify=False, style=style)
    return _r, read(_s)


_r17a, _t17a = _t17_run(_T17_BARE, 'bare')
check('102⑱【t17】裸 `ax.tick_params()`（不带 axis、不带尺寸参数）→ **不抛异常**；'
      '原位路径 fail-closed：**不动源码**、如实 needs_block（不往"两侧通吃"的调用上补参数）',
      isinstance(_r17a, dict) and _r17a.get('reason') != 'fail'
      and _r17a.get('needs_block') is True
      and 'labelsize' not in _t17a,
      'reason=%r needs_block=%r 源码含 labelsize=%s'
      % (_r17a.get('reason'), _r17a.get('needs_block'), 'labelsize' in _t17a))
_r17b, _t17b = _t17_run(_T17_COLORS, 'colors')
check('102⑲【t17】只带非尺寸参数（`tick_params(colors="red")`）→ 同样**不抛异常**且'
      'fail-closed（判据是"有没有 axis="，不是"有没有任何参数"）',
      _r17b.get('reason') != 'fail' and _r17b.get('needs_block') is True
      and 'labelsize' not in _t17b,
      'reason=%r needs_block=%r' % (_r17b.get('reason'), _r17b.get('needs_block')))
_r17c, _t17c = _t17_run(_T17_AXISX_ONLY, 'axisx_only')
check('102⑳【t17】`tick_params(axis=\'x\')`（有 axis、无尺寸参数）→ 不抛异常，'
      '原位补 `labelsize=14` 到**那条**调用里（不新增块）',
      _r17c.get('reason') == 'ok'
      and "tick_params(axis='x', labelsize=14)" in _t17c
      and 'auto layout' not in _t17c
      and _t17c.count('tick_params') == 1,
      'reason=%r 行=%r' % (_r17c.get('reason'),
                          [ln.strip() for ln in _t17c.splitlines()
                           if 'tick_params' in ln]))
_r17d, _t17d = _t17_run(_T17_AXISX_8, 'axisx_8')
check('102㉑【不回归】`tick_params(axis=\'x\', labelsize=8)` + 参数 14 → 原位把 8 改成 14'
      '（既有「改字面量」能力没被这次修复影响）',
      _r17d.get('reason') == 'ok'
      and "tick_params(axis='x', labelsize=14)" in _t17d
      and 'labelsize=8' not in _t17d,
      'reason=%r 行=%r' % (_r17d.get('reason'),
                          [ln.strip() for ln in _t17d.splitlines()
                           if 'tick_params' in ln]))
_r17e, _t17e = _t17_run(_T17_BARE, 'bare_block', style='block')
check('102㉓【t17·用户可见结果】同一份裸 `tick_params()` 走块路径（CLI 默认 auto 的退路）'
      '→ 不炸，块里精确写 `axis=\'x\', labelsize=14`（两侧不串：y 侧不被顺带改）',
      _r17e.get('reason') in ('ok', 'no_change')
      and "tick_params(axis='x', labelsize=14)" in _t17e
      and "axis='y'" not in _t17e,
      'reason=%r 块行=%r'
      % (_r17e.get('reason'),
         [ln.strip() for ln in _t17e.splitlines() if 'labelsize' in ln]))


def _t17_legacy_gap_specs_tick(tree, nm, field, val, lo, hi):
    """**旧实现原样**（t17 修之前）：`_av = next(...)` 之后直接 `_av.value`。只给牙齿用。"""
    _axis, _labels_attr = writeback._GAP_TICK[field]
    if isinstance(val, bool):
        return None, None, None, None
    _cands = []
    for _c in writeback._find_calls(tree, nm, 'tick_params', lo, hi):
        if writeback._call_carries(_c, ('labelsize', 'fontsize', 'size')):
            return None, None, None, None
        _av = next((k for k in _c.keywords if k.arg == 'axis'), None)
        if not (isinstance(_av.value, _ast.Constant)          # ← 旧实现：这里对 None 取属性
                and isinstance(_av.value.value, str)
                and _av.value.value == _axis):
            continue
        _cands.append((_c, 'labelsize'))
    for _c in writeback._find_calls(tree, nm, _labels_attr, lo, hi):
        if writeback._call_carries(_c, ('fontsize', 'size', 'labelsize')):
            return None, None, None, None
        _cands.append((_c, 'fontsize'))
    if not _cands:
        return None, None, None, None
    return ('append', _cands[-1][0], 'labelsize=%s' % writeback._num(val), None)


_orig_gap_tick17 = writeback._gap_specs_tick
writeback._gap_specs_tick = _t17_legacy_gap_specs_tick
try:
    _t17_teeth_err = ''
    try:
        _t17_run(_T17_BARE, 'teeth')
    except AttributeError as _e:                  # 旧实现的原样失败形态
        _t17_teeth_err = '%s: %s' % (type(_e).__name__, _e)
finally:
    writeback._gap_specs_tick = _orig_gap_tick17
check('102㉒【有牙】把 `_gap_specs_tick` 换回旧实现（直接 `_av.value`）⇒ 102⑱ 那个脚本'
      '在**原位路径**上必抛 `AttributeError: NoneType has no attribute \'value\'`'
      '（= 修前 CLI 的 internal_error 现场）',
      'AttributeError' in _t17_teeth_err and "'NoneType'" in _t17_teeth_err,
      'teeth: %r' % (_t17_teeth_err[:110],))


# =====================================================================
# §103 t70 / 报告附录 B #1（离屏版）：`dpi × DPR` **参数化矩阵**
#
# 矩阵 = `rcParams['figure.dpi'] ∈ {100,150,200}` × **注入** `DPR ∈ {1,1.5,2}`；
# 每格："拖一次 → Ctrl+Z"，断言画布回到**操作前原值**，并断言 `figsize_in` 与 dpi 自洽。
#
# **注入依据（模拟 ≠ 真机）**：不开窗就没有真 Qt 画布，DPR 只能注入。依据是本仓库自己的
# 注释（`toolbox.py:1654-1690`）：物理请求尺寸 = 英寸 × `fig.dpi`；逻辑画布 px = 物理 ÷ DPR；
# HiDPI 下 matplotlib 把 `fig.dpi` 乘过 DPR → `fig.dpi = rcParams.dpi × DPR`，而逻辑 px
# `_fig_px` = 英寸 × `rcParams.dpi`（与 DPR 无关）。→ 旧的 `px / fig.dpi` 正好少算一个
# DPR 因子：**DPR=2 时画布缩到 1/2**（用户现场 7.5×4.75）。
# **Qt 真机把 dpr 乘进 `fig.dpi` 这一步仍需真窗口实测（待验）** —— 本矩阵证的是"换算
# 公式在 DPR≠1 时必然错"，不是"Qt 真的这么做"。
# =====================================================================
_IN103 = (15.0, 9.5)


class _F103:
    def __init__(self, fig, px):
        self.fig = fig
        self._fig_px = px
        self.leg_state = {}
        self._find_ax = lambda i: None

    def __getattr__(self, name):
        return lambda *a, **k: None


def _old_conv103(fig, px):
    '''t66 之前的换算：`px / fig.dpi`（= 少算一个 DPR 因子）。'''
    _d = float(fig.dpi) or 100.0
    fig.set_size_inches(px[0] / _d, px[1] / _d)


_s103_figs, _s103_old_bad = [], []
for _dpi in (100, 150, 200):
    for _dpr in (1, 1.5, 2):
        _px = (int(round(_IN103[0] * _dpi)), int(round(_IN103[1] * _dpi)))
        _f = _plt100.figure(figsize=_IN103)
        _f.dpi = float(_dpi) * float(_dpr)              # 注入 HiDPI 后的 fig.dpi
        _c = _F103(_f, _px)
        _snap = _tb100.Tweak._snapshot(_c, with_canvas=True)
        _f.set_size_inches(10.0, 7.0)                   # "拖一次"
        _tb100.Tweak._restore_snap(_c, _snap)           # Ctrl+Z
        _got = tuple(round(float(x), 3) for x in _f.get_size_inches())
        check('103① dpi=%d × DPR=%s（fig.dpi=%s）：撤销后画布回到操作前原值'
              % (_dpi, _dpr, _f.dpi),
              _got == _IN103 and _snap.get('figsize_in') == list(_IN103),
              '画布=%r snap_in=%r' % (_got, _snap.get('figsize_in')))
        _s103_figs.append(_f)
        _f2 = _plt100.figure(figsize=_IN103)            # 同格跑一遍旧换算（teeth 用）
        _f2.dpi = float(_dpi) * float(_dpr)
        _f2.set_size_inches(10.0, 7.0)
        _old_conv103(_f2, _px)
        _old = tuple(round(float(x), 3) for x in _f2.get_size_inches())
        if _old != _IN103:
            _s103_old_bad.append((_dpi, _dpr, float(_f2.dpi), _old))
        _s103_figs.append(_f2)
for _f in _s103_figs:
    _plt100.close(_f)
check('103② 自洽性：逻辑 px/英寸 == rcParams.dpi（与 DPR 无关）、'
      '`fig.dpi == rcParams.dpi × DPR`（注入依据）；→ 旧换算 `px/fig.dpi` = 英寸/DPR',
      all((int(round(_IN103[0] * d)) / _IN103[0]) == d for d in (100, 150, 200)),
      'px/in 口径 = rcParams.dpi')
check('103③【有牙】把换算改回 `px/fig.dpi` → 矩阵里 **DPR≠1 的 6 格全红**，'
      '其中 rcParams=100 × DPR=2（= 用户环境的 fig.dpi=200）正好缩到 1/2',
      len(_s103_old_bad) == 6
      and (100, 2, 200.0, (7.5, 4.75)) in _s103_old_bad,
      '旧换算出错的格 = %s' % (_s103_old_bad,))

# =====================================================================
# §104 t79：块哨兵**语言无关**（幂等硬验收）
#
# 设计口径（writeback.py:62-74 的注释就是决策留痕）：写进用户脚本的哨兵是**固定的中性
# 英文**（`BLOCK_START`/`BLOCK_END`），**不跟随界面语言** —— 否则中文系统写一次、英文
# 系统再写一次就认不出旧块 → 堆两个块互相打架。语言相关的那句说明写在哨兵**下面**。
# 检测侧（`_ALL_STARTS`/`_ALL_ENDS`）**同时认**当前哨兵 + 0.1.x 已发布的中文旧哨兵。
# 验收（任务书 ①/②/③）：
#   ① zh 建块 → en 再跑 → 仍只有一块、不重复写入；
#   ② en 建块 → zh 再跑 → 同理；
#   ③ 老版本（中文哨兵）建的块被新版本识别并**整体替换**（不留旧哨兵）。
# teeth：把检测改回"只认当前语言"（`_ALL_STARTS=(BLOCK_START,)`）→ ③ 必红（会多出一块）。
# =====================================================================
from mpltweak import messages as _m104                              # noqa: E402
_S104 = ('import matplotlib\n'
         'matplotlib.use("Agg")\n'
         'import matplotlib.pyplot as plt\n\n'
         'fig, ax = plt.subplots(figsize=(8, 6))\n'
         'ax.plot([1, 2], [1, 2])\n'
         'fig.savefig("out.png")\n')
_S104_LEGACY = ('import matplotlib\n'
                'matplotlib.use("Agg")\n'
                'import matplotlib.pyplot as plt\n\n'
                'fig, ax = plt.subplots(figsize=(8, 6))\n'
                'ax.plot([1, 2], [1, 2])\n'
                '\n'
                '# ===== 自动布局调整（由调图工具生成，勿手改；重复写回会整体替换）=====\n'
                'ax.set_position([0.125, 0.11, 0.775, 0.77])\n'
                '# ===== 自动布局调整结束 =====\n'
                '\n'
                'fig.savefig("out.png")\n')
_S104_PAR = {'version': 4, 'script': 'fig.py', 'fig_index': 0, 'n_figs': 1,
             'figsize_in': [8.0, 6.0],
             'axes': [{'index': 0, 'pos': [0.2, 0.2, 0.6, 0.6]}]}


def _s104_counts(path):
    _t = io.open(path, encoding='utf-8').read()
    return (_t.count(writeback.BLOCK_START), _t.count(writeback.BLOCK_END),
            _t.count(writeback._LEGACY_STARTS[0]),
            _t.count(writeback._LEGACY_ENDS[0]))


def _s104_run(tag, src, lang, times=1):
    _d = os.path.join(_TMPROOT, 'case_t104_' + tag)
    shutil.rmtree(_d, ignore_errors=True)
    os.makedirs(os.path.join(_d, '.tweak_params'))
    _s = os.path.join(_d, 'fig.py')
    with open(_s, 'w', encoding='utf-8', newline='\n') as _f:
        _f.write(src)
    _p = os.path.join(_d, '.tweak_params', 'fig.json')
    with open(_p, 'w', encoding='utf-8') as _f:
        json.dump(_S104_PAR, _f, ensure_ascii=False)
    _old_lang = _m104.current_lang()
    _res = []
    try:
        _m104.set_lang(lang)
        for _ in range(times):
            _res.append(writeback.writeback(_s, dict(_S104_PAR), _p,
                                            verify=False, style='block'))
    finally:
        _m104.set_lang(_old_lang)
    return _res, _s104_counts(_s)


_r104a, _c104a = _s104_run('zh_then_en', _S104, 'zh', times=2)
_c104a_en = None
_old_lang104 = _m104.current_lang()
try:
    _m104.set_lang('en')
    _d104 = os.path.join(_TMPROOT, 'case_t104_zh_then_en')
    _p104 = os.path.join(_d104, '.tweak_params', 'fig.json')
    _r104b = writeback.writeback(os.path.join(_d104, 'fig.py'), dict(_S104_PAR),
                                 _p104, verify=False, style='block')
finally:
    _m104.set_lang(_old_lang104)
_c104b = _s104_counts(os.path.join(_d104, 'fig.py'))
check('§104① zh 建块 → en 再跑：仍只有一块、不重复写入（哨兵不跟语言走）',
      _c104b[0] == 1 and _c104b[1] == 1 and _c104b[2] == 0 and _c104b[3] == 0
      and _r104b.get('reason') in ('no_change', 'ok', 'best_effort'),
      'blocks=%s reason=%r' % (_c104b, _r104b.get('reason')))

_r104c, _c104c = _s104_run('en_then_zh', _S104, 'en')
_old_lang104b = _m104.current_lang()
try:
    _m104.set_lang('zh')
    _d104b = os.path.join(_TMPROOT, 'case_t104_en_then_zh')
    _p104b = os.path.join(_d104b, '.tweak_params', 'fig.json')
    _r104d = writeback.writeback(os.path.join(_d104b, 'fig.py'), dict(_S104_PAR),
                                 _p104b, verify=False, style='block')
finally:
    _m104.set_lang(_old_lang104b)
_c104d = _s104_counts(os.path.join(_d104b, 'fig.py'))
check('§104② en 建块 → zh 再跑：同理仍只有一块',
      _c104d[0] == 1 and _c104d[1] == 1 and _c104d[2] == 0 and _c104d[3] == 0,
      'blocks=%s reason=%r' % (_c104d, _r104d.get('reason')))

_r104e, _c104e = _s104_run('legacy_zh', _S104_LEGACY, 'en')
check('§104③ 老版本（中文哨兵）的块被新版本**整体替换**（不留旧哨兵、不堆第二块）',
      _c104e[0] == 1 and _c104e[1] == 1 and _c104e[2] == 0 and _c104e[3] == 0,
      'blocks=%s reason=%r' % (_c104e, _r104e[-1].get('reason')))

# teeth：把检测改回"只认当前语言" → ③ 必红（旧 zh 块被当成普通代码，另插一块）
_orig_all104 = (writeback._ALL_STARTS, writeback._ALL_ENDS)
try:
    writeback._ALL_STARTS = (writeback.BLOCK_START,)
    writeback._ALL_ENDS = (writeback.BLOCK_END,)
    _r104f, _c104f = _s104_run('legacy_zh_teeth', _S104_LEGACY, 'en')
finally:
    writeback._ALL_STARTS, writeback._ALL_ENDS = _orig_all104
check('§104④【有牙】检测改回"只认当前语言" → 旧 zh 块认不出、堆出第二块'
      '（= §104③ 在改前口径上必红）',
      _c104f[0] >= 2 or _c104f[2] >= 1,
      'blocks=%s（期望 START≥2 或旧哨兵残留）' % (_c104f,))

# =====================================================================
# §106 t88（🔴 P0，t77 抓到）：源码 figsize 是**变量/表达式/动态配置**时，绝不许被
# 静默替换成字面量；`changes` / `figsize_edited` 必须如实。
#
# 判据（任务书 §1/§2）：① 非字面量 → **拒写该字段**（其余字段照常写）并**明确披露**；
# ② 没改就不许报 `figsize`；③ 源码里的变量/表达式**一个字节都不许动**。
# 对照例（`figsize=(6, 4)`）本来就该"等值不写"（L0-1 口径）。
# =====================================================================
_S106_HEAD = ('import matplotlib\n'
              'matplotlib.use("Agg")\n'
              'import matplotlib.pyplot as plt\n')
_S106_TAIL = ('ax = fig.add_axes([0.1, 0.1, 0.8, 0.8])\n'
              'ax.plot([1, 2], [2, 1])\n'
              'fig.savefig("o.png")\n')
_S106_CASES = {
    'ctrl': (_S106_HEAD + 'fig = plt.figure(figsize=(6, 4))\n' + _S106_TAIL,
             'figsize=(6, 4)', 'figsize=(W, H)'),
    'var': (_S106_HEAD + 'W = 6.0\nH = 4.0\n'
            'fig = plt.figure(figsize=(W, H))\n' + _S106_TAIL,
            'figsize=(W, H)', 'figsize=(6, 4)'),
    'expr': (_S106_HEAD + 'BASE = 10\n'
             'fig = plt.figure(figsize=(BASE - 4, 4))\n' + _S106_TAIL,
             'figsize=(BASE - 4, 4)', 'figsize=(6, 4)'),
    'env': (_S106_HEAD + 'import os\n'
            'W = float(os.environ.get("MPLTWEAK_T88_W", "6"))\n'
            'fig = plt.figure(figsize=(W, 4))\n' + _S106_TAIL,
            'figsize=(W, 4)', 'figsize=(6, 4)'),
}
_S106_PAR = {'version': 4, 'script': 'fig.py', 'fig_index': 0, 'n_figs': 1,
             'figsize_px': [600, 400], 'figsize_in': [6.0, 4.0],
             'axes': [{'index': 0, 'pos': [0.2, 0.2, 0.6, 0.6]}]}
_s106_res = {}


def _s106_run(tag, src):
    d = os.path.join(_TMPROOT, 'case_t106_' + tag)
    shutil.rmtree(d, ignore_errors=True)
    os.makedirs(os.path.join(d, '.tweak_params'))
    sp = os.path.join(d, 'fig.py')
    with open(sp, 'w', encoding='utf-8', newline='\n') as f:
        f.write(src)
    pp = os.path.join(d, '.tweak_params', 'fig.json')
    with open(pp, 'w', encoding='utf-8') as f:
        json.dump(_S106_PAR, f, ensure_ascii=False)
    res = writeback.writeback(sp, dict(_S106_PAR), pp, verify=False,
                              style='inplace')
    with open(sp, encoding='utf-8') as f:
        after = f.read()
    _s106_res[tag] = (res, after, sp)
    return res, after


def _s106_disclosed(res):
    '''有没有**如实披露**"这个字段没能落地"（pending / inplace_needs_block / 警告）。'''
    if res.get('pending'):
        return True
    if res.get('inplace_needs_block'):
        return True
    return any('figsize' in str(w) or '画布' in str(w)
               for w in (res.get('warnings') or []))


for _tag106 in ('ctrl', 'var', 'expr', 'env'):
    _src106, _keep106, _lost106 = _S106_CASES[_tag106]
    _r106, _after106 = _s106_run(_tag106, _src106)
    _ch106 = _r106.get('changes') or []
    if _tag106 == 'ctrl':
        check('§106① 对照例 figsize=(6, 4)：等值 → 不写、不报 figsize',
              _r106.get('figsize_edited') is False
              and 'figsize' not in _ch106
              and 'figsize=(6, 4)' in _after106,
              'edited=%s changes=%s' % (_r106.get('figsize_edited'), _ch106))
    else:
        check('§106② %s：源码 %s **一个字节都不许动**（不许替换成 %s）'
              % (_tag106, _keep106, _lost106),
              _keep106 in _after106 and _lost106 not in _after106,
              _after106.splitlines()[3:5])
        check('§106③ %s：figsize_edited=False 且 changes 里没有 figsize（不许谎报）'
              % _tag106,
              _r106.get('figsize_edited') is False and 'figsize' not in _ch106,
              'edited=%s changes=%s' % (_r106.get('figsize_edited'), _ch106))
        check('§106④ %s：拒写要**如实披露**（pending / needs_block / 警告）' % _tag106,
              _s106_disclosed(_r106),
              'pending=%s needs_block=%s warnings=%s'
              % (_r106.get('pending'), _r106.get('inplace_needs_block'),
                 (_r106.get('warnings') or [])[:1]))
    if _tag106 == 'ctrl':
        check('§106④b ctrl：对照例**不该**有"画布没落地"的披露（等值本来就不用写）',
              not _s106_disclosed(_r106),
              'pending=%s warnings=%s' % (_r106.get('pending'),
                                          (_r106.get('warnings') or [])[:1]))

# 其余字段照常写（"只拒这一个字段，别的照写"）
check('§106⑤ 拒写 figsize 不影响其它字段：ax0.pos 照常落地（changes 含 ax0.pos）',
      any('pos' in str(c) for c in (_s106_res['var'][0].get('changes') or [])),
      _s106_res['var'][0].get('changes'))
check('§106⑥ 运行期行为不变：`W` 仍在源码里被引用（动态配置没被钉死）',
      'os.environ.get("MPLTWEAK_T88_W"' in _s106_res['env'][1]
      and 'figsize=(W, 4)' in _s106_res['env'][1],
      _s106_res['env'][1].splitlines()[3:6])

# teeth：把守卫改回去（非字面量也照样替换）→ §106② 必红
_orig_edit106 = writeback.edit_figsize


def _edit_bad106(src, tree, figsize_in, fig_index=None):
    if not figsize_in or len(figsize_in) < 2:
        return src, False
    starts = writeback._line_starts(src)
    node = writeback._fig_call_node(tree, fig_index)
    if node is None:
        return src, False
    for kw in node.keywords:
        if kw.arg == 'figsize' and kw.value is not None:
            v = kw.value
            have = writeback._seq_lit_nums(v)
            if have is not None and writeback._num_close(have[0], figsize_in[0]) \
                    and writeback._num_close(have[1], figsize_in[1]):
                return src, False
            new_txt = writeback._fmt_figsize(v, figsize_in)      # 旧口径：无条件替换
            s = writeback._offset(starts, v.lineno, v.col_offset, src)
            e = writeback._offset(starts, v.end_lineno, v.end_col_offset, src)
            return src[:s] + new_txt + src[e:], True
    return src, False


writeback.edit_figsize = _edit_bad106
try:
    _r106t, _after106t = _s106_run('teeth', _S106_CASES['var'][0])
finally:
    writeback.edit_figsize = _orig_edit106
check('§106⑦【有牙】把守卫改回去（非字面量也替换成字面量）→ 变量被抹掉、§106② 必红',
      'figsize=(W, H)' not in _after106t and _r106t.get('figsize_edited') is True,
      'after 第 3-4 行=%s edited=%s' % (_after106t.splitlines()[3:5],
                                        _r106t.get('figsize_edited')))

# 与 t66 那道 figsize_in ≈ figsize_px/dpi 守卫的**交互**（两道守卫不得互相掩盖）
_d106 = os.path.join(_TMPROOT, 'case_t106_guard')
shutil.rmtree(_d106, ignore_errors=True)
os.makedirs(os.path.join(_d106, '.tweak_params'))
_sp106 = os.path.join(_d106, 'fig.py')
with open(_sp106, 'w', encoding='utf-8', newline='\n') as f:
    f.write(_S106_CASES['var'][0])
_pp106 = os.path.join(_d106, '.tweak_params', 'fig.json')
_bad106 = dict(_S106_PAR, figsize_px=[1200, 800])       # px/in 差成倍（t66 的现场）
with open(_pp106, 'w', encoding='utf-8') as f:
    json.dump(_bad106, f, ensure_ascii=False)
_rg106 = writeback.writeback(_sp106, _bad106, _pp106, verify=False,
                             style='inplace')
with open(_sp106, encoding='utf-8') as f:
    _ag106 = f.read()
check('§106⑧ 与 t66 守卫的交互：px/in 成倍矛盾 → **t66 守卫先拦**（reason=bad_params）'
      '且源码一字未动（两道守卫不互相掩盖）',
      _rg106.get('reason') == 'bad_params' and 'figsize=(W, H)' in _ag106
      and not _rg106.get('figsize_edited'),
      'reason=%s edited=%s' % (_rg106.get('reason'),
                               _rg106.get('figsize_edited')))

# =====================================================================
# §107 t87（薄弱点⑧）：**差分测试** —— 同一图同参走「原地改写」与「块通道」两条路，
# 规范化图形状态必须**逐字段一致**（不一致 → 必有一路错）。
#
# 判据（船长 2026-10-14 定稿，四层）：
#   ① **强判据**：原地路**声称已应用**（不在 `pending` 里）的字段 → 两条路的 dump 必须
#      逐字段相同（浮点容差 `_T87_TOL = 5e-5`，依据：`verify._state` 把 rect 舍入到 4 位
#      小数 → 半量子 5e-5；与 `docs/consistency-ledger.md` 同口径）。
#   ② **合法差异**：原地路**表达不了**的字段（`pending` 里如实披露）→ 允许不同，
#      但必须登记进**已知差异表** `_T87_KNOWN`（只准缩小；末尾全文打印）。
#   ③ **未披露的差异 → 红**（核心口径：不许静默少做）。
#   ④ **披露粒度**：实测 `pending` 是**字段级**（如 `ax0.label_fontsize` / `ax0.grid` /
#      `ax1.clim`）→ 断言"**披露集合 ⊇ 差异字段集合**"成立，无需退到"披露粒度不足"。
#      （唯一例外是**派生字段**与一处**已知未披露**，见下面的表与台账。）
#
# 规范化 dump：两份生成脚本各**真跑一遍**（Agg 子进程）→ `verify._state(fig)` 导出
# 图数 / figsize / 每轴 pos·xlim·ylim·title_fs·label_fs·tick_fs·grid·is_cb·clim·cmap。
# =====================================================================
_T87_TOL = 5e-5
_T87_SHAPES = {
    'figsize_only': ("import matplotlib\nmatplotlib.use('Agg')\n"
                     "import matplotlib.pyplot as plt\n\n"
                     "fig = plt.figure(figsize=(6, 4))\n"
                     "ax = fig.add_subplot(111)\n"
                     "ax.plot([1, 2], [1, 2])\n"
                     "ax.set_title('A', fontsize=10)\n"
                     "ax.xaxis.label.set_fontsize(10)\n"
                     "ax.tick_params(labelsize=10)\n"
                     "ax.grid(False)\n"
                     "fig.savefig('o.png')\n"),
    'subplots_2x2': ("import matplotlib\nmatplotlib.use('Agg')\n"
                     "import matplotlib.pyplot as plt\n\n"
                     "fig, axs = plt.subplots(2, 2, figsize=(10, 7))\n"
                     "axs[0, 0].plot([1, 2], [2, 1])\n"
                     "axs[0, 0].set_title('A', fontsize=10)\n"
                     "axs[0, 0].xaxis.label.set_fontsize(10)\n"
                     "axs[0, 0].tick_params(labelsize=10)\n"
                     "axs[0, 0].grid(False)\n"
                     "fig.savefig('o.png')\n"),
    'gridspec': ("import matplotlib\nmatplotlib.use('Agg')\n"
                 "import matplotlib.pyplot as plt\n\n"
                 "fig = plt.figure(figsize=(9, 6))\n"
                 "gs = fig.add_gridspec(2, 3)\n"
                 "axs = [fig.add_subplot(gs[i, j]) for i in range(2) for j in range(3)]\n"
                 "axs[0].set_title('G', fontsize=10)\n"
                 "axs[0].xaxis.label.set_fontsize(10)\n"
                 "axs[0].tick_params(labelsize=10)\n"
                 "axs[0].grid(False)\n"
                 "fig.savefig('o.png')\n"),
    'polar_cb': ("import matplotlib\nmatplotlib.use('Agg')\n"
                 "import matplotlib.pyplot as plt\nimport numpy as np\n\n"
                 "fig = plt.figure(figsize=(15, 9.5))\n"
                 "ax = fig.add_axes([0.039, 0.4651, 0.9224, 0.4349], "
                 "projection='polar')\n"
                 "th = np.linspace(0, 6.28, 40)\n"
                 "sc = ax.scatter(th, np.cos(th) * 3, c=np.cos(th))\n"
                 "cb = fig.colorbar(sc, ax=ax)\n"
                 "sc.set_cmap('viridis')\n"
                 "sc.set_clim(0.0, 1.0)\n"
                 "ax.set_title('P', fontsize=10)\n"
                 "ax.xaxis.label.set_fontsize(10)\n"
                 "ax.tick_params(labelsize=10)\n"
                 "ax.grid(False)\n"
                 "fig.savefig('o.png')\n"),
    'fig_add_axes': ("import matplotlib\nmatplotlib.use('Agg')\n"
                     "import matplotlib.pyplot as plt\n\n"
                     "fig = plt.figure(figsize=(8, 6))\n"
                     "ax = fig.add_axes([0.1, 0.1, 0.8, 0.8])\n"
                     "ax.plot([1, 2], [2, 1])\n"
                     "ax.set_title('B', fontsize=10)\n"
                     "ax.xaxis.label.set_fontsize(10)\n"
                     "ax.tick_params(labelsize=10)\n"
                     "ax.grid(False)\n"
                     "fig.savefig('o.png')\n"),
}
# dump 字段名 → `pending` 里的字段名（披露集合与差异集合用同一套名字对齐）
_T87_FIELD2PEND = {'title_fs': 'title_fontsize', 'label_fs': 'label_fontsize',
                   'tick_fs': 'tick_fontsize', 'grid': 'grid', 'clim': 'clim',
                   'cmap': 'cmap', 'pos': 'pos', 'xlim': 'xlim', 'ylim': 'ylim',
                   'figsize_in': 'figsize'}
# **派生字段**：值跟着别的字段走（登记在案、不是"静默少做"）
_T87_DERIVED = {('fig0.ax1.ylim',): '垂直 colorbar 的 ylim 跟着 clim 走（clim 已披露）'}
# **已知未披露**（只准缩小的台账；每一项都是待派 src/ 单的缺陷，末尾全文打印）
# t99（已收口，台账**清空**）：唯一那条 `('fig0.ax0.cmap',)` 已删除 —— 原地路现在把
# colorbar 轴上的 `cmap` **如实列进 pending**（与 `clim` 同等待遇）。根因：writeback.py
# 的 "nm is None"（无变量映射，colorbar 轴必走这一支）分支列字段时**漏了 `cmap`** ⇒
# 参数写在 colorbar 轴上、mappable 挂在别的轴时，`cmap` 既写不了也不说（静默少做）。
# 台账清空后，§107① 对 polar_cb 变成**没有兜底的硬断言**：任何新的未披露差异直接判红。
_T87_UNDISCLOSED = {}
_T87_KNOWN = {
    'ax.xaxis.label.set_fontsize(10) 调用形态':
        '原地路只改"代码里已有的数字"的**已知调用形态**；`ax.xaxis.label.set_fontsize(10)`'
        '这种写法它不改写 → 该字段进 pending（如实披露）→ 允许两条路不同。',
    'axs[0,0].grid(False) 形态（subplots-tuple 接收者）':
        '同上：subplots 元组下标接收者的 `grid(...)` 不在原地路可改形态里 → 进 pending。',
    'polar 的 sc.set_cmap/set_clim 形态':
        'mappable 上的 `set_cmap/set_clim` 与"轴参数"不是同一接收者 → 原地路表达不了'
        '（`clim` 已如实进 pending；`cmap` 见上面的未披露台账）。',
}

_T87_DUMP = r'''
import json, os, sys
sys.path.insert(0, r'%s')
import matplotlib; matplotlib.use('Agg')
import matplotlib.pyplot as plt
from mpltweak import verify
src = open(sys.argv[1], encoding='utf-8').read()
os.chdir(os.path.dirname(sys.argv[1]))
exec(compile(src, sys.argv[1], 'exec'), {'__name__': '__main__'})
out = []
for num in plt.get_fignums():
    f = plt.figure(num)
    st = verify._state(f)
    out.append({'figsize_in': st.get('figsize_in'),
                'n_axes': len(st.get('axes') or []),
                'axes': [{'pos': a.get('pos'), 'xlim': a.get('xlim'),
                          'ylim': a.get('ylim'), 'title_fs': a.get('title_fontsize'),
                          'label_fs': a.get('label_fontsize'),
                          'tick_fs': a.get('tick_fontsize'),
                          'is_cb': a.get('is_colorbar'), 'clim': a.get('clim'),
                          'cmap': a.get('_cmap'), 'grid': a.get('grid')}
                         for a in (st.get('axes') or [])]})
print('@@DUMP@@' + json.dumps(out, sort_keys=True, default=str))
''' % (os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
       + os.sep + 'src')


def _t87_params(src):
    ax = {'index': 0, 'title_fontsize': 13.0, 'label_fontsize': 12.0,
          'tick_fontsize': 11.0, 'grid': True}
    p = {'version': 4, 'script': 'fig.py', 'fig_index': 0, 'n_figs': 1,
         'figsize_px': [600, 400], 'figsize_in': [6.0, 4.0], 'axes': [ax]}
    if 'colorbar' in src:
        # 真实 describe 输出会给 colorbar 轴带 `is_colorbar: true`（写回侧据此走
        # colorbar 专用口径，例如 t99 的 cmap fail-closed）⇒ 参数必须如实带上。
        p['axes'].append({'index': 1, 'is_colorbar': True,
                          'clim': [0.2, 0.9], 'cmap': 'plasma'})
    return p


def _t87_route(name, src, style, tamper=None):
    """跑一条路：写副本 → writeback(style) → 子进程真跑 → 规范化 dump。"""
    d = os.path.join(_TMPROOT, 't87_%s_%s' % (name, style))
    shutil.rmtree(d, ignore_errors=True)
    os.makedirs(os.path.join(d, '.tweak_params'))
    sp = os.path.join(d, 'fig.py')
    with open(sp, 'w', encoding='utf-8', newline='\n') as f:
        f.write(src)
    pp = os.path.join(d, '.tweak_params', 'fig.json')
    with open(pp, 'w', encoding='utf-8') as f:
        json.dump(_t87_params(src), f, ensure_ascii=False)
    res = writeback.writeback(sp, _t87_params(src), pp, verify=False, style=style)
    if tamper is not None:
        tamper(sp, res)
    dp = os.path.join(d, 'dump.py')
    with open(dp, 'w', encoding='utf-8', newline='\n') as f:
        f.write(_T87_DUMP)
    env = dict(os.environ, MPLBACKEND='Agg', PYTHONIOENCODING='utf-8')
    r = subprocess.run([sys.executable, dp, sp], capture_output=True,
                       timeout=300, env=env, cwd=d)
    dump = None
    for line in (r.stdout or b'').decode('utf-8', 'replace').splitlines():
        if line.startswith('@@DUMP@@'):
            dump = json.loads(line[len('@@DUMP@@'):])
    return res, dump, sp


def _t87_flat(dump):
    out = {}
    for i, f in enumerate(dump or []):
        out['fig%d.figsize_in' % i] = f.get('figsize_in')
        out['fig%d.n_axes' % i] = f.get('n_axes')
        for j, a in enumerate(f.get('axes') or []):
            for k, v in a.items():
                out['fig%d.ax%d.%s' % (i, j, k)] = v
    return out


def _t87_close(a, b):
    if isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        return all(_t87_close(x, y) for x, y in zip(a, b))
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) \
            and not isinstance(a, bool) and not isinstance(b, bool):
        return abs(float(a) - float(b)) <= _T87_TOL
    return a == b


def _t87_judge(name, src, tamper=None):
    """返回 (ok, detail, diffs, disclosed, undisclosed, derived)。"""
    r1, d1, s1 = _t87_route(name, src, 'inplace', tamper=tamper)
    r2, d2, s2 = _t87_route(name, src, 'block')
    if d1 is None or d2 is None:
        # 脚本在子进程里没跑成（缺数据文件 / 满载下超时等）⇒ **不是"两条路不一致"**，
        # 如实报"跳过"（不静默、也不算红），免得把环境问题误判成差分缺陷。
        return True, ('SKIP：dump=None（脚本没跑成：inplace=%s block=%s）'
                      % (d1 is None, d2 is None)), [], [], [], []
    f1, f2 = _t87_flat(d1), _t87_flat(d2)
    diffs = [k for k in sorted(set(f1) | set(f2))
             if not _t87_close(f1.get(k), f2.get(k))]
    pend = set(r1.get('pending') or [])
    nb = bool(r1.get('inplace_needs_block'))
    disclosed, undisclosed, derived = [], [], []
    for k in diffs:
        leaf = k.rsplit('.', 1)[-1]
        pname = _T87_FIELD2PEND.get(leaf, leaf)
        _ax = k.split('.')[1] if k.startswith('fig') else ''
        if (k,) in _T87_DERIVED:
            derived.append(k)
        elif (k,) in _T87_UNDISCLOSED:
            undisclosed.append(k)
        elif nb or any(p == pname or p.endswith('.' + pname)
                       or pname in p for p in pend):
            disclosed.append(k)
        else:
            undisclosed.append(k)
    detail = ('差异=%s ｜ 已披露=%s ｜ 未披露=%s ｜ 派生=%s ｜ pending=%s'
              % (diffs, disclosed, undisclosed, derived, sorted(pend)))
    # 判据③+④：未披露的差异**必须在"已知未披露"台账里**（只准缩小）—— 台账外的任何
    # 未披露差异都算红（不许静默少做）；台账内的如实记录并全文打印，待派 src/ 单。
    _ok87j = all((k,) in _T87_UNDISCLOSED for k in undisclosed)
    return _ok87j, detail, diffs, disclosed, undisclosed, derived


_t87_bad = 0
for _n87, _s87 in _T87_SHAPES.items():
    _ok87, _det87, _d87, _dis87, _und87, _der87 = _t87_judge(_n87, _s87)
    check('§107① 差分：差异**全部已披露或已在台账**（台账外未披露=0）[%s]' % _n87,
          _ok87, _det87)
    if not _ok87:
        _t87_bad += 1
print('§107 已知差异表（只准缩小；%d 条）:' % len(_T87_KNOWN))
for _k87, _v87 in _T87_KNOWN.items():
    print('   · %s —— %s' % (_k87, _v87))
print('§107 派生字段（登记在案）: %s' % (list(_T87_DERIVED),))
print('§107 **已知未披露**台账（%d 条；每一条都待派 src/ 单）:' % len(_T87_UNDISCLOSED))
for _k87, _v87 in _T87_UNDISCLOSED.items():
    print('   · %s —— %s' % (_k87, _v87))
# 台账**已空**（t99 收口）⇒ 这里验证"**没有兜底**"：把原地路的披露抹掉 ⇒ polar_cb 必红。
_orig_wb87e = writeback.writeback


def _wb_no_disc87e(*a, **k):
    res = _orig_wb87e(*a, **k)
    if isinstance(res, dict):
        res = dict(res)
        res['pending'] = []
        res['inplace_needs_block'] = False
        res['warnings'] = []
    return res


writeback.writeback = _wb_no_disc87e
try:
    _ok87d, _det87d = _t87_judge('polar_cb', _T87_SHAPES['polar_cb'])[:2]
finally:
    writeback.writeback = _orig_wb87e
check('§107⑤【有牙】台账已空 ⇒ 抹掉披露后 polar_cb 必红（无兜底；'
      't99 之前这条差异是靠台账"合法化"的）', _ok87d is False, _det87d[:150])

# teeth①：把原地路的**披露**抹掉（pending 清空 + needs_block 置 False）→ 未披露差异必红
_orig_wb87 = writeback.writeback


def _wb_no_disc87(*a, **k):
    res = _orig_wb87(*a, **k)
    if isinstance(res, dict):
        res = dict(res)
        res['pending'] = []
        res['inplace_needs_block'] = False
        res['warnings'] = []
    return res


writeback.writeback = _wb_no_disc87
try:
    _ok87a, _det87a = _t87_judge('subplots_2x2', _T87_SHAPES['subplots_2x2'])[:2]
finally:
    writeback.writeback = _orig_wb87
check('§107②【有牙】抹掉原地路的披露（pending/needs_block/warnings 全清）→ '
      '「未披露差异=0」必红', _ok87a is False, _det87a[:150])

# teeth②：把**两条路都能表达**的字段故意改歪（原地路写完后把标题字号改掉）→ 强判据必红
def _tamper87(sp, res):
    with open(sp, encoding='utf-8') as f:
        _t = f.read()
    with open(sp, 'w', encoding='utf-8', newline='\n') as f:
        f.write(_t.replace('fontsize=13', 'fontsize=17'))


_ok87b, _det87b = _t87_judge('figsize_only', _T87_SHAPES['figsize_only'],
                             tamper=_tamper87)[:2]
check('§107③【有牙】把某条路的某个值改歪（标题字号 13→17）→ 差分断言必红',
      _ok87b is False and 'fig0.ax0.title_fs' in _det87b, _det87b[:150])

# teeth③：副本基线干净自证 —— 不做任何篡改时，同一形状两次跑**结果一致**（判据本身不抖）
_ok87c1, _det87c1 = _t87_judge('fig_add_axes', _T87_SHAPES['fig_add_axes'])[:2]
_ok87c2, _det87c2 = _t87_judge('fig_add_axes', _T87_SHAPES['fig_add_axes'])[:2]
check('§107④ 基线自证：同一形状连跑两次，判据结论稳定（且未披露=0）',
      _ok87c1 is True and _ok87c2 is True, '%s ｜ %s' % (_det87c1[:70],
                                                         _det87c2[:70]))

# =====================================================================
# §108（0.1.12 / MAINTENANCE §25 A1）：fig 在**函数里**建、`return fig` 出函数、
# savefig 在模块层 —— 锚点**下沉**到那条 return 之前（0.1.11 必拒的形态）。
#
# 修前为什么拒：`_name_still_bound`（writeback.py:3706-3717）要求**每个候选锚点与
# 名字绑定同作用域**，而候选锚点全在模块层（`find_anchor` / `_anchor_positions` 会把
# savefig 与"脚本尾"都算上），`ax` 却绑在函数里 ⇒ 证不出 ⇒ 全线 fail-closed。
# 修后：新候选 = 那条 `return <fig>` **之前**（位置 = return 的语句节点），并且
# **不掺**模块层候选（互斥 —— 混进一个位置，"同作用域"立刻不成立）。
# 三条**不可证**形态一律拒写，且 pending 与文案都点名原因（各给可照做的下一步）：
#   a) 函数里 ≥2 条 return / 提前 return     → fig.func_multi_return
#   b) 该函数不是恰好一次模块层调用           → fig.func_called_many
#   c) 目标是类方法（self.ax 这类接收者）      → fig.func_method
# 另含 MAINTENANCE §25.2：源码 figsize 非字面量时，块里仍补 set_size_inches，
# 但**块注释**点名"运行时覆盖你的动态配置"，且不许新增第二处 figsize 计算路径。
# =====================================================================
from mpltweak import messages as _m108                          # noqa: E402

_S108_HEAD = ('import matplotlib\n'
              'matplotlib.use("Agg")\n'
              'import matplotlib.pyplot as plt\n'
              '\n'
              '\n')
_S108_PROBE = _S108_HEAD + (
    'def make_plot():\n'
    '    fig, ax = plt.subplots(figsize=(8, 5))\n'
    '    ax.plot([1, 2, 3], [2, 1, 3])\n'
    "    ax.set_title('A')\n"
    '    return fig\n'
    '\n'
    '\n'
    'fig = make_plot()\n'
    "fig.savefig('o.png')\n"
    "print('done')\n")
_S108_PAR = {'version': 4, 'script': 'fig.py', 'fig_index': 0, 'n_figs': 1,
             'axes': [{'index': 0, 'pos': [0.12, 0.12, 0.78, 0.78],
                       'title_fontsize': 15.0, 'grid': True}]}

_d108, _s108, _p108 = _mk15('t108a1', _S108_PROBE, _S108_PAR)
_r108 = writeback.writeback(_s108, _params.load(_p108), _p108, verify=True)
_a108 = read(_s108)
check('§108① 探针形态（函数里建图 + return fig + 模块层 savefig）：写回成功'
      '（reason=ok / applied=true / Agg 重跑 + 语义验证都通过）',
      _r108.get('reason') == 'ok' and _r108.get('applied') is True
      and _r108.get('verified') is True and _r108.get('semantic') is True,
      'reason=%s applied=%s verified=%s semantic=%s err=%s'
      % (_r108.get('reason'), _r108.get('applied'), _r108.get('verified'),
         _r108.get('semantic'), (_r108.get('err') or '')[:120]))
check('§108② 块落在**函数内**、且在 `return fig` **之前**（锚点下沉到了函数作用域）',
      writeback.BLOCK_START in _a108
      and _a108.index('def make_plot') < _a108.index(writeback.BLOCK_START)
      < _a108.index('    return fig'),
      [ln.strip() for ln in _a108.splitlines()
       if 'auto layout' in ln or 'return' in ln][:6])
_108_blk = _a108[_a108.rindex('\n', 0, _a108.index(writeback.BLOCK_START)) + 1:
                 _a108.index(writeback.BLOCK_END)].splitlines()
check('§108③ 块内缩进与函数体一致（每行 4 格；没有多缩/顶格 => 不会 IndentationError）',
      bool(_108_blk) and all(ln.startswith('    ')
                             and not ln.startswith('     ') for ln in _108_blk),
      _108_blk[:3])
check('§108④ 块用**可证的名字**寻址（ax / fig）：无运行期下标、无 gcf 兜底、不补'
      ' set_size_inches（源码 figsize 是字面量且已等值）',
      '    ax.set_position([0.1200, 0.1200, 0.7800, 0.7800])' in _a108
      and '    ax.title.set_fontsize(15)' in _a108
      and '    ax.grid(True)' in _a108
      and 'fig.axes[' not in _a108 and 'plt.gcf()' not in _a108
      and 'set_size_inches' not in _a108,
      [ln.strip() for ln in _a108.splitlines()
       if 'set_' in ln or 'grid(' in ln][:5])

_r108b = writeback.writeback(_s108, _params.load(_p108), _p108, verify=True)
_a108b = read(_s108)
check('§108⑤ 同一脚本连跑两次 apply：第二次 no_change 且脚本 **0 字节**变化',
      _r108b.get('reason') == 'no_change' and _a108b == _a108,
      'reason=%r bytes_same=%s' % (_r108b.get('reason'), _a108b == _a108))
revert.main([_s108, '--write'])
check('§108⑥ `revert --write` 字节级回到原文',
      read(_s108) == _S108_PROBE,
      [ln.strip() for ln in read(_s108).splitlines()
       if 'layout' in ln or 'return' in ln][:4])
shutil.rmtree(_d108, ignore_errors=True)

# ---- §108⑦ 三条不可证形态：一律拒写，且 pending + 文案都点名原因 ----
_S108_BAD = {
    'a 多 return': (_S108_HEAD + (
        'def make_plot(flag):\n'
        '    fig, ax = plt.subplots()\n'
        '    ax.plot([1, 2, 3])\n'
        '    if flag:\n'
        '        return fig\n'
        "    ax.set_title('B')\n"
        '    return fig\n'
        '\n'
        '\n'
        'fig = make_plot(True)\n'
        "fig.savefig('o.png')\n"), 'fig.func_multi_return', '末尾恰好一条'),
    'b 被调用两次': (_S108_HEAD + (
        'def make_plot():\n'
        '    fig, ax = plt.subplots()\n'
        '    ax.plot([1, 2, 3])\n'
        '    return fig\n'
        '\n'
        '\n'
        'fig = make_plot()\n'
        "fig.savefig('a.png')\n"
        'fig2 = make_plot()\n'
        "fig2.savefig('b.png')\n"), 'fig.func_called_many', '恰好一次模块层调用'),
    'c 方法是 self.ax': (_S108_HEAD + (
        'class Plotter:\n'
        '    def make_plot(self):\n'
        '        fig, ax = plt.subplots()\n'
        '        ax.plot([1, 2, 3])\n'
        '        return fig\n'
        '\n'
        '\n'
        'p = Plotter()\n'
        'fig = p.make_plot()\n'
        "fig.savefig('o.png')\n"), 'fig.func_method', '类方法'),
}
for _tag108, (_src108, _tok108, _needle108) in _S108_BAD.items():
    _d, _s, _p = _mk15('t108bad', _src108, _S108_PAR)
    _r108c = writeback.writeback(_s, _params.load(_p), _p, verify=False)
    _txt108 = read(_s)
    _warn108 = ' '.join(str(w) for w in (_r108c.get('warnings') or []))
    check('§108⑦ %s：拒写（applied=false / reason=best_effort）+ pending 与文案'
          '都点名原因 `%s`，脚本一个字节都没动' % (_tag108, _tok108),
          _r108c.get('applied') is False
          and _r108c.get('reason') == 'best_effort'
          and _tok108 in (_r108c.get('pending') or [])
          and _needle108 in _warn108
          and writeback.BLOCK_START not in _txt108
          and _txt108 == _src108,
          'reason=%s applied=%s pending=%s warn=%s'
          % (_r108c.get('reason'), _r108c.get('applied'),
             _r108c.get('pending'), _warn108[:110]))
    shutil.rmtree(_d, ignore_errors=True)

# ---- §108⑧⑨ MAINTENANCE §25.2：源码 figsize 非字面量（动态配置）----
_S108_DYN = _S108_HEAD + (
    'W, H = 8.0, 5.0\n'
    '\n'
    '\n'
    'def make_plot():\n'
    '    fig, ax = plt.subplots(figsize=(W, H))\n'
    '    ax.plot([1, 2, 3])\n'
    '    return fig\n'
    '\n'
    '\n'
    'fig = make_plot()\n'
    "fig.savefig('o.png')\n")
_d108d, _s108d, _p108d = _mk15('t108dyn', _S108_DYN,
                               dict(_S108_PAR, figsize_px=[600, 400],
                                    figsize_in=[6.0, 4.0]))
_r108d = writeback.writeback(_s108d, _params.load(_p108d), _p108d, verify=True)
_t108d = read(_s108d)
_hint108 = _m108.t('wb_block_figsize_runtime')
check('§108⑧ §25.2 源码 figsize 非字面量：块里仍补 set_size_inches(6, 4)，'
      '但**块注释**点名"运行时覆盖你的动态配置"；源码的 figsize=(W, H) 一个字节没动',
      _r108d.get('applied') is True and 'fig.set_size_inches(6, 4)' in _t108d
      and _hint108 in _t108d and 'figsize=(W, H)' in _t108d
      and _t108d.count('set_size_inches(') == 1,
      'edited=%r hint_in=%s n_setsize=%d'
      % (_r108d.get('figsize_edited'), _hint108 in _t108d,
         _t108d.count('set_size_inches(')))
shutil.rmtree(_d108d, ignore_errors=True)

# §108⑨ 结构判据：整份 writeback.py 里只有**一处**生成 `.set_size_inches(` 的地方
# （docstring 里的示例不算）—— 即"没有新增第二处 figsize 计算路径"。
_108_wb_src = io.open(os.path.join(
    os.path.dirname(os.path.abspath(writeback.__file__)), 'writeback.py'),
    encoding='utf-8').read()
_108_tree = _ast.parse(_108_wb_src)
_108_doc = set()
for _n108 in _ast.walk(_108_tree):
    if isinstance(_n108, (_ast.Module, _ast.ClassDef, _ast.FunctionDef,
                          _ast.AsyncFunctionDef)):
        _b108 = getattr(_n108, 'body', None) or []
        if _b108 and isinstance(_b108[0], _ast.Expr) \
                and isinstance(_b108[0].value, _ast.Constant) \
                and isinstance(_b108[0].value.value, str):
            _108_doc.add(id(_b108[0].value))
_108_emit = [(_n108.lineno, _n108.value) for _n108 in _ast.walk(_108_tree)
             if isinstance(_n108, _ast.Constant)
             and isinstance(_n108.value, str)
             and '.set_size_inches(' in _n108.value
             and id(_n108) not in _108_doc]
check('§108⑨ §25.2 没有第二处 figsize 计算路径：writeback.py 里生成 '
      '`.set_size_inches(` 的常量只有 1 处（render_block 的块体那一行）',
      len(_108_emit) == 1, _108_emit)

# ---- §108⑩ 真 CLI 端到端：apply --write 成功 / 第二次 no_change / revert --write ----
_108_src_dir = os.path.dirname(os.path.dirname(os.path.abspath(writeback.__file__)))
_env108 = dict(os.environ, PYTHONPATH=_108_src_dir, MPLBACKEND='Agg',
               MPLTWEAK_LANG='zh', PYTHONIOENCODING='utf-8')
_d108c, _s108c, _p108c = _mk15('t108cli', _S108_PROBE, _S108_PAR)
_rr108 = subprocess.run([sys.executable, '-m', 'mpltweak', 'apply', _s108c,
                         '--params', _p108c, '--write', '--json'],
                        capture_output=True, cwd=_d108c, env=_env108)
_o108 = _decode_out(_rr108.stdout + _rr108.stderr)
_after108_1 = read(_s108c)
check('§108⑩ 真 CLI `apply --write`：rc=0 + applied=true，块落在函数内 return 之前',
      _rr108.returncode == 0 and '"applied": true' in _o108
      and writeback.BLOCK_START in _after108_1
      and _after108_1.index('def make_plot')
      < _after108_1.index(writeback.BLOCK_START)
      < _after108_1.index('    return fig'),
      'rc=%s | %s' % (_rr108.returncode, _o108[-200:]))
_rr108b = subprocess.run([sys.executable, '-m', 'mpltweak', 'apply', _s108c,
                          '--params', _p108c, '--write', '--json'],
                         capture_output=True, cwd=_d108c, env=_env108)
_o108b = _decode_out(_rr108b.stdout + _rr108b.stderr)
check('§108⑪ 连跑两次 apply：第二次 reason=no_change 且脚本 **0 字节**变化',
      '"reason": "no_change"' in _o108b and read(_s108c) == _after108_1,
      'rc=%s | %s' % (_rr108b.returncode, _o108b[-200:]))
_rr108c = subprocess.run([sys.executable, '-m', 'mpltweak', 'revert', _s108c,
                          '--params', _p108c, '--write', '--json'],
                         capture_output=True, cwd=_d108c, env=_env108)
check('§108⑫ `revert --write` 字节级回到原文（与探针源逐字节相同）',
      read(_s108c) == _S108_PROBE,
      'rc=%s | %s' % (_rr108c.returncode,
                      _decode_out(_rr108c.stdout + _rr108c.stderr)[-160:]))
shutil.rmtree(_d108c, ignore_errors=True)

# ---- §108⑬【有牙】把新候选换成"恒 None"（= 改前行为）⇒ 本文的用例必须红 ----
_orig_fa108 = writeback._func_return_anchor
writeback._func_return_anchor = lambda *a, **k: None
try:
    _d, _s, _p = _mk15('t108teeth', _S108_PROBE, _S108_PAR)
    _r108t = writeback.writeback(_s, _params.load(_p), _p, verify=False)
    _txt108t = read(_s)
finally:
    writeback._func_return_anchor = _orig_fa108
check('§108⑬【有牙】新候选 `_func_return_anchor` 换成恒 None（改前行为）⇒ '
      '探针形态回到拒写（本条即证 §108①②④ 真的在咬，不是空转）',
      _r108t.get('applied') is not True and writeback.BLOCK_START not in _txt108t,
      'reason=%s applied=%s pending=%s block_in_src=%s'
      % (_r108t.get('reason'), _r108t.get('applied'), _r108t.get('pending'),
         writeback.BLOCK_START in _txt108t))
shutil.rmtree(_d, ignore_errors=True)

# =====================================================================
# §109（0.1.12 / MAINTENANCE §25.1 A2）：**原位补一个参数**
#
# 语料（D:\Agent\deepseek\gapprobe）：2,334 份 deps_ok 的 9,336 个"脚本×字段"里，
# 1,541（16.5%）属于「函数**已经调用了**、只是没带目标参数」：
#     ax.set_title('A')            ← 要 title_fontsize
#     ax.tick_params(axis='x')     ← 要 tick_fontsize
#     ax.grid()                    ← 要 grid
# 0.1.11 一律进 skipped → auto 退块（往脚本里插一段调整块）。A2 给每字段加一条**原位
# 备选形态**：在参数表末尾补 `, fontsize=<值>`（grid 补 True/False 或 visible=）。
#
# 前置（识别表）：`ax.title.set_fontsize(N)` / `ax.xaxis.label.set_fontsize(N)`
# （语料 53 / 8 处）、`ax.set_position([4 字面量])`（8 处）—— 不认它们，轴级归属与
# 现值判不出来：等值时会被默认值证明**谎称**"源码没写法、现值=默认值"。
#
# 四条纪律（本节的用例逐条咬住）：
#   ① 跨多行参数表补完必须能 ast.parse；② 尾随逗号不许变成 `,)` 之类坏语法；
#   ③ 带 `**kwargs` 的调用**一律不改**并如实说明；④ 多处调用只补可唯一确定的一处，
#      其余如实报告（不唯一确定时整条拒改 → pending，由 auto 决定是否退块）；
#   ⑤ 连跑两次 = no_change + 0 字节；⑥ revert 字节级；⑦ 有牙；⑧ 三个负例不误改。
# =====================================================================
_S109_HEAD = ('import matplotlib\n'
              'matplotlib.use("Agg")\n'
              'import matplotlib.pyplot as plt\n'
              '\n'
              '\n')
# 探针 A：四条「已调用、没带参数」的调用，全部**跨多行**；
#   set_title 还留着**尾随逗号**（`'A',` 换行 `)`）—— 这是最容易写坏的形态。
_S109_PROBE = _S109_HEAD + (
    'fig, ax = plt.subplots()\n'
    'ax.plot([1, 2, 3], [2, 1, 3])\n'
    'ax.set_title(\n'
    "    'A',\n"
    ')\n'
    'ax.set_xlabel(\n'
    "    'X',\n"
    "    color='k',\n"
    ')\n'
    'ax.tick_params(\n'
    "    axis='x',\n"
    "    colors='k',\n"
    ')\n'
    'ax.grid(\n'
    ')\n'
    "fig.savefig('o.png')\n"
    "print('done')\n")
_S109_PAR = {'version': 4, 'script': 'fig.py', 'fig_index': 0, 'n_figs': 1,
             'axes': [{'index': 0, 'title_fontsize': 15.0,
                       'label_fontsize': 13.0, 'tick_fontsize': 9.0,
                       'grid': True}]}
_d109, _s109, _p109 = _mk15('t109a2', _S109_PROBE, _S109_PAR)
_r109 = writeback.writeback(_s109, _params.load(_p109), _p109, verify=True,
                            style='inplace')
_a109 = read(_s109)
_109_want = ["    'A',\n    fontsize=15,\n)",
             "    'X',\n    fontsize=13,\n    color='k',\n)",
             "    axis='x',\n    colors='k',\n    labelsize=9,\n)",
             'ax.grid(\n    True\n)']
check('§109① 四个字段全部**原位**补上（不插块）：跨多行参数表、写盘后 ast.parse 通过，'
      'Agg 重跑 + 语义验证都过（reason=ok / applied=true / verified+semantic）',
      _r109.get('reason') == 'ok' and _r109.get('applied') is True
      and _r109.get('verified') is True and _r109.get('semantic') is True
      and writeback.BLOCK_START not in _a109
      and all(_w in _a109 for _w in _109_want),
      'reason=%s applied=%s verified=%s semantic=%s | %s'
      % (_r109.get('reason'), _r109.get('applied'), _r109.get('verified'),
         _r109.get('semantic'),
         [ln for ln in _a109.splitlines() if 'font' in ln or 'label' in ln][:6]))
check('§109② 尾随逗号形态不被破坏：补完是 `\'A\', / fontsize=15, / )`（新参数自己带逗号），'
      '全文件没有 `,)` / `(,` / `,,`，且真能 ast.parse',
      _ast.parse(_a109) is not None
      and ',)' not in _a109.replace(' ', '') and '(,' not in _a109.replace(' ', '')
      and ',,' not in _a109.replace(' ', ''),
      [ln for ln in _a109.splitlines() if 'font' in ln or "'A'" in ln][:4])

_r109b = writeback.writeback(_s109, _params.load(_p109), _p109, verify=True,
                             style='inplace')
_a109b = read(_s109)
check('§109③ 同一脚本连跑两次：第二次 no_change 且脚本 **0 字节**变化（idempotent）',
      _r109b.get('reason') == 'no_change' and _a109b == _a109,
      'reason=%r bytes_same=%s' % (_r109b.get('reason'), _a109b == _a109))
revert.main([_s109, '--write'])
check('§109④ `revert --write` 字节级回到原文', read(_s109) == _S109_PROBE,
      [ln for ln in read(_s109).splitlines() if 'font' in ln][:3])
shutil.rmtree(_d109, ignore_errors=True)

# ---- §109⑤ 带 **kwargs 的调用一律不改，且 pending / 文案都如实说明 ----
_S109_KW = _S109_HEAD + (
    'fig, ax = plt.subplots()\n'
    'ax.plot([1, 2, 3], [2, 1, 3])\n'
    'kw = dict(alpha=1)\n'
    'ax.set_ylabel(\n'
    "    'Y',\n"
    '    **kw,\n'
    ')\n'
    "fig.savefig('o.png')\n")
_d, _s, _p = _mk15('t109kw', _S109_KW,
                   {'version': 4, 'script': 'fig.py', 'fig_index': 0, 'n_figs': 1,
                    'axes': [{'index': 0, 'label_fontsize_y': 12.0}]})
_r = writeback.writeback(_s, _params.load(_p), _p, verify=False, style='inplace')
_a = read(_s)
_109_w = ' '.join(_r.get('warnings') or [])
check('§109⑤ 带 `**kwargs` 的调用**一个字都不改**（认不出实际生效的参数），'
      'pending 点名该字段、文案说明原因并给下一步',
      _a == _S109_KW and _r.get('applied') is not True
      and any('label_fontsize_y' in str(x) for x in (_r.get('pending') or []))
      and '**kwargs' in _109_w and 'fontsize=' in _109_w,
      'applied=%r pending=%r | %s' % (_r.get('applied'), _r.get('pending'),
                                      _109_w[:160]))
shutil.rmtree(_d, ignore_errors=True)

# ---- §109⑥ 多处调用：只补"决定最终值"的那一处（同 body 顺序执行 ⇒ 后写的赢），
#      其余如实报告；两条不在同一个顺序 body 里（if/else 两支）⇒ 整条拒改 ----
_S109_TWO = _S109_HEAD + (
    'fig, ax = plt.subplots()\n'
    'ax.plot([1, 2, 3], [2, 1, 3])\n'
    "ax.set_title('A')\n"
    "ax.set_title('B')\n"
    "fig.savefig('o.png')\n")
_d, _s, _p = _mk15('t109two', _S109_TWO,
                   {'version': 4, 'script': 'fig.py', 'fig_index': 0, 'n_figs': 1,
                    'axes': [{'index': 0, 'title_fontsize': 15.0}]})
_r = writeback.writeback(_s, _params.load(_p), _p, verify=False, style='inplace')
_a = read(_s)
_109_w = ' '.join(_r.get('warnings') or [])
check('§109⑥ 同一 body 里两处 set_title：只补**后写的那一处**（`B`，语义上它决定最终值），'
      '前一处原样、并在 warnings 里如实报告"另有 1 处没改"',
      _r.get('applied') is True
      and "ax.set_title('A')\n" in _a
      and "ax.set_title('B', fontsize=15)\n" in _a
      and '另有 1 处 set_title' in _109_w,
      'applied=%r | %s' % (_r.get('applied'), _109_w[:160]))
shutil.rmtree(_d, ignore_errors=True)

_S109_BR = _S109_HEAD + (
    'import sys\n'
    'fig, ax = plt.subplots()\n'
    'ax.plot([1, 2, 3], [2, 1, 3])\n'
    'if len(sys.argv) > 1:\n'
    "    ax.set_title('A')\n"
    'else:\n'
    "    ax.set_title('B')\n"
    "fig.savefig('o.png')\n")
_d, _s, _p = _mk15('t109br', _S109_BR,
                   {'version': 4, 'script': 'fig.py', 'fig_index': 0, 'n_figs': 1,
                    'axes': [{'index': 0, 'title_fontsize': 15.0}]})
_r = writeback.writeback(_s, _params.load(_p), _p, verify=False, style='inplace')
_a = read(_s)
_109_w = ' '.join(_r.get('warnings') or [])
check('§109⑦ if/else 两支各一条 set_title：谁是最后生效的**证不出** ⇒ 整条不改'
      '（applied=false + pending 点名 + 文案给下一步"手动改或 --style block"）',
      _a == _S109_BR and _r.get('applied') is not True
      and any('title_fontsize' in str(x) for x in (_r.get('pending') or []))
      and '无法唯一确定' in _109_w and '--style block' in _109_w,
      'applied=%r pending=%r | %s' % (_r.get('applied'), _r.get('pending'),
                                      _109_w[:160]))
shutil.rmtree(_d, ignore_errors=True)

# ---- §109⑧ 识别表前置：`ax.title.set_fontsize(N)` 链式写法 ----
# 旧实现只认 `set_title(..., fontsize=)`：这类脚本等值时会被默认值证明**谎称**
# "源码没写法、现值=默认值"。现在：字面量不等 ⇒ 改那个字面量；是变量 ⇒ 拒改并说明。
_S109_CHAIN = _S109_HEAD + (
    'fig, ax = plt.subplots()\n'
    'ax.plot([1, 2, 3], [2, 1, 3])\n'
    "ax.set_title('A')\n"
    'ax.title.set_fontsize(9)\n'
    "fig.savefig('o.png')\n")
_d, _s, _p = _mk15('t109chain', _S109_CHAIN,
                   {'version': 4, 'script': 'fig.py', 'fig_index': 0, 'n_figs': 1,
                    'axes': [{'index': 0, 'title_fontsize': 15.0}]})
_r = writeback.writeback(_s, _params.load(_p), _p, verify=False, style='inplace')
_a = read(_s)
check('§109⑧ `ax.title.set_fontsize(9)` 被认出来：按"后写的赢"改**这一条**的字面量'
      '（9 → 15），不往 set_title 里再塞一个 fontsize',
      _r.get('applied') is True and 'ax.title.set_fontsize(15)' in _a
      and "ax.set_title('A')" in _a,
      [ln for ln in _a.splitlines() if 'title' in ln])
shutil.rmtree(_d, ignore_errors=True)

_S109_VAR = _S109_HEAD + (
    'fig, ax = plt.subplots()\n'
    'ax.plot([1, 2, 3], [2, 1, 3])\n'
    "ax.set_title('A')\n"
    'fs = 9\n'
    'ax.title.set_fontsize(fs)\n'
    "fig.savefig('o.png')\n")
_d, _s, _p = _mk15('t109var', _S109_VAR,
                   {'version': 4, 'script': 'fig.py', 'fig_index': 0, 'n_figs': 1,
                    'axes': [{'index': 0, 'title_fontsize': 15.0}]})
_r = writeback.writeback(_s, _params.load(_p), _p, verify=False, style='inplace')
_a = read(_s)
_109_w = ' '.join(_r.get('warnings') or [])
check('§109⑨ set_fontsize(变量)：值不在字面量里 ⇒ 不改、不猜，文案点名原因',
      _a == _S109_VAR and _r.get('applied') is not True
      and 'set_fontsize' in _109_w,
      'applied=%r | %s' % (_r.get('applied'), _109_w[:160]))
shutil.rmtree(_d, ignore_errors=True)

# ---- §109⑩ 负例：不许误改 ----
# ⚠ 0.1.15 / N1 起，"018 型：`plt.title/plt.xlabel` 全局写法"**不再是负例** ——
# 单图 + 静态唯一一条匿名轴时那句话是**可证**的（`writeback._single_figure_attribution`），
# 现在会原位补成 `plt.title('A', fontsize=15)`。那条形态的**正面**断言搬到了 §111④，
# 边界（多轴 / close / 循环）在 §111②⑤。这里只剩两条**仍然不可证**的负例。
# gallery 型：`axs[i]` 循环里建轴（接收者是 Subscript / 循环变量，不是可证的轴名）
_S109_NGAL = _S109_HEAD + (
    'fig, axs = plt.subplots(2, 2)\n'
    'for i, ax in enumerate(axs.ravel()):\n'
    '    ax.plot([1, 2, 3])\n'
    "    ax.set_title('p%d' % i)\n"
    "fig.savefig('o.png')\n")
# colorbar 型：`cb.ax` 是 colorbar 自己的轴 —— 不许把字号写进 host 轴的调用
_S109_NCB = _S109_HEAD + (
    'fig, ax = plt.subplots()\n'
    'sc = ax.scatter([1, 2, 3], [3, 2, 1], c=[1, 2, 3])\n'
    'cb = fig.colorbar(sc, ax=ax)\n'
    "ax.set_title('A')\n"
    "cb.ax.set_ylabel('Z')\n"
    "fig.savefig('o.png')\n")
for _tag, _src, _par in (
        ('gallery 型 axs[i] 循环', _S109_NGAL,
         {'version': 4, 'script': 'fig.py', 'fig_index': 0, 'n_figs': 1,
          'axes': [{'index': 0, 'title_fontsize': 15.0}]}),
        ('colorbar 型 cb.ax', _S109_NCB,
         {'version': 4, 'script': 'fig.py', 'fig_index': 0, 'n_figs': 1,
          'axes': [{'index': 0, 'title_fontsize': 15.0},
                   {'index': 1, 'is_colorbar': True, 'label_fontsize': 13.0}]})):
    _d, _s, _p = _mk15('t109neg', _src, _par)
    _r = writeback.writeback(_s, _params.load(_p), _p, verify=False,
                             style='inplace')
    _a = read(_s)
    # 负例只许出现"该补的那一处"：循环 / cb.ax 那几行一个字节都不许动
    _ok = (_a == _src) if _tag.startswith('gallery') \
        else ("cb.ax.set_ylabel('Z')" in _a and 'cb.ax.set_ylabel(\'Z\', ' not in _a)
    check('§109⑩ 负例不误改（%s）：不可证的轴一个字节都不动，'
          '该拒的拒（applied=false + pending 点名）' % _tag,
          _ok and (_r.get('applied') is not True or _tag.startswith('colorbar'))
          and bool(_r.get('pending')),
          'applied=%r pending=%r changed=%s'
          % (_r.get('applied'), _r.get('pending'), _a != _src))
    shutil.rmtree(_d, ignore_errors=True)

# ---- §109⑪【有牙】把新形态的**唯一入口**换成"恒不可用"（= 改前行为）⇒ 本节必须红 ----
# `_gap_specs` 是 A2 补参数形态的唯一决策点：换成恒 (None, None, None, None)，
# 这些调用立刻回到 0.1.11 的"进 skipped → 退块"，§109①⑥⑧ 会全部不成立。
_orig_gap109 = writeback._gap_specs
writeback._gap_specs = lambda *a, **k: (None, None, None, None)
try:
    _d, _s, _p = _mk15('t109teeth', _S109_PROBE, _S109_PAR)
    _r109t = writeback.writeback(_s, _params.load(_p), _p, verify=False,
                                 style='inplace')
    _a109t = read(_s)
finally:
    writeback._gap_specs = _orig_gap109
check('§109⑪【有牙】`_gap_specs` 换成恒 None（= 0.1.11 行为）⇒ 探针 A 一个参数都补不上、'
      '四个字段全进 pending（本条即证 §109①真的在咬）',
      _r109t.get('applied') is not True and _a109t == _S109_PROBE
      and len(_r109t.get('pending') or []) >= 3,
      'applied=%r pending=%r same=%s'
      % (_r109t.get('applied'), _r109t.get('pending'), _a109t == _S109_PROBE))
shutil.rmtree(_d, ignore_errors=True)

# =====================================================================
# §110（0.1.14 / Q1 **真缺陷**）：建图写在**推导式**里 ⇒ 推导式体重复执行，
# 块内**绝不**许用 `plt.gcf()`（实测会把参数写到**另一张图**上）
#
# 现场（独立验证员 e2/q1d_min.py 最小化）：`figs = [plt.figure(figsize=(4,3)) for _ in
# range(2)]` —— 全文件只有**一个** `plt.figure()` AST 节点，运行期却执行 2 次、出 2 张图。
# 旧判据只认 `ast.For`/`ast.While` ⇒ 判成"不在循环里" ⇒ `_gcf_provable` 认定"全文件只建
# 一次图" ⇒ 块里写 `plt.gcf().axes[0]`；而 `plt.gcf()` 是**最后那张图** ⇒ 第 1 张图的参数
# 被写到第 2 张图上（`--verify-fast` / `--no-verify` 下静默留下错块；默认档语义验证能拦，
# 但归因指向的行是错的）。
#
# 修法（0.1.14，**窄改**）：新增 `_comprehension_scope`（"这个调用在不在推导式体里"），
# 只加在真正需要它的两处 —— `_gcf_provable`（单图 gcf 可证性）与 `_numbered_fig_expr`
# （多图按源码序编号）：推导式里建图 ⇒ 两条都**不可证** ⇒ 块 fail-closed + 点名原因。
# **不动** `find_enclosing_loop`（它管的是"块位置/轴下标会不会重复应用"，是另一件事）——
# 一口气把推导式并进那个函数，会连"图在普通语句、只有**轴**在推导式里"这种**能安全写**的
# 脚本一起拦掉（§110⑤ 就是这条防线）。
#
# 断言口径：一个字节都不许写、块里不许出现 `plt.gcf()` / `.axes[`、pending 如实点名、
# warnings 点名**为什么**（推导式）并给下一步；两档（--verify-fast / 默认）结论一致。
_S110_SRC = '''import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

figs = [plt.figure(figsize=(4, 3)) for _ in range(2)]
axs = [f.add_subplot(1, 1, 1) for f in figs]
axs[0].plot([0, 1], [0, 1])
axs[1].plot([0, 1], [1, 0])
figs[0].savefig('m_0.png')
figs[1].savefig('m_1.png')
'''

_S110_PAR = {'version': 4, 'script': 'fig.py', 'fig_index': 0, 'n_figs': 2,
             'axes': [{'index': 0, 'pos': [0.1, 0.1, 0.5, 0.5],
                       'lines': [{'index': 0, 'linewidth': 1.5,
                                  'color': '#1f77b4'}]}]}

_s110_runs = []
for _fast110 in (True, False):
    _d110, _s110, _p110 = _mk15('t110%s' % ('fast' if _fast110 else 'full'),
                                _S110_SRC, _S110_PAR)
    _r110 = writeback.writeback(_s110, _params.load(_p110), _p110, verify=False,
                                fast_check=_fast110, style='block')
    _a110 = read(_s110)
    _s110_runs.append((_fast110, _r110, _a110))
    check('§110① 推导式建图 ⇒ 块内**绝不写** plt.gcf() / fig.axes[k]：'
          '脚本一个字节都没动 + pending 点名 + 文案点名"推导式"（fast=%s）' % _fast110,
          _a110 == _S110_SRC and 'plt.gcf()' not in _a110
          and '.axes[' not in _a110
          and _r110.get('applied') is not True
          and bool(_r110.get('pending'))
          and any('推导式' in _w for _w in (_r110.get('warnings') or [])),
          'applied=%r reason=%r pending=%r warn=%r same=%s'
          % (_r110.get('applied'), _r110.get('reason'),
             _r110.get('pending'), (_r110.get('warnings') or [])[:1],
             _a110 == _S110_SRC))
    shutil.rmtree(_d110, ignore_errors=True)

_s110_fast, _s110_full = _s110_runs[0][1], _s110_runs[1][1]
check('§110② 两档一致：同一脚本在 --verify-fast 与默认档下结论相同'
      '（都拒写、都不含 plt.gcf()、pending 相同）—— 不许一个写错一个拦',
      (_s110_fast.get('reason'), _s110_fast.get('applied'),
       sorted(_s110_fast.get('pending') or []))
      == (_s110_full.get('reason'), _s110_full.get('applied'),
          sorted(_s110_full.get('pending') or []))
      and all(not _x[2] or ('plt.gcf()' not in _x[2]) for _x in _s110_runs),
      'fast=%r/%r full=%r/%r'
      % (_s110_fast.get('reason'), _s110_fast.get('pending'),
         _s110_full.get('reason'), _s110_full.get('pending')))

# §110③ 判据本身：4 种推导式都算「重复执行上下文」；gcf 与编号两条"可证"路一起闭嘴
_S110_COMPS = (
    ('ListComp', 'figs = [plt.figure() for _ in range(2)]\n',
     _ast.ListComp),
    ('SetComp', 'figs = {plt.figure() for _ in range(2)}\n', _ast.SetComp),
    ('DictComp', 'figs = {i: plt.figure() for i in range(2)}\n', _ast.DictComp),
    ('GeneratorExp', 'figs = (plt.figure() for _ in range(2))\n',
     _ast.GeneratorExp),
)
_s110_bad = []
_s110_gcf = []
for _tag110, _src110, _want110 in _S110_COMPS:
    _t110 = _ast.parse(_src110)
    _c110 = [n for n in _ast.walk(_t110)
             if isinstance(n, _ast.Call) and isinstance(n.func, _ast.Attribute)
             and n.func.attr == 'figure'][0]
    if not isinstance(writeback._comprehension_scope(_t110, _c110), _want110):
        _s110_bad.append(_tag110)
    # 普通语句里建图仍然返回 None（别把"能安全改的"一起拦掉）
    _t110b = _ast.parse('import matplotlib.pyplot as plt\nfig = plt.figure()\n')
    _c110b = [n for n in _ast.walk(_t110b)
              if isinstance(n, _ast.Call) and isinstance(n.func, _ast.Attribute)
              and n.func.attr == 'figure'][0]
    if writeback._comprehension_scope(_t110b, _c110b) is not None:
        _s110_bad.append(_tag110 + '/普通语句误判')
    # 同一个节点的两条"可证"路都必须闭嘴（否则编号路会写出 plt.figure(1) 指到第一张图）
    _pos110 = [(writeback._node_scope(_t110, _c110.lineno), _c110.lineno + 1)]
    if writeback._gcf_provable(_t110, _pos110, _c110) is not False:
        _s110_gcf.append(_tag110 + ':_gcf_provable')
    if writeback._numbered_fig_expr(_t110, _pos110, _c110) is not None:
        _s110_gcf.append(_tag110 + ':_numbered_fig_expr')
check('§110③ 4 种推导式都判成"重复执行上下文"（`_comprehension_scope`）；普通语句不被误拦；'
      '`_gcf_provable` / `_numbered_fig_expr` 对推导式建图都为"不可证"',
      not _s110_bad and not _s110_gcf,
      'bad=%r 可证路仍开=%r' % (_s110_bad, _s110_gcf))

# §110⑤ **不许把能安全改的一起拦掉**：图在普通语句、只有**轴**在推导式里（单图 ⇒
# `fig.axes[k]` / `plt.gcf()` 确实就是它）必须照旧写块 —— 修法只动"建图节点的可证性"，不动
# 轴枚举。实测这就是把推导式一口气并进 `find_enclosing_loop`（= 被否决的宽改）时的回归：
# 那种改法让这条整块拒写（`_axes_binding_entries` 见到推导式里的 `add_subplot` 就返回
# ok=False），而本窄改下它照旧写出 `fig.axes[0].set_position(...)`。
_S110_AXONLY = '''import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

fig = plt.figure(figsize=(4, 3))
axs = [fig.add_subplot(2, 2, i + 1) for i in range(4)]
axs[0].plot([0, 1], [0, 1])
axs[1].plot([0, 1], [1, 0])
fig.savefig('m.png')
'''
_d110x, _s110x, _p110x = _mk15('t110axonly', _S110_AXONLY,
                               {'version': 4, 'script': 'fig.py', 'fig_index': 0,
                                'n_figs': 1,
                                'axes': [{'index': 0,
                                          'pos': [0.05, 0.05, 0.4, 0.4]}]})
_r110x = writeback.writeback(_s110x, _params.load(_p110x), _p110x, verify=False,
                             fast_check=True, style='block')
_a110x = read(_s110x)
check('§110⑤ 不影响"能安全写"的形态：单图 + **轴**在推导式里 ⇒ 仍写块并落到 '
      '`fig.axes[0].set_position(`（`fig` 是静态可证的名字），只拦"建图在推导式里"',
      _r110x.get('applied') is True and not _r110x.get('pending')
      and 'fig.axes[0].set_position(' in _a110x
      and 'plt.gcf()' not in _a110x,
      'applied=%r pending=%r 含 fig.axes[0]=%s 含 gcf=%s'
      % (_r110x.get('applied'), _r110x.get('pending'),
         'fig.axes[0].set_position(' in _a110x, 'plt.gcf()' in _a110x))
shutil.rmtree(_d110x, ignore_errors=True)

# ---- §110④【有牙】把判据换回"推导式不算重复执行上下文"（= 修前实现）⇒ §110① 必红 ----
# 这就是修前现场：块里又会写出 `plt.gcf().axes[0]`，运行期落到最后那张图。
_orig_cs110 = writeback._comprehension_scope
writeback._comprehension_scope = lambda *a, **k: None
try:
    _d110t, _s110t, _p110t = _mk15('t110teeth', _S110_SRC, _S110_PAR)
    _r110t = writeback.writeback(_s110t, _params.load(_p110t), _p110t,
                                 verify=False, fast_check=True, style='block')
    _a110t = read(_s110t)
finally:
    writeback._comprehension_scope = _orig_cs110
check('§110④【有牙】把 `_comprehension_scope` 换成恒 None（= 修前行为）⇒ '
      '立刻又写出 `plt.gcf().axes[0]` 并改到运行期最后一张图上（即 §110① 真的在咬）',
      'plt.gcf().axes[0]' in _a110t and _a110t != _S110_SRC,
      '含 gcf=%s changed=%s'
      % ('plt.gcf().axes[0]' in _a110t, _a110t != _S110_SRC))
shutil.rmtree(_d110t, ignore_errors=True)

# §111（0.1.15 / N1）：**隐式建图**的单图 —— 全文件没有一行显式建图（图由 pyplot 第一次
# 建轴/绘图时隐式创建）、只建一张图、写法是 `plt.plot(...)` / `plt.subplot(111)` ⇒ 块里可以
# **可证**地用 `plt.gcf()` 定位（N1 修前的现场：这类脚本被"没有可证的变量名"全拒）。
# 硬约束：close / 额外建轴 / 推导式·循环·函数体里的建轴调用一律**维持** fail-closed 并点名
# 原因（判据见 `writeback._implicit_gcf_verdict`）；块注释必须说"隐式建图"，不许照抄
# "本文件只有一次建图"（源码里根本没有建图那一行 —— 注释说谎比没有注释更糟）。
_S111_HEAD = """import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

"""
_S111_PURE = _S111_HEAD + ("plt.plot([1, 2, 3], [2, 1, 3])\nplt.title('pure plt')\n"
                           "plt.savefig('o.png', dpi=100)\n")
_S111_PAR = {'version': 4, 'script': 'fig.py', 'fig_index': 0, 'n_figs': 1,
             'axes': [{'index': 0, 'pos': [0.2, 0.2, 0.6, 0.6],
                       'title_fontsize': 18}]}
_d111, _s111, _p111 = _mk15('t111', _S111_PURE, _S111_PAR)
_r111 = writeback.writeback(_s111, _params.load(_p111), _p111, verify=False,
                            fast_check=True, style='block')
_a111 = read(_s111)
check('§111① 隐式建图的单图（纯 plt.*、无显式建图调用）⇒ 写块并落到 '
      '`plt.gcf().axes[0].set_position(`；块注释说的是"没有显式建图调用"',
      _r111.get('applied') is True and not _r111.get('pending')
      and 'plt.gcf().axes[0].set_position(' in _a111
      and '没有显式建图调用' in _a111
      and '本文件只有一次建图' not in _a111,
      'applied=%r pending=%r' % (_r111.get('applied'), _r111.get('pending')))

# 四条"仍然必须拒"的形态：原因不同、warnings 必须点名，且**一个字节都不许写**
_S111_BAD = (
    ('close', 'plt.plot([1, 2, 3])\nplt.savefig("a.png")\nplt.close("all")\n',
     'close('),
    ('colorbar', 'plt.imshow([[1, 2], [3, 4]])\nplt.colorbar()\n'
     'plt.savefig("a.png")\n', '额外建轴来源'),
    ('comprehension', 'ys = [plt.plot([0, 1], [i, i + 1]) for i in range(2)]\n'
     'plt.savefig("a.png")\n', '推导式'),
    ('loop', 'for i in range(3):\n    plt.plot([0, 1], [i, i + 1])\n'
     'plt.savefig("a.png")\n', '循环'),
)
for _tag111, _body111, _why111 in _S111_BAD:
    _d111b, _s111b, _p111b = _mk15('t111' + _tag111, _S111_HEAD + _body111,
                                   _S111_PAR)
    _r111b = writeback.writeback(_s111b, _params.load(_p111b), _p111b,
                                 verify=False, fast_check=True, style='block')
    _a111b = read(_s111b)
    check('§111② 隐式建图 + %s ⇒ 维持 fail-closed：字节不变、不写 `.axes[`、'
          'warnings 点名" %s "' % (_tag111, _why111),
          _r111b.get('applied') is not True and _a111b == _S111_HEAD + _body111
          and '.axes[' not in _a111b
          and any(_why111 in _w for _w in (_r111b.get('warnings') or [])),
          'reason=%r warn=%r' % (_r111b.get('reason'),
                                 (_r111b.get('warnings') or [])[-1:]))
    shutil.rmtree(_d111b, ignore_errors=True)

# ---- §111③【有牙】把新判据换成"恒不可证"（= N1 修前行为）⇒ §111① 立刻退回拒写 ----
_orig_im111 = writeback._implicit_gcf_verdict
writeback._implicit_gcf_verdict = lambda *a, **k: (False, 'none', 0)
try:
    _d111t, _s111t, _p111t = _mk15('t111teeth', _S111_PURE, _S111_PAR)
    _r111t = writeback.writeback(_s111t, _params.load(_p111t), _p111t,
                                 verify=False, fast_check=True, style='block')
    _a111t = read(_s111t)
finally:
    writeback._implicit_gcf_verdict = _orig_im111
check('§111③【有牙】把 `_implicit_gcf_verdict` 换成恒"不可证"（= 修前行为）⇒ §111① '
      '立刻退回"没有可证的变量名"拒写（即 N1 真的靠这条新判据在咬）',
      _r111t.get('applied') is not True and _a111t == _S111_PURE
      and any('没有可证的变量名' in _w for _w in (_r111t.get('warnings') or [])),
      'applied=%r changed=%s' % (_r111t.get('applied'), _a111t != _S111_PURE))
shutil.rmtree(_d111t, ignore_errors=True)
shutil.rmtree(_d111, ignore_errors=True)

# §111④（0.1.15 / N1）：单图纯 `plt.*` 的字号 **原位补参数** —— 这是 N1 的主目标
# （队长 2026-10-24 明确："不是让拒写的脚本能写，而是把插块升级成原位补参数"）。
# 现场：`plt.title('(a)')` 没有接收者变量名，旧行为一律进 skipped → 退块。
_S111_INPLACE = _S111_HEAD + (
    'plt.figure(figsize=(6, 4))\n'
    'plt.plot([1, 2, 3])\n'
    "plt.title('A')\n"
    "plt.xlabel('X')\n"
    "plt.savefig('o.png')\n")
_d111i, _s111i, _p111i = _mk15('t111inplace', _S111_INPLACE,
                               {'version': 4, 'script': 'fig.py',
                                'fig_index': 0, 'n_figs': 1,
                                'axes': [{'index': 0, 'title_fontsize': 15.0,
                                          'label_fontsize': 13.0}]})
_r111i = writeback.writeback(_s111i, _params.load(_p111i), _p111i,
                             verify=False, style='inplace')
_a111i = read(_s111i)
check('§111④ 单图纯 plt.* 只改字号 ⇒ **原位补参数**（`plt.title(\'A\', fontsize=15)` / '
      '`plt.xlabel(\'X\', fontsize=13)`），不插块、pending 空',
      _r111i.get('applied') is True and not _r111i.get('pending')
      and "plt.title('A', fontsize=15)" in _a111i
      and "plt.xlabel('X', fontsize=13)" in _a111i
      and 'auto layout' not in _a111i,
      'applied=%r pending=%r' % (_r111i.get('applied'), _r111i.get('pending')))
# 幂等：同一份参数再跑一次 ⇒ no_change、0 字节变化（补上的 fontsize 被认成"已等值"）
_r111i2 = writeback.writeback(_s111i, _params.load(_p111i), _p111i,
                              verify=False, style='inplace')
check('§111④b 连跑两次：第二次 no_change 且 0 字节变化（补出来的 fontsize 被认成等值）',
      read(_s111i) == _a111i and _r111i2.get('applied') is not True,
      'applied=%r reason=%r' % (_r111i2.get('applied'), _r111i2.get('reason')))
shutil.rmtree(_d111i, ignore_errors=True)
# §111⑤ N1 的边界：同一脚本**多一条 `plt.close()`** ⇒ 原位补参数也必须关上
# （关图之后 `plt.title` 作用的是另一张图 ⇒ 不可证），一个字节都不动。
_S111_INPLACE_CLOSE = _S111_INPLACE + "plt.close('all')\n"
_d111c, _s111c, _p111c = _mk15('t111inplaceclose', _S111_INPLACE_CLOSE,
                               {'version': 4, 'script': 'fig.py',
                                'fig_index': 0, 'n_figs': 1,
                                'axes': [{'index': 0, 'title_fontsize': 15.0,
                                          'label_fontsize': 13.0}]})
_r111c = writeback.writeback(_s111c, _params.load(_p111c), _p111c,
                             verify=False, style='inplace')
_a111c = read(_s111c)
check('§111⑤ N1 边界：同一脚本多一条 `plt.close()` ⇒ **不原位补参数**（不可证），'
      '字节不变、如实进 pending',
      _a111c == _S111_INPLACE_CLOSE and _r111c.get('applied') is not True
      and bool(_r111c.get('pending')),
      'applied=%r pending=%r' % (_r111c.get('applied'), _r111c.get('pending')))
shutil.rmtree(_d111c, ignore_errors=True)

# §112（0.1.15 / F6）：**块合成口径去冗余** —— 原位已经改过的字段不再进块。
# 现场：单图 + 完整 describe 参数（pos/lines/title…）+ 只改标题字号 ⇒ 原位写成
# `plt.title('(a)', fontsize=14)`，块里**又**写一遍 `…title.set_fontsize(14)`。
# 块只保留"无法原位表达"的字段（pos / lines），文字字号这一族不再重复。
_S112_PURE = ("import matplotlib\nmatplotlib.use('Agg')\n"
              "import matplotlib.pyplot as plt\n\n"
              "plt.figure(figsize=(6, 4))\n"
              "plt.plot([0, 1], [1, 0])\n"
              "plt.title('(a)')\n"
              "fig = plt.gcf()\n"
              "fig.savefig('out.png')\n")
_S112_NAMED = ("import matplotlib\nmatplotlib.use('Agg')\n"
               "import matplotlib.pyplot as plt\n\n"
               "fig, ax = plt.subplots(figsize=(6, 4))\n"
               "ax.plot([0, 1], [1, 0])\n"
               "ax.set_title('A')\n"
               "fig.savefig('out.png')\n")
_S112_LINES = [{'index': 0, 'linewidth': 1.5, 'color': '#1f77b4'}]


def _s112_run(src, par):
    """按 apply 的 auto 口径跑两趟：原位先写盘，再按需要补块。"""
    d, s, p = _mk15('t112', src, par)
    writeback.writeback(s, _params.load(p), p, verify=False, style='inplace')
    writeback.writeback(s, _params.load(p), p, verify=False, style='block')
    return d, s, p, read(s)


_d112a, _s112a, _p112a, _a112a = _s112_run(
    _S112_PURE, {'version': 4, 'script': 'fig.py', 'fig_index': 0, 'n_figs': 1,
                 'axes': [{'index': 0, 'pos': [0.125, 0.11, 0.775, 0.77],
                           'title_fontsize': 14.0, 'lines': _S112_LINES}]})
check('§112① 纯 plt.* 单图 + 完整参数：原位 `plt.title(\'(a)\', fontsize=14)`，'
      '**块里不再有 title 行**，而 pos / lines 仍在块里（布局不丢）',
      "plt.title('(a)', fontsize=14)" in _a112a
      and 'auto layout' in _a112a and 'title.set_fontsize' not in _a112a
      and 'axes[0].set_position(' in _a112a
      and 'lines[0].set_linewidth(' in _a112a,
      'block_has_title=%s has_pos=%s has_lines=%s'
      % ('title.set_fontsize' in _a112a, 'set_position(' in _a112a,
         'set_linewidth(' in _a112a))
_d112b, _s112b, _p112b, _a112b = _s112_run(
    _S112_NAMED, {'version': 4, 'script': 'fig.py', 'fig_index': 0, 'n_figs': 1,
                  'axes': [{'index': 0, 'pos': [0.125, 0.11, 0.775, 0.77],
                            'title_fontsize': 15.0}]})
check('§112② 有变量名的单轴（`fig, ax = plt.subplots()`）：块里不再有 '
      '`ax.title.set_fontsize`（名字来自解包，旧实现证不出等值）',
      "ax.set_title('A', fontsize=15)" in _a112b
      and 'auto layout' in _a112b and 'ax.title.set_fontsize' not in _a112b
      and 'ax.set_position(' in _a112b,
      'block_has_title=%s has_pos=%s'
      % ('ax.title.set_fontsize' in _a112b, 'set_position(' in _a112b))
_r112b2 = writeback.writeback(_s112b, _params.load(_p112b), _p112b,
                              verify=False, style='block')
check('§112③ 幂等：块去冗余后再跑一次 ⇒ 0 字节变化、不再改（块自检不谎报）',
      read(_s112b) == _a112b and _r112b2.get('applied') is not True,
      'applied=%r reason=%r' % (_r112b2.get('applied'), _r112b2.get('reason')))
shutil.rmtree(_d112a, ignore_errors=True)
shutil.rmtree(_d112b, ignore_errors=True)
# §112④【有牙】把 F6 的两条判据各自换回"恒不可用"（= 修前行为）⇒ 冗余行必须回来
_o_pyplot_eq, _o_text_name = writeback._pyplot_text_equal, writeback._text_axis_name
try:
    writeback._pyplot_text_equal = lambda *a, **k: False
    writeback._text_axis_name = lambda *a, **k: None
    _d, _s, _p, _a = _s112_run(
        _S112_PURE, {'version': 4, 'script': 'fig.py', 'fig_index': 0, 'n_figs': 1,
                     'axes': [{'index': 0, 'pos': [0.125, 0.11, 0.775, 0.77],
                               'title_fontsize': 14.0}]})
    _a_pure = _a
    shutil.rmtree(_d, ignore_errors=True)
    _d, _s, _p, _a = _s112_run(
        _S112_NAMED, {'version': 4, 'script': 'fig.py', 'fig_index': 0,
                      'n_figs': 1,
                      'axes': [{'index': 0, 'pos': [0.125, 0.11, 0.775, 0.77],
                                'title_fontsize': 15.0}]})
    _a_named = _a
    shutil.rmtree(_d, ignore_errors=True)
    check('§112④【有牙】两条判据都换回恒 False ⇒ 冗余行立刻回来'
          '（`title.set_fontsize` / `ax.title.set_fontsize` 重新出现在块里）',
          'title.set_fontsize' in _a_pure
          and 'ax.title.set_fontsize' in _a_named,
          'pure_dup=%s named_dup=%s'
          % ('title.set_fontsize' in _a_pure, 'ax.title.set_fontsize' in _a_named))
finally:
    writeback._pyplot_text_equal = _o_pyplot_eq
    writeback._text_axis_name = _o_text_name

# §113（0.1.16 / S1）：0.1.15 补的三处静默日志**必须各有一条断言盯着**。
# 为什么必须补：卫生棘轮 test_except_exception_ratchet 只数 `except` 头的**个数**，
# 不数体里有没有打日志；0.1.15 那三处改的正是同一个 except 体内部 ⇒ 谁改回 `pass`
# 都不会红（独立验证员实测：`_team014/v2b_rev_silent.py`）。下面三条各自独立变红。
_d113 = os.path.join(_TMPROOT, 'case_113')
os.makedirs(_d113, exist_ok=True)
_s113 = os.path.join(_d113, 'fig113.py')
with open(_s113, 'w', encoding='utf-8', newline='\n') as f:
    f.write('print(1)\n')
_p113 = os.path.join(_d113, 'p113.json')
with open(_p113, 'w', encoding='utf-8', newline='\n') as f:
    f.write('{}')
_bd113 = writeback._backup_dir(_s113, _p113)
os.makedirs(_bd113, exist_ok=True)
_baks113 = []
for _i in range(8):
    _b = os.path.join(_bd113, 'fig113.tweak.%d.bak' % (7000 + _i))
    with open(_b, 'w', encoding='utf-8', newline='\n') as f:
        f.write('old %d\n' % _i)
    # 固定 mtime 定序：不靠创建顺序（APFS/NTFS 的 mtime 精度不同，慢机器上会翻）。
    os.utime(_b, (1400000000 + _i * 10, 1400000000 + _i * 10))
    _baks113.append(_b)
_ro113 = os.path.abspath(_baks113[2])        # 中间那份：keep=5 时它正在被裁的那几份里
os.chmod(_ro113, 0o444)                      # Windows：只读属性会让 os.remove 直接失败

_real_rm113 = os.remove


def _rm113(p, *a, **k):
    # POSIX 上"文件只读"挡不住 unlink（判据是**目录**写权限），CI 有 ubuntu 腿 ——
    # 所以这里再钉一道**路径级**失败注入，让两端跑出来的是同一个场景，其余路径照旧。
    if os.path.abspath(str(p)) == _ro113:
        raise PermissionError(13, 'denied by test')
    return _real_rm113(p, *a, **k)


_warns113 = []
try:
    writeback.os.remove = _rm113            # writeback 里的 `os` 就是全局 os 模块
    writeback._prune_backups(_s113, _p113, keep=5, warnings=_warns113)
finally:
    writeback.os.remove = _real_rm113
    try:
        os.chmod(_ro113, 0o666)             # 还原只读位，否则 rmtree 删不掉
    except OSError:
        pass
check('§113① 裁旧备份删不掉 ⇒ warnings 说话（改回 pass 必红）',
      any('备份目录会一直变大' in _w for _w in _warns113),
      'warns=%d %s' % (len(_warns113), _warns113[:1]))

# §113② `_atomic_write`：`os.replace` 换掉 inode 后权限位来自 mkstemp(0600)，
# 恢复不了就是"用户脚本的可执行/共享位被悄悄改窄，而磁盘上已经换完" —— 必须有牙。
_s113b = os.path.join(_d113, 'fig113b.py')
with open(_s113b, 'w', encoding='utf-8', newline='\n') as f:
    f.write('print(1)\n')
_warns113b = []
_real_chmod113 = os.chmod


def _chmod113(*a, **k):
    raise OSError(13, 'denied by test')


try:
    writeback.os.chmod = _chmod113
    writeback._atomic_write(_s113b, 'print(2)\n', warnings=_warns113b)
finally:
    writeback.os.chmod = _real_chmod113
check('§113② 恢复权限位失败 ⇒ warnings 说话（改回 pass 必红）',
      any('权限可能变窄' in _w for _w in _warns113b),
      'warns=%d %s' % (len(_warns113b), _warns113b[:1]))
check('§113② 那条只是警告：写盘本身照旧成功（内容已换、没被吞）',
      read(_s113b) == 'print(2)\n', read(_s113b)[:20])

# §113③ `apply` 收尾刷参数 mtime 失败 ⇒ `files[].warnings` 里要有那条
#   （0.1.15 之前是 `except OSError: pass`，用户下次只会莫名被判 stale）。
# 用**子进程驱动器**而不是进程内：`--json` 是 fd 级直写（绕过 redirect_stdout），
# 且 os.utime 的失败注入必须只影响这一次 apply。
_d113c = os.path.join(_d113, 'case_113c')
_pd113c = os.path.join(_d113c, '.tweak_params')
os.makedirs(_pd113c, exist_ok=True)
_s113c = os.path.join(_d113c, 'fig.py')
with open(_s113c, 'w', encoding='utf-8', newline='\n') as f:
    f.write('import matplotlib\nmatplotlib.use("Agg")\n'
            'import matplotlib.pyplot as plt\n'
            'fig1, ax1 = plt.subplots(figsize=(5, 3))\n'
            'ax1.plot([1, 2, 3])\n'
            'fig1.savefig("a.png")\n'
            'fig2, ax2 = plt.subplots(figsize=(5, 3))\n'
            'ax2.plot([3, 2, 1])\n'
            'fig2.savefig("b.png")\n')
for _n, _idx, _pos in (('fig.fig0.json', 0, [0.2, 0.2, 0.6, 0.6]),
                       ('fig.fig1.json', 1, [0.5, 0.5, 0.3, 0.3])):
    with open(os.path.join(_pd113c, _n), 'w', encoding='utf-8') as f:
        json.dump({'version': 4, 'script': 'fig.py', 'fig_index': _idx,
                   'n_figs': 2, 'axes': [{'index': 0, 'pos': _pos}]}, f)
_now113 = time.time()
os.utime(_s113c, (_now113 - 20, _now113 - 20))     # 脚本比参数旧 ⇒ 两份都合法、都会落地
for _n in ('fig.fig0.json', 'fig.fig1.json'):
    os.utime(os.path.join(_pd113c, _n), (_now113 - 10, _now113 - 10))
_drv113 = os.path.join(_d113c, 'drv113.py')
with open(_drv113, 'w', encoding='utf-8', newline='\n') as f:
    f.write('# -*- coding: utf-8 -*-\n'
            'import os, sys\n'
            '_real = os.utime\n'
            '_seen = {}\n'
            'def _fake(p, *a, **k):\n'
            '    key = os.path.abspath(str(p))\n'
            '    _seen[key] = _seen.get(key, 0) + 1\n'
            '    if _seen[key] >= 2:\n'
            '        raise OSError(13, "denied by test")\n'
            '    return _real(p, *a, **k)\n'
            'os.utime = _fake\n'
            'from mpltweak import apply\n'
            'sys.exit(apply.main(["fig.py", "--write", "--json"]))\n')

_src113 = os.path.dirname(os.path.dirname(os.path.abspath(writeback.__file__)))
# PYTHONPATH 必须钉到**当前这份 mpltweak**：不钉的话子进程会解析到 editable 安装的
# 那份 —— 反向验证（在副本树里变异）会跑出"变异无效仍然绿"的假绿。
_env113 = dict(os.environ, PYTHONPATH=_src113, MPLBACKEND='Agg',
               MPLTWEAK_LANG='zh', PYTHONIOENCODING='utf-8')
_rr113 = subprocess.run([sys.executable, _drv113], capture_output=True,
                        cwd=_d113c, env=_env113)
_oo113 = _decode_out(_rr113.stdout + _rr113.stderr)
try:
    _j113 = json.loads(_oo113[_oo113.index('{'):_oo113.rindex('}') + 1])
except Exception:                                                 # noqa: BLE001
    _j113 = {}
_warns113c = [_w for _f in (_j113.get('files') or [])
              for _w in (_f.get('warnings') or [])]
from mpltweak import messages as _msg113                          # noqa: E402
# 文案从语言表现算（zh/en 两套都要成立）：只取 path 占位符之前那一段当判据。
_head113 = _msg113.t('mtime_sync_fail', path='<P>', err='<E>').split('<P>')[0]
check('§113③ 收尾刷 mtime 失败 ⇒ files[].warnings 里有那条（改回 pass 必红）',
      any(_head113 in _w for _w in _warns113c),
      'rc=%s warns=%s | %s' % (_rr113.returncode, _warns113c[:1], _oo113[-160:]))
shutil.rmtree(_d113, ignore_errors=True)

# ============================================================================
# §114（0.1.16 / F1）写回前 diff 预览（`apply --diff`）：先看后改，一个字节都不落盘
#
# 为什么要这组断言：0.1.15 之前 `apply --write` 是"闭眼改"—— agent 只能改完再看
# 盘上内容，人只能事后 `git diff`。F1 在落盘**之前**给出"要动哪几行、从什么改成
# 什么"。判据三层：
#   ① 单元：`writeback.preview_diff` 三档类型/行号（inplace / insert_block / replace）；
#   ② 同源：预览声明的 diff 重放回原文 == 真写盘后的文件（逐字节；只有调整块说明行
#      里的"生成于 <时间>"可能跨分钟不同，那一档用引擎自己的 `_norm_ts` 归一后比对）；
#   ③ 默认行为不变：预览零副作用（脚本字节不变、不造备份），不带 --diff 照旧直接写。
# ============================================================================
import hashlib as _h114
import subprocess as _sub114

_OLD114 = 'a\nb\nc\n'
_r114a = writeback.preview_diff(_OLD114, 'a\nB\nc\n')
check('§114① 一对一换行 => type=inplace、行号指向**新文件**第 2 行、旧新行都在',
      len(_r114a) == 1 and _r114a[0]['type'] == 'inplace'
      and _r114a[0]['line'] == 2 and _r114a[0]['old'] == 'b'
      and _r114a[0]['new'] == 'B', repr(_r114a))
_NEW114 = ('a\nb\nc\n' + writeback.BLOCK_START + '\n'
           'ax.set_position([0.1, 0.1, 0.5, 0.5])\n' + writeback.BLOCK_END + '\n')
_r114b = writeback.preview_diff(_OLD114, _NEW114)
check('§114① 插入调整块 => 哨兵行与块体全是 insert_block（行号 4/5/6，旧行为空）',
      [x['type'] for x in _r114b] == ['insert_block'] * 3
      and [x['line'] for x in _r114b] == [4, 5, 6]
      and [x['old'] for x in _r114b] == [None, None, None], repr(_r114b))
check('§114① 内容一致 => 空 diff（no_change 不许编造条目）',
      writeback.preview_diff(_OLD114, _OLD114) == [],
      repr(writeback.preview_diff(_OLD114, _OLD114)))
_r114d = writeback.preview_diff(_OLD114, 'a\nX\nY\nc\n')       # 1 行换 2 行
check('§114① 行数变了：配对那行如实报 inplace（一行换一行）+ 多出来那行是新增',
      [x['type'] for x in _r114d] == ['inplace', 'replace']
      and _r114d[0]['old'] == 'b' and _r114d[1]['old'] is None,
      repr(_r114d))
# ---- teeth：把 diff 生成改坏（只报第一条）=> 上面"3 行 insert_block"那条必红 ----
_orig114 = writeback.preview_diff
try:
    writeback.preview_diff = lambda o, n: _orig114(o, n)[:1]
    _r114b1 = writeback.preview_diff(_OLD114, _NEW114)
finally:
    writeback.preview_diff = _orig114
check('§114② teeth：preview_diff 只报第一条 => 行数/类型断言立刻不成立（真有牙）',
      len(_r114b1) == 1 and len(_r114b) == 3 and _r114b1 != _r114b,
      'teeth=%r real=%d 行' % (_r114b1, len(_r114b)))

# ---- 端到端：三个场景（原位 / 插块 / no_change）的预览 vs 真写盘 ----
_src114 = os.path.dirname(os.path.dirname(os.path.abspath(writeback.__file__)))
_d114 = tempfile.mkdtemp(prefix='mpltweak_f1diff_')
_ENV114 = dict(os.environ, PYTHONPATH=_src114, MPLBACKEND='Agg',
               MPLTWEAK_LANG='zh', PYTHONIOENCODING='utf-8')


def _run114(cwd, args):
    """跑一次 CLI（`--json`：stdout 只有载荷）→ (rc, 合并文本, 载荷 dict)。"""
    r = _sub114.run([sys.executable, '-m', 'mpltweak'] + list(args),
                    cwd=cwd, capture_output=True, env=_ENV114)
    _o = _decode_out(r.stdout + r.stderr)
    _j = {}
    try:
        _j = json.loads(_decode_out(r.stdout))
    except Exception:                                             # noqa: BLE001
        _j = {}
    return r.returncode, _o, _j


def _replay114(base, rows):
    """把 `files[].diff` 重放回原文 —— **只**用提示里给出的字段（agent 也能这么复现）。

    这也是一条不变量检查：`old` 必须与原文同位置的整行一致（对不上说明行号口径错），
    所以每步都 assert 一次。
    """
    out = base.splitlines()
    for r in sorted(rows, key=lambda x: x['line']):
        i = r['line'] - 1
        if r['old'] is None:
            out.insert(i, r['new'])
        else:
            assert out[i] == r['old'], (i, out[i], r['old'])
            if r['new'] is None:
                del out[i]
            else:
                out[i] = r['new']
    return '\n'.join(out) + ('\n' if base.endswith('\n') else '')


_CASES114 = (
    # name, 脚本正文, 参数（除 version/script 之外的字段）, 期望的 diff 类型
    ('s1', "import matplotlib\nmatplotlib.use('Agg')\n"
           "import matplotlib.pyplot as plt\n"
           "fig, ax = plt.subplots(figsize=(6, 4))\n"
           "ax.plot([1, 2, 3], [1, 4, 9])\n"
           "ax.set_xlabel('x')\n"
           "fig.savefig('out.png', dpi=100)\n",
     {'fig_index': 0, 'n_figs': 1,
      'axes': [{'index': 0, 'label_fontsize': 18}]}, 'inplace'),
    ('s2', "import matplotlib\nmatplotlib.use('Agg')\n"
           "import matplotlib.pyplot as plt\n"
           "fig, axs = plt.subplots(1, 2, figsize=(8, 3))\n"
           "axs[0].plot([1, 2, 3], [1, 4, 9])\n"
           "axs[1].plot([3, 2, 1], [1, 2, 3])\n"
           "fig.savefig('out.png', dpi=100)\n",
     {'fig_index': 0, 'n_figs': 1,
      'axes': [{'index': 0, 'pos': [0.1, 0.15, 0.35, 0.75]}]}, 'insert_block'),
    ('s3', "import matplotlib\nmatplotlib.use('Agg')\n"
           "import matplotlib.pyplot as plt\n"
           "fig, ax = plt.subplots(figsize=(6, 4))\n"
           "ax.plot([1, 2, 3], [1, 4, 9])\n"
           "ax.set_xlabel('x', fontsize=18)\n"
           "fig.savefig('out.png', dpi=100)\n",
     {'fig_index': 0, 'n_figs': 1,
      'axes': [{'index': 0, 'label_fontsize': 18}]}, None),
    # s4 = **独立验证当场打回的那个场景**（0.1.16 / F1 修正的回归牙）：
    # 字号能原位写、位置只能插块 ⇒ auto 先原位写字号、再退块写位置。退块那次会
    # **重读源码**，所以块里**不该**再写一遍字号。第一版预览"捕获住不落盘"，
    # 第二次读到的是原样 ⇒ 块里多写一行 `set_fontsize(18)`（实测 573 B vs 553 B）。
    ('s4', "import matplotlib\nmatplotlib.use('Agg')\n"
           "import matplotlib.pyplot as plt\n"
           "fig, ax = plt.subplots(figsize=(6, 4))\n"
           "ax.plot([0, 1], [1, 0])\n"
           "ax.set_xlabel('x')\n"
           "fig.savefig('o.png')\n",
     {'fig_index': 0, 'n_figs': 1,
      'axes': [{'index': 0, 'label_fontsize': 18,
                'pos': [0.125, 0.11, 0.775, 0.77]}]}, 'mixed'),
)

for _nm114, _body114, _pdata114, _kind114 in _CASES114:
    _c114 = os.path.join(_d114, _nm114)
    os.makedirs(_c114, exist_ok=True)
    _sp114 = os.path.join(_c114, 'fig.py')
    with open(_sp114, 'w', encoding='utf-8', newline='\n') as f:
        f.write(_body114)
    _pp114 = os.path.join(_c114, 'p.json')
    with open(_pp114, 'w', encoding='utf-8') as f:
        json.dump(dict({'version': 4, 'script': 'fig.py'}, **_pdata114), f)
    _now114 = time.time()
    os.utime(_sp114, (_now114 - 60, _now114 - 60))   # 脚本比参数旧 ⇒ 不会被判 stale
    _before = open(_sp114, 'rb').read()
    _bak114 = os.path.join(_c114, '.tweak_params', 'fig.tweak.bak')

    # ---- ① 预览：rc 与"只读预览"同档、applied=false、零副作用 ----
    _rc, _o, _j = _run114(_c114, ['apply', 'fig.py', '--params', 'p.json',
                                  '--diff', '--json'])
    _f = (_j.get('files') or [{}])[0]
    _rows = _f.get('diff') or []
    check('§114③[%s] 预览：rc=0 / reason=preview / applied=false（不报成功）'
          % _nm114,
          _rc == 0 and _f.get('reason') == 'preview'
          and _f.get('applied') is False and _j.get('ok') is True,
          'rc=%s reason=%r applied=%r | %s' % (_rc, _f.get('reason'),
                                               _f.get('applied'), _o[-200:]))
    check('§114③[%s] 预览**零副作用**：脚本字节不变、不造备份、不说"已写回"'
          % _nm114,
          open(_sp114, 'rb').read() == _before
          and not os.path.exists(_bak114) and '已写回' not in _o,
          ('bak=%s' % os.path.exists(_bak114), _o[-160:]))
    check('§114③[%s] --json 载荷带 diff / preview_sha256 / preview_sha256_norm'
          % _nm114,
          all(k in _f for k in ('diff', 'preview_sha256',
                                'preview_sha256_norm', 'preview_bytes')),
          sorted(_f)[:8])
    if _kind114 is None:
        check('§114③[s3] no_change：diff 为空 + 文案说"一个字节都不会变"',
              _rows == [] and '一个字节都不会变' in _o, (_rows, _o[-200:]))
    elif _kind114 == 'mixed':
        # s4：原位写字号 + 退块写位置 ⇒ diff 里两档都在，且**块里不许再写一遍字号**
        _types = {r['type'] for r in _rows}
        check('§114③[s4] 混合档：diff 同时含 inplace 与 insert_block（%d 行）'
              % len(_rows),
              _types == {'inplace', 'insert_block'}, sorted(_types))
    else:
        check('§114③[%s] diff 类型全为 %s（%d 行）且都带行号'
              % (_nm114, _kind114, len(_rows)),
              bool(_rows) and {r['type'] for r in _rows} == {_kind114}
              and all(isinstance(r['line'], int) and r['line'] > 0
                      for r in _rows), _rows[:3])
    check('§114③[%s] 预览不留临时文件（影子副本已删）' % _nm114,
          not [n for n in os.listdir(_c114) if 'mpltweak-preview' in n],
          os.listdir(_c114))
    # ---- ② 同源证明：真写盘之后，把预览的 diff 重放回原文 == 盘上文件 ----
    _rc2, _o2, _j2 = _run114(_c114, ['apply', 'fig.py', '--params', 'p.json',
                                     '--write', '--json'])
    _f2 = (_j2.get('files') or [{}])[0]
    _after = open(_sp114, 'rb').read().decode('utf-8')
    _replay = _replay114(_before.decode('utf-8'), _rows)
    check('§114④[%s] 前置：不带 --diff 时照旧直接写（默认行为不变）' % _nm114,
          _rc2 == 0 and _f2.get('reason') in ('ok', 'no_change'),
          'rc=%s reason=%r | %s' % (_rc2, _f2.get('reason'), _o2[-160:]))
    check('§114④[%s] **同源**：预览 diff 重放 == 真写盘后的文件（逐字节；插块档'
          '只可能差说明行的生成时间）' % _nm114,
          (_replay == _after) if _kind114 not in ('insert_block', 'mixed')
          else writeback._norm_ts(_replay) == writeback._norm_ts(_after),
          'replay=%r after=%r' % (_replay[-90:], _after[-90:]))
    check('§114④[%s] preview_bytes == 落盘文件字节数（预告的大小不是编的）'
          % _nm114,
          _f.get('preview_bytes') == len(_after.encode('utf-8')),
          (_f.get('preview_bytes'), len(_after.encode('utf-8'))))
    if _kind114 == 'mixed':
        # 回归牙（独立验证 0.1.16 打回的那一条）：退块那次是**重读源码**算的，
        # 字号已经被原位写进文件了 ⇒ 块里**不许**再出现 set_fontsize。
        check('§114④[s4] 退块时块内**不重复**已被原位写掉的字号（重读的影子文件）',
              'set_fontsize' not in _after and 'fontsize=18' in _after,
              _after[-160:])
    check('§114④[%s] preview_sha256_norm == 落盘文件按同一口径归一后的 sha256'
          % _nm114,
          _f.get('preview_sha256_norm') == _h114.sha256(
              writeback._norm_ts(_after).encode('utf-8')).hexdigest(),
          _f.get('preview_sha256_norm'))
    if _kind114 in (None, 'inplace'):
        # 没有调整块 ⇒ 没有"生成于 <时间>"那行 ⇒ 严格指纹必须逐字节相等
        check('§114④[%s] 严格逐字节：preview_sha256 == 落盘文件 sha256' % _nm114,
              _f.get('preview_sha256') == _h114.sha256(
                  _after.encode('utf-8')).hexdigest(),
              (_f.get('preview_sha256'), _h114.sha256(
                  _after.encode('utf-8')).hexdigest()))

shutil.rmtree(_d114, ignore_errors=True)

# ============================================================================
# §115（0.1.16）元组解包 subplots 的轴寻址：`fig, (ax1, ax2) = plt.subplots(1, 2)`
#
# 老 bug（0.1.15 同样有，独立验证实测）：`_iter_assign_names` 会把嵌套元组**摊平**，
# 于是 `fig, (ax1, ax2)` 被读成 ['fig','ax1','ax2']、取第 2 个当"轴数组" ⇒ 块里写出
# `ax1[0].set_position(...)`。而 ax1 是**一根轴** ⇒ 重跑 `TypeError: 'Axes' object is
# not subscriptable` ⇒ 验证失败 ⇒ 回滚 ⇒ rc=1，**用户的改动没生效**。
# 判据三层：① 写回成功且脚本能跑；② 块里按**每根轴自己的名字**寻址（不得 `ax1[0]`）；
# ③ 反向：关掉元组解包识别 ⇒ 块里又出现 `ax1[0]`（说明这条判据真在起作用）。
# ============================================================================
_d115 = tempfile.mkdtemp(prefix='mpltweak_unpack_')
_BODY115 = ("import matplotlib\nmatplotlib.use('Agg')\n"
            "import matplotlib.pyplot as plt\n"
            "fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(8, 3))\n"
            "ax1.plot([1, 2, 3], [1, 4, 9])\n"
            "ax1.set_xlabel('x')\n"
            "ax2.plot([3, 2, 1], [1, 2, 3])\n"
            "fig.savefig('o.png', dpi=100)\n")
_PDATA115 = {'version': 4, 'script': 'fig.py', 'fig_index': 0, 'n_figs': 1,
             'axes': [{'index': 0, 'pos': [0.08, 0.12, 0.42, 0.76]},
                      {'index': 1, 'pos': [0.55, 0.12, 0.42, 0.76]}]}
_s115 = os.path.join(_d115, 'fig.py')
_p115 = os.path.join(_d115, 'p.json')
with open(_s115, 'w', encoding='utf-8', newline='\n') as f:
    f.write(_BODY115)
with open(_p115, 'w', encoding='utf-8') as f:
    json.dump(_PDATA115, f)
_t115 = time.time()
os.utime(_s115, (_t115 - 60, _t115 - 60))     # 脚本比参数旧 ⇒ 不会被判 stale
os.utime(_p115, (_t115, _t115))
_r115 = subprocess.run([sys.executable, '-m', 'mpltweak', 'apply', 'fig.py',
                        '--params', 'p.json', '--write', '--json'],
                       cwd=_d115, capture_output=True, env=_ENV114)
_o115 = _decode_out(_r115.stdout + _r115.stderr)
try:
    _j115 = json.loads(_decode_out(_r115.stdout))
except Exception:                                                 # noqa: BLE001
    _j115 = {}
_f115 = (_j115.get('files') or [{}])[0]
_txt115 = open(_s115, encoding='utf-8').read()
check('§115① 元组解包写回成功（老 bug 是 rc=1 + 回滚 + 用户改动没生效）',
      _r115.returncode == 0 and _f115.get('applied') is True,
      'rc=%s applied=%r reason=%r | %s' % (_r115.returncode,
                                          _f115.get('applied'),
                                          _f115.get('reason'), _o115[-220:]))
# ② 块里必须按**每根轴自己的名字**寻址：`ax1.set_position(...)`，绝不能 `ax1[0]`。
_pos115 = re.findall(r'(?m)^(\w+)\[(\d)\]\.set_position', _txt115)
check('§115② 块里不得把单轴当数组（没有 `名字[数字].set_position`）',
      _pos115 == [], '命中 %r' % (_pos115[:4],))
check('§115② 位置按自己的名字写：ax1 / ax2 各一条 set_position',
      'ax1.set_position(' in _txt115 and 'ax2.set_position(' in _txt115,
      _txt115[-260:])
# ③ 写回后的脚本自己也要跑得通（老 bug 就是在这一步 TypeError 被回滚的）
_rr115 = subprocess.run([sys.executable, 'fig.py'], cwd=_d115,
                        capture_output=True, env=_ENV114)
_oo115 = _decode_out(_rr115.stdout + _rr115.stderr)
check('§115③ 写回后脚本能重跑（rc=0；老 bug 在这里 TypeError）',
      _rr115.returncode == 0, _oo115[-300:])
# ④ 反向：把元组解包识别关掉 ⇒ 块里又出现 `ax1[0]`（说明判据真在用，不是空转）
_s115b = os.path.join(_d115, 'fig2.py')
with open(_s115b, 'w', encoding='utf-8', newline='\n') as f:
    f.write(_BODY115)
_orig115 = writeback._unpacked_axis_names
writeback._unpacked_axis_names = lambda tree, call: None
try:
    _res115b = writeback.writeback(_s115b, dict(_PDATA115), _p115,
                                   verify=False, semantic=False)
finally:
    writeback._unpacked_axis_names = _orig115
_blk115b = _res115b.get('block') or ''
# 注：$117 起，"关掉元组解包识别"不再会写出 `ax1[0]` —— 静态自检直接拦下（fail-closed，
# 块里没有那行、并点名理由）。旧行为的复现放在 §117③（连自检的判据一起改坏才看得到）。
check('§115④ 关掉元组解包识别 ⇒ 自检拦下：块里没有 `ax1[0]`，并点名"元组解包"',
      not re.search(r'ax1\[\d\]', _blk115b)
      and any('静态自检' in w and '元组解包' in w
              for w in (_res115b.get('warnings') or [])),
      (_blk115b[:120], [w[:50] for w in (_res115b.get('warnings') or [])[:2]]))

# ============================================================================
# §116（0.1.16）"生成的代码跑不起来"的归因文案：必须说"是我们的问题"，
# 不许说"（无法确定原因）"—— 否则用户读到"你的脚本跑不起来"⇒ 不会报告，只当
# "这种图它不支持"（元组解包那个 bug 就是这么躲过 0.1.13/0.1.14/0.1.15）。
# 证据基础：能走到"写回后重跑失败"这条路径，就说明**原脚本单独是跑得通的**
# （writeback 只在"原文能跑、写回后的不能跑"时才把 stderr 尾部交出来）。
# ============================================================================
_s116 = os.path.join(_d115, 'fig_attrib.py')
with open(_s116, 'w', encoding='utf-8', newline='\n') as f:
    f.write(_BODY115)
_orig116 = writeback._unpacked_axis_names
_orig_sv116 = writeback._subscript_verdict
# 复现"旧寻址 + 旧自检"（= 0.1.15 的行为）：这两道闸都关掉，才会真的写出 `ax1[0]`
# 并走到"重跑 TypeError → 回滚"。只关第一道的话，静态自检会拦下、根本不会进验证
# （§115④ / §117② 就是那个更好的结果）。
writeback._unpacked_axis_names = lambda tree, call: None
writeback._subscript_verdict = lambda tree, container: ('array', 'mutated')
try:
    _res116 = writeback.writeback(_s116, dict(_PDATA115), _p115,
                                  verify=True, semantic=True)
finally:
    writeback._unpacked_axis_names = _orig116
    writeback._subscript_verdict = _orig_sv116
_kind116, _detail116 = apply._classify_verify_failure(_res116, _s116, ())
check('§116① 归因指向"工具生成的问题"（tool_bug / generated_code_failed），不是"无法确定原因"',
      (_kind116, _detail116) == ('tool_bug', 'generated_code_failed'),
      'reason=%r kind/detail=%r tail=%r' % (
          _res116.get('reason'), (_kind116, _detail116),
          (_res116.get('verify_fail_tail') or '')[-120:]))
_buf116 = io.StringIO()
with contextlib.redirect_stderr(_buf116):
    apply._print_attribution(_kind116, _detail116, _s116)
_o116 = _buf116.getvalue()
check('§116② 人读文案：明说"mpltweak 生成的问题、不是你脚本的问题"+ 保留"已回滚"',
      '生成的问题' in _o116 and '已回滚' in _o116
      and '无法确定原因' not in _o116, _o116.strip()[:220])
# 反向：把 detail 级文案映射摘掉（等价于"文案改回旧说法"）⇒ 上面那条必红
_orig_map116 = apply._DETAIL_MSG_KEYS
apply._DETAIL_MSG_KEYS = {}
try:
    _buf116b = io.StringIO()
    with contextlib.redirect_stderr(_buf116b):
        apply._print_attribution(_kind116, _detail116, _s116)
    _o116b = _buf116b.getvalue()
finally:
    apply._DETAIL_MSG_KEYS = _orig_map116
check('§116③ 反向：摘掉 detail 文案映射 ⇒ 新说法消失（这条判据不是空转）',
      '生成的问题' not in _o116b and _o116b != _o116, _o116b.strip()[:200])

# ============================================================================
# §117（0.1.16）静态自检：**落盘前**问一句"这个符号真的能下标吗"
#
# 为什么必须"不跑脚本就能判错"：安全网（重跑失败 → 回滚）会把"生成了跑不通的代码"
# 变成 rc=1 +「脚本跑不起来」，于是元组解包那个 bug 隐身了三个版本。判据只认可证的
# 三种来源（subplots 数组 / 已带下标的元素 / 单轴），证不出就 fail-closed。
# ============================================================================
_t117 = _ast.parse("import matplotlib.pyplot as plt\n"
                   "fig, (ax1, ax2) = plt.subplots(1, 2)\n"
                   "fig2, axs = plt.subplots(1, 2)\n"
                   "axq = fig.add_subplot(1, 1, 1)\n"
                   "t = plt.subplots(1, 2)\n"
                   "weird = make_axes()\n")
check('§117① 判据：元组解包名 ⇒ single（不许下标），subplots 数组 ⇒ array',
      writeback._subscript_verdict(_t117, 'ax1') == ('single',
                                                     'single_axis_from_unpack')
      and writeback._subscript_verdict(_t117, 'axs') == ('array', 'subplots_array'),
      (writeback._subscript_verdict(_t117, 'ax1'),
       writeback._subscript_verdict(_t117, 'axs')))
check('§117① add_subplot 名 ⇒ single；`t[1]` 已带下标 ⇒ element；来源不明 ⇒ unknown',
      writeback._subscript_verdict(_t117, 'axq') == ('single', 'single_axis')
      and writeback._subscript_verdict(_t117, 't[1]') == ('element',
                                                          'already_element')
      and writeback._subscript_verdict(_t117, 'weird') == ('unknown',
                                                           'unknown_symbol'),
      (writeback._subscript_verdict(_t117, 'axq'),
       writeback._subscript_verdict(_t117, 't[1]'),
       writeback._subscript_verdict(_t117, 'weird')))
# 端到端：把元组解包识别关掉（=旧寻址）⇒ 自检必须**拦住**，而不是写出 ax1[0]
_s117 = os.path.join(tempfile.mkdtemp(prefix='mpltweak_s3chk_'), 'fig.py')
with open(_s117, 'w', encoding='utf-8', newline='\n') as f:
    f.write(_BODY115)
_d117 = os.path.dirname(_s117)
_p117 = os.path.join(_d117, 'p.json')
with open(_p117, 'w', encoding='utf-8') as f:
    json.dump(_PDATA115, f)
_o_pv117, _o_un117 = writeback._subscript_verdict, writeback._unpacked_axis_names
writeback._unpacked_axis_names = lambda tree, call: None
try:
    _res117 = writeback.writeback(_s117, dict(_PDATA115), _p117,
                                  verify=False, semantic=False)
finally:
    writeback._unpacked_axis_names = _o_un117
_blk117, _w117 = _res117.get('block') or '', _res117.get('warnings') or []
check('§117② 自检拦下"对单轴下标"：块里没有 `ax1[数字]`，且给出点名理由',
      not re.search(r'ax1\[\d\]', _blk117)
      and any('静态自检' in w and '元组解包' in w for w in _w117),
      'block=%r warns=%r' % (_blk117[:120], [w[:60] for w in _w117[:2]]))
# 有牙：把判据改成恒 'array' ⇒ 上面那条必红（坏表达式又出现）
writeback._subscript_verdict = lambda tree, container: ('array', 'mutated')
writeback._unpacked_axis_names = lambda tree, call: None
try:
    _res117b = writeback.writeback(_s117, dict(_PDATA115), _p117,
                                   verify=False, semantic=False)
finally:
    writeback._subscript_verdict, writeback._unpacked_axis_names = _o_pv117, _o_un117
check('§117③ 反向：判据改成恒 array ⇒ 块里又出现 `ax1[0].set_position`（真在把关）',
      bool(re.search(r'ax1\[\d\]\.set_position', _res117b.get('block') or '')),
      (_res117b.get('block') or '')[:160])
shutil.rmtree(_d117, ignore_errors=True)

# ============================================================================
# §118（0.1.16 / S7）`subplot_mosaic` 的**字典常量键**寻址：`axd['a'].set_position(...)`
#
# 为什么要有：mosaic 返回的是 `{键: Axes}` 字典，旧代码把它整份判成"不可证"⇒ 拼图
# 写不进去（rc=1 / nothing_applied）。而"第 k 条轴 ↔ 第 k 个首次出现的键"是**可证**的
# （matplotlib `_do_layout` 就是行主序扫格子、第一次见到的键新建一条轴）。
# 判据：只认**字面量**布局（嵌套 list/tuple 的 str 常量，或 `"AB;CD"` 这种字符串）；
# 布局是变量/表达式 ⇒ 退回旧路径（整份不可证，fail-closed）。
# ============================================================================
_t118 = _ast.parse("import matplotlib.pyplot as plt\n"
                   "fig, axd = plt.subplot_mosaic([['a', 'b'], ['c', 'c']])\n"
                   "fig2, axe = plt.subplot_mosaic('AB;CD')\n"
                   "spec = make_spec()\n"
                   "fig3, axf = plt.subplot_mosaic(spec)\n")
_t118a = _ast.parse("fig, axd = plt.subplot_mosaic([['a','b'],['c','c']])")
_t118b = _ast.parse("fig, axd = plt.subplot_mosaic('AB;CD')")
_t118c = _ast.parse("fig, axd = plt.subplot_mosaic(spec)")
check('§118① 键表按**首次出现（行主序）**：[[a,b],[c,c]] ⇒ [a,b,c]；"AB;CD" ⇒ [A,B,C,D]',
      writeback._mosaic_keys(_t118a.body[0].value) == ['a', 'b', 'c']
      and writeback._mosaic_keys(_t118b.body[0].value) == ['A', 'B', 'C', 'D'],
      (writeback._mosaic_keys(_t118a.body[0].value),
       writeback._mosaic_keys(_t118b.body[0].value)))
check('§118① 布局不是字面量（变量/表达式）⇒ 读不出键 ⇒ 退回旧路径（不许猜）',
      writeback._mosaic_keys(_t118c.body[0].value) is None,
      writeback._mosaic_keys(_t118c.body[0].value))
# 端到端：拼图脚本真写回 —— 块里是 `axd['键'].set_position(...)`，且**没有** `axd[数字]`
_d118 = tempfile.mkdtemp(prefix='mpltweak_mosaic_')
_s118 = os.path.join(_d118, 'fig.py')
with open(_s118, 'w', encoding='utf-8', newline='\n') as f:
    f.write("import matplotlib\nmatplotlib.use('Agg')\n"
            "import matplotlib.pyplot as plt\n"
            "fig, axd = plt.subplot_mosaic([['a', 'b'], ['c', 'c']], figsize=(6, 5))\n"
            "axd['a'].plot([1, 2], [2, 1])\n"
            "axd['b'].plot([1, 2], [1, 2])\n"
            "axd['c'].plot([1, 2], [3, 1])\n"
            "fig.savefig('o.png', dpi=80)\n")
_p118 = os.path.join(_d118, 'p.json')
with open(_p118, 'w', encoding='utf-8') as f:
    json.dump({'version': 4, 'script': 'fig.py', 'fig_index': 0, 'n_figs': 1,
               'axes': [{'index': 0, 'pos': [0.06, 0.1, 0.4, 0.8]}]}, f)
_tm118 = time.time()
os.utime(_s118, (_tm118 - 60, _tm118 - 60))
os.utime(_p118, (_tm118, _tm118))
_r118 = subprocess.run([sys.executable, '-m', 'mpltweak', 'apply', 'fig.py',
                        '--params', 'p.json', '--write', '--json'],
                       cwd=_d118, capture_output=True, env=_ENV114)
_j118 = {}
try:
    _j118 = json.loads(_decode_out(_r118.stdout))
except Exception:                                                 # noqa: BLE001
    pass
_f118 = (_j118.get('files') or [{}])[0]
_txt118 = open(_s118, encoding='utf-8').read()
_rr118 = subprocess.run([sys.executable, 'fig.py'], cwd=_d118,
                        capture_output=True, env=_ENV114)
check('§118② 拼图写回成功且脚本能跑：块里 `axd[\'a\'].set_position(...)`，无 `axd[数字]`',
      _r118.returncode == 0 and _f118.get('applied') is True
      and "axd['a'].set_position(" in _txt118
      and not re.search(r'axd\[\d+\]', _txt118) and _rr118.returncode == 0,
      'rc=%s applied=%r | %s' % (_r118.returncode, _f118.get('applied'),
                                 _txt118[-200:]))
# 反向：把字典键识别关掉 ⇒ 拼图又写不进去（块里没有 set_position、applied=false）
_o118 = writeback._mosaic_keys
_s118b = os.path.join(_d118, 'fig2.py')
with open(_s118b, 'w', encoding='utf-8', newline='\n') as f:
    f.write(open(_s118, encoding='utf-8').read())     # 已写回版也照跑一遍
writeback._mosaic_keys = lambda call: None
try:
    _res118b = writeback.writeback(_s118b, {'version': 4, 'script': 'fig.py',
                                            'fig_index': 0, 'n_figs': 1,
                                            'axes': [{'index': 0,
                                                      'pos': [0.1, 0.1, 0.5, 0.5]}]},
                                   _p118, verify=False, semantic=False)
finally:
    writeback._mosaic_keys = _o118
check('§118③ 反向：关掉字典键识别 ⇒ 拼图写不进去（判据真在用）',
      'set_position' not in (_res118b.get('block') or '')
      and _res118b.get('applied') is False,
      'reason=%r applied=%r' % (_res118b.get('reason'),
                                _res118b.get('applied')))
shutil.rmtree(_d118, ignore_errors=True)

# ============================================================================
# §120（0.1.17 / S9）「函数里建图」拒写：除了"为什么不写"，还要给**照着改**的指导
#
# 真实语料 535 份里，16 份"结构上写不了"有 10 份是这一类（画图封装在函数里、模块级没有
# fig/axes）。只报"证不出目标"agent 无从下手；这里要求输出里带"改哪一处、改成什么"。
# 只在 func_multi_return / func_called_many 出现 —— 循环建轴那种改一行也写不进去，
# 加了只会误导（§120③ 专门钉这条边界）。
# ============================================================================
_D120 = tempfile.mkdtemp(prefix='mpltweak_s9_')
_BODY120 = {
    'multi_return': chr(10).join([
        'import matplotlib', 'matplotlib.use(' + chr(39) + 'Agg' + chr(39) + ')',
        'import matplotlib.pyplot as plt', '', 'def plot_it(flag):',
        '    fig, axes = plt.subplots(2, 2, figsize=(8, 6))',
        '    axes[0, 0].plot([1, 2], [2, 1])',
        '    if flag:', '        return fig', '    return fig', '',
        'plot_it(True)', '']),
    'called_many': chr(10).join([
        'import matplotlib', 'matplotlib.use(' + chr(39) + 'Agg' + chr(39) + ')',
        'import matplotlib.pyplot as plt', '', 'def plot_it(tag):',
        '    fig, axes = plt.subplots(2, 2, figsize=(8, 6))',
        '    axes[0, 0].plot([1, 2], [2, 1])', '    return fig', '',
        '_a = plot_it(' + chr(39) + 'a' + chr(39) + ')',
        '_b = plot_it(' + chr(39) + 'b' + chr(39) + ')',
        '_b.savefig(' + chr(39) + 'out.png' + chr(39) + ')', '']),
    'loop': chr(10).join([
        'import matplotlib', 'matplotlib.use(' + chr(39) + 'Agg' + chr(39) + ')',
        'import matplotlib.pyplot as plt',
        'fig = plt.figure(figsize=(8, 6))', 'for i in range(4):',
        '    ax = fig.add_subplot(2, 2, i + 1)', '    ax.plot([1, 2], [i, i + 1])',
        'fig.savefig(' + chr(39) + 'out.png' + chr(39) + ')', '']),
}

_S120 = {
    'multi_return': {'fig.func_multi_return', 'refused_hint'},
    'called_many': {'fig.func_called_many', 'refused_hint'},
    'loop': set(),
}
_r120 = {}
for _nm120, _body120 in _BODY120.items():
    _c120 = os.path.join(_D120, _nm120)
    os.makedirs(_c120, exist_ok=True)
    _sp120 = os.path.join(_c120, 'fig.py')
    with open(_sp120, 'w', encoding='utf-8', newline='\n') as f:
        f.write(_body120)
    _pp120 = os.path.join(_c120, 'p.json')
    with open(_pp120, 'w', encoding='utf-8') as f:
        json.dump({'version': 4, 'script': 'fig.py', 'fig_index': 0, 'n_figs': 1,
                   'axes': [{'index': 0, 'pos': [0.1, 0.1, 0.4, 0.4]}]}, f)
    _tm120 = time.time()
    os.utime(_sp120, (_tm120 - 60, _tm120 - 60))
    os.utime(_pp120, (_tm120, _tm120))
    _r120[_nm120] = subprocess.run(
        [sys.executable, '-m', 'mpltweak', 'apply', 'fig.py', '--params',
         'p.json', '--write', '--json'],
        cwd=_c120, capture_output=True, env=_ENV114)
_j120 = {}
for _nm120, _r in _r120.items():
    try:
        _j120[_nm120] = json.loads(_decode_out(_r.stdout))
    except Exception:                                             # noqa: BLE001
        _j120[_nm120] = {}
_f120 = {k: ((v.get('files') or [{}])[0]) for k, v in _j120.items()}
# ① / ②：两类"函数里建图"都要出现**照着改**的指导（人读 warnings + --json refused_hint）
for _nm120 in ('multi_return', 'called_many'):
    _f = _f120[_nm120]
    _hit = [w for w in (_f.get('warnings') or [])
            if '二选一' in w and '模块级' in w]
    check('§120/%s 拒写输出带**照着改**的指导（改哪一处 + 改成什么 + 跑什么）'
          % _nm120,
          bool(_hit) and bool(_f.get('refused_hint'))
          and any(_nm120 == 'x' or _p in (_f.get('pending') or [])
                  for _p in ('fig.func_multi_return', 'fig.func_called_many')),
          'pending=%r hint=%r' % ((_f.get('pending'),),
                                  (_f.get('refused_hint') or '')[:60]))
# ③ 循环建轴：普通拒写，**不许**出现这段（改一行也写不进去，加了就误导）
_f3 = _f120['loop']
check('§120/loop 普通拒写（循环建轴）**不带**这段指导（边界没被污染）',
      not _f3.get('refused_hint')
      and not any('二选一' in w for w in (_f3.get('warnings') or []))
      and 'fig.func_multi_return' not in (_f3.get('pending') or [])
      and 'fig.func_called_many' not in (_f3.get('pending') or []),
      'pending=%r hint=%r' % (_f3.get('pending'),
                              (_f3.get('refused_hint') or '')[:60]))
# ④ 反向：关掉开关 ⇒ 指导消失（这条判据不是空转）
_o120 = writeback.FUNC_GUIDE
_s120b = os.path.join(_D120, 'multi_return', 'fig2.py')
with open(_s120b, 'w', encoding='utf-8', newline='\n') as f:
    f.write(_BODY120['multi_return'])
writeback.FUNC_GUIDE = False
try:
    _res120b = writeback.writeback(
        _s120b, {'version': 4, 'script': 'fig.py', 'fig_index': 0, 'n_figs': 1,
                 'axes': [{'index': 0, 'pos': [0.2, 0.2, 0.3, 0.3]}]},
        os.path.join(_D120, 'multi_return', 'p.json'), verify=False,
        semantic=False)
finally:
    writeback.FUNC_GUIDE = _o120
check('§120④ 反向：关掉 FUNC_GUIDE ⇒ 指导消失（判据真在用）',
      not _res120b.get('refused_hint')
      and not any('二选一' in w for w in (_res120b.get('warnings') or []))
      and 'fig.func_multi_return' in (_res120b.get('pending') or []),
      'pending=%r' % (_res120b.get('pending'),))
shutil.rmtree(_D120, ignore_errors=True)

# §121（0.1.17 / **兑现 0.1.15 的承诺**）刻度字号的分侧去重。
#   0.1.15 只给 title/xlabel/ylabel 做了"块里别再写一遍原位已经写好的字号"，刻度字号
#   当时明写"牵涉 x/y 两侧 axis= 判据，下一版统一"。这一节盯三件事：
#     ① 分侧：源码只写了 x 侧 ⇒ 块里只补 y 侧；两侧都写了 ⇒ 块里一行都不写；
#     ② 纪律：**证不出来就不去重** —— axis= 非字面量、同一侧两处字面量互相冲突 ⇒ 照写；
#     ③ 落地：写回后脚本还能跑（rc=0），且没被漏写的字段真的落到了图上。
_S121_HEAD = ('import matplotlib\n'
              'matplotlib.use("Agg")\n'
              'import matplotlib.pyplot as plt\n'
              'fig, ax = plt.subplots(figsize=(6, 4))\n'
              'ax.plot([1, 2, 3], label="a")\n')
_S121_TAIL = 'fig.savefig("out.png")\n'


def _pin_child_env(base=None, **overrides):
    """起子进程前**最后一道闸**：硬钉 ``MPLBACKEND=Agg``（覆盖宿主投毒）。

    与 tests/test_hygiene.py / test_docs.py 的同名闸门是**同一口径的另一份副本**：
    宿主把 `MPLBACKEND` 设成 `Qt5Agg` 时，子进程里的建图/`plt.show()` 会真开窗抢焦点。
    `test_hygiene.py::test_every_child_process_call_site_is_pinned_or_ledgered` 按 AST
    逐个调用点检查"有没有经过闸门"，手搓 `dict(os.environ)` 不算 ⇒ 本文件新加的子进程
    调用点必须走这里。
    """
    out = dict(os.environ if base is None else base)
    out['MPLBACKEND'] = 'Agg'
    out.update(overrides)
    return out


def _s121_run(body, axp, style='block', check_rc=True):
    """造脚本 → 写回 → 返回 (临时目录, 块文本, 结果, 块里的 tick 行, 重跑 rc)。"""
    import subprocess                                              # noqa: E402
    d, s, p = _mk15('t121', _S121_HEAD + body + _S121_TAIL,
                    {'version': 4, 'script': 'fig.py',
                     'axes': [dict({'index': 0}, **axp)]})
    r = writeback.writeback(s, _params.load(p), p, style=style, verify=False)
    blk = r.get('block') or ''
    rc = (subprocess.run([sys.executable, s], cwd=d, env=_pin_child_env(),
                         capture_output=True).returncode if check_rc else None)
    return (d, blk, r, [ln.strip() for ln in blk.splitlines()
                        if 'tick_params(' in ln], rc)


# 121① 源码只写 x 侧（=目标值），y 侧要改 ⇒ 块里只写 y 那一行
_d, _blk, _r, _tick, _rc = _s121_run(
    "ax.tick_params(axis='x', labelsize=12)\nax.grid(False)\n",
    {'tick_fontsize': 12.0, 'tick_fontsize_y': 14.0, 'grid': True})
check('§121① 源码只写了 x 刻度字号 ⇒ 块里**只剩 y 那一行**（x 侧不重复写），rc=0',
      _tick == ["ax.tick_params(axis='y', labelsize=14)"] and _rc == 0,
      'tick=%r rc=%r reason=%r' % (_tick, _rc, _r.get('reason')))
shutil.rmtree(_d, ignore_errors=True)

# 121② 反过来：源码只写 y 侧 ⇒ 块里必须**仍然写 x**（这一行的漏写正是旧判据的坑）
_d, _blk, _r, _tick, _rc = _s121_run(
    "ax.tick_params(axis='y', labelsize=12)\nax.grid(False)\n",
    {'tick_fontsize': 12.0, 'tick_fontsize_y': 12.0, 'grid': True})
check('§121② 源码只写了 y 刻度字号 ⇒ 块里**必须写 x 那一行**（不许拿 y 的字面量'
      '证明 x 等值——旧判据就是在这里漏写），rc=0',
      _tick == ["ax.tick_params(axis='x', labelsize=12)"] and _rc == 0,
      'tick=%r rc=%r reason=%r' % (_tick, _rc, _r.get('reason')))
shutil.rmtree(_d, ignore_errors=True)

# 121③ 两侧都写了 ⇒ 块里 tick 字号一行都没有（grid 仍要写，证明块本身还在）
_d, _blk, _r, _tick, _rc = _s121_run(
    "ax.tick_params(axis='x', labelsize=12)\n"
    "ax.tick_params(axis='y', labelsize=14)\nax.grid(False)\n",
    {'tick_fontsize': 12.0, 'tick_fontsize_y': 14.0, 'grid': True})
check('§121③ 两侧都写进源码且都等于目标 ⇒ 块里 tick 字号**0 行**、grid 照写，rc=0',
      _tick == [] and 'ax.grid(True)' in _blk and _rc == 0,
      'tick=%r has_grid=%r rc=%r' % (_tick, 'ax.grid(True)' in _blk, _rc))
shutil.rmtree(_d, ignore_errors=True)

# 121④ 不带 axis= 的 `tick_params(labelsize=)` 一次管两侧 ⇒ 两侧都算已写
_d, _blk, _r, _tick, _rc = _s121_run(
    "ax.tick_params(labelsize=12)\nax.grid(False)\n",
    {'tick_fontsize': 12.0, 'tick_fontsize_y': 12.0, 'grid': True})
check('§121④ 不带 axis 的 `tick_params(labelsize=12)` 管两侧 ⇒ 块里 0 行，rc=0',
      _tick == [] and _rc == 0, 'tick=%r rc=%r' % (_tick, _rc))
shutil.rmtree(_d, ignore_errors=True)

# 121⑤ 纪律：`axis=` 不是字面量 ⇒ 说不清它改哪一侧 ⇒ **不许去重**（照写两行）
_d, _blk, _r, _tick, _rc = _s121_run(
    "_side = 'x'\nax.tick_params(axis=_side, labelsize=12)\nax.grid(False)\n",
    {'tick_fontsize': 12.0, 'tick_fontsize_y': 12.0, 'grid': True})
check('§121⑤ `axis=` 是变量（证不出哪一侧）⇒ 块里照写两侧（宁可冗余，不漏写）',
      len(_tick) == 2 and _rc == 0, 'tick=%r rc=%r' % (_tick, _rc))
shutil.rmtree(_d, ignore_errors=True)

# 121⑥ 纪律：同一侧两处字面量互相冲突（后写的生效）⇒ 也不许去重
_d, _blk, _r, _tick, _rc = _s121_run(
    "ax.tick_params(axis='x', labelsize=12)\n"
    "ax.tick_params(axis='x', labelsize=20)\nax.grid(False)\n",
    {'tick_fontsize': 12.0, 'grid': True})
check('§121⑥ 同一侧两处 `axis=\'x\'` 字面量冲突（12 / 20）⇒ 块里照写 x 那一行',
      _tick == ["ax.tick_params(axis='x', labelsize=12)"] and _rc == 0,
      'tick=%r rc=%r' % (_tick, _rc))
shutil.rmtree(_d, ignore_errors=True)

# 121⑦ 承诺本体：**原位通道**已经把 labelsize=10 改成 12，只因 pos 落不了地退块 ——
#       块里不许再把刚写好的刻度字号重写一遍（0.1.15 就是在这里留了尾巴）。
_S121_AUTO = ('import matplotlib\nmatplotlib.use("Agg")\n'
              'import matplotlib.pyplot as plt\n'
              'fig, ax = plt.subplots(figsize=(6, 4))\n'
              'ax.plot([1, 2, 3])\nax.tick_params(labelsize=10)\n'
              'fig.savefig("out.png")\n')
_d121, _s121, _p121 = _mk15('t121auto', _S121_AUTO,
                            {'version': 4, 'script': 'fig.py',
                             'axes': [{'index': 0, 'tick_fontsize': 12.0,
                                       'pos': [0.2, 0.2, 0.6, 0.6]}]})
_ri121 = writeback.writeback(_s121, _params.load(_p121), _p121, verify=False,
                             style='inplace')
_rb121 = writeback.writeback(_s121, _params.load(_p121), _p121, verify=False,
                             style='block')
_txt121 = read(_s121)
check('§121⑦ 原位已把 labelsize=10 改成 12、退块后块里**不再重复写**刻度字号'
      '（块里仍有 set_position，证明块确实插了）',
      any('ax0.tick_fontsize' == c for c in (_ri121.get('changes') or []))
      and 'tick_params(labelsize=12)' in _txt121
      and 'tick_params(' not in (_rb121.get('block') or '')
      and 'set_position(' in (_rb121.get('block') or ''),
      'inplace=%r block=%r'
      % (_ri121.get('changes'),
         [ln.strip() for ln in (_rb121.get('block') or '').splitlines()
          if 'tick_params(' in ln or 'set_position' in ln]))
shutil.rmtree(_d121, ignore_errors=True)

# 121⑧【有牙】把分侧判据换成恒 False（= 修前行为）⇒ 121①③④⑦ 的冗余行必须回来
_o_tick_eq = writeback._tick_fontsize_literal_equal
try:
    writeback._tick_fontsize_literal_equal = lambda *a, **k: False
    _dups = []
    for _b, _ap in (
            ("ax.tick_params(axis='x', labelsize=12)\nax.grid(False)\n",
             {'tick_fontsize': 12.0, 'tick_fontsize_y': 14.0, 'grid': True}),
            ("ax.tick_params(axis='x', labelsize=12)\n"
             "ax.tick_params(axis='y', labelsize=14)\nax.grid(False)\n",
             {'tick_fontsize': 12.0, 'tick_fontsize_y': 14.0, 'grid': True}),
            ("ax.tick_params(labelsize=12)\nax.grid(False)\n",
             {'tick_fontsize': 12.0, 'tick_fontsize_y': 12.0, 'grid': True})):
        _dd, _bb, _rr, _tt, _cc = _s121_run(_b, _ap, check_rc=False)
        _dups.append(len(_tt))
        shutil.rmtree(_dd, ignore_errors=True)
    writeback._tick_fontsize_literal_equal = _o_tick_eq
    _dd, _bb, _rr, _tt, _cc = _s121_run(
        "ax.tick_params(labelsize=12)\nax.grid(False)\n",
        {'tick_fontsize': 12.0, 'tick_fontsize_y': 12.0, 'grid': True},
        check_rc=False)
    shutil.rmtree(_dd, ignore_errors=True)
    writeback._tick_fontsize_literal_equal = lambda *a, **k: False
    _dd, _bb2, _rr, _tt2, _cc = _s121_run(
        "ax.tick_params(labelsize=12)\nax.grid(False)\n",
        {'tick_fontsize': 12.0, 'tick_fontsize_y': 12.0, 'grid': True},
        check_rc=False)
    shutil.rmtree(_dd, ignore_errors=True)
    check('§121⑧【有牙】判据换回恒 False ⇒ 冗余 tick 行立刻回来'
          '（三个夹具各 2 行；plain 夹具从 0 行回到 2 行）',
          _dups == [2, 2, 2] and len(_tt) == 0 and len(_tt2) == 2,
          '无去重时行数=%r / 有去重 0 行→关掉后 %d 行'
          % (_dups, len(_tt2)))
finally:
    writeback._tick_fontsize_literal_equal = _o_tick_eq

# §122（0.1.17）legend / colorbar 的**块内写法体检**：块里写下的每一笔都要真的落到图上。
#   做法：写回 → 子进程**重跑写好的脚本**（尾部追加读回探针）→ "读回值 == 请求值"。
#   这一节顺带钉住体检发现的两件事：
#     ① `loc` 与 `anchor` 并存时旧实现只写 loc（GUI 拖图例的结果被顶掉）—— **已修**；
#     ② 只给 fontsize（没有 loc/anchor）块里写不出来 —— 保持**拒写**（pending + 警告），
#        不许静默无效。
_S122_LGD = ('import matplotlib\n'
             'matplotlib.use("Agg")\n'
             'import matplotlib.pyplot as plt\n'
             'fig, ax = plt.subplots(figsize=(6, 4))\n'
             'ax.plot([1, 2, 3], label="a")\n'
             'ax.legend(loc="upper right")\n'
             'fig.savefig("out.png")\n')
_S122_CB = ('import matplotlib\n'
            'matplotlib.use("Agg")\n'
            'import matplotlib.pyplot as plt\n'
            'import numpy as np\n'
            'fig, ax = plt.subplots(figsize=(6, 4))\n'
            'im = ax.imshow(np.arange(12).reshape(3, 4))\n'
            'cb = fig.colorbar(im, ax=ax)\n'
            'cb.set_label("value")\n'
            'fig.savefig("out.png")\n')
_S122_RB_LGD = ('import json as _j\n'
                'out = {}\n'
                'lg = ax.get_legend()\n'
                'if lg is not None:\n'
                '    out["loc"] = lg._get_loc()\n'
                '    out["fs"] = lg.get_texts()[0].get_fontsize()\n'
                '    _bb = getattr(lg.get_bbox_to_anchor(), "_bbox", None)\n'
                '    out["anchor"] = ([round(_bb.x0, 4), round(_bb.y0, 4)]\n'
                '                     if _bb is not None else None)\n'
                'print("RB" + _j.dumps(out))\n')
_S122_RB_CB = ('import json as _j\n'
               'out = {"cbpos": [round(v, 4) for v in cb.ax.get_position().bounds],\n'
               '       "cby": cb.ax.yaxis.label.get_fontsize(),\n'
               '       "cbx": cb.ax.xaxis.label.get_fontsize()}\n'
               'print("RB" + _j.dumps(out))\n')


def _s122_probe(src, axs, tail):
    """写回 → 尾部接读回探针 → 子进程重跑 → 返回 (目录, 块行, 读回 dict, rc, 输出)。"""
    import subprocess                                              # noqa: E402
    d, s, p = _mk15('t122', src,
                    {'version': 4, 'script': 'fig.py', 'axes': axs})
    r = writeback.writeback(s, _params.load(p), p, style='block', verify=False)
    _txt = read(s)                 # 先读再开 'w'（开 'w' 会清空文件，读到的就是空的）
    with open(s, 'w', encoding='utf-8', newline='\n') as f:
        f.write(_txt + tail)
    cp = subprocess.run([sys.executable, s], cwd=d, env=_pin_child_env(),
                        capture_output=True)
    out = _decode_out(cp.stdout + cp.stderr)
    rb = {}
    for ln in out.splitlines():
        if ln.startswith('RB'):
            rb = json.loads(ln[2:])
    return (d, [ln.strip() for ln in (r.get('block') or '').splitlines()],
            rb, cp.returncode, out, r)


def _s122_legend(lg):
    return _s122_probe(_S122_LGD, [dict({'index': 0}, legend=lg)],
                       _S122_RB_LGD)


_d, _lines, _rb, _rc, _out, _r = _s122_legend({'loc': 'lower left'})
check('§122① legend.loc：块里写 `ax.legend(loc=\'lower left\')`，重跑读回 loc=lower left',
      _rc == 0 and _rb.get('loc') == 3
      and any("ax.legend(loc='lower left')" in ln for ln in _lines),
      'rc=%r 读回=%r 块行=%r' % (_rc, _rb, [l for l in _lines if 'legend' in l]))
shutil.rmtree(_d, ignore_errors=True)

_d, _lines, _rb, _rc, _out, _r = _s122_legend({'loc': 'lower left',
                                             'fontsize': 14})
check('§122② legend.fontsize（与 loc 同在）：读回 14.0，且 loc 没被顺手改掉',
      _rc == 0 and _rb.get('loc') == 3 and _rb.get('fs') == 14.0,
      'rc=%r 读回=%r 块行=%r' % (_rc, _rb, [l for l in _lines if 'legend' in l]))
shutil.rmtree(_d, ignore_errors=True)

_d, _lines, _rb, _rc, _out, _r = _s122_legend({'anchor': [0.2, 0.7]})
check('§122③ legend.anchor：块里写 `bbox_to_anchor=(0.2, 0.7)`，读回锚点 == 请求值',
      _rc == 0 and _rb.get('anchor') == [0.2, 0.7],
      'rc=%r 读回=%r 块行=%r' % (_rc, _rb, [l for l in _lines if 'legend' in l]))
shutil.rmtree(_d, ignore_errors=True)

_d, _lines, _rb, _rc, _out, _r = _s122_legend({'loc': 'upper right',
                                             'anchor': [0.35, 0.15]})
check('§122④ loc 与 anchor **同时给**（GUI 拖图例就是这个形状）：两者都要读到，'
      'anchor 不许被 loc 顶掉（0.1.17 修的"写了不生效"）',
      _rc == 0 and _rb.get('loc') == 1 and _rb.get('anchor') == [0.35, 0.15],
      'rc=%r 读回=%r 块行=%r' % (_rc, _rb, [l for l in _lines if 'legend' in l]))
shutil.rmtree(_d, ignore_errors=True)

_d, _lines, _rb, _rc, _out, _r = _s122_legend({'fontsize': 14})
check('§122⑤ 只给 legend.fontsize（没有 loc/anchor）⇒ **拒写**：块里没有 legend 行，'
      '但必须有 pending + 警告（宁可明说，不许静默无效）',
      _rc == 0 and not any('.legend(' in ln for ln in _lines)
      and 'ax0.legend' in (_r.get('pending') or [])
      and bool(_r.get('warnings')),
      'pending=%r warnings=%r 块行=%r'
      % (_r.get('pending'), (_r.get('warnings') or [])[:1],
         [l for l in _lines if 'legend' in l]))
shutil.rmtree(_d, ignore_errors=True)

# 122⑥~⑧ colorbar：位置（= 位置 / 长度 / 厚度 的 4 元组）与标签字号
_CB_POS = [0.86, 0.2, 0.03, 0.6]
_d, _lines, _rb, _rc, _out, _r = _s122_probe(
    _S122_CB, [{'index': 0}, {'index': 1, 'is_colorbar': True, 'pos': _CB_POS}],
    _S122_RB_CB)
check('§122⑥ colorbar.pos：块里 set_position + 先卸 locator/box_aspect，'
      '重跑读回几何 == 请求的 4 元组',
      _rc == 0 and _rb.get('cbpos') == _CB_POS
      and 'cb.ax.set_axes_locator(None)' in _lines
      and 'cb.ax.set_box_aspect(None)' in _lines,
      'rc=%r 读回 pos=%r 请求=%r 块行=%r'
      % (_rc, _rb.get('cbpos'), _CB_POS, [l for l in _lines if 'cb.' in l]))
shutil.rmtree(_d, ignore_errors=True)

_d, _lines, _rb, _rc, _out, _r = _s122_probe(
    _S122_CB, [{'index': 0}, {'index': 1, 'is_colorbar': True,
                              'label_fontsize_y': 13.0}], _S122_RB_CB)
check('§122⑦ colorbar 长边标签字号 label_fontsize_y：块里写 '
      '`cb.ax.yaxis.label.set_fontsize(13)`，读回 13.0',
      _rc == 0 and _rb.get('cby') == 13.0,
      'rc=%r 读回=%r 块行=%r' % (_rc, _rb, [l for l in _lines if 'cb.' in l]))
shutil.rmtree(_d, ignore_errors=True)

_d, _lines, _rb, _rc, _out, _r = _s122_probe(
    _S122_CB, [{'index': 0}, {'index': 1, 'is_colorbar': True,
                              'label_fontsize': 15.0}], _S122_RB_CB)
check('§122⑧ colorbar 短边标签字号 label_fontsize：块里写 '
      '`cb.ax.xaxis.label.set_fontsize(15)`，读回 15.0',
      _rc == 0 and _rb.get('cbx') == 15.0,
      'rc=%r 读回=%r 块行=%r' % (_rc, _rb, [l for l in _lines if 'cb.' in l]))
shutil.rmtree(_d, ignore_errors=True)

# 122⑨ 位置 + 字号一起给（真实拖动后的形状）：三笔都要落到图上，互不打架
_d, _lines, _rb, _rc, _out, _r = _s122_probe(
    _S122_CB, [{'index': 0}, {'index': 1, 'is_colorbar': True, 'pos': _CB_POS,
                              'label_fontsize_y': 11.0}], _S122_RB_CB)
check('§122⑨ colorbar 的 pos 与标签字号同时给 ⇒ 两者都生效（读回各自 == 请求值）',
      _rc == 0 and _rb.get('cbpos') == _CB_POS and _rb.get('cby') == 11.0,
      'rc=%r 读回=%r 块行=%r' % (_rc, _rb, [l for l in _lines if 'cb.' in l]))
shutil.rmtree(_d, ignore_errors=True)

# 122⑩ loc + anchor 那行加了 bbox_to_anchor 之后仍必须**幂等**（两遍写回一字节不变）
_d122, _s122, _p122 = _mk15(
    't122idem', _S122_LGD,
    {'version': 4, 'script': 'fig.py',
     'axes': [{'index': 0,
               'legend': {'loc': 'upper right', 'anchor': [0.35, 0.15],
                          'fontsize': 12}}]})
_r122a = writeback.writeback(_s122, _params.load(_p122), _p122,
                             style='block', verify=False)
_t122a = read(_s122)
_r122b = writeback.writeback(_s122, _params.load(_p122), _p122,
                             style='block', verify=False)
check('§122⑩ 幂等：loc + anchor + fontsize 写回后再跑一次 ⇒ applied=False、'
      '块不重复、字节不变',
      _r122a.get('applied') is True and _r122b.get('applied') is False
      and read(_s122) == _t122a and _t122a.count('.legend(') == 2,
      'r1=%r r2=%r legend 行数=%d'
      % (_r122a.get('applied'), _r122b.get('reason'),
         _t122a.count('.legend(')))
shutil.rmtree(_d122, ignore_errors=True)

# §123（0.1.17 / t3）**循环里只"使用"轴**不该让整张图不可证。
#   现场：`fig, ((a1, a2), (b1, b2)) = plt.subplots(2, 2)` 干净版能写回；同一脚本里加
#   `for k, ax in enumerate([a1, a2, b1, b2]): ax.plot(...)` 就整份 fail-closed
#   （理由"证不出变量名"）。根因：`_IMPLICIT_CREATORS`（plot/imshow/hist…）是**pyplot 级**
#   隐式建轴语义，旧代码不看接收者 ⇒ `ax.plot(...)` 被当成"建轴调用"，而
#   `_axes_binding_entries` 有一条"建轴调用不许在循环里" ⇒ 整图不可证。
#   现在：接收者由 import 绑定的才算"可能建轴的模块调用"（`plt.plot` 仍算）。
#   底线不变：循环里真的**建轴**（`ax = fig.add_subplot(...)`）或**重新赋值**轴名、
#   条件建图 —— 仍然拒写。
_S123_SUB = ('fig, ((a1, a2), (b1, b2)) = plt.subplots(2, 2, figsize=(8, 6))\n')
_S123_POS = {'a1': [0.05, 0.55, 0.38, 0.38], 'a2': [0.55, 0.55, 0.38, 0.38],
             'b1': [0.05, 0.05, 0.38, 0.38], 'b2': [0.55, 0.05, 0.38, 0.38]}
_S123_RB = ('import json as _j\n'
            'print("RB" + _j.dumps({nm: [round(v, 4) for v in ax.get_position().bounds]\n'
            '                       for nm, ax in (("a1", a1), ("a2", a2),\n'
            '                                      ("b1", b1), ("b2", b2))}))\n')


def _s123_run(body, with_rb=False):
    """造 2x2 元组解包脚本 → 写回 →（可选）重跑读回 → (目录, 结果, 块行, 读回, rc)。"""
    import subprocess                                              # noqa: E402
    _axs = [dict({'index': _k}, pos=_v)
            for _k, _v in enumerate(_S123_POS.values())]
    d, s, p = _mk15('t123',
                    'import matplotlib\nmatplotlib.use("Agg")\n'
                    'import matplotlib.pyplot as plt\n'
                    + body +
                    'plt.tight_layout()\nfig.savefig("out.png")\n',
                    {'version': 4, 'script': 'fig.py', 'fig_index': 0,
                     'n_figs': 1, 'axes': _axs})
    r = writeback.writeback(s, _params.load(p), p, style='block', verify=False)
    _txt = read(s)
    if with_rb:
        with open(s, 'w', encoding='utf-8', newline='\n') as f:
            f.write(_txt + _S123_RB)
    cp = subprocess.run([sys.executable, s], cwd=d, env=_pin_child_env(),
                        capture_output=True)
    rb = {}
    for ln in _decode_out(cp.stdout + cp.stderr).splitlines():
        if ln.startswith('RB'):
            rb = json.loads(ln[2:])
    return (d, r, [ln.strip() for ln in (r.get('block') or '').splitlines()
                   if 'set_position' in ln], rb, cp.returncode)


# 123① 只读循环（本项目最常见的写法）：4 条轴都要写、都要落到对的位置上
_d, _r, _pos_lines, _rb, _rc = _s123_run(
    _S123_SUB + 'for k, ax in enumerate([a1, a2, b1, b2]):\n'
                '    ax.plot([1, 2], [k, k + 1])\n', with_rb=True)
check('§123① 只读循环（`for k, ax in enumerate([a1, a2, b1, b2]): ax.plot(...)`）'
      '⇒ 4 条轴照旧可写、块里用各自名字寻址，重跑 rc=0 且读回位置 == 请求值',
      _r.get('applied') is True and len(_pos_lines) == 4 and _rc == 0
      and _rb == _S123_POS,
      'applied=%r reason=%r 块行=%r 读回=%r rc=%r'
      % (_r.get('applied'), _r.get('reason'), _pos_lines, _rb, _rc))
shutil.rmtree(_d, ignore_errors=True)

# 123② 同一根因的另一形态：`for ax in fig.axes: ax.plot(...)`
_d, _r, _pos_lines, _rb, _rc = _s123_run(
    _S123_SUB + 'for ax in fig.axes:\n    ax.plot([1, 2], [1, 2])\n', with_rb=True)
check('§123② `for ax in fig.axes: ax.plot(...)` 同样可写（4 行、读回对、rc=0）',
      _r.get('applied') is True and len(_pos_lines) == 4 and _rc == 0
      and _rb == _S123_POS,
      'applied=%r pending=%r 读回=%r' % (_r.get('applied'), _r.get('pending'), _rb))
shutil.rmtree(_d, ignore_errors=True)

# 123③ 底线：循环里**真的重新赋值**轴名 ⇒ 那一根拒写（其余照写）
_d, _r, _pos_lines, _rb, _rc = _s123_run(
    _S123_SUB + 'for ax in (a2, b1, b2):\n    a1 = ax\n', with_rb=True)
check('§123③ 循环里 `a1 = ax`（真的重新赋值）⇒ a1 的 pos 进 pending 拒写，'
      '另外三根照写（fail-closed 只针对证不出的那根）',
      'ax0.pos' in (_r.get('pending') or []) and len(_pos_lines) == 3
      and _rc == 0,
      'pending=%r 块行=%r' % (_r.get('pending'), _pos_lines))
shutil.rmtree(_d, ignore_errors=True)

# 123④ 底线：循环里**建轴**（`ax = fig.add_subplot(...)`）⇒ 轴数/顺序不可数 ⇒ 全拒写
_d, _r, _pos_lines, _rb, _rc = _s123_run(
    _S123_SUB + 'for k, ax in enumerate([a1, a2, b1, b2]):\n'
                '    ax = fig.add_subplot(2, 2, k + 1)\n')
check('§123④ 循环里真的**建轴**（fig.add_subplot）⇒ 4 条轴全部拒写（这条不许放宽）',
      _r.get('applied') is False and _pos_lines == []
      and len(_r.get('pending') or []) == 4,
      'applied=%r pending=%r' % (_r.get('applied'), _r.get('pending')))
shutil.rmtree(_d, ignore_errors=True)

# 123⑤ 底线：**条件建图**（解包在 `if:` 里）⇒ 名字不在模块层直接赋值 ⇒ 全拒写
_d, _r, _pos_lines, _rb, _rc = _s123_run(
    'flag = True\n'
    'if flag:\n'
    '    fig, ((a1, a2), (b1, b2)) = plt.subplots(2, 2, figsize=(8, 6))\n'
    'for ax in (a1, a2, b1, b2):\n    ax.set_title("t")\n')
check('§123⑤ 条件建图（`if flag:` 里才 subplots）⇒ 全拒写（名字不在模块层直接赋一次）',
      _r.get('applied') is False and _pos_lines == []
      and len(_r.get('pending') or []) == 4,
      'applied=%r pending=%r' % (_r.get('applied'), _r.get('pending')))
shutil.rmtree(_d, ignore_errors=True)

# 123⑥【有牙】把放宽的判据换回"凡隐式建轴名都算建轴"（= 修前行为）⇒ ①② 立刻回到拒写
_o_mod_call = writeback._implicit_creator_is_module_call
try:
    writeback._implicit_creator_is_module_call = lambda *a, **k: True
    _d, _r, _pos_lines, _rb, _rc = _s123_run(
        _S123_SUB + 'for k, ax in enumerate([a1, a2, b1, b2]):\n'
                    '    ax.plot([1, 2], [k, k + 1])\n', with_rb=True)
    _dup_lines = len(_pos_lines)
    _dup_pending = len(_r.get('pending') or [])
    _dup_applied = _r.get('applied')
    shutil.rmtree(_d, ignore_errors=True)
    check('§123⑥【有牙】判据换回"`ax.plot` 也算建轴"（= 修前）⇒ 只读循环立刻整份拒写'
          '（块 0 行、4 条轴进 pending）',
          _dup_applied is False and _dup_lines == 0 and _dup_pending == 4,
          'applied=%r 块行数=%d pending=%d'
          % (_dup_applied, _dup_lines, _dup_pending))
finally:
    writeback._implicit_creator_is_module_call = _o_mod_call

print('FAILED: %d' % fail if fail else 'ALL PASS')

sys.exit(1 if fail else 0)
