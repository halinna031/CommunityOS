import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

os.environ["COMMUNITYOS_DB"] = str(Path(tempfile.mkdtemp()) / "api.db")   # before importing backend
os.environ.pop("GEMINI_API_KEY", None)

from backend import hours, intent, scoring  # noqa: E402
from backend.service import Service, ValidationError  # noqa: E402

NOW = datetime(2026, 10, 13, 14, 0, tzinfo=ZoneInfo("America/Toronto"))  # Tuesday 14:00


def fresh_service() -> Service:
    return Service(db_path=Path(tempfile.mkdtemp()) / "t.db")


class HoursTests(unittest.TestCase):
    def test_open_and_closed(self):
        spec = "Mon-Fri 09:00-17:00; Sat 10:00-14:00"
        self.assertTrue(hours.status_at(spec, NOW)["is_open"])
        late = NOW.replace(hour=18)
        st = hours.status_at(spec, late)
        self.assertFalse(st["is_open"])
        self.assertEqual(st["next_open"]["day"], "wed")

    def test_split_shifts_wraparound_and_unknown(self):
        self.assertFalse(hours.status_at("Tue,Thu 13:00-17:00, 18:00-20:00", NOW.replace(hour=17, minute=30))["is_open"])
        self.assertTrue(hours.status_at("Sun-Thu 18:00-24:00", NOW.replace(hour=23))["is_open"])
        self.assertIsNone(hours.status_at("By appointment", NOW)["is_open"])


class IntentTests(unittest.TestCase):
    def test_donation_en_zh(self):
        for q in ("I need somewhere to donate furniture", "我有一张旧沙发想捐掉"):
            i = intent.parse_intent_rules(q)
            self.assertEqual((i["category"], i["needs"]), ("donation", ["furniture"]))
        self.assertEqual(intent.parse_intent_rules("我有一张旧沙发想捐掉")["language"], "zh")

    def test_constraints_identity_urgent(self):
        i = intent.parse_intent_rules("I'm a student, need free math help open now, no ID")
        self.assertTrue(all(i["constraints"][k] for k in ("free", "open_now", "no_id")))
        self.assertIn("students", i["identity"])
        self.assertEqual(i["category"], "learning")
        self.assertTrue(intent.parse_intent_rules("我无家可归")["urgent"])

    def test_llm_output_is_validated(self):
        rules = intent.parse_intent_rules("donate a sofa")
        bad = {"category": "evil", "needs": ["furniture", "rm -rf"], "constraints": "x", "urgent": False, "language": "fr"}
        out = intent._normalize_llm(bad, rules)
        self.assertEqual(out["category"], "donation")           # invalid value ignored
        self.assertEqual(out["needs"], ["furniture"])           # unknown tags dropped
        urgent_rules = intent.parse_intent_rules("I am homeless")
        self.assertTrue(intent._normalize_llm({"urgent": False}, urgent_rules)["urgent"])  # never lose emergencies


class SearchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.svc = fresh_service()

    def ids(self, q, **kw):
        return [r["id"] for r in self.svc.search(q, now=NOW, use_llm=False, **kw)["results"]]

    def test_trap_places_that_refuse_item_are_excluded(self):
        ids = self.ids("I need somewhere to donate furniture", k=10)
        self.assertTrue({"real-donate-restore-waterloo", "real-donate-restore-cambridge"} <= set(ids))
        for resource in self.svc.resources:
            if resource["id"] in ids:
                self.assertNotIn("furniture", resource["not_accepted"])

    def test_no_answer_returns_fallback_not_guesses(self):
        r = self.svc.search("I want to donate a mattress", now=NOW, use_llm=False)
        self.assertEqual(r["results"], [])
        self.assertIn("211", r["fallback"])

    def test_emergency_block(self):
        r = self.svc.search("I have no place to sleep tonight", now=NOW, use_llm=False)
        self.assertTrue(r["emergency"])
        self.assertEqual(r["results"], [])

    def test_region_selection_filters_resources(self):
        ids = self.ids("I need a food hamper", region_id="cambridge", k=10)
        self.assertIn("real-food-cambridge-food-bank", ids)
        self.assertNotIn("real-food-house-of-friendship", ids)

    def test_hard_constraints_and_relaxation(self):
        r = self.svc.search("food pantry open now no ID", now=NOW, use_llm=False, k=10)
        for x in r["results"]:
            self.assertTrue(x["open_status"]["is_open"] and not x["requires_id"])
        r = self.svc.search("hot meal open now", now=NOW.replace(hour=3), use_llm=False)   # 3am: nothing open -> relax
        self.assertTrue(r["relaxed"])
        self.assertTrue(any(s["code"] == "violates_open_now" for s in r["results"][0]["signals"]))

    def test_region_scope_is_returned(self):
        r = self.svc.search("donate a sofa", now=NOW, use_llm=False, region_id="waterloo")
        self.assertEqual(r["region_id"], "waterloo")
        self.assertTrue(all(x["region_id"] == "waterloo" for x in r["results"]))

    def test_validation(self):
        with self.assertRaises(ValidationError):
            self.svc.search("   ")
        with self.assertRaises(ValidationError):
            self.svc.search("x" * 501)

    def test_weights_sum_to_one_and_score_bounds(self):
        r = self.svc.search("quiet place to study", now=NOW, use_llm=False)
        self.assertAlmostEqual(sum(r["weights"].values()), 1.0, places=6)
        for x in r["results"]:
            self.assertAlmostEqual(sum(x["weights"].values()), 1.0, places=2)
            self.assertTrue(0 <= x["score"] <= 100)

    def test_explanations_only_state_known_facts(self):
        r = self.svc.search("donate furniture", now=NOW, use_llm=False)["results"][0]
        self.assertIn("furniture", r["summary"].lower())
        self.assertTrue(r["source_url"].startswith("http"))


class ReportLoopTests(unittest.TestCase):
    def test_reports_sink_flag_and_admin_reset(self):
        svc = fresh_service()
        base = svc.search("donate furniture", now=NOW, use_llm=False)["results"]
        top = base[0]["id"]
        for i in range(3):
            res = svc.report(top, "closed", "door locked", client_id=f"c{i}")
        self.assertTrue(res["needs_review"])
        after = svc.search("donate furniture", now=NOW, use_llm=False)["results"]
        moved = next(x for x in after if x["id"] == top)
        self.assertTrue(moved["needs_review"])
        self.assertGreater(moved["rank"], 1)                  # no longer first
        self.assertNotEqual(after[0]["id"], top)
        self.assertEqual(svc.admin_reports()[0]["n"], 3)

        svc.admin_resolve(top, "verify")                      # volunteer confirms -> freshness reset, reports closed
        again = svc.search("donate furniture", now=NOW, use_llm=False)["results"]
        self.assertEqual(again[0]["id"], top)
        self.assertFalse(again[0]["needs_review"])
        self.assertEqual(svc.admin_reports(), [])

    def test_duplicate_and_invalid_reports(self):
        svc = fresh_service()
        rid = "real-donate-restore-waterloo"
        self.assertFalse(svc.report(rid, "closed", client_id="same")["duplicate"])
        self.assertTrue(svc.report(rid, "closed", client_id="same")["duplicate"])
        with self.assertRaises(ValidationError):
            svc.report(rid, "spam")
        with self.assertRaises(ValidationError):
            svc.report("nope", "closed")

    def test_admin_update_applies_fix_and_whitelists_fields(self):
        svc = fresh_service()
        rid = "real-food-house-of-friendship"
        svc.report(rid, "hours_changed")
        svc.admin_resolve(rid, "update", {"hours": "Tue 10:00-12:00"})
        resource = next(r for r in svc.resources if r["id"] == rid)
        self.assertEqual(resource["hours"], "Tue 10:00-12:00")
        with self.assertRaises(ValidationError):
            svc.admin_resolve(rid, "update", {"id": "hacked"})
        with self.assertRaises(ValidationError):
            svc.admin_resolve(rid, "update", {"hours; DROP TABLE resources": 1})


class ScoringUnitTests(unittest.TestCase):
    def test_confidence_decay_and_walk_time(self):
        self.assertEqual(scoring.confidence_from_reports(0), 1.0)
        self.assertLess(scoring.confidence_from_reports(3), 0.52)
        self.assertEqual(scoring.confidence_from_reports(50), 0.3)
        self.assertEqual(scoring.walk_minutes(1.0), 16)


class IngestValidationTests(unittest.TestCase):
    def test_validator_catches_bad_extractions(self):
        from backend.ingest import validate_resource
        good = {"name": "X", "category": "donation", "description": "d", "address": "a", "hours": "Mon-Fri 09:00-17:00",
                "eligibility": ["anyone"], "accepted_items": ["furniture"], "not_accepted": ["electronics"]}
        self.assertEqual(validate_resource(good), [])
        self.assertIn("item both accepted and not accepted", validate_resource({**good, "not_accepted": ["furniture"]}))
        self.assertIn("hours not parseable", validate_resource({**good, "hours": "whenever"}))
        self.assertIn("bad category", validate_resource({**good, "category": "pets"}))
        self.assertIn("donation site without accepted_items", validate_resource({**good, "accepted_items": []}))


class HttpTests(unittest.TestCase):
    """End-to-end through the stdlib server (same JSON contract as the FastAPI app)."""
    @classmethod
    def setUpClass(cls):
        from http.server import ThreadingHTTPServer
        from backend import simple_server
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), simple_server.Handler)
        cls.base = f"http://127.0.0.1:{cls.srv.server_address[1]}"
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def call(self, path, body=None, headers=None):
        req = urllib.request.Request(self.base + path, data=None if body is None else json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json", **(headers or {})})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_search_report_admin_flow(self):
        code, data = self.call("/api/search", {"query": "我有一张旧沙发想捐掉"})
        self.assertEqual(code, 200)
        self.assertEqual(data["intent"]["language"], "zh")
        self.assertTrue(data["results"])
        rid = data["results"][0]["id"]
        self.assertEqual(self.call("/api/report", {"resource_id": rid, "reason": "closed", "client_id": "t"})[0], 200)
        self.assertEqual(self.call("/api/admin/reports")[0], 401)
        code, rows = self.call("/api/admin/reports", headers={"X-Admin-Token": "demo-admin"})
        self.assertEqual((code, rows[0]["resource_id"]), (200, rid))
        self.assertEqual(self.call("/api/search", {"query": ""})[0], 400)

    def test_pages_served(self):
        for path in ("/", "/admin"):
            with urllib.request.urlopen(self.base + path) as r:
                self.assertEqual(r.status, 200)
                self.assertIn(b"<html", r.read()[:400].lower())


if __name__ == "__main__":
    unittest.main()
