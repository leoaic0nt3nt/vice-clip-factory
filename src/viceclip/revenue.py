"""Local campaign tracking and a ledger of commissions reported by partners."""

import csv
import datetime as dt
import hashlib
import json
import os
import re
import sqlite3
from collections import Counter
from contextlib import closing, contextmanager
from pathlib import Path
from urllib.parse import unquote_plus, urlencode, urlsplit, urlunsplit

from . import promo


_COLUMNS = ("event_id", "campaign_id", "clip_id", "status", "amount", "currency", "updated_at")
_STATUSES = ("pending", "paid", "refunded", "rejected")
_QUEUED = ("pending", "approved", "uploading", "processing")
_OFFER = re.compile(r"offer-[0-9a-f]{12}")
_CLIP = re.compile(r"[0-9a-f]{64}")
_SCALE = 1_000_000


def campaign_id(campaign):
    digest = hashlib.sha256(campaign["url"].encode("utf-8")).hexdigest()
    return "offer-" + digest[:12]


def clip_campaign(campaign, clip_sha256):
    if not _CLIP.fullmatch(clip_sha256):
        raise ValueError("Укажите полный SHA256 мастер-файла ролика")
    return {"id": campaign_id(campaign), "clip_id": clip_sha256, "text": campaign["text"]}


def tracking_link(campaign, *, platform="youtube", clip_id=None):
    promo.validate(campaign["text"], campaign["url"], campaign.get("position", "bottom"))
    if platform not in ("youtube", "instagram"):
        raise ValueError("Источник перехода: youtube или instagram")
    if platform == "instagram" and clip_id is not None:
        raise ValueError("Для Instagram используйте общую ссылку профиля без --clip-id")
    if clip_id is not None and not _CLIP.fullmatch(clip_id):
        raise ValueError("Укажите полный SHA256 мастер-файла ролика")
    offer = campaign_id(campaign)
    tags = {"utm_source": platform, "utm_medium": "social", "utm_campaign": offer,
            "utm_content": clip_id or "profile"}
    url = urlsplit(campaign["url"])
    # Keep partner parameters byte-for-byte, including repeated keys and escaped values.
    query = [piece for piece in url.query.split("&")
             if piece and unquote_plus(piece.partition("=")[0]) not in tags]
    query.append(urlencode(tags))
    return {"campaign_id": offer, "clip_id": clip_id,
            "url": urlunsplit(url._replace(query="&".join(query)))}


def _event(row):
    event_id, offer, clip, status, amount, currency, stamp = (row[name].strip() for name in _COLUMNS)
    provider, _, identifier = event_id.partition(":")
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,50}", provider) or not identifier or len(event_id) > 256 or not event_id.isprintable():
        raise ValueError("event_id должен содержать код сервиса и ID события")
    if not _OFFER.fullmatch(offer) or (clip and not _CLIP.fullmatch(clip)):
        raise ValueError("Укажите campaign_id из Vice Clip Factory и полный clip_id либо оставьте clip_id пустым")
    if status not in _STATUSES:
        raise ValueError("status должен быть pending, paid, refunded или rejected")
    if not re.fullmatch(r"[A-Z]{3}", currency) or not re.fullmatch(r"[0-9]{1,12}(?:\.[0-9]{1,6})?", amount):
        raise ValueError("Укажите код валюты и сумму от 0 с точкой и до шести знаков после неё")
    main, _, decimal = amount.partition(".")
    amount_units = int(main) * _SCALE + int(decimal.ljust(6, "0"))
    try:
        timestamp = dt.datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        if timestamp.tzinfo is None:
            raise ValueError
        updated_at = timestamp.astimezone(dt.timezone.utc).isoformat(timespec="microseconds")
    except (ValueError, OverflowError):
        raise ValueError("updated_at должен быть временем ISO 8601 с часовым поясом") from None
    return event_id, offer, clip or None, status, amount_units, currency, updated_at


def _events(csv_path):
    try:
        with Path(csv_path).expanduser().open(encoding="utf-8-sig", newline="") as stream:
            rows = csv.DictReader(stream, strict=True)
            names = rows.fieldnames or []
            if len(set(names)) != len(names) or not set(_COLUMNS).issubset(names):
                raise ValueError("Добавьте столбцы CSV: " + ",".join(_COLUMNS))
            for row in rows:
                try:
                    if None in row or any(row[name] is None for name in _COLUMNS):
                        raise ValueError("Количество полей отличается от заголовка CSV")
                    yield rows.line_num, _event(row)
                except ValueError as exc:
                    raise ValueError(f"CSV, строка {rows.line_num}: {exc}") from None
    except (csv.Error, UnicodeError) as exc:
        raise ValueError(f"Нужен корректный CSV в UTF-8: {exc}") from None


def import_conversions(ledger_path, csv_path):
    """Import a whole CSV atomically and apply only newer event snapshots."""
    path = Path(ledger_path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path, timeout=20)) as db:
        os.chmod(path, 0o600)
        with db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("""CREATE TABLE IF NOT EXISTS conversions (
                event_id TEXT PRIMARY KEY, campaign_id TEXT NOT NULL, clip_id TEXT,
                status TEXT NOT NULL, amount_units INTEGER NOT NULL, currency TEXT NOT NULL,
                updated_at TEXT NOT NULL)""")
            stats = {"inserted": 0, "updated": 0, "unchanged": 0, "older": 0}
            for line, event in _events(csv_path):
                previous = db.execute("SELECT * FROM conversions WHERE event_id=?", (event[0],)).fetchone()
                if previous is None:
                    db.execute("INSERT INTO conversions VALUES (?,?,?,?,?,?,?)", event)
                    stats["inserted"] += 1
                elif event == previous:
                    stats["unchanged"] += 1
                elif event[-1] == previous[-1]:
                    raise ValueError(f"CSV, строка {line}: разные данные события с одинаковым updated_at")
                elif event[-1] < previous[-1]:
                    stats["older"] += 1
                else:
                    db.execute("REPLACE INTO conversions VALUES (?,?,?,?,?,?,?)", event)
                    stats["updated"] += 1
    return stats


@contextmanager
def _read_database(path, table):
    if path is None or not Path(path).expanduser().is_file():
        yield None
        return
    uri = Path(path).expanduser().resolve().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, timeout=20, uri=True)) as db:
        db.row_factory = sqlite3.Row
        exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
        yield db if exists else None


def _summary(events):
    statuses = Counter(event["status"] for event in events)
    paid = Counter()
    for event in events:
        if event["status"] == "paid":
            paid[event["currency"]] += event["amount_units"]
    amounts = {}
    for currency, value in sorted(paid.items()):
        units, decimal = divmod(value, _SCALE)
        amounts[currency] = f"{units}.{decimal:06d}".rstrip("0").rstrip(".")
    return {"conversions_by_status": {status: statuses[status] for status in _STATUSES},
            "net_paid_commission_by_currency": amounts}


def _offer(offers, offer_id, text=None):
    offer = offers.setdefault(offer_id, {"clips": {}, "events": []})
    if text:
        offer["text"] = text
    return offer


def _clip(offer, clip_id, title=None):
    clip = offer["clips"].setdefault(clip_id, {"produced": False, "jobs": [], "events": []})
    if title:
        clip["title"] = title
    return clip


def _production(path, offers):
    with _read_database(path, "media_jobs") as db:
        if db is None:
            return {"available": False, "produced_clips": None, "campaign_variants": None}
        clips = variants = 0
        for row in db.execute("SELECT result FROM media_jobs WHERE result IS NOT NULL ORDER BY updated_at,key"):
            for clip in json.loads(row["result"]).get("clips", []):
                clips += 1
                variant = clip.get("variants", {}).get("youtube", {})
                variants += bool(variant.get("sponsored"))
                campaign = variant.get("campaign")
                if campaign:
                    offer = _offer(offers, campaign["id"], campaign.get("text"))
                    _clip(offer, campaign["clip_id"], clip.get("title"))["produced"] = True
    return {"available": True, "produced_clips": clips, "campaign_variants": variants}


def _publication(path, offers):
    with _read_database(path, "jobs") as db:
        if db is None:
            return {"available": False, "jobs_total": 0, "accounts": []}
        columns = {row["name"] for row in db.execute("PRAGMA table_info(jobs)")}
        verified = "playback_verified" if "playback_verified" in columns else "0"
        accounts = {}
        jobs_total = 0
        for row in db.execute(f"""SELECT id, account, platform, status, remote_id, metadata,
                             {verified} AS verified FROM jobs ORDER BY id"""):
            jobs_total += 1
            key = (row["account"], row["platform"])
            account = accounts.setdefault(key, {"statuses": Counter(), "playback_verified": 0})
            account["statuses"][row["status"]] += 1
            account["playback_verified"] += bool(row["verified"])
            metadata = json.loads(row["metadata"])
            campaign = metadata.get("campaign")
            if campaign:
                offer = _offer(offers, campaign["id"], campaign.get("text"))
                clip = _clip(offer, campaign["clip_id"], metadata.get("title"))
                clip["jobs"].append({"id": row["id"], "account": row["account"], "platform": row["platform"],
                                     "status": row["status"], "remote_id": row["remote_id"]})
        breakdown = []
        for (account, platform), counts in sorted(accounts.items()):
            statuses = counts["statuses"]
            breakdown.append({"account": account, "platform": platform,
                              "queued": sum(statuses[status] for status in _QUEUED),
                              "uploaded": statuses["uploaded"], "published": statuses["published"],
                              "playback_verified": counts["playback_verified"], "jobs_by_status": dict(statuses)})
    return {"available": True, "jobs_total": jobs_total, "accounts": breakdown}


def validate_clip_campaign(campaign, clip_sha, *, queue_path, state_path):
    """Require a saved render or queue entry for the active offer and clip."""
    offer_id = clip_campaign(campaign, clip_sha)["id"]
    known = {}
    _production(state_path, known)
    _publication(queue_path, known)
    if clip_sha in known.get(offer_id, {}).get("clips", {}):
        return
    if any(clip_sha in offer["clips"] for offer in known.values()):
        raise ValueError("Клип сохранён с другим оффером. Сверьте campaign_id в viceclip revenue report")
    raise ValueError("Для этого clip_id нет сохранённой кампании. Возьмите clip_id из viceclip revenue report")


def report(queue_path, *, state_path=None, ledger_path=None):
    """Summarize local production, provider job states, and imported partner data."""
    offers = {}
    production = _production(state_path, offers)
    publication = _publication(queue_path, offers)
    with _read_database(ledger_path, "conversions") as db:
        events = list(db.execute("SELECT * FROM conversions")) if db is not None else []
        available = db is not None
    for event in events:
        offer = _offer(offers, event["campaign_id"])
        offer["events"].append(event)
        if event["clip_id"]:
            _clip(offer, event["clip_id"])["events"].append(event)
    campaigns = []
    for offer_id, offer in sorted(offers.items()):
        clips = [{"clip_id": clip_id, "title": clip.get("title"), "produced": clip["produced"],
                  "jobs": clip["jobs"], **_summary(clip["events"])} for clip_id, clip in sorted(offer["clips"].items())]
        campaigns.append({"campaign_id": offer_id, "text": offer.get("text"), "clips": clips,
                          "campaign_only": _summary([event for event in offer["events"] if not event["clip_id"]]),
                          **_summary(offer["events"])})
    return {"production": production, "publication": publication,
            "partner_ledger": {"available": available, **_summary(events)}, "campaigns": campaigns}
