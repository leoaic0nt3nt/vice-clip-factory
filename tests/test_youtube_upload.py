"""YouTube protocol tests with fake Google responses and a real local queue."""
import io
import json
import tempfile
import unittest
import urllib.error
import urllib.parse
from email.message import Message
from pathlib import Path
from unittest.mock import patch

from viceclip import publishers
from viceclip.queue import Queue


SESSION = "https://www.googleapis.com/upload/youtube/v3/videos?upload_id=private-session"
VIDEO_ID = "abcDEF12_-3"
CHANNEL = {"id": "channel-owned", "snippet": {"title": "Owned Channel"}}
CONTENTS = b"approved-clip-bytes"


class Response(io.BytesIO):
    def __init__(self, body=None, headers=None):
        super().__init__(json.dumps(body).encode() if body is not None else b"")
        self.headers = _headers(headers or {})


def _headers(values):
    headers = Message()
    for key, value in values.items():
        headers[key] = value
    return headers


def _http_error(code, *, headers=None, reason=None):
    body = {"error": {"errors": [{"reason": reason}]}}
    return urllib.error.HTTPError(SESSION, code, "response omitted", _headers(headers or {}), Response(body))


class YouTubeUploadTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "clip.mp4"
        self.path.write_bytes(CONTENTS)
        self.account = {"id": "owned", "platform": "youtube", "remote_id": CHANNEL["id"]}
        self.config = {"source_policy": {"mode": "configured_sources", "user_responsible": True},
                       "sources": [{"id": "source", "platforms": ["youtube"]}], "accounts": [self.account]}
        self.queue = Queue(Path(tmp.name) / "queue.sqlite3")
        self.addCleanup(self.queue.db.close)
        self.sent = []

    def _enqueue(self, **metadata):
        queued = self.queue.add(self.path, "source", "owned", {"title": "A short clip", **metadata}, self.config)
        self.queue.approve(queued["id"])
        return queued["id"]

    def _due(self, job_id):
        with self.queue.db:
            self.queue.db.execute("UPDATE jobs SET scheduled_at=? WHERE id=?", ("2000-01-01T00:00:00+00:00", job_id))

    def _run(self, *replies):
        pending = iter(replies)

        def api(request, *, timeout):
            path = urllib.parse.urlsplit(request.full_url)
            if path.path == "/youtube/v3/channels":
                self.assertEqual(request.method, "GET")
                self.assertIn("mine=true", path.query)
                return Response({"items": [CHANNEL]})
            body = request.data.read() if hasattr(request.data, "read") else request.data
            self.sent.append((request.method, request.full_url, dict(request.header_items()), body))
            result = next(pending)
            if isinstance(result, Exception):
                raise result
            return result

        with patch("viceclip.publishers.token_for", return_value="test-only-token"), patch("urllib.request.urlopen", side_effect=api):
            return self.queue.run(self.config, execute=True)

    def test_upload_declares_language_privacy_and_paid_placement(self):
        for sponsored in (False, True):
            with self.subTest(sponsored=sponsored):
                self.path.write_bytes(CONTENTS + bytes((sponsored,)))
                self._enqueue(sponsored=sponsored, privacy="unlisted", language="en")
                result = self._run(Response(headers={"Location": SESSION}), Response({"id": VIDEO_ID}))
                self.assertEqual(result[0]["status"], "uploaded")
                self.assertFalse(result[0]["playback_verified"])
                request = self.sent[-2]
                self.assertEqual(request[0], "POST")
                self.assertIn("paidProductPlacementDetails", urllib.parse.parse_qs(urllib.parse.urlsplit(request[1]).query)["part"][0])
                body = json.loads(request[3])
                self.assertEqual(body["status"]["privacyStatus"], "unlisted")
                self.assertEqual(body["snippet"]["defaultLanguage"], "en")
                self.assertIs(body["paidProductPlacementDetails"]["hasPaidProductPlacement"], sponsored)
                self.assertEqual(self.sent[-1][3], self.path.read_bytes())

    def test_interrupted_upload_checks_saved_session_and_sends_only_remaining_bytes(self):
        job_id = self._enqueue()
        result = self._run(Response(headers={"Location": SESSION}), TimeoutError("connection lost"))
        self.assertEqual(result[0]["status"], "processing")
        self.assertNotIn("session_uri", self.queue.list()[0])
        self._due(job_id)
        result = self._run(_http_error(308, headers={"Range": "bytes=0-4"}), Response({"id": VIDEO_ID}))
        self.assertEqual(result[0]["status"], "uploaded")
        self.assertEqual(sum(method == "POST" for method, *_ in self.sent), 1)
        _, location, headers, body = self.sent[-2]
        self.assertEqual(location, SESSION)
        self.assertIsNone(body)
        self.assertEqual(headers["Content-range"], f"bytes */{len(CONTENTS)}")
        _, location, headers, body = self.sent[-1]
        self.assertEqual(location, SESSION)
        self.assertEqual(headers["Content-range"], f"bytes 5-{len(CONTENTS)-1}/{len(CONTENTS)}")
        self.assertEqual(body, CONTENTS[5:])

    def test_completed_session_returns_id_without_resending_media(self):
        job_id = self._enqueue()
        self._run(Response(headers={"Location": SESSION}), TimeoutError("missing final response"))
        self._due(job_id)
        result = self._run(Response({"id": VIDEO_ID}))
        self.assertEqual(result[0]["remote_id"], VIDEO_ID)
        self.assertEqual(len(self.sent), 3)
        self.assertIsNone(self.sent[-1][3])

    def test_process_restart_recovers_saved_session_without_starting_another(self):
        job_id = self._enqueue()
        with self.queue.db:
            self.queue.db.execute(
                "UPDATE jobs SET status='uploading',stage='upload_started',session_uri=? WHERE id=?",
                (SESSION, job_id),
            )
        result = self._run(Response({"id": VIDEO_ID}))
        self.assertEqual(result[0]["status"], "uploaded")
        self.assertEqual([(method, url, body) for method, url, _, body in self.sent],
                         [("PUT", SESSION, None)])

    def test_expired_session_holds_job_for_reconciliation(self):
        job_id = self._enqueue()
        self._run(Response(headers={"Location": SESSION}), TimeoutError("missing final response"))
        self._due(job_id)
        result = self._run(_http_error(404))
        self.assertEqual(result[0]["status"], "unknown")
        self.assertNotIn(SESSION, result[0]["error"])
        with self.assertRaises(ValueError):
            self.queue.approve(job_id)
        self.assertEqual(self._run(), [])

    def test_quota_response_defers_before_media_transfer(self):
        self._enqueue()
        result = self._run(_http_error(403, reason="quotaExceeded"))
        self.assertEqual(result[0]["status"], "processing")
        self.assertEqual([method for method, *_ in self.sent], ["POST"])
        self.assertIsNone(self.queue.db.execute("SELECT session_uri FROM jobs").fetchone()[0])


class YouTubeReadinessTests(unittest.TestCase):
    def test_owned_video_reports_actual_visibility_and_processing(self):
        response = {"items": [{"id": VIDEO_ID, "snippet": {"channelId": CHANNEL["id"]},
                               "status": {"privacyStatus": "private", "uploadStatus": "processed"},
                               "processingDetails": {"processingStatus": "succeeded"},
                               "paidProductPlacementDetails": {"hasPaidProductPlacement": False}}]}
        account = {"platform": "youtube", "id": "owned", "remote_id": CHANNEL["id"]}
        with patch("viceclip.publishers.token_for", return_value="test-only-token"), patch(
            "viceclip.publishers.request_json", side_effect=[{"items": [CHANNEL]}, response],
        ) as api:
            result = publishers.youtube_readiness(account, video_id=VIDEO_ID, expected_privacy="public")
        self.assertTrue(result["oauth_channel_verified"])
        self.assertFalse(result["video"]["privacy_matches"])
        self.assertFalse(result["video"]["playback_verified"])
        self.assertEqual(result["video"]["processing_status"], "succeeded")
        self.assertTrue(all(call.kwargs.get("method", "GET") == "GET" for call in api.call_args_list))
        self.assertNotIn("test-only-token", str(result))

    def test_right_token_cannot_claim_a_different_channels_video(self):
        response = {"items": [{"id": VIDEO_ID, "snippet": {"channelId": "other"}}]}
        account = {"platform": "youtube", "id": "owned", "remote_id": CHANNEL["id"]}
        with patch("viceclip.publishers.token_for", return_value="test-only-token"), patch(
            "viceclip.publishers.request_json", side_effect=[{"items": [CHANNEL]}, response],
        ):
            with self.assertRaisesRegex(ValueError, "not found on the connected"):
                publishers.youtube_readiness(account, video_id=VIDEO_ID)
