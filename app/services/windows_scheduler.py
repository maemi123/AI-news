from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

TASK_NAME = 'AI-News-Daily-Push'
RETRY_TASKS = (
    (TASK_NAME, 0, 1),
    (f'{TASK_NAME}-Retry-1', 30, 2),
    (f'{TASK_NAME}-Retry-2', 60, 3),
)


class WindowsTaskSchedulerError(RuntimeError):
    pass


@dataclass(slots=True)
class WindowsTaskStatus:
    registered: bool
    task_name: str = TASK_NAME
    next_run_at: datetime | None = None
    last_run_at: datetime | None = None
    last_task_result: int | None = None
    last_sync_error: str | None = None
    executor_path: str | None = None


class WindowsTaskSchedulerService:
    def __init__(self) -> None:
        self.project_root = Path(__file__).resolve().parents[2]

    @property
    def is_supported(self) -> bool:
        return os.name == 'nt'

    def resolve_python_command(self) -> str:
        venv_python = self.project_root / '.venv' / 'Scripts' / 'python.exe'
        if venv_python.exists():
            return str(venv_python)
        return 'py -3'

    def _build_task_action(self, attempt_slot: int) -> tuple[str, str]:
        venv_python = self.project_root / '.venv' / 'Scripts' / 'python.exe'
        if venv_python.exists():
            return str(venv_python), f'-m app.run_scheduled_job --attempt-slot {attempt_slot}'
        return 'py.exe', f'-3 -m app.run_scheduled_job --attempt-slot {attempt_slot}'

    def _ensure_supported(self) -> None:
        if not self.is_supported:
            raise WindowsTaskSchedulerError('Current environment does not support Windows scheduled tasks.')

    def _run_command(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            args,
            cwd=self.project_root,
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            check=False,
        )

    def _parse_datetime(self, value: Any) -> datetime | None:
        if not value:
            return None
        text = str(value).strip()
        if not text or text.startswith('0001-01-01') or text.startswith('1899-12-30'):
            return None

        match = re.fullmatch(r'/Date\((?P<timestamp>-?\d+)\)/', text)
        if match:
            timestamp_ms = int(match.group('timestamp'))
            parsed = datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc)
            return None if parsed.year < 2005 else parsed

        normalized = text.replace('Z', '+00:00')
        try:
            return datetime.fromisoformat(normalized)
        except ValueError:
            pass

        for fmt in (
            '%Y/%m/%d %H:%M:%S',
            '%Y-%m-%d %H:%M:%S',
            '%Y/%m/%d %H:%M',
            '%Y-%m-%d %H:%M',
            '%m/%d/%Y %I:%M:%S %p',
            '%m/%d/%Y %I:%M %p',
        ):
            try:
                return datetime.strptime(text, fmt)
            except ValueError:
                continue
        return None

    def _extract_executor_path(self, execute: str | None) -> str | None:
        if execute and execute.lower().endswith('python.exe'):
            return execute
        if execute and execute.lower().endswith('py.exe'):
            return self.resolve_python_command()
        return None

    def _query_single_task_status_sync(self, task_name: str) -> WindowsTaskStatus:
        self._ensure_supported()

        task_command = [
            'powershell',
            '-NoProfile',
            '-Command',
            (
                f"$task = Get-ScheduledTask -TaskName '{task_name}' -ErrorAction SilentlyContinue; "
                "if (-not $task) { exit 0 } "
                "$task | Select-Object "
                "TaskName,State,"
                "@{Name='Execute';Expression={$_.Actions[0].Execute}},"
                "@{Name='Arguments';Expression={$_.Actions[0].Arguments}},"
                "@{Name='WorkingDirectory';Expression={$_.Actions[0].WorkingDirectory}} "
                '| ConvertTo-Json -Compress'
            ),
        ]
        info_command = [
            'powershell',
            '-NoProfile',
            '-Command',
            (
                f"$info = Get-ScheduledTaskInfo -TaskName '{task_name}' -ErrorAction SilentlyContinue; "
                "if (-not $info) { exit 0 } "
                "$info | Select-Object NextRunTime,LastRunTime,LastTaskResult | ConvertTo-Json -Compress"
            ),
        ]

        task_result = self._run_command(task_command)
        if task_result.returncode != 0:
            raise WindowsTaskSchedulerError((task_result.stderr or task_result.stdout or 'Failed to query Windows task.').strip())

        task_stdout = task_result.stdout.strip()
        if not task_stdout:
            return WindowsTaskStatus(registered=False, task_name=task_name, executor_path=self.resolve_python_command())

        info_result = self._run_command(info_command)
        if info_result.returncode != 0:
            raise WindowsTaskSchedulerError(
                (info_result.stderr or info_result.stdout or 'Failed to query Windows task details.').strip()
            )

        task_data = json.loads(task_stdout)
        info_data: dict[str, Any] = {}
        if info_result.stdout.strip():
            info_data = json.loads(info_result.stdout)

        return WindowsTaskStatus(
            registered=True,
            task_name=str(task_data.get('TaskName') or task_name),
            next_run_at=self._parse_datetime(info_data.get('NextRunTime')),
            last_run_at=self._parse_datetime(info_data.get('LastRunTime')),
            last_task_result=int(info_data['LastTaskResult']) if info_data.get('LastTaskResult') is not None else None,
            executor_path=self._extract_executor_path(task_data.get('Execute')) or self.resolve_python_command(),
        )

    def _query_task_status_sync(self) -> WindowsTaskStatus:
        primary_status = self._query_single_task_status_sync(TASK_NAME)
        if not primary_status.registered:
            return primary_status

        for task_name, _, _ in RETRY_TASKS[1:]:
            status = self._query_single_task_status_sync(task_name)
            if not status.registered:
                return WindowsTaskStatus(
                    registered=False,
                    task_name=TASK_NAME,
                    last_sync_error=f'Missing retry task: {task_name}',
                    executor_path=primary_status.executor_path,
                )
        return primary_status

    def _create_or_update_task_sync(self, *, hour: int, minute: int) -> None:
        self._ensure_supported()
        for task_name, minute_offset, attempt_slot in RETRY_TASKS:
            total_minutes = hour * 60 + minute + minute_offset
            scheduled_hour = (total_minutes // 60) % 24
            scheduled_minute = total_minutes % 60
            execute, arguments = self._build_task_action(attempt_slot)
            command = (
                "$action = New-ScheduledTaskAction "
                f"-Execute '{execute}' "
                f"-Argument '{arguments}' "
                f"-WorkingDirectory '{self.project_root}'; "
                f"$trigger = New-ScheduledTaskTrigger -Daily -At '{scheduled_hour:02d}:{scheduled_minute:02d}'; "
                f"Register-ScheduledTask -TaskName '{task_name}' -Action $action -Trigger $trigger -Force | Out-Null"
            )
            result = self._run_command(['powershell', '-NoProfile', '-Command', command])
            if result.returncode != 0:
                raise WindowsTaskSchedulerError(
                    (result.stderr or result.stdout or f'Failed to create Windows task {task_name}.').strip()
                )

    def _delete_task_sync(self) -> None:
        self._ensure_supported()
        for task_name, _, _ in RETRY_TASKS:
            result = self._run_command(
                [
                    'powershell',
                    '-NoProfile',
                    '-Command',
                    (
                        f"$task = Get-ScheduledTask -TaskName '{task_name}' -ErrorAction SilentlyContinue; "
                        "if ($task) { "
                        f"Unregister-ScheduledTask -TaskName '{task_name}' -Confirm:$false"
                        ' }'
                    ),
                ]
            )
            if result.returncode != 0:
                raise WindowsTaskSchedulerError(
                    (result.stderr or result.stdout or f'Failed to delete Windows task {task_name}.').strip()
                )

    async def get_status(self) -> WindowsTaskStatus:
        try:
            return await asyncio.to_thread(self._query_task_status_sync)
        except WindowsTaskSchedulerError as exc:
            return WindowsTaskStatus(
                registered=False,
                last_sync_error=str(exc),
                executor_path=self.resolve_python_command() if self.is_supported else None,
            )

    async def sync_task(self, *, enabled: bool, hour: int, minute: int) -> WindowsTaskStatus:
        self._ensure_supported()
        if enabled:
            await asyncio.to_thread(self._create_or_update_task_sync, hour=hour, minute=minute)
        else:
            await asyncio.to_thread(self._delete_task_sync)
        return await asyncio.to_thread(self._query_task_status_sync)

    async def run_task_now(self) -> None:
        self._ensure_supported()

        def _run() -> None:
            result = self._run_command(
                [
                    'powershell',
                    '-NoProfile',
                    '-Command',
                    f"Start-ScheduledTask -TaskName '{TASK_NAME}'",
                ]
            )
            if result.returncode != 0:
                raise WindowsTaskSchedulerError((result.stderr or result.stdout or 'Failed to start Windows task.').strip())

        await asyncio.to_thread(_run)
