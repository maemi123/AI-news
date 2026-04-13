from __future__ import annotations

from app.config import get_settings
from app.models import SystemSetting
from app.schemas import SystemSettingsResponse, SystemSettingsUpdate
from app.services.fetcher import FetcherService
from app.services.windows_scheduler import WindowsTaskSchedulerError, WindowsTaskSchedulerService


def mask_token(token: str | None) -> str | None:
    if not token:
        return None
    token = token.strip()
    if len(token) <= 8:
        return '*' * len(token)
    return f'{token[:4]}***{token[-4:]}'


class SystemSettingsService:
    def __init__(self) -> None:
        self.config = get_settings()
        self.windows_scheduler = WindowsTaskSchedulerService()

    async def get_or_create(self, session) -> SystemSetting:
        settings = await session.get(SystemSetting, 1)
        if settings is not None:
            return settings

        settings = SystemSetting(
            id=1,
            scheduler_enabled=self.config.scheduler_enabled,
            daily_report_hour=self.config.daily_report_hour,
            daily_report_minute=self.config.daily_report_minute,
            fetch_lookback_hours=self.config.fetch_lookback_hours,
            scheduler_timezone=self.config.scheduler_timezone,
            push_provider='pushplus',
            pushplus_token=self.config.pushplus_token.strip() or None,
            seed_default_monitor_sources=self.config.seed_default_monitor_sources,
        )
        session.add(settings)
        await session.commit()
        await session.refresh(settings)
        return settings

    async def read_response(self, session) -> SystemSettingsResponse:
        settings = await self.get_or_create(session)
        scheduler_status = await self.windows_scheduler.get_status()
        return SystemSettingsResponse(
            scheduler_enabled=settings.scheduler_enabled,
            daily_report_hour=settings.daily_report_hour,
            daily_report_minute=settings.daily_report_minute,
            scheduler_timezone=settings.scheduler_timezone,
            fetch_lookback_hours=settings.fetch_lookback_hours,
            scheduler_backend='windows_task',
            scheduler_task_registered=scheduler_status.registered,
            scheduler_task_name=scheduler_status.task_name,
            scheduler_next_run_at=scheduler_status.next_run_at,
            scheduler_last_run_at=scheduler_status.last_run_at,
            scheduler_last_task_result=scheduler_status.last_task_result,
            scheduler_last_sync_error=scheduler_status.last_sync_error,
            scheduler_executor_path=scheduler_status.executor_path,
            push_provider=settings.push_provider,
            pushplus_configured=bool(settings.pushplus_token),
            pushplus_token_masked=mask_token(settings.pushplus_token),
            seed_default_monitor_sources=settings.seed_default_monitor_sources,
        )

    async def update(self, session, payload: SystemSettingsUpdate) -> SystemSettingsResponse:
        settings = await self.get_or_create(session)

        try:
            await self.windows_scheduler.sync_task(
                enabled=payload.scheduler_enabled,
                hour=payload.daily_report_hour,
                minute=payload.daily_report_minute,
            )
        except WindowsTaskSchedulerError:
            await session.rollback()
            raise

        settings.scheduler_enabled = payload.scheduler_enabled
        settings.daily_report_hour = payload.daily_report_hour
        settings.daily_report_minute = payload.daily_report_minute
        settings.scheduler_timezone = payload.scheduler_timezone
        settings.fetch_lookback_hours = payload.fetch_lookback_hours
        settings.push_provider = 'pushplus'

        new_token = (payload.pushplus_token or '').strip()
        if new_token:
            settings.pushplus_token = new_token

        await session.commit()
        await session.refresh(settings)
        FetcherService.invalidate_cache()
        return await self.read_response(session)
