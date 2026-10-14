# 组合分母 **5,880** 是怎么来的 + 抽样口径

## 一、分母 = 四个维度的笛卡尔积

`tests/test_cli_fuzz.py` 里四个维度**确定性枚举**（不是随机拼）：

```
DENOM = len(COMMANDS) × len(INPUTS) × len(PARAMS) × len(OPTIONS)
      =      7        ×     10      ×      6      ×      14      = 5,880
```

| 维度 | 条数 | 取值 |
|---|---|---|
| `COMMANDS` | 7 | `doctor` / `describe` / `check` / `apply` / `apply --write` / `revert` / `revert --write` |
| `INPUTS` | 10 | 正常脚本 / 语法坏 / 运行期抛异常 / 需要命令行参数 / 空文件 / 二进制 / **目录** / 不存在 / 只读 / 超长路径 |
| `PARAMS` | 6 | 无 `--params` / 文件不存在 / 坏 JSON / 缺 `version` / 顶层非对象 / 合法（describe 真产物） |
| `OPTIONS` | 14 | `()` / `--json` / `--fig 0·1·99·-1` / `--timeout 60·abc·0` / `--force` / `--no-tight` / `--nope`（未知）/ `--json --json`（重复）/ `-- extra` |

⇒ **7 × 10 × 6 × 14 = 5,880** 是**枚举出来的组合总数**（脚本每次运行都把它打印出来，
并同时打印本轮实跑条数与跳过条数）。

## 二、抽样口径（**分母是枚举、实跑是抽样**）

* `sample(n, seed)`：**分层**抽样 —— 先给**每个命令**各抽 1 条（保证 `7/7` 命令覆盖），
  其余用 `random.Random(seed).shuffle` 从剩余组合里取；`seed` 默认 `0`，
  可用 `MPLTWEAK_CLIFUZZ_SEED` 改 ⇒ **同 seed 完全可复现**。
* 默认 `MPLTWEAK_CLIFUZZ_N = 12`（CI smoke，秒级）；发布期用位置参数或环境变量加大
  （本包给的是 **`N=240`** 那一次）。
* `N=240` 时：命令覆盖 **7/7**、输入覆盖 **10/10**、跳过 **0** 条。
  rc 分布 `[(0, 10), (1, 1), (2, 229)]` —— 脚本会**显式断言"不是全 rc=0 的假绿"**。

## 三、五条断言（每次调用都过一遍）

| # | 断言 | 判据 |
|---|---|---|
| 1 | 永不出现未处理的 traceback | stderr 不得含 `Traceback (most recent call last)` |
| 2 | rc 必属文档化集合 | rc ∈ `{0,1,2,4}`（`messages.py` 的 `--help` 文案钉死；0.1.9 的 3 已退役）；脚本找不到 = 2、未知选项 = 2 |
| 3 | 报错说人话 | rc≠0 时 **stdout 或 stderr 至少一路**说到点子上；用户输入类错误必须点名**路径或选项名** |
| 4 | `--json` 恒可解析 | 给了 `--json` 且 stdout 非空 ⇒ 必须是合法 JSON；`apply --json` 失败时必须带 `error_code`（B9 契约） |
| 5 | en 下无中文 | 全程 `MPLTWEAK_LANG=en` ⇒ stdout/stderr 逐字节 ASCII（台账只准缩小） |

> 断言 3 写成"**两路之一**"而不是"必须 stderr"，是因为 `apply` **不带 `--json`** 时
> 人读错误走 **stdout**（`no params: <路径>`）—— 那是产品既定行为，按字面要求"必须 stderr"
> 会把既定行为判成缺陷（检查器误报）。理由写在 `cli_fuzz/test_cli_fuzz.py` 文件头。

## 四、怎么自己复算

```powershell
# CI smoke（秒级）
& $py tests/test_cli_fuzz.py
# 发布期规模（本包给的那次）
& $py tests/test_cli_fuzz.py 240
# 换 seed 复现别的抽样
$env:MPLTWEAK_CLIFUZZ_SEED='7'; & $py tests/test_cli_fuzz.py 240
```

输入样本（正常/坏语法/目录/只读/超长路径…）与参数文件（坏 JSON/缺 version…）
都由脚本在**临时目录**里**确定性重建**，不依赖任何外部语料 ⇒ 第三方**开箱可重跑**。
