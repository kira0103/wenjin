# 客户端与独立资料服务

问津支持两种运行方式：原有桌面模式，以及客户端连接独立资料服务的模式。

独立服务目前面向**一个资料工作区、一位所有者、多个客户端**。连接凭据授予该工作区的全部业务权限；它不是多人账号或租户权限系统。一个工作区运行一个 API 进程和一个 Worker，两个进程共享服务端资料目录。

```text
浏览器 / Electron 客户端
  ├─ 页面、编辑草稿、PDF 渲染、文件选择与下载
  └─ HTTP API
       ├─ FastAPI：认证、业务校验、查询、保存、上传、导出
       ├─ Worker：研究、格式识别、批量点校、文献解析和索引
       ├─ SQLite：现有项目、工作稿、点校、投稿和检查点
       ├─ PostgreSQL + pgvector：文献、检索、助手和持久化任务
       └─ 服务端原件、成果和上传暂存目录
```

客户端退出不会停止独立服务或 Worker。API 重启保留 Worker 正在执行的任务；Worker 意外中断后，普通研究和格式识别任务显示为可恢复，避免自动重复未知用量的模型调用。知识库任务沿用租约恢复机制，点校保留已有的片段与人工合并规则。

## 准备数据库和服务工作区

先安装 README 中的 Node.js、Python 依赖，准备独立 PostgreSQL，并安装 `vector`、`pg_trgm` 扩展。数据库角色与权限可以参考仓库的 `deployment/knowledge/init.sh`；迁移账号使用 `shixue_owner`，API 与 Worker 使用 `shixue_app`。

以下命令为 Windows PowerShell 示例。Linux 使用同样的 Python 模块入口，将 `.venv\Scripts\python.exe` 换成 `.venv/bin/python`。

```powershell
# 先完成数据库结构升级，使用迁移账号。
$env:SHIXUE_KB_MIGRATION_URL = 'postgresql+psycopg://shixue_owner:<URL编码后的密码>@127.0.0.1:5432/shixue_knowledge'
.venv\Scripts\python.exe -m backend.knowledge.cli upgrade
Remove-Item Env:SHIXUE_KB_MIGRATION_URL

# API 和 Worker 使用相同的环境、系统账号与服务端资料目录。
$env:SHIXUE_KB_DATABASE_URL = 'postgresql+psycopg://shixue_app:<URL编码后的密码>@127.0.0.1:5432/shixue_knowledge'
$env:SHIXUE_SERVER_TOKEN = '<至少32字符的随机连接凭据>'
$env:SHIXUE_MODEL_API_KEY = '<模型服务密钥>'

# 停止该工作区的 API、Worker 后执行。检查旧资料并激活统一文献库。
.venv\Scripts\python.exe -m backend.server --data-dir .local/server-workspace --prepare
npm run build:ui
```

可以用 `python -c "import secrets; print(secrets.token_urlsafe(48))"` 生成连接凭据。服务启动时不会自动生成或打印凭据。模型地址、名称和容量仍在页面的“模型与设置”中配置；`SHIXUE_MODEL_API_KEY` 由执行服务读取，页面只能看到是否已配置。

需要快速搭建开发数据库时，可继续使用 `compose.knowledge.yml`、`dev-prepare` 和 `dev-upgrade`。该 Compose 的主机端口是 **55432**，上述连接 URL 的端口应相应调整。

## 启动 API 与 Worker

在两个终端中加载相同的服务环境，然后分别运行：

```powershell
# 终端一：API 和已构建的网页
npm run server -- --data-dir .local/server-workspace --ui-dir dist --port 8765

# 终端二：后台执行
npm run worker -- --data-dir .local/server-workspace
```

浏览器打开 `http://127.0.0.1:8765`，输入 `SHIXUE_SERVER_TOKEN` 的值。浏览器登录后使用有效期 12 小时的 HttpOnly、SameSite 会话 Cookie，不将连接凭据存入浏览器本地存储。会话过期时重新连接，已打开页面中的编辑状态继续保留。

开发网页时可运行 `npm run dev:web`，访问 `http://127.0.0.1:5173`。Vite 将 `/api` 代理到 `http://127.0.0.1:8765`；通过 `SHIXUE_DEV_SERVER` 修改代理目标。API 可自行托管网页，也可以由反向代理同时提供网页和 `/api`，保持同源。

API 默认仅监听回环地址。远程访问应通过 HTTPS 反向代理；需要直接监听网卡时使用 `--host 0.0.0.0`。仅将 HTTP API 暴露给客户端，数据库保持在服务端网络内。不要同时用原有桌面模式打开同一份服务端工作区。

`GET /health` 检查 API 存活；认证后的 `GET /api/service/status` 返回后台执行进程是否在线。API 在线而 Worker 未启动时，任务保存在队列中，启动 Worker 后继续领取。

Linux 服务管理示例位于 `deployment/server/`。API 与 Worker 是两个独立服务，API 重启不应连带停止 Worker。环境文件由服务账号读取，包含数据库 URL 和模型密钥，不放入代码仓库。

## Electron 薄客户端

开发时运行：

```powershell
npm run dev:client
```

启动后填写服务根地址和连接凭据。也可以由环境提供：

```powershell
$env:SHIXUE_SERVER_URL = 'https://wenjin.example.com'
$env:SHIXUE_SERVER_TOKEN = '<连接凭据>'
npm run dev:client
```

地址格式为 `https://wenjin.example.com`，不附带 `/api`、用户名、密码或查询参数。凭据仅留在主进程，并在操作系统安全存储可用时加密保存；无法安全加密时只保存地址，下次启动重新输入凭据。

```powershell
# 只打包页面与 Electron，不包含 Python、PostgreSQL 或模型运行时。
npm run dist:client
```

输出为 `release/client/`。原有 `npm run dev`、`dist:win`、`dist:portable` 保留本机完整桌面模式。

## 上传与迁移

远程导入上传文件内容及相对目录清单。服务不会读取客户端提交的绝对路径。每块最多 4 MB、单文件最多 250 MB、每批最多 20 GB；目录最多 50,000 个文件，多文件选择最多 5,000 个文件。

浏览器和 Electron 保存上传批次编号。上传中断后，重新选择同一批、内容未变化的文件，可读取服务器已接收的位置并继续。全部上传完成后才生成文献预览，确认后由 Worker 解析入库。`uploads/` 是服务端暂存目录，导入完成前应保留；原件入库后由现有文献存储保存。

工作区首次初始化生成 `workspace-identity.json`，保留旧目录对应的数据库 key。完整移动工作区时必须一起携带此文件；移动后的查询继续使用相同身份。备份恢复继续按恢复目标绑定新的工作区范围。

迁移已有资料建议使用原有完整备份与恢复流程，同时迁移 SQLite、PostgreSQL 和原件；跨操作系统迁移使用逻辑备份恢复，避免保留旧系统的绝对原件路径。不要直接把客户端本机 SQLite 放到网络共享盘供多个客户端读写。

工作区切换、库结构升级、恢复和服务停止由服务端管理。先停止 Worker 和 API，再运行升级、恢复或 `--prepare`，最后重新启动两个进程。客户端的业务 API 无法关闭整个服务，也无法提交服务端目录扫描或在线全库激活操作。

## 代码边界和验证

- `src/workbench/transport.ts`：浏览器 HTTP 与 Electron IPC 通信适配。
- `src/workbench/platform.ts`：文件选择、保存、外链、主题等平台能力。
- `backend/server.py`、`backend/auth.py`：独立 API 启动、认证与管理边界。
- `backend/worker.py`：独立后台执行进程，复用现有业务状态和知识库任务租约。
- `backend/uploads.py`：持久化分块上传及服务端文献预览。
- `backend/api/exports.py`、`backend/services/exports.py`：HTTP 适配与稿件、引文导出用例。
- `backend/identity.py`：稳定工作区身份。

当前保留双数据库和既有历史迁移链。后续可继续按业务模块迁移 SQLite 数据到 PostgreSQL；客户端已经通过 API 访问业务数据，数据库调整不需要再改变客户端的存储方式。

```powershell
npm test
npm run build:ui
.venv\Scripts\python.exe -m unittest discover -s tests -v

# 显式启用独立的 Windows PostgreSQL 上传集成验证
$env:SHIXUE_MANAGED_TESTS = '1'
.venv\Scripts\python.exe -m unittest discover -s tests -p test_server_uploads.py -v
```

`test_service_process.py` 使用真实 API、Worker 和本地模拟模型验证关闭客户端、API 重启后的任务持续执行，不调用云端模型。数据库集成测试使用临时集群，不连接真实研究库。

构建网页后可用隐藏窗口验证浏览器及已打包的薄客户端：

```powershell
.\node_modules\.bin\electron-builder.cmd --dir --win --config electron-builder.client.cjs
.\node_modules\electron\dist\electron.exe scripts/check_client_server_gui.cjs --client-binary release/client/win-unpacked/问津客户端.exe
```

该检查不依赖真实研究数据或云端模型；截图、运行日志和报告位于 `.local/client-server-gui/`。
