"""Backup & recovery (§56): scheduled backups, rotation, integrity checks.

SQLite uses the online backup API (consistent snapshot without locking
readers for long). Postgres shells out to pg_dump when available; otherwise
reports NOT CONFIGURED honestly — never pretends a backup happened.
"""

from __future__ import annotations

import glob
import logging
import os
import shutil
import sqlite3
import subprocess
import time

from zepay.core.util import utcnow_iso

log = logging.getLogger("zepay.backup")


class BackupManager:
    def __init__(self, storage, config, settings, audit_fn):
        self.db = storage
        self.config = config
        self.settings = settings
        self.audit = audit_fn

    # ------------------------------------------------------------------
    def run(self, label: str = "manual") -> dict:
        ts = time.strftime("%Y%m%d-%H%M%S")
        backup_dir = self.settings.backup_dir
        os.makedirs(backup_dir, exist_ok=True)
        backend = self.settings.db_backend
        out: dict = {"ts": utcnow_iso(), "backend": backend, "label": label, "ok": False}
        if backend == "sqlite":
            src = self.settings.db_path
            dst = os.path.join(backup_dir, f"zepay-{ts}.db")
            try:
                src_conn = sqlite3.connect(src)
                dst_conn = sqlite3.connect(dst)
                with dst_conn:
                    src_conn.backup(dst_conn)
                dst_conn.close()
                src_conn.close()
                # integrity check on the BACKUP, not the source
                chk = sqlite3.connect(dst).execute("PRAGMA integrity_check").fetchone()
                out.update(
                    {
                        "ok": chk and chk[0] == "ok",
                        "path": dst,
                        "size_mb": round(os.path.getsize(dst) / 1e6, 2),
                        "integrity": chk[0] if chk else "?",
                    }
                )
            except Exception as e:
                out.update({"ok": False, "error": str(e)})
        elif backend == "postgres":
            if shutil.which("pg_dump") is None:
                out.update(
                    {
                        "ok": False,
                        "error": "pg_dump NOT AVAILABLE on this host — backup NOT "
                        "performed (honest failure, no fake backup)",
                    }
                )
                return out
            dst = os.path.join(backup_dir, f"zepay-{ts}.sql.gz")
            try:
                with open(dst, "wb") as fh:
                    subprocess.run(
                        ["pg_dump", self.settings.postgres_dsn], stdout=fh, check=True, timeout=300
                    )
                out.update(
                    {"ok": True, "path": dst, "size_mb": round(os.path.getsize(dst) / 1e6, 2)}
                )
            except Exception as e:
                out.update({"ok": False, "error": str(e)})
        else:
            out["error"] = f"unknown backend {backend}"
        self._rotate()
        if out["ok"]:
            self.audit("backup", "backup_created", {"path": out.get("path"), "label": label})
        else:
            self.audit("backup", "backup_failed", {"error": str(out.get("error"))[:200]})
        return out

    def _rotate(self) -> None:
        keep = int(self.config.get("backup_keep", 14))
        backup_dir = self.settings.backup_dir
        files = sorted(glob.glob(os.path.join(backup_dir, "zepay-*")))
        for old in files[:-keep] if len(files) > keep else []:
            try:
                os.remove(old)
            except OSError:
                pass

    def list(self) -> list[dict]:
        backup_dir = self.settings.backup_dir
        out = []
        for p in sorted(glob.glob(os.path.join(backup_dir, "zepay-*")), reverse=True):
            st = os.stat(p)
            out.append(
                {
                    "path": p,
                    "size_mb": round(st.st_size / 1e6, 2),
                    "created": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(st.st_mtime)),
                }
            )
        return out

    def restore_instructions(self) -> dict:
        return {
            "sqlite": "1) Stop ZEPAY. 2) Copy the chosen backup over the data dir's "
            "zepay.db. 3) Restart — schema migrations re-apply automatically.",
            "postgres": "1) Stop ZEPAY. 2) gunzip -c backup.sql.gz | psql <dsn>. " "3) Restart.",
            "note": "Restore is intentionally manual — an automated overwrite of the "
            "audit trail would violate immutability requirements (§56).",
        }
