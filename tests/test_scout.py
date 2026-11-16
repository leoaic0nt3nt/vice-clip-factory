import copy
import os
import unittest
from unittest.mock import patch

from scripts.scout_sources import scout_sources


class ScoutTests(unittest.TestCase):
    def test_missing_keys_make_no_requests(self):
        with patch.dict(os.environ, {}, clear=True), patch("scripts.scout_sources._json_get") as get:
            result = scout_sources()
        get.assert_not_called()
        self.assertEqual(result["source_candidates"], [])
        self.assertEqual({row["platform"] for row in result["skipped"]}, {"youtube", "twitch"})
        self.assertFalse(result["auto_approve"])

    def test_grouped_candidates_never_inherit_permissions_or_download(self):
        config = {"sources": [{"id": "youtube-v1", "permission": {"status": "owned", "allow_download": True}}],
                  "creators": [{"remote_id": "UC1", "permission": {"status": "licensed", "allow_publish": True}}]}
        original = copy.deepcopy(config)
        responses = [
            {"items": [{"id": {"videoId": video}, "snippet": {"channelId": "UC1", "channelTitle": "Creator",
                "title": "GTA moment", "publishedAt": "2026-11-16T00:00:00Z"}} for video in ("v1", "v2")]},
            {"data": [{"id": "game-1", "name": "Grand Theft Auto VI"}]},
            {"data": [{"id": "stream-1", "user_id": "u1", "user_login": "creator",
                "user_name": "Creator", "viewer_count": 1200, "title": "Gameplay"}]}]
        env = {"YOUTUBE_API_KEY": "test-youtube", "TWITCH_ACCESS_TOKEN": "test-twitch", "TWITCH_CLIENT_ID": "test-client"}
        with patch.dict(os.environ, env, clear=True), patch("scripts.scout_sources._json_get", side_effect=responses) as get:
            result = scout_sources(config=config)
        self.assertEqual(get.call_count, 3)
        self.assertEqual(get.call_args_list[0].args[1]["maxResults"], 10)
        self.assertEqual(get.call_args_list[0].args[1]["order"], "viewCount")
        self.assertEqual(get.call_args_list[2].args[1], {"game_id": "game-1", "first": 20})
        self.assertEqual(len(result["source_candidates"]), 2)
        youtube = next(row for row in result["source_candidates"] if row["platform"] == "youtube")
        self.assertEqual(len(youtube["evidence"]), 2)
        for row in result["source_candidates"]:
            self.assertEqual(row["status"], "needs_rights")
            self.assertEqual(row["permission"]["status"], "unverified")
            for action in ("download", "edit", "publish"):
                self.assertIs(row["permission"]["allow_" + action], False)
            self.assertTrue(row["public_url"].startswith("https://"))
        self.assertEqual(config, original)
        self.assertNotIn("test-youtube", str(result))
        self.assertNotIn("test-twitch", str(result))


if __name__ == "__main__":
    unittest.main()
