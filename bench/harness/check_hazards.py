# -*- coding: utf-8 -*-
"""§9 用：① 13 条 M4 脚本里是否本来就有非线性刻度 ② 插入块处 fig 名字是否可解析
③ M1 的 11 条里哪些是 PEP 701（3.12+）语法。全部纯静态。"""
import ast
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import l0_harness as H

WORKF = os.path.join(H.HERE, 'work_final')
M4 = ['gallery/misc/logos2.py',
      'gallery/scikitlearn/classification/plot_classification_probability.py',
      'gallery/scikitlearn/model_selection/plot_successive_halving_heatmap.py',
      'zenodo/10635817__main.py', 'zenodo/17295616__correlation_plot.py',
      'zenodo/17295616__sla_contour.py', 'zenodo/18150811__correlation_plot.py',
      'zenodo/18508640__Zenodo_Santer_AGU_Advances_Matplotlib_Figure_03.py',
      'zenodo/20154193__Cylinder.py', 'zenodo/20154193__RealSpace.py',
      'zenodo/23013498__zndo_figs_pca.py', 'zenodo/23192393__figure_panels.py',
      'zenodo/5809269__O3_FYSP_paper.py']
M1 = ['zenodo/17778435__calculate_sea_ice_velocity_field.py',
      'zenodo/18790272__linear_design_integration.py',
      'zenodo/18790272__platform_summary.py',
      'zenodo/21509938__Word-Cloud-Generation.py',
      'zenodo/22099105__analysis_pisa2022.py',
      'zenodo/22761293__poparea_fit.py',
      'zenodo/23003706__cuda_edge_common_preserved_logic.py',
      'zenodo/23114431__build_si_readable.py',
      'zenodo/23252566__Bootstrap_Code.py',
      'zenodo/23252566__Sampling_Frequency_Distributions_Plotter.py',
      'zenodo/23252566__TIC_TOC_and_Base_Sat_Plotter.py']
SCALE_RE = re.compile(r'\b(semilogx|semilogy|loglog|set_xscale|set_yscale|'
                      r'\.xscale|\.yscale|log=True)\b')
out = []
out.append('===== ① 13 条 M4 脚本里的刻度写法（会不会被注入的 linear 覆盖）=====')
for p in M4:
    fp = os.path.join(H.CORPUS, p.replace('/', os.sep))
    text, _ = H.decode_source(H.read_bytes(fp))
    hits = []
    for i, ln in enumerate(text.split('\n'), 1):
        if SCALE_RE.search(ln):
            hits.append('L%-5d %s' % (i, ln.strip()[:100]))
    out.append('- %s：%s' % (p, ('%d 处刻度写法' % len(hits)) if hits else '**没有任何非线性刻度写法**'))
    for h in hits[:6]:
        out.append('    %s' % h)
out.append('')
out.append('===== ② 插入块的作用域与 fig 名字解析（静态）=====')
for p in M4:
    stem = os.path.splitext(os.path.basename(p))[0]
    d = os.path.join(WORKF, stem, 'directed.py')
    if not os.path.exists(d):
        out.append('- %s：工作副本缺失' % p)
        continue
    after, _ = H.decode_source(H.read_bytes(d))
    lines = after.split('\n')
    idx = [i for i, ln in enumerate(lines) if H.BLOCK_SENTINEL in ln]
    if not idx:
        out.append('- %s：无块' % p)
        continue
    i0 = idx[0]
    indent = len(lines[i0]) - len(lines[i0].lstrip())
    block = lines[i0:i0 + 12]
    var_use = [ln.strip() for ln in block if 'axes' in ln]
    # 找块所在作用域：往上找最近的缩进更小的 def/for/if 行
    scope = 'module'
    for j in range(i0 - 1, -1, -1):
        s = lines[j]
        if not s.strip():
            continue
        ind = len(s) - len(s.lstrip())
        if ind < indent and re.match(r'\s*(def |class |async def )', s):
            scope = s.strip()[:70]
            break
    # 该作用域内是否有 fig 赋值
    fig_bind = []
    for j, s in enumerate(lines):
        if re.match(r'\s*(fig|Figure)\s*=', s):
            fig_bind.append((j + 1, s.strip()[:60]))
    out.append('- %s' % p)
    out.append('    块缩进=%d，所在作用域=%s' % (indent, scope))
    out.append('    块内引用：%s' % json.dumps(var_use, ensure_ascii=False))
    out.append('    文件里 fig/Figure 赋值共 %d 处，最近的在块之前：%s' % (
        len(fig_bind),
        next((str(ln) + ' ' + s for ln, s in reversed(fig_bind) if ln < i0 + 1), '无')))
out.append('')
out.append('===== ③ M1 的 11 条：PEP 701 / 其它 =====')
for p in M1:
    fp = os.path.join(H.CORPUS, p.replace('/', os.sep))
    raw = open(fp, 'rb').read()
    txt = raw.decode('utf-8', 'replace')
    try:
        ast.parse(raw)
        out.append('- %s: 解析成功（不应出现）' % p)
        continue
    except SyntaxError as e:
        msg = '%s @L%s' % (e.msg, e.lineno)
    pep701 = ('backslash' in msg) or ("unmatched '['" in msg) or ("unmatched" in msg and 'f-string' in msg)
    out.append('- %s: %s   → %s' % (p, msg, 'PEP 701（f-string 内引号/反斜杠，需 Python 3.12+）'
                                    if pep701 else '真语法错'))
print('\n'.join(out))
with open(os.path.join(H.HERE, '_hazard.txt'), 'w', encoding='utf-8') as f:
    f.write('\n'.join(out) + '\n')
