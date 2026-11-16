"""Transparent editorial heuristics; all scores are ranking evidence, not predictions."""
import math
import re

EVENTS = ("погон", "взрыв", "прыж", "авари", "неожидан", "смотри", "что за", "полици", "вертол", "мисси", "баг", "провал", "chase", "explos", "jump", "crash", "police", "helicopter", "fail", "glitch", "watch", "no way", "mission")
SETUP = ("сейчас", "если", "попроб", "смотри", "watch", "try", "what if", "let's")
PAYOFF = ("получ", "упал", "взрыв", "провал", "вот это", "успел", "crash", "fail", "did it", "explos", "no way", "landed")


def _mean(values):
    return sum(values) / len(values) if values else 0


def _window(values, start, end):
    return values[max(0, int(start)):max(0, math.ceil(end))]


def iou(a, b):
    intersection = max(0, min(a[1], b[1]) - max(a[0], b[0]))
    return intersection / max(.001, (a[1]-a[0]) + (b[1]-b[0]) - intersection)


def select_candidates(analysis, transcript, *, top=3, min_duration=12, max_duration=38):
    duration = analysis["duration"]
    if not (0 < min_duration <= max_duration) or top < 1:
        raise ValueError("Invalid candidate limits")
    seeds = [float(s["start"]) for s in transcript if any(k in s["text"].lower() for k in EVENTS)]
    audio = analysis.get("audio_dbfs", [])
    motion = analysis.get("motion", [])
    seeds += sorted(range(len(audio)), key=lambda x: audio[x], reverse=True)[:8]
    seeds += sorted(range(len(motion)), key=lambda x: motion[x], reverse=True)[:8]
    seeds += [0]
    candidates = []
    for seed in seeds:
        start = max(0, seed - 3)
        end = min(duration, start + max_duration)
        if end-start < min(min_duration, duration):
            start = max(0, end-min_duration)
        segments = [s for s in transcript if s["start"] < end and s["end"] > start]
        # Preserve the beginning of a sentence when a seed lands inside it.
        if segments and segments[0]["start"] < start and end-segments[0]["start"] <= max_duration+2:
            start = segments[0]["start"]
        # Keep a sentence spanning the edge; a soft +2s handle is allowed.
        if segments and segments[-1]["end"] > end and segments[-1]["end"]-start <= max_duration + 2:
            end = min(duration, segments[-1]["end"])
        text = " ".join(s["text"] for s in segments).lower()
        hits = sorted({k for k in EVENTS if k in text})
        event = min(1, len(hits)/3)
        local_audio = _window(audio, start, end)
        audio_score = min(1, max(0, (_mean(local_audio)+45)/35)) if local_audio else 0
        local_motion = _window(motion, start, end)
        motion_score = min(1, _mean(local_motion)/20) if local_motion else 0
        setup = any(k in text for k in SETUP)
        payoff = any(k in text for k in PAYOFF)
        structure = .5 * setup + .5 * payoff
        if segments:
            score = round(100 * (.4*event + .2*audio_score + .2*motion_score + .2*structure), 1)
            mode = "speech_audio_motion"
        else:
            signals = ([audio_score] if local_audio else []) + ([motion_score] if local_motion else [])
            score = round(100 * _mean(signals), 1)
            mode = "audio_motion"  # No unavailable speech component can make action clips impossible to qualify.
        title = next((s["text"].strip() for s in segments if any(k in s["text"].lower() for k in EVENTS)), "Игровой момент")
        candidates.append({"start": round(start, 3), "end": round(end, 3), "score": score,
                           "hook": title[:95], "evidence": {"event_keywords": hits, "audio_energy": round(audio_score, 3),
                           "motion": round(motion_score, 3), "setup": setup, "payoff": payoff, "mode": mode},
                           "review_required": True})
    chosen = []
    for candidate in sorted(candidates, key=lambda x: (-x["score"], x["start"])):
        if all(iou((candidate["start"], candidate["end"]), (x["start"], x["end"])) <= .35 for x in chosen):
            candidate["id"] = f"clip-{len(chosen)+1:02d}"
            chosen.append(candidate)
        if len(chosen) == top:
            break
    return chosen


def kept_ranges(start, end, analysis, transcript, trim_silence=False):
    """Only remove >=2s quiet AND static AND speech-free interior gaps, with handles."""
    if not trim_silence or not analysis.get("audio_dbfs") or not analysis.get("motion"):
        return [(start, end)]
    audio, motion = analysis["audio_dbfs"], analysis["motion"]
    quiet = []
    for second in range(math.ceil(start+1), math.floor(end-1)):
        speech = any(s["start"] < second+1.5 and s["end"] > second-.5 for s in transcript)
        if second < min(len(audio), len(motion)) and audio[second] < -40 and motion[second] < 1.5 and not speech:
            quiet.append(second)
    groups = []
    for second in quiet:
        if groups and second == groups[-1][-1]+1:
            groups[-1].append(second)
        else:
            groups.append([second])
    cuts = [(g[0]+.2, g[-1]+.8) for g in groups if len(g) >= 2]
    result, cursor = [], start
    for a, b in cuts:
        if a > cursor:
            result.append((cursor, a))
        cursor = b
    if cursor < end:
        result.append((cursor, end))
    return result


def remap_captions(transcript, ranges):
    output, offset = [], 0.0
    for a, b in ranges:
        for s in transcript:
            left, right = max(a, s["start"]), min(b, s["end"])
            if right > left:
                output.append({"start": round(offset+left-a, 3), "end": round(offset+right-a, 3), "text": s["text"]})
        offset += b-a
    return output
