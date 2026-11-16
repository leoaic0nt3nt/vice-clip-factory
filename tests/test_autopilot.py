import datetime as dt
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from viceclip.autopilot import Autopilot, SourceNotReady
from viceclip.media import fingerprint
from viceclip.queue import Queue


class AutopilotTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.input = self.root / "source.mp4"
        self.input.write_bytes(b"original input bytes")
        self.now = time.time() - 5
        self.calls = []
        self.config = {
            "sources": [{"id": "own", "path": str(self.input), "enabled": True,
                "platforms": ["youtube", "instagram"], "permission": {
                    "status": "owned", "reference": "fixture", "allow_edit": True,
                    "allow_download": True, "allow_publish": True}}],
            "accounts": [{"id": "yt", "platform": "youtube", "max_posts_per_day": 3}],
            "autopilot": {"enabled": True, "execute": False, "min_score": 50,
                "max_sources_per_tick": 10, "min_free_bytes": 0,
                "retry_backoff_seconds": 10, "posting_gap_seconds": 3600}}

    def processor(self, source, input_path, work, config):
        self.calls.append(source["id"])
        path = work / "clips" / "one.mp4"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(("rendered " + source["id"]).encode())
        return {"input_sha256": fingerprint(input_path), "clips": [{"file": str(path),
            "sha256": fingerprint(path), "score": 90, "title": "A real hook",
            "public_url": "https://example.test/immutable.mp4"}]}

    def worker(self, **kwargs):
        worker = Autopilot(self.config, base_dir=self.root, clock=lambda: self.now,
                           processor=self.processor, **kwargs)
        self.addCleanup(worker.close)
        return worker

    def test_repeat_ticks_do_not_rerender_or_duplicate_publish_jobs(self):
        worker = self.worker()
        first = worker.tick(execute=False)
        second = worker.tick(execute=False)
        self.assertEqual(len(first["processed"]), 1)
        self.assertEqual(second["processed"], [])
        self.assertEqual(self.calls, ["own"])
        queue = Queue(self.root / "data/publish.sqlite3")
        self.addCleanup(queue.db.close)
        self.assertEqual(len(queue.list()), 1)
        self.assertEqual(queue.list()[0]["status"], "approved")

    def test_connecting_accounts_after_preview_backfills_cached_renders(self):
        account = self.config["accounts"].pop()
        worker = self.worker()
        self.assertEqual(worker.tick()["processed"][0]["jobs"], 0)
        worker.config["accounts"].append(account)
        self.assertEqual(len(worker.tick()["jobs"]), 1)
        worker.config["accounts"].append({**account, "id":"second-channel"})
        report = worker.tick()
        queue = Queue(self.root / "data/publish.sqlite3")
        self.addCleanup(queue.db.close)
        self.assertEqual({job["account"] for job in queue.list()}, {"yt", "second-channel"})
        self.assertEqual(len(queue.list()), 2)
        self.assertEqual(self.calls, ["own"])

    def test_source_specific_channel_routing(self):
        self.config["accounts"].append({"id":"second-channel","platform":"youtube"})
        self.config["sources"][0]["account_ids"]=["second-channel"]
        report=self.worker().tick()
        self.assertEqual([job["account"] for job in report["jobs"]],["second-channel"])

    def test_setup_style_inbox_works_with_empty_explicit_source_ids(self):
        inbox = self.root / "inbox"
        inbox.mkdir()
        (inbox / "one.mp4").write_bytes(b"new arrival")
        self.config["sources"][0].pop("path")
        self.config["autopilot"].update(source_ids=[], inboxes=[{"path":str(inbox), "source_id":"own", "stable_seconds":0}])
        os.utime(inbox / "one.mp4", (self.now - 10, self.now - 10))
        report = self.worker().tick()
        self.assertEqual(len(report["processed"]), 1)

    def test_one_broken_source_does_not_stop_another(self):
        self.config["sources"].append({**self.config["sources"][0], "id": "broken"})
        def download(source, work, config):
            if source["id"] == "broken":
                raise RuntimeError("source temporarily unavailable")
            return self.input
        report = self.worker(downloader=download).tick()
        self.assertEqual([p["source"] for p in report["processed"]], ["own"])
        self.assertEqual(report["errors"][0]["source"], "broken")
        self.assertTrue(report["publish"])

    def test_restart_reuses_completed_render_after_enqueue_failure(self):
        class FailsEnqueue(Queue):
            def add(self, *a, **k):
                raise ValueError("account temporarily misconfigured")
        first = self.worker(queue_factory=FailsEnqueue)
        self.assertEqual(len(first.tick()["errors"]), 1)
        self.now += 11
        restarted = self.worker()
        self.assertEqual(len(restarted.tick()["processed"]), 1)
        self.assertEqual(self.calls, ["own"])

    def test_autoqueues_each_destination_with_spacing(self):
        self.config["accounts"].append({"id": "ig", "platform": "instagram", "max_posts_per_day": 3})
        report = self.worker().tick()
        self.assertEqual({j["platform"] for j in report["jobs"]}, {"youtube", "instagram"})
        queue = Queue(self.root / "data/publish.sqlite3")
        self.addCleanup(queue.db.close)
        self.assertEqual([j["status"] for j in queue.list()], ["approved", "approved"])

    def test_ambiguous_remote_upload_is_never_automatically_reapproved(self):
        uploads = []
        class AmbiguousQueue(Queue):
            def run(self, config, *, execute=False):
                def upload(*args):
                    uploads.append(1)
                    raise TimeoutError("response lost after remote write")
                return super().run(config, execute=execute, uploader=upload)
        worker = self.worker(queue_factory=AmbiguousQueue)
        self.assertEqual(worker.tick(execute=True)["publish"][0]["status"], "unknown")
        self.now += 60
        worker.tick(execute=True)
        self.assertEqual(len(uploads), 1)

    def test_public_youtube_channel_polling_needs_no_api_key(self):
        creator = {"id": "selected", "platform": "youtube", "url": "https://www.youtube.com/@creator/videos"}
        response = type("Response", (), {"stdout": json.dumps({"entries": [
            {"id": "video000001", "title": "first", "live_status": "not_live"},
            {"id": "live0000001", "live_status": "is_live"}]})})()
        with patch.dict(os.environ, {}, clear=True), patch("viceclip.autopilot.integrations.executable", return_value="yt-dlp"), patch.object(Autopilot, "_run", return_value=response) as run:
            sources = self.worker()._discover_creator(creator)["sources"]
        self.assertEqual([s["id"] for s in sources], ["youtube-video000001"])
        self.assertIn("--flat-playlist", run.call_args.args[0])

    def test_nested_youtube_channel_tabs_are_not_mistaken_for_video_ids(self):
        creator={"id":"selected","platform":"youtube","url":"https://www.youtube.com/@creator"}
        root={"entries":[{"_type":"url","id":"UC_CHANNEL_IDENTIFIER","url":"https://www.youtube.com/@creator/streams"}]}
        tab={"entries":[{"_type":"url","id":"video000001","title":"A stream replay"}]}
        responses=[type("Response",(),{"stdout":json.dumps(data)})() for data in (root,tab)]
        with patch.dict(os.environ,{},clear=True),patch("viceclip.autopilot.integrations.executable",return_value="yt-dlp"),patch.object(Autopilot,"_run",side_effect=responses):
            sources=self.worker()._discover_creator(creator)["sources"]
        self.assertEqual([s["url"] for s in sources],["https://www.youtube.com/watch?v=video000001"])

    def test_query_scout_autonomous_ingest_requires_operator_setting(self):
        self.config["source_policy"]={"mode":"configured_sources","user_responsible":True}
        self.config["autopilot"]["scout_queries"]=["GTA VI gameplay"]
        result={"source_candidates":[{"platform":"youtube","author_id":"creator","evidence":[{"url":"https://www.youtube.com/watch?v=video000001","title":"New gameplay"}]}]}
        worker=self.worker(scouter=lambda **kw:result,downloader=lambda *args:self.input)
        report=worker.tick()
        self.assertEqual(len(report["processed"]),1)
        self.assertFalse(report["discovery"][0]["auto_ingest"])
        worker.settings["ingest_scout_candidates"]=True
        self.now+=3601
        report=worker.tick()
        self.assertEqual([item["source"] for item in report["processed"]],["scout-youtube-video000001"])

    def test_generated_remote_input_is_cleaned_but_originals_are_preserved(self):
        self.config["sources"][0].pop("path")
        self.config["sources"][0]["url"]="https://example.test/source.mp4"
        downloaded=[]
        def download(source,work,config):
            target=work/"input/source.mp4"
            target.parent.mkdir(parents=True)
            target.write_bytes(b"downloaded input")
            downloaded.append(target)
            return target
        worker=self.worker(downloader=download)
        worker.tick()
        self.assertFalse(downloaded[0].exists())
        self.assertTrue(self.input.exists())
        work=downloaded[0].parents[1]
        local=work/"user-selected.mp4";local.write_bytes(b"user original")
        self.assertFalse(worker._cleanup_download({"path":str(local)},local,work))
        self.assertTrue(local.exists())

    def test_queue_capacity_still_runs_publisher_without_new_processing(self):
        worker=self.worker()
        worker.tick()
        self.config["sources"].append({**self.config["sources"][0],"id":"second"})
        self.config["autopilot"]["max_pending_jobs"]=1
        report=worker.tick()
        self.assertEqual(report["backpressure"],"queue_capacity")
        self.assertTrue(report["publish"])
        self.assertEqual(self.calls,["own"])

    def test_retention_only_archives_completed_publications(self):
        worker=self.worker();worker.tick()
        row=worker.db.execute("SELECT result FROM media_jobs").fetchone()
        clip=Path(json.loads(row[0])["clips"][0]["file"])
        queue=Queue(self.root/"data/publish.sqlite3")
        self.addCleanup(queue.db.close)
        self.now+=8*86400
        worker.tick()
        self.assertTrue(clip.exists()) # Pending approval/upload is never discarded.
        queue.db.execute("UPDATE jobs SET status='uploaded'");queue.db.commit()
        report=worker.tick()
        self.assertEqual(report["archived_sources"],1)
        self.assertFalse(clip.exists())
        self.assertTrue(self.input.exists())

    def test_timeout_stops_the_downloaders_child_processes(self):
        sentinel=self.root/"orphan-created-file"
        child_code="import time;from pathlib import Path;time.sleep(0.7);Path("+repr(str(sentinel))+").write_text('orphan')"
        parent_code="import subprocess,sys,time;subprocess.Popen([sys.executable,'-c',"+repr(child_code)+"]);time.sleep(10)"
        with self.assertRaises(subprocess.TimeoutExpired):
            self.worker()._run([sys.executable,"-c",parent_code],timeout=.15)
        time.sleep(.9)
        self.assertFalse(sentinel.exists())

    def test_discovery_failure_keeps_polling_other_creators(self):
        self.config["creators"] = [{"id": "bad", "platform": "youtube"}, {"id": "good", "platform": "youtube"}]
        seen = []
        def discover(config, lookback):
            creator = config["creators"][0]["id"]
            seen.append(creator)
            if creator == "bad":
                raise RuntimeError("provider outage")
            return {"sources": [], "skipped": []}
        report = self.worker(discoverer=discover).tick()
        self.assertEqual(seen, ["bad", "good"])
        self.assertEqual(len(report["processed"]), 1)

    def test_waiting_live_source_does_not_exhaust_retry_budget(self):
        def wait(*args):
            raise SourceNotReady("stream is still live")
        worker = self.worker(downloader=wait)
        report = worker.tick()
        self.assertEqual(report["discovery"][0]["state"], "waiting")
        job = worker.db.execute("SELECT stage,failures FROM media_jobs").fetchone()
        self.assertEqual((job["stage"], job["failures"]), ("waiting", 0))

    def test_definite_publish_failure_gets_bounded_backoff_only(self):
        attempts = []
        class RejectQueue(Queue):
            def run(self, config, *, execute=False):
                def reject(*args):
                    attempts.append(1)
                    raise ValueError("token needs refresh")
                return super().run(config, execute=execute, uploader=reject)
        self.config["autopilot"]["publish_max_retries"] = 1
        worker = self.worker(queue_factory=RejectQueue)
        worker.tick(execute=True)  # First provider refusal.
        worker.tick(execute=True)  # Registers retry due time, no immediate retry.
        self.assertEqual(len(attempts), 1)
        self.now += 11
        worker.tick(execute=True)
        self.assertEqual(len(attempts), 2)
        self.now += 100
        worker.tick(execute=True)
        self.assertEqual(len(attempts), 2)

    def test_changed_completed_render_cannot_be_published_after_restart(self):
        class FailsEnqueue(Queue):
            def add(self, *a, **k):
                raise ValueError("temporary setup issue")
        worker = self.worker(queue_factory=FailsEnqueue)
        worker.tick()
        result_file = next((self.root / "output/autopilot").glob("*/result.json"))
        output = json.loads(result_file.read_text())["clips"][0]["file"]
        Path(output).write_bytes(b"changed after review")
        self.now += 11
        report = self.worker().tick()
        self.assertIn("bytes do not match", report["errors"][0]["error"])
        self.assertEqual(report["jobs"], [])


if __name__ == "__main__":
    unittest.main()
