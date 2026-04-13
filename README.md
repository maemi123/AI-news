# AI 时讯自动处理系统

一个面向个人或小团队的 AI 资讯监控项目，用来持续抓取 B 站、X / Twitter、微博等平台账号动态，过滤非 AI 内容，生成中文摘要，并通过 PushPlus 推送日报。

这套系统现在已经调整为 Windows 计划任务主调度：

- 前端管理页负责配置每日推送时间
- 后端保存设置时会自动同步 Windows 计划任务
- 即使 FastAPI 服务没有运行，到点后也能自动执行采集与 PushPlus 推送
- 日常资源占用更低，不需要长期挂着 Python Web 服务只是为了定时推送

## 功能概览

- 监控多个平台账号并抓取最新内容
- 将英文内容整理为中文标题和中文摘要
- 过滤明显与 AI 无关的噪音内容
- 使用大模型生成摘要、分类、重要性评分
- 将处理结果落库，并记录每个监控源最近一次抓取状态
- 支持管理页手动“立即采集”和“测试推送”
- 支持 PushPlus 日报推送
- 支持自建 RSSHub，为 X / Twitter 以及部分 B 站场景提供抓取能力

## 当前平台状态

- X / Twitter：依赖自建 RSSHub 和可用的出海网络，本机无法访问 `x.com` 时会失败
- B 站：可用，但存在明显风控波动，成功率受账号、时间窗和请求频率影响
- 微博：当前已支持项目内直连微博接口抓取，强依赖有效登录态 cookie 和数字 uid

## 技术栈

- Python 3.11+
- FastAPI
- SQLAlchemy + SQLite
- httpx
- yt-dlp
- feedparser
- DeepSeek 兼容接口
- PushPlus
- RSSHub
- Windows Task Scheduler

## 目录结构

```text
app/                 FastAPI 应用与核心业务逻辑
rsshub/              自建 RSSHub 的 Docker 部署文件
.env.example         项目主环境变量模板
requirements.txt     Python 依赖
README.md            项目说明
```

## 快速开始

1. 安装依赖

```bash
pip install -r requirements.txt
```

2. 复制环境变量

Windows:

```bash
copy .env.example .env
```

macOS / Linux:

```bash
cp .env.example .env
```

3. 至少配置这些变量

- `DEEPSEEK_API_KEY`
- `PUSHPLUS_TOKEN`
- `DATABASE_URL`

4. 建议同时配置

- `BILIBILI_SESSDATA`
- `BILIBILI_BILI_JCT`
- `BILIBILI_BUVID3`
- `WEIBO_COOKIES`
- `WHISPER_API_KEY`
- `RSSHUB_BASE_URL`

5. 启动管理服务

```bash
uvicorn app.main:app --reload
```

启动后可访问：

- 管理页：`http://127.0.0.1:8000/manage`
- 接口文档：`http://127.0.0.1:8000/docs`

## Windows 自动任务

这是当前默认调度方案。

### 工作方式

- 管理页中的“自动任务 / 小时 / 分钟 / 时区”仍然保留
- 保存后，后端会同步一个固定名称的 Windows 计划任务：`AI-News-Daily-Push`
- 计划任务会直接执行：

```bash
python -m app.run_scheduled_job
```

- 独立脚本会自动初始化数据库、读取当前设置、执行采集并推送，然后退出

### 优点

- FastAPI 不需要一直运行
- 系统资源占用更低
- 定时推送更接近真实部署环境

### 当前实现细节

- 优先使用项目内 `.venv\Scripts\python.exe`
- 如果没有 `.venv`，则回退到 `py -3`
- Windows 任务工作目录固定为项目根目录，避免 `.env`、SQLite 相对路径失效

### 验证方式

保存管理页设置后，可以在 PowerShell 中查看任务：

```powershell
schtasks /Query /TN AI-News-Daily-Push /FO LIST /V
```

也可以直接执行一次独立任务：

```bash
python -m app.run_scheduled_job
```

## 环境变量说明

### 基础运行

- `DEBUG`：是否开启调试日志，默认建议 `false`
- `DATABASE_URL`：数据库连接串，默认使用本地 SQLite

### 大模型

- `DEEPSEEK_API_KEY`
- `DEEPSEEK_BASE_URL`
- `DEEPSEEK_MODEL`

### Whisper 转写

- `WHISPER_API_KEY`
- `WHISPER_BASE_URL`
- `WHISPER_MODEL`

### PushPlus

- `PUSHPLUS_TOKEN`

### B 站

- `BILIBILI_SESSDATA`
- `BILIBILI_BILI_JCT`
- `BILIBILI_BUVID3`

### 微博

- `WEIBO_COOKIES`

### 调度

- `SCHEDULER_ENABLED`
- `DAILY_REPORT_HOUR`
- `DAILY_REPORT_MINUTE`
- `FETCH_LOOKBACK_HOURS`
- `SCHEDULER_TIMEZONE`

说明：

- 这些字段现在表示“Windows 自动任务的计划时间”和内容计算时区
- 真正的定时触发由 Windows 系统计划任务负责

### 自建 RSSHub

- `RSSHUB_BASE_URL`

## 自建 RSSHub

项目已经支持优先使用自建 RSSHub。

1. 进入 [rsshub/README.md](/D:/pythonProject/AI_news/rsshub/README.md)
2. 按说明复制 `rsshub/.env.example` 为 `rsshub/.env`
3. 填写 X / Twitter、微博相关凭据
4. 启动 RSSHub
5. 在项目根目录 `.env` 中设置：

```text
RSSHUB_BASE_URL=http://127.0.0.1:1200
```

应用在抓取 X / Twitter 时会自动优先走自建 RSSHub，不需要逐条改数据库里的 `rss_url`。

微博当前优先走项目内直连接口抓取，只有在未配置项目主 `.env` 中的 `WEIBO_COOKIES` 时才会退回 RSS 方案。

## 运行逻辑说明

### 采集流程

1. 从 `monitor_sources` 读取激活中的监控源
2. 按平台调用不同抓取策略
3. 做去重、过滤和 AI 相关性判断
4. 对有效内容生成中文标题、中文摘要和评分
5. 将结果写入 `processed_contents`
6. 更新每个监控源的最近抓取状态
7. 可选推送到 PushPlus

### B 站抓取策略

当前实现按以下顺序回退：

1. 官方空间接口
2. `yt-dlp`
3. 自建 RSSHub B 站路由

即便如此，B 站依然可能因为 `412`、`-352` 等风控错误出现波动。

### X / Twitter 抓取策略

- 优先使用自建 RSSHub `/twitter/user/:id`
- 配合 Twitter 登录态和可用的出海网络后可稳定工作
- 如果本机无法访问 `https://x.com`，则这一路会直接失败

### 微博抓取策略

- 优先使用项目内直连微博接口
- 必须是数字 uid
- 必须提供有效的 `WEIBO_COOKIES`
- 当直连接口不可用时，才会尝试 RSS 路径

## GitHub 发布建议

推送到 GitHub 前，建议检查下面这些点：

- 不要提交 `.env`
- 不要提交 `rsshub/.env`
- 不要提交任何真实 cookie、token、SESSDATA、Webhook 地址
- 保留 `.env.example` 作为模板，不要把真实值写进去
- 确认 `.gitignore` 已包含 `.codex`、数据库文件、日志文件和本地环境文件

如果你需要先清理暂存区里误加的敏感文件，可以用：

```bash
git rm --cached .env
git rm --cached rsshub/.env
```

如果某些文件已经被历史提交过，单纯 `.gitignore` 不会自动删除历史记录，需要额外做历史清理。

## 已知限制

- 微博对登录态要求高，cookie 经常失效
- B 站风控较强，部分账号会间歇性失败
- 即使来源账号本身是 AI 圈人物，也不能保证每条动态都与 AI 相关
- 上游平台规则变化会直接影响抓取稳定性
- X / Twitter 对本机网络环境要求高，是否能访问 `x.com` 会直接影响结果
- Windows 自动推送依赖本机处于开机状态，关机时不会执行

## 适合的使用方式

- 个人 AI 行业追踪
- 小团队内部日报
- 资讯聚合与后续二次加工
- 作为更大自动化系统的数据采集前端
