"""Central configuration. Everything is overridable via environment variables."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
SEED_PATH = DATA_DIR / "resources.json"
DB_PATH = Path(os.getenv("COMMUNITYOS_DB", DATA_DIR / "communityos.db"))
FRONTEND_DIR = ROOT / "frontend"

TIMEZONE = os.getenv("COMMUNITYOS_TZ", "America/Toronto")

# --- LLM (optional: the system works without a key using rule-based fallbacks)
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
# Gemini is reserved for ingestion. Interactive searches must not consume the
# free-tier request quota; search intent always uses the local rule parser.
USE_LLM_INTENT = False
USE_LLM_EXPLAIN = False

# --- Retrieval backend: "tfidf" (zero-dependency) or "st" (sentence-transformers)
EMBEDDING_BACKEND = os.getenv("EMBEDDING_BACKEND", "tfidf")
ST_MODEL = os.getenv("ST_MODEL", "paraphrase-multilingual-MiniLM-L12-v2")

# --- Ranking weights (sum to 1.0). Exposed to the UI for transparency.
WEIGHTS = {
    "match": 0.35,        # does it explicitly accept/offer what the user needs
    "eligibility": 0.20,  # is the user eligible
    "distance": 0.15,     # how close
    "open": 0.15,         # open right now
    "freshness": 0.10,    # how recently verified (and not reported)
    "barrier": 0.05,      # no appointment / no ID / free
}

# --- Staleness / reporting
REPORT_REVIEW_THRESHOLD = 3     # open reports before a card is flagged "needs review"
REPORT_CONFIDENCE_DECAY = 0.8   # confidence *= 0.8 per open report
FRESH_DAYS = 30                 # verified within this many days = fully fresh
STALE_DAYS = 365                # older than this = minimum freshness

ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "demo-admin")

# --- Anchor points for "near campus" (lat, lng)
CAMPUSES = {
    "uw": (43.4723, -80.5449),
    "wlu": (43.4740, -80.5280),
    "conestoga": (43.3892, -80.4062),
}
DEFAULT_CENTER = (43.4516, -80.4925)  # downtown Kitchener

# Region directory used by the UI, ingestion workflow and search scope.
# These centers are ranking fallbacks only; region_id is the authoritative scope.
REGIONS = {
    "waterloo_region": {"center": (43.4516, -80.4925), "names": {"zh": "滑铁卢地区（全部）", "en": "Waterloo Region (all)"}},
    "kitchener": {"center": (43.4516, -80.4925), "names": {"zh": "基奇纳", "en": "Kitchener"}},
    "waterloo": {"center": (43.4643, -80.5204), "names": {"zh": "滑铁卢市", "en": "Waterloo"}},
    "cambridge": {"center": (43.3616, -80.3144), "names": {"zh": "剑桥市", "en": "Cambridge"}},
}
DEFAULT_REGION = "waterloo_region"
