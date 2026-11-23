# Attesta 审计 Agent 独立安全与完成判定复核（2026-11-20）

- 复核人：`security-review`（独立复核，不参与实现）
- 复核对象：分支 `codex/project-ocr-audit`；本次复核起点为 `c33d7db`，复核期间队友新增 `ddb20d0`（`agent_tools.py` +3 行，`prepare_workpaper` 的 `ValueError` 兜底）与 `638a3e6`（仅新增 `tests/test_agent_ui_smoke.py`）；**文内行号以 HEAD `638a3e6` 为准**。除 `agent_tools.py` 那 3 行外，`agent.py`、`agent_ui.py`、`ai.py`、`gui.py` 自 `eecd59b` 未变。
- 方法：逐行审阅 `agent.py` / `agent_tools.py` / `agent_ui.py` / `ai.chat_tools` / 现有测试，并用只读合成探针验证可执行结论（探针只在 `build/` 下建临时目录，运行后清理；未改产品代码、测试、README 或其他文档；未提交 git；复核结束时工作树干净，唯一新增文件为本报告）。
- 定向测试：`.venv\Scripts\python.exe -B -m unittest tests.test_agent tests.test_agent_tools tests.test_agent_scenario tests.test_ai_tools -q` → **Ran 40 tests, OK**（1.331s，HEAD `638a3e6`）。另运行队友新增的桌面冒烟测试 `tests.test_agent_ui_smoke` → **Ran 2 tests, OK**（1.622s），但它替换了真实确认桥，不能证明弹窗/超时/停止路径安全（见 L12）。

## 0. 结论摘要

**未发现可确认的高危问题**：没有找到"不经 `ApprovalDecision` 写文件"、"模型自造路径覆盖原模板/原数据库"、"伪造 artifact 让 `finish(done)` 通过"或"API Key 进入运行记录/auditlog"的可执行路径。写文件、完成判定、来源失效和工具结果回传这四条主边界在代码上都有真实的本地检查，不是只靠提示词。

发现的主要问题集中在**确认内容准确性、取消语义、出站范围声明的一致性和完成判定的覆盖口径**：7 个中危、12 个低危。中危里最值得先修的是 M1（TB 写入确认可能显示"将写入 0 个字段"而实际写入 N 个）和 M2（底稿导出前缺少产品既有的来源副本重验），其次是 M5（确认弹窗期间"停止"不可达，且停止后仍可能写入）。

## 1. 六个必答问题的直接结论

| 问题 | 结论 |
| --- | --- |
| 1. 能否无 `ApprovalDecision` 写文件？拒绝后能否重试？自动确认是否复用错误路径/覆盖？ | **不能**。所有 `write` 类工具在 `_execute` 中先过 `_approve_write`，工具只使用 `runtime.approved_path`，schema 里没有路径参数。拒绝后同名工具进入 `_blocked_tools`，本次运行不再执行（但模型仍可重复调用，不弹窗、不计数，见 L3）。自动确认后第二次写入的路径是 `""`，工具回落到带时间戳的默认新文件名，两个导出函数和 `workbook.write_results` 都会拒绝已存在文件/原模板，**不存在复用错误路径覆盖**（见 1.2 M1 的确认内容问题）。 |
| 2. `finish(done)` 能否被伪造 artifact（kind/target/path/sha256）绕过？缺 TB 待处理清单/底稿是否一定 partial？来源失效覆盖 TB 与底稿吗？ | **不能伪造**：kind/target/path 由工具写死或来自已校验索引与用户确认路径，sha256 由实际文件计算，没有任何工具能向 `context.artifacts` 追加模型数据。缺 TB 待处理清单或底稿一定把 `done` 降为 `partial`（`agent.py:644-681`，测试已覆盖）。来源失效在每个循环、每次导出和 finalize 都检查，覆盖 TB 与底稿。削弱点：`report_indexes` 留空时"底稿"只要求已准备项目（M7）、finish 不复算哈希（L1）、底稿导出前缺一致副本重验（M2）。 |
| 3. 能否借工具参数写出原模板/原数据库/项目外文件？证据行、序时账、知识片段会超范围外发吗？`result_scope` 有无绕过？ | **不能**：没有路径参数；写入路径只来自确认框或默认输出目录；原模板有同文件检查，原库只以只读锁 + 临时一致副本读取。**证据行从不发给模型**；序时账/客商/知识只在 `prepare_workpaper` 中先过 `spend_model_call` 再发送（顺序正确，`agent_tools.py:373→384`），当前 UI 的启动范围恒含这些类别，未发现真实超范围发送。`result_scope` 有一个已复现的绕过口：**`ok:false` 携带的 `data` 不经过范围校验就回传模型**（M3），当前产品内所有失败返回都不带 data，属潜在边界缺口；另有两处"请求范围声明偏窄"（M6，当前被 UI 范围掩盖）。 |
| 4. 提示注入（科目名/知识片段/文档指令）能改变工具行为或诱导未授权写入吗？ | **不能改变工具行为或写入目标**：白名单、参数 schema、写入审批、金额本地计算、结果范围、引用校验均不读取自然语言；审批弹窗的摘要/明细基本是运行时固定文案 + 本地事实，路径只能由用户在对话框选择。**残余风险**是注入可以影响模型文字与建议、诱导模型反复弹确认/提问、把注入文字当作审计说明写进底稿（本地核对只查数字与引用，不查语义），需要人工复核模型文字。 |
| 5. UI 工作线程、确认超时、关闭窗口、停止是否存在死锁或静默继续？Key/客户原文会进运行 JSON 或 auditlog 吗？ | **未发现死锁**（事件用队列、Tk 只在主线程）；但工作线程直接读 Tk 变量（M4）是潜在异常点，确认弹窗 `grab_set` 期间"停止"按钮不可点、超时 600 秒才自动拒绝，且停止请求后若用户仍点"允许"写入仍会发生（M5）。超时/拒绝有可见事件，不是静默继续。**Key 不会进入运行 JSON/auditlog**（无字段、HTTP 错误脱敏、地址拒绝 userinfo）；**客户原文风险存在但有限**：运行 JSON 会保存模型最终答复（≤12000 字符）、工具参数字符串（≤160 字符）等，可能含模型复述的客户信息（L4），与设计"无客户原文"的表述不完全一致。 |
| 6. 预算/循环/重复限制有效吗？异常路径会把失败显示成完成或漏掉 artifact 吗？ | **总体有效**（步数/工具/模型/时间、单轮 8 调用、参数与结果上限、连续重复检测）。缺陷：模型请求上限存在 off-by-one（实测 `max_model_calls=1` 仍发出 2 次请求，probe1）；被拒绝/未知工具的调用不计入工具预算也不进重复检测（probe4）；时间上限只在调用间隙检查。异常路径**不会把失败显示成完成**：写失败/登记失败会进入 error 或缺口，缺 artifact 必然 partial；已生成的 artifact 保留在结果里，不会漏掉（L5/L6/L7 是清理与审计一致性小问题）。 |

## 2. 问题清单

### 2.1 高危

**未发现可确认的高危问题。** 具体核对过以下"本应高危"的路径，均被本地检查阻断：

- 绕过写文件确认：`_execute` 只对 `spec.kind == "write"` 走 `_approve_write`，生产注册表里只有 `export_tb_copy` / `export_workpaper` 两个 write 工具（`agent_tools.py:555-558, 596-602`），没有"read/compute/model 工具内部写交付物"的路径。
- 覆盖原模板/原库：`export_tb_copy` 显式比较 `path == scan.path` 并拒绝已存在文件（`agent_tools.py:267-271`），`workbook.write_results` 还会再做同文件/同后缀/只读打开/模板摘要校验并以 `os.rename` 拒绝覆盖已有目标（`workbook.py:250-256, 275-280, 306-310`）。
- 完成判定伪造：`finish` 的 `status/summary/gaps` 只是文字，`_completion_gaps` 只读 `context.artifacts`（`agent.py:633-664`），而 artifacts 只能由两个导出工具用本地路径登记（`agent_tools.py:295-300, 474-477`），没有工具暴露 artifact 写入。
- 凭证泄漏：`DeepSeekClient` 的 key 只存在于 client；`_http_error` 回显前把 key 替换为 `***`（`ai.py:181-209`）；`normalize_base_url` 拒绝带账号/密码/查询参数的地址（`settings.py:26-37`）；audit 只记 model 与 base_url（`agent.py:714-716`）。

### 2.2 中危

#### M1. TB 写入确认的内容可能严重低估实际写入量（确认先于扫描）

- **证据**：`_tb_approval_details` 用 `context.results` 计算"将写入字段/覆盖数/示例"（`agent_tools.py:244-254`，`_pending` 见 `54-60`）；而扫描发生在 `export_tb_copy` **执行内部** `_ensure_scan`（`agent_tools.py:258`），审批发生在此之前（`agent.py:530-543`）。
- **实测（probe7）**：合成上下文 `scan=None, results=[]` 时，`_tb_approval_details` 返回"将写入字段 0 个；允许覆盖 0 个；写入示例：无选中字段"；同一次 `export_tb_copy` 内部扫描后实际 `selected=2`，示例变为两条金额。用户看到"没有要写的内容"却批准了一次真实写入。
- **影响**：写文件确认的核心信息失真；写出的仍是新副本、不改原模板，但用户对"写了哪些字段、覆盖了什么"没有知情。系统提示要求"先 scan_template"（`agent.py:609-610`）不是硬约束，模型顺序可被绕过或被注入诱导。
- **复现/测试建议**：新建 `test_agent_tools` 用例——无 `scan` 的上下文先取 `_tb_approval_details`，再（mock `workbook`）执行 `export_tb_copy`，断言"确认显示的字段数 == 实际写入数"。修法二选一：`_tb_approval_details` 内先做本地 `_ensure_scan`（不联网）；或 `export_tb_copy` 在 `context.scan is None` 时返回"请先扫描模板"而不自动扫描。

#### M2. `export_workpaper` 导出前没有重验一致副本来源（产品既有口径缺失）

- **证据**：`prepare_workpaper` 只在准备时用 `_validate_paper_data` 比对本次读到的行与快照行（`agent_tools.py:348-354, 368-370`）；`export_workpaper` 只做 `ensure_current()`（UI 指纹，`agent_tools.py:437`），随后直接使用准备时缓存的 `data` 建册（`458-467`），**没有调用 `dxn.validate_paper_data_source`**。而产品既有流程在发送/导出前都做这一步：`gui.py:580`、`gui.py:942`（`auto_intake_ui.py:145` 同样），该函数会按 source 文件大小/时间/哈希与缺失表重验一致副本（`dxn.py:281-304`）。
- **影响**：`prepare_workpaper` 与 `export_workpaper` 之间（可能跨数分钟、含模型往返与用户确认）原账套被替换、补入缺失表或临时副本失效时，底稿仍按旧来源导出并以 `done` 结束；`record_artifact` 只把 `source_fingerprint` 字符串写进产物记录（`agent_tools.py:474`），不校验它。属"已导出文件的依据失效"未闭环。
- **复现/测试建议**：合成 `prepared_workpapers["1-010"]["data"]`，mock `dxn.validate_paper_data_source` 返回 False，调用 `export_workpaper`；当前会成功导出，期望应拒绝并把缺口写入 `context.gaps`。修法：导出前 `if not dxn.validate_paper_data_source(data): return _error(...)`，与 `gui.py:942` 对齐。

#### M3. `result_scope` 只在 `ok` 为真时校验：`ok:false` 携带的数据原样回传模型

- **证据**：`_execute` 计算 `required_scope` 后写的是 `if required_scope and result.get("ok"):`（`agent.py:547-552`）；不满足时仍把完整 `result`（含 `data`）追加进下一轮消息（`_append_tool_result`，`agent.py:627-631, 735`）。
- **实测（probe2）**：注册一个 `result_scope=("journal",)` 的合成工具，返回 `{"ok": False, "error": "...", "data": {"journal": "SECRET-JOURNAL-TEXT"}}`；运行后 `scope_approver` 一次都没被调用，`SECRET-JOURNAL-TEXT` 出现在下一轮模型消息里。
- **影响**：当前生产工具的所有失败路径都只返回 `{"ok": False, "error": ...}`（`_error`，`agent_tools.py:32-33`；异常兜底 `agent.py:347-350`），因此不是现存泄露。但设计 7.5② 承诺的是"结果回传前按类别再核对"，实现却依赖工具作者"失败时不要带 data"；任何一次工具改动（例如返回部分 data + 失败原因）都会立即变成静默越权外发。
- **复现/测试建议**：把 `tests/test_agent.py::test_tool_result_scope_is_checked_before_sending_to_model` 扩展为 `ok:false` 变体，断言未授权类别不出现在下一条消息。修法：无论 `ok` 真假，未授权时一律把结果裁剪为 `{"ok": False, "error": ...}`（或拒绝 `ok:false` 携带 data）。

#### M4. 工作线程直接读 Tk 变量（`_current_fingerprint` → `app.template_var.get()`）

- **证据**：worker 线程执行 `AgentRuntime.run()`（`agent_ui.py:305-317`），循环每步调用 `context.ensure_current()`（`agent.py:721-722`）→ `current_fingerprint()` → `_current_fingerprint(app)`，其中 `app.template_var.get()` 是 Tkinter `StringVar` 读取（`agent_ui.py:200-206, 285`）；`_finalize`、`export_tb_copy`、`export_workpaper` 同样在 worker 线程触发这条路径（`agent.py:668`；`agent_tools.py:259, 437`）。
- **影响**：Tkinter 非线程安全。主循环正常运行时（`_pump` 每 80ms 跑在主线程）跨线程调用通常经 Tcl 事件转发返回；一旦主循环未运行/窗口已销毁（例如强制结束、异常退出、后续把确认桥改成不依赖 mainloop 的形态），会抛 `RuntimeError: main thread is not in main loop` 或阻塞。该异常会以 `error` 结束本次运行，且 `_save_run_record`/`_finish` 都在主线程（`agent_ui.py:353-364, 367-397`），届时可能既不落运行记录也不显示结果。
- **复现/测试建议**：把模板路径/摘要改为启动时存入 `AgentContext`（`ProjectSnapshot` 已有 `template_digest`），worker 只读普通属性；或把 UI 指纹快照到普通变量（主线程 `after` 定时刷新），worker 读该变量。可加一个"worker 不调用 Tk"的静态检查/测试（把 App 的 `template_var` 换成会抛异常的假对象，跑一次 `start→finish`）。

#### M5. 停止/取消在确认桥等待期间不可达、且审批后无二次取消检查（停止后仍可能写入）

- **证据**：`ApprovalDialog` 打开后 `grab_set()`（`agent_ui.py:465`），主窗口的"停止"按钮（`agent_ui.py:111-112`）在弹窗期间不可点击；worker 在 `_ask_approval` 中阻塞等待最多 600 秒（`agent_ui.py:400-414`），`stop()` 只置取消标志（`320-325`），不会唤醒该等待。`_execute` 只在**审批前**调用一次 `check_cancel`（`agent.py:519`），审批返回后直接执行 handler（`530-544`），**没有再次检查取消**；因此"等待确认期间点了停止、随后用户点了允许"时，文件仍会被写出，之后循环下一次 `check_cancel` 才以 `cancelled` 结束（`agent.py:721, 741-744`）。
- **影响**：与设计 4.5"随时可停止……未完成的写文件不再发生"有偏差；停止操作在确认弹窗期间实际不可用，用户只能关闭弹窗（等于拒绝）或等 10 分钟超时；关闭主窗口时 `_request_close` 只能发起停止并保持窗口（`gui.py:1091-1102`），最长仍要等 600 秒。
- **复现/测试建议**：在 `_execute` 审批返回后、调用 handler 前补 `self.context.check_cancel()`；确认桥把 `threading.Event.wait` 改成 `stop` 可唤醒的等待（例如 stop 时注入 `ApprovalDecision(False, note="用户已停止")`）；测试用脚本化 approve 回调模拟"等待中被取消"，断言 handler 未被调用。UI 层面建议给确认对话框增加"停止本次运行"按钮，或在停止时主动关闭当前确认框。

#### M6. 出站（发往模型）数据范围靠工具内手写声明，且两处声明偏窄

- **证据**：
  - `run_tb_ai_fill` 只声明 `["tb"]`（`agent_tools.py:164`），但随后 `ai.review` 发送的 `review_payload` 含候选科目的名称/路径/方向/期初/期末/发生额与父子核对证据（`ai.py:595-624, 627-694`），实际含 `ledger` 类别。
  - `prepare_workpaper` 只声明 `["workpaper", "journal"]`（`agent_tools.py:373`），但 `papers.pack_request` 会把本地知识片段注入请求 `knowledge_refs`（`papers.py:475-482`），实际含 `knowledge` 类别。
  - 当前 UI 的启动范围恒含 `ledger/tb`，papers 阶段恒含 `knowledge`（`agent_ui.py:175-180`），所以现阶段没有真实超范围发送；但"每次模型请求按类别授权"这一核心不变式并没有被集中强制，只依赖各工具写得对不对。
  - 设计 6.1 还要求"每次模型请求记录用了哪些类别、多少条"；实现只记 `agent_tool_call(tool, kind, args)`（`agent.py:528`），没有请求级类别/条数记录。
- **影响**：潜在超范围外发（一旦范围配置或工具组合变化）；审计上无法逐请求复核"发了什么类别"。
- **复现/测试建议**：为 `ToolSpec` 增加 `request_scope`，由 `spend_model_call` 按注册表声明统一校验（或至少在 `ai.review` / `pack_request` 调用点显式传入类别常量）；测试断言 `run_tb_ai_fill` 的请求包含 `ledger` 时必然触发 `ledger` 授权。

#### M7. `report_indexes` 留空时，"整册底稿"完成判据只覆盖已准备项目

- **证据**：`_completion_gaps` 对 papers 阶段取 `targets = list(context.report_indexes)`，为空才回落到 `context.prepared_workpapers`（`agent.py:655-659`）；每个 target 只要有同 target 的 workpaper 文件即算齐全（`661-663`）。
- **实测（probe8）**：`stages=("papers",)`、`report_indexes=()`、仅准备/登记 `1-010` 一册时，`_completion_gaps()` 返回 `[]`，模型 `finish(done)` 会被接受，即使 `list_report_projects` 同时列出多个可出册项目。
- **影响**：用户勾选"整册底稿"但没填索引、期待 Agent 先列项目再确认时，模型可以只做一册就拿到 `done`。是否算缺陷取决于产品口径：若"整册"指"一个报表项目一册"，风险较低；若指本次应覆盖的项目集合，则完成判定偏松。
- **复现/测试建议**：补 UI 侧/运行时用例（papers 阶段、空 `report_indexes`、mock 导出单册 → 断言 `partial` 或注明"仅完成 1/N"）。修法：留空时要求先 `list_report_projects` 并用 `ask_user` 确认范围，或把 "本次列出的项目集合" 作为完成核对基准，而不是"模型自己准备过的项目"。

### 2.3 低危

#### L1. 完成判定只在登记时计算 sha256，finish 时不复算

- **证据**：`record_artifact` 写入时计算 `_sha256`（`agent.py:239-248`）；`_artifact_ok` 只要求 `path.is_file()` 且 `artifact.get("sha256")` 非空（`agent.py:633-642`），不比对当前文件。
- **实测（probe5）**：登记后替换输出文件内容，`finish(done)` 仍返回 done，记录的 sha（`0682c5…`）与当前文件 sha（`1d96ae…`）不一致。
- **影响**：模型无法改文件，实践中触发面很小；但与设计 7.5①"存在且 SHA-256 有效"的表述不完全一致，也掩盖导出后被其他程序替换的情况。
- **建议**：finalize 时对每个 artifact 重算 `_sha256` 与登记值比对，不一致记缺口并降级；补测试。

#### L2. 来源指纹不含 `app.results`（TB 结果），依赖 UI 的 busy 门禁

- **证据**：`ProjectSnapshot.revision()` = `(app_revision, ledger_digest, template_digest, template_path)`（`agent.py:140-142`），`_current_fingerprint` 同构（`agent_ui.py:200-206`），不含 `app.results`。设计 6.2 要求"TB 结果任一变化"触发 stale。
- **现状**：TB 结果的修改入口（勾选/覆盖/人工指定来源等）在当前界面都有 `if self.busy: return` 门禁（`gui.py:2304, 2329, 2343, 2352, 2388`），agent 运行时 `busy=True`，所以暂不可触发。一旦以后新增不看守 busy 的结果编辑入口，指纹不会发现。
- **建议**：把 `results` 的轻量摘要（数量+选中/状态计数，或结果对象 digest）加入指纹；或在 TB 编辑入口统一强制 busy 检查。

#### L3. 被拒绝/未知工具的调用不计工具预算、不进重复检测

- **证据**：`_execute` 对未知工具、`_blocked_tools` 命中直接 return（`agent.py:513-517`），发生在 `_check_repeat`（`504-508, 518`）与 `budget.spend_tool`（`520`）之前。
- **实测（probe4）**：`max_tool_calls=1`、`max_steps=100`、`max_model_calls=100`，模型连续 30 次调用被拒绝的 write 工具，实际运行 31 轮、写入 0 次，只受模型/步数上限约束；被拒工具既不弹窗也不计预算。
- **影响**：默认 20/40/12 下仍被 12 次模型请求兜住，不构成无限循环；但预算面板/审计上的"工具调用数"会低估实际尝试，且拒绝后的重试提示会反复出现。
- **建议**：未知/被拒调用也 `spend_tool()` 并进入重复检测；或在设计文档中明确"预算只统计已执行工具"。

#### L4. 运行记录会保存模型最终答复与工具参数摘要，可能含客户复述/原文片段

- **证据**：运行记录 = `config`（objective 等）+ `result.as_dict()`（`agent_ui.py:353-364`）；`result.summary` 来自模型 finish 文字，最长 12000（`agent.py:691, 700-705`），`answer` 事件 detail 最长 2000（`452-453`），工具事件里字符串参数截 160（`760-776`）。audit 另记 objective/subject_label/model/base_url/工具摘要/错误文本（`agent.py:528, 536, 541, 557, 714-716, 751`）。
- **实测（probe6）**：模型最终答复里的金额"1,234,567.89"与合同号"HT-2026-001"同时出现在"运行记录 JSON"（config.objective + result.summary + events）中；工具参数字符串以 160 字符进入事件/审计。
- **影响**：Key 不会进入（已核对）；但设计 7.4 第 8 条"无客户原文"表述过强——模型复述的客户信息、objective 里用户自己填的客户信息会以明文留在 `%LOCALAPPDATA%\ZSZH-TB\agent\runs\*.json`。属本机明文留痕，按项目既有口径（不承诺日志不可篡改）可接受，但应把文档口径改为"不记录完整工作簿/原库/凭证，模型答复按文字保存"。
- **建议**：文档改为准确表述；如需要，运行记录只保留结构化摘要与产物哈希，`summary` 仅截短或另存为"模型答复"字段并提示可能含客户信息。

#### L5. `export_workpaper` 未捕获异常时不走临时文件清理分支

- **证据**：清理只在 `except (OSError, ValueError, KeyError)` 分支执行（`agent_tools.py:455-473`）；其他异常（例如准备数据字段异常导致的 `AttributeError/TypeError`）会穿过 handler，被 `registry.call` 兜成 `ok:false`（`agent.py:349-350`），但 `.attesta-*` 临时文件路径的清理不会执行，可能遗留隐藏临时文件。
- **影响**：低概率遗留垃圾文件；不会覆盖原文件。
- **建议**：把清理放进 `finally`；补一个 `dxn_paper.build` 抛 `TypeError` 的测试断言目录无 `.attesta-*` 残留。

#### L6. `finish` 在 `run()` 中被提前拦截，绕过工具参数 schema 与工具预算

- **证据**：`run()` 对 `call.get("name") == "finish"` 直接 `_finish(call)`（`agent.py:731-733`），不经过 `registry.call` 的参数校验（`336-354`），也不 `spend_tool`；`_finish` 自己对 `status/summary/gaps` 做宽松归一（`686-698`）。
- **实测（probe9）**：`finish` 携带 schema 未定义的 `injected_path` 字段、`summary` 为数字 123，仍被接受并返回 done（summary 变 "123"）。
- **影响**：不影响完成判定（仍由本地 artifact 核对），只是设计上 `finish` 声明的 `required/additionalProperties` 实际不生效，且模型可把超长自由文本（≤16000）塞进 finish 参数进入运行记录。低。
- **建议**：在 `run()` 中对 finish 也做与 `registry.call` 一致的参数校验，或直接统一走 `_execute`。

#### L7. 审计日志追加失败不阻断写入，只在本轮结束时提示

- **证据**：`_audit` 吞掉回调异常（`agent.py:462-468`）；`agent_ui._audit` 把失败存到 `app.agent_audit_error`（`agent_ui.py:344-350`），仅在 `_finish` 时提示"审计日志写入失败"（`381-382`）。`auditlog.append` 在链异常/检查点失败时抛错（`auditlog.py:104-122`）。
- **影响**：日志链损坏后本次运行仍会写文件并继续，追溯性在运行中丢失，只在结束后提示。
- **建议**：如项目要求"可追溯优先"，可在审计首次失败后置一个"不再执行写工具"的标志；至少把提示提前到事件流。

#### L8. 用户无法逐类取消启动确认中的数据类别，面板也不显示剩余预算/类别条数

- **证据**：启动确认只由"交付物"复选推导范围（`agent_ui.py:175-180, 248`），`_manifest_text` 只列类别文字，没有逐类勾选，也没有字符数/条数（`209-228`）；运行面板不显示预算余量。设计 4.1/6.1 要求"逐类可取消、按类别显示条数与字符数"。
- **影响**：隐私控制的粒度弱于设计承诺；用户只能整体取消或改用论文。属未实现声明，不构成越权。
- **建议**：在 UI/文档二选一收敛：实现选择器，或把设计文档改为"按交付物推导范围"。

#### L9. 本地绝对路径回传模型

- **证据**：`get_project_context` 返回 `artifacts`（含 `path` 全路径）与模板名（`agent_tools.py:70-84`）；`scan_template` 返回 `str(scan.path)`（`127-134`）；`export_tb_copy`/`export_workpaper` 返回输出全路径（`301-305, 478-481`）。这些路径可能含 Windows 用户名、客户文件夹名，而启动确认的数据类别表没有"本机路径"一项。
- **影响**：低（不含文件内容），但属于范围清单外的信息外发。
- **建议**：对模型只回传 `Path(name)` 或相对名；产物路径只给用户界面。

#### L10. 出站范围拒绝会取消整个运行，与设计"取消该次请求"不一致

- **证据**：`ensure_scope` 在用户拒绝时抛 `AgentCancelled`（`agent.py:230-231`）；`registry.call` 把 `AgentCancelled` 转成 `{"ok": False, "cancelled": True}`（`347-348`），`_execute` 对非 write 工具再抛 `AgentCancelled`（`559-560`）→ 整个运行以 cancelled 结束（`741-744`）。而结果回传侧的范围拒绝只是把结果替换掉、运行继续（`550-552`）。
- **影响**：当前 UI 范围配置下几乎不触发（见 M6）；一旦触发，用户体验是"拒绝一次扩大范围 = 整个任务白跑"，且与设计文案不一致。
- **建议**：统一语义；若选择"取消该次请求"，让 `ensure_scope` 返回布尔/专用异常给 handler，而不是冒泡为运行级取消。

#### L11. 自动确认后默认输出目录被静默改为首次选择文件的父目录

- **证据**：`_approve_write` 在 `auto_confirm_rest` 时把 `context.output_dir` 改为首次确认路径的父目录（`agent.py:579-585`），后续同类写入的默认路径随之变化。
- **影响**：用户已勾选"同类写入不再询问"，属预期内；但与启动确认"输出目录：本次唯一写入根目录"的表述有落差，且默认路径的变化没有在事件里提示。
- **建议**：启动确认文案明确"首次选择后同类写入沿用该目录"；或在 auto 时保持启动输出目录，仅首次文件使用用户路径。

#### L12. 测试覆盖缺口与一个名不副实的用例

- **证据**：
  - `test_error_response_redacts_key`（`tests/test_ai_tools.py:113-117`）实际只测"返回结构异常"，没有覆盖 `_http_error` 的 key 替换（`ai.py:206-209`）。
  - `test_run_result_does_not_contain_credentials` 只断言字面量 `sk-` 不出现（`tests/test_agent.py:179-184`）。
  - 没有覆盖 M1（确认先于扫描）、M2（底稿来源重验）、M3（`ok:false` 带 data）、M4（worker 不碰 Tk）、M5（审批等待中停止）、M6（请求类别声明）、M7（空索引覆盖）、L1（哈希复算）。
  - 桌面冒烟测试把 `_ask_approval` 整体替换为脚本（`tests/test_agent_ui_smoke.py:172`），未覆盖真实弹窗的超时/关闭/停止路径。
- **建议**：至少补 M1、M2、M3、M5 四个用例（都可离线合成，不需要真实模型/Excel）；把 UI 确认桥的等待逻辑抽成可测的纯函数，再断言超时/停止语义。

## 3. 已实现且可信的防线（核验过的具体路径）

1. **写文件硬门槛**：`_execute` 仅对 `kind == "write"` 调 `_approve_write`（`agent.py:530-543`），审批回调缺失/异常一律返回拒绝（`470-479`）；拒绝后工具加入 `_blocked_tools` 不再执行（`516-517, 530-539`）；工具 schema 均 `additionalProperties: false` 且没有路径参数（`agent_tools.py:555-558, 596-602`），未知参数被 `_validate_arguments` 拒绝（`agent.py:357-394`；`tests/test_agent.py:144-155`）。有测试证明拒绝不执行、批准使用确认路径（`tests/test_agent.py:96-132`）。
2. **双重文件保护**：工具层拒绝原模板/已存在文件（`agent_tools.py:267-271, 440-441`）；`workbook.write_results` 再做 `resolve`、同文件、后缀、只读打开、公式检查、模板摘要前后校验，并用 `os.rename`（Windows 下拒绝已存在目标）发布（`workbook.py:248-313`）；`planned_writes` 还会重算金额、拒绝公式/未允许覆盖（`workbook.py:44-69`，`matching.py:845-857`）。底稿走临时文件 + `os.replace`（`dxn_paper.py:1173-1179`）。
3. **输出位置受控**：写入路径只来自用户确认框（`ApprovalDecision.path`）或默认路径 = 启动输出目录 / 模板所在目录 + 固定命名（`agent_tools.py:235-241, 267, 406-411, 439`）。
4. **快照与来源失效**：每步 `ensure_current`、每次导出前与 finalize 再查（`agent.py:212-217, 668-672, 721-722`；`agent_tools.py:259, 437`）；指纹含 UI 修订号、账套行摘要、模板摘要与路径（`agent.py:140-142`；`agent_ui.py:200-206`）；TB 导出还有 `workbook.validate_source_context`/模板摘要校验（`workbook.py:24-41, 306-310`）；原账套以只读锁 + 临时一致副本读取并在复制前后比对大小/时间（`dxn.py:241-274`），底稿准备时再比对本次读到的行与快照行（`agent_tools.py:348-354`）。`test_agent_scenario.py::test_source_change_between_scan_and_write_blocks_export` 覆盖"确认前模板变化 → stale 且不落盘"。
5. **完成判定本地化**：`_completion_gaps` 按交付物核对 `tb`/`tb_pending`/`workpaper` 的 kind、target、文件存在与登记哈希；缺项把 `done` 改 `partial` 并在 summary/gaps 里列出（`agent.py:633-681`）；纯文本结束也走同一核对（`737-740`）；模型无法写 artifacts（见 2.1）。测试：`tests/test_agent.py:186-215`、冒烟测试 `test_papers_checked_without_report_scope_is_partial`。
6. **结果回程范围校验（主路径）**：`_execute` 对 `ok` 结果按 `spec.result_scope` + 可选 `_scope` 校验，未授权即替换为失败提示、不进入模型消息（`agent.py:547-553`；`tests/test_agent.py:217-238`）。
7. **出站前审批的时序正确**：`prepare_workpaper` 先 `spend_model_call(["workpaper","journal"])` 再组装并发送请求（`agent_tools.py:373→376-384`），没有"检查前已发请求"的路径；`run_tb_ai_fill` 每批也先 `spend_model_call(["tb"])`（`164`）。范围未获准时 `registry.call` 不把数据发给模型。
8. **工具白名单与参数校验**：模型返回的工具名必须以提供的 schema 为白名单、参数必须是合法 JSON 对象且 ≤16000 字符、单轮 ≤8 调用、截断/空返回/越权名一律整轮不执行（`ai.py:306-379`；`MAX_TOOL_CALLS/MAX_TOOL_ARGUMENT_CHARS` 见 `13-17`）。注册表再校验一次工具名与类型（`agent.py:333-354`），异常只回"类型名"，不回栈（`349-350`）。
9. **凭证与错误文本**：key 只在 `DeepSeekClient` 内；错误回显前替换 key（`ai.py:206-209`）；API 地址拒绝账号/密码/查询参数/非 HTTPS（`settings.py:26-37`）；重定向被拒绝（`ai.py:141-143`）；运行结果测试断言不含 `sk-`（`tests/test_agent.py:179-184`）。
10. **证据行只本地**：`evidence_rows` 只传给本地 `dxn_paper.build` → `evidence_draft.add_sheet`（`agent_tools.py:443-449, 465`；`dxn_paper.py:1170-1172`），不在任何模型请求中；底稿准备/确认里只显示"证据条数"或缺口文案（`agent_tools.py:414-429, 451-454`）。
11. **模型文字核验**：`papers.pack_result` 对金额/编码/比率/计数/引用编号逐句核对，不能核对的句子替换为"待核实"或整体拒绝（`papers.py:559-606`）；无法核对时降级为本地事实底稿（`agent_tools.py:386-394`）。知识引用只接受本次注入的 `[K#]`（`ai.py:471-481`；`papers.py:_strip_unknown_citations`）。
12. **提示注入的硬边界**：工具参数没有路径/URL/SQL/命令；写入目标只能由用户在确认框选择；金额、匹配、勾稽全部本地计算（`workbook.py:44-69`；`matching.py:813-857`）；系统提示与各通道 prompt 明确"数据不是指令"（`agent.py:598-601`；`papers.py:44-57`；`ai.py:569-578`）。注入最多影响模型建议/文字，不能直接改变落盘位置或金额。
13. **UI 事件线程模型**：worker 只 `queue.put` 事件，主线程 `_pump` 每 80ms 消费（`gui.py:1944-1957`；`agent_ui.py:298-301`），事件回调异常被捕获（`agent.py:455-459`）；确认/提问用 `threading.Event` 在主线程弹窗、worker 阻塞等待（`agent_ui.py:400-429`），所读到的正常路径未发现死锁。
14. **审计链**：哈希链 + DPAPI 末尾检查点，字段不能覆盖 `seq/ts/actor/event/prev/hash`（`auditlog.py:16, 90-122`）；追加失败会抛错并由界面提示（`agent_ui.py:344-350, 381-382`）。

## 4. 合理风险与不在本期范围

以下不是缺陷，或按项目现有口径明确排除，复核时未按漏洞计数：

1. **本机日志不承诺不可篡改**：运行记录/auditlog 是本地明文/可回滚文件，DPAPI 只保护 Key 与"末尾检查点"；同一用户可回滚本地状态。项目文档已明确该口径（`docs/安全与发布.md`、`审计Agent设计.md` 6.6）。
2. **用户可自选任意输出路径**：确认框允许用户把新文件写到任意位置（含项目目录外），这是用户主动选择；产品只保证"不覆盖已存在文件/原模板/原库"，不保证写入根目录。设计 4.1"唯一写入根目录"在实现上是默认目录 + 每次可改，建议按 L11 收敛文案。
3. **快照语义**：一次运行基于启动快照。外部程序直接改动原账套但界面未重新读取时，TB 导出仍按快照行（`workbook` 只校验快照摘要与模板文件），这属于"一次运行即快照"的既有取舍；底稿侧有 M2 的缺口，建议补。
4. **提示注入不可根除**：本地校验能保证金额/编码/写入路径不被文字改变，但不能保证模型文字语义正确；底稿审计说明仍必须人工复核（产品口径已如此）。
5. **合规留痕强度**：auditlog 只有哈希链 + 本机检查点，不是受控服务端存证；signing/独立授权不在本期（设计"不做"清单）。
6. **预算不计量 token/费用**：预算只限调用次数与时长，界面未显示剩余预算与每次请求类别计数（设计 6.1/6.3 的展示项未实现，见 L8）。
7. **M3 附注/报告未接入**：`reporting.py` 有初版代码与测试，但 `build_registry` 不注册任何 reporting 工具（`agent_tools.py:512-603` 无 reporting 引用），界面也不显示为可用交付物；`AgentContext.notes_ready/report_ready`（`agent.py:203-204`）从未被设置/校验，若未来新增阶段必须同步补 `_completion_gaps` 分支。
8. **未实现但已声明的交互**：`pause_after_tb` 仅预留（`agent.py:158, 186, 191`；设计 4.3 自认未接界面）；启动范围确认的逐类勾选/条数字符数（设计 4.1/6.1）未实现（L8）；真实确认桥的超时/停止路径无自动化测试（L12）。
9. **`report_indexes` 留空时的产品口径需产品确认**（M7）：若"整册"仅指单报表项目一册，则 M7 可降为文档说明；若指覆盖项目集合，则应修完成判据。

## 5. 定向测试与复现记录

### 5.1 定向测试（在 HEAD `638a3e6` 实测）

命令（在项目根目录，`cmd /c` 形式避免 PowerShell 把 stderr 当错误）：

```powershell
cmd /c ".venv\Scripts\python.exe -B -m unittest tests.test_agent tests.test_agent_tools tests.test_agent_scenario tests.test_ai_tools -q 2>&1"
```

结果：**`Ran 40 tests in 1.331s` / `OK`**（exit code 0）。

补充运行了队友新增的桌面冒烟测试：

```powershell
cmd /c ".venv\Scripts\python.exe -B -m unittest tests.test_agent_ui_smoke -v 2>&1"
```

结果：**`Ran 2 tests in 1.622s` / `OK`**（exit code 0）。覆盖"TB-only 端到端导出 + 原模板字节不变"与"papers 勾选但未填索引 → partial + 缺口"。注意该测试替换了 `_ask_approval`（`tests/test_agent_ui_smoke.py:172`），不能证明真实弹窗/超时/停止路径。

### 5.2 合成探针（只读，未入库）

以下结论由内联 Python 合成脚本在本机 `build/` 临时目录验证，运行后目录已清理，工作树保持干净：

| 探针 | 验证内容 | 结果 |
| --- | --- | --- |
| probe1 | `max_model_calls=1` 时的实际模型请求数 | **2 次**（第 2 次在 budget 检查前已发出）→ L3/预算 off-by-one |
| probe2 | `result_scope=("journal",)` 的工具返回 `ok:false + data` | scope 审批 0 次，`SECRET-JOURNAL-TEXT` 进入下一轮消息 → M3 |
| probe3 | 自动确认后同一工具第二次写入拿到的路径 | `['C:/out/first.xlsx', '']` → 不复用旧路径，回落默认新文件 |
| probe4 | `max_tool_calls=1` 时连续调用被拒绝的 write 工具 | 31 轮、0 次写入、只受模型/步数限制 → L3 |
| probe5 | 登记 artifact 后替换文件内容再 `finish(done)` | 仍返回 done，登记 sha 与当前文件不一致 → L1 |
| probe6 | 模型答复含客户金额/合同号时的运行记录 | 金额与合同号进入 config/result 序列化文本 → L4 |
| probe7 | 未扫描模板时 `_tb_approval_details` 与扫描后对比 | 确认显示"0 个字段"，扫描后实际 2 个 → M1 |
| probe8 | papers 阶段、`report_indexes` 空、仅一册 artifact | `_completion_gaps()` 返回 `[]` → M7 |
| probe9 | `finish` 携带 schema 外字段、summary 为数字 | 被接受并返回 done → L6 |

### 5.3 复核边界说明

- 没有连接真实 DeepSeek 接口，也没有使用真实账套/客户 TB/Key；所有行为验证都用合成对象与脚本化模型响应（符合 `docs/审计Agent设计.md` 7.1）。
- 提示注入只做了代码路径分析，没有用真实模型做红队复现（无法离线构造）；结论按"硬边界 + 残余风险"给出。
- 复核未修改产品代码、测试、README 或其他文档，未提交 git；本报告是唯一新增文件。

## 6. 复核后修复状态（主代理 2026-11-20）

复核后已处理：**M1**（TB 确认前本地补扫描，确认字段数与实际一致）、**M2**（底稿导出前重验一致副本来源，失败写入缺口）、**M3**（`ok:false` 结果也做范围校验并丢弃 `data`）、**M4**（运行线程不再读 Tk 变量，改为启动快照）、**M5**（审批后二次取消检查，确认框增加“停止本次运行”）、**M7**（`report_indexes` 留空时完成判定必为 partial）、**L1**（完成判定重算文件 SHA-256）。**M6** 的请求类别声明已补 `ledger`/`knowledge`，逐请求类别留痕仍未实现。其余低危项保留为后续改进。
