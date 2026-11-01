# 问津维护手册

日常启动和使用见 [README](../README.md)。本手册集中说明安装、部署、数据、开发和发布；最后核对：2026-10-31。

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

本机直接 `npm start`，首次自动建立 `admin / admin123`、构建页面并准备数据库；`npm stop` 正常停止全部服务。入口为 [launcher.py](../backend/launcher.py)，只在源码或前端构建变化时重建页面，不修改已有管理员密码。登录页先开放，资料服务异步准备，并预热最近登录的一个启用研究账号；首次建个人工作区仍需等待初始化。网关复用 HTTP 连接，按数据库身份缓存分区配置，并短暂缓存健康检查；每个业务请求仍重新校验账号会话。默认账号可以直接进入管理后台，在“账号与安全”修改密码；修改后原会话失效，重启继续使用新密码。

启动选择保存在 `.local/backend-launcher.json`。需要指定原门户或端口时，首次运行 `npm start -- --data-dir <门户目录> --port 8765`，之后继续使用 `npm start`；`--no-open` 关闭自动打开浏览器。记住的账号库缺失时会拒绝创建空库，桌面资料目录也不能作为门户目录使用。`SHIXUE_PORTAL_ADMIN_PASSWORD` 可覆盖首次默认密码，`SHIXUE_LAUNCHER_CONFIG` 指定独立启动配置文件。普通创建账号和修改密码仍要求至少12位，`admin123` 仅作为首次管理员初始化的例外，账号库只保存加盐哈希。

服务器部署继续使用原 CLI，先执行 `npm run build:ui`，再用 `SHIXUE_PORTAL_ADMIN_PASSWORD` 配合 `npm run portal -- --data-dir <门户目录> --init-admin admin` 初始化一次；日常启动使用 `npm run portal -- --data-dir <门户目录> --ui-dir dist --port 8765`。手动入口保持宿主独立运行，适合 systemd 管理；一键本机入口在退出时一并停止宿主。

使用外部 PostgreSQL 时，在启动门户前设置 `SHIXUE_PORTAL_PG_ADMIN_URL`。管理账号需要创建数据库、角色和安装扩展的权限；门户建立一个公共数据库，为各账号创建独立研究 schema 与受限角色。

| 环境变量 | 用途 |
| --- | --- |
| `SHIXUE_PORTAL_ADMIN_PASSWORD` | 仅在 `--init-admin` 时提供首次管理员密码 |
| `SHIXUE_PORTAL_PG_ADMIN_URL` | 门户专用数据库管理连接；未设置时使用内置库 |
| `SHIXUE_MODEL_API_KEY` | 后台模型调用密钥；地址与模型名在管理员后台配置 |
| `SHIXUE_PORTAL_URL` | 桌面登录客户端连接的后台根地址，默认 `http://127.0.0.1:8765` |
| `SHIXUE_DEV_SERVER` | Vite `/api` 代理目标，默认 `http://127.0.0.1:8765` |

模型地址和名称会同步到运行中的工作区。密钥来自各宿主的启动环境；更换密钥需要正常停止门户与全部工作区后重启，仅重启门户主进程不会改变已有宿主的环境。

公共文献位于 PostgreSQL 的 `public` schema；个人研究位于 `w_<workspace_id>`。文献表以数据库角色和行级权限区分系统与个人：系统行所有账号可读，个人行仅所属角色可读写，跨归属的原件/版本关联由触发器拒绝。研究表仍按 schema 隔离。新增迁移 kb0013 保留旧文献为系统行，原 ID 和引用不变；兼容的 kb0012 个人研究分区只更新版本标记和授权，不搬动研究记录。其他不兼容结构拒绝启动。

个人原件保存在所属 `workspaces/<id>/assets/personal/`，系统原件位于 `shared-library/assets/`。公共中文期刊元数据由共享宿主维护，个人收藏与备注通过映射保留原投稿期刊 ID；英文目录读取同一服务端快照。账号选择、内部连接令牌和数据库 search_path 由服务端决定。会话使用 HttpOnly Cookie；账号停用、角色修改或密码重置会撤销登录。管理员后台只汇总个人工作区状态及公共引用数量，不提供私人笔记正文入口。

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

旧个人服务客户端用 `npm run dev:personal-server`，连接页填写根地址和令牌，也可设置 `SHIXUE_SERVER_URL` 和 `SHIXUE_SERVER_TOKEN`。服务地址不附带 `/api`。日常 `npm run dev` / `npm run dev:client` 则连接多账号门户，使用用户名和密码。

API 和 Worker 独立运行；API 重启保留队列及 Worker 的执行状态。完整维护前停止两者。个人服务示例见 [API](../deployment/server/wenjin-api.service.example) 与 [Worker](../deployment/server/wenjin-worker.service.example)。

远程导入上传文件内容与相对目录，不读取客户端绝对路径。单块上限 4 MB、单文件 250 MB、单批 20 GB；目录最多 50,000 个文件。上传中断后重新选择同一批未变化的文件可继续；确认导入前保留服务端上传暂存目录。

## 备份与迁移

| 运行方式 | 完整备份范围 |
| --- | --- |
| 本机桌面或个人服务 | SQLite、PostgreSQL、原件、工作稿、成果与工作区配置；优先使用桌面的“数据与备份” |
| 多账号门户 | 账号库、公共 PostgreSQL 库及所有个人 schema、`shared-library/`、`workspaces/` 与私有配置 |

一键启动的后台用 `npm stop` 或 `Ctrl+C` 停止。手动部署先停止对外门户，再执行 `npm run portal -- --data-dir <门户目录> --stop-workspaces`，等待个人 Worker 和共享数据库退出。使用外部 PostgreSQL 时做逻辑备份，并配套保存文件目录。

门户页面禁用旧单工作区的全库备份入口，避免生成缺少其他账号文件的备份。恢复写入新的目标目录，核对数据库和原件后再切换；SQLite 审计快照不是完整备份。

`workspace-identity.json` 保存稳定工作区身份，完整移动目录时一起携带。固定资料目录由 `%APPDATA%/shixue-workbench/bootstrap.json` 记录，应用以实际配置为准。迁移现有资料不会自动把私人文献公开。

旧版“每账号一个数据库”首次建立公共库时，可在门户停机、任务结束并备份后，明确发布一个旧账号的全部文献：

```powershell
.venv\Scripts\python.exe -m backend.portal_migrate --data-dir .local/portal-preview --username alice --publish-library
```

目标公共库必须为空，目标个人分区不能已有数据。迁移校验原件、保留文献和引用 ID，把研究记录放回个人分区，原数据库继续保留。已有公共库时，通过管理员界面选择原件导入，不使用这个全库入口覆盖数据。

只发布已加工的殆知阁语料时，使用 [portal_corpus_migrate.py](../backend/portal_corpus_migrate.py)。此入口仅接受空的系统文献库，目标后台须已初始化；源工作区须停止使用。它只复制指定语料的当前版本、原件、录文和有效索引，不发布其他文献或个人研究记录：

```powershell
.venv\Scripts\python.exe -m backend.portal_corpus_migrate --data-dir .local/portal-preview --source-data-dir 工作台数据 --source-path E:\daizhigev20 --slug daizhige --report .local/corpus-migration/report.json
```

原目录和旧数据库保留。后台原始目录位于 `shared-library/corpora/daizhige/`，内容寻址原件位于 `shared-library/assets/`；逐文件校验 SHA256，保留源修订号清单。数据库发布为单一事务，重建检索索引并刷新表统计。2026-10-31 本机迁入 15,295 部、28,950 个文件、309,754 段录文，核验报告位于 `.local/corpus-migration-20261031/`。这是专用原文／归一检索索引，向量加工单独执行。

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
| [electron/portal-main.cjs](../electron/portal-main.cjs)、[portal.cjs](../electron/portal.cjs) | 默认桌面启动入口、登录窗口、后台连接与独立浏览器会话 |
| [electron/main.cjs](../electron/main.cjs)、[preload.cjs](../electron/preload.cjs) | 兼容旧本机模式与个人服务令牌客户端 |
| [transport.ts](../src/workbench/transport.ts)、[platform.ts](../src/workbench/platform.ts) | HTTP/IPC 适配，文件选择、下载和外链 |
| [App.tsx](../src/workbench/App.tsx)、[src/portal](../src/portal) | 研究界面、Web 登录与管理后台 |
| [app.py](../backend/app.py)、[server.py](../backend/server.py)、[worker.py](../backend/worker.py) | 本机服务装配、个人 API 与持久化任务执行 |
| [portal.py](../backend/portal.py)、[portal_fleet.py](../backend/portal_fleet.py) | 会话校验、路由及工作区宿主管理 |
| [portal_shared_database.py](../backend/portal_shared_database.py)、[shared.py](../backend/knowledge/shared.py) | 共享文献与个人分区的表和权限边界 |
| [backend/knowledge](../backend/knowledge) | 文献、录文、索引、检索、研究助手、历史兼容与备份 |
| [backend/api](../backend/api)、[backend/services](../backend/services) | 新增业务的 HTTP 适配和服务用例 |
| [submissions.py](../backend/submissions.py)、[english_journals.py](../backend/english_journals.py) | 私人投稿记录与统一公共中英文期刊目录 |

SQLite 保存项目、工作稿、点校、投稿和检查点；PostgreSQL 保存文献、版本、原文块、索引及助手研究数据。录文修订、历史数据激活和兼容读取代码由现有资料继续使用，清理代码前需检查静态引用、动态加载、打包入口与迁移链。

管理后台的“检索配置”“助手与 Skill”经 `/api/admin/library/knowledge/…` 维护公共配置。检索设置、助手定义及版本显式读取 `public` 表，个人分区旧配置不自动发布；历史任务仍可核验原有私有技能快照。个人角色只读公共配置；版本表仅额外授予不可变 `id` 列的 UPDATE 权限以支持发布结果时的 `FOR SHARE` 锁，触发器拒绝 ID 变化。分区配置 `config_policy` 版本确保旧账号补齐权限，不改变研究记录及其外键。用户 API 禁止修改、导入、发布、撤销及测试技能，也禁止配置检索与下载重排模型；用户列表只提供启用且未撤销的已发布版本。重排权重统一保存在后台缓存。修改服务端代码后先 `npm stop`，再 `npm start`，让已有宿主和 Worker 加载新代码。

## 验证与发布

账号门户的视觉样式集中在 [modern.css](../src/portal/modern.css)，作用域为 `.portal-root` 与 `.portal-workspace`，避免覆盖旧本机模式。页面容器只保留一个主标题；研究稿件、批次历史和嵌入组件放在对应页面的内容区域。助手表单用独立的字段网格和操作行；弹窗挂载到视口，并随登录状态一起遮蔽。布局验收覆盖常规宽屏、3299 像素宽屏、手机导航和深色外观。

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

桌面入口使用 [portal-main.cjs](../electron/portal-main.cjs) 明确调用窗口启动，不用 `require.main` 判断 Electron 是否为主模块。启动阶段保存在客户端 profile 的 `startup.json`；`SHIXUE_CLIENT_PROFILE` 可显式指定独立 profile。`node scripts/check_desktop_command.cjs` 使用真实 `npm run dev` 检查后台在线与离线的启动路径；`--smoke-test` 仅用于隐藏窗口验收并自动退出。

界面验收先构建页面，再运行 `.\node_modules\.bin\electron.cmd scripts/check_portal_gui.cjs`。实际桌面登录入口用 `.\node_modules\.bin\electron.cmd scripts/check_desktop_portal_gui.cjs` 验收。旧本机与个人令牌客户端的验收入口分别为 `scripts/check_project_shell_gui.cjs`、`scripts/check_client_server_gui.cjs`；测试报告、截图写入 `.local/`。完整逐页检查由 [portal_page_audit.cjs](../scripts/portal_page_audit.cjs) 驱动，覆盖全部主导航、点校子流程、空状态、列表、详情、编辑弹窗和两种研究助手；测试夹具只写隔离数据库，不调用付费模型。真实研究资料不作测试夹具。

| 构建命令 | 产物 |
| --- | --- |
| `npm run build:ui` | `dist/` 前端资源 |
| `npm run build:backend` | `build/backend/` 中的 PyInstaller 后端 |
| `npm run dist:win`、`npm run dist:portable` | 包含 Python 和数据库运行时的 Windows 安装版、便携版 |
| `npm run dist:client` | `release/client/` 薄客户端，运行时依赖独立服务 |
| `npm run dist:candidate` | 当前候选版安装包和便携包，版本与目录由 npm 脚本定义 |

默认桌面包也使用账号门户入口；`dist:client` 省略本机后端和数据库，适合当前部署。兼容完整包的构建需要 `build/postgres/`；发布时保留第三方许可与运行时清单。代码推送不会自动发布安装包。固定候选包检查工具使用预定工作区和验收报告，不属于新克隆的常规构建流程。

### RAG 配置与后台执行

源码对照：[Dify RAG](https://github.com/langgenius/dify/tree/main/api/core/rag)、[Dify 清洗器](https://github.com/langgenius/dify/blob/main/api/core/rag/cleaner/clean_processor.py)、[父子索引处理](https://github.com/langgenius/dify/blob/main/api/core/rag/index_processor/processor/parent_child_index_processor.py)，以及 [FastGPT](https://github.com/labring/FastGPT/blob/main/README.md) 的入库、多库复用、分块管理和搜索测试。此前检索参数设计参考 [Dify 配置](https://docs.dify.ai/en/use-dify/knowledge/create-knowledge/setting-indexing-methods) 与 [临时测试](https://docs.dify.ai/en/use-dify/knowledge/test-retrieval)。当前实现使用本项目后台与 PostgreSQL，不依赖另行部署这些平台。

| 环节 | 当前实际实现 |
| --- | --- |
| 原件入库 | 批量文件／目录上传、直接录入文本、重复原件复用；新增 HTML、CSV、PPTX、XLSX 文本读取；保留原件与来源 |
| 解析与 OCR | PDF 自动路由／始终尝试 OCR／禁用 OCR；扫描图片需要 OCR。重新解析生成新修订，旧引用保留 |
| 清洗 | 控制字符、空白、网址、邮箱、段内重复行；清洗发生在切分之前，只改变派生索引文本，并维护原文字符映射 |
| 切分 | 自动路由、文档结构、语义、句段递归、固定字符、指定分隔符及父子；可组合语义父块与递归／语义子块，预览与入库共用原文映射实现 |
| 存量加工 | 按版本提交最多 20 份文献重建索引／重新解析，防止并发覆盖；任务保存加工配置快照，支持取消与按原配置重试 |
| Query 处理 | 问题改写、多 Query 扩展、HyDE；至多一次模型调用、限制查询数量与输出预算、保留原问题及失败回退。精确原句跳过处理；全文模式跳过 HyDE |
| 召回与重排 | 原句、全文、向量、混合检索；Top K、每路候选、加权 RRF、向量余弦阈值、BGE 重排原始分阈值与故障回退分别生效 |
| 原文与引用 | 父块及上下文受章节、文件、版本、页段和权限约束；生成的改写／HyDE 文本不会作为文献或引用 |
| 调试与评测 | 后台清洗切分预览、真实检索临时测试、Query 轨迹、阶段耗时、模型准备与索引状态、已有金标准评测记录 |
| 尚未覆盖的平台能力 | URL／API 数据源持续同步、自动生成 QA 知识库、图谱知识编译、多模态图文检索、任意嵌入模型热切换 |

策略保存在 `public.kb_retrieval_settings.options` 的版本化 JSON 中，新增 `ingestion` 与 `query` 配置；读取旧配置时补齐默认值。默认不清洗、不改写，兼容已有资料。文档解析任务、目录批次和研究任务均固定策略快照。修改默认规则不会自动改动已入库文献；管理员可在加工列表显式应用当前表单。

后台实现分别为 [cleaning.py](../backend/knowledge/cleaning.py)、[chunking.py](../backend/knowledge/chunking.py)、[file_loaders.py](../backend/knowledge/file_loaders.py)、[pipeline_api.py](../backend/knowledge/pipeline_api.py)、[query_processing.py](../backend/knowledge/query_processing.py) 和 [retrieval.py](../backend/knowledge/retrieval.py)。清洗后的分块记录仍以原文 Unicode 字符坐标保存，检索规范化文本与向量输入使用清洗结果。原句精确搜索直接查原文，保持逐字核验。PPTX／XLSX 不执行宏、公式或外部引用；XLSX 仅使用已有缓存值，HTML 脚本与样式不进入录文。

自动路由与跨段组织位于 [adaptive_chunking.py](../backend/knowledge/adaptive_chunking.py)。它对每份文献分散抽样，结合标题、句段、表格行和 OCR／多栏提示选择方案；结构不明确的长文可选语义，短文或布局质量存疑时采用递归。语义比较使用后台嵌入模型，限制窗口、输入数量和块字符上限；模型异常或预算不足时记录回退，不保存异常详情。自动路由是可解释的启发式选择，实际检索效果仍由文献样本及金标准评测验证；不自动修复竖排、双栏阅读顺序或复杂表格。

迁移 kb0014 为 `kb_editions` 增加版本化的 `processing_config`／`processing_version`，为 `kb_chunks` 增加 `parent_spans`，保留现有文献归属、录文、索引与引用。兼容的 kb0012／kb0013 私有分区只更新版本标记。文档策略由 [processing_policy.py](../backend/knowledge/processing_policy.py) 读取；优先使用文档覆盖，其次是批次／表单，最后是全局默认，并在任务入队时固定。修改策略不回写已排队任务，也不自动重建旧索引。管理员列表可逐文档配置；上传对话框可逐文件选择，超过展示数量的文件采用批次默认。核心古籍仍使用专用源导入，导入后可设置独立索引策略。

父块以原文范围列表关联，同一附件、同一页段及同类录文中可跨自然段组织。表格行组可额外关联原表头，不复制成新的权威录文。读取父块时逐段复核版本、附件、查询范围及 RLS 权限，并受回答字符预算限制。预览中的跨段逻辑块对应多个可定位原文片段；引用始终锚定实际原文块。语义预览禁止下载模型，最终向量加工沿用正常后台模型准备流程。

多 Query 在同一数据库版本快照和相同权限范围内召回，按原文块合并后统一重排；HyDE 只改变向量检索输入。Query 模型输出有结构、长度、数量约束，诊断不保存异常堆栈或模型密钥。研究流程中的 Query 模型调用与原有调用共用轮次、Token 和取消预算，旧任务继续使用固定策略。

系统与个人向量同样保存在 PostgreSQL + pgvector，源文件、录文块、分块和索引均由 `owner_role` 的 RLS 约束；普通客户端不能提供数据库角色或切换为维护角色。运行角色须无超级用户、表所有权与 `BYPASSRLS` 权限，健康检查包含这些条件。`tests/test_rag_pipeline.py` 使用真实受限角色、相同关键词和相同向量验证两用户的关键词／向量／混合召回、默认范围、伪造文献 ID、改写、多 Query、HyDE 和持久化证据快照隔离。大量古籍目录同步使用集合差，避免 RLS 下关联反连接在完整目录上退化为平方扫描。

管理员的 `rag-preview`、`rag-documents`、`rag-process`、`rag-text` 经 `/api/admin/library/knowledge/…` 访问；普通工作区入口和私有宿主均拒绝调用。个人导入仍按账号隔离，使用统一默认策略；管理员加工列表只展示系统文献。核心古籍保留专用导入更新流程，可在此重建其索引。检索配置默认进入“文献加工”，系统文献库提供直达入口；不再将已有文献的任务操作放在配置表单底部。`rag-documents` 支持 `state=all/pending/ready/mismatch/processing/failed`，返回筛选统计及已保存的模型名和维度；文本索引就绪不等同于向量就绪，向量状态核对活动版本、模型空间及最近任务。`rag-process` 的 `operation=process` 自动为已有录文排 `index`，无录文但有原件排 `import` 并继续排向量索引；仍按版本加锁、手选每次最多 100 份、同参数去重，保留旧录文与索引。全量提交使用独立的 `rag-process-all` 入口，后端固定匹配文献、录文版本、文献独立策略、全局策略及向量模型，不向浏览器传输完整清单。`bulk_processing.py` 在 API 进程运行可恢复的分发器，每次锁定并检查 50 份文献，将子任务和游标在同一事务提交；自动跳过已有任务、已就绪索引、原文变更及缺少原件的文献。控制记录使用 kind=rag_batch、state=dispatching，向量 Worker 不会领取；API 启动恢复租约过期的分发器，不中断正在向量化的工作进程。批次取消仅影响自身尚未完成的子任务，保留成功索引；重试从冻结清单重新检查，避免重复创建活动任务。进度汇总将未分发清单也计入分母，与实际子任务按文献去重，明确区分“入队完成”和“向量化完成”。分页 limit 支持 20／50／100，不影响全量范围。进度查询先物化全量批次的模型及目标数组，仅保留统计所需列，用 UUID 与文献主键关联，避免对每个目标反复读取大型 JSON 清单。列表先物化当前页，再计算原件数、单篇任务和队列位置，深分页不会计算前面数千条文献的详情。前端加载请求设 20 秒等待上限，失败结束加载并提供重试；刷新失败保留已有列表，轮询在前一请求完成后再等待 2.5 秒，避免请求堆积。Windows 私有状态文件保持原子替换，对短暂的文件共享冲突作有界重试，避免宿主因短暂读锁退出。队列位置通过窗口排序一次生成，最新任务按 UUID 关联，避免逐篇扫描完整队列。`errors.py` 对 SQL 超时、连接异常、数据冲突及未知异常返回 JSON、稳定错误类型和排查编号；日志记录同一编号、异常类型及应用栈位置，不记录异常原文、密码或请求正文。`transport.ts` 将断连、非 JSON 错误与超时统一为 `ApiError`，读取默认 30 秒、写入默认 240 秒，不自动重复写操作。`SystemFeedback.tsx` 在 Web 与桌面公共入口显示持续错误提示及重复次数，401 沿用重新登录流程；React 渲染错误显示刷新入口，未捕获的页面操作异常也会显示提示。后台任务返回 200 但状态为失败时，整体进度单独显示失败提醒，可一键筛选失败文献查看原因及重试。前端提供“全选所有匹配文献”状态，跨页保留并允许逐篇排除；修改题名／状态筛选会清空选择，翻页与进度刷新不会清空跨页选择。`rag-process-all` 的 `scope=filtered` 按 query/state 匹配，`scope=library` 忽略这两个筛选；`exclude_ids` 在后台匹配阶段排除，浏览器不提交全部文献 ID。普通手选入口未选文献不会提交全库任务。系统文献库的“编辑文献书目”使用全类型、含核心古籍的分页书目检索，复用带版本检查的书目更新接口；分类名称提供直接编辑按钮，核心古籍原始录文仍通过源更新维护。`rag-documents` 同时返回 `processing_progress`，由 `processing_progress.py` 汇总独立于列表筛选／分页的批次、执行队列和当前模型全库就绪数。新提交在任务 payload 固定 processing_batch，解析派生索引沿用批次并记录父任务；旧队列按同一事务的 created_at 分组。每个批次取每份文献最新的解析／索引任务，避免父子任务重复计数；解析占文献进度前 20%，其后的索引衔接剩余部分。整体百分比结合阶段进度，终止的失败／取消也计为执行结束，成功数单列。Worker 将阶段计数写入 result.progress，记录块数、语义样本数、分块向量数及各原件完成数，成功结果保留最终计数；同阶段同百分比的高频回报限制为最多每半秒一次。原文读取、清洗切分、语义边界、向量推理和写入索引分别上报；排队位置、更新时间和执行器租约状态同时展示，不虚构剩余时间。

向量配置由 [embedding_config.py](../backend/knowledge/embedding_config.py) 定义，保存在全局检索设置的 `embedding` 字段；默认保留 MiniLM 384 维。提供本地 FastEmbed 模型及兼容接口预设，固定维度模型拒绝任意维度，支持降维的接口按能力校验并显式控制 `dimensions` 参数。兼容请求依据 [官方 Create embeddings 文档](https://developers.openai.com/api/reference/resources/embeddings/methods/create)，实现为 [embedding_service.py](../backend/knowledge/embedding_service.py)，校验响应数量、下标、向量维度、有限数值与非零范数，并限制批次、响应体和超时；不自动跟随重定向，也不截短或补零伪造维度。

迁移 kb0015 将 `kb_chunks.embedding` 改为可变维度 `vector`，保留原有向量。384／512／768／1024／1536 维使用对应的部分 HNSW 表达式索引，2048／3072 维用 `halfvec` 表达式索引加速且保留全精度原向量；其他允许维度走精确距离扫描。查询同时限制已授权范围、当前版本、模型键和实际维度。模型、接口地址、维度、前缀及修订标识定义向量空间，本地权重还使用文件指纹；批次、超时和密钥轮换不改变空间。更换空间后旧索引保留并显示重建提示，不能将旧向量与新查询混算。

入库、批次和重建任务在入队时固定嵌入配置，并逐级传给索引任务。索引记录配置、空间标识和真实维度，全部成功并复核原文版本后才切换。历史未携带嵌入配置的任务按旧 MiniLM 384 维执行。语义切分使用同一份任务模型配置；纯预览仅使用已缓存本地模型，接口模式预览不外发文献。`embedding-info`、`embedding-credential`、`embedding-test`、`embedding-prepare` 仅在管理员配置入口开放；连接测试使用固定句，实际接口调用可能计费；本地模型准备是可查看进度的独立后台任务。`embedding-info` 通过 `embedding_cache.py` 只读检查与推理相同的缓存目录，识别 Hugging Face 当前引用、旧版 FastEmbed 缓存、必需 ONNX／外部权重／分词文件及已记录大小；不实例化模型、不发起下载或推理。预设返回 availability，任务按模型分别返回 preparation_jobs；其他模型的准备任务不会阻止准备当前模型。状态表示文件是否存在，实际推理仍需显式测试。模型清单直接提交 `embedding-prepare`，按资料库和模型去重；取消及重试使用统一任务接口，重试拒绝与同模型的活动任务冲突。`embedding_download.py` 通过隐藏子进程运行 FastEmbed 下载、加载和固定句维度校验；父进程检查任务状态及租约，取消时终止子进程，父进程失联时由心跳监视退出。失败和取消保留缓存，重试复用下载器支持的缓存／断点文件，不删除其他模型。下载阶段显示活动状态，完成后显示校验阶段；不将下载百分比估算为真实传输进度。成功后清除该模型的进程内旧缓存，下次加载已校验文件。下载、取消和重试均不修改已保存的模型配置或现有文献索引。

API Key 不写入数据库设置、任务和诊断，由 [embedding_secrets.py](../backend/knowledge/embedding_secrets.py) 保存到后台共享资料目录的 `.embedding-secrets/`，凭据引用绑定服务根地址。Windows 使用当前服务账号 DPAPI 加密，其他平台使用受限目录及文件权限；跨机器或跨账号恢复后需重新录入 Windows 凭据。完整后台备份需包含此目录，公开资料导出不携带密钥。也可读取指定的后台环境变量。接口错误不回显服务响应或密钥，文献与查询前缀分别生效。

页面入口是 [RetrievalOptionsPanel.tsx](../src/workbench/knowledge/RetrievalOptionsPanel.tsx)，入库与 Query 配置分别为 [IngestionPanel.tsx](../src/workbench/knowledge/IngestionPanel.tsx) 和 [QueryProcessingPanel.tsx](../src/workbench/knowledge/QueryProcessingPanel.tsx)。原文清洗、分块、解析、模型调用和检索计算均在后台；浏览器只提交参数、发起任务并展示结果。

运行 `SHIXUE_MANAGED_TESTS=1` 下的 `tests.test_adaptive_chunking`、`tests.test_rag_pipeline`、`tests.test_parent_rerank`、`tests.test_research_assistant` 和门户集成测试，覆盖自动选择、真实语义边界、逐文件覆盖、清洗映射、配置冻结、Query 预算、权限范围和后台任务发布约束；GUI 检查实际混合上传、文档策略预览／保存及手机布局，不调用付费模型。

## 数据与提交边界

模型密钥、数据库密码、内部令牌、环境文件、真实研究资料、完整备份、模型响应和安装包不提交 Git。旧本机与个人服务客户端凭据使用系统凭据库；账号客户端使用独立 Electron profile 的 HttpOnly 会话，不保存明文账号密码；服务端凭据与账号库保存在受保护的私有目录。内置数据库和业务 Worker 使用的权限由服务端管理。

提交前检查 `git status --short`、`git diff --cached --stat` 与暂存内容；`.gitignore` 不影响已经跟踪的文件。若有效密钥被提交，先撤销和轮换，再处理历史副本；问题报告只保留脱敏的复现信息。


## 文献加工 GPU 加速

`npm run setup:gpu` 调用 [setup_gpu_runtime.py](../scripts/setup_gpu_runtime.py)，按 [requirements-gpu.txt](../backend/requirements-gpu.txt) 将 ONNX Runtime CUDA 12 / cuDNN 9 依赖安装到 `.local/gpu-runtime`，以已缓存的 E5 模型实际推理验证后写入 `ready.json`。自定义资料位置使用 `.venv\Scripts\python.exe scripts/setup_gpu_runtime.py --library-root <系统资料库目录>`；缺少模型时先在管理员页面下载，不在此入口自动下载文献模型。

`portal_runtime` 在导入工作区服务前加载已验证且 Python 版本匹配的独立运行时，不替换原有 CPU 包。CUDA 会话设置 6 GiB 显存分配上限；GPU 初始化失败可回退 CPU 并记录原因，实际设备写入 `embedding-execution.json`，只接受当前 Worker PID 的状态。CPU 默认按核心数使用最多 8 个线程；部署可用 `SHIXUE_EMBEDDING_THREADS` 调整，`SHIXUE_EMBEDDING_DEVICE=cpu` 强制回退、`cuda` 要求 GPU 可用。依据 [ONNX Runtime CUDA 文档](https://onnxruntime.ai/docs/execution-providers/CUDA-ExecutionProvider.html) 和 [FastEmbed GPU 文档](https://qdrant.github.io/fastembed/examples/FastEmbed_GPU/) 验证 DLL 预加载及实际会话设备，不能仅凭 providers 列表认定 GPU 已执行。

切换 CPU / GPU 不改变模型、维度、前缀和向量空间标识，已有索引继续有效。`auto_semantic=false` 为自动规则默认值：有结构使用结构父子块，无明显结构的长文用递归父子块；显式 `semantic` 或语义父子规则不受此开关影响。自动规则版本记录为 `document-routing-spans-v2`。

同一台机器可在 GPU 运行时就绪后再运行 `npm run setup:fp16`（先 `npm stop`），把已缓存的 E5 权重转换为半精度副本：`scripts/optimize_embedding_fp16.py` 直接改写已缓存 ONNX 的外部权重（不下载模型），再把 `fp32` 与 `fp16` 的输出在 480 字左右的样本上逐一对比，余弦达到阈值才写入带 `validated` 标记的 `meta.json`。目录位于 `<系统资料库>/models/fp16/<模型>/`，`models.py` 只加载经过校验的副本，加载失败或副本缺失时自动使用原始权重；副本通过 `identity.txt` 沿用原始权重指纹，`embedding_space`、模型键和已有索引都不变，因此查询向量与历史文档向量仍可比较。转换器不依赖 `onnx` 包，按 protobuf 线格式处理，未识别的字段原样保留；只对已完成数值验证的模型开放。设置 `SHIXUE_EMBEDDING_FP16=0` 可强制回退原始精度。本机实测 fp16 与 fp32 输出的余弦均值 0.999997、最小值 0.999981，同条件吞吐约 1.3–1.8 倍。

GPU 会话默认启用 TF32（`NVIDIA_TF32_OVERRIDE=1`），cuBLAS 的 fp32 矩阵乘走张量核心，实测本机 E5-large 约快 20%，输出余弦与严格 fp32 的差异在小数点后 6 位；设置 `SHIXUE_TF32=0` 可恢复严格 fp32。`embedding-execution.json` 记录实际设备、`tf32` 与 `fp16` 标记。文档向量化按分块配置的“每批文本数”分批调用模型（本地默认仍为 16，可提高到 64 以上提速，外层不再固定 32 条）；分块文本的归一化、分词与向量序列化在后台线程与 GPU 推理重叠执行，写入阶段只在最后提交一次。

显式优化已有未完成队列时使用 `processing_upgrade.optimize_pending`：只更新排队自动策略中未主动启用语义计算的文献，撤销运行任务的旧租约并重新排队，对“多次意外中断”失败任务允许重试；保留模型配置、批次标识、手动策略和所有已发布索引。任务 payload 记录 `performance_update`；重复执行保持幂等。需在撤销租约后切换旧 Worker，新任务通过版本与租约门控发布，旧进程不能覆盖结果。不要仅杀 venv 重定向父进程而遗留实际 Python Worker。
