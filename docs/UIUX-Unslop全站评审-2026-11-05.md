# 问津（史学工作台）全站页面评审与修改意见 · unslop

> 评审日期：2026-11-05 ｜ 评审对象：WSL `/root/wenjin`（HEAD `c89d6a7`）前端代码 + 部署实例 `http://127.0.0.1:8765` ｜ 方法：unslop skill（[heydamir/unslop](https://github.com/heydamir/unslop)，CC BY 4.0）九类迹象审计 + 静态度量 + 页面截图 ｜ 性质：只读评审，未改动任何代码、数据与部署。

---

## 0. 结论摘要

### 0.1 一句话结论

**这不是「AI 味」很重的界面，而是一套已经洗干净、但还没收口的系统。** 全站没有渐变、没有 emoji 图标、没有发光、没有循环动画，文案克制、危险操作有防护，很多细节（聚焦管理、删除确认、加载态、诚实的状态说明）比多数同类产品扎实；真正拖住它的是**三套视觉系统叠加、设计 token 没有收口、字号与间距不统一**，以及**研究助手、材料阅读、投稿管理三个核心页面的信息架构问题**。按 unslop 的判定属于「轻度 slop / polish 模式」：保留现有品牌、布局和功能，按本报告的 pass 顺序修。

### 0.2 最值得先做的 7 件事（P0）

| # | 问题 | 位置 | 为什么先做 | 归属 |
| --- | --- | --- | --- | --- |
| P0-1 | 管理登录页公开印着默认口令 `admin / admin123` | `WebEntry.tsx:57` | 任何仍用初始口令的部署等于裸奔后台；删一行即可 | PA01 |
| P0-2 | 三套色彩系统 + 266 组 `--tone-*` + 未定义 token 叠加：浅色下「待复核」与「已审定」同色，暗色下知识库文字只有 2.2–2.6:1 | `theme.css` / `palette.css` / `shell.css` / `modern.css` / `failures.css:3,6` / `processing-table.css:1-2` / `knowledge.css:31` | 所有「看起来不像设计过」的根源，可一次性收敛 | WB01/02、KB25、FB46/47、PA09、S1–S5 |
| P0-3 | 430px 手机端标题栏溢出，「研究助手」被裁掉 | `modern.css:389-391`、`App.tsx:402` | 移动端第一屏即不可用 | WB08 |
| P0-4 | 全局错误「双报」：页内错误 + 右上角持久浮层；两个浮层压在 64px 标题栏上且颜色不随主题 | `transport.ts:64`、`SystemFeedback.tsx:9-17`、`theme.css:313`、`systemFeedback.css:1` | 每一页都会遇到的第一层「不可信感」 | FB34/35/36 |
| P0-5 | 研究助手/协同研究的结果区没有排版：`.kb-markdown` 无任何 CSS，标题直出、列表是字面 `•`；未核验时草稿整段消失 | `MarkdownPreview.tsx:19,21,25`、`ResearchAssistant.tsx:68,75` | 研究助手是产品的重点能力，产出却是全站最糙的一块 | KB29/KB30/KB31 |
| P0-6 | 项目配置弹窗把「归档 / 删除 / 保存」三个按钮并排 | `ResearchViews.tsx:40` | 误点一次就进删除流程，数据风险 | WB39 |
| P0-7 | 投稿管理 0 篇时仍渲染 7 条空泳道 + 横向滚动，第 7 列被裁 | `SubmissionWorkbench.tsx:84`、`submissions.css:32-34` | 新用户第一屏是坏掉的看板 | FB01 |

### 0.3 评分卡

| 维度 | 现状 | 依据 | 目标 |
| --- | --- | --- | --- |
| 视觉基调 | B+ | 浅色暖白 + 青绿，登录页宋体标语有产品味；无渐变/光晕/emoji | 保持，中性面转纸白暖中性 |
| 系统一致性 | D | 门户墨绿/蓝灰 + 壳层石墨 + legacy 紫；266 tone ×2；431 个色值；7 条字体栈；29 种字号；20 种圆角；15 种图标尺寸 | 1 套语义 token、7 级字号、4 档圆角、2 档图标 |
| 排版与密度 | C- | 12px 最多（269 次）、≤11px 160 处、9–10px 仍有 41 处；行高 1.8–1.9 共 76 处；阅读正文行宽约 65 汉字 | 正文 14px、最小 12px、行高 1.5–1.75、正文 36–44em |
| 信息架构 | C+ | 项目概览四步引导清楚；但阅读页四层头栏、检索页长说明+大空白、助手结果区混乱、投稿空看板 | 每屏一个主任务、一个实心主按钮 |
| 反馈与状态 | C+ | 手段齐全（toast/空态/错误边界/重试），但双报、两套浮层、三种加载态、toast 颜色靠文案正则 | 单一 feedback stack + 显式 tone |
| 可达性 | C | 313 个 aria-label、skip-link、焦点陷阱做得好；但对比度 4.41/4.25 不达 AA、控件边框 1.2:1、部分 tab 无方向键、11px 小字偏多 | 全部正文 ≥4.5:1，控件边界 ≥3:1 |
| 移动端 | C- | 后台导航可抽屉、部分页面有窄屏策略；工作台标题栏 430px 溢出、期刊表只露两列 | 360–430px 全页面无横向溢出 |
| 文案 | C+ | 无感叹号、无夸张营销、有事实与边界说明；但术语不统一、长说明常驻、默认口令提示、成功态靠猜 | 术语表 + 每屏帮助 ≤1 行 |

### 0.4 本报告怎么读

- 第 1 章是**系统层**：方法、方向、九类 slop 迹象清单、设计系统体检数据、按顺序的修改方案。
- 第 2–5 章是**页面层**：门户/后台、用户工作台、文献库/知识库/点校/研究助手、投稿/期刊/全局反馈，逐页给出「做得对 / 主要问题 / 改法」，每条带 `文件:行号`、unslop 归类、优先级 P0/P1/P2。
- 第 6 章把四组问题**去重后排成路线图**；第 7 章是可直接勾选的验收清单；第 8 章是证据、术语表、Skill 安装记录与评审边界。
- 行号全部指向 WSL `/root/wenjin` 的 `c89d6a7`；路径省略前缀，例如 `src/workbench/App.tsx:402` 即 `/root/wenjin/src/workbench/App.tsx:402`。

### 0.5 与既有评审的关系

项目根目录已有 2026-11-01/02 的多份评审（`UIUX页面修改意见-2026-11-01.md`、`UIUX-Taste评审-2026-11-02.md`、`UIUX-Impeccable审计-2026-11-02.md` 等），其中「渐变、emoji、光晕、循环动效、过度承诺文案」等重 slop 项已经修复（本轮扫描确认：渐变 0、emoji 0、UI 感叹号 0、`transition-all` 0）。本报告**不重复已完成项**，只针对当前代码状态；与旧报告重叠的部分集中体现在第 1 章的系统收口与第 6 章的路线图里。

---

## 1. 方法、方向与系统体检

> 本章是系统层：先交代怎么评、方向是什么，再给出全站 slop 迹象清单与设计系统体检数据。页面层的具体问题见第 2–5 章。

### A.1 评审对象、方法与证据边界

| 项目 | 内容 |
| --- | --- |
| 代码基线 | WSL `/root/wenjin`，HEAD `c89d6a7`（3 个 perf 提交在本地 Windows 仓库 `c46b02f` 之后） |
| 与 Windows 本地差异 | `git diff --stat c46b02f..c89d6a7 -- src/` 为空：**前端 UI 代码与本地快照逐字节一致**；差异仅在 `backend/knowledge/retrieval.py`（检索性能）。因此页面截图对 WSL 代码同样有效 |
| 部署实例 | WSL2 Docker `wenjin-app-1` → `http://127.0.0.1:8765`（登录页可用；管理员口令已变更，登录后页面未抓取） |
| 代码规模 | `src/` 90 个 tsx/ts 文件、17 个 CSS 文件（264 KB，8,884 条声明）；lucide-react 图标库；React 18 + Vite |
| 评审 Skill | `unslop`（https://github.com/heydamir/unslop，commit `a6f121e`，CC BY 4.0，作者 Damir Sokolovsky @heydamir）。已安装到本项目 `.dsh/skills/unslop/`，评审按其九类迹象（间距/颜色/图标/文案/字体/布局/细节/视觉/动效）执行 |
| 截图证据 | `.local/unslop-shots/fixture/` 63 张（项目自带 GUI 验收夹具，覆盖空状态、列表、详情、编辑弹窗、暗色、移动端）；`.local/unslop-shots/live-login-*.png` 3 张（真实部署登录页） |
| 静态扫描 | `.dsh/scratch/unslop-review/audit-scan.json`（颜色/字号/圆角/阴影/emoji/文案）、`audit-system.json`（声明数、间距、图标尺寸、对比度） |
| 边界 | 只读评审，未改动任何代码、数据与部署；未对真实账号做写操作；未评估后端功能正确性与性能 |

**总体判定：不属于「重 slop」。** 全站 0 个色相渐变、0 个渐变文字、0 处彩色光晕、0 个 emoji 图标、0 句感叹号文案、0 个 looping 动画；配色、留白、圆角、按钮层级已经过一轮系统整理，视觉是克制的。按 unslop 的判定，这是**轻度 slop / polish 模式**：保留现有品牌与布局，修的是系统层的一致性（token、字号、间距、图标、身份统一）和页面层的信息架构。

---

### A.2 方向（Direction）

> 按 unslop 的要求写五行，从产品本身推导，不追趋势。

- **产品**：面向历史研究者与写作者的文献工作台——把选材、阅读、点校、检索、研究、成稿与投稿连成一条可追溯出处的研究链。
- **性格**：考据式的克制、纸墨的温润、朱批的果断（不是「现代、简洁、高级」这种放在任何产品都成立的词）。
- **世界**：纸、墨、书页、档案盒、朱砂批注、夜读台灯。中性面应当偏**纸白/墨灰的暖中性**，而不是现在的冷蓝灰 + 薄荷绿；强调色保留现有青绿（品牌已成型），但只出现在「当前选择 / 主操作 / 一个关键数字」。
- **时刻**（每屏只留一到两个，其余保持安静）：
  1. 文献阅读的双栏对照：左原件扫描页、右录文，页码用等宽字体对齐——这是产品最独特、最有「实物感」的一屏（截图 `audit-material-reader.png` 已有雏形）。
  2. 研究编辑器里的「证据 → 判断 → 成稿」链条：每条摘录/判断带出处签（卷叶/页码/段号），是产品区别于通用写作工具的地方（`audit-research-editor.png` 目前只有左右分栏，未把出处链做出来）。
- **签名细节**：**出处签**——`卷三·叶十二·段四` 这类定位用 12px 等宽字体 + 一条 1px 细线（朱砂色只在「待人工核验」状态使用），统一出现在摘录、引用、检索结果、投稿材料的出处位置。只此一个签名，不每页加花。

**把方向落到现有实现上，只做三件事**：
1. 中性色从冷蓝灰（`#f4f7f9` / `#101c25`）转向纸白/墨灰暖中性（例如 `#f7f5f0` / `#191a18`），青绿 `#157d70` 保留为唯一强调色。
2. 把「出处签」做成一个组件（`<SourceTag>`），先用在阅读页摘录、研究编辑器、检索结果三处。
3. 全站字体阶梯收敛到 7 级（见 A.4），把 10px/11px 的说明文字抬到 12px。

---

### A.3 全站 slop 迹象清单（unslop 九类）

| # | 类别 | 迹象 | 位置（WSL 路径） | 修法 |
| --- | --- | --- | --- | --- |
| S1 | 颜色 | 三套中性色/强调色并存：桌面壳 `shell.css` 暖灰、门户 `modern.css` 冷蓝灰、旧 `theme.css` + `palette.css` 紫调暗色 | `src/workbench/shell.css:1-2`、`src/portal/modern.css:2-13`、`src/workbench/palette.css:1-3` | 保留一套语义 token（见 A.5-1），门户与桌面只改 `--bg/--surface/--line/--text` 取值 |
| S2 | 颜色 | 266 个按十六进制命名的 tone token（每个主题一套，共 532 条声明），另有 9 个语义变量与 14 个 portal 变量；全站 431 个唯一颜色 | `src/workbench/palette.css:1-3`；`theme.css` 中 436 处 `var(--tone-*)` | 用 codemod 把 tone 映射到语义变量，palette.css 只保留过渡期映射表 |
| S3 | 颜色 | 背景带色相：门户亮色 `#f4f7f9`（冷蓝）、暗色 `#101c25/#172832`（蓝绿）；桌面暗色 `#1e1e1e` | `src/portal/modern.css:2-13`；`src/workbench/shell.css:1-2` | 亮色改纸白暖中性；暗色改墨灰中性，青色只留强调色 |
| S4 | 颜色 | 彩色阴影与超大面积软阴影：`0 2px 4px #157d7018`、`0 24px 100px #10243030`、`0 20px 100px #0002`，最大 blur 118px | `modern.css:22,31,64,70`；`portal.css:37` | 仅两级 elevation：卡片不用阴影（用 hairline），浮层 `0 16px 40px rgba(20,28,32,.16)` |
| S5 | 颜色 | 语义色未收口：错误红在 `systemFeedback.css` 写死 `#bc4834`，检索错误又用 `--danger` fallback | `src/workbench/systemFeedback.css:1`；`src/workbench/knowledge/retrieval.css:61` | 统一 `--danger/--success/--warning` 三个 token，暗色各给一档 |
| S6 | 字体 | 字号过碎：29 种字号；12px 出现 269 次、11px 124 次、10px 25 次、9px 8 次（≤11px 共 160 处）；正文行高 1.8/1.9 共 76 处 | 全 CSS；典型 `portal.css:8,12,16,22,24,26,30,32,33,34,35,41`、`modern.css:93,202,215` | 7 级阶梯（A.4），UI 正文 14px、元信息 12px、行高 1.5–1.6 |
| S7 | 字体 | 字重 550/650 非标准字重 32 处；H1 有 22/23/24/25/27/28 六种尺寸，且 27–28px 带 `-0.6px` 负字距（中文大标题不需要） | `portal.css:17,20,25,29,30,32`；`modern.css:65,225,416`；`desktop-density.css:4,39,49` | 字重只用 400/500/600；H1 固定 28/24（桌面/移动），中文字距 0 |
| S8 | 图标 | 15 种图标尺寸：16×80、15×37、17×33、14×26、18×21、20×10、19×8、13、24、22、28、21、12、26、40；无一处设置 strokeWidth（默认 2） | tsx 全局统计 | 只允许 16/18 两档，`strokeWidth={1.5}`；正文内联箭头改用 `ArrowRight` 图标 |
| S9 | 图标 | 14 处 `→` 字符当按钮/流程箭头用（`从文献库选材 →`、`下一步 →`） | `ResearchViews.tsx`、`Pages.tsx`、`AdminConsole.tsx`、`CoreLibrary.tsx` 等 | 换成 lucide `ArrowRight size={16}`，或从文案删除 |
| S10 | 间距 | 无任何间距 token（`--space-*` 0 个）；gap 值前三是 12/8/10，但 14/7/5/9/18/22/17/11/13/15 合计 150+ 处；控件高度 8 种（34/36/38/39/40/42/44/46/48） | 全 CSS | 4px 阶梯 token；控件 32/36/40 三档 |
| S11 | 细节 | 圆角 20 种（3,4,5,6,7,8,9,10,11,12,13,14,15,16,20,50%,999px）；阴影 blur 18 种 | 全 CSS | 圆角 4/8/12/16 四档，嵌套=外圆角-内边距（≥4） |
| S12 | 细节 | 8 种 z-index 体系混用（1…4000，浮层 1200/1100/4000） | `portal.css:37`、`modern.css:109,458`、`systemFeedback.css:1` | z 阶梯：10 内容 / 40 侧栏 / 60 遮罩 / 80 抽屉 / 100 弹窗 / 120 toast |
| S13 | 细节 | `!important` 16 处，其中 `color:…!important` 5 处用于覆盖门户样式 | `portal.css:21,22`、`submissions.css:70`、`english-journals.css:61`、`shell.css:3`、`retrieval.css:61,102,134` | 用选择器顺序/变量解决，逐步清零 |
| S14 | 动效 | 两处动画布局属性：进度条 `transition:width .18s`、skip-link `transition:top .12s` | `overlays.css:14,41` | 进度条用 `transform:scaleX()`；skip-link 用 `transform:translateY()` |
| S15 | 动效 | 过渡时长 5 种（.12/.15/.16/.18/.2s）、缓动 3 种；`prefers-reduced-motion` 仅 4 处覆盖，门户用 `*` 全局禁用但工作台未覆盖 | `modern.css:333`、`overlays.css`、`theme.css` | 两个时长（.12s 反馈 / .2s 进入）+ 一个 ease-out；全局 reduced-motion |
| S16 | 布局 | 页宽三套：`1220px`（theme.css `.page-container`）、`1400px`（shell.css）、`admin` 全宽 + `clamp` 内边距；同一应用宽屏下内容落点不一致 | `theme.css:44`、`shell.css:38`、`portal.css:29` | 统一 `--content-max:1400px`，页面内边距 24/32 |
| S17 | 布局 | 页面空态大面积死白：概览/批量点校/用户管理页底部半屏空白，首屏未按整屏构图 | 截图 `user-workspace.png`、`audit-collation-batch.png`、`admin-users.png` | 空态卡下方补「下一步/最近记录」或让内容区自适应高度 |
| S18 | 文案 | 登录页问候式标题「欢迎回到问津」；管理登录页常驻「首次默认账号：admin / admin123」提示（真实部署口令已改，提示会误导并暴露默认口令习惯） | `src/portal/WebEntry.tsx:57` | 标题改「登录」/「问津 · 登录」；默认账号提示只在未初始化时显示 |
| S19 | 文案 | 说明文案偏长：中文长字符串 24 条（最长 151 字），帮助文字行高 1.8–1.9，表单下方常驻两行说明 | `ProcessingProgress.tsx`、`BatchViews.tsx`、`KnowledgeWorkbench.tsx`、`Pages.tsx` | 每屏帮助文字 ≤1 行 24 字；长说明收进 `?` 悬浮 |
| S20 | 文案 | 术语不统一：文献/材料/资料/原文混用；工作台/工作区/工作空间混用；研究判断/研究结论/研究成果混用；入库/加工/索引混用 | 全站 | 产出一份 20 词术语表，统一文案与 aria-label |
| S21 | 组件 | 空状态卡既大又居中，且与页头主按钮重复（批量点校页头「新建批次」+ 空态里「新建批次」） | `ResearchViews.tsx`（CollationWorkspace）、`Pages.tsx` | 空态只留一个主操作；页头按钮改为「新建」并滚动/聚焦空态 |
| S22 | 组件 | 状态徽标仍是 10px 胶囊（已启用/尚未启动/待准备） | `portal.css:33`、`.tag` 系列 | 12px，去胶囊化：状态点 + 文本 |
| S23 | 可达性 | 亮色 `--portal-muted:#657583` 对 `#f4f7f9` 仅 **4.41:1**，对薄荷底 `#eaf5f0` 仅 **4.25:1**，10–11px 小字不达 AA | `modern.css:3` | 改为 `#5b6a78`（对底色 5.17:1，对 soft 4.98:1） |
| S24 | 可达性 | 焦点样式：门户 `outline:2px solid var(--portal-accent)`（可用）；工作台 `outline-color:var(--muted)` 对比不足；skip-link 存在（好） | `modern.css:20`、`shell.css:3` | 统一 2px accent 焦点环 + 2px offset |
| S25 | 视觉 | 全站无「一个亮点」：信息密度平均，阅读页的扫描原件本可以成为亮点但被工具条挤到 60% 宽度 | `audit-material-reader.png` | 阅读页给原件更大宽度与更安静的工具栏（见页面评审） |

---

### A.4 设计系统体检数据与目标值

| 指标 | 现状 | 目标 | 依据 |
| --- | --- | --- | --- |
| CSS 规模 | 17 文件 / 264 KB / 8,884 条声明 | 先收敛 token，不拆文件 | 扫描 |
| 颜色 | 431 个唯一色值；266 tone × 2 主题；14 portal 变量 | 1 条中性 ramp（10 级）+ 3 语义 + 1 强调 | A.5-1 |
| 字体族 | 7 条 font-family：`Segoe UI` 优先 3 种、`Microsoft YaHei UI` 优先 1 种、Noto Serif SC 标题 1 种、Consolas 1 种 | 界面 `Segoe UI → Microsoft YaHei UI → system-ui` 一种；标题衬线仅登录页/项目名；等宽仅数字/出处签 | 字体栈需统一顺序，否则桌面与 Web 拉丁字形不一致 |
| 字号 | 29 种；12px 最多；≤11px 160 处 | 7 级：28 / 22 / 17 / 15 / 14 / 13 / 12 | 12 为最小字号 |
| 字重 | 400/500/550/600/650 | 400/500/600 | 非标准字重在 Windows 上会回退 |
| 行高 | 20 种；1.8×64、1.7×32、1.6×25、1.9×12 | 1.5 UI / 1.6 紧凑表 / 1.75 长文 | |
| 间距 | 无 token；gap 12/8/10 为主 + 14/7/5/9/18/22 等 150+ 处 | `--space-1..8` = 4/8/12/16/20/24/32/40 | 4px 网格 |
| 控件高度 | 34/36/38/39/40/42/44/46/48 | 32 / 36 / 40 | 触控 ≥36，主操作 40 |
| 圆角 | 20 种 | 4 / 8 / 12 / 16 | 嵌套公式 |
| 阴影 | 50 处，18 种 blur，最大 118px，含彩色 | 2 级 | A.5-5 |
| 玻璃 | 7 处 backdrop-filter | 只保留弹窗遮罩 1 处 | 玻璃卡片是 slop 迹象 |
| 图标 | 15 种尺寸、默认 2px stroke | 16/18、1.5 stroke | A.5-6 |
| 动效 | 6 动画 / 3 keyframes / 5 时长 / 3 缓动 | 2 时长 / 1 ease-out / 全局 reduced-motion | A.5-8 |
| z-index | 21 个不同值（1–4000） | 6 级阶梯 | A.5-9 |
| 对比度 | `muted` 4.41 / 4.25 不达 AA；其余合格（13.05、14.04、5.0、7.55、8.75、13.57） | 全部 ≥4.5（正文）；大字 ≥3 | A.5-7 |
| 可达性 | 313 个 aria-label；skip-link；`role=tablist` + 方向键仅部分实现 | 保持并补齐 | 页面评审 |

---

### A.5 系统级修改方案（按执行顺序）

#### 1）P0：一套语义 token，三套视觉身份收口
保留 `src/workbench/shell.css` 的 9 个语义变量作为唯一命名，扩展为：

```css
:root {
  --bg:#f7f5f0; --sidebar:#fbf9f5; --surface:#fff; --surface-2:#f3f0ea;
  --line:#e5e0d7; --text:#26241f; --text-2:#5f5a51; --text-3:#8a8479;
  --accent:#157d70; --accent-ink:#fff; --accent-soft:#eaf3f0;
  --danger:#a8453a; --success:#2e7d5b; --warning:#8a6a2f;
  --radius-1:4px; --radius-2:8px; --radius-3:12px; --radius-4:16px;
  --space-1:4px; … --space-8:40px;
  --font-ui:'Segoe UI','Microsoft YaHei UI','Noto Sans SC',system-ui,sans-serif;
  --font-display:'Noto Serif SC','Source Han Serif SC',serif;
  --font-mono:'Consolas','SFMono-Regular',monospace;
}
[data-theme=dark] { --bg:#191a18; --sidebar:#1f201d; --surface:#242521; --surface-2:#2b2c28;
  --line:#3a3b36; --text:#e8e6df; --text-2:#b3afa4; --text-3:#8b877d;
  --accent:#7fcbb9; --accent-ink:#10241f; --accent-soft:#24352f; }
```

- `palette.css` 的两套 tone 映射保留一个版本（约 40 行）做 codemod 过渡，把 `theme.css` 里 436 处 `var(--tone-*)` 按语义归并：`--tone-111318/15171d/0e1015 → --bg`；`--tone-20232c/232631/191c23 → --surface`；`--tone-333743/2b2e38/303541 → --line`；`--tone-e8e9ee → --text`；`--tone-a6aab7/b1b6c4/b9bdc9 → --text-2`；`--tone-c7bdff/b7acff/d0bded → --accent`。完成后删除 tone 表与旧 `theme.css` 中重复定义。
- `modern.css` 的 `--portal-*` 改为引用同一语义变量，只覆盖「入口页」需要的值，不再维护第二套色板。
- 桌面壳（`App.tsx` 无 portalUser 时）与门户工作台（`portal-workspace`）最终呈现同一套中性色与强调色。

#### 2）P0：字号阶梯与行高
```css
--text-xs:12px; --text-sm:13px; --text-base:14px; --text-md:15px;
--text-lg:17px; --text-xl:22px; --text-2xl:28px;
--leading-ui:1.5; --leading-tight:1.35; --leading-prose:1.75;
```
- 全站 10px/9px/8px（约 40 处）直接抬到 12px；11px 中除表格元信息外抬到 12px。
- 正文 14px（当前 body 14px 但大量组件覆盖成 12/13px）；表格 13px；表头 12px。
- 行高：UI 1.5；表格 1.6；帮助/长文 1.75（现在 1.8/1.9 的 76 处统一）。
- 字重清零非标准值：550→500，650→600；H1 统一 600。

#### 3）P0：对比度
- `--portal-muted`（`modern.css:3`）改为 `#5b6a78`；暗色 `#9caeba` 保持（7.55:1）。
- 「薄荷底 + muted 文字」的组合（`--portal-soft` 徽标/提示条）改用 `--text-2` 或把底改为 `--surface`。
- 加一条 CI 检查：从截图抽 `getComputedStyle`，对 12px 以下文本断言 ≥4.5:1。

#### 4）P1：4px 间距与控件高度
- 新增 `--space-*`；把 10/14/18/22/7/9/5 等值替换到最近阶梯；宁取整不保留个性。
- 控件高度 32（图标/小）+36（常规）+40（主操作）；导航行高 32–36，gap 2–4。
- 卡片内边距 20/24；面板 16；页面边距 24（移动）/32（桌面）。

#### 5）P1：阴影、圆角与玻璃
- 卡片：无阴影，`1px solid var(--line)`；hover 用 `--surface-2`。
- 浮层（菜单/tooltip）：`0 8px 24px rgba(24,32,36,.10)`。
- 弹窗：`0 24px 60px rgba(24,32,36,.18)`，遮罩 `rgba(16,24,28,.42)`，去掉 backdrop blur（或只留 2px）。
- 圆角：按钮 8、输入 8、卡片 12、弹窗 16、胶囊 999 只用于标签；嵌套内圆角 = 外圆角 − padding。

#### 6）P1：图标规范
- 只允许 `size={16}`（行内/按钮）与 `size={18}`（导航/标题）；`strokeWidth={1.5}`。
- 图标栏统一 16px 盒 + 8px 间距；装饰性图标 `aria-hidden`，功能图标必须有 `aria-label`。
- 删除 `→` 字符渲染，改用 `ArrowRight`（14 处）。
- 状态点 6px，语义色只用于状态点/错误条。

#### 7）P1：分层
- z-index 阶梯：10 内容 / 40 侧栏 / 60 遮罩 / 80 抽屉 / 100 弹窗 / 120 toast；`systemFeedback.css:1` 的 4000 改为 120。
- 删除 5 处 `!important` 颜色覆盖（`portal.css:21,22` 等），用变量优先级解决。

#### 8）P2：动效
- 两个时长：`.12s` 反馈（hover/press）、`.2s` 进入；统一 `cubic-bezier(.2,.7,.3,1)`。
- 只过渡 `color/background-color/border-color/opacity/transform`；`overlays.css:14` 进度条改 `transform:scaleX()`，`overlays.css:41` skip-link 改 `transform`。
- 全局：`@media (prefers-reduced-motion: reduce){ *,*::before,*::after{animation-duration:.01ms!important;animation-iteration-count:1!important;transition-duration:.01ms!important} }`（当前只有门户禁用）。

#### 9）P1：术语与文案
- 术语表（首版）：文献（库）/ 材料（项目内）/ 原文 / 录文 / 出处 / 工作单元 / 研究工作 / 研究成果 / 研究判断 / 投稿。
- 空状态：「还没有…」+ 一句解释 + 一个主操作（当前常有页头与空态两个重复按钮）。
- 登录页：问候标题去掉；默认账号提示只在首次初始化时出现。
- 每条帮助文字 ≤24 字，长说明收进 `PageHeading` 的 `?` 弹层（组件已有：`page-heading-help`）。

---

### A.6 方向落地后的「一屏一句话」

| 页面 | 一句话目标 | 当前差距 |
| --- | --- | --- |
| 登录 | 让人相信这是研究者的工作台，而不是通用 SaaS | 青绿+薄荷+居中表单偏模板；左栏文案已具备产品感 |
| 项目概览 | 今天该做什么：选题→选材→建单元 | 四步卡片平均铺开，缺少「下一步」主次 |
| 材料阅读 | 原件与录文一眼对照、随时摘录 | 原件区偏窄，工具条占两行，摘录入口不显眼 |
| 研究编辑器 | 证据→判断→成稿的链条可见 | 左右分栏是空的，出处链未呈现 |
| 文献库/检索 | 找到一篇文献并打开它 | 目录与检索切换清楚，但信息层级平均 |
| 点校 | 哪里失败、下一步点哪里 | 空态/批次页留白过大，状态信息密度低 |
| 投稿 | 这篇投哪里、到哪一步了 | 表单式弹窗，缺少「目标-状态-截止」一行摘要 |
| 后台概览 | 服务是否健康、谁需要我处理 | 数据卡与列表齐备，但缺异常优先的排序 |

---

## 2. 门户与管理后台页面评审

> 覆盖登录（用户端/管理端）、访问受限、登录过期、首次改密、账号与安全、管理后台壳层与 10 个分区、后台弹窗与反馈、后台移动端与超宽屏。问题编号 PA01–PA31，路径以 `/root/wenjin` 为根。

评审对象：WSL `/root/wenjin`（部署实例 `http://127.0.0.1:8765`），只读静态评审，未改代码、未起浏览器。
证据：代码行号 + 截图（`.local/unslop-shots/fixture/` 与 `live-*.png`）。有截图的页面：登录（桌面/移动/用户端/管理端）、用户管理、模型服务、期刊库（中/英/深色/移动/超宽）、系统文献库（空态/有数据/移动/超宽）、检索配置。无截图：运行概览、工作区运行、操作记录、访问受限页、首次改密页（结论来自代码）。系统级 token/对比度方案见 `00-system.md §A.4`，组件内部细节见 `03-knowledge.md §5–6`、`04-submissions-feedback.md §3`，本文不重复展开。

### 0. 覆盖页面清单

| 页面 | 入口/路由 | 主要文件（行号） | 首屏任务是否清晰 |
| --- | --- | --- | --- |
| 用户端登录 | `/app/` | `src/portal/WebEntry.tsx:54-57` | 清晰：表单与主按钮首屏可见；但 h1 是营销语 |
| 管理端登录 | `/admin/` | `src/portal/WebEntry.tsx:54-57` | 清晰；但未登录即公开默认口令（PA01） |
| 连接失败/重试 | 任何入口首次加载 | `src/portal/WebEntry.tsx:14,18,20` | 尚可：有错误文案+重试，但无品牌、用旧皮肤 |
| 登录过期提示 | 任意页面 401 后 | `src/portal/WebEntry.tsx:29,49` | 清晰：回到登录页 + `role=status` 提示 |
| 访问受限页 | 研究用户访问 `/admin/` | `src/portal/WebEntry.tsx:45` | 部分：没说明"为什么不行" |
| 首次改密 | 登录后 `password_change_required` | `src/portal/WebEntry.tsx:44`、`AccountSettings.tsx:20` | 清晰：单卡单目标 |
| 管理后台壳层 | `/admin/` | `src/portal/AdminConsole.tsx:64` | 清晰：3 组 10 项导航 + 分区标题 |
| 运行概览 | `/admin/` 默认分区 | `AdminConsole.tsx:66` | 部分：统计卡等权，异常不突出（PA14） |
| 系统文献库 | 侧栏「系统文献库」 | `AdminConsole.tsx:65`、`SharedLibrary.tsx:12-13`、`KnowledgeWorkbench.tsx:88` | 清晰：目录/检索双视图 + 导入；双主按钮打架 |
| 期刊库管理 | 侧栏「期刊库管理」 | `AdminConsole.tsx:65`、`SubmissionWorkbench.tsx:68-84` | 清晰：目录计数 + 表格；后台无法批量导入 |
| 检索配置 | 侧栏「检索配置」 | `AdminConsole.tsx:65`、`RetrievalOptionsPanel.tsx:46-48` | 部分：7 个 tab 同排，流程说明被收起 |
| 助手与 Skill | 侧栏「助手与 Skill」 | `AdminConsole.tsx:65`、`AssistantManagement.tsx:40` | 中等：功能可见，术语三套叫法 |
| 用户管理 | 侧栏「用户管理」 | `AdminConsole.tsx:67` | 清晰：建立用户 primary + 搜索 |
| 工作区运行 | 侧栏「工作区运行」 | `AdminConsole.tsx:68` | 不清晰：唯一动作对在用账号全禁用（PA22） |
| 模型服务 | 侧栏「模型服务」 | `AdminConsole.tsx:80-84` | 清晰：单表单；错误态与 Tab 顺序有问题 |
| 操作记录 | 侧栏「操作记录」 | `AdminConsole.tsx:70,77` | 部分：能读，不可查（PA25） |
| 账号与安全 | 侧栏「账号与安全」 | `AdminConsole.tsx:71`、`AccountSettings.tsx:20` | 清晰 |
| 后台弹窗 | 建立/编辑/改密/期刊编辑 | `AdminConsole.tsx:41-61,72`、`Pages.tsx:20-38` | 清晰：标题+表单+取消/保存 |

### 1. 登录页（用户端 / 管理端）

做得对：左右分栏、宋体大标语 + 极淡同心圆线是明确的「文献—纸页」方向，不是模板；表单只有一个 primary，输入框 44px 高、focus 有 2px 青绿环（`portal.css:18-21`）；错误 `role=alert`、过期 `role=status`；全站无 emoji、无渐变、无发光。

#### PA01 · P0 · 归类：文案（安全）
- 位置：`/root/wenjin/src/portal/WebEntry.tsx:57`
- 现象：管理端登录页未登录状态直接印出「首次默认账号：admin / admin123。登录后可在“账号与安全”修改密码。」。只要部署仍保留启动默认口令（`/root/wenjin/backend/portal_accounts.py:14-15`、`/root/wenjin/backend/launcher.py:116-117`），任何访客可接管后台；改过密码后这行又是过期错误信息，且 8 位口令与界面「至少 12 个字符」互相矛盾。
- 改法：从 `Login` 删除该行（或仅当 `/api/session` 返回 `default_password:true` 时，以一次性提示 + 强制改密引导出现）；bootstrap 管理员改 `require_change=True`；README 的默认口令提示同步删除或改为首次启动时终端输出一次。

#### PA02 · P1 · 归类：文案/字体层级
- 位置：`/root/wenjin/src/portal/WebEntry.tsx:57`
- 现象：页面唯一 `<h1>` 是营销语「循文献之线，抵达研究深处。」，真正的任务标题却是 `<h2>`「欢迎回到问津 / 登录管理后台」；用户端标题还是问候式（unslop 明令：标题写页面/上下文，不写 Welcome back）；"登录后，继续你的文献与研究工作。" 与左栏 3 条卖点重复表达同一件事。
- 改法：`h1` 改为「登录问津」/「登录管理后台」，`h2` 降为一句说明；营销标语保留在左栏，用 `<blockquote>` 或 28px 展示体；右侧说明压成一句「使用管理员提供的账号登录」。同时解决读屏用户"页面主标题是广告"的语义问题。

#### PA03 · P1 · 归类：可达性/细节
- 位置：`/root/wenjin/src/portal/WebEntry.tsx:57`
- 现象：密码输入没有「显示密码」切换、没有 Caps Lock 提示；管理员口令是 20 位随机串（部署实例即如此），盲输一次一个字符错误就失败，只能反复试。
- 改法：密码字段右侧加 `button.portal-icon-button`（Eye/EyeOff，16px，`aria-label="显示密码"`，`aria-pressed`），输入区加 `:has(input:focus)` 不变；CapsLock 开启时在 label 行尾显示 12px muted「大写锁定已开启」。

#### PA04 · P2 · 归类：文案/颜色一致性
- 位置：`/root/wenjin/src/portal/WebEntry.tsx:57`、`14,20`；`portal.css:1,20-22`；`modern.css:2-4,21`
- 现象：右侧叠了 4 行辅助文本：默认口令提示（11px）、"没有账号或忘记密码"（11px）、分隔线后的"前往用户工作区"（12px）、底部免责声明（10px）；占位符与 label 重复（label「用户名」+ placeholder「输入用户名」）。另外加载/连接失败页不在 `.portal-root` 内，仍用 `portal.css:1` 的旧 token（bg `#f5f6f3`、accent `#254e3d`），而登录页已被 `modern.css:2-4` 改为（`#f4f7f9`、`#157d70`）：深色下 `#171c19` vs `#101c25` 明显两套皮肤，重试按钮也是旧深绿。
- 改法：辅助文字收成 2 行（「忘记密码？请联系管理员」+「管理员入口」合并成一行 quiet link），删除独立分隔行或与入口切换合并；placeholder 置空或写「例如 admin」。把 `.portal-loading` 加入 `modern.css:2` 的选择器列表（或给节点加 `portal-root` 类），使加载/错误页按钮与登录按钮同色、同 40px 高。

#### PA05 · P2 · 归类：布局/响应式
- 位置：`portal.css:9-15,41`；截图 `.local/unslop-shots/live-login-user-mobile.png`
- 现象：≤760px 时品牌故事区仍占 325px（h1 32px + 两行副文案 + 分隔线），登录表单从 y≈396 开始；900px 高能放下，但 667px 高的手机（主流旧机型/横屏）登录按钮已接近折线，卖点与页脚只是被 `display:none` 隐藏、故事区并未收缩。
- 改法：移动端故事区压到 144–168px（品牌行 + 单行标语，隐藏副文案与分隔线），或把品牌提到表单卡顶部、整块故事区在 ≤560px 隐藏；以 375×667 验证登录主按钮在首屏内。

### 2. 访问受限、登录过期、首次改密、账号与安全

做得对：四个状态都复用了品牌/账户卡，单列居中、目标唯一；过期与失败信息用 `role=status` / `role=alert` 区分；受限页给出了两条明确出路（进入研究工作区、切换账号）。

#### PA06 · P2 · 归类：文案/操作路径
- 位置：`/root/wenjin/src/portal/WebEntry.tsx:45`
- 现象：「这里是管理员工作区」之后只有「当前账号可以进入自己的研究工作区。」——没说明"当前账号是研究用户、无管理权限"，也没给"返回登录/联系管理员"；两个按钮之外没有第三条路。
- 改法：正文改成两句：`当前账号 @{username} 是研究用户，没有管理权限。需要管理后台请联系管理员。`；次要动作补「返回登录」，主按钮保留「进入研究工作区」。

#### PA07 · P2 · 归类：可达性/一致性（首次改密页）
- 位置：`WebEntry.tsx:44`、`AccountSettings.tsx:20`；`modern.css:19-33`；`theme.css:10`、`palette.css:3`
- 现象：`.portal-security-page` / `.portal-access-denied` 在 `modern.css:2` 的 token 作用域里，却不在 `:19` 的 `:focus-visible` 列表里：按钮焦点环回落到旧主题（深色为紫 `#c9beff`），输入框由 `portal.css:19` 得到青绿环——同一页两种焦点色；控件也走旧规格（圆角 7px、12px 字、39px 高）而不是 8px/13px/40px。
- 改法：把 `modern.css:18-33` 的 `:is(.portal-root,.portal-workspace,.portal-modal)` 统一补上 `.portal-security-page,.portal-access-denied,.portal-loading`；或直接给这两个页面节点加 `portal-root` 类，一处收口。

#### PA08 · P2 · 归类：细节/反馈（账号与安全）
- 位置：`AccountSettings.tsx:15-16,20`；`portal.css:38`；`overlays.css:185-186`
- 现象：改密成功后 `setUpdated(true)` 紧接着 `onLogout()`，用户只看到「已安全退出，请重新登录。」，成功提示永远不可见；表单无显示密码、两次不一致只在提交后弹 alert；嵌入后台时 900px 卡片里表单固定 440px 左对齐，右侧约 400px 空；`overlays.css:185-186` 还隐藏了卡片内标题，卡片缺标题。
- 改法：登出前先显示 1.2s `role=status`「密码已更新，正在退出…」再登出；密码字段补显示切换；账号卡改为 720px 单列，或右侧补「上次登录 / 登录设备」信息填满；给嵌入卡补回 `<h2>账号与安全</h2>`（或删掉那条 `display:none`）。

### 3. 管理后台壳层与导航

做得对：侧栏 3 组 10 项 + 用户数徽标 + 底部「进入研究工作区」；`aria-current=page`、Escape 关抽屉、抽屉隐藏态用 `visibility:hidden` 防 Tab（`modern.css:351-352`）；顶栏主题切换有动态 `aria-label`；全局 2px focus-visible 环（`modern.css:19`）。

#### PA09 · P1 · 归类：颜色（深色主题）
- 位置：`overlays.css:151-177`；`modern.css:37-52`（已被覆盖的规则）；`modern.css:13-17`；截图 `.local/unslop-shots/fixture/admin-journals-dark.png`
- 现象：浅色侧栏回到 `--portal-surface`（#fff），深色主题侧栏硬编码 `#142e31` 墨绿，而页面背景 `#101c25`、卡片 `#172832` 是蓝灰——同一屏两种色相，侧栏与顶栏之间能看到色缝；侧栏文字另有 13 个硬编码 hex（#b9ccce/#82a5a6/#25494a/#33595a…），完全不随 token 走。
- 改法：新增 `--admin-sidebar-bg/--admin-sidebar-text/--admin-sidebar-active`，浅色=surface/muted/soft，深色= #172832/#9caeba/#1c3937，`overlays.css` 两段 26 行并成一段变量映射；删除 `modern.css:37-52` 与 `overlays.css:165-177` 的重复硬编码。

#### PA10 · P1 · 归类：字体/颜色（后台字号与对比度）
- 位置：`portal.css:1`；`modern.css:2-4,85-95`；`overlays.css:199,219`；`desktop-density.css:4`
- 现象：后台可见文字大量 11–12px：表头/表格 12、审计表头与概览审计行 11、页脚 11、状态 11、角色徽标 11，侧栏小字 8px、登录脚注 10px；`portal.css / modern.css / overlays.css / desktop-density.css` 共出现 22 种 font-size（8–33px），15/16/18/20/21/22/23/24/25/26/27/28 连续堆叠。实测对比度：`--portal-muted #657583` 在 `--portal-bg #f4f7f9` 上 4.41:1、在 `--portal-soft #eaf5f0` 上 4.25:1；状态「准备中/正在停止」`#a27635` 在 soft 上 3.63:1；强调色 `#157d70` 在 soft 上 4.48:1——均低于 4.5:1。
- 改法：后台正文/表格统一 13px（表头 12px 但改 secondary 色）；`--portal-muted` → `#566573`（bg 5.57 / soft 5.37 / 白 5.99），warn → `#8a6832`（soft 4.58）或 `#7a5a24`（5.68），accent 作文字时用深一档 `#0f6f63`（soft 5.42）；侧栏副标/页脚不低于 11px。字号阶梯的系统方案见 `00-system.md §A.4`，本节只给门户落点。

#### PA11 · P2 · 归类：细节/维护性（页头规格死代码）
- 位置：`portal.css:12,29,35,41`；`modern.css:62-68,284-290`；`overlays.css:95-100`；`desktop-density.css:2-15`；`PageHeading.tsx:32-35`
- 现象：`.portal-page-heading`、`.portal-heading-copy`、`.portal-heading-icon`、`.portal-eyebrow`、`.portal-audit-list`、`.portal-audit-icon` 在整个 `src` 内没有任何 JSX 引用（全仓 grep 只命中 CSS）。真正渲染的是 `.page-heading`（`PageHeading.tsx:32`），所以后台页头实际是 22px/650 + 说明收进 ⓘ 浮层（`desktop-density.css:4,11`），而三个 CSS 层里"42–54px 图标 + 28px 标题 + eyebrow + 可见描述"的规格从未生效，约 30 行规则空转。
- 改法：二选一收口——(a) 删掉上述死规则，页头只保留 `desktop-density.css:2-15` 一套；(b) 若确实要页头图标，改 `PageHeading` 支持 icon 并删掉 22px 覆盖。`:35`、`:94` 的 audit-list 规则一并删除。

#### PA12 · P2 · 归类：图标/间距（导航密度）
- 位置：`AdminConsole.tsx:5,32,64,72,76`；`modern.css:24,44`；截图 `fixture/admin-users.png`
- 现象：10 个导航项里「助手与 Skill」和「模型服务」共用 `Settings2`；图标尺寸 15–40px 混用（后台 15–28：nav 18、菜单 21、关闭 20、空态 28；登录/受限页 17–40：LogIn 17、ShieldCheck 40），lucide 全站默认 stroke 2 而非规范 1.5；nav 行高 45px（unslop 建议 32–36），10 项 + 3 组标题合计约 840px，900px 高窗口里侧栏必须滚动；组标题上 25/下 8 与行内 12/14 也不成比例。
- 改法：模型服务换 `Cpu`、检索配置换 `SlidersHorizontal`、助手保留 `Settings2`（`AdminConsole.tsx:32`）；图标统一 16/18 + `strokeWidth={1.5}`（做一个 `<Icon>` 包装）；nav 行高 36–38、gap 2–4，组标题 24/8；`.portal-icon-button` 桌面 32×32、移动 44×44。

#### PA13 · P2 · 归类：文案/可达性（术语与顶栏）
- 位置：`AdminConsole.tsx:32,64,68`；`WebEntry.tsx:45,57`
- 现象：同一产品四套词：「工作空间」（侧栏分组）/「工作区」（每用户运行容器）/「研究工作区」（用户端入口）/「独立工作区」；顶栏永远写「管理后台」，与页头标题重复且无面包屑；顶栏「管理员」徽章 + 头像 + 姓名三个身份元素并排；管理端缺工作台已有的「跳到主内容」skip link（`overlays.css:41`、`App.tsx:401`）。
- 改法：侧栏分组改「内容与配置」，保留「工作区」只指运行容器；顶栏去掉「管理后台」文字，改面包屑 `管理后台 / 用户管理`；角色信息移到账号页，顶栏只留头像+姓名；在 `AdminConsole.tsx:64` 最前面加 `<a className="skip-link" href="#admin-content">跳到主要内容</a>`，给 `.portal-admin-content` 补 `id="admin-content"`。

### 4. 运行概览

做得对：4 项统计 + 工作区状态 + 最近操作的两栏结构清楚；数字用 `tabular-nums`（`modern.css:72`）；卡片标题/说明/动作层级统一；`align-items:start` 避免了等高拉伸（`overlays.css:180-181`）。

#### PA14 · P1 · 归类：布局/信息层级
- 位置：`AdminConsole.tsx:66`
- 现象：4 张统计卡等宽等高，最需要处理的「需要检查」（`failed_workspaces`）排在最后、同权重且不可点击；「工作区状态」卡 `users.slice(0,6)` 未按状态排序，失败的工作区可能根本不在前 6 条；「最近管理操作」卡没有「查看全部」，而左侧卡有。
- 改法：「需要检查」卡用 `--portal-error` 色数字并在卡片点击时 `setSection('workspaces')`；工作区列表按 failed → offline → starting → stopped → ready 排序再 `slice`；两张卡标题右侧都放「查看全部」quiet link（对齐 `AdminConsole.tsx:66` 左侧卡的 `portal-link-button`）。

#### PA15 · P1 · 归类：文案/错误态
- 位置：`AdminConsole.tsx:66,67`
- 现象：加载中 `overview=null` 时统计卡显示「—」和「— 个账号已启用」，把"未知"混成"0/无"；接口失败时 `loading` 已置 false，用户表空态显示「没有匹配的账号。」而不是"读取失败"，与页头红条并存；概览「工作区状态」空表在请求失败时也会显示「还没有研究用户，可先建立账号。」
- 改法：统计卡 detail 用三态渲染（`读取中…`/`读取失败`/实际句）；表格空态改 `loading?'正在读取…':error?'读取失败，请刷新':'没有匹配的账号。'`；`error` 存在时不渲染"还没有研究用户"引导。

### 5. 系统文献库 / 期刊库管理 / 检索配置 / 助手与 Skill

做得对：系统文献库空态有图标 + 标题 + 说明 + CTA（`DocumentBrowser.tsx:61`）；期刊库表格是后台最专业的一屏——sticky 表头、分页、命中数、来源浮层（`modern.css:156-171`，截图 `fixture/admin-journals.png`）；检索配置 7 个子页职责划分清楚；助手配置有草稿/版本/测试/导入。

#### PA16 · P2 · 归类：布局/层级（系统文献库入口）
- 位置：`KnowledgeWorkbench.tsx:88`、`DocumentBrowser.tsx:61`、`SharedLibrary.tsx:11`；截图 `fixture/admin-library-empty.png`
- 现象：工具栏「导入文献」是 primary，空态里又有 primary「导入第一份文献」，同屏两个同权重主按钮；工具栏 5 个动作（文献加工与向量化 / 编辑文献书目 / 处理任务 / 导入文献 / 刷新）用 4 种样式，其中「文献加工与向量化」其实是跳去「检索配置」分区（`SharedLibrary.tsx:11` → `AdminConsole.tsx:65`），「编辑文献书目」是页内模式切换，两者与页内动作混排。
- 改法：空态只保留主 CTA（工具栏导入降为 secondary），或空态 CTA 改 secondary 并复用同一导入弹窗；「文献加工与向量化 / 编辑文献书目」移出工具栏到页头右侧 quiet 链（侧栏已有入口，可删）；刷新统一 icon-button。工具栏细节见 `03-knowledge.md KB19`。

#### PA17 · P1 · 归类：操作路径（期刊库管理）
- 位置：`SubmissionWorkbench.tsx:66,79,83`
- 现象：`catalogOnly` 下 CSV 导入按钮被 `!catalogOnly` 条件隐藏（`:66`），但空态仍写「…或导入目录」（`:83`）、「来源与版本」仍列导入数据集（`:79`）；管理员面对 935 种中文刊 / 23,113 种英文刊，只能在表格里逐条「新增期刊」，无法批量维护。
- 改法：`catalogOnly` 下保留「导入目录 CSV」按钮（复用 `:60` 的 `pickCsv` 与 `:97` 的预览/指纹去重弹窗），按钮文案写「导入目录 CSV」；若产品确实不开放后台导入，则删掉 `:83` 的"导入目录"并把空态改成「新增第一本期刊」，不要留下做不到的指引。

#### PA18 · P1 · 归类：布局/响应式（期刊库移动端）
- 位置：`modern.css:157-171`（`min-width:730px`、操作列在最右）；截图 `fixture/admin-journals-mobile.png`
- 现象：430px 宽下目录表只露出「期刊名称/学科门类」，`min-width:730px` 让「收录等级/相关链接/操作（编辑）」全部在横向滚动区外，且没有滚动提示或卡片降级——手机上无法完成一次期刊编辑。
- 改法：<760px 改列表卡片（第一行刊名 2 行截断，第二行学科 + 等级徽标，行尾 44px 编辑 icon-button）；最小改动是把操作列 sticky：`.catalog-table th:last-child,.catalog-table td:last-child{position:sticky;right:0;background:var(--portal-surface)}`，并在表尾加「← 左右滑动查看更多列」11px 提示。

#### PA19 · P2 · 归类：文案/信息密度（期刊库命名与滚动）
- 位置：`AdminConsole.tsx:32,64`、`SubmissionWorkbench.tsx:70,74,78`、`modern.css:156`
- 现象：同一功能四个名字：「期刊库管理」（导航/页头）/「中文 C 刊目录」「英文学术期刊」（页头指标）/「中文C刊库」「英文期刊库」（tab）；计数出现三处（页头指标、筛选结果「已展示 80 种」、分页「库内 23,113 种」）；`max-height:60vh` 让表格内部滚动条与页面滚动条叠加（1480×1000 截图同时可见两条）。
- 改法：统一「中文期刊目录 / 英文期刊目录」，tab 与指标同词；页头只留总数，「命中/已展示」留在工具栏，分页处删「库内」；表格改 `max-height:calc(100dvh - 260px)` 或在页面级滚动，避免双滚动。

#### PA20 · P2 · 归类：图标/层级（检索配置与助手）
- 位置：`RetrievalOptionsPanel.tsx:47-48`、`KnowledgeWorkbench.tsx:99`、`AdminConsole.tsx:64`、`AssistantManagement.tsx:40`；截图 `fixture/audit-rag-processing.png`
- 现象：检索配置 7 个 tab 一行，三个用同一个 `Database` 图标，「查询处理」用 `Sparkles`（AI 星芒，unslop 明令去除）；页头「加工流程：文档解析→…→重排与上下文」以及「加工流程·5 步」在管理端都被收起（页头描述进 ⓘ；`RetrievalOptionsPanel.tsx:47` 因 `serverManaged` 隐藏），第一次进来只能猜流程。助手页三处叫法不一：导航「助手与 Skill」/ 页头叙述「研究技能、智能体」/ 卡片「助手配置」。
- 改法：tab 图标按语义区分（文献加工=Inbox、解析与切分=Scissors、向量模型=Boxes、查询处理=Route、检索参数=SlidersHorizontal、检索测试=FlaskConical、评测记录=ClipboardCheck），去掉 Sparkles；「5 步流程」作为 tab 下默认可见的 12px 一行说明，不进浮层；术语统一：「研究助手」为导航，页内统一「技能 / 智能体」，删 "Skill"。

### 6. 用户管理 / 工作区运行 / 模型服务 / 操作记录

做得对：用户表信息密度好（头像 + 显示名 + @账号 + 当前账号标记 + 角色/账号状态/工作区分列）；状态用「点 + 文字」双编码；模型服务的「密钥由服务器管理，页面不读取不显示」说明诚实；审计表紧凑、时间用 `tabular-nums`（`overlays.css:200`）。

#### PA21 · P1 · 归类：细节/操作安全（用户管理）
- 位置：`AdminConsole.tsx:72`；对照 `AccountSettings.tsx:20`
- 现象：建立用户/重置密码对话框只有一个「临时密码」输入：无确认、无显示、无生成、无复制，也没有「至少 12 位」提示（服务端要求 12 位，同字段在 `AccountSettings.tsx:20` 有该提示）；管理员手输 12 位随机串错一个字符，用户即无法登录且只能再重置。另外编辑自己时「账号角色」下拉仍可改（只有"启用账号"复选框被禁用），存在自降权入口。
- 改法：临时密码加确认框 + 显示切换 +「生成随机密码」（`crypto.getRandomValues` 16 位），保存后弹一次「复制临时密码」；helper 写「至少 12 个字符，用户首次登录需修改」；`role` 下拉 `disabled={dialog.user.id===user.id}`；用户表日期改 `toLocaleDateString('zh-CN')` 或相对时间并右对齐 + `tabular-nums`。

#### PA22 · P1 · 归类：操作路径（工作区运行）
- 位置：`AdminConsole.tsx:68`
- 现象：页面唯一动作「停止工作区」的禁用条件是 `busy || !!item.enabled || !['ready','starting','offline','failed'].includes(state)`——账号启用时对每一行都禁用；原因只写在 `title` 悬浮提示（"停用账号后可停止工作区"），触屏/键盘看不到。管理员要停一个异常工作区，必须先跳到用户管理停用账号再回来。
- 改法：方案 A：允许直接停工作区 + `confirmDialog`（"停用后该用户正在运行的任务会中断"），账号启用与工作区运行解耦；方案 B：保留限制但把禁用原因改成行内 11px 说明 + 「前往账号设置」链接。另补空态与「最近刷新时间」，表格行高从 70px 收到 56px。

#### PA23 · P1 · 归类：错误态（模型服务）
- 位置：`AdminConsole.tsx:84`
- 现象：首次读取失败时 `value=null`，页面只渲染 `<p>{error||'正在读取配置…'}</p>`：错误没颜色、没重试、没区分加载与失败；只能切走再切回以重新挂载；成功后加载中的文字也不带 spinner。
- 改法：拆三态——加载 `LoaderCircle className="spin"` + 「正在读取配置…」；失败 `.portal-alert` + 「重新读取」按钮；成功表单。把加载逻辑抽成 `load()` 供重试复用。

#### PA24 · P2 · 归类：可达性/细节（模型服务表单）
- 位置：`AdminConsole.tsx:84`；`overlays.css:187-194`
- 现象：桌面用 CSS 把「服务名称|模型名称」并排、把 DOM 第二字段「模型服务地址」排到第二行，视觉顺序与 DOM/读屏顺序不一致（Tab：服务名称 → 模型服务地址 → 模型名称，光标在页面上跳回上一行）；两个 token 数字输入空值无 placeholder/默认值/单位说明；卡头是 `<span>连接信息与容量限制</span>` + 装饰 `Settings2`，没有 `h2`；保存按钮常亮无未保存态，notice 只占左半列（`overlays.css:191-194` 只把 form-grid/facts/actions 拉满）。
- 改法：删掉 `overlays.css:188-190` 的错位网格，改两列自然流（服务名称|模型名称同行、地址整行）；token 输入加「如 128000」「如 4096」与 11px 说明；卡头改 `<h2>` 并删右侧装饰图标；保存按钮 `disabled={!dirty}` + 顶部「有未保存的修改」；notice/error 加 `grid-column:1/-1`。

#### PA25 · P1 · 归类：信息架构（操作记录）
- 位置：`AdminConsole.tsx:70,77`、`AdminConsole.tsx:17`；`overlays.css:197-202`
- 现象：一屏 100 条记录只有时间/操作者/动作/对象四列；无搜索、无动作筛选、无时间范围、无分页；`details` 字段只放在 `<tr title>` 悬浮提示里（触屏、键盘不可达）；动作名只有 8 类粗粒度映射（`:17`），未覆盖的 action 直接显示原始英文 id，同类操作看起来完全一样。
- 改法：工具栏加搜索 + 动作 select + 时间范围（复用 `.portal-search`、`.portal-secondary` 样式）；「对象」后加「详情」列（截断 340px，`title` 保留全文），或行改 `<details>` 展开；分页 50/页；给「维护系统文献库」补 `details` 摘要。

### 7. 后台弹窗与反馈状态

做得对：后台对话框已具备初始焦点、Tab 陷阱、Escape 关闭、`aria-modal`（比 11-01 评审时前进了一步）；全局 2px focus-visible 环；重置密码先开表单而不是直接执行；错误文案体系完整（`transport.ts:16-19`、`errors.ts:13-19`）。

#### PA26 · P1 · 归类：可达性/一致性（对话框焦点）
- 位置：`AdminConsole.tsx:41-61`；对照 `Pages.tsx:24-37`
- 现象：自带 `useEffect` 焦点逻辑比产品 Modal 缺三件事：关闭后不归还焦点（`previous.focus()`）；依赖数组 `[dialog,busy]` 使保存期间 busy 变化时重新聚焦第一个输入，正在输入的焦点被抢走；无 `aria-describedby`。产品已有更完整实现（`Pages.tsx:24-37` 含 previous 还原与 trap），后台却重复造了一套。
- 改法：后台对话框直接复用 workbench `Modal`（`Pages.tsx:20-38`），或抽 `useDialogFocus(dialog)`；busy 不进依赖，仅 dialog 变化时聚焦；关闭时 `previous?.focus()`。

#### PA27 · P2 · 归类：反馈/状态残留
- 位置：`AdminConsole.tsx:35,64,72`、`WebEntry.tsx:49`、`transport.ts:64`
- 现象：反馈分散三处——页头下 `.portal-alert/.portal-notice`、对话框内 error、全局 toast（请求失败时 `reportFailure` 触发）；`navigate()` 只清 notice 不清 error，上一分区的错误会跟到下一分区；成功 notice 永不自动消失（截图 `fixture/admin-users.png` 的"账号信息已保存。"后续操作仍在）；会话过期时 AdminConsole 只是被 `display:none` 包住，同账号重登后旧的 section/对话框状态会复现。
- 改法：统一为「局部错误进上下文 + 全局只做非阻断 toast」；`navigate` 里补 `setError('')`；notice 4s 自动消失或下次交互清除；会话过期时用 `key` 卸载内容（如 `key={connected?user.id:'signed-out'}`），不要只 display:none。

#### PA28 · P2 · 归类：细节（破坏性操作）
- 位置：`AdminConsole.tsx:62,63,72`
- 现象：停用账号是「编辑用户」弹窗里勾 checkbox 后点保存（无确认、无"将立即撤销登录"提示）；「停止工作区」直接 POST；「重置密码」会立即让原登录失效，但入口文案只写"重置密码"。
- 改法：停用账号时保存按钮文案改「停用账号并撤销登录」，checkbox 旁加 11px 说明；停止工作区走 `confirmDialog`（产品已有 dialogQueue）；重置密码弹窗提交按钮旁提示"原登录将失效"。

### 8. 后台移动端与超宽屏

做得对：<760px 侧栏变抽屉 + 遮罩，隐藏态 `visibility:hidden` 防 Tab 进入（`modern.css:351-352`）；页头动作在移动端自动 icon 化；表格容器都有 `overflow-x`。

#### PA29 · P1 · 归类：布局（超宽屏）
- 位置：`modern.css:60,68,223`、`overlays.css:93`、`desktop-density.css:71-74`；截图 `fixture/admin-journals-ultrawide.png`、`fixture/admin-library-ultrawide.png`
- 现象：`.portal-admin-content` 与 `.page-container` 都是 `width:100%` 且没有 `max-width`；2845px 截图里文献库搜索框被拉到约 2000px 宽，期刊 5 列铺满全屏、刊名与「编辑」相距 1400px 以上，扫读困难；统计/卡片全部随视口拉伸。
- 改法：`.portal-admin-content{max-width:1600px;margin:0 auto}`（同行 workbench 页容器已用 1600px，见 `modern.css:223`）；>1920px 时文献库右侧空白可放「最近加工/待处理」辅助栏；表格仍可全宽。

#### PA30 · P2 · 归类：响应式/可达性（断点与抽屉）
- 位置：`modern.css:36,257-265,268-278`；`AdminConsole.tsx:23,64`
- 现象：761–980px 仍是固定 216px 侧栏 + 内容，760px 宽窗口内容只剩约 490px，表格全部横向滚动；移动抽屉无焦点陷阱、无 body 滚动锁定，打开后焦点仍在被遮罩盖住的顶栏按钮上，关闭也不归还焦点（Escape 只 `setState`）。
- 改法：抽屉断点上移到 `max-width:1024px`；打开时聚焦第一个 nav 项、抽屉加 `aria-modal="true"`、`document.body.style.overflow='hidden'`，关闭时焦点回 toggle。

#### PA31 · P2 · 归类：细节（移动端图标/指标/触控）
- 位置：`modern.css:24,306`、`portal.css:41`；截图 `fixture/admin-journals-mobile.png`
- 现象：`.portal-icon-button` 固定 36×36（低于 44 触控下限）；页头指标被拆成「935」+ 下一行「种期刊」（`modern.css:306` 把 `small` 设为 `display:block`）；移动抽屉导航图标被 `portal.css:41` 的 `.portal-admin-sidebar nav svg{width:15px}` 强制 15px，与 16/18 规范不一致。
- 改法：移动端 icon-button 44×44；单位改回行内（删 `modern.css:306` 的 `strong small{display:block}` 或仅 >520px 保留）；删 `portal.css:41` 的 15px 规则。

### 9. 本组最重要的 3 个问题

1. **PA01（P0）未登录页面公开默认管理员口令**：`WebEntry.tsx:57` 直接在 /admin/ 登录页印出 `admin / admin123`；任何仍用初始口令的部署等于无鉴权后台。今天就能删，并顺手把 bootstrap 管理员设为强制改密。
2. **PA09 + PA10（P1）后台视觉系统两套 token，深色主题色相分叉，且 11–12px 文字 + 4.25–4.41:1 对比度**：侧栏 `#142e31` 与页面 `#101c25/#172832` 双色相，muted/警告/强调三处低于 4.5:1，覆盖全部 10 个后台分区；应先收 token（侧栏变量化 + 字号 ≥13px + muted/warn/accent 调深），再谈逐页细节。
3. **PA21（P1）临时密码对话框没有确认/显示/生成/复制**：错一个字符就要给用户重置一次，是后台最高频也最容易制造锁号的失败点；加确认 + 生成 + 复制并提示"原登录将失效"即可闭环。

紧随其后：PA17（期刊库后台无批量导入）、PA22（工作区停止动作全禁用）、PA25（操作记录不可查）。

### 10. 做得好的地方

- **方向对，且没有典型 slop**：登录页宋体大标语 + 极淡圆环线 + 纸感浅绿，贴近「文献—阅读—写作」；全站 TSX 无 emoji、无渐变文字/发光/彩色阴影，`portal.css` 里唯一的 `filter:brightness` 只是 hover。
- **品牌组件统一**：`Brand` 被登录页、后台、首次改密三处复用（`WebEntry.tsx:52`），扁平书页 glyph + 「问津 WENJIN」，没有渐变 tile 或 sparkle。
- **后台信息架构清楚**：3 组 10 项导航 + 用户数徽标 + 底部去工作区入口，`aria-current`、Escape 关抽屉、抽屉隐藏态防 Tab（`AdminConsole.tsx:32`、`modern.css:351-352`）。
- **空态是有设计的**：系统文献库空态给图标 + 标题 + 说明 + 主 CTA（`DocumentBrowser.tsx:61`），比多数后台"一行灰字"好得多。
- **期刊库表格是专业水准**：sticky 表头、分页、命中数、来源浮层、CSV 预览含行数/新增/已有/错误行/指纹去重（`SubmissionWorkbench.tsx:60,97`、`modern.css:156-171`）。
- **键盘基础在变好**：全局 `:focus-visible` 2px 青绿环（`modern.css:19`）；后台对话框已有初始焦点 + Tab 陷阱 + Escape（`AdminConsole.tsx:41-61`）；`prefers-reduced-motion` 有覆盖（`modern.css:332-334`）。
- **错误文案成体系**：`transport.ts:16-19` + `errors.ts:13-19` 把 401/403/409/422/429/5xx 和超时映射成中文动作建议，比裸 `error.message` 可靠。
- **对"共享/私有"边界的表达一致**：登录脚注、侧栏底部、账号页都说明「系统文献共享，个人研究隔离」，虽然重复过多（可精简为一处），但产品原则传达得住。

---

## 3. 用户工作台核心页面评审

> 覆盖工作台壳层、项目概览、项目材料、材料阅读、研究工作列表与编辑器、古籍点校三阶段与点校调试、工作成果、执行记录、个人设置、项目弹窗与反馈。问题编号 WB01–WB44。

### 0. 覆盖页面清单

| 页面 | 入口/路由 | 主要文件（WSL） | 首屏任务是否清晰 |
| --- | --- | --- | --- |
| 工作台壳层（标题栏/侧栏/项目切换/底栏/助手面板） | 所有页面外层 | `src/workbench/App.tsx:400-455`、`shell.css:4-16`、`src/portal/modern.css:197-392` | 是（项目名、当前项目导航、最近材料清楚）；移动端标题栏溢出裁切 |
| 项目概览 | `#/home` | `ResearchViews.tsx:21-34` | 是（继续卡片+四步引导），但「下一步」重复、空态无动作 |
| 项目材料 | `#/project-materials`、`#/library` | `knowledge/ProjectLibrary.tsx` | 部分（空态无 CTA、搜索框是裸 `<input>`） |
| 材料阅读 | `#/materials/:id` | `Pages.tsx:60-96`、`DocumentReader.tsx:17`、`PdfReader.tsx` | 是（正文突出），但四层头栏挤压、默认处理范围 1–6 与当前页脱钩 |
| 研究工作列表 | `#/research`（非点校项目） | `ResearchViews.tsx:58-60` | 是（新建工作单元+状态筛选） |
| 研究编辑器 | `#/research/:unitId` | `ResearchViews.tsx:79-172` | 部分（自动保存 vs 保存按钮、模式按钮抢占主操作） |
| 古籍点校（准备卷文本/批量点校/点校稿） | `#/volume-preparation`、`#/collation-batch`、`#/research` | `ResearchViews.tsx:43-56`、`PreprocessViews.tsx`、`BatchViews.tsx` | 部分（步骤条无完成态；批量页是监控台式密度） |
| 点校调试 | `#/collation-debug` | `CollationDebug.tsx` | 否（首屏是 12 行提示词墙 + 接口地址，面向开发者） |
| 工作成果 | `#/artifacts`、`#/artifacts/:id` | `Pages.tsx:49-58` | 是（卡片 footer 三类信息混排） |
| 执行记录 | `#/tasks`、`#/tasks/:id` | `Pages.tsx:40-47` | 是（筛选+状态徽标+详情） |
| 个人设置 | `#/settings` | `App.tsx:436-442`、`AppearanceSettings.tsx:5`、`overlays.css:46-50` | 是（二级导航清楚；模型信息错位到账号页） |
| 项目弹窗/抽屉/反馈 | Modal/Drawer/Wizard/Toast | `Pages.tsx:20-38`、`Overlays.tsx:43-85`、`App.tsx:455` | 部分（危险操作与保存同排、弹层内主题掉线） |

### 1. 工作台壳层（标题栏/侧边栏/项目切换/底栏/助手面板）

**做得对**
- 项目切换、当前项目、最近材料三段结构清楚，`aria-current="page"`/`aria-expanded` 齐全（`App.tsx:406-411`）；`PanelDivider` 有 `role="separator"`、`aria-valuenow` 与方向键（`PanelDivider.tsx:11`）；`Ctrl K` 与 `Ctrl F` 有全局键位（`App.tsx:201-203`）。
- 状态栏把「资料服务已连接 + 当前项目 + 快捷键」放底部一条 11px 静默信息（`App.tsx:450`），`status-live-dot` 是一个成立的产品签名（`theme.css:56`）。

#### 1.1 壳层与跨页视觉系统（先修这层）

**主要问题**
- **WB01 [P0] 三套色彩系统叠加，品牌色/状态色没有单一来源。** 门户是墨绿+蓝灰（`modern.css:3-4`，深色 `:14-16`），壳层是中性石墨（`shell.css:1-2`），legacy 主题是紫色暗色（`theme.css:1` 以 `--tone-e8e9ee` 近白文字铺底；`:29`、`:94-97`、`:108`、`:119`、`:236`、`:366-368` 仍引用紫/蓝紫 `--tone-*`），再由 `palette.css:2-3` 给 **266 组 `--tone-*` 各写一遍浅/深映射**（共 532 条声明）把暗色值改写成 `#fafaf9/#f4f4f1/#dcdcd7/#74746e/#5d5d57/#32322f` 六档灰。全部 CSS 268,424 字节 / 17 文件（`main.tsx:6-11` 五层 import 顺序），`.page-container` 被 5 个文件重定义、`.setup-card`/`.continue-card`/`.statusbar` 各 4 个。→ 改法：只保留一组语义 token（`--bg/--surface/--surface-2/--line/--text/--muted/--accent/--accent-soft/--danger/--status-*`），把 `--tone-*` 一次性替换为语义 token 后删除 `palette.css`；覆盖层合并为「基础 + 门户」两层，删除重复的 `.app-shell`/`.portal-workspace` 同义规则。
- **WB02 [P0] 浅色主题下状态色语义坍塌：待复核与已审定完全同色。** `theme.css:366-368` 的三档状态经 `palette.css:2` 映射后：`.status-working`=#32322f on #dcdcd7，`.status-review`=**#5d5d57 on #fafaf9**，`.status-approved`=**#5d5d57 on #fafaf9**（两档同色）。`.artifact-kind`（`theme.css:261`）、`.filter-active`（`:236`）、`.empty-art`（`:112`）、`.skill-icon*`（`:94-97`）、`.settings-card-heading>svg`（`:287`）也都塌成 3 档灰。`shell.css:14` 只给 `.badge-completed/failed/paused` 补了浅色语义，`.unit-status`/`.status-*` 没有。→ 改法：新增 `--status-working:#8a6d1f / --status-review:#a05a1f / --status-approved:#2f6f4f` 三组（浅色）并让 `.unit-status` 加 1px 同色半透明描边；`.empty-art` 用 `--muted`，AI/skill 图标统一 `--muted` 不再各自着色。
- **WB03 [P1] 弹层里门户主题掉线。** `Drawer`/`Modal` 用 `createPortal(..., document.body)`（`Overlays.tsx:48-57`、`Pages.tsx:37`），只带 `.portal-modal` 标记；门户规则绝大多数以 `.portal-workspace` 开头（`modern.css:197-508`），因此打开抽屉后 `--portal-*` 派生样式与 `[aria-pressed]` 选中态不生效——深色抽屉里筛选选中态实测是 legacy 紫（`theme.css:236` + `fixture/audit-material-reference-drawer-dark.png`），浅色抽屉里是暖灰 `#dcdcd7`（`fixture/audit-material-reference-drawer.png`），和页面的冷色系对不上。→ 改法：把弹层相关规则改写成 `:is(.portal-workspace,.portal-modal)`，或把 portal 根提到 `#root` 让弹层继承同一变量域。
- **WB04 [P1] 字号体系失控：33 个不同字号、5 种字重。** 全站 CSS 出现 8/9/10/10.5/11/12/13/…/33px 共 33 个字号（11px 出现 124 次、12px 269 次）；壳层关键信息 9–10px：`modern.css:202`（WENJIN 9px、字距 1.8px）、`:215`（当前项目 10px，字距 2px）、`:217`（工作区说明 10px）、`:229`（状态栏 10px）、`knowledge.css:44`（10.5px）；字重 400/500/**550**/**600**/**650**（550 出现 15 次、650 17 次，如 `desktop-density.css:4` 页面标题 22px/650、`theme.css:62` 25px/650）。→ 改法：6 档字号 `12/13/14/15/18/22/26`（最小 11px，正文字号不进到 10px 以下），字重只留 400/500/600，`550→500`、`650→600`；小字不加负字距（现有 `letter-spacing:-.2/-.6/-.8px` 只应出现在 ≥22px 标题）。
- **WB05 [P1] 间距不在网格上：1574 条 spacing 声明、42 个不同值、259 条奇数 px（16%）。** 4px 网格占 805 条（51%），2px 步进 510 条，其余 259 条是 3/5/7/9/11/13/15/17/19/25px。典型：`.page-container` 被 4 层覆盖后门户实际生效 `18px 24px 24px`（`desktop-density.css:36`，18 不在网格）、`.continue-card{padding:22px}`（`theme.css:72`）、同类列表行内边距不一致 `.task-row{padding:16px 18px}`（`:98`）vs `.unit-row{padding:18px}`（`:360`）vs `.batch-table td{padding:12px 8px}`（`:471`）、`.batch-stats>div{padding:16px}`（`:465`）。→ 改法：间距 token `4/8/12/16/20/24/32/40/48`；卡片 20/24，页面边 24/32，列表行 32/36/40；先修 `.page-container`/`.continue-card`/`.unit-row`/`.batch-table` 四处即可覆盖大半屏。
- **WB06 [P2] 16 种圆角值。** 8px(60)/6px(50)/7px(39)/10px(38)/9px(21)/5px(17)/12px(16)/4/11/13/15/16/999px，且 `.task-badge{border-radius:20px}`（`theme.css:105`）把状态做成胶囊，`.drawer-panel` 15px（`overlays.css:38`）。→ 改法：控件 8、卡片 12、面板 16、chip 6，全部嵌套半径 = 外 − 内边距，删 5/7/9/11/13/15。
- **WB07 [P1] 深色主题是另一套产品性格。** 深色门户是蓝绿 navy（`modern.css:14-16`），但 legacy 紫色仍在深色下生效（`theme.css:29` 导航紫底、`:94-97` 彩色技能图标、`:108` 紫色 executing 徽标、`:119` `.library-switch` 紫、`:236` 紫色筛选选中、`:366-368` 紫/琥珀/绿状态）——默认浅色尚可，用户一旦切深色就回到「紫色 AI 感」。→ 改法：深色同样只走语义 token；把上述选择器的 `--tone-*` 替换为 `--accent`/`--surface-2`/`--status-*`；浅色与深色各写一份 `--status-*` 即可。
- **WB08 [P0] 430px 手机端标题栏溢出、「研究助手」被右边缘裁掉。** 标题栏文字标签只在 `max-width:380px` 才隐藏（`modern.css:389-391`），而 430px 视口下标题栏同时保留「项目材料 + Ctrl K」搜索触发器、`林若舟` 账号名（`max-width:72–116px`，`modern.css:205,387`）与带文字的「研究助手」按钮（`App.tsx:402`），实测溢出（`fixture/user-workspace-mobile.png`、`fixture/user-navigation-mobile.png`）。→ 把「隐藏按钮文字」断点提到 ≤560px：`.titlebar-end .button-quiet>span{display:none}`、`.titlebar-end .search-trigger>span,.titlebar-end .search-trigger kbd{display:none}`，账号按钮只留姓名首字（32px 圆）；标题栏只保留一个搜索入口。
- **WB09 [P1] 对比度不足项（按 WCAG 实算）。** `--portal-muted:#657583` on `--portal-bg:#f4f7f9` = **4.41:1**（低于 4.5，且大量用于 11–12px 元信息：`modern.css:215-217,229`）；`--portal-accent:#157d70` on `--portal-soft:#eaf5f0` = **4.48:1**（激活导航 `modern.css:213`、保存状态 chip `desktop-density.css:17`）；`.artifact-kind`/`.task-badge` 在浅色下背景与卡片接近（`theme.css:105,261`）。另：输入框/控件边框取 `--line`（`modern.css:20`、`theme.css:7`），`#e4ebef` on `#fff` = **1.2:1**，不满足 1.4.11 的 3:1 控件边界要求。→ 改法：`--portal-muted→#5a6a77`（≈5.2:1）；`--accent-on-soft` 用 `#116b60`；新增 `--control-border:#b9c6cf` 专门给 input/select/textarea/checkbox 描边（hairline 仍用 `--line`）。

### 2. 项目概览

**做得对**：继续卡片先给「最近一份稿件+继续工作」，四步引导（添加材料→准备工作文本→建立工作单元→设置规则）顺序正确，主按钮只有一个（`ResearchViews.tsx:33`）；`project-summary` 五项指标在门户下做成 5 列带分隔线的数字条（`modern.css:337-342`），比一排彩色卡片克制。

**主要问题**
- **WB10 [P1] 「最近执行记录」空态只有一行小字、无动作、无图形，却占掉 240px 白卡。** `ResearchViews.tsx:33` 末尾 `{!tasks.length && <p className="scope-note">…</p>}`，对照其它页统一用的 `Empty` 组件（`Pages.tsx:18`，含 `.empty-art`），门户空态有 50px 圆角图标底（`modern.css:401-405`）——这里全都没用上。→ 用 `Empty`，标题「还没有执行记录」，正文一句，主按钮「打开材料」/「去研究工作」。
- **WB11 [P1] 同屏两个「下一步」。** 继续卡片主按钮「从文献库选材」（`ResearchViews.tsx:33`）与步骤卡 1 的「下一步 →」（同处，`<small>{…'下一步 →'}</small>`）指向同一动作；且「下一步 →」是文本箭头，与其它地方的 `<ArrowRight/>`（如「继续工作」按钮）不一致。→ 步骤卡只作为状态展示（已完成/待准备/当前），整卡可点即可；当前步骤不要同时用 `bg+accent border+inset 3px+编号反色` 四重信号（`overlays.css:138-139`），保留 accent 左边线+编号即可。
- **WB12 [P2] 说明文字用 `title` 当 tooltip，移动端/键盘不可达。** `ResearchViews.tsx:33` 两处 `<p className="scope-note" title="进度只统计已建立范围…">`；同页 `PageHeading` 已有可点击的 ⓘ details 方案（`PageHeading.tsx:33`）。→ 统一为 details/summary 或收敛成一句 12px caption。

### 3. 项目材料 / 项目文献库

**做得对**：标题行只放「从文献库添加（次）+ 导入并关联项目（主）」两个动作（`ProjectLibrary.tsx:16`）；材料卡按「版本—用途—范围—文件名」组织，点文件名直接进阅读（`:18-20`）；用途/范围编辑用行内 `kb-editor` 而不是再开弹窗，离开有 `confirmDiscard` 守护（`:14`）。

**主要问题**
- **WB13 [P1] 空态没有 CTA，且与顶部主按钮相距一整屏。** `ProjectLibrary.tsx:21`：`<div className="empty-block">还没有为项目选择文献。可从文献库选材，或导入个人文献。</div>`——纯文本死胡同；同项目里准备卷文本页的空态已经带主按钮（`PreprocessViews.tsx:13`），`Empty` 的图标/按钮结构（`Pages.tsx:18`）这里都没用。→ 空态内放主按钮「从文献库选材」+ 次按钮「导入个人文献」，文案只说结论（「还没有为项目选择文献」）。
- **WB14 [P1] 搜索框是裸 `<input>` 拉满 1180px。** `ProjectLibrary.tsx:17` 直接 `<input aria-label="搜索项目材料" placeholder="题名、版本或用途说明">`，无容器、无图标、无清除、无结果计数；过滤后无匹配时页面只剩空白（空态条件 `!sources.length`，不是「无匹配」）。→ 套 `.inline-search`（`theme.css:121`）并限宽 360–420，加「N 条 / 无匹配」行与清除按钮；空态与无匹配分开。
- **WB15 [P2] 版本状态拼进标题、移出无确认。** `ProjectLibrary.tsx:18`：`<h2>{s.title} · {s.label||'版本待补'}</h2>` 把元信息混入标题；同排「移出这一范围」是 `.button-quiet`（无 danger 色、无二次确认）。→ 标题仅书名，版本用 12px 徽标；「移出」改 `.button-danger` + 复用 `DeleteDialogs` 的确认模式。

### 4. 材料阅读页

**做得对**：阅读器工具栏只保留「文件名 / 段号 / 上下页 / 导出原件」（`DocumentReader.tsx:17`），翻页输入框有 `aria-label` 与夹取（`viewState.ts:18`）；顶部动作把「参考文献（N）/任务与出处」收进抽屉、`Esc` 优先给最上层弹窗（`Pages.tsx:69`）；`Ctrl ↩ 执行` 有真实实现（`Pages.tsx:94`）。

**主要问题**
- **WB16 [P1] 一屏四条横向 chrome，正文被压到 ~700px。** 标题栏 56px（≥980px，`desktop-density.css:34-35`；窄屏 64px，`modern.css:198`）+ 面包屑栏 36px（`desktop-density.css:37`；窄屏 51px，`modern.css:222`，`App.tsx:417` 在 materials 页仍渲染）+ 约 100–110px 文档头（`Pages.tsx:70`）+ 42px 阅读器工具栏（`theme.css:167`），合计约 240px。面包屑「明清地方社会研究 > 公共测试文献」与文档头的「返回项目材料 / 材料详情 / 公共测试文献」重复。→ materials 页不渲染 `.topbar`，返回与面包屑并入 `workspace-header`，文档头压到 64–72px；阅读优先。
- **WB17 [P1] 正文行宽 ~1100px、无最大宽度。** `.text-document-body{font:17px/2.1 …;padding:28px 36px}`（`theme.css:376`）没有 `max-width`，17px × 2.1 行高在 1480 宽窗口下单行约 65 个汉字（`fixture/audit-material-reader.png`），远超声东/中文舒适阅读区。→ `.text-document-body{max-width:42em;margin-inline:auto;padding:32px 24px}`；`古籍/点校`正文建议加 `--font-reading:'Source Han Serif SC','Noto Serif SC','Songti SC',serif`（当前全站只有 `Microsoft YaHei UI` 一种无衬线，`theme.css:1`、`shell.css:16`、`modern.css:10` 三处字栈还各不相同）。
- **WB18 [P1] 默认处理范围 1–6 页与当前阅读页脱钩。** `viewState.ts:8`：`pageFrom:1, pageTo:Math.min(6,pages)`；用户翻到第 120 页点「运行任务」，模型收到的是第 1–6 页（`Pages.tsx:89` 面板里显示 1–6，但视觉弱）。→ 首次进入时 `pageFrom/pageTo` 跟随 `readerPage`；面板顶部用一句 12px 明确「将处理：第 X–Y 页」，保留「当前页 / 全文」快捷（已存在，`Pages.tsx:89`）。
- **WB19 [P2] 材料处理面板默认展开、与全局研究助手并存。** `viewState.ts:8` `sideOpen:true` 让每份新材料一进来右侧就占 340px（`theme.css:162`）；同时标题栏「研究助手」可再开 380px 的 `project-assistant-panel`（`App.tsx:402,454`），两面板同开会把正文压到约 500px。→ 默认收起；两者合并为同一面板的两个 tab（问答 / 处理任务），或把「材料处理」改名「任务处理」并并入助手面板。
- **WB20 [P2] 抽屉出现二级标题。** `Drawer` 标题「论文参考文献」+ 内容 `<h2>参考文献追踪</h2>`（`Pages.tsx:74,108`；`Overlays.tsx:51`），「任务与出处」同样（`Pages.tsx:77-78`）。→ 去掉内部 h2，必要时改成 12px caption；抽屉内只保留区块标题。

### 5. 研究工作列表 / 研究编辑器

**做得对**：列表一行给「标题 / 类型·来源·范围·版本 / 状态 / 箭头」，空态带动作按钮（`ResearchViews.tsx:60`）；编辑器左右对照（原文 vs 工作稿）、`unit-content-editor` 用 17px/2.1 保证书写舒适（`theme.css:392`），离开有未保存拦截（`ResearchViews.tsx:162` 的 `persist`/`dirty` 状态）。

**主要问题**
- **WB21 [P1] 筛选器缺 `aria-pressed`，门户选中态样式因此不生效。** `ResearchViews.tsx:60` 只给 `className={filter === id ? 'filter-active' : ''}`；执行记录页同一控件有 `aria-pressed`（`Pages.tsx:46`），而门户针对 `[aria-pressed=true]` 写的品牌色选中态（`modern.css:413-415`）在研究中页落空，只剩 legacy 灰（`theme.css:236`）。→ 抽一个 `FilterGroup`（`role="group" aria-label`，按钮带 `aria-pressed`），两页共用。
- **WB22 [P1] 「自动保存」与「保存」按钮并存，保存语义不清。** 编辑器占位文案写「输入后自动保存」（`ResearchViews.tsx:164`），头部却同时有「已保存 · 版本 N」状态、`保存` 按钮、`导出稿件`、`人工审定` 四个动作（`:162`）。→ 保留自动保存 + 状态文案；手动动作改「另存版本」；主按钮只留「人工审定」。
- **WB23 [P1] 模式切换用主按钮表达，页面上同时 3–4 个绿色主按钮。** `ResearchViews.tsx:163`：`本卷清洗底文/本卷原始录文/查看来源附件` 三选一用 `button-primary` vs `button-secondary` 切换，`校记与待核` 展开时也是 primary，加上头部「人工审定」——同一屏最多 4 个实心品牌色按钮，用户看不出哪个是真正的下一步。→ 改成 tab 组（`role="tablist"` + `aria-selected`，样式用下划线/浅底），页面上只保留一个实心主按钮。
- **WB24 [P2] 工作单元行太高、没有排序/搜索。** `.unit-row{padding:18px}`（`theme.css:360`）使单行约 100px；只有 4 个状态筛选。→ 行高 56–64（padding 12 16），加「最近更新/卷序」排序，超过 20 条提供搜索。
- **WB25 [P2] 「新建工作单元」在无材料时禁用但无解释。** `ResearchViews.tsx:60` `disabled={!materials.length}`，无 `title`、无提示。→ 禁用时给 tooltip「先添加材料」或把按钮降级成引导到项目材料。

### 6. 古籍点校工作区与点校调试

**做得对**：四步导航（添加底本→准备卷文本→批量点校→点校稿）把长流程讲清楚（`ResearchViews.tsx:45-51`）；准备页把固定处理规则逐条列出再执行（`PreprocessViews.tsx:63-74`）；批量页有「批次监控 + 文件执行列表 + 请求明细 + 导出」的完整闭环，空态带主按钮（`BatchViews.tsx:108`）；用量含糊处会写明「未报告项不视为零」（`:101`），诚实。

**主要问题**
- **WB26 [P1] 两步标题两行、两个 ⓘ、两个主操作。** 点校稿阶段先渲染 h1「古籍点校」（`ResearchViews.tsx:45`），紧接着 `ResearchList embedded` 又渲染 h2「点校稿」+ 自己的主按钮（`:60`），截图里就是两排标题（`fixture/audit-collation-drafts.png`）。→ 阶段名降级为分组标题（14px 或 eyebrow），主按钮只保留在 h1 行；ⓘ 只留一个。
- **WB27 [P1] 步骤条只有「当前」没有「完成」，箭头是文本 `→`。** `ResearchViews.tsx:46-51` 四个 `button` 只有 `collation-step-current`，没有 done/计数；分隔符是 `<span aria-hidden="true">→</span>`。用户无法判断第 2 步是否已准备、能否跳到第 3 步。→ 每步显示状态（`✓ 已准备 12 卷` / `待准备`），完成态用 lucide `Check`，当前态用 accent 下划线；箭头改 `<ChevronRight size={14}/>`。
- **WB28 [P1] 底本 `<select>` 拉满 1180px。** `.form-label select{width:100%}`（`theme.css:331`）作用在「底本 / 原文附件」下拉上（`PreprocessViews.tsx:14`；`fixture/audit-collation-prepare.png`），一个原生下拉横跨整屏。→ 表单控件 `max-width:420px`，与该页其它卡片内容左对齐。
- **WB29 [P1] 同一张卡片里两个主按钮。** `PreprocessViews.tsx:77`「导出全部卷 ZIP」是 `button-primary`，同卡底部「批量点校」也是 `button-primary`（`:85`）。→ 导出降为 secondary，唯一 primary 留给「批量点校」。
- **WB30 [P2] 点校调试页是开发者控制台，却放在用户导航里。** 标题内嵌 `FlaskConical size={24}`（`CollationDebug.tsx:87`），下面直接展示 `provider · model · base_url`（`:88`），左栏是 `rows={12}` 的提示词正文（`:94`），首屏即提示词墙（`fixture/audit-collation-debug.png`）。→ 提示词默认折叠（summary 显示「点校提示词 · 4,200 字」），模型/地址收进「运行环境」details，标题去图标；入口在用户侧改为「高级设置」下的次级链接。
- **WB31 [P2] 批量页 6 个同权重按钮，h2 是状态词。** `BatchViews.tsx:97-98` 一行里 `删除批次/暂停/继续/停止/重试失败文件/执行事件` 全是 `button-secondary`，危险操作与运行控制同排；`<h2>{batchNames[batch.status]}</h2>`（`:97`）把「已完成」当区块标题。→ 运行控制一组（暂停/继续/停止）+ 危险区一组（删除，danger），h2 用「批次监控 · 已完成」；`进度` 用自定义 8px 条替代原生 `<progress>`（`:101`，`theme.css:468` 只设了 accent-color）。
- **WB32 [P2] 用户文案里是工程语言。** 「未完整计量/在途请求/结束原因/响应标识/缓存复用不新增请求/未返回标识」（`BatchViews.tsx:100-101,109`；`CollationDebug.tsx:89`），以及全工作台 65 处 `token`（如「上下文 token 容量」「每百万输入 token 单价」，`Pages.tsx:114`）。→ 主界面只保留「本次处理 X 份 · 估算费用 ¥Y（单价 Z）」；token/请求数/结束原因收进 `details`，术语改为「字数用量」。

### 7. 工作成果 / 执行记录

**做得对**：执行记录页有「全部/进行中/需要处理」筛选 + `aria-pressed`（`Pages.tsx:46`）、状态徽标带 `role=status` 的 loading 图标（`:16`）、详情页把「处理范围/补充要求/错误+重试」讲清楚（`:45`）；成果页把「持续研究稿件」与「任务成果」分开（`:57`），空态用统一 `Empty`。

**主要问题**
- **WB33 [P1] 成果卡 footer 三类信息混排。** `Pages.tsx:57`：`创建投稿论文（quiet 按钮） + <span>{material||'粘贴文本'}</span> + 导出（quiet 按钮）`，由 `justify-content:space-between` 撑开（`theme.css:266`），截图里「粘贴文本」孤零零卡在两个动作之间（`fixture/audit-results-populated.png`）。→ footer 左侧放 meta（来源 · 时间，12px muted），右侧放动作组；导出用 `icon-btn` + `aria-label`。
- **WB34 [P2] 时间格式三套。** 成果卡 `timeAgo` 只给「01:23」或「3 天前」（`Pages.tsx:14`），执行记录给「2026/11/5 01:23:07」（`:15`），批量/批次用 `toLocaleString('zh-CN')`（`BatchViews.tsx:95`）。→ 统一「≤24h 相对时间 + 绝对时间 title」，数字用 `font-variant-numeric:tabular-nums`（`modern.css:341` 已给指标数字用了，可推广）。
- **WB35 [P2] 所有空态共用书本图标。** `Empty` 固定 `<LibraryBig size={26}/>`（`Pages.tsx:18`），执行记录/成果/材料都是同一本书。→ `Empty` 增加 `icon` 参数（任务 `History`、成果 `Layers3`、材料 `LibraryBig`）。

### 8. 个人设置

**做得对**：二级导航 + 内容卡的层级清楚（`overlays.css:46-52`），外观页用 `role="radiogroup"` + `role="radio" aria-checked`（`AppearanceSettings.tsx:5`），设置页头有 ⓘ 说明（`App.tsx:436`），账号页把改密/退出/模型说明按序排列。

**主要问题**
- **WB36 [P1] 导航选中态 4 重信号，未选中项像禁用。** `overlays.css:48-50`：选中同时用 `background+1px border+font-weight 600+inset 3px`；未选中只是灰色文字无任何容器（`fixture/audit-settings-外观.png`）。→ 选中 = `--surface-2` 底 + 2px accent 左条，去掉边框和阴影；未选中 hover 给浅底；组内 `gap:2px`，分组前加 11px caption。
- **WB37 [P1] 模型信息被塞进「账号与安全」页底部。** `App.tsx:439` `<div className="portal-model-info">研究模型由管理员配置。当前模型：…</div>` 出现在改密/退出下方；门户设置只有「外观/账号与安全」两页（`App.tsx:93`）。→ 新增「关于/运行环境」分区（模型、资料库版本、客户端版本），或移到外观页页脚 caption。
- **WB38 [P2] 主题卡选中样式比主品牌色更强，预览是线框。** `shell.css:13` `[aria-checked=true]{border-color:var(--text);box-shadow:0 0 0 1px var(--text)}` 形成 2px 黑双环；预览块是 `#edede8/#f2f2ee` 色块（`:13`），不是真实界面。→ 选中用 accent 2px + 白底；预览换成真实浅/深截图的 1:1 缩略（或缩小版 shell：sidebar+content+statusbar 三块真实色）。

### 9. 项目相关弹窗与反馈状态

**做得对**：`Modal` 有焦点陷阱、Esc、焦点恢复（`Pages.tsx:20-38`），`Drawer` 额外锁背景滚动（`Overlays.tsx:10-37`），`Wizard` 有 `progressbar` 与步骤 `aria-current`（`Overlays.tsx:76-77`）；项目删除要求输入项目名（`DeleteDialogs.tsx:9-16`）、批次删除要勾选确认并列明后果（`:24-33`）——这是全项目最稳的一组交互。

**主要问题**
- **WB39 [P0] 项目配置弹窗把「归档」「删除」「保存」并排。** `ResearchViews.tsx:40`：`modal-actions` 里 `归档项目（保留资料与稿件） · 删除研究项目 · 保存项目设置` 三个按钮同权重相邻，删除是 `button-danger`。误点一次即进删除流程。→ 弹窗内加「危险区域」分隔（标题 12px muted + 说明 + 底部 danger 按钮），保存固定在右下；归档/删除移到独立入口。
- **WB40 [P1] Toast 的成功/失败是猜出来的。** `App.tsx:66`：`toastSuccess = /已|成功|完成|保存|复制|导出/.test(toast) && !/失败|错误|异常|无法|不可/.test(toast)`——「已取消」「已删除」会配绿勾，「已导入 3 份；a.txt：不可读取」会配红叹号。→ `setToast(message, tone)` 显式传 tone，调用处按结果给 `success|error|neutral`。
- **WB41 [P2] Modal 不锁背景滚动。** `Modal`（`Pages.tsx:20-38`）没有 body overflow 处理，`Drawer` 有（`Overlays.tsx:15-16`），弹窗打开后底层还能滚。→ 把 `useDialogFocus` 抽为公共 hook，给 `Modal` 复用。
- **WB42 [P2] `role="tablist"` 但无 `tabpanel`。** 设置/管理台子导航用 `role="tab"` 切换「页面」（`App.tsx:437,445`），没有 `aria-controls`/`role="tabpanel"`，屏幕阅读器定位不到面板。→ 面板加 `role="tabpanel" aria-labelledby`，或改成普通 nav + `aria-current="page"`。
- **WB43 [P2] 重复 id 破坏「跳到主内容」。** `App.tsx:421` 的 `content-scroll` 与 `Pages.tsx:71` 的 `workbench-center` 同时使用 `id="workspace-content"`，跳转目标不确定。→ 只保留一个 id（阅读页那个改用 `aria-label`）。
- **WB44 [P2] 三态组件三套。** loading：`page-loading`（`theme.css:116`）、`reader-loading`（`:181`）、启动态（`App.tsx:422`）；错误：`persistent-error`/`inline-error`/`service-error`（`theme.css:279,282,311`）；空态：`Empty`/裸文本/`empty-block`（`Pages.tsx:18`、`ProjectLibrary.tsx:21`、`BatchViews.tsx:108`）。→ 落一份 `Loading/Empty/Error` 组件规范，页面只调用组件。

### 10. 本组最重要的 3 个问题

1. **WB01+WB02 [P0] 视觉系统是三套牌子的混合体，浅色下状态色已失去语义。** 门户墨绿 + 壳层石墨 + legacy 紫，靠 `palette.css` 266 组 tone 在浅色里「翻译」成六档暖灰；结果是「待复核」与「已审定」在浅色下同色（#5d5d57 on #fafaf9），弹层内外主题不一致（WB03），切到深色又回到紫色 AI 感（WB07）。这是所有「看起来不像设计过」的根源，且能靠 token 迁移一次性收敛。
2. **WB16+WB17+WB18 [P1] 核心阅读被头栏和错位的默认范围削弱。** 一屏四条横向 chrome 只剩约 700px 给正文，正文行宽约 65 个汉字（`theme.css:376` 无 max-width）；同时「处理范围」默认 1–6 与当前页脱钩（`viewState.ts:8`），用户以为在问当前页、实际在花 1–6 页的模型费用。阅读器的信息架构和排版是产品的定义性体验，优先级高于任何装饰修正。
3. **WB08 [P0] 430px 手机端标题栏直接溢出、「研究助手」被裁掉。** 文字标签只在 ≤380px 才隐藏（`modern.css:389-391`），而 430px 时标题栏内容宽约 460px（搜索触发器保留「项目材料 + Ctrl K」，账号名 72–116px，助手按钮带文字）；手机端第一屏就不可用，且底部状态栏/项目信息仍按桌面密度排（`fixture/user-workspace-mobile.png`）。

### 11. 做得好的地方

- **克制的自研交互层**：`Modal/Drawer/Wizard` 的焦点陷阱、Esc 分层（抽屉里再弹 Modal 时 Esc 先关 Modal，`Pages.tsx:69`）、`aria-live` toast、可暂停的 6.5s 定时（`App.tsx:455`）、`skip-link`（`App.tsx:401`、`overlays.css:41-43`）、`role="separator"` 可键盘调节的分栏（`PanelDivider.tsx:11`）——这套细节比多数同类产品扎实。
- **删除确认做得对**：项目删除要输入项目名，批次删除要勾选并逐条列明「删什么、留什么」（`DeleteDialogs.tsx:9-33`）。
- **没有 emoji、没有感叹号、没有渐变**：全工作台 TSX 扫描无 emoji 图标、无 UI 感叹号，CSS 中无任何 `gradient()`（只用了 816 处 hex / 431 个色值，但性质是 legacy 调色板而非渐变 slop）；`animate-pulse`/parallax 之类循环动效也不存在，仅保留 `spin` 并支持 `prefers-reduced-motion`（`theme.css:320-325`）。
- **诚实的加载与边界文案**：「正在启动资料服务 · N 秒」（`App.tsx:422`）、「未报告项不视为零」（`BatchViews.tsx:101`）、「原文快照仅保存前 20,000 字」（`Pages.tsx:55`）、「保存书目不等于持有全文」（`Pages.tsx:108`）——不承诺做不到的事，这点与 unslop 的 copy 原则一致。
- **有产品味的小细节**：状态栏 `status-live-dot`（`theme.css:56`）、`kbd` 键帽（`theme.css:51`）、`Ctrl ↩ 执行` 真实生效（`Pages.tsx:94`）、段落号语义「段/页」按材料类型切换（`DocumentReader.tsx:9`）——继续保留并在全站统一，就是问津自己的 signature。

---

## 4. 文献库 / 知识库 / 古籍点校 / 研究助手页面评审

> 覆盖用户端文献库（资料目录、正文检索、阅读详情、核心古籍库、未分类）、管理端系统文献库与加工队列、检索配置 7 个分区、失败诊断与索引历史、研究助手（单助手/团队）、助手与 Skill 设置、协作研究、项目助手面板。问题编号 KB01–KB39。

### 0. 覆盖页面清单

| 页面 | 入口/路由 | 主要文件 | 首屏任务是否清晰 |
| --- | --- | --- | --- |
| 资料目录（用户端） | `/app/` → 文献库 → 资料目录 | `KnowledgeWorkbench.tsx:93`、`DocumentBrowser.tsx` | 清晰：分类侧栏 + 文件表 + 导入主按钮，一眼可懂 |
| 正文检索 | 文献库 → 正文检索 | `UnifiedLiteratureSearch.tsx` | 部分：表单清楚，但范围语义与空屏无引导 |
| 文献阅读详情 | 目录点文件 / 检索结果"打开原文" | `LibraryDocumentReader.tsx`、`KnowledgeWorkbench.tsx:94` | 清晰：左原件右录文；摘录入口弱 |
| 维护原文与版本（点校/录文对照） | 阅读页"维护原文与版本"、管理端 | `KnowledgeWorkbench.tsx:101-107`、`SourceOutline.tsx` | 不清晰：首屏两条工具条，主任务"保存为新修订"在页面中下 |
| 核心古籍库 | 打开古籍 / 古籍阅读 | `CoreLibrary.tsx` | 部分：书目/原文检索是伪 tab，11 个藏部勾选常驻 |
| 未分类/系统文献书目 | 管理端、设置 → 历史文献待归类 | `UnclassifiedCatalog.tsx` | 清晰：表格 + 两个行内动作 |
| 系统文献库（管理端） | `/admin/` → 系统文献库 | `SharedLibrary.tsx:12-13`、`KnowledgeWorkbench.tsx:86-93` | 部分：工具栏把跨页导航与页内动作混排 |
| 文献加工队列 | `/admin/` → 检索配置 → 文献加工 | `RetrievalOptionsPanel.tsx:48,53`、`IngestionPanel.tsx` | 部分：两个 primary 竞争，"全库加工"置于选择控件之上 |
| 解析与切分 / 向量模型 / 查询处理 / 检索参数 | 同上 7 个 tab | `ChunkStrategyFields.tsx`、`EmbeddingPanel.tsx`、`QueryProcessingPanel.tsx` | 清晰：编号分节 + 字段级说明，但保存条在三处重复 |
| 检索测试与检索诊断 | 检索参数 → 检索测试 / 结果"检索依据" | `RetrievalOptionsPanel.tsx:75-77`、`RetrievalDiagnostics.tsx` | 部分：诊断默认折叠，技术 JSON 直出 |
| 评测记录 | 检索配置 → 评测记录 | `EvaluationPanel.tsx` | 不清晰：无空态，四个指标挤成一段 |
| 失败诊断 | 文献加工 → 失败诊断 | `FailurePanel.tsx` | 清晰：阶段/原因/批次三层筛选 + 抽屉详情，本模块最成熟的一屏 |
| 索引历史清理 | 向量模型页底部 | `IndexHistoryPanel.tsx` | 清晰但位置过深 |
| 研究助手（单助手） | `/app` → 研究助手 / 项目右侧面板 | `ResearchAssistant.tsx` | 不清晰：表单清楚，结果区（草稿/证据/保存）割裂 |
| 协同研究（多助手） | 研究助手 → 多助手协作 | `CollaborationResearch.tsx` | 不清晰：流程/范围/预算同表单，"协作图"是卡片墙 |
| 助手与 Skill 设置 | `/admin/` → 助手与 Skill | `AssistantManagement.tsx` | 不清晰：一屏 7 个同级按钮 + JSON 文本域 |
| 项目助手面板 | 项目内右侧面板 | `ProjectAssistantPanel.tsx` | 清晰，但保存成果弹窗与独立页重复实现 |
| 文献库数据库设置 | 设置 → 数据 | `KnowledgeSettings.tsx` | 清晰：dirty 时禁用测试并说明原因 |

**本组共同背景（跨页，先读这一节再看逐页）**

- 间距/尺寸没有尺度：本组 6 个 CSS 文件里 `min-height` 出现 21 个不同值（控件高度就有 24/27/28/30/32/34/36/38/40/42/48 等 11 档）、`border-radius` 12 个值（2…12、20）、`padding` 出现 13/15/17/11 13 这类非 4 倍数（实测自 `knowledge.css`、`retrieval.css`、`processing-*.css`、`failures.css`、`chunkStrategies.css`）。
- 本组样式被门户 CSS 托管：`src/portal/modern.css:114-124、230-243、300-302、476-514` 有 40+ 条只针对 `.kb-*` 的覆盖规则，同一屏在 `/app/文献库`、`/admin/系统文献库`、`/admin/检索配置` 三种宿主下 padding 是 18/20/24、圆角 9–13 各不同；强调色也随之在"墨灰（workbench）/青绿（portal）"之间切换。
- 图标无统一规格：`src/workbench/knowledge/*.tsx` 共 65 处 lucide 图标使用 11 种尺寸（13/14/15/16/17/18/19/20/21/24/28），`strokeWidth` 0 处设置（默认 2），与 Lead 的 S8 同源。
- 页面标题：管理端由 `AdminConsole.tsx:64` 的 `PageHeading` 提供（标题取自导航名，所以管理端标题正确）；工作台侧 `App.tsx:444` 给所有管理子页一个统一的 H1「管理」（描述"助手与 Skill、检索配置和资料维护。"），三个子页靠内部 tab 区分——层级正确但用词过泛。

### 1. 文献库：资料目录与批量选择

**做得对**：`<aside aria-label="导入分类">` + `<nav aria-label="文献路径">` 面包屑 + 表格结构语义完整（`DocumentBrowser.tsx:44,50,65-66`）；去抖 200ms + generation ticket 防竞态、加工中 3s 轮询（`DocumentBrowser.tsx:32-33`）；空库有设计过的空态卡（图标 + 标题 + CTA，`knowledge.css:52`、`DocumentBrowser.tsx:61`）。

**主要问题**

- **KB01（P1·细节）批量选择 500 上限是隐形的**：`DocumentBrowser.tsx:66` 表头全选只取 `result.files.slice(0,500)`；`:68` 超出后其余 checkbox 变 disabled，界面没有任何说明；`LibraryScopePicker.tsx:18` 同样逻辑。用户以为全选了，实际静默丢文件。改法：表头改成「选择本页 / 选择全部匹配（N）」两段式，复用 `IngestionPanel.tsx:95` 已有的「全选所有匹配文献（N 份）」交互；达到上限时给 `role="status"` 的明确提示。
- **KB02（P1·颜色）选中态在紫调与中性之间漂移**：`knowledge.css:31` 写死 `.kb-file-groups>button.is-selected{background:var(--tone-302b46);color:var(--tone-e4dfff)}`，暗色下即 `#302b46`（紫）/`#e4dfff`（紫白，`palette.css:3`）；`shell.css:9` 想把同一选择器改成中性 `--accent-soft/--text`，但两者同权重、只靠样式表顺序决定胜负，`modern.css:121` 在管理端又覆盖一次。改法：删掉 tone 直写，选中态只用 `--accent-soft` + `--text` + 2px `--accent` 左侧条，一处定义。
- **KB03（P2·视觉/文案）空态两套 + 与页头主按钮重复**：设计过的空态只有 1 处（`DocumentBrowser.tsx:61`），模块内还有 7 处 `<p className="empty-block">`（`DocumentBrowser.tsx:73`、`CoreLibrary.tsx:90,95`、`UnclassifiedCatalog.tsx:68`、`LibraryDocumentReader.tsx:24`、`FailurePanel.tsx:53` 等），落到 `theme.css:111` 就是 180px 高、无图无动作的一行字；同时页头已有 teal「导入文献」（`KnowledgeWorkbench.tsx:88`），空态卡里又有一个「导入第一份文献」，同屏两个主按钮。改法：抽一个 `<EmptyItem icon title hint action>`，只出现在内容区，页头按钮保留唯一主操作。
- **KB04（P2·细节/可达性）"修改分类"用自由文本 + datalist**：`DocumentBrowser.tsx:78` 让用户手输分类名（`list="document-group-names"`），容易产生错字/重复分类。改法：分类已知，改成 `<select>` + 「新建分类…」尾项，新建时才出现文本框。
- **KB05（P2·布局）每行 3 个文字按钮**：`DocumentBrowser.tsx:71` 每行固定「阅读 / 检索 / 修改分类」，`knowledge.css:55` 还给操作列 204px。改法：行标题即"阅读"入口（键盘可聚焦），其余两个收进行尾 `MoreHorizontal` 菜单（`DocumentBrowser.tsx:53` 已有该菜单模式）。

**改法小结**：先收敛选中态 token 与空态组件（KB02/KB03），再把批量选择改成显式上限（KB01），最后减按钮密度（KB05）。

### 2. 文献库：正文检索

**做得对**：输入框支持原句/关键词/自然语言并在说明里讲清语义与关键词的差别（`UnifiedLiteratureSearch.tsx:13`）；检索结果保留原文、出处、页/段与三个后续动作（`打开原文 / 复制引文与出处 / 用此文献研究`，`:13`）；剪贴板失败有降级文案（`:12`）。

**主要问题**

- **KB06（P1·布局）没有初始空态，首屏 2/3 是空白**：组件只有 `{searched&&...}` 与结果分支（`UnifiedLiteratureSearch.tsx:13`），未检索时除了一行 12px 说明什么也没有；截图 `audit-library-search.png` 就是大片空白。改法：未检索时给"示例问题 3 条 + 最近检索 + 可检索范围说明（一行）"，示例点击即搜；已有 `RetrievalOptionsPanel.tsx:76` 的 `.rag-test-empty` 模式可复制。
- **KB07（P1·文案/语义）范围下拉同值不同义**：`UnifiedLiteratureSearch.tsx:13` 中 `value="all"` 的选项文字会在「当前分类／目录」和「整个文献库」之间变化（`libraryScope.document_group_id` 存在时），而「全部文献（不限分类）」是另一个值 `global`；用户无法从文字判断"全部"到底含不含当前分类。改法：范围只留三个值（当前目录/整个文献库/本项目），"当前目录"用二级控件显示路径（可复用 `DocumentBrowser.tsx:50` 面包屑），不要把路径信息塞进 option 文本。
- **KB08（P1·字体/布局）说明段落无行宽限制**：`:13` 那段 6 句说明落在 `theme.css:146` 的 `.scope-note`（12px、无 `max-width`），在 1480px 下每行 90+ 汉字（见 `audit-library-search.png`）。改法：说明类文本统一 `max-width: 62ch`，并把它降级为一行 hint + `ⓘ`（`PageHeading` 已有该模式，`PageHeading.tsx:33`）。
- **KB09（P2·文案/布局）后台术语外泄 + 两工具栏抢戏**：`:13` 的"跟随后台配置"把管理员配置概念推到用户面前；「查找原文」是 primary，而真正差异化的「交给研究助手」是 secondary 且排在第二行左侧。改法：模式默认隐藏到"高级"，主按钮改「检索并交给研究助手」，普通检索作为次操作。

**改法小结**：补空态与示例（KB06）是收益最高的一步；随后合并范围语义（KB07）和行宽（KB08）。

### 3. 文献详情阅读（原件+录文、页/修订、摘录）

**做得对**：原件/录文双栏 + 分册与修订选择 + `SourceOutline` 目录，结构对（`KnowledgeWorkbench.tsx:102-104`）；录文正文用 17px/1.95 行高与 1000px 行宽（`knowledge.css:16`），适合古籍长文；块级质量提示（`置信度 / 待人工核验 / 可能为双栏`）是真正的专业细节（`:104-105`）。

**主要问题**

- **KB10（P1·视觉）文本类原件会把正文渲染两遍**：`KnowledgeWorkbench.tsx:103` 左侧在非 PDF、无扫描图时直接 `<pre>{pageBlocks.map(b=>b.body).join('\n\n')}</pre>`，`:104` 右侧又逐块渲染同一批 `blocks`；TXT/Markdown 原件的阅读页因此左右完全重复（截图 `admin-failure.png` 即该页：左侧框内只有一行正文，右侧再次显示）。改法：`isPdf||image` 才启用双栏，纯文本原件降为单栏"录文 + 目录"；或左栏显示原始文件字节/行号视图。
- **KB11（P1·布局）三重滚动容器**：原文维护页 `knowledge.css:1` 给 `.kb-original{height:70vh;position:sticky;top:0;overflow:auto}`、`.kb-text{max-height:75vh;overflow:auto}`，两栏各自内部滚动；用户阅读页则是 `.kb-document-original{height:76vh}`（`knowledge.css:56`）被 `shell.css:9` 改成 `calc(100dvh - 300px)`，`.kb-document-transcript{max-height:76vh}`（`knowledge.css:57`）再加一层；页面本身还能滚。改法：外层只保留页面滚动，左右两栏各自 `height: calc(100dvh - 页面头部)` 且内部不滚，或把原件栏改成 `position:sticky` 不加固定高度。
- **KB12（P1·布局）"维护原文与版本"首屏工具条过载**：`KnowledgeWorkbench.tsx:101` 一行 6 个控件（返回/编辑书目/编辑版本/新增版本/添加原件/重建索引，管理端再加"项目关联"），`:102` 又一行 4 个（分册/页段/修订/读取最新修订），`:106` 的主任务「保存为新修订」沉到页面中下。改法：首屏只留「返回 + 标题 + 保存修订」，其余进入右对齐 `MoreHorizontal` 菜单或右侧"版本"抽屉；`:102` 的四个控件合并为一条工具栏（分册 → 页段 → 修订），把「读取最新修订」变成修订下拉里的一个选项。
- **KB13（P1·细节）`SourceOutline` 完全无样式**：`SourceOutline.tsx:21` 用 `.kb-outline`，该 class 在全仓 CSS 中不存在（已全量搜索），实际渲染成浏览器默认 `<details><ul><li><details>`，没有缩进/字号/间距体系。改法：补 `.kb-outline` 样式（12px/1.7、树层级缩进 16px、当前页高亮），或直接用现有 `.kb-quality` 折叠风格。
- **KB14（P2·细节/可达性）禁用无原因 + 抽屉里的无标签下拉**：`:106` 「保存为新修订」在 `revision!==active_revision` 时 disabled 但无提示；`:108` 项目资料抽屉里三个 `<select>` 只有 `aria-label`（研究项目/项目用途/关联范围），视觉上没有字，靠当前值猜。改法：禁用时给 `title` + 相邻一行原因（"当前查看历史修订，切回最新修订后可保存"）；抽屉里给可见 `<label>`（现在其它页面已普遍使用 label 包裹，例如 `ChunkStrategyFields.tsx:14`）。

**改法小结**：先修双栏重复（KB10）与单一滚动（KB11），这两项直接决定"阅读/点校"这一核心动作的舒适度；工具条重排（KB12）次之。

### 4. 核心古籍库与未分类文献

**做得对**：命中高亮按码点切片（`CoreLibrary.tsx:18`），不会切坏生僻字；检索模式把"繁简归一/原字形"讲清楚（`:91`）；每条命中给出 `source_path` 与"提取录文第 N 字起"（`:94`），可追溯性扎实；未分类表把原件/正文/语义索引三种状态分列（`UnclassifiedCatalog.tsx:61-63`）。

**主要问题**

- **KB15（P1·细节/可达性）"书目浏览 / 原文检索"是伪 tab**：`CoreLibrary.tsx:81` 用 `button-primary`/`button-secondary` 表示当前视图，无 `role="tab"`、无 `aria-selected`、无方向键；同模块另有 `.kb-tabs`（`KnowledgeWorkbench.tsx:87`）和 `.rag-tabs`（`RetrievalOptionsPanel.tsx:48`）两套真 tab，研究助手还有一套按钮伪 tab（`ResearchAssistant.tsx:26`）——一个产品四种 tab 表达。改法：收敛为一种 `.segmented` 组件（`aria-selected` + 左右方向键 + 当前项用 `--accent-soft` 底），三处替换。
- **KB16（P1·细节/视觉）原始 JSON 与调试数据直接暴露给使用者**：`CoreLibrary.tsx:101` 把 `metadata` 用 `<pre>{JSON.stringify(...,null,2)}</pre>` 整块展示；`RetrievalDiagnostics.tsx:27` 把 `snapshot/results/diagnostics` 三段 JSON 直接铺开（该组件同时出现在用户侧研究助手 `ResearchAssistant.tsx:74`）；`AssistantManagement.tsx:79` 的"版本对照"是两段裸 JSON 并排。改法：默认只给结构化字段（模型/维度/版本/耗时/命中数）；原始 JSON 收进"复制诊断"或"下载 JSON"，并在 `<pre>` 上加等宽字体与 `max-height`。
- **KB17（P2·文案）术语一物多名**（建议并入 Lead 术语表）：「交给研究助手」（`UnifiedLiteratureSearch.tsx:13`）／「用于问答」（`CoreLibrary.tsx:90,94`）／「用此文献研究」（`UnifiedLiteratureSearch.tsx:13`）／「检索」按钮（`DocumentBrowser.tsx:71`）指同一动作；「研究成果/工作成果/项目成果」三种叫法（`ResearchAssistant.tsx:76`、`KnowledgeWorkbench.tsx:123`、`ProjectAssistantPanel.tsx:16`）；「协同研究/多助手协作」两种（`CollaborationResearch.tsx:32`、`ResearchAssistant.tsx:26`）；「Skill 中心/技能」混用（`AssistantManagement.tsx:41,45`）。改法：定表后用 codemod 全文替换，`:class` 与 `aria-label` 同步改。
- **KB18（P2·细节）状态与筛选的细节粗糙**：`UnclassifiedCatalog.tsx:61-63` 三行 `<small>` 塞在同一单元格（"原件：已有/正文：可检索/语义索引：未就绪"），无法一眼比较；`:47` 年份筛选是无 `type`、无校验的裸 input；`CoreLibrary.tsx:16` 分类名混用繁体「四庫全書」。改法：状态列改成三个小圆点+短词（就绪/待补），年份改 `inputMode="numeric"` + 前端校验，分类名统一简体。

### 5. 管理端：系统文献库与文献加工队列

**做得对**：后台状态可信度高——`OverallProcessingProgress` 有 `role="progressbar"` + `aria-valuetext`（`ProcessingProgress.tsx:84`），并且刻意不让"未完成"显示 100%（`:33-37`，注释写明理由）；刷新失败保留上次数据并标注（`IngestionPanel.tsx:96`、`ProcessingProgress.tsx:101`）；重活全部走带范围/成本说明的确认弹窗（`IngestionPanel.tsx:58,63,68`）。

**主要问题**

- **KB19（P1·布局/层级）工具栏把跨页导航和页内动作混成同一排**：管理端系统文献库的工具条是「文献加工与向量化 / 编辑文献书目」（`SharedLibrary.tsx:11` 注入的 `libraryActions`，实为跳去另一个管理分区）+「处理任务 / 导入文献 / 刷新」（`KnowledgeWorkbench.tsx:88`）共 5 个，样式只有 primary/secondary 两个层级（截图 `admin-shared-library.png`）。改法：跨页入口移出工具栏（侧栏已有导航），或做成页头右上的一行 `button-quiet` 链接；工具栏只保留本页动作，primary 唯一（导入文献）。
- **KB20（P1·布局/视觉）两个 primary 抢同一个任务**：`IngestionPanel.tsx:94` 的「处理全部系统文献」是整条 `--accent-soft` 横幅 + primary 按钮，位置在筛选和勾选之上；`:95` 又有「处理并建立索引」primary。用户面对"我到底该点哪个"（截图 `audit-rag-processing.png` 中两个 teal 按钮同屏）。改法：把"全库加工"降为工具条右侧 `button-secondary` + 二次确认（已有确认），主按钮只保留"处理所选"；或在选择为 0 时主按钮文案变成"处理全部系统文献"。
- **KB21（P1·间距/布局）过滤行 8 个控件 + 一行混四种信息**：`ProcessingFilters.tsx:14` 同排放搜索表单、状态/切分方式/模型三个 select、「更多筛选」、「清除筛选」、刷新图标；`processing-table.css:5` 用魔法数列 `minmax(180px,1fr) 132px 140px 190px auto auto 34px` 定死。`IngestionPanel.tsx:93` 又把「匹配 N 份」「新加工优先使用文献独立策略」勾选、一句解释、「失败诊断」链接塞进一行。改法：常用筛选保留三个（状态/类型/关键词），其余进「更多筛选」抽屉；`匹配 N 份` 单独一行；把"失败诊断"移到队列区标题右侧。
- **KB22（P2·图标/文案）两条 tab 同图标 + 支持格式裸串**：`RetrievalOptionsPanel.tsx:48` 的「文献加工」和「解析与切分」都用 `Database` 图标；`IngestionPanel.tsx:78` 直接输出「PDF · Word · TXT · … 扫描图片」没有标签词。改法：解析用 `FileSearch`、切分用 `Scissors`（或去掉图标只留文字）；格式行改成「支持格式：PDF、Word、TXT…」。
- **KB23（P2·视觉/细节）队列统计折叠 + 进度条四种高度**：索引统计藏在 `<details>`（`IngestionPanel.tsx:89`），首屏看到的是"当前默认模型：… 384 维 · 索引统计与配置"这行技术串；进度条高度实测 4/5/6/7px 四种（`retrieval.css:34,59,66,84,200`、`processing-monitor.css:24,49`），同屏出现两条粗细不同的条。改法：统计四数（待向量化/已就绪/待重建/失败）放出 `details` 外做成一行 inline stat；进度条统一 6px、`border-radius:3px`。

### 6. 管理端：向量模型 / 切分策略 / Query / 检索诊断 / 评测 / 失败 / 索引历史

**做得对**：参数页的编号分节 + 每字段一行说明（`ChunkStrategyFields.tsx:14-20`、`RetrievalOptionsPanel.tsx:58-73`）把"文档是 100 字符不是 100 token""原句检索跳过改写"这类易错点讲明白；`FailurePanel` 的阶段/原因/批次三层筛选 + 抽屉里的"本次任务配置 + 最后记录位置 + 建议"（`FailurePanel.tsx:48-56`）是全模块信息架构最好的一屏；索引清理有预览→确认→后台任务三态（`IndexHistoryPanel.tsx:14,19-20`）。

**主要问题**

- **KB24（P2·布局/文案）"保存检索配置"出现在三个 tab**：`RetrievalOptionsPanel.tsx:73`、`IngestionPanel.tsx:82`、`QueryProcessingPanel.tsx:7` 各有一个 `.rag-savebar`，文案都叫「保存检索配置」，实际都保存整份 options（父级 `save()`）。用户不知道在哪个 tab 保存才生效。改法：保留一个 sticky 保存条（放页头右侧，复用 `PageHeadingActions`），dirty 时显示"有未保存修改"；tab 内只保留"先测试"类次级动作。
- **KB25（P0·颜色/可达性）暗色主题下大片文字不达 AA，原因是引用了不存在的 token**：`failures.css:3,6` 和 `processing-table.css:1-2` 使用 `--text-muted`、`--surface-soft`，这两个变量在全仓 CSS 中**未定义**（已全量搜索），永远落到亮色 fallback：`color:#526676`（对 workbench 暗色面板 `#242424` 实测 **2.60:1**）、`background:#f4f7f9`（暗色主题里出现浅色表头条带）。同类还有 `retrieval.css:167,169,171` 硬编码 `#a04332`（对 `#2c2c2c` 实测 **2.22:1**）、`#ae3e2d`（对 `#242424` **2.61:1**），以及 `retrieval.css:16` 的 `.rag-save-state.is-dirty{color:#99641d;background:#f8f0df}`（暗色主题里是一块浅黄药丸，对自身底 4.41:1，且与全局色板无关）。改法：全部换成 `--muted`/`--surface-2`/`--danger`（`--danger` 当前也未定义，需在 `shell.css` 补 `--danger:#a8453a`，暗色 `#e5928a`），并在 CI 里加一条"禁止未定义 var 的 fallback 十六进制"规则。
- **KB26（P1·布局/细节）评测页没有空态与层级**：`EvaluationPanel.tsx:21` 首屏就是 select + 无可见 label 的名称 input + 「新建评测集」；没有任何评测集时页面只剩这三个控件（无引导）；结果区把 Recall@10/NDCG@10/MRR@10/应无结果通过率压进一个 `<p>`（`:26`），四个指标权重相同。改法：补"先建评测集 → 标注 3–5 个问题 → 运行"的三步空态；结果区把 NDCG@10 做成一个 28–32px 主数字 + 一句对比（"较上次 +3.2"），其余三个降为 12px 次级行。
- **KB27（P2·细节）数字与单位格式不统一**：`IndexHistoryPanel.tsx:19` 同一行里 `eligible_indexes` 裸数字、`eligible_chunks.toLocaleString()`；`RetrievalOptionsPanel.tsx:77` 融合分 5 位小数（`toFixed(5)`）而相似度 3 位；`IndexHistoryPanel.tsx:8` 用 GiB/MiB，`EmbeddingPanel.tsx:9` 用 GB/MB 描述同一类体积；`EmbeddingPanel.tsx:44` 的索引数裸输出。改法：统一 `Intl.NumberFormat('zh-CN')` + `tabular-nums`；分数一律 3 位或百分比；体积单位只留 GiB/MiB。
- **KB28（P2·细节）折叠嵌套与双动作条**：`KnowledgeWorkbench.tsx:120` 用 `<details class="kb-quality"><summary>高级处理：切分与索引</summary>` 包住 `ChunkProfilePanel`，而后者自身又返回 `<details class="kb-quality"><summary>检索分块预览与配置</summary>`（`ChunkProfilePanel.tsx:17`）——同一功能两次折叠；`DocumentStrategyDialog.tsx:30` 与 `:32` 各放一组 `.modal-actions`（中间一组 + 底部关闭），弹窗有两个动作区。改法：去掉外层折叠，面板直接平铺；弹窗只保留底部一组动作（预览/保存/保存并重建放右侧）。

### 7. 研究助手（单助手/团队、结果与证据、保存笔记）

**做得对**：研究问题与输出形式（研究问答/带出处提纲/段落草稿/资料概述）把"要什么"前置（`ResearchAssistant.tsx:59`）；研究过程按步骤列出并可直接跳到该步的检索依据（`:72-73`）；证据块带 `[证据号] 题名 · 版本 · 页/段` 与「核验原文」（`:77`）；对预算与数据外发的说明不夸大（`:65`）；保存为成果时保证"同一任务只存一份"（`KnowledgeWorkbench.tsx:123`）。

**主要问题**

- **KB29（P0·视觉/字体）研究草稿用"零样式"的 Markdown 渲染器，核心产出没有排版**：`MarkdownPreview.tsx:25` 输出 `<div className="kb-markdown">`，而 `.kb-markdown` 在全仓 CSS 中**没有任何规则**（已全量搜索）。于是：`#`/`##` 标题直出浏览器默认 h1/h2（`2em`=28px 加粗，`:19`），与页面 H1 抢层级；`<pre>` 在非 `.kb-editor pre` 上下文里是默认等宽 13px；列表用字面 "•" 拼成 `<div>• …</div>`（`:21`），复制出来是字符而非列表语义。研究草稿/协同草稿/技能方法预览三处都用它（`ResearchAssistant.tsx:75`、`CollaborationResearch.tsx:56,60`、`AssistantManagement.tsx:68`）。改法：新增 `.kb-markdown` 样式表（正文 15px/1.9、`h1-h3` 映射到 16/15/14px 且 `margin:24px 0 8px`、`ul/li` 用真列表、`pre` 用 `--surface-2` 底 + 13px 等宽），并把 Markdown 标题统一降 3 级（h1→h4）。
- **KB30（P1·文案/状态）"已完成但未核验"时草稿整段不显示**：`ResearchAssistant.tsx:75` 的渲染条件是 `run.state==='succeeded'&&run.verified`，未核验时页面只剩证据列表，没有任何"为什么没有草稿"的说明；对比 `CollaborationResearch.tsx:60` 只判断 `state==='succeeded'`，两处逻辑不一致。改法：草稿区始终渲染，用 12px 提示条区分"引用格式与原文已核验"与"引用未通过核验，草稿仅供校对（N 处需人工确认）"；需与后端确认 `succeeded && !verified` 是否真的会出现。
- **KB31（P1·布局）首次使用没有空态，历史记录只有一个下拉**：`ResearchAssistant.tsx:68` 的"研究记录"未开始时只有一项「选择记录」，没有任何"还没有研究记录/开始第一次研究"的引导；面板窄态下这块是页面最后一个元素，下面全空（截图 `audit-assistant-single.png`）。改法：`runs.length===0` 时换成"还没有研究记录 · 用上面的问题开始"；`runs.length>0` 时改成 3–5 条最近记录列表（问题前 24 字 + 状态点 + 相对时间），"全部记录"进详情。
- **KB32（P2·细节/状态）状态只给数字百分比**：`ResearchAssistant.tsx:69` 是「研究中 · 检索原文 · 40%」纯文本，而队列页已有语义化 `progressbar`（`ProcessingProgress.tsx:84`）；暂停/重试按钮在状态文案右侧（`:70-71`）。改法：复用一个 `<RunStatus>`：状态点 + 进度条（4px）+ 当前步骤，重试/停止放进行尾菜单。
- **KB33（P2·字体/文案）预算与用量是未格式化的裸数字**：`ResearchAssistant.tsx:78`「已预留预算 60000」、`CollaborationResearch.tsx:62`「已预留预算 180000 · 服务商返回用量 123456 token」。改法：千分位 + 单位 + 只保留两个数（本次用量 / 预算上限，用 `12,345 / 60,000` 形式，等宽数字）。

### 8. 助手与 Skill 设置、协作研究、项目助手面板

**做得对**：助手/Skill 配置有草稿-发布-撤销三段式与 dirty 离开确认（`AssistantManagement.tsx:28,64,78`），版本固定绑定、被引用提示（`:69,72`）都在；协作研究把角色与总预算收进折叠区，默认值合理（`CollaborationResearch.tsx:41-45`）；项目助手面板复用同一套表单，嵌入式布局由 `modern.css:452-455` 收窄，不重复实现逻辑。

**主要问题**

- **KB34（P1·可达性）键盘用户无法导入技能包**：`AssistantManagement.tsx:46-47` 用 `<label className="button-secondary">导入 ZIP<input type="file">` 做触发，而 `knowledge.css:7` 是 `.kb-assistant-management input[type=file]{display:none}`——`<label>` 不在 Tab 序列、隐藏的 input 也拿不到焦点，只能用鼠标。改法：改成 `<button>` + 隐藏 input 通过 `ref.click()` 触发（`folderInput` ref 已有，`:19`），或把 input 做成 `sr-only` 而非 `display:none`。
- **KB35（P1·布局）一屏 7 个同级按钮**：`AssistantManagement.tsx:61-67` 保存草稿（primary）/校验技能/用文献测试已保存草稿/发布版本/停用/复制为新项目/删除未发布草稿 排在一行，删除与发布相邻。改法：主区只留「保存草稿」「发布版本」，其余进右侧 `MoreHorizontal`（测试/校验/复制/停用/删除），删除单独分组并用 `--danger`。
- **KB36（P1·文案/细节）要求用户手写 JSON**：`AssistantManagement.tsx:60` 「填写相对路径与文本组成的 JSON」+ `:59` 「固定版本绑定」是 ID 文本域，错误提示也是 "参考文件需为路径与文本组成的对象"（`:30`）。改法：参考文件改成"文件名 + 内容"两列的列表编辑器（可增删行），固定版本绑定用多选列表；JSON 只作为导出格式。
- **KB37（P1·视觉）"版本对照"不是对照**：`AssistantManagement.tsx:79` 把一个历史版本和一个草稿的完整 JSON 并排放进 `<pre>`，用户得自己逐行比对。改法：对 `name/description/instructions/tools/skill_version_ids` 做字段级 diff（改动行 `+`/`-` 底色），JSON 折叠在最后。
- **KB38（P1·视觉）"协作任务关系"没有任何关系**：`CollaborationResearch.tsx:52` 把节点渲染成 `auto-fit minmax(250px,1fr)` 卡片网格（`knowledge.css:12`），依赖只写成一行「等待：A、B」，状态只靠边框颜色（`knowledge.css:14` 的 `--tone-32746b/--tone-b76b49`）。改法：按依赖拓扑排成 2–3 列纵向流程（搜集 → 复核 → 写作），用 1px 连线 + 状态点表达依赖；这正是本项目"专业助手分工"最该有的一个视觉时刻。
- **KB39（P2·可达性/文案）团队模式的正文域没有可见标签**：`CollaborationResearch.tsx:36` 只有 `aria-label="协作研究问题"`，而单助手模式有 `<label className="assistant-question">研究问题`（`ResearchAssistant.tsx:58`），截图里团队模式第一个控件就是无标题文本框。改法：补同一 label；同时把两个模式的「设置 / 个人设置」按钮名统一（`CollaborationResearch.tsx:40` vs `ResearchAssistant.tsx:62`）。

### 9. 本组最重要的 3 个问题

1. **研究助手与协同研究的结果区承载不了核心任务（KB29/KB30/KB31）**：研究草稿是全产品最该被"读"的产出，却用零样式 Markdown 渲染（`.kb-markdown` 无任何 CSS，h1 直出页级标题、列表是字面 •）；未核验时草稿整段消失且无解释；首次使用没有空态，历史只剩一个下拉。优先级 P0，建议按"状态条 → 草稿（带出处签）→ 证据 → 保存"重排结果区。
2. **暗色主题下管理端知识库大面积文字不达 AA，根因是引用了不存在的 token（KB25，附带 KB02）**：`--text-muted`/`--surface-soft`/`--danger` 全仓未定义，`failures.css`、`processing-table.css` 永远用亮色 fallback（实测 2.22–2.63:1），而资料目录选中态还残留紫调 `#302b46`。这是本组唯一可量化的可读性硬伤，修复成本低（一次 token 收口 + 3 个文件替换），应随 Lead 的语义 token 一起做。
3. **"范围"语义与双主操作让检索/加工的可信度下降（KB07/KB20/KB21）**：正文检索的范围下拉同一 value 会随上下文改名（当前分类／目录 vs 整个文献库），管理端加工页同时摆着"处理全部系统文献"和"处理并建立索引"两个 primary，过滤行 8 个控件把"匹配多少、用哪套策略、失败去哪看"压成一行。统一成一个范围控件 + 一条选择/动作栏，是本组信息架构收益最大的一步。

### 10. 做得好的地方

1. **页面说明收进 ⓘ 的模式**：`PageHeading.tsx:19-34`（注释写明 "Persistent instructions belong beside their fields"）——长说明不再占首屏，Escape 可关闭并把焦点还给 summary，是全站最值得推广的组件。
2. **进度与队列语义诚实**：`ProcessingProgress.tsx:33-37` 刻意不让未完成显示 100%（并有注释说明），`:84` 用 `aria-valuetext` 描述"已结束/成功/失败/取消"，`:103-108` 解释每个计数的含义；`:87` 明确写"结束包含失败、取消与跳过，不代表全部成功"——符合 unslop 对"可核验声明"的要求。
3. **危险操作的可控性**：全库加工、取消全部入库、清理历史索引、撤销版本都先给范围 + 成本 + 保留策略再确认（`IngestionPanel.tsx:58,63,68`、`IndexHistoryPanel.tsx:14`、`AssistantManagement.tsx:78`），失败诊断还提供"先重试 5 份"（`FailurePanel.tsx:56`）。
4. **编辑态保护完整**：草稿 dirty 时离开确认/`beforeunload`（`KnowledgeWorkbench.tsx:67`、`AssistantManagement.tsx:28`），未保存的密钥单独标记（`RetrievalOptionsPanel.tsx:31-32`），保存失败保留上次数据并标注（`IngestionPanel.tsx:96`）。
5. **专业细节而非装饰**：繁简归一/原字形检索的差异说明（`CoreLibrary.tsx:92`）、按码点高亮（`:18`）、"文字段号不是原书页码"（`KnowledgeWorkbench.tsx:104`）、"字符数不等于 Token 数"（`ChunkStrategyFields.tsx:17`）——这些说明让界面像给研究者做的。
6. **可达性基础扎实**：`role="progressbar"`、`role="list/listitem"`（`EmbeddingPanel.tsx:38`）、`aria-busy`（`FailurePanel.tsx:53`）、`aria-pressed`（`LibraryDocumentReader.tsx:19`）、`role="alert"/"status"` 在错误/成功态上基本齐全；`theme.css:325` 已处理 `prefers-reduced-motion`，本组唯一动效是开关位移（`retrieval.css:132`）。
7. **失败诊断的信息架构**（`FailurePanel.tsx`）可直接作为其他管理页的模板：原因 chips 带计数 → 表格 → 抽屉（诊断/原配置/最后位置/关联原件）→ 原配置重试预览，没有一处是装饰。

---

## 5. 投稿 / 期刊页面与全局反馈系统评审

> 覆盖论文投稿管理（列表/详情/流程/向导）、中文 C 刊库、英文期刊库、期刊库管理、全局反馈（toast/确认/错误边界/连接）、主题与动效、全站文案 Top 20、可达性抽查。问题编号 FB01–FB60。

评审对象：WSL `/root/wenjin` 当前代码（只读静态审查）；截图证据取自 `.local/unslop-shots/fixture/`（2026-11-05 生成，含 `audit-empty-论文投稿管理.png`、`admin-journals*.png`、`audit-new-paper-step2*.png`、`user-failure.png`）。行号以当前文件中行号为准。分级：P0＝首屏/主路径必须修；P1＝明显影响效率、信任或可达性；P2＝打磨项。

### 0. 覆盖页面与组件清单

| 页面 / 组件 | 入口与关键文件:行 | 首屏任务是否清楚 |
| --- | --- | --- |
| 论文投稿管理（用户） | `App.tsx:423` → `SubmissionWorkbench.tsx:68-98` | 中：标题+5 张统计+看板，但空态和下一步动作弱（FB01） |
| 中文 C 刊库（用户） | `SubmissionWorkbench.tsx:77-83` | 中：筛选条与期刊墙密度高，主操作是“找期刊”但缺选择入口 |
| 英文期刊库（用户/管理员） | `EnglishJournalLibrary.tsx:186-256` | 较好：首屏即筛选+数据表+分页，任务链最短 |
| 期刊库管理（管理员） | `AdminConsole.tsx:65` → `SubmissionWorkbench.tsx:70,131-133` | 好：标题+两项目录计数+目录表，来源可展开 |
| 新建论文向导（3 步） | `SubmissionWorkbench.tsx:91-95`、`Overlays.tsx:66-86` | 中：步骤清楚，但期刊选择是原生下拉（FB26） |
| 论文编辑弹窗 / 详情抽屉 | `SubmissionWorkbench.tsx:88-90` / `110-118` | 详情好；编辑表单字段 10+ 未分组（FB15） |
| 期刊编辑/备注、CSV 导入预览 | `SubmissionWorkbench.tsx:87,97`、`JournalSources:105-109` | 好：带行数预览、指纹去重、来源说明 |
| 全局反馈（toast/确认/错误边界/连接） | `App.tsx:455`、`SystemFeedback.tsx:6-23`、`Pages.tsx:20-38`、`AppDialogs.tsx`、`dialogQueue.ts`、`DeleteDialogs.tsx`、`ServiceConnection.tsx` | 手段齐全但相互重叠，同一错误会讲两遍（FB34-35） |
| 主题与外观 | `appearance.ts:6-12`、`AppearanceSettings.tsx:5`、`theme.css:1`、`shell.css:1-2`、`palette.css:2-3`、`modern.css:2-17` | 能切换，但 token 来源割裂（FB46-47） |

### 1. 论文投稿管理：列表与状态

- **FB01（P0，布局/空状态）** 位置 `SubmissionWorkbench.tsx:84` + `submissions.css:32-34`。现象：0 篇论文时仍渲染 7 条固定 `min-height:300px` 的空泳道，每条写“暂无论文”，并因 `grid-auto-flow:column` / `minmax(220px,1fr)` 在 1480 宽下仍需横向滚动（截图 `.local/unslop-shots/fixture/audit-empty-论文投稿管理.png` 第 7 列被裁掉）。改法：`data.papers.length===0` 时改渲染单个 `Empty`（`Pages.tsx:18`）：“还没有投稿论文”+主按钮“新建论文”+次按钮“从工作成果创建”，不渲染泳道；仅 ≥1 篇时显示看板。
- **FB02（P1，布局/响应式）** 位置 `SubmissionWorkbench.tsx:41`、`:84`。现象：默认 `view='board'`，手机端 7 泳道只能横向拖；`submissions.css:73` 只处理了统计和表单，看板无窄屏策略。改法：`<900px` 默认列表视图（或 `@container workshop (max-width:900px)` 下看板改纵向分组），并给看板加“横向滚动”渐隐提示。
- **FB03（P1，颜色）** 位置 `submissions.css:55-58`。现象：投稿状态色直连旧 `--tone-*`：深色下“已录用/已发表”是 `--tone-c9bfff/--tone-5e537e`（紫），与 portal 青绿 accent 冲突；浅色下 palette 把这些 tone 折叠成 `#32322f/#5d5d57/#dcdcd7`（`palette.css:2`），review/revision/accepted/published 四个状态几乎同灰，语义丢失。改法：在 `shell.css:1-2` 与 `modern.css:3-17` 增加 `--status-review/--status-done/--status-muted` token（浅色 #b3742a / #28805b / #6b7280，深色 #ffe1a0 / #8edbb6 / #9caeba），`submissions.css` 只引用 token。
- **FB04（P1，文案/状态）** 位置 `SubmissionWorkbench.tsx:84`。现象：`const age = p.last_date ? … : 0`，无进展日期的论文显示“最近进展距今 0 天”，把“没有记录”讲成“今天有进展”。改法：改为 `p.last_date ? 距今 N 天 : '尚无进展记录'`；120 天阈值提为常量 `OVERDUE_DAYS` 并在提示里写明“超过 120 天未更新”。
- **FB05（P2，细节）** 位置 `SubmissionWorkbench.tsx:73` + `submissions.css:2`。现象：5 张等宽统计里混入“中文期刊/收藏期刊”两个目录指标，与“全部论文/审稿与返修/录用与发表”不同维度；`stats` 又取全部未归档论文，不随筛选/归档变化（`:64`）。改法：前 3 张论文指标一排、后 2 张并入中文 C 刊库 tab 头部（可复用 `:70` 的 `catalog-summary` 结构），统计卡加 `font-variant-numeric:tabular-nums`。
- **FB06（P2，文案/逻辑）** 位置 `SubmissionWorkbench.tsx:84`。现象：空提示判断用 `!data.papers.length`，用户勾选“已归档”后若归档论文非空但当前筛选为空，会误显示“从新建论文开始”的引导。改法：用过滤后的 `papers.length` 判断并区分“没有符合条件的论文”。
- **FB07（P2，细节/可达性）** 位置 `SubmissionWorkbench.tsx:84`。现象：卡片把 `h3/p/progress` 放进 `<button>`（HTML 内容模型不合法）；`<progress>` 无 `aria-label`。改法：外层 `article` + 覆盖式 `button`（或 `div` + `role="button"`），`progress` 加 `aria-label={`写作进度 ${p.progress}%`}`。
- **FB08（P2，细节）** 位置 `SubmissionWorkbench.tsx:84`、`submissions.css:22-58`。现象：泳道类 `lane-${s}` 全站无对应 CSS（死类名）；状态 pill 用 `999px`、卡片 7px、面板 10px、来源块 6px，半径语言不统一；`图标 12/13/14/15/16/17px` 混用（`:65-66,69,81-82,103,132`）。改法：删 `lane-*` 或补语义；半径收敛为控件 8 / 卡片 12 / 面板 14；图标统一 16（次要 14）。

### 2. 论文投稿管理：详情与流程

- **FB09（P1，反馈）** 位置 `SubmissionWorkbench.tsx:59,96`（`setNotice('投稿进展已记录')`）→ 渲染在 `:72` 页面顶部；抽屉遮罩 `z-index:120`（`overlays.css:2`）压在其上。现象：用户在“论文详情”抽屉里记录进展成功后看不到任何确认。改法：操作反馈进抽屉（footer 上方 `role="status"` 条），或复用全局 toast `App.tsx:455`。
- **FB10（P1，布局/层级）** 位置 `SubmissionWorkbench.tsx:114` + 状态机 `:27`。现象：review 状态 4 个动作（收到返修/收到录用/收到拒稿/撤回投稿）全为同款 `button-secondary`，无主次；“撤回投稿”与“收到录用”危险度相反却同级。改法：最常用动作 primary（收到返修），其余 secondary，撤回投稿改 `button-quiet`+危险确认；或收进“记录进展”下拉，保留一个主按钮。
- **FB11（P2，细节/去重）** 位置 `SubmissionWorkbench.tsx:116`。现象：同一组 `paper.events` 先按 attempt 渲染时间线，末尾“完整操作记录”再全量列一遍，信息重复。改法：时间线合并系统事件，`details` 只保留 attempt 之外的事件或删除。
- **FB12（P2，文案/校验）** 位置 `SubmissionWorkbench.tsx:115`。现象：“实际日期”只设 `max={today()}`，未校验早于上次投稿/事件日期；说明字段 placeholder 很长。改法：`min` 取上次事件日期或给“早于上次进展”提示；placeholder 收到一句（“审稿意见、稿件编号、返修要求”）。
- **FB13（P2，细节）** 位置 `SubmissionWorkbench.tsx:113`。现象：`paper.notes` 用 `submission-preserve` 全文平铺，长备注把操作区推远。改法：4 行 clamp + “展开全文”。
- **FB14（P2，空状态）** 位置 `SubmissionWorkbench.tsx:116`。现象：“尚未正式投稿”只有一句话，没有“打开投稿地址/记录进展”的下一步。改法：空态给一个“记录第一次投稿”按钮。
- **FB15（P2，层级）** 位置 `SubmissionWorkbench.tsx:88-90`。现象：编辑弹窗一屏 10 个字段（题目/作者/学科/优先级/项目/期刊/阶段/进度/备注/归档）无分组标题。改法：分三组“论文信息 / 投稿目标 / 归档与备注”，组间 24px、组内 12px。

### 3. 期刊库（中英文目录、筛选、详情）

- **FB16（P1，可达性）** 位置 `EnglishJournalLibrary.tsx:282`。现象：整行 `<tr onClick>` 展开，无 `tabIndex`/键盘处理；`aria-selected` 挂在 `tr` 上不符合 ARIA 表格语义。改法：以行内“详情”按钮（`:307` 已有 `aria-expanded/aria-controls`）为唯一展开入口，去掉整行点击，或补 `tabIndex=0`+Enter/Space 并移除 `aria-selected`。
- **FB17（P1，颜色/主题）** 位置 `english-journals.css:39-44` + `palette.css:2`（浅色映射）与 `modern.css:186-189`（管理员视图固定浅底）。现象：浅色主题 Q1-Q4 四档色全退化为灰，分区色码不可读；管理员视图 tier2-4 固定 `#edf3fc/#fcf5e7/#fcf0ed`，深色下变成浅底 pill。改法：定义 `--ej-tier-1..4` 前景/背景 token（浅色深一档、深色浅一档），前景对比 ≥4.5:1；管理员覆盖只保留布局不改色。
- **FB18（P2，文案/去重）** 位置 `SubmissionWorkbench.tsx:70,73`、`EnglishJournalLibrary.tsx:215,247,252`。现象：同一目录总数出现 4 次（页头/统计“935 种期刊”、行内“命中 N 种”、分页“共 N 种”、右下“库内 N 种”）。改法：只留分页行“第 x / y 页 · 共 N 种”，筛选区只显示“筛选后 N 种”。
- **FB19（P2，可达性/焦点）** 位置 `english-journals.css:6-7`。现象：`.ej-search input:focus-visible{outline:none}`，聚焦只剩 1px 边框变色，不满足“至少 2px 周长的可见焦点”；而 `modern.css:147` 给 `submission-search` 的是 2px outline。改法：`.ej-search:focus-within{outline:2px solid var(--portal-accent);outline-offset:2px}`，与 submission-search 保持一致。
- **FB20（P2，文案/信任）** 位置 `EnglishJournalLibrary.tsx:82`、`:219-223`。现象：数据缺失时提示“请确认后端已放置 data/english_journals.json.gz，并重启本地服务”，把部署路径暴露给普通研究者；且同时触发全局错误浮层（FB34）。改法：主文案“期刊数据未能加载，请稍后重试”，路径类信息放“错误详情”折叠；`重试` 按钮保留。
- **FB21（P2，移动端）** 位置 `modern.css:157`（`min-width:730px`）。现象：管理员目录表在 430px 屏只露出“期刊名称/学科门类”两列（截图 `admin-journals-mobile.png`），等级/链接/编辑需横滚。改法：`<760px` 切换为卡片行（名称+等级+编辑），或只保留三列并用详情抽屉承载链接。
- **FB22（P2，可达性/细节）** 位置 `SubmissionWorkbench.tsx:132`。现象：管理员目录 `<th>` 无 `scope="col"`；同一文件 `:122` 的论文表有 `scope`，不一致。改法：全部补 `scope="col"`。
- **FB23（P2，布局/细节）** 位置 `submissions.css:22,28`。现象：`.journal-note` 固定 `min-height:225px` + 备注 3 行 clamp，内容少的卡片底部大片空白，内容多的信息被截断。改法：`min-height:0`，标题 2 行 clamp，底部元信息 `margin-top:auto`。
- **FB24（P2，文案/细节）** 位置 `JournalSourceDetails.tsx:19-21`、`SubmissionWorkbench.tsx:79`。现象：摘要按钮写“来源与版本”，展开内容是“目录来源与导入记录”；位置用 JS `translate` 掰正（`:5-12`）。改法：摘要改“目录来源”，弹层改用 CSS 右对齐/顶部锚定，去掉 `translate` hack。
- **FB25（P2，空状态）** 位置 `EnglishJournalLibrary.tsx:239`。现象：空态文案让用户“点重置筛选”，但按钮在筛选区上方需回滚；无数据与无筛选结果共用一格。改法：无筛选结果时在空行内放“重置筛选”按钮，短句不带句号：“暂无匹配期刊”。

### 4. 投稿/期刊相关弹窗与选择器

- **FB26（P1，交互）** 位置 `SubmissionJournalPicker.tsx:34-40`。现象：目标期刊是全量原生 `<select>`（`data.journals` 935 条，见 `SubmissionWorkbench.tsx:70`），没有学科/等级筛选，“查找期刊”输入只影响 option 列表；用户必须先知道刊名。改法：改为搜索式 combobox（输入 → 结果列表 ≤50 条，显示等级/学科，收藏与最近使用置顶），下拉面板复用期刊墙的 info 结构。
- **FB27（P1，组件一致性）** 位置 `app-dialogs.css:1-5`、`theme.css:315-319`、`portal.css:37`。现象：站内并存三套弹窗外观：原生 `dialog`（半径 14、危险色 #a83832、底部灰条）、自绘 `modal-panel`（半径 10）、`portal-dialog`（半径 12）；截图 `user-failure.png` 里“放弃未保存的修改?”与背后的新建论文弹窗一眼可辨是两套东西。改法：确认框统一走一种实现（推荐原生 `<dialog>`，复用 `modal-panel` 的 token 与圆角），删除第三套。
- **FB28（P2，文案）** 位置 `dialogQueue.ts:33`。现象：标题“放弃未保存的修改?”+描述“尚未保存的修改会被丢弃。”同义重复。改法：描述写具体后果（“离开后本次填写不会保存，已记录的信息不受影响”），或删描述。
- **FB29（P2，反馈去重）** 位置 `SubmissionWorkbench.tsx:72` 与 `:87,90,97,116`。现象：同一条 `error` 在页面顶部 banner 和表单内各渲染一遍。改法：表单错误只留在表单内，页面 banner 只承担加载失败。
- **FB30（P2，状态）** 位置 `SubmissionWorkbench.tsx:97`。现象：提示“该文件已经导入，重复确认不会新增记录”后，主按钮仍是“确认导入”且可点。改法：`already_imported` 时主按钮改“完成/关闭”，或直接禁用并说明。
- **FB31（P2，细节）** 位置 `Pages.tsx:20-38` vs `Overlays.tsx:15-16`。现象：Drawer 打开会锁 `body.overflow`，Modal 不锁，长页面弹窗背后仍可滚动。改法：Modal 同步锁滚动（或用 `<dialog>` 顶层）。
- **FB32（P2，反馈）** 位置 `SubmissionJournalPicker.tsx:36-37`。现象：输入筛选后没有“N 个匹配/无匹配”的反馈，只能从 select 选项数猜。改法：输入下方加一行 `role="status"` 的“匹配 N 种”，无匹配时给“放宽关键词”提示。
- **FB33（P2，文案）** 位置 `SubmissionWorkbench.tsx:91-95`。现象：向导分 3 步，但期刊“可稍后选择”没有说明；步骤 2/3 里“论文阶段/写作进度”与步骤 1 的“题目与作者”关系不直观。改法：步骤 2 加一句“目标期刊可稍后在详情里修改”，步骤标题改为动作短语。

### 5. 全局反馈系统（toast、确认框、错误边界、加载/空状态）

- **FB34（P0，反馈/信任）** 位置 `transport.ts:64` + `SystemFeedback.tsx:9-17` + 页面内联错误（`SubmissionWorkbench.tsx:72`、`EnglishJournalLibrary.tsx:219`）。现象：`request()`→`call()` 每次失败都会派发 `wenjin:request-error`，全局右上角弹一条持久错误；页面同时 `setError` 在自己的区块再展示一次，同一错误讲两遍，且两处措辞不同。改法：默认只保留页面内联错误，全局层只处理未捕获异常和 401/503；或在 `reportFailure` 增加 `silent` 参数，由页面显式声明“已在页内处理”。
- **FB35（P1，布局/反馈）** 位置 `theme.css:313`（`.toast top:62px`）、`overlays.css:64`（`.toast z-index:130`）、`systemFeedback.css:1`（`top:20px; z-index:4000`）、`modern.css:198`（portal 标题栏 64px）、`desktop-density.css:35`（窄屏 56px）。现象：两个顶部浮层可以同时出现并互相覆盖；全局错误浮层压在 64px 标题栏上，挡住右上角账号/研究助手/主题按钮。改法：收敛为单一 feedback stack：`position:fixed; top:calc(var(--titlebar-h,64px) + 12px); right:24px; display:grid; gap:8px;` 最多 3 条，全局 toast 与页面 toast 共用一个容器。
- **FB36（P1，颜色/主题）** 位置 `systemFeedback.css:1`（`#edc7bd` 边框、`#bc4834` 图标、`#10282f26` 阴影）、`app-dialogs.css:5`（危险色 `#a83832`）。现象：全局错误浮层与危险按钮不随主题切换，深色模式下出现浅粉边框；与 portal 的 `--portal-error` token（`portal.css:2`）无关联。改法：新增 `--danger/-line/-bg` token（浅 #b4483c/#e9c8c0/#fff3ef，深 #ffb4a8/#915958/#392526），浮层全用变量。
- **FB37（P2，反馈）** 位置 `App.tsx:66`。现象：成功/失败用中文正则嗅探（`/已|成功|完成|保存|复制|导出/` 且排除失败词），文案一改就错色，混合结果（“已导入 3 份；2 份失败”）只能走一种。改法：`setToast({kind:'success'|'error', text})` 显式传类型。
- **FB38（P2，可达性/细节）** 位置 `SystemFeedback.tsx:17`、`systemFeedback.css:1`。现象：全局错误无自动收起、无 Esc；关闭按钮 24×24。改法：关闭按钮 32×32、支持 Esc、非诊断类错误 8s 自动收起（“错误详情”默认折叠保住信息）。
- **FB39（P2，一致性）** 位置 `SubmissionWorkbench.tsx:75`（骨架 `catalog-loading` + `modern.css:192-194`）、`EnglishJournalLibrary.tsx:226`（文本 `ej-status`）、`Pages.tsx:116`（`page-loading` spinner）。现象：三种加载样式并存、三套节奏。改法：统一“骨架（列表/表）/文本状态（局部刷新）/空态组件”三档，并规定最小显示时长。
- **FB40（P2，文案）** 位置 `SubmissionWorkbench.tsx:75`。现象：论文 tab 加载失败也显示“期刊目录暂未加载/正在读取期刊目录…”，与当前视图不符。改法：按当前 tab 输出“投稿数据/期刊目录”。
- **FB41（P2，一致性）** 位置 `Pages.tsx:18`（`Empty` 组件）vs `SubmissionWorkbench.tsx:83,121`（裸 `<p className="empty-block">`）vs `EnglishJournalLibrary.tsx:239`（`td` 文本）。现象：空态样式与动作规格不统一。改法：全部走 `Empty`（图标+标题+最多一个动作），无动作时也保留标题层级。
- **FB42（P2，行为）** 位置 `SubmissionWorkbench.tsx:47`（`beforeunload`）与 `:47,53`（`confirmDiscard`）。现象：未保存离开同时有浏览器原生对话框和站内确认框两条路径，文案不同但含义相同。改法：保留站内确认作为主动作，`beforeunload` 仅在跨标签/刷新时兜底，文案对齐。
- **FB43（P2，容错）** 位置 `SystemFeedback.tsx:19-23`、`main.tsx:16`。现象：错误边界只包根节点，任一局部渲染异常都会替换整页；`componentDidCatch` 为空，不记录。改法：按页面/路由包边界，错误页提供“刷新当前页”，并上报一次。
- **FB44（P2，空状态/文案）** 位置 `ServiceConnection.tsx:22,37`。现象：首次会话检查失败（服务未起）时，页面既提示“服务暂时无法连接”又要求输入连接凭据，用户会去翻凭据而不是重启服务。改法：无网络/503 时先给“重试连接”，凭据框仅在返回 401 时出现。
- **FB45（P2，反馈）** 位置 `SubmissionWorkbench.tsx:43,72`。现象：绿色 `notice` 无自动消失，只有切 tab/下一次操作才清；抽屉里通知也不可见（FB09）。改法：4s 自动收起并复用统一 toast。

### 6. 主题与动效（浅色/深色、reduce-motion、过渡）

- **FB46（P1，颜色/主题一致性）** 位置 `shell.css:2`（`#1e1e1e` 独立壳）、`modern.css:14`（`#101c25` 浏览器工作区）、`overlays.css:165`（`#142e31` 管理侧栏）、`portal.css:2`（`#171c19` 遗留 `:root`）。现象：深色主题存在 4 个不同底色，`/app` 与 `/admin` 之间切换时中性色跳变（截图中管理侧栏偏青、内容区偏海军蓝）。改法：确定单一深色 ramp（建议 `--bg #0f1a22 / --surface #16242c / --line #2a3b47`），删除 `overlays.css:165-177` 的硬编码，`portal.css:2` 只留 alias。
- **FB47（P1，颜色/主题）** 位置 `theme.css:1,10,11,64,119,236`。现象：`theme.css` 仍把 accent/焦点环/选中态指向紫系（`#b7acff/#6351ae/#c9beff/#51459a`），而 portal 体系是青绿（`modern.css:4,15`）；桌面本地模式整站发紫、浏览器模式青绿，同一产品两副面孔。改法：`theme.css` 中紫系 tone 全部替换为 `var(--portal-accent)`/语义 token，焦点环统一。
- **FB48（P2，颜色/主题）** 位置 `english-journals.css:33-35` + `palette.css:2`。现象：浅色主题下 `.ej-row:hover > td` 用 `--tone-20232c`→`#fafaf9`，与行底 `#fff` 几乎无差；展开态 `--tone-252936` 同样失效。改法：hover/选中用 `color-mix(in srgb, var(--portal-accent) 6%, var(--surface))`，并保留 1px 内描边。
- **FB49（P2，颜色/可达性）** 位置 `modern.css:187-188`。现象：管理员英文库 tier2/3/4 pill 是 11px 文字，`#9c752f on #fcf5e7`≈3.9:1、`#aa6c64 on #fcf0ed`≈3.8:1，低于 AA 4.5:1。改法：前景加深（#7a5a1f / #8a4f47）或字号提到 12px/600。
- **FB50（P2，动效/可达性）** 位置 `modern.css:332-333`、`overlays.css:28-31,41,68`、`english-journals.css:33`、`theme.css:325`。现象：reduce-motion 在浏览器 portal 内覆盖较全，但桌面模式仍会跑 `.ej-row`、`.settings-nav-item`、`.skip-link` 的过渡，规则是按选择器点名维护、容易漏。改法：在 `shell.css` 增加全局兜底 `@media (prefers-reduced-motion: reduce){*,*::before,*::after{animation-duration:.01ms!important;transition-duration:.01ms!important}}`。
- **FB51（P2，动效）** 位置 `overlays.css:14`。现象：向导进度条动画 `width`（布局属性），弱机可能抖动；全站其余动效克制（drawer 200ms、仅加载用 spin、无 `transition-all`/`hover:scale`/循环动画）。改法：改为 `transform:scaleX()` + `transform-origin:left`。
- **FB52（P2，细节）** 位置 `theme.css:1` 与 `shell.css:1` 都声明 `:root` 的 `color-scheme`（dark/light），当前靠 `main.tsx:6-8` 的加载顺序才正确；`theme.css:11` 与 `shell.css:3` 的 `::selection` 也分别紫/绿。改法：`color-scheme` 只在 `[data-theme]` 上声明，选择色统一 token。

### 7. 全站文案问题 Top 20（含位置与改法）

| # | 位置 | 现状 | 问题 | 改法 |
| --- | --- | --- | --- | --- |
| 1 | `WebEntry.tsx:57` | “首次默认账号：admin / admin123” | 在公开登录页暴露默认口令 | 删除；改“初始密码请联系管理员” |
| 2 | `WebEntry.tsx:57` | “欢迎回到问津” | 问候式大标题，不符 unslop | 标题写任务：“登录研究工作区” |
| 3 | `SubmissionWorkbench.tsx:84` | “最近进展距今 0 天” | 无日期被讲成今天有进展（FB04） | “尚无进展记录” |
| 4 | `SubmissionWorkbench.tsx:70,73,74` | “中文 C 刊目录 / 中文期刊 / 中文C刊库” | 同一对象四种叫法 | 统一“中文 C 刊库”（tab）/“中文 C 刊”（计数） |
| 5 | `SubmissionWorkbench.tsx:75` | “期刊目录暂未加载” | 论文页加载失败也这么说 | 按 tab 输出“投稿数据暂未加载” |
| 6 | `SubmissionWorkbench.tsx:78` | “筛选结果 80 种 · 已展示 80 种” | 两项相等时冗余 | “80 / 935 种”或只留“80 种” |
| 7 | `EnglishJournalLibrary.tsx:215,247,252` | “命中 N 种 / 共 N 种 / 库内 N 种” | 同一数字 4 处 | 合并到分页行 |
| 8 | `EnglishJournalLibrary.tsx:82` | “data/english_journals.json.gz…” | 用户读到部署路径 | “期刊数据未能加载，请稍后重试” |
| 9 | `EnglishJournalLibrary.tsx:239` | “英文期刊库暂无数据。” | 短句带句号且无动作 | “暂无英文期刊数据”+重试 |
| 10 | `SubmissionWorkbench.tsx:97` | “该文件已经导入，重复确认不会新增记录。” | 与“确认导入”按钮矛盾 | 按钮改“完成/关闭” |
| 11 | `SubmissionWorkbench.tsx:96` | “投稿进展已记录” | 提示在抽屉外不可见（FB09） | 提示进抽屉 |
| 12 | `SubmissionWorkbench.tsx:66,88` | “论文阶段筛选 / 论文阶段” | 与看板列语义（状态）混用 | 统一“投稿阶段” |
| 13 | `SubmissionWorkbench.tsx:79` | “目录来源与导入记录（N）” | 外层摘要却叫“来源与版本” | 摘要改“目录来源” |
| 14 | `SubmissionWorkbench.tsx:83` | “没有符合条件的期刊。可调整筛选条件或导入目录。” | 无导入入口定位（按钮在别处） | 空态内给“重置筛选/导入目录”按钮 |
| 15 | `dialogQueue.ts:33` | “放弃未保存的修改?”+“尚未保存的修改会被丢弃。” | 标题与描述同义 | 描述写具体后果 |
| 16 | `SystemFeedback.tsx:10` | “请检查当前页面的提示” | 指向不明，用户不知道看哪 | “操作未完成，请重试或刷新页面” |
| 17 | `DeleteDialogs.tsx:10-11` | “将删除该项目的…以及项目内的拆分、调试和执行记录。” | 一句塞 4 类对象 | 数量用 `details` 事实行，正文一句话 |
| 18 | `ServiceConnection.tsx:22,37` | “资料服务 / 后台服务 / 服务” | 同一后端三种称呼 | 统一“资料服务” |
| 19 | `App.tsx:455` | “已导入 3 份；2 份失败” | 成功与失败混在一个 toast | 成功 toast + 失败明细分开 |
| 20 | `EnglishJournalLibrary.tsx:51` | “JCR 2025（Clarivate 2025 版，2026 年 6 月发布）…” | 版本声明硬编码在代码里，会与 `meta` 漂移 | 用 `data.meta.label/generated_at` 渲染 |

### 8. 可达性抽查（焦点、键盘、ARIA）

- **FB53（P1，ARIA）** 位置 `SubmissionWorkbench.tsx:74`：`role="tablist"`+`role="tab"` 已写，但无 roving tabindex、无 ←/→ 切换、无 `aria-controls`/`role="tabpanel"`；而设置页 `App.tsx:437` 实现了 `tabIndex`/方向键。改法：补全 tab 模式，或降级为 `aria-pressed` 按钮组。
- **FB54（P1，键盘）** 位置 `EnglishJournalLibrary.tsx:282`：整行可点但不可聚焦（详见 FB16），键盘用户只能通过“详情”按钮展开——保留按钮即可，但应移除行点击的误导或补齐键盘等价。
- **FB55（P1，焦点可见）** 位置 `english-journals.css:6`、`submissions.css:52`：前者清掉 outline 只留 1px 边框；后者 `.paper-row:focus-visible{outline:none}` 仅靠左侧 2px 阴影，焦点环与全站 `modern.css:19` 的 2px accent 不一致。改法：统一 `outline:2px solid var(--portal-accent); outline-offset:2px`。
- **FB56（P2，ARIA）** 位置 `SubmissionWorkbench.tsx:132`：表头缺 `scope`（对比 `:122`）；`:84` 的 `progress` 无名称。
- **FB57（P2，错误播报）** 位置 `DeleteDialogs.tsx:14,31`：删除失败用 `<p className="persistent-error">` 渲染，无 `role="alert"`；`:22` 的禁用按钮用 `title` 解释原因，键盘/读屏不可达。改法：错误加 `role="alert"`，原因放进可见文案。
- **FB58（P2，组件）** 位置 `AppearanceSettings.tsx:5`：`role="radiogroup"`+`role="radio"` 无方向键导航（三张卡片要 Tab 三次）。改法：补 ←/→/Home/End 或改 `aria-pressed` 按钮组。
- **FB59（P2，控件尺寸）** 位置 `systemFeedback.css:1`（关闭 24×24）、`app-dialogs.css:3`（30×30）、`theme.css:70`（32×32）。改法：统一 32×32（触屏 36）。
- **FB60（P2，细节）** 位置 `AppDialogs.tsx:15`：原生 `<dialog>` 已有 `aria-modal`，再手写 `aria-modal="true"` 属冗余；`:17` 关闭按钮文案固定“关闭确认弹窗”，建议随 tone 改“取消/关闭”。（焦点进入、Esc、`autoFocus` 处理是对的，见 Section 10。）

### 9. 本组最重要的 3 个问题

1. **全局反馈体系在叠加而不是在收敛（FB34/35/36）。** 同一失败既出现在页内又出现在右上角持久浮层，两个顶部浮层还压在 64px 标题栏上，且颜色硬编码不随主题。这是每一页都会遇到的第一层“不可信感”，应最先改成单一 feedback stack + 页内错误优先。
2. **投稿管理首屏与状态语义同时失效（FB01/FB02/FB03）。** 新用户第一屏是 7 条空泳道+横向滚动；状态色在浅色主题退化成灰、深色主题残留紫色，与全站青绿体系冲突。“投稿管理”是否像一个可用的工具，取决于这两处。
3. **期刊主路径的操作效率与可达性（FB26/FB16/FB17）。** 新建论文用 935 条原生 select 选期刊，英文期刊表不能键盘展开，分区色码在浅色下不可读——这是投稿流程的核心动作，应改为可搜索、可键盘、色码可读的选择与浏览体验。

### 10. 做得好的地方

- **领域分寸感**：多处明确“只记录进展、不代投”（`SubmissionWorkbench.tsx:115`）、数据来源与版本声明（`SubmissionWorkbench.tsx:79`、`EnglishJournalLibrary.tsx:216`）、删除前备份与范围说明（`DeleteDialogs.tsx:10-11,26-28`）——是研究工具该说的话，不是营销话术。
- **可核查的数据导入**：CSV 预览给出总数/新增/已有/错误行/指纹去重（`SubmissionWorkbench.tsx:60,97`），英文库做了 300ms 防抖、旧响应竞态丢弃、页码越界静默修正（`EnglishJournalLibrary.tsx:104-152`），细节工程很扎实。
- **错误文案的质量**：`errors.ts:13-19` 与 `transport.ts:16-19` 按状态码给出具体下一步，不甩锅、不说“未知错误”；`EnglishJournalLibrary.tsx:81-85` 的分类逻辑本身正确（只是不该把开发路径直接给用户）。
- **焦点与键盘基础**：Drawer/Modal/Wizard 都有 Esc、Tab 焦点圈、打开记录/关闭恢复（`Overlays.tsx:10-37`、`Pages.tsx:24-35`），并且用 `isAppDialogOpen()` 让下层弹窗让路（`Pages.tsx:29`、`Overlays.tsx:20`）；还提供 skip-link（`App.tsx:401`）。
- **动效克制**：全站无 `transition-all`、无 hover 放大、无循环装饰动画，只有抽屉 200ms、加载 spin，且 portal 与抽屉都有 reduce-motion 处理（`modern.css:332-333`、`overlays.css:28-31`）。
- **界面语言干净**：图标统一 lucide，全站无 emoji、无感叹号、无“一键/无缝/颠覆”类词（仅 `VolumeCompare.tsx:3` 的正则里出现标点字符）；空态、错误态、加载态在关键页面都有实现。

---

## 6. 汇总修改路线图

> 把四组评审去重后按依赖排序。**先系统收口，再页面重构，最后打磨**：token 与字号间距是所有页面改动的公共底座，先做一次，后面每条页面建议才不用各自打补丁。每个条目末尾括号内是分报告的编号。

### 6.1 P0：本周内（安全、数据风险、首屏不可用、全局体验）

| 顺序 | 工作项 | 位置 | 改法 | 验收 | 来源 |
| --- | --- | --- | --- | --- | --- |
| 1 | 删除登录页默认口令提示 | `WebEntry.tsx:57` | 删除 `首次默认账号：admin / admin123` 一行；仅在「尚未初始化账号」时显示引导 | 未登录页不再出现任何口令；初始化流程仍可完成 | PA01 |
| 2 | 语义 token 收口（一次性） | `shell.css:1-2`、`modern.css:2-17`、`theme.css`、`palette.css`、`failures.css:3,6`、`processing-table.css:1-2`、`knowledge.css:31`、`submissions.css:55-58` | 按第 1 章 A.5-1 建一套 `--bg/--surface/--line/--text/--text-2/--accent/--status-*`；补 `--text-muted/--surface-soft/--danger` 或改用既有 token；`theme.css` 的状态规则改引用 `--status-*`；删除 `--tone-*`（过渡期保留 40 行映射） | 浅色下「待复核/已审定/待准备」三色可区分；暗色管理端文字 ≥4.5:1；全站不再出现未定义变量 | WB01/02、KB25、FB46/47、PA09、S1–S5 |
| 3 | 移动端标题栏与断点 | `modern.css:197-229,389-391`、`App.tsx:402` | 「隐藏按钮文字」断点从 380px 提到 560px；统计/表格类横向 chrome 在 760px 收为图标或行内；账号只留首字圆 | 430px 截图无横向溢出、无文字被裁 | WB08 |
| 4 | 全局反馈收敛 | `transport.ts:64`、`SystemFeedback.tsx:9-17`、`theme.css:313`、`overlays.css:64`、`systemFeedback.css:1` | 页内错误优先，全局只接 401/503/未捕获异常；单一 feedback stack：`top:calc(标题栏高 + 12px)`、最多 3 条、显式 `success/error` tone（`setToast(msg, tone)`）；错误浮层全用语义 token | 同一失败只出现一次；浮层不遮挡账号/助手/主题按钮；深浅色一致 | FB34/35/36/37、WB40 |
| 5 | 研究助手结果区重排 | `MarkdownPreview.tsx:19-25`、`ResearchAssistant.tsx:68,75`、`research.css` | 给 `.kb-markdown` 补排版（h1 18/600、h2 15/600、正文 14/1.75、列表用 `ul` 真列表、引用块左线、代码块底色）；未核验时显示草稿 + 顶部状态条（而非整段隐藏）；首次进入给 `Empty`（示例问题 ×3 + 选择范围）；结果区顺序：状态条 → 草稿 → 证据 → 保存 | 草稿可读、层级不与页面 H1 打架；未核验有解释；首次使用有入口 | KB29/30/31 |
| 6 | 项目配置弹窗危险区分层 | `ResearchViews.tsx:40` | 「保存」固定右下 primary；「归档」「删除」移入独立「危险区域」（12px caption + 说明 + danger 按钮）；删除仍要求输入项目名 | 保存与删除不再相邻；误点成本为零 | WB39 |
| 7 | 投稿空态与看板 | `SubmissionWorkbench.tsx:84`、`submissions.css:32-34` | 0 篇时只渲染 `Empty`（标题 + 一句 +「新建论文」主按钮 +「从工作成果创建」次按钮）；≥1 篇才显示泳道；`<900px` 默认列表视图；顺带修 `p.last_date ? 距今 N 天 : 尚无进展记录` | 0 篇首屏无横向滚动；手机默认可用；无「距今 0 天」 | FB01/02/04 |

### 6.2 P1：两周内（系统参数、核心页面信息架构、可访问性）

**A. 设计系统参数（做完 A 再逐页改）**

| 工作项 | 位置 | 改法 | 验收 | 来源 |
| --- | --- | --- | --- | --- |
| 字号阶梯与字重 | 全站 CSS | 7 级 `28/22/17/15/14/13/12`；UI 正文 14、元信息 12；字重 400/500/600（550→500、650→600）；中文大标题字距 0 | 字号种类 ≤8；无 <12px 正文；标题字重 ≤600 | S6/S7、WB04、PA10 |
| 4px 间距与控件高度 | 全站 CSS | `--space-1..8`；卡片 20/24、页面 24/32、列表行 32–36；控件 32/36/40 | 95% 间距值落在 4px 网格；控件高度 ≤4 种 | S10、WB05 |
| 圆角与嵌套 | 全站 CSS | 4/8/12/16 四档；内圆角 = 外圆角 − padding（≥4）；删 5/7/9/11/13/15 | 圆角种类 ≤5 | S11、WB06 |
| 阴影与玻璃 | `modern.css:22,31,64,70`、`portal.css:37`、`app-dialogs.css:2` | 卡片去阴影用 hairline；浮层 1 级；弹窗 1 级（`0 24px 60px rgba(24,32,36,.18)`）；backdrop blur 只留弹窗遮罩一档 | 阴影 ≤3 种；彩色阴影 0 | S4/S5、FB36 |
| 图标规范 | 全局 tsx | 16/18 两档 + `strokeWidth={1.5}`；装饰图标 `aria-hidden`；14 处 `→` 字符改 `ArrowRight`；技能/状态图标统一 muted | 图标尺寸种类 ≤3；无字符箭头当图标 | S8/S9 |
| 对比度与控件边界 | `modern.css:3`、`portal.css`、`shell.css:3` | `--portal-muted:#5b6a78`；`--accent-on-soft:#116b60`；新增 `--control-border:#b9c6cf` 给输入框/勾选（≥3:1） | 12px 文本 ≥4.5:1；控件边界 ≥3:1 | S23、WB09、FB49 |
| 动效收敛 | `overlays.css:14,41`、全局 | 两时长（.12/.2s）+ 一缓动；只过渡 color/bg/border/opacity/transform；进度条 `transform:scaleX()`；全局 reduced-motion 兜底 | 无布局属性动画；reduce 下动画关闭 | S14/S15、FB50/51 |
| z-index 与 !important | `portal.css:37`、`modern.css:109,458`、`systemFeedback.css:1` | 6 级 z 阶梯（10/40/60/80/100/120）；清除颜色类 `!important` | z-index 值 ≤6 种；`!important` ≤8 处 | S12/S13 |

**B. 核心页面信息架构**

| 工作项 | 位置 | 改法 | 验收 | 来源 |
| --- | --- | --- | --- | --- |
| 材料阅读页减负 | `App.tsx:417`、`desktop-density.css:34-37`、`theme.css:376`、`viewState.ts:8` | materials 页不渲染 `.topbar`；返回/面包屑并入文档头；文档头压到 64–72px；正文 `max-width:42em;margin-inline:auto`；处理范围默认跟随当前页，面板顶部显示「将处理：第 X–Y 页」 | 正文可见高度 ≥ 一屏 70%；行宽 36–44em；默认范围与当前页一致 | WB16/17/18 |
| 研究编辑器主操作 | `ResearchViews.tsx:162-164` | 模式切换改 tab（下划线/浅底），去掉 `button-primary`；保留自动保存 + 状态文字；手动动作改「另存版本」；主按钮只留「人工审定」 | 一屏一个实心主按钮；保存语义唯一 | WB22/23 |
| 点校流程与调试页 | `ResearchViews.tsx:45-51,60`、`PreprocessViews.tsx:14,77-85`、`CollationDebug.tsx:87-94` | 阶段名降为分组标题，消除双标题/双 ⓘ；步骤条加完成态与计数，箭头用 `ChevronRight`；底本 select 限宽 420；导出降为 secondary；调试页提示词默认折叠、模型/地址收进「运行环境」 | 一步一标题一主按钮；步骤可判断进度 | WB26–30 |
| 文献库范围语义 | `UnifiedLiteratureSearch.tsx:13`、`IngestionPanel.tsx:94-95`、`ProcessingFilters.tsx:14` | 范围下拉用「范围：当前分类 / 整个文献库」显式前缀；加工页只留一个 primary（处理并建立索引），「处理全部系统文献」降为 secondary；过滤行拆成「筛选」+「失败入口」两行 | 同一 value 不随上下文改名；一屏一个 primary | KB07/20/21 |
| 期刊选择与浏览 | `SubmissionJournalPicker.tsx:34-40`、`EnglishJournalLibrary.tsx:282`、`english-journals.css:39-44` | 目标期刊改搜索式 combobox（≤50 条、显示等级/学科、最近使用置顶）；期刊表只保留行内「详情」按钮展开并补齐键盘；Q1–Q4 改 `--ej-tier-*` token（前景 ≥4.5:1） | 不知道刊名也能选刊；键盘可达；色码可读 | FB16/17/26 |

**C. 弹窗、主题与后台效率**

| 工作项 | 位置 | 改法 | 验收 | 来源 |
| --- | --- | --- | --- | --- |
| 弹层主题掉线 | `Overlays.tsx:48-57`、`Pages.tsx:37`、`modern.css` | `createPortal` 到 `body` 后补 `.portal-modal` 变量作用域（`:is(.portal-workspace,.portal-modal)`），或把 portal 变量挂到 `#root` | 抽屉内外同一主题、选中态同色 | WB03 |
| 三套弹窗合一 | `app-dialogs.css:1-5`、`theme.css:315-319`、`portal.css:37` | 统一走一种实现（推荐原生 `<dialog>` + 现有 token），删除两套外观 | 全站弹窗外观 ≤1 种 | FB27 |
| 后台临时密码对话框 | `AdminConsole.tsx`（建立用户/重置密码） | 临时密码增加确认输入、显示/复制、自动生成；提示「原登录将失效」 | 管理员无需重置两次即可交付账号 | PA21 |
| 后台运行概览与错误态 | `AdminConsole.tsx` 概览/模型服务/操作记录 | 概览按「异常优先」排序并提供跳转；模型保存失败给字段级错误与重试；操作记录支持按操作者/对象筛选 | 异常 3 步内可定位；失败可重试 | PA14/15/25 |
| 后台期刊库与工作区 | 期刊库管理、工作区运行 | 期刊库加批量导入/CSV 入口；工作区停止动作在非运行态给出原因而非全禁用 | 后台主任务闭环 | PA17/22 |

### 6.3 P2：一个月内（打磨与产品签名）

- **产品签名落地（对应第 1 章方向）**：新增 `<SourceTag>`（等宽 12px：`卷三·叶十二·段四` + 1px 细线），先用于阅读页摘录、研究编辑器出处、检索结果三处；阅读页把「原件扫描页」作为一个视觉时刻（更大宽度、安静工具栏、纸张质感），这是全站唯一需要的「亮点」。
- **文案与术语全面替换**：按第 8.3 节术语表与各分报告的 Top 文案表替换；帮助文字 ≤1 行，长说明收进 `PageHeading` 的 ⓘ 弹层（`PageHeading.tsx:19-34` 已是正确模式）。
- **组件统一**：`Loading/Empty/Error` 三态组件（`Pages.tsx:18` 的 `Empty` 增加 `icon` 参数；空态不再全站共用一本书）；时间格式统一（≤24h 相对 + `title` 绝对、`tabular-nums`）；状态徽标去 10px 胶囊化（状态点 + 12px 文本）。
- **后台移动端**：期刊表在 <760px 改卡片行（名称 + 等级 + 编辑），或只留三列 + 详情抽屉（PA18/PA30/FB21）。
- **设置页整理**：模型信息从「账号与安全」底部移入「关于/运行环境」（WB37）；主题卡预览改用真实界面缩略（WB38）；主题/设置导航选中态简化为「浅底 + 2px accent 左条」（WB36）。
- **细节清单**：成果卡 footer 分组（WB33）、批量页按钮分组（WB31）、调试页工程语言（WB32）、重复 id（WB43）、`role=tablist` 补 `tabpanel`（WB42/FB53）、`aria-selected` 从 `<tr>` 移除（FB16）、空态动作定位（FB25/41/44）、删除失败 `role=alert`（FB57）、关闭按钮 32px（FB59）。
- **深色主题完整走查**：按第 1 章 token 落地后，逐页截图核对；目标是深色下不再出现任何紫调残留与浅底条纹。

### 6.4 建议的提交切分（便于评审）

1. `chore(css): semantic tokens` —— 只改 CSS 变量与引用，不动布局（token 收口 + 未定义 token 修复）。
2. `fix(copy): remove default credential hint` —— 安全与登录页文案。
3. `fix(mobile): titlebar breakpoints` —— 响应式断点。
4. `refactor(feedback): single stack + explicit tone` —— toast/error。
5. `fix(workbench): reference dialog / danger zone` —— 数据风险。
6. `fix(assistant): markdown styles + unverified state` —— 助手结果区。
7. `fix(submissions): empty board` —— 投稿空态。
8. 之后按 A→B→C 的顺序分系统参数、页面信息架构、打磨三批。

---

## 7. 验收清单（可直接勾选）

**系统与 token**
- [ ] 全站 CSS 中 `var(--tone-` 只存在于 `palette.css` 的过渡映射表，新代码 0 新增
- [ ] `--text-muted`、`--surface-soft`、`--danger` 等被引用的变量全部有定义（包括暗色）
- [ ] 深浅色各只有 1 个页面底色、1 套提示色、1 个强调色
- [ ] 浅色下「待复核 / 已审定 / 待准备」三种状态色两两可区分
- [ ] 字号种类 ≤8；正文 14px；不存在 <12px 的正文；标题字重 ≤600
- [ ] 间距 95% 落在 4px 网格；控件高度 ≤4 种（32/36/40/44）
- [ ] 圆角 ≤5 种；阴影 ≤3 种；无彩色阴影；backdrop blur ≤1 处
- [ ] 图标 size 只出现 16/18（装饰性 12 除外）；strokeWidth 1.5 覆盖 ≥90%
- [ ] `transition` 不出现 width/height/top/left；全局 `prefers-reduced-motion` 兜底
- [ ] z-index 值 ≤6 种

**可达性**
- [ ] 12px 及以下文本对比度 ≥4.5:1；控件边界 ≥3:1（抽查 20 处）
- [ ] 所有 icon-only 按钮有 `aria-label`；所有功能图标不是 emoji/字符箭头
- [ ] 键盘可完成：登录、切换项目、打开文献、摘录、保存研究成果、切换筛选、关闭弹窗
- [ ] 每个 tab 组件有 `aria-controls` + `role=tabpanel` 或已降级为 `aria-pressed`
- [ ] 焦点环全站统一 2px accent + 2px offset，不被 `outline:none` 清除

**页面**
- [ ] 430px 下：工作台标题栏无溢出；后台导航可开合；期刊表可用；无横向滚动
- [ ] 每个页面只有一个 H1；每屏只有一个实心主按钮（多步向导除外）
- [ ] 阅读页：正文行宽 36–44em；默认处理范围 = 当前页；工具栏 ≤1 行
- [ ] 研究助手：草稿有排版、未核验有解释、首次进入有空态、证据与草稿分区清楚
- [ ] 投稿管理：0 篇显示单个空态；状态色可读；目标期刊可搜索
- [ ] 点校：步骤条有完成态；一屏一个主按钮；危险操作成组
- [ ] 项目配置弹窗：保存与归档/删除分离；删除需输入项目名
- [ ] 全局错误只出现一次；浮层不压标题栏；toast 颜色由显式 tone 决定

**文案**
- [ ] 无感叹号、无问候式标题、无默认口令提示、无「最新/超级/无缝」类词
- [ ] 术语表落词一致（文献/材料/工作单元/研究工作/研究成果/投稿…）
- [ ] 空状态：标题 + 一句解释 + 至多一个动作；短句不带句号
- [ ] 所有相对时间有参照（「距今 N 天」vs「尚无记录」）

**回归**
- [ ] `npm run build:ui`（tsc + vite）通过
- [ ] `vitest run src` 通过
- [ ] `node scripts/check_portal_gui.cjs` 通过（本轮已发现它有两处断言与新代码漂移：`/api/admin/users` 返回 401 而脚本期望 403、新建论文向导关闭时序；属测试脚本待更新，不是产品缺陷）
- [ ] 深色/浅色各截图 12 个主页面，与第 1 章 A.6 的「一屏一句话」逐条对照

---

## 8. 附录

### 8.1 Skill 安装记录与许可证

- **来源**：[https://github.com/heydamir/unslop](https://github.com/heydamir/unslop)，commit `a6f121eee5f29c2f74976b75456feae8b66afb3a`（2026-11-05 拉取）。
- **安装位置**：`.dsh/skills/unslop/`（本项目 skill 目录，DSH 会话已能识别并加载）。
- **安装内容**：`SKILL.md`、`references/{signs,tokens,visuals,effects,refs}.md`、`references/refs/*.webp`、`scripts/{snapshot,story}.mjs`、`agents/openai.yaml`（按官方 README 的 `cp -r unslop/skills/unslop ~/.claude/skills/` 等价方式复制）。
- **许可**：**CC BY 4.0**，作者 Damir Sokolovsky（[@heydamir](https://x.com/heydamir)，[heydamir.design/unslop](https://heydamir.design/unslop)）。本报告按 unslop 的方法与术语进行审计，已在文首与本节保留署名与链接。
- **使用方式**：评审时加载 `unslop` skill，按其 `references/signs.md` 的九类迹象逐页审计，按其 Direction 五要素写产品方向（第 1 章），按 tokens 建议给出具体数值。未使用其 image generation 与 story video 能力（本任务只需要评审文档）。

### 8.2 证据索引

| 证据 | 路径 | 说明 |
| --- | --- | --- |
| 页面截图（夹具） | `.local/unslop-shots/fixture/*.png`（63 张） | 项目自带 GUI 验收夹具生成：登录页（桌面/移动）、后台 10 区、用户工作台各页、空态/列表/详情/编辑弹窗、点校三阶段、研究助手单/团队、设置页、浅色为主 + 深色与移动端抽查 |
| 真实部署截图 | `.local/unslop-shots/live-login-*.png`（3 张） | WSL 部署实例 `http://127.0.0.1:8765` 的登录页（管理员口令已变更，登录后页面未抓取，页面证据以夹具与代码为准） |
| 静态扫描 1 | `.dsh/scratch/unslop-review/audit-scan.json` | 颜色/渐变/阴影/玻璃/字号/字重/圆角/z-index/emoji/文案/内联样式统计 |
| 静态扫描 2 | `.dsh/scratch/unslop-review/audit-system.json` | 8,884 条声明、间距/控件高度/行高/字距/动效、图标尺寸/字重、对比度实算 |
| 扫描脚本 | `.dsh/scratch/unslop-review/audit-scan.mjs`、`audit-system.mjs` | 可重跑：`node .dsh/scratch/unslop-review/audit-*.mjs` |
| 截图脚本 | `.dsh/scratch/unslop-review/run/check_portal_gui.cjs`（去断言的夹具版）、`live-shots.cjs` | 复用项目 `scripts/check_portal_gui.cjs` + `scripts/portal_fixture.py` 的验收夹具 |
| 代码基线差异 | `git -C /root/wenjin diff --stat c46b02f..c89d6a7 -- src/` 为空 | WSL 与 Windows 本地前端代码逐字节一致，差异仅在 `backend/knowledge/retrieval.py` |
| 分组底稿 | `.dsh/scratch/unslop-review/00-system.md`、`01-portal-admin.md`、`02-workbench.md`、`03-knowledge.md`、`04-submissions-feedback.md` | 本报告第 1–5 章的原始素材 |

> 证据边界：夹具数据是界面验收用的占位账号与测试文献，不是真实研究资料；截图主题默认浅色（`appearance.ts:6` 缺省 `light`）。深色与真实数据下的页面表现以代码与少量深色截图为准，落地时建议按验收清单补拍。

### 8.3 术语表（建议统一）

| 概念 | 统一用词 | 不再使用 | 说明 |
| --- | --- | --- | --- |
| 公共/个人文献集合 | 文献库（系统文献库 / 我的文献库） | 资料库、资料目录混称 | 页面标题与导航统一 |
| 项目内使用的文献 | 项目材料 | 资料、原文（指代不清时） | 「材料」只在项目语境用 |
| 单本文献的数字化全文 | 原文 / 录文 | 正文（用于 OCR 文本时易混） | 原件=扫描/PDF；录文=文本 |
| 出处定位 | 出处（卷/叶/页/段） | 引用位置、位置信息 | 签名组件 `SourceTag` 使用 |
| 研究的工作对象 | 研究工作（单元） | 课题、任务（任务=执行记录） | 导航「研究工作」 |
| 执行记录 | 执行记录 | 任务日志、运行记录 | |
| 产出 | 研究成果 | 工作成果 / 项目成果 / 成果（混用） | 导航「研究成果」，与「项目概览」一致 |
| 投稿 | 论文投稿 / 投稿管理 | 期刊投递、论文管理 | |
| 助手 | 研究助手 | AI 助手、智能体（面向用户时） | 「助手与 Skill」仅后台用 |
| 后台服务 | 资料服务 | 后台服务、本地服务、服务 | 状态栏与错误文案统一 |
| 向量化与索引 | 文献加工 / 建立索引 | 入库、向量化（对用户） | 后台可保留技术词，用户侧用「加工」 |
| 点校 | 古籍点校 / 点校稿 | 校对（与错别字检查混用） | |
| 模型计费 | 字数用量 / 费用估算 | token（用户界面） | token 仅后台技术区 |

### 8.4 评审边界与未覆盖项

- 只做**前端 UI/UX 与视觉**评审；未评审后端功能正确性、检索质量、模型输出质量、数据库与部署性能。
- 未对真实管理员账号之外的业务数据做任何写操作；未改动 WSL 仓库、Docker 容器与 `/root/wenjin-data`。
- 截图来自界面验收夹具（占位账号、测试文献），与真实数据下的极端长文本/大数量列表表现可能有差异；建议在真实数据上按验收清单复测一次。
- 未做跨浏览器验证（Chromium 内核外）与真机移动端验证；移动端结论来自 430×900 视口截图与代码断点。
- 第 1 章 A.5 的 token 迁移是建议方案，落地时需按实际构建顺序回归 `npm run build:ui` 与 `vitest`。

---

*本报告由 unslop 方法驱动：先审计、再定方向、按 pass 顺序修改；评审文档由 Team Lead 汇总四组页面评审与系统扫描结果而成。*
