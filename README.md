# 问津 Wenjin · 史学工作台

面向个人历史研究的 Windows 桌面工作台，将文献原件、研究项目、古籍点校、阅读笔记和论文投稿放在同一工作区。

当前版本：**0.5.0-rc.12**（开发候选版）。应用内名称暂沿用“史学工作台”，包名为 `shixue-workbench`。

## 能做什么

- **文献库**：导入 PDF、Word、TXT、Markdown 和目录，维护书目、版本、分类与附件；同一文献可被多个项目引用。
- **阅读与摘录**：PDF 连续滚动、文字阅读、原文定位、摘录与项目关联。
- **古籍整理**：本地拆分与清洗卷文本，批量 AI 点校，断点继续、差异对照、校记和人工审定。
- **研究助手**：文献检索、向量与关键词混合检索、带来源的研究问答，以及可配置的助手与 Skill。
- **论文投稿**：期刊卡片、CSV 导入、论文进度、投稿与返修记录、研究成果关联。
- **本地管理**：工作区迁移、备份恢复、内置 PostgreSQL + pgvector，以及外部数据库模式。

AI 输出需要人工核验；点校中的文字变化会进入差异复核。古籍拆分的固定规则主要针对《古今合璧事类备要》，其他版式应先验证。

## 从源码运行

主要支持 Windows x64。准备 Node.js 22、npm、Python 3.12；以下命令使用 PowerShell。

```powershell
git clone https://github.com/kira0103/wenjin.git
cd wenjin
npm ci
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
```

完整文献库需要数据库。开发时可使用 Docker Desktop 启动独立的 PostgreSQL：

```powershell
.venv\Scripts\python.exe -m backend.knowledge.cli dev-prepare
docker compose -f compose.knowledge.yml up -d --wait
.venv\Scripts\python.exe -m backend.knowledge.cli dev-upgrade
.venv\Scripts\python.exe -m backend.knowledge.cli dev-configure --data-dir '.\工作台数据'
npm run dev
```

首次启动选择“建立工作区”，配置文件会记住实际资料路径。上述命令适用于全新克隆；已有工作区请使用其实际路径，开发环境勿连接正式研究数据库。

`npm run dev` 同时启动 Vite 和 Electron，Electron 自动启动 Python 服务。Vite 页面依赖桌面桥接，不能直接作为普通网站完整使用。

不使用 Docker 时，可准备 `build/postgres` 内置运行时，见[数据库与打包说明](docs/development.md)。正式桌面包包含 Python 和数据库运行时，日常使用无需安装 Python 或 Docker。

## 模型和数据

在“模型与设置”中配置兼容 OpenAI Chat Completions 的服务地址、模型名与 API 密钥。模型服务可能收费；检索和本地模型的首次准备可能需要下载文件。AI 请求会将所选文本和检索上下文发给配置的服务商。

模型密钥与外部数据库密码通过系统凭据库保存；内置数据库凭据由程序生成并保存在资料目录的受限私有配置中。不要共享工作区、凭据文件或带密码的连接 URL。

新克隆默认没有文献、论文和真实期刊目录。投稿管理提供[虚构 CSV 示例](backend/data/journals.example.csv)，可从界面导入自己的目录。核心古籍需要自行准备本地来源目录并导入，仓库不附带全量语料和模型权重。

开发工作区默认位于项目下的 `工作台数据/`，已被 Git 忽略；也可在应用中选择已有工作区。备份应通过“数据与备份”操作，或正常退出后复制完整资料目录，不要只复制 SQLite 文件。

## 验证与构建

```powershell
npm test
npm run build:ui
.venv\Scripts\python.exe -m unittest discover -s tests -v
```

默认后端测试使用临时目录与模拟模型；真实 PostgreSQL 和 Windows 内置数据库集成测试需显式启用，见[开发说明](docs/development.md)。

准备内置 PostgreSQL 后，可打包 Windows 桌面程序：

```powershell
npm run dist:win
npm run dist:portable
# 候选版同时生成安装版和免安装版
npm run dist:candidate
```

输出位于 `release/`；`build/`、`dist/`、`release/` 均不提交到源码仓库。源码推送不会自动发布安装包。

## 项目结构

| 目录 | 用途 |
| --- | --- |
| `src/` | React + TypeScript 界面 |
| `electron/` | 桌面窗口、IPC、服务与工作区启动 |
| `backend/` | FastAPI 服务、研究与点校、知识库 |
| `backend/migrations/` | PostgreSQL 结构迁移，升级旧库所需 |
| `tests/` | Python 与桌面路径测试；前端测试也位于 `src/` |
| `deployment/` | 数据库初始化与固定摘要的运行时依赖清单 |
| `scripts/` | 构建、数据检查与隔离桌面验收工具，见[脚本说明](scripts/README.md) |
| `docs/` | 面向仓库使用者的开发与维护文档 |

更多架构关系见 [CODEBASE.md](CODEBASE.md)，协作约定见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 仓库边界

仓库仅包含代码、测试和公开开发文档。本机研究数据、真实期刊目录、密钥、数据库、日志、备份、安装包及内部设计验收记录已排除。提交前仍应检查暂存内容，见 [SECURITY.md](SECURITY.md)。

第三方依赖、模型与语料分别遵循其自身许可。仓库目前未另行声明项目开源许可证。
