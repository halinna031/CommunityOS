"""Application service: the one place the API layers call. Pure Python, easy to test."""
from __future__ import annotations
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from . import config, db, explain, hours, intent as intent_mod, retrieval, scoring
from .embeddings import build_index

REPORT_REASONS = ["closed", "hours_changed", "items_changed", "wrong_address", "other"]


class ValidationError(ValueError):
    pass


class Service:
    def __init__(self, db_path=None, backend: str | None = None):
        self.db_path = db_path or config.DB_PATH
        db.init_db(self.db_path)
        self.backend = backend
        self._reload()

    def _reload(self) -> None:
        self.resources = db.all_resources(self.db_path)
        self.index = build_index(self.resources, self.backend)

    # ------------------------------------------------------------------ search
    def search(self, text: str, *, lat: float | None = None, lng: float | None = None,
               now: datetime | None = None, k: int = 5, use_llm: bool | None = None,
               region_id: str = config.DEFAULT_REGION) -> dict:
        t0 = time.perf_counter()
        text = (text or "").strip()
        if not text:
            raise ValidationError("query is empty")
        if len(text) > 500:
            raise ValidationError("query too long (max 500 chars)")
        now = now or datetime.now(ZoneInfo(config.TIMEZONE))
        # Keep interactive search independent of Gemini quotas. Gemini remains
        # available to the ingestion pipeline, where extracted records are reviewed.
        intent = intent_mod.parse_intent(text, use_llm=False)
        lang = intent["language"]
        user_loc = (lat, lng) if lat is not None and lng is not None else None
        if region_id not in config.REGIONS:
            raise ValidationError("unknown region")
        region_center = config.REGIONS[region_id]["center"]

        out = retrieval.hybrid_search(self.resources, intent, self.index, now=now,
                                      report_counts=db.open_report_counts(self.db_path), user_loc=user_loc,
                                      region_id=region_id, region_center=region_center, k=k)
        results = []
        for rank, r in enumerate(out["results"], 1):
            ex = explain.explain_result(r, lang)
            results.append({
                "rank": rank, "id": r["id"], "name": r["name"], "category": r["category"],
                "description": r["summary_zh"] if lang == "zh" and r.get("summary_zh") else r["description"],
                "address": r["address"], "lat": r["lat"], "lng": r["lng"], "neighborhood": r["neighborhood"],
                "region_id": r.get("region_id", "waterloo_region"),
                "phone": r["phone"], "url": r["url"], "source_url": r["source_url"],
                "hours_table": hours.weekly_table(r["hours"]), "hours_raw": r["hours"],
                "requires_appointment": r["requires_appointment"], "requires_id": r["requires_id"], "fee_cad": r["fee_cad"],
                "eligibility": r["eligibility"], "languages": r["languages"],
                "accepted_items": r["accepted_items"], "not_accepted": r["not_accepted"], "services": r["services"],
                "last_verified_at": r["last_verified_at"], "is_sample": r["is_sample"],
                "score": r["rank_info"]["score"], "components": r["rank_info"]["components"],
                "weighted": r["rank_info"]["weighted"], "weights": r["rank_info"]["weights"],
                "open_reports": r["rank_info"]["open_reports"], "confidence": r["rank_info"]["confidence"],
                "needs_review": r["rank_info"]["needs_review"], "open_status": r["rank_info"]["open_status"],
                "distance_km": r["rank_info"]["distance_km"],
                "summary": ex["summary"], "signals": ex["signals"],
            })
        return {
            "query": text, "intent": intent, "results": results, "relaxed": out["relaxed"],
            "total_candidates": out["total_candidates"],
            "emergency": explain.EMERGENCY[lang] if intent["urgent"] else None,
            "fallback": explain.FALLBACK[lang] if not results else None,
            "weights": config.WEIGHTS, "region_id": region_id,
            "meta": {"ms": round((time.perf_counter() - t0) * 1000, 1), "now": now.isoformat(timespec="minutes"),
                     "intent_source": intent["source"], "index": type(self.index).__name__},
        }

    # ------------------------------------------------------------------ reports
    def report(self, resource_id: str, reason: str, note: str = "", client_id: str = "") -> dict:
        if reason not in REPORT_REASONS:
            raise ValidationError(f"reason must be one of {REPORT_REASONS}")
        note = (note or "").strip()[:300]
        client_id = (client_id or "")[:64]
        with db.connect(self.db_path) as conn:
            if not conn.execute("SELECT 1 FROM resources WHERE id=?", (resource_id,)).fetchone():
                raise ValidationError("unknown resource")
            if client_id and conn.execute(
                    "SELECT 1 FROM reports WHERE resource_id=? AND client_id=? AND status='open'",
                    (resource_id, client_id)).fetchone():
                return {"ok": True, "duplicate": True, **self._report_state(conn, resource_id)}
            conn.execute("INSERT INTO reports (resource_id, reason, note, client_id, created_at) VALUES (?,?,?,?,?)",
                         (resource_id, reason, note, client_id or None, db.now_iso()))
            return {"ok": True, "duplicate": False, **self._report_state(conn, resource_id)}

    @staticmethod
    def _report_state(conn, rid: str) -> dict:
        n = conn.execute("SELECT COUNT(*) FROM reports WHERE resource_id=? AND status='open'", (rid,)).fetchone()[0]
        return {"open_reports": n, "confidence": round(scoring.confidence_from_reports(n), 2),
                "needs_review": n >= config.REPORT_REVIEW_THRESHOLD}

    # ------------------------------------------------------------------ admin
    def admin_reports(self) -> list[dict]:
        with db.connect(self.db_path) as conn:
            rows = conn.execute("""SELECT r.id AS resource_id, r.name, r.hours, r.last_verified_at,
                                          COUNT(p.id) AS n, GROUP_CONCAT(p.reason) AS reasons,
                                          GROUP_CONCAT(COALESCE(NULLIF(p.note,''),'-'), ' | ') AS notes
                                   FROM reports p JOIN resources r ON r.id = p.resource_id
                                   WHERE p.status='open' GROUP BY r.id ORDER BY n DESC""").fetchall()
        return [{**dict(r), "reasons": r["reasons"].split(","), "needs_review": r["n"] >= config.REPORT_REVIEW_THRESHOLD} for r in rows]

    def admin_resolve(self, resource_id: str, action: str, updates: dict | None = None) -> dict:
        if action not in ("verify", "update", "dismiss"):
            raise ValidationError("action must be verify | update | dismiss")
        updates = updates or {}
        with db.connect(self.db_path) as conn:
            row = conn.execute("SELECT 1 FROM resources WHERE id=?", (resource_id,)).fetchone()
            if not row:
                raise ValidationError("unknown resource")
            if action == "update":
                bad = set(updates) - db.EDITABLE_FIELDS
                if bad or not updates:
                    raise ValidationError(f"editable fields only: {sorted(db.EDITABLE_FIELDS)}")
                if "region_id" in updates and updates["region_id"] not in config.REGIONS:
                    raise ValidationError(f"region_id must be one of {sorted(config.REGIONS)}")
                sets, vals = [], []
                import json
                for f, v in updates.items():
                    sets.append(f"{f}=?")
                    vals.append(json.dumps(v, ensure_ascii=False) if f in db.LIST_FIELDS else v)
                conn.execute(f"UPDATE resources SET {','.join(sets)} WHERE id=?", (*vals, resource_id))
            if action in ("verify", "update"):   # re-verification resets freshness
                conn.execute("UPDATE resources SET last_verified_at=? WHERE id=?", (datetime.now().date().isoformat(), resource_id))
            status = "dismissed" if action == "dismiss" else "resolved"
            conn.execute("UPDATE reports SET status=?, resolved_at=? WHERE resource_id=? AND status='open'",
                         (status, db.now_iso(), resource_id))
        self._reload()
        return {"ok": True, "action": action}

    # ----------------------------------------------------------- ingestion review
    def admin_candidates(self) -> list[dict]:
        from .candidate_queue import list_candidates
        return list_candidates()

    def admin_resolve_candidate(self, candidate_id: str, action: str, updates: dict | None = None) -> dict:
        from .candidate_queue import resolve_candidate
        try:
            result = resolve_candidate(candidate_id, action, updates, db_path=self.db_path)
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc
        if action == "approve":
            self._reload()
        return result

    # ------------------------------------------------------------------ misc
    def meta(self) -> dict:
        cats: dict[str, int] = {}
        for r in self.resources:
            cats[r["category"]] = cats.get(r["category"], 0) + 1
        return {"weights": config.WEIGHTS, "report_reasons": REPORT_REASONS, "categories": cats,
                "total": len(self.resources), "has_sample_data": any(r["is_sample"] for r in self.resources),
                "llm_enabled": bool(config.GEMINI_API_KEY and config.USE_LLM_INTENT),
                "regions": [{"id": rid, **details} for rid, details in config.REGIONS.items()],
                "default_region": config.DEFAULT_REGION,
                "review_threshold": config.REPORT_REVIEW_THRESHOLD}
