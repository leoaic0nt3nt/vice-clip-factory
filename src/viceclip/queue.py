"""Transactional SQLite queue with deduplication and explicit approval."""
import datetime as dt
import json
import sqlite3
from pathlib import Path
from .media import fingerprint
from .rights import check_rights, get_account
from . import publishers
from .storage import configured as storage_configured


def utcnow():
    return dt.datetime.now(dt.timezone.utc)


def canonical_time(value=None):
    parsed = dt.datetime.fromisoformat((value or utcnow().isoformat()).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Schedule must include a timezone, e.g. 2026-11-20T12:00:00+03:00")
    return parsed.astimezone(dt.timezone.utc).isoformat()


class Queue:
    def __init__(self, path):
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, timeout=20)
        # SQLite stores private resumable session URLs. Keep the file user-readable only.
        import os
        os.chmod(self.path, 0o600)
        self.db.row_factory = sqlite3.Row
        self.db.execute("""CREATE TABLE IF NOT EXISTS jobs (
            id INTEGER PRIMARY KEY, sha256 TEXT NOT NULL, account TEXT NOT NULL, platform TEXT NOT NULL,
            source_id TEXT NOT NULL, file TEXT NOT NULL, metadata TEXT NOT NULL,
            scheduled_at TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', remote_id TEXT,
            stage TEXT, error TEXT, published_at TEXT, playback_verified INTEGER NOT NULL DEFAULT 0, UNIQUE(sha256,account,platform))""")
        columns = {row["name"] for row in self.db.execute("PRAGMA table_info(jobs)")}
        if "playback_verified" not in columns:
            self.db.execute("ALTER TABLE jobs ADD COLUMN playback_verified INTEGER NOT NULL DEFAULT 0")
        if "session_uri" not in columns:
            self.db.execute("ALTER TABLE jobs ADD COLUMN session_uri TEXT")
        self.db.commit()

    def add(self, file, source_id, account_id, metadata, config, schedule=None):
        account = get_account(config, account_id)
        path = Path(file).resolve()
        if not path.is_file():
            raise ValueError("Clip file does not exist")
        digest = fingerprint(path)
        metadata = dict(metadata)
        sidecar = path.with_suffix(".json")
        if sidecar.exists():
            provenance = json.loads(sidecar.read_text(encoding="utf-8"))
            if provenance.get("output_sha256") != digest or provenance.get("source_id") != source_id:
                raise ValueError("Rendered sidecar does not match file/source; rebuild or review provenance")
            metadata["sponsored"] = bool(metadata.get("sponsored") or provenance.get("sponsored"))
            metadata["sidecar_sha256"] = fingerprint(sidecar)
            metadata["sidecar_path"] = str(sidecar)
        check_rights(config, source_id, "publish", account["platform"],
                     commercial=account.get("commercial", False), sponsored=metadata.get("sponsored", False))
        when = canonical_time(schedule)
        if not metadata.get("title"):
            raise ValueError("Title is required")
        if (account["platform"] == "instagram" and not (metadata.get("public_url") or "").startswith("https://")
                and not storage_configured(account.get("storage", config.get("storage")))):
            raise ValueError("Connect cloud media delivery or provide a public HTTPS media URL for Instagram")
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO jobs(sha256,account,platform,source_id,file,metadata,scheduled_at) VALUES(?,?,?,?,?,?,?)",
                            (digest, account_id, account["platform"], source_id, str(path), json.dumps(metadata, ensure_ascii=False), when))
        row = dict(self.db.execute("SELECT * FROM jobs WHERE sha256=? AND account=? AND platform=?", (digest,account_id,account["platform"])).fetchone())
        row.pop("session_uri",None)
        return row

    def list(self):
        rows = [dict(row) for row in self.db.execute("SELECT * FROM jobs ORDER BY scheduled_at,id")]
        for row in rows:
            row.pop("session_uri",None)
        return rows

    def approve(self, job_id):
        with self.db:
            count = self.db.execute("UPDATE jobs SET status='approved',error=NULL WHERE id=? AND status IN ('pending','failed')", (job_id,)).rowcount
        if count != 1:
            raise ValueError("Only pending or definite-failed jobs can be approved; unknown uploads must be reconciled")

    def reconcile(self, job_id, remote_id):
        if not remote_id or not str(remote_id).strip():
            raise ValueError("Provide a verified remote video/media id")
        with self.db:
            count = self.db.execute("UPDATE jobs SET status=CASE WHEN platform='youtube' THEN 'uploaded' ELSE 'published' END,remote_id=?,published_at=?,error=NULL WHERE id=? AND status IN ('unknown','uploading')",
                                    (remote_id,utcnow().isoformat(),job_id)).rowcount
        if count != 1:
            raise ValueError("Only unknown or interrupted uploading jobs can be reconciled")

    def run(self, config, *, execute=False, uploader=None):
        if not execute:
            return self._run(config, execute=False, uploader=uploader)
        import fcntl
        import os
        lock_path = str(self.path) + ".upload.lock"
        with open(lock_path, "a") as lock:
            os.fchmod(lock.fileno(), 0o600)
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return []
            # A previous process stopped after the session URL was persisted.
            # The uploader queries that same session before sending anything.
            with self.db:
                self.db.execute("""UPDATE jobs SET status='processing'
                    WHERE status='uploading' AND platform='youtube'
                    AND stage='upload_started' AND session_uri IS NOT NULL""")
            return self._run(config, execute=True, uploader=uploader)

    def _run(self, config, *, execute, uploader):
        results = []
        for raw in self.db.execute("SELECT * FROM jobs WHERE status IN ('approved','processing') AND scheduled_at<=? ORDER BY scheduled_at,id", (utcnow().isoformat(),)).fetchall():
            job = dict(raw)
            job["metadata"] = json.loads(job["metadata"])
            try:
                account = dict(get_account(config, job["account"]))
                account["_storage"] = account.get("storage", config.get("storage"))
                daily_limit = int(account.get("max_posts_per_day", 3))
                if daily_limit < 1:
                    raise ValueError("Account daily publishing limit must be positive")
                check_rights(config, job["source_id"], "publish", job["platform"],
                             commercial=account.get("commercial", False), sponsored=job["metadata"].get("sponsored", False))
                if fingerprint(job["file"]) != job["sha256"]:
                    raise ValueError(f"Job {job['id']} file changed after approval")
                if job["metadata"].get("sidecar_path") and fingerprint(job["metadata"]["sidecar_path"]) != job["metadata"]["sidecar_sha256"]:
                    raise ValueError(f"Job {job['id']} render provenance changed after approval")
            except (ValueError, OSError, KeyError, TypeError) as exc:
                with self.db:
                    self.db.execute("UPDATE jobs SET status='failed',error=? WHERE id=?", (str(exc)[:300], job["id"]))
                results.append({"id": job["id"], "status": "failed", "error": str(exc)[:300]})
                continue
            if account.get("enabled") is False:
                results.append({"id": job["id"], "status": "account_paused", "account": job["account"]})
                continue
            if not execute:
                results.append({"id": job["id"], "status": "dry_run", "account": job["account"], "platform": job["platform"]})
                continue
            if uploader is None:
                try:
                    publishers.token_for(account)
                except ValueError as exc:
                    with self.db:
                        self.db.execute("UPDATE jobs SET status='failed',error=? WHERE id=?", (str(exc)[:300],job["id"]))
                    results.append({"id":job["id"],"status":"failed","error":str(exc)[:300]})
                    continue
            self.db.execute("BEGIN IMMEDIATE")
            since = (utcnow()-dt.timedelta(days=1)).isoformat()
            used = self.db.execute("SELECT count(*) FROM jobs WHERE account=? AND platform=? AND id<>? AND (status IN ('uploading','processing','unknown') OR (status IN ('published','uploaded') AND published_at>=?))", (job["account"], job["platform"], job["id"], since)).fetchone()[0]
            if used >= daily_limit:
                self.db.rollback()
                results.append({"id": job["id"], "status": "account_limit"})
                continue
            claimed = self.db.execute("UPDATE jobs SET status='uploading' WHERE id=? AND status IN ('approved','processing')", (job["id"],)).rowcount
            self.db.commit()
            if not claimed:
                continue
            mutated = False
            def progress(stage, remote):
                nonlocal mutated
                mutated = True
                with self.db:
                    if stage == "upload_started":
                        self.db.execute("UPDATE jobs SET stage=?,session_uri=? WHERE id=?", (stage,remote,job["id"]))
                    else:
                        self.db.execute("UPDATE jobs SET stage=?,remote_id=COALESCE(?,remote_id) WHERE id=?", (stage,remote,job["id"]))
            try:
                remote_id = (uploader or publishers.upload)(job, account, progress)
                if not remote_id:
                    raise publishers.AmbiguousUpload("Provider returned no id")
                accepted_status = "uploaded" if job["platform"] == "youtube" else "published"
                with self.db:
                    self.db.execute("UPDATE jobs SET status=?,remote_id=?,published_at=? WHERE id=?", (accepted_status,str(remote_id),utcnow().isoformat(),job["id"]))
                results.append({"id": job["id"], "status": accepted_status, "remote_id": remote_id, "playback_verified": False})
            except Exception as exc:
                if isinstance(exc, (publishers.PendingUpload, publishers.DeferredUpload)):
                    when = (utcnow() + dt.timedelta(seconds=exc.delay_seconds)).isoformat()
                    with self.db:
                        self.db.execute("UPDATE jobs SET status='processing',scheduled_at=?,error=? WHERE id=?", (when,str(exc)[:300],job["id"]))
                    results.append({"id": job["id"], "status": "processing", "retry_at": when})
                    continue
                status = "failed" if isinstance(exc,publishers.RejectedUpload) or isinstance(exc,ValueError) and not mutated else "unknown"
                with self.db:
                    self.db.execute("UPDATE jobs SET status=?,error=? WHERE id=?", (status,str(exc)[:300],job["id"]))
                results.append({"id": job["id"], "status": status, "error": str(exc)[:300]})
        return results
