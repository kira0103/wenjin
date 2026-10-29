# Web 用户工作区与管理员后台

多用户入口为 `/app/` 个人研究工作区和 `/admin/` 管理后台。管理员也可以进入自己的研究工作区。

## 公共资料与个人研究

| 数据 | 保存与权限 |
| --- | --- |
| 文献书目、版本、原件、录文、分类、切分与向量索引 | 公共资料库保存一份，所有已登录研究用户可读；管理员维护 |
| 项目、项目选材、摘录、笔记、研究检索记录、助手配置 | 按账号独立保存 |
| 点校、预处理、研究任务、进度、工作稿、成果、投稿记录 | 按账号独立保存，后台持续执行 |

PostgreSQL 的 `public` schema 保存公共文献。每个账号使用 `w_<workspace_id>` 研究 schema 和单独的运行角色。运行角色只有公共表的 SELECT 权限及自己研究表的读写权限，不能读取其他账号的研究表，也不能改公共文献。个人研究表的外键引用公共文献；表、索引、约束及不可变助手版本触发器在分区创建时按当前结构建立，并记录布局版本和文献结构版本。结构不匹配会停止启动，需在维护窗口升级，不会静默重建。

账号选择和数据库 search_path 均由服务端身份决定，不能通过请求参数切换。公共目录查询使用公共库身份；私人研究记录即使有相同的 `project-main` 项目 ID，仍在各自 schema 中。

原件保存在 `shared-library/assets/`。各账号的 SQLite、工作稿、检查点、任务日志与成果保存在 `workspaces/<workspace_id>/`；不会为每个账号重新导入文献或建立一份索引。一个公共资料库宿主负责导入和索引，各账号仍有独立 API 与 Worker。

管理员在后台“公共资料库”中上传文件或目录、维护分类、编辑书目、发布录文修订。公共库中的维护操作记录到管理审计；个人工作区里的管理员账号也没有直接修改公共文献的权限。普通用户当前通过管理员发布资料。

公共修订追加新版本，历史录文与引用保留；文献移出公共目录采用软删除。项目已有固定输入快照和摘录不会被新录文覆盖。管理员删除预览只汇总各账号的引用数量，不返回私人项目名称或笔记正文。

## 账号与配置

密码使用带随机盐的 scrypt 哈希，会话凭据只以哈希存入账号库。浏览器使用 HttpOnly、SameSite Cookie；停用账号、修改角色或重置密码会撤销登录。临时密码首次登录后必须修改。

模型服务和数据库配置由管理员管理。私人草稿缓存在按账号分开的标签页存储中；另一标签页切换账号后，旧页面请求会被阻止。

## Windows 试运行

先安装 README 的依赖并准备 `build/postgres` 内置运行时。测试和门户使用新的目录，不会读取已有的桌面工作区。

```powershell
$env:SHIXUE_PORTAL_ADMIN_PASSWORD = '<至少12位的初始管理员密码>'
npm run portal -- --data-dir .local/portal-data --init-admin admin
Remove-Item Env:SHIXUE_PORTAL_ADMIN_PASSWORD
npm run build:ui
npm run portal -- --data-dir .local/portal-data --ui-dir dist --host 127.0.0.1 --port 8765
```

访问 `http://127.0.0.1:8765/admin/` 建号、配置模型并发布公共文献，研究用户访问 `/app/`。开发界面使用 `npm run dev:web`，`/api` 默认代理到 8765，可用 `SHIXUE_DEV_SERVER` 修改。

## 外部 PostgreSQL

```powershell
$env:SHIXUE_PORTAL_PG_ADMIN_URL = 'postgresql+psycopg://<管理账号>:<URL编码密码>@127.0.0.1:5432/postgres'
$env:SHIXUE_MODEL_API_KEY = '<模型密钥>'
npm run portal -- --data-dir .local/portal-data --ui-dir dist
```

管理连接需有创建数据库、角色和安装 vector、pg_trgm 扩展的权限。门户只建立一个公共数据库，并在其中为每个账号分配研究 schema 与受限角色。管理凭据保存在受保护的运行目录，业务 API、Worker 和浏览器不接收数据库管理连接。

Linux 使用 `.venv/bin/python -m backend.portal`，通过 HTTPS 同源反向代理提供网页和 `/api`。参考 `deployment/server/wenjin-portal.service.example`，调整服务账号、目录和环境文件。

## 重启与备份

门户重启重新连接现有宿主，关闭网页或退出账号不停止任务。systemd 示例使用 `KillMode=process`，只重启门户主进程。

维护前停止对外门户，然后执行：

```powershell
npm run portal -- --data-dir .local/portal-data --stop-workspaces
```

命令先等待个人 Worker 保存并退出，再停公共数据库。随后配套备份账号库、公共 PostgreSQL 数据库（含所有个人 schema）、`shared-library/`、`workspaces/` 和私有配置。外部 PostgreSQL 使用逻辑备份。网页中的旧单工作区全库备份入口在门户模式禁用，避免生成缺少其他账号文件的不完整备份。

## 旧账号迁移与显式发布

新门户不会自动公开已有本机资料，也不会在登录时替换旧账号数据库。此前“每账号一个数据库”的工作区会被识别并保留，需在离线维护窗口处理。

首次从旧账号建立公共库时，可明确发布该账号的全部文献和原件：

```powershell
# 先停止门户并完成配套备份；公共库必须为空。
.venv\Scripts\python.exe -m backend.portal_migrate --data-dir .local/portal-data --username alice --publish-library
```

迁移校验原件、保持文献与引用 ID，将文献数据放入公共库、笔记和研究记录放入该账号研究分区，SQLite 和原有工作稿留在原账号目录。原数据库保留；导入失败时数据库事务回滚。迁移凭据与发布回执支持提交后中断的重试。存在未完成任务、公共库已有文献或目标个人分区已有数据时会拒绝覆盖。

这个入口适用于首次建立公共库；之后其他旧库的文献须在管理员界面选择原件导入，不能用全库迁移覆盖公共库。桌面个人服务模式继续保留，可用于读取和核对旧资料。已有桌面资料不会因启动门户被改变。

## 验证

```powershell
npm test
npm run build:ui
.venv\Scripts\python.exe -m unittest tests.test_portal -v
$env:SHIXUE_MANAGED_TESTS = '1'
.venv\Scripts\python.exe -m unittest tests.test_portal_native -v
.\node_modules\electron\dist\electron.exe scripts/check_portal_gui.cjs
```

真实数据库验证覆盖两账号读取同一原件、私人项目选材和笔记隔离、数据库越权拒绝、修订后引用保留、门户重启时任务持续执行、外部数据库部署与旧库显式发布。界面验收使用临时账号和虚构文献，报告位于 `.local/portal-gui/`。
