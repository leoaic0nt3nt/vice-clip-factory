import csv
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from viceclip import promo, revenue
from viceclip.autopilot import Autopilot
from viceclip.queue import Queue


class RevenueTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.ledger = self.root / "ledger.sqlite3"
        self.queue = self.root / "publish.sqlite3"
        self.csv = self.root / "commissions.csv"
        self.campaign = promo.validate("Партнёрский оффер", "https://example.test/shop?ref=FIRST&ref=SECOND%20X&key=&utm_source=old#more")
        self.offer = revenue.campaign_id(self.campaign)
        self.clip = "a" * 64

    def event(self, **changes):
        return {"event_id": "network:123", "campaign_id": self.offer, "clip_id": self.clip,
                "status": "paid", "amount": "8.123456", "currency": "USD",
                "updated_at": "2026-11-18T10:00:00+03:00", **changes}

    def write(self, *events):
        with self.csv.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(self.event()))
            writer.writeheader()
            writer.writerows(events)
        return self.csv

    def test_link_preserves_partner_parameters_and_profiles_have_no_clip(self):
        link = revenue.tracking_link(self.campaign)
        self.assertIsNone(link["clip_id"])
        self.assertIn("ref=FIRST&ref=SECOND%20X&key=", link["url"])
        url = urlsplit(link["url"])
        self.assertEqual(url.fragment, "more")
        self.assertEqual(parse_qs(url.query)["utm_source"], ["youtube"])
        self.assertEqual(parse_qs(url.query)["utm_campaign"], [self.offer])
        self.assertEqual(parse_qs(url.query)["utm_content"], ["profile"])
        tracked = revenue.tracking_link(self.campaign, clip_id=self.clip)
        self.assertEqual(parse_qs(urlsplit(tracked["url"]).query)["utm_content"], [self.clip])
        self.assertEqual(revenue.campaign_id({**self.campaign, "text": "Другой текст"}), self.offer)
        with self.assertRaisesRegex(ValueError, "SHA256"):
            revenue.tracking_link(self.campaign, clip_id="clip-01")

    def test_instagram_link_is_a_profile_link_for_the_same_offer(self):
        link = revenue.tracking_link(self.campaign, platform="instagram")
        tags = parse_qs(urlsplit(link["url"]).query)
        self.assertEqual(tags["utm_source"], ["instagram"])
        self.assertEqual(tags["utm_campaign"], [self.offer])
        self.assertEqual(tags["utm_content"], ["profile"])
        self.assertIsNone(link["clip_id"])
        with self.assertRaisesRegex(ValueError, "без --clip-id"):
            revenue.tracking_link(self.campaign, platform="instagram", clip_id=self.clip)

    def test_import_is_idempotent_and_adds_paid_commissions_per_currency(self):
        path = self.write(self.event(), self.event(event_id="network:124", amount="0.000004"),
                          self.event(event_id="second:gid://Order/123", currency="EUR", amount="10", clip_id=""),
                          self.event(event_id="network:125", status="pending", amount="500"))
        self.assertEqual(revenue.import_conversions(self.ledger, path)["inserted"], 4)
        self.assertEqual(revenue.import_conversions(self.ledger, path)["unchanged"], 4)
        report = revenue.report(self.queue, ledger_path=self.ledger)
        money = report["partner_ledger"]
        self.assertEqual(money["net_paid_commission_by_currency"], {"USD": "8.12346", "EUR": "10"})
        self.assertEqual(money["conversions_by_status"], {"pending": 1, "paid": 3, "refunded": 0, "rejected": 0})
        campaign = report["campaigns"][0]
        self.assertEqual(campaign["campaign_only"]["net_paid_commission_by_currency"], {"EUR": "10"})
        self.assertEqual(campaign["clips"][0]["net_paid_commission_by_currency"], {"USD": "8.12346"})
        self.assertEqual(self.ledger.stat().st_mode & 0o777, 0o600)

    def test_newer_refund_replaces_paid_and_stale_export_cannot_restore_it(self):
        paid = self.event()
        revenue.import_conversions(self.ledger, self.write(paid))
        refund = self.event(status="refunded", updated_at="2026-11-18T07:01:00Z")
        self.assertEqual(revenue.import_conversions(self.ledger, self.write(refund))["updated"], 1)
        self.assertEqual(revenue.import_conversions(self.ledger, self.write(paid))["older"], 1)
        ledger = revenue.report(self.queue, ledger_path=self.ledger)["partner_ledger"]
        self.assertEqual(ledger["net_paid_commission_by_currency"], {})
        self.assertEqual(ledger["conversions_by_status"]["refunded"], 1)

    def test_conflicting_snapshot_rolls_back_whole_import(self):
        revenue.import_conversions(self.ledger, self.write(self.event()))
        collision = self.event(amount="15", updated_at="2026-11-18T07:00:00Z")
        batch = self.write(self.event(event_id="network:124"), collision)
        with self.assertRaisesRegex(ValueError, "строка 3.*updated_at"):
            revenue.import_conversions(self.ledger, batch)
        ledger = revenue.report(self.queue, ledger_path=self.ledger)["partner_ledger"]
        self.assertEqual(ledger["net_paid_commission_by_currency"], {"USD": "8.123456"})

    def test_rejects_invalid_money_or_missing_timezone(self):
        for bad in ("NaN", "Infinity", "-0.5", "1e7", "1,23", "0.0000001", "1234567890123"):
            with self.subTest(amount=bad), self.assertRaisesRegex(ValueError, "сумму"):
                revenue.import_conversions(self.ledger, self.write(self.event(amount=bad)))
        with self.assertRaisesRegex(ValueError, "код валюты"):
            revenue.import_conversions(self.ledger, self.write(self.event(currency="BTC??")))
        with self.assertRaisesRegex(ValueError, "часовым поясом"):
            revenue.import_conversions(self.ledger, self.write(self.event(updated_at="2026-11-18T12:34:56")))
        self.assertFalse(revenue.report(self.queue, ledger_path=self.ledger)["partner_ledger"]["available"])

    def test_reports_saved_production_and_provider_status_without_claiming_views(self):
        worker = Autopilot({}, base_dir=self.root)
        campaign = revenue.clip_campaign(self.campaign, self.clip)
        worker._record("file:own:1", {"id": "own"})
        worker._set("file:own:1", stage="archived", result=json.dumps({"clips": [
            {"sha256": self.clip, "title": "Смена машины",
             "variants": {"youtube": {"sponsored": True, "campaign": campaign}}},
            {"sha256": "b" * 64, "title": "Ночной заезд",
             "variants": {"youtube": {"sponsored": True}}},
        ]}))
        worker.close()
        queue = Queue(self.queue)
        with closing(queue.db) as db, db:
            for digest, account, platform, status, remote_id, playback, metadata in (
                ("a", "yt", "youtube", "uploaded", "provider-id", 0, {"campaign": campaign}),
                ("b", "yt", "youtube", "approved", None, 0, {}),
                ("c", "ig", "instagram", "published", "media-id", 1, {}),
            ):
                db.execute("""INSERT INTO jobs(sha256,account,platform,source_id,file,metadata,scheduled_at,
                              status,remote_id,playback_verified,session_uri) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                           (digest, account, platform, "own", "private-file", json.dumps(metadata), "2026-11-18",
                            status, remote_id, playback, "private-session"))
        revenue.import_conversions(self.ledger, self.write(self.event()))
        report = revenue.report(self.queue, state_path=worker.state_path, ledger_path=self.ledger)
        self.assertEqual(report["production"], {"available": True, "produced_clips": 2, "campaign_variants": 2})
        self.assertEqual(report["publication"]["jobs_total"], 3)
        channels = {row["platform"]: row for row in report["publication"]["accounts"]}
        self.assertEqual((channels["youtube"]["queued"], channels["youtube"]["uploaded"],
                          channels["youtube"]["published"]), (1, 1, 0))
        self.assertEqual(channels["instagram"]["playback_verified"], 1)
        result = report["campaigns"][0]["clips"][0]
        self.assertEqual(result["title"], "Смена машины")
        self.assertTrue(result["produced"])
        self.assertEqual(result["jobs"][0]["remote_id"], "provider-id")
        self.assertEqual(result["net_paid_commission_by_currency"], {"USD": "8.123456"})
        self.assertNotIn("views", json.dumps(report))
        self.assertNotIn("private-", json.dumps(report))

    def test_clip_link_requires_actual_matching_campaign_without_mutating_databases(self):
        worker = Autopilot({}, base_dir=self.root)
        saved_campaign = revenue.clip_campaign(self.campaign, self.clip)
        worker._record("file:own:1", {"id": "own"})
        worker._set("file:own:1", stage="rendered", result=json.dumps({"clips": [
            {"sha256": self.clip, "variants": {"youtube": {"sponsored": True, "campaign": saved_campaign}}},
        ]}))
        worker.close()
        queue = Queue(self.queue)
        with closing(queue.db) as db, db:
            db.execute("""INSERT INTO jobs(sha256,account,platform,source_id,file,metadata,scheduled_at)
                          VALUES ('ad-hash','yt','youtube','own','ad.mp4',?,'2026-11-18')""",
                       (json.dumps({"campaign": saved_campaign}),))
        before = {path: path.read_bytes() for path in (worker.state_path, self.queue)}
        for paths in ({"queue_path": None, "state_path": worker.state_path},
                      {"queue_path": self.queue, "state_path": None}):
            with self.subTest(paths=paths):
                same = {**self.campaign, "text": "Обновлённая плашка"}
                revenue.validate_clip_campaign(same, self.clip, **paths)
                other = {**self.campaign, "url": "https://example.test/other-offer"}
                with self.assertRaisesRegex(ValueError, "с другим оффером"):
                    revenue.validate_clip_campaign(other, self.clip, **paths)
                with self.assertRaisesRegex(ValueError, "нет сохранённой кампании"):
                    revenue.validate_clip_campaign(same, "b" * 64, **paths)
        self.assertEqual({path: path.read_bytes() for path in before}, before)

    def test_reads_legacy_queue_without_modifying_schema(self):
        with closing(sqlite3.connect(self.queue)) as db, db:
            db.execute("CREATE TABLE jobs(id INTEGER, account TEXT, platform TEXT, status TEXT, remote_id TEXT, metadata TEXT)")
            db.execute("INSERT INTO jobs VALUES (1, 'yt', 'youtube', 'uploaded', 'old-id', '{}')")
        before = self.queue.read_bytes()
        report = revenue.report(self.queue, state_path=self.root / "missing.sqlite3", ledger_path=self.ledger)
        self.assertEqual(report["publication"]["accounts"][0]["uploaded"], 1)
        self.assertIsNone(report["production"]["produced_clips"])
        self.assertFalse(report["partner_ledger"]["available"])
        self.assertEqual(self.queue.read_bytes(), before)
        self.assertFalse(self.ledger.exists())


if __name__ == "__main__":
    unittest.main()
