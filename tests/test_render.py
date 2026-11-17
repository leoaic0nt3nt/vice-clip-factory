"""Real FFmpeg regression for seeking and concatenating late source passages."""
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from viceclip.media import fingerprint, probe
from viceclip.render import render


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg is required")
class SeekRenderTests(unittest.TestCase):
    def test_late_passages_keep_picture_timing_after_a_cut(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "source.mp4"
            subprocess.run([
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                "testsrc2=size=160x90:rate=30:duration=12", "-f", "lavfi", "-i",
                "sine=frequency=440:sample_rate=48000:duration=12", "-c:v", "libx264",
                "-preset", "ultrafast", "-crf", "12", "-pix_fmt", "yuv420p", "-c:a", "aac",
                "-shortest", str(source)], check=True)
            plan = {"source_id": "test", "analysis": {"sha256": fingerprint(source), "has_audio": True},
                    "transcript": [], "clips": [{"id": "clip-01", "start": 7, "end": 11,
                                                     "hook": "Timing test"}]}
            with patch("viceclip.render.kept_ranges", return_value=[(7, 8), (10, 11)]):
                target = Path(render(source, plan, root / "out", {"preset": "ultrafast"})[0])
            info = probe(target)
            self.assertAlmostEqual(float(info["format"]["duration"]), 2, delta=.05)
            self.assertTrue(any(s["codec_type"] == "audio" for s in info["streams"]))

            def frame(path, time, filters):
                return subprocess.check_output([
                    "ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", str(time), "-i", str(path),
                    "-frames:v", "1", "-vf", filters, "-pix_fmt", "rgb24", "-f", "rawvideo", "-"])

            for output_time, source_time in ((.5, 7.5), (1.5, 10.5)):
                expected = frame(source, source_time, "scale=160:90")
                actual = frame(target, output_time, "crop=1080:608:0:656,scale=160:90")
                self.assertEqual(len(expected), len(actual))
                error = sum(abs(a-b) for a, b in zip(actual, expected)) / len(expected)
                self.assertLess(error, 12, "Rendered picture must match the selected source time")
