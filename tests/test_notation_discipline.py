# -*- coding: utf-8 -*-
"""tests/test_notation_discipline.py —— 记号纪律检查器自己的套件（0.1.11）

被检查的东西在 ``tools/notation_discipline.py``（只报告、不修复）。本套件做两件事：

1. **teeth**：对**合成样例**注入已知违规 ⇒ 检查器必须报出；同时注入
   **已知不该报**的样例（注释/docstring 里的禁用字形、受准的 ``√`` / ``×``）⇒
   必须**一处都不报**。后者比前者更重要：误报会让检查器废掉（见文件头判据）。
2. **真仓库**：跑一遍全仓，要求 ``src/mpltweak/``（产品面 = 用户控制台）零违规。
   非产品面的命中里，凡文件自带控制台兜底（``reconfigure(errors=``）的归为
   **刻意例外**，逐条打印，不判红。

为什么单独一个套件、而不是塞进 ``tests/test_hygiene.py``：hygiene 的两条棘轮只管
``messages.py`` 模板表与「测试套件有没有装兜底」，管不到 ``src/`` 里**直接 print 出去**
的字符串，也不管记号字形。分工写在 ``tools/notation_discipline.py`` 文件头。

口径：``MAINTENANCE.md:336``（§8.5 微软雅黑缺 ``✓`` U+2713 / ``↺`` U+21BA 的字形 →
用 ``√`` U+221A / ``×`` U+00D7``）+ ``CONTRIBUTING.md:71-74``（终端字符串必须 GBK 可编码）
+ ASCII ``[OK]`` / ``[X]``。
"""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOL = os.path.join(ROOT, 'tools', 'notation_discipline.py')
PY = sys.executable

# 控制台兜底（T13/T15 同款；也被 tests/test_hygiene.py 的 GBK 棘轮认这个标记）
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(errors='replace')
    except (AttributeError, ValueError, OSError):
        pass

_fails = []


def check(name, cond, detail=''):
    mark = 'OK' if cond else 'FAIL'
    print('%s | %s%s' % (mark, name, (' | ' + str(detail)) if detail else ''))
    if not cond:
        _fails.append(name)


def _run_tool(root):
    """跑检查器（CLI 真身，不是 import 内部函数）→ (findings, rc)。"""
    env = dict(os.environ, PYTHONIOENCODING='utf-8', MPLBACKEND='Agg')
    r = subprocess.run([PY, TOOL, '--root', root, '--json'],
                       capture_output=True, env=env, cwd=ROOT)
    out = r.stdout.decode('utf-8', 'replace')
    try:
        findings = json.loads(out)
    except ValueError:
        findings = None
    return findings, r.returncode, out


def _load_tool():
    """按**文件路径**加载检查器模块（判据级牙齿要直接调 `rules_for()`）。

    为什么不 `import notation_discipline`：`tools/` 不是包、也不在 sys.path 上；
    按路径加载不依赖调用者的 cwd / PYTHONPATH（与 test_msg_key_audit 的 `M` 同一思路，
    只是那边 `sys.path.insert` 了 tools/，这边用 importlib 以免污染 sys.path）。
    """
    import importlib.util
    spec = importlib.util.spec_from_file_location('_notation_under_test', TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _encodable(ch):
    """该字符能否编进 GBK（**真编码**，失败即不合规）。"""
    try:
        ch.encode('gbk')
        return True
    except UnicodeEncodeError:
        return False


def _write(root, rel, text):
    p = os.path.join(root, rel.replace('/', os.sep))
    d = os.path.dirname(p)
    if d and not os.path.isdir(d):
        os.makedirs(d)
    with io.open(p, 'w', encoding='utf-8', newline='\n') as f:
        f.write(text)
    return p


def _hits(findings, rel, rule=None):
    out = [f for f in findings if f['rel'].replace('\\', '/') == rel]
    if rule is not None:
        out = [f for f in out if f['rule'] == rule]
    return out


def _fmt(findings):
    return ['%s:%d %s %s' % (f['rel'], f['line'], f['codepoint'], f['rule'])
            for f in findings]


# ---------------------------------------------------------------------- teeth
def teeth():
    print('---- teeth：合成样例注入已知违规 / 已知不该报 ----')
    tmp = tempfile.mkdtemp(prefix='t15_notation_')
    try:
        # ① 已知**不该报**：注释 + docstring + 受准字形 + 普通中文
        _write(tmp, 'clean.py',
               '# -*- coding: utf-8 -*-\n'
               '"""模块 docstring 里写 \u2713 \u21ba 也不算输出串（本检查器不判）。"""\n'
               '\n'
               '\n'
               'def helper():\n'
               '    """docstring: \u2713 \u26a0 \u21d2 都不该被报。"""\n'
               '    # 注释：\u2713 done / \u21d2 结论\n'
               '    return 1\n'
               '\n'
               '\n'
               'def go():\n'
               '    ok = "\u221a 已通过"\n'
               '    bad = "\u00d7 失败"\n'
               '    print(ok, bad)          # 受准字形：\u221a / \u00d7\n'
               '    x = "\u2713"               # 不是输出串（只是赋值）\n'
               '    print(x)\n'          # ← 这个**是**输出串，见 ② 的边界
               )
        # 上面最后两行其实是输出串（print(x) 的实参是一个变量，不是字面量）
        # —— 字面量在赋值语句里、不在 print 实参里，所以**不该**被报。
        findings, rc, out = _run_tool(tmp)
        check('teeth 01 检查器能跑通并输出合法 JSON（rc 只反映真违规）',
              findings is not None, 'rc=%s 输出头=%r' % (rc, out[:120]))
        check('teeth 02 【误报守卫】注释 / docstring 里的 U+2713 U+21BA U+26A0 U+21D2 一处都不报'
              '（**不是因为它们"合规"** —— 注释不上终端；同款字符进了输出串就要报，'
              '见 teeth 06 / 18-22）',
              not _hits(findings or [], 'clean.py'),
              '实得 %s' % _fmt(_hits(findings or [], 'clean.py')))
        check('teeth 03 【误报守卫】受准字形 \u221a(U+221A) / \u00d7(U+00D7) 不报',
              not [f for f in (findings or []) if f['char'] in ('\u221a', '\u00d7')],
              '实得 %s' % _fmt([f for f in (findings or [])
                                if f['char'] in ('\u221a', '\u00d7')]))

        # ② 直接 print 里的禁用字形 ⇒ R1 + R2 都要报
        _write(tmp, 'bad_mark.py',
               '# -*- coding: utf-8 -*-\n'
               'print("\u2713 已写回")\n')
        findings, _rc, _out = _run_tool(tmp)
        hm = _hits(findings or [], 'bad_mark.py')
        check('teeth 04 print() 里的 U+2713 必须报（R1 记号 + R2 GBK 至少各一条）',
              any(f['rule'] == 'mark' for f in hm) and any(f['rule'] == 'gbk' for f in hm)
              and all(f['line'] == 2 for f in hm),
              '实得 %s' % _fmt(hm))
        check('teeth 05 报出位置带 文件:行号 与原文行',
              bool(hm) and 'print' in hm[0]['snippet'] and hm[0]['codepoint'] == 'U+2713',
              '实得 %s' % (hm[0] if hm else None))

        # ③ GBK 编不出的箭头 ⇒ R2
        _write(tmp, 'bad_gbk.py',
               '# -*- coding: utf-8 -*-\n'
               'import sys\n'
               'sys.stderr.write("\u21d2 \u7ed3\u8bba\\n")\n')
        findings, _rc, _out = _run_tool(tmp)
        hg = _hits(findings or [], 'bad_gbk.py')
        check('teeth 06 sys.stderr.write() 里的 U+21D2 必须报 R2（GBK 编不出）',
              any(f['rule'] == 'gbk' for f in hg), '实得 %s' % _fmt(hg))

        # ⑥ GBK 判据本体（t28 / B6）：**必须是真编码**，不是手写白名单 / 区间表。
        # 事故：D1 往 messages.py 的两条 zh 模板里写了 `⇒`(U+21D2)，`pytest -k
        # messages_are_gbk` 当场红（`'gbk' codec can't encode character '\u21d2'`）。
        # 谁要是把 rules_for() 里的 `ch.encode('gbk')` 换成"箭头都算合规"这类白名单，
        # 下面两张表里必有一张变红 —— ①逐字符字面期望 ②字面期望本身与真编码对拍。
        _rt = _load_tool().rules_for
        # 字面期望（**独立 oracle**：不是从 rules_for 抄来的，也不是现算的）
        _gbk_table = [
            ('\u21d2', False),   # ⇒ RIGHTWARDS DOUBLE ARROW —— D1 事故那次踩的就是它
            ('\u21d4', False),   # ⇔ LEFT RIGHT DOUBLE ARROW
            ('\u27f9', False),   # ⟹ LONG RIGHTWARDS DOUBLE ARROW
            ('\u2192', True),    # → RIGHTWARDS ARROW（本仓库的主力箭头，GBK 里有）
            ('\u2014', True),    # — EM DASH
            ('\u2013', True),    # – EN DASH
            ('\u221a', True),    # √ 受准字形（MAINTENANCE §8.5）
            ('\u00d7', True),    # × 受准字形
            ('\u4e2d', True),    # 中（汉字，当然编得出）
        ]
        _bad_pred = ['U+%04X %s 期望%s实得%s' % (
            ord(ch), ch, '不判 gbk' if not ok else '判 gbk',
            '判 gbk' if 'gbk' in _rt(ch) else '不判 gbk')
            for ch, ok in _gbk_table
            if ('gbk' in _rt(ch)) == ok]          # ← 期望 ok 却判了 gbk / 反之
        check('teeth 18 【判据】U+21D2 / U+21D4 / U+27F9 判"编不进"，'
              'U+2192 / U+2014 / U+221A / U+00D7 / 汉字 判"能编"',
              not _bad_pred, '不符 %s' % (_bad_pred,))
        # 字面期望本身也要对拍真编码（期望写错了 == 牙齿本身失效）
        _bad_oracle = ['U+%04X %s 字面期望%s，真编码%s' % (
            ord(ch), ch, '能编' if ok else '编不进',
            '能编' if _encodable(ch) else '编不进')
            for ch, ok in _gbk_table if _encodable(ch) != ok]
        check('teeth 19 上面那张**字面期望表**与真实 `encode("gbk")` 一致（期望表本身不许错）',
              not _bad_oracle, '不符 %s' % (_bad_oracle,))
        # 定位精度：这条判据是按**字符**判的（不能靠整串混过去）
        check('teeth 20 判据按字符粒度：`"a ⇒ b"` 里只有 U+21D2 被点名',
              [c for c in 'a \u21d2 b' if ord(c) > 127 and 'gbk' in _rt(c)]
              == ['\u21d2'],
              '实得 %s' % ([c for c in 'a \u21d2 b'
                            if ord(c) > 127 and 'gbk' in _rt(c)],))

        # ⑦ CLI 级复刻 D1：**产品面** MESSAGES 表里的 ⇒ 必须报 gbk/error，→ / — 不报
        _write(tmp, 'src/mpltweak/fake_messages.py',
               '# -*- coding: utf-8 -*-\n'
               'MESSAGES = {\n'
               '    "a": {"zh": "x \u21d2 y", "en": "x -> y"},\n'
               '    "b": {"zh": "x \u21d4 y", "en": "x <-> y"},\n'
               '    "c": {"zh": "x \u27f9 y", "en": "x => y"},\n'
               '    "d": {"zh": "x \u2192 y", "en": "x -> y"},\n'
               '    "e": {"zh": "x \u2014 y", "en": "x - y"},\n'
               '}\n')
        findings, _rc, _out = _run_tool(tmp)
        fm = _hits(findings or [], 'src/mpltweak/fake_messages.py')
        _gbk_lines = sorted({f['line'] for f in fm if f['rule'] == 'gbk'})
        check('teeth 21 消息表里的 ⇒ / ⇔ / ⟹ 各报一条 gbk（行 3/4/5，severity=error）',
              _gbk_lines == [3, 4, 5]
              and all(f['severity'] == 'error' for f in fm if f['rule'] == 'gbk'),
              '实得 gbk 行 %s；命中 %s' % (_gbk_lines, _fmt(fm)))
        check('teeth 22 【误报守卫】同一张表里的 → / — 一条都不报',
              not [f for f in fm if f['line'] in (6, 7)],
              '实得 %s' % _fmt([f for f in fm if f['line'] in (6, 7)]))

        # ④ 打印助手**按函数体自动发现**（不靠名字白名单）
        _write(tmp, 'helper.py',
               '# -*- coding: utf-8 -*-\n'
               'def _say(msg):\n'
               '    print("[mpltweak] " + msg)\n'
               '\n'
               '\n'
               'def unrelated():\n'
               '    return "\u2713"\n'        # 同名的诱惑：这个函数不打印，不该传导
               '\n'
               '\n'
               'def go():\n'
               '    _say("\u2713 \u5df2\u5199\u56de")\n'
               '    unrelated()\n')
        findings, _rc, _out = _run_tool(tmp)
        fh = _hits(findings or [], 'helper.py')
        check('teeth 07 打印助手 _say() 被**按函数体**发现，其字面量实参要报',
              any(f['line'] == 11 for f in fh), '实得 %s' % _fmt(fh))
        check('teeth 08 【误报守卫】共文件里**不打印**的函数（unrelated）不被传导',
              not any(f['origin'].startswith('函数 unrelated') for f in fh),
              '实得来源 %s' % sorted({f['origin'] for f in fh}))

        # ⑤ 一跳跨函数：return 的字面量被 print 出去
        _write(tmp, 'retchain.py',
               '# -*- coding: utf-8 -*-\n'
               'def hint():\n'
               '    return "\u2713 \u5df2\u5c31\u7eea"\n'
               '\n'
               '\n'
               'def never_printed():\n'
               '    return "\u2713"\n'
               '\n'
               '\n'
               'print(hint())\n'
               'never_printed()\n')
        findings, _rc, _out = _run_tool(tmp)
        fr = _hits(findings or [], 'retchain.py')
        check('teeth 09 一跳跨函数：print(hint()) 里 hint() 的 return 字面量要报',
              any(f['line'] == 3 for f in fr), '实得 %s' % _fmt(fr))
        check('teeth 10 【误报守卫】返回值没上终端的函数（never_printed）不报',
              not any(f['line'] == 7 for f in fr), '实得 %s' % _fmt(fr))

        # ⑥ 消息表（模块级 XXX_MESSAGES）
        _write(tmp, 'table.py',
               '# -*- coding: utf-8 -*-\n'
               'MESSAGES = {\n'
               '    "written": {"zh": "\u2713 \u5df2\u5199\u56de", "en": "written"},\n'
               '}\n')
        findings, _rc, _out = _run_tool(tmp)
        ft = _hits(findings or [], 'table.py')
        check('teeth 11 消息表 MESSAGES 的模板串要报（模板必然上终端）',
              any(f['line'] == 3 for f in ft), '实得 %s' % _fmt(ft))

        # ⑦ 分级：装了控制台兜底 ⇒ 刻意例外；但 src/mpltweak/ 产品面零容忍
        _write(tmp, 'exempted.py',
               '# -*- coding: utf-8 -*-\n'
               'import sys\n'
               'for _s in (sys.stdout, sys.stderr):\n'
               '    try:\n'
               "        _s.reconfigure(errors='replace')\n"
               '    except Exception:\n'
               '        pass\n'
               'print("\u2713 \u5df2\u5199\u56de")\n')
        _write(tmp, 'src/mpltweak/prod.py',
               '# -*- coding: utf-8 -*-\n'
               'import sys\n'
               "sys.stdout.reconfigure(errors='replace')\n"   # 兜底也不放宽产品面
               'print("\u2713 \u5df2\u5199\u56de")\n')
        findings, rc, _out = _run_tool(tmp)
        sev = {f['rel'].replace('\\', '/'): f['severity']
               for f in (findings or []) if f['rel'].endswith('.py')}
        check('teeth 12 装了控制台兜底的非产品面文件 -> severity=exempt（刻意例外）',
              sev.get('exempted.py') == 'exempt', '实得 %s' % sev.get('exempted.py'))
        check('teeth 13 src/mpltweak/ 产品面**即使装了兜底也必须 error**（用户控制台零容忍）',
              sev.get('src/mpltweak/prod.py') == 'error',
              '实得 %s' % sev.get('src/mpltweak/prod.py'))
        check('teeth 14 有真违规时 rc 必须非 0（能被 CI 咬到）', rc != 0, '实得 rc=%s' % rc)

        # ⑧ 全干净 ⇒ 0 命中、rc=0（防"恒红"）
        clean2 = tempfile.mkdtemp(prefix='t15_clean_')
        try:
            _write(clean2, 'pure.py',
                   '# -*- coding: utf-8 -*-\n'
                   'print("[OK] \u5df2\u539f\u4f4d\u5199\u56de")\n'
                   'print("[X] \u9a8c\u8bc1\u672a\u901a\u8fc7")\n'
                   'print("\u221a \u5e03\u5c40\u5065\u5eb7")\n')
            findings, rc, _out = _run_tool(clean2)
            check('teeth 15 【防恒红】ASCII [OK]/[X] + 受准字形 -> 0 命中、rc=0',
                  not findings and rc == 0,
                  '实得 %s rc=%s' % (_fmt(findings or []), rc))
        finally:
            shutil.rmtree(clean2, ignore_errors=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        check('teeth 16 临时样例目录已清理（不留脏文件）', not os.path.exists(tmp),
              '还在 %s' % tmp if os.path.exists(tmp) else '')

    # ---- t98：**文件级**跳过临时/探针名（原来只跳目录）----
    # 病根：别的测试会往仓库里写"用完就删"的探针 `.py` —— `tests/test_agent_api.py:309`
    # 写 `tests/.tmp_dpi.py`、`:445` 写 `tests/_jsonenc9.py`。并发跑两条臂（或与别的
    # agent 的跑重叠）时，**A 臂的探针文件正被 B 臂的 notation 扫描看到** ⇒ 假红
    # （t87 那次**两臂都红、分离跑都过**，就是这个机制）。修法 = `iter_py()` 做**文件级**
    # 跳过，且跳过面**很窄**（只认既有临时/探针命名约定，不按"未跟踪"整片跳）。
    tmp17 = tempfile.mkdtemp(prefix='notation_t98_')

    def _mk17(name):
        p = os.path.join(tmp17, name)
        with open(p, 'w', encoding='utf-8', newline='') as f:
            f.write("print('\u2713 done')\n")      # U+2713 ⇒ R1 记号 + R2 GBK 编不出
        return p

    try:
        _mk17('.tmp_probe_t98.py')                 # 探针名 ⇒ 必须被跳过
        _f17, _rc17, _ = _run_tool(tmp17)
        check('teeth 17a 探针名（.tmp_*）**不报** ⇒ rc=0（否则并发跑会假红）',
              not _f17 and _rc17 == 0, '实得 %s rc=%s' % (_fmt(_f17 or []), _rc17))
        _mk17('normal_t98.py')                     # 正常名 ⇒ 必须照报
        _f17b, _rc17b, _ = _run_tool(tmp17)
        check('teeth 17b 正常名**照报** ⇒ rc≠0（证明跳过很窄、没藏真违规）',
              bool(_f17b) and _rc17b != 0,
              '实得 %s rc=%s' % (_fmt(_f17b or []), _rc17b))
    finally:
        shutil.rmtree(tmp17, ignore_errors=True)


# ------------------------------------------------------------------- 真仓库
def run_real():
    print('---- 真仓库：全仓扫描 ----')
    findings, rc, out = _run_tool(ROOT)
    if findings is None:
        check('真仓库：检查器可解析输出', False, '输出头=%r' % out[:200])
        return
    errs = [f for f in findings if f['severity'] == 'error']
    exs = [f for f in findings if f['severity'] == 'exempt']
    print('总命中 %d：真违规 %d / 刻意例外 %d' % (len(findings), len(errs), len(exs)))
    check('真仓库：产品面（src/mpltweak/）与未豁免文件 0 真违规', not errs,
          '实得 %d 处：%s' % (len(errs), _fmt(errs)[:5]))
    print('     ---- 刻意例外逐条（文件带控制台兜底，输出已清洗）----')
    for f in exs:
        print('     %s:%d  %s %s  <%s>' % (f['rel'], f['line'], f['codepoint'],
                                          f['char'], f['rule']))
    check('真仓库 rc 与"有无真违规"一致', (rc != 0) == bool(errs),
          'rc=%s errs=%d' % (rc, len(errs)))


def main():
    print('记号纪律检查器套件（tools/notation_discipline.py 的自证与真仓库扫描）')
    print('口径：MAINTENANCE.md:336 §8.5 字形 + CONTRIBUTING.md:71-74 GBK 可编码 + [OK]/[X]')
    print('')
    teeth()
    run_real()
    print('')
    if _fails:
        print('FAILED: %d 项 -> %s' % (len(_fails), _fails))
        return 1
    print('ALL PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
