# -*- coding: utf-8 -*-
"""真窗口自动化测试（#4 / t16）：**真开窗口** + 事件投递（**不劫持真实鼠标**）。

================================================================================
为什么必须有这一层
================================================================================
产品卖点是"像 PPT 一样拖拽调 matplotlib"，但用户实机一用就撞到两个
**只在真实窗口环境才出现**的 bug：

  * #1 Ctrl+F 裁白边：200% 缩放下尺寸缩一半、主标题被裁；
  * #2 画布超屏拖拽错位：窗口被 Windows 钳制（resizeEvent 静默改 figsize）。

离屏测试**天生照不到**这类问题：离屏是 100% 缩放、没有窗口管理器、没有
``resizeEvent`` 钳制。所以本套件真开窗口、真投事件。

================================================================================
事件注入机制（任务书明令：**不劫持真实鼠标**）
================================================================================
  * Qt：只用 PyQt5/PySide2 自带的 QtTest —— ``QTest.mousePress`` /
    ``QTest.mouseMove`` / ``QTest.mouseRelease`` / ``QTest.keyClick``。
    它们在控件上**投递**一个 QMouseEvent/QKeyEvent（C++ 侧走 ``qApp->notify()``），
    **不移动系统光标**、不产生 SendInput。
  * Tk：只用 ``widget.event_generate('<Button-1>', x=..., y=...)`` 等 Tk 事件投递。
  * **禁止**（本文件一个字都没有，grep 可证）：``pyautogui`` / ``pynput`` /
    ``win32api`` / ``SendInput`` / ``mouse_event`` / ``keybd_event`` /
    ``QCursor.setPos`` / 任何 ctypes 系统输入调用。

================================================================================
打扰控制（"窗口短暂出现"是唯一的真实打扰，且本文件主动消掉它的副作用）
================================================================================
第二层打扰比第一层烦人得多，必须消掉：

  1. **窗口短暂出现**（必需，否则就不是真窗口测试了）；
  2. **抢焦点/抢输入** —— 用户 2026-10-12 报告"刚刚有人抢我鼠标"，机制已查明：
     **抢的是焦点，不是光标**。两条途径：
       * 产品侧 ``src/mpltweak/toolbox.py`` 的 ``Tweak.__init__``（1351-1363）
         自己就 ``w.show()`` + ``w.raise_()``；
       * matplotlib ``FigureManagerQT.show()`` 在
         ``rcParams['figure.raise_window'] = True``（**默认值**）时会
         ``activateWindow()`` + ``raise_()``。

     ⚠️ **注意：这不只是"测试"问题——产品开窗本来就该弹到最前**
     （toolbox.py:1341 注释："用户不需要强抢焦点，只保证弹在最前"）。所以正确
     做法是**只在测试进程内 monkeypatch，绝不改 src/**：
       * 子进程开头设 ``rcParams['figure.raise_window'] = False``；
       * 建图后、构造 ``Tweak`` **之前**，把该窗口实例的 ``raise_()`` /
         ``activateWindow()`` 换成 no-op，并给窗口加
         ``Qt.WA_ShowWithoutActivating``（show() 就不激活窗口）。
     Tk 侧没有等价开关，只能保证测试自己**不**调用 ``lift()``/``focus_force()``
     （Tk 的窗口本就没有 ``raise_()``，产品那行在 toolbox.py:1357-1361 的
     try/except 里静默跳过）。

================================================================================
三层设计（用户批准的"不要很多断言，但多试试不同操作"）
================================================================================
  * **A 层（乱按）**：真窗口里随机手势 **6000 次 = 6 类图形 × 1000 次**，
    **只断言 2 条环境无关铁律**：① 不崩 ② 状态未坏（面板没丢 / 几何合法 / 内容还在）。
    抗假红最强。6 类图形（用户 2026-10-12 要求「换各种不同的图测试一下」）：
    2×2（monkey，既有）｜**4×4 = 16 面板**｜**GridSpec 不规则**（跨行跨列 + 非等分比例）｜
    **主面板 + 独立 colorbar 轴**｜**constrained_layout** 2×2｜**1×3 横排 + tight_layout**
    （+ 故意很长的中文主标题）。逐类读数与等待倍率见「多图形扩展（t22）」一节。
  * **B 层（精测）**：6 条关键效果断言（对应 #1/#2 实测 bug），**全部宽容**
    （范围 / 相对位移 + 容差，**禁止精确等值**）+ **失败自动重试 1 次**。
  * **C 层（环境场景）**：**16 类**场景至少各 1 条断言（8 类环境 + ⑨ 框选/Ctrl 多选
    + ⑩ Tk 回退路径的单位口径 + ⑪ 撤销/重做往返 + ⑫~⑯ 五种新图形的确定性补测）。
    ⑨ 是用户 2026-10-12 目击出来的盲区；⑩ 是 Tk-huge 实测出来的**真缺陷**
    （见「后端差异 / 已知边界」与 KNOWN_DEFECTS）；⑪ 的判据与理由见
    「撤销/重做往返判据」一节；⑫~⑯ 见「多图形扩展（t22）」一节。
  * **硬断言共 27 条**（**无 pin**：C9 曾以"已知缺陷 pin"承载 Tk 单位口径缺陷，t20 修复
    落地后已于 2026-10-12 移除 pin、转为硬断言）：
    A1-A2 + B1-B6 + C1/C2/C3/C4/C6/C7/C8/C9/C10 + C11~C20（16 面板 2 条 / GridSpec 2 条 /
    colorbar 2 条 / constrained_layout 2 条 / 1×3 tight_layout 2 条）。
  * **软检查**：只打警告日志、不判失败；且**拆成独立指标**——
    ① 几何变化率 ② 选中态变化率 ③ 框选命中率（首跑把前两者糊成一个数，
    把"框选/点选"误读成"事件没落地"），另加 ④ 撤销/重做按键次数、
    ⑤ 多图形 fuzz 逐类读数（含每类的等待倍率）。

================================================================================
用户 2026-10-12 目击报告与归因（写进交付物，供后来人）
================================================================================
用户原话：「拖选区域从来都没选中过图，更别说选中多个图」。

**一句话结论（给用户看）：(a) 测试伪影，不是引擎缺陷 —— 框选 / Ctrl 多选是真实产品
功能，且已被 C2/C3/C4 三条断言覆盖；用户看到的是"窗口忠实执行了「完全框住才选中」
的规则"，而当时测试画出的选框太小（≤0.15×画布）根本框不住 0.38×0.34 的面板。**
证据链（全部代码级，只读核实）：
  * 框选 / Ctrl 多选**是真实产品功能**（不是测试凭空造的手势）：
    文件头 ``toolbox.py:19-20`` 明文规则「Ctrl+点击（或 Shift+点击）加选/减选，或拖空白
    框选（框选需「完全框住」面板才选中，相交不算）」；状态 ``@1318-1326``；
    空白按下=框选起点 ``@2710-2713``；框选更新 ``@2812-2814``；框选结束 ``@2477-2504``
    （**判据 ``@2497-2499``：``px0 >= bx0 and px0+pw <= bx1 and ...`` = 完全框住**）；
    Ctrl/Shift 点选切换 ``@2715-2729``（以 ``event.modifiers`` 为准）；
    多选整体拖拽 ``@2733-2744``；帮助表 ``@117-120``；对齐/均分命令依赖选中
    ``@2052`` / ``@3640``。
  * 首跑 A 层的 band 手势 = 随机空白点拖 **±0.15×画布**，而面板是 **0.38×0.34**
    ⇒ **在数学上不可能完全包住** ⇒ 那 40 次框选**必然 0 选中**。
  * 修正：新增 **C2/C3/C4** 三条**选中态**硬断言（框选 2 个 / Ctrl 加选到 3 / 空白清空），
    并把手势指标拆成「①几何变化率」与「②选中态变化率」两个独立指标分开展示。
  * **同时修 A 层 fuzz 的 band 手势尺寸**（首跑只画 ±0.15 的小框 = 数学上包不住面板，
    等于框选路径没测）：现在四态都可达 —— 完整包住 1 个 / 完整包住 2 个 / 框在空白带里
    （数学上不可能包住）/ 小框（视为点击）；并新增软指标「A 层③框选命中率」，
    让"框选到底有没有生效"在日志里**可见**。
  * 附带发现（已报船长并已派修复）：``toolbox.py:2478`` 的 docstring 写「与选框**相交**
    的面板全选中」，与其正下方 ``@2497-2499`` 的代码「**完全框住**才选中」矛盾
    → 注释过期，会误导后来人。

================================================================================
C1 真缺陷证据链（2026-10-12 qt 首跑实测 + 只读代码核实；**C1 断言保持严格**）
================================================================================
实测：请求 figsize **30×22in（长宽比 1.364）** → 实得 **9.12×2.82in（长宽比 3.234，
偏 +137%）**；引擎自身日志：``[mpltweak] 画布 1601x496 超出屏幕可用区 960x516，
已自动缩放到屏内 (57%)``；全程**没有** ``_CLAMP_HINT``（toolbox.py:96）输出
⇒ 0.1.10#1-③ 的钳制守卫**从未触发**。

链条（次序 → 钳制 → 变形 → 被记录为开窗值）：
  1. ``Tweak.__init__`` **先** ``w.show(); w.raise_()``（toolbox.py:1358-1359），
     **之后**才 ``_fit_to_screen()``（1368）与 ``_figsize_open = list(...)``（1369）；
     ``_fit_to_screen`` 全仓**只有一个调用点**（1368）。
  2. ``w.show()`` 时窗口按 30×22in 的尺寸出现 → Windows 把它钳制到屏内。
  3. 钳制触发 Qt ``resizeEvent`` → ``backend_qt.py:389-401``：
     ``w = event.size().width() * self.device_pixel_ratio`` … ``dpival = self.figure.dpi``
     … ``self.figure.set_size_inches(w / dpival, h / dpival, forward=False)``
     —— 用的是**窗口客户区尺寸**、**不保长宽比** ⇒ figsize 被改成非等比。
  4. ``_fit_to_screen()``（toolbox.py:1535）读到**已经被改过**的画布 1601×496，
     再做**等比** 57% 缩放 → 9.12×2.82in（已变形的尺寸被等比缩小，变形原样保留）。
  5. ``_figsize_open``（1369）记录的正是这个 9.12×2.82 ⇒ 之后 ``_on_resize`` 的钳制守卫
     （1489-1503）拿它当基准，而画布此时已不超屏（``@1494`` 条件不成立）
     ⇒ **永不修复**；而且这个变形值会随 ``apply`` 写回用户脚本。
  ※ 直接实测 vs 推断：请求/实得英寸、引擎日志、无 ``_CLAMP_HINT``、B5 Δ=0.000 是**直接
     实测**；"第 2/3 步的窗口被钳制尺寸 = 1601×496"由日志反推（= inches×dpi）。本套件的
     ``huge`` 场景现在**内插桩**（``_instrument()``，仅测试进程内 patch）直接记录
     ``resizeEvent`` 的客户区尺寸与 ``_fit_to_screen`` 前后的英寸，把这条链从推断变实测。
  ※ **行号时效**：本节的 toolbox.py 行号是**修复前**（t19 之前）的；t19 修复后对应
     ``toolbox.py:1341-1363``（_fit_to_screen/_figsize_open **提前到 w.show() 之前**）
     与 ``:1583``（_fit_to_screen 本体）、``:1516``（钳制守卫判据）。

================================================================================
后端差异 / 已知边界（只读核实，2026-10-12）
================================================================================
**Q：没装 PyQt5 的用户，0.1.9 决策 B 承诺的"回退 Tk 开窗"到底能不能开出来？**
**A：能开（结论 (i)，非缺陷）** —— 显示路径**不是** ``Tweak.__init__`` 的 ``w.show()``，
而是启动器收尾那次**真正的** ``plt.show()``：
  * ``launch.py:43-50``：后端候选 ``('QtAgg','Qt5Agg','Qt6Agg','TkAgg')`` 逐个
    ``matplotlib.use(..., force=True)`` → PyQt5 不在就落到 **TkAgg**（回退实现存在）；
  * ``launch.py:367-369``：``_emit_backend_hint(...)`` 往 **stderr** 打一句人话
    （装 ``mpltweak[qt]`` 的提示；不污染 stdout 的 --json / MCP 输出）；
  * ``launch.py:644``：``real_show()`` —— 脚本跑完、Tweak 挂好后调用**真实的**
    ``plt.show()``（脚本自己的 ``plt.show`` 在 ``:386-390`` 被临时换成 no-op，此处已还原）；
  * matplotlib ``backends/_backend_tk.py:491`` ``FigureManagerTk`` 在 ``:510``/``:536``
    ``window.withdraw()``（**创建时就先藏起来**），``:597`` ``def show(self)`` →
    ``:603`` ``self.window.deiconify()`` ⇒ ``plt.show()`` 一调，窗口就出来，关窗结束 mainloop。
  * 所以 ``toolbox.py:1381`` 的 ``w.show()`` 在 Tk 上只是**被静默跳过的冗余尝试**
    （Tk toplevel 没有 ``show()`` → ``AttributeError`` → ``:1380-1386`` try/except 吃掉）；
    全仓 grep 确认 src/mpltweak 里**没有任何** ``deiconify``/``wm_deiconify``/``update()``。
  * 由此解释本套件的 tk 第 1 次失败：**测试**只构造 Tweak、从不调 ``plt.show()``（会阻塞），
    所以 Tk 场景必须由测试自己 ``deiconify()``（``TkDriver.show_window()``）——
    是**测试的责任**，不是产品缺陷。
  * 与 Qt 的一个可见差别（不影响可用性）：Qt 上窗口在 ``Tweak.__init__`` 里就显示，
    **Tk 上要等到 ``launch.py:644`` 那次 ``plt.show()`` 才显示**（用户观感差一瞬）。

**★ Tk 侧真缺陷（2026-10-12 实测确认，(iii) 结论：`_fit_to_screen` 未生效且 figsize 被改形）**
触发命令：``python tests/test_real_window.py --backend tk --scenario huge``（figsize 30×22in @dpi100，
Tk 窗口按真实用户时序：``Tweak.__init__`` 期间 withdrawn、到 ``launch.py:644`` 的 ``plt.show()`` 才 deiconify）。
实测数据（一次运行，全部来自 ``@@RW@@`` 与引擎日志）：
  * 引擎日志：``画布 3000x2200 超出屏幕可用区 3840x2160，已自动缩放到屏内 (93%)``；
    ``_fit_to_screen`` trace：``before [30.0, 22.0] → after [27.9818, 20.52]`` ⇒ 只缩到 93.3%
    （等比 ✅，但**远远不够**）。
  * 映射后窗口客户区 **5596×2119 物理 px**（服务器屏幕只有 3840×2160）⇒ **比屏幕还宽**，
    WM 把高度钳到 ≈2119px。
  * 最终 figsize **27.98×10.275in** ⇒ 长宽比 **2.723** vs 请求 **1.3636** ⇒ **偏 +99.7%（高度被砍一半）**。
  * 全程 **没有** ``_CLAMP_HINT`` ⇒ 0.1.10#1-③ 的钳制守卫**没触发**。
链条（单位口径不一致，两半互相掩盖）：
  1. ``toolbox.py:1533`` ``_screen_available()`` 在 Tk 上返回 ``winfo_screenwidth/height``
     = **物理**像素（Tk 进程 DPI-unaware）→ 实测 ``3840×2160``（逻辑屏幕其实只有 1920×1080）。
  2. ``toolbox.py:1583`` ``_fit_to_screen()`` 拿 ``canvas.get_width_height()``（**逻辑**像素）
     去比这块**物理**屏幕 ⇒ 阈值实际放宽约一倍 ⇒ ``_fit_size_for`` 算出 93% 而不是 ~60%。
  3. 更糟的是：窗口还没映射时 ``device_pixel_ratio`` 读出来是 **1.0**（matplotlib
     ``_backend_tk.py:207`` 在 ``<Map>`` 事件里才 ``_update_device_pixel_ratio()``，
     实测：withdrawn 时 dpr=1.0、映射后 dpr=2.0）⇒ 第 2 步连"逻辑像素"都不准，直接拿
     figsize×dpi 当画布像素。
  4. 于是窗口按"比屏幕还大"的尺寸出现 ⇒ WM 钳制 ⇒ matplotlib ``_backend_tk.py:279-286``
     的 ``resize()``（Tk ``<Configure>``）用客户区像素逐轴 ``set_size_inches(...)``
     ⇒ **figsize 被改成非等比**（实测高度 20.52→10.275in）。
  5. ``toolbox.py:1516`` 的钳制守卫判据 ``if _W > _avail[0] or _H > _avail[1]`` 同样混单位
     （逻辑 2798×1027 vs 物理 3840×2160）⇒ **条件为假** ⇒ 不恢复 ⇒ 变形**永久固化**
     （并会随 ``apply`` 写回用户脚本）。
影响面（推导，非实测）：**任何画布逻辑像素大于逻辑屏幕、但小于物理屏幕的图**都会中招
（本机 200% 缩放下 ≈ 画布宽 1920~3840px；dpi 越高英寸阈值越低，dpi200 时 ≈9.6~19.2in）。
**这正好打在没有 PyQt5 的用户的回退路径上**（``launch.py:43-50`` 落 TkAgg，见上一节）。
Qt 侧不受影响：``avail`` 与 ``get_width_height()`` 都是逻辑像素，且 t19 已把
``_fit_to_screen``/``_figsize_open`` 提前到 ``show()`` 之前（``toolbox.py:1341-1363``）。

**★ 闭环（2026-10-12，t20 修复后）**：`--backend tk` 复跑实测 **偏差 +99.7% → 0.0%**
（请求 30×22in 比值 1.364 → 实得 13.99×10.26in 比值 1.364），窗口客户区 **2798×2116 ≤
屏幕 3840×2160**；fit trace 显示**两次** ``_fit_to_screen``：开窗前的等比 93%，以及
**映射后的二次自检**（``27.98×10.275 → 13.9909×10.26`` 等比修正，正是 t20 的修法②）。
C9 的 pin 已移除、转为硬断言；证据：``run_tk4.log``（pin 过期报红）→ ``run_tk5.log``（转绿）。
t20 的改动位置（供后来人）：``toolbox.py:1517`` dpr 自检、``:1556 _canvas_dpr``、
``:1612`` Tk 折逻辑 px、``:1637`` 英寸→逻辑 px（Tk 折算、Qt 不折算）。

================================================================================
撤销 / 重做往返判据（C10；口径与理由写在这里，NUMBERS.md 同步）
================================================================================
  * 一次"往返" = 拖动面板 → **Ctrl+Z** → **Ctrl+Y**，取四个几何快照（**全部面板**的
    bbox，figure 比例坐标 6 位小数）：G0 拖前 ｜ G1 拖后 ｜ G2 undo 后 ｜ G3 redo 后。
  * **判据（未放宽）**：**每一次"确实被驱动"（拖动真的产生了位移）的往返都必须端到端
    一致** —— ``|G2−G0|∞ ≤ 0.002`` **且** ``|G3−G1|∞ ≤ 0.002``；并且本轮至少要有 **1 次**
    干净往返（否则判据会退化成恒真）。
  * **已知 harness 限制（2026-10-12；有界排查一轮后记为"未定位"，影响面有限）**：
    本夹具里**第 2 次及以后的拖动**，matplotlib 自己报 ``inaxes=None``（btn=1、事件确实投递
    到 canvas 级回调、引擎的 press 回调也仍在：``canvas.callbacks`` 7/7/7 不变），而
    ``_bbox(ax1)`` 说该点在面板内 ⇒ 属**测试侧坐标系异常**，未定位。
    **已排除的**（有界排查一轮的实测）：坐标基准**没有漂移** —— 两次拖动前
    ``get_width_height()=800×600``（逻辑）、``fig.bbox=1600×1200``（物理）、``dpr=2.0``
    完全一致；且引擎口径（``_bbox`` × figbbox）与 matplotlib 的 ``ax.bbox.bounds``
    **逐位相同**（ax0=[128,660,608,408]、ax1=[896,660,608,408]）；press 事件坐标
    (1200,864) 确实落在 ax1 的 mpl bbox 内。⇒ 漂移/单位口径**不是**原因，剩余怀疑在
    "mpl 的 inaxes 判定 vs 事件投递"这一层，本轮未再深挖。
    **影响面**：只影响"**连续多次往返**"的取数（第 1 次往返干净通过：``d_undo=0.0``、
    ``d_redo=0.0``，引擎日志 ``拖轴开始/结束 ax0``）；C10 因此只断言"**每一次确实被驱动的
    往返都必须端到端一致**，且至少 1 次"，未驱动部分显式标注、不计入一致、也不冒充缺陷。
    原始证据：``_tmp_t16_real/undoredo_diag.log``（``press_events``/``axes_dump``/
    ``press_cbs``/``base`` 四组）。
  * **"上一态"不是我们假设的**：它就是引擎 ``_push_undo()``（toolbox.py:2454）自己压进
    undo 栈的那份 bbox 快照；绑定见 toolbox.py:3774-3778，帮助表 @220-221
    （Ctrl+Z 撤销 ／ Ctrl+Y·Ctrl+Shift+Z 重做）。
  * 容差 0.002（千分之二，约一两像素量级）是"宽容但会咬人"的口径：它不要求精确等值，
    但足以抓住"撤销没回上一态 / 重做没复现"；**不写恒真判据**（例如只断言"不崩"）。
  * **真实性口径**：只报**本轮实跑**发生的往返数字；**不用"同 seed 确定性重放"造历史
    轮次的数字**（重放可作交叉验证，但不作 README 数）。
  * A 层的随机 `key` 手势也会发到 ctrl+z/ctrl+y —— 那部分**只计数**
    （软指标「A 层④撤销/重做按键次数」）；一致性判定只认 C10 的确定性往返
    （随机按键无法配成"往返对"）。

================================================================================
多图形扩展（t22）：图形 × 操作 矩阵里"真窗口"的那一半
================================================================================
用户 2026-10-12 要求「**换各种不同的图测试一下，增加可靠性**」。本条把 A 层从"一种图"
扩到 **6 类图形 × 1000 次 = 6000 次**，并给 5 种新图形各加 2 条 C 层断言（C11~C20）。

  * **判据一律不放宽**：A 层仍是那两条铁律（只多跑图形，不改判据）；B 层 6 条一字未改；
    新图形的 Ctrl+F 判据**复用 B1 的既有口径**（每维 ratio∈[0.80,1.20] 且确实生效），
    不新造弱判据（**没有**任何一条是"跑完没崩就算过"）。
  * **pump_scale（等待倍率：只改节奏，不改判据）**：monkey 仍用 **1.0**（与既有 4 轮
    口径一致；1000 次实测 **248s**）；5 种新图形用 **0.25**（每步等待 1/4）。
    理由：6000 次手势若全按 B/C 层节奏（每步 12~70ms 真等待）总时长 ~25min，而
    A 层只断言"不崩 + 状态未坏"；事件注入本身是**同步**投递（QTest / event_generate
    + processEvents），等待只影响引擎的节流与重绘节奏。每类的 pump_scale 都显式写在
    `CFG_DEFAULT`、进日志、并进 NUMBERS.md 内部区（含每类实测耗时）。
  * **每类图形的确定性补测**（都在**开窗后、乱按前的新版面**上做，同一窗口内；理由见下）：
      - **4×4 = 16 面板**（C11/C12）：拖 1 个面板 → **只有该面板**几何变化（其余 ≤0.002 视为
        没动；目标面板先用 mpl 的 `canvas.inaxes` 确认"中心点确实落在它自己身上"，
        避免面板重叠时把事件打进别的面板）；框选完整包住左上 2×2 → 选中的**正是那 4 个**
        （多面板命中判定 + 事件路由）。框选起手点必须让**引擎**判为空白
        （`toolbox.py:2710-2713`），且选框不许把别的面板**完整**框住——两条都由子进程
        按**显示坐标**显式检查并回报（`margin`/`start`/`fully_other`）。
      - **GridSpec 不规则**（C13/C14）：开窗后每个面板的实测 bbox 与 **matplotlib 自己
        声明的 GridSpec 几何**（`GridSpec.get_grid_positions(fig)`）一致（≤0.002）——
        即"引擎没有把不规则版面拉平"；判据不恒真由**声明几何**兜底（行高比 ≥1.5、
        整行宽/单列宽 ≥1.5，否则这条会 FAIL 提示 fixture 退化成等分）。另做 Ctrl+F 复核。
      - **主面板 + 独立 colorbar 轴**（C15/C16）：colorbar 轴数用**引擎自己的**
        `_is_colorbar_ax`（多信号判定）数，必须是 1；拖主面板 → colorbar 轴几何**不变**
        （≤0.002）；拖 colorbar 本体 → colorbar 确实变了、主面板**不变**。
        ※ "colorbar 不跟随主面板"是**当前设计的实测行为**（引擎只移动被拖/被选中的面板，
        `toolbox.py:2940` 多选拖拽同此口径）。若将来改成"colorbar 跟随父面板"，这两条会
        报红——属**有意变更**，改判据时必须同步改这段文字与 NUMBERS.md（不许静默放宽）。
      - **constrained_layout**（C17/C18）：开窗后**布局稳定**——连续两次采样（间隔
        500ms 事件泵）的 figsize 与全部面板几何 ≤0.002 不变（constrained_layout 会在
        每次绘制时重算，所以"稳定"是它的核心不变量）；另做 Ctrl+F 复核。
        ★ **C18 的判据在 2026-10-12 首轮真跑后修正过一次，理由与证据如下**（不是放宽）：
        首轮 C18 FAIL（`ratio=1.000/1.000`、`确实生效=False`）。定位过程：① 离线用**同一
        fixture**（Agg、同 figsize）直接调 `Tweak._fit_to_content()`，抓到引擎原文
        `[mpltweak] fit: 内容已贴边，无需适配（10.00x7.00 in）` ⇒ 按键到达了引擎，引擎走的是
        `toolbox.py:2352-2355` 的**设计内分支**（内容盒已 ≥ 画布 ⇒ 无事可做），**不是缺陷**；
        ② 同一探针在 gridspec 上得到 `8.34x7.08（原 10.00x7.50）`、ratio 0.8343/0.9443，与真窗口
        实测的 0.834/0.943 **逐位相同** ⇒ 按键路径与换算口径都正常。结论：**原判据（"必须生效"）
        对 constrained_layout 图本身就是错的**（该图按设计已无白边可裁），属测试侧 bug。
        修正后的守卫**更严**：ratio 区间 [0.80,1.20] 与面板数判据**一字未改**，另要求
        **要么真的裁了**（ratio 偏离 1 > 0.005），**要么引擎明确回报"收到快捷键"+"内容已贴边，
        无需适配"**（键盘若没到引擎，这两行都不会出现 ⇒ FAIL；自检里有一条专门的空虚性变异
        为此把关）。引擎原文逐字进证据（`ratio_check['fit_log']`）。
      - **1×3 横排 + tight_layout + 长中文标题**（C19/C20）：三面板同一行（y0 极差
        ≤0.002）、x 严格递增、互不重叠；长中文 suptitle 在 Ctrl+F **前后**都完整在
        画布内（0.5px 容差，与 B2 同口径）。
  * **为什么补测放在 fuzz 之前**（不是为了省事）：这些判据问的是"引擎在**版式定义清楚的
    新版面**上怎么处理一次确定手势"。乱按 1000 次之后面板已互相重叠、位置任意：
    "左上 2×2 的并集外框"没有定义（框选退化成碰运气）、Ctrl+F 会为了包住被拖到画布外的
    面板而**放大**画布（ratio 判据会被夹具状态误伤，不是引擎错）。所以补测一律在**新版面**
    上量；**fuzz 只负责 A 层那两条铁律**，fuzz 之后只复核"面板还在不在"。
  * **一次窗口两用**：每类图形一个场景、一个子进程，顺序 = 开窗 → 确定性补测 → A 层
    1000 次手势。理由："开窗口贵、手势便宜"（窗口开关 + 首帧才是主要成本）；两条线的判据
    **分开写**——补测只喂 C11~C20，fuzz 只喂 A1/A2，互不借力。

================================================================================
harness 单位口径修正（t22，**测试侧**；不改 src/、不放宽任何判据）
================================================================================
问题：``toolbox._hit_handle()`` 与 matplotlib 的 ``ax.bbox`` / ``fig.transFigure`` 都在
**显示坐标**里工作（Qt 侧 = 逻辑 px × ``device_pixel_ratio``；本机 200% 缩放 ⇒ ×2），
而本套件注入事件用的是**逻辑 px**（``QtDriver._pt()`` → 控件坐标，matplotlib 自己乘 dpr；
``backend_qt`` 源码实证：``_get_event_xy`` = ``pos.x() * dpr``、
``y = (fig.bbox.height / dpr - pos.y()) * dpr``）。
⇒ 凡"问引擎会不会把这点当手柄"的地方，都要把逻辑 px **乘 dpr** 再问。

修了 3 处（**只影响"选点/是否执行"，注入点一字未改、判据一个字没动**）：
  * ``_find_blank()``：空白点的**手柄探测**改用显示 px ⇒ 不再可能给出"引擎其实当拉角"的点；
  * ``_one_gesture()`` 的 ``resize`` 分支与 band 起手角：手柄探测改用显示 px ⇒ **拉角手势
    真的开始执行**（修正前 dpr=2 时抓取区被缩小一半，绝大多数拉角手势被自己跳过，A 层其实
    几乎没测到真缩放）⇒ 预期 **A 层"几何变化率"会明显上升**，那是覆盖变真、不是引擎变了；
  * ``_fig_open_checks()`` 的框选起手点搜索：同上，并把 ``margin``/``start``/``fully_other``
    写进证据（证明"起手点确实是空白、且没有别的面板被完整框住"）。

**与「撤销/重做」那一处未定位坐标系异常的关系**（同一**族**问题，但**不是**同一个 bug）：
  * 同族：都是"**harness 与引擎在坐标系/单位上口径不一致**"——本套件一律按**逻辑 px**
    记点、注入，而引擎/ matplotlib 的命中判定走**显示 px**（× dpr）。
  * 但单位口径**不能解释** C10 的 ``inaxes=None``：那一轮的诊断证据显示，引擎口径
    （``_bbox`` × figbbox）与 matplotlib 的 ``ax.bbox.bounds`` **逐位相同**
    （ax0=[128,660,608,408]、ax1=[896,660,608,408]），且 press 事件坐标 (1200,864)
    确实落在 ax1 的 mpl bbox 内 ⇒ 那次"点在图内却被判 None"**不是** px/dpr 折算错。
  * ⇒ 该异常**仍记为未定位**（见第 2 节），排查方向不变：优先查
    ``ax.patch.get_visible()``（mpl 的 ``inaxes`` 要求 patch 可见；若被 ghost/blit 流程
    隐藏过没恢复，那就是**真缺陷候选**）。本次单位修正只让"手柄探测"这一类判定与引擎对齐，
    不要误读成"那个异常已经修好了"。

================================================================================
已知低危项（有实测口径；记入 0.1.11，本套件**不改 src/**）
================================================================================
  * **离散插值拖拽时，ghost 相对"最后一次 motion"滞后一步**：C8 实测 —— 16 步插值
    走完时 ghost x0 = 0.1362 = 目标 0.1050 **+ 恰好 1/16 位移**；补两次 settle
    （落点重复）后被吸附到 0.1000，松手后 0.1000，残差 0.0000。第 1 轮 5 步插值时
    偏差恰为 4/5，与首跑现象吻合。真实用户是连续上百个 motion ⇒ 影响 ≈1/N，
    肉眼不可见 ⇒ **本套件用"更真实的输入"（16 步 + settle）覆盖，C8 判据未放宽**
    （残差 ≤0.002、位移误差 ≤0.02）。
  * C1 的"窗口被钳制尺寸"在第 1 轮由引擎日志反推（1601×496 = inches×dpi）；现在
    ``huge`` 场景带 ``_instrument()`` 插桩（仅测试进程内 patch），可直接实测
    ``resizeEvent`` 客户区尺寸与 ``_fit_to_screen`` 前后的英寸。
  * **跨后端坐标单位（测试侧教训，非产品缺陷）**：Qt 控件坐标是**逻辑** px
    （matplotlib 自己乘 dpr），Tk 控件坐标是**物理** px（而 ``get_width_height()``
    返回逻辑 px）⇒ Tk 驱动必须自己乘 ``device_pixel_ratio``，否则注入位移**恰好差一半**
    （首跑 C7 实测 0.5×）。Qt 侧传逻辑 px 则实测 |Δ|=0.000。

================================================================================
纪律
================================================================================
  * **不进 CI**（需要真实显示器）、**不进 pytest 收集**（pyproject.toml 的
    ``python_files`` 只收 ``test_suites.py`` / ``test_hygiene.py``；本文件是
    模块级独立套件，风格同其它套件：末尾 ALL PASS/FAILED + ``sys.exit``）。
  * **不改 src/**：发现真实缺陷（HiDPI / 钳制 / 拖拽落位）只如实报告，不自己修。
  * 真窗口测试**不要**设 ``MPLBACKEND=Agg``（否则就没有窗口了）。本文件在子进程
    env 里**显式删掉** MPLBACKEND（若外部环境设了 Agg），再用 ``matplotlib.use()``
    显式指定后端。
  * 父进程**绝不** import pyplot —— 所有窗口只存在于子进程里，**一个场景一个
    子进程**、跑完即退，避免窗口堆积与残留 GUI 进程（超时会 ``taskkill /T``）。

用法：
    python tests/test_real_window.py --dry-list            # 免弹窗：只列场景与断言
    python tests/test_real_window.py --selftest            # 免弹窗：断言机器自检（合成数据）
    python tests/test_real_window.py --backend qt          # Qt 后端（主档）
    python tests/test_real_window.py --backend tk          # Tk 后端（冒烟）
    python tests/test_real_window.py --backend all         # 两个后端都跑
    python tests/test_real_window.py --layer A             # 只跑 A 层
    python tests/test_real_window.py --log run.log         # 同时把完整日志写文件
内部参数（父进程自己用，也可手工单跑一个场景看原始测量）：
    python tests/test_real_window.py --scenario fit --backend qt --emit-json
    ⚠️ 手工直跑 `--scenario` 时必须**自己设 PYTHONPATH**（父进程会自动注入，直跑不会）：
        set PYTHONPATH=D:\Agent\deepseek\mpltweak\src
        python tests/test_real_window.py --backend tk --scenario huge    # C9 用的就是这个

变更记录（对后来人）：
  * **t22（2026-10-12）多图形扩展**：A 层从"一种图 200 次"扩到 **6 类图形 × 1000 次**，
    新增 C11~C20（共 27 条硬断言）、`pump_scale`（等待倍率，只改节奏）、
    `_fig_fixture()` / `_fuzz_loop()` / `_fig_open_checks()` / `_fig_post_checks()`；
    `--timeout` 默认 300→1800s（1000 次手势的场景在 300s 下会被误杀）。
    **monkey 场景的版面与 pump×1.0 一字未改**（与既有 4 轮口径可比）；`B1~B6` 判据未动。
    旧 17 条口径的日志（`run_qt*.log` ≤8 / `run_tk*.log` ≤6）**不再代表本版**，见 NUMBERS.md。
  * Tk-huge 场景（C9）引入后，`TkDriver.tk_geom()` 与 `_sc_huge` 的 **Tk 分支**已变更
    （要在 Tweak **之后**才 deiconify，忠实复现 `launch.py:644` 的时序）；
    **qt 分支与 tkwin 场景一字未变**。
"""

import argparse
import io
import json
import math
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time
import traceback

# ---------------------------------------------------------------------------
# 路径 / 常量
# ---------------------------------------------------------------------------
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, 'src')
HERE = os.path.abspath(__file__)
MARK = '@@RW@@'          # 子进程把测量结果以这一行 JSON 交回父进程

# 套件风格：中文消息（与其它套件一致）
os.environ.setdefault('MPLTWEAK_LANG', 'zh')

# 本文件会把**任意子进程文本**（含 GBK 字节解出来的 U+FFFD）交给 print，
# 按 test_hygiene.py 第 3 条守卫的规则必须装输出兜底（GBK 控制台不崩）。
try:
    sys.stdout.reconfigure(errors='replace')
    sys.stderr.reconfigure(errors='replace')
except (AttributeError, ValueError, OSError):
    pass

# ---------------------------------------------------------------------------
# 场景与断言台账（--dry-list 直接打印它们，不 import pyplot、不开任何窗口）
# ---------------------------------------------------------------------------
# 每项: (场景 id, 后端, 环境档, 覆盖的 C 场景, 说明)
SCENARIOS = [
    ('monkey', 'qt', '1x / dpi100', 'A 层（乱按）· 2×2',
     '真窗口随机手势 1000 次（2×2 四面板）：拖面板 / 拉角 / 拖图例 / 框选 / 修饰键点选 / 快捷键 / 方向键'),
    ('figs_grid4x4', 'qt', '1x / dpi100', '⑫ 4×4＝16 面板',
     '4×4=16 面板：随机手势 1000 次（pump×0.25）+ 拖 1 个只动该面板 + 框选完整包住左上 2×2'),
    ('figs_gridspec', 'qt', '1x / dpi100', '⑬ GridSpec 不规则版面',
     'GridSpec 3×3 跨行跨列（height_ratios=[2,1,1] width_ratios=[1,2,1]）：随机手势 1000 次'
     '（pump×0.25）+ 开窗结构与 mpl 声明几何一致 + Ctrl+F 不缩半'),
    ('figs_colorbar', 'qt', '1x / dpi100', '⑭ 主面板 + colorbar 轴',
     '主面板 + 独立 colorbar 轴（cax）：随机手势 1000 次（pump×0.25）'
     '+ 拖主面板 colorbar 不动 + 拖 colorbar 主面板不动'),
    ('figs_clayout', 'qt', '1x / dpi100', '⑮ constrained_layout',
     'constrained_layout 2×2：随机手势 1000 次（pump×0.25）+ 开窗布局稳定 + Ctrl+F 不缩半'),
    ('figs_tight1x3', 'qt', '1x / dpi100', '⑯ 1×3 横排 + tight_layout',
     'plt.subplots(1,3)+tight_layout+长中文 suptitle：随机手势 1000 次（pump×0.25）'
     '+ 横排几何校验 + 长标题不裁'),
    ('fit', 'qt', '1x·2x × dpi100·200', '② 200% 缩放 · ③ Ctrl+F 裁白边',
     'Ctrl+F 裁白边：4 组（QT_SCALE_FACTOR∈{1,2} × dpi∈{100,200}）'),
    ('huge', 'qt', '2x / dpi100', '① 小屏开大图 · ⑤ 窗口被钳制',
     '请求 30×22in（远超屏幕可用区）→ 开窗自动缩放 + 事件泵稳定性'),
    ('drag', 'qt', '1x / dpi100', '④ 真拖拽位移',
     'QTest 真拖拽面板 → 位移量（相对位移 + 容差）'),
    ('snap', 'qt', '1x / dpi100', '⑧ 多面板+吸附',
     '4 面板 + 拖到 0.005 缝隙 → 吸附应把缝隙收成 0'),
    ('qtwin', 'qt', '1x / dpi100', '⑥ Qt 后端', 'Qt 真 native 窗口存在性'),
    ('hoverkeys', 'qt', '1x / dpi100', '观察项（软检查）',
     '依赖鼠标悬停的 +/- 快捷键（只观测不判失败）'),
    ('tkwin', 'tk', '1x / dpi100', '⑦ Tk 后端',
     'Tk 真窗口 + event_generate 真拖拽'),
    ('select', 'qt', '1x / dpi100', '⑨ 框选 / Ctrl 多选（用户目击的盲区）',
     '框选完整包住上排 2 个 → Ctrl 点选加选第 3 个 → 空白点击清空'),
    ('undoredo', 'qt', '1x / dpi100', '⑪ 撤销 / 重做往返',
     '拖面板 → Ctrl+Z 回上一态 → Ctrl+Y 复现（2 次往返，偏差 ≤0.002）'),
]

# 每项: (断言 id, 层, 说明, 后端, 失败是否重试)
CHECKS = [
    ('A1', 'A', '乱按不崩：6 类图形 × 1000 次真手势（=6000 次）零异常、子进程 rc=0', 'qt', False),
    ('A2', 'A', '乱按后状态未坏：面板未丢 / 几何合法 / 内容还在（6 类图形逐类查）', 'qt', False),
    ('B1', 'B', '200% 缩放 + dpi=200 下 Ctrl+F 不缩半（ratio∈[0.55,0.99]）且确实生效', 'qt', True),
    ('B2', 'B', 'Ctrl+F 后主标题完整在画布内，且与内容不重叠（gap ≥ -2px）', 'qt', True),
    ('B3', 'B', 'Ctrl+F 结果与 dpi / 屏幕缩放解耦（4 组 ratio 极差 ≤ 0.08）', 'qt', True),
    ('B4', 'B', '超大 figure：画布被缩到屏内、只缩不放、该缩时确实缩了', 'qt', True),
    ('B5', 'B', '超大 figure：事件泵 2s 后 figsize 不被静默改写（±0.05in）', 'qt', True),
    ('B6', 'B', '真拖拽位移符合预期（相对位移 ±0.03 归一化）', 'qt', True),
    ('C1', 'C', '小屏开大图：窗口 frameGeometry 未越出可用区（≤1.15×）+ 长宽比守恒', 'qt', True),
    ('C6', 'C', 'Qt 后端：真 native 窗口存在且可见', 'qt', True),
    ('C7', 'C', 'Tk 后端：真窗口已映射 + event_generate 真拖拽位移符合预期', 'tk', True),
    ('C8', 'C', '多面板吸附：0.005 的缝隙被吸成 0（≤0.002）', 'qt', True),
    ('C2', 'C', '框选（完整包住上排 2 个）→ 选中的正是那 2 个面板', 'qt', True),
    ('C3', 'C', 'Ctrl+点击下排左面板 → 加选到 3 个（实测 event.modifiers 路径）', 'qt', True),
    ('C4', 'C', '空白纯点击 → 清空选中', 'qt', True),
    ('C9', 'C', 'Tk 下 huge 长宽比偏差 ≤2%（t20 已修：+99.7% → 0.0%，现为硬断言）', 'tk', True),
    ('C10', 'C', '撤销/重做往返一致（undo 回上一态 + redo 复现，偏差 ≤0.002）', 'qt', True),
    ('C11', 'C', '4×4=16 面板：手势后 16 个都在 + 拖 1 个后面板集合不变、只有该面板几何变（≤0.002 判不动）', 'qt', True),
    ('C12', 'C', '16 面板：框选完整包住左上 2×2 → 选中的正是那 4 个（多面板命中/路由）', 'qt', True),
    ('C13', 'C', 'GridSpec 不规则版面：开窗后每个面板实测 bbox 与 matplotlib 声明的 GridSpec 几何一致（≤0.002）', 'qt', True),
    ('C14', 'C', 'GridSpec 图：Ctrl+F 不缩半（每维 ratio∈[0.80,1.20] 且确实生效）+ 面板数不变', 'qt', True),
    ('C15', 'C', '主面板+colorbar：colorbar 轴数=1（引擎多信号判定）+ 拖主面板后 colorbar 几何不变（≤0.002）', 'qt', True),
    ('C16', 'C', '主面板+colorbar：拖 colorbar 本体 → colorbar 确实变了、主面板几何不变（≤0.002）', 'qt', True),
    ('C17', 'C', 'constrained_layout：开窗后布局稳定（两次采样 figsize 与全部面板几何 ≤0.002 不变）', 'qt', True),
    ('C18', 'C', 'constrained_layout 图：Ctrl+F 不缩半（每维 ratio∈[0.80,1.20]；且"确实生效"'
                  '或引擎明确回报"内容已贴边、无需适配"）+ 面板数不变', 'qt', True),
    ('C19', 'C', '1×3 tight_layout：三面板同一行（y 差 ≤0.002）/ x 严格递增 / 互不重叠，且面板数=3', 'qt', True),
    ('C20', 'C', '1×3 图：长中文 suptitle 完整在画布内（Ctrl+F 前后都成立，容差 0.5px）', 'qt', True),
]

# C 层 8 类场景 → 断言映射（报告里逐条打印，证明 8 类都覆盖到了）
C_MAP = [
    ('① 小屏开大图', 'huge（QT_SCALE_FACTOR=2，逻辑可用区只剩 960×516）→ C1'),
    ('② 200% 缩放', 'fit × (QT_SCALE_FACTOR=2) → B1 / B3'),
    ('③ Ctrl+F 裁白边', 'fit → B1 / B2'),
    ('④ 真拖拽位移', 'drag → B6'),
    ('⑤ 窗口被钳制', 'huge → B4 / B5 / C1（stderr 里的钳制提示也会记录进证据）'),
    ('⑥ Qt 后端', 'qtwin → C6'),
    ('⑦ Tk 后端', 'tkwin → C7'),
    ('⑧ 多面板 + 吸附', 'snap → C8'),
    ('⑨ 框选 / Ctrl 多选', 'select → C2 / C3 / C4（★ 用户 2026-10-12 目击出来的盲区，见文件头）'),
    ('⑩ Tk 回退路径（单位口径）', 'huge@tk → C9（t20 已修：长宽比 +99.7% → 0.0%；pin 已移除，现为硬断言）'),
    ('⑪ 撤销 / 重做往返', 'undoredo → C10（真 Ctrl+Z / Ctrl+Y）'),
    ('⑫ 4×4＝16 面板', 'figs_grid4x4 → C11 / C12（多面板事件路由 + 框选命中 4 个）'),
    ('⑬ GridSpec 不规则版面', 'figs_gridspec → C13 / C14（结构与声明几何一致 + Ctrl+F 不缩半）'),
    ('⑭ 主面板 + colorbar 轴', 'figs_colorbar → C15 / C16（colorbar 轴不被连带改 / 拖 colorbar 不动主面板）'),
    ('⑮ constrained_layout', 'figs_clayout → C17 / C18（开窗布局稳定 + Ctrl+F 不缩半）'),
    ('⑯ 1×3 横排 + tight_layout', 'figs_tight1x3 → C19 / C20（横排几何 + 长中文标题不裁）'),
]

# ---------------------------------------------------------------------------
# 已知缺陷 pin（沿用本项目既有做法：``tests/test_property_writeback.py`` 里的
# ``_known_defect_sparse_axis``（t5 修复后已移除）—— **断言照写、判据不放松**，
# 但在 pin 期把"预期 FAIL"显式登记出来，让套件保持**可信的绿**：
#   * 断言一样照跑，实测值一样打印；
#   * 预期 FAIL 且确实 FAIL → 记 ``[PIN]``，**不计入失败**；
#   * 预期 FAIL 却**通过了** → 说明缺陷已修 ⇒ **报 FAIL** 并要求移除 pin
#     （这条正是防"无主 pin"永久留在仓库里的闸门）。
# ---------------------------------------------------------------------------
KNOWN_DEFECTS = {
    # （空）2026-10-12：C9 的 pin 已按它自己的 remove_when 移除 ——
    # t20 落地后复跑实测：长宽比偏差 **+99.7% → 0.0%**
    # （请求 30×22in 比值 1.364 → 实得 13.99×10.26in 比值 1.364），
    # 窗口客户区 2798×2116 ≤ 屏幕 3840×2160，fit trace 显示"映射后二次自检"确实跑了
    # （27.98×10.275 → 13.9909×10.26 等比修正）。C9 现在是**硬断言**（无 pin）。
    # 证据：run_tk4.log（pin 过期报红 → 触发移除）、run_tk5.log（移除后同一判据转绿）。
    # 历史：本条沿用本项目 ``_known_defect_sparse_axis``（t5 修复后移除）的做法 ——
    # 带缺陷编号、实测证据、源码行号与 remove_when 双条件；机制本身（Report.pin +
    # 三向自检）保留，供将来再出现已知引擎缺陷时复用。
}

SOFT_ITEMS = [
    'A 层①几何变化率（真窗口点击可能因抢焦点丢失）—— 只看这一个数会把"框选/点选"'
    '误读成"没落地"，必须与②分开看',
    'A 层②选中态变化率（选择类事件是否真的落到了引擎的选择逻辑）',
    'Ctrl+F 是否真的缩短了画布（ratio < 1）',
    '窗口 isActiveWindow（焦点是否被系统抢走）',
    '依赖 hover 的 +/- 快捷键是否崩（观察项；禁跑期无法先验证，只记录不判失败）',
    'A 层④撤销/重做按键次数（随机手势里实发次数；一致性只由 C10 的确定性往返判定）',
    'A 层⑤多图形 fuzz 逐类变化率 + pump 倍率（monkey 1.0，5 种新图形 0.25：'
    '等待只影响引擎节流/重绘节奏，A 层两条铁律判据一字未改）',
]

# 默认场景配置
#   * ``n``          —— A 层 fuzz 手势次数（6 类图形各 1000 次 ⇒ 合计 6000 次）
#   * ``pump_scale`` —— 每步事件泵等待的倍率（**只改节奏，不改任何判据**）：
#                       monkey 用 1.0（与既有 4 轮口径一致），5 种新图形用 0.25
#                       （理由见文件头「多图形扩展」一节与 NUMBERS.md 内部区）。
#   * ``kind``       —— 图形种类（`_fig_fixture` 按它造图）
CFG_DEFAULT = {
    'monkey': {'figsize': [9.0, 6.5], 'dpi': 100, 'n': 1000, 'seed': 20261012,
               'kind': 'sub2x2', 'pump_scale': 1.0},
    'figs_grid4x4': {'figsize': [12.0, 9.0], 'dpi': 100, 'n': 1000, 'seed': 20261012,
                     'kind': 'grid4x4', 'pump_scale': 0.25},
    'figs_gridspec': {'figsize': [10.0, 7.5], 'dpi': 100, 'n': 1000, 'seed': 20261012,
                      'kind': 'gridspec', 'pump_scale': 0.25},
    'figs_colorbar': {'figsize': [10.0, 6.5], 'dpi': 100, 'n': 1000, 'seed': 20261012,
                      'kind': 'colorbar', 'pump_scale': 0.25},
    'figs_clayout': {'figsize': [10.0, 7.0], 'dpi': 100, 'n': 1000, 'seed': 20261012,
                     'kind': 'clayout', 'pump_scale': 0.25},
    'figs_tight1x3': {'figsize': [12.0, 4.5], 'dpi': 100, 'n': 1000, 'seed': 20261012,
                      'kind': 'tight1x3', 'pump_scale': 0.25},
    'fit': {'figsize': [3.0, 2.2], 'dpi': 100},
    'huge': {'figsize': [30.0, 22.0], 'dpi': 100},
    'drag': {'figsize': [8.0, 6.0], 'dpi': 100},
    'snap': {'figsize': [8.0, 6.0], 'dpi': 100},
    'qtwin': {'figsize': [6.0, 4.5], 'dpi': 100},
    'hoverkeys': {'figsize': [7.0, 5.0], 'dpi': 100},
    'select': {'figsize': [9.0, 6.5], 'dpi': 100},
    'undoredo': {'figsize': [8.0, 6.0], 'dpi': 100},
    'tkwin': {'figsize': [8.0, 6.0], 'dpi': 100},
}

# fit 场景的 4 个"环境组合"：(QT_SCALE_FACTOR, figure dpi)
#   * QT_SCALE_FACTOR —— 确定性模拟 200% 缩放（任务书点名的杠杆），
#     它改变窗口/屏幕的**逻辑像素**数与 devicePixelRatio；
#   * figure dpi —— mpl 侧的**物理像素**密度（dpi=200 就是用户实机撞 bug 的档）。
#   ratio = after_in / before_in 与两者**都无关**（都是英寸比），所以
#   "4 组 ratio 应当一致"才是真正的不变量（见 B3）：一旦有人把物理像素当逻辑
#   像素用（#1-② 的根因），ratio 就会随 dpi/缩放漂移。
FIT_COMBOS = [(1, 100), (1, 200), (2, 100), (2, 200)]

LOG_LINES = []


def say(msg=''):
    """打印并累积进 --log 的日志。"""
    LOG_LINES.append(msg)
    print(msg, flush=True)


# ---------------------------------------------------------------------------
# 子进程执行（超时杀进程树，避免残留 GUI 进程）
# ---------------------------------------------------------------------------
def _kill_tree(pid):
    try:
        if os.name == 'nt':
            subprocess.run(['taskkill', '/F', '/T', '/PID', str(pid)],
                           capture_output=True, timeout=20)
        else:
            os.kill(pid, 9)
    except Exception:
        pass


def _run_proc(cmd, env, timeout, cwd=None):
    """跑一个子进程，超时**杀整棵树**。

    故意不传 creationflags：子进程继承父进程的控制台（不会额外弹控制台窗口）；
    Qt 的窗口是子进程内部自己创建的，与本函数无关。
    """
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         env=env, cwd=cwd or ROOT)
    timed_out = False
    try:
        raw_out, raw_err = p.communicate(timeout=timeout)
        rc = p.returncode
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill_tree(p.pid)
        try:
            raw_out, raw_err = p.communicate(timeout=15)
        except subprocess.TimeoutExpired:
            raw_out, raw_err = b'', b''
        rc = -9
    dec = lambda b: (b or b'').decode('utf-8', 'replace')      # noqa: E731
    return dec(raw_out), dec(raw_err), rc, timed_out


class Runner:
    """一个后端的场景执行器：成功结果进缓存（同一场景不重复开窗）。"""

    def __init__(self, backend, timeout):
        self.backend = backend
        self.timeout = timeout
        self.cache = {}
        self.spawns = 0

    def get(self, scenario, env=None, cfg=None, force=False):
        env = env or {}
        cfg = cfg or CFG_DEFAULT.get(scenario, {})
        key = (scenario, tuple(sorted(env.items())), json.dumps(cfg, sort_keys=True))
        if not force and key in self.cache:
            return self.cache[key]
        rec = self._spawn(scenario, env, cfg)
        if rec['rc'] == 0 and rec.get('data') and rec['data'].get('error') is None:
            self.cache[key] = rec
        else:
            self.cache.pop(key, None)
        return rec

    def _spawn(self, scenario, env_extra, cfg):
        cmd = [sys.executable, HERE, '--scenario', scenario,
               '--backend', self.backend, '--emit-json', '--cfg', json.dumps(cfg)]
        env = dict(os.environ)
        # 真窗口测试**绝不能**继承 MPLBACKEND=Agg（否则窗口根本不会出现）。
        env.pop('MPLBACKEND', None)
        pp = env.get('PYTHONPATH') or ''
        env['PYTHONPATH'] = SRC + (os.pathsep + pp if pp else '')
        env['MPLTWEAK_LANG'] = 'zh'
        # 子进程输出固定 UTF-8：父进程按 UTF-8 解码（errors='replace'），
        # 这样中文证据（[mpltweak] 日志、traceback）在任何控制台代码页下都读得对。
        env['PYTHONIOENCODING'] = 'utf-8'
        env.update(env_extra)
        t0 = time.time()
        out, err, rc, timed_out = _run_proc(cmd, env, self.timeout)
        rec = {'rc': rc, 'stdout': out, 'stderr': err, 'secs': round(time.time() - t0, 1),
               'scenario': scenario, 'backend': self.backend,
               'env': dict(env_extra), 'cfg': cfg, 'timeout': timed_out, 'data': None}
        for line in out.splitlines():
            if line.startswith(MARK):
                try:
                    rec['data'] = json.loads(line[len(MARK):])
                except ValueError:
                    pass
        self.spawns += 1
        return rec


def _preflight_delivery(backend, timeout):
    """跑一次投递前置探针（独立子进程）→ ``(ok, why, data)``。

    独立进程的理由与所有场景一致：真窗口 + pyplot 会污染进程状态，探针也不例外；
    而且探针**崩了/挂住**时不能把父进程一起带走。
    """
    cfg = dict(CFG_DEFAULT['drag'])
    cmd = [sys.executable, HERE, '--preflight', '--backend', backend,
           '--emit-json', '--cfg', json.dumps(cfg)]
    env = dict(os.environ)
    env.pop('MPLBACKEND', None)
    pp = env.get('PYTHONPATH') or ''
    env['PYTHONPATH'] = SRC + (os.pathsep + pp if pp else '')
    env['MPLTWEAK_LANG'] = 'zh'
    env['PYTHONIOENCODING'] = 'utf-8'
    out, err, rc, timed_out = _run_proc(cmd, env, min(timeout, 180))
    data = None
    for line in out.splitlines():
        if line.startswith(MARK):
            try:
                data = json.loads(line[len(MARK):])
            except ValueError:
                pass
    if timed_out:
        return False, '探针超时（%ss 内没交回结果）—— 本环境起不了真窗口' % min(timeout, 180), data
    if data is None:
        return False, ('探针没交回结果（rc=%s）—— 真窗口环境不可用。尾部输出：%s'
                       % (rc, _tail(err or out, 4))), data
    if data.get('error'):
        return False, ('探针自己报错：%s —— 真窗口环境不可用' % data['error']), data
    if not data.get('delivery'):
        parts = ['本环境投递不到鼠标事件' if int(data.get('motion') or 0) < 1
                 else '本环境投递不到按键事件（鼠标能动）']
        parts.append('实测收到 motion=%s / key=%s（期望各 ≥1）'
                     % (data.get('motion'), data.get('key')))
        if data.get('active') is False:
            parts.append('而且窗口没拿到焦点（isActiveWindow=False）—— '
                         '没有桌面前台/会话时就是这样，事件会被系统丢掉')
        if data.get('forced_undeliverable'):
            parts.append('（这次是 MPLTWEAK_RW_FORCE_UNDELIVERABLE=1 人为制造的环境）')
        return False, '；'.join(parts), data
    return True, '投递正常（motion=%s / key=%s，active=%s）' % (
        data.get('motion'), data.get('key'), data.get('active')), data


def _tail(text, n=12):
    lines = [l for l in (text or '').splitlines() if l.strip()]
    return ' ⏎ '.join(lines[-n:])


def _d(rec):
    return (rec or {}).get('data') or {}


def _log_lines(rec, pat):
    rx = re.compile(pat)
    return [l for l in (rec.get('stdout') or '').splitlines() if rx.search(l)]


# ===========================================================================
# 子进程侧：真窗口驱动（事件投递，不移动真实光标）
# ===========================================================================
# 鼠标移动怎么投递：True = `QApplication.postEvent(QMouseEvent(MouseMove))`，
# False = 0.1.17 及以前的 `QTest.mouseMove`（**本机实测发 0 个 motion 事件**）。
# 留开关只为"修复前后同机对比"与反向验证（tests/test_real_window.py --preflight）。
MOVE_VIA_POST = True


class QtDriver:
    def __init__(self, fig):
        from matplotlib.backends import qt_compat
        self.QtCore = qt_compat.QtCore
        self.QtGui = getattr(qt_compat, 'QtGui', None)      # postEvent 要用 QMouseEvent
        QtWidgets = qt_compat.QtWidgets
        self.qt_api = qt_compat.QT_API
        self._btn_down = False                              # 拖拽中：buttons 要如实
        mod = __import__(self.qt_api + '.QtTest', fromlist=['QTest'])
        self.QTest = mod.QTest
        self.app = (QtWidgets.QApplication.instance()
                    or QtWidgets.QApplication(['mpltweak-realwindow']))
        Qt = self.QtCore.Qt
        self._lbtn = Qt.LeftButton
        self._keys = {
            'f': Qt.Key_F, 'n': Qt.Key_N, 'e': Qt.Key_E, 'z': Qt.Key_Z,
            'y': Qt.Key_Y, 'c': Qt.Key_C, 'g': Qt.Key_G, 's': Qt.Key_S,
            'x': Qt.Key_X, 'plus': Qt.Key_Plus, 'minus': Qt.Key_Minus,
            'equal': Qt.Key_Equal, 'space': Qt.Key_Space, 'escape': Qt.Key_Escape,
            'left': Qt.Key_Left, 'right': Qt.Key_Right,
            'up': Qt.Key_Up, 'down': Qt.Key_Down,
        }

    # ---- 打扰控制：只在**测试进程内**抑制抢焦点（不动 src/）----
    def harden(self, fig):
        try:
            fig.canvas.manager.window.setAttribute(
                self.QtCore.Qt.WA_ShowWithoutActivating, True)
        except Exception:
            pass
        try:
            w = fig.canvas.manager.window
            w.raise_ = lambda *a, **k: None            # 产品 Tweak.__init__ 会直接调它
            w.activateWindow = lambda *a, **k: None    # FigureManagerQT.show() 会调它
        except Exception:
            pass

    def pump(self, ms=120):
        end = time.time() + ms / 1000.0
        while time.time() < end:
            self.app.processEvents()
            time.sleep(0.008)

    def _pt(self, fig, x, y):
        W, H = fig.canvas.get_width_height()
        return self.QtCore.QPoint(int(round(x)), int(round(H - y)))

    def _mods(self, names):
        Qt = self.QtCore.Qt
        m = Qt.NoModifier
        for n in names:
            if n == 'ctrl':
                m |= Qt.ControlModifier
            elif n == 'shift':
                m |= Qt.ShiftModifier
            elif n == 'alt':
                m |= Qt.AltModifier
        return m

    def press(self, fig, x, y, mods=()):
        self._btn_down = True
        self.QTest.mousePress(fig.canvas, self._lbtn, self._mods(mods), self._pt(fig, x, y))

    def _motion_event(self, fig, pt, mods):
        """造一个 MouseMove 事件（Qt5 / Qt6 构造签名不同，逐档降级，不猜）。"""
        Qt, QtCore, QtGui = self.QtCore.Qt, self.QtCore, self.QtGui
        btn = Qt.LeftButton if self._btn_down else Qt.NoButton
        glob = self.QtCore.QPointF(self.window(fig).mapToGlobal(pt))
        local = self.QtCore.QPointF(pt)
        try:
            return QtGui.QMouseEvent(QtCore.QEvent.MouseMove, local, glob,
                                     btn, btn, self._mods(mods))
        except TypeError:
            pass
        # Qt6/PySide6：多一个 source 参数；再不行退回 QPoint 版
        try:
            return QtGui.QMouseEvent(QtCore.QEvent.MouseMove, local, glob,
                                     btn, btn, self._mods(mods),
                                     QtGui.QMouseEvent.MouseEventSource)
        except (TypeError, AttributeError):
            pass
        return QtGui.QMouseEvent(QtCore.QEvent.MouseMove, pt, pt, btn, btn,
                                 self._mods(mods))

    def move(self, fig, x, y, mods=()):
        """投递一次鼠标移动。

        **为什么不用 `QTest.mouseMove`**（0.1.18 本机实测，冻结 HEAD 树）：
        它在这台机 / 这个 Qt 版本上发出 **0 个 motion 事件**（产品收不到 ⇒ 拖拽、
        框选、吸附、撤销全部假红，整套真窗口套件 90 分钟里 7 条 FAIL 有 5 条出自这里）；
        改走 `QApplication.postEvent(canvas, QMouseEvent(MouseMove, …))` 能把驱动发出的
        每一次移动都送到（`drag` 场景 8 步插值 + 2 次落点重复 = **10 发 10 收**），
        位移精确到 −0.0800 / −0.1100（请求 −0.08 / −0.11）。前后对比见
        `_team014/rw_delivery.md`（含探针只发 2 次时 `motion=2` 的对照读数）。

        注意：`postEvent` 与 `QTest` 一样**不移动系统光标**（内部只走 Qt 事件队列），
        所以"不劫持真实鼠标"的约定不变。
        """
        pt = self._pt(fig, x, y)
        if not MOVE_VIA_POST:
            self.QTest.mouseMove(fig.canvas, pt)     # 旧路（只用于前后对比/反向验证）
            return
        self.app.postEvent(fig.canvas, self._motion_event(fig, pt, mods))

    def release(self, fig, x, y, mods=()):
        self._btn_down = False
        self.QTest.mouseRelease(fig.canvas, self._lbtn, self._mods(mods), self._pt(fig, x, y))

    def key(self, fig, combo):
        parts = combo.split('+')
        self.QTest.keyClick(fig.canvas, self._keys[parts[-1]], self._mods(parts[:-1]))

    def window(self, fig):
        return fig.canvas.manager.window

    def frame(self, fig):
        fg = self.window(fig).frameGeometry()
        return [fg.x(), fg.y(), fg.width(), fg.height()]

    def screen_of(self, fig):
        s = self.window(fig).screen()
        sz, g = s.size(), s.availableGeometry()
        return [sz.width(), sz.height()], [g.width(), g.height()]

    def active(self, fig):
        return bool(self.window(fig).isActiveWindow())


class TkDriver:
    def __init__(self, fig):
        import tkinter
        self.tk = tkinter
        self.widget = fig.canvas.get_tk_widget()
        self.root = self.widget.winfo_toplevel()
        self.qt_api = None

    def harden(self, fig):
        # Tk 没有 Qt 的 WA_ShowWithoutActivating；测试侧只保证**不**主动
        # lift()/focus_force()。（产品的 w.raise_() 在 toolbox.py:1357 的
        # try/except 里，Tk 上静默跳过。）
        try:
            self.root.wm_attributes('-topmost', False)
        except Exception:
            pass

    def show_window(self, fig):
        """把 Tk 窗口**映射**出来（= 用户在脚本里 ``plt.show()`` 的效果）。

        为什么必须由测试显式做（首跑 C7 FAIL 的根因，mapped=False）：
          * 产品的 ``Tweak.__init__`` 靠 ``w.show()`` 开窗，而 **Tk 的 toplevel 没有
            ``show()``** → 那一行在 ``toolbox.py:1357-1361`` 的 try/except 里被静默跳过；
          * 产品真正的入口 ``launch.py:8`` **固定用 QtAgg**（``pyproject.toml`` 的依赖也
            只写 ``qt``），所以 Tk 路径只有本测试会走 ⇒ "把窗口显示出来"是**测试的责任**，
            不是产品缺陷。真实 Tk 用户在脚本里调用 ``plt.show()`` 才映射窗口。
        只 deiconify（不 lift / 不 focus_force），尽量不抢焦点。
        """
        try:
            self.root.deiconify()
        except Exception:
            pass
        try:
            self.root.update_idletasks()
            self.root.update()
        except Exception:
            pass

    def pump(self, ms=120):
        end = time.time() + ms / 1000.0
        while time.time() < end:
            try:
                self.root.update()
            except self.tk.TclError:
                break
            time.sleep(0.008)

    def _emit(self, ev, **kw):
        try:
            self.widget.event_generate(ev, when='now', **kw)
        except self.tk.TclError:
            self.widget.event_generate(ev, when='tail', **kw)
            self.root.update()

    def _state(self, names):
        # Tk 在 win32 上：Shift=1, Control=4（与产品 _mpl_modifiers 口径一致）
        s = 0
        for n in names:
            if n == 'shift':
                s |= 1
            elif n == 'ctrl':
                s |= 4
            elif n == 'alt':
                s |= 8
        return s

    def _pt(self, fig, x, y):
        """mpl 显示坐标（左下原点，**逻辑** px）→ Tk 控件坐标（左上原点，**物理** px）。

        ★ 两个后端的单位口径不同（首跑 C7 实测出来的一课）：
          * **Qt**：控件坐标 = 逻辑 px，matplotlib 的 ``mouseEventCoords`` 自己会乘
            ``device_pixel_ratio`` ⇒ 传逻辑 px 是对的（qt 侧实测 |Δ|=0.000）；
          * **Tk**：控件坐标 = **物理** px（本机 Tk scaling=2.0，
            ``canvas.device_pixel_ratio=2.0``），而 ``get_width_height()`` 返回的是
            **逻辑** px ⇒ 必须自己乘 dpr，否则位移会**恰好差一半**
            （首跑实测：请求 (-0.060,-0.080) → 实得 (-0.030,-0.040) = 0.5×）。
        """
        W, H = fig.canvas.get_width_height()
        s = float(getattr(fig.canvas, 'device_pixel_ratio', 1.0) or 1.0)
        return int(round(x * s)), int(round((H - y) * s))

    def press(self, fig, x, y, mods=()):
        px, py = self._pt(fig, x, y)
        self._emit('<Button-1>', x=px, y=py, state=self._state(mods))

    def move(self, fig, x, y, mods=()):
        px, py = self._pt(fig, x, y)
        self._emit('<Motion>', x=px, y=py, state=self._state(mods))

    def release(self, fig, x, y, mods=()):
        px, py = self._pt(fig, x, y)
        self._emit('<ButtonRelease-1>', x=px, y=py, state=self._state(mods))

    def key(self, fig, combo):
        parts = combo.split('+')
        ks, st = parts[-1], self._state(parts[:-1])
        self._emit('<KeyPress>', keysym=ks, state=st)
        self._emit('<KeyRelease>', keysym=ks, state=st)

    def tk_geom(self, fig):
        """Tk 窗口的客户区几何（**物理** px）+ 映射状态。

        Qt 侧判断"窗口是否被 WM 钳制"用 ``frameGeometry()``；Tk 没有那个 API，
        对应的就是 ``winfo_rootx/rooty/width/height`` + ``winfo_ismapped/viewable``。
        """
        try:
            r = self.root
            return {'xy': [int(r.winfo_rootx()), int(r.winfo_rooty())],
                    'wh': [int(r.winfo_width()), int(r.winfo_height())],
                    'mapped': bool(r.winfo_ismapped()),
                    'viewable': bool(r.winfo_viewable()),
                    'screen': [int(r.winfo_screenwidth()),
                               int(r.winfo_screenheight())]}
        except Exception as e:
            return {'err': str(e)}

    def window(self, fig):
        return self.root

    def frame(self, fig):
        return None                      # Tk：不提供 frameGeometry，C1 只跑 Qt

    def screen_of(self, fig):
        wg = self.widget
        return ([wg.winfo_screenwidth(), wg.winfo_screenheight()],
                [wg.winfo_screenwidth(), wg.winfo_screenheight()])

    def active(self, fig):
        return None


# ===========================================================================
# 子进程侧：场景夹具与测量
# ===========================================================================
_TITLE_CN = '主标题：HiDPI 裁白边回归（中文）'
_TITLE_EN = 'Main Title: HiDPI fit regression'
_ACTIVE = {}


def _remember(fig, drv):
    _ACTIVE['fig'] = fig
    _ACTIVE['drv'] = drv
    return drv


def _pick_title(mpl):
    """优先用真实中文字体（用户场景就是中文标题），没有就退英文。"""
    try:
        from matplotlib import font_manager
        names = {f.name for f in font_manager.fontManager.ttflist}
        for cand in ('Microsoft YaHei', 'SimHei', 'SimSun'):
            if cand in names:
                mpl.rcParams['font.sans-serif'] = [cand, 'DejaVu Sans']
                mpl.rcParams['axes.unicode_minus'] = False
                return _TITLE_CN
    except Exception:
        pass
    return _TITLE_EN


def _new_fig(ns, mpl, figsize=None):
    cfg = ns.cfg
    return mpl.pyplot.figure(figsize=tuple(figsize or cfg['figsize']),
                             dpi=float(cfg['dpi']))


def _driver(ns, fig):
    """Qt 驱动与图无关（只要 QApplication）；Tk 驱动必须有 canvas。"""
    return _remember(fig, QtDriver(fig) if ns.backend == 'qt' else TkDriver(fig))


def _add_panels(fig, boxes, legend=True):
    axs = []
    for i, b in enumerate(boxes):
        ax = fig.add_axes(b)
        xs = [0, 1, 2, 3, 4]
        ax.plot(xs, [v * (i + 1) for v in xs], label='s%d' % i)
        ax.set_title('P%d' % (i + 1), fontsize=9)
        if legend:
            ax.legend(loc='upper right', fontsize=6)
        ax._rw_tag = 'ax%d' % i
        axs.append(ax)
    return axs


def _title_box(fig):
    """主标题外框 [x0,y0,x1,y1]，显示坐标（左下原点 px，与 fig.bbox 同空间）。"""
    st = getattr(fig, '_suptitle', None)
    if st is None:
        return None
    try:
        bb = st.get_window_extent(fig.canvas.get_renderer())
    except Exception:
        try:
            bb = st.get_window_extent()
        except Exception:
            return None
    return [float(bb.x0), float(bb.y0), float(bb.x1), float(bb.y1)]


def _geom(fig, bbox):
    g = []
    for ax in fig.axes:
        g.extend([round(float(v), 6) for v in bbox(ax)])
    return g


def _content_count(ax):
    return (len(ax.lines) + len(ax.collections) + len(ax.patches)
            + len(ax.images) + len(ax.texts))


def _drag(drv, fig, x0, y0, x1, y1, steps=8, mods=(), settle=2, pump_scale=1.0):
    """真拖拽：steps 段插值 + settle 次「落点重复」（真实用户是连续上百个 motion）。

    settle 是 C8 首跑失败后的修正：首跑只用 5 步插值，实测位移**恰好是请求的 4/5**
    （少一步），这种情况无法区分"引擎吞了最后一步/松手"与"我的驱动太粗糙"。
    真实用户不会只发 5 个 motion，所以按真实节奏补足：段数加密 + 松手前在同一落点再投
    两次。**这不是放宽断言**——C8 判据一个字没改，只是把输入做得更像真人。

    ``pump_scale``（t22 多图形扩展新增，默认 1.0 = 既有口径）只缩放**每步等待**，
    不改 motion 段数/落点/事件类型。5 种新图形的 A 层 fuzz 用 0.25（见文件头
    「多图形扩展」）；B/C 层与 monkey 场景仍是 1.0。
    """
    drv.press(fig, x0, y0, mods)
    drv.pump(20 * pump_scale)
    for i in range(1, steps + 1):
        drv.move(fig, x0 + (x1 - x0) * i / float(steps),
                 y0 + (y1 - y0) * i / float(steps), mods)
        drv.pump(12 * pump_scale)
    for _ in range(max(0, settle)):
        drv.move(fig, x1, y1, mods)
        drv.pump(70 * pump_scale)
    drv.release(fig, x1, y1, mods)
    drv.pump(80 * pump_scale)


def _instrument(fig, Tweak):
    """测试进程内插桩（**不改 src/**）：把 C1 证据链从"推断"变成"实测"。

      * 记每一次 resizeEvent 的窗口客户区尺寸 / device_pixel_ratio / 当时的 figsize
        （backend_qt.py:389-401 那段就是"钳制即改 figsize"的源头）；
      * 记 `_fit_to_screen()` 前后各一次的 figsize。
    只在本子进程内 patch，进程结束即消失（与扰控的 monkeypatch 同一原则）。
    """
    tr = {'resize': [], 'resize_n': 0, 'fit': []}
    try:
        canvas = fig.canvas
        orig_re = canvas.resizeEvent

        def _wrapped(ev):
            tr['resize_n'] += 1
            if len(tr['resize']) < 6:
                try:
                    tr['resize'].append({
                        'wh': [int(ev.size().width()), int(ev.size().height())],
                        'dpr': round(float(canvas.device_pixel_ratio), 3),
                        'inches': [round(float(v), 4) for v in fig.get_size_inches()]})
                except Exception:
                    pass
            return orig_re(ev)

        canvas.resizeEvent = _wrapped
    except Exception:
        pass
    try:
        orig_fit = Tweak._fit_to_screen

        def _fit(self):
            before = [round(float(v), 4) for v in self.fig.get_size_inches()]
            r = orig_fit(self)
            after = [round(float(v), 4) for v in self.fig.get_size_inches()]
            tr['fit'].append({'before': before, 'after': after})
            return r

        Tweak._fit_to_screen = _fit
    except Exception:
        pass
    return tr


def _in_axes(fig, bbox, px, py, W, H):
    for ax in fig.axes:
        b = bbox(ax)
        if (b[0] * W <= px <= (b[0] + b[2]) * W
                and b[1] * H <= py <= (b[1] + b[3]) * H):
            return True
    return False


def _find_blank(fig, bbox, hit_handle, W, H, rng, tries=30):
    """找一个「空白点」（不在任何面板里、也不是拖拽手柄）。

    框选起手**必须**落在空白处（``toolbox.py:2710-2713`` 的 ``if event.inaxes is None``），
    否则会被当成"点选面板"而不是"起框"。

    ⚠️ **单位口径（t22 修正）**：``_hit_handle`` 内部用 ``fig.transFigure`` / ``ax.bbox``，
    即 **显示坐标**（Qt 侧 = 逻辑 px × ``device_pixel_ratio``；本机 200% 缩放 ⇒ ×2），
    而本套件注入的是**逻辑 px**。所以"引擎会不会把这点当手柄"必须用**显示 px** 问；
    注入点本身仍是逻辑 px（驱动负责映回控件坐标）。
    修正前（只传逻辑 px）会把抓取区缩小 dpr 倍 ⇒ 探针比引擎**更宽松**，可能给出
    "引擎其实当成拉角"的点（框选退化成缩放）。这只影响 harness 的选点，不改任何判据。
    """
    dpr = float(getattr(fig.canvas, 'device_pixel_ratio', 1.0) or 1.0)
    for _ in range(tries):
        px, py = rng.uniform(0.02, 0.98) * W, rng.uniform(0.02, 0.98) * H
        hit_ax, _h = hit_handle(fig, px * dpr, py * dpr)
        if hit_ax is None and not _in_axes(fig, bbox, px, py, W, H):
            return (px, py)
    return None


def _one_gesture(drv, ctx, fig, tw, rng, i):
    """一次随机手势（A 层）。"""
    bbox, _hit_handle = ctx['_bbox'], ctx['_hit_handle']
    W, H = fig.canvas.get_width_height()
    if W <= 2 or H <= 2 or not fig.axes:
        return
    dpr_now = float(getattr(fig.canvas, 'device_pixel_ratio', 1.0) or 1.0)
    kinds = ['drag', 'drag', 'resize', 'resize', 'legend', 'band',
             'clickmod', 'key', 'key', 'arrows']
    kind = kinds[i % len(kinds)]
    ps = float(ctx.get('pump_scale', 1.0) or 1.0)     # 等待倍率（判据无关，见 _drag）
    ctx['last_kind'] = kind
    ctx['last_key'] = ''            # ← 每次手势先清空，只有 key 类手势会写它（防重复计数）
    ax = rng.choice(fig.axes)
    b = bbox(ax)
    cx, cy = (b[0] + b[2] / 2.0) * W, (b[1] + b[3] / 2.0) * H

    if kind == 'drag':
        dx, dy = rng.uniform(-0.10, 0.10) * W, rng.uniform(-0.10, 0.10) * H
        _drag(drv, fig, cx, cy, cx + dx, cy + dy, pump_scale=ps)
        return

    if kind == 'resize':
        _, nx, ny = rng.choice([('tl', b[0], b[1] + b[3]), ('tr', b[0] + b[2], b[1] + b[3]),
                                ('bl', b[0], b[1]), ('br', b[0] + b[2], b[1])])
        px, py = nx * W, ny * H
        # ⚠️ 单位口径（t22 修正）：`_hit_handle` 用**显示坐标**判抓取区，而这里注入的是
        # **逻辑 px**（驱动负责映回控件坐标）⇒ 问"引擎会不会抓到角/边"必须乘 dpr。
        # 修正前只传逻辑 px ⇒ 抓取区被缩小 dpr 倍 ⇒ 绝大多数拉角手势被自己跳过
        # （"没抓到手柄"），A 层其实**几乎没测到真缩放**。修正后注入点一字未变。
        hit_ax, _h = _hit_handle(fig, px * float(dpr_now), py * float(dpr_now))
        if hit_ax is None:
            return                      # 没抓到手柄 → 跳过这次
        dx, dy = rng.uniform(-0.07, 0.07) * W, rng.uniform(-0.07, 0.07) * H
        _drag(drv, fig, px, py, px + dx, py + dy, pump_scale=ps)
        return

    if kind == 'legend':
        leg = ax.get_legend()
        if leg is None:
            return
        try:
            e = leg.get_window_extent(fig.canvas.get_renderer())
        except Exception:
            return
        px, py = (e.x0 + e.x1) / 2.0, (e.y0 + e.y1) / 2.0
        got_ax, _l = tw._legend_at(px, py)
        if got_ax is None:
            return
        dx, dy = rng.uniform(-0.05, 0.05) * W, rng.uniform(-0.05, 0.05) * H
        _drag(drv, fig, px, py, px + dx, py + dy, pump_scale=ps)
        return

    if kind == 'band':
        # ★ 首跑这里只画 dx,dy∈[-0.15,0.15] 的小框，而面板是 0.38×0.34
        #   ⇒ **数学上永远包不住**（引擎判据是完全框住：toolbox.py:2497-2499）
        #   ⇒ 框选路径等于没测（用户 2026-10-12 看到的正是这个现象）。
        # 现在四态都可达：enclose1 / enclose2（完整包住 1 或 2 个面板）/
        # blank（框在空白带里，数学上不可能包住任何面板）/ small（小框 = 视为点击）。
        mode = rng.choice(['enclose1', 'enclose1', 'enclose2', 'blank', 'small'])
        ctx['last_kind'] = 'band:' + mode
        if mode == 'small':
            spot = _find_blank(fig, bbox, _hit_handle, W, H, rng)
            if spot is None:
                return
            dx, dy = rng.uniform(-0.03, 0.03) * W, rng.uniform(-0.03, 0.03) * H
            _drag(drv, fig, spot[0], spot[1], spot[0] + dx, spot[1] + dy, steps=4,
                  pump_scale=ps)
            return
        if mode == 'blank':
            # 空白带：两列之间（x≈0.46~0.53）或两行之间（y≈0.44~0.53）
            if rng.random() < 0.5:
                x0 = rng.uniform(0.46, 0.53)
                y0 = rng.uniform(0.05, 0.44)
                x1 = x0 + rng.uniform(0.02, 0.07)
                y1 = y0 + rng.uniform(0.08, 0.28)
            else:
                y0 = rng.uniform(0.44, 0.53)
                x0 = rng.uniform(0.05, 0.44)
                x1 = x0 + rng.uniform(0.08, 0.28)
                y1 = y0 + rng.uniform(0.02, 0.07)
            _drag(drv, fig, x0 * W, y0 * H, x1 * W, y1 * H, steps=6, pump_scale=ps)
            return
        # enclose1 / enclose2：取目标面板的并集外框，外扩 m 后从其中一个角拖到对角
        n_ax = 1 if mode == 'enclose1' else 2
        pick = rng.sample(range(len(fig.axes)), min(n_ax, len(fig.axes)))
        xs0 = min(bbox(fig.axes[i])[0] for i in pick)
        ys0 = min(bbox(fig.axes[i])[1] for i in pick)
        xs1 = max(bbox(fig.axes[i])[0] + bbox(fig.axes[i])[2] for i in pick)
        ys1 = max(bbox(fig.axes[i])[1] + bbox(fig.axes[i])[3] for i in pick)
        m = rng.uniform(0.02, 0.05)
        corners = [(xs0 - m, ys0 - m), (xs1 + m, ys1 + m),
                   (xs1 + m, ys0 - m), (xs0 - m, ys1 + m)]
        order = [0, 1, 2, 3]
        rng.shuffle(order)
        pick_c = None
        for idx in order:
            sx, sy = corners[idx]
            if not (0.0 <= sx <= 1.0 and 0.0 <= sy <= 1.0):
                continue
            hit_ax, _h = _hit_handle(fig, sx * W * dpr_now, sy * H * dpr_now)
            if hit_ax is None and not _in_axes(fig, bbox, sx * W, sy * H, W, H):
                pick_c = idx
                break
        if pick_c is None:
            return                       # 四个角都被别的面板占了 → 跳过这次
        sx, sy = corners[pick_c]
        ox, oy = corners[pick_c ^ 1]     # 对角点：保证框住整个 union
        _drag(drv, fig, sx * W, sy * H, ox * W, oy * H, steps=8, pump_scale=ps)
        return

    if kind == 'clickmod':
        mods = ('ctrl',) if rng.random() < 0.5 else ('shift',)
        drv.press(fig, cx, cy, mods)
        drv.pump(20 * ps)
        drv.release(fig, cx, cy, mods)
        drv.pump(20 * ps)
        return

    if kind == 'arrows':
        drv.key(fig, rng.choice(['left', 'right', 'up', 'down']))
        return

    # key：先动一下鼠标给 hover 定位，再按键
    drv.move(fig, rng.uniform(0.05, 0.95) * W, rng.uniform(0.05, 0.95) * H)
    drv.pump(20 * ps)
    _k = rng.choice(['ctrl+f', 'f', 'n', 'ctrl+z', 'ctrl+y', 'e', 'space'])
    ctx['last_key'] = _k            # ← (a) 供 A 层统计"实际发出过多少次撤销/重做"
    drv.key(fig, _k)


# ---------------------------------------------------------------------------
# 多图形扩展（t22）：6 类图形 × 1000 次随机手势 = 6000 次（A 层两条铁律一字不改）
# ---------------------------------------------------------------------------
# 用户 2026-10-12 要求「换各种不同的图测试一下，增加可靠性」。这里把 A 层从"一种图"
# 扩到 6 类，并给 5 种新图形各加 2 条 C 层断言（C11~C20；判据与理由见文件头同名一节）。
TOUCH_TOL = 0.002        # "几何没被动到"的容差（figure 比例；与 C8/C10 同量级）
CTRLF_LO, CTRLF_HI = 0.80, 1.20   # Ctrl+F 每维 ratio 的"不缩半"区间（与 B1 同一口径）


def _one_line_log(log):
    """把引擎原文行压成一行证据（**逐字保留**，不改写、不摘要）。"""
    lines = [str(l).strip() for l in (log or []) if str(l).strip()]
    return ' ⏎ '.join(lines) if lines else '（引擎无 fit 行）'


def _ctrlf_branch_ok(rc, want_n):
    """**C18 的判据本体**（模块级：这样"定向复验"能调用**同一段判据代码**，不靠重写一遍）。

    只用于 constrained_layout 那一张图。为什么不能用通用的"必须生效"：
    constrained_layout 把轴排到几乎没有白边，引擎 `_fit_to_content()` 会走
    `toolbox.py:2352-2355` 的**设计内分支**（`if NW_in >= fiw and NH_in >= fih:
    内容已贴边，无需适配`）⇒ `ratio == 1.000`、`ran == False` 是**正确行为**。
    （2026-10-12 首轮真跑就是按"必须生效"判它 FAIL，事后定位为**测试侧判据 bug**：
    离线用同一 fixture 直接调引擎方法，引擎原文 = "内容已贴边，无需适配"，
    且同一探针在 gridspec 上复现出与真窗口逐位相同的 0.834/0.943。）

    **非空虚性守卫（比"必须生效"更严，不是放宽）**：必须满足其一 ——
      (a) 真的裁了（ratio 偏离 1 超过 0.005）；或
      (b) 引擎明确回报 "收到快捷键" **且** "内容已贴边，无需适配"
          （按键若丢，这两行都不会出现 ⇒ FAIL）。
    ratio 区间 [CTRLF_LO, CTRLF_HI] 与面板数判据**一字未改**。
    返回 ``(ok, detail)``。
    """
    rc = rc or {}
    b = rc.get('before') or [0.0, 0.0]
    a = rc.get('after') or [0.0, 0.0]
    r = rc.get('ratio') or [0.0, 0.0]
    log = rc.get('fit_log') or []
    got_key = any('收到快捷键' in str(l) for l in log)
    tight = any('内容已贴边，无需适配' in str(l) for l in log)
    ran = bool(rc.get('ran'))
    ok = (CTRLF_LO <= r[0] <= CTRLF_HI and CTRLF_LO <= r[1] <= CTRLF_HI
          and rc.get('n_axes_after_cf') == want_n
          and (ran or (got_key and tight)))
    det = ('Ctrl+F：%.2f×%.2fin → %.2f×%.2fin（ratio=%.3f / %.3f，判据 [%.2f,%.2f] 与 B1 '
           '同一口径）；确实生效=%s；引擎收到按键=%s；引擎判"内容已贴边、无需适配"=%s；'
           '面板数 %s → %s；引擎原文=%s'
           % (b[0], b[1], a[0], a[1], r[0], r[1], CTRLF_LO, CTRLF_HI, ran,
              got_key, tight, want_n, rc.get('n_axes_after_cf'), _one_line_log(log)))
    return ok, det


def _decorate(ax, i, legend=True):
    """给已有 Axes 加内容/标题/图例/测试侧 tag（与 ``_add_panels`` 同口径）。"""
    xs = [0, 1, 2, 3, 4]
    ax.plot(xs, [v * (i + 1) for v in xs], label='s%d' % i)
    ax.set_title('P%d' % (i + 1), fontsize=9)
    if legend:
        try:
            ax.legend(loc='upper right', fontsize=6)
        except Exception:
            pass
    ax._rw_tag = 'ax%d' % i
    return ax


def _fig_fixture(ns, mpl, kind):
    """按 kind 造"各种不同的图"，返回 (fig, meta)。

    **全部用用户真实写法造图**（subplots / add_gridspec / colorbar(cax) /
    layout='constrained' / tight_layout），不用 add_axes 手摆位置来"喂"引擎。
    meta 只描述 fixture 结构（面板数 / tag / gridspec 对象 / 跨行跨列），不含判据。
    """
    cfg = ns.cfg
    fs = tuple(cfg.get('figsize', [9.0, 6.5]))
    dpi = float(cfg.get('dpi', 100))
    title = _pick_title(mpl)
    meta = {'kind': kind, 'figsize_req': [float(v) for v in fs], 'dpi': dpi}
    if kind == 'sub2x2':                      # 既有 monkey 场景（版面一字未改）
        fig = _new_fig(ns, mpl, fs)
        _add_panels(fig, [[0.08, 0.55, 0.38, 0.34], [0.56, 0.55, 0.38, 0.34],
                          [0.08, 0.09, 0.38, 0.34], [0.56, 0.09, 0.38, 0.34]])
        fig.suptitle(title, fontsize=12)
    elif kind == 'grid4x4':                   # 4×4 = 16 面板
        fig = _new_fig(ns, mpl, fs)
        axs = fig.subplots(4, 4)
        for i, ax in enumerate([a for row in axs for a in row]):
            _decorate(ax, i)
        fig.suptitle(title, fontsize=11)
        meta['grid'] = [4, 4]
    elif kind == 'gridspec':                  # 3×3 跨行跨列 + 非等分比例
        fig = _new_fig(ns, mpl, fs)
        gs = fig.add_gridspec(3, 3, height_ratios=[2.0, 1.0, 1.0],
                              width_ratios=[1.0, 2.0, 1.0])
        spans = [(0, 0, 0, 2), (1, 0, 1, 1), (1, 2, 1, 2), (2, 0, 2, 2)]
        for i, (r0, c0, r1, c1) in enumerate(spans):
            _decorate(fig.add_subplot(gs[r0:r1 + 1, c0:c1 + 1]), i)
        fig.suptitle(title, fontsize=11)
        meta['gs'] = gs
        meta['spans'] = spans
        meta['gridspec'] = ('height_ratios=[2,1,1] width_ratios=[1,2,1]；'
                            '4 块：顶整行 / 中 2 列 / 中 1 列 / 底整行')
    elif kind == 'colorbar':                  # 主面板 + 独立 colorbar 轴
        fig = _new_fig(ns, mpl, fs)
        ax = fig.add_axes([0.08, 0.12, 0.62, 0.76])
        _decorate(ax, 0, legend=False)
        im = ax.imshow([[float((i * j) % 7) for j in range(12)] for i in range(9)],
                       aspect='auto')
        cax = fig.add_axes([0.78, 0.12, 0.06, 0.76])
        fig.colorbar(im, cax=cax)
        cax._rw_tag = 'cb0'
        fig.suptitle(title, fontsize=11)
        meta['main_tag'] = 'ax0'
        meta['cbar_tag'] = 'cb0'
    elif kind == 'clayout':                   # constrained_layout
        fig = mpl.pyplot.figure(figsize=fs, dpi=dpi, layout='constrained')
        axs = fig.subplots(2, 2)
        for i, ax in enumerate([a for row in axs for a in row]):
            _decorate(ax, i)
        fig.suptitle(title, fontsize=11)
        meta['layout'] = 'constrained'
    elif kind == 'tight1x3':                  # 1×3 横排 + tight_layout + 长标题
        fig = _new_fig(ns, mpl, fs)
        axs = fig.subplots(1, 3)
        for i, ax in enumerate(axs):
            _decorate(ax, i)
        fig.tight_layout()
        if title == _TITLE_CN:
            long_title = (title + '（1×3 横排 + tight_layout：故意写一个很长很长的中文主'
                                 '标题，用来验证 Ctrl+F 之后标题不会被裁掉）')
        else:
            long_title = title + ' (1x3 tight_layout, deliberately very long title)'
        fig.suptitle(long_title, fontsize=11)
        meta['nrow'] = 1
        meta['long_title'] = long_title
    else:
        raise ValueError('未知图形 kind=%r' % (kind,))
    return fig, meta


def _tag_of(fig, ax):
    return getattr(ax, '_rw_tag', 'ax%d' % fig.axes.index(ax))


def _bbox_map(fig, bbox):
    return {_tag_of(fig, ax): [float(v) for v in bbox(ax)] for ax in fig.axes}


def _changed(old, new, tol):
    """返回 (变化的面板 tag 列表, 最大逐维偏差)；tol 以内视为"没动"。"""
    ch, dev = [], 0.0
    for tag, b in sorted(new.items()):
        o = old.get(tag)
        if o is None or len(o) != 4 or len(b) != 4:
            ch.append(tag)
            continue
        d = max(abs(b[i] - o[i]) for i in range(4))
        dev = max(dev, d)
        if d > tol:
            ch.append(tag)
    return ch, dev


def _open_window(ns, mpl, ctx, tmp, fig, tag):
    """建驱动 + 构造 Tweak（真开窗）+ 首帧事件泵；返回 (drv, tw)。"""
    ctx['pump_scale'] = float(ns.cfg.get('pump_scale', 1.0) or 1.0)
    drv = _driver(ns, fig)
    drv.harden(fig)
    tw = ctx['Tweak'](fig, export_path=os.path.join(tmp, tag + '.json'),
                      heavy=False, fig_index=0, n_figs=1)
    drv.pump(400)
    ctx['drv'], ctx['tw'] = drv, tw
    return drv, tw


def _fuzz_loop(ns, mpl, ctx, fig, tag):
    """A 层通用乱按循环：monkey 与 5 种新图形共用（判据/指标口径完全一致）。"""
    cfg = ns.cfg
    drv, tw = ctx['drv'], ctx['tw']
    tags_before = [getattr(a, '_rw_tag', '?') for a in fig.axes]
    errors, landed, sel_landed = [], 0, 0
    band_stat = {'big': 0, 'hit': 0, 'small': 0, 'blank': 0}
    undo_press = redo_press = 0          # (a) 撤销/重做按键**次数**（一致性由 C10 判定）
    active_n = active_samples = 0        # ★ 反污染：窗口被激活的采样数（用户点了窗口）
    ctx['band_stat'] = band_stat
    ctx['last_kind'] = ''
    ctx['last_key'] = ''
    n = int(cfg.get('n', 200))
    rng = random.Random(int(cfg.get('seed', 20261012)))
    t0 = time.time()
    for i in range(n):
        gb = _geom(fig, ctx['_bbox'])
        sb = len(tw._selected)
        if i % 10 == 0:
            # ★ 反污染采样：正常情况下测试进程的扰控（WA_ShowWithoutActivating 等）
            #   保证窗口**不应被激活**；一旦 isActiveWindow=True，极可能是用户点了窗口
            #   → 这轮读数可能被外部交互污染，必须标出来（数字表只用干净运行）。
            active_samples += 1
            try:
                if drv.active(fig):
                    active_n += 1
            except Exception:
                pass
        try:
            _one_gesture(drv, ctx, fig, tw, rng, i)
        except BaseException as e:          # 手势层兜底：记下来但不中断整轮
            errors.append('%s: %s' % (type(e).__name__, e))
            continue
        drv.pump(15)
        if _geom(fig, ctx['_bbox']) != gb:
            landed += 1
        # ② 选中态变化 = "事件真的落到了引擎的选择逻辑"。**与几何变化是两个独立指标**：
        #    框选/点选本身不改几何，只看几何会把它们全记成"没落地"——首跑就是这么误读的。
        sel_changed = (len(tw._selected) != sb)
        if sel_changed:
            sel_landed += 1
        # ③ 框选专项统计：大框（完整包住 1~2 个）到底有没有真的选中？——这是用户
        #    2026-10-12 目击出的盲区，必须在日志里**可见**（软指标，不判失败）。
        # (a) 撤销/重做按键计数：只统计"本轮真的发出去了多少次"
        _lk2 = ctx.get('last_key') or ''
        if _lk2.startswith('ctrl+z'):
            undo_press += 1
        elif _lk2.startswith('ctrl+y'):
            redo_press += 1
        lk = ctx.get('last_kind') or ''
        if lk.startswith('band:'):
            if lk.endswith('small'):
                band_stat['small'] += 1
            elif lk.endswith('blank'):
                band_stat['blank'] += 1
            else:
                band_stat['big'] += 1
                if len(tw._selected) >= 1:
                    band_stat['hit'] += 1
    drv.pump(400)
    return {
        'n': n, 'errors': errors[:20], 'n_errors': len(errors),
        'kind': tag, 'pump_scale': float(ctx.get('pump_scale', 1.0) or 1.0),
        'secs_gestures': round(time.time() - t0, 1),
        'landed': landed, 'landed_ratio': (landed / float(n)) if n else 0.0,
        'sel_landed': sel_landed,
        'sel_landed_ratio': (sel_landed / float(n)) if n else 0.0,
        'band_big': band_stat['big'], 'band_hit': band_stat['hit'],
        'band_small': band_stat['small'], 'band_blank': band_stat['blank'],
        'undo_presses': undo_press, 'redo_presses': redo_press,
        'active_n': active_n, 'active_samples': active_samples,
        'suspected': bool(active_n),
        'tags_before': tags_before,
        'tags_after': [getattr(a, '_rw_tag', '?') for a in fig.axes],
        'geom_after': _geom(fig, ctx['_bbox']),
        'content_counts': [_content_count(a) for a in fig.axes],
        'canvas': list(fig.canvas.get_width_height()),
        'dpr': float(getattr(fig.canvas, 'device_pixel_ratio', 1.0) or 1.0),
        'min_size': float(ctx['MIN_SIZE']),
        'backend': mpl.get_backend(),
    }


def _sc_monkey(ns, mpl, ctx, tmp):
    """A 层：2×2（既有图形）真窗口随机手势 N 次。"""
    fig, _meta = _fig_fixture(ns, mpl, 'sub2x2')
    _open_window(ns, mpl, ctx, tmp, fig, 'sub2x2')
    return _fuzz_loop(ns, mpl, ctx, fig, 'sub2x2')


def _fig_open_checks(ns, mpl, ctx, fig, kind, meta):
    """开窗后、乱按**前**的确定性读数（C11/C12/C13/C15/C16/C17/C19 的读数来源）。

    刻意放在 fuzz **之前**：这批判据问的是"引擎在**版式定义清楚的新版面**上怎么处理一次
    确定手势"（拖 1 个面板只动它 / 框选完整包住 4 个就恰好选中 4 个 / 拖 colorbar 不动主
    面板）。若挪到 1000 次乱按之后，面板已互相重叠、位置任意，"左上 2×2 的并集外框"没有
    定义 —— 那时量到的是运气，不是引擎行为。**fuzz 只负责 A 层那两条铁律。**
    """
    drv, tw = ctx['drv'], ctx['tw']
    bbox = ctx['_bbox']
    W, H = [float(v) for v in fig.canvas.get_width_height()]
    dpr = float(getattr(fig.canvas, 'device_pixel_ratio', 1.0) or 1.0)
    tags = [_tag_of(fig, a) for a in fig.axes]
    axmap = {t: a for t, a in zip(tags, fig.axes)}
    out = {'n_axes_open': len(fig.axes), 'tags_open': list(tags),
           'size_open': [float(v) for v in fig.get_size_inches()],
           'cbar_n': None, 'structure': None, 'layout_stable': None,
           'drag': None, 'marquee': None, 'cbar_drag': None, 'row': None}

    def _clear_selection():
        """空白纯点击 = 清空选中（与 C4 同一交互），保证确定性手势是"单面板"语义。"""
        spot = _find_blank(fig, bbox, ctx['_hit_handle'], W, H, random.Random(7), tries=60)
        if spot is None:
            return False
        drv.press(fig, spot[0], spot[1])
        drv.pump(30)
        drv.release(fig, spot[0], spot[1])
        drv.pump(60)
        return True

    def _center_px(ax):
        b = bbox(ax)
        return ((b[0] + b[2] / 2.0) * W, (b[1] + b[3] / 2.0) * H)

    def _drag_center(ax, dx_n, dy_n, steps=8):
        cx, cy = _center_px(ax)
        _drag(drv, fig, cx, cy, cx + dx_n * W, cy + dy_n * H, steps=steps)
        drv.pump(120)

    def _self_center_ok(ax):
        """该面板的**中心点**是否真的落在它自己身上（mpl 公开 API `canvas.inaxes`）。

        面板重叠时事件会落到"最上面"那个；先问 mpl 一声，免得把手势打进别的面板、
        把"只动目标"变成量错对象（那是夹具问题，不是引擎问题）。
        返回 True/False；拿不到就 None（= 问不出来，按首选来）。
        """
        cx, cy = _center_px(ax)
        try:
            # canvas.inaxes 吃**显示坐标**（Qt 侧 = 逻辑 px × device_pixel_ratio）
            return fig.canvas.inaxes((cx * dpr, cy * dpr)) is ax
        except Exception:
            return None

    def _pick_target(prefer):
        for t in [prefer] + [x for x in tags if x != prefer]:
            if _self_center_ok(axmap[t]) is not False:
                return t, ('prefer' if t == prefer else 'self-center-scan')
        return prefer, 'fallback'

    def _only_target(prefer_tag, dx_n, dy_n, note):
        cleared = _clear_selection()
        target_tag, picked = _pick_target(prefer_tag)
        before = _bbox_map(fig, bbox)
        _drag_center(axmap[target_tag], dx_n, dy_n)
        after = _bbox_map(fig, bbox)
        ch, _dev = _changed(before, after, TOUCH_TOL)
        tgt_dev = max(abs(after[target_tag][i] - before[target_tag][i])
                      for i in range(4))
        odev = 0.0
        for t, b in after.items():
            o = before.get(t)
            if t == target_tag or o is None or len(o) != 4:
                continue
            odev = max(odev, max(abs(b[i] - o[i]) for i in range(4)))
        return {'note': note, 'requested': prefer_tag, 'target': target_tag,
                'target_picked': picked, 'cleared': cleared,
                'dev_target': round(tgt_dev, 6), 'changed': ch,
                'changed_others': [t for t in ch if t != target_tag],
                'max_dev_others': round(odev, 6),
                'before': {t: [round(v, 6) for v in b] for t, b in before.items()},
                'after': {t: [round(v, 6) for v in b] for t, b in after.items()}}
    if kind == 'colorbar':
        # colorbar 轴数用**引擎自己的多信号判定**（不是"宽 < 0.1 就算色条"这种凑数）
        is_cb = ctx.get('_is_cbar')
        try:
            out['cbar_n'] = len([a for a in fig.axes if is_cb(a)])
        except Exception as e:
            out['cbar_n'] = None
            out['cbar_err'] = '%s: %s' % (type(e).__name__, e)
    if kind == 'gridspec':
        st = {'n': len(fig.axes), 'per_dev': {}, 'max_dev': None,
              'declared_h_ratio': None, 'declared_w_ratio': None, 'irregular': False}
        gs = meta.get('gs')
        try:
            bots, tops, lefts, rights = gs.get_grid_positions(fig)
            for (r0, c0, r1, c1), ax in zip(meta['spans'], fig.axes):
                exp = [float(lefts[c0]), float(bots[r0]),
                       float(rights[c1] - lefts[c0]), float(tops[r1] - bots[r0])]
                got = [float(v) for v in bbox(ax)]
                st['per_dev'][_tag_of(fig, ax)] = round(
                    max(abs(got[i] - exp[i]) for i in range(4)), 6)
            st['max_dev'] = max(st['per_dev'].values()) if st['per_dev'] else None
            # "fixture 真的不规则"由 matplotlib **自己声明**的几何证明（不是我们嘴说）：
            #   行高比 = 2（height_ratios[0]/[1]）；整行宽 vs 单列宽 ≥ 1.5。
            st['declared_h_ratio'] = float(tops[0] - bots[0]) / float(tops[1] - bots[1])
            w_full = float(rights[2] - lefts[0])
            w_one = float(rights[2] - lefts[2])
            st['declared_w_ratio'] = w_full / w_one if w_one else None
            st['irregular'] = bool(st['declared_h_ratio'] >= 1.5
                                   and st['declared_w_ratio'] >= 1.5)
        except Exception as e:
            st['err'] = '%s: %s' % (type(e).__name__, e)
        out['structure'] = st
    if kind == 'clayout':
        # 布局稳定：construded_layout 会在**每次绘制**时重算 → 开窗后连续两次采样必须一致
        drv = ctx['drv']
        bbox = ctx['_bbox']
        g1 = _bbox_map(fig, bbox)
        s1 = [float(v) for v in fig.get_size_inches()]
        drv.pump(500)
        g2 = _bbox_map(fig, bbox)
        s2 = [float(v) for v in fig.get_size_inches()]
        ch, dev = _changed(g1, g2, TOUCH_TOL)
        out['layout_stable'] = {
            'size1': [round(v, 6) for v in s1], 'size2': [round(v, 6) for v in s2],
            'd_size': round(max(abs(s2[i] - s1[i]) for i in range(2)), 6),
            'd_geom': round(dev, 6), 'changed': ch}

    # ---- 确定性手势（都在**新版面**上量：拖 1 个面板 / 框选 4 个 / 拖 colorbar）----
    if kind == 'grid4x4':
        out['drag'] = _only_target('ax5', 0.03, 0.03, '拖 1 个面板（首选 ax5）')
        # 框选：完整包住左上 2×2（ax0/ax1/ax4/ax5）。起手点必须在空白处
        # （engine 判据 `toolbox.py:2710-2713`：inaxes is None 才是框选起点），
        # 且选框不能把别的面板**完整**框住（否则会多选）。
        wanted = ['ax0', 'ax1', 'ax4', 'ax5']
        idx = [tags.index(t) for t in wanted]
        bs = [bbox(fig.axes[i]) for i in idx]
        x0 = min(b[0] for b in bs)
        y0 = min(b[1] for b in bs)
        x1 = max(b[0] + b[2] for b in bs)
        y1 = max(b[1] + b[3] for b in bs)
        mq = {'wanted': wanted, 'got': None, 'margin': None, 'start': None,
              'fully_other': None, 'cleared': _clear_selection()}
        for m in (0.004, 0.006, 0.008, 0.010, 0.013, 0.016):
            sx, sy = x0 - m, y1 + m
            if not (0.0 <= sx <= 1.0 and 0.0 <= sy <= 1.0):
                continue
            hit_ax, _h = ctx['_hit_handle'](fig, sx * W * dpr, sy * H * dpr)
            if hit_ax is not None or _in_axes(fig, bbox, sx * W, sy * H, W, H):
                continue                  # 引擎会把这点当拉角/落在别的面板上 → 换更大的外扩
            others_idx = [i for i in range(len(fig.axes)) if i not in idx]
            full = [tags[i] for i in others_idx
                    if (bbox(fig.axes[i])[0] >= x0 - m
                        and bbox(fig.axes[i])[0] + bbox(fig.axes[i])[2] <= x1 + m
                        and bbox(fig.axes[i])[1] >= y0 - m
                        and bbox(fig.axes[i])[1] + bbox(fig.axes[i])[3] <= y1 + m)]
            mq.update({'margin': m, 'start': [round(sx, 4), round(sy, 4)],
                       'fully_other': full})
            if full:
                continue
            _drag(drv, fig, sx * W, sy * H, (x1 + m) * W, (y0 - m) * H, steps=10)
            drv.pump(150)
            sel = [tags[i] for i in range(len(fig.axes))
                   if id(fig.axes[i]) in tw._selected]
            mq['got'] = sorted(sel)
            break
        out['marquee'] = mq
    elif kind == 'colorbar':
        main_t, cb_t = meta['main_tag'], meta['cbar_tag']
        out['drag'] = _only_target(main_t, -0.05, -0.04, '拖主面板（colorbar 应不动）')
        # 拖 colorbar 本体：colorbar 变、主面板不变
        _clear_selection()
        cax = axmap[cb_t]
        center_self = _self_center_ok(cax)
        before = _bbox_map(fig, bbox)
        _drag_center(cax, -0.03, 0.05)
        after = _bbox_map(fig, bbox)
        ch, _dev = _changed(before, after, TOUCH_TOL)
        out['cbar_drag'] = {
            'note': '拖 colorbar 本体', 'target': cb_t, 'center_self': center_self,
            'dev_target': round(max(abs(after[cb_t][i] - before[cb_t][i])
                                    for i in range(4)), 6),
            'dev_main': round(max(abs(after[main_t][i] - before[main_t][i])
                                  for i in range(4)), 6),
            'changed': ch,
            'before': {t: [round(v, 6) for v in b] for t, b in before.items()},
            'after': {t: [round(v, 6) for v in b] for t, b in after.items()}}
    elif kind == 'tight1x3':
        g = _bbox_map(fig, bbox)
        rows = []
        for t in tags:
            b = g[t]
            rows.append({'tag': t, 'y0': round(b[1], 6), 'y1': round(b[1] + b[3], 6),
                         'x0': round(b[0], 6), 'x1': round(b[0] + b[2], 6)})
        out['row'] = {'rows': rows,
                      'dy': round(max(abs(r['y0'] - rows[0]['y0']) for r in rows), 6)
                      if rows else None,
                      'x_inc': bool(all(rows[i]['x1'] <= rows[i + 1]['x0'] + TOUCH_TOL
                                        for i in range(len(rows) - 1))
                                    and all(rows[i]['x0'] < rows[i + 1]['x0']
                                            for i in range(len(rows) - 1)))
                      if rows else False}
    # ---- Ctrl+F（新图形沿用 B1 的"不缩半"口径：每维 ratio∈[0.80,1.20] 且确实生效）----
    if kind in ('gridspec', 'clayout', 'tight1x3'):
        before_in = [float(v) for v in fig.get_size_inches()]
        tb0 = _title_box(fig)
        fb0 = [float(fig.bbox.width), float(fig.bbox.height)]
        # 抓**引擎自己**在 Ctrl+F 前后打的 `fit:` 行：这是判定"按键到底有没有到引擎、
        # 走的是哪个分支"的**唯一权威**。没有它，`ratio=1.000` 有两种解释——引擎判
        # "内容已贴边，无需适配"（`toolbox.py:2352-2355` 的设计分支）或**按键根本没到**；
        # 两者靠肉眼分不开。所以 C18 的空虚性守卫需要这行原文。
        _buf = io.StringIO()
        _old_out = sys.stdout
        fit_log = []
        try:
            sys.stdout = _buf
            drv.key(fig, 'ctrl+f')
            drv.pump(500)
        finally:
            sys.stdout = _old_out
        try:
            fig.canvas.draw()
        except Exception:
            pass
        fit_log = [l.strip() for l in _buf.getvalue().splitlines() if 'fit:' in l]
        after_in = [float(v) for v in fig.get_size_inches()]
        rw = after_in[0] / before_in[0] if before_in[0] else 0.0
        rh = after_in[1] / before_in[1] if before_in[1] else 0.0
        tb1 = _title_box(fig)
        fb1 = [float(fig.bbox.width), float(fig.bbox.height)]
        out['ratio_check'] = {
            'before': [round(v, 4) for v in before_in],
            'after': [round(v, 4) for v in after_in],
            'ratio': [round(rw, 4), round(rh, 4)],
            'ran': bool(abs(rw - 1.0) > 0.005 or abs(rh - 1.0) > 0.005),
            'fit_log': fit_log,
            'n_axes_after_cf': len(fig.axes)}
        if tb1 is not None and tb0 is not None:
            out['title_check'] = {
                'before': [round(v, 1) for v in tb0],
                'after': [round(v, 1) for v in tb1],
                'inside_before': bool(tb0[0] >= -0.5 and tb0[1] >= -0.5
                                      and tb0[2] <= fb0[0] + 0.5 and tb0[3] <= fb0[1] + 0.5),
                'inside_after': bool(tb1[0] >= -0.5 and tb1[1] >= -0.5
                                     and tb1[2] <= fb1[0] + 0.5 and tb1[3] <= fb1[1] + 0.5),
                'figbbox_before': [round(v, 1) for v in fb0],
                'figbbox_after': [round(v, 1) for v in fb1]}
        else:
            out['title_check'] = None
    return out


def _fig_post_checks(ns, mpl, ctx, fig, kind, meta):
    """乱按**之后**的收尾读数：面板还在不在（C11 的"手势后 16 个都在"只用这一块）。"""
    out = {'n_axes_after': len(fig.axes),
           'tags_after': [_tag_of(fig, a) for a in fig.axes],
           'size_after': [float(v) for v in fig.get_size_inches()]}
    return out

def _sc_figs(ns, mpl, ctx, tmp):
    """⑫~⑯：5 种新图形（开窗 → 确定性补测 → 1000 次乱按，**同一窗口内**完成）。

    一次窗口两用：窗口开关 + 首帧是主要成本，A 层手势很便宜。但两条线的判据**分开写**：
    补测（新版面上的一次确定手势）只喂 C11~C20，fuzz 只喂 A1/A2 —— 互不借力。
    顺序上**补测在前**：乱按之后版面已任意（重叠/被拖到画布外），框选外框与 Ctrl+F 的
    ratio 判据都会被夹具状态误伤（详见文件头「多图形扩展（t22）」）。
    """
    kind = ns.cfg.get('kind') or 'grid4x4'
    fig, meta = _fig_fixture(ns, mpl, kind)
    _open_window(ns, mpl, ctx, tmp, fig, kind)
    pre = _fig_open_checks(ns, mpl, ctx, fig, kind, meta)
    data = _fuzz_loop(ns, mpl, ctx, fig, kind)
    post = _fig_post_checks(ns, mpl, ctx, fig, kind, meta)
    figs = dict(pre)
    figs.update(post)
    figs['kind'] = kind
    figs['meta'] = {k: v for k, v in meta.items() if k != 'gs'}
    data['figs'] = figs
    return data


def _sc_fit(ns, mpl, ctx, tmp):
    """B/C：Ctrl+F 裁白边（#1-②/#1-①），由 QT_SCALE_FACTOR × dpi 组合驱动。"""
    cfg = ns.cfg
    fig = _new_fig(ns, mpl)
    # 版面刻意留白（内容不贴边）→ Ctrl+F 应该**真的**缩短画布；
    # 主标题故意从 x=0.02 起、ha='left'，比面板左边界更靠左 —— 这正是
    # #1-① 的场景（旧实现不把 fig 级文字算进内容盒 → 标题被裁）。
    _add_panels(fig, [[0.14, 0.17, 0.72, 0.60]])
    fig.suptitle(_pick_title(mpl), fontsize=11, x=0.02, ha='left', fontweight='bold')
    drv = _driver(ns, fig)
    drv.harden(fig)
    tw = ctx['Tweak'](fig, export_path=os.path.join(tmp, 'fit.json'),
                      heavy=False, fig_index=0, n_figs=1)
    drv.pump(400)
    screen, avail = drv.screen_of(fig)
    dpr = float(getattr(fig.canvas, 'device_pixel_ratio', 1.0) or 1.0)
    before = [float(v) for v in fig.get_size_inches()]
    drv.key(fig, 'ctrl+f')
    drv.pump(500)
    try:
        fig.canvas.draw()
    except Exception:
        pass
    after = [float(v) for v in fig.get_size_inches()]
    tb = _title_box(fig)
    Wpx, Hpx = float(fig.bbox.width), float(fig.bbox.height)
    # sep = 主标题下沿 − 面板上沿（px）。**标题在面板上方时为正**（≈0 = 贴着，
    # 负 = 真重叠）。首跑这里符号写反了，把正常版面误判成"重叠"；详见文件头
    # "用户目击报告与归因"后面的 C1 一节。
    seps = []
    for ax in fig.axes:
        b = ctx['_bbox'](ax)
        seps.append((tb[1] if tb else 0.0) - (b[1] + b[3]) * Hpx)
    return {
        'inches_before': before, 'inches_after': after,
        'ratio': [after[0] / before[0] if before[0] else 0.0,
                  after[1] / before[1] if before[1] else 0.0],
        'title_inside': bool(tb and tb[0] >= -0.5 and tb[1] >= -0.5
                             and tb[2] <= Wpx + 0.5 and tb[3] <= Hpx + 0.5),
        'title_box': tb, 'figbbox': [Wpx, Hpx],
        'sep': (min(seps) if seps else -99.0),
        'canvas': list(fig.canvas.get_width_height()),
        'scale': os.environ.get('QT_SCALE_FACTOR'), 'dpi': float(cfg['dpi']),
        'dpr': dpr, 'screen': screen, 'avail': avail, 'backend': mpl.get_backend(),
        'figsize_open': (list(tw._figsize_open) if tw._figsize_open else None),
    }


def _sc_huge(ns, mpl, ctx, tmp):
    """B/C：请求远超屏幕的 figure（#2-① 钳制 / _fit_to_screen / figsize 静默改）。"""
    cfg = ns.cfg
    req = [float(v) for v in cfg['figsize']]
    fig = _new_fig(ns, mpl)
    _add_panels(fig, [[0.07, 0.52, 0.40, 0.40], [0.53, 0.52, 0.40, 0.40],
                      [0.30, 0.07, 0.40, 0.38]])
    drv = _driver(ns, fig)
    drv.harden(fig)
    trace = _instrument(fig, ctx['Tweak'])       # 测试进程内插桩（不改 src/）
    tw = ctx['Tweak'](fig, export_path=os.path.join(tmp, 'huge.json'),
                      heavy=False, fig_index=0, n_figs=1)
    drv.pump(500)
    screen, avail = drv.screen_of(fig)
    open_in = [float(v) for v in (tw._figsize_open or fig.get_size_inches())]
    dpr = float(getattr(fig.canvas, 'device_pixel_ratio', 1.0) or 1.0)
    canvas_open = list(fig.canvas.get_width_height())
    tk_geo_open = (drv.tk_geom(fig) if ns.backend == 'tk' else None)
    if ns.backend == 'tk':
        # ★ 忠实复现真实用户时序：Tk 窗口在 ``Tweak.__init__`` 期间是 **withdrawn**
        #   （matplotlib ``_backend_tk.py:510/536``），要到 ``launch.py:644`` 的
        #   ``real_show()``（真 ``plt.show()``）才 ``deiconify()``。所以这里必须
        #   **在 Tweak 之后**才显示窗口，才能复现"钳制发生在 show 之后"这一真实情形。
        drv.show_window(fig)
        drv.pump(800)
    drv.pump(2000)                  # 事件泵：让 resizeEvent / <Configure> 钳制路径有机会跑
    after = [float(v) for v in fig.get_size_inches()]
    tk_geo_after = (drv.tk_geom(fig) if ns.backend == 'tk' else None)
    return {
        'inches_req': req, 'inches_open': open_in, 'inches_after': after,
        'canvas_open': canvas_open, 'canvas': list(fig.canvas.get_width_height()),
        'screen': screen, 'avail': avail, 'dpr': dpr, 'dpi': float(cfg['dpi']),
        'frame': (drv.frame(fig) if ns.backend == 'qt' else None),
        'tk_geom_open': tk_geo_open, 'tk_geom_after': tk_geo_after,
        'trace': trace,
        'backend': mpl.get_backend(),
    }


def _sc_drag(ns, mpl, ctx, tmp):
    """B：真拖拽位移（#2-②）。"""
    fig = _new_fig(ns, mpl)
    axs = _add_panels(fig, [[0.10, 0.55, 0.36, 0.34], [0.55, 0.12, 0.36, 0.34]])
    drv = _driver(ns, fig)
    drv.harden(fig)
    ctx['Tweak'](fig, export_path=os.path.join(tmp, 'drag.json'),
                 heavy=False, fig_index=0, n_figs=1)
    drv.pump(400)
    W, H = fig.canvas.get_width_height()
    ax = axs[0]
    b = ctx['_bbox'](ax)
    cx, cy = (b[0] + b[2] / 2.0) * W, (b[1] + b[3] / 2.0) * H
    dx_px, dy_px = -0.08 * W, -0.11 * H
    t0 = _geom(fig, ctx['_bbox'])
    _drag(drv, fig, cx, cy, cx + dx_px, cy + dy_px)
    nb = ctx['_bbox'](ax)
    return {
        'before': [float(v) for v in b], 'after': [float(v) for v in nb],
        'req': [dx_px / W, dy_px / H],
        'got': [float(nb[0] - b[0]), float(nb[1] - b[1])],
        'geom_changed': t0 != _geom(fig, ctx['_bbox']), 'canvas': [W, H],
        'dpr': float(getattr(fig.canvas, 'device_pixel_ratio', 1.0) or 1.0),
        'backend': mpl.get_backend(),
    }


def _sc_snap(ns, mpl, ctx, tmp):
    """C⑧：多面板 + 吸附（拖到 0.005 缝隙处，吸附应把缝隙收成 0）。"""
    fig = _new_fig(ns, mpl)
    axs = _add_panels(fig, [[0.10, 0.14, 0.32, 0.30], [0.60, 0.58, 0.32, 0.30]])
    drv = _driver(ns, fig)
    drv.harden(fig)
    ctx['Tweak'](fig, export_path=os.path.join(tmp, 'snap.json'),
                 heavy=False, fig_index=0, n_figs=1)
    drv.pump(400)
    W, H = fig.canvas.get_width_height()
    ax1 = axs[1]
    b = ctx['_bbox'](ax1)
    a0x0 = ctx['_bbox'](axs[0])[0]
    gap = 0.005                        # 略小于 SNAP_TOL(=0.006) → 必须被吸掉
    target_x0 = a0x0 + gap
    req_dx = target_x0 - b[0]
    cx, cy = (b[0] + b[2] / 2.0) * W, (b[1] + b[3] / 2.0) * H
    # 手写拖拽序列（不用 _drag）：**松手前**读一次"幽灵"位置，把"运动阶段"与"提交阶段"
    # 分开量 —— 首跑欠量（恰好 4/5）时，这样才能定位到底是 motion 还是 release 的问题。
    steps = 16
    drv.press(fig, cx, cy)
    drv.pump(30)
    for i in range(1, steps + 1):
        drv.move(fig, cx + req_dx * W * i / float(steps), cy)
        drv.pump(12)
    ghost_mid = float(ctx['_bbox'](ax1)[0])
    for _ in range(2):                   # settle：落点再投两次（真实用户是连续 motion）
        drv.move(fig, cx + req_dx * W, cy)
        drv.pump(70)
    ghost_end = float(ctx['_bbox'](ax1)[0])
    drv.release(fig, cx + req_dx * W, cy)
    drv.pump(120)
    nb = ctx['_bbox'](ax1)
    return {
        'ax0_x0': float(a0x0), 'target_x0': float(target_x0), 'gap': gap,
        'before_x0': float(b[0]), 'after_x0': float(nb[0]),
        'req_dx': float(req_dx), 'got_dx': float(nb[0] - b[0]),
        'ghost_mid_x0': ghost_mid, 'ghost_end_x0': ghost_end,
        'motions': steps + 2,
        'snap_tol': float(ctx['SNAP_TOL']), 'canvas': [W, H],
        'dpr': float(getattr(fig.canvas, 'device_pixel_ratio', 1.0) or 1.0),
        'backend': mpl.get_backend(),
    }


def _sc_select(ns, mpl, ctx, tmp):
    """C2/C3/C4：框选 / Ctrl 多选 / 空白清空（用户 2026-10-12 目击出来的盲区）。

    ★ 这三条测的是**真实产品功能**，不是测试凭空造的手势 —— 代码位置：
      * ``toolbox.py:19-20`` 明文规则：「Ctrl+点击（或 Shift+点击）加选/减选，或拖空白
        框选（框选需「完全框住」面板才选中，相交不算）」；
      * 状态 ``@1318-1326``（_selected/_sel_artists/_band/_band_origin/_band_active/
        _multi_drag/_multi_origin/_drag_ax）；
      * 空白按下=框选起点 ``@2710-2713``；框选更新 ``@2812-2814``；框选结束
        ``@2477-2504``（**判据 @2497-2499 = 完全框住**，@2490-2492 小框视为点击=清空）；
      * Ctrl/Shift 点选切换 ``@2715-2729``（**以 event.modifiers 为准**，因为真实 Qt
        鼠标事件不带 key —— t2-A6/t6-H7 的老坑，本条断言顺带实测它）；
      * 多选整体拖拽 ``@2733-2744``；帮助表 ``@117-120``；对齐/均分依赖选中
        ``@2052``/``@3640``。

    版图刻意用 monkey 那张（上排两个 [0.08,0.55]/[0.56,0.55]，下排两个
    [0.08,0.09]/[0.56,0.09]，尺寸 0.38×0.34），这样：
      框选 (0.02,0.49)→(0.98,0.96) **完整包住上排 2 个、不含下排** ⇒ 期望恰好 2 个。
    """
    fig = _new_fig(ns, mpl)
    axs = _add_panels(fig, [[0.08, 0.55, 0.38, 0.34], [0.56, 0.55, 0.38, 0.34],
                            [0.08, 0.09, 0.38, 0.34], [0.56, 0.09, 0.38, 0.34]])
    drv = _driver(ns, fig)
    drv.harden(fig)
    tw = ctx['Tweak'](fig, export_path=os.path.join(tmp, 'select.json'),
                      heavy=False, fig_index=0, n_figs=1)
    drv.pump(400)
    W, H = fig.canvas.get_width_height()

    def tags():
        out = []
        for i, ax in enumerate(fig.axes):
            if id(ax) in tw._selected:
                out.append(getattr(ax, '_rw_tag', 'ax?%d' % i))
        return sorted(out)

    stage0 = tags()
    # 1) 框选：从空白点拖到能**完整包住上排 2 个**（且不含下排）
    _drag(drv, fig, 0.02 * W, 0.49 * H, 0.98 * W, 0.96 * H, steps=10)
    stage1 = tags()
    # 2) Ctrl+点击 下排左面板中心 → 加选 1 个
    b2 = ctx['_bbox'](axs[2])
    c2x, c2y = (b2[0] + b2[2] / 2.0) * W, (b2[1] + b2[3] / 2.0) * H
    drv.press(fig, c2x, c2y, ('ctrl',))
    drv.pump(80)
    drv.release(fig, c2x, c2y, ('ctrl',))
    drv.pump(200)
    stage2 = tags()
    # 3) 空白纯点击 → 清空（@2490-2492/2482-2484：小框/无框视为点击）
    drv.press(fig, 0.50 * W, 0.03 * H)
    drv.pump(80)
    drv.release(fig, 0.50 * W, 0.03 * H)
    drv.pump(200)
    stage3 = tags()
    return {
        'stage0': stage0, 'stage1': stage1, 'stage2': stage2, 'stage3': stage3,
        'canvas': [W, H],
        'dpr': float(getattr(fig.canvas, 'device_pixel_ratio', 1.0) or 1.0),
        'backend': mpl.get_backend(),
    }


def _sc_undoredo(ns, mpl, ctx, tmp):
    """C10：撤销/重做**往返一致**（真窗口 + 真 Ctrl+Z / Ctrl+Y 键事件）。

    判据与理由（船长要求写清；NUMBERS.md 同步）：
      * 一次"往返" = 拖动一个面板 → Ctrl+Z → Ctrl+Y，取四个几何快照
        （**全部面板**的 bbox，figure 比例坐标，6 位小数）：
        G0 拖动前 ｜ G1 拖动后（已吸附落位）｜ G2 undo 后 ｜ G3 redo 后。
      * 一致 = ``|G2-G0|∞ ≤ tol`` **且** ``|G3-G1|∞ ≤ tol``，tol = **0.002**
        （千分之二，约一两像素量级；不是"精确等值"，也不是随便放大的宽带）。
      * **"上一态"不是猜的**：它就是引擎自己 ``_push_undo()``（toolbox.py:2454）压进
        undo 栈的那份 bbox 快照；undo/redo 的绑定在 toolbox.py:3774-3778，
        帮助表 @220-221（Ctrl+Z 撤销 / Ctrl+Y·Ctrl+Shift+Z 重做）。
      * 每轮**还要求"拖动确实生效"**（G1 ≠ G0）—— 否则这次往返什么都没验证。
      * 为什么不退化成一个"更容易过"的判据（比如"只要不崩"）：那种判据对"撤销能不能
        回到上一态"零信息量。**宁可这条红，也不写恒真判据。**
    """
    fig = _new_fig(ns, mpl)
    axs = _add_panels(fig, [[0.08, 0.55, 0.38, 0.34], [0.56, 0.55, 0.38, 0.34]],
                      legend=False)
    drv = _driver(ns, fig)
    drv.harden(fig)
    tw = ctx['Tweak'](fig, export_path=os.path.join(tmp, 'undoredo.json'),
                      heavy=False, fig_index=0, n_figs=1)
    # ★ 决定性插桩：在 **canvas 上独立注册** matplotlib 的 `button_press_event`
    #   （与引擎自己那份注册无关）。为什么不用"包裹 Tweak._on_press"：引擎在 __init__ 里
    #   注册的是**当时那个绑定方法**，事后改类属性进不了回调链（上一版就是这么空手的）。
    #   这一份能回答："matplotlib 到底有没有把 press 投递出来（几次/按钮号/inaxes 是谁）"。
    _press_rec = []

    def _on_bp(ev):
        try:
            ia = None
            for k, a in enumerate(fig.axes):
                if a is getattr(ev, 'inaxes', None):
                    ia = k
            _press_rec.append({'name': 'press', 'x': round(float(ev.x), 1),
                               'y': round(float(ev.y), 1),
                               'btn': int(getattr(ev, 'button', -1)),
                               'dbl': bool(getattr(ev, 'dblclick', False)),
                               'inaxes': ia})
        except Exception as e:
            _press_rec.append({'name': 'err', 'err': '%s: %s' % (type(e).__name__, e)})

    def _on_br(ev):
        _press_rec.append({'name': 'release'})

    fig.canvas.mpl_connect('button_press_event', _on_bp)
    fig.canvas.mpl_connect('button_release_event', _on_br)

    def _n_press_cbs():
        """canvas 上 `button_press_event` 的回调条数（含我自己注册的那 1 条）。

        引擎的 `_on_press` 是在 ``Tweak.__init__`` 里 ``mpl_connect`` 注册的；撤销/重做
        若走 `_disconnect()`/`_connect()` 重建，把这条漏掉，就会出现"matplotlib 投递了
        press（我的探针收到了），但引擎毫无反应（无选中、无框选、无日志）"。
        """
        try:
            return len(fig.canvas.callbacks.callbacks.get('button_press_event', {}))
        except Exception:
            return -1
    drv.pump(400)
    W, H = fig.canvas.get_width_height()
    bbox = ctx['_bbox']
    tol = 0.002

    def _dmax(a, b):
        if len(a) != len(b):
            return 9.9
        return max([abs(x - y) for x, y in zip(a, b)] or [9.9])

    # ★ 反污染采样：正常情况下扰控保证窗口不被激活；被激活 ⇒ 极可能是用户点了窗口
    #   ⇒ 这轮读数可能被污染（数字表只用干净运行，见 assemble 里的重跑逻辑）。
    _pol = {'n': 0, 'samples': 0}

    def _sample():
        _pol['samples'] += 1
        try:
            if drv.active(fig):
                _pol['n'] += 1
        except Exception:
            pass

    def _probe(ax, ddx, ddy):
        """一次普通拖动 + 两个读数。

        ★ 判别用插桩：**press 之后立刻**读引擎内部状态（``_drag_ax`` / ``_selected``）。
          * 若 ``_selected`` 里没有被按的面板 ⇒ press **根本没到**引擎的 ``_on_press``
            （事件投递/坐标口径问题 = 测试侧）；
          * 若有、但拖完没有位移 ⇒ press 到了、拖拽路径没生效（引擎侧），
            这时再拿 ``_drag_ax`` 是否仍在来区分"运动阶段丢了"还是"提交阶段丢了"。
        """
        G = _geom(fig, bbox)
        _m0 = len(_press_rec)          # 本探针期间新产生的 press/release 记录
        # ★ 有界排查用：把"坐标基准"在**每次拖动前**记全，与第 1 次对比。
        #   关键对照：引擎口径 `_bbox(ax)`(=ax._position)×fig.bbox.size  vs  matplotlib
        #   自己 hit-test 用的 `ax.bbox.bounds`；两者不一致 = 位置改了没重画/基准漂移。
        _W2, _H2 = fig.canvas.get_width_height()
        _bb = [float(v) for v in fig.bbox.size]
        try:
            _wg = drv.widget
            _wsz = [int(_wg.width()), int(_wg.height())]
        except Exception:
            _wsz = None
        _axb = [[round(float(v), 1) for v in a.bbox.bounds] for a in fig.axes]
        _exp = [[round(bbox(a)[0] * _bb[0], 1), round(bbox(a)[1] * _bb[1], 1),
                 round(bbox(a)[2] * _bb[0], 1), round(bbox(a)[3] * _bb[1], 1)]
                for a in fig.axes]
        _base = {'WH': [_W2, _H2], 'figbbox': _bb, 'widget_size': _wsz,
                 'dpr': float(getattr(fig.canvas, 'device_pixel_ratio', 1.0) or 1.0),
                 'ax_bbox_mpl': _axb, 'ax_bbox_from_position': _exp}
        b = bbox(ax)
        cx, cy = (b[0] + b[2] / 2.0) * W, (b[1] + b[3] / 2.0) * H
        drv.press(fig, cx, cy)
        drv.pump(80)
        _sel_after_press = sorted(
            getattr(a, '_rw_tag', '?') for a in fig.axes
            if id(a) in getattr(tw, '_selected', set()))
        _drag_after_press = bool(getattr(tw, '_drag', False))
        _band_after_press = getattr(tw, '_band', None) is not None
        for k in range(1, 11):
            drv.move(fig, cx + ddx * W * k / 10.0, cy + ddy * H * k / 10.0)
            drv.pump(12)
        _drag_mid = bool(getattr(tw, '_drag', False))
        for _ in range(2):
            drv.move(fig, cx + ddx * W, cy + ddy * H)
            drv.pump(70)
        G1a = _geom(fig, bbox)
        drv.release(fig, cx + ddx * W, cy + ddy * H)
        drv.pump(400)          # 落位/提交留足时间（首跑"松手即读"曾读到半成品）
        G1 = _geom(fig, bbox)
        _sample()
        return {'G0': G, 'G1a': G1a, 'G1': G1,
                'moved_raw': G1a != G, 'moved': G1 != G,
                'base': _base,
                'sel_after_press': _sel_after_press,
                'drag_ax_after_press': _drag_after_press,
                'drag_ax_mid': _drag_mid,
                'band_after_press': _band_after_press,
                # ↓ 决定性转储：press 的像素点、画布尺寸、被按轴的 bbox、按下后是否走了
                #   "空白 → 框选起点"（_band_origin 被置上就说明引擎认为这是空白处），
                #   以及同一把 _bbox 量出来的全部轴（看它到底在哪）。
                'press_xy': [round(cx, 1), round(cy, 1)], 'WH': [W, H],
                'bbox_pressed': [round(v, 4) for v in b],
                'band_origin_set': getattr(tw, '_band_origin', None) is not None,
                'press_events': [dict(e, name=e.get('name')) for e in _press_rec[_m0:]],
                'press_cbs_now': _n_press_cbs(),
                'axes_dump': [[getattr(a, '_rw_tag', '?')] +
                              [round(v, 3) for v in bbox(a)] for a in fig.axes]}

    trips = []
    probes = []

    def _trip(ax, ddx, ddy, i):
        p = _probe(ax, ddx, ddy)
        _cb_before = _n_press_cbs()
        drv.key(fig, 'ctrl+z')
        drv.pump(350)
        G2 = _geom(fig, bbox)
        _cb_after_undo = _n_press_cbs()
        drv.key(fig, 'ctrl+y')
        drv.pump(350)
        G3 = _geom(fig, bbox)
        _cb_after_redo = _n_press_cbs()
        t = {'i': i, 'tag': getattr(ax, '_rw_tag', 'ax?'),
             'moved': p['moved'], 'moved_raw': p['moved_raw'],
             'd_undo': _dmax(G2, p['G0']), 'd_redo': _dmax(G3, p['G1']),
             'G0': p['G0'], 'G1a': p['G1a'], 'G1': p['G1'], 'G2': G2, 'G3': G3,
             'press_cbs': [_cb_before, _cb_after_undo, _cb_after_redo]}
        trips.append(t)
        return t

    # P1：一次往返（ax0）。判据只认 P1/P4 这两次**确定性往返**。
    _trip(axs[0], -0.10, -0.08, 0)
    # P2/P3：**诊断探针** —— redo 之后的第一次/第二次"普通拖动"是否生效。
    # 首跑实测：trip#1 的拖动完全没生效，而随后的 Ctrl+Z 却回滚了 trip#0 的位移
    # （d_undo=0.1 == trip#0 的 x 位移量）⇒ 需要判别是"redo 后首次编辑被吞"（引擎行为）
    # 还是"某个面板/坐标特例"（测试夹具）。这两个探针不改判据，只把机制记进证据。
    _p2 = _probe(axs[1], 0.07, -0.12)
    probes.append(dict(_p2, tag='ax1', note='redo 后第 1 次普通拖动'))
    _p3 = _probe(axs[1], 0.05, 0.06)
    probes.append(dict(_p3, tag='ax1', note='redo 后第 2 次普通拖动'))
    # P4：第二次往返（ax1）。
    _trip(axs[1], -0.06, -0.07, 1)

    ok_list = [t for t in trips
               if t['moved'] and t['d_undo'] <= tol and t['d_redo'] <= tol]
    return {
        'n_trips': len(trips), 'ok_n': len(ok_list), 'tol': tol,
        'moved_n': sum(1 for t in trips if t['moved']),
        'max_dev': max([max(t['d_undo'], t['d_redo']) for t in trips] or [9.9]),
        'trips': trips, 'probes': probes,
        'probe_moved': [bool(p['moved']) for p in probes],
        'active_n': _pol['n'], 'active_samples': _pol['samples'],
        'suspected': bool(_pol['n']),
        'canvas': [W, H],
        'dpr': float(getattr(fig.canvas, 'device_pixel_ratio', 1.0) or 1.0),
        'backend': mpl.get_backend(),
    }


def _sc_qtwin(ns, mpl, ctx, tmp):
    """C⑥：Qt 真 native 窗口存在性。"""
    fig = _new_fig(ns, mpl)
    _add_panels(fig, [[0.15, 0.18, 0.70, 0.65]])
    drv = _driver(ns, fig)
    drv.harden(fig)
    ctx['Tweak'](fig, export_path=os.path.join(tmp, 'qtwin.json'),
                 heavy=False, fig_index=0, n_figs=1)
    drv.pump(600)
    w = drv.window(fig)
    screen, avail = drv.screen_of(fig)
    return {
        'visible': bool(w.isVisible()), 'winid': int(w.winId()),
        'backend': mpl.get_backend(), 'qt_api': getattr(drv, 'qt_api', None),
        'title': str(w.windowTitle()), 'active': drv.active(fig),
        'platform': str(drv.app.platformName()),
        'screen': screen, 'avail': avail,
        'dpr': float(getattr(fig.canvas, 'device_pixel_ratio', 1.0) or 1.0),
    }


def _sc_hoverkeys(ns, mpl, ctx, tmp):
    """观察项（软检查）：依赖 hover 的 +/- 快捷键。

    本版**不判失败**：禁跑期无法先确认"崩"到底是引擎缺陷还是测试伪影，
    先只记录现象，等放行后复现确认。
    """
    fig = _new_fig(ns, mpl)
    axs = _add_panels(fig, [[0.12, 0.20, 0.60, 0.60]])
    drv = _driver(ns, fig)
    drv.harden(fig)
    ctx['Tweak'](fig, export_path=os.path.join(tmp, 'hoverkeys.json'),
                 heavy=False, fig_index=0, n_figs=1)
    drv.pump(400)
    W, H = fig.canvas.get_width_height()
    b = ctx['_bbox'](axs[0])
    px, py = (b[0] + b[2] / 2.0) * W, (b[1] + b[3] / 2.0) * H
    drv.move(fig, px, py)
    drv.pump(150)
    drv.key(fig, 'plus')
    drv.pump(150)
    drv.key(fig, 'minus')
    drv.pump(150)
    return {'done': True, 'backend': mpl.get_backend(), 'dpr': 1.0}


def _sc_probe(ns, mpl, ctx, tmp):
    """投递前置探针：**开跑前**先确认这台机真的能把事件送进窗口。

    为什么非要这一条：0.1.17 那一版真窗口套件在本机跑了 90 分钟、红了 7 条，其中 5 条
    的根因是"`QTest.mouseMove` 一个 motion 事件都没送出去"——**环境/投递问题被当成了产品
    缺陷**。90 分钟假红的代价太大，所以开跑前先花 2 秒问三个问题：

      1. 鼠标**移动**收得到吗（`motion_notify_event` 计数 ≥1）？
      2. 按键收得到吗（`key_press_event` 计数 ≥1）？
      3. 窗口拿到焦点了吗（`isActiveWindow()`）？

    收不到就往回交 `delivery: false` + 一句人话，父进程据此 **skip**（不是硬跑）。
    `MPLTWEAK_RW_FORCE_UNDELIVERABLE=1` 时本探针**故意不投**，用来演示"环境投递不到"
    这条分支（反向验证，见 `_team014/rw_delivery.md`）。
    """
    forced = bool(os.environ.get('MPLTWEAK_RW_FORCE_UNDELIVERABLE'))
    fig = _new_fig(ns, mpl)
    ax = fig.add_subplot(111)
    ax.plot([0, 1], [1, 0])
    drv = _driver(ns, fig)
    drv.harden(fig)
    try:                                  # 探针不走 Tweak，自己把窗口显示出来
        drv.show_window(fig)
    except AttributeError:
        try:
            fig.canvas.manager.window.show()
        except Exception:
            pass
    drv.pump(400)
    got = {'motion': 0, 'key': 0}

    def _on_motion(ev):
        got['motion'] += 1

    def _on_key(ev):
        got['key'] += 1

    fig.canvas.mpl_connect('motion_notify_event', _on_motion)
    fig.canvas.mpl_connect('key_press_event', _on_key)
    W, H = fig.canvas.get_width_height()
    if not forced:
        drv.move(fig, 0.30 * W, 0.40 * H)
        drv.pump(60)
        drv.move(fig, 0.62 * W, 0.48 * H)
        drv.pump(60)
        drv.key(fig, 'x')
        drv.pump(150)
    try:
        active = bool(drv.active(fig))
    except Exception:
        active = None
    return {
        'delivery': bool(got['motion'] >= 1 and got['key'] >= 1),
        'motion': got['motion'], 'key': got['key'], 'active': active,
        'forced_undeliverable': forced, 'via_post': bool(MOVE_VIA_POST),
        'backend': mpl.get_backend(), 'canvas': [W, H],
        'needs_window': True,
    }


def _sc_tkwin(ns, mpl, ctx, tmp):
    """C⑦：Tk 真窗口 + event_generate 真拖拽。"""
    fig = _new_fig(ns, mpl)
    axs = _add_panels(fig, [[0.12, 0.50, 0.40, 0.34], [0.55, 0.12, 0.40, 0.34]])
    drv = _driver(ns, fig)
    drv.harden(fig)
    ctx['Tweak'](fig, export_path=os.path.join(tmp, 'tkwin.json'),
                 heavy=False, fig_index=0, n_figs=1)
    drv.show_window(fig)        # ★ Tk 必须由测试自己映射窗口（见 TkDriver.show_window）
    drv.pump(700)
    wg = drv.widget
    screen, avail = drv.screen_of(fig)
    W, H = fig.canvas.get_width_height()
    ax = axs[0]
    b = ctx['_bbox'](ax)
    cx, cy = (b[0] + b[2] / 2.0) * W, (b[1] + b[3] / 2.0) * H
    dx_px, dy_px = -0.06 * W, -0.08 * H
    _drag(drv, fig, cx, cy, cx + dx_px, cy + dy_px)
    nb = ctx['_bbox'](ax)
    try:                                     # 焦点记录（Tk 没有 isActiveWindow，用焦点对象代理）
        focus = str(drv.root.focus_get())
    except Exception:
        focus = '?'
    return {
        'mapped': bool(wg.winfo_ismapped()), 'viewable': bool(wg.winfo_viewable()),
        'backend': mpl.get_backend(), 'screen': screen, 'avail': avail,
        'widget': str(wg.winfo_class()), 'focus': focus,
        'title': str(drv.root.title()),
        'req': [dx_px / W, dy_px / H],
        'got': [float(nb[0] - b[0]), float(nb[1] - b[1])],
        'geom_changed': list(ctx['_bbox'](ax)) != list(b),
        'canvas': [W, H], 'dpr': float(getattr(fig.canvas, 'device_pixel_ratio', 1.0) or 1.0),
    }


_CHILD_FUNCS = {
    # 投递前置探针**不是**一个"场景"（不进 SCENARIOS、不出断言行），只是开跑前的自检；
    # 台账那边要求 `set(SCENARIOS) == set(_CHILD_FUNCS)`，所以下面台账口径里把它排除。
    'preflight': _sc_probe,
    'monkey': _sc_monkey,
    # ⑫~⑯ 五种新图形（t22）：同一个函数按 cfg['kind'] 造图 + 补测
    'figs_grid4x4': _sc_figs,
    'figs_gridspec': _sc_figs,
    'figs_colorbar': _sc_figs,
    'figs_clayout': _sc_figs,
    'figs_tight1x3': _sc_figs,
    'fit': _sc_fit,
    'huge': _sc_huge,
    'drag': _sc_drag,
    'snap': _sc_snap,
    'qtwin': _sc_qtwin,
    'select': _sc_select,
    'undoredo': _sc_undoredo,
    'hoverkeys': _sc_hoverkeys,
    'tkwin': _sc_tkwin,
}


def _child_main(ns):
    """子进程入口：真开窗、真投事件、把测量结果以 @@RW@@ JSON 交回。"""
    import matplotlib
    backend = 'QtAgg' if ns.backend == 'qt' else 'TkAgg'
    matplotlib.use(backend)                    # 显式指定：不受 MPLBACKEND / rcParams 影响
    # 打扰控制 ①：matplotlib 默认 figure.raise_window=True 会让
    # FigureManagerQT.show() 去 activateWindow()+raise_()（抢焦点）。
    matplotlib.rcParams['figure.raise_window'] = False
    import matplotlib.pyplot as plt            # noqa: F401  （use() 之后再 import）

    from mpltweak.toolbox import (Tweak, _bbox, _hit_handle, MIN_SIZE, SNAP_TOL,
                                 _is_colorbar_ax)
    ctx = {'Tweak': Tweak, '_bbox': _bbox, '_hit_handle': _hit_handle,
           'MIN_SIZE': MIN_SIZE, 'SNAP_TOL': SNAP_TOL, 'plt': plt,
           '_is_cbar': _is_colorbar_ax}

    tmp = tempfile.mkdtemp(prefix='mpltweak_rw_')
    out = {'scenario': ns.scenario, 'backend': ns.backend, 'cfg': ns.cfg,
           'error': None, 'traceback': None, 'py': sys.version.split()[0],
           'mpl': matplotlib.__version__, 'qt_api': None,
           'scale_env': os.environ.get('QT_SCALE_FACTOR')}
    try:
        data = _CHILD_FUNCS[ns.scenario](ns, matplotlib, ctx, tmp)
        out.update(data or {})
        drv = _ACTIVE.get('drv')
        if drv is not None:
            out['qt_api'] = getattr(drv, 'qt_api', None)
            drv.pump(60)
    except BaseException as e:
        out['error'] = '%s: %s' % (type(e).__name__, e)
        out['traceback'] = traceback.format_exc()[-2000:]
    finally:
        if not ns.keep_tmp:
            shutil.rmtree(tmp, ignore_errors=True)
    print(MARK + json.dumps(out, ensure_ascii=False, default=str), flush=True)
    return 0


# ===========================================================================
# 父进程侧：断言（纯数值；父进程**绝不** import pyplot）
# ===========================================================================
class Report:
    def __init__(self, backend):
        self.backend = backend
        self.rows = []
        self.softs = []
        self.facts = {}

    def check(self, cid, layer, desc, ok, detail, attempts=1):
        self.rows.append({'id': cid, 'layer': layer, 'desc': desc,
                          'ok': bool(ok), 'detail': detail, 'attempts': attempts})
        note = ''
        if attempts > 1:
            note = ('（重试 %d 次后通过）' % attempts) if ok else ('（%d 次尝试仍失败）' % attempts)
        say('  [%s] %s %s %s' % ('PASS' if ok else 'FAIL', cid, desc, note))
        say('        %s' % detail)

    def soft(self, label, detail, warn=False):
        self.softs.append({'label': label, 'detail': detail, 'warn': bool(warn)})
        say('  [%s] SOFT %s — %s' % ('WARN' if warn else 'OK', label, detail))

    @property
    def failed(self):
        return [r for r in self.rows if not r['ok'] and not r.get('pinned')]

    def pin(self, cid, layer, desc, detail, pin):
        """记一条「已知缺陷 pin」：预期 FAIL 且确实 FAIL → **不计失败**（保持可信的绿）。"""
        self.rows.append({'id': cid, 'layer': layer, 'desc': desc, 'ok': False,
                          'detail': detail, 'attempts': 1, 'pinned': True, 'pin': pin})
        say('  [PIN] %s %s' % (cid, desc))
        say('        %s' % detail)
        say('        已知缺陷 pin：缺陷=%s；修复后请移除 —— %s'
            % (pin.get('task'), pin.get('remove_when')))


def _fail_detail(rec, prefix=''):
    if rec is None:
        return prefix + '场景未产出结果'
    if rec.get('timeout'):
        return prefix + '子进程超时被杀（%.1fs）' % rec.get('secs', -1)
    if rec.get('rc') != 0:
        return (prefix + '子进程 rc=%s（GUI 回调里未捕获的 Python 异常会让 PyQt 直接 '
                'qFatal 中止；stderr 里有 traceback）stderr 尾: %s'
                % (rec.get('rc'), _tail(rec.get('stderr'))))
    d = _d(rec)
    if d.get('error'):
        return prefix + '子进程内异常: %s | %s' % (d['error'], (d.get('traceback') or '')[-400:])
    return prefix + '无数据（@@RW@@ 行缺失）stderr 尾: %s' % _tail(rec.get('stderr'))


def _finite(v):
    try:
        return math.isfinite(float(v))
    except (TypeError, ValueError):
        return False


def _note_facts(rep, rec, keys=('screen', 'avail', 'dpr', 'dpi', 'scale',
                                'qt_api', 'py', 'mpl', 'backend', 'platform')):
    d = _d(rec)
    for k in keys:
        if d.get(k) is not None and k not in rep.facts:
            rep.facts[k] = d[k]


def assemble(runner, rep, layer='all'):
    """按层装配断言。qt 后端跑 A/B/C；tk 后端只跑 C⑦（冒烟）。"""
    backend = runner.backend

    # ======================= A 层（乱按） =======================
    # 6 类图形各 1000 次真手势（= 6000 次）；**判据只有那两条铁律**，一字未放宽：
    #   ① 不崩（子进程 rc=0、手势层零异常）
    #   ② 状态未坏（面板集合未变 / 内容还在 / 几何有限且合法）
    # 逐类读数都进 det（哪一类坏了一眼可见）；反污染（isActiveWindow）也按类采样。
    if backend == 'qt' and layer in ('A', 'all'):
        _FUZZ_IDS = ('monkey', 'figs_grid4x4', 'figs_gridspec', 'figs_colorbar',
                     'figs_clayout', 'figs_tight1x3')

        def _fuzz_all(force=False):
            """顺序取 6 个 fuzz 场景；**每新开一个窗口就打印一行进度**（含实测耗时）。

            进度行的用处有两个：① 长批次（6×1000 手势）跑的时候能**边跑边看**每一类的
            真实耗时（外部的长任务输出是可流式读到的）；② 把"每个子进程的墙钟"写进日志，
            供旁边证明"6000 次手势实际花了多久"（NUMBERS.md 的排期数字来自这里，不是估算）。
            """
            out = []
            for sid in _FUZZ_IDS:
                before = runner.spawns
                rec = runner.get(sid, None, None, force=force)
                if runner.spawns > before:
                    d = _d(rec) or {}
                    say('  [fuzz 进度] %s：n=%s 几何变化=%s 选择变化=%s pump×%s '
                        '子进程耗时=%ss rc=%s' % (sid, d.get('n'), d.get('landed'),
                                              d.get('sel_landed'), d.get('pump_scale'),
                                              rec.get('secs'), rec.get('rc')))
                out.append((sid, rec))
            return out

        def _brief(sid, d):
            return ('%s %s 次(几何%d/选择%d，pump×%s，%ss)'
                    % (sid, d.get('n', -1), d.get('landed', -1), d.get('sel_landed', -1),
                       d.get('pump_scale', -1), d.get('secs_gestures', -1)))

        def pred_a1(force=False):
            recs = _fuzz_all(force)
            briefs, tot = [], 0
            for sid, rec in recs:
                d = _d(rec)
                if rec.get('rc') != 0 or not d:
                    return False, '%s: %s' % (sid, _fail_detail(rec))
                if d.get('error'):
                    return False, '%s 子进程内异常: %s' % (sid, d['error'])
                errs = d.get('errors') or []
                if errs:
                    return False, '%s: %d 次手势抛异常，首条: %s' % (sid, len(errs), errs[0])
                tot += int(d.get('n', 0) or 0)
                briefs.append(_brief(sid, d))
            return True, ('%d 次真手势零异常（%d 类图形：%s）；子进程 rc 全 0；后端 %s'
                          % (tot, len(recs), '｜'.join(briefs),
                             (_d(recs[0][1]) or {}).get('backend')))

        def _susp_list():
            return [(sid, _d(rec) or {}) for sid, rec in _fuzz_all()
                    if (_d(rec) or {}).get('active_n')]

        def _susp_str(items):
            return ', '.join('%s %s/%s 次采样被激活'
                             % (sid, d.get('active_n'), d.get('active_samples'))
                             for sid, d in items)

        ok, det = pred_a1(False)
        _susp = _susp_list()
        if ok and _susp:
            # ★ 反污染：正常情况下扰控保证窗口**不被激活**；被激活 ⇒ 极可能是用户点了窗口
            #   ⇒ 这轮读数可能被污染 ⇒ 重跑一次、用干净读数（数字表只用干净运行）。
            ok, det = pred_a1(True)
            _s2 = _susp_str(_susp_list()) or '重跑后全干净'
            det += ('；★ WARN: external interaction suspected（首轮 %s → 已重跑全部 6 类；'
                    '重跑 %s）' % (_susp_str(_susp), _s2))
        elif _susp:
            det += '；★ WARN: external interaction suspected（%s）' % _susp_str(_susp)
        rep.check('A1', 'A', '乱按不崩（6 类图形 × 1000 次 = 6000 次真手势无异常）', ok, det)

        def _a2_probs(d):
            probs = []
            tb, ta = d.get('tags_before') or [], d.get('tags_after') or []
            if sorted(tb) != sorted(ta):
                probs.append('面板丢/多了: %r → %r' % (tb, ta))
            cc = d.get('content_counts') or []
            if cc and min(cc) < 1:
                probs.append('有面板内容被清空: %r' % cc)
            bad = []
            gm = d.get('geom_after') or []
            for i in range(0, len(gm) - 3, 4):
                x, y, w, h = gm[i], gm[i + 1], gm[i + 2], gm[i + 3]
                if not all(_finite(v) for v in (x, y, w, h)):
                    bad.append('非有限数 %r' % [x, y, w, h])
                    continue
                if w < 0.019 or h < 0.019:
                    bad.append('尺寸小于 MIN_SIZE: w=%.4f h=%.4f' % (w, h))
                # 产品**没有画布钳制**（只有 MIN_SIZE=0.02）：拖拽/缩放允许把面板放到画布
                # 外（matplotlib 本来支持 [x0<0] 这类位置，也是给共享 colorbar 让位的常规
                # 做法）。所以只钉"被推到完全看不见"才算状态坏 —— 首跑把判据写成"不得越出
                # 画布 ±0.05"是过严（面板停在 -0.08 是按设计允许的，不是状态坏了）。
                if x + w <= 0.0 or x >= 1.0 or y + h <= 0.0 or y >= 1.0:
                    bad.append('面板被推到完全离开画布: %r' % [x, y, w, h])
            if bad:
                probs.append('几何非法 %d 处: %s' % (len(bad), '; '.join(bad[:3])))
            return probs

        def pred_a2(force=False):
            recs = _fuzz_all(force)
            briefs, bad_all = [], []
            for sid, rec in recs:
                d = _d(rec)
                if rec.get('rc') != 0 or not d:
                    return False, '%s: %s' % (sid, _fail_detail(rec))
                probs = _a2_probs(d)
                if probs:
                    bad_all.append('%s: %s' % (sid, '｜'.join(probs)))
                briefs.append('%s 面板%d/内容%r' % (sid, len(d.get('tags_after') or []),
                                                   d.get('content_counts')))
            if bad_all:
                return False, '；'.join(bad_all)
            return True, ('6 类图形乱按后状态均未坏（面板集合未变 / 内容还在 / 几何有限合法）：'
                          '%s' % '；'.join(briefs))

        ok, det = pred_a2(False)
        rep.check('A2', 'A', '乱按后状态未坏（6 类图形逐类查：面板/内容/几何）', ok, det)

        dm = _d(_fuzz_all()[0][1])
        if dm:
            n = dm.get('n', -1)
            lr = dm.get('landed_ratio', 0.0)
            sr = dm.get('sel_landed_ratio', 0.0)
            # ★ 两个**独立**指标：几何变化率 ≠ 事件落地率。框选/点选只改选中态、不改几何；
            #   首跑把两者糊成一个数，36% 被误读成"事件没落地"（真相是框太小，见文件头）。
            rep.soft('A 层①几何变化率（monkey）',
                     '%.1f%%（%d/%d 次手势改变了面板几何）'
                     % (100.0 * lr, dm.get('landed', -1), n), warn=lr < 0.30)
            rep.soft('A 层②选中态变化率（monkey）',
                     '%.1f%%（%d/%d 次手势改变了选中集合 → 选择类事件确实落到了引擎）'
                     % (100.0 * sr, dm.get('sel_landed', -1), n), warn=sr < 0.05)
            big = dm.get('band_big', 0)
            hit = dm.get('band_hit', 0)
            rep.soft('A 层③框选命中率（monkey）',
                     '大框（完整包住 1~2 个面板）%d 次 → %d 次真的选中 ≥1 个（%s）；'
                     '另：小框 %s 次 + 空白框 %s 次（测"框不中时无副作用"）'
                     % (big, hit, ('%.0f%%' % (100.0 * hit / big)) if big else 'n/a',
                        dm.get('band_small', -1), dm.get('band_blank', -1)),
                     warn=(big > 0 and hit == 0))
            # ④ 撤销/重做**按键次数**：A 层随机 key 手势里真的发出去了多少次
            #    （只计数、不判一致；一致性由确定性的 C10 往返判定 —— 随机按键无法配对）
            rep.soft('A 层④撤销/重做按键次数（6 类图形合计）',
                     'ctrl+z ×%s / ctrl+y ×%s（本轮 6000 次随机手势里实际发出的次数）'
                     % (sum(int((_d(r) or {}).get('undo_presses', 0) or 0)
                            for _s, r in _fuzz_all()),
                        sum(int((_d(r) or {}).get('redo_presses', 0) or 0)
                            for _s, r in _fuzz_all())))
            # ⑤ 多图形 fuzz：逐类变化率 + pump 倍率（等待只影响节奏，判据一字未改）
            rep.soft('A 层⑤多图形 fuzz 逐类读数',
                     '；'.join(_brief(sid, _d(rec) or {})
                               for sid, rec in _fuzz_all()[1:]),
                     warn=any(((_d(r) or {}).get('n_errors') or 0) > 0
                              for _s, r in _fuzz_all()))

    # ======================= B/C：fit（Ctrl+F 裁白边） =======================
    if backend == 'qt' and layer in ('B', 'C', 'all'):
        fit_cfg = dict(CFG_DEFAULT['fit'])

        def _fit(sc, dpi, force=False):
            return runner.get('fit', {'QT_SCALE_FACTOR': str(sc)},
                              {'figsize': fit_cfg['figsize'], 'dpi': dpi}, force=force)

        for (sc, dpi) in FIT_COMBOS:           # 先把 4 组跑出来（缓存命中则秒回）
            _fit(sc, dpi)
        _note_facts(rep, _fit(2, 200))

        if layer in ('B', 'all'):
            def pred_b1(force=False):
                rec = _fit(2, 200, force)
                d = _d(rec)
                if rec.get('rc') != 0 or not d:
                    return False, _fail_detail(rec)
                rw, rh = d['ratio']
                # 「不缩半」的正确判据：这个 bug 的特征是 ratio 被 dpr/dpi 放大/缩小一个
                # **整倍数**（2×/4×），所以用 [0.80,1.20] 抓倍数级错误，同时要求 Ctrl+F
                # **确实动了**（任一维变化 ≥0.5%）。首跑写成"两维都必须缩"是过度规定：
                # 小图（3×2.2in）里 11pt 主标题 + tick + 图例让 tight bbox 需要更多高度，
                # _fit_to_content 按设计会**加**边距（首跑 ratio_h=1.055 是对的，不是 bug）。
                # ★ 精度口径不在这里，而在 B3：4 组（scale×dpi）ratio 极差 ≤0.08。
                ran = abs(rw - 1.0) > 0.005 or abs(rh - 1.0) > 0.005
                ok = 0.80 <= rw <= 1.20 and 0.80 <= rh <= 1.20 and ran
                det = ('ratio_w=%.3f ratio_h=%.3f（%.2f×%.2f → %.2f×%.2f in）'
                       'QT_SCALE_FACTOR=%s dpi=%s dpr=%.2f 可用区=%s 画布=%s'
                       % (rw, rh, d['inches_before'][0], d['inches_before'][1],
                          d['inches_after'][0], d['inches_after'][1], d.get('scale'),
                          d.get('dpi'), d.get('dpr', -1), d.get('avail'), d.get('canvas')))
                if not ran:
                    det += ' ← Ctrl+F 没有生效（画布未缩短），先确认快捷键链路'
                return ok, det

            ok, det = pred_b1(False)
            rep.check('B1', 'B', '200% 缩放+dpi200 下 Ctrl+F 不缩半', ok, det)

            def pred_b2(force=False):
                rec = _fit(2, 200, force)
                d = _d(rec)
                if rec.get('rc') != 0 or not d:
                    return False, _fail_detail(rec)
                tb, fb = d.get('title_box'), d.get('figbbox')
                sep = d.get('sep', -99.0)
                # sep = 标题下沿 − 面板上沿：**标题在面板上方时为正**（≈0 = 贴着、负 = 真重叠）。
                # 首跑这里符号写反了，把正常版面误判成"重叠"——判据修好，引擎一个字没动。
                ok = bool(d.get('title_inside')) and sep >= -2.0
                det = ('主标题外框=%s（画布 %s）完整在画布内=%s；标题下沿−面板上沿=%.1fpx'
                       % ([round(v, 1) for v in tb] if tb else None,
                          [round(v, 1) for v in fb] if fb else None,
                          d.get('title_inside'), sep))
                return ok, det

            ok, det = pred_b2(False)
            rep.check('B2', 'B', 'Ctrl+F 后主标题不被裁 / 不与内容重叠', ok, det)

            def pred_b3(force=False):
                vals, bad = [], []
                for (sc, dpi) in FIT_COMBOS:
                    rec = _fit(sc, dpi, force)
                    d = _d(rec)
                    if rec.get('rc') != 0 or not d:
                        bad.append('scale=%s dpi=%s: %s' % (sc, dpi, _fail_detail(rec)))
                        continue
                    vals.append((sc, dpi, d['ratio'][0], d['ratio'][1]))
                if bad:
                    return False, ' | '.join(bad)
                rws = [v[2] for v in vals]
                rhs = [v[3] for v in vals]
                sw, sh = max(rws) - min(rws), max(rhs) - min(rhs)
                ok = sw <= 0.08 and sh <= 0.08
                det = ('4 组 ratio_w=%s 极差=%.3f；ratio_h=%s 极差=%.3f'
                       '（与 dpi / 屏幕缩放解耦）'
                       % ([round(v, 3) for v in rws], sw, [round(v, 3) for v in rhs], sh))
                return ok, det

            ok, det = pred_b3(False)
            rep.check('B3', 'B', 'Ctrl+F 与 dpi/缩放解耦（4 组一致）', ok, det)

        # ---- huge（超大 figure / 小屏）----
        huge_env = {'QT_SCALE_FACTOR': '2'}
        huge_cfg = dict(CFG_DEFAULT['huge'])

        def _huge(force=False):
            return runner.get('huge', huge_env, huge_cfg, force=force)

        if layer in ('B', 'all'):
            def pred_b4(force=False):
                rec = _huge(force)
                d = _d(rec)
                if rec.get('rc') != 0 or not d:
                    return False, _fail_detail(rec)
                av, cv = d.get('avail'), d.get('canvas')
                dpr, dpi = d.get('dpr', 1.0), d.get('dpi', 100.0)
                req, aft = d['inches_req'], d['inches_after']
                if not av or not cv:
                    return False, '拿不到屏幕可用区/画布尺寸: avail=%s canvas=%s' % (av, cv)
                need = (req[0] * dpi / dpr > av[0] + 2) or (req[1] * dpi / dpr > av[1] + 2)
                ok_canvas = cv[0] <= av[0] * 1.05 + 2 and cv[1] <= av[1] * 1.05 + 2
                ok_only_shrink = aft[0] <= req[0] * 1.001 + 0.01 and aft[1] <= req[1] * 1.001 + 0.01
                ok_shrank = (not need) or (aft[0] < req[0] - 0.01 or aft[1] < req[1] - 0.01)
                ok = ok_canvas and ok_only_shrink and ok_shrank
                det = ('请求 %.0f×%.0fin @dpi%s；可用区=%s 画布=%s dpr=%.2f；开窗后 %.2f×%.2fin；'
                       '需要缩放=%s 只缩不放=%s 该缩就缩=%s'
                       % (req[0], req[1], dpi, av, cv, dpr, aft[0], aft[1],
                          need, ok_only_shrink, ok_shrank))
                return ok, det

            ok, det = pred_b4(False)
            rep.check('B4', 'B', '超大 figure 被缩到屏内（只缩不放）', ok, det)

            def pred_b5(force=False):
                rec = _huge(force)
                d = _d(rec)
                if rec.get('rc') != 0 or not d:
                    return False, _fail_detail(rec)
                o, a = d['inches_open'], d['inches_after']
                dw, dh = abs(a[0] - o[0]), abs(a[1] - o[1])
                ok = dw <= 0.05 and dh <= 0.05
                hints = _log_lines(rec, r'钳制|超出可用区|已自动缩放')
                det = ('开窗 figsize=%.2f×%.2fin → 事件泵 2s 后 %.2f×%.2fin（Δ=%.3f/%.3f in）；'
                       '引擎提示: %s' % (o[0], o[1], a[0], a[1], dw, dh, (hints[-2:] or '无')))
                return ok, det

            ok, det = pred_b5(False)
            rep.check('B5', 'B', '事件泵后 figsize 不被静默改写', ok, det)

        if layer in ('C', 'all'):
            def pred_c1(force=False):
                rec = _huge(force)
                d = _d(rec)
                if rec.get('rc') != 0 or not d:
                    return False, _fail_detail(rec)
                fr, av = d.get('frame'), d.get('avail')
                if not fr or not av:
                    return False, '拿不到窗口 frameGeometry/可用区: frame=%s avail=%s' % (fr, av)
                x, y, w, h = fr
                ok_frame = (w <= av[0] * 1.15 and h <= av[1] * 1.15
                            and x > -av[0] * 0.15 and y > -av[1] * 0.15)
                req, aft = d['inches_req'], d['inches_after']
                ar_req = req[0] / req[1] if req[1] else 0.0
                ar_aft = aft[0] / aft[1] if aft[1] else 0.0
                ok_ar = abs(ar_aft - ar_req) <= 0.02 * ar_req if ar_req else False
                ok = ok_frame and ok_ar
                det = ('窗口 frameGeometry=%s vs 可用区=%s（未越屏=%s）；'
                       '长宽比 请求 %.3f → 实得 %.3f（守恒=%s）'
                       % (fr, av, ok_frame, ar_req, ar_aft, ok_ar))
                return ok, det

            ok, det = pred_c1(False)
            rep.check('C1', 'C', '小屏开大图：窗口未越屏 + 长宽比守恒', ok, det)

    # ======================= B：drag =======================
    if backend == 'qt' and layer in ('B', 'all'):
        def pred_b6(force=False):
            rec = runner.get('drag', None, None, force=force)
            d = _d(rec)
            if rec.get('rc') != 0 or not d:
                return False, _fail_detail(rec)
            req, got = d['req'], d['got']
            dw, dh = abs(got[0] - req[0]), abs(got[1] - req[1])
            ok = dw <= 0.03 and dh <= 0.03 and bool(d.get('geom_changed'))
            det = ('请求相对位移 (%.3f, %.3f) → 实得 (%.3f, %.3f)；|Δ|=(%.3f, %.3f)；几何变化=%s'
                   % (req[0], req[1], got[0], got[1], dw, dh, d.get('geom_changed')))
            return ok, det

        ok, det = pred_b6(False)
        rep.check('B6', 'B', '真拖拽位移符合预期（±0.03）', ok, det)

    # ======================= C6 / C8（qt）=======================
    if backend == 'qt' and layer in ('C', 'all'):
        def pred_c6(force=False):
            rec = runner.get('qtwin', None, None, force=force)
            d = _d(rec)
            if rec.get('rc') != 0 or not d:
                return False, _fail_detail(rec)
            ok = (bool(d.get('visible')) and int(d.get('winid', 0)) != 0
                  and 'qt' in str(d.get('backend', '')).lower())
            det = ('后端=%s(%s) 窗口可见=%s winId=%s 标题=%r 平台=%s 屏幕=%s 可用区=%s '
                   'dpr=%.2f isActiveWindow=%s'
                   % (d.get('backend'), d.get('qt_api'), d.get('visible'), d.get('winid'),
                      d.get('title'), d.get('platform'), d.get('screen'), d.get('avail'),
                      d.get('dpr', -1), d.get('active')))
            return ok, det

        ok, det = pred_c6(False)
        rep.check('C6', 'C', 'Qt 真 native 窗口存在且可见', ok, det)
        _note_facts(rep, runner.get('qtwin', None, None, force=False))

        def pred_c8(force=False):
            rec = runner.get('snap', None, None, force=force)
            d = _d(rec)
            if rec.get('rc') != 0 or not d:
                return False, _fail_detail(rec)
            after, a0x0 = d['after_x0'], d['ax0_x0']
            ok = abs(after - a0x0) <= 0.002 and abs(d['got_dx'] - d['req_dx']) <= 0.02
            det = ('ax0.x0=%.4f；ax1 拖前 x0=%.4f 请求 dx=%.4f（目标缝隙 %.4f < SNAP_TOL %.4f）'
                   '→ 运动中(松手前) x0=%.4f、settle 后=%.4f、松手后 x0=%.4f，'
                   '与 ax0.x0 残差=%.4f；共投递 motion %s 个'
                   % (a0x0, d['before_x0'], d['req_dx'], d['gap'], d['snap_tol'],
                      d.get('ghost_mid_x0', -1.0), d.get('ghost_end_x0', -1.0),
                      after, abs(after - a0x0), d.get('motions')))
            return ok, det

        ok, det = pred_c8(False)
        rep.check('C8', 'C', '多面板吸附把 0.005 缝隙收成 0', ok, det)

        # ---- C2/C3/C4：框选 / Ctrl 多选 / 空白清空（用户 2026-10-12 目击的盲区）----
        # 刻意**不写成循环**：断言 id 必须字面量出现在 rep.check() 里，台账机器守卫
        # （_ledger_audit）才查得出"声明了但没实现"。
        def _sel(force=False):
            return runner.get('select', None, None, force=force)

        def _sel_stage(key, want, force=False):
            rec = _sel(force)
            d = _d(rec)
            if rec.get('rc') != 0 or not d:
                return False, _fail_detail(rec)
            got = d.get(key)
            logs = _log_lines(rec, r'框选 \d+ 个面板|多选拖拽')
            det = ('%s 实测 = %s（期望 %s）；四阶段 stage0=%s → stage1=%s → stage2=%s → '
                   'stage3=%s；引擎日志 %s'
                   % (key, got, want, d.get('stage0'), d.get('stage1'), d.get('stage2'),
                      d.get('stage3'), (logs[:3] or '无')))
            return (got == want), det

        def _sel_retry(key, want):
            ok_, det_ = _sel_stage(key, want, False)
            if not ok_:                      # 失败自动重跑该场景 1 次
                ok_, det_ = _sel_stage(key, want, True)
                det_ += '（已重跑 1 次）'
            return ok_, det_

        ok, det = _sel_retry('stage1', ['ax0', 'ax1'])
        rep.check('C2', 'C', '框选（完整包住上排 2 个）→ 选中的正是那 2 个', ok, det)
        ok, det = _sel_retry('stage2', ['ax0', 'ax1', 'ax2'])
        rep.check('C3', 'C', 'Ctrl+点击下排左面板 → 加选到 3 个（实测 event.modifiers 路径）', ok, det)
        ok, det = _sel_retry('stage3', [])
        rep.check('C4', 'C', '空白纯点击 → 清空选中', ok, det)

        # ---- C10：撤销/重做往返一致（真窗口真按键；判据理由见 _sc_undoredo 与文件头）----
        def _ur(force=False):
            return runner.get('undoredo', None, None, force=force)

        def pred_c10(force=False):
            rec = _ur(force)
            d = _d(rec)
            if rec.get('rc') != 0 or not d:
                return False, _fail_detail(rec)
            trips = d.get('trips') or []
            tol = d.get('tol', 0.002)
            n, moved = d.get('n_trips', 0), d.get('moved_n', 0)
            # 判据（不放松）：**每一次"确实被驱动"（拖动真的产生了位移）的往返，都必须
            # 端到端一致**（undo 回 G0、redo 复现 G1，容差 0.002）；并且本轮至少要有
            # **1 次**干净往返，否则等于没测（防止退化成恒真）。
            driven = [t for t in trips if t.get('moved')]
            ok_n = sum(1 for t in driven
                       if t['d_undo'] <= tol and t['d_redo'] <= tol)
            ok = (len(driven) >= 1) and (ok_n == len(driven))
            det = ('已驱动 %d/%d 次往返（拖动真的产生位移的），其中一致 %d 次；'
                   '最大偏差 %.4f（容差 %.4f）；逐轮 (moved, d_undo, d_redo)=%s'
                   % (len(driven), n, ok_n, d.get('max_dev', 9.9), tol,
                      [(bool(t.get('moved')), round(t['d_undo'], 4),
                        round(t['d_redo'], 4)) for t in trips]))
            _und = [t['i'] for t in trips if not t.get('moved')]
            if _und:
                # 已知 harness 限制（2026-10-12，未定位）：本夹具里第 2 次及以后的拖动，
                # matplotlib 自己报 `inaxes=None`（引擎因此无反应、无日志），而 `_bbox`
                # 说该点在面板内 ⇒ **测试侧坐标系异常**，本轮拿不到可信读数。
                # 按"不造弱判据"口径：这一部分**不计入一致**、也**不冒充引擎缺陷**。
                det += ('；★ 未驱动的轮次 %s（matplotlib 报 inaxes=None，属测试侧坐标系'
                        '异常，未定位；这部分不判一致、也不冒充缺陷）' % _und)
            if d.get('probes'):
                det += ('；诊断探针（每次往返前后各做一次普通拖动）moved=%s'
                        % d.get('probe_moved'))
            return ok, det

        ok, det = pred_c10(False)
        if not ok:                       # 失败自动重跑该场景 1 次（与其它 B/C 断言一致）
            ok, det = pred_c10(True)
            det += '（已重跑 1 次）'
        _dU1 = _d(_ur())
        if ok and _dU1.get('suspected'):
            _susU = (_dU1.get('active_n'), _dU1.get('active_samples'))
            ok, det = pred_c10(True)
            _dU2 = _d(_ur())
            det += ('；★ WARN: external interaction suspected（首轮 %s/%s 次采样窗口被激活 '
                    '→ 已重跑一次；重跑 %s/%s）'
                    % (_susU[0], _susU[1], _dU2.get('active_n', -1),
                       _dU2.get('active_samples', -1)))
        elif _dU1.get('suspected'):
            det += ('；★ WARN: external interaction suspected（%s/%s 次采样窗口被激活）'
                    % (_dU1.get('active_n'), _dU1.get('active_samples')))
        rep.check('C10', 'C', '撤销/重做往返一致（undo 回上一态 + redo 复现）', ok, det)

        # ---- C11~C20：5 种新图形的确定性补测（t22 多图形扩展）----
        # 每条判据都建立在"实测 vs 声明/预期"上，**不是**"跑完没崩就算过"：
        #   * C11/C16：只动目标（其余面板偏差 ≤0.002 = 没动）；
        #   * C13：实测 bbox vs matplotlib 自己声明的 GridSpec 几何（≤0.002）；
        #   * C14/C18：Ctrl+F 的"不缩半"口径**与 B1 完全相同**（每维 ratio∈[0.80,1.20]
        #     且确实生效），只是换到不规则版面 / constrained_layout 上再验一遍；
        #   * C15：colorbar 轴数用**引擎自己的** `_is_colorbar_ax` 判定；
        #   * C19/C20：横排几何关系与长标题的**画布内包含**（0.5px 容差，同 B2）。
        def _figs_d(sid, force=False):
            rec = runner.get(sid, None, None, force=force)
            d = _d(rec)
            if rec.get('rc') != 0 or not d:
                return None, _fail_detail(rec)
            if d.get('error'):
                return None, '子进程内异常: %s' % d['error']
            f = d.get('figs') or {}
            if not f:
                return None, '子进程没交回 figs 测量块（%s）' % sid
            return f, ''

        def _figs_retry(fn):
            ok_, det_ = fn(False)
            if not ok_:
                ok_, det_ = fn(True)
                det_ += '（已重跑 1 次）'
            return ok_, det_

        def _num(v, dflt=0.0):
            """取数值（None/非数 → dflt）。**不能用 `or`**：0.0 是合法读数
            （"完全没动"就是 0.0），`0.0 or 9.0` 会把它误判成 9.0。"""
            try:
                return float(dflt) if v is None else float(v)
            except (TypeError, ValueError):
                return float(dflt)

        def _one_line(log):
            """兼容旧调用点：转调模块级 `_one_line_log()`。"""
            return _one_line_log(log)

        def pred_c11(force=False):
            f, err = _figs_d('figs_grid4x4', force)
            if f is None:
                return False, err
            drag = f.get('drag') or {}
            n_open, n_after = f.get('n_axes_open'), f.get('n_axes_after')
            tags = f.get('tags_after') or []
            ok = (n_open == 16 and n_after == 16 and len(set(tags)) == 16
                  and drag.get('changed') == [drag.get('target')]
                  and _num(drag.get('dev_target')) > 0.005
                  and _num(drag.get('max_dev_others'), 9.0) <= TOUCH_TOL)
            det = ('开窗 %s 面板 / 1000 次手势后 %s 面板（去重 %d 个 tag）；拖 %s 请求 0.03 → '
                   '实测位移 %.4f；几何变化的面板=%s（期望只有目标）；其余面板最大偏差 %.4f'
                   '（≤0.002 视为没动）'
                   % (n_open, n_after, len(set(tags)), drag.get('target'),
                      _num(drag.get('dev_target'), -1), drag.get('changed'),
                      _num(drag.get('max_dev_others'), -1)))
            return ok, det

        ok, det = _figs_retry(pred_c11)
        rep.check('C11', 'C', '4×4=16 面板：手势后 16 个都在 + 拖 1 个只动该面板', ok, det)

        def pred_c12(force=False):
            f, err = _figs_d('figs_grid4x4', force)
            if f is None:
                return False, err
            mq = f.get('marquee') or {}
            got = mq.get('got')
            want = sorted(mq.get('wanted') or [])
            ok = bool(mq.get('cleared')) and got == want and len(want) == 4
            det = ('框选完整包住左上 2×2（%s）：起点=%s、外扩=%.3f、被同时"完整框住"的其它面板=%s'
                   ' → 选中=%s（期望 %s）；框前空白点击清空选中=%s'
                   % (mq.get('wanted'), mq.get('start'),
                      float(mq.get('margin') or -1), mq.get('fully_other'), got, want,
                      mq.get('cleared')))
            return ok, det

        ok, det = _figs_retry(pred_c12)
        rep.check('C12', 'C', '16 面板：框选完整包住左上 2×2 → 选中的正是那 4 个', ok, det)

        def pred_c13(force=False):
            f, err = _figs_d('figs_gridspec', force)
            if f is None:
                return False, err
            st = f.get('structure') or {}
            md = st.get('max_dev')
            dh = float(st.get('declared_h_ratio') or 0.0)
            dw = float(st.get('declared_w_ratio') or 0.0)
            ok = (st.get('n') == 4 and md is not None and float(md) <= TOUCH_TOL
                  and bool(st.get('irregular')) and dh >= 1.5 and dw >= 1.5)
            det = ('开窗后 %s 个面板；逐面板实测 bbox vs matplotlib 声明的 GridSpec 几何的偏差 '
                   '=%s，最大 %.6f（容差 0.002）；声明行高比=%.3f 列宽比=%.3f（≥1.5 ⇒ fixture '
                   '确实非等分，判据不恒真）'
                   % (st.get('n'), st.get('per_dev'),
                      float(md) if md is not None else -1, dh, dw))
            return ok, det

        ok, det = _figs_retry(pred_c13)
        rep.check('C13', 'C', 'GridSpec 不规则版面：实测几何与 mpl 声明一致（≤0.002）', ok, det)

        def _ctrlf(sid, want_n, force=False):
            """Ctrl+F 的**通用**判据（与 B1 同一口径）：每维 ratio∈[CTRLF_LO,CTRLF_HI]
            且 **确实生效**（`ran=True`）。`figs_gridspec`/`figs_tight1x3` 用这一条。"""
            f, err = _figs_d(sid, force)
            if f is None:
                return False, err
            rc = f.get('ratio_check') or {}
            b = rc.get('before') or [0.0, 0.0]
            a = rc.get('after') or [0.0, 0.0]
            r = rc.get('ratio') or [0.0, 0.0]
            ok = (CTRLF_LO <= r[0] <= CTRLF_HI and CTRLF_LO <= r[1] <= CTRLF_HI
                  and bool(rc.get('ran')) and rc.get('n_axes_after_cf') == want_n)
            det = ('Ctrl+F：%.2f×%.2fin → %.2f×%.2fin（ratio=%.3f / %.3f，判据 [%.2f,%.2f]'
                   ' 与 B1 同一口径）；确实生效=%s；面板数 %s → %s；引擎原文=%s'
                   % (b[0], b[1], a[0], a[1], r[0], r[1], CTRLF_LO, CTRLF_HI,
                      rc.get('ran'), want_n, rc.get('n_axes_after_cf'),
                      _one_line(rc.get('fit_log'))))
            return ok, det

        def _ctrlf_branch(sid, want_n, force=False):
            """取数 + 调用**模块级**判据 `_ctrlf_branch_ok()`（判据本体见那里与文件头）。"""
            f, err = _figs_d(sid, force)
            if f is None:
                return False, err
            return _ctrlf_branch_ok(f.get('ratio_check'), want_n)

        ok, det = _figs_retry(lambda force=False: _ctrlf('figs_gridspec', 4, force))
        rep.check('C14', 'C', 'GridSpec 图：Ctrl+F 不缩半（每维 ratio∈[0.80,1.20]）', ok, det)

        def pred_c15(force=False):
            f, err = _figs_d('figs_colorbar', force)
            if f is None:
                return False, err
            drag = f.get('drag') or {}
            cb = _num(drag.get('max_dev_others'), 9.0)
            ok = (f.get('cbar_n') == 1 and drag.get('changed') == [drag.get('target')]
                  and _num(drag.get('dev_target')) > 0.005 and cb <= TOUCH_TOL)
            det = ('colorbar 轴数（引擎 `_is_colorbar_ax` 多信号判定）=%s（期望 1）；'
                   '拖主面板（请求 -0.05,-0.04）→ 主面板实测位移 %.4f、几何变化的面板=%s；'
                   'colorbar 轴最大偏差 %.4f（≤0.002 ⇒ 没被连带改）'
                   % (f.get('cbar_n'), _num(drag.get('dev_target'), -1),
                      drag.get('changed'), cb))
            return ok, det

        ok, det = _figs_retry(pred_c15)
        rep.check('C15', 'C', '主面板+colorbar：colorbar 轴数=1 + 拖主面板 colorbar 不动', ok, det)

        def pred_c16(force=False):
            f, err = _figs_d('figs_colorbar', force)
            if f is None:
                return False, err
            cd = f.get('cbar_drag') or {}
            ok = (_num(cd.get('dev_target')) > 0.005
                  and _num(cd.get('dev_main'), 9.0) <= TOUCH_TOL)
            det = ('拖 colorbar 本体（请求 -0.03,+0.05）：colorbar 实测位移 %.4f（应 >0.005）；'
                   '主面板最大偏差 %.4f（应 ≤0.002）；几何变化的面板=%s'
                   % (_num(cd.get('dev_target'), -1), _num(cd.get('dev_main'), -1),
                      cd.get('changed')))
            return ok, det

        ok, det = _figs_retry(pred_c16)
        rep.check('C16', 'C', '主面板+colorbar：拖 colorbar 本体 → only 它变、主面板不变', ok, det)

        def pred_c17(force=False):
            f, err = _figs_d('figs_clayout', force)
            if f is None:
                return False, err
            ls = f.get('layout_stable') or {}
            ds, dg = ls.get('d_size'), ls.get('d_geom')
            ok = (ds is not None and float(ds) <= TOUCH_TOL and dg is not None
                  and float(dg) <= TOUCH_TOL and not (ls.get('changed') or []))
            det = ('开窗后两次采样（间隔 500ms 事件泵）：figsize %s → %s（Δ=%.6f）；全部面板几何'
                   '最大偏差 %.6f（≤0.002）；几何变化的面板=%s'
                   % (ls.get('size1'), ls.get('size2'),
                      float(ds) if ds is not None else -1,
                      float(dg) if dg is not None else -1, ls.get('changed')))
            return ok, det

        ok, det = _figs_retry(pred_c17)
        rep.check('C17', 'C', 'constrained_layout：开窗后布局稳定（两次采样一致）', ok, det)

        ok, det = _figs_retry(lambda force=False: _ctrlf_branch('figs_clayout', 4, force))
        rep.check('C18', 'C', 'constrained_layout 图：Ctrl+F 不缩半（每维 ratio∈[0.80,1.20]；'
                              '且"确实生效"或引擎明确回报"内容已贴边、无需适配"）', ok, det)

        def pred_c19(force=False):
            f, err = _figs_d('figs_tight1x3', force)
            if f is None:
                return False, err
            row = f.get('row') or {}
            rows = row.get('rows') or []
            dy = row.get('dy')
            ok = (len(rows) == 3 and dy is not None and float(dy) <= TOUCH_TOL
                  and bool(row.get('x_inc')))
            det = ('面板 %d 个；三面板 y0 极差 %.6f（"同一行"判据 ≤0.002）；x 递增且互不重叠=%s；'
                   '逐面板 [tag,y0,x0,x1]=%s'
                   % (len(rows), float(dy) if dy is not None else -1, row.get('x_inc'),
                      [[r['tag'], r['y0'], r['x0'], r['x1']] for r in rows]))
            return ok, det

        ok, det = _figs_retry(pred_c19)
        rep.check('C19', 'C', '1×3 tight_layout：三面板同一行 / x 递增 / 互不重叠', ok, det)

        def pred_c20(force=False):
            f, err = _figs_d('figs_tight1x3', force)
            if f is None:
                return False, err
            tc = f.get('title_check') or {}
            ok = bool(tc.get('inside_before')) and bool(tc.get('inside_after'))
            det = ('长中文 suptitle 外框：Ctrl+F 前=%s（画布 %s，在内=%s）；Ctrl+F 后=%s'
                   '（画布 %s，在内=%s）（容差 0.5px，同 B2）'
                   % (tc.get('before'), tc.get('figbbox_before'), tc.get('inside_before'),
                      tc.get('after'), tc.get('figbbox_after'), tc.get('inside_after')))
            return ok, det

        ok, det = _figs_retry(pred_c20)
        rep.check('C20', 'C', '1×3 图：长中文 suptitle 完整在画布内（Ctrl+F 前后）', ok, det)

    # ======================= C7（tk 冒烟）=======================
    if backend == 'tk' and layer in ('C', 'all'):
        def pred_c7(force=False):
            rec = runner.get('tkwin', None, None, force=force)
            d = _d(rec)
            if rec.get('rc') != 0 or not d:
                return False, _fail_detail(rec)
            req = d.get('req') or [0, 0]
            got = d.get('got') or [0, 0]
            dw, dh = abs(got[0] - req[0]), abs(got[1] - req[1])
            ok = (bool(d.get('mapped')) and bool(d.get('viewable'))
                  and dw <= 0.05 and dh <= 0.05 and bool(d.get('geom_changed')))
            det = ('后端=%s 控件=%s 已映射=%s 可见=%s 屏幕=%s 标题=%r 焦点对象=%s；'
                   'event_generate 真拖拽 请求(%.3f,%.3f) → 实得(%.3f,%.3f) '
                   '|Δ|=(%.3f,%.3f) 几何变化=%s'
                   % (d.get('backend'), d.get('widget'), d.get('mapped'), d.get('viewable'),
                      d.get('screen'), d.get('title'), d.get('focus'),
                      req[0], req[1], got[0], got[1], dw, dh, d.get('geom_changed')))
            return ok, det

        ok, det = pred_c7(False)
        rep.check('C7', 'C', 'Tk 真窗口 + event_generate 真拖拽', ok, det)
        _note_facts(rep, runner.get('tkwin', None, None, force=False))

        # ---- C9：Tk 下 huge 的长宽比守恒（★ 已知缺陷 pin，见 KNOWN_DEFECTS）----
        # 判据与 C1 完全一致（长宽比偏差 ≤2%），一个字都不放松。
        # 不做"失败自动重试"：它要开一个 30×22in 的大窗，重试代价高；而它是**确定性
        # 算术**（英寸比值），不是竞态测量，重试没有额外信息量。
        def pred_c9(force=False):
            rec = runner.get('huge', None, None, force=force)
            d = _d(rec)
            if rec.get('rc') != 0 or not d:
                return False, _fail_detail(rec)
            req, aft = d.get('inches_req'), d.get('inches_after')
            if not req or not aft or not req[1] or not aft[1]:
                return False, '拿不到 inches_req/inches_after: %s / %s' % (req, aft)
            ar_req, ar_aft = req[0] / req[1], aft[0] / aft[1]
            dev = abs(ar_aft - ar_req) / ar_req
            g = d.get('tk_geom_after') or {}
            det = ('请求 %.2f×%.2fin（比值 %.3f）→ 实得 %.2f×%.2fin（比值 %.3f）偏差 %.1f%%；'
                   '窗口客户区=%s 屏幕=%s 可用区=%s（未映射时 dpr=%s）；fit trace=%s'
                   % (req[0], req[1], ar_req, aft[0], aft[1], ar_aft, 100.0 * dev,
                      g.get('wh'), d.get('screen'), d.get('avail'), d.get('dpr'),
                      (d.get('trace') or {}).get('fit')))
            return (dev <= 0.02), det

        ok, det = pred_c9(False)
        _pin_c9 = KNOWN_DEFECTS.get('C9')
        if _pin_c9 and not ok:
            rep.pin('C9', 'C', 'Tk 下 huge 长宽比偏差 ≤2%', det, _pin_c9)
        elif _pin_c9 and ok:
            rep.check('C9', 'C', 'Tk 下 huge 长宽比偏差 ≤2%', False,
                      '★ pin 过期：本缺陷已修（现在通过）→ 请移除 KNOWN_DEFECTS["C9"]，'
                      'C9 转硬断言。%s' % det)
        else:
            rep.check('C9', 'C', 'Tk 下 huge 长宽比偏差 ≤2%', ok, det)

    # ======================= 软检查（只警告）=======================
    if backend == 'qt' and layer in ('B', 'C', 'all'):
        df = _d(runner.get('fit', {'QT_SCALE_FACTOR': '2'},
                           {'figsize': CFG_DEFAULT['fit']['figsize'], 'dpi': 200}, force=False))
        if df:
            rw = df['ratio'][0]
            rep.soft('Ctrl+F 画布缩短', 'ratio_w=%.3f（<1 表示真的缩短了）' % rw, warn=rw >= 1.0)
        dq = _d(runner.get('qtwin', None, None, force=False))
        if dq:
            rep.soft('窗口焦点', 'isActiveWindow=%s（真窗口点击可能因抢焦点丢失，属正常）'
                     % dq.get('active'))
        dh = _d(runner.get('hoverkeys', None, None, force=False))
        if dh:
            rep.soft('hover 依赖快捷键(+/-) 观察项',
                     '已投递 plus/minus，子进程正常返回（本版只观测，不判失败）')
        else:
            rh = runner.get('hoverkeys', None, None, force=False)
            if rh and (rh.get('rc') != 0 or _d(rh).get('error')):
                rep.soft('hover 依赖快捷键(+/-) 观察项',
                         '子进程异常/中止（rc=%s）→ 疑似真实缺陷，待放行后复现确认；本版不判失败。'
                         'stderr 尾: %s' % (rh.get('rc'), _tail(rh.get('stderr'), 6)), warn=True)


# ===========================================================================
# 静态自审（AST 级；只读源码文本，不 import pyplot、不开窗）
# ===========================================================================
# 把"人工 grep 过一遍"的三条纪律变成每次 --selftest 都会跑的机器守卫：
#   ① **不劫持鼠标**：源码里不许出现 pyautogui / pynput / win32api / SendInput /
#      SetCursorPos / mouse_event / keybd_event / QCursor.setPos / ctypes；
#   ② **不阻塞**：不许有 plt.show() / exec_() / mainloop() / input()；
#   ③ **注入只走驱动类**：QTest.* 只能出现在 QtDriver 里、event_generate 只能出现在
#      TkDriver 里 —— 这条把"事件注入机制"从注释变成可执行约束。
# 另加台账自洽（声明的断言/场景必须都真的被实现）。
_BANNED_INPUT = ('pyautogui', 'pynput', 'win32api', 'win32con', 'SendInput',
                 'SetCursorPos', 'mouse_event', 'keybd_event', 'setPos', 'ctypes')
_BLOCKING_ATTRS = ('exec_', 'exec', 'mainloop')
_ALLOW_EXTRA_CHECKS = {'X0'}        # main() 里"装配抛异常"的兜底行，不是验收断言


def _static_audit(text=None):
    """对源码文本做静态自审，返回 (problems, notes)。"""
    import ast as _ast
    problems, notes = [], []
    if text is None:
        with open(HERE, 'r', encoding='utf-8') as f:
            text = f.read()
    if '\r' in text:
        problems.append('文件里出现 CR（要求纯 LF，避免混排）')
    if text.startswith('\ufeff'):
        problems.append('文件以 BOM 开头（要求无 BOM UTF-8）')
    try:
        tree = _ast.parse(text)
    except SyntaxError as e:
        return ['语法错：%s' % e], notes

    # ---- ① 禁止的输入模拟（只看真代码：docstring/字符串常量天然不会命中）----
    hits = []
    for node in _ast.walk(tree):
        if isinstance(node, _ast.Attribute) and node.attr.split('.')[-1] in _BANNED_INPUT:
            hits.append((node.lineno, 'Attribute .%s' % node.attr))
        elif isinstance(node, _ast.Name) and node.id in _BANNED_INPUT:
            hits.append((node.lineno, 'Name %s' % node.id))
        elif isinstance(node, _ast.Import):
            for a in node.names:
                if a.name.split('.')[0] in _BANNED_INPUT:
                    hits.append((node.lineno, 'import %s' % a.name))
        elif isinstance(node, _ast.ImportFrom):
            if (node.module or '').split('.')[0] in _BANNED_INPUT:
                hits.append((node.lineno, 'from %s import ...' % node.module))
            for a in node.names:
                if a.name in _BANNED_INPUT:
                    hits.append((node.lineno, 'from %s import %s' % (node.module, a.name)))
    if hits:
        problems.append('出现移动真实光标/系统输入的 API：%s' % hits[:5])
    else:
        notes.append('① 无移动光标 / 系统输入 API（%d 个禁用名一个都没出现在代码里）'
                     % len(_BANNED_INPUT))

    # ---- ② 阻塞调用 / 自建事件循环 ----
    blk = []
    for node in _ast.walk(tree):
        if not isinstance(node, _ast.Call):
            continue
        f = node.func
        if isinstance(f, _ast.Name) and f.id == 'input':
            blk.append((node.lineno, 'input()'))
        if isinstance(f, _ast.Attribute) and f.attr in _BLOCKING_ATTRS:
            blk.append((node.lineno, '.%s()' % f.attr))
        if isinstance(f, _ast.Attribute) and f.attr == 'show':
            base = f.value
            nm = base.id if isinstance(base, _ast.Name) else getattr(base, 'attr', '')
            if nm in ('plt', 'pyplot'):
                blk.append((node.lineno, '%s.show()' % nm))
    if blk:
        problems.append('出现阻塞调用/自建事件循环：%s' % blk[:5])
    else:
        notes.append('② 无 plt.show() / exec_() / mainloop() / input()（不会卡住窗口循环）')

    # ---- ③ 事件注入只准在驱动类里 ----
    ranges = {}
    for node in _ast.walk(tree):
        if isinstance(node, _ast.ClassDef) and node.name in ('QtDriver', 'TkDriver'):
            ranges[node.name] = (node.lineno, node.end_lineno or node.lineno)
    for cls in ('QtDriver', 'TkDriver'):
        if cls not in ranges:
            problems.append('找不到驱动类 %s' % cls)
    if 'QtDriver' in ranges and 'TkDriver' in ranges:
        q0, q1 = ranges['QtDriver']
        t0, t1 = ranges['TkDriver']
        bad_inj, n_qtest = [], 0
        for node in _ast.walk(tree):
            if isinstance(node, _ast.Attribute) and node.attr == 'QTest':
                n_qtest += 1
                if not (q0 <= node.lineno <= q1):
                    bad_inj.append((node.lineno, 'QTest 出现在 QtDriver 之外'))
            if isinstance(node, _ast.Attribute) and node.attr == 'event_generate':
                if not (t0 <= node.lineno <= t1):
                    bad_inj.append((node.lineno, 'event_generate 出现在 TkDriver 之外'))
        if bad_inj:
            problems.append('事件注入越界：%s' % bad_inj[:5])
        elif n_qtest == 0:
            problems.append('一次 QTest 都没用到？注入机制可能被改坏了')
        else:
            notes.append('③ 事件注入全在驱动类内（QTest 引用 %d 处；event_generate 均在 '
                         'TkDriver 内）' % n_qtest)
    return problems, notes


def _ledger_audit():
    """台账自洽：声明的断言/场景必须真的被实现，覆盖表不许引用不存在的断言。"""
    problems, notes = [], []
    with open(HERE, 'r', encoding='utf-8') as f:
        text = f.read()
    declared = [c[0] for c in CHECKS]
    if len(declared) != len(set(declared)):
        problems.append('CHECKS 里有重复 id')
    impl = set(re.findall(r"rep\.check\(\s*'([A-Z][0-9]+)'", text)) - _ALLOW_EXTRA_CHECKS
    if set(declared) != impl:
        problems.append('声明与实现不一致：仅声明=%s 仅实现=%s'
                        % (sorted(set(declared) - impl), sorted(impl - set(declared))))
    else:
        notes.append('台账：%d 条硬断言「声明 ↔ rep.check 实现」一一对应' % len(declared))
    scen_ids = set(s[0] for s in SCENARIOS)
    # `preflight` 是开跑前的投递自检，不是报告里的场景 ⇒ 台账口径里排除它
    if scen_ids != set(_CHILD_FUNCS) - {'preflight'}:
        problems.append('SCENARIOS 与 _CHILD_FUNCS 不一致：%s vs %s'
                        % (sorted(scen_ids), sorted(_CHILD_FUNCS)))
    else:
        notes.append('台账：%d 个场景「声明 ↔ 实现」一一对应' % len(scen_ids))
    for cid, lyr, desc, bk, retry in CHECKS:
        if bk not in ('qt', 'tk'):
            problems.append('断言 %s 的后端 %r 非法' % (cid, bk))
    cmap_ids = set()
    for name, how in C_MAP:
        cmap_ids |= set(re.findall(r'\b([ABC][0-9]+)\b', how))
    unknown = cmap_ids - set(declared)
    if unknown:
        problems.append('C 层覆盖表引用了不存在的断言：%s' % sorted(unknown))
    else:
        notes.append('台账：C 层 %d 类覆盖表引用的断言都存在（%s）'
                     % (len(C_MAP), ', '.join(sorted(cmap_ids))))
    return problems, notes


# ===========================================================================
# --selftest：断言机器自检（**合成数据**，不开窗、不 import pyplot）
# ===========================================================================
# 目的只有一个：证明父进程那套"读数 → 判定"的机器接对了，而且**不是恒真的**。
# 它**绝不是**真跑证据 —— 合成数据是人写的，真窗口行为必须靠 --backend qt/tk 实跑。
#
#   * 基础态：合成一组"理想真跑"读数 → 27 条硬断言应全过；
#   * 变异态：逐个把某条断言对应的物理量弄坏 → **那一条必须 FAIL**（证明断言
#     真的在看那个量，而不是写了个恒真的谓词）。
class _FakeRunner:
    """喂合成读数给 assemble（不开窗、不起子进程）。"""

    def __init__(self, data, backend='qt'):
        self.data = data
        self.backend = backend
        self.spawns = 0

    def get(self, scenario, env=None, cfg=None, force=False):
        env = env or {}
        cfg = cfg or CFG_DEFAULT.get(scenario, {})
        key = scenario
        if scenario == 'fit':
            key = 'fit_%s_%s' % (env.get('QT_SCALE_FACTOR'), cfg.get('dpi'))
        return {'rc': 0, 'stdout': '', 'stderr': '', 'secs': 0.1,
                'scenario': scenario, 'backend': self.backend,
                'env': env, 'cfg': cfg, 'timeout': False, 'data': self.data.get(key)}


def _synth_tk_huge(distorted=True):
    """C9 的合成数据：Tk 下 huge 的**实测**形态。

    ``distorted=True`` → 复现 2026-10-12 的缺陷（27.98×10.275in，比值 2.723）；
    ``False`` → 修复后的形态（27.9818×20.52in，比值守恒）。
    """
    return {
        'scenario': 'huge', 'backend': 'TkAgg', 'error': None,
        'inches_req': [30.0, 22.0], 'inches_open': [27.9818, 20.52],
        'inches_after': ([27.98, 10.275] if distorted else [27.9818, 20.52]),
        'canvas_open': [2798, 2052],
        'canvas': ([2798, 1027] if distorted else [2798, 2052]),
        'screen': [3840, 2160], 'avail': [3840, 2160], 'dpr': 1.0, 'dpi': 100.0,
        'frame': None,
        'tk_geom_open': {'xy': [209, 254], 'wh': [200, 200], 'mapped': False,
                         'viewable': False, 'screen': [3840, 2160]},
        'tk_geom_after': {'xy': [209, 254],
                          'wh': ([5596, 2119] if distorted else [2800, 2052]),
                          'mapped': True, 'viewable': True, 'screen': [3840, 2160]},
        'trace': {'resize': [], 'resize_n': 0,
                  'fit': [{'before': [30.0, 22.0], 'after': [27.9818, 20.52]}]},
        'py': '3.10.19', 'mpl': '3.10.8',
    }


def _synth_data():
    good_axes = [[0.10, 0.55, 0.36, 0.34], [0.55, 0.55, 0.36, 0.34],
                 [0.10, 0.10, 0.36, 0.34], [0.55, 0.10, 0.36, 0.34]]
    geom = [v for b in good_axes for v in b]
    fit = {}
    for sc, dpi in FIT_COMBOS:
        fit['fit_%s_%s' % (sc, dpi)] = {
            'inches_before': [3.0, 2.2], 'inches_after': [2.5, 1.9],
            'ratio': [0.833, 0.864], 'title_inside': True,
            'title_box': [5.0, 150.0, 250.0, 200.0], 'figbbox': [300.0, 220.0],
            'gap': 8.0, 'sep': 8.0, 'canvas': [250, 190], 'scale': str(sc), 'dpi': float(dpi),
            'dpr': float(sc), 'screen': [1920, 1080], 'avail': [1920, 1032],
            'backend': 'QtAgg', 'figsize_open': [3.0, 2.2],
        }
    fit['monkey'] = {
        'n': 1000, 'errors': [], 'n_errors': 0, 'landed': 150, 'landed_ratio': 0.15,
        'sel_landed': 30, 'sel_landed_ratio': 0.03,
        'kind': 'sub2x2', 'pump_scale': 1.0, 'secs_gestures': 248.0,
        'band_big': 40, 'band_hit': 38, 'band_small': 8, 'band_blank': 8,
        'undo_presses': 5, 'redo_presses': 5,
        'tags_before': ['ax0', 'ax1', 'ax2', 'ax3'],
        'tags_after': ['ax0', 'ax1', 'ax2', 'ax3'],
        'geom_after': geom, 'content_counts': [3, 3, 3, 3],
        'canvas': [900, 650], 'dpr': 1.0, 'min_size': 0.02, 'backend': 'QtAgg',
    }
    fit['huge'] = {
        'inches_req': [30.0, 22.0], 'inches_open': [13.0, 9.5],
        'inches_after': [13.0, 9.5], 'canvas_open': [900, 480],
        'canvas': [900, 480], 'screen': [960, 540], 'avail': [960, 516],
        'dpr': 1.0, 'dpi': 100.0, 'frame': [10, 10, 920, 500], 'backend': 'QtAgg',
    }
    fit['drag'] = {
        'before': [0.10, 0.55, 0.36, 0.34], 'after': [0.021, 0.44, 0.36, 0.34],
        'req': [-0.08, -0.11], 'got': [-0.079, -0.109], 'geom_changed': True,
        'canvas': [800, 600], 'dpr': 1.0, 'backend': 'QtAgg',
    }
    fit['snap'] = {
        'ax0_x0': 0.10, 'target_x0': 0.105, 'gap': 0.005, 'before_x0': 0.60,
        'after_x0': 0.1002, 'req_dx': -0.495, 'got_dx': -0.4998,
        'snap_tol': 0.006, 'canvas': [800, 600], 'dpr': 1.0, 'backend': 'QtAgg',
    }
    fit['qtwin'] = {
        'visible': True, 'winid': 12345, 'backend': 'QtAgg', 'qt_api': 'PyQt5',
        'title': 'mpltweak — 拖动调布局', 'active': False, 'platform': 'windows',
        'screen': [1920, 1080], 'avail': [1920, 1032], 'dpr': 2.0,
        'py': '3.10.19', 'mpl': '3.10.8',
    }
    fit['hoverkeys'] = {'done': True, 'backend': 'QtAgg', 'dpr': 1.0}
    fit['select'] = {
        'stage0': [], 'stage1': ['ax0', 'ax1'], 'stage2': ['ax0', 'ax1', 'ax2'],
        'stage3': [], 'canvas': [900, 650], 'dpr': 2.0, 'backend': 'QtAgg',
    }
    fit['undoredo'] = {
        'n_trips': 2, 'ok_n': 2, 'moved_n': 2, 'tol': 0.002, 'max_dev': 0.0,
        'canvas': [800, 600], 'dpr': 2.0, 'backend': 'QtAgg',
        'trips': [
            {'i': 0, 'tag': 'ax0', 'moved': True, 'd_undo': 0.0, 'd_redo': 0.0,
             'G0': [0.08, 0.55, 0.38, 0.34, 0.56, 0.55, 0.38, 0.34],
             'G1': [0.02, 0.47, 0.38, 0.34, 0.56, 0.55, 0.38, 0.34],
             'G2': [0.08, 0.55, 0.38, 0.34, 0.56, 0.55, 0.38, 0.34],
             'G3': [0.02, 0.47, 0.38, 0.34, 0.56, 0.55, 0.38, 0.34]},
            {'i': 1, 'tag': 'ax1', 'moved': True, 'd_undo': 0.0, 'd_redo': 0.0,
             'G0': [0.08, 0.55, 0.38, 0.34, 0.56, 0.55, 0.38, 0.34],
             'G1': [0.08, 0.55, 0.38, 0.34, 0.63, 0.43, 0.38, 0.34],
             'G2': [0.08, 0.55, 0.38, 0.34, 0.56, 0.55, 0.38, 0.34],
             'G3': [0.08, 0.55, 0.38, 0.34, 0.63, 0.43, 0.38, 0.34]},
        ],
    }
    fit['tkwin'] = {
        'mapped': True, 'viewable': True, 'backend': 'TkAgg',
        'screen': [3840, 2160], 'avail': [3840, 2160], 'widget': 'Canvas',
        'focus': 'None', 'title': 'mpltweak — 拖动调布局',
        'req': [-0.06, -0.08], 'got': [-0.059, -0.079], 'geom_changed': True,
        'canvas': [800, 600], 'dpr': 1.0, 'py': '3.10.19', 'mpl': '3.10.8',
    }
    # ---- ⑫~⑯ 五种新图形（t22）：合成"理想真跑"读数 ----
    #   fuzz 部分（n/errors/tags_after/geom_after/...）喂 A1/A2；
    #   figs 部分（结构/只动目标/Ctrl+F/标题）喂 C11~C20。
    def _grid_geom(nrow, ncol, x0=0.055, y0=0.06, w=0.2, h=0.2, gx=0.235, gy=0.23):
        g = []
        for r in range(nrow):
            for c in range(ncol):
                g.extend([x0 + c * gx, y0 + r * gy, w, h])
        return g

    def _fuzz_synth(kind, n_axes, geom, figs, pump_scale=0.25):
        return {
            'n': 1000, 'errors': [], 'n_errors': 0, 'kind': kind,
            'pump_scale': pump_scale, 'secs_gestures': 70.0,
            'landed': 150, 'landed_ratio': 0.15, 'sel_landed': 40,
            'sel_landed_ratio': 0.04,
            'band_big': 200, 'band_hit': 120, 'band_small': 100, 'band_blank': 100,
            'undo_presses': 30, 'redo_presses': 28,
            'active_n': 0, 'active_samples': 100, 'suspected': False,
            'tags_before': ['ax%d' % i for i in range(n_axes)],
            'tags_after': ['ax%d' % i for i in range(n_axes)],
            'geom_after': geom, 'content_counts': [3] * n_axes,
            'canvas': [1200, 900], 'dpr': 2.0, 'min_size': 0.02,
            'backend': 'QtAgg', 'figs': figs,
        }

    _g16 = _grid_geom(4, 4)
    fit['figs_grid4x4'] = _fuzz_synth('grid4x4', 16, _g16, {
        'kind': 'grid4x4', 'n_axes_open': 16, 'n_axes_after': 16,
        'tags_open': ['ax%d' % i for i in range(16)],
        'tags_after': ['ax%d' % i for i in range(16)],
        'size_open': [12.0, 9.0], 'size_after': [12.0, 9.0], 'cbar_n': None,
        'structure': None, 'layout_stable': None,
        'drag': {'note': '拖 ax5', 'target': 'ax5', 'dev_target': 0.0298,
                 'changed': ['ax5'], 'changed_others': [], 'max_dev_others': 0.0},
        'marquee': {'wanted': ['ax0', 'ax1', 'ax4', 'ax5'],
                    'got': ['ax0', 'ax1', 'ax4', 'ax5'], 'margin': 0.008,
                    'start': [0.039, 0.947], 'fully_other': [], 'cleared': True},
        'cbar_drag': None, 'ratio_check': None, 'title_check': None, 'row': None,
        'meta': {'kind': 'grid4x4', 'grid': [4, 4]},
    })
    fit['figs_gridspec'] = _fuzz_synth('gridspec', 4,
                                       [0.125, 0.59, 0.775, 0.29,
                                        0.125, 0.29, 0.5, 0.24,
                                        0.63, 0.29, 0.27, 0.24,
                                        0.125, 0.11, 0.775, 0.14], {
        'kind': 'gridspec', 'n_axes_open': 4, 'n_axes_after': 4,
        'tags_open': ['ax0', 'ax1', 'ax2', 'ax3'],
        'tags_after': ['ax0', 'ax1', 'ax2', 'ax3'],
        'size_open': [10.0, 7.5], 'size_after': [10.0, 7.5], 'cbar_n': None,
        'structure': {'n': 4,
                      'per_dev': {'ax0': 0.0, 'ax1': 0.0, 'ax2': 0.0, 'ax3': 0.0},
                      'max_dev': 0.0, 'declared_h_ratio': 2.0,
                      'declared_w_ratio': 2.87, 'irregular': True},
        'layout_stable': None, 'drag': None, 'marquee': None, 'cbar_drag': None,
        'ratio_check': {'before': [10.0, 7.5], 'after': [8.3, 6.2],
                        'ratio': [0.83, 0.8267], 'ran': True,
                        'n_axes_after_cf': 4},
        'title_check': None, 'row': None,
        'meta': {'kind': 'gridspec', 'gridspec': 'height_ratios=[2,1,1]'},
    })
    fit['figs_colorbar'] = _fuzz_synth('colorbar', 2,
                                       [0.08, 0.12, 0.62, 0.76,
                                        0.78, 0.12, 0.06, 0.76], {
        'kind': 'colorbar', 'n_axes_open': 2, 'n_axes_after': 2,
        'tags_open': ['ax0', 'cb0'], 'tags_after': ['ax0', 'cb0'],
        'size_open': [10.0, 6.5], 'size_after': [10.0, 6.5], 'cbar_n': 1,
        'structure': None, 'layout_stable': None,
        'drag': {'note': '拖主面板', 'target': 'ax0', 'dev_target': 0.05,
                 'changed': ['ax0'], 'changed_others': [], 'max_dev_others': 0.0},
        'marquee': None,
        'cbar_drag': {'note': '拖 colorbar 本体', 'target': 'cb0',
                      'dev_target': 0.0298, 'dev_main': 0.0, 'changed': ['cb0']},
        'ratio_check': None, 'title_check': None, 'row': None,
        'meta': {'kind': 'colorbar', 'main_tag': 'ax0', 'cbar_tag': 'cb0'},
    })
    fit['figs_clayout'] = _fuzz_synth('clayout', 4,
                                      [0.06, 0.53, 0.42, 0.41,
                                       0.54, 0.53, 0.42, 0.41,
                                       0.06, 0.06, 0.42, 0.41,
                                       0.54, 0.06, 0.42, 0.41], {
        'kind': 'clayout', 'n_axes_open': 4, 'n_axes_after': 4,
        'tags_open': ['ax0', 'ax1', 'ax2', 'ax3'],
        'tags_after': ['ax0', 'ax1', 'ax2', 'ax3'],
        'size_open': [10.0, 7.0], 'size_after': [10.0, 7.0], 'cbar_n': None,
        'structure': None,
        'layout_stable': {'size1': [10.0, 7.0], 'size2': [10.0, 7.0],
                          'd_size': 0.0, 'd_geom': 0.0, 'changed': []},
        'drag': None, 'marquee': None, 'cbar_drag': None,
        # 真实形态（2026-10-12 实跑 + 离线同 fixture 复现）：constrained_layout 图
        # 的轴几乎贴满画布 ⇒ 引擎走"内容已贴边、无需适配"分支 ⇒ ratio=1.000、ran=False。
        # 合成基线按**实测形态**造，这样 C18 的分支感知守卫在自检里就被真正走到。
        'ratio_check': {'before': [10.0, 7.0], 'after': [10.0, 7.0],
                        'ratio': [1.0, 1.0], 'ran': False,
                        'fit_log': ['[mpltweak] fit: 收到快捷键',
                                    '[mpltweak] fit: 内容已贴边，无需适配（10.00x7.00 in）'],
                        'n_axes_after_cf': 4},
        'title_check': None, 'row': None,
        'meta': {'kind': 'clayout', 'layout': 'constrained'},
    })
    fit['figs_tight1x3'] = _fuzz_synth('tight1x3', 3,
                                       [0.05, 0.18, 0.28, 0.60,
                                        0.36, 0.18, 0.28, 0.60,
                                        0.67, 0.18, 0.28, 0.60], {
        'kind': 'tight1x3', 'n_axes_open': 3, 'n_axes_after': 3,
        'tags_open': ['ax0', 'ax1', 'ax2'],
        'tags_after': ['ax0', 'ax1', 'ax2'],
        'size_open': [12.0, 4.5], 'size_after': [12.0, 4.5], 'cbar_n': None,
        'structure': None, 'layout_stable': None, 'drag': None, 'marquee': None,
        'cbar_drag': None,
        'ratio_check': {'before': [12.0, 4.5], 'after': [11.2, 4.2],
                        'ratio': [0.9333, 0.9333], 'ran': True,
                        'n_axes_after_cf': 3},
        'title_check': {'before': [60.0, 380.0, 1140.0, 430.0],
                        'after': [55.0, 355.0, 1065.0, 402.0],
                        'inside_before': True, 'inside_after': True,
                        'figbbox_before': [1200.0, 450.0],
                        'figbbox_after': [1120.0, 420.0]},
        'row': {'rows': [{'tag': 'ax0', 'y0': 0.18, 'y1': 0.78, 'x0': 0.05, 'x1': 0.33},
                         {'tag': 'ax1', 'y0': 0.18, 'y1': 0.78, 'x0': 0.36, 'x1': 0.64},
                         {'tag': 'ax2', 'y0': 0.18, 'y1': 0.78, 'x0': 0.67, 'x1': 0.95}],
                'dy': 0.0, 'x_inc': True},
        'meta': {'kind': 'tight1x3', 'nrow': 1},
    })
    return fit


# 变异矩阵: (目标断言 id, 后端, 施加的破坏, 人话说明)
_MUTATIONS = [
    ('A1', 'qt', lambda d: d['monkey'].update(errors=['TypeError: boom'], n_errors=1),
     '一次手势抛异常'),
    ('A2', 'qt', lambda d: d['monkey'].update(tags_after=['ax0', 'ax1', 'ax2']),
     '乱按后面板丢了 1 个'),
    ('B1', 'qt', lambda d: d['fit_2_200'].update(ratio=[0.50, 0.50]),
     'Ctrl+F 把画布缩了一半'),
    ('B2', 'qt', lambda d: d['fit_2_200'].update(title_inside=False),
     '主标题被裁到画布外'),
    ('B3', 'qt', lambda d: d['fit_1_200'].update(ratio=[0.70, 0.70]),
     'dpi=200 那档 ratio 漂移 0.13'),
    ('B4', 'qt', lambda d: d['huge'].update(canvas=[2000, 1200]),
     '超大画布没被缩到屏内'),
    ('B5', 'qt', lambda d: d['huge'].update(inches_after=[13.4, 9.9]),
     '事件泵后 figsize 被静默改了 0.4in'),
    ('B6', 'qt', lambda d: d['drag'].update(got=[0.0, 0.0]),
     '真拖拽没产生位移'),
    ('C1', 'qt', lambda d: d['huge'].update(frame=[0, 0, 2000, 1200]),
     '窗口 frameGeometry 越出可用区'),
    ('C6', 'qt', lambda d: d['qtwin'].update(visible=False),
     'Qt 窗口不可见'),
    ('C7', 'tk', lambda d: d['tkwin'].update(mapped=False),
     'Tk 窗口未映射'),
    ('C8', 'qt', lambda d: d['snap'].update(after_x0=0.1055),
     '吸附没把 0.005 缝隙收成 0'),
    # ↓ 首跑暴露的 4 条"测试侧判据"修正，必须证明改完仍然会咬人（不是放宽成恒真）
    ('A2', 'qt', lambda d: d['monkey'].update(
        geom_after=[0.08, 0.55, 0.38, 0.34, 1.5, 0.55, 0.38, 0.34,
                    0.08, 0.09, 0.38, 0.34, 0.56, 0.09, 0.38, 0.34]),
     '面板被推到完全离开画布（新判据仍要抓得住）'),
    ('A2', 'qt', lambda d: d['monkey'].update(content_counts=[3, 3, 0, 3]),
     '有面板内容被清空'),
    ('B1', 'qt', lambda d: d['fit_2_200'].update(ratio=[2.0, 2.0]),
     'Ctrl+F 反而把画布放大了 2 倍（反向倍数错误也要抓）'),
    ('C2', 'qt', lambda d: d['select'].update(stage1=['ax0']),
     '框选只选中 1 个（= 用户看到的现象）'),
    ('C3', 'qt', lambda d: d['select'].update(stage2=['ax0', 'ax1']),
     'Ctrl 点选没加选（event.modifiers 路径失效）'),
    ('C4', 'qt', lambda d: d['select'].update(stage3=['ax0']),
     '空白点击没清空选中'),
    ('C10', 'qt', lambda d: d['undoredo']['trips'][0].update(d_undo=0.05),
     'undo 没回到上一态'),
    ('C10', 'qt', lambda d: d['undoredo']['trips'][1].update(d_redo=0.05),
     'redo 没复现拖动结果'),
    ('C10', 'qt', lambda d: [t.update(moved=False) for t in d['undoredo']['trips']],
     '一次往返都没驱动成功（防判据退化成恒真）'),
    # ↓ t22 多图形扩展：新断言每条都要证明"谓词会咬人"（否则等于没测）
    ('A1', 'qt', lambda d: d['figs_grid4x4'].update(errors=['ValueError: boom'], n_errors=1),
     '新图形（4×4）一次手势抛异常 → A1 聚合必须抓到'),
    ('A2', 'qt', lambda d: d['figs_gridspec'].update(tags_after=['ax0', 'ax1']),
     '新图形（GridSpec）乱按后丢了 2 个面板 → A2 聚合必须抓到'),
    ('C11', 'qt', lambda d: d['figs_grid4x4']['figs']['drag'].update(changed=['ax5', 'ax9']),
     '拖一个面板却把另一个也带走了（"只动目标"判据）'),
    ('C11', 'qt', lambda d: d['figs_grid4x4']['figs'].update(n_axes_after=15),
     '16 面板掉成 15 个'),
    ('C12', 'qt', lambda d: d['figs_grid4x4']['figs']['marquee'].update(
        got=['ax0', 'ax1']),
     '16 面板上框选只选中 2 个（多面板命中/路由不对）'),
    ('C13', 'qt', lambda d: d['figs_gridspec']['figs']['structure'].update(max_dev=0.02),
     'GridSpec 版面被拉平/挪位 0.02（超过 0.002 容差）'),
    ('C14', 'qt', lambda d: d['figs_gridspec']['figs']['ratio_check'].update(
        ratio=[0.50, 0.50]),
     'GridSpec 图 Ctrl+F 缩了一半'),
    ('C15', 'qt', lambda d: d['figs_colorbar']['figs']['drag'].update(
        changed=['ax0', 'cb0'], max_dev_others=0.05),
     '拖主面板把 colorbar 轴一起带走了'),
    ('C15', 'qt', lambda d: d['figs_colorbar']['figs'].update(cbar_n=0),
     'colorbar 轴没被引擎的 _is_colorbar_ax 认出来'),
    ('C16', 'qt', lambda d: d['figs_colorbar']['figs']['cbar_drag'].update(dev_main=0.04),
     '拖 colorbar 把主面板也带走了'),
    ('C17', 'qt', lambda d: d['figs_clayout']['figs']['layout_stable'].update(
        d_geom=0.02, changed=['ax0']),
     'constrained_layout 开窗后两次采样不一致（布局自己在动）'),
    ('C18', 'qt', lambda d: d['figs_clayout']['figs']['ratio_check'].update(
        ratio=[2.0, 2.0]),
     'constrained_layout 图 Ctrl+F 反而放大 2 倍'),
    ('C18', 'qt', lambda d: d['figs_clayout']['figs']['ratio_check'].update(
        fit_log=[], ran=False),
     'constrained_layout 图 ratio=1.000 但**引擎一行都没有**（按键根本没到引擎）'
     '—— 这一条专门证明 C18 的分支感知守卫不空虚'),
    ('C19', 'qt', lambda d: d['figs_tight1x3']['figs']['row'].update(dy=0.05),
     '1×3 三面板不在同一行'),
    ('C20', 'qt', lambda d: d['figs_tight1x3']['figs']['title_check'].update(
        inside_after=False),
     '长中文标题 Ctrl+F 后被裁到画布外'),
]


def selftest():
    exp_qt = [c[0] for c in CHECKS if c[3] == 'qt']
    exp_tk = [c[0] for c in CHECKS if c[3] == 'tk']
    say('=' * 78)
    say('断言机器自检（--selftest）—— **合成数据**，不开窗、不 import pyplot')
    say('这不是真跑证据；真窗口行为只能靠 --backend qt / tk 实跑')
    say('=' * 78)
    saved_say = say

    def quiet(msg=''):
        LOG_LINES.append(msg)

    problems = []
    audit_notes = []
    # ---- 0) 静态自审 + 台账自审（免弹窗；把人工 grep 变成机器守卫）----
    for label, fn in (('静态自审', _static_audit), ('台账自审', _ledger_audit)):
        try:
            probs, notes = fn()
        except BaseException as e:
            probs, notes = ['%s 自身抛异常: %s: %s' % (label, type(e).__name__, e)], []
        problems += ['[%s] %s' % (label, p) for p in probs]
        audit_notes += ['%s：%s' % (label, n) for n in notes]
    try:
        with open(HERE, 'r', encoding='utf-8') as f:
            _src = f.read()
    except OSError as e:
        _src, _ = '', problems.append('[静态自审] 读不到自己: %s' % e)
    for why, bad_src in (
            ('加入 pyautogui 调用', _src + '\nimport pyautogui\npyautogui.click(1, 2)\n'),
            ('加入 plt.show() 阻塞调用', _src + '\nplt.show()\n'),
            ('改成 CRLF 混排', _src.replace('\n', '\r\n', 1))):
        bp, _bn = _static_audit(bad_src)
        if bp:
            audit_notes.append('自审灵敏度：%s → 被抓到（%s）' % (why, bp[0][:52]))
        else:
            problems.append('[静态自审] 变异「%s」没被抓住 → 守卫失效' % why)
    try:
        globals()['say'] = quiet

        # ---- 1) 基础态：27 条硬断言应全过（C9 已是硬断言、无 pin）----
        rep = Report('qt')
        assemble(_FakeRunner(_synth_data(), 'qt'), rep, 'all')
        got_ids = [r['id'] for r in rep.rows]
        if sorted(got_ids) != sorted(exp_qt):
            problems.append('qt 断言集合不符: %r != %r' % (got_ids, exp_qt))
        for r in rep.failed:
            problems.append('基础态下 %s 本应通过却 FAIL: %s' % (r['id'], r['detail']))

        rep_tk = Report('tk')
        _tk_base = _synth_data()
        # 2026-10-12：C9 的 pin 已随 t20 移除 ⇒ 基线用**修复后**的形态（硬断言应通过）
        _tk_base['huge'] = _synth_tk_huge(distorted=False)
        assemble(_FakeRunner(_tk_base, 'tk'), rep_tk, 'all')
        if [r['id'] for r in rep_tk.rows] != ['C7', 'C9']:
            problems.append('tk 断言集合应为 [C7, C9]，实得 %r' % [r['id'] for r in rep_tk.rows])
        for r in rep_tk.failed:
            problems.append('基础态(tk)下 %s 本应通过却 FAIL' % r['id'])

        # ---- 2) 谓词灵敏度：逐条破坏 → 那一条必须 FAIL ----
        n_ok = 0
        for cid, bk, mutate, why in _MUTATIONS:
            data = _synth_data()
            mutate(data)
            repm = Report(bk)
            assemble(_FakeRunner(data, bk), repm, 'all')
            hit = [r for r in repm.rows if r['id'] == cid]
            if not hit:
                problems.append('变异 %s：找不到该断言' % cid)
                continue
            if hit[0]['ok']:
                problems.append('变异 %s（%s）后断言仍然 PASS → **谓词恒真，等于没测**'
                                % (cid, why))
            else:
                n_ok += 1
                others = [r['id'] for r in repm.failed if r['id'] != cid]
                saved_say('  [OK ] 变异 %s（%s）→ 该条 FAIL；连带 FAIL: %s'
                          % (cid, why, (others or '无')))
        # ---- 3) 软指标灵敏度：把"框选 40 次全不中"喂进去 → ③ 必须 WARN ----
        #    （否则"框选路径没被测到"这件事在日志里仍然不可见，等于白修）
        data = _synth_data()
        data['monkey'].update(band_big=40, band_hit=0)
        repm = Report('qt')
        assemble(_FakeRunner(data, 'qt'), repm, 'A')
        wsoft = [s for s in repm.softs if '框选' in s['label']]
        if not wsoft:
            problems.append('找不到软指标③（框选命中率）→ 指标没接上')
        elif not wsoft[0]['warn']:
            problems.append('软指标③ 对「框选 40 次全不中」没有 WARN → 指标失效')
        else:
            audit_notes.append('软指标灵敏度：框选 40 次全不中 → ③ 正确 WARN')

        # ---- 4) pin 机器三向自检（专治"无主 pin"）----
        # 2026-10-12：C9 的**真 pin 已随 t20 移除**；这里改用**合成 pin**继续守卫这套机器
        # 本身（否则"pin 机制"在真 pin 移除后就再没人测了）。
        _STUB_PIN = {'task': 'selftest-only', 'title': '合成缺陷（自检用）',
                     'evidence': '无（合成）', 'lines': '无（合成）',
                     'remove_when': '自检结束即恢复'}
        _tk_fixed = _synth_data()
        _tk_fixed['huge'] = _synth_tk_huge(distorted=False)   # t20 修复后的形态
        _tk_broken = _synth_data()
        _tk_broken['huge'] = _synth_tk_huge(distorted=True)   # 缺陷形态
        _saved_pin = KNOWN_DEFECTS.get('C9')
        KNOWN_DEFECTS['C9'] = dict(_STUB_PIN)
        try:
            # (a) 缺陷形态 + pin → 必须记 [PIN] 且**不计失败**
            rep_a = Report('tk')
            assemble(_FakeRunner(_tk_broken, 'tk'), rep_a, 'all')
            c9a = [r for r in rep_a.rows if r['id'] == 'C9']
            if not (c9a and c9a[0].get('pinned')) or rep_a.failed:
                problems.append('pin(a)：缺陷形态下没记成 [PIN] / 仍被计入失败')
            else:
                audit_notes.append('pin 灵敏度(a)：缺陷形态 → [PIN]、不计失败 ✅')
            # (b) 缺陷已修但 pin 还在 → 必须报红（否则 pin 会永远留在仓库里）
            rep_b = Report('tk')
            assemble(_FakeRunner(_tk_fixed, 'tk'), rep_b, 'all')
            c9b = [r for r in rep_b.rows if r['id'] == 'C9']
            if not (c9b and (not c9b[0]['ok']) and 'pin' in (c9b[0]['detail'] or '')):
                problems.append('pin(b)：修复形态但 pin 未移除 → 没有报红（会出现无主 pin）')
            else:
                audit_notes.append('pin 灵敏度(b)：修复形态 + pin 未移除 → 正确报红要求移除 ✅')
        finally:
            if _saved_pin is None:
                KNOWN_DEFECTS.pop('C9', None)
            else:
                KNOWN_DEFECTS['C9'] = _saved_pin
        # (c) 移除 pin 后，**同一判据**在修复形态下必须转绿（C9 变硬断言）
        rep_c = Report('tk')
        assemble(_FakeRunner(_tk_fixed, 'tk'), rep_c, 'all')
        c9c = [r for r in rep_c.rows if r['id'] == 'C9']
        if not (c9c and c9c[0]['ok']) or rep_c.failed:
            problems.append('pin(c)：移除 pin 后 C9 在修复形态下没有转绿（应为硬断言）')
        else:
            audit_notes.append('pin 灵敏度(c)：移除 pin 后 C9 是硬断言且通过 ✅')
    finally:
        globals()['say'] = saved_say

    saved_say('')
    saved_say('  静态自审 / 台账自审（免弹窗，机器守卫）：')
    for n in audit_notes:
        saved_say('    · %s' % n)
    saved_say('')
    saved_say('  基础态：qt %d 条 / tk %d 条硬断言应全过（C9 已是硬断言、无 pin）✅'
              % (len(exp_qt), len(exp_tk)))
    saved_say('  谓词灵敏度：%d/%d 条变异被捕获' % (n_ok, len(_MUTATIONS)))
    saved_say('')
    if problems:
        for p in problems:
            saved_say('  [FAIL] %s' % p)
        saved_say('SELFTEST FAILED（%d 个问题）' % len(problems))
        return 1
    saved_say('SELFTEST ALL PASS（断言机器接对了，且谓词对各物理量敏感）')
    return 0


# ===========================================================================
# --dry-list：免弹窗地列出场景与断言
# ===========================================================================
def _machine_load():
    """开跑前的机器负载自检（**只读**，用于证明"这次是干净运行"）。

    为什么需要：本套件对时序敏感（HiDPI dpr 自检、吸附残差、拖拽落位都跟时间有关），
    机器被别的重活占满时读数会被污染。所以每次运行都留一条可追溯的负载记录：
      * 其它 ``python`` 进程数（``tasklist``；拿不到就 -1）；
      * 一段**固定**整数循环的耗时（同机同版本可比：越小越空）。数值本身**不作判据**。
    """
    t0 = time.perf_counter()
    s = 0
    for i in range(300000):
        s += i * i % 7
    ms = (time.perf_counter() - t0) * 1000.0
    n = -1
    try:
        out = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq python.exe', '/NH'],
                             capture_output=True, text=True, timeout=20).stdout
        n = len([l for l in out.splitlines() if l.strip().lower().startswith('python')])
    except Exception:
        n = -1
    return n, ms, s


def _src_state():
    """记录本次跑的是**哪一版 src**。

    C1 的修复可能同时改动 toolbox.py（船长已另派），所以每次实跑都必须把"测的是哪版"
    写进日志与报告，否则事后说不清结论属于哪一版代码。
    """
    import hashlib
    parts = []
    for name in ('toolbox.py', 'writeback.py', 'apply.py', 'verify.py'):
        p = os.path.join(SRC, 'mpltweak', name)
        try:
            with open(p, 'rb') as f:
                parts.append('%s=%s' % (name, hashlib.md5(f.read()).hexdigest()[:10]))
        except OSError:
            parts.append('%s=?' % name)
    git = 'git=?'
    try:
        head = subprocess.run(['git', 'rev-parse', '--short', 'HEAD'], cwd=ROOT,
                              capture_output=True, text=True, timeout=20).stdout.strip()
        dirty = subprocess.run(['git', 'status', '--porcelain', '--', 'src/'], cwd=ROOT,
                               capture_output=True, text=True, timeout=20).stdout
        n = len([l for l in dirty.splitlines() if l.strip()])
        git = 'git HEAD=%s, src 已改文件=%d' % (head or '?', n)
    except Exception:
        pass
    return ' '.join(parts) + ' | ' + git


def dry_list():
    say('=' * 78)
    say('真窗口自动化测试（t16 / #4）—— 场景与断言清单（--dry-list）')
    say('**本命令不 import pyplot、不开任何窗口**')
    say('=' * 78)
    say('')
    say('【事件注入机制】Qt: QTest.mousePress / mouseMove / mouseRelease / keyClick；'
        'Tk: widget.event_generate')
    say('【禁止项】pyautogui / pynput / win32api / SendInput / mouse_event / '
        'keybd_event / QCursor.setPos —— 本文件均无（grep 可证）')
    say('【打扰控制】子进程内 rcParams["figure.raise_window"]=False + 窗口 '
        'raise_()/activateWindow() 置 no-op + WA_ShowWithoutActivating'
        '（只 patch 测试进程，不动 src/）')
    say('')
    say('场景（%d 个）：' % len(SCENARIOS))
    say('  %-10s %-5s %-17s %-26s %s' % ('id', '后端', '环境档', '覆盖的 C 场景', '说明'))
    for sid, bk, envd, cmap, desc in SCENARIOS:
        say('  %-10s %-5s %-17s %-26s %s' % (sid, bk, envd, cmap, desc))
    say('')
    say('硬断言（%d 条）：' % len(CHECKS))
    for cid, lyr, desc, bk, retry in CHECKS:
        say('  [%s] %-3s %-4s %s%s' % (lyr, cid, bk, desc,
                                       '（失败自动重试 1 次）' if retry else ''))
    say('')
    say('C 层 %d 类场景覆盖（验收要求逐类至少 1 条断言）：' % len(C_MAP))
    for name, how in C_MAP:
        say('  %-18s → %s' % (name, how))
    say('')
    say('软检查（只打警告、不判失败）：')
    for s in SOFT_ITEMS:
        say('  * %s' % s)
    say('')
    say('pytest 收集：本文件**不被收集**（pyproject.toml 的 python_files 只含 '
        'test_suites.py / test_hygiene.py）；不进 CI（需要真实显示器）。')
    mpl_loaded = 'matplotlib.pyplot' in sys.modules
    say('本次 --dry-list 是否 import 了 pyplot: %s（必须为 False）' % mpl_loaded)
    say('')
    ok = not mpl_loaded
    say('DRY-LIST %s' % ('OK' if ok else 'FAILED'))
    return 0 if ok else 1


# ===========================================================================
# main
# ===========================================================================
def main(argv=None):
    ap = argparse.ArgumentParser(
        description='真窗口自动化测试（真开窗 + 事件投递，不劫持真实鼠标）')
    ap.add_argument('--backend', choices=['qt', 'tk', 'all'], default='qt')
    ap.add_argument('--layer', choices=['A', 'B', 'C', 'all'], default='all')
    ap.add_argument('--dry-list', action='store_true',
                    help='只列场景与断言，不 import pyplot、不开任何窗口')
    ap.add_argument('--selftest', action='store_true',
                    help='断言机器自检（合成数据）：不开窗、不 import pyplot，'
                         '验证谓词接对了且不恒真')
    ap.add_argument('--preflight', action='store_true',
                    help='内部：投递前置探针（真开一个窗口，发 1 次鼠标移动 + 1 次按键，'
                         '数收到几次）。父进程据此决定"能不能跑"，收不到就 skip')
    ap.add_argument('--scenario', choices=sorted(_CHILD_FUNCS),
                    help='内部：只跑一个场景并输出 @@RW@@ JSON')
    ap.add_argument('--cfg', default=None, help='内部：场景配置 JSON')
    ap.add_argument('--emit-json', action='store_true', help='内部：输出 @@RW@@ 行')
    ap.add_argument('--keep-tmp', action='store_true', help='内部：保留临时目录')
    ap.add_argument('--timeout', type=float, default=1800.0,
                    help='单个场景子进程超时（秒）；默认 1800 —— 6 类图形的 A 层'
                         '每个场景要真投 1000 次手势（monkey 实测 ~250s，新图形 pump×0.25 '
                         '约 60~90s），300s 会误杀')
    ap.add_argument('--log', default=None, help='把完整日志同时写到该文件')
    ns = ap.parse_args(argv)

    if ns.dry_list:
        return dry_list()

    if ns.selftest:                      # ---- 断言机器自检（不开窗）----
        return selftest()

    if ns.preflight:                     # ---- 投递前置探针（子进程）----
        ns.cfg = json.loads(ns.cfg) if ns.cfg else dict(CFG_DEFAULT['drag'])
        ns.scenario = 'preflight'
        return _child_main(ns)

    if ns.scenario:                      # ---- 子进程模式 ----
        ns.cfg = json.loads(ns.cfg) if ns.cfg else CFG_DEFAULT.get(ns.scenario, {})
        return _child_main(ns)

    # ---------------- 父进程 ----------------
    if os.environ.get('MPLBACKEND'):
        say('[注意] 外部 MPLBACKEND=%r 已设；子进程会**显式删掉**它'
            '（真窗口测试不能让 Agg 生效）' % os.environ['MPLBACKEND'])
    try:
        import matplotlib                                     # noqa: F401
        mpl_ver = matplotlib.__version__
    except Exception as e:                                    # pragma: no cover
        say('[致命] 父进程拿不到 matplotlib: %s' % e)
        return 2

    backends = ['qt', 'tk'] if ns.backend == 'all' else [ns.backend]
    say('=' * 78)
    say('真窗口自动化测试（t16 / #4）—— 后端 %s，层 %s' % (','.join(backends), ns.layer))
    say('=' * 78)
    say('解释器: %s (py %s / matplotlib %s)'
        % (sys.executable, sys.version.split()[0], mpl_ver))
    say('机制: Qt QTest 事件投递 / Tk event_generate（**不移动真实光标**）')
    say('源码状态: %s' % _src_state())
    _nload, _lms, _ = _machine_load()
    say('机器负载自检（开跑前，只读、不作判据）: 其它 python 进程 %s 个；CPU 基准 %.0f ms'
        '（同机可比、越小越空；本套件对时序敏感，数字表只用"安静机器"上的运行）'
        % ('?' if _nload < 0 else _nload, _lms))
    say('打扰控制: figure.raise_window=False + raise_()/activateWindow() no-op '
        '+ WA_ShowWithoutActivating（仅测试进程内 patch，不动 src/）')

    # ---- 投递前置探针（0.1.18 / t2）：先确认"事件真的送得进去"，再决定跑不跑 ----
    # 代价 2 秒，买的是"不会白跑 90 分钟再红 5 条其实是环境问题"。
    _skip_qt = False
    if 'qt' in backends:
        _ok, _why, _pdata = _preflight_delivery('qt', ns.timeout)
        say('投递自检（Qt）：%s —— %s' % ('通过' if _ok else '**不通过**', _why))
        if _ok and _pdata is not None and _pdata.get('active') is False:
            # 投递正常但窗口没前台焦点：本机实测**不影响**事件投递（motion 收到了），
            # 只是如实记一笔 —— 将来若出现"投递正常但手势不生效"，先看这一行。
            say('  （注：窗口没有前台焦点 isActiveWindow=False，但事件照样收得到；'
                '本条只作记录，不影响判定）')
        if not _ok:
            _skip_qt = True
            say('')
            say('[跳过] Qt 真窗口场景**这次不跑**：%s。' % _why)
            say('       这不是被测产品的问题 —— 是**投递层**不可用（QTest/postEvent 都'
                '送不进去）。先在能弹窗的桌面会话里跑一次，或检查：')
            say('       ① 是否在无桌面的服务/会话里跑（RDP 断开、CI 无头机）；')
            say('       ② 控制台是否被别的窗口全屏独占；')
            say('       ③ 本文件的驱动开关 MOVE_VIA_POST 是否被人为关掉。')
            say('       想看"探针本身对不对"：设 MPLTWEAK_RW_FORCE_UNDELIVERABLE=1 '
                '重跑，应当出现同一句跳过。')
            if 'tk' not in backends:
                say('')
                say('结论：本次 **SKIP**（rc=0，不是失败）。')
                return 0
    say('')

    total_fail = 0
    total_rows = 0
    reports = {}
    ran = []                     # **真的跑了**的后端（跳过的不进汇总，否则关键表会 KeyError）
    t_run0 = time.time()
    for bk in backends:
        if bk == 'qt' and _skip_qt:
            say('-' * 78)
            say('后端 qt —— **跳过**（投递自检不通过，见上）')
            continue
        say('-' * 78)
        say('后端 %s' % bk)
        say('-' * 78)
        t_bk0 = time.time()
        runner = Runner(bk, ns.timeout)
        rep = Report(bk)
        try:
            assemble(runner, rep, ns.layer)
        except KeyboardInterrupt:
            raise
        except BaseException as e:
            say('[异常] 装配断言时出错: %s' % e)
            say(traceback.format_exc()[-1500:])
            rep.check('X0', 'X', '断言装配未抛异常', False, '%s: %s' % (type(e).__name__, e))
        reports[bk] = rep
        ran.append(bk)
        say('')
        say('  子进程启动次数: %d（每场景一个进程、跑完即退 → 不留 GUI 进程；'
            '超时会 taskkill /T）' % runner.spawns)
        say('  后端 %s 小结: %d/%d 通过，%d 失败（本后端墙钟 %.1f s = %.1f min）'
            % (bk, len(rep.rows) - len(rep.failed), len(rep.rows), len(rep.failed),
               time.time() - t_bk0, (time.time() - t_bk0) / 60.0))
        total_fail += len(rep.failed)
        total_rows += len(rep.rows)

    # ---------------- 汇总 ----------------
    say('')
    say('=' * 78)
    say('汇总')
    say('=' * 78)
    say('  断言逐条：')
    for bk in ran:
        rep = reports[bk]
        for r in rep.rows:
            say('    [%s] %-3s %-4s %s' % ('PASS' if r['ok'] else 'FAIL', r['id'], bk, r['desc']))
    say('  硬断言: %d 条，失败 %d 条' % (total_rows, total_fail))
    n_soft = sum(len(reports[b].softs) for b in ran)
    _pinned = [(bk, r) for bk in ran for r in reports[bk].rows if r.get('pinned')]
    if _pinned:
        say('  已知缺陷 pin: %d 条（预期 FAIL、**不计失败**；缺陷修好会立刻报红要求移除）'
            % len(_pinned))
        for bk, r in _pinned:
            p = r['pin']
            say('    [PIN] %s  %s  —  %s' % (r['id'], bk, p.get('title')))
            say('          缺陷编号=%s ｜ 移除条件: %s' % (p.get('task'), p.get('remove_when')))
    say('  软检查: %d 条（只警告，不判失败）' % n_soft)
    for bk in ran:
        for s in reports[bk].softs:
            say('    [%s] %-4s %s — %s' % ('WARN' if s['warn'] else 'OK', bk,
                                           s['label'], s['detail']))
    say('')
    say('  本次运行总墙钟: %.1f s = %.1f min（含全部子进程；逐场景耗时另见各 A 层明细）'
        % (time.time() - t_run0, (time.time() - t_run0) / 60.0))
    say('  运行环境（来自本次真实运行）：')
    for bk in ran:
        f = reports[bk].facts
        if f:
            say('    %-4s py=%s mpl=%s 后端=%s(%s) 平台=%s 屏幕=%s 可用区=%s '
                'canvas dpr=%s dpi=%s QT_SCALE_FACTOR=%s'
                % (bk, f.get('py'), f.get('mpl'), f.get('backend'), f.get('qt_api'),
                   f.get('platform'), f.get('screen'), f.get('avail'), f.get('dpr'),
                   f.get('dpi'), f.get('scale')))
        else:
            say('    %-4s （本层未包含采集环境信息的场景）' % bk)
    say('')
    say('  C 层 %d 类场景覆盖：' % len(C_MAP))
    for name, how in C_MAP:
        say('    %-18s → %s' % (name, how))
    say('')

    if ns.log:
        try:
            with open(ns.log, 'w', encoding='utf-8', errors='replace') as f:
                f.write('\n'.join(LOG_LINES) + '\n')
            say('  完整日志已写入: %s' % ns.log)
        except OSError as e:
            say('  [注意] 写日志失败: %s' % e)
    say('')

    if total_rows == 0:
        say('NO CHECKS：该 --backend/--layer 组合下没有任何断言（后端 %s / 层 %s）'
            % (','.join(backends), ns.layer))
        return 2
    ok = (total_fail == 0)
    say('ALL PASS' if ok else 'FAILED（%d/%d 条硬断言未过）' % (total_fail, total_rows))
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
