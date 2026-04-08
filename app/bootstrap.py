from __future__ import annotations

from typing import Any
from urllib.parse import quote

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import MonitorSource


DEFAULT_MONITOR_SOURCES: list[dict[str, Any]] = [
    {'name': 'Sam Altman', 'platform': 'twitter', 'platform_id': 'sama', 'source_url': 'https://x.com/sama', 'rss_url': 'https://rsshub.app/twitter/user/sama', 'category': 'company', 'importance_weight': 5, 'extra_config': {'note': 'OpenAI CEO，行业风向标'}},
    {'name': 'Jensen Huang', 'platform': 'twitter', 'platform_id': 'JensenHuang', 'source_url': 'https://x.com/JensenHuang', 'rss_url': 'https://rsshub.app/twitter/user/JensenHuang', 'category': 'company', 'importance_weight': 5, 'extra_config': {'note': 'NVIDIA CEO，算力趋势核心来源'}},
    {'name': 'Satya Nadella', 'platform': 'twitter', 'platform_id': 'satyanadella', 'source_url': 'https://x.com/satyanadella', 'rss_url': 'https://rsshub.app/twitter/user/satyanadella', 'category': 'company', 'importance_weight': 5, 'extra_config': {'note': 'Microsoft CEO，AI办公战略关键人物'}},
    {'name': 'Demis Hassabis', 'platform': 'twitter', 'platform_id': 'demishassabis', 'source_url': 'https://x.com/demishassabis', 'rss_url': 'https://rsshub.app/twitter/user/demishassabis', 'category': 'company', 'importance_weight': 5, 'extra_config': {'note': 'DeepMind CEO，AI for Science 领军人物'}},
    {'name': 'Dario Amodei', 'platform': 'twitter', 'platform_id': 'darioamodei', 'source_url': 'https://x.com/darioamodei', 'rss_url': 'https://rsshub.app/twitter/user/darioamodei', 'category': 'company', 'importance_weight': 5, 'extra_config': {'note': 'Anthropic CEO，AI安全代表人物'}},
    {'name': 'Elon Musk', 'platform': 'twitter', 'platform_id': 'elonmusk', 'source_url': 'https://x.com/elonmusk', 'rss_url': 'https://rsshub.app/twitter/user/elonmusk', 'category': 'company', 'importance_weight': 5, 'extra_config': {'note': 'xAI / Tesla，具身智能与AI安全话题中心'}},
    {'name': 'Andrew Ng', 'platform': 'twitter', 'platform_id': 'AndrewYNg', 'source_url': 'https://x.com/AndrewYNg', 'rss_url': 'https://rsshub.app/twitter/user/AndrewYNg', 'category': 'academic', 'importance_weight': 4, 'extra_config': {'note': 'AI布道师，适合追踪产业和教育视角'}},
    {'name': 'Andrej Karpathy', 'platform': 'twitter', 'platform_id': 'karpathy', 'source_url': 'https://x.com/karpathy', 'rss_url': 'https://rsshub.app/twitter/user/karpathy', 'category': 'kol', 'importance_weight': 5, 'extra_config': {'note': '技术解读密度高，适合跟进模型与自动驾驶观点'}},
    {'name': 'Yann LeCun', 'platform': 'twitter', 'platform_id': 'ylecun', 'source_url': 'https://x.com/ylecun', 'rss_url': 'https://rsshub.app/twitter/user/ylecun', 'category': 'academic', 'importance_weight': 5, 'extra_config': {'note': 'AI教父之一，常有路线之争相关观点'}},
    {'name': 'Fei-Fei Li', 'platform': 'twitter', 'platform_id': 'drfeifei', 'source_url': 'https://x.com/drfeifei', 'rss_url': 'https://rsshub.app/twitter/user/drfeifei', 'category': 'academic', 'importance_weight': 5, 'extra_config': {'note': '计算机视觉与空间智能重要人物'}},
    {'name': '梁文锋', 'platform': 'twitter', 'platform_id': 'liangwenfeng', 'source_url': 'https://x.com/liangwenfeng', 'rss_url': 'https://rsshub.app/twitter/user/liangwenfeng', 'category': 'company', 'importance_weight': 5, 'extra_config': {'note': 'DeepSeek 创始人'}},
    {'name': '杨植麟', 'platform': 'weibo', 'platform_id': '杨植麟', 'rss_url': 'https://rsshub.app/weibo/user/杨植麟', 'category': 'company', 'importance_weight': 5, 'extra_config': {'note': '月之暗面(Kimi) 创始人'}},
    {'name': '张鹏', 'platform': 'weibo', 'platform_id': '张鹏', 'rss_url': 'https://rsshub.app/weibo/user/张鹏', 'category': 'company', 'importance_weight': 4, 'extra_config': {'note': '智谱AI CEO'}},
    {'name': '周鸿祎', 'platform': 'weibo', 'platform_id': '周鸿祎', 'rss_url': 'https://rsshub.app/weibo/user/周鸿祎', 'category': 'company', 'importance_weight': 4, 'extra_config': {'note': 'AI趋势和安全观点输出频繁'}},
    {'name': '李开复', 'platform': 'weibo', 'platform_id': '李开复', 'rss_url': 'https://rsshub.app/weibo/user/李开复', 'category': 'company', 'importance_weight': 4, 'extra_config': {'note': '宏观与投资视角重要来源'}},
    {'name': '彭志辉（稚晖君）', 'platform': 'weibo', 'platform_id': '稚晖君', 'rss_url': 'https://rsshub.app/weibo/user/稚晖君', 'category': 'company', 'importance_weight': 5, 'extra_config': {'note': '具身智能和机器人技术明星'}},
    {'name': '张亚勤', 'platform': 'weibo', 'platform_id': '张亚勤', 'rss_url': 'https://rsshub.app/weibo/user/张亚勤', 'category': 'academic', 'importance_weight': 4, 'extra_config': {'note': '产业与学术兼具'}},
    {'name': '唐杰', 'platform': 'weibo', 'platform_id': '唐杰', 'rss_url': 'https://rsshub.app/weibo/user/唐杰', 'category': 'academic', 'importance_weight': 5, 'extra_config': {'note': '大模型技术路径重要人物'}},
    {'name': '姚顺雨', 'platform': 'weibo', 'platform_id': '姚顺雨', 'rss_url': 'https://rsshub.app/weibo/user/姚顺雨', 'category': 'academic', 'importance_weight': 4, 'extra_config': {'note': '腾讯首席科学家'}},
    {'name': '林俊旸', 'platform': 'weibo', 'platform_id': '林俊旸', 'rss_url': 'https://rsshub.app/weibo/user/林俊旸', 'category': 'company', 'importance_weight': 4, 'extra_config': {'note': '阿里通义实验室负责人'}},
    {'name': '罗福莉', 'platform': 'weibo', 'platform_id': '罗福莉', 'rss_url': 'https://rsshub.app/weibo/user/罗福莉', 'category': 'company', 'importance_weight': 4, 'extra_config': {'note': '终端智能方向的重要实践者'}},
    {'name': '宝玉', 'platform': 'weibo', 'platform_id': '宝玉', 'rss_url': 'https://rsshub.app/weibo/user/宝玉', 'category': 'kol', 'importance_weight': 4, 'extra_config': {'note': '中文AI技术解读很快'}},
    {'name': '小互', 'platform': 'weibo', 'platform_id': '小互', 'rss_url': 'https://rsshub.app/weibo/user/小互', 'category': 'kol', 'importance_weight': 3, 'extra_config': {'note': 'AI应用发现型博主'}},
    {'name': '归藏', 'platform': 'weibo', 'platform_id': '归藏', 'rss_url': 'https://rsshub.app/weibo/user/归藏', 'category': 'kol', 'importance_weight': 3, 'extra_config': {'note': 'AIGC Prompt 方向'}},
    {'name': 'Orange AI', 'platform': 'weibo', 'platform_id': 'Orange AI', 'rss_url': 'https://rsshub.app/weibo/user/Orange AI', 'category': 'kol', 'importance_weight': 4, 'extra_config': {'note': 'AI自动化和Agent实践'}},
    {'name': '田奇', 'platform': 'weibo', 'platform_id': '田奇', 'rss_url': 'https://rsshub.app/weibo/user/田奇', 'category': 'academic', 'importance_weight': 4, 'extra_config': {'note': 'AI for Science 和视觉研究'}},
    {'name': '林达华', 'platform': 'weibo', 'platform_id': '林达华', 'rss_url': 'https://rsshub.app/weibo/user/林达华', 'category': 'academic', 'importance_weight': 4, 'extra_config': {'note': 'OpenMMLab 和开源生态'}},
    {'name': '夏立雪', 'platform': 'weibo', 'platform_id': '夏立雪', 'rss_url': 'https://rsshub.app/weibo/user/夏立雪', 'category': 'company', 'importance_weight': 4, 'extra_config': {'note': '关注推理成本和部署效率'}},
    {'name': '王仲远', 'platform': 'weibo', 'platform_id': '王仲远', 'rss_url': 'https://rsshub.app/weibo/user/王仲远', 'category': 'academic', 'importance_weight': 4, 'extra_config': {'note': '智源研究院院长'}},
    {'name': '橘鸦Juya', 'platform': 'bilibili', 'platform_id': '285286947', 'source_url': 'https://space.bilibili.com/285286947', 'category': 'kol', 'importance_weight': 4, 'extra_config': {'note': 'B站AI内容UP主'}},
    {'name': 'infinite灵感港', 'platform': 'bilibili', 'platform_id': '3493082576193678', 'source_url': 'https://space.bilibili.com/3493082576193678', 'category': 'kol', 'importance_weight': 4, 'extra_config': {'note': 'B站AI内容UP主'}},
]


def _default_source_url(platform: str, platform_id: str) -> str | None:
    if platform == 'twitter':
        return f'https://x.com/{platform_id}'
    if platform == 'weibo':
        return f'https://weibo.com/n/{quote(platform_id)}'
    if platform == 'bilibili':
        return f'https://space.bilibili.com/{platform_id}'
    return None


async def seed_default_monitor_sources(session: AsyncSession) -> int:
    settings = get_settings()
    if not settings.seed_default_monitor_sources:
        return 0

    inserted = 0
    for item in DEFAULT_MONITOR_SOURCES:
        result = await session.execute(
            select(MonitorSource).where(
                MonitorSource.platform == item['platform'],
                MonitorSource.platform_id == item['platform_id'],
            )
        )
        existing = result.scalar_one_or_none()
        if existing is not None:
            continue

        source = MonitorSource(
            name=item['name'],
            platform=item['platform'],
            platform_id=item['platform_id'],
            source_url=item.get('source_url') or _default_source_url(item['platform'], item['platform_id']),
            rss_url=item.get('rss_url'),
            category=item.get('category', 'kol'),
            importance_weight=item.get('importance_weight', 3),
            is_active=True,
            extra_config=item.get('extra_config', {}),
        )
        session.add(source)
        inserted += 1

    if inserted:
        await session.commit()
    return inserted
