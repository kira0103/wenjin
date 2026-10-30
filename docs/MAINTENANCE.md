# 问津维护手册

日常启动和使用见 [README](../README.md)。本手册集中说明安装、部署、数据、开发和发布；最后核对：2026-10-30。

## 文档维护规则

- 现行文档固定为 `README.md` 和 `docs/MAINTENANCE.md`。使用说明写前者，部署和开发说明写本文件。
- 统一在 `main` 开发；功能、命令、数据结构或权限变化时，在同一提交更新对应文档，并修改核对日期。
- 命令以 [package.json](../package.json) 和各 CLI 的 `--help` 为准；版本、数据库迁移头和运行时版本分别查代码与依赖清单，避免重复抄写后失效。
- 设计讨论、阶段验收、一次性报告和截图保存在 Git 忽略的 `.local/`，使用 JSON 或日志记录结果，不新增现行说明文档。
- 提交前运行 `npm run docs:check`。它检查文档数量、相对链接和 npm 命令；行为说明由本次修改者对照代码核对。

2026-10-30 清理前的文档保存在本机 `.local/document-archive/20261030/`，仓库历史也保留旧版说明。归档仅供追溯。

## 首次安装

目标环境为 Windows x64、Node.js 22、Python 3.12、PowerShell 7。Linux 服务端使用 `.venv/bin/python` 替换下文的 Windows Python 路径；npm 的服务快捷命令目前使用 Windows 路径。

```powershell
git clone https://github.com/kira0103/wenjin.git
cd wenjin
npm ci
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
```

已克隆的工作区直接在项目根目录执行后续命令。Python 依赖以 [requirements.txt](../backend/requirements.txt) 为准，npm 依赖由 [package-lock.json](../package-lock.json) 固定。

## 数据库准备

文献库需要 PostgreSQL、`vector` 和 `pg_trgm` 扩展。桌面版可使用内置运行时或外部数据库；Web 门户默认使用内置运行时，也支持专用外部 PostgreSQL。

### 内置运行时

开发环境从 `build/postgres/` 加载运行时。首次准备：

```powershell
.venv\Scripts\python.exe scripts/fetch_managed_build_inputs.py
.venv\Scripts\python.exe scripts/prepare_managed_runtime.py
```

构建输入、固定下载地址与摘要见 [运行时清单](../deployment/knowledge/managed-runtime-inputs.json)。准备过程需要网络和 Windows 构建工具。资料目录使用本地固定磁盘；运行时目录与数据库资料目录分别保存。

### Docker 开发库

```powershell
.venv\Scripts\python.exe -m backend.knowledge.cli dev-prepare
docker compose -f compose.knowledge.yml up -d --wait
.venv\Scripts\python.exe -m backend.knowledge.cli dev-upgrade
.venv\Scripts\python.exe -m backend.knowledge.cli dev-configure --data-dir '.\工作台数据'
```

[Compose](../compose.knowledge.yml) 仅映射 `127.0.0.1:55432`。随机凭据保存在 `.local/knowledge/`；`shixue_admin` 管理数据库和扩展，`shixue_owner` 执行结构升级，`shixue_app` 运行日常业务。停止使用 `docker compose -f compose.knowledge.yml stop`，保留数据卷。

### 外部数据库与升级

角色、扩展及默认权限参考 [init.sh](../deployment/knowledge/init.sh)。应用角色需要表的读写及序列权限，不授予管理员或 schema CREATE 权限。设置迁移连接后显式升级：

```powershell
# 将占位值换成管理系统提供的连接；密码须做 URL 编码。
$env:SHIXUE_KB_MIGRATION_URL = 'postgresql+psycopg://shixue_owner:<密码>@127.0.0.1:5432/shixue_knowledge'
.venv\Scripts\python.exe -m backend.knowledge.cli upgrade
Remove-Item Env:SHIXUE_KB_MIGRATION_URL
$env:SHIXUE_KB_DATABASE_URL = 'postgresql+psycopg://shixue_app:<密码>@127.0.0.1:5432/shixue_knowledge'
```

外部库的健康检查不执行升级。先停止使用该库的服务、做配套备份并在隔离环境验证，再升级实际库。当前结构版本以 [database.py](../backend/knowledge/database.py) 为准；新结构增加迁移，保留已发布的 [迁移链](../backend/migrations/versions)，不能修改旧迁移来替代升级。

## 门户部署

本机初始化与启动按 [README 的 Web 门户步骤](../README.md#web-门户)。门户自己管理公共库及各账号的 API、Worker，无需另开个人服务的 Worker。

使用外部 PostgreSQL 时，在启动门户前设置 `SHIXUE_PORTAL_PG_ADMIN_URL`。管理账号需要创建数据库、角色和安装扩展的权限；门户建立一个公共数据库，为各账号创建独立研究 schema 与受限角色。

| 环境变量 | 用途 |
| --- | --- |
| `SHIXUE_PORTAL_ADMIN_PASSWORD` | 仅在 `--init-admin` 时提供首次管理员密码 |
| `SHIXUE_PORTAL_PG_ADMIN_URL` | 门户专用数据库管理连接；未设置时使用内置库 |
| `SHIXUE_MODEL_API_KEY` | 后台模型调用密钥；地址与模型名在管理员后台配置 |
| `SHIXUE_DEV_SERVER` | Vite `/api` 代理目标，默认 `http://127.0.0.1:8765` |

模型地址和名称会同步到运行中的工作区。密钥来自各宿主的启动环境；更换密钥需要正常停止门户与全部工作区后重启，仅重启门户主进程不会改变已有宿主的环境。

公共文献位于 PostgreSQL 的 `public` schema；个人研究位于 `w_<workspace_id>`。普通账号只有公共表 SELECT 权限及自己研究表的读写权限。schema 的结构版本不兼容时会拒绝启动，不会重建覆盖。

账号选择、内部连接令牌和数据库 search_path 由服务端决定。会话使用 HttpOnly Cookie；账号停用、角色修改或密码重置会撤销登录。管理员后台只汇总个人工作区状态及公共引用数量，不提供私人笔记正文入口。

Linux 使用 `python -m backend.portal` 启动，通过 HTTPS 同源反向代理提供网页和 `/api`。调整 [门户 systemd 示例](../deployment/server/wenjin-portal.service.example) 的账号、目录和受限环境文件。示例使用 `KillMode=process`，门户重启时保留工作区宿主和后台任务。

## 个人服务部署

这一模式服务于一个工作区所有者，可同时连接浏览器与 Electron 薄客户端。先准备外部数据库、升级结构，再让 API 与 Worker 使用同一个资料目录和同一套环境。

```powershell
$env:SHIXUE_SERVER_TOKEN = Read-Host '设置至少32字符的服务连接令牌' -MaskInput
$env:SHIXUE_MODEL_API_KEY = Read-Host '模型 API Key' -MaskInput
# SHIXUE_KB_DATABASE_URL 使用上一节的应用角色连接。
.venv\Scripts\python.exe -m backend.server --data-dir .local/server-workspace --prepare
npm run build:ui
```

在两个分别加载相同环境变量的终端启动：

```powershell
# 终端一：API 与网页。8766 与 README 的门户示例端口分开。
npm run server -- --data-dir .local/server-workspace --ui-dir dist --port 8766

# 终端二：后台任务
npm run worker -- --data-dir .local/server-workspace
```

浏览器访问 `http://127.0.0.1:8766` 并输入服务令牌。模型地址和名称在个人页面配置，密钥由服务端环境提供。`GET /health` 检查 API，认证后的 `GET /api/service/status` 检查 Worker。

薄客户端可在连接页填写根地址和令牌，也可在启动它的终端设置 `SHIXUE_SERVER_URL` 和 `SHIXUE_SERVER_TOKEN` 后运行 `npm run dev:client`。服务地址不附带 `/api`。当前客户端接个人服务，尚未接入多用户门户登录。

API 和 Worker 独立运行；API 重启保留队列及 Worker 的执行状态。完整维护前停止两者。个人服务示例见 [API](../deployment/server/wenjin-api.service.example) 与 [Worker](../deployment/server/wenjin-worker.service.example)。

远程导入上传文件内容与相对目录，不读取客户端绝对路径。单块上限 4 MB、单文件 250 MB、单批 20 GB；目录最多 50,000 个文件。上传中断后重新选择同一批未变化的文件可继续；确认导入前保留服务端上传暂存目录。

## 备份与迁移

| 运行方式 | 完整备份范围 |
| --- | --- |
| 本机桌面或个人服务 | SQLite、PostgreSQL、原件、工作稿、成果与工作区配置；优先使用桌面的“数据与备份” |
| 多账号门户 | 账号库、公共 PostgreSQL 库及所有个人 schema、`shared-library/`、`workspaces/` 与私有配置 |

门户停机顺序见 [README](../README.md#停止与备份)：先停止对外门户，再用同一资料目录执行 `--stop-workspaces`，等待个人 Worker 和共享数据库退出。使用外部 PostgreSQL 时做逻辑备份，并配套保存文件目录。

门户页面禁用旧单工作区的全库备份入口，避免生成缺少其他账号文件的备份。恢复写入新的目标目录，核对数据库和原件后再切换；SQLite 审计快照不是完整备份。

`workspace-identity.json` 保存稳定工作区身份，完整移动目录时一起携带。固定资料目录由 `%APPDATA%/shixue-workbench/bootstrap.json` 记录，应用以实际配置为准。迁移现有资料不会自动把私人文献公开。

旧版“每账号一个数据库”首次建立公共库时，可在门户停机、任务结束并备份后，明确发布一个旧账号的全部文献：

```powershell
.venv\Scripts\python.exe -m backend.portal_migrate --data-dir .local/portal-preview --username alice --publish-library
```

目标公共库必须为空，目标个人分区不能已有数据。迁移校验原件、保留文献和引用 ID，把研究记录放回个人分区，原数据库继续保留。已有公共库时，通过管理员界面选择原件导入，不使用这个全库入口覆盖数据。

## 可选资料

| 文件 | 处理方式 |
| --- | --- |
| [journals.example.csv](../backend/data/journals.example.csv) | 虚构期刊示例，说明中文目录的 CSV 格式 |
| `backend/data/CSSCI_2025-2026.csv` | 本机可选中文期刊目录，启动时导入；缺失时可从页面导入自己的 CSV |
| `backend/data/english_journals.json.gz` | 英文期刊只读快照；后端读取，不写工作区数据库，缺失时页面提示 |
| 核心古籍来源目录 | 用户自行准备；导入时保留目录、原件和来源信息 |

英文目录可用 [build_english_journals.py](../scripts/build_english_journals.py) 从自行准备的 ShowJCR `jcr.db` 生成，参数为 `--db` 与可选 `--out`。页面并列显示 JCR、中科院和新锐字段，学科按数据中的中科院大类筛选；分区口径与版本以快照标记为准，替换数据文件后重启后端。

真实目录、语料和模型权重不提交 Git。桌面后端打包会带入当时 `backend/data/` 中存在的数据；分发前核对数据来源和使用许可。

## 代码位置

| 位置 | 职责 |
| --- | --- |
| [electron/main.cjs](../electron/main.cjs)、[preload.cjs](../electron/preload.cjs) | 窗口、平台能力、本机服务生命周期与远程连接 |
| [transport.ts](../src/workbench/transport.ts)、[platform.ts](../src/workbench/platform.ts) | HTTP/IPC 适配，文件选择、下载和外链 |
| [App.tsx](../src/workbench/App.tsx)、[src/portal](../src/portal) | 研究界面、Web 登录与管理后台 |
| [app.py](../backend/app.py)、[server.py](../backend/server.py)、[worker.py](../backend/worker.py) | 本机服务装配、个人 API 与持久化任务执行 |
| [portal.py](../backend/portal.py)、[portal_fleet.py](../backend/portal_fleet.py) | 会话校验、路由及工作区宿主管理 |
| [portal_shared_database.py](../backend/portal_shared_database.py)、[shared.py](../backend/knowledge/shared.py) | 共享文献与个人分区的表和权限边界 |
| [backend/knowledge](../backend/knowledge) | 文献、录文、索引、检索、研究助手、历史兼容与备份 |
| [backend/api](../backend/api)、[backend/services](../backend/services) | 新增业务的 HTTP 适配和服务用例 |
| [submissions.py](../backend/submissions.py)、[english_journals.py](../backend/english_journals.py) | 私人投稿记录与共享英文期刊查询 |

SQLite 保存项目、工作稿、点校、投稿和检查点；PostgreSQL 保存文献、版本、原文块、索引及助手研究数据。录文修订、历史数据激活和兼容读取代码由现有资料继续使用，清理代码前需检查静态引用、动态加载、打包入口与迁移链。

## 验证与发布

```powershell
npm run docs:check
npm test
npm run build:ui
.venv\Scripts\python.exe -m unittest discover -s tests -v
```

测试使用独立工作区与模拟模型。默认跳过需要额外数据库环境的用例；提交说明写明跳过范围。准备 `build/postgres/` 后启用真实门户集成验证：

```powershell
$env:SHIXUE_MANAGED_TESTS = '1'
.venv\Scripts\python.exe -m unittest tests.test_portal_native -v
Remove-Item Env:SHIXUE_MANAGED_TESTS
```

Docker 文献集成测试用 `SHIXUE_KB_INTEGRATION=1`，入口为 `tests.test_knowledge_postgres`。对真实收费模型的调用不作为默认回归。

界面验收先构建页面，再运行 `.\node_modules\.bin\electron.cmd scripts/check_portal_gui.cjs`。桌面和薄客户端入口分别为 `scripts/check_project_shell_gui.cjs`、`scripts/check_client_server_gui.cjs`；测试报告、截图写入 `.local/`。真实研究资料不作测试夹具。

| 构建命令 | 产物 |
| --- | --- |
| `npm run build:ui` | `dist/` 前端资源 |
| `npm run build:backend` | `build/backend/` 中的 PyInstaller 后端 |
| `npm run dist:win`、`npm run dist:portable` | 包含 Python 和数据库运行时的 Windows 安装版、便携版 |
| `npm run dist:client` | `release/client/` 薄客户端，运行时依赖独立服务 |
| `npm run dist:candidate` | 当前候选版安装包和便携包，版本与目录由 npm 脚本定义 |

完整桌面包需要 `build/postgres/`；发布时保留第三方许可与运行时清单。代码推送不会自动发布安装包。固定候选包检查工具使用预定工作区和验收报告，不属于新克隆的常规构建流程。

## 数据与提交边界

模型密钥、数据库密码、内部令牌、环境文件、真实研究资料、完整备份、模型响应和安装包不提交 Git。桌面凭据使用系统凭据库；服务端凭据与账号库保存在受保护的私有目录。内置数据库和业务 Worker 使用的权限由服务端管理。

提交前检查 `git status --short`、`git diff --cached --stat` 与暂存内容；`.gitignore` 不影响已经跟踪的文件。若有效密钥被提交，先撤销和轮换，再处理历史副本；问题报告只保留脱敏的复现信息。
