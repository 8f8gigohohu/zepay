"""Encrypted secrets store (§50).

Values are encrypted with ChaCha20-Poly1305 under HKDF-derived per-purpose
subkeys of the install master key. The store keeps only ciphertext; plaintext
exists solely in process memory at the moment of use. Names are metadata;
values are never logged, never serialized to the frontend, never backed up in
plaintext.
"""

from __future__ import annotations

import logging

from zepay.core.util import utcnow_iso
from zepay.security.crypto import SecretBox

log = logging.getLogger("zepay.secrets")


class SecretsStore:
    def __init__(self, box: SecretBox, storage):
        self.box = box
        self.db = storage

    def put(self, name: str, value: str, purpose: str = "general") -> None:
        token = self.box.encrypt(value, purpose=purpose)
        now = utcnow_iso()
        self.db.execute(
            "INSERT INTO secrets (name, purpose, token, created_at, updated_at) VALUES (?,?,?,?,?)"
            " ON CONFLICT(name) DO UPDATE SET purpose=excluded.purpose, token=excluded.token,"
            " updated_at=excluded.updated_at",
            (name, purpose, token, now, now),
        )
        log.info("secret stored: %s (purpose=%s, %d chars ciphertext)", name, purpose, len(token))

    def get(self, name: str) -> str | None:
        row = self.db.query_one("SELECT purpose, token FROM secrets WHERE name=?", (name,))
        if not row:
            return None
        try:
            return self.box.decrypt(row["token"], purpose=row["purpose"])
        except Exception as e:
            log.error("secret decrypt failed for %s: %s", name, e)
            return None

    def get_purpose(self, name: str) -> str | None:
        row = self.db.query_one("SELECT purpose FROM secrets WHERE name=?", (name,))
        return row["purpose"] if row else None

    def delete(self, name: str) -> None:
        self.db.execute("DELETE FROM secrets WHERE name=?", (name,))
        log.info("secret deleted: %s", name)

    def names(self) -> list[str]:
        rows = self.db.query("SELECT name FROM secrets ORDER BY name")
        return [str(r["name"]) for r in rows]
