"""Official HTTP upload adapters. Tokens come exclusively from environment."""
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


class AmbiguousUpload(RuntimeError):
    """Remote side effect may have happened. Reconcile rather than retry."""


def request_json(url, token, *, method="GET", payload=None, timeout=60):
    body = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(url, data=body, method=method,
                                     headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"Provider HTTP {exc.code}") from None
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        raise RuntimeError("Provider network error or invalid response") from None


def token_for(account):
    """Refresh Google OAuth tokens from a private local file; Meta lifecycle is manual."""
    token_env = account.get("token_env", "")
    value = os.environ.get(token_env, "") if token_env else ""
    if value:
        return value
    filename = account.get("oauth_token_file")
    if not filename:
        raise ValueError(f"Missing OAuth access token environment variable: {token_env}")
    path = Path(filename).expanduser().resolve()
    if not path.is_file():
        raise ValueError("Private OAuth token file is missing")
    if path.stat().st_mode & 0o077:
        raise ValueError("OAuth token file must be private: chmod 600 <file>")
    state = json.loads(path.read_text(encoding="utf-8"))
    if state.get("access_token") and state.get("expires_at", 0) > time.time() + 60:
        return state["access_token"]
    if account["platform"] != "youtube":
        raise ValueError("Meta token expired: renew long-lived access manually before resuming")
    endpoint = state.get("token_uri", "https://oauth2.googleapis.com/token")
    if not endpoint.startswith("https://"):
        raise ValueError("OAuth token URI must use HTTPS")
    fields = {"client_id": state["client_id"], "client_secret": state["client_secret"],
              "refresh_token": state["refresh_token"], "grant_type": "refresh_token"}
    request = urllib.request.Request(endpoint, data=urllib.parse.urlencode(fields).encode(),
                                     headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            result = json.load(response)
    except Exception:
        raise ValueError("OAuth refresh failed; reauthorize this account") from None
    if not result.get("access_token"):
        raise ValueError("OAuth refresh returned no access token")
    state.update(result)
    state["expires_at"] = time.time() + result.get("expires_in", 3600)
    temporary = path.with_suffix(path.suffix + ".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(descriptor, "w") as stream:
            json.dump(state, stream)
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()
    return state["access_token"]


def youtube_upload(job, account, token, progress):
    expected_channel = account.get("remote_id", "")
    if not expected_channel or expected_channel.startswith("REPLACE_"):
        raise ValueError("Configure the expected YouTube channel ID before upload")
    try:
        identity = request_json("https://www.googleapis.com/youtube/v3/channels?part=id&mine=true", token)
    except RuntimeError:
        raise ValueError("Cannot verify expected YouTube channel before upload; check OAuth scopes") from None
    if expected_channel not in {item.get("id") for item in identity.get("items", [])}:
        raise ValueError("YouTube OAuth token belongs to a different channel; upload refused")
    path = Path(job["file"])
    metadata = job["metadata"]
    if path.stat().st_size > 300*1024*1024:
        raise ValueError("Rendered clip exceeds the MVP 300MiB upload limit")
    body = {"snippet": {"title": metadata["title"][:100], "description": metadata.get("caption", ""), "categoryId": "20"},
            "status": {"privacyStatus": metadata.get("privacy", "private"), "selfDeclaredMadeForKids": False}}
    if metadata.get("sponsored"):
        body["paidProductPlacementDetails"] = {"hasPaidProductPlacement": True}
    parts = "snippet,status" + (",paidProductPlacementDetails" if metadata.get("sponsored") else "")
    base = account.get("upload_base", "https://www.googleapis.com/upload/youtube/v3")
    url = base.rstrip("/") + "/videos?" + urllib.parse.urlencode({"uploadType": "resumable", "part": parts})
    request = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST", headers={
        "Authorization": "Bearer " + token, "Content-Type": "application/json; charset=UTF-8",
        "X-Upload-Content-Type": "video/mp4", "X-Upload-Content-Length": str(path.stat().st_size)})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            location = response.headers.get("Location")
        if not location or not location.startswith("https://"):
            raise RuntimeError("Provider did not return HTTPS upload session")
        progress("upload_started", location)
        request = urllib.request.Request(location, data=path.read_bytes(), method="PUT", headers={
            "Authorization": "Bearer " + token, "Content-Type": "video/mp4", "Content-Length": str(path.stat().st_size)})
        with urllib.request.urlopen(request, timeout=600) as response:
            result = json.load(response)
        if not result.get("id"):
            raise RuntimeError("Upload response missing video id")
        return result["id"]
    except Exception:
        raise AmbiguousUpload("YouTube outcome unknown; inspect channel and reconcile this job") from None


def verify_public_media(job):
    """Confirm the object Meta will fetch is the exact approved file, bounded at 300MiB."""
    import hashlib
    url = job["metadata"].get("public_url", "")
    if not url.startswith("https://"):
        raise ValueError("Instagram requires a publicly reachable HTTPS video URL")
    digest, size = hashlib.sha256(), 0
    try:
        request = urllib.request.Request(url, headers={"Accept-Encoding":"identity"})
        with urllib.request.urlopen(request, timeout=60) as response:
            if not response.geturl().startswith("https://"):
                raise ValueError("Public video URL redirected outside HTTPS")
            while True:
                chunk = response.read(1024*1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > 300*1024*1024:
                    raise ValueError("Public clip exceeds 300MiB verification limit")
                digest.update(chunk)
    except ValueError:
        raise
    except Exception:
        raise ValueError("Cannot verify public media bytes before Instagram upload") from None
    if not job.get("sha256") or digest.hexdigest() != job["sha256"]:
        raise ValueError("Public URL content does not match the approved clip SHA256")


def instagram_upload(job, account, token, progress):
    if job["metadata"].get("sponsored"):
        raise ValueError("Sponsored Instagram publishing disabled until paid partnership flow is implemented")
    public_url = job["metadata"].get("public_url", "")
    if not public_url.startswith("https://"):
        raise ValueError("Instagram requires a publicly reachable HTTPS video URL")
    base = account.get("api_base", "https://graph.instagram.com/v27.0").rstrip("/")
    account_id = urllib.parse.quote(str(account["remote_id"]), safe="")
    try:
        limit = request_json(f"{base}/{account_id}/content_publishing_limit?fields=config,quota_usage", token)
    except RuntimeError:
        raise ValueError("Cannot verify Instagram publishing limit before upload") from None
    for row in limit.get("data", []):
        total = row.get("config", {}).get("quota_total")
        if total is not None and row.get("quota_usage", 0) >= total:
            raise ValueError("Instagram publishing limit reached")
    if not limit.get("data"):
        raise ValueError("Instagram did not return a publishing limit; confirm permissions")
    verify_public_media(job)
    try:
        result = request_json(f"{base}/{account_id}/media", token, method="POST", payload={
            "media_type": "REELS", "video_url": public_url, "caption": job["metadata"].get("caption", ""), "share_to_feed": True})
        container_id = result.get("id")
        if not container_id:
            raise RuntimeError("Container response missing id")
        progress("container_created", container_id)
        for _ in range(30):
            status = request_json(f"{base}/{container_id}?fields=status_code", token)
            if status.get("status_code") == "FINISHED":
                break
            if status.get("status_code") in ("ERROR", "EXPIRED"):
                raise RuntimeError("Instagram processing failed")
            time.sleep(2)
        else:
            raise RuntimeError("Instagram processing timed out")
        progress("publish_started", container_id)
        result = request_json(f"{base}/{account_id}/media_publish", token, method="POST", payload={"creation_id": container_id})
        if not result.get("id"):
            raise RuntimeError("Publish response missing media id")
        return result["id"]
    except Exception:
        raise AmbiguousUpload("Instagram needs reconciliation; inspect stored container and account before retrying") from None


def upload(job, account, progress):
    token = token_for(account)
    if job["platform"] == "youtube":
        return youtube_upload(job, account, token, progress)
    if job["platform"] == "instagram":
        return instagram_upload(job, account, token, progress)
    raise ValueError("Unsupported platform")
