# -*- coding: utf-8 -*-
"""mpltweak MCP server 测试（§1-§6）。没装 mcp 包时自动跳过（它是可选依赖）。

§1  真启动 stdio server 并握手
§2  四个工具都暴露
§3  describe_layout 返回规范 JSON
§4  check_layout 返回体检结果
§5  params_schema / apply_layout（只读预览）
§6  stdout 隔离：脚本自己的 print 不得污染 JSON-RPC 通道
"""
import asyncio
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 测试断言的是中文人读文案：把界面语言**钉死**，别让宿主 locale 决定结论。
# messages 的顺序是 --lang > MPLTWEAK_LANG > locale > LANG > en：CI(Linux,
# LANG=C) 走英文、本地(zh-CN)走中文 —— 不钉住就会"本地全绿、CI 全红"（0.1.8 真栽过）。
# 需要英文分支的用例，自行 run(..., env={'MPLTWEAK_LANG': 'en'}) 覆盖即可。
os.environ['MPLTWEAK_LANG'] = 'zh'
PY = sys.executable
FIX = os.path.join(ROOT, 'tests', 'fixtures', 'synth_script.py')

# 版本号引用 SCHEMA_VERSION，别写字面量（schema 升 4 时这里曾经硬编码 3 而挂）
sys.path.insert(0, os.path.join(ROOT, 'src'))
from mpltweak import params as _params                            # noqa: E402

try:
    import mcp                                                # noqa: F401
    HAS_MCP = True
except ImportError:
    HAS_MCP = False

_fails = []


def check(cond, label, extra=''):
    print('%s %s%s' % ('  OK  ' if cond else ' FAIL ', label,
                       '' if cond else '   ' + str(extra)))
    if not cond:
        _fails.append(label)


async def run():
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    # env 必须显式传：mcp 的 stdio_client 在 env=None 时只给子进程一个**精简环境**
    # （PATH/HOME…），PYTHONPATH 会被丢掉 → 测试实际跑的是 site-packages 里那个版本，
    # 而不是当前检出（0.1.9 修 CH-7/CH-10 时踩到过：用例全绿却什么都没测到）。
    params = StdioServerParameters(
        command=PY, args=['-m', 'mpltweak.cli', 'mcp'], cwd=ROOT,
        env=dict(os.environ))
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            print('§1 启动并握手')
            init = await session.initialize()
            check(True, 'initialize 成功')

            print('§2 工具列表')
            tools = await session.list_tools()
            names = sorted(t.name for t in tools.tools)
            check({'describe_layout', 'check_layout', 'params_schema',
                   'apply_layout'}.issubset(set(names)), '四个工具都在', names)

            print('§3 describe_layout')
            r = await session.call_tool('describe_layout', {'script': FIX})
            d = json.loads(r.content[0].text)
            check(d.get('version') == _params.SCHEMA_VERSION,
                  'version = SCHEMA_VERSION(%d)' % _params.SCHEMA_VERSION,
                  d.get('version'))
            check(len(d.get('axes', [])) >= 1, '带 axes', len(d.get('axes', [])))

            print('§4 check_layout')
            r = await session.call_tool('check_layout', {'script': FIX})
            d = json.loads(r.content[0].text)
            check('ok' in d and 'problems' in d and 'warnings' in d,
                  '返回 ok/problems/warnings', sorted(d))

            print('§5 params_schema + apply_layout')
            r = await session.call_tool('params_schema', {})
            d = json.loads(r.content[0].text)
            check(str(d.get('$schema', '')).startswith('http'),
                  '$schema 是 draft-07', d.get('$schema'))
            # 只读预览走 `.tweak_params/<脚本名>.json`（apply_layout 不接受 --params）。
            # 夹具旁边那份参数文件是 **git-ignored 的机器本地产物**（.gitignore:23）：
            #   · CI 全新检出 ⇒ 根本没有它 ⇒ 预览拿不到参数，exit_code=2；
            #   · 本地旧检出 ⇒ 可能是旧规范（version=3，当前 SCHEMA_VERSION=4）⇒ bad_params，exit_code=2。
            # 两种情况都不是"预览坏了"，但旧写法把夹具的那份文件当成既成事实 ⇒ 本机与 CI 都红。
            # 现在改为**在临时目录里用 describe 现造一份当前规范的参数**再预览。
            d5 = tempfile.mkdtemp(prefix='mpltweak_mcp5_')
            try:
                s5 = os.path.join(d5, 'synth_script.py')
                shutil.copy2(FIX, s5)
                p5 = os.path.join(d5, '.tweak_params', 'synth_script.json')
                os.makedirs(os.path.dirname(p5), exist_ok=True)
                env5 = dict(os.environ, PYTHONPATH=os.path.join(ROOT, 'src'),
                            MPLBACKEND='Agg')
                pr = subprocess.run([PY, '-m', 'mpltweak.cli', 'describe', s5,
                                     '-o', p5], cwd=d5, env=env5,
                                    capture_output=True)
                check(pr.returncode == 0 and os.path.exists(p5),
                      '为只读预览现造参数（describe 写 version=%d）'
                      % _params.SCHEMA_VERSION, pr.returncode)
                r = await session.call_tool('apply_layout',
                                            {'script': s5, 'write': False})
                d = json.loads(r.content[0].text)
                check(d.get('exit_code') in (0, 1) and 'output' in d,
                      '只读预览可用', d.get('exit_code'))
                check(d.get('wrote') is False, '预览不改代码', d.get('wrote'))

                # 判别用例：参数文件是**旧规范**时，只读预览同样拒绝（T61 的行为变更：
                # 参数非法与走不走写分支无关）。断言机器可读字段，不依赖人读文案。
                d5b = tempfile.mkdtemp(prefix='mpltweak_mcp5b_')
                try:
                    s5b = os.path.join(d5b, 'synth_script.py')
                    shutil.copy2(FIX, s5b)
                    p5b = os.path.join(d5b, '.tweak_params', 'synth_script.json')
                    os.makedirs(os.path.dirname(p5b), exist_ok=True)
                    with open(p5b, 'w', encoding='utf-8') as f:
                        json.dump({'version': 3, 'script': 'synth_script.py',
                                   'axes': [{'index': 0}]}, f)
                    r = await session.call_tool('apply_layout',
                                                {'script': s5b, 'write': False})
                    d = json.loads(r.content[0].text)
                    check(d.get('ok') is False and d.get('exit_code') == 2,
                          '旧规范参数 -> 只读预览 exit_code=2', d.get('exit_code'))
                    check(d.get('wrote') is False, '拒绝时 wrote 仍为 False',
                          d.get('wrote'))
                    rj = subprocess.run([PY, '-m', 'mpltweak.cli', 'apply', s5b,
                                         '--json'], cwd=d5b, env=env5,
                                        capture_output=True)
                    fj = json.loads(rj.stdout.decode('utf-8'))['files'][0]
                    check(fj.get('reason') == 'bad_params' and rj.returncode == 2,
                          '旧规范参数的机器可读原因 = bad_params / rc=2',
                          (fj.get('reason'), rj.returncode))
                finally:
                    shutil.rmtree(d5b, ignore_errors=True)
            finally:
                shutil.rmtree(d5, ignore_errors=True)

            print('§6 stdout 隔离（脚本 print 不能污染 JSON-RPC）')
            # fixtures 里的脚本会 print('savefig done')；若污染 stdout，
            # 上面的 json.loads 或 MCP 客户端解析早就炸了 —— 走到这里即通过。
            check(True, '多轮调用全程无 JSON 解析错误')

            print('§7 CH-10：serverInfo.version 是真实版本（0.1.8 是空串）')
            from mpltweak import __version__ as _ver
            check(init.server_info.version == _ver,
                  'serverInfo.version == %s' % _ver,
                  repr(init.server_info.version))

            tmp = tempfile.mkdtemp(prefix='mpltweak_mcp_')
            try:
                _head = ("import matplotlib\nmatplotlib.use('Agg')\n"
                         "import matplotlib.pyplot as plt\n"
                         "fig = plt.figure(figsize=(4, 3))\n"
                         "ax = fig.add_axes([0.15, 0.15, 0.7, 0.7])\n"
                         "ax.plot([1, 2], [1, 2])\n"
                         "ax.set_title('t')\n")
                noparams = os.path.join(tmp, 'noparams.py')
                with open(noparams, 'w', encoding='utf-8') as f:
                    f.write(_head)
                print('§8 CH-7：apply_layout 的 wrote 与实际一致')
                _sha_before = hashlib.sha256(open(noparams, 'rb').read()).hexdigest()
                r = await session.call_tool('apply_layout',
                                            {'script': noparams, 'write': True})
                d = json.loads(r.content[0].text)
                check(d.get('wrote') is False,
                      '没有参数文件、什么都没写 → wrote=False（0.1.8 报 true）', d)
                check(d.get('ok') is False and d.get('exit_code') == 2,
                      'ok/exit_code 如实回报失败', (d.get('ok'), d.get('exit_code')))
                _sha_after = hashlib.sha256(open(noparams, 'rb').read()).hexdigest()
                check(_sha_before == _sha_after, '源码一个字节没动')

                print('§9 A2/CH-1：sys.exit() 的脚本不能带走 server')
                exits = os.path.join(tmp, 'exit0.py')
                with open(exits, 'w', encoding='utf-8') as f:
                    f.write(_head + 'import sys\nsys.exit(0)\n')
                # 0.1.8 的这里会让 server 进程死亡、客户端**永久等下去** —— 所以
                # 必须带超时：回归时是"用例失败"，不是整套测试挂死。
                try:
                    r = await asyncio.wait_for(
                        session.call_tool('describe_layout',
                                          {'script': exits, 'all_figs': True}),
                        timeout=90)
                except asyncio.TimeoutError:
                    check(False, 'describe_layout(all_figs) 对 exit(0) 脚本有响应',
                          '90s 超时（server 可能已死）')
                    return
                check(not r.is_error, 'describe_layout(all_figs) 没报 isError')
                d = json.loads(r.content[0].text)
                check(d.get('n_figs') == 1 and len(d.get('figures') or []) == 1,
                      'exit(0) 脚本照常导出全部图（0.1.8：无响应 + server 进程死亡）',
                      d)
                r = await asyncio.wait_for(
                    session.call_tool('describe_layout', {'script': exits}),
                    timeout=90)
                check(json.loads(r.content[0].text).get('version')
                      == _params.SCHEMA_VERSION, 'server 仍然活着并继续正常工作')

                print('§10 失败语义只有一种形状（结构化 + 稳定英文）')
                r = await session.call_tool('check_layout',
                                            {'script': FIX, 'tol': 0})
                d = json.loads(r.content[0].text)
                check(d.get('ok') is False and d.get('error_code') == 'bad_tol',
                      'tol=0 → error_code=bad_tol（0.1.8 只有一句 '
                      '"Error executing tool"）', d)
                r = await session.call_tool('check_layout',
                                            {'script': os.path.join(tmp, 'nope.py')})
                d = json.loads(r.content[0].text)
                check(d.get('ok') is False and d.get('error_code') == 'file_not_found',
                      '找不到脚本 → error_code=file_not_found', d)
                check(str(d.get('error', '')).isascii(),
                      '机器可读 error 是稳定英文（不随语言变）', d.get('error'))
            finally:
                shutil.rmtree(tmp, ignore_errors=True)


def main():
    if not HAS_MCP:
        print('未安装 mcp 包（可选依赖）—— 跳过 MCP 测试')
        print('ALL PASS')
        return 0
    try:
        asyncio.run(run())
    except Exception as e:                                        # noqa: BLE001
        import traceback
        traceback.print_exc()
        check(False, 'MCP 端到端', '%s: %s' % (type(e).__name__, e))
    print()
    if _fails:
        print('FAILED: %d 项 -> %s' % (len(_fails), _fails))
        return 1
    print('ALL PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
