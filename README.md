# AI 时讯自动处理系统

一个面向个人或小团队的 AI 资讯监控项目，用来持续抓取 B 站、X / Twitter、微博等平台账号动态，过滤非 AI 内容，生成中文摘要，并通过 PushPlus 推送日报。

当前版本已经把“定时采集与推送”调整为 `Windows 计划任务主调度`，同时支持“双人互动版 AI 随身听播客”。

## 功能概览

- 监控多个平台账号并抓取最新内容
- 自动过滤明显与 AI 无关的噪声内容
- 生成中文标题、中文摘要、分类和重要性评分
- 支持 PushPlus 文本日报推送
- 支持双人互动播客音频生成并附带外链推送
- 支持两种播客通道切换：
  - `内置 TTS`：走当前 OpenAI 兼容语音接口
  - `Edge TTS`：走 Microsoft Edge TTS，本地合成、资源更轻
- 支持自建 RSSHub，为 X / Twitter 以及部分 B 站场景提供抓取能力
- 支持 Windows 计划任务自动补偿重试：9:00 失败后，9:30 和 10:00 再试，最多 3 次

## 当前平台状态

- X / Twitter：推荐通过自建 RSSHub 抓取；本机需要能访问 `x.com`
- B 站：可用，但存在风控波动，部分账号会间歇失败
- 微博：支持项目内直连抓取，但强依赖有效 cookie 和正确 uid

## 技术栈

- Python 3.11+
- FastAPI
- SQLAlchemy + SQLite
- httpx
- yt-dlp
- feedparser
- PushPlus
- RSSHub
- Windows Task Scheduler
- 阿里云 OSS / S3 兼容对象存储

## 目录结构

```text
app/                 FastAPI 应用与核心业务逻辑
rsshub/              自建 RSSHub 的 Docker 部署文件
.env.example         环境变量模板
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
- `RSSHUB_BASE_URL`

5. 启动管理服务

```bash
uvicorn app.main:app --reload
```

启动后可访问：

- 管理页：[http://127.0.0.1:8000/manage](http://127.0.0.1:8000/manage)
- 接口文档：[http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)

## Windows 自动任务

这是当前默认调度方案。

### 工作方式

- 管理页中的“自动任务 / 小时 / 分钟 / 时区”仍然保留
- 保存后，后端会同步 Windows 计划任务：
  - `AI-News-Daily-Push`
  - `AI-News-Daily-Push-Retry-1`
  - `AI-News-Daily-Push-Retry-2`
- 计划任务会直接执行：

```bash
python -m app.run_scheduled_job
```

- 即使 FastAPI 没有运行，到点后也会自动采集和推送
- 当日首次成功推送后，后续补偿任务会自动跳过
- 如果 9:00 推送失败，会在 9:30、10:00 自动重试，最多 3 次

### 验证方式

PowerShell 查看任务：

```powershell
schtasks /Query /TN AI-News-Daily-Push /FO LIST /V
```

手动执行一次独立任务：

```bash
python -m app.run_scheduled_job
```

## 双人 AI 随身听

项目支持基于当天 AI 时讯生成双人互动版中文播客：

- 男声 `host_a`
- 女声 `host_b`
- 风格偏“搭档聊天”，不是纯播报腔
- 音频生成失败不会阻断文本日报推送

### 播客通道

管理页支持切换：

- `内置 TTS`
- `Edge TTS`

#### 1. 内置 TTS

适合已经接好 OpenAI 兼容语音接口的场景。

相关变量：

- `PODCAST_CHANNEL=built_in`
- `TTS_API_KEY`
- `TTS_BASE_URL`
- `TTS_MODEL`
- `TTS_VOICE_MALE`
- `TTS_VOICE_FEMALE`
- `TTS_FORMAT`

#### 2. Edge TTS

更轻量，本地直接合成，通常中文自然度也更稳一些。

相关变量：

- `PODCAST_CHANNEL=edge_tts`
- `TTS_VOICE_MALE`
- `TTS_VOICE_FEMALE`

推荐中文音色：

- 男声：`zh-CN-YunyangNeural`
- 女声：`zh-CN-XiaoxiaoNeural`

说明：

- Edge TTS 生成分段音频后会自动拼接
- 最终上传的是 `mp3`
- 不依赖本地 FastAPI 托管音频

## 对象存储

播客音频会上传到对象存储并在 PushPlus 中附带链接。

常用变量：

- `AUDIO_STORAGE_PROVIDER`
- `AUDIO_STORAGE_ENDPOINT`
- `AUDIO_STORAGE_BUCKET`
- `AUDIO_STORAGE_ACCESS_KEY`
- `AUDIO_STORAGE_SECRET_KEY`
- `AUDIO_STORAGE_REGION`
- `AUDIO_STORAGE_PUBLIC_BASE_URL`

阿里云 OSS 可直接使用：

- `AUDIO_STORAGE_PROVIDER=s3` 或项目内阿里云兼容配置
- `AUDIO_STORAGE_ENDPOINT=oss-cn-beijing.aliyuncs.com`

## 环境变量说明

### 基础运行

- `DEBUG`
- `DATABASE_URL`

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

### 播客

- `PODCAST_AUDIO_ENABLED`
- `PODCAST_INCLUDE_AUDIO_LINK`
- `PODCAST_CHANNEL`
- `TTS_API_KEY`
- `TTS_BASE_URL`
- `TTS_MODEL`
- `TTS_VOICE_MALE`
- `TTS_VOICE_FEMALE`
- `TTS_FORMAT`

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

### RSSHub

- `RSSHUB_BASE_URL`

## 自建 RSSHub

项目支持优先使用自建 RSSHub。

1. 查看 [rsshub/README.md](/D:/pythonProject/AI_news/rsshub/README.md)
2. 复制 `rsshub/.env.example` 为 `rsshub/.env`
3. 填写 Twitter、微博等相关凭据
4. 启动 RSSHub
5. 在项目根目录 `.env` 中设置：

```text
RSSHUB_BASE_URL=http://127.0.0.1:1200
```

## 抓取逻辑说明

### 采集流程

1. 从 `monitor_sources` 读取启用中的监控源
2. 按平台调用不同抓取策略
3. 去重、过滤、判断 AI 相关性
4. 生成中文摘要和评分
5. 写入 `processed_contents`
6. 触发日报与播客生成

### B 站抓取策略

按以下顺序回退：

1. 官方空间接口
2. `yt-dlp`
3. RSSHub B 站路由

### X / Twitter 抓取策略

- 优先使用 RSSHub `/twitter/user/:id`
- 本机需要具备可访问 `x.com` 的网络环境

### 微博抓取策略

- 优先使用项目内直连微博接口
- 必须提供有效 `WEIBO_COOKIES`
- `platform_id` 必须为数字 uid

## 已知限制

- 微博 cookie 容易失效
- B 站风控较强，稳定性会波动
- X / Twitter 强依赖网络环境
- 即使来源账号本身属于 AI 圈，也不能保证每条动态都与 AI 强相关
- Windows 定时任务依赖机器处于开机状态

## 适合的使用方式

- 个人 AI 行业追踪
- 小团队内部 AI 日报
- 资讯聚合后二次加工
- 作为更大自动化系统的数据采集前端
