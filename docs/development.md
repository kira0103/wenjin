# 开发、数据库与打包

## 环境

主要目标为 Windows x64，开发环境使用 Python 3.12 与 Node.js 22。Python 依赖见 `backend/requirements.txt`，npm 使用 `package-lock.json`。开发命令中的 `.venv\Scripts\python.exe` 与 Windows 打包流程匹配。

## 数据库选项

**Docker 开发库**：按根目录 README 执行 `dev-prepare`、Compose、`dev-upgrade`、`dev-configure`。随机密码位于 `.local/knowledge/`，工作台凭据另存于系统凭据库；不要打印密码文件。Compose 仅绑定 `127.0.0.1:55432`。`docker compose -f compose.knowledge.yml stop` 保留数据卷；不要用删卷来修复已有数据库。

**外部数据库**：需要 PostgreSQL、vector 和 pg_trgm 扩展。数据库管理员建立角色与扩展，迁移账号执行结构升级，应用账号负责业务读写。详细权限见 [deployment/knowledge](../deployment/knowledge/README.md)。应用不会自动升级外部库。

**内置数据库**：开发启动默认在 `build/postgres/` 查找运行时，打包后从资源目录加载。资料目录需要本地固定磁盘的 NTFS 文件系统；不要将运行时目录误当成数据库资料目录。

## 准备内置运行时

先安装 Python 与 npm 依赖，再执行：

```powershell
.venv\Scripts\python.exe scripts\fetch_managed_build_inputs.py
.venv\Scripts\python.exe scripts\prepare_managed_runtime.py
```

下载脚本使用 `deployment/knowledge/managed-runtime-inputs.json` 中的固定 URL 与 SHA-256，下载 PostgreSQL、pgvector、Microsoft 构建工具、SDK 和运行库到 `.local/runtime-downloads/`，不会将编译器全局安装。准备脚本编译 pgvector、复制许可和运行库、调整 Windows CLI 的 UTF-8 清单并生成 `runtime-manifest.json`。该步骤需要网络、Windows 安装程序组件和充足磁盘空间。

目前固定 PostgreSQL 17.11 与 pgvector 0.8.7；更新前应检查来源、摘要及数据库兼容性。脚本中还保留工具链内部版本路径，供应商归档布局变化时需要同步核验。

## 测试

```powershell
npm test
npm run build:ui
.venv\Scripts\python.exe -m unittest discover -s tests -v
```

默认后端测试使用临时资料、模拟模型和受控请求；真实数据库集成测试默认跳过。需要 Docker 开发库的集成测试：

```powershell
$env:SHIXUE_KB_INTEGRATION = '1'
.venv\Scripts\python.exe -m unittest tests.test_knowledge_postgres -v
Remove-Item Env:SHIXUE_KB_INTEGRATION
```

需要准备好 `build/postgres/` 的 Windows 内置库测试，可设置 `SHIXUE_MANAGED_TESTS=1` 后执行相关测试或完整回归，结束后移除环境变量。集成测试会启动独立数据库，执行前确认没有额外的连接覆盖或正式资料路径。不要默认开启真实收费模型调用。

## 打包

`npm run build:backend` 使用 PyInstaller 生成目录式 Python 服务到 `build/backend/`，`.spec` 为自动生成文件。`npm run dist:win`、`dist:portable` 和 `dist:candidate` 依次构建界面、后端，并通过 electron-builder 打包。

Electron 打包需要 `build/backend/ShixueWorkbenchService/` 与 `build/postgres/`。最终包应保留第三方许可和数据库运行时清单。打包命令生成本地安装包，不自动上传 GitHub Release。

## 本机脚本与数据

`scripts/` 保留历史候选包验收工具，可能引用已被 Git 忽略的旧发布包、报告和内部验收文档。它们服务于特定版本复核，不能代替上述通用开发流程。

根目录既有的历史设计与验收 Markdown 在本机保留，默认不纳入仓库。旧 README、代码导航和删除的冗余文件在本机 `.local/publish-backup/` 中留有副本。新公开说明统一维护在仓库文档中。
