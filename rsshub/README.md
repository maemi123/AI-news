# RSSHub 部署说明

这个目录提供了给本项目配套使用的 RSSHub 自建部署文件，主要用于为 X / Twitter 和部分 B 站场景提供抓取能力。

## 当前方案

- 默认使用 `ghcr.io/diygod/rsshub:chromium-bundled`
- 目标是先以最少依赖快速跑通
- 当前配置默认使用 `memory` 缓存

这样做的好处是：

- 少依赖额外容器
- 在新机器上更容易启动
- 更适合先验证功能链路

## 准备步骤

1. 复制环境变量模板

```bash
cp .env.example .env
```

Windows:

```bash
copy .env.example .env
```

2. 填入你自己的账号信息

- `TWITTER_USERNAME`
- `TWITTER_PASSWORD`
- `TWITTER_AUTH_TOKEN`
- `TWITTER_CT0`

3. 启动 RSSHub

```bash
docker compose up -d
```

4. 检查健康状态

```bash
curl http://127.0.0.1:1200/healthz
```

正常会返回：

```text
ok
```

5. 回到项目根目录，在 `.env` 中配置：

```text
RSSHUB_BASE_URL=http://127.0.0.1:1200
```

## 关键环境变量说明

- `PORT`：RSSHub 暴露端口，默认 `1200`
- `NODE_ENV`：默认 `production`
- `CACHE_TYPE`：默认 `memory`
- `TWITTER_USERNAME`：X / Twitter 登录用户名
- `TWITTER_PASSWORD`：X / Twitter 密码
- `TWITTER_AUTH_TOKEN`：X / Twitter `auth_token`
- `TWITTER_CT0`：X / Twitter `ct0`

## 常见问题

### 微博返回 503

常见原因：

- 如果你仍在用 RSSHub 微博路由，`WEIBO_COOKIES` 已失效
- cookie 不完整
- 账号登录态本身不稳定

如果日志中出现：

```text
Cookies expired. Please update WEIBO_COOKIES
```

说明需要重新导出微博 cookie。

注意：当前主项目已经优先改为“项目内直连微博接口”抓取，所以微博不再强依赖 RSSHub。

### B 站返回 412 或 -352

这通常是 B 站风控，不一定是代码有问题。即使：

- 已配置 B 站 cookie
- 已切换 UA
- 已增加回退链路

仍然可能在某些账号或某些时间段失败。

### X / Twitter 返回空内容

常见原因：

- 登录态不完整
- `auth_token` / `ct0` 过期
- 本机网络无法访问 `https://x.com`
- 目标账号近期没有可抓取内容

## 部署建议

- 如果只是个人使用，当前单容器方案就够了
- 如果后续抓取量变大，可以再考虑 Redis 或更完整的浏览器服务拆分
- 若准备公开仓库，请确保不要提交 `rsshub/.env`
