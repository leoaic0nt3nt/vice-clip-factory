"""Small explicit commands around upstream tools, editorial plan and upload queue."""
import argparse
import json
from pathlib import Path
import sys
import subprocess
from . import __version__
from . import integrations
from .media import analyze, doctor, read_transcript
from .rights import load_config, check_rights
from .scoring import select_candidates
from .render import render
from .queue import Queue


def save(path, value):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def plan_media(input_path, config, source_id, transcript_path, top, min_duration, max_duration):
    check_rights(config, source_id, "edit")
    analysis = analyze(input_path)
    transcript = read_transcript(transcript_path, analysis["duration"])
    return {"version": 1, "source_id": source_id, "analysis": analysis, "transcript": transcript,
            "clips": select_candidates(analysis, transcript, top=top, min_duration=min_duration, max_duration=max_duration),
            "limitations": "Rule-based ranking, no gameplay semantics, no auto-tracking, no guarantee of virality. Review setup/payoff, rights and originality."}


def parser():
    root = argparse.ArgumentParser(prog="viceclip", description="Rights-aware gameplay clip orchestration MVP")
    root.add_argument("--version", action="version", version=__version__)
    commands = root.add_subparsers(dest="command", required=True)
    commands.add_parser("doctor")
    p = commands.add_parser("analyze")
    p.add_argument("input"); p.add_argument("--output", default="output/analysis.json")
    p = commands.add_parser("plan")
    p.add_argument("input"); p.add_argument("--config", default="examples/config.json"); p.add_argument("--source-id", required=True)
    p.add_argument("--transcript"); p.add_argument("--output", default="output/plan.json")
    p.add_argument("--top", type=int, default=3); p.add_argument("--min-duration", type=float, default=12); p.add_argument("--max-duration", type=float, default=38)
    p = commands.add_parser("render")
    p.add_argument("input"); p.add_argument("--plan", required=True); p.add_argument("--config", default="examples/config.json")
    p.add_argument("--output-dir", default="output/clips"); p.add_argument("--top", type=int)
    p = commands.add_parser("download")
    p.add_argument("source_id"); p.add_argument("--config", default="examples/config.json"); p.add_argument("--output-dir", default="data/downloads")
    p = commands.add_parser("discover")
    p.add_argument("--config", default="examples/config.json"); p.add_argument("--output", default="output/discovered.json")
    p.add_argument("--lookback-hours", type=float, default=24); p.add_argument("--merge-config", action="store_true", help="Register only channel-wide explicitly permitted discovered sources")
    p = commands.add_parser("asr")
    p.add_argument("input"); p.add_argument("--output-dir", default="output/asr"); p.add_argument("--device", choices=["cpu", "cuda"], default="cpu"); p.add_argument("--language")
    p = commands.add_parser("scenes")
    p.add_argument("input"); p.add_argument("--output-dir", default="output/scenes")
    p = commands.add_parser("autocut")
    p.add_argument("input"); p.add_argument("--output", required=True)
    p = commands.add_parser("run", help="Local/approved URL -> analyze -> plan -> portrait clips; no publishing")
    p.add_argument("--input"); p.add_argument("--source-id", required=True); p.add_argument("--config", default="examples/config.json")
    p.add_argument("--transcript"); p.add_argument("--asr", action="store_true"); p.add_argument("--device", choices=["cpu", "cuda"], default="cpu")
    p.add_argument("--top", type=int, default=3); p.add_argument("--output-dir", required=True)
    p = commands.add_parser("queue")
    actions = p.add_subparsers(dest="action", required=True)
    for action in ("add", "list", "approve", "run", "reconcile"):
        q = actions.add_parser(action)
        q.add_argument("--db", default="data/publish.sqlite3")
        if action in ("add", "run"):
            q.add_argument("--config", default="examples/config.json")
        if action == "add":
            q.add_argument("file"); q.add_argument("--source-id", required=True); q.add_argument("--account", required=True)
            q.add_argument("--title", required=True); q.add_argument("--caption", default=""); q.add_argument("--public-url")
            q.add_argument("--schedule"); q.add_argument("--sponsored", action="store_true"); q.add_argument("--privacy", choices=["private", "unlisted", "public"], default="private")
        if action in ("approve", "reconcile"):
            q.add_argument("id", type=int)
        if action == "reconcile":
            q.add_argument("--remote-id", required=True)
        if action == "run":
            q.add_argument("--execute", action="store_true", help="Perform real external uploads for due APPROVED jobs")
    return root


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        config = load_config(args.config) if hasattr(args, "config") else None
        result = None
        if args.command == "doctor":
            result = {"version": __version__, "tools": doctor(), "note": "Core render uses FFmpeg + Pillow; libass is optional. ASR/scenes/auto-editor are optional upstream installs."}
        elif args.command == "analyze":
            result = analyze(args.input); save(args.output, result)
        elif args.command == "plan":
            result = plan_media(args.input, config, args.source_id, args.transcript, args.top, args.min_duration, args.max_duration)
            save(args.output, result)
        elif args.command == "render":
            plan = load_config(args.plan)
            style = config.get("style", {})
            check_rights(config, plan["source_id"], "edit", sponsored=style.get("sponsored", False))
            result = {"outputs": render(args.input, plan, args.output_dir, style, args.top)}
        elif args.command == "download":
            result = {"input": integrations.download(config, args.source_id, args.output_dir)}
        elif args.command == "discover":
            result = integrations.discover(config, args.lookback_hours)
            save(args.output, result)
            if args.merge_config:
                known = {source["id"] for source in config.get("sources", [])}
                config.setdefault("sources", []).extend(source for source in result["sources"] if source["ready_for_ingest"] and source["id"] not in known)
                save(args.config, config)
        elif args.command == "asr":
            result = {"transcript": integrations.transcribe(args.input, args.output_dir, args.device, args.language)}
        elif args.command == "scenes":
            result = {"scene_lists": integrations.detect_scenes(args.input, args.output_dir)}
        elif args.command == "autocut":
            result = {"input": integrations.autocut(args.input, args.output)}
        elif args.command == "run":
            destination = Path(args.output_dir)
            if destination.exists() and any(destination.iterdir()):
                raise ValueError("Use a fresh output directory for each run")
            destination.mkdir(parents=True, exist_ok=True)
            input_path = args.input or integrations.download(config, args.source_id, destination/"input")
            transcript_path = args.transcript
            if args.asr:
                transcript_path = integrations.transcribe(input_path, destination/"asr", args.device)
            plan = plan_media(input_path, config, args.source_id, transcript_path, args.top, 12, 38)
            save(destination/"plan.json", plan)
            style = config.get("style", {})
            check_rights(config, args.source_id, "edit", sponsored=style.get("sponsored", False))
            result = {"plan": str((destination/"plan.json").resolve()), "outputs": render(input_path, plan, destination/"clips", style, args.top)}
            save(destination/"result.json", result)
        elif args.command == "queue":
            queue = Queue(args.db)
            if args.action == "add":
                metadata = {"title": args.title, "caption": args.caption, "privacy": args.privacy, "sponsored": args.sponsored, "public_url": args.public_url}
                result = queue.add(args.file, args.source_id, args.account, metadata, config, args.schedule)
            elif args.action == "list":
                result = queue.list()
            elif args.action == "approve":
                queue.approve(args.id); result = {"id": args.id, "status": "approved"}
            elif args.action == "reconcile":
                queue.reconcile(args.id, args.remote_id); result = {"id": args.id, "status": "reconciled", "playback_verified": False}
            else:
                result = queue.run(config, execute=args.execute)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except subprocess.CalledProcessError as error:
        print(f"viceclip: upstream command failed with exit code {error.returncode}; inspect its diagnostic above",file=sys.stderr)
        return 1
    except (ValueError, RuntimeError, OSError, KeyError) as error:
        print(f"viceclip: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
