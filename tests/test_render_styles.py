"""Verify portrait styling on decoded FFmpeg output."""
import io
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageChops

from viceclip.media import fingerprint, probe
from viceclip.promo import youtube_style
from viceclip.render import render


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg is required")
class RenderStyleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(temp.cleanup)
        cls.root = Path(temp.name)
        cls.source = cls.root / "source.mp4"
        subprocess.run([
            "ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=gray:s=320x180:r=30:d=5",
            "-vf", "drawbox=x=0:y=0:w=96:h=180:color=darkred:t=fill,"
                   "drawbox=x=224:y=0:w=96:h=180:color=blue:t=fill",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(cls.source)
        ], check=True)
        cls.sha = fingerprint(cls.source)

    def export(self, name, **style):
        plan = {
            "source_id": "render-style-test",
            "analysis": {"sha256": self.sha, "has_audio": False},
            "transcript": [{
                "start": 1.117, "end": 2.05, "text": "Первая вторая",
                "words": [
                    {"word": "Первая", "start": 1.117, "end": 1.5},
                    {"word": "вторая", "start": 1.75, "end": 2.05},
                ],
            }],
            "clips": [{"id": "clip-01", "start": .7, "end": 4.7, "hook": "Догонит ли патруль?",
                       "crop_center_x": .9 if name == "portrait" else .5}],
        }
        return Path(render(self.source, plan, self.root / name, {"preset": "ultrafast", **style})[0])

    @staticmethod
    def frame(path, number):
        result = subprocess.check_output([
            "ffmpeg", "-v", "error", "-i", str(path), "-vf", f"select=eq(n\\,{number})",
            "-frames:v", "1", "-c:v", "png", "-f", "image2pipe", "-"
        ])
        return Image.open(io.BytesIO(result)).convert("RGB")

    @staticmethod
    def pixels(image):
        if hasattr(image, "get_flattened_data"):
            return image.get_flattened_data()
        return image.getdata()

    @classmethod
    def amber(cls, image, box):
        return [(index % (box[2] - box[0])) + box[0]
                for index, (r, g, b) in enumerate(cls.pixels(image.crop(box)))
                if r > 180 and 100 < g < 215 and 28 < b < 140 and r - g > 38]

    def test_portrait_fills_frame_using_requested_focus_while_fit_keeps_hud(self):
        fit = self.export("fit", brand="")
        portrait = self.export("portrait", layout="portrait", caption_mode="phrase")
        full = self.frame(portrait, 0)
        framed = self.frame(fit, 0)
        self.assertEqual(full.size, (1080, 1920))
        self.assertAlmostEqual(float(probe(portrait)["format"]["duration"]), 4, delta=.04)
        for point in ((540, 20), (540, 1880), (540, 970)):
            r, g, b = full.getpixel(point)
            self.assertGreater(b - max(r, g), 120, "Right-hand subject must fill portrait")
        r, g, b = framed.getpixel((150, 960))
        self.assertGreater(r - max(g, b), 70, "Fit preserves the left side of the gameplay frame")
        self.assertLess(max(framed.getpixel((540, 20))), 50, "Fit pads the top")

    def test_word_accent_and_compact_ad_follow_actual_frames(self):
        style = youtube_style(
            {"layout": "portrait", "caption_mode": "words", "hook_duration": .8},
            {"text": "Демо продукта", "position": "top"},
        )
        video = self.export("promoted", **style)
        subtitles = (0, 1080, 1080, 1470)
        label = (290, 170, 790, 230)
        body = (345, 230, 735, 312)
        before, first, gap, second, before_end, after_end, cta = [
            self.frame(video, n) for n in (12, 13, 30, 32, 40, 41, 90)
        ]

        # Relative to source seek .7, the first word starts at .417 (frame 13).
        self.assertFalse(self.amber(before, subtitles))
        first_x = self.amber(first, subtitles)
        self.assertGreater(len(first_x), 100)
        self.assertFalse(self.amber(gap, subtitles))
        second_x = self.amber(second, subtitles)
        self.assertGreater(len(second_x), 100)
        self.assertGreater(min(second_x) - min(first_x), 200)
        self.assertGreater(len(self.amber(before_end, subtitles)), 100)
        self.assertFalse(self.amber(after_end, subtitles))
        self.assertFalse(self.amber(first, label))
        self.assertGreater(len(self.amber(second, label)), 20)
        self.assertGreater(len(self.amber(cta, label)), 20)
        self.assertIsNotNone(ImageChops.difference(second.crop(body), cta.crop(body)).getbbox())
        self.assertEqual(second.getpixel((985, 1550)), cta.getpixel((985, 1550)))

        clean = self.export("clean", layout="portrait", caption_mode="words", hook_duration=.8)
        self.assertFalse(self.amber(self.frame(clean, 32), label))
        self.assertIs(json.loads(video.with_suffix(".json").read_text())["sponsored"], True)
        self.assertIs(json.loads(clean.with_suffix(".json").read_text())["sponsored"], False)

    def test_bottom_badge_honors_campaign_position_with_separate_caption_area(self):
        style = youtube_style(
            {"layout": "portrait", "caption_mode": "words", "hook_duration": .8},
            {"text": "Демо продукта", "position": "bottom"},
        )
        video = self.export("bottom", **style)
        frame = self.frame(video, 32)
        self.assertFalse(self.amber(frame, (300, 170, 780, 230)))
        self.assertGreater(len(self.amber(frame, (300, 1520, 780, 1570))), 20)
        self.assertGreater(len(self.amber(frame, (0, 1100, 1080, 1370))), 100)

    def test_meme_uses_static_hook_and_suppresses_dialogue(self):
        video = self.export("meme", layout="portrait", caption_mode="meme")
        box = (100, 190, 980, 410)
        first, during_speech, late = [self.frame(video, n) for n in (12, 32, 118)]
        first_hook = first.crop(box)
        self.assertGreater(self.bright_pixels(first_hook), 1000)
        for frame in (during_speech, late):
            delta = ImageChops.difference(first_hook, frame.crop(box)).convert("L").histogram()
            self.assertLess(sum(delta[12:]), 150)
        self.assertEqual(self.bright_pixels(during_speech.crop((0, 1100, 1080, 1450))), 0)
        self.assertFalse(self.amber(during_speech, (0, 1100, 1080, 1450)))
        self.assertEqual(json.loads(video.with_suffix(".json").read_text())["display_captions"], [])

    @classmethod
    def bright_pixels(cls, image):
        return sum(r > 225 and g > 225 and b > 225 for r, g, b in cls.pixels(image))

    def test_meme_honors_explicit_hook_duration(self):
        video = self.export("meme-short-hook", layout="portrait", caption_mode="meme", hook_duration=.8)
        box = (100, 190, 980, 410)
        self.assertGreater(self.bright_pixels(self.frame(video, 23).crop(box)), 1000)
        self.assertEqual(self.bright_pixels(self.frame(video, 24).crop(box)), 0)
