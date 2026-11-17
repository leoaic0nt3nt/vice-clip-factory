"""Small explicit commands around upstream tools, editorial plan and upload queue."""
import argparse
import json
from pathlib import Path
import sys
import subprocess
import sqlite3
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


def plan_media(input_path, config, source_id, transcript_path, top, min_duration, max_duration, *, editorial_cache_dir=None):
    check_rights(config, source_id, "edit")
    analysis = analyze(input_path)
    transcript = read_transcript(transcript_path, analysis["duration"])
    editorial = config.get("autopilot", {}).get("editorial", {})
    provider = editorial.get("provider", "baseline")
    if provider == "codex":
        from .editorial import select_editorial_candidates, DEFAULT_MODEL
        cache = editorial_cache_dir or Path(config.get("autopilot", {}).get("output_dir", "output")) / "editorial-cache"
        language = editorial.get("output_language", "ru")
        clips = select_editorial_candidates(input_path, analysis, transcript, cache_dir=cache,
            model=editorial.get("model", DEFAULT_MODEL), top=top, min_duration=min_duration,
            max_duration=max_duration, timeout=float(editorial.get("timeout_seconds", 180)),
            output_language=language)
        return {"version": 2, "source_id": source_id, "analysis": analysis, "transcript": transcript,
                "clips": clips, "selector": {"provider": provider, "model": editorial.get("model", DEFAULT_MODEL)},
                "limitations": "AI editorial assessment from transcript and sampled frames; not a prediction of reach or income."}
    if provider != "baseline":
        raise ValueError("Editorial provider must be codex or baseline")
    return {"version": 1, "source_id": source_id, "analysis": analysis, "transcript": transcript,
            "clips": select_candidates(analysis, transcript, top=top, min_duration=min_duration, max_duration=max_duration),
            "selector": {"provider": "baseline"},
            "limitations": "Rule-based ranking, no gameplay semantics, no auto-tracking, no guarantee of virality. Review setup/payoff, rights and originality."}


def parser():
    root = argparse.ArgumentParser(prog="viceclip", description="Vice Clip Factory · автоматические клипы и публикации")
    root.add_argument("--version", action="version", version=__version__)
    commands = root.add_subparsers(dest="command", required=True)
    commands.add_parser("doctor")
    from .onboarding import default_config_path
    p = commands.add_parser("setup",help="Мастер первого запуска Vice Clip Factory")
    p.add_argument("--config",default=str(default_config_path()))
    p.add_argument("--non-interactive",action="store_true")
    p.add_argument("--source",action="append",default=[])
    p.add_argument("--language",choices=["en","ru"],help="Язык заголовков и CTA. Субтитры сохраняют язык исходника.")
    p.add_argument("--youtube-privacy",choices=["private","unlisted","public"])
    p.add_argument("--inbox");p.add_argument("--no-asr",action="store_true")
    p.add_argument("--execute",action="store_true");p.add_argument("--accept-terms",action="store_true")
    p = commands.add_parser("promo",help="Реклама и ссылка для новых YouTube-клипов")
    p.add_argument("--config",default=str(default_config_path()))
    p.add_argument("--text");p.add_argument("--url")
    p.add_argument("--position",choices=["top","bottom"])
    p.add_argument("--start",type=float,help="Секунда появления, по умолчанию 4")
    p.add_argument("--seconds",type=float,help="Длительность показа от 1 до 8 секунд")
    p.add_argument("--cta",help="Короткий призыв к действию")
    p.add_argument("--language",choices=["en","ru"],help="Язык пометки и CTA")
    artwork = p.add_mutually_exclusive_group()
    artwork.add_argument("--artwork",help="Файл PNG, JPEG или WebP, в том числе из ChatGPT Image")
    artwork.add_argument("--no-artwork",action="store_true")
    promo_action=p.add_mutually_exclusive_group()
    promo_action.add_argument("--show",action="store_true")
    promo_action.add_argument("--off",action="store_true")
    p = commands.add_parser("market",help="Сменить язык заголовков для новых клипов")
    p.add_argument("--config",default=str(default_config_path()))
    p.add_argument("--language",required=True,choices=["en","ru"])
    p = commands.add_parser("connect",help="Подключить ещё один канал")
    p.add_argument("platform",choices=["youtube","instagram"])
    p.add_argument("--config",default=str(default_config_path()))
    p.add_argument("--name",required=True)
    p.add_argument("--client-secrets");p.add_argument("--token-env",default="INSTAGRAM_ACCESS_TOKEN")
    p = commands.add_parser("youtube",help="Проверка канала и статуса загруженного ролика")
    youtube_actions=p.add_subparsers(dest="action",required=True)
    q=youtube_actions.add_parser("check")
    q.add_argument("--config",default=str(default_config_path()))
    q.add_argument("--account",required=True)
    q.add_argument("--video-id")
    q.add_argument("--privacy",choices=["private","unlisted","public"])
    p = commands.add_parser("revenue",help="Ссылка на оффер, реальные комиссии и отчёт выпуска")
    actions = p.add_subparsers(dest="action",required=True)
    for action in ("link","import","report"):
        q = actions.add_parser(action)
        q.add_argument("--config",default=str(default_config_path()))
        if action == "link":
            q.add_argument("--clip-id")
            q.add_argument("--platform",choices=["youtube","instagram"],default="youtube")
        if action == "import":
            q.add_argument("csv")
    p = commands.add_parser("autopilot",help="Постоянная работа, расписание и управление")
    actions=p.add_subparsers(dest="action",required=True)
    for action in ("tick","run","start","stop","status","logs","pause","resume"):
        q=actions.add_parser(action)
        q.add_argument("--config",default=str(default_config_path()))
        if action in ("tick","run","start"):
            mode=q.add_mutually_exclusive_group()
            mode.add_argument("--execute",dest="execute",action="store_const",const=True,default=None)
            mode.add_argument("--preview",dest="execute",action="store_const",const=False)
        if action=="run":q.add_argument("--interval",type=float)
        if action=="start":q.add_argument("--persistent",action="store_true")
        if action=="logs":q.add_argument("--lines",type=int,default=40)
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
    from .network import configure_certificates
    configure_certificates()
    args = parser().parse_args(argv)
    try:
        config = load_config(args.config) if hasattr(args, "config") and args.command not in ("setup","autopilot") else None
        result = None
        if args.command == "setup":
            from .onboarding import setup
            result=setup(args.config,non_interactive=args.non_interactive,sources=args.source,inbox=args.inbox,
                         no_asr=args.no_asr,execute=args.execute,accept_terms=args.accept_terms,
                         language=args.language,youtube_privacy=args.youtube_privacy)
        elif args.command == "promo":
            from . import promo
            if args.off:
                result=promo.disable(args.config)
            elif args.show:
                result=promo.status(args.config)
            else:
                from .onboarding import ask
                previous = promo.active_campaign(config) or {}
                edit_flags = any(x is not None for x in
                                 (args.text, args.url, args.position, args.start, args.seconds,
                                  args.cta, args.language, args.artwork)) or args.no_artwork
                if not edit_flags:
                    print("Плашка добавляется в новые YouTube-клипы. Instagram получает чистый ролик.")
                    print("Разместите свою ссылку в профиле канала один раз; текст в самом видео не кликабелен.")
                    profile_language = config.get("autopilot", {}).get("editorial", {}).get("output_language", "ru")
                    text=ask("Текст плашки",previous.get("text","YOUR PRODUCT" if profile_language == "en" else "ТВОЙ ПРОДУКТ"))
                    url=ask("Ссылка на продукт или ваша реферальная ссылка (HTTPS)",previous.get("url",""))
                    position=ask("Положение: top - сверху, bottom - снизу",previous.get("position","top"))
                else:
                    text=args.text if args.text is not None else previous.get("text")
                    url=args.url if args.url is not None else previous.get("url")
                    position=args.position or previous.get("position","top")
                    if text is None or url is None:
                        raise ValueError("Для первой кампании укажите --text и --url")
                result=promo.configure(args.config,text=text,url=url,position=position,
                    artwork=args.artwork,drop_artwork=args.no_artwork,start=args.start,duration=args.seconds,
                    cta=args.cta,language=args.language)
        elif args.command == "market":
            from .onboarding import private_json
            editorial = config.setdefault("autopilot", {}).setdefault("editorial", {})
            editorial.update(provider="codex", output_language=args.language)
            private_json(args.config,config)
            result={"output_language": args.language, "applies_to": "new_renders_only",
                    "subtitle_language": "source", "campaign_language_command": "viceclip promo --language " + args.language}
        elif args.command == "youtube":
            from .publishers import youtube_readiness
            from .rights import get_account
            if args.privacy and not args.video_id:
                raise ValueError("--privacy используется вместе с --video-id")
            account = get_account(config, args.account)
            if account.get("platform") != "youtube":
                raise ValueError("Выберите подключённый аккаунт YouTube")
            result = youtube_readiness(account, video_id=args.video_id, expected_privacy=args.privacy)
        elif args.command == "connect":
            import os
            import re
            from .onboarding import private_json
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,50}",args.name):
                raise ValueError("Имя канала: латинские буквы, цифры, дефис или подчёркивание")
            if any(a["id"]==args.name for a in config.get("accounts",[])):
                raise ValueError("Канал с таким именем уже подключён")
            token_file=Path(args.config).resolve().parent/"secrets"/(args.name+".json")
            if args.platform=="youtube":
                from .auth import connect_youtube
                if not args.client_secrets:raise ValueError("Укажите --client-secrets с файлом подключения Google")
                info=connect_youtube(args.client_secrets,token_file)
            else:
                from .auth import connect_instagram
                token=os.environ.get(args.token_env)
                if not token:raise ValueError("Передайте токен в переменную "+args.token_env)
                info=connect_instagram(token,token_file)
            config.setdefault("accounts",[]).append({"id":args.name,"platform":args.platform,
                "commercial":True,"max_posts_per_day":3,**info})
            config.setdefault("autopilot",{}).setdefault("accounts",[]).append(args.name)
            private_json(args.config,config)
            result={"connected":args.name,"platform":args.platform,"remote_id":info["remote_id"]}
        elif args.command == "revenue":
            from . import revenue, promo
            home=Path(args.config).expanduser().resolve().parent
            settings=config.get("autopilot",{})
            def profile_path(key, fallback):
                path=Path(settings.get(key,fallback)).expanduser()
                return path if path.is_absolute() else home/path
            ledger=profile_path("revenue_db","revenue.sqlite3")
            if args.action == "link":
                campaign=promo.active_campaign(config)
                if not campaign:
                    raise ValueError("Сначала настройте оффер: viceclip promo")
                if args.clip_id:
                    revenue.validate_clip_campaign(campaign,args.clip_id,
                        queue_path=profile_path("publish_db","data/publish.sqlite3"),
                        state_path=profile_path("state_path","data/autopilot.sqlite3"))
                result=revenue.tracking_link(campaign,platform=args.platform,clip_id=args.clip_id)
            elif args.action == "import":
                result=revenue.import_conversions(ledger,args.csv)
            else:
                result=revenue.report(profile_path("publish_db","data/publish.sqlite3"),
                    state_path=profile_path("state_path","data/autopilot.sqlite3"),ledger_path=ledger)
        elif args.command == "autopilot":
            from . import service
            if args.action == "status":
                result={"service":service.service_status(args.config)}
                try:
                    from .autopilot import Autopilot
                    pilot=Autopilot(args.config)
                    try:result["autopilot"]=pilot.status()
                    finally:pilot.close()
                except (ValueError,OSError,KeyError) as exc:
                    result["configuration_error"]=str(exc)
            elif args.action in ("tick","run"):
                from .autopilot import Autopilot
                pilot=Autopilot(args.config)
                try:
                    if args.action=="tick":result=pilot.tick(execute=args.execute)
                    elif args.action=="run":result=pilot.run(execute=args.execute,interval=args.interval)
                finally:pilot.close()
            elif args.action=="start":
                load_config(args.config)
                result=service.start(args.config,execute=args.execute,persistent=args.persistent)
            elif args.action=="stop":result=service.stop(args.config)
            elif args.action=="logs":
                print(service.logs(args.config,args.lines));return 0
            else:
                from .onboarding import private_json
                config=load_config(args.config)
                config.setdefault("autopilot",{})["enabled"]=args.action=="resume"
                private_json(args.config,config)
                result={"enabled":config["autopilot"]["enabled"]}
        elif args.command == "doctor":
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
    except KeyboardInterrupt:
        print("Vice Clip Factory остановлен",file=sys.stderr)
        return 130
    except (ValueError, RuntimeError, OSError, KeyError, sqlite3.Error) as error:
        print(f"viceclip: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
