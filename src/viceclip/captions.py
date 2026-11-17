"""Prepare short, word-synchronized captions for vertical gameplay."""
import re
from functools import lru_cache
from pathlib import Path

from PIL import ImageFont

from .scoring import timed_words


ROBOTO_CONDENSED = Path(__file__).resolve().parents[2] / "assets/fonts/RobotoCondensed/RobotoCondensed.ttf"
IVORY = "#FFF9ED"
CAPTION_Y = 1216
ENTRANCE_SECONDS = 2 / 30


@lru_cache(maxsize=64)
def caption_font(size, configured=None):
    path = configured or ROBOTO_CONDENSED
    try:
        face = ImageFont.truetype(str(path), size)
    except OSError as exc:
        raise ValueError(f"Cannot open caption font: {path}") from exc
    if not configured:
        face.set_variation_by_axes([900])
    return face


def _caption_text(words):
    text, spans = "", []
    for word in words:
        if text and not re.match(r"^[,.;:!?…]|^-\w", word):
            text += " "
        spans.append((len(text), len(text) + len(word)))
        text += word
    return text, spans


def caption_cues(captions, mode):
    """Keep up to four words per cue and preserve available word boundaries."""
    if mode == "meme":
        return []
    cues = []
    for caption in captions:
        timed = timed_words(caption)
        tokens = [word["word"].strip() for word in timed] if timed else caption["text"].split()
        count = len(tokens)
        first = 0
        while first < count:
            remaining = count - first
            # Five words read more naturally in groups of three and two.
            last = first + (3 if remaining == 5 else min(4, remaining))
            text, spans = _caption_text(tokens[first:last])
            if timed:
                words = timed[first:last]
                cue = {"start": words[0]["start"], "end": max(word["end"] for word in words), "text": text}
                if mode == "words":
                    cue["accents"] = [(word["start"], word["end"], span) for word, span in zip(words, spans)]
            else:
                start, end = caption["start"], caption["end"]
                cue = {"start": start + (end - start) * first / count,
                       "end": start + (end - start) * last / count, "text": text}
            cues.append(cue)
            first = last
    return cues


def uppercase_caption(text, active=None):
    """Uppercase display text while preserving the active span across expansions."""
    if active is not None:
        start, end = active
        active = len(text[:start].upper()), len(text[:end].upper())
    return text.upper(), active
