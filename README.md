# AI 时讯自动处理系统

一个面向个人或小团队的 AI 资讯监控项目，用来持续抓取 B 站、X / Twitter、微博等平台账号动态，过滤非 AI 内容，生成中文摘要，并通过 PushPlus 推送日报。

## 功能概览

- 监控多个平台账号并抓取最新内容
- 将英文内容翻译整理为中文标题和中文摘要
- 过滤明显与 AI 无关的噪音内容
- 使用大模型生成摘要、分类、重要性评分
- 将处理结果落库，并记录每个监控源最近一次抓取状态
- 通过 PushPlus 推送测试消息和日报
- 支持自建 RSSHub，为 X / Twitter、微博等平台提供 RSS 抓取能力

## 当前平台状态

- X / Twitter：当前最稳定，推荐优先使用自建 RSSHub
- B 站：可用，但存在明显风控波动，成功率受账号、时间窗和请求频率影响
- 微博：理论可做，但强依赖有效登录态 cookie 和数字 uid；如果 cookie 失效会直接抓取失败

## 技术栈

- Python 3.11+
- FastAPI
- SQLAlchemy + SQLite
- httpx
- yt-dlp
- feedparser
- APScheduler
- DeepSeek 兼容接口
- PushPlus
- RSSHub

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
- `WHISPER_API_KEY`
- `RSSHUB_BASE_URL`

5. 启动服务

```bash
uvicorn app.main:app --reload
```

启动后可访问：

- 接口文档：`http://127.0.0.1:8000/docs`

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

### 调度

- `SCHEDULER_ENABLED`
- `DAILY_REPORT_HOUR`
- `DAILY_REPORT_MINUTE`
- `FETCH_LOOKBACK_HOURS`
- `SCHEDULER_TIMEZONE`

### 自建 RSSHub

- `RSSHUB_BASE_URL`

## 自建 RSSHub

项目已经支持优先使用自建 RSSHub。

1. 进入 [rsshub/README.md](/D:/pythonProject/AI_news/rsshub/README.md)
2. 按说明复制 `rsshub/.env.example` 为 `rsshub/.env`
3. 填写 X / Twitter、微博相关凭据
4. 启动 RSSHub
5. 在项目根目录 `.env` 中设置 `RSSHUB_BASE_URL=http://127.0.0.1:1200`

应用在抓取微博和 X / Twitter 时会自动优先走自建 RSSHub，不需要逐条改数据库里的 `rss_url`。

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
- 配合 Twitter 登录态后，当前是全项目里最稳定的一路来源

### 微博抓取策略

- 使用自建 RSSHub `/weibo/user/:uid`
- 必须是数字 uid
- 必须提供对 RSSHub 可用的微博 cookie

## GitHub 部署建议

如果你准备把项目公开到 GitHub，建议：

- 不要提交 `.env`
- 不要提交 `rsshub/.env`
- 不要提交任何浏览器 cookie、token、密码
- 使用 `.env.example` 提供占位配置
- 在 README 里明确说明微博和 X / Twitter 依赖登录态
- 把 `.codex/` 这类本地代理工作目录忽略掉

## 已知限制

- 微博对登录态要求高，cookie 经常失效
- B 站风控较强，部分账号会间歇性失败
- 即使来源账号本身是 AI 圈人物，也不能保证每条动态都与 AI 相关
- 上游平台规则变化会直接影响抓取稳定性

## 适合的使用方式

- 个人 AI 行业追踪
- 小团队内部日报
- 资讯聚合与后续二次加工
- 作为更大自动化系统的数据采集前端
