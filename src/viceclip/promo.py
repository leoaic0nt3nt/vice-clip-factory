"""One-time product/referral promotion settings for newly rendered YouTube clips."""
from pathlib import Path
from urllib.parse import urlparse

from .onboarding import private_json
from .rights import load_config

MAX_TEXT = 80


def validate(text, url, position="top"):
    if not isinstance(text, str) or any(ord(char) < 32 for char in text):
        raise ValueError("Текст плашки должен быть одной строкой")
    text = " ".join(text.split())
    if not text or len(text) > MAX_TEXT:
        raise ValueError(f"Напишите короткий текст плашки: от 1 до {MAX_TEXT} символов")
    if not isinstance(url, str) or any(char.isspace() for char in url):
        raise ValueError("Укажите полную HTTPS-ссылку на продукт или партнёрское предложение")
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Укажите HTTPS-ссылку без логина и пароля")
    if position not in ("top", "bottom"):
        raise ValueError("Позиция плашки: top или bottom")
    return {"enabled": True, "platforms": ["youtube"], "text": text,
            "url": url, "position": position, "sponsored": True}


def active_campaign(config):
    campaign = config.get("campaign") or {}
    if campaign.get("enabled") is not True:
        return None
    if campaign.get("platforms", ["youtube"]) != ["youtube"]:
        raise ValueError("Рекламная плашка поддерживается для YouTube; Instagram получает обычный ролик")
    if campaign.get("sponsored", True) is not True:
        raise ValueError("Рабочая реклама должна сохранять рекламный статус")
    return validate(campaign.get("text"), campaign.get("url"), campaign.get("position", "bottom"))


def base_style(config):
    style = dict(config.get("style", {}))
    if "campaign" in config:
        style.update(sponsor_text="", sponsored=False)
    return style


def youtube_style(style, campaign):
    return {**style, "sponsor_text": campaign["text"], "sponsor_position": campaign["position"], "sponsored": True}


def campaign_caption(campaign):
    text = campaign["text"]
    if "ссылка в профиле" not in text.lower():
        text += " · Ссылка в профиле"
    return "Реклама. " + text


def receipt(config):
    campaign = active_campaign(config)
    return {"enabled": bool(campaign), "campaign": campaign,
            "applies_to": "new_renders_only",
            "message": "Настройка применяется к новым видео. Готовые ролики и очередь не изменяются.",
            "profile_link_instruction": "Один раз добавьте вашу ссылку в профиль YouTube. Обычный URL в описании Shorts не кликабелен; программа профиль не редактирует.",
            "instagram": "Instagram получает обычную версию без рекламной плашки и рекламной подписи."}


def configure(config_path, *, text, url, position="top"):
    config = load_config(config_path)
    config["campaign"] = validate(text, url, position)
    private_json(config_path, config)
    return receipt(config)


def disable(config_path):
    config = load_config(config_path)
    config["campaign"] = {"enabled": False}
    config.setdefault("style", {}).update(sponsor_text="", sponsored=False)
    private_json(config_path, config)
    return receipt(config)


def status(config_path):
    return receipt(load_config(config_path))


def assets(result):
    """All immutable media referenced by a saved run, including platform variants."""
    for clip in result.get("clips", []):
        yield clip
        variants = clip.get("variants", {})
        if not isinstance(variants, dict):
            raise ValueError("Saved platform variants are invalid")
        for variant in variants.values():
            if not isinstance(variant, dict) or not variant.get("file") or not variant.get("sha256"):
                raise ValueError("Saved platform variant needs a file and SHA256")
            yield variant


def valid_result_files(result, fingerprint):
    return all(Path(item["file"]).is_file() and fingerprint(item["file"]) == item["sha256"] for item in assets(result))
