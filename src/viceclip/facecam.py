"""Detect and frame a webcam panel embedded in a stream recording."""
from fractions import Fraction
from statistics import median


FACECAM_OBSERVATIONS_SCHEMA = {
    "type": "array",
    "items": {
        "type": "object",
        "additionalProperties": False,
        "required": ["at", "rect"],
        "properties": {
            "at": {"type": "number"},
            "rect": {"type": "array", "items": {"type": "number"}},
        },
    },
}

FACECAM_PROMPT = """
For each clip, inspect its sampled frames for a fixed, bounded rectangular webcam
inset showing a real person in a separate live camera feed over gameplay. Look for
real facial movement or changes in pose within the inset while the game changes
independently. HUD portraits, cutscenes, character faces, menus, chat, logos and
pictures do not count. Use an empty facecam_observations array if uncertain, if the
inset is covered or missing in any observed part of the clip, or if it changes
position or size. Chroma-key cutouts without a distinct panel also do not count.
If confirmed, give three to six independent observations across the clip, spanning
its beginning, middle and end. For each, use the exact labeled source timestamp
as at and measure rect [left, top, width, height] of the whole camera picture,
excluding nearby chat, HUD and any decorative frame. Normalize coordinates to
0–1 within that individual video thumbnail, ignoring timestamp labels and padding.
Measure the panel on each frame. Do not guess coordinates or reuse another clip's.
"""


def _overlap(a, b):
    x, y, w, h = a
    bx, by, bw, bh = b
    width = max(0, min(x + w, bx + bw) - max(x, bx))
    height = max(0, min(y + h, by + bh) - max(y, by))
    intersection = width * height
    return intersection / (w * h + bw * bh - intersection)


def validate_detected_facecam(observations, sampled_times, *, start, end):
    """Reduce corroborated observations to a renderer rectangle, or omit the inset."""
    if not isinstance(observations, list) or not 3 <= len(observations) <= 6:
        return None
    labels = {round(second, 2) for second in sampled_times if start <= second <= end}
    rectangles = {}
    for observation in observations:
        if not isinstance(observation, dict) or observation.keys() != {"at", "rect"}:
            return None
        at = observation["at"]
        if type(at) not in (int, float) or at not in labels or at in rectangles:
            return None
        try:
            rect = validate_facecam({"rect": observation["rect"]})["rect"]
        except ValueError:
            return None
        x, y, width, height = rect
        if not (.06 <= width <= .38 and .07 <= height <= .55 and .009 <= width * height <= .16):
            return None
        # Embedded camera panels occupy a screen edge. The interior is primarily
        # gameplay, including cutscenes and nearby in-world faces.
        if min(x, y, 1 - x - width, 1 - y - height) > .16:
            return None
        if any(_overlap(rect, previous) < .72 for previous in rectangles.values()):
            return None
        rectangles[at] = rect
    span = end - start
    if min(rectangles) > start + span / 3 or max(rectangles) < end - span / 3:
        return None
    coordinates = [median(values) for values in zip(*rectangles.values())]
    try:
        return validate_facecam({"rect": coordinates})
    except ValueError:
        return None


def validate_facecam(spec):
    if spec is None:
        return None
    if not isinstance(spec, dict) or set(spec) - {"rect", "position"}:
        raise ValueError("facecam must contain rect [x, y, width, height] and optional position")
    rect = spec.get("rect")
    if (not isinstance(rect, (list, tuple)) or len(rect) != 4
            or any(type(value) not in (int, float) or not 0 <= value <= 1 for value in rect)):
        raise ValueError("facecam.rect must have four numbers from 0 to 1")
    x, y, width, height = map(float, rect)
    if min(width, height) < .01 or x + width > 1 or y + height > 1:
        raise ValueError("facecam.rect must fit inside the source and be at least 0.01 wide and high")
    position = spec.get("position", "left")
    if position not in ("left", "right"):
        raise ValueError("facecam.position must be left or right")
    return {"rect": [x, y, width, height], "position": position}


def display_aspect(stream):
    sar = stream.get("sample_aspect_ratio") or "1:1"
    try:
        pixel_aspect = float(Fraction(sar.replace(":", "/"))) or 1
    except (ValueError, ZeroDivisionError):
        pixel_aspect = 1
    aspect = stream["width"] * pixel_aspect / stream["height"]
    # FFmpeg autorotates the input before applying the filtergraph.
    rotation = next((item["rotation"] for item in stream.get("side_data_list", []) if "rotation" in item), 0)
    return 1 / aspect if round(float(rotation) / 90) % 2 else aspect


def portrait_center(rect, aspect, x, y):
    """Keep the embedded webcam outside the portrait crop when the shot allows it."""
    portrait = 9 / 16
    horizontal = aspect >= portrait
    axis = 0 if horizontal else 1
    span = min(portrait / aspect, aspect / portrait)
    center = x if horizontal else y
    start = min(max(center - span / 2, 0), 1 - span)
    camera_start, camera_end = rect[axis], rect[axis] + rect[axis + 2]
    margin = .002
    if start + span <= camera_start - margin or start >= camera_end + margin:
        return x, y
    neighboring = (camera_start - span - margin, camera_end + margin)
    viable = [offset for offset in neighboring if 0 <= offset <= 1 - span
              and offset <= center <= offset + span and abs(offset - start) <= span / 3]
    if not viable:
        return None
    adjusted = min(viable, key=lambda offset: abs(offset - start)) + span / 2
    return (adjusted, y) if horizontal else (x, adjusted)


def overlay_filters(spec, top_promo=False):
    x, y, width, height = spec["rect"]
    # Use the displayed crop aspect, including nonsquare source pixels.
    crop = (f"crop=w='max(2,iw*{width:.12g})':h='max(2,ih*{height:.12g})'"
            f":x='iw*{x:.12g}':y='ih*{y:.12g}'")
    scale = "scale=w='max(2,2*trunc(min(352,320*dar)/2))':h='max(2,2*trunc(min(320,352/dar)/2))'"
    frame = "pad=iw+4:ih+4:2:2:color=0xF1F3E8,pad=iw+8:ih+8:4:4:color=0x11151A"
    # Right edge leaves room for Shorts/Reels controls. The top promo can
    # remain below the title in meme mode, so put the webcam below both.
    left = 64 if spec["position"] == "left" else "W-w-164"
    top = 672 if top_promo else 448
    return [f"[face]{crop},{scale},setsar=1,{frame}[panel]",
            f"[game][panel]overlay=x={left}:y={top}[layout]"]
