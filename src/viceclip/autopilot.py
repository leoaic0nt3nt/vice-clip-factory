"""Persistent source-to-publish orchestration for the Vice Clip Factory service.

Configuration selects sources once. A run does not ask for approval on each clip.
Provider credentials, network access and account quotas remain platform concerns.
"""
from __future__ import annotations

import contextlib
import datetime as dt
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import signal
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request

from . import integrations
from .media import fingerprint, probe
from .queue import Queue
from .rights import load_config
from . import promo

ROOT = Path(__file__).resolve().parents[2]


class SourceNotReady(RuntimeError):
    """A scheduled/live source is still being recorded; wait without using retry budget."""


def _save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _error(error):
    message = re.sub(r"(?i)(access_token|token|api_key|signature|sig)=([^&\s]+)", r"\1=[redacted]", str(error))
    return re.sub(r"(?i)Bearer\s+\S+", "Bearer [redacted]", message)[:400]


class Autopilot:
    """One persistent worker per state database, with resumable local stages."""

    def __init__(self, config, *, base_dir=None, state_path=None, clock=time.time,
                 discoverer=None, downloader=None, processor=None, scouter=None, queue_factory=Queue):
        self.config_path = Path(config).expanduser().resolve() if isinstance(config, (str, Path)) else None
        self.config = load_config(self.config_path) if self.config_path else dict(config)
        self.base = Path(base_dir or (self.config_path.parent if self.config_path else ROOT)).expanduser().resolve()
        self.clock = clock
        self.discoverer = discoverer or integrations.discover
        self.downloader = downloader
        self.processor = processor
        self.scouter = scouter
        self.queue_factory = queue_factory
        self.stop_event = None
        self.settings = self.config.get("autopilot", {})
        self.state_path = self._path(state_path or self.settings.get("state_path", "data/autopilot.sqlite3"))
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.state_path, timeout=30)
        os.chmod(self.state_path, 0o600)
        self.db.row_factory = sqlite3.Row
        self.db.execute("""CREATE TABLE IF NOT EXISTS media_jobs (
            key TEXT PRIMARY KEY, source_id TEXT NOT NULL, source_json TEXT NOT NULL,
            stage TEXT NOT NULL DEFAULT 'new', input_path TEXT, input_sha TEXT,
            result TEXT, failures INTEGER NOT NULL DEFAULT 0, retry_at REAL NOT NULL DEFAULT 0,
            error TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL)""")
        self.db.execute("CREATE TABLE IF NOT EXISTS worker_meta (key TEXT PRIMARY KEY, value TEXT)")
        columns = {row["name"] for row in self.db.execute("PRAGMA table_info(media_jobs)")}
        if "routing_signature" not in columns:
            self.db.execute("ALTER TABLE media_jobs ADD COLUMN routing_signature TEXT")
        self.db.commit()

    def _path(self, path):
        value = Path(path).expanduser()
        return value.resolve() if value.is_absolute() else (self.base / value).resolve()

    def close(self):
        self.db.close()

    def _reload(self):
        if self.config_path:
            self.config = load_config(self.config_path)
        self.settings = self.config.get("autopilot", {})

    def _run(self, command, *, timeout, text=False, capture_output=True, check=True, **kwargs):
        """Bound the entire child process group, including FFmpeg spawned by a downloader."""
        process = subprocess.Popen(command, start_new_session=True, text=text,
            stdout=subprocess.PIPE if capture_output else None,
            stderr=subprocess.PIPE if capture_output else None, **kwargs)
        deadline = time.monotonic() + timeout
        try:
            while True:
                if self.stop_event and self.stop_event.is_set():
                    raise InterruptedError("Worker is stopping")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(command, timeout)
                try:
                    output, error = process.communicate(timeout=min(1, remaining))
                    break
                except subprocess.TimeoutExpired:
                    continue
        except BaseException:
            try:
                os.killpg(process.pid, signal.SIGTERM)
                process.communicate(timeout=3)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
                process.communicate()
            raise
        if check and process.returncode:
            raise subprocess.CalledProcessError(process.returncode, command, output, error)
        return subprocess.CompletedProcess(command, process.returncode, output, error)

    @contextlib.contextmanager
    def _lock(self):
        import fcntl
        with open(str(self.state_path) + ".lock", "a") as stream:
            os.chmod(stream.name, 0o600)
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                yield False
            else:
                try:
                    yield True
                finally:
                    fcntl.flock(stream, fcntl.LOCK_UN)

    def _record(self, key, source):
        now = self.clock()
        with self.db:
            self.db.execute("""INSERT INTO media_jobs(key,source_id,source_json,created_at,updated_at)
                VALUES(?,?,?,?,?) ON CONFLICT(key) DO UPDATE SET source_json=excluded.source_json""",
                (key, source["id"], json.dumps(source), now, now))

    def _set(self, key, **values):
        values["updated_at"] = self.clock()
        with self.db:
            self.db.execute("UPDATE media_jobs SET " + ",".join(name + "=?" for name in values) + " WHERE key=?",
                            (*values.values(), key))

    def _source_key(self, source):
        path = source.get("path") or source.get("local_path")
        if path:
            return "file:" + source["id"] + ":" + fingerprint(self._path(path))
        digest = hashlib.sha256(str(source.get("url", "")).encode()).hexdigest()
        key = "url:" + source["id"] + ":" + digest
        saved_live = self.db.execute("SELECT value FROM worker_meta WHERE key=?", ("live_source:" + source["id"],)).fetchone()
        if source.get("live") is True or (self.settings.get("live_segments") is True and saved_live and saved_live[0] == "true"):
            seconds = max(30, int(self.settings.get("live_segment_seconds", 180)))
            key += ":" + str(int(self.clock()) // seconds)
        return key

    def _gather(self, report):
        explicit = {s["id"]: dict(s) for s in self.config.get("sources", []) if s.get("enabled", True)}
        allowed_ids = self.settings.get("source_ids")
        for source in explicit.values():
            if allowed_ids is not None and source["id"] not in allowed_ids:
                continue
            if not source.get("url") and not (source.get("path") or source.get("local_path")):
                continue
            try:
                self._record(self._source_key(source), source)
            except (ValueError, OSError) as exc:
                report["errors"].append({"source": source["id"], "error": _error(exc)})
        for inbox in self.settings.get("inboxes", []):
            template = explicit.get(inbox["source_id"])
            if template is None:
                report["errors"].append({"source": inbox["source_id"], "error": "Inbox source is not configured/enabled"})
                continue
            directory = self._path(inbox["path"])
            if not directory.is_dir():
                report["errors"].append({"source": inbox["source_id"], "error": "Inbox directory does not exist"})
                continue
            for path in sorted(directory.iterdir()):
                if not path.is_file() or path.suffix.lower() not in (".mp4", ".mov", ".mkv", ".webm"):
                    continue
                if self.clock() - path.stat().st_mtime < int(inbox.get("stable_seconds", 30)):
                    continue
                source = {**template, "path": str(path.resolve()), "url": ""}
                self._record(self._source_key(source), source)
        # A failed source API must not prevent other creators, files or publishing.
        for creator in self.config.get("creators", []):
            if not creator.get("enabled", True):
                continue
            try:
                found = self._discover_creator(creator)
                report["discovery"].extend(found.get("skipped", []))
                for source in found.get("sources", []):
                    source = {**source, "creator": creator["id"], "enabled": True, "selected": True,
                              "platforms": creator.get("destinations") or self._platforms()}
                    self._record(self._source_key(source), source)
            except Exception as exc:
                report["errors"].append({"creator": creator.get("id"), "error": _error(exc)})
        self._scout_queries(report)

    def _scout_queries(self, report):
        queries = self.settings.get("scout_queries", [])
        if not queries:
            return
        if self.scouter is None and os.environ.get("TWITCH_CLIENT_ID"):
            from . import auth
            if hasattr(auth, "twitch_token"):
                try:
                    os.environ["TWITCH_ACCESS_TOKEN"] = auth.twitch_token({})
                except ValueError as exc:
                    report["errors"].append({"stage": "twitch_connection", "error": _error(exc)})
        policy = self.config.get("source_policy", {})
        ingest = self.settings.get("ingest_scout_candidates") is True and policy.get("mode") == "configured_sources" and policy.get("user_responsible") is True
        for query in queries[:3]:
            if not isinstance(query, str) or not query.strip():
                continue
            key = "scout:" + hashlib.sha256(query.encode()).hexdigest()
            previous = self.db.execute("SELECT value FROM worker_meta WHERE key=?", (key,)).fetchone()
            stamp = json.loads(previous[0]) if previous else {}
            connected = bool(os.environ.get("YOUTUBE_API_KEY")) or bool(os.environ.get("TWITCH_ACCESS_TOKEN"))
            if previous and stamp.get("connected") == connected and self.clock() - stamp.get("at", 0) < int(self.settings.get("scout_interval_seconds", 3600)):
                continue
            try:
                scouter = self.scouter
                if scouter is None:
                    spec = importlib.util.spec_from_file_location("viceclip_source_scout", ROOT / "scripts/scout_sources.py")
                    module = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(module)
                    scouter = module.scout_sources
                found = scouter(query=query, twitch_game=self.settings.get("twitch_game", "Grand Theft Auto VI"),
                                lookback_hours=min(168, float(self.settings.get("lookback_hours", 24))))
                _save(self._path(self.settings.get("output_dir", "output/autopilot")) / (key.replace(":", "-")[:24] + ".json"), found)
                report["discovery"].append({"query": query, "candidates": len(found["source_candidates"]), "auto_ingest": ingest})
                if ingest:
                    for candidate in found["source_candidates"][:max(1, int(self.settings.get("scout_max_authors", 3)))]:
                        sources = []
                        if candidate["platform"] == "youtube":
                            for evidence in candidate.get("evidence", [])[:2]:
                                url = evidence.get("url", "")
                                video_id = urllib.parse.parse_qs(urllib.parse.urlparse(url).query).get("v", [""])[0]
                                if video_id:
                                    sources.append({"id": "scout-youtube-" + video_id, "url": url, "title": evidence.get("title", "")})
                        elif candidate["platform"] == "twitch":
                            if self.settings.get("live_segments") is True:
                                sources = [{"id": "scout-twitch-" + candidate["author_id"], "url": candidate["public_url"], "live": True}]
                            else:
                                creator = {"id": "query-twitch-" + candidate["author_id"], "platform": "twitch", "remote_id": candidate["author_id"], "enabled": True}
                                sources = self._discover_creator(creator).get("sources", [])[:3]
                        for source in sources:
                            source.update(enabled=True, selected=True, query=query, platforms=self._platforms())
                            self._record(self._source_key(source), source)
                with self.db:
                    self.db.execute("INSERT OR REPLACE INTO worker_meta VALUES(?,?)", (key, json.dumps({"at": self.clock(), "connected": connected})))
            except Exception as exc:
                report["errors"].append({"query": query, "error": _error(exc)})

    def _youtube_entries(self, metadata, *, remaining=3):
        """Unwrap channel tabs; a channel/playlist ID must never become a video ID."""
        videos = []
        for entry in metadata.get("entries", []):
            if not entry:
                continue
            if isinstance(entry.get("entries"), list):
                videos.extend(self._youtube_entries(entry, remaining=remaining))
                continue
            url = entry.get("url", "")
            parsed = urllib.parse.urlparse(url)
            is_tab = parsed.netloc.endswith("youtube.com") and parsed.path.rstrip("/").endswith(("/videos", "/streams"))
            if is_tab and remaining > 0:
                binary = integrations.executable("yt-dlp", "VICECLIP_YT_DLP", ".venv/bin/yt-dlp")
                nested = self._run([binary, "--ignore-config", "--flat-playlist", "--playlist-end", "10", "--dump-single-json",
                                    "--skip-download", "--no-warnings", "--", url], capture_output=True, text=True, check=True, timeout=90)
                videos.extend(self._youtube_entries(json.loads(nested.stdout), remaining=remaining - 1))
            elif re.fullmatch(r"[A-Za-z0-9_-]{11}", str(entry.get("id", ""))) and entry.get("_type") != "playlist":
                videos.append(entry)
            if len(videos) >= 10:
                break
        return videos[:10]

    def _discover_creator(self, creator):
        """Poll selected public channels without requiring a YouTube API project."""
        if self.discoverer is not integrations.discover:
            return self.discoverer({**self.config, "creators": [creator]}, self.settings.get("lookback_hours", 24))
        creator = dict(creator)
        if creator.get("platform") == "youtube" and creator.get("url"):
            key = os.environ.get(creator.get("api_key_env", "YOUTUBE_API_KEY"), "")
            if not (creator.get("remote_id") and key):
                binary = integrations.executable("yt-dlp", "VICECLIP_YT_DLP", ".venv/bin/yt-dlp")
                response = self._run([binary, "--ignore-config", "--flat-playlist", "--playlist-end", "10",
                    "--dump-single-json", "--skip-download", "--no-warnings", "--", creator["url"]],
                    capture_output=True, text=True, check=True, timeout=90)
                metadata = json.loads(response.stdout)
                sources = []
                since = self.clock() - float(self.settings.get("lookback_hours", 24)) * 3600
                for entry in self._youtube_entries(metadata):
                    if not entry or not entry.get("id"):
                        continue
                    timestamp = entry.get("timestamp") or entry.get("release_timestamp")
                    if timestamp and timestamp < since:
                        continue
                    live = entry.get("is_live") is True or entry.get("live_status") == "is_live"
                    if entry.get("live_status") == "is_upcoming" or (live and self.settings.get("live_segments") is not True):
                        continue
                    source = {"id": "youtube-" + entry["id"], "url": "https://www.youtube.com/watch?v=" + entry["id"],
                              "title": entry.get("title", ""), "creator": creator["id"], "live": live,
                              "permission": dict(creator.get("permission", {}))}
                    sources.append(source)
                return {"sources": sources, "skipped": []}
        if creator.get("platform") == "twitch":
            from . import auth
            if hasattr(auth, "twitch_token"):
                os.environ[creator.get("token_env", "TWITCH_ACCESS_TOKEN")] = auth.twitch_token(creator)
        if creator.get("platform") == "twitch" and not creator.get("remote_id"):
            login = creator.get("login") or urllib.parse.urlparse(creator.get("url", "")).path.strip("/").split("/")[0]
            client = os.environ.get(creator.get("client_id_env", "TWITCH_CLIENT_ID"), "")
            token = os.environ.get(creator.get("token_env", "TWITCH_ACCESS_TOKEN"), "")
            if not login or not client or not token:
                return {"sources": [], "skipped": [{"creator": creator["id"], "reason": "Twitch connection is not configured"}]}
            users = integrations._json_get("https://api.twitch.tv/helix/users", {"login": login},
                                          {"Client-Id": client, "Authorization": "Bearer " + token})
            if not users.get("data"):
                return {"sources": [], "skipped": [{"creator": creator["id"], "reason": "Twitch creator was not found"}]}
            creator["remote_id"] = users["data"][0]["id"]
        return self.discoverer({**self.config, "creators": [creator]}, self.settings.get("lookback_hours", 24))

    def _platforms(self):
        return sorted({a["platform"] for a in self.config.get("accounts", [])})

    def _runtime(self):
        sources = {s["id"]: dict(s) for s in self.config.get("sources", [])}
        creators = {c["id"]: c for c in self.config.get("creators", []) if c.get("enabled", True)}
        for row in self.db.execute("SELECT source_json FROM media_jobs"):
            source = json.loads(row[0])
            if source["id"] not in sources and (source.get("creator") in creators or self._query_selected(source)):
                sources[source["id"]] = source
        return {**self.config, "sources": list(sources.values())}

    def _query_selected(self, source):
        policy = self.config.get("source_policy", {})
        return (self.settings.get("ingest_scout_candidates") is True and policy.get("mode") == "configured_sources"
                and policy.get("user_responsible") is True and source.get("query") in self.settings.get("scout_queries", []))

    def _is_active(self, source):
        for configured in self.config.get("sources", []):
            if configured["id"] == source["id"]:
                selected = self.settings.get("source_ids")
                inbox_ids = {item["source_id"] for item in self.settings.get("inboxes", [])}
                return configured.get("enabled", True) and (selected is None or configured["id"] in selected or configured["id"] in inbox_ids)
        return self._query_selected(source) or any(c.get("id") == source.get("creator") and c.get("enabled", True)
                   for c in self.config.get("creators", []))

    def _cleanup_download(self, source, path, work):
        if self.settings.get("cleanup_downloads", True) is not True or source.get("path") or source.get("local_path"):
            return False
        path = Path(path).resolve() if path else None
        if path and path.is_file() and path.is_relative_to(work.resolve()):
            path.unlink()
            return True
        return False

    def _input(self, source, work, runtime):
        if self.downloader:
            return Path(self.downloader(source, work, runtime)).resolve()
        path = source.get("path") or source.get("local_path")
        if path:
            result = self._path(path)
            if not result.is_file():
                raise ValueError("Configured input file is missing")
            return result
        url = source.get("url", "")
        if urllib.parse.urlparse(url).scheme != "https":
            raise ValueError("A public HTTPS source URL or local file is required")
        if source.get("live") is True:
            return self._capture_live(url, work)
        output = work / "input"
        output.mkdir(parents=True, exist_ok=True)
        if source.get("acquisition") == "direct" or urllib.parse.urlparse(url).path.lower().endswith((".mp4", ".mov", ".mkv", ".webm")):
            target = output / "source.mp4"
            temporary = target.with_suffix(".part")
            limit = int(self.settings.get("max_download_bytes", 2 * 1024**3))
            size, started = 0, time.monotonic()
            try:
                with urllib.request.urlopen(url, timeout=30) as response, open(temporary, "wb") as stream:
                    for chunk in iter(lambda: response.read(1024 * 1024), b""):
                        size += len(chunk)
                        if size > limit or time.monotonic() - started > self.settings.get("download_timeout_seconds", 900):
                            raise ValueError("Source download exceeded the configured size/time budget")
                        stream.write(chunk)
                os.replace(temporary, target)
            finally:
                temporary.unlink(missing_ok=True)
            return target
        # Run the existing downloader in a child process so a hanging remote source
        # cannot hold the service forever. Its source config stays in a private dir.
        binary = integrations.executable("yt-dlp", "VICECLIP_YT_DLP", ".venv/bin/yt-dlp")
        metadata = self._run([binary, "--ignore-config", "--no-playlist", "--skip-download", "--dump-single-json", "--no-warnings", "--", url],
                                  capture_output=True, text=True, check=True, timeout=60)
        metadata = json.loads(metadata.stdout)
        if metadata.get("live_status") == "is_upcoming":
            raise SourceNotReady("Source is scheduled but has not started")
        if metadata.get("is_live") is True or metadata.get("live_status") == "is_live":
            if self.settings.get("live_segments") is not True:
                raise SourceNotReady("Live source is recording; completed-video mode will retry later")
            with self.db:
                self.db.execute("INSERT OR REPLACE INTO worker_meta VALUES(?,?)", ("live_source:" + source["id"], "true"))
            return self._capture_live(url, work)
        if metadata.get("duration") and float(metadata["duration"]) > float(self.settings.get("max_source_seconds", 10800)):
            raise ValueError("Source exceeds max_source_seconds before download")
        config_file = work / "runtime-config.json"
        _save(config_file, runtime)
        os.chmod(config_file, 0o600)
        command = [sys.executable, "-m", "viceclip.cli", "download", source["id"], "--config", str(config_file), "--output-dir", str(output)]
        result = self._run(command, cwd=ROOT, capture_output=True, text=True, check=True,
                                timeout=int(self.settings.get("download_timeout_seconds", 900)))
        return Path(json.loads(result.stdout)["input"]).resolve()

    def _capture_live(self, url, work):
        binary = integrations.executable("yt-dlp", "VICECLIP_YT_DLP", ".venv/bin/yt-dlp")
        result = self._run([binary, "--ignore-config", "--no-playlist", "-f", "b[height<=1080]/b", "--get-url", "--", url],
                                capture_output=True, text=True, check=True, timeout=60)
        urls = [x.strip() for x in result.stdout.splitlines() if x.strip().startswith("https://")]
        if len(urls) != 1:
            raise ValueError("Live capture requires a single muxed public stream URL")
        seconds = min(900, max(30, int(self.settings.get("live_segment_seconds", 180))))
        target = work / "live-segment.mp4"
        temporary = work / "live-segment.partial.mp4"
        self._run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-i", urls[0],
                        "-t", str(seconds), "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac", "-movflags", "+faststart", "-y", str(temporary)],
                       capture_output=True, check=True, timeout=seconds + 90)
        os.replace(temporary, target)
        return target

    def _process(self, source, input_path, work, runtime):
        if self.processor:
            return self.processor(source, input_path, work, runtime)
        result_file = work / "result.json"
        if result_file.exists():
            result = json.loads(result_file.read_text())
            if promo.valid_result_files(result, fingerprint):
                return result
            raise ValueError("Saved render changed; inspect the run directory before retry")
        info = probe(input_path)
        duration = float(info["format"]["duration"])
        if duration > float(self.settings.get("max_source_seconds", 10800)):
            raise ValueError("Source exceeds max_source_seconds; use bounded segments or a shorter source")
        if self.settings.get("engine", "baseline") == "medusa":
            if promo.active_campaign(runtime):
                raise ValueError("Рекламный режим использует стандартный движок: выберите autopilot.engine=baseline")
            return self._medusa(source, input_path, work, runtime)
        from .cli import plan_media
        from .render import render
        plan_file = work / "plan.json"
        if plan_file.exists():
            plan = json.loads(plan_file.read_text())
            if plan["analysis"]["sha256"] != fingerprint(input_path):
                raise ValueError("Source bytes changed after planning")
        else:
            transcript = source.get("transcript")
            if transcript:
                transcript = str(self._path(transcript))
            asr = self.settings.get("asr", {})
            if asr.get("enabled", False):
                if asr.get("provider", "faster-whisper") == "faster-whisper":
                    from .speech import transcribe_file
                    transcript = transcribe_file(input_path, work / "asr", model=asr.get("model", "base"),
                                                 device=asr.get("device", "cpu"), language=asr.get("language"))
                else:
                    transcript = integrations.transcribe(input_path, work / "asr", asr.get("device", "cpu"), asr.get("language"))
            plan = plan_media(input_path, runtime, source["id"], transcript,
                              int(self.settings.get("clips_per_source", 3)),
                              float(self.settings.get("min_clip_seconds", 15)), float(self.settings.get("max_clip_seconds", 55)))
            # Freeze the appearance at planning time. Updating promo never rewrites
            # an interrupted run or previously queued media under its old hash.
            plan["_render_style"] = promo.base_style(runtime)
            plan["_campaign"] = promo.active_campaign(runtime)
            _save(plan_file, plan)
        render_style = plan.get("_render_style", runtime.get("style", {}))
        campaign = plan.get("_campaign")
        self._set(self.current_key, stage="planned")
        clips = []
        for candidate in plan["clips"]:
            if candidate["score"] < float(self.settings.get("min_score", 40)):
                continue
            target = work / "clips" / (candidate["id"] + ".mp4")
            sidecar = target.with_suffix(".json")
            ready = False
            if target.exists() and sidecar.exists():
                existing = json.loads(sidecar.read_text())
                ready = existing.get("output_sha256") == fingerprint(target)
            if not ready:
                if target.exists():
                    # Preserve an interrupted export for diagnostics; never publish it.
                    target.rename(target.with_suffix(".interrupted-" + str(int(self.clock())) + ".mp4"))
                render(input_path, {**plan, "clips": [candidate]}, work / "clips", render_style)
            clip = {"file": str(target), "sha256": fingerprint(target), "score": candidate["score"],
                    "title": candidate["hook"], "source_id": source["id"],
                    "sponsored": bool(render_style.get("sponsored", False))}
            if campaign:
                promo_dir = work / "clips" / "youtube-promo"
                variant = promo_dir / (candidate["id"] + ".mp4")
                variant_sidecar = variant.with_suffix(".json")
                ready = False
                if variant.exists() and variant_sidecar.exists():
                    existing = json.loads(variant_sidecar.read_text())
                    ready = (existing.get("output_sha256") == fingerprint(variant)
                             and existing.get("sponsored") is True)
                if not ready:
                    if variant.exists():
                        variant.rename(variant.with_suffix(".interrupted-" + str(int(self.clock())) + ".mp4"))
                    render(input_path, {**plan, "clips": [candidate]}, promo_dir, promo.youtube_style(render_style, campaign))
                clip["variants"] = {"youtube": {"file": str(variant), "sha256": fingerprint(variant),
                    "sponsored": True, "caption": promo.campaign_caption(campaign)}}
            clips.append(clip)
        result = {"engine": "baseline", "input_sha256": fingerprint(input_path), "clips": clips}
        _save(result_file, result)
        return result

    def _medusa(self, source, input_path, work, runtime):
        python = self._path(self.settings.get("medusa_python", str(ROOT / ".tools/medusa/bin/python")))
        if not python.is_file():
            raise ValueError("AI engine is not installed; run viceclip setup with the AI profile")
        config_file = work / "runtime-config.json"
        _save(config_file, runtime)
        os.chmod(config_file, 0o600)
        output = work / "medusa"
        manifest = output / "manifest.json"
        if not manifest.exists():
            command = [str(python), str(ROOT / "scripts/run_medusa.py"), str(input_path), "--config", str(config_file),
                       "--source-id", source["id"], "--out", str(output), "--clips", str(self.settings.get("clips_per_source", 3))]
            if self.settings.get("asr", {}).get("enabled", False):
                command.append("--captions")
            if self.settings.get("allow_cloud_ai") is True:
                command.append("--allow-cloud")
            if output.exists() and any(output.iterdir()):
                output.rename(work / ("medusa.interrupted-" + str(int(self.clock()))))
            self._run(command, capture_output=True, check=True, timeout=int(self.settings.get("processing_timeout_seconds", 3600)))
        data = json.loads(manifest.read_text())
        clips = []
        for item in data.get("clips", []):
            path = (output / item["file"]).resolve()
            if not path.is_relative_to(output.resolve()) or not path.is_file():
                raise ValueError("AI engine returned an invalid output file")
            clips.append({"file": str(path), "sha256": fingerprint(path),
                          "score": item.get("virality_score") if item.get("virality_score") is not None else min(100, max(0, item.get("score", 0) * 100)),
                          "title": item.get("hook") or "Gameplay highlight", "source_id": source["id"]})
        result = {"engine": "medusa", "input_sha256": fingerprint(input_path), "clips": clips}
        _save(work / "result.json", result)
        return result

    def _schedule(self, queue, account_id):
        row = queue.db.execute("SELECT MAX(scheduled_at) FROM jobs WHERE account=?", (account_id,)).fetchone()
        now = dt.datetime.fromtimestamp(self.clock(), dt.timezone.utc)
        if row[0]:
            last = dt.datetime.fromisoformat(row[0])
            now = max(now, last + dt.timedelta(seconds=max(0, int(self.settings.get("posting_gap_seconds", 3600)))))
        return now.isoformat()

    def _enqueue(self, source, result, runtime, queue):
        wanted = self.settings.get("accounts")
        routed = source.get("account_ids")
        accounts = [a for a in runtime.get("accounts", []) if a.get("enabled", True) and (wanted is None or a["id"] in wanted)
                    and (routed is None or a["id"] in routed)]
        jobs = []
        for clip in result["clips"]:
            if clip.get("score", 0) < float(self.settings.get("min_score", 40)):
                continue
            for account in accounts:
                if "platforms" in source and account["platform"] not in source["platforms"]:
                    continue
                selected = clip.get("variants", {}).get(account["platform"], clip)
                if not Path(selected["file"]).is_file() or fingerprint(selected["file"]) != selected["sha256"]:
                    raise ValueError("Saved platform variant changed; refusing publication")
                caption = account.get("caption", self.settings.get("caption", ""))
                if selected.get("caption"):
                    caption = "\n\n".join(value for value in (caption, selected["caption"]) if value)
                metadata = {"title": clip["title"][:100], "caption": caption,
                            "privacy": account.get("privacy", self.settings.get("privacy", "private")),
                            "sponsored": selected.get("sponsored", clip.get("sponsored", runtime.get("style", {}).get("sponsored", False)))}
                if selected.get("public_url"):
                    metadata["public_url"] = selected["public_url"]
                job = queue.add(selected["file"], source["id"], account["id"], metadata, runtime, self._schedule(queue, account["id"]))
                if self.settings.get("auto_approve", True) is True and job["status"] == "pending":
                    queue.approve(job["id"])
                jobs.append({"id": job["id"], "account": account["id"], "platform": account["platform"]})
        return jobs

    def _routing_signature(self):
        wanted = self.settings.get("accounts")
        settings = {"accounts": [(a["id"], a["platform"], a.get("enabled", True)) for a in self.config.get("accounts", [])
                     if wanted is None or a["id"] in wanted],
                    "sources": [(s["id"], s.get("platforms"), s.get("account_ids")) for s in self.config.get("sources", [])],
                    "min_score": self.settings.get("min_score", 40), "auto_approve": self.settings.get("auto_approve", True)}
        return hashlib.sha256(json.dumps(settings, sort_keys=True).encode()).hexdigest()

    def _capacity(self, queue):
        selected = self.settings.get("accounts")
        accounts = [a for a in self.config.get("accounts", []) if a.get("enabled", True) and (selected is None or a["id"] in selected)]
        if not accounts:
            rendered = self.db.execute("SELECT COUNT(*) FROM media_jobs WHERE stage='done'").fetchone()[0]
            if rendered >= int(self.settings.get("max_preview_sources", 3)):
                return "preview_limit"
        else:
            account_ids = {account["id"] for account in accounts}
            pending = sum(job["account"] in account_ids and job["status"] not in ("uploaded", "published") for job in queue.list())
            if pending >= int(self.settings.get("max_pending_jobs", 21 * len(accounts))):
                return "queue_capacity"
        return None

    def _cleanup_outputs(self, queue):
        days = float(self.settings.get("retention_days", 7))
        if days <= 0:
            return 0
        cleaned = 0
        cutoff = self.clock() - days * 86400
        route = self._routing_signature()
        for row in self.db.execute("SELECT * FROM media_jobs WHERE stage='done' AND updated_at<? AND routing_signature=?", (cutoff, route)).fetchall():
            result = json.loads(row["result"] or '{}')
            files = [asset["file"] for asset in promo.assets(result)]
            statuses = [job[0] for file in files for job in queue.db.execute("SELECT status FROM jobs WHERE file=?", (file,))]
            if not statuses or any(status not in ("uploaded", "published") for status in statuses):
                continue
            work = self._path(self.settings.get("output_dir", "output/autopilot")) / hashlib.sha256(row["key"].encode()).hexdigest()[:24]
            if work.is_dir():
                shutil.rmtree(work)
            self._set(row["key"], stage="archived")
            cleaned += 1
        return cleaned

    def _retry_failed_publications(self, queue):
        """Retry only definite refusals, with persisted backoff and a finite budget."""
        now = self.clock()
        for job in queue.list():
            if job["status"] != "failed":
                continue
            key = "queue_retry:" + str(job["id"])
            saved = self.db.execute("SELECT value FROM worker_meta WHERE key=?", (key,)).fetchone()
            retry = json.loads(saved[0]) if saved else {"attempts": 0, "next_at": now + int(self.settings.get("retry_backoff_seconds", 30))}
            if saved and retry["next_at"] <= now and retry["attempts"] < int(self.settings.get("publish_max_retries", 3)):
                queue.approve(job["id"])
                retry["attempts"] += 1
                retry["next_at"] = now + min(3600, int(self.settings.get("retry_backoff_seconds", 30)) * 2 ** retry["attempts"])
            with self.db:
                self.db.execute("INSERT OR REPLACE INTO worker_meta VALUES(?,?)", (key, json.dumps(retry)))

    def tick(self, *, execute=None):
        self._reload()
        report = {"processed": [], "jobs": [], "discovery": [], "errors": [], "publish": []}
        if self.settings.get("enabled", True) is not True:
            return {**report, "state": "paused"}
        execute = self.settings.get("execute", False) is True if execute is None else bool(execute)
        with self._lock() as acquired:
            if not acquired:
                return {**report, "state": "busy"}
            self._gather(report)
            runtime = self._runtime()
            queue = self.queue_factory(self._path(self.settings.get("publish_db", "data/publish.sqlite3")))
            try:
                route = self._routing_signature()
                rows = self.db.execute("""SELECT * FROM media_jobs WHERE stage!='archived' AND (stage!='done' OR COALESCE(routing_signature,'')!=?) AND retry_at<=?
                    AND failures<? ORDER BY created_at,key""",
                    (route, self.clock(), int(self.settings.get("max_retries", 4)))).fetchall()
                processed_count = 0
                for raw in rows:
                    if self.stop_event and self.stop_event.is_set():
                        break
                    source = json.loads(raw["source_json"])
                    if not self._is_active(source):
                        continue
                    if processed_count >= int(self.settings.get("max_sources_per_tick", 2)):
                        break
                    processed_count += 1
                    self.current_key = raw["key"]
                    work = self._path(self.settings.get("output_dir", "output/autopilot")) / hashlib.sha256(raw["key"].encode()).hexdigest()[:24]
                    work.mkdir(parents=True, exist_ok=True)
                    try:
                        result_file = work / "result.json"
                        capacity = self._capacity(queue)
                        if capacity:
                            report["backpressure"] = capacity
                            break
                        if not result_file.exists() and shutil.disk_usage(work).free < int(self.settings.get("min_free_bytes", 512 * 1024**2)):
                            raise ValueError("Not enough free disk space for another source")
                        managed_input = raw["input_path"]
                        if result_file.exists():
                            result = json.loads(result_file.read_text())
                            digest = raw["input_sha"]
                        else:
                            path = Path(raw["input_path"]) if raw["input_path"] and Path(raw["input_path"]).is_file() else self._input(source, work, runtime)
                            managed_input = str(path)
                            digest = fingerprint(path)
                            if raw["input_sha"] and digest != raw["input_sha"]:
                                raise ValueError("Input changed during a saved run")
                            self._set(raw["key"], input_path=str(path), input_sha=digest, stage="downloaded", error=None)
                            result = self._process(source, path, work, runtime)
                        if result.get("input_sha256") != digest or not promo.valid_result_files(result, fingerprint):
                            raise ValueError("Saved render bytes do not match the completed run")
                        if not result_file.exists():
                            _save(result_file, result)
                        self._set(raw["key"], stage="rendered", result=json.dumps(result))
                        self._cleanup_download(source, managed_input, work)
                        jobs = self._enqueue(source, result, runtime, queue)
                        self._set(raw["key"], stage="done", error=None, retry_at=0, routing_signature=route)
                        report["processed"].append({"source": source["id"], "clips": len(result["clips"]), "jobs": len(jobs)})
                        report["jobs"].extend(jobs)
                    except InterruptedError:
                        self._set(raw["key"], retry_at=0)
                        break
                    except SourceNotReady as exc:
                        retry = self.clock() + max(60, int(self.settings.get("poll_interval_seconds", 300)))
                        self._set(raw["key"], stage="waiting", retry_at=retry, error=None)
                        report["discovery"].append({"source": source["id"], "state": "waiting", "reason": _error(exc)})
                    except Exception as exc:
                        failures = raw["failures"] + 1
                        retry = self.clock() + min(3600, int(self.settings.get("retry_backoff_seconds", 30)) * 2 ** (failures - 1))
                        self._set(raw["key"], stage="failed", failures=failures, retry_at=retry, error=_error(exc))
                        report["errors"].append({"source": source["id"], "error": _error(exc), "retry_at": retry})
                try:
                    if not (self.stop_event and self.stop_event.is_set()):
                        self._retry_failed_publications(queue)
                        report["publish"] = queue.run(runtime, execute=execute)
                        report["archived_sources"] = self._cleanup_outputs(queue)
                except Exception as exc:
                    report["errors"].append({"stage": "publish", "error": _error(exc)})
            finally:
                queue.db.close()
            with self.db:
                self.db.execute("INSERT OR REPLACE INTO worker_meta VALUES('last_tick',?)", (json.dumps({"at": self.clock(), "execute": execute, "report": report}),))
            return {**report, "state": "running", "execute": execute}

    def status(self):
        counts = {row[0]: row[1] for row in self.db.execute("SELECT stage,COUNT(*) FROM media_jobs GROUP BY stage")}
        last = self.db.execute("SELECT value FROM worker_meta WHERE key='last_tick'").fetchone()
        return {"state_path": str(self.state_path), "sources": counts,
                "last_tick": json.loads(last[0]) if last else None,
                "recent_errors": [dict(r) for r in self.db.execute("SELECT source_id,stage,failures,retry_at,error FROM media_jobs WHERE error IS NOT NULL ORDER BY updated_at DESC LIMIT 10")]}

    def run(self, *, execute=None, interval=None, stop_event=None):
        stop = stop_event or threading.Event()
        self.stop_event = stop
        installed = {}
        if threading.current_thread() is threading.main_thread():
            for signum in (signal.SIGINT, signal.SIGTERM):
                installed[signum] = signal.getsignal(signum)
                signal.signal(signum, lambda *_: stop.set())
        try:
            while not stop.is_set():
                try:
                    report = self.tick(execute=execute)
                except Exception as exc:
                    report = {"state": "error", "error": _error(exc)}
                print(json.dumps(report, ensure_ascii=False), flush=True)
                stop.wait(max(1, int(interval or self.settings.get("poll_interval_seconds", 60))))
        finally:
            self.stop_event = None
            for signum, previous in installed.items():
                signal.signal(signum, previous)
