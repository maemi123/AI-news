from __future__ import annotations

import asyncio
from dataclasses import dataclass

from app.config import get_settings


class AudioStorageError(RuntimeError):
    pass


@dataclass(slots=True)
class UploadedAudio:
    key: str
    public_url: str


class AudioStorageService:
    def __init__(self) -> None:
        self.settings = get_settings()

    async def upload_audio(self, *, audio_bytes: bytes, key: str, content_type: str) -> UploadedAudio:
        if not self.settings.has_valid_audio_storage:
            raise AudioStorageError('Audio storage configuration is incomplete.')

        provider = self.settings.audio_storage_provider.strip().lower()
        endpoint = self.settings.audio_storage_endpoint.strip().lower()
        if provider == 'oss' or 'aliyuncs.com' in endpoint:
            return await self._upload_with_oss(audio_bytes=audio_bytes, key=key, content_type=content_type)

        return await self._upload_with_s3(audio_bytes=audio_bytes, key=key, content_type=content_type)

    async def _upload_with_oss(self, *, audio_bytes: bytes, key: str, content_type: str) -> UploadedAudio:
        try:
            import oss2
        except ImportError as exc:
            raise AudioStorageError('oss2 is required for Aliyun OSS upload. Run pip install oss2.') from exc

        auth = oss2.Auth(
            self.settings.audio_storage_access_key,
            self.settings.audio_storage_secret_key,
        )
        endpoint = self.settings.audio_storage_endpoint.strip()
        bucket_name = self.settings.audio_storage_bucket.strip()
        bucket = oss2.Bucket(auth, endpoint, bucket_name)
        headers = {'Content-Type': content_type}
        await asyncio.to_thread(bucket.put_object, key, audio_bytes, headers=headers)
        public_base_url = self.settings.audio_storage_public_base_url.strip().rstrip('/')
        return UploadedAudio(
            key=key,
            public_url=f'{public_base_url}/{key.lstrip("/")}',
        )

    async def _upload_with_s3(self, *, audio_bytes: bytes, key: str, content_type: str) -> UploadedAudio:
        try:
            import boto3
        except ImportError as exc:
            raise AudioStorageError('boto3 is required for S3-compatible audio storage upload.') from exc

        client_kwargs = {
            'aws_access_key_id': self.settings.audio_storage_access_key,
            'aws_secret_access_key': self.settings.audio_storage_secret_key,
            'region_name': self.settings.audio_storage_region or 'auto',
        }
        if self.settings.audio_storage_endpoint.strip():
            client_kwargs['endpoint_url'] = self.settings.audio_storage_endpoint.strip()

        client = boto3.client('s3', **client_kwargs)
        await asyncio.to_thread(
            client.put_object,
            Bucket=self.settings.audio_storage_bucket,
            Key=key,
            Body=audio_bytes,
            ContentType=content_type,
        )
        public_base_url = self.settings.audio_storage_public_base_url.strip().rstrip('/')
        return UploadedAudio(
            key=key,
            public_url=f'{public_base_url}/{key.lstrip("/")}',
        )
