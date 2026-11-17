import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from viceclip.autopilot import Autopilot
from viceclip.cli import main, plan_media
from viceclip.editorial import EditorialUnavailable
from viceclip.media import fingerprint
from viceclip.onboarding import private_json
from viceclip.service import runtime_path


class WorkflowTests(unittest.TestCase):
    def test_configured_editor_selects_plan_and_does_not_fall_back(self):
        config={"source_policy":{"mode":"configured_sources","user_responsible":True},
                "sources":[{"id":"own"}],"autopilot":{"editorial":{"provider":"codex","model":"gpt-6.2-sol"}}}
        analysis={"duration":60,"sha256":"a"*64}
        clips=[{"id":"clip-01","start":10,"end":35,"hook":"Прыжок через ограду"}]
        with patch("viceclip.cli.analyze",return_value=analysis), \
                patch("viceclip.cli.read_transcript",return_value=[]), \
                patch("viceclip.editorial.select_editorial_candidates",return_value=clips) as editor, \
                patch("viceclip.cli.select_candidates",side_effect=AssertionError("no fallback")):
            plan=plan_media("source.mp4",config,"own",None,2,20,40,editorial_cache_dir="cache")
            self.assertEqual(plan["clips"],clips)
            self.assertEqual(plan["selector"],{"provider":"codex","model":"gpt-6.2-sol"})
            self.assertEqual(editor.call_args.kwargs["cache_dir"],"cache")
            editor.side_effect=EditorialUnavailable("Войдите в Codex")
            with self.assertRaises(EditorialUnavailable):
                plan_media("source.mp4",config,"own",None,2,20,40)

    def test_missing_login_waits_without_download_or_exhausting_source_attempts(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);source=root/"input.mp4";source.write_bytes(b"fixture")
            config={"source_policy":{"mode":"configured_sources","user_responsible":True},
                    "sources":[{"id":"selected","url":"https://example.test/recording","platforms":[]}],
                    "accounts":[],"autopilot":{"editorial":{"provider":"codex"},"poll_interval_seconds":60}}
            now=[1_000_000_000.]
            downloader=Mock(return_value=source)
            processor=Mock(return_value={"input_sha256":fingerprint(source),"clips":[]})
            worker=Autopilot(config,base_dir=root,clock=lambda:now[0],downloader=downloader,processor=processor)
            try:
                with patch("viceclip.editorial.check_editorial_ready",side_effect=EditorialUnavailable("Войдите в Codex")):
                    for _ in range(5):
                        result=worker.tick(execute=False)
                        self.assertEqual(result["discovery"][0]["state"],"waiting")
                        now[0]+=61
                self.assertEqual(worker.db.execute("SELECT failures FROM media_jobs").fetchone()[0],0)
                downloader.assert_not_called()
                with patch("viceclip.editorial.check_editorial_ready"):
                    result=worker.tick(execute=False)
                self.assertEqual(len(result["processed"]),1)
                downloader.assert_called_once()
            finally:
                worker.close()

    def test_service_keeps_node_and_codex_paths_from_the_working_shell(self):
        with patch("viceclip.service.shutil.which",side_effect=lambda name:f"/custom/{name}/bin/{name}"), \
                patch.dict("os.environ",{"PATH":"/custom/extra/bin:/usr/bin"}):
            paths=runtime_path().split(":")
        self.assertIn("/custom/node/bin",paths)
        self.assertIn("/custom/codex/bin",paths)
        self.assertIn("/custom/extra/bin",paths)
        self.assertEqual(len(paths),len(set(paths)))

    def test_revenue_report_resolves_legacy_autopilot_default_paths(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder).resolve();config=root/"config.json";private_json(config,{"autopilot":{}})
            with patch("viceclip.revenue.report",return_value={}) as report, \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(["revenue","report","--config",str(config)]),0)
            report.assert_called_once_with(root/"data/publish.sqlite3",state_path=root/"data/autopilot.sqlite3",
                                           ledger_path=root/"revenue.sqlite3")
