# Attesta 审计 Agent 修复回归复核（2026-11-23）

- 复核人：`security-review`（独立复核，不参与修复实现）
- 复核基线：**HEAD `571ae96`**。其中 `3d1048b` 修复 M1/M2/M3/M5/M7/L1/L3/L10，`a0ee9e4` 修复 M6/L6/L9，`f03bbee` 修复 L2；`571ae96` 仅文档（`docs/审计Agent设计.md` 口径对齐），代码基线等同 `f03bbee`。本文行号以 `571ae96` 为准。
- 方法：逐行对照 `3d1048b`/`a0ee9e4`/`f03bbee` 的 diff 与当前代码；用只读合成探针验证 M1–M7/L1/L2/L6/L9/M6；用真实 Tk `App` + 脚本化模型 + 真实确认弹窗做 M5 端到端探针（程序化点击弹窗内"停止本次运行"）；运行定向测试。未修改产品代码、测试、README 或其他文档，未提交 git，工作树复核结束时干净，唯一新增文件为本报告。
- 定向测试（HEAD `571ae96`）：
  - `cmd /c ".venv\Scripts\python.exe -B -m unittest tests.test_agent tests.test_agent_tools tests.test_agent_scenario tests.test_ai_tools tests.test_agent_ui_smoke -q 2>&1"` → **Ran 47 tests, OK**（7.309s，exit 0）。
  - `cmd /c ".venv\Scripts\python.exe -B -m unittest tests.test_agent_acceptance -q 2>&1"` → **Ran 4 tests, OK（skipped=1，103.871s）**；跳过的是真实 Excel 用例，原因是本机 Excel COM 报 `-2147352567`（内存/磁盘空间不足），不是代码失败；其余 3 个（mock Excel 编排、证据页真实写入、无证据缺口）通过。

## 0. 结论摘要

- **本轮要求验证的项：M1/M2/M3/M5/M7/L1/M6/L6 已修复并有测试或探针证据；L2 已修复；M4 主路径已修复但残留一处 worker→Tk 读取；L9 部分修复（显式路径字段已最小化，但每个工具结果的 `revision` 仍携带绝对模板路径）。**
- 未发现修复引入的越权写文件、伪造完成、凭证泄漏回归；写文件确认、完成判定、来源核销、结果范围四条主防线仍然成立。
- 需要跟进的主要回归风险：
  1. **R1（中低）**：生产运行期的 `stale` 检测仍有缺口——动态指纹用的是启动快照里的 `ledger_digest/template_digest`，模板文件在磁盘上被替换或账套行在界面外变化不会在运行中触发 `stale`（导出时 `workbook`/`dxn.validate_paper_data_source` 仍会拦截，但设计 6.2 的"模板任一变化即 stale"只部分成立）。
  2. **R2（中）**：M7 修复后，`report_indexes` 留空**永远**只能得到 `partial`；而界面仍写着"留空则由 Agent 列出后询问"（`agent_ui.py:90`），且没有任何工具能把用户回答写回 `report_indexes`——该文案承诺的流程走不到 `done`，需要产品/界面口径收敛。
  3. **R3（低中）**：被拒绝/未知工具的调用现在计入工具预算并参与"连续三次相同参数"检测（`agent.py:517-524`）；模型若在被拒后仍重试 3 次，本次运行会以循环/预算 `error` 结束，而不是按设计继续。行为是 fail-closed，但报错文案没有点明"该工具已被用户拒绝"。
- 另有 M3/M1/M2/L1/L6 的若干低危残留（失败结果仍触发范围审批、`scope_blocked` 标记丢失、扫描/来源校验发生在确认之后、哈希不符时的缺口文案等），见第 3 节。

## 1. 逐项结论

| 项 | 状态 | 关键证据 |
| --- | --- | --- |
| M1 确认字段数与实际写入一致 | **已修复** | `agent_tools.py:248-264`（明细前补 `_ensure_scan`）；测试 `tests/test_agent_tools.py:181-186`；探针：明细"将写入字段 2 个" = 扫描后 `selected=2` = mock `write_results` 实际写入 2 |
| M2 底稿导出前重验一致副本 | **已修复** | `agent_tools.py:453-460`；测试 `tests/test_agent_tools.py:188-201`；探针：校验失败→`ok:false`、不落盘、写入缺口；校验通过→正常出册 |
| M3 `ok:false + data` 不泄漏 | **已修复** | `agent.py:553-565`（先无条件 `ensure_scope`，失败结果裁剪为 `{ok,error[,cancelled]}`）；测试 `tests/test_agent.py:240-253`；探针：未授权 `SECRET` 不出现在下一轮消息 |
| M4 运行线程不读 Tk 变量 | **主路径已修复，仍存在一处** | 已修复：`agent_ui.py:294-297` 运行线程指纹只读 `app._context_revision`/普通属性/`_results_digest(app.results)`；仍存在：`evidence_provider`→`evidence_ui.paper_rows`→`evidence_draft.source_key` 读 `app.template_var.get()`（`agent_ui.py:287-292`、`agent_tools.py:432-437,465-470`、`evidence_ui.py:66-68`、`evidence_draft.py:33-42`） |
| M5 审批后二次取消 + 弹窗停止按钮 | **已修复** | `agent.py:547-548`（批准后 `check_cancel`）；`agent_ui.py:481`（"停止本次运行"）、`agent_ui.py:501-507`；测试 `tests/test_agent.py:266-286`；**真实 Tk 端到端探针**：程序化点击弹窗"停止本次运行"→`status=cancelled`、写入调用 0、无输出文件、原模板不变 |
| M7 空 `report_indexes` 必 partial | **已修复（但引出 R2 流程矛盾）** | `agent.py:667-673`；探针：已准备一册 + 空索引 → `gaps=["整册底稿未指定报表项目范围"]`，`finish(done)`→`partial` |
| L1 finish 重算 artifact SHA-256 | **已修复** | `agent.py:645-654`（`artifact.sha256 == _sha256(path)`）；探针：登记后篡改文件→`partial`（缺口"TB 副本文件缺失或已被移动"）；文件未变→`done` |
| M6 `agent_model_request` 审计 | **已修复** | `agent.py:198,236-241,735-744`（运行级事件+审计+`context.model_requests`）；`agent_tools.py:167-168`（`run_tb_ai_fill` scope `[tb,ledger]`）、`385-386`（`prepare_workpaper` scope `[workpaper,journal,knowledge]`）；探针：2 次运行级审计（含 messages/chars/scope）、2 条 `model_request` 事件、2 条 `model_requests` |
| L6 finish 参数 schema 校验 | **已修复** | `agent.py:750-758`；测试 `tests/test_agent.py:255-264`；探针：带 schema 外字段/缺 `summary` 的 finish 被拒并作为工具结果回传，模型重试合法 finish 后 `done` |
| L9 模型结果去掉绝对路径 | **部分修复，仍存在** | 已修复：`agent_tools.py:79-82`（artifacts 只回 kind/name/target/sha256）、`:130-133`（模板名）、`:313-315,501-503`（输出文件名）；仍存在：所有 `_ok` 结果的 `revision` 仍是 `snapshot.revision()`（`agent_tools.py:18-19,26-29`；`agent.py:141-143`），含**绝对模板路径**（探针用 `P:/客户A/项目资料/template.xlsx` 复现，模型可见） |
| L2 TB 结果摘要纳入来源指纹 | **已修复** | `agent.py:139-143`（`results_digest` 进入 `revision()`）；`agent_ui.py:183-191`（`_results_digest`）、`:194-208`（`_snapshot` 写入）、`:211-218`（`_current_fingerprint`）、`:294-297`（运行线程闭包）；探针：`selected` 变化或 `app_revision` 变化均抛 `StaleContextError`，初始指纹相等 |

## 2. 已修复项的证据与残留

### 2.1 M1：确认先于扫描（已修复）

- 修复点：`_tb_approval_details` 在 `context.scan is None` 时先本地 `_ensure_scan`（`agent_tools.py:248-254`），之后才统计"将写入字段/覆盖数/示例"（`255-264`）；扫描失败只返回一行"无法预先扫描模板"，不阻断流程。
- 验证：探针（合成模板、`scan=None`）取明细得到"2 个；允许覆盖 0 个"，同一上下文扫描后 `results=2`、`selected=2`；mock `workbook.write_results` 记录实际写入 `selected=2`，三者一致。新增测试 `test_tb_approval_details_scan_before_counting`（`tests/test_agent_tools.py:181-186`）覆盖"不再是 0 个"。
- **残留（低）**：明细计算在用户确认前就修改了 `context.scan/results`（不落盘，无越权）；若扫描失败，确认框显示"无法预先扫描模板"仍允许点"允许并继续"，而 `export_tb_copy` 在 `_ensure_scan`（`agent_tools.py:268`）抛出的 `ValueError` 不在 handler 的 try 内，最终只会得到 `工具执行失败：ValueError` 这类不可读错误（`agent.py:349-350`）。建议扫描失败时让明细直接返回不可批准信号，或把 `_ensure_scan` 包进可读错误。

### 2.2 M2：底稿导出前重验来源（已修复）

- 修复点：`export_workpaper` 在 `data.source_path` 非空时调用 `dxn.validate_paper_data_source(data)`，失败则 `context.add_gap(...)` + `_error(...)`，不落盘（`agent_tools.py:453-460`）；与产品既有流程一致（`gui.py:580,942`；`dxn.py:281-304`）。
- 验证：探针 mock `validate_paper_data_source` 为 False → `ok:false`、文件不存在、`gaps` 含"一致副本"；为 True（mock `dxn_paper.build`）→ 正常导出。新增测试 `test_workpaper_export_rechecks_source_snapshot`（`tests/test_agent_tools.py:188-201`）。
- **残留/行为变化（低）**：
  - 仅 `source_path` 非空时校验；合成/旧 `PaperData` 会跳过（现状测试数据即如此），建议未来把"无 source_path 也拒绝"作为显式约定。
  - 校验发生在用户确认**之后**：用户可能先确认写入、再看到"来源已失效"。可在 `approval_details` 阶段先做轻量提示。
  - 生产 `source_path` 是原账套目录，校验会重新只读锁定并哈希原库文件；若导出时鼎信诺重新打开了该项目，导出会报"无法重验账套来源"（prepare 时本来就要求关闭，属预期安全行为，但操作面有变化）。

### 2.3 M3：失败结果不能夹带数据（已修复）

- 修复点：`_execute` 对任何结果先按 `result_scope` 调 `ensure_scope`，失败后再统一裁剪为 `{ok:false,error,可选cancelled}`（`agent.py:553-565`）；`ensure_scope` 拒绝时改为抛 `ScopeError`（`agent.py:232-233`），不再把"扩大范围被拒"升级成整轮取消。
- 验证：探针合成工具返回 `{"ok":false,"data":{"journal":"SECRET"}}`、`allowed_scope={"tb"}`、`scope_approver` 拒绝 → 下一轮模型消息中无 `SECRET`；新增测试 `test_failed_tool_result_cannot_carry_unauthorized_data`（`tests/test_agent.py:240-253`）。
- **残留（低）**：失败结果也会触发范围审批（探针 `prompts=[{journal}]`）——用户可能被询问一个"不会发送任何数据"的失败结果；且 `scope_blocked` 标记在裁剪后丢失，审计无法区分"用户拒绝扩大范围"和"普通工具失败"。建议先裁剪再 `ensure_scope`（仅对 `ok` 结果审批），或在裁剪结果中保留 `scope_blocked`。

### 2.4 M4：worker 不读 Tk（主路径已修复，仍存在一处）

- 已修复：`start` 的运行线程指纹闭包只读 `app._context_revision`、`snapshot.ledger_digest/template_digest/template_path`、`_results_digest(app.results)`（`agent_ui.py:294-297`），不再调用 `_current_fingerprint(app)`（`_current_fingerprint` 仅保留给主线程 `_finish` 使用，`agent_ui.py:400-401`）。`inspect.getsource(start)` 探针确认源码中无 `template_var`/`_current_fingerprint`。
- **仍存在（中）**：证据提供器仍在 worker 线程读取 Tk：`context.evidence_provider` 指向 `evidence_for(index)`（`agent_ui.py:287-292`），被 `_workpaper_approval_details`（`agent_tools.py:432-437`）和 `export_workpaper`（`agent_tools.py:465-470`）调用；`evidence_ui.paper_rows`（`evidence_ui.py:66-68`）→ `evidence_draft.source_key(app)` → `app.template_var.get()`（`evidence_draft.py:33-42`）。触发条件：papers 阶段 + 该索引不在启动时预取的 `evidence_rows`（模型准备了非 `report_indexes` 的项目），或启动时证据读取异常（`agent_ui.py:280-286`）。风险仍是 Tkinter 跨线程调用（主循环未运行/窗口销毁时 `RuntimeError`），与 M4 同类。建议：把模板路径/摘要作为参数传入证据提供器（不读 app 变量），或在主线程预取所有可能索引的证据。

### 2.5 M5：审批等待期间的停止（已修复）

- 修复点：写工具批准后执行前再 `check_cancel()`（`agent.py:547-548`），取消优先于落盘；确认弹窗新增"停止本次运行"按钮（`agent_ui.py:481`），`_stop` 置取消标志并返回拒绝决策（`agent_ui.py:501-507`）。
- 验证：单测 `test_cancel_during_write_approval_prevents_write`（`tests/test_agent.py:266-286`）通过；**真实 Tk 端到端探针**（真实 `App`、脚本化模型、真实 `ApprovalDialog`，程序化点击"停止本次运行"）结果：`status=cancelled`、`write_results` 调用 0、输出目录无文件、原模板字节不变、事件含"已拒绝写入/已停止"。
- **残留（低）**：确认等待期间主窗口"停止"按钮仍被模态 `grab_set` 挡住（可用弹窗内按钮替代）；`agent_write_approved` 审计在二次 `check_cancel` 之前写入（`agent.py:545-548`），若随后取消，审计会出现"已批准但未执行"而没有对应取消事件；确认超时仍是 600 秒（`agent_ui.py:426-431`），超时提示对"扩大范围"场景仍写"已拒绝写入"。建议把批准审计移到取消检查之后或补 `agent_write_cancelled`。

### 2.6 M7：空 `report_indexes` 必 partial（已修复，但见 R2）

- 修复点：`_completion_gaps` 不再回落到 `prepared_workpapers`，空索引直接记缺口（`agent.py:667-673`）。
- 验证：探针（papers 阶段、空索引、已登记一册 workpaper artifact）`_completion_gaps()` 非空，`finish(done)` 返回 `partial`；UI 冒烟测试 `test_papers_checked_without_report_scope_is_partial` 通过。
- **回归风险 R2（中）**：`agent_ui.py:90` 仍写着"留空则由 Agent 列出后询问"，但 `report_indexes` 在启动时冻结（`agent_ui.py:272-275,299-305`），注册表里也没有任何工具能回填它（`agent_tools.py:535-625`）。因此"留空让 Agent 询问"的流程即使模型 `list_report_projects` + `ask_user` + 准备/导出多册，最终也**必然 `partial`**。建议：要么界面禁止留空（要求填索引），要么允许运行中经审批把用户回答写回 `context.report_indexes`，要么把"本次已准备且在列表中的项目集合"作为完成基准并同步文案。

### 2.7 M6：模型请求审计（已修复）

- 修复点：`AgentContext.model_requests`（`agent.py:198`）；`spend_model_call` 记录声明 scope（`agent.py:236-241`）；主循环每次模型请求后 `emit("model_request")` + `_audit("agent_model_request", messages, chars, scope)`（`agent.py:735-744`）；`run_tb_ai_fill`/`prepare_workpaper` 在发送前记录工具级 `agent_model_request`，scope 分别为 `[tb,ledger]`、`[workpaper,journal,knowledge]`（`agent_tools.py:166-168,385-386`）。
- 验证：探针两轮脚本运行得到 2 条 `agent_model_request` 审计（含 `messages/chars/scope`）、2 条 `context.model_requests`、2 个 `model_request` 事件；工具级探针得到 `source=run_tb_ai_fill, scope=[tb,ledger]` 与 `source=prepare_workpaper, scope=[workpaper,journal,knowledge]`。
- **残留（低）**：运行级审计记录的是**已授权 scope 集合**（`allowed_scope`），不是本次 payload 实际类别；审计写在请求返回之后，`chat_tools` 抛 `AIError` 的失败请求不会留下 `agent_model_request`；`run_tb_ai_fill` 的 `messages` 字段实际是批次 target 数，语义略偏。建议在请求前记录"声明类别+条数"，并覆盖失败请求。

### 2.8 L6：finish 参数校验（已修复）

- 修复点：主循环拦截 `finish` 时先按注册的 schema `_validate_arguments`，不合法则把错误作为工具结果回传并继续循环（`agent.py:750-758`）。
- 验证：单测 `test_finish_schema_is_validated_before_completion`（`tests/test_agent.py:255-264`）通过；探针：带 `injected_path` 的 finish 被拒（"未定义参数"）、缺 `summary` 被拒（"缺少参数"），随后合法 `finish` 返回 `done`。
- **残留（低）**：非法 finish 不消耗工具预算（只受模型请求/步数上限约束）；`_finish` 仍把未知 `status` 归一为 `done`（`agent.py:698-700`），属语义宽松，不影响本地完成核对。

### 2.9 L9：模型结果最小化路径（部分修复）

- 已修复：`get_project_context` 的 `artifacts` 只回 `kind/name/target/sha256`（`agent_tools.py:79-82`）；`scan_template` 回模板文件名（`:130-133`）；两个导出工具回输出文件名（`:313-315,501-503`）。探针确认这些字段中无绝对路径。
- **仍存在（中低）**：每个 `_ok` 结果固定带 `"revision": _revision(context)`（`agent_tools.py:18-19,26-29`），而 `ProjectSnapshot.revision()` 含 `str(template_path)`（`agent.py:141-143`）。生产 `template_path` 来自文件选择框（绝对路径），因此模型仍能看到形如 `P:/客户A/项目资料/template.xlsx` 的绝对路径（探针已复现），可能泄露客户目录/用户名。建议 `_revision` 改为 `(app_revision, ledger_digest, template_digest, Path(template_path).name)` 或只回模板摘要，不回归路径。

### 2.10 L1：finish 重算哈希（已修复）

- 修复点：`_artifact_ok` 现在比对登记值与当前文件 `_sha256`（`agent.py:645-654`）。
- 验证：探针登记 TB/TB待处理 artifact 后篡改 TB 文件 → `finish(done)` 降级 `partial`（缺口"TB 副本文件缺失或已被移动"）；不篡改 → `done`。
- **残留（低）**：哈希不符时缺口文案仍是"缺失或已被移动"（`agent.py:663-664`），与"文件被修改"不符；无对应新增测试；建议改为"缺失或哈希不符"并补用例。

### 2.11 L2：TB 结果纳入来源指纹（已修复）

- 修复点：`ProjectSnapshot.results_digest` 进入 `revision()`（`agent.py:139-143`）；`_results_digest` 基于每项 `(target.id,status,selected,amount,source_ids)` 取 16 位 sha（`agent_ui.py:183-191`）；`_snapshot` 写入启动值（`:207`）；`_current_fingerprint` 使用实时值（`:217-218`）；运行线程闭包使用普通属性 + 实时结果摘要（`:294-297`）。
- 验证：探针用"结果列表即时摘要"模拟生产指纹，初始相等；改动某项 `selected` → `StaleContextError`；仅改 `app_revision` → `StaleContextError`。`AgentContext.ensure_current`（`agent.py:214-218`）据此可在运行中检测 TB 结果与修订号变化。
- **残留（见 R1，中低）**：闭包使用 `snapshot.ledger_digest/template_digest`（启动快照），因此模板文件在磁盘上被替换、账套行在界面外变化不会在运行中触发 `stale`；不过模板写入前 `workbook.write_results` 仍做模板摘要校验（`workbook.py:309-310`），底稿导出仍做来源指纹校验（M2），实际不会写出"来源已变"的产物。

## 3. 回归风险清单

| 编号 | 风险 | 证据 | 建议 |
| --- | --- | --- | --- |
| R1 | 运行期 `stale` 检测不完整：模板文件磁盘替换/账套外部变化不触发 `stale`，与设计 6.2 "主体、期间、模板…任一变化即 stale" 不完全一致 | `agent_ui.py:294-297`（闭包用 `snapshot.ledger_digest/template_digest`）；对比 `agent_ui.py:211-218`（主线程 `_current_fingerprint` 才重新哈希模板） | 闭包里加入 `fields.file_digest(snapshot.template_path)` 的轻量 mtime/size 检查，或明确文档为"导出时校验" |
| R2 | M7 与 UI 文案冲突：留空索引必 `partial`，但界面承诺"留空则由 Agent 列出后询问"，且无工具回填 `report_indexes` | `agent_ui.py:90,272-275`；`agent.py:667-673`；`agent_tools.py:535-625` 无回填工具 | 三选一：禁止留空 / 运行中经确认扩写 `report_indexes` / 改完成基准与文案 |
| R3 | 被拒/未知工具调用现在计入工具预算并参与重复检测，模型重试会导致运行以 `error` 结束（fail-closed，但文案不提"已被拒绝"） | `agent.py:517-524`；探针：`max_tool_calls=2` 时 5 次被拒调用 → 预算错误；3 次同参被拒 → "连续三次以相同参数调用"错误 | 被拒工具跳过重复检测或错误文案注明"用户已拒绝该工具，继续调用将结束运行"；同步文档 |
| R4 | M3 的失败结果也会触发范围审批；`scope_blocked` 标记在裁剪后丢失 | `agent.py:553-565`；探针 `prompts=[{journal}]`；`scope_blocked` 未保留 | 先裁剪再审批（仅 `ok` 结果），或保留 `scope_blocked` 供审计/UI |
| R5 | `agent_write_approved` 审计先于"批准后取消检查"；若随后取消会出现"已批准但未执行"且无取消事件 | `agent.py:545-548` | 审计移到 `check_cancel` 之后，或补 `agent_write_cancelled` |
| R6 | worker 读取 `app.results` 普通属性做实时摘要，理论上与主线程改写竞争（GIL 下多为安全，但可能得到撕裂摘要导致误 stale/误通过） | `agent_ui.py:185-191,294-297` | 主线程发布不可变摘要副本（如 `app._agent_results_digest`），worker 只读该单值 |
| R7 | `_finish` 载入 TB 结果时的指纹比较用原始 `app.template_var` 字符串与 `snapshot` 的 `str(Path(...))` 比较，用户输入正斜杠路径时可能不相等而不载入（预存在，非本次引入） | `agent_ui.py:401` vs `agent.py:141-143` | 统一用 `str(Path(...))` 归一化 |
| R8 | 验收/冒烟未覆盖 M4/M6/M7(已准备一册的空索引)/L1/L9/L2 与真实确认桥 | 现有新增测试见 §4；冒烟测试替换了 `_ask_approval`（`tests/test_agent_ui_smoke.py:172`） | 见第 5 节建议用例 |

## 4. 已实现且可信的防线（回归确认，未退化）

1. **写文件仍需确认且路径不可由模型指定**：`write` 工具必经 `_approve_write`，工具只用 `runtime.approved_path`；schema 无路径参数（`agent_tools.py:555-558,596-602`）；拒绝后进入 `_blocked_tools`（`agent.py:534-543`）。
2. **文件写入双重保护**：`export_tb_copy`/`export_workpaper` 拒绝原模板/已存在文件（`agent_tools.py:277-281,461-463`）；`workbook.write_results` 再做同文件/只读/摘要校验并以 `os.rename` 发布（`workbook.py:250-313`）；底稿走临时文件+`finally` 清理（`agent_tools.py:477-496`）。
3. **完成判定本地化 + 哈希复核**：`_completion_gaps` 只读本地 artifacts，缺项 `done→partial`（`agent.py:656-694`）；`_artifact_ok` 重算 SHA（`:645-654`）。
4. **来源失效检查**：每步/每次导出 `ensure_current`（`agent.py:214-218,731-732`；`agent_tools.py:269,451`）；TB 导出另有 `workbook.validate_source_context`/模板摘要（`workbook.py:24-41,309-310`）；底稿导出有 M2 来源指纹复核（`agent_tools.py:453-460`）。
5. **回程范围校验 + 失败结果裁剪**（M3，`agent.py:553-565`）；**出站前范围检查**（`agent_tools.py:166,385` 在发送前调用 `spend_model_call`）。
6. **凭证与日志口径**：运行记录/audit 不含 Key（`ai.py:206-209`、`settings.py:26-37`、`agent.py:723-725`）；设计文档已按实际行为修正"模型答复按文字保存"的口径（`571ae96`）。
7. **M5 取消语义**：确认弹窗内可直接"停止本次运行"，停止后不再写入（真实 Tk 端到端验证）。

## 5. 探针与测试记录

### 5.1 端到端 Tk 探针（M5）

构造真实 `gui.App`（隔离 settings/autodetect/auditlog/知识库，脚本化 `DeepSeekClient`，mock Excel 写入），脚本为 `scan_template → export_tb_copy`；在真实 `ApprovalDialog` 出现时程序化点击"停止本次运行"。结果：

```
dialog_stop_clicked: True
status: cancelled
write_calls: 0 | report_calls: 0 | files: []
template_intact: True
events: [..., 已拒绝写入, 已停止]
```

### 5.2 合成探针（只读，未入库）

| 探针 | 结果 |
| --- | --- |
| M1 | 未扫描上下文明细"2 个" = 扫描后 `selected=2` = mock 实际写入 2 |
| M2 | `validate_paper_data_source=False` → `ok:false`、无文件、gap；`=True` → 正常出册；无 `source_path` → 跳过校验 |
| M3 | `ok:false+data` 未授权 → 无 `SECRET` 泄漏；失败结果仍触发一次 scope 询问 |
| M4 | `start` 源码无 `template_var`/`_current_fingerprint`；`evidence_draft.source_key` 仍含 `template_var` |
| M5 | 审批回调内取消 → 写入调用 0、`cancelled`；真实弹窗停止 → 同上（§5.1） |
| M6 | 2 条 `agent_model_request`（messages/chars/scope）、2 条 `model_request` 事件、2 条 `model_requests`；工具级审计 scope 正确 |
| M7 | 空索引 + 已准备一册 → 缺口非空、`finish(done)`→`partial` |
| L1 | 篡改 artifact → `partial`；未篡改 → `done` |
| L2 | 结果摘要/修订号变化 → `StaleContextError`；初始相等 |
| L6 | schema 外字段/缺 summary 被拒，重试合法 finish → `done` |
| L9 | 显式路径字段已最小化；`revision` 仍含绝对 `template_path`（`P:/客户A/项目资料/template.xlsx` 复现） |
| R3 | 被拒工具 5 次调用（`max_tool_calls=2`）→ 预算错误；3 次同参 → 循环停止错误 |

### 5.3 未执行/环境限制

- 未连接真实 DeepSeek，未读真实账套/客户 TB/Key；生产 DXN 来源复核（M2）只用 mock 数据。
- `tests.test_agent_acceptance` 中真实 Excel 用例因本机 Excel COM `-2147352567` 内存错误被 `skip`，不代表代码失败；其余 3 例通过。
- 真实确认弹窗的"停止"用程序化点击验证（§5.1），未做人工手测；主窗口 Stop 在模态确认期间的可用性仍受 `grab_set` 限制（可用弹窗按钮）。

## 6. 建议补充的测试（本次不得改测试，仅建议）

1. `test_agent.py`：M4——把 `template_var` 替换为抛异常的假对象，跑一次 `start→finish`，断言无 Tk 读取异常；L2——用可变的结果列表 + `current_fingerprint` 闭包副本，断言 `StaleContextError`。
2. `test_agent_tools.py`：M7 的"空索引 + 已准备一册"用例（当前只有 UI 冒烟覆盖空索引无准备）；L1 的哈希不符缺口文案；L9 断言 `_ok`/`get_project_context` 序列化后不含 `template_path` 绝对路径。
3. `test_agent.py`：M6 断言 `audit` 收到 `agent_model_request`（messages/chars/scope）且 `context.model_requests` 长度正确。
4. `test_agent_ui_smoke.py`：不替换 `_ask_approval`，用 `after` 自动点击"停止本次运行"覆盖真实确认桥（本次复核探针即此形态，可固化为测试）。
5. 为 R3 补一条"被拒工具重试"的预期行为测试，明确是 fail-closed 还是继续。

## 7. 复核后追加修复（主代理 2026-11-23）

复核报告以 `571ae96` 为基线；之后主代理继续处理了报告中的残留：

- **M4**：移除 worker 侧的 `evidence_provider`，证据行在启动时由主线程按 `report_indexes` 预取；`export_workpaper` 仍兼容 provider，但 UI 不再传入。
- **L9**：`_revision()` 不再回传绝对路径，只回 `app_revision/ledger_digest/template_digest/results_digest`。
- **R2**：勾选整册底稿时必须填写 `report_indexes`；留空时启动前提示并拒绝开始，文案改为“必填”，不再承诺“Agent 列出后询问”。
- **R1**：运行线程指纹在每步读取模板文件 SHA-256，模板磁盘变化会触发 stale；账套外部变化仍依赖导出时来源校验。
- **R4**：失败结果先裁剪 `data` 再做范围校验，仅成功结果需要范围审批。
- **R5**：`agent_write_approved` 审计移到二次取消检查之后；取消时记录 `agent_write_cancelled`。
- **R7**：`_finish` 比较前把模板路径归一化为 `str(Path(...))`。
- **L1 文案**：哈希不符的缺口改为“缺失、被移动或哈希不符”。
- **M6 失败请求**：`chat_tools` 抛错前记录 `agent_model_request(status=failed)`。

仍未处理：R3 的 fail-closed 文案、R6 的 worker 结果摘要理论竞争、M6 的 payload 实际类别逐项计数、L8 的逐类勾选/预算面板。真实 DeepSeek 与 Excel COM 仍需本机非沙箱联调。
