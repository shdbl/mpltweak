# 语料全量静态筛选报告（t7）—— 公平口径真跑候选池

> 纯静态筛选：**不执行任何语料脚本**（当前无文件系统沙箱）。两层筛选 + 金丝雀自证见下。

## 0 配置与口径
- 语料: `<corpus_root>`（只读；扫描前后快照逐项相等 = **未被修改**: 是）
- plot==true: **4433 份**（manifest 口径；注意任务书写的 4,427 与实测 4,433 有差，以实测为准）。分层: {'gallery': 1144, 'zenodo': 2719, 'github': 570}
- 参数（与 t4 完全一致）: `{"version": 4, "fig_index": 0, "figsize_in": [8.5, 5.5]}`（单一字段 figsize）
- 目标预检命令: `python -m mpltweak apply <脚本> --params <pp> --write --dry-run --no-verify --json`（20s 超时）
- 并发: 有界 worker 池 jobs=3（t4 教训：绝不一次 spawn 全部）
- 解释器: <python>；耗时: 目标预检 586.5s

## 1 不执行证据（源码位置）
- apply.py:543-551：无 `--write` 时 apply 直接返回 `reason=preview`，**writeback 根本没被调用**（既不做分析也不执行）——所以预检必须带 `--write --dry-run` 才能拿到完整分析；
- writeback.py:2835-2843：inplace 路径 `if dry_run: return` 在备份/写盘/verify **之前**早退，只返回分析结果；
- writeback.py:2815-2826：唯一会执行脚本的 `_pending_vs_live`（2821）只在**轴级 pending 非空**时可达；本任务参数只有 figsize → `_pending` 恒空 → 永不执行；再加 `--no-verify` 使 verify=False，该分支结构性不可达（双重保险）；
- writeback.py:4572/4803-4805：块路径 `if not dry_run:` 才是真实插入+重跑（执行所在）；dry-run 走 else 纯文本预览（静态）；
- apply.py:649：`_applied = ... and not args.dry_run` → dry-run 永不报"已写回"。

## 2 金丝雀自证（canary）
- 脚本 canary：假脚本含 `open('CANARY_EXECUTED.txt','w')` 副作用，走**同一**筛选管线（依赖预检 + apply dry-run）→ 结束后哨兵文件**不存在**：✅ deps_ok=True target_ok=True rc=0 timed_out=False
- 依赖 canary：假包 `__init__.py` 含写文件副作用，走**同一** find_spec 路径 → 规格找到（spec=True）但 **未执行**（exec=False）：✅

## 3 核心计数
| 指标 | 份数 | 说明 |
|---|---:|---|
| total_plot | 4433 | plot==true 全量 |
| deps_ok | 2334 | 依赖齐（全部顶层 import 可 find_spec） |
| deps_missing | 2000 | 至少缺一个依赖（另解析失败 99 份） |
| target_ok | 3214 | 静态可定位改写目标（原位或块计划） |
| target_none | 1191 | 定位不到（no_fig/no_axes/fail/no_change…） |
| target_na | 28 | 预检超时/异常/无法解析 |
| **both_ok** | **1837** | **依赖齐 + 有目标（= 公平口径真跑候选池）** |

占比：deps_ok 2334/4433 = 52.7%；target_ok 3214/4433 = 72.5%；both_ok 41.4%

## 4 both_ok 分层（来源）
- zenodo: 987
- gallery: 709
- github: 141

## 5 最常缺失的库 Top 20（份数）
| 库 | 缺失份数 |
|---:|---:|
| `skimage` | 192 |
| `astropy` | 113 |
| `torch` | 106 |
| `sunpy` | 105 |
| `geopandas` | 62 |
| `cv2` | 62 |
| `mplsoccer` | 51 |
| `metpy` | 50 |
| `pyart` | 49 |
| `h5py` | 49 |
| `tensorflow` | 48 |
| `utils` | 47 |
| `iris` | 43 |
| `obspy` | 43 |
| `xgboost` | 39 |
| `config` | 39 |
| `rasterio` | 35 |
| `shap` | 25 |
| `numba` | 24 |
| `mplfinance` | 24 |

## 6 both_ok 样例（20 条，供后续真跑抽样）
- `018/gallery/3D/bar3d_simple.py`（gallery）
- `018/gallery/3D/fill_between3d_simple.py`（gallery）
- `018/gallery/3D/plot3d_simple.py`（gallery）
- `018/gallery/3D/quiver3d_simple.py`（gallery）
- `018/gallery/3D/scatter3d_simple.py`（gallery）
- `018/gallery/3D/stem3d.py`（gallery）
- `018/gallery/3D/surface3d_simple.py`（gallery）
- `018/gallery/3D/trisurf3d_simple.py`（gallery）
- `018/gallery/3D/voxels_simple.py`（gallery）
- `018/gallery/3D/wire3d_simple.py`（gallery）
- `018/gallery/animation/animate_decay.py`（gallery）
- `018/gallery/animation/animated_histogram.py`（gallery）
- `018/gallery/animation/double_pendulum.py`（gallery）
- `018/gallery/animation/animation_demo.py`（gallery）
- `018/gallery/animation/bayes_update.py`（gallery）
- `018/gallery/animation/dynamic_image.py`（gallery）
- `018/gallery/animation/multiple_axes.py`（gallery）
- `018/gallery/animation/frame_grabbing_sgskip.py`（gallery）
- `018/gallery/animation/rain.py`（gallery）
- `018/gallery/animation/random_walk.py`（gallery）

## 7 未判定（target_na 28）构成
| 类别 | 份数 | 说明 |
|---|---:|---|
| 引擎静态分析崩溃 | 22 | `TypeError: expected AST, got 'list'`（writeback.py:3614 `ast.dump(_call_sig(node))`，_call_sig 返回的是字符串列表）——**真实引擎 bug**（见下） |
| 工作目录命名遮蔽 | 1 | 脚本自身叫 `collections.py`，遮蔽 stdlib → apply CLI 崩溃（筛选 workdir 布局的伪影，非引擎问题） |
| 预检超时（>20s） | 5 | 超大脚本静态分析慢（dry-run 无执行，纯 AST 耗时） |

**引擎 bug 详述（发现于 t7，报引擎线）**：`writeback.py:3628-3631` 的 `_call_sig(node)`
返回 `[ast.dump(a) for a in node.args] + [...]`（**字符串列表**），而 `writeback.py:3614`
对它调 `ast.dump(...)`（只接受 AST 节点）→ 任何含"匿名 axes 返回调用"（未赋值给名字的
axes-returning 调用）的脚本，`apply` 直接 internal_error（rc=2），既不报 target 也不
fail-closed 优雅拒绝。全量 4,433 份里命中 22 份（networkx algorithms / scikitlearn /
misc 等）。建议修复：3614 行改为 `'%s|%s' % (attr, '|'.join(_call_sig(node)))`。
影响评估：这 22 份脚本当前**任何模式**下 apply 都会崩（静态阶段就崩），不算入候选池。

## 8 复现
```
set TEMP=<tmp>& set TMP=<tmp>
cd <work>\mpltweak
<python> tools/corpus_filter_t7.py --jobs 3
```

产出: `filter.jsonl`（逐脚本 path/source/stratum/deps_ok/missing_libs/target_ok/target_detail/both_ok）+ 本文件。
