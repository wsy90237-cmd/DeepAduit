# ZSZH TB 自动填写系统

Windows 桌面工具：从**鼎信诺科目余额明细**只读取数，按用户导入的模板**识别字段并填列期初 / 期末**，并对现有 TB 做复核。
所有金额由本地规则计算，AI 只提供「来源科目」建议，不能改写金额。

## 核心约束

- **原库永不写入**：取数前对科目余额表文件加独占只读锁，复制一致副本并校验哈希，再用副本启动临时 `mysqld`（`--skip-grant-tables`、随机端口、127.0.0.1）。复制期间源文件发生变化即中止。
- **不依赖固定列**：目标位置由表头文字、合并单元格、科目层级与期间推断，不使用固定 D/E 列或行号。
- **默认只补空**：空白单元格预勾选；已有值一律不勾选，覆盖需逐项确认。
- **公式受保护**：公式单元格不参与写入，写后在 Excel 中重算并逐格比对，任何非目标单元格变化即判定输出失败。
- **账面 ≠ 审定**：账面数据不能填审定列；余额、发生额、损益方向发生额严格区分；真实零值与缺失值分开处理。
- **AI 只建议**：请求前可预览上传内容，返回结构经白名单校验，金额仍由 `resolve_match` 本地计算；无 Key 时全部功能可离线使用。
- **Key 本机加密**：API Key 以 Windows DPAPI 加密存放在 `%LOCALAPPDATA%\ZSZH-TB\settings.json`，不进入项目目录，不随代码提交。

## 目录结构

```
tb_autofill/
  dxn.py        鼎信诺只读取数（加锁、副本、临时 mysqld、科目聚合）
  fields.py     模板表头 / 期间 / 单位 / 借贷侧识别
  matching.py   文字与层级匹配、重复认领校验、AI 建议落地
  workbook.py   Excel COM 写入、写后全表比对、CSV 复核报告
  ai.py         DeepSeek 客户端（模型动态发现、返回结构严格校验）
  settings.py   DPAPI 密钥与本机设置
  models.py     Decimal 金额与数据模型
  gui.py        tkinter 客户端界面
tests/          59 项单元测试 + 合成模板夹具
docs/           需求与验收、字段匹配证据、联调记录
packaging/      PyInstaller 配置
```

## 环境要求

- Windows 10/11（`dxn.py` 依赖 `msvcrt`/`kernel32`，Excel 写入依赖 COM）
- Python 3.12 及以上
- 已安装 Microsoft Excel（另存与重算使用 `win32com`，WPS 未验证）
- 鼎信诺 7100 / MyISAM 科目余额库（`data/powersj/c_kmyek`）

## 安装与运行

```bat
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m tb_autofill
```

也可 `python launch.py`，或安装为脚本后运行 `zszh-tb-autofill`。

## 使用流程

1. **① 数据源**：自动探测鼎信诺安装目录（或手动指定含 `bin\mysqld.exe` 的目录）→ 扫描项目 → 选择项目。
2. 选择**主体单位、年度、截止月份** → 读取科目余额（界面状态栏显示读到多少科目）。
3. **② 模板与期间**：选择 `.xlsx` / `.xlsm` 模板 → 选择仅期初 / 仅期末 / 期初+期末 → 金额单位（自动识别，冲突时手动指定）。
4. 点击**识别并匹配**：逐个字段给出状态（已匹配 / 待确认 / 未匹配 / 缺少依据 / 公式保护 / 重复认领 / 口径不符），并列出应填值、现有值、差额、来源科目与原因。
5. **③ 复核**：点击行首勾选框切换写入；已有值需二次确认覆盖；`指定来源…` 可人工选择多个科目（求和）；`AI 复核` 先预览上传内容再请求。
6. **导出复核报告（CSV）** 留痕，或 **另存为 Excel…** 生成填列结果（原模板不变，输出逐格校验）。
7. 双击任意行查看明细（来源科目的期初/期末/借贷发生额与判定原因）。

## 测试

```bat
.venv\Scripts\python -m unittest discover -s tests -v
```

59 项测试覆盖：别名与全角归一化、父科目限定、同名歧义、父子重复认领、损益方向发生额、自然方向负余额、借贷分列、期初/期末、单位换算、公式保护、真实零与缺失、账面/审定、混合主体期间、DeepSeek 返回体白名单、模板表头与公式识别。

合成模板生成：`python tests/fixtures/make_template.py`（全部为虚构数据）。

## 打包

```bat
.venv\Scripts\python -m pip install -r requirements-build.txt
.venv\Scripts\pyinstaller packaging\zszh-tb-autofill.spec
```

产物：`dist\ZSZH-TB自动填写.exe`。需在未安装 Python 的目标机验证取数、另存与 Key 管理。

## 已知限制

- 仅支持 `.xlsx` / `.xlsm` 模板；旧版 `.xls` 请先用 Excel 另存。
- 需本机安装 Excel；无 Excel 时仅可识别、匹配与导出 CSV，不能另存。
- 当前统一来源没有「上期发生额」，期初发生额类目标会报告缺少依据，不会用本年发生额顶替。
- 未把各行业特殊科目全部纳入自动规则；无法确定的名称与金额一律保留为待确认。
- DeepSeek 可用模型以账户 `/models` 返回为准；未开通的模型无法选用。

## 第三方内容

`.agents/skills/` 下为项目内使用的第三方技能说明（`ponytail` 标注 MIT），仅作开发约定参考，不参与打包。
