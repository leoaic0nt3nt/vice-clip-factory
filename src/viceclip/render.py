"""Deterministic FFmpeg renderer for full-frame and gameplay portrait layouts."""
import json
import math
import subprocess
from tempfile import TemporaryDirectory
from pathlib import Path
from .facecam import display_aspect, overlay_filters, portrait_center, validate_facecam
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


def write_ass(path, captions, hook, duration, style, hook_end=None):
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
    portrait = style.get("layout") == "portrait"
    compact = style.get("ad_format") in ("compact", "motion")
    if portrait:
        header = header.replace("Caption,Arial,54", "Caption,Inter Tight,76")
        header = header.replace("1,3,1,2,100,100,470,1", "1,7,0,8,115,115,1146,1")
        header = header.replace("Hook,Arial,67", "Hook,Inter Tight,74")
        header = header.replace("1,2,1,8,85,85,285,1", "1,7,0,8,95,95,226,1")
    if style.get("sponsor_position", "bottom") == "top" and not compact:
        header = header.replace("Style: Hook,Arial,67", "Style: Hook,Arial,48").replace("8,85,85,285,1", "8,85,85,230,1")
        header = header.replace("Style: Ad,Arial,34,&H00100A09,&H000000FF,&H00100A09,&H90000000,-1,0,0,0,100,100,0,0,1,0,0,2,80,80,275,1", "Style: Ad,Arial,30,&H00FFFFFF,&H000000FF,&H00100A09,&H90000000,-1,0,0,0,100,100,0,0,1,0,0,8,80,80,510,1")
    else:
        header = header.replace("Style: Ad,Arial,34,&H00100A09", "Style: Ad,Arial,34,&H00FFFFFF")
    events = []
    brand = style.get("brand", "" if portrait else "@leo_ai_content")
    if brand and (not portrait or brand != "@your_channel"):
        events.append(f"Dialogue: 1,0:00:00.00,{_time(duration)},Brand,,0,0,0,,{_ass_text(brand)}")
    if hook:
        hook_end = min(5, duration) if hook_end is None else hook_end
        events.append(f"Dialogue: 2,0:00:00.00,{_time(hook_end)},Hook,,0,0,0,,{_ass_text(hook)}")
    if style.get("sponsor_text") and not compact:
        prefix = "Реклама: " if style.get("sponsored") else "DEMO SLOT: "
        events.append(f"Dialogue: 3,0:00:00.00,{_time(duration)},Ad,,0,0,0,,{_ass_text(prefix+style['sponsor_text'])}")
    for cue in captions:
        events.append(f"Dialogue: 4,{_time(cue['start'])},{_time(cue['end'])},Caption,,0,0,0,,{_ass_text(cue['text'])}")
    Path(path).write_text(header + "\n".join(events) + "\n", encoding="utf-8")


def _clip_style(style, candidate):
    layout = style.get("layout", "fit")
    if layout not in ("fit", "portrait"):
        raise ValueError("style.layout must be fit or portrait")
    mode = style.get("caption_mode", candidate.get("caption_mode", "words" if layout == "portrait" else "legacy"))
    if mode == "auto":
        mode = candidate.get("caption_mode", "words" if layout == "portrait" else "legacy")
    if mode not in ("legacy", "words", "phrase", "meme"):
        raise ValueError("style.caption_mode must be auto, legacy, words, phrase or meme")
    ad_format = style.get("ad_format", "compact" if layout == "portrait" else "legacy")
    if ad_format not in ("compact", "legacy", "motion"):
        raise ValueError("style.ad_format must be compact, legacy or motion")
    return {**style, "layout": layout, "caption_mode": mode, "ad_format": ad_format}


def _crop_center(candidate, axis):
    value = float(candidate.get(f"crop_center_{axis}", .5))
    if not math.isfinite(value) or not 0 <= value <= 1:
        raise ValueError(f"crop_center_{axis} must be within [0, 1]")
    return value


def _layout(style, candidate, aspect):
    facecam = style.get("facecam")
    portrait = style["layout"] == "portrait"
    facecam_rendered = False
    if portrait:
        x, y = _crop_center(candidate, "x"), _crop_center(candidate, "y")
        if facecam:
            adjusted = portrait_center(facecam["rect"], aspect, x, y)
            if adjusted is not None:
                x, y = adjusted
                facecam_rendered = True
        # Crop the source before upscaling. Clamp the requested center when the
        # desired framing would otherwise reach past either edge.
        layout = ("crop=w='min(iw,ih*9/16/sar)':h='min(ih,iw*sar*16/9)'"
                  f":x='clip(iw*{x}-ow/2,0,iw-ow)':y='clip(ih*{y}-oh/2,0,ih-oh)'"
                  ",scale=1080:1920,setsar=1")
    else:
        layout = "scale=1080:1080:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2:color=0x0A1022,setsar=1,fps=30"
    if style.get("sponsor_text") and style["ad_format"] == "legacy":
        position, height = (490, 150) if style.get("sponsor_position") == "top" else (1500, 175)
        layout += f",drawbox=x=60:y={position}:w=960:h={height}:color=0x234DFF:t=fill"
    if not facecam_rendered:
        fps = "fps=30," if portrait else ""
        return [f"[vc]{fps}{layout}[layout]"], False
    promo_default = "bottom" if style["ad_format"] == "legacy" else "top"
    top_promo = bool(style.get("sponsor_text") and style.get("sponsor_position", promo_default) == "top")
    return ["[vc]fps=30,split=2[base][face]", f"[base]{layout}[game]", *overlay_filters(facecam, top_promo)], True


def render(input_path, plan, output_dir, style=None, top=None):
    if fingerprint(input_path) != plan["analysis"]["sha256"]:
        raise ValueError("Input changed since planning; analyze it again")
    style = style or {}
    facecam = validate_facecam(plan.get("facecam", style.get("facecam")))
    style = {**style, "facecam": facecam}
    aspect = None
    if facecam and style.get("layout") == "portrait" and plan["clips"]:
        stream = next(item for item in probe(input_path)["streams"] if item["codec_type"] == "video")
        aspect = display_aspect(stream)
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
        local_style = _clip_style(style, candidate)
        # The nonverbal stretch can contain the stunt itself or its reaction.
        story = candidate.get("evidence", {}).get("kind") in ("gag", "fail", "stunt", "mission")
        ranges = kept_ranges(candidate["start"], candidate["end"], plan["analysis"],
                             plan["transcript"], local_style.get("trim_silence", False) and not story)
        duration = sum(b-a for a,b in ranges)
        if duration <= 0:
            raise ValueError("Clip has no kept source footage")
        captions = remap_captions(plan["transcript"], ranges)
        if local_style["caption_mode"] == "legacy":
            display_captions = captions
        else:
            from .raster import caption_cues
            display_captions = caption_cues(captions, local_style["caption_mode"])
        hook = candidate["hook"]
        default_hook_duration = 3.2 if local_style["layout"] == "portrait" else 5
        if local_style["caption_mode"] == "meme":
            default_hook_duration = duration
        hook_limit = float(local_style.get("hook_duration", default_hook_duration))
        if not math.isfinite(hook_limit) or hook_limit < 0:
            raise ValueError("style.hook_duration must be a nonnegative number")
        hook_end = min(duration, hook_limit) if hook else 0
        ass = output_dir / f"{cid}.ass"
        write_ass(ass, display_captions, hook, duration, local_style, hook_end)
        has_audio = plan["analysis"].get("has_audio", False)
        # Seek once to the chosen passage instead of decoding the whole stream
        # for every short clip. Caption/sidecar times stay in source coordinates.
        origin = ranges[0][0]
        source_span = ranges[-1][1] - origin
        filters, streams = [], []
        for i, (a,b) in enumerate(ranges):
            filters.append(f"[0:v]trim=start={a-origin}:end={b-origin},setpts=PTS-STARTPTS[v{i}]")
            streams.append(f"[v{i}]")
            if has_audio:
                filters.append(f"[0:a]atrim=start={a-origin}:end={b-origin},asetpts=PTS-STARTPTS[a{i}]")
                streams.append(f"[a{i}]")
        if has_audio:
            filters.append("".join(streams)+f"concat=n={len(ranges)}:v=1:a=1[vc][ac]")
        else:
            filters.append("".join(streams)+f"concat=n={len(ranges)}:v=1:a=0[vc]")
        layout_filters, facecam_rendered = _layout(local_style, candidate, aspect)
        filters.extend(layout_filters)
        command = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-filter_complex_threads", "1",
                   "-ss", str(origin), "-t", str(source_span), "-i", str(Path(input_path).resolve())]
        use_ass = (local_style["layout"] == "fit" and local_style["ad_format"] == "legacy"
                   and local_style["caption_mode"] == "legacy" and has_filter("ass"))
        with TemporaryDirectory(prefix=".overlay-", dir=output_dir) as temporary:
            if use_ass:
                # Relative subtitle path avoids filtergraph escaping of user paths.
                filters.append(f"[layout]ass=filename={cid}.ass[vout]")
            else:
                from .raster import overlay_timeline
                manifest, overlay_y = overlay_timeline(Path(temporary), display_captions, hook, duration, local_style, hook_end)
                command += ["-f", "concat", "-safe", "0", "-i", str(manifest)]
                filters.append(f"[layout][1:v]overlay=y={overlay_y}:eof_action=repeat[vout]")
            command += ["-filter_complex", ";".join(filters), "-map", "[vout]"]
            if has_audio:
                command += ["-map", "[ac]", "-c:a", "aac", "-b:a", "128k", "-ar", "48000"]
            command += ["-c:v", "libx264", "-threads", "4", "-preset", local_style.get("preset", "veryfast"), "-crf", "21", "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-n", target.name]
            subprocess.run(command, cwd=output_dir, check=True)
        info = probe(target)
        metadata = {"source_id": plan["source_id"], "input_sha256": plan["analysis"]["sha256"], "output_sha256": fingerprint(target),
                    "kept_source_ranges": ranges, "duration": float(info["format"]["duration"]), "captions": captions,
                    "candidate": candidate, "sponsored": bool(local_style.get("sponsored")), "originality_reviewed": False,
                    "display_captions": display_captions, "layout": local_style["layout"],
                    "caption_mode": local_style["caption_mode"], "hook_duration": hook_end,
                    "ad_format": local_style["ad_format"] if local_style.get("sponsor_text") else None,
                    "note": "Human editorial review required before queue approval."}
        if facecam:
            metadata["facecam"] = {**facecam, "rendered": facecam_rendered}
        target.with_suffix(".json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        outputs.append(str(target))
    return outputs
