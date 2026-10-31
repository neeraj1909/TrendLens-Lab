import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from socialrum.video import burn_captions


class BurnCaptionsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.video = self.root / "source.mp4"
        if not shutil.which("ffmpeg"):
            self.skipTest("ffmpeg is not installed")
        made = subprocess.run(
            ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
             "-i", "color=c=black:s=320x240:d=1", "-c:v", "libx264", "-pix_fmt", "yuv420p",
             str(self.video)], capture_output=True, check=False,
        )
        if made.returncode != 0:
            self.skipTest("ffmpeg cannot generate the tiny local test fixture")
        self.subtitle = self.root / "captions.srt"
        self.subtitle.write_text("1\n00:00:00,000 --> 00:00:00,900\nHello local captions\n", encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def test_burns_user_supplied_srt_into_local_video(self):
        output = self.root / "captioned.mp4"
        result = burn_captions(self.video, self.subtitle, output)

        self.assertEqual(result, output)
        self.assertGreater(output.stat().st_size, 0)
        self.assertTrue(self.video.exists())
        self.assertTrue(self.subtitle.exists())

    def test_rejects_invalid_subtitle_without_creating_output_or_changing_inputs(self):
        self.subtitle.write_text("not a subtitle", encoding="utf-8")
        original_video = self.video.read_bytes()
        output = self.root / "captioned.mp4"

        with self.assertRaisesRegex(ValueError, "timed"):
            burn_captions(self.video, self.subtitle, output)

        self.assertFalse(output.exists())
        self.assertEqual(self.video.read_bytes(), original_video)

    def test_refuses_to_overwrite_existing_output(self):
        output = self.root / "captioned.mp4"
        output.write_bytes(b"keep this")

        with self.assertRaisesRegex(ValueError, "already exists"):
            burn_captions(self.video, self.subtitle, output)

        self.assertEqual(output.read_bytes(), b"keep this")

    def test_rejects_container_without_matching_encoder_configuration(self):
        with self.assertRaisesRegex(ValueError, "Output must use .mp4, .mov, or .mkv"):
            burn_captions(self.video, self.subtitle, self.root / "captioned.webm")
        self.assertFalse((self.root / "captioned.webm").exists())

    def test_reports_missing_ffmpeg_without_touching_output(self):
        output = self.root / "captioned.mp4"
        with patch("socialrum.video.shutil.which", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "ffmpeg is required"):
                burn_captions(self.video, self.subtitle, output)
        self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
