"""FFmpeg audio energy and one-frame-per-second motion, not semantic vision."""
import array
import json
import math
import shutil
import subprocess
from pathlib import Path


def probe(path):
    result = subprocess.run(["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)], check=True, capture_output=True, text=True)
    data = json.loads(result.stdout)
    if not any(s["codec_type"] == "video" for s in data["streams"]):
        raise ValueError("Input has no video stream")
    return data


def fingerprint(path):
    import hashlib
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _chunks(command, size):
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    try:
        while True:
            chunk = process.stdout.read(size)
            if not chunk:
                break
            yield chunk
        if process.wait() != 0:
            raise RuntimeError("FFmpeg analysis failed")
    finally:
        process.stdout.close()
        if process.poll() is None:
            process.kill()
            process.wait()


def analyze(path):
    info = probe(path)
    duration = float(info["format"]["duration"])
    has_audio = any(s["codec_type"] == "audio" for s in info["streams"])
    energy = []
    if has_audio:
        cmd = ["ffmpeg", "-v", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", "8000", "-f", "s16le", "-"]
        for chunk in _chunks(cmd, 16000):
            samples = array.array("h", chunk[:len(chunk) // 2 * 2])
            rms = math.sqrt(sum(x*x for x in samples) / max(1, len(samples))) / 32768
            energy.append(round(20 * math.log10(max(rms, 1e-7)), 2))
    frames = []
    previous = None
    cmd = ["ffmpeg", "-v", "error", "-i", str(path), "-an", "-vf", "fps=1,scale=64:36,format=gray", "-f", "rawvideo", "-"]
    for frame in _chunks(cmd, 64 * 36):
        if len(frame) != 64 * 36:
            continue
        delta = sum(abs(a-b) for a, b in zip(frame, previous)) / len(frame) if previous else 0
        frames.append(round(delta, 2))
        previous = frame
    return {"input": str(Path(path).resolve()), "sha256": fingerprint(path), "duration": duration,
            "has_audio": has_audio, "audio_dbfs": energy, "motion": frames,
            "method": "mono 8kHz RMS per second + 64x36 frame difference at 1fps; no semantic AI"}


def read_transcript(path, duration):
    if not path:
        return []
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    segments = data.get("segments", data) if isinstance(data, dict) else data
    for s in segments:
        if not isinstance(s.get("text"), str) or not (0 <= s["start"] < s["end"] <= duration + .1):
            raise ValueError("Transcript segments need text and valid absolute start/end seconds")
    return sorted(segments, key=lambda s: s["start"])


def has_filter(name):
    import re
    result = subprocess.run(["ffmpeg", "-hide_banner", "-filters"],capture_output=True,text=True,check=True)
    return bool(re.search(r"\s"+re.escape(name)+r"\s+",result.stdout))


def doctor():
    data = {name: shutil.which(name) for name in ("ffmpeg", "ffprobe", "yt-dlp", "whisperx", "scenedetect", "auto-editor")}
    data["ffmpeg_filters"] = {name: has_filter(name) if data["ffmpeg"] else False for name in ("ass", "drawtext", "overlay")}
    try:
        import PIL
        data["pillow"] = PIL.__version__
    except ImportError:
        data["pillow"] = None
    return data
