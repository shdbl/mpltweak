# 语料级不变量扫描报告（t4 / ⑥；t23 扩到全量池）

> 复用 t2（tests/test_property_writeback.py）四条铁律断言函数，对语料（corpus_union，只读）逐份跑不变量扫描。语料目录**未被修改**：✅。

## 0 扫描配置
- 候选池: `<evidence>/corpus_filter_t7/filter.jsonl`
- 池 sha256: `bf22238f61184f17be91fbbf05fe570cb42eac8d523b7e2cf557a087002add99`（续跑对账依据，见 `RUN_META.json`）
- 本次分母: **1837 份**（池内 both_ok 全量；本轮实跑 482 份、续跑跳过 1355 份）
- 分层（来源）: {'gallery': 709, 'zenodo': 987, 'github': 141}
- 沙箱: `MPLTWEAK_SANDBOX_ROOT=<sandbox_root>`（禁 GUI 层 + 无人值守层强制开；文件/子进程/网络由 audit hook 守卫）
- 每份参数: `{version:4, fig_index:0, figsize_in:[8.5,5.5]}`；验证档: **真跑档**（apply --write 默认，verify 重跑 + 语义比对，防弹窗守卫生效）
- 解释器: <python>
- 耗时: 1122s

## 0b 沙箱边界（本次运行的已知边界，如实说明）

- 沙箱判定"是否在跑用户脚本"依据 **`argv[0]` 是否落在 `MPLTWEAK_SANDBOX_ROOT` 内**；
  **真跑链路**（`apply` → `python -m mpltweak.verify …` → 脚本）里那个进程的
  `argv[0]` 是 mpltweak 模块路径 ⇒ 落在 **harness 侧，只有"文件层"生效**。
- 因此：**越界读写删在真跑链路里实测被拦住**（试点证据 §4c：`PermissionError: 沙箱：open(write) 被禁止`，磁盘无越界文件）；
  但 **socket/subprocess 层在链路里不生效** ⇒ 本版靠**静态预筛**兜住：
  含联网/起子进程调用的脚本一律 `na_skip`（`na_reason=net_risk`）**不真跑**，
  所以本次全量**在构造上不会碰到网络**。
- B 档口径：只调 `plt.show()` / `fig.show()` 的脚本**照常真跑** —— `MPLBACKEND=Agg`
  下 show() 是无害 no-op，且沙箱还硬钉 Agg、拦 GUI 库导入与 `matplotlib.use(<GUI后端>)`（t15 D1/D2 与本次 §4c/B 档实测）。真正会开窗的（A 档）静态排除、不真跑。
- 精细判定规则（"argv 里出现游乐场内的脚本路径也算脚本进程"，需避免 CLI 自身被
  误判而拦死 verify 的 spawn）列入 **0.1.11 候选**。

## 1 判定分布（判据不适用 vs 真违约）
| 判定 | 份数 | 说明 |
|---|---:|---|
| **pass** | 928 | 适用四律全部通过 |
| **violation** | 0 | **真违约**（见 §3） |
| **na_gui** | 345 | 判据不适用：脚本要开窗/需要 GUI（其中静态防弹窗未真跑 28 份，运行时禁 GUI 层拦截见 §4b） |
| （对照）B 档 plt.show() | 1087 | **已真跑**：只调 `plt.show()` 的脚本在 `MPLBACKEND=Agg` 下无害（沙箱还硬钉 Agg + 拦 GUI 库导入），计入上面各档 |
| **na_stdin** | 464 | 判据不适用：脚本等人输入（沙箱无人值守层让 input() 直接 EOF） |
| na_skip | 29 | 判据不适用：语料侧语法错/编码/超限/非 py（其中联网/子进程静态预筛 29 份，见 §4d） |
| na_reject | 0 | 判据不适用：引擎拒写（no_fig/no_axes/fail），字节未动 |
| na_timeout | 71 | 判据不适用：worker 超时被杀（无法判定） |
| na_error | 0 | 判据不适用：worker 异常（引擎抛错） |

口径说明（对齐 _numbers_e28a608.md）：「幂等/可逆」只对**成功写回**批（reason ∈ ok/best_effort/no_change）断言；「语法守恒/只动目标」对**任何**结果成立（拒写批要求字节不动）。退块（best_effort + block）是引擎的正常降级路径，不是违约。

## 2 四条铁律（成功写回批 928 份）
| 铁律 | 通过 | 说明 |
|---|---:|---|
| ① 幂等 | 928/928 | 二次 apply 字节不变 + reason=no_change；另 0 份判定层边界（字节稳定但 reason≠no_change，见 §2b） |
| ② 可逆 | 928/928 | revert --write 逐字节还原（无备份时 rc=2 且不动） |
| ③ 语法守恒 | 928/928 | 写回后 ast.parse（**任何**结果都成立，含拒写批） |
| ④ 只动目标 | 928/928 | 落地字段值以字面量出现 + diff 外科手术（**任何**结果） |
| 承诺违约 | 928/928 | 声称写回但字节未变（应恒为 0） |
| 拒写不改字节 | n/a（本轮 na_reject = 0，此判据没有对象） | 拒写批脚本一个字节都不动 |

### 2b 幂等判定层边界（字节稳定但 reason≠no_change，0 份，**已知边界**）
按 _numbers_e28a608.md m5 口径：二次 apply 后**字节不变**即字节级幂等成立；仅当引擎因永久 pending 字段（如轴变量证不出）反复报 best_effort 时 reason ≠ no_change，属"判定层非幂等"的已知边界，**不计为违约**。明细见 results.jsonl 的 idem_boundary 字段。

## 3 真违约样例
**0 份**（本子集内承诺违约 0 / 幂等 0 / 可逆 0 / 语法 0 / 只动目标 0）。

## 4 判据不适用明细（na_skip 29）
| 脚本路径 | 原因 |
|---|---|
| `018/gallery/lines_bars_and_markers/timeline.py` | 静态预筛：脚本含联网/起子进程调用（真跑链路里 socket/subprocess 层不生效，见 SUMMARY 试点证据）→ 不真跑 |
| `018/gallery/scikitlearn/applications/plot_out_of_core_classification.py` | 静态预筛：脚本含联网/起子进程调用（真跑链路里 socket/subprocess 层不生效，见 SUMMARY 试点证据）→ 不真跑 |
| `018/gallery/text_labels_and_annotations/mathtext_examples.py` | 静态预筛：脚本含联网/起子进程调用（真跑链路里 socket/subprocess 层不生效，见 SUMMARY 试点证据）→ 不真跑 |
| `018/zenodo/20681418__wikipedia_pageviews.py` | 静态预筛：脚本含联网/起子进程调用（真跑链路里 socket/subprocess 层不生效，见 SUMMARY 试点证据）→ 不真跑 |
| `018/zenodo/22691847__e1_calibration_missingness.py` | 静态预筛：脚本含联网/起子进程调用（真跑链路里 socket/subprocess 层不生效，见 SUMMARY 试点证据）→ 不真跑 |
| `018/zenodo/22691847__e1_dca_figure7.py` | 静态预筛：脚本含联网/起子进程调用（真跑链路里 socket/subprocess 层不生效，见 SUMMARY 试点证据）→ 不真跑 |
| `018/zenodo/22691847__e1_figures_1_4.py` | 静态预筛：脚本含联网/起子进程调用（真跑链路里 socket/subprocess 层不生效，见 SUMMARY 试点证据）→ 不真跑 |
| `big/github/cartopy/examples/lines_and_polygons/ocean_bathymetry.py` | 静态预筛：脚本含联网/起子进程调用（真跑链路里 socket/subprocess 层不生效，见 SUMMARY 试点证据）→ 不真跑 |
| `big/github/cartopy/examples/scalar_data/aurora_forecast.py` | 静态预筛：脚本含联网/起子进程调用（真跑链路里 socket/subprocess 层不生效，见 SUMMARY 试点证据）→ 不真跑 |
| `big/github/statsmodels/examples/python/interactions_anova.py` | 静态预筛：脚本含联网/起子进程调用（真跑链路里 socket/subprocess 层不生效，见 SUMMARY 试点证据）→ 不真跑 |
| `big/github/statsmodels/examples/python/statespace_sarimax_stata.py` | 静态预筛：脚本含联网/起子进程调用（真跑链路里 socket/subprocess 层不生效，见 SUMMARY 试点证据）→ 不真跑 |
| `big/zenodo/14957162__FigurasPorArticulo.py` | 静态预筛：脚本含联网/起子进程调用（真跑链路里 socket/subprocess 层不生效，见 SUMMARY 试点证据）→ 不真跑 |
| `big/zenodo/19689912__yxjoivv.py` | 静态预筛：脚本含联网/起子进程调用（真跑链路里 socket/subprocess 层不生效，见 SUMMARY 试点证据）→ 不真跑 |
| `big/zenodo/20286538__nac_fchk_matrix.py` | 静态预筛：脚本含联网/起子进程调用（真跑链路里 socket/subprocess 层不生效，见 SUMMARY 试点证据）→ 不真跑 |
| `big/zenodo/20752617__inten-history.py` | 静态预筛：脚本含联网/起子进程调用（真跑链路里 socket/subprocess 层不生效，见 SUMMARY 试点证据）→ 不真跑 |
| `big/zenodo/20921761__run_benchmark.py` | 静态预筛：脚本含联网/起子进程调用（真跑链路里 socket/subprocess 层不生效，见 SUMMARY 试点证据）→ 不真跑 |
| `big/zenodo/20921761__run_real_benchmark.py` | 静态预筛：脚本含联网/起子进程调用（真跑链路里 socket/subprocess 层不生效，见 SUMMARY 试点证据）→ 不真跑 |
| `big/zenodo/21299013__6-4-2-3_Alternative_Substructure_prediction.py` | 静态预筛：脚本含联网/起子进程调用（真跑链路里 socket/subprocess 层不生效，见 SUMMARY 试点证据）→ 不真跑 |
| `big/zenodo/21408851__empirical_tdi.py` | 静态预筛：脚本含联网/起子进程调用（真跑链路里 socket/subprocess 层不生效，见 SUMMARY 试点证据）→ 不真跑 |
| `big/zenodo/21497627__07_generate_spatial_synthesis_heatmap.py` | 静态预筛：脚本含联网/起子进程调用（真跑链路里 socket/subprocess 层不生效，见 SUMMARY 试点证据）→ 不真跑 |
| `big/zenodo/21681849__11_generate_figure_02_annual_significant_extent.py` | 静态预筛：脚本含联网/起子进程调用（真跑链路里 socket/subprocess 层不生效，见 SUMMARY 试点证据）→ 不真跑 |
| `big/zenodo/21681849__13_generate_figure_13_integrated_synthesis.py` | 静态预筛：脚本含联网/起子进程调用（真跑链路里 socket/subprocess 层不生效，见 SUMMARY 试点证据）→ 不真跑 |
| `big/zenodo/21852099__PLOS_Main_Figure_Rebuild.py` | 静态预筛：脚本含联网/起子进程调用（真跑链路里 socket/subprocess 层不生效，见 SUMMARY 试点证据）→ 不真跑 |
| `big/zenodo/22089837__cascade_shelf_analysis__1_.py` | 静态预筛：脚本含联网/起子进程调用（真跑链路里 socket/subprocess 层不生效，见 SUMMARY 试点证据）→ 不真跑 |
| `big/zenodo/22089837__cascade_shelf_analysis__2_.py` | 静态预筛：脚本含联网/起子进程调用（真跑链路里 socket/subprocess 层不生效，见 SUMMARY 试点证据）→ 不真跑 |
| `big/zenodo/22949729__run_demo.py` | 静态预筛：脚本含联网/起子进程调用（真跑链路里 socket/subprocess 层不生效，见 SUMMARY 试点证据）→ 不真跑 |
| `big/zenodo/23062302__trend_maps_withControls.py` | 静态预筛：脚本含联网/起子进程调用（真跑链路里 socket/subprocess 层不生效，见 SUMMARY 试点证据）→ 不真跑 |
| `big/zenodo/3924688__plot_simulation.py` | 静态预筛：脚本含联网/起子进程调用（真跑链路里 socket/subprocess 层不生效，见 SUMMARY 试点证据）→ 不真跑 |
| `big/zenodo/7016758__toa_cre_anom.py` | 静态预筛：脚本含联网/起子进程调用（真跑链路里 socket/subprocess 层不生效，见 SUMMARY 试点证据）→ 不真跑 |

## 6 超时明细（na_timeout 71）
- `018/gallery/scikitlearn/applications/plot_digits_denoising.py`
- `018/gallery/scikitlearn/ensemble/plot_hgbt_regression.py`
- `018/gallery/scikitlearn/ensemble/plot_stack_predictors.py`
- `018/gallery/scikitlearn/inspection/plot_linear_model_coefficient_interpretation.py`
- `018/gallery/scikitlearn/inspection/plot_partial_dependence.py`
- `018/gallery/scikitlearn/kernel_approximation/plot_scalable_poly_kernels.py`
- `018/gallery/scikitlearn/linear_model/plot_poisson_regression_non_normal_loss.py`
- `018/gallery/scikitlearn/linear_model/plot_sgd_early_stopping.py`
- `018/gallery/scikitlearn/linear_model/plot_sparse_logistic_regression_mnist.py`
- `018/gallery/scikitlearn/linear_model/plot_tweedie_regression_insurance_claims.py`
- `018/gallery/scikitlearn/manifold/plot_swissroll.py`
- `018/gallery/scikitlearn/manifold/plot_t_sne_perplexity.py`
- `018/gallery/scikitlearn/model_selection/plot_cost_sensitive_learning.py`
- `018/gallery/scikitlearn/model_selection/plot_tuned_decision_threshold.py`
- `018/gallery/scikitlearn/multioutput/plot_classifier_chain_yeast.py`
- `018/gallery/scikitlearn/neural_networks/plot_mnist_filters.py`
- `018/gallery/scikitlearn/preprocessing/plot_target_encoder.py`
- `018/gallery/scikitlearn/release_highlights/plot_release_highlights_1_2_0.py`
- `018/gallery/scikitlearn/svm/plot_svm_scale_c.py`
- `018/zenodo/20154193__Cylinder.py`
- `018/zenodo/20154193__RealSpace.py`
- `018/zenodo/21534407__verification_script_v4.py`
- `018/zenodo/21933057__reproduce_paper.py`
- `018/zenodo/22235219__create_fig2.py`
- `018/zenodo/22934864__civilizational_cognitive_transitions_analysis.py`
- `big/gallery/scikit-learn-dev/linear_model/plot_sgd_early_stopping.py`
- `big/gallery/scikit-learn-dev/linear_model/plot_sparse_logistic_regression_mnist.py`
- `big/github/statsmodels/archive/examples/ex_kernel_regression3.py`
- `big/zenodo/10278919__gradient_consensus_demo.py`
- `big/zenodo/10778302__cc_ppmodel.py`
- `big/zenodo/15867446__Ellipsiodal.py`
- `big/zenodo/17521316__phaseshift_X_multiseed.py`
- `big/zenodo/17904980__LCS4B_Living_Curvature_Seam.py`
- `big/zenodo/18648519__Fig3.py`
- `big/zenodo/18680601__hbnpolaritons.py`
- `big/zenodo/19372816__drps_appendix_D-1.py`
- `big/zenodo/19774947__Vital_AI_Simulations_v3.py`
- `big/zenodo/20314372__ferrari_extreme_edition_v4_2__no_brakes_collapse_experiment__canonical______0_85.py`
- `big/zenodo/20516972__I_corr_convergence_PRReady.py`
- `big/zenodo/20539950__quadratic_drag_projectile.py`
- `big/zenodo/20791994__s1_alpha_sensitivity.py`
- `big/zenodo/20813032__desorption.py`
- `big/zenodo/20813032__desorption_smallGrains.py`
- `big/zenodo/20813032__simulPompages.py`
- `big/zenodo/20944498__ivl_pso_simulation-3.py`
- `big/zenodo/21323500__mc_reduced.py`
- `big/zenodo/21332984__mcda_diagnostic_FIXED.py`
- `big/zenodo/21390665__06_S4_targeted_outreach.py`
- `big/zenodo/21390665__Figure_S12_targeted_exposure_PNAS.py`
- `big/zenodo/21390665__s4_dynamic_mechanism_patched.py`
- `big/zenodo/21408851__cme_canonical.py`
- `big/zenodo/21420735__fpm_simulator.py`
- `big/zenodo/21446071__recovery_order_verification.py`
- `big/zenodo/21451913__Argo10_correct.py`
- `big/zenodo/21451913__Argo10_correct_fixed.py`
- `big/zenodo/21489795__public_reputation_dynamics_model.py`
- `big/zenodo/21499076__aps_journal_.py`
- `big/zenodo/21500264__evidence_closed_loop.py`
- `big/zenodo/21640484__dispersion_relation_circular.py`
- `big/zenodo/21700444__figB_lambda_multi.py`
- `big/zenodo/21754337__generate_all_figures_and_simulations.py`
- `big/zenodo/21778506__simulations_article1.py`
- `big/zenodo/21874523__aiwan_code.py`
- `big/zenodo/21901089__simulation.py`
- `big/zenodo/22151044__2026h_figures.py`
- `big/zenodo/22342594__prelim_study.py`
- `big/zenodo/22797611__lls_relaxation.py`
- `big/zenodo/23074192__figures_final.py`
- `big/zenodo/23079335__CGH_gen_PC_3D.py`
- `big/zenodo/23162329__3_-Geometric_Work.py`
- `big/zenodo/23210799__fig2_plot5.py`

## 4b 判定层拦截明细（na_gui 345 / na_stdin 464）
| 脚本路径 | 判定 | 守卫证据（截断） |
|---|---|---|
| `018/gallery/animation/animation_demo.py` | na_gui | A 档静态防弹窗：真会开窗的信号 plt.pause()（不真跑） |
| `018/gallery/axisartist/simple_axis_pad.py` | na_gui | rc=1 reason=fail err=脚本本身在无头环境跑不通（缺数据/依赖，或需要 GUI/交互），无法用 Agg 验证这次写回是否可用 → 默认**不保留**：已回滚。确实要保留请显式加 --keep-unverified。 |
| `018/gallery/color/color_sequences.py` | na_gui | rc=1 reason=fail err=脚本本身在无头环境跑不通（缺数据/依赖，或需要 GUI/交互），无法用 Agg 验证这次写回是否可用 → 默认**不保留**：已回滚。确实要保留请显式加 --keep-unverified。 |
| `018/gallery/event_handling/ginput_manual_clabel_sgskip.py` | na_gui | A 档静态防弹窗：真会开窗的信号 plt.waitforbuttonpress(), plt.ginput()（不真跑） |
| `018/gallery/event_handling/pick_event_demo.py` | na_gui | rc=1 reason=fail err=目标值在重跑后仍未达标：figsize [8.5, 5.5]≠[6.4, 4.8]（脚本本身能跑通）。提示：脚本在第 208 行（savefig/plt.show）之后可能还有会覆盖布局的调用；把那 |
| `018/gallery/images_contours_and_fields/colormap_normalizations.py` | na_gui | rc=1 reason=fail err=目标值在重跑后仍未达标：figsize [8.5, 5.5]≠[6.4, 4.8]（脚本本身能跑通）。提示：脚本在第 153 行（savefig/plt.show）之后可能还有会覆盖布局的调用；把那 |
| `018/gallery/images_contours_and_fields/colormap_normalizations_symlognorm.py` | na_gui | rc=1 reason=fail err=目标值在重跑后仍未达标：figsize [8.5, 5.5]≠[6.4, 4.8]（脚本本身能跑通）。提示：脚本在第 85 行（savefig/plt.show）之后可能还有会覆盖布局的调用；把那些 |
| `018/gallery/images_contours_and_fields/image_annotated_heatmap.py` | na_gui | rc=1 reason=fail err=脚本本身在无头环境跑不通（缺数据/依赖，或需要 GUI/交互），无法用 Agg 验证这次写回是否可用 → 默认**不保留**：已回滚。确实要保留请显式加 --keep-unverified，或先修好 |
| `018/gallery/images_contours_and_fields/pcolor_demo.py` | na_gui | rc=1 reason=fail err=目标值在重跑后仍未达标：figsize [8.5, 5.5]≠[6.4, 4.8]（脚本本身能跑通）。提示：脚本在第 108 行（savefig/plt.show）之后可能还有会覆盖布局的调用；把那 |
| `018/gallery/images_contours_and_fields/plot_streamplot.py` | na_gui | rc=1 reason=fail err=脚本本身在无头环境跑不通（缺数据/依赖，或需要 GUI/交互），无法用 Agg 验证这次写回是否可用 → 默认**不保留**：已回滚。确实要保留请显式加 --keep-unverified。 |
| `018/gallery/lines_bars_and_markers/barchart.py` | na_gui | rc=1 reason=fail err=脚本本身在无头环境跑不通（缺数据/依赖，或需要 GUI/交互），无法用 Agg 验证这次写回是否可用 → 默认**不保留**：已回滚。确实要保留请显式加 --keep-unverified，或先修好 |
| `018/gallery/lines_bars_and_markers/broken_barh.py` | na_gui | rc=1 reason=fail err=脚本本身在无头环境跑不通（缺数据/依赖，或需要 GUI/交互），无法用 Agg 验证这次写回是否可用 → 默认**不保留**：已回滚。确实要保留请显式加 --keep-unverified，或先修好 |
| `018/gallery/lines_bars_and_markers/hat_graph.py` | na_gui | rc=1 reason=fail err=脚本本身在无头环境跑不通（缺数据/依赖，或需要 GUI/交互），无法用 Agg 验证这次写回是否可用 → 默认**不保留**：已回滚。确实要保留请显式加 --keep-unverified，或先修好 |
| `018/gallery/misc/demo_agg_filter.py` | na_gui | rc=1 reason=fail err=脚本本身在无头环境跑不通（缺数据/依赖，或需要 GUI/交互），无法用 Agg 验证这次写回是否可用 → 默认**不保留**：已回滚。确实要保留请显式加 --keep-unverified，或先修好 |
| `018/gallery/mplot3d/pathpatch3d.py` | na_gui | rc=1 reason=fail err=脚本本身在无头环境跑不通（缺数据/依赖，或需要 GUI/交互），无法用 Agg 验证这次写回是否可用 → 默认**不保留**：已回滚。确实要保留请显式加 --keep-unverified，或先修好 |
| `018/gallery/networkx/3d_drawing/plot_facebook_3d.py` | na_gui | rc=1 reason=fail err=脚本本身在无头环境跑不通（缺数据/依赖，或需要 GUI/交互），无法用 Agg 验证这次写回是否可用 → 默认**不保留**：已回滚。确实要保留请显式加 --keep-unverified，或先修好 |
| `018/gallery/networkx/algorithms/plot_betweenness_centrality.py` | na_gui | rc=1 reason=fail err=脚本本身在无头环境跑不通（缺数据/依赖，或需要 GUI/交互），无法用 Agg 验证这次写回是否可用 → 默认**不保留**：已回滚。确实要保留请显式加 --keep-unverified。 |
| `018/gallery/networkx/algorithms/plot_lca.py` | na_gui | rc=1 reason=fail err=脚本本身在无头环境跑不通（缺数据/依赖，或需要 GUI/交互），无法用 Agg 验证这次写回是否可用 → 默认**不保留**：已回滚。确实要保留请显式加 --keep-unverified。 |
| `018/gallery/networkx/algorithms/plot_subgraphs.py` | na_gui | rc=1 reason=fail err=脚本本身在无头环境跑不通（缺数据/依赖，或需要 GUI/交互），无法用 Agg 验证这次写回是否可用 → 默认**不保留**：已回滚。确实要保留请显式加 --keep-unverified。 |
| `018/gallery/networkx/drawing/plot_chess_masters.py` | na_gui | rc=1 reason=fail err=脚本本身在无头环境跑不通（缺数据/依赖，或需要 GUI/交互），无法用 Agg 验证这次写回是否可用 → 默认**不保留**：已回滚。确实要保留请显式加 --keep-unverified。 |
| `018/gallery/networkx/drawing/plot_custom_node_icons.py` | na_gui | rc=1 reason=fail err=脚本本身在无头环境跑不通（缺数据/依赖，或需要 GUI/交互），无法用 Agg 验证这次写回是否可用 → 默认**不保留**：已回滚。确实要保留请显式加 --keep-unverified，或先修好 |
| `018/gallery/networkx/drawing/plot_knuth_miles.py` | na_gui | rc=1 reason=fail err=脚本本身在无头环境跑不通（缺数据/依赖，或需要 GUI/交互），无法用 Agg 验证这次写回是否可用 → 默认**不保留**：已回滚。确实要保留请显式加 --keep-unverified。 |
| `018/gallery/networkx/drawing/plot_multigraphs.py` | na_gui | rc=1 reason=fail err=脚本本身在无头环境跑不通（缺数据/依赖，或需要 GUI/交互），无法用 Agg 验证这次写回是否可用 → 默认**不保留**：已回滚。确实要保留请显式加 --keep-unverified，或先修好 |
| `018/gallery/networkx/drawing/plot_rainbow_coloring.py` | na_gui | rc=1 reason=fail err=脚本本身在无头环境跑不通（缺数据/依赖，或需要 GUI/交互），无法用 Agg 验证这次写回是否可用 → 默认**不保留**：已回滚。确实要保留请显式加 --keep-unverified。 |
| `018/gallery/networkx/drawing/plot_sampson.py` | na_gui | rc=1 reason=fail err=脚本本身在无头环境跑不通（缺数据/依赖，或需要 GUI/交互），无法用 Agg 验证这次写回是否可用 → 默认**不保留**：已回滚。确实要保留请显式加 --keep-unverified，或先修好 |
| `018/gallery/networkx/drawing/plot_spring_layout.py` | na_gui | rc=1 reason=fail err=脚本本身在无头环境跑不通（缺数据/依赖，或需要 GUI/交互），无法用 Agg 验证这次写回是否可用 → 默认**不保留**：已回滚。确实要保留请显式加 --keep-unverified。 |
| `018/gallery/networkx/graph/plot_dag_layout.py` | na_gui | rc=1 reason=fail err=脚本本身在无头环境跑不通（缺数据/依赖，或需要 GUI/交互），无法用 Agg 验证这次写回是否可用 → 默认**不保留**：已回滚。确实要保留请显式加 --keep-unverified，或先修好 |
| `018/gallery/networkx/graph/plot_football.py` | na_gui | rc=1 reason=fail err=脚本本身在无头环境跑不通（缺数据/依赖，或需要 GUI/交互），无法用 Agg 验证这次写回是否可用 → 默认**不保留**：已回滚。确实要保留请显式加 --keep-unverified。 |
| `018/gallery/networkx/graph/plot_triad_types.py` | na_gui | rc=1 reason=fail err=脚本本身在无头环境跑不通（缺数据/依赖，或需要 GUI/交互），无法用 Agg 验证这次写回是否可用 → 默认**不保留**：已回滚。确实要保留请显式加 --keep-unverified。 |
| `018/gallery/networkx/graph/plot_visibility_graph.py` | na_gui | rc=1 reason=fail err=脚本本身在无头环境跑不通（缺数据/依赖，或需要 GUI/交互），无法用 Agg 验证这次写回是否可用 → 默认**不保留**：已回滚。确实要保留请显式加 --keep-unverified。 |
| … | | 共 809 份，其余见 results.jsonl |

## 4d 联网/子进程静态预筛（na_skip 29 份，**不真跑**）

原因：试点实测发现**真跑链路里 socket/subprocess 那一层没有生效** ——
沙箱判断"是不是在跑用户脚本"看 `argv[0]` 是否为游乐场内的真实文件，而
verify 子进程的 argv[0] 是 mpltweak 模块路径（`python -m mpltweak.verify`），
于是被当作 harness 进程，只剩文件层（文件层对 harness 也生效，所以
**越界读写删依然是拦得住的**，试点证据见 §4c）。按"指望不上的层就别给它
机会"的口径，这类脚本静态预筛、不真跑。

> 边界（如实说明）：① 沙箱"是否脚本进程"以 `argv[0]` 判定，**真跑链路（`python -m mpltweak.verify …`）落在 harness 侧，只有文件层生效**；
> ② 联网/子进程层在本版靠**静态预筛**兜住（命中即 `na_skip`，不真跑）；
> ③ 精细设计判定规则（例如"argv 里出现游乐场内的脚本路径也算脚本进程"，需同时避免 CLI 自身被误判而拦死 verify 的 spawn）列入 **0.1.11 候选**。

| 脚本路径 | 原因 |
|---|---|
| `018/gallery/lines_bars_and_markers/timeline.py` | 静态预筛：脚本含联网/起子进程调用（真跑链路里 socket/subprocess 层不生效，见 SUMMARY 试点证据）→ 不真跑 |
| `018/gallery/scikitlearn/applications/plot_out_of_core_classification.py` | 静态预筛：脚本含联网/起子进程调用（真跑链路里 socket/subprocess 层不生效，见 SUMMARY 试点证据）→ 不真跑 |
| `018/gallery/text_labels_and_annotations/mathtext_examples.py` | 静态预筛：脚本含联网/起子进程调用（真跑链路里 socket/subprocess 层不生效，见 SUMMARY 试点证据）→ 不真跑 |
| `018/zenodo/20681418__wikipedia_pageviews.py` | 静态预筛：脚本含联网/起子进程调用（真跑链路里 socket/subprocess 层不生效，见 SUMMARY 试点证据）→ 不真跑 |
| `018/zenodo/22691847__e1_calibration_missingness.py` | 静态预筛：脚本含联网/起子进程调用（真跑链路里 socket/subprocess 层不生效，见 SUMMARY 试点证据）→ 不真跑 |
| `018/zenodo/22691847__e1_dca_figure7.py` | 静态预筛：脚本含联网/起子进程调用（真跑链路里 socket/subprocess 层不生效，见 SUMMARY 试点证据）→ 不真跑 |
| `018/zenodo/22691847__e1_figures_1_4.py` | 静态预筛：脚本含联网/起子进程调用（真跑链路里 socket/subprocess 层不生效，见 SUMMARY 试点证据）→ 不真跑 |
| `big/github/cartopy/examples/lines_and_polygons/ocean_bathymetry.py` | 静态预筛：脚本含联网/起子进程调用（真跑链路里 socket/subprocess 层不生效，见 SUMMARY 试点证据）→ 不真跑 |
| `big/github/cartopy/examples/scalar_data/aurora_forecast.py` | 静态预筛：脚本含联网/起子进程调用（真跑链路里 socket/subprocess 层不生效，见 SUMMARY 试点证据）→ 不真跑 |
| `big/github/statsmodels/examples/python/interactions_anova.py` | 静态预筛：脚本含联网/起子进程调用（真跑链路里 socket/subprocess 层不生效，见 SUMMARY 试点证据）→ 不真跑 |
| `big/github/statsmodels/examples/python/statespace_sarimax_stata.py` | 静态预筛：脚本含联网/起子进程调用（真跑链路里 socket/subprocess 层不生效，见 SUMMARY 试点证据）→ 不真跑 |
| `big/zenodo/14957162__FigurasPorArticulo.py` | 静态预筛：脚本含联网/起子进程调用（真跑链路里 socket/subprocess 层不生效，见 SUMMARY 试点证据）→ 不真跑 |
| `big/zenodo/19689912__yxjoivv.py` | 静态预筛：脚本含联网/起子进程调用（真跑链路里 socket/subprocess 层不生效，见 SUMMARY 试点证据）→ 不真跑 |
| `big/zenodo/20286538__nac_fchk_matrix.py` | 静态预筛：脚本含联网/起子进程调用（真跑链路里 socket/subprocess 层不生效，见 SUMMARY 试点证据）→ 不真跑 |
| `big/zenodo/20752617__inten-history.py` | 静态预筛：脚本含联网/起子进程调用（真跑链路里 socket/subprocess 层不生效，见 SUMMARY 试点证据）→ 不真跑 |
| `big/zenodo/20921761__run_benchmark.py` | 静态预筛：脚本含联网/起子进程调用（真跑链路里 socket/subprocess 层不生效，见 SUMMARY 试点证据）→ 不真跑 |
| `big/zenodo/20921761__run_real_benchmark.py` | 静态预筛：脚本含联网/起子进程调用（真跑链路里 socket/subprocess 层不生效，见 SUMMARY 试点证据）→ 不真跑 |
| `big/zenodo/21299013__6-4-2-3_Alternative_Substructure_prediction.py` | 静态预筛：脚本含联网/起子进程调用（真跑链路里 socket/subprocess 层不生效，见 SUMMARY 试点证据）→ 不真跑 |
| `big/zenodo/21408851__empirical_tdi.py` | 静态预筛：脚本含联网/起子进程调用（真跑链路里 socket/subprocess 层不生效，见 SUMMARY 试点证据）→ 不真跑 |
| `big/zenodo/21497627__07_generate_spatial_synthesis_heatmap.py` | 静态预筛：脚本含联网/起子进程调用（真跑链路里 socket/subprocess 层不生效，见 SUMMARY 试点证据）→ 不真跑 |
| `big/zenodo/21681849__11_generate_figure_02_annual_significant_extent.py` | 静态预筛：脚本含联网/起子进程调用（真跑链路里 socket/subprocess 层不生效，见 SUMMARY 试点证据）→ 不真跑 |
| `big/zenodo/21681849__13_generate_figure_13_integrated_synthesis.py` | 静态预筛：脚本含联网/起子进程调用（真跑链路里 socket/subprocess 层不生效，见 SUMMARY 试点证据）→ 不真跑 |
| `big/zenodo/21852099__PLOS_Main_Figure_Rebuild.py` | 静态预筛：脚本含联网/起子进程调用（真跑链路里 socket/subprocess 层不生效，见 SUMMARY 试点证据）→ 不真跑 |
| `big/zenodo/22089837__cascade_shelf_analysis__1_.py` | 静态预筛：脚本含联网/起子进程调用（真跑链路里 socket/subprocess 层不生效，见 SUMMARY 试点证据）→ 不真跑 |
| `big/zenodo/22089837__cascade_shelf_analysis__2_.py` | 静态预筛：脚本含联网/起子进程调用（真跑链路里 socket/subprocess 层不生效，见 SUMMARY 试点证据）→ 不真跑 |
| `big/zenodo/22949729__run_demo.py` | 静态预筛：脚本含联网/起子进程调用（真跑链路里 socket/subprocess 层不生效，见 SUMMARY 试点证据）→ 不真跑 |
| `big/zenodo/23062302__trend_maps_withControls.py` | 静态预筛：脚本含联网/起子进程调用（真跑链路里 socket/subprocess 层不生效，见 SUMMARY 试点证据）→ 不真跑 |
| `big/zenodo/3924688__plot_simulation.py` | 静态预筛：脚本含联网/起子进程调用（真跑链路里 socket/subprocess 层不生效，见 SUMMARY 试点证据）→ 不真跑 |
| `big/zenodo/7016758__toa_cre_anom.py` | 静态预筛：脚本含联网/起子进程调用（真跑链路里 socket/subprocess 层不生效，见 SUMMARY 试点证据）→ 不真跑 |

## 9 src 溯源（本次运行时的源码状态）
- git HEAD: `02998e66b0ad98bdd4fdd71db55a4b9e018c5924`；工作区未提交改动 37 个文件（提交闸门归船长，本次运行就在这个工作区状态下进行）
- `src/mpltweak/apply.py` md5 `ea6bae7111fe290f42c584db4f30c772`
- `src/mpltweak/writeback.py` md5 `e7595412a52f64265b87583de9e4f5df`
- `src/mpltweak/verify.py` md5 `5b54f60f62db73164e5b1a8d467b22a7`
- `src/mpltweak/messages.py` md5 `811af04fdff4723d230f7c204d84626d`
- `src/mpltweak/preflight.py` md5 `65b20d3858a72557dc4d92c66cc28e04`
- `src/mpltweak/params.py` md5 `a9a27f26835b7d310fda57eee130c110`
- `src/mpltweak/revert.py` md5 `d744a6b745ffffa582470332cebfadd5`
- `src/mpltweak/launch.py` md5 `f7ce0b653a65e18d33b4a110480b971f`
- `src/mpltweak/cli.py` md5 `ad942b2dbd36eaf0468f5913b4709f93`

## 10 复现
```
set MPLBACKEND=Agg
set PYTHONPATH=<work>\mpltweak\src
cd <work>\mpltweak
:: 试点：前 48 份 + 沙箱逐类拦截证据（先跑这个）
<python> tests/test_corpus_invariants.py --pilot 48
:: 全量 1,837 份（随时可中断；再跑同一条命令自动续跑，不重不漏）
<python> tests/test_corpus_invariants.py --resume
```

## 11 运行观察（内存与并发；外部采样）

内存/并发观察（由 t28 owner 在 runner 外部采样；postfix 轮：jobs=6 起跑 → 干净停手一次 → --resume --jobs 8 → 内存到线再停手一次 → --resume --jobs 6 跑完）

| 阶段 | 已完成份数 | 进程数 | 进程树总内存 |
|---|---:|---:|---:|
| jobs=6 第1次 | 390 | 22 | 1762 MB |
| jobs=6 第2次 | 466 | 26 | 2298 MB |
| jobs=6 第3次 | 748 | 27 | 2384 MB |
| jobs=6 停手（754 份） | 754 | 0 | 0 MB |
| jobs=8 第1次 | 930 | 30 | 2026 MB |
| jobs=8 第2次 | 1116 | 30 | 1951 MB |
| jobs=8 第3次（触发动作） | 1296 | 32 | 3667 MB |
| jobs=8 停手（1355 份，实测 3804MB） | 1355 | 28 | 3804 MB |
| jobs=6 续跑第1次 | 1464 | 25 | 1525 MB |

- **动作线**：进程树总内存 ~4000 MB ⇒ 干净停手（按自己的路径特征精确匹配，未触碰他人进程）→ 降并发续跑。① 起始 `--jobs 6`；② 按船长裁定在 754 份处干净停手（精确匹配自己的 25 个进程，未碰他人）→ `--resume --jobs 8`；③ **内存到线**：jobs=8 段 5 分钟内由 1,951MB 涨到 3,667MB，随即在 **3,804MB** 停手（精确匹配、零残留、零坏 JSON、在飞的 3 个进程自行跑完落地 1354→1355）→ `--resume --jobs 6` 跑完（按规则**降级后不再升回 8**）。两次停手均：坏 JSON 0、临时文件残留 0、剩余进程 0（复查 12 秒仍为 0 ⇒ 未误伤他人进程）。逐份原子落盘全程开启。
- **成因**：t23 的教训在本轮复现：进程数并不单调，但进程树总量可以**单调爬升**（本轮 jobs=8 段 1,951→3,667→3,804 MB，5 分钟内 +1.7GB），**成因仍未定位**（不排除 worker 进出时的碎片/缓存累积，也不排除单个 worker 生命周期内的缓慢增长；既不断言是泄漏、也不断言不是）。
- 本轮分三段跑完（段间为按裁定/动作线的干净停手 + `--resume`，非崩溃）：seg1 jobs=6（0→754 份）、seg2 jobs=8（754→1,355 份）、seg3 jobs=6（1,355→1,837 份）。
- **注意**：SUMMARY §0 的「本轮实跑 N 份 / 耗时」反映的是**最后一次续跑**那一段（不是整轮）；整轮的真实分段耗时见本文件与交付说明。
- **下次跑的调参建议**：延续并强化 t23 建议：① **默认 jobs 上限取 6**（8 槽在 stdin/hanger 密集区会快速爬升到 4GB 线）；② 或每 N 份重启一次 worker 池；③ 采样间隔 5 分钟、**动作线 4GB（判据写 `>=4000`）**；④ 停手/续跑动作已被两轮验证为零损坏，可作为常规手段。
- 其它：本轮为 postfix（修复后）轮；**禁止跨轮 `--resume` 混用**（同轮内为切 jobs 而做的续跑合法：`RUN_META` 池指纹与 limit 逐字一致）。

## 12 本轮的口径与给下一步的提示

- **本轮是"修复前基线"**：违约 3 例（同一签名）如实记录，**未删除、未改标**；最小复现见交付说明（四时刻哈希 → 块模式重试循环里的守卫基准缺陷）。
- **修复后重跑必须换新目录、禁用 `--resume`**：例如 `--out <workspace>/_review/corpus_invariants_t23_postfix`（**不要**加 `--resume`）—— `RUN_META` 的池指纹**不跟踪 src 版本**，续跑会把"修复前/修复后"的结果混进同一份统计，数字就废了。

## 13 与 t23（修复前基线）的逐桶对照

> 两轮口径完全一致（同一候选池 / 同一分层分母 / 同一四律统计方式 / 显式 `MPLBACKEND=Agg` / 零 GUI 对象），**唯一差异 = 本轮用的是 t27 修复后的 `writeback.py`**。

| 判定 | t23（修复前） | 本轮 postfix（修复后） | 差 |
|---|---:|---:|---:|
| **pass** | 929 | 928 | -1 |
| **violation** | 3 | 0 | -3 |
| **na_gui** | 342 | 345 | +3 |
| **na_stdin** | 464 | 464 | +0 |
| **na_skip** | 29 | 29 | +0 |
| **na_timeout** | 70 | 71 | +1 |
| **na_reject** | 0 | 0 | +0 |
| **na_error** | 0 | 0 | +0 |
| 合计 | 1837 | 1837 | +0 |

- **两轮的唯一差异 = t27 对 `writeback.py` 块路径的两处改动**（守卫基准改为“我们最后写入的内容” + 命中/重试前回滚）。除此之外口径完全一致：同一候选池（`_review/corpus_filter_t7/filter.jsonl`，both_ok=1,837，pool_sha256 `bf22238f61184f17…`）、同一分层分母、同一四律统计方式、显式 `MPLBACKEND=Agg`、零 GUI 对象、禁跨轮 `--resume`（每轮独立目录）。`RUN_META.json` 逐字对比：两轮的 pool_sha256 / limit(null) / corpus(corpus_union) / fast(false) / n_all(1837) 完全相同，**唯一差异是 jobs**（t23 末段 4 vs 本轮 6→8→6）——jobs 不进入任何一份脚本的判定。
- **为什么 t23 的基线里已经包含 T57 稀疏轴改动、却不构成污染**：`apply_inplace` 的 T57 修复是本 0.1.10 周期内**已完成的既定改动**（原始需求里的“稀疏轴写回错轴”），只是整个周期都没 commit（工作区 37 个未提交文件），并非“半成品混入”。t23 那一轮 SUMMARY 记录的 `src/mpltweak/writeback.py` md5 = `88297ccf71026dc69ae4f64ab3425cc9`，**正是 T57 已包含、而 t27 尚未施加时的状态**；本轮 postfix 记录的是 `e7595412a52f64265b87583de9e4f5df`。md5 的差异**只来自 t27 的两处改动**（t14 的守卫/回滚框架两轮都在）。
- **修复前（t23）的 3 例违约**：`018/gallery/images_contours_and_fields/pcolor_demo.py`(3260→3491)、`018/gallery/statistics/boxplot_demo.py`(8093→8324)、`018/gallery/statistics/psd_demo.py`(5357→5588)——**都恰好 +231 字节**、`reason1='external_modified'`、`style='block'`；四时刻哈希证明“守卫把自身写入当外部改动 + 命中不回滚”。
- **修复后（t27 单例复跑，已取证）**：同三例均为 `最终字节 == 原始字节（差 +0）`、无调整块残留、`reason='fail'`、无 `external_modified`，且时间线里出现 `T2 回滚尝试后 … 返回='已回滚'`（修复前没有这一行）→ 按 worker 口径这三例从 **violation** 落到 **na_reject**（引擎拒写、字节未动）。

产出: `results.jsonl`（逐份结果）、`results/s_%03d.json`（逐份原子落盘，续跑依据）、`RUN_META.json`（池指纹对账）+ 本文件。
