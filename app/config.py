import json
from functools import lru_cache
from typing import List

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PLACEHOLDER_SECRET_VALUES = {
    '',
    'your_sessdata_here',
    'your_deepseek_api_key',
    'your_pushplus_token',
    'your_token_here',
}


class Settings(BaseSettings):
    app_name: str = 'AI News System'
    debug: bool = Field(default=False, alias='DEBUG')

    bilibili_sessdata: str = Field(default='', alias='BILIBILI_SESSDATA')
    bilibili_bili_jct: str = Field(default='', alias='BILIBILI_BILI_JCT')
    bilibili_buvid3: str = Field(default='', alias='BILIBILI_BUVID3')

    deepseek_api_key: str = Field(default='', alias='DEEPSEEK_API_KEY')
    deepseek_base_url: str = Field(default='https://api.deepseek.com/v1', alias='DEEPSEEK_BASE_URL')
    deepseek_model: str = Field(default='deepseek-chat', alias='DEEPSEEK_MODEL')

    whisper_api_key: str = Field(default='', alias='WHISPER_API_KEY')
    whisper_base_url: str = Field(default='https://api.openai.com/v1', alias='WHISPER_BASE_URL')
    whisper_model: str = Field(default='whisper-large-v3', alias='WHISPER_MODEL')

    pushplus_token: str = Field(default='', alias='PUSHPLUS_TOKEN')
    wecom_webhook_url: str = Field(default='', alias='WECOM_WEBHOOK_URL')
    rsshub_base_url: str = Field(default='', alias='RSSHUB_BASE_URL')
    database_url: str = Field(default='sqlite+aiosqlite:///./ai_news.db', alias='DATABASE_URL')
    target_up_ids_raw: str = Field(default='', alias='TARGET_UP_IDS')

    scheduler_enabled: bool = Field(default=True, alias='SCHEDULER_ENABLED')
    daily_report_hour: int = Field(default=8, alias='DAILY_REPORT_HOUR')
    daily_report_minute: int = Field(default=0, alias='DAILY_REPORT_MINUTE')
    fetch_lookback_hours: int = Field(default=24, alias='FETCH_LOOKBACK_HOURS')
    scheduler_timezone: str = Field(default='Asia/Shanghai', alias='SCHEDULER_TIMEZONE')
    seed_default_monitor_sources: bool = Field(default=True, alias='SEED_DEFAULT_MONITOR_SOURCES')

    model_config = SettingsConfigDict(
        env_file='.env',
        env_file_encoding='utf-8',
        case_sensitive=False,
        extra='ignore',
        populate_by_name=True,
    )

    @property
    def target_up_ids(self) -> List[int]:
        values = []
        for item in self.target_up_ids_raw.split(','):
            item = item.strip()
            if item.isdigit():
                values.append(int(item))
        return values

    @property
    def deepseek_chat_completions_url(self) -> str:
        return self.deepseek_base_url.rstrip('/') + '/chat/completions'

    @property
    def whisper_transcriptions_url(self) -> str:
        return self.whisper_base_url.rstrip('/') + '/audio/transcriptions'

    @property
    def effective_bilibili_sessdata(self) -> str:
        value = self.bilibili_sessdata.strip()
        if not value:
            return ''
        if value.lower() in PLACEHOLDER_SECRET_VALUES:
            return ''
        return value

    @property
    def effective_bilibili_bili_jct(self) -> str:
        value = self.bilibili_bili_jct.strip()
        if not value or value.lower() in PLACEHOLDER_SECRET_VALUES:
            return ''
        return value

    @property
    def effective_bilibili_buvid3(self) -> str:
        value = self.bilibili_buvid3.strip()
        if not value or value.lower() in PLACEHOLDER_SECRET_VALUES:
            return ''
        return value

    @property
    def has_valid_wecom_webhook(self) -> bool:
        prefix = 'https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key='
        return self.wecom_webhook_url.startswith(prefix) and len(self.wecom_webhook_url) > len(prefix)

    @property
    def has_valid_pushplus_token(self) -> bool:
        return len(self.pushplus_token.strip()) >= 16

    @property
    def effective_rsshub_base_url(self) -> str:
        value = self.rsshub_base_url.strip().rstrip('/')
        if not value:
            return ''
        if value.startswith('http://') or value.startswith('https://'):
            return value
        return f'http://{value}'

    def masked_dict(self) -> dict:
        data = self.model_dump(by_alias=True)
        for key in (
            'DEEPSEEK_API_KEY',
            'WHISPER_API_KEY',
            'BILIBILI_SESSDATA',
            'BILIBILI_BILI_JCT',
            'BILIBILI_BUVID3',
            'WECOM_WEBHOOK_URL',
        ):
            value = data.get(key, '')
            if value:
                data[key] = value[:6] + '***'
        data['TARGET_UP_IDS'] = json.dumps(self.target_up_ids, ensure_ascii=False)
        return data


@lru_cache
def get_settings() -> Settings:
    return Settings()
