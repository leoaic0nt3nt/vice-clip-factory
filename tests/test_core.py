import datetime as dt
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from viceclip.rights import check_rights
from viceclip.scoring import select_candidates, kept_ranges, remap_captions, iou
from viceclip.queue import Queue, canonical_time
from viceclip.integrations import discover
from viceclip.publishers import token_for, instagram_upload, youtube_upload


def fixture_config():
    return {"sources": [{"id": "own", "permission": {"status": "owned", "reference": "test", "allow_publish": True, "allow_edit": True}, "platforms": ["youtube"]}],
            "accounts": [{"id": "a", "platform": "youtube", "token_env": "TEST_TOKEN", "max_posts_per_day": 2, "commercial": False}]}


class RightsTests(unittest.TestCase):
    def test_discovery_is_not_permission(self):
        config = fixture_config()
        config["sources"][0]["permission"]["status"] = "unverified"
        with self.assertRaises(ValueError):
            check_rights(config, "own", "publish", "youtube")

    def test_creator_permission_is_not_commercial_game_license(self):
        with self.assertRaises(ValueError):
            check_rights(fixture_config(), "own", "publish", "youtube", commercial=True)

    def test_expired_permission(self):
        config = fixture_config()
        config["sources"][0]["permission"]["expires_at"] = "2020-01-01T00:00:00Z"
        with self.assertRaises(ValueError):
            check_rights(config, "own", "publish", "youtube")

    def test_wrong_destination(self):
        with self.assertRaises(ValueError):
            check_rights(fixture_config(), "own", "publish", "instagram")

    def test_permission_flags_must_be_boolean_true(self):
        config=fixture_config()
        config["sources"][0]["permission"]["allow_publish"]="false"
        with self.assertRaises(ValueError):
            check_rights(config,"own","publish","youtube")
        config["sources"][0]["permission"].update(allow_publish=True,publisher_commercial="false",allow_sponsorship="true")
        with self.assertRaises(ValueError):
            check_rights(config,"own","publish","youtube",sponsored=True)


class EditorialTests(unittest.TestCase):
    def test_action_is_ranked_and_candidates_do_not_duplicate(self):
        analysis = {"duration": 100, "audio_dbfs": [-50]*100, "motion": [1]*100}
        for second in range(60, 72):
            analysis["audio_dbfs"][second] = -8
            analysis["motion"][second] = 35
        transcript = [{"start": 60, "end": 64, "text": "Смотри, сейчас погоня и прыжок"}, {"start": 65, "end": 69, "text": "Вот это получилось! Взрыв!"}]
        candidates = select_candidates(analysis, transcript, top=3, max_duration=18)
        self.assertTrue(candidates[0]["evidence"]["event_keywords"])
        self.assertTrue(candidates[0]["evidence"]["setup"])
        self.assertTrue(candidates[0]["evidence"]["payoff"])
        for a in candidates:
            for b in candidates:
                if a is not b:
                    self.assertLessEqual(iou((a["start"],a["end"]),(b["start"],b["end"])), .35)

    def test_silent_action_is_preserved(self):
        analysis = {"audio_dbfs": [-60]*20, "motion": [30]*20}
        self.assertEqual(kept_ranges(0,20,analysis,[],True), [(0,20)])

    def test_speech_prevents_cut_and_static_silence_can_be_cut(self):
        analysis = {"audio_dbfs": [-60]*20, "motion": [0]*20}
        speech = [{"start": 0, "end": 20, "text": "quiet speech"}]
        self.assertEqual(kept_ranges(0,20,analysis,speech,True), [(0,20)])
        ranges = kept_ranges(0,20,analysis,[],True)
        self.assertLess(sum(b-a for a,b in ranges), 20)
        self.assertEqual(ranges[0][0], 0)
        self.assertEqual(ranges[-1][1], 20)

    def test_caption_timing_after_multiple_cuts(self):
        result = remap_captions([{"start": 1, "end": 3, "text": "one"}, {"start": 7, "end": 9, "text": "two"}], [(0,4),(6,10)])
        self.assertEqual(result, [{"start":1,"end":3,"text":"one"},{"start":5,"end":7,"text":"two"}])

    def test_word_captions_exclude_speech_outside_clip_and_clamp_crossing_words(self):
        transcript = [{"start": 0, "end": 10, "text": "before crossing kept after", "words": [
            {"start": 0, "end": 2, "word": " before"},
            {"start": 2, "end": 3, "word": " crossing"},
            {"start": 3, "end": 4, "word": " kept"},
            {"start": 4, "end": 5, "word": " after"},
        ]}]
        result = remap_captions(transcript, [(2.5, 4)])
        self.assertEqual(result, [{"start": 0, "end": 1.5, "text": "crossing kept", "words": [
            {"start": 0, "end": .5, "word": " crossing"},
            {"start": .5, "end": 1.5, "word": " kept"},
        ]}])
        # Words ending at the start or starting at the end do not overlap.
        self.assertEqual(remap_captions(transcript, [(5, 6)]), [])

    def test_word_captions_remap_both_sides_of_removed_gap(self):
        transcript = [{"start": 0, "end": 9, "text": "one removed two", "words": [
            {"start": 1, "end": 2, "word": "one"},
            {"start": 4, "end": 5, "word": "removed"},
            {"start": 7, "end": 8, "word": "two"},
        ]}]
        result = remap_captions(transcript, [(0, 3), (6, 9)])
        self.assertEqual([(c["start"], c["end"], c["text"]) for c in result],
                         [(1, 2, "one"), (4, 5, "two")])
        self.assertEqual(result[1]["words"], [{"start": 4, "end": 5, "word": "two"}])

    def test_word_captions_split_long_speech_and_meaningful_pauses(self):
        words = [{"start": i*.3, "end": i*.3+.2, "word": " слово"} for i in range(7)]
        words += [{"start": 3, "end": 3.2, "word": " дальше"}]
        result = remap_captions([{"start": 0, "end": 4, "text": "ignored", "words": words}], [(0, 4)])
        self.assertEqual([len(c["words"]) for c in result], [6, 1, 1])
        self.assertTrue(all(len(c["text"]) <= 48 for c in result))
        self.assertEqual(result[-1]["start"], 3)
        long_words = [{"start": i, "end": i+.5, "word": " четырнадцать"} for i in range(4)]
        result = remap_captions([{"start": 0, "end": 4, "text": "ignored", "words": long_words}], [(0, 4)])
        self.assertEqual([len(c["words"]) for c in result], [3, 1])

    def test_hook_keeps_whole_words_within_seventy_characters(self):
        text = "Смотри сейчас машина провалилась с моста и уехала прямо в метро через огромную дыру"
        clip = select_candidates({"duration": 40, "audio_dbfs": [], "motion": []},
                                 [{"start": 0, "end": 10, "text": text}], top=1)[0]
        self.assertLessEqual(len(clip["hook"]), 70)
        self.assertTrue(clip["hook"].endswith("…"))
        self.assertTrue(text.startswith(clip["hook"][:-1] + " "))
        fallback = select_candidates({"duration": 40, "audio_dbfs": [], "motion": []}, [], top=1)[0]
        self.assertEqual(fallback["hook"], "Игровой момент")

    def test_short_clip_boundaries(self):
        clips = select_candidates({"duration": 4, "audio_dbfs": [], "motion": []}, [], top=1)
        self.assertEqual((clips[0]["start"], clips[0]["end"]), (0,4))


class QueueTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)/"clip.mp4"
        self.path.write_bytes(b"test fixture bytes")
        self.queue = Queue(Path(self.tmp.name)/"queue.db")
        self.addCleanup(self.queue.db.close)
        self.config = fixture_config()

    def add(self, **kwargs):
        return self.queue.add(self.path,"own","a",{"title":"test"},self.config,**kwargs)

    def test_dedup_per_account_and_dry_run_has_no_side_effect(self):
        a,b = self.add(), self.add()
        self.assertEqual(a["id"],b["id"])
        self.queue.approve(a["id"])
        with patch("viceclip.publishers.upload", side_effect=AssertionError("must not upload")):
            self.assertEqual(self.queue.run(self.config)[0]["status"], "dry_run")
        self.assertEqual(self.queue.list()[0]["status"], "approved")

    def test_ambiguous_failure_is_never_retried(self):
        job = self.add(); self.queue.approve(job["id"])
        calls = []
        def ambiguous(*args):
            calls.append(1)
            raise TimeoutError("ambiguous test")
        result = self.queue.run(self.config,execute=True,uploader=ambiguous)
        self.assertEqual(result[0]["status"], "unknown")
        self.assertEqual(self.queue.run(self.config,execute=True,uploader=ambiguous), [])
        self.assertEqual(len(calls),1)
        with self.assertRaises(ValueError):
            self.queue.approve(job["id"])
        self.queue.reconcile(job["id"],"verified_remote_123")
        self.assertEqual(self.queue.list()[0]["status"],"uploaded")

    def test_mutation_after_approval_blocks_upload(self):
        job = self.add(); self.queue.approve(job["id"])
        self.path.write_bytes(b"changed")
        uploader = unittest.mock.Mock(return_value="bad")
        self.assertEqual(self.queue.run(self.config,execute=True,uploader=uploader)[0]["status"], "failed")
        uploader.assert_not_called()

    def test_permission_is_rechecked_at_execution(self):
        job = self.add(); self.queue.approve(job["id"])
        self.config["sources"][0]["permission"]["allow_publish"] = False
        uploader = unittest.mock.Mock(return_value="bad")
        self.assertEqual(self.queue.run(self.config,execute=True,uploader=uploader)[0]["status"], "failed")
        uploader.assert_not_called()

    def test_future_job_waits(self):
        job = self.add(schedule=(dt.datetime.now(dt.timezone.utc)+dt.timedelta(days=1)).isoformat())
        self.queue.approve(job["id"])
        self.assertEqual(self.queue.run(self.config), [])

    def test_schedule_requires_timezone(self):
        with self.assertRaises(ValueError):
            canonical_time("2026-11-20T10:00:00")

    def test_account_daily_cap_is_enforced(self):
        self.config["accounts"][0]["max_posts_per_day"] = 1
        first = self.add(); self.queue.approve(first["id"])
        self.path = Path(self.tmp.name)/"second.mp4"
        self.path.write_bytes(b"second bytes")
        second = self.add(); self.queue.approve(second["id"])
        outcomes = self.queue.run(self.config,execute=True,uploader=lambda *args:"remote")
        self.assertEqual([r["status"] for r in outcomes],["uploaded","account_limit"])

    def test_sponsored_sidecar_cannot_be_hidden_by_omitting_cli_flag(self):
        from viceclip.media import fingerprint
        self.path.with_suffix(".json").write_text(json.dumps({"source_id":"own","output_sha256":fingerprint(self.path),"sponsored":True}))
        with self.assertRaises(ValueError):
            self.add()

    def test_sponsored_instagram_is_explicitly_blocked(self):
        config=fixture_config()
        config["sources"][0]["platforms"].append("instagram")
        config["sources"][0]["permission"].update(publisher_commercial=True,allow_sponsorship=True)
        with self.assertRaises(ValueError):
            check_rights(config,"own","publish","instagram",sponsored=True)

    def test_definite_preflight_error_can_be_corrected_and_reapproved(self):
        job=self.add(); self.queue.approve(job["id"])
        def rejected(*a):
            raise ValueError("wrong channel before remote write")
        self.assertEqual(self.queue.run(self.config,execute=True,uploader=rejected)[0]["status"],"failed")
        self.queue.approve(job["id"])
        self.assertEqual(self.queue.run(self.config,execute=True,uploader=lambda *a:"remote")[0]["status"],"uploaded")

    def test_resumable_session_is_private_and_not_in_list(self):
        job=self.add(); self.queue.approve(job["id"])
        def uncertain(job,account,progress):
            progress("upload_started","https://upload.example.test/private-session")
            raise TimeoutError("response lost")
        self.queue.run(self.config,execute=True,uploader=uncertain)
        raw=self.queue.db.execute("SELECT session_uri FROM jobs WHERE id=?",(job["id"],)).fetchone()[0]
        self.assertTrue(raw.endswith("private-session"))
        self.assertNotIn("session_uri",self.queue.list()[0])
        self.assertNotIn("session_uri",self.add())


class IntegrationTests(unittest.TestCase):
    def test_disabled_discovery_never_calls_network(self):
        with patch("viceclip.integrations._json_get", side_effect=AssertionError("no request")):
            self.assertEqual(discover({"creators": [{"enabled":False}]} )["sources"], [])

    def test_discovered_single_upload_permission_is_not_inherited(self):
        creator = {"id":"c","enabled":True,"platform":"youtube","remote_id":"channel", "permission":{"status":"licensed","scope":"single_upload"}}
        api = {"items":[{"id":{"videoId":"123"},"snippet":{"title":"new"}}]}
        with patch.dict(os.environ, {"YOUTUBE_API_KEY":"test"}), patch("viceclip.integrations._json_get",return_value=api):
            found = discover({"creators":[creator]})["sources"][0]
        self.assertFalse(found["ready_for_ingest"])

    def test_plaintext_shared_oauth_file_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)/"oauth.json"; p.write_text('{}'); p.chmod(0o644)
            with self.assertRaises(ValueError):
                token_for({"platform":"youtube","oauth_token_file":str(p)})

    def test_youtube_token_cannot_publish_to_wrong_channel(self):
        with patch("viceclip.publishers.request_json",return_value={"items":[{"id":"other-channel"}]}), patch("urllib.request.urlopen",side_effect=AssertionError("no upload")):
            with self.assertRaises(ValueError):
                youtube_upload({}, {"remote_id":"expected-channel"}, "secret", lambda *a:None)

    def test_instagram_public_url_must_match_approved_bytes(self):
        import io
        from viceclip.publishers import verify_public_media
        response=io.BytesIO(b"wrong bytes")
        response.geturl=lambda:"https://example.test/video.mp4"
        with patch("urllib.request.urlopen",return_value=response):
            with self.assertRaises(ValueError):
                verify_public_media({"sha256":"wronghash","metadata":{"public_url":"https://example.test/video.mp4"}})

    def test_instagram_checks_live_limit_before_container(self):
        account={"remote_id":"123"}
        with patch("viceclip.publishers.request_json",return_value={"data":[{"quota_usage":100,"config":{"quota_total":100}}]}) as api:
            with self.assertRaises(ValueError):
                instagram_upload({"metadata":{"public_url":"https://example.com/clip.mp4"}}, account,"secret",lambda *a:None)
            self.assertEqual(api.call_count,1)


if __name__ == "__main__":
    unittest.main()
