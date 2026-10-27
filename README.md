# DeepAudit 审计工作台

DeepAudit 从鼎信诺科目余额的只读副本取数，按用户模板识别、匹配、复核和另存 TB。金额由本地规则计算，AI 仅提供来源建议。当前版本提供 TB 功能，附注和底稿页面明确标注为规划中。

## 工作空间

- **项目总览**：主体、期间、科目数、字段数和待复核数量。
- **TB 填列与复核**：读取账套、导入模板、确认来源、复核差额、另存 Excel、导出 CSV。
- **财务报表附注 / 审计底稿**：后续工作流入口，不提供尚未实现的生成按钮。
- **安全与设置**：AI 设置、本机日志校验、发布保护范围。

## 数据保护

- 复制前持有源文件只读锁，数据库引擎仅打开经过校验的临时副本；原数据库不写入、不修复、不升级。
- 主体、年度、月份、模板、填列期间或单位改变时，旧匹配与 AI 提案失效。后台旧响应不能重新激活旧结果。
- 写入前核对完整账套指纹与来源成员；实际导出和留痕使用同一份不可混用的任务快照。
- 多层合并表头合并判断口径，保护审定数、调整列、公式和未选期间。
- 当前来源提供年初累计发生额；本月发生额缺少单月依据时报告缺少依据，不用累计额代替。
- 默认只补空，已有值需确认覆盖。Excel 重算后按前后有效单元格并集校验。
- API Key 用 Windows DPAPI 加密。旧存储目录 `%LOCALAPPDATA%\ZSZH-TB` 保留兼容，改名不会丢失已有设置。
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

构建脚本运行合成金额、DPAPI、日志截断与表头保护自检，且要求所有业务模块均为原生编译。任何自检失败都不会生成发布清单。版本只维护在 `tb_autofill/__init__.py`；标签与版本不符时拒绝构建。

GitHub Actions 在私有源码仓库测试、MSVC 编译并保存运行产物；推送匹配版本的标签后将上述文件挂到 GitHub Releases。未配置代码签名证书，不能声称已签名。

更多说明见 [安全与发布](docs/安全与发布.md)、[工作空间与扩展规划](docs/工作空间与扩展规划.md)。历史实际账套/API 联调记录保留在 `docs/联调记录.md`，合成测试不能代替真实联调。
