# -*- coding: utf-8 -*-
"""mpltweak GUI 续调 launch._apply_saved 顶层非对象 JSON 守卫回归（0.1.10）。

背景（fuzz-params 在 params fuzz 里发现，船长授权修复）：
  GUI 续调路径读上次保存的参数文件套回图。顶层非对象 JSON（数组/字符串/
  数字/null）过去会让 `data.get('axes', [])`（_apply_saved）崩成
  AttributeError —— 参数文件坏了就崩掉整个开窗流程，违背"续调是尽力而为"
  （文件坏了就跳过，绝不让 GUI 开窗崩掉）的语义。

修法（launch.py，与 params.load 的 0.1.10 修复同款语义）：
  * _apply_saved()：json.load 后先 isinstance(data, dict) 守卫，非对象 →
    stderr 一句人话提示 + return 0（不续调、不崩、不打扰主流程）。
  * main() 续调循环：_d2.get('fig_index') 是续调流程里更早的一道闸，非对象
    会在那里先崩（AttributeError，根本到不了 _apply_saved）—— 同样加
    isinstance 守卫，跳过该文件（本轮一并修，属同一崩溃类）。

本套件断言（纯函数级，不真开窗口）：
  * 顶层数组/字符串/数字/null 各一：_apply_saved(stub_fig, path) 返回 0、
    不抛异常、stderr 有"顶层不是对象"人话提示；
  * 合法 dict 参数：不被误跳过（无提示、不抛异常、返回 int）；
  * 文件不存在：返回 0（既有语义不回归）。
  stub fig 只需带空 axes 列表（_apply_saved 的非对象路径根本不触碰 fig；
  正常路径也只调 fig.axes[i].* / fig.set_size_inches）。
"""
import contextlib
import io
import json
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 界面语言钉死（与其它七个套件一致）：断言的是中文文案，别让宿主 locale 决定结论。
os.environ['MPLTWEAK_LANG'] = 'zh'
sys.path.insert(0, os.path.join(ROOT, 'src'))
from mpltweak.launch import _apply_saved            # noqa: E402

# GBK 控制台兜底（同 test_check.py / test_fuzz_params.py）：CI 是 UTF-8，
# 本地 cp936 上一次 UnicodeEncodeError 就把整套从 ALL PASS 打成 rc=1 的假红。
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(errors='replace')
    except (AttributeError, ValueError, OSError):
        pass


def _gbk_safe(s):
    s = str(s)
    enc = getattr(sys.stdout, 'encoding', None) or 'utf-8'
    try:
        return s.encode(enc, 'replace').decode(enc, 'replace')
    except (LookupError, UnicodeDecodeError):
        return s.encode('ascii', 'backslashreplace').decode('ascii')


_TMPROOT = os.path.join(ROOT, 'tests', '.tmp_launch_%d' % os.getpid())


class _StubFig:
    """_apply_saved 的桩 fig：非对象参数路径不触碰它；空 axes + set_size_inches
    兜底，保证万一走到正常路径也不炸（不需要真窗口）。"""

    def __init__(self):
        self.axes = []

    def set_size_inches(self, w, h):
        pass


_fails = []


def check(name, cond, detail=''):
    mark = 'OK' if cond else 'FAIL'
    print('%s | %s%s' % (mark, _gbk_safe(name),
                         (' | ' + _gbk_safe(detail)) if detail else ''))
    if not cond:
        _fails.append(name)


# 顶层非对象负载：数组/字符串/数字/null 各一（json.dump 写文件，模拟用户手改坏）
_NON_OBJECTS = (
    ('array', [1, 2, 3]),
    ('string', 'hello'),
    ('number', 42),
    ('null', None),
)

# 合法 dict：续调要正常走（不被误跳过）；figsize_in 顺带验证 stub 的
# set_size_inches 兜底路径。
_LEGAL_PARAMS = {'version': 4, 'fig_index': 0, 'axes': [],
                 'figsize_in': [8.0, 6.0]}


def run_guards():
    fig = _StubFig()
    for tag, payload in _NON_OBJECTS:
        pth = os.path.join(_TMPROOT, '%s.json' % tag)
        with open(pth, 'w', encoding='utf-8') as f:
            json.dump(payload, f)
        err = io.StringIO()
        try:
            with contextlib.redirect_stderr(err):
                n = _apply_saved(fig, pth)
            raised = None
        except Exception as e:                       # noqa: BLE001
            n, raised = None, e
        check('_apply_saved %s：返回 0 不抛异常' % tag,
              raised is None and n == 0,
              ('raised=%s' % _gbk_safe(repr(raised))[:200]) if raised
              else 'n=%s' % n)
        check('_apply_saved %s：stderr 有人话提示' % tag,
              '顶层不是对象' in err.getvalue(),
              err.getvalue().strip()[:120])

    # 合法 dict 参数：不被误跳过（无提示、不抛异常、返回 int）
    pth = os.path.join(_TMPROOT, 'legal.json')
    with open(pth, 'w', encoding='utf-8') as f:
        json.dump(_LEGAL_PARAMS, f)
    err = io.StringIO()
    try:
        with contextlib.redirect_stderr(err):
            n = _apply_saved(fig, pth)
        raised = None
    except Exception as e:                           # noqa: BLE001
        n, raised = None, e
    check('_apply_saved 合法 dict：不误跳过（无提示、不抛异常、返回 int）',
          raised is None and isinstance(n, int) and err.getvalue() == '',
          ('raised=%s' % _gbk_safe(repr(raised))[:200]) if raised
          else 'n=%r stderr=%r' % (n, err.getvalue()[:120]))

    # 文件不存在：既有语义 return 0
    try:
        n = _apply_saved(fig, os.path.join(_TMPROOT, 'nope.json'))
        raised = None
    except Exception as e:                           # noqa: BLE001
        n, raised = None, e
    check('_apply_saved 文件不存在：return 0 不抛异常',
          raised is None and n == 0,
          ('raised=%s' % _gbk_safe(repr(raised))[:200]) if raised else 'n=%s' % n)


def main():
    os.makedirs(_TMPROOT, exist_ok=True)
    print('launch._apply_saved 顶层非对象 JSON 守卫回归（0.1.10）')
    run_guards()
    shutil.rmtree(_TMPROOT, ignore_errors=True)
    print()
    if _fails:
        print('FAILED: %d 项 -> %s' % (len(_fails), _fails))
        return 1
    print('ALL PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
