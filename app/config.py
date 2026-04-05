import json
from functools import lru_cache
from typing import List

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = 'AI News System'
    debug: bool = True

    bilibili_sessdata: str = Field(default='', alias='BILIBILI_SESSDATA')

    deepseek_api_key: str = Field(default='', alias='DEEPSEEK_API_KEY')
    deepseek_base_url: str = Field(default='https://api.deepseek.com/v1', alias='DEEPSEEK_BASE_URL')
    deepseek_model: str = Field(default='deepseek-chat', alias='DEEPSEEK_MODEL')

    whisper_api_key: str = Field(default='', alias='WHISPER_API_KEY')
    whisper_base_url: str = Field(default='https://api.openai.com/v1', alias='WHISPER_BASE_URL')
    whisper_model: str = Field(default='whisper-1', alias='WHISPER_MODEL')

    wecom_webhook_url: str = Field(default='', alias='WECOM_WEBHOOK_URL')
    database_url: str = Field(default='sqlite+aiosqlite:///./ai_news.db', alias='DATABASE_URL')
    target_up_ids_raw: str = Field(default='', alias='TARGET_UP_IDS')

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

    def masked_dict(self) -> dict:
        data = self.model_dump(by_alias=True)
        for key in ('DEEPSEEK_API_KEY', 'WHISPER_API_KEY', 'BILIBILI_SESSDATA', 'WECOM_WEBHOOK_URL'):
            value = data.get(key, '')
            if value:
                data[key] = value[:6] + '***'
        data['TARGET_UP_IDS'] = json.dumps(self.target_up_ids, ensure_ascii=False)
        return data


@lru_cache
def get_settings() -> Settings:
    return Settings()
