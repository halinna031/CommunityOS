"""Semantic index with two interchangeable backends.

* TfidfIndex  - zero dependencies, deterministic, great for a hackathon demo/CI.
* STIndex     - sentence-transformers (multilingual) for real dense embeddings.

Both expose:  index.search(query: str) -> dict[resource_id, cosine_score]
"""
from __future__ import annotations
import math
import re
from collections import Counter

from . import config

_STOP = set("a an the of to for and or in on at is are i me my we you it be can do does where how what is there "
            "some any with this that have has need want looking find get".split())


def _stem(w: str) -> str:
    for suf in ("ing", "ies", "es", "s"):
        if w.endswith(suf) and len(w) - len(suf) >= 3:
            return w[: -len(suf)] + ("y" if suf == "ies" else "")
    return w


def tokenize(text: str) -> list[str]:
    text = text.lower()
    toks = [_stem(w) for w in re.findall(r"[a-z0-9]+", text) if w not in _STOP]
    cjk = re.findall(r"[\u4e00-\u9fff]", text)  # character bigrams for Chinese
    toks += [a + b for a, b in zip(cjk, cjk[1:])]
    return toks


def doc_text(r: dict) -> str:
    """Text that represents a resource semantically. NOTE: `not_accepted` is deliberately excluded,
    but free-text descriptions may still say 'cannot accept furniture' -- exactly the trap that
    pure vector search falls into and our structured hard-filters avoid."""
    parts = [r["name"], r.get("subcategory") or "", r.get("description") or "",
             " ".join(r.get("services", [])), " ".join(r.get("accepted_items", [])),
             " ".join(r.get("tags", [])), r.get("neighborhood") or ""]
    return " ".join(p.replace("_", " ") for p in parts)


class TfidfIndex:
    def __init__(self, resources: list[dict]):
        self.ids = [r["id"] for r in resources]
        docs = [Counter(tokenize(doc_text(r))) for r in resources]
        n = len(docs)
        df: Counter = Counter()
        for d in docs:
            df.update(d.keys())
        self.idf = {t: math.log((1 + n) / (1 + c)) + 1 for t, c in df.items()}
        self.vecs = [self._vec(d) for d in docs]

    def _vec(self, counts: Counter) -> dict[str, float]:
        v = {t: (1 + math.log(c)) * self.idf.get(t, 0.0) for t, c in counts.items() if t in self.idf}
        norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
        return {t: x / norm for t, x in v.items()}

    def search(self, query: str) -> dict[str, float]:
        q = self._vec(Counter(tokenize(query)))
        if not q:
            return {i: 0.0 for i in self.ids}
        return {rid: sum(w * v.get(t, 0.0) for t, w in q.items()) for rid, v in zip(self.ids, self.vecs)}


class STIndex:
    def __init__(self, resources: list[dict]):
        from sentence_transformers import SentenceTransformer  # lazy import
        self.model = SentenceTransformer(config.ST_MODEL)
        self.ids = [r["id"] for r in resources]
        self.vecs = self.model.encode([doc_text(r) for r in resources], normalize_embeddings=True)

    def search(self, query: str) -> dict[str, float]:
        q = self.model.encode([query], normalize_embeddings=True)[0]
        return {rid: float(max(0.0, q @ v)) for rid, v in zip(self.ids, self.vecs)}


def build_index(resources: list[dict], backend: str | None = None):
    backend = backend or config.EMBEDDING_BACKEND
    if backend == "st":
        try:
            return STIndex(resources)
        except Exception as e:  # missing package / model download failure
            print(f"[embeddings] sentence-transformers unavailable ({e}); falling back to TF-IDF")
    return TfidfIndex(resources)
