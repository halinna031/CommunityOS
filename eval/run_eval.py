"""Compare  (A) naive vector-only retrieval  vs  (B) CommunityOS hybrid retrieval.

    python -m eval.run_eval            # prints a table and writes eval/results.md

Fixed clock (Tue 2026-10-13 14:00 Toronto) so results are reproducible.
Metrics
  hit@1 / hit@3   : an expected resource is in the top-1 / top-3        (queries with answers)
  violation@3     : # of top-3 results that explicitly refuse the requested item (donation traps)
  wrong-cat@3     : # of top-3 results from a different category than the expected answers
  no-answer ok    : queries with NO valid resource that correctly return nothing
Baseline note: the baseline gets the *same* English query_text our intent step produces, so we measure
retrieval/ranking quality, not translation (otherwise TF-IDF on Chinese would be an unfair strawman).
"""
from __future__ import annotations
import json
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend import intent as intent_mod, retrieval  # noqa: E402
from backend.service import Service  # noqa: E402

NOW = datetime(2026, 10, 13, 14, 0, tzinfo=ZoneInfo("America/Toronto"))


def run(backend: str | None = None, verbose: bool = True) -> dict:
    queries = json.loads((ROOT / "eval" / "queries.json").read_text(encoding="utf-8"))
    svc = Service(db_path=Path(tempfile.mkdtemp()) / "eval.db", backend=backend)
    by_id = {r["id"]: r for r in svc.resources}
    stats = {m: {"hit1": 0, "hit3": 0, "viol": 0, "wrongcat": 0, "none_ok": 0} for m in ("vector", "hybrid")}
    n_ans = sum(1 for q in queries if not q["expect_none"])
    n_none = len(queries) - n_ans
    rows = []
    for q in queries:
        it = intent_mod.parse_intent(q["query"], use_llm=False)
        base = retrieval.vector_only_search(svc.resources, it["query_text"], svc.index, k=3)
        hyb = [r["id"] for r in svc.search(q["query"], now=NOW, k=3, use_llm=False)["results"]]
        for name, ids in (("vector", [r["id"] for r in base]), ("hybrid", hyb)):
            s = stats[name]
            if q["expect_none"]:
                s["none_ok"] += int(not ids)
            else:
                s["hit1"] += int(ids[:1] and ids[0] in q["expected"])
                s["hit3"] += int(any(i in q["expected"] for i in ids))
                exp_cat = by_id[q["expected"][0]]["category"]
                s["wrongcat"] += sum(by_id[i]["category"] != exp_cat for i in ids)
            if q["item"]:
                s["viol"] += sum(q["item"] in by_id[i]["not_accepted"] for i in ids)
        rows.append((q["id"], q["query"], [r["id"] for r in base], hyb, q["expected"]))
    if verbose:
        for qid, qq, b, h, e in rows:
            print(f"{qid} {qq[:48]:48} exp={e}\n      vector={b}\n      hybrid={h}")
    out = {"n_answerable": n_ans, "n_no_answer": n_none, **stats}
    return out


def to_markdown(r: dict) -> str:
    a, n = r["n_answerable"], r["n_no_answer"]
    v, h = r["vector"], r["hybrid"]
    pct = lambda x, d: f"{100 * x / d:.0f}%"
    return "\n".join([
        f"| Metric ({a} answerable + {n} no-answer queries) | Vector-only | CommunityOS hybrid |",
        "|---|---|---|",
        f"| Top-1 hit | {pct(v['hit1'], a)} | {pct(h['hit1'], a)} |",
        f"| Top-3 hit | {pct(v['hit3'], a)} | {pct(h['hit3'], a)} |",
        f"| Results that explicitly REFUSE the requested item (lower is better) | {v['viol']} | {h['viol']} |",
        f"| Wrong-category results in top-3 (lower is better) | {v['wrongcat']} | {h['wrongcat']} |",
        f"| No-answer queries correctly returning nothing | {v['none_ok']}/{n} | {h['none_ok']}/{n} |",
    ])


if __name__ == "__main__":
    res = run(verbose="-q" not in sys.argv)
    md = to_markdown(res)
    print("\n" + md)
    (ROOT / "eval" / "results.md").write_text(md + "\n", encoding="utf-8")
