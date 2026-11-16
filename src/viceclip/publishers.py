"""Official upload adapters with renewable account credentials and automatic media delivery."""
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


class AmbiguousUpload(RuntimeError):
    """Remote side effect may have happened. Reconcile rather than retry."""


class PendingUpload(RuntimeError):
    """A known container is processing; resume that resource on a later tick."""
    def __init__(self, message, delay_seconds=30):
        super().__init__(message)
        self.delay_seconds = delay_seconds


class DeferredUpload(ValueError):
    """Known quota refusal before publication; wait without creating a duplicate."""
    def __init__(self, message, delay_seconds=3600):
        super().__init__(message)
        self.delay_seconds = delay_seconds


class RejectedUpload(ValueError):
    """Provider explicitly rejected processing before media_publish was called."""


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
    from .auth import token_for as renewable_token
    return renewable_token(account)


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
    from .storage import configured, deliver
    container_id = job.get("remote_id") if job.get("stage") == "container_created" else None
    public_url = job["metadata"].get("public_url") or ""
    if not container_id and not public_url.startswith("https://") and not configured(account.get("_storage")):
        raise ValueError("Connect cloud media delivery for this Instagram account")
    base = account.get("api_base", "https://graph.instagram.com/v27.0").rstrip("/")
    account_id = urllib.parse.quote(str(account["remote_id"]), safe="")
    try:
        limit = request_json(f"{base}/{account_id}/content_publishing_limit?fields=config,quota_usage", token)
    except RuntimeError:
        raise ValueError("Cannot verify Instagram publishing limit before upload") from None
    for row in limit.get("data", []):
        total = row.get("config", {}).get("quota_total")
        if total is not None and row.get("quota_usage", 0) >= total:
            raise DeferredUpload("Instagram publishing limit reached; waiting for quota")
    if not limit.get("data"):
        raise ValueError("Instagram did not return a publishing limit; confirm permissions")
    if urllib.parse.urlparse(base).hostname == "graph.instagram.com":
        from .auth import instagram_identity
        try:
            identity = request_json(base + "/me?fields=id,user_id,username", token)
            connected_id, _ = instagram_identity(identity)
        except (RuntimeError, ValueError):
            raise ValueError("Cannot verify the connected Instagram account before publication") from None
        if connected_id != str(account["remote_id"]):
            raise ValueError("Instagram credential belongs to a different account; publication refused")
    if not container_id:
        if not public_url:
            delivered = deliver(job["file"], account["_storage"], sha256=job["sha256"])
            public_url = delivered["public_url"]
            # Signed URL exists only for this attempt; never persist in public output.
            job["metadata"]["public_url"] = public_url
        verify_public_media(job)
        try:
            result = request_json(f"{base}/{account_id}/media", token, method="POST", payload={
                "media_type": "REELS", "video_url": public_url, "caption": job["metadata"].get("caption", ""), "share_to_feed": True})
            container_id = result.get("id")
            if not container_id:
                raise RuntimeError("Container response missing id")
            progress("container_created", container_id)
        except Exception:
            raise AmbiguousUpload("Instagram container creation outcome unknown; reconcile before creating another") from None
    attempts = max(1, min(30, int(account.get("processing_poll_attempts", 3))))
    interval = max(0, min(10, float(account.get("processing_poll_interval", 2))))
    for attempt in range(attempts):
        try:
            status = request_json(f"{base}/{container_id}?fields=status_code", token)
        except RuntimeError:
            raise PendingUpload("Instagram status temporarily unavailable; the same container will be checked again") from None
        code = status.get("status_code")
        if code == "FINISHED":
            break
        if code in ("ERROR", "EXPIRED"):
            progress("container_failed", container_id)
            raise RejectedUpload("Instagram explicitly rejected or expired this media container")
        if code == "PUBLISHED":
            raise AmbiguousUpload("Instagram container is already published; verify its media ID before reconciliation")
        if attempt + 1 < attempts:
            time.sleep(interval)
    else:
        raise PendingUpload("Instagram is processing; the same container will resume automatically")
    progress("publish_started", container_id)
    try:
        result = request_json(f"{base}/{account_id}/media_publish", token, method="POST", payload={"creation_id": container_id})
        if not result.get("id"):
            raise RuntimeError("Publish response missing media id")
        return result["id"]
    except Exception:
        raise AmbiguousUpload("Instagram final publication outcome unknown; reconcile before retrying") from None


def upload(job, account, progress):
    token = token_for(account)
    if job["platform"] == "youtube":
        return youtube_upload(job, account, token, progress)
    if job["platform"] == "instagram":
        return instagram_upload(job, account, token, progress)
    raise ValueError("Unsupported platform")
