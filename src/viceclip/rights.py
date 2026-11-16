"""Explicit source permissions, rechecked immediately before side effects."""
from pathlib import Path
import datetime as dt
import json


def load_config(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def check_rights(config, source_id, action, platform=None, commercial=False, sponsored=False):
    source = next((s for s in config.get("sources", []) if s["id"] == source_id), None)
    if not source:
        raise ValueError(f"Source is not allowlisted: {source_id}")
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
