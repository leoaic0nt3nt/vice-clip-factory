"""Selected source settings and backwards-compatible legacy permissions."""
from pathlib import Path
import datetime as dt
import json


def load_config(path):
    config = json.loads(Path(path).read_text(encoding="utf-8"))
    secret_file = config.get("secret_env_file") if isinstance(config,dict) else None
    if secret_file:
        import os
        import re
        secret = Path(secret_file).expanduser()
        if not secret.is_absolute():
            secret = Path(path).resolve().parent / secret
        if secret.exists():
            if secret.stat().st_mode & 0o077:
                raise ValueError("Файл подключения должен быть приватным: chmod 600 " + str(secret))
            values = json.loads(secret.read_text(encoding="utf-8"))
            if not isinstance(values,dict):
                raise ValueError("Файл подключения должен содержать JSON-объект")
            for key,value in values.items():
                if not re.fullmatch(r"[A-Z][A-Z0-9_]*",key) or not isinstance(value,str):
                    raise ValueError("Некорректное имя или значение настройки подключения")
                if value:
                    os.environ[key] = value
    return config


def check_rights(config, source_id, action, platform=None, commercial=False, sponsored=False):
    source = next((s for s in config.get("sources", []) if s["id"] == source_id), None)
    if not source:
        raise ValueError(f"Источник не выбран: {source_id}")
    policy = config.get("source_policy", {})
    if policy.get("mode") == "configured_sources":
        if policy.get("user_responsible") is not True:
            raise ValueError("Завершите мастер первоначальной настройки")
        if source.get("enabled",True) is not True:
            raise ValueError("Источник отключён")
        if source.get(f"allow_{action}",True) is not True:
            raise ValueError("Это действие отключено для источника")
        destinations = source.get("platforms", [a.get("platform") for a in config.get("accounts",[])])
        if platform and platform not in destinations:
            raise ValueError("Площадка не выбрана для этого источника")
        if sponsored and platform == "instagram":
            raise ValueError("Рекламная публикация Instagram требует отдельного partnership-подключения")
        return source
    permission = source.get("permission", {})
    if permission.get("status") not in ("owned", "licensed"):
        raise ValueError("Permission must be owned or licensed; discovery is not permission")
    if not permission.get("reference"):
        raise ValueError("A permission reference is required")
    if permission.get(f"allow_{action}") is not True:
        raise ValueError(f"Permission does not allow {action}")
    if permission.get("expires_at"):
        expiry = dt.datetime.fromisoformat(permission["expires_at"].replace("Z", "+00:00"))
        if expiry.tzinfo is None or expiry <= dt.datetime.now(dt.timezone.utc):
            raise ValueError("Permission expired or expiry lacks timezone")
    if platform and platform not in source.get("platforms", []):
        raise ValueError(f"Source is not cleared for platform {platform}")
    if sponsored and platform == "instagram":
        raise ValueError("Sponsored Instagram publishing is disabled: paid partnership workflow is not implemented")
    if (commercial or sponsored) and permission.get("publisher_commercial") is not True:
        raise ValueError("Commercial use needs a separate game publisher permission attestation")
    if sponsored and permission.get("allow_sponsorship") is not True:
        raise ValueError("Source is not cleared for sponsorship")
    return source


def get_account(config, account_id):
    account = next((a for a in config.get("accounts", []) if a["id"] == account_id), None)
    if not account or account.get("platform") not in ("youtube", "instagram"):
        raise ValueError("Unknown account or unsupported platform")
    return account
