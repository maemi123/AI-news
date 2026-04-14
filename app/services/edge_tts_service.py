from __future__ import annotations
import re
import shutil
import subprocess
import uuid
from pathlib import Path

import imageio_ffmpeg


class EdgeTTSServiceError(RuntimeError):
    pass


class EdgeDialogueTTSService:
    async def synthesize_dialogue(
        self,
        dialogue_lines: list[dict[str, str]],
        *,
        male_voice: str,
        female_voice: str,
    ) -> tuple[bytes, int, str, str]:
        if not dialogue_lines:
            raise EdgeTTSServiceError('No dialogue lines available for Edge TTS synthesis.')

        try:
            import edge_tts
        except ImportError as exc:
            raise EdgeTTSServiceError('edge-tts is not installed. Run pip install edge-tts.') from exc

        ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
        temp_root = Path.cwd() / '.tmp_edge_tts'
        temp_root.mkdir(parents=True, exist_ok=True)
        temp_path = temp_root / f'ai_news_edge_tts_{uuid.uuid4().hex}'
        temp_path.mkdir(parents=True, exist_ok=True)
        try:
            mp3_segments: list[Path] = []

            for index, line in enumerate(dialogue_lines, start=1):
                voice = male_voice if str(line.get('speaker')) == 'host_a' else female_voice
                target = temp_path / f'segment_{index:03d}.mp3'
                communicate = edge_tts.Communicate(str(line.get('text') or '').strip(), voice)
                await communicate.save(str(target))
                if not target.exists() or target.stat().st_size == 0:
                    raise EdgeTTSServiceError(f'Edge TTS produced an empty segment for line {index}.')
                mp3_segments.append(target)

            silence_path = temp_path / 'silence.mp3'
            silence_result = subprocess.run(
                [
                    ffmpeg_exe,
                    '-y',
                    '-f',
                    'lavfi',
                    '-t',
                    '0.18',
                    '-i',
                    'anullsrc=r=24000:cl=mono',
                    '-codec:a',
                    'libmp3lame',
                    '-b:a',
                    '96k',
                    str(silence_path),
                ],
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace',
                check=False,
            )
            if silence_result.returncode != 0:
                raise EdgeTTSServiceError(
                    f'Failed to generate silence clip for Edge TTS merge: {silence_result.stderr.strip() or silence_result.stdout.strip()}'
                )

            concat_list = temp_path / 'concat.txt'
            concat_lines: list[str] = []
            for index, segment in enumerate(mp3_segments):
                concat_lines.append(f"file '{segment.as_posix()}'")
                if index < len(mp3_segments) - 1:
                    concat_lines.append(f"file '{silence_path.as_posix()}'")
            concat_list.write_text('\n'.join(concat_lines), encoding='utf-8')

            output_path = temp_path / 'podcast.mp3'
            merge_result = subprocess.run(
                [
                    ffmpeg_exe,
                    '-y',
                    '-f',
                    'concat',
                    '-safe',
                    '0',
                    '-i',
                    str(concat_list),
                    '-codec:a',
                    'libmp3lame',
                    '-b:a',
                    '96k',
                    str(output_path),
                ],
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace',
                check=False,
            )
            if merge_result.returncode != 0 or not output_path.exists():
                raise EdgeTTSServiceError(
                    f'Failed to merge Edge TTS mp3 clips: {merge_result.stderr.strip() or merge_result.stdout.strip()}'
                )

            duration_seconds = self._read_duration_seconds(ffmpeg_exe=ffmpeg_exe, path=output_path)
            return output_path.read_bytes(), duration_seconds, 'audio/mpeg', 'mp3'
        finally:
            shutil.rmtree(temp_path, ignore_errors=True)

    def _read_duration_seconds(self, *, ffmpeg_exe: str, path: Path) -> int:
        result = subprocess.run(
            [ffmpeg_exe, '-i', str(path)],
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            check=False,
        )
        stderr = result.stderr or ''
        match = re.search(r'Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)', stderr)
        if not match:
            return 0
        hours = int(match.group(1))
        minutes = int(match.group(2))
        seconds = float(match.group(3))
        return max(1, int(round(hours * 3600 + minutes * 60 + seconds)))
