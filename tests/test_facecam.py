"""Source webcam validation, reframing and cut-timing regression."""
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from viceclip.facecam import portrait_center, validate_facecam
from viceclip.media import fingerprint, probe
from viceclip.render import render
from viceclip.scoring import kept_ranges


def _source(folder, duration):
    source = folder / "source.mp4"
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
        f"color=gray:size=320x180:rate=30:duration={duration}", "-f", "lavfi", "-i",
        f"sine=frequency=440:sample_rate=48000:duration={duration}", "-vf",
        "drawbox=x=8:y=44:w=72:h=92:color=blue:t=fill,"
        "drawbox=x=8:y=44:w=72:h=92:color=lime:t=fill:enable='lt(t,1)'",
        "-af", "volume=0:enable='between(t,2,5)'", "-c:v", "libx264",
        "-pix_fmt", "yuv420p", "-c:a", "aac", str(source)
    ], check=True)
    return source


class FacecamTests(unittest.TestCase):
    def test_validation_rejects_bad_positions_and_regions(self):
        self.assertEqual(validate_facecam({"rect": [0, .1, .4, .3]}),
                         {"rect": [0, .1, .4, .3], "position": "left"})
        for raw in ([], {}, {"rect": [False, 0, .3, .4]}, {"rect": [.9, 0, .3, .4]},
                    {"rect": [0, 0, .3, float("nan")]}, {"rect": [0, 0, .3, .4], "position": "center"}):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                validate_facecam(raw)

    def test_portrait_preserves_framing_or_avoids_splitting_the_camera(self):
        aspect = 16 / 9
        self.assertEqual(portrait_center([.02, .2, .18, .32], aspect, .5, .5), (.5, .5))
        shifted, _ = portrait_center([.2, .2, .18, .32], aspect, .5, .5)
        self.assertGreater(shifted - (9 / 16) / aspect / 2, .38)
        self.assertIsNone(portrait_center([.4, .2, .2, .32], aspect, .5, .5))
        self.assertIsNone(portrait_center([.02, .2, .18, .32], 9 / 16, .5, .5))

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg is required")
    def test_facecam_uses_same_kept_ranges_and_stays_below_top_promo(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = _source(root, 2)
            ranges = [(.2, .7), (1.2, 1.7)]
            analysis = {"sha256": fingerprint(source), "has_audio": True}
            plan = {"source_id": "test", "analysis": analysis, "transcript": [],
                    "clips": [{"id": "clip-01", "start": .2, "end": 1.7, "hook": "The setup"}]}
            facecam = {"position": "right", "rect": [.025, .25, .22, .5]}
            style = {"layout": "portrait", "preset": "ultrafast", "facecam": facecam,
                     "ad_format": "motion", "sponsor_position": "top", "sponsor_text": "Demo",
                     "sponsor_start": 0, "sponsor_duration": 1}
            with patch("viceclip.render.kept_ranges", return_value=ranges):
                target = Path(render(source, plan, root / "out", style)[0])
            metadata = json.loads(target.with_suffix(".json").read_text())
            self.assertEqual(metadata["facecam"], {**facecam, "rendered": True})
            self.assertEqual(metadata["kept_source_ranges"], [list(r) for r in ranges])
            self.assertAlmostEqual(metadata["duration"], 1, delta=.06)
            self.assertTrue(any(stream["codec_type"] == "audio" for stream in probe(target)["streams"]))

            def pixel(time, x, y):
                data = subprocess.check_output([
                    "ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", str(time),
                    "-i", str(target), "-frames:v", "1", "-vf", f"crop=8:8:{x}:{y},scale=1:1,format=rgb24",
                    "-f", "rawvideo", "-"
                ])
                return tuple(data)

            red, green, blue = pixel(.22, 800, 760)
            self.assertGreater(green, max(red, blue) + 150)
            red, green, blue = pixel(.72, 800, 760)
            self.assertGreater(blue, max(red, green) + 150)
            red, green, blue = pixel(.72, 100, 760)
            self.assertLess(max(red, green, blue) - min(red, green, blue), 12)

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg is required")
    def test_source_facecam_overrides_style_and_stunt_keeps_silent_action(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = _source(root, 6)
            analysis = {"sha256": fingerprint(source), "has_audio": True,
                        "audio_dbfs": [-18, -18, -60, -60, -60, -18], "motion": [5, 5, 0, 0, 0, 5]}
            self.assertEqual(kept_ranges(0, 6, analysis, [], True), [(0, 2.2), (4.8, 6)])
            candidate = {"id": "clip-01", "start": 0, "end": 6, "hook": "Watch the landing",
                         "evidence": {"kind": "stunt", "setup_at": .5, "action_at": 3, "payoff_at": 4}}
            global_cam = {"rect": [.70, .15, .25, .4], "position": "left"}
            source_cam = {"rect": [.025, .25, .22, .5], "position": "right"}
            style = {"layout": "portrait", "preset": "ultrafast", "facecam": global_cam, "trim_silence": True}
            plan = {"source_id": "test", "analysis": analysis, "transcript": [], "clips": [candidate]}
            for name, camera in (("source-camera", source_cam), ("source-off", None)):
                with self.subTest(name=name):
                    target = Path(render(source, {**plan, "facecam": camera}, root / name, style)[0])
                    metadata = json.loads(target.with_suffix(".json").read_text())
                    self.assertEqual(metadata["kept_source_ranges"], [[0, 6]])
                    self.assertAlmostEqual(metadata["duration"], 6, delta=.06)
                    if camera is None:
                        self.assertNotIn("facecam", metadata)
                    else:
                        self.assertEqual(metadata["facecam"], {**source_cam, "rendered": True})
