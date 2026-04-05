import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db_session
from app.schemas import ProcessVideoResponse
from app.services.ai_processor import AIProcessorError
from app.services.bilibili_service import BilibiliAPIError
from app.services.video_processor import VideoProcessor, VideoProcessorError

LOGGER = logging.getLogger(__name__)
router = APIRouter()


def build_error_detail(message: str, *, stage: str, hint: str) -> dict:
    return {
        "message": message,
        "stage": stage,
        "hint": hint,
    }


def get_hint_from_error(message: str, *, stage: str) -> str:
    normalized = message.lower()

    if "deepseek_api_key" in normalized:
        return "请在 `.env` 中填写 `DEEPSEEK_API_KEY`，保存后重启服务。"
    if "whisper_api_key" in normalized:
        return "这个视频没有可用字幕，系统已切到音频转写模式。请在 `.env` 中填写 `WHISPER_API_KEY`，必要时同时配置 `WHISPER_BASE_URL` 和 `WHISPER_MODEL`。"
    if "authentication fails" in normalized or "401" in normalized or "invalid api key" in normalized:
        return "DeepSeek 认证失败，请检查 `DEEPSEEK_API_KEY` 是否正确，以及 `DEEPSEEK_BASE_URL` 是否为 `https://api.deepseek.com/v1`。"
    if "当前 mvp 仅支持处理带 cc 字幕的视频" in normalized:
        return "这个视频当前没有可直接使用的 CC 字幕。下一步需要接入音频下载和 Whisper 转写。"
    if "音频下载失败" in normalized:
        return "系统没有拿到字幕，已经尝试下载音频，但下载阶段失败了。请检查网络、视频可访问性，或确认本机已安装 `yt-dlp` 依赖。"
    if "whisper 转写失败" in normalized or "whisper 请求失败" in normalized:
        return "音频已经进入转写阶段，但 Whisper 服务调用失败。请检查 `WHISPER_API_KEY`、`WHISPER_BASE_URL`、`WHISPER_MODEL` 和网络。"
    if "内部错误" in normalized:
        return "后端处理过程中出现了未预期错误。刷新页面后重试；如果持续出现，请查看终端日志。"
    if "字幕下载失败" in normalized:
        return "视频可能存在字幕，但字幕地址不可访问。可以稍后重试，或检查网络与 B 站访问状态。"
    if "b站接口请求失败" in normalized:
        return "请检查网络是否正常；如果视频受登录限制，请在 `.env` 中配置有效的 `BILIBILI_SESSDATA`。"
    if "返回结构不符合预期" in normalized or "不是合法 json" in normalized:
        return "模型返回格式不稳定。可以稍后重试，或降低提示词复杂度。"
    if "请求失败" in normalized:
        return "外部服务调用失败，请检查网络连接和对应 API 配置。"

    if stage == "bilibili":
        return "请确认 BV 号正确、视频可访问，并检查是否需要登录态。"
    if stage == "ai":
        return "请检查 DeepSeek 配置和网络状态，然后再试一次。"
    return "请根据报错信息检查配置后重试。"


@router.get("/health", summary="健康检查")
async def health_check() -> dict:
    """返回服务运行状态。"""
    return {"status": "ok"}


@router.post(
    "/test/process_video/{bv_id}",
    response_model=ProcessVideoResponse,
    summary="测试处理单个 B 站视频",
)
async def test_process_video(
    bv_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> ProcessVideoResponse:
    """抓取单个视频字幕并调用 DeepSeek 生成摘要分类。"""
    processor = VideoProcessor()
    try:
        result = await processor.process_video(session, bv_id)
    except BilibiliAPIError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=build_error_detail(
                f"B站数据获取失败: {exc}",
                stage="bilibili",
                hint=get_hint_from_error(str(exc), stage="bilibili"),
            ),
        ) from exc
    except AIProcessorError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=build_error_detail(
                f"AI 处理失败: {exc}",
                stage="ai",
                hint=get_hint_from_error(str(exc), stage="ai"),
            ),
        ) from exc
    except VideoProcessorError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=build_error_detail(
                str(exc),
                stage="pipeline",
                hint=get_hint_from_error(str(exc), stage="pipeline"),
            ),
        ) from exc
    except Exception as exc:
        LOGGER.exception("处理视频时出现未捕获异常: %s", bv_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=build_error_detail(
                f"内部错误: {exc}",
                stage="internal",
                hint=get_hint_from_error("内部错误", stage="internal"),
            ),
        ) from exc

    return ProcessVideoResponse(**result)
