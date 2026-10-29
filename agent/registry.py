"""SQLite subscription registry. Upserts are idempotent, so re-running the
agent on the same data never duplicates rows or price history."""
import sqlite3
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Optional

from agent.models import AlertDecision, Evaluation, Subscription

SCHEMA = """
CREATE TABLE IF NOT EXISTS subscriptions (
    merchant TEXT PRIMARY KEY,
    category TEXT NOT NULL,
    cadence TEXT NOT NULL,
    current_price_inr REAL NOT NULL,
    last_charge_date TEXT NOT NULL,
    last_used_date TEXT,
    status TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS price_history (
    merchant TEXT NOT NULL,
    charge_date TEXT NOT NULL,
    price_inr REAL NOT NULL,
    PRIMARY KEY (merchant, charge_date)
);
CREATE TABLE IF NOT EXISTS last_decisions (
    merchant TEXT PRIMARY KEY,
    alert INTEGER NOT NULL,
    severity TEXT NOT NULL,
    reason TEXT NOT NULL,
    source TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS evaluations (
    merchant TEXT PRIMARY KEY,
    fingerprint TEXT NOT NULL,
    source TEXT NOT NULL,
    alerted INTEGER NOT NULL,
    notified INTEGER NOT NULL
);
"""


class Registry:
    def __init__(self, path: Path | str):
        self.path = str(path)
        with self._conn() as c:
            c.executescript(SCHEMA)

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def upsert(self, sub: Subscription) -> None:
        with self._conn() as c:
            # COALESCE: a re-detect (which doesn't know usage) must not wipe last_used_date.
            c.execute(
                """INSERT INTO subscriptions VALUES (?,?,?,?,?,?,?)
                   ON CONFLICT(merchant) DO UPDATE SET
                     category=excluded.category, cadence=excluded.cadence,
                     current_price_inr=excluded.current_price_inr,
                     last_charge_date=excluded.last_charge_date,
                     last_used_date=COALESCE(excluded.last_used_date, subscriptions.last_used_date),
                     status=excluded.status""",
                (sub.merchant, sub.category, sub.cadence, sub.current_price_inr,
                 sub.last_charge_date.isoformat(),
                 sub.last_used_date.isoformat() if sub.last_used_date else None,
                 sub.status),
            )
            c.executemany(
                "INSERT OR IGNORE INTO price_history VALUES (?,?,?)",
                [(sub.merchant, d.isoformat(), p) for d, p in sub.price_history],
            )

    def set_last_used(self, merchant: str, when: Optional[date]) -> None:
        if when is None:
            return
        with self._conn() as c:
            c.execute("UPDATE subscriptions SET last_used_date=? WHERE merchant=?",
                      (when.isoformat(), merchant))

    def get_all(self) -> list[Subscription]:
        with self._conn() as c:
            rows = c.execute("SELECT * FROM subscriptions ORDER BY merchant COLLATE NOCASE").fetchall()
            out = []
            for r in rows:
                hist = c.execute(
                    "SELECT charge_date, price_inr FROM price_history WHERE merchant=? ORDER BY charge_date",
                    (r["merchant"],)).fetchall()
                out.append(Subscription(
                    merchant=r["merchant"], category=r["category"], cadence=r["cadence"],
                    current_price_inr=r["current_price_inr"],
                    price_history=[(date.fromisoformat(h[0]), h[1]) for h in hist],
                    last_charge_date=date.fromisoformat(r["last_charge_date"]),
                    last_used_date=date.fromisoformat(r["last_used_date"]) if r["last_used_date"] else None,
                    status=r["status"],
                ))
            return out

    # --- evaluation memory (duplicate-alert suppression) -------------------
    def get_evaluation(self, merchant: str) -> Optional[Evaluation]:
        with self._conn() as c:
            r = c.execute("SELECT * FROM evaluations WHERE merchant=?", (merchant,)).fetchone()
        if r is None:
            return None
        return Evaluation(merchant=r["merchant"], fingerprint=r["fingerprint"], source=r["source"],
                          alerted=bool(r["alerted"]), notified=bool(r["notified"]))

    def save_evaluation(self, e: Evaluation) -> None:
        with self._conn() as c:
            c.execute("INSERT OR REPLACE INTO evaluations VALUES (?,?,?,?,?)",
                      (e.merchant, e.fingerprint, e.source, int(e.alerted), int(e.notified)))

    def clear_evaluation(self, merchant: str) -> None:
        with self._conn() as c:
            c.execute("DELETE FROM evaluations WHERE merchant=?", (merchant,))

    # --- last decision text, for display (the dashboard) --------------------
    def save_decision(self, d: AlertDecision) -> None:
        with self._conn() as c:
            c.execute("INSERT OR REPLACE INTO last_decisions VALUES (?,?,?,?,?)",
                      (d.merchant, int(d.alert), d.severity, d.reason, d.source))

    def get_decisions(self) -> dict[str, AlertDecision]:
        with self._conn() as c:
            rows = c.execute("SELECT * FROM last_decisions").fetchall()
        return {r["merchant"]: AlertDecision(merchant=r["merchant"], alert=bool(r["alert"]),
                                             severity=r["severity"], reason=r["reason"],
                                             source=r["source"]) for r in rows}

    def clear_decision(self, merchant: str) -> None:
        with self._conn() as c:
            c.execute("DELETE FROM last_decisions WHERE merchant=?", (merchant,))
