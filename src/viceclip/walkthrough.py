"""Scan every part of a recorded playthrough, then render the strongest episodes."""
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
from pathlib import Path
import resource
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
import time

from . import promo
from .editorial import check_editorial_ready, EditorialError, EditorialUnavailable
from .media import fingerprint, probe
from .render import render
from .speech import transcribe_file


def windows(duration, seconds, overlap):
    start = 0
    while start < duration:
        end = min(start + seconds, duration)
        # Never strand the last seconds in an interval too short to edit.
        if duration - end < overlap:
            end = duration
        yield start, end
        if end == duration:
            break
        start = end - overlap


def rank_windows(scanned, limit):
    pool = []
    for window in scanned:
        for candidate in window["plan"]["clips"]:
            pool.append((window, candidate))
    pool.sort(key=lambda item: (-item[1]["score"], item[0]["start"] + item[1]["start"]))
    picked = []
    kept_intervals = []
    for window, candidate in pool:
        start = window["start"] + candidate["start"]
        end = window["start"] + candidate["end"]
        payoff_at = window["start"] + candidate["evidence"]["payoff_at"]
        ranges = source_ranges(window, candidate)
        if any(left < prior_right and right > prior_left for left, right in ranges
               for prior_left, prior_right in kept_intervals):
            continue
        if any(abs(payoff_at - payoff) < 15 for _, _, payoff, _, _ in picked):
            continue
        picked.append((start, end, payoff_at, window, candidate))
        kept_intervals.extend(ranges)
        if len(picked) == limit:
            break
    return picked


def source_ranges(window, candidate):
    local = candidate.get("story_ranges", [(candidate["start"], candidate["end"])])
    return [[round(window["start"] + left, 3), round(window["start"] + right, 3)] for left, right in local]


def _slice(source, destination, start, end):
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-ss", str(start), "-i", str(source),
                    "-t", str(end - start), "-map", "0:v:0", "-map", "0:a:0?", "-c", "copy",
                    "-movflags", "+faststart", "-n", str(destination)], check=True)


def _save(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def _prepare(source, directory, config, number, start, end):
    from .cli import plan_media

    settings = config["autopilot"]
    settings_asr = settings.get("asr", {})
    temporary_dir = directory / "windows" / f"{number:03}"
    temporary_dir.mkdir(parents=True, exist_ok=True)
    stored_path = temporary_dir / "result.json"
    if stored_path.exists():
        return json.loads(stored_path.read_text(encoding="utf-8"))
    started = time.monotonic()
    if shutil.disk_usage(directory).free < 128 * 1024**2:
        raise ValueError("Нужно хотя бы 128 МиБ свободного места для следующего окна")
    with TemporaryDirectory(prefix="source-", dir=temporary_dir) as folder:
        path = Path(folder) / "window.mp4"
        _slice(source, path, start, end)
        measured_duration = float(probe(path)["format"]["duration"])
        if measured_duration + .5 < end - start:
            raise ValueError(f"Окно {number} короче исходного диапазона")
        transcript = transcribe_file(path, Path(folder) / "asr", model=settings_asr.get("model", "small"),
                                     device=settings_asr.get("device", "cpu"),
                                     language=settings_asr.get("language"))
        attempts = []
        for attempt in range(2):
            usage = {}
            attempts.append(usage)
            try:
                plan = plan_media(path, config, "walkthrough", transcript, 2, 20, 45,
                                  editorial_cache_dir=temporary_dir / "editorial", telemetry=usage)
                break
            except EditorialUnavailable:
                raise
            except EditorialError:
                if attempt:
                    _save(temporary_dir / "failed-usage.json", attempts)
                    raise
        result = {"number": number, "start": start, "end": end,
                  "bytes": path.stat().st_size, "seconds": round(time.monotonic() - started, 2),
                  "usage": attempts, "plan": plan}
    _save(stored_path, result)
    return result


def scan(source, config, output_dir, *, top=3, window_seconds=420, overlap=50, workers=2):
    source = Path(source).expanduser().resolve(strict=True)
    if not 180 <= window_seconds <= 450 or not 40 <= overlap < window_seconds / 2:
        raise ValueError("Окно должно быть 180–450 с, перекрытие от 40 с до половины окна")
    if not 1 <= workers <= 3 or not 1 <= top <= 3:
        raise ValueError("Для одного прогона выберите 1–3 процесса и 1–3 клипа")
    directory = Path(output_dir).expanduser().resolve()
    directory.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    original_bytes = source.stat().st_size
    duration = float(probe(source)["format"]["duration"])
    if duration < 20:
        raise ValueError("Для монтажа нужно видео от 20 секунд")
    config = {**config, "source_policy": {"mode": "configured_sources", "user_responsible": True},
              "sources": [{"id": "walkthrough"}]}
    autopilot = config.get("autopilot", {})
    config["autopilot"] = {**autopilot, "editorial": {**autopilot.get("editorial", {}),
                            "provider": "codex", "story_mode": True, "mission_mode": True}}
    config["style"] = {**config.get("style", {}), "layout": "portrait"}
    receipt = {"input_sha256": fingerprint(source), "duration": duration, "source_bytes": original_bytes,
               "window_seconds": window_seconds, "overlap_seconds": overlap, "top": top,
               "editorial": config["autopilot"]["editorial"], "asr": config["autopilot"].get("asr"),
               "style": config["style"], "campaign": promo.active_campaign(config)}
    marker = directory / "run.json"
    if marker.exists():
        if json.loads(marker.read_text(encoding="utf-8")) != receipt:
            raise ValueError("Папка привязана к другому исходнику или настройкам")
    else:
        if (directory / "windows").exists() or (directory / "clips").exists():
            raise ValueError("Выберите пустую папку результатов")
        _save(marker, receipt)
    slots = list(enumerate(windows(duration, window_seconds, overlap), 1))
    if any(not (directory / "windows" / f"{number:03}" / "result.json").exists() for number, _ in slots):
        check_editorial_ready()
    longest = max(end - start for _, (start, end) in slots)
    required_free = 192 * 1024**2 + int(original_bytes / duration * longest * min(workers, len(slots)) * 1.5)
    if shutil.disk_usage(directory).free < required_free:
        raise ValueError(f"Для этого прогона нужно около {required_free / 1024**2:.0f} МиБ свободного места в папке результатов")
    scanned, errors = [], []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        tasks = {executor.submit(_prepare, source, directory, config, number, start, end): number
                 for number, (start, end) in slots}
        for task in as_completed(tasks):
            try:
                window = task.result()
            except (ValueError, OSError, RuntimeError, subprocess.CalledProcessError) as error:
                errors.append({"window": tasks[task], "reason": str(error)})
                print(f"Окно {tasks[task]}/{len(slots)}: {error}", file=sys.stderr, flush=True)
                if isinstance(error, EditorialUnavailable):
                    for pending in tasks:
                        pending.cancel()
                    raise
            else:
                scanned.append(window)
                print(f"Окно {window['number']}/{len(slots)}: {len(window['plan']['clips'])} кандидат(а)",
                      file=sys.stderr, flush=True)
    if errors:
        _save(directory / "errors.json", errors)
        raise ValueError(f"Не удалось проверить {len(errors)} окон. Повторите команду для оставшихся диапазонов")
    (directory / "errors.json").unlink(missing_ok=True)
    campaign = receipt["campaign"]
    base = promo.base_style(config)
    exports = []
    for index, (start, end, payoff, window, candidate) in enumerate(rank_windows(scanned, top), 1):
        clip_id = f"story-{index:02}"
        candidate = {**candidate, "id": clip_id}
        plan = {**window["plan"], "clips": [candidate]}
        versions = [("clean", base)]
        if campaign:
            versions.append(("youtube-promo", promo.youtube_style(base, campaign)))
        files = {}
        with TemporaryDirectory(prefix="render-source-", dir=directory) as folder:
            local = Path(folder) / "window.mp4"
            _slice(source, local, window["start"], window["end"])
            for variant, style in versions:
                dest = directory / "clips" / variant
                target = dest / f"{clip_id}.mp4"
                previous = target.with_suffix(".json")
                if target.is_file() and previous.is_file():
                    saved = json.loads(previous.read_text(encoding="utf-8"))
                    if (saved.get("output_sha256") != fingerprint(target)
                            or saved.get("candidate") != json.loads(json.dumps(candidate))):
                        raise ValueError(f"Проверьте ранее сохранённый клип {target.name}")
                else:
                    if target.exists():
                        target.rename(target.with_suffix(f".partial-{time.time_ns()}.mp4"))
                    render(local, plan, dest, style)
                files[variant] = str(target)
        ranges = source_ranges(window, candidate)
        exports.append({"source_start": start, "source_end": end, "source_payoff": payoff,
                        "source_ranges": ranges, "duration": round(sum(b - a for a, b in ranges), 3),
                        "window": window["number"], "window_start_seconds": window["start"],
                        "candidate": candidate, "files": files})
    usage = {}
    for window in scanned:
        for attempt in window["usage"]:
            for key, number in attempt.items():
                usage[key] = usage.get(key, 0) + number
    self_stats = resource.getrusage(resource.RUSAGE_SELF)
    child_stats = resource.getrusage(resource.RUSAGE_CHILDREN)
    rss_unit = 1 if sys.platform == "darwin" else 1024
    result = {**receipt, "source_file": source.name, "windows": len(slots), "windows_completed": len(scanned),
              "candidates": sum(len(window["plan"]["clips"]) for window in scanned),
              "max_window_bytes": max(window["bytes"] for window in scanned),
              "output_bytes": sum(path.stat().st_size for path in directory.rglob("*") if path.is_file()),
              "workers": workers, "usage": usage, "processing_seconds": round(time.monotonic() - started, 2),
              "process_peak_ram_bytes": self_stats.ru_maxrss * rss_unit,
              "peak_child_ram_bytes": child_stats.ru_maxrss * rss_unit,
              "cpu_seconds": round(self_stats.ru_utime + self_stats.ru_stime +
                                   child_stats.ru_utime + child_stats.ru_stime, 2),
              "clips": exports}
    _save(directory / "report.json", result)
    return result
