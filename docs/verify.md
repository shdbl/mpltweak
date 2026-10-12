# 写回与验证

## 三层验证

`--write` 不是"盲改"，每一步都可回滚，而且**每一层跑没跑、结果算不算数，JSON 里都如实标**：

1. **语法层**（`layers.syntax`）—— 写盘后**无条件** `ast.parse`（原位与块两条路径都做）：写回只要引入
   语法错误，**当场回滚**，不会留下一份跑不起来的脚本；
2. **重跑层**（`layers.rerun`）—— 用 Agg 无头重跑。退出码非 0 时会进一步判别两件事：
   **"我们写坏了"** → 回滚；**"脚本本来就跑不通"**（缺数据 / 缺依赖 / 需要 GUI）→ **默认也回滚 +
   明确失败**（错误里写明"默认**不保留**，要保留请显式 `--keep-unverified`"）；
3. **语义层**（`layers.semantic`）—— 重跑后 dump 目标图状态，与参数里的位置 / 字号 / grid / spines /
   clim 逐项比对；"跑通了但布局没落到目标图"会被判失败。**取不到图状态时这一层是 `null`、`verified`
   也是 `null`**，不再谎报 `true`（人读会明说"只确认了脚本能跑通，**没有**验证布局是否落到目标图上"）。

`--json` 里这三层的状态就是 `layers = {syntax, rerun, semantic}`：`true` = 这一层跑了；
`null` / 缺键 = **这一层没跑**（所以 `verified` 现在可能是 `null` 或 `false`，不再是"恒为 true"）。
块路径另外给 `anchor`（块插在哪：`before` / `after` / `tail`）、`attempts`（每次尝试落在第几行、
成没成）与 `err`（失败原因）—— 出问题时能一眼看出"块插到哪里、为什么没成"。

**`--keep-unverified`（自担风险的档位）**：脚本本身在无头环境跑不通时（缺数据 / 依赖 / 需要 GUI），
**默认是不保留**这次写回的 —— 那时验证证明不了这次写回可用，留下坏写回比失败更糟。只有你确实知道
自己在干什么（例如脚本要读一个只有你机器上有的数据文件）才加它保留；此时 `verified: null` +
warning，**请自己跑一遍确认**。

**这是确定性写回的保障机制。** 备份写在**参数文件旁边**（默认就是
`.tweak_params/<脚本>.tweak.bak`，不散落到代码目录）；用 `--params other/x.json` 时备份跟着参数文件走、
落在 `other/` —— `revert` 也按同一条规则去找，所以要回退就得传同一个 `--params`。
并发跑两个 `apply --write` 时，各自还会多留一份带 pid 的副本（`.tweak.<pid>.bak`，只留最近 5 份），
避免它们互相覆盖那唯一的恢复点；同一个脚本的写回用 `<脚本>.apply.lock` 串行化，
所以两处改动不会"凭空消失"。
慢脚本超时不会被误判为失败，只提示手动确认。

> **备份只有"最近一次"**：`.tweak.bak` 每次 `apply --write` 都会被新的一份覆盖。所以反复写回几次之后，
> **最早的原文已经不在备份里了**（只剩倒数第二次的内容）。写完确认没问题就提交到版本控制，或者
> 把 `.tweak_params/` 一起留档。

写回是**原子替换**（先写临时文件再 `os.replace`），所以脚本不会出现"改到一半"的状态；
代价是**文件 inode 会变**：如果你用硬链接指向这个脚本，替换后链接仍指向旧内容。
软链接不受影响，文件权限位会被保留。

## 后悔了：`mpltweak revert`

写回错了、或者拖完发现还是原来那版好看？不用去翻备份：

```bash
mpltweak revert fig1.py            # 只预览：会改回哪几行（默认，不动代码）
mpltweak revert fig1.py --write    # 真的回退到"上次写回之前"
```

它用的就是**参数文件旁边**那份备份（默认 `.tweak_params/<脚本>.tweak.bak`，每次
`apply --write` 之前自动留的）。写回时若用了 `--params`，回退也要带上同一个 `--params`，
否则会报 `[revert] 还没有可回退的备份`（`rc=2`）。
回退的是**最近一次**写回 —— 备份每次写回都被覆盖，反复写回后再 revert 回不到最早的原文。
**revert 自身也可逆**：覆盖前会把当前版本另存为 `<脚本>.revert.bak`（也在参数文件旁边），
所以手滑了还能再回去。
恢复是**字节级原样复制**（BOM / 换行 / 编码都不动）。

## 慢脚本：`--verify-fast`（快速档）

脚本要跑几分钟、你只是想先看一眼结果，可以用快速档：

```bash
mpltweak apply fig1.py --write --verify-fast
```

**它的检查范围**：源码仍然是合法 Python（语法错误当场回滚到批前状态）。

**它不检查什么**：**没有重跑脚本** —— 不保证脚本能跑通，也不保证布局真的落到目标图上。
所以它只适合"先快速落实、马上自己跑一遍看图"的迭代过程；正式落实请去掉这个开关。
`--json` 里会如实标 `"verify_mode": "fast"`、`"verified": null`、
`layers = {syntax: true, rerun: null, semantic: null}`；**人读输出也会明说**：
`[i] 本次只做了**语法检查**：没有重跑脚本、没有比对布局是否真的等于参数（--verify-fast 的能力边界）`
—— agent 和你都一眼能看出来这份结果没经过重跑。

## 接进 CI：`--strict`

`check` 默认把布局引擎冲突（`constrained_layout` / `autolayout`）当**提示** —— 那是 matplotlib 的正常写法。
如果你的项目要求"别用会被引擎覆盖的布局方式"，加 `--strict` 让它变成**问题**（退出码 1）：

```bash
mpltweak check fig1.py --strict            # 有布局引擎冲突就 rc=1
mpltweak apply fig1.py --write --strict    # 有冲突时拒绝写回（要放行加 --allow-layout-conflict）
```

`check --json` 也是天然的 CI 用例（`ok` 字段 + 退出码）。注意 **`check` 一次只接受一个脚本** ——
`mpltweak check figures/*.py` 会被 shell 展开成多个位置参数，argparse 直接报
`unrecognized arguments` 并 `rc=2`（实测）；在 cmd.exe 这类**不展开通配符**的 shell 里，则是
`[check] 找不到脚本: …\*.py` + `rc=2`。所以批量要自己循环：

```bash
# 单个脚本：退出码就是门禁（有 jq 时也可以顺手看 ok 字段）
mpltweak check fig1.py --json | jq -e '.ok'
```

```python
# 一批脚本：存成 check_layout.py 再运行（不依赖 jq，Windows / macOS / Linux 通用）
import glob, subprocess, sys
bad = [f for f in sorted(glob.glob('figures/*.py'))
       if subprocess.run(['mpltweak', 'check', f, '--json']).returncode != 0]
print('layout check failed:', bad)
sys.exit(1 if bad else 0)
```

**贴边文字与 `bbox_inches='tight'`**：check 只比较文字外框是否落在 figure 框内，
**不读** `savefig(bbox_inches=...)`；识别到
`bbox_inches='tight'` 就**降级为提示**（`rc=0`；`--json` 里 `tight_bbox: true`、该条
`downgraded: "tight_bbox"`），要按旧口径严判加 `--no-tight`。实测
（`audit_018/promo/t14_verify.log`）：同一段越界文字，无 tight 时 `rc=1` / `error`，
有 tight 时 `rc=0` / `warn`。

**`--tol` 只收有限正数**：`inf` / `nan` / `<= 0` 会让几何检查静默失效，现在直接拒绝并说明原因
（`rc=2`：`容差必须是**有限正数**（收到 inf）：inf / nan / <= 0 会让几何检查静默失效
`）。

CI 还有一条更省事的静态门禁：`mpltweak check fig1.py --static`（**不执行脚本**，见「执行边界」）。

## 调整块的插入点硬约束与"写回被整体拒绝"

**块的插入点有硬约束**：脚本里有 `savefig` / `show` 时，调整块**只允许插在最后一个 `savefig` / `show`
之前** —— 插在它后面等于"你调的布局根本没进图"，而这正是最难发现的一类事故。行内复合语句
（如 `if flag: fig.savefig("o.png")`）会把插入点**提升到那条语句之前**；找不到合规插入点就**拒绝写回**
（`reason=fail`、脚本一个字节都不动），不会再出现"报成功、你的 PNG 却没变"。实测
（本次发行前实测）：`if flag: fig.savefig("o.png")` 在第 76 行时，块落在第 9–75 行、
`anchor="before"`、`attempts=[{"anchor": "before", "line": 9, "ok": true, "semantic": true}]`。

`--style` 有两条路。`auto`（默认）能原位改就原位，改不了就退到插入调整块；`inplace`（显式）要求原位，改不了就 `rc=1` / `applied=false`，不假装都改好；`block` 一律插块。
判读落地情况看 `apply --json` 的 `files[]`：`needs_block`、`pending`、`inplace_pending` / `inplace_needs_block`、`auto_fallback`、`inplace_changes` / `inplace_reason`。
逐字段含义与三种风格的实测对照（rc / JSON）见 [docs/params.md](docs/params.md)。

## 它到底改了什么

> 上面这两行英文标记就是**实际写进你脚本的那两行**（`# ===== auto layout (generated) - do not
> edit inside =====` … `# ===== end auto layout =====`），不是示意。早期的中文标记
> （`# ===== 自动布局调整…`），工具也**仍认得**并会整体替换，但新写的块一律用英文这两行 —— 想 grep
> 自己脚本里的块，请用 `auto layout`。

- **平时的零侵入**：不加 `import`、脚本里不留任何工具痕迹、不生成副文件；开窗只写参数。
- **写回是你主动要求的动作**（`apply --write`），它本来就要改代码 —— 问题在**改多少**：
  开窗导出的是**当时各轴的实测状态**（每个轴的 pos / 字号 / 网格 / spines / 线宽 / 图例 /
  xlim），直通写回会把这一整份**钉进源码**。实测：一个 8 行的 `plt.subplots(2, 2)` 脚本被插进
  30+ 行的块，连"当时恰好是默认值"的那些字段也一起写死（以后改脚本里的默认值就不生效了）。
- **只给"代码里确有写法"的等值参数 = 不改源码**（实测）：只传 `index` + `pos`（可选
  `figsize_in`）且值与原图一致时，`apply --write --verify-fast` 报 `reason=no_change`、
  `changes=[]`、字节不变（`audit_018/promo/t14_verify3.log`）。
- **代码里没有写法的字段，等值时也不再插块**（退块判据是**逐字段三态**，见「`--style` 与 JSON
  字段」一节）：只有"有目标值 ∧ 与源码现值不同 ∧ 原位写不了"的字段才会退块。实测 `pos` 等值 +
  `title_fontsize: 12.0`（源码里没写 `fontsize=`），以及 `label_fontsize` + `tick_fontsize` /
  `spines` / `grid` / `xscale` / `figsize` 等值 → `reason=no_change`、不插块、`sha256` 不变
  （复现：`audit_018/promo/t16_verify.log`）。反向对照（**值确实不同**）仍退块并把值落到图上：
  `title_fontsize: 20.0`、`tick_fontsize: 7.0`、`spines={'top': False}`（同日志）。
- **`describe → apply` 直通依然可能"改代码"**：只要参数里带了**确实不同**、而源码里又写不了的字段，
  它就会插入调整块（那正是"落地"的方式）。所以别把直通写回当成"无需改动"，写回后看一眼 `git diff`。
- **`--verify-fast` 下这条更保守**：它不重跑脚本，证明不了"某字段与源码现值相等"，源码里没有可比写法
  时会退块（详见「给 AI / agent 的排版接口」一节）。
- **只想改你要的字段**：参数写成最小形态
  `{"version": 4, "script": "fig1.py", "axes": [{"index": 0, "pos": [0.07, 0.54, 0.44, 0.40]}]}`。
  只有参数里**显式**给出的字段才会写回（缺 `grid` 键不再被当成 `false` 补上；实测
  `audit_018/promo/t14_verify.log` 的"最小参数"一条：`ax.grid(True)` 原样保留、未出现
  `grid(False)`）。
- 写回前先 `mpltweak apply fig1.py`（只读预览，**不会执行**脚本）或 `git diff` 看一眼。

