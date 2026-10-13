"""Step 4: explicit, explainable scoring.

Every component is in [0,1]; total = sum(weight * component) * 100.
Each component also emits *signals* (structured facts) that explain.py renders in words -
the LLM never gets to invent them.
"""
from __future__ import annotations
import math
from datetime import datetime, date

from . import config
from .hours import status_at

# ---------------------------------------------------------------- helpers
def haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    (la1, lo1), (la2, lo2) = a, b
    p1, p2 = math.radians(la1), math.radians(la2)
    dphi, dl = p2 - p1, math.radians(lo2 - lo1)
    h = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(h))


def walk_minutes(km: float) -> int:
    return round(km * 1.3 / 5.0 * 60)  # 5 km/h with a 1.3 street-network detour factor


def age_days(verified: str | None, today: date) -> int | None:
    if not verified:
        return None
    return max(0, (today - date.fromisoformat(verified[:10])).days)


def confidence_from_reports(n_open: int) -> float:
    return max(0.3, config.REPORT_CONFIDENCE_DECAY ** n_open)


def needs_field(res: dict, intent: dict) -> list[str]:
    """Which resource field do the user's `needs` refer to?"""
    return res["accepted_items"] if intent["category"] == "donation" else res["services"]


def match_needs(res: dict, intent: dict) -> tuple[list[str], list[str], list[str]]:
    """-> (hits, misses, blocked). `blocked` = user wants X but the resource says it does NOT take X."""
    needs = intent["needs"]
    have = set(needs_field(res, intent))
    hits = [n for n in needs if n in have]
    blocked = [n for n in needs if n in set(res.get("not_accepted", []))] if intent["category"] == "donation" else []
    misses = [n for n in needs if n not in have and n not in blocked]
    return hits, misses, blocked


def reference_point(intent: dict, user_loc: tuple[float, float] | None,
                    region_center: tuple[float, float] | None = None) -> tuple[float, float] | None:
    if intent.get("campus") in config.CAMPUSES:
        return config.CAMPUSES[intent["campus"]]
    if intent["constraints"].get("near_campus") and not user_loc:
        return config.CAMPUSES["uw"]
    return user_loc or region_center


# ---------------------------------------------------------------- scoring
def effective_weights(intent: dict, user_loc) -> dict[str, float]:
    """Base weights, except: if the user explicitly asked for 'near campus', distance counts double."""
    W = dict(config.WEIGHTS)
    if intent["constraints"].get("near_campus") or intent.get("campus"):
        extra = W["distance"]                      # +0.15
        rest = 1.0 - W["distance"]
        W = {k: (v * (rest - extra) / rest if k != "distance" else v + extra) for k, v in W.items()}
    return W


def score_resource(res: dict, intent: dict, *, now: datetime, semantic: float, n_open_reports: int,
                   user_loc: tuple[float, float] | None, region_center: tuple[float, float] | None = None) -> dict:
    W = effective_weights(intent, user_loc)
    signals: list[dict] = []
    comp: dict[str, float] = {}

    # ---- 1. match (explicit needs + semantic similarity)
    hits, misses, blocked = match_needs(res, intent)
    if intent["needs"]:
        explicit = len(hits) / len(intent["needs"])
        if hits:
            signals.append({"level": "ok", "code": "has_needs", "p": {"items": hits, "donation": intent["category"] == "donation"}})
        if misses:
            signals.append({"level": "warn", "code": "unconfirmed_needs", "p": {"items": misses}})
    else:
        explicit = 1.0 if (intent["category"] is None or res["category"] == intent["category"]) else 0.3
        if intent["category"]:
            signals.append({"level": "ok", "code": "category_ok", "p": {"category": res["category"]}})
    comp["match"] = 0.7 * explicit + 0.3 * min(1.0, semantic)

    # ---- 2. eligibility
    elig = set(res["eligibility"]) or {"anyone"}
    ident = set(intent["identity"])
    if "anyone" in elig:
        comp["eligibility"] = 1.0
        signals.append({"level": "ok", "code": "open_to_all", "p": {}})
    elif ident & elig:
        comp["eligibility"] = 1.0
        signals.append({"level": "ok", "code": "eligible", "p": {"who": sorted(ident & elig)}})
    elif not ident:
        comp["eligibility"] = 0.6
        signals.append({"level": "warn", "code": "restricted_unknown", "p": {"who": sorted(elig)}})
    else:
        comp["eligibility"] = 0.0
        signals.append({"level": "bad", "code": "restricted", "p": {"who": sorted(elig)}})
    if intent["language"] == "zh" and any(l in ("Mandarin", "Chinese", "Cantonese") for l in res["languages"]):
        comp["eligibility"] = min(1.0, comp["eligibility"] + 0.05)
        signals.append({"level": "ok", "code": "speaks_zh", "p": {}})

    # ---- 3. distance
    ref = reference_point(intent, user_loc, region_center)
    distance_km = None
    if ref and res.get("lat") is not None:
        distance_km = haversine_km(ref, (res["lat"], res["lng"]))
        comp["distance"] = max(0.0, 1.0 - distance_km / 10.0)
        signals.append({"level": "ok" if distance_km <= 3 else "info", "code": "distance",
                        "p": {"km": round(distance_km, 1), "walk_min": walk_minutes(distance_km),
                              "from_campus": bool(intent.get("campus") or (intent["constraints"].get("near_campus") and not user_loc)),
                              "from_region": bool(not user_loc and not intent.get("campus") and not intent["constraints"].get("near_campus") and region_center)}})
    else:
        comp["distance"] = 0.5  # unknown location -> neutral, and we say nothing

    # ---- 4. open now
    st = status_at(res["hours"], now)
    if st["is_open"] is True:
        comp["open"] = 1.0
        signals.append({"level": "ok", "code": "open_now", "p": {"closes": st["closes_at"]}})
    elif st["is_open"] is False:
        comp["open"] = 0.2
        signals.append({"level": "warn", "code": "closed_now", "p": {"next": st["next_open"]}})
    else:
        comp["open"] = 0.5
        signals.append({"level": "info", "code": "hours_unknown", "p": {}})

    # ---- 5. freshness x confidence (reports lower confidence)
    age = age_days(res.get("last_verified_at"), now.date())
    if age is None:
        fresh = 0.2
    elif age <= config.FRESH_DAYS:
        fresh = 1.0
    else:
        fresh = max(0.2, 1.0 - 0.8 * (age - config.FRESH_DAYS) / (config.STALE_DAYS - config.FRESH_DAYS))
    confidence = confidence_from_reports(n_open_reports)
    comp["freshness"] = fresh * confidence
    if age is not None:
        signals.append({"level": "ok" if age <= 90 else ("warn" if age > 180 else "info"),
                        "code": "verified", "p": {"days": age}})
    if n_open_reports:
        signals.append({"level": "warn", "code": "reports_pending", "p": {"n": n_open_reports}})

    # ---- 6. barrier (appointment / ID / fee)
    barrier = 1.0
    if res["requires_appointment"]:
        barrier -= 0.4
        signals.append({"level": "warn", "code": "needs_appointment", "p": {}})
    else:
        signals.append({"level": "ok", "code": "walk_in", "p": {}})
    if res["requires_id"]:
        barrier -= 0.3
        signals.append({"level": "warn", "code": "needs_id", "p": {}})
    if res["fee_cad"] > 0:
        barrier -= 0.3
        signals.append({"level": "warn", "code": "has_fee", "p": {"fee": res["fee_cad"]}})
    comp["barrier"] = max(0.0, barrier)

    total = sum(W[k] * comp[k] for k in W) * 100
    needs_review = n_open_reports >= config.REPORT_REVIEW_THRESHOLD
    if needs_review:
        total *= 0.7  # sink possibly-outdated entries in the ranking
    return {
        "score": round(total, 1),
        "components": {k: round(v, 3) for k, v in comp.items()},
        "weighted": {k: round(W[k] * comp[k] * 100, 1) for k in W},
        "weights": {k: round(v, 3) for k, v in W.items()},
        "signals": signals,
        "distance_km": None if distance_km is None else round(distance_km, 2),
        "open_status": st,
        "age_days": age,
        "confidence": round(confidence, 2),
        "needs_review": needs_review,
        "open_reports": n_open_reports,
    }
