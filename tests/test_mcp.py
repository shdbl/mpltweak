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
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
FIX = os.path.join(ROOT, 'tests', 'fixtures', 'synth_script.py')

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

    params = StdioServerParameters(
        command=PY, args=['-m', 'mpltweak.cli', 'mcp'], cwd=ROOT)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            print('§1 启动并握手')
            await session.initialize()
            check(True, 'initialize 成功')

            print('§2 工具列表')
            tools = await session.list_tools()
            names = sorted(t.name for t in tools.tools)
            check({'describe_layout', 'check_layout', 'params_schema',
                   'apply_layout'}.issubset(set(names)), '四个工具都在', names)

            print('§3 describe_layout')
            r = await session.call_tool('describe_layout', {'script': FIX})
            d = json.loads(r.content[0].text)
            check(d.get('version') == 3, 'version=3', d.get('version'))
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
            r = await session.call_tool('apply_layout',
                                        {'script': FIX, 'write': False})
            d = json.loads(r.content[0].text)
            check(d.get('exit_code') in (0, 1) and 'output' in d,
                  '只读预览可用', d.get('exit_code'))
            check(d.get('wrote') is False, '预览不改代码', d.get('wrote'))

            print('§6 stdout 隔离（脚本 print 不能污染 JSON-RPC）')
            # fixtures 里的脚本会 print('savefig done')；若污染 stdout，
            # 上面的 json.loads 或 MCP 客户端解析早就炸了 —— 走到这里即通过。
            check(True, '多轮调用全程无 JSON 解析错误')


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
