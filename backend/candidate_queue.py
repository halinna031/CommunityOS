"""Human review queue for LLM-extracted resource candidates."""
from __future__ import annotations
import json
import re
from datetime import date, datetime, timezone

from . import config, db
from .ingest import validate_resource


def _read(path=None):
    path = path or config.DATA_DIR / "pending.json"
    return path, json.loads(path.read_text(encoding="utf-8")) if path.exists() else []


def list_candidates(path=None):
    _, rows = _read(path)
    return [row for row in rows if row.get("review_status", "pending") == "pending"]


def resolve_candidate(candidate_id: str, action: str, updates=None, *, db_path=None, queue_path=None):
    if action not in {"approve", "reject"}:
        raise ValueError("action must be approve or reject")
    queue_path, rows = _read(queue_path)
    candidate = next((r for r in rows if r.get("id") == candidate_id), None)
    if not candidate or candidate.get("review_status", "pending") != "pending":
        raise ValueError("unknown or already reviewed candidate")
    if action == "approve":
        allowed = db.EDITABLE_FIELDS | {"category", "subcategory"}
        updates = updates or {}
        if set(updates) - allowed:
            raise ValueError("updates contain non-editable fields")
        candidate.update(updates)
        problems = validate_resource(candidate)
        if problems:
            raise ValueError("candidate needs correction: " + "; ".join(problems))
        with db.connect(db_path) as conn:
            duplicate = conn.execute(
                "SELECT id FROM resources WHERE lower(name)=lower(?) OR (source_url<>'' AND lower(source_url)=lower(?)) LIMIT 1",
                (candidate["name"], candidate.get("source_url", "")),
            ).fetchone()
            if duplicate:
                raise ValueError("duplicate of published resource " + duplicate["id"])
            published = {k: v for k, v in candidate.items() if not k.startswith("_") and k not in {"evidence", "confidence", "collected_at", "review_status"}}
            published["id"] = re.sub(r"^pending-", "real-", candidate["id"])
            published["last_verified_at"] = date.today().isoformat()
            published["is_sample"] = False
            db.upsert_resource(conn, published)
        candidate["review_status"] = "approved"
    else:
        candidate["review_status"] = "rejected"
    candidate["reviewed_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    queue_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True, "action": action, "resource_id": candidate.get("id")}
