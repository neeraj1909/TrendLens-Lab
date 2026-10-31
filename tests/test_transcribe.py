import subprocess
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from socialrum.transcribe import TranscriptionError, transcribe_media


SRT = "1\n00:00:00,000 --> 00:00:01,200\nHello local world.\n"


class FakeProcess:
    def __init__(self, stdout=b"", stderr=b"", returncode=0, timeout=False):
        self.stdout = BytesIO(stdout)
        self.stderr = BytesIO(stderr)
        self.returncode = returncode
        self.timeout = timeout

    def wait(self, timeout=None):
        if self.timeout and timeout is not None:
            raise subprocess.TimeoutExpired("fake-process", timeout)
        return self.returncode

    def kill(self):
        self.timeout = False


class TranscribeMediaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.media = self.root / "clip.wav"
        self.media.write_bytes(b"source media")
        self.cli = self.root / "whisper-cli"
        self.cli.write_bytes(b"executable placeholder")
        self.cli.chmod(0o700)
        self.model = self.root / "model.bin"
        self.model.write_bytes(b"model")
        self.source_bytes = self.media.read_bytes()

    def tearDown(self):
        self.temp.cleanup()

    def _fake_run(self, command, **kwargs):
        if command[0] == "/usr/bin/ffprobe":
            return FakeProcess(b"2.0\n")
        if command[0] == "/usr/bin/ffmpeg":
            Path(command[-1]).write_bytes(b"W" * 100)
            return FakeProcess()
        if command[0] == str(self.cli):
            output_base = Path(command[command.index("--output-file") + 1])
            Path(f"{output_base}.srt").write_text(SRT, encoding="utf-8")
            return FakeProcess()
        self.fail(f"unexpected command: {command}")

    @patch(
        "socialrum.transcribe.shutil.which",
        side_effect=lambda name: {
            "ffprobe": "/usr/bin/ffprobe",
            "ffmpeg": "/usr/bin/ffmpeg",
        }.get(str(name)),
    )
    @patch("socialrum.transcribe.subprocess.Popen")
    def test_extracts_local_audio_and_returns_validated_srt(self, run, _which):
        run.side_effect = self._fake_run

        result = transcribe_media(
            self.media, whisper_cli=self.cli, model_path=self.model
        )

        self.assertEqual(result, SRT)
        self.assertEqual(self.media.read_bytes(), self.source_bytes)
        self.assertEqual(run.call_count, 3)
        commands = [call.args[0] for call in run.call_args_list]
        self.assertIn("-ac", commands[1])
        self.assertIn("16000", commands[1])
        self.assertIn("--no-gpu", commands[2])
        self.assertIn("--output-srt", commands[2])
        self.assertIn("-protocol_whitelist", commands[0])
        self.assertIn("file", commands[0])
        self.assertIn("-protocol_whitelist", commands[1])
        self.assertIn("file", commands[1])
        for command in commands:
            self.assertIsInstance(command, list)

    @patch(
        "socialrum.transcribe.shutil.which",
        side_effect=lambda name: {
            "ffprobe": "/usr/bin/ffprobe",
            "ffmpeg": "/usr/bin/ffmpeg",
        }.get(str(name)),
    )
    @patch("socialrum.transcribe.subprocess.Popen")
    def test_rejects_invalid_timestamps_and_preserves_source(self, run, _which):
        def fake(command, **kwargs):
            result = self._fake_run(command, **kwargs)
            if command[0] == str(self.cli):
                output_base = Path(command[command.index("--output-file") + 1])
                Path(f"{output_base}.srt").write_text(
                    "1\n00:00:02,000 --> 00:00:01,000\nBad\n", encoding="utf-8"
                )
            return result

        run.side_effect = fake

        with self.assertRaisesRegex(TranscriptionError, "timestamps"):
            transcribe_media(self.media, whisper_cli=self.cli, model_path=self.model)
        self.assertEqual(self.media.read_bytes(), self.source_bytes)

    def test_rejects_missing_cli_or_model_before_running_tools(self):
        with patch("socialrum.transcribe.subprocess.Popen") as run:
            with self.assertRaisesRegex(TranscriptionError, "CLI is unavailable"):
                transcribe_media(
                    self.media, whisper_cli=self.root / "missing", model_path=self.model
                )
            with self.assertRaisesRegex(TranscriptionError, "model is unavailable"):
                transcribe_media(
                    self.media, whisper_cli=self.cli, model_path=self.root / "missing"
                )
        run.assert_not_called()

    def test_rejects_oversized_media_and_unsupported_extension(self):
        self.media.write_bytes(b"x" * (100 * 1024 * 1024 + 1))
        with self.assertRaisesRegex(ValueError, "100 MiB"):
            transcribe_media(self.media, whisper_cli=self.cli, model_path=self.model)
        unsupported = self.root / "clip.exe"
        unsupported.write_bytes(b"x")
        with self.assertRaisesRegex(ValueError, "Unsupported media extension"):
            transcribe_media(unsupported, whisper_cli=self.cli, model_path=self.model)

    def test_rejects_duration_above_ten_minutes(self):
        with (
            patch(
                "socialrum.transcribe.shutil.which",
                side_effect=lambda name: {
                    "ffprobe": "/usr/bin/ffprobe",
                    "ffmpeg": "/usr/bin/ffmpeg",
                }.get(str(name)),
            ),
            patch(
                "socialrum.transcribe.subprocess.Popen",
                return_value=FakeProcess(b"601\n"),
            ) as run,
        ):
            with self.assertRaisesRegex(ValueError, "10 minutes"):
                transcribe_media(
                    self.media, whisper_cli=self.cli, model_path=self.model
                )
        run.assert_called_once()

    def test_rejects_remote_input_and_bounds_subprocess_output(self):
        with patch("socialrum.transcribe.subprocess.Popen") as process:
            with self.assertRaisesRegex(ValueError, "regular file"):
                transcribe_media(
                    "https://example.invalid/clip.wav",
                    whisper_cli=self.cli,
                    model_path=self.model,
                )
            process.assert_not_called()

        with (
            patch(
                "socialrum.transcribe.shutil.which",
                side_effect=lambda name: {
                    "ffprobe": "/usr/bin/ffprobe",
                    "ffmpeg": "/usr/bin/ffmpeg",
                }.get(str(name)),
            ),
            patch(
                "socialrum.transcribe.subprocess.Popen",
                return_value=FakeProcess(b"x" * (16 * 1024 + 1)),
            ) as process,
        ):
            with self.assertRaisesRegex(TranscriptionError, "bounded stdout"):
                transcribe_media(
                    self.media, whisper_cli=self.cli, model_path=self.model
                )
        process.assert_called_once()

        with (
            patch(
                "socialrum.transcribe.shutil.which",
                side_effect=lambda name: {
                    "ffprobe": "/usr/bin/ffprobe",
                    "ffmpeg": "/usr/bin/ffmpeg",
                }.get(str(name)),
            ),
            patch(
                "socialrum.transcribe.subprocess.Popen",
                return_value=FakeProcess(b"2\n", b"x" * (64 * 1024 + 1)),
            ) as process,
        ):
            with self.assertRaisesRegex(TranscriptionError, "bounded stderr"):
                transcribe_media(
                    self.media, whisper_cli=self.cli, model_path=self.model
                )
        process.assert_called_once()

    def test_reports_timeout_and_cleans_temporary_files(self):
        temp_paths = []
        real_temp_dir = tempfile.TemporaryDirectory

        def tracked_temp_dir(*args, **kwargs):
            created = real_temp_dir(*args, **kwargs)
            temp_paths.append(Path(created.name))
            return created

        with (
            patch(
                "socialrum.transcribe.tempfile.TemporaryDirectory",
                side_effect=tracked_temp_dir,
            ),
            patch(
                "socialrum.transcribe.shutil.which",
                side_effect=lambda name: {
                    "ffprobe": "/usr/bin/ffprobe",
                    "ffmpeg": "/usr/bin/ffmpeg",
                }.get(str(name)),
            ),
            patch("socialrum.transcribe.subprocess.Popen") as run,
        ):

            def timeout(command, **kwargs):
                if command[0] == "/usr/bin/ffprobe":
                    return FakeProcess(b"2\n")
                if command[0] == "/usr/bin/ffmpeg":
                    Path(command[-1]).write_bytes(b"W" * 100)
                    return FakeProcess()
                return FakeProcess(timeout=True)

            run.side_effect = timeout
            with self.assertRaisesRegex(TranscriptionError, "time limit"):
                transcribe_media(
                    self.media, whisper_cli=self.cli, model_path=self.model
                )
        self.assertTrue(temp_paths)
        self.assertFalse(temp_paths[0].exists())


if __name__ == "__main__":
    unittest.main()
