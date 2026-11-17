"""Official upload adapters with renewable account credentials and automatic media delivery."""
import http.client
import json
import re
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


_YOUTUBE_API = "https://www.googleapis.com/youtube/v3"
_YOUTUBE_UPLOAD_BASE = "https://www.googleapis.com/upload/youtube/v3"
_YOUTUBE_QUOTA_REASONS = {
    "dailyLimitExceeded", "quotaExceeded", "rateLimitExceeded", "userRateLimitExceeded", "uploadLimitExceeded",
}


def _youtube_channel(account, token):
    expected_channel = account.get("remote_id", "")
    if not expected_channel or expected_channel.startswith("REPLACE_"):
        raise ValueError("Configure the expected YouTube channel ID before upload")
    try:
        identity = request_json(_YOUTUBE_API + "/channels?part=id,snippet&mine=true", token)
    except RuntimeError:
        raise ValueError("Cannot verify expected YouTube channel before upload; check OAuth scopes") from None
    for item in identity.get("items", []):
        if item.get("id") == expected_channel:
            return item
    raise ValueError("YouTube OAuth token belongs to a different channel; upload refused")


def youtube_readiness(account, *, video_id=None, expected_privacy=None):
    """Read channel identity and optional video state using the owner's OAuth credential."""
    if account.get("platform") != "youtube":
        raise ValueError("Choose a connected YouTube account")
    if video_id is not None and not re.fullmatch(r"[A-Za-z0-9_-]{11}", str(video_id)):
        raise ValueError("Enter the YouTube video ID")
    if expected_privacy is not None and expected_privacy not in ("public", "private", "unlisted"):
        raise ValueError("Expected visibility must be public, private or unlisted")
    token = token_for(account)
    channel = _youtube_channel(account, token)
    report = {
        "account": account.get("id"), "channel_id": channel["id"],
        "channel_name": channel.get("snippet", {}).get("title"),
        "oauth_channel_verified": True, "upload_tested": False,
    }
    if video_id is None:
        return report
    query = urllib.parse.urlencode({"part": "snippet,status,processingDetails,paidProductPlacementDetails", "id": video_id})
    response = request_json(_YOUTUBE_API + "/videos?" + query, token)
    video = next((item for item in response.get("items", []) if item.get("id") == video_id), None)
    if video is None or video.get("snippet", {}).get("channelId") != channel["id"]:
        raise ValueError("This video was not found on the connected YouTube channel")
    status = video.get("status", {})
    privacy = status.get("privacyStatus")
    report["video"] = {
        "id": video_id,
        "privacy": privacy,
        "expected_privacy": expected_privacy,
        "privacy_matches": privacy == expected_privacy if expected_privacy is not None and privacy is not None else None,
        "upload_status": status.get("uploadStatus"),
        "processing_status": video.get("processingDetails", {}).get("processingStatus"),
        "publish_at": status.get("publishAt"),
        "paid_product_placement": video.get("paidProductPlacementDetails", {}).get("hasPaidProductPlacement"),
        "playback_verified": False,
    }
    return report


def _youtube_endpoint(url):
    parsed = urllib.parse.urlsplit(url)
    if (parsed.scheme != "https" or parsed.netloc not in ("www.googleapis.com", "youtube.googleapis.com")
            or not parsed.path.startswith("/upload/youtube/v3/")):
        raise ValueError("YouTube returned an invalid official upload endpoint")
    return url


def _youtube_retry_after(headers, default=30):
    if headers:
        value = headers.get("Retry-After", "")
        if value.isascii() and value.isdigit() and len(value) <= 6:
            return max(default, int(value))
    return default


def _youtube_error_reason(error):
    try:
        data = json.loads(error.read(32768))
        reason = data.get("error", {}).get("errors", [{}])[0].get("reason")
        return reason if isinstance(reason, str) and re.fullmatch(r"[A-Za-z]+", reason) else None
    except (AttributeError, IndexError, OSError, TypeError, ValueError):
        return None
    finally:
        error.close()


def _youtube_begin(account, token, body, size):
    base = account.get("upload_base", _YOUTUBE_UPLOAD_BASE)
    url = _youtube_endpoint(base.rstrip("/") + "/videos?" + urllib.parse.urlencode({
        "uploadType": "resumable", "part": "snippet,status,paidProductPlacementDetails",
    }))
    request = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST", headers={
        "Authorization": "Bearer " + token, "Content-Type": "application/json; charset=UTF-8",
        "X-Upload-Content-Type": "video/mp4", "X-Upload-Content-Length": str(size),
    })
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            location = response.headers.get("Location")
    except urllib.error.HTTPError as error:
        delay = _youtube_retry_after(error.headers, default=3600)
        reason = _youtube_error_reason(error)
        if reason in _YOUTUBE_QUOTA_REASONS or error.code == 429:
            raise DeferredUpload("YouTube upload quota or channel upload limit reached", delay) from None
        if error.code == 408 or error.code >= 500:
            raise DeferredUpload("YouTube session temporarily unavailable before sending any video bytes", 60) from None
        # An HTTP refusal of the initiation request has not transferred media.
        detail = f"HTTP {error.code}" + (f", {reason}" if reason else "")
        raise RejectedUpload(f"YouTube rejected session setup ({detail}); check credentials and metadata") from None
    except (OSError, http.client.HTTPException):
        raise DeferredUpload("YouTube session temporarily unavailable before sending any video bytes", 60) from None
    if not location:
        raise RejectedUpload("YouTube did not return a resumable upload session")
    return _youtube_endpoint(location)


def _youtube_transfer(location, token, size, *, source=None, offset=0):
    """Send media to or query an existing session. Returns video ID or the confirmed byte offset."""
    headers = {"Authorization": "Bearer " + token}
    if source is None:
        headers.update({"Content-Length": "0", "Content-Range": f"bytes */{size}"})
    else:
        source.seek(offset)
        headers.update({"Content-Type": "video/mp4", "Content-Length": str(size - offset)})
        if offset:
            headers["Content-Range"] = f"bytes {offset}-{size - 1}/{size}"
    request = urllib.request.Request(location, data=source, method="PUT", headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=60 if source is None else 600) as response:
            result = json.load(response)
    except urllib.error.HTTPError as error:
        if error.code == 308:
            received = error.headers.get("Range", "") if error.headers else ""
            delay = _youtube_retry_after(error.headers)
            explicit_wait = error.headers and error.headers.get("Retry-After")
            error.close()
            if source is not None or explicit_wait:
                raise PendingUpload("YouTube upload is incomplete; the same session will resume", delay) from None
            match = re.fullmatch(r"bytes=0-(\d+)", received)
            if not received:
                return 0
            if match and len(match[1]) <= 12 and int(match[1]) + 1 < size:
                return int(match[1]) + 1
            raise AmbiguousUpload("YouTube returned an inconsistent byte range; inspect this upload before retrying") from None
        delay = _youtube_retry_after(error.headers, default=30)
        reason = _youtube_error_reason(error)
        if error.code in (408, 429) or error.code >= 500 or reason in _YOUTUBE_QUOTA_REASONS:
            if reason in _YOUTUBE_QUOTA_REASONS:
                delay = max(delay, 3600)
            raise PendingUpload("YouTube session will be checked before sending more media", delay) from None
        # In particular, an expired session may have successfully completed earlier.
        raise AmbiguousUpload(f"YouTube session status could not be confirmed (HTTP {error.code}); inspect Studio") from None
    except (OSError, http.client.HTTPException):
        raise PendingUpload("YouTube connection interrupted; the same session will be checked", 30) from None
    except (ValueError, UnicodeError):
        raise AmbiguousUpload("YouTube returned an unreadable result; inspect Studio before retrying") from None
    video_id = result.get("id") if isinstance(result, dict) else None
    if not isinstance(video_id, str) or not video_id.strip():
        raise AmbiguousUpload("YouTube accepted the request without confirming a video ID; inspect Studio")
    return video_id


def youtube_upload(job, account, token, progress):
    _youtube_channel(account, token)
    path = Path(job["file"])
    metadata = job["metadata"]
    size = path.stat().st_size
    if not 0 < size <= 300 * 1024 * 1024:
        raise ValueError("YouTube requires a nonempty clip within the 300MiB upload limit")
    privacy = metadata.get("privacy", "private")
    if privacy not in ("public", "private", "unlisted"):
        raise ValueError("YouTube visibility must be public, private or unlisted")
    body = {"snippet": {"title": metadata["title"][:100], "description": metadata.get("caption", ""), "categoryId": "20"},
            "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": False},
            "paidProductPlacementDetails": {"hasPaidProductPlacement": bool(metadata.get("sponsored"))}}
    if "language" in metadata:
        if metadata["language"] not in ("en", "ru"):
            raise ValueError("Video metadata language must be en or ru")
        body["snippet"]["defaultLanguage"] = metadata["language"]

    if job.get("stage") == "upload_started":
        try:
            location = _youtube_endpoint(job.get("session_uri") or "")
        except ValueError:
            raise AmbiguousUpload("Saved YouTube session is unavailable; inspect Studio before retrying") from None
        prior = _youtube_transfer(location, token, size)
        if isinstance(prior, str):
            return prior
        offset = prior
    else:
        location = _youtube_begin(account, token, body, size)
        progress("upload_started", location)
        offset = 0
    with path.open("rb") as source:
        return _youtube_transfer(location, token, size, source=source, offset=offset)


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
