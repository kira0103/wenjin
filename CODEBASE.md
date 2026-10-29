# 代码导航

## 桌面与界面

`electron/main.cjs` 负责窗口、工作区选择、Python 服务生命周期和 IPC；`preload.cjs` 只暴露受控桌面能力。后端绑定本机地址，桌面生成每次启动的请求令牌。

以上为原有桌面模式。独立服务模式使用 `backend/server.py` 启动 API，`backend/worker.py` 执行持久化任务；浏览器使用会话 Cookie，Electron 薄客户端通过主进程连接远程服务。`transport.ts` 统一通信，`platform.ts` 隔离文件选择等平台能力，业务组件不直接依赖桌面桥接。薄客户端不管理服务或数据库生命周期。详见 [docs/client-server.md](docs/client-server.md)。

多用户 Web 入口位于 `src/portal/`：`WebEntry.tsx` 负责账号登录与用户入口，`AdminConsole.tsx` 管理账号、模型和工作区状态。`backend/portal.py` 验证角色并代理用户业务 API，`portal_accounts.py` 与 `portal_migrations.py` 管理账号、会话和版本化账号库；`portal_fleet.py` 只从当前会话选择目标工作区，`portal_workspace_host.py` 维护一个公共资料库宿主和各账号的独立执行进程。`portal_database.py` 初始化公共数据库与管理角色，`portal_shared_database.py` 按版本建立个人研究 schema，`knowledge/shared.py` 定义公共表清单。公共表只授予用户 SELECT，个人 schema 的表、外键、索引和不可变版本触发器独立建立；`portal_migrate.py` 提供旧账号的显式离线发布。详见 [docs/web-portal.md](docs/web-portal.md)。

`src/workbench/App.tsx` 组织导航、项目工作区和助手面板。`LibraryViews.tsx` 只保留历史来源阅读器和共享显示名称；当前项目材料页为 `knowledge/ProjectLibrary.tsx`，全局目录入口为 `knowledge/DocumentBrowser.tsx`。`ResearchViews.tsx`、`BatchViews.tsx`、`SubmissionWorkbench.tsx` 分别承载研究、点校批次和投稿。`knowledge/UnclassifiedCatalog.tsx` 供设置页核对历史待归类书目。

## 后端与持久化

`backend/app.py` 装配 FastAPI 和桌面服务。`research.py`、`collation.py`、`collation_debug.py`、`preprocessing.py`、`submissions.py` 管理研究工作、点校、文本准备和投稿。`model_transport.py` 负责模型网络调用，`text_diff.py` 计算正文差异。

工作区 SQLite 保存项目、工作稿、点校和投稿等数据；PostgreSQL + pgvector 保存统一文献、版本、原文块、锚点、检索索引及研究助手数据。原件和派生成果位于资料目录，不属于代码仓库。完整备份需要同时考虑数据库与文件，不能只复制其中一个数据库。

`auth.py` 管理服务认证和管理操作边界；`uploads.py` 管理可续传的文件上传，将服务端暂存文件交给现有导入用例。`identity.py` 将既有工作区 key 固定在 `workspace-identity.json`，完整移动目录后继续可读。新增用例按 `api/` → `services/` 分层；稿件和引文导出已使用该结构，稿件导出校验版本且允许显式提交未保存草稿。

`backend/knowledge/` 的主要职责：

| 模块 | 职责 |
| --- | --- |
| `product_runtime.py`、`managed.py` | 内置 PostgreSQL 启停和结构兼容检查 |
| `config.py`、`database.py`、`migration.py` | 连接设置、凭据、事务与迁移 |
| `catalog.py`、`document_library.py`、`document_imports.py` | 版本、文档目录和导入 |
| `ingestion.py`、`chunking.py`、`core_corpus.py` | 录文、分块、核心古籍与文字索引 |
| `retrieval.py`、`reranking.py`、`parent_context.py` | 混合检索、重排与上下文 |
| `research_assistant.py`、`collaboration.py`、`assistant_registry.py` | 研究助手、协作执行和配置 |
| `activation.py`、`compat.py`、`legacy.py` | 历史数据激活与兼容读取 |
| `backup.py`、`audit.py` | 备份恢复与只读审计 |

当前 PostgreSQL 迁移头为 `kb0012`。所有 `backend/migrations/versions/` 迁移按链升级；即使旧功能界面已调整，迁移和历史兼容代码仍可能被已有资料使用，不能按文件年龄删除。

## 测试与工具

`npm test` 执行 `src/` 内 Vitest 测试和 `tests/workspace_path.test.cjs`。Python 单元测试用 `unittest discover`，有环境依赖的数据库测试默认跳过。集成验证使用独立测试库，不应指向真实工作区。

`scripts/` 中 `fetch_managed_build_inputs.py` 和 `prepare_managed_runtime.py` 用于构建数据库运行时。当前桌面验收以 `check_project_shell_gui.cjs` 为入口，使用独立工作区和模拟模型。旧候选版的重复检查、一次性升级和封包脚本已移除，需要追溯时查看 Git 历史；保留工具的用途和前置条件见 [scripts/README.md](scripts/README.md)。
