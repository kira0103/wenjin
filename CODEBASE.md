# 代码导航

## 桌面与界面

`electron/main.cjs` 负责窗口、工作区选择、Python 服务生命周期和 IPC；`preload.cjs` 只暴露受控桌面能力。后端绑定本机地址，桌面生成每次启动的请求令牌。

`src/workbench/App.tsx` 组织导航、项目工作区和助手面板。`LibraryViews.tsx`、`ResearchViews.tsx`、`BatchViews.tsx`、`SubmissionWorkbench.tsx` 分别承载资料、研究、点校批次和投稿工作。`src/workbench/knowledge/` 包含统一文献库、核心古籍、检索与助手；当前目录入口由 `DocumentBrowser.tsx` 承载。

## 后端与持久化

`backend/app.py` 装配 FastAPI 和桌面服务。`research.py`、`collation.py`、`collation_debug.py`、`preprocessing.py`、`submissions.py` 管理研究工作、点校、文本准备和投稿。`model_transport.py` 负责模型网络调用，`text_diff.py` 计算正文差异。

工作区 SQLite 保存项目、工作稿、点校和投稿等数据；PostgreSQL + pgvector 保存统一文献、版本、原文块、锚点、检索索引及研究助手数据。原件和派生成果位于资料目录，不属于代码仓库。完整备份需要同时考虑数据库与文件，不能只复制其中一个数据库。

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

`scripts/` 中 `fetch_managed_build_inputs.py` 和 `prepare_managed_runtime.py` 用于构建数据库运行时。`prepare_*candidate.py`、`seal_*candidate.py` 和部分 `check_*` 是历史候选包的迁移或验收工具，依赖指定版本发布目录、本地报告或内部文档，不属于全新克隆的启动流程；不能当作通用升级命令批量运行。
