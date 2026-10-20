# PostgreSQL 开发部署

当前迁移头为 `kb0012`。推荐按[根目录 README](../../README.md) 启动 Compose 开发库；内置数据库打包见[开发说明](../../docs/development.md)。

Compose 使用固定摘要的 pgvector / PostgreSQL 17 镜像，仅开放 `127.0.0.1:55432`。`dev-prepare` 为管理员、迁移账号和应用账号生成独立随机密码到 `.local/knowledge/`，已有文件不会覆盖。密码不得提交或打印到共享日志。

初始化脚本只对新数据卷执行。`shixue_admin` 创建数据库和扩展，`shixue_owner` 执行迁移，`shixue_app` 执行业务读写。应用账号不应拥有管理员权限或 schema CREATE 权限。

## 外部部署

管理员准备 `vector`、`pg_trgm`，撤销 public schema 对 PUBLIC 的 CREATE，并按 `init.sh` 配置角色、表和序列的权限。迁移后核对默认权限及已有对象的权限。已有部署需要核心古籍序列权限：

```sql
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO shixue_app;
ALTER DEFAULT PRIVILEGES FOR ROLE shixue_owner IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO shixue_app;
```

由安全配置注入 `SHIXUE_KB_MIGRATION_URL` 后执行 `.venv\Scripts\python.exe -m backend.knowledge.cli upgrade`。工作台可通过设置界面配置应用账号，也支持 `SHIXUE_KB_DATABASE_URL` 覆盖。不要把含密码的真实 URL 写到命令示例、共享历史或报告中。

外部数据库升级需要显式执行，健康检查不会自动改结构。先对完整资料做备份并在隔离环境验证，再升级实际工作区。

## 审计、备份与恢复

`backend.knowledge.cli audit` 对旧 SQLite 做只读快照与一致性审计，不是包含所有原件的完整备份。日常完整备份恢复优先使用应用内“数据与备份”。恢复应写入新的资料目录，不能覆盖正在使用的工作区。

停止开发库使用 `docker compose -f compose.knowledge.yml stop`，数据卷继续保留。不要提交 Docker 卷、数据库文件、完整备份或本地模型缓存。
