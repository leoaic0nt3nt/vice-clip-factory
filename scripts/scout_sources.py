#!/usr/bin/env python3
"""Scout public creator metadata; never acquire media or approve source rights."""
import argparse
import datetime as dt
import json
import os
from pathlib import Path
import sys
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from viceclip.integrations import _json_get


def scout_sources(query="GTA VI gameplay stream", twitch_game="Grand Theft Auto VI",
                  lookback_hours=72, config=None):
    if not 0 < lookback_hours <= 168 or not query.strip() or not twitch_game.strip():
        raise ValueError("Use nonempty search/game names and a lookback of 0-168 hours")
    settings = (config or {}).get("scout", {})
    now = dt.datetime.now(dt.timezone.utc)
    since = (now - dt.timedelta(hours=lookback_hours)).isoformat().replace("+00:00", "Z")
    candidates, skipped = {}, []

    def record(platform, author_id, name, url, evidence):
        key = platform + ":" + author_id
        if key not in candidates:
            candidates[key] = {"id": "scout:" + key, "platform": platform,
                "author_id": author_id, "author_name": name, "public_url": url,
                "status": "needs_rights", "evidence": [],
                "permission": {"status": "unverified", "reference": "",
                    "allow_download": False, "allow_edit": False, "allow_publish": False}}
        candidates[key]["evidence"].append(evidence)

    key = os.environ.get(settings.get("youtube_api_key_env", "YOUTUBE_API_KEY"), "")
    if not key:
        skipped.append({"platform": "youtube", "reason": "missing YouTube API key"})
    else:
        try:
            result = _json_get("https://www.googleapis.com/youtube/v3/search", {
                "key": key, "part": "snippet", "q": query, "type": "video",
                "order": "viewCount", "publishedAfter": since, "maxResults": 10})
            for item in result.get("items", [])[:10]:
                snippet = item.get("snippet", {})
                author_id = snippet.get("channelId")
                video_id = item.get("id", {}).get("videoId")
                if not author_id or not video_id:
                    continue
                record("youtube", str(author_id), snippet.get("channelTitle", ""),
                    "https://www.youtube.com/channel/" + quote(str(author_id), safe=""), {
                    "kind": "recent_video", "title": snippet.get("title", ""),
                    "url": "https://www.youtube.com/watch?v=" + quote(str(video_id), safe=""),
                    "published_at": snippet.get("publishedAt"),
                    "ranking": "search.order=viewCount; counts not returned by search"})
        except RuntimeError:
            skipped.append({"platform": "youtube", "reason": "discovery request failed"})

    token = os.environ.get(settings.get("twitch_token_env", "TWITCH_ACCESS_TOKEN"), "")
    client = os.environ.get(settings.get("twitch_client_id_env", "TWITCH_CLIENT_ID"), "")
    if not token or not client:
        skipped.append({"platform": "twitch", "reason": "missing Twitch credentials"})
    else:
        try:
            headers = {"Client-Id": client, "Authorization": "Bearer " + token}
            games = _json_get("https://api.twitch.tv/helix/games", {"name": twitch_game}, headers)
            game_id = next((g.get("id") for g in games.get("data", []) if g.get("id")), None)
            if not game_id:
                skipped.append({"platform": "twitch", "reason": "game category not found"})
            else:
                streams = _json_get("https://api.twitch.tv/helix/streams",
                                    {"game_id": game_id, "first": 20}, headers)
                for stream in streams.get("data", [])[:20]:
                    author_id, login = stream.get("user_id"), stream.get("user_login")
                    if not author_id or not login:
                        continue
                    url = "https://www.twitch.tv/" + quote(str(login), safe="")
                    record("twitch", str(author_id), stream.get("user_name", str(login)), url, {
                        "kind": "live_stream", "stream_id": stream.get("id"), "url": url,
                        "title": stream.get("title", ""), "game_id": game_id,
                        "viewer_count": stream.get("viewer_count"), "started_at": stream.get("started_at")})
        except RuntimeError:
            skipped.append({"platform": "twitch", "reason": "discovery request failed"})
    return {"checked_at": now.isoformat(), "since": since, "query": query,
        "twitch_game": twitch_game, "source_candidates": list(candidates.values()),
        "skipped": skipped, "auto_approve": False,
        "limitations": "Metadata only: newest-window YouTube top 10 by viewCount and Twitch live top 20; no pagination, media download, rights inheritance or registry merge. Twitch lists current streams, not a historical lookback."}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--query", default="GTA VI gameplay stream")
    parser.add_argument("--twitch-game", default="Grand Theft Auto VI")
    parser.add_argument("--lookback-hours", type=float, default=72)
    parser.add_argument("--config", type=Path, help="Optional JSON: scout.*_env names only; rights are never inherited")
    parser.add_argument("--output", type=Path, default=Path("output/source-candidates.json"))
    args = parser.parse_args(argv)
    try:
        config = json.loads(args.config.read_text(encoding="utf-8")) if args.config else None
        result = scout_sources(args.query, args.twitch_game, args.lookback_hours, config)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except (ValueError, OSError, TypeError, AttributeError) as exc:
        parser.error(str(exc))
    print(json.dumps({"output": str(args.output.resolve()), "candidates": len(result["source_candidates"]),
                      "skipped": result["skipped"], "auto_approve": False}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
