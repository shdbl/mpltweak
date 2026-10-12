# -*- coding: utf-8 -*-
# writeback 引擎 golden 测试（自包含：临时目录现场造脚本 + 参数 JSON）：
#   1. 零 LLM 写回：fig 变量解析 / savefig 前插入 / figsize 原位替换
#   2. colorbar 轴用 _colorbar.mappable 结构导航（不依赖首次 draw）
#   3. 幂等：重复写回 = 整体替换旧块，不叠加
#   4. 安全网：写回后 Agg 重跑失败 → 自动回滚备份
import os
import io
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
# 名字）⇒ 块里找不到可证的目标对象 ⇒ 它的 pos/clim/cmap 按 fail-closed 不写、如实进
# pending。ax0/ax1 照常落地 ⇒ reason 从 ok 变成**诚实**的 best_effort（不是退步：
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
# 也没有 close ⇒ `plt.gcf()` 就是那张图、`.axes[k]` 就是静态第 k 条轴 ⇒ **允许写块**，
# 并在块注释里写明"凭什么可证"。这一档在原 census 里是 B 档（403/1158）的主体，
# 一刀切拒掉换不到任何安全性。
check('T70-B 纯 pyplot 单图：可证 ⇒ 照常写块、验证通过',
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
# 对照：多图（§6）与循环（§9）条件不成立 ⇒ 仍然拒绝（见下面两节）

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
# `plt.figure(...)` ⇒ 第 2 张就是编号 2）、且模块层没有 close、没有额外建轴来源 ⇒
# 块改用 `plt.figure(2).axes[0]` 定位（T70 时代的"一律拒绝"被这条可证路径救回）。
# 注意：仍然**不许**出现 `plt.gcf()` / `plt.gca()` / `fig.axes[i]`（运行期下标）。
_src6 = read(script6)
_blk6 = (_src6.split(writeback.BLOCK_START)[-1].split('end auto layout')[0]
         if writeback.BLOCK_START in _src6 else '')
check('T74 多图 + 未命名 figure：编号可证 ⇒ 块用 plt.figure(2).axes[0]（块内无运行期下标）',
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
# 又是 pyplot 隐式建轴（认不出条数）⇒ 目标证不出 ⇒ fail-closed 不写盘。
# 旧的 `if i == 2:` 循环守卫因此对**这种形态**不再可达（守卫逻辑仍在，但没有可证的
# 目标对象）；这是本任务有意的能力收窄，详见报告 §T70。
check('T70 循环里 pyplot 建图建轴：目标证不出 ⇒ 拒绝写（不插块、字节不变）',
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
#   脚本 mtime = now-10s，两份参数 = now-5s（参数比脚本新 ⇒ 合法，不该拦）；
#   旧实现里第一份写回后脚本变成 now ⇒ 第二份被判 stale（差 5s > 1s 容差）→ 用例必失败。
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
# T73（零兼容）：参数只给了 x ⇒ y 标签字号**不许**被写成 x 的值（保持源码原值）
check('中文：只给 x ⇒ y 标签字号保持源码原值（T73 零兼容）',
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
#   用 'utf-8-sig' **写入时会自动加 BOM** → 用户的脚本被改脏，compile() 立刻报
#   invalid non-printable character U+FEFF。七套件全都查不出来（它们不查 BOM），
#   是被 t1 的验收脚本"落盘语法非法=True"照出来的。
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
# T73（零兼容）：没有 label_fontsize_y ⇒ **一个字节都不动 y**（旧期望是"y 也写成 14"）
check('49b T73：无 label_fontsize_y ⇒ x 写 14、y 保持源码原值 11',
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
check('49d T73：块模式无 y ⇒ 只写 x 那一行（y 不出现）',
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

# 53h 反向：值**确实不同**的同类字段必须退块落地（默认值证明不许把真差异也吞掉）
for _f, _v, _mark in (('title_fontsize', 20.0, 'set_fontsize(20)'),
                      ('spines', {'top': False}, "'top'"),
                      ('tick_fontsize', 7.0, 'labelsize=7')):
    _ax = {'index': 0, 'pos': [0.1, 0.1, 0.8, 0.8], _f: _v}
    _d, _s, _p = _mk15('t15d1n', _ADD_SRC,
                       {'version': 4, 'script': 'fig.py', 'axes': [_ax]})
    _rr = subprocess.run([sys.executable, '-m', 'mpltweak', 'apply', _s, '--params', _p,
                          '--write', '--verify-fast', '--json'],
                         capture_output=True, cwd=_d, env=_env15)
    _oo = _decode_out(_rr.stdout + _rr.stderr)
    check('53h 反向：%s=%r 与现值不同 → 退块落地（含 %s）' % (_f, _v, _mark),
          _rr.returncode == 0 and writeback.BLOCK_START in read(_s)
          and _mark in read(_s), _oo[-260:])
    shutil.rmtree(_d, ignore_errors=True)

# 53i 默认值证明的**门**：源码里出现能改该字段的写法（rcParams）→ 不许证明，保守退块
_GATE_SRC = _ADD_SRC.replace('ax.plot([1, 2, 3])',
                             "plt.rcParams['axes.titlesize'] = 12\nax.plot([1, 2, 3])")
_d, _s, _p = _mk15('t15gate', _GATE_SRC,
                   {'version': 4, 'script': 'fig.py',
                    'axes': [{'index': 0, 'pos': [0.1, 0.1, 0.8, 0.8],
                              'title_fontsize': 12.0}]})
_r53i = writeback.writeback(_s, _params.load(_p), _p, verify=False,
                            fast_check=True, style='inplace')
check('53i 源码里有 rcParams 改字号 → 放弃默认值证明（保守退块，不静默吞）',
      _r53i.get('needs_block') is True, 'nb=%r' % _r53i.get('needs_block'))
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
#   且两份参数都早于"第一张写回后的脚本" ⇒ 旧实现必判第二份 stale。
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
# 56b 真 stale：脚本确实在参数保存之后被手改 ⇒ 仍要拦住，且理由与事实相符
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

#   59b --verify-fast（只查语法）：写回语法合法 ⇒ 保留 + rc=0，但必须如实标注
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
#   ⇒ **对数轴被改成线性**（L0 最终复测：23192393__figure_panels / 5809269__O3_FYSP_paper）。
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


# ---- 68. T58：重名轴 + 部分覆盖 ⇒ 任何模式都不许"写错行并被保留"（9 格钉子）----
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

# 69b 干净往返不破功：有字面量且等值 ⇒ no_change 且字节不变
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


# 69c（T60 / Bug 6 v2）：无 figsize 字面量 + 参数 == 脚本实际会用的尺寸 ⇒ 必须 no_change，
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

# 69c-v2：脚本里显式 fig.set_size_inches(6.4, 4.8)、参数与之相等 ⇒ 同样 no_change
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
#   reason=bad_params ⇒ apply rc=2）。
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
# （a）声称"无需改动"（reason=no_change 且未写盘）⇒ 必须证明脚本**生效尺寸已经==目标**
#      （独立读取器读不出 ⇒ 未知 ⇒ FAIL）；**P3'/P7 修前正是这一支**（假等值 ⇒ 静默 no_change）。
# （b）没写盘、也没说 no_change ⇒ 必须**明确申报**退块（needs_block=True 或 pending 非空），否则同样是静默丢改动；
# （c）真写了盘 ⇒ 改完后生效尺寸必须==目标。
# 生效尺寸用**本测试自带的独立读取器**（绝不调产品函数 _figsize_from_source，避免自证）。
def _eff_size(src):
    """独立读取脚本的**生效尺寸**：所有可读字面量按 lineno 取最后一条；有尺寸语句但读不出 ⇒ None；
    完全没有尺寸语句 ⇒ matplotlib 默认值。"""
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
        return None                      # 有尺寸语句但读不出 ⇒ 未知
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
check('69e-R1 读取器：figsize= 字面量 ⇒ [9.0, 5.0]', _hit(_eff_size(_P1 % _H69E), [9.0, 5.0]),
      str(_eff_size(_P1 % _H69E)))
check('69e-R2 读取器：后置 set_size_inches 覆盖 ⇒ [4.0, 3.0]',
      _hit(_eff_size(_P3H % _H69E), [4.0, 3.0]), str(_eff_size(_P3H % _H69E)))
check('69e-R3 读取器：变量尺寸（有语句但读不出）⇒ None（未知）',
      _eff_size(_P7 % _H69E) is None, str(_eff_size(_P7 % _H69E)))
check('69e-R4 读取器：完全没有尺寸语句 ⇒ rcParams 默认 [6.4, 4.8]',
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
        _ok = _hit(_eff, _fs)                       # 空口宣称 ⇒ 必须证明本来就对
    elif _same:
        _kind = '未写盘+已申报退块'
        _ok = bool(_nb is True or _pend)            # 弃权可以，但必须说清楚
    else:
        _kind = '声称已改（已写盘）'
        _ok = _hit(_eff, _fs)                       # 真改了 ⇒ 改完必须对
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
#      `ast.Assign` 上（(a)~(d) 四条路径都够不到"内联"的 add_axes）⇒ 判"证不出" ⇒
#      best_effort ⇒ auto 退块 ⇒ 插一段
#      `for _ax, _p in zip(fig.axes, [[0.78, 0.18, 0.2, 0.03]]): _ax.set_position(_p)`。
#      而那个脚本运行期有 **12** 条轴（11 个面板由 `for i in range(11):
#      plt.subplot(3,4,i+1)` 循环建出 + 色条 cax 是第 12 条）：`fig.axes[0]` 是**主图**，
#      不是参数里记的那条 cax。步骤 1 实测：块执行前 fig.axes[0].pos=
#      (0.0656, 0.7086, 0.1698, 0.2483)、cax=(0.78, 0.18, 0.2, 0.03)；块执行后
#      fig.axes[0]=(0.78, 0.18, 0.2, 0.03)、cax 一个数都没动 ⇒ **把主图改成了色条的几何**。
#   C  同一参数两次 apply 不幂等：语料 `big/zenodo/21785935__03_ml_pipeline.py`
#      （136 KB / 20 张图）第 1 次原位把 `fig = plt.figure(figsize=(18, 12))` 改成 (6, 4)；
#      第 2 次被**另一张图**的 `fig_bee.set_size_inches(10, 7)` 误判成"目标尺寸已被覆盖"
#      ⇒ 等值证明失败 ⇒ auto 退块又插一遍 `plt.gcf().set_size_inches(6, 4)`（+5 行）。
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

# ---- 71①（A 的主症状）：值相等的内联 cax pos ⇒ 判"本来就对"，一个字节都不动 ----
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
check('71① auto 全流程：值相等 ⇒ 字节不变、无调整块、无 set_position',
      _rc71a == 0 and _t71a == _S71_A
      and writeback.BLOCK_START not in _t71a and 'set_position' not in _t71a,
      'rc=%s | %s' % (_rc71a, [_l for _l in _t71a.splitlines()
                              if 'position' in _l or 'auto layout' in _l]))
shutil.rmtree(_d71a, ignore_errors=True)

# ---- 71①b（A 的第二症状）：值不同 + 循环建轴 ⇒ 块内目标轴证不出 ⇒ fail-closed ----
#   旧实现在这里会插 zip 块，把位置写到运行时第一张轴（主图）上 —— 宁可不改。
_d71b, _s71b, _p71b = _mk15('t67b', _S71_A,
                            {'version': 4, 'script': 'fig.py',
                             'axes': [{'index': 0,
                                       'pos': [0.10, 0.10, 0.50, 0.50]}]})
_r71b = writeback.writeback(_s71b, _params.load(_p71b), _p71b, verify=False,
                            fast_check=True, style='inplace')
check('71①b 值不同 + 循环建轴：原位写不了 ⇒ best_effort + needs_block（不写盘）',
      _r71b.get('reason') == 'best_effort' and _r71b.get('needs_block') is True
      and _r71b.get('applied') is False and read(_s71b) == _S71_A,
      'reason=%r nb=%r applied=%r' % (_r71b.get('reason'),
                                      _r71b.get('needs_block'),
                                      _r71b.get('applied')))
_rc71b = apply.main([_s71b, '--params', _p71b, '--write', '--verify-fast'])
_t71b = read(_s71b)
check('71①b auto 退块时 fail-closed：不许把位置写到 fig.axes[0]（字节不变、不插块）',
      _rc71b == 0 and _t71b == _S71_A
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
# 缺陷：块通道的人读行只看 `reason` 不看 `applied` ⇒ "拒绝写回"的路径
# （applied=false，例如 T67 的"运行时轴数证不出 ⇒ 不写位置块"fail-closed）也打
# `[OK] 已写回: <脚本>`；bad_params 入口拒写只有一句"已拒写"、没有统一的"未写回"行。
# JSON 一直是诚实的（ok/applied/needs_block/pending 都对），坏的只有人读那一行。
# 修法（apply.py + messages.py）：原位/块两条人读通道都按 `applied`（且非 dry-run）决定；
# 未写盘 → `未写回[<reason>]：脚本未被改动（<脚本>）`（中英走 messages 表）。
# 反向验证（fix018/t68/t68_rev.py）：关掉这两处判定（恢复无条件 written_block +
# 删掉 bad_params 那行）⇒ 72① / 72② 必须 FAIL。
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


# 72① 坏参数（params.validate_errors 命中）⇒ rc=2、字节不变、人读不得说"已写回"
_rc72a, _o72a, _t72a = _s72_run(
    _GRID_SRC, {'version': 4, 'script': 'fig.py',
                'axes': [{'index': 0, 'pos': [0.1, 0.2, 0.3]}]})
check('72① 坏参数：rc=2、未写盘、人读不得出现「已写回」，须如实「未写回」+ 原因码',
      _rc72a == 2 and '已写回' not in _o72a and '未写回' in _o72a
      and 'bad_params' in _o72a and _t72a == _GRID_SRC,
      'rc=%s | %s' % (_rc72a, [_l for _l in _o72a.splitlines() if '写回' in _l]))

# 72② 块目标证不出（applied=false + needs_block=true，T67 的 fail-closed）⇒ 同上
_rc72b, _o72b, _t72b = _s72_run(
    _S71_A, {'version': 4, 'script': 'fig.py',
             'axes': [{'index': 0, 'pos': [0.10, 0.10, 0.50, 0.50]}]})
check('72② 块目标证不出（一个字节都没写）：人读不得说「已写回」，须「未写回」+ 原因码',
      _rc72b == 0 and '[OK] 已写回' not in _o72b and '未写回' in _o72b
      and 'best_effort' in _o72b and _t72b == _S71_A,
      'rc=%s | %s' % (_rc72b, [_l for _l in _o72b.splitlines() if '写回' in _l]))

# 72③a 正常**原位**写盘 ⇒ 人读仍要说"已原位写回"（不许因为本次修正而少说）
_rc72c, _o72c, _t72c = _s72_run(
    _S72_IP, {'version': 4, 'script': 'fig.py', 'figsize_in': [6.0, 4.0],
              'fig_index': 0, 'n_figs': 1, 'axes': []})
check('72③a 正常原位写盘：人读仍明确「已原位写回」，且数字真的落地',
      _rc72c == 0 and '已原位写回' in _o72c and '未写回' not in _o72c
      and 'figsize=(6, 4)' in _t72c,
      'rc=%s | %s' % (_rc72c, [_l for _l in _o72c.splitlines() if '写回' in _l]))

# 72③b 正常**追加调整块** ⇒ 人读仍要说"已写回"，且块真进文件
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
#   ① 目标对象自己的变量名 / 单次字面量 `subplots` 容器的元素 ⇒ 可证，块里就用它；
#   ② 纯 pyplot 单图（全文件只有一次建图、无 colorbar/inset/twin/close）⇒
#      `plt.gcf().axes[k]` 也可证（块注释里写明凭什么可证）；
#   ③ 其余（名字出作用域 / 循环里建图建轴 / 多图没名字 / 未命名 colorbar 轴）⇒ 目标
#      证不出 ⇒ **拒绝写**：不插块、字节不变、applied=false + needs_block + pending，
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
check('73① 目标可证 ⇒ 块用变量名，块内无运行期下标/动态定位',
      'ax.set_xlim(0, 4)' in _blk73a
      and not any(k in _blk73a for k in ('fig.axes[', 'plt.gcf()', 'plt.gca()',
                                         'get_axes()[', 'zip(')),
      _blk73a[:160])
check('73① 成功路径没退步：真写盘（applied=True 且块进文件）',
      _r73a.get('applied') is True and writeback.BLOCK_START in read(_s73a),
      'applied=%r' % (_r73a.get('applied'),))
shutil.rmtree(_d73a, ignore_errors=True)

# ② 目标不可证：图与轴建在函数里，块的锚点在模块层 ⇒ 名字在块位置**没有绑定**
_SRC73B = ('import matplotlib\nmatplotlib.use("Agg")\n'
           'import matplotlib.pyplot as plt\n\n\n'
           'def draw():\n'
           '    fig, ax = plt.subplots(figsize=(6, 3))\n'
           '    ax.plot([1, 2, 3])\n'
           '    return fig\n\n\n'
           'fig = draw()\n'
           'fig.savefig("o.png")\n')
_d73b, _s73b, _p73b = _mk15('t73b', _SRC73B, _PAR73A)
_r73b = writeback.writeback(_s73b, _params.load(_p73b), _p73b, verify=False,
                            fast_check=True, style='block')
check('73② 目标不可证 ⇒ 拒绝写：不插块 + 字节不变 + applied=false + pending 非空',
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
#   （`d.figure_.set_size_inches(10, 6)`）误判成"本图被覆盖" ⇒ `figsize_already_ok` False
#   ⇒ 第二次 apply 又插块、又改字节，并**重写固定名备份** ⇒ revert 只能回到中间态；
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
# ⇒ 备份必须仍是**批前原件**（而不是上一次写回后的中间态）。
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

# ---- 77. T73 零兼容：参数里没写 y ⇒ **一个字都不动 y** ----
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
check('77① 只给 x ⇒ y 侧 set_ylabel 逐字节不变、changes 不含 y 侧',
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
check('77② x/y 都给且都不同 ⇒ 两侧都改、changes 两侧都报',
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
check('77③ 窗口风格参数（x/y 都给且已等于源码）⇒ no_change、两边都不动',
      _r77c.get('reason') == 'no_change'
      and 'fontsize=9' in read(_S77c) and 'fontsize=20' in read(_S77c),
      'reason=%r' % (_r77c.get('reason'),))
shutil.rmtree(_D77, ignore_errors=True)
shutil.rmtree(_D77b, ignore_errors=True)
shutil.rmtree(_D77c, ignore_errors=True)

# ---- 78. T75b：describe 补 y 侧字号字段 + verify **真的判 y** -----------------
#   T73 删掉了"参数没写 y ⇒ 沿用 x"的兼容回退（零兼容），随之暴露两个缺口：
#   ① `describe` 只导出 x 侧三个字号字段 ⇒ agent 读不到 y 侧、也不知道能设；
#   ② `verify.compare` 只比 x 侧 ⇒ **纯 y 改动在语义层不被判定**（"没比却说通过"）。
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

# 78④ 只有 x 的参数 ⇒ y 一个字节都不动（T73 不变量）
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
#   模块层没有 close ⇒ 它**就是**目标图。
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
check('74① 多图编号可证 ⇒ 块用 plt.figure(2).axes[0]，且不含 gcf/gca/fig.axes[',
      'plt.figure(2).axes[0]' in _src74a
      and 'plt.gcf()' not in _src74a and 'plt.gca()' not in _src74a
      and 'fig.axes[' not in _src74a,
      str([ln.strip() for ln in _src74a.splitlines() if '.axes[' in ln]))
# ②a `close(2)` ⇒ 编号会被复用/重置 ⇒ 必须拒绝（字节不变 + pending + 不插块）
_D74b, _S74b, _P74b = _mk15(
    't74', _SRC74.replace('plt.savefig', 'plt.close(2)\nplt.savefig'), _PAR74)
_prior74b = read(_S74b)
_r74b = writeback.writeback(_S74b, _params.load(_P74b), _P74b, verify=False,
                           fast_check=True, style='block')
check('74② 有 close(2) ⇒ 拒绝写（字节不变 + pending 非空 + 不插块）',
      _r74b.get('applied') is False and bool(_r74b.get('pending'))
      and read(_S74b) == _prior74b
      and writeback.BLOCK_START not in read(_S74b),
      'applied=%r pending=%r' % (_r74b.get('applied'), _r74b.get('pending')))
# ②b 循环里建图 ⇒ 编号不可枚举 ⇒ 拒绝
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
check('74② 循环里建图 ⇒ 编号不可枚举 ⇒ 拒绝写（字节不变 + pending 非空）',
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
# （a）声称"无需改动"（reason=no_change 且未写盘）⇒ 必须证明脚本**生效尺寸已经==目标**
#      （独立读取器读不出 ⇒ 未知 ⇒ FAIL）；**P3'/P7 修前正是这一支**（假等值 ⇒ 静默 no_change）。
# （b）没写盘、也没说 no_change ⇒ 必须**明确申报**退块（needs_block=True 或 pending 非空），否则同样是静默丢改动；
# （c）真写了盘 ⇒ 改完后生效尺寸必须==目标。
# 生效尺寸用**本测试自带的独立读取器**（绝不调产品函数 _figsize_from_source，避免自证）。
def _eff_size(src):
    """独立读取脚本的**生效尺寸**：所有可读字面量按 lineno 取最后一条；有尺寸语句但读不出 ⇒ None；
    完全没有尺寸语句 ⇒ matplotlib 默认值。"""
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
        return None                      # 有尺寸语句但读不出 ⇒ 未知
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
check('69e-R1 读取器：figsize= 字面量 ⇒ [9.0, 5.0]', _hit(_eff_size(_P1 % _H69E), [9.0, 5.0]),
      str(_eff_size(_P1 % _H69E)))
check('69e-R2 读取器：后置 set_size_inches 覆盖 ⇒ [4.0, 3.0]',
      _hit(_eff_size(_P3H % _H69E), [4.0, 3.0]), str(_eff_size(_P3H % _H69E)))
check('69e-R3 读取器：变量尺寸（有语句但读不出）⇒ None（未知）',
      _eff_size(_P7 % _H69E) is None, str(_eff_size(_P7 % _H69E)))
check('69e-R4 读取器：完全没有尺寸语句 ⇒ rcParams 默认 [6.4, 4.8]',
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
        _ok = _hit(_eff, _fs)                       # 空口宣称 ⇒ 必须证明本来就对
    elif _same:
        _kind = '未写盘+已申报退块'
        _ok = bool(_nb is True or _pend)            # 弃权可以，但必须说清楚
    else:
        _kind = '声称已改（已写盘）'
        _ok = _hit(_eff, _fs)                       # 真改了 ⇒ 改完必须对
    check('%s 不变量[%s]：生效尺寸 %s vs 目标 %s' % (_tag, _kind, _eff, _fs), _ok,
          'reason=%r nb=%r pending=%r bytes_same=%s eff=%s'
          % (_rsn, _nb, _pend, _same, _eff))
    shutil.rmtree(_d, ignore_errors=True)


# ---- 79. T78：tick_fontsize 原位写回必须按 axis 过滤（不许改到 axis='y' 那条）----
# 实测来源：zenodo `22254023__figure_lib.py`（第 812 行 axis='y' 在前、第 833 行 axis='x' 在后）。
# 旧代码用不过滤 axis 的 _find_call ⇒ 命中最早的 812（y）改成目标值，而该改的 833（x）没动，
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

# 79④ axis= 是**变量** ⇒ 证不出 ⇒ 不许乱猜（那条字面量必须原样不动）
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

# ---- 84. T84（blocker，Codex r4 §2.3）：同图**重名轴** ⇒ 原位写回禁止改别的轴 ----
#   `fig, (ax, ax) = plt.subplots(1, 2)`：名字 ax 在运行期只认**最后一次**解包（第二条轴），
#   而 `_find_call` 只能按名字取"最早那次调用" ⇒ 轴 0 的字段会被写到**第二条轴**那行。
#   T57 的判据建立在 `axes_calls`（只收 add_axes/add_subplot）上，**看不见同名解包** ⇒ 漏。
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
check('84① 同图重名轴 + 参数只给 index=0 ⇒ 禁止原位写回（applied=false / pending 非空）',
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
check('84② 对照：参数覆盖全部被绑定轴 ⇒ 允许原位写（applied=true、字节变了）',
      _f84b.get('applied') is True and _a84b != _b84b,
      'reason=%r applied=%r pending=%r' % (_f84b.get('reason'), _f84b.get('applied'),
                                           _f84b.get('pending')))
shutil.rmtree(_d84b, ignore_errors=True)

print('FAILED: %d' % fail if fail else 'ALL PASS')
sys.exit(1 if fail else 0)


