from __future__ import annotations

import asyncio
import io
import subprocess
import tempfile
import wave
from dataclasses import dataclass
from pathlib import Path

import httpx
import imageio_ffmpeg

from app.config import get_settings


class TTSServiceError(RuntimeError):
    pass


@dataclass(slots=True)
class SynthesizedClip:
    speaker: str
    audio_bytes: bytes
    duration_seconds: float


class DialogueTTSService:
    def __init__(self) -> None:
        self.settings = get_settings()

    async def synthesize_dialogue(self, dialogue_lines: list[dict[str, str]]) -> tuple[bytes, int, str, str]:
        if not self.settings.has_valid_tts_config:
            raise TTSServiceError('TTS_API_KEY or TTS_MODEL is not configured.')

        clips: list[SynthesizedClip] = []
        for line in dialogue_lines:
            clip = await self._synthesize_line(
                speaker=str(line['speaker']),
                text=str(line['text']),
            )
            clips.append(clip)

        merged_wav, duration_seconds = await asyncio.to_thread(self._merge_wav_clips, clips)
        mp3_audio = await asyncio.to_thread(self._convert_wav_to_mp3, merged_wav)
        return mp3_audio, duration_seconds, 'audio/mpeg', 'mp3'

    async def _synthesize_line(self, *, speaker: str, text: str) -> SynthesizedClip:
        voice = self.settings.tts_voice_male if speaker == 'host_a' else self.settings.tts_voice_female
        headers = {
            'Authorization': f'Bearer {self.settings.tts_api_key}',
            'Content-Type': 'application/json',
        }
        payload = {
            'model': self.settings.tts_model,
            'voice': voice,
            'input': text,
            'response_format': 'wav',
        }

        try:
            async with httpx.AsyncClient(timeout=180.0) as client:
                response = await client.post(
                    self.settings.tts_speech_url,
                    headers=headers,
                    json=payload,
                )
                response.raise_for_status()
                audio_bytes = response.content
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text[:500]
            raise TTSServiceError(f'TTS service returned an error: {detail}') from exc
        except httpx.HTTPError as exc:
            raise TTSServiceError(f'TTS request failed: {exc}') from exc

        duration = self._read_wav_duration(audio_bytes)
        return SynthesizedClip(speaker=speaker, audio_bytes=audio_bytes, duration_seconds=duration)

    def _read_wav_duration(self, audio_bytes: bytes) -> float:
        with wave.open(io.BytesIO(audio_bytes), 'rb') as wav_reader:
            frames = wav_reader.getnframes()
            rate = wav_reader.getframerate()
            return frames / rate if rate else 0.0

    def _merge_wav_clips(self, clips: list[SynthesizedClip]) -> tuple[bytes, int]:
        if not clips:
            raise TTSServiceError('No synthesized clips available.')

        output = io.BytesIO()
        silence_ms = 180
        sample_params = None

        with wave.open(output, 'wb') as writer:
            for index, clip in enumerate(clips):
                with wave.open(io.BytesIO(clip.audio_bytes), 'rb') as reader:
                    params = (
                        reader.getnchannels(),
                        reader.getsampwidth(),
                        reader.getframerate(),
                        reader.getcomptype(),
                        reader.getcompname(),
                    )
                    if sample_params is None:
                        sample_params = params
                        writer.setnchannels(params[0])
                        writer.setsampwidth(params[1])
                        writer.setframerate(params[2])
                    elif params != sample_params:
                        raise TTSServiceError('TTS wav clips use inconsistent audio parameters.')

                    frames = reader.readframes(reader.getnframes())
                    writer.writeframes(frames)

                    if index < len(clips) - 1:
                        silence_frames = int(reader.getframerate() * (silence_ms / 1000))
                        silence_bytes = b'\x00' * silence_frames * reader.getsampwidth() * reader.getnchannels()
                        writer.writeframes(silence_bytes)

        if sample_params is None:
            raise TTSServiceError('Unable to determine wav parameters for synthesized audio.')

        merged_audio = output.getvalue()
        duration_seconds = int(round(self._read_wav_duration(merged_audio)))
        return merged_audio, max(1, duration_seconds)

    def _convert_wav_to_mp3(self, wav_audio: bytes) -> bytes:
        ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
        with tempfile.TemporaryDirectory(prefix='ai_news_podcast_') as temp_dir:
            temp_path = Path(temp_dir)
            wav_path = temp_path / 'podcast.wav'
            mp3_path = temp_path / 'podcast.mp3'
            wav_path.write_bytes(wav_audio)
            result = subprocess.run(
                [
                    ffmpeg_exe,
                    '-y',
                    '-i',
                    str(wav_path),
                    '-codec:a',
                    'libmp3lame',
                    '-b:a',
                    '96k',
                    str(mp3_path),
                ],
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace',
                check=False,
            )
            if result.returncode != 0 or not mp3_path.exists():
                raise TTSServiceError(f'Failed to convert wav to mp3: {result.stderr.strip() or result.stdout.strip()}')
            return mp3_path.read_bytes()
