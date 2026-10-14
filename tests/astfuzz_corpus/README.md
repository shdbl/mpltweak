# astfuzz_corpus：t77 语法化 AST 模糊测试的可复现 corpus

> 注：本行标题刻意**不用** EM DASH（U+2014）—— 那会命中 `tests/test_docs.py` 的
> "新增歧义标题"棘轮（歧义字符会让 `#锚点` 链接解析失败）。语料说明不需要它。

## 这是什么

`corpus.json` 里每条 = **一个 seed + 实测钉住的期望结果**。它保证任何可疑/失败样本都能
**精确复现** —— 因为**脚本不存盘**：`tools/ast_fuzz_gen.py` 能从 seed **确定性重建**
同一份脚本（生成器只用 `random.Random(seed)`，不碰全局 random）。所以 seed 本身就是
"最小化后的样本"。

## 怎么用

```powershell
# 只跑 corpus 里的这几条（约 45 秒）
& $py tests\test_ast_fuzz.py --corpus

# 或跑任意规模；seed < N 的 corpus 条目会自动被交叉核对
& $py tests\test_ast_fuzz.py 36          # 全覆盖：9 形态 × 4 种 figsize 写法
$env:MPLTWEAK_ASTFUZZ_N='1000'; & $py tests\test_ast_fuzz.py   # 发布期大样本
```

`hard: true` 的条目是**硬断言**（只有对照那条）；其余是**实测记录** —— 结果"变好"属
**矩阵漂移**，只报告、不判红（一个本来降级、现在能写的变化是进步，不是 bug）。

## 里面钉了什么

| 类别 | seed | 钉住什么 |
|---|---|---|
| **对照** | 0 | 全字面量 ⇒ **必须**能原位写回（唯一硬断言） |
| 对抗形态 ① | 1, 2 | 嵌套解构 / 经典 `fig,(ax1,ax2)` 解包 ⇒ 安全降级 |
| 对抗形态 ② | 3 | 列表推导里建轴 ⇒ 安全降级 |
| 对抗形态 ③ | 4 | 海象运算符 `left := 0.15` ⇒ 安全降级 |
| 对抗形态 ④ | 5 | 类方法 + 嵌套函数 + 装饰器 + `style.context` ⇒ 安全降级 |
| 对抗形态 ⑤ | 6 | 字面量表达式 `1e-1` / `5/10` ⇒ 安全降级 |
| 长尾 | 7, 8 | 变量驱动 pos / for 循环建轴 ⇒ 安全降级 |
| **已知缺陷** | 117, 153, 198 | pos 照常落地 **且** figsize 的变量/表达式/环境变量被改写成字面量 |
| 已知缺陷（另一形态） | 9 | pos 本身非字面量 ⇒ 只改了 figsize 那一行 |

## 与台账的关系

已知缺陷登记在 `tests/test_ast_fuzz.py` 的 `KNOWN_OVERWRITE`（**只准缩小**），
并已立 **t88**（`src/` 的活，挂进冻结门）。**t88 修好后**：

1. `KNOWN_OVERWRITE` 条目会变 stale（套件会提示删除）；
2. 本表 `defect_*` 那 4 条的 `expect` 需要更新（`figsize_written` 应变成 `False`）。

两处都要改 —— 这就是"台账只准缩小"的落地动作。
