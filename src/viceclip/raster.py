"""Draw timed text and promotion layers for one FFmpeg overlay stream."""
import math
import re
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .captions import CAPTION_Y, ENTRANCE_SECONDS, IVORY, caption_cues, caption_font, uppercase_caption

WIDTH, HEIGHT, FPS = 1080, 1920, 30
ACCENT = "#FFB647"
BUNDLED_FONT = Path(__file__).resolve().parents[2] / "assets" / "fonts" / "InterTight-Bold.ttf"


@lru_cache(maxsize=96)
def font(size, configured=None):
    paths = [configured, BUNDLED_FONT, "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
             "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf"]
    for path in paths:
        if path:
            try:
                return ImageFont.truetype(str(path), size)
            except OSError:
                pass
    raise ValueError("Install a Cyrillic-capable font or set style.font_path")


def _lines(text, face, width):
    start, end = 0, 0
    for token in re.finditer(r"\S+", text):
        if end and face.getlength(text[start:token.end()]) > width:
            yield start, text[start:end]
            start = token.start()
        end = token.end()
    if end:
        yield start, text[start:end]


def _fit(text, size, width, max_lines, configured, max_height, face_loader):
    while True:
        face = face_loader(size, configured)
        lines = list(_lines(text, face, width))
        if size == 1 or (len(lines) <= max_lines and (size + 7) * len(lines) <= max_height
                         and all(face.getlength(line) <= width for _, line in lines)):
            return face, lines
        size = max(1, size - 2)


def _text(text, *, size, width, max_lines, configured, stroke=2, color="white",
          active=None, panel=False, max_height=HEIGHT, face_loader=font, accent=ACCENT, shadow=False):
    padding = 22 if panel else stroke + 3
    face, lines = _fit(text, size, width, max_lines, configured, max_height - 2 * padding, face_loader)
    line_height = face.size + 7
    content_width = max(face.getlength(line) for _, line in lines)
    image = Image.new("RGBA", (math.ceil(content_width) + 2 * padding, line_height * len(lines) + 2 * padding))
    draw = ImageDraw.Draw(image)
    if panel:
        draw.rounded_rectangle((0, 0, image.width - 1, image.height - 1), 25, fill=(12, 13, 19, 225))
    for index, (offset, line) in enumerate(lines):
        x = (image.width - face.getlength(line)) / 2
        y = padding + index * line_height + round(face.size * .80)
        if shadow:
            draw.text((x, y + 3), line, anchor="ls", font=face, fill="#090B11", stroke_width=stroke,
                      stroke_fill="#090B11")
        draw.text((x, y), line, anchor="ls", font=face, fill=color, stroke_width=stroke, stroke_fill="#101016")
        if active and active[0] < offset + len(line) and active[1] > offset:
            left, right = max(0, active[0] - offset), min(len(line), active[1] - offset)
            draw.text((x + face.getlength(line[:left]), y), line[left:right], anchor="ls", font=face,
                      fill=accent, stroke_width=stroke, stroke_fill="#101016")
    return image


def _compact_badge(text, label, configured):
    image = Image.new("RGBA", (386, 138))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((0, 0, image.width - 1, image.height - 1), 21,
                           fill=(17, 19, 28, 241), outline=(255, 255, 255, 75), width=2)
    draw.text((193, 16), label, anchor="mt", font=font(23, configured), fill=ACCENT)
    body = _text(text, size=34, width=342, max_lines=3, configured=configured, stroke=0, max_height=86)
    image.alpha_composite(body, ((image.width - body.width) // 2, 47 + (86 - body.height) // 2))
    return image


def _legacy_cues(captions):
    for caption in captions:
        words = caption["text"].split()
        chunks = []
        for word in words:
            if not chunks or len(" ".join(chunks[-1] + [word])) > 65:
                chunks.append([word])
            else:
                chunks[-1].append(word)
        cursor = 0
        segment_len = caption["end"] - caption["start"]
        for chunk in chunks:
            start = caption["start"] + segment_len * cursor / len(words)
            cursor += len(chunk)
            end = caption["start"] + segment_len * cursor / len(words)
            yield {"start": start, "end": end, "text": " ".join(chunk)}


def overlay_timeline(directory, captions, hook, duration, style, hook_end):
    """Encode sparse RGBA frames with concat timestamps aligned to output frames."""
    configured = style.get("font_path")
    portrait = style["layout"] == "portrait"
    compact = style["ad_format"] == "compact"
    meme = style["caption_mode"] == "meme"
    modern_captions = portrait and style.get("caption_style", "gaming") != "classic"
    caption_path = style.get("caption_font_path") or configured
    sponsor = " ".join((style.get("sponsor_text") or "").split())
    motion_promo = style["ad_format"] == "motion"
    top_sponsor = style.get("sponsor_position") == "top" and not (compact or motion_promo)
    brand = style.get("brand", "" if portrait else "@leo_ai_content")
    if portrait and brand == "@your_channel":
        brand = ""
    hook = " ".join(hook.split())
    ad_start = min(hook_end, duration * .54) if hook else 0
    layers = {name: [] for name in ("brand", "hook", "promo", "caption", "accent")}

    def add(layer, start, end, image, y):
        if end > start:
            layers[layer].append((start, end, image, y))

    def add_text(layer, text, start, end, *, y, size, width=900, max_lines=4, **options):
        if text.strip():
            image = _text(text, size=size, width=width, max_lines=max_lines, configured=configured, **options)
            add(layer, start, end, image, y)

    if brand:
        add_text("brand", brand, 0, duration, y=129 if portrait else 140, size=29 if portrait else 32,
                 max_lines=1, color="white" if portrait else "#5274FF")
    if modern_captions:
        if hook:
            image = _text(hook.upper(), size=81, width=805, max_lines=2, configured=caption_path,
                          stroke=4, panel=meme, max_height=209, color=IVORY, face_loader=caption_font, shadow=True)
            add("hook", 0, hook_end, image, 226)
    elif portrait:
        add_text("hook", hook, 0, hook_end, y=226, size=74, width=840, max_lines=2, stroke=7, panel=meme)
    else:
        add_text("hook", hook, 0, hook_end, y=230 if top_sponsor else 285, size=48 if top_sponsor else 65)
    if sponsor and motion_promo:
        from .creative import layers as promo_layers
        for start, end, image, y in promo_layers(style, duration, hook_end, font, _text):
            add("promo", start, end, image, y)
    elif sponsor and compact:
        # Put the badge below any opening text that remains during a short clip.
        overlay_hook = bool(hook and ad_start < hook_end)
        if style.get("sponsor_position", "top") == "bottom":
            base_y = 1520 if portrait or style["caption_mode"] != "legacy" else 1580
        else:
            base_y = (447 if portrait else 612) if overlay_hook else 178
        label = "РЕКЛАМА" if style.get("sponsored") else "ДЕМО"
        start = ad_start
        phase_length = max(.25, min(2.6, (duration - start) / 2))
        phases = [_compact_badge(message, label, configured)
                  for message in (sponsor, "Ссылка в профиле")]
        for index in range(math.ceil((duration - start) / phase_length)):
            stop = min(duration, start + phase_length)
            add("promo", start, stop, phases[index % 2], base_y)
            start = stop
    elif sponsor:
        prefix = "Реклама: " if style.get("sponsored") else "DEMO SLOT: "
        add_text("promo", prefix + sponsor, 0, duration, y=510 if top_sponsor else 1550,
                 size=30 if top_sponsor else 34, width=870)
    if style["caption_mode"] != "legacy" and modern_captions:
        accent_color = style.get("caption_accent", ACCENT)

        def add_caption(layer, cue, start, end, active=None):
            if start >= end or not cue["text"].strip():
                return
            text, span = uppercase_caption(cue["text"], active)
            image = _text(text, size=81, width=775, max_lines=2, configured=caption_path,
                          stroke=4, active=span, color=IVORY, face_loader=caption_font,
                          accent=accent_color, shadow=True)
            settle = min(end, cue["start"] + ENTRANCE_SECONDS)
            if start < settle:
                arrived = image.resize((round(image.width * .96), round(image.height * .96)), Image.Resampling.LANCZOS)
                add(layer, start, settle, arrived, CAPTION_Y + 5)
                start = settle
            add(layer, start, end, image, CAPTION_Y)

        for cue in captions:
            add_caption("caption", cue, cue["start"], cue["end"])
            for start, end, active in cue.get("accents", []):
                add_caption("accent", cue, start, end, active)
    elif style["caption_mode"] != "legacy":
        options = {"y": 1146, "size": 76, "width": 825, "max_lines": 2, "stroke": 7}
        for cue in captions:
            add_text("caption", cue["text"], cue["start"], cue["end"], **options)
            for start, end, active in cue.get("accents", []):
                add_text("accent", cue["text"], start, end, active=active, **options)
    else:
        for cue in _legacy_cues(captions):
            add_text("caption", cue["text"], cue["start"], cue["end"], y=1325, size=52)

    duration_frames = math.ceil(duration * FPS)
    # The frame at timestamp t displays events that started on or before t.
    frames = {0, duration_frames}
    ordered = []
    for name, events in layers.items():
        for start, end, image, y in events:
            a = max(0, math.ceil(start * FPS - 1e-8))
            b = min(duration_frames, math.ceil(end * FPS - 1e-8))
            if a < b:
                frames.update((a, b))
                ordered.append((name, a, b, image, y))
    limits = sorted(frames)
    # Limit transparent pixels transferred for the entire sequence. Legacy text
    # uses different safe areas, so derive the region from the actual layers.
    top = (min(y for _, _, _, _, y in ordered) // 2) * 2 if ordered else 0
    bottom = max((y + image.height for _, _, _, image, y in ordered), default=2)
    height = min(HEIGHT - top, math.ceil((bottom - top) / 2) * 2)
    names, manifest = {}, ["ffconcat version 1.0"]
    # Coalesce adjacent identical screens. The final repeated frame lets concat
    # honor the last duration even on builds that drop it without a following file.
    screens = []
    for start, end in zip(limits, limits[1:]):
        active = {name: index for index, (name, a, b, _, _) in enumerate(ordered) if a <= start < b}
        # The accented plate already includes all the neutral text.
        if "accent" in active:
            active["caption"] = active.pop("accent")
        key = tuple(active.values())
        if screens and key == screens[-1][0]:
            screens[-1] = (key, screens[-1][1] + end - start)
        else:
            screens.append((key, end - start))
    for key, frames_count in screens:
        if key not in names:
            names[key] = f"overlay-{len(names):04d}.png"
            frame = Image.new("RGBA", (WIDTH, height))
            for index in key:
                _, _, _, image, y = ordered[index]
                frame.alpha_composite(image, ((WIDTH - image.width) // 2, y - top))
            frame.save(directory / names[key], compress_level=1)
        filename = names[key]
        manifest += [f"file '{filename}'", f"option framerate {FPS}", f"duration {frames_count / FPS:.9f}"]
    manifest += [f"file '{filename}'", f"option framerate {FPS}"]
    path = directory / "overlay.ffconcat"
    path.write_text("\n".join(manifest) + "\n", encoding="utf-8")
    return path, top
