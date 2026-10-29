# 维护脚本

应用启动和常规构建命令见根目录 README。这里的脚本用于开发或显式验收，不会随桌面启动自动运行。

## 构建和本地资料工具

| 脚本 | 用途与输入 |
| --- | --- |
| `fetch_managed_build_inputs.py`、`prepare_managed_runtime.py` | 下载校验构建输入，生成 `build/postgres/` |
| `prepare_knowledge_model.py` | 为指定资料目录准备本地检索模型 |
| `import_core_corpus.py` | 以 `--data-dir`、`--source` 指定独立目标和古籍源目录 |
| `check_core_candidate_data.py` | 核验指定目标目录的古籍、原件和索引，需要已导入的资料 |
| `prepare_assistant_candidate.py` | 显式指定源目录和目标目录，离线复制并核验资料；现有应用内迁移入口优先 |
| `build_english_journals.py` | 从 ShowJCR 的 `jcr.db` 生成 `backend/data/english_journals.json.gz`（英文期刊库数据，需先自行下载 `jcr.db`） |

## 回归与桌面验收

日常先运行 `npm test`、`npm run build:ui` 和 Python 单元测试。可使用以下工具进行专项验证：

- `check_assistant_system.py`：助手测试，支持 `--full`、`--native`、`--output`。
- `check_retrieval_quality.py`：检索测试，支持 `--full`、`--native`、`--rerank`。
- `check_project_shell_gui.cjs`：当前主界面的完整桌面流程，在独立工作区使用内置 PostgreSQL 和模拟模型；覆盖目录、PDF 连续阅读、项目助手、主题、面板、协同执行和取消。
- `check_client_server_gui.cjs`：在隐藏窗口验证普通浏览器的会话登录与项目保存；可通过 `--client-binary` 指定薄客户端程序，进一步验证远程连接及客户端退出后服务继续运行。使用独立临时服务和资料目录，截图与报告保存在 `.local/client-server-gui/`。
- `check_portal_gui.cjs`：使用 `portal_fixture.py` 建立临时多用户门户，检查管理员建号、模型配置、个人工作区和越权拒绝；需要内置 PostgreSQL。截图与报告位于 `.local/portal-gui/`，窗口默认隐藏。
- `check_*_gui.cjs` 的其他专项入口：分别检查古籍、投稿、研究助手、助手配置和检索；需要对应运行时与模型缓存。
- `managed_database_probe.py`、`check_packaged_*.py`：运行时生命周期、打包服务、导入检索及备份恢复的专项检查，需要构建好的后端；部分检查还需要 Docker 开发库。

运行当前源码桌面验收前，准备 Python 依赖、内置 PostgreSQL 和本地模型缓存，再构建界面：

```powershell
npm run build:ui
.\node_modules\electron\dist\electron.exe scripts\check_project_shell_gui.cjs
```

报告和截图写入 `.local/shell-rc12/`。源码验收成功报告为 `source-gui-report.json`，失败信息为 `gui-error.txt`。Windows 图形程序可能在命令行返回后继续运行，应以本次生成的报告与进程退出为准。`gui_service_process.cjs` 是共享的测试服务清理模块，不是独立命令。

## 当前候选包验证

`check_shell_bootstrap.cjs` 和 `check_shell_startup_close.cjs` 检查当前候选包的实际主进程、固定工作区启动和提前退出；依赖当前 GUI 测试生成的独立资料及发布目录。`check_startup_service.py` 接收显式后端路径和报告输出，测量启动耗时。

`check_shell_candidate_runtime.py` 与 `seal_shell_candidate.py` 是当前 rc.12 的固定资料/交付核验，依赖本机指定的工作区、报告和内部验收文档；不属于全新克隆的构建流程。切换版本时需要同步调整这些交付约束，不要套用到其他资料库。

旧 rc.2–rc.11 的重复脚本、旧导航 GUI 探针和已完成的一次性升级工具已删除。数据库正式迁移仍完整保留在 `backend/migrations/`，历史工具可从 Git 历史恢复。
