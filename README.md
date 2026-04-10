# AI 时讯自动处理系统

一个用于监控 AI 相关账号动态、抓取内容、调用大模型总结并推送到 PushPlus 的小型自动化系统。

## 当前能力

- 监控 B 站、微博、X / Twitter 账号
- 抓取新内容并落库
- 调用 DeepSeek 生成摘要
- 通过 PushPlus 推送日报和测试消息
- 在监控源配置中记录最近一次抓取状态、错误和数量

## 快速开始

安装依赖：

```bash
pip install -r requirements.txt
```

复制环境变量模板：

```bash
copy .env.example .env
```

最少需要配置：

- `DEEPSEEK_API_KEY`
- `PUSHPLUS_TOKEN`
- `DATABASE_URL`

可选但强烈建议配置：

- `BILIBILI_SESSDATA`
- `BILIBILI_BILI_JCT`
- `BILIBILI_BUVID3`
- `WHISPER_API_KEY`
- `RSSHUB_BASE_URL`

启动服务：

```bash
uvicorn app.main:app --reload
```

## 自建 RSSHub

项目已经支持优先使用自建 RSSHub。

1. 进入 [rsshub/README.md](/D:/pythonProject/AI_news/rsshub/README.md) 按说明准备环境。
2. 启动自建 RSSHub 后，在项目根目录 `.env` 中设置 `RSSHUB_BASE_URL=http://127.0.0.1:1200`。
3. 应用会在抓取微博和 X / Twitter 时优先走自建实例，不需要你手动逐条修改数据库里的 `rss_url`。

## 平台现状

- B 站：当前最可行，已经在本项目里跑通过真实抓取、摘要和 PushPlus 推送。
- 微博：理论上可行，但公共 RSSHub 很容易 403，更建议自建 RSSHub 并配置微博 cookies。
- X / Twitter：理论上也可行，但最脆弱，需要自建 RSSHub 和有效的 Twitter 登录凭据，公共镜像通常不稳定。

## 当前限制

- 这台机器当前没有安装 Docker，所以仓库里虽然已经提供了 RSSHub 部署文件，但我无法在这里直接帮你把自建实例拉起来。
- 微博和 X / Twitter 的抓取稳定性强依赖 cookies、登录态和上游平台反爬策略。
- B 站接口存在一定风控波动，当前代码已经加入 cookies、重试和 `yt-dlp` 回退，但仍可能偶发失败。
