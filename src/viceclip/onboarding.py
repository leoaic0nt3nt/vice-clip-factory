"""Guided product setup; credentials stay in a private local directory."""
import getpass
import json
import os
from pathlib import Path
import re
from urllib.parse import urlparse


def default_config_path():
    override = os.environ.get("VICECLIP_CONFIG")
    if override:
        return Path(override).expanduser().resolve()
    return Path.home() / ".viceclip" / "config.json"


def private_json(path, value):
    path = Path(path).expanduser().resolve()
    if not path.parent.exists():
        path.parent.mkdir(parents=True,mode=0o700)
    temporary = path.with_suffix(path.suffix+".tmp")
    fd = os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
    os.fchmod(fd,0o600)
    with os.fdopen(fd,"w",encoding="utf-8") as stream:
        json.dump(value,stream,ensure_ascii=False,indent=2)
        stream.write("\n")
    os.chmod(temporary,0o600)
    os.replace(temporary,path)


def base_config(config_path):
    home = Path(config_path).expanduser().resolve().parent
    inbox = home / "inbox"
    return {"product":"Vice Clip Factory","schema_version":2,
        "secret_env_file":str(home/"secrets/env.json"),
        "source_policy":{"mode":"configured_sources","user_responsible":True},
        "sources":[{"id":"inbox","creator":"Мои выбранные записи","enabled":True,
                    "platforms":["youtube","instagram"]}],"creators":[],"accounts":[],
        "style":{"brand":"@your_channel","trim_silence":True,"sponsor_text":"",
                 "sponsored":False,"sponsor_position":"top","preset":"veryfast",
                 "layout":"portrait","ad_format":"compact"},
        "autopilot":{"enabled":True,"execute":False,"poll_interval_seconds":300,
             "privacy":"public","state_path":str(home/"state.sqlite3"),"publish_db":str(home/"publish.sqlite3"),
             "output_dir":str(home/"output"),"inboxes":[{"path":str(inbox),"source_id":"inbox"}],
             "source_ids":[],"accounts":[],"auto_approve":True,"min_score":45,
             "clips_per_source":2,"min_clip_seconds":20,"max_clip_seconds":40,
             "posting_gap_seconds":14400,"max_source_seconds":14400,
             "editorial":{"provider":"codex","model":"gpt-6.2-sol","timeout_seconds":180,"output_language":"en"},
             "asr":{"enabled":True,"provider":"faster-whisper","model":"small","device":"cpu","language":None}}}


def add_source(config, url):
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError("Укажите полную HTTPS-ссылку")
    host,path = parsed.netloc.lower(),parsed.path
    index = len(config["sources"]) + len(config["creators"])
    identifier = f"source-{index}"
    common = {"id":identifier,"url":url,"enabled":True,"platforms":["youtube","instagram"],
              "destinations":["youtube","instagram"]}
    is_channel = host.endswith("youtube.com") and (path.startswith(("/@","/channel/","/c/","/user/")))
    if is_channel:
        common["platform"]="youtube"
        if path.startswith("/channel/"):
            common["remote_id"]=path.split("/")[2]
        config["creators"].append(common)
    elif host.endswith("twitch.tv") and len([x for x in path.split("/") if x]) == 1:
        common.update(platform="twitch",login=path.strip("/"),token_env="TWITCH_ACCESS_TOKEN",client_id_env="TWITCH_CLIENT_ID")
        config["creators"].append(common)
    else:
        config["sources"].append(common)
        config["autopilot"]["source_ids"].append(identifier)
    return identifier


def ask(prompt, default="", secret=False):
    suffix = f" [{default}]" if default and not secret else ""
    value = (getpass.getpass(prompt+suffix+": ") if secret else input(prompt+suffix+": ")).strip()
    return value or default


def yes(prompt, default=False):
    value = ask(prompt+" (да/нет)","да" if default else "нет").lower()
    return value in ("да","д","yes","y","1")


def setup(config_path=None, *, non_interactive=False, sources=None, inbox=None, no_asr=False,
          execute=False, accept_terms=False, language=None, youtube_privacy=None):
    path = Path(config_path or default_config_path()).expanduser().resolve()
    if path.exists():
        if non_interactive or not yes("Настройка уже существует. Заменить её?",False):
            raise ValueError("Конфигурация сохранена. Для изменения используйте новый путь --config или подключите аккаунт отдельной командой.")
    config = base_config(path)
    secret_values = {}
    print("\nVICE CLIP FACTORY · Первый запуск\n")
    print("Редактор GPT-6.2 Sol использует вход через codex login. Кадры и расшифровка передаются в Codex для отбора сцен.")
    if non_interactive:
        if not accept_terms:
            raise ValueError("Для настройки через код укажите --accept-terms: оператор отвечает за выбранные источники и правила площадок.")
        for url in sources or []:
            add_source(config,url)
        config["autopilot"]["execute"] = execute
    else:
        print("Один раз выберите источники, оформление и аккаунты. После этого автопилот работает по расписанию.")
        print("Используйте доступные вам источники и соблюдайте правила площадок. Условия: docs/TERMS.md.")
        if not yes("Продолжить настройку",True):
            raise ValueError("Настройка отменена")
        language = language or ask("Язык выпуска (en / ru)","en").lower()
        config["style"]["brand"] = ask("Подпись вашего канала","@your_channel")
        while True:
            url = ask("Ссылка на канал YouTube, Twitch или запись (Enter - закончить)")
            if not url:
                break
            try:
                add_source(config,url)
            except ValueError as exc:
                print(str(exc))
        if yes("Автоматически искать новые записи по теме",not bool(config["creators"] or config["autopilot"]["source_ids"])):
            query=ask("Тема поиска","GTA VI gameplay stream")
            secret_values["YOUTUBE_API_KEY"]=ask("Ключ поиска YouTube (из пакета подключения)",secret=True)
            config["autopilot"]["scout_queries"]=[query]
            config["autopilot"]["ingest_scout_candidates"]=True
        if any(c.get("platform")=="twitch" for c in config["creators"]):
            print("Twitch: данные официального приложения нужны один раз для наблюдения за каналами.")
            secret_values["TWITCH_CLIENT_ID"]=ask("Twitch Client ID")
            secret_values["TWITCH_CLIENT_SECRET"]=ask("Twitch Client Secret",secret=True)
        count=0
        while yes("Подключить канал YouTube",count==0):
            from .auth import connect_youtube
            client = ask("Путь к файлу подключения Google (client_secret JSON)")
            token_file = path.parent / "secrets" / f"youtube-{count+1}.json"
            try:
                info = connect_youtube(client,token_file)
                account = {"id":f"youtube-{count+1}","platform":"youtube","max_posts_per_day":3,"commercial":True,**info}
                config["accounts"].append(account); count+=1
                print("YouTube подключён: "+str(account.get("remote_id")))
            except (ValueError,RuntimeError,OSError,ImportError) as exc:
                print("Подключение не завершено: "+str(exc))
                if not yes("Повторить подключение",False):break
        count=0
        while yes("Подключить профиль Instagram",False):
            from .auth import connect_instagram
            token=ask("Long-lived токен официального Instagram Login",secret=True)
            token_file=path.parent/"secrets"/f"instagram-{count+1}.json"
            try:
                info=connect_instagram(token,token_file)
                account={"id":f"instagram-{count+1}","platform":"instagram","max_posts_per_day":3,"commercial":True,**info}
                config["accounts"].append(account);count+=1
                print("Instagram подключён: "+str(account.get("remote_id")))
            except (ValueError,RuntimeError,OSError,ImportError) as exc:
                print("Подключение не завершено: "+str(exc))
                if not yes("Повторить подключение",False):break
        if any(a["platform"]=="instagram" for a in config["accounts"]):
            print("Настройте доставку роликов в Instagram один раз. Подходит S3-совместимое хранилище.")
            bucket=ask("Название bucket")
            endpoint=ask("HTTPS endpoint хранилища (Enter для AWS)")
            region=ask("Регион","auto" if endpoint else "us-east-1")
            secret_values["S3_ACCESS_KEY_ID"]=ask("Storage access key",secret=True)
            secret_values["S3_SECRET_ACCESS_KEY"]=ask("Storage secret key",secret=True)
            config["storage"]={"provider":"s3","bucket":bucket,"endpoint_url":endpoint or None,
                  "region":region,"prefix":"viceclip","presign_seconds":86400,
                  "access_key_env":"S3_ACCESS_KEY_ID","secret_key_env":"S3_SECRET_ACCESS_KEY"}
        clips = int(ask("Сколько клипов готовить из одной записи (1–2)","2"))
        if not 1 <= clips <= 2:
            raise ValueError("Смысловой редактор готовит 1–2 лучших клипа за проход")
        config["autopilot"]["clips_per_source"] = clips
        posts=int(ask("Максимум публикаций в день на канал","3"))
        for account in config["accounts"]:account["max_posts_per_day"]=max(1,posts)
        config["autopilot"]["posting_gap_seconds"]=max(60,86400//max(1,posts))
        config["autopilot"]["execute"]=bool(config["accounts"]) and yes("Публиковать готовые ролики автоматически и публично",False)
    if language is not None:
        if language not in ("en", "ru"):
            raise ValueError("Язык выпуска должен быть en или ru")
        config["autopilot"]["editorial"]["output_language"] = language
    if youtube_privacy is not None:
        if youtube_privacy not in ("private", "unlisted", "public"):
            raise ValueError("Видимость YouTube должна быть private, unlisted или public")
        config["autopilot"]["privacy"] = youtube_privacy
    if inbox:
        config["autopilot"]["inboxes"][0]["path"]=str(Path(inbox).expanduser().resolve())
    if no_asr:config["autopilot"]["asr"]["enabled"]=False
    config["autopilot"]["accounts"]=[a["id"] for a in config["accounts"]]
    Path(config["autopilot"]["inboxes"][0]["path"]).mkdir(parents=True,exist_ok=True)
    if secret_values:private_json(config["secret_env_file"],secret_values)
    private_json(path,config)
    print("\nНастройка сохранена: "+str(path))
    import shlex
    suffix="" if path==default_config_path() else " --config "+shlex.quote(str(path))
    print("Запуск: viceclip autopilot start --persistent"+suffix)
    print("Проверка: viceclip autopilot status"+suffix)
    return {"config":str(path),"accounts":len(config["accounts"]),"mode":"publish" if config["autopilot"]["execute"] else "preview"}
