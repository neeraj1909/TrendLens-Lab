"""Local video utilities for creator workflows.

Captions are supplied by the user as SRT or WebVTT files. This module burns
them into a video with the locally installed ffmpeg; it does not transcribe
audio or contact a remote service.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

MAX_VIDEO_BYTES = 500 * 1024 * 1024
MAX_SUBTITLE_BYTES = 5 * 1024 * 1024
MAX_OUTPUT_BYTES = 1024 * 1024 * 1024
_TIMESTAMP = re.compile(
    r"(?:\d{2}:)?\d{2}:\d{2}[,.]\d{3}\s+-->\s+(?:\d{2}:)?\d{2}:\d{2}[,.]\d{3}"
)
_OUTPUT_SUFFIXES = {".mp4", ".mov", ".mkv"}


def _read_limited_file(path: Path, limit: int, label: str) -> bytes:
    try:
        if not path.is_file():
            raise ValueError(f"{label} must be a regular file: {path}")
        size = path.stat().st_size
        if size <= 0 or size > limit:
            raise ValueError(f"{label} must be between 1 and {limit} bytes")
        with path.open("rb") as stream:
            data = stream.read(limit + 1)
    except OSError as exc:
        raise ValueError(f"Cannot read {label}: {exc}") from exc
    if len(data) > limit:
        raise ValueError(f"{label} exceeds the {limit}-byte limit")
    return data


def _validate_subtitles(data: bytes, suffix: str) -> None:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("Subtitle file must be UTF-8 encoded") from exc
    if suffix == ".vtt" and not text.lstrip().startswith("WEBVTT"):
        raise ValueError("WebVTT file must start with WEBVTT")
    if not _TIMESTAMP.search(text):
        raise ValueError("Subtitle file must contain at least one timed SRT/VTT cue")


def burn_captions(
    video_path: str | os.PathLike[str],
    subtitle_path: str | os.PathLike[str],
    output_path: str | os.PathLike[str],
) -> Path:
    """Burn supplied SRT/WebVTT captions into a local video using ffmpeg.

    Inputs are capped at 500 MiB (video) and 5 MiB (subtitles). Output is
    written atomically and capped at 1 GiB. Existing output files are never
    overwritten. Raises ``ValueError`` for invalid paths/content and
    ``RuntimeError`` when ffmpeg is unavailable or cannot render the file.
    """
    video = Path(video_path).expanduser()
    subtitle = Path(subtitle_path).expanduser()
    output = Path(output_path).expanduser()
    if subtitle.suffix.lower() not in {".srt", ".vtt"}:
        raise ValueError("Subtitle file must use .srt or .vtt extension")
    if output.suffix.lower() not in _OUTPUT_SUFFIXES:
        raise ValueError("Output must use .mp4, .mov, or .mkv extension")
    if not output.parent.is_dir():
        raise ValueError(f"Output directory does not exist: {output.parent}")
    try:
        resolved_output = output.resolve(strict=False)
        if resolved_output in {video.resolve(), subtitle.resolve()}:
            raise ValueError("Output path must differ from both input paths")
        if output.exists():
            raise ValueError(f"Output already exists; refusing to overwrite: {output}")
    except OSError as exc:
        raise ValueError(f"Invalid path: {exc}") from exc

    _read_limited_file(video, MAX_VIDEO_BYTES, "Video")
    subtitle_data = _read_limited_file(subtitle, MAX_SUBTITLE_BYTES, "Subtitle")
    _validate_subtitles(subtitle_data, subtitle.suffix.lower())
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg is required for local caption rendering")

    temp_output: Path | None = None
    try:
        with tempfile.TemporaryDirectory(prefix="socialrum-captions-") as temp_dir:
            temp = Path(temp_dir)
            # A fixed, controlled filename keeps filter syntax independent of
            # user-provided paths and avoids shell or filter injection.
            staged_subtitle = temp / ("captions" + subtitle.suffix.lower())
            staged_subtitle.write_bytes(subtitle_data)
            temp_output = output.parent / f".{output.stem}.socialrum-tmp{output.suffix}"
            if temp_output.exists():
                raise RuntimeError(f"Temporary output already exists: {temp_output}")
            filter_arg = f"subtitles=filename={staged_subtitle.as_posix()}"
            command = [
                ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                "-i", str(video), "-vf", filter_arg, "-map", "0:v:0", "-map", "0:a?",
                "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
                "-c:a", "aac", "-movflags", "+faststart", str(temp_output),
            ]
            result = subprocess.run(command, capture_output=True, text=True, timeout=180, check=False)
            if result.returncode != 0:
                detail = result.stderr.strip()[-1200:]
                raise RuntimeError(f"ffmpeg could not render captions: {detail}")
            if not temp_output.is_file() or temp_output.stat().st_size == 0:
                raise RuntimeError("ffmpeg produced no video output")
            if temp_output.stat().st_size > MAX_OUTPUT_BYTES:
                raise RuntimeError(f"Rendered video exceeds the {MAX_OUTPUT_BYTES}-byte output limit")
            # Hard-link creation fails atomically if another process created the
            # requested target since the initial check; it never clobbers it.
            os.link(temp_output, output)
            return output
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("ffmpeg caption rendering exceeded the 180-second limit") from exc
    except FileExistsError as exc:
        raise ValueError(f"Output already exists; refusing to overwrite: {output}") from exc
    except OSError as exc:
        raise RuntimeError(f"Could not run local ffmpeg rendering: {exc}") from exc
    finally:
        if temp_output is not None:
            try:
                temp_output.unlink(missing_ok=True)
            except OSError:
                pass
