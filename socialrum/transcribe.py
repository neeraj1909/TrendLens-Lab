"""Optional local speech transcription through a whisper.cpp CLI.

The caller must supply trusted local executables and a model. This module never
downloads models or configures remote services. FFmpeg input is restricted to
local files. The English tiny model does not establish general accuracy.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

MAX_MEDIA_BYTES = 100 * 1024 * 1024
MAX_DURATION_SECONDS = 10 * 60
MAX_SRT_BYTES = 5 * 1024 * 1024
MAX_PROCESS_STDOUT_BYTES = 16 * 1024
MAX_PROCESS_STDERR_BYTES = 64 * 1024
_MEDIA_SUFFIXES = {
    ".mp4",
    ".mov",
    ".mkv",
    ".webm",
    ".mp3",
    ".wav",
    ".m4a",
    ".aac",
    ".flac",
    ".ogg",
    ".opus",
}
_CUE = re.compile(
    r"(?m)^\s*(\d{2}:\d{2}:\d{2},\d{3})\s+-->\s+"
    r"(\d{2}:\d{2}:\d{2},\d{3})\s*$"
)


class TranscriptionError(RuntimeError):
    """A local transcription dependency or processing step failed."""


def _time_ms(value: str) -> int:
    hours, minutes, rest = value.split(":")
    seconds, millis = rest.split(",")
    return ((int(hours) * 60 + int(minutes)) * 60 + int(seconds)) * 1000 + int(millis)


def _validate_srt(data: bytes) -> str:
    if not data or len(data) > MAX_SRT_BYTES:
        raise TranscriptionError("Whisper produced an empty or oversized SRT file")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise TranscriptionError("Whisper produced an invalid UTF-8 SRT file") from exc
    cues = list(_CUE.finditer(text))
    if not cues:
        raise TranscriptionError("Whisper produced no timed subtitle cues")
    previous_end = -1
    for cue in cues:
        start, end = map(_time_ms, cue.groups())
        if start < previous_end or end <= start:
            raise TranscriptionError(
                "Whisper produced invalid or out-of-order subtitle timestamps"
            )
        previous_end = end
    return text


def transcribe_media(
    media_path: str | os.PathLike[str],
    *,
    whisper_cli: str | os.PathLike[str],
    model_path: str | os.PathLike[str],
    ffmpeg: str | os.PathLike[str] = "ffmpeg",
    ffprobe: str | os.PathLike[str] = "ffprobe",
    timeout_seconds: int = 120,
) -> str:
    """Return machine-generated SRT using explicitly configured local tools.

    Media must be a regular file no larger than 100 MiB, with a supported
    audio/video suffix, and no longer than 10 minutes. Processing occurs in a
    temporary directory, leaving the source untouched. Calls are argv-based;
    no shell is used. FFmpeg and ffprobe are restricted to local files. Process
    output is drained with fixed memory caps and timeouts.
    """
    media = Path(media_path).expanduser()
    cli = Path(whisper_cli).expanduser()
    model = Path(model_path).expanduser()
    if media.suffix.lower() not in _MEDIA_SUFFIXES:
        raise ValueError(f"Unsupported media extension: {media.suffix or '(none)'}")
    try:
        if not media.is_file():
            raise ValueError(f"Media must be a regular file: {media}")
        if media.stat().st_size <= 0 or media.stat().st_size > MAX_MEDIA_BYTES:
            raise ValueError("Media must be between 1 byte and 100 MiB")
        if not cli.is_file() or not os.access(cli, os.X_OK):
            raise TranscriptionError(
                f"whisper.cpp CLI is unavailable or not executable: {cli}"
            )
        if not model.is_file() or not os.access(model, os.R_OK):
            raise TranscriptionError(
                f"Local Whisper model is unavailable or unreadable: {model}"
            )
    except OSError as exc:
        raise ValueError(
            f"Cannot inspect transcription input or configuration: {exc}"
        ) from exc
    if (
        not isinstance(timeout_seconds, int)
        or isinstance(timeout_seconds, bool)
        or not 1 <= timeout_seconds <= 600
    ):
        raise ValueError("timeout_seconds must be an integer from 1 to 600")

    ffmpeg_exe = shutil.which(str(ffmpeg))
    ffprobe_exe = shutil.which(str(ffprobe))
    if not ffmpeg_exe:
        raise TranscriptionError(f"ffmpeg executable not found: {ffmpeg}")
    if not ffprobe_exe:
        raise TranscriptionError(f"ffprobe executable not found: {ffprobe}")

    def run(command: list[str], label: str, timeout: int = timeout_seconds):
        captured = {"stdout": bytearray(), "stderr": bytearray()}
        exceeded: set[str] = set()
        limits = {
            "stdout": MAX_PROCESS_STDOUT_BYTES,
            "stderr": MAX_PROCESS_STDERR_BYTES,
        }

        def drain(stream, name: str) -> None:
            while True:
                chunk = stream.read(8192)
                if not chunk:
                    return
                room = limits[name] + 1 - len(captured[name])
                if room > 0:
                    captured[name].extend(chunk[:room])
                if len(captured[name]) > limits[name]:
                    exceeded.add(name)

        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except OSError as exc:
            raise TranscriptionError(f"Could not run {label}: {exc}") from exc

        readers = [
            threading.Thread(
                target=drain, args=(process.stdout, "stdout"), daemon=True
            ),
            threading.Thread(
                target=drain, args=(process.stderr, "stderr"), daemon=True
            ),
        ]
        for reader in readers:
            reader.start()
        try:
            return_code = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            process.kill()
            process.wait()
            for reader in readers:
                reader.join()
            raise TranscriptionError(
                f"{label} exceeded the {timeout}-second time limit"
            ) from exc
        finally:
            if process.stdout:
                process.stdout.close()
            if process.stderr:
                process.stderr.close()
        for reader in readers:
            reader.join()
        if exceeded:
            names = " and ".join(sorted(exceeded))
            raise TranscriptionError(
                f"{label} exceeded the bounded {names} diagnostic output limit"
            )
        stdout = captured["stdout"].decode("utf-8", errors="replace")
        stderr = captured["stderr"].decode("utf-8", errors="replace")
        if return_code:
            detail = stderr.strip()[-1000:]
            raise TranscriptionError(
                f"{label} failed ({return_code}): {detail or 'no diagnostic output'}"
            )
        return subprocess.CompletedProcess(command, return_code, stdout, stderr)

    probe = run(
        [
            ffprobe_exe,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            "-protocol_whitelist",
            "file",
            "-i",
            str(media),
        ],
        "ffprobe media inspection",
    )
    try:
        duration = float(probe.stdout.strip())
    except ValueError as exc:
        raise TranscriptionError("ffprobe could not determine media duration") from exc
    if not 0 < duration <= MAX_DURATION_SECONDS:
        raise ValueError(
            "Media duration must be greater than 0 and no longer than 10 minutes"
        )

    with tempfile.TemporaryDirectory(prefix="socialrum-transcribe-") as temp_dir:
        temp = Path(temp_dir)
        wav_path = temp / "audio.wav"
        srt_base = temp / "transcript"
        srt_path = temp / "transcript.srt"
        run(
            [
                ffmpeg_exe,
                "-nostdin",
                "-hide_banner",
                "-loglevel",
                "error",
                "-protocol_whitelist",
                "file",
                "-i",
                str(media),
                "-map",
                "0:a:0",
                "-vn",
                "-ac",
                "1",
                "-ar",
                "16000",
                "-c:a",
                "pcm_s16le",
                str(wav_path),
            ],
            "ffmpeg audio extraction",
        )
        if not wav_path.is_file() or not 44 <= wav_path.stat().st_size <= 20_000_000:
            raise TranscriptionError(
                "ffmpeg produced missing or oversized 16 kHz audio"
            )
        run(
            [
                str(cli),
                "--no-gpu",
                "--threads",
                "2",
                "--model",
                str(model),
                "--output-srt",
                "--output-file",
                str(srt_base),
                str(wav_path),
            ],
            "local Whisper transcription",
            timeout=min(timeout_seconds, 300),
        )
        try:
            if (
                not srt_path.is_file()
                or not 0 < srt_path.stat().st_size <= MAX_SRT_BYTES
            ):
                raise TranscriptionError(
                    "Whisper produced a missing, empty, or oversized SRT file"
                )
            with srt_path.open("rb") as stream:
                srt_data = stream.read(MAX_SRT_BYTES + 1)
        except OSError as exc:
            raise TranscriptionError("Whisper did not create its SRT output") from exc
        return _validate_srt(srt_data)
