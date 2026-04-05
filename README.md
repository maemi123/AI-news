# AI 时讯自动处理系统 MVP

## 已实现内容

- FastAPI 基础服务入口
- SQLAlchemy 异步数据库与 `Video`、`Summary` 模型
- B 站单视频信息获取与 CC 字幕抓取
- DeepSeek 摘要与分类服务
- 测试接口 `POST /test/process_video/{bv_id}`
- 本地脚本 `test_single_video.py`

## 安装

```bash
pip install -r requirements.txt
```

## 环境变量

复制 `.env.example` 为 `.env` 并填写：

- `DEEPSEEK_API_KEY`
- `DEEPSEEK_BASE_URL=https://api.deepseek.com/v1`
- `BILIBILI_SESSDATA`（部分视频可能需要）
- `WHISPER_API_KEY`（无字幕视频转写时需要）
- `WHISPER_BASE_URL=https://api.openai.com/v1`
- `WHISPER_MODEL=whisper-1`
- `DATABASE_URL`

企业微信机器人地址必须是完整可用的 webhook；如果配置的是占位地址，会返回 `invalid webhook url`。

## 启动

```bash
uvicorn app.main:app --reload
```

打开文档：

- `http://127.0.0.1:8000/docs`

## 测试

接口测试：

```bash
curl -X POST http://127.0.0.1:8000/test/process_video/BV1xx411c7mD
```

脚本测试：

```bash
python test_single_video.py BV1xx411c7mD
```

## 当前限制

- 优先使用 B 站 CC 字幕；无字幕时会尝试下载音频并调用 Whisper 转写
- 无字幕视频要想成功处理，必须先配置可用的 Whisper API
- 批量抓取、定时任务、推送服务为下一阶段
