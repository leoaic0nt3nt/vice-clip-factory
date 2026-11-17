import contextlib
import io
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from PIL import Image

from viceclip import promo, raster
from viceclip.cli import main
from viceclip.creative import layers, read_artwork
from viceclip.onboarding import base_config, private_json


class MotionPromoTests(unittest.TestCase):
    def test_artwork_is_fixed_for_existing_plans_and_new_placement_is_reused(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            profile = root / "config.json"
            private_json(profile, base_config(profile))
            art = root / "creator.png"
            Image.new("RGBA", (96, 96), "lime").save(art)
            first = promo.configure(profile, text="Creator Toolkit", url="https://example.test",
                                    artwork=art, position="top", start=4, duration=3)["campaign"]
            Image.new("RGBA", (96, 96), "red").save(art)
            with contextlib.redirect_stdout(io.StringIO()):
                exit_code = main(["promo", "--config", str(profile), "--artwork", str(art),
                                  "--position", "bottom"])
            self.assertEqual(exit_code, 0)
            second = promo.status(profile)["campaign"]
            art.unlink()
            self.assertNotEqual(first["artwork"]["sha256"], second["artwork"]["sha256"])
            self.assertEqual(read_artwork(first["artwork"]).getpixel((0, 0)), (0, 255, 0, 255))
            self.assertEqual(read_artwork(second["artwork"]).getpixel((0, 0)), (255, 0, 0, 255))
            self.assertEqual((second["start"], second["duration"], second["position"]), (4, 3, "bottom"))
            animated = layers(promo.youtube_style({}, second), 20, 3.2, raster.font, raster._text)
            self.assertEqual(min(start for start, _, _, _ in animated), 4)
            self.assertEqual(max(end for _, end, _, _ in animated), 7)
            self.assertGreater(min(y for _, _, _, y in animated), 1450)
            self.assertTrue(all(card.width < 1080 for _, _, card, _ in animated))
            self.assertIn("Link in profile", promo.campaign_caption(second))

    def test_invalid_placement_and_non_images_leave_active_campaign_intact(self):
        with TemporaryDirectory() as directory:
            profile = Path(directory) / "config.json"
            private_json(profile, base_config(profile))
            promo.configure(profile, text="Creator Toolkit", url="https://example.test")
            old = profile.read_bytes()
            bad = Path(directory) / "not-an-image.mp4"
            bad.write_bytes(b"not a graphic")
            for options in ({"artwork": bad}, {"duration": 60}, {"start": float("nan")}, {"cta": "   "}):
                with self.subTest(options=options), self.assertRaises(ValueError):
                    promo.configure(profile, text="Changed", url="https://example.test", **options)
                self.assertEqual(profile.read_bytes(), old)
