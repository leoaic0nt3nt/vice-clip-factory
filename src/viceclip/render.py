"""Deterministic FFmpeg portrait renderer; retains the full gameplay frame/HUD."""
import json
import subprocess
from pathlib import Path
from .media import fingerprint, probe, has_filter
from .scoring import kept_ranges, remap_captions


def _ass_text(text):
    return str(text).replace("\\", "\\\\").replace("{", "(").replace("}", ")").replace("\n", "\\N")


def _time(seconds):
    ticks = round(seconds * 100)
    hours, rest = divmod(ticks, 360000)
    minutes, rest = divmod(rest, 6000)
    sec, centi = divmod(rest, 100)
    return f"{hours}:{minutes:02d}:{sec:02d}.{centi:02d}"


def write_ass(path, captions, hook, duration, style):
    header = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 0
[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Caption,Arial,54,&H00FFFFFF,&H000000FF,&H00100A09,&H90000000,-1,0,0,0,100,100,0,0,1,3,1,2,100,100,470,1
Style: Hook,Arial,67,&H00FFFFFF,&H000000FF,&H00100A09,&H90000000,-1,0,0,0,100,100,0,0,1,2,1,8,85,85,285,1
Style: Brand,Arial,32,&H00FF4D23,&H000000FF,&H00100A09,&H90000000,-1,0,0,0,100,100,1,0,1,1,0,8,70,70,140,1
Style: Ad,Arial,34,&H00100A09,&H000000FF,&H00100A09,&H90000000,-1,0,0,0,100,100,0,0,1,0,0,2,80,80,275,1
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    if style.get("sponsor_position", "bottom") == "top":
        header = header.replace("Style: Hook,Arial,67", "Style: Hook,Arial,60").replace("8,85,85,285,1", "8,85,85,250,1")
        header = header.replace("Style: Ad,Arial,34,&H00100A09,&H000000FF,&H00100A09,&H90000000,-1,0,0,0,100,100,0,0,1,0,0,2,80,80,275,1", "Style: Ad,Arial,34,&H00FFFFFF,&H000000FF,&H00100A09,&H90000000,-1,0,0,0,100,100,0,0,1,0,0,8,80,80,540,1")
    else:
        header = header.replace("Style: Ad,Arial,34,&H00100A09", "Style: Ad,Arial,34,&H00FFFFFF")
    events = [f"Dialogue: 1,0:00:00.00,{_time(duration)},Brand,,0,0,0,,{_ass_text(style.get('brand', '@leo_ai_content'))}"]
    events.append(f"Dialogue: 2,0:00:00.00,{_time(min(5,duration))},Hook,,0,0,0,,{_ass_text(hook)}")
    if style.get("sponsor_text"):
        prefix = "Реклама / Paid promotion: " if style.get("sponsored") else "DEMO SLOT: "
        events.append(f"Dialogue: 3,0:00:00.00,{_time(duration)},Ad,,0,0,0,,{_ass_text(prefix+style['sponsor_text'])}")
    for s in captions:
        events.append(f"Dialogue: 4,{_time(s['start'])},{_time(s['end'])},Caption,,0,0,0,,{_ass_text(s['text'])}")
    Path(path).write_text(header + "\n".join(events) + "\n", encoding="utf-8")


def render(input_path, plan, output_dir, style=None, top=None):
    if fingerprint(input_path) != plan["analysis"]["sha256"]:
        raise ValueError("Input changed since planning; analyze it again")
    style = style or {}
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    outputs = []
    for candidate in plan["clips"][:top]:
        cid = candidate["id"]
        if not cid.replace("-", "").isalnum():
            raise ValueError("Invalid clip id")
        target = output_dir / f"{cid}.mp4"
        if target.exists():
            raise ValueError(f"Output already exists: {target}; use another directory")
        ranges = kept_ranges(candidate["start"], candidate["end"], plan["analysis"], plan["transcript"], style.get("trim_silence", False))
        duration = sum(b-a for a,b in ranges)
        captions = remap_captions(plan["transcript"], ranges)
        ass = output_dir / f"{cid}.ass"
        write_ass(ass, captions, candidate["hook"], duration, style)
        has_audio = plan["analysis"].get("has_audio", False)
        filters, streams = [], []
        for i, (a,b) in enumerate(ranges):
            filters.append(f"[0:v]trim=start={a}:end={b},setpts=PTS-STARTPTS[v{i}]")
            streams.append(f"[v{i}]")
            if has_audio:
                filters.append(f"[0:a]atrim=start={a}:end={b},asetpts=PTS-STARTPTS[a{i}]")
                streams.append(f"[a{i}]")
        if has_audio:
            filters.append("".join(streams)+f"concat=n={len(ranges)}:v=1:a=1[vc][ac]")
        else:
            filters.append("".join(streams)+f"concat=n={len(ranges)}:v=1:a=0[vc]")
        # Full landscape picture fits inside portrait; never discard a crosshair/HUD.
        layout = "[vc]scale=1080:1080:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2:color=0x0A1022,setsar=1,fps=30"
        if style.get("sponsor_text"):
            position = 500 if style.get("sponsor_position", "bottom") == "top" else 1500
            layout += f",drawbox=x=60:y={position}:w=960:h=175:color=0x234DFF:t=fill"
        filters.append(layout + "[layout]")
        command = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-filter_complex_threads", "1", "-i", str(Path(input_path).resolve())]
        if has_filter("ass"):
            # Relative subtitle path avoids filtergraph escaping of user paths.
            filters.append(f"[layout]ass=filename={cid}.ass[vout]")
        else:
            from .raster import plates
            overlays = plates(output_dir,cid,captions,candidate["hook"],duration,style)
            previous = "layout"
            for index,(path,start,end) in enumerate(overlays,1):
                command += ["-loop", "1", "-i", path.name]
                next_label = "vout" if index == len(overlays) else f"plate{index}"
                filters.append(f"[{previous}][{index}:v]overlay=enable='gte(t,{start})*lt(t,{end})':shortest=1[{next_label}]")
                previous = next_label
        command += ["-filter_complex", ";".join(filters), "-map", "[vout]"]
        if has_audio:
            command += ["-map", "[ac]", "-c:a", "aac", "-b:a", "128k", "-ar", "48000"]
        command += ["-c:v", "libx264", "-threads", "4", "-preset", style.get("preset", "veryfast"), "-crf", "21", "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-n", target.name]
        subprocess.run(command, cwd=output_dir, check=True)
        info = probe(target)
        metadata = {"source_id": plan["source_id"], "input_sha256": plan["analysis"]["sha256"], "output_sha256": fingerprint(target),
                    "kept_source_ranges": ranges, "duration": float(info["format"]["duration"]), "captions": captions,
                    "candidate": candidate, "sponsored": bool(style.get("sponsored")), "originality_reviewed": False,
                    "note": "Rule-based selection. Human editorial review required before queue approval."}
        target.with_suffix(".json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        outputs.append(str(target))
    return outputs
