import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import types
import unittest
from unittest.mock import MagicMock, patch

from viceclip import auth, publishers, storage
from viceclip.queue import Queue


class S3Error(Exception):
    def __init__(self, code):
        self.response = {"Error": {"Code": code}}


class FakeS3:
    def __init__(self):
        self.objects, self.puts, self.signs = {}, [], []

    def head_object(self, Bucket, Key):
        if Key not in self.objects:
            raise S3Error("404")
        return self.objects[Key]

    def put_object(self, **kw):
        data = kw["Body"].read()
        self.puts.append({k: v for k, v in kw.items() if k != "Body"})
        self.objects[kw["Key"]] = {"Metadata": kw["Metadata"], "ContentLength": len(data)}

    def generate_presigned_url(self, operation, **kw):
        self.signs.append((operation, kw))
        return "https://media.example.test/" + kw["Params"]["Key"] + "?private-signature=secret"


def config():
    return {"sources": [{"id": "own", "permission": {"status": "owned", "reference": "fixture",
        "allow_publish": True, "allow_edit": True}, "platforms": ["youtube", "instagram"]}],
        "accounts": [{"id": "youtube-a", "platform": "youtube", "remote_id": "yt-a"},
                     {"id": "instagram-a", "platform": "instagram", "remote_id": "ig-a"}],
        "storage": {"provider": "s3", "bucket": "media", "prefix": "factory", "presign_seconds": 86400}}


class StorageTests(unittest.TestCase):
    def test_source_hash_upload_is_immutable_and_reused(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "clip.mp4"
            path.write_bytes(b"approved MP4 fixture")
            s3 = FakeS3()
            first = storage.deliver(path, config()["storage"], client=s3)
            second = storage.deliver(path, config()["storage"], client=s3)
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            self.assertTrue(first["object_key"].endswith(digest + ".mp4"))
            self.assertEqual(len(s3.puts), 1)
            self.assertEqual(s3.puts[0]["IfNoneMatch"], "*")
            self.assertEqual(s3.puts[0]["Metadata"], {"sha256": digest})
            self.assertNotIn("ACL", s3.puts[0])
            self.assertTrue(second["reused"])
            self.assertEqual(s3.signs[-1][1]["ExpiresIn"], 86400)

    def test_existing_object_with_different_metadata_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "clip.mp4"
            path.write_bytes(b"approved fixture")
            s3 = FakeS3()
            s3.head_object = lambda **kw: {"Metadata": {"sha256": "other"}, "ContentLength": 10}
            with self.assertRaises(ValueError):
                storage.deliver(path, config()["storage"], client=s3)
            self.assertEqual(s3.puts, [])

    def test_changed_file_is_rejected_before_any_cloud_call(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "clip.mp4"
            path.write_bytes(b"changed")
            s3 = MagicMock()
            with self.assertRaises(ValueError):
                storage.deliver(path, config()["storage"], sha256="old-hash", client=s3)
            s3.head_object.assert_not_called()


class ConnectionTests(unittest.TestCase):
    def test_twitch_app_token_is_cached_and_replaced_before_expiry(self):
        creator = {"client_id_env": "TWITCH_ID_TEST", "client_secret_env": "TWITCH_SECRET_TEST"}
        auth._TWITCH_APP_TOKENS.clear()
        with patch.dict(os.environ, {"TWITCH_ID_TEST": "client", "TWITCH_SECRET_TEST": "private"}, clear=True), patch("viceclip.auth.time.time", side_effect=[1000, 1010, 1800]), patch("viceclip.auth._http_json", side_effect=[{"access_token": "first", "expires_in": 800}, {"access_token": "second", "expires_in": 800}]) as request:
            self.assertEqual(auth.twitch_token(creator), "first")
            self.assertEqual(auth.twitch_token(creator), "first")
            self.assertEqual(auth.twitch_token(creator), "second")
        self.assertEqual(request.call_count, 2)
        self.assertEqual(request.call_args.kwargs["fields"]["grant_type"], "client_credentials")

    def test_twitch_existing_token_config_remains_usable_without_secret(self):
        with patch.dict(os.environ, {"TWITCH_CLIENT_ID": "client", "TWITCH_ACCESS_TOKEN": "legacy"}, clear=True), patch("viceclip.auth._http_json") as request:
            self.assertEqual(auth.twitch_token({}), "legacy")
        request.assert_not_called()

    def test_twitch_token_is_validated_hourly_and_replaced_if_revoked(self):
        auth._TWITCH_APP_TOKENS.clear()
        with patch.dict(os.environ, {"TWITCH_CLIENT_ID": "client", "TWITCH_CLIENT_SECRET": "private"}, clear=True), patch("viceclip.auth.time.time", side_effect=[1000, 5000]), patch("viceclip.auth._http_json", side_effect=[{"access_token": "first", "expires_in": 10000}, ValueError("revoked"), {"access_token": "replacement", "expires_in": 10000}]) as request:
            self.assertEqual(auth.twitch_token({}), "first")
            self.assertEqual(auth.twitch_token({}), "replacement")
        self.assertEqual(request.call_args_list[1].kwargs["authorization_scheme"], "OAuth")
        self.assertEqual(request.call_count, 3)

    def test_instagram_connect_keeps_professional_id_and_renewable_private_file(self):
        with tempfile.TemporaryDirectory() as d:
            file = Path(d) / "instagram.json"
            identity = {"data": [{"id": "app-scoped", "user_id": "ig-a", "username": "creator"}]}
            with patch("viceclip.auth._http_json", return_value=identity):
                account = auth.connect_instagram("private-long-lived-token", file)
            self.assertEqual(account["remote_id"], "ig-a")
            self.assertNotIn("access_token", account)
            state = json.loads(file.read_text())
            self.assertEqual(state["token_type"], "instagram_long_lived")
            self.assertEqual(file.stat().st_mode & 0o777, 0o600)

    def test_instagram_refreshes_valid_older_token_before_expiry(self):
        with tempfile.TemporaryDirectory() as d:
            file = Path(d) / "instagram.json"
            now = time.time()
            auth.write_private_json(file, {"access_token": "old", "remote_id": "ig-a",
                "expires_at": now + 2 * 86400, "issued_at": now - 40 * 86400,
                "token_type": "instagram_long_lived"})
            with patch.dict(os.environ, {}, clear=True), patch("viceclip.auth._http_json", return_value={"access_token": "new", "expires_in": 5184000}) as request:
                got = auth.token_for({"platform": "instagram", "remote_id": "ig-a", "oauth_token_file": str(file)})
            self.assertEqual(got, "new")
            self.assertIn("ig_refresh_token", request.call_args.args[0])
            self.assertGreater(json.loads(file.read_text())["expires_at"], now + 59 * 86400)
            self.assertEqual(file.stat().st_mode & 0o777, 0o600)

    def test_expired_instagram_token_cannot_be_refreshed(self):
        with tempfile.TemporaryDirectory() as d:
            file = Path(d) / "instagram.json"
            auth.write_private_json(file, {"access_token": "expired", "expires_at": 1,
                "issued_at": 0, "token_type": "instagram_long_lived"})
            with patch("viceclip.auth._http_json") as request:
                with self.assertRaises(ValueError):
                    auth.token_for({"platform": "instagram", "oauth_token_file": str(file)})
            request.assert_not_called()

    def test_youtube_connection_uses_pkce_and_browser_consent(self):
        with tempfile.TemporaryDirectory() as d:
            flow = MagicMock()
            flow.run_local_server.return_value = types.SimpleNamespace(
                refresh_token="private-refresh", token="private-access", client_id="client", client_secret="secret",
                expiry=dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=1))
            factory = MagicMock()
            factory.from_client_secrets_file.return_value = flow
            module = types.ModuleType("google_auth_oauthlib.flow")
            module.InstalledAppFlow = factory
            with patch.dict(sys.modules, {"google_auth_oauthlib": types.ModuleType("google_auth_oauthlib"), "google_auth_oauthlib.flow": module}), patch("viceclip.auth._http_json", return_value={"items": [{"id": "yt-a", "snippet": {"title": "Channel"}}]}):
                account = auth.connect_youtube("client.json", Path(d) / "youtube.json")
            self.assertTrue(factory.from_client_secrets_file.call_args.kwargs["autogenerate_code_verifier"])
            self.assertEqual(flow.run_local_server.call_args.kwargs["host"], "127.0.0.1")
            self.assertEqual(account["remote_id"], "yt-a")
            self.assertNotIn("refresh_token", account)


class QueueConnectionTests(unittest.TestCase):
    def test_instagram_can_be_enqueued_without_manual_public_url(self):
        with tempfile.TemporaryDirectory() as d:
            file = Path(d) / "clip.mp4"
            file.write_bytes(b"clip")
            queue = Queue(Path(d) / "queue.sqlite3")
            self.addCleanup(queue.db.close)
            job = queue.add(file, "own", "instagram-a", {"title": "clip"}, config())
            self.assertEqual(job["platform"], "instagram")
            bad = config(); bad.pop("storage")
            with self.assertRaises(ValueError):
                queue.add(file, "own", "instagram-a", {"title": "clip"}, bad)

    def test_one_broken_account_does_not_stop_other_accounts(self):
        with tempfile.TemporaryDirectory() as d:
            file = Path(d) / "clip.mp4"
            file.write_bytes(b"clip")
            queue = Queue(Path(d) / "queue.sqlite3")
            self.addCleanup(queue.db.close)
            cfg = config()
            cfg["accounts"].append({"id": "youtube-b", "platform": "youtube", "remote_id": "yt-b"})
            first = queue.add(file, "own", "youtube-a", {"title": "clip"}, cfg)
            second = queue.add(file, "own", "youtube-b", {"title": "clip"}, cfg)
            queue.approve(first["id"]); queue.approve(second["id"])
            cfg["accounts"] = [a for a in cfg["accounts"] if a["id"] != "youtube-a"]
            with patch("viceclip.publishers.upload", side_effect=AssertionError("injected uploader expected")):
                result = queue.run(cfg, execute=True, uploader=lambda *a: "remote-b")
            self.assertEqual([r["status"] for r in result], ["failed", "uploaded"])
            self.assertEqual(result[1]["remote_id"], "remote-b")

    def test_instagram_automatic_delivery_precedes_container_and_preserves_hash_check(self):
        job = {"file": "clip.mp4", "sha256": "approved-hash", "metadata": {"title": "clip", "caption": "text"}}
        account = {"remote_id": "ig-a", "_storage": config()["storage"]}
        responses = [{"data": [{"quota_usage": 0, "config": {"quota_total": 100}}]},
                     {"user_id": "ig-a", "username": "creator"}, {"id": "container"},
                     {"status_code": "FINISHED"}, {"id": "published-media"}]
        events = []
        def verify(actual):
            self.assertEqual(actual["sha256"], "approved-hash")
            self.assertEqual(actual["metadata"]["public_url"], "https://media.example.test/approved.mp4?sig=private")
        with patch("viceclip.publishers.request_json", side_effect=responses), patch("viceclip.storage.deliver", return_value={"public_url": "https://media.example.test/approved.mp4?sig=private"}) as delivery, patch("viceclip.publishers.verify_public_media", side_effect=verify) as verifier:
            result = publishers.instagram_upload(job, account, "private-token", lambda *a: events.append(a))
        self.assertEqual(result, "published-media")
        delivery.assert_called_once_with("clip.mp4", config()["storage"], sha256="approved-hash")
        verifier.assert_called_once()
        self.assertEqual(events[0], ("container_created", "container"))

    def test_slow_instagram_container_resumes_without_uploading_or_creating_again(self):
        with tempfile.TemporaryDirectory() as d:
            file = Path(d) / "clip.mp4"
            file.write_bytes(b"clip")
            queue = Queue(Path(d) / "queue.sqlite3")
            self.addCleanup(queue.db.close)
            cfg = config()
            cfg["accounts"][1].update(max_posts_per_day=1, processing_poll_attempts=1)
            job = queue.add(file, "own", "instagram-a", {"title": "clip"}, cfg)
            queue.approve(job["id"])
            quota = {"data": [{"quota_usage": 0, "config": {"quota_total": 100}}]}
            identity = {"user_id": "ig-a", "username": "creator"}
            uploader = lambda j, a, p: publishers.instagram_upload(j, a, "test-token", p)
            with patch("viceclip.publishers.request_json", side_effect=[quota, identity, {"id": "container-once"}, {"status_code": "IN_PROGRESS"}]), patch("viceclip.storage.deliver", return_value={"public_url": "https://media.example.test/clip.mp4"}) as delivery, patch("viceclip.publishers.verify_public_media"):
                first = queue.run(cfg, execute=True, uploader=uploader)
            self.assertEqual(first[0]["status"], "processing")
            delivery.assert_called_once()
            self.assertEqual(queue.list()[0]["remote_id"], "container-once")
            with queue.db:
                queue.db.execute("UPDATE jobs SET scheduled_at='2000-01-01T00:00:00+00:00'")
            with patch("viceclip.publishers.request_json", side_effect=[quota, identity, {"status_code": "FINISHED"}, {"id": "published-media"}]) as api, patch("viceclip.storage.deliver", side_effect=AssertionError("must reuse container")), patch("viceclip.publishers.verify_public_media", side_effect=AssertionError("already fetched")):
                second = queue.run(cfg, execute=True, uploader=uploader)
            self.assertEqual(second[0]["status"], "published")
            posts = [call for call in api.call_args_list if call.kwargs.get("method") == "POST"]
            self.assertEqual(len(posts), 1)
            self.assertTrue(posts[0].args[0].endswith("/media_publish"))
            self.assertEqual(posts[0].kwargs["payload"]["creation_id"], "container-once")

    def test_instagram_quota_waits_without_marking_ambiguous_or_creating_container(self):
        with tempfile.TemporaryDirectory() as d:
            file = Path(d) / "clip.mp4"
            file.write_bytes(b"clip")
            queue = Queue(Path(d) / "queue.sqlite3")
            self.addCleanup(queue.db.close)
            cfg = config()
            job = queue.add(file, "own", "instagram-a", {"title": "clip"}, cfg)
            queue.approve(job["id"])
            with patch("viceclip.publishers.request_json", return_value={"data": [{"quota_usage": 100, "config": {"quota_total": 100}}]}) as api:
                result = queue.run(cfg, execute=True, uploader=lambda j, a, p: publishers.instagram_upload(j, a, "test-token", p))
            self.assertEqual(result[0]["status"], "processing")
            self.assertIsNone(queue.list()[0]["remote_id"])
            self.assertEqual(api.call_count, 1)


if __name__ == "__main__":
    unittest.main()
