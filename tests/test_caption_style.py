import unittest

from viceclip.captions import caption_cues, caption_font, uppercase_caption


class CaptionStyleTests(unittest.TestCase):
    def test_short_cues_retain_exact_word_times_and_text(self):
        tokens = ["Смотри", "он", "летит", "прямо", "на", "крышу!"]
        words = [{"word": word, "start": i * .5, "end": i * .5 + .4} for i, word in enumerate(tokens)]
        cues = caption_cues([{"start": 0, "end": 3, "text": " ".join(tokens), "words": words}], "words")
        self.assertEqual([cue["text"] for cue in cues], ["Смотри он летит прямо", "на крышу!"])
        self.assertEqual((cues[1]["start"], cues[1]["end"]), (2, 2.9))
        start, end, (left, right) = cues[1]["accents"][1]
        self.assertEqual((start, end, cues[1]["text"][left:right]), (2.5, 2.9, "крышу!"))

    def test_unaligned_dialogue_stays_readable_without_guessed_word_highlight(self):
        cues = caption_cues([{"start": 1.0, "end": 3.0, "text": "Wait for the blue car",
                              "words": [{"word": "Wait"}]}], "words")
        self.assertEqual([cue["text"] for cue in cues], ["Wait for the", "blue car"])
        self.assertEqual((cues[0]["start"], cues[-1]["end"]), (1.0, 3.0))
        self.assertFalse(any("accents" in cue for cue in cues))

    def test_uppercase_keeps_unicode_accent_aligned_and_font_supports_cyrillic(self):
        text, (left, right) = uppercase_caption("Straße и машина", (9, 15))
        self.assertEqual(text[left:right], "МАШИНА")
        face = caption_font(72)
        self.assertEqual(face.getname()[0], "Roboto Condensed")
        self.assertNotEqual(bytes(face.getmask("Ж")), bytes(face.getmask("\u0378")))
