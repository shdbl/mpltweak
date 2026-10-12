# CHANGELOG

## 0.1.9

### 这一版用户能感知到的变化

- **坏参数不再被写进你的源码，也不再"假装成功"（T61，行为变更）**：本版校验器覆盖的非法参数（尺寸 / 字号 / 轴序号 / NaN / inf 等）在**任何写盘或插块之前**被拒绝（`reason=bad_params`、`rc=2`、`ok=false`、字节不变、不插块）。**只读预览（不加 `--write`）同样会拒绝** —— 参数文件本身非法与走不走写分支无关。
- **拒绝或未写盘时，终端如实输出"未写回 + 原因"（T68）**：此前人读那一行仍打印 `[OK] 已写回:`；`--json` 的字段与取值未变。
- **参数里没写 y 侧字号时，一个字节都不动 y（T73，行为变更）**：申请改 x 就只改 x，y 保持源码原值。
- **追加的调整块只用能静态证明的定位（T70，行为变更）**：证不出"作用在哪张图 / 哪条轴"时**拒绝写盘**，并打印一段可直接粘贴的手改片段。
- **`describe` 补上 y 侧字号字段，`verify` 真的判 y（T75）**：`label_fontsize_y` / `tick_fontsize_y` 现在会导出；y 写错会判失败；**已请求却判不了的字段**会出现在 `--json` 的 `detail` 里（判不了 ≠ 判失败，但会说出来）。
- **连改两次后 `revert` 回到原件（T71）**：备份改为**首份优先**（在本版实测的调用序列下成立），`revert` 不再只回到中间态。
- **源码末尾没有换行符时，调整块不再被重复插入（T46）**。
- **同一张图里重名轴、且参数只覆盖其中一条时，不再把改动写到另一条轴那一行（T57）**。
- **图级尺寸的两处"静默丢改动"已修（Bug 6 / T64）**：改 `set_size_inches` 之后的覆盖，以及把 matplotlib 默认值当作真实尺寸的判据。

### 修复

每条格式：现象 → 根因 → 修法 → 证据。

**参数校验（T61）**
- 现象：7 类非法参数会被真的写进源码（`pos` 宽或高 ≤ 0、整块在画布外、`figsize` 任一维 ≤ 0、`fontsize < 0`、轴序号越界或为负）；另有 3 类静默假装成功（`fig_index` 越界 / `version` 缺失 / `version ≠ 4` 报 `no_change` + `rc=0`）。
- 根因：这些字段在写盘路径上没有统一的前置校验，越界轴序号还会被 Python 解释成"最后一张轴"。
- 修法：在写盘与插块之前统一校验，失败即 `bad_params`。**明确不拒**：仍有可见部分的负 `left`/`bottom`、倒置的 `xlim`/`ylim`、`fontsize == 0`（合法，只在 warnings 里提示）。
- 证据：并集语料 D 场景 482 行中 280 行产出用例 × 27 类 = **7,560 次**；修复后在合法用法之外 **0 不安全**，3 类静默假成功的 `not_rejected` **21 → 0**。反向：逐条关掉新增校验 ⇒ 对应用例变红（FAIL=3/3/1/3/1/2/1）。

**写回定位（T67 / T70 / T57 / T46）**
- 现象：`pos` 值相等却仍被写盘；追加块用运行期下标定位时，可能"目标轴零改动，却把主图改成了色条那么大"（实测：运行期 12 条轴、`cax is fig.axes[0] = False`；块后 `fig.axes[0]` 变成 `(0.78, 0.18, 0.2, 0.03)` 而 cax 未变）。
- 根因：内联在实参里的 `add_axes` 认不出（回退追加块）；块用 `fig.axes[i]` / `plt.gca()` / `plt.gcf().axes[i]` / `zip(fig.axes, [...])` 这类**运行期下标**指对象，而运行期 `fig.axes` 还混着色条轴、嵌入轴与双轴。
- 修法：给 `pos` 加内联 `cax=fig.add_axes([...])` 的等值证明；块只允许静态可证的定位（目标对象的变量名、可证的 figure 名；纯 pyplot 且全文件只建一张图时 `plt.gcf()` 等价可用）；**证不出 ⇒ 拒绝写**（`applied=false`、字节不变、人读输出"未写回 + 原因"）并打印可粘贴骨架。同源修掉"同图重名轴写错行"（T57）与"尾部无换行导致块重复插入"（T46）。
- 证据：全库静态 **允许 441 / 拒绝 882，A 类归零**；S2 判据4 由改前 **A 674 / B 403 / C 81**（分母 1,158 块）到本版 **A 0 / B 0 / C 427**（分母 427 块）——**A+B 占比 93.0% → 0.0%**（只比占比，绝对数不并排）。T46：`replay_f_scene.py --all-diff` 由 **12/12 复现**降到 **0/12**。T57 配 §68 九格钉子用例（撤掉判据 ⇒ 3 条用例变红）。
- 附带说明（措辞纪律）：追加块路径**每次多出一个空行**（抽样 150/150，分布 `{1: 150}`；严格"逐字节 == pristine" = **0 / 1,286**）⇒ 对外只能说「除块本身与一个空行外，原文逐字节不变」。

**图级尺寸与幂等（Bug 6 / T64 / T71）**
- 现象：`figsize_already_ok()` 只看 `plt.subplots(figsize=…)` / `plt.figure(figsize=…)` 那一处，忽略其后的 `fig.set_size_inches(w, h)` ⇒ 判"尺寸已对" ⇒ 改动被静默丢弃；源码里读不出尺寸时会落回 matplotlib 默认值 6.4×4.8 ⇒ 目标恰好等于默认值时同样静默不改。T71：同一份参数连着 `apply --write` 两次，第二次又改字节；随后 `revert` 返回码 0 却没回到原件。
- 根因：T64 是判据只看一处、且拿默认值冒充真实尺寸；T71 是两条叠加 ——（1）带点号的接收者（`tree_disp.figure_.set_size_inches(10, 6)`）被误判成"本图被后续覆盖"，第二次 apply 认不出"尺寸已到位"而退回插块；（2）块通道的 `_make_backup` 重写固定名备份，备份变成"改过一次的中间态"。
- 修法：按**最后一条生效的 `set_size_inches`** 比对（`_later_size_override()` 改看接收者**根名字**，新增 `_rebound_between`，非变量名接收者不可证）；读不出字面量就**弃权退块**，不拿默认值冒充；备份**首份优先**（在本版实测的调用序列下成立）。
- 证据：`_probe_t64_e2e.py`（真实 CLI `apply --write --style auto --json` + 独立生效尺寸读取器，读不出记"未知"）修复前 **6/8** → 修复后 **8/8**。T71 用同一把尺子、同一输入：**`revert` 失败 2 → 0**；幂等失败 2 → 1（剩的那 1 例见"已知边界"，无数据损失）。语料 C 连跑三次：修复前 #2 插 5 行块（136651 → 136918 字节），修复后 #1 `ok`（原位）→ #2/#3 `no_change`、字节不变。

**y 侧字号（T73 / T75）**
- 现象：参数只有 `label_fontsize` 时，工具会把 y 也按 x 的值改掉（静态体检 **130 / 131 例**"只要求改 x、y 侧也被改了且未申报"：`scatter_demo2.py` → `changes=['ax0.label_fontsize']`、`warnings=[]`，而 `set_ylabel` 的 fontsize 15 → 21）。另一层：`describe` 从不导出 `label_fontsize_y` / `tick_fontsize_y`（后者连采集都没有），而 `verify` 不判 y ⇒ 存在"没比却说通过"的空间。
- 根因：写回层与判据层各有一处"y 未设置时沿用 x"的兼容回退（共 14 处）；采集侧未采 y 刻度字号；比对侧只认 x 侧字段。
- 修法：删掉该回退（`你说改 x 就只改 x`）；`describe` 纯增量补上两个 y 字段（既有键名 / 顺序 / 语义不变）；`verify` 增加 y 侧独立比对；**已请求却判不了的字段写进 `detail` 的"未判定字段"清单**。
- 证据：`describe` 对 x=11 / y=17、tick x=9 / y=13 的脚本导出 `label_fontsize 11.0 / tick_fontsize 9.0 / label_fontsize_y 17.0 / tick_fontsize_y 13.0`（真值，不是复制 x）；y 标签字号写错 ⇒ `ok=False detail='ax0 label_fontsize_y 23≠17.0'`；只给 x 且 x 的期望恰好等于源码 y 的值 ⇒ 判失败（旧行为会假通过）。反向：把回退加回去 ⇒ 5 条用例变红；关掉新增的 y 比较 ⇒ 3 条断言变红（含"没比却说通过"原样复现）。

**人读输出（T68）**
- 现象：工具没有写盘时（`bad_params` / 块目标证不出 / `no_fig`），终端人读行仍打印 `[OK] 已写回:`。
- 根因：人读层与 JSON 层取值来源不同，人读层没有接入"是否真的写盘"。
- 修法：人读行改为"**未写回：<原因>**"；`--json` 的字段与取值一个都不变。
- 证据：5 个场景与打补丁前逐字对比，JSON 相同。

### 追加修复：同图重名轴不再改错对象（T84）

- **现象**：`fig, (ax, ax) = plt.subplots(1, 2)` 这种"一条语句里同名解包"，参数只给 `index=0` 时，
  工具会改到**运行期属于第二条轴**的那行，并报 `applied=true` —— **改错对象 + 谎报成功**。
- **根因**：写回判据建立在"建图 / 建轴调用"上，只收 `add_axes` / `add_subplot`，
  一条语句里的**同名解包**不在其中；其后的单图反查又把这个名字补回映射 ⇒ 按名字定位 ⇒ 落到另一条轴。
- **修法**：新增"数建图赋值目标里同名出现次数"的判据；**名字绑定 ≥2 条轴、而参数未覆盖全部被绑定轴 ⇒ 禁止原位写回**
  （`applied=false`、`pending` 非空、字节不变、人读"未写回"）。
- **验证**：回归 5 条 PASS；**反向**（去掉新判据）`rc=1 / FAIL=5` 逐字复现上述症状；
  两种编码环境 runner **404 / 0 ALL PASS**。
- **已知边界（同族风险面）**：任何"名字绑定 ≥2 条轴"的写法（`ax = args[0]`、循环里重绑、`ax = any_list[k]`）
  都可能属同一类；本版只封住"**同名解包 + 参数未覆盖全部**"这一格，其余按证据在后续版本逐个收口。

### 已知边界（本版不声称的）

- **语义层"不判"的字段**（会显式说出来，不是"没比却说通过"）：`cmap`、`lines[]`（linewidth / color）、`legend`（fontsize / loc / labels）、`aspect_locked`、`is_colorbar`、`figsize_px`、`n_figs` / `version`。根因：采集侧（`verify._state`）没有采前五项，没有实际值就无从比对；现状：这些字段被请求却判不了时，会出现在 `--json` 的 `detail` 的"未判定字段"清单里；计划在 0.1.10 先补采集、再补比对。
- **明确拒绝（能力边界，不是失败）**：未命名 colorbar 轴、循环里建图 / 建轴、目标对象静态证不出时，不写盘、不插块（`applied=false`、`pending` 非空），并打印可粘贴的手改片段。全库 4,427 份里这类拒绝（含 fail-closed）共 **7** 例。
- **参数非法被拒 37 例的性质**：测量脚手架按站点推出的参数越过了校验器的合法边界（例：轴序号越界、同图重名轴），需逐例区分"测试台构造问题"与"工具校验过严"。影响：轴标签 x 的 92.5% 里有 11 例属此列；**剔掉后是 160/161 ≈ 99.4%，此口径必须随行标注**。
- **幂等 / 撤销**：字节层幂等成立；**判定层**在个别脚本上仍非幂等 —— 第二次 `apply` 的 `reason` 可能是 `best_effort` 而非 `no_change`（字节未再变、备份与 `revert` 均正确，**无数据损失**）。L0 m5（幂等）实测 **2,245 / 2,315**，其中 **70** 例全是上述 `best_effort + bytes_same=True`，另有 **69** 例 `precondition_no_write`（第一次未改动字节）记为 skipped。
- **拒绝路径下参数文件的时间戳仍会被刷新** ⇒ GUI 的"参数新鲜度"提示可能不反映"这次其实没写回"（人读输出与 `--json` 都如实给"未写回"）。
- **`clim` 在语料上未覆盖**（寻址模型全判够不到）；扫描器**只覆盖原位那 10 个族**，`spines` / `xscale` / `yscale` / `clim` 本次**无站点**（块路径的独立测量是另一次、口径不同，不与本表拼接）。
- **尾行换行与块**：追加块路径每次多出一个空行（抽样 150/150）；严格"逐字节 == pristine" = 0 / 1,286（详见"修复 · 写回定位"末条）。
- **本版对外只报静态层结论**：以上主表与块判据都来自不执行语料脚本的静态判定；运行时行为验证推迟到下一版。
- **0.1.10 候选（本轮实测发现，未修）**：① 消息层在非 UTF-8 控制台上可能吞行 —— 父进程不设 `PYTHONIOENCODING` 时，"未写回"这类中文提示在子进程里没有输出（用例捕获到空列表）；候选修法是输出带 `errors='replace'` 或 ASCII 回退。② 块渲染器里 T67 的 `_pos_block_target_ok` / `_runtime_axes_count` 已成死代码，本轮不删（删了会让 §71 / §69e 的反向配方失效），记入清理项。

### 测试与验收

**套件面**
- 两种编码环境下 `tests/test_writeback.py` 均 **ALL PASS / 0 FAIL**；`pytest -q` = **rc=0**（**34 passed / 5 skipped**，含 hygiene 棘轮，基线未涨）。
- 反向验证：本条修复的判据逐项"关掉"后，对应用例变红（T61 七条 FAIL=3/3/1/3/1/2/1；T57 三条；T73 五条；T75 三条）。

**静态实测面**（语料并集 = **4,427 / 4,427** 行覆盖；1 个脚本隔离重扫仍 >300s，**单列、不进分母**）
- 脚本级：`error=None` **2,396** / `no_figure` **1,940** / `syntax` **90** / `timeout` **1**。
- 主表（**两个层级**：`有站点` / `可达` 是**脚本级**，`真原位` 是**组合级**；**通过率 = 真原位 ÷ 可原位到达**）：

  | 可调项 | 有站点(脚本) | 可达(脚本) | 真原位(组合) | 通过率 |
  |---|---:|---:|---:|---:|
  | `figsize` | 2,256 | 2,256 | 2,247 | **99.6%** |
  | `pos` | 131 | 94 | 93 | 98.9% |
  | 标题字号 | 193 | 173 | 165 | 95.4% |
  | 轴标签字号 x | 181 | 173 | 160 | 92.5% |
  | 轴标签字号 y | 180 | 171 | 166 | 97.1% |
  | 刻度字号 x | 189 | 91 | 85 | 93.4% |
  | 刻度字号 y | 6 | 5 | 4 | 样本不足(n=5) |
  | `grid` | 131 | 124 | 118 | 95.2% |
  | `xlim` | 401 | 247 | 232 | 93.9% |
  | `ylim` | 433 | 276 | 261 | 94.6% |
  | **合计** | **4,101** | **3,610** | **3,531** | **97.8%** |

- 未到 100% 的构成（逐族）：**改为块落地 3** · **证不出而拒绝 7** · **参数非法被拒 37**。
- 块路径：分母 **427 块**；判据4 **A=0 / B=0 / C=427**，全库运行期下标命中 **0**。
- `verify_fail(mask)`（"没比却说通过"）：历史 138 → 131 → **原位路径 0 / 3,531** ✓；唯一残留 1 例经裁定属**判据不适用**（`mask` 判据假定只有原位改写，该行是 `auto_fallback` 块行、块文本是新增的），块行按 S2 判据全过。
- L0 六项（`corpus_l0` 5,330 份）：m1 语法可解析 **5,234 / 5,328**（94 例为语料自身语法错）；m2 站点抽取 **2,315 / 2,315**；m3 干净往返 **2,306 / 2,315**；m4 定向改写 **2,235 / 2,246**；m5 幂等 **2,245 / 2,315**；m6 revert 回原件 **2,246 / 2,246**。

### 数字

语料 **10,158 份**（Zenodo 6,904 / GitHub 科研绘图仓库 2,045 / 官方 gallery 1,209）。

- **真原位 3,531 / 3,610 = 97.8%**（口径：`style=inplace` 且 `applied=true` 且字节变了；通过率 = 真原位 ÷ 可原位到达）
- **承诺违约 0**（工具声称原位、字节却没变；语料级判定，本次静态判定范围内为 0）
- **覆盖 4,427 / 4,427**
- **块 427 / 427**（块内不用运行期下标）

## [0.1.9] — 2026-10-12（详细修复台账；用户可感知变化见上方 `## 0.1.9`）

> **本节是发布前草稿**：四条修复线（引擎 T7/T11/T12/T15、CLI-MCP-执行边界 T8/T13、GUI T9）已完成
> 并合入集成副本 `mpltweak-fix018`（分支 `fix019`，25 文件 +4445/−537，零冲突）。
> **版本号 0.1.9、日期 2026-10-12**（`pyproject.toml` + `src/mpltweak/__init__.py` 同步），
> 发布前确认 CI 结论为绿。
>
> 依据：三条审计线（写回引擎 E1–E15 / CLI-MCP CH-1–CH-12 / GUI F1–F5）+ 推广实测（P0/P1）
> + L0 语料压测（合并后复测 812 个真实脚本，`corpus018/harness/l0_report_merged.md`；
> 0.1.8 基线在 `l0_report.md`，那次 751 个脚本）。
>
> **这一版的主题是"该收手的时候要收手"**：0.1.8 在语料上「干净往返 / 幂等 / 可逆」三项**全灭
> （各 0/27）**，根因是同一条 —— 参数与源码完全一致时它仍然插块改源码（E1；第二波的 L0-1..L0-4
> 与第三波 T15 的逐字段三态判据把这条收尾）；同时把「执行边界」讲清楚并收窄。

### 这一版用户能感知到的变化（可直接用进 release notes）

三条都实测过、都能独立复现（原始记录在 `audit_018/promo/`）：

1. **`os._exit()` 的脚本不再带走 MCP server。**
   0.1.8：脚本里 `os._exit(3)` → 整个 MCP server 一起死掉，客户端后续调用全部无响应
   （实测：后续写 stdin 直接 `Errno 22`、server 进程以 3 退出）；**0.1.9**：同一个脚本 → 返回结构化的
   `{"ok": false, "error_code": "collect_failed", "error": "collect failed: …"}`，
   **server 继续可用**（`t29_mcp3.txt`）。
   CLI 侧同理：0.1.8 `describe <该脚本> --all-figs` = `rc=3` + **stdout 0 字节**（静默空输出），
   0.1.9 = `rc=2` + 明确的 `[describe] 采集失败: …`（`t29_mcp_checks.txt`）。
2. **MCP 跨调用不再串味。**
   在同一个长驻 MCP 会话里，先采集一个把 `plt.rcParams["font.size"] = 30` 的脚本，再采集一个普通脚本：
   0.1.8 采到 `title_fontsize = 36.0`（= 12×30/10，上一个调用的 `rcParams` 泄漏进来了），
   **0.1.9** 采到 `title_fontsize = 12.0`，与该脚本用 CLI 干净采集的结果**逐字相同**（连 `pos` 一致）
   （`t29_mcp2.txt`）。
3. **MCP `apply_layout` 的 `wrote` 如实报。**
   0.1.9 实测：`write=false` → `wrote=false`、脚本 `sha256` 不变；`write=true` → `wrote=true`、
   `sha256` 变（退块写入 `[0.2000, 0.2000, 0.6000, 0.6000]`）；`requested_write` 单列一个字段表示
   "你请求写了吗"（`t29_mcp3.txt`）。这条修的是 CH-7 报的"`wrote: true` 与实际不符"。

### 修复 · 写回引擎

- **E1（高，主干缺陷）** `--style auto` 在"参数与源码完全一致"时也往用户脚本里插调整块 →
  现在"没有真改动"一律不动（`no_change`），只在**确实有改动且原位够不到**时才退块。
  *为什么*：L0 语料 27/27 命中，并连带让"干净往返 / 幂等 / 可逆"三项全灭 ——
  写回的门槛不该是"参数文件存在"，而是"参数与源码真的不同"。
  修后全量复测（812 脚本）：M3 251/0、M5 27/0、M6 27/0。
  这条的最后一段尾巴（源码里没有写法的字段"等值也退块"）在**第三波 T15** 里收尾：判据改成逐字段
  三态以后，`pos` 等值 + `title_fontsize` / `spines` / `grid` / `xscale` / `figsize` 等值全部
  `no_change`、不插块、字节不变（`audit_018/promo/t16_verify.log`；反向对照见 T15 条目）。
- **E2（高）** 参数缺 `grid` 键被按默认 `false` 补齐 → 把用户脚本里的 `ax.grid(True)` 改成
  `False`（会改变出图结果）→ 现在只有参数里**显式**出现 `grid` 才写回。
  *为什么*：静默改变用户的图，比报错严重得多。
- **E3（高）** `--style block` 写盘后验证抛异常**不回滚**：脚本被留在半成品状态、命令却报失败 →
  现在任何验证异常都触发备份恢复。*为什么*："永不留下坏脚本"是核心承诺，这里曾有破口
  （物证 `scen/p4_xlim_含null/fig.py`）。
- **E4（高）** 带 colorbar 的脚本 `fraction` 伪字段永远算"真改动" → `auto` 永不退块、用户要的
  位置改动被**静默丢弃**且 `rc=0`。*为什么*：静默丢改动 = 用户以为调好了，回头图没变。
- **E5（中）** 干净往返把 `fraction=0.15` 写成 `0.0410769`（永不 `no_change`）→
  只有用户真的动过才写。
- **E6（中）** 脏参数（6 类输入）→ `internal_error`，连**只读预览**也崩；`params.validate`
  检测到却只 `print` → 统一结构化错误 + 退出码，先校验再动手。
- **E7（中）** 原位"一处都没写"仍打印 `[OK] 已原位写回` 并附假解释 → 如实报"没有可写回的改动"。
- **E8（中）** 值没变的 `grid` 永不判空改（重复 apply 谎报改动、白写备份、白跑验证）→
  按"新旧值相同"判空改。
- **E9（低）** `--dry-run` 也打印 `[OK] 已原位写回`，JSON 里没有 `dry_run` 标记 → 文案与字段如实标注。
- **E10（低）** UTF-8 BOM 被静默删除 → 写回 / 恢复按原编码原样保留。
- **E11（低）** 块模式往 CRLF 文件里插 5 行裸 LF（混行）→ 跟随文件原有换行。
- **E12（低）** 调整块标记残缺时静默插第二份块，且报 `ok/verified/semantic` 全绿 →
  识别残缺标记（修复或明确拒绝）。
- **E13（低）** `fig, ax = plt.subplots()`（最常见单轴写法）没有轴变量映射，警告却甩锅给源码 →
  警告改成说清"这是工具的映射缺口"。
- **E14（低）** `insert_or_replace` 的 docstring 与实现不符 → 改正文档串。
- **E15（低）** `layoutwarn` 不认裸名 `subplots(layout='constrained')` → 补识别。

### 修复 · CLI / API / MCP

- **CH-1（高）** `collect_all` 缺 `SystemExit` handler → CLI `describe --all-figs` 静默空输出
  （0.1.8：脚本里 `sys.exit()` 或 `os._exit()` 都是 `rc=3` + **stdout 0 字节**），MCP server 则会被
  **`os._exit()`** 的脚本带走（0.1.8 实测：`describe_layout` 无响应、server 进程以脚本的退出码 3 结束）。
  **两者要区分开**：同一实测里 `sys.exit(3)` 的脚本在 0.1.8 的 **MCP 路径下仍存活**（只有 CLI 路径被带走）
  —— 因为 `sys.exit()` 抛的是可被捕获的 `SystemExit`，`os._exit()` 直接结束进程。
  → 捕获后返回结构化失败（与 `collect` 共用 helper）。0.1.9 实测：两种脚本都返回结构化结果
  （`n_figs=1`）、server 存活（`audit_018/promo/t29_mcp2.txt`、`t29_mcp3.txt`）。
- **CH-2（高）** MCP 长驻进程的跨调用状态污染：`sys.path` 泄漏能让**同一个脚本的判定翻转**、
  `sys.modules` 缓存不失效、matplotlib `rcParams` 串味后**可能被写进用户源码** →
  **check / describe / MCP 改为子进程执行**（复用 `verify.py` 的 main driver，扩 `--all-figs`）；
  库嵌入场景再补 `sys.path` 回收 + `rcParams` 快照/恢复。
  *为什么*：一次进程级隔离，比在长驻进程里逐项打补丁可靠。
- **CH-3（中）** `check --json --tol inf` 输出**非法 JSON**（`"tol": Infinity`）并静默关掉几何检查 →
  拒绝非法值。
- **CH-4（中）** `bbox_inches='tight'` 的贴边图被误报 `text_out_of_canvas`（error 级 → 恒
  `rc=1`）→ 识别 `savefig(bbox_inches=...)` 后降级为 warning（`--json` 里 `tight_bbox: true`、
  该条 `downgraded: "tight_bbox"`），另加 `--no-tight` 按旧口径严判。
  *为什么*：README 推荐的 CI 配方在存量正常项目上会永远红，用户最后只能把这条检查关掉。
  实测：同一段越界文字，无 tight → `rc=1` / `error`，有 tight → `rc=0` / `warn`
  （`audit_018/promo/t14_verify.log`）。
- **CH-5（中）** `schema` 校验不了本工具自己的产物：`xscale` enum 缺 `symlog/logit` 等取值；
  顶层放开 `additionalProperties` 时，`--all-figs` 的嵌套形态
  （`{version, script, n_figs, figures:[…]}`）不在规范里 → **嵌套静默假通过** → 补齐 `SCALE_ENUM`、
  顶层收紧为 `additionalProperties=false`，并把 `figures` 直接列进顶层 `properties`
  （**实现里没有 `oneOf`**：只有"顶层 `additionalProperties=false` + 显式列出 `figures`"这一条机制），
  于是单图与 `--all-figs` 两种形态都能校验、结构不对的文件会被抓。实测：`describe`（单图）与
  `describe --all-figs`（嵌套）的产物都通过 jsonschema 校验；把嵌套 `figures[0].axes[0].xscale`
  改成 `bogus_scale` → 报 `'bogus_scale' is not one of ['linear', 'log', 'symlog', 'logit',
  'asinh', 'function', 'functionlog', None]`；塞一个顶层未知键 → 报
  `Additional properties are not allowed ('hello' was unexpected)`
  （`audit_018/promo/t27_schema.log`、`t27_validate.txt`）。
- **CH-6（中）** 退出码矩阵不一致（`describe`=1 / `check`,`apply`=2 / internal=1 / 未知=3）→
  `describe` 已并入 `rc=2`（脚本不存在 / 采集失败），并把**实测矩阵**写进 README（中英各一节）。
  **残差（未统一，待定契约，属 B 堆）**：同一个"脚本找不到"，**顶层路径**（`mpltweak <脚本>`，含
  `launch`，消息都是 `[launch] 找不到脚本: …`）返回 **`rc=3`**，而子命令 `describe`/`check`/`apply`/
  `revert`（`[xxx] 找不到脚本: …`）返回 **`rc=2`**；顶层的未知 flag 也是 `rc=3`
  （`[launch] 需要脚本路径，或用 --doctor 做环境自检`）而不是 argparse 惯用的 2 —— 本轮**只改文档、
  不改代码**（统一会动到顶层入口的兼容性，需先定契约）。实测矩阵：
  `audit_018/promo/t28_exit_matrix.txt`。
- **CH-7（中）** MCP `apply_layout` 的 `wrote: true` 与实际不符 → 如实报。
- **CH-8（低-中）** MCP 错误语义两套（业务错误 `isError=false`；异常错误 `isError=true` 却
  **丢失原因**）→ **统一为 payload 内的 `error_code` / `error`**：实测两类都是 `isError=false`，
  业务错误 `apply_layout(<不存在的脚本>)` → `{"ok": false, "error_code": "file_not_found",
  "error": "script not found: …"}`；异常错误 `describe_layout(<抛异常的脚本>)` →
  `{"ok": false, "error_code": "collect_failed", "error": "collect failed: RuntimeError: …"}`
  —— 机器可判（`error_code` 稳定）+ 人可读（`error` 带原始原因）都在 payload 里
  （`audit_018/promo/t29_mcp2.txt`）。
- **CH-9（低）** `schema` 忽略所有参数（`--help` / 未知 flag 都原样输出 JSON 且 `rc=0`）→
  正常参数解析。
- **CH-10（低）** MCP `serverInfo.version` 是空串 → 填真实版本。
- **CH-11（低）** sdist 混入 `src/mpltweak.egg-info/*` → `MANIFEST.in` 里
  `prune src/mpltweak.egg-info`，把 **egg-info 相关 8 项压到 3 项**（A/B 构建实测：0.1.8 基线 43 条目
  → 合并树 39 条目）。**仍会进 sdist 的三项**：`setup.cfg`、`src/mpltweak.egg-info/`、
  `src/mpltweak.egg-info/SOURCES.txt` —— 这三项由 setuptools 在构建期自己生成，MANIFEST 的 `prune`
  排不掉（`MANIFEST.in` 自己的注释也承认这一点）。证据：`audit_018/promo/t27_sdist_ab.txt`、
  `t27_mcp_sdist.txt`。
- **CH-12（低）** `check` 的人读文案曾硬编码中文 → 0.1.9 起 `--lang {auto,zh,en}`
  （`apply` / `describe` / `check` 与顶层入口都有；`revert` / `schema` / `doctor` 没有这个开关）以及
  环境变量 `MPLTWEAK_LANG=en` 都能切英文（实测 `check <脚本> --lang en` →
  `checked 1 axes, tolerance 0.010`，`MPLTWEAK_LANG=en` 同效）。
  **边界要说清**：`--json` 的**结构**一直可解析，但**默认语言下 `msg` / `warnings` 字段仍是中文**
  —— 要全英文得 `--json --lang en`（实测该组合 stdout 无中文，单独 `--json` 则含中文）；
  MCP 工具返回的就是同一份 JSON 文本，同理。
  证据：`audit_018/promo/t29_lang.txt`、`t28_cand.txt`。
- **A4（新增能力）** `check --static`：只做 AST 扫描 + 布局引擎冲突检测、**完全不执行**脚本 ——
  给"脚本跑起来有副作用"和 CI 场景一条静态通道。

### 修复 · GUI

- **F1（中）** 裸点击压幽灵撤销步：第一次 `Ctrl+Z` 谎报"撤销：移动 axN"但画面没动 →
  只在**真改动**时入栈。
- **F2（低）** colorbar 端点悬停仍提示"Alt+拖端=改 clim"（该功能已删）→ 删掉说谎提示与死代码。
- **F3（中）** `export()` 只采 `xlabel` 字号 → 悬停 y 轴标签调字号被静默丢弃（apply 后写成
  xlabel 的字号）→ 按悬停对象取。
- **F4（中）** 续调 `_apply_saved` 只恢复"中间标题"（`loc='left'/'right'` 标题字号丢），
  且字号 / label / tick / spines / lines / legend 的恢复全不计入 `n` → 全部恢复并计数。
- **F5（中）** 干净环境（无 CJK 字体）下画布内中文提示变**豆腐块**、`Glyph missing` 刷屏 →
  per-artist `fontproperties` + 英文回退，**不得改全局 `rcParams`**。

### 新增契约（L0 语料发现）

- **RC-3** `params.normalize` 注入默认 `grid=False`（与 E2 同根）：每次 apply 都打印
  "axN 未原位应用：grid"，并把 `no_change` 顶成 `best_effort`。
- **RC-5** `apply --json` 的 `files[]` 没有 `auto_fallback` 字段（只能靠 `style=="block"` 反推），
  且回退后 inplace 那次的 `reason` / `warnings` 被整份覆盖 → agent 看不出"原位为什么没落地"。
- **RC-6** 参数索引陷阱：`params.axes[i]` 必须按 `fig.axes` 顺序编号（**含** `add_subplot` 的轴、
  **剔除** colorbar 槽轴 `cax`）→ 已写进 README（中/英）与 schema 说明。
- **RC-7**（按复测更正）"revert 字节级可逆"的限定是**单次**写回可逆：0.1.8 在 26/27 个可测脚本上
  本来是好的，唯一真失败是 **L0-4**（一次 apply 内先原位、再退块 → 固定备份名被第二次写盘覆盖 →
  revert 拿不回原件）；此前"Cascade 全灭"的判断是脚手架口径缺陷（旧闸门用旧备份路径恒假），
  不是 revert 缺陷。L0-4 已修（见下）。
- **RC-8** **只改画布尺寸不可表达（0.1.8 的行为，已修）**：参数里没有 `axes` 就 `NO_AXES` 早退
  （0.1.8 实测 `{"figsize_in":[8,6]}` → `rc=1 / reason=no_axes` / 脚本未改）→ 改成
  `if not axes and not figsize_in:` 才早退，并让 `figsize` 走原位改写。修后实测：`rc=0`、
  `style=inplace`、`changes=["figsize"]`，`plt.subplots(figsize=(6, 4))` → `(10, 6)`
  （`audit_018/promo/t14_verify.log`）。
  *为什么*：论文型绘图脚本里 **89.5%** 只有"画布尺寸"这一个可静态构造的目标。
- **RC-9** `changes=[]` 但脚本确实被改（`rc=0 / reason=ok / style=block / changes=[]`，sha 已变）→
  补块摘要字段，agent 才能看出"写了什么"。

### 修复 · 第二波（L0 复测暴露）

- **L0-1 等值 figsize 被整段重写**：`figsize=[6, 3]` → `(6, 3)`、`(4., 4.)` → `(4, 4)`、
  `(16,10)` → `(16, 10)`（值不变、字节变），是 M3 残余的主因（RC-8 打开这条路径后暴露）→
  命中 `figsize=` 后**先比原值**，相等就不编辑、不写盘、不进 `changes`；真改时才生成新文本，
  并保留原容器形态（原来写列表就还是列表）。
  *为什么*：写回的语义是"值变了才写"，格式化差异不该算改动。顺带修掉一个幂等陷阱：等值时原位路径
  会发假警告、块路径会多补一行 `set_size_inches`，第二次 `apply` 因此块内容不等而破功。
- **L0-1b `subplot_mosaic` 漏算建图调用 → 改到另一张图**（**数据损坏**级）：`FIG_FACTORIES`
  只有 `('figure','subplots')`，"第 N 张图"的节点表因此错位 —— 实测
  `gallery/images_contours_and_fields/image_demo.py` 把第 3 张图的 `figsize=(10, 3)` 改成了
  第 2 张图的 `(7, 3.5)`。→ 把 `subplot_mosaic` 计入建图调用，并让 `edit_figsize` / `_fig_span` /
  `_figure_axes_for_index` / `count_figures` / `axis_var_index` 全部对齐。复测：`fig_index=1`
  只动 mosaic 那张，另一张 `(10, 3)` 原样不动。
  *为什么*：改错图是这类工具最不可接受的失败模式（比报错严重得多）。
- **L0-2 目标值等于源码现值、但"无法原位核对"（区间外 / cax）时仍退块插块** → 本该 `no_change`
  的 apply 改写了源码。修法：在 `apply_inplace` 末尾把"**能静态证明与源码现值相等**"的字段剔除
  （新增 `_proved_equal` / `_pos_literal_equal` / `_seq4_nums`）；证明是**单向**的，证不出就保守
  退块。修后这 3 条均为 `reason=no_change / applied=false / block_lines=0`，而"位置确实不同"的
  对照仍正常退块落地（不静默丢改动）。
- **L0-3 等值写回谎报 `changes=["figsize"]` + `reason=ok`** → 与 L0-1 同根：等值不再进 `changes`。
- **L0-4 一次 apply 内先原位、再退块时备份被第二次写盘覆盖** → `revert` 拿不回原件（0.1.8 物证：
  `zenodo/20154193__RealSpace.py` 写回 20567 B → revert 后 20568 B，差的就是那句等值重写的
  `figsize=(16,10)` → `(16, 10)`）。修法：`writeback(..., backup=<path>)` 支持**复用调用方的备份**，
  auto 回退时把第一次的备份传进去。
  *为什么*：备份是唯一的恢复点，同一个 apply 内任何路径都不该覆盖它。
- **`label_fontsize_y`（新字段，GUI 导出 → 写回贯通）**：悬停 y 轴标签调字号以前会被静默丢弃
  （写回时被写成 xlabel 的字号）→ `params.py` + `writeback._label_fontsizes()` 支持 y 单独设，
  y 未设置时沿用 x（老参数文件行为不变），`changes` 只报真正变了的那个，并补进 `fields_by_axis`
  使"只改 y"也被语义验证覆盖；`mpltweak schema` 同步。**未改 `verify.py`**（跨文件零侵入）。
- **E15 裸名 `subplots(layout='constrained')` + i18n 接线**：`layoutwarn` 新增裸名白名单
  （`subplots` / `figure` / `Figure` / `subplot_mosaic`），`layout='tight'` / `set_layout_engine('tight')`
  归 autolayout（5 条负例钉住"裸名 helper 不误报"）；`apply.py` 三条文案走 `_t_new(...)`
  （键在 `MESSAGES` 里就翻译，否则用模板，**绝不打印裸 key**），T8 合入后自动切换。
- **Tk 后端告知（P0-1 的代码落地）**：`_detect_backend()` 按 QtAgg → Qt5Agg → Qt6Agg → TkAgg
  **真建一次 canvas** 探测；回退 Tk 时往 **stderr** 打一句人话提示（中英走 `messages` 表，
  `MPLTWEAK_NO_HINT=1` 静默、`--dry-run` 不提示），`doctor` 多报一行
  `开窗后端 : Tk（未装 Qt；想要 Qt 界面：pip install "mpltweak[qt]"）`。实测
  `_emit_backend_hint("TkAgg") → True`、`"QtAgg" → False`、`dry_run=True → False`、
  `MPLTWEAK_NO_HINT=1 → False`。
- **执行边界子进程化（P0-5 的代码落地）**：`check` / `describe` / MCP 每次调用都起**独立子进程**
  （复用 `verify.py` 的 driver，扩 `--envelope` / `--all-figs` / `--text`，状态 JSON 写系统临时目录，
  **不在用户脚本目录留文件**），用户脚本的 `print` 照旧出现在终端；`--static` 是唯一不执行脚本的
  通道。实测：`check --static` 不写探针标记文件、`check --no-text` 会写；`collect_isolated` 前后
  父进程 `font.size` 10.0 → 10.0、`sys.path` / `sys.modules` 无残留；MCP 跑完会 `sys.exit(3)` 的
  脚本后仍能应答 `tools/list`（0.1.8 上 server 会被脚本带走）。

### 修复 · 第三波（T15：退块判据改逐字段三态）

- **该退块时不退块 → 字段被静默丢掉**：`figsize` 能原位改、`pos` 写不了时，旧判据是"原位一个字段
  都没落地才退块"，于是"有落地"就判 `needs_block=False` → `pos` **静默丢**、命令还 `rc=0` 报成功。
  修法：判据与"有没有落地"彻底解耦，改成**逐字段三态**（①已原位落地 ②与源码现值相等 ③有目标值、
  与现值不同、原位写不了），只要存在第 ③ 类就 `needs_block=True` 必须退块；显式 `--style inplace`
  遇到第 ③ 类**明确失败**（`rc=1` + 打印"这些字段代码里没有可改的写法、本次**没能落地**：ax0.pos"
  + `pending=[...]`），绝不 `rc=0` + `applied=true` 假装都改好了。
  *为什么*：静默丢改动 = 用户以为调好了、回头图没变；"报成功"比"报错"更糟。
  实测 B 情形（figsize 可原位 + pos 写不了）：`rc=0 / style=block / applied=true / auto_fallback=true
  / inplace_changes=["figsize"] / inplace_pending=["ax0.pos"]`，块落地后回读 `pos` 与目标一致；
  同一份参数加 `--style inplace`：`rc=1 / applied=false / needs_block=true / pending=["ax0.pos"]`
  （`audit_018/promo/t16_verify.log`）。
- **不该退块时退块 → 等值也插块（E1 的最后一段尾巴）**：源码里没有任何能决定该字段的写法时
  （`title_fontsize` / `label_fontsize`（含 `_y`）/ `tick_fontsize` / `spines` / `grid` /
  `xscale`、`yscale`），参数值等于实际值也曾经一律退块插 12 行 → 新增**默认值等价证明**
  （`_use_matplotlib_default` / `_default_pt`：源码里没有对应写法 ⇒ 实际渲染值就是 matplotlib
  默认值；相对名按 matplotlib 自己的换算，如 `'large'` → 1.2×`font.size`=12.0）。**反向保护**：
  源码里出现 `fontsize` / `labelsize` / `titlesize` / `rcParams` / `rc(` / `set_style` / `set_theme`
  / `style.use`（`spines` 另看 `spines` / `set_visible` / `axis(` / `frame_on`，`grid` 看 `grid`，
  `scale` 看 `scale`）就**放弃证明**、退回保守退块。
  *为什么*：写回的门槛是"值与现值真的不同"，不是"源码里有没有写出这个字段"。
  实测六种等值情形全部 `no_change` + 字节不变 + 不插块；反向对照 `title_fontsize=20.0` /
  `tick_fontsize=7.0` / `spines={'top': False}` 仍退块并把值落到图上（`audit_018/promo/t16_verify.log`）。
- **`apply --json` 新增三个字段**：`pending`（这次还没落地的字段）、`inplace_pending` /
  `inplace_needs_block`（原位那一趟为什么退块）；`needs_block` 的语义收紧为"**这次 apply 之后**是否
  仍有字段没落地"。*为什么*：agent 需要能判读"原位为什么没成"，而不是只靠 `style=block` 反推。
- **`--verify-fast` 的诚实边界**：它不重跑脚本，因此证明不了"某字段与源码现值相等"；源码里没有可比
  写法时（典型 `plt.subplots()` 的网格轴位置由 subplotspec + 边距算出）只能**保守退块**。
  实测：12 个真实语料脚本按 agent 主路径（`describe` 原样导出 → `apply --write --verify-fast`）
  抽样，能跑起来的 **7/7 仍插块**（`audit_018/engine-hunter/fix018/t15_agent_path.txt`）；同一路径
  不加 `--verify-fast` 即 `no_change`（本地 A/B：同一个 12 行 `plt.subplots(2, 2)` 脚本，加它 →
  `style=block` 且块 31 行，不加 → `reason=no_change / applied=false`，
  `audit_018/promo/t16_probe2.log`）。README（中/英）「给 AI / agent 的排版接口」已写明：
  **agent 路径建议不要加 `--verify-fast`**。
- **`set_position` 证明缺口（M3 最后一条 fail）**：`cax` 在函数里用 `fig.colorbar(..., cax=cbar_ax)`
  创建时，静态建图区间圈不到它（0.1.8 同样失败）→ `_pos_literal_equal` 新增 (d) 路径：从
  `fig.colorbar(..., cax=X)` 反查 cax 名字再比字面量（触发条件：参数标了 `is_colorbar`，或本图区间
  内没有可比字面量）。修后 M3 的 fail 清单清空。

### 修复 · 第四波（T23：多图 stale + 验证链诚实 + 块锚点硬约束 + 刻度字号分列 + 新开关 --keep-unverified）

- **脚本本来就跑不通时不再留下没验证的写回**：写盘后**无条件** `ast.parse`（原位与块两条路径都做），
  语法被写坏**当场回滚**；重跑失败时先判别"我们写坏了"还是"脚本本来就跑不通"（缺数据 / 依赖 /
  需要 GUI）——**后者默认回滚 + 明确失败**，错误信息写明"默认**不保留**，要保留请显式
  `--keep-unverified`"；新增开关 `--keep-unverified` 才保留（`verified=null` + warning）。
  *为什么*：那时验证什么都证明不了，留下坏写回比失败更糟。
- **验证链不再"宣传比实际硬"**：取不到图状态时 `verified` 由 `true` 改为 `null`（`semantic=null`），
  人读明说"只确认了脚本能跑通、**没有**验证布局是否落到目标图上"；`--json` 新增
  **`layers = {syntax, rerun, semantic}`**（`true`=这一层跑了 / `null` 或缺键=没跑）、**`anchor`**、
  **`attempts`**、**`err`**；`--verify-fast` 的人读输出明确写"本次只做了**语法检查**：没有重跑脚本、
  没有比对布局是否真的等于参数"。
- **块锚点的硬约束**：有 `savefig` / `show` 时块**只允许插在最后一个 `savefig` / `show` 之前**，
  行内复合语句（`if flag: fig.savefig("o.png")`）把插入点**提升到该语句之前**；找不到合规锚点就
  **拒绝写回**（`reason=fail` + 脚本不动），不再"报已验证、你的 PNG 却没变"。
  实测：`if flag: fig.savefig("o.png")` 在第 76 行时块落 9–75 行、`anchor="before"`。
- **刻度字号 x/y 分列**：`tick_fontsize`（x）与 `tick_fontsize_y`（y）分开采集与写回，与
  `label_fontsize_y` 同款；源码里已有 `axis='x'/'y'` 时各改各的，只有一个 `labelsize=` 时两字段一起
  退块（块里分两行写）。实测：`tick_fontsize_y` 11 → 14 时源码 `axis="y"` 那行原位改成 `labelsize=14`。
  证据：`audit_018/promo/t32_probe.log`。

- **多图逐张写回不再把后续参数误判为过期**：一次 `apply --write` 会把该脚本的多份参数文件
  （主文件 + `<stem>.fig<k>.json`）逐张落实，而每落地一张就要改写一次脚本 —— 旧判据拿"**此刻的**脚本
  mtime"去比下一份参数文件，于是只要第一张的写回超过 1.0s 的容差，第 2 张就必然被判 `stale` 跳过，
  理由还是"脚本在参数保存之后被修改过"（与事实相反）。现在基准取**本批次开始时刻**（只反映用户自己的
  手改），并在落地成功后刷新该批参数的 mtime；真正过期的参数**仍然会被拦下**（`reason=stale`）。
  实测（`audit_018/promo/t33_stale.log`）：单图写回 **16.3s**（>1.0s 的"时间炸弹"前提成立）→ 一次
  `apply` 两份参数**都** `reason=ok` / `applied=true` / `layers` 三层全绿；把第 2 份参数打桩成比脚本
  旧 10s → 仍然 `reason=stale` / `applied=false`（拦得住）。

### 修复 · 第五波（T35 / T36）

- **默认不再给每个轴注入刻度设置（以前会把源码明写的对数轴改成线性轴）**：工具过去给每个轴注入默认
  `xscale` / `yscale = 'linear'` —— 当源码里明写 `ax.set_xscale("log")` 时，这个"没人要求过的目标值"
  被当成真改动 → 退块 → 块里塞 `set_xscale('linear')` ⇒ **存出来的 PNG / PDF 里那条轴真的从 log 变成
  linear**（真实语料里有 31 处 `plt.yscale('log')` 的脚本会中招）。现在 `AXIS_DEFAULTS` 的 scale 改成
  `None`（＝**未设置**，与既有的 `grid` 同一先例），下游一律用 `is not None` 判"有目标值"；**块渲染器
  也只写确实需要落地的字段**（等值字段不再插块）。⇒ **不再改动你没有指定的刻度设置**，源码里明写的
  `log` 不会被默认值覆盖。
  实测（`audit_018/promo/t40_probe.log`）：源码 `ax.set_xscale("log")` + 参数**只给 `pos`**（与现值一致）
  → `reason=no_change` / `style=inplace` / `applied=false` / `block_lines=0` / `changes=[]`、`ok=true`、
  **`sha256` 不变**、源码里没有调整块。
  （一条边界：参数文件里**显式**写了 `xscale: "linear"` 的 —— 例如轴改成 log **之前**存下的老参数文件
  —— 工具会照你那句话执行；重新 `describe` 一份即可。）
- **多图逐张写回后 `revert` 能回到真正的原件**：一次 `apply --write` 处理多份参数（多图逐张写回）时
  会连着改同一个脚本好几遍，而备份是**固定名** → 第二遍就把备份覆盖成"已经改过一次"的中间态，于是
  `revert` 只回到「原件 + 第一段调整块」，**不是**跑 `apply` 之前那一份。现在**批次内首份备份沿循环
  透传**（与"同一次 apply 内 inplace→block 复用备份"同一机制），备份 = 真正的批前原件。
  实测（`audit_018/promo/t40_probe.log`，带两份参数的 2 图脚本）：批前
  `sha256=bb1edee7…` → 一次 `apply` 逐张写回两份（都 `applied=true`）后 `sha256=498dbd45…`（已变）→
  `revert --write` 后 `sha256=bb1edee7…` **== 批前原件 ✓**；备份文件本身也等于批前原件。

### 文档 · 推广

- **P0-1** 窗口后端告知（**方案 B，用户已拍板**）：PyQt5 在 → Qt；不在 → Tk 开窗，并在
  **stderr** 打一句"想要 Qt 界面请 `pip install "mpltweak[qt]"`"；文案进 `messages.py` 中英表
  并过 GBK 棘轮；`doctor` 报当前后端；配测试。
- **P0-2 / P0-3 / P0-4** README（中/英）与实现对齐三处：插入块标记串（实际写入的是
  `# ===== auto layout (generated) - do not edit inside =====`，旧中文串仍可识别）、
  CI 配方（`check` 一次只收一个脚本，给出可用的循环写法）、`launch` 用法。
  本轮按合并后的代码复核并更新：**`launch` 已实现为显式子命令**（`mpltweak launch fig1.py` ≡
  `mpltweak fig1.py`，实测 `--dry-run` `rc=0`）；0.1.8 上它不存在（会被当成"打开名为 `launch`
  的脚本"，`rc=3`）——README 两种写法的行为都写清楚了。块标记串 / `--static` / `--tol` /
  子进程隔离也都在合并代码上逐条复跑（`audit_018/promo/t14_verify.log`、`t14_verify2.log`）。
- **P0-5** README（中/英）新增「执行边界」一节：哪些命令会执行你的脚本、副作用清单
  （文件 / 网络 / 缓存 / cwd / 导入路径 / `rcParams`）、同一脚本会被重复执行、**不是沙箱**、
  以及 `check --static` 这条不执行的通道。
- **P0-6** 「零侵入」表述校准：`describe → apply` 直通写回会把**当时整张图的状态**钉进源码
  （实测 8 行脚本变 30+ 行），并给出"只传 index+pos"的最小用法；0.1.8 缺 `grid` 键的坑已修
  （不显式给就不写）。第三波 T15 之后这条边界改了：只含代码里**确有写法**的等值字段 → `no_change`
  且字节不变；参数里带了源码里没有写法的字号 / `spines` 这类字段、而值又与原图一致 → 也 `no_change`
  （逐字段三态判据，见 T15 条目）；**值确实不同**的仍然退块落地。
- **P0-7** 发布流程补一条硬检查：**打 Release 前必须看 CI 结论**（0.1.8 是在 CI 红的提交
  `1248109` 上发布的，而徽章因为跟 `main` HEAD 显示 passing）。
- **P1**（不在本版范围内，发布时注意）：PyPI 项目页仍 pin 着旧演示 GIF（`a4e55c0`，下次发版时
  换成当前 HEAD 的图片）；wheel 里 77.3% 的体积是两张内置帮助 PNG（可瘦身）。
- README（中/英）新增：实测适配面（覆盖率按受众分开报）、参数索引约定与 `figsize`-only 用法、
  窗口后端一节。
- **文档事实源纠错（外部评审条目 9）**：`skills/mpltweak/SKILL.md`（专门喂给 AI 的操作手册）
  的参数格式版本号写成 `version 3`（实际 `SCHEMA_VERSION = 4`）、并称“开窗需要 Qt，已含在默认依赖”
  （`pyproject.toml` 里 Qt 是可选 extra `[qt]`）→ 本轮按代码纠错，并把 0.1.9 的运行时告知
  （Qt 优先 / 无 PyQt5 → Tk 开窗 + stderr 提示 + `MPLTWEAK_NO_HINT`）、`--style inplace`
  写不了会 `rc=1`、`check --static` 不执行脚本、`--verify-fast` 的诚实边界一并写进手册。
  中英 README 安装段补齐：英文版原先缺 `python -m mpltweak` 兜底（`mpltweak` 不在 PATH 时用）。
  另按 T19 复核者的观察补一条契约：`describe --all-figs` 的嵌套 `{"figures":[...]}`
  **不能**直接喂回 `apply --params`（`rc=1 / reason=no_axes`，明确失败、不静默），中英各一节。

### 测试与验收

- 七套件 `ALL PASS` + `pytest -q` 全绿，并额外跑一遍 **`MPLTWEAK_LANG=en pytest -q`**
  （0.1.8 的 CI 红正是"本地 zh 绿、CI en 红"）。
- **每条修复配一条反向验证**：把修复改回旧写法，对应用例必须失败。
- L0 语料在修复版上全量重跑（812 个真实脚本，`l0_report_merged.md`）：**M3 干净往返 251 pass /
  0 fail、M5 幂等 27/0、M6 可逆 27/0**（0.1.8 基线：0/27、0/27、26/1）；M4 定向改写 23/4 = 85.2%，
  4 条如实标注为脚手架判据口径下的"结构上够不到"；M1 可解析 809/3（3 条是语料侧的 Colab `!pip`
  非 Python 文件）。覆盖率按受众分列（gallery vs 论文配套代码）并写进 README。
- 合并副本实测：`pytest -q` 25 passed（引擎）/ 28 passed（GUI，zh 与 en 各一遍）；七套件 ALL PASS；
  `test_writeback` 201 PASS（第二波新增 §50/§51/§52 共 28 条）、`test_check` 51 OK（含 §7d 14 条
  裸名负例）。
- 反向验证（把修复改回旧写法 → 对应用例必须失败）：引擎线用 0.1.8 原版引擎跑新用例 **27 FAILED**、
  修复后 **0 FAILED**；第二波 R-D / R-E / R-F / R-G 四组全部命中（R-F 复现的正是"改错图"）；
  T8 的 R5 命中 `test_agent_api` 失败项。
- 第三波（T15）自测：`tests/test_writeback.py` **225 PASS / 0 FAIL**（新增 §53a–§53j 共 17 条，
  含"等值不插块"的六种字段与 `--style inplace` 明确失败）；反向验证 R-A / R-B / R-C 全 PASS
  （把判据改回旧写法，对应用例必须失败）。本轮文档改动在合并后的代码上逐条复跑，
  证据见 `audit_018/promo/t16_verify.log`、`t16_probe2.log`。
- 宽兜底棘轮（`tests/test_hygiene.py`）只许降不许升；新模块必须登记。

### 发布提醒（照旧，重要）

- **推 tag 不发版**：PyPI 发布由 GitHub **Release 的 published 事件**触发，必须在 GitHub 上
  真正发布 Release；
- 同一个版本号不要发布两次；发布前用 `ls-remote` 复核远程 hash；
- 「更新 GitHub 仓库」与「发布新 PyPI 版本」是两件事。

## [0.1.8] — 2026-10-10

> 这一版把"布局调完能不能确定落地"这条链路补齐了：新增 `revert` 回退、
> `--verify-fast` 快速档、`--strict` 严格档、轴范围 `xlim/ylim`（参数 schema v4）、
> `check` 按**真实渲染尺寸**查文字，并修掉了 `--json` 输出编码与元组/带符号写法
> 原位写回等 6 个真 bug。测试：七套件 + `pytest -q` 25 项全绿（GBK 与 UTF-8 环境各跑一遍），
> 12 项反向验证全部命中；两个模型家族的独立只读审阅共 10 条发现全部修复。


### 修复
- **`--json` 成功路径的 stdout 不是 UTF-8**：`check` / `describe` / `apply --json` / `schema`
  原先用 `print()`，在管道被捕获时按**本地编码**（中国 Windows 上是 cp936/GBK）输出；JSON 里
  只要有一个中文（`check` 的 `msg`、`schema` 的 `description`），按 UTF-8 解码的 agent / MCP
  客户端 / CI 就会直接崩。现在统一走 fd 级 UTF-8 写出（与失败路径 `fail_json` 一致）。
- **顶层兜底 `guard_json_main` 在异常路径上失效**：`messages.py` 用了 `sys` 却没 import，
  写出结构化 JSON 之后就在 `sys.stderr.write` 上抛 `NameError`，把本该打印的人话提示、
  `MPLTWEAK_DEBUG` 的 traceback 和 `return 1` 全部吃掉（用户看到的是与真实故障无关的 traceback）。
- **元组/带符号写法的原位写回**：原先只认列表字面量与 `ast.Constant`，于是
  `add_axes((-0.01, ...))`（元组）和 `set_xlim(-0.01, 1.01)`（负号在 AST 里是 `UnaryOp`）
  会被**静默**丢进"未原位应用"——用户看到的是"拖了没反应"。
- **`--json` 的 `style` 字段在写回时永远是 `null`**：写回路径没设 `style`，导致 `apply` 永远走
  "已插入调整块"那条消息分支，**"原位修改 N 处"永远打不出来**，agent 也判断不了这次是哪种写回。
  （只读预览 `--json` 里 `style` 现在填**请求的模式**：`auto` 表示"逐图待定"——预览阶段还没决定
  这张图会不会退到块。）

### 新增
- **布局引擎冲突检测**（`layoutwarn.py`）：静态扫描脚本，命中
  `constrained_layout=True` / `layout='constrained'|'compressed'` / `set_constrained_layout` /
  `set_layout_engine` / `rcParams['figure.constrained_layout.use']` / `rcParams['figure.autolayout']`
  时给出**带行号**的提示与修复建议（`launch` 开窗前、`apply` 写回时、`check` 的 `layout_engine` 字段）。
  原因：布局引擎在**每次绘制**时重算轴位置，写回/拖拽的位置会被它覆盖，而旧行为只表现为一句
  "验证不一致"，指不到病因。
- **`mpltweak revert <脚本> [--write]`**：回退到上次写回之前（默认只预览）。用现成的
  `.tweak_params/<脚本>.tweak.bak`，**自身也可逆**（覆盖前把当前版本留成 `<脚本>.revert.bak`），
  恢复是字节级原样复制（BOM / 换行 / 编码不动）。
- **`check` 按真实渲染尺寸查文字**：绘制一次后用 `Text.get_window_extent()` 量外框 ——
  **超出画布 = 问题**（客观可判定），**文字互相压住 = 提示**；同轴超长刻度标签按
  "沿刻度方向的重叠比例"判"太密"。纯几何框查不出这类问题。`--no-text` 可退回纯几何口径。
- **轴范围 `xlim` / `ylim`（参数 schema v4）**：`describe` 采集（**只在脚本显式固定过时**才记，
  autoscale 不记），`apply` 原位改写 `set_xlim(a, b)` / `set_xlim((a, b))` / `set_xlim(xmin=, xmax=)`
  三种写法，`--style block` 的调整块也会带上，写回后由语义验证比对范围是否真的等于参数。
- **`check` 轴范围不统一提示**：多面板里 ≥60% 面板共享同一范围、另有少数不同 → 提示
  （各自为政的多面板图不报）。
- **`--verify-fast` 快速验证档**：只做语法静态检查（写坏当场回滚），**不重跑脚本**；
  `--json` 标 `verify_mode=fast` + `verified=null`，人读输出原话说清"不保证能跑通、也不保证布局正确"。
- **`--strict`**：`check --strict` 把布局引擎冲突升级为问题（rc=1，接 CI）；
  `apply --write --strict` 在冲突时拒绝写回（`--allow-layout-conflict` 可放行）。
- **轴网格身份 `cell`**：参数里记录每个轴调图时的网格格位 `[行数, 列数, 起始行, 起始列]`，
  写回后回比 —— 脚本改动导致轴序漂移时，语义验证会**指名道姓**地失败并回滚
  （而不是默默把位置套到别的面板上）；调整块注释里也带出这份身份。
- `tests/test_hygiene.py`（宽兜底棘轮 + 消息表 GBK 可编码 + 新模块登记）、
  `tests/test_suites.py`（pytest 包装）、`tools/audit_exceptions.py`（宽兜底清单与可窄化提示）。
- `CHANGELOG.md`（本文件）。

### 文档
- `CONTRIBUTING.md`：改正 Qt 依赖描述（`[qt]` extra，不是默认依赖）；补 pytest 路线、
  "加一套测试要改哪四处"、宽兜底棘轮与可窄化清单的用法。
- README（中/英）：新增 `revert` / `--verify-fast` / `--strict` 三节；「不适用场景」；
  「原位改数字 vs 插入调整块」（`--style auto` 的逐图判定、跨轮混用与彻底统一的正确做法）；
  参数文件示例补 `xlim` / `ylim` / `cell` 并升到 v4；`check` 一行写清新增检查项。

## [0.1.7] — 2026-10-10
### 修复
- 发布前审计的 **27 项修复**：写回引擎、并发写回、输出契约、交互细节。
### 文档
- README 演示图改用 **commit sha** 引用（避免 `@main` 缓存导致新 GIF 不生效）。
- CI 工作流、贡献者文档、issue / PR 模板、social preview 素材。

## [0.1.6] — 2026-10-09
### 修复
- 演示 GIF 播放异常慢（重新压缩时逐帧 `duration` 丢失，整段节奏被拍平）。
### 文档
- README 图片全部改为**绝对 URL**（PyPI 不渲染相对路径图片）。
- 英文项目简介。

## [0.1.5] — 2026-10-09
### 新增
- `check`（排版体检：对齐 / 等大 / 间距 / 字号 / 越界 / 重叠，`--json` 可机读）。
- `mcp` 服务端（把排版能力暴露给 AI 客户端），可选依赖 `mpltweak[mcp]`。
### 文档
- README 重构（目录 / MCP 与 skill 章节）；重写 AI 用的 skill 说明。

## [0.1.4] — 2026-10-09（**已发布到 PyPI**）
### 新增
- agent 接口第一版：`describe`（不开窗导出当前排版）/ `schema`（输出 JSON Schema）/
  `apply --json`（机器可读结果），打通"读 → 改 → 写"闭环。

## [0.1.3] — 2026-10-09
### 文档
- README 定版：更朴素的说法、带表头的对比表、说明命令在终端而不是 Python Console 里跑。

## [0.1.2] — 2026-10-09
### 新增
- **参数过期保护**：脚本在参数保存之后被手动改过时，拒绝套用旧参数（mtime 比较），
  避免静默覆盖用户的手动改动；确要续调用 `--force-resume`。

## [0.1.1] — 2026-10-09
### 变更
- PyQt5 进默认依赖（0.1.7 起改为 `[qt]` extra，开窗才需要）。

## [0.1.0] — 2026-10-09
### 新增
- 首个版本：交互式调 matplotlib 多图排版（拖拽 / 对齐 / 吸附 / 字号 / colorbar），
  参数静默落盘，AST 确定性写回 + 三层验证 + 失败自动回滚。
