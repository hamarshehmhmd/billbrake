from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone


def periods(now: datetime | None = None) -> tuple[str, str]:
    now = now or datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%d"), now.strftime("%Y-%m")


class Store:
    """SQLite ledger. Spend is tracked per project per UTC day and UTC month."""

    def __init__(self, path: str):
        self._lock = threading.Lock()
        self.db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("""CREATE TABLE IF NOT EXISTS spend (
            project TEXT, period TEXT, usd REAL NOT NULL DEFAULT 0, requests INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (project, period))""")
        self.db.execute("""CREATE TABLE IF NOT EXISTS events (
            ts TEXT, project TEXT, provider TEXT, model TEXT, input_tokens INTEGER, output_tokens INTEGER,
            usd REAL, status TEXT)""")

    def get(self, project: str, period: str) -> float:
        row = self.db.execute("SELECT usd FROM spend WHERE project=? AND period=?", (project, period)).fetchone()
        return row[0] if row else 0.0

    def add(self, project: str, usd: float, now: datetime | None = None, count: bool = True) -> None:
        with self._lock:
            for per in periods(now):
                self.db.execute(
                    "INSERT INTO spend(project, period, usd, requests) VALUES (?,?,?,?) "
                    "ON CONFLICT(project, period) DO UPDATE SET usd=usd+excluded.usd, "
                    "requests=requests+excluded.requests",
                    (project, per, usd, 1 if count else 0))

    def log(self, project, provider, model, itok, otok, usd, status):
        self.db.execute("INSERT INTO events VALUES (?,?,?,?,?,?,?,?)",
                        (datetime.now(timezone.utc).isoformat(), project, provider, model, itok, otok, usd, status))

    def reset(self, project: str, period: str | None = None) -> None:
        if period:
            self.db.execute("DELETE FROM spend WHERE project=? AND period=?", (project, period))
        else:
            self.db.execute("DELETE FROM spend WHERE project=?", (project,))
