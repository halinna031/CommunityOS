"""SQLite storage: resources + user reports. No ORM, no personal data stored."""
from __future__ import annotations
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

from . import config

LIST_FIELDS = ("eligibility", "accepted_items", "not_accepted", "services", "languages", "tags")

SCHEMA = """
CREATE TABLE IF NOT EXISTS resources (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  category TEXT NOT NULL,
  subcategory TEXT,
  description TEXT,
  summary_zh TEXT,
  address TEXT,
  lat REAL, lng REAL,
  neighborhood TEXT,
  region_id TEXT NOT NULL DEFAULT 'waterloo_region',
  hours TEXT,
  eligibility TEXT,          -- JSON list
  requires_appointment INTEGER DEFAULT 0,
  requires_id INTEGER DEFAULT 0,
  fee_cad REAL DEFAULT 0,
  accepted_items TEXT,       -- JSON list (donation sites)
  not_accepted TEXT,         -- JSON list
  services TEXT,             -- JSON list of canonical service tags
  languages TEXT,            -- JSON list
  tags TEXT,                 -- JSON list
  phone TEXT, url TEXT, source_url TEXT,
  last_verified_at TEXT,     -- ISO date
  is_sample INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS reports (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  resource_id TEXT NOT NULL REFERENCES resources(id),
  reason TEXT NOT NULL,
  note TEXT,
  client_id TEXT,            -- random id kept in the browser; used only to de-duplicate
  status TEXT NOT NULL DEFAULT 'open',   -- open | resolved | dismissed
  created_at TEXT NOT NULL,
  resolved_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_reports_resource ON reports(resource_id, status);
"""

EDITABLE_FIELDS = {
    "name", "description", "summary_zh", "address", "lat", "lng", "hours", "phone", "url", "region_id",
    "requires_appointment", "requires_id", "fee_cad",
    "eligibility", "accepted_items", "not_accepted", "services", "languages", "tags",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def connect(path=None):
    conn = sqlite3.connect(str(path or config.DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _row_to_resource(row: sqlite3.Row) -> dict:
    d = dict(row)
    for f in LIST_FIELDS:
        d[f] = json.loads(d[f]) if d.get(f) else []
    for f in ("requires_appointment", "requires_id", "is_sample"):
        d[f] = bool(d[f])
    return d


def init_db(path=None, seed_path=None, reset: bool = False) -> None:
    path = path or config.DB_PATH
    if reset and path.exists():
        path.unlink()
    with connect(path) as conn:
        conn.executescript(SCHEMA)
        # Upgrade databases created before region-aware search.
        columns = {r["name"] for r in conn.execute("PRAGMA table_info(resources)")}
        if "region_id" not in columns:
            conn.execute("ALTER TABLE resources ADD COLUMN region_id TEXT NOT NULL DEFAULT 'waterloo_region'")
            # Infer municipality once for existing seed records. Never override
            # a region_id that an editor has deliberately assigned later.
            conn.execute("UPDATE resources SET region_id='cambridge' WHERE lower(coalesce(neighborhood,'') || ' ' || coalesce(address,'')) LIKE '%cambridge%'")
            conn.execute("UPDATE resources SET region_id='kitchener' WHERE lower(coalesce(neighborhood,'') || ' ' || coalesce(address,'')) LIKE '%kitchener%'")
            conn.execute("UPDATE resources SET region_id='waterloo' WHERE lower(coalesce(neighborhood,'') || ' ' || coalesce(address,'')) LIKE '%waterloo%' AND lower(coalesce(neighborhood,'') || ' ' || coalesce(address,'')) NOT LIKE '%kitchener%'")
        if conn.execute("SELECT COUNT(*) FROM resources").fetchone()[0] == 0:
            seed = json.loads((seed_path or config.SEED_PATH).read_text(encoding="utf-8"))
            for r in seed:
                upsert_resource(conn, r)


def upsert_resource(conn, r: dict) -> None:
    row = dict(r)
    if not row.get("region_id"):
        place = (str(row.get("neighborhood") or "") + " " + str(row.get("address") or "")).lower()
        row["region_id"] = ("cambridge" if "cambridge" in place else
                            "kitchener" if "kitchener" in place else
                            "waterloo" if "waterloo" in place else config.DEFAULT_REGION)
    for f in LIST_FIELDS:
        row[f] = json.dumps(row.get(f, []), ensure_ascii=False)
    cols = ["id", "name", "category", "subcategory", "description", "summary_zh", "address", "lat", "lng",
            "neighborhood", "region_id", "hours", "eligibility", "requires_appointment", "requires_id", "fee_cad",
            "accepted_items", "not_accepted", "services", "languages", "tags", "phone", "url",
            "source_url", "last_verified_at", "is_sample"]
    vals = [row.get(c) for c in cols]
    conn.execute(f"INSERT OR REPLACE INTO resources ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", vals)


def all_resources(path=None) -> list[dict]:
    with connect(path) as conn:
        return [_row_to_resource(r) for r in conn.execute("SELECT * FROM resources ORDER BY id")]


def get_resource(rid: str, path=None) -> dict | None:
    with connect(path) as conn:
        row = conn.execute("SELECT * FROM resources WHERE id=?", (rid,)).fetchone()
        return _row_to_resource(row) if row else None


def open_report_counts(path=None) -> dict[str, int]:
    with connect(path) as conn:
        rows = conn.execute("SELECT resource_id, COUNT(*) c FROM reports WHERE status='open' GROUP BY resource_id")
        return {r["resource_id"]: r["c"] for r in rows}


def open_report_reasons(rid: str, path=None) -> list[str]:
    with connect(path) as conn:
        rows = conn.execute("SELECT reason FROM reports WHERE resource_id=? AND status='open'", (rid,))
        return [r["reason"] for r in rows]
