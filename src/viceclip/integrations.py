"""Thin orchestration of pinned upstream CLIs and official discovery APIs."""
import datetime as dt
import json
import os
from pathlib import Path
import shutil
import subprocess
import urllib.parse
import urllib.request
from .rights import check_rights

ROOT = Path(__file__).resolve().parents[2]


def executable(name, env=None, local=None):
    candidate = os.environ.get(env, "") if env else ""
    if candidate:
        if Path(candidate).is_file() or shutil.which(candidate):
            return candidate
        raise ValueError(f"Configured executable {env} does not exist")
    if local and (ROOT / local).is_file():
        return str(ROOT / local)
    located = shutil.which(name)
    if not located:
        raise ValueError(f"Install upstream tool first: {name}; see docs/setup.md")
    return located


def transcribe(input_path, output_dir, device="cpu", language=None):
    binary = executable("whisperx", "VICECLIP_WHISPERX", ".tools/whisperx/bin/whisperx")
    out = Path(output_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)
    command = [binary, str(Path(input_path).resolve()), "--output_dir", str(out), "--output_format", "json", "--device", device,
               "--compute_type", "int8" if device == "cpu" else "float16"]
    if language:
        command += ["--language", language]
    subprocess.run(command, check=True)
    result = out / (Path(input_path).stem + ".json")
    if not result.exists():
        raise RuntimeError("WhisperX finished without expected JSON transcript")
    return str(result)


def detect_scenes(input_path, output_dir):
    binary = executable("scenedetect", "VICECLIP_SCENEDETECT", ".venv/bin/scenedetect")
    out = Path(output_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)
    subprocess.run([binary, "-i", str(Path(input_path).resolve()), "-o", str(out), "detect-content", "list-scenes"], check=True)
    return [str(p) for p in sorted(out.glob("*.csv"))]


def autocut(input_path, output_path):
    """Optional upstream pass. Re-transcribe its output because source timing changes."""
    binary = executable("auto-editor", "VICECLIP_AUTO_EDITOR")
    if Path(output_path).exists():
        raise ValueError("Auto-editor output already exists")
    # Preserve motion OR audible action; never use speech-only silence cutting on gameplay.
    subprocess.run([binary, str(Path(input_path).resolve()), "--edit", "(or audio:0.03 motion:0.02)",
                    "--output", str(Path(output_path).resolve()), "--no-open"], check=True)
    return str(Path(output_path).resolve())


def download(config, source_id, output_dir):
    source = check_rights(config, source_id, "download")
    url = source.get("url", "")
    if urllib.parse.urlparse(url).scheme != "https":
        raise ValueError("An approved HTTPS source URL is required")
    binary = executable("yt-dlp", "VICECLIP_YT_DLP", ".venv/bin/yt-dlp")
    out = Path(output_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)
    command = [binary, "--ignore-config", "--no-playlist", "--no-overwrites", "--no-progress", "--restrict-filenames",
               "-f", "bv[ext=mp4][vcodec^=avc1][height<=1080]+ba[ext=m4a]/b[ext=mp4][height<=1080]/b",
               "--merge-output-format", "mp4", "-P", str(out), "-o", "%(extractor)s-%(id)s.%(ext)s",
               "--print", "after_move:filepath", "--", url]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError("yt-dlp could not download this approved source; inspect upstream CLI access/auth requirements")
    paths = [Path(line.strip()) for line in result.stdout.splitlines() if Path(line.strip()).is_file()]
    if len(paths) != 1:
        raise RuntimeError("Download did not return exactly one media path")
    return str(paths[0].resolve())


def _json_get(base, params, headers=None):
    request = urllib.request.Request(base + "?" + urllib.parse.urlencode(params), headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)
    except Exception:
        raise RuntimeError("Discovery API request failed; check configured credentials, quotas and creator IDs") from None


def discover(config, lookback_hours=24):
    """Discover metadata only; inherit permissions solely from explicit channel-wide grants."""
    since = (dt.datetime.now(dt.timezone.utc)-dt.timedelta(hours=lookback_hours)).isoformat().replace("+00:00", "Z")
    found, skipped = [], []
    for creator in config.get("creators", []):
        if not creator.get("enabled", True):
            continue
        platform = creator.get("platform")
        rows = []
        if platform == "youtube":
            key = os.environ.get(creator.get("api_key_env", "YOUTUBE_API_KEY"), "")
            if not key:
                skipped.append({"creator": creator["id"], "reason": "missing YouTube API key"})
                continue
            data = _json_get("https://www.googleapis.com/youtube/v3/search", {"key": key, "part": "snippet", "channelId": creator["remote_id"],
                "type": "video", "order": "date", "publishedAfter": since, "maxResults": 10})
            for item in data.get("items", []):
                video_id = item.get("id", {}).get("videoId")
                if video_id:
                    rows.append((video_id, "https://www.youtube.com/watch?v=" + video_id, item["snippet"]["title"]))
        elif platform == "twitch":
            token = os.environ.get(creator.get("token_env", "TWITCH_ACCESS_TOKEN"), "")
            client_id = os.environ.get(creator.get("client_id_env", "TWITCH_CLIENT_ID"), "")
            if not token or not client_id:
                skipped.append({"creator": creator["id"], "reason": "missing Twitch credentials"})
                continue
            data = _json_get("https://api.twitch.tv/helix/clips", {"broadcaster_id": creator["remote_id"], "started_at": since, "first": 20},
                             {"Client-Id": client_id, "Authorization": "Bearer " + token})
            rows = [(row["id"], row["url"], row["title"]) for row in data.get("data", [])]
        else:
            skipped.append({"creator": creator["id"], "reason": "unsupported platform"})
            continue
        for media_id, url, title in rows:
            permission = dict(creator.get("permission", {}))
            if permission.get("scope") != "all_channel_uploads":
                permission = {"status": "unverified", "reference": "Discovery does not grant rights"}
            source = {"id": platform + "-" + media_id, "url": url, "title": title, "creator": creator["id"],
                      "permission": permission, "platforms": creator.get("destinations", [])}
            try:
                check_rights({"sources": [source]}, source["id"], "download")
                source["ready_for_ingest"] = True
            except ValueError:
                source["ready_for_ingest"] = False
            found.append(source)
    return {"since": since, "sources": found, "skipped": skipped,
            "note": "Bounded discovery: newest 10 YouTube videos or 20 Twitch clips per creator per tick; no VOD downloading API or access bypass."}
