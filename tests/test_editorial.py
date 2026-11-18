import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from viceclip import editorial
from viceclip.scoring import remap_captions


def response_clip(**changes):
    return {
        "start": 5, "end": 30, "score": 88, "hook": "Колёса держатся по Wi‑Fi?",
        "setup": "Водитель пытается тронуться после удара.",
        "payoff": "Машина едет на сломанном колесе.",
        "why": "После удара машина едет боком.",
        "setup_at": 8, "payoff_at": 28, "crop_center_x": .46, "caption_mode": "phrase",
        **changes,
    }


def story_clip(**changes):
    return response_clip(kind="fail", action="Водитель жмёт газ и пытается выровняться.",
                         action_at=19, **changes)


LIMITS = dict(duration=120, top=2, min_duration=20, max_duration=45)


class ResponseTests(unittest.TestCase):
    def test_valid_clip_and_renderer_fields(self):
        valid = {"clips": [response_clip(score=60), response_clip(
            start=35, end=60, setup_at=37, payoff_at=55, hook="Поворот без руля?",
        )]}
        clips = editorial.validate_response(valid, **LIMITS)
        self.assertEqual([clip["start"] for clip in clips], [35, 5])
        self.assertEqual(clips[0]["id"], "clip-01")
        self.assertEqual(clips[1]["crop_center_x"], .46)
        self.assertEqual(clips[0]["caption_mode"], "phrase")
        self.assertEqual(clips[0]["evidence"]["model"], "gpt-6.2-sol")
        self.assertEqual(clips[0]["evidence"]["payoff_at"], 55)
        self.assertEqual(editorial.validate_response({"clips": []}, **LIMITS), [])

    def test_rejects_malformed_and_unusable_clips(self):
        invalid = [
            None, [], {"clips": {}}, {"clips": [1]}, {"clips": [], "extra": 1},
            {"clips": [response_clip(start=-1)]}, {"clips": [response_clip(end=121)]},
            {"clips": [response_clip(end=23)]}, {"clips": [response_clip(end=55)]},
            {"clips": [response_clip(score=True)]}, {"clips": [response_clip(score=float("nan"))]},
            {"clips": [response_clip(payoff_at=8)]}, {"clips": [response_clip(setup_at=32)]},
            {"clips": [response_clip(caption_mode="caption")]},
            {"clips": [response_clip(crop_center_x=2)]},
            {"clips": [response_clip(hook="Г" * 71)]},
            {"clips": [response_clip(hook="Only English")]},
            {"clips": [response_clip(), response_clip(start=6, hook="Повтор сцены")]},
            {"clips": [response_clip(), response_clip(
                start=31, end=60, setup_at=38, payoff_at=50, hook="колёса держатся по wi-fi!",
            )]},
            {"clips": [response_clip()] * 3},
        ]
        for data in invalid:
            with self.subTest(data=data), self.assertRaises(editorial.EditorialError):
                editorial.validate_response(data, **LIMITS)

    def test_story_mode_requires_an_early_setup_and_a_visible_payoff(self):
        clip = story_clip()
        clips = editorial.validate_response({"clips": [clip]}, story_mode=True, **LIMITS)
        self.assertEqual(clips[0]["evidence"]["kind"], "fail")
        self.assertEqual(clips[0]["evidence"]["action"], clip["action"])
        self.assertEqual(clips[0]["evidence"]["action_at"], 19)
        self.assertEqual(editorial.validate_response({"clips": []}, story_mode=True, **LIMITS), [])
        invalid = [
            response_clip(),
            {**clip, "kind": "banter"},
            {**clip, "setup_at": 11},
            {**clip, "action_at": 7},
            {**clip, "action_at": 28.1},
            {**clip, "payoff_at": 29.1},
            {**clip, "payoff_at": 19.5},
            {**clip, "action": clip["payoff"]},
        ]
        for candidate in invalid:
            with self.subTest(clip=candidate), self.assertRaises(editorial.EditorialError):
                editorial.validate_response({"clips": [candidate]}, story_mode=True, **LIMITS)

    def test_story_mode_rejects_a_repeated_payoff(self):
        second = {**story_clip(), "start": 35, "end": 60, "setup_at": 38,
                  "action_at": 49, "payoff_at": 58, "hook": "Так можно доехать?"}
        with self.assertRaisesRegex(editorial.EditorialError, "повторил развязку"):
            editorial.validate_response({"clips": [story_clip(), second]}, story_mode=True,
                                        **LIMITS)


class RequestTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        base = Path(directory.name)
        self.input_file = base / "gameplay.mp4"
        self.input_file.touch()
        self.cache_dir = base / "cache"
        self.analysis = {"duration": 120, "sha256": "source-a",
                         "audio_dbfs": [-36] * 120, "motion": [2] * 120}
        self.speech = [{"start": 6, "end": 8, "text": "Тестовая реплика.",
                        "words": [{"start": 6.15, "end": 6.8, "word": " Тестовая"},
                                  {"start": 6.8, "end": 7.85, "word": " реплика."}]}]

    def choose(self, **options):
        return editorial.select_editorial_candidates(
            self.input_file, self.analysis, self.speech, cache_dir=self.cache_dir, **options,
        )

    def test_cache_reuses_only_matching_valid_inputs(self):
        with patch.object(editorial, "_contact_sheets", return_value=(["frames.jpg"], [6.0])), \
                patch.object(editorial, "_codex", return_value={"clips": [response_clip()]}) as request:
            first = self.choose()
            self.assertEqual(self.choose(), first)
            request.assert_called_once()
            prompt = request.call_args.args[1]
            self.assertIn('[6.15,7.85,"Тестовая реплика."]', prompt)
            stored = next(self.cache_dir.glob("*.json"))
            self.assertNotIn("Тестовая реплика", stored.read_text())

            tampered = json.loads(stored.read_text())
            tampered["response"]["clips"][0]["score"] = 23
            stored.write_text(json.dumps(tampered))
            self.assertEqual(self.choose(), first)
            self.assertEqual(request.call_count, 2)

            tampered["response"]["clips"][0]["start"] = -15
            tampered["response_sha256"] = editorial._digest(tampered["response"])
            stored.write_text(json.dumps(tampered))
            self.assertEqual(self.choose(), first)
            self.assertEqual(request.call_count, 3)

            self.choose(model="gpt-6.2-sol-test")
            self.speech[0]["words"][0]["word"] = " Обновлённая"
            self.choose()
            self.analysis["sha256"] = "source-b"
            self.choose()
            self.assertEqual(request.call_count, 6)

    def test_english_hooks_use_separate_cache_and_preserve_source_speech(self):
        english_clip = response_clip(
            hook="Can a Car Still Drive on Three Wheels?",
            setup="The player tries to pull away after the impact.",
            payoff="The car moves with a damaged wheel.",
            why="The car turns sideways immediately after moving.",
        )
        with patch.object(editorial, "_contact_sheets", return_value=(["frames.jpg"], [6.0])), \
                patch.object(editorial, "_codex", side_effect=[
                    {"clips": [response_clip()]}, {"clips": [english_clip]},
                ]) as request:
            russian = self.choose()
            english = self.choose(output_language="en")
            self.assertEqual(english[0]["hook"], english_clip["hook"])
            self.assertEqual(english[0]["output_language"], "en")
            self.assertEqual(english[0]["evidence"]["setup"], english_clip["setup"])
            prompt = request.call_args.args[1]
            self.assertIn("new English", prompt)
            self.assertIn('[6.15,7.85,"Тестовая реплика."]', prompt)
            self.assertEqual(self.choose(output_language="en"), english)
            self.assertEqual(self.choose(), russian)
            self.assertEqual(request.call_count, 2)
        with self.assertRaisesRegex(editorial.EditorialError, "на английском"):
            editorial.validate_response({"clips": [response_clip()]}, output_language="en", **LIMITS)

    def test_story_mode_requires_labeled_source_frames_and_uses_a_separate_cache(self):
        observed = [6.0, 8.0, 19.0, 28.0]
        with patch.object(editorial, "_contact_sheets", return_value=(["frames.jpg"], observed)), \
                patch.object(editorial, "_codex", side_effect=[
                    {"clips": [response_clip()]}, {"clips": [{**story_clip(), "action_at": 18}]},
                    {"clips": [story_clip()]},
                ]) as request:
            classic = self.choose()
            with self.assertRaisesRegex(editorial.EditorialError, "наблюдаемые кадры"):
                self.choose(story_mode=True)
            self.assertEqual(len(list(self.cache_dir.glob("*.json"))), 1)
            story = self.choose(story_mode=True)
            self.assertEqual(self.choose(story_mode=True), story)
            self.assertEqual(self.choose(), classic)
            self.assertEqual(request.call_count, 3)
            strict_request = request.call_args
            self.assertIn("cold open", strict_request.args[1])
            self.assertIn("action_at", strict_request.kwargs["schema"]["properties"]["clips"]
                          ["items"]["required"])

    def test_editorial_failure_propagates_and_is_never_cached(self):
        with patch.object(editorial, "_contact_sheets", return_value=(["frames.jpg"], [6.0])), \
                patch.object(editorial, "_codex", side_effect=editorial.EditorialError("ошибка")):
            with self.assertRaisesRegex(editorial.EditorialError, "ошибка"):
                self.choose()
        self.assertFalse(self.cache_dir.exists())

    def test_malformed_remote_output_is_never_cached(self):
        with patch.object(editorial, "_contact_sheets", return_value=(["frames.jpg"], [6.0])), \
                patch.object(editorial, "_codex", return_value={"clips": [response_clip(end=10)]}):
            with self.assertRaisesRegex(editorial.EditorialError, "Длительность"):
                self.choose()
        self.assertFalse(self.cache_dir.exists())

    def test_no_remote_request_for_short_source(self):
        self.analysis["duration"] = 10
        with patch.object(editorial, "_codex") as request:
            self.assertEqual(self.choose(), [])
            request.assert_not_called()

    def test_four_hour_onboarding_limit_is_supported(self):
        self.analysis["duration"] = 14400
        with patch.object(editorial, "_contact_sheets", return_value=(["frames.jpg"], [6.0])), \
                patch.object(editorial, "_codex", return_value={"clips": [response_clip()]}) as request:
            self.assertEqual(len(self.choose()), 1)
            self.assertIn('"duration_seconds":14400.0', request.call_args.args[1])

    def test_partial_whisperx_alignment_keeps_entire_segment_in_evidence_and_subtitles(self):
        self.speech[0]["text"] = "В 2014 году."
        for unaligned in (
            {"word": "2014"}, {"start": 6.3, "word": "2014"},
            {"start": 6.3, "end": float("inf"), "word": "2014"},
            {"start": float("nan"), "end": 6.8, "word": "2014"},
            {"start": 6.8, "end": 6.8, "word": "2014"},
        ):
            with self.subTest(word=unaligned):
                self.speech[0]["words"][0] = unaligned
                self.assertEqual(editorial._speech_evidence(self.speech, [6.0]), [[6, 8, "В 2014 году."]])
                self.assertEqual(remap_captions(self.speech, [(5.5, 7.0)]),
                                 [{"start": .5, "end": 1.5, "text": "В 2014 году."}])

    def test_timeout_stops_process_group(self):
        with self.assertRaisesRegex(editorial.EditorialError, "Истекло время ожидания"):
            editorial._run(
                [sys.executable, "-c", "import time; time.sleep(30)"], timeout=.05,
                message="редактор", stdin=subprocess.DEVNULL,
            )

    def test_cli_provider_errors_do_not_echo_diagnostics(self):
        with tempfile.TemporaryDirectory() as workspace, \
                patch.object(editorial.shutil, "which", return_value="/bin/codex"), \
                patch.object(editorial, "_run", return_value=(1, b"", b"model not supported secret")):
            with self.assertRaisesRegex(editorial.EditorialUnavailable, "Модель gpt-6.2-sol недоступна") as raised:
                editorial._codex(Path(workspace), "prompt", [], editorial.DEFAULT_MODEL, timeout=10)
            self.assertNotIn("secret", str(raised.exception))

    def test_cli_operational_failures_signal_wait(self):
        for status, stdout, stderr in (
            (1, b"", b"401 Unauthorized, secret"),
            (1, b"", b"unexpected status 429 Too Many Requests, secret"),
            (1, b"", b"Usage limit reached, secret"),
            (1, b"", b"failed to connect, secret"),
            (0, b'{"type":"turn.failed","error":{"message":"quota secret"}}', b""),
        ):
            with self.subTest(status=status, response=stdout or stderr), \
                    tempfile.TemporaryDirectory() as workspace, \
                    patch.object(editorial.shutil, "which", return_value="/bin/codex"), \
                    patch.object(editorial, "_run", return_value=(status, stdout, stderr)):
                with self.assertRaises(editorial.EditorialUnavailable) as raised:
                    editorial._codex(Path(workspace), "prompt", [], editorial.DEFAULT_MODEL, timeout=10)
                self.assertNotIn("secret", str(raised.exception))

    def test_preflight_checks_login_without_exposing_credentials(self):
        with patch.object(editorial.shutil, "which", return_value=None):
            with self.assertRaisesRegex(editorial.EditorialUnavailable, "Установите"):
                editorial.check_editorial_ready()

        with patch.object(editorial.shutil, "which", return_value="/bin/codex"), \
                patch.object(editorial, "_run", return_value=(0, b"", b"")) as run:
            self.assertEqual(editorial.check_editorial_ready(), "/bin/codex")
            self.assertEqual(run.call_args.args[0], ["/bin/codex", "login", "status"])
            self.assertIs(run.call_args.kwargs["failure"], editorial.EditorialUnavailable)
            run.return_value = (1, b"secret", b"")
            with self.assertRaisesRegex(editorial.EditorialUnavailable, "Войдите") as raised:
                editorial.check_editorial_ready()
            self.assertNotIn("secret", str(raised.exception))

    def test_cli_override_and_tool_activity_rejection(self):
        def run(command, **options):
            self.assertEqual(command[0], "/opt/viceclip/codex")
            self.assertIn("--ignore-user-config", command)
            self.assertIn("--ephemeral", command)
            self.assertNotIn("AWS_SECRET_ACCESS_KEY", options["env"])
            path = Path(command[command.index("-o") + 1])
            path.write_text('{"clips":[]}')
            event = b'{"type":"item.completed","item":{"type":"command_execution"}}'
            return 0, event, b""

        with tempfile.TemporaryDirectory() as workspace, \
                patch.dict(editorial.os.environ, {"VICECLIP_CODEX": "/opt/viceclip/codex",
                                                   "AWS_SECRET_ACCESS_KEY": "unrelated"}), \
                patch.object(editorial.shutil, "which", side_effect=lambda name: name), \
                patch.object(editorial, "_run", side_effect=run):
            with self.assertRaisesRegex(editorial.EditorialError, "внешние инструменты"):
                editorial._codex(Path(workspace), "prompt", [], editorial.DEFAULT_MODEL, timeout=10)


if __name__ == "__main__":
    unittest.main()
