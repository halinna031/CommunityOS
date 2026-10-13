"""Steps 2-4: hybrid retrieval = structured hard filters -> semantic similarity -> explicit scoring.

Graceful degradation ("relaxation") if nothing survives the strict filters:
  1. strict:            category + needs + constraints (+ never blocked items)
  2. drop constraints:  keep category + needs  (UI says which constraints were dropped)
  3. drop needs:        keep category           (NOT for donations - a wrong drop-off trip is costly)
  4. nothing:           honest "no match" + fallback (211)
"""
from __future__ import annotations
from datetime import datetime

from . import scoring
from .hours import parse_hours, status_at

MIN_SEMANTIC_WHEN_NO_CATEGORY = 0.2


def _is_24h(res: dict) -> bool:
    table = parse_hours(res["hours"])
    return all(any(a == 0 and b >= 1440 for a, b in table[d]) for d in range(7))


def _constraint_ok(res: dict, intent: dict, now: datetime, active: dict) -> bool:
    if active.get("free") and res["fee_cad"] > 0:
        return False
    if active.get("no_id") and res["requires_id"]:
        return False
    if active.get("no_appointment") and res["requires_appointment"]:
        return False
    if active.get("open_24h") and not _is_24h(res):
        return False
    if active.get("open_now") and status_at(res["hours"], now)["is_open"] is not True:
        return False
    return True


def _eligible_ok(res: dict, intent: dict) -> bool:
    """If the user told us who they are and the resource is restricted to someone else, exclude."""
    elig = set(res["eligibility"]) or {"anyone"}
    ident = set(intent["identity"])
    return "anyone" in elig or not ident or bool(ident & elig)


def hybrid_search(resources: list[dict], intent: dict, index, *, now: datetime, report_counts: dict[str, int],
                  user_loc=None, region_id: str | None = None, region_center=None, k: int = 5) -> dict:
    sem = index.search(intent["query_text"]) if intent["query_text"] else {r["id"]: 0.0 for r in resources}
    top_sem = max(sem.values()) or 1.0
    sem_norm = {rid: s / top_sem for rid, s in sem.items()}
    active = {c: v for c, v in intent["constraints"].items() if v}
    cat = intent["category"]

    def stage(use_constraints: bool, use_needs: bool) -> list[dict]:
        out = []
        for r in resources:
            if region_id and region_id != "waterloo_region" and r.get("region_id", "waterloo_region") != region_id:
                continue
            if cat and r["category"] != cat:
                continue
            hits, misses, blocked = scoring.match_needs(r, intent)
            if blocked:                                   # explicit "we do NOT take this" - always excluded
                continue
            if use_needs and intent["needs"] and not hits:
                continue
            if not _eligible_ok(r, intent):
                continue
            if use_constraints and not _constraint_ok(r, intent, now, active):
                continue
            if not cat and sem[r["id"]] < MIN_SEMANTIC_WHEN_NO_CATEGORY:
                continue
            out.append(r)
        return out

    relaxed: list[str] = []
    if intent["urgent"] and not cat:     # crisis with no clear service category: show emergency help, not random cards
        return {"results": [], "relaxed": [], "total_candidates": 0}
    cands = stage(True, True)
    if not cands and active:
        cands = stage(False, True)
        if cands:
            relaxed.append("constraints:" + ",".join(sorted(k_ for k_ in active if k_ != "near_campus")))
    if not cands and intent["needs"] and cat != "donation":
        cands = stage(False, False)
        if cands:
            relaxed.append("needs")
    relaxed = [x for x in relaxed if x != "constraints:"]

    scored = []
    for r in cands:
        s = scoring.score_resource(r, intent, now=now, semantic=sem_norm[r["id"]],
                                   n_open_reports=report_counts.get(r["id"], 0), user_loc=user_loc,
                                   region_center=region_center)
        # surface constraints the user asked for that this (relaxed) result violates
        if "constraints:" in "".join(relaxed):
            if active.get("open_now") and s["open_status"]["is_open"] is not True:
                s["signals"].append({"level": "warn", "code": "violates_open_now", "p": {}})
            if active.get("open_24h") and not _is_24h(r):
                s["signals"].append({"level": "warn", "code": "violates_24h", "p": {}})
            if active.get("no_id") and r["requires_id"]:
                s["signals"].append({"level": "warn", "code": "violates_no_id", "p": {}})
            if active.get("no_appointment") and r["requires_appointment"]:
                s["signals"].append({"level": "warn", "code": "violates_no_appt", "p": {}})
        scored.append({**r, "rank_info": s})
    scored.sort(key=lambda x: (-x["rank_info"]["score"], x["id"]))
    return {"results": scored[:k], "relaxed": relaxed, "total_candidates": len(scored)}


def vector_only_search(resources: list[dict], query_text: str, index, k: int = 5) -> list[dict]:
    """Baseline for the evaluation: plain semantic top-k, no filters, no scoring."""
    sem = index.search(query_text)
    by_id = {r["id"]: r for r in resources}
    ranked = sorted(((s, rid) for rid, s in sem.items() if s > 0), key=lambda x: (-x[0], x[1]))
    return [by_id[rid] for _, rid in ranked[:k]]
