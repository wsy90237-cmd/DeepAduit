# DeepAudit 审计工作台

DeepAudit 面向企业年度财务报表审计。从鼎信诺科目余额的只读副本取数，按用户模板识别、匹配、复核和另存 TB；接入模型和财会知识，辅助复核 TB、附注、底稿及现有报告。金额由本地规则计算，AI 只提供建议。0.5.0 提供接入与辅助复核闭环，完整审计程序、附注/底稿生成及正式报告组装签发仍待实现。

## 工作空间

- **审计驾驶舱**：主体、期间、科目数、字段数、待复核数量，以及模型和知识来源入口。
- **账套与 TB**：读取账套、导入模板、确认来源、复核差额、另存 Excel、导出 CSV。
- **风险与计划 / 程序与证据**：全流程设计页面，明确当前尚缺的风险信息和审计证据。
- **财务报表附注 / 审计底稿**：选择现有 TXT、MD、DOCX、文字 PDF，结合知识片段辅助复核；生成与模板导出待实现。
- **报告中心**：动态准备清单、缺口说明、HTML 清单导出及现有报告文字复核；不自动出具审计意见。
- **财会知识库**：本地资料导入检索、ima 只读搜索、知识引用和 Agent 复核。
- **模型与安全**：模型 API 配置、鉴权与对话能力验证、本机日志校验、发布保护范围。

## 模型与知识库接入

在应用「AI 设置」中输入自己的 Key，验证凭证并获取模型，再按需验证对话能力。默认 DeepSeek，也支持提供 `/models` 与 JSON Chat Completions 的 HTTPS 兼容接口。切换 API 地址必须重新输入对应服务的 Key。对话能力测试会产生少量 token 用量，点击前有提示。

ima 需要独立的 Client ID 和 API Key，在 [ima 官方开放接口页](https://ima.qq.com/agent-interface) 自行配置，再到「财会知识库 → 配置 ima 连接」加密保存。搜索库名或填写库 ID 后检索正文；“已关注”不代表接口必然有读取权限。无法取得正文时不会把标题冒充内容，不能访问的库也不会自动导出或绕过授权。

暂时没有 ima 接口权限时，可导入自己有权使用的 TXT、MD、DOCX、文字 PDF。本地为关键词检索，不提供 OCR 或向量检索。选用 1–5 个正文片段后，Agent 请求先显示目的服务、模型、问题、历史和资料范围；确认后才发送。引用编号缺失或不存在时，提示并丢弃改动提案。引用编号校验不能证明原文适用性或模型结论正确。

详细步骤见 [模型与知识库接入](docs/模型与知识库接入.md)，全流程设计及阶段范围见 [年度审计引擎设计](docs/年度审计引擎设计.md)。

## 数据保护

- 复制前持有源文件只读锁，数据库引擎仅打开经过校验的临时副本；原数据库不写入、不修复、不升级。
- 主体、年度、月份、模板、填列期间或单位改变时，旧匹配与 AI 提案失效。后台旧响应不能重新激活旧结果。
- 写入前核对完整账套指纹与来源成员；实际导出和留痕使用同一份不可混用的任务快照。
- 多层合并表头合并判断口径，保护审定数、调整列、公式和未选期间。
- 当前来源提供年初累计发生额；本月发生额缺少单月依据时报告缺少依据，不用累计额代替。
- 默认只补空，已有值需确认覆盖。Excel 重算后按前后有效单元格并集校验。
- 模型与 ima 凭证分别用 Windows DPAPI 加密，存入 `%LOCALAPPDATA%\ZSZH-TB\connections.json`。新文件不存在时只读兼容旧 `settings.json`，保存不会更改旧版文件，避免旧版误用新服务 Key。
- 知识原文缓存在同目录的 `knowledge.sqlite3`，未加密；本机账户和磁盘访问权限仍需妥善保护。待复核文档只提取文字放入本次内存上下文，不自动加入知识库。
- 新日志有哈希链、文件锁及 DPAPI 末尾检查点，可发现仍有检查点时的尾部截断。旧日志没有检查点时只报告链内校验。不能抵御同一账户回滚全部日志与检查点。

## 运行与测试

需要 Windows 10/11 x64、Python 3.12+；推荐 3.13。Excel 另存依赖已安装的 Microsoft Excel 与 pywin32，WPS 未验证。

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python launch.py
.venv\Scripts\python -m unittest discover -s tests -v
```

开发预览使用隔离设置和合成数据：`python tools/preview_ui.py`。安装后的命令为 `deepaudit`，保留旧命令名兼容已有脚本。

## 编译客户发布包

正式发布改用 Nuitka 原生编译，不再提供直接分发业务 Python 字节码的 PyInstaller 配置。

```powershell
.venv\Scripts\python -m pip install -r requirements-build.txt
.venv\Scripts\python tools/build_release.py --compiler msvc --jobs 2
```

MSVC 需 Visual Studio 2022+ C++ 构建工具；本机没有 MSVC 可使用 `--compiler zig`，Nuitka 会下载其支持的编译器。源码保留开发能力，编译包用 isolated 模式，减少外部 Python 路径注入；这不等于无法逆向。

只分发以下文件：`dist/DeepAudit.exe`、`SHA256SUMS.txt`、`INSTALL.txt`、`BUILDINFO.json`。不要分发开发包、源代码、C 代码、编译报告、构建目录、真实数据或日志。

构建脚本运行合成金额、DPAPI、日志截断、表头保护、本地知识、PDF 依赖和报告准备度自检，且要求全部 15 个业务模块均为原生编译。任何自检失败都不会生成发布清单。版本只维护在 `tb_autofill/__init__.py`；标签与版本不符时拒绝构建。

GitHub Actions 在私有源码仓库测试、MSVC 编译并保存运行产物；推送匹配版本的标签后将上述文件挂到 GitHub Releases。未配置代码签名证书，不能声称已签名。

更多说明见 [安全与发布](docs/安全与发布.md)、[工作空间与扩展规划](docs/工作空间与扩展规划.md)。历史实际账套/API 联调记录保留在 `docs/联调记录.md`，合成测试不能代替真实联调。
