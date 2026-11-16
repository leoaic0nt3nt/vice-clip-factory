import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from viceclip import promo
from viceclip.autopilot import Autopilot
from viceclip.media import fingerprint
from viceclip.onboarding import private_json
from viceclip.queue import Queue
from viceclip.render import write_ass


class PromoTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.input = self.root / "source.mp4"
        self.input.write_bytes(b"original footage")
        self.source = {"id": "own", "path": str(self.input), "enabled": True,
                       "platforms": ["youtube", "instagram"]}
        self.config = {
            "source_policy": {"mode": "configured_sources", "user_responsible": True},
            "sources": [self.source],
            "accounts": [{"id": "yt", "platform": "youtube"}, {"id": "ig", "platform": "instagram"}],
            "storage": {"provider": "s3", "bucket": "private-media"},
            "style": {"brand": "@test", "sponsor_text": "OLD GLOBAL DEMO", "sponsored": False},
            "autopilot": {"min_score": 40, "asr": {"enabled": False}, "max_source_seconds": 60},
            "campaign": promo.validate("Мой продукт · Ссылка в профиле", "https://example.test/ref?code=LEO"),
        }
        self.plan = {"source_id": "own", "analysis": {"sha256": fingerprint(self.input)}, "transcript": [],
                     "clips": [{"id": "clip-01", "start": 0, "end": 6, "score": 90, "hook": "Игровой момент"}]}

    def worker(self):
        worker = Autopilot(self.config, base_dir=self.root)
        self.addCleanup(worker.close)
        worker.current_key = "fixture"
        worker._record("fixture", self.source)
        return worker

    def fake_render(self, input_file, plan, output, style):
        target = Path(output) / (plan["clips"][0]["id"] + ".mp4")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(style, ensure_ascii=False))
        target.with_suffix(".json").write_text(json.dumps({
            "source_id": "own", "output_sha256": fingerprint(target), "sponsored": bool(style.get("sponsored"))}))
        return [str(target)]

    def render_result(self, worker):
        work = self.root / "render"
        work.mkdir(exist_ok=True)
        with patch("viceclip.autopilot.probe", return_value={"format": {"duration": "6"}}), \
                patch("viceclip.cli.plan_media", return_value=copy.deepcopy(self.plan)), \
                patch("viceclip.render.render", side_effect=self.fake_render) as render:
            result = worker._process(self.source, self.input, work, self.config)
        return result, render

    def test_youtube_has_disclosed_promo_while_instagram_uses_clean_master(self):
        worker = self.worker()
        result, rendered = self.render_result(worker)
        self.assertEqual(rendered.call_count, 2)
        clean_style = json.loads(Path(result["clips"][0]["file"]).read_text())
        self.assertEqual(clean_style["sponsor_text"], "")
        self.assertFalse(clean_style["sponsored"])
        variant = result["clips"][0]["variants"]["youtube"]
        ad_style = json.loads(Path(variant["file"]).read_text())
        self.assertTrue(ad_style["sponsored"])
        self.assertEqual(ad_style["sponsor_text"], self.config["campaign"]["text"])
        queue = Queue(self.root / "queue.sqlite3")
        self.addCleanup(queue.db.close)
        jobs = worker._enqueue(self.source, result, self.config, queue)
        self.assertEqual(len(jobs), 2)
        by_platform = {job["platform"]: job for job in queue.list()}
        self.assertEqual(by_platform["youtube"]["file"], variant["file"])
        yt_metadata = json.loads(by_platform["youtube"]["metadata"])
        self.assertTrue(yt_metadata["sponsored"])
        self.assertIn("Ссылка в профиле", yt_metadata["caption"])
        self.assertNotIn(self.config["campaign"]["url"], yt_metadata["caption"])
        self.assertEqual(by_platform["instagram"]["file"], result["clips"][0]["file"])
        ig_metadata = json.loads(by_platform["instagram"]["metadata"])
        self.assertFalse(ig_metadata["sponsored"])
        self.assertNotIn("Мой продукт", ig_metadata["caption"])

    def test_real_promo_ass_does_not_say_demo(self):
        style = promo.youtube_style({}, self.config["campaign"])
        ass = self.root / "promo.ass"
        write_ass(ass, [], "Хук", 6, style)
        self.assertIn("Реклама", ass.read_text())
        self.assertIn("Ссылка в профиле", ass.read_text())
        self.assertNotIn("DEMO SLOT", ass.read_text())

    def test_changed_variant_is_rejected_after_restart(self):
        worker = self.worker()
        result, _ = self.render_result(worker)
        Path(result["clips"][0]["variants"]["youtube"]["file"]).write_bytes(b"changed ad")
        with self.assertRaisesRegex(ValueError, "Saved render changed"):
            worker._process(self.source, self.input, self.root / "render", self.config)
        queue = Queue(self.root / "queue.sqlite3")
        self.addCleanup(queue.db.close)
        with self.assertRaisesRegex(ValueError, "variant changed"):
            worker._enqueue(self.source, result, self.config, queue)
        self.assertEqual(queue.list(), [])

    def test_existing_render_keeps_its_campaign_when_settings_change_or_turn_off(self):
        worker = self.worker()
        original, _ = self.render_result(worker)
        self.config["campaign"] = {"enabled": False}
        with patch("viceclip.render.render") as render:
            same = worker._process(self.source, self.input, self.root / "render", self.config)
        render.assert_not_called()
        self.assertEqual(same, original)
        self.assertIn("Мой продукт", same["clips"][0]["variants"]["youtube"]["caption"])

    def test_without_campaign_the_legacy_render_path_is_unchanged(self):
        self.config.pop("campaign")
        result, rendered = self.render_result(self.worker())
        self.assertEqual(rendered.call_count, 1)
        self.assertNotIn("variants", result["clips"][0])
        self.assertEqual(json.loads(Path(result["clips"][0]["file"]).read_text())["sponsor_text"], "OLD GLOBAL DEMO")

    def test_configuration_validates_url_text_and_position_without_following_link(self):
        config_file = self.root / "config.json"
        private_json(config_file, self.config)
        with patch("urllib.request.urlopen") as request:
            result = promo.configure(config_file, text="Промокод LEO · Ссылка в профиле",
                                     url="https://example.test/ref?code=LEO", position="top")
        request.assert_not_called()
        self.assertEqual(result["applies_to"], "new_renders_only")
        self.assertEqual(result["campaign"]["position"], "top")
        self.assertIn("не кликабелен", result["profile_link_instruction"])
        self.assertEqual(config_file.stat().st_mode & 0o777, 0o600)
        for url in ("http://example.test", "https://", "javascript:alert(1)", "https://user:pass@example.test", "https://example.test/ref code"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                promo.validate("Нормальный текст", url)
        for text in ("", "x" * 81, "первая\nвторая", "текст\tдругой"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                promo.validate(text, "https://example.test")
        with self.assertRaises(ValueError):
            promo.validate("Текст", "https://example.test", "left")

    def test_turning_off_changes_only_configuration_not_existing_queue(self):
        queue = Queue(self.root / "queue.sqlite3")
        self.addCleanup(queue.db.close)
        file = self.root / "ready.mp4"
        file.write_bytes(b"already approved clip")
        queue.add(file, "own", "yt", {"title": "Старый выпуск"}, self.config)
        before = queue.list()
        config_file = self.root / "config.json"
        private_json(config_file, self.config)
        result = promo.disable(config_file)
        self.assertFalse(result["enabled"])
        self.assertEqual(queue.list(), before)
        self.assertEqual(json.loads(config_file.read_text())["style"]["sponsor_text"], "")

    def test_invalid_instagram_campaign_cannot_silently_skip_partnership_requirements(self):
        self.config["campaign"]["platforms"] = ["youtube", "instagram"]
        with self.assertRaisesRegex(ValueError, "Instagram"):
            promo.active_campaign(self.config)


if __name__ == "__main__":
    unittest.main()
