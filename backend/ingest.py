"""Data-ingestion helper: raw web-page text -> validated resource record (for HUMAN review).

    python -m backend.ingest page.txt --url https://org.example/donate            # needs GEMINI_API_KEY
    -> appends to data/pending.json. A person checks it, then copies it into data/resources.json.

The LLM only *extracts*; `validate_resource` rejects anything outside the schema, and
nothing is published automatically. Never trust extracted hours/addresses without a look.
"""
from __future__ import annotations
import argparse
from html.parser import HTMLParser
import ipaddress
import json
import re
import socket
import time
from datetime import date
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from urllib.robotparser import RobotFileParser

from . import config, hours
from .intent import NEEDS

CATEGORIES = {"food_assistance", "donation", "learning", "study_space", "healthcare"}
ELIGIBILITY = {"anyone", "students", "newcomers", "low_income", "seniors"}
REGION_IDS = set(config.REGIONS)
REQUIRED = ["name", "category", "description", "address", "hours", "eligibility"]
CRAWLER_UA = "CommunityOSResourceBot/1.0"
MAX_PAGE_BYTES = 2_000_000


class _VisibleText(HTMLParser):
    """Small stdlib HTML-to-text extractor used for human-reviewed candidates."""
    SKIP = {"script", "style", "noscript", "svg", "nav"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip_depth += 1
        elif not self.skip_depth and tag in {"br", "p", "div", "li", "tr", "h1", "h2", "h3"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.skip_depth:
            self.skip_depth -= 1
        elif not self.skip_depth and tag in {"p", "div", "li", "tr", "h1", "h2", "h3"}:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.skip_depth:
            self.parts.append(data)


def _public_https_url(url: str) -> tuple[str, str]:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("crawler accepts public HTTPS URLs only")
    host = parsed.hostname.lower().rstrip(".")
    if host in {"localhost", "localhost.localdomain"} or host.endswith(".local"):
        raise ValueError("local network URLs are not allowed")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        # A non-IP hostname is resolved below to reject obvious private targets.
        try:
            addresses = {ipaddress.ip_address(info[4][0]) for info in socket.getaddrinfo(host, None)}
        except OSError as dns_error:
            raise ValueError(f"could not resolve source host: {dns_error}") from dns_error
        if not addresses or any(not address.is_global for address in addresses):
            raise ValueError("local or private network URLs are not allowed")
    else:
        if not ip.is_global:
            raise ValueError("local or private network URLs are not allowed")
    return host, parsed.path or "/"


class _SameHostRedirect(HTTPRedirectHandler):
    def __init__(self, expected_host: str):
        super().__init__()
        self.expected_host = expected_host

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new_host, _ = _public_https_url(newurl)
        if new_host != self.expected_host:
            raise ValueError("cross-host redirects are not followed")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch_page_text(url: str) -> str:
    """Fetch one public HTTPS page if its robots.txt permits this crawler."""
    host, path = _public_https_url(url)
    parsed = urlsplit(url)
    origin = f"https://{host}" + (f":{parsed.port}" if parsed.port else "")
    robots_url = origin + "/robots.txt"
    robots = RobotFileParser()
    opener = build_opener(_SameHostRedirect(host))
    try:
        request = Request(robots_url, headers={"User-Agent": CRAWLER_UA})
        with opener.open(request, timeout=15) as response:
            robots.parse(response.read(256_000).decode("utf-8", errors="replace").splitlines())
    except HTTPError as exc:
        if exc.code == 404:
            robots.parse([])
        else:
            raise ValueError(f"robots.txt returned HTTP {exc.code}; refusing to crawl") from exc
    except URLError as exc:
        raise ValueError(f"could not check robots.txt; refusing to crawl: {exc}") from exc
    if not robots.can_fetch(CRAWLER_UA, url):
        raise ValueError("robots.txt disallows this page")

    time.sleep(1.0)  # polite pause after robots.txt before fetching page content
    request = Request(url, headers={"User-Agent": CRAWLER_UA, "Accept": "text/html,application/xhtml+xml"})
    with opener.open(request, timeout=20) as response:
        content_type = response.headers.get_content_type()
        if content_type not in {"text/html", "application/xhtml+xml"}:
            raise ValueError(f"unsupported content type: {content_type}")
        raw = response.read(MAX_PAGE_BYTES + 1)
        if len(raw) > MAX_PAGE_BYTES:
            raise ValueError(f"page exceeds {MAX_PAGE_BYTES} bytes")
        charset = response.headers.get_content_charset() or "utf-8"
    parser = _VisibleText()
    parser.feed(raw.decode(charset, errors="replace"))
    text = re.sub(r"[ \t\r\f\v]+", " ", "".join(parser.parts))
    text = re.sub(r"\n\s*\n+", "\n", text).strip()
    if not text:
        raise ValueError("page had no extractable text")
    return text[:12000]

PROMPT = f"""Extract ONE community resource from the page text as JSON only (no prose, no fences).
Fields: name, category ({sorted(CATEGORIES)}), subcategory, description (<=300 chars, only facts from the page),
address, lat, lng (null if unknown, never guess), neighborhood,
hours (format "Mon-Fri 09:00-17:00; Sat 10:00-14:00", or "" if unclear),
eligibility (array from {sorted(ELIGIBILITY)}), requires_appointment (bool), requires_id (bool), fee_cad (number),
accepted_items (array from {sorted(NEEDS)}; donation sites only; ONLY items the page says are accepted),
not_accepted (array from the same list; ONLY items the page says are NOT accepted),
services (array from the same list), languages (array), phone, evidence (object mapping important fields to short exact quotes copied from the page).
If the page does not state something, leave it empty/false/null. Never invent."""


def validate_resource(d: dict) -> list[str]:
    """Return a list of problems (empty list = valid)."""
    errs = [f"missing {k}" for k in REQUIRED if not d.get(k)]
    if d.get("category") not in CATEGORIES:
        errs.append("bad category")
    if d.get("region_id") is not None and d.get("region_id") not in REGION_IDS:
        errs.append("bad region_id")
    if not set(d.get("eligibility") or []) <= ELIGIBILITY:
        errs.append("bad eligibility tag")
    for f in ("accepted_items", "not_accepted", "services"):
        if not set(d.get(f) or []) <= set(NEEDS):
            errs.append(f"unknown tag in {f}")
    if set(d.get("accepted_items") or []) & set(d.get("not_accepted") or []):
        errs.append("item both accepted and not accepted")
    if d.get("category") == "donation" and not d.get("accepted_items"):
        errs.append("donation site without accepted_items")
    if d.get("category") == "healthcare" and not d.get("services"):
        errs.append("healthcare resource without confirmed services")
    h = d.get("hours") or ""
    if h and not any(hours.parse_hours(h).values()):
        errs.append("hours not parseable")
    for f, lo, hi in (("lat", -90, 90), ("lng", -180, 180)):
        if d.get(f) is not None and not (lo <= float(d[f]) <= hi):
            errs.append(f"{f} out of range")
    if d.get("fee_cad") is not None and float(d["fee_cad"]) < 0:
        errs.append("negative fee")
    return errs


def extract_resource(page_text: str, source_url: str, region_id: str) -> dict:
    if region_id not in REGION_IDS:
        raise ValueError(f"region_id must be one of {sorted(REGION_IDS)}")
    from .llm import generate_text
    raw = generate_text(page_text[:12000], system_instruction=PROMPT,
                        max_output_tokens=1200, json_mode=True,
                        min_interval_seconds=13.0)
    m = re.search(r"\{.*\}", raw, re.S)
    d = json.loads(m.group(0)) if m else {}
    evidence = d.get("evidence") if isinstance(d.get("evidence"), dict) else {}
    # Retain only source excerpts that actually occur on the fetched page.
    d["evidence"] = {str(k): str(v).strip()[:300] for k, v in evidence.items()
                     if isinstance(v, str) and v.strip() and v.strip().casefold() in page_text.casefold()}
    d.update({"url": source_url, "source_url": source_url, "region_id": region_id,
              "collected_at": date.today().isoformat(), "last_verified_at": None,
              "is_sample": False, "tags": d.get("tags", []),
              "id": "pending-" + re.sub(r"[^a-z0-9]+", "-", (d.get("name") or "x").lower()).strip("-")[:40]})
    d.setdefault("requires_appointment", False); d.setdefault("requires_id", False); d.setdefault("fee_cad", 0)
    required_evidence = {"name", "description", "address", "hours"}
    d["confidence"] = round(len(required_evidence.intersection(d["evidence"])) / len(required_evidence), 2)
    d["_problems"] = validate_resource(d)
    return d


def _append_candidate(rec: dict, out=None) -> str:
    out = out or config.DATA_DIR / "pending.json"
    pending = json.loads(out.read_text(encoding="utf-8")) if out.exists() else []
    source_url = rec.get("source_url", "").rstrip("/").casefold()
    name = (rec.get("name") or "").casefold()
    for index, old in enumerate(pending):
        old_url = old.get("source_url", "").rstrip("/").casefold()
        old_name = (old.get("name") or "").casefold()
        if (source_url and source_url == old_url) or (name and name == old_name):
            if old.get("review_status", "pending") != "pending":
                return "duplicate"
            old_problems = old.get("_problems") or validate_resource(old)
            new_problems = rec.get("_problems") or validate_resource(rec)
            old_quality = (-len(old_problems), float(old.get("confidence") or 0))
            new_quality = (-len(new_problems), float(rec.get("confidence") or 0))
            if new_quality > old_quality:
                pending[index] = rec
                out.write_text(json.dumps(pending, ensure_ascii=False, indent=2), encoding="utf-8")
                return "updated"
            return "duplicate"
    pending.append(rec)
    out.write_text(json.dumps(pending, ensure_ascii=False, indent=2), encoding="utf-8")
    return "added"


def _source_has_completed_candidate(source_url: str, out=None) -> bool:
    out = out or config.DATA_DIR / "pending.json"
    pending = json.loads(out.read_text(encoding="utf-8")) if out.exists() else []
    wanted = source_url.rstrip("/").casefold()
    for rec in pending:
        if rec.get("source_url", "").rstrip("/").casefold() != wanted:
            continue
        if rec.get("review_status") in {"approved", "rejected"}:
            return True
        if not (rec.get("_problems") or validate_resource(rec)):
            return True
    return False


def _ingest_one(source_url: str, region_id: str) -> dict:
    text = fetch_page_text(source_url)
    return extract_resource(text, source_url, region_id)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("source", nargs="?", help="local text file or public HTTPS page URL")
    ap.add_argument("--url", help="canonical source URL when source is a local text file")
    ap.add_argument("--region", choices=sorted(REGION_IDS), help="region served by a single source")
    ap.add_argument("--sources-file", help="JSON list of {url, region_id} entries for a polite batch run")
    ap.add_argument("--refresh", action="store_true", help="reprocess sources that already have a reviewed or valid candidate")
    a = ap.parse_args()
    out = config.DATA_DIR / "pending.json"
    if a.sources_file:
        if a.source or a.url or a.region:
            ap.error("--sources-file cannot be combined with source, --url, or --region")
        sources = json.loads(open(a.sources_file, encoding="utf-8").read())
        if not isinstance(sources, list):
            ap.error("sources file must contain a JSON array")
        for item in sources:
            try:
                if not a.refresh and _source_has_completed_candidate(item["url"], out):
                    print("skipped: existing candidate", item["url"])
                    continue
                rec = _ingest_one(item["url"], item["region_id"])
                outcome = _append_candidate(rec, out)
                print(outcome + ":", rec.get("name") or item["url"], rec.get("_problems", []))
            except Exception as exc:
                print("failed:", item.get("url", "?"), str(exc))
    else:
        if not a.source or not a.region:
            ap.error("source and --region are required unless --sources-file is used")
        if a.source.startswith("https://"):
            if a.url:
                ap.error("do not pass --url when source is already an HTTPS URL")
            source_url = a.source
            page_text = fetch_page_text(source_url)
        else:
            if not a.url:
                ap.error("--url is required when source is a local text file")
            source_url = a.url
            page_text = open(a.source, encoding="utf-8").read()
        rec = extract_resource(page_text, source_url, a.region)
        outcome = _append_candidate(rec, out)
        print("problems:" if rec["_problems"] else "review:", rec["_problems"] or "", "->", outcome, "data/pending.json")
