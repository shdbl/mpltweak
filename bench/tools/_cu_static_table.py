# -*- coding: utf-8 -*-
"""船长自算静态层主表（S1）：路径诚实性 + 能力缺口 + 承诺违约。
数据：verdicts/ 下的 scan_full.jsonl（静态扫描，不执行脚本；scan_rest 若存在会自动并入）。
用法：python _cu_static_table.py
"""
import collections
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.normpath(os.path.join(HERE, '..', 'verdicts'))
# 输入 = 本分支 verdicts/ 下**已脱敏**的逐脚本判定（源扫描由 harness/ 的脚手架产生）
SRCS = [os.path.join(OUT, f) for f in ('scan_full.jsonl', 'scan_rest.jsonl')
        if os.path.exists(os.path.join(OUT, f))]
FAMS = ['figsize_in', 'pos', 'title_fontsize', 'label_fontsize', 'label_fontsize_y',
        'tick_fontsize', 'tick_fontsize_y', 'grid', 'xlim', 'ylim']
CN = {'figsize_in': '画布尺寸 figsize', 'pos': '面板位置 pos', 'title_fontsize': '标题字号',
      'label_fontsize': '轴标签字号 x', 'label_fontsize_y': '轴标签字号 y',
      'tick_fontsize': '刻度字号 x', 'tick_fontsize_y': '刻度字号 y',
      'grid': '网格 grid', 'xlim': '轴范围 xlim', 'ylim': '轴范围 ylim'}


def load(path):
    rows = []
    if not os.path.exists(path):
        return rows
    with open(path, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except Exception:
                    pass
    return rows


def main():
    rows = []
    for s in SRCS:
        rs = load(s)
        print('  %s -> %d 行' % (os.path.basename(s), len(rs)))
        rows += rs
    # 去重（按 path）
    seen = {}
    for r in rows:
        seen[r.get('path')] = r
    rows = list(seen.values())
    print('合计（按 path 去重）= %d 行' % len(rows))

    err = collections.Counter(str(r.get('error')) for r in rows)
    print('\n各脚本状态：')
    print('  可分析(error=None) = %d' % err.get('None', 0))
    print('  no_figure          = %d' % err.get('no_figure', 0))
    syn = sum(v for k, v in err.items() if k.startswith('syntax'))
    print('  语法错             = %d' % syn)

    print('\n' + '=' * 128)
    print('静态层主表（S1）：分母分三层；"该原位"= 静态寻址模型判可达；"真原位成功"= 工具申报原位且字节确实变了')
    print('=' * 128)
    hdr = '%-16s %8s %8s %8s %8s %8s %8s %8s' % (
        '可调项', '有站点', '可达', '真原位', '退块', '拒绝', '承诺违约', '通过率')
    print(hdr)
    print('-' * len(hdr))
    tot = collections.Counter()
    for f in FAMS:
        has = reach = ok = block = ref = viol = 0
        for r in rows:
            nls = (r.get('n_literal_sites') or {}).get(f) or 0
            if not nls:
                continue
            has += 1
            nrs = (r.get('n_reach_sites') or {}).get(f) or 0
            if not nrs:
                continue
            reach += 1
            d = (r.get('families') or {}).get(f) or {}
            style = str(d.get('decl_style'))
            applied = d.get('decl_applied')
            changed = d.get('bytes_changed')
            rc = d.get('rc')
            if style == 'inplace' and applied is True and changed is True:
                ok += 1
            elif style == 'inplace' and applied is True and changed is False:
                viol += 1
            elif applied is False and rc not in (0, None):
                ref += 1
            elif style == 'block' or d.get('decl_needs_block') is True or d.get('decl_auto_fallback'):
                block += 1
            else:
                block += 1
        pct = ('**%.1f%%**' % (100.0 * ok / reach)) if reach >= 20 else ('样本不足(n=%d)' % reach)
        print('%-16s %8d %8d %8d %8d %8d %8d %14s' % (CN[f], has, reach, ok, block, ref, viol, pct))
        tot['has'] += has
        tot['reach'] += reach
        tot['ok'] += ok
        tot['block'] += block
        tot['ref'] += ref
        tot['viol'] += viol
    print('-' * len(hdr))
    pct = ('%.1f%%' % (100.0 * tot['ok'] / tot['reach'])) if tot['reach'] else '-'
    print('%-16s %8d %8d %8d %8d %8d %8d %14s' % (
        '合计', tot['has'], tot['reach'], tot['ok'], tot['block'], tot['ref'], tot['viol'], pct))

    print('\n解读：')
    print('  · **承诺违约**（工具说原位、字节却没变）＝ 必须为 0，否则是产品 bug；')
    print('  · **退块**（模型说可达，工具却追加了代码块）＝ 能力缺口，需逐族解释；')
    print('  · 拒绝 = rc≠0（坏参数等），属能力边界，不算失败。')


if __name__ == '__main__':
    main()
