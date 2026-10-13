# 探针：原样跑 vs 套壳跑 等价性（t39 阶段 1）

> **只读**：语料 `corpus_union` 逐份 sha256 前后对账见 §6；脚本一律复制到临时
> 工作区（A/B 两个内容相同的副本目录）后分别跑，产出文件落在临时区。

## 0 配置
- 候选池: `<evidence>/corpus_filter_t7/filter.jsonl`（sha256 `bf22238f61184f17be91fbbf05fe570cb42eac8d523b7e2cf557a087002add99`）
- 语料根: `<corpus_root>`
- 解释器: `<python>`
- 抽样: seed=20261013，目标 120 份，每层 ≥40
- 单份超时: 60s，jobs=2
- A 路: `python <脚本>`（cwd=副本目录，MPLBACKEND=Agg）
- B 路: `python -m mpltweak.verify <脚本> <state.json> 0`（同 cwd/同 env）

## 0b A 档静态防弹窗门禁（硬门禁：先筛后跑）

`MPLBACKEND=Agg` **挡不住脚本自己** `matplotlib.use("TkAgg")` / 
`switch_backend(...)` —— 而本探针的 **A 路是裸跑**，所以静态筛是主防线
（本探针第一轮就是因此真弹了窗，轮次作废重跑）。

- 判据来源：按字节复用 `tests/test_corpus_invariants.py` 的 `_window_risk()` + 其模块级常量
   （sha256 `0b224f78b9d0a1d90a918b0035a5212bf39742ba1ae7de33e30944010271d54b`；**不 import 该模块** —— 它是模块级脚本，import 会真跑整轮）
- 口径（t23，与 tests 完全一致）：GUI 库 `import`；`use(<GUI后端>)` /
  `switch_backend(<GUI后端>)`；`filedialog`/`messagebox`/`askopenfilename` 等；
  `webbrowser.open()`；**以及"不确定"的** `plt.pause|ion|iwait|
  waitforbuttonpress|ginput|get_current_fig_manager`。**宁可少跑，也不弹窗。**
- 探针额外加严（比 tests 更保守）：`ast.parse` 失败 ⇒ `syntax_unparseable` 排除；
  源码里出现 `importlib` / `__import__(` / `exec(` / `eval(`（静态判据看不进
  字符串里到底导了什么）⇒ `dynamic_code_unknown` 排除。
- 池 4433 行 → 过筛 **4065** 份；排除明细：{'window_risk_A': 123, 'dynamic_code_unknown': 146, 'syntax_unparseable': 99}（另 0 行文件不存在）
- **跑前自证**：最终样本内 A 档命中数 = **0**（非 0 时探针 rc=2 直接拒跑）；
  且每份在真正 spawn 之前**再筛一次**（纵深防御，见 `one_case`）。
- 补证：A/B 两路子进程 env 均显式 `MPLBACKEND=Agg`。
- 轮次留档（同一目录下的历史文件，**不作为结论依据**）：
  `results_UNSCREENED_ROUND1_VOID.jsonl`（未过筛轮次，跑到 70/150 时被
  终止，原因：A 路裸跑导致真实语料脚本开窗）；
  `results_ROUND2_partial_superseded.jsonl`（已过筛，但探针 normalize() 把
  子工作目录 a/b 残留在输出里 ⇒ 误报路径差异，作废重跑）。
  判定实现/归一化版本见每份记录里的 `norm_version`（本版 = 3）。

- 判定与观测分离：判定实现只此一份（`classify()`），既在跑的时候用，也能对
  已落盘的原始观测 `--reclassify` 重算 —— 改判据不必重跑语料（省时间、也少
  一次"跑真实脚本"的风险面）。

## 1 样本

### 1a 抽样规则（`build_sample(sample_n, per_stratum, …)` 的确切语义）

先过 A 档门禁（§0b），再按**三步**抽，**全是"保底/上限"而不是"先到先得"**：

1. **类别钉选**：对六个必含类各钉最多 **6** 份（按 both_ok 优先、再按路径排序）；
   同一份脚本可同时命中多类，**只计一次**（所以 pick 次数 > 去重份数）。
2. **分层新增**：gallery / zenodo / github 三层各再补最多 **`per_stratum` 份**
   **新出现的**脚本（已在步骤 1 钉住的不会占这个名额）。
3. **补足**：若去重总数仍 < **`sample_n`**，从全池随机补到 `sample_n`。

⇒ `sample_n` 是**总数保底**（≥ 这么多），**不是上限**；`per_stratum` 是**每层新增**
的**上限**。本次 `--sample 120 --per-stratum 40` 的实际算式：

```
类别钉选：6 类 × ≤6 次 = 36 次 pick，去重后 30 份（6 份跨类重复，只算一次）
分层新增 gallery：40（步骤 2）+ 19（步骤 1 落在该层）= 59
分层新增 zenodo ：40（步骤 2）+ 11（步骤 1 落在该层）= 51
分层新增 github ：40（步骤 2）+  0（步骤 1 落在该层）= 40
步骤 3 补足：0 份（去重总数已达 150 ≥ sample_n=120，未触发补足）
**合计 = 30（类别去重）+ 120（分层新增）= 150 份**
```

（注意：类别钉选与分层新增**互不重叠**，因为步骤 2 只收"还没被钉住"的脚本；而"类别去重"里那 6 份重复，来自同一份脚本同时命中多类。）

### 1b 计数与不适用明细（150 − 6 = 144）
- 全部记录: **150**；参与一致率统计: **144**；不适用: **6**
- 分层: {'gallery': 59, 'github': 40, 'zenodo': 51}
- 六类必含覆盖（去重份数）: {'ends_with_close': 8, 'savefig_only': 17, 'show_only': 79, 'grid': 98, 'side': 20, 'close_call': 30, 'savefig': 57, 'show': 94}
- 池内 deps_missing 抽样: 6 份

| # | 路径 | 归类 | 原因（一句话）|
|---:|---|---|---|
| 1 | `018/gallery/scikitlearn/miscellaneous/plot_outlier_detection_bench.py` | na_timeout | — |
| 2 | `018/zenodo/15516949__Visualizzatore_3D_dello_Spazio_Narrativo_Proppiano_1.2.4.py` | na_skip | stdin_risk |
| 3 | `018/zenodo/21719201__RunVenusFPAH03b.py` | na_skip | stdin_risk |
| 4 | `big/zenodo/21499076__aps_journal_.py` | na_timeout | — |
| 5 | `big/zenodo/22151044__2026h_figures.py` | na_timeout | — |
| 6 | `big/zenodo/8342045__AnalyzeMonte2.py` | na_skip | stdin_risk |

- **分母声明**：本报告 §2 / §3 / §4 的**所有**分母都是 **144**（= 全部 150 份 − 6 份不适用），**没有任何指标偷用 150**。

## 2 判定分布

| 判定 | 份数 | 含义 |
|---|---:|---|
| equivalent | 63 | 两侧同样跑通，输出/产出文件一致 |
| equivalent_fail | 51 | 两侧同样**跑不通**（缺数据/依赖），一致 |
| diff_wrapper_benign | 0 | 只有已知的套壳噪声差异（show 被拦掉等） |
| diff_wrapper_nostate | 8 | A 跑通、B **取不到图状态**（脚本自己 close 了图）|
| diff_artifacts | 1 | 产出文件不一致 |
| diff_unknown / diff_wrapper | 21 | **待解释**，见 §4 |
| na_skip / na_timeout / na_error | 6 | 不适用 |

## 3 四项逐项一致率（分母 144）

| 项 | 一致 | 占比 |
|---|---:|---:|
| 退出码语义（两侧同为跑通/同为跑不通）| 136 | 94.4% |
| stdout+stderr（严格，合流后多重集）| 16 | 11.1% |
| stdout+stderr（抹掉已知套壳噪声）| 123 | 85.4% |
| 产出文件（存在/大小/sha256）| 143 | 99.3% |

## 4 不一致逐条分类

### 4a 退出码分类计数

| exit_class | 份数 |
|---|---:|
| B_NOSTATE_CLOSED | 8 |
| EQ_FAIL | 63 |
| EQ_OK | 73 |

### 4a2 输出差异分类计数（pad_class）

| pad_class | 份数 | 含义 |
|---|---:|---|
| frame_number_drift | 73 | 同一警告，仅"源码位置行号"漂移（套壳多包一层 runpy） |
| wrapper_err_channel | 50 | 两侧都跑不通；A 直吐完整 traceback，B 收成一行 `[verify] ...` |
| unexplained | 21 | **待解释**，见逐条明细 |

### 4a3 产出文件：非确定性格式豁免（size 同、sha 不同，且格式自带时间戳/随机 id）

- 豁免格式计数: {'.svg': 6, '.eps': 1, '.pdf': 4}（0 份 = 无豁免）
- 说明：SVG/EPS/PDF 里 matplotlib 会写创建时间与随机元素 id ⇒ 逐字节不可复现；PNG 不带时间戳，仍按 sha256 严格判等。

### [diff_unknown] `018/gallery/scikitlearn/model_selection/plot_learning_curve.py`
- stratum=gallery tags=grid,show,show_only both_ok=True
- a_rc=0 b_rc=0 exit_class=EQ_OK
  - 只在 B: <conda_env>\lib\site-packages\joblib\externals\loky\backend\resource_tracker.py:359: UserWarning: resource_tracker: There appear to be N leaked folder objects to clean up at shutdown
  warnings.warn(
  - 只在 B: <conda_env>\lib\site-packages\joblib\externals\loky\backend\resource_tracker.py:375: UserWarning: resource_tracker: <TMP> FileNotFoundError(2, '系统找不到指定的路径。')
  warnings.warn(f"resource_tracker: {name}: {e!r}")
  - 只在 B: <conda_env>\lib\site-packages\joblib\externals\loky\backend\resource_tracker.py:375: UserWarning: resource_tracker: <TMP> FileNotFoundError(2, '系统找不到指定的路径。')
  warnings.warn(f"resource_tracker: {name}: {e!r}")
  - 只在 B: <conda_env>\lib\site-packages\joblib\externals\loky\backend\resource_tracker.py:375: UserWarning: resource_tracker: <TMP> FileNotFoundError(2, '系统找不到指定的路径。')
  warnings.warn(f"resource_tracker: {name}: {e!r}")
  - 只在 B: <conda_env>\lib\site-packages\joblib\externals\loky\backend\resource_tracker.py:375: UserWarning: resource_tracker: <TMP> FileNotFoundError(2, '系统找不到指定的路径。')
  warnings.warn(f"resource_tracker: {name}: {e!r}")

### [diff_unknown] `018/gallery/scikitlearn/model_selection/plot_multi_metric_evaluation.py`
- stratum=gallery tags=show,show_only both_ok=True
- a_rc=0 b_rc=0 exit_class=EQ_OK
  - 只在 B: <conda_env>\lib\site-packages\joblib\externals\loky\backend\resource_tracker.py:359: UserWarning: resource_tracker: There appear to be N leaked folder objects to clean up at shutdown
  warnings.warn(
  - 只在 B: <conda_env>\lib\site-packages\joblib\externals\loky\backend\resource_tracker.py:375: UserWarning: resource_tracker: <TMP> FileNotFoundError(2, '系统找不到指定的路径。')
  warnings.warn(f"resource_tracker: {name}: {e!r}")
  - 只在 B: <conda_env>\lib\site-packages\joblib\externals\loky\backend\resource_tracker.py:375: UserWarning: resource_tracker: <TMP> FileNotFoundError(2, '系统找不到指定的路径。')
  warnings.warn(f"resource_tracker: {name}: {e!r}")
  - 只在 B: Exception ignored in atexit callback: <function TemporaryResourcesManager.register_folder_finalizer.<locals>._cleanup at 0xADDR>
  - 只在 B: Exception ignored in atexit callback: <function TemporaryResourcesManager.register_folder_finalizer.<locals>._cleanup at 0xADDR>

### [diff_unknown] `018/gallery/shapes_and_collections/hatch_demo.py`
- stratum=gallery tags=grid,show,show_only both_ok=True
- a_rc=1 b_rc=1 exit_class=EQ_FAIL
  - 只在 A: ImportError: cannot import name 'deque' from partially initialized module 'collections' (most likely due to a circular import) (<WORK>\collections.py)
  - 只在 A: Traceback (most recent call last):
  File "<F>", line 18, in <module>
    import matplotlib.pyplot as plt
  File "<F>", line 139, in <module>
    from collections import namedtuple
  File "<F>", line 20, in <module>
    import matplotlib.pyplot as plt
  File "<F>", line 44, in <module>
    from contextlib import AbstractContextManager, ExitStack
  File "<F>", line 5, in <module>
    from collections import deque
  - 只在 B: Could not import runpy module
  - 只在 B: ImportError: cannot import name 'namedtuple' from partially initialized module 'collections' (most likely due to a circular import) (<WORK>\collections.py)
  - 只在 B: Traceback (most recent call last):
  File "<F>", line 15, in <module>
    import importlib.util
  File "<F>", line 14, in <module>
    from contextlib import contextmanager
  File "<F>", line 5, in <module>
    from collections import deque
  File "<F>", line 20, in <module>
    import matplotlib.pyplot as plt
  File "<F>", line 139, in <module>
    from collections import namedtuple

### [diff_unknown] `018/zenodo/23142271__make_figure_7.py`
- stratum=zenodo tags=close_call,ends_with_close,grid,savefig both_ok=True
- a_rc=1 b_rc=3 exit_class=EQ_FAIL
  - 只在 A: Data file not found: CBU1.csv

### [diff_unknown] `big/github/statsmodels/archive/examples/ex_feasible_gls_het_0.py`
- stratum=github tags=show,show_only both_ok=True
- a_rc=0 b_rc=0 exit_class=EQ_OK
  - 只在 A: Time:                        12:06:33   Log-Likelihood:                -344.90
  - 只在 A: Time:                        12:06:33   Log-Likelihood:                -400.09
  - 只在 B: Time:                        12:06:35   Log-Likelihood:                -344.90
  - 只在 B: Time:                        12:06:35   Log-Likelihood:                -400.09

### [diff_unknown] `big/github/statsmodels/archive/examples/ex_multivar_kde.py`
- stratum=github tags=show,show_only both_ok=True
- a_rc=1 b_rc=3 exit_class=EQ_FAIL
  - 只在 A: Traceback (most recent call last):
  File "<F>", line 50, in <module>
    ax = fig.gca(projection="3d")
  - 只在 A: TypeError: FigureBase.gca() got an unexpected keyword argument 'projection'
  - 只在 B: <conda_env>\lib\site-packages\joblib\externals\loky\backend\resource_tracker.py:359: UserWarning: resource_tracker: There appear to be N leaked folder objects to clean up at shutdown
  warnings.warn(
  - 只在 B: <conda_env>\lib\site-packages\joblib\externals\loky\backend\resource_tracker.py:375: UserWarning: resource_tracker: <TMP> FileNotFoundError(2, '系统找不到指定的路径。')
  warnings.warn(f"resource_tracker: {name}: {e!r}")
  - 只在 B: <conda_env>\lib\site-packages\joblib\externals\loky\backend\resource_tracker.py:375: UserWarning: resource_tracker: <TMP> FileNotFoundError(2, '系统找不到指定的路径。')
  warnings.warn(f"resource_tracker: {name}: {e!r}")
  - 只在 B: Exception ignored in atexit callback: <function TemporaryResourcesManager.register_folder_finalizer.<locals>._cleanup at 0xADDR>
  - 只在 B: Exception ignored in atexit callback: <function TemporaryResourcesManager.register_folder_finalizer.<locals>._cleanup at 0xADDR>

### [diff_unknown] `big/github/statsmodels/archive/examples/ex_pareto_plot.py`
- stratum=github tags=show,show_only both_ok=True
- a_rc=0 b_rc=0 exit_class=EQ_OK
  - 只在 A: LinregressResult(slope=np.float64(-0.9622342797049946), intercept=np.float64(6.893396054185818), rvalue=np.float64(-0.9986092306216158), pvalue=np.float64(0.0), stderr=np.float64(0.001608094187845401), intercept_stderr=np.float64(0.00232193136572485))
  - 只在 B: LinregressResult(slope=np.float64(-0.9625400078534373), intercept=np.float64(6.887840881006244), rvalue=np.float64(-0.998633759558222), pvalue=np.float64(0.0), stderr=np.float64(0.0015943271928334452), intercept_stderr=np.float64(0.0022948609501260444))

### [diff_unknown] `big/github/statsmodels/archive/examples/example_crossval.py`
- stratum=github tags=show,show_only both_ok=True
- a_rc=1 b_rc=3 exit_class=EQ_FAIL
  - 只在 A:         True,  True,  True,  True,  True,  True,  True,  True,  True,
  - 只在 A:         True,  True,  True]), slice(None, None, None))' is an invalid key
  - 只在 A: During handling of the above exception, another exception occurred:
  - 只在 A: Traceback (most recent call last):
  File "<F>", line 20, in <module>
    res = sm.OLS(data.endog[inidx], data.exog[inidx, :]).fit()
  File "<F>", line 4113, in __getitem__
    indexer = self.columns.get_loc(key)
  File "<F>", line 3824, in get_loc
    self._check_indexing_error(key)
  File "<F>", line 6072, in _check_indexing_error
    raise InvalidIndexError(key)
  - 只在 A: Traceback (most recent call last):
  File "<F>", line 3812, in get_loc
    return self._engine.get_loc(casted_key)
  File "<F>", line 167, in pandas._libs.index.IndexEngine.get_loc
  File "<F>", line 173, in pandas._libs.index.IndexEngine.get_loc

### [diff_unknown] `big/github/statsmodels/archive/examples/example_gam_0.py`
- stratum=github tags=show,show_only both_ok=True
- a_rc=1 b_rc=3 exit_class=EQ_FAIL
  - 只在 A: -0.22773898735907971 -2.7510814905612406 0.8088817603013799
  - 只在 A: Traceback (most recent call last):
  File "<F>", line 93, in <module>
    m = GAM(b, d, family=f)
  File "<F>", line 350, in __init__
    AdditiveModel.__init__(self, exog, smoothers=smoothers, family=family)
  File "<F>", line 200, in __init__
    self.smoothers = smoothers or [default_smoother(exog[:,i]) for i in range(exog.shape[1])]
  File "<F>", line 200, in <listcomp>
    self.smoothers = smoothers or [default_smoother(exog[:,i]) for i in range(exog.shape[1])]
  File "<F>", line 86, in default_smoother
    knots = _x[np.linspace(0, n-1, nknots).astype(np.int32)]
  File "<F>", line 121, in linspace
    num = operator.index(num)
  - 只在 A: TypeError: 'numpy.float64' object cannot be interpreted as an integer
  - 只在 A: z.std() 0.8977405344297115
  - 只在 B: -0.2293419058687977 -2.833691867552191 0.8002144814218367
  - 只在 B: z.std() 0.9115932883413986

### [diff_unknown] `big/github/statsmodels/archive/examples/l1_demo/sklearn_compare.py`
- stratum=github tags=show,show_only both_ok=True
- a_rc=1 b_rc=3 exit_class=EQ_FAIL
  - 只在 A: Exception: The anes96 dataset is now loaded in as a short version that cannot be used here
  - 只在 A: Traceback (most recent call last):
  File "<F>", line 39, in <module>
    raise Exception(

### [diff_unknown] `big/github/statsmodels/archive/examples/try_quantile_regression1.py`
- stratum=github tags=show,show_only both_ok=True
- a_rc=0 b_rc=0 exit_class=EQ_OK
  - 只在 A: ols      [1.3514 0.9321 0.9041 1.227 ]
  - 只在 A: qr 0.1   [1.0052 1.008  0.999  0.9958]
  - 只在 A: qr 0.5   [1.1155 1.0392 1.011  1.0073]
  - 只在 A: qr 0.75  [1.2945 0.9423 1.0445 1.2289]
  - 只在 B: ols      [1.3651 0.8988 0.8764 1.2966]
  - 只在 B: qr 0.1   [1.0045 1.0035 1.0011 0.9991]
  - 只在 B: qr 0.5   [1.123  0.9901 0.9459 1.0553]
  - 只在 B: qr 0.75  [1.337  0.9415 0.9292 1.1612]

### [diff_unknown] `big/github/statsmodels/examples/python/glm.py`
- stratum=github tags=grid both_ok=True
- a_rc=0 b_rc=0 exit_class=EQ_OK
  - 只在 A: Time:                          12:07:13   Pearson chi2:                 4.05e+03
  - 只在 A: Time:                        12:07:13   Pearson chi2:                   0.0862
  - 只在 A: Time:                        12:07:13   Pearson chi2:                 1.02e-05
  - 只在 B: Time:                          12:07:15   Pearson chi2:                 4.05e+03
  - 只在 B: Time:                        12:07:15   Pearson chi2:                   0.0862
  - 只在 B: Time:                        12:07:15   Pearson chi2:                 1.02e-05

### [diff_unknown] `big/github/statsmodels/examples/python/robust_models_0.py`
- stratum=github tags=grid both_ok=True
- a_rc=0 b_rc=0 exit_class=EQ_OK
  - 只在 A:   6.11061132  6.36291994  6.61026259  6.85263927  7.09004997  7.32249471
  - 只在 A:   7.54997348  7.77248627  7.9900331   8.20261396  8.41022884  8.61287776
  - 只在 A:   8.8105607   9.00327768  9.19102868  9.37381372  9.55163278  9.72448588
  - 只在 A:   9.892373   10.05529416 10.21324934 10.36623856 10.5142618  10.65731908
  - 只在 A:  10.79541038 10.92853571 11.05669508 11.17988847 11.29811589 11.41137734
  - 只在 B:   6.33478054  6.56598915  6.79312852  7.01619863  7.23519951  7.45013113
  - 只在 B:   7.66099352  7.86778665  8.07051054  8.26916519  8.46375058  8.65426674
  - 只在 B:   8.84071364  9.0230913   9.20139972  9.37563889  9.54580881  9.71190949
  - 只在 B:   9.87394092 10.03190311 10.18579605 10.33561974 10.48137419 10.62305939
  - 只在 B:  10.76067535 10.89422206 11.02369953 11.14910775 11.27044672 11.38771645

### [diff_unknown] `big/github/statsmodels/examples/python/tsa_arma_0.py`
- stratum=github tags= both_ok=True
- a_rc=0 b_rc=0 exit_class=EQ_OK
  - 只在 A:             AC           Q      Prob(>Q)
  - 只在 A: <conda_env>\lib\site-packages\statsmodels\tsa\statespace\sarimax.py:978: UserWarning: Non-invertible starting MA parameters found. Using zeros as starting parameters.
  warn('Non-invertible starting MA parameters found.'
  - 只在 B: <conda_env>\lib\site-packages\statsmodels\tsa\statespace\sarimax.py:978: UserWarning: Non-invertible starting MA parameters found. Using zeros as starting parameters.
  warn('Non-invertible starting MA parameters found.'
            AC           Q      Prob(>Q)

### [diff_unknown] `big/github/statsmodels/examples/python/wls.py`
- stratum=github tags=grid both_ok=True
- a_rc=0 b_rc=0 exit_class=EQ_OK
  - 只在 A: Time:                        12:07:29   Log-Likelihood:                -50.716
  - 只在 A: Time:                        12:07:29   Log-Likelihood:                -51.136
  - 只在 B: Time:                        12:07:31   Log-Likelihood:                -50.716
  - 只在 B: Time:                        12:07:31   Log-Likelihood:                -51.136

### [diff_unknown] `big/zenodo/10278919__gradient_consensus_demo.py`
- stratum=zenodo tags=savefig,show both_ok=True
- a_rc=1 b_rc=3 exit_class=EQ_FAIL
  - 只在 A: ........................................................................................................................................................................................................................................................................................................................................................................................................................................................................................................................................................................................................................
  - 只在 A: ModuleNotFoundError: No module named 'tifffile'
  - 只在 A: Traceback (most recent call last):
  File "<F>", line 651, in <module>
    main()
  File "<F>", line 546, in main
    to_tif('01_true_fluorophores.tif', d.true_fluorophores)
  File "<F>", line 511, in to_tif
    import tifffile as tf
  - 只在 B: ........................................................................................................................................................................................................................................................................................................................................................................................................................................................................................................................................................................................................................[verify] ModuleNotFoundError: No module named 'tifffile'

### [diff_unknown] `big/zenodo/18820771__CreateEmbeddingVisuals7.py`
- stratum=zenodo tags=close_call,savefig,side both_ok=True
- a_rc=2 b_rc=3 exit_class=EQ_FAIL
  - 只在 A: 18820771__CreateEmbeddingVisuals7.py: error: the following arguments are required: csv_file
  - 只在 B: 18820771__CreateEmbeddingVisuals7.py: error: unrecognized arguments: <WORK>\state.json 0

### [diff_unknown] `big/zenodo/21387252__ppmi_entropy_analysis.py`
- stratum=zenodo tags=close_call,grid,savefig,side both_ok=True
- a_rc=1 b_rc=3 exit_class=EQ_FAIL
  - 只在 A:   ERROR: Cannot find Participant_Status file.
  - 只在 A:   Loading PPMI data from: data/
  - 只在 A:   Rodriguez (2026)
  - 只在 A:   Shannon entropy · Permutation tested · Model-independent
  - 只在 A: ======================================================================
  - 只在 B:                                           [--n-perms N_PERMS] [--min-n MIN_N]
  - 只在 B:                                           [--output OUTPUT] [--analysis {h1}]
  - 只在 B:                                           [--seed SEED]
  - 只在 B: 21387252__ppmi_entropy_analysis.py: error: unrecognized arguments: <SCRIPT> <WORK>\state.json 0
  - 只在 B: usage: 21387252__ppmi_entropy_analysis.py [-h] [--data-dir DATA_DIR]

### [diff_unknown] `big/zenodo/22089837__cascade_shelf_analysis_CANONICAL_scaffold.py`
- stratum=zenodo tags=close_call,grid,savefig,side both_ok=True
- a_rc=1 b_rc=3 exit_class=EQ_FAIL
  - 只在 A: [stations] provide --stations <csv> (or run --selftest)
  - 只在 B:        [--dist-col DIST_COL] [--iho IHO] [--outdir OUTDIR] [--figure]
  - 只在 B:        [--selftest]
  - 只在 B:        [-h] [--stations STATIONS] [--coastline COASTLINE]
  - 只在 B: 22089837__cascade_shelf_analysis_CANONICAL_scaffold.py: error: unrecognized arguments: <SCRIPT> <WORK>\state.json 0
  - 只在 B: usage: 22089837__cascade_shelf_analysis_CANONICAL_scaffold.py

### [diff_unknown] `big/zenodo/22289078__NB04_chapter3_figures.py`
- stratum=zenodo tags=close_call,grid,savefig,side both_ok=True
- a_rc=1 b_rc=3 exit_class=EQ_FAIL
  - 只在 A: FileNotFoundError: [Errno 2] No such file or directory: '/g/data/gv90/xl1657/phd/M1_workspace/processed/regridded\\CCI_CS2_201305.npz'
  - 只在 A: Started: 2026-10-13 12:25:55
  - 只在 A: Traceback (most recent call last):
  File "<F>", line 546, in <module>
    fig_spatial_maps()
  File "<F>", line 184, in fig_spatial_maps
    ref = np.load(os.path.join(BASE_REGRIDDED, 'CCI_CS2_201305.npz'))
  File "<F>", line 451, in load
    fid = stack.enter_context(open(os.fspath(file), "rb"))
  - 只在 B: Started: 2026-10-13 12:26:54

### [diff_unknown] `big/zenodo/22752752__simulation.py`
- stratum=zenodo tags=close_call,grid,savefig,side both_ok=True
- a_rc=1 b_rc=3 exit_class=EQ_FAIL
  - 只在 A: FileNotFoundError: [Errno 2] No such file or directory: '<work>\\.tmp_t39\\cmp_work\\798b0d2e44ba2801\\a\\parameters\\empirical_targets.csv'
  - 只在 A: Traceback (most recent call last):
  File "<F>", line 380, in <module>
    run(args)
  File "<F>", line 343, in run
    targets=load_targets()
  File "<F>", line 34, in load_targets
    targets = pd.read_csv(ROOT / 'parameters/empirical_targets.csv')
  File "<F>", line 1026, in read_csv
    return _read(filepath_or_buffer, kwds)
  File "<F>", line 620, in _read
    parser = TextFileReader(filepath_or_buffer, **kwds)
  File "<F>", line 1620, in __init__
    self._engine = self._make_engine(f, self.engine)
  File "<F>", line 1880, in _make_engine
    self.handles = get_handle(
  File "<F>", line 873, in get_handle
    handle = open(
  - 只在 B:                                [--n-mc N_MC] [--n-export N_EXPORT]
  - 只在 B:                                [--n-sensitivity N_SENSITIVITY]
  - 只在 B: 22752752__simulation.py: error: unrecognized arguments: <SCRIPT> <WORK>\state.json 0
  - 只在 B: usage: 22752752__simulation.py [-h] [--output-dir OUTPUT_DIR] [--seed SEED]

### 4b 产出文件不一致（已扣除非确定性格式豁免）
- `018/zenodo/18910861__generate_all_figures.py`: reason=size:figures_supplementary/ige_dynamics/2WQR_rg.png 185701≠140667；只在 A=['figures_supplementary/ige_dynamics/2WQR_rg.png', 'figures_supplementary/ige_dynamics/2WQR_rmsd.png', 'figures_supplementary/ige_dynamics/2Y7Q_eigenvalues.png', 'figures_supplementary/ige_dynamics/2Y7Q_rg.png', 'figures_supplementary/ige_dynamics/2Y7Q_rmsd.png', 'figures_supplementary/ige_dynamics/7SHT_eigenvalues.png', 'figures_supplementary/ige_dynamics/7SHT_projection.png', 'figures_supplementary/ige_dynamics/7SHT_rg.png', 'figures_supplementary/ige_dynamics/7SHT_rmsd.png', 'figures_supplementary/lnp_detailed/PEG_LNP_mRNA_dist.png', 'figures_supplementary/lnp_detailed/PEG_LNP_pot.png', 'figures_supplementary/lnp_detailed/PEG_LNP_rg.png', 'figures_supplementary/lnp_detailed/PEG_LNP_rmsd.png', 'figures_supplementary/lnp_detailed/PGL_LNP_mRNA_dist.png', 'figures_supplementary/lnp_detailed/PGL_LNP_mRNA_pot.png', 'figures_supplementary/lnp_detailed/PGL_LNP_mRNA_rg.png', 'figures_supplementary/lnp_detailed/PGL_LNP_mRNA_rmsd.png', 'figures_supplementary/lnp_detailed/PGL_LNP_pot.png', 'figures_supplementary/lnp_detailed/PGL_LNP_rg.png', 'figures_supplementary/lnp_detailed/PGL_LNP_rmsd.png', 'figures_supplementary/polymer_interaction/peg_distance.png', 'figures_supplementary/polymer_interaction/peg_interaction_energy.png', 'figures_supplementary/polymer_interaction/peg_sasa.png', 'figures_supplementary/polymer_interaction/pgl_distance.png', 'figures_supplementary/polymer_interaction/pgl_interaction_energy.png', 'figures_supplementary/polymer_interaction/pgl_sasa.png', 'figures_supplementary/tellurium/S14_pca_plot.png', 'figures_supplementary/tellurium/S15_feature_importance.png']；只在 B=['figures_supplementary/ige_dynamics/2WQR_rg.png', 'figures_supplementary/ige_dynamics/2WQR_rmsd.png', 'figures_supplementary/ige_dynamics/2Y7Q_eigenvalues.png', 'figures_supplementary/ige_dynamics/2Y7Q_rg.png', 'figures_supplementary/ige_dynamics/2Y7Q_rmsd.png', 'figures_supplementary/ige_dynamics/7SHT_eigenvalues.png', 'figures_supplementary/ige_dynamics/7SHT_projection.png', 'figures_supplementary/ige_dynamics/7SHT_rg.png', 'figures_supplementary/ige_dynamics/7SHT_rmsd.png', 'figures_supplementary/lnp_detailed/PEG_LNP_mRNA_dist.png', 'figures_supplementary/lnp_detailed/PEG_LNP_pot.png', 'figures_supplementary/lnp_detailed/PEG_LNP_rg.png', 'figures_supplementary/lnp_detailed/PEG_LNP_rmsd.png', 'figures_supplementary/lnp_detailed/PGL_LNP_mRNA_dist.png', 'figures_supplementary/lnp_detailed/PGL_LNP_mRNA_pot.png', 'figures_supplementary/lnp_detailed/PGL_LNP_mRNA_rg.png', 'figures_supplementary/lnp_detailed/PGL_LNP_mRNA_rmsd.png', 'figures_supplementary/lnp_detailed/PGL_LNP_pot.png', 'figures_supplementary/lnp_detailed/PGL_LNP_rg.png', 'figures_supplementary/lnp_detailed/PGL_LNP_rmsd.png', 'figures_supplementary/polymer_interaction/peg_distance.png', 'figures_supplementary/polymer_interaction/peg_interaction_energy.png', 'figures_supplementary/polymer_interaction/peg_sasa.png', 'figures_supplementary/polymer_interaction/pgl_distance.png', 'figures_supplementary/polymer_interaction/pgl_interaction_energy.png', 'figures_supplementary/polymer_interaction/pgl_sasa.png', 'figures_supplementary/tellurium/S14_pca_plot.png', 'figures_supplementary/tellurium/S15_feature_importance.png']

### 4b2 A vs A′ 噪声地板（同一路"原样跑"跑两次）

- 对照分母 40 份；**A vs A′ 自身一致率 = 77.5%**；A vs A′ 就不一致的有 9 份
- 结论：**A vs B 的不一致不能全记在套壳头上** —— 下面把二者分开：

| 分组 | 份数 | 说明 |
|---|---:|---|
| 环境/非确定性（A vs A′ **同样**不一致）| 9 | 时钟、无种子随机数、joblib 资源追踪竞态、语料目录遮蔽标准库等 |
| 仍待解释（A vs A′ **一致**，只有套壳那次不同）| 12 | 见下逐条 |

- (环境/非确定性) `big/github/statsmodels/archive/examples/ex_feasible_gls_het_0.py` — pad_class=unexplained exit=EQ_OK
- (环境/非确定性) `big/github/statsmodels/archive/examples/ex_pareto_plot.py` — pad_class=unexplained exit=EQ_OK
- (环境/非确定性) `big/github/statsmodels/archive/examples/example_gam_0.py` — pad_class=unexplained exit=EQ_FAIL
- (环境/非确定性) `big/github/statsmodels/archive/examples/try_quantile_regression1.py` — pad_class=unexplained exit=EQ_OK
- (环境/非确定性) `big/github/statsmodels/examples/python/glm.py` — pad_class=unexplained exit=EQ_OK
- (环境/非确定性) `big/github/statsmodels/examples/python/robust_models_0.py` — pad_class=unexplained exit=EQ_OK
- (环境/非确定性) `big/github/statsmodels/examples/python/wls.py` — pad_class=unexplained exit=EQ_OK
- (环境/非确定性) `big/zenodo/22289078__NB04_chapter3_figures.py` — pad_class=unexplained exit=EQ_FAIL
- (环境/非确定性) `big/zenodo/22752752__simulation.py` — pad_class=unexplained exit=EQ_FAIL

- (**待解释**) `018/gallery/scikitlearn/model_selection/plot_learning_curve.py` — pad_class=unexplained exit=EQ_OK
- (**待解释**) `018/gallery/scikitlearn/model_selection/plot_multi_metric_evaluation.py` — pad_class=unexplained exit=EQ_OK
- (**待解释**) `018/gallery/shapes_and_collections/hatch_demo.py` — pad_class=unexplained exit=EQ_FAIL
- (**待解释**) `018/zenodo/23142271__make_figure_7.py` — pad_class=unexplained exit=EQ_FAIL
- (**待解释**) `big/github/statsmodels/archive/examples/ex_multivar_kde.py` — pad_class=unexplained exit=EQ_FAIL
- (**待解释**) `big/github/statsmodels/archive/examples/example_crossval.py` — pad_class=unexplained exit=EQ_FAIL
- (**待解释**) `big/github/statsmodels/archive/examples/l1_demo/sklearn_compare.py` — pad_class=unexplained exit=EQ_FAIL
- (**待解释**) `big/github/statsmodels/examples/python/tsa_arma_0.py` — pad_class=unexplained exit=EQ_OK
- (**待解释**) `big/zenodo/10278919__gradient_consensus_demo.py` — pad_class=unexplained exit=EQ_FAIL
- (**待解释**) `big/zenodo/18820771__CreateEmbeddingVisuals7.py` — pad_class=unexplained exit=EQ_FAIL
- (**待解释**) `big/zenodo/21387252__ppmi_entropy_analysis.py` — pad_class=unexplained exit=EQ_FAIL
- (**待解释**) `big/zenodo/22089837__cascade_shelf_analysis_CANONICAL_scaffold.py` — pad_class=unexplained exit=EQ_FAIL

### 4c 已知套壳噪声（不计入不一致）

- `W_SHOW_SILENCED`: 63 行
- `W_VERIFY_PREFIX`: 19 行

### 4d 套壳取不到图状态（B_NOSTATE_CLOSED）逐条

- `018/zenodo/21349735__nutristruct_reproducibility.py`（tags=grid,savefig,savefig_only）
- `big/github/statsmodels/archive/examples/ex_outliers_influence.py`（tags=）
- `big/zenodo/14873595__HNPlot.py`（tags=close_call,ends_with_close,grid,savefig,side）
- `big/zenodo/19574627__codon_correlation.py`（tags=close_call,savefig,side）
- `big/zenodo/19334145__plot_set_up_classes.py`（tags=close_call,ends_with_close,savefig,show）
- `big/zenodo/22796824__Folds.py`（tags=close_call,ends_with_close,savefig）
- `big/zenodo/22872215__plotting.py`（tags=close_call,grid,savefig）
- `big/zenodo/8335795__plot_jenoptik_chm15k.py`（tags=grid,savefig,savefig_only）

## 5 耗时

- A 路中位: 0.97s；B 路中位: 1.00s；两路合计中位: 1.94s
- A 路总和: 265.0s；B 路总和: 273.1s；两路合计: 538.2s
- **B 单遍 / 两遍现况** = 0.51（越低越省；理想 ≈ B/(A+B)）

## 6 语料只读对账（跑前 / 跑后逐份 sha256）

- **核对方式**：每份脚本在**被跑之前**记下 `sha256(原文件)` 存进该记录的
  `corpus_sha256` 字段；本报告生成时对**同一批路径**重新算一遍 sha256 并与
  之逐个比对，不一致就列出来。
- 另：脚本从不就地跑 —— 一律复制到 `<work>/<id>/{a,b}/` 两个内容相同的副本
  目录再跑，产出文件（savefig/to_csv/open('w')）全部落在临时工作区。
- 故"语料未被修改"有两重证据：① 逐份 sha256 对账；② 产出文件根本不在语料树里。

- 抽样脚本 sha256 前后不一致: **0** 份
