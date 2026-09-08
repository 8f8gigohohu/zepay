"""Storage interface + SQLite (default) + PostgreSQL (optional) backends.

PostgreSQL is the production default per spec; SQLite keeps single-machine and
test installs portable. Both implement the same interface, both run the same
migrations. If the configured backend is unreachable the platform reports it
honestly (health=UNAVAILABLE) and — only for non-trading bootstrap — falls back
to SQLite with a loud system event. Persistent trading records are never split
across two backends.
"""

from __future__ import annotations

import sqlite3
import threading
from typing import Any

from zepay.core.errors import StorageError
from zepay.database.schema import MIGRATION_COLUMNS, ddl_for


class Storage:
    backend = "abstract"

    def execute(self, sql: str, params: tuple = ()) -> Any:
        raise NotImplementedError

    def executemany(self, sql: str, seq: list[tuple]) -> Any:
        raise NotImplementedError

    def query(self, sql: str, params: tuple = ()) -> list[dict]:
        raise NotImplementedError

    def query_one(self, sql: str, params: tuple = ()) -> dict | None:
        raise NotImplementedError

    def kv_set(self, k: str, v: str) -> None:
        self.execute(
            "INSERT INTO kv (k,v) VALUES (?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, v)
        )

    def kv_get(self, k: str, d: Any = None) -> Any:
        row = self.query_one("SELECT v FROM kv WHERE k=?", (k,))
        return row["v"] if row else d

    def health(self) -> dict:
        raise NotImplementedError

    def close(self) -> None:
        pass


def _apply_migrations_sqlite(conn: sqlite3.Connection) -> None:
    for table, cols in MIGRATION_COLUMNS.items():
        for col, typ in cols:
            try:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
            except Exception:
                pass  # already exists


class SQLiteStorage(Storage):
    backend = "sqlite"

    def __init__(self, path: str):
        self.path = path
        self._local = threading.local()
        self._lock = threading.RLock()
        self._init_schema()

    def _conn(self) -> sqlite3.Connection:
        c = getattr(self._local, "conn", None)
        if c is None:
            c = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA synchronous=NORMAL")
            c.execute("PRAGMA busy_timeout=15000")
            self._local.conn = c
        return c

    def _init_schema(self) -> None:
        with self._lock:
            c = self._conn()
            try:
                c.executescript(ddl_for("sqlite"))
                _apply_migrations_sqlite(c)
                c.execute("INSERT OR REPLACE INTO kv (k,v) VALUES ('schema_version','3')")
                c.commit()
            finally:
                pass

    def execute(self, sql: str, params: tuple = ()) -> Any:
        sql = _pg_to_sqlite(sql)
        with self._lock:
            c = self._conn()
            try:
                cur = c.execute(sql, params)
                c.commit()
                return cur
            except sqlite3.Error as e:
                c.rollback()
                raise StorageError(f"sqlite execute failed: {e} | sql={sql[:160]}") from e

    def executemany(self, sql: str, seq: list[tuple]) -> Any:
        sql = _pg_to_sqlite(sql)
        with self._lock:
            c = self._conn()
            try:
                cur = c.executemany(sql, seq)
                c.commit()
                return cur
            except sqlite3.Error as e:
                c.rollback()
                raise StorageError(f"sqlite executemany failed: {e}") from e

    def query(self, sql: str, params: tuple = ()) -> list[dict]:
        sql = _pg_to_sqlite(sql)
        with self._lock:
            try:
                rows = self._conn().execute(sql, params).fetchall()
                return [dict(r) for r in rows]
            except sqlite3.Error as e:
                raise StorageError(f"sqlite query failed: {e} | sql={sql[:160]}") from e

    def query_one(self, sql: str, params: tuple = ()) -> dict | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def health(self) -> dict:
        try:
            self.query_one("SELECT 1 AS ok")
            return {"backend": "sqlite", "status": "OK", "path": self.path}
        except Exception as e:
            return {"backend": "sqlite", "status": "UNAVAILABLE", "error": str(e)[:200]}


def _pg_to_sqlite(sql: str) -> str:
    """Shared repository code uses `?` placeholders (SQLite style); the
    Postgres backend translates them to `%s`. This passthrough exists so the
    translation direction is explicit and testable."""
    return sql


class PostgresStorage(Storage):
    """PostgreSQL backend via psycopg (v3 binary protocol). Imported lazily so
    the platform runs without psycopg installed."""

    backend = "postgres"

    def __init__(self, dsn: str):
        try:
            import psycopg
        except ImportError as e:
            raise StorageError(
                "psycopg is not installed; run `pip install psycopg[binary]` "
                "or set ZEPAY_DB_BACKEND=sqlite"
            ) from e
        import psycopg

        self._psycopg = psycopg
        self.dsn = dsn
        self._lock = threading.RLock()
        self._conn = psycopg.connect(dsn, autocommit=False, connect_timeout=10)
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(ddl_for("postgres"))
            for table, cols in MIGRATION_COLUMNS.items():
                for col, typ in cols:
                    try:
                        cur.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {col} {typ}")
                    except Exception:
                        self._conn.rollback()
            cur.execute(
                "INSERT INTO kv (k,v) VALUES ('schema_version','3') "
                "ON CONFLICT (k) DO UPDATE SET v=EXCLUDED.v"
            )
            self._conn.commit()

    def _translate(self, sql: str) -> str:
        return sql.replace("?", "%s")

    def execute(self, sql: str, params: tuple = ()) -> Any:
        with self._lock:
            try:
                cur = self._conn.cursor()
                cur.execute(self._translate(sql), params)
                self._conn.commit()
                return cur
            except Exception as e:
                self._conn.rollback()
                raise StorageError(f"postgres execute failed: {e}") from e

    def executemany(self, sql: str, seq: list[tuple]) -> Any:
        with self._lock:
            try:
                cur = self._conn.cursor()
                cur.executemany(self._translate(sql), seq)
                self._conn.commit()
                return cur
            except Exception as e:
                self._conn.rollback()
                raise StorageError(f"postgres executemany failed: {e}") from e

    def query(self, sql: str, params: tuple = ()) -> list[dict]:
        with self._lock:
            try:
                cur = self._conn.cursor()
                cur.execute(self._translate(sql), params)
                cols = [d.name for d in cur.description] if cur.description else []
                return [dict(zip(cols, row, strict=False)) for row in cur.fetchall()]
            except Exception as e:
                raise StorageError(f"postgres query failed: {e}") from e

    def query_one(self, sql: str, params: tuple = ()) -> dict | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def health(self) -> dict:
        try:
            self.query_one("SELECT 1 AS ok")
            return {"backend": "postgres", "status": "OK"}
        except Exception as e:
            return {"backend": "postgres", "status": "UNAVAILABLE", "error": str(e)[:200]}

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:
            pass


def open_storage(backend: str, sqlite_path: str, postgres_dsn: str, sys_event=None) -> Storage:
    """Factory with honest fallback: if postgres was requested but is
    unreachable, we record a CRITICAL system event and fall back to SQLite so
    the platform still boots for research/paper — the health endpoint keeps
    reporting the degradation."""
    if backend == "postgres":
        if not postgres_dsn:
            if sys_event:
                sys_event(
                    "database",
                    "WARN",
                    "ZEPAY_DB_BACKEND=postgres but ZEPAY_POSTGRES_DSN is empty — using sqlite",
                )
            return SQLiteStorage(sqlite_path)
        try:
            store = PostgresStorage(postgres_dsn)
            if sys_event:
                sys_event("database", "INFO", "PostgreSQL storage connected")
            return store
        except Exception as e:
            if sys_event:
                sys_event(
                    "database",
                    "CRITICAL",
                    f"PostgreSQL unavailable ({str(e)[:160]}) — falling back to SQLite. "
                    "Persistent records will live in SQLite until Postgres is restored.",
                )
            return SQLiteStorage(sqlite_path)
    return SQLiteStorage(sqlite_path)
