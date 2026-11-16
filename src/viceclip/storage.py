"""Content-addressed private S3 delivery for Instagram's URL-based ingest."""
import os
from pathlib import Path
import re

from .media import fingerprint


def configured(settings):
    return isinstance(settings, dict) and settings.get("provider") == "s3" and bool(settings.get("bucket"))


def _client(settings):
    try:
        import boto3
        from botocore.config import Config
    except ImportError:
        raise ValueError("Install cloud delivery: pip install '.[cloud]'") from None
    endpoint = settings.get("endpoint_url")
    if endpoint and not endpoint.startswith("https://"):
        raise ValueError("Storage endpoint must use HTTPS")
    options = {"region_name": settings.get("region") or ("auto" if endpoint else "us-east-1"),
               "config": Config(signature_version="s3v4", retries={"max_attempts": 3, "mode": "standard"})}
    if endpoint:
        options["endpoint_url"] = endpoint
    for field, setting, default in (("aws_access_key_id", "access_key_env", "S3_ACCESS_KEY_ID"),
                                     ("aws_secret_access_key", "secret_key_env", "S3_SECRET_ACCESS_KEY"),
                                     ("aws_session_token", "session_token_env", "S3_SESSION_TOKEN")):
        value = os.environ.get(settings.get(setting, default), "")
        if value:
            options[field] = value
    if bool(options.get("aws_access_key_id")) != bool(options.get("aws_secret_access_key")):
        raise ValueError("Storage access key and secret key must be configured together")
    # When explicit credentials are absent, boto3's standard IAM/profile chain applies.
    try:
        return boto3.client("s3", **options)
    except Exception:
        raise ValueError("Cannot initialize storage credentials; check bucket connection settings") from None


def _code(error):
    return str(getattr(error, "response", {}).get("Error", {}).get("Code", ""))


def _matches(head, digest, size):
    return head.get("Metadata", {}).get("sha256") == digest and head.get("ContentLength") == size


def deliver(file, settings, *, sha256=None, client=None):
    """Upload bytes once under SHA-256; return a fresh private GET URL per attempt.

    No ACL is made public, no bucket is created, and no object is overwritten.
    The caller must keep the returned signed URL out of public logs.
    """
    if not configured(settings):
        raise ValueError("Configure an S3-compatible media bucket to publish Instagram files automatically")
    path = Path(file)
    if not path.is_file():
        raise ValueError("Media file is missing")
    size = path.stat().st_size
    if not 0 < size <= 300 * 1024 * 1024:
        raise ValueError("Instagram media delivery supports nonempty files up to 300MiB")
    digest = fingerprint(path)
    if sha256 is not None and digest != sha256:
        raise ValueError("Media changed before cloud delivery")
    expiry = int(settings.get("presign_seconds", 86400))
    if not 3600 <= expiry <= 604800:
        raise ValueError("Storage signed URL lifetime must be 3600..604800 seconds")
    prefix = str(settings.get("prefix", "viceclip")).strip("/")
    if not re.fullmatch(r"[A-Za-z0-9/_-]*", prefix) or ".." in prefix:
        raise ValueError("Storage prefix may contain letters, digits, '/', '_' and '-'")
    key = (prefix + "/" if prefix else "") + digest[:2] + "/" + digest + ".mp4"
    bucket = str(settings["bucket"])
    s3 = client or _client(settings)
    exists = False
    try:
        head = s3.head_object(Bucket=bucket, Key=key)
        if not _matches(head, digest, size):
            raise ValueError("Cloud object key exists with different bytes; refusing overwrite")
        exists = True
    except ValueError:
        raise
    except Exception as error:
        if _code(error) not in ("404", "NoSuchKey", "NotFound"):
            raise ValueError("Cannot inspect media bucket; check endpoint, region and permissions") from None
    if not exists:
        try:
            with path.open("rb") as body:
                s3.put_object(Bucket=bucket, Key=key, Body=body, ContentLength=size,
                              ContentType="video/mp4", Metadata={"sha256": digest}, IfNoneMatch="*")
        except Exception as error:
            if _code(error) not in ("412", "PreconditionFailed"):
                raise ValueError("Cloud media transfer failed; the next attempt rechecks the immutable object") from None
        try:
            head = s3.head_object(Bucket=bucket, Key=key)
        except Exception:
            raise ValueError("Cannot verify uploaded media object") from None
        if not _matches(head, digest, size):
            raise ValueError("Cloud media integrity metadata does not match the clip")
    try:
        url = s3.generate_presigned_url("get_object", Params={"Bucket": bucket, "Key": key},
                                        ExpiresIn=expiry, HttpMethod="GET")
    except Exception:
        raise ValueError("Cannot create a temporary media download URL") from None
    if not str(url).startswith("https://"):
        raise ValueError("Cloud media download URL must use HTTPS")
    return {"public_url": url, "object_key": key, "sha256": digest, "size": size,
            "expires_in": expiry, "reused": exists}
