"""Camada de banco compatível com SQLite (local) e Postgres (produção).

Escolha automática:
- Se a variável de ambiente DATABASE_URL existir  -> Postgres (psycopg 3)
- Caso contrário                                  -> SQLite (arquivo local)

O wrapper uniformiza as diferenças de dialeto:
- placeholders: o código usa `?`; no Postgres viram `%s`
- INSERT: emula `cursor.lastrowid` via `RETURNING id`
- linhas: acessíveis por nome (`row["col"]`) e convertíveis com `dict(row)`
"""
import os
import sqlite3

DATABASE_URL = os.environ.get("DATABASE_URL")
IS_PG = bool(DATABASE_URL)
DB_PATH = os.environ.get("DB_PATH", os.path.join(os.path.dirname(__file__), "orgeral.db"))

if IS_PG:
    import psycopg
    from psycopg.rows import dict_row


class _Cursor:
    def __init__(self, raw, is_pg):
        self._raw = raw
        self._is_pg = is_pg
        self.lastrowid = None

    def execute(self, sql, params=()):
        if self._is_pg:
            sql_pg = sql.replace("?", "%s")
            is_insert = sql.lstrip()[:6].upper() == "INSERT"
            if is_insert and "RETURNING" not in sql.upper():
                sql_pg = sql_pg.rstrip().rstrip(";") + " RETURNING id"
            self._raw.execute(sql_pg, params)
            if is_insert:
                row = self._raw.fetchone()
                self.lastrowid = row["id"] if row else None
        else:
            self._raw.execute(sql, params)
            self.lastrowid = self._raw.lastrowid
        return self

    def fetchone(self):
        return self._raw.fetchone()

    def fetchall(self):
        return self._raw.fetchall()


class _Connection:
    def __init__(self, raw, is_pg):
        self._raw = raw
        self._is_pg = is_pg

    def execute(self, sql, params=()):
        if self._is_pg:
            cur = self._raw.cursor(row_factory=dict_row)
        else:
            cur = self._raw.cursor()
        return _Cursor(cur, self._is_pg).execute(sql, params)

    def commit(self):
        self._raw.commit()

    def close(self):
        self._raw.close()


def connect():
    if IS_PG:
        return _Connection(psycopg.connect(DATABASE_URL), True)
    raw = sqlite3.connect(DB_PATH)
    raw.row_factory = sqlite3.Row
    return _Connection(raw, False)


# ── Schema ────────────────────────────────────────────────────────────────────
_USERS_SQLITE = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    google_id TEXT UNIQUE NOT NULL,
    email TEXT, name TEXT, picture TEXT,
    access_token TEXT, refresh_token TEXT, token_expiry REAL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
)"""

_TASKS_SQLITE = """
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL DEFAULT 1,
    title TEXT NOT NULL, description TEXT,
    date TEXT NOT NULL, time TEXT,
    color TEXT DEFAULT '#555555', subject TEXT DEFAULT '',
    task_type TEXT DEFAULT '', completed INTEGER DEFAULT 0,
    gcal_event_id TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
)"""

_USERS_PG = """
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    google_id TEXT UNIQUE NOT NULL,
    email TEXT, name TEXT, picture TEXT,
    access_token TEXT, refresh_token TEXT, token_expiry DOUBLE PRECISION,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)"""

_TASKS_PG = """
CREATE TABLE IF NOT EXISTS tasks (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL DEFAULT 1,
    title TEXT NOT NULL, description TEXT,
    date TEXT NOT NULL, time TEXT,
    color TEXT DEFAULT '#555555', subject TEXT DEFAULT '',
    task_type TEXT DEFAULT '', completed INTEGER DEFAULT 0,
    gcal_event_id TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)"""

# Colunas adicionadas em bancos antigos (idempotente).
_MIGRATIONS = {
    "tasks": (
        "subject TEXT DEFAULT ''",
        "task_type TEXT DEFAULT ''",
        "completed INTEGER DEFAULT 0",
        "user_id INTEGER NOT NULL DEFAULT 1",
        "gcal_event_id TEXT",
    ),
    "users": (
        "access_token TEXT",
        "refresh_token TEXT",
        "token_expiry REAL",
    ),
}


def init_db():
    conn = connect()
    if IS_PG:
        conn.execute(_USERS_PG)
        conn.execute(_TASKS_PG)
        for table, cols in _MIGRATIONS.items():
            for coldef in cols:
                # Postgres aborta a transação em erro de DDL; use IF NOT EXISTS.
                conn.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {coldef}")
    else:
        conn.execute(_USERS_SQLITE)
        conn.execute(_TASKS_SQLITE)
        for table, cols in _MIGRATIONS.items():
            for coldef in cols:
                try:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {coldef}")
                except sqlite3.OperationalError:
                    pass  # coluna já existe
    conn.commit()
    conn.close()
