# DeepSeek 交付复核（2026-11-05）

本文件按任务书要求提交给 Codex 独立复核。本轮只做本地开发、测试与候选包，**未推送、未发布、未替换** `P:\exetoBe\软件开发\审计底稿软件` 下的 0.6.2 在用目录，也未编译正式 EXE。

本地提交：`a657629 feat: assemble report-scope papers with template library coverage [skip ci]`（父提交 `5366841`），复核文档提交 `11a7081`；候选包按 `11a7081` 冻结构建，本节校验值在构建后追加，不影响包内容。未推送、未发布、未替换在用 0.6.2 目录。

## 一、结论摘要

1. 底稿已从「一册一个一级科目」改为**按报表项目整册**：货币资金含库存现金、银行存款、其他货币资金；固定资产含原值、累计折旧及对应减值；无形资产含原价、累计摊销及对应减值。科目树只用于查看，点击不改变出册范围。
2. 往来明细按账套实际选中的客商核算维度列示，同名不同编码不合并、父子不重复、辅助与总账差异保留、缺值留空。
3. 损益明细表有 1—12 月发生额列及本期、上期发生额，只取独立发生额字段（`c_kmyek.fse`、`c_cwkmk.ws_fse`/`sn_ws_fse`），不再用借贷单边累计或结转后余额代填。
4. 17 套安装标准全部完成静态结构解析与原件保留：5,747 个工作簿、35,902 张表、910,906 条可读公式、13,649 条未解析、解析失败 0。运行时模板目录仍只绑定「企业会计准则(已执行新金融工具、新收入和新租赁准则)」一套。
5. 完整回归 **611 项全部通过**（37 个测试模块）。0.6.3 未编译开发候选包已生成（ZIP SHA-256 `7a9c3ca1…a4b83`，737.8 MB），模板原件随包携带并逐文件校验。

仍未完成：附注与报告生成（界面不提供可用生成按钮）、原插件外部函数内部算法、其余 16 套标准的运行时用表规划、真实账套业务验收。

## 二、本轮修改（文件与具体行为）

### 数据层

- `tb_autofill/models.py`
  - `LedgerRow` 增加 `period_amount`、`previous_period_amount`、`monthly_amounts`、`previous_monthly_amounts`、`period_source`、`previous_period_source`。
  - 新增 `AuxiliaryBalanceRow`、`PaperData`（同次一致副本的科目、辅助余额、凭证、核对结果、警告、来源指纹）。
  - 新增 `ledger_row_from_record()`：缓存/快照统一还原口径（金额回 `Decimal`、月份固定 12 位、缺失保持 `None`）。此前 JSON 往返把元组变列表，导致同一科目在缓存前后不相等（见「代码评审记录」第 1 条）。
- `tb_autofill/dxn.py`
  - `read_paper_data(subject, codes, install_path, with_journal, status_callback)`：一次一致副本读取所选报表项目，附带客商维度、月度/上期发生额与序时账。
  - `validate_paper_data_source(data)`：重锁来源并逐文件比对 SHA-256、大小与修改时间；来源成员变化、之前缺失的表后来出现，均判为失效。
  - `_aggregate()` 增加月度 `fse` 与独立本期发生额：仅当 1 月至当前月完整且无缺值时才求和，否则为 `None`。
  - `_paper_occurrences()`：上期优先取同主体、同账套上年同期（1 月至同一月份）；`c_cwkmk.ws_fse/sn_ws_fse` 仅在 12 月口径使用。
  - `_auxiliary_balances()`：按 `c_kmvshsxm` 中该科目实际配置且标记选中的维度解析，不硬编码 KH/GYS；同科目多维度、同名不同编码、同月重复、方向/名称期间冲突一律停止或标记歧义。
- `tb_autofill/audit_index.py`
  - `resolve()` 对含「坏账准备」的科目改走 `_reserve_owner()`：只有名称能明确对应应收类别时才归属，否则返回未确认。
  - `reserve_components(rows, paper, library)`：共享坏账准备按明确子类分派；总根与子类不符、类别缺失、方向或账套期间不一致时抛错，禁止整根计入应收账款。

### 报表项目整册导出

- `tb_autofill/dxn_paper.py`
  - `group_accounts()` 追加 `reserve_components()` 的备抵子类，形成整册科目集合。
  - `presentation_direction()` 按原模板「报表项目借贷」乘数决定列示方向（资产借、负债/权益/收入贷、费用借）；`totals()` 对余额类按列示方向取符号，借贷发生额各自保留方向，任一来源缺失即为未知。
  - `consistency()` 损益科目改按独立发生额核对，缺来源返回 `(None, None)` 而不是报差异。
  - `AUDIT_COLUMNS_PL` 列名改为「本期账面发生额 / 本期未审发生额 / 本期审定发生额 / 上期…」，避免把余额叫成本期数。
  - `build(..., accounts=None, paper_data=...)`：校验客商与发生额来源与本册科目快照一致；同科目多维度、跨账套期间直接停止导出。
- `tb_autofill/paper_sheets.py`
  - `movement_rows()` 损益行改用 `period_amount`/`previous_period_amount`，缺上期时明确写「未提供」。
  - `asset_component_rows()`：原值、累计折旧/摊销、减值按相同明细名衔接；名称歧义不分摊；某类备抵科目在账套中不存在时该栏留空、净额按零计；科目存在但金额未知时净额留空。
  - 银行账户识别不再限定 4 位科目代码。
- `tb_autofill/paper_pack.py`
  - `period_amount()` 只取独立发生额，缺来源返回 `None`。
  - `reconcile()` 损益缺发生额时返回 `consistent=None`；新增 `verdict_text()`/`check_status()` 三态文案，未知不再显示成「不一致」。
  - 导引表勾稽行缺依据时显示「待补 / 缺少发生额依据」，不再把 `None` 格式化成金额。
- `tb_autofill/papers.py`
  - 新增 `report_scope(rows, paper, library)`；`pack_request(code='')` 支持报表整册，旧单科目接口保留。
  - AI 事实增加 `current_period`/`previous_period`、`monthly_rows`、`auxiliary`（最多 50 条）、`auxiliary_checks`、`source_warnings`；`source_path/source_tables/source_fingerprint` 不进入请求。
- `tb_autofill/analysis.py`：`fact_amounts()` 覆盖新增的月度、客商与差异字段，模型回答中的金额校验同步扩展。

### 界面与 AI

- `tb_autofill/gui.py`
  - `PaperDialog` 以报表项目为任务键；下方树只展示整册科目与明细，选择变化不清空草稿、不缩小范围。
  - 接入 `PaperData` 与来源重验：预览前、发送前、导出前分别校验来源，失效即清空草稿。
  - 专项表建议使用独立结构化请求；必备表不可删，未知表名不采纳，失败明确提示。
- `tb_autofill/workspace.py`：底稿页标题、说明、步骤与按钮改为「制作报表项目底稿」，总览卡片同步。

### 模板库与证据

- 新增 `tools/harvest_dxn_templates.py`（507 套单标准采集）与 `tools/harvest_dxn_library.py`（17 套批量清单，含 `--reuse`/`--only`/输出目录防越界）。
- 新增 `tb_autofill/dxn_templates.py`：按报表项目查候选工作表、生成用表规划上下文、按原模板字段重建参考栏目；`spec_for()` 明确写「未执行原软件插件公式」。
- 新增 `tb_autofill/assets/dxn-templates.json`（结构目录，1.8MB，只含名称、字段位置、结构与公式示例，不含金额）。
- 文档：`docs/dxn-template-evidence-2026-11-05.md`（单标准证据，已补全量进展）、`docs/dxn-template-coverage-2026-11-05.md`（17 套逐目录统计）、`docs/release-v0.6.3-dev.md`。

### 打包与版本

- `tb_autofill/__init__.py`：0.6.2 → 0.6.3。
- `tools/build_dev.py`：新增 `--template-library`/`--skip-templates`；`carry_templates()` 逐文件比对各标准 `ORIGINALS.sha256` 后写入 `templates/TEMPLATES.sha256`，清单哈希进 `BUILDINFO.json`；模板原件缺失即拒绝打包；打包阶段源码必须干净且提交号不变。
- `tools/build_release.py`：`NATIVE_MODULES` 增加到 25 个（含 `dxn_templates`），正式 Nuitka 发布门槛同步。
- `packaging/开发版说明.txt`、`README.md`、`AGENTS.md`：删除「每册只含一个一级科目」旧约定，改为报表项目整册，并写明模板原件随包校验方式。

## 三、取数与公式依据

1. **报表项目合册**：原模板 `披露表(标准)!C5:C7`（货币资金）分列库存现金、银行存款、其他货币资金；固定资产 `E35=E13-E24`、`E57=E35-E46`，原值、折旧、减值逐层勾稽；无形资产 `C5/C19/C33/C47` 分列原价、累计摊销、减值准备。整册归集按此结构，不按科目代码硬编码。
2. **列示方向**：固定资产 `审定表!E10` 为 `报表项目借贷(...) * (SUMIF(方向,"借",金额)-SUMIF(方向,"贷",金额))`，即方向乘数；备抵科目按真实方向参与净额，未把每个根科目转正后相加。
3. **独立发生额**：管理费用模板隐藏字段声明 `审定表 E7/G7/M7` 为账面/未审/审定发生额，`O7/Q7/T7` 为上期，`明细表 G8:R8` 为 1—12 月发生额。因此只使用 `fse` 类独立字段，`fse_1..12` 为月度，`c_cwkmk.ws_fse`/`sn_ws_fse` 只在 12 月口径作全年来源。
4. **客商维度**：`c_kmvshsxm` 记录科目下实际配置的核算维度及选中标志，`c_hsxm_yye` 提供各维度余额；维度码随账套变化，因此按配置解析而非写死 KH/GYS。
5. **共享坏账准备**：`b_sjcx.txt` 要求结合坏账准备核对，但安装字典没有「所有 1231 都归应收账款」的依据，故按明确子类名称分派，关系不明时阻止净额。

## 四、测试执行记录

全部使用合成数据；未打开真实账套、未发起模型请求、未运行会弹窗的交互步骤（UI 测试沿用意外对话框拦截）。

| 命令 | 范围 | 结果 |
| --- | --- | --- |
| `.venv/Scripts/python.exe -B -m unittest <37 个 tests/test_*.py 模块>` | 完整回归 | **Ran 611 tests in 365.6s — OK** |
| 同上（提交前一次） | 完整回归 | Ran 611 tests in 151.7s — OK |
| `-m unittest tests.test_paper_data_sources tests.test_dxn_template_harvest tests.test_report_paper_data tests.test_paper_analysis_cutoff tests.test_paper_pack tests.test_primary_papers tests.test_qa_guards tests.test_dev_package tests.test_review_guards tests.test_workspace` | 重点区域 | Ran 155 tests in 45.3s — OK |
| `-m unittest tests.test_report_components tests.test_report_paper_data` | 备抵与客商 | Ran 22 tests in 10.4s — OK |
| `-m unittest tests.test_dxn_paper` | 整册导出 | Ran 28 tests in 81.8s — OK |
| `-m unittest tests.test_paper_pack` | 底稿包与勾稽 | Ran 32 tests in 8.9s — OK |
| `-m unittest tests.test_primary_papers` | 界面与导出 | Ran 19 tests in 14.8s — OK |
| `-m unittest tests.test_paper_analysis_cutoff` | 分析与截止 | Ran 20 tests in 32.1s — OK |
| `-m unittest tests.test_paper_data_sources` | 数据源与客商/发生额 | Ran 24 tests in 1.3s — OK |
| `-m unittest tests.test_dxn_template_harvest` | 模板采集 | Ran 10 tests in 0.9s — OK |
| `-m unittest tests.test_dev_package` | 开发包与模板携带 | Ran 6 tests in 0.8s — OK |

本轮新发现并修复的失败（不是旧记录）：

1. `tests.test_primary_papers.PaperUiTests.test_workspace_button_opens_paper_dialog_without_api_key`：按钮文案仍为「制作底稿（一级科目）」，改为「制作报表项目底稿」后通过。
2. `tests.test_paper_materials.BaselineTextTests.test_profit_loss_notes_use_period_amount`：损益说明丢失「上年账套」来源提示，补齐同主体同期间口径后通过。
3. `tests.test_qa_guards...test_cache_roundtrip...`：缓存往返把 `monthly_amounts` 元组变列表，`LedgerRow` 不相等；统一改用 `ledger_row_from_record()` 后通过。
4. `tests.test_review_guards.ReleaseCoverageTests`：`NATIVE_MODULES` 缺 `tb_autofill.dxn_templates`，补齐为 25 个后通过。
5. `tests.test_connections_knowledge` 导入 `test_ai` 依赖运行目录，改为兼容 `tests.` 前缀后通过。

## 五、模板覆盖统计与未实现项

逐目录数字、原件清单哈希见 `docs/dxn-template-coverage-2026-11-05.md`。汇总：

- 17 套标准、5,747 个工作簿、35,902 张表、910,906 条可读公式、13,649 条未解析、0 个解析失败、5,891 个原件文件（含各标准配置）。
- 运行时绑定：仅「企业会计准则(已执行新金融工具、新收入和新租赁准则)」1.6.2.0（507/3,157），用于用表规划与参考栏目；其余 16 套只有静态结构 + 原件。
- 未解析公式：xlrd 对相对名称/操作数异常，或引用处没有共享/数组定义；保留单元格、令牌 SHA-256 与原因，未猜测补写。
- 外部函数（`报表项目借贷`、`取报表项目数`、`取月余额`、`账表差异调整`、`重分类分录` 等）只取得名称与调用参数，内部算法未取得。
- `.frm` 仅有的 InnoDB 对应关系表未能从一致副本证明绑定，仍按「关系不明」处理，不做净额推断。

## 六、本地候选包

- 版本：0.6.3，未编译（developer-source），Python 3.12.10，构建源提交 `11a7081`。
- 目录：`dist/DeepAudit-0.6.3-dev/`；压缩包：`dist/DeepAudit-0.6.3-dev/DeepAudit-0.6.3-dev-windows-x64.zip`（737.8 MB）。
- ZIP SHA-256：`7a9c3ca1551159fe6b5bad44bc4fc69988d4e0b834e73e0c57de9960e96a4b83`。
- `FILES.sha256`：8,524 行（解压后逐文件校验）；`templates/TEMPLATES.sha256`：5,891 行，清单哈希 `61250d11cab55ae266131bb891cef78b7d64e69a089ded71453962191540e8e3`，已写入 `BUILDINFO.json`。
- `BUILDINFO.json`：`templates: {standards: 17, files: 5891}`，`compiled: false`。
- 自检（9 项，全部通过）：`decimal_amount`、`dpapi_key`、`audit_checkpoint`、`protected_header`、`local_knowledge`、`pdf_runtime`、`report_readiness`、`brand_assets`、`paper_index`。
- 附加校验：`zipfile.testzip()` 返回 `None`，ZIP 共 8,525 个条目，含 `templates/TEMPLATES.sha256`、`tb_autofill/assets/dxn-templates.json`、`BUILDINFO.json`；包内运行时复跑 `runtime/python.exe -I -B tools/run_gui.py --selftest` 输出 `DeepAudit selftest ok 0.6.3`。
- `dist/` 现只保留本版本目录，`DeepAudit-0.6.2-dev` 已按约定清理；在用安装目录 `P:\exetoBe\软件开发\审计底稿软件` 未被改动。
- 复现命令：

```text
.venv/Scripts/python.exe -B tools/build_dev.py
```

默认读取 `local-data/dxn-library-20261105`，可用 `--template-library <目录>` 指定、`--skip-templates` 只打程序。打包要求工作树干净且提交号在构建期间不变，否则拒绝生成清单。

## 七、代码评审记录（本轮发现并修正）

1. **缓存/快照还原口径重复且不一致**：`tools/qa/per_file_check.py` 与 `tools/qa/eval_ai_review.py` 各自反序列化 `LedgerRow`，月份字段还原不完整。统一到 `models.ledger_row_from_record()`，两处共用，删除重复代码。
2. **来源重验函数尾部重复计算**：`validate_paper_data_source()` 既在开头比较成员集合，又在结尾重算一遍并重复检查缺表。抽出 `_present_table()`，单次校验后直接返回。
3. **三态勾稽被压成二态**：损益缺独立发生额时 `consistent` 曾为 `False`，界面会写成「不一致，需追查」。改为 `None` 并新增 `verdict_text()`/`check_status()`，未知显示「缺少发生额依据，待补」。
4. **资产组成里的硬编码**：`asset_component_rows()` 曾用 `root.code[:4] in {"1601","1701"}` 与名称白名单判断原值科目。改为结构性判断（非累计折旧/摊销、非减值准备即原值科目），删除科目代码硬编码。
5. **缺项被当作零**：净额曾用 `(accumulated or 0)`；改为「科目不存在按零、科目存在但金额未知则净额留空」，并补一条合成测试锁定。
6. **打包可能静默少带标准**：`carry_templates()` 原先跳过没有原件目录的标准；改为列名报错并拒绝打包。
7. **模板库采集目录重复**：批量采集产生的 `dxn-library-supplement` 合并后删除，仅保留 `dxn-library-20261105`；`dist/` 只保留最新一版。

## 八、需重点复核与保留风险

1. **`c_kmyek.fse` 的语义**：实现按「月度独立发生额」使用，依据是安装模板字段声明与 `c_kmyek2.fse_1..12` 的对应关系，但未在真实账套逐月比对。若某账套 `fse` 实为累计口径，1—12 月列会失真。建议 Codex 取一个已知账套核对 1 月与 12 月。
2. **上年同期来源**：上期数只取同主体、同账套上年 1 月至同月；跨年账套复制、上年账套号变化时会留空，这是有意保守，需要业务侧确认可接受。
3. **共享坏账准备分派**：依赖子科目名称能明确对应应收类别（应收账款/其他应收款/应收票据/长期应收款/预付账款）。名称不规范时整册报错阻止，而不是猜测。
4. **客商维度选择**：按 `c_kmvshsxm` 的选中标志与中文维度名（客户/供应商/客商/往来单位）识别。若账套用其他命名（如「往来户」），会提示「未取得客商维度」而不猜。
5. **模板原件体积**：候选包内含 5,891 个原件文件（约 850MB 未压缩），ZIP 中按存储方式打包，体积较大；如只需程序，用 `--skip-templates`。
6. **未做真实账套验收**：所有测试为合成数据；导出格式、界面流程尚未在真实项目上跑通。
7. **附注/报告**：仍不可用，界面不提供生成按钮，符合本轮边界。

## 九、未完成事项

- 其余 16 套标准的运行时用表规划（当前仅结构 + 原件）。
- 原插件外部函数内部算法、1,192 + 12,457 条未解析公式的补齐。
- 真实账套一致性副本上的业务验收与性能验证。
- 正式 EXE 的 Nuitka 编译（本轮不编译）。
