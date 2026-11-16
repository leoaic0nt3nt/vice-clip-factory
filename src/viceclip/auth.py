"""One-time account connection and unattended refresh using official OAuth endpoints."""
import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
import time
import urllib.parse
import urllib.request


YOUTUBE_SCOPES = ["https://www.googleapis.com/auth/youtube.upload",
                  "https://www.googleapis.com/auth/youtube.readonly"]
_TWITCH_APP_TOKENS = {}


def write_private_json(filename, value):
    path = Path(filename).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return str(path)


def _http_json(url, *, token=None, fields=None, authorization_scheme="Bearer"):
    headers = {"Authorization": authorization_scheme + " " + token} if token else {}
    body = None
    if fields is not None:
        body = urllib.parse.urlencode(fields).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    request = urllib.request.Request(url, data=body, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)
    except Exception:
        # Provider error bodies and request URLs may contain credentials.
        raise ValueError("Account authorization request failed; check app settings and reconnect the account") from None


def twitch_token(creator, *, force=False):
    """Get/renew a public-metadata app token; keep old supplied-token configs usable.

    Each operator supplies their own Twitch application credentials once. App
    secrets are never returned, logged, or embedded in a distributed client.
    """
    client = os.environ.get(creator.get("client_id_env", "TWITCH_CLIENT_ID"), "")
    secret = os.environ.get(creator.get("client_secret_env", "TWITCH_CLIENT_SECRET"), "")
    if not secret:
        supplied = os.environ.get(creator.get("token_env", "TWITCH_ACCESS_TOKEN"), "")
        if client and supplied:
            return supplied
        raise ValueError("Connect Twitch with its application Client ID and Client Secret")
    if not client:
        raise ValueError("Twitch Client ID is missing")
    cache_key = (client, hashlib.sha256(secret.encode()).hexdigest())
    now = time.time()
    cached = _TWITCH_APP_TOKENS.get(cache_key)
    if not force and cached and cached["expires_at"] > now + 60:
        if now - cached["validated_at"] < 3600:
            return cached["token"]
        try:
            checked = _http_json("https://id.twitch.tv/oauth2/validate", token=cached["token"],
                                 authorization_scheme="OAuth")
            if checked.get("client_id") != client or float(checked.get("expires_in", 0)) <= 60:
                raise ValueError("Twitch app token no longer valid")
            cached.update(validated_at=now, expires_at=now + float(checked["expires_in"]))
            return cached["token"]
        except ValueError:
            _TWITCH_APP_TOKENS.pop(cache_key, None)
    result = _http_json("https://id.twitch.tv/oauth2/token", fields={
        "client_id": client, "client_secret": secret, "grant_type": "client_credentials"})
    token = result.get("access_token")
    try:
        lifetime = float(result.get("expires_in", 0))
    except (TypeError, ValueError):
        lifetime = 0
    if not token or not math.isfinite(lifetime) or lifetime <= 60:
        raise ValueError("Twitch did not return a valid renewable app token")
    _TWITCH_APP_TOKENS[cache_key] = {"token": token, "expires_at": now + lifetime, "validated_at": now}
    return token


def instagram_identity(data):
    row = data
    if isinstance(data.get("data"), list):
        row = data["data"][0] if data["data"] else {}
    remote_id = row.get("user_id") or row.get("id")
    if not remote_id:
        raise ValueError("Instagram did not return a professional account ID")
    return str(remote_id), str(row.get("username", ""))


def connect_youtube(client_secrets_file, token_file):
    """Open official browser consent with PKCE and save a renewable channel credential."""
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        raise ValueError("Install account connection: pip install '.[connect]'") from None
    flow = InstalledAppFlow.from_client_secrets_file(str(client_secrets_file),
            scopes=YOUTUBE_SCOPES, autogenerate_code_verifier=True)
    credentials = flow.run_local_server(host="127.0.0.1", port=0, open_browser=True,
            access_type="offline", prompt="consent", timeout_seconds=300)
    if not credentials.refresh_token:
        raise ValueError("Google did not grant offline access; reconnect with consent to receive a refresh token")
    identity = _http_json("https://www.googleapis.com/youtube/v3/channels?part=id,snippet&mine=true",
                          token=credentials.token)
    channels = identity.get("items", [])
    if len(channels) != 1 or not channels[0].get("id"):
        raise ValueError("Select one YouTube channel during Google authorization, then reconnect")
    channel = channels[0]
    expiry = credentials.expiry
    if expiry is None:
        expires_at = time.time() + 3000
    else:
        expires_at = expiry.replace(tzinfo=dt.timezone.utc).timestamp() if expiry.tzinfo is None else expiry.timestamp()
    saved = write_private_json(token_file, {
        "platform": "youtube", "remote_id": channel["id"],
        "client_id": credentials.client_id, "client_secret": credentials.client_secret,
        "refresh_token": credentials.refresh_token, "access_token": credentials.token,
        "token_uri": "https://oauth2.googleapis.com/token", "expires_at": expires_at,
    })
    return {"platform": "youtube", "remote_id": channel["id"],
            "username": channel.get("snippet", {}).get("title", ""), "oauth_token_file": saved}


def connect_instagram(access_token, token_file, api_base="https://graph.instagram.com/v27.0", expires_in=5184000):
    """Connect a long-lived token from Meta's official account dashboard/login.

    Meta app secrets are not embedded or distributed in this desktop client.
    Dashboard tokens are long-lived; a short-lived login token must be exchanged
    by the app owner's server before this method is called.
    """
    base = str(api_base).rstrip("/")
    parsed = urllib.parse.urlparse(base)
    if parsed.scheme != "https" or parsed.netloc != "graph.instagram.com":
        raise ValueError("Instagram Login connection requires the official graph.instagram.com HTTPS API")
    if not access_token or not 86400 < int(expires_in) <= 5184000:
        raise ValueError("Provide an official long-lived Instagram token with a valid expiry")
    remote_id, username = instagram_identity(_http_json(base + "/me?fields=id,user_id,username", token=access_token))
    now = time.time()
    saved = write_private_json(token_file, {"platform": "instagram", "remote_id": remote_id,
        "username": username, "access_token": access_token, "token_type": "instagram_long_lived",
        "issued_at": now, "expires_at": now + int(expires_in), "api_base": base})
    return {"platform": "instagram", "remote_id": remote_id, "username": username,
            "oauth_token_file": saved, "api_base": base, "login_method": "instagram"}


def token_for(account):
    env_name = account.get("token_env", "")
    if env_name and os.environ.get(env_name):
        return os.environ[env_name]
    filename = account.get("oauth_token_file")
    if not filename:
        raise ValueError("Connect this account first; no renewable credential is configured")
    path = Path(filename).expanduser().resolve()
    if not path.is_file() or path.stat().st_mode & 0o077:
        raise ValueError("OAuth token file must exist and be private (chmod 600)")
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(state, dict):
            raise ValueError("Invalid token record")
        now, expiry = time.time(), float(state.get("expires_at", 0))
        issued_at = float(state.get("issued_at", now))
        if not math.isfinite(expiry) or not math.isfinite(issued_at):
            raise ValueError("Invalid credential timestamps")
    except (OSError, ValueError, TypeError):
        raise ValueError("Account credential file is invalid; reconnect this account") from None
    if state.get("remote_id") and account.get("remote_id") and str(state["remote_id"]) != str(account["remote_id"]):
        raise ValueError("Credential file belongs to a different configured account")
    if account["platform"] == "instagram":
        if not state.get("access_token") or expiry <= now:
            raise ValueError("Instagram credential expired; reconnect this account")
        refreshable = state.get("token_type") == "instagram_long_lived" and now - issued_at >= 86400
        if expiry > now + 7 * 86400 or not refreshable:
            return state["access_token"]
        url = "https://graph.instagram.com/refresh_access_token?" + urllib.parse.urlencode({
            "grant_type": "ig_refresh_token", "access_token": state["access_token"]})
        try:
            result = _http_json(url)
        except ValueError:
            if expiry > now + 3600:
                return state["access_token"]  # A transient refresh failure need not stop a still-valid account.
            raise
        if not result.get("access_token"):
            raise ValueError("Instagram refresh returned no credential; reconnect this account")
        state.update(result)
        state.update(issued_at=now, expires_at=now + int(result.get("expires_in", 5184000)))
    elif account["platform"] == "youtube":
        if state.get("access_token") and expiry > now + 60:
            return state["access_token"]
        fields = {"client_id": state.get("client_id"), "client_secret": state.get("client_secret"),
                  "refresh_token": state.get("refresh_token"), "grant_type": "refresh_token"}
        if not all(fields.values()):
            raise ValueError("Google offline credential is incomplete; reconnect this account")
        result = _http_json("https://oauth2.googleapis.com/token", fields=fields)
        if not result.get("access_token"):
            raise ValueError("Google refresh returned no credential; reconnect this account")
        state.update(result)
        state["expires_at"] = now + int(result.get("expires_in", 3600))
    else:
        raise ValueError("Unsupported account platform")
    write_private_json(path, state)
    return state["access_token"]
