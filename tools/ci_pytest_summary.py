# -*- coding: utf-8 -*-
"""跑 `pytest -q`，把输出落盘 + 写进 GitHub Actions 的 job summary，再回传 pytest 的退出码。

**为什么需要它**：本机 token 权限拿不到 Actions 的 job 日志（API 返回 401），而 step 的
原始输出要在 UI 里登录点开。把失败摘要写进 `GITHUB_STEP_SUMMARY` 之后，**check-run 的
注解/摘要里就能直接读到失败用例** —— 排查不再依赖日志权限。

**为什么用 Python 而不是 shell**：CI 矩阵里 windows-latest 的默认 shell 是 pwsh、
ubuntu-latest 是 bash —— Python 写法两边一致（`set -e` / `PIPESTATUS` / `tee` 这类
bash-ism 在 pwsh 上会直接报错）。
"""
import os
import pathlib
import subprocess
import sys

TAIL = 40000          # 写进 summary 的尾部字符数
ECHO = 20000          # 回显到 job 日志的尾部字符数


def main():
    proc = subprocess.run([sys.executable, "-m", "pytest", "-q"],
                          capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    out = (proc.stdout or "") + (proc.stderr or "")
    pathlib.Path("pytest_full.log").write_text(out, encoding="utf-8")

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as fh:
            fh.write("## pytest -q（末 %d 字符）\n\n```text\n%s\n```\n\n" % (TAIL, out[-TAIL:]))

    # 关键：**注解（annotation）是 Actions API 能直接读到的通道**
    # GET /repos/{owner}/{repo}/check-runs/{id}/annotations —— 本机 token 读不到 job 日志（401），
    # 但读得到注解。失败时把尾部输出作为一条 error 注解发出（换行需转义成 %0A）。
    if proc.returncode != 0:
        msg = (out[-8000:].replace("%", "%25").replace("\r", "").replace("\n", "%0A"))
        print("::error title=pytest -q 失败（退出码 %d）::%s" % (proc.returncode, msg))

    print(out[-ECHO:])
    return proc.returncode


if __name__ == "__main__":
    sys.exit(main())
